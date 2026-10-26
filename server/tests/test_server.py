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
        pass


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = Registry(self.tmp.name)
        self.a = self.registry.create('Phone A')
        self.b = self.registry.create('Phone B')
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

    def api(self, path, body=None, token=None, capture=False, extra=None):
        headers = {'X-Open-iPhone-Use-Token' if capture else 'Authorization':
                   (token or self.a.token) if capture else 'Bearer ' + (token or self.registry.admin_token)}
        headers.update(extra or {})
        request = Request(self.url + path, data=body, headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                return json.load(response)
        except HTTPError as error:
            error.close()
            raise

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
        with self.assertRaises(HTTPError) as error:
            self.api('/api/devices', token=self.a.token)
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError):
            self.api('/api/frame', JPEG, self.registry.admin_token, True)
        reloaded = Registry(self.tmp.name)
        self.assertEqual(reloaded.devices[self.a.id].token, self.a.token)
        self.assertFalse(reloaded.devices[self.a.id].active)
        self.assertEqual(self.registry.path.stat().st_mode & 0o777, 0o600)

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
            if tool['name'] != 'list_devices':
                self.assertIn('device_id', tool['inputSchema']['required'])

    def test_cli_serve_shutdown_and_lease_release(self):
        state = str(Path(self.tmp.name)/'cli-state')
        process = subprocess.Popen([sys.executable, '-m', 'oiu.cli', '--state', state,
                                    'serve', '--local-http', '--capture-only', '--port', '0'], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            line = process.stdout.readline().strip()
            self.assertTrue(line.startswith('Open iPhone Use: http://127.0.0.1:'))
            url = line.split('Open iPhone Use: ')[1]
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

    def test_shared_invitation_enrolls_independent_capture_credentials(self):
        def enroll(invite):
            return self.api('/api/enroll', b'{}', extra={'X-Open-iPhone-Use-Invite':invite})
        with self.assertRaises(HTTPError): enroll('invalid')
        first,second=enroll(self.registry.invite_token),enroll(self.registry.invite_token)
        self.assertNotEqual(first['capture_path'],second['capture_path'])
        for item in (first,second):
            self.api('/api/frame',JPEG,item['capture_path'].split('token=')[1],True)
        devices=self.api('/api/devices')
        self.assertEqual(len(devices),4)
        self.assertEqual(sum(d['capture_active'] for d in devices),2)
        self.assertEqual(len({d['name'] for d in devices}),4)

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
