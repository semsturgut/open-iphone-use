import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from oiu.core import Registry
from oiu.http import Server
from oiu.mcp import tools

JPEG = b'\xff\xd8\xfftest'


class FakeHID:
    def __init__(self):
        self.calls = []
        self.enter = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.closed = False

    def request(self, operation, **arguments):
        self.calls.append((operation, arguments))
        if operation == 'click' and self.block:
            self.enter.set()
            self.release.wait(2)
        if operation == 'stop_control':
            self.release.set()
        return {'ok': True, 'message': operation}

    def ingest(self, jpeg):
        return {}

    def stop_capture(self):
        return self.request('stop_control')

    def close(self):
        self.closed = True


class FakeRadio:
    def __init__(self):
        self.items = [dict(id='bt-a',name='Phone A',name_source='test-bluetooth',reports=[1,2]),
                      dict(id='bt-b',name='Phone B',name_source='test-bluetooth',reports=[1,2]),
                      dict(id='bt-c',name='Phone C',name_source='test-bluetooth',reports=[1,2])]
    def peers(self): return self.items


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = Registry(self.tmp.name)
        self.registry.radio = FakeRadio()
        self.a = self.registry.create_bluetooth('bt-a')
        self.b = self.registry.create_bluetooth('bt-b')
        self.a.backend, self.b.backend = FakeHID(), FakeHID()
        self.server = Server(('127.0.0.1', 0), self.registry)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.registry.close()
        self.tmp.cleanup()

    def api(self, path, body=None, token=None, capture=False, extra=None, method=None):
        headers = {'X-Open-iPhone-Use-Token' if capture else 'Authorization':
                   (token or self.a.token) if capture else 'Bearer ' + (token or self.registry.admin_token)}
        headers.update(extra or {})
        request = Request(self.url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return json.load(response)
        except HTTPError as error:
            error.close()
            raise

    def test_loopback_is_automatic_but_dns_rebinding_and_cross_site_requests_are_rejected(self):
        with urlopen(self.url+'/api/devices',timeout=2) as response: self.assertEqual(len(json.load(response)),2)
        with urlopen(self.url+'/',timeout=2) as response: self.assertNotIn(b'id="token"',response.read())
        for headers in ({'Host':f'evil.example:{self.server.server_port}'},
                        {'Host':f'192.168.1.2:{self.server.server_port}'}, {'Sec-Fetch-Site':'cross-site'}):
            with self.assertRaises(HTTPError) as error: self.api('/api/devices',extra=headers)
            self.assertEqual(error.exception.code,403)
        self.server.operator=False
        try:
            for route in ('/','/app.js','/api/setup','/api/devices','/api/health','/api/status'):
                with self.assertRaises(HTTPError) as error: self.api(route)
                self.assertEqual(error.exception.code,403)
            # The private capture link and uploads still work on the capture listener.
            with urlopen(self.url+'/capture?token='+self.a.token,timeout=2) as response: self.assertEqual(response.status,200)
            self.api('/api/frame',JPEG,token=self.a.token,capture=True)
        finally: self.server.operator=True

    def test_cli_uses_loopback_without_a_client_credentials_file(self):
        client_state=str(Path(self.tmp.name)/'empty-client')
        output=subprocess.run([sys.executable,'-m','oiu.cli','--state',client_state,'--url',self.url,'devices'],
                              check=True,capture_output=True,text=True)
        self.assertEqual(len(json.loads(output.stdout)),2)
        self.assertFalse(Path(client_state).exists())

    def test_expected_frame_window_accepts_recent_history_and_preparation_needs_no_id(self):
        self.a.upload(JPEG); first=self.a.frame_id
        self.a.upload(JPEG+b'next')
        self.assertTrue(self.a.action('click',{'expectedFrameID':first})['ok'])
        self.a.history.clear(); self.a.history.append((first,time.monotonic()-2.1))
        with self.assertRaises(ValueError): self.a.action('click',{'expectedFrameID':first})
        self.assertTrue(self.a.action('ensure_session',{})['ok'])
        self.assertTrue(self.a.action('prepare_session',{})['ok'])
        for tool in tools():
            if tool['name'] in ('ensure_session','prepare_session'):
                self.assertNotIn('expected_frame_id',tool['inputSchema']['required'])

    def test_duplicate_bluetooth_names_remain_distinguishable(self):
        self.registry.radio.items[0]['name']='iPhone'
        self.registry.radio.items[1]['name']='iPhone'
        devices=self.api('/api/devices')
        self.assertNotEqual(devices[0]['name'],devices[1]['name'])
        self.assertTrue(all(d['bluetooth_name']=='iPhone' for d in devices))
        candidates=self.api('/api/bluetooth')
        self.assertNotEqual(candidates[0]['name'],candidates[1]['name'])

    def test_operator_static_assets(self):
        web = Path(__file__).resolve().parents[1] / 'oiu' / 'web'
        for route, filename, mime in [('/', 'index.html', 'text/html'),
                                      ('/app.js', 'app.js', 'text/javascript'),
                                      ('/style.css', 'style.css', 'text/css'),
                                      ('/qrcode.js', 'qrcode.js', 'text/javascript')]:
            with self.subTest(route=route), urlopen(self.url + route, timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), mime)
                self.assertEqual(response.read(), (web / filename).read_bytes())
        html = (web / 'index.html').read_text()
        self.assertLess(html.index('src="/qrcode.js"'), html.index('src="/app.js"'))

    @unittest.skipUnless(__import__('shutil').which('node'), 'Node required for web interaction checks')
    def test_operator_link_actions(self):
        subprocess.run(['node', str(Path(__file__).with_name('test_web_links.cjs'))],
                       check=True, capture_output=True, text=True)

    def test_independent_credentials_frames_and_restart(self):
        self.api('/api/frame', JPEG, self.a.token, True)
        self.api('/api/frame', JPEG + b'b', self.b.token, True)
        self.assertNotEqual(self.a.frame_id, self.b.frame_id)
        self.assertEqual(len(self.api('/api/devices')), 2)
        self.assertTrue(all(d['capture_active'] for d in self.api('/api/devices')))
        self.server.operator=False
        try:
            with self.assertRaises(HTTPError) as error: self.api('/api/devices', token=self.a.token)
            self.assertEqual(error.exception.code,403)
        finally: self.server.operator=True
        with self.assertRaises(HTTPError):
            self.api('/api/frame', JPEG, self.registry.admin_token, True)
        reloaded = Registry(self.tmp.name)
        self.assertEqual(reloaded.devices[self.a.id].token, self.a.token)
        self.assertFalse(reloaded.devices[self.a.id].active)
        self.assertEqual(self.registry.path.stat().st_mode & 0o777, 0o600)

    def test_registered_devices_have_private_persistent_links_before_frames(self):
        created = self.api('/api/devices', json.dumps({'bt_id': 'bt-c'}).encode())
        before = self.api('/api/devices')
        self.assertEqual(len(before), 3)
        for device in before:
            self.assertIsNone(device['frame_id'])
            self.assertFalse(device['capture_active'])
            self.assertIn('control_status', device)
            self.assertEqual(device['capture_path'],
                             '/capture?token=' + self.registry.devices[device['device_id']].token)
        self.assertEqual(created['capture_path'], before[-1]['capture_path'])
        self.server.operator=False
        try:
            for headers in ({}, {'Authorization':'Bearer '+self.registry.admin_token}):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(self.url+'/api/devices',headers=headers),timeout=2)
                self.assertEqual(error.exception.code,403); error.exception.close()
        finally: self.server.operator=True
        # Recreate the registry and read the links through the authenticated API.
        self.server.registry = Registry(self.tmp.name)
        after = self.api('/api/devices')
        self.assertEqual([(d['device_id'], d['capture_path']) for d in after],
                         [(d['device_id'], d['capture_path']) for d in before])
        with urlopen(self.url + created['capture_path'], timeout=2) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.registry.path.stat().st_mode & 0o777, 0o600)
        # Recover owner-only permissions even if a leftover temp file was too open.
        temporary = self.registry.path.with_suffix('.tmp')
        temporary.write_text('{}')
        temporary.chmod(0o644)
        self.registry.save()
        self.assertEqual(self.registry.path.stat().st_mode & 0o777, 0o600)

    def test_remove_requires_operator_and_revokes_live_device_across_restart(self):
        self.a.upload(JPEG)
        self.b.upload(JPEG)
        old_token, other_token = self.a.token, self.b.token
        path = f'/api/devices/{self.a.id}'
        self.server.operator=False
        try:
            for token in ('invalid',old_token,self.registry.admin_token):
                with self.assertRaises(HTTPError) as error: self.api(path,token=token,method='DELETE')
                self.assertEqual(error.exception.code,403)
        finally: self.server.operator=True
        with self.assertRaises(HTTPError) as error:
            self.api(path, extra={'Origin': 'https://hostile.example'}, method='DELETE')
        self.assertEqual(error.exception.code, 401)
        self.assertEqual(len(self.registry.devices), 2)
        result = self.api(path, method='DELETE')
        self.assertTrue(result['ok'])
        self.assertEqual(result['device_id'], self.a.id)
        self.assertEqual([d['device_id'] for d in self.api('/api/devices')], [self.b.id])
        self.assertTrue(self.a.removed)
        self.assertTrue(self.a.backend.closed)
        self.assertIn(('stop_control', {}), self.a.backend.calls)
        self.assertEqual((self.a.jpeg, self.a.frame_id, self.a.at, len(self.a.history)), (b'', None, 0, 0))
        for route, body, capture in [(f'/capture?token={old_token}', None, False),
                ('/api/frame', JPEG, True), ('/api/tap', b'{"x":0.5,"y":0.5}', True),
                ('/api/pointer', b'{"epoch":"old"}', True)]:
            with self.assertRaises(HTTPError) as error:
                self.api(route, body, token=old_token, capture=capture)
            self.assertEqual(error.exception.code, 401)
        with self.assertRaises(ValueError): self.a.upload(JPEG)
        with self.assertRaises(ValueError): self.a.action('send_key', {})
        with self.assertRaises(HTTPError) as error: self.api(path, method='DELETE')
        self.assertEqual(error.exception.code, 404)
        self.assertTrue(self.b.active)
        self.assertFalse(self.b.backend.closed)
        self.assertEqual(self.b.token, other_token)
        self.assertTrue(self.b.action('click', {'expectedFrameID': self.b.frame_id})['ok'])
        self.server.registry = Registry(self.tmp.name)
        self.assertNotIn(self.a.id, self.server.registry.devices)
        with self.assertRaises(PermissionError): self.server.registry.capture_device(old_token)
        self.assertEqual(self.server.registry.devices[self.b.id].token, other_token)

    def test_remove_cancels_running_and_queued_actions(self):
        self.a.upload(JPEG)
        self.a.backend.block = True
        with ThreadPoolExecutor(3) as pool:
            first = pool.submit(self.a.action, 'click', {'expectedFrameID': self.a.frame_id})
            self.assertTrue(self.a.backend.enter.wait(1))
            waiting = pool.submit(self.a.action, 'send_key', {'expectedFrameID': self.a.frame_id, 'key': 'enter'})
            removed = pool.submit(self.api, f'/api/devices/{self.a.id}', method='DELETE')
            self.assertTrue(removed.result(3)['ok'])
            first.result(2)
            with self.assertRaises(ValueError): waiting.result(2)
        self.assertFalse(any(op == 'send_key' for op, _ in self.a.backend.calls))
        self.assertTrue(self.a.backend.closed)

    def test_remove_preserves_identity_when_persisting_revocation_fails(self):
        old_token = self.a.token
        with patch.object(self.registry, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(HTTPError) as error:
                self.api(f'/api/devices/{self.a.id}', method='DELETE')
            self.assertEqual(error.exception.code, 400)
        self.assertIs(self.registry.devices[self.a.id], self.a)
        self.assertEqual(self.a.token, old_token)
        self.assertFalse(self.a.removed)
        self.assertFalse(self.a.backend.closed)
        self.assertIn(self.a.id, Registry(self.tmp.name).devices)

    def test_remove_revokes_even_if_backend_cleanup_reports_failure(self):
        old_token = self.a.token
        with patch.object(self.a.backend, 'stop_capture', side_effect=OSError('radio unavailable')):
            result = self.api(f'/api/devices/{self.a.id}', method='DELETE')
        self.assertTrue(result['ok'])
        self.assertEqual(result['warnings'], ['radio unavailable'])
        self.assertTrue(self.a.backend.closed)
        with self.assertRaises(PermissionError): self.registry.capture_device(old_token)

    def test_remove_cli_and_mcp_tool(self):
        base = [sys.executable, '-m', 'oiu.cli', '--state', self.tmp.name, '--url', self.url]
        removed = subprocess.run(base + ['remove', '--device', self.a.id],
                                 check=True, capture_output=True, text=True)
        self.assertTrue(json.loads(removed.stdout)['ok'])
        again = subprocess.run(base + ['remove', '--device', self.a.id], capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)
        invalid = subprocess.run(base + ['remove', '--device', '../devices'], capture_output=True, text=True)
        self.assertNotEqual(invalid.returncode, 0)
        remove_tool = next(tool for tool in tools() if tool['name'] == 'remove_device')
        self.assertEqual(remove_tool['inputSchema']['required'], ['device_id'])
        self.assertTrue(remove_tool['annotations']['destructiveHint'])
        messages = [dict(jsonrpc='2.0', id=1, method='tools/call', params={'name': 'remove_device', 'arguments': {}}),
                    dict(jsonrpc='2.0', id=2, method='tools/call', params={'name': 'remove_device', 'arguments': {'device_id': self.b.id}})]
        output = subprocess.run(base + ['mcp'], input='\n'.join(map(json.dumps, messages)) + '\n',
                                check=True, capture_output=True, text=True)
        first, second = map(json.loads, output.stdout.splitlines())
        self.assertTrue(first['result']['isError'])
        self.assertFalse(second['result']['isError'])
        self.assertEqual(self.registry.list(), [])

    def test_cross_device_and_stale_frames_cannot_control(self):
        self.a.upload(JPEG)
        self.b.upload(JPEG)
        with self.assertRaises(ValueError):
            self.a.action('click', {'expectedFrameID': self.b.frame_id})
        self.a.action('click', {'expectedFrameID': self.a.frame_id})
        self.assertEqual(sum(op == 'click' for op, _ in self.a.backend.calls), 1)
        self.assertFalse(self.b.backend.calls)
        self.a.at -= 6
        with self.assertRaises(ValueError):
            self.a.action('click', {'expectedFrameID': self.a.frame_id})
        self.a.stop()
        self.a.upload(JPEG)
        self.assertEqual(len(self.a.history), 1)

    def test_stop_cancels_waiting_actions_without_blocking_another_phone(self):
        self.a.upload(JPEG)
        self.b.upload(JPEG)
        self.a.backend.block = True
        with ThreadPoolExecutor(3) as pool:
            first = pool.submit(self.a.action, 'click', {'expectedFrameID': self.a.frame_id})
            self.assertTrue(self.a.backend.enter.wait(1))
            waiting = pool.submit(self.a.action, 'send_key', {'expectedFrameID': self.a.frame_id, 'key': 'enter'})
            time.sleep(.03)
            self.assertTrue(self.b.action('click', {'expectedFrameID': self.b.frame_id})['ok'])
            self.a.stop()
            first.result(2)
            with self.assertRaises(ValueError):
                waiting.result(2)
        self.assertFalse(any(op == 'send_key' for op, _ in self.a.backend.calls))

    def test_watchdog_releases_input(self):
        self.a.upload(JPEG)
        self.a.at -= 6
        deadline = time.monotonic() + 2
        while self.a.active and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(self.a.active)
        self.assertIn(('stop_control', {}), self.a.backend.calls)

    def test_bad_jpeg_cross_origin_and_unknown_device(self):
        with self.assertRaises(HTTPError):
            self.api('/api/frame', b'invalid', capture=True)
        with self.assertRaises(HTTPError) as error:
            self.api('/api/devices', extra={'Origin': 'https://hostile.example'})
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError):
            self.api('/api/devices/unknown/status')
        with self.assertRaises(ValueError):
            self.a.action('arbitrary_native_operation', {})

    def test_atomic_snapshot_and_unavailable_backend(self):
        self.a.upload(JPEG)
        snapshot = self.api(f'/api/devices/{self.a.id}/snapshot')
        self.assertEqual(snapshot['frame']['frameID'], self.a.frame_id)
        self.assertEqual(base64.b64decode(snapshot['frame']['base64']), JPEG)
        device = self.registry.create('No HID')
        device.upload(JPEG)
        result = device.action('click', {'expectedFrameID': device.frame_id})
        self.assertFalse(result['ok'])

    def test_cli_and_mcp_live_service(self):
        self.a.upload(JPEG)
        base = [sys.executable, '-m', 'oiu.cli', '--state', self.tmp.name, '--url', self.url]
        listed = subprocess.run(base + ['devices'], capture_output=True, text=True, check=True)
        self.assertEqual(len(json.loads(listed.stdout)), 2)
        messages = [dict(jsonrpc='2.0', id=1, method='initialize', params={'protocolVersion': '2024-11-05'}),
                    dict(jsonrpc='2.0', method='notifications/initialized'),
                    dict(jsonrpc='2.0', id=2, method='tools/list'),
                    dict(jsonrpc='2.0', id=3, method='tools/call', params={'name': 'get_latest_screenshot', 'arguments': {'device_id': self.a.id}}),
                    dict(jsonrpc='2.0', id=4, method='tools/call', params={'name': 'click', 'arguments': {'button': 'left'}})]
        output = subprocess.run(base + ['mcp'], input='\n'.join(map(json.dumps, messages)) + '\n',
                                text=True, capture_output=True, check=True)
        replies = [json.loads(line) for line in output.stdout.splitlines()]
        self.assertEqual(len(replies), 4)
        self.assertEqual(replies[0]['result']['protocolVersion'], '2024-11-05')
        self.assertEqual(replies[2]['result']['content'][1]['type'], 'image')
        self.assertTrue(replies[3]['result']['isError'])
        for tool in tools():
            if tool['name'] not in ('list_devices','list_bluetooth_candidates','create_device'):
                self.assertIn('device_id', tool['inputSchema']['required'])

    def test_cli_serve_shutdown_and_lease_release(self):
        state = str(Path(self.tmp.name)/'cli-state')
        process = subprocess.Popen([sys.executable, '-m', 'oiu.cli', '--state', state,
                                    'serve', '--foreground', '--local-http', '--capture-only', '--port', '0'], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            line = process.stdout.readline().strip()
            self.assertEqual(line,'Open iPhone Use is running')
            line=process.stdout.readline().strip()
            self.assertTrue(line.startswith('Web UI:  http://127.0.0.1:'))
            url=line.split('Web UI:  ')[1]
            token = json.loads((Path(state)/'state.json').read_text())['admin_token']
            with urlopen(Request(url+'/api/devices', headers={'Authorization': 'Bearer '+token}), timeout=2) as response:
                self.assertEqual(json.load(response), [])
            process.terminate()
            process.wait(3)
            self.assertEqual(process.returncode, 0)
            from oiu.state import StateLease
            lease = StateLease(state)
            lease.close()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()

    def test_bluetooth_first_creation_requires_connected_unique_selected_peer(self):
        candidates = self.api('/api/bluetooth')
        self.assertEqual([p['id'] for p in candidates if p['available']], ['bt-c'])
        self.server.operator=False
        try:
            with self.assertRaises(HTTPError) as error: self.api('/api/bluetooth',token=self.a.token)
            self.assertEqual(error.exception.code,403)
        finally: self.server.operator=True
        for body in ({'name':'arbitrary'}, {'bt_id':'missing'}, {'bt_id':'bt-a'}):
            with self.assertRaises(HTTPError) as error:
                self.api('/api/devices',json.dumps(body).encode())
            self.assertEqual(error.exception.code,400)
        self.registry.radio.items[2]['reports']=[1]
        with self.assertRaises(HTTPError): self.api('/api/devices',b'{"bt_id":"bt-c"}')
        self.registry.radio.items[2]['reports']=[1,2]
        created = self.api('/api/devices',b'{"bt_id":"bt-c"}')
        self.assertEqual(created['name'],'Phone C')
        self.assertEqual(created['bt_id'],'bt-c')
        device = self.registry.devices[created['device_id']]
        self.assertEqual(Registry(self.tmp.name).devices[device.id].bt_id,'bt-c')
        self.assertIsNone(device.frame_id)
        for route, body in [('/api/enroll',b'{}'),('/join?token='+self.registry.invite_token,None)]:
            with self.assertRaises(HTTPError) as error: self.api(route,body)
            self.assertEqual(error.exception.code,410)

    def test_bluetooth_and_sharing_status_are_independent(self):
        self.a.upload(JPEG)
        before = self.api('/api/devices')
        self.assertTrue(before[0]['bluetooth_connected'])
        self.assertTrue(before[0]['sharing_active'])
        self.assertTrue(before[1]['bluetooth_connected'])
        self.assertFalse(before[1]['sharing_active'])
        self.registry.radio.items = [p for p in self.registry.radio.items if p['id']!='bt-a']
        after = self.api('/api/devices')
        self.assertFalse(after[0]['bluetooth_connected'])
        self.assertTrue(after[0]['sharing_active'])
        self.assertIn('required',after[0]['bluetooth_error'])
        status = self.api(f'/api/devices/{self.a.id}/status')
        self.assertFalse(status['device']['bluetooth_connected'])

    def test_new_link_and_management_mcp(self):
        base=[sys.executable,'-m','oiu.cli','--state',self.tmp.name,'--url',self.url]
        new=subprocess.run(base+['new'],check=True,capture_output=True,text=True)
        self.assertIn('Phone C',new.stdout)
        self.assertIn('/capture?token=',new.stdout)
        self.assertIn('██',new.stdout)
        device=next(d for d in self.registry.devices.values() if d.bt_id=='bt-c')
        link=subprocess.run(base+['link',device.id],check=True,capture_output=True,text=True)
        self.assertIn(device.token,link.stdout)
        removed=subprocess.run(base+['remove',device.id],check=True,capture_output=True,text=True)
        self.assertTrue(json.loads(removed.stdout)['ok'])
        messages=[dict(jsonrpc='2.0',id=1,method='tools/call',params={'name':'list_bluetooth_candidates'}),
                  dict(jsonrpc='2.0',id=2,method='tools/call',params={'name':'create_device','arguments':{'bt_id':'bt-c'}})]
        mcp=subprocess.run(base+['mcp'],input='\n'.join(map(json.dumps,messages))+'\n',check=True,capture_output=True,text=True)
        replies=list(map(json.loads,mcp.stdout.splitlines()))
        self.assertTrue(all(not r['result']['isError'] for r in replies))
        self.assertIn('capture_url',json.loads(replies[1]['result']['content'][0]['text']))

    def test_cli_new_requires_explicit_selection_when_two_candidates_are_available(self):
        self.registry.radio.items.append(dict(id='bt-d',name='Phone D',reports=[1,2]))
        base=[sys.executable,'-m','oiu.cli','--state',self.tmp.name,'--url',self.url]
        ambiguous=subprocess.run(base+['new'],capture_output=True,text=True)
        self.assertNotEqual(ambiguous.returncode,0)
        self.assertIn('--bt',ambiguous.stderr)
        selected=subprocess.run(base+['new','--bt','bt-d'],check=True,capture_output=True,text=True)
        self.assertIn('Phone D',selected.stdout)
        self.assertEqual(len(self.registry.devices),3)
        self.assertTrue(any(d.bt_id=='bt-d' for d in self.registry.devices.values()))

    def test_name_fallback_and_sanitization_never_use_another_peers_name(self):
        self.registry.radio.items[2].pop('name')
        candidate=next(p for p in self.api('/api/bluetooth') if p['id']=='bt-c')
        self.assertEqual(candidate['name'],'iPhone')
        self.registry.radio.items[2]['name']='Unsafe\x1b[31m\nName'
        created=self.api('/api/devices',b'{"bt_id":"bt-c"}')
        self.assertNotIn('\x1b',created['name'])
        self.assertNotIn('\n',created['name'])

    def test_setup_updates_auto_origin_and_live_tls_certificate_on_network_change(self):
        import socket
        import ssl
        from oiu.bootstrap import certificates, LANOrigin
        cert, key, ca, _ = certificates(self.tmp.name, '192.168.1.2')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        tls = Server(('127.0.0.1', 0), self.registry, context, watchdog=False)
        thread = threading.Thread(target=tls.serve_forever, daemon=True)
        thread.start()
        changes = []
        self.registry.capture_origin = LANOrigin(self.tmp.name, '192.168.1.2', tls.server_port,
                                                context, changes.append)
        original_ca = Path(ca).read_bytes()
        try:
            with patch('oiu.bootstrap.lan_address', return_value='172.20.10.2'):
                # The capture listener role never permits operator mutations.
                self.server.operator=False
                try:
                    with self.assertRaises(HTTPError) as unauthorized: urlopen(self.url+'/api/setup',timeout=2)
                    self.assertEqual(unauthorized.exception.code,403); unauthorized.exception.close()
                finally: self.server.operator=True
                self.assertEqual(changes, [])
                setup = self.api('/api/setup')
                self.assertEqual(setup['capture_origin'], f'https://172.20.10.2:{tls.server_port}')
                self.assertEqual(len(changes), 1)
                self.api('/api/setup')
                self.assertEqual(len(changes), 1)  # No repeated renewal for unchanged IP.
            self.assertEqual(Path(ca).read_bytes(), original_ca)
            # A new handshake on the existing listener validates the new IP with the same CA.
            client = ssl.create_default_context(cafile=ca)
            with socket.create_connection(('127.0.0.1', tls.server_port), timeout=2) as raw:
                with client.wrap_socket(raw, server_hostname='172.20.10.2') as secure:
                    self.assertTrue(secure.getpeercert())
            with patch('oiu.bootstrap.lan_address', side_effect=OSError('No LAN address')):
                with self.assertRaises(HTTPError) as error:
                    self.api('/api/setup')
                self.assertEqual(error.exception.code, 400)
                self.assertEqual(len(changes), 1)
        finally:
            tls.shutdown(); tls.server_close(); thread.join(2)

    def test_single_writer_state_lease(self):
        from oiu.state import StateLease
        first = StateLease(self.tmp.name)
        try:
            with self.assertRaises(ValueError):
                StateLease(self.tmp.name)
        finally:
            first.close()
        second = StateLease(self.tmp.name)
        second.close()

    @unittest.skipUnless(__import__('shutil').which('openssl'), 'OpenSSL required for test certificate')
    def test_tls_capture_and_default_trust_rejection(self):
        import ssl
        from oiu.http import tls_context
        cert, key = str(Path(self.tmp.name)/'cert.pem'), str(Path(self.tmp.name)/'key.pem')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', key, '-out', cert, '-days', '1', '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server = Server(('127.0.0.1', 0), self.registry, tls_context(cert, key))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = 'https://localhost:' + str(server.server_port)
        try:
            with self.assertRaises(OSError):
                urlopen(url, timeout=2)
            request = Request(url + '/api/frame', data=JPEG,
                              headers={'X-Open-iPhone-Use-Token': self.a.token})
            with urlopen(request, context=ssl.create_default_context(cafile=cert), timeout=3) as response:
                self.assertEqual(json.load(response)['frameID'], self.a.frame_id)
        finally:
            server.shutdown()
            server.server_close()

    def test_capture_page_matches_native_source(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual((root/'macos/Sources/OpenIPhoneUseCore/Resources/capture.html').read_bytes(),
                         (root/'server/oiu/web/capture.html').read_bytes())


if __name__ == '__main__':
    unittest.main()
