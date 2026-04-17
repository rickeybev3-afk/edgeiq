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
import datetime
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

PROXY_PORT = int(os.environ.get("PORT", "8080"))
STREAMLIT_PORT = 8501
streamlit_ready = False
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_TRADING_MODE_FILE = "/tmp/trading_mode.json"
_TRADING_WRITE_SECRET = os.environ.get("DASHBOARD_WRITE_SECRET", "").strip()
_USER_PREFS_FILE = ".local/user_prefs.json"
_OWNER_USER_ID = os.environ.get("OWNER_USER_ID", "").strip() or "anonymous"


def _get_trading_mode() -> str:
    """Return the current trading mode ('paper' or 'live').

    Reads from /tmp/trading_mode.json if it exists (written by this server or
    by backend.py when the Streamlit sidebar toggle is used).  Falls back to
    the IS_PAPER_ALPACA environment variable so the server's default always
    matches the configured value.
    """
    try:
        with open(_TRADING_MODE_FILE) as _f:
            return _f.read().strip() or "paper"
    except FileNotFoundError:
        return "paper" if os.environ.get("IS_PAPER_ALPACA", "true").strip().lower() == "true" else "live"
    except Exception:
        return "paper"


def _set_trading_mode(mode: str) -> None:
    """Persist a new trading mode to /tmp/trading_mode.json.

    Both deploy_server.py (React API) and backend.py (Streamlit) share this
    file so a change made from either surface is visible to both.
    """
    try:
        with open(_TRADING_MODE_FILE, "w") as _f:
            _f.write(mode)
    except Exception:
        pass


def _clear_mismatch_in_health_file() -> None:
    """Optimistically clear alpaca_mode_mismatch in the startup health file.

    Called immediately after a successful POST /api/trading-mode so the React
    dashboard's health poll clears the mismatch banner straight away — before
    the Streamlit background thread finishes its re-check.  If the new mode
    still mismatches the credentials, Streamlit will overwrite the file with
    the correct mismatch state once its check completes.
    """
    _health_path = "/tmp/startup_health.json"
    try:
        try:
            with open(_health_path) as _hf:
                _data = json.load(_hf)
        except (FileNotFoundError, ValueError):
            _data = {}
        _data["alpaca_mode_mismatch"] = False
        _data["alpaca_mismatch_message"] = ""
        with open(_health_path, "w") as _hf:
            json.dump(_data, _hf)
    except Exception:
        pass

def _load_owner_prefs() -> dict:
    """Load the owner's user preferences from local file + Supabase (same store as backend.py).

    Reads .local/user_prefs.json first, then attempts a Supabase query using the
    SUPABASE_URL / SUPABASE_KEY environment variables.  Returns an empty dict on
    any failure so callers can safely use .get() with defaults.
    """
    uid = _OWNER_USER_ID
    result: dict = {}

    # Local file (always written by backend.py save_user_prefs)
    try:
        if os.path.exists(_USER_PREFS_FILE):
            with open(_USER_PREFS_FILE) as _f:
                result = json.load(_f).get(uid, {})
    except Exception:
        pass

    # Supabase takes precedence when available (mirrors backend.py load_user_prefs)
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = (
        os.environ.get("SUPABASE_KEY") or
        os.environ.get("SUPABASE_ANON_KEY") or
        os.environ.get("VITE_SUPABASE_ANON_KEY") or
        ""
    )
    if supabase_url and supabase_key:
        try:
            import urllib.request as _ur
            req = _ur.Request(
                f"{supabase_url}/rest/v1/user_preferences?user_id=eq.{uid}&select=prefs&limit=1",
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Accept": "application/json",
                },
            )
            with _ur.urlopen(req, timeout=4) as resp:
                rows = json.loads(resp.read())
                if rows:
                    raw = rows[0].get("prefs", "{}")
                    result = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass

    return result


def _save_owner_prefs(prefs: dict) -> None:
    """Persist the owner's user preferences to local file + Supabase.

    Mirrors the write path used by backend.py save_user_prefs so the stored value
    is immediately visible to get_beta_chat_ids and all backend logic.
    """
    import datetime as _dt
    uid = _OWNER_USER_ID

    # Local file first
    try:
        all_prefs: dict = {}
        if os.path.exists(_USER_PREFS_FILE):
            with open(_USER_PREFS_FILE) as _f:
                all_prefs = json.load(_f)
        all_prefs[uid] = prefs
        os.makedirs(os.path.dirname(_USER_PREFS_FILE), exist_ok=True)
        with open(_USER_PREFS_FILE, "w") as _f:
            json.dump(all_prefs, _f)
    except Exception:
        pass

    # Supabase upsert
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = (
        os.environ.get("SUPABASE_KEY") or
        os.environ.get("SUPABASE_ANON_KEY") or
        os.environ.get("VITE_SUPABASE_ANON_KEY") or
        ""
    )
    if supabase_url and supabase_key:
        try:
            import urllib.request as _ur
            payload = json.dumps({
                "user_id": uid,
                "prefs": json.dumps(prefs),
                "updated_at": _dt.datetime.utcnow().isoformat(),
            }).encode()
            req = _ur.Request(
                f"{supabase_url}/rest/v1/user_preferences",
                data=payload,
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates",
                },
                method="POST",
            )
            _ur.urlopen(req, timeout=4)
        except Exception:
            pass


_DB_CACHE_TTL = 10
_DB_EVENTS_MAX = 50
_db_cache_lock = threading.Lock()
_db_reachable_cache: Optional[bool] = None  # None until first check completes
_db_cache_checked_at: Optional[datetime] = None
# Each entry: {"started_at": str, "ended_at": str|None, "duration_seconds": int|None}
_db_events: list = []
_db_current_outage_start: Optional[datetime] = None


def _send_db_alert(message: str) -> None:
    """Post a DB state-change alert to ALERT_WEBHOOK_URL; silently skipped if not configured."""
    webhook_url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return
    try:
        payload = json.dumps({"text": message}).encode()
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _refresh_db_cache() -> None:
    """Background thread: refresh the DB reachability cache every _DB_CACHE_TTL seconds."""
    global _db_reachable_cache, _db_cache_checked_at, _db_events, _db_current_outage_start
    while True:
        result = _check_db_reachable()
        with _db_cache_lock:
            previous = _db_reachable_cache
            now = datetime.now(timezone.utc)
            if previous is not None and result != previous:
                # Genuine transition — previous state was known, and it changed.
                ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                if result:
                    msg = f"[deploy_server] DB came back online at {ts}"
                    print(msg, flush=True)
                    _send_db_alert(f":white_check_mark: EdgeIQ DB recovery: database is back online at {ts}")
                    if _db_current_outage_start is not None:
                        duration = int((now - _db_current_outage_start).total_seconds())
                        event = {
                            "started_at": _db_current_outage_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "ended_at": ts,
                            "duration_seconds": duration,
                        }
                        _db_events.append(event)
                        if len(_db_events) > _DB_EVENTS_MAX:
                            _db_events = _db_events[-_DB_EVENTS_MAX:]
                        _db_current_outage_start = None
                else:
                    msg = f"[deploy_server] DB became unreachable at {ts}"
                    print(msg, flush=True)
                    _send_db_alert(f":red_circle: EdgeIQ DB outage: database became unreachable at {ts}")
                    _db_current_outage_start = now
            elif previous is None:
                # First check — establish baseline, log but do not alert externally.
                ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                state = "reachable" if result else "unreachable"
                print(f"[deploy_server] DB initial state at {ts}: {state}", flush=True)
                if not result:
                    _db_current_outage_start = now
            _db_reachable_cache = result
            _db_cache_checked_at = now
        time.sleep(_DB_CACHE_TTL)


def _get_db_events() -> list:
    """Return a copy of recent DB outage events (newest first)."""
    with _db_cache_lock:
        events = list(reversed(_db_events))
        if _db_current_outage_start is not None:
            now = datetime.now(timezone.utc)
            duration = int((now - _db_current_outage_start).total_seconds())
            ongoing = {
                "started_at": _db_current_outage_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ended_at": None,
                "duration_seconds": duration,
            }
            events = [ongoing] + events
        return events


def _get_db_reachable() -> bool:
    """Return the cached DB reachability result; never blocks on a network call."""
    with _db_cache_lock:
        return bool(_db_reachable_cache)


def _get_db_checked_at() -> Optional[str]:
    """Return the ISO-8601 UTC timestamp of the last completed DB check, or None."""
    with _db_cache_lock:
        if _db_cache_checked_at is None:
            return None
        return _db_cache_checked_at.isoformat()


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
        if path == "/api/trading-mode":
            self._trading_mode_get()
            return
        if path == "/api/credential-alerts":
            self._credential_alerts_get()
            return
        if path == "/api/db-events":
            self._db_events_get()
            return
        if path == "/api/backfill-health":
            self._backfill_health()
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
        path = self.path.split("?")[0]
        if path == "/api/trading-mode":
            self._trading_mode_post()
            return
        if path == "/api/credential-alerts":
            self._credential_alerts_post()
            return
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
        The db_reachable field is served from a cached value maintained by the
        _refresh_db_cache background thread (refreshed every _DB_CACHE_TTL seconds),
        so this method never blocks on a network call.
        """
        health_path = "/tmp/startup_health.json"
        try:
            with open(health_path) as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {"ok": False, "errors": ["Health status not yet available — server may still be starting."]}
        except Exception as e:
            data = {"ok": False, "errors": [f"Could not read health status: {e}"]}
        data["db_reachable"] = _get_db_reachable()
        data["db_checked_at"] = _get_db_checked_at()
        status = 200 if data.get("ok") else 503
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _backfill_health(self):
        """Return the latest backfill run stats written by backfill_context_levels.py.

        The backfill script writes /tmp/backfill_health.json with keys:
          completed_at, rows_saved, no_bars, errors
        If the file is absent the endpoint returns {"available": false}.
        """
        backfill_path = "/tmp/backfill_health.json"
        try:
            with open(backfill_path) as f:
                data = json.load(f)
            data["available"] = True
        except FileNotFoundError:
            data = {"available": False}
        except Exception as e:
            data = {"available": False, "error": str(e)}
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _trading_mode_get(self):
        """Return the current trading mode as JSON: {"mode": "paper"|"live"}."""
        body = json.dumps({"mode": _get_trading_mode()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _trading_mode_post(self):
        """Accept {"mode": "paper"|"live"} and persist the new trading mode.

        When the DASHBOARD_WRITE_SECRET environment variable is set the request
        must include a matching X-Dashboard-Secret header, otherwise the server
        returns 401 Unauthorized.  When the variable is absent the endpoint is
        unprotected (consistent with the rest of this server's API surface) but
        operators are strongly encouraged to set DASHBOARD_WRITE_SECRET in
        production to prevent unauthenticated mode changes.
        """
        if _TRADING_WRITE_SECRET:
            client_secret = self.headers.get("X-Dashboard-Secret", "")
            if client_secret != _TRADING_WRITE_SECRET:
                body = json.dumps({"error": "Unauthorized"}).encode()
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw) if raw else {}
            mode = payload.get("mode", "").strip().lower()
            if mode not in ("paper", "live"):
                body = json.dumps({"error": "mode must be 'paper' or 'live'"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            _set_trading_mode(mode)
            # Optimistically clear the mismatch banner so the React dashboard
            # reflects the new choice immediately.  The Streamlit background
            # thread will re-run the credential check and overwrite this with
            # the authoritative result once it completes.
            _clear_mismatch_in_health_file()
            body = json.dumps({"mode": mode}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

    def _credential_alerts_get(self):
        """Return credential_alerts_enabled from owner's user prefs (default True)."""
        try:
            prefs = _load_owner_prefs()
            enabled = prefs.get("credential_alerts_enabled", True)
            body = json.dumps({"enabled": bool(enabled)}).encode()
            self.send_response(200)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _db_events_get(self):
        """Return recent DB connectivity outage events as JSON."""
        body = json.dumps({"events": _get_db_events()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _credential_alerts_post(self):
        """Accept {"enabled": bool} and persist credential_alerts_enabled in owner's user prefs.

        Requires DASHBOARD_WRITE_SECRET header when the env var is set, matching
        the same authz model used by POST /api/trading-mode.
        """
        if _TRADING_WRITE_SECRET:
            client_secret = self.headers.get("X-Dashboard-Secret", "")
            if client_secret != _TRADING_WRITE_SECRET:
                body = json.dumps({"error": "Unauthorized"}).encode()
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw) if raw else {}
            if "enabled" not in payload:
                body = json.dumps({"error": "'enabled' field is required"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            enabled = bool(payload["enabled"])
            prefs = _load_owner_prefs()
            prefs["credential_alerts_enabled"] = enabled
            _save_owner_prefs(prefs)
            body = json.dumps({"enabled": enabled}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
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
    threading.Thread(target=_refresh_db_cache, daemon=True, name="db-cache-refresher").start()

    print(f"[deploy_server] Proxy listening on port {PROXY_PORT}", flush=True)
    server = ThreadedServer(("0.0.0.0", PROXY_PORT), Handler)
    server.serve_forever()
