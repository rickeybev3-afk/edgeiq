import http.server
import http.client
import socketserver
import os
import sys
import threading
import subprocess
import time


STREAMLIT_PORT = 8501
PROXY_PORT = int(os.environ.get("PORT", "8080"))


class StreamlitProxy(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def do_HEAD(self):
        self._proxy()

    def do_OPTIONS(self):
        self._proxy()

    def _proxy(self):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", STREAMLIT_PORT, timeout=30)
            body = None
            content_length = self.headers.get("Content-Length")
            if content_length:
                body = self.rfile.read(int(content_length))

            headers = {}
            for key, val in self.headers.items():
                if key.lower() not in ("host", "transfer-encoding"):
                    headers[key] = val

            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status)
            for key, val in resp.getheaders():
                if key.lower() not in ("transfer-encoding",):
                    self.send_header(key, val)
            self.end_headers()
            self.wfile.write(resp.read())
            conn.close()
        except Exception:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Streamlit not ready")

    def log_message(self, format, *args):
        pass


def start_streamlit():
    subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         "--server.port", str(STREAMLIT_PORT),
         "--server.headless", "true",
         "--server.address", "0.0.0.0"],
        cwd="/home/runner/workspace"
    )


if __name__ == "__main__":
    start_streamlit()
    time.sleep(2)
    print(f"[proxy] Proxy listening on port {PROXY_PORT}, forwarding to Streamlit on {STREAMLIT_PORT}")
    with socketserver.TCPServer(("0.0.0.0", PROXY_PORT), StreamlitProxy) as httpd:
        httpd.serve_forever()
