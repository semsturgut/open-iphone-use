"""Local HTTP capture transport and operator API; TLS supplied by SSLContext adapter."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import ssl
import sys
import threading
from urllib.parse import parse_qs, urlsplit

from .core import MAX_FRAME

WEB = Path(__file__).parent / "web"


def tls_context(cert, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, registry, context=None, watchdog=True):
        self.registry = registry
        self.capacity = threading.BoundedSemaphore(64)
        self.stopped = threading.Event()
        super().__init__(address, Handler)
        if context:
            self.socket = context.wrap_socket(self.socket, server_side=True, do_handshake_on_connect=False)
        self.watchdog = threading.Thread(target=self._watch, daemon=True)
        if watchdog: self.watchdog.start()

    def _watch(self):
        while not self.stopped.wait(.25):
            for device in list(self.registry.devices.values()):
                if device.active and not device.summary()["capture_active"]:
                    try:
                        device.stop()
                    except (OSError, ValueError):
                        pass

    def process_request(self, request, address):
        if not self.capacity.acquire(False):
            self.shutdown_request(request)
            return
        request.settimeout(10)
        try:
            super().process_request(request, address)
        except BaseException:
            self.capacity.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.capacity.release()

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if not isinstance(error, (ssl.SSLError, ConnectionError, TimeoutError)):
            print(f"HTTP handler failed: {type(error).__name__}", file=sys.stderr)

    def server_close(self):
        self.stopped.set()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenIPhoneUse"

    def log_message(self, *args):
        pass  # Never log credential-bearing capture URLs or personal frame payloads.

    def reply(self, value, status=200, mime="application/json"):
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def body(self, limit):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Chunked requests are unsupported")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= limit:
            raise ValueError("Payload too large")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError("Incomplete body")
        return data

    def admin(self):
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if not secrets.compare_digest(token, self.server.registry.admin_token):
            raise PermissionError("Operator credential required")

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def do_DELETE(self):
        self.dispatch(False, delete=True)

    def dispatch(self, post, delete=False):
        try:
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                raise PermissionError("Cross-origin request rejected")
            path = urlsplit(self.path)
            registry = self.server.registry
            if delete:
                self.admin()
                parts = path.path.strip('/').split('/')
                if len(parts) == 3 and parts[:2] == ['api', 'devices']:
                    return self.reply(registry.remove(parts[2]))
                return self.reply({'error': 'Not found'}, 404)
            if not post and path.path in ("/", "/app.js", "/style.css", "/qrcode.js"):
                file = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/qrcode.js": "qrcode.js"}[path.path]
                mime = {"/": "text/html", "/app.js": "text/javascript", "/style.css": "text/css", "/qrcode.js": "text/javascript"}[path.path]
                return self.reply((WEB / file).read_bytes(), mime=mime)
            if path.path in ('/join', '/api/enroll'):
                return self.reply({'error': 'Bluetooth-first onboarding required. Pair in AssistiveTouch, then select this phone in new or the operator UI.'}, 410)
            if path.path == "/capture" or path.path in ("/api/frame", "/api/stop", "/api/tap", "/api/calibrate", "/api/pointer"):
                token = (parse_qs(path.query).get("token", [""])[0] if path.path == "/capture"
                         else self.headers.get("X-Open-iPhone-Use-Token", ""))
                device = registry.capture_device(token)
                if path.path == "/capture" and not post:
                    return self.reply((WEB / "capture.html").read_bytes(), mime="text/html")
                if not post:
                    raise ValueError("POST required")
                body = self.body(MAX_FRAME)
                if path.path == "/api/frame":
                    return self.reply(device.upload(body))
                if path.path == "/api/stop":
                    device.stop()
                    return self.reply({"ok": True})
                if path.path == '/api/pointer':
                    data = json.loads(body)
                    return self.reply(device.backend.request('capture_pointer', epoch=data.get('epoch')))
                if path.path == "/api/tap":
                    data = json.loads(body)
                    x, y = float(data["x"]), float(data["y"])
                    if not (0 <= x <= 1 and 0 <= y <= 1):
                        raise ValueError("Tap outside screen")
                    return self.reply(device.backend.request("capture_tap", x=x, y=y))
                data = json.loads(body or b'{}')
                if data.get('force') is True:
                    return self.reply(device.action('prepare_session', {'expectedFrameID': device.frame_id}))
                # Share-start readiness is read-only; the first operator tap prepares input.
                return self.reply({"ok": False, "message": "Sharing active. Tap the screen in the operator grid to prepare and control this phone."})
            self.admin()
            if path.path == '/api/setup' and not post:
                origin = registry.capture_origin() if hasattr(registry, 'capture_origin') else getattr(registry, 'public_url', '')
                certificate_url = None
                if getattr(registry, 'certificate_port', None):
                    certificate_url = f'http://{urlsplit(origin).hostname}:{registry.certificate_port}/root-ca.cer'
                return self.reply(dict(bluetooth_required=True,capture_origin=origin,certificate_url=certificate_url,
                                       ca_fingerprint=getattr(registry, 'ca_fingerprint', None)))
            if path.path == "/api/devices":
                if post:
                    device = registry.create_bluetooth(json.loads(self.body(4096)).get("bt_id"))
                    return self.reply(dict(device_id=device.id, name=device.name, bt_id=device.bt_id, capture_path=f"/capture?token={device.token}"))
                return self.reply(registry.list(include_links=True))
            if path.path == '/api/bluetooth' and not post:
                return self.reply(registry.candidates())
            if path.path == '/api/health' and not post:
                return self.reply(dict(ok=True, instance_id=getattr(registry, 'instance_id', '')))
            if path.path == '/api/shutdown' and post:
                shutdown = getattr(registry, 'shutdown', None)
                if shutdown is None: raise ValueError('Service shutdown unavailable')
                self.reply(dict(ok=True, message='Server stopping'))
                threading.Thread(target=shutdown, daemon=True).start()
                return
            parts = path.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "devices"]:
                device = registry.devices[parts[2]]
                if not post and parts[3] == "snapshot":
                    with device.lock:
                        if not device.jpeg:
                            raise ValueError("No frame received")
                        return self.reply(dict(ok=True, device=device.summary(), frame=dict(
                            frameID=device.frame_id, ageMilliseconds=device.summary()["age_ms"],
                            captureActive=device.summary()["capture_active"],
                            base64=base64.b64encode(device.jpeg).decode(), mimeType="image/jpeg")))
                if not post and parts[3] == "frame":
                    with device.lock:
                        return self.reply(device.jpeg, mime="image/jpeg")
                if post and parts[3] == "action":
                    data = json.loads(self.body(8192))
                    return self.reply(device.action(data["operation"], data.get("arguments", {})))
                if not post and parts[3] == "status":
                    result = device.backend.request("get_device_status")
                    summary = next(d for d in registry.list() if d['device_id'] == device.id)
                    return self.reply(dict(device=summary, backend=result))
            self.reply({"error": "Not found"}, 404)
        except PermissionError as error:
            self.reply({"error": str(error)}, 401)
        except (KeyError, StopIteration):
            self.reply({"error": "Unknown device or missing field"}, 404)
        except (ValueError, TypeError, OSError) as error:
            self.reply({"error": str(error)}, 400)
