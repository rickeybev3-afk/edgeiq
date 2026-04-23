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
• After each backfill (startup and midnight) the pending row resolver step
  runs: it calls backfill_pending_sim_rows.py to resolve any rows whose
  actual_outcome is still 'Pending' (left behind when batch_backtest.py
  fails mid-run).  If more than PENDING_ROW_ALERT_THRESHOLD (default 50)
  rows remain after the resolver, a Slack/Telegram alert is fired.
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
from calib_threshold import resolve_calib_threshold

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

_TP_CALIB_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".edgeiq_tp_calib_run.json",
)

_TP_PREVIEW_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".edgeiq_tp_preview_run.json",
)

_MORNING_TCS_CALIB_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".edgeiq_morning_tcs_calib.json",
)

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

_PENDING_RESOLVER_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".edgeiq_pending_resolver_alert.json",
)

_DEFAULT_PENDING_ALERT_THRESHOLD = 50

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
    In dev (REPLIT_DEPLOYMENT not set) notifications are suppressed unless
    DEV_TG_ENABLED=1 is explicitly set, preventing duplicate alerts when
    the Replit IDE is opened alongside a live deployment.
    """
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.info("Telegram credentials not set — skipping Telegram notification.")
        return
    _is_deployed = bool(os.environ.get("REPLIT_DEPLOYMENT"))
    _dev_tg_ok   = os.environ.get("DEV_TG_ENABLED", "").strip() == "1"
    if not _is_deployed and not _dev_tg_ok:
        log.debug("[_send_telegram] DEV mode — suppressed (set DEV_TG_ENABLED=1 to enable locally)")
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


# ── Generic screener calibration check ───────────────────────────────────────


def _check_screener_calibration_due(
    screener_key: str,
    script_name: str,
    min_trades: int | None = None,
    cooldown_hours: float = _DEFAULT_COOLDOWN_HOURS,
    extra_args: str = "",
) -> None:
    """Alert when enough trades for *screener_key* have settled to warrant re-calibration.

    Queries paper_trades for settled rows (screener_pass=screener_key AND
    tiered_pnl_r IS NOT NULL).  If the count reaches *min_trades* AND
    _SP_MULT_TABLE[screener_key] is still 1.00 in paper_trader_bot.py, a
    warning is logged and a Slack / Telegram alert is emitted prompting the
    trader to run *script_name*.

    A *cooldown_hours*-hour cooldown (persisted to a per-screener JSON file)
    prevents the same alert from firing on every nightly run.  The cooldown is
    cleared automatically once the multiplier is updated away from 1.00.

    No automatic edits are made — human-in-the-loop is preserved for the
    actual multiplier update.

    When *min_trades* is ``None`` (the default) the threshold is resolved via
    ``resolve_calib_threshold(screener_key)`` from ``calib_threshold.py``,
    which checks ``CALIB_MIN_TRADES_<SCREENER_KEY_UPPER>`` first, then the
    ``SQUEEZE_CALIB_MIN_TRADES`` legacy alias for the squeeze screener, and
    finally falls back to the module default (30).  Pass an explicit integer
    only when you need to hard-override the env-var mechanism (e.g. from a
    dedicated wrapper that has its own backward-compatible env-var name).
    """
    if min_trades is None:
        min_trades = resolve_calib_threshold(screener_key)

    log.info("Checking %s calibration status …", screener_key)

    _state_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f".edgeiq_{screener_key}_calib_alert.json",
    )

    # ── Step 1: count settled trades for this screener ────────────────────────
    trade_count: int = 0
    try:
        import backend as _backend
        if not getattr(_backend, "supabase", None):
            log.warning(
                "Supabase client not initialised — skipping %s calibration check.",
                screener_key,
            )
            return
        resp = (
            _backend.supabase
            .table("paper_trades")
            .select("id", count="exact")
            .eq("screener_pass", screener_key)
            .not_.is_("tiered_pnl_r", "null")
            .execute()
        )
        trade_count = resp.count if resp.count is not None else len(resp.data or [])
        log.info(
            "%s settled trade count: %d (threshold: %d).",
            screener_key,
            trade_count,
            min_trades,
        )
    except Exception as _exc:
        log.warning("Could not query %s settled trade count: %s", screener_key, _exc)
        return

    if trade_count < min_trades:
        log.info(
            "%s calibration not yet due (%d / %d settled trades).",
            screener_key,
            trade_count,
            min_trades,
        )
        return

    # ── Step 2: read current SP_MULT_TABLE[screener_key] from trade_utils ───
    current_mult: float | None = None
    try:
        import trade_utils as _tu
        current_mult = _tu.SP_MULT_TABLE.get(screener_key)
    except Exception as _exc:
        log.warning(
            "Could not import SP_MULT_TABLE from trade_utils: %s", _exc
        )
        return

    if current_mult is None:
        log.warning(
            "Could not find '%s' entry in _SP_MULT_TABLE — skipping calibration check.",
            screener_key,
        )
        return

    log.info("Current _SP_MULT_TABLE['%s'] = %.2f.", screener_key, current_mult)

    if abs(current_mult - 1.00) > 0.001:
        # Already calibrated away from baseline — clear any stale cooldown state.
        log.info(
            "_SP_MULT_TABLE['%s'] = %.2f (not 1.00) — calibration already applied.",
            screener_key,
            current_mult,
        )
        try:
            os.remove(_state_file)
        except FileNotFoundError:
            pass
        except Exception as _exc:
            log.warning(
                "Could not remove %s calib state file: %s", screener_key, _exc
            )
        return

    # ── Step 3: enforce cooldown to avoid repeated nightly alerts ─────────────
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    try:
        with open(_state_file) as _sf:
            _state = json.load(_sf)
        _last_str = _state.get("last_alerted_utc", "")
        if _last_str:
            _last = datetime.datetime.fromisoformat(_last_str)
            if _last.tzinfo is None:
                _last = _last.replace(tzinfo=datetime.timezone.utc)
            _hours_since = (now_utc - _last).total_seconds() / 3600
            if _hours_since < cooldown_hours:
                log.info(
                    "%s calibration alert already sent %.1f h ago — "
                    "suppressing duplicate (cooldown: %.0f h).",
                    screener_key,
                    _hours_since,
                    cooldown_hours,
                )
                return
    except FileNotFoundError:
        pass  # First alert for this run — proceed.
    except Exception as _exc:
        log.warning(
            "Could not read %s calibration alert state: %s", screener_key, _exc
        )

    # ── Step 4: emit alert ────────────────────────────────────────────────────
    _script_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), script_name
    )
    _script_exists = os.path.isfile(_script_path)

    # Build the full run command, including any extra args (e.g. --pass squeeze).
    _run_cmd = " ".join(
        p for p in ["python", script_name, extra_args.strip(), "--apply"] if p
    )

    # Detect auto-generated stubs that still need manual completion before --apply.
    _is_stub = False
    if _script_exists:
        try:
            with open(_script_path, encoding="utf-8") as _sf:
                _is_stub = "AUTO-GENERATED STUB" in _sf.read(512)
        except OSError:
            pass

    if _script_exists:
        if _is_stub:
            plain_msg = (
                f"{screener_key} calibration due — {trade_count} settled {screener_key} trades found "
                f"(threshold: {min_trades}). "
                f"WARNING: {script_name} is an auto-generated stub — complete the TODO section before running --apply."
            )
            html_msg = (
                f"\U0001f4ca <b>{screener_key} calibration due</b>\n\n"
                f"{trade_count} settled {screener_key} trades found "
                f"(threshold: {min_trades}).\n\n"
                f"\u26a0\ufe0f <b>{script_name} is an auto-generated stub.</b> "
                f"Open it and complete the TODO section before running "
                f"<code>{_run_cmd}</code>."
            )
            log.warning(
                "%s CALIBRATION DUE — %d settled %s trades found "
                "(>= %d threshold, _SP_MULT_TABLE['%s'] still 1.00). "
                "WARNING: %s is an auto-generated stub — complete its TODO section before running --apply.",
                screener_key.upper(),
                trade_count,
                screener_key,
                min_trades,
                screener_key,
                script_name,
            )
        else:
            plain_msg = (
                f"{screener_key} calibration due — {trade_count} settled {screener_key} trades found "
                f"(threshold: {min_trades}). "
                f"Run: {_run_cmd}"
            )
            html_msg = (
                f"\U0001f4ca <b>{screener_key} calibration due</b>\n\n"
                f"{trade_count} settled {screener_key} trades found "
                f"(threshold: {min_trades}).\n\n"
                f"Run <code>{_run_cmd}</code> to apply the recommended "
                f"multiplier automatically."
            )
            log.warning(
                "%s CALIBRATION DUE — %d settled %s trades found "
                "(>= %d threshold, _SP_MULT_TABLE['%s'] still 1.00). "
                "Run: %s",
                screener_key.upper(),
                trade_count,
                screener_key,
                min_trades,
                screener_key,
                _run_cmd,
            )
    else:
        plain_msg = (
            f"{screener_key} calibration due — {trade_count} settled {screener_key} trades found "
            f"(threshold: {min_trades}). "
            f"No calibration script found yet — create {script_name} to proceed"
        )
        html_msg = (
            f"\U0001f4ca <b>{screener_key} calibration due</b>\n\n"
            f"{trade_count} settled {screener_key} trades found "
            f"(threshold: {min_trades}).\n\n"
            f"No calibration script found yet — create <code>{script_name}</code> to proceed."
        )
        log.warning(
            "%s CALIBRATION DUE — %d settled %s trades found "
            "(>= %d threshold, _SP_MULT_TABLE['%s'] still 1.00). "
            "No calibration script found yet — create %s to proceed.",
            screener_key.upper(),
            trade_count,
            screener_key,
            min_trades,
            screener_key,
            script_name,
        )
    _send_slack(plain_msg)
    _send_telegram(html_msg)

    # Persist alert timestamp so subsequent runs within the cooldown window are
    # suppressed.
    try:
        with open(_state_file, "w") as _sf:
            json.dump(
                {
                    "last_alerted_utc": now_utc.isoformat(),
                    "trade_count": trade_count,
                },
                _sf,
            )
    except Exception as _exc:
        log.warning(
            "Could not write %s calibration alert state: %s", screener_key, _exc
        )


# ── Per-screener cooldown helper ──────────────────────────────────────────────

_DEFAULT_CALIB_COOLDOWN_HOURS = 23


def _get_calib_cooldown_hours(screener_key: str) -> int:
    """Return the calibration cooldown (hours) for *screener_key*.

    Resolution order:
      1. ``CALIB_COOLDOWN_HOURS_<SCREENER_KEY>`` env var (upper-cased key,
         e.g. ``CALIB_COOLDOWN_HOURS_SQUEEZE``)
      2. ``CALIB_COOLDOWN_HOURS`` global env var (applies to all screeners)
      3. ``_DEFAULT_CALIB_COOLDOWN_HOURS`` (23)

    Invalid (non-positive-integer) values are skipped with a warning and the
    next level in the resolution order is tried.
    """
    env_key = f"CALIB_COOLDOWN_HOURS_{screener_key.upper().replace('-', '_')}"
    raw = os.getenv(env_key, "").strip()
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
        log.warning(
            "%s='%s' is not a valid positive integer; falling back to global/default.",
            env_key,
            raw,
        )

    global_raw = os.getenv("CALIB_COOLDOWN_HOURS", "").strip()
    if global_raw:
        try:
            v = int(global_raw)
            if v > 0:
                return v
        except ValueError:
            pass
        log.warning(
            "CALIB_COOLDOWN_HOURS='%s' is not a valid positive integer; using default %d h.",
            global_raw,
            _DEFAULT_CALIB_COOLDOWN_HOURS,
        )

    return _DEFAULT_CALIB_COOLDOWN_HOURS


# ── Squeeze calibration check ─────────────────────────────────────────────────

def _get_squeeze_calib_min_trades() -> int:
    """Return the minimum settled squeeze trade count that triggers a calibration alert.

    Resolution order:
      1. DB preference (Supabase app_config table, via
         backend.resolve_squeeze_calib_min_trades_effective).
      2. Env-var / default via ``resolve_calib_threshold("squeeze")`` from
         ``calib_threshold.py``, which checks ``CALIB_MIN_TRADES_SQUEEZE``
         then the ``SQUEEZE_CALIB_MIN_TRADES`` legacy alias, then defaults to 30.
    """
    try:
        import backend as _backend
        val = _backend.resolve_squeeze_calib_min_trades_effective()
        if isinstance(val, int) and val > 0:
            log.debug("Squeeze calib min-trades (effective): %d", val)
            return val
        log.warning(
            "DB squeeze calib min-trades value %r is not a positive integer; "
            "falling back to env var / default.",
            val,
        )
    except Exception as _exc:
        log.warning(
            "Could not resolve squeeze calib threshold via backend: %s — "
            "falling back to env var / default.",
            _exc,
        )

    return resolve_calib_threshold("squeeze")


def _check_squeeze_calibration_due() -> None:
    """Alert when enough squeeze trades have settled to warrant re-calibration.

    Delegates to _check_screener_calibration_due with squeeze-specific parameters.
    The threshold is read dynamically via _get_squeeze_calib_min_trades() so the
    SQUEEZE_CALIB_MIN_TRADES env var override (Task #1623) is preserved.
    """
    _check_screener_calibration_due(
        "squeeze",
        "calibrate_sp_mult.py",
        _get_squeeze_calib_min_trades(),
        _get_calib_cooldown_hours("squeeze"),
        extra_args="--pass squeeze",
    )


# ── Gap-down calibration check ────────────────────────────────────────────────


def _check_gap_down_calibration_due() -> None:
    """Alert when enough gap_down trades have settled to warrant re-calibration.

    Delegates to _check_screener_calibration_due with gap_down-specific parameters.
    The threshold is resolved via ``resolve_calib_threshold("gap_down")`` from
    ``calib_threshold.py``, which checks ``CALIB_MIN_TRADES_GAP_DOWN`` before
    falling back to the default of 30.
    """
    _check_screener_calibration_due(
        "gap_down",
        "calibrate_sp_mult.py",
        cooldown_hours=_get_calib_cooldown_hours("gap_down"),
        extra_args="--pass gap_down",
    )


# ── Auto-discover all uncalibrated screeners ──────────────────────────────────

def _check_all_uncalibrated_screeners() -> None:
    """Check calibration status for every screener whose multiplier is still 1.00×.

    Reads _SP_MULT_TABLE from paper_trader_bot at runtime so that any new
    screener added to that table is automatically covered — no manual wiring
    required in this file.

    Per-screener min_trades resolution order:
      • squeeze  — ``_get_squeeze_calib_min_trades()``, which checks DB
                   preference, then ``CALIB_MIN_TRADES_SQUEEZE``, then
                   ``SQUEEZE_CALIB_MIN_TRADES`` (backward-compatible), via
                   ``resolve_calib_threshold`` from calib_threshold.py.
      • any other key at 1.00× — ``resolve_calib_threshold(key)`` from
                   calib_threshold.py checks ``CALIB_MIN_TRADES_{KEY}``
                   (e.g. ``CALIB_MIN_TRADES_GAP_DOWN``) before the default of 30.

    Per-screener cooldown resolution (all keys):
      • ``_get_calib_cooldown_hours(key)`` — checks ``CALIB_COOLDOWN_HOURS_{KEY}``
        env var before falling back to ``_DEFAULT_CALIB_COOLDOWN_HOURS`` (23 h).

    A startup INFO line is emitted for every checked screener showing its
    effective threshold and the env var name to use for overrides.

    Missing calibration scripts are auto-generated as routing stubs so
    operators are unblocked immediately; a Slack/Telegram alert is sent
    (at most once per cooldown window) listing all newly stubbed screeners.
    """
    try:
        import trade_utils as _tu
        sp_table: dict[str, float] = _tu.SP_MULT_TABLE
    except Exception as _exc:
        log.warning(
            "Could not import SP_MULT_TABLE from trade_utils — "
            "skipping auto calibration checks: %s",
            _exc,
        )
        return

    _per_key_script: dict[str, str] = {
        "squeeze": "calibrate_sp_mult.py",
        "gap_down": "calibrate_sp_mult.py",
        "other": "calibrate_sp_mult.py",
        "trend": "calibrate_sp_mult.py",
    }
    _per_key_extra_args: dict[str, str] = {
        "squeeze": "--pass squeeze",
        "gap_down": "--pass gap_down",
        "other": "--pass other",
        "trend": "--pass trend",
    }

    _base_dir = os.path.dirname(os.path.abspath(__file__))

    # ── Startup: auto-generate stubs for any 1.00× screener missing its calibration script ──
    _missing_scripts: list[str] = []
    for key, mult in sp_table.items():
        if abs(mult - 1.00) > 0.001:
            continue
        script_name = _per_key_script.get(key, f"calibrate_{key}_mult.py")
        script_path = os.path.join(_base_dir, script_name)
        if not os.path.isfile(script_path):
            _stub_content = f'''\
"""
{script_name}  —  AUTO-GENERATED STUB
-----------------------------------------------------------------------
This file was created automatically because screener '{key}' was found
with a 1.00x multiplier but no calibration script existed on disk.

TODO: Review and complete this stub before running calibration.
  - If '{key}' should route through the unified calibrate_sp_mult.py
    (like 'squeeze' and 'gap_down'), verify the --pass value below and
    remove this notice.
  - If '{key}' needs its own bespoke calibration logic, replace the
    body of this script with that logic.
-----------------------------------------------------------------------
"""

import os
import sys
import subprocess

_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibrate_sp_mult.py")


def _build_forwarded(argv: list) -> list:
    """Return the command list that should be executed for the given argv."""
    if len(argv) > 1 and argv[1] == "--self-test":
        return [sys.executable, _script, "--self-test"]
    return [sys.executable, _script, "--pass", "{key}"] + argv[1:]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        print("Running {script_name} routing self-tests...")
        all_ok = True

        # Test 1: target script exists
        target_ok = os.path.isfile(_script)
        print(f"  {{\'OK  \' if target_ok else \'FAIL\'}} target script exists: {{_script}}")
        if not target_ok:
            all_ok = False

        # Test 2: normal invocation injects --pass {key}
        fwd = _build_forwarded(["{script_name}"])
        expected = [sys.executable, _script, "--pass", "{key}"]
        routing_ok = fwd == expected
        print(f"  {{\'OK  \' if routing_ok else \'FAIL\'}} bare invocation routes to --pass {key}: {{fwd[2:]}}")
        if not routing_ok:
            all_ok = False

        print()
        print("  NOTE  This is an AUTO-GENERATED STUB — complete the TODO in the docstring before relying on results.")
        if all_ok:
            print("Routing self-tests passed (stub unreviewed).")
        else:
            print("SELF-TEST FAILURES — routing may be broken.")
            sys.exit(1)
        sys.exit(0)

    sys.exit(subprocess.run(_build_forwarded(sys.argv)).returncode)
'''
            try:
                with open(script_path, "w", encoding="utf-8") as _fh:
                    _fh.write(_stub_content)
                log.info(
                    "Auto-generated calibration stub for new screener '%s': %s — "
                    "open that file and complete the TODO section before running calibration.",
                    key,
                    script_path,
                )
                _missing_scripts.append(script_name)
            except OSError as _write_exc:
                log.warning(
                    "Could not write calibration stub for screener '%s' to %s: %s. "
                    "Create calibrate_%s_mult.py manually before running calibration.",
                    key,
                    script_path,
                    _write_exc,
                    key,
                )
                _missing_scripts.append(script_name)

    # ── Send Slack + Telegram alert if any scripts were just auto-stubbed ────
    if _missing_scripts:
        _missing_script_state_file = os.path.join(
            _base_dir,
            ".edgeiq_missing_calib_script_alert.json",
        )
        _now_utc = datetime.datetime.now(datetime.timezone.utc)
        _send_missing_alert = True
        try:
            with open(_missing_script_state_file) as _msf:
                _ms_state = json.load(_msf)
            _last_sent_iso = _ms_state.get("last_sent_utc", "")
            if _last_sent_iso:
                _last_sent_dt = datetime.datetime.fromisoformat(_last_sent_iso)
                if _last_sent_dt.tzinfo is None:
                    _last_sent_dt = _last_sent_dt.replace(tzinfo=datetime.timezone.utc)
                _hours_since = (_now_utc - _last_sent_dt).total_seconds() / 3600
                if _hours_since < _DEFAULT_CALIB_COOLDOWN_HOURS:
                    log.info(
                        "Missing-calibration-script alert suppressed — sent %.1f h ago "
                        "(cooldown: %.0f h).",
                        _hours_since,
                        _DEFAULT_CALIB_COOLDOWN_HOURS,
                    )
                    _send_missing_alert = False
        except FileNotFoundError:
            pass
        except Exception as _ms_err:
            log.warning("Could not read missing-script alert state file: %s", _ms_err)

        if _send_missing_alert:
            _bullet_list = "\n".join(f"  • {s}" for s in _missing_scripts)
            _plain_msg = (
                "⚠️ Missing calibration script(s) detected.\n"
                "The following 1.00× screener(s) have no calibration script on disk:\n"
                f"{_bullet_list}\n"
                "Create the corresponding calibrate_<key>_mult.py file(s) before running calibration."
            )
            import html as _html_mod
            _html_msg = (
                "⚠️ <b>Missing calibration script(s) detected.</b>\n"
                "The following 1.00× screener(s) have no calibration script on disk:\n"
                + "\n".join(f"  • <code>{_html_mod.escape(s)}</code>" for s in _missing_scripts)
                + "\nCreate the corresponding <code>calibrate_&lt;key&gt;_mult.py</code> "
                "file(s) before running calibration."
            )
            _send_slack(_plain_msg)
            _send_telegram(_html_msg)
            try:
                with open(_missing_script_state_file, "w") as _msf:
                    json.dump({"last_sent_utc": _now_utc.isoformat()}, _msf)
            except Exception as _ms_err:
                log.warning("Could not write missing-script alert state file: %s", _ms_err)

    for key, mult in sp_table.items():
        if abs(mult - 1.00) > 0.001:
            continue
        script = _per_key_script.get(key, f"calibrate_{key}_mult.py")
        min_trades = (
            _get_squeeze_calib_min_trades() if key == "squeeze"
            else resolve_calib_threshold(key)
        )
        cooldown = _get_calib_cooldown_hours(key)
        if key == "squeeze":
            log.info(
                "Screener 'squeeze': calibration threshold = %d trades "
                "(env override: CALIB_MIN_TRADES_SQUEEZE or SQUEEZE_CALIB_MIN_TRADES)",
                min_trades,
            )
        else:
            log.info(
                "Screener '%s': calibration threshold = %d trades "
                "(env override key: CALIB_MIN_TRADES_%s)",
                key,
                min_trades,
                key.upper().replace("-", "_"),
            )
        _check_screener_calibration_due(
            key, script, min_trades, cooldown,
            extra_args=_per_key_extra_args.get(key, ""),
        )


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


# ── Weekly adaptive TP calibration ───────────────────────────────────────────


def run_tp_calibration() -> None:
    """Run calibrate_adaptive_mgmt.py --apply and send a Telegram summary.

    Intended to be called once a week (Sunday night / Monday 00:05 ET).
    The function:
      1. Invokes calibrate_adaptive_mgmt.py --apply as a subprocess.
      2. Parses key values from its stdout (trade count, old/new multiplier,
         or the "not enough trades" message).
      3. Sends a Telegram notification summarising the outcome.
      4. Records the run timestamp to .edgeiq_tp_calib_run.json so callers
         can check when it last ran.
    """
    import re as _re

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "calibrate_adaptive_mgmt.py")
    cmd = [sys.executable, script, "--apply"]

    log.info("─── Weekly TP calibration ────────────────────────────────────")
    log.info("Running: %s", " ".join(cmd))
    start = time.monotonic()

    captured_output = ""
    exit_code = 0
    exception_msg = ""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        captured_output = result.stdout or ""
        exit_code = result.returncode
        if captured_output:
            sys.stdout.write(captured_output)
            sys.stdout.flush()
        if exit_code == 0:
            log.info("TP calibration completed (exit 0, %.0fs).", time.monotonic() - start)
        else:
            log.warning("TP calibration exited with code %d.", exit_code)
    except Exception as exc:
        exception_msg = str(exc)
        log.error("TP calibration raised an exception: %s", exc)

    elapsed_s = time.monotonic() - start
    run_date = _et_now().strftime("%Y-%m-%d")

    # ── Persist run timestamp ─────────────────────────────────────────────────
    try:
        with open(_TP_CALIB_STATE_FILE, "w") as _sf:
            json.dump({"last_run_utc": datetime.datetime.utcnow().isoformat(),
                       "exit_code": exit_code}, _sf)
    except Exception as _se:
        log.warning("Could not write TP calibration state file: %s", _se)

    # ── Parse key values from script output ───────────────────────────────────
    # "Only N settled adaptive trade(s) found (minimum required: 50)."
    _m_low = _re.search(r"Only (\d+) settled adaptive trade", captured_output)
    # "Found N settled adaptive row(s) total; M classified as TP_RAISED"
    _m_found = _re.search(r"Found (\d+) settled adaptive row", captured_output)
    # "adaptive_exits.json updated: tp_raise_mult OLD → NEW"
    _m_update = _re.search(
        r"adaptive_exits\.json updated:\s*tp_raise_mult\s+([\d.]+)\s*\u2192\s*([\d.]+)",
        captured_output,
    )
    # Fallback arrow written as ASCII
    if not _m_update:
        _m_update = _re.search(
            r"adaptive_exits\.json updated:\s*tp_raise_mult\s+([\d.]+)\s*-+>\s*([\d.]+)",
            captured_output,
        )

    # ── Build Telegram message ────────────────────────────────────────────────
    if exception_msg:
        tg_msg = (
            f"\u26a0\ufe0f <b>Weekly TP Calibration — ERROR</b>\n"
            f"Date: {run_date}\n"
            f"<b>Exception:</b> <code>{html.escape(exception_msg[:300])}</code>"
        )
    elif exit_code != 0 and not _m_low:
        tail = "\n".join(captured_output.splitlines()[-15:]).strip()
        tg_msg = (
            f"\u26a0\ufe0f <b>Weekly TP Calibration — FAILED</b>\n"
            f"Date: {run_date}  |  Exit code: {exit_code}\n"
            f"<code>{html.escape(tail[:600])}</code>"
        )
    elif _m_low:
        n_found = int(_m_low.group(1))
        tg_msg = (
            f"\u23f8 <b>Weekly TP Calibration — skipped (not enough data)</b>\n"
            f"Date: {run_date}\n"
            f"Only <b>{n_found}</b> settled adaptive trade(s) found "
            f"(minimum required: 50).\n"
            f"Calibration will run automatically once enough trades have settled."
        )
    elif _m_update:
        old_mult = _m_update.group(1)
        new_mult = _m_update.group(2)
        n_trades = int(_m_found.group(1)) if _m_found else "?"
        changed = abs(float(old_mult) - float(new_mult)) > 1e-6
        icon = "\u2705" if changed else "\u2139\ufe0f"
        change_line = (
            f"tp_raise_mult: <b>{old_mult} \u2192 {new_mult}</b>"
            if changed
            else f"tp_raise_mult: <b>{new_mult}</b> (no change)"
        )
        tg_msg = (
            f"{icon} <b>Weekly TP Calibration</b>\n"
            f"Date: {run_date}\n"
            f"{change_line}\n"
            f"Trades used: <b>{n_trades}</b> settled adaptive rows\n"
            f"adaptive_exits.json updated."
        )
    else:
        # Calibration ran but we couldn't parse expected lines — show tail.
        tail = "\n".join(captured_output.splitlines()[-10:]).strip()
        tg_msg = (
            f"\u2139\ufe0f <b>Weekly TP Calibration</b>\n"
            f"Date: {run_date}  |  Elapsed: {elapsed_s:.0f}s\n"
            f"<code>{html.escape(tail[:600])}</code>"
        )

    _send_telegram(tg_msg)
    log.info("TP calibration Telegram summary sent.")


def _should_run_tp_calibration() -> bool:
    """Return True if the weekly TP calibration should run now.

    Fires when the current ET day is Monday (weekday() == 0), which corresponds
    to the Sunday-night 00:05 ET scheduled run.  A state file prevents it from
    running more than once per week: if the last run was within the past 6 days
    the function returns False.
    """
    now_et = _et_now()
    if now_et.weekday() != 0:  # 0 = Monday (Sunday night run)
        log.info(
            "TP calibration check: today is %s — not Sunday night, skipping.",
            now_et.strftime("%A"),
        )
        return False

    try:
        with open(_TP_CALIB_STATE_FILE) as _sf:
            _state = json.load(_sf)
        _last_run_str = _state.get("last_run_utc", "")
        if _last_run_str:
            _last_run = datetime.datetime.fromisoformat(_last_run_str)
            _age_hours = (datetime.datetime.utcnow() - _last_run).total_seconds() / 3600
            if _age_hours < 6 * 24:  # ran within the last 6 days
                log.info(
                    "TP calibration already ran %.1f h ago — skipping duplicate run.",
                    _age_hours,
                )
                return False
    except FileNotFoundError:
        pass  # No state file → first run; proceed.
    except Exception as _e:
        log.warning("Could not read TP calibration state file: %s", _e)

    return True


def _should_run_tp_preview() -> bool:
    """Return True if the Friday-night TP calibration preview should run now.

    Fires when the current ET day is Saturday (weekday() == 5), which
    corresponds to the Friday-night 00:05 ET scheduled run.  A separate
    state file prevents it from running more than once per week.
    """
    now_et = _et_now()
    if now_et.weekday() != 5:  # 5 = Saturday (Friday-night run)
        log.info(
            "TP preview check: today is %s — not Friday night, skipping.",
            now_et.strftime("%A"),
        )
        return False

    try:
        with open(_TP_PREVIEW_STATE_FILE) as _sf:
            _state = json.load(_sf)
        _last_run_str = _state.get("last_run_utc", "")
        if _last_run_str:
            _last_run = datetime.datetime.fromisoformat(_last_run_str)
            _age_hours = (datetime.datetime.utcnow() - _last_run).total_seconds() / 3600
            if _age_hours < 6 * 24:  # ran within the last 6 days
                log.info(
                    "TP preview already ran %.1f h ago — skipping duplicate run.",
                    _age_hours,
                )
                return False
    except FileNotFoundError:
        pass  # No state file → first run; proceed.
    except Exception as _e:
        log.warning("Could not read TP preview state file: %s", _e)

    return True


def run_tp_calibration_preview() -> None:
    """Run calibrate_adaptive_mgmt.py in dry-run mode and send a Telegram preview.

    Intended to be called once a week on Friday night (Sat 00:05 ET), giving
    traders a heads-up about what the Sunday-night automatic calibration would
    apply.  The function:
      1. Reads the current tp_raise_mult from adaptive_exits.json (for context).
      2. Invokes calibrate_adaptive_mgmt.py (no --apply) as a subprocess.
      3. Parses key values from its stdout (trade count, recommended multiplier).
      4. Sends a Telegram message framed as a preview / dry-run summary.
      5. Records the run timestamp to .edgeiq_tp_preview_run.json.
    """
    import re as _re

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "calibrate_adaptive_mgmt.py")
    cmd = [sys.executable, script]  # no --apply → dry-run mode

    log.info("─── Friday-night TP calibration preview ──────────────────────")
    log.info("Running: %s", " ".join(cmd))
    start = time.monotonic()

    captured_output = ""
    exit_code = 0
    exception_msg = ""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        captured_output = result.stdout or ""
        exit_code = result.returncode
        if captured_output:
            sys.stdout.write(captured_output)
            sys.stdout.flush()
        if exit_code == 0:
            log.info("TP preview dry-run completed (exit 0, %.0fs).", time.monotonic() - start)
        else:
            log.warning("TP preview dry-run exited with code %d.", exit_code)
    except Exception as exc:
        exception_msg = str(exc)
        log.error("TP preview dry-run raised an exception: %s", exc)

    elapsed_s = time.monotonic() - start
    run_date = _et_now().strftime("%Y-%m-%d")

    # ── Persist run timestamp ─────────────────────────────────────────────────
    try:
        with open(_TP_PREVIEW_STATE_FILE, "w") as _sf:
            json.dump({"last_run_utc": datetime.datetime.utcnow().isoformat(),
                       "exit_code": exit_code}, _sf)
    except Exception as _se:
        log.warning("Could not write TP preview state file: %s", _se)

    # ── Read current multiplier from adaptive_exits.json ─────────────────────
    _adaptive_exits_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "adaptive_exits.json")
    try:
        with open(_adaptive_exits_path) as _af:
            _cfg = json.load(_af)
        current_mult = _cfg.get("tp_raise_mult", 0.50)
    except Exception:
        current_mult = 0.50

    # ── Parse key values from script output ───────────────────────────────────
    # "Only N settled adaptive trade(s) found (minimum required: 50)."
    _m_low = _re.search(r"Only (\d+) settled adaptive trade", captured_output)
    # "Found N settled adaptive row(s) total; M classified as TP_RAISED"
    _m_found = _re.search(r"Found (\d+) settled adaptive row", captured_output)
    # "Recommendation: tp_raise_mult = X.XX  (was Y.YY)"
    _m_rec = _re.search(r"Recommendation:\s*tp_raise_mult\s*=\s*([\d.]+)", captured_output)

    # ── Build Telegram message ────────────────────────────────────────────────
    if exception_msg:
        tg_msg = (
            f"\u26a0\ufe0f <b>Friday TP Calibration Preview — ERROR</b>\n"
            f"Date: {run_date}\n"
            f"<b>Exception:</b> <code>{html.escape(exception_msg[:300])}</code>"
        )
    elif exit_code != 0 and not _m_low:
        tail = "\n".join(captured_output.splitlines()[-15:]).strip()
        tg_msg = (
            f"\u26a0\ufe0f <b>Friday TP Calibration Preview — FAILED</b>\n"
            f"Date: {run_date}  |  Exit code: {exit_code}\n"
            f"<code>{html.escape(tail[:600])}</code>"
        )
    elif _m_low:
        n_found = int(_m_low.group(1))
        tg_msg = (
            f"\u23f8 <b>TP Calibration Preview (Friday dry-run)</b>\n"
            f"Date: {run_date}\n"
            f"Only <b>{n_found}</b> settled adaptive trade(s) found "
            f"(minimum required: 50).\n"
            f"No recommendation yet — the Sunday calibration will also skip.\n"
            f"Current multiplier: <b>tp_raise_mult = {current_mult:.2f}</b> (unchanged)."
        )
    elif _m_rec:
        candidate = float(_m_rec.group(1))
        n_trades = int(_m_found.group(1)) if _m_found else "?"
        changed = abs(candidate - current_mult) > 1e-6
        change_line = (
            f"tp_raise_mult: <b>{current_mult:.2f} \u2192 {candidate:.2f}</b>"
            if changed
            else f"tp_raise_mult: <b>{candidate:.2f}</b> (no change expected)"
        )
        tg_msg = (
            f"\U0001f50d <b>TP Calibration Preview (Friday dry-run)</b>\n"
            f"Date: {run_date}\n"
            f"This is a preview of Sunday\u2019s automatic calibration.\n"
            f"{change_line}\n"
            f"Trades used: <b>{n_trades}</b> settled adaptive rows\n"
            f"No changes written \u2014 Sunday\u2019s run will apply this unless vetoed."
        )
    else:
        tail = "\n".join(captured_output.splitlines()[-10:]).strip()
        tg_msg = (
            f"\U0001f50d <b>TP Calibration Preview (Friday dry-run)</b>\n"
            f"Date: {run_date}  |  Elapsed: {elapsed_s:.0f}s\n"
            f"<code>{html.escape(tail[:600])}</code>"
        )

    _send_telegram(tg_msg)
    log.info("TP calibration preview Telegram summary sent.")


# ── Pending row resolver ──────────────────────────────────────────────────────


def _get_pending_alert_threshold() -> int:
    """Return the configured threshold above which unresolved Pending rows trigger an alert.

    Read from PENDING_ROW_ALERT_THRESHOLD (default 50).  Set to 0 to disable
    the pending-row alert entirely.
    """
    raw = os.getenv("PENDING_ROW_ALERT_THRESHOLD", "").strip()
    if raw:
        try:
            v = int(raw)
            if v >= 0:
                return v
        except ValueError:
            log.warning(
                "PENDING_ROW_ALERT_THRESHOLD='%s' is not a valid integer; "
                "using default %d.",
                raw,
                _DEFAULT_PENDING_ALERT_THRESHOLD,
            )
    return _DEFAULT_PENDING_ALERT_THRESHOLD


def _pending_maybe_send_recovery() -> None:
    """Send a one-time recovery notification if a previous pending-row alert was on record."""
    try:
        with open(_PENDING_RESOLVER_STATE_FILE) as _sf:
            _state = json.load(_sf)
        if _state.get("last_alerted_utc"):
            log.info("Pending resolver: all clear — sending recovery notification.")
            _send_slack(
                "[EdgeIQ] Pending row resolver: all Pending rows have been resolved. "
                "No further alerts will fire until new rows accumulate."
            )
            _send_telegram(
                "\u2705 <b>Pending row resolver — all clear</b>\n"
                "All Pending rows have been resolved. No further alerts will fire "
                "until new rows accumulate."
            )
        os.remove(_PENDING_RESOLVER_STATE_FILE)
    except FileNotFoundError:
        pass
    except Exception as _e:
        log.warning("Could not clear pending resolver alert state: %s", _e)


def run_pending_resolver(date_from: str = "", date_to: str = "") -> None:
    """Resolve any backtest_sim_runs rows whose actual_outcome = 'Pending'.

    Imports backfill_pending_sim_rows in-process and calls backfill_pending().
    This is intentionally run *after* run_backfill() so that any Pending rows
    left behind by a partial batch_backtest.py run are caught within the same
    nightly window rather than silently accumulating.

    After the resolver finishes, the remaining Pending count is checked.
    If it exceeds PENDING_ROW_ALERT_THRESHOLD (default 50) a Slack + Telegram
    alert is fired so the operator knows rows need attention.  A 23-hour
    cooldown (persisted to _PENDING_RESOLVER_STATE_FILE) prevents the same
    alert from firing on every consecutive nightly run.

    If the remaining count is zero and a previous alert was on record, a
    one-time recovery notification is sent before clearing the state.
    """
    log.info("─── Pending row resolver ─────────────────────────────────────")
    threshold = _get_pending_alert_threshold()

    # ── Import the resolver module ────────────────────────────────────────────
    try:
        import backfill_pending_sim_rows as _bpr
    except ImportError as _ie:
        log.error(
            "Could not import backfill_pending_sim_rows — pending resolver skipped: %s",
            _ie,
        )
        return

    # ── Count Pending rows before running ─────────────────────────────────────
    count_before = _bpr.count_pending(date_from=date_from, date_to=date_to)
    if count_before == 0:
        log.info("Pending resolver: no Pending rows found — nothing to do.")
        _pending_maybe_send_recovery()
        return
    if count_before < 0:
        log.warning("Pending resolver: pre-run count query failed — skipping resolver run.")
        return

    log.info(
        "Pending resolver: %d Pending row(s) found — running backfill_pending().",
        count_before,
    )

    # ── Run the resolver ──────────────────────────────────────────────────────
    rate_limit = "--no-ratelimit" not in sys.argv
    resolver_stats: dict = {}
    try:
        resolver_stats = _bpr.backfill_pending(
            dry_run=False,
            rate_limit=rate_limit,
            date_from=date_from,
            date_to=date_to,
        )
    except Exception as _exc:
        log.error("Pending resolver raised an exception: %s", _exc)

    resolved = resolver_stats.get("updated", 0)
    non_dir  = resolver_stats.get("non_directional", 0)
    errors   = resolver_stats.get("errors", 0)
    log.info(
        "Pending resolver finished: resolved=%d, non_directional=%d, errors=%d.",
        resolved,
        non_dir,
        errors,
    )

    # ── Count Pending rows after running ──────────────────────────────────────
    count_after = _bpr.count_pending(date_from=date_from, date_to=date_to)
    if count_after < 0:
        log.warning(
            "Pending resolver: post-run count query failed — cannot assess remaining rows."
        )
        return

    log.info(
        "Pending resolver: %d row(s) before → %d row(s) after (%d resolved this run).",
        count_before,
        count_after,
        count_before - count_after,
    )

    # ── Recovery notification if resolved to zero ──────────────────────────────
    if count_after == 0:
        log.info("Pending resolver: all Pending rows resolved.")
        _pending_maybe_send_recovery()
        return

    # ── Alert if remaining count exceeds threshold ─────────────────────────────
    if threshold == 0:
        log.info(
            "Pending resolver: %d row(s) remain but PENDING_ROW_ALERT_THRESHOLD=0 "
            "— alert suppressed.",
            count_after,
        )
        return

    if count_after <= threshold:
        log.info(
            "Pending resolver: %d row(s) remain (threshold: %d) — within acceptable range.",
            count_after,
            threshold,
        )
        return

    # Remaining count exceeds threshold — check 23-hour cooldown before alerting.
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    try:
        with open(_PENDING_RESOLVER_STATE_FILE) as _sf:
            _pr_state = json.load(_sf)
        _last_str = _pr_state.get("last_alerted_utc", "")
        if _last_str:
            _last_dt = datetime.datetime.fromisoformat(_last_str)
            if _last_dt.tzinfo is None:
                _last_dt = _last_dt.replace(tzinfo=datetime.timezone.utc)
            _hours_ago = (now_utc - _last_dt).total_seconds() / 3600
            if _hours_ago < _DEFAULT_COOLDOWN_HOURS:
                log.info(
                    "Pending resolver alert already sent %.1f h ago — suppressing "
                    "duplicate (cooldown: %d h).",
                    _hours_ago,
                    _DEFAULT_COOLDOWN_HOURS,
                )
                return
    except FileNotFoundError:
        pass
    except Exception as _e:
        log.warning("Could not read pending resolver alert state: %s", _e)

    run_date = _et_now().strftime("%Y-%m-%d")
    plain_msg = (
        f"[EdgeIQ] {count_after} unresolved Pending rows remain after nightly resolver\n"
        f"Date: {run_date}\n"
        f"Before: {count_before}  After: {count_after}  Resolved this run: {resolved}\n"
        f"Threshold: {threshold}  Errors: {errors}\n"
        f"Re-run backfill_pending_sim_rows.py manually or investigate Alpaca bar-fetch errors.\n"
        f"Set PENDING_ROW_ALERT_THRESHOLD to adjust this threshold (0 to disable)."
    )
    html_msg = (
        f"\u26a0\ufe0f <b>Pending row resolver — unresolved rows remain</b>\n"
        f"Date: {run_date}\n"
        f"<b>Before:</b> {count_before}  <b>After:</b> {count_after}  "
        f"<b>Resolved this run:</b> {resolved}\n"
        f"<b>Threshold:</b> {threshold}  <b>Errors:</b> {errors}\n"
        f"Re-run <code>backfill_pending_sim_rows.py</code> manually or investigate "
        f"Alpaca bar-fetch errors.\n"
        f"<i>Set PENDING_ROW_ALERT_THRESHOLD to adjust this threshold (0 to disable).</i>"
    )
    log.warning(
        "Pending resolver alert: %d row(s) remain after resolver run (threshold: %d).",
        count_after,
        threshold,
    )
    _send_slack(plain_msg)
    _send_telegram(html_msg)

    # Persist alert timestamp for cooldown deduplication.
    try:
        with open(_PENDING_RESOLVER_STATE_FILE, "w") as _sf:
            json.dump(
                {
                    "last_alerted_utc": now_utc.isoformat(),
                    "count_after": count_after,
                },
                _sf,
            )
    except Exception as _e:
        log.warning("Could not write pending resolver alert state: %s", _e)


# ── Morning TCS floor auto-recalibration ──────────────────────────────────────

_FILTER_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "filter_config.json"
)

_MORNING_TCS_WIN_RATE_THRESHOLD = 0.75
_MORNING_TCS_CALIB_DEFAULT_DAYS = 90
_MORNING_TCS_CALIB_DEFAULT_MIN_TRADES = 50


def _query_morning_tcs_band(
    backend_mod,
    tcs_lo: int,
    tcs_hi: int,
    date_from_str: str,
    limit: int = 5000,
) -> tuple:
    """Return (total_settled, win_count) for a morning TCS band over the trailing window.

    Queries backtest_sim_runs for rows where scan_type='morning',
    tcs between tcs_lo and tcs_hi (inclusive), actual_outcome is not null
    and not 'Pending', and sim_date >= date_from_str.

    Returns (-1, -1) on any query error.
    """
    try:
        resp = (
            backend_mod.supabase
            .table("backtest_sim_runs")
            .select("actual_outcome,win_loss")
            .eq("scan_type", "morning")
            .gte("tcs", tcs_lo)
            .lte("tcs", tcs_hi)
            .gte("sim_date", date_from_str)
            .not_.is_("actual_outcome", "null")
            .neq("actual_outcome", "Pending")
            .limit(limit)
            .execute()
        )
        rows = resp.data or []
        total = len(rows)
        wins  = sum(1 for r in rows if str(r.get("win_loss", "")).strip() == "Win")
        if total >= limit:
            log.warning(
                "Morning TCS band %d-%d result set hit limit=%d — consider raising limit.",
                tcs_lo, tcs_hi, limit,
            )
        return total, wins
    except Exception as exc:
        log.warning(
            "Could not query backtest_sim_runs for morning TCS %d-%d: %s",
            tcs_lo, tcs_hi, exc,
        )
        return -1, -1


def _recalibrate_morning_tcs_floor() -> None:
    """Auto-update morning_tcs_min in filter_config.json based on recent backtest WR.

    Logic (all over the trailing window set by MORNING_TCS_CALIB_DAYS, default 90 days):

    1. Query backtest_sim_runs for morning TCS 55-59 settled rows and compute WR.
    2. Query backtest_sim_runs for morning TCS 50-54 settled rows and compute WR.
    3. Decision:
       a. If 55-59 WR < 75%  -> raise floor to 60 and send a Telegram alert.
       b. If 55-59 WR >= 75% AND 50-54 WR >= 75%  -> lower floor to 50 and send alert.
       c. If 55-59 WR >= 75% but 50-54 WR < 75%  -> keep floor at 55 (no change needed).
    4. When the floor changes, filter_config.json is updated with the new value and a note.
    5. If either band has fewer than MORNING_TCS_CALIB_MIN_TRADES (default 50) settled rows
       the decision for that band is skipped (insufficient data).

    No-op guard: if the computed new floor equals the value already in filter_config.json
    the function returns early without writing the file or sending an alert.
    """
    log.info("─── Morning TCS floor recalibration ──────────────────────────")

    try:
        trailing_days = int(os.getenv("MORNING_TCS_CALIB_DAYS", str(_MORNING_TCS_CALIB_DEFAULT_DAYS)))
    except (ValueError, TypeError):
        trailing_days = _MORNING_TCS_CALIB_DEFAULT_DAYS
        log.warning("Invalid MORNING_TCS_CALIB_DAYS — using default %d days.", trailing_days)

    try:
        min_trades = int(os.getenv("MORNING_TCS_CALIB_MIN_TRADES", str(_MORNING_TCS_CALIB_DEFAULT_MIN_TRADES)))
    except (ValueError, TypeError):
        min_trades = _MORNING_TCS_CALIB_DEFAULT_MIN_TRADES
        log.warning("Invalid MORNING_TCS_CALIB_MIN_TRADES — using default %d.", min_trades)

    date_from_dt  = datetime.datetime.utcnow() - datetime.timedelta(days=trailing_days)
    date_from_str = date_from_dt.strftime("%Y-%m-%d")
    log.info(
        "Morning TCS floor check: trailing window %d days (from %s), min_trades=%d.",
        trailing_days, date_from_str, min_trades,
    )

    try:
        import backend as _backend
        if not getattr(_backend, "supabase", None):
            log.warning("Supabase client not initialised — skipping morning TCS recalibration.")
            return
    except Exception as exc:
        log.warning("Could not import backend for morning TCS recalibration: %s", exc)
        return

    total_5559, wins_5559 = _query_morning_tcs_band(_backend, 55, 59, date_from_str)
    total_5054, wins_5054 = _query_morning_tcs_band(_backend, 50, 54, date_from_str)

    if total_5559 < 0:
        log.warning("Morning TCS 55-59 query failed — aborting recalibration.")
        return

    if total_5559 < min_trades:
        log.info(
            "Morning TCS 55-59 band: only %d settled row(s) in trailing %d days "
            "(min_trades=%d) — insufficient data, skipping.",
            total_5559, trailing_days, min_trades,
        )
        return

    wr_5559 = wins_5559 / total_5559
    log.info(
        "Morning TCS 55-59: %d settled, %d wins, WR=%.1f%% (threshold: %.0f%%).",
        total_5559, wins_5559, wr_5559 * 100, _MORNING_TCS_WIN_RATE_THRESHOLD * 100,
    )

    has_5054_data = total_5054 >= 0 and total_5054 >= min_trades
    wr_5054 = None
    if total_5054 < 0:
        log.warning("Morning TCS 50-54 query failed — treating 50-54 band as unavailable.")
    elif total_5054 < min_trades:
        log.info(
            "Morning TCS 50-54 band: only %d settled row(s) (min_trades=%d) — insufficient data.",
            total_5054, min_trades,
        )
    else:
        wr_5054 = wins_5054 / total_5054
        log.info(
            "Morning TCS 50-54: %d settled, %d wins, WR=%.1f%% (threshold: %.0f%%).",
            total_5054, wins_5054, wr_5054 * 100, _MORNING_TCS_WIN_RATE_THRESHOLD * 100,
        )

    now_et   = _et_now()
    run_date = now_et.strftime("%Y-%m-%d")

    if wr_5559 < _MORNING_TCS_WIN_RATE_THRESHOLD:
        new_floor = 60
        decision  = "raise"
        note = (
            f"Morning TCS floor raised 55->60 on {run_date} by nightly recalibration "
            f"(MORNING_TCS_CALIB_DAYS={trailing_days}). "
            f"TCS 55-59: {wr_5559*100:.1f}% WR / {total_5559} trades -- "
            f"below {_MORNING_TCS_WIN_RATE_THRESHOLD*100:.0f}% threshold."
        )
        tg_msg = (
            f"\u26a0\ufe0f <b>Morning TCS floor raised: 55 \u2192 60</b>\n"
            f"Date: {run_date}\n"
            f"TCS 55-59 WR dropped to <b>{wr_5559*100:.1f}%</b> "
            f"({wins_5559}/{total_5559} trades, trailing {trailing_days} days).\n"
            f"Threshold: {_MORNING_TCS_WIN_RATE_THRESHOLD*100:.0f}% \u2014 floor raised back to 60.\n"
            f"<i>filter_config.json updated automatically.</i>"
        )
    elif has_5054_data and wr_5054 is not None and wr_5054 >= _MORNING_TCS_WIN_RATE_THRESHOLD:
        new_floor = 50
        decision  = "lower"
        note = (
            f"Morning TCS floor lowered 55->50 on {run_date} by nightly recalibration "
            f"(MORNING_TCS_CALIB_DAYS={trailing_days}). "
            f"TCS 55-59: {wr_5559*100:.1f}% WR / {total_5559} trades, "
            f"TCS 50-54: {wr_5054*100:.1f}% WR / {total_5054} trades -- "
            f"both bands clear {_MORNING_TCS_WIN_RATE_THRESHOLD*100:.0f}% threshold."
        )
        tg_msg = (
            f"\u2705 <b>Morning TCS floor lowered: 55 \u2192 50</b>\n"
            f"Date: {run_date}\n"
            f"TCS 55-59: <b>{wr_5559*100:.1f}%</b> WR ({total_5559} trades)  "
            f"TCS 50-54: <b>{wr_5054*100:.1f}%</b> WR ({total_5054} trades)\n"
            f"Both bands clear {_MORNING_TCS_WIN_RATE_THRESHOLD*100:.0f}% \u2014 floor lowered to 50.\n"
            f"<i>filter_config.json updated automatically.</i>"
        )
    else:
        new_floor = 55
        decision  = "hold"
        if has_5054_data and wr_5054 is not None:
            log.info(
                "Morning TCS floor holds at 55 — 55-59 WR %.1f%% (ok), "
                "50-54 WR %.1f%% (below threshold).",
                wr_5559 * 100, wr_5054 * 100,
            )
        else:
            log.info(
                "Morning TCS floor holds at 55 — 55-59 WR %.1f%% (ok), "
                "50-54 band has insufficient data.",
                wr_5559 * 100,
            )
        note   = None
        tg_msg = None

    try:
        with open(_FILTER_CONFIG_PATH) as _fcf:
            cfg = json.load(_fcf)
    except Exception as exc:
        log.warning(
            "Could not read filter_config.json for morning TCS update — aborting to avoid "
            "overwriting a partially-parsed config: %s",
            exc,
        )
        return

    current_floor = int(cfg.get("morning_tcs_min", 60))

    if decision == "hold" and current_floor == new_floor:
        log.info("Morning TCS floor unchanged at %d — no update needed.", new_floor)
        return

    if decision != "hold" and current_floor == new_floor:
        log.info(
            "Morning TCS floor already at %d — no change needed "
            "(55-59 WR=%.1f%%, decision=%s).",
            new_floor, wr_5559 * 100, decision,
        )
        return

    log.info(
        "Morning TCS floor: %d -> %d (decision=%s, 55-59 WR=%.1f%%).",
        current_floor, new_floor, decision, wr_5559 * 100,
    )

    cfg["morning_tcs_min"] = new_floor
    if note:
        cfg["_morning_tcs_min_note"] = note
    try:
        with open(_FILTER_CONFIG_PATH, "w") as _fcf:
            json.dump(cfg, _fcf, indent=2)
        log.info("filter_config.json updated: morning_tcs_min=%d.", new_floor)
    except Exception as exc:
        log.warning("Could not write filter_config.json with new morning TCS floor: %s", exc)
        return

    try:
        with open(_MORNING_TCS_CALIB_STATE_FILE, "w") as _sf:
            json.dump(
                {
                    "last_run_utc":  datetime.datetime.utcnow().isoformat(),
                    "floor_set":     new_floor,
                    "decision":      decision,
                    "wr_5559":       round(wr_5559, 4),
                    "n_5559":        total_5559,
                    "wr_5054":       round(wr_5054, 4) if wr_5054 is not None else None,
                    "n_5054":        total_5054 if has_5054_data else None,
                    "trailing_days": trailing_days,
                },
                _sf,
                indent=2,
            )
    except Exception as exc:
        log.warning("Could not write morning TCS calib state file: %s", exc)

    if tg_msg:
        _send_telegram(tg_msg)
        log.info("Morning TCS floor recalibration Telegram alert sent.")


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

    _squeeze_min = _get_squeeze_calib_min_trades()
    log.info(
        "Squeeze calibration threshold: %d settled trades "
        "(set CALIB_MIN_TRADES_SQUEEZE or legacy SQUEEZE_CALIB_MIN_TRADES to override).",
        _squeeze_min,
    )

    # Run backfill immediately on startup to catch any rows written since last run.
    # Check heartbeat first so a missed run is flagged before fresh history is written.
    log.info("─── Startup run ──────────────────────────────────────────────")
    _check_backfill_heartbeat()
    run_backfill(date_from=date_from, date_to=date_to)
    run_pending_resolver(date_from=date_from, date_to=date_to)
    _check_all_uncalibrated_screeners()
    _recalibrate_morning_tcs_floor()
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
            run_pending_resolver(date_from=date_from, date_to=date_to)
            if _shutdown.is_set():
                break
            _check_all_uncalibrated_screeners()
            _recalibrate_morning_tcs_floor()
            if _shutdown.is_set():
                break
            # Send TP calibration preview on Friday night (Sat 00:05 ET).
            if _should_run_tp_preview():
                run_tp_calibration_preview()
                if _shutdown.is_set():
                    break
            # Run TP calibration once a week on Sunday night (Mon 00:05 ET).
            if _should_run_tp_calibration():
                run_tp_calibration()
                if _shutdown.is_set():
                    break

        refresh_summary_cache()

    log.info("Nightly Tiered P&L Refresh — stopped cleanly.")


if __name__ == "__main__":
    main()
