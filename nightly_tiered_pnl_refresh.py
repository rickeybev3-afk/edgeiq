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

_CACHE_ALERT_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".edgeiq_cache_alert_state.json",
)

_DEFAULT_COOLDOWN_HOURS = 23


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
    """
    state = _read_alert_state()
    if alert_key in state:
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
    """
    cooldown_hours = _get_cooldown_hours()
    now_utc = datetime.datetime.utcnow()

    if cooldown_hours > 0:
        state = _read_alert_state()
        key_state = state.get(alert_key, {})
        last_sent_iso = key_state.get("last_sent_utc") if isinstance(key_state, dict) else None
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
                    return
            except Exception as _parse_err:
                log.warning(
                    "Could not parse last_sent_utc for key '%s' from state file: %s",
                    alert_key,
                    _parse_err,
                )

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

    email_subject = "[EdgeIQ] Ladder cache refresh FAILED"
    email_html = (
        f"<p>⚠️ <strong>EdgeIQ — Ladder cache refresh FAILED</strong></p>"
        f"<p><strong>Time:</strong> {timestamp}</p>"
        f"<p><strong>Error:</strong> <code>{html.escape(error_msg[:400])}</code></p>"
    )
    _send_email_alert(email_subject, plain_msg, email_html)

    state = _read_alert_state()
    state[alert_key] = {"last_sent_utc": now_utc.isoformat()}
    _write_alert_state(state)


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

        msg = (
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

    # Run backfill immediately on startup to catch any rows written since last run.
    log.info("─── Startup run ──────────────────────────────────────────────")
    run_backfill(date_from=date_from, date_to=date_to)
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
            run_backfill(date_from=date_from, date_to=date_to)
            if _shutdown.is_set():
                break

        refresh_summary_cache()

    log.info("Nightly Tiered P&L Refresh — stopped cleanly.")


if __name__ == "__main__":
    main()
