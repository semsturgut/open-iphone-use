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

    def summary(self):
        with self.lock:
            age = int((time.monotonic() - self.at) * 1000) if self.at else None
            return dict(device_id=self.id, name=self.name, frame_id=self.frame_id,
                        age_ms=age, capture_active=self.active and age is not None and age <= 5000,
                        hid=type(self.backend).__name__,
                        control_status=getattr(self.backend, "label", "Control configured"))

    def upload(self, jpeg):
        if len(jpeg) > MAX_FRAME or not jpeg.startswith(b"\xff\xd8\xff"):
            raise ValueError("Expected JPEG up to 4 MiB")
        with self.lock:
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
        self.devices = {d["id"]: Device(**d) for d in saved["devices"]}
        self.save()

    def save(self):
        with self.lock:
            data = dict(admin_token=self.admin_token, invite_token=self.invite_token, devices=[dict(id=d.id, name=d.name, token=d.token)
                                                              for d in self.devices.values()])
            temporary = self.path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as file:
                json.dump(data, file)
            os.replace(temporary, self.path)

    def create(self, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("Device name must have 1–80 characters")
        with self.lock:
            if len(self.devices) >= 64:
                raise ValueError("64-device limit reached")
            device = Device(uuid.uuid4().hex, name.strip(), secrets.token_hex(32))
            if self.backend_factory: device.backend = self.backend_factory(device)
            self.devices[device.id] = device
            self.save()
            return device

    def capture_device(self, token):
        with self.lock:
            for device in self.devices.values():
                if secrets.compare_digest(device.token, token):
                    return device
        raise PermissionError("Invalid capture credential")

    def list(self):
        with self.lock:
            return [d.summary() for d in self.devices.values()]

    def close(self):
        for device in list(self.devices.values()):
            try:
                device.stop()
            except (OSError, ValueError):
                pass
            finally:
                device.backend.close()
