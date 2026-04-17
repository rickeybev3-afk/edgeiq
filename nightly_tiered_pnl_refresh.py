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
  --no-ratelimit   Skip inter-request Alpaca sleep (useful on paid data plans)
"""

import sys, os, time, datetime, subprocess, logging, signal, threading, json, html

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


def _send_slack(message: str) -> None:
    """Send a plain-text message via a Slack incoming webhook.

    Reads SLACK_WEBHOOK_URL from the environment.  Silently skips (log-only)
    if the variable is absent or empty.
    """
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
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


# ── Cache-failure alert dispatcher ───────────────────────────────────────────


def _send_cache_failure_alert(error_msg: str) -> None:
    """Dispatch an operator alert when mv_tiered_pnl_summary refresh fails.

    Tries both Slack (SLACK_WEBHOOK_URL) and Telegram (TELEGRAM_BOT_TOKEN +
    TELEGRAM_CHAT_ID).  Each channel silently skips if its credentials are
    absent, so operators only need to configure whichever channel they use.
    """
    timestamp = _et_now().strftime("%Y-%m-%d %H:%M:%S ET")
    plain_msg = (
        f"[EdgeIQ] Ladder cache refresh FAILED\n"
        f"Time: {timestamp}\n"
        f"Error: {error_msg}"
    )
    _send_slack(plain_msg)

    html_msg = (
        f"⚠️ <b>EdgeIQ — Ladder cache refresh FAILED</b>\n"
        f"Time: {timestamp}\n"
        f"<b>Error:</b> <code>{html.escape(error_msg[:400])}</code>"
    )
    _send_telegram(html_msg)


# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("nightly_tiered_pnl")


# ── Ladder P&L summary-cache refresh ─────────────────────────────────────────

def refresh_summary_cache():
    """Call backend.refresh_mv_tiered_pnl_summary() and log the outcome.

    The function is imported lazily so this module can still start up even if
    backend.py has import-time side effects that haven't finished yet.
    """
    log.info("Refreshing mv_tiered_pnl_summary materialised view …")
    start = time.monotonic()
    try:
        import backend as _backend
        result = _backend.refresh_mv_tiered_pnl_summary()
        elapsed = time.monotonic() - start
        if result.get("success"):
            log.info(
                "mv_tiered_pnl_summary refresh complete (%.1fs): %s",
                elapsed,
                result.get("message", "ok"),
            )
        else:
            failure_msg = result.get("message", "unknown error")
            log.warning(
                "mv_tiered_pnl_summary refresh failed (%.1fs): %s",
                elapsed,
                failure_msg,
            )
            _send_cache_failure_alert(failure_msg)
    except Exception as exc:
        elapsed = time.monotonic() - start
        log.error(
            "mv_tiered_pnl_summary refresh raised an exception after %.1fs: %s",
            elapsed,
            exc,
        )
        _send_cache_failure_alert(f"Exception after {elapsed:.1f}s: {exc}")


# ── Backfill runner ───────────────────────────────────────────────────────────

def run_backfill():
    """Invoke run_tiered_pnl_backfill.py --backtest-only as a subprocess.

    Using subprocess (rather than importing the module) keeps each run in a
    fresh interpreter context, which avoids any state leakage between nightly
    runs and makes it trivial to read the output as a stream.

    After each run a Telegram summary is sent (if credentials are available)
    with rows fetched, updated, skipped, errors, and elapsed time.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "run_tiered_pnl_backfill.py")
    cmd = [sys.executable, script, "--backtest-only"]

    # Honour --no-ratelimit if this wrapper was started with it.
    if "--no-ratelimit" in sys.argv:
        cmd.append("--no-ratelimit")

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
        fetched  = bt.get("fetched", 0)
        updated  = bt.get("updated", 0)
        skipped  = (bt.get("skipped_no_bars", 0) + bt.get("skipped_no_tiered", 0))
        errors   = bt.get("errors", 0)
        elapsed_s = stats.get("elapsed_s", elapsed)
        mins, secs = divmod(int(elapsed_s), 60)
        elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"

        status_icon = "✅" if errors == 0 else "⚠️"
        msg = (
            f"{status_icon} <b>Nightly Tiered P&amp;L Refresh</b>\n"
            f"Date: {run_date}\n"
            f"Rows fetched : {fetched}\n"
            f"Rows updated : {updated}\n"
            f"Rows skipped : {skipped}\n"
            f"Errors       : {errors}\n"
            f"Elapsed      : {elapsed_str}"
        )

    _send_telegram(msg)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    log.info("=" * 60)
    log.info("Nightly Tiered P&L Refresh — started")
    log.info("Scheduled events (ET):  21:00 → cache refresh,  00:05 → backfill + cache refresh")
    log.info("=" * 60)

    # Report which failure-alert channels are active so misconfiguration is visible immediately.
    slack_active    = bool(os.getenv("SLACK_WEBHOOK_URL", "").strip())
    telegram_active = (
        bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
        and bool(os.getenv("TELEGRAM_CHAT_ID", "").strip())
    )
    if slack_active or telegram_active:
        channels = ", ".join(filter(None, [
            "Slack" if slack_active else "",
            "Telegram" if telegram_active else "",
        ]))
        log.info("Cache-failure alerts enabled via: %s", channels)
    else:
        log.warning(
            "No alert channels configured — cache-refresh failures will only appear in logs. "
            "Set SLACK_WEBHOOK_URL and/or TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID to enable alerts."
        )

    # Run backfill immediately on startup to catch any rows written since last run.
    log.info("─── Startup run ──────────────────────────────────────────────")
    run_backfill()
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
            run_backfill()
            if _shutdown.is_set():
                break

        refresh_summary_cache()

    log.info("Nightly Tiered P&L Refresh — stopped cleanly.")


if __name__ == "__main__":
    main()
