"""Persistent identities, scoped credentials and bounded, independent action queues."""
from collections import deque
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import secrets
import threading
import time
import uuid

from .backends import CaptureOnlyBackend
from .permissions import secure_directory

MAX_FRAME = 4 * 1024 * 1024
ACTIONS = {"move_pointer", "click", "scroll", "type_text", "send_key", "pointer_tap",
           "pointer_move", "pointer_home", "ensure_session", "prepare_session", "swipe",
           "advance_feed", "set_page_board", "pointer_quick_probe"}


@dataclass
class Device:
    id: str
    name: str
    token: str
    backend: object = field(default_factory=CaptureOnlyBackend)
    jpeg: bytes = b""
    frame_id: str | None = None
    at: float = 0
    active: bool = False
    history: deque = field(default_factory=lambda: deque(maxlen=512))
    lock: object = field(default_factory=threading.RLock)
    action_lock: object = field(default_factory=threading.Lock)
    slots: object = field(default_factory=lambda: threading.BoundedSemaphore(16))
    epoch: int = 0
    removed: bool = False
    bt_id: str | None = None
    bluetooth_connected: bool = False
    bluetooth_error: str = ""
    name_source: str = "fallback"

    def require_present(self):
        if self.removed:
            raise ValueError("Device removed")

    def summary(self):
        with self.lock:
            age = int((time.monotonic() - self.at) * 1000) if self.at else None
            return dict(device_id=self.id, name=self.name, frame_id=self.frame_id,
                        age_ms=age, capture_active=self.active and age is not None and age <= 5000,
                        hid=type(self.backend).__name__, bt_id=self.bt_id,
                        bluetooth_connected=self.bluetooth_connected, bluetooth_error=self.bluetooth_error,
                        name_source=self.name_source, sharing_active=self.active and age is not None and age <= 5000,
                        control_status=getattr(self.backend, "label", "Control configured"))

    def upload(self, jpeg):
        if len(jpeg) > MAX_FRAME or not jpeg.startswith(b"\xff\xd8\xff"):
            raise ValueError("Expected JPEG up to 4 MiB")
        with self.lock:
            self.require_present()
            # Adapter ingress stays concurrent with actions waiting for fresh captures.
            controls = self.backend.ingest(jpeg)
            self.frame_id = controls.get("frameID", str(uuid.uuid4()))
            self.jpeg, self.at, self.active = jpeg, time.monotonic(), True
            self.history.append((self.frame_id, self.at))
            return {"fps": 15, "frameID": self.frame_id, **controls}

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
                        raise ValueError("Capture inactive or older than five seconds")
                    if not any(fid == expected and time.monotonic() - at <= 5 for fid, at in self.history):
                        raise ValueError("Read a fresh screenshot and pass expected_frame_id for this device")
                result = self.backend.request(operation, **arguments)
                if not result.get("ok"):
                    self.backend.request("stop_control")
                return result
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
        if self.path.exists():
            saved = json.loads(self.path.read_text())
        else:
            saved = {"admin_token": secrets.token_hex(32), "devices": []}
        self.admin_token = saved["admin_token"]
        self.invite_token = saved.get("invite_token", secrets.token_hex(32))
        self.backend_factory = None
        self.radio = None
        self.retiring = set()
        self.devices = {d["id"]: Device(**d) for d in saved["devices"]}
        self.save()

    def save(self):
        with self.lock:
            data = dict(admin_token=self.admin_token, invite_token=self.invite_token, devices=[dict(id=d.id, name=d.name, token=d.token, bt_id=d.bt_id, name_source=d.name_source)
                                                              for d in self.devices.values()])
            temporary = self.path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as file:
                os.chmod(temporary, 0o600)
                json.dump(data, file)
            os.replace(temporary, self.path)

    def create(self, name, bt_id=None, name_source="fallback"):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("Device name must have 1–80 characters")
        with self.lock:
            if len(self.devices) >= 64:
                raise ValueError("64-device limit reached")
            if bt_id and (bt_id in self.retiring or any(d.bt_id == bt_id for d in self.devices.values())):
                raise ValueError('This Bluetooth phone has already been added')
            device = Device(uuid.uuid4().hex, name.strip(), secrets.token_hex(32), bt_id=bt_id, name_source=name_source)
            if self.backend_factory: device.backend = self.backend_factory(device)
            self.devices[device.id] = device
            try: self.save()
            except OSError:
                del self.devices[device.id]
                device.backend.close()
                raise
            return device

    def bluetooth_peers(self):
        if self.radio is None:
            raise ValueError('Bluetooth HID is required. Pair your iPhone in AssistiveTouch > Devices > Bluetooth Devices first.')
        result = []
        for peer in self.radio.peers():
            if set(peer.get('reports', [])) != {1, 2}: continue
            name = ''.join(c for c in (peer.get('name') or '') if c.isprintable()).strip()[:80]
            result.append(dict(peer, name=name or f"iPhone ({peer['id'][-4:]})",
                               name_source=peer.get('name_source', 'fallback') if name else 'fallback'))
        return result

    def candidates(self):
        peers = self.bluetooth_peers()
        with self.lock:
            added = {d.bt_id: d.id for d in self.devices.values() if d.bt_id}
            retiring = set(self.retiring)
        return [dict(p, bluetooth_connected=True, available=p['id'] not in added,
                     device_id=added.get(p['id'])) for p in peers if p['id'] not in retiring]

    def create_bluetooth(self, bt_id):
        if not isinstance(bt_id, str) or not bt_id or len(bt_id) > 512:
            raise ValueError('Select a Bluetooth-connected phone using bt_id')
        peer = next((p for p in self.bluetooth_peers() if p['id'] == bt_id), None)
        if peer is None:
            raise ValueError('Bluetooth HID connection required; connect both keyboard and pointer reports before adding this phone')
        return self.create(peer['name'], bt_id, peer['name_source'])

    def capture_device(self, token):
        with self.lock:
            for device in self.devices.values():
                if secrets.compare_digest(device.token, token):
                    return device
        raise PermissionError("Invalid capture credential")

    def remove(self, device_id):
        # Persist revocation before reporting success. A stale handler reference is
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
                if device.bt_id: self.retiring.add(device.bt_id)
                device.removed = True
                device.token = ""
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
        with self.lock:
            self.retiring.discard(device.bt_id)
        result = dict(ok=True, device_id=device_id, message="Phone removed; its sharing link is revoked.")
        if warnings:
            result["warnings"] = warnings
            result["message"] += " Control cleanup reported: " + "; ".join(warnings)
        return result

    def list(self, include_links=False):
        error = ''
        try: peers = {p['id']: p for p in self.bluetooth_peers()}
        except (OSError, ValueError) as failure:
            peers, error = {}, str(failure)
        with self.lock:
            changed = False
            result = []
            for d in self.devices.values():
                peer = peers.get(d.bt_id)
                d.bluetooth_connected = peer is not None
                d.bluetooth_error = error or ('' if peer else 'Bluetooth HID connection required')
                if peer and (d.name, d.name_source) != (peer['name'], peer['name_source']):
                    d.name, d.name_source = peer['name'], peer['name_source']; changed = True
                item = d.summary()
                if include_links: item['capture_path'] = f'/capture?token={d.token}'
                result.append(item)
            if changed: self.save()
            return result

    def close(self):
        for device in list(self.devices.values()):
            try:
                device.stop()
            except (OSError, ValueError):
                pass
            finally:
                device.backend.close()
