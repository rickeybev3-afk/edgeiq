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
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

SUMMARY_FILE = "filter_grid_summary.json"
CATCH_UP_THRESHOLD_DAYS = 7


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
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'='*60}", flush=True)
    print(f"[grid-search-scheduler] Run started at {now_str}", flush=True)
    print(f"{'='*60}", flush=True)

    cmd = [sys.executable, "filter_grid_search.py", "--phase", "3"]
    result = subprocess.run(cmd)

    finish_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if result.returncode == 0:
        print(f"\n[grid-search-scheduler] Run completed successfully at {finish_str}.", flush=True)
        print("[grid-search-scheduler] Output JSON files refreshed.", flush=True)
    else:
        print(
            f"\n[grid-search-scheduler] Run FAILED (exit code {result.returncode}) at {finish_str}.",
            flush=True,
        )
    print(f"{'='*60}\n", flush=True)


def main() -> None:
    print("[grid-search-scheduler] Phase 3 grid search weekly scheduler starting.", flush=True)
    print("[grid-search-scheduler] Cron schedule: Sunday 02:00 UTC (day_of_week=sun, hour=2, minute=0)", flush=True)

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


SCHEDULE_HOUR   = 2
SCHEDULE_MINUTE = 0


if __name__ == "__main__":
    main()
