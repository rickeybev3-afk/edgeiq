"""
nightly_tiered_pnl_refresh.py
──────────────────────────────
Long-running scheduler that keeps backtest_sim_runs.tiered_pnl_r up-to-date
by calling the backtest-only backfill once every night, and refreshes the
mv_tiered_pnl_summary materialised view so the Ladder tab always shows
current stats without manual intervention.

Design
──────
• On startup it runs the backfill immediately so any rows that accumulated
  since the last run are caught straight away.
• After each run it sleeps until midnight US/Eastern (≈ 00:05 ET to give
  any late-night batch jobs a few minutes to finish writing).
• At 21:00 ET (9 PM, after market close) the mv_tiered_pnl_summary view is
  refreshed so the Ladder tab is warm well before the midnight backfill.
• After each midnight backfill the view is refreshed a second time so it
  captures any rows written by the backfill itself.
• If the backfill or refresh raises an exception the scheduler logs the error
  and continues to the next nightly window rather than crashing.
• SIGTERM / SIGINT (e.g. Replit workflow stop) are caught so the process
  exits cleanly rather than mid-sleep or mid-run.

Usage (managed by the "Nightly Tiered P&L Refresh" Replit workflow)
──────────────────────────────────────────────────────────────────────
  python nightly_tiered_pnl_refresh.py

Flags passed through to run_tiered_pnl_backfill
─────────────────────────────────────────────────
  --no-ratelimit          Skip inter-request Alpaca sleep (useful on paid data plans)
  --date-from YYYY-MM-DD  Only rescan rows with sim_date >= this date
  --date-to   YYYY-MM-DD  Only rescan rows with sim_date <= this date
"""

import sys, os, time, datetime, subprocess, logging, signal, threading, json, html, argparse

# ── Shutdown flag — set by signal handler ─────────────────────────────────────

_shutdown = threading.Event()


def _handle_signal(signum, frame):
    log.info("Received signal %d — shutting down cleanly after current operation.",
             signum)
    _shutdown.set()


# ── Timezone helper (zoneinfo stdlib ≥ 3.9; fallback: fixed -5/-4 offset) ────

def _et_now() -> datetime.datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        # Fallback: approximate ET as UTC-4 (EDT) or UTC-5 (EST).
        # Rough DST: clocks spring forward 2nd Sun in Mar, back 1st Sun in Nov.
        utc = datetime.datetime.utcnow()
        month = utc.month
        if 3 < month < 11:
            offset = -4
        elif month == 3:
            day = utc.day
            offset = -4 if day >= 8 else -5
        elif month == 11:
            day = utc.day
            offset = -5 if day >= 7 else -4
        else:
            offset = -5
        return utc + datetime.timedelta(hours=offset)


def _seconds_until_midnight_et(target_hour: int = 0, target_minute: int = 5) -> float:
    """Return seconds until the next occurrence of target_hour:target_minute ET."""
    now_et = _et_now()
    target_today = now_et.replace(
        hour=target_hour, minute=target_minute, second=0, microsecond=0
    )
    if now_et >= target_today:
        target_today += datetime.timedelta(days=1)
    return (target_today - now_et).total_seconds()


def _interruptible_sleep(seconds: float) -> bool:
    """Sleep for up to `seconds`, waking early if _shutdown is set.

    Returns True if sleep completed normally, False if interrupted by shutdown.
    Polls every 5 s so shutdown latency is at most 5 s.
    """
    deadline = time.monotonic() + seconds
    while not _shutdown.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(5.0, remaining))
    return False


# ── Slack helper ──────────────────────────────────────────────────────────────


def _send_slack(message: str, webhook_url: str = "") -> None:
    """Send a plain-text message via a Slack incoming webhook.

    Reads SLACK_WEBHOOK_URL from the environment when *webhook_url* is not
    supplied explicitly.  Silently skips (log-only) if the resolved URL is
    absent or empty.
    """
    webhook_url = (webhook_url or os.getenv("SLACK_WEBHOOK_URL", "")).strip()
    if not webhook_url:
        log.info("SLACK_WEBHOOK_URL not set — skipping Slack notification.")
        return
    try:
        import urllib.request as _urllib_req
        import json as _json
        payload = _json.dumps({"text": message}).encode()
        req = _urllib_req.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urllib_req.urlopen(req, timeout=10):
            pass
        log.info("Slack notification sent.")
    except Exception as exc:
        log.warning("Slack send error: %s", exc)


# ── Telegram helper ───────────────────────────────────────────────────────────

_STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".edgeiq_tiered_pnl_run_stats.json")

_NR_FINISH_FILE = "/tmp/nightly_refresh.finish_time"

_CACHE_ALERT_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".edgeiq_cache_alert_state.json",
)

_HEARTBEAT_ALERT_STATE_FILE = os.getenv(
    "BACKFILL_HEARTBEAT_STATE_PATH",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        ".backfill_heartbeat_alerted.json",
    ),
)

_SQUEEZE_CALIB_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".edgeiq_squeeze_calib_alert.json",
)

_GAP_DOWN_CALIB_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".edgeiq_gap_down_calib_alert.json",
)

_DEFAULT_COOLDOWN_HOURS = 23
_DEFAULT_SUPPRESSION_ALERT_NIGHTS = 5


def _get_suppression_threshold() -> int:
    """Return the consecutive-failure night count that triggers an escalation alert.

    Read from CACHE_SUPPRESSION_ALERT_NIGHTS (default 5).  Set to 0 to disable
    escalation alerts entirely.
    """
    raw = os.getenv("CACHE_SUPPRESSION_ALERT_NIGHTS", "").strip()
    if raw:
        try:
            v = int(raw)
            if v >= 0:
                return v
        except ValueError:
            log.warning(
                "CACHE_SUPPRESSION_ALERT_NIGHTS='%s' is not a valid integer; "
                "using default %d.",
                raw,
                _DEFAULT_SUPPRESSION_ALERT_NIGHTS,
            )
    return _DEFAULT_SUPPRESSION_ALERT_NIGHTS


def _get_cooldown_hours() -> float:
    """Return the configured alert cooldown in hours (default 23)."""
    raw = os.getenv("CACHE_ALERT_COOLDOWN_HOURS", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            log.warning(
                "CACHE_ALERT_COOLDOWN_HOURS='%s' is not a valid number; using default %d h.",
                raw,
                _DEFAULT_COOLDOWN_HOURS,
            )
    return _DEFAULT_COOLDOWN_HOURS


def _read_alert_state() -> dict:
    """Return the persisted alert state, or an empty dict on any error."""
    try:
        with open(_CACHE_ALERT_STATE_FILE) as _f:
            return json.load(_f)
    except FileNotFoundError:
        return {}
    except Exception as _err:
        log.warning("Could not read cache alert state file: %s", _err)
        return {}


def _write_alert_state(state: dict) -> None:
    """Persist the alert state dict to disk."""
    try:
        with open(_CACHE_ALERT_STATE_FILE, "w") as _f:
            json.dump(state, _f)
    except Exception as _err:
        log.warning("Could not write cache alert state file: %s", _err)


def _clear_alert_cooldown(alert_key: str) -> None:
    """Clear the cooldown for a specific alert key so the next failure alerts fresh.

    Only the entry for `alert_key` is removed; cooldowns for other views are
    left intact so an independent persistent failure continues to be suppressed.

    If at least one failure alert was sent during the streak (i.e. last_sent_utc
    is present), a one-time recovery notification is dispatched before clearing
    the state so operators know the issue has resolved.
    Removing the key resets consecutive_failures to zero for this view.
    """
    state = _read_alert_state()
    if alert_key in state:
        key_state = state[alert_key]
        had_alert = isinstance(key_state, dict) and key_state.get("last_sent_utc")
        if had_alert:
            recovery_plain = f"Cache healthy again: {alert_key} refreshed successfully."
            recovery_html  = f"Cache healthy again: <b>{alert_key}</b> refreshed successfully."
            log.info("Sending cache-recovery notification for '%s'.", alert_key)
            _send_slack(recovery_plain)
            _send_telegram(recovery_html)
        del state[alert_key]
        _write_alert_state(state)


def _send_telegram(message: str) -> None:
    """Send a Telegram message using TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.

    Silently skips (log-only) if either credential is absent.
    """
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.info("Telegram credentials not set — skipping Telegram notification.")
        return
    try:
        import urllib.request as _urllib_req
        import urllib.parse  as _urllib_parse
        body = _urllib_parse.urlencode({
            "chat_id":    chat_id,
            "text":       message,
            "parse_mode": "HTML",
        }).encode()
        req  = _urllib_req.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            method="POST",
        )
        with _urllib_req.urlopen(req, timeout=10):
            pass
        log.info("Telegram notification sent.")
    except Exception as exc:
        log.warning("Telegram send error: %s", exc)


# ── Backfill heartbeat check ──────────────────────────────────────────────────


def _check_backfill_heartbeat() -> None:
    """Send a Telegram alert if the backfill history file is stale or absent.

    Resolves the history file path the same way backfill_context_levels.py does:
    1. BACKFILL_HISTORY_PATH env var (if set)
    2. <repo>/backfill_history.json (default)

    Compares the latest completed_at timestamp against the current UTC time.
    If the gap exceeds the configurable window (BACKFILL_HEARTBEAT_HOURS,
    default 25 h), or the file does not exist at all, a Telegram alert is sent
    so the operator knows a run was skipped.

    This catches silent failures such as:
      • The cron / Replit workflow never started the backfill script.
      • The script crashed before reaching the health-file write.
      • Alpaca credentials were missing and the script exited early.
    """
    _default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "backfill_history.json")
    history_path = os.getenv("BACKFILL_HISTORY_PATH", _default_path)
    try:
        window_hours = float(os.getenv("BACKFILL_HEARTBEAT_HOURS", "25"))
    except (ValueError, TypeError):
        log.warning(
            "Invalid BACKFILL_HEARTBEAT_HOURS value — falling back to 25 h default."
        )
        window_hours = 25.0
    _owner_id = os.getenv("OWNER_USER_ID", "").strip() or "anonymous"
    _prefs_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".local", "user_prefs.json")
    _owner_prefs: dict = {}
    try:
        if os.path.exists(_prefs_file):
            with open(_prefs_file) as _pf:
                _all_prefs = json.load(_pf)
            _owner_prefs = _all_prefs.get(_owner_id, {})
    except Exception as _pe:
        log.warning("Could not read owner prefs from local file for heartbeat window: %s", _pe)
    _supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    _supabase_key = (
        os.environ.get("SUPABASE_KEY") or
        os.environ.get("SUPABASE_ANON_KEY") or
        os.environ.get("VITE_SUPABASE_ANON_KEY") or
        ""
    )
    if _supabase_url and _supabase_key:
        try:
            import urllib.request as _ur
            import urllib.parse as _up
            _req = _ur.Request(
                f"{_supabase_url}/rest/v1/user_preferences?user_id=eq.{_up.quote(_owner_id, safe='')}&select=prefs&limit=1",
                headers={
                    "apikey": _supabase_key,
                    "Authorization": f"Bearer {_supabase_key}",
                    "Accept": "application/json",
                },
            )
            with _ur.urlopen(_req, timeout=4) as _resp:
                _rows = json.loads(_resp.read())
                if _rows:
                    _raw = _rows[0].get("prefs", "{}")
                    _owner_prefs = json.loads(_raw) if isinstance(_raw, str) else (_raw or {})
        except Exception as _spe:
            log.warning("Could not read owner prefs from Supabase for heartbeat window: %s", _spe)
    if "backfill_heartbeat_hours" in _owner_prefs:
        try:
            window_hours = float(_owner_prefs["backfill_heartbeat_hours"])
            log.info("Backfill heartbeat window overridden by dashboard setting: %.1f h.", window_hours)
        except (TypeError, ValueError) as _pe:
            log.warning("Could not apply owner pref for heartbeat window: %s", _pe)
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    last_completed_at: str | None = None
    read_error: str = ""
    try:
        with open(history_path) as _hf:
            _history = json.load(_hf)
        if isinstance(_history, list) and len(_history) > 0:
            last_completed_at = _history[-1].get("completed_at")
    except FileNotFoundError:
        pass  # treated as "no record" below
    except Exception as _e:
        log.warning("Could not read backfill history for heartbeat check: %s", _e)
        read_error = str(_e)

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
                    age_hours, window_hours,
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
                try:
                    os.remove(_HEARTBEAT_ALERT_STATE_FILE)
                    log.info("Cleared heartbeat alert state (backfill is healthy).")
                except FileNotFoundError:
                    pass
                except Exception as _e:
                    log.warning("Could not remove heartbeat alert state file: %s", _e)
                return
            age_desc = f"last run completed {age_hours:.1f} h ago (threshold: {window_hours:.0f} h)"
        except Exception as _e:
            log.warning("Could not parse backfill completed_at for heartbeat: %s", _e)
            age_desc = f"backfill completed_at timestamp could not be parsed ({_e})"

    # Deduplication — suppress if an alert was already sent within the same
    # outage window (< 25 h ago).
    try:
        with open(_HEARTBEAT_ALERT_STATE_FILE) as _sf:
            _hb_state = json.load(_sf)
        _last_alerted_str = _hb_state.get("last_alerted_utc", "")
        if _last_alerted_str:
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
                return
    except FileNotFoundError:
        pass  # No state file yet — first alert for this outage.
    except Exception as _e:
        log.warning("Could not read heartbeat alert state file: %s", _e)

    log.warning("Backfill heartbeat check FAILED: %s — sending Telegram alert.", age_desc)
    _send_telegram(
        f"\U0001f6a8 <b>Backfill appears to have been skipped</b>\n\n"
        f"{html.escape(age_desc)}\n\n"
        f"Check that <code>backfill_context_levels.py</code> is running correctly and "
        f"that Alpaca credentials are valid.\n\n"
        f"<i>Heartbeat window: {window_hours:.0f} h — set BACKFILL_HEARTBEAT_HOURS to change.</i>"
    )

    # Persist the alert timestamp so subsequent runs within the outage window
    # are suppressed.
    try:
        with open(_HEARTBEAT_ALERT_STATE_FILE, "w") as _sf:
            json.dump({"last_alerted_utc": now_utc.isoformat()}, _sf)
    except Exception as _e:
        log.warning("Could not write heartbeat alert state file: %s", _e)


# ── Squeeze calibration check ─────────────────────────────────────────────────

_SQUEEZE_CALIB_MIN_TRADES = 30
_SQUEEZE_CALIB_COOLDOWN_HOURS = 23


def _check_squeeze_calibration_due() -> None:
    """Alert when enough squeeze trades have settled to warrant re-calibration.

    Queries paper_trades for settled squeeze rows (screener_pass='squeeze' AND
    tiered_pnl_r IS NOT NULL).  If the count reaches _SQUEEZE_CALIB_MIN_TRADES
    AND _SP_MULT_TABLE['squeeze'] is still 1.00 in paper_trader_bot.py, a
    warning is logged and a Slack / Telegram alert is emitted prompting the
    trader to run calibrate_squeeze_mult.py.

    A 23-hour cooldown (persisted to _SQUEEZE_CALIB_STATE_FILE) prevents the
    same alert from firing on every nightly run.  The cooldown is cleared
    automatically once the multiplier is updated away from 1.00.

    No automatic edits are made — human-in-the-loop is preserved for the
    actual multiplier update.
    """
    import re as _re

    log.info("Checking squeeze calibration status …")

    # ── Step 1: count settled squeeze trades ──────────────────────────────────
    squeeze_count: int = 0
    try:
        import backend as _backend
        if not getattr(_backend, "supabase", None):
            log.warning(
                "Supabase client not initialised — skipping squeeze calibration check."
            )
            return
        resp = (
            _backend.supabase
            .table("paper_trades")
            .select("id", count="exact")
            .eq("screener_pass", "squeeze")
            .not_.is_("tiered_pnl_r", "null")
            .execute()
        )
        squeeze_count = resp.count if resp.count is not None else len(resp.data or [])
        log.info(
            "Squeeze settled trade count: %d (threshold: %d).",
            squeeze_count,
            _SQUEEZE_CALIB_MIN_TRADES,
        )
    except Exception as _exc:
        log.warning("Could not query squeeze settled trade count: %s", _exc)
        return

    if squeeze_count < _SQUEEZE_CALIB_MIN_TRADES:
        log.info(
            "Squeeze calibration not yet due (%d / %d settled trades).",
            squeeze_count,
            _SQUEEZE_CALIB_MIN_TRADES,
        )
        return

    # ── Step 2: read current _SP_MULT_TABLE['squeeze'] from paper_trader_bot.py
    _bot_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "paper_trader_bot.py"
    )
    squeeze_mult: float | None = None
    try:
        with open(_bot_path) as _bf:
            for _line in _bf:
                _m = _re.search(r'"squeeze"\s*:\s*([\d.]+)', _line)
                if _m:
                    squeeze_mult = float(_m.group(1))
                    break
    except Exception as _exc:
        log.warning(
            "Could not read _SP_MULT_TABLE from paper_trader_bot.py: %s", _exc
        )
        return

    if squeeze_mult is None:
        log.warning(
            "Could not find 'squeeze' entry in _SP_MULT_TABLE — skipping calibration check."
        )
        return

    log.info("Current _SP_MULT_TABLE['squeeze'] = %.2f.", squeeze_mult)

    if abs(squeeze_mult - 1.00) > 0.001:
        # Already calibrated away from baseline — clear any stale cooldown state.
        log.info(
            "_SP_MULT_TABLE['squeeze'] = %.2f (not 1.00) — calibration already applied.",
            squeeze_mult,
        )
        try:
            os.remove(_SQUEEZE_CALIB_STATE_FILE)
        except FileNotFoundError:
            pass
        except Exception as _exc:
            log.warning("Could not remove squeeze calib state file: %s", _exc)
        return

    # ── Step 3: enforce cooldown to avoid repeated nightly alerts ─────────────
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    try:
        with open(_SQUEEZE_CALIB_STATE_FILE) as _sf:
            _state = json.load(_sf)
        _last_str = _state.get("last_alerted_utc", "")
        if _last_str:
            _last = datetime.datetime.fromisoformat(_last_str)
            if _last.tzinfo is None:
                _last = _last.replace(tzinfo=datetime.timezone.utc)
            _hours_since = (now_utc - _last).total_seconds() / 3600
            if _hours_since < _SQUEEZE_CALIB_COOLDOWN_HOURS:
                log.info(
                    "Squeeze calibration alert already sent %.1f h ago — "
                    "suppressing duplicate (cooldown: %.0f h).",
                    _hours_since,
                    _SQUEEZE_CALIB_COOLDOWN_HOURS,
                )
                return
    except FileNotFoundError:
        pass  # First alert for this run — proceed.
    except Exception as _exc:
        log.warning("Could not read squeeze calibration alert state: %s", _exc)

    # ── Step 4: emit alert ────────────────────────────────────────────────────
    plain_msg = (
        f"squeeze calibration due — {squeeze_count} settled squeeze trades found "
        f"(threshold: {_SQUEEZE_CALIB_MIN_TRADES}). "
        f"Run: python calibrate_squeeze_mult.py"
    )
    html_msg = (
        f"\U0001f4ca <b>Squeeze calibration due</b>\n\n"
        f"{squeeze_count} settled squeeze trades found "
        f"(threshold: {_SQUEEZE_CALIB_MIN_TRADES}).\n\n"
        f"Run <code>python calibrate_squeeze_mult.py</code> to compute the recommended "
        f"multiplier and paste it into <code>paper_trader_bot.py</code>.\n\n"
        f"<i>_SP_MULT_TABLE['squeeze'] is currently 1.00\u00d7 (baseline — "
        f"no automatic edits are made).</i>"
    )
    log.warning(
        "SQUEEZE CALIBRATION DUE — %d settled squeeze trades found "
        "(>= %d threshold, _SP_MULT_TABLE['squeeze'] still 1.00). "
        "Run: python calibrate_squeeze_mult.py",
        squeeze_count,
        _SQUEEZE_CALIB_MIN_TRADES,
    )
    _send_slack(plain_msg)
    _send_telegram(html_msg)

    # Persist alert timestamp so subsequent runs within the cooldown window are
    # suppressed.
    try:
        with open(_SQUEEZE_CALIB_STATE_FILE, "w") as _sf:
            json.dump(
                {
                    "last_alerted_utc": now_utc.isoformat(),
                    "trade_count": squeeze_count,
                },
                _sf,
            )
    except Exception as _exc:
        log.warning("Could not write squeeze calibration alert state: %s", _exc)


# ── Gap-down calibration check ────────────────────────────────────────────────

_GAP_DOWN_CALIB_MIN_TRADES = 30
_GAP_DOWN_CALIB_COOLDOWN_HOURS = 23


def _check_gap_down_calibration_due() -> None:
    """Alert when enough gap_down trades have settled to warrant re-calibration.

    Queries paper_trades for settled gap_down rows (screener_pass='gap_down' AND
    tiered_pnl_r IS NOT NULL).  If the count reaches _GAP_DOWN_CALIB_MIN_TRADES
    AND _SP_MULT_TABLE['gap_down'] is still 1.00 in paper_trader_bot.py, a
    warning is logged and a Slack / Telegram alert is emitted prompting the
    trader to run calibrate_gap_down_mult.py.

    A 23-hour cooldown (persisted to _GAP_DOWN_CALIB_STATE_FILE) prevents the
    same alert from firing on every nightly run.  The cooldown is cleared
    automatically once the multiplier is updated away from 1.00.

    No automatic edits are made — human-in-the-loop is preserved for the
    actual multiplier update.
    """
    import re as _re

    log.info("Checking gap_down calibration status …")

    # ── Step 1: count settled gap_down trades ─────────────────────────────────
    gap_down_count: int = 0
    try:
        import backend as _backend
        if not getattr(_backend, "supabase", None):
            log.warning(
                "Supabase client not initialised — skipping gap_down calibration check."
            )
            return
        resp = (
            _backend.supabase
            .table("paper_trades")
            .select("id", count="exact")
            .eq("screener_pass", "gap_down")
            .not_.is_("tiered_pnl_r", "null")
            .execute()
        )
        gap_down_count = resp.count if resp.count is not None else len(resp.data or [])
        log.info(
            "Gap-down settled trade count: %d (threshold: %d).",
            gap_down_count,
            _GAP_DOWN_CALIB_MIN_TRADES,
        )
    except Exception as _exc:
        log.warning("Could not query gap_down settled trade count: %s", _exc)
        return

    if gap_down_count < _GAP_DOWN_CALIB_MIN_TRADES:
        log.info(
            "Gap-down calibration not yet due (%d / %d settled trades).",
            gap_down_count,
            _GAP_DOWN_CALIB_MIN_TRADES,
        )
        return

    # ── Step 2: read current _SP_MULT_TABLE['gap_down'] from paper_trader_bot.py
    _bot_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "paper_trader_bot.py"
    )
    gap_down_mult: float | None = None
    try:
        with open(_bot_path) as _bf:
            for _line in _bf:
                _m = _re.search(r'"gap_down"\s*:\s*([\d.]+)', _line)
                if _m:
                    gap_down_mult = float(_m.group(1))
                    break
    except Exception as _exc:
        log.warning(
            "Could not read _SP_MULT_TABLE from paper_trader_bot.py: %s", _exc
        )
        return

    if gap_down_mult is None:
        log.warning(
            "Could not find 'gap_down' entry in _SP_MULT_TABLE — skipping calibration check."
        )
        return

    log.info("Current _SP_MULT_TABLE['gap_down'] = %.2f.", gap_down_mult)

    if abs(gap_down_mult - 1.00) > 0.001:
        # Already calibrated away from baseline — clear any stale cooldown state.
        log.info(
            "_SP_MULT_TABLE['gap_down'] = %.2f (not 1.00) — calibration already applied.",
            gap_down_mult,
        )
        try:
            os.remove(_GAP_DOWN_CALIB_STATE_FILE)
        except FileNotFoundError:
            pass
        except Exception as _exc:
            log.warning("Could not remove gap_down calib state file: %s", _exc)
        return

    # ── Step 3: enforce cooldown to avoid repeated nightly alerts ─────────────
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    try:
        with open(_GAP_DOWN_CALIB_STATE_FILE) as _sf:
            _state = json.load(_sf)
        _last_str = _state.get("last_alerted_utc", "")
        if _last_str:
            _last = datetime.datetime.fromisoformat(_last_str)
            if _last.tzinfo is None:
                _last = _last.replace(tzinfo=datetime.timezone.utc)
            _hours_since = (now_utc - _last).total_seconds() / 3600
            if _hours_since < _GAP_DOWN_CALIB_COOLDOWN_HOURS:
                log.info(
                    "Gap-down calibration alert already sent %.1f h ago — "
                    "suppressing duplicate (cooldown: %.0f h).",
                    _hours_since,
                    _GAP_DOWN_CALIB_COOLDOWN_HOURS,
                )
                return
    except FileNotFoundError:
        pass  # First alert for this run — proceed.
    except Exception as _exc:
        log.warning("Could not read gap_down calibration alert state: %s", _exc)

    # ── Step 4: emit alert ────────────────────────────────────────────────────
    plain_msg = (
        f"gap_down calibration due — {gap_down_count} settled gap_down trades found "
        f"(threshold: {_GAP_DOWN_CALIB_MIN_TRADES}). "
        f"Run: python calibrate_gap_down_mult.py"
    )
    html_msg = (
        f"\U0001f4ca <b>Gap-down calibration due</b>\n\n"
        f"{gap_down_count} settled gap_down trades found "
        f"(threshold: {_GAP_DOWN_CALIB_MIN_TRADES}).\n\n"
        f"Run <code>python calibrate_gap_down_mult.py</code> to compute the recommended "
        f"multiplier and paste it into <code>paper_trader_bot.py</code>.\n\n"
        f"<i>_SP_MULT_TABLE['gap_down'] is currently 1.00\u00d7 (baseline — "
        f"no automatic edits are made).</i>"
    )
    log.warning(
        "GAP_DOWN CALIBRATION DUE — %d settled gap_down trades found "
        "(>= %d threshold, _SP_MULT_TABLE['gap_down'] still 1.00). "
        "Run: python calibrate_gap_down_mult.py",
        gap_down_count,
        _GAP_DOWN_CALIB_MIN_TRADES,
    )
    _send_slack(plain_msg)
    _send_telegram(html_msg)

    # Persist alert timestamp so subsequent runs within the cooldown window are
    # suppressed.
    try:
        with open(_GAP_DOWN_CALIB_STATE_FILE, "w") as _sf:
            json.dump(
                {
                    "last_alerted_utc": now_utc.isoformat(),
                    "trade_count": gap_down_count,
                },
                _sf,
            )
    except Exception as _exc:
        log.warning("Could not write gap_down calibration alert state: %s", _exc)


# ── Email helper ─────────────────────────────────────────────────────────────


def _send_email_alert(subject: str, body_plain: str, body_html: str) -> None:
    """Send an alert email via SendGrid API or SMTP.

    Credential resolution (tried in order):
      1. SendGrid  — SENDGRID_API_KEY + ALERT_EMAIL_FROM + ALERT_EMAIL_TO
      2. SMTP      — SMTP_HOST + ALERT_EMAIL_FROM + ALERT_EMAIL_TO
                     SMTP_USER / SMTP_PASSWORD are optional (unauthenticated
                     relays are supported).
                     SMTP_PORT defaults to 587, SMTP_TLS defaults to true.

    Silently skips (log-only) when no credentials are present.
    """
    from_addr = os.getenv("ALERT_EMAIL_FROM", "").strip()
    to_addr   = os.getenv("ALERT_EMAIL_TO",   "").strip()

    if not from_addr or not to_addr:
        log.info("ALERT_EMAIL_FROM/ALERT_EMAIL_TO not set — skipping email notification.")
        return

    sendgrid_key = os.getenv("SENDGRID_API_KEY", "").strip()
    smtp_host    = os.getenv("SMTP_HOST",        "").strip()

    if not sendgrid_key and not smtp_host:
        log.info("No email credentials configured (SENDGRID_API_KEY or SMTP_HOST) — skipping email notification.")
        return

    if sendgrid_key:
        _send_email_via_sendgrid(sendgrid_key, from_addr, to_addr, subject, body_plain, body_html)
    else:
        _send_email_via_smtp(smtp_host, from_addr, to_addr, subject, body_plain, body_html)


def _send_email_via_sendgrid(
    api_key: str, from_addr: str, to_addr: str,
    subject: str, body_plain: str, body_html: str,
) -> None:
    try:
        import json as _json
        import urllib.request as _urllib_req

        payload = _json.dumps({
            "personalizations": [{"to": [{"email": to_addr}]}],
            "from":    {"email": from_addr},
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": body_plain},
                {"type": "text/html",  "value": body_html},
            ],
        }).encode()
        req = _urllib_req.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        with _urllib_req.urlopen(req, timeout=15) as resp:
            status = resp.status
        if status in (200, 202):
            log.info("Email alert sent via SendGrid.")
        else:
            log.warning("SendGrid returned unexpected status %s.", status)
    except Exception as exc:
        log.warning("SendGrid send error: %s", exc)


def _send_email_via_smtp(
    smtp_host: str, from_addr: str, to_addr: str,
    subject: str, body_plain: str, body_html: str,
) -> None:
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text      import MIMEText

        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER",     "").strip()
        smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()
        use_tls   = os.getenv("SMTP_TLS", "true").strip().lower() not in ("0", "false", "no")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = from_addr
        msg["To"]      = to_addr
        msg.attach(MIMEText(body_plain, "plain"))
        msg.attach(MIMEText(body_html,  "html"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            if use_tls:
                server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        log.info("Email alert sent via SMTP (%s:%s).", smtp_host, smtp_port)
    except Exception as exc:
        log.warning("SMTP send error: %s", exc)


# ── Suppression-escalation alert ─────────────────────────────────────────────


def _send_suppression_escalation_alert(alert_key: str, consecutive: int) -> None:
    """Send a high-priority escalation alert when a view exceeds the suppression threshold.

    Called whenever consecutive_failures reaches a multiple of the configured
    threshold (CACHE_SUPPRESSION_ALERT_NIGHTS, default 5), e.g. at nights 5, 10, 15 …
    The alert is separate from the nightly summary so operators receive an
    out-of-band notification even if they are not actively reading the summary.
    """
    threshold = _get_suppression_threshold()
    timestamp = _et_now().strftime("%Y-%m-%d %H:%M:%S ET")

    plain_msg = (
        f"[EdgeIQ] ESCALATION: cache '{alert_key}' suppressed "
        f"{consecutive} consecutive nights\n"
        f"Time: {timestamp}\n"
        f"This view has been silently failing for {consecutive} nights in a row "
        f"(escalation threshold: {threshold} nights).\n"
        f"Immediate investigation required.\n"
        f"Set CACHE_SUPPRESSION_ALERT_NIGHTS to change the threshold."
    )
    html_msg = (
        f"\U0001f6a8 <b>EdgeIQ — Cache suppression ESCALATION</b>\n"
        f"Time: {timestamp}\n"
        f"View: <b>{html.escape(alert_key)}</b>\n"
        f"This cache has been silently failing for <b>{consecutive} consecutive "
        f"nights</b> (threshold: {threshold}).\n"
        f"<b>Immediate investigation required.</b>\n"
        f"<i>Set CACHE_SUPPRESSION_ALERT_NIGHTS to change the threshold.</i>"
    )
    email_subject = (
        f"[EdgeIQ] ESCALATION: cache '{alert_key}' suppressed "
        f"{consecutive} consecutive nights"
    )
    email_html = (
        f"<p>\U0001f6a8 <strong>EdgeIQ &mdash; Cache suppression ESCALATION</strong></p>"
        f"<p><strong>Time:</strong> {timestamp}</p>"
        f"<p><strong>View:</strong> <code>{html.escape(alert_key)}</code></p>"
        f"<p>This cache has been silently failing for "
        f"<strong>{consecutive} consecutive nights</strong> "
        f"(escalation threshold: {threshold} nights).</p>"
        f"<p><strong>Immediate investigation required.</strong></p>"
        f"<p><em>Set CACHE_SUPPRESSION_ALERT_NIGHTS to change the threshold.</em></p>"
    )

    log.warning(
        "Suppression escalation: '%s' has failed %d consecutive nights "
        "(threshold: %d) — sending high-priority alert.",
        alert_key,
        consecutive,
        threshold,
    )
    _send_slack(plain_msg)
    _send_telegram(html_msg)
    _send_email_alert(email_subject, plain_msg, email_html)


def _maybe_send_suppression_escalation(alert_key: str, key_state: dict) -> None:
    """Fire a high-priority escalation if consecutive_failures has crossed a threshold multiple.

    Reads CACHE_SUPPRESSION_ALERT_NIGHTS (default 5).  The escalation is
    triggered whenever consecutive_failures equals a positive multiple of that
    threshold — i.e. at nights 5, 10, 15 … — so operators receive repeated
    reminders rather than a single one-time alert.

    Semantic note: consecutive_failures is incremented on *every* failure call,
    whether or not the regular per-view alert was suppressed by the 23-hour
    cooldown.  This is intentional — night 1 is a real failure too, and the
    escalation should reflect how many nights in a row the view has been broken,
    not just how many times the regular notification was suppressed.

    Pass threshold=0 (via the env var) to disable escalation entirely.
    """
    threshold = _get_suppression_threshold()
    if threshold <= 0:
        return
    consecutive = int(key_state.get("consecutive_failures") or 0)
    if consecutive >= threshold and consecutive % threshold == 0:
        _send_suppression_escalation_alert(alert_key, consecutive)


# ── Cache-failure alert dispatcher ───────────────────────────────────────────


def _send_cache_failure_alert(error_msg: str, alert_key: str = "default") -> None:
    """Dispatch an operator alert when a materialised-view refresh fails.

    Tries Slack (SLACK_WEBHOOK_URL), Telegram (TELEGRAM_BOT_TOKEN +
    TELEGRAM_CHAT_ID), and Email (SENDGRID_API_KEY or SMTP_* + ALERT_EMAIL_*).
    Each channel silently skips if its credentials are absent, so operators
    only need to configure whichever channel(s) they use.

    Cooldown: alerts are tracked independently per `alert_key` (typically the
    view name).  If an alert for this key was already sent within
    CACHE_ALERT_COOLDOWN_HOURS (default 23 h) the notification is suppressed
    so operators are not flooded when the same view fails on consecutive nights.
    A successful refresh for the same key resets its cooldown, so the very next
    failure always generates a fresh alert.  Independent views are unaffected:
    if view A succeeds its cooldown is cleared while view B's cooldown remains
    in place.  Set CACHE_ALERT_COOLDOWN_HOURS=0 to disable suppression entirely.

    consecutive_failures is incremented on every call (whether suppressed or
    not) so the nightly Telegram summary can report how many nights in a row
    the alert has been firing.  _clear_alert_cooldown() resets the counter.
    """
    cooldown_hours = _get_cooldown_hours()
    now_utc = datetime.datetime.utcnow()

    # Always increment the consecutive-failure counter, even when suppressed.
    state = _read_alert_state()
    key_state = state.get(alert_key, {})
    if not isinstance(key_state, dict):
        key_state = {}
    key_state["consecutive_failures"] = int(key_state.get("consecutive_failures") or 0) + 1
    state[alert_key] = key_state

    if cooldown_hours > 0:
        last_sent_iso = key_state.get("last_sent_utc")
        if last_sent_iso:
            try:
                last_sent_dt = datetime.datetime.fromisoformat(last_sent_iso)
                age_hours = (now_utc - last_sent_dt).total_seconds() / 3600
                if age_hours < cooldown_hours:
                    log.info(
                        "Cache-failure alert suppressed for '%s' "
                        "(last sent %.1f h ago, cooldown %.0f h). Error: %s",
                        alert_key,
                        age_hours,
                        cooldown_hours,
                        error_msg,
                    )
                    _write_alert_state(state)
                    # Even when the regular alert is suppressed, check if the
                    # consecutive-failure count has crossed the escalation threshold.
                    _maybe_send_suppression_escalation(alert_key, key_state)
                    return
            except Exception as _parse_err:
                log.warning(
                    "Could not parse last_sent_utc for key '%s' from state file: %s",
                    alert_key,
                    _parse_err,
                )

    timestamp = _et_now().strftime("%Y-%m-%d %H:%M:%S ET")
    consecutive = int(key_state.get("consecutive_failures") or 1)
    streak_plain = f"\nThis view has failed {consecutive} nights in a row." if consecutive > 1 else ""
    streak_html  = f"\n<b>This view has failed {consecutive} nights in a row.</b>" if consecutive > 1 else ""
    streak_email = f"<p><strong>This view has failed {consecutive} nights in a row.</strong></p>" if consecutive > 1 else ""

    def _ordinal(n: int) -> str:
        if 11 <= (n % 100) <= 13:
            return f"{n}th"
        return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"

    streak_subject_suffix = f" ({_ordinal(consecutive)} consecutive)" if consecutive > 1 else ""
    streak_heading_suffix = f" ({_ordinal(consecutive)} consecutive)" if consecutive > 1 else ""

    escalation_threshold = int(os.getenv("CACHE_ALERT_ESCALATION_THRESHOLD", "3"))
    is_critical = consecutive >= escalation_threshold

    if is_critical:
        plain_msg = (
            f"[EdgeIQ CRITICAL] Ladder cache refresh FAILED ({consecutive} consecutive)\n"
            f"Time: {timestamp}\n"
            f"Error: {error_msg}"
            f"{streak_plain}"
        )
    else:
        plain_msg = (
            f"[EdgeIQ] Ladder cache refresh FAILED\n"
            f"Time: {timestamp}\n"
            f"Error: {error_msg}"
            f"{streak_plain}"
        )

    _send_slack(plain_msg)
    if is_critical:
        escalation_webhook = os.getenv("SLACK_ESCALATION_WEBHOOK_URL", "").strip()
        if escalation_webhook:
            log.info(
                "Escalating to SLACK_ESCALATION_WEBHOOK_URL after %d consecutive failures.",
                consecutive,
            )
            _send_slack(plain_msg, webhook_url=escalation_webhook)

    if is_critical:
        html_msg = (
            f"🚨 <b>EdgeIQ CRITICAL — Ladder cache refresh FAILED ({consecutive} consecutive)</b>\n"
            f"Time: {timestamp}\n"
            f"<b>Error:</b> <code>{html.escape(error_msg[:400])}</code>"
            f"{streak_html}"
        )
    else:
        html_msg = (
            f"⚠️ <b>EdgeIQ — Ladder cache refresh FAILED{streak_heading_suffix}</b>\n"
            f"Time: {timestamp}\n"
            f"<b>Error:</b> <code>{html.escape(error_msg[:400])}</code>"
            f"{streak_html}"
        )
    _send_telegram(html_msg)

    if is_critical:
        email_subject = f"[EdgeIQ CRITICAL] Ladder cache refresh FAILED ({consecutive} consecutive)"
        email_html = (
            f"<p>🚨 <strong>EdgeIQ CRITICAL — Ladder cache refresh FAILED ({consecutive} consecutive)</strong></p>"
            f"<p><strong>Time:</strong> {timestamp}</p>"
            f"<p><strong>Error:</strong> <code>{html.escape(error_msg[:400])}</code></p>"
            f"{streak_email}"
        )
    else:
        email_subject = f"[EdgeIQ] Ladder cache refresh FAILED{streak_subject_suffix}"
        email_html = (
            f"<p>⚠️ <strong>EdgeIQ — Ladder cache refresh FAILED</strong></p>"
            f"<p><strong>Time:</strong> {timestamp}</p>"
            f"<p><strong>Error:</strong> <code>{html.escape(error_msg[:400])}</code></p>"
            f"{streak_email}"
        )
    _send_email_alert(email_subject, plain_msg, email_html)

    key_state["last_sent_utc"] = now_utc.isoformat()
    state[alert_key] = key_state
    _write_alert_state(state)
    # Also check escalation on the first/fresh alert path.
    _maybe_send_suppression_escalation(alert_key, key_state)


# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("nightly_tiered_pnl")


# ── Ladder P&L summary-cache refresh ─────────────────────────────────────────

def _refresh_one_view(backend_fn_name: str, view_name: str) -> None:
    """Refresh a single materialised view by calling the named backend function.

    Parameters
    ----------
    backend_fn_name : name of the function on the backend module, e.g.
                      'refresh_mv_tiered_pnl_summary'.
    view_name       : display name used in log/alert messages.
    """
    log.info("Refreshing %s materialised view …", view_name)
    start = time.monotonic()
    try:
        import backend as _backend
        fn = getattr(_backend, backend_fn_name)
        result = fn()
        elapsed = time.monotonic() - start
        if result.get("success"):
            log.info(
                "%s refresh complete (%.1fs): %s",
                view_name,
                elapsed,
                result.get("message", "ok"),
            )
            _clear_alert_cooldown(alert_key=view_name)
        else:
            failure_msg = result.get("message", "unknown error")
            log.warning(
                "%s refresh failed (%.1fs): %s",
                view_name,
                elapsed,
                failure_msg,
            )
            _send_cache_failure_alert(f"{view_name}: {failure_msg}", alert_key=view_name)
    except Exception as exc:
        elapsed = time.monotonic() - start
        log.error(
            "%s refresh raised an exception after %.1fs: %s",
            view_name,
            elapsed,
            exc,
        )
        _send_cache_failure_alert(
            f"{view_name} exception after {elapsed:.1f}s: {exc}",
            alert_key=view_name,
        )


def refresh_summary_cache():
    """Refresh both the backtest and paper-trades Ladder materialised views.

    Calls refresh_mv_tiered_pnl_summary() (backtest_sim_runs) and
    refresh_mv_paper_tiered_pnl_summary() (paper_trades) so both Ladder
    stat cards benefit from the pre-aggregated cache after each nightly run.
    """
    _refresh_one_view("refresh_mv_tiered_pnl_summary",       "mv_tiered_pnl_summary")
    _refresh_one_view("refresh_mv_paper_tiered_pnl_summary", "mv_paper_tiered_pnl_summary")


# ── Backfill runner ───────────────────────────────────────────────────────────

def run_backfill(date_from: str = "", date_to: str = ""):
    """Invoke run_tiered_pnl_backfill.py (combined backtest + paper pass) as a subprocess.

    Running without --backtest-only ensures both backtest_sim_runs rows *and*
    paper_trades rows are kept up-to-date, so mv_paper_tiered_pnl_summary
    (and the Ladder cache) always reflects the latest paper trade activity.

    Using subprocess (rather than importing the module) keeps each run in a
    fresh interpreter context, which avoids any state leakage between nightly
    runs and makes it trivial to read the output as a stream.

    After each run a Telegram summary is sent (if credentials are available)
    with rows fetched, updated, skipped, errors, and elapsed time for both
    the backtest and paper-trades phases.

    date_from / date_to: optional ISO date strings (YYYY-MM-DD) forwarded to
    run_tiered_pnl_backfill.py to scope the rescan to a specific sim_date
    window.  When omitted the full-table scan runs as normal.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "run_tiered_pnl_backfill.py")
    cmd = [sys.executable, script]

    # Honour --no-ratelimit if this wrapper was started with it.
    if "--no-ratelimit" in sys.argv:
        cmd.append("--no-ratelimit")

    # Forward date-window filters when supplied.
    if date_from:
        cmd.extend(["--date-from", date_from])
    if date_to:
        cmd.extend(["--date-to", date_to])

    # Remove any stale stats file so we can detect a fresh write.
    try:
        os.remove(_STATS_FILE)
    except FileNotFoundError:
        pass
    except Exception as _rm_err:
        log.warning("Could not remove stale stats file: %s", _rm_err)

    log.info("Starting backfill:  %s", " ".join(cmd))
    start = time.monotonic()
    exception_msg: str = ""
    exit_code: int = 0
    captured_output: str = ""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # merge stderr into stdout
            text=True,
        )
        captured_output = result.stdout or ""
        elapsed = time.monotonic() - start
        exit_code = result.returncode
        # Echo captured output so it appears in the workflow console logs.
        if captured_output:
            sys.stdout.write(captured_output)
            sys.stdout.flush()
        if exit_code == 0:
            log.info("Backfill complete (%.0fs, exit 0).", elapsed)
        else:
            log.warning("Backfill exited with code %d (%.0fs).", exit_code, elapsed)
    except Exception as exc:
        elapsed = time.monotonic() - start
        exception_msg = str(exc)
        log.error("Backfill raised an exception after %.0fs: %s", elapsed, exc)

    # ── Build and send Telegram summary ───────────────────────────────────────
    run_date = _et_now().strftime("%Y-%m-%d")
    elapsed_fmt = f"{elapsed:.0f}s"

    def _tail(text: str, lines: int = 20) -> str:
        """Return the last `lines` lines of text, HTML-escaped."""
        tail = "\n".join(text.splitlines()[-lines:]).strip()
        return html.escape(tail)[:800]  # cap at 800 chars to stay within TG limits

    if exception_msg:
        msg = (
            f"⚠️ <b>Nightly Tiered P&amp;L Refresh — ERROR</b>\n"
            f"Date: {run_date}\n"
            f"Elapsed: {elapsed_fmt}\n"
            f"<b>Exception:</b> <code>{html.escape(exception_msg[:300])}</code>"
        )
        try:
            with open(_NR_FINISH_FILE, "w") as _nrf:
                json.dump({"finished_at": time.time(), "elapsed_s": elapsed, "success": False}, _nrf)
        except Exception:
            pass
        _send_telegram(msg)
        return

    # Try to read stats written by the backfill script.
    stats: dict = {}
    try:
        with open(_STATS_FILE) as _f:
            stats = json.load(_f)
    except FileNotFoundError:
        log.warning("Stats file not found — backfill may have failed before writing it.")
    except Exception as _read_err:
        log.warning("Could not read stats file: %s", _read_err)

    bt = stats.get("backtest") or {}
    pt = stats.get("paper_trades") or {}

    if exit_code != 0 or not stats:
        # Backfill exited with an error or produced no stats; include output tail.
        error_excerpt = _tail(captured_output) if captured_output else "(no output captured)"
        msg = (
            f"⚠️ <b>Nightly Tiered P&amp;L Refresh — FAILED</b>\n"
            f"Date: {run_date}\n"
            f"Exit code: {exit_code}\n"
            f"Elapsed: {elapsed_fmt}\n"
            f"<b>Last output:</b>\n<code>{error_excerpt}</code>"
        )
    else:
        elapsed_s = stats.get("elapsed_s", elapsed)
        mins, secs = divmod(int(elapsed_s), 60)
        elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"

        bt_fetched  = bt.get("fetched", 0)
        bt_updated  = bt.get("updated", 0)
        bt_skipped  = bt.get("skipped_no_bars", 0) + bt.get("skipped_no_tiered", 0)
        bt_errors   = bt.get("errors", 0)

        pt_fetched  = pt.get("fetched", 0)
        pt_updated  = pt.get("updated", 0)
        pt_skipped  = pt.get("skipped_no_bars", 0) + pt.get("skipped_no_tiered", 0)
        pt_errors   = pt.get("errors", 0)

        total_errors = bt_errors + pt_errors
        status_icon = "✅" if total_errors == 0 else "⚠️"

        # Report per-view consecutive-failure streaks so operators can see which
        # specific cache is repeatedly failing rather than just an aggregate total.
        _TG_LIMIT = 4096
        alert_state = _read_alert_state()
        failing_views = {
            key: int(v.get("consecutive_failures", 0))
            for key, v in alert_state.items()
            if isinstance(v, dict) and int(v.get("consecutive_failures", 0)) > 0
        }

        base_msg = (
            f"{status_icon} <b>Nightly Tiered P&amp;L Refresh</b>\n"
            f"Date: {run_date}\n"
            f"\n"
            f"<b>backtest_sim_runs</b>\n"
            f"  Fetched : {bt_fetched}\n"
            f"  Updated : {bt_updated}\n"
            f"  Skipped : {bt_skipped}\n"
            f"  Errors  : {bt_errors}\n"
            f"\n"
            f"<b>paper_trades</b>\n"
            f"  Fetched : {pt_fetched}\n"
            f"  Updated : {pt_updated}\n"
            f"  Skipped : {pt_skipped}\n"
            f"  Errors  : {pt_errors}\n"
            f"\n"
            f"Elapsed : {elapsed_str}"
        )

        if failing_views:
            # Sort descending by failure count so the worst offender appears first.
            sorted_views = sorted(failing_views.items(), key=lambda x: x[1], reverse=True)

            # Build a compact monospace table inside <pre> so it renders like a grid
            # in Telegram's HTML mode (which does not support real <table> tags).
            col_w = max(len(k) for k, _ in sorted_views)
            col_w = max(col_w, len("View"))  # at least as wide as the header
            divider = f"{'-' * col_w}-+-{'-------'}"
            col_header = f"{'View':<{col_w}} | Nights"

            # Build rows, respecting the Telegram 4096-char hard limit.
            header_block = f"\n\u26a0\ufe0f <b>Cache alerts suppressed:</b>\n<pre>{col_header}\n{divider}"
            footer_block = "</pre>"
            # Budget = total limit minus the fixed parts already committed.
            budget = _TG_LIMIT - len(base_msg) - len(header_block) - len(footer_block)

            rows: list[str] = []
            omitted = 0
            for key, count in sorted_views:
                nights_label = f"{count} night{'s' if count != 1 else ''}"
                row = f"\n{key:<{col_w}} | {nights_label}"
                if len(row) <= budget:
                    rows.append(row)
                    budget -= len(row)
                else:
                    omitted += 1

            if omitted:
                tail = f"\n\u2026 (+{omitted} more)"
                if len(tail) <= budget:
                    rows.append(tail)

            suppressed_line = header_block + "".join(rows) + footer_block
        else:
            suppressed_line = ""

        msg = base_msg + suppressed_line

    _nr_elapsed_s = stats.get("elapsed_s", elapsed) if (exit_code == 0 and bool(stats)) else elapsed
    try:
        with open(_NR_FINISH_FILE, "w") as _nrf:
            json.dump({
                "finished_at": time.time(),
                "elapsed_s": _nr_elapsed_s,
                "success": exit_code == 0 and bool(stats),
            }, _nrf)
    except Exception:
        pass

    _send_telegram(msg)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Nightly Tiered P&L Refresh scheduler",
        add_help=False,
    )
    parser.add_argument(
        "--date-from",
        default="",
        metavar="YYYY-MM-DD",
        help="Only rescan backtest_sim_runs rows with sim_date >= this date.",
    )
    parser.add_argument(
        "--date-to",
        default="",
        metavar="YYYY-MM-DD",
        help="Only rescan backtest_sim_runs rows with sim_date <= this date.",
    )
    parser.add_argument(
        "--no-ratelimit",
        action="store_true",
        default=False,
        help="Skip inter-request Alpaca sleep (forwarded to the backfill script).",
    )
    args, _unknown = parser.parse_known_args()

    date_from = args.date_from.strip()
    date_to   = args.date_to.strip()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    log.info("=" * 60)
    log.info("Nightly Tiered P&L Refresh — started")
    log.info("Scheduled events (ET):  21:00 → cache refresh (both views),  00:05 → backfill + cache refresh")
    if date_from or date_to:
        log.info(
            "Date window: %s → %s",
            date_from or "(unbounded)",
            date_to   or "(unbounded)",
        )
    log.info("=" * 60)

    # Report which failure-alert channels are active so misconfiguration is visible immediately.
    slack_active    = bool(os.getenv("SLACK_WEBHOOK_URL", "").strip())
    telegram_active = (
        bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
        and bool(os.getenv("TELEGRAM_CHAT_ID", "").strip())
    )
    email_active = (
        bool(os.getenv("ALERT_EMAIL_FROM", "").strip())
        and bool(os.getenv("ALERT_EMAIL_TO",   "").strip())
        and (
            bool(os.getenv("SENDGRID_API_KEY", "").strip())
            or bool(os.getenv("SMTP_HOST", "").strip())
        )
    )
    if slack_active or telegram_active or email_active:
        channels = ", ".join(filter(None, [
            "Slack"    if slack_active    else "",
            "Telegram" if telegram_active else "",
            "Email"    if email_active    else "",
        ]))
        log.info("Cache-failure alerts enabled via: %s", channels)
    else:
        log.warning(
            "No alert channels configured — cache-refresh failures will only appear in logs. "
            "Set SLACK_WEBHOOK_URL, TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID, or "
            "SENDGRID_API_KEY (or SMTP_HOST)+ALERT_EMAIL_FROM+ALERT_EMAIL_TO to enable alerts."
        )

    suppression_threshold = _get_suppression_threshold()
    if suppression_threshold > 0:
        log.info(
            "Suppression-escalation alerts enabled: high-priority alert fires "
            "when a view fails %d+ consecutive nights (set CACHE_SUPPRESSION_ALERT_NIGHTS "
            "to change, or 0 to disable).",
            suppression_threshold,
        )
    else:
        log.info(
            "Suppression-escalation alerts disabled "
            "(CACHE_SUPPRESSION_ALERT_NIGHTS=0)."
        )

    # Run backfill immediately on startup to catch any rows written since last run.
    # Check heartbeat first so a missed run is flagged before fresh history is written.
    log.info("─── Startup run ──────────────────────────────────────────────")
    _check_backfill_heartbeat()
    run_backfill(date_from=date_from, date_to=date_to)
    _check_squeeze_calibration_due()
    _check_gap_down_calibration_due()
    refresh_summary_cache()

    run_number = 0
    while not _shutdown.is_set():
        # Calculate seconds until each scheduled event.
        secs_to_9pm     = _seconds_until_midnight_et(target_hour=21, target_minute=0)
        secs_to_midnight = _seconds_until_midnight_et(target_hour=0,  target_minute=5)

        # Wake at whichever event is sooner.
        if secs_to_9pm < secs_to_midnight:
            sleep_secs = secs_to_9pm
            next_event = "21:00 ET cache refresh"
            do_backfill = False
        else:
            sleep_secs = secs_to_midnight
            next_event = "00:05 ET backfill + cache refresh"
            do_backfill = True

        wake_et = _et_now() + datetime.timedelta(seconds=sleep_secs)
        log.info(
            "Next event: %s at %s ET (sleeping %.0f s / %.1f h).",
            next_event,
            wake_et.strftime("%Y-%m-%d %H:%M"),
            sleep_secs,
            sleep_secs / 3600,
        )

        completed = _interruptible_sleep(sleep_secs)
        if not completed:
            break

        run_number += 1
        log.info("─── Event #%d: %s ─────────────────────────────────────────",
                 run_number, next_event)

        if do_backfill:
            _check_backfill_heartbeat()
            run_backfill(date_from=date_from, date_to=date_to)
            if _shutdown.is_set():
                break
            _check_squeeze_calibration_due()
            _check_gap_down_calibration_due()

        refresh_summary_cache()

    log.info("Nightly Tiered P&L Refresh — stopped cleanly.")


if __name__ == "__main__":
    main()
