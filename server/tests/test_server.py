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
from PIL import Image

from oiu.core import Registry, FRAME_HISTORY_LIMIT, FRAME_MAX_AGE
from oiu.http import Server
from oiu.mcp import tools

_image = io.BytesIO()
Image.new('RGB', (9, 19), 'white').save(_image, format='JPEG')
JPEG = _image.getvalue()
CANDIDATES = [dict(udid='00008110-AAAA', name='Phone A', ios='27.0', connection='USB'),
              dict(udid='00008030-BBBB', name='Phone B', ios='27.0.1', connection='Wi-Fi'),
              dict(udid='00008030-CCCC', name='Phone C', ios='27.0', connection='USB')]


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.enter = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.closed = False
        self.label = 'Connected over USB'

    def request(self, operation, **arguments):
        self.calls.append((operation, arguments))
        if operation == 'pointer_tap' and self.block:
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


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = Registry(self.tmp.name)
        self.registry.backend_factory = lambda device: FakeBackend()
        self.a = self.registry.create(CANDIDATES[0]['udid'], 'Phone A')
        self.b = self.registry.create(CANDIDATES[1]['udid'], 'Phone B')
        self.server = Server(('127.0.0.1', 0), self.registry)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.candidates = patch('oiu.coredevice.candidates', return_value=CANDIDATES)
        self.candidates.start()

    def tearDown(self):
        self.candidates.stop()
        self.server.shutdown()
        self.server.server_close()
        self.registry.close()
        self.tmp.cleanup()

    def api(self, path, body=None, extra=None, method=None):
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        request = Request(self.url + path, data=data, headers=dict({'Content-Type': 'application/json'}, **(extra or {})), method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return json.load(response)
        except HTTPError as error:
            error.close()
            raise

    def cli(self, *arguments, **options):
        return subprocess.run([sys.executable, '-m', 'oiu.cli', '--state', self.tmp.name, '--url', self.url, *arguments],
                              capture_output=True, text=True, **options)

    def test_loopback_only_and_dns_rebinding_and_cross_site_requests_are_rejected(self):
        self.assertEqual(len(self.api('/api/devices')), 2)
        for headers in ({'Host': f'evil.example:{self.server.server_port}'}, {'Host': f'192.168.1.2:{self.server.server_port}'},
                        {'Sec-Fetch-Site': 'cross-site'}, {'Origin': 'https://hostile.example'}):
            with self.assertRaises(HTTPError) as error: self.api('/api/devices', extra=headers)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(ValueError): Server(('0.0.0.0', 0), self.registry)

    def test_static_assets_have_no_bluetooth_or_sharing(self):
        for path in ('/', '/app.js', '/style.css'):
            with urlopen(self.url + path, timeout=2) as response:
                body = response.read().decode()
            self.assertNotIn('Bluetooth', body)
            self.assertNotIn('sharing link', body.lower())

    def test_candidates_show_what_is_added_and_create_uses_the_device_name(self):
        listed = {c['udid']: c for c in self.api('/api/candidates')}
        self.assertFalse(listed[self.a.udid]['available'])
        self.assertEqual(listed[self.a.udid]['device_id'], self.a.id)
        self.assertTrue(listed['00008030-CCCC']['available'])
        created = self.api('/api/devices', {'udid': '00008030-CCCC'})
        self.assertEqual((created['name'], created['udid'], created['name_source']), ('Phone C', '00008030-CCCC', 'device'))
        self.assertEqual(self.api('/api/devices', {'udid': '00008030-CCCC'})['device_id'], created['device_id'])
        with self.assertRaises(HTTPError): self.api('/api/devices', {'udid': '../etc'})

    def test_phones_persist_and_bluetooth_records_are_dropped(self):
        state = json.loads((Path(self.tmp.name) / 'state.json').read_text())
        state['devices'].append(dict(id='legacy', name='Old phone', token='t', bt_id='peer'))
        (Path(self.tmp.name) / 'state.json').write_text(json.dumps(state))
        restored = Registry(self.tmp.name)
        self.assertEqual({d.udid for d in restored.devices.values()}, {self.a.udid, self.b.udid})
        self.assertNotIn('token', (Path(self.tmp.name) / 'state.json').read_text())

    def test_expected_frame_rules(self):
        self.a.upload(JPEG); first = self.a.frame_id
        self.a.history[-1] = (first, time.monotonic() - 30)
        self.a.upload(JPEG + b'next')
        self.assertTrue(self.a.action('pointer_tap', {'expectedFrameID': first, 'x': .5, 'y': .5})['ok'])
        with self.assertRaisesRegex(ValueError, 'before your last action.*get_latest_screenshot'):
            self.a.action('pointer_tap', {'expectedFrameID': first})
        self.a.history.append(('expired', time.monotonic() - FRAME_MAX_AGE - 1))
        with self.assertRaisesRegex(ValueError, 'older than 120 seconds'):
            self.a.action('pointer_tap', {'expectedFrameID': 'expired'})
        with self.assertRaisesRegex(ValueError, 'Unknown frame id'):
            self.a.action('pointer_tap', {'expectedFrameID': 'unknown'})
        self.assertTrue(self.a.action('ensure_session', {})['ok'])
        ensure = next(t for t in tools() if t['name'] == 'ensure_session')
        self.assertNotIn('expected_frame_id', ensure['inputSchema']['required'])

    def test_action_result_frame_after_input_end_is_valid_for_the_next_action(self):
        self.a.upload(JPEG)
        ended = time.monotonic()
        self.a.upload(JPEG)
        frame = self.a.frame_id
        self.a.backend.request = lambda operation, **arguments: {'ok': True, 'inputEndedAt': ended}
        self.a.action('pointer_tap', {'expectedFrameID': frame})
        self.assertTrue(self.a.action('pointer_tap', {'expectedFrameID': frame})['ok'])

    def test_frame_history_is_bounded(self):
        for _ in range(FRAME_HISTORY_LIMIT + 5): self.a.upload(JPEG)
        self.assertEqual(len(self.a.history), FRAME_HISTORY_LIMIT)

    def test_cross_device_and_stale_frames_cannot_control(self):
        self.a.upload(JPEG); self.b.upload(JPEG)
        with self.assertRaises(ValueError): self.a.action('pointer_tap', {'expectedFrameID': self.b.frame_id})
        self.a.action('pointer_tap', {'expectedFrameID': self.a.frame_id})
        self.assertFalse(self.b.backend.calls)
        self.a.at -= 6
        with self.assertRaisesRegex(ValueError, 'not live'): self.a.action('pointer_tap', {'expectedFrameID': self.a.frame_id})

    def test_stop_cancels_waiting_actions_without_blocking_another_phone(self):
        self.a.upload(JPEG); self.b.upload(JPEG)
        self.a.backend.block = True
        with ThreadPoolExecutor(3) as pool:
            first = pool.submit(self.a.action, 'pointer_tap', {'expectedFrameID': self.a.frame_id})
            self.assertTrue(self.a.backend.enter.wait(1))
            waiting = pool.submit(self.a.action, 'send_key', {'expectedFrameID': self.a.frame_id, 'key': 'enter'})
            time.sleep(.03)
            self.assertTrue(self.b.action('pointer_tap', {'expectedFrameID': self.b.frame_id})['ok'])
            self.a.stop()
            first.result(2)
            with self.assertRaises(ValueError): waiting.result(2)
        self.assertFalse(any(op == 'send_key' for op, _ in self.a.backend.calls))

    def test_remove_cancels_queued_actions_and_survives_cleanup_errors(self):
        self.a.upload(JPEG)
        self.a.backend.block = True
        with ThreadPoolExecutor(3) as pool:
            first = pool.submit(self.a.action, 'pointer_tap', {'expectedFrameID': self.a.frame_id})
            self.assertTrue(self.a.backend.enter.wait(1))
            waiting = pool.submit(self.a.action, 'send_key', {'expectedFrameID': self.a.frame_id, 'key': 'enter'})
            removed = pool.submit(self.api, f'/api/devices/{self.a.id}', method='DELETE')
            self.assertTrue(removed.result(3)['ok'])
            first.result(2)
            with self.assertRaises(ValueError): waiting.result(2)
        self.assertTrue(self.a.backend.closed)
        with patch.object(self.b.backend, 'stop_capture', side_effect=OSError('tunnel gone')):
            result = self.api(f'/api/devices/{self.b.id}', method='DELETE')
        self.assertEqual(result['warnings'], ['tunnel gone'])
        self.assertEqual(Registry(self.tmp.name).devices, {})

    def test_remove_keeps_the_phone_when_saving_fails(self):
        with patch.object(self.registry, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(HTTPError) as error: self.api(f'/api/devices/{self.a.id}', method='DELETE')
            self.assertEqual(error.exception.code, 400)
        self.assertIs(self.registry.devices[self.a.id], self.a)
        self.assertFalse(self.a.backend.closed)

    def test_watchdog_releases_input(self):
        self.a.upload(JPEG)
        self.a.at -= 6
        deadline = time.monotonic() + 2
        while self.a.active and time.monotonic() < deadline: time.sleep(.02)
        self.assertFalse(self.a.active)
        self.assertIn(('stop_control', {}), self.a.backend.calls)

    def test_snapshot_status_unknown_device_and_unavailable_control(self):
        self.a.upload(JPEG)
        snapshot = self.api(f'/api/devices/{self.a.id}/snapshot')
        self.assertEqual(base64.b64decode(snapshot['frame']['base64']), JPEG)
        self.assertEqual((snapshot['frame']['width'], snapshot['frame']['height']), (9, 19))
        self.assertEqual(self.api(f'/api/devices/{self.a.id}/status')['device']['control_status'], 'Connected over USB')
        with self.assertRaises(HTTPError): self.api('/api/devices/unknown/status')
        with self.assertRaises(ValueError): self.a.action('arbitrary_native_operation', {})
        self.registry.backend_factory = None
        plain = self.registry.create('00008030-CCCC')
        plain.upload(JPEG)
        self.assertFalse(plain.action('pointer_tap', {'expectedFrameID': plain.frame_id})['ok'])

    def test_cli_new_devices_remove_and_status(self):
        new = self.cli('new', check=True)
        self.assertIn('Phone C', new.stdout)
        self.assertEqual(len(json.loads(self.cli('devices', check=True).stdout)), 3)
        self.assertIn('not connected or is already added', self.cli('new', '--udid', self.a.udid).stderr)
        self.assertTrue(json.loads(self.cli('remove', self.a.id, check=True).stdout)['ok'])
        self.assertNotEqual(self.cli('remove', '--device', '../devices').returncode, 0)

    def test_mcp_tools_and_calls(self):
        names = {t['name'] for t in tools()}
        self.assertIn('list_candidates', names)
        self.assertFalse(names & {'list_bluetooth_candidates', 'move_pointer', 'pointer_home', 'set_page_board', 'prepare_session'})
        for tool in tools():
            if tool['name'] not in ('list_devices', 'list_candidates', 'create_device'):
                self.assertIn('device_id', tool['inputSchema']['required'])
        self.a.upload(JPEG)
        messages = [dict(jsonrpc='2.0', id=1, method='tools/call', params={'name': 'list_candidates'}),
                    dict(jsonrpc='2.0', id=2, method='tools/call', params={'name': 'create_device', 'arguments': {'udid': '00008030-CCCC'}}),
                    dict(jsonrpc='2.0', id=3, method='tools/call', params={'name': 'get_latest_screenshot', 'arguments': {'device_id': self.a.id}}),
                    dict(jsonrpc='2.0', id=4, method='tools/call', params={'name': 'remove_device', 'arguments': {}})]
        output = self.cli('mcp', input='\n'.join(map(json.dumps, messages)) + '\n', check=True)
        listed, created, shot, invalid = map(json.loads, output.stdout.splitlines())
        self.assertEqual(len(json.loads(listed['result']['content'][0]['text'])), 3)
        self.assertEqual(json.loads(created['result']['content'][0]['text'])['name'], 'Phone C')
        self.assertEqual(shot['result']['content'][1]['type'], 'image')
        self.assertTrue(invalid['result']['isError'])

    def test_cli_serve_shutdown_and_lease_release(self):
        state = str(Path(self.tmp.name) / 'cli-state')
        process = subprocess.Popen([sys.executable, '-m', 'oiu.cli', '--state', state, 'serve', '--foreground',
                                    '--no-control', '--port', '0'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'Open iPhone Use is running')
            url = process.stdout.readline().strip().split('Web UI:  ')[1]
            self.assertTrue(url.startswith('http://127.0.0.1:'))
            with urlopen(url + '/api/devices', timeout=2) as response: self.assertEqual(json.load(response), [])
            process.terminate(); process.wait(3)
            self.assertEqual(process.returncode, 0)
            from oiu.state import StateLease
            StateLease(state).close()
        finally:
            if process.poll() is None: process.kill(); process.wait()
            process.stdout.close(); process.stderr.close()

    def test_single_writer_state_lease(self):
        from oiu.state import StateLease
        first = StateLease(self.tmp.name)
        try:
            with self.assertRaises(ValueError): StateLease(self.tmp.name)
        finally:
            first.close()
        StateLease(self.tmp.name).close()


if __name__ == '__main__':
    unittest.main()
