import asyncio
import copy
import io
import plistlib
import time
import unittest
from unittest.mock import AsyncMock

from PIL import Image

from oiu.accessibility import Archive, AXError, AccessibilitySession, Tree, request_archive
from oiu.frames import frame_payload
from oiu.mcp import tools
import test_coredevice


def obj(oid, application=False):
    return dict(**{'class': 'AXPTranslationObject', 'pid': 123, 'objectID': oid,
                                  'isApplicationElement': application})


def rect(x=10, y=20, w=30, h=40):
    return {'class': 'NSValue', 'NS.rectval': '{{%s, %s}, {%s, %s}}' % (x, y, w, h), 'NS.special': 3}


def attributes():
    return {21: rect(), 1: {'class': 'NSValue', 'NS.pointval': '{25, 40}', 'NS.special': 1},
            27: 1, 32: True, 33: 'Continue', 45: 2, 53: '', 77: 1, 124: True,
            60: {'TokenType': 'AXElementTokenSimulator', 'ElementData': b'stable-element'}}


def tree():
    root, child = obj(1, True), obj(2)
    attrs = {(123, 1): {21: rect(0, 0, 100, 200), 8: [child], 9: [child]},
             (123, 2): {**attributes(), 8: [], 9: []}}
    return Tree((123, 1), {(123, 1): root, (123, 2): child}, attrs,
                {(123, 1): (), (123, 2): (0,)}, (), 1)


def envelope(kind='AXPTreeDumpTypeInitialDump'):
    t = tree()
    records = [{'class': 'AXPTranslatorResponse', 'associatedRequestType': 4, 'error': 0,
                'resultData': t.objects[t.root]}]
    for key, attrs in t.attributes.items():
        records.append({'class': 'AXPTranslatorResponse', 'associatedRequestType': 5, 'error': 0,
                        'associatedTranslationObject': t.objects[key], 'resultData': attrs})
    return {'class': 'AXPTranslatorResponse', 'associatedRequestType': 11, 'error': 0,
            'resultData': {'treeDumpType': kind, 'treeDump': records}}


class ArchiveTests(unittest.TestCase):
    def test_request_archive_round_trips_without_native_classes(self):
        for kind in (5, 6):
            request = Archive(request_archive(kind, obj(2), point=(25, 40) if kind == 6 else None)).root
            self.assertEqual(request['class'], 'AXPTranslatorRequest')
            self.assertEqual(request['requestType'], kind)
            if kind == 6:
                self.assertEqual(request['translation']['pid'], 0)
                self.assertEqual(request['clientType'], 0)
                self.assertEqual(request['parameters']['point']['NS.pointval'], '{25, 40}')
            else:
                self.assertIn(60, request['parameters']['attributes'])
                self.assertEqual(request['translation']['pid'], 123)

    def test_invalid_archive_and_cycle_are_rejected(self):
        for data in (b'not a plist', b'bplist00broken'):
            with self.assertRaises(AXError): Archive(data)
        cycle = {'$archiver': 'NSKeyedArchiver', '$version': 100000, '$top': {'root': plistlib.UID(1)},
                 '$objects': ['$null', {'$class': plistlib.UID(2), 'NS.objects': [plistlib.UID(1)]},
                              {'$classname': 'NSArray'}]}
        with self.assertRaises(AXError): Archive(plistlib.dumps(cycle, fmt=plistlib.FMT_BINARY))

    def test_tree_requires_complete_children_and_rejects_cycles(self):
        _, parsed = Tree.parse(envelope())
        self.assertEqual(parsed.paths[(123, 2)], (0,))
        for key in (8, 9):
            bad = envelope()
            bad['resultData']['treeDump'][2]['resultData'][key] = [obj(1, True)]
            with self.assertRaises(AXError): Tree.parse(bad)

    def test_shape_ignores_descendant_object_ids_and_preserves_pid(self):
        _, initial = Tree.parse(envelope())
        data = envelope('AXPTreeDumpTypeAdditionalData')
        data['resultData']['treeDump'][1]['resultData'][8][0]['objectID'] = 3
        data['resultData']['treeDump'][2]['associatedTranslationObject']['objectID'] = 3
        _, additional = Tree.parse(data)
        self.assertEqual(initial.shape, additional.shape)

    def test_rows_escape_delimiters_and_newlines(self):
        t = tree()
        t.attributes[(123, 2)][33] = 'A|B\nC'
        rows, refs = t.rows('snapshot')
        self.assertIn('snapshot-1|tap|button|A\\|B\\nC|', rows)
        self.assertEqual(refs['snapshot-1'], (123, 2))


class CheckedRefTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ax = AccessibilitySession(None)
        self.ax.tree = self.ax.observed = tree()
        self.ax.observed_at = time.monotonic()
        self.ax.refs = {'ref': (123, 2)}
        self.hit = obj(99)  # A hit-test translation has its own object ID.
        self.calls = []

    def reader(self, changed=None, covered=False, push=False):
        async def request(kind, target, attribute=0, point=None):
            self.calls.append(kind)
            if kind == 6:
                if push: self.ax.sequence += 1
                return self.hit
            if target['objectID'] == 1: return self.ax.observed.attributes[(123, 1)]
            value = copy.deepcopy(attributes())
            if target['objectID'] == 99 and covered:
                value[60]['ElementData'] = b'overlay'
            if target['objectID'] == 2 and changed:
                value.update(changed)
            return value
        self.ax.request = request

    async def test_live_token_identity_accepts_new_translation_id(self):
        self.reader()
        self.assertEqual(await self.ax.checked_point('ref'), (.25, .2))
        self.assertEqual(self.calls, [5, 5, 6, 5, 5, 6])

    async def test_movement_visibility_and_value_changes_refuse_before_hit(self):
        for changed in ({21: rect(11)}, {32: False}, {27: 0}, {53: 'changed'}):
            self.calls.clear()
            self.reader(changed=changed)
            with self.assertRaises(AXError): await self.ax.checked_point('ref')
            self.assertNotIn(6, self.calls)

    async def test_covering_element_and_push_during_check_are_rejected(self):
        for options in ({'covered': True}, {'push': True}):
            self.reader(**options)
            with self.assertRaises(AXError): await self.ax.checked_point('ref')

    async def test_unknown_expired_and_disconnected_refs_are_rejected(self):
        with self.assertRaises(AXError): await self.ax.checked_point('other-phone-ref')
        self.ax.observed_at -= 121
        with self.assertRaises(AXError): await self.ax.checked_point('ref')
        self.ax.observed_at = time.monotonic()
        await self.ax.close()
        with self.assertRaises(AXError): await self.ax.checked_point('ref')

    async def test_only_matching_initial_additional_pair_is_admitted(self):
        queue = asyncio.Queue()
        # Feed already-decoded inert records through the reader's archive boundary.
        from unittest.mock import patch
        from types import SimpleNamespace
        async def feed(payload):
            with patch('oiu.accessibility.Archive', return_value=SimpleNamespace(root=payload)):
                await queue.put(('processDataFromRemoteDevice:', [b'archive']))
                await asyncio.sleep(.01)
        reader = asyncio.create_task(self.ax.read(queue))
        try:
            await feed(envelope())
            self.assertIsNone(self.ax.tree)
            await feed(envelope('AXPTreeDumpTypeAdditionalData'))
            self.assertIsNotNone(self.ax.tree)
            await feed(envelope('AXPTreeDumpTypeAdditionalData'))
            self.assertIsNone(self.ax.tree)
            self.assertIsInstance(self.ax.error, AXError)
        finally:
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass


class FrameModeTests(unittest.TestCase):
    def test_modes_preserve_native_hash_and_preview_aspect_ratio(self):
        output = io.BytesIO()
        Image.new('RGB', (1200, 2400), 'white').save(output, format='JPEG')
        full, preview, none = [frame_payload(output.getvalue(), mode) for mode in ('full', 'preview', 'none')]
        self.assertEqual(preview['width'], 320)
        self.assertEqual(preview['height'], 640)
        self.assertEqual(preview['sourceHeight'], 2400)
        self.assertEqual(full['screenHash'], preview['screenHash'])
        self.assertEqual(full['screenHash'], none['screenHash'])
        self.assertNotIn('base64', none)
        self.assertLess(len(preview['base64']), len(full['base64']))

    def test_only_usb_uses_lower_post_input_latency(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from oiu.coredevice import stream_connection
        from pymobiledevice3.remote.core_device.vnc_server import VncStreamServer
        with patch.object(VncStreamServer, '__init__', return_value=None):
            connection = stream_connection()
            self.assertEqual(connection(SimpleNamespace(is_in_process_tunnel=True)).latency, .03)
            self.assertEqual(connection(SimpleNamespace(is_in_process_tunnel=False)).latency, .15)

    def test_mcp_advertises_optional_images_and_ref_without_frame(self):
        schemas = {t['name']: t['inputSchema'] for t in tools()}
        self.assertEqual(schemas['tap_ref']['required'], ['device_id', 'ref'])
        self.assertEqual(schemas['get_ui_tree']['required'], ['device_id'])
        self.assertEqual(schemas['batch']['properties']['steps']['maxItems'], 16)
        self.assertNotIn('image_mode', schemas['pointer_tap']['required'])


class BackendAgentTests(unittest.TestCase):
    def setUp(self):
        test_coredevice.CoreDeviceTests.setUp(self)
        class FakeAX:
            refresh = AsyncMock(return_value={'ok': True, 'rows': 'ref|tap|button|Settings|', 'count': 1})
            checked_point = AsyncMock(return_value=(.25, .2))
        self.connection.accessibility = FakeAX()

    def test_read_and_ref_need_no_screenshot_and_rejection_sends_nothing(self):
        before = self.device.last_input_completed_at
        self.assertTrue(self.device.action('get_ui_tree', {})['ok'])
        self.assertEqual(self.device.last_input_completed_at, before)
        result = self.device.action('tap_ref', {'ref': 'ref'})
        self.assertNotIn('base64', result['frame'])
        self.assertEqual(self.connection.calls[-1][0], 'tap')
        self.connection.calls.clear()
        self.connection.accessibility.checked_point.side_effect = AXError('targetCovered', 'covered')
        result = self.device.action('tap_ref', {'ref': 'ref'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'targetCovered')
        self.assertEqual(self.connection.calls, [])  # No touch or cleanup release.

    def test_batch_validates_all_steps_before_any_input(self):
        for bad in ({'operation': 'type_text', 'text': 'é'}, {'operation': []}, {'operation': 'scroll', 'dy': True}):
            result = self.device.action('batch', {'steps': [{'operation': 'send_key', 'key': 'home'}, bad]})
            self.assertFalse(result['ok'])
            self.assertEqual(self.connection.calls, [])

    def test_batch_stops_on_rejected_ref_without_cleanup_input(self):
        self.connection.accessibility.checked_point.side_effect = AXError('targetChanged', 'moved')
        result = self.device.action('batch', {'steps': [
            {'operation': 'send_key', 'key': 'home'}, {'operation': 'tap_ref', 'ref': 'ref'},
            {'operation': 'send_key', 'key': 'home'}]})
        self.assertEqual(result['completed'], 1)
        self.assertEqual(result['stoppedAt'], 1)
        self.assertEqual(self.connection.calls, [('button', 0x0C, 0x40)])

    def test_batch_runs_three_steps_with_one_final_frame(self):
        result = self.device.action('batch', {'steps': [
            {'operation': 'tap_ref', 'ref': 'ref'}, {'operation': 'send_key', 'key': 'home'},
            {'operation': 'get_ui_tree'}], 'image_mode': 'preview'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['completed'], 3)
        self.assertIn('base64', result['frame'])
        self.assertTrue(all('frame' not in step for step in result['results']))

    def test_cancel_batch_prevents_later_steps(self):
        import threading
        entered = threading.Event()
        async def waiting(ref):
            entered.set()
            await asyncio.Event().wait()
        self.connection.accessibility.checked_point.side_effect = waiting
        result = {}
        worker = threading.Thread(target=lambda: result.update(self.device.action('batch', {'steps': [
            {'operation': 'tap_ref', 'ref': 'ref'}, {'operation': 'send_key', 'key': 'home'}]})))
        worker.start()
        self.assertTrue(entered.wait(2))
        self.device.action('stop_control', {})
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(result['ok'])
        self.assertTrue(all(call[0] == 'release' for call in self.connection.calls))

    def test_stale_capture_ref_and_read_reject_without_release(self):
        self.device.at = time.monotonic() - 6
        for operation in ('tap_ref', 'get_ui_tree', 'batch'):
            with self.assertRaisesRegex(ValueError, 'Screen not live'):
                self.device.action(operation, {'ref': 'ref'})
        self.assertEqual(self.connection.calls, [])
