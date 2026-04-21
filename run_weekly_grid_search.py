"""
run_weekly_grid_search.py — Weekly cron scheduler for Phase 3 filter grid search.

Uses APScheduler's BlockingScheduler with a CronTrigger to run
`python3 filter_grid_search.py --phase 3` every Sunday at 02:00 UTC.

Designed to run as a persistent Replit workflow ("Weekly Phase 3 Grid Search").
Each triggered execution runs the grid search in an isolated subprocess with its
own log boundary so success/failure status is clearly visible in the workflow pane.

Startup behaviour
─────────────────
• If the most recent run recorded in filter_grid_summary.json is more than 7 days
  old (or no prior run exists), the grid search fires immediately on startup to
  catch up for any missed weeks, then resumes the Sunday 02:00 UTC schedule.
• Otherwise it waits for the next scheduled Sunday 02:00 UTC trigger.

Notifications
─────────────
After every run (success or failure) a short alert is sent to one or both of:
  • Slack / generic webhook — set ALERT_WEBHOOK_URL to a Slack incoming-webhook or
    any endpoint that accepts {"text": "..."} JSON POST requests.
  • Telegram — set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.

If neither destination is configured the notification step is silently skipped.

To control which events send a notification set GRID_SEARCH_NOTIFY_ON to a
comma-separated list of event types:
  • "success"          — notify only on successful runs
  • "failure"          — notify only on failed runs
  • "success,failure"  — notify on both (default when the variable is absent or empty)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

SUMMARY_FILE = "filter_grid_summary.json"
CATCH_UP_THRESHOLD_DAYS = 7
_ALERT_CONFIG_FILE = ".local/grid_search_alert_config.json"


def _load_ui_alert_config() -> dict:
    """Load notification settings saved via the dashboard UI.

    Returns a dict with keys: webhook_url, telegram_token, telegram_chat_id.
    Falls back to empty strings when the file does not exist or cannot be parsed.
    """
    try:
        if os.path.exists(_ALERT_CONFIG_FILE):
            with open(_ALERT_CONFIG_FILE) as _f:
                data = json.load(_f)
                return {
                    "webhook_url": data.get("webhook_url", "").strip(),
                    "telegram_token": data.get("telegram_token", "").strip(),
                    "telegram_chat_id": data.get("telegram_chat_id", "").strip(),
                }
    except Exception:
        pass
    return {"webhook_url": "", "telegram_token": "", "telegram_chat_id": ""}


SCHEDULE_HOUR   = 2
SCHEDULE_MINUTE = 0


# ── Notification helpers ───────────────────────────────────────────────────────

def _notify_webhook(message: str) -> None:
    """POST *message* to the configured webhook URL as {"text": message}.

    Checks the dashboard UI config first (.local/grid_search_alert_config.json),
    then falls back to the ALERT_WEBHOOK_URL environment variable.
    Silently skipped when neither is set.
    """
    webhook_url = _load_ui_alert_config()["webhook_url"] or os.environ.get("ALERT_WEBHOOK_URL", "").strip()
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
        urllib.request.urlopen(req, timeout=10)
        print("[grid-search-scheduler] Webhook notification sent.", flush=True)
    except Exception as exc:
        print(f"[grid-search-scheduler] WARNING: webhook notification failed — {exc}", flush=True)


def _notify_telegram(message: str) -> None:
    """Send *message* via Telegram bot.

    Checks the dashboard UI config first (.local/grid_search_alert_config.json),
    then falls back to the TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID environment
    variables.  Silently skipped when either value is missing.
    """
    ui_cfg = _load_ui_alert_config()
    token = ui_cfg["telegram_token"] or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = ui_cfg["telegram_chat_id"] or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        payload = json.dumps({"chat_id": chat_id, "text": message}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        print("[grid-search-scheduler] Telegram notification sent.", flush=True)
    except Exception as exc:
        print(f"[grid-search-scheduler] WARNING: Telegram notification failed — {exc}", flush=True)


def _send_notification(message: str) -> None:
    """Dispatch *message* to every configured notification destination."""
    _notify_webhook(message)
    _notify_telegram(message)


def _notify_on_events() -> frozenset[str]:
    """Return the set of event types that should trigger a notification.

    Reads GRID_SEARCH_NOTIFY_ON (comma-separated, case-insensitive).
    Recognised tokens are ``success`` and ``failure``.  Unknown tokens are
    ignored.  When the variable is absent or empty, defaults to both events
    (preserving existing behaviour).
    """
    raw = os.environ.get("GRID_SEARCH_NOTIFY_ON", "").strip()
    if not raw:
        return frozenset({"success", "failure"})
    tokens = {t.strip().lower() for t in raw.split(",")}
    recognised = tokens & {"success", "failure"}
    if not recognised:
        return frozenset({"success", "failure"})
    return frozenset(recognised)


def _build_success_message(start_time: datetime, finish_time: datetime) -> str:
    """Build a human-readable success summary from the run metadata."""
    duration_secs = int((finish_time - start_time).total_seconds())
    duration_str = f"{duration_secs // 60}m {duration_secs % 60}s"

    lines = [
        ":white_check_mark: EdgeIQ grid search completed successfully",
        f"Duration: {duration_str}",
    ]

    if os.path.exists(SUMMARY_FILE):
        try:
            with open(SUMMARY_FILE) as f:
                data = json.load(f)
            best = data.get("best_combo") or data.get("best") or {}
            combos = data.get("combos_tested")
            total_rows = data.get("total_rows")

            if combos is not None:
                lines.append(f"Combos tested: {combos:,}")
            if total_rows is not None:
                lines.append(f"Rows evaluated: {total_rows:,}")
            if best:
                sharpe = best.get("sharpe")
                n = (
                    best.get("n_trades")
                    or best.get("n")
                    or best.get("trades")
                    or best.get("total_trades")
                )
                if sharpe is not None:
                    lines.append(f"Top Sharpe: {sharpe:.3f}")
                if n is not None:
                    lines.append(f"N (trades): {n}")
        except Exception as exc:
            lines.append(f"(could not read summary: {exc})")

    return "\n".join(lines)


def _build_failure_message(exit_code: int, start_time: datetime, finish_time: datetime) -> str:
    """Build a human-readable failure alert."""
    duration_secs = int((finish_time - start_time).total_seconds())
    duration_str = f"{duration_secs // 60}m {duration_secs % 60}s"
    finish_str = finish_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        f":red_circle: EdgeIQ grid search FAILED (exit code {exit_code})\n"
        f"Failed at: {finish_str}\n"
        f"Duration before failure: {duration_str}"
    )


# ── Core run logic ─────────────────────────────────────────────────────────────

def _last_run_utc() -> datetime | None:
    """Return the timestamp of the last completed run from the summary file, or None."""
    if not os.path.exists(SUMMARY_FILE):
        return None
    try:
        with open(SUMMARY_FILE) as f:
            data = json.load(f)
        ts = data.get("run_started") or data.get("timestamp")
        if ts:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception:
        pass
    return None


def run_grid_search() -> None:
    """Execute Phase 3 grid search as a subprocess. Streams output to stdout."""
    start_time = datetime.now(timezone.utc)
    now_str = start_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'='*60}", flush=True)
    print(f"[grid-search-scheduler] Run started at {now_str}", flush=True)
    print(f"{'='*60}", flush=True)

    cmd = [sys.executable, "filter_grid_search.py", "--phase", "3"]
    result = subprocess.run(cmd)

    finish_time = datetime.now(timezone.utc)
    finish_str = finish_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    notify_on = _notify_on_events()
    if result.returncode == 0:
        print(f"\n[grid-search-scheduler] Run completed successfully at {finish_str}.", flush=True)
        print("[grid-search-scheduler] Output JSON files refreshed.", flush=True)
        if "success" in notify_on:
            _send_notification(_build_success_message(start_time, finish_time))
        else:
            print("[grid-search-scheduler] Success notification suppressed (GRID_SEARCH_NOTIFY_ON).", flush=True)
    else:
        print(
            f"\n[grid-search-scheduler] Run FAILED (exit code {result.returncode}) at {finish_str}.",
            flush=True,
        )
        if "failure" in notify_on:
            _send_notification(_build_failure_message(result.returncode, start_time, finish_time))
        else:
            print("[grid-search-scheduler] Failure notification suppressed (GRID_SEARCH_NOTIFY_ON).", flush=True)
    print(f"{'='*60}\n", flush=True)


def main() -> None:
    print("[grid-search-scheduler] Phase 3 grid search weekly scheduler starting.", flush=True)
    print("[grid-search-scheduler] Cron schedule: Sunday 02:00 UTC (day_of_week=sun, hour=2, minute=0)", flush=True)

    ui_cfg = _load_ui_alert_config()
    webhook_configured = bool(ui_cfg["webhook_url"] or os.environ.get("ALERT_WEBHOOK_URL", "").strip())
    telegram_configured = bool(
        (ui_cfg["telegram_token"] or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())
        and (ui_cfg["telegram_chat_id"] or os.environ.get("TELEGRAM_CHAT_ID", "").strip())
    )
    if webhook_configured or telegram_configured:
        destinations = []
        if webhook_configured:
            src = "dashboard UI" if ui_cfg["webhook_url"] else "ALERT_WEBHOOK_URL env var"
            destinations.append(f"webhook ({src})")
        if telegram_configured:
            src = "dashboard UI" if ui_cfg["telegram_token"] else "env vars"
            destinations.append(f"Telegram ({src})")
        print(
            f"[grid-search-scheduler] Notifications enabled via: {', '.join(destinations)}",
            flush=True,
        )
        notify_on = _notify_on_events()
        print(
            f"[grid-search-scheduler] Notifying on events: {', '.join(sorted(notify_on))} "
            f"(GRID_SEARCH_NOTIFY_ON={os.environ.get('GRID_SEARCH_NOTIFY_ON', '<not set — default: both>').strip() or '<empty — default: both>'})",
            flush=True,
        )
    else:
        print(
            "[grid-search-scheduler] No notification destination configured "
            "(set ALERT_WEBHOOK_URL and/or TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID via the dashboard or as env vars).",
            flush=True,
        )

    scheduler = BlockingScheduler(timezone="UTC")
    trigger = CronTrigger(day_of_week="sun", hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, timezone="UTC")

    scheduler.add_job(run_grid_search, trigger, misfire_grace_time=3600, id="phase3_grid_search")

    last_run = _last_run_utc()
    if last_run is None:
        print("[grid-search-scheduler] No prior run found — running grid search immediately for initial results.", flush=True)
        run_grid_search()
    else:
        age_days = (datetime.now(timezone.utc) - last_run).total_seconds() / 86400
        if age_days > CATCH_UP_THRESHOLD_DAYS:
            print(
                f"[grid-search-scheduler] Last run was {age_days:.1f} days ago — "
                "running catch-up grid search immediately.",
                flush=True,
            )
            run_grid_search()
        else:
            print(
                f"[grid-search-scheduler] Last run was {age_days:.1f} days ago. "
                "Waiting for next Sunday 02:00 UTC trigger.",
                flush=True,
            )

    print("[grid-search-scheduler] Scheduler armed. Waiting for next Sunday 02:00 UTC trigger...", flush=True)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("[grid-search-scheduler] Scheduler stopped.", flush=True)


if __name__ == "__main__":
    main()
