"""Public CA download only; no screen, invitation, operator or control APIs on HTTP."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading


class CertificateServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, certificate):
        self.certificate=Path(certificate).read_bytes()
        super().__init__(address, Handler)
        self.thread=threading.Thread(target=self.serve_forever,daemon=True)
        self.thread.start()
    def close(self):
        self.shutdown(); self.server_close(); self.thread.join(2)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        if self.path!='/root-ca.cer':
            self.send_error(404); return
        self.connection.settimeout(5)
        self.send_response(200)
        self.send_header('Content-Type','application/x-x509-ca-cert')
        self.send_header('Content-Length',str(len(self.server.certificate)))
        self.send_header('Content-Disposition','attachment; filename="Open-iPhone-Use-CA.cer"')
        self.end_headers(); self.wfile.write(self.server.certificate)
