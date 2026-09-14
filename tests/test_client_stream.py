"""The client streams large bodies in chunks and reports progress; the server sees every byte."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from dbe.client import EnclaveClient


class Echo(BaseHTTPRequestHandler):
    def do_PUT(self):
        n = int(self.headers["Content-Length"])
        body = self.rfile.read(n)
        payload = json.dumps({"received": len(body), "first": body[:4].decode(), "last": body[-4:].decode()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep test output quiet
        pass


def test_streaming_upload_reports_progress_and_delivers_every_byte():
    server = HTTPServer(("127.0.0.1", 0), Echo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = b"HEAD" + b"x" * (3 * 1024 * 1024) + b"TAIL"
        seen = []
        client = EnclaveClient(dev_url=f"http://127.0.0.1:{server.server_port}")
        status, payload = client.request("PUT", "/api/model/adapter", body, signed=False, progress=lambda s, t: seen.append((s, t)))
        assert status == 200 and payload == {"received": len(body), "first": "HEAD", "last": "TAIL"}
        assert seen[0] == (0, len(body)) and seen[-1] == (len(body), len(body))
        assert all(a <= b for (a, _), (b, _) in zip(seen, seen[1:]))
        assert len(seen) > 5
    finally:
        server.shutdown()
