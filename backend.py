import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta, time as dtime
import time
import pytz
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import csv
import os
import logging
import requests
from collections import deque
try:
    import streamlit as st
    _ST_AVAILABLE = True
except (ImportError, Exception):
    _ST_AVAILABLE = False
    st = None  # type: ignore

from supabase import create_client, Client

_raw_supabase_url = os.environ.get("SUPABASE_URL", "")
import re as _re
_url_match = _re.search(r"supabase\.com/dashboard/project/([a-z0-9]+)", _raw_supabase_url)
if _url_match:
    SUPABASE_URL = f"https://{_url_match.group(1)}.supabase.co"
    logging.warning("SUPABASE_URL was a dashboard link; auto-corrected to API URL: %s", SUPABASE_URL)
elif _raw_supabase_url and ".supabase.co" in _raw_supabase_url:
    SUPABASE_URL = _raw_supabase_url.split(".supabase.co")[0].split("https://")[-1]
    SUPABASE_URL = f"https://{SUPABASE_URL}.supabase.co"
else:
    SUPABASE_URL = _raw_supabase_url
SUPABASE_KEY = (
    os.environ.get("SUPABASE_KEY") or
    os.environ.get("SUPABASE_ANON_KEY") or
    os.environ.get("VITE_SUPABASE_ANON_KEY")
)
SUPABASE_ANON_KEY = (
    os.environ.get("SUPABASE_ANON_KEY") or
    os.environ.get("VITE_SUPABASE_ANON_KEY") or
    SUPABASE_KEY
)
SUPABASE_ACCESS_TOKEN = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()

_SUPABASE_URL_PATTERN = _re.compile(r'^https://[a-z0-9]+\.supabase\.co$')

ALPACA_API_KEY    = os.environ.get("ALPACA_API_KEY", "").strip()
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "").strip()

# Sentinel stored in tiered_pnl_r for rows whose bar data will never be
# available (delisted tickers, data too old, etc.).  The sentinel is written
# only when Alpaca conclusively returns an empty bar series (not when an
# exception is raised, which indicates a transient network/auth failure).
# Once stamped, the row exits the IS NULL pending count permanently.
# load_backtest_sim_history() replaces sentinel with NaN automatically so
# downstream .notna()/.dropna() filters exclude such rows without extra handling.
TIERED_PNL_SENTINEL = -9999.0

# Sentinel stamped into backtest_sim_runs.vwap_at_ib when Alpaca returns zero
# bars for that (ticker, sim_date) — meaning data is genuinely unavailable
# (delisted stock, pre-listing, market holiday gap, etc.).  A real VWAP is
# always a positive price, so -1.0 is unambiguous.  The value tells the backfill
# script "this row has been attempted; skip it on future runs".
# get_backtest_pace_target() treats vwap_at_ib <= 0 the same as NULL so these
# rows continue to pass the VWAP gate (backward-compat behaviour for unfillable rows).
VWAP_AT_IB_SENTINEL = -1.0

# How many times run_backtest_tiered_backfill_batch() retries a bar fetch that
# raises an exception (transient API/network failure) before giving up on that
# row for this batch.  Rows that exhaust retries are skipped without stamping
# the sentinel — they remain retriable in future backfill runs.
# Rows that conclusively return zero bars (no exception, just no data) are
# stamped immediately regardless of this counter.
try:
    BACKFILL_BAR_FETCH_MAX_RETRIES: int = max(0, int(
        os.environ.get("BACKFILL_BAR_FETCH_MAX_RETRIES", "2")
    ))
except (ValueError, TypeError):
    BACKFILL_BAR_FETCH_MAX_RETRIES = 2

# IS_PAPER_ALPACA declares the intended trading mode: "true" = paper (default), "false" = live.
# Set IS_PAPER_ALPACA=false in your environment when using live brokerage keys.
IS_PAPER_ALPACA = os.environ.get("IS_PAPER_ALPACA", "true").strip().lower() == "true"
# Shared file used as IPC between deploy_server.py (proxy process) and this
# Streamlit process.  POST /api/trading-mode writes the file; get_trading_mode()
# and _check_alpaca_account_type() read it to pick up changes without a restart.
_TRADING_MODE_FILE = "/tmp/trading_mode.json"

# Each entry is (secret_name, human_readable_message).
# Validation is split into two sets:
#   _supabase_errors  — secrets that block Supabase client creation.
#   _startup_errors   — all required secrets (superset); used for the summary
#                       log and the /api/health payload.
# Optional secrets (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) are intentionally
# omitted: those helpers return early gracefully when the vars are absent, so
# the app functions correctly without them.
_supabase_errors: list[tuple[str, str]] = []
_startup_errors:  list[tuple[str, str]] = []

if not SUPABASE_URL:
    _supabase_errors.append((
        "SUPABASE_URL",
        "SUPABASE_URL is missing. Set it to https://<project-ref>.supabase.co",
    ))
elif not _SUPABASE_URL_PATTERN.match(SUPABASE_URL):
    _supabase_errors.append((
        "SUPABASE_URL",
        f"SUPABASE_URL is malformed: {SUPABASE_URL!r}. "
        "Expected format: https://<project-ref>.supabase.co",
    ))

if not SUPABASE_KEY:
    _supabase_errors.append((
        "SUPABASE_KEY",
        "SUPABASE_KEY (or SUPABASE_ANON_KEY / VITE_SUPABASE_ANON_KEY) is missing or empty.",
    ))

_startup_errors.extend(_supabase_errors)

if not ALPACA_API_KEY:
    _startup_errors.append((
        "ALPACA_API_KEY",
        "ALPACA_API_KEY is missing. Required for live and paper order placement.",
    ))

if not ALPACA_SECRET_KEY:
    _startup_errors.append((
        "ALPACA_SECRET_KEY",
        "ALPACA_SECRET_KEY is missing. Required for live and paper order placement.",
    ))

# ── Setup Checklist catalog ───────────────────────────────────────────────────
# Describes every required secret so the Setup Checklist page can show operators
# exactly what is missing and where to get it.
_SECRET_CATALOG: list[dict] = [
    {
        "name": "SUPABASE_URL",
        "label": "Supabase Project URL",
        "description": (
            "The API endpoint for your Supabase project. Used for all database reads and writes. "
            "Must be in the form https://<project-ref>.supabase.co"
        ),
        "obtain_url": "https://supabase.com/dashboard/project/_/settings/api",
        "obtain_label": "Supabase Dashboard → Settings → API → Project URL",
    },
    {
        "name": "SUPABASE_KEY",
        "label": "Supabase Anon Key",
        "description": (
            "The public anon/service key for your Supabase project. "
            "Also accepted as SUPABASE_ANON_KEY or VITE_SUPABASE_ANON_KEY."
        ),
        "obtain_url": "https://supabase.com/dashboard/project/_/settings/api",
        "obtain_label": "Supabase Dashboard → Settings → API → Project API Keys → anon / public",
    },
    {
        "name": "SUPABASE_ACCESS_TOKEN",
        "label": "Supabase Personal Access Token (optional)",
        "description": (
            "A Supabase account-level personal access token (PAT). "
            "Required for automatic database table creation on first startup via the "
            "Supabase Management API. Without it the app still works, but the app_config "
            "table must be created manually via the Supabase SQL editor."
        ),
        "obtain_url": "https://supabase.com/dashboard/account/tokens",
        "obtain_label": "Supabase Dashboard → Account → Access Tokens → Generate new token",
        "optional": True,
    },
    {
        "name": "ALPACA_API_KEY",
        "label": "Alpaca API Key",
        "description": (
            "Your Alpaca brokerage API key. Required for live and paper order placement "
            "and for fetching market data bars."
        ),
        "obtain_url": "https://app.alpaca.markets/paper/dashboard/overview",
        "obtain_label": "Alpaca Dashboard → Paper (or Live) → API Keys → Generate New Key",
    },
    {
        "name": "ALPACA_SECRET_KEY",
        "label": "Alpaca Secret Key",
        "description": (
            "Your Alpaca brokerage secret key. Paired with ALPACA_API_KEY; "
            "shown only once at creation time."
        ),
        "obtain_url": "https://app.alpaca.markets/paper/dashboard/overview",
        "obtain_label": "Alpaca Dashboard → Paper (or Live) → API Keys → Generate New Key",
    },
]

_error_names     = {_n for _n, _ in _startup_errors}
_malformed_names = {_n for _n, _m in _startup_errors if "malformed" in _m.lower()}


def _resolve_secret_status(name: str) -> str:
    """Return 'set', 'malformed', or 'missing' for a given secret name."""
    if name in _malformed_names:
        return "malformed"
    if name in _error_names:
        return "missing"
    return "set"


_secret_statuses: dict[str, str] = {
    _item["name"]: _resolve_secret_status(_item["name"])
    for _item in _SECRET_CATALOG
}
_secret_statuses["SUPABASE_ACCESS_TOKEN"] = (
    "set" if SUPABASE_ACCESS_TOKEN else "missing"
)


def recheck_secret_statuses() -> None:
    """Re-read environment variables and update _startup_errors / _secret_statuses in-place.

    Because Python caches module imports, the module-level validation code only
    runs once at startup.  This function lets operators trigger a fresh check
    (e.g. after adding a missing secret) without restarting the entire app.
    Both ``_startup_errors`` and ``_secret_statuses`` are mutable objects shared
    by reference with any importer, so clearing and repopulating them in-place
    immediately reflects the new state everywhere they are read.
    """
    _fresh_supabase_errors: list[tuple[str, str]] = []
    _fresh_startup_errors:  list[tuple[str, str]] = []

    _fresh_url = (os.environ.get("SUPABASE_URL", "") or "").strip()
    _url_m = _re.search(r"supabase\.com/dashboard/project/([a-z0-9]+)", _fresh_url)
    if _url_m:
        _fresh_url = f"https://{_url_m.group(1)}.supabase.co"
    elif _fresh_url and ".supabase.co" in _fresh_url:
        _fresh_url = f"https://{_fresh_url.split('.supabase.co')[0].split('https://')[-1]}.supabase.co"

    _fresh_key = (
        os.environ.get("SUPABASE_KEY") or
        os.environ.get("SUPABASE_ANON_KEY") or
        os.environ.get("VITE_SUPABASE_ANON_KEY") or
        ""
    )
    _fresh_alpaca_api    = os.environ.get("ALPACA_API_KEY",    "").strip()
    _fresh_alpaca_secret = os.environ.get("ALPACA_SECRET_KEY", "").strip()

    if not _fresh_url:
        _fresh_supabase_errors.append((
            "SUPABASE_URL",
            "SUPABASE_URL is missing. Set it to https://<project-ref>.supabase.co",
        ))
    elif not _SUPABASE_URL_PATTERN.match(_fresh_url):
        _fresh_supabase_errors.append((
            "SUPABASE_URL",
            f"SUPABASE_URL is malformed: {_fresh_url!r}. "
            "Expected format: https://<project-ref>.supabase.co",
        ))

    if not _fresh_key:
        _fresh_supabase_errors.append((
            "SUPABASE_KEY",
            "SUPABASE_KEY (or SUPABASE_ANON_KEY / VITE_SUPABASE_ANON_KEY) is missing or empty.",
        ))

    _fresh_startup_errors.extend(_fresh_supabase_errors)

    if not _fresh_alpaca_api:
        _fresh_startup_errors.append((
            "ALPACA_API_KEY",
            "ALPACA_API_KEY is missing. Required for live and paper order placement.",
        ))

    if not _fresh_alpaca_secret:
        _fresh_startup_errors.append((
            "ALPACA_SECRET_KEY",
            "ALPACA_SECRET_KEY is missing. Required for live and paper order placement.",
        ))

    _startup_errors.clear()
    _startup_errors.extend(_fresh_startup_errors)

    _supabase_errors.clear()
    _supabase_errors.extend(_fresh_supabase_errors)

    _fresh_error_names     = {_n for _n, _ in _fresh_startup_errors}
    _fresh_malformed_names = {_n for _n, _m in _fresh_startup_errors if "malformed" in _m.lower()}

    def _fresh_status(name: str) -> str:
        if name in _fresh_malformed_names:
            return "malformed"
        if name in _fresh_error_names:
            return "missing"
        return "set"

    _secret_statuses.clear()
    _secret_statuses.update({
        _item["name"]: _fresh_status(_item["name"])
        for _item in _SECRET_CATALOG
    })
    _fresh_pat = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    _secret_statuses["SUPABASE_ACCESS_TOKEN"] = "set" if _fresh_pat else "missing"

    _write_health_file()

    # Re-initialise Supabase clients if they are now healthy so that data reads
    # succeed without requiring a full server restart.
    if not _fresh_supabase_errors:
        global SUPABASE_URL, SUPABASE_KEY, SUPABASE_ANON_KEY   # noqa: PLW0603
        global SUPABASE_ACCESS_TOKEN                           # noqa: PLW0603
        global ALPACA_API_KEY, ALPACA_SECRET_KEY               # noqa: PLW0603
        global supabase, supabase_anon                         # noqa: PLW0603

        SUPABASE_URL = _fresh_url
        SUPABASE_KEY = _fresh_key
        SUPABASE_ANON_KEY = (
            os.environ.get("SUPABASE_ANON_KEY") or
            os.environ.get("VITE_SUPABASE_ANON_KEY") or
            _fresh_key
        )
        SUPABASE_ACCESS_TOKEN = _fresh_pat
        ALPACA_API_KEY    = _fresh_alpaca_api
        ALPACA_SECRET_KEY = _fresh_alpaca_secret

        _reinit_error: str = ""
        try:
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            logging.info("[RECHECK] Supabase client re-initialised with updated credentials.")
        except Exception as _e:
            _reinit_error = str(_e)[:120]
            logging.error("[RECHECK] Failed to re-create Supabase client: %s", _e)
            supabase = None

        try:
            supabase_anon = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
            logging.info("[RECHECK] Supabase anon client re-initialised.")
        except Exception as _e:
            if not _reinit_error:
                _reinit_error = str(_e)[:120]
            logging.error("[RECHECK] Failed to re-create Supabase anon client: %s", _e)
            supabase_anon = None

        if _reinit_error:
            _runtime_err = (
                "SUPABASE_URL",
                f"Supabase client could not be created after re-check: {_reinit_error}",
            )
            _supabase_errors.append(_runtime_err)
            _startup_errors.append(_runtime_err)
            _secret_statuses["SUPABASE_URL"] = "malformed"
            _write_health_file()


if _startup_errors:
    for _name, _err in _startup_errors:
        logging.error("[STARTUP] Required secret misconfigured — %s", _err)
    logging.error(
        "[STARTUP] %d secret(s) need attention before the app will work correctly: %s",
        len(_startup_errors),
        ", ".join(_name for _name, _ in _startup_errors),
    )

# ── Alpaca credential mismatch status ────────────────────────────────────────
# Mutated in-place by the background thread so app.py always sees the latest
# value without a new import.  None means the check has not finished yet.
_alpaca_mismatch_status: dict = {
    "checked": False,   # True once the background check has completed
    "mismatch": False,  # True if keys don't match IS_PAPER_ALPACA
    "message":  "",     # Human-readable description when mismatch=True
}

# Write startup health status to a file so the proxy can expose /api/health
# Tracks the app_config table status set by _ensure_app_config_table_exists().
# Values: "ok" (table existed), "created" (just created), "missing" (absent/unreachable).
_app_config_table_status: str = "missing"


def _write_health_file() -> None:
    """Persist the current health payload to /tmp/startup_health.json."""
    try:
        import json as _json
        _health_path = "/tmp/startup_health.json"
        _health_payload = {
            "ok": len(_startup_errors) == 0,
            "errors": [{"secret": _n, "message": _m} for _n, _m in _startup_errors],
            "alpaca_mode_mismatch": _alpaca_mismatch_status["mismatch"],
            "alpaca_mismatch_message": _alpaca_mismatch_status["message"],
            "app_config_table": _app_config_table_status,
        }
        with open(_health_path, "w") as _hf:
            _json.dump(_health_payload, _hf)
    except Exception as _he:
        logging.warning("[STARTUP] Could not write startup health file: %s", _he)

_write_health_file()

# ── Alpaca paper/live account type validation (non-blocking) ─────────────────
# Checks the is_paper_account field from GET /v2/account and compares it against
# IS_PAPER_ALPACA so the operator is warned before any orders are placed if keys
# belong to the wrong account type.
if ALPACA_API_KEY and ALPACA_SECRET_KEY:
    def _check_alpaca_account_type() -> None:
        import threading as _threading
        def _run() -> None:
            global IS_PAPER_ALPACA
            # Sync IS_PAPER_ALPACA from the shared mode file so that changes made
            # through the React dashboard's POST /api/trading-mode are reflected
            # here before the credential check runs.  This ensures both the
            # Streamlit sidebar and the React UI produce identical mismatch-check
            # behaviour.
            try:
                with open(_TRADING_MODE_FILE) as _tmf_sync:
                    _file_mode = _tmf_sync.read().strip()
                if _file_mode in ("paper", "live"):
                    IS_PAPER_ALPACA = (_file_mode == "paper")
            except FileNotFoundError:
                pass
            except Exception:
                pass
            try:
                import requests as _req
                _hdrs = {
                    "APCA-API-KEY-ID":     ALPACA_API_KEY,
                    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
                }
                _mode_label = "paper (IS_PAPER_ALPACA=true)" if IS_PAPER_ALPACA else "live (IS_PAPER_ALPACA=false)"
                _mismatch_msg = ""
                # Try paper endpoint first — paper keys authenticate here; live keys do not.
                _resp_paper = _req.get(
                    "https://paper-api.alpaca.markets/v2/account",
                    headers=_hdrs, timeout=8
                )
                if _resp_paper.status_code == 200:
                    _key_is_paper = _resp_paper.json().get("is_paper_account", True)
                    if _key_is_paper:
                        if IS_PAPER_ALPACA:
                            logging.info(
                                "[STARTUP] Alpaca credentials verified: paper keys match "
                                "paper mode (IS_PAPER_ALPACA=true) ✓"
                            )
                        else:
                            _mismatch_msg = (
                                "Paper keys detected but Trading Mode is set to Live. "
                                "Live orders will be rejected by Alpaca. "
                                "Switch Trading Mode to Paper in the dashboard sidebar, or replace your keys with live brokerage keys."
                            )
                            logging.warning(
                                "[STARTUP] ⚠️  ALPACA CREDENTIAL MISMATCH: keys belong to a "
                                "PAPER trading account (is_paper_account=True) but "
                                "IS_PAPER_ALPACA=false (live mode). %s", _mismatch_msg
                            )
                    else:
                        # Rare: paper endpoint accepted the keys but is_paper_account=False.
                        logging.warning(
                            "[STARTUP] Alpaca keys authenticated on the paper endpoint "
                            "but is_paper_account=False — unexpected combination. "
                            "Verify your key type matches your intended %s mode.",
                            _mode_label,
                        )
                elif _resp_paper.status_code in (401, 403):
                    # Paper endpoint rejected the keys — try the live endpoint.
                    _resp_live = _req.get(
                        "https://api.alpaca.markets/v2/account",
                        headers=_hdrs, timeout=8
                    )
                    if _resp_live.status_code == 200:
                        _key_is_paper = _resp_live.json().get("is_paper_account", False)
                        if not _key_is_paper:
                            if IS_PAPER_ALPACA:
                                _mismatch_msg = (
                                    "Live keys detected but Trading Mode is set to Paper. "
                                    "Paper trading will fail or route real orders unexpectedly. "
                                    "Switch Trading Mode to Live in the dashboard sidebar, or replace your keys with paper trading keys."
                                )
                                logging.warning(
                                    "[STARTUP] ⚠️  ALPACA CREDENTIAL MISMATCH: keys belong to a "
                                    "LIVE brokerage account (is_paper_account=False) but "
                                    "IS_PAPER_ALPACA=true (paper mode). %s", _mismatch_msg
                                )
                            else:
                                logging.info(
                                    "[STARTUP] Alpaca credentials verified: live keys match "
                                    "live mode (IS_PAPER_ALPACA=false) ✓"
                                )
                        else:
                            # Rare: live endpoint responded but is_paper_account=True.
                            logging.warning(
                                "[STARTUP] Alpaca keys authenticated on the live endpoint "
                                "but is_paper_account=True — unexpected combination. "
                                "Verify your key type matches your intended %s mode.",
                                _mode_label,
                            )
                    else:
                        logging.warning(
                            "[STARTUP] Alpaca account type check inconclusive "
                            "(paper=%s live=%s). Verify your keys manually.",
                            _resp_paper.status_code, _resp_live.status_code,
                        )
                else:
                    # Unexpected status from paper endpoint (e.g. 5xx transient error).
                    logging.warning(
                        "[STARTUP] Alpaca account type check inconclusive: paper endpoint "
                        "returned unexpected status %s. Verify your keys manually.",
                        _resp_paper.status_code,
                    )
                # Update the shared status dict in-place so app.py sees the result.
                _alpaca_mismatch_status["checked"] = True
                _alpaca_mismatch_status["mismatch"] = bool(_mismatch_msg)
                _alpaca_mismatch_status["message"]  = _mismatch_msg
                # Re-write the health file so /api/health reflects the check result.
                _write_health_file()
            except Exception as _ae:
                logging.debug("[STARTUP] Alpaca account-type check skipped: %s", _ae)
                _alpaca_mismatch_status["checked"] = True
        _threading.Thread(target=_run, daemon=True, name="alpaca-account-check").start()
    _check_alpaca_account_type()


def set_trading_mode(is_paper: bool) -> None:
    """Update the in-memory trading mode (IS_PAPER_ALPACA) at runtime.

    This is called when a trader changes the Trading Mode toggle in the
    dashboard so the new mode takes effect immediately without a restart.
    After updating the global, it re-runs the credential mismatch check
    in the background so _alpaca_mismatch_status stays consistent with the
    newly selected mode.  The new value is also written to
    /tmp/trading_mode.json so the proxy server can serve it via
    GET /api/trading-mode without reading backend.py's in-memory state.
    """
    global IS_PAPER_ALPACA
    IS_PAPER_ALPACA = is_paper
    logging.info(
        "[TRADING_MODE] Trading mode updated to %s",
        "PAPER" if is_paper else "LIVE",
    )
    try:
        import json as _json
        with open(_TRADING_MODE_FILE, "w") as _tmf:
            _tmf.write("paper" if is_paper else "live")
    except Exception as _tme:
        logging.debug("[TRADING_MODE] Could not write trading mode file: %s", _tme)
    if ALPACA_API_KEY and ALPACA_SECRET_KEY:
        _check_alpaca_account_type()


def get_trading_mode() -> bool:
    """Return the current IS_PAPER_ALPACA value (True = paper, False = live).

    Also syncs IS_PAPER_ALPACA from /tmp/trading_mode.json if the file has
    been updated by the React dashboard's POST /api/trading-mode endpoint so
    that changes made through the React UI are picked up here automatically.
    """
    global IS_PAPER_ALPACA
    try:
        with open(_TRADING_MODE_FILE) as _tmf:
            _mode = _tmf.read().strip()
        if _mode in ("paper", "live"):
            IS_PAPER_ALPACA = (_mode == "paper")
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return IS_PAPER_ALPACA


def check_credential_match_sync(api_key: str, secret_key: str, is_paper: bool) -> dict:
    """Synchronously check whether the given Alpaca credentials match the desired mode.

    Returns a dict with keys:
      matched      — True if the key type equals the intended mode
      key_is_paper — True/False for the detected key type, None if unknown
      error        — human-readable error string, or None on success
    """
    if not api_key or not secret_key:
        return {"matched": True, "key_is_paper": None, "error": None}
    try:
        import requests as _req
        _hdrs = {
            "APCA-API-KEY-ID":     api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }
        _r_paper = _req.get(
            "https://paper-api.alpaca.markets/v2/account",
            headers=_hdrs, timeout=8,
        )
        if _r_paper.status_code == 200:
            _key_is_paper = _r_paper.json().get("is_paper_account", True)
            return {
                "matched": _key_is_paper == is_paper,
                "key_is_paper": _key_is_paper,
                "error": None,
            }
        if _r_paper.status_code in (401, 403):
            _r_live = _req.get(
                "https://api.alpaca.markets/v2/account",
                headers=_hdrs, timeout=8,
            )
            if _r_live.status_code == 200:
                _key_is_paper = _r_live.json().get("is_paper_account", False)
                return {
                    "matched": _key_is_paper == is_paper,
                    "key_is_paper": _key_is_paper,
                    "error": None,
                }
            return {
                "matched": False,
                "key_is_paper": None,
                "error": f"Could not verify credentials (HTTP {_r_live.status_code})",
            }
        return {
            "matched": False,
            "key_is_paper": None,
            "error": f"Alpaca returned HTTP {_r_paper.status_code}",
        }
    except Exception as _ce:
        return {"matched": False, "key_is_paper": None, "error": str(_ce)}


# Supabase client creation is gated only on Supabase-specific secrets so that
# missing Alpaca credentials do not prevent data/analysis features from working.
if SUPABASE_URL and SUPABASE_KEY and not _supabase_errors:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None
    if _supabase_errors:
        logging.error(
            "[STARTUP] Supabase client NOT initialised due to %d configuration error(s) above. "
            "Fix the secrets and restart the server.",
            len(_supabase_errors),
        )

# ── RLS-enforcing client (anon key + user JWT) ────────────────────────────────
# This client respects Row Level Security. After a user logs in, call
# set_user_session() to bind their JWT so all queries are user-scoped.
if SUPABASE_URL and SUPABASE_ANON_KEY and not _supabase_errors:
    supabase_anon: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
else:
    supabase_anon = None


_db_last_checked_ts: float = 0.0  # monotonic timestamp of last check_db_connection() call


def get_db_last_checked_ts() -> float:
    """Return the monotonic timestamp of the last check_db_connection() call.

    Returns 0.0 if check_db_connection() has not been called yet this session.
    This timestamp is updated every time the function actually runs (i.e. when
    the Streamlit cache TTL expires or the retry button clears the cache), so
    callers can show how fresh the cached status reading is.
    """
    return _db_last_checked_ts


def check_db_connection() -> tuple[bool, str]:
    """Perform a lightweight check of Supabase reachability.

    Returns (True, "") when the database is reachable and credentials are
    accepted.  Returns (False, reason) for any failure, including missing
    credentials or network/auth errors.  Uses a HEAD request to the REST
    root so no table needs to exist and no rows are transferred.
    """
    global _db_last_checked_ts
    _db_last_checked_ts = time.monotonic()
    if not SUPABASE_URL or not SUPABASE_KEY or _supabase_errors:
        return False, "Credentials not configured"
    try:
        resp = requests.head(
            f"{SUPABASE_URL}/rest/v1/",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=5,
        )
        if resp.status_code in (200, 404):
            return True, ""
        return False, f"HTTP {resp.status_code}"
    except requests.exceptions.Timeout:
        return False, "Timed out"
    except Exception as exc:
        return False, str(exc)[:80]


# ── Runtime credential health monitoring ──────────────────────────────────────
# Periodically re-validates Alpaca and Supabase credentials in the background
# so that revoked or expired keys are surfaced as a dashboard banner rather
# than cryptic per-trade errors buried in logs.
_runtime_credential_errors: list[tuple[str, str]] = []
_runtime_credential_lock = threading.Lock()
_RUNTIME_CHECK_INTERVAL_S: float = 300.0   # 5 minutes between re-checks
_runtime_last_check_ts: float = 0.0        # monotonic timestamp of last check
_runtime_last_healthy_ts: float = 0.0      # monotonic timestamp of last fully-clean check
_runtime_first_check_done: bool = False    # True once _run_credential_check() finishes at least once

# Tracks which credential providers have been confirmed working at least once.
# A failure is only surfaced as a "runtime failure" (was working, now broken)
# after the provider appears in this set, making the banner message accurate.
_providers_confirmed_ok: set[str] = set()

# Tracks which credential names have already triggered a Telegram alert this
# session so we don't re-alert on every 5-minute check cycle.
_runtime_credential_alerted: set[str] = set()


def _send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    """Send a single Telegram message via the Bot API.

    Returns True on success (HTTP 200), False otherwise.  All network errors
    are caught and logged so callers never need to guard against exceptions.
    """
    import requests as _req_tg
    try:
        _resp = _req_tg.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
        if _resp.status_code == 200:
            return True
        logging.warning(
            "[TG] sendMessage failed: HTTP %s — %s", _resp.status_code, _resp.text[:120]
        )
        return False
    except Exception as _exc:
        logging.warning("[TG] sendMessage error: %s", _exc)
        return False


def _run_credential_check() -> None:
    """Validate Alpaca and Supabase credentials and update _runtime_credential_errors.

    Only credentials that were configured at startup are re-checked here; missing
    secrets are already covered by _startup_errors and shown at startup.

    A failure is only reported as a runtime failure once the provider has been
    confirmed healthy at least once, ensuring "was working, now broken" is accurate.
    """
    global _runtime_last_check_ts, _runtime_last_healthy_ts, _runtime_first_check_done
    errors: list[tuple[str, str]] = []

    # Per-run outcome flags — True only when the provider actively returned a
    # positive (200-OK / db-ok) response in this specific run.
    supabase_ok_this_run: bool = False
    alpaca_ok_this_run: bool = False
    supabase_configured: bool = bool(SUPABASE_URL and SUPABASE_KEY and not _supabase_errors)
    alpaca_configured: bool = bool(ALPACA_API_KEY and ALPACA_SECRET_KEY)

    # — Supabase —
    # Only re-check if credentials were present and valid at startup.
    if supabase_configured:
        ok, reason = check_db_connection()
        if ok:
            supabase_ok_this_run = True
            _providers_confirmed_ok.add("supabase")
        elif "supabase" in _providers_confirmed_ok:
            # Was working, now failing — surface as a runtime error.
            errors.append((
                "SUPABASE_KEY",
                f"Supabase credentials are no longer accepted (were working earlier "
                f"this session): {reason}. Check that SUPABASE_URL and SUPABASE_KEY "
                f"have not expired or been revoked.",
            ))
        else:
            # Never confirmed — log quietly; startup banner already covers missing creds.
            logging.debug("[RUNTIME] Supabase connectivity check failed before first success: %s", reason)

    # — Alpaca —
    # Only re-check if both keys were present at startup.
    if alpaca_configured:
        try:
            _hdrs = {
                "APCA-API-KEY-ID":     ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            }
            _url = (
                "https://paper-api.alpaca.markets/v2/account"
                if IS_PAPER_ALPACA else
                "https://api.alpaca.markets/v2/account"
            )
            _r = requests.get(_url, headers=_hdrs, timeout=8)
            if _r.status_code == 200:
                alpaca_ok_this_run = True
                _providers_confirmed_ok.add("alpaca")
            elif _r.status_code in (401, 403):
                if "alpaca" in _providers_confirmed_ok:
                    errors.append((
                        "ALPACA_API_KEY",
                        f"Alpaca credentials are no longer accepted by the API "
                        f"(HTTP {_r.status_code}) — the key may have been revoked or "
                        f"rotated since the app started. Update ALPACA_API_KEY and "
                        f"ALPACA_SECRET_KEY, then restart the app.",
                    ))
                else:
                    logging.debug(
                        "[RUNTIME] Alpaca credential check failed before first success (HTTP %s)",
                        _r.status_code,
                    )
        except Exception as _exc:
            logging.debug("[RUNTIME] Alpaca credential re-check failed: %s", _exc)

    _now = time.monotonic()
    with _runtime_credential_lock:
        _runtime_credential_errors[:] = errors
    _runtime_last_check_ts = _now

    # Only advance the "last confirmed healthy" timestamp when every configured
    # provider actively returned a positive response in this run.  An empty
    # errors list is not sufficient because failures can be silently suppressed
    # (not-yet-confirmed providers, caught exceptions, unconfigured credentials).
    configured_providers_all_ok = (
        (not supabase_configured or supabase_ok_this_run) and
        (not alpaca_configured or alpaca_ok_this_run) and
        (supabase_configured or alpaca_configured)  # at least one was checked
    )
    if configured_providers_all_ok:
        _runtime_last_healthy_ts = _now

    if errors:
        for _n, _m in errors:
            logging.warning("[RUNTIME] Mid-session credential failure — %s: %s", _n, _m)
    else:
        logging.debug("[RUNTIME] Credential re-check passed — all secrets still valid.")

    # ── Telegram alert for newly detected credential failures ─────────────────
    # Only fire once per credential per session (not on every 5-minute cycle).
    import os as _os_cred
    _tg_token   = _os_cred.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    _tg_chat_id = _os_cred.environ.get("TELEGRAM_CHAT_ID", "").strip()
    _failed_names = {_n for _n, _ in errors}

    if _tg_token:
        # Collect opted-in subscriber chat IDs for credential alerts.
        try:
            _sub_pairs = get_beta_chat_ids(credential_alerts_only=True)
        except Exception as _exc_sub:
            logging.warning("[RUNTIME] Could not fetch subscriber chat IDs for credential alert: %s", _exc_sub)
            _sub_pairs = []

        # Build deduplicated list of chat IDs to notify.  Admin chat always
        # receives these alerts; subscriber chat IDs are added when they have
        # opted in (default is opted in).
        def _build_recipients(exclude_admin: bool = False) -> list[str]:
            recipients: list[str] = []
            if _tg_chat_id and not exclude_admin:
                recipients.append(_tg_chat_id)
            for _uid, _cid in _sub_pairs:
                _cid_str = str(_cid)
                if _cid_str not in recipients:
                    recipients.append(_cid_str)
            return recipients

        # ── Failure alerts ────────────────────────────────────────────────
        _new_failures = [(_n, _m) for _n, _m in errors if _n not in _runtime_credential_alerted]
        for _cred_name, _cred_msg in _new_failures:
            _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _alert_msg = (
                f"🔑 <b>Credential Failure Detected</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🚨 <b>{_cred_name}</b> is no longer valid.\n"
                f"⏰ Detected at: <b>{_ts}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"{_cred_msg}"
            )
            _delivered = False
            for _rcpt in _build_recipients():
                if _send_telegram_message(_tg_token, _rcpt, _alert_msg):
                    _delivered = True
                else:
                    logging.warning(
                        "[RUNTIME] Telegram credential alert not delivered to %s for %s",
                        _rcpt, _cred_name,
                    )
            if _delivered:
                _runtime_credential_alerted.add(_cred_name)

        # ── Recovery alerts ───────────────────────────────────────────────
        # Send a one-time recovery notice for any credential that was
        # previously alerted but is now healthy again.
        _recovered_names = _runtime_credential_alerted - _failed_names
        for _recovered_name in list(_recovered_names):
            _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _recovery_msg = (
                f"✅ <b>Credential Recovered</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🟢 <b>{_recovered_name}</b> is valid again.\n"
                f"⏰ Recovered at: <b>{_ts}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━"
            )
            for _rcpt in _build_recipients():
                if not _send_telegram_message(_tg_token, _rcpt, _recovery_msg):
                    logging.warning(
                        "[RUNTIME] Telegram credential recovery alert not delivered to %s for %s",
                        _rcpt, _recovered_name,
                    )

    # Always clear recovered credentials from the alerted set regardless of
    # whether Telegram is configured — ensures a later re-failure always alerts.
    for _recovered in _runtime_credential_alerted - _failed_names:
        _runtime_credential_alerted.discard(_recovered)
    _runtime_first_check_done = True


def get_runtime_first_check_done() -> bool:
    """Return True once _run_credential_check() has completed at least one full run.

    Unlike get_runtime_last_check_ts(), this is only set to True *after* the
    background thread finishes its work — not optimistically before it spawns.
    Use this to distinguish "check still in-flight" from "check ran but unhealthy".
    """
    return _runtime_first_check_done


def get_runtime_last_check_ts() -> float:
    """Return the monotonic timestamp of the most recent credential health check.

    Returns 0.0 if no check has run yet since startup.
    """
    return _runtime_last_check_ts


def get_runtime_last_healthy_ts() -> float:
    """Return the monotonic timestamp of the last fully-clean credential check.

    Only updated when _run_credential_check completes with zero errors, so
    this timestamp accurately reflects the last time all credentials were
    confirmed working — not merely the last time a check was attempted.

    Returns 0.0 if credentials have not yet been confirmed healthy this session.
    """
    return _runtime_last_healthy_ts


def check_credentials_runtime(
    force: bool = False,
    interval_s: float | None = None,
) -> list[tuple[str, str]]:
    """Return any runtime credential failures detected since startup.

    Spawns a background re-validation thread if the check interval has elapsed
    (or *force* is True), then immediately returns the most recently cached
    results so the UI is never blocked by a network call.

    *interval_s* overrides the module-level ``_RUNTIME_CHECK_INTERVAL_S``
    default when provided (e.g. a value stored in Streamlit session state).

    Returns an empty list when all credentials remain valid.
    """
    global _runtime_last_check_ts
    effective_interval = interval_s if interval_s is not None else _RUNTIME_CHECK_INTERVAL_S
    elapsed = time.monotonic() - _runtime_last_check_ts
    if force or elapsed >= effective_interval:
        # Optimistically update the timestamp before spawning so that rapid
        # reruns don't trigger a flood of parallel check threads.
        _runtime_last_check_ts = time.monotonic()
        threading.Thread(
            target=_run_credential_check,
            daemon=True,
            name="runtime-cred-check",
        ).start()
    with _runtime_credential_lock:
        return list(_runtime_credential_errors)


def set_user_session(access_token: str, refresh_token: str) -> None:
    """Bind a logged-in user's JWT to the RLS-enforcing client.

    Must be called after every login or session restore so that
    supabase_anon queries are automatically scoped to that user.
    The paper_trader_bot continues to use the service-key client
    (supabase) which bypasses RLS by design.
    """
    if supabase_anon and access_token:
        try:
            supabase_anon.auth.set_session(access_token, refresh_token or "")
        except Exception:
            pass

# ── Supabase Auth helpers ─────────────────────────────────────────────────────

def auth_login(email: str, password: str) -> dict:
    """Sign in via Supabase email/password auth."""
    if not supabase:
        return {"user": None, "session": None, "error": "Supabase not configured."}
    try:
        resp = supabase.auth.sign_in_with_password({"email": email, "password": password})
        return {"user": resp.user, "session": resp.session, "error": None}
    except Exception as exc:
        msg = str(exc)
        if "Invalid login credentials" in msg:
            msg = "Invalid email or password."
        elif "Email not confirmed" in msg:
            msg = "Please confirm your email before logging in."
        return {"user": None, "session": None, "error": msg}


def auth_signup(email: str, password: str) -> dict:
    """Sign up via Supabase email/password auth."""
    if not supabase:
        return {"user": None, "session": None, "error": "Supabase not configured."}
    try:
        resp = supabase.auth.sign_up({"email": email, "password": password})
        return {"user": resp.user, "session": resp.session, "error": None}
    except Exception as exc:
        return {"user": None, "session": None, "error": str(exc)}


def auth_signout() -> None:
    """Sign out the current Supabase auth session."""
    if not supabase:
        return
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    clear_session_cache()


_SESSION_CACHE = os.path.join(os.path.dirname(__file__), ".local", "session_cache.json")


def save_session_cache(user_id: str, email: str, refresh_token: str) -> None:
    """Persist the Supabase refresh token to disk so we can restore the session
    after a server restart without asking the user to log in again."""
    try:
        os.makedirs(os.path.dirname(_SESSION_CACHE), exist_ok=True)
        with open(_SESSION_CACHE, "w") as _f:
            json.dump({"user_id": user_id, "email": email,
                       "refresh_token": refresh_token}, _f)
    except Exception:
        pass


def load_session_cache() -> dict:
    """Read the persisted session cache. Returns {} if missing or corrupt."""
    try:
        if os.path.exists(_SESSION_CACHE):
            with open(_SESSION_CACHE) as _f:
                return json.load(_f)
    except Exception:
        pass
    return {}


def clear_session_cache() -> None:
    """Delete the session cache (called on explicit sign-out)."""
    try:
        if os.path.exists(_SESSION_CACHE):
            os.remove(_SESSION_CACHE)
    except Exception:
        pass


def try_restore_session() -> dict:
    """Attempt to restore a previous session from the cached refresh token.

    Returns {"user": <User>, "email": str} on success, {} on failure.
    """
    if not supabase:
        return {}
    cache = load_session_cache()
    token = cache.get("refresh_token", "")
    if not token:
        return {}
    try:
        resp = supabase.auth.refresh_session(token)
        if resp and resp.user:
            # Persist the new refresh token (it rotates on each use)
            save_session_cache(
                str(resp.user.id),
                str(resp.user.email),
                resp.session.refresh_token if resp.session else token,
            )
            return {
                "user":          resp.user,
                "email":         str(resp.user.email),
                "access_token":  resp.session.access_token  if resp.session else "",
                "refresh_token": resp.session.refresh_token if resp.session else "",
            }
    except Exception as _e:
        print(f"Session restore failed: {_e}")
        clear_session_cache()
    return {}


def check_user_id_column_exists() -> bool:
    """Return True if user_id column already exists in trade_journal."""
    if not supabase:
        return False
    try:
        supabase.table("trade_journal").select("user_id").limit(1).execute()
        return True
    except Exception as e:
        return "user_id" not in str(e)  # column error → False; other errors → assume True

from engine_v2 import (
    calculate_v2_metrics, get_profile_and_shape, calculate_historical_retention,
    identify_overhead_supply, detect_volatility_halts, v2_brain_final_boss,
    calculate_time_multiplier, v2_brain_v3, get_volume_profile_v2, v2_execution_logic
)

STATE_FILE   = "trade_state.json"
TRACKER_FILE = "accuracy_tracker.csv"
WEIGHTS_FILE      = "brain_weights.json"            # ⛔ READ-ONLY — live personal brain (paper trades + journal)
HIST_WEIGHTS_FILE    = "brain_weights_historical.json" # historical brain — calibrated from backtest_sim_runs
TCS_THRESHOLDS_FILE  = "tcs_thresholds.json"          # per-structure TCS cutoffs saved after nightly recalibration
TCS_THRESHOLD_HISTORY_FILE = "tcs_threshold_history.jsonl"  # append-only history log (one JSON record per line)
def _parse_retention_days(env_val: str | None, default: int = 90) -> int:
    try:
        val = int(env_val)
        return val if val > 0 else default
    except (TypeError, ValueError):
        return default

TCS_HISTORY_RETENTION_DAYS = _parse_retention_days(os.environ.get("TCS_HISTORY_RETENTION_DAYS"), 90)  # days of history to keep in the threshold history log
TCS_BASE_SCORE = _parse_retention_days(os.environ.get("TCS_BASE_SCORE"), 65)  # baseline TCS gate used in compute_structure_tcs_thresholds(); override via env var

# Canonical display-label mapping for TCS structure weight keys.
# Single source of truth — imported by app.py and paper_trader_bot.py.
WK_DISPLAY: dict[str, str] = {
    "trend_bull":     "📈 Trend Bull",
    "trend_bear":     "📉 Trend Bear",
    "double_dist":    "🔀 Double Dist",
    "non_trend":      "➡️ Non-Trend",
    "normal":         "🔔 Normal",
    "neutral":        "⚖️ Neutral",
    "ntrl_extreme":   "⚡ Ntrl Extreme",
    "nrml_variation": "〰️ Nrml Variation",
}

WK_DISPLAY_PLAIN: dict[str, str] = {
    k: _re.sub(r"[^\w\s()/\-]", "", v).strip()
    for k, v in WK_DISPLAY.items()
}
HICONS_FILE  = "high_conviction_log.csv"
HICONS_THRESHOLD = 75.0
MODE_SWITCH_AUDIT_FILE = "trading_mode_audit.csv"
SA_JOURNAL_FILE  = "sa_journal.csv"
JOURNAL_PATH = "trade_journal.csv"
_JOURNAL_COLS = [
    "timestamp", "ticker", "price", "structure", "tcs", "rvol",
    "ib_high", "ib_low", "notes", "grade", "grade_reason",
    "source", "entry_price", "exit_price", "pnl_pct", "win_loss",
    "followed_plan", "deviation_notes", "transcript", "audio_b64",
    "voice_signals",
    "process_grade", "process_grade_reason",
]

_BRAIN_WEIGHT_KEYS = [
    "trend_bull", "trend_bear", "double_dist",
    "non_trend",  "normal",     "neutral",
    "ntrl_extreme", "nrml_variation",
]
_RECALIBRATE_EVERY = 10
EASTERN = pytz.timezone("America/New_York")

# ── NYSE Market Holiday Calendar ──────────────────────────────────────────────
# Standard NYSE holidays 2025–2027  (observed date when holiday falls on weekend)
_NYSE_HOLIDAYS: set = {
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
    "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
    "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
}


def is_trading_day(d: date) -> bool:
    """Return True if d is a NYSE trading day (not a weekend or known holiday)."""
    return d.weekday() < 5 and d.isoformat() not in _NYSE_HOLIDAYS


def get_last_trading_day(as_of: date = None,
                         api_key: str = "",
                         secret_key: str = "") -> date:
    """Return the most recent completed NYSE trading day on or before as_of.

    Strategy:
    1. Ask Alpaca's /v1/calendar if credentials are supplied (most accurate).
    2. Fall back to hardcoded _NYSE_HOLIDAYS list.
    3. Last resort: skip weekends only.
    """
    if as_of is None:
        as_of = date.today()

    # ── Alpaca calendar (accurate, handles early closes & ad-hoc closures) ──
    if api_key and secret_key:
        try:
            start_str = (as_of - timedelta(days=14)).isoformat()
            end_str   = as_of.isoformat()
            r = requests.get(
                "https://paper-api.alpaca.markets/v1/calendar",
                params={"start": start_str, "end": end_str},
                headers={
                    "APCA-API-KEY-ID":     api_key,
                    "APCA-API-SECRET-KEY": secret_key,
                },
                timeout=5,
            )
            if r.status_code == 200:
                cal = r.json()
                trading_dates = sorted(
                    [c["date"] for c in cal if c["date"] <= end_str],
                    reverse=True,
                )
                if trading_dates:
                    return date.fromisoformat(trading_dates[0])
        except Exception:
            pass

    # ── Hardcoded holiday fallback ──────────────────────────────────────────
    d = as_of
    for _ in range(14):
        if is_trading_day(d):
            return d
        d -= timedelta(days=1)

    # ── Absolute last resort: weekend-only ──────────────────────────────────
    d = as_of
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def fetch_bars(api_key, secret_key, ticker, trade_date, feed="sip"):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client = StockHistoricalDataClient(api_key, secret_key)
    mo = EASTERN.localize(datetime(trade_date.year, trade_date.month, trade_date.day, 9, 30))
    mc = EASTERN.localize(datetime(trade_date.year, trade_date.month, trade_date.day, 16, 0))
    # When fetching today's intraday data cap end to now so the API doesn't
    # get a future end time. If we're before market open, nothing to fetch yet.
    # Paid SIP subscription — real-time data, no delay cap needed.
    now_et = datetime.now(EASTERN)
    if trade_date >= now_et.date():
        if now_et <= mo:
            return pd.DataFrame()   # pre-market — no bars yet
        if now_et < mc:
            mc = now_et             # mid-session — cap end to current time
    req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
                           start=mo, end=mc, feed=feed)
    bars = client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level="symbol")
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(EASTERN)
    df = df.sort_index()
    df = df[(df.index.time >= dtime(9, 30)) & (df.index.time <= dtime(16, 0))]
    df["vwap"] = compute_vwap(df)
    return df


def compute_vwap(df: "pd.DataFrame") -> "pd.Series":
    """Compute intraday VWAP anchored to the session open.

    Typical Price = (High + Low + Close) / 3
    VWAP = cumsum(Typical Price × Volume) / cumsum(Volume)

    Returns a Series aligned to df.index, or an empty Series on failure.
    """
    try:
        tp  = (df["high"] + df["low"] + df["close"]) / 3.0
        vol = df["volume"].replace(0, float("nan"))
        cum_tpv = (tp * vol).cumsum()
        cum_vol = vol.cumsum()
        return cum_tpv / cum_vol
    except Exception:
        return pd.Series(dtype=float)


def compute_initial_balance(df):
    """Return (ib_high, ib_low) for the standard 9:30–10:30 first-hour window.

    Includes bars with timestamps from 9:30 through 10:30 (inclusive) —
    matching the industry convention used by most platforms (Webull, TOS, etc.)
    where the IB is the first 60 minutes of the regular session.
    Builds the cutoff from the date of the first bar to avoid tz-replace issues.
    """
    if df.empty:
        return None, None
    first_ts = df.index[0]
    tz = first_ts.tzinfo
    ib_end = pd.Timestamp(
        year=first_ts.year, month=first_ts.month, day=first_ts.day,
        hour=10, minute=30, second=59, tz=tz,
    )
    ib_data = df[df.index <= ib_end]
    if ib_data.empty:
        return None, None
    return float(ib_data["high"].max()), float(ib_data["low"].min())


def compute_volume_profile(df, num_bins):
    price_min = df["low"].min()
    price_max = df["high"].max()
    bins = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bins[:-1] + bins[1:]) / 2
    vap = np.zeros(num_bins)
    for _, row in df.iterrows():
        lo, hi, vol = row["low"], row["high"], row["volume"]
        i0 = max(0, int(np.searchsorted(bins, lo, side="left")) - 1)
        i1 = min(num_bins, int(np.searchsorted(bins, hi, side="right")))
        sp = i1 - i0
        if sp > 0:
            vap[i0:i1] += vol / sp
    poc_idx = int(np.argmax(vap))
    return bin_centers, vap, float(bin_centers[poc_idx])


def _compute_value_area(bin_centers, vap, pct=0.70):
    """Return (VAL, VAH) — the price range containing `pct` of session volume.

    Starts at the POC and expands one bin at a time (always adding whichever
    adjacent bin has more volume), until the accumulated total reaches the
    target percentage.  This is the CME / Market Profile standard method.
    """
    total = float(np.sum(vap))
    if total == 0 or len(vap) == 0:
        return None, None
    poc_idx = int(np.argmax(vap))
    acc = float(vap[poc_idx])
    lo = hi = poc_idx
    while acc / total < pct:
        can_up = hi + 1 < len(vap)
        can_dn = lo - 1 >= 0
        if not can_up and not can_dn:
            break
        uv = float(vap[hi + 1]) if can_up else -1.0
        dv = float(vap[lo - 1]) if can_dn else -1.0
        if uv >= dv:
            hi += 1; acc += uv
        else:
            lo -= 1; acc += dv
    return float(bin_centers[lo]), float(bin_centers[hi])


# ══════════════════════════════════════════════════════════════════════════════
# SMALL ACCOUNT CHALLENGE — HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def compute_macd(close_series, fast=12, slow=26, signal=9):
    """Return (macd_line, signal_line, histogram) as pandas Series."""
    ema_f = close_series.ewm(span=fast, adjust=False).mean()
    ema_s = close_series.ewm(span=slow, adjust=False).mean()
    macd  = ema_f - ema_s
    sig   = macd.ewm(span=signal, adjust=False).mean()
    return macd, sig, macd - sig



def get_whole_half_levels(price_low, price_high):
    """Return all $0.50 increment levels between price_low and price_high.
    Whole dollars are key resistance; half dollars secondary.
    """
    lo = np.floor(price_low * 2) / 2
    hi = np.ceil(price_high * 2) / 2
    return [round(x, 2) for x in np.arange(lo, hi + 0.01, 0.50)
            if price_low * 0.98 <= x <= price_high * 1.02]


def detect_poc_shift(bin_centers, vap):
    """Classify POC position relative to the full profile range.
    Upper third = Bullish (buyers in control); lower third = Bearish.
    """
    if len(bin_centers) == 0:
        return "Neutral — no data", "#ffa726"
    poc_idx = int(np.argmax(vap))
    pct = poc_idx / len(bin_centers)
    if pct >= 0.67:
        return "Bullish — POC in upper zone ↑", "#4caf50"
    if pct <= 0.33:
        return "Bearish — POC in lower zone ↓", "#ef5350"
    return "Neutral — POC mid-range", "#ffa726"


def count_consecutive_greens(df):
    """Count how many consecutive green candles appear at the tail of df."""
    closes = df["close"].values
    opens  = df["open"].values
    count  = 0
    for i in range(len(closes) - 1, -1, -1):
        if closes[i] > opens[i]:
            count += 1
        else:
            break
    return count


def compute_recovery_ratio(loss_pct):
    """Return the % gain required to recover from loss_pct% drawdown."""
    if loss_pct <= 0:
        return 0.0
    if loss_pct >= 100:
        return float("inf")
    return round((loss_pct / (100.0 - loss_pct)) * 100.0, 1)


def load_sa_journal():
    """Load the Small Account trade log from CSV."""
    if not os.path.exists(SA_JOURNAL_FILE):
        return []
    try:
        return pd.read_csv(SA_JOURNAL_FILE).to_dict("records")
    except Exception:
        return []


def save_sa_journal(entries):
    """Persist the Small Account trade log to CSV."""
    if not entries:
        return
    try:
        pd.DataFrame(entries).to_csv(SA_JOURNAL_FILE, index=False)
    except Exception:
        pass


def _find_peaks(smoothed, bin_centers, threshold_pct=0.30):
    """Return indices of local maxima that exceed threshold_pct of the profile max."""
    n = len(smoothed)
    max_v = smoothed.max()
    peaks = []
    for i in range(3, n - 3):
        if (smoothed[i] >= max_v * threshold_pct and
                smoothed[i] > smoothed[i-1] and smoothed[i] > smoothed[i+1] and
                smoothed[i] > smoothed[i-2] and smoothed[i] > smoothed[i+2]):
            # Deduplicate: require at least 3 bins from the previous accepted peak
            if not peaks or (i - peaks[-1]) >= 3:
                peaks.append(i)
    return peaks


def _is_strong_hvn(pk, vap):
    """True if peak qualifies as an HVN by small-cap DD criteria.

    Either:
      • Volume in ±2-bin window around peak > 20 % of total session volume, OR
      • Peak bin volume > 2.5× the average bin volume.
    """
    total_vol = vap.sum()
    if total_vol == 0:
        return False
    avg_bin = total_vol / len(vap)
    window = vap[max(0, pk-2): min(len(vap), pk+3)].sum()
    return (window / total_vol > 0.20) or (vap[pk] > 2.5 * avg_bin)


def _detect_double_distribution(bin_centers, vap, min_bin_sep=15):
    """Return (pk1_idx, pk2_idx, lvn_idx) if a valid Double Distribution is found, else None."""
    smoothed = np.convolve(vap.astype(float), np.ones(5)/5, mode="same")
    peaks = _find_peaks(smoothed, bin_centers, threshold_pct=0.25)
    for j in range(len(peaks) - 1):
        pk1, pk2 = peaks[j], peaks[j+1]
        # Must be at least 15 bins apart
        if (pk2 - pk1) < min_bin_sep:
            continue
        # Both peaks must qualify as strong HVNs
        if not (_is_strong_hvn(pk1, vap) and _is_strong_hvn(pk2, vap)):
            continue
        # Must have a clear LVN valley between them
        vi = int(np.argmin(smoothed[pk1:pk2+1])) + pk1
        if smoothed[vi] < 0.60 * min(smoothed[pk1], smoothed[pk2]):
            return pk1, pk2, vi
    return None


def compute_atr(df, period=14):
    """Average True Range over `period` bars (or full session when fewer bars available)."""
    if df.empty:
        return 0.01
    if len(df) < 2:
        return max(0.01, float(df["high"].iloc[0] - df["low"].iloc[0]))
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return max(0.01, float(tr.rolling(period, min_periods=1).mean().iloc[-1]))


# ══════════════════════════════════════════════════════════════════════════════
# TIER 3 — CHART PATTERN DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def _resample_bars(df_1m, rule="5min"):
    """Resample 1-minute OHLCV bars to a coarser timeframe."""
    if df_1m is None or df_1m.empty:
        return pd.DataFrame()
    agg = {c: ("first" if c == "open" else "max" if c == "high"
               else "min" if c == "low" else "last" if c == "close"
               else "sum")
           for c in ["open", "high", "low", "close", "volume"] if c in df_1m.columns}
    if not agg:
        return pd.DataFrame()
    try:
        return df_1m.resample(rule).agg(agg).dropna(subset=["close"])
    except Exception:
        return pd.DataFrame()


def _find_swing_highs(df, lookback=2):
    """Return integer positions of swing high bars (local maxima ± lookback bars)."""
    highs = df["high"].values
    n = len(highs)
    out = []
    for i in range(lookback, n - lookback):
        if all(highs[i] >= highs[i - j] for j in range(1, lookback + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, lookback + 1)):
            out.append(i)
    return out


def _find_swing_lows(df, lookback=2):
    """Return integer positions of swing low bars (local minima ± lookback bars)."""
    lows = df["low"].values
    n = len(lows)
    out = []
    for i in range(lookback, n - lookback):
        if all(lows[i] <= lows[i - j] for j in range(1, lookback + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, lookback + 1)):
            out.append(i)
    return out


def detect_chart_patterns(df_1m, poc_price=None, ib_high=None, ib_low=None):
    """Detect classic chart patterns on 5m and 1hr resampled bars.

    Returns a list of pattern dicts sorted by score descending.  Each dict:
        name        — pattern name (str)
        direction   — 'Bullish' | 'Bearish'
        timeframe   — '5m' | '1hr'
        score       — 0.0–1.0 weighted confidence
        confluence  — list[str] of confluence reasons
        description — plain-language explanation
        neckline    — key price level (float | None)
    """
    if df_1m is None or df_1m.empty or len(df_1m) < 20:
        return []

    patterns = []

    for tf_label, rule in [("5m", "5min"), ("1hr", "60min")]:
        df_tf = _resample_bars(df_1m, rule)
        if df_tf is None or len(df_tf) < 8:
            continue

        # 5m: lookback=3 (15 min on each side) — filters micro-noise on fast bars
        # 1hr: lookback=2 (2 hrs on each side) — already structural
        _lb = 3 if tf_label == "5m" else 2
        sh_idx = _find_swing_highs(df_tf, lookback=_lb)
        sl_idx = _find_swing_lows(df_tf, lookback=_lb)
        atr_val = compute_atr(df_tf, period=min(14, len(df_tf)))
        close_now = float(df_tf["close"].iloc[-1])
        n = len(df_tf)

        # ── Reverse Head & Shoulders (Bullish) ────────────────────────────
        if len(sl_idx) >= 3:
            ls_i, h_i, rs_i = sl_idx[-3], sl_idx[-2], sl_idx[-1]
            p_ls = float(df_tf["low"].iloc[ls_i])
            p_h  = float(df_tf["low"].iloc[h_i])
            p_rs = float(df_tf["low"].iloc[rs_i])
            if p_h < p_ls and p_h < p_rs:
                sym = abs(p_ls - p_rs) / max(abs(p_h - (p_ls + p_rs) / 2), 0.001)
                if sym < 0.80:
                    hl = float(df_tf["high"].iloc[ls_i:h_i + 1].max()) if h_i > ls_i else p_ls
                    hr = float(df_tf["high"].iloc[h_i:rs_i + 1].max()) if rs_i > h_i else p_rs
                    neckline = round((hl + hr) / 2.0, 4)
                    score = 0.70
                    conf = []
                    if poc_price and abs(p_h - poc_price) / max(poc_price, 0.001) < 0.02:
                        score += 0.10
                        conf.append("Head at POC")
                    if ib_low and abs(p_h - ib_low) / max(ib_low, 0.001) < 0.02:
                        score += 0.10
                        conf.append("Head at IB Low")
                    if close_now >= neckline * 0.985:
                        score += 0.10
                        conf.append("Price at neckline — breakout imminent")
                    nl_str = f"${neckline:.2f}"
                    desc = (f"L-shoulder ${p_ls:.2f} → Head ${p_h:.2f} → "
                            f"R-shoulder ${p_rs:.2f}. Neckline ~{nl_str}.")
                    patterns.append({"name": "Reverse Head & Shoulders",
                                     "direction": "Bullish", "timeframe": tf_label,
                                     "score": round(min(score, 1.0), 2),
                                     "confluence": conf, "description": desc,
                                     "neckline": neckline})

        # ── Head & Shoulders (Bearish) ────────────────────────────────────
        if len(sh_idx) >= 3:
            ls_i, h_i, rs_i = sh_idx[-3], sh_idx[-2], sh_idx[-1]
            p_ls = float(df_tf["high"].iloc[ls_i])
            p_h  = float(df_tf["high"].iloc[h_i])
            p_rs = float(df_tf["high"].iloc[rs_i])
            if p_h > p_ls and p_h > p_rs:
                sym = abs(p_ls - p_rs) / max(abs(p_h - (p_ls + p_rs) / 2), 0.001)
                if sym < 0.80:
                    ll = float(df_tf["low"].iloc[ls_i:h_i + 1].min()) if h_i > ls_i else p_ls
                    lr = float(df_tf["low"].iloc[h_i:rs_i + 1].min()) if rs_i > h_i else p_rs
                    neckline = round((ll + lr) / 2.0, 4)
                    score = 0.70
                    conf = []
                    if poc_price and abs(p_h - poc_price) / max(poc_price, 0.001) < 0.02:
                        score += 0.10
                        conf.append("Head at POC")
                    if ib_high and abs(p_h - ib_high) / max(ib_high, 0.001) < 0.02:
                        score += 0.10
                        conf.append("Head at IB High")
                    if close_now <= neckline * 1.015:
                        score += 0.10
                        conf.append("Price testing neckline")
                    nl_str = f"${neckline:.2f}"
                    desc = (f"L-shoulder ${p_ls:.2f} → Head ${p_h:.2f} → "
                            f"R-shoulder ${p_rs:.2f}. Neckline ~{nl_str}.")
                    patterns.append({"name": "Head & Shoulders",
                                     "direction": "Bearish", "timeframe": tf_label,
                                     "score": round(min(score, 1.0), 2),
                                     "confluence": conf, "description": desc,
                                     "neckline": neckline})

        # ── Double Bottom (Bullish) ───────────────────────────────────────
        if len(sl_idx) >= 2:
            i1, i2 = sl_idx[-2], sl_idx[-1]
            p1 = float(df_tf["low"].iloc[i1])
            p2 = float(df_tf["low"].iloc[i2])
            mid_price = (p1 + p2) / 2.0
            diff_pct = abs(p1 - p2) / max(mid_price, 0.001)
            if diff_pct < 0.03:
                neckline = round(float(df_tf["high"].iloc[i1:i2 + 1].max()), 4)
                score = 0.65
                conf = []
                if poc_price and abs(mid_price - poc_price) / max(poc_price, 0.001) < 0.025:
                    score += 0.10
                    conf.append("Bottoms at POC")
                if ib_low and abs(mid_price - ib_low) / max(ib_low, 0.001) < 0.025:
                    score += 0.10
                    conf.append("Bottoms at IB Low")
                if close_now > neckline:
                    score += 0.15
                    conf.append("Neckline broken — confirmed")
                elif close_now >= neckline * 0.985:
                    score += 0.05
                    conf.append("Price at neckline")
                diff_pct_str = f"{diff_pct * 100:.1f}"
                neckline_str = f"${neckline:.2f}"
                desc = (f"Two lows at ${p1:.2f} / ${p2:.2f} ({diff_pct_str}% apart). "
                        f"Neckline {neckline_str}.")
                patterns.append({"name": "Double Bottom",
                                 "direction": "Bullish", "timeframe": tf_label,
                                 "score": round(min(score, 1.0), 2),
                                 "confluence": conf, "description": desc,
                                 "neckline": neckline})

        # ── Double Top (Bearish) ──────────────────────────────────────────
        if len(sh_idx) >= 2:
            i1, i2 = sh_idx[-2], sh_idx[-1]
            p1 = float(df_tf["high"].iloc[i1])
            p2 = float(df_tf["high"].iloc[i2])
            mid_price = (p1 + p2) / 2.0
            diff_pct = abs(p1 - p2) / max(mid_price, 0.001)
            if diff_pct < 0.03:
                neckline = round(float(df_tf["low"].iloc[i1:i2 + 1].min()), 4)
                score = 0.65
                conf = []
                if poc_price and abs(mid_price - poc_price) / max(poc_price, 0.001) < 0.025:
                    score += 0.10
                    conf.append("Tops at POC")
                if ib_high and abs(mid_price - ib_high) / max(ib_high, 0.001) < 0.025:
                    score += 0.10
                    conf.append("Tops at IB High")
                if close_now < neckline:
                    score += 0.15
                    conf.append("Neckline broken — confirmed")
                diff_pct_str = f"{diff_pct * 100:.1f}"
                neckline_str = f"${neckline:.2f}"
                desc = (f"Two highs at ${p1:.2f} / ${p2:.2f} ({diff_pct_str}% apart). "
                        f"Neckline {neckline_str}.")
                patterns.append({"name": "Double Top",
                                 "direction": "Bearish", "timeframe": tf_label,
                                 "score": round(min(score, 1.0), 2),
                                 "confluence": conf, "description": desc,
                                 "neckline": neckline})

        # ── Bull Flag (Bullish) ───────────────────────────────────────────
        if n >= 10:
            mid = n // 2
            pole_move = float(df_tf["close"].iloc[mid]) - float(df_tf["close"].iloc[0])
            pole_range = (float(df_tf["high"].iloc[:mid].max())
                         - float(df_tf["low"].iloc[:mid].min()))
            flag_hi = float(df_tf["high"].iloc[mid:].max())
            flag_lo = float(df_tf["low"].iloc[mid:].min())
            flag_range = flag_hi - flag_lo
            flag_slope = ((float(df_tf["close"].iloc[-1]) - float(df_tf["close"].iloc[mid]))
                         / max(n - mid, 1))
            is_pole = pole_move > atr_val * 2.5 and pole_move > 0
            is_tight = flag_range < pole_range * 0.55
            is_down_drift = flag_slope < 0
            if is_pole and is_tight and is_down_drift:
                score = 0.68
                conf = []
                if poc_price and flag_lo <= poc_price <= flag_hi:
                    score += 0.12
                    conf.append("Flag consolidating at POC")
                if ib_high and flag_lo <= ib_high <= flag_hi:
                    score += 0.10
                    conf.append("Flag at IB High")
                pole_str = f"${pole_move:.2f}"
                flag_str = f"${flag_range:.2f}"
                target_str = f"${flag_hi + pole_move:.2f}"
                desc = (f"Pole +{pole_str} → tight flag range {flag_str}. "
                        f"Breakout target ~{target_str}.")
                patterns.append({"name": "Bull Flag",
                                 "direction": "Bullish", "timeframe": tf_label,
                                 "score": round(min(score, 1.0), 2),
                                 "confluence": conf, "description": desc,
                                 "neckline": round(flag_hi, 4)})

        # ── Bear Flag (Bearish) ───────────────────────────────────────────
        if n >= 10:
            mid = n // 2
            pole_move = float(df_tf["close"].iloc[0]) - float(df_tf["close"].iloc[mid])
            pole_range = (float(df_tf["high"].iloc[:mid].max())
                         - float(df_tf["low"].iloc[:mid].min()))
            flag_hi = float(df_tf["high"].iloc[mid:].max())
            flag_lo = float(df_tf["low"].iloc[mid:].min())
            flag_range = flag_hi - flag_lo
            flag_slope = ((float(df_tf["close"].iloc[-1]) - float(df_tf["close"].iloc[mid]))
                         / max(n - mid, 1))
            is_pole = pole_move > atr_val * 2.5 and pole_move > 0
            is_tight = flag_range < pole_range * 0.55
            is_up_drift = flag_slope > 0
            if is_pole and is_tight and is_up_drift:
                score = 0.68
                conf = []
                if poc_price and flag_lo <= poc_price <= flag_hi:
                    score += 0.12
                    conf.append("Flag at POC")
                target_str = f"${flag_lo - pole_move:.2f}"
                pole_str = f"${pole_move:.2f}"
                flag_str = f"${flag_range:.2f}"
                desc = (f"Pole drop -{pole_str} → counter-rally {flag_str}. "
                        f"Breakdown target ~{target_str}.")
                patterns.append({"name": "Bear Flag",
                                 "direction": "Bearish", "timeframe": tf_label,
                                 "score": round(min(score, 1.0), 2),
                                 "confluence": conf, "description": desc,
                                 "neckline": round(flag_lo, 4)})

        # ── Cup & Handle (Bullish) ────────────────────────────────────────
        if n >= 15:
            cup_end = n * 2 // 3
            cup_df = df_tf.iloc[:cup_end]
            cup_start = float(cup_df["close"].iloc[0])
            cup_low = float(cup_df["low"].min())
            cup_end_price = float(cup_df["close"].iloc[-1])
            depth = cup_start - cup_low
            recovery = (cup_end_price - cup_low) / max(depth, 0.001)
            handle_df = df_tf.iloc[cup_end:]
            if len(handle_df) > 0:
                h_hi = float(handle_df["high"].max())
                h_lo = float(handle_df["low"].min())
                handle_depth_ratio = (h_hi - h_lo) / max(depth, 0.001)
                is_cup = recovery > 0.65 and depth > atr_val * 2
                is_handle = 0.04 < handle_depth_ratio < 0.45
                if is_cup and is_handle:
                    score = 0.72
                    conf = []
                    if poc_price and abs(cup_low - poc_price) / max(poc_price, 0.001) < 0.025:
                        score += 0.12
                        conf.append("Cup base at POC")
                    target = cup_start + depth
                    recovery_str = f"{recovery * 100:.0f}"
                    h_lo_str = f"${h_lo:.2f}"
                    h_hi_str = f"${h_hi:.2f}"
                    target_str = f"${target:.2f}"
                    desc = (f"Cup base ${cup_low:.2f} → {recovery_str}% recovered. "
                            f"Handle {h_lo_str}–{h_hi_str}. Target {target_str}.")
                    patterns.append({"name": "Cup & Handle",
                                     "direction": "Bullish", "timeframe": tf_label,
                                     "score": round(min(score, 1.0), 2),
                                     "confluence": conf, "description": desc,
                                     "neckline": round(cup_start, 4)})

    # ── Inside Bar (any timeframe) ─────────────────────────────────────────
    for tf_label, rule in [("5m", "5min"), ("1hr", "60min")]:
        df_tf = _resample_bars(df_1m, rule)
        if df_tf is None or len(df_tf) < 3:
            continue
        cur_high = float(df_tf["high"].iloc[-1])
        cur_low  = float(df_tf["low"].iloc[-1])
        prev_high = float(df_tf["high"].iloc[-2])
        prev_low  = float(df_tf["low"].iloc[-2])
        if cur_high <= prev_high and cur_low >= prev_low:
            ib_range = prev_high - prev_low
            cur_range = cur_high - cur_low
            compression = 1.0 - (cur_range / max(ib_range, 0.001))
            score = 0.60
            conf = []
            if compression > 0.50:
                score += 0.10
                conf.append(f"Strong compression ({compression*100:.0f}%)")
            if poc_price and prev_low <= poc_price <= prev_high:
                score += 0.10
                conf.append("Inside bar at POC")
            if ib_high and (abs(prev_high - ib_high) / max(ib_high, 0.001) < 0.015):
                score += 0.10
                conf.append("Inside bar at IB High")
            if ib_low and (abs(prev_low - ib_low) / max(ib_low, 0.001) < 0.015):
                score += 0.10
                conf.append("Inside bar at IB Low")
            cur_close = float(df_tf["close"].iloc[-1])
            midpoint = (prev_low + prev_high) / 2
            direction = "Bullish" if cur_close > midpoint else "Bearish"
            desc = (f"Current bar (H:{cur_high:.2f} L:{cur_low:.2f}) fully inside "
                    f"prior bar (H:{prev_high:.2f} L:{prev_low:.2f}). "
                    f"Compression {compression*100:.0f}%. Break above {prev_high:.2f} = bullish, "
                    f"below {prev_low:.2f} = bearish.")
            patterns.append({"name": "Inside Bar",
                             "direction": direction, "timeframe": tf_label,
                             "score": round(min(score, 1.0), 2),
                             "confluence": conf, "description": desc,
                             "neckline": round(prev_high, 4)})

    # ── Confluence boost: stacked patterns ────────────────────────────────────
    bull = [p for p in patterns if p["direction"] == "Bullish"]
    bear = [p for p in patterns if p["direction"] == "Bearish"]
    if len(bull) >= 2:
        extra = f"Stacked with {len(bull) - 1} other bullish pattern(s)"
        for p in bull:
            p["confluence"].append(extra)
            p["score"] = round(min(p["score"] * 1.15, 1.0), 2)
    if len(bear) >= 2:
        extra = f"Stacked with {len(bear) - 1} other bearish pattern(s)"
        for p in bear:
            p["confluence"].append(extra)
            p["score"] = round(min(p["score"] * 1.15, 1.0), 2)

    patterns.sort(key=lambda x: x["score"], reverse=True)
    return patterns


def scan_ticker_patterns(api_key: str, secret_key: str, ticker: str,
                         trade_date, feed: str = "iex") -> list:
    """Fetch intraday bars for a single ticker and return detected chart patterns.

    Wrapper around fetch_bars + detect_chart_patterns used by the gap scanner
    to show pattern alerts alongside each scanner card.  Returns [] on failure.
    """
    try:
        df = fetch_bars(api_key, secret_key, ticker, trade_date, feed=feed)
        if df is None or df.empty or len(df) < 20:
            return []
        return detect_chart_patterns(df)
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
# MARKET BRAIN  — real-time IB tracker + structure predictor
# ══════════════════════════════════════════════════════════════════════════════

class MarketBrain:
    """Runs alongside classify_day_structure to predict structure mid-session.

    Call update(df, rvol) on each refresh; read `.prediction` for the live call.
    After the actual structure is classified, call log_accuracy() to record
    Predicted vs Actual in accuracy_tracker.csv.
    """

    _STRUCTURE_COLORS = {
        "Trend Day":            "#ff9800",
        "Double Distribution":  "#00bcd4",
        "Non-Trend":            "#78909c",
        "Normal":               "#66bb6a",
        "Normal Variation":     "#aed581",
        "Neutral":              "#80cbc4",
        "Neutral Extreme":      "#7e57c2",
        "Analyzing IB…":        "#888888",
    }

    def __init__(self):
        self.ib_high        = 0.0
        self.ib_low         = float("inf")
        self.ib_set         = False
        self.high_touched   = False
        self.low_touched    = False
        self.prediction     = "Analyzing IB…"

    # ── Restore from session state so we survive Streamlit reruns ──────────────
    def load_from_session(self):
        self.ib_high      = st.session_state.brain_ib_high
        self.ib_low       = st.session_state.brain_ib_low
        self.ib_set       = st.session_state.brain_ib_set
        self.high_touched = st.session_state.brain_high_touched
        self.low_touched  = st.session_state.brain_low_touched
        self.prediction   = st.session_state.brain_predicted or "Analyzing IB…"

    def save_to_session(self):
        st.session_state.brain_ib_high      = self.ib_high
        st.session_state.brain_ib_low       = self.ib_low
        st.session_state.brain_ib_set       = self.ib_set
        st.session_state.brain_high_touched = self.high_touched
        st.session_state.brain_low_touched  = self.low_touched
        st.session_state.brain_predicted    = self.prediction

    # ── Main update call ───────────────────────────────────────────────────────
    def update(self, df, rvol=None, ib_vol_pct=None, poc_price=None, has_double_dist=False):
        """Ingest fresh bar data, update IB, and re-predict.

        The 7-structure framework (Dalton / volume profile):
        ┌─────────────────────────────────────────────────────────────────────┐
        │  IB interaction          │  Close position    │  Structure          │
        ├──────────────────────────┼────────────────────┼─────────────────────┤
        │  Neither side broken     │  IB was wide       │  Normal             │
        │  Neither side broken     │  IB was narrow/low │  Non-Trend          │
        │  BOTH sides broken       │  Near middle/IB    │  Neutral            │
        │  BOTH sides broken       │  Near day extreme  │  Neutral Extreme    │
        │  ONE side only broken    │  Moderate move     │  Normal Variation   │
        │  ONE side only broken    │  Two vol clusters  │  Double Distribution│
        │  ONE side only broken    │  Dominant/early    │  Trend Day          │
        └─────────────────────────────────────────────────────────────────────┘

        Parameters
        ----------
        df              : OHLCV DataFrame (ET-indexed, may contain NaN reindex rows)
        rvol            : relative volume vs expected; None → 0.0
        ib_vol_pct      : fraction of total session volume traded inside IB (0–1)
        poc_price       : Point of Control from volume profile
        has_double_dist : True when _detect_double_distribution() found two peaks
        """
        if df.empty:
            return
        # Strip NaN rows inserted by the chart reindex grid
        _df = df.dropna(subset=["open", "high", "low", "close"])
        if _df.empty:
            return
        rvol = rvol or 0.0
        ib_end = _df.index[0].replace(hour=10, minute=30, second=0)

        # Accumulate IB extremes over the first hour (9:30–10:30)
        ib_df = _df[_df.index <= ib_end]
        if not ib_df.empty:
            self.ib_high = max(self.ib_high, float(ib_df["high"].max()))
            self.ib_low  = min(self.ib_low,  float(ib_df["low"].min()))

        last_time = _df.index[-1].time()
        if last_time > dtime(10, 30):
            self.ib_set = True

        if self.ib_set and self.ib_high > 0 and self.ib_low < float("inf"):
            current_price = float(_df["close"].iloc[-1])
            day_high      = float(_df["high"].max())
            day_low       = float(_df["low"].min())
            ib_range      = self.ib_high - self.ib_low

            if day_high >= self.ib_high:  self.high_touched = True
            if day_low  <= self.ib_low:   self.low_touched  = True

            # ── IB interaction buckets (the core 3-way split) ─────────────────
            no_break     = not self.high_touched and not self.low_touched
            both_broken  = self.high_touched and self.low_touched
            one_side_up  = self.high_touched and not self.low_touched
            one_side_dn  = self.low_touched  and not self.high_touched
            one_side     = one_side_up or one_side_dn

            # ── Derived signals ───────────────────────────────────────────────
            _ivp            = ib_vol_pct if ib_vol_pct is not None else 0.5
            directional_vol = _ivp < 0.35   # <35% of volume in IB → directional
            balanced_vol    = _ivp > 0.62   # >62% of volume in IB → rotational

            poc_outside_ib  = (poc_price is not None
                               and (poc_price > self.ib_high or poc_price < self.ib_low))

            total_range      = day_high - day_low
            range_expansion  = total_range / ib_range if ib_range > 0 else 1.0

            # Where did price close in today's range? (0.0 = at day low, 1.0 = at day high)
            close_pct        = ((current_price - day_low) / total_range
                                if total_range > 0 else 0.5)
            # "Near extreme" = closing in the top 20% or bottom 20% of day range
            close_at_extreme = close_pct >= 0.80 or close_pct <= 0.20
            # "In the middle" = closing within IB range or close to it
            close_near_ib    = self.ib_low <= current_price <= self.ib_high

            # ── BRANCH 1: Neither IB side violated ───────────────────────────
            # Both Normal and Non-Trend have no break. The difference is IB SIZE:
            #   Normal   → wide IB set by large players early; price stays inside
            #   Non-Trend → narrow IB, no volume/interest (holiday, eve-of-news, etc.)
            if no_break:
                # < 1.5% of price AND balanced vol AND minimal range expansion = Non-Trend
                is_narrow = ib_range < 0.015 * self.ib_high
                if is_narrow and balanced_vol and range_expansion <= 1.25:
                    self.prediction = "Non-Trend"
                else:
                    self.prediction = "Normal"

            # ── BRANCH 2: BOTH IB sides violated → always Neutral family ─────
            # Transcript: "both sides violated → EITHER closes in middle (Neutral)
            # OR one side dominates and closes near an extreme (Neutral Extreme)"
            elif both_broken:
                if close_at_extreme:
                    self.prediction = "Neutral Extreme"
                else:
                    self.prediction = "Neutral"

            # ── BRANCH 3: ONE side only violated → Trend / Dbl Dist / Nrml Var
            # Transcript: Trend = "pretty much from the open, very dominant, ONE side only"
            # Double Dist = two distinct volume clusters; a thin LVN in the middle
            # Normal Variation = one side broken but NOT dominant/early
            else:  # one_side is True
                # Double Distribution: bimodal profile detected OR
                # POC migrated out of IB but IB still has meaningful volume
                # (volume stayed in 2 places, not fully directional)
                is_double = has_double_dist or (poc_outside_ib and not directional_vol)

                # Trend: POC fully migrated + all volume directional, OR
                # strong early break + close firmly at the extreme
                is_trend = (
                    (poc_outside_ib and directional_vol)
                    or (close_at_extreme and range_expansion >= 2.0)
                    or (close_at_extreme and rvol >= 2.0)
                    or (close_at_extreme and directional_vol)
                )

                if is_trend:
                    self.prediction = "Trend Day"
                elif is_double:
                    self.prediction = "Double Distribution"
                else:
                    self.prediction = "Normal Variation"
        else:
            self.prediction = "Analyzing IB…"

        self.save_to_session()

    def color(self):
        return self._STRUCTURE_COLORS.get(self.prediction, "#888")


# ── Accuracy tracker persistence ──────────────────────────────────────────────

def load_accuracy_tracker(user_id: str = "") -> pd.DataFrame:
    """Load MarketBrain accuracy history from Supabase, optionally filtered by user_id."""
    cols = ["timestamp", "symbol", "predicted", "actual", "correct",
            "entry_price", "exit_price", "mfe", "compare_key"]
    if not supabase:
        return pd.DataFrame(columns=cols)
    try:
        q = supabase.table("accuracy_tracker").select("*")
        if user_id:
            try:
                q = q.eq("user_id", user_id)
            except Exception:
                pass
        response = q.execute()
        data = response.data
        if not data:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(data)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        return df
    except Exception as e:
        print(f"Database read error (tracker): {e}")
        return pd.DataFrame(columns=cols)


def _clean_structure_label(raw: str) -> str:
    """Strip emoji/special chars and truncate to 30 chars for a canonical label."""
    import re
    s = re.sub(r"[^\w\s()/\-]", "", str(raw)).strip()
    return s[:30] if len(s) > 30 else s


def log_accuracy_entry(symbol, predicted, actual, compare_key="",
                       entry_price=0.0, exit_price=0.0, mfe=0.0,
                       user_id: str = ""):
    """Log Predicted vs Actual structure to Supabase."""
    if not supabase:
        return
    predicted = _clean_structure_label(predicted)
    actual    = _clean_structure_label(actual)
    correct = "✅" if _strip_emoji(predicted) in _strip_emoji(actual) or \
                     _strip_emoji(actual) in _strip_emoji(predicted) else "❌"
    row = {
        "timestamp":   datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol":      symbol,
        "predicted":   predicted,
        "actual":      actual,
        "correct":     correct,
        "entry_price": float(entry_price),
        "exit_price":  float(exit_price),
        "mfe":         float(mfe),
        "compare_key": compare_key,
    }
    if user_id:
        row["user_id"] = user_id
    try:
        supabase.table("accuracy_tracker").insert(row).execute()
        res = supabase.table("accuracy_tracker").select("id", count="exact").execute()
        _n_rows = res.count if res.count else 0
        if _n_rows > 0 and _n_rows % _RECALIBRATE_EVERY == 0:
            recalibrate_brain_weights()
    except Exception as e:
        print(f"Database write error (tracker): {e}")


def log_high_conviction(ticker, trade_date, structure, prob,
                        ib_high=None, ib_low=None, poc_price=None):
    """Append a row to high_conviction_log.csv when top prob ≥ HICONS_THRESHOLD.

    Deduplication: one row per ticker+date combination — existing row is
    updated (overwritten) if prob is higher than what was previously recorded.
    """
    _cols = ["timestamp", "ticker", "date", "structure", "prob_pct",
             "ib_high", "ib_low", "poc_price"]
    _row  = {
        "timestamp": datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S"),
        "ticker":    ticker,
        "date":      str(trade_date),
        "structure": structure,
        "prob_pct":  round(prob, 1),
        "ib_high":   round(ib_high, 4) if ib_high else "",
        "ib_low":    round(ib_low, 4)  if ib_low  else "",
        "poc_price": round(poc_price, 4) if poc_price else "",
    }
    # Load existing, drop any previous row for same ticker+date, then append
    if os.path.exists(HICONS_FILE):
        try:
            _df = pd.read_csv(HICONS_FILE, encoding="utf-8")
            _mask = ~((_df["ticker"] == ticker) & (_df["date"] == str(trade_date)))
            _df = _df[_mask]
        except Exception:
            _df = pd.DataFrame(columns=_cols)
    else:
        _df = pd.DataFrame(columns=_cols)
    _new = pd.concat([_df, pd.DataFrame([_row])], ignore_index=True)
    _new.to_csv(HICONS_FILE, index=False, encoding="utf-8")


def load_high_conviction_log():
    """Return the high conviction log as a DataFrame, newest entries first."""
    if not os.path.exists(HICONS_FILE):
        return pd.DataFrame()
    try:
        _df = pd.read_csv(HICONS_FILE, encoding="utf-8")
        if _df.empty:
            return _df
        return _df.sort_values("prob_pct", ascending=False).reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def log_mode_switch(user_id: str, previous_mode: str, new_mode: str) -> None:
    """Append one row to the trading-mode audit log (append-only CSV)."""
    _cols = ["timestamp", "user_id", "previous_mode", "new_mode"]
    _row = {
        "timestamp":     datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S"),
        "user_id":       user_id or "unknown",
        "previous_mode": previous_mode,
        "new_mode":      new_mode,
    }
    if os.path.exists(MODE_SWITCH_AUDIT_FILE):
        try:
            _df = pd.read_csv(MODE_SWITCH_AUDIT_FILE, encoding="utf-8")
        except Exception:
            _df = pd.DataFrame(columns=_cols)
    else:
        _df = pd.DataFrame(columns=_cols)
    _df = pd.concat([_df, pd.DataFrame([_row])], ignore_index=True)
    _df.to_csv(MODE_SWITCH_AUDIT_FILE, index=False, encoding="utf-8")


def load_mode_switch_log() -> "pd.DataFrame":
    """Return the trading-mode audit log as a DataFrame, newest entries first."""
    if not os.path.exists(MODE_SWITCH_AUDIT_FILE):
        return pd.DataFrame()
    try:
        _df = pd.read_csv(MODE_SWITCH_AUDIT_FILE, encoding="utf-8")
        if _df.empty:
            return _df
        return _df.iloc[::-1].reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _strip_emoji(s):
    """Rough emoji stripper for fuzzy structure matching."""
    import re
    return re.sub(r"[^\w\s/()]", "", str(s)).strip().lower()


# ══════════════════════════════════════════════════════════════════════════════
# ADAPTIVE BRAIN LEARNING — per-structure accuracy weights
# ══════════════════════════════════════════════════════════════════════════════

def _label_to_weight_key(label: str) -> str:
    """Map a raw structure label to one of the canonical weight keys."""
    s = label.lower()
    if "bear" in s or "down" in s:          return "trend_bear"
    if "bull" in s:                          return "trend_bull"   # Bullish Break → trend_bull
    if "trend" in s:                         return "trend_bull"
    if "double" in s or "dbl" in s:         return "double_dist"
    if "non" in s:                           return "non_trend"
    if "variation" in s or "var" in s:       return "nrml_variation"
    if "extreme" in s:                       return "ntrl_extreme"
    if "neutral" in s:                       return "neutral"
    if "normal" in s or "balance" in s:      return "normal"
    return "normal"   # safe default

# Public alias — used by paper_trader_bot for per-structure TCS lookup
label_to_weight_key = _label_to_weight_key


def load_brain_weights(user_id: str = "") -> dict:
    """Load adaptive calibration weights — per-user from Supabase prefs, then local file.

    Per-user weights are stored inside user_preferences.prefs["brain_weights"] so no
    extra table is needed.  Falls back to the global brain_weights.json for backward
    compatibility and anonymous use.
    """
    import json as _json
    defaults = {k: 1.0 for k in _BRAIN_WEIGHT_KEYS}

    # Per-user path (Supabase prefs)
    if user_id:
        try:
            prefs  = load_user_prefs(user_id)
            stored = prefs.get("brain_weights", {})
            if stored and isinstance(stored, dict):
                return {k: float(stored.get(k, defaults.get(k, 1.0)))
                        for k in _BRAIN_WEIGHT_KEYS}
        except Exception:
            pass

    # Global file fallback
    if not os.path.exists(WEIGHTS_FILE):
        return defaults
    try:
        with open(WEIGHTS_FILE) as f:
            stored = _json.load(f)
        return {k: float(stored.get(k, 1.0)) for k in _BRAIN_WEIGHT_KEYS}
    except Exception:
        return defaults


def _save_brain_weights(weights: dict, user_id: str = "") -> None:
    """Persist weights to global file AND, if user_id supplied, to per-user Supabase prefs."""
    import json as _json
    clean = {k: round(float(v), 4) for k, v in weights.items()}

    # Always write global file (backward compat / anonymous)
    try:
        with open(WEIGHTS_FILE, "w") as f:
            _json.dump(clean, f, indent=2)
    except Exception:
        pass

    # Per-user persistence via user_preferences.prefs
    if user_id:
        try:
            prefs = load_user_prefs(user_id)
            prefs["brain_weights"] = clean
            save_user_prefs(user_id, prefs)
        except Exception:
            pass


def recalibrate_brain_weights(user_id: str = "") -> dict:
    """Read the accuracy tracker, compute per-structure accuracy, and update weights.

    Learning rule (smoothed exponential moving average):
      target = 1.5 if acc ≥ 70% | 1.0 if 50-70% | 0.75 if 30-50% | 0.5 if < 30%
      new_weight = old_weight × 0.70  +  target × 0.30   (30% learning rate)

    Structures with fewer than 5 samples are left unchanged (avoid overfitting).
    Returns the updated weights dict.
    """
    weights = load_brain_weights(user_id)
    if not os.path.exists(TRACKER_FILE):
        return weights
    try:
        df = pd.read_csv(TRACKER_FILE)
        if df.empty or "predicted" not in df.columns or "correct" not in df.columns:
            return weights

        # Group by predicted structure
        for raw_label, grp in df.groupby("predicted"):
            if len(grp) < 5:
                continue   # too few samples — skip
            acc = (grp["correct"] == "✅").sum() / len(grp)
            wk  = _label_to_weight_key(str(raw_label))

            # Target weight based on accuracy band
            if acc >= 0.70:   target = 1.50
            elif acc >= 0.50: target = 1.00
            elif acc >= 0.30: target = 0.75
            else:             target = 0.50

            # Smooth update (EMA-style, 30% learning rate)
            old = weights.get(wk, 1.0)
            weights[wk] = round(old * 0.70 + target * 0.30, 4)

        _save_brain_weights(weights, user_id)
    except Exception:
        pass
    return weights


def recalibrate_from_supabase(user_id: str = "") -> dict:
    """Read ALL live outcome data from Supabase and update brain weights.

    Data sources (tracked SEPARATELY, then volume-weighted blended):
      1. accuracy_tracker table  — journal-verified trades (predicted / correct ✅/❌)
      2. paper_trades table      — bot paper trades (predicted / win_loss Win/Loss)

    Blending approach (volume-weighted, adapts with data):
      Each source's accuracy is computed independently per structure, then blended
      proportionally by sample count — NOT a fixed 50/50.
      As data grows, the source with more verified trades earns more influence.

      blend rules per structure:
        both sources have ≥MIN_SAMPLES  → acc = (j_n*j_acc + b_n*b_acc) / (j_n+b_n)
        only journal  has ≥MIN_SAMPLES  → acc = journal_acc
        only bot      has ≥MIN_SAMPLES  → acc = bot_acc
        neither has ≥MIN_SAMPLES        → skip (no update)

      MIN_SAMPLES scales with total verified data:
        <50 total rows  → MIN_SAMPLES = 3   (early days, accept thin data)
        50–200 rows     → MIN_SAMPLES = 5
        200–500 rows    → MIN_SAMPLES = 8
        500+ rows       → MIN_SAMPLES = 12

    Learning rule (adaptive EMA, rate scales with per-structure sample count):
      target = 1.5 if acc ≥ 70% | 1.0 if 50–70% | 0.75 if 30–50% | 0.5 if <30%
      EMA rate scales with n:  <10→0.10 | 10–25→0.15 | 25–50→0.25 | 50–100→0.35 | 100+→0.40
      new_weight = old_weight × (1−rate) + target × rate

    Returns dict:
      {
        "weights":      {structure_key: new_weight, …},
        "deltas":       [{key, old, new, delta, blended_acc, journal_acc, bot_acc,
                          journal_n, bot_n}, …],
        "sources":      {"accuracy_tracker": N, "paper_trades": N, "total": N},
        "calibrated":   bool,
        "timestamp":    iso string,
      }
    """
    import collections as _col

    weights = load_brain_weights(user_id)
    result  = {
        "weights":    weights,
        "deltas":     [],
        "sources":    {"accuracy_tracker": 0, "paper_trades": 0, "total": 0},
        "calibrated": False,
        "timestamp":  datetime.now(EASTERN).isoformat(),
    }

    if not supabase:
        return result

    # ── Separate accumulators per source ──────────────────────────────────
    journal_data: dict = _col.defaultdict(lambda: {"wins": 0, "total": 0})
    bot_data:     dict = _col.defaultdict(lambda: {"wins": 0, "total": 0})

    # Source 1: accuracy_tracker (journal / manual trades)
    try:
        q = supabase.table("accuracy_tracker").select("predicted,correct")
        if user_id:
            q = q.eq("user_id", user_id)
        rows = q.execute().data or []
        for r in rows:
            pred    = str(r.get("predicted", "") or "").strip()
            correct = str(r.get("correct",   "") or "").strip()
            if not pred:
                continue
            wk = _label_to_weight_key(pred)
            journal_data[wk]["total"] += 1
            if "✅" in correct:
                journal_data[wk]["wins"] += 1
        result["sources"]["accuracy_tracker"] = len(rows)
    except Exception as e:
        print(f"recalibrate_from_supabase: accuracy_tracker error: {e}")

    # Source 2: paper_trades (bot automated signals)
    try:
        q = supabase.table("paper_trades").select("predicted,win_loss")
        if user_id:
            q = q.eq("user_id", user_id)
        rows = q.execute().data or []
        for r in rows:
            pred = str(r.get("predicted", "") or "").strip()
            wl   = str(r.get("win_loss",  "") or "").strip().lower()
            if not pred or not wl or wl in ("", "none", "pending"):
                continue
            wk = _label_to_weight_key(pred)
            bot_data[wk]["total"] += 1
            if wl == "win":
                bot_data[wk]["wins"] += 1
        result["sources"]["paper_trades"] = len(rows)
    except Exception as e:
        print(f"recalibrate_from_supabase: paper_trades error: {e}")

    result["sources"]["total"] = (
        result["sources"]["accuracy_tracker"] + result["sources"]["paper_trades"]
    )

    # ── Adaptive blend and EMA update ──────────────────────────────────────
    # MIN_SAMPLES scales with total verified data — avoid overfitting on thin data
    total_verified = result["sources"]["total"]
    if   total_verified < 50:  MIN_SAMPLES = 3
    elif total_verified < 200: MIN_SAMPLES = 5
    elif total_verified < 500: MIN_SAMPLES = 8
    else:                      MIN_SAMPLES = 12

    all_keys = set(journal_data.keys()) | set(bot_data.keys())
    deltas   = []

    for wk in all_keys:
        j = journal_data[wk]
        b = bot_data[wk]

        j_ok = j["total"] >= MIN_SAMPLES
        b_ok = b["total"] >= MIN_SAMPLES

        if not j_ok and not b_ok:
            continue   # not enough data in either source — skip

        j_n   = j["total"]
        b_n   = b["total"]
        j_acc = (j["wins"] / j_n) if j_ok else None
        b_acc = (b["wins"] / b_n) if b_ok else None

        # Volume-weighted blend — sample count determines influence, not a fixed split
        if j_ok and b_ok:
            blended = (j_n * j_acc + b_n * b_acc) / (j_n + b_n)
        elif j_ok:
            blended = j_acc   # only journal has enough data
        else:
            blended = b_acc   # only bot has enough data

        if   blended >= 0.70: target = 1.50
        elif blended >= 0.50: target = 1.00
        elif blended >= 0.30: target = 0.75
        else:                 target = 0.50

        # EMA rate scales with total per-structure samples — more data = faster learning
        total_n = j_n + b_n
        if   total_n >= 100: ema_rate = 0.40
        elif total_n >=  50: ema_rate = 0.35
        elif total_n >=  25: ema_rate = 0.25
        elif total_n >=  10: ema_rate = 0.15
        else:                ema_rate = 0.10

        old_val     = weights.get(wk, 1.0)
        new_val     = round(old_val * (1 - ema_rate) + target * ema_rate, 4)
        weights[wk] = new_val

        deltas.append({
            "key":         wk,
            "old":         round(old_val, 4),
            "new":         new_val,
            "delta":       round(new_val - old_val, 4),
            "blended_acc": round(blended * 100, 1),
            "journal_acc": round(j_acc * 100, 1) if j_ok else None,
            "bot_acc":     round(b_acc * 100, 1) if b_ok else None,
            "journal_n":   j_n,
            "bot_n":       b_n,
            "ema_rate":    ema_rate,
            "min_samples": MIN_SAMPLES,
            "target":      target,
        })

    if deltas:
        _save_brain_weights(weights, user_id)
        result["calibrated"] = True

    # After live-brain calibration, persist the latest per-structure TCS thresholds
    # so the bot can load them at scan time without hitting Supabase mid-morning.
    try:
        save_tcs_thresholds(compute_structure_tcs_thresholds())
    except Exception:
        pass

    result["weights"] = weights
    result["deltas"]  = sorted(deltas, key=lambda x: abs(x["delta"]), reverse=True)
    return result


# ── Historical Brain (separate from live personal brain) ──────────────────────

def load_historical_brain_weights() -> dict:
    """Load the historical brain weights from brain_weights_historical.json.
    Falls back to neutral 1.0 for all keys if file doesn't exist yet."""
    import json as _json
    defaults = {k: 1.0 for k in _BRAIN_WEIGHT_KEYS}
    if not os.path.exists(HIST_WEIGHTS_FILE):
        return defaults
    try:
        with open(HIST_WEIGHTS_FILE) as f:
            stored = _json.load(f)
        return {k: float(stored.get(k, 1.0)) for k in _BRAIN_WEIGHT_KEYS}
    except Exception:
        return defaults


def _save_historical_brain_weights(weights: dict) -> None:
    """Persist historical brain weights to brain_weights_historical.json."""
    import json as _json
    clean = {k: round(float(v), 4) for k, v in weights.items()}
    try:
        with open(HIST_WEIGHTS_FILE, "w") as f:
            _json.dump(clean, f, indent=2)
    except Exception:
        pass


def save_tcs_thresholds(thresholds: list) -> None:
    """Save per-structure TCS thresholds keyed by weight_key to tcs_thresholds.json.

    Called after each brain calibration so the bot can load at scan time
    without hitting Supabase mid-morning.

    Format: {"neutral": 59, "ntrl_extreme": 49, "double_dist": 49, ...}

    History recording is intentionally NOT done here — callers are responsible
    for calling append_tcs_threshold_history(old, new) once after the full
    recalibration sequence completes so that a single, clean event is stored
    using the true before/after snapshots.
    """
    import json as _json
    out: dict = {}
    for t in thresholds:
        wk  = t.get("wk", "")
        tcs = t.get("recommended_tcs", 50)
        if wk:
            out[wk] = int(tcs)

    try:
        with open(TCS_THRESHOLDS_FILE, "w") as f:
            _json.dump(out, f, indent=2)
    except Exception:
        pass


def append_tcs_threshold_history(previous: dict, current: dict, min_delta: int = 3) -> None:
    """Persist a TCS threshold-shift event to tcs_threshold_history.jsonl.

    Appends one record when at least one structure's threshold moved by
    *min_delta* or more points.  Call this ONCE after a complete
    recalibration cycle (both brains) using the snapshots taken before and
    after the full run — not inside the individual save_tcs_thresholds calls.

    Args:
        previous: dict of {weight_key: int} captured before recalibration.
        current:  dict of {weight_key: int} captured after recalibration.
        min_delta: minimum absolute point change that triggers a record (default 3).
    """
    import json as _json
    import datetime as _dt

    if not current:
        return

    has_shift = any(
        abs(int(current.get(k, 0)) - int(previous.get(k, 0))) >= min_delta
        for k in set(current) | set(previous)
        if isinstance(current.get(k), (int, float)) and isinstance(previous.get(k), (int, float))
    )
    if not has_shift and previous:
        return

    try:
        record = {
            "timestamp": _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "thresholds": {k: int(v) for k, v in current.items()},
            "previous":   {k: int(v) for k, v in previous.items()},
        }
        with open(TCS_THRESHOLD_HISTORY_FILE, "a") as _hf:
            _hf.write(_json.dumps(record) + "\n")

        # Trim entries older than TCS_HISTORY_RETENTION_DAYS so the file doesn't grow indefinitely
        cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=TCS_HISTORY_RETENTION_DAYS)
        try:
            with open(TCS_THRESHOLD_HISTORY_FILE) as _hf:
                raw_lines = _hf.readlines()
            kept = []
            for line in raw_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = _json.loads(line)
                    ts_str = entry.get("timestamp", "")
                    ts = _dt.datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
                    if ts >= cutoff:
                        kept.append(line)
                except Exception:
                    kept.append(line)
            with open(TCS_THRESHOLD_HISTORY_FILE, "w") as _hf:
                _hf.write("\n".join(kept) + ("\n" if kept else ""))
        except Exception:
            pass
    except Exception:
        pass

    _notify_tcs_threshold_shift(previous, current)


# ── TCS alert config: Supabase-backed with local JSON fallback ────────────────
#
# Required Supabase table — see migrations/create_app_config.sql.
# Run that script once in the Supabase SQL editor to create app_config:
#
#   CREATE TABLE IF NOT EXISTS app_config (
#       key        TEXT PRIMARY KEY,
#       value      JSONB NOT NULL,
#       updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
#   );
#
# The table stores arbitrary app-level config blobs keyed by a string.
# The TCS alert preferences are stored under key = 'tcs_alert_config'.

_APP_CONFIG_TABLE = "app_config"
_TCS_ALERT_CONFIG_KEY = "tcs_alert_config"
_TCS_ALERT_CFG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tcs_alert_config.json"
)

_IB_RANGE_PCT_CONFIG_KEY = "ib_range_pct_config"
_IB_RANGE_PCT_CFG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ib_range_pct_config.json"
)
_IB_RANGE_PCT_DEFAULT = 10.0


def _load_raw_tcs_alert_cfg() -> dict:
    """Return the parsed TCS alert config dict.

    Tries Supabase first.  Falls back to the local ``tcs_alert_config.json``
    file when Supabase is unavailable or the row doesn't exist yet.  Returns an
    empty dict when neither source has data.
    """
    import json as _json

    if supabase is not None:
        try:
            resp = (
                supabase.table(_APP_CONFIG_TABLE)
                .select("value")
                .eq("key", _TCS_ALERT_CONFIG_KEY)
                .limit(1)
                .execute()
            )
            if resp.data:
                val = resp.data[0].get("value")
                if isinstance(val, dict):
                    return val
        except Exception:
            pass

    if os.path.exists(_TCS_ALERT_CFG_PATH):
        try:
            with open(_TCS_ALERT_CFG_PATH) as _f:
                cfg = _json.load(_f)
            if isinstance(cfg, dict):
                return cfg
        except Exception:
            pass

    return {}


def _load_tcs_alert_structures() -> set | None:
    """Return the set of structure keys opted in for Telegram alerts.

    Reads config from Supabase (``app_config`` table, key ``tcs_alert_config``),
    falling back to ``tcs_alert_config.json`` when the DB is unavailable.

    Returns:
        * ``None``  – no config found or ``alert_structures`` key missing → alert
          on *all* structures (default / backwards-compatible behaviour).
        * A ``set`` of strings – alert only on those keys (may be empty, which
          silences every alert).
    """
    cfg = _load_raw_tcs_alert_cfg()
    if "alert_structures" in cfg:
        return set(cfg["alert_structures"])
    return None


def load_tcs_alert_structures() -> set | None:
    """Public wrapper around ``_load_tcs_alert_structures`` for use by the UI layer."""
    return _load_tcs_alert_structures()


def save_tcs_alert_structures(structures) -> bool:
    """Persist *structures* (any iterable of structure keys) durably.

    Writes to Supabase (``app_config`` table) as the primary store and also
    updates the local ``tcs_alert_config.json`` as a fallback cache.  Only
    recognised keys (those present in :data:`WK_DISPLAY`) are written; unknown
    keys are silently discarded.  Preserves any existing ``thresholds`` map.
    Returns ``True`` when at least one storage backend succeeds.
    """
    import json as _json

    valid = set(WK_DISPLAY.keys())
    sanitised = sorted(k for k in structures if k in valid)

    existing_thresholds: dict = {}
    try:
        existing_thresholds = _load_tcs_alert_thresholds()
    except Exception:
        pass

    db_value: dict = {"alert_structures": sanitised}
    if existing_thresholds:
        db_value["thresholds"] = existing_thresholds

    db_ok = False
    if supabase is not None:
        try:
            import datetime as _dt
            supabase.table(_APP_CONFIG_TABLE).upsert(
                {
                    "key": _TCS_ALERT_CONFIG_KEY,
                    "value": db_value,
                    "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                },
                on_conflict="key",
            ).execute()
            db_ok = True
        except Exception as _exc:
            logging.warning(
                "[save_tcs_alert_structures] Supabase write failed: %s. "
                "Will attempt local JSON fallback.",
                _exc,
            )

    file_cfg: dict = {
        "_comment": (
            "Controls which TCS structures trigger Telegram threshold-shift alerts and "
            "what delta (in points) must be exceeded before an alert fires. "
            "Remove a key from alert_structures to silence alerts for that structure. "
            "Set alert_structures to [] to silence ALL alerts, or delete this file to "
            "receive alerts for every structure (default). "
            "Use the optional thresholds map to set a per-structure minimum delta; "
            "structures not listed fall back to the global default of 5 points. "
            "Example: {\"alert_structures\": [\"trend_bull\", \"normal\"], "
            "\"thresholds\": {\"trend_bull\": 8, \"normal\": 3}}"
        ),
        "alert_structures": sanitised,
    }
    if existing_thresholds:
        file_cfg["thresholds"] = existing_thresholds

    file_ok = False
    try:
        with open(_TCS_ALERT_CFG_PATH, "w") as _f:
            _json.dump(file_cfg, _f, indent=2)
        file_ok = True
    except Exception as _exc:
        logging.warning(
            "[save_tcs_alert_structures] Local JSON write failed: %s.", _exc
        )

    if not db_ok and not file_ok:
        logging.error(
            "[save_tcs_alert_structures] Both Supabase and local file writes failed. "
            "Alert preferences were NOT persisted."
        )

    return db_ok or file_ok


_IB_RANGE_PCT_MIN = 1.0
_IB_RANGE_PCT_MAX = 50.0


def _clamp_ib_threshold(value) -> float | None:
    """Return *value* clamped to [_IB_RANGE_PCT_MIN, _IB_RANGE_PCT_MAX] or None if invalid."""
    if not isinstance(value, (int, float)):
        return None
    v = float(value)
    if not (0 < v):
        return None
    return max(_IB_RANGE_PCT_MIN, min(_IB_RANGE_PCT_MAX, v))


def load_ib_range_pct_threshold() -> float:
    """Return the IB range % filter threshold (default 10.0).

    Reads from the Supabase ``app_config`` table (key ``ib_range_pct_config``)
    first, then falls back to a local ``ib_range_pct_config.json`` file.
    Returns the compile-time default (10.0) when neither source has data.

    Values are clamped to [1.0, 50.0] so a manually corrupted config cannot
    break the dashboard ``st.number_input`` bounds or the bot filter.
    """
    import json as _json

    if supabase is not None:
        try:
            resp = (
                supabase.table(_APP_CONFIG_TABLE)
                .select("value")
                .eq("key", _IB_RANGE_PCT_CONFIG_KEY)
                .limit(1)
                .execute()
            )
            if resp.data:
                val = resp.data[0].get("value")
                if isinstance(val, dict) and "threshold" in val:
                    clamped = _clamp_ib_threshold(val["threshold"])
                    if clamped is not None:
                        return clamped
        except Exception:
            pass

    if os.path.exists(_IB_RANGE_PCT_CFG_PATH):
        try:
            with open(_IB_RANGE_PCT_CFG_PATH) as _f:
                cfg = _json.load(_f)
            if isinstance(cfg, dict) and "threshold" in cfg:
                clamped = _clamp_ib_threshold(cfg["threshold"])
                if clamped is not None:
                    return clamped
        except Exception:
            pass

    return _IB_RANGE_PCT_DEFAULT


def save_ib_range_pct_threshold(value: float) -> bool:
    """Persist *value* as the IB range % filter threshold.

    Writes to Supabase (``app_config`` table) and falls back to a local JSON
    file.  Returns ``True`` when at least one storage backend succeeds.
    """
    import json as _json
    import datetime as _dt

    if not isinstance(value, (int, float)) or value <= 0:
        logging.warning(
            "[save_ib_range_pct_threshold] Refusing to save non-positive value: %s", value
        )
        return False

    db_value = {"threshold": round(float(value), 4)}

    db_ok = False
    if supabase is not None:
        try:
            supabase.table(_APP_CONFIG_TABLE).upsert(
                {
                    "key": _IB_RANGE_PCT_CONFIG_KEY,
                    "value": db_value,
                    "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                },
                on_conflict="key",
            ).execute()
            db_ok = True
        except Exception as _exc:
            logging.warning(
                "[save_ib_range_pct_threshold] Supabase write failed: %s. "
                "Will attempt local JSON fallback.",
                _exc,
            )

    file_ok = False
    try:
        file_cfg = {"threshold": round(float(value), 4)}
        with open(_IB_RANGE_PCT_CFG_PATH, "w") as _f:
            _json.dump(file_cfg, _f, indent=2)
        file_ok = True
    except Exception as _exc:
        logging.warning(
            "[save_ib_range_pct_threshold] Local JSON write failed: %s.", _exc
        )

    if not db_ok and not file_ok:
        logging.error(
            "[save_ib_range_pct_threshold] Both Supabase and local file writes failed. "
            "IB range %% threshold was NOT persisted."
        )

    return db_ok or file_ok


def _load_tcs_alert_thresholds() -> dict:
    """Return a map of structure_key → minimum delta required to fire an alert.

    Reads from Supabase first, falling back to ``tcs_alert_config.json``.  Any
    structure *not* present in the map will use the global default of 5 points.
    Entries with non-numeric values are skipped silently.

    Returns an empty dict when neither source has threshold data.
    """
    cfg = _load_raw_tcs_alert_cfg()
    raw = cfg.get("thresholds", {})
    if not isinstance(raw, dict):
        return {}
    result: dict = {}
    for k, v in raw.items():
        try:
            result[str(k)] = float(v)
        except (TypeError, ValueError):
            logging.warning(
                "[_load_tcs_alert_thresholds] Skipping invalid threshold value for %r: %r",
                k, v,
            )
    return result


def load_tcs_alert_thresholds() -> dict:
    """Public wrapper around ``_load_tcs_alert_thresholds`` for use by the UI layer."""
    return _load_tcs_alert_thresholds()


def save_tcs_alert_thresholds(thresholds: dict) -> bool:
    """Persist per-structure delta *thresholds* durably.

    Writes to Supabase (``app_config`` table) as the primary store and also
    updates the local ``tcs_alert_config.json`` as a fallback cache.  Only
    recognised structure keys (those present in :data:`WK_DISPLAY`) are written;
    unknown keys are silently discarded.  Numeric values are coerced to
    ``float``; non-numeric values are skipped.  Preserves any existing
    ``alert_structures`` list.  Returns ``True`` when at least one storage
    backend succeeds.
    """
    import json as _json

    _THRESH_MIN = 0.5
    _THRESH_MAX = 50.0

    valid = set(WK_DISPLAY.keys())
    sanitised: dict = {}
    for k, v in thresholds.items():
        if k not in valid:
            continue
        try:
            clamped = max(_THRESH_MIN, min(_THRESH_MAX, float(v)))
            sanitised[k] = clamped
        except (TypeError, ValueError):
            logging.warning(
                "[save_tcs_alert_thresholds] Skipping non-numeric threshold for %r: %r",
                k, v,
            )

    # Preserve the tri-state semantics of alert_structures:
    #   None   → no explicit config → default (all structures enabled)
    #   set()  → explicit opt-in list saved by the user (may be empty)
    # We must NOT convert None into [] — that would silence all alerts.
    existing_set = None
    try:
        existing_set = _load_tcs_alert_structures()
    except Exception:
        pass

    db_value: dict = {}
    if existing_set is not None:
        db_value["alert_structures"] = sorted(existing_set)
    if sanitised:
        db_value["thresholds"] = sanitised

    db_ok = False
    if supabase is not None:
        try:
            import datetime as _dt
            supabase.table(_APP_CONFIG_TABLE).upsert(
                {
                    "key": _TCS_ALERT_CONFIG_KEY,
                    "value": db_value,
                    "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                },
                on_conflict="key",
            ).execute()
            db_ok = True
        except Exception as _exc:
            logging.warning(
                "[save_tcs_alert_thresholds] Supabase write failed: %s. "
                "Will attempt local JSON fallback.",
                _exc,
            )

    file_cfg: dict = {
        "_comment": (
            "Controls which TCS structures trigger Telegram threshold-shift alerts and "
            "what delta (in points) must be exceeded before an alert fires. "
            "Remove a key from alert_structures to silence alerts for that structure. "
            "Set alert_structures to [] to silence ALL alerts, or delete this file to "
            "receive alerts for every structure (default). "
            "Use the optional thresholds map to set a per-structure minimum delta; "
            "structures not listed fall back to the global default of 5 points. "
            "Example: {\"alert_structures\": [\"trend_bull\", \"normal\"], "
            "\"thresholds\": {\"trend_bull\": 8, \"normal\": 3}}"
        ),
    }
    if existing_set is not None:
        file_cfg["alert_structures"] = sorted(existing_set)
    if sanitised:
        file_cfg["thresholds"] = sanitised

    file_ok = False
    try:
        with open(_TCS_ALERT_CFG_PATH, "w") as _f:
            _json.dump(file_cfg, _f, indent=2)
        file_ok = True
    except Exception as _exc:
        logging.warning(
            "[save_tcs_alert_thresholds] Local JSON write failed: %s.", _exc
        )

    if not db_ok and not file_ok:
        logging.error(
            "[save_tcs_alert_thresholds] Both Supabase and local file writes failed. "
            "Threshold preferences were NOT persisted."
        )

    return db_ok or file_ok


def get_tcs_alert_config_last_saved() -> str | None:
    """Return a human-readable 'last saved' string for the TCS alert config.

    Checks Supabase ``app_config`` for the ``updated_at`` timestamp first;
    falls back to the local ``tcs_alert_config.json`` file mtime.  Returns
    ``None`` when no saved config can be found at all.

    The returned string is localised to the server's timezone.
    """
    import datetime as _dt

    if supabase is not None:
        try:
            resp = (
                supabase.table(_APP_CONFIG_TABLE)
                .select("updated_at")
                .eq("key", _TCS_ALERT_CONFIG_KEY)
                .limit(1)
                .execute()
            )
            if resp.data:
                raw_ts = resp.data[0].get("updated_at", "")
                if raw_ts:
                    parsed = _dt.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    local = parsed.astimezone().replace(tzinfo=None)
                    return local.strftime("%b %d, %Y at %I:%M %p") + " (database)"
        except Exception:
            pass

    if os.path.exists(_TCS_ALERT_CFG_PATH):
        try:
            mtime = os.path.getmtime(_TCS_ALERT_CFG_PATH)
            local = _dt.datetime.fromtimestamp(mtime)
            return local.strftime("%b %d, %Y at %I:%M %p") + " (local file)"
        except Exception:
            pass

    return None


_APP_CONFIG_DDL = (
    "CREATE TABLE IF NOT EXISTS app_config ("
    "key TEXT PRIMARY KEY, "
    "value JSONB NOT NULL, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    ");"
)


def _create_app_config_via_management_api() -> bool:
    """Attempt to create the ``app_config`` table via the Supabase Management API.

    Uses ``api.supabase.com/v1/projects/{ref}/database/query`` with a Supabase
    personal access token (``SUPABASE_ACCESS_TOKEN``) as the Bearer token.
    Returns ``True`` when the DDL statement is accepted **and** a subsequent
    probe confirms the table is now readable, ``False`` otherwise.

    This does NOT go through PostgREST — it calls the Supabase Management REST
    API which accepts arbitrary SQL and requires an account-level personal
    access token (PAT), not the project's anon/service-role key.
    Returns ``False`` immediately when ``SUPABASE_ACCESS_TOKEN`` is not set.
    """
    import re as _re2
    import urllib.request as _urllib_req
    import json as _json2

    if not SUPABASE_ACCESS_TOKEN:
        return False

    _ref_match = _re2.search(r"https://([a-z0-9]+)\.supabase\.co", SUPABASE_URL or "")
    if not _ref_match:
        return False
    _ref = _ref_match.group(1)
    _mgmt_url = f"https://api.supabase.com/v1/projects/{_ref}/database/query"
    _payload = _json2.dumps({"query": _APP_CONFIG_DDL}).encode()
    _req = _urllib_req.Request(
        _mgmt_url,
        data=_payload,
        headers={
            "Authorization": f"Bearer {SUPABASE_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with _urllib_req.urlopen(_req, timeout=10) as _resp:
            _status = _resp.status
        if _status not in (200, 201, 204):
            logging.debug(
                "[STARTUP] Management API returned HTTP %s while creating app_config table.",
                _status,
            )
            return False
    except Exception as _e:
        logging.debug(
            "[STARTUP] Management API call failed while creating app_config table: %s", _e
        )
        return False

    if supabase is None:
        return True
    try:
        supabase.table(_APP_CONFIG_TABLE).select("key").limit(1).execute()
        return True
    except Exception as _verify_exc:
        logging.debug(
            "[STARTUP] app_config table probe after creation failed: %s", _verify_exc
        )
        return False


def _ensure_app_config_table_exists() -> None:
    """Create the ``app_config`` Supabase table automatically on first startup.

    The table is required for durable alert preference storage.  If it is
    absent the code attempts to create it automatically via the Supabase
    Management API (``api.supabase.com``) using a personal access token
    (``SUPABASE_ACCESS_TOKEN``), which avoids any PostgREST limitation on
    DDL statements::

        CREATE TABLE IF NOT EXISTS app_config (
            key        TEXT PRIMARY KEY,
            value      JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

    Only acts when the error message contains PostgreSQL error code 42P01
    ("undefined_table") to avoid spurious errors during transient
    network/auth issues.  Falls back to a warning when
    ``SUPABASE_ACCESS_TOKEN`` is not set or the Management API call fails.

    Sets the module-level ``_app_config_table_status`` to one of:
    - ``"ok"``      — table already existed and is reachable
    - ``"created"`` — table was absent and was created successfully this run
    - ``"missing"`` — table is absent or unreachable (operators must act)
    """
    global _app_config_table_status
    if supabase is None:
        return
    try:
        supabase.table(_APP_CONFIG_TABLE).select("key").limit(1).execute()
        _app_config_table_status = "ok"
    except Exception as _exc:
        _is_missing_table = False
        _code = getattr(_exc, "code", None)
        if _code is not None:
            _is_missing_table = str(_code) == "42P01"
        if not _is_missing_table:
            _msg = str(_exc)
            _is_missing_table = (
                "42P01" in _msg
                or "undefined_table" in _msg
                or "does not exist" in _msg.lower()
            )
        if _is_missing_table:
            if not SUPABASE_ACCESS_TOKEN:
                logging.warning(
                    "[STARTUP] app_config table not found. "
                    "Set SUPABASE_ACCESS_TOKEN (Supabase Dashboard → Account → Access Tokens) "
                    "to allow automatic table creation on startup. "
                    "Alert preferences will fall back to tcs_alert_config.json until then. "
                    "Alternatively run this SQL in the Supabase SQL editor: %s",
                    _APP_CONFIG_DDL,
                )
                _app_config_table_status = "missing"
            else:
                logging.info(
                    "[STARTUP] app_config table not found — attempting to create it via "
                    "the Supabase Management API…"
                )
                if _create_app_config_via_management_api():
                    logging.info(
                        "[STARTUP] app_config table created and verified successfully via "
                        "the Supabase Management API."
                    )
                    _app_config_table_status = "created"
                else:
                    logging.warning(
                        "[STARTUP] app_config table not found and could not be created "
                        "automatically (Management API call failed or post-creation probe failed). "
                        "Alert preferences will fall back to tcs_alert_config.json. "
                        "Run the following SQL in the Supabase SQL editor to fix this: %s",
                        _APP_CONFIG_DDL,
                    )
                    _app_config_table_status = "missing"
        else:
            logging.debug(
                "[STARTUP] Could not verify app_config table existence (transient error): %s", _exc
            )
            _app_config_table_status = "missing"


_ensure_app_config_table_exists()
_write_health_file()

_TCS_ALERT_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tcs_alert_cache.json"
)

_LADDER_REFRESH_META_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ladder_refresh_meta.json"
)


def _load_tcs_alert_cache() -> dict:
    """Load the deduplication cache from disk, discarding entries from past UTC days."""
    import json as _json
    import datetime as _dt

    today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    try:
        with open(_TCS_ALERT_CACHE_FILE) as _f:
            raw: dict = _json.load(_f)
        return {k: v for k, v in raw.items() if k.endswith(today)}
    except FileNotFoundError:
        return {}
    except Exception as _exc:
        logging.warning(
            "[TCS] Failed to read alert cache from %s: %s — deduplication guard reset, duplicate alerts may fire",
            _TCS_ALERT_CACHE_FILE,
            _exc,
        )
        return {}


def _save_tcs_alert_cache(cache: dict) -> None:
    """Persist the deduplication cache to disk."""
    import json as _json

    try:
        with open(_TCS_ALERT_CACHE_FILE, "w") as _f:
            _json.dump(cache, _f)
    except Exception as _exc:
        logging.warning(
            "[TCS] Failed to write alert cache to %s: %s — duplicate-alert guard may not persist across restarts",
            _TCS_ALERT_CACHE_FILE,
            _exc,
        )


_tcs_alert_cache: dict = _load_tcs_alert_cache()  # {structure_YYYY-MM-DD: True}


def _notify_tcs_threshold_shift(previous: dict, current: dict) -> None:
    """Send a Telegram alert for any TCS structure whose threshold moved enough.

    The minimum delta required before an alert fires is controlled per-structure
    via the optional ``thresholds`` map in ``tcs_alert_config.json``.  Any
    structure not listed there falls back to the global default of 5 points.

    Only fires when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are configured.
    Silently skips if nothing changed significantly or credentials are absent.

    Which structures trigger an alert is controlled by ``tcs_alert_config.json``
    in the project root.  When the file is absent (or lacks the
    ``alert_structures`` key) every structure is eligible — preserving the
    original behaviour.  Set ``alert_structures`` to a subset of structure
    keys to receive alerts only for those; an empty list silences all alerts.

    Duplicate alerts for the same structure on the same UTC day are suppressed
    via ``_tcs_alert_cache`` so that re-runs of recalibration do not spam
    traders with identical notifications.
    """
    import os as _os
    import requests as _req
    import datetime as _dt

    _token   = _os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    _chat_id = _os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not _token or not _chat_id:
        return

    opted_in   = _load_tcs_alert_structures()
    thresholds = _load_tcs_alert_thresholds()

    _DEFAULT_THRESHOLD = 5
    _today = _dt.datetime.utcnow().strftime("%Y-%m-%d")

    lines = []
    alerted_keys = []
    all_keys = set(list(previous.keys()) + list(current.keys()))
    for wk in sorted(all_keys):
        if opted_in is not None and wk not in opted_in:
            continue
        old_val = previous.get(wk)
        new_val = current.get(wk)
        if old_val is None or new_val is None:
            continue
        delta = new_val - old_val
        _threshold = thresholds.get(wk, _DEFAULT_THRESHOLD)
        if abs(delta) < _threshold:
            continue
        _cache_key = f"{wk}_{_today}"
        if _cache_key in _tcs_alert_cache:
            continue
        arrow   = "↑" if delta > 0 else "↓"
        label   = "stricter" if delta > 0 else "looser"
        display = wk.replace("_", " ").title()
        lines.append(f"  • {display}: {old_val} → {new_val} {arrow} ({label})")
        alerted_keys.append(_cache_key)

    if not lines:
        return

    _date_str = _dt.datetime.utcnow().strftime("%b %d, %Y %H:%M UTC")
    shifts    = "\n".join(lines)
    msg = (
        f"⚙️ <b>TCS Threshold Shift Detected</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{shifts}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {_date_str}\n"
        f"Recalibrated automatically — review before market open."
    )
    import logging as _logging
    import time as _time

    def _send_one(chat_id):
        try:
            _resp = _req.post(
                f"https://api.telegram.org/bot{_token}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                timeout=8,
            )
            if _resp.status_code != 200:
                _logging.getLogger(__name__).warning(
                    "TCS threshold shift Telegram alert failed (chat %s): %s %s",
                    chat_id,
                    _resp.status_code,
                    _resp.text[:120],
                )
        except Exception as _exc:
            _logging.getLogger(__name__).warning(
                "TCS threshold shift Telegram alert error (chat %s): %s", chat_id, _exc
            )

    # Always notify the main admin chat
    _send_one(_chat_id)

    # Mark alerted structures as sent for today so that any re-run of
    # recalibration within the same UTC day does not fire duplicate alerts.
    for _k in alerted_keys:
        _tcs_alert_cache[_k] = True
    for _k in [k for k in list(_tcs_alert_cache) if not k.endswith(_today)]:
        _tcs_alert_cache.pop(_k, None)
    _save_tcs_alert_cache(_tcs_alert_cache)

    # Also broadcast to all beta subscribers, excluding the owner (who already
    # received the message via the main TELEGRAM_CHAT_ID above).
    # Only subscribers who have not opted out of TCS alerts are included.
    _owner_id = _os.environ.get("PAPER_TRADE_USER_ID", "").strip()
    try:
        _pairs = get_beta_chat_ids(exclude_user_id=_owner_id, tcs_alerts_only=True)
        for _uid, _sub_chat_id in _pairs:
            if str(_sub_chat_id) == str(_chat_id):
                continue  # extra guard: skip if chat_id matches main chat
            _send_one(_sub_chat_id)
            _time.sleep(0.1)
    except Exception as _exc:
        _logging.getLogger(__name__).warning(
            "TCS threshold shift subscriber broadcast error: %s", _exc
        )


def load_tcs_threshold_history(days: int = 14) -> list:
    """Load recent TCS threshold history from tcs_threshold_history.jsonl.

    Returns a list of records (dicts) from the last *days* calendar days,
    oldest first.  Each record has keys:
      timestamp (str ISO-8601), thresholds (dict), previous (dict)

    Returns an empty list if the file doesn't exist or can't be read.
    """
    import json as _json
    import datetime as _dt
    if not os.path.exists(TCS_THRESHOLD_HISTORY_FILE):
        return []
    cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=days)
    records: list = []
    try:
        with open(TCS_THRESHOLD_HISTORY_FILE) as _hf:
            for line in _hf:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                    ts_str = rec.get("timestamp", "")
                    # Normalize Z-suffix so fromisoformat accepts it (Python <3.11)
                    ts = _dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
                    if ts >= cutoff:
                        records.append(rec)
                except Exception:
                    continue
    except Exception:
        return []
    return records


def load_tcs_thresholds(default: int = 50) -> dict:
    """Load per-structure TCS thresholds from tcs_thresholds.json.

    Returns dict keyed by weight_key with int TCS values.
    Falls back to default (50) for any missing key or if file doesn't exist.
    """
    import json as _json
    defaults = {k: default for k in _BRAIN_WEIGHT_KEYS}
    if not os.path.exists(TCS_THRESHOLDS_FILE):
        return defaults
    try:
        with open(TCS_THRESHOLDS_FILE) as f:
            stored = _json.load(f)
        return {k: int(stored.get(k, default)) for k in _BRAIN_WEIGHT_KEYS}
    except Exception:
        return defaults


def recalibrate_from_history(user_id: str = "") -> dict:
    """Calibrate the HISTORICAL brain using backtest_sim_runs (11,000+ rows).

    This is a SEPARATE brain from the live personal brain (brain_weights.json).
    It learns statistical priors for all 7 structure types from years of historical
    data so that structures with zero live paper trades still get calibrated weights.

    Learning rate: standard EMA (same as live brain) — the data is large and real,
    so no artificial cap is needed. With 1,000+ samples per structure, the EMA will
    converge to a stable prior quickly.

    Returns same shape as recalibrate_from_supabase() for easy comparison display.
    """
    import collections as _col

    weights = load_historical_brain_weights()
    result = {
        "weights":    weights,
        "deltas":     [],
        "sources":    {"backtest_sim_runs": 0, "total": 0},
        "calibrated": False,
        "timestamp":  datetime.now(EASTERN).isoformat(),
    }

    if not supabase:
        return result

    hist_data: dict = _col.defaultdict(lambda: {"wins": 0, "total": 0})

    try:
        # Pull all resolved backtest rows — paginate to get all 11,000+
        all_rows = []
        page_size = 1000
        offset = 0
        while True:
            q = supabase.table("backtest_sim_runs").select("predicted,win_loss")
            if user_id:
                q = q.eq("user_id", user_id)
            q = q.in_("win_loss", ["Win", "Loss"]).range(offset, offset + page_size - 1)
            batch = q.execute().data or []
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        for r in all_rows:
            pred = str(r.get("predicted", "") or "").strip()
            wl   = str(r.get("win_loss",  "") or "").strip().lower()
            if not pred or wl not in ("win", "loss"):
                continue
            wk = _label_to_weight_key(pred)
            hist_data[wk]["total"] += 1
            if wl == "win":
                hist_data[wk]["wins"] += 1

        result["sources"]["backtest_sim_runs"] = len(all_rows)
        result["sources"]["total"] = len(all_rows)
    except Exception as e:
        print(f"recalibrate_from_history: backtest_sim_runs error: {e}")

    if not hist_data:
        return result

    deltas = []
    for wk, h in hist_data.items():
        h_n = h["total"]
        if h_n < 5:
            continue

        h_acc = h["wins"] / h_n

        if   h_acc >= 0.70: target = 1.50
        elif h_acc >= 0.50: target = 1.00
        elif h_acc >= 0.30: target = 0.75
        else:               target = 0.50

        # EMA rate — same scale as live brain (large N → fast convergence)
        if   h_n >= 100: ema_rate = 0.40
        elif h_n >=  50: ema_rate = 0.35
        elif h_n >=  25: ema_rate = 0.25
        elif h_n >=  10: ema_rate = 0.15
        else:            ema_rate = 0.10

        old_val     = weights.get(wk, 1.0)
        new_val     = round(old_val * (1 - ema_rate) + target * ema_rate, 4)
        weights[wk] = new_val

        deltas.append({
            "key":        wk,
            "old":        round(old_val, 4),
            "new":        new_val,
            "delta":      round(new_val - old_val, 4),
            "hist_acc":   round(h_acc * 100, 1),
            "hist_n":     h_n,
            "ema_rate":   ema_rate,
            "target":     target,
        })

    if deltas:
        _save_historical_brain_weights(weights)
        result["calibrated"] = True

    # After historical-brain calibration, persist fresh TCS thresholds
    # so the bot can use per-structure cutoffs at scan time.
    try:
        save_tcs_thresholds(compute_structure_tcs_thresholds())
    except Exception:
        pass

    result["weights"] = weights
    result["deltas"]  = sorted(deltas, key=lambda x: abs(x["delta"]), reverse=True)
    return result


def blend_brain_weights(
    live_weights: dict,
    hist_weights: dict,
    live_n: int,
    hist_n: int,
) -> dict:
    """Volume-weighted blend of live personal brain + historical brain.

    As live data grows, the live brain earns more influence automatically.
    With 0 live trades: 100% historical prior.
    With 300 live vs 11,000 hist: ~2.7% live, 97.3% historical.
    With 3,000 live vs 11,000 hist: ~21% live, 79% historical.

    This means the historical brain anchors early signal quality while
    the live brain gradually earns dominance through real personal data.
    """
    total = live_n + hist_n
    if total == 0:
        return {k: 1.0 for k in _BRAIN_WEIGHT_KEYS}

    live_ratio = live_n / total
    hist_ratio = hist_n / total

    blended = {}
    for k in _BRAIN_WEIGHT_KEYS:
        lw = live_weights.get(k, 1.0)
        hw = hist_weights.get(k, 1.0)
        blended[k] = round(live_ratio * lw + hist_ratio * hw, 4)
    return blended


def compute_structure_tcs_thresholds() -> list[dict]:
    """Compute per-structure TCS thresholds based on actual hit rates.

    Logic:
      - Pulls per-structure accuracy from accuracy_tracker + paper_trades + backtest_sim_runs
      - Higher hit rate → lower TCS threshold (take these trades more aggressively)
      - Lower hit rate → higher TCS threshold (require more confirmation)

    Threshold formula:
      base_tcs = TCS_BASE_SCORE (default 65; override via TCS_BASE_SCORE env var)
      adjustment = (hit_rate - 60) * 0.5
      threshold = base_tcs - adjustment
      Clamped to [45, 85] range

    So a structure with 85% hit rate → threshold ~52 (take it easier)
       a structure with 40% hit rate → threshold ~75 (need strong confluence)

    Returns list of dicts sorted by recommended threshold (lowest first = strongest edge):
      [{structure, hit_rate, sample_count, journal_n, bot_n, brain_weight,
        recommended_tcs, confidence, status}, ...]
    """
    if not supabase:
        return []

    import collections as _col

    journal_data: dict = _col.defaultdict(lambda: {"wins": 0, "total": 0})
    bot_data:     dict = _col.defaultdict(lambda: {"wins": 0, "total": 0})
    hist_data:    dict = _col.defaultdict(lambda: {"wins": 0, "total": 0})

    # Source 1: accuracy_tracker (journal / manual)
    try:
        q = supabase.table("accuracy_tracker").select("predicted,correct").range(0, 9999)
        for row in (q.execute().data or []):
            pred = row.get("predicted", "")
            corr = row.get("correct", "")
            if not pred:
                continue
            wk = _label_to_weight_key(pred)
            if not wk:
                continue
            journal_data[wk]["total"] += 1
            if "✅" in str(corr):
                journal_data[wk]["wins"] += 1
    except Exception:
        pass

    # Source 2: paper_trades (bot automated signals)
    try:
        q = supabase.table("paper_trades").select("predicted,win_loss").range(0, 9999)
        for row in (q.execute().data or []):
            pred = row.get("predicted", "")
            wl   = str(row.get("win_loss", "")).strip().lower()
            if not pred or wl not in ("win", "loss"):
                continue
            wk = _label_to_weight_key(pred)
            if not wk:
                continue
            bot_data[wk]["total"] += 1
            if wl == "win":
                bot_data[wk]["wins"] += 1
    except Exception:
        pass

    # Source 3: backtest_sim_runs (historical prior — paginated)
    try:
        page_size = 1000
        offset    = 0
        while True:
            q = (
                supabase.table("backtest_sim_runs")
                .select("predicted,win_loss")
                .in_("win_loss", ["Win", "Loss"])
                .range(offset, offset + page_size - 1)
            )
            batch = q.execute().data or []
            for row in batch:
                pred = str(row.get("predicted", "") or "").strip()
                wl   = str(row.get("win_loss",  "") or "").strip().lower()
                if not pred or wl not in ("win", "loss"):
                    continue
                wk = _label_to_weight_key(pred)
                if not wk:
                    continue
                hist_data[wk]["total"] += 1
                if wl == "win":
                    hist_data[wk]["wins"] += 1
            if len(batch) < page_size:
                break
            offset += page_size
    except Exception:
        pass

    # Load BLENDED brain weights (live personal + historical prior)
    live_weights = load_brain_weights()
    hist_weights = load_historical_brain_weights()
    live_n_total = sum(d["total"] for d in journal_data.values()) + sum(d["total"] for d in bot_data.values())
    hist_n_total = sum(d["total"] for d in hist_data.values())
    blended_weights = blend_brain_weights(live_weights, hist_weights, live_n_total, hist_n_total)

    STRUCTURE_LABELS = {
        "neutral":      "🔄 Neutral",
        "ntrl_extreme": "⚡ Neutral Extreme",
        "normal":       "📊 Normal",
        "trend_bull":   "🚀 Trend Day",
        "double_dist":  "📉 Double Distribution",
        "non_trend":    "🔃 Rotational",
        "nrml_variation": "❓ Other",
    }

    BASE_TCS = TCS_BASE_SCORE
    results = []

    for wk, label in STRUCTURE_LABELS.items():
        j   = journal_data[wk]
        b   = bot_data[wk]
        h   = hist_data[wk]
        j_n = j["total"]
        b_n = b["total"]
        h_n = h["total"]
        live_n = j_n + b_n
        total_n = live_n + h_n

        if total_n == 0:
            results.append({
                "wk":                    wk,
                "structure":             label,
                "hit_rate":              None,
                "sample_count":          0,
                "journal_n":             0,
                "bot_n":                 0,
                "historical_n":          0,
                "historical_n_raw":      0,
                "brain_weight":          blended_weights.get(wk, 1.0),
                "recommended_tcs":       BASE_TCS,
                "confidence":            "No Data",
                "status":                "⏳",
            })
            continue

        # Per-source accuracy
        j_acc = (j["wins"] / j_n) if j_n > 0 else None
        b_acc = (b["wins"] / b_n) if b_n > 0 else None
        h_acc = (h["wins"] / h_n) if h_n > 0 else None

        # Graduated live-first blend:
        #   - Below _LIVE_MIN_OVERRIDE trades: live sample too thin → backtest dominates fully (no cap)
        #   - At or above _LIVE_MIN_OVERRIDE trades: live is statistically meaningful → backtest capped at 2× live
        # This prevents a handful of live trades from overriding thousands of validated backtest records,
        # while still ensuring live data takes over once it reaches a meaningful sample size.
        _HIST_CAP_MULT     = 2.0   # once live ≥ threshold, backtest capped at 2× live
        _LIVE_MIN_OVERRIDE = 30    # minimum live trades before live overrides backtest
        if h_n > 0:
            if live_n >= _LIVE_MIN_OVERRIDE:
                h_eff_n = min(h_n, int(live_n * _HIST_CAP_MULT))
            else:
                h_eff_n = h_n   # live too thin — backtest dominates as prior
        else:
            h_eff_n = 0

        numerator   = 0.0
        denominator = 0.0
        if j_n > 0 and j_acc is not None:
            numerator   += j_n * j_acc
            denominator += j_n
        if b_n > 0 and b_acc is not None:
            numerator   += b_n * b_acc
            denominator += b_n
        if h_eff_n > 0 and h_acc is not None:
            numerator   += h_eff_n * h_acc
            denominator += h_eff_n

        hit_rate = (numerator / denominator) if denominator > 0 else 0.5
        hit_pct  = hit_rate * 100

        adjustment = (hit_pct - 60) * 0.5
        rec_tcs    = round(max(45, min(85, BASE_TCS - adjustment)))

        # Confidence based on total sample count
        if total_n >= 100:
            conf = "High"
        elif total_n >= 30:
            conf = "Medium"
        elif total_n >= 10:
            conf = "Low"
        else:
            conf = "Very Low"

        if hit_pct >= 70:   status = "🟢"
        elif hit_pct >= 55: status = "🟡"
        elif hit_pct >= 40: status = "🟠"
        else:               status = "🔴"

        results.append({
            "wk":                    wk,
            "structure":             label,
            "hit_rate":              round(hit_pct, 1),
            "sample_count":          j_n + b_n + h_eff_n,  # effective total used in blend
            "journal_n":             j_n,
            "bot_n":                 b_n,
            "historical_n":          h_eff_n,   # capped effective count used in blend
            "historical_n_raw":      h_n,       # actual backtest records available
            "brain_weight":          round(blended_weights.get(wk, 1.0), 4),
            "recommended_tcs":       rec_tcs,
            "confidence":            conf,
            "status":                status,
        })

    results.sort(key=lambda x: x["recommended_tcs"])
    return results


def brain_weights_summary(user_id: str = "") -> list[dict]:
    """Return a list of dicts for displaying the learned weight table."""
    weights  = load_brain_weights(user_id)
    if not os.path.exists(TRACKER_FILE):
        return []
    try:
        df = pd.read_csv(TRACKER_FILE)
        if df.empty or "predicted" not in df.columns:
            return []
        rows = []
        for raw_label, grp in df.groupby("predicted"):
            wk   = _label_to_weight_key(str(raw_label))
            n    = len(grp)
            acc  = (grp["correct"] == "✅").sum() / n if n > 0 else 0
            w    = weights.get(wk, 1.0)
            rows.append({
                "Structure":  raw_label,
                "Samples":    n,
                "Accuracy":   round(acc * 100, 1),
                "Multiplier": w,
                "Status": ("✅ Trusted" if w >= 1.3 else
                           "🟢 Good"    if w >= 1.0 else
                           "🟡 Reduced" if w >= 0.7 else
                           "🔴 Low Confidence"),
            })
        rows.sort(key=lambda r: r["Multiplier"], reverse=True)
        return rows
    except Exception:
        return []


# ── Predictive probability engine (signal conditions + outcomes) ──────────────
_SIGNAL_CONDITIONS_FILE = ".local/signal_conditions.json"
_SIGNAL_OUTCOMES_FILE   = ".local/signal_outcomes.json"


def _edge_band(score: float) -> str:
    if score >= 75:   return "75+"
    if score >= 65:   return "65-75"
    if score >= 50:   return "50-65"
    return "<50"


def _rvol_band(rvol: float) -> str:
    if rvol >= 3:  return "3+"
    if rvol >= 2:  return "2-3"
    if rvol >= 1:  return "1-2"
    return "<1"


def save_signal_conditions(user_id: str, ticker: str, trade_date,
                           edge_score: float, rvol: float, structure: str,
                           tcs: float = 0.0, buy_pressure: float = 0.0) -> None:
    """Store signal conditions at analysis time so they can be paired with outcomes later.

    Called from the Main Chart tab every time a full analysis runs.
    Keyed by user_id + ticker + date so repeated analyses on the same day overwrite.
    """
    import json as _json
    key = f"{user_id}_{ticker.upper()}_{str(trade_date)}"
    entry = {
        "ticker":       ticker.upper(),
        "date":         str(trade_date),
        "user_id":      user_id,
        "edge_score":   round(float(edge_score), 1),
        "edge_band":    _edge_band(float(edge_score)),
        "rvol":         round(float(rvol), 2),
        "rvol_band":    _rvol_band(float(rvol)),
        "structure":    str(structure),
        "tcs":          round(float(tcs), 1),
        "buy_pressure": round(float(buy_pressure), 1),
        "saved_at":     datetime.utcnow().isoformat(),
    }
    try:
        data: dict = {}
        os.makedirs(".local", exist_ok=True)
        if os.path.exists(_SIGNAL_CONDITIONS_FILE):
            with open(_SIGNAL_CONDITIONS_FILE) as _f:
                data = _json.load(_f)
        data[key] = entry
        with open(_SIGNAL_CONDITIONS_FILE, "w") as _f:
            _json.dump(data, _f)
    except Exception:
        pass


def get_signal_conditions(user_id: str, ticker: str, trade_date) -> dict:
    """Retrieve stored signal conditions for a specific user+ticker+date."""
    import json as _json
    key = f"{user_id}_{ticker.upper()}_{str(trade_date)}"
    try:
        if os.path.exists(_SIGNAL_CONDITIONS_FILE):
            with open(_SIGNAL_CONDITIONS_FILE) as _f:
                data = _json.load(_f)
            return data.get(key, {})
    except Exception:
        pass
    return {}


def log_signal_outcome(user_id: str, ticker: str, trade_date,
                       outcome_win: bool, outcome_pct: float = 0.0) -> None:
    """Pair stored signal conditions with a verified outcome.

    Called when the user marks a prediction correct/wrong in the EOD review.
    Deduplicates by user+ticker+date so re-marking updates the record.
    """
    import json as _json
    conditions = get_signal_conditions(user_id, ticker, str(trade_date))
    edge  = conditions.get("edge_score", 0.0)
    rvol  = conditions.get("rvol", 0.0)
    struct = conditions.get("structure", "Unknown")
    tcs   = conditions.get("tcs", 0.0)

    entry = {
        "user_id":      user_id,
        "ticker":       ticker.upper(),
        "date":         str(trade_date),
        "edge_score":   float(conditions.get("edge_score", edge)),
        "edge_band":    _edge_band(float(edge)),
        "rvol":         float(rvol),
        "rvol_band":    _rvol_band(float(rvol)),
        "structure":    str(struct),
        "tcs":          float(tcs),
        "buy_pressure": float(conditions.get("buy_pressure", 0.0)),
        "outcome_win":  bool(outcome_win),
        "outcome_pct":  round(float(outcome_pct), 2),
        "logged_at":    datetime.utcnow().isoformat(),
    }
    try:
        os.makedirs(".local", exist_ok=True)
        outcomes: list = []
        if os.path.exists(_SIGNAL_OUTCOMES_FILE):
            with open(_SIGNAL_OUTCOMES_FILE) as _f:
                outcomes = _json.load(_f)
        outcomes = [o for o in outcomes if not (
            o.get("user_id") == user_id and
            o.get("ticker")  == ticker.upper() and
            o.get("date")    == str(trade_date)
        )]
        outcomes.append(entry)
        with open(_SIGNAL_OUTCOMES_FILE, "w") as _f:
            _json.dump(outcomes, _f)
    except Exception:
        pass


def compute_win_rates(user_id: str, min_samples: int = 3) -> dict:
    """Compute historical win rates grouped by condition cluster from logged outcomes.

    Returns a dict with three sub-keys:
      "_total"    : {"n": ..., "win_rate": ...}
      "_by_edge"  : {band: {"n": ..., "win_rate": ...}, ...}
      "_by_struct": {structure: {"n": ..., "win_rate": ..., "avg_pct": ...}, ...}
      <cluster>   : {"n": ..., "wins": ..., "win_rate": ..., "avg_pct": ..., "sufficient": bool}
                    where <cluster> = "edge:<band> rvol:<band> struct:<structure>"
    """
    import json as _json
    from collections import defaultdict
    try:
        if not os.path.exists(_SIGNAL_OUTCOMES_FILE):
            return {}
        with open(_SIGNAL_OUTCOMES_FILE) as _f:
            all_outcomes = _json.load(_f)
        outcomes = [o for o in all_outcomes if o.get("user_id") == user_id]
        if not outcomes:
            return {}

        result: dict = {}

        # Full cluster grouping
        clusters: dict = defaultdict(list)
        for o in outcomes:
            k = (f"edge:{o.get('edge_band','?')} "
                 f"rvol:{o.get('rvol_band','?')} "
                 f"struct:{o.get('structure','?')}")
            clusters[k].append(o)
        for k, grp in clusters.items():
            n    = len(grp)
            wins = sum(1 for o in grp if o.get("outcome_win"))
            avg  = (sum(o.get("outcome_pct", 0) for o in grp) / n) if n else 0
            result[k] = {
                "n":          n,
                "wins":       wins,
                "win_rate":   round(wins / n, 3) if n else 0,
                "avg_pct":    round(avg, 2),
                "sufficient": n >= min_samples,
            }

        # By edge band
        by_edge: dict = defaultdict(list)
        for o in outcomes:
            by_edge[o.get("edge_band", "?")].append(o)
        result["_by_edge"] = {
            band: {
                "n":        len(g),
                "win_rate": round(sum(1 for o in g if o.get("outcome_win")) / len(g), 3),
            }
            for band, g in by_edge.items() if g
        }

        # By structure
        by_struct: dict = defaultdict(list)
        for o in outcomes:
            by_struct[o.get("structure", "?")].append(o)
        result["_by_struct"] = {}
        for struct, grp in by_struct.items():
            n    = len(grp)
            wins = sum(1 for o in grp if o.get("outcome_win"))
            avg  = (sum(o.get("outcome_pct", 0) for o in grp) / n) if n else 0
            result["_by_struct"][struct] = {
                "n":        n,
                "win_rate": round(wins / n, 3) if n else 0,
                "avg_pct":  round(avg, 2),
            }

        # Overall
        n_total = len(outcomes)
        result["_total"] = {
            "n":        n_total,
            "win_rate": round(
                sum(1 for o in outcomes if o.get("outcome_win")) / n_total, 3
            ) if n_total else 0,
        }
        return result
    except Exception:
        return {}


def get_predictive_context(user_id: str, edge_score: float,
                           rvol: float, structure: str) -> dict:
    """Return historical win-rate context for the current signal conditions.

    Tries exact cluster match first; falls back to edge-band and overall.
    Returns empty dict if no signal log exists yet.
    """
    rates = compute_win_rates(user_id, min_samples=3)
    if not rates:
        return {}

    cluster_key = (f"edge:{_edge_band(edge_score)} "
                   f"rvol:{_rvol_band(rvol)} "
                   f"struct:{structure}")
    exact      = rates.get(cluster_key, {})
    by_edge    = rates.get("_by_edge", {}).get(_edge_band(edge_score), {})
    by_struct  = rates.get("_by_struct", {}).get(structure, {})
    overall    = rates.get("_total", {})

    return {
        "cluster_key": cluster_key,
        "exact":       exact if exact.get("sufficient") else {},
        "by_edge":     by_edge,
        "by_struct":   by_struct,
        "overall":     overall,
    }


# ── Monte Carlo equity simulation ─────────────────────────────────────────────

def monte_carlo_equity_curves(
    trade_results: list,
    starting_equity: float = 10_000.0,
    n_simulations: int = 1_000,
    risk_pct: float = 0.02,
    slippage_drag_pct: float = 0.0,
) -> dict:
    """Simulate N equity curves by randomly reshuffling the trade sequence.

    Each trade risks `risk_pct` of current equity.  A win grows equity by
    (risk_pct × |aft_move_pct| / 100) and a loss shrinks it by risk_pct.
    slippage_drag_pct is subtracted from every trade (win or lose).

    Returns P10 / P50 / P90 equity curves and final-equity distribution stats.
    Empty dict if fewer than 3 trades.
    """
    import random
    import numpy as np

    outcomes = []
    for r in trade_results:
        move = r.get("aft_move_pct", 0.0)
        win  = r.get("win_loss", "") == "Win"
        ret  = (risk_pct * (abs(move) / 100.0) if win else -risk_pct) - slippage_drag_pct
        outcomes.append(float(ret))

    if len(outcomes) < 3:
        return {}

    random.seed(42)
    all_curves   = []
    final_equities = []

    for _ in range(n_simulations):
        shuffled = outcomes.copy()
        random.shuffle(shuffled)
        equity = starting_equity
        curve  = [equity]
        for ret in shuffled:
            equity = max(0.01, equity * (1.0 + ret))
            curve.append(equity)
        all_curves.append(curve)
        final_equities.append(equity)

    arr  = np.array(all_curves)
    p10  = np.percentile(arr, 10, axis=0).tolist()
    p50  = np.percentile(arr, 50, axis=0).tolist()
    p90  = np.percentile(arr, 90, axis=0).tolist()

    final_equities.sort()
    profitable = sum(1 for e in final_equities if e > starting_equity)

    return {
        "p10":            p10,
        "p50":            p50,
        "p90":            p90,
        "final_equities": final_equities,
        "pct_profitable": round(profitable / len(final_equities) * 100, 1),
        "median_final":   round(float(np.percentile(final_equities, 50)), 2),
        "p10_final":      round(float(np.percentile(final_equities, 10)), 2),
        "p90_final":      round(float(np.percentile(final_equities, 90)), 2),
        "n_trades":       len(outcomes),
        "n_simulations":  n_simulations,
        "starting":       starting_equity,
    }


def monte_carlo_from_r_series(
    pnl_r_list: list,
    starting_equity: float = 25_000.0,
    n_simulations: int = 1_000,
    risk_pct: float = 0.01,
) -> dict:
    """Monte Carlo equity simulation using a direct R-multiple series.

    Unlike monte_carlo_equity_curves(), which requires aft_move_pct + win_loss
    and assumes ±1R outcomes, this function accepts pnl_r_sim values directly
    (e.g., +1.5R, -1.0R, +0.75R) as produced by the v5 trailing-stop simulator.

    Each trade compounds as: equity *= (1 + risk_pct * r) where r is pnl_r_sim.

    Returns the same dict schema as monte_carlo_equity_curves():
    p10, p50, p90, final_equities, pct_profitable, median_final, p10_final,
    p90_final, n_trades, n_simulations, starting.
    """
    import random
    import numpy as np

    outcomes = [float(r) for r in pnl_r_list if r is not None]
    if len(outcomes) < 3:
        return {}

    random.seed(42)
    all_curves     = []
    final_equities = []

    for _ in range(n_simulations):
        shuffled = outcomes.copy()
        random.shuffle(shuffled)
        equity = starting_equity
        curve  = [equity]
        for r in shuffled:
            equity = max(0.01, equity * (1.0 + risk_pct * r))
            curve.append(equity)
        all_curves.append(curve)
        final_equities.append(equity)

    arr  = np.array(all_curves)
    p10  = np.percentile(arr, 10, axis=0).tolist()
    p50  = np.percentile(arr, 50, axis=0).tolist()
    p90  = np.percentile(arr, 90, axis=0).tolist()

    final_equities.sort()
    profitable = sum(1 for e in final_equities if e > starting_equity)

    return {
        "p10":            p10,
        "p50":            p50,
        "p90":            p90,
        "final_equities": final_equities,
        "pct_profitable": round(profitable / len(final_equities) * 100, 1),
        "median_final":   round(float(np.percentile(final_equities, 50)), 2),
        "p10_final":      round(float(np.percentile(final_equities, 10)), 2),
        "p90_final":      round(float(np.percentile(final_equities, 90)), 2),
        "n_trades":       len(outcomes),
        "n_simulations":  n_simulations,
        "starting":       starting_equity,
    }


# ── Position state persistence ────────────────────────────────────────────────

def load_position_state():
    """Load persisted position from trade_state.json into session state."""
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        for k in ("position_in", "position_avg_entry", "position_peak_price",
                  "position_ticker", "position_shares", "position_structure"):
            if k in data:
                st.session_state[k] = data[k]
    except Exception:
        pass


def save_position_state():
    """Persist current position session state to trade_state.json."""
    data = {k: st.session_state.get(k)
            for k in ("position_in", "position_avg_entry", "position_peak_price",
                      "position_ticker", "position_shares", "position_structure")}
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)


def enter_position(ticker, avg_entry, shares, structure):
    st.session_state.position_in        = True
    st.session_state.position_avg_entry = float(avg_entry)
    st.session_state.position_peak_price = float(avg_entry)
    st.session_state.position_ticker    = ticker
    st.session_state.position_shares    = int(shares)
    st.session_state.position_structure = structure
    save_position_state()


def exit_position(exit_price, actual_structure=""):
    """Record the exit, log to accuracy tracker, clear position."""
    entry   = st.session_state.position_avg_entry
    mfe     = st.session_state.position_peak_price
    sym     = st.session_state.position_ticker
    pred    = st.session_state.position_structure
    shares  = st.session_state.position_shares
    if entry > 0:
        log_accuracy_entry(sym, pred, actual_structure or pred,
                           entry_price=entry, exit_price=float(exit_price), mfe=mfe)
    st.session_state.position_in        = False
    st.session_state.position_avg_entry = 0.0
    st.session_state.position_peak_price = 0.0
    st.session_state.position_ticker    = ""
    st.session_state.position_shares    = 0
    st.session_state.position_structure = ""
    save_position_state()
    pnl = (float(exit_price) - entry) * shares if shares > 0 else 0
    return pnl


# Load persisted position on startup (only runs once per session via default init)
if _ST_AVAILABLE and st is not None and not st.session_state.get("_position_loaded"):
    load_position_state()
    st.session_state["_position_loaded"] = True


def compute_ib_volume_stats(df, ib_high, ib_low):
    """Return (ib_vol_pct, ib_range_ratio) — both in [0, 1].

    ib_vol_pct  : fraction of total session volume traded while close was inside [ib_low, ib_high]
    ib_range_ratio : IB range / day range  — how much of the day was captured in the opening hour
    """
    if df.empty or ib_high is None or ib_low is None:
        return 0.5, 0.5
    total_vol = float(df["volume"].sum())
    if total_vol <= 0:
        return 0.5, 0.5
    inside_mask = (df["close"] >= ib_low) & (df["close"] <= ib_high)
    ib_vol  = float(df.loc[inside_mask, "volume"].sum())
    ib_vol_pct = ib_vol / total_vol
    day_range = float(df["high"].max()) - float(df["low"].min())
    ib_range  = ib_high - ib_low
    ib_range_ratio = (ib_range / day_range) if day_range > 0 else 0.5
    return round(ib_vol_pct, 3), round(ib_range_ratio, 3)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  ⛔  READ-ONLY — DO NOT MODIFY                                              ║
# ║  classify_day_structure()                                                    ║
# ║  Core 7-structure IB-interaction decision tree.  Any change here breaks     ║
# ║  the entire signal engine and all downstream scoring.                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
def classify_day_structure(df, bin_centers, vap, ib_high, ib_low, poc_price,
                           avg_daily_vol=None):
    """7-structure classification using the exact IB-interaction decision tree.

    Decision tree (mirrors the video framework):
      1. Double Distribution  — bimodal volume profile (always wins if detected)
      2. No IB break          — Normal (wide IB) or Non-Trend (narrow/low-vol IB)
      3. Both sides broken    — Neutral Extreme (close at day extreme) or Neutral
      4. One side only broken — Trend Day (dominant early move) or Normal Variation

    Returns (label, color, detail, insight).
    """
    day_high    = float(df["high"].max())
    day_low     = float(df["low"].min())
    total_range = day_high - day_low
    ib_range    = (ib_high - ib_low) if (ib_high is not None and ib_low is not None) else 0.0
    final_price = float(df["close"].iloc[-1])

    ib_vol_pct, ib_range_ratio = compute_ib_volume_stats(df, ib_high, ib_low)

    if total_range == 0 or ib_range == 0:
        return ("⚖️ Normal / Balanced", "#66bb6a",
                "Insufficient range data.",
                "Not enough price movement to classify structure reliably.")

    atr = compute_atr(df)

    # IB boundary flags
    ib_high_touched = day_high >= ib_high
    ib_low_touched  = day_low  <= ib_low
    both_touched    = ib_high_touched and ib_low_touched
    one_side_up     = ib_high_touched and not ib_low_touched
    one_side_down   = ib_low_touched  and not ib_high_touched
    no_break        = not ib_high_touched and not ib_low_touched

    # Distance of close from IB boundary
    if final_price > ib_high:
        dist_from_ib = final_price - ib_high
    elif final_price < ib_low:
        dist_from_ib = ib_low - final_price
    else:
        dist_from_ib = 0.0

    # Where did the close land in the day's range? (0 = at low, 1 = at high)
    close_pct       = (final_price - day_low) / total_range if total_range > 0 else 0.5
    # "At extreme" = top or bottom 20% of day range
    at_high_extreme = close_pct >= 0.80
    at_low_extreme  = close_pct <= 0.20
    at_extreme      = at_high_extreme or at_low_extreme

    # Early IB violation (first 2 hrs of regular session)
    two_hr_end = df.index[0].replace(hour=11, minute=30)
    early_df   = df[df.index <= two_hr_end]
    early_high = float(early_df["high"].max()) if not early_df.empty else day_high
    early_low  = float(early_df["low"].min())  if not early_df.empty else day_low
    viol_early_up   = early_high > ib_high
    viol_early_down = early_low  < ib_low

    # Directional-volume signal (IB vol% < 0.40 = volume moved outside IB = directional)
    directional_vol  = ib_vol_pct < 0.40 and ib_range_ratio < 0.40
    balanced_vol     = ib_vol_pct > 0.65

    # ── STEP 1: Double Distribution (volume-based, always wins if detected) ───
    dd = _detect_double_distribution(bin_centers, vap)
    if dd is not None:
        pk1, pk2, vi = dd
        sep_price = bin_centers[pk2] - bin_centers[pk1]
        lvn_price = float(bin_centers[vi])
        pct1 = vap[max(0,pk1-2):min(len(vap),pk1+3)].sum() / vap.sum() * 100
        pct2 = vap[max(0,pk2-2):min(len(vap),pk2+3)].sum() / vap.sum() * 100
        detail  = (f"HVNs at ${bin_centers[pk1]:.2f} ({pct1:.0f}% vol) & "
                   f"${bin_centers[pk2]:.2f} ({pct2:.0f}% vol). "
                   f"LVN at ${lvn_price:.2f} (${sep_price:.2f} gap).")
        insight = (f"Two separate auctions detected. LVN at ${lvn_price:.2f} separates the "
                   f"two value areas — expect rapid, high-momentum moves through it. "
                   f"Gap Fill toward the opposing HVN is the primary target.")
        return ("⚡ Double Distribution", "#00bcd4", detail, insight)

    # ── STEP 2: No IB break → Normal or Non-Trend ─────────────────────────────
    if no_break:
        # Non-Trend: narrow IB + low volume interest (holiday, eve-of-news, etc.)
        is_narrow_ib = ib_range < 0.20 * total_range
        total_vol = float(df["volume"].sum())
        if avg_daily_vol and avg_daily_vol > 0:
            pace     = (total_vol / max(1, len(df))) * 390.0
            is_low_vol = (pace / avg_daily_vol) < 0.80
        else:
            is_low_vol = ib_range / max(0.001, day_high) < 0.005
        ib_vol_confirms_nontrend = ib_vol_pct > 0.72 and ib_range_ratio < 0.25
        if is_narrow_ib and (is_low_vol or ib_vol_confirms_nontrend):
            detail  = (f"IB ${ib_range:.2f} = {ib_range/total_range*100:.0f}% of day range. "
                       f"IB volume {ib_vol_pct*100:.0f}% of session total. "
                       f"Volume participation is anemic — no institutional interest.")
            insight = (f"Tight initial balance with {ib_vol_pct*100:.0f}% of session volume "
                       f"inside the opening range signals no institutional interest. "
                       f"Avoid chasing breakouts. Wait for a volume-backed catalyst.")
            return ("😴 Non-Trend", "#78909c", detail, insight)

        # Normal: wide IB set by large players in first hour, never violated
        pct_inside = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean()) * 100
        ib_vol_str = (f"IB absorbed {ib_vol_pct*100:.0f}% of volume — "
                      f"{'strong balance' if ib_vol_pct > 0.60 else 'moderate balance'}.")
        detail  = (f"IB ${ib_high:.2f}–${ib_low:.2f} never violated. "
                   f"Price inside IB for {pct_inside:.0f}% of session. {ib_vol_str}")
        insight = (f"Classic Normal day — large players set a wide range early and left. "
                   f"{ib_vol_pct*100:.0f}% of volume stayed inside the 9:30–10:30 range. "
                   f"No directional conviction. Fade the extremes and target POC ${poc_price:.2f}.")
        return ("⚖️ Normal", "#66bb6a", detail, insight)

    # ── STEP 3: BOTH sides broken → always Neutral family ─────────────────────
    # Per the video: "both sides violated" means EITHER:
    #   • Neutral Extreme: one side ultimately dominated, close near the day extreme
    #   • Neutral: coast-to-coast but closes back in the middle area
    if both_touched:
        if at_extreme:
            side        = "high" if at_high_extreme else "low"
            extreme_lvl = ib_high if at_high_extreme else ib_low
            detail  = (f"Both IB extremes tested. Price closing at day's {side} "
                       f"(${final_price:.2f}, top {close_pct*100:.0f}% of range) — "
                       f"late-session dominance confirmed.")
            insight = (f"Both sides of the IB were probed, then one side took over. "
                       f"Late-session conviction pushed the close to the "
                       f"{'top' if at_high_extreme else 'bottom'} 20% of the day range. "
                       f"This pattern frequently resolves with a "
                       f"{'gap up' if at_high_extreme else 'gap down'} next morning. "
                       f"Key level: ${extreme_lvl:.2f}.")
            return ("⚡ Neutral Extreme", "#7e57c2", detail, insight)
        else:
            # Closes anywhere that is NOT at the extreme = Neutral
            # (back inside IB, between IB and extreme band, or middle of range)
            pct_inside = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean()) * 100
            detail  = (f"Both IB extremes tested. Close at ${final_price:.2f} "
                       f"({close_pct*100:.0f}% of day range) — neither side dominated.")
            insight = (f"Coast-to-coast action with no winner — a classic Neutral day. "
                       f"Large players on both sides active but not far off on value. "
                       f"Price gravitates back toward POC ${poc_price:.2f}. "
                       f"Fade the extremes; avoid chasing direction into the close.")
            return ("🔄 Neutral", "#80cbc4", detail, insight)

    # ── STEP 4: ONE side only broken → Trend Day or Normal Variation ──────────
    # Per the video: Trend = "dominated from pretty much the open, only one side violated"
    # Normal Variation = one side breached but NOT dominant/sustained
    bullish = one_side_up
    dist_atr = dist_from_ib / atr if atr > 0 else 0

    # Trend Day: early violation + close firmly outside IB + directional volume OR 2× ATR move
    is_trend = (
        ((viol_early_up   and at_high_extreme and bullish) or
         (viol_early_down and at_low_extreme  and not bullish))
        and (dist_from_ib > 1.0 * atr or directional_vol)
    )

    if is_trend:
        direction = "Bullish" if bullish else "Bearish"
        confirmed = " ✅ IB vol confirms" if directional_vol else ""
        detail  = (f"{direction} Trend — IB {'High' if bullish else 'Low'} violated early, "
                   f"price {dist_atr:.1f}× ATR outside IB. "
                   f"{ib_vol_pct*100:.0f}% of volume inside IB — directional flow.{confirmed}")
        insight = (f"Strong directional conviction from the open — only ONE IB side ever touched. "
                   f"{'Buyers' if bullish else 'Sellers'} dominated all session. "
                   f"Trend continuation is the high-probability path. "
                   f"Add on pullbacks to POC ${poc_price:.2f}; avoid fading.")
        lbl = "📈 Trend Day" if bullish else "📉 Trend Day (Bear)"
        return (lbl, "#ff9800", detail, insight)

    # Normal Variation: one side broken, but not a full trend
    direction = "Up" if bullish else "Down"
    detail  = (f"IB {'High' if bullish else 'Low'} "
               f"${ib_high if bullish else ib_low:.2f} breached; "
               f"opposite side ${ib_low if bullish else ib_high:.2f} held. "
               f"Close at ${final_price:.2f} ({close_pct*100:.0f}% of range).")
    insight = (f"{'Buyers' if bullish else 'Sellers'} pushed outside the opening range "
               f"but didn't sustain a full trend. "
               f"New value area forming {'above' if bullish else 'below'} "
               f"${ib_high if bullish else ib_low:.2f}. "
               f"Watch for acceptance or rejection at that level.")
    return (f"📊 Normal Variation ({direction})", "#aed581" if bullish else "#ffab91",
            detail, insight)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  ⛔  READ-ONLY — DO NOT MODIFY                                              ║
# ║  compute_structure_probabilities()                                           ║
# ║  Probabilistic scorer using the same decision tree as classify_day_         ║
# ║  structure().  Weights are hand-calibrated — do not touch.                  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
def compute_structure_probabilities(df, bin_centers, vap, ib_high, ib_low, poc_price):
    """Score each of the 7 structures using the same IB-interaction decision tree
    as classify_day_structure.  Scores are converted to percentages at the end.

    Key invariant (mirrors the video framework):
      • no_break       → only Normal / Non-Trend get high scores
      • both_hit       → only Neutral / Neutral Extreme get high scores
      • one_side_only  → only Trend / Normal Variation / Dbl Dist get high scores
    """
    day_high    = float(df["high"].max())
    day_low     = float(df["low"].min())
    total_range = day_high - day_low
    ib_range    = (ib_high - ib_low) if (ib_high is not None and ib_low is not None) else 0.0
    final_price = float(df["close"].iloc[-1])
    fallback    = {"Non-Trend": 14.0, "Normal": 14.0, "Trend": 14.0,
                   "Ntrl Extreme": 14.0, "Neutral": 14.0, "Nrml Var": 15.0, "Dbl Dist": 15.0}
    if total_range == 0 or ib_range == 0:
        return fallback

    ib_vol_pct, ib_range_ratio = compute_ib_volume_stats(df, ib_high, ib_low)

    rr          = total_range / ib_range
    pct_inside  = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean())

    # IB boundary state (the core 3-way split)
    ib_high_hit  = day_high >= ib_high
    ib_low_hit   = day_low  <= ib_low
    both_hit     = ib_high_hit and ib_low_hit
    one_side     = ib_high_hit ^ ib_low_hit   # XOR: exactly one side broken
    no_break     = not ib_high_hit and not ib_low_hit

    atr = compute_atr(df)
    if final_price > ib_high:
        dist_ib = final_price - ib_high
    elif final_price < ib_low:
        dist_ib = ib_low - final_price
    else:
        dist_ib = 0.0

    # Close position in day range (0 = at low, 1 = at high)
    close_pct   = (final_price - day_low) / total_range if total_range > 0 else 0.5
    at_extreme  = close_pct >= 0.80 or close_pct <= 0.20   # top/bottom 20% of range

    # Early IB violation — only meaningful for one-side-only days
    two_hr_end  = df.index[0].replace(hour=11, minute=30)
    early_df    = df[df.index <= two_hr_end]
    early_high  = float(early_df["high"].max()) if not early_df.empty else day_high
    early_low   = float(early_df["low"].min())  if not early_df.empty else day_low
    viol_early  = (early_high > ib_high) or (early_low < ib_low)

    directional_vol = ib_vol_pct < 0.40 and ib_range_ratio < 0.40
    has_dd          = _detect_double_distribution(bin_centers, vap) is not None

    # ── Volume multipliers ────────────────────────────────────────────────────
    ib_balance_boost = max(0.5, ib_vol_pct * 2.0)           # high → balanced day
    ib_trend_boost   = max(0.5, (1.0 - ib_vol_pct) * 2.0)  # low  → directional day

    # ── Scores gated by IB-interaction bucket ─────────────────────────────────
    # Non-Trend / Normal → only score when no IB break
    if no_break:
        is_narrow = ib_range < 0.20 * total_range
        s_nontrend = max(2.0, (1.0 - rr) * 40.0 * ib_balance_boost) if is_narrow else 2.0
        s_normal   = (5.0 + pct_inside * 60.0) * ib_balance_boost
    else:
        s_nontrend = 2.0
        s_normal   = 2.0

    # Neutral / Neutral Extreme → only score when BOTH sides broken
    if both_hit:
        s_ntrl_extreme = 70.0 if at_extreme else 4.0
        s_neutral      = 4.0  if at_extreme else 70.0
    else:
        s_ntrl_extreme = 2.0
        s_neutral      = 2.0

    # Trend / Normal Variation / Dbl Dist → only score when ONE side broken
    if one_side:
        # Trend: early break, close at extreme, directional volume
        trend_strength = 5.0 + max(0.0, (dist_ib / max(atr, 0.01) - 1.0) * 25.0)
        is_trend_day   = viol_early and at_extreme
        s_trend   = trend_strength * ib_trend_boost if is_trend_day else 4.0
        s_nrml_var= 4.0 if is_trend_day else (40.0 * (0.7 + 0.6 * (1.0 - ib_vol_pct)))
        s_dbl_dist= 70.0 if has_dd else 4.0
    else:
        s_trend    = 2.0
        s_nrml_var = 2.0
        s_dbl_dist = 70.0 if has_dd else 2.0   # DD can still override on both-hit days

    scores = {
        "Non-Trend":    s_nontrend,
        "Normal":       s_normal,
        "Trend":        s_trend,
        "Ntrl Extreme": s_ntrl_extreme,
        "Neutral":      s_neutral,
        "Nrml Var":     s_nrml_var,
        "Dbl Dist":     s_dbl_dist,
    }

    # ── Apply adaptive learned weights ─────────────────────────────────────────
    # Maps probability-engine keys → canonical weight keys
    _score_to_wkey = {
        "Non-Trend":    "non_trend",
        "Normal":       "normal",
        "Trend":        "trend_bull",
        "Ntrl Extreme": "ntrl_extreme",
        "Neutral":      "neutral",
        "Nrml Var":     "nrml_variation",
        "Dbl Dist":     "double_dist",
    }
    try:
        _w = load_brain_weights()
        scores = {k: v * _w.get(_score_to_wkey.get(k, "normal"), 1.0)
                  for k, v in scores.items()}
    except Exception:
        pass   # weights unavailable — use raw scores

    total = sum(scores.values())
    return {k: round(v / total * 100, 1) for k, v in scores.items()}


def fetch_avg_daily_volume(api_key, secret_key, ticker, trade_date, lookback_days=50):
    """Return the average total daily volume for ticker over the last N trading days before trade_date.
    Default is 50 days to provide a robust, statistically stable baseline."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client = StockHistoricalDataClient(api_key, secret_key)
    start = EASTERN.localize(
        datetime(trade_date.year, trade_date.month, trade_date.day) - timedelta(days=lookback_days * 2)
    )
    end = EASTERN.localize(datetime(trade_date.year, trade_date.month, trade_date.day))
    req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Day, start=start, end=end)
    bars = client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        return None
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level="symbol")
    df = df.sort_index()
    # Keep only the last lookback_days complete days
    df = df.tail(lookback_days)
    if df.empty:
        return None
    return float(df["volume"].mean())


def fetch_daily_stats(api_key, secret_key, ticker, trade_date, lookback_days=50):
    """Return (avg_daily_volume, prev_close) for ticker before trade_date in one API call.

    prev_close is the close of the last complete trading day before trade_date.
    avg_daily_volume is the mean daily volume over the last lookback_days sessions.
    Either value may be None if data is unavailable.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    try:
        client = StockHistoricalDataClient(api_key, secret_key)
        start = EASTERN.localize(
            datetime(trade_date.year, trade_date.month, trade_date.day) - timedelta(days=lookback_days * 2)
        )
        end = EASTERN.localize(datetime(trade_date.year, trade_date.month, trade_date.day))
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Day, start=start, end=end)
        bars = client.get_stock_bars(req)
        df = bars.df
        if df.empty:
            return None, None
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(ticker, level="symbol")
        df = df.sort_index()
        prev_close = float(df["close"].iloc[-1]) if not df.empty else None
        df_vol = df.tail(lookback_days)
        avg_vol = float(df_vol["volume"].mean()) if not df_vol.empty else None
        return avg_vol, prev_close
    except Exception:
        return None, None


def fetch_etf_pct_change(api_key, secret_key, etf, trade_date, feed="iex"):
    """Return today's open-to-close percent change for the given ETF ticker."""
    try:
        df = fetch_bars(api_key, secret_key, etf, trade_date, feed=feed)
        if df.empty:
            return 0.0
        open_price = float(df["open"].iloc[0])
        close_price = float(df["close"].iloc[-1])
        if open_price == 0:
            return 0.0
        return (close_price - open_price) / open_price * 100.0
    except Exception:
        return 0.0


def is_market_open():
    """True if the current EST clock is within regular session hours (9:30–16:00)."""
    t = datetime.now(EASTERN).time()
    return dtime(9, 30) <= t <= dtime(16, 0)


def build_rvol_intraday_curve(api_key, secret_key, ticker, trade_date,
                               lookback_days=50, feed="iex"):
    """Build a 390-element list of average cumulative volume at each minute from open.

    Each element i represents the expected cumulative volume after (i+1) minutes of
    trading, averaged across the last lookback_days sessions before trade_date.
    Uses 50-day default for a statistically robust baseline.
    Returns None if insufficient data.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(api_key, secret_key)
    start_dt = EASTERN.localize(
        datetime(trade_date.year, trade_date.month, trade_date.day)
        - timedelta(days=lookback_days * 3)   # extra buffer for weekends/holidays
    )
    end_dt = EASTERN.localize(
        datetime(trade_date.year, trade_date.month, trade_date.day)
    )
    req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
                           start=start_dt, end=end_dt, feed=feed)
    bars = client.get_stock_bars(req)
    df_all = bars.df
    if df_all.empty:
        return None

    if isinstance(df_all.index, pd.MultiIndex):
        df_all = df_all.xs(ticker, level="symbol")
    df_all.index = pd.to_datetime(df_all.index)
    if df_all.index.tz is None:
        df_all.index = df_all.index.tz_localize("UTC")
    df_all.index = df_all.index.tz_convert(EASTERN)
    df_all = df_all.sort_index()

    # Keep only market hours
    df_all = df_all[(df_all.index.time >= dtime(9, 30)) &
                    (df_all.index.time <= dtime(16, 0))]
    if df_all.empty:
        return None

    # Vectorised minutes-from-open (9:30 = minute 0)
    df_all = df_all.copy()
    df_all["_mins"] = df_all.index.hour * 60 + df_all.index.minute - (9 * 60 + 30)
    df_all["_date"] = pd.to_datetime(df_all.index.date)

    day_curves = []
    for day, grp in df_all.groupby("_date"):
        if day.date() >= trade_date:           # exclude the analysis date itself
            continue
        cv = np.zeros(390)
        for _, row in grp.iterrows():
            m = int(row["_mins"])
            if 0 <= m < 390:
                cv[m] = float(row["volume"])
        day_curves.append(np.cumsum(cv))

    if not day_curves:
        return None

    day_curves = day_curves[-lookback_days:]   # keep most recent N days
    return np.mean(day_curves, axis=0).tolist()


def compute_rvol(df, intraday_curve=None, avg_daily_vol=None):
    """Time-segmented RVOL (preferred) with pace-adjusted fallback.

    Time-segmented: compare cumulative volume at current elapsed minute to the
    historical average cumulative volume at the same minute of day.
    Fallback: extrapolate current pace to full session / full-day average.
    Returns None if no baseline is available.
    """
    if df.empty:
        return None
    current_vol = float(df["volume"].sum())
    elapsed_bars = max(1, len(df))

    # ── Time-segmented RVOL ───────────────────────────────────────────────────
    if intraday_curve is not None and len(intraday_curve) >= elapsed_bars:
        idx = min(elapsed_bars - 1, len(intraday_curve) - 1)
        expected_vol = float(intraday_curve[idx])
        if expected_vol > 0:
            return round(current_vol / expected_vol, 2)

    # ── Pace-adjusted fallback ────────────────────────────────────────────────
    if avg_daily_vol is not None and avg_daily_vol > 0:
        pace = (current_vol / elapsed_bars) * 390   # 390-minute session
        return round(pace / avg_daily_vol, 2)

    return None


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  ⛔  READ-ONLY — DO NOT MODIFY                                              ║
# ║  compute_buy_sell_pressure()                                                 ║
# ║  Tape-reading signal (uptick ratio, delta, absorption).  Core input to      ║
# ║  Edge Score and TCS.  Calibrated thresholds — do not touch.                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
def compute_buy_sell_pressure(df,
                               lookback_len=10,
                               baseline_weight=0.5,
                               sell_pct_floor=0.0,
                               sell_pct_ceiling=1.0):
    """Estimate session-cumulative buy vs sell volume using the blended CLV+Tick method.

    Mirrors the ThinkScript Blended split formula:
        sellPctCLV  = (high − close) / (high − low)     ← close location value
        sellPctTick = 1 if close < close[1]              ← up/down tick
                      0 if close > close[1]
                      0.5 otherwise
        sellPctRaw      = (sellPctCLV + sellPctTick) / 2
        sellPctBaseline = rolling mean of sellPctRaw over lookback_len bars
        sellPctBlended  = (1−baseline_weight)×sellPctRaw + baseline_weight×sellPctBaseline
        sellPct         = clamp(sellPctBlended, floor, ceiling)
        buyPct          = 1 − sellPct

    Momentum compares last 5 bars vs prior 5 bars (RSI-style ramping detection).
    Returns dict with keys: buy_pct, sell_pct, trend_now, trend_prev,
                            total_buy, total_sell — or None if insufficient data.
    """
    if df.empty or len(df) < 2:
        return None
    _df = df.dropna(subset=["open", "high", "low", "close", "volume"]).copy()
    if len(_df) < 2:
        return None

    # ── CLV component ─────────────────────────────────────────────────────────
    hl = (_df["high"] - _df["low"]).replace(0, np.nan)
    sell_pct_clv = (((_df["high"] - _df["close"]) / hl).fillna(0.5)).clip(0, 1)

    # ── Up/Down Tick component ────────────────────────────────────────────────
    close_prev = _df["close"].shift(1)
    sell_pct_tick = np.where(
        _df["close"] < close_prev, 1.0,
        np.where(_df["close"] > close_prev, 0.0, 0.5)
    )
    sell_pct_tick = pd.Series(sell_pct_tick, index=_df.index).fillna(0.5)

    # ── Blend CLV + Tick → apply baseline smoothing → clamp ──────────────────
    sell_pct_raw      = (sell_pct_clv + sell_pct_tick) / 2.0
    sell_pct_baseline = sell_pct_raw.rolling(window=max(1, lookback_len),
                                              min_periods=1).mean()
    sell_pct_blended  = ((1.0 - baseline_weight) * sell_pct_raw
                         + baseline_weight * sell_pct_baseline)
    sell_pct          = sell_pct_blended.clip(sell_pct_floor, sell_pct_ceiling)
    buy_pct_series    = 1.0 - sell_pct

    _df["buy_vol"]  = _df["volume"] * buy_pct_series
    _df["sell_vol"] = _df["volume"] * sell_pct

    total_buy  = float(_df["buy_vol"].sum())
    total_sell = float(_df["sell_vol"].sum())
    total_vol  = total_buy + total_sell
    if total_vol == 0:
        return None

    buy_pct_session = total_buy / total_vol * 100.0

    def _pct(sub):
        b = float(sub["buy_vol"].sum())
        s = float(sub["sell_vol"].sum())
        return b / (b + s) * 100.0 if (b + s) > 0 else 50.0

    # Momentum: last 5 bars vs prior 5 bars
    recent5    = _df.tail(5)
    prior5     = _df.iloc[-10:-5] if len(_df) >= 10 else _df.head(max(1, len(_df) // 2))
    trend_now  = _pct(recent5)
    trend_prev = _pct(prior5)

    return {
        "buy_pct":    round(buy_pct_session, 1),
        "sell_pct":   round(100.0 - buy_pct_session, 1),
        "trend_now":  round(trend_now, 1),
        "trend_prev": round(trend_prev, 1),
        "total_buy":  total_buy,
        "total_sell": total_sell,
    }


def compute_order_flow_signals(df, ib_high=None, ib_low=None):
    """Tier 2 order flow proxy signals derived from 1-min OHLCV bars.

    Signals returned (all based on bar structure — no L2 data required):

    pressure_accel   : "Accelerating" | "Decelerating" | "Flat"
                       Compares 3-bar vs 10-bar buy pressure windows.
    pressure_short   : buy% for last 3 bars  (0-100)
    pressure_medium  : buy% for last 10 bars (0-100)
    pressure_long    : buy% for last 20 bars (0-100)

    bar_quality      : 0-100. % of last 10 bars where close > midpoint of bar range.
                       100 = all bars closed near high; 0 = all closed near low.
    bar_quality_label: "Buyers Dominant" | "Sellers Dominant" | "Contested"

    vol_surge_ratio  : current-bar volume / 10-bar avg volume (1.0 = baseline)
    vol_surge_label  : "Surge" | "Above Avg" | "Normal" | "Thin"

    streak           : int. +N = N consecutive bars closing higher than prior close.
                       -N = N consecutive bars closing lower than prior close.
    streak_label     : "Strong Upward Tape" | "Moderate Upward Tape" | etc.

    ib_proximity     : "At IB High" | "At IB Low" | "Mid-Range" | None
    ib_vol_confirm   : True if vol_surge_ratio >= 1.5 while at IB extreme

    composite_signal : "Strong Buy Flow" | "Moderate Buy Flow" | "Neutral" |
                       "Moderate Sell Flow" | "Strong Sell Flow"
    composite_score  : -100 to +100 (positive = bullish flow)
    """
    if df is None or df.empty or len(df) < 3:
        return None

    _df = df.dropna(subset=["open", "high", "low", "close", "volume"]).copy()
    if len(_df) < 3:
        return None

    # ── Per-bar buy fraction (reuse CLV+Tick formula) ─────────────────────────
    hl = (_df["high"] - _df["low"]).replace(0, np.nan)
    sell_clv  = ((_df["high"] - _df["close"]) / hl).fillna(0.5).clip(0, 1)
    close_prev = _df["close"].shift(1)
    sell_tick  = np.where(
        _df["close"] < close_prev, 1.0,
        np.where(_df["close"] > close_prev, 0.0, 0.5)
    )
    sell_frac = pd.Series(
        ((sell_clv + pd.Series(sell_tick, index=_df.index)) / 2.0).values,
        index=_df.index,
    ).clip(0, 1)
    buy_frac = 1.0 - sell_frac

    def _win_buy_pct(sub_buy, sub_vol):
        bv = (sub_buy * sub_vol).sum()
        tv = sub_vol.sum()
        return float(bv / tv * 100.0) if tv > 0 else 50.0

    n = len(_df)
    buy_f = buy_frac.values
    vols  = _df["volume"].values

    short_n  = min(3,  n)
    medium_n = min(10, n)
    long_n   = min(20, n)

    p_short  = _win_buy_pct(buy_f[-short_n:],  vols[-short_n:])
    p_medium = _win_buy_pct(buy_f[-medium_n:], vols[-medium_n:])
    p_long   = _win_buy_pct(buy_f[-long_n:],   vols[-long_n:])

    accel_delta = p_short - p_medium
    if accel_delta > 4:
        pressure_accel = "Accelerating"
    elif accel_delta < -4:
        pressure_accel = "Decelerating"
    else:
        pressure_accel = "Flat"

    # ── Bar quality (close vs midpoint of each bar's range) ──────────────────
    bq_n    = min(10, n)
    bq_sub  = _df.tail(bq_n)
    mid     = (bq_sub["high"] + bq_sub["low"]) / 2.0
    bar_quality = float((bq_sub["close"] > mid).sum() / bq_n * 100.0)
    if bar_quality >= 65:
        bar_quality_label = "Buyers Dominant"
    elif bar_quality <= 35:
        bar_quality_label = "Sellers Dominant"
    else:
        bar_quality_label = "Contested"

    # ── Volume surge ratio (last bar vs 10-bar avg) ───────────────────────────
    avg_vol_10 = float(np.mean(vols[-min(10, n):])) if n >= 2 else 1.0
    cur_vol    = float(vols[-1]) if n >= 1 else 0.0
    vol_surge_ratio = (cur_vol / avg_vol_10) if avg_vol_10 > 0 else 1.0
    if vol_surge_ratio >= 2.0:
        vol_surge_label = "Surge"
    elif vol_surge_ratio >= 1.3:
        vol_surge_label = "Above Avg"
    elif vol_surge_ratio >= 0.7:
        vol_surge_label = "Normal"
    else:
        vol_surge_label = "Thin"

    # ── Consecutive close streak ───────────────────────────────────────────────
    closes = _df["close"].values
    streak = 0
    if len(closes) >= 2:
        direction = 1 if closes[-1] >= closes[-2] else -1
        for i in range(len(closes) - 2, 0, -1):
            if direction == 1 and closes[i] >= closes[i - 1]:
                streak += 1
            elif direction == -1 and closes[i] <= closes[i - 1]:
                streak -= 1
            else:
                break
        if direction == 1:
            streak = max(streak, 1)
        else:
            streak = min(streak, -1)

    if streak >= 5:
        streak_label = "Strong Upward Tape"
    elif streak >= 3:
        streak_label = "Moderate Upward Tape"
    elif streak >= 1:
        streak_label = "Mild Upward Tape"
    elif streak <= -5:
        streak_label = "Strong Downward Tape"
    elif streak <= -3:
        streak_label = "Moderate Downward Tape"
    elif streak <= -1:
        streak_label = "Mild Downward Tape"
    else:
        streak_label = "Mixed Tape"

    # ── IB proximity + volume confirmation ────────────────────────────────────
    last_close  = float(closes[-1])
    ib_proximity    = None
    ib_vol_confirm  = False
    if ib_high is not None and ib_low is not None:
        ib_range = ib_high - ib_low
        if ib_range > 0:
            if last_close >= ib_high - 0.05 * ib_range:
                ib_proximity   = "At IB High"
                ib_vol_confirm = vol_surge_ratio >= 1.5
            elif last_close <= ib_low + 0.05 * ib_range:
                ib_proximity   = "At IB Low"
                ib_vol_confirm = vol_surge_ratio >= 1.5
            else:
                ib_proximity = "Mid-Range"

    # ── Composite score (-100 to +100) ────────────────────────────────────────
    # Components:
    #   pressure short vs 50  → weight 35
    #   bar quality vs 50     → weight 30
    #   streak contribution   → weight 20
    #   vol surge             → weight 15 (surge amplifies direction)
    p_score   = (p_short - 50.0) * (35.0 / 50.0)          # -35 to +35
    bq_score  = (bar_quality - 50.0) * (30.0 / 50.0)       # -30 to +30
    str_score = float(np.clip(streak, -5, 5)) / 5.0 * 20.0 # -20 to +20
    if vol_surge_ratio >= 1.5:
        vol_score = 10.0 if p_short >= 50 else -10.0
    elif vol_surge_ratio >= 1.2:
        vol_score = 5.0 if p_short >= 50 else -5.0
    else:
        vol_score = 0.0
    composite_score = float(np.clip(p_score + bq_score + str_score + vol_score, -100, 100))

    if composite_score >= 40:
        composite_signal = "Strong Buy Flow"
    elif composite_score >= 15:
        composite_signal = "Moderate Buy Flow"
    elif composite_score <= -40:
        composite_signal = "Strong Sell Flow"
    elif composite_score <= -15:
        composite_signal = "Moderate Sell Flow"
    else:
        composite_signal = "Neutral"

    return {
        "pressure_accel":    pressure_accel,
        "pressure_short":    round(p_short,  1),
        "pressure_medium":   round(p_medium, 1),
        "pressure_long":     round(p_long,   1),
        "accel_delta":       round(accel_delta, 1),
        "bar_quality":       round(bar_quality, 1),
        "bar_quality_label": bar_quality_label,
        "vol_surge_ratio":   round(vol_surge_ratio, 2),
        "vol_surge_label":   vol_surge_label,
        "streak":            streak,
        "streak_label":      streak_label,
        "ib_proximity":      ib_proximity,
        "ib_vol_confirm":    ib_vol_confirm,
        "composite_signal":  composite_signal,
        "composite_score":   round(composite_score, 1),
    }


def rvol_classify(rvol, pct_chg_today, elapsed_bars=None, price_now=None):
    """Time-aware RVOL label.

    elapsed_bars — minutes since 9:30 AM open (None = historical full-day view)
    price_now    — current last price (for small-cap volatility adjustment)
    Returns (label | None, color, is_runner, is_play)
    """
    if rvol is None:
        return None, "#aaaaaa", False, False

    # ── Runner tiers (highest priority) ───────────────────────────────────────
    if rvol > 5.5:
        return "🚀 MULTI-DAY RUNNER POTENTIAL", "#FFD700", True, True
    if rvol > 4.0:
        return "🔥 STOCK IN PLAY", "#FF6B35", False, True

    # ── 9:30–10:00 AM "Fuel Check" (first 30 minutes of session) ─────────────
    is_open_window = elapsed_bars is not None and 1 <= elapsed_bars <= 30
    if is_open_window and rvol > 3.0:
        return "🔥 HIGH CONVICTION OPEN", "#FF9500", False, True

    # ── Fake-out with small-cap volatility adjustment ─────────────────────────
    if rvol < 1.2 and pct_chg_today > 0.5:
        # For stocks priced $2–$20 (small-cap / low-float) noise threshold is 1%
        # — only flag as fake-out if divergence is meaningful (> 1% move)
        if price_now is not None and 2.0 <= price_now <= 20.0:
            if pct_chg_today < 1.0:          # within noise band → ignore
                return None, "#aaaaaa", False, False
        return "⚠️ DEAD CAT / FAKE-OUT RISK", "#ef5350", False, False

    return None, "#aaaaaa", False, False


def compute_model_prediction(df, rvol, tcs, sector_bonus, market_open=True):
    """Classify move as Fake-out / High Conviction / Consolidation.

    market_open=False → returns ('Market Closed', '') so the renderer shows
    the sleep-mode info box instead of a directional warning.
    """
    if not market_open:
        return "Market Closed", ""

    if len(df) < 2:
        return "Consolidation", "Insufficient bars for prediction."

    price_start = float(df["open"].iloc[0])
    price_now   = float(df["close"].iloc[-1])
    pct_chg = (price_now - price_start) / price_start * 100.0 if price_start > 0 else 0.0

    # Fake-out: price up but volume weak
    if rvol is not None and rvol < 1.2 and pct_chg > 0.5:
        return ("Fake-out",
                f"Price +{pct_chg:.1f}% on anemic RVOL {rvol:.1f}× — volume is NOT confirming "
                "the move. High reversal risk. Wait for RVOL > 2.0 before trusting direction.")

    # High Conviction: strong RVOL + strong TCS
    if rvol is not None and rvol > 4.0 and tcs >= 60:
        tail = " Sector tailwind adds confirmation." if sector_bonus > 0 else ""
        return ("High Conviction",
                f"RVOL {rvol:.1f}× surge confirms directional participation. "
                f"TCS {tcs:.0f}% — institutional footprint visible. "
                f"Trend continuation is the high-probability path.{tail}")

    # Consolidation: low TCS
    if tcs < 35:
        return ("Consolidation",
                f"TCS {tcs:.0f}% — low trend energy. Price coiling inside range. "
                "Watch for a Volume Velocity spike to signal the next push.")

    # Moderate high conviction
    if abs(pct_chg) > 0.5 and (rvol is None or rvol >= 1.5):
        direction = f"+{pct_chg:.1f}%" if pct_chg > 0 else f"{pct_chg:.1f}%"
        bias = "upward" if pct_chg > 0 else "downward"
        return ("High Conviction",
                f"Price {direction} with TCS {tcs:.0f}% and volume not diverging. "
                f"Structure supports {bias} continuation.")

    return ("Consolidation",
            f"Mixed signals — TCS {tcs:.0f}%, "
            f"RVOL {'N/A' if rvol is None else f'{rvol:.1f}×'}. "
            "Price and volume not clearly aligned; range-bound action expected.")


def compute_tcs(df, ib_high, ib_low, poc_price, sector_bonus=0.0):
    """Trend Confidence Score (0–100).

    Three equally-weighted factors:
      • Range Factor    (40 pts) — day range vs IB range
      • Velocity Factor (30 pts) — current vol/min vs session avg vol/min
      • Structure Factor (30 pts) — price > 1 ATR from POC and trending away
    Optional sector_bonus: +10 pts if sector ETF is up > 1%.
    """
    tcs = 0.0

    day_high = float(df["high"].max())
    day_low = float(df["low"].min())
    total_range = day_high - day_low
    ib_range = (ib_high - ib_low) if (ib_high and ib_low) else 0.0
    final_price = float(df["close"].iloc[-1])

    # ── Range Factor (40 pts) ─────────────────────────────────────────────────
    if ib_range > 0:
        rr = total_range / ib_range
        if rr >= 2.5:
            tcs += 40.0
        elif rr > 1.1:
            tcs += 40.0 * (rr - 1.1) / (2.5 - 1.1)

    # ── Velocity Factor (30 pts) ──────────────────────────────────────────────
    if len(df) >= 6:
        w = min(3, len(df) // 2)
        current_vel = float(df["volume"].iloc[-w:].mean())
        avg_vel = float(df["volume"].mean())
        if avg_vel > 0:
            vr = current_vel / avg_vel
            if vr >= 2.0:
                tcs += 30.0
            elif vr > 1.0:
                tcs += 30.0 * (vr - 1.0) / (2.0 - 1.0)

    # ── Structure Factor (30 pts) ─────────────────────────────────────────────
    if len(df) >= 3:
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)
        atr = float(tr.rolling(window=min(14, len(df))).mean().iloc[-1])

        if atr > 0 and abs(final_price - poc_price) > atr:
            # "Moving" = last 3 closes trending further from POC
            if len(df) >= 4:
                poc_side = 1 if final_price > poc_price else -1
                move = float(df["close"].iloc[-1]) - float(df["close"].iloc[-4])
                if move * poc_side > 0:
                    tcs += 30.0          # trending away — full credit
                else:
                    tcs += 15.0          # beyond ATR but stalling
            else:
                tcs += 20.0

    # ── Sector Tailwind bonus (+10 pts if sector ETF up > 1%) ────────────────
    tcs += sector_bonus

    return round(min(100.0, tcs), 1)


def compute_volume_velocity(df):
    if len(df) < 4:
        return None, None, None
    w = min(3, len(df) // 2)
    recent = float(df["volume"].iloc[-w:].mean())
    if len(df) < 2 * w:
        return recent, None, None
    prev = float(df["volume"].iloc[-2*w:-w].mean())
    if prev == 0:
        return recent, None, None
    chg = (recent - prev) / prev * 100
    return recent, abs(chg), ("↑" if chg >= 0 else "↓")


def compute_target_zones(df, ib_high, ib_low, bin_centers, vap, tcs):
    """Return a list of dynamic target zone dicts based on structure.

    Each dict: {type, price, label, color, description, [lvn_price, lvn_idx]}
    """
    targets = []
    if df.empty or ib_high is None or ib_low is None:
        return targets
    ib_range = ib_high - ib_low
    if ib_range <= 0:
        return targets

    final_price  = float(df["close"].iloc[-1])
    day_high     = float(df["high"].max())
    day_low      = float(df["low"].min())

    ib_high_violated = bool((df["high"] >= ib_high).any())
    ib_low_violated  = bool((df["low"]  <= ib_low).any())
    price_back_inside = ib_low < final_price < ib_high

    # ── Coast-to-Coast ────────────────────────────────────────────────────────
    if ib_high_violated and price_back_inside:
        targets.append({
            "type": "coast_to_coast",
            "price": ib_low,
            "label": "🎯 C2C Target",
            "color": "#ff5252",
            "description": (f"IB High violated → price returned inside → "
                            f"Coast-to-Coast target: IB Low ${ib_low:.2f}"),
        })
    if ib_low_violated and price_back_inside:
        targets.append({
            "type": "coast_to_coast",
            "price": ib_high,
            "label": "🎯 C2C Target",
            "color": "#00e676",
            "description": (f"IB Low violated → price returned inside → "
                            f"Coast-to-Coast target: IB High ${ib_high:.2f}"),
        })

    # ── Range Extension  (TCS > 70 %) ────────────────────────────────────────
    if tcs > 70 and ib_range > 0:
        bullish = ib_high_violated and not ib_low_violated
        bearish = ib_low_violated  and not ib_high_violated
        if bullish:
            ext15 = ib_high + 1.5 * ib_range
            ext20 = ib_high + 2.0 * ib_range
            targets.append({"type": "trend_extension", "price": ext15,
                            "label": "🎯 1.5× Ext", "color": "#26a69a",
                            "description": f"Bullish 1.5× IB extension: ${ext15:.2f}"})
            targets.append({"type": "trend_extension", "price": ext20,
                            "label": "🎯 2.0× Ext", "color": "#4caf50",
                            "description": f"Bullish 2.0× IB extension: ${ext20:.2f}"})
        elif bearish:
            ext15 = ib_low - 1.5 * ib_range
            ext20 = ib_low - 2.0 * ib_range
            targets.append({"type": "trend_extension", "price": ext15,
                            "label": "🎯 1.5× Ext", "color": "#ef5350",
                            "description": f"Bearish 1.5× IB extension: ${ext15:.2f}"})
            targets.append({"type": "trend_extension", "price": ext20,
                            "label": "🎯 2.0× Ext", "color": "#c62828",
                            "description": f"Bearish 2.0× IB extension: ${ext20:.2f}"})

    # ── Gap Fill (Double Distribution LVN) ───────────────────────────────────
    dd = _detect_double_distribution(bin_centers, vap)
    if dd is not None:
        pk1, pk2, vi = dd
        lvn_price = float(bin_centers[vi])
        hvn1 = float(bin_centers[pk1])
        hvn2 = float(bin_centers[pk2])
        target_hvn = hvn2 if final_price < lvn_price else hvn1
        targets.append({
            "type": "gap_fill",
            "price": target_hvn,
            "lvn_price": lvn_price,
            "lvn_idx": int(vi),
            "label": "🎯 Gap Fill",
            "color": "#ffd700",
            "description": (f"DD LVN at ${lvn_price:.2f} → "
                            f"Gap Fill target ${target_hvn:.2f}"),
        })

    return targets


def _stream_worker(api_key, secret_key, ticker, feed_str, data_queue, stop_event):
    import asyncio
    from alpaca.data.live import StockDataStream
    from alpaca.data.enums import DataFeed

    feed_enum = DataFeed.SIP if feed_str == "sip" else DataFeed.IEX
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stream = StockDataStream(api_key, secret_key, feed=feed_enum)

    async def on_trade(trade):
        try:
            data_queue.put_nowait({"t": "trade", "p": float(trade.price),
                                   "s": float(trade.size), "ts": trade.timestamp})
        except Exception:
            pass

    async def on_bar(bar):
        try:
            data_queue.put_nowait({"t": "bar", "o": float(bar.open), "h": float(bar.high),
                                   "l": float(bar.low), "c": float(bar.close),
                                   "v": float(bar.volume), "ts": bar.timestamp})
        except Exception:
            pass

    stream.subscribe_trades(on_trade, ticker)
    stream.subscribe_bars(on_bar, ticker)

    async def run_until_stopped():
        # _run_forever() is the actual coroutine; stream.run() wraps it in
        # asyncio.run() which would conflict with our already-running loop.
        task = asyncio.ensure_future(stream._run_forever())
        while not stop_event.is_set():
            await asyncio.sleep(0.3)
            if task.done():
                break
        stream.stop()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception:
            pass
        finally:
            if not task.done():
                task.cancel()

    try:
        loop.run_until_complete(run_until_stopped())
    except Exception as e:
        try:
            data_queue.put_nowait({"t": "error", "msg": str(e)})
        except Exception:
            pass
    finally:
        try:
            loop.close()
        except Exception:
            pass


def start_stream(api_key, secret_key, ticker, feed_str,
                 historical_bars: list | None = None):
    """Start the WebSocket stream for `ticker`.

    historical_bars — optional list of bar dicts pre-loaded from today's
        session (9:30 AM to now).  If provided, live_bars is seeded with
        this data so the volume profile, IB, VWAP, and TCS are all computed
        from the full day context the moment the stream starts — not from
        scratch on the first arriving bar.

    Bar dict format:
        {"open": float, "high": float, "low": float,
         "close": float, "volume": float, "timestamp": <Timestamp>}
    """
    q = queue.Queue(maxsize=10000)
    ev = threading.Event()
    t = threading.Thread(target=_stream_worker,
                         args=(api_key, secret_key, ticker, feed_str, q, ev),
                         daemon=True)
    t.start()
    st.session_state.live_queue = q
    st.session_state.live_stop_event = ev
    st.session_state.live_thread = t
    st.session_state.live_active = True
    st.session_state.live_bars = list(historical_bars) if historical_bars else []
    st.session_state.live_current_bar = None
    st.session_state.live_trades = deque(maxlen=3000)
    st.session_state.live_ticker = ticker
    st.session_state.live_error = None
    # Reset alert state for the new session
    st.session_state.tcs_fired_high = False
    st.session_state.tcs_was_high = False


def stop_stream():
    if st.session_state.live_stop_event:
        st.session_state.live_stop_event.set()
    st.session_state.live_active = False
    st.session_state.live_queue = None
    st.session_state.live_stop_event = None
    st.session_state.live_thread = None


def drain_queue():
    q = st.session_state.live_queue
    if q is None:
        return
    cur = st.session_state.live_current_bar or {}
    processed = 0
    while processed < 1000:
        try:
            item = q.get_nowait()
            processed += 1
        except queue.Empty:
            break
        t = item.get("t")
        if t == "error":
            st.session_state.live_error = item.get("msg", "Unknown error")
            st.session_state.live_active = False
        elif t == "bar":
            st.session_state.live_bars.append(
                {"open": item["o"], "high": item["h"], "low": item["l"],
                 "close": item["c"], "volume": item["v"], "timestamp": item["ts"]}
            )
            cur = {}
        elif t == "trade":
            p, s, ts = item["p"], item["s"], item["ts"]
            st.session_state.live_trades.append({"price": p, "size": s, "ts": ts})
            if not cur:
                cur = {"open": p, "high": p, "low": p, "close": p, "volume": s, "timestamp": ts}
            else:
                cur["high"] = max(cur["high"], p)
                cur["low"] = min(cur["low"], p)
                cur["close"] = p
                cur["volume"] = cur.get("volume", 0) + s
                cur["timestamp"] = ts
    st.session_state.live_current_bar = cur if cur else None


def build_live_df():
    rows = list(st.session_state.live_bars)
    if st.session_state.live_current_bar:
        rows.append(st.session_state.live_current_bar)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["timestamp"], utc=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(EASTERN)
    df = df.drop(columns=["timestamp"], errors="ignore")
    needed = ["open", "high", "low", "close", "volume"]
    if not all(c in df.columns for c in needed):
        return pd.DataFrame()
    df = df[needed].sort_index()
    df = df[(df.index.time >= dtime(9, 30)) & (df.index.time <= dtime(16, 0))]
    df["vwap"] = compute_vwap(df)
    return df


# ══════════════════════════════════════════════════════════════════════════════
# TRADE JOURNAL
# ══════════════════════════════════════════════════════════════════════════════

import csv
import os

JOURNAL_PATH = "trade_journal.csv"
_JOURNAL_COLS = [
    "timestamp", "ticker", "price", "structure", "tcs", "rvol",
    "ib_high", "ib_low", "notes", "grade", "grade_reason",
    "source", "entry_price", "exit_price", "pnl_pct", "win_loss",
    "followed_plan", "deviation_notes", "transcript", "audio_b64",
    "voice_signals",
    "process_grade", "process_grade_reason",
]


def load_journal(user_id: str = "") -> "pd.DataFrame":
    """Load the trade journal from Supabase, optionally filtered by user_id."""
    if not supabase:
        return pd.DataFrame(columns=_JOURNAL_COLS)
    try:
        q = supabase.table("trade_journal").select("*")
        if user_id:
            try:
                q = q.eq("user_id", user_id)
            except Exception:
                pass
        response = q.execute()
        data = response.data
        if not data:
            return pd.DataFrame(columns=_JOURNAL_COLS)
        df = pd.DataFrame(data)
        for col in _JOURNAL_COLS:
            if col not in df.columns:
                df[col] = ""
        # Return all columns so cognitive/voice memo fields are preserved
        return df
    except Exception as e:
        print(f"Database read error (journal): {e}")
        return pd.DataFrame(columns=_JOURNAL_COLS)


def save_journal_entry(entry: dict, user_id: str = ""):
    """Save a new trade journal entry to Supabase."""
    if not supabase:
        print("Error: Supabase not connected.")
        return
    try:
        row = {k: entry.get(k, None) for k in _JOURNAL_COLS}
        if user_id:
            row["user_id"] = user_id
        supabase.table("trade_journal").insert(row).execute()
    except Exception as e:
        print(f"Database write error (journal): {e}")


_VJ_SIGNAL_KEYS = [
    "fomo_entry", "panic_exit", "thesis_drift", "revenge_trade",
    "oversized", "high_stress_language", "held_drawdown", "followed_plan",
    "scaled_exits", "key_level_ref", "setup_named", "adapted_tape",
]

_VJ_SIGNAL_LABELS = {
    "fomo_entry":           "FOMO Entry",
    "panic_exit":           "Panic Exit",
    "thesis_drift":         "Thesis Drift",
    "revenge_trade":        "Revenge Trade",
    "oversized":            "Oversized",
    "high_stress_language": "High Stress Language",
    "held_drawdown":        "Held Drawdown",
    "followed_plan":        "Followed Plan",
    "scaled_exits":         "Scaled Exits",
    "key_level_ref":        "Key Level Reference",
    "setup_named":          "Setup Named",
    "adapted_tape":         "Adapted to Tape",
}

_VJ_POSITIVE_SIGNALS = frozenset(
    {"followed_plan", "scaled_exits", "key_level_ref", "setup_named", "adapted_tape", "held_drawdown"}
)
_VJ_NEGATIVE_SIGNALS = frozenset(
    {"fomo_entry", "panic_exit", "thesis_drift", "revenge_trade", "oversized", "high_stress_language"}
)


def transcribe_audio_bytes(audio_bytes: bytes, suffix: str = ".wav") -> str:
    """Transcribe raw audio bytes using Whisper.

    Returns the transcript string, or an empty string if transcription fails or
    OPENAI_API_KEY is not configured.
    """
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        return ""
    try:
        import openai as _openai
        import tempfile as _tempfile
        client = _openai.OpenAI(api_key=openai_key)
        with _tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        with open(tmp_path, "rb") as f:
            result = client.audio.transcriptions.create(model="whisper-1", file=f)
        os.unlink(tmp_path)
        return result.text
    except Exception as e:
        print(f"transcribe_audio_bytes error: {e}")
        return ""


def ai_extract_signals(transcript: str) -> dict:
    """Extract 12 behavioral trading signals from a transcript using GPT-4.

    Returns a dict mapping each signal key to a bool. Returns an empty dict
    if the API key is missing or the call fails.
    """
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key or not transcript.strip():
        return {}
    try:
        import openai as _openai
        import json as _json
        import re as _re
        client = _openai.OpenAI(api_key=openai_key)
        prompt = f"""You are a behavioral trading analyst. Read this trader's live trade commentary and identify which behavioral signals are present.

TRANSCRIPT:
{transcript[:6000]}

Return a JSON object with ONLY these keys, each value true or false:
fomo_entry, panic_exit, thesis_drift, revenge_trade, oversized, high_stress_language,
held_drawdown, followed_plan, scaled_exits, key_level_ref, setup_named, adapted_tape

Definitions:
- fomo_entry: trader entered before planned level or chased price
- panic_exit: exited early due to fear or drawdown without thesis change
- thesis_drift: changed plan or reasoning mid-trade
- revenge_trade: entered a new trade immediately after a losing trade out of frustration
- oversized: took a position too large relative to their stated conviction
- high_stress_language: expressed frustration, panic, or emotional distress verbally
- held_drawdown: held through a drawdown with thesis intact and conviction
- followed_plan: executed exactly as they pre-planned
- scaled_exits: took partial profits in a systematic, planned way
- key_level_ref: referenced specific price levels, volume nodes, or structure levels
- setup_named: named the pattern or structure type before entering
- adapted_tape: changed approach when market conditions changed, showing regime awareness

Return ONLY valid JSON, no explanation."""
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        raw = _re.sub(r"```json|```", "", raw).strip()
        return _json.loads(raw)
    except Exception as e:
        print(f"ai_extract_signals error: {e}")
        return {}


def update_journal_process_grade(
    entry_id, followed_plan: str, deviation_notes: str
) -> bool:
    """Update only the process-grade fields on an existing journal entry."""
    if not supabase:
        print("Error: Supabase not connected.")
        return False
    try:
        patch = {
            "followed_plan": followed_plan,
            "deviation_notes": deviation_notes,
        }
        supabase.table("trade_journal").update(patch).eq("id", entry_id).execute()
        return True
    except Exception as e:
        print(f"Database write error (update_journal_process_grade): {e}")
        return False


def ensure_telegram_columns() -> bool:
    """Add Telegram-logging columns to trade_journal if they don't exist.
    Safe to call on every bot startup — uses IF NOT EXISTS.
    Returns True on success."""
    if not supabase:
        return False
    cols = [
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual'",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS entry_price FLOAT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS exit_price FLOAT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS win_loss TEXT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS pnl_pct FLOAT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS dedup_key TEXT",
    ]
    try:
        for sql in cols:
            supabase.rpc("exec_sql", {"query": sql}).execute()
        return True
    except Exception:
        try:
            supabase.table("trade_journal").select("source,entry_price,exit_price,win_loss,pnl_pct,dedup_key").limit(1).execute()
            return True
        except Exception as e:
            print(f"ensure_telegram_columns warning: {e}")
            return False


def ensure_cognitive_columns() -> bool:
    """Add cognitive profiling columns to trade_journal if they don't exist.
    Safe to call on startup — uses IF NOT EXISTS."""
    if not supabase:
        return False
    cols = [
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS transcript TEXT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS cognitive_tags JSONB",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS entry_quality TEXT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS behavioral_summary TEXT",
    ]
    try:
        for sql in cols:
            supabase.rpc("exec_sql", {"query": sql}).execute()
        return True
    except Exception:
        return False


def update_journal_process_grade(row_id, followed_plan: str, deviation_notes: str, user_id: str = "") -> bool:
    """Update the followed_plan and deviation_notes on an existing trade_journal row.

    Parameters
    ----------
    row_id        : the Supabase row id (integer primary key) of the journal entry
    followed_plan : "yes" or "no"
    deviation_notes: free-text explanation when plan was not followed
    user_id       : optional user_id for safety filter
    Returns True on success, False on failure.
    """
    if not supabase:
        return False
    try:
        q = supabase.table("trade_journal").update({
            "followed_plan": followed_plan,
            "deviation_notes": deviation_notes,
        }).eq("id", row_id)
        if user_id:
            q = q.eq("user_id", user_id)
        q.execute()
        return True
    except Exception as e:
        print(f"update_journal_process_grade error: {e}")
        return False


def ensure_process_columns() -> bool:
    """Add process-quality columns to trade_journal if they don't exist.
    Safe to call on startup — uses IF NOT EXISTS."""
    if not supabase:
        return False
    cols = [
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS followed_plan TEXT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS deviation_notes TEXT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS process_grade TEXT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS process_grade_reason TEXT",
    ]
    try:
        for sql in cols:
            supabase.rpc("exec_sql", {"query": sql}).execute()
        return True
    except Exception:
        try:
            supabase.table("trade_journal").select(
                "followed_plan,deviation_notes,process_grade,process_grade_reason"
            ).limit(1).execute()
            return True
        except Exception as e:
            print(f"ensure_process_columns warning: {e}")
            return False


def extract_cognitive_tags(transcript: str) -> dict:
    """Rule-based cognitive tag extraction from a voice memo transcript.

    Returns a dict of boolean flags and string labels capturing the
    trader's behavioral patterns during the trade.
    """
    t = transcript.lower()

    fomo_keywords    = ["fomo", "chased", "bad entry", "jumped in", "chasing", "i fomo"]
    hold_keywords    = ["held", "still in", "conviction", "trust it", "i trust", "full confidence", "good faith"]
    drift_keywords   = ["now i think", "actually", "changed my", "but actually", "pivot", "different plan"]
    scale_keywords   = ["50%", "take some", "partial", "scale", "half", "took off"]
    volume_keywords  = ["volume", "rvol", "relative vol", "volume picking up", "volume not div"]
    research_keywords= ["didn't research", "no research", "didn't screen", "no screening",
                        "didn't do any screen", "didn't even", "didn't look"]
    stress_keywords  = ["holy shit", "shit", "scared", "nervous", "oh shit", "not good",
                        "this is bad", "uh oh", "panic"]
    level_keywords   = ["support", "resistance", "zone", "level", "ib high", "ib low", "poc",
                        "vwap", "key level", "orange line", "trend line"]

    def _hit(keywords): return any(k in t for k in keywords)

    fomo_entry          = _hit(fomo_keywords)
    held_under_pressure = _hit(hold_keywords) and ("down" in t or "loss" in t or "losing" in t)
    thesis_drift        = _hit(drift_keywords)
    scaled_exits        = _hit(scale_keywords)
    volume_conviction   = _hit(volume_keywords)
    no_premarket_research = _hit(research_keywords)
    high_stress         = _hit(stress_keywords)
    used_key_levels     = _hit(level_keywords)

    if fomo_entry:
        entry_quality = "fomo"
    elif "planned" in t or "waited" in t or "i was waiting" in t:
        entry_quality = "planned"
    else:
        entry_quality = "reactive"

    flags = []
    if fomo_entry:            flags.append("fomo_entry")
    if held_under_pressure:   flags.append("held_under_pressure")
    if thesis_drift:          flags.append("thesis_drift")
    if scaled_exits:          flags.append("scaled_exits")
    if volume_conviction:     flags.append("volume_conviction")
    if no_premarket_research: flags.append("no_premarket_research")
    if high_stress:           flags.append("high_stress_language")
    if used_key_levels:       flags.append("used_key_levels")

    return {
        "fomo_entry":             fomo_entry,
        "held_under_pressure":    held_under_pressure,
        "thesis_drift":           thesis_drift,
        "scaled_exits":           scaled_exits,
        "volume_conviction":      volume_conviction,
        "no_premarket_research":  no_premarket_research,
        "high_stress":            high_stress,
        "used_key_levels":        used_key_levels,
        "entry_quality":          entry_quality,
        "flags":                  flags,
    }


def _build_behavioral_summary(tags: dict, ticker: str, win_loss: str) -> str:
    """Build a plain-English one-paragraph behavioral summary from extracted tags."""
    lines = []

    eq = tags.get("entry_quality", "reactive")
    if eq == "fomo":
        lines.append(f"FOMO entry on {ticker} — entered without waiting for the planned level.")
    elif eq == "planned":
        lines.append(f"Planned entry on {ticker} — waited for the setup before committing.")
    else:
        lines.append(f"Reactive entry on {ticker} — entered based on real-time read.")

    if tags.get("held_under_pressure"):
        lines.append("Held through drawdown with conviction — did not panic-exit.")
    if tags.get("volume_conviction"):
        lines.append("Volume was the primary hold thesis — not capitulated when price dipped.")
    if tags.get("thesis_drift"):
        lines.append("Thesis shifted mid-trade — original plan evolved as new data came in.")
    if tags.get("scaled_exits"):
        lines.append("Scaled out of the position — did not exit all at once.")
    if tags.get("no_premarket_research"):
        lines.append("No pre-market research done — trade was identified and executed same session.")
    if tags.get("high_stress"):
        lines.append("High emotional language detected — stress or excitement peaked during the trade.")
    if tags.get("used_key_levels"):
        lines.append("Referenced specific price levels and zones throughout the trade.")

    outcome = "Win" if win_loss and win_loss.lower() in ("win", "w", "profit") else win_loss or "Unknown"
    lines.append(f"Outcome: {outcome}.")

    return " ".join(lines)


def log_voice_memo(
    transcript:      str,
    ticker:          str,
    trade_date:      str,
    entry_price:     float,
    exit_price:      float,
    pnl_pct:         float,
    win_loss:        str  = "Win",
    user_id:         str  = "",
    notes_extra:     str  = "",
    followed_plan:   str  = "yes",
    deviation_notes: str  = "",
) -> dict:
    """Full pipeline: extract cognitive tags from transcript → save to trade_journal.

    Returns: {saved: bool, tags: dict, behavioral_summary: str, error: str|None}
    """
    if not supabase:
        return {"saved": False, "tags": {}, "behavioral_summary": "", "error": "No Supabase"}

    try:
        ensure_cognitive_columns()

        tags     = extract_cognitive_tags(transcript)
        summary  = _build_behavioral_summary(tags, ticker, win_loss)

        import json as _json
        from datetime import datetime as _dt

        # Auto-detect plan deviation from transcript keywords.
        # If the transcript contains clear deviation signals, override followed_plan
        # to "no" regardless of the default value passed in.
        _deviation_signals = {"fomo_entry", "thesis_drift", "no_premarket_research"}
        if any(tags.get(sig) for sig in _deviation_signals):
            followed_plan = "no"

        notes = f"[Voice Memo] {notes_extra}" if notes_extra else "[Voice Memo]"

        row = {
            "user_id":            user_id,
            "ticker":             ticker.upper(),
            "timestamp":          f"{trade_date}T00:00:00",
            "source":             "voice_memo",
            "entry_price":        round(float(entry_price), 4),
            "exit_price":         round(float(exit_price), 4),
            "win_loss":           win_loss,
            "pnl_pct":            round(float(pnl_pct), 4),
            "notes":              notes,
            "transcript":         transcript,
            "cognitive_tags":     _json.dumps(tags),
            "entry_quality":      tags.get("entry_quality", "reactive"),
            "behavioral_summary": summary,
            "grade":              "A" if win_loss == "Win" else "F",
            "grade_reason":       f"Voice memo — P&L: {pnl_pct:+.2f}%",
            "dedup_key":          f"voice_{ticker.upper()}_{trade_date}",
            "followed_plan":      followed_plan.lower() if followed_plan else "yes",
            "deviation_notes":    deviation_notes if followed_plan and followed_plan.lower() == "no" else "",
        }

        existing = (
            supabase.table("trade_journal")
            .select("id")
            .eq("user_id", user_id)
            .eq("dedup_key", row["dedup_key"])
            .execute()
        )
        if existing.data:
            return {"saved": False, "tags": tags, "behavioral_summary": summary,
                    "error": "Duplicate — already logged"}

        supabase.table("trade_journal").insert(row).execute()
        return {
            "saved": True,
            "tags": tags,
            "behavioral_summary": summary,
            "followed_plan": followed_plan.lower() if followed_plan else "yes",
            "error": None,
        }

    except Exception as e:
        return {"saved": False, "tags": {}, "behavioral_summary": "", "error": str(e)}


def save_telegram_trade(ticker: str, win_loss: str, entry_price: float,
                        exit_price: float, notes: str = "",
                        user_id: str = "", trade_date=None) -> dict:
    """Insert a Telegram-logged trade into trade_journal with dedup protection.

    Returns dict: {saved: bool, duplicate: bool, pnl_pct: float, error: str|None}
    """
    if not supabase:
        return {"saved": False, "duplicate": False, "pnl_pct": 0.0,
                "error": "Supabase not connected"}
    try:
        from datetime import date as _date, datetime as _dt
        import math

        today_str  = str(trade_date or _date.today())
        entry_p    = round(float(entry_price), 4)
        exit_p     = round(float(exit_price), 4)
        pnl_pct    = round((exit_p - entry_p) / entry_p * 100, 2) if entry_p != 0 else 0.0
        dedup_key  = f"{ticker.upper()}_{today_str}_{entry_p}_{exit_p}"

        # Dedup check — prefer dedup_key column, fall back to grade_reason prefix
        _grade_reason_key = f"tg|{dedup_key}"
        try:
            existing = (supabase.table("trade_journal")
                        .select("id")
                        .eq("dedup_key", dedup_key)
                        .execute())
        except Exception:
            existing = (supabase.table("trade_journal")
                        .select("id")
                        .eq("grade_reason", _grade_reason_key)
                        .execute())
        if existing.data:
            return {"saved": False, "duplicate": True,
                    "pnl_pct": pnl_pct, "error": None}

        # Dedup via grade_reason when dedup_key column may not exist yet
        _grade_reason = f"tg|{dedup_key}"

        # Packed notes: "[Entry: X → Exit: Y | Win | +Z%] user note"
        sign = "+" if pnl_pct >= 0 else ""
        _packed_notes = (
            f"[Entry: {entry_p} → Exit: {exit_p} | "
            f"{'Win' if win_loss.lower()=='win' else 'Loss'} | {sign}{pnl_pct:.1f}%]"
        )
        if notes:
            _packed_notes += f" {notes}"

        # Core row using always-existing columns
        row = {
            "timestamp":    _dt.utcnow().isoformat(),
            "ticker":       ticker.upper(),
            "price":        entry_p,
            "notes":        _packed_notes,
            "structure":    "",
            "tcs":          None,
            "rvol":         None,
            "ib_high":      None,
            "ib_low":       None,
            "grade":        "W" if win_loss.lower() == "win" else "L",
            "grade_reason": _grade_reason,
        }
        if user_id:
            row["user_id"] = user_id

        # Try to add extended columns — gracefully skip if they don't exist yet
        try:
            supabase.table("trade_journal").select("source").limit(1).execute()
            row["source"]      = "telegram"
            row["entry_price"] = entry_p
            row["exit_price"]  = exit_p
            row["win_loss"]    = win_loss.capitalize()
            row["pnl_pct"]     = pnl_pct
            row["dedup_key"]   = dedup_key
        except Exception:
            pass  # Extended columns not yet added — core columns still work

        supabase.table("trade_journal").insert(row).execute()
        return {"saved": True, "duplicate": False,
                "pnl_pct": pnl_pct, "error": None}

    except Exception as e:
        return {"saved": False, "duplicate": False,
                "pnl_pct": 0.0, "error": str(e)}


def backfill_unknown_structures(api_key: str, secret_key: str, user_id: str,
                                feed: str = "iex") -> dict:
    """Re-enrich journal rows where structure is Unknown/null/empty.

    Fetches the actual bar data for each affected row, runs enrich_trade_context,
    and patches the row in Supabase with the correct structure, tcs, rvol,
    ib_high, and ib_low values.

    Returns dict: {updated: int, failed: int, skipped: int, errors: list}
    """
    if not supabase:
        return {"updated": 0, "failed": 0, "skipped": 0, "errors": ["Supabase not connected"]}

    _STALE = {"Unknown", "unknown", "", None,
              "Trending Up", "Trending Down", "At IB High", "At IB Low", "Inside IB"}

    try:
        q = supabase.table("trade_journal").select("*")
        if user_id:
            q = q.eq("user_id", user_id)
        resp = q.execute()
        rows = resp.data or []
    except Exception as e:
        return {"updated": 0, "failed": 0, "skipped": 0, "errors": [str(e)]}

    targets = [r for r in rows if r.get("structure") in _STALE]
    if not targets:
        return {"updated": 0, "failed": 0, "skipped": len(rows), "errors": []}

    updated, failed, errors = 0, 0, []
    for row in targets:
        row_id   = row.get("id")
        ticker   = row.get("ticker", "")
        ts_raw   = row.get("timestamp", "")
        if not row_id or not ticker or not ts_raw:
            failed += 1
            continue
        try:
            from dateutil.parser import parse as _dp
            trade_dt = _dp(str(ts_raw)).date()
        except Exception:
            failed += 1
            continue
        try:
            ctx = enrich_trade_context(api_key, secret_key, ticker, trade_dt, feed=feed)
            if not ctx:
                failed += 1
                errors.append(f"{ticker} {trade_dt}: enrich returned empty")
                continue
            patch = {k: ctx[k] for k in ("structure", "tcs", "rvol", "ib_high", "ib_low")
                     if k in ctx and ctx[k] is not None}
            if not patch:
                failed += 1
                continue
            _extra_parts = []
            if ctx.get("gap_pct") is not None:
                _extra_parts.append(f"Gap: {ctx['gap_pct']:+.1f}%")
            if ctx.get("poc_price") is not None:
                _extra_parts.append(f"POC: ${ctx['poc_price']:.4f}")
            if ctx.get("top_pattern"):
                _pdir = ctx.get("top_pattern_direction", "")
                _pscore = ctx.get("top_pattern_score", 0)
                _extra_parts.append(
                    f"Pattern: {ctx['top_pattern']} ({_pdir}, {_pscore:.0%})")
            if _extra_parts:
                old_notes = row.get("notes", "") or ""
                patch["notes"] = old_notes + " | " + " | ".join(_extra_parts)
            supabase.table("trade_journal").update(patch).eq("id", row_id).execute()
            updated += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{ticker} {trade_dt}: {exc}")

    return {"updated": updated, "failed": failed,
            "skipped": len(rows) - len(targets), "errors": errors}


def parse_webull_csv(df: "pd.DataFrame") -> list:
    """Parse a Webull order-history CSV DataFrame into round-trip trade dicts.

    Handles multiple Webull export formats (column name variations).
    Pairs Buy→Sell using FIFO per ticker. Open positions (no matching sell)
    are silently skipped — they have not yet been closed.

    Returns a list of dicts compatible with save_journal_entry().
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    cols_lower = {c.lower(): c for c in df.columns}

    def _find(candidates):
        for cand in candidates:
            if cand in cols_lower:
                return cols_lower[cand]
        for cand in candidates:
            for col_l, col in cols_lower.items():
                if cand in col_l:
                    return col
        return None

    sym_col    = _find(["symbol", "sym.", "ticker", "sym", "stock"])
    side_col   = _find(["side", "b/s", "action", "type", "order side"])
    qty_col    = _find(["filled qty", "fill qty", "qty filled", "executed qty",
                         "filled", "qty", "quantity", "shares"])
    price_col  = _find(["avg price", "avg. price", "fill price", "exec price",
                         "filled price", "executed price", "price"])
    time_col   = _find(["create time", "filled time", "time placed", "order time",
                         "time", "date", "datetime"])
    status_col = _find(["status"])

    if not sym_col or not side_col or not qty_col or not price_col or not time_col:
        return []

    if status_col:
        df = df[df[status_col].astype(str).str.lower().str.contains("fill", na=False)]

    df["_side"] = df[side_col].astype(str).str.lower().str.strip()
    df = df[df["_side"].str.contains("buy|sell", na=False)]

    df["_qty"]   = pd.to_numeric(df[qty_col],   errors="coerce")
    df["_price"] = pd.to_numeric(df[price_col], errors="coerce")
    df["_time"]  = pd.to_datetime(df[time_col], errors="coerce", infer_datetime_format=True)
    df["_sym"]   = df[sym_col].astype(str).str.upper().str.strip()

    df = df.dropna(subset=["_qty", "_price", "_time", "_sym"]).sort_values("_time")

    buy_queues: dict = {}
    trades = []

    for _, row in df.iterrows():
        sym   = row["_sym"]
        side  = row["_side"]
        qty   = float(row["_qty"])
        price = float(row["_price"])
        ts    = row["_time"]

        if "buy" in side:
            buy_queues.setdefault(sym, []).append(
                {"time": ts, "price": price, "qty": qty, "remaining": qty}
            )

        elif "sell" in side:
            queue = buy_queues.get(sym, [])
            if not queue:
                continue

            qty_left       = qty
            entry_cost     = 0.0
            entry_qty_tot  = 0.0
            entry_price_wt = 0.0
            entry_time     = None

            while qty_left > 0 and queue:
                buy     = queue[0]
                matched = min(buy["remaining"], qty_left)
                entry_cost     += buy["price"] * matched
                entry_price_wt += buy["price"] * matched
                entry_qty_tot  += matched
                if entry_time is None:
                    entry_time = buy["time"]
                buy["remaining"] -= matched
                qty_left         -= matched
                if buy["remaining"] <= 0:
                    queue.pop(0)

            if entry_qty_tot == 0:
                continue

            avg_entry  = entry_price_wt / entry_qty_tot
            sell_total = price * qty
            pnl        = sell_total - entry_cost
            pnl_pct    = pnl / entry_cost * 100 if entry_cost > 0 else 0
            shares_int = int(round(entry_qty_tot))

            if pnl_pct > 5:
                grade = "A"
            elif pnl_pct > 1:
                grade = "B"
            elif pnl_pct > -2:
                grade = "C"
            elif pnl_pct > -5:
                grade = "D"
            else:
                grade = "F"

            trades.append({
                "timestamp":   entry_time.isoformat() if hasattr(entry_time, "isoformat") else str(entry_time),
                "ticker":      sym,
                "price":       round(avg_entry, 4),
                "entry_price": round(avg_entry, 4),
                "exit_price":  round(price, 4),
                "pnl_pct":     round(pnl_pct, 2),
                "win_loss":    "Win" if pnl_pct > 0 else "Loss",
                "source":      "manual",
                "mfe":         round(pnl, 2),
                "shares":      shares_int,
                "structure":   "Unknown",
                "tcs":         None,
                "rvol":        None,
                "ib_high":     None,
                "ib_low":      None,
                "exit_timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "notes": (
                    f"Webull import | Exit: ${price:.4f} | "
                    f"P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%) | "
                    f"Shares: {shares_int} | "
                    f"ExitTS: {ts.strftime('%Y-%m-%d %H:%M') if hasattr(ts, 'strftime') else ts}"
                ),
                "grade":        grade,
                "grade_reason": f"Auto-graded from P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)",
            })

    return trades


def compute_journal_model_crossref(journal_df: "pd.DataFrame",
                                   bt_df: "pd.DataFrame") -> dict:
    """Cross-reference personal trade journal against backtest model predictions.

    Joins on ticker + trade date.  Returns a dict with:
        matched_df   : rows where both journal entry and model prediction exist
        unmatched_n  : journal trades with no model prediction on that day
        by_structure : list of dicts {structure, trades, grades, d_f_pct, avg_pnl_est}
        filter_sim   : {blocked, allowed, d_f_blocked_pct, d_f_allowed_pct}
        alignment    : pct of D/F trades the model had flagged as Neutral/NtrlExtreme
    """
    import re

    empty = {
        "matched_df": pd.DataFrame(),
        "unmatched_n": 0,
        "by_structure": [],
        "filter_sim": {},
        "alignment": 0.0,
    }

    if journal_df is None or journal_df.empty:
        return empty
    if bt_df is None or bt_df.empty:
        return empty

    jdf = journal_df.copy()
    bdf = bt_df.copy()

    jdf["_ticker"] = jdf["ticker"].astype(str).str.upper().str.strip()
    jdf["_date"]   = pd.to_datetime(jdf["timestamp"], errors="coerce").dt.date.astype(str)

    bdf["_ticker"] = bdf["ticker"].astype(str).str.upper().str.strip()
    bdf["_date"]   = bdf["sim_date"].astype(str).str[:10]

    # Deduplicate backtest rows: multiple calibration runs (IEX → SIP) create
    # duplicate (ticker, date) entries.  Keep the row with the best TCS data so
    # the merge stays 1-to-1 with the journal.
    if "tcs" in bdf.columns:
        bdf = bdf.sort_values(
            by=["_ticker", "_date", "tcs"],
            ascending=[True, True, False],
            na_position="last",
        )
    else:
        bdf = bdf.sort_values(by=["_ticker", "_date"])
    bdf = bdf.drop_duplicates(subset=["_ticker", "_date"], keep="first").reset_index(drop=True)

    # Rename backtest columns that clash with journal column names (tcs, ib_high, ib_low)
    # so pandas merge doesn't silently rename them to _x/_y suffixes.
    _bt_rename = {"tcs": "bt_tcs", "ib_high": "bt_ib_high", "ib_low": "bt_ib_low"}
    bdf = bdf.rename(columns={k: v for k, v in _bt_rename.items() if k in bdf.columns})

    _PNL_RE = re.compile(r"P&L:\s*\$([\-\+]?[\d\.]+)")

    def _extract_pnl(notes_str):
        m = _PNL_RE.search(str(notes_str))
        return float(m.group(1)) if m else None

    jdf["_pnl_est"] = jdf["notes"].apply(_extract_pnl)

    _bt_cols = ["_ticker", "_date", "predicted", "bt_tcs", "win_loss",
                "follow_thru_pct", "bt_ib_high", "bt_ib_low", "open_price"]
    _bt_cols = [c for c in _bt_cols if c in bdf.columns]

    merged = jdf.merge(
        bdf[_bt_cols],
        on=["_ticker", "_date"],
        how="left",
    )

    unmatched_n = merged["predicted"].isna().sum()
    matched_df  = merged[merged["predicted"].notna()].copy()

    if matched_df.empty:
        return {**empty, "unmatched_n": int(unmatched_n)}

    _NEUTRAL_STRUCTS = {"Neutral", "Ntrl Extreme", "Ntrl_Extreme",
                        "Neutral Extreme", "NtrlExtreme"}

    by_structure = []
    for struct, grp in matched_df.groupby("predicted"):
        grades   = grp["grade"].fillna("?").tolist()
        df_count = sum(1 for g in grades if g in {"D", "F"})
        df_pct   = round(df_count / len(grades) * 100, 1) if grades else 0.0
        pnl_vals = grp["_pnl_est"].dropna().tolist()
        avg_pnl  = round(sum(pnl_vals) / len(pnl_vals), 2) if pnl_vals else None
        grade_counts = {}
        for g in grades:
            grade_counts[g] = grade_counts.get(g, 0) + 1
        by_structure.append({
            "structure":   struct,
            "trades":      len(grp),
            "grade_counts": grade_counts,
            "d_f_pct":     df_pct,
            "avg_pnl_est": avg_pnl,
        })
    by_structure.sort(key=lambda x: -x["trades"])

    is_neutral = matched_df["predicted"].isin(_NEUTRAL_STRUCTS)
    tcs_vals   = pd.to_numeric(matched_df.get("bt_tcs", pd.Series(dtype=float)),
                               errors="coerce")
    high_tcs   = tcs_vals >= 75

    would_block = is_neutral | (~high_tcs)
    blocked     = matched_df[would_block]
    allowed     = matched_df[~would_block]

    def _df_pct_of(df):
        if df.empty:
            return 0.0
        total = len(df)
        bad   = sum(1 for g in df["grade"].fillna("?") if g in {"D", "F"})
        return round(bad / total * 100, 1)

    filter_sim = {
        "blocked_n":       int(len(blocked)),
        "allowed_n":       int(len(allowed)),
        "d_f_blocked_pct": _df_pct_of(blocked),
        "d_f_allowed_pct": _df_pct_of(allowed),
        "pnl_blocked":     round(blocked["_pnl_est"].dropna().sum(), 2),
        "pnl_allowed":     round(allowed["_pnl_est"].dropna().sum(), 2),
    }

    df_trades = matched_df[matched_df["grade"].isin({"D", "F"})]
    if df_trades.empty:
        alignment = 0.0
    else:
        warned = df_trades["predicted"].isin(_NEUTRAL_STRUCTS).sum()
        alignment = round(warned / len(df_trades) * 100, 1)

    # ── Within-Neutral Quality Analysis ─────────────────────────────────────
    neutral_rows = matched_df[matched_df["predicted"].isin(_NEUTRAL_STRUCTS)].copy()
    neutral_quality: dict = {"tcs_buckets": [], "ib_position": [], "recommendation": ""}

    if not neutral_rows.empty:
        tcs_num = pd.to_numeric(neutral_rows.get("bt_tcs", pd.Series(dtype=float)),
                                errors="coerce")
        neutral_rows = neutral_rows.copy()
        neutral_rows["_tcs_num"] = tcs_num

        def _tcs_bucket(v):
            if pd.isna(v):    return "No TCS"
            if v < 40:        return "< 40 (Weak)"
            if v < 55:        return "40–55 (Moderate)"
            if v < 70:        return "55–70 (Strong)"
            return "70+ (Extreme)"

        _bucket_order = ["< 40 (Weak)", "40–55 (Moderate)", "55–70 (Strong)",
                         "70+ (Extreme)", "No TCS"]
        neutral_rows["_tcs_bucket"] = neutral_rows["_tcs_num"].apply(_tcs_bucket)

        tcs_buckets = []
        for bucket in _bucket_order:
            grp = neutral_rows[neutral_rows["_tcs_bucket"] == bucket]
            if grp.empty:
                continue
            grades = grp["grade"].fillna("?").tolist()
            ab_ct  = sum(1 for g in grades if g in {"A", "B"})
            df_ct  = sum(1 for g in grades if g in {"D", "F"})
            ab_pct = round(ab_ct / len(grades) * 100, 1)
            df_pct = round(df_ct / len(grades) * 100, 1)
            gc = {}
            for g in grades:
                gc[g] = gc.get(g, 0) + 1
            tcs_buckets.append({
                "bucket": bucket, "trades": len(grp),
                "ab_pct": ab_pct, "df_pct": df_pct, "grade_counts": gc,
            })
        neutral_quality["tcs_buckets"] = tcs_buckets

        if "bt_ib_high" in neutral_rows.columns and "bt_ib_low" in neutral_rows.columns:
            entry_price = pd.to_numeric(neutral_rows["price"], errors="coerce")
            ib_h = pd.to_numeric(neutral_rows["bt_ib_high"], errors="coerce")
            ib_l = pd.to_numeric(neutral_rows["bt_ib_low"],  errors="coerce")
            ib_range = (ib_h - ib_l).replace(0, pd.NA)

            def _ib_pos(row_tuple):
                ep, ih, il = row_tuple
                if pd.isna(ep) or pd.isna(ih) or pd.isna(il) or ih == il:
                    return "Unknown"
                margin = (ih - il) * 0.05
                if ep >= ih - margin and ep <= ih + margin:
                    return "At IB High"
                if ep >= il - margin and ep <= il + margin:
                    return "At IB Low"
                if il < ep < ih:
                    return "Inside IB"
                if ep > ih + margin:
                    return "Extended Above IB"
                return "Extended Below IB"

            neutral_rows["_ib_pos"] = list(map(
                _ib_pos,
                zip(entry_price, ib_h, ib_l),
            ))

            _ib_order = ["At IB High", "At IB Low", "Inside IB",
                         "Extended Above IB", "Extended Below IB", "Unknown"]
            ib_positions = []
            for pos in _ib_order:
                grp = neutral_rows[neutral_rows["_ib_pos"] == pos]
                if grp.empty:
                    continue
                grades = grp["grade"].fillna("?").tolist()
                ab_ct  = sum(1 for g in grades if g in {"A", "B"})
                df_ct  = sum(1 for g in grades if g in {"D", "F"})
                ab_pct = round(ab_ct / len(grades) * 100, 1)
                df_pct = round(df_ct / len(grades) * 100, 1)
                gc = {}
                for g in grades:
                    gc[g] = gc.get(g, 0) + 1
                ib_positions.append({
                    "position": pos, "trades": len(grp),
                    "ab_pct": ab_pct, "df_pct": df_pct, "grade_counts": gc,
                })
            neutral_quality["ib_position"] = ib_positions

        best_bucket = max(tcs_buckets, key=lambda x: x["ab_pct"] - x["df_pct"],
                          default=None) if tcs_buckets else None
        best_pos    = max(neutral_quality["ib_position"],
                          key=lambda x: x["ab_pct"] - x["df_pct"],
                          default=None) if neutral_quality["ib_position"] else None
        rec_parts = []
        if best_bucket:
            rec_parts.append(f"TCS in {best_bucket['bucket']} ({best_bucket['ab_pct']}% A/B rate)")
        if best_pos:
            rec_parts.append(f"entry at {best_pos['position']} ({best_pos['ab_pct']}% A/B rate)")
        if rec_parts:
            neutral_quality["recommendation"] = (
                "On Neutral days, best outcomes when: " + " AND ".join(rec_parts) + "."
            )

    return {
        "matched_df":      matched_df,
        "unmatched_n":     int(unmatched_n),
        "by_structure":    by_structure,
        "filter_sim":      filter_sim,
        "alignment":       alignment,
        "neutral_quality": neutral_quality,
    }


def fetch_live_quote(ticker: str) -> dict:
    """Fetch current price and today's volume via yfinance.
    Returns dict with keys: price, volume, error (None on success).
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.upper().strip())
        info = t.fast_info
        price  = float(info.last_price)  if info.last_price  else None
        volume = int(info.three_month_average_volume) if info.three_month_average_volume else None
        # prefer today's volume from 1d history
        hist = t.history(period="1d", interval="1m")
        if not hist.empty and "Volume" in hist.columns:
            volume = int(hist["Volume"].sum())
        if price is None:
            return {"price": None, "volume": None, "error": f"No data returned for '{ticker}'"}
        return {"price": round(price, 4), "volume": volume, "error": None}
    except Exception as e:
        return {"price": None, "volume": None, "error": str(e)}


def alpaca_kill_switch(api_key: str, secret_key: str, is_paper: bool = True) -> dict:
    """Cancel ALL open Alpaca orders and close ALL open positions.

    Returns a dict with keys:
        orders_cancelled  : int   — number of orders cancelled
        positions_closed  : int   — number of positions closed
        errors            : list  — any per-item error messages
        ok                : bool  — True if no errors at all
    """
    import requests as _req

    base = "https://paper-api.alpaca.markets" if is_paper else "https://api.alpaca.markets"
    headers = {
        "APCA-API-KEY-ID":     api_key,
        "APCA-API-SECRET-KEY": secret_key,
        "accept":              "application/json",
    }
    result = {"orders_cancelled": 0, "positions_closed": 0, "errors": [], "ok": True}

    # 1. Cancel all open orders (DELETE /v2/orders)
    try:
        r = _req.delete(f"{base}/v2/orders", headers=headers, timeout=10)
        if r.status_code in (200, 207):
            cancelled = r.json() if r.content else []
            if isinstance(cancelled, list):
                result["orders_cancelled"] = len(cancelled)
            else:
                result["orders_cancelled"] = 1
        elif r.status_code == 422:
            result["orders_cancelled"] = 0  # no open orders — fine
        else:
            result["errors"].append(f"Cancel orders HTTP {r.status_code}: {r.text[:120]}")
            result["ok"] = False
    except Exception as exc:
        result["errors"].append(f"Cancel orders exception: {exc}")
        result["ok"] = False

    # 2. Close all positions (DELETE /v2/positions — liquidates all immediately)
    try:
        r = _req.delete(f"{base}/v2/positions", headers=headers,
                        params={"cancel_orders": "true"}, timeout=10)
        if r.status_code in (200, 207):
            closed = r.json() if r.content else []
            if isinstance(closed, list):
                result["positions_closed"] = len(closed)
            else:
                result["positions_closed"] = 1
        elif r.status_code == 422:
            result["positions_closed"] = 0  # no open positions — fine
        else:
            result["errors"].append(f"Close positions HTTP {r.status_code}: {r.text[:120]}")
            result["ok"] = False
    except Exception as exc:
        result["errors"].append(f"Close positions exception: {exc}")
        result["ok"] = False

    return result


def fetch_alpaca_fills(api_key: str, secret_key: str,
                       is_paper: bool = True,
                       trade_date: str = None) -> tuple:
    """Fetch filled orders from Alpaca Trading REST API for a given date.

    Returns (fills_list, error_string).  error_string is None on success.
    """
    base = ("https://paper-api.alpaca.markets"
            if is_paper else "https://api.alpaca.markets")
    headers = {
        "APCA-API-KEY-ID":     api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }
    if trade_date is None:
        trade_date = datetime.now(EASTERN).strftime("%Y-%m-%d")

    params = {
        "status":    "closed",
        "after":     f"{trade_date}T00:00:00Z",
        "until":     f"{trade_date}T23:59:59Z",
        "limit":     200,
        "direction": "desc",
    }
    try:
        resp = requests.get(f"{base}/v2/orders",
                            headers=headers, params=params, timeout=12)
        if resp.status_code == 401:
            return [], "Authentication failed — check your API Key and Secret Key."
        if resp.status_code == 403:
            return [], "Access forbidden — are you using a paper key on a live endpoint (or vice versa)?"
        resp.raise_for_status()
        orders = resp.json()
        if isinstance(orders, dict) and "message" in orders:
            return [], orders["message"]
        filled = [o for o in orders if o.get("status") == "filled"]
        return filled, None
    except requests.exceptions.Timeout:
        return [], "Request timed out — Alpaca API did not respond in time."
    except Exception as exc:
        return [], str(exc)


def place_alpaca_bracket_order(
    ticker: str,
    ib_high: float,
    ib_low: float,
    direction: str,
    risk_dollars: float = 500.0,
    target_r: float = 2.0,
    is_paper: bool = True,
    api_key: str = "",
    secret_key: str = "",
) -> dict:
    """Place an IB-breakout bracket order on Alpaca.

    Bullish Break → buy stop at IB high | stop-loss at IB low | take-profit at IB high + 2×range
    Bearish Break → sell stop at IB low | stop-loss at IB high | take-profit at IB low − 2×range

    Risk is fixed at risk_dollars regardless of price — qty is computed so that
    one full stop-out = exactly risk_dollars lost.

    Returns dict with keys: ok (bool), order_id, qty, entry, stop, target, error.
    """
    ak = api_key  or ALPACA_API_KEY
    sk = secret_key or ALPACA_SECRET_KEY
    if not ak or not sk:
        return {"ok": False, "error": "Missing Alpaca credentials"}

    ib_range = round(ib_high - ib_low, 4)
    if ib_range <= 0:
        return {"ok": False, "error": f"Invalid IB range ({ib_range})"}

    # Alpaca requires penny increments (2 dp) for stocks ≥ $1.
    # Sub-penny prices cause HTTP 422 "minimum pricing criteria" errors.
    _pr = lambda x: round(x, 2)

    if direction == "Bullish Break":
        entry  = _pr(ib_high)
        stop   = _pr(ib_low)
        target = _pr(entry + target_r * ib_range)
        side   = "buy"
    elif direction == "Bearish Break":
        entry  = _pr(ib_low)
        stop   = _pr(ib_high)
        target = _pr(entry - target_r * ib_range)
        side   = "sell"
    else:
        return {"ok": False, "error": f"Unsupported direction: {direction}"}

    qty = max(1, int(risk_dollars / ib_range))

    base    = "https://paper-api.alpaca.markets" if is_paper else "https://api.alpaca.markets"
    headers = {
        "APCA-API-KEY-ID":     ak,
        "APCA-API-SECRET-KEY": sk,
        "Content-Type":        "application/json",
    }
    payload = {
        "symbol":        ticker.upper(),
        "qty":           str(qty),
        "side":          side,
        "type":          "stop",
        "time_in_force": "day",
        "stop_price":    str(entry),
        "order_class":   "bracket",
        "stop_loss":     {"stop_price": str(stop)},
        "take_profit":   {"limit_price": str(target)},
    }

    try:
        resp = requests.post(
            f"{base}/v2/orders",
            headers=headers,
            json=payload,
            timeout=10,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code in (200, 201):
            return {
                "ok":       True,
                "order_id": data.get("id", ""),
                "qty":      qty,
                "entry":    entry,
                "stop":     stop,
                "target":   target,
                "side":     side,
                "raw":      data,
            }
        msg = data.get("message") or data.get("error") or resp.text[:200]
        return {"ok": False, "error": f"HTTP {resp.status_code}: {msg}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_alpaca_account_equity(
    is_paper: bool = True,
    api_key: str = "",
    secret_key: str = "",
) -> float | None:
    """Return total portfolio equity from Alpaca account.

    Returns float (dollars) or None if the request fails.
    Used by the bot to compute dynamic 1% risk per trade.
    """
    ak = api_key   or ALPACA_API_KEY
    sk = secret_key or ALPACA_SECRET_KEY
    if not ak or not sk:
        return None
    base    = "https://paper-api.alpaca.markets" if is_paper else "https://api.alpaca.markets"
    headers = {"APCA-API-KEY-ID": ak, "APCA-API-SECRET-KEY": sk}
    try:
        resp = requests.get(f"{base}/v2/account", headers=headers, timeout=10)
        if resp.status_code == 200 and resp.content:
            data   = resp.json()
            equity = data.get("equity") or data.get("portfolio_value")
            return float(equity) if equity else None
        return None
    except Exception:
        return None


def cancel_alpaca_day_orders(
    is_paper: bool = True,
    api_key: str = "",
    secret_key: str = "",
) -> dict:
    """Cancel all open (unfilled) orders for today on Alpaca.

    Returns dict: cancelled (int), errors (int).
    """
    ak = api_key  or ALPACA_API_KEY
    sk = secret_key or ALPACA_SECRET_KEY
    if not ak or not sk:
        return {"cancelled": 0, "errors": 0, "error": "Missing Alpaca credentials"}

    base    = "https://paper-api.alpaca.markets" if is_paper else "https://api.alpaca.markets"
    headers = {"APCA-API-KEY-ID": ak, "APCA-API-SECRET-KEY": sk}

    try:
        resp = requests.delete(f"{base}/v2/orders", headers=headers, timeout=10)
        if resp.status_code == 207:
            results   = resp.json() if resp.content else []
            cancelled = sum(1 for r in results if r.get("status") == 200)
            errors    = sum(1 for r in results if r.get("status") != 200)
            return {"cancelled": cancelled, "errors": errors}
        if resp.status_code == 200:
            return {"cancelled": 0, "errors": 0}
        return {"cancelled": 0, "errors": 1, "error": resp.text[:200]}
    except Exception as exc:
        return {"cancelled": 0, "errors": 1, "error": str(exc)}


def get_alpaca_open_positions(
    is_paper: bool = True,
    api_key: str = "",
    secret_key: str = "",
) -> list:
    """Return all open positions from Alpaca as a list of dicts."""
    ak = api_key  or ALPACA_API_KEY
    sk = secret_key or ALPACA_SECRET_KEY
    if not ak or not sk:
        return []
    base    = "https://paper-api.alpaca.markets" if is_paper else "https://api.alpaca.markets"
    headers = {"APCA-API-KEY-ID": ak, "APCA-API-SECRET-KEY": sk}
    try:
        resp = requests.get(f"{base}/v2/positions", headers=headers, timeout=10)
        return resp.json() if resp.status_code == 200 and resp.content else []
    except Exception:
        return []


def reconcile_alpaca_fills(
    trade_date: str,
    user_id: str,
    is_paper: bool = True,
    api_key: str = "",
    secret_key: str = "",
) -> dict:
    """Fetch today's filled orders from Alpaca and patch alpaca_fill_price
    and alpaca_exit_fill_price on matching paper_trades rows.

    - alpaca_fill_price      : entry fill (parent bracket order filled_avg_price)
    - alpaca_exit_fill_price : exit fill from a filled take-profit or stop-loss leg

    Matching is by ticker + trade_date + user_id.
    Returns dict: matched (int), unmatched (int), errors (int), exit_fills (int).
    """
    if not supabase:
        return {"matched": 0, "unmatched": 0, "errors": 0, "exit_fills": 0}

    fills, err = fetch_alpaca_fills(
        api_key=api_key or ALPACA_API_KEY,
        secret_key=secret_key or ALPACA_SECRET_KEY,
        is_paper=is_paper,
        trade_date=trade_date,
    )
    if err:
        return {"matched": 0, "unmatched": 0, "errors": 1, "error": err, "exit_fills": 0}

    matched = unmatched = errors = exit_fills = 0
    for fill in fills:
        sym        = (fill.get("symbol") or "").upper()
        fill_price = float(fill.get("filled_avg_price") or 0)
        if not sym or fill_price <= 0:
            continue

        # ── Extract exit fill from bracket legs ───────────────────────────────
        # Alpaca bracket orders carry child legs in the `legs` array.
        # Each leg has a `type` ("limit" = take-profit, "stop"/"stop_limit" = stop-loss)
        # and a `status`. When exactly one leg is filled the other is cancelled.
        exit_fill_price: float | None = None
        legs = fill.get("legs") or []
        for leg in legs:
            leg_status = (leg.get("status") or "").lower()
            if leg_status == "filled":
                leg_price = float(leg.get("filled_avg_price") or 0)
                if leg_price > 0:
                    exit_fill_price = leg_price
                    exit_fills += 1
                    break  # only one leg fills in a bracket

        patch: dict = {"alpaca_fill_price": fill_price}
        if exit_fill_price is not None:
            patch["alpaca_exit_fill_price"] = exit_fill_price

        try:
            (
                supabase.table("paper_trades")
                .update(patch)
                .eq("user_id", user_id)
                .eq("trade_date", trade_date)
                .eq("ticker", sym)
                .execute()
            )
            matched += 1
        except Exception:
            errors += 1

    return {"matched": matched, "unmatched": unmatched, "errors": errors, "exit_fills": exit_fills}


def match_fills_to_roundtrips(fills: list) -> list:
    """Match Alpaca buy+sell fills into round-trip trades.

    Groups fills by symbol, computes weighted-average entry/exit,
    and returns a list of trade summary dicts.
    """
    from collections import defaultdict
    by_sym = defaultdict(lambda: {"buys": [], "sells": []})

    for order in fills:
        sym        = (order.get("symbol") or "").upper()
        side       = order.get("side", "")
        fill_price = float(order.get("filled_avg_price") or 0)
        qty        = float(order.get("filled_qty") or 0)
        filled_at  = str(order.get("filled_at") or "")
        if fill_price <= 0 or qty <= 0:
            continue
        if side == "buy":
            by_sym[sym]["buys"].append({"price": fill_price, "qty": qty, "time": filled_at})
        elif side == "sell":
            by_sym[sym]["sells"].append({"price": fill_price, "qty": qty, "time": filled_at})

    results = []
    for sym, sides in by_sym.items():
        if not sides["buys"] or not sides["sells"]:
            continue
        total_buy_qty  = sum(b["qty"] for b in sides["buys"])
        total_sell_qty = sum(s["qty"] for s in sides["sells"])
        avg_entry = (sum(b["price"] * b["qty"] for b in sides["buys"])  / total_buy_qty)
        avg_exit  = (sum(s["price"] * s["qty"] for s in sides["sells"]) / total_sell_qty)
        matched_qty   = min(total_buy_qty, total_sell_qty)
        pnl_dollars   = (avg_exit - avg_entry) * matched_qty
        pnl_pct       = ((avg_exit - avg_entry) / avg_entry * 100) if avg_entry > 0 else 0.0
        win_loss      = "Win" if pnl_dollars > 0 else ("Loss" if pnl_dollars < 0 else "Breakeven")

        # Earliest fill time for display
        all_times = [b["time"] for b in sides["buys"]] + [s["time"] for s in sides["sells"]]
        earliest  = sorted(t for t in all_times if t)[:1]
        fill_time = earliest[0][:16].replace("T", " ") if earliest else ""

        results.append({
            "symbol":      sym,
            "avg_entry":   round(avg_entry, 4),
            "avg_exit":    round(avg_exit, 4),
            "qty":         matched_qty,
            "pnl_dollars": round(pnl_dollars, 4),
            "pnl_pct":     round(pnl_pct, 2),
            "win_loss":    win_loss,
            "fill_time":   fill_time,
        })

    results.sort(key=lambda r: r["fill_time"])
    return results


def save_trade_review(journal_row: dict, exit_price: float,
                      actual_structure: str, direction: str = "Long",
                      user_id: str = "") -> dict:
    """Calculate trade outcome and persist to accuracy_tracker.

    Parameters
    ----------
    journal_row      : row dict from trade_journal (must have 'ticker', 'price', 'structure')
    exit_price       : actual exit price entered by the user
    actual_structure : actual day structure the user observed
    direction        : "Long" or "Short"

    Returns
    -------
    dict with keys: win_loss, pnl_dollars, pnl_pct, correct_structure
    """
    entry_price = float(journal_row.get("price", 0.0))
    ticker      = str(journal_row.get("ticker", ""))
    predicted   = str(journal_row.get("structure", ""))

    if entry_price <= 0:
        return {"win_loss": "N/A", "pnl_dollars": 0.0, "pnl_pct": 0.0,
                "correct_structure": False, "error": "Invalid entry price"}

    pnl_dollars = (exit_price - entry_price) if direction == "Long" \
                  else (entry_price - exit_price)
    pnl_pct     = (pnl_dollars / entry_price) * 100
    win_loss    = "Win" if pnl_dollars > 0 else ("Loss" if pnl_dollars < 0 else "Breakeven")

    correct_structure = (
        _strip_emoji(predicted.lower()) in _strip_emoji(actual_structure.lower()) or
        _strip_emoji(actual_structure.lower()) in _strip_emoji(predicted.lower())
    )

    log_accuracy_entry(
        symbol      = ticker,
        predicted   = predicted,
        actual      = actual_structure,
        compare_key = "manual_review",
        entry_price = entry_price,
        exit_price  = exit_price,
        mfe         = round(pnl_dollars, 4),
        user_id     = user_id,
    )

    return {
        "win_loss":          win_loss,
        "pnl_dollars":       round(pnl_dollars, 4),
        "pnl_pct":           round(pnl_pct, 2),
        "correct_structure": correct_structure,
        "error":             None,
    }


def compute_trade_grade(rvol, tcs, price, ib_high, ib_low, structure_label, voice_signals=None):
    """Return (grade, reason, signal_meta) based on RVOL, TCS, price relative to IB, and behavioural signals.

    voice_signals, if provided, is a dict keyed by _VJ_SIGNAL_KEYS.  Positive signals
    (followed_plan, scaled_exits, etc.) can raise the base grade by one step; negative
    signals (fomo_entry, panic_exit, etc.) can lower it.  Hard disqualifiers (RVOL < 1.0
    or trend-inside-IB) are never overridden by behavioural data.

    signal_meta is a dict with keys:
      - "impact": "raised" | "lowered" | "noted_positive" | "noted_negative" | None
      - "pos_signals": list of human-readable labels for positive signals that fired
      - "neg_signals": list of human-readable labels for negative signals that fired
    """
    rvol_val = rvol if rvol is not None else 0.0
    is_trend  = "trend" in structure_label.lower()
    price_inside_ib = (
        (ib_low is not None and ib_high is not None) and (ib_low < price < ib_high)
    )
    price_above_ib = (ib_high is not None) and (price > ib_high)

    # F — disqualifying conditions first (cannot be rescued by behaviour)
    _empty_meta = {"impact": None, "pos_signals": [], "neg_signals": []}
    if rvol_val < 1.0:
        return "F", f"Grade F: Low-volume setup (RVOL {rvol_val:.1f}×) — unfavorable odds.", _empty_meta
    if is_trend and price_inside_ib:
        return "F", "Grade F: Trend attempt but price is still inside IB — no breakout confirmation.", _empty_meta

    # A — ideal setup
    if rvol_val > 4.0 and tcs > 70 and price_above_ib:
        base_grade = "A"
        base_reason = (f"RVOL {rvol_val:.1f}×, TCS {tcs:.0f}%, price above IB High — "
                       f"elite, high-conviction setup.")
    # B — solid
    elif rvol_val > 2.0 and tcs > 50:
        base_grade = "B"
        base_reason = (f"RVOL {rvol_val:.1f}×, TCS {tcs:.0f}% — solid participation "
                       f"with reasonable confidence.")
    # C — moderate
    elif (1.0 <= rvol_val <= 2.0) or (30 <= tcs <= 50):
        base_grade = "C"
        base_reason = (f"Moderate quality (RVOL {rvol_val:.1f}×, TCS {tcs:.0f}%) — "
                       f"acceptable but below ideal thresholds.")
    # F — catch-all low confidence
    else:
        base_grade = "F"
        base_reason = (f"Low confidence (RVOL {rvol_val:.1f}×, TCS {tcs:.0f}%) — "
                       f"avoid or reduce size significantly.")

    # ── Behavioural adjustment ────────────────────────────────────────────────
    _grade_order = ["F", "C", "B", "A"]
    signals = voice_signals or {}
    pos_hits = [k for k in _VJ_POSITIVE_SIGNALS if signals.get(k)]
    neg_hits = [k for k in _VJ_NEGATIVE_SIGNALS if signals.get(k)]
    net = len(pos_hits) - len(neg_hits)

    adjusted_grade = base_grade
    behaviour_note = ""

    if net >= 2 and base_grade != "A":
        idx = _grade_order.index(base_grade)
        adjusted_grade = _grade_order[min(idx + 1, len(_grade_order) - 1)]
        pos_labels = ", ".join(_VJ_SIGNAL_LABELS.get(k, k) for k in pos_hits)
        behaviour_note = f" Raised by positive behaviour ({pos_labels})."
    elif net <= -2 and base_grade != "F":
        idx = _grade_order.index(base_grade)
        adjusted_grade = _grade_order[max(idx - 1, 0)]
        neg_labels = ", ".join(_VJ_SIGNAL_LABELS.get(k, k) for k in neg_hits)
        behaviour_note = f" Lowered by negative behaviour ({neg_labels})."
    elif net > 0:
        pos_labels = ", ".join(_VJ_SIGNAL_LABELS.get(k, k) for k in pos_hits)
        behaviour_note = f" Positive signals noted: {pos_labels}."
    elif net < 0:
        neg_labels = ", ".join(_VJ_SIGNAL_LABELS.get(k, k) for k in neg_hits)
        behaviour_note = f" Negative signals noted: {neg_labels}."

    reason = f"Grade {adjusted_grade}: {base_reason}{behaviour_note}"

    if net >= 2 and base_grade != "A":
        _impact = "raised"
    elif net <= -2 and base_grade != "F":
        _impact = "lowered"
    elif net > 0:
        _impact = "noted_positive"
    elif net < 0:
        _impact = "noted_negative"
    else:
        _impact = None

    signal_meta = {
        "impact":      _impact,
        "pos_signals": [(_VJ_SIGNAL_LABELS.get(k, k)) for k in pos_hits],
        "neg_signals": [(_VJ_SIGNAL_LABELS.get(k, k)) for k in neg_hits],
    }
    return adjusted_grade, reason, signal_meta


def compute_process_grade(signals: dict) -> tuple:
    """Map 12 behavioral signals to a process grade (A/B/C/D/F) and reason.

    Positive signals (followed_plan, scaled_exits, setup_named, key_level_ref,
    adapted_tape, held_drawdown) each add +1 to the score.
    Risk signals (fomo_entry, panic_exit, revenge_trade, thesis_drift,
    oversized, high_stress_language) each subtract -1 from the score.

    Score → Grade:  >= 4 → A | >= 2 → B | >= 0 → C | >= -2 → D | < -2 → F

    Returns (grade, reason).
    """
    _pos = frozenset({
        "followed_plan", "scaled_exits", "key_level_ref",
        "setup_named", "adapted_tape", "held_drawdown",
    })
    _neg = frozenset({
        "fomo_entry", "panic_exit", "thesis_drift",
        "revenge_trade", "oversized", "high_stress_language",
    })
    _labels = {
        "fomo_entry":           "FOMO Entry",
        "panic_exit":           "Panic Exit",
        "thesis_drift":         "Thesis Drift",
        "revenge_trade":        "Revenge Trade",
        "oversized":            "Oversized",
        "high_stress_language": "High Stress Language",
        "held_drawdown":        "Held Drawdown",
        "followed_plan":        "Followed Plan",
        "scaled_exits":         "Scaled Exits",
        "key_level_ref":        "Key Level Reference",
        "setup_named":          "Setup Named",
        "adapted_tape":         "Adapted to Tape",
    }

    active_pos = [k for k in _pos if signals.get(k)]
    active_neg = [k for k in _neg if signals.get(k)]
    net = len(active_pos) - len(active_neg)

    if net >= 4:
        grade = "A"
    elif net >= 2:
        grade = "B"
    elif net >= 0:
        grade = "C"
    elif net >= -2:
        grade = "D"
    else:
        grade = "F"

    if not active_pos and not active_neg:
        reason = f"Process Grade {grade}: No behavioral signals detected — grade reflects neutral baseline."
    else:
        parts = []
        if active_pos:
            parts.append(f"Strengths: {', '.join(_labels[k] for k in active_pos)}")
        if active_neg:
            parts.append(f"Risk signals: {', '.join(_labels[k] for k in active_neg)}")
        reason = f"Process Grade {grade}: {' · '.join(parts)}."

    return grade, reason


_GRADE_COLORS = {"A": "#4caf50", "B": "#26a69a", "C": "#ffa726", "D": "#ef9a9a", "F": "#ef5350"}
_GRADE_SCORE  = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}


def fetch_snapshots_bulk(api_key, secret_key, tickers, feed="iex"):
    """Batch-fetch latest price + previous day's close for a list of tickers.

    Works during market hours AND after hours / weekends by cascading through
    every available data field on the snapshot object.

    Returns {sym: {"price": float, "prev_close": float}} for qualifying tickers.
    Raises on authentication / network errors so the caller can show them.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockSnapshotRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(api_key, secret_key)

    # ── Step 1: try snapshot endpoint ─────────────────────────────────────────
    snap_result = {}
    try:
        req   = StockSnapshotRequest(symbol_or_symbols=list(tickers), feed=feed)
        snaps = client.get_stock_snapshot(req)

        for sym, snap in snaps.items():
            try:
                # Price: latest_trade → latest_quote mid → daily_bar close
                price = None
                if getattr(snap, "latest_trade", None) and snap.latest_trade.price:
                    price = float(snap.latest_trade.price)
                if price is None and getattr(snap, "latest_quote", None):
                    q = snap.latest_quote
                    ask = getattr(q, "ask_price", None)
                    bid = getattr(q, "bid_price", None)
                    if ask and bid and ask > 0 and bid > 0:
                        price = (float(ask) + float(bid)) / 2
                if price is None and getattr(snap, "daily_bar", None) and snap.daily_bar.close:
                    price = float(snap.daily_bar.close)

                # Prev close: prev_daily_bar → fall back to daily_bar open
                prev_close = None
                if getattr(snap, "prev_daily_bar", None) and snap.prev_daily_bar.close:
                    prev_close = float(snap.prev_daily_bar.close)
                if prev_close is None and getattr(snap, "daily_bar", None) and snap.daily_bar.open:
                    prev_close = float(snap.daily_bar.open)

                if price and price > 0:
                    snap_result[sym] = {
                        "price":      price,
                        "prev_close": prev_close if prev_close and prev_close > 0 else price,
                    }
            except Exception:
                pass
    except Exception as snap_err:
        # Snapshot endpoint failed entirely — fall through to daily bars
        snap_err_str = str(snap_err)
        if any(k in snap_err_str.lower() for k in ("forbidden", "unauthorized", "403", "401")):
            raise  # bad credentials — surface immediately

    # ── Step 1b: if SIP snapshot returned empty, retry with IEX ───────────────
    if not snap_result and feed != "iex":
        try:
            req   = StockSnapshotRequest(symbol_or_symbols=list(tickers), feed="iex")
            snaps = client.get_stock_snapshot(req)
            for sym, snap in snaps.items():
                if sym in snap_result:
                    continue  # already have it
                try:
                    price = None
                    if getattr(snap, "latest_trade", None) and snap.latest_trade.price:
                        price = float(snap.latest_trade.price)
                    if price is None and getattr(snap, "latest_quote", None):
                        q = snap.latest_quote
                        ask = getattr(q, "ask_price", None)
                        bid = getattr(q, "bid_price", None)
                        if ask and bid and ask > 0 and bid > 0:
                            price = (float(ask) + float(bid)) / 2
                    if price is None and getattr(snap, "daily_bar", None) and snap.daily_bar.close:
                        price = float(snap.daily_bar.close)
                    prev_close = None
                    if getattr(snap, "prev_daily_bar", None) and snap.prev_daily_bar.close:
                        prev_close = float(snap.prev_daily_bar.close)
                    if prev_close is None and getattr(snap, "daily_bar", None) and snap.daily_bar.open:
                        prev_close = float(snap.daily_bar.open)
                    if price and price > 0:
                        snap_result[sym] = {
                            "price":      price,
                            "prev_close": prev_close if prev_close and prev_close > 0 else price,
                        }
                except Exception:
                    pass
        except Exception:
            pass

    if snap_result:
        return snap_result

    # ── Step 2: fallback — fetch last 5 daily bars for each ticker ─────────────
    # This path is used when both snapshot endpoints returned empty (e.g. after hours)
    daily_result = {}
    end_dt   = datetime.now(pytz.UTC)
    start_dt = end_dt - timedelta(days=10)

    for sym in tickers:
        try:
            req = StockBarsRequest(
                symbol_or_symbols=sym,
                timeframe=TimeFrame.Day,
                start=start_dt,
                end=end_dt,
                feed="iex",  # always use IEX for daily bar fallback
            )
            bars = client.get_stock_bars(req)
            df   = bars.df
            if df.empty:
                continue
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(sym, level="symbol")
            df = df.sort_index()
            if len(df) < 1:
                continue
            price      = float(df["close"].iloc[-1])
            prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else price
            if price > 0:
                daily_result[sym] = {"price": price, "prev_close": prev_close}
        except Exception:
            pass

    return daily_result


def fetch_premarket_vols(api_key, secret_key, ticker, trade_date,
                         lookback_days=10, feed="iex"):
    """Fetch today's pre-market volume + 10-day historical average.

    Pre-market window = 4:00 AM – 9:29 AM EST (regular extended hours).
    Returns (today_pm_vol: float, avg_hist_pm_vol: float | None).
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(api_key, secret_key)
    start_dt = EASTERN.localize(
        datetime(trade_date.year, trade_date.month, trade_date.day)
        - timedelta(days=lookback_days * 3)   # buffer for weekends / holidays
    )
    # Include up to 9:30 AM today to capture this morning's pre-market bars
    end_dt = EASTERN.localize(
        datetime(trade_date.year, trade_date.month, trade_date.day, 9, 30)
    )
    req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
                           start=start_dt, end=end_dt, feed=feed)
    bars = client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        return 0.0, None

    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level="symbol")
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(EASTERN)
    df = df.sort_index()

    # Filter to pre-market window: 4:00 AM – 9:29 AM
    df = df[(df.index.time >= dtime(4, 0)) & (df.index.time < dtime(9, 30))]
    df["_date"] = df.index.date
    daily_vols = df.groupby("_date")["volume"].sum()

    today_vol = float(daily_vols.get(trade_date, 0.0))
    hist_vols = daily_vols[daily_vols.index < trade_date].tail(lookback_days)
    avg_vol = float(hist_vols.mean()) if not hist_vols.empty else None

    return today_vol, avg_vol


def run_gap_scanner(api_key, secret_key, watchlist, trade_date, feed="iex",
                    min_price: float = 1.0, max_price: float = 50.0,
                    min_rvol: float = 0.0):
    """Run the full gap-scanner pipeline and return qualifying tickers by gap/RVOL.

    Pipeline:
      1. Batch-fetch snapshots (price + prev_close)
      2. Filter to configurable price range (default $1–$50)
      3. Fetch pre-market volumes + 10-day historical average per qualifying ticker
      4. Compute Gap % and Pre-Market RVOL
      5. Filter by min_rvol floor (when PM RVOL is available)
      6. Sort by absolute gap %, return all qualifying tickers (no hard cap)

    Args:
      min_rvol: Minimum PM RVOL threshold. Tickers with PM RVOL below this are
                filtered out. Default 0.0 (no filter). Recommended: 2.0.
                Only applied when PM data is available (SIP feed).

    Returns list of dicts: [{ticker, price, gap_pct, pm_vol, avg_pm_vol, pm_rvol}]
    Raises exceptions so the caller can surface them to the UI.
    """
    # Step 1 — batch snapshots (let exception propagate so UI can show the message)
    snaps = fetch_snapshots_bulk(api_key, secret_key, watchlist, feed=feed)

    if not snaps:
        raise ValueError(
            "No snapshot data returned. Check your API credentials and that the "
            "tickers exist on Alpaca."
        )

    # Step 2 — filter by configurable price range
    qualifying = {
        sym: d for sym, d in snaps.items()
        if d.get("price") is not None and min_price <= d["price"] <= max_price
    }
    filtered_out = [
        f"{sym} (${d['price']:.2f})" for sym, d in snaps.items()
        if d.get("price") is not None and not (min_price <= d["price"] <= max_price)
    ]
    if not qualifying:
        out_of_range = [s for s, d in snaps.items() if d.get("price") is not None]
        raise ValueError(
            f"All {len(out_of_range)} tickers are outside the ${min_price:.0f}–${max_price:.0f} scan range "
            f"({', '.join(out_of_range[:5])}). "
            "Adjust the price range filter or add different tickers."
        )

    # Step 3 & 4 — pre-market volume + compute metrics
    # On IEX (free tier) pre-market bars are unavailable — we gracefully degrade
    # to gap-only mode after the first subscription error.
    pm_data_available = True
    rows = []
    for sym, snap_data in qualifying.items():
        pm_vol, avg_pm_vol = 0.0, None
        if pm_data_available:
            try:
                pm_vol, avg_pm_vol = fetch_premarket_vols(
                    api_key, secret_key, sym, trade_date,
                    lookback_days=10, feed=feed)
            except Exception as _pm_err:
                err_str = str(_pm_err).lower()
                if "subscription" in err_str or "permit" in err_str or "sip" in err_str:
                    # Free-tier account — skip PM vol for all remaining tickers
                    pm_data_available = False
                # Any other error: leave pm_vol/avg_pm_vol as 0/None and continue

        price      = snap_data["price"]
        prev_close = snap_data["prev_close"]
        gap_pct    = ((price - prev_close) / prev_close * 100.0
                      if prev_close and prev_close > 0 else 0.0)
        pm_rvol    = (round(pm_vol / avg_pm_vol, 2)
                      if avg_pm_vol and avg_pm_vol > 0 else None)

        rows.append({
            "ticker":          sym,
            "price":           round(price, 2),
            "gap_pct":         round(gap_pct, 2),
            "pm_vol":          int(pm_vol),
            "avg_pm_vol":      round(avg_pm_vol, 0) if avg_pm_vol else None,
            "pm_rvol":         pm_rvol,
            "pm_data_available": pm_data_available,
        })

    # Step 5 — RVOL floor filter (only when PM data is available)
    rvol_filtered = []
    if min_rvol > 0 and pm_data_available:
        pre_count = len(rows)
        rows = [r for r in rows
                if r["pm_rvol"] is None or r["pm_rvol"] >= min_rvol]
        dropped = pre_count - len(rows)
        if dropped > 0:
            rvol_filtered = [f"Filtered {dropped} tickers below RVOL {min_rvol:.1f}x"]

    # Step 6 — sort by absolute gap %, then RVOL as tiebreaker
    rows.sort(key=lambda r: (
        abs(r["gap_pct"]),
        r["pm_rvol"] if r["pm_rvol"] is not None else -1,
    ), reverse=True)

    for r in rows:
        r["pm_data_available"] = pm_data_available

    return {"rows": rows, "filtered_out": filtered_out, "rvol_filtered": rvol_filtered}


def compute_pretrade_quality(
    api_key: str, secret_key: str,
    sym: str,
    trade_date,
    feed: str = "sip",
) -> dict:
    """Compute real-time pre-trade quality metrics for a single ticker.

    Uses today's bars (up to now).  IB is locked at 9:30–10:30 AM per the
    standard Volume Profile protocol.

    Returns a dict with keys:
        tcs, tcs_bucket, ib_high, ib_low, current_price, ib_position,
        tcs_ok, ib_ok, go_signal, ib_formed
    or {"error": <str>} on failure.
    """
    try:
        df = fetch_bars(api_key, secret_key, sym, trade_date, feed=feed)
        if df.empty or len(df) < 5:
            return {"error": "No bar data available"}

        # IB window: 9:30–10:30 AM
        ib_cutoff = df.index[0].replace(hour=10, minute=30, second=0)
        ib_df = df[df.index <= ib_cutoff]
        ib_formed = len(ib_df) >= 5  # IB needs at least 5 bars

        if ib_formed:
            ib_high, ib_low = compute_initial_balance(ib_df)
        else:
            ib_high, ib_low = compute_initial_balance(df)

        if not ib_high or not ib_low or ib_high == ib_low:
            return {"error": "IB could not be computed (insufficient range)"}

        _, vap, poc_price = compute_volume_profile(
            ib_df if ib_formed else df, num_bins=30
        )
        tcs = float(compute_tcs(ib_df if ib_formed else df, ib_high, ib_low, poc_price))

        current_price = float(df["close"].iloc[-1])

        # IB position — 5% of IB range as "at boundary" tolerance
        margin = (ib_high - ib_low) * 0.05
        if current_price >= ib_high + margin:
            ib_pos = "Extended Above IB"
        elif current_price <= ib_low - margin:
            ib_pos = "Extended Below IB"
        elif current_price <= ib_low + margin:
            ib_pos = "At IB Low"
        elif current_price >= ib_high - margin:
            ib_pos = "At IB High"
        else:
            ib_pos = "Inside IB"

        # TCS bucket
        if tcs < 40:
            tcs_bucket = "Weak"
        elif tcs < 55:
            tcs_bucket = "Moderate"
        elif tcs < 70:
            tcs_bucket = "Strong"
        else:
            tcs_bucket = "Extreme"

        # Derived rule from calibration: TCS 55–70 AND At IB Low → best outcomes
        tcs_ok = 55 <= tcs < 70
        ib_ok  = ib_pos == "At IB Low"

        return {
            "tcs":           round(tcs, 1),
            "tcs_bucket":    tcs_bucket,
            "ib_high":       round(ib_high, 2),
            "ib_low":        round(ib_low, 2),
            "current_price": round(current_price, 2),
            "ib_position":   ib_pos,
            "ib_formed":     ib_formed,
            "tcs_ok":        tcs_ok,
            "ib_ok":         ib_ok,
            "go_signal":     tcs_ok and ib_ok,
        }
    except Exception as e:
        return {"error": str(e)}


def enrich_trade_context(api_key: str, secret_key: str, ticker: str,
                         trade_date, feed: str = "iex") -> dict:
    """Retroactively compute full context for a historical trade.

    Called automatically during Webull CSV import so every journal entry has the
    same context fields as a live analysis.  Uses the FULL 7-structure classifier
    (classify_day_structure), computes gap%, detects chart patterns, and returns
    POC price — matching what the live analysis produces.

    Returns a dict with keys:
        tcs, rvol, ib_high, ib_low, structure, gap_pct, poc_price,
        top_pattern, top_pattern_score, top_pattern_direction
    Returns {} on any failure — safe; caller keeps whatever data it already has.
    """
    try:
        from datetime import date as _date, timedelta
        import requests as _req

        if hasattr(trade_date, "date"):
            trade_dt = trade_date.date()
        elif isinstance(trade_date, str):
            from dateutil.parser import parse as _dp
            trade_dt = _dp(trade_date).date()
        else:
            trade_dt = trade_date

        df = fetch_bars(api_key, secret_key, ticker, trade_dt, feed=feed)
        if df is None or df.empty or len(df) < 5:
            return {}

        ib_cutoff = df.index[0].replace(hour=10, minute=30, second=0)
        ib_df = df[df.index <= ib_cutoff]
        ib_formed = len(ib_df) >= 5

        ib_high, ib_low = compute_initial_balance(ib_df if ib_formed else df)
        if not ib_high or not ib_low or ib_high == ib_low:
            return {}

        bin_centers, vap, poc_price = compute_volume_profile(df, num_bins=100)

        tcs = float(compute_tcs(ib_df if ib_formed else df, ib_high, ib_low, poc_price))

        today_vol = float(df["volume"].sum())
        rvol = None
        avg_daily_vol = None
        gap_pct = None

        try:
            start_window = (trade_dt - timedelta(days=18)).isoformat()
            end_window   = trade_dt.isoformat()
            daily_url = (
                f"https://data.alpaca.markets/v2/stocks/{ticker}/bars"
                f"?timeframe=1Day&start={start_window}&end={end_window}"
                f"&feed={feed}&limit=14"
            )
            headers = {
                "APCA-API-KEY-ID":     api_key,
                "APCA-API-SECRET-KEY": secret_key,
            }
            resp = _req.get(daily_url, headers=headers, timeout=8)
            daily_bars = resp.json().get("bars", [])

            prior_bars = [
                b for b in daily_bars
                if b.get("t", "")[:10] != trade_dt.isoformat()
            ]
            prior_vols = [b["v"] for b in prior_bars if "v" in b]

            if prior_vols:
                avg_daily_vol = sum(prior_vols) / len(prior_vols)
                if today_vol > 0 and avg_daily_vol > 0:
                    rvol = round(today_vol / avg_daily_vol, 2)

            if prior_bars:
                prev_close = prior_bars[-1].get("c", 0)
                open_price = float(df["open"].iloc[0])
                if prev_close and prev_close > 0:
                    gap_pct = round((open_price - prev_close) / prev_close * 100, 2)
        except Exception:
            pass

        label, _color, _detail, _insight = classify_day_structure(
            df, bin_centers, vap, ib_high, ib_low, poc_price,
            avg_daily_vol=avg_daily_vol,
        )
        structure = label

        top_pattern = None
        top_pattern_score = None
        top_pattern_dir = None
        try:
            patterns = detect_chart_patterns(df, poc_price=poc_price,
                                             ib_high=ib_high, ib_low=ib_low)
            if patterns:
                top_pattern = patterns[0].get("name", "")
                top_pattern_score = patterns[0].get("score", 0)
                top_pattern_dir = patterns[0].get("direction", "")
        except Exception:
            pass

        return {
            "tcs":                   round(tcs, 1),
            "rvol":                  rvol,
            "ib_high":               round(ib_high, 2),
            "ib_low":                round(ib_low, 2),
            "structure":             structure,
            "poc_price":             round(poc_price, 4),
            "gap_pct":               gap_pct,
            "top_pattern":           top_pattern,
            "top_pattern_score":     round(top_pattern_score, 2) if top_pattern_score else None,
            "top_pattern_direction": top_pattern_dir,
        }

    except Exception:
        return {}


def _prior_trading_day(d) -> "date":
    """Return the last NYSE trading day strictly before `d`."""
    from datetime import timedelta
    candidate = d - timedelta(days=1)
    for _ in range(10):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return candidate


def fetch_key_levels(api_key: str, secret_key: str, ticker: str,
                     trade_date, entry_low=None, entry_high=None,
                     current_price=None, feed: str = "iex") -> dict:
    """Fetch structural key levels for setup brief confluence detection.

    Gathers four classes of price levels and checks each against the
    entry zone ([entry_low, entry_high]) for confluence:

    1. PDH / PDL / PDC — Prior day session High / Low / Close
    2. ONH / ONL      — Overnight pre-market High / Low (4:00–9:30 AM)
    3. Round numbers  — Psychologically significant levels near current price
    4. Liquidity pools — Swing highs/lows from prior day (stop clusters)

    Returns a dict with all levels and confluence annotations.
    On any API failure, returns an empty dict (brief still works, just no levels).
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    import numpy as np

    result = {
        "pdh": None, "pdl": None, "pdc": None,
        "onh": None, "onl": None, "on_vol": 0.0,
        "round_numbers": [],
        "swing_highs": [],
        "swing_lows": [],
        "confluence_notes": [],
        "has_confluence": False,
    }

    if hasattr(trade_date, "date"):
        trade_date = trade_date.date()
    prior_day = _prior_trading_day(trade_date)
    client    = StockHistoricalDataClient(api_key, secret_key)

    # ── 1. Prior Day OHLC (regular session) ──────────────────────────────────
    try:
        pd_mo = EASTERN.localize(datetime(prior_day.year, prior_day.month, prior_day.day, 9, 30))
        pd_mc = EASTERN.localize(datetime(prior_day.year, prior_day.month, prior_day.day, 16, 0))
        req_pd = StockBarsRequest(symbol_or_symbols=ticker,
                                  timeframe=TimeFrame.Minute,
                                  start=pd_mo, end=pd_mc, feed=feed)
        bars_pd = client.get_stock_bars(req_pd)
        df_pd   = bars_pd.df
        if not df_pd.empty:
            if isinstance(df_pd.index, pd.MultiIndex):
                df_pd = df_pd.xs(ticker, level="symbol")
            df_pd.index = pd.to_datetime(df_pd.index)
            if df_pd.index.tz is None:
                df_pd.index = df_pd.index.tz_localize("UTC")
            df_pd.index = df_pd.index.tz_convert(EASTERN)
            df_pd = df_pd.sort_index()
            df_pd = df_pd[(df_pd.index.time >= dtime(9, 30)) &
                          (df_pd.index.time <= dtime(16, 0))]
            if not df_pd.empty:
                result["pdh"] = round(float(df_pd["high"].max()), 4)
                result["pdl"] = round(float(df_pd["low"].min()), 4)
                result["pdc"] = round(float(df_pd["close"].iloc[-1]), 4)

                # ── Swing highs / lows (15-min aggregation for stability) ────
                df_15 = df_pd.resample("15min").agg(
                    {"high": "max", "low": "min", "close": "last", "volume": "sum"}
                ).dropna()
                if len(df_15) >= 5:
                    highs = df_15["high"].values
                    lows  = df_15["low"].values
                    sh = [float(highs[i]) for i in range(1, len(highs) - 1)
                          if highs[i] >= highs[i-1] and highs[i] >= highs[i+1]]
                    sl = [float(lows[i]) for i in range(1, len(lows) - 1)
                          if lows[i] <= lows[i-1] and lows[i] <= lows[i+1]]
                    result["swing_highs"] = sorted(set(round(v, 4) for v in sh), reverse=True)[:3]
                    result["swing_lows"]  = sorted(set(round(v, 4) for v in sl))[:3]
    except Exception as _e:
        print(f"fetch_key_levels prior day error: {_e}")

    # ── 2. Overnight / Pre-market Bars (4:00 AM – 9:29 AM trade_date) ────────
    try:
        on_start = EASTERN.localize(
            datetime(trade_date.year, trade_date.month, trade_date.day, 4, 0))
        on_end   = EASTERN.localize(
            datetime(trade_date.year, trade_date.month, trade_date.day, 9, 30))
        now_et = datetime.now(EASTERN)
        if on_end > now_et:
            on_end = min(on_end, now_et)
        if on_start < on_end:
            req_on = StockBarsRequest(symbol_or_symbols=ticker,
                                      timeframe=TimeFrame.Minute,
                                      start=on_start, end=on_end, feed=feed)
            bars_on = client.get_stock_bars(req_on)
            df_on   = bars_on.df
            if not df_on.empty:
                if isinstance(df_on.index, pd.MultiIndex):
                    df_on = df_on.xs(ticker, level="symbol")
                df_on.index = pd.to_datetime(df_on.index)
                if df_on.index.tz is None:
                    df_on.index = df_on.index.tz_localize("UTC")
                df_on.index = df_on.index.tz_convert(EASTERN)
                df_on = df_on.sort_index()
                df_on = df_on[(df_on.index.time >= dtime(4, 0)) &
                               (df_on.index.time < dtime(9, 30))]
                if not df_on.empty:
                    result["onh"]    = round(float(df_on["high"].max()), 4)
                    result["onl"]    = round(float(df_on["low"].min()), 4)
                    result["on_vol"] = float(df_on["volume"].sum())
    except Exception as _e:
        print(f"fetch_key_levels overnight error: {_e}")

    # ── 3. Round Numbers near current price ───────────────────────────────────
    ref_price = current_price or result["pdc"] or 1.0
    if ref_price > 0:
        if ref_price < 1.0:
            step = 0.25
        elif ref_price < 5.0:
            step = 0.50
        elif ref_price < 20.0:
            step = 1.00
        elif ref_price < 50.0:
            step = 5.00
        else:
            step = 10.00
        import math
        lo_rn = math.floor(ref_price / step) * step
        rounds = [round(lo_rn + i * step, 4)
                  for i in range(-4, 6)
                  if abs(lo_rn + i * step - ref_price) / ref_price <= 0.20
                  and lo_rn + i * step > 0]
        result["round_numbers"] = rounds

    # ── 4. Confluence detection ───────────────────────────────────────────────
    if entry_low is not None and entry_high is not None:
        mid = (entry_low + entry_high) / 2.0
        tol = max((entry_high - entry_low) * 0.5, mid * 0.01)  # ±1% or half zone

        def _near(level) -> bool:
            return level is not None and abs(level - mid) <= tol

        def _zone_overlap(level) -> bool:
            """Level falls inside or very near the entry zone."""
            return level is not None and (entry_low - tol) <= level <= (entry_high + tol)

        notes = []
        if _zone_overlap(result["pdh"]):
            notes.append(f"Entry near Prior Day High ${result['pdh']:.4f} — resistance overhead")
        if _zone_overlap(result["pdl"]):
            notes.append(f"Entry at Prior Day Low ${result['pdl']:.4f} — strong support floor")
        if _zone_overlap(result["pdc"]):
            notes.append(f"Entry near Prior Day Close ${result['pdc']:.4f} — acceptance level")
        if _zone_overlap(result["onh"]):
            notes.append(f"Entry at Overnight High ${result['onh']:.4f} — pre-market resistance")
        if _zone_overlap(result["onl"]):
            notes.append(f"Entry at Overnight Low ${result['onl']:.4f} — pre-market support floor")
        for sh in result["swing_highs"]:
            if _zone_overlap(sh):
                notes.append(f"Entry at prior swing high ${sh:.4f} — liquidity pool above")
        for sl in result["swing_lows"]:
            if _zone_overlap(sl):
                notes.append(f"Entry at prior swing low ${sl:.4f} — stop cluster below")
        for rn in result["round_numbers"]:
            if _zone_overlap(rn):
                notes.append(f"Round number ${rn:.2f} inside entry zone — psychological magnet")

        result["confluence_notes"] = notes
        result["has_confluence"]   = len(notes) > 0

    return result


def compute_setup_brief(api_key: str, secret_key: str, ticker: str,
                        pred_date, user_id: str = "", feed: str = "iex") -> dict:
    """Generate a full pre-market trade plan for one ticker on pred_date.

    Synthesizes all available signals into an actionable setup brief:
      - Structure prediction + brain confidence
      - Entry zone (from IB levels and/or detected pattern neckline)
      - Entry trigger (human-readable condition: price level + RVOL + time gate)
      - Stop level (from pattern geometry or IB Low floor)
      - Price targets R1/R2/R3 (from volume profile extensions)
      - User's personal win rate for this exact condition cluster

    The win_rate_pct and win_rate_context fields update automatically every
    time the brief is regenerated — no rebuild needed as more trades are logged.

    Returns a dict on success, {"error": str} on failure.
    """
    try:
        from datetime import date as _date

        if hasattr(pred_date, "date"):
            _dt = pred_date.date()
        elif isinstance(pred_date, str):
            from dateutil.parser import parse as _dp
            _dt = _dp(pred_date).date()
        else:
            _dt = pred_date

        # ── 1. Fetch intraday bars ────────────────────────────────────────────
        df = fetch_bars(api_key, secret_key, ticker, _dt, feed=feed)
        if df is None or df.empty or len(df) < 5:
            return {"error": "Insufficient bar data"}

        # ── 2. Volume profile and IB ──────────────────────────────────────────
        bin_centers, vap, poc_price = compute_volume_profile(df, num_bins=100)
        ib_high, ib_low = compute_initial_balance(df)
        if ib_high is None or ib_low is None:
            return {"error": "IB not formed yet"}
        ib_range = ib_high - ib_low

        # ── 3. TCS and IB position ───────────────────────────────────────────
        tcs = float(compute_tcs(df, ib_high, ib_low, poc_price))
        final_price = float(df["close"].iloc[-1])
        margin      = ib_range * 0.05
        if final_price >= ib_high + margin:
            ib_pos = "Extended Above IB"
        elif final_price <= ib_low - margin:
            ib_pos = "Extended Below IB"
        elif final_price <= ib_low + margin:
            ib_pos = "At IB Low"
        elif final_price >= ib_high - margin:
            ib_pos = "At IB High"
        else:
            ib_pos = "Inside IB"

        # ── 4. Pattern detection ──────────────────────────────────────────────
        patterns    = detect_chart_patterns(df, poc_price=poc_price,
                                            ib_high=ib_high, ib_low=ib_low)
        top_pattern = patterns[0] if patterns else None
        pattern_name     = top_pattern.get("name", "")    if top_pattern else ""
        pattern_neckline = top_pattern.get("neckline")    if top_pattern else None
        pattern_conf     = top_pattern.get("score", 0)   if top_pattern else 0
        # Parse head price from description string (e.g. "Head $0.28")
        import re as _re_sb
        pattern_head = None
        if top_pattern:
            _hm = _re_sb.search(r"Head \$([\d\.]+)", top_pattern.get("description", ""))
            if _hm:
                try:
                    pattern_head = float(_hm.group(1))
                except ValueError:
                    pattern_head = None

        # ── 5. RVOL + gap_pct ────────────────────────────────────────────────
        try:
            rvol_curve = build_rvol_intraday_curve(
                api_key, secret_key, ticker, _dt, lookback_days=10, feed=feed)
        except Exception:
            rvol_curve = None
        avg_vol, _prev_close_brief = fetch_daily_stats(api_key, secret_key, ticker, _dt)
        rvol = compute_rvol(df, intraday_curve=rvol_curve, avg_daily_vol=avg_vol)
        _open_px_brief = float(df["open"].iloc[0]) if not df.empty else None
        gap_pct = (
            round((_open_px_brief - _prev_close_brief) / _prev_close_brief * 100, 2)
            if _open_px_brief and _prev_close_brief and _prev_close_brief > 0 else None
        )
        rvol_band_label = _rvol_band(float(rvol)) if rvol else "Normal"

        # ── 6. Brain model prediction ─────────────────────────────────────────
        try:
            brain_pred = compute_model_prediction(df, rvol, tcs, sector_bonus=0.0)
            predicted_structure = brain_pred.get("label", ib_pos)
            brain_confidence    = float(brain_pred.get("confidence", 0.5)) * 100
        except Exception:
            predicted_structure = ib_pos
            brain_confidence    = 50.0

        # ── 7. Entry zone ─────────────────────────────────────────────────────
        _is_pattern_entry = (
            pattern_neckline is not None and
            any(k in pattern_name.lower() for k in ("head", "h&s", "reverse", "double"))
        )
        if _is_pattern_entry:
            # Pattern-based: neckline is the trigger; enter within 1% of neckline
            entry_low  = round(pattern_neckline * 0.990, 4)
            entry_high = round(pattern_neckline * 1.010, 4)
            trigger    = (f"Neckline reclaim ${pattern_neckline:.4f} "
                          f"with RVOL > 2× after 10:30 ET")
        elif ib_pos == "At IB Low":
            entry_low  = round(ib_low - ib_range * 0.02, 4)
            entry_high = round(ib_low + ib_range * 0.08, 4)
            trigger    = (f"Hold above IB Low ${ib_low:.4f} with RVOL > 2× "
                          f"after 10:30 ET — look for reclaim candle")
        elif ib_pos == "At IB High":
            entry_low  = round(ib_high - ib_range * 0.02, 4)
            entry_high = round(ib_high + ib_range * 0.05, 4)
            trigger    = (f"IB High ${ib_high:.4f} breakout + hold "
                          f"with RVOL > 2× after 10:30 ET")
        elif ib_pos == "Extended Above IB":
            vwap_val   = float(df["vwap"].iloc[-1]) if "vwap" in df.columns else final_price
            entry_low  = round(vwap_val * 0.990, 4)
            entry_high = round(vwap_val * 1.010, 4)
            trigger    = (f"Pullback to VWAP ${vwap_val:.4f} and reclaim "
                          f"with RVOL > 1.5× — momentum continuation entry")
        else:  # Inside IB / generic
            entry_low  = round(poc_price * 0.985, 4)
            entry_high = round(poc_price * 1.015, 4)
            trigger    = (f"Wait for IB break with RVOL > 2.5× after 10:30 ET — "
                          f"no edge inside IB without volume confirmation")

        # ── 8. Stop level ─────────────────────────────────────────────────────
        if pattern_head is not None:
            stop_level = round(float(pattern_head) * 0.995, 4)  # 0.5% below head
        elif ib_pos in ("At IB Low", "Extended Below IB"):
            stop_level = round(ib_low - ib_range * 0.15, 4)
        elif ib_pos == "At IB High":
            stop_level = round(ib_high - ib_range * 0.20, 4)
        else:
            stop_level = round(entry_low - (entry_high - entry_low) * 1.5, 4)

        # ── 9. Price targets from volume profile ──────────────────────────────
        tz_list = compute_target_zones(df, ib_high, ib_low, bin_centers, vap, tcs)
        # Collect upside target prices (above entry) sorted ascending
        target_prices = sorted(
            set(round(z["price"], 4) for z in tz_list if z["price"] > entry_high),
        )[:3]
        # Fallback targets from IB extensions if volume profile gave nothing
        if not target_prices:
            target_prices = [
                round(ib_high + ib_range * 1.0, 4),
                round(ib_high + ib_range * 1.5, 4),
                round(ib_high + ib_range * 2.0, 4),
            ]

        # ── 10. Key Levels: PDH/PDL/PDC, Overnight, Round Numbers, Liq Pools ─
        key_levels = {}
        try:
            key_levels = fetch_key_levels(
                api_key, secret_key, ticker, _dt,
                entry_low=entry_low, entry_high=entry_high,
                current_price=final_price, feed=feed,
            )
            # Enhance trigger string with confluence notes (first 2 max)
            if key_levels.get("has_confluence"):
                conf_notes = key_levels.get("confluence_notes", [])[:2]
                trigger = trigger + " ⭐ Confluence: " + " | ".join(conf_notes)
        except Exception as _kle:
            print(f"fetch_key_levels skipped: {_kle}")

        # ── 11. User's personal win rate for this condition ───────────────────
        win_rate_pct     = None
        win_rate_context = "No data yet — keep trading to build calibration."
        confidence_label = "LOW"
        try:
            if user_id:
                wr_data = compute_win_rates(user_id, min_samples=1)
                tcs_bucket = (
                    "Weak" if tcs < 40 else
                    "Moderate" if tcs < 55 else
                    "Strong" if tcs < 70 else "Elite"
                )
                edge_band_label = _edge_band(tcs)
                cluster_key = (
                    f"edge:{edge_band_label} "
                    f"rvol:{rvol_band_label} "
                    f"struct:{ib_pos}"
                )
                cluster = wr_data.get(cluster_key)
                if cluster and cluster.get("n", 0) >= 1:
                    wr_pct = cluster["win_rate"] * 100
                    n      = cluster["n"]
                    win_rate_pct     = round(wr_pct, 1)
                    win_rate_context = (
                        f"{ib_pos} + TCS {tcs_bucket} + RVOL {rvol_band_label}: "
                        f"{wr_pct:.0f}% win rate ({n} trade{'s' if n!=1 else ''})"
                    )
                    if wr_pct >= 75 and n >= 5:
                        confidence_label = "HIGH"
                    elif wr_pct >= 55 and n >= 3:
                        confidence_label = "MODERATE"
                    else:
                        confidence_label = "LOW"
                else:
                    # Fall back to structure-only
                    struct_data = wr_data.get("_by_struct", {}).get(ib_pos)
                    if struct_data and struct_data.get("n", 0) >= 1:
                        wr_pct = struct_data["win_rate"] * 100
                        n      = struct_data["n"]
                        win_rate_pct     = round(wr_pct, 1)
                        win_rate_context = (
                            f"{ib_pos}: {wr_pct:.0f}% win rate ({n} trade{'s' if n!=1 else ''}) "
                            f"— building {ib_pos} + TCS history"
                        )
                        confidence_label = "MODERATE" if wr_pct >= 55 else "LOW"
        except Exception:
            pass

        return {
            "ticker":            ticker,
            "pred_date":         str(_dt),
            "predicted_structure": predicted_structure,
            "brain_confidence":  round(brain_confidence, 1),
            "ib_position":       ib_pos,
            "tcs":               round(tcs, 1),
            "rvol":              rvol,
            "gap_pct":           gap_pct,
            "rvol_band":         rvol_band_label,
            "pattern":           pattern_name,
            "pattern_neckline":  pattern_neckline,
            "pattern_confidence": pattern_conf,
            "entry_zone_low":    entry_low,
            "entry_zone_high":   entry_high,
            "entry_trigger":     trigger,
            "stop_level":        stop_level,
            "targets":           target_prices,
            "win_rate_pct":      win_rate_pct,
            "win_rate_context":  win_rate_context,
            "confidence_label":  confidence_label,
            # Key levels
            "pdh":               key_levels.get("pdh"),
            "pdl":               key_levels.get("pdl"),
            "pdc":               key_levels.get("pdc"),
            "onh":               key_levels.get("onh"),
            "onl":               key_levels.get("onl"),
            "on_vol":            key_levels.get("on_vol", 0.0),
            "round_numbers":     key_levels.get("round_numbers", []),
            "swing_highs":       key_levels.get("swing_highs", []),
            "swing_lows":        key_levels.get("swing_lows", []),
            "confluence_notes":  key_levels.get("confluence_notes", []),
            "has_confluence":    key_levels.get("has_confluence", False),
        }

    except Exception as e:
        return {"error": str(e)}


def _parse_batch_pairs(text: str) -> list[tuple]:
    """Parse 'M/D: T1, T2, ...' lines into [(ticker, date), ...] for year 2026."""
    import re
    pairs = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        date_part, tickers_part = line.split(":", 1)
        date_part = date_part.strip()
        m = re.match(r"(\d{1,2})/(\d{1,2})", date_part)
        if not m:
            continue
        mo, dy = int(m.group(1)), int(m.group(2))
        try:
            trade_date = date(2026, mo, dy)
        except ValueError:
            continue
        for t in tickers_part.split(","):
            t = t.strip().upper()
            if t:
                pairs.append((t, trade_date))
    return pairs


def run_single_backtest(api_key, secret_key, ticker, trade_date, feed="iex", num_bins=100):
    """Full pipeline for one ticker/date: fetch → classify → brain → log."""
    result = {"ticker": ticker, "date": str(trade_date),
              "predicted": "—", "actual": "—", "correct": "—", "status": "OK"}
    try:
        df = fetch_bars(api_key, secret_key, ticker, trade_date, feed=feed)
        if df.empty or len(df) < 10:
            result["status"] = "No data"
            return result

        bin_centers, vap, poc_price = compute_volume_profile(df, num_bins)
        ib_high, ib_low = compute_initial_balance(df)
        if ib_high is None or ib_low is None:
            result["status"] = "No IB data"
            return result

        label, color, detail, insight = classify_day_structure(
            df, bin_centers, vap, ib_high, ib_low, poc_price
        )
        result["actual"] = label

        # Simulate MarketBrain with the full day's bars + rich signals
        brain = MarketBrain()
        try:
            _bt_ivp, _ = compute_ib_volume_stats(df, ib_high, ib_low)
        except Exception:
            _bt_ivp = None
        _bt_has_dd = _detect_double_distribution(bin_centers, vap) is not None
        brain.update(df, ib_vol_pct=_bt_ivp, poc_price=poc_price,
                     has_double_dist=_bt_has_dd)
        prediction = brain.prediction
        result["predicted"] = prediction

        if not brain.ib_set or prediction == "Analyzing IB…":
            result["status"] = "IB incomplete"
            return result

        # Build compare_key and dedup check
        ck = f"{ticker}_{trade_date}_{float(ib_high):.4f}_{float(ib_low):.4f}"
        if os.path.exists(TRACKER_FILE):
            try:
                _chk = pd.read_csv(TRACKER_FILE, encoding="utf-8")
                if "compare_key" in _chk.columns and (_chk["compare_key"] == ck).any():
                    result["status"] = "Already logged"
                    # Still show correct/wrong from existing row
                    _row = _chk[_chk["compare_key"] == ck]
                    if not _row.empty and "correct" in _row.columns:
                        result["correct"] = str(_row["correct"].iloc[0])
                    return result
            except Exception:
                pass

        log_accuracy_entry(ticker, prediction, label, compare_key=ck)
        result["correct"] = ("✅" if _strip_emoji(prediction) in _strip_emoji(label)
                             or _strip_emoji(label) in _strip_emoji(prediction) else "❌")
    except Exception as e:
        result["status"] = f"Error: {str(e)[:60]}"
    return result


# ── Analytics & Edge ──────────────────────────────────────────────────────────

def compute_edge_analytics(journal_df: pd.DataFrame,
                           tracker_df: pd.DataFrame) -> dict:
    """Join trade_journal + accuracy_tracker and compute full edge stats.

    Returns
    -------
    dict with keys:
      summary            – high-level KPIs
      equity_curve       – DataFrame (timestamp, symbol, mfe, cumulative_pnl)
      daily_pnl          – DataFrame (date, pnl, cumulative_pnl)
      win_rate_by_struct – DataFrame (structure, trades, wins, win_rate, avg_pnl)
      grade_distribution – dict {grade: count}
      tcs_edge           – DataFrame (tcs_bucket, trades, win_rate)
    """
    empty = {
        "summary": {
            "win_rate": 0.0, "total_pnl": 0.0, "avg_win": 0.0,
            "avg_loss": 0.0, "profit_factor": 0.0,
            "total_trades": 0, "trade_days": 0,
        },
        "equity_curve":        pd.DataFrame(),
        "daily_pnl":           pd.DataFrame(),
        "win_rate_by_struct":  pd.DataFrame(),
        "grade_distribution":  {},
        "tcs_edge":            pd.DataFrame(),
    }

    # ── Clean tracker ──────────────────────────────────────────────────────
    tdf = tracker_df.copy() if not tracker_df.empty else pd.DataFrame()
    if tdf.empty:
        return empty

    for col in ("entry_price", "exit_price", "mfe"):
        tdf[col] = pd.to_numeric(tdf.get(col, 0), errors="coerce").fillna(0.0)
    tdf["timestamp"] = pd.to_datetime(tdf.get("timestamp", pd.NaT), errors="coerce")

    trades = tdf[(tdf["entry_price"] > 0) & (tdf["exit_price"] > 0)].copy()
    if trades.empty:
        return empty

    trades = trades.sort_values("timestamp").reset_index(drop=True)

    wins   = trades[trades["mfe"] > 0]
    losses = trades[trades["mfe"] < 0]

    total_trades  = len(trades)
    win_count     = len(wins)
    win_rate      = round(win_count / total_trades * 100, 1) if total_trades else 0.0
    total_pnl     = round(float(trades["mfe"].sum()), 2)
    avg_win       = round(float(wins["mfe"].mean()), 2)   if not wins.empty   else 0.0
    avg_loss      = round(float(losses["mfe"].mean()), 2) if not losses.empty else 0.0
    gross_win     = float(wins["mfe"].sum())              if not wins.empty   else 0.0
    gross_loss    = abs(float(losses["mfe"].sum()))       if not losses.empty else 0.0
    profit_factor = round(gross_win / gross_loss, 2)      if gross_loss > 0   else 999.0
    trade_days    = int(trades["timestamp"].dt.date.nunique())

    # ── Equity curve ────────────────────────────────────────────────────────
    trades["cumulative_pnl"] = trades["mfe"].cumsum()
    equity_curve = trades[["timestamp", "symbol", "mfe", "cumulative_pnl"]].copy()

    # ── Daily P&L ───────────────────────────────────────────────────────────
    trades["date"] = trades["timestamp"].dt.date
    daily = (trades.groupby("date")["mfe"].sum()
             .reset_index().rename(columns={"mfe": "pnl"}))
    daily["cumulative_pnl"] = daily["pnl"].cumsum()

    # ── Win rate by predicted structure ─────────────────────────────────────
    struct_rows = []
    if "predicted" in trades.columns:
        for struct, grp in trades.groupby("predicted"):
            s = str(struct).strip()
            if not s:
                continue
            tc = len(grp); wc = int((grp["mfe"] > 0).sum())
            struct_rows.append({
                "structure": s,
                "trades":    tc,
                "wins":      wc,
                "win_rate":  round(wc / tc * 100, 1) if tc else 0.0,
                "avg_pnl":   round(float(grp["mfe"].mean()), 2),
            })
    wr_struct = (pd.DataFrame(struct_rows).sort_values("win_rate", ascending=False)
                 if struct_rows else pd.DataFrame())

    # ── TCS edge ────────────────────────────────────────────────────────────
    tcs_edge = pd.DataFrame()
    if not journal_df.empty and "tcs" in journal_df.columns:
        jdf = journal_df.copy()
        jdf["tcs"] = pd.to_numeric(jdf.get("tcs", 0), errors="coerce").fillna(0)
        jdf["tcs_bucket"] = pd.cut(
            jdf["tcs"],
            bins=[0, 40, 55, 65, 75, 101],
            labels=["<40", "40–54", "55–64", "65–74", "75+"],
        )
        jdf["timestamp"] = pd.to_datetime(jdf.get("timestamp", ""), errors="coerce")
        merged = pd.merge(
            jdf[["timestamp", "ticker", "tcs", "tcs_bucket"]],
            trades[["timestamp", "symbol", "mfe"]],
            left_on="ticker", right_on="symbol", how="inner",
            suffixes=("_j", "_t"),
        )
        if not merged.empty:
            tcs_rows = []
            for bkt, grp in merged.groupby("tcs_bucket", observed=True):
                tc = len(grp); wc = int((grp["mfe"] > 0).sum())
                tcs_rows.append({
                    "tcs_bucket": str(bkt),
                    "trades":     tc,
                    "win_rate":   round(wc / tc * 100, 1) if tc else 0.0,
                })
            tcs_edge = pd.DataFrame(tcs_rows)

    # ── Grade distribution ──────────────────────────────────────────────────
    grade_dist = {}
    if not journal_df.empty and "grade" in journal_df.columns:
        grade_dist = {str(k): int(v)
                      for k, v in journal_df["grade"].value_counts().items()}

    return {
        "summary": {
            "win_rate": win_rate, "total_pnl": total_pnl,
            "avg_win": avg_win,   "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "total_trades": total_trades, "trade_days": trade_days,
        },
        "equity_curve":       equity_curve,
        "daily_pnl":          daily,
        "win_rate_by_struct": wr_struct,
        "grade_distribution": grade_dist,
        "tcs_edge":           tcs_edge,
    }


# ── Portfolio Risk Metrics (Sharpe, Alpha, Drawdown) ──────────────────────────
def compute_portfolio_metrics(paper_df: "pd.DataFrame",
                              api_key: str = "", secret_key: str = "") -> dict:
    """Compute Sharpe ratio, alpha vs SPY, and max drawdown from paper trades.

    Parameters
    ----------
    paper_df : DataFrame with columns: trade_date, win_loss, follow_thru_pct (or post_alert_move_pct)
    api_key, secret_key : Alpaca creds for SPY benchmark (optional)

    Returns dict with: sharpe, sharpe_monthly, alpha_vs_spy, max_drawdown_pct,
                        current_drawdown_pct, daily_returns DataFrame
    """
    empty = {
        "sharpe": None, "sharpe_monthly": None,
        "alpha_vs_spy": None, "max_drawdown_pct": None,
        "current_drawdown_pct": None, "daily_returns": pd.DataFrame(),
        "rolling_drawdown": pd.DataFrame(),
        "trade_count": 0,
    }
    if paper_df is None or paper_df.empty:
        return empty

    df = paper_df.copy()
    df["trade_date"] = pd.to_datetime(df.get("trade_date", ""), errors="coerce")
    df = df.dropna(subset=["trade_date"])

    pnl_col = None
    for c in ("post_alert_move_pct", "follow_thru_pct"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
            if df[c].notna().sum() > 0:
                pnl_col = c
                break
    if pnl_col is None:
        wl = df.get("win_loss", pd.Series(dtype=str))
        wl_clean = wl.astype(str).str.strip().str.lower()
        df["_synth_ret"] = wl_clean.map({"win": 1.0, "loss": -1.0, "w": 1.0, "l": -1.0}).fillna(0.0)
        pnl_col = "_synth_ret"

    daily = (df.groupby(df["trade_date"].dt.date)[pnl_col]
             .mean().reset_index())
    daily.columns = ["date", "return_pct"]
    daily = daily.sort_values("date").reset_index(drop=True)

    if len(daily) < 3:
        empty["trade_count"] = len(df)
        return empty

    returns = daily["return_pct"]
    mean_r = returns.mean()
    std_r = returns.std()

    sharpe = round(mean_r / std_r, 3) if std_r > 0 else None
    trading_days_per_year = 252
    sharpe_annual = round(sharpe * (trading_days_per_year ** 0.5), 3) if sharpe is not None else None

    cum_ret = (1 + returns.fillna(0) / 100).cumprod()
    running_max = cum_ret.cummax()
    drawdown = (cum_ret - running_max) / running_max * 100
    _max_dd_raw = float(drawdown.min())
    max_dd = round(_max_dd_raw, 2) if _max_dd_raw == _max_dd_raw else 0.0
    _cur_dd_raw = float(drawdown.iloc[-1]) if len(drawdown) > 0 else 0.0
    current_dd = round(_cur_dd_raw, 2) if _cur_dd_raw == _cur_dd_raw else 0.0

    rolling_dd = daily[["date"]].copy()
    rolling_dd["drawdown_pct"] = (
        drawdown.replace([float("inf"), float("-inf")], float("nan")).fillna(0).values
    )

    alpha_spy = None
    alpha_iwm = None
    if api_key and secret_key:
        try:
            import requests as _req
            _start = str(daily["date"].min())
            _end = str(daily["date"].max())
            _hdr = {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": secret_key,
            }
            for _sym, _key in [("SPY", "spy"), ("IWM", "iwm")]:
                try:
                    _url = (f"https://data.alpaca.markets/v2/stocks/{_sym}/bars"
                            f"?timeframe=1Day&start={_start}&end={_end}&limit=500")
                    _r = _req.get(_url, headers=_hdr, timeout=10)
                    if _r.status_code == 200:
                        _bars = _r.json().get("bars", [])
                        if len(_bars) >= 2:
                            _bm_df = pd.DataFrame(_bars)
                            _bm_df["date"] = pd.to_datetime(_bm_df["t"]).dt.date
                            _bm_df[f"{_key}_ret"] = _bm_df["c"].pct_change() * 100
                            _bm_df = _bm_df.dropna(subset=[f"{_key}_ret"])
                            _merged = pd.merge(daily, _bm_df[["date", f"{_key}_ret"]], on="date", how="inner")
                            if len(_merged) >= 3:
                                _a = round(_merged["return_pct"].mean() - _merged[f"{_key}_ret"].mean(), 3)
                                if _key == "spy":
                                    alpha_spy = _a
                                else:
                                    alpha_iwm = _a
                except Exception:
                    pass
        except Exception:
            pass

    return {
        "sharpe": sharpe,
        "sharpe_annual": sharpe_annual,
        "alpha_vs_spy": alpha_spy,
        "alpha_vs_iwm": alpha_iwm,
        "max_drawdown_pct": max_dd,
        "current_drawdown_pct": current_dd,
        "daily_returns": daily,
        "rolling_drawdown": rolling_dd,
        "trade_count": len(df),
    }


def run_pending_migrations() -> dict:
    """Attempt to run all pending ALTER TABLE migrations via exec_sql RPC.

    Returns dict with {ran: int, failed: int, already_exist: int, errors: list}.
    If exec_sql doesn't exist, returns instructions to create it.
    """
    if not supabase:
        return {"ran": 0, "failed": 0, "already_exist": 0,
                "errors": ["Supabase not connected"]}

    migrations = [
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mae REAL",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mfe REAL",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_time TEXT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS exit_trigger TEXT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS exit_obs TEXT",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_ib_distance REAL",
        "ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS tcs REAL",
        "ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS rvol REAL",
        "ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS edge_score REAL",
        "ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS predicted_structure TEXT",
        "ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS confidence_label TEXT",
        # EOD close price — required for eod_pnl_r computation in compute_trade_sim_tiered()
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS close_price NUMERIC",
        "ALTER TABLE paper_trades       ADD COLUMN IF NOT EXISTS close_price NUMERIC",
        # Tiered P&L columns for backtest_sim_runs (50/25/25 ladder backfill)
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS tiered_pnl_r NUMERIC",
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS eod_pnl_r NUMERIC",
        # Entry quality filter metrics (IB range % and VWAP at IB close)
        # ib_range_pct = (ib_high - ib_low) / open_price * 100; computed at insert time
        # vwap_at_ib   = VWAP of 5-min bars up to IB close; injected by log_context_levels
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS ib_range_pct REAL",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS vwap_at_ib REAL",
        # TCS floor — per-structure threshold at trade time; enables Marginal vs Comfortable breakdown
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS tcs_floor SMALLINT",
        # Data quality flag — 'ok' when intraday bars were available, 'no_bars' when not
        "ALTER TABLE backtest_context_levels ADD COLUMN IF NOT EXISTS data_quality TEXT DEFAULT 'ok'",
        # Performance index for Ladder tab — speeds up filtering by outcome + tiered_pnl_r + date
        (
            "CREATE INDEX IF NOT EXISTS idx_bsr_outcome_tiered_date "
            "ON backtest_sim_runs(actual_outcome, tiered_pnl_r, sim_date)"
        ),
        # Batch backtest sim columns — written by batch_backtest.py and run_sim_backfill.py
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS scan_type TEXT DEFAULT 'morning'",
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS sim_outcome TEXT",
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS pnl_r_sim FLOAT",
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS pnl_pct_sim FLOAT",
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS entry_price_sim FLOAT",
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS stop_price_sim FLOAT",
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS stop_dist_pct FLOAT",
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS target_price_sim FLOAT",
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS gap_pct FLOAT",
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS gap_vs_ib_pct FLOAT",
        # Version stamps — used by run_sim_backfill.py to detect stale rows
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS sim_version TEXT",
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS tiered_sim_version TEXT",
        # vwap_at_ib for backtest_sim_runs — written by batch_backtest.py (optional field)
        # used by get_backtest_pace_target() to apply full live filter stack (TCS+IB+VWAP)
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS vwap_at_ib FLOAT",
        # skip_reason — written by _place_order_for_setup() in paper_trader_bot.py
        # values: order_placed | orders_disabled | non_directional | bearish_break_filtered
        #         ib_too_wide | vwap_misaligned | pdt_blocked | concurrent_cap | order_failed | unknown
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS skip_reason TEXT",
        # paper_trades pnl_r_sim — needed by mv_paper_tiered_pnl_summary materialized view
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS pnl_r_sim FLOAT",
        # Actual exit fill price from Alpaca bracket order legs (take-profit or stop-loss child fill).
        # When present, used instead of close_price (EOD proxy) for P&L calculation.
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS alpaca_exit_fill_price NUMERIC",
        # Intraday S/R levels written by log_context_levels() at scan time.
        # Nearest key level (prev_day_high, prev_day_low, VWAP) above/below the IB break price.
        # Read directly by _monitor_trailing_stops() so the v6 trail-tightening logic
        # does not have to fall back to the nightly backtest_context_levels table (which
        # is never populated intraday and therefore returns NULL for today's live trades).
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS nearest_resistance REAL",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS nearest_support    REAL",
        # Realized R outcome from actual Alpaca fill prices (entry fill vs. exit fill).
        # Written by _force_close_all_positions() after the 3:30 PM EOD force-close.
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS pnl_r_actual NUMERIC",
        # RVOL size bonus multiplier — written by _place_order_for_setup() in paper_trader_bot.py
        # 1.00 = no bonus; 1.25 = RVOL 2.0-2.99; 1.50 = RVOL ≥ 3.0
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS rvol_mult REAL DEFAULT 1.0",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS rvol_size_mult REAL",
        # Trailing stop context — written by _monitor_trailing_stops in paper_trader_bot.py
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trail_activated BOOLEAN",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trail_size_r REAL",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trail_sr_level REAL",
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trail_sr_source TEXT",
        # Free-text notes field — used by force-close and trailing stop patches
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS notes TEXT",
        # RVOL size bonus multiplier for batch backtest rows — mirrors the live bot bonus.
        # 1.00 = no bonus; 1.25 = RVOL 2.0-2.99; 1.50 = RVOL ≥ 3.0
        # pnl_r_sim already reflects this multiplier when rvol_mult > 1.0.
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS rvol_mult REAL DEFAULT 1.0",
        # Voice trade journal — transcript and audio stored alongside each entry
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS transcript TEXT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS audio_b64 TEXT",
        # Behavioral signals extracted from voice recordings via GPT-4.
        # Stored as JSONB so analytics can query individual signal keys.
        # Keys match _VJ_SIGNAL_KEYS: fomo_entry, panic_exit, followed_plan, etc.
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS voice_signals JSONB",
        # Screener pass that produced this ticker — 'gap' | 'trend' | 'squeeze' | 'other'
        # Written by paper_trader_bot at order placement; backfilled from SMA20/SMA50 for history.
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS screener_pass TEXT",
        "ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS screener_pass TEXT",
        # Screener-pass position-size multiplier applied at order placement.
        # Derived from 5-yr backtest: other=1.15×, gap=1.00×, trend=0.85×, squeeze=1.00×.
        # Stored for audit — multiplier table may change over time; this records what was used.
        "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS sp_mult REAL DEFAULT 1.0",
    ]

    ran = 0
    failed = 0
    already = 0
    errors = []

    for sql in migrations:
        try:
            supabase.rpc("exec_sql", {"query": sql}).execute()
            ran += 1
        except Exception as e:
            es = str(e)
            if "PGRST202" in es:
                return {"ran": ran, "failed": len(migrations), "already_exist": 0,
                        "errors": ["exec_sql function not found — run CREATE FUNCTION in Supabase SQL Editor first"],
                        "needs_exec_sql": True}
            elif "already exists" in es.lower() or "42701" in es:
                already += 1
            else:
                failed += 1
                errors.append(f"{sql}: {es[:100]}")

    return {"ran": ran, "failed": failed, "already_exist": already, "errors": errors}


_EXEC_SQL_FUNCTION = """-- Run this ONCE in Supabase SQL Editor to enable automatic migrations:
CREATE OR REPLACE FUNCTION exec_sql(query text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  EXECUTE query;
END;
$$;
"""

_ALL_PENDING_MIGRATIONS = """-- Run in Supabase SQL Editor (one-time):
-- 1. Create the exec_sql helper function:
CREATE OR REPLACE FUNCTION exec_sql(query text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  EXECUTE query;
END;
$$;

-- 2. Paper trades MAE/MFE columns:
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mae REAL;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mfe REAL;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_time TEXT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS exit_trigger TEXT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_ib_distance REAL;

-- 3. Ticker rankings context columns:
ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS tcs REAL;
ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS rvol REAL;
ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS edge_score REAL;
ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS predicted_structure TEXT;
ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS confidence_label TEXT;

-- 4. EOD close price for eod_pnl_r computation:
ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS close_price NUMERIC;
ALTER TABLE paper_trades       ADD COLUMN IF NOT EXISTS close_price NUMERIC;

-- 5. Tiered P&L columns for backtest_sim_runs (50/25/25 ladder backfill):
ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS tiered_pnl_r NUMERIC;
ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS eod_pnl_r NUMERIC;

-- 6. Performance index for the Ladder tab (speeds up filtered reads on 16 k+ rows):
CREATE INDEX IF NOT EXISTS idx_bsr_outcome_tiered_date
    ON backtest_sim_runs(actual_outcome, tiered_pnl_r, sim_date);

-- 7. Materialised summary view — refreshed nightly by refresh_mv_tiered_pnl_summary():
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_tiered_pnl_summary AS
SELECT
    user_id,
    predicted                         AS screener,
    scan_type,
    date_trunc('month', sim_date::date)::date AS month,
    COUNT(*)                          AS total_trades,
    COUNT(*) FILTER (WHERE tiered_pnl_r > 0)  AS wins,
    COUNT(*) FILTER (WHERE tiered_pnl_r <= 0) AS losses,
    ROUND(AVG(tiered_pnl_r)::numeric, 4)      AS avg_tiered_r,
    ROUND(SUM(tiered_pnl_r)::numeric, 4)      AS sum_tiered_r,
    ROUND(AVG(eod_pnl_r)::numeric, 4)         AS avg_eod_r,
    ROUND(SUM(eod_pnl_r)::numeric, 4)         AS sum_eod_r,
    ROUND(AVG(pnl_r_sim)::numeric, 4)         AS avg_sim_r,
    ROUND(
        COUNT(*) FILTER (WHERE tiered_pnl_r > 0)::numeric
        / NULLIF(COUNT(*), 0) * 100, 2
    )                                          AS win_rate_pct
FROM backtest_sim_runs
WHERE tiered_pnl_r IS NOT NULL
  AND tiered_pnl_r <> -9999
GROUP BY user_id, screener, scan_type, month;

CREATE UNIQUE INDEX IF NOT EXISTS mv_tiered_pnl_summary_uidx
    ON mv_tiered_pnl_summary(user_id, screener, scan_type, month);

-- 8. Materialised summary view for paper_trades Ladder stats — refreshed nightly:
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_paper_tiered_pnl_summary AS
SELECT
    user_id,
    predicted                              AS screener,
    COALESCE(scan_type, 'morning')         AS scan_type,
    date_trunc('month', trade_date::date)::date AS month,
    COUNT(*)                               AS total_trades,
    COUNT(*) FILTER (WHERE tiered_pnl_r > 0)   AS wins,
    COUNT(*) FILTER (WHERE tiered_pnl_r <= 0)  AS losses,
    ROUND(AVG(tiered_pnl_r)::numeric, 4)       AS avg_tiered_r,
    ROUND(SUM(tiered_pnl_r)::numeric, 4)       AS sum_tiered_r,
    ROUND(AVG(eod_pnl_r)::numeric, 4)          AS avg_eod_r,
    ROUND(SUM(eod_pnl_r)::numeric, 4)          AS sum_eod_r,
    ROUND(AVG(pnl_r_sim)::numeric, 4)          AS avg_sim_r,
    ROUND(
        COUNT(*) FILTER (WHERE tiered_pnl_r > 0)::numeric
        / NULLIF(COUNT(*), 0) * 100, 2
    )                                           AS win_rate_pct
FROM paper_trades
WHERE tiered_pnl_r IS NOT NULL
  AND tiered_pnl_r <> -9999
GROUP BY user_id, screener, scan_type, month;

CREATE UNIQUE INDEX IF NOT EXISTS mv_paper_tiered_pnl_summary_uidx
    ON mv_paper_tiered_pnl_summary(user_id, screener, scan_type, month);

-- 9. Realized R from 3:30 PM force-close fills (written by _force_close_all_positions):
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS pnl_r_actual NUMERIC;

-- 10. Screener pass — which Finviz scan produced this ticker:
--     'gap' = ≥3% gap-of-day | 'trend' = ≥1% + above SMA20/SMA50 | 'squeeze' = short-float ≥15%
--     'other' = unclassified rows (abs gap < 3%, not above both SMAs)
ALTER TABLE paper_trades       ADD COLUMN IF NOT EXISTS screener_pass TEXT;
ALTER TABLE backtest_sim_runs  ADD COLUMN IF NOT EXISTS screener_pass TEXT;
"""


# ── skip_reason one-time data backfill ────────────────────────────────────────
def backfill_paper_trades_skip_reason(user_id: str) -> int:
    """Backfill skip_reason for historical paper_trades rows.

    Called at app startup (via st.session_state guard) so the funnel is
    meaningful for data pre-dating bot-side skip_reason logging.  Also called
    after the close-price / IB backfill pipeline finishes, so that rows which
    were previously left as 'unknown' (because ib_range_pct / vwap_at_ib were
    NULL at the time) can be reclassified now that those columns are populated.

    Priority order applied to rows where skip_reason IS NULL **or 'unknown'**:
      1. alpaca_order_id IS NOT NULL                         → order_placed
      2. predicted contains 'bearish'                        → bearish_break_filtered
      3. ib_range_pct IS NOT NULL AND ib_range_pct >= threshold → ib_too_wide
         (threshold = load_ib_range_pct_threshold(), default 10.0)
      4. vwap_at_ib/close_price mismatch with direction      → vwap_misaligned
      5. everything else                                     → unknown

    Returns count of rows updated.
    """
    if not supabase:
        return 0
    # NOTE: No outer try/except — schema errors (e.g. "column skip_reason does
    # not exist") MUST propagate so the app.py startup block leaves the
    # session flag False and retries on the next render.  Only individual
    # batch-update failures are silenced (transient network/row-lock issues).
    _ib_threshold = load_ib_range_pct_threshold()
    _updated = 0
    _hist: list = []
    _offset = 0
    while True:
        _page = (
            supabase.table("paper_trades")
            .select("id,predicted,alpaca_order_id,ib_range_pct,vwap_at_ib,close_price")
            .eq("user_id", user_id)
            .or_("skip_reason.is.null,skip_reason.eq.unknown")
            .range(_offset, _offset + 499)
            .execute()
        )
        _rows = _page.data or []
        _hist.extend(_rows)
        if len(_rows) < 500:
            break
        _offset += 500
    if not _hist:
        return 0

    _placed:    list = []
    _bearish:   list = []
    _ib_wide:   list = []
    _vwap_mis:  list = []
    _unknown:   list = []

    for _r in _hist:
        _rid       = _r["id"]
        _predicted = str(_r.get("predicted") or "").lower()
        _ib_pct    = _r.get("ib_range_pct")
        _vwap      = _r.get("vwap_at_ib")
        _close     = _r.get("close_price")

        if _r.get("alpaca_order_id"):
            _placed.append(_rid)
        elif "bearish" in _predicted:
            _bearish.append(_rid)
        elif _ib_pct is not None and float(_ib_pct) >= _ib_threshold:
            _ib_wide.append(_rid)
        elif _vwap is not None and _close is not None:
            # Only bullish-break rows reach here; bearish rows are already
            # captured by the bearish_break_filtered branch above.
            if "bullish" in _predicted and _close < _vwap:
                _vwap_mis.append(_rid)
            else:
                _unknown.append(_rid)
        else:
            _unknown.append(_rid)

    for _ids, _reason in (
        (_placed,   "order_placed"),
        (_bearish,  "bearish_break_filtered"),
        (_ib_wide,  "ib_too_wide"),
        (_vwap_mis, "vwap_misaligned"),
        (_unknown,  "unknown"),
    ):
        for _i in range(0, len(_ids), 50):
            _chunk = _ids[_i : _i + 50]
            try:
                supabase.table("paper_trades").update(
                    {"skip_reason": _reason}
                ).in_("id", _chunk).execute()
                _updated += len(_chunk)
            except Exception:
                pass  # transient batch failure — continue with remaining chunks
    _still_unknown    = len(_unknown)
    _reclassified     = len(_hist) - _still_unknown  # in-memory classification outcome
    logging.info(
        "[skip_reason backfill] %d row(s) reclassified, %d row(s) still unknown "
        "(ib_range_pct / vwap_at_ib missing — may resolve after a close-price backfill).",
        _reclassified,
        _still_unknown,
    )
    return _updated


# ── Live Playbook Screener ──────────────────────────────────────────────────────
def scan_playbook(api_key: str, secret_key: str, top: int = 50) -> tuple:
    """Scan Alpaca for today's most-active and top-gaining small-cap stocks ($2–$20).

    Returns
    -------
    (rows: list[dict], error: str)
        rows — sorted by % change descending; each dict has:
            ticker, price, change_pct, volume, source
        error — non-empty string only if *both* endpoints fail
    """
    if not api_key or not secret_key:
        return [], "No API credentials provided."

    headers = {
        "APCA-API-KEY-ID":     api_key,
        "APCA-API-SECRET-KEY": secret_key,
        "accept":              "application/json",
    }
    base   = "https://data.alpaca.markets/v1beta1/screener/stocks"
    pool   = {}
    errors = []

    # ── Most Actives ─────────────────────────────────────────────────────────
    try:
        r = requests.get(
            f"{base}/most-actives",
            params={"by": "volume", "top": top},
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            for item in r.json().get("most_actives", []):
                sym        = str(item.get("symbol", "")).upper()
                price      = float(item.get("price", 0) or 0)
                change_pct = float(item.get("percent_change", 0) or 0)
                volume     = int(item.get("volume", 0) or 0)
                if sym and 2.0 <= price <= 20.0:
                    pool[sym] = {
                        "ticker":     sym,
                        "price":      price,
                        "change_pct": change_pct,
                        "volume":     volume,
                        "source":     "Active",
                    }
        else:
            errors.append(f"most-actives HTTP {r.status_code}")
    except Exception as exc:
        errors.append(f"most-actives: {exc}")

    # ── Top Gainers ───────────────────────────────────────────────────────────
    try:
        r = requests.get(
            f"{base}/movers",
            params={"market_type": "stocks", "top": top},
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            for item in r.json().get("gainers", []):
                sym        = str(item.get("symbol", "")).upper()
                price      = float(item.get("price", 0) or 0)
                change_pct = float(item.get("percent_change", 0) or 0)
                volume     = int(item.get("volume", 0) or 0)
                if sym and 2.0 <= price <= 20.0:
                    if sym in pool:
                        pool[sym]["source"] = "Active + Gainer"
                    else:
                        pool[sym] = {
                            "ticker":     sym,
                            "price":      price,
                            "change_pct": change_pct,
                            "volume":     volume,
                            "source":     "Gainer",
                        }
        elif r.status_code not in (400, 422) or not pool:
            # Only surface the error if most-actives also came up empty
            errors.append(f"movers HTTP {r.status_code}")
    except Exception as exc:
        errors.append(f"movers: {exc}")

    rows = sorted(pool.values(), key=lambda x: x["change_pct"], reverse=True)
    if errors and not rows:
        # If every failure was a 400/422, the market is simply closed/inactive
        non_auth = [e for e in errors if "400" in e or "422" in e]
        if len(non_auth) == len(errors):
            err = "market_closed"
        else:
            err = "; ".join(errors)
    else:
        err = ""
    return rows, err


# ── Historical Backtester ───────────────────────────────────────────────────────
_BACKTEST_DIRECTIONAL  = ("Trend", "Nrml Var", "Normal Var")
_BACKTEST_RANGE        = ("Non-Trend", "Non Trend")
_BACKTEST_NEUTRAL_EXT  = ("Ntrl Extreme", "Neutral Extreme")  # high-vol: any break wins
_BACKTEST_BALANCED     = ("Neutral",)                          # pure balanced: needs both sides
_BACKTEST_BIMODAL      = ("Dbl Dist", "Double")
_BACKTEST_NORMAL       = ("Normal",)   # Normal (not Var) — range-ish


def _backtest_single(api_key: str, secret_key: str, sym: str,
                     trade_date, feed: str, price_min: float, price_max: float,
                     cutoff_hour: int = 10, cutoff_minute: int = 30,
                     slippage_pct: float = 0.0):
    """Fetch one ticker's historical bars, score the morning, evaluate the afternoon.

    slippage_pct: one-way slippage as a percentage (e.g. 0.5 = 0.5%).
    Applied to both entry and exit, so total drag = slippage_pct × 2.
    Returns a result dict or None if data is insufficient / out of price range.
    """
    try:
        df = fetch_bars(api_key, secret_key, sym, trade_date, feed=feed)
        if df.empty or len(df) < 10:
            return None

        # Price range gate: use first bar open price
        open_px = float(df["open"].iloc[0])
        if not (price_min <= open_px <= price_max):
            return None

        # Split at prediction cutoff (IB always 9:30–10:30; engine sees up to cutoff)
        ib_cutoff = df.index[0].replace(hour=cutoff_hour, minute=cutoff_minute, second=0)
        pm_df  = df[df.index <= ib_cutoff]   # engine input (9:30 → cutoff)
        aft_df = df[df.index > ib_cutoff]    # actual outcome (cutoff → 4:00 PM)

        if len(pm_df) < 5:
            return None

        morning_only = len(aft_df) < 5  # live scan before afternoon data is available

        # Morning engine run
        ib_high, ib_low = compute_initial_balance(pm_df)
        if not ib_high or not ib_low:
            return None

        bin_centers, vap, poc_price = compute_volume_profile(pm_df, num_bins=30)
        tcs   = float(compute_tcs(pm_df, ib_high, ib_low, poc_price))
        probs = compute_structure_probabilities(
            pm_df, bin_centers, vap, ib_high, ib_low, poc_price
        )
        predicted_struct = max(probs, key=probs.get) if probs else "—"
        confidence = round(probs.get(predicted_struct, 0.0), 1)

        # Close at prediction cutoff (IB close or current bar) — used for directional bias
        pm_close = float(pm_df["close"].iloc[-1])

        # Afternoon reality — placeholder when afternoon bars not yet available
        if morning_only:
            aft_high       = ib_high
            aft_low        = ib_low
            close_px       = pm_close
            actual_outcome = "Pending"
            actual_icon    = "…"
            broke_up       = False
            broke_down     = False
        else:
            aft_high = float(aft_df["high"].max())
            aft_low  = float(aft_df["low"].min())
            close_px = float(aft_df["close"].iloc[-1])
            broke_up   = aft_high > ib_high
            broke_down = aft_low  < ib_low

        # ── Directional resolution ────────────────────────────────────────────
        # compute_structure_probabilities is non-directional ("Trend" not "Bullish
        # Break") because when called with IB-only bars day_high ≈ ib_high and
        # day_low ≈ ib_low, making both_hit always True and suppressing directional
        # scores.  Resolve direction here using close-vs-IB-midpoint at cutoff —
        # the standard Volume-Profile daily-bias anchor used by the framework.
        # Structures that imply a break → Bullish/Bearish Break (tradeable).
        # Range structures (Normal, Non-Trend) stay as-is (no order placed).
        _ib_mid = (ib_high + ib_low) / 2.0
        _ORDERABLE_STRUCTS = frozenset(("Trend", "Nrml Var", "Ntrl Extreme"))
        if predicted_struct in _ORDERABLE_STRUCTS and _ib_mid > 0:
            predicted = "Bullish Break" if pm_close > _ib_mid else "Bearish Break"
        else:
            predicted = predicted_struct

        if not morning_only:
            if broke_up and broke_down:
                actual_outcome = "Both Sides"
                actual_icon    = "↕"
            elif broke_up:
                actual_outcome = "Bullish Break"
                actual_icon    = "↑"
            elif broke_down:
                actual_outcome = "Bearish Break"
                actual_icon    = "↓"
            else:
                actual_outcome = "Range-Bound"
                actual_icon    = "—"

        # Win/Loss: does predicted category match actual outcome?
        if morning_only:
            win      = None
            aft_move = 0.0
        else:
            if predicted in ("Bullish Break", "Bearish Break"):
                # Directional predictions require exact direction match
                win = actual_outcome == predicted
            else:
                is_dir      = any(k in predicted for k in _BACKTEST_DIRECTIONAL)
                is_range    = any(k in predicted for k in _BACKTEST_RANGE)
                is_neut_ext = any(k in predicted for k in _BACKTEST_NEUTRAL_EXT)
                is_balanced = (not is_neut_ext and
                               any(k in predicted for k in _BACKTEST_BALANCED))
                is_bimodal  = any(k in predicted for k in _BACKTEST_BIMODAL)
                is_normal   = (not is_dir and not is_range and not is_neut_ext
                               and not is_balanced and not is_bimodal
                               and "Normal" in predicted)

                if is_dir:
                    win = actual_outcome in ("Bullish Break", "Bearish Break")
                elif is_neut_ext:
                    win = actual_outcome in ("Bullish Break", "Bearish Break", "Both Sides")
                elif is_range or is_normal:
                    win = actual_outcome == "Range-Bound"
                elif is_balanced:
                    win = actual_outcome in ("Both Sides", "Bullish Break", "Bearish Break")
                elif is_bimodal:
                    win = actual_outcome in ("Bullish Break", "Bearish Break", "Both Sides")
                else:
                    win = False

            if broke_up and broke_down:
                _ft_up   = (aft_high - ib_high) / ib_high * 100
                _ft_down = (ib_low   - aft_low)  / ib_low  * 100
                aft_move = _ft_up if _ft_up >= _ft_down else -_ft_down
            elif broke_up:
                aft_move = (aft_high - ib_high) / ib_high * 100
            elif broke_down:
                aft_move = -((ib_low - aft_low) / ib_low * 100)
            else:
                aft_move = 0.0

        # Slippage drag: entry + exit, each side costs slippage_pct
        # Applied to the magnitude (directional sign preserved)
        _slip_drag = slippage_pct * 2.0
        if aft_move > 0:
            aft_move = max(0.0, aft_move - _slip_drag)
        elif aft_move < 0:
            aft_move = min(0.0, aft_move + _slip_drag)

        # ── False break detection ────────────────────────────────────────────────
        # A false break = IB violated but price closed back inside within 30 min
        # (6 × 5-min bars). This is the classic "shake & bake" reversal trap.
        _aft_r = aft_df.reset_index()
        false_break_up   = False
        false_break_down = False
        if broke_up:
            _up_bars = _aft_r[_aft_r["high"] > ib_high]
            if not _up_bars.empty:
                _fi = _up_bars.index[0]
                _w  = _aft_r.loc[_fi : _fi + 6]
                false_break_up = bool((_w["close"] < ib_high).any())
        if broke_down:
            _dn_bars = _aft_r[_aft_r["low"] < ib_low]
            if not _dn_bars.empty:
                _fi = _dn_bars.index[0]
                _w  = _aft_r.loc[_fi : _fi + 6]
                false_break_down = bool((_w["close"] > ib_low).any())

        _avg_vol, _prev_close = fetch_daily_stats(api_key, secret_key, sym, trade_date)
        _rvol_val = compute_rvol(pm_df, avg_daily_vol=_avg_vol)
        _gap_pct = (
            round((open_px - _prev_close) / _prev_close * 100, 2)
            if _prev_close and _prev_close > 0 else None
        )

        _mae_val = None
        _mfe_val = None
        _entry_time_val = None
        _exit_trigger_val = None
        _entry_ib_dist_val = None

        _alert_px = float(pm_df["close"].iloc[-1])
        if _alert_px and _alert_px > 0:
            _entry_time_val = pm_df.index[-1].strftime("%H:%M") if hasattr(pm_df.index[-1], 'strftime') else None
            _nearest_ib = min(abs(_alert_px - ib_high), abs(_alert_px - ib_low))
            _entry_ib_dist_val = round(_nearest_ib / _alert_px * 100, 2) if _alert_px > 0 else None

        if not morning_only and _alert_px and _alert_px > 0 and not aft_df.empty:
            _aft_highest = float(aft_df["high"].max())
            _aft_lowest = float(aft_df["low"].min())
            _is_bearish_pred = (predicted == "Bearish Break" or
                                any(k in predicted for k in ("Trend Day Down", "Bearish")))
            if _is_bearish_pred:
                _mfe_val = round((_alert_px - _aft_lowest) / _alert_px * 100, 2)
                _mae_val = round((_aft_highest - _alert_px) / _alert_px * 100, 2)
            else:
                _mfe_val = round((_aft_highest - _alert_px) / _alert_px * 100, 2)
                _mae_val = round((_alert_px - _aft_lowest) / _alert_px * 100, 2)

            if _is_bearish_pred:
                if close_px <= _alert_px:
                    if _aft_lowest <= ib_low * 0.995:
                        _exit_trigger_val = "target_hit"
                    else:
                        _exit_trigger_val = "time_based"
                else:
                    if _aft_highest >= ib_high * 1.005:
                        _exit_trigger_val = "stop_hit"
                    else:
                        _exit_trigger_val = "time_based"
            else:
                if close_px >= _alert_px:
                    if _aft_highest >= ib_high * 1.005:
                        _exit_trigger_val = "target_hit"
                    else:
                        _exit_trigger_val = "time_based"
                else:
                    if _aft_lowest <= ib_low * 0.995:
                        _exit_trigger_val = "stop_hit"
                    else:
                        _exit_trigger_val = "time_based"

        _bt_grade, _bt_grade_reason, _ = compute_trade_grade(
            _rvol_val, tcs, pm_close, ib_high, ib_low, predicted_struct,
            voice_signals=None,
        )

        return {
            "ticker":           sym,
            "open_price":       round(open_px, 2),
            "ib_high":          round(ib_high, 2),
            "ib_low":           round(ib_low, 2),
            "tcs":              round(tcs, 1),
            "rvol":             _rvol_val,
            "gap_pct":          _gap_pct,
            "predicted":        predicted,
            "predicted_struct": predicted_struct,
            "confidence":       confidence,
            "actual_outcome":   actual_outcome,
            "actual_icon":      actual_icon,
            "close_price":      round(close_px, 2),
            "cutoff_price":     round(pm_close, 2),
            "aft_move_pct":     round(aft_move, 2),
            "win_loss":         "Pending" if win is None else ("Win" if win else "Loss"),
            "false_break_up":   false_break_up,
            "false_break_down": false_break_down,
            "mae":              _mae_val,
            "mfe":              _mfe_val,
            "entry_time":       _entry_time_val,
            "exit_trigger":     _exit_trigger_val,
            "entry_ib_distance": _entry_ib_dist_val,
            "grade":            _bt_grade,
            "grade_reason":     _bt_grade_reason,
        }
    except Exception:
        return None


def run_historical_backtest(
    api_key: str, secret_key: str,
    trade_date,
    tickers: list,
    feed: str = "sip",
    price_min: float = 2.0,
    price_max: float = 20.0,
    cutoff_hour: int = 10,
    cutoff_minute: int = 30,
    slippage_pct: float = 0.0,
) -> tuple:
    """Run the quant engine on morning-only historical data and score against afternoon.

    Returns (results: list[dict], summary: dict).
    Results are sorted by TCS descending.
    """
    if not tickers:
        return [], {"error": "No tickers provided."}
    if not api_key or not secret_key:
        return [], {"error": "Alpaca credentials missing."}

    results = []
    with ThreadPoolExecutor(max_workers=min(10, len(tickers))) as executor:
        futures = {
            executor.submit(
                _backtest_single, api_key, secret_key, sym,
                trade_date, feed, price_min, price_max,
                cutoff_hour, cutoff_minute, slippage_pct
            ): sym
            for sym in tickers
        }
        for future in as_completed(futures):
            r = future.result()
            if r is not None:
                results.append(r)

    if not results:
        return [], {"error": "No valid data returned. Check tickers and date (market must have been open)."}

    results.sort(key=lambda x: x["tcs"], reverse=True)

    wins     = sum(1 for r in results if r["win_loss"] == "Win")
    losses   = len(results) - wins
    win_rate = round(wins / len(results) * 100, 1) if results else 0.0

    # Directional breakdown — independent of structure prediction accuracy
    bull_rows  = [r for r in results if r["actual_outcome"] == "Bullish Break"]
    bear_rows  = [r for r in results if r["actual_outcome"] == "Bearish Break"]
    both_rows  = [r for r in results if r["actual_outcome"] == "Both Sides"]
    range_rows = [r for r in results if r["actual_outcome"] == "Range-Bound"]

    avg_bull_ft = (round(sum(r["aft_move_pct"] for r in bull_rows) / len(bull_rows), 1)
                   if bull_rows else 0.0)
    avg_bear_ft = (round(sum(abs(r["aft_move_pct"]) for r in bear_rows) / len(bear_rows), 1)
                   if bear_rows else 0.0)

    long_win_rate = round(len(bull_rows) / len(results) * 100, 1) if results else 0.0

    # False break stats
    fb_up   = [r for r in results if r.get("false_break_up")]
    fb_down = [r for r in results if r.get("false_break_down")]
    _breakable = len(bull_rows) + len(bear_rows) + len(both_rows)
    false_break_rate = (round((len(fb_up) + len(fb_down)) / _breakable * 100, 1)
                        if _breakable else 0.0)

    _eod_vals    = [r["eod_pnl_r"]    for r in results if r.get("eod_pnl_r")    is not None and r["eod_pnl_r"]    == r["eod_pnl_r"]]
    _tiered_vals = [r["tiered_pnl_r"] for r in results if r.get("tiered_pnl_r") is not None and r["tiered_pnl_r"] == r["tiered_pnl_r"]]

    summary = {
        "win_rate":         win_rate,
        "total":            len(results),
        "wins":             wins,
        "losses":           losses,
        "highest_tcs":      round(max(r["tcs"] for r in results), 1),
        "avg_tcs":          round(sum(r["tcs"] for r in results) / len(results), 1),
        "bull_breaks":      len(bull_rows),
        "bear_breaks":      len(bear_rows),
        "both_breaks":      len(both_rows),
        "range_bound":      len(range_rows),
        "avg_bull_ft":      avg_bull_ft,
        "avg_bear_ft":      avg_bear_ft,
        "long_win_rate":    long_win_rate,
        "false_break_rate": false_break_rate,
        "fb_up_count":      len(fb_up),
        "fb_down_count":    len(fb_down),
        "avg_eod_pnl_r":    round(sum(_eod_vals) / len(_eod_vals), 3) if _eod_vals else None,
        "avg_tiered_pnl_r": round(sum(_tiered_vals) / len(_tiered_vals), 3) if _tiered_vals else None,
        "eod_pnl_r_count":  len(_eod_vals),
        "tiered_pnl_r_count": len(_tiered_vals),
    }
    return results, summary


def run_backtest_range(
    api_key: str, secret_key: str,
    start_date, end_date,
    tickers: list,
    feed: str = "sip",
    price_min: float = 2.0,
    price_max: float = 20.0,
    slippage_pct: float = 0.0,
) -> tuple:
    """Run the backtest across a date range (max 65 weekdays ≈ 3 months).

    Returns (all_results, agg_summary, daily_list) where:
    - all_results   : flat list of every row with 'sim_date' and 'split' ('train'/'test') added
    - agg_summary   : aggregate stats with walk-forward train/test breakdown
    - daily_list    : [(date, results, summary), ...] one entry per trading day

    Walk-forward split: first 70% of trading days = train, last 30% = test.
    This gives an honest out-of-sample win rate on dates the model never saw.
    """
    def _summarise(rows: list, label: str) -> dict:
        if not rows:
            return {"label": label, "total": 0, "win_rate": 0.0}
        total = len(rows)
        wins  = sum(1 for r in rows if r["win_loss"] == "Win")
        bull  = [r for r in rows if r["actual_outcome"] == "Bullish Break"]
        bear  = [r for r in rows if r["actual_outcome"] == "Bearish Break"]
        both  = [r for r in rows if r["actual_outcome"] == "Both Sides"]
        rng   = [r for r in rows if r["actual_outcome"] == "Range-Bound"]
        fb_u  = [r for r in rows if r.get("false_break_up")]
        fb_d  = [r for r in rows if r.get("false_break_down")]
        brk   = len(bull) + len(bear) + len(both)
        _e_v  = [r["eod_pnl_r"]    for r in rows if r.get("eod_pnl_r")    is not None and r["eod_pnl_r"]    == r["eod_pnl_r"]]
        _t_v  = [r["tiered_pnl_r"] for r in rows if r.get("tiered_pnl_r") is not None and r["tiered_pnl_r"] == r["tiered_pnl_r"]]
        return {
            "label":            label,
            "total":            total,
            "wins":             wins,
            "losses":           total - wins,
            "win_rate":         round(wins / total * 100, 1) if total else 0.0,
            "highest_tcs":      round(max(r["tcs"] for r in rows), 1),
            "avg_tcs":          round(sum(r["tcs"] for r in rows) / total, 1),
            "bull_breaks":      len(bull),
            "bear_breaks":      len(bear),
            "both_breaks":      len(both),
            "range_bound":      len(rng),
            "avg_bull_ft":      (round(sum(r["aft_move_pct"] for r in bull) / len(bull), 1)
                                 if bull else 0.0),
            "avg_bear_ft":      (round(sum(abs(r["aft_move_pct"]) for r in bear) / len(bear), 1)
                                 if bear else 0.0),
            "long_win_rate":    round(len(bull) / total * 100, 1) if total else 0.0,
            "false_break_rate": (round((len(fb_u) + len(fb_d)) / brk * 100, 1)
                                 if brk else 0.0),
            "fb_up_count":      len(fb_u),
            "fb_down_count":    len(fb_d),
            "avg_eod_pnl_r":    round(sum(_e_v) / len(_e_v), 3) if _e_v else None,
            "avg_tiered_pnl_r": round(sum(_t_v) / len(_t_v), 3) if _t_v else None,
            "eod_pnl_r_count":  len(_e_v),
            "tiered_pnl_r_count": len(_t_v),
        }

    # Collect weekdays in range, cap at 65 (~3 calendar months)
    trading_days = []
    cur = start_date
    while cur <= end_date and len(trading_days) < 65:
        if cur.weekday() < 5:
            trading_days.append(cur)
        cur += timedelta(days=1)

    if not trading_days:
        return [], {"error": "No trading days in selected range."}, []

    # Walk-forward split: first 70% = train, last 30% = test
    split_idx   = max(1, int(len(trading_days) * 0.70))
    train_days  = set(str(d) for d in trading_days[:split_idx])

    daily_list = []
    for d in trading_days:
        r, s = run_historical_backtest(
            api_key, secret_key, d, tickers, feed, price_min, price_max,
            slippage_pct=slippage_pct
        )
        if not s.get("error") and r:
            split_label = "train" if str(d) in train_days else "test"
            for row in r:
                row["sim_date"] = str(d)
                row["split"]    = split_label
            daily_list.append((d, r, s))

    if not daily_list:
        return [], {"error": "No valid data for any date in range."}, []

    all_results  = []
    for _, r, _ in daily_list:
        all_results.extend(r)

    train_rows  = [r for r in all_results if r.get("split") == "train"]
    test_rows   = [r for r in all_results if r.get("split") == "test"]

    agg_summary = _summarise(all_results, "All")
    agg_summary["days_run"]    = len(daily_list)
    agg_summary["slippage_pct"] = slippage_pct
    agg_summary["train"]       = _summarise(train_rows, "Train (in-sample)")
    agg_summary["test"]        = _summarise(test_rows,  "Test  (out-of-sample)")

    return all_results, agg_summary, daily_list


# ── Backtest Supabase persistence ────────────────────────────────────────────
def save_backtest_sim_runs(rows: list, user_id: str = ""):
    """Batch-insert backtest simulation rows to Supabase."""
    if not supabase or not rows:
        return
    try:
        records = []
        for r in rows:
            rec = {
                "user_id":        user_id or "",
                "sim_date":       str(r.get("sim_date", "")),
                "ticker":         r.get("ticker", ""),
                "open_price":     r.get("open_price"),
                "close_price":    r.get("close_price"),
                "ib_low":         r.get("ib_low"),
                "ib_high":        r.get("ib_high"),
                "tcs":            r.get("tcs"),
                "predicted":      r.get("predicted", ""),
                "actual_outcome": r.get("actual_outcome", ""),
                "win_loss":       r.get("win_loss", ""),
                "follow_thru_pct": r.get("aft_move_pct"),
                "false_break_up":   bool(r.get("false_break_up", False)),
                "false_break_down": bool(r.get("false_break_down", False)),
            }
            for _opt_f in ("vwap_at_ib", "ib_range_pct", "gap_pct", "gap_vs_ib_pct", "scan_type", "rvol"):
                _opt_v = r.get(_opt_f)
                if _opt_v is not None:
                    rec[_opt_f] = _opt_v
            # Auto-compute pnl_r_sim (simple sim P&L) on insert — no backfill needed for this field.
            # NOTE: tiered_pnl_r is intentionally omitted here; see comment below.
            _sim = apply_rvol_sizing_to_sim(compute_trade_sim(rec), rec.get("rvol"))
            if _sim.get("sim_outcome") not in ("no_trade", "missing_data", "invalid_ib", None):
                rec["sim_outcome"]      = _sim["sim_outcome"]
                rec["pnl_r_sim"]        = _sim.get("pnl_r_sim")
                rec["pnl_pct_sim"]      = _sim.get("pnl_pct_sim")
                rec["entry_price_sim"]  = _sim.get("entry_price_sim")
                rec["stop_price_sim"]   = _sim.get("stop_price_sim")
                rec["stop_dist_pct"]    = _sim.get("stop_dist_pct")
                rec["target_price_sim"] = _sim.get("target_price_sim")
                rec["sim_version"]      = SIM_VERSION
                # Compute eod_pnl_r when close_price is available.
                # NOTE: tiered_pnl_r is intentionally NOT stored here because
                # computing it requires bar-by-bar intraday data (aft_df) from
                # Alpaca, which is too slow to fetch at batch-insert time.
                # tiered_pnl_r will be NULL on every newly-inserted row until
                # run_tiered_pnl_backfill.py (or the dashboard backfill trigger)
                # is executed.  The count of pending rows is surfaced in the
                # "Backtest Sim P&L — Historical" section of the Performance tab.
                _close = r.get("close_price")
                if (_close is not None and rec.get("ib_high") is not None
                        and rec.get("ib_low") is not None
                        and rec.get("actual_outcome") in ("Bullish Break", "Bearish Break")):
                    _tiered = compute_trade_sim_tiered(
                        aft_df    = None,
                        ib_high   = rec["ib_high"],
                        ib_low    = rec["ib_low"],
                        direction = rec["actual_outcome"],
                        close_px  = _close,
                    )
                    if _tiered.get("eod_pnl_r") is not None:
                        rec["eod_pnl_r"] = _tiered["eod_pnl_r"]
                    # tiered_pnl_r left NULL — backfill required (see above)
            records.append(rec)
        supabase.table("backtest_sim_runs").insert(records).execute()
        try:
            _ref_result = refresh_mv_tiered_pnl_summary()
            if not _ref_result.get("success"):
                print(
                    f"save_backtest_sim_runs: mv refresh returned failure (non-fatal): "
                    f"{_ref_result.get('message', _ref_result)}"
                )
        except Exception as _ref_err:
            print(f"save_backtest_sim_runs: mv refresh failed (non-fatal): {_ref_err}")
    except Exception as e:
        print(f"Backtest save error: {e}")


_BACKTEST_SIM_COLS = (
    "id,user_id,sim_date,ticker,open_price,close_price,ib_low,ib_high,"
    "tcs,predicted,actual_outcome,win_loss,follow_thru_pct,false_break_up,"
    "false_break_down,scan_type,gap_pct,gap_vs_ib_pct,pnl_r_sim,pnl_pct_sim,"
    "eod_pnl_r,tiered_pnl_r,vwap_at_ib,screener_pass"
)


def load_backtest_sim_history(user_id: str = "") -> "pd.DataFrame":
    """Load saved backtest runs from Supabase using pagination (all rows).

    Only the columns consumed by the UI are fetched — this avoids transferring
    wide SELECT * payloads over the wire and is significantly faster with
    16 000+ rows in the table.

    Supabase caps single responses at 1 000 rows regardless of .limit().
    We paginate in 1 000-row pages until we have the full dataset.

    Rows whose tiered_pnl_r equals TIERED_PNL_SENTINEL (-9999) are permanently
    unfillable (no Alpaca bars available).  The sentinel is replaced with NaN so
    that all downstream .notna()/.dropna() filters exclude them automatically
    without any extra handling in the UI layer.
    """
    if not supabase:
        return pd.DataFrame()
    try:
        page_size = 1000
        offset = 0
        all_data: list = []
        while True:
            q = supabase.table("backtest_sim_runs").select(_BACKTEST_SIM_COLS)
            if user_id:
                q = q.eq("user_id", user_id)
            page = q.order("sim_date", desc=True).range(offset, offset + page_size - 1).execute().data or []
            all_data.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        df = pd.DataFrame(all_data) if all_data else pd.DataFrame()
        if not df.empty and "tiered_pnl_r" in df.columns:
            import numpy as _np
            df["tiered_pnl_r"] = pd.to_numeric(df["tiered_pnl_r"], errors="coerce")
            df.loc[df["tiered_pnl_r"] == TIERED_PNL_SENTINEL, "tiered_pnl_r"] = _np.nan
        return df
    except Exception as e:
        print(f"Backtest load error: {e}")
        return pd.DataFrame()


def load_backtest_saved_dates(user_id: str = "") -> list:
    """Return a sorted list of distinct sim_date strings saved by this user.

    Fetches only the sim_date column via pagination so this is fast even with
    tens of thousands of rows.  Returns newest dates first.
    """
    if not supabase:
        return []
    try:
        page_size = 1000
        offset = 0
        seen: set = set()
        while True:
            q = supabase.table("backtest_sim_runs").select("sim_date")
            if user_id:
                q = q.eq("user_id", user_id)
            page = q.order("sim_date", desc=True).range(offset, offset + page_size - 1).execute().data or []
            for row in page:
                seen.add(str(row["sim_date"]))
            if len(page) < page_size:
                break
            offset += page_size
        return sorted(seen, reverse=True)
    except Exception as e:
        print(f"load_backtest_saved_dates error: {e}")
        return []


def load_backtest_rows_for_dates(user_id: str, dates: list) -> "pd.DataFrame":
    """Load full backtest rows for a specific list of sim_date values.

    Uses .in_() filtering + pagination so only the requested dates are
    transferred.  This is much faster than loading the full history and
    filtering in memory.
    """
    if not supabase or not dates:
        return pd.DataFrame()
    try:
        page_size = 1000
        offset = 0
        all_data: list = []
        date_strs = [str(d) for d in dates]
        while True:
            q = (
                supabase.table("backtest_sim_runs")
                .select(_BACKTEST_SIM_COLS)
                .in_("sim_date", date_strs)
            )
            if user_id:
                q = q.eq("user_id", user_id)
            page = q.order("sim_date", desc=True).range(offset, offset + page_size - 1).execute().data or []
            all_data.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        df = pd.DataFrame(all_data) if all_data else pd.DataFrame()
        if not df.empty and "tiered_pnl_r" in df.columns:
            import numpy as _np
            df["tiered_pnl_r"] = pd.to_numeric(df["tiered_pnl_r"], errors="coerce")
            df.loc[df["tiered_pnl_r"] == TIERED_PNL_SENTINEL, "tiered_pnl_r"] = _np.nan
        return df
    except Exception as e:
        print(f"load_backtest_rows_for_dates error: {e}")
        return pd.DataFrame()


def get_backtest_pace_target(
    user_id: str = "",
    ticker: str = "",
    start_date: str = "",
    end_date: str = "",
) -> dict:
    """Compute the live-filter pace target from backtest_sim_runs.

    Applies the same entry-quality filter stack used by the live paper-trader
    bot:
      1. TCS >= 50
      2. IB range < configured threshold % of open price  (computed from
         ib_high, ib_low, open_price).  The threshold is read from
         ``load_ib_range_pct_threshold()`` (Supabase app_config → local JSON
         → compile-time default of 10.0) so that changing the IB threshold
         in the UI is immediately reflected in the pace target on the next
         cache-TTL cycle.
      3. VWAP alignment: Bullish → IB midpoint > vwap_at_ib
                         Bearish → IB midpoint < vwap_at_ib
         (only applied to rows where vwap_at_ib IS NOT NULL — older rows that
          pre-date vwap_at_ib logging fall through and are still counted,
          preserving backward compatibility with pre-vwap batch runs)

    Optional filters:
        ticker     – restrict to a single ticker (case-insensitive)
        start_date – restrict to sim_date >= start_date (ISO string, e.g. "2024-01-01")
        end_date   – restrict to sim_date <= end_date  (ISO string)

    Rows are fetched via offset pagination so there is no hard row-cap; the
    function always reflects the full table regardless of dataset size.

    Returns a dict:
        per_day      – avg qualifying setups per trading day  (float)
        per_year     – per_day × 250  (int)
        count        – total qualifying rows (TCS + IB + VWAP-aligned)
        bdays        – trading-day span (min→max sim_date of qualifying rows)
        min_date     – earliest qualifying sim_date
        max_date     – latest qualifying sim_date
        tcs_ib_count – rows passing TCS≥50 + IB < threshold (before VWAP gate)
        vwap_count   – rows passing all three filters (same as count)
        ib_threshold – the IB range % threshold used for this computation
        scope        – human-readable description of the active filters
        is_fallback  – True when no data exists and defaults are returned

    Args:
        user_id    – filter rows to this user (empty = all users)
        start_date – ISO date string (YYYY-MM-DD); only include rows where
                     sim_date >= start_date.  Empty string = no lower bound.
        end_date   – ISO date string (YYYY-MM-DD); only include rows where
                     sim_date <= end_date.  Empty string = no upper bound.

    Falls back to {"per_day": 0.81, "per_year": 202, "is_fallback": True}
    on any error or when the table has no qualifying rows.
    """
    _ib_threshold = load_ib_range_pct_threshold()

    # Build a human-readable scope label for CSV/Excel annotations
    _scope_parts: list = []
    if ticker:
        _scope_parts.append(f"{ticker.upper()} only")
    else:
        _scope_parts.append("All tickers")
    if start_date and end_date and start_date == end_date:
        _scope_parts.append(start_date)
    elif start_date and end_date:
        _scope_parts.append(f"{start_date}\u2013{end_date}")
    elif start_date:
        _scope_parts.append(f"from {start_date}")
    elif end_date:
        _scope_parts.append(f"to {end_date}")
    _scope = ", ".join(_scope_parts)

    _default: dict = {
        "count": 0, "bdays": 0,
        "per_day": 0.81, "per_year": 202,
        "min_date": "", "max_date": "",
        "tcs_ib_count": 0, "vwap_count": 0,
        "ib_threshold": _ib_threshold,
        "scope": _scope,
        "is_fallback": True,
    }
    if not supabase:
        return _default
    try:
        import pandas as _pd_bpt

        _PAGE = 1000
        _offset = 0
        _all_rows: list = []
        while True:
            q = (
                supabase.table("backtest_sim_runs")
                .select("sim_date,tcs,ib_high,ib_low,open_price,vwap_at_ib,predicted")
                .gte("tcs", 50)
            )
            if user_id:
                q = q.eq("user_id", user_id)
            if ticker:
                q = q.eq("ticker", ticker.upper())
            if start_date:
                q = q.gte("sim_date", start_date)
            if end_date:
                q = q.lte("sim_date", end_date)
            _chunk = q.range(_offset, _offset + _PAGE - 1).execute().data or []
            _all_rows.extend(_chunk)
            if len(_chunk) < _PAGE:
                break
            _offset += _PAGE

        if not _all_rows:
            return _default

        _df = _pd_bpt.DataFrame(_all_rows)
        for _col in ("tcs", "ib_high", "ib_low", "open_price", "vwap_at_ib"):
            _df[_col] = _pd_bpt.to_numeric(_df[_col], errors="coerce")

        _valid = (
            _df["open_price"].notna() & (_df["open_price"] > 0) &
            _df["ib_high"].notna() & _df["ib_low"].notna()
        )
        _df = _df[_valid].copy()

        _df["_ib_pct"] = (_df["ib_high"] - _df["ib_low"]) / _df["open_price"] * 100
        _df = _df[_df["_ib_pct"] < _ib_threshold]

        if _df.empty:
            return _default

        _tcs_ib_count = len(_df)

        _df["_ib_mid"] = (_df["ib_high"] + _df["ib_low"]) / 2

        _pred_s      = _df["predicted"].fillna("").str.lower()
        _bullish_mask = _pred_s.str.contains("bullish|long|up", na=False)
        _bearish_mask = _pred_s.str.contains("bearish|short|down", na=False)
        # Neutral / unknown direction: neither bullish nor bearish label.
        # The VWAP gate is directional (mid > vwap for longs, mid < vwap for
        # shorts), so it cannot be meaningfully applied to Neutral predictions.
        # Neutral rows pass through regardless of vwap_at_ib.
        _no_direction = ~_bullish_mask & ~_bearish_mask
        # _has_vwap: True only for rows with a real, positive VWAP.
        # Rows where vwap_at_ib is NULL (old rows without VWAP data) OR where
        # vwap_at_ib == VWAP_AT_IB_SENTINEL (-1.0, stamped when Alpaca has no
        # bars for that ticker/date) both pass through unconditionally so that
        # unfillable rows don't distort the count.
        _has_vwap    = _df["vwap_at_ib"].notna() & (_df["vwap_at_ib"] > 0)

        _vwap_ok = (
            (~_has_vwap) |                                                     # no VWAP data → pass through
            (_no_direction) |                                                  # Neutral predictions → pass through
            (_has_vwap & _bullish_mask & (_df["_ib_mid"] > _df["vwap_at_ib"])) |
            (_has_vwap & _bearish_mask & (_df["_ib_mid"] < _df["vwap_at_ib"]))
        )
        _df = _df[_vwap_ok]

        if _df.empty:
            return {
                **_default,
                "tcs_ib_count": _tcs_ib_count,
                "vwap_count":   0,
                "per_day":      0.0,
                "per_year":     0,
                "is_fallback":  False,
            }

        _cnt = len(_df)
        _dates = _pd_bpt.to_datetime(_df["sim_date"], errors="coerce").dropna()
        if _dates.empty:
            return _default

        _min_d = _dates.min().date()
        _max_d = _dates.max().date()
        _bdays = max(1, len(_pd_bpt.bdate_range(str(_min_d), str(_max_d))))
        _per_day = round(_cnt / _bdays, 2)
        _per_year = round(_per_day * 250)

        return {
            "count":         _cnt,
            "bdays":         _bdays,
            "per_day":       _per_day,
            "per_year":      _per_year,
            "min_date":      str(_min_d),
            "max_date":      str(_max_d),
            "tcs_ib_count":  _tcs_ib_count,
            "vwap_count":    _cnt,
            "ib_threshold":  _ib_threshold,
            "scope":         _scope,
            "is_fallback":   False,
        }
    except Exception as _bpt_err:
        logging.warning("get_backtest_pace_target: %s", _bpt_err)
        return _default


def get_ladder_pnl_summary(
    user_id: str = "",
    start_date: str = "",
    end_date: str = "",
) -> "pd.DataFrame":
    """Return pre-aggregated Ladder P&L summary stats from the materialised view.

    Reads *mv_tiered_pnl_summary* (created via the SQL in _ALL_PENDING_MIGRATIONS)
    which stores per-screener/scan_type/month roll-ups.  Falling back to an
    in-process aggregate from backtest_sim_runs if the view is unavailable.

    Parameters
    ----------
    user_id    : scope to a single user; empty string = all users.
    start_date : ISO date string (inclusive); empty = no lower bound.
    end_date   : ISO date string (inclusive); empty = no upper bound.

    Returns a DataFrame with columns:
        screener, scan_type, month, total_trades, wins, losses,
        avg_tiered_r, sum_tiered_r, avg_eod_r, sum_eod_r, win_rate_pct
    """
    if not supabase:
        return pd.DataFrame()

    try:
        q = supabase.table("mv_tiered_pnl_summary").select(
            "screener,scan_type,month,total_trades,wins,losses,"
            "avg_tiered_r,sum_tiered_r,avg_eod_r,sum_eod_r,win_rate_pct"
        )
        if user_id:
            q = q.eq("user_id", user_id)
        if start_date:
            q = q.gte("month", start_date)
        if end_date:
            q = q.lte("month", end_date)
        data = q.order("month", desc=True).execute().data
        return pd.DataFrame(data) if data else pd.DataFrame()
    except Exception as e:
        err = str(e)
        if "relation" in err.lower() and "does not exist" in err.lower():
            print(
                "get_ladder_pnl_summary: mv_tiered_pnl_summary not yet created. "
                "Run the SQL in _ALL_PENDING_MIGRATIONS in the Supabase SQL Editor."
            )
        else:
            print(f"get_ladder_pnl_summary error: {e}")
        return pd.DataFrame()


def _write_ladder_refresh_timestamp() -> None:
    """Persist the current UTC timestamp to _LADDER_REFRESH_META_FILE."""
    import json as _json
    import datetime as _dt

    ts = _dt.datetime.utcnow().isoformat() + "Z"
    try:
        with open(_LADDER_REFRESH_META_FILE, "w") as _f:
            _json.dump({"last_refreshed_utc": ts}, _f)
    except Exception as _exc:
        print(f"_write_ladder_refresh_timestamp: could not write meta file: {_exc}")


def _utc_to_et(utc_dt: "datetime.datetime") -> "datetime.datetime":
    """Convert a UTC datetime to US/Eastern, with DST-aware fallback.

    Prefers zoneinfo (stdlib ≥ 3.9); falls back to a fixed-offset
    approximation that accounts for EDT vs EST correctly.
    """
    import datetime as _dt

    try:
        from zoneinfo import ZoneInfo
        return utc_dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        month = utc_dt.month
        if 3 < month < 11:
            offset = -4
        elif month == 3:
            offset = -4 if utc_dt.day >= 8 else -5
        elif month == 11:
            offset = -5 if utc_dt.day >= 7 else -4
        else:
            offset = -5
        return utc_dt + _dt.timedelta(hours=offset)


def get_ladder_refresh_timestamp() -> str:
    """Return the last-refresh timestamp as a human-readable ET string.

    Reads _LADDER_REFRESH_META_FILE written by refresh_mv_tiered_pnl_summary().
    Returns an empty string if the file is absent or unreadable.
    Format: "Apr 16, 9:01 PM ET"
    """
    import json as _json
    import datetime as _dt

    try:
        with open(_LADDER_REFRESH_META_FILE) as _f:
            meta = _json.load(_f)
        ts_str = meta.get("last_refreshed_utc", "")
        if not ts_str:
            return ""
        utc_dt = _dt.datetime.fromisoformat(ts_str.rstrip("Z")).replace(
            tzinfo=_dt.timezone.utc
        )
        et_dt = _utc_to_et(utc_dt)
        return et_dt.strftime("%b %-d, %-I:%M %p ET")
    except FileNotFoundError:
        return ""
    except Exception as _exc:
        print(f"get_ladder_refresh_timestamp: {_exc}")
        return ""


def refresh_mv_tiered_pnl_summary() -> dict:
    """Trigger a REFRESH MATERIALIZED VIEW CONCURRENTLY on mv_tiered_pnl_summary.

    Designed to be called from a nightly scheduled job or manually from the
    admin panel.  Uses the exec_sql RPC so no direct DB connection is needed.

    Returns dict with keys: success (bool), message (str).
    """
    if not supabase:
        return {"success": False, "message": "Supabase not connected"}
    try:
        supabase.rpc(
            "exec_sql",
            {"query": "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_tiered_pnl_summary"},
        ).execute()
        _write_ladder_refresh_timestamp()
        return {"success": True, "message": "mv_tiered_pnl_summary refreshed successfully"}
    except Exception as e:
        err = str(e)
        if "PGRST202" in err:
            return {
                "success": False,
                "message": (
                    "exec_sql function not found — run CREATE FUNCTION in the "
                    "Supabase SQL Editor first (see _EXEC_SQL_FUNCTION constant)."
                ),
            }
        if "relation" in err.lower() and "does not exist" in err.lower():
            return {
                "success": False,
                "message": (
                    "mv_tiered_pnl_summary does not exist yet. "
                    "Run the SQL in _ALL_PENDING_MIGRATIONS to create it."
                ),
            }
        return {"success": False, "message": f"Refresh failed: {err}"}


def get_paper_ladder_pnl_summary(
    user_id: str = "",
    start_date: str = "",
    end_date: str = "",
) -> "pd.DataFrame":
    """Return pre-aggregated Ladder P&L summary stats for paper trades from the
    materialised view mv_paper_tiered_pnl_summary.

    Reads *mv_paper_tiered_pnl_summary* (created via the SQL in
    _ALL_PENDING_MIGRATIONS) which stores per-screener/scan_type/month roll-ups
    of paper_trades.tiered_pnl_r.  Returns an empty DataFrame if the view has
    not yet been created or is unavailable.

    Parameters
    ----------
    user_id    : scope to a single user; empty string = all users.
    start_date : ISO date string (inclusive); empty = no lower bound.
    end_date   : ISO date string (inclusive); empty = no upper bound.

    Returns a DataFrame with columns:
        screener, scan_type, month, total_trades, wins, losses,
        avg_tiered_r, sum_tiered_r, avg_eod_r, sum_eod_r, win_rate_pct
    """
    if not supabase:
        return pd.DataFrame()

    try:
        q = supabase.table("mv_paper_tiered_pnl_summary").select(
            "screener,scan_type,month,total_trades,wins,losses,"
            "avg_tiered_r,sum_tiered_r,avg_eod_r,sum_eod_r,win_rate_pct"
        )
        if user_id:
            q = q.eq("user_id", user_id)
        if start_date:
            q = q.gte("month", start_date)
        if end_date:
            q = q.lte("month", end_date)
        data = q.order("month", desc=True).execute().data
        return pd.DataFrame(data) if data else pd.DataFrame()
    except Exception as e:
        err = str(e)
        if "relation" in err.lower() and "does not exist" in err.lower():
            print(
                "get_paper_ladder_pnl_summary: mv_paper_tiered_pnl_summary not yet created. "
                "Run the SQL in _ALL_PENDING_MIGRATIONS in the Supabase SQL Editor."
            )
        else:
            print(f"get_paper_ladder_pnl_summary error: {e}")
        return pd.DataFrame()


def refresh_mv_paper_tiered_pnl_summary() -> dict:
    """Trigger a REFRESH MATERIALIZED VIEW CONCURRENTLY on mv_paper_tiered_pnl_summary.

    Designed to be called from a nightly scheduled job or manually from the
    admin panel.  Uses the exec_sql RPC so no direct DB connection is needed.

    Returns dict with keys: success (bool), message (str).
    """
    if not supabase:
        return {"success": False, "message": "Supabase not connected"}
    try:
        supabase.rpc(
            "exec_sql",
            {"query": "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_paper_tiered_pnl_summary"},
        ).execute()
        return {"success": True, "message": "mv_paper_tiered_pnl_summary refreshed successfully"}
    except Exception as e:
        err = str(e)
        if "PGRST202" in err:
            return {
                "success": False,
                "message": (
                    "exec_sql function not found — run CREATE FUNCTION in the "
                    "Supabase SQL Editor first (see _EXEC_SQL_FUNCTION constant)."
                ),
            }
        if "relation" in err.lower() and "does not exist" in err.lower():
            return {
                "success": False,
                "message": (
                    "mv_paper_tiered_pnl_summary does not exist yet. "
                    "Run the SQL in _ALL_PENDING_MIGRATIONS to create it."
                ),
            }
        return {"success": False, "message": f"Refresh failed: {err}"}


def count_backtest_tiered_pending(user_id: str = "") -> int:
    """Return the count of backtest_sim_runs rows that qualify for tiered P&L backfill.

    Qualifying rows have a Bullish/Bearish actual_outcome, NULL tiered_pnl_r,
    and all three price fields (close_price, ib_high, ib_low) populated.

    Pass *user_id* to scope the count to a single user (recommended for multi-tenant
    deployments). An empty string skips the user filter and counts all rows —
    appropriate for operator/admin use or single-tenant installations.
    """
    if not supabase:
        return 0
    try:
        q = (
            supabase.table("backtest_sim_runs")
            .select("id", count="exact")
            .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
            .is_("tiered_pnl_r", "null")
            .not_.is_("close_price", "null")
            .not_.is_("ib_high", "null")
            .not_.is_("ib_low", "null")
        )
        if user_id:
            q = q.eq("user_id", user_id)
        resp = q.execute()
        return resp.count or 0
    except Exception as e:
        print(f"count_backtest_tiered_pending error: {e}")
        return 0


def count_missing_close_price_in_range(
    start_date: str | None = None,
    end_date: str | None = None,
    user_id: str = "",
    table: str | None = None,
) -> int:
    """Return the total number of rows with close_price IS NULL within the
    given date range.  Used for the pre-flight check in the backfill UI.

    Parameters
    ----------
    start_date : str or None
        Lower bound (YYYY-MM-DD, inclusive).  None means no lower bound.
    end_date : str or None
        Upper bound (YYYY-MM-DD, inclusive).  None means no upper bound.
    user_id : str
        When non-empty only rows belonging to that user are counted.
    table : str or None
        When set to "backtest_sim_runs" or "paper_trades" only that table is
        queried.  None (default) queries both tables.

    Returns
    -------
    int
        Total null-close_price row count across the selected table(s).
        Returns 0 when Supabase is unavailable or an error occurs.
    """
    if not supabase:
        return 0
    try:
        all_tables = [
            ("backtest_sim_runs", "sim_date"),
            ("paper_trades",      "trade_date"),
        ]
        tables_to_query = (
            [(t, f) for t, f in all_tables if t == table]
            if table else all_tables
        )
        total = 0
        for tbl, date_field in tables_to_query:
            q = (
                supabase.table(tbl)
                .select("id", count="exact")
                .is_("close_price", "null")
            )
            if user_id:
                q = q.eq("user_id", user_id)
            if start_date:
                q = q.gte(date_field, start_date)
            if end_date:
                q = q.lte(date_field, end_date)
            resp = q.limit(1).execute()
            total += resp.count or 0
        return total
    except Exception as e:
        print(f"count_missing_close_price_in_range error: {e}")
        return 0


def count_backtest_tiered_sentinel(
    user_id: str = "",
    ticker: str = "",
    date_from: str = "",
    date_to: str = "",
) -> int:
    """Return the count of backtest_sim_runs rows stamped with the unavailability sentinel.

    Rows with tiered_pnl_r == TIERED_PNL_SENTINEL (-9999) were retired because
    Alpaca returned no bar data at the time of the backfill.  This function lets
    operators see how many such rows exist, optionally scoped to a ticker or
    sim_date range, before deciding whether to reset them.

    Parameters
    ----------
    user_id   : Scope to a single user (empty = all users).
    ticker    : Exact ticker symbol filter (empty = all tickers).
    date_from : Lower bound on sim_date, inclusive (YYYY-MM-DD, empty = no lower bound).
    date_to   : Upper bound on sim_date, inclusive (YYYY-MM-DD, empty = no upper bound).
    """
    if not supabase:
        return 0
    try:
        q = (
            supabase.table("backtest_sim_runs")
            .select("id", count="exact")
            .eq("tiered_pnl_r", TIERED_PNL_SENTINEL)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        if ticker:
            q = q.eq("ticker", ticker.upper())
        if date_from:
            q = q.gte("sim_date", date_from)
        if date_to:
            q = q.lte("sim_date", date_to)
        resp = q.execute()
        return resp.count or 0
    except Exception as e:
        print(f"count_backtest_tiered_sentinel error: {e}")
        return 0


def reset_backtest_tiered_sentinel(
    user_id: str = "",
    ticker: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """Clear the unavailability sentinel from backtest_sim_runs rows.

    Sets tiered_pnl_r back to NULL so the rows re-enter the IS NULL pending
    count and become eligible for a fresh backfill attempt.

    Parameters
    ----------
    user_id   : Scope to a single user (empty = all users).
    ticker    : Exact ticker symbol filter (empty = all tickers).
    date_from : Lower bound on sim_date, inclusive (YYYY-MM-DD, empty = no lower bound).
    date_to   : Upper bound on sim_date, inclusive (YYYY-MM-DD, empty = no upper bound).

    Returns
    -------
    dict with keys:
      reset  – int, number of rows whose sentinel was cleared.
      error  – str | None, error message if the operation failed.
    """
    if not supabase:
        return {"reset": 0, "error": "Supabase not initialised"}
    try:
        q = (
            supabase.table("backtest_sim_runs")
            .update({"tiered_pnl_r": None})
            .eq("tiered_pnl_r", TIERED_PNL_SENTINEL)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        if ticker:
            q = q.eq("ticker", ticker.upper())
        if date_from:
            q = q.gte("sim_date", date_from)
        if date_to:
            q = q.lte("sim_date", date_to)
        resp = q.execute()
        reset_count = len(resp.data) if resp.data else 0
        return {"reset": reset_count, "error": None}
    except Exception as e:
        print(f"reset_backtest_tiered_sentinel error: {e}")
        return {"reset": 0, "error": str(e)}


def list_backtest_tiered_sentinel_tickers(
    user_id: str = "",
    date_from: str = "",
    date_to: str = "",
    top_n: int = 50,
) -> dict:
    """Return a per-ticker breakdown of sentinel-stamped backtest rows.

    Rows with tiered_pnl_r == TIERED_PNL_SENTINEL (-9999) were retired because
    Alpaca returned no bar data.  This function lets operators see *which* tickers
    are affected and how many rows each one has, so they can prioritise
    investigation without having to query the database manually.

    Parameters
    ----------
    user_id   : Scope to a single user (empty = all users).
    date_from : Lower bound on sim_date, inclusive (YYYY-MM-DD, empty = no lower bound).
    date_to   : Upper bound on sim_date, inclusive (YYYY-MM-DD, empty = no upper bound).
    top_n     : Maximum number of tickers to return (sorted by row count descending).

    Returns
    -------
    dict with keys:
      total_sentinel      – int, total sentinel-stamped rows in scope.
      total_tickers       – int, number of distinct tickers affected.
      ticker_list_complete – bool, True when all rows were seen during aggregation.
      tickers             – list of dicts sorted descending by count:
                              {"ticker": str, "count": int,
                               "date_from": str | None, "date_to": str | None}
    """
    if not supabase:
        return {"total_sentinel": 0, "total_tickers": 0,
                "ticker_list_complete": True, "tickers": []}
    try:
        def _base_q():
            q = (
                supabase.table("backtest_sim_runs")
                .eq("tiered_pnl_r", TIERED_PNL_SENTINEL)
            )
            if user_id:
                q = q.eq("user_id", user_id)
            if date_from:
                q = q.gte("sim_date", date_from)
            if date_to:
                q = q.lte("sim_date", date_to)
            return q

        # 1. Exact total count (server-side).
        count_resp = _base_q().select("id", count="exact").limit(1).execute()
        total_sentinel = count_resp.count or 0
        if total_sentinel == 0:
            return {"total_sentinel": 0, "total_tickers": 0,
                    "ticker_list_complete": True, "tickers": []}

        # 2. Paginate to build per-ticker aggregation (count + date range).
        PAGE = 1000
        MAX_PAGES = 20
        ticker_counts: dict[str, int] = {}
        ticker_min: dict[str, str] = {}
        ticker_max: dict[str, str] = {}
        rows_seen = 0
        for page in range(MAX_PAGES):
            chunk = (
                _base_q()
                .select("ticker,sim_date")
                .range(page * PAGE, page * PAGE + PAGE - 1)
                .execute()
                .data or []
            )
            for r in chunk:
                t = r.get("ticker") or "UNKNOWN"
                d = r.get("sim_date") or ""
                ticker_counts[t] = ticker_counts.get(t, 0) + 1
                if d:
                    if t not in ticker_min or d < ticker_min[t]:
                        ticker_min[t] = d
                    if t not in ticker_max or d > ticker_max[t]:
                        ticker_max[t] = d
            rows_seen += len(chunk)
            if len(chunk) < PAGE:
                break

        ticker_list_complete = (rows_seen >= total_sentinel)
        sorted_tickers = sorted(
            ticker_counts.items(), key=lambda x: x[1], reverse=True
        )[:top_n]
        tickers = [
            {
                "ticker": t,
                "count": c,
                "date_from": ticker_min.get(t),
                "date_to": ticker_max.get(t),
            }
            for t, c in sorted_tickers
        ]
        return {
            "total_sentinel": total_sentinel,
            "total_tickers": len(ticker_counts),
            "ticker_list_complete": ticker_list_complete,
            "tickers": tickers,
        }
    except Exception as e:
        print(f"list_backtest_tiered_sentinel_tickers error: {e}")
        return {"total_sentinel": 0, "total_tickers": 0,
                "ticker_list_complete": True, "tickers": []}


def count_paper_trades_tiered_sentinel(
    user_id: str = "",
    ticker: str = "",
    date_from: str = "",
    date_to: str = "",
) -> int:
    """Return the count of paper_trades rows stamped with the unavailability sentinel.

    Rows with tiered_pnl_r == TIERED_PNL_SENTINEL (-9999) were retired because
    Alpaca returned no bar data at the time of the backfill.  This function lets
    operators see how many such rows exist, optionally scoped to a ticker or
    trade_date range, before deciding whether to reset them.

    Parameters
    ----------
    user_id   : Scope to a single user (empty = all users).
    ticker    : Exact ticker symbol filter (empty = all tickers).
    date_from : Lower bound on trade_date, inclusive (YYYY-MM-DD, empty = no lower bound).
    date_to   : Upper bound on trade_date, inclusive (YYYY-MM-DD, empty = no upper bound).
    """
    if not supabase:
        return 0
    try:
        q = (
            supabase.table("paper_trades")
            .select("id", count="exact")
            .eq("tiered_pnl_r", TIERED_PNL_SENTINEL)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        if ticker:
            q = q.eq("ticker", ticker.upper())
        if date_from:
            q = q.gte("trade_date", date_from)
        if date_to:
            q = q.lte("trade_date", date_to)
        resp = q.execute()
        return resp.count or 0
    except Exception as e:
        print(f"count_paper_trades_tiered_sentinel error: {e}")
        return 0


def reset_paper_trades_tiered_sentinel(
    user_id: str = "",
    ticker: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """Clear the unavailability sentinel from paper_trades rows.

    Sets tiered_pnl_r back to NULL so the rows re-enter the IS NULL pending
    count and become eligible for a fresh backfill attempt.

    Parameters
    ----------
    user_id   : Scope to a single user (empty = all users).
    ticker    : Exact ticker symbol filter (empty = all tickers).
    date_from : Lower bound on trade_date, inclusive (YYYY-MM-DD, empty = no lower bound).
    date_to   : Upper bound on trade_date, inclusive (YYYY-MM-DD, empty = no upper bound).

    Returns
    -------
    dict with keys:
      reset  – int, number of rows whose sentinel was cleared.
      error  – str | None, error message if the operation failed.
    """
    if not supabase:
        return {"reset": 0, "error": "Supabase not initialised"}
    try:
        q = (
            supabase.table("paper_trades")
            .update({"tiered_pnl_r": None})
            .eq("tiered_pnl_r", TIERED_PNL_SENTINEL)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        if ticker:
            q = q.eq("ticker", ticker.upper())
        if date_from:
            q = q.gte("trade_date", date_from)
        if date_to:
            q = q.lte("trade_date", date_to)
        resp = q.execute()
        reset_count = len(resp.data) if resp.data else 0
        return {"reset": reset_count, "error": None}
    except Exception as e:
        print(f"reset_paper_trades_tiered_sentinel error: {e}")
        return {"reset": 0, "error": str(e)}


def get_paper_trades_tiered_sentinel_stats(
    user_id: str = "",
    ticker: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """Return a per-ticker breakdown of paper_trades rows stamped with the unavailability sentinel.

    Rows with tiered_pnl_r == TIERED_PNL_SENTINEL (-9999) were retired because
    Alpaca returned no bar data at the time of the backfill.  This function lets
    operators see which tickers are affected most, without transferring all rows.

    Parameters
    ----------
    user_id   : Scope to a single user (empty = all users).
    ticker    : Exact ticker symbol filter (empty = all tickers).
    date_from : Lower bound on trade_date, inclusive (YYYY-MM-DD, empty = no lower bound).
    date_to   : Upper bound on trade_date, inclusive (YYYY-MM-DD, empty = no upper bound).

    Returns
    -------
    dict with keys:
      total_sentinel       – int, exact count of sentinel-stamped rows
      total_tickers        – int, number of distinct tickers affected
      top_tickers          – list of {"ticker": str, "count": int} sorted descending (top 10)
      ticker_list_complete – bool, True when all affected rows were seen during aggregation
    """
    if not supabase:
        return {"total_sentinel": 0, "total_tickers": 0, "top_tickers": [],
                "ticker_list_complete": True}
    try:
        def _base_q():
            q = (
                supabase.table("paper_trades")
                .eq("tiered_pnl_r", TIERED_PNL_SENTINEL)
            )
            if user_id:
                q = q.eq("user_id", user_id)
            if ticker:
                q = q.eq("ticker", ticker.upper())
            if date_from:
                q = q.gte("trade_date", date_from)
            if date_to:
                q = q.lte("trade_date", date_to)
            return q

        # 1. Exact total count (server-side, no row data transferred)
        count_resp = _base_q().select("id", count="exact").limit(1).execute()
        total_sentinel = count_resp.count or 0
        if total_sentinel == 0:
            return {"total_sentinel": 0, "total_tickers": 0, "top_tickers": [],
                    "ticker_list_complete": True}

        # 2. Paginate ticker column to build per-ticker breakdown.
        #    Page size 1000; stop after 20 pages (20 000 rows) to bound latency.
        PAGE = 1000
        MAX_PAGES = 20
        counts: dict[str, int] = {}
        rows_seen = 0
        for page in range(MAX_PAGES):
            chunk = (
                _base_q()
                .select("ticker")
                .range(page * PAGE, page * PAGE + PAGE - 1)
                .execute()
                .data or []
            )
            for r in chunk:
                t = r.get("ticker") or "UNKNOWN"
                counts[t] = counts.get(t, 0) + 1
            rows_seen += len(chunk)
            if len(chunk) < PAGE:
                break  # last page reached

        ticker_list_complete = (rows_seen >= total_sentinel)
        top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
        return {
            "total_sentinel": total_sentinel,
            "total_tickers": len(counts),
            "top_tickers": [{"ticker": t, "count": c} for t, c in top],
            "ticker_list_complete": ticker_list_complete,
        }
    except Exception as e:
        print(f"get_paper_trades_tiered_sentinel_stats error: {e}")
        return {"total_sentinel": 0, "total_tickers": 0, "top_tickers": [],
                "ticker_list_complete": True}


def list_paper_trades_tiered_sentinel_tickers(
    user_id: str = "",
    ticker: str = "",
    date_from: str = "",
    date_to: str = "",
    top_n: int = 50,
) -> dict:
    """Return a per-ticker breakdown of sentinel-stamped paper_trades rows.

    Rows with tiered_pnl_r == TIERED_PNL_SENTINEL (-9999) were retired because
    Alpaca returned no bar data.  This function lets operators see which tickers
    are affected, how many rows each has, and the earliest/latest trade_date seen,
    so they can prioritise investigation without querying the database manually.

    Parameters
    ----------
    user_id   : Scope to a single user (empty = all users).
    ticker    : Exact ticker symbol filter (empty = all tickers).
    date_from : Lower bound on trade_date, inclusive (YYYY-MM-DD, empty = no lower bound).
    date_to   : Upper bound on trade_date, inclusive (YYYY-MM-DD, empty = no upper bound).
    top_n     : Maximum number of tickers to return (sorted by row count descending).

    Returns
    -------
    dict with keys:
      total_sentinel       – int, total sentinel-stamped rows in scope.
      total_tickers        – int, number of distinct tickers affected.
      ticker_list_complete – bool, True when all rows were seen during aggregation.
      tickers              – list of dicts sorted descending by count:
                               {"ticker": str, "count": int,
                                "date_from": str | None, "date_to": str | None}
    """
    if not supabase:
        return {"total_sentinel": 0, "total_tickers": 0,
                "ticker_list_complete": True, "tickers": []}
    try:
        def _base_q():
            q = (
                supabase.table("paper_trades")
                .eq("tiered_pnl_r", TIERED_PNL_SENTINEL)
            )
            if user_id:
                q = q.eq("user_id", user_id)
            if ticker:
                q = q.eq("ticker", ticker.upper())
            if date_from:
                q = q.gte("trade_date", date_from)
            if date_to:
                q = q.lte("trade_date", date_to)
            return q

        count_resp = _base_q().select("id", count="exact").limit(1).execute()
        total_sentinel = count_resp.count or 0
        if total_sentinel == 0:
            return {"total_sentinel": 0, "total_tickers": 0,
                    "ticker_list_complete": True, "tickers": []}

        PAGE = 1000
        MAX_PAGES = 20
        ticker_counts: dict[str, int] = {}
        ticker_min: dict[str, str] = {}
        ticker_max: dict[str, str] = {}
        rows_seen = 0
        for page in range(MAX_PAGES):
            chunk = (
                _base_q()
                .select("ticker,trade_date")
                .range(page * PAGE, page * PAGE + PAGE - 1)
                .execute()
                .data or []
            )
            for r in chunk:
                t = r.get("ticker") or "UNKNOWN"
                d = r.get("trade_date") or ""
                ticker_counts[t] = ticker_counts.get(t, 0) + 1
                if d:
                    if t not in ticker_min or d < ticker_min[t]:
                        ticker_min[t] = d
                    if t not in ticker_max or d > ticker_max[t]:
                        ticker_max[t] = d
            rows_seen += len(chunk)
            if len(chunk) < PAGE:
                break

        ticker_list_complete = (rows_seen >= total_sentinel)
        sorted_tickers = sorted(
            ticker_counts.items(), key=lambda x: x[1], reverse=True
        )[:top_n]
        tickers = [
            {
                "ticker": t,
                "count": c,
                "date_from": ticker_min.get(t),
                "date_to": ticker_max.get(t),
            }
            for t, c in sorted_tickers
        ]
        return {
            "total_sentinel": total_sentinel,
            "total_tickers": len(ticker_counts),
            "ticker_list_complete": ticker_list_complete,
            "tickers": tickers,
        }
    except Exception as e:
        print(f"list_paper_trades_tiered_sentinel_tickers error: {e}")
        return {"total_sentinel": 0, "total_tickers": 0,
                "ticker_list_complete": True, "tickers": []}


def get_missing_close_price_stats(user_id: str = "") -> dict:
    """Return stats on backtest_sim_runs rows that have no close_price.

    These are rows for Bullish/Bearish outcomes where EOD P&L cannot be computed
    because the EOD close price was never fetched (delisted tickers, OTC names, etc.).

    Uses two queries:
      1. An exact server-side count (no row data transferred).
      2. A paginated ticker fetch to build a full per-ticker breakdown.

    Returns a dict:
      total_missing        – int, exact count of qualifying rows with NULL close_price
      total_tickers        – int, number of distinct tickers affected
      top_tickers          – list of {"ticker": str, "count": int} sorted descending (top 10)
      all_tickers          – list of {"ticker": str, "count": int} full sorted list (up to
                             MAX_PAGES * PAGE rows sampled); use for CSV export
      ticker_list_complete – bool, True when all affected rows were seen during aggregation
                             (False means the export may be a partial sample)
    """
    if not supabase:
        return {"total_missing": 0, "total_tickers": 0, "top_tickers": [], "all_tickers": [],
                "ticker_list_complete": True}
    try:
        def _base_q():
            q = (
                supabase.table("backtest_sim_runs")
                .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
                .is_("close_price", "null")
            )
            if user_id:
                q = q.eq("user_id", user_id)
            return q

        # 1. Exact total count (server-side, no row data; head=True suppresses row payload)
        count_resp = _base_q().select("id", count="exact").limit(1).execute()
        total_missing = count_resp.count or 0
        if total_missing == 0:
            return {"total_missing": 0, "total_tickers": 0, "top_tickers": [],
                    "ticker_list_complete": True}

        # 2. Paginate ticker column to build per-ticker breakdown.
        #    Page size 1000; stop after 20 pages (20 000 rows) to bound latency.
        PAGE = 1000
        MAX_PAGES = 20
        counts: dict[str, int] = {}
        rows_seen = 0
        for page in range(MAX_PAGES):
            chunk = (
                _base_q()
                .select("ticker")
                .range(page * PAGE, page * PAGE + PAGE - 1)
                .execute()
                .data or []
            )
            for r in chunk:
                t = r.get("ticker") or "UNKNOWN"
                counts[t] = counts.get(t, 0) + 1
            rows_seen += len(chunk)
            if len(chunk) < PAGE:
                break  # last page reached

        ticker_list_complete = (rows_seen >= total_missing)
        all_sorted = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        top = all_sorted[:10]
        return {
            "total_missing": total_missing,
            "total_tickers": len(counts),
            "top_tickers": [{"ticker": t, "count": c} for t, c in top],
            "all_tickers": [{"ticker": t, "count": c} for t, c in all_sorted],
            "ticker_list_complete": ticker_list_complete,
        }
    except Exception as e:
        print(f"get_missing_close_price_stats error: {e}")
        return {"total_missing": 0, "total_tickers": 0, "top_tickers": [],
                "ticker_list_complete": True}


def get_paper_trade_missing_close_price_stats(user_id: str = "") -> dict:
    """Return stats on paper_trades rows that are resolved but have no close_price.

    Resolved trades are those with win_loss in ('W', 'L', 'Win', 'Loss').
    A missing close_price means EOD P&L (eod_pnl_r) cannot be computed for those
    rows — typically caused by delisted tickers, OTC names, or failed EOD fetches.

    Returns a dict:
      total_missing        – int, exact count of qualifying rows with NULL close_price
      total_tickers        – int, number of distinct tickers affected
      top_tickers          – list of {"ticker": str, "count": int} sorted descending (top 10)
      all_tickers          – list of {"ticker": str, "count": int} full sorted list (up to
                             MAX_PAGES * PAGE rows sampled); use for CSV export
      ticker_list_complete – bool, True when all affected rows were seen during aggregation
                             (False means the export may be a partial sample)
    """
    if not supabase:
        return {"total_missing": 0, "total_tickers": 0, "top_tickers": [], "all_tickers": [],
                "ticker_list_complete": True}
    try:
        def _base_q():
            q = (
                supabase.table("paper_trades")
                .in_("win_loss", ["W", "L", "Win", "Loss"])
                .is_("close_price", "null")
            )
            if user_id:
                q = q.eq("user_id", user_id)
            return q

        # 1. Exact total count (server-side, no row data)
        count_resp = _base_q().select("id", count="exact").limit(1).execute()
        total_missing = count_resp.count or 0
        if total_missing == 0:
            return {"total_missing": 0, "total_tickers": 0, "top_tickers": [],
                    "ticker_list_complete": True}

        # 2. Paginate ticker column to build per-ticker breakdown.
        PAGE = 1000
        MAX_PAGES = 20
        counts: dict[str, int] = {}
        rows_seen = 0
        for page in range(MAX_PAGES):
            chunk = (
                _base_q()
                .select("ticker")
                .range(page * PAGE, page * PAGE + PAGE - 1)
                .execute()
                .data or []
            )
            for r in chunk:
                t = r.get("ticker") or "UNKNOWN"
                counts[t] = counts.get(t, 0) + 1
            rows_seen += len(chunk)
            if len(chunk) < PAGE:
                break

        ticker_list_complete = (rows_seen >= total_missing)
        all_sorted = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        top = all_sorted[:10]
        return {
            "total_missing": total_missing,
            "total_tickers": len(counts),
            "top_tickers": [{"ticker": t, "count": c} for t, c in top],
            "all_tickers": [{"ticker": t, "count": c} for t, c in all_sorted],
            "ticker_list_complete": ticker_list_complete,
        }
    except Exception as e:
        print(f"get_paper_trade_missing_close_price_stats error: {e}")
        return {"total_missing": 0, "total_tickers": 0, "top_tickers": [],
                "ticker_list_complete": True}


def run_close_price_backfill_batch(user_id: str = "", dry_run: bool = False,
                                   progress_callback=None) -> dict:
    """Attempt to re-fetch EOD close prices from Alpaca for all rows that are
    currently missing close_price in backtest_sim_runs and paper_trades.

    Groups null rows by date so only one Alpaca call is made per day, then
    writes results back to Supabase concurrently.

    Returns a dict with keys:
      total_rows    – number of rows with null close_price at the start
      filled        – number of rows successfully updated with a close price
      still_missing – number of rows that remained unfilled (no Alpaca data)
      errors        – number of write errors encountered

    progress_callback(done: int, total: int, date_str: str) — optional callable
      invoked after each date batch is processed so callers can display live
      progress.  ``done`` is the number of dates completed so far, ``total`` is
      the total number of unique dates to process, and ``date_str`` is the date
      string that was just finished.
    """
    from datetime import datetime, timedelta
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor, as_completed

    TABLES = [
        ("backtest_sim_runs", "sim_date"),
        ("paper_trades",      "trade_date"),
    ]
    PAGE_SZ       = 1000
    BATCH_TICKERS = 50
    MAX_WORKERS   = 20

    stats = {"total_rows": 0, "filled": 0, "still_missing": 0, "errors": 0}

    if not supabase:
        stats["errors"] = 1
        return stats

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        stats["errors"] = 1
        return stats

    def _fetch_null_rows(table: str, date_field: str) -> list:
        rows, offset = [], 0
        while True:
            q = (
                supabase.table(table)
                .select(f"id,ticker,{date_field}")
                .is_("close_price", "null")
            )
            if user_id:
                q = q.eq("user_id", user_id)
            chunk = q.range(offset, offset + PAGE_SZ - 1).execute().data or []
            rows.extend(chunk)
            if len(chunk) < PAGE_SZ:
                break
            offset += PAGE_SZ
        return rows

    def _fetch_closes_for_date(trade_date_str: str, tickers: list) -> dict:
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests  import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            import pandas as pd

            client     = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
            trade_date = datetime.strptime(trade_date_str, "%Y-%m-%d")
            start_dt   = trade_date
            end_dt     = trade_date + timedelta(days=1)
            closes: dict = {}

            for i in range(0, len(tickers), BATCH_TICKERS):
                batch = tickers[i: i + BATCH_TICKERS]
                try:
                    req  = StockBarsRequest(
                        symbol_or_symbols=batch,
                        timeframe=TimeFrame.Day,
                        start=start_dt,
                        end=end_dt,
                        feed="iex",
                    )
                    bars = client.get_stock_bars(req)
                    df   = bars.df
                    if df.empty:
                        continue
                    if isinstance(df.index, pd.MultiIndex):
                        for sym in batch:
                            try:
                                sym_df = df.xs(sym, level="symbol")
                                if not sym_df.empty:
                                    closes[sym] = float(sym_df["close"].iloc[-1])
                            except KeyError:
                                pass
                    else:
                        if not df.empty and len(batch) == 1:
                            closes[batch[0]] = float(df["close"].iloc[-1])
                except Exception as _e:
                    print(f"run_close_price_backfill_batch alpaca error {trade_date_str}: {_e}")
            return closes
        except Exception as _e:
            print(f"run_close_price_backfill_batch fetch_closes error: {_e}")
            return {}

    def _update_one(table: str, row_id, close_price: float):
        supabase.table(table).update(
            {"close_price": round(close_price, 4)}
        ).eq("id", row_id).execute()

    # ── Phase 1: collect all date-batches across both tables ─────────────────
    all_work: list = []  # [(table, date_field, trade_date_str, date_rows), …]
    for table, date_field in TABLES:
        try:
            rows = _fetch_null_rows(table, date_field)
        except Exception as e:
            print(f"run_close_price_backfill_batch fetch error ({table}): {e}")
            stats["errors"] += 1
            continue

        stats["total_rows"] += len(rows)

        by_date: dict = defaultdict(list)
        for r in rows:
            sd = (r.get(date_field) or "")[:10]
            if sd:
                by_date[sd].append(r)

        for trade_date_str, date_rows in sorted(by_date.items()):
            all_work.append((table, date_field, trade_date_str, date_rows))

    total_dates = len(all_work)

    # ── Phase 2: process each date-batch, firing progress callback each time ─
    for work_idx, (table, date_field, trade_date_str, date_rows) in enumerate(all_work):
        tickers = sorted({r.get("ticker", "") for r in date_rows if r.get("ticker")})
        if not tickers:
            stats["still_missing"] += len(date_rows)
            if progress_callback:
                progress_callback(work_idx + 1, total_dates, trade_date_str)
            continue

        closes = _fetch_closes_for_date(trade_date_str, tickers)

        updates = []
        for r in date_rows:
            ticker = r.get("ticker", "")
            cp     = closes.get(ticker)
            if cp is None:
                stats["still_missing"] += 1
            else:
                updates.append((r["id"], cp))

        if dry_run or not updates:
            stats["filled"] += len(updates)
            if progress_callback:
                progress_callback(work_idx + 1, total_dates, trade_date_str)
            continue

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_update_one, table, rid, cp): rid
                       for rid, cp in updates}
            for fut in as_completed(futures):
                try:
                    fut.result()
                    stats["filled"] += 1
                except Exception as e:
                    print(f"run_close_price_backfill_batch write error: {e}")
                    stats["errors"] += 1

        if progress_callback:
            progress_callback(work_idx + 1, total_dates, trade_date_str)

    return stats


def recompute_eod_pnl_for_filled_rows(user_id: str = "", progress_callback=None) -> dict:
    """Recompute eod_pnl_r for backtest_sim_runs and paper_trades rows that now
    have a close_price but are still missing eod_pnl_r.

    Called immediately after run_close_price_backfill_batch() fills in close prices
    so the dashboard reflects up-to-date EOD P&L without requiring a separate
    run_sim_backfill.py run.

    Only processes rows with a known direction (Bullish/Bearish Break) and valid
    IB range — the same prerequisites used by compute_trade_sim_tiered().

    Returns a dict with keys:
      recomputed_backtest – rows from backtest_sim_runs successfully updated with eod_pnl_r
      recomputed_paper    – rows from paper_trades successfully updated with eod_pnl_r
      skipped             – rows processed but not updated (bad data / compute returned None)
      errors              – write errors encountered
    """
    PAGE_SZ = 1000
    MAX_WORKERS = 20
    from concurrent.futures import ThreadPoolExecutor, as_completed

    stats = {"recomputed_backtest": 0, "recomputed_paper": 0, "skipped": 0, "errors": 0}

    if not supabase:
        stats["errors"] = 1
        return stats

    TABLES = [
        "backtest_sim_runs",
        "paper_trades",
    ]

    all_rows: list[tuple[str, dict]] = []

    for table in TABLES:
        offset = 0
        while True:
            q = (
                supabase.table(table)
                .select("id,actual_outcome,ib_high,ib_low,close_price")
                .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
                .is_("eod_pnl_r", "null")
                .not_.is_("close_price", "null")
                .not_.is_("ib_high", "null")
                .not_.is_("ib_low", "null")
            )
            if user_id:
                q = q.eq("user_id", user_id)
            chunk = q.range(offset, offset + PAGE_SZ - 1).execute().data or []
            all_rows.extend((table, row) for row in chunk)
            if len(chunk) < PAGE_SZ:
                break
            offset += PAGE_SZ

    def _compute_and_write(table_row):
        table, row = table_row
        result = compute_trade_sim_tiered(
            aft_df=None,
            ib_high=row.get("ib_high"),
            ib_low=row.get("ib_low"),
            direction=row.get("actual_outcome", ""),
            close_px=row.get("close_price"),
        )
        eod = result.get("eod_pnl_r")
        if eod is None:
            return "skipped"
        supabase.table(table).update(
            {"eod_pnl_r": eod}
        ).eq("id", row["id"]).execute()
        return "recomputed_backtest" if table == "backtest_sim_runs" else "recomputed_paper"

    total_rows = len(all_rows)
    done_count = 0

    if progress_callback and total_rows > 0:
        progress_callback(0, total_rows)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_compute_and_write, tr): tr[1]["id"] for tr in all_rows}
        for fut in as_completed(futures):
            try:
                outcome = fut.result()
                stats[outcome] += 1
            except Exception as e:
                print(f"recompute_eod_pnl_for_filled_rows write error: {e}")
                stats["errors"] += 1
            done_count += 1
            if progress_callback:
                progress_callback(done_count, total_rows)

    return stats


def sync_sim_fields_backtest(user_id: str = "") -> dict:
    """Fill sim_outcome and related sim fields for backtest_sim_runs rows where
    sim_outcome IS NULL.

    Computes sim fields via compute_trade_sim() and writes a patch that includes
    sim_version so that subsequent --skip-existing runs in run_sim_backfill.py
    correctly skip the rows that were filled here.

    Uses keyset (cursor) pagination ordered by id so that updating rows mid-loop
    does not cause the offset window to skip un-processed rows.

    Returns a dict with keys:
      updated  – rows successfully patched
      skipped  – rows where sim computation returned no_trade / missing / invalid
      errors   – write errors encountered
    """
    PAGE_SZ = 1000
    stats = {"updated": 0, "skipped": 0, "errors": 0}

    if not supabase:
        stats["errors"] = 1
        return stats

    _SIM_COLS = (
        "id,predicted,actual_outcome,ib_low,ib_high,"
        "follow_thru_pct,close_price,false_break_up,false_break_down,rvol"
    )

    last_id = None
    while True:
        try:
            q = (
                supabase.table("backtest_sim_runs")
                .select(_SIM_COLS)
                .is_("sim_outcome", "null")
                .order("id")
                .limit(PAGE_SZ)
            )
            if user_id:
                q = q.eq("user_id", user_id)
            if last_id is not None:
                q = q.gt("id", last_id)
            chunk = q.execute().data or []
        except Exception as _fetch_err:
            print(f"sync_sim_fields_backtest fetch error: {_fetch_err}")
            stats["errors"] += 1
            break

        for row in chunk:
            last_id = row["id"]
            try:
                sim = apply_rvol_sizing_to_sim(compute_trade_sim(row), row.get("rvol"))
                if sim.get("sim_outcome") in ("no_trade", "missing_data", "invalid_ib", None):
                    stats["skipped"] += 1
                    continue
                patch = {
                    "sim_outcome":      sim["sim_outcome"],
                    "pnl_r_sim":        sim.get("pnl_r_sim"),
                    "pnl_pct_sim":      sim.get("pnl_pct_sim"),
                    "entry_price_sim":  sim.get("entry_price_sim"),
                    "stop_price_sim":   sim.get("stop_price_sim"),
                    "stop_dist_pct":    sim.get("stop_dist_pct"),
                    "target_price_sim": sim.get("target_price_sim"),
                    "sim_version":      SIM_VERSION,
                }
                supabase.table("backtest_sim_runs").update(patch).eq("id", row["id"]).execute()
                stats["updated"] += 1
            except Exception as _upd_err:
                print(f"sync_sim_fields_backtest update error id={row.get('id')}: {_upd_err}")
                stats["errors"] += 1

        if len(chunk) < PAGE_SZ:
            break

    return stats


def sync_sim_fields_paper(user_id: str = "") -> dict:
    """Fill sim_outcome and related sim fields for paper_trades rows where
    sim_outcome IS NULL.

    Computes sim fields via compute_trade_sim() and writes a patch that includes
    sim_version so that subsequent --skip-existing runs in run_sim_backfill.py
    correctly skip the rows that were filled here.

    Uses keyset (cursor) pagination ordered by id so that updating rows mid-loop
    does not cause the offset window to skip un-processed rows.

    Returns a dict with keys:
      updated  – rows successfully patched
      skipped  – rows where sim computation returned no_trade / missing / invalid
      errors   – write errors encountered
    """
    PAGE_SZ = 1000
    stats = {"updated": 0, "skipped": 0, "errors": 0}

    if not supabase:
        stats["errors"] = 1
        return stats

    _SIM_COLS = (
        "id,predicted,actual_outcome,ib_low,ib_high,"
        "follow_thru_pct,close_price,false_break_up,false_break_down,rvol"
    )

    last_id = None
    while True:
        try:
            q = (
                supabase.table("paper_trades")
                .select(_SIM_COLS)
                .is_("sim_outcome", "null")
                .order("id")
                .limit(PAGE_SZ)
            )
            if user_id:
                q = q.eq("user_id", user_id)
            if last_id is not None:
                q = q.gt("id", last_id)
            chunk = q.execute().data or []
        except Exception as _fetch_err:
            print(f"sync_sim_fields_paper fetch error: {_fetch_err}")
            stats["errors"] += 1
            break

        for row in chunk:
            last_id = row["id"]
            try:
                sim = apply_rvol_sizing_to_sim(compute_trade_sim(row), row.get("rvol"))
                if sim.get("sim_outcome") in ("no_trade", "missing_data", "invalid_ib", None):
                    stats["skipped"] += 1
                    continue
                patch = {
                    "sim_outcome":      sim["sim_outcome"],
                    "pnl_r_sim":        sim.get("pnl_r_sim"),
                    "pnl_pct_sim":      sim.get("pnl_pct_sim"),
                    "entry_price_sim":  sim.get("entry_price_sim"),
                    "stop_price_sim":   sim.get("stop_price_sim"),
                    "stop_dist_pct":    sim.get("stop_dist_pct"),
                    "target_price_sim": sim.get("target_price_sim"),
                    "sim_version":      SIM_VERSION,
                }
                supabase.table("paper_trades").update(patch).eq("id", row["id"]).execute()
                stats["updated"] += 1
            except Exception as _upd_err:
                print(f"sync_sim_fields_paper update error id={row.get('id')}: {_upd_err}")
                stats["errors"] += 1

        if len(chunk) < PAGE_SZ:
            break

    return stats


def run_backtest_tiered_backfill_batch(batch_size: int = 25, dry_run: bool = False,
                                       user_id: str = "",
                                       exclude_ids: list | None = None) -> dict:
    """Process one batch of backtest_sim_runs rows missing tiered_pnl_r.

    Fetches up to *batch_size* qualifying rows (oldest first by sim_date then id),
    pulls 1-minute bars from Alpaca for each, computes tiered_pnl_r via the
    50/25/25 ladder, and writes the result back to the DB.  Returns a summary
    dict with keys: fetched, updated, skipped_no_bars, skipped_no_tiered, errors,
    remaining, skipped_ids.

    *exclude_ids* — list of row IDs to skip in this batch.  Pass the accumulated
    ``skipped_ids`` from previous batches so the full-backfill loop can advance past
    rows that are permanently unprocessable (no Alpaca bars, no entry cross).  This
    mirrors the skipped_ids exclusion used by the CLI script and prevents the loop
    from re-fetching the same unprocessable rows indefinitely.

    Pass *user_id* to limit processing to one user's rows (recommended in
    multi-tenant deployments). Empty string processes all users' rows.

    Use this for the one-click dashboard trigger.  For large backlogs without a UI,
    run ``python run_tiered_pnl_backfill.py --backtest-only`` from the shell.
    """
    from datetime import date as _date, datetime as _datetime
    stats = {"fetched": 0, "updated": 0, "skipped_no_bars": 0,
             "skipped_no_tiered": 0, "errors": 0, "remaining": 0,
             "skipped_ids": []}

    if not supabase:
        stats["errors"] = 1
        return stats

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        stats["errors"] = 1
        return stats

    try:
        q = (
            supabase.table("backtest_sim_runs")
            .select("id,ticker,sim_date,actual_outcome,ib_high,ib_low,close_price,eod_pnl_r")
            .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
            .is_("tiered_pnl_r", "null")
            .not_.is_("close_price", "null")
            .not_.is_("ib_high", "null")
            .not_.is_("ib_low", "null")
            .order("sim_date", desc=False)
            .order("id", desc=False)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        if exclude_ids:
            q = q.not_.in_("id", list(exclude_ids))
        rows = q.limit(batch_size).execute().data or []
    except Exception as e:
        print(f"run_backtest_tiered_backfill_batch fetch error: {e}")
        stats["errors"] = 1
        return stats

    stats["fetched"] = len(rows)

    import pytz as _pytz

    eastern = _pytz.timezone("US/Eastern")

    for row in rows:
        row_id       = row["id"]
        ticker       = row.get("ticker", "")
        sim_date_raw = row.get("sim_date")
        direction    = row.get("actual_outcome", "")
        ib_high      = row.get("ib_high")
        ib_low       = row.get("ib_low")
        close_price  = row.get("close_price")
        existing_eod = row.get("eod_pnl_r")

        try:
            if isinstance(sim_date_raw, str):
                sim_date = _date.fromisoformat(sim_date_raw[:10])
            elif isinstance(sim_date_raw, _date):
                sim_date = sim_date_raw
            else:
                raise ValueError(f"unexpected type {type(sim_date_raw)}")
        except Exception:
            stats["errors"] += 1
            stats["skipped_ids"].append(row_id)
            continue

        # ── Fetch Alpaca bars with retry for transient exceptions ────────────
        # Distinguish two cases:
        #   • Exception raised            → transient (network/auth); retry up to
        #     BACKFILL_BAR_FETCH_MAX_RETRIES times, then skip WITHOUT sentinel.
        #   • No exception, empty result  → conclusive "no data" for this ticker/date;
        #     stamp sentinel so the row exits the IS NULL pending count permanently.
        full_df = None
        _fetch_exception = False
        for _attempt in range(BACKFILL_BAR_FETCH_MAX_RETRIES + 1):
            try:
                _df = fetch_bars(ALPACA_API_KEY, ALPACA_SECRET_KEY, ticker, sim_date)
                _fetch_exception = False
                full_df = _df
                break
            except Exception:
                _fetch_exception = True
                if _attempt < BACKFILL_BAR_FETCH_MAX_RETRIES:
                    time.sleep(0.5)

        if _fetch_exception:
            # All retries raised exceptions — transient failure; skip without stamping.
            stats["errors"] += 1
            continue

        if full_df is None or len(full_df) == 0:
            # API returned successfully but no bars exist — this ticker/date has no
            # data (delisted, pre-listing, holiday gap, etc.).  Stamp the sentinel so
            # the row exits the IS NULL pending count permanently.
            eod_only = compute_trade_sim_tiered(
                aft_df=None, ib_high=ib_high, ib_low=ib_low,
                direction=direction, close_px=close_price,
            )
            if not dry_run:
                patch_no_bar: dict = {"tiered_pnl_r": TIERED_PNL_SENTINEL}
                if existing_eod is None and eod_only.get("eod_pnl_r") is not None:
                    patch_no_bar["eod_pnl_r"] = eod_only["eod_pnl_r"]
                try:
                    supabase.table("backtest_sim_runs").update(
                        patch_no_bar
                    ).eq("id", row_id).execute()
                except Exception:
                    pass
            stats["skipped_no_bars"] += 1
            stats["skipped_ids"].append(row_id)
            continue

        try:
            cutoff_naive = _datetime(sim_date.year, sim_date.month, sim_date.day, 10, 30, 59)
            tz = full_df.index.tz
            if tz is not None:
                cutoff = eastern.localize(cutoff_naive)
            else:
                cutoff = cutoff_naive
            aft_df = full_df[full_df.index > cutoff]
            aft_df = aft_df if not aft_df.empty else None
        except Exception:
            aft_df = None

        result = compute_trade_sim_tiered(
            aft_df=aft_df, ib_high=ib_high, ib_low=ib_low,
            direction=direction, close_px=close_price,
        )

        tiered_pnl_r = result.get("tiered_pnl_r")
        eod_pnl_r    = result.get("eod_pnl_r")

        if tiered_pnl_r is None:
            stats["skipped_no_tiered"] += 1
            stats["skipped_ids"].append(row_id)
            if not dry_run and existing_eod is None and eod_pnl_r is not None:
                try:
                    supabase.table("backtest_sim_runs").update(
                        {"eod_pnl_r": eod_pnl_r}
                    ).eq("id", row_id).execute()
                except Exception:
                    pass
            continue

        patch = {"tiered_pnl_r": tiered_pnl_r}
        if existing_eod is None and eod_pnl_r is not None:
            patch["eod_pnl_r"] = eod_pnl_r

        if not dry_run:
            try:
                supabase.table("backtest_sim_runs").update(patch).eq("id", row_id).execute()
                stats["updated"] += 1
            except Exception as e:
                print(f"run_backtest_tiered_backfill_batch update error id={row_id}: {e}")
                stats["errors"] += 1
                stats["skipped_ids"].append(row_id)
        else:
            stats["updated"] += 1

    stats["remaining"] = max(0, count_backtest_tiered_pending(user_id=user_id))
    return stats


# ── Paper Trading ─────────────────────────────────────────────────────────────

_PAPER_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
  id             SERIAL PRIMARY KEY,
  user_id        TEXT,
  trade_date     DATE,
  ticker         TEXT,
  tcs            FLOAT,
  predicted      TEXT,
  ib_low         FLOAT,
  ib_high        FLOAT,
  open_price     FLOAT,
  actual_outcome TEXT,
  follow_thru_pct FLOAT,
  win_loss       TEXT,
  false_break_up  BOOLEAN DEFAULT FALSE,
  false_break_down BOOLEAN DEFAULT FALSE,
  min_tcs_filter  INT DEFAULT 50,
  regime_tag      TEXT,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);
"""

_PAPER_TRADES_REGIME_MIGRATION = (
    "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS regime_tag TEXT;"
)

_RVOL_COLUMNS_MIGRATION = (
    "-- Run in Supabase SQL Editor to add RVOL persistence columns:\n"
    "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS rvol REAL;\n"
    "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS gap_pct REAL;\n"
    "ALTER TABLE watchlist_predictions ADD COLUMN IF NOT EXISTS rvol REAL;\n"
    "ALTER TABLE watchlist_predictions ADD COLUMN IF NOT EXISTS gap_pct REAL;\n"
)

_MAE_MFE_COLUMNS_MIGRATION = (
    "-- Run in Supabase SQL Editor to add MAE/MFE execution depth columns:\n"
    "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mae REAL;\n"
    "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mfe REAL;\n"
    "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_time TEXT;\n"
    "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS exit_trigger TEXT;\n"
    "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_ib_distance REAL;\n"
)


def ensure_rvol_columns() -> bool:
    if not supabase:
        return False
    try:
        supabase.table("paper_trades").select("rvol").limit(1).execute()
        return True
    except Exception as e:
        err = str(e).lower()
        if any(k in err for k in ("column", "not exist", "not found", "pgrst")):
            print("rvol/gap_pct columns missing.\nRun in Supabase SQL Editor:\n\n" + _RVOL_COLUMNS_MIGRATION)
            return False
        return False


def ensure_paper_trades_regime_column() -> bool:
    """Check if regime_tag column exists in paper_trades. Returns True if present.

    If missing, prints the migration SQL to run in Supabase SQL Editor.
    """
    if not supabase:
        return False
    try:
        supabase.table("paper_trades").select("regime_tag").limit(1).execute()
        return True
    except Exception as e:
        err = str(e).lower()
        if any(k in err for k in ("column", "not exist", "not found", "pgrst")):
            print(
                "regime_tag column missing from paper_trades.\n"
                "Run in Supabase SQL Editor:\n\n"
                + _PAPER_TRADES_REGIME_MIGRATION
            )
            return False
        print(f"ensure_paper_trades_regime_column error: {e}")
        return False


def ensure_mae_mfe_columns() -> bool:
    if not supabase:
        return False
    try:
        supabase.table("paper_trades").select("mae").limit(1).execute()
        return True
    except Exception as e:
        err = str(e).lower()
        if any(k in err for k in ("column", "not exist", "not found", "pgrst")):
            print("mae/mfe columns missing.\nRun in Supabase SQL Editor:\n\n" + _MAE_MFE_COLUMNS_MIGRATION)
            return False
        return False


def ensure_paper_trades_table() -> bool:
    """Check if paper_trades table exists. Returns True if ready, False if missing."""
    if not supabase:
        return False
    try:
        supabase.table("paper_trades").select("id").limit(1).execute()
        return True
    except Exception as e:
        err_str = str(e).lower()
        # Supabase returns 404/relation-not-found when the table is missing
        if "404" in err_str or "relation" in err_str or "does not exist" in err_str or "not found" in err_str:
            print("paper_trades table not found. Create it in Supabase SQL editor — see Paper Trade tab for the SQL.")
            return False
        # Any other error (auth, network) — log and treat as unavailable
        print(f"paper_trades table check error: {e}")
        return False


# Formula version stamped on every sim row by _sim_patch().
# Bump this string whenever compute_trade_sim() logic changes so that
# --skip-existing automatically re-processes stale (old-version) rows.
# SIM_VERSION = "v3"  # v3 = adaptive target_r per row (EOD close, awaiting clean MFE re-backfill)
# SIM_VERSION = "v4"  # v4 = intraday bracket sim: MAE>=1R→stop, MFE>=target_r→exit at fixed target
#                     # Enable once backfill_mfe_mae.py --force-recompute completes (2026-04-18 fix)
# SIM_VERSION = "v5"  # v5 = trailing stop sim: T1 hit → trail 1R below MFE peak (mirrors paper bot).
#                     # MFE>=target_r → captured = MFE-1.0R (trail fires 1R from peak).
#                     # MFE<target_r AND MAE>=1R → stopped at -1.0R.
#                     # Neither → EOD close (position held). Clean MFE data loaded 2026-04-18.
SIM_VERSION = "v6"  # v6 = S/R-aware trail tightening: same as v5 but when nearest_resistance
#                   # (bullish) or nearest_support (bearish) is within 0.3R of entry, the trail
#                   # tightens to 0.5R instead of 1.0R — locks in more gain near S/R walls.
#                   # Falls back to 1.0R trail when context level data is absent.
#                   # sim_outcome = "tight_trail_exit" for tightened cases.
#
#                   # Validated 2026-04-18 on 991 trades (full 2930-row paginated context):
#                   #   v5 expectancy: +0.0514R/trade (432W/552L, +50.97R total)
#                   #   v6 expectancy: +0.0661R/trade (433W/552L, +65.47R total)
#                   #   delta: +0.0146R/trade (+14.50R) — v6 > v5 ✅ kept as production
#                   #   tight_trail_exit fires: 29/991 = 2.93% of trades (very selective)

# Formula version stamped on every row that writes eod_pnl_r.
# Bump this string whenever compute_trade_sim_tiered() EOD logic changes so
# that --skip-existing can detect stale eod_pnl_r values and re-process them.
TIERED_SIM_VERSION = "v1"

# ── Adaptive exit target (mirrors paper_trader_bot._adaptive_target_r) ────────
# Reads adaptive_exits.json — 3-layer calibration from 24,837 qualified backtest
# rows + 314 live paper_trades. Updated 2026-04-18.
_ADAPTIVE_EXITS_CFG: dict = {}
try:
    _aef_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adaptive_exits.json")
    with open(_aef_path) as _f:
        _ADAPTIVE_EXITS_CFG = json.load(_f)
except Exception:
    pass


_RVOL_SIZE_TIERS_DEFAULT_B = [
    {"rvol_min": 3.5, "multiplier": 1.5},
    {"rvol_min": 2.5, "multiplier": 1.25},
]

def rvol_size_mult(rvol: float | None) -> float:
    """Return position-size multiplier based on RVOL bonus tiers.

    Tiers are loaded fresh from adaptive_exits.json on each call so that
    changes saved via the dashboard take effect without a process restart.
    Tiers are evaluated highest rvol_min first; first match wins.
    Returns 1.0 (baseline) when rvol is None or below all thresholds.
    """
    if rvol is None or rvol != rvol:  # None or NaN
        return 1.0
    try:
        _path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adaptive_exits.json")
        with open(_path) as _f:
            cfg = json.load(_f)
        tiers = cfg.get("rvol_size_tiers", _RVOL_SIZE_TIERS_DEFAULT_B)
    except Exception:
        tiers = _RVOL_SIZE_TIERS_DEFAULT_B
    for tier in sorted(tiers, key=lambda t: t["rvol_min"], reverse=True):
        if rvol >= tier["rvol_min"]:
            return float(tier["multiplier"])
    return 1.0


def adaptive_target_r(tcs: float, scan_type: str = "", structure: str = "") -> float:
    """Return MFE-calibrated exit target in R-units.

    3-layer lookup (most specific wins):
      1. Structure override — Bearish Break → 0.5R, Dbl Dist → 1.5R
      2. Scan type + TCS band — morning/intraday × TCS tier
      3. TCS-only fallback

    Falls back to 1.0R if config is missing.
    """
    cfg = _ADAPTIVE_EXITS_CFG
    if not cfg:
        return 1.0

    # Layer 1: structure override
    for struct_key, target in cfg.get("structure_overrides", {}).items():
        if struct_key.lower() in structure.lower():
            return float(target)

    # Layer 2: scan_type + TCS band
    for band in cfg.get("scan_and_tcs", []):
        if band["scan_type"] == scan_type and band["tcs_min"] <= tcs < band["tcs_max"]:
            return float(band["target_r"])

    # Layer 3: TCS-only fallback
    for band in cfg.get("tcs_fallback", []):
        if band["tcs_min"] <= tcs < band["tcs_max"]:
            return float(band["target_r"])

    return float(cfg.get("global_fallback_target_r", 1.0))


def apply_rvol_sizing_to_sim(sim: dict, rvol_raw) -> dict:
    """Apply RVOL bonus position-size multiplier to a compute_trade_sim() result.

    Call this after compute_trade_sim() at every site that writes pnl_r_sim to
    the database so that all sim producers (batch_backtest, live-insert, backfill)
    consistently model the dollar-scaling effect of high RVOL.

    A 1.25× RVOL size bonus on a +1.5R win → +1.875R effective account R;
    a 1.25× boost on a -1.0R loss → -1.25R effective loss.
    Both pnl_r_sim and pnl_pct_sim are scaled so the DB values reflect the
    actual RVOL-boosted position size in all dimensions.

    Returns the original sim dict unchanged when:
    - rvol_raw is None / falsy (no RVOL data available for this row)
    - RVOL is below all configured thresholds (multiplier = 1.0)
    - pnl_r_sim is None (no_trade / missing_data rows)
    """
    if not rvol_raw:
        return sim
    try:
        _mult = rvol_size_mult(float(rvol_raw))
    except (TypeError, ValueError):
        return sim
    result = dict(sim)
    result["rvol_mult"] = _mult
    if _mult == 1.0:
        return result
    _pnl_r = sim.get("pnl_r_sim")
    if _pnl_r is None:
        return result
    result["pnl_r_sim"] = round(float(_pnl_r) * _mult, 4)
    _pnl_pct = sim.get("pnl_pct_sim")
    if _pnl_pct is not None:
        result["pnl_pct_sim"] = round(float(_pnl_pct) * _mult, 4)
    return result


def compute_trade_sim(r: dict, target_r: float = 2.0,
                      nearest_resistance: float | None = None,
                      nearest_support:    float | None = None) -> dict:
    """Simulate an IB-breakout paper trade from an EdgeIQ structure setup.

    Trade direction signal priority:
      1. Use `predicted` if it is "Bullish Break" or "Bearish Break"
         (directional paper trade predictions)
      2. Otherwise fall back to `actual_outcome` — simulates taking every real
         IB breakout that occurred (Range-Bound / Both Sides = no_trade)

    Rules:
      Bullish Break → long entry at IB high, stop at IB low
      Bearish Break → short entry at IB low, stop at IB high
      target = entry ± target_r × IB range

    Uses follow_thru_pct (% move from IB break level to EOD close) for P&L.
    false_break_up / false_break_down used to detect stop-outs before recovery.

    Returns dict with sim fields. pnl_r_sim is capped at −1.0 (stop) on the
    downside; upside is uncapped (reflects actual EOD close).
    """
    predicted   = (r.get("predicted") or "").strip()
    actual      = (r.get("actual_outcome") or "").strip()
    ib_low      = r.get("ib_low")
    ib_high     = r.get("ib_high")
    ft_pct      = r.get("follow_thru_pct")
    close_price = r.get("close_price")
    false_up    = bool(r.get("false_break_up", False))
    false_dn    = bool(r.get("false_break_down", False))

    # v4/v5: intraday sim using post-IB MFE/MAE (re-backfilled with hm > entry_hm fix).
    # v4 = bracket sim: MAE>=1R→stop, MFE>=target_r→exit at fixed target_r.
    # v5 = trailing stop sim (mirrors paper bot):
    #   MFE >= target_r (T1 hit) → trail 1R below MFE peak → captured = MFE - 1.0R
    #   MFE <  target_r AND MAE >= 1.0R → stopped at -1.0R
    #   Neither → EOD close (position held)
    # Priority: T1/MFE check first (price ran before it stopped), MAE second.
    _mfe_r = r.get("mfe")
    _mae_r = r.get("mae")
    _has_clean_mfe = (
        _mfe_r is not None and _mae_r is not None
        and float(_mfe_r) > -9000 and float(_mae_r) > -9000   # exclude sentinel -9999; negative MAE is valid (price never dipped below entry)
    )
    _use_bracket_sim  = SIM_VERSION == "v4" and _has_clean_mfe
    _use_trail_sim    = SIM_VERSION == "v5" and _has_clean_mfe
    _use_trail_sim_v6 = SIM_VERSION == "v6" and _has_clean_mfe

    # Allow nearest_resistance / nearest_support to be passed via row dict (for backfill)
    # or as explicit kwargs (for direct calls).
    if nearest_resistance is None:
        nearest_resistance = r.get("nearest_resistance")
    if nearest_support is None:
        nearest_support = r.get("nearest_support")
    if nearest_resistance is not None:
        nearest_resistance = float(nearest_resistance)
    if nearest_support is not None:
        nearest_support = float(nearest_support)
    if _has_clean_mfe and (_use_bracket_sim or _use_trail_sim or _use_trail_sim_v6):
        _mfe_r = float(_mfe_r)
        _mae_r = float(_mae_r)

    NO_TRADE = {"sim_outcome": "no_trade",  "pnl_r_sim": None, "pnl_pct_sim": None,
                "entry_price_sim": None, "stop_price_sim": None,
                "stop_dist_pct": None, "target_price_sim": None}

    # ── Actual fill override: if we have real Alpaca exit data, use it directly ─
    # This makes the "sim" perfectly match the actual bot for reconciled paper trades.
    _actual_r = r.get("pnl_r_actual")
    _actual_exit = r.get("alpaca_exit_fill_price")
    if _actual_r is not None and _actual_exit is not None:
        _actual_r_f = float(_actual_r)
        _wl = r.get("win_loss", "")
        if _wl == "Win":
            _actual_outcome = "t1_hit" if _actual_r_f >= 1.8 else "trailing_exit"
        elif _actual_r_f <= -0.9:
            _actual_outcome = "stopped_out"
        else:
            _actual_outcome = "partial_loss"
        return {
            "sim_outcome":      _actual_outcome,
            "pnl_r_sim":        round(_actual_r_f, 3),
            "pnl_pct_sim":      None,
            "entry_price_sim":  r.get("alpaca_fill_price"),
            "stop_price_sim":   r.get("ib_low"),
            "stop_dist_pct":    None,
            "target_price_sim": None,
        }

    # Determine trade direction — use actual_outcome (confirmed market move)
    # actual_outcome = "Bullish Break" / "Bearish Break" → market broke that direction
    # predicted = structure type ("Neutral", "Ntrl Extreme") — NOT directional
    if actual in ("Bullish Break", "Bearish Break"):
        direction = actual
    elif predicted in ("Bullish Break", "Bearish Break"):
        direction = predicted
    else:
        return NO_TRADE
    if ib_low is None or ib_high is None:
        return {**NO_TRADE, "sim_outcome": "missing_data"}

    ib_low, ib_high = float(ib_low), float(ib_high)
    ib_range = ib_high - ib_low
    if ib_range <= 0:
        return {**NO_TRADE, "sim_outcome": "invalid_ib"}

    if direction == "Bullish Break":
        entry         = ib_high
        stop          = ib_low
        target        = entry + target_r * ib_range
        stop_dist_pct = ib_range / entry * 100

        # ── Smart stop-widening (Bullish) — mirrors paper bot logic ──────────
        # 5yr analysis: TCS>=70 + IB<=3% + RVOL>=3 → 95% false shakeout rate on stopped trades.
        # TCS>=65 + intraday + RVOL>=2.5 → elevated false shakeout risk.
        # Widen the effective stop; share sizing adjusts automatically in live bot.
        _tcs_s     = float(r.get("tcs") or 0)
        _rvol_s    = float(r.get("rvol") or 0)
        _ib_pct_s  = float(r.get("ib_range_pct") or 999)
        _scan_s    = (r.get("scan_type") or "").strip()
        _smart_buf = 0.0
        if _tcs_s >= 70 and _ib_pct_s <= 3.0 and _rvol_s >= 3.0:
            _smart_buf = 0.5
        elif _tcs_s >= 65 and _scan_s == "intraday" and _rvol_s >= 2.5:
            _smart_buf = 0.25
        _eff_stop_r   = 1.0 + _smart_buf   # effective stop in R units from entry
        _eff_stop_pct = _eff_stop_r * stop_dist_pct

        # ── v4: Intraday bracket sim (clean post-IB MFE/MAE) ─────────────────
        if _use_bracket_sim:
            if _mae_r >= _eff_stop_r:
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(-_eff_stop_pct, 2), "pnl_r_sim": -_eff_stop_r,
                    "sim_outcome": "stopped_out", "sim_version": SIM_VERSION,
                }
            if _mfe_r >= target_r:
                pnl_pct_hit = target_r * stop_dist_pct
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(pnl_pct_hit, 2), "pnl_r_sim": round(target_r, 3),
                    "sim_outcome": "hit_target", "sim_version": SIM_VERSION,
                }
            # Neither fired — position open at EOD, fall through

        # ── v5: Trailing stop sim — mirrors paper bot T1 → trail 1R below peak ─
        if _use_trail_sim:
            # MAE check FIRST: if stop was hit (MAE >= eff_stop_r), trade is closed.
            # Smart stop widens the threshold — wider stop = fewer false shakeout stops.
            if _mae_r >= _eff_stop_r:
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(-_eff_stop_pct, 2), "pnl_r_sim": -_eff_stop_r,
                    "sim_outcome": "stopped_out", "sim_version": SIM_VERSION,
                }
            if _mfe_r >= target_r:
                # T1 hit: trailing stop converts bracket. Trail = 1R below MFE peak.
                captured_r = max(0.0, round(_mfe_r - 1.0, 3))
                captured_pct = captured_r * stop_dist_pct
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(captured_pct, 2), "pnl_r_sim": captured_r,
                    "sim_outcome": "trailing_exit", "sim_version": SIM_VERSION,
                }
            # T1 not hit, stop not hit — held to EOD, fall through

        # ── v6: S/R-aware trail tightening (Bullish) ─────────────────────────
        if _use_trail_sim_v6:
            # MFE check FIRST: if T1 was hit the trailing stop locks in profit.
            # Even if MAE later exceeds eff_stop_r, the position was already closed at
            # the trail level — so MAE after T1 is irrelevant (the bot already exited).
            # Smart stop widens the effective stop for qualifying high-TCS/tight-IB/high-RVOL trades.
            if _mfe_r >= target_r:
                # Check if nearest_resistance is within 0.3R ABOVE entry (bounded: 0 ≤ dist ≤ 0.3R).
                # Level must be above entry (dist >= 0) to avoid false tightening on wrong-side data.
                _trail_r = 1.0
                _outcome = "trailing_exit"
                if (nearest_resistance is not None
                        and 0.0 <= (nearest_resistance - entry) <= 0.3 * ib_range):
                    _trail_r = 0.5
                    _outcome = "tight_trail_exit"
                captured_r   = max(0.0, round(_mfe_r - _trail_r, 3))
                captured_pct = captured_r * stop_dist_pct
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(captured_pct, 2), "pnl_r_sim": captured_r,
                    "sim_outcome": _outcome, "sim_version": SIM_VERSION,
                }
            # T1 not hit — now check if stop was hit (MAE >= eff_stop_r).
            if _mae_r >= _eff_stop_r:
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(-_eff_stop_pct, 2), "pnl_r_sim": -_eff_stop_r,
                    "sim_outcome": "stopped_out", "sim_version": SIM_VERSION,
                }
            # Neither T1 nor stop hit — held to EOD, fall through

        # ── EOD close fallback ────────────────────────────────────────────────
        if close_price is not None:
            close_price = float(close_price)
            _eff_stop_price = ib_low - _smart_buf * ib_range
            if close_price <= _eff_stop_price:
                # EOD close below effective stop → stopped out
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(-_eff_stop_pct, 2), "pnl_r_sim": -_eff_stop_r,
                    "sim_outcome": "stopped_out", "sim_version": SIM_VERSION,
                }
            pnl_pct = (close_price - ib_high) / ib_high * 100
        elif ft_pct is not None:
            # Fallback to follow_thru_pct when no close_price.
            if false_up:
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(-_eff_stop_pct, 2), "pnl_r_sim": -_eff_stop_r,
                    "sim_outcome": "stopped_out", "sim_version": SIM_VERSION,
                }
            pnl_pct = float(ft_pct)
        else:
            return {**NO_TRADE, "sim_outcome": "missing_data"}

    else:  # Bearish Break
        entry         = ib_low
        stop          = ib_high
        target        = entry - target_r * ib_range
        stop_dist_pct = ib_range / entry * 100

        # ── v4: Intraday bracket sim ──────────────────────────────────────────
        if _use_bracket_sim:
            if _mae_r >= 1.0:
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(-stop_dist_pct, 2), "pnl_r_sim": -1.0,
                    "sim_outcome": "stopped_out", "sim_version": SIM_VERSION,
                }
            if _mfe_r >= target_r:
                pnl_pct_hit = target_r * stop_dist_pct
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(pnl_pct_hit, 2), "pnl_r_sim": round(target_r, 3),
                    "sim_outcome": "hit_target", "sim_version": SIM_VERSION,
                }
            # Neither fired — position open at EOD, fall through

        # ── v5: Trailing stop sim — mirrors paper bot T1 → trail 1R below peak ─
        if _use_trail_sim:
            # MAE check FIRST: stop hit means trade is closed — post-stopout MFE is irrelevant.
            if _mae_r >= 1.0:
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(-stop_dist_pct, 2), "pnl_r_sim": -1.0,
                    "sim_outcome": "stopped_out", "sim_version": SIM_VERSION,
                }
            if _mfe_r >= target_r:
                captured_r = max(0.0, round(_mfe_r - 1.0, 3))
                captured_pct = captured_r * stop_dist_pct
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(captured_pct, 2), "pnl_r_sim": captured_r,
                    "sim_outcome": "trailing_exit", "sim_version": SIM_VERSION,
                }
            # T1 not hit, stop not hit — held to EOD, fall through

        # ── v6: S/R-aware trail tightening (Bearish) ─────────────────────────
        if _use_trail_sim_v6:
            # MFE check FIRST: if T1 was hit the trailing stop locks in profit.
            # MAE after T1 is irrelevant — the position was already closed at the trail level.
            if _mfe_r >= target_r:
                # Check if nearest_support is within 0.3R BELOW entry (bounded: 0 ≤ dist ≤ 0.3R).
                # Level must be below entry (dist >= 0) to avoid false tightening on wrong-side data.
                _trail_r = 1.0
                _outcome = "trailing_exit"
                if (nearest_support is not None
                        and 0.0 <= (entry - nearest_support) <= 0.3 * ib_range):
                    _trail_r = 0.5
                    _outcome = "tight_trail_exit"
                captured_r   = max(0.0, round(_mfe_r - _trail_r, 3))
                captured_pct = captured_r * stop_dist_pct
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(captured_pct, 2), "pnl_r_sim": captured_r,
                    "sim_outcome": _outcome, "sim_version": SIM_VERSION,
                }
            # T1 not hit — now check if stop was hit (MAE >= 1R for bearish).
            if _mae_r >= 1.0:
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(-stop_dist_pct, 2), "pnl_r_sim": -1.0,
                    "sim_outcome": "stopped_out", "sim_version": SIM_VERSION,
                }
            # Neither T1 nor stop hit — held to EOD, fall through

        # ── EOD close fallback ────────────────────────────────────────────────
        if close_price is not None:
            close_price = float(close_price)
            if close_price >= ib_high:
                # EOD close above stop → full stop out
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(-stop_dist_pct, 2), "pnl_r_sim": -1.0,
                    "sim_outcome": "stopped_out", "sim_version": SIM_VERSION,
                }
            pnl_pct = (ib_low - close_price) / ib_low * 100   # positive when price fell
        elif ft_pct is not None:
            # false_break_down = price broke IB low then recovered back above IB high → stop out.
            if false_dn:
                return {
                    "entry_price_sim": round(entry, 4), "stop_price_sim": round(stop, 4),
                    "stop_dist_pct": round(stop_dist_pct, 2), "target_price_sim": round(target, 4),
                    "pnl_pct_sim": round(-stop_dist_pct, 2), "pnl_r_sim": -1.0,
                    "sim_outcome": "stopped_out", "sim_version": SIM_VERSION,
                }
            pnl_pct = -float(ft_pct)   # negative ft_pct = price fell = profit for short
        else:
            return {**NO_TRADE, "sim_outcome": "missing_data"}

    pnl_r = pnl_pct / stop_dist_pct if stop_dist_pct else 0.0

    # Cap loss at −1R (full stop); upside uncapped
    if pnl_r < -1.0:
        pnl_r   = -1.0
        pnl_pct = -stop_dist_pct

    if pnl_r >= target_r:
        sim_outcome = "hit_target"
    elif pnl_r >= 1.0:
        sim_outcome = "partial_win"
    elif pnl_r >= 0.0:
        sim_outcome = "breakeven"
    elif pnl_r >= -1.0:
        sim_outcome = "partial_loss"
    else:
        sim_outcome = "stopped_out"

    return {
        "entry_price_sim":  round(entry, 4),
        "stop_price_sim":   round(stop, 4),
        "stop_dist_pct":    round(stop_dist_pct, 2),
        "target_price_sim": round(target, 4),
        "pnl_pct_sim":      round(pnl_pct, 2),
        "pnl_r_sim":        round(pnl_r, 3),
        "sim_outcome":      sim_outcome,
        "sim_version":      SIM_VERSION,
    }


def compute_trade_sim_tiered(
    aft_df,
    ib_high:   float,
    ib_low:    float,
    direction: str,
    close_px=None,
) -> dict:
    """Bar-by-bar tiered exit simulation for an IB breakout trade.

    Implements the statistical exit ladder derived from the historical R distribution
    (analysed over 1,000 trades):
      - 66.9 % of trades peak below 1R  →  1R is the most important harvest level
      - 41 % of 1R+ trades continue to 2R+  →  keep half alive after 1R
      - 57 % of 2R+ trades continue to 3R+  →  runner has statistical backing

    Exit ladder:
      1. 50 % of position exits at 1R (1× IB range from entry)
      2. Stop moves to breakeven after 1R exit (locks 0.5R floor)
      3. 25 % exits at 2R
      4. Remaining 25 % exits at EOD close price (the runner)

    Also computes eod_pnl_r: full-position P&L held to EOD close (no tiered exits).

    Returns dict:  eod_pnl_r, tiered_pnl_r, hit_1r (bool), hit_2r (bool).
    Returns None values when bars are unavailable or direction is not actionable.
    """
    NO_RESULT = {"eod_pnl_r": None, "tiered_pnl_r": None, "hit_1r": False, "hit_2r": False}

    if direction not in ("Bullish Break", "Bearish Break"):
        return NO_RESULT
    if close_px is None:
        return NO_RESULT

    try:
        ib_high  = float(ib_high)
        ib_low   = float(ib_low)
        close_px = float(close_px)
        ib_range = ib_high - ib_low
        if ib_range <= 0:
            return NO_RESULT
    except (TypeError, ValueError):
        return NO_RESULT

    # ── EOD Hold P&L (full position held to close — no stops, raw outcome) ─────
    # Intentionally uncapped: if price crashed through the stop level and never
    # recovered, EOD Hold reflects the full realized loss (can exceed -1R).
    # This is the "no risk management" baseline, not a tradeable strategy.
    if direction == "Bullish Break":
        entry        = ib_high
        stop_initial = ib_low
        target_1r    = entry + ib_range
        target_2r    = entry + 2.0 * ib_range
        eod_raw_r    = (close_px - entry) / ib_range
    else:  # Bearish Break
        entry        = ib_low
        stop_initial = ib_high
        target_1r    = entry - ib_range
        target_2r    = entry - 2.0 * ib_range
        eod_raw_r    = (entry - close_px) / ib_range  # positive when price falls

    eod_pnl_r = round(eod_raw_r, 4)  # uncapped — raw hold-to-close R

    # ── Tiered exit: bar-by-bar replay ───────────────────────────────────────
    if aft_df is None or len(aft_df) == 0:
        return {"eod_pnl_r": eod_pnl_r, "tiered_pnl_r": None, "hit_1r": False, "hit_2r": False}

    # Same-bar ordering convention: stop is evaluated before targets on every bar,
    # including the entry bar. This is the conservative "stop-priority" assumption:
    # if a single bar touches both stop and target, the stop is taken (worst-case).
    stop_level = stop_initial
    hit_1r     = False
    hit_2r     = False
    locked_r   = 0.0
    remaining  = 1.0   # fraction of position still open
    entered    = False  # True once price first crosses the entry level

    for _, bar in aft_df.iterrows():
        try:
            bar_high = float(bar.get("high") or 0)
            bar_low  = float(bar.get("low")  or 0)
        except (TypeError, ValueError):
            continue  # skip malformed bar; don't abort the whole sim

        # Gate: skip bars where price hasn't yet crossed the breakout entry level.
        # This prevents mismatch when aft_df starts before the actual breakout.
        if not entered:
            if direction == "Bullish Break" and bar_high >= entry:
                entered = True
            elif direction == "Bearish Break" and bar_low <= entry:
                entered = True
            else:
                continue  # not yet in a position — skip stop/target logic

        if direction == "Bullish Break":
            if not hit_1r:
                if bar_low <= stop_level:         # stop checked first (stop-priority)
                    locked_r += remaining * (-1.0)
                    remaining = 0.0
                    break
                if bar_high >= target_1r:
                    hit_1r     = True
                    locked_r  += 0.50 * 1.0
                    remaining -= 0.50
                    stop_level = entry            # stop → breakeven
            elif not hit_2r:
                if bar_low <= entry:              # breakeven stop hit
                    locked_r += remaining * 0.0
                    remaining = 0.0
                    break
                if bar_high >= target_2r:
                    hit_2r     = True
                    locked_r  += 0.25 * 2.0
                    remaining -= 0.25
            # else: runner still open — keep scanning

        else:  # Bearish Break
            if not hit_1r:
                if bar_high >= stop_level:        # stop checked first (stop-priority)
                    locked_r += remaining * (-1.0)
                    remaining = 0.0
                    break
                if bar_low <= target_1r:
                    hit_1r     = True
                    locked_r  += 0.50 * 1.0
                    remaining -= 0.50
                    stop_level = entry
            elif not hit_2r:
                if bar_high >= entry:             # breakeven stop hit
                    locked_r += remaining * 0.0
                    remaining = 0.0
                    break
                if bar_low <= target_2r:
                    hit_2r     = True
                    locked_r  += 0.25 * 2.0
                    remaining -= 0.25

    # If price never crossed the entry level in aft_df, no tiered position was opened.
    # eod_pnl_r (hold-to-close) is still valid — tiered_pnl_r is not.
    if not entered:
        return {"eod_pnl_r": eod_pnl_r, "tiered_pnl_r": None, "hit_1r": False, "hit_2r": False}

    # Exit remaining open position at EOD close (runner portion for tiered sim)
    if remaining > 0:
        if direction == "Bullish Break":
            eod_partial_r = (close_px - entry) / ib_range
        else:
            eod_partial_r = (entry - close_px) / ib_range
        # Runner floor: stop-to-BE prevents runner losing more than 0R after 1R hit;
        # initial stop prevents full position losing more than -1R before 1R hit.
        eod_partial_r = max(eod_partial_r, 0.0 if hit_1r else -1.0)
        locked_r += remaining * eod_partial_r

    return {
        "eod_pnl_r":    eod_pnl_r,
        "tiered_pnl_r": round(locked_r, 4),
        "hit_1r":       hit_1r,
        "hit_2r":       hit_2r,
    }


def log_paper_trades(rows: list, user_id: str = "", min_tcs: int = 50) -> dict:
    """Save paper trade scan results to paper_trades table.
    Deduplicates by (user_id, trade_date, ticker, scan_type) — allows morning
    AND intraday entries for the same ticker on the same day.
    Returns dict with saved count and skipped count."""
    if not supabase or not rows:
        return {"saved": 0, "skipped": 0, "error": "No data"}
    try:
        existing = (
            supabase.table("paper_trades")
            .select(
                "ticker, trade_date, scan_type, "
                "sim_outcome, pnl_r_sim, entry_price_sim, stop_price_sim, target_price_sim"
            )
            .eq("user_id", user_id)
            .execute()
            .data or []
        )
        existing_keys = {
            (r["ticker"], str(r["trade_date"]), r.get("scan_type") or "morning")
            for r in existing
        }
        # Build a lookup so skipped rows can reuse stored sim values from the DB.
        existing_sim_map = {
            (r["ticker"], str(r["trade_date"]), r.get("scan_type") or "morning"): r
            for r in existing
        }
        records, skipped = [], 0
        skipped_sim_rows = []
        skipped_sim_failed = []   # tickers excluded due to missing/invalid sim data (already-logged)
        new_sim_failed = []       # tickers excluded due to missing/invalid sim data (newly inserted)
        _SIM_REASON_LABELS = {
            "missing_data": "missing IB data",
            "invalid_ib":   "invalid IB range",
        }
        for r in rows:
            scan_type = r.get("scan_type") or "morning"
            key = (r.get("ticker", ""), str(r.get("sim_date", r.get("trade_date", ""))), scan_type)
            if key in existing_keys:
                skipped += 1
                # Prefer the stored sim values from the DB record; fall back to
                # recomputing from the input row so the confirmation screen always
                # shows P&L data even when the DB record predates sim columns.
                _db_rec = existing_sim_map.get(key, {})
                if _db_rec.get("sim_outcome") not in ("no_trade", "missing_data", "invalid_ib", None):
                    skipped_sim_rows.append({
                        "ticker":           r.get("ticker", ""),
                        "sim_outcome":      _db_rec.get("sim_outcome"),
                        "pnl_r_sim":        _db_rec.get("pnl_r_sim"),
                        "entry_price_sim":  _db_rec.get("entry_price_sim"),
                        "stop_price_sim":   _db_rec.get("stop_price_sim"),
                        "target_price_sim": _db_rec.get("target_price_sim"),
                        "already_logged":   True,
                    })
                else:
                    # DB record has no sim data — recompute from current input row.
                    _skip_record = {
                        "ticker":          r.get("ticker", ""),
                        "trade_date":      str(r.get("sim_date", r.get("trade_date", ""))),
                        "predicted":       r.get("predicted", ""),
                        "ib_low":          r.get("ib_low"),
                        "ib_high":         r.get("ib_high"),
                        "open_price":      r.get("open_price"),
                        "alert_price":     r.get("close_price"),
                        "follow_thru_pct": r.get("aft_move_pct"),
                    }
                    _skip_sim = compute_trade_sim(_skip_record)
                    _skip_outcome = _skip_sim.get("sim_outcome")
                    if _skip_outcome not in ("no_trade", "missing_data", "invalid_ib", None):
                        skipped_sim_rows.append({
                            "ticker":           r.get("ticker", ""),
                            "sim_outcome":      _skip_outcome,
                            "pnl_r_sim":        _skip_sim.get("pnl_r_sim"),
                            "entry_price_sim":  _skip_sim.get("entry_price_sim"),
                            "stop_price_sim":   _skip_sim.get("stop_price_sim"),
                            "target_price_sim": _skip_sim.get("target_price_sim"),
                            "already_logged":   True,
                        })
                    elif _skip_outcome in ("missing_data", "invalid_ib"):
                        # Sim computation failed due to a data-quality problem — record why
                        # so the trader is warned on the confirmation screen.
                        _reason = _SIM_REASON_LABELS.get(_skip_outcome, "unknown reason")
                        skipped_sim_failed.append({
                            "ticker": r.get("ticker", ""),
                            "reason": _reason,
                        })
                    # no_trade means the setup legitimately didn't trigger — no warning needed.
                continue
            row_record = {
                "user_id":        user_id or "",
                "trade_date":     str(r.get("sim_date", r.get("trade_date", ""))),
                "ticker":         r.get("ticker", ""),
                "tcs":            r.get("tcs"),
                "predicted":      r.get("predicted", ""),
                "ib_low":         r.get("ib_low"),
                "ib_high":        r.get("ib_high"),
                "open_price":     r.get("open_price"),
                "alert_price":    r.get("close_price"),
                "alert_time":     datetime.utcnow().isoformat(),
                "structure_conf": r.get("confidence"),
                "actual_outcome": r.get("actual_outcome", ""),
                "follow_thru_pct": r.get("aft_move_pct"),
                "win_loss":       r.get("win_loss", ""),
                "false_break_up":  bool(r.get("false_break_up", False)),
                "false_break_down": bool(r.get("false_break_down", False)),
                "min_tcs_filter": min_tcs,
            }
            if r.get("rvol") is not None:
                row_record["rvol"] = round(float(r["rvol"]), 2)
            if r.get("gap_pct") is not None:
                row_record["gap_pct"] = round(float(r["gap_pct"]), 2)
            if r.get("mae") is not None:
                row_record["mae"] = round(float(r["mae"]), 2)
            if r.get("mfe") is not None:
                row_record["mfe"] = round(float(r["mfe"]), 2)
            if r.get("entry_time"):
                row_record["entry_time"] = r["entry_time"]
            if r.get("exit_trigger"):
                row_record["exit_trigger"] = r["exit_trigger"]
            if r.get("exit_obs"):
                row_record["exit_obs"] = r["exit_obs"]
            if r.get("entry_ib_distance") is not None:
                row_record["entry_ib_distance"] = round(float(r["entry_ib_distance"]), 2)
            if r.get("regime_tag"):
                row_record["regime_tag"] = r["regime_tag"]
            if r.get("scan_type"):
                row_record["scan_type"] = r["scan_type"]
            # ib_range_pct: computed from existing fields — persisted for filter auditing
            _ib_h  = float(r.get("ib_high")  or 0)
            _ib_l  = float(r.get("ib_low")   or 0)
            _o_px  = float(r.get("open_price") or 0)
            if _ib_h > _ib_l > 0 and _o_px > 0:
                row_record["ib_range_pct"] = round((_ib_h - _ib_l) / _o_px * 100, 4)
            # vwap_at_ib: populated by log_context_levels (which runs after log_paper_trades);
            # if it's already in r (e.g. batch backfill path), include it now
            if r.get("vwap_at_ib") is not None:
                row_record["vwap_at_ib"] = r["vwap_at_ib"]
            # tcs_floor: the per-structure TCS threshold active when the trade was taken.
            # Required for the Marginal vs Comfortable breakdown in the sweep tier cards.
            if r.get("tcs_floor") is not None:
                row_record["tcs_floor"] = int(r["tcs_floor"])
            elif r.get("_struct_tcs_floor") is not None:
                row_record["tcs_floor"] = int(r["_struct_tcs_floor"])
            # Auto-compute pnl_r_sim (simple sim P&L) on insert — no backfill needed for this field.
            # tiered_pnl_r for paper_trades is populated by run_tiered_pnl_backfill.py.
            _sim = apply_rvol_sizing_to_sim(
                compute_trade_sim(row_record), row_record.get("rvol")
            )
            _new_sim_outcome = _sim.get("sim_outcome")
            if _new_sim_outcome not in ("no_trade", "missing_data", "invalid_ib", None):
                row_record["sim_outcome"]      = _new_sim_outcome
                row_record["pnl_r_sim"]        = _sim.get("pnl_r_sim")
                row_record["pnl_pct_sim"]      = _sim.get("pnl_pct_sim")
                row_record["entry_price_sim"]  = _sim.get("entry_price_sim")
                row_record["stop_price_sim"]   = _sim.get("stop_price_sim")
                row_record["stop_dist_pct"]    = _sim.get("stop_dist_pct")
                row_record["target_price_sim"] = _sim.get("target_price_sim")
                row_record["sim_version"]      = SIM_VERSION
            elif _new_sim_outcome in ("missing_data", "invalid_ib"):
                # Sim could not be computed for this newly-inserted trade — warn the trader.
                _new_reason = _SIM_REASON_LABELS.get(_new_sim_outcome, "unknown reason")
                new_sim_failed.append({
                    "ticker": row_record["ticker"],
                    "reason": _new_reason,
                })
            records.append(row_record)
        # Only include records that have a valid sim_outcome so that the warning
        # "excluded from the table below" is accurate for failed-sim tickers.
        _failed_new_tickers = {entry["ticker"] for entry in new_sim_failed}
        sim_rows = [
            {
                "ticker":           rec["ticker"],
                "sim_outcome":      rec.get("sim_outcome"),
                "pnl_r_sim":        rec.get("pnl_r_sim"),
                "entry_price_sim":  rec.get("entry_price_sim"),
                "stop_price_sim":   rec.get("stop_price_sim"),
                "target_price_sim": rec.get("target_price_sim"),
            }
            for rec in records
            if rec["ticker"] not in _failed_new_tickers
        ]
        if records:
            try:
                supabase.table("paper_trades").insert(records).execute()
            except Exception as _ins_err:
                _err_s = str(_ins_err).lower()
                _optional_cols = ["rvol", "gap_pct", "mae", "mfe", "entry_time",
                                  "exit_trigger", "entry_ib_distance", "scan_type",
                                  "sim_outcome", "pnl_r_sim", "pnl_pct_sim",
                                  "entry_price_sim", "stop_price_sim",
                                  "stop_dist_pct", "target_price_sim",
                                  "sim_version",
                                  "ib_range_pct", "vwap_at_ib", "tcs_floor"]
                if any(col in _err_s for col in _optional_cols):
                    for rec in records:
                        for col in _optional_cols:
                            rec.pop(col, None)
                    supabase.table("paper_trades").insert(records).execute()
                    print("log_paper_trades: optional columns missing — saved without them")
                else:
                    raise
        return {
            "saved":            len(records),
            "skipped":          skipped,
            "sim_rows":         sim_rows + skipped_sim_rows,
            "sim_failed":       new_sim_failed + skipped_sim_failed,
        }
    except Exception as e:
        return {"saved": 0, "skipped": 0, "error": str(e)}


# ── R/Trade Projection Scenarios ────────────────────────────────────────────
# Centralized scenario constants used by compute_r_projection() and the Analytics UI.
# All three dec2026 targets are from the Phase 1 financial projection model
# ($7k start, $1,500 position, 2.14% risk/trade, 202 trades/yr, May 2026 start).
# Boundaries are threshold-based: a trader is "at" a scenario when they've reached its R target.
R_PROJECTION_SCENARIOS = [
    {"name": "Conservative", "r": 0.50, "dec2026": 25_800},
    {"name": "Expected",     "r": 0.79, "dec2026": 51_400},
    {"name": "Stretch",      "r": 1.20, "dec2026": 80_000},
]


def compute_r_projection(user_id: str = "", window: int | None = 30) -> dict:
    """Compute trailing R/trade from the last `window` settled paper trades
    and map it against the 3 financial projection scenarios (R_PROJECTION_SCENARIOS).

    Scenario assignment uses threshold-based boundaries:
        trailing_r < 0.50  → "Below Conservative"
        0.50 ≤ r < 0.79    → "Conservative"
        0.79 ≤ r < 1.20    → "Expected"
        r ≥ 1.20           → "Stretch"

    Primary R column: tiered_pnl_r (50/25/25 ladder).
    Fallback: pnl_r_sim (simple MFE sim) if tiered_pnl_r has no data.

    Returns a dict with:
        trailing_r      – float | None
        trade_count     – int
        scenario        – str ("Conservative" | "Expected" | "Stretch" | "Below Conservative")
        scenario_r      – float  (the canonical R threshold for that scenario)
        dec2026_est     – float | None  (piecewise-interpolated Dec 2026 account value)
        months_left     – float  (months remaining to Dec 31 2026 from today)
        r_source        – str    ("tiered_pnl_r" or "pnl_r_sim")
    """
    from datetime import date as _date_cls
    _today = _date_cls.today()
    _dec31 = _date_cls(2026, 12, 31)
    _days_left = max((_dec31 - _today).days, 0)
    _months_left = round(_days_left / 30.44, 1)

    _empty = {
        "trailing_r": None,
        "trade_count": 0,
        "scenario": None,
        "scenario_r": None,
        "dec2026_est": None,
        "months_left": _months_left,
        "r_source": "tiered_pnl_r",
    }

    if not supabase:
        return _empty

    def _fetch_r_vals(col: str) -> list:
        """Fetch R values from settled trades.

        "Settled" is inferred via two non-null proxies:
          - win_loss set: EOD outcome resolution ran
          - R column set: tiered sim (or fallback MFE sim) was computed
        The paper_trades table has no explicit settled flag; this is the
        canonical approach used elsewhere in the codebase.
        """
        try:
            q = (
                supabase.table("paper_trades")
                .select(f"trade_date, {col}")
                .eq("user_id", user_id)
                .not_.is_("win_loss", "null")
                .not_.is_(col, "null")
                .order("trade_date", desc=True)
            )
            if window is not None:
                q = q.limit(window)
            rows = q.execute().data or []
            return [float(r[col]) for r in rows if r.get(col) is not None]
        except Exception as e:
            print(f"compute_r_projection: fetch error ({col}): {e}")
            return []

    # Primary: tiered_pnl_r (50/25/25 ladder — preferred)
    vals = _fetch_r_vals("tiered_pnl_r")
    r_source = "tiered_pnl_r"

    # Fallback: pnl_r_sim (simple MFE-based sim) if no tiered data available
    if not vals:
        vals = _fetch_r_vals("pnl_r_sim")
        r_source = "pnl_r_sim"

    if not vals:
        return _empty

    trailing_r = round(sum(vals) / len(vals), 3)
    trade_count = len(vals)

    # ── Scenario mapping — threshold-based ───────────────────────────────
    # A trader is "at" a scenario when they've reached its R target.
    # Conservative band: [0.50, 0.79)  Expected band: [0.79, 1.20)
    if trailing_r < 0.50:
        scenario = "Below Conservative"
        scenario_r = 0.50
    elif trailing_r < 0.79:
        scenario = "Conservative"
        scenario_r = 0.50
    elif trailing_r < 1.20:
        scenario = "Expected"
        scenario_r = 0.79
    else:
        scenario = "Stretch"
        scenario_r = 1.20

    # ── Dec 2026 estimate via piecewise linear interpolation ─────────────
    def _interp_dec2026(r: float) -> float:
        lo  = R_PROJECTION_SCENARIOS[0]
        mid = R_PROJECTION_SCENARIOS[1]
        hi  = R_PROJECTION_SCENARIOS[2]
        if r <= lo["r"]:
            frac = max(r / lo["r"], 0.0)
            return round(frac * lo["dec2026"], 0)
        elif r <= mid["r"]:
            frac = (r - lo["r"]) / (mid["r"] - lo["r"])
            return round(lo["dec2026"] + frac * (mid["dec2026"] - lo["dec2026"]), 0)
        elif r <= hi["r"]:
            frac = (r - mid["r"]) / (hi["r"] - mid["r"])
            return round(mid["dec2026"] + frac * (hi["dec2026"] - mid["dec2026"]), 0)
        else:
            slope = (hi["dec2026"] - mid["dec2026"]) / (hi["r"] - mid["r"])
            return round(hi["dec2026"] + slope * (r - hi["r"]), 0)

    dec2026_est = _interp_dec2026(trailing_r)

    return {
        "trailing_r": trailing_r,
        "trade_count": trade_count,
        "scenario": scenario,
        "scenario_r": scenario_r,
        "dec2026_est": dec2026_est,
        "months_left": _months_left,
        "r_source": r_source,
    }


def compute_r_trend_history(user_id: str = "", r_source: str | None = None) -> "pd.DataFrame":
    """Return a DataFrame of settled paper trades (oldest first) with rolling R averages.

    Columns returned:
        trade_num  – sequential trade index (1-based)
        trade_date – date string (YYYY-MM-DD)
        r_val      – raw R value for that trade
        roll10     – 10-trade rolling average (NaN until 10 trades accumulated)
        roll30     – 30-trade rolling average (NaN until 30 trades accumulated)

    r_source controls which R column to use:
        None / "tiered_pnl_r" → tries tiered_pnl_r first, falls back to pnl_r_sim
        "pnl_r_sim"           → uses pnl_r_sim directly
    """
    import pandas as _pd_trend

    _empty_df = _pd_trend.DataFrame(
        columns=["trade_num", "trade_date", "r_val", "roll10", "roll30", "r_source"]
    )

    if not supabase:
        return _empty_df

    def _fetch_all(col: str) -> list:
        try:
            rows = (
                supabase.table("paper_trades")
                .select(f"trade_date, {col}")
                .eq("user_id", user_id)
                .not_.is_("win_loss", "null")
                .not_.is_(col, "null")
                .order("trade_date", desc=False)
                .execute()
                .data or []
            )
            return [(r["trade_date"], float(r[col])) for r in rows if r.get(col) is not None]
        except Exception as e:
            print(f"compute_r_trend_history: fetch error ({col}): {e}")
            return []

    if r_source == "pnl_r_sim":
        pairs = _fetch_all("pnl_r_sim")
        used_source = "pnl_r_sim"
    else:
        pairs = _fetch_all("tiered_pnl_r")
        used_source = "tiered_pnl_r"
        if not pairs:
            pairs = _fetch_all("pnl_r_sim")
            used_source = "pnl_r_sim"

    if not pairs:
        return _empty_df

    df = _pd_trend.DataFrame(pairs, columns=["trade_date", "r_val"])
    df["trade_num"] = range(1, len(df) + 1)
    df["roll10"] = df["r_val"].rolling(window=10, min_periods=10).mean()
    df["roll30"] = df["r_val"].rolling(window=30, min_periods=30).mean()
    df["r_source"] = used_source

    return df[["trade_num", "trade_date", "r_val", "roll10", "roll30", "r_source"]]


def load_paper_trades(user_id: str = "", days: int = 21) -> "pd.DataFrame":
    """Load paper trades from the last N days (default 21 = 3 weeks).

    Rows whose tiered_pnl_r equals TIERED_PNL_SENTINEL (-9999) are permanently
    unfillable (no Alpaca bars available).  The sentinel is replaced with NaN so
    that all downstream .notna()/.dropna() filters exclude them automatically
    without any extra handling in the UI layer.
    """
    if not supabase:
        return pd.DataFrame()
    try:
        from datetime import date, timedelta
        cutoff = str(date.today() - timedelta(days=days + 7))
        q = (
            supabase.table("paper_trades")
            .select("*")
            .eq("user_id", user_id)
            .gte("trade_date", cutoff)
            .order("trade_date", desc=True)
        )
        data = q.execute().data
        df = pd.DataFrame(data) if data else pd.DataFrame()
        if not df.empty and "tiered_pnl_r" in df.columns:
            import numpy as _np
            df["tiered_pnl_r"] = pd.to_numeric(df["tiered_pnl_r"], errors="coerce")
            df.loc[df["tiered_pnl_r"] == TIERED_PNL_SENTINEL, "tiered_pnl_r"] = _np.nan
        return df
    except Exception as e:
        print(f"Paper trades load error: {e}")
        return pd.DataFrame()


def get_intraday_closed_paper_trades(user_id: str = "") -> "pd.DataFrame":
    """Return today's paper_trades rows that have been closed intraday (win_loss IS NOT NULL).

    These are typically trailing-stop fills that _monitor_trailing_stops() patches
    in real-time throughout the day, giving traders a live running tally of realized R
    before the nightly EOD sweep runs.

    Columns returned (subset):
        ticker, win_loss, pnl_r_actual, alpaca_exit_fill_price,
        alpaca_fill_price, stop_price_sim, entry_time, exit_trigger,
        trade_date, predicted, tcs
    """
    if not supabase:
        return pd.DataFrame()
    try:
        from datetime import date
        today = str(date.today())
        q = (
            supabase.table("paper_trades")
            .select(
                "id,ticker,win_loss,pnl_r_actual,alpaca_exit_fill_price,"
                "alpaca_fill_price,stop_price_sim,entry_time,exit_trigger,"
                "trade_date,predicted,tcs"
            )
            .eq("trade_date", today)
            .not_.is_("win_loss", "null")
            .order("id", desc=False)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        data = q.execute().data
        return pd.DataFrame(data) if data else pd.DataFrame()
    except Exception as e:
        print(f"get_intraday_closed_paper_trades error: {e}")
        return pd.DataFrame()


def count_paper_tiered_pending(user_id: str = "") -> int:
    """Return the count of paper_trades rows that qualify for tiered P&L backfill.

    Qualifying rows have a Bullish/Bearish actual_outcome, NULL tiered_pnl_r,
    and all three price fields (close_price, ib_high, ib_low) populated.
    Sentinel-stamped rows (tiered_pnl_r = TIERED_PNL_SENTINEL) are non-NULL and
    therefore excluded automatically by the IS NULL filter.

    Pass *user_id* to scope the count to a single user (recommended for multi-tenant
    deployments). An empty string skips the user filter and counts all rows.
    """
    if not supabase:
        return 0
    try:
        q = (
            supabase.table("paper_trades")
            .select("id", count="exact")
            .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
            .is_("tiered_pnl_r", "null")
            .not_.is_("close_price", "null")
            .not_.is_("ib_high", "null")
            .not_.is_("ib_low", "null")
        )
        if user_id:
            q = q.eq("user_id", user_id)
        resp = q.execute()
        return resp.count or 0
    except Exception as e:
        print(f"count_paper_tiered_pending error: {e}")
        return 0


def update_paper_trade_outcomes(trade_date: str, results: list, user_id: str = "") -> dict:
    """Update paper trades for a given date with final EOD outcomes.

    Matches on (user_id, trade_date, ticker) and patches
    actual_outcome, follow_thru_pct, win_loss, false_break_up/down,
    and post_alert_move_pct (EOD close vs alert_price at IB close).
    Returns dict with updated count.
    """
    if not supabase or not results:
        return {"updated": 0}

    # Parse trade_date for Alpaca bar fetching (needed for tiered P&L)
    try:
        from datetime import date as _date_cls
        _td_parts = str(trade_date).split("-")
        _trade_date_obj = _date_cls(int(_td_parts[0]), int(_td_parts[1]), int(_td_parts[2]))
    except Exception:
        _trade_date_obj = None

    _alpaca_key = ALPACA_API_KEY
    _alpaca_sec = ALPACA_SECRET_KEY

    # Batch-fetch stored alert_price, ib_high, ib_low values for this date so we can
    # compute post_alert_move_pct and use stored IB levels as fallback for tiered P&L
    try:
        existing = (
            supabase.table("paper_trades")
            .select("ticker, alert_price, ib_high, ib_low")
            .eq("user_id", user_id)
            .eq("trade_date", str(trade_date))
            .execute()
            .data or []
        )
        alert_prices     = {row["ticker"]: row.get("alert_price") for row in existing}
        stored_ib_high   = {row["ticker"]: row.get("ib_high")    for row in existing}
        stored_ib_low    = {row["ticker"]: row.get("ib_low")     for row in existing}
        existing_tickers = {row["ticker"] for row in existing}
    except Exception:
        alert_prices     = {}
        stored_ib_high   = {}
        stored_ib_low    = {}
        existing_tickers = set()

    updated = 0
    inserted = 0
    for r in results:
        try:
            ticker    = r.get("ticker", "")
            eod_close = r.get("close_price")
            ap        = alert_prices.get(ticker)
            if ap and eod_close and float(ap) > 0:
                post_alert = round((float(eod_close) - float(ap)) / float(ap) * 100, 2)
            else:
                post_alert = None

            # ── Compute simulation P&L (IB breakout rules) ───────────────────
            sim = apply_rvol_sizing_to_sim(
                compute_trade_sim({
                    **r,
                    "follow_thru_pct": r.get("aft_move_pct"),
                    "close_price":     r.get("close_price"),   # EOD close for realistic P&L
                }),
                r.get("rvol"),
            )

            # ── Tiered exit P&L (50/25/25 ladder) — requires afternoon bars ──
            # Only compute for confirmed breakout directions; gracefully skip on
            # any Alpaca error so the rest of the EOD update is not affected.
            _tiered_result = {"eod_pnl_r": None, "tiered_pnl_r": None}
            _actual_outcome = r.get("actual_outcome", "")
            if (
                _actual_outcome in ("Bullish Break", "Bearish Break")
                and _trade_date_obj is not None
                and _alpaca_key
                and _alpaca_sec
            ):
                try:
                    # Use IB levels from the result dict; fall back to stored DB values
                    _ib_high = r.get("ib_high") or stored_ib_high.get(ticker)
                    _ib_low  = r.get("ib_low")  or stored_ib_low.get(ticker)
                    _eod_cls = r.get("close_price")
                    if _ib_high is not None and _ib_low is not None and _eod_cls is not None:
                        # Fetch intraday bars; build aft_df from bars after 10:30 AM.
                        # compute_trade_sim_tiered can still return eod_pnl_r even
                        # when aft_df is None (bars unavailable), so always call it.
                        _full_bars = fetch_bars(_alpaca_key, _alpaca_sec, ticker, _trade_date_obj)
                        _aft_df = None
                        if not _full_bars.empty:
                            _ib_cutoff = pd.Timestamp(
                                year=_trade_date_obj.year,
                                month=_trade_date_obj.month,
                                day=_trade_date_obj.day,
                                hour=10, minute=30, second=59,
                                tz=_full_bars.index.tz,
                            )
                            _sliced = _full_bars[_full_bars.index > _ib_cutoff]
                            _aft_df = _sliced if not _sliced.empty else None
                        _tiered_result = compute_trade_sim_tiered(
                            aft_df    = _aft_df,
                            ib_high   = float(_ib_high),
                            ib_low    = float(_ib_low),
                            direction = _actual_outcome,
                            close_px  = float(_eod_cls),
                        )
                        print(
                            f"Paper trade tiered P&L ({ticker}): "
                            f"eod_pnl_r={_tiered_result.get('eod_pnl_r')} "
                            f"tiered_pnl_r={_tiered_result.get('tiered_pnl_r')}"
                        )
                except Exception as _tiered_err:
                    print(f"Paper trade tiered P&L error ({ticker}): {_tiered_err}")

            # ── MAE / MFE from intraday bars (long or short) ─────────────────
            # _full_bars is only available if bars were fetched above, but we
            # attempt a fresh fetch if needed so all exit types get MAE/MFE.
            _bars_for_excursion = None
            if "_full_bars" in locals() and not _full_bars.empty:
                _bars_for_excursion = _full_bars
            elif (
                r.get("mae") is None
                and _trade_date_obj is not None
                and _alpaca_key and _alpaca_sec
                and _actual_outcome not in ("", None)
            ):
                try:
                    _bars_for_excursion = fetch_bars(_alpaca_key, _alpaca_sec, ticker, _trade_date_obj)
                except Exception:
                    _bars_for_excursion = None

            if (
                _bars_for_excursion is not None
                and not _bars_for_excursion.empty
                and r.get("mae") is None
            ):
                _ap_val = alert_prices.get(ticker)
                if _ap_val and float(_ap_val) > 0:
                    _ep = float(_ap_val)
                    _is_long = "Bullish" in str(_actual_outcome)
                    _bl = _bars_for_excursion["low"]
                    _bh = _bars_for_excursion["high"]
                    if _is_long:
                        _mae_val = round((float(_bl.min()) - _ep) / _ep * 100, 2)
                        _mfe_val = round((float(_bh.max()) - _ep) / _ep * 100, 2)
                    else:
                        _mae_val = round((_ep - float(_bh.max())) / _ep * 100, 2)
                        _mfe_val = round((_ep - float(_bl.min())) / _ep * 100, 2)
                    r["mae"] = _mae_val
                    r["mfe"] = _mfe_val

            if ticker in existing_tickers:
                # ── UPDATE existing record ────────────────────────────────────
                patch = {
                    "actual_outcome":      r.get("actual_outcome", ""),
                    "follow_thru_pct":     r.get("aft_move_pct"),
                    "win_loss":            r.get("win_loss", ""),
                    "false_break_up":      bool(r.get("false_break_up", False)),
                    "false_break_down":    bool(r.get("false_break_down", False)),
                    "post_alert_move_pct": post_alert,
                }
                if r.get("close_price") is not None:
                    patch["close_price"] = round(float(r["close_price"]), 4)
                # Add sim fields if meaningful
                if sim.get("sim_outcome") not in ("no_trade", "missing_data", "invalid_ib", None):
                    patch["sim_outcome"]      = sim["sim_outcome"]
                    patch["pnl_r_sim"]        = sim.get("pnl_r_sim")
                    patch["pnl_pct_sim"]      = sim.get("pnl_pct_sim")
                    patch["entry_price_sim"]  = sim.get("entry_price_sim")
                    patch["stop_price_sim"]   = sim.get("stop_price_sim")
                    patch["stop_dist_pct"]    = sim.get("stop_dist_pct")
                    patch["target_price_sim"] = sim.get("target_price_sim")
                    patch["sim_version"]      = SIM_VERSION
                if r.get("mae") is not None:
                    patch["mae"] = round(float(r["mae"]), 2)
                if r.get("mfe") is not None:
                    patch["mfe"] = round(float(r["mfe"]), 2)
                if r.get("exit_trigger"):
                    patch["exit_trigger"] = r["exit_trigger"]
                # Add tiered exit P&L fields if computed
                if _tiered_result.get("tiered_pnl_r") is not None:
                    patch["tiered_pnl_r"] = _tiered_result["tiered_pnl_r"]
                if _tiered_result.get("eod_pnl_r") is not None:
                    patch["eod_pnl_r"] = _tiered_result["eod_pnl_r"]
                try:
                    (
                        supabase.table("paper_trades")
                        .update(patch)
                        .eq("user_id", user_id)
                        .eq("trade_date", str(trade_date))
                        .eq("ticker", ticker)
                        .execute()
                    )
                except Exception as _upd_err:
                    _upd_s = str(_upd_err).lower()
                    _opt_update_cols = [
                        "mae", "mfe", "exit_trigger", "entry_ib_distance", "entry_time",
                        "tiered_pnl_r", "eod_pnl_r", "sim_version",
                    ]
                    if any(col in _upd_s for col in _opt_update_cols):
                        for col in _opt_update_cols:
                            patch.pop(col, None)
                        (
                            supabase.table("paper_trades")
                            .update(patch)
                            .eq("user_id", user_id)
                            .eq("trade_date", str(trade_date))
                            .eq("ticker", ticker)
                            .execute()
                        )
                        print(f"Paper trade update ({ticker}): optional columns missing — saved without them")
                    else:
                        raise
                updated += 1

            else:
                # ── INSERT new record for EOD-only tickers ────────────────────
                insert_row = {
                    "user_id":             user_id,
                    "trade_date":          str(trade_date),
                    "ticker":              ticker,
                    "tcs":                 r.get("tcs"),
                    "predicted":           r.get("predicted", ""),
                    "actual_outcome":      r.get("actual_outcome", ""),
                    "follow_thru_pct":     r.get("aft_move_pct"),
                    "win_loss":            r.get("win_loss", ""),
                    "false_break_up":      bool(r.get("false_break_up", False)),
                    "false_break_down":    bool(r.get("false_break_down", False)),
                    "post_alert_move_pct": post_alert,
                    "open_price":          r.get("open_price"),
                    "ib_low":              r.get("ib_low"),
                    "ib_high":             r.get("ib_high"),
                    "min_tcs_filter":      r.get("min_tcs_filter", 50),
                    "scan_type":           r.get("scan_type", "eod"),
                }
                if sim.get("sim_outcome") not in ("no_trade", "missing_data", "invalid_ib", None):
                    insert_row["sim_outcome"]      = sim["sim_outcome"]
                    insert_row["pnl_r_sim"]        = sim.get("pnl_r_sim")
                    insert_row["pnl_pct_sim"]      = sim.get("pnl_pct_sim")
                    insert_row["entry_price_sim"]  = sim.get("entry_price_sim")
                    insert_row["stop_price_sim"]   = sim.get("stop_price_sim")
                    insert_row["stop_dist_pct"]    = sim.get("stop_dist_pct")
                    insert_row["target_price_sim"] = sim.get("target_price_sim")
                    insert_row["sim_version"]      = SIM_VERSION
                if r.get("mae") is not None:
                    insert_row["mae"] = round(float(r["mae"]), 2)
                if r.get("mfe") is not None:
                    insert_row["mfe"] = round(float(r["mfe"]), 2)
                # Add tiered exit P&L fields if computed
                if _tiered_result.get("tiered_pnl_r") is not None:
                    insert_row["tiered_pnl_r"] = _tiered_result["tiered_pnl_r"]
                if _tiered_result.get("eod_pnl_r") is not None:
                    insert_row["eod_pnl_r"] = _tiered_result["eod_pnl_r"]
                # Remove None values to let DB defaults apply
                insert_row = {k: v for k, v in insert_row.items() if v is not None}
                (
                    supabase.table("paper_trades")
                    .insert(insert_row)
                    .execute()
                )
                inserted += 1
                print(f"Paper trade inserted (EOD-only): {ticker} {r.get('win_loss','?')} — {r.get('actual_outcome','?')}")

        except Exception as e:
            print(f"Paper trade update error ({r.get('ticker')}): {e}")

    if inserted:
        print(f"Paper trade EOD: {updated} updated + {inserted} newly inserted")
    return {"updated": updated + inserted}


def patch_exit_obs(ticker: str, trade_date, exit_obs: str, user_id: str = "") -> bool:
    """Save a manual exit observation note to an existing paper trade row.

    If trade_date is None, targets the most recent trade for that ticker.
    """
    if not supabase or not ticker or not exit_obs:
        return False
    try:
        q = (supabase.table("paper_trades")
             .update({"exit_obs": exit_obs.strip()})
             .eq("user_id", user_id)
             .eq("ticker", ticker.upper()))
        if trade_date is not None:
            q = q.eq("trade_date", str(trade_date))
        else:
            # Find the most recent trade for this ticker and update that row
            recent = (supabase.table("paper_trades")
                      .select("id,trade_date")
                      .eq("user_id", user_id)
                      .eq("ticker", ticker.upper())
                      .order("trade_date", desc=True)
                      .limit(1)
                      .execute())
            if not recent.data:
                return False
            row_id = recent.data[0]["id"]
            q = (supabase.table("paper_trades")
                 .update({"exit_obs": exit_obs.strip()})
                 .eq("id", row_id))
        q.execute()
        return True
    except Exception as e:
        print(f"patch_exit_obs error ({ticker}): {e}")
        return False


# ── Nightly Ticker Rankings ────────────────────────────────────────────────────

def ensure_ticker_rankings_table() -> bool:
    """Return True if ticker_rankings table exists/is ready."""
    if not supabase:
        return False
    try:
        supabase.table("ticker_rankings").select("id").limit(1).execute()
        return True
    except Exception:
        return False


_TICKER_RANKINGS_CONTEXT_MIGRATION = (
    "-- Run in Supabase SQL Editor to add ranking context columns:\n"
    "ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS tcs REAL;\n"
    "ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS rvol REAL;\n"
    "ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS edge_score REAL;\n"
    "ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS predicted_structure TEXT;\n"
    "ALTER TABLE ticker_rankings ADD COLUMN IF NOT EXISTS confidence_label TEXT;\n"
)


def save_ticker_rankings(user_id: str, rating_date, rankings: list) -> dict:
    """Upsert a list of {ticker, rank, notes, ...context} dicts for a given night.

    rating_date: date object or YYYY-MM-DD string.
    Context fields (tcs, rvol, edge_score, predicted_structure, confidence_label)
    are stored when present. Graceful fallback if columns don't exist yet.
    Returns {saved: int, errors: int}.
    """
    if not supabase or not rankings:
        return {"saved": 0, "errors": 0}
    date_str = str(rating_date)
    saved = errors = 0
    _context_cols = ["tcs", "rvol", "edge_score", "predicted_structure", "confidence_label"]
    _include_context = True
    for r in rankings:
        ticker = r.get("ticker", "").strip().upper()
        rank   = int(r.get("rank", 0))
        notes  = r.get("notes", "")
        if not ticker:
            continue
        row = {
            "user_id":     user_id,
            "rating_date": date_str,
            "ticker":      ticker,
            "rank":        rank,
            "notes":       notes,
            "verified":    False,
        }
        if _include_context:
            for col in _context_cols:
                val = r.get(col)
                if val is not None:
                    if col in ("tcs", "rvol", "edge_score"):
                        row[col] = round(float(val), 2)
                    else:
                        row[col] = str(val)
        try:
            supabase.table("ticker_rankings").upsert(
                row, on_conflict="user_id,rating_date,ticker"
            ).execute()
            saved += 1
        except Exception as _e:
            _es = str(_e).lower()
            if _include_context and any(col in _es for col in _context_cols):
                _include_context = False
                for col in _context_cols:
                    row.pop(col, None)
                try:
                    supabase.table("ticker_rankings").upsert(
                        row, on_conflict="user_id,rating_date,ticker"
                    ).execute()
                    saved += 1
                    print("ticker_rankings: context columns missing — saved without them.\n"
                          "Run in SQL Editor:\n" + _TICKER_RANKINGS_CONTEXT_MIGRATION)
                except Exception:
                    errors += 1
            else:
                errors += 1
    return {"saved": saved, "errors": errors}


def load_ticker_rankings(user_id: str, rating_date=None) -> "pd.DataFrame":
    """Load ticker rankings for a given date (or all if None)."""
    if not supabase:
        return pd.DataFrame()
    try:
        q = (supabase.table("ticker_rankings")
             .select("rating_date,ticker,rank,notes,actual_chg_pct,actual_open,actual_close,verified,tcs,rvol,edge_score,predicted_structure,confidence_label")
             .eq("user_id", user_id)
             .order("rating_date", desc=True)
             .order("rank", desc=True))
        if rating_date:
            q = q.eq("rating_date", str(rating_date))
        res = q.execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame()
    except Exception:
        try:
            q = (supabase.table("ticker_rankings")
                 .select("rating_date,ticker,rank,notes,actual_chg_pct,actual_open,actual_close,verified")
                 .eq("user_id", user_id)
                 .order("rating_date", desc=True)
                 .order("rank", desc=True))
            if rating_date:
                q = q.eq("rating_date", str(rating_date))
            res = q.execute()
            return pd.DataFrame(res.data) if res.data else pd.DataFrame()
        except Exception:
            return pd.DataFrame()


def verify_ticker_rankings(api_key: str, secret_key: str, user_id: str, rating_date,
                           same_day: bool = False) -> dict:
    """Pull price data for all ranked tickers on rating_date and write
    actual_chg_pct, actual_open, actual_close, verified=True back to Supabase.

    same_day=False (default): uses next trading day's data (for ratings made the
      night before a session — verify after that session closes).
    same_day=True: uses the rating_date itself as the trading day (for ratings
      made early morning of a session — verify same evening after close).

    Returns {verified: int, errors: int, rows: list[dict]}.
    """
    if not supabase:
        return {"verified": 0, "errors": 0, "rows": []}
    import datetime as _dt
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame as TF

    df = load_ticker_rankings(user_id, rating_date)
    if df.empty:
        return {"verified": 0, "errors": 0, "rows": []}

    r_date = rating_date if isinstance(rating_date, _dt.date) else _dt.date.fromisoformat(str(rating_date))
    if same_day:
        trading_day = r_date
    else:
        trading_day = r_date + _dt.timedelta(days=1)
        while trading_day.weekday() >= 5:
            trading_day += _dt.timedelta(days=1)

    client = StockHistoricalDataClient(api_key, secret_key)
    mo = EASTERN.localize(_dt.datetime(trading_day.year, trading_day.month, trading_day.day, 9, 30))
    mc = EASTERN.localize(_dt.datetime(trading_day.year, trading_day.month, trading_day.day, 16, 0))

    verified = errors = 0
    rows = []
    for _, row in df.iterrows():
        ticker = row["ticker"]
        rank   = int(row.get("rank", 0)) if row.get("rank") is not None else 0
        try:
            req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TF.Day,
                                   start=mo, end=mc)
            bars = client.get_stock_bars(req)
            bdf  = bars.df
            if bdf.empty:
                errors += 1
                continue
            if isinstance(bdf.index, pd.MultiIndex):
                bdf = bdf.xs(ticker, level="symbol")
            open_p  = round(float(bdf["open"].iloc[0]), 4)
            close_p = round(float(bdf["close"].iloc[-1]), 4)
            chg     = round((close_p - open_p) / open_p * 100, 2) if open_p else 0.0
            supabase.table("ticker_rankings").update({
                "actual_open":    open_p,
                "actual_close":   close_p,
                "actual_chg_pct": chg,
                "verified":       True,
            }).eq("user_id", user_id).eq("rating_date", str(rating_date)).eq("ticker", ticker).execute()
            verified += 1
            rows.append({"ticker": ticker, "rank": rank, "chg": chg,
                         "open": open_p, "close": close_p,
                         "notes": str(row.get("notes") or "")})
        except Exception:
            errors += 1
    return {"verified": verified, "errors": errors, "rows": rows}


def load_ranking_accuracy(user_id: str) -> "pd.DataFrame":
    """Return accuracy stats grouped by rank tier for verified rankings."""
    if not supabase:
        return pd.DataFrame()
    try:
        try:
            res = (supabase.table("ticker_rankings")
                   .select("rank,actual_chg_pct,verified,tcs,rvol,edge_score,predicted_structure,confidence_label")
                   .eq("user_id", user_id)
                   .eq("verified", True)
                   .execute())
        except Exception:
            res = (supabase.table("ticker_rankings")
                   .select("rank,actual_chg_pct,verified")
                   .eq("user_id", user_id)
                   .eq("verified", True)
                   .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        # Rank 4/5 = bullish (win = positive chg)
        # Rank 1/2 = bearish/fade (win = negative chg)
        # Rank 3   = neutral (not scored)
        # Rank 0   = don't take the trade (excluded from scoring)
        def _ranking_win(row):
            chg = row["actual_chg_pct"]
            if chg is None:
                return False
            if row["rank"] in (4, 5):
                return chg > 0
            elif row["rank"] in (1, 2):
                return chg < 0
            return False  # rank 3 = neutral, rank 0 = skip
        df["winner"] = df.apply(_ranking_win, axis=1)
        agg_dict = {
            "trades": ("actual_chg_pct", "count"),
            "winners": ("winner", "sum"),
            "avg_chg": ("actual_chg_pct", "mean"),
        }
        if "tcs" in df.columns:
            agg_dict["avg_tcs"] = ("tcs", "mean")
        if "rvol" in df.columns:
            agg_dict["avg_rvol"] = ("rvol", "mean")
        acc = (df.groupby("rank")
               .agg(**agg_dict)
               .reset_index()
               .sort_values("rank", ascending=False))
        acc["win_rate"] = (acc["winners"] / acc["trades"] * 100).round(1)
        acc["avg_chg"]  = acc["avg_chg"].round(2)
        if "avg_tcs" in acc.columns:
            acc["avg_tcs"] = acc["avg_tcs"].round(1)
        if "avg_rvol" in acc.columns:
            acc["avg_rvol"] = acc["avg_rvol"].round(1)
        return acc
    except Exception:
        return pd.DataFrame()


# ── Cognitive Delta Log ────────────────────────────────────────────────────────
_COGNITIVE_DELTA_SQL = """
CREATE TABLE IF NOT EXISTS cognitive_delta_log (
  id           SERIAL PRIMARY KEY,
  user_id      TEXT NOT NULL DEFAULT '',
  trade_date   DATE NOT NULL,
  ticker       TEXT NOT NULL,
  system_rank  INT,
  system_tcs   FLOAT,
  system_structure TEXT,
  user_action  TEXT NOT NULL,
  actual_chg   FLOAT,
  system_correct BOOLEAN,
  notes        TEXT,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, trade_date, ticker, user_action)
);
""".strip()


def ensure_cognitive_delta_table() -> bool:
    if not supabase:
        return False
    try:
        supabase.table("cognitive_delta_log").select("id").limit(1).execute()
        return True
    except Exception:
        try:
            supabase.rpc("exec_sql", {"sql": _COGNITIVE_DELTA_SQL}).execute()
            return True
        except Exception as e:
            print(f"ensure_cognitive_delta_table error: {e}")
            return False


def save_cognitive_delta_entries(user_id: str, trade_date, entries: list) -> dict:
    """Save a list of cognitive delta entries for a given day.

    Each entry dict: {ticker, system_rank, system_tcs, system_structure, user_action, notes}
    user_action: 'followed' | 'skipped' | 'override'
    """
    if not supabase or not entries:
        return {"saved": 0, "errors": []}
    saved, errors = 0, []
    for e in entries:
        try:
            row = {
                "user_id":           user_id,
                "trade_date":        str(trade_date),
                "ticker":            e["ticker"].upper(),
                "system_rank":       e.get("system_rank"),
                "system_tcs":        e.get("system_tcs"),
                "system_structure":  e.get("system_structure"),
                "user_action":       e.get("user_action", "followed"),
                "notes":             e.get("notes", ""),
            }
            supabase.table("cognitive_delta_log").upsert(row, on_conflict="user_id,trade_date,ticker,user_action").execute()
            saved += 1
        except Exception as ex:
            errors.append(f"{e.get('ticker','?')}: {ex}")
    return {"saved": saved, "errors": errors}


def load_cognitive_delta_today(user_id: str, trade_date=None) -> "pd.DataFrame":
    if not supabase:
        return pd.DataFrame()
    try:
        d = str(trade_date or date.today())
        res = (supabase.table("cognitive_delta_log")
               .select("*")
               .eq("user_id", user_id)
               .eq("trade_date", d)
               .execute())
        return pd.DataFrame(res.data) if res.data else pd.DataFrame()
    except Exception as e:
        print(f"load_cognitive_delta_today error: {e}")
        return pd.DataFrame()


def verify_cognitive_delta(api_key: str, secret_key: str, user_id: str, trade_date=None) -> int:
    """Fill in actual_chg + system_correct for all unverified delta entries on trade_date."""
    if not supabase:
        return 0
    try:
        d = trade_date or date.today()
        res = (supabase.table("cognitive_delta_log")
               .select("id,ticker,system_rank,user_action")
               .eq("user_id", user_id)
               .eq("trade_date", str(d))
               .is_("actual_chg", "null")
               .execute())
        if not res.data:
            return 0
        tickers = list({r["ticker"] for r in res.data})
        try:
            import alpaca_trade_api as tradeapi
            _a = tradeapi.REST(api_key, secret_key, base_url="https://data.alpaca.markets")
            snaps = _a.get_snapshots(tickers, feed="iex")
            prices = {}
            for sym, snap in snaps.items():
                try:
                    prices[sym] = (snap.daily_bar.close - snap.daily_bar.open) / snap.daily_bar.open * 100
                except Exception:
                    pass
        except Exception:
            prices = {}
        updated = 0
        for r in res.data:
            chg = prices.get(r["ticker"])
            if chg is None:
                continue
            rank = r.get("system_rank")
            if rank in (4, 5):
                correct = chg > 0
            elif rank in (1, 2):
                correct = chg < 0
            else:
                correct = None
            try:
                supabase.table("cognitive_delta_log").update({
                    "actual_chg": round(chg, 2),
                    "system_correct": correct,
                }).eq("id", r["id"]).execute()
                updated += 1
            except Exception:
                pass
        return updated
    except Exception as e:
        print(f"verify_cognitive_delta error: {e}")
        return 0


def load_cognitive_delta_analysis(user_id: str) -> "pd.DataFrame":
    """Aggregate the cognitive delta log: system vs. user deviance accuracy."""
    if not supabase:
        return pd.DataFrame()
    try:
        res = (supabase.table("cognitive_delta_log")
               .select("*")
               .eq("user_id", user_id)
               .not_.is_("actual_chg", "null")
               .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df["actual_chg"] = pd.to_numeric(df["actual_chg"], errors="coerce")
        df["user_won"] = df.apply(lambda r: (
            r["actual_chg"] > 0 if r["user_action"] in ("followed", "override")
            else (r["actual_chg"] < 0 if r["user_action"] == "skipped" else None)
        ), axis=1)
        return df
    except Exception as e:
        print(f"load_cognitive_delta_analysis error: {e}")
        return pd.DataFrame()


# ── Bot vs Trader Convergence ───────────────────────────────────────────────────

def compute_bot_vs_trader_stats(user_id: str, days: int = 90) -> dict:
    """Cross-reference bot paper_trades with trader cognitive_delta_log decisions.

    Returns a dict with keys:
      - 'daily'             : list of per-day summary dicts
      - 'patterns'          : list of human-readable pattern flag strings
      - 'convergence_series': list of (date_str, rate_float) pairs
      - 'summary'           : overall aggregate stats dict
    """
    empty = {"daily": [], "patterns": [], "convergence_series": [], "summary": {}}
    if not supabase or not user_id:
        return empty
    try:
        from datetime import date as _date, timedelta as _td
        cutoff = str(_date.today() - _td(days=days))

        pt_res = (supabase.table("paper_trades")
                  .select("trade_date,ticker,tcs,rvol,alpaca_order_id,predicted,win_loss,scan_type")
                  .eq("user_id", user_id)
                  .gte("trade_date", cutoff)
                  .execute())
        pt_data = pt_res.data or []

        cd_res = (supabase.table("cognitive_delta_log")
                  .select("trade_date,ticker,user_action,system_tcs,system_structure,actual_chg,notes")
                  .eq("user_id", user_id)
                  .gte("trade_date", cutoff)
                  .execute())
        cd_data = cd_res.data or []

        if not pt_data and not cd_data:
            return empty

        pt_df = pd.DataFrame(pt_data) if pt_data else pd.DataFrame(
            columns=["trade_date", "ticker", "tcs", "rvol", "alpaca_order_id", "predicted", "win_loss", "scan_type"]
        )
        cd_df = pd.DataFrame(cd_data) if cd_data else pd.DataFrame(
            columns=["trade_date", "ticker", "user_action", "system_tcs", "system_structure", "actual_chg", "notes"]
        )

        if not pt_df.empty:
            pt_df["ticker"]     = pt_df["ticker"].astype(str).str.upper().str.strip()
            pt_df["trade_date"] = pt_df["trade_date"].astype(str).str[:10]
            pt_df["tcs"]        = pd.to_numeric(pt_df["tcs"], errors="coerce")
            pt_df["rvol"]       = pd.to_numeric(pt_df["rvol"], errors="coerce")

        if not cd_df.empty:
            cd_df["ticker"]     = cd_df["ticker"].astype(str).str.upper().str.strip()
            cd_df["trade_date"] = cd_df["trade_date"].astype(str).str[:10]

        # Per-day loop
        all_dates = sorted(
            set(pt_df["trade_date"].unique().tolist() if not pt_df.empty else []) |
            set(cd_df["trade_date"].unique().tolist() if not cd_df.empty else [])
        )

        daily = []
        for d in all_dates:
            pt_day = pt_df[pt_df["trade_date"] == d] if not pt_df.empty else pd.DataFrame()
            cd_day = cd_df[cd_df["trade_date"] == d] if not cd_df.empty else pd.DataFrame()

            bot_tickers = set(pt_day["ticker"].tolist()) if not pt_day.empty else set()

            followed_tickers  = set()
            skipped_tickers   = set()
            override_tickers  = set()
            if not cd_day.empty:
                followed_tickers  = set(cd_day[cd_day["user_action"] == "followed"]["ticker"].tolist())
                skipped_tickers   = set(cd_day[cd_day["user_action"] == "skipped"]["ticker"].tolist())
                override_tickers  = set(cd_day[cd_day["user_action"] == "override"]["ticker"].tolist())

            overlap      = bot_tickers & followed_tickers
            bot_only     = bot_tickers - followed_tickers - override_tickers
            user_only    = override_tickers - bot_tickers
            union        = bot_tickers | followed_tickers | override_tickers
            conv_rate    = round(len(overlap) / len(union) * 100, 1) if union else 0.0

            daily.append({
                "date":             d,
                "bot_count":        len(bot_tickers),
                "user_followed":    len(followed_tickers),
                "user_overrides":   len(override_tickers),
                "overlap":          len(overlap),
                "bot_only":         len(bot_only),
                "user_only":        len(user_only),
                "convergence_rate": conv_rate,
                "bot_tickers":      sorted(bot_tickers),
                "followed_tickers": sorted(followed_tickers),
                "skipped_tickers":  sorted(skipped_tickers),
                "override_tickers": sorted(override_tickers),
            })

        # Pattern analysis (needs both data sources)
        patterns = []
        if not pt_df.empty and not cd_df.empty:
            # Deduplicate cd_df to one action per (trade_date, ticker): most decisive wins
            # Priority: followed > override > skipped > no_log
            _action_priority = {"followed": 0, "override": 1, "skipped": 2, "no_log": 3}
            _cd_dedup = (
                cd_df[["trade_date", "ticker", "user_action"]]
                .assign(_priority=lambda df: df["user_action"].map(
                    lambda a: _action_priority.get(a, 3)
                ))
                .sort_values("_priority")
                .drop_duplicates(subset=["trade_date", "ticker"], keep="first")
                .drop(columns=["_priority"])
            )
            merged = pt_df.merge(
                _cd_dedup,
                on=["trade_date", "ticker"],
                how="left",
            )
            merged["user_action"] = merged["user_action"].fillna("no_log")
            total_calls    = len(merged)
            total_skipped  = (merged["user_action"] == "skipped").sum()
            total_followed = (merged["user_action"] == "followed").sum()

            if total_calls >= 5:
                skip_rate   = round(total_skipped  / total_calls * 100, 1)
                follow_rate = round(total_followed / total_calls * 100, 1)
                patterns.append(
                    f"Overall: you follow {follow_rate}% of bot calls and skip {skip_rate}%"
                    f" ({total_followed} followed / {total_skipped} skipped / {total_calls} total)"
                )

            # RVOL-based skip rate
            if "rvol" in merged.columns and int(merged["rvol"].notna().sum()) >= 5:
                for rvol_label, rvol_mask in [
                    ("RVOL < 1.5",  merged["rvol"] <  1.5),
                    ("RVOL ≥ 2.0",  merged["rvol"] >= 2.0),
                ]:
                    subset = merged[rvol_mask]
                    if len(subset) >= 3:
                        pct = round((subset["user_action"] == "skipped").sum() / len(subset) * 100, 1)
                        n_skip = int((subset["user_action"] == "skipped").sum())
                        patterns.append(
                            f"{rvol_label}: you skip {pct}% of bot calls ({n_skip}/{len(subset)})"
                        )

            # TCS-based skip rate
            if "tcs" in merged.columns and int(merged["tcs"].notna().sum()) >= 5:
                for tcs_label, tcs_mask in [
                    ("TCS < 60",  merged["tcs"] <  60),
                    ("TCS ≥ 75",  merged["tcs"] >= 75),
                ]:
                    subset = merged[tcs_mask]
                    if len(subset) >= 3:
                        pct    = round((subset["user_action"] == "skipped").sum() / len(subset) * 100, 1)
                        n_skip = int((subset["user_action"] == "skipped").sum())
                        patterns.append(
                            f"{tcs_label}: you skip {pct}% of bot calls ({n_skip}/{len(subset)})"
                        )

            # Per-structure skip rate (only flag outliers: ≥ 60% or ≤ 20% skip rate)
            if "predicted" in merged.columns and int(merged["predicted"].notna().sum()) >= 5:
                for struct in merged["predicted"].dropna().unique():
                    s_rows = merged[merged["predicted"] == struct]
                    if len(s_rows) >= 3:
                        pct    = round((s_rows["user_action"] == "skipped").sum() / len(s_rows) * 100, 1)
                        n_skip = int((s_rows["user_action"] == "skipped").sum())
                        if pct >= 60 or pct <= 20:
                            patterns.append(
                                f"{struct}: you skip {pct}% of calls ({n_skip}/{len(s_rows)})"
                            )

        # Convergence series (only days where bot actually fired calls)
        convergence_series = [
            (d["date"], d["convergence_rate"])
            for d in daily
            if d["bot_count"] > 0
        ]

        # Summary totals
        total_days_active  = len([d for d in daily if d["bot_count"] > 0 or d["user_followed"] > 0])
        total_bot          = sum(d["bot_count"]      for d in daily)
        total_followed_all = sum(d["user_followed"]  for d in daily)
        total_overrides    = sum(d["user_overrides"] for d in daily)
        total_overlap      = sum(d["overlap"]        for d in daily)
        overall_conv       = round(total_overlap / max(total_bot + total_overrides, 1) * 100, 1)

        # Convergence trend via linear slope (last 30 data points)
        recent = convergence_series[-30:]
        conv_trend = "insufficient_data"
        if len(recent) >= 5:
            xs    = list(range(len(recent)))
            ys    = [r for _, r in recent]
            avg_x = sum(xs) / len(xs)
            avg_y = sum(ys) / len(ys)
            denom = sum((x - avg_x) ** 2 for x in xs) or 1
            slope = sum((xs[i] - avg_x) * (ys[i] - avg_y) for i in range(len(xs))) / denom
            if   slope >  0.5:  conv_trend = "rising_fast"
            elif slope >  0.1:  conv_trend = "rising_slow"
            elif slope < -0.5:  conv_trend = "falling_fast"
            elif slope < -0.1:  conv_trend = "falling_slow"
            else:               conv_trend = "flat"

        summary = {
            "total_days":              total_days_active,
            "total_bot_calls":         total_bot,
            "total_followed":          total_followed_all,
            "total_overrides":         total_overrides,
            "total_overlap":           total_overlap,
            "overall_convergence_rate": overall_conv,
            "convergence_trend":       conv_trend,
        }

        return {
            "daily":              daily,
            "patterns":           patterns,
            "convergence_series": convergence_series,
            "summary":            summary,
        }
    except Exception as e:
        print(f"compute_bot_vs_trader_stats error: {e}")
        return empty


# ── Decision Log ───────────────────────────────────────────────────────────────
_DECISION_LOG_SQL = """
CREATE TABLE IF NOT EXISTS decision_log (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID NOT NULL,
  decision_date    DATE NOT NULL DEFAULT CURRENT_DATE,
  category         TEXT NOT NULL,
  call             TEXT NOT NULL,
  reasoning        TEXT,
  outcome          TEXT NOT NULL DEFAULT 'Pending',
  outcome_notes    TEXT,
  outcome_date     DATE,
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  updated_at       TIMESTAMPTZ,
  last_reopened_at TIMESTAMPTZ,
  reopen_count     INTEGER NOT NULL DEFAULT 0,
  reopen_notes     TEXT,
  reopen_history   JSONB NOT NULL DEFAULT '[]'
);
""".strip()

_DECISION_LOG_RLS_SQLS = [
    "ALTER TABLE decision_log ENABLE ROW LEVEL SECURITY",
    (
        "CREATE POLICY dl_select ON decision_log FOR SELECT "
        "USING (user_id::text = auth.uid()::text)"
    ),
    (
        "CREATE POLICY dl_insert ON decision_log FOR INSERT "
        "WITH CHECK (user_id::text = auth.uid()::text)"
    ),
    (
        "CREATE POLICY dl_update ON decision_log FOR UPDATE "
        "USING (user_id::text = auth.uid()::text)"
    ),
]

_DECISION_SEEDS = [
    {
        "decision_date": "2026-01-01",
        "category": "System Design",
        "call": "VP/IB structure has genuine alpha that can be systematized",
        "reasoning": "Initial hypothesis based on manual backtesting of Volume Profile patterns.",
        "outcome": "Confirmed",
        "outcome_date": "2026-01-31",
        "outcome_notes": "Live paper WR = 83.8% overall, 95.2% at TCS>=50. Edge confirmed.",
    },
    {
        "decision_date": "2026-01-15",
        "category": "Filter",
        "call": "TCS scoring is the key filter — higher TCS = dramatically better WR",
        "reasoning": "Hypothesis that a composite conviction score would separate signal from noise.",
        "outcome": "Confirmed",
        "outcome_date": "2026-02-14",
        "outcome_notes": "TCS>=50 live WR 95.2% vs 83.8% overall — 11.4pp improvement confirmed.",
    },
    {
        "decision_date": "2026-02-01",
        "category": "System Design",
        "call": "Intraday scan will outperform morning scan on WR",
        "reasoning": "Intraday setups have more confirmation data (IB established, volume settled).",
        "outcome": "Confirmed",
        "outcome_date": "2026-03-03",
        "outcome_notes": "Intraday WR 83.7%, avg win +1.07R vs morning 83.9% avg win +0.88R. Intraday edges on R.",
    },
    {
        "decision_date": "2026-03-01",
        "category": "Market Thesis",
        "call": "Live paper WR will track backtest WR within 5%",
        "reasoning": "If the edge is real and not overfit, live and backtest should converge.",
        "outcome": "Confirmed",
        "outcome_date": "2026-03-31",
        "outcome_notes": "TCS>=50 live WR 84.6% vs backtest 85.2% — 0.6pp gap. Near-perfect tracking.",
    },
    {
        "decision_date": "2026-03-15",
        "category": "Filter",
        "call": "Full combo filter (TCS>=50 + IB<10% + VWAP) will push live WR above 90%",
        "reasoning": "Combining three independent confirmation signals should compound the edge.",
        "outcome": "Confirmed",
        "outcome_date": "2026-04-14",
        "outcome_notes": "Full filter live WR = 93.3% on 15 trades (+1.343R expectancy). Target exceeded.",
    },
    {
        "decision_date": "2026-04-01",
        "category": "Timing",
        "call": "Phase 2 gate (30 trades, 60% WR, 30 days) achievable by May 6, 2026",
        "reasoning": "Gates 1+2 already passed. Gate 3 (days) is purely calendar — unlocks May 6.",
        "outcome": "Pending",
        "outcome_date": None,
        "outcome_notes": None,
    },
    {
        "decision_date": "2026-04-10",
        "category": "Sizing",
        "call": "Starting $7k compounded at 1.4 trades/day reaches $1M within 12 months at live WR",
        "reasoning": "Geometric mean compounding: 92.3% WR, +1.54R avg win, 2.14% risk/trade.",
        "outcome": "Pending",
        "outcome_date": None,
        "outcome_notes": None,
    },
    {
        "decision_date": "2026-04-17",
        "category": "System Design",
        "call": "Brain not generating directional signals in live conditions — needs investigation",
        "reasoning": "Bot showing 0 Alpaca bracket orders despite LIVE_ORDERS_ENABLED=true. All predictions are Neutral/Ntrl Extreme.",
        "outcome": "Pending",
        "outcome_date": None,
        "outcome_notes": None,
    },
]


def ensure_decision_log_table() -> bool:
    """Create the decision_log table and RLS policies if they don't exist.

    Returns True if the table is available for use.
    Uses the Supabase service-role key (module-level `supabase` client), which bypasses
    RLS for application writes. RLS + policies are still applied so direct/anon access
    is properly scoped to the owning user.
    """
    if not supabase:
        return False
    try:
        supabase.table("decision_log").select("id").limit(1).execute()
    except Exception:
        try:
            supabase.rpc("exec_sql", {"query": _DECISION_LOG_SQL}).execute()
        except Exception as e:
            print(f"ensure_decision_log_table CREATE error: {e}")
            return False

    for rls_sql in _DECISION_LOG_RLS_SQLS:
        try:
            supabase.rpc("exec_sql", {"query": rls_sql}).execute()
        except Exception as e:
            es = str(e)
            if "already exists" in es.lower() or "42710" in es or "42P16" in es:
                pass
            else:
                print(f"ensure_decision_log_table RLS warning ({rls_sql[:40]}): {es[:120]}")

    try:
        supabase.rpc(
            "exec_sql",
            {"query": "ALTER TABLE decision_log ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ"},
        ).execute()
    except Exception as e:
        print(f"ensure_decision_log_table migration (updated_at) warning: {e}")

    try:
        supabase.rpc(
            "exec_sql",
            {"query": "ALTER TABLE decision_log ADD COLUMN IF NOT EXISTS last_reopened_at TIMESTAMPTZ"},
        ).execute()
    except Exception as e:
        print(f"ensure_decision_log_table migration (last_reopened_at) warning: {e}")

    try:
        supabase.rpc(
            "exec_sql",
            {"query": "ALTER TABLE decision_log ADD COLUMN IF NOT EXISTS reopen_count INTEGER NOT NULL DEFAULT 0"},
        ).execute()
    except Exception as e:
        print(f"ensure_decision_log_table migration (reopen_count) warning: {e}")

    try:
        supabase.rpc(
            "exec_sql",
            {"query": "ALTER TABLE decision_log ADD COLUMN IF NOT EXISTS reopen_notes TEXT"},
        ).execute()
    except Exception as e:
        print(f"ensure_decision_log_table migration (reopen_notes) warning: {e}")

    try:
        supabase.rpc(
            "exec_sql",
            {
                "query": (
                    "ALTER TABLE decision_log "
                    "ADD COLUMN IF NOT EXISTS reopen_history JSONB NOT NULL DEFAULT '[]'"
                )
            },
        ).execute()
    except Exception as e:
        print(f"ensure_decision_log_table migration (reopen_history) warning: {e}")

    return True


def get_decisions(user_id: str) -> list:
    """Fetch all decision_log rows for user, newest first."""
    if not supabase:
        return []
    try:
        res = (
            supabase.table("decision_log")
            .select("*")
            .eq("user_id", user_id)
            .order("decision_date", desc=True)
            .order("created_at", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"get_decisions error: {e}")
        return []


def insert_decision(user_id: str, decision_date, category: str, call: str, reasoning: str = "") -> bool:
    """Insert a new decision into decision_log."""
    if not supabase:
        return False
    try:
        row = {
            "user_id": user_id,
            "decision_date": str(decision_date),
            "category": category,
            "call": call.strip(),
            "reasoning": reasoning.strip() or None,
            "outcome": "Pending",
        }
        supabase.table("decision_log").insert(row).execute()
        return True
    except Exception as e:
        print(f"insert_decision error: {e}")
        return False


def update_decision_outcome(
    decision_id: str,
    user_id: str,
    outcome: str,
    outcome_date,
    outcome_notes: str = "",
    is_edit: bool = False,
    reopen_notes: str = "",
) -> bool:
    """Update outcome fields for an existing decision.

    Filters by both `id` AND `user_id` so callers can only modify their own rows,
    even without relying solely on RLS.

    When ``is_edit=True`` the ``updated_at`` column is stamped with the current
    UTC time so the UI can display an "edited <date>" label.

    When ``outcome=="Pending"`` (a reopen) ``last_reopened_at`` is stamped with
    the current UTC time, ``reopen_notes`` is written with the trader's optional
    reason, and ``outcome_notes`` is left untouched so the original outcome
    annotation is preserved.
    """
    if not supabase:
        return False
    import re as _re
    _UUID_RE = _re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        _re.IGNORECASE,
    )
    if not _UUID_RE.match(str(decision_id)) or not _UUID_RE.match(str(user_id)):
        print("update_decision_outcome: invalid UUID format in decision_id or user_id")
        return False
    try:
        if outcome == "Pending":
            _reopen_ts = datetime.utcnow().isoformat()
            patch = {
                "outcome": outcome,
                "outcome_date": None,
                "updated_at": None,
                "last_reopened_at": _reopen_ts,
                "reopen_notes": reopen_notes.strip() or None,
            }
            supabase.table("decision_log").update(patch).eq("id", decision_id).eq("user_id", user_id).execute()
            # Atomically increment reopen_count and append to reopen_history at the DB level.
            # decision_id and user_id are validated as strict UUID format above
            # (hex digits and hyphens only), so interpolation here is safe.
            # reopen_notes is passed via jsonb_build_object to avoid SQL injection.
            _safe_notes = reopen_notes.strip().replace("'", "''")
            _inc_sql = (
                "UPDATE decision_log "
                "SET reopen_count = COALESCE(reopen_count, 0) + 1, "
                "    reopen_history = COALESCE(reopen_history, '[]'::jsonb) || "
                f"        jsonb_build_array(jsonb_build_object('at', '{_reopen_ts}'::text, 'notes', '{_safe_notes}'::text)) "
                f"WHERE id = '{decision_id}' AND user_id = '{user_id}'"
            )
            supabase.rpc("exec_sql", {"query": _inc_sql}).execute()
        else:
            patch = {
                "outcome": outcome,
                "outcome_date": str(outcome_date) if outcome_date else None,
                "outcome_notes": outcome_notes.strip() or None,
                "reopen_notes": None,
            }
            if is_edit:
                patch["updated_at"] = datetime.utcnow().isoformat()
            supabase.table("decision_log").update(patch).eq("id", decision_id).eq("user_id", user_id).execute()
        return True
    except Exception as e:
        print(f"update_decision_outcome error: {e}")
        return False


def delete_decision(decision_id: str, user_id: str) -> bool:
    """Delete a single decision_log row owned by user_id."""
    if not supabase:
        return False
    try:
        supabase.table("decision_log").delete().eq("id", decision_id).eq("user_id", user_id).execute()
        return True
    except Exception as e:
        print(f"delete_decision error: {e}")
        return False


def update_decision(decision_id: str, user_id: str, decision_date, category: str, call: str, reasoning: str = "") -> bool:
    """Update the core fields (date, category, call, reasoning) of an existing decision.

    Filters by both ``id`` AND ``user_id`` so callers can only modify their own rows.
    """
    if not supabase:
        return False
    try:
        patch = {
            "decision_date": str(decision_date) if decision_date else None,
            "category": category,
            "call": call.strip(),
            "reasoning": reasoning.strip() or None,
        }
        supabase.table("decision_log").update(patch).eq("id", decision_id).eq("user_id", user_id).execute()
        return True
    except Exception as e:
        print(f"update_decision error: {e}")
        return False


def seed_decisions_if_empty(user_id: str) -> int:
    """If no decisions exist for this user, insert the seed set. Returns rows inserted."""
    if not supabase:
        return 0
    try:
        check = (
            supabase.table("decision_log")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )
        if (check.count or 0) > 0:
            return 0
        inserted = 0
        for seed in _DECISION_SEEDS:
            row = {
                "user_id": user_id,
                "decision_date": seed["decision_date"],
                "category": seed["category"],
                "call": seed["call"],
                "reasoning": seed.get("reasoning"),
                "outcome": seed["outcome"],
                "outcome_date": seed.get("outcome_date"),
                "outcome_notes": seed.get("outcome_notes"),
            }
            supabase.table("decision_log").insert(row).execute()
            inserted += 1
        return inserted
    except Exception as e:
        print(f"seed_decisions_if_empty error: {e}")
        return 0


# ── Playbook Quant Scoring ──────────────────────────────────────────────────────
def _score_single_ticker(api_key: str, secret_key: str, sym: str,
                         trade_date, feed: str = "iex"):
    """Fetch intraday bars for one ticker and return (sym, tcs, top_structure, struct_conf).

    Returns (sym, None, None, 0.0) on any data or calculation failure.
    struct_conf = probability (0–100) of the top structure prediction.
    """
    try:
        df = fetch_bars(api_key, secret_key, sym, trade_date, feed=feed)
        if df.empty or len(df) < 5:
            return sym, None, None, 0.0

        ib_high, ib_low = compute_initial_balance(df)
        if not ib_high or not ib_low:
            ib_high = float(df["high"].max())
            ib_low  = float(df["low"].min())

        bin_centers, vap, poc_price = compute_volume_profile(df, num_bins=50)
        tcs   = compute_tcs(df, ib_high, ib_low, poc_price)
        probs = compute_structure_probabilities(
            df, bin_centers, vap, ib_high, ib_low, poc_price
        )
        top_struct  = max(probs, key=probs.get) if probs else "—"
        struct_conf = round(float(probs.get(top_struct, 0.0)), 1) if probs else 0.0
        return sym, round(float(tcs), 1), top_struct, struct_conf
    except Exception:
        return sym, None, None, 0.0


# ── Discord Alert Engine ─────────────────────────────────────────────────────
_discord_alert_cache: dict = {}   # {ticker_YYYY-MM-DD: timestamp_float}


def send_discord_alert(
    webhook_url: str,
    ticker: str,
    price: float,
    rvol: float,
    tcs: float,
    structure: str,
    edge_score: float = 0.0,
) -> bool:
    """Send a high-conviction signal embed to a Discord webhook.

    Returns True on success, False on failure or if the webhook URL is blank.
    Callers should check the per-day de-dup cache before calling this.
    """
    if not webhook_url or not webhook_url.startswith("http"):
        return False

    rvol_str   = f"{rvol:.1f}x" if rvol else "—"
    price_str  = f"${price:.2f}" if price else "—"
    tcs_bar    = "🟩" * int(tcs // 20) + "⬜" * (5 - int(tcs // 20))
    edge_color = 0x4CAF50 if edge_score >= 85 else (0xFFA726 if edge_score >= 75 else 0x90CAF9)

    payload = {
        "username": "VolumeProfile Bot",
        "avatar_url": "https://cdn-icons-png.flaticon.com/512/2172/2172832.png",
        "embeds": [
            {
                "title": f"🚀 HIGH CONVICTION SIGNAL — ${ticker}",
                "color": edge_color,
                "fields": [
                    {"name": "💰 Price",       "value": price_str,           "inline": True},
                    {"name": "📊 TCS",         "value": f"{tcs:.0f}/100 {tcs_bar}", "inline": True},
                    {"name": "⚡ Edge Score",  "value": f"{edge_score:.0f}/100",    "inline": True},
                    {"name": "🔥 RVOL",        "value": rvol_str,            "inline": True},
                    {"name": "🏗️ Structure",   "value": structure or "—",    "inline": True},
                    {"name": "📅 Date",        "value": date.today().strftime("%b %d, %Y"), "inline": True},
                ],
                "footer": {"text": "Volume Profile Terminal · Auto-Alert"},
            }
        ],
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=5)
        return resp.status_code in (200, 204)
    except Exception:
        return False


def _maybe_discord_alert(
    webhook_url: str,
    ticker: str,
    price: float,
    rvol: float,
    tcs: float,
    structure: str,
    edge_score: float,
) -> None:
    """Fire a Discord alert for this ticker if it hasn't been alerted today."""
    if not webhook_url:
        return
    cache_key = f"{ticker}_{date.today().isoformat()}"
    if cache_key in _discord_alert_cache:
        return
    success = send_discord_alert(
        webhook_url=webhook_url,
        ticker=ticker,
        price=price,
        rvol=rvol,
        tcs=tcs,
        structure=structure,
        edge_score=edge_score,
    )
    if success:
        _discord_alert_cache[cache_key] = True
        # Prune old keys (keep only today's entries)
        today = date.today().isoformat()
        stale = [k for k in list(_discord_alert_cache) if not k.endswith(today)]
        for k in stale:
            _discord_alert_cache.pop(k, None)


_tg_playbook_cache: dict = {}   # {ticker_YYYY-MM-DD: True}


def _maybe_telegram_playbook_alert(
    ticker: str,
    price: float,
    rvol: float,
    tcs: float,
    structure: str,
    edge_score: float,
) -> None:
    """Fire a Telegram alert for a high-conviction Playbook signal (TCS≥80, Edge≥75).
    De-duped per ticker per day. Uses TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars.
    """
    import os as _os, requests as _req
    _token   = _os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    _chat_id = _os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not _token or not _chat_id:
        return

    _cache_key = f"{ticker}_{date.today().isoformat()}"
    if _cache_key in _tg_playbook_cache:
        return

    _price_str = f"${price:.2f}" if price else "—"
    _rvol_str  = f"{rvol:.1f}×" if rvol else "—"
    _tcs_bar   = "🟩" * int(tcs // 20) + "⬜" * (5 - int(tcs // 20))
    _edge_lbl  = "🔥 ELITE" if edge_score >= 85 else "⚡ HIGH"

    _msg = (
        f"🚀 <b>HIGH CONVICTION — {ticker}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price:      <b>{_price_str}</b>\n"
        f"📊 TCS:        <b>{tcs:.0f}/100</b>  {_tcs_bar}\n"
        f"{_edge_lbl} Edge Score: <b>{edge_score:.0f}/100</b>\n"
        f"🔥 RVOL:       <b>{_rvol_str}</b>\n"
        f"🏗️ Structure:  <b>{structure or '—'}</b>\n"
        f"📅 {date.today().strftime('%b %d, %Y')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Playbook signal — review before entry."
    )
    try:
        _resp = _req.post(
            f"https://api.telegram.org/bot{_token}/sendMessage",
            json={"chat_id": _chat_id, "text": _msg, "parse_mode": "HTML"},
            timeout=8,
        )
        if _resp.status_code == 200:
            _tg_playbook_cache[_cache_key] = True
            _today = date.today().isoformat()
            for _k in [k for k in list(_tg_playbook_cache) if not k.endswith(_today)]:
                _tg_playbook_cache.pop(_k, None)
    except Exception:
        pass


def validate_tg_bot_token(token: str) -> dict | None:
    """Call the Telegram getMe API to verify *token* is a live, working bot.

    Returns the bot info dict (keys: id, username, first_name, …) on success,
    or None on any failure (bad token, network error, etc.).
    """
    if not token:
        return None
    try:
        _resp = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=8,
        )
        _data = _resp.json()
        if _data.get("ok") and _data.get("result"):
            return _data["result"]
    except Exception:
        pass
    return None


def validate_discord_webhook(url: str) -> dict | None:
    """Send a GET request to a Discord webhook URL to verify it is live.

    Returns the webhook info dict (keys: id, name, channel_id, …) on success,
    or None on any failure (revoked webhook, bad URL, network error, etc.).
    """
    if not url:
        return None
    try:
        _resp = requests.get(url, timeout=8)
        if _resp.status_code == 200:
            _data = _resp.json()
            if _data.get("id"):
                return _data
    except Exception:
        pass
    return None


def send_divergence_alert(
    flagged_rows: list,
    threshold: float,
    tg_token: str = "",
    tg_chat_id: str = "",
    discord_webhook_url: str = "",
) -> dict:
    """Send a divergence alert with the flagged-tickers CSV to Telegram and/or Discord.

    Parameters
    ----------
    flagged_rows : list of dicts with keys ``Ticker``, ``Divergence Magnitude``,
                   ``Max Divergence label``
    threshold : the divergence threshold that was used
    tg_token / tg_chat_id : Telegram credentials (read from env if blank)
    discord_webhook_url : Discord webhook URL (read from env if blank)

    Returns
    -------
    dict with keys ``telegram`` and ``discord`` (True = sent, False = failed/skipped)
    """
    import io as _io

    tg_token    = (tg_token    or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    tg_chat_id  = (tg_chat_id  or os.environ.get("TELEGRAM_CHAT_ID",    "")).strip()
    discord_webhook_url = (
        discord_webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
    ).strip()

    n = len(flagged_rows)
    today_str = date.today().strftime("%b %d, %Y")

    csv_lines = ["Ticker,Divergence Magnitude,Max Divergence label"]
    for row in flagged_rows:
        csv_lines.append(
            f"{row['Ticker']},{row['Divergence Magnitude']},{row['Max Divergence label']}"
        )
    csv_bytes = "\n".join(csv_lines).encode("utf-8")

    results = {"telegram": False, "discord": False}

    if tg_token and tg_chat_id:
        _ticker_lines = "\n".join(
            f"• <b>{r['Ticker']}</b>  mag={r['Divergence Magnitude']}  "
            f"{r['Max Divergence label']}"
            for r in flagged_rows
        )
        _tg_msg = (
            f"⚠️ <b>DIVERGENCE ALERT — {n} ticker{'s' if n != 1 else ''} flagged</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Threshold: <b>{threshold:.2f}</b>   |   📅 {today_str}\n\n"
            f"{_ticker_lines}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Full list attached as CSV."
        )
        try:
            _doc_resp = requests.post(
                f"https://api.telegram.org/bot{tg_token}/sendDocument",
                data={"chat_id": tg_chat_id, "caption": _tg_msg, "parse_mode": "HTML"},
                files={"document": ("flagged_tickers.csv", _io.BytesIO(csv_bytes), "text/csv")},
                timeout=10,
            )
            results["telegram"] = _doc_resp.status_code == 200
        except Exception:
            results["telegram"] = False

    if discord_webhook_url and discord_webhook_url.startswith("http"):
        _ticker_value = "\n".join(
            f"`{r['Ticker']}` — mag {r['Divergence Magnitude']} — {r['Max Divergence label']}"
            for r in flagged_rows
        ) or "—"
        _embed_payload = {
            "username": "VolumeProfile Bot",
            "embeds": [
                {
                    "title": f"⚠️ Divergence Alert — {n} ticker{'s' if n != 1 else ''} flagged",
                    "color": 0xFFD600,
                    "fields": [
                        {"name": "Threshold", "value": f"{threshold:.2f}", "inline": True},
                        {"name": "Date",      "value": today_str,          "inline": True},
                        {"name": "Flagged tickers", "value": _ticker_value[:1024], "inline": False},
                    ],
                    "footer": {"text": "Full list in attached CSV  ·  Volume Profile Terminal"},
                }
            ],
        }
        try:
            _dc_resp = requests.post(
                discord_webhook_url,
                data={"payload_json": json.dumps(_embed_payload)},
                files={"files[0]": ("flagged_tickers.csv", _io.BytesIO(csv_bytes), "text/csv")},
                timeout=10,
            )
            results["discord"] = _dc_resp.status_code in (200, 204)
        except Exception:
            results["discord"] = False

    return results


def send_test_alert(
    tg_token: str = "",
    tg_chat_id: str = "",
    discord_webhook_url: str = "",
) -> dict:
    """Send a short test message to Telegram and/or Discord to verify the connection.

    Parameters
    ----------
    tg_token / tg_chat_id : Telegram credentials (read from env if blank)
    discord_webhook_url : Discord webhook URL

    Returns
    -------
    dict with keys ``telegram`` and ``discord`` (True = sent, False = failed/skipped)
    """
    tg_token = (tg_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
    tg_chat_id = (tg_chat_id or "").strip()
    discord_webhook_url = (discord_webhook_url or "").strip()

    results = {"telegram": False, "discord": False}

    if tg_token and tg_chat_id:
        _msg = (
            "✅ <b>EdgeIQ — Test Alert</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "EdgeIQ divergence alerts are now routed to this channel."
        )
        try:
            _resp = requests.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                data={"chat_id": tg_chat_id, "text": _msg, "parse_mode": "HTML"},
                timeout=10,
            )
            results["telegram"] = _resp.status_code == 200
        except Exception:
            results["telegram"] = False

    if discord_webhook_url and discord_webhook_url.startswith("https://discord.com/api/webhooks/"):
        _payload = {
            "username": "VolumeProfile Bot",
            "embeds": [
                {
                    "title": "✅ EdgeIQ — Test Alert",
                    "description": "EdgeIQ divergence alerts are now routed to this channel.",
                    "color": 0x00C853,
                }
            ],
        }
        try:
            _resp = requests.post(
                discord_webhook_url,
                json=_payload,
                timeout=10,
            )
            results["discord"] = _resp.status_code in (200, 204)
        except Exception:
            results["discord"] = False

    return results


def score_playbook_tickers(rows: list, api_key: str, secret_key: str,
                           feed: str = "iex", max_tickers: int = 20,
                           user_id: str = "",
                           discord_webhook_url: str = "") -> list:
    """Enrich Playbook rows with TCS, structure, and self-calibrating Edge Score.

    Edge Score (0–100) combines TCS, structure confidence, recent market
    environment, and false break rate — weights auto-calibrate from saved
    backtest history.
    """
    if not rows or not api_key or not secret_key:
        for row in rows:
            row.setdefault("tcs", None)
            row.setdefault("structure", "—")
            row.setdefault("edge_score", None)
        return rows

    # Pre-load adaptive weights + environment stats once for the whole batch
    weights  = compute_adaptive_weights(user_id)
    env_stat = get_recent_env_stats(user_id, days=5)

    # Roll back to most recent actual trading day (holiday-aware)
    trade_date = get_last_trading_day(api_key=api_key, secret_key=secret_key)

    subset = rows[:max_tickers]
    scored: dict = {}

    with ThreadPoolExecutor(max_workers=min(8, len(subset))) as executor:
        future_map = {
            executor.submit(
                _score_single_ticker, api_key, secret_key,
                r["ticker"], trade_date, feed
            ): r["ticker"]
            for r in subset
        }
        for future in as_completed(future_map):
            sym, tcs, structure, struct_conf = future.result()
            scored[sym] = (tcs, structure if structure else "—", struct_conf)

    for row in rows:
        sym = row["ticker"]
        if sym in scored:
            tcs, structure, struct_conf = scored[sym]
            row["tcs"]         = tcs
            row["structure"]   = structure
            row["struct_conf"] = struct_conf
            if tcs is not None:
                edge, breakdown = compute_edge_score(
                    tcs=tcs,
                    structure_conf=struct_conf,
                    env_long_rate=env_stat["long_rate"],
                    recent_false_brk_rate=env_stat["false_brk_rate"],
                    weights=weights,
                )
                row["edge_score"]     = edge
                row["edge_breakdown"] = breakdown
                # ── Telegram alert: TCS ≥ 80 and Edge Score ≥ 75 ────────────
                if tcs >= 80 and edge >= 75:
                    _maybe_telegram_playbook_alert(
                        ticker=sym,
                        price=float(row.get("price") or 0),
                        rvol=float(row.get("rvol") or 0),
                        tcs=tcs,
                        structure=structure,
                        edge_score=edge,
                    )
            else:
                row["edge_score"]     = None
                row["edge_breakdown"] = {}
        else:
            row["tcs"]         = None
            row["structure"]   = "—"
            row["struct_conf"] = 0.0
            row["edge_score"]  = None
            row["edge_breakdown"] = {}

    # Sort by edge score descending (None last)
    rows.sort(key=lambda r: r.get("edge_score") or -1, reverse=True)
    return rows


# ── Self-Calibrating Edge Score Engine ──────────────────────────────────────
_DEFAULT_EDGE_WEIGHTS = {
    "tcs":         0.35,
    "structure":   0.25,
    "environment": 0.25,
    "false_break": 0.15,
}


def compute_adaptive_weights(user_id: str = "") -> dict:
    """Load backtest history and compute data-calibrated signal weights.

    Requires at least 15 saved rows to calibrate. Falls back to defaults
    if there is insufficient data or Supabase is unavailable.

    Returns a dict with keys: tcs, structure, environment, false_break,
    rows_used (int), calibrated (bool).
    """
    df = load_backtest_sim_history(user_id)
    if df.empty:
        return {**_DEFAULT_EDGE_WEIGHTS, "rows_used": 0, "calibrated": False}

    try:
        # Deduplicate: keep only the most recent run for each (ticker, sim_date) pair
        # so replaying the same backtest day doesn't skew the weights
        df["sim_date"] = pd.to_datetime(df.get("sim_date", pd.NaT), errors="coerce")
        if "ticker" in df.columns and "sim_date" in df.columns:
            df = (df.sort_values("created_at", errors="ignore")
                    .drop_duplicates(subset=["ticker", "sim_date"], keep="last")
                    .reset_index(drop=True))

        if len(df) < 15:
            return {**_DEFAULT_EDGE_WEIGHTS, "rows_used": len(df), "calibrated": False}

        df["win_bin"] = (df["win_loss"] == "Win").astype(float)
        df["tcs_num"] = pd.to_numeric(df["tcs"], errors="coerce").fillna(0)

        # TCS correlation with wins (Pearson)
        tcs_corr = float(df["tcs_num"].corr(df["win_bin"]))
        if pd.isna(tcs_corr):
            tcs_corr = 0.0
        # Shift base weight by correlation signal, clamp to [0.15, 0.55]
        tcs_w = max(0.15, min(0.55, 0.35 + tcs_corr * 0.25))

        # Structure reliability: how well has the model been winning overall?
        overall_wr = float(df["win_bin"].mean())
        # Higher overall win rate → structure predictions are reliable → weight more
        struct_w = max(0.10, min(0.40, 0.25 + (overall_wr - 0.50) * 0.30))

        # Remaining weight split 60/40 between environment and false break
        remaining = max(0.10, 1.0 - tcs_w - struct_w)
        env_w = round(remaining * 0.60, 3)
        fb_w  = round(remaining * 0.40, 3)

        return {
            "tcs":         round(tcs_w, 3),
            "structure":   round(struct_w, 3),
            "environment": env_w,
            "false_break": fb_w,
            "rows_used":   len(df),
            "calibrated":  True,
        }
    except Exception:
        return {**_DEFAULT_EDGE_WEIGHTS, "rows_used": len(df), "calibrated": False}


def get_recent_env_stats(user_id: str = "", days: int = 5) -> dict:
    """Get recent market environment stats from saved backtest history.

    Returns dict with:
    - long_rate (float 0–100): % of recent setups that went bullish
    - false_brk_rate (float 0–100): % of IB breaks that reversed within 30 min
    - rows_used (int): how many rows were used
    """
    df = load_backtest_sim_history(user_id)
    if df.empty:
        return {"long_rate": 50.0, "false_brk_rate": 0.0, "rows_used": 0}

    try:
        df["sim_date"] = pd.to_datetime(df["sim_date"], errors="coerce")
        # Deduplicate replays: one row per (ticker, sim_date), most recent run
        if "ticker" in df.columns and "sim_date" in df.columns:
            df = (df.sort_values("created_at", errors="ignore")
                    .drop_duplicates(subset=["ticker", "sim_date"], keep="last")
                    .reset_index(drop=True))
        cutoff = pd.Timestamp.now(tz="UTC").tz_localize(None) - pd.Timedelta(days=days)
        recent = df[df["sim_date"] >= cutoff]
        if len(recent) < 10:
            recent = df.tail(50)   # fallback: last 50 rows regardless of date

        bull  = (recent["actual_outcome"] == "Bullish Break").sum()
        total = len(recent)
        long_rate = round(float(bull) / total * 100, 1) if total else 50.0

        fb_up   = recent["false_break_up"].fillna(False).astype(bool).sum()
        fb_down = recent["false_break_down"].fillna(False).astype(bool).sum()
        breakable = int((recent["actual_outcome"] != "Range-Bound").sum())
        false_brk_rate = (round((int(fb_up) + int(fb_down)) / breakable * 100, 1)
                          if breakable else 0.0)

        return {
            "long_rate":      long_rate,
            "false_brk_rate": false_brk_rate,
            "rows_used":      total,
        }
    except Exception:
        return {"long_rate": 50.0, "false_brk_rate": 0.0, "rows_used": 0}


def compute_edge_score(
    tcs: float,
    structure_conf: float,
    env_long_rate: float,
    recent_false_brk_rate: float,
    weights: dict,
) -> tuple:
    """Compute a composite 0–100 Edge Score for a live setup.

    Returns (score: float, breakdown: dict).

    Inputs (all 0–100):
    - tcs                  : TCS momentum score
    - structure_conf       : model's confidence in its top structure pick
    - env_long_rate        : % of recent setups that went bullish (market environment)
    - recent_false_brk_rate: % of recent IB breaks that faked out (lower = cleaner tape)
    """
    w = weights

    tcs_pts    = min(100.0, max(0.0, tcs))            * w.get("tcs",         0.35)
    struct_pts = min(100.0, max(0.0, structure_conf))  * w.get("structure",   0.25)
    env_pts    = min(100.0, max(0.0, env_long_rate))   * w.get("environment", 0.25)
    fb_clean   = max(0.0, 100.0 - recent_false_brk_rate)
    fb_pts     = fb_clean                              * w.get("false_break", 0.15)

    score = round(min(100.0, tcs_pts + struct_pts + env_pts + fb_pts), 1)
    return score, {
        "tcs_pts":    round(tcs_pts,    1),
        "struct_pts": round(struct_pts, 1),
        "env_pts":    round(env_pts,    1),
        "fb_pts":     round(fb_pts,     1),
        "total":      score,
    }


# ── Backtest Structure Analytics ─────────────────────────────────────────────
def compute_backtest_structure_stats(user_id: str = "") -> "pd.DataFrame":
    """Compute win rate, avg follow-through, and false break rate by structure type.

    Uses saved backtest_sim_runs (deduplicated by ticker+date) so the stats
    reflect unique setups only, not replay noise.

    Returns a DataFrame with columns:
      structure, trades, wins, win_rate, avg_follow_thru, false_brk_rate
    Sorted by win_rate descending.
    """
    df = load_backtest_sim_history(user_id)
    if df.empty:
        return pd.DataFrame(columns=[
            "structure", "trades", "wins", "win_rate", "avg_follow_thru", "false_brk_rate"
        ])

    try:
        df["sim_date"] = pd.to_datetime(df.get("sim_date", pd.NaT), errors="coerce")
        if "ticker" in df.columns and "sim_date" in df.columns:
            df = (df.sort_values("created_at", errors="ignore")
                    .drop_duplicates(subset=["ticker", "sim_date"], keep="last")
                    .reset_index(drop=True))

        if "predicted_structure" not in df.columns:
            return pd.DataFrame()

        df["win_bin"]   = (df["win_loss"] == "Win").astype(int)
        df["ft_num"]    = pd.to_numeric(df.get("follow_thru_pct", pd.Series(dtype=float)),
                                        errors="coerce")
        fb_up   = df.get("false_break_up",   pd.Series([False] * len(df))).fillna(False).astype(bool)
        fb_down = df.get("false_break_down",  pd.Series([False] * len(df))).fillna(False).astype(bool)
        df["false_brk"] = (fb_up | fb_down).astype(int)

        grp = df.groupby("predicted_structure", as_index=False).agg(
            trades        = ("win_bin",    "count"),
            wins          = ("win_bin",    "sum"),
            avg_follow_thru = ("ft_num",  lambda x: round(x.mean(), 2) if x.notna().any() else 0.0),
            false_brks    = ("false_brk", "sum"),
        )
        grp["win_rate"]       = (grp["wins"] / grp["trades"] * 100).round(1)
        grp["false_brk_rate"] = (grp["false_brks"] / grp["trades"] * 100).round(1)
        grp = grp.rename(columns={"predicted_structure": "structure"})
        grp = grp.sort_values("win_rate", ascending=False).reset_index(drop=True)
        return grp[["structure", "trades", "wins", "win_rate", "avg_follow_thru", "false_brk_rate"]]
    except Exception:
        return pd.DataFrame()


# ── Finviz Watchlist Fetcher ───────────────────────────────────────────────────
def fetch_finviz_watchlist(
    change_min_pct: float = 3.0,
    float_max_m:    float = 100.0,
    price_min:      float = 1.0,
    price_max:      float = 20.0,
    max_tickers:    int   = 100,
    avg_vol_min_k:  int   = 1000,
    extra_filters:  list  = None,
) -> list:
    """Screen for the daily watchlist using Yahoo Finance (Finviz migrated to
    JS-only rendering, making HTML scraping impossible).

    Pulls from Yahoo Finance predefined screens (day_gainers, most_actives,
    aggressive_small_caps) then applies the same price / change / volume
    filters that the original Finviz function used.

    extra_filters hint mapping (kept for call-site compatibility):
      ['ta_sma20_pa', 'ta_sma50_pa'] → trend-continuation pass (most_actives)
      ['sh_short_o15']               → squeeze pass (aggressive_small_caps)
      None                           → gap-of-day pass (day_gainers)

    Returns a deduplicated list of uppercase ticker strings (up to max_tickers).
    Returns [] on any error so the bot falls back to its stored watchlist.
    """
    import requests as _req

    _sess = _req.Session()
    _sess.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    })

    _YF_BASE = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
    _avg_vol_min = avg_vol_min_k * 1_000

    # Determine which Yahoo screen to use based on caller hints
    _extra = extra_filters or []
    if any("sma" in f for f in _extra):
        # Trend-continuation pass → most actives (above moving averages)
        _scr_ids = ["most_actives", "day_gainers", "small_cap_gainers"]
    elif any("short" in f for f in _extra):
        # Short-squeeze pass → aggressive small caps + small cap gainers
        _scr_ids = ["aggressive_small_caps", "small_cap_gainers", "day_gainers"]
    else:
        # Gap-of-day default pass
        _scr_ids = ["day_gainers", "small_cap_gainers", "most_actives"]

    _seen: set  = set()
    _tickers: list = []

    for _scr_id in _scr_ids:
        if len(_tickers) >= max_tickers:
            break
        try:
            _resp = _sess.get(
                _YF_BASE,
                params={
                    "formatted": "false",
                    "lang": "en-US",
                    "region": "US",
                    "scrIds": _scr_id,
                    "start": 0,
                    "count": 200,
                },
                timeout=12,
            )
            _resp.raise_for_status()
            _data   = _resp.json()
            _result = _data.get("finance", {}).get("result", [])
            if not _result:
                continue
            _quotes = _result[0].get("quotes", [])

            for _q in _quotes:
                _sym  = (_q.get("symbol") or "").strip().upper()
                _chg  = _q.get("regularMarketChangePercent") or 0.0
                _price = _q.get("regularMarketPrice") or 0.0
                _avol = _q.get("averageDailyVolume10Day") or _q.get("averageDailyVolume3Month") or 0

                # Apply same filters as the original Finviz screener
                if not _sym or not _sym.isalpha() or len(_sym) > 5:
                    continue
                if _price < price_min or _price > price_max:
                    continue
                if _chg < change_min_pct:
                    continue
                if _avol < _avg_vol_min:
                    continue
                if _sym in _seen:
                    continue

                _seen.add(_sym)
                _tickers.append(_sym)
                if len(_tickers) >= max_tickers:
                    break

            time.sleep(0.3)

        except Exception as _e:
            logging.warning(f"Yahoo Finance screener fetch error ({_scr_id}): {_e}")

    logging.info(f"Finviz watchlist: fetched {len(_tickers)} tickers")
    return _tickers[:max_tickers]


def fetch_premarket_gappers(
    api_key:      str,
    secret_key:   str,
    min_gap_pct:  float = 15.0,
    price_min:    float = 1.0,
    price_max:    float = 50.0,
    min_pm_vol:   int   = 100_000,
    top:          int   = 100,
) -> tuple[list, str]:
    """Scan Alpaca SIP data for pre-market gappers (runs ~9:10 AM ET).

    Unlike the Finviz watchlist this has NO historical avg-vol requirement,
    catching dormant stocks that suddenly have a catalyst (e.g. BIRD +149%).
    Uses the /v1beta1/screener endpoints which return live pre-market data
    when called before 9:30 AM on the SIP feed.

    Filters:
      - Price $1–$50 (wider than Finviz's $20 cap — catches large gappers)
      - Gap % ≥ min_gap_pct (default 15%)
      - Pre-market volume ≥ min_pm_vol (default 100K — rules out noise)
      - US-listed common stocks only (Alpaca screener already filters this)

    Returns (list of result dicts sorted by |gap_pct| desc, error_str).
    Each dict: {ticker, price, gap_pct, pm_vol, source}
    """
    import requests as _req
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}
    base    = "https://data.alpaca.markets/v1beta1/screener/stocks"
    pool    = {}
    errors  = []

    endpoints = [
        (f"{base}/most-actives", {"by": "volume", "top": top}, "most_actives"),
        (f"{base}/movers",       {"market_type": "stocks", "top": top}, "gainers"),
    ]

    for url, params, key in endpoints:
        try:
            r = _req.get(url, params=params, headers=headers, timeout=10)
            if r.status_code == 200:
                for item in r.json().get(key, []):
                    sym        = str(item.get("symbol", "")).upper()
                    price      = float(item.get("price", 0) or 0)
                    change_pct = float(item.get("percent_change", 0) or 0)
                    volume     = int(item.get("volume", 0) or 0)

                    if not sym:
                        continue
                    if not (price_min <= price <= price_max):
                        continue
                    if abs(change_pct) < min_gap_pct:
                        continue
                    if volume < min_pm_vol:
                        continue

                    if sym in pool:
                        pool[sym]["source"] = "Active+Gainer"
                        pool[sym]["pm_vol"] = max(pool[sym]["pm_vol"], volume)
                    else:
                        pool[sym] = {
                            "ticker":   sym,
                            "price":    round(price, 2),
                            "gap_pct":  round(change_pct, 2),
                            "pm_vol":   volume,
                            "source":   "PreMarket",
                        }
            elif r.status_code not in (400, 422):
                errors.append(f"{key} HTTP {r.status_code}")
        except Exception as exc:
            errors.append(f"{key}: {exc}")

    results = sorted(pool.values(), key=lambda x: abs(x["gap_pct"]), reverse=True)
    return results, "; ".join(errors) if errors else ""


# ── Watchlist Persistence ─────────────────────────────────────────────────────
def save_watchlist(tickers: list, user_id: str = "") -> bool:
    """Upsert a user's custom watchlist to Supabase (table: user_watchlist).

    Stores one row per user with a JSON-encoded list of tickers.
    Returns True on success, False on failure.
    """
    if not supabase:
        return False
    try:
        import json as _json
        payload = {
            "user_id":   user_id or "anonymous",
            "tickers":   _json.dumps([t.strip().upper() for t in tickers if t.strip()]),
            "updated_at": datetime.utcnow().isoformat(),
        }
        supabase.table("user_watchlist").upsert(payload, on_conflict="user_id").execute()
        return True
    except Exception:
        return False


def load_watchlist(user_id: str = "") -> list:
    """Load a user's saved watchlist from Supabase.

    Returns a list of ticker strings, or [] if not found / table missing.
    """
    if not supabase:
        return []
    try:
        import json as _json
        uid = user_id or "anonymous"
        res = (supabase.table("user_watchlist")
               .select("tickers")
               .eq("user_id", uid)
               .limit(1)
               .execute())
        if res.data:
            raw = res.data[0].get("tickers", "[]")
            return _json.loads(raw) if isinstance(raw, str) else list(raw)
        return []
    except Exception:
        return []


# ── Daily Scan Log ────────────────────────────────────────────────────────────

_DAILY_SCAN_LOG_DDL = """
CREATE TABLE IF NOT EXISTS daily_scan_log (
    id           BIGSERIAL PRIMARY KEY,
    scan_date    DATE        NOT NULL,
    ticker       VARCHAR(10) NOT NULL,
    screener_pass VARCHAR(20) NOT NULL,
    slot         VARCHAR(10) NOT NULL DEFAULT 'morning',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS daily_scan_log_date_idx ON daily_scan_log (scan_date DESC);
"""


def ensure_daily_scan_log_table() -> bool:
    """Create daily_scan_log table if it doesn't exist. Returns True when ready."""
    if not supabase:
        return False
    try:
        supabase.table("daily_scan_log").select("id").limit(1).execute()
        return True
    except Exception as e:
        err = str(e).lower()
        if "404" in err or "relation" in err or "does not exist" in err or "not found" in err:
            try:
                supabase.rpc("exec_sql", {"query": _DAILY_SCAN_LOG_DDL}).execute()
                return True
            except Exception as e2:
                logging.warning(f"ensure_daily_scan_log_table create error: {e2}")
                return False
        logging.warning(f"ensure_daily_scan_log_table check error: {e}")
        return False


def save_daily_scan_log(
    gap_tickers: list,
    trend_tickers: list,
    squeeze_tickers: list,
    scan_date=None,
    slot: str = "morning",
) -> bool:
    """Persist the morning/midday Finviz scan results to daily_scan_log.

    Inserts one row per ticker tagged with its screener pass (gap/trend/squeeze).
    Existing rows for the same (scan_date, slot) are deleted first so a re-run
    doesn't double-insert.

    Returns True on success, False on any error.
    """
    if not supabase:
        return False
    try:
        import datetime as _dt
        if scan_date is None:
            scan_date = _dt.date.today()
        scan_date_str = scan_date.isoformat() if hasattr(scan_date, "isoformat") else str(scan_date)

        supabase.table("daily_scan_log").delete().eq("scan_date", scan_date_str).eq("slot", slot).execute()

        rows = []
        seen: set = set()
        for t in gap_tickers:
            t = t.strip().upper()
            if t and t not in seen:
                rows.append({"scan_date": scan_date_str, "ticker": t, "screener_pass": "gap", "slot": slot})
                seen.add(t)
        for t in trend_tickers:
            t = t.strip().upper()
            if t and t not in seen:
                rows.append({"scan_date": scan_date_str, "ticker": t, "screener_pass": "trend", "slot": slot})
                seen.add(t)
        for t in squeeze_tickers:
            t = t.strip().upper()
            if t and t not in seen:
                rows.append({"scan_date": scan_date_str, "ticker": t, "screener_pass": "squeeze", "slot": slot})
                seen.add(t)

        if rows:
            supabase.table("daily_scan_log").insert(rows).execute()
        logging.info(f"[save_daily_scan_log] {scan_date_str}/{slot}: {len(rows)} tickers saved")
        return True
    except Exception as e:
        logging.warning(f"save_daily_scan_log error: {e}")
        return False


def get_earliest_scan_date():
    """Return the earliest date in daily_scan_log, or None if unavailable."""
    if not supabase:
        return None
    try:
        import datetime as _dt
        res = (supabase.table("daily_scan_log")
               .select("scan_date")
               .order("scan_date", desc=False)
               .limit(1)
               .execute())
        if res.data and res.data[0].get("scan_date"):
            raw = res.data[0]["scan_date"]
            if isinstance(raw, str):
                return _dt.date.fromisoformat(raw[:10])
            return raw
        return None
    except Exception as e:
        logging.warning(f"get_earliest_scan_date error: {e}")
        return None


def load_daily_scan_log(scan_date=None) -> dict:
    """Load daily_scan_log rows for a given date.

    Returns a dict:
      {
        "gap":     [list of tickers],
        "trend":   [list of tickers],
        "squeeze": [list of tickers],
        "all":     [all tickers deduped],
        "total":   int,
      }
    Returns empty structure on error or no data.
    """
    _empty = {"gap": [], "trend": [], "squeeze": [], "all": [], "total": 0}
    if not supabase:
        return _empty
    try:
        import datetime as _dt
        if scan_date is None:
            scan_date = _dt.date.today()
        scan_date_str = scan_date.isoformat() if hasattr(scan_date, "isoformat") else str(scan_date)

        res = (supabase.table("daily_scan_log")
               .select("ticker,screener_pass")
               .eq("scan_date", scan_date_str)
               .execute())
        if not res.data:
            return _empty

        gap, trend, squeeze, seen = [], [], [], set()
        for row in res.data:
            t = row.get("ticker", "").strip().upper()
            p = row.get("screener_pass", "gap")
            if not t or t in seen:
                continue
            seen.add(t)
            if p == "trend":
                trend.append(t)
            elif p == "squeeze":
                squeeze.append(t)
            else:
                gap.append(t)

        all_tickers = gap + trend + squeeze
        return {"gap": gap, "trend": trend, "squeeze": squeeze, "all": all_tickers, "total": len(all_tickers)}
    except Exception as e:
        logging.warning(f"load_daily_scan_log error: {e}")
        return _empty


# ── End-of-Day Review Notes ───────────────────────────────────────────────────

def _compress_image_b64(file_bytes: bytes, max_px: int = 900) -> str:
    """Resize image to max_px on longest side and return as base64 JPEG string."""
    from PIL import Image as _Image
    import io as _io, base64 as _b64
    img = _Image.open(_io.BytesIO(file_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_px:
        scale = max_px / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), _Image.LANCZOS)
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=75, optimize=True)
    return _b64.b64encode(buf.getvalue()).decode()


_EOD_BACKUP = os.path.join(os.path.dirname(__file__), ".local", "eod_notes_backup.json")


def _load_local_eod_backup() -> list:
    """Read the local JSON backup file. Returns list of note dicts."""
    import json as _json
    try:
        if os.path.exists(_EOD_BACKUP):
            with open(_EOD_BACKUP, "r") as _f:
                data = _json.load(_f)
                return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _save_local_eod_backup(note: dict) -> None:
    """Upsert a note dict into the local backup (keyed by user_id + note_date + watch_tickers).

    Each ticker on each date is a fully independent entry — no merging.
    """
    import json as _json
    rows = _load_local_eod_backup()
    key = (note.get("user_id", ""), note.get("note_date", ""), note.get("watch_tickers", "").strip())
    rows = [r for r in rows
            if (r.get("user_id", ""), r.get("note_date", ""), r.get("watch_tickers", "").strip()) != key]
    rows.append(note)
    rows.sort(key=lambda r: (r.get("note_date", ""), r.get("watch_tickers", "")), reverse=True)
    os.makedirs(os.path.dirname(_EOD_BACKUP), exist_ok=True)
    with open(_EOD_BACKUP, "w") as _f:
        _json.dump(rows, _f)


def save_eod_note(note_date, notes: str, watch_tickers: str,
                  images_b64: list, user_id: str = "") -> tuple:
    """Upsert an end-of-day review note.

    Tries Supabase first; always also writes to local backup so data is
    never lost during outages.

    Returns (ok: bool, source: str) where source is 'supabase', 'local', or 'error'.
    """
    import json as _json
    uid  = user_id or "anonymous"
    nd   = str(note_date)
    wt   = watch_tickers.strip()
    now  = datetime.utcnow().isoformat()

    payload = {
        "user_id":       uid,
        "note_date":     nd,
        "notes":         notes.strip(),
        "watch_tickers": wt,
        "images":        images_b64,
        "updated_at":    now,
    }

    # Always persist locally first — never lost
    _save_local_eod_backup(payload)
    print(f"save_eod_note local backup: {nd} | {wt} | {len(images_b64)} images")

    # Then try Supabase using DELETE + INSERT (avoids any ON CONFLICT constraint issues)
    if supabase:
        try:
            sb_payload = {
                "user_id":       uid,
                "note_date":     nd,
                "notes":         notes.strip(),
                "watch_tickers": wt,
                "images":        _json.dumps(images_b64),
                "updated_at":    now,
            }
            # Delete existing row for this user+date+ticker, then insert fresh
            supabase.table("eod_notes").delete()\
                .eq("user_id", uid)\
                .eq("note_date", nd)\
                .eq("watch_tickers", wt)\
                .execute()
            supabase.table("eod_notes").insert(sb_payload).execute()
            print(f"save_eod_note Supabase OK: {nd} | {wt}")
            return True, "supabase"
        except Exception as e:
            print(f"save_eod_note Supabase error (local backup kept): {e}")
            return True, "local"

    return True, "local"


def delete_eod_note(note_date, watch_tickers: str, user_id: str = "") -> bool:
    """Delete a specific EOD note from both Supabase and local backup."""
    import json as _json
    uid = user_id or "anonymous"
    nd  = str(note_date)
    wt  = watch_tickers.strip()

    # Remove from local backup
    all_local = _load_local_eod_backup()
    filtered  = [r for r in all_local
                 if not (r.get("user_id") == uid
                         and str(r.get("note_date", "")) == nd
                         and r.get("watch_tickers", "").strip() == wt)]
    if len(filtered) < len(all_local):
        try:
            os.makedirs(os.path.dirname(_EOD_BACKUP), exist_ok=True)
            with open(_EOD_BACKUP, "w") as _ff:
                _json.dump(filtered, _ff)
        except Exception:
            pass

    # Remove from Supabase
    if supabase:
        try:
            supabase.table("eod_notes").delete()\
                .eq("user_id", uid)\
                .eq("note_date", nd)\
                .eq("watch_tickers", wt)\
                .execute()
        except Exception as e:
            print(f"delete_eod_note error: {e}")
    return True


def _sync_local_to_supabase(user_id: str = "") -> int:
    """Push local notes to Supabase using DELETE+INSERT. Returns count synced."""
    if not supabase:
        return 0
    import json as _json
    uid = user_id or "anonymous"
    local = [r for r in _load_local_eod_backup() if r.get("user_id") == uid]
    synced = 0
    for note in local:
        try:
            nd = str(note.get("note_date", ""))
            wt = note.get("watch_tickers", "").strip()
            sb = {
                "user_id":       uid,
                "note_date":     nd,
                "notes":         note.get("notes", ""),
                "watch_tickers": wt,
                "images":        _json.dumps(note.get("images", [])),
                "updated_at":    note.get("updated_at", datetime.utcnow().isoformat()),
            }
            supabase.table("eod_notes").delete()\
                .eq("user_id", uid).eq("note_date", nd).eq("watch_tickers", wt)\
                .execute()
            supabase.table("eod_notes").insert(sb).execute()
            synced += 1
        except Exception:
            pass
    return synced


def load_eod_notes(user_id: str = "", limit: int = 60) -> list:
    """Load EOD review notes — merges Supabase + local backup, newest first.

    Supabase records win on conflicts. Local-only records are included and
    auto-synced to Supabase in the background when it's reachable.
    """
    import json as _json
    uid = user_id or "anonymous"

    # Load local backup — include both uid-specific AND 'anonymous' entries (migration safety)
    all_local = _load_local_eod_backup()
    local_rows = [r for r in all_local
                  if r.get("user_id") == uid or r.get("user_id") == "anonymous"]
    # Re-stamp anonymous entries with the real uid so future syncs are correct
    for r in local_rows:
        if r.get("user_id") == "anonymous" and uid and uid != "anonymous":
            r["user_id"] = uid
    # Persist the re-stamped backup
    if any(r.get("user_id") == "anonymous" for r in all_local) and uid and uid != "anonymous":
        non_anon = [r for r in all_local if r.get("user_id") != "anonymous"]
        updated  = non_anon + local_rows
        try:
            import json as _jj
            os.makedirs(os.path.dirname(_EOD_BACKUP), exist_ok=True)
            with open(_EOD_BACKUP, "w") as _ff:
                _jj.dump(updated, _ff)
        except Exception:
            pass

    sb_rows = []
    sb_ok = False
    if supabase:
        try:
            res = (supabase.table("eod_notes")
                   .select("note_date,notes,watch_tickers,images,updated_at")
                   .eq("user_id", uid)
                   .order("note_date", desc=True)
                   .limit(limit)
                   .execute())
            sb_ok = True
            for r in (res.data or []):
                val = r.get("images", "[]")
                if isinstance(val, str):
                    try: val = _json.loads(val)
                    except: val = []
                r["images"] = val
                r.setdefault("outcome", {})
                sb_rows.append(r)
        except Exception as e:
            print(f"load_eod_notes Supabase error: {e}")

    if sb_ok:
        # Merge by (note_date, watch_tickers) — whichever version has the newer
        # updated_at wins.  This means a locally-saved entry (with images) beats
        # a stale Supabase row even when Supabase successfully loaded.
        _merged_dict: dict = {}
        for _r in sb_rows:
            _k = (str(_r.get("note_date", "")), _r.get("watch_tickers", "").strip())
            _merged_dict[_k] = _r
        _local_only_keys = []
        for _r in local_rows:
            _k = (str(_r.get("note_date", "")), _r.get("watch_tickers", "").strip())
            if _k in _merged_dict:
                # Both exist — prefer whichever is newer
                _local_ts = str(_r.get("updated_at", ""))
                _sb_ts    = str(_merged_dict[_k].get("updated_at", ""))
                if _local_ts > _sb_ts:
                    _merged_dict[_k] = _r  # local is newer (e.g. has images)
            else:
                _merged_dict[_k] = _r
                _local_only_keys.append(_k)
        merged = list(_merged_dict.values())
        # Auto-sync local-only entries to Supabase quietly
        if _local_only_keys:
            try:
                _sync_local_to_supabase(uid)
            except Exception:
                pass
    else:
        # Supabase down — return local backup only
        merged = local_rows

    merged.sort(key=lambda r: (r.get("note_date", ""), r.get("watch_tickers", "")), reverse=True)
    return merged[:limit]


def enrich_eod_from_journal(eod_notes: list, journal_df) -> list:
    """Merge quantitative journal data into EOD notes without duplication.

    For each EOD note, scans `journal_df` for a matching (ticker, date) entry.
    When found:
      - EOD note keeps its narrative text, images, and outcome (it is primary)
      - TCS, RVOL, IB high/low, structure are pulled from the journal row if
        the EOD note doesn't already carry them
      - A `_journal_ctx` dict is attached to the EOD note for display/analytics:
          {ticker: {tcs, rvol, ib_high, ib_low, structure, grade}}

    This prevents double-counting: the same trade is represented once, combining
    the qualitative depth of the EOD note with the quantitative precision of the
    journal row.  Analytics (win rates, brain calibration) should prefer this
    merged record over either source alone.
    """
    if not eod_notes or journal_df is None or journal_df.empty:
        return eod_notes

    import pandas as _pd

    # Build lookup: (ticker_upper, date_str) → journal row dict
    _jlookup: dict = {}
    for _, _jr in journal_df.iterrows():
        _tk = str(_jr.get("ticker", "")).upper().strip()
        _ts = str(_jr.get("timestamp", ""))[:10]
        if _tk and _ts:
            _jlookup[(_tk, _ts)] = _jr.to_dict()

    enriched = []
    for note in eod_notes:
        note = dict(note)  # copy — never mutate the original
        _nd  = str(note.get("note_date", ""))[:10]
        _wt  = str(note.get("watch_tickers", ""))
        _ctx: dict = {}

        for _tk_raw in [t.strip().upper() for t in _wt.split(",") if t.strip()]:
            _jrow = _jlookup.get((_tk_raw, _nd))
            if not _jrow:
                continue

            _entry: dict = {}
            for _field in ("tcs", "rvol", "ib_high", "ib_low", "structure", "grade"):
                _val = _jrow.get(_field)
                if _val is not None and str(_val) not in ("", "nan", "None"):
                    _entry[_field] = _val
            if _entry:
                _ctx[_tk_raw] = _entry

        if _ctx:
            note["_journal_ctx"] = _ctx
        enriched.append(note)

    return enriched


# ── EOD Prediction Verification ───────────────────────────────────────────────

def get_next_trading_day(after_date, api_key: str = "", secret_key: str = ""):
    """Return the first trading day strictly after `after_date`."""
    from datetime import timedelta
    candidate = after_date + timedelta(days=1)
    for _ in range(10):
        if is_trading_day(candidate):
            return candidate
        candidate += timedelta(days=1)
    return candidate


def verify_eod_predictions(note_date, watch_tickers_str: str, notes_text: str,
                           api_key: str, secret_key: str) -> dict:
    """Fetch next trading day's OHLC for each watched ticker and check if
    price levels mentioned in notes were touched.

    Returns dict keyed by ticker:
        {next_date, open, high, low, close,
         levels_above: [...], levels_below: [...],
         above_hit: bool, below_hit: bool}
    """
    import re as _re
    from datetime import date as _date

    if isinstance(note_date, str):
        note_date = _date.fromisoformat(note_date)

    next_day = get_next_trading_day(note_date, api_key, secret_key)

    # Parse tickers
    raw_tickers = [t.strip().upper() for t in _re.split(r"[,\s]+", watch_tickers_str) if t.strip()]

    # Parse price levels from notes (global — apply to all tickers for now)
    above_levels = [float(v.replace("$", "")) for v in
                    _re.findall(r"[Pp]rice\s+[Aa]bove\s+([\$]?[\d\.]+)", notes_text)]
    below_levels = [float(v.replace("$", "")) for v in
                    _re.findall(r"[Pp]rice\s+[Bb]elow\s+([\$]?[\d\.]+)", notes_text)]

    results = {}
    for ticker in raw_tickers:
        try:
            bars = fetch_bars(api_key, secret_key, ticker, next_day)
            if bars.empty:
                results[ticker] = {"next_date": str(next_day), "no_data": True,
                                   "levels_above": above_levels,
                                   "levels_below": below_levels}
                continue
            day_open  = float(bars["open"].iloc[0])
            day_high  = float(bars["high"].max())
            day_low   = float(bars["low"].min())
            day_close = float(bars["close"].iloc[-1])
            above_hit = any(day_high >= lv for lv in above_levels) if above_levels else None
            below_hit = any(day_low  <= lv for lv in below_levels) if below_levels else None
            results[ticker] = {
                "next_date":    str(next_day),
                "open":         round(day_open, 4),
                "high":         round(day_high, 4),
                "low":          round(day_low, 4),
                "close":        round(day_close, 4),
                "levels_above": above_levels,
                "levels_below": below_levels,
                "above_hit":    above_hit,
                "below_hit":    below_hit,
                "no_data":      False,
            }
        except Exception as e:
            results[ticker] = {"next_date": str(next_day), "error": str(e),
                               "levels_above": above_levels,
                               "levels_below": below_levels}
    return results


def save_eod_outcome(note_date, outcome: dict, user_id: str = "") -> bool:
    """Persist the verification outcome into eod_notes.outcome column."""
    if not supabase:
        return False
    try:
        import json as _json
        supabase.table("eod_notes").update(
            {"outcome": _json.dumps(outcome),
             "updated_at": datetime.utcnow().isoformat()}
        ).eq("user_id", user_id or "anonymous").eq("note_date", str(note_date)).execute()
        return True
    except Exception as e:
        print(f"save_eod_outcome error: {e}")
        return False


# ── Watchlist Prediction Engine ───────────────────────────────────────────────

def save_watchlist_predictions(predictions: list, user_id: str = "") -> bool:
    """Upsert batch structure+edge predictions for the user's watchlist.

    predictions: list of dicts with base keys:
        ticker, pred_date, predicted_structure, tcs, edge_score
    Optional setup brief keys (stored when present; ignored if schema not migrated):
        entry_zone_low, entry_zone_high, entry_trigger, stop_level,
        targets, pattern, pattern_neckline, win_rate_pct,
        win_rate_context, confidence_label
    One row per (user_id, ticker, pred_date) — safe to re-run same day.
    """
    import json as _json
    if not supabase or not predictions:
        return False

    def _build_row(p, include_brief: bool) -> dict:
        row = {
            "user_id":             user_id or "anonymous",
            "ticker":              str(p.get("ticker", "")).upper().strip(),
            "pred_date":           str(p.get("pred_date", date.today())),
            "predicted_structure": p.get("predicted_structure") or "—",
            "tcs":                 float(p.get("tcs") or 0),
            "edge_score":          float(p.get("edge_score") or 0),
            "verified":            False,
            "actual_structure":    "",
            "correct":             "",
        }
        if include_brief:
            targets_raw = p.get("targets")
            row["entry_zone_low"]   = p.get("entry_zone_low")
            row["entry_zone_high"]  = p.get("entry_zone_high")
            row["entry_trigger"]    = p.get("entry_trigger") or ""
            row["stop_level"]       = p.get("stop_level")
            row["targets"]          = (_json.dumps(targets_raw)
                                       if isinstance(targets_raw, list) else None)
            row["pattern"]          = p.get("pattern") or ""
            row["pattern_neckline"] = p.get("pattern_neckline")
            row["win_rate_pct"]     = p.get("win_rate_pct")
            row["win_rate_context"] = p.get("win_rate_context") or ""
            row["confidence_label"] = p.get("confidence_label") or "LOW"
            if p.get("rvol") is not None:
                row["rvol"] = round(float(p["rvol"]), 2)
            if p.get("gap_pct") is not None:
                row["gap_pct"] = round(float(p["gap_pct"]), 2)
        return row

    try:
        rows = [_build_row(p, include_brief=True) for p in predictions]
        supabase.table("watchlist_predictions").upsert(
            rows, on_conflict="user_id,ticker,pred_date"
        ).execute()
        return True
    except Exception as e1:
        # Schema not yet migrated — fall back to base columns only
        print(f"save_watchlist_predictions full schema failed ({e1}), retrying base columns")
        try:
            rows = [_build_row(p, include_brief=False) for p in predictions]
            supabase.table("watchlist_predictions").upsert(
                rows, on_conflict="user_id,ticker,pred_date"
            ).execute()
            return True
        except Exception as e2:
            print(f"save_watchlist_predictions error: {e2}")
            return False


def load_watchlist_predictions(user_id: str = "", pred_date=None) -> pd.DataFrame:
    """Load watchlist predictions from Supabase.

    If pred_date is None, loads all rows for the user sorted by date desc.
    """
    _base_cols = ["ticker", "pred_date", "predicted_structure", "tcs",
                  "edge_score", "actual_structure", "verified", "correct"]
    _brief_cols = ["entry_zone_low", "entry_zone_high", "entry_trigger",
                   "stop_level", "targets", "pattern", "pattern_neckline",
                   "win_rate_pct", "win_rate_context", "confidence_label"]
    _all_cols = _base_cols + _brief_cols
    if not supabase:
        return pd.DataFrame(columns=_all_cols)
    try:
        q = supabase.table("watchlist_predictions").select("*")
        uid = user_id or "anonymous"
        q = q.eq("user_id", uid)
        if pred_date:
            _ld_date  = str(pred_date)
            _ld_next  = str(pred_date + timedelta(days=1))
            q = q.gte("pred_date", _ld_date).lt("pred_date", _ld_next)
        q = q.order("edge_score", desc=True).limit(300)
        res = q.execute()
        if not res.data:
            return pd.DataFrame(columns=_all_cols)
        df = pd.DataFrame(res.data)
        for c in _all_cols:
            if c not in df.columns:
                df[c] = "" if c in _base_cols else None
        # Decode targets JSON string → list if needed
        if "targets" in df.columns:
            import json as _json
            def _parse_targets(v):
                if isinstance(v, list):
                    return v
                if isinstance(v, str) and v:
                    try:
                        return _json.loads(v)
                    except Exception:
                        pass
                return []
            df["targets"] = df["targets"].apply(_parse_targets)
        return df
    except Exception as e:
        print(f"load_watchlist_predictions error: {e}")
        return pd.DataFrame(columns=_all_cols)


def get_next_trading_day(as_of: date = None,
                         api_key: str = "",
                         secret_key: str = "") -> date:
    """Return the next NYSE trading day on or after as_of.

    - If as_of is already a trading day, returns as_of.
    - If it's a weekend/holiday, advances to the next open day.
    Uses Alpaca calendar when credentials available; falls back to
    weekend-skip + hardcoded holiday list.
    """
    if as_of is None:
        as_of = date.today()

    if api_key and secret_key:
        try:
            start_str = as_of.isoformat()
            end_str   = (as_of + timedelta(days=14)).isoformat()
            r = requests.get(
                "https://paper-api.alpaca.markets/v1/calendar",
                params={"start": start_str, "end": end_str},
                headers={
                    "APCA-API-KEY-ID":     api_key,
                    "APCA-API-SECRET-KEY": secret_key,
                },
                timeout=5,
            )
            if r.status_code == 200:
                cal = r.json()
                trading_dates = sorted([c["date"] for c in cal if c["date"] >= start_str])
                if trading_dates:
                    return date.fromisoformat(trading_dates[0])
        except Exception:
            pass

    # Fallback: skip weekends and hardcoded holidays
    d = as_of
    for _ in range(14):
        if is_trading_day(d):
            return d
        d += timedelta(days=1)
    return as_of


def verify_watchlist_predictions(api_key: str, secret_key: str,
                                  user_id: str = "", pred_date=None) -> dict:
    """Fetch end-of-day data and verify pending watchlist predictions.

    For each unverified prediction on pred_date (default: last trading day):
    - Re-runs the scoring engine on the full day's bars
    - Compares predicted_structure vs actual end-of-day structure
    - Updates the Supabase row with actual_structure + correct flag
    - Logs to accuracy_tracker so the brain can calibrate

    Returns a summary dict: {verified, correct, accuracy, date, error}.
    """
    if not supabase or not api_key or not secret_key:
        return {"verified": 0, "correct": 0, "accuracy": 0.0,
                "error": "No credentials"}

    # Default to last completed trading day (holiday-aware)
    if pred_date is None:
        # Start from yesterday and find the last actual trading day
        check_date = get_last_trading_day(
            as_of=date.today() - timedelta(days=1),
            api_key=api_key, secret_key=secret_key,
        )
    else:
        check_date = pred_date

    # Bar data date: if check_date is a non-trading day (weekend/holiday),
    # advance to the next actual trading day so we can still verify predictions
    # that were saved with a weekend/holiday date.
    if is_trading_day(check_date):
        bar_date = check_date
    else:
        bar_date = get_next_trading_day(
            as_of=check_date, api_key=api_key, secret_key=secret_key
        )

    # When user explicitly provides a date, fetch ALL predictions for that date
    # (including already-verified) so they can re-run verification.
    _explicit_date = pred_date is not None

    try:
        uid = user_id or "anonymous"
        _date_str  = str(check_date)
        _next_str  = str(check_date + timedelta(days=1))
        q = (supabase.table("watchlist_predictions")
             .select("*")
             .eq("user_id", uid)
             .gte("pred_date", _date_str)
             .lt("pred_date", _next_str))
        if not _explicit_date:
            q = q.eq("verified", False)
        res = q.execute()
        pending = res.data or []
    except Exception as e:
        return {"verified": 0, "correct": 0, "accuracy": 0.0, "error": str(e)}

    if not pending:
        return {"verified": 0, "correct": 0, "accuracy": 0.0,
                "date": str(check_date),
                "error": f"No predictions found for {check_date}"}

    verified_count = 0
    correct_count  = 0

    with ThreadPoolExecutor(max_workers=min(8, len(pending))) as executor:
        future_map = {
            executor.submit(
                _score_single_ticker, api_key, secret_key,
                p["ticker"], bar_date, "iex"
            ): p
            for p in pending
        }
        for future in as_completed(future_map):
            pred = future_map[future]
            try:
                sym, _tcs, actual_structure, _conf = future.result()
                if not actual_structure or actual_structure in ("—", ""):
                    continue
                predicted   = pred.get("predicted_structure", "")
                no_prediction = not predicted or predicted.strip() in ("—", "", "—")

                if no_prediction:
                    # No real structure was predicted — record actual for reference
                    # but do NOT write to accuracy_tracker (nothing to evaluate)
                    try:
                        supabase.table("watchlist_predictions").update({
                            "actual_structure": actual_structure,
                            "verified":         True,
                            "correct":          "",
                        }).eq("id", pred["id"]).execute()
                    except Exception:
                        pass
                    verified_count += 1
                    continue

                is_correct  = (
                    _strip_emoji(predicted) in _strip_emoji(actual_structure) or
                    _strip_emoji(actual_structure) in _strip_emoji(predicted)
                )
                correct_str = "✅" if is_correct else "❌"

                # Persist result back to the prediction row
                try:
                    supabase.table("watchlist_predictions").update({
                        "actual_structure": actual_structure,
                        "verified":         True,
                        "correct":          correct_str,
                    }).eq("id", pred["id"]).execute()
                except Exception:
                    pass

                # Feed into accuracy_tracker → triggers brain recalibration
                log_accuracy_entry(
                    symbol=sym,
                    predicted=predicted,
                    actual=actual_structure,
                    compare_key="watchlist_pred",
                    user_id=user_id,
                )
                verified_count += 1
                if is_correct:
                    correct_count += 1
            except Exception:
                continue

    accuracy = (correct_count / verified_count * 100) if verified_count > 0 else 0.0
    return {
        "verified":  verified_count,
        "total":     len(pending),
        "correct":   correct_count,
        "accuracy":  round(accuracy, 1),
        "date":      str(check_date),   # original pred_date (for display)
        "bar_date":  str(bar_date),     # actual trading day bars were fetched from
    }


# ── Webull Pattern Retroactive Scanner ───────────────────────────────────────

def scan_journal_patterns(
    api_key: str,
    secret_key: str,
    journal_df: "pd.DataFrame",
    feed: str = "iex",
) -> dict:
    """Retroactively detect chart patterns on every trade session in journal_df.

    For each unique (ticker, date) pair, fetches Alpaca 1-min bars and runs
    detect_chart_patterns.  Grades A/B count as wins; C/F count as losses.

    Returns a dict:
        sessions      — list of {ticker, date, grade, patterns, is_win}
        summary       — {pattern_name: {win, loss, total, win_rate}}
        by_outcome    — {"win": {pat:count}, "loss": {pat:count}}
        total_sessions— number of unique sessions attempted
        scanned       — number successfully scanned (had bar data)
        errors        — number that failed / had no data
    """
    if journal_df is None or journal_df.empty:
        return {"sessions": [], "summary": {}, "by_outcome": {"win": {}, "loss": {}},
                "total_sessions": 0, "scanned": 0, "errors": 0}

    WIN_GRADES  = {"A", "B"}

    # Build unique (ticker, date, grade) sessions — use the most recent grade per pair
    records = []
    ts_col = "timestamp"
    if ts_col not in journal_df.columns:
        return {"sessions": [], "summary": {}, "by_outcome": {"win": {}, "loss": {}},
                "total_sessions": 0, "scanned": 0, "errors": 0}

    _jdf = journal_df.copy()
    _jdf["_ts"]  = pd.to_datetime(_jdf[ts_col], errors="coerce")
    _jdf["_date"] = _jdf["_ts"].dt.date
    _jdf["_grade"] = _jdf["grade"].astype(str).str.upper().str.strip() if "grade" in _jdf.columns else "—"

    seen = {}
    for _, row in _jdf.dropna(subset=["_date"]).iterrows():
        tk = str(row.get("ticker", "")).upper().strip()
        dt = row["_date"]
        gr = row["_grade"]
        if not tk or not dt or tk == "NAN":
            continue
        key = (tk, dt)
        if key not in seen:
            seen[key] = gr
        else:
            # Prefer A > B > C > F over whatever we already have
            _rank = {"A": 0, "B": 1, "C": 2, "F": 3}
            if _rank.get(gr, 9) < _rank.get(seen[key], 9):
                seen[key] = gr

    sessions_meta = [{"ticker": k[0], "date": k[1], "grade": v,
                      "is_win": v in WIN_GRADES} for k, v in seen.items()]

    if not sessions_meta:
        return {"sessions": [], "summary": {}, "by_outcome": {"win": {}, "loss": {}},
                "total_sessions": 0, "scanned": 0, "errors": 0}

    # Batch-fetch bars + run pattern detection in parallel
    def _scan_one(meta):
        try:
            df = fetch_bars(api_key, secret_key, meta["ticker"], meta["date"], feed=feed)
            if df.empty or len(df) < 20:
                return None
            patterns = detect_chart_patterns(df)
            return {**meta, "patterns": patterns}
        except Exception:
            return None

    results = []
    with ThreadPoolExecutor(max_workers=min(10, len(sessions_meta))) as ex:
        future_map = {ex.submit(_scan_one, m): m for m in sessions_meta}
        for fut in as_completed(future_map):
            r = fut.result()
            if r is not None:
                results.append(r)

    # Aggregate pattern counts by outcome
    pat_stats: dict = {}
    win_counts: dict = {}
    loss_counts: dict = {}

    for sess in results:
        is_win = sess["is_win"]
        seen_pats = set()
        for p in sess.get("patterns", []):
            name = p["name"]
            if name in seen_pats:
                continue
            seen_pats.add(name)
            if name not in pat_stats:
                pat_stats[name] = {"win": 0, "loss": 0, "total": 0}
            if is_win:
                pat_stats[name]["win"] += 1
                win_counts[name] = win_counts.get(name, 0) + 1
            else:
                pat_stats[name]["loss"] += 1
                loss_counts[name] = loss_counts.get(name, 0) + 1
            pat_stats[name]["total"] += 1

    # Compute win rate per pattern
    total_wins   = sum(1 for s in results if s["is_win"])
    total_losses = sum(1 for s in results if not s["is_win"])

    summary = {}
    for pat, counts in pat_stats.items():
        t = counts["total"]
        summary[pat] = {
            "win":       counts["win"],
            "loss":      counts["loss"],
            "total":     t,
            "win_rate":  round(counts["win"] / t * 100, 1) if t > 0 else 0.0,
        }

    return {
        "sessions":       results,
        "summary":        summary,
        "by_outcome":     {"win": win_counts, "loss": loss_counts},
        "total_sessions": len(sessions_meta),
        "scanned":        len(results),
        "errors":         len(sessions_meta) - len(results),
        "total_wins":     total_wins,
        "total_losses":   total_losses,
    }


# ── God Mode — Live Trade Execution ──────────────────────────────────────────

def execute_alpaca_trade(
    api_key: str,
    secret_key: str,
    is_paper: bool,
    ticker: str,
    qty: int,
    side: str,
    limit_price: float = None,
) -> dict:
    """Submit a live or paper trade to Alpaca.

    Parameters
    ----------
    api_key, secret_key : Alpaca credentials entered in the sidebar.
    is_paper            : True  → paper trading endpoint
                          False → live trading endpoint
    ticker              : Stock symbol, e.g. 'GME'
    qty                 : Number of shares (whole shares only)
    side                : 'buy' or 'sell'
    limit_price         : If provided, submits a Day Limit order;
                          otherwise submits a Market order.

    Returns
    -------
    dict with keys:
        success  (bool)
        order_id (str)   — Alpaca order UUID on success
        message  (str)   — human-readable confirmation or error detail
    """
    if not api_key or not secret_key:
        return {"success": False, "order_id": None,
                "message": "No API credentials — enter your Alpaca key and secret in the sidebar."}
    if qty <= 0:
        return {"success": False, "order_id": None,
                "message": "Quantity must be at least 1 share."}
    if side not in ("buy", "sell"):
        return {"success": False, "order_id": None,
                "message": f"Invalid side '{side}' — must be 'buy' or 'sell'."}

    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        client = TradingClient(api_key, secret_key, paper=is_paper)

        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

        if limit_price is not None and limit_price > 0:
            req = LimitOrderRequest(
                symbol=ticker.upper(),
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
                limit_price=round(float(limit_price), 2),
            )
            order_type_label = f"LIMIT @ ${limit_price:.2f}"
        else:
            req = MarketOrderRequest(
                symbol=ticker.upper(),
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
            )
            order_type_label = "MARKET"

        order = client.submit_order(req)
        env_label = "PAPER" if is_paper else "LIVE"
        return {
            "success":  True,
            "order_id": str(order.id),
            "message":  (
                f"✅ {env_label} {side.upper()} {qty} {ticker.upper()} "
                f"({order_type_label}) submitted. "
                f"Order ID: {order.id} · Status: {order.status}"
            ),
        }

    except Exception as exc:
        return {"success": False, "order_id": None, "message": f"Alpaca error: {exc}"}


# ── User Preferences ──────────────────────────────────────────────────────────
_USER_PREFS_FILE = ".local/user_prefs.json"


def save_user_prefs(user_id: str, prefs: dict) -> bool:
    """Persist user preferences (API keys, webhook, etc.) to Supabase + local file."""
    import json as _json
    uid = user_id or "anonymous"

    # Always write locally first
    try:
        all_prefs: dict = {}
        if os.path.exists(_USER_PREFS_FILE):
            with open(_USER_PREFS_FILE) as _f:
                all_prefs = _json.load(_f)
        all_prefs[uid] = prefs
        os.makedirs(os.path.dirname(_USER_PREFS_FILE), exist_ok=True)
        with open(_USER_PREFS_FILE, "w") as _f:
            _json.dump(all_prefs, _f)
    except Exception:
        pass

    # Then Supabase
    if supabase:
        try:
            supabase.table("user_preferences").upsert(
                {"user_id": uid, "prefs": _json.dumps(prefs),
                 "updated_at": datetime.utcnow().isoformat()},
                on_conflict="user_id",
            ).execute()
        except Exception as e:
            print(f"save_user_prefs error: {e}")
    return True


def load_user_prefs(user_id: str) -> dict:
    """Load user preferences — Supabase first, local file fallback."""
    import json as _json
    uid = user_id or "anonymous"

    if supabase:
        try:
            res = (supabase.table("user_preferences")
                   .select("prefs")
                   .eq("user_id", uid)
                   .limit(1)
                   .execute())
            if res.data:
                raw = res.data[0].get("prefs", "{}")
                return _json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass

    try:
        if os.path.exists(_USER_PREFS_FILE):
            with open(_USER_PREFS_FILE) as _f:
                return _json.load(_f).get(uid, {})
    except Exception:
        pass
    return {}


def save_beta_chat_id(user_id: str, chat_id, tg_name: str = "") -> bool:
    """Store a beta tester's Telegram chat ID (and optional display name) in their user prefs."""
    if not user_id:
        return False
    prefs = load_user_prefs(user_id)
    prefs["tg_chat_id"] = str(chat_id)
    if tg_name:
        prefs["tg_name"] = tg_name
    return save_user_prefs(user_id, prefs)


def get_beta_chat_ids(
    exclude_user_id: str = "",
    tcs_alerts_only: bool = False,
    morning_alerts_only: bool = False,
    credential_alerts_only: bool = False,
    eod_alerts_only: bool = False,
) -> list:
    """Return list of (user_id, chat_id) tuples for all beta subscribers.

    Skips exclude_user_id (the owner) so they don't get duplicate messages.
    Falls back to the local prefs file when Supabase is unavailable.

    When tcs_alerts_only is True, subscribers who have opted out of TCS
    threshold shift alerts (tcs_alerts_enabled == False) are excluded.

    When morning_alerts_only is True, subscribers who have opted out of
    morning setup alerts (morning_alerts_enabled == False) are excluded.

    When credential_alerts_only is True, subscribers who have opted out of
    credential failure/recovery alerts (credential_alerts_enabled == False)
    are excluded.

    When eod_alerts_only is True, subscribers who have opted out of
    end-of-day result summaries (eod_alerts_enabled == False) are excluded.
    """
    import json as _json

    def _keep(prefs: dict) -> bool:
        """Return True if the subscriber should receive this alert type."""
        if tcs_alerts_only and prefs.get("tcs_alerts_enabled", True) is False:
            return False
        if morning_alerts_only and prefs.get("morning_alerts_enabled", True) is False:
            return False
        if credential_alerts_only and prefs.get("credential_alerts_enabled", True) is False:
            return False
        if eod_alerts_only and prefs.get("eod_alerts_enabled", True) is False:
            return False
        return True

    def _extract_pairs(rows_dict: dict) -> list:
        found = []
        for uid, prefs in rows_dict.items():
            if exclude_user_id and uid == exclude_user_id:
                continue
            if not isinstance(prefs, dict):
                continue
            if not _keep(prefs):
                continue
            cid = prefs.get("tg_chat_id")
            if cid:
                try:
                    found.append((uid, int(cid)))
                except (ValueError, TypeError):
                    pass
        return found

    if supabase:
        try:
            res = supabase.table("user_preferences").select("user_id,prefs").execute()
            pairs = []
            for row in res.data:
                uid = row.get("user_id", "")
                if exclude_user_id and uid == exclude_user_id:
                    continue
                raw = row.get("prefs", "{}")
                prefs = _json.loads(raw) if isinstance(raw, str) else (raw or {})
                if not _keep(prefs):
                    continue
                cid = prefs.get("tg_chat_id")
                if cid:
                    try:
                        pairs.append((uid, int(cid)))
                    except (ValueError, TypeError):
                        pass
            return pairs
        except Exception as e:
            print(f"get_beta_chat_ids Supabase error, trying local fallback: {e}")

    # Local file fallback when Supabase is unavailable
    try:
        if os.path.exists(_USER_PREFS_FILE):
            with open(_USER_PREFS_FILE) as _f:
                all_prefs = _json.load(_f)
            return _extract_pairs(all_prefs)
    except Exception as e:
        print(f"get_beta_chat_ids local fallback error: {e}")
    return []


def get_user_id_by_chat_id(chat_id) -> str:
    """Look up the user_id whose tg_chat_id matches the given Telegram chat_id.

    Returns an empty string when no match is found.
    """
    import json as _json
    target = str(chat_id)

    if supabase:
        try:
            res = supabase.table("user_preferences").select("user_id,prefs").execute()
            for row in res.data:
                raw = row.get("prefs", "{}")
                prefs = _json.loads(raw) if isinstance(raw, str) else (raw or {})
                if str(prefs.get("tg_chat_id", "")) == target:
                    return row.get("user_id", "")
        except Exception as e:
            print(f"get_user_id_by_chat_id Supabase error, trying local fallback: {e}")

    try:
        if os.path.exists(_USER_PREFS_FILE):
            with open(_USER_PREFS_FILE) as _f:
                all_prefs = _json.load(_f)
            for uid, prefs in all_prefs.items():
                if isinstance(prefs, dict) and str(prefs.get("tg_chat_id", "")) == target:
                    return uid
    except Exception as e:
        print(f"get_user_id_by_chat_id local fallback error: {e}")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# MACRO BREADTH REGIME  (Stockbee breadth data — top-down regime filter)
# ══════════════════════════════════════════════════════════════════════════════

def classify_macro_regime(
    four_pct_count: int,
    ratio_13_34: float,
    q_up: int,
    q_down: int,
) -> dict:
    """Classify macro market regime from Stockbee breadth inputs.

    Inputs:
      four_pct_count — Stocks up 4%+ on the day (from Stockbee Market Monitor)
      ratio_13_34    — 5-day or 10-day Advance/Decline ratio (>1.0 = more advances)
      q_up           — Stocks up 25%+ in a quarter
      q_down         — Stocks down 25%+ in a quarter

    Returns:
      regime_tag:    "hot_tape" | "warm" | "cold"
      label:         display label with emoji
      color:         hex color for UI
      mode:          "home_run" | "singles" | "caution"
      tcs_floor_adj: int — TCS threshold shift (negative = lower bar on hot tape)
      description:   brief explanation string
    """
    _desc = (
        f"{four_pct_count} stocks up 4%+ · A/D {ratio_13_34:.1f}x · "
        f"Q: {q_up} up / {q_down} down"
    )

    # ── Quarterly breadth ratio (Stockbee 25%/quarter flip) ─────────────────
    # q_ratio > 1.0 = more stocks up 25%+ than down 25%+ (bullish quarterly)
    # q_ratio < 1.0 = more stocks down 25%+ than up 25%+ (bearish quarterly)
    # When no quarterly data supplied (both 0), treat as neutral (ratio = 1.0)
    q_ratio = (q_up / max(q_down, 1)) if (q_up > 0 or q_down > 0) else 1.0

    # ── Strict rule-based classification (three-signal system) ───────────────
    # All three Stockbee breadth inputs feed the regime:
    #   Signal 1: daily 4%+ count  (momentum / thrust)
    #   Signal 2: 13%/34d A/D ratio  (intermediate breadth)
    #   Signal 3: quarterly 25% flip ratio  (macro tide)
    #
    # hot  = strong daily (≥600) AND strong A/D (≥2.0) AND quarterly not bearish (≥1.0)
    # warm = good daily (≥300) AND positive A/D (≥1.0)  [quarterly neutral or better]
    # cold = everything else (weak daily, weak A/D, or deeply bearish quarterly)
    if four_pct_count >= 600 and ratio_13_34 >= 2.0 and q_ratio >= 1.0:
        return {
            "regime_tag":    "hot_tape",
            "label":         "🔥 Hot Tape",
            "color":         "#ff6b35",
            "mode":          "home_run",
            "tcs_floor_adj": -10,
            "description":   _desc,
        }
    elif four_pct_count >= 300 and ratio_13_34 >= 1.0:
        return {
            "regime_tag":    "warm",
            "label":         "🟡 Warm Tape",
            "color":         "#ffd700",
            "mode":          "singles",
            "tcs_floor_adj": 0,
            "description":   _desc,
        }
    else:
        return {
            "regime_tag":    "cold",
            "label":         "❄️ Cold Tape",
            "color":         "#5c9bd4",
            "mode":          "caution",
            "tcs_floor_adj": +10,
            "description":   _desc,
        }


_MACRO_BREADTH_SQL = """
CREATE TABLE IF NOT EXISTS macro_breadth_log (
  id              SERIAL PRIMARY KEY,
  user_id         TEXT NOT NULL DEFAULT '',
  trade_date      DATE NOT NULL,
  four_pct_count  INT NOT NULL DEFAULT 0,
  ratio_13_34     FLOAT NOT NULL DEFAULT 0.0,
  q_up            INT NOT NULL DEFAULT 0,
  q_down          INT NOT NULL DEFAULT 0,
  regime_tag      TEXT,
  mode            TEXT,
  tcs_floor_adj   INT DEFAULT 0,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(trade_date, user_id)
);
""".strip()


def ensure_macro_breadth_log_table() -> bool:
    """Check if macro_breadth_log exists in Supabase. Returns True if ready.

    If missing, prints the SQL needed and returns False.
    Create the table by pasting _MACRO_BREADTH_SQL into the Supabase SQL editor.
    """
    if not supabase:
        return False
    try:
        supabase.table("macro_breadth_log").select("id").limit(1).execute()
        return True
    except Exception as e:
        err = str(e).lower()
        if any(k in err for k in ("404", "relation", "does not exist", "not found", "pgrst205")):
            print(
                "macro_breadth_log table not found.\n"
                "Run the following SQL in your Supabase SQL Editor:\n\n"
                + _MACRO_BREADTH_SQL
            )
            return False
        print(f"ensure_macro_breadth_log_table error: {e}")
        return False


def save_breadth_regime(
    trade_date,
    four_pct: int,
    ratio_13_34: float,
    q_up: int,
    q_down: int,
    user_id: str = "",
) -> bool:
    """Persist a breadth regime snapshot to Supabase (macro_breadth_log table).

    Upserts by (trade_date, user_id) so re-logging the same day updates in-place.
    Returns True on success.
    """
    if not supabase:
        return False
    regime = classify_macro_regime(four_pct, ratio_13_34, q_up, q_down)
    row = {
        "trade_date":     str(trade_date),
        "four_pct_count": int(four_pct),
        "ratio_13_34":    float(ratio_13_34),
        "q_up":           int(q_up),
        "q_down":         int(q_down),
        "regime_tag":     regime["regime_tag"],
        "mode":           regime["mode"],
        "tcs_floor_adj":  regime["tcs_floor_adj"],
        "user_id":        user_id or "",
    }
    try:
        supabase.table("macro_breadth_log").upsert(
            row, on_conflict="trade_date,user_id"
        ).execute()
        return True
    except Exception as e:
        print(f"save_breadth_regime error: {e}")
        return False


def get_breadth_regime(trade_date=None, user_id: str = "") -> dict:
    """Retrieve the most recent breadth regime from Supabase for a specific user.

    If trade_date is given, looks up that specific date for the user.
    Otherwise returns the most recent entry for that user.
    Falls back to a neutral 'no data' dict on any error.
    user_id is required to scope results correctly; global reads are not permitted.
    """
    _neutral = {
        "regime_tag":    "unknown",
        "label":         "⬜ No Data",
        "color":         "#555555",
        "mode":          "singles",
        "tcs_floor_adj": 0,
        "description":   "No breadth data yet — enter today's numbers in the sidebar.",
        "trade_date":    "",
    }
    if not supabase:
        return _neutral
    # Require user_id to prevent cross-user data leakage
    uid = user_id or ""
    try:
        q = (
            supabase.table("macro_breadth_log")
            .select("*")
            .eq("user_id", uid)
        )
        if trade_date:
            q = q.eq("trade_date", str(trade_date))
        res = q.order("trade_date", desc=True).limit(1).execute()
        if res.data:
            row = res.data[0]
            four_pct = row.get("four_pct_count", 0)
            ratio    = row.get("ratio_13_34",    0.0)
            q_up_val = row.get("q_up",           0)
            q_dn_val = row.get("q_down",         0)
            result = classify_macro_regime(four_pct, ratio, q_up_val, q_dn_val)
            # Always include the raw breadth inputs so callers like
            # map_regime_to_kalshi() can compute confidence from the actual values
            # instead of relying only on the derived regime_tag.
            result["trade_date"]     = row.get("trade_date", "")
            result["four_pct_count"] = int(four_pct)
            result["ratio_13_34"]    = float(ratio)
            result["q_up"]           = int(q_up_val)
            result["q_down"]         = int(q_dn_val)
            return result
    except Exception as e:
        print(f"get_breadth_regime error: {e}")
    return _neutral


def get_breadth_regime_history(days: int = 30, user_id: str = "") -> list:
    """Return up to `days` breadth regime entries from Supabase for a user, newest first.

    user_id is required to scope results to the authenticated user only.
    """
    if not supabase:
        return []
    uid = user_id or ""
    try:
        from datetime import date as _date, timedelta as _td
        cutoff = str(_date.today() - _td(days=days))
        res = (
            supabase.table("macro_breadth_log")
            .select("*")
            .eq("user_id", uid)
            .gte("trade_date", cutoff)
            .order("trade_date", desc=True)
            .limit(days)
            .execute()
        )
        raw = res.data or []
        # Enrich each row with computed regime fields
        enriched = []
        for row in raw:
            entry = classify_macro_regime(
                row.get("four_pct_count", 0),
                row.get("ratio_13_34", 0.0),
                row.get("q_up", 0),
                row.get("q_down", 0),
            )
            entry["trade_date"] = row.get("trade_date", "")
            enriched.append(entry)
        return enriched
    except Exception as e:
        print(f"get_breadth_regime_history error: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# KALSHI PREDICTION MARKET BOT  (paper trading against macro breadth signals)
# ══════════════════════════════════════════════════════════════════════════════

# ── Kalshi API helpers ────────────────────────────────────────────────────────

_KALSHI_DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"
_KALSHI_LIVE_BASE = "https://trading-api.kalshi.com/trade-api/v2"

_KALSHI_MACRO_KEYWORDS = [
    "s&p 500", "s&p500", "spx", "spy",
    "nasdaq", "ndx", "qqq",
    "dow jones", "djia",
    "fed", "federal reserve", "interest rate", "fomc",
    "inflation", "cpi", "pce",
    "unemployment", "nonfarm", "jobs",
    "gdp", "recession",
    "vix",
    "market",
]


def _kalshi_base(live: bool = False) -> str:
    return _KALSHI_LIVE_BASE if live else _KALSHI_DEMO_BASE


def kalshi_login(email: str, password: str, live: bool = False) -> str:
    """Authenticate with Kalshi API. Returns bearer token or '' on failure."""
    try:
        resp = requests.post(
            f"{_kalshi_base(live)}/login",
            json={"email": email, "password": password},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("token", "")
        print(f"kalshi_login failed: {resp.status_code} {resp.text[:120]}")
    except Exception as e:
        print(f"kalshi_login error: {e}")
    return ""


def fetch_kalshi_markets(token: str = "", live: bool = False, limit: int = 200) -> list:
    """Pull open Kalshi markets. Filters to macro-relevant titles.

    Returns list of dicts with: ticker, title, category, yes_price, no_price,
    expiration_time, event_ticker.
    Token is optional — public markets are accessible without auth.
    """
    try:
        params = {"status": "open", "limit": str(min(limit, 200))}
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = requests.get(
            f"{_kalshi_base(live)}/markets",
            params=params,
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"fetch_kalshi_markets: {resp.status_code} {resp.text[:120]}")
            return []
        raw_markets = resp.json().get("markets", [])
        macro_markets = []
        for m in raw_markets:
            title = (m.get("title") or "").lower()
            subtitle = (m.get("subtitle") or "").lower()
            combined = title + " " + subtitle
            if any(kw in combined for kw in _KALSHI_MACRO_KEYWORDS):
                macro_markets.append({
                    "ticker":           m.get("ticker", ""),
                    "event_ticker":     m.get("event_ticker", ""),
                    "title":            m.get("title", ""),
                    "subtitle":         m.get("subtitle", ""),
                    "category":         m.get("category", ""),
                    "yes_price":        int(m.get("yes_bid", m.get("yes_ask", 50)) or 50),
                    "no_price":         int(m.get("no_bid", m.get("no_ask", 50)) or 50),
                    "expiration_time":  m.get("expiration_time", ""),
                    "open_interest":    int(m.get("open_interest", 0) or 0),
                    "volume":           int(m.get("volume", 0) or 0),
                    "result":           m.get("result"),
                    "status":           m.get("status", ""),
                })
        return macro_markets
    except Exception as e:
        print(f"fetch_kalshi_markets error: {e}")
        return []


def fetch_kalshi_market_by_ticker(ticker: str, token: str = "", live: bool = False) -> dict:
    """Fetch a single Kalshi market by ticker for outcome resolution."""
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = requests.get(
            f"{_kalshi_base(live)}/markets/{ticker}",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("market", {})
    except Exception as e:
        print(f"fetch_kalshi_market_by_ticker error: {e}")
    return {}


# ── Signal mapping ────────────────────────────────────────────────────────────

_BULLISH_TERMS = [
    "above", "higher", "rise", "rally", "up", "bull", "gain",
    "exceed", "over", "close above", "end above", "finish above",
    "increase", "positive", "green",
]
_BEARISH_TERMS = [
    "below", "lower", "fall", "drop", "down", "bear", "loss",
    "under", "close below", "end below", "finish below",
    "decrease", "negative", "red",
]

_REGIME_BASE_CONFIDENCE = {
    "hot_tape": 0.72,
    "warm":     0.58,
    "cold":     0.68,
}


def map_regime_to_kalshi(regime: dict, markets: list) -> list:
    """Map breadth regime signals directly to a scored list of Kalshi market opportunities.

    Confidence is derived from the three raw Stockbee breadth inputs stored in `regime`:
      • four_pct_count  — stocks up 4%+ today  (daily thrust; threshold: 300/600)
      • ratio_13_34     — 13-day / 34-day A/D ratio  (intermediate breadth; threshold: 1.0/2.0)
      • q_ratio         — q_up / q_down (quarterly 25% flip ratio; threshold: 1.0)

    Confidence is built in two stages:
      1. Breadth confidence: how strongly each of the three inputs exceeds its threshold.
         Each input contributes an independent boost on top of the regime base.
      2. Market confidence: breadth confidence × sentiment alignment × price discount.

    Reasoning strings embed concrete metric values so every prediction is auditable.

    Returns list sorted by edge_score descending.
    Only includes markets with confidence >= 0.55.
    """
    regime_tag = regime.get("regime_tag", "unknown")
    if regime_tag == "unknown":
        return []

    # ── Extract raw breadth inputs ────────────────────────────────────────────
    four_pct  = float(regime.get("four_pct_count", 0))
    ratio     = float(regime.get("ratio_13_34",    0.0))
    q_up      = float(regime.get("q_up",           0))
    q_down    = float(regime.get("q_down",         0))
    q_ratio   = (q_up / max(q_down, 1)) if (q_up > 0 or q_down > 0) else 1.0

    # ── Compute per-input boosts relative to thresholds ──────────────────────
    # Scale factor for each input: how much above its threshold is it?
    # Capped at 1.0 (no bonus for being "very" hot beyond the hot tier).
    #   4% count: hot threshold=600, warm threshold=300; scale to [0.0, 1.0]
    four_pct_boost = min(max((four_pct - 300) / 300.0, 0.0), 1.0)
    #   A/D ratio:  hot threshold=2.0, warm threshold=1.0; scale to [0.0, 1.0]
    ratio_boost = min(max((ratio - 1.0) / 1.0, 0.0), 1.0)
    #   Q ratio:    hot threshold=1.0 (neutral); scale above 1.0 up to 2.0
    q_boost = min(max((q_ratio - 1.0) / 1.0, 0.0), 1.0) if regime_tag != "cold" else 0.0

    # Aggregate breadth confidence: regime base + weighted signal boosts
    # Weights: A/D ratio most informative (0.40), 4% count (0.35), Q ratio (0.25)
    base_conf = _REGIME_BASE_CONFIDENCE.get(regime_tag, 0.55)
    breadth_max_boost = 0.12  # max additional confidence from perfect breadth
    breadth_conf = base_conf + breadth_max_boost * (
        0.35 * four_pct_boost + 0.40 * ratio_boost + 0.25 * q_boost
    )
    breadth_conf = min(breadth_conf, 0.92)  # hard cap

    # Directional bias: hot_tape/warm → bullish, cold → bearish
    macro_bullish = regime_tag in ("hot_tape", "warm")

    # Build human-readable breadth evidence string (embedded in every alert)
    _q_str = f"{int(q_up)}↑/{int(q_down)}↓ 25%Q (ratio {q_ratio:.1f}x)"
    breadth_evidence = (
        f"4%/day={int(four_pct)} "
        f"({'✓' if four_pct >= 300 else '✗'}≥300"
        f"{'✓' if four_pct >= 600 else ''}≥600), "
        f"A/D={ratio:.2f}x "
        f"({'✓' if ratio >= 1.0 else '✗'}≥1.0"
        f"{'✓' if ratio >= 2.0 else ''}≥2.0), "
        f"{_q_str}"
    )

    results = []
    for m in markets:
        title = (m.get("title") or "").lower()
        subtitle = (m.get("subtitle") or "").lower()
        combined = title + " " + subtitle
        yes_price = int(m.get("yes_price", 50) or 50)
        no_price  = 100 - yes_price

        # ── Market sentiment detection ──────────────────────────────────────
        bull_count = sum(1 for t in _BULLISH_TERMS if t in combined)
        bear_count = sum(1 for t in _BEARISH_TERMS if t in combined)

        if bull_count == bear_count == 0:
            continue  # can't read directionality

        # Sentiment score: +1.0 fully bullish, -1.0 fully bearish
        total_terms = bull_count + bear_count
        sentiment = (bull_count - bear_count) / max(total_terms, 1)

        # Fed-specific: hot/warm tape = Fed stays or cuts (YES on "no hike", etc.)
        fed_market = any(kw in combined for kw in ["fed", "federal reserve", "fomc", "rate", "hike", "cut"])
        if fed_market:
            # Rate cut/pause markets are bullish for stocks
            rate_cut  = any(kw in combined for kw in ["cut", "lower", "decrease", "pause", "hold"])
            rate_hike = any(kw in combined for kw in ["hike", "raise", "increase", "higher"])
            if rate_cut:
                sentiment = 1.0 if macro_bullish else -1.0
            elif rate_hike:
                sentiment = -1.0 if macro_bullish else 1.0

        # ── Determine our predicted side ────────────────────────────────────
        if macro_bullish:
            if sentiment > 0:
                predicted_side   = "YES"
                price_of_our_side = yes_price
            elif sentiment < 0:
                predicted_side   = "NO"
                price_of_our_side = no_price
            else:
                continue
        else:  # cold tape = bearish
            if sentiment < 0:
                predicted_side   = "YES"
                price_of_our_side = yes_price
            elif sentiment > 0:
                predicted_side   = "NO"
                price_of_our_side = no_price
            else:
                continue

        # ── Confidence / edge computation ────────────────────────────────────
        # Sentiment strength multiplier: strong alignment → full confidence
        sentiment_strength = abs(sentiment)
        confidence = breadth_conf * (0.75 + 0.25 * sentiment_strength)

        # Discount if the market already strongly prices in our view
        # (less edge buying at 85¢ vs 50¢ even if right direction)
        price_discount = 1.0
        if price_of_our_side > 75:
            price_discount = 0.60   # already priced in — minimal edge
        elif price_of_our_side > 65:
            price_discount = 0.80
        elif price_of_our_side < 30:
            price_discount = 0.90   # contrarian — slight discount
        confidence *= price_discount
        confidence = round(confidence, 4)

        if confidence < 0.55:
            continue

        # Edge score: confidence premium over random (50% baseline)
        edge_score = round(confidence - 0.50, 4)

        # ── Auditable reasoning string (breadth metrics always included) ─────
        regime_label  = regime.get("label", regime_tag)
        direction_word = "bullish" if macro_bullish else "bearish"
        reason = (
            f"{regime_label} → {direction_word} | "
            f"Breadth: {breadth_evidence} | "
            f"Market signal: {'bullish' if sentiment > 0 else 'bearish'} "
            f"({predicted_side} pays {100 - price_of_our_side}¢ on {price_of_our_side}¢ risk)"
        )

        results.append({
            "ticker":           m["ticker"],
            "title":            m.get("title", ""),
            "category":         m.get("category", ""),
            "predicted_side":   predicted_side,
            "yes_price":        yes_price,
            "no_price":         no_price,
            "price_of_our_side": price_of_our_side,
            "confidence":       confidence,
            "edge_score":       edge_score,
            "reasoning":        reason,
            "breadth_evidence": breadth_evidence,   # stored in Supabase for auditability
            "four_pct_count":   int(four_pct),
            "ratio_13_34":      round(ratio, 3),
            "q_ratio":          round(q_ratio, 3),
            "expiration_time":  m.get("expiration_time", ""),
        })

    results.sort(key=lambda x: x["edge_score"], reverse=True)
    return results


# ── Kelly position sizing ─────────────────────────────────────────────────────

def kalshi_kelly_size(
    confidence: float,
    price_cents: int,
    account_value_cents: int = 10_000_00,  # default $10,000 paper account in cents
    kelly_fraction: float = 0.25,          # fractional Kelly (conservative)
    max_pct: float = 0.10,                 # max 10% per trade
) -> dict:
    """Compute Kelly-optimal position size for a Kalshi prediction market.

    Binary market Kelly formula:
      b = (100 - price_cents) / price_cents  (net odds if YES pays off)
      p = confidence (win probability)
      f* = (p * b - (1 - p)) / b  (Kelly fraction)
      fractional Kelly = f* × kelly_fraction

    Returns:
      kelly_f:    raw Kelly fraction
      size_f:     fractional Kelly fraction (kelly_f × kelly_fraction)
      contracts:  number of $1 contracts (1 contract = 100 cents cost)
      cost_cents: total cost in cents
      max_win_cents: max profit in cents if correct
    """
    if price_cents <= 0 or price_cents >= 100:
        return {"kelly_f": 0, "size_f": 0, "contracts": 0, "cost_cents": 0, "max_win_cents": 0}

    b = (100 - price_cents) / price_cents
    p = max(0.01, min(0.99, confidence))
    raw_kelly = (p * b - (1 - p)) / b

    # Negative or zero Kelly means no edge — do NOT force entry
    if raw_kelly <= 0:
        return {"kelly_f": round(raw_kelly, 4), "size_f": 0, "contracts": 0,
                "cost_cents": 0, "max_win_cents": 0}

    size_f = raw_kelly * kelly_fraction
    max_size_f = max_pct
    final_f = min(size_f, max_size_f)

    budget_cents = int(account_value_cents * final_f)
    contracts = budget_cents // price_cents  # no forced minimum — 0 is valid
    if contracts <= 0:
        return {"kelly_f": round(raw_kelly, 4), "size_f": round(final_f, 4),
                "contracts": 0, "cost_cents": 0, "max_win_cents": 0}

    cost_cents = contracts * price_cents
    max_win_cents = contracts * (100 - price_cents)

    return {
        "kelly_f":       round(raw_kelly, 4),
        "size_f":        round(final_f, 4),
        "contracts":     contracts,
        "cost_cents":    cost_cents,
        "max_win_cents": max_win_cents,
    }


# ── Supabase persistence ──────────────────────────────────────────────────────

_KALSHI_PREDICTIONS_SQL = """
CREATE TABLE IF NOT EXISTS kalshi_predictions (
  id                   BIGSERIAL PRIMARY KEY,
  user_id              TEXT NOT NULL DEFAULT '',
  trade_date           DATE NOT NULL,
  market_ticker        TEXT NOT NULL,
  market_title         TEXT,
  market_category      TEXT,
  regime_tag           TEXT NOT NULL,
  regime_label         TEXT,
  predicted_side       TEXT NOT NULL,
  entry_price_cents    INTEGER NOT NULL,
  confidence           FLOAT,
  kelly_fraction       FLOAT,
  paper_contracts      INTEGER DEFAULT 1,
  paper_cost_cents     INTEGER,
  paper_max_win_cents  INTEGER,
  outcome_result       TEXT,
  settlement_cents     INTEGER,
  pnl_cents            INTEGER,
  won                  BOOLEAN,
  settled_at           TIMESTAMPTZ,
  reasoning            TEXT,
  created_at           TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(trade_date, market_ticker, user_id)
);
""".strip()


def ensure_kalshi_tables() -> bool:
    """Check if kalshi_predictions table exists. Returns True if ready.

    If missing, prints the required SQL and returns False.
    Run _KALSHI_PREDICTIONS_SQL in the Supabase SQL Editor to create the table.
    """
    if not supabase:
        return False
    try:
        supabase.table("kalshi_predictions").select("id").limit(1).execute()
        return True
    except Exception as e:
        err = str(e).lower()
        if any(k in err for k in ("404", "relation", "does not exist", "not found", "pgrst205")):
            print(
                "kalshi_predictions table not found.\n"
                "Run the following SQL in your Supabase SQL Editor:\n\n"
                + _KALSHI_PREDICTIONS_SQL
            )
            return False
        print(f"ensure_kalshi_tables error: {e}")
        return False


def log_kalshi_prediction(
    trade_date,
    market: dict,
    regime: dict,
    sizing: dict,
    user_id: str = "",
) -> dict:
    """Persist a Kalshi paper trade prediction to Supabase.

    Upserts by (trade_date, market_ticker, user_id).
    Returns {"saved": True} or {"error": str}.
    """
    if not supabase:
        return {"error": "Supabase not configured"}
    row = {
        "trade_date":         str(trade_date),
        "user_id":            user_id or "",
        "market_ticker":      market.get("ticker", ""),
        "market_title":       market.get("title", ""),
        "market_category":    market.get("category", ""),
        "regime_tag":         regime.get("regime_tag", ""),
        "regime_label":       regime.get("label", ""),
        "predicted_side":     market.get("predicted_side", ""),
        "entry_price_cents":  int(market.get("price_of_our_side", 50)),
        "confidence":         float(market.get("confidence", 0)),
        "kelly_fraction":     float(sizing.get("size_f", 0)),
        "paper_contracts":    int(sizing.get("contracts", 1)),
        "paper_cost_cents":   int(sizing.get("cost_cents", 0)),
        "paper_max_win_cents": int(sizing.get("max_win_cents", 0)),
        "reasoning":          market.get("reasoning", ""),
    }
    try:
        supabase.table("kalshi_predictions").upsert(
            row, on_conflict="trade_date,market_ticker,user_id"
        ).execute()
        return {"saved": True}
    except Exception as e:
        print(f"log_kalshi_prediction error: {e}")
        return {"error": str(e)}


def update_kalshi_outcomes(
    trade_date=None,
    token: str = "",
    user_id: str = "",
    live: bool = False,
    lookback_days: int = 90,
) -> dict:
    """Check Kalshi API for settled markets and update outcomes in Supabase.

    Scans ALL unresolved predictions within the last `lookback_days` days —
    NOT just a single trade_date — because Kalshi macro markets frequently
    settle on a future date (e.g. monthly Fed decisions, quarterly GDP).
    Predictions from any prior day remain pending until their market settles.

    `trade_date` is retained for API compatibility but is ignored when
    querying; use `lookback_days` to control the history window.

    Returns {"updated": n, "total": m}.
    """
    if not supabase:
        return {"updated": 0, "total": 0, "error": "Supabase not configured"}
    try:
        cutoff = str(
            (datetime.now(EASTERN).date() - timedelta(days=lookback_days))
        )
        res = (
            supabase.table("kalshi_predictions")
            .select("*")
            .eq("user_id", user_id or "")
            .gte("trade_date", cutoff)
            .is_("outcome_result", "null")
            .execute()
        )
        rows = res.data or []
    except Exception as e:
        return {"updated": 0, "total": 0, "error": str(e)}

    updated = 0
    for row in rows:
        ticker = row.get("market_ticker", "")
        if not ticker:
            continue
        market_data = fetch_kalshi_market_by_ticker(ticker, token=token, live=live)
        result = market_data.get("result")
        if not result:
            continue  # not settled yet

        predicted_side = row.get("predicted_side", "")
        contracts      = int(row.get("paper_contracts", 1) or 1)
        entry_price    = int(row.get("entry_price_cents", 50) or 50)

        # Kalshi results: "yes" or "no"
        won = (result.lower() == predicted_side.lower())
        if won:
            settlement_cents = 100
            pnl_cents = contracts * (100 - entry_price)
        else:
            settlement_cents = 0
            pnl_cents = -contracts * entry_price

        try:
            supabase.table("kalshi_predictions").update({
                "outcome_result":   result.upper(),
                "settlement_cents": settlement_cents,
                "pnl_cents":        pnl_cents,
                "won":              won,
                "settled_at":       datetime.now(EASTERN).isoformat(),
            }).eq("id", row["id"]).execute()
            updated += 1
        except Exception as e:
            print(f"update_kalshi_outcomes update error for {ticker}: {e}")

    return {"updated": updated, "total": len(rows)}


def get_kalshi_predictions(
    days: int = 30,
    user_id: str = "",
    settled_only: bool = False,
) -> list:
    """Retrieve Kalshi prediction history from Supabase.

    Returns list of dicts, newest first.
    """
    if not supabase:
        return []
    try:
        from datetime import date as _date, timedelta as _td
        cutoff = str(_date.today() - _td(days=days))
        q = (
            supabase.table("kalshi_predictions")
            .select("*")
            .eq("user_id", user_id or "")
            .gte("trade_date", cutoff)
            .order("created_at", desc=True)
        )
        if settled_only:
            q = q.not_.is_("outcome_result", "null")
        res = q.execute()
        return res.data or []
    except Exception as e:
        print(f"get_kalshi_predictions error: {e}")
        return []


def get_kalshi_performance_summary(user_id: str = "") -> dict:
    """Summarise Kalshi paper trading performance.

    Returns:
      total, won, lost, pending, win_rate, total_pnl_cents, avg_confidence,
      first_trade_date (ISO date string of the oldest logged prediction),
      paper_days_elapsed (calendar days since first logged trade).
    """
    rows = get_kalshi_predictions(days=365, user_id=user_id)
    _empty = {
        "total": 0, "won": 0, "lost": 0, "pending": 0,
        "win_rate": 0.0, "total_pnl_cents": 0, "avg_confidence": 0.0,
        "first_trade_date": None, "paper_days_elapsed": 0,
    }
    if not rows:
        return _empty
    settled   = [r for r in rows if r.get("outcome_result") is not None]
    won       = [r for r in settled if r.get("won")]
    lost      = [r for r in settled if not r.get("won")]
    pending   = [r for r in rows if r.get("outcome_result") is None]
    pnl       = sum(int(r.get("pnl_cents", 0) or 0) for r in settled)
    conf_vals = [float(r["confidence"]) for r in rows if r.get("confidence")]

    # Compute paper run duration (calendar days since first prediction logged)
    trade_dates = [r.get("trade_date") for r in rows if r.get("trade_date")]
    first_trade_date: str | None = min(trade_dates) if trade_dates else None
    paper_days_elapsed = 0
    if first_trade_date:
        try:
            from datetime import date as _date
            start = _date.fromisoformat(str(first_trade_date))
            paper_days_elapsed = (datetime.now(EASTERN).date() - start).days
        except Exception:
            paper_days_elapsed = 0

    return {
        "total":              len(rows),
        "won":                len(won),
        "lost":               len(lost),
        "pending":            len(pending),
        "win_rate":           round(100 * len(won) / max(len(settled), 1), 1),
        "total_pnl_cents":    pnl,
        "avg_confidence":     round(sum(conf_vals) / max(len(conf_vals), 1), 3),
        "first_trade_date":   first_trade_date,
        "paper_days_elapsed": paper_days_elapsed,
    }


# ── Social Sentiment: StockTwits ───────────────────────────────────────────────

def fetch_stocktwits_sentiment(ticker: str) -> dict:
    """
    Fetch StockTwits sentiment for a ticker. Public endpoint, no API key required.
    Returns dict with bull_pct, bear_pct, neutral_pct, msg_count, msg_velocity, trending, error.
    """
    import datetime as _dt
    NO_DATA = {
        "bull_pct": None, "bear_pct": None, "neutral_pct": None,
        "msg_count": 0, "msg_velocity": 0.0, "trending": False, "error": True,
    }
    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker.upper()}.json"
        resp = requests.get(url, timeout=5,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return NO_DATA
        data = resp.json()
        messages = (data.get("messages") or [])[:30]
        if not messages:
            return NO_DATA

        bull = 0
        bear = 0
        neutral = 0
        now_utc = _dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc)
        msgs_last_hour = 0
        msgs_prev_hour = 0

        for m in messages:
            # Sentiment tag
            sent = (m.get("entities") or {}).get("sentiment") or {}
            basic = (sent.get("basic") or "").lower()
            if basic == "bullish":
                bull += 1
            elif basic == "bearish":
                bear += 1
            else:
                neutral += 1

            # Message velocity: count msgs in last 1h vs prev 1h
            ts_str = m.get("created_at") or ""
            try:
                ts = _dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age_h = (now_utc - ts).total_seconds() / 3600
                if age_h <= 1:
                    msgs_last_hour += 1
                elif age_h <= 2:
                    msgs_prev_hour += 1
            except Exception:
                pass

        total = bull + bear + neutral
        if total == 0:
            return NO_DATA

        velocity = float(msgs_last_hour) - float(msgs_prev_hour)
        trending = bool(msgs_last_hour >= 3 and velocity > 0)

        return {
            "bull_pct":    round(bull   / total * 100, 1),
            "bear_pct":    round(bear   / total * 100, 1),
            "neutral_pct": round(neutral / total * 100, 1),
            "msg_count":   total,
            "msg_velocity": velocity,
            "trending":    trending,
            "error":       False,
        }
    except Exception:
        return NO_DATA


# ── Psychology Engine: Lunar Cycle + Runner Similarity ────────────────────────

def get_lunar_phase(dt=None) -> dict:
    """
    Compute current lunar phase using standard moon age formula.
    Pure math — no external library.
    Returns: phase_name, emoji, moon_age_days, retail_mania (bool), icon_label.
    """
    import datetime as _dt
    if dt is None:
        dt = _dt.date.today()
    elif hasattr(dt, "date"):
        dt = dt.date()

    # Reference new moon: Jan 6, 2000 (Julian date midpoint)
    ref_new_moon = _dt.date(2000, 1, 6)
    SYNODIC = 29.53059  # days

    days_since = (dt - ref_new_moon).days
    moon_age   = days_since % SYNODIC

    if moon_age < 1.0 or moon_age >= 28.5:
        phase, emoji = "New Moon", "🌑"
    elif moon_age < 7.4:
        phase, emoji = "Waxing Crescent", "🌒"
    elif moon_age < 8.4:
        phase, emoji = "First Quarter", "🌓"
    elif moon_age < 14.8:
        phase, emoji = "Waxing Gibbous", "🌔"
    elif moon_age < 15.8:
        phase, emoji = "Full Moon", "🌕"
    elif moon_age < 22.1:
        phase, emoji = "Waning Gibbous", "🌖"
    elif moon_age < 23.1:
        phase, emoji = "Last Quarter", "🌗"
    else:
        phase, emoji = "Waning Crescent", "🌘"

    retail_mania = phase in ("New Moon", "Full Moon")
    icon_label   = "Retail Mania Window 🔥" if retail_mania else phase

    return {
        "phase":         phase,
        "emoji":         emoji,
        "moon_age_days": round(moon_age, 1),
        "retail_mania":  retail_mania,
        "icon_label":    icon_label,
    }


# ── Runner Similarity Engine ──────────────────────────────────────────────────

# 10 archetypal volume profile vectors (20 bins each, normalized to sum=1)
# Each represents a characteristic small-cap intraday volume distribution
_RUNNER_ARCHETYPES = {
    "Classic Breakout":     [0.18, 0.12, 0.09, 0.07, 0.06, 0.05, 0.05, 0.05, 0.04, 0.04,
                              0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.03, 0.03, 0.01],
    "Late-Day Runner":      [0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 0.05, 0.05,
                              0.06, 0.07, 0.08, 0.09, 0.10, 0.10, 0.08, 0.06, 0.03, 0.01],
    "Steady Accumulation":  [0.08, 0.07, 0.07, 0.06, 0.06, 0.06, 0.05, 0.05, 0.05, 0.05,
                              0.05, 0.05, 0.05, 0.05, 0.05, 0.04, 0.04, 0.04, 0.03, 0.01],
    "Open-Drive Power":     [0.25, 0.14, 0.10, 0.08, 0.06, 0.05, 0.05, 0.04, 0.04, 0.03,
                              0.03, 0.03, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01, 0.00, 0.01],
    "Double Distribution":  [0.10, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.03, 0.04, 0.05,
                              0.06, 0.07, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01],
    "Short Squeeze":        [0.05, 0.04, 0.04, 0.04, 0.04, 0.04, 0.05, 0.06, 0.08, 0.10,
                              0.12, 0.12, 0.10, 0.08, 0.06, 0.05, 0.04, 0.03, 0.02, 0.00],
    "Dump (Distribution)":  [0.20, 0.15, 0.12, 0.10, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03,
                              0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.00, 0.00, 0.00, 0.00],
    "Low Volume Drift":     [0.06, 0.06, 0.06, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05,
                              0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.04, 0.04, 0.03],
    "News Spike Fade":      [0.22, 0.16, 0.12, 0.09, 0.07, 0.06, 0.05, 0.05, 0.04, 0.03,
                              0.03, 0.02, 0.02, 0.01, 0.01, 0.01, 0.00, 0.00, 0.00, 0.00],
    "IB Range Compression": [0.07, 0.07, 0.07, 0.07, 0.07, 0.07, 0.07, 0.06, 0.06, 0.05,
                              0.05, 0.05, 0.04, 0.04, 0.04, 0.03, 0.03, 0.03, 0.02, 0.01],
}


def compute_runner_similarity(bin_centers: list, vap: list) -> dict:
    """
    Compare today's volume profile (bin_centers, vap) against 10 archetypal profiles
    using cosine similarity. Returns top match name, similarity %, and a confidence label.
    """
    import math

    NO_RESULT = {"archetype": "Unknown", "similarity": 0.0, "label": "No match", "is_strong": False}

    if not vap or len(vap) < 5:
        return NO_RESULT

    # Resample vap to 20 bins via uniform bucketing
    n = len(vap)
    N = 20
    total_vol = sum(vap)
    if total_vol <= 0:
        return NO_RESULT

    # Normalize to 20 bins
    bin_size = n / N
    vec = []
    for i in range(N):
        start = int(i * bin_size)
        end   = int((i + 1) * bin_size)
        vec.append(sum(vap[start:end]) / total_vol)

    # Cosine similarity vs each archetype
    def cosine(a, b):
        dot  = sum(x * y for x, y in zip(a, b))
        na   = math.sqrt(sum(x*x for x in a))
        nb   = math.sqrt(sum(x*x for x in b))
        return dot / (na * nb + 1e-12)

    best_name  = "Unknown"
    best_score = -1.0
    for name, arch in _RUNNER_ARCHETYPES.items():
        s = cosine(vec, arch)
        if s > best_score:
            best_score = s
            best_name  = name

    similarity_pct = round(best_score * 100, 1)
    is_strong      = similarity_pct >= 60.0
    label = (
        f"Runner DNA: {similarity_pct:.0f}% — {best_name}" if is_strong
        else f"DNA: {similarity_pct:.0f}% (weak match)"
    )

    return {
        "archetype":   best_name,
        "similarity":  similarity_pct,
        "label":       label,
        "is_strong":   is_strong,
    }
