"""CoreDevice backend: Xcode's remote display HID and screenshots over a rootless RSD tunnel.

Needs the optional pymobiledevice3 dependency, Developer Mode and a USB-paired phone.
Touch and keyboard use dtuhidd's universal HID service. iOS only publishes it with a
Developer Disk Image that ships it (Xcode 27+) and Settings > Developer > UI Automation
enabled. Screenshots use the CoreDevice screen capture service, or the DVT instruments
screenshot service with older images, so capture works before HID is available.
Coordinates are absolute: no pointer, calibration or Safari sharing is involved.
"""
import asyncio
import base64
from concurrent.futures import CancelledError
import contextlib
import io
import threading
import time
import uuid

from . import vision
from .hid import chord, fraction, stroke

UNIT = 65535
HID_SERVICE = 'com.apple.coredevice.hid.universalhidservice'
SCREEN_CAPTURE_SERVICE = 'com.apple.coredevice.screencaptureservice'
HID_UNAVAILABLE = ('This iPhone does not publish CoreDevice HID. Mount a Developer Disk Image that ships it '
                   '(Xcode 27 or later) and enable Settings > Developer > UI Automation.')
MISSING_DEPENDENCY = 'CoreDevice control needs pymobiledevice3: pip install "open-iphone-use-server[coredevice]"'
# PyTCP's userspace stack is a process-global singleton: one tunnel, so one phone, per server.
_tunnel = threading.Lock()


def units(value):
    return round(fraction(value) * UNIT)


def usages(report):
    """Usage set held by an 8-byte keyboard report from hid.chord/keyboard."""
    return tuple(0xE0 + bit for bit in range(8) if report[0] >> bit & 1) + ((report[2],) if report[2] else ())


def held(modifier, usage):
    return usages(bytes([modifier, 0, usage]))


def jpeg(image_bytes):
    from PIL import Image
    with Image.open(io.BytesIO(image_bytes)) as image:
        output = io.BytesIO()
        image.convert('RGB').save(output, format='JPEG', quality=80)
        return output.getvalue()


def candidates():
    """USB-attached phones by UDID. Pairing and Developer Mode are checked when control starts."""
    try:
        from pymobiledevice3.usbmux import list_devices
    except ImportError:
        raise ValueError(MISSING_DEPENDENCY) from None
    devices = asyncio.run(list_devices())
    return [dict(udid=d.serial, connection=d.connection_type) for d in devices if d.is_usb]


class PmdConnection:
    def __init__(self, rsd, stack):
        self.rsd, self.stack = rsd, stack
        self.hid = self.keyboard = None

    @property
    def services(self):
        return self.rsd.peer_info.get('Services', {})

    @property
    def hid_available(self):
        return HID_SERVICE in self.services

    async def frames(self):
        if SCREEN_CAPTURE_SERVICE in self.services:
            from pymobiledevice3.remote.core_device.screen_capture_service import ScreenCaptureService
            async with ScreenCaptureService(self.rsd) as service:
                while True:
                    yield (await service.capture_screenshot())['image']
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot
        async with DvtProvider(self.rsd) as dvt, Screenshot(dvt) as service:
            while True:
                yield await service.get_screenshot()

    async def touch_service(self):
        if self.hid is None:
            if not self.hid_available: raise OSError(HID_UNAVAILABLE)
            from pymobiledevice3.remote.core_device.hid_service import touch_session
            # The session's media stream holds backboardd's HID authentication open.
            self.hid = await self.stack.enter_async_context(touch_session(self.rsd))
        return self.hid

    async def tap(self, x, y):
        await (await self.touch_service()).multi_tap([(x, y)])

    async def drag(self, x1, y1, x2, y2, duration):
        await (await self.touch_service()).multi_drag([(x1, y1, x2, y2)], steps=max(8, round(duration * 60)), duration=duration)

    async def release(self, x, y):
        from pymobiledevice3.remote.core_device.hid_service import TOUCHSCREEN_STATE_RELEASE
        if self.hid: await self.hid.send_touchscreen(TOUCHSCREEN_STATE_RELEASE, x, y)
        if self.keyboard is not None: await self.hid.send_keyboard(self.keyboard, ())

    async def press(self, held):
        service = await self.touch_service()
        if self.keyboard is None: self.keyboard = await service.create_keyboard_service()
        modifiers = tuple(u for u in held if u >= 0xE0)
        if modifiers and len(modifiers) < len(held):
            # iOS can ignore a modifier that arrives with its key; establish it first.
            await service.send_keyboard(self.keyboard, modifiers); await asyncio.sleep(.04)
        await service.send_keyboard(self.keyboard, held); await asyncio.sleep(.04)
        await service.send_keyboard(self.keyboard, ()); await asyncio.sleep(.02)


@contextlib.asynccontextmanager
async def open_connection(udid):
    try:
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
    except ImportError:
        raise OSError(MISSING_DEPENDENCY) from None
    async with UserspaceRsdTunnel(serial=udid) as rsd, contextlib.AsyncExitStack() as stack:
        yield PmdConnection(rsd, stack)


class CoreDeviceBackend:
    """One phone over CoreDevice. Frames are pulled from the phone and uploaded to its Device."""
    RETRY_SECONDS = 2

    def __init__(self, device, udid, opener=open_connection):
        if not _tunnel.acquire(blocking=False):
            raise ValueError('Only one CoreDevice phone per server is supported')
        self.device, self.udid, self.opener = device, udid, opener
        self.condition = threading.Condition()
        self.connection = None
        self.closed = False
        self.error = 'Connecting to the iPhone over CoreDevice'
        self.at = self.requested = self.pending = 0
        self.point = (.5, .5)
        self.running = self.task = None
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True, name='oiu-coredevice')
        self.thread.start()
        asyncio.run_coroutine_threadsafe(self.start(), self.loop).result(5)

    async def start(self):
        self.task = asyncio.create_task(self.run())

    @property
    def label(self):
        connection = self.connection
        if self.error or connection is None: return 'CoreDevice: ' + (self.error or 'reconnecting')
        if not connection.hid_available: return 'CoreDevice screen only: ' + HID_UNAVAILABLE
        return 'CoreDevice connected'

    def is_fresh(self):
        return bool(not self.closed and self.at and time.monotonic() - self.at <= 5)

    async def run(self):
        while not self.closed:
            try:
                async with self.opener(self.udid) as connection:
                    self.connection, self.error = connection, ''
                    frames = connection.frames()
                    while True:
                        requested = time.monotonic()
                        image = await anext(frames)
                        self.deliver(await asyncio.to_thread(jpeg, image), requested)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # Tunnel, pairing and service errors are reported, then retried.
                self.error = str(error) or type(error).__name__
            finally:
                self.connection = None
            await asyncio.sleep(self.RETRY_SECONDS)

    def deliver(self, image, requested):
        # A screenshot requested before an input ended may predate its effect.
        self.pending = requested
        try: self.device.upload(image)
        except ValueError: pass  # Removed device; close() stops the loop.

    def ingest(self, image):
        frame_id = str(uuid.uuid4())
        with self.condition:
            self.at, self.requested = time.monotonic(), self.pending
            self.condition.notify_all()
        return dict(frameID=frame_id)

    def wait_frame(self, after, timeout=5):
        with self.condition:
            if not self.condition.wait_for(lambda: self.closed or self.requested > after, timeout):
                raise OSError('No CoreDevice screenshot arrived after the input ended')
            if self.closed: raise ValueError('Device removed or control closed')

    def call(self, coroutine, timeout=60):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        self.running = future
        try: return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise OSError('CoreDevice input timed out') from None
        except CancelledError:
            raise ValueError('Action canceled by stop') from None
        except Exception as error:
            if isinstance(error, (OSError, ValueError)): raise
            raise OSError(str(error) or type(error).__name__) from error
        finally:
            self.running = None

    def connected(self):
        if self.connection is None: raise OSError(self.error or 'CoreDevice tunnel is not connected')
        return self.connection

    async def perform(self, connection, operation, args):
        if operation in ('ensure_session', 'prepare_session', 'pointer_quick_probe'):
            await connection.touch_service()
            app = args.get('return_app')
            if app is not None:
                await connection.press(usages(chord('cmd+space'))); await asyncio.sleep(.6)
                for character in app + '\n':
                    await connection.press(held(*stroke(character)))
                await asyncio.sleep(.5)
            return 'Absolute CoreDevice touch is ready; no calibration is needed'
        if operation == 'set_page_board': return 'CoreDevice needs no calibration board'
        if operation == 'pointer_home':
            self.point = (0., 0.)
            return 'Touch target reset to the top-left corner'
        if operation == 'move_pointer':
            raise ValueError('CoreDevice touches are absolute; use pointer_move or pointer_tap with x and y')
        if operation == 'pointer_move':
            self.point = (fraction(args['x']), fraction(args['y']))
            return 'Touch target set to (%s,%s); nothing was touched' % self.point
        if operation in ('pointer_tap', 'click'):
            if operation == 'click' and args.get('button', 'left') != 'left':
                raise ValueError('CoreDevice supports a primary touch only')
            if operation == 'pointer_tap': self.point = (fraction(args['x']), fraction(args['y']))
            await connection.tap(units(self.point[0]), units(self.point[1]))
            return 'Touched (%s,%s) with an absolute CoreDevice touch' % self.point
        if operation == 'type_text':
            text = args['text']
            if not isinstance(text, str) or len(text.encode()) > 500: raise ValueError('Text must contain at most 500 ASCII bytes')
            strokes = [stroke(c) for c in text]  # Validate all text before any report.
            for modifier, usage in strokes:
                await connection.press(held(modifier, usage))
            return None
        if operation == 'send_key':
            await connection.press(usages(chord(args['key'])))
            return None
        if operation == 'scroll':
            amount = args.get('dy', 0)
            if type(amount) is not int or abs(amount) > 1200: raise ValueError('Scroll dy must be an integer -1200…1200')
            if not amount: return 'Nothing to scroll'
            # Positive dy moves content up, as the HID wheel path does: drag the finger up.
            half = max(.05, min(.3, abs(amount) / 1200 * .3)) * (1 if amount > 0 else -1)
            await connection.drag(units(.5), units(.5 + half), units(.5), units(.5 - half), .3)
            return None
        if operation in ('swipe', 'advance_feed'):
            direction = args.get('profile', 'up') if operation == 'swipe' else ('up' if args.get('dy', 1) == 1 else 'down')
            if direction not in ('up', 'down', 'left', 'right'): raise ValueError('Swipe direction must be up/down/left/right')
            if operation == 'advance_feed' and args.get('dy', 1) not in (-1, 1): raise ValueError('Feed dy must be 1 or -1')
            start = {'up': (.5, .7), 'down': (.5, .3), 'left': (.75, .5), 'right': (.25, .5)}[direction]
            end = {'up': (.5, .3), 'down': (.5, .7), 'left': (.25, .5), 'right': (.75, .5)}[direction]
            await connection.drag(*map(units, start + end), .25)
            return 'Dragged ' + direction + (', always verify the next item in the returned frame' if operation == 'advance_feed' else '')
        raise ValueError('Unknown control operation')

    def request(self, operation, **args):
        if self.closed: raise ValueError('Device removed or control closed')
        if operation in ('capture_tap', 'capture_pointer'):
            return dict(ok=False, message='CoreDevice control does not use Safari sharing')
        if operation == 'stop_control': return self.stop_capture()
        if operation == 'get_device_status':
            connection = self.connection
            return dict(ok=True, message=self.label, status=dict(
                transport='coredevice', udid=self.udid, connected=connection is not None,
                hid_available=bool(connection and connection.hid_available), captureActive=self.is_fresh(),
                sharing_active=self.is_fresh(), error=self.error))
        try:
            message = self.call(self.perform(self.connected(), operation, args))
            ended = time.monotonic()
            if operation in ('swipe', 'advance_feed', 'scroll'): time.sleep(.2)
            self.wait_frame(ended)
            with self.device.lock:
                image, frame_id, frame_at = self.device.jpeg, self.device.frame_id, self.device.at
            width, height = vision.dimensions(image)
            result = dict(ok=True, message=message or 'Input completed. Inspect the returned screen to verify the UI outcome.',
                          inputEndedAt=ended, commandID=str(uuid.uuid4()),
                          frame=dict(frameID=frame_id, captureActive=self.is_fresh(),
                                     ageMilliseconds=int((time.monotonic() - frame_at) * 1000), mimeType='image/jpeg',
                                     width=width, height=height, base64=base64.b64encode(image).decode()))
            if operation == 'pointer_tap':
                result.update(target=list(self.point), click_sent=True, landing='absolute', verified=True,
                              observation_method='coredevice-absolute')
            return result
        except (ValueError, OSError) as error:
            return dict(ok=False, message=str(error))

    def stop_capture(self):
        running = self.running
        if running: running.cancel()
        connection = self.connection
        if connection is not None and connection.hid is not None:
            try: asyncio.run_coroutine_threadsafe(connection.release(units(self.point[0]), units(self.point[1])), self.loop).result(3)
            except Exception: pass  # Best effort: the session may already be gone.
        return dict(ok=True, message='Input canceled and released for this phone')

    def close(self):
        if self.closed: return
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        try: asyncio.run_coroutine_threadsafe(self.shutdown(), self.loop).result(10)
        except Exception: pass  # Teardown is best effort; the loop stops regardless.
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        _tunnel.release()

    async def shutdown(self):
        # Cancel inside the loop and await it so the tunnel unwinds before the loop stops.
        self.task.cancel()
        with contextlib.suppress(BaseException): await self.task

    def remove(self):
        self.close()
