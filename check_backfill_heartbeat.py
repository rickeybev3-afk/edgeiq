"""
check_backfill_heartbeat.py
────────────────────────────
Lightweight standalone script that checks whether the nightly backfill has run
recently and sends a Telegram alert if it has not.

Use this as an independent safety net — especially when nightly_tiered_pnl_refresh.py
itself might not be running (e.g. the Replit workflow stopped, a cron job failed,
or the process crashed before any backfill logic executed).

How it works
────────────
backfill_context_levels.py writes a JSON entry to backfill_history.json at the
end of every successful run.  This script reads that file and compares the latest
completed_at timestamp against the current UTC time.  If the gap exceeds the
configurable window (BACKFILL_HEARTBEAT_HOURS, default 25 h), or the history
file is absent entirely, a Telegram alert is sent.

Configuration (environment variables)
──────────────────────────────────────
  TELEGRAM_BOT_TOKEN        Required for Telegram alerts.
  TELEGRAM_CHAT_ID          Required for Telegram alerts.
  BACKFILL_HEARTBEAT_HOURS  Alert threshold in hours (default: 25).
                            Can be overridden at runtime via the dashboard
                            Settings page (backfill_heartbeat_hours pref in
                            .local/user_prefs.json).  The pref takes
                            precedence over this env var.
  BACKFILL_HISTORY_PATH     Path to the history JSON.
                            Defaults to <repo>/backfill_history.json, matching
                            the default used by backfill_context_levels.py.

Usage
─────
  python check_backfill_heartbeat.py

Exit codes
──────────
  0  Heartbeat OK (last run within the configured window).
  1  Alert sent — backfill appears missing, stale, or history file unreadable.
"""

import datetime
import html
import json
import logging
import os
import sys
import urllib.parse
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("backfill_heartbeat")

_DEFAULT_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "backfill_history.json"
)
_BACKFILL_HISTORY_PATH = os.getenv("BACKFILL_HISTORY_PATH", _DEFAULT_HISTORY_PATH)

_HEARTBEAT_ALERT_STATE_FILE = os.getenv(
    "BACKFILL_HEARTBEAT_STATE_PATH",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        ".backfill_heartbeat_alerted.json",
    ),
)


def _read_heartbeat_alert_state() -> dict:
    """Return the persisted heartbeat alert state, or an empty dict."""
    try:
        with open(_HEARTBEAT_ALERT_STATE_FILE) as _sf:
            return json.load(_sf)
    except FileNotFoundError:
        return {}
    except Exception as _e:
        log.warning("Could not read heartbeat alert state file: %s", _e)
        return {}


def _write_heartbeat_alert_state(last_alerted_utc: datetime.datetime) -> None:
    """Persist the timestamp of the most recent heartbeat alert."""
    try:
        with open(_HEARTBEAT_ALERT_STATE_FILE, "w") as _sf:
            json.dump({"last_alerted_utc": last_alerted_utc.isoformat()}, _sf)
    except Exception as _e:
        log.warning("Could not write heartbeat alert state file: %s", _e)


def _clear_heartbeat_alert_state() -> None:
    """Remove the heartbeat alert state file when a fresh run is detected."""
    try:
        os.remove(_HEARTBEAT_ALERT_STATE_FILE)
        log.info("Cleared heartbeat alert state (backfill is healthy).")
    except FileNotFoundError:
        pass
    except Exception as _e:
        log.warning("Could not remove heartbeat alert state file: %s", _e)


def _send_telegram(message: str) -> None:
    """Send a Telegram message using TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.

    Silently skips (log-only) if either credential is absent.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.info("Telegram credentials not set — skipping Telegram notification.")
        return
    try:
        body = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
        log.info("Telegram notification sent.")
    except Exception as exc:
        log.warning("Telegram send error: %s", exc)


def check_heartbeat() -> int:
    """Run the heartbeat check.

    Returns:
        0  — last run is within the allowed window (OK).
        1  — alert sent (backfill missing, stale, or history file unreadable).
    """
    try:
        window_hours = float(os.getenv("BACKFILL_HEARTBEAT_HOURS", "25"))
    except (ValueError, TypeError):
        log.warning(
            "Invalid BACKFILL_HEARTBEAT_HOURS value — falling back to 25 h default."
        )
        window_hours = 25.0
    _prefs_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".local", "user_prefs.json")
    try:
        if os.path.exists(_prefs_file):
            with open(_prefs_file) as _pf:
                _all_prefs = json.load(_pf)
            _owner_id = os.getenv("OWNER_USER_ID", "").strip() or "anonymous"
            _owner_prefs = _all_prefs.get(_owner_id, {})
            if "backfill_heartbeat_hours" in _owner_prefs:
                window_hours = float(_owner_prefs["backfill_heartbeat_hours"])
                log.info("Backfill heartbeat window overridden by dashboard setting: %.1f h.", window_hours)
    except Exception as _pe:
        log.warning("Could not read owner prefs for heartbeat window: %s", _pe)
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    last_completed_at: str | None = None
    read_error: str = ""
    try:
        with open(_BACKFILL_HISTORY_PATH) as _hf:
            history = json.load(_hf)
        if isinstance(history, list) and len(history) > 0:
            last_completed_at = history[-1].get("completed_at")
    except FileNotFoundError:
        log.warning("Backfill history file not found at %s.", _BACKFILL_HISTORY_PATH)
        # treated as "no record" below; alert will be sent
    except Exception as exc:
        log.warning("Could not read backfill history: %s", exc)
        read_error = str(exc)

    if read_error:
        age_desc = f"backfill history file could not be read ({read_error})"
    elif last_completed_at is None:
        age_desc = "no record of a previous backfill run found"
    else:
        try:
            last_dt = datetime.datetime.fromisoformat(last_completed_at)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=datetime.timezone.utc)
            age_hours = (now_utc - last_dt).total_seconds() / 3600
            if age_hours <= window_hours:
                log.info(
                    "Backfill heartbeat OK — last run %.1f h ago (threshold: %.0f h).",
                    age_hours,
                    window_hours,
                )
                # Fresh run detected — if a previous outage alert was on record,
                # send a one-time recovery notification then clear the state file.
                _previous_outage = os.path.exists(_HEARTBEAT_ALERT_STATE_FILE)
                if _previous_outage:
                    log.info("Previous outage alert found — sending recovery notification.")
                    _send_telegram(
                        "\u2705 <b>Backfill is healthy again</b>\n\n"
                        f"The backfill completed successfully (last run {age_hours:.1f} h ago). "
                        "No further alerts will be sent until the next outage."
                    )
                _clear_heartbeat_alert_state()
                return 0
            age_desc = (
                f"last run completed {age_hours:.1f} h ago "
                f"(threshold: {window_hours:.0f} h)"
            )
        except Exception as exc:
            log.warning("Could not parse completed_at timestamp: %s", exc)
            age_desc = f"backfill completed_at timestamp could not be parsed ({exc})"

    # Deduplication — suppress if an alert was already sent within the same
    # outage window (< 25 h ago).
    _hb_state = _read_heartbeat_alert_state()
    _last_alerted_str = _hb_state.get("last_alerted_utc", "")
    if _last_alerted_str:
        try:
            _last_alerted = datetime.datetime.fromisoformat(_last_alerted_str)
            if _last_alerted.tzinfo is None:
                _last_alerted = _last_alerted.replace(tzinfo=datetime.timezone.utc)
            _hours_since_alert = (now_utc - _last_alerted).total_seconds() / 3600
            if _hours_since_alert < window_hours:
                log.info(
                    "Heartbeat alert already sent %.1f h ago — suppressing duplicate "
                    "notification for this outage window (threshold: %.0f h).",
                    _hours_since_alert,
                    window_hours,
                )
                return 1
        except Exception as exc:
            log.warning("Could not parse last_alerted_utc from state file: %s", exc)

    log.warning(
        "Backfill heartbeat check FAILED: %s — sending Telegram alert.", age_desc
    )
    _send_telegram(
        f"\U0001f6a8 <b>Backfill appears to have been skipped</b>\n\n"
        f"{html.escape(age_desc)}\n\n"
        f"Check that <code>backfill_context_levels.py</code> is running correctly and "
        f"that Alpaca credentials are valid.\n\n"
        f"<i>Heartbeat window: {window_hours:.0f} h — set BACKFILL_HEARTBEAT_HOURS to change.</i>"
    )

    # Persist the alert timestamp so subsequent runs within the outage window
    # are suppressed.
    _write_heartbeat_alert_state(now_utc)
    return 1


if __name__ == "__main__":
    sys.exit(check_heartbeat())
