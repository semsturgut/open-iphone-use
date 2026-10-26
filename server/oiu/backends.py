"""Device-scoped HID interface. No network route accepts backend configuration."""
import json
import base64
import os
from pathlib import Path
import socket
import subprocess
import time
from typing import Protocol


class HIDBackend(Protocol):
    def request(self, operation: str, **arguments) -> dict: ...
    def ingest(self, jpeg: bytes) -> dict: ...
    def stop_capture(self) -> dict: ...
    def close(self) -> None: ...


class UnavailableBackend:
    def __init__(self, platform="none"):
        self.platform = platform
        self.label = "Control unavailable"

    def request(self, operation, **arguments):
        return {"ok": False, "message": f"{self.platform} HID unavailable; capture/viewing works. See docs/headless-server.md."}

    def ingest(self, jpeg):
        return {}

    def stop_capture(self):
        return self.request("stop_control")

    def close(self):
        pass


class BlueZBackend(UnavailableBackend):
    """Adapter boundary for org.bluez.GattManager1/LEAdvertisingManager1; not implemented."""
    def __init__(self):
        super().__init__("BlueZ (planned)")


class WindowsBackend(UnavailableBackend):
    """Adapter boundary for WinRT GattServiceProvider; not implemented."""
    def __init__(self):
        super().__init__("Windows GATT (planned)")


class MacBackend:
    """One isolated native process, pairing binding and calibration namespace per device.

    Parallel capture is supported. Multiple peripheral processes on one physical Mac
    radio are experimental and require physical verification; never broadcast to peers.
    """
    label = "macOS HID configured"

    def __init__(self, executable, directory, device_id, central=None):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = str(self.directory / "bridge.sock")
        if len(self.path.encode()) > 100:
            raise ValueError("Native socket path too long; use a shorter --state directory")
        env = dict(os.environ, OIU_HEADLESS="1", OIU_SUPPORT_DIRECTORY=str(self.directory),
                   OIU_DEVICE_NAMESPACE=device_id, OIU_DEVICE_NAME="OIU " + device_id[:8])
        env.pop("OIU_EXPECTED_CENTRAL", None)
        if central:
            env["OIU_EXPECTED_CENTRAL"] = central
        self.process = subprocess.Popen([executable], env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL)
        for _ in range(100):
            if self.process.poll() is not None:
                break
            if Path(self.path).exists():
                try:
                    identity = self.request("headless_identity")
                    if identity.get("message") == str(self.process.pid):
                        return
                except (OSError, ValueError):
                    pass
            time.sleep(.05)
        self.close()
        raise RuntimeError("Native headless helper did not start")

    def request(self, operation, **arguments):
        payload = dict(arguments, operation=operation, deviceID="iphone", requestID=os.urandom(16).hex())
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(90 if operation not in {"ingest_frame", "capture_stop", "stop_control", "get_device_status", "capture_tap", "headless_identity"} else 3)
            client.connect(self.path)
            client.sendall(json.dumps(payload).encode() + b"\n")
            with client.makefile("rb") as reader:
                data = reader.readline(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("Oversized native response")
            return json.loads(data)

    def ingest(self, jpeg):
        response = self.request("ingest_frame", text=base64.b64encode(jpeg).decode())
        if not response.get("ok"):
            raise ValueError(response.get("message", "Native ingest failed"))
        return json.loads(response["message"])

    def stop_capture(self):
        return self.request("capture_stop")

    def close(self):
        if self.process.poll() is None:
            try:
                self.request("stop_control")
            except (OSError, ValueError):
                pass
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
