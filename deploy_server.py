import http.server
import http.client
import socketserver
import os
import sys
import subprocess
import threading
import time

PROXY_PORT = int(os.environ.get("PORT", "8080"))
STREAMLIT_PORT = 8501
streamlit_ready = False


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if not streamlit_ready:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>EdgeIQ is starting...</h2><script>setTimeout(()=>location.reload(),3000)</script></body></html>")
            return
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def do_OPTIONS(self):
        self._proxy()

    def _proxy(self):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", STREAMLIT_PORT, timeout=30)
            body = None
            cl = self.headers.get("Content-Length")
            if cl:
                body = self.rfile.read(int(cl))
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "transfer-encoding")}
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ("transfer-encoding",):
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp.read())
            conn.close()
        except Exception:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>EdgeIQ is starting...</h2><script>setTimeout(()=>location.reload(),3000)</script></body></html>")

    def log_message(self, fmt, *args):
        pass


def start_streamlit():
    global streamlit_ready
    subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         "--server.port", str(STREAMLIT_PORT),
         "--server.headless", "true",
         "--server.address", "127.0.0.1"],
        cwd="/home/runner/workspace"
    )
    for _ in range(120):
        try:
            c = http.client.HTTPConnection("127.0.0.1", STREAMLIT_PORT, timeout=2)
            c.request("GET", "/_stcore/health")
            r = c.getresponse()
            if r.status == 200:
                streamlit_ready = True
                print(f"[deploy_server] Streamlit ready on {STREAMLIT_PORT}", flush=True)
                return
            c.close()
        except Exception:
            pass
        time.sleep(1)
    print("[deploy_server] WARNING: Streamlit never became ready", flush=True)


if __name__ == "__main__":
    subprocess.Popen(["python3", "paper_trader_bot.py"], cwd="/home/runner/workspace",
                     stdout=open("/tmp/paper_trader_bot.log", "a"), stderr=subprocess.STDOUT)
    subprocess.Popen(["python3", "kalshi_bot.py"], cwd="/home/runner/workspace",
                     stdout=open("/tmp/kalshi_bot.log", "a"), stderr=subprocess.STDOUT)

    threading.Thread(target=start_streamlit, daemon=True).start()

    print(f"[deploy_server] Proxy listening on port {PROXY_PORT}", flush=True)
    with socketserver.TCPServer(("0.0.0.0", PROXY_PORT), Handler) as httpd:
        httpd.serve_forever()
