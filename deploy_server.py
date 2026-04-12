import http.server
import http.client
import socketserver
import socket
import os
import sys
import subprocess
import threading
import time
import select
import struct
import hashlib
import base64

PROXY_PORT = int(os.environ.get("PORT", "8080"))
STREAMLIT_PORT = 8501
streamlit_ready = False

LOADING_PAGE = b"""<html><head><title>EdgeIQ</title></head>
<body style="background:#0e1117;color:#fafafa;display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif">
<div style="text-align:center"><h2>EdgeIQ is starting...</h2><p style="color:#888">Please wait a moment</p>
<script>setTimeout(()=>location.reload(),3000)</script></div></body></html>"""


def pipe_sockets(s1, s2):
    """Bidirectionally pipe data between two sockets until one closes."""
    try:
        while True:
            readable, _, _ = select.select([s1, s2], [], [], 30)
            if not readable:
                break
            for s in readable:
                data = s.recv(65536)
                if not data:
                    return
                target = s2 if s is s1 else s1
                target.sendall(data)
    except Exception:
        pass
    finally:
        try:
            s1.close()
        except Exception:
            pass
        try:
            s2.close()
        except Exception:
            pass


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ws_key = self.headers.get("Sec-WebSocket-Key")
        if ws_key and self.headers.get("Upgrade", "").lower() == "websocket":
            self._handle_websocket()
            return
        if not streamlit_ready:
            self._loading()
            return
        self._proxy()

    def do_POST(self):
        self._proxy() if streamlit_ready else self._loading()

    def do_PUT(self):
        self._proxy() if streamlit_ready else self._loading()

    def do_DELETE(self):
        self._proxy() if streamlit_ready else self._loading()

    def do_OPTIONS(self):
        self._proxy() if streamlit_ready else self._loading()

    def _loading(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(LOADING_PAGE)))
        self.end_headers()
        self.wfile.write(LOADING_PAGE)

    def _handle_websocket(self):
        """Proxy WebSocket upgrade and bidirectional frames to Streamlit."""
        if not streamlit_ready:
            self.send_response(503)
            self.end_headers()
            return
        try:
            upstream = socket.create_connection(("127.0.0.1", STREAMLIT_PORT), timeout=10)
            raw_headers = ""
            for key, val in self.headers.items():
                if key.lower() != "host":
                    raw_headers += f"{key}: {val}\r\n"
            raw_headers += f"Host: 127.0.0.1:{STREAMLIT_PORT}\r\n"
            request_line = f"{self.command} {self.path} HTTP/1.1\r\n"
            upstream.sendall((request_line + raw_headers + "\r\n").encode())

            response_data = b""
            while b"\r\n\r\n" not in response_data:
                chunk = upstream.recv(4096)
                if not chunk:
                    self.send_response(502)
                    self.end_headers()
                    upstream.close()
                    return
                response_data += chunk

            header_end = response_data.index(b"\r\n\r\n") + 4
            self.wfile.write(response_data[:header_end])
            self.wfile.flush()

            if len(response_data) > header_end:
                upstream_extra = response_data[header_end:]
                self.wfile.write(upstream_extra)
                self.wfile.flush()

            client_sock = self.request
            pipe_sockets(client_sock, upstream)
        except Exception:
            try:
                self.send_response(502)
                self.end_headers()
            except Exception:
                pass

    def _proxy(self):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", STREAMLIT_PORT, timeout=30)
            body = None
            cl = self.headers.get("Content-Length")
            if cl:
                body = self.rfile.read(int(cl))
            headers = {}
            for k, v in self.headers.items():
                if k.lower() not in ("host", "transfer-encoding"):
                    headers[k] = v
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
            self._loading()

    def log_message(self, fmt, *args):
        pass


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_streamlit():
    global streamlit_ready
    subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         "--server.port", str(STREAMLIT_PORT),
         "--server.headless", "true",
         "--server.address", "127.0.0.1",
         "--server.enableCORS", "false",
         "--server.enableXsrfProtection", "false"],
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
    print("[deploy_server] WARNING: Streamlit did not become ready", flush=True)


if __name__ == "__main__":
    subprocess.Popen(["python3", "paper_trader_bot.py"], cwd="/home/runner/workspace",
                     stdout=open("/tmp/paper_trader_bot.log", "a"), stderr=subprocess.STDOUT)
    subprocess.Popen(["python3", "kalshi_bot.py"], cwd="/home/runner/workspace",
                     stdout=open("/tmp/kalshi_bot.log", "a"), stderr=subprocess.STDOUT)

    threading.Thread(target=start_streamlit, daemon=True).start()

    print(f"[deploy_server] Proxy listening on port {PROXY_PORT}", flush=True)
    server = ThreadedServer(("0.0.0.0", PROXY_PORT), Handler)
    server.serve_forever()
