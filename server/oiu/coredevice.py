"""CoreDevice backend: Xcode's remote display stream and HID over a rootless RSD tunnel.

Needs pymobiledevice3 (a macOS dependency), Developer Mode and a USB-paired phone.
With a Developer Disk Image that ships the display and universal HID services (Xcode 27+,
iOS 27+), the screen arrives as the HEVC stream Xcode mirrors, decoded with VideoToolbox,
and touch and keyboard go through dtuhidd. That stream must stay up for input: it holds
backboardd's HID authentication open. Older images fall back to screenshots without input.
Coordinates are absolute: no pointer, calibration or Safari sharing is involved.
"""
import asyncio
from concurrent.futures import CancelledError
import contextlib
import functools
import io
import threading
import time
import uuid

from .accessibility import AccessibilitySession, AXError
from .frames import frame_payload, image_mode
from .keys import chord, fraction, stroke

UNIT = 65535
HID_SERVICE = 'com.apple.coredevice.hid.universalhidservice'
DISPLAY_SERVICE = 'com.apple.coredevice.displayservice'
SCREEN_CAPTURE_SERVICE = 'com.apple.coredevice.screencaptureservice'
HID_UNAVAILABLE = ('This iPhone does not publish CoreDevice HID. Install Xcode 27 or later and open it once with '
                   'the phone connected; enable Settings > Developer > UI Automation if the phone shows it.')
MISSING_DEPENDENCY = 'USB control needs pymobiledevice3, which the macOS installer includes; rerun the installer'
# Hardware buttons over Indigo HID (consumer page). A keyboard's Home key does nothing on an
# iPhone, so "home" means the Home button here.
BUTTONS = dict(home=(0x0C, 0x40), home_button=(0x0C, 0x40), lock=(0x0C, 0x30), volume_up=(0x0C, 0xE9),
               volume_down=(0x0C, 0xEA), mute=(0x0C, 0xE2), siri=(0x0C, 0xCF))
# Over Wi-Fi the encoder is capped at this bitrate instead of the 6 Mbit/s Xcode negotiates.
# It halves the radio traffic (and the heat that comes with it) at a softer picture; the
# phone ignores requests for a smaller resolution.
WIFI_MAX_BITRATE = 2_000_000
# A static screen sends no new video frames; resend the last one so captures stay fresh.
STILL_FRAME_SECONDS = .5
# PyTCP's userspace stack is a process-global singleton: one USB tunnel, so one USB phone, per
# server. Wi-Fi phones ride remoted's tunnels and are not limited.
_usb_tunnel = threading.Lock()
USB_BUSY = 'Another phone already uses this server\'s USB connection; remove it or connect this phone over Wi-Fi'



def units(value):
    return round(fraction(value) * UNIT)


def held(modifier, usage):
    """HID keyboard usages held for a (modifier bits, usage) stroke: modifiers are 0xE0 + bit."""
    return tuple(0xE0 + bit for bit in range(8) if modifier >> bit & 1) + ((usage,) if usage else ())


def jpeg(image_bytes=None, bgra=None, size=None):
    from PIL import Image
    image = Image.frombuffer('RGB', size, bgra, 'raw', 'BGRX', 0, 1) if bgra else Image.open(io.BytesIO(image_bytes))
    with image:
        output = io.BytesIO()
        image.convert('RGB').save(output, format='JPEG', quality=80)
        return output.getvalue()


def capped(tiers, max_bitrate):
    """Offer bitrate tiers with every network cap (kind 0) lowered to max_bitrate."""
    return tuple((kind, min(bps, max_bitrate) if kind == 0 else bps, buffer) for kind, bps, buffer in tiers)


def candidates():
    """Paired physical phones reachable now, over USB or Wi-Fi (Xcode's devicectl), else USB only."""
    import json, subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix='.json') as output:
        try:
            subprocess.run(['xcrun', 'devicectl', 'list', 'devices', '--quiet', '--json-output', output.name],
                           check=True, capture_output=True, timeout=20)
            devices = json.load(open(output.name))['result']['devices']
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            return usb_candidates()
    transports = dict(wired='USB', localNetwork='Wi-Fi')
    return [dict(udid=d['hardwareProperties']['udid'], name=d['deviceProperties'].get('name'),
                 ios=d['deviceProperties'].get('osVersionNumber'), model=d['hardwareProperties'].get('productType'),
                 connection=transports[d['connectionProperties']['transportType']])
            for d in devices
            if d.get('hardwareProperties', {}).get('reality') == 'physical'
            and d.get('connectionProperties', {}).get('pairingState') == 'paired'
            and d['connectionProperties'].get('transportType') in transports]


def usb_candidates():
    try:
        from pymobiledevice3.usbmux import list_devices
    except ImportError:
        raise ValueError(MISSING_DEPENDENCY) from None

    async def listing():
        return [dict(udid=d.serial, connection='USB', **await details(d.serial))
                for d in await list_devices() if d.is_usb]
    return asyncio.run(listing())


_details = {}


async def details(udid):
    """Owner-chosen name, iOS version and model, cached per UDID; empty for an untrusted host."""
    if udid not in _details:
        from pymobiledevice3.lockdown import create_using_usbmux
        try:
            lockdown = await create_using_usbmux(serial=udid, autopair=False)
            info = lockdown.short_info
            _details[udid] = dict(name=info.get('DeviceName'), ios=info.get('ProductVersion'), model=info.get('ProductType'))
        except Exception:
            return {}  # Not trusted yet: retry on the next listing.
    return _details[udid]


class ScreenshotConnection:
    """Screen only, for images without the display stream; HID cannot authenticate here."""
    hid = None
    latency = 0

    def __init__(self, rsd, reason=HID_UNAVAILABLE):
        self.rsd, self.reason = rsd, reason
        self.accessibility = AccessibilitySession(rsd)

    hid_available = False

    async def frames(self):
        if SCREEN_CAPTURE_SERVICE in self.rsd.peer_info.get('Services', {}):
            from pymobiledevice3.remote.core_device.screen_capture_service import ScreenCaptureService
            async with ScreenCaptureService(self.rsd) as service:
                while True:
                    yield await asyncio.to_thread(jpeg, (await service.capture_screenshot())['image'])
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot
        async with DvtProvider(self.rsd) as dvt, Screenshot(dvt) as service:
            while True:
                yield await asyncio.to_thread(jpeg, await service.get_screenshot())

    async def touch_service(self):
        raise OSError(self.reason)

    tap = drag = press = button = touch_service

    async def release(self, x, y):
        pass

    async def aclose(self):
        await self.accessibility.close()


@functools.cache
def stream_connection():
    """Reuses pymobiledevice3's VNC pipeline: RTP depacketizing, VideoToolbox decode,
    keyframe recovery and the RTCP keepalive without which iOS drops the stream in ~25 s."""
    from pymobiledevice3.remote.core_device.display_service import DisplayService, require_video_stream_support
    from pymobiledevice3.remote.core_device.hid_service import TOUCH_AUTH_SETTLE_SECONDS, TOUCHSCREEN_STATE_RELEASE
    from pymobiledevice3.remote.core_device.screen_stream import open_media_receiver
    from pymobiledevice3.remote.core_device.vnc_server import VncStreamServer

    class StreamConnection(VncStreamServer):
        hid_available = True
        # Frames still in the encoder when an input ends may predate its effect.
        latency = .15

        def __init__(self, rsd):
            super().__init__(rsd)
            self.accessibility = AccessibilitySession(rsd)
            self.latency = .03 if rsd.is_in_process_tunnel else .15
            self.updated = asyncio.Event()
            self.tasks = []
            self.service = self.transport = None

        @property
        def hid(self):
            return self._uhs

        async def open(self):
            require_video_stream_support(self._rsd)
            self.service = DisplayService(self._rsd)
            await self.service.connect()
            self.transport, receiver = self.media_receiver()
            if self._rsd.is_in_process_tunnel:
                start = self.service.start_video_stream(
                    receiver_ip=receiver, receiver_port=self.transport.port, sender_ip=self._sender_ip,
                    display_id=self._display_id, client_session_id=uuid.uuid4())
            else:
                start = self.start_capped_stream(receiver, WIFI_MAX_BITRATE)
            answer = await asyncio.wait_for(start, 15)
            config = answer['connection'].get('streamConfig', {})
            self.max_bitrate = config.get('TXMaxBitrate')
            port = int(config.get('SourcePort', 0))
            # streamConfig names SSRCs from the device's side: ours is RemoteSSRC.
            self._local_ssrc, self._remote_ssrc = int(config.get('RemoteSSRC', 0)), int(config.get('LocalSSRC', 0))
            self._rtcp_dest = (self._sender_ip, port) if port else None
            self._active_transport = self.transport
            self._loop = asyncio.get_running_loop()
            self.tasks = [asyncio.create_task(self._udp_recv_and_pipe(self.transport)),
                          asyncio.create_task(self._decoder_refresh_loop()),
                          asyncio.create_task(self._rtcp_send_loop(self.transport))]
            await asyncio.sleep(TOUCH_AUTH_SETTLE_SECONDS)

        async def start_capped_stream(self, receiver, max_bitrate):
            """DisplayService.start_video_stream with its bitrate tiers capped. The request is the
            same; only the offer's network bitrate caps change, and the phone honours them."""
            from pymobiledevice3.remote.core_device import display_service as ds, media_stream_offer as offers
            from pymobiledevice3.remote.xpc_message import XpcInt64Type, XpcUInt64Type
            import plistlib, random, zlib
            tiers = capped(offers._DEFAULT_VIDEO_BITRATE_TIERS, max_bitrate)
            blob = offers.build_media_blob_video(random.randint(0, 0xFFFFFFFF), bitrate_tiers=tiers)
            offer = plistlib.dumps({
                'avcMediaStreamOptionRemoteEndpointInfo': offers.build_remote_endpoint_info('Mac15,9', '2205.3.1', '25F80'),
                'avcMediaStreamNegotiatorMode': offers.NEGOTIATOR_MODE_VIDEO,
                'avcMediaStreamNegotiatorMediaBlob': zlib.compress(blob, level=9),
                'avcMediaStreamOptionCallID': str(uuid.uuid4()).upper()}, fmt=plistlib.FMT_BINARY)
            return await self.service.invoke('com.apple.coredevice.feature.startmediastream', {
                'clientSupportedFeatures': XpcUInt64Type(ds._CLIENT_SUPPORTED_FEATURES), 'direction': 'output',
                'negotiatorOffer': offer,
                'options': {
                    'AVCMediaStreamNegotiatorAccessNetworkType': {'int': XpcInt64Type(ds._DEFAULT_ACCESS_NETWORK_TYPE)},
                    'AVCMediaStreamNegotiatorTransportProtocolType': {'int': XpcInt64Type(ds._DEFAULT_TRANSPORT_PROTOCOL_TYPE)},
                    'CoreDeviceVideoDisplayMode': {'string': 'DisplayByID'},
                    'VideoStreamForDisplayID': {'int': XpcInt64Type(self._display_id)},
                    'avcMediaStreamOptionClientSessionID': {'uuid': uuid.uuid4()}},
                'receiverIP': receiver, 'receiverPort': XpcUInt64Type(self.transport.port), 'senderIP': self._sender_ip,
                'timeout': XpcUInt64Type(20), 'type': 'video'},
                action_identifier='com.apple.coredevice.action.mediastreamstart')

        def media_receiver(self):
            """Where the phone sends RTP. pymobiledevice3 chooses the userspace stack whenever any
            userspace tunnel is open in this process, but a Wi-Fi phone on remoted's tunnel can only
            reach a host kernel socket, so decide by this phone's own tunnel."""
            if self._rsd.is_in_process_tunnel:
                return open_media_receiver(self.service, (8 * 1024 * 1024, 4 * 1024 * 1024))
            import socket
            from pymobiledevice3.remote.core_device.screen_stream import _KernelUdp
            sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
            sock.bind(('::', 0))
            for size in (8 * 1024 * 1024, 4 * 1024 * 1024):
                with contextlib.suppress(OSError):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, size)
                    break
            return _KernelUdp(sock), self.service.service.local_address[0]

        def _broadcast_frame(self, bgra):
            super()._broadcast_frame(bgra)
            self.updated.set()

        async def frames(self):
            last = None
            while True:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.updated.wait(), STILL_FRAME_SECONDS)
                if self.updated.is_set() or last is None:
                    await self.updated.wait()
                    self.updated.clear()
                    last = await asyncio.to_thread(jpeg, bgra=self._latest_frame, size=(self._fb_width, self._fb_height))
                yield last

        async def touch_service(self):
            await self._ensure_hid()
            return self._uhs

        async def tap(self, x, y):
            await (await self.touch_service()).multi_tap([(x, y)])

        async def drag(self, x1, y1, x2, y2, duration):
            await (await self.touch_service()).multi_drag([(x1, y1, x2, y2)], steps=max(8, round(duration * 60)), duration=duration)

        async def release(self, x, y):
            if self._uhs is None: return
            await self._uhs.send_touchscreen(TOUCHSCREEN_STATE_RELEASE, x, y)
            if self._kb_service_id is not None: await self._uhs.send_keyboard(self._kb_service_id, ())

        async def button(self, page, code):
            from pymobiledevice3.remote.core_device.hid_service import HID_BUTTON_STATE_DOWN, HID_BUTTON_STATE_UP
            await self._ensure_indigo()
            await self._indigo.send_button(page, code, HID_BUTTON_STATE_DOWN); await asyncio.sleep(.08)
            await self._indigo.send_button(page, code, HID_BUTTON_STATE_UP)

        async def press(self, keys):
            await self._ensure_keyboard()
            service, keyboard = self._uhs, self._kb_service_id
            modifiers = tuple(u for u in keys if u >= 0xE0)
            if modifiers and len(modifiers) < len(keys):
                # iOS can ignore a modifier that arrives with its key; establish it first.
                await service.send_keyboard(keyboard, modifiers); await asyncio.sleep(.04)
            await service.send_keyboard(keyboard, keys); await asyncio.sleep(.04)
            await service.send_keyboard(keyboard, ()); await asyncio.sleep(.02)

        async def aclose(self):
            await self.accessibility.close()
            for task in self.tasks: task.cancel()
            for task in self.tasks:
                with contextlib.suppress(BaseException): await task
            await self._stop_hid()
            if self._transcoder is not None:
                with contextlib.suppress(Exception): self._transcoder.close()
            if self.transport is not None:
                with contextlib.suppress(Exception): self.transport.close()
            if self.service is not None:
                with contextlib.suppress(Exception): await self.service.close()
                # Release the device-side session so the camera and microphone are freed.
                with contextlib.suppress(Exception): await asyncio.wait_for(DisplayService.stop_all_streams(self._rsd), 6)

    return StreamConnection


@contextlib.asynccontextmanager
async def tunnel(udid):
    """USB: our own rootless userspace tunnel. Wi-Fi: ride the tunnel macOS's remoted already
    keeps to a wirelessly paired phone (no root); remoted may briefly take its RSD connection back,
    and the backend then reconnects."""
    try:
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
        from pymobiledevice3.usbmux import list_devices
    except ImportError:
        raise OSError(MISSING_DEPENDENCY) from None
    if any(d.serial == udid and d.is_usb for d in await list_devices()):
        if not _usb_tunnel.acquire(blocking=False): raise OSError(USB_BUSY)
        try:
            async with UserspaceRsdTunnel(serial=udid) as rsd:
                yield rsd, 'USB'
        finally:
            _usb_tunnel.release()
        return
    from pymobiledevice3.remote.native_tunnel import NativeRemotedTunnel
    native = NativeRemotedTunnel(serial=udid)
    rsd = await native.aopen()
    try:
        yield rsd, 'Wi-Fi'
    finally:
        await native.aclose()


@contextlib.asynccontextmanager
async def open_connection(udid):
    async with tunnel(udid) as (rsd, transport):
        services = rsd.peer_info.get('Services', {})
        connection = ScreenshotConnection(rsd)
        if HID_SERVICE in services and DISPLAY_SERVICE in services:
            stream = stream_connection()(rsd)
            try:
                await stream.open()
                connection = stream
            except Exception as error:  # Media in use (camera, microphone) or a wedged daemon.
                await stream.aclose()
                connection = ScreenshotConnection(rsd, 'The screen stream that authorizes input did not start: '
                                                       + (str(error) or type(error).__name__))
        connection.transport = transport
        try:
            yield connection
        finally:
            await connection.aclose()


class CoreDeviceBackend:
    """One phone over CoreDevice. Frames are pulled from the phone and uploaded to its Device."""
    RETRY_SECONDS = 2

    def __init__(self, device, udid, opener=open_connection):
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
        if self.error or connection is None: return 'Not connected: ' + (self.error or 'reconnecting')
        if not connection.hid_available: return 'Screen only: ' + connection.reason
        return 'Connected over ' + getattr(connection, 'transport', 'USB')

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
                        self.deliver(await anext(frames), requested)
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

    @property
    def connection_latency(self):
        return getattr(self.connection, 'latency', 0)

    def connected(self):
        if self.connection is None: raise OSError(self.error or 'CoreDevice tunnel is not connected')
        return self.connection

    async def perform(self, connection, operation, args):
        if operation == 'ensure_session':
            await connection.touch_service()
            app = args.get('return_app')
            if app is not None:
                await connection.press(held(*chord('cmd+space'))); await asyncio.sleep(.6)
                for character in app + '\n':
                    await connection.press(held(*stroke(character)))
                await asyncio.sleep(.5)
            return 'Input is ready'
        if operation == 'pointer_move':
            self.point = (fraction(args['x']), fraction(args['y']))
            return 'Touch target set to (%s,%s); nothing was touched' % self.point
        if operation in ('pointer_tap', 'click'):
            if operation == 'click' and args.get('button', 'left') != 'left':
                raise ValueError('Only a primary touch is supported')
            if operation == 'pointer_tap': self.point = (fraction(args['x']), fraction(args['y']))
            await connection.tap(units(self.point[0]), units(self.point[1]))
            return 'Touched (%s,%s)' % self.point
        if operation == 'type_text':
            text = args['text']
            if not isinstance(text, str) or len(text.encode()) > 500: raise ValueError('Text must contain at most 500 ASCII bytes')
            strokes = [stroke(c) for c in text]  # Validate all text before any report.
            for modifier, usage in strokes:
                await connection.press(held(modifier, usage))
            return None
        if operation == 'send_key':
            key = args['key']
            if isinstance(key, str) and key.lower() in BUTTONS:
                await connection.button(*BUTTONS[key.lower()])
                return 'Pressed the %s hardware button' % key.lower()
            await connection.press(held(*chord(key)))
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

    def response_frame(self, mode):
        with self.device.lock:
            image, frame_id, frame_at = self.device.jpeg, self.device.frame_id, self.device.at
        return frame_payload(image, mode, frameID=frame_id, captureActive=self.is_fresh(),
                             ageMilliseconds=int((time.monotonic() - frame_at) * 1000))

    def validate_batch(self, steps):
        fields = {'tap_ref': {'ref'}, 'get_ui_tree': set(), 'type_text': {'text'},
                  'send_key': {'key'}, 'scroll': {'dy'}, 'swipe': {'profile'}, 'advance_feed': {'dy'}}
        if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
            raise ValueError('Batch must contain 1 to 16 steps')
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get('operation'), str) or step['operation'] not in fields:
                raise ValueError('Unsupported batch operation')
            operation = step['operation']
            if set(step) != fields[operation] | {'operation'}:
                raise ValueError('Invalid or missing batch step arguments')
            if operation == 'tap_ref' and (not isinstance(step['ref'], str) or not step['ref']):
                raise ValueError('Invalid ref')
            if operation == 'type_text':
                value = step['text']
                if not isinstance(value, str) or len(value.encode()) > 500:
                    raise ValueError('Text must contain at most 500 ASCII bytes')
                for character in value:
                    stroke(character)
            if operation == 'send_key':
                value = step['key']
                if not isinstance(value, str):
                    raise ValueError('Invalid key')
                if value.lower() not in BUTTONS:
                    chord(value)
            if operation == 'scroll' and (type(step['dy']) is not int or abs(step['dy']) > 1200):
                raise ValueError('Scroll dy must be an integer -1200…1200')
            if operation == 'advance_feed' and (type(step['dy']) is not int or step['dy'] not in (-1, 1)):
                raise ValueError('Feed dy must be 1 or -1')
            if operation == 'swipe' and step['profile'] not in ('up', 'down', 'left', 'right'):
                raise ValueError('Swipe direction must be up/down/left/right')

    async def execute(self, connection, operation, args):
        if operation == 'get_ui_tree':
            return await connection.accessibility.refresh()
        if operation == 'tap_ref':
            try:
                await connection.touch_service()
                point = await connection.accessibility.checked_point(args['ref'])
            except AXError:
                raise
            except Exception as error:
                raise AXError('accessibilityUnavailable', str(error) or 'Accessibility target check failed') from error
            # No await between admission and entering the existing HID path.
            self.point = point
            await connection.tap(*map(units, point))
            message = 'Touched checked accessibility target'
        else:
            message = await self.perform(connection, operation, args)
        ended = time.monotonic()
        await asyncio.to_thread(self.wait_frame, ended + self.connection_latency)
        return dict(ok=True, message=message or 'Input completed. Inspect the result to verify the UI outcome.',
                    inputEndedAt=ended, inputSent=operation != 'pointer_move', commandID=str(uuid.uuid4()))

    async def execute_batch(self, connection, steps):
        results = []
        ended = None
        deadline = asyncio.get_running_loop().time() + 80
        for index, step in enumerate(steps):
            if not self.is_fresh():
                result = dict(ok=False, code='screenStale', message='Screen is no longer live', inputSent=False)
            else:
                try:
                    async with asyncio.timeout_at(deadline):
                        result = await self.execute(connection, step['operation'], {k: v for k, v in step.items() if k != 'operation'})
                except (ValueError, OSError, TimeoutError) as error:
                    result = dict(ok=False, code=getattr(error, 'code', 'actionFailed'), message=str(error),
                                  inputSent=not isinstance(error, AXError) and step['operation'] != 'get_ui_tree')
            results.append(dict(index=index, **result))
            ended = result.get('inputEndedAt', ended)
            if not result.get('ok'):
                break
        return dict(ok=all(r['ok'] for r in results), results=results, completed=sum(r['ok'] for r in results),
                    inputEndedAt=ended, inputSent=any(r.get('inputSent') for r in results),
                    stoppedAt=results[-1]['index'] if not results[-1]['ok'] else None,
                    cleanupRequired=not results[-1]['ok'] and results[-1].get('inputSent', True))

    def request(self, operation, **args):
        if self.closed: raise ValueError('Device removed or control closed')
        if operation == 'stop_control': return self.stop_capture()
        if operation == 'get_device_status':
            connection = self.connection
            return dict(ok=True, message=self.label, status=dict(
                connection=getattr(connection, 'transport', None), max_bitrate=getattr(connection, 'max_bitrate', None),
                udid=self.udid, connected=connection is not None,
                hid_available=bool(connection and connection.hid_available), captureActive=self.is_fresh(),
                sharing_active=self.is_fresh(), error=self.error))
        try:
            mode = image_mode(args.get('image_mode', 'none' if operation in ('get_ui_tree', 'tap_ref', 'batch') else 'full'))
            if operation == 'batch':
                self.validate_batch(args.get('steps'))
        except ValueError as error:
            return dict(ok=False, code='invalidArguments', message=str(error), inputSent=False)
        try:
            connection = self.connected()
            if operation == 'batch':
                result = self.call(self.execute_batch(connection, args['steps']), timeout=85)
            else:
                result = self.call(self.execute(connection, operation, args))
            result['frame'] = self.response_frame(mode)
            if operation == 'pointer_tap':
                result.update(target=list(self.point))
            return result
        except (ValueError, OSError, TimeoutError) as error:
            return dict(ok=False, code=getattr(error, 'code', 'actionFailed'), message=str(error),
                        inputSent=not isinstance(error, AXError) and operation != 'get_ui_tree')

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
        try: asyncio.run_coroutine_threadsafe(self.shutdown(), self.loop).result(20)
        except Exception: pass  # Teardown is best effort; the loop stops regardless.
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        if not self.thread.is_alive():
            self.loop.close()

    async def shutdown(self):
        # Cancel inside the loop and await it so the tunnel unwinds before the loop stops.
        self.task.cancel()
        with contextlib.suppress(BaseException): await self.task

    def remove(self):
        self.close()
