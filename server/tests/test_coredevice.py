import asyncio
import contextlib
import io
import tempfile
import threading
import time
import unittest

from PIL import Image

from oiu import coredevice
from oiu.coredevice import CoreDeviceBackend, UNIT, held, usages
from oiu.core import Device, Registry
from oiu.hid import chord

_image = io.BytesIO()
Image.new('RGB', (9, 19), 'white').save(_image, format='PNG')
PNG = _image.getvalue()


class FakeConnection:
    def __init__(self, hid_available=True):
        self.hid_available = hid_available
        self.hid = None
        self.calls = []
        self.frame_delay = .01

    async def frames(self):
        while True:
            await asyncio.sleep(self.frame_delay)
            yield PNG

    async def touch_service(self):
        if not self.hid_available: raise OSError(coredevice.HID_UNAVAILABLE)
        self.hid = self.hid or object()
        return self.hid

    async def tap(self, x, y):
        await self.touch_service(); self.calls.append(('tap', x, y))

    async def drag(self, *path):
        await self.touch_service(); self.calls.append(('drag', *path))

    async def press(self, keys):
        await self.touch_service(); self.calls.append(('press', keys))

    async def release(self, x, y):
        self.calls.append(('release', x, y))


def opener(connection):
    @contextlib.asynccontextmanager
    async def open_connection(udid):
        yield connection
    return open_connection


class CoreDeviceTests(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        self.device = Device('phone', 'Phone', 'token', udid='00008110-TEST')
        self.device.backend = CoreDeviceBackend(self.device, self.device.udid, opener(self.connection))
        self.addCleanup(self.device.backend.close)
        deadline = time.monotonic() + 3
        while not self.device.summary()['capture_active'] and time.monotonic() < deadline: time.sleep(.01)

    def act(self, operation, **arguments):
        deadline = time.monotonic() + 3  # A failed action also invalidates earlier frames.
        while self.device.at <= self.device.last_input_completed_at and time.monotonic() < deadline: time.sleep(.01)
        if operation not in ('ensure_session', 'prepare_session', 'pointer_quick_probe'):
            arguments['expectedFrameID'] = self.device.frame_id
        return self.device.action(operation, arguments)

    def test_frames_flow_without_sharing_or_bluetooth(self):
        summary = self.device.summary()
        self.assertTrue(summary['capture_active'])
        self.assertEqual(summary['transport'], 'coredevice')
        self.assertEqual(summary['control_status'], 'CoreDevice connected')

    def test_tap_is_absolute_and_returns_a_later_frame(self):
        result = self.act('pointer_tap', x=.25, y=1)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.connection.calls, [('tap', round(.25 * UNIT), UNIT)])
        self.assertEqual(result['landing'], 'absolute')
        self.assertGreater(self.device.backend.requested, result['inputEndedAt'])
        self.assertEqual(result['frame']['frameID'], self.device.frame_id)
        self.assertTrue(self.act('pointer_tap', x=.5, y=.5)['ok'])  # The returned frame is valid next.

    def test_click_uses_the_last_pointer_target(self):
        self.act('pointer_move', x=.1, y=.2)
        self.act('click')
        self.assertEqual(self.connection.calls, [('tap', round(.1 * UNIT), round(.2 * UNIT))])

    def test_relative_motion_is_refused(self):
        result = self.act('move_pointer', dx=3, dy=0)
        self.assertFalse(result['ok'])
        self.assertIn('absolute', result['message'])
        self.assertEqual(self.connection.calls, [])

    def test_text_is_validated_before_any_key(self):
        self.assertFalse(self.act('type_text', text='aé')['ok'])
        self.assertEqual(self.connection.calls, [])
        self.assertTrue(self.act('type_text', text='aB')['ok'])
        self.assertEqual(self.connection.calls, [('press', (0x04,)), ('press', (0xE1, 0x05))])

    def test_key_chords_map_modifiers_to_usages(self):
        self.assertEqual(usages(chord('cmd+space')), (0xE3, 0x2C))
        self.assertEqual(held(0, 0x28), (0x28,))
        self.act('send_key', key='cmd+h')
        self.assertEqual(self.connection.calls, [('press', (0xE3, 0x0B))])

    def test_swipes_and_scrolls_drag_the_finger(self):
        self.assertFalse(self.act('advance_feed', dy=1)['settled'])
        self.act('scroll', dy=600)
        drag, scroll = self.connection.calls
        self.assertEqual(drag[1:5], (round(.5 * UNIT), round(.7 * UNIT), round(.5 * UNIT), round(.3 * UNIT)))
        self.assertGreater(scroll[2], scroll[4])  # Positive dy drags up, moving content up.

    def test_missing_hid_reports_the_requirement(self):
        self.connection.hid_available = False
        result = self.act('ensure_session')
        self.assertFalse(result['ok'])
        self.assertIn('UI Automation', result['message'])

    def test_one_coredevice_phone_per_server(self):
        with self.assertRaises(ValueError):
            CoreDeviceBackend(Device('two', 'Two', 'token', udid='other'), 'other', opener(FakeConnection()))

    def test_stop_releases_the_touch(self):
        self.act('pointer_tap', x=.5, y=.5)
        self.device.action('stop_control', {})
        self.assertEqual(self.connection.calls[-1][0], 'release')


class FailingOpenerTests(unittest.TestCase):
    def test_connection_errors_are_reported_and_retried(self):
        attempts = threading.Event()

        @contextlib.asynccontextmanager
        async def failing(udid):
            attempts.set()
            raise OSError('Developer Mode is off')
            yield

        device = Device('phone', 'Phone', 'token', udid='u1')
        backend = CoreDeviceBackend(device, 'u1', failing)
        backend.RETRY_SECONDS = 0
        self.addCleanup(backend.close)
        self.assertTrue(attempts.wait(2))
        time.sleep(.05)
        self.assertEqual(backend.label, 'CoreDevice: Developer Mode is off')
        self.assertFalse(backend.request('pointer_tap', x=.5, y=.5)['ok'])


class RegistryTests(unittest.TestCase):
    def test_coredevice_phones_persist_by_udid(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Registry(directory)
            device = registry.create_coredevice('00008110-000A2D842EA1801E')
            self.assertIs(registry.create_coredevice('00008110-000A2D842EA1801E'), device)
            with self.assertRaises(ValueError): registry.create_coredevice('../bad')
            item, = registry.list(include_links=True)
            self.assertEqual((item['transport'], item['udid']), ('coredevice', device.udid))
            self.assertEqual(Registry(directory).devices[device.id].udid, device.udid)
            self.assertTrue(registry.global_status()['bluetooth_connected'])


if __name__ == '__main__':
    unittest.main()
