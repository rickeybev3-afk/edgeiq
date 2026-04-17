"""
nightly_tiered_pnl_refresh.py
──────────────────────────────
Long-running scheduler that keeps backtest_sim_runs.tiered_pnl_r up-to-date
by calling the backtest-only backfill once every night.

Design
──────
• On startup it runs the backfill immediately so any rows that accumulated
  since the last run are caught straight away.
• After each run it sleeps until midnight US/Eastern (≈ 00:05 ET to give
  any late-night batch jobs a few minutes to finish writing).
• If the backfill itself raises an exception the scheduler logs the error
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

import sys, os, time, datetime, subprocess, logging, signal, threading

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


# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("nightly_tiered_pnl")


# ── Backfill runner ───────────────────────────────────────────────────────────

def run_backfill():
    """Invoke run_tiered_pnl_backfill.py --backtest-only as a subprocess.

    Using subprocess (rather than importing the module) keeps each run in a
    fresh interpreter context, which avoids any state leakage between nightly
    runs and makes it trivial to read the output as a stream.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "run_tiered_pnl_backfill.py")
    cmd = [sys.executable, script, "--backtest-only"]

    # Honour --no-ratelimit if this wrapper was started with it.
    if "--no-ratelimit" in sys.argv:
        cmd.append("--no-ratelimit")

    log.info("Starting backfill:  %s", " ".join(cmd))
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            stdout=sys.stdout,
            stderr=sys.stderr,
            text=True,
        )
        elapsed = time.monotonic() - start
        if result.returncode == 0:
            log.info("Backfill complete (%.0fs, exit 0).", elapsed)
        else:
            log.warning("Backfill exited with code %d (%.0fs).",
                        result.returncode, elapsed)
    except Exception as exc:
        elapsed = time.monotonic() - start
        log.error("Backfill raised an exception after %.0fs: %s", elapsed, exc)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    log.info("=" * 60)
    log.info("Nightly Tiered P&L Refresh — started")
    log.info("=" * 60)

    run_number = 0
    while not _shutdown.is_set():
        run_number += 1
        log.info("─── Run #%d ───────────────────────────────────────────────",
                 run_number)
        run_backfill()

        if _shutdown.is_set():
            break

        sleep_secs = _seconds_until_midnight_et(target_hour=0, target_minute=5)
        wake_et = _et_now() + datetime.timedelta(seconds=sleep_secs)
        log.info(
            "Next run at %s ET (sleeping %.0f s / %.1f h).",
            wake_et.strftime("%Y-%m-%d %H:%M"),
            sleep_secs,
            sleep_secs / 3600,
        )
        completed = _interruptible_sleep(sleep_secs)
        if not completed:
            break

    log.info("Nightly Tiered P&L Refresh — stopped cleanly.")


if __name__ == "__main__":
    main()
