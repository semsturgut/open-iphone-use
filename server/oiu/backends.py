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


class CaptureOnlyBackend:
    def __init__(self, platform="capture-only"):
        self.platform = platform
        self.label = "Control unavailable"

    def request(self, operation, **arguments):
        return {"ok": False, "message": "Capture-only diagnostic mode. Restart without --capture-only for native HID."}

    def ingest(self, jpeg):
        return {}

    def stop_capture(self):
        return self.request("stop_control")

    def close(self):
        pass


class MacBackend:
    """One isolated native process, pairing binding and calibration namespace per device.

    Parallel capture is supported. Multiple peripheral processes on one physical Mac
    radio are experimental and require physical verification; never broadcast to peers.
    """
    label = "macOS HID configured"

    def __init__(self, executable, directory, device_id, central=None, radio=False):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = str(self.directory / "bridge.sock")
        if len(self.path.encode()) > 100:
            raise ValueError("Native socket path too long; use a shorter --state directory")
        self.wait_for_previous_helper()
        env = dict(os.environ, OIU_HEADLESS="1", OIU_SUPPORT_DIRECTORY=str(self.directory),
                   OIU_DEVICE_NAMESPACE=device_id, OIU_DEVICE_NAME="OIU " + device_id[:8])
        env.pop("OIU_EXPECTED_CENTRAL", None)
        if central:
            env["OIU_EXPECTED_CENTRAL"] = central
        self.process = subprocess.Popen([executable] + (["--radio"] if radio else []), env=env, stdin=subprocess.DEVNULL,
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

    def wait_for_previous_helper(self):
        # A crashed parent releases the state lease before its child detects
        # reparenting. Never unlink its live socket and overlap two peripherals.
        deadline = time.monotonic() + 5
        while Path(self.path).exists():
            try:
                identity = self.request('headless_identity')
            except (ConnectionRefusedError, FileNotFoundError):
                return  # Stale socket: the broker will replace it.
            except (OSError, ValueError) as error:
                raise RuntimeError('Existing native helper could not be checked; refusing to overlap it') from error
            if not identity.get('ok') or not identity.get('message'):
                raise RuntimeError('Unexpected native helper on the existing socket')
            if time.monotonic() >= deadline:
                raise RuntimeError('Previous native helper is still running; retry after it exits')
            time.sleep(.1)

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


class MacRadio:
    """One native shared CoreBluetooth peripheral, with an explicit central on every report."""
    def __init__(self, executable, directory):
        self.bridge = MacBackend(executable, directory, 'shared-radio', radio=True)
        deadline=time.monotonic()+4
        self.startup=self.invoke('radio_status')
        while self.startup['state'] in ('starting','publishing','waitingForBluetooth','resetting','bluetoothOff') and time.monotonic()<deadline:
            time.sleep(.05)
            self.startup=self.invoke('radio_status')

    def invoke(self, operation, **arguments):
        response = self.bridge.request(operation, **arguments)
        if not response.get('ok'): raise OSError(response.get('message', 'Native radio request failed'))
        return json.loads(response['message'])

    def peers(self):
        status = self.invoke('radio_status')
        self.startup = status
        if status['state'] in ('bluetoothPermissionDenied', 'peripheralUnsupported', 'bluetoothOff', 'servicePublishFailed', 'advertisingFailed', 'bluetoothInitializationTimedOut', 'waitingForBluetoothPermission'):
            raise OSError(status.get('error') or status['state'])
        return status['peers']

    def send(self, peer, report, payload):
        self.invoke('radio_send', key=peer, dx=report, text=base64.b64encode(payload).decode())

    def release(self, peer):
        try: self.invoke('radio_release', key=peer)
        except (OSError, ValueError): pass

    def close(self):
        try:
            for peer in self.invoke('radio_status')['peers']: self.release(peer['id'])
        except (OSError,ValueError): pass
        finally: self.bridge.close()


def create_radio(state, helper=None):
    import sys
    if sys.platform == 'darwin':
        from .bootstrap import mac_helper
        return MacRadio(helper or mac_helper(), Path(state)/'radio')
    if sys.platform == 'linux':
        from .platforms.bluez import BlueZRadio
        return BlueZRadio()
    if sys.platform == 'win32':
        from .platforms.windows import WindowsRadio
        return WindowsRadio()
    raise OSError(f'Unsupported host platform: {sys.platform}')
