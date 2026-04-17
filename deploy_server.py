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
import json
import urllib.request
import urllib.error

PROXY_PORT = int(os.environ.get("PORT", "8080"))
STREAMLIT_PORT = 8501
streamlit_ready = False
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

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


MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".json": "application/json",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".svg":  "image/svg+xml",
    ".ico":  "image/x-icon",
}


def _check_db_reachable() -> bool:
    """Perform a lightweight live check of Supabase reachability.

    Mirrors the logic in backend.py check_db_connection() but uses only
    the standard library so it can run inside the proxy process.
    Returns True when the database responds with HTTP 200 or 404 (which
    means the REST root is reachable and the API key is accepted).
    """
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = (
        os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("VITE_SUPABASE_ANON_KEY")
        or ""
    ).strip()
    if not supabase_url or not supabase_key:
        return False
    try:
        req = urllib.request.Request(
            f"{supabase_url}/rest/v1/",
            method="HEAD",
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status in (200, 404)
    except urllib.error.HTTPError as exc:
        return exc.code in (200, 404)
    except Exception:
        return False


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # Health endpoint — reads startup errors written by backend.py at import time
        path = self.path.split("?")[0]
        if path == "/api/health":
            self._health()
            return
        # Serve files from /static/ directly — bypass Streamlit to ensure correct content-type
        if path.startswith("/app/static/") or path.startswith("/static/"):
            rel = path.replace("/app/static/", "", 1).replace("/static/", "", 1)
            file_path = os.path.join(STATIC_DIR, rel)
            if os.path.isfile(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                mime = MIME_TYPES.get(ext, "application/octet-stream")
                with open(file_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

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

    def _health(self):
        """Return startup health status written by backend.py at import time.

        backend.py writes /tmp/startup_health.json with {"ok": bool, "errors": [...]}
        when it is imported by the Streamlit process (app.py → backend.py).
        If the file is absent the server is still starting; 503 is returned.
        A live db_reachable check is performed at request time so monitoring
        tools can detect connectivity regressions without restarting the app.
        """
        health_path = "/tmp/startup_health.json"
        try:
            with open(health_path) as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {"ok": False, "errors": ["Health status not yet available — server may still be starting."]}
        except Exception as e:
            data = {"ok": False, "errors": [f"Could not read health status: {e}"]}
        data["db_reachable"] = _check_db_reachable()
        status = 200 if data.get("ok") else 503
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

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
