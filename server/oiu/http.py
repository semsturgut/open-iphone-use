"""Loopback-only Web UI and API: device list, live MJPEG, snapshots and actions."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ipaddress
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit

from . import vision

WEB = Path(__file__).parent / "web"
STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}


class OperatorDenied(PermissionError):
    pass


def candidates(registry):
    from .coredevice import candidates
    added = {d.udid: d.id for d in registry.devices.values()}
    return [dict(c, available=c['udid'] not in added, device_id=added.get(c['udid'])) for c in candidates()]


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, registry, watchdog=True):
        if not ipaddress.ip_address(address[0]).is_loopback:
            raise ValueError('The Web UI binds to a loopback address only')
        self.registry = registry
        self.capacity = threading.BoundedSemaphore(64)
        self.stopped = threading.Event()
        super().__init__(address, Handler)
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
        if not isinstance(error, (ConnectionError, TimeoutError)):
            print(f"HTTP handler failed: {type(error).__name__}", file=sys.stderr)

    def server_close(self):
        self.stopped.set()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenIPhoneUse"

    def log_message(self, *args):
        pass  # Never log personal frame payloads.

    def reply(self, value, status=200, mime="application/json"):
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def stream(self, device, fps=30):
        """Live MJPEG: browsers render multipart JPEG in an <img>."""
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.connection.settimeout(None)
        last = None
        try:
            while not device.removed and not self.server.stopped.is_set():
                with device.lock:
                    frame_id, jpeg = device.frame_id, device.jpeg
                if frame_id and frame_id != last:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpeg) + jpeg + b"\r\n")
                    self.wfile.flush()
                    last = frame_id
                time.sleep(1 / fps)
        except (ConnectionError, TimeoutError):
            pass  # The viewer closed the page.

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
        # Network boundary, not a bearer credential: never authorize forwarded headers,
        # DNS-rebound Host names or cross-site browser actions.
        if not ipaddress.ip_address(self.client_address[0]).is_loopback:
            raise OperatorDenied('The Web UI is available only on this computer')
        host = urlsplit('http://' + self.headers.get('Host', ''))
        if host.hostname not in ('localhost', '127.0.0.1', '::1') or host.port != self.server.server_port:
            raise OperatorDenied('Loopback Host required')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise OperatorDenied('Cross-site request rejected')
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            raise OperatorDenied("Cross-origin request rejected")

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def do_DELETE(self):
        self.dispatch(False, delete=True)

    def dispatch(self, post, delete=False):
        try:
            self.admin()
            path = urlsplit(self.path).path
            registry = self.server.registry
            parts = path.strip('/').split('/')
            if delete:
                if len(parts) == 3 and parts[:2] == ['api', 'devices']:
                    return self.reply(registry.remove(parts[2]))
                return self.reply({'error': 'Not found'}, 404)
            if not post and path in STATIC:
                file, mime = STATIC[path]
                return self.reply((WEB / file).read_bytes(), mime=mime)
            if path == "/api/devices":
                if post:
                    udid = json.loads(self.body(4096)).get("udid")
                    name = next((c.get('name') for c in candidates(registry) if c['udid'] == udid), None)
                    device = registry.create(udid, name)
                    return self.reply(device.summary())
                return self.reply(registry.list())
            if path == '/api/candidates' and not post:
                return self.reply(candidates(registry))
            if path == '/api/health' and not post:
                return self.reply(dict(ok=True, instance_id=getattr(registry, 'instance_id', ''), **registry.global_status()))
            if path == '/api/status' and not post:
                return self.reply(registry.global_status())
            if path == '/api/shutdown' and post:
                shutdown = getattr(registry, 'shutdown', None)
                if shutdown is None: raise ValueError('Service shutdown unavailable')
                self.reply(dict(ok=True, message='Server stopping'))
                threading.Thread(target=shutdown, daemon=True).start()
                return
            if len(parts) == 4 and parts[:2] == ["api", "devices"]:
                device = registry.devices[parts[2]]
                if not post and parts[3] == "snapshot":
                    with device.lock:
                        if not device.jpeg:
                            raise ValueError("No frame received")
                        summary = device.summary()
                        width, height = vision.dimensions(device.jpeg)
                        return self.reply(dict(ok=True, device=summary, frame=dict(
                            frameID=device.frame_id, ageMilliseconds=summary["age_ms"], captureActive=summary["capture_active"],
                            width=width, height=height, base64=base64.b64encode(device.jpeg).decode(), mimeType="image/jpeg")))
                if not post and parts[3] == "stream":
                    return self.stream(device)
                if not post and parts[3] == "frame":
                    with device.lock:
                        return self.reply(device.jpeg, mime="image/jpeg")
                if post and parts[3] == "action":
                    data = json.loads(self.body(8192))
                    return self.reply(device.action(data["operation"], data.get("arguments", {})))
                if not post and parts[3] == "status":
                    return self.reply(dict(device=device.summary(), backend=device.backend.request("get_device_status")))
            self.reply({"error": "Not found"}, 404)
        except OperatorDenied as error:
            self.reply({"error": str(error)}, 403)
        except (KeyError, StopIteration):
            self.reply({"error": "Unknown device or missing field"}, 404)
        except (ValueError, TypeError, OSError) as error:
            self.reply({"error": str(error)}, 400)
