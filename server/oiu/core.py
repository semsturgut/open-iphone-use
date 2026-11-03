"""Persistent phones, their latest frames and bounded, serialized action queues."""
from collections import deque
from dataclasses import dataclass, field
import json
import base64
import threading
import time
import uuid

from . import vision
from .files import atomic_json
from .names import specific
from .permissions import secure_directory

FRAME_MAX_AGE = 120.0
# At 45 fps this retains three minutes of IDs, including expired IDs so their age can be reported.
FRAME_HISTORY_LIMIT = 8192
MAX_FRAME = 4 * 1024 * 1024
PREPARATION = {"ensure_session"}
ACTIONS = {"ensure_session", "pointer_tap", "pointer_move", "click", "type_text", "send_key", "scroll",
           "swipe", "advance_feed"}


class Unavailable:
    """Placeholder control for a phone whose connection could not be created."""
    def __init__(self, reason="Control unavailable"):
        self.label = reason

    def request(self, operation, **arguments):
        return {"ok": False, "message": self.label}

    def ingest(self, jpeg):
        return {}

    def stop_capture(self):
        return {"ok": True, "message": "Nothing to cancel"}

    def close(self):
        pass


@dataclass
class Device:
    id: str
    name: str
    udid: str
    name_source: str = "fallback"
    backend: object = field(default_factory=Unavailable)
    jpeg: bytes = b""
    frame_id: str | None = None
    at: float = 0
    active: bool = False
    history: deque = field(default_factory=lambda: deque(maxlen=FRAME_HISTORY_LIMIT))
    last_input_completed_at: float = 0
    lock: object = field(default_factory=threading.RLock)
    action_lock: object = field(default_factory=threading.Lock)
    slots: object = field(default_factory=lambda: threading.BoundedSemaphore(16))
    epoch: int = 0
    removed: bool = False

    def require_present(self):
        if self.removed:
            raise ValueError("Device removed")

    def summary(self):
        with self.lock:
            age = int((time.monotonic() - self.at) * 1000) if self.at else None
            live = self.active and age is not None and age <= 5000
            return dict(device_id=self.id, name=self.name, udid=self.udid, name_source=self.name_source,
                        frame_id=self.frame_id, age_ms=age, capture_active=live,
                        control_status=getattr(self.backend, "label", "Control configured"))

    def upload(self, jpeg):
        if len(jpeg) > MAX_FRAME or not jpeg.startswith(b"\xff\xd8\xff"):
            raise ValueError("Expected JPEG up to 4 MiB")
        with self.lock:
            self.require_present()
            controls = self.backend.ingest(jpeg)
            self.frame_id = controls.get("frameID", str(uuid.uuid4()))
            self.jpeg, self.at, self.active = jpeg, time.monotonic(), True
            self.history.append((self.frame_id, self.at))
            return {"frameID": self.frame_id}

    def stop(self):
        with self.lock:
            self.epoch += 1
            self.active = False
            self.history.clear()
        return self.backend.stop_capture()

    def action(self, operation, arguments):
        with self.lock:
            self.require_present()
        if operation == "stop_control":
            return self.stop()  # Bypasses the action queue to cancel immediately.
        if operation not in ACTIONS:
            raise ValueError("Unknown action")
        if not self.slots.acquire(blocking=False):
            raise ValueError("Device action queue full")
        try:
            generation = self.epoch
            with self.action_lock:
                with self.lock:
                    self.require_present()
                    if generation != self.epoch:
                        raise ValueError("Action canceled by stop")
                    expected = arguments.get("expectedFrameID")
                    if not self.summary()["capture_active"]:
                        raise ValueError("Screen not live: no frame in the last five seconds")
                    if operation not in PREPARATION:
                        frame_at = next((at for fid, at in reversed(self.history) if fid == expected), None)
                        retry = "Call get_latest_screenshot and retry with its frame_id."
                        if frame_at is None:
                            raise ValueError(f"Unknown frame id for this device. {retry}")
                        if time.monotonic() - frame_at > FRAME_MAX_AGE:
                            raise ValueError(f"Frame is older than {int(FRAME_MAX_AGE)} seconds. {retry}")
                        if frame_at <= self.last_input_completed_at:
                            raise ValueError(f"Frame was captured before your last action completed. {retry}")
                # Record even failed or interrupted backend calls: they may have
                # delivered partial input. Frames arriving during the call do not
                # prove that the caller observed its final effect.
                try:
                    result = self.backend.request(operation, **arguments)
                    if not result.get("ok"):
                        self.backend.request("stop_control")
                        with self.lock:
                            self.last_input_completed_at = time.monotonic()
                    else:
                        with self.lock:
                            # The backend reports when the input actually ended;
                            # frames captured after that moment are valid for the next action.
                            self.last_input_completed_at = result.get("inputEndedAt") if isinstance(result.get("inputEndedAt"), float) else time.monotonic()
                    frame = result.get('frame')
                    if isinstance(frame, dict) and frame.get('base64'):
                        raw = base64.b64decode(frame['base64'], validate=True)
                        frame['width'], frame['height'] = vision.dimensions(raw)
                    if operation in ('swipe', 'advance_feed', 'scroll') and result.get('ok'):
                        result.update(settled=False, settle_hint='UI may still be animating; take a fresh screenshot before targeting.')
                    return result
                except (OSError, ValueError):
                    with self.lock:
                        self.last_input_completed_at = time.monotonic()
                    raise
        except (OSError, ValueError):
            self.backend.request("stop_control")
            raise
        finally:
            self.slots.release()


class Registry:
    def __init__(self, directory):
        self.directory = secure_directory(directory)
        self.lock = threading.RLock()
        self.path = self.directory / "state.json"
        saved = json.loads(self.path.read_text()) if self.path.exists() else {"devices": []}
        self.backend_factory = None
        # Phones from the Bluetooth releases have no UDID and cannot be controlled here.
        self.devices = {d["id"]: Device(d["id"], d["name"], d["udid"], d.get("name_source", "fallback"))
                        for d in saved["devices"] if d.get("udid")}
        self.save()

    def save(self):
        with self.lock:
            atomic_json(self.path, dict(devices=[dict(id=d.id, name=d.name, udid=d.udid, name_source=d.name_source)
                                                 for d in self.devices.values()]))

    def create(self, udid, name=None):
        if not isinstance(udid, str) or not 1 <= len(udid) <= 64 or not all(c.isalnum() or c == '-' for c in udid):
            raise ValueError('Select a connected phone using its UDID')
        with self.lock:
            existing = next((d for d in self.devices.values() if d.udid == udid), None)
            if existing: return existing
            if len(self.devices) >= 64:
                raise ValueError("64-device limit reached")
            clean = specific(name)
            device = Device(uuid.uuid4().hex, clean or 'iPhone ' + udid[-6:], udid, 'device' if clean else 'fallback')
            if self.backend_factory: device.backend = self.backend_factory(device)
            self.devices[device.id] = device
            try: self.save()
            except OSError:
                del self.devices[device.id]
                device.backend.close()
                raise
            return device

    def remove(self, device_id):
        # Persist removal before reporting success. A stale handler reference is
        # tombstoned as well, so it cannot upload or enter the action queue again.
        with self.lock:
            device = self.devices[device_id]
            with device.lock:
                del self.devices[device_id]
                try:
                    self.save()
                except OSError:
                    self.devices[device_id] = device
                    raise
                device.removed = True
                device.epoch += 1
                device.active = False
                device.jpeg, device.frame_id, device.at = b"", None, 0
                device.history.clear()
        warnings = []
        # Cancel before waiting for the action lock: running input may be waiting
        # for a frame and queued work must not delay cancellation.
        cleanup = getattr(device.backend, "remove", None)
        if cleanup is None:
            try:
                device.backend.stop_capture()
            except (OSError, ValueError) as error:
                warnings.append(str(error))
            cleanup = device.backend.close
        try:
            cleanup()
        except (OSError, ValueError) as error:
            warnings.append(str(error))
        with device.action_lock:
            pass  # Running input has finished; queued actions reject the tombstone.
        result = dict(ok=True, device_id=device_id, message="Phone removed; its control stopped.")
        if warnings:
            result["warnings"] = warnings
            result["message"] += " Control cleanup reported: " + "; ".join(warnings)
        return result

    def list(self):
        with self.lock:
            return [d.summary() for d in self.devices.values()]

    def global_status(self):
        devices = self.list()
        return dict(devices_count=len(devices), live_count=sum(d['capture_active'] for d in devices))

    def close(self):
        for device in list(self.devices.values()):
            try:
                device.stop()
            except (OSError, ValueError):
                pass
            finally:
                device.backend.close()
