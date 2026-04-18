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
                return 0
            age_desc = (
                f"last run completed {age_hours:.1f} h ago "
                f"(threshold: {window_hours:.0f} h)"
            )
        except Exception as exc:
            log.warning("Could not parse completed_at timestamp: %s", exc)
            age_desc = f"backfill completed_at timestamp could not be parsed ({exc})"

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
    return 1


if __name__ == "__main__":
    sys.exit(check_heartbeat())
