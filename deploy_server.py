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
import logging
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
_DEFAULT_PAPER_LOOKBACK_DAYS = int(os.environ.get("PAPER_CLOSE_LOOKBACK_DAYS", "60"))
_DEFAULT_BACKTEST_LOOKBACK_DAYS = int(os.environ.get("BACKTEST_CLOSE_LOOKBACK_DAYS", "60"))
_DEFAULT_MIN_TCS = int(os.environ.get("PAPER_TRADE_MIN_TCS", "50"))
_ADAPTIVE_EXITS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adaptive_exits.json")
_RVOL_SIZE_TIERS_DEFAULT = [
    {"rvol_min": 3.5, "multiplier": 1.5},
    {"rvol_min": 2.5, "multiplier": 1.25},
]
try:
    _DEFAULT_HEARTBEAT_HOURS = float(os.environ.get("BACKFILL_HEARTBEAT_HOURS", "25"))
except (ValueError, TypeError):
    _DEFAULT_HEARTBEAT_HOURS = 25.0


def _get_trading_mode() -> str:
    """Return the current trading mode ('paper' or 'live').

    Priority order:
    1. Supabase-backed user_preferences (persists across restarts)
    2. /tmp/trading_mode.json (written by this server or backend.py; volatile)
    3. IS_PAPER_ALPACA environment variable (configured default)
    """
    try:
        prefs = _load_owner_prefs()
        if "trading_mode" in prefs:
            return prefs["trading_mode"]
    except Exception:
        pass
    try:
        with open(_TRADING_MODE_FILE) as _f:
            val = _f.read().strip()
            if val:
                return val
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return "paper" if os.environ.get("IS_PAPER_ALPACA", "true").strip().lower() == "true" else "live"


def _set_trading_mode(mode: str) -> None:
    """Persist a new trading mode to Supabase user_preferences and /tmp/trading_mode.json.

    Supabase is the durable store so the setting survives server restarts.
    The /tmp file is still written for backend.py (Streamlit) compatibility —
    both surfaces share it so a change from either is immediately visible to the
    other without waiting for a Supabase round-trip.
    """
    try:
        prefs = _load_owner_prefs()
        prefs["trading_mode"] = mode
        _save_owner_prefs(prefs)
    except Exception:
        pass
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


def _load_all_subscriber_prefs() -> list:
    """Return a list of all subscribers with their credential_alerts_enabled status.

    Queries user_preferences from Supabase (all rows) or local file fallback.
    Each entry:  {"user_id": str, "credential_alerts_enabled": bool, "tg_name": str}
    """
    result = []

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
                f"{supabase_url}/rest/v1/user_preferences?select=user_id,prefs",
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Accept": "application/json",
                },
            )
            with _ur.urlopen(req, timeout=4) as resp:
                rows = json.loads(resp.read())
                for row in rows:
                    uid = row.get("user_id", "")
                    raw = row.get("prefs", "{}")
                    prefs = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    result.append({
                        "user_id": uid,
                        "credential_alerts_enabled": prefs.get("credential_alerts_enabled", True) is not False,
                        "tg_name": prefs.get("tg_name", ""),
                    })
            return result
        except Exception:
            pass

    # Local file fallback
    try:
        if os.path.exists(_USER_PREFS_FILE):
            with open(_USER_PREFS_FILE) as _f:
                all_prefs = json.load(_f)
            for uid, prefs in all_prefs.items():
                if not isinstance(prefs, dict):
                    continue
                result.append({
                    "user_id": uid,
                    "credential_alerts_enabled": prefs.get("credential_alerts_enabled", True) is not False,
                    "tg_name": prefs.get("tg_name", ""),
                })
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


def _save_subscriber_prefs(user_id: str, prefs: dict) -> bool:
    """Persist a single subscriber's user preferences to local file + Supabase.

    Mirrors the write path used by backend.py save_user_prefs so the stored
    value is immediately visible to all backend logic.

    Returns True when the write succeeded on every configured store.
    Returns False when Supabase is configured but the upsert failed — the local
    file is still updated in that case, but the backend (which reads Supabase
    first) may continue using stale prefs until Supabase is reachable again.
    """
    import datetime as _dt
    uid = user_id
    local_ok = False

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
        local_ok = True
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
            return True
        except Exception as _e:
            logging.warning("_save_subscriber_prefs: Supabase upsert failed for user_id=%s: %s", uid, _e)
            return False

    return local_ok


def _load_subscriber_prefs(user_id: str) -> dict:
    """Load a single subscriber's prefs from local file + Supabase."""
    uid = user_id
    result: dict = {}

    try:
        if os.path.exists(_USER_PREFS_FILE):
            with open(_USER_PREFS_FILE) as _f:
                result = json.load(_f).get(uid, {})
    except Exception:
        pass

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
            import urllib.parse as _up
            req = _ur.Request(
                f"{supabase_url}/rest/v1/user_preferences?user_id=eq.{_up.quote(uid)}&select=prefs&limit=1",
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
                    result = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            pass

    return result


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
        if path == "/api/subscribers/credential-alerts":
            self._subscribers_credential_alerts_get()
            return
        if path == "/api/backfill-error-alerts":
            self._backfill_error_alerts_get()
            return
        if path == "/api/recalc-zero-alerts":
            self._recalc_zero_alerts_get()
            return
        if path == "/api/db-events":
            self._db_events_get()
            return
        if path == "/api/backfill-health":
            self._backfill_health()
            return
        if path == "/api/eod-recalc-health":
            self._eod_recalc_health()
            return
        if path == "/api/paper-lookback":
            self._paper_lookback_get()
            return
        if path == "/api/eod-sweep":
            self._eod_sweep()
            return
        if path == "/api/backfill-heartbeat-window":
            self._backfill_heartbeat_window_get()
            return
        if path == "/api/backtest-lookback":
            self._backtest_lookback_get()
            return
        if path == "/api/paper-trade-min-tcs":
            self._paper_trade_min_tcs_get()
            return
        if path == "/api/rvol-size-tiers":
            self._rvol_size_tiers_get()
            return
        if path == "/api/config":
            self._config_get()
            return
        if path == "/api/gap-down-calibration":
            self._gap_down_calibration()
            return
        if path == "/api/screener-calibration":
            self._screener_calibration()
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
        if path == "/api/backfill-error-alerts":
            self._backfill_error_alerts_post()
            return
        if path == "/api/recalc-zero-alerts":
            self._recalc_zero_alerts_post()
            return
        if path == "/api/subscribers/credential-alerts":
            self._subscribers_credential_alerts_post()
            return
        if path == "/api/paper-lookback":
            self._paper_lookback_post()
            return
        if path == "/api/backfill-heartbeat-window":
            self._backfill_heartbeat_window_post()
            return
        if path == "/api/backtest-lookback":
            self._backtest_lookback_post()
            return
        if path == "/api/paper-trade-min-tcs":
            self._paper_trade_min_tcs_post()
            return
        if path == "/api/rvol-size-tiers":
            self._rvol_size_tiers_post()
            return
        if path == "/api/backfill-dryrun":
            self._backfill_dryrun_post()
            return
        if path == "/api/context-dryrun":
            self._context_dryrun_post()
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
        """Return backfill run stats written by backfill_context_levels.py.

        The backfill script appends each run to the path set by the
        BACKFILL_HISTORY_PATH env variable (default: backfill_history.json
        next to this file).  The response includes the latest entry's fields
        at the top level for backward compatibility, plus a 'history' array
        (newest first) and 'available'.
        If the file is absent the endpoint returns {"available": false}.
        """
        _default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backfill_history.json')
        history_path = os.environ.get('BACKFILL_HISTORY_PATH', _default_path)
        _legacy_path = '/tmp/backfill_history.json'
        if not os.path.exists(history_path) and os.path.exists(_legacy_path):
            try:
                import shutil as _shutil
                _shutil.copy2(_legacy_path, history_path)
            except Exception as _me:
                logging.warning(f'Could not migrate backfill history from {_legacy_path} to {history_path}: {_me}')
        try:
            heartbeat_hours = float(os.environ.get("BACKFILL_HEARTBEAT_HOURS", "25"))
        except (ValueError, TypeError):
            heartbeat_hours = 25.0
        try:
            _prefs = _load_owner_prefs()
            if "backfill_heartbeat_hours" in _prefs:
                heartbeat_hours = float(_prefs["backfill_heartbeat_hours"])
        except Exception:
            pass
        try:
            with open(history_path) as f:
                history = json.load(f)
            if not isinstance(history, list) or len(history) == 0:
                data = {"available": False, "heartbeat_hours": heartbeat_hours, "is_overdue": True, "history_path": history_path}
            else:
                latest = history[-1]
                completed_at_str = latest.get("completed_at")
                is_overdue = True
                if completed_at_str:
                    try:
                        import datetime as _dt
                        completed_at = _dt.datetime.fromisoformat(completed_at_str.replace("Z", "+00:00"))
                        if completed_at.tzinfo is None:
                            completed_at = completed_at.replace(tzinfo=_dt.timezone.utc)
                        age_hours = (_dt.datetime.now(_dt.timezone.utc) - completed_at).total_seconds() / 3600.0
                        is_overdue = age_hours > heartbeat_hours
                    except Exception:
                        is_overdue = True
                # Build per-script summary (latest entry per script name).
                # Only recognised script names get their own row; anything
                # missing, empty, or unrecognised is collapsed into "other".
                _KNOWN_SCRIPTS = {
                    "backfill_context_levels",
                    "backfill_close_prices",
                    "backfill_ib_vwap",
                }
                _script_latest: dict = {}
                _script_history: dict = {}
                for _entry in history:
                    _raw = _entry.get("script") or ""
                    _sname = _raw if _raw in _KNOWN_SCRIPTS else "other"
                    _script_latest[_sname] = _entry  # last write wins (history is oldest→newest)
                    _script_history.setdefault(_sname, []).append(_entry)

                import datetime as _dt

                def _compute_overdue(completed_at_str, hb_hours):
                    if not completed_at_str:
                        return True
                    try:
                        _dt2 = _dt.datetime.fromisoformat(completed_at_str.replace("Z", "+00:00"))
                        if _dt2.tzinfo is None:
                            _dt2 = _dt2.replace(tzinfo=_dt.timezone.utc)
                        _age_h = (_dt.datetime.now(_dt.timezone.utc) - _dt2).total_seconds() / 3600.0
                        return _age_h > hb_hours
                    except Exception:
                        return True

                _scripts_summary: dict = {}
                for _sname, _entry in _script_latest.items():
                    _cat = _entry.get("completed_at")
                    _overdue = _compute_overdue(_cat, heartbeat_hours)
                    # Build per-script history (newest first, up to 10 entries).
                    # is_overdue semantics mirror the main history table:
                    #   index 0 (latest run)  → age-from-now > heartbeat
                    #   index i>0 (older run) → gap to next newer run > heartbeat
                    _hist_entries = list(reversed(_script_history.get(_sname, [])))[:10]
                    _hist_rows = []
                    for _hidx, _he in enumerate(_hist_entries):
                        _he_cat = _he.get("completed_at")
                        if _hidx == 0:
                            _he_overdue = _compute_overdue(_he_cat, heartbeat_hours)
                        else:
                            _newer_cat = _hist_entries[_hidx - 1].get("completed_at")
                            if _he_cat and _newer_cat:
                                try:
                                    _t_old = _dt.datetime.fromisoformat(_he_cat.replace("Z", "+00:00"))
                                    _t_new = _dt.datetime.fromisoformat(_newer_cat.replace("Z", "+00:00"))
                                    if _t_old.tzinfo is None:
                                        _t_old = _t_old.replace(tzinfo=_dt.timezone.utc)
                                    if _t_new.tzinfo is None:
                                        _t_new = _t_new.replace(tzinfo=_dt.timezone.utc)
                                    _gap_h = (_t_new - _t_old).total_seconds() / 3600.0
                                    _he_overdue = _gap_h > heartbeat_hours
                                except Exception:
                                    _he_overdue = True
                            else:
                                _he_overdue = True
                        _hist_rows.append({
                            "completed_at": _he_cat,
                            "rows_saved": _he.get("rows_saved"),
                            "no_bars": _he.get("no_bars"),
                            "errors": _he.get("errors"),
                            "is_overdue": _he_overdue,
                        })
                    _scripts_summary[_sname] = {
                        "completed_at": _cat,
                        "rows_saved": _entry.get("rows_saved"),
                        "no_bars": _entry.get("no_bars"),
                        "errors": _entry.get("errors"),
                        "is_overdue": _overdue,
                        "history": _hist_rows,
                    }

                data = {
                    "available": True,
                    "completed_at": completed_at_str,
                    "rows_saved": latest.get("rows_saved"),
                    "no_bars": latest.get("no_bars"),
                    "errors": latest.get("errors"),
                    "heartbeat_hours": heartbeat_hours,
                    "is_overdue": is_overdue,
                    "history": list(reversed(history)),
                    "history_path": history_path,
                    "scripts": _scripts_summary,
                }
        except FileNotFoundError:
            data = {"available": False, "heartbeat_hours": heartbeat_hours, "is_overdue": True, "history_path": history_path}
        except json.JSONDecodeError:
            data = {"available": False, "error": "history file corrupt", "heartbeat_hours": heartbeat_hours, "is_overdue": True, "history_path": history_path}
        except Exception as e:
            data = {"available": False, "error": str(e), "heartbeat_hours": heartbeat_hours, "is_overdue": True, "history_path": history_path}
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _eod_recalc_health(self):
        """Return EOD P&L recalc run history written by paper_trader_bot.py.

        Reads the JSON file whose path is set by EOD_RECALC_HISTORY_PATH
        (default: eod_recalc_history.json next to this file).  The response
        includes the latest entry's fields at the top level, a 'history' array
        (newest first), and 'available'.
        If the file is absent the endpoint returns {"available": false}.
        """
        _default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eod_recalc_history.json")
        history_path = os.environ.get("EOD_RECALC_HISTORY_PATH", _default_path)
        try:
            with open(history_path) as f:
                history = json.load(f)
            if not isinstance(history, list) or len(history) == 0:
                data = {"available": False}
            else:
                latest = history[-1]
                data = {
                    "available": True,
                    "completed_at": latest.get("completed_at"),
                    "path": latest.get("path"),
                    "written": latest.get("written"),
                    "skipped": latest.get("skipped"),
                    "elapsed_s": latest.get("elapsed_s"),
                    "history": list(reversed(history)),
                }
        except FileNotFoundError:
            data = {"available": False}
        except json.JSONDecodeError:
            data = {"available": False, "error": "history file corrupt"}
        except Exception as e:
            data = {"available": False, "error": str(e)}
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _eod_sweep(self):
        """Return the most recent EOD P&L sweep result written by paper_trader_bot.py.

        paper_trader_bot.py writes eod_sweep_status.json after each
        nightly_recalibration() run with the fields:
          ran_at         — ISO-8601 UTC timestamp of the run
          paper_healed   — number of paper-trade rows updated
          backtest_healed — number of backtest rows updated
          total_healed   — combined total

        The path is set by EOD_SWEEP_STATUS_PATH env variable (default:
        eod_sweep_status.json next to this file).  A legacy /tmp copy is
        migrated automatically on first read if the primary path is absent.

        It also appends each run to eod_sweep_history.json (next to this file).
        The response includes a `history` array (newest first, up to 30 entries).
        If the file is absent the endpoint returns {"available": false}.
        """
        _default_sweep_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'eod_sweep_status.json')
        sweep_path = os.environ.get('EOD_SWEEP_STATUS_PATH', _default_sweep_path)
        _legacy_sweep_path = '/tmp/eod_sweep_status.json'
        if not os.path.exists(sweep_path) and os.path.exists(_legacy_sweep_path):
            try:
                import shutil as _shutil
                _shutil.copy2(_legacy_sweep_path, sweep_path)
            except Exception as _me:
                logging.warning(f'Could not migrate EOD sweep status from {_legacy_sweep_path} to {sweep_path}: {_me}')
        try:
            with open(sweep_path) as f:
                payload = json.load(f)
            data = {"available": True, **payload}
        except FileNotFoundError:
            data = {"available": False}
        except json.JSONDecodeError:
            data = {"available": False, "error": "status file corrupt"}
        except Exception as e:
            data = {"available": False, "error": str(e)}

        # Attach history (newest first, cap at 30 entries)
        history_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eod_sweep_history.json")
        try:
            with open(history_path) as hf:
                raw_history = json.load(hf)
            if isinstance(raw_history, list):
                data["history"] = list(reversed(raw_history))[:30]
            else:
                data["history"] = []
        except FileNotFoundError:
            data["history"] = []
        except Exception:
            data["history"] = []

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

    def _subscribers_credential_alerts_get(self):
        """Return all subscribers with their credential_alerts_enabled opt-out status.

        Queries all rows in user_preferences (Supabase first, local file fallback)
        and returns a list of objects:
          [{"user_id": str, "credential_alerts_enabled": bool}, ...]
        """
        try:
            subscribers = _load_all_subscriber_prefs()
            body = json.dumps({"subscribers": subscribers}).encode()
            self.send_response(200)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _subscribers_credential_alerts_post(self):
        """Accept {"user_id": str, "enabled": bool} and update that subscriber's credential_alerts_enabled pref.

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
            if "user_id" not in payload or "enabled" not in payload:
                body = json.dumps({"error": "'user_id' and 'enabled' fields are required"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            user_id = str(payload["user_id"])
            if not isinstance(payload["enabled"], bool):
                body = json.dumps({"error": "'enabled' must be a JSON boolean"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            enabled = payload["enabled"]
            prefs = _load_subscriber_prefs(user_id)
            prefs["credential_alerts_enabled"] = enabled
            saved = _save_subscriber_prefs(user_id, prefs)
            if saved:
                body = json.dumps({"user_id": user_id, "enabled": enabled}).encode()
                self.send_response(200)
            else:
                body = json.dumps({
                    "error": "Preference saved to local file but Supabase write failed — backend may read stale value until Supabase recovers",
                    "user_id": user_id,
                    "enabled": enabled,
                }).encode()
                self.send_response(502)
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

    def _backfill_error_alerts_get(self):
        """Return backfill_error_alerts_enabled from owner's user prefs (default True)."""
        try:
            prefs = _load_owner_prefs()
            enabled = prefs.get("backfill_error_alerts_enabled", True)
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

    def _backfill_error_alerts_post(self):
        """Accept {"enabled": bool} and persist backfill_error_alerts_enabled in owner's user prefs."""
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
            prefs["backfill_error_alerts_enabled"] = enabled
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

    def _recalc_zero_alerts_get(self):
        """Return recalc_zero_alerts_enabled from owner's user prefs (default True)."""
        try:
            prefs = _load_owner_prefs()
            enabled = prefs.get("recalc_zero_alerts_enabled", True)
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

    def _recalc_zero_alerts_post(self):
        """Accept {"enabled": bool} and persist recalc_zero_alerts_enabled in owner's user prefs."""
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
            prefs["recalc_zero_alerts_enabled"] = enabled
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

    def _paper_lookback_get(self):
        """Return the effective paper-trades look-back window.

        Reads paper_close_lookback_days from owner prefs when set, otherwise
        returns the value of the PAPER_CLOSE_LOOKBACK_DAYS env var (default 60).
        Response: {"days": int, "source": "override"|"env"}
        """
        try:
            prefs = _load_owner_prefs()
            if "paper_close_lookback_days" in prefs:
                days = int(prefs["paper_close_lookback_days"])
                source = "override"
            else:
                days = _DEFAULT_PAPER_LOOKBACK_DAYS
                source = "env"
            body = json.dumps({"days": days, "source": source}).encode()
            self.send_response(200)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _paper_lookback_post(self):
        """Accept {"days": int} and persist paper_close_lookback_days in owner's user prefs.

        Accepts a positive integer between 1 and 3650 (10 years).  Passing null
        for days clears the override and reverts to the env-var default.
        Requires DASHBOARD_WRITE_SECRET header when the env var is set.
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
            if "days" not in payload:
                body = json.dumps({"error": "'days' field is required"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            raw_days = payload["days"]
            if raw_days is None:
                prefs = _load_owner_prefs()
                prefs.pop("paper_close_lookback_days", None)
                _save_owner_prefs(prefs)
                body = json.dumps({"days": _DEFAULT_PAPER_LOOKBACK_DAYS, "source": "env"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                days = int(raw_days)
            except (TypeError, ValueError):
                body = json.dumps({"error": "'days' must be an integer"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            if days < 1 or days > 3650:
                body = json.dumps({"error": "'days' must be between 1 and 3650"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            prefs = _load_owner_prefs()
            prefs["paper_close_lookback_days"] = days
            _save_owner_prefs(prefs)
            body = json.dumps({"days": days, "source": "override"}).encode()
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

    def _backfill_heartbeat_window_get(self):
        """Return the effective backfill heartbeat window.

        Reads backfill_heartbeat_hours from owner prefs when set, otherwise
        returns the value of the BACKFILL_HEARTBEAT_HOURS env var (default 25).
        Response: {"hours": float, "source": "override"|"env"}
        """
        try:
            prefs = _load_owner_prefs()
            if "backfill_heartbeat_hours" in prefs:
                hours = float(prefs["backfill_heartbeat_hours"])
                source = "override"
            else:
                hours = _DEFAULT_HEARTBEAT_HOURS
                source = "env"
            body = json.dumps({"hours": hours, "source": source}).encode()
            self.send_response(200)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _backfill_dryrun_post(self):
        """Run run_sim_backfill.py --dry-run and return a structured JSON summary.

        This endpoint is intentionally read-only — it passes --dry-run so no
        database writes are performed.  The raw stdout is captured, key summary
        lines are parsed, and the result is returned as JSON.

        Because the dry-run may take a while (it queries the database), the
        request has a 300-second subprocess timeout.  On timeout the partial
        output that was already collected is returned with a 'timed_out' flag.

        The DASHBOARD_WRITE_SECRET guard is NOT applied here because the
        dry-run makes no writes; it is purely a preview operation.
        """
        import re as _re
        import subprocess as _sp
        import sys as _sys

        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_sim_backfill.py")
        if not os.path.isfile(script):
            body = json.dumps({"error": "run_sim_backfill.py not found on server"}).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return

        timed_out = False
        try:
            result = _sp.run(
                [_sys.executable, script, "--dry-run"],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            raw = result.stdout + ("\n" + result.stderr if result.stderr.strip() else "")
        except _sp.TimeoutExpired as _te:
            raw = (_te.stdout or "") + ("\n" + (_te.stderr or ""))
            timed_out = True
        except Exception as exc:
            body = json.dumps({"error": f"Failed to start dry-run: {exc}"}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return

        tables = []
        grand_total = None
        elapsed_s = None

        for line in raw.splitlines():
            m = _re.search(
                r"DRY RUN total for (\S+):\s+([\d,]+)\s+row\(s\) would be updated"
                r".*?Bullish Break:\s*([\d,]+).*?Bearish Break:\s*([\d,]+)"
                r".*?\+\s*([\d,]+)\s+unfillable",
                line,
            )
            if m:
                tables.append({
                    "table":    m.group(1),
                    "total":    int(m.group(2).replace(",", "")),
                    "bullish":  int(m.group(3).replace(",", "")),
                    "bearish":  int(m.group(4).replace(",", "")),
                    "unfillable": int(m.group(5).replace(",", "")),
                })
                continue
            m2 = _re.search(r"([\d,]+)\s+row\(s\) across all users/tables would be updated", line)
            if m2:
                grand_total = int(m2.group(1).replace(",", ""))
            m3 = _re.search(r"DRY RUN COMPLETE\s*[—-]\s*([\d.]+)s elapsed", line)
            if m3:
                elapsed_s = float(m3.group(1))

        data = {
            "tables":      tables,
            "grand_total": grand_total,
            "elapsed_s":   elapsed_s,
            "timed_out":   timed_out,
            "raw_output":  raw,
        }
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _context_dryrun_post(self):
        """Run run_sim_backfill.py --context-only --dry-run --out and return structured JSON.

        The script counts how many context-level rows (S/R, VWAP, MACD) would be
        updated without performing any database writes.  The JSON report written by
        the script is read back and returned to the caller with the same envelope
        used by the full pipeline dry-run: generated_at, mode, pipeline, totals, rows.

        The DASHBOARD_WRITE_SECRET guard is NOT applied here — this is a read-only
        preview operation.
        """
        import subprocess as _sp
        import sys as _sys
        import tempfile as _tmp

        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_sim_backfill.py")
        if not os.path.isfile(script):
            body = json.dumps({"error": "run_sim_backfill.py not found on server"}).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return

        timed_out   = False
        exit_code   = 0
        report      = None
        raw         = ""

        with _tmp.NamedTemporaryFile(suffix=".json", delete=False) as _tf:
            out_path = _tf.name

        try:
            result = _sp.run(
                [_sys.executable, script, "--context-only", "--dry-run", f"--out={out_path}"],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            exit_code = result.returncode
            raw = result.stdout + ("\n" + result.stderr if result.stderr.strip() else "")
        except _sp.TimeoutExpired as _te:
            raw = (_te.stdout or "") + ("\n" + (_te.stderr or ""))
            timed_out = True
        except Exception as exc:
            body = json.dumps({"error": f"Failed to start context dry-run: {exc}"}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            with open(out_path) as _f:
                report = json.load(_f)
        except Exception:
            report = None
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass

        if report is not None:
            report["timed_out"]  = timed_out
            report["raw_output"] = raw
            data = report
            http_status = 200
        else:
            # Script exited without writing a report — treat as an error when the
            # exit code is non-zero or the run was not simply a timeout.
            failed = (exit_code != 0) and not timed_out
            data = {
                "generated_at": None,
                "mode":         "dry-run",
                "pipeline":     "context-only",
                "totals":       None,
                "rows":         [],
                "timed_out":    timed_out,
                "raw_output":   raw,
                "error":        f"Script exited with code {exit_code}" if failed else None,
            }
            http_status = 500 if failed else 200

        body = json.dumps(data).encode()
        self.send_response(http_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _backfill_heartbeat_window_post(self):
        """Accept {"hours": float|null} and persist backfill_heartbeat_hours in owner's user prefs.

        Accepts a positive number between 1 and 8760 (365 days).  Passing null
        for hours clears the override and reverts to the env-var default.
        Requires DASHBOARD_WRITE_SECRET header when the env var is set.
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
            if "hours" not in payload:
                body = json.dumps({"error": "'hours' field is required"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            raw_hours = payload["hours"]
            if raw_hours is None:
                prefs = _load_owner_prefs()
                prefs.pop("backfill_heartbeat_hours", None)
                _save_owner_prefs(prefs)
                body = json.dumps({"hours": _DEFAULT_HEARTBEAT_HOURS, "source": "env"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                hours = float(raw_hours)
            except (TypeError, ValueError):
                body = json.dumps({"error": "'hours' must be a number"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            if hours < 1 or hours > 8760:
                body = json.dumps({"error": "'hours' must be between 1 and 8760"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            prefs = _load_owner_prefs()
            prefs["backfill_heartbeat_hours"] = hours
            _save_owner_prefs(prefs)
            body = json.dumps({"hours": hours, "source": "override"}).encode()
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

    def _backtest_lookback_get(self):
        """Return the effective backtest close look-back window.

        Reads backtest_close_lookback_days from owner prefs when set, otherwise
        returns the value of the BACKTEST_CLOSE_LOOKBACK_DAYS env var (default 60).
        Response: {"days": int, "source": "override"|"env"}
        """
        try:
            prefs = _load_owner_prefs()
            if "backtest_close_lookback_days" in prefs:
                days = int(prefs["backtest_close_lookback_days"])
                source = "override"
            else:
                days = _DEFAULT_BACKTEST_LOOKBACK_DAYS
                source = "env"
            body = json.dumps({"days": days, "source": source}).encode()
            self.send_response(200)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _backtest_lookback_post(self):
        """Accept {"days": int|null} and persist backtest_close_lookback_days in owner's user prefs.

        Accepts a positive integer between 1 and 3650 (10 years).  Passing null
        for days clears the override and reverts to the env-var default.
        Requires DASHBOARD_WRITE_SECRET header when the env var is set.
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
            if "days" not in payload:
                body = json.dumps({"error": "'days' field is required"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            raw_days = payload["days"]
            if raw_days is None:
                prefs = _load_owner_prefs()
                prefs.pop("backtest_close_lookback_days", None)
                _save_owner_prefs(prefs)
                body = json.dumps({"days": _DEFAULT_BACKTEST_LOOKBACK_DAYS, "source": "env"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                days = int(raw_days)
            except (TypeError, ValueError):
                body = json.dumps({"error": "'days' must be an integer"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            if days < 1 or days > 3650:
                body = json.dumps({"error": "'days' must be between 1 and 3650"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            prefs = _load_owner_prefs()
            prefs["backtest_close_lookback_days"] = days
            _save_owner_prefs(prefs)
            body = json.dumps({"days": days, "source": "override"}).encode()
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

    def _paper_trade_min_tcs_get(self):
        """Return the effective paper trade minimum TCS threshold.

        Reads paper_trade_min_tcs from owner prefs when set, otherwise
        returns the value of the PAPER_TRADE_MIN_TCS env var (default 50).
        Response: {"value": int, "source": "override"|"env"}
        """
        try:
            prefs = _load_owner_prefs()
            if "paper_trade_min_tcs" in prefs:
                val = int(prefs["paper_trade_min_tcs"])
                source = "override"
            else:
                val = _DEFAULT_MIN_TCS
                source = "env"
            body = json.dumps({"value": val, "source": source}).encode()
            self.send_response(200)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _paper_trade_min_tcs_post(self):
        """Accept {"value": int|null} and persist paper_trade_min_tcs in owner's user prefs.

        Accepts a positive integer between 0 and 100.  Passing null for value
        clears the override and reverts to the env-var default.
        Requires DASHBOARD_WRITE_SECRET header when the env var is set.
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
            if "value" not in payload:
                body = json.dumps({"error": "'value' field is required"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            raw_val = payload["value"]
            if raw_val is None:
                prefs = _load_owner_prefs()
                prefs.pop("paper_trade_min_tcs", None)
                _save_owner_prefs(prefs)
                body = json.dumps({"value": _DEFAULT_MIN_TCS, "source": "env"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                val = int(raw_val)
            except (TypeError, ValueError):
                body = json.dumps({"error": "'value' must be an integer"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            if val < 0 or val > 100:
                body = json.dumps({"error": "'value' must be between 0 and 100"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            prefs = _load_owner_prefs()
            prefs["paper_trade_min_tcs"] = val
            _save_owner_prefs(prefs)
            body = json.dumps({"value": val, "source": "override"}).encode()
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

    def _rvol_size_tiers_get(self):
        """Return the current RVOL position-size tiers from adaptive_exits.json.

        Response: {"tiers": [{"rvol_min": float, "multiplier": float}, ...],
                   "defaults": [...]}
        Tiers are sorted by rvol_min descending (highest first).
        """
        try:
            with open(_ADAPTIVE_EXITS_JSON) as _f:
                cfg = json.load(_f)
            tiers = cfg.get("rvol_size_tiers", _RVOL_SIZE_TIERS_DEFAULT)
            tiers_sorted = sorted(tiers, key=lambda t: t["rvol_min"], reverse=True)
            payload = {"tiers": tiers_sorted, "defaults": _RVOL_SIZE_TIERS_DEFAULT}
            body = json.dumps(payload).encode()
            self.send_response(200)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _rvol_size_tiers_post(self):
        """Accept {"tiers": [...]} and persist rvol_size_tiers in adaptive_exits.json.

        Each tier must have:
          rvol_min   — float > 0
          multiplier — float > 1.0

        Validation errors are returned as 400. File write errors as 500.
        Requires DASHBOARD_WRITE_SECRET header when the env var is set.
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

        def _json_error(msg: str, code: int = 400):
            b = json.dumps({"error": msg}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b)

        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw) if raw else {}
        except Exception:
            _json_error("Invalid JSON body")
            return

        if "tiers" not in payload:
            _json_error("'tiers' field is required")
            return

        raw_tiers = payload["tiers"]
        if not isinstance(raw_tiers, list):
            _json_error("'tiers' must be an array")
            return

        validated = []
        for i, t in enumerate(raw_tiers):
            if not isinstance(t, dict):
                _json_error(f"Tier {i+1} must be an object")
                return
            try:
                rvol_min = float(t["rvol_min"])
                multiplier = float(t["multiplier"])
            except (KeyError, TypeError, ValueError):
                _json_error(f"Tier {i+1} must have numeric 'rvol_min' and 'multiplier'")
                return
            if rvol_min <= 0:
                _json_error(f"Tier {i+1}: rvol_min must be > 0 (got {rvol_min})")
                return
            if multiplier <= 1.0:
                _json_error(f"Tier {i+1}: multiplier must be > 1.0 (got {multiplier})")
                return
            validated.append({"rvol_min": rvol_min, "multiplier": multiplier})

        try:
            with open(_ADAPTIVE_EXITS_JSON) as _f:
                cfg = json.load(_f)
            cfg["rvol_size_tiers"] = sorted(validated, key=lambda t: t["rvol_min"], reverse=True)
            cfg["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            with open(_ADAPTIVE_EXITS_JSON, "w") as _f:
                json.dump(cfg, _f, indent=2)
                _f.write("\n")
            result = sorted(validated, key=lambda t: t["rvol_min"], reverse=True)
            body = json.dumps({"tiers": result, "defaults": _RVOL_SIZE_TIERS_DEFAULT}).encode()
            self.send_response(200)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _config_get(self):
        """Return a snapshot of all key configuration values active at runtime.

        Combines environment-variable defaults with any user-pref overrides so
        traders can verify the live values without reading logs or env files.

        Response schema:
        {
          "paper_close_lookback_days":  {"value": int,   "source": "override"|"env"},
          "backtest_close_lookback_days": {"value": int, "source": "override"|"env"},
          "paper_trade_min_tcs":        {"value": int,   "source": "override"|"env"},
          "backfill_heartbeat_hours":   {"value": float, "source": "override"|"env"}
        }
        """
        try:
            prefs = _load_owner_prefs()

            if "paper_close_lookback_days" in prefs:
                paper_days = int(prefs["paper_close_lookback_days"])
                paper_source = "override"
            else:
                paper_days = _DEFAULT_PAPER_LOOKBACK_DAYS
                paper_source = "env"

            if "backfill_heartbeat_hours" in prefs:
                heartbeat_hours = float(prefs["backfill_heartbeat_hours"])
                heartbeat_source = "override"
            else:
                heartbeat_hours = _DEFAULT_HEARTBEAT_HOURS
                heartbeat_source = "env"

            if "backtest_close_lookback_days" in prefs:
                backtest_days = int(prefs["backtest_close_lookback_days"])
                backtest_source = "override"
            else:
                backtest_days = _DEFAULT_BACKTEST_LOOKBACK_DAYS
                backtest_source = "env"

            if "paper_trade_min_tcs" in prefs:
                min_tcs = int(prefs["paper_trade_min_tcs"])
                min_tcs_source = "override"
            else:
                min_tcs = _DEFAULT_MIN_TCS
                min_tcs_source = "env"

            payload = {
                "paper_close_lookback_days": {"value": paper_days, "source": paper_source},
                "backtest_close_lookback_days": {"value": backtest_days, "source": backtest_source},
                "paper_trade_min_tcs": {"value": min_tcs, "source": min_tcs_source},
                "backfill_heartbeat_hours": {"value": heartbeat_hours, "source": heartbeat_source},
            }
            body = json.dumps(payload).encode()
            self.send_response(200)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _screener_calibration(self):
        """Return settled trade count vs calibration threshold for each active screener.

        Screeners returned: squeeze, gap_down.
        For each screener the threshold is resolved from env vars before falling back to 30:
          - squeeze:   CALIB_MIN_TRADES_SQUEEZE, then SQUEEZE_CALIB_MIN_TRADES, then 30
          - gap_down:  CALIB_MIN_TRADES_GAP_DOWN, then 30

        Response:
          {
            "screeners": [
              {"key": str, "label": str, "count": int, "threshold": int,
               "ready": bool, "script": str, "error": null|str},
              ...
            ]
          }
        """
        _DEFAULT_THRESHOLD = 30

        def _resolve_threshold(screener_key: str) -> int:
            upper = screener_key.upper().replace("-", "_")
            env_key = f"CALIB_MIN_TRADES_{upper}"
            raw = os.environ.get(env_key, "").strip()
            if raw:
                try:
                    v = int(raw)
                    if v > 0:
                        return v
                except ValueError:
                    pass
            if screener_key == "squeeze":
                legacy = os.environ.get("SQUEEZE_CALIB_MIN_TRADES", "").strip()
                if legacy:
                    try:
                        v = int(legacy)
                        if v > 0:
                            return v
                    except ValueError:
                        pass
            return _DEFAULT_THRESHOLD

        supabase_url = os.environ.get("SUPABASE_URL", "").strip()
        supabase_key = (
            os.environ.get("SUPABASE_KEY") or
            os.environ.get("SUPABASE_ANON_KEY") or
            os.environ.get("VITE_SUPABASE_ANON_KEY") or
            ""
        ).strip()

        SCREENERS = [
            {
                "key": "squeeze",
                "label": "Squeeze",
                "script": "calibrate_squeeze_mult.py",
                "extra_filters": "",
            },
            {
                "key": "gap_down",
                "label": "Bearish Break",
                "script": "calibrate_gap_down_mult.py",
                "extra_filters": "&predicted=eq.Bearish%20Break",
            },
        ]

        results = []
        for s in SCREENERS:
            threshold = _resolve_threshold(s["key"])
            if not supabase_url or not supabase_key:
                results.append({
                    "key": s["key"],
                    "label": s["label"],
                    "count": 0,
                    "threshold": threshold,
                    "ready": False,
                    "script": s["script"],
                    "error": "Supabase not configured",
                })
                continue
            try:
                import urllib.request as _ur
                query_string = (
                    f"screener_pass=eq.{s['key']}"
                    "&tiered_pnl_r=not.is.null"
                    + s["extra_filters"]
                    + "&select=id"
                )
                req = _ur.Request(
                    f"{supabase_url}/rest/v1/paper_trades?{query_string}",
                    headers={
                        "apikey": supabase_key,
                        "Authorization": f"Bearer {supabase_key}",
                        "Accept": "application/json",
                        "Prefer": "count=exact",
                        "Range": "0-0",
                    },
                )
                count = 0
                try:
                    with _ur.urlopen(req, timeout=10) as resp:
                        content_range = resp.getheader("Content-Range", "")
                        if "/" in content_range:
                            total_str = content_range.split("/", 1)[1]
                            count = int(total_str) if total_str.isdigit() else 0
                except Exception as _inner:
                    import urllib.error as _ue
                    if isinstance(_inner, _ue.HTTPError):
                        content_range = _inner.headers.get("Content-Range", "")
                        if "/" in content_range:
                            total_str = content_range.split("/", 1)[1]
                            count = int(total_str) if total_str.isdigit() else 0
                        elif _inner.code not in (200, 206):
                            raise
                    else:
                        raise
                results.append({
                    "key": s["key"],
                    "label": s["label"],
                    "count": count,
                    "threshold": threshold,
                    "ready": count >= threshold,
                    "script": s["script"],
                    "error": None,
                })
            except Exception as exc:
                logging.warning("screener-calibration endpoint error (%s): %s", s["key"], exc)
                results.append({
                    "key": s["key"],
                    "label": s["label"],
                    "count": 0,
                    "threshold": threshold,
                    "ready": False,
                    "script": s["script"],
                    "error": "Could not load calibration data.",
                })

        body = json.dumps({"screeners": results}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _gap_down_calibration(self):
        """Return settled Bearish Break (gap_down) trade count vs the calibration threshold.

        Queries paper_trades for rows where screener_pass='gap_down' AND
        tiered_pnl_r IS NOT NULL AND predicted='Bearish Break', matching the
        exact filter used by calibrate_gap_down_mult.py.

        The threshold is resolved from CALIB_MIN_TRADES_GAP_DOWN env var,
        falling back to 30 if not set or invalid.

        Response:
          {"count": int, "threshold": int, "ready": bool, "error": null|str}
        """
        _raw_threshold = os.environ.get("CALIB_MIN_TRADES_GAP_DOWN", "").strip()
        THRESHOLD = 30
        if _raw_threshold:
            try:
                _v = int(_raw_threshold)
                if _v > 0:
                    THRESHOLD = _v
            except ValueError:
                pass
        supabase_url = os.environ.get("SUPABASE_URL", "").strip()
        supabase_key = (
            os.environ.get("SUPABASE_KEY") or
            os.environ.get("SUPABASE_ANON_KEY") or
            os.environ.get("VITE_SUPABASE_ANON_KEY") or
            ""
        ).strip()
        if not supabase_url or not supabase_key:
            body = json.dumps({"count": 0, "threshold": THRESHOLD, "ready": False, "error": "Supabase not configured"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            import urllib.parse as _up
            # Use select=id with Range/head to get exact count from Content-Range header.
            # Requesting only id with a Range cap of 0-0 avoids loading row data; the
            # Content-Range header "0-0/TOTAL" gives the authoritative count.
            query_string = (
                "screener_pass=eq.gap_down"
                "&tiered_pnl_r=not.is.null"
                "&predicted=eq." + _up.quote("Bearish Break")
                + "&select=id"
            )
            req = urllib.request.Request(
                f"{supabase_url}/rest/v1/paper_trades?{query_string}",
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Accept": "application/json",
                    "Prefer": "count=exact",
                    "Range": "0-0",
                },
            )
            count = 0
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    content_range = resp.getheader("Content-Range", "")
                    # Content-Range format: "0-0/TOTAL" or "*/TOTAL"
                    if "/" in content_range:
                        total_str = content_range.split("/", 1)[1]
                        count = int(total_str) if total_str.isdigit() else 0
            except urllib.error.HTTPError as http_exc:
                # 206 Partial Content is the expected response for Range requests; also accept 200
                content_range = http_exc.headers.get("Content-Range", "")
                if "/" in content_range:
                    total_str = content_range.split("/", 1)[1]
                    count = int(total_str) if total_str.isdigit() else 0
                elif http_exc.code not in (200, 206):
                    raise
            payload = {"count": count, "threshold": THRESHOLD, "ready": count >= THRESHOLD, "error": None}
            body = json.dumps(payload).encode()
            self.send_response(200)
        except Exception as exc:
            logging.warning("gap-down calibration endpoint error: %s", exc)
            body = json.dumps({"count": 0, "threshold": THRESHOLD, "ready": False, "error": "Could not load calibration data."}).encode()
            self.send_response(200)
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
        cwd=os.path.dirname(os.path.abspath(__file__))
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
    _BOT_DIR = os.path.dirname(os.path.abspath(__file__))
    _PY = sys.executable  # same interpreter + packages as this process

    _prod_env = {**os.environ, "EDGEIQ_PRODUCTION": "1"}

    subprocess.Popen([_PY, "paper_trader_bot.py"], cwd=_BOT_DIR, env=_prod_env,
                     stdout=open("/tmp/paper_trader_bot.log", "a"), stderr=subprocess.STDOUT)
    subprocess.Popen([_PY, "kalshi_bot.py"], cwd=_BOT_DIR, env=_prod_env,
                     stdout=open("/tmp/kalshi_bot.log", "a"), stderr=subprocess.STDOUT)
    subprocess.Popen([_PY, "nightly_tiered_pnl_refresh.py"], cwd=_BOT_DIR, env=_prod_env,
                     stdout=open("/tmp/nightly_refresh.log", "a"), stderr=subprocess.STDOUT)
    subprocess.Popen([_PY, "offering_short_bot.py"], cwd=_BOT_DIR, env=_prod_env,
                     stdout=open("/tmp/offering_short_bot.log", "a"), stderr=subprocess.STDOUT)

    threading.Thread(target=start_streamlit, daemon=True).start()
    threading.Thread(target=_refresh_db_cache, daemon=True, name="db-cache-refresher").start()

    print(f"[deploy_server] Proxy listening on port {PROXY_PORT}", flush=True)
    server = ThreadedServer(("0.0.0.0", PROXY_PORT), Handler)
    server.serve_forever()
