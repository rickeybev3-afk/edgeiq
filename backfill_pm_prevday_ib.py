"""
backfill_pm_prevday_ib.py — Backfill pre-market & prior-day IB data for all-time scope.

Adds 5 columns to backtest_sim_runs for every (ticker, sim_date) pair:
  pm_ib_high      — pre-market session high (4:00–9:30 AM ET)
  pm_ib_low       — pre-market session low
  pm_range_pct    — (pm_high − pm_low) / open × 100
  prev_day_ib_high — prior RTH IB high (from prior row in backtest_sim_runs)
  prev_day_ib_low  — prior RTH IB low

Migration SQL (run once in Supabase → SQL Editor before first use):
  ALTER TABLE backtest_sim_runs
    ADD COLUMN IF NOT EXISTS pm_ib_high       REAL,
    ADD COLUMN IF NOT EXISTS pm_ib_low        REAL,
    ADD COLUMN IF NOT EXISTS pm_range_pct     REAL,
    ADD COLUMN IF NOT EXISTS prev_day_ib_high REAL,
    ADD COLUMN IF NOT EXISTS prev_day_ib_low  REAL;

Usage:
    python backfill_pm_prevday_ib.py               # all rows with nulls
    python backfill_pm_prevday_ib.py --start 2025-01-01 --end 2025-12-31
    python backfill_pm_prevday_ib.py --dry-run      # print plan only
    python backfill_pm_prevday_ib.py --force        # re-fill all rows even if populated
    python backfill_pm_prevday_ib.py --max-dates 50 # limit dates per run (for testing)

Checkpointing:
  Progress is saved to backfill_pm_ib_checkpoint.json every 50 dates.
  Re-running the script automatically resumes from the last checkpoint.
  Use --reset-checkpoint to start fresh.
"""

import os
import re
import sys
import json
import time
import logging
import argparse
import statistics
from datetime import datetime, timedelta
from collections import defaultdict

import requests

from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Supabase connection ────────────────────────────────────────────────────
raw_url = os.environ.get("SUPABASE_URL", "")
_m = re.search(r"supabase\.com/dashboard/project/([a-z0-9]+)", raw_url)
if _m:
    SUPABASE_URL = f"https://{_m.group(1)}.supabase.co"
elif ".supabase.co" in raw_url:
    _pid = raw_url.split(".supabase.co")[0].split("https://")[-1]
    SUPABASE_URL = f"https://{_pid}.supabase.co"
else:
    SUPABASE_URL = raw_url

SUPABASE_KEY = (
    os.environ.get("SUPABASE_KEY") or
    os.environ.get("SUPABASE_ANON_KEY", "")
)
USER_ID = "a5e1fcab-8369-42c4-8550-a8a19734510c"

ALPACA_API_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")

CHECKPOINT_FILE   = "backfill_pm_ib_checkpoint.json"
PAGE_SIZE         = 1000
ALPACA_SLEEP      = 0.35   # seconds between Alpaca calls (avoid rate-limit)
SAVE_EVERY        = 50     # save checkpoint every N dates

MIGRATION_SQL = """
ALTER TABLE backtest_sim_runs
  ADD COLUMN IF NOT EXISTS pm_ib_high       REAL,
  ADD COLUMN IF NOT EXISTS pm_ib_low        REAL,
  ADD COLUMN IF NOT EXISTS pm_range_pct     REAL,
  ADD COLUMN IF NOT EXISTS prev_day_ib_high REAL,
  ADD COLUMN IF NOT EXISTS prev_day_ib_low  REAL;
""".strip()

NEW_COLUMNS = ["pm_ib_high", "pm_ib_low", "pm_range_pct", "prev_day_ib_high", "prev_day_ib_low"]


# ── Alpaca helpers ─────────────────────────────────────────────────────────

def _alpaca_headers():
    return {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }


def fetch_pm_data(ticker: str, date_str: str, open_px: float) -> dict:
    """Return dict with pm_ib_high, pm_ib_low, pm_range_pct for 4:00–9:30 ET."""
    empty = {"pm_ib_high": None, "pm_ib_low": None, "pm_range_pct": None}
    if open_px <= 0:
        return empty
    try:
        import pytz
        ET  = pytz.timezone("America/New_York")
        dt  = datetime.strptime(date_str, "%Y-%m-%d")
        start = ET.localize(dt.replace(hour=4,  minute=0)).isoformat()
        end   = ET.localize(dt.replace(hour=9, minute=30)).isoformat()
        r = requests.get(
            f"https://data.alpaca.markets/v2/stocks/{ticker}/bars",
            headers=_alpaca_headers(),
            params={
                "start": start, "end": end,
                "timeframe": "1Min", "feed": "sip", "limit": 400,
            },
            timeout=15,
        )
        if r.status_code == 200:
            bars = r.json().get("bars") or []
            if bars:
                hi = max(b["h"] for b in bars)
                lo = min(b["l"] for b in bars)
                return {
                    "pm_ib_high":   round(hi, 4),
                    "pm_ib_low":    round(lo, 4),
                    "pm_range_pct": round((hi - lo) / open_px * 100, 4),
                }
    except Exception as e:
        log.debug(f"PM fetch {ticker} {date_str}: {e}")
    return empty


def fetch_prev_day_ib(ticker: str, date_str: str) -> dict:
    """Return dict with prev_day_ib_high/low from Alpaca daily bars."""
    empty = {"prev_day_ib_high": None, "prev_day_ib_low": None}
    try:
        _end   = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        _start = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
        r = requests.get(
            f"https://data.alpaca.markets/v2/stocks/{ticker}/bars",
            headers=_alpaca_headers(),
            params={
                "start": _start, "end": _end,
                "timeframe": "1Day", "feed": "iex", "limit": 5,
            },
            timeout=15,
        )
        if r.status_code == 200:
            bars = r.json().get("bars") or []
            if bars:
                return {
                    "prev_day_ib_high": round(float(bars[-1]["h"]), 4),
                    "prev_day_ib_low":  round(float(bars[-1]["l"]), 4),
                }
    except Exception as e:
        log.debug(f"Prev-IB fetch {ticker} {date_str}: {e}")
    return empty


# ── Supabase helpers ───────────────────────────────────────────────────────

def check_columns_exist(sb) -> bool:
    """Return True if PM columns already exist in backtest_sim_runs."""
    try:
        sb.table("backtest_sim_runs").select(
            "pm_ib_high, pm_ib_low, pm_range_pct, prev_day_ib_high, prev_day_ib_low"
        ).limit(1).execute()
        return True
    except Exception as e:
        err = str(e).lower()
        if "column" in err or "does not exist" in err or "42703" in err:
            return False
        raise


def fetch_null_rows(sb, start_date, end_date, force: bool) -> list[dict]:
    """Fetch (id, ticker, sim_date, open_price) rows needing PM backfill."""
    rows = []
    offset = 0
    log.info("Fetching rows needing PM/IB backfill…")
    while True:
        q = (
            sb.table("backtest_sim_runs")
            .select("id, ticker, sim_date, open_price")
            .eq("user_id", USER_ID)
            .order("sim_date")
            .range(offset, offset + PAGE_SIZE - 1)
        )
        if not force:
            q = q.is_("pm_ib_high", "null")
        if start_date:
            q = q.gte("sim_date", start_date)
        if end_date:
            q = q.lte("sim_date", end_date)
        batch = q.execute().data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        if offset % 10000 == 0:
            log.info(f"  …{offset:,} rows scanned")
    log.info(f"Rows to backfill: {len(rows):,}")
    return rows


def group_by_date(rows) -> dict:
    """Group rows by sim_date → list of {id, ticker, open_price}."""
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["sim_date"]].append({
            "id":         r["id"],
            "ticker":     r["ticker"],
            "open_price": float(r.get("open_price") or 0),
        })
    return dict(sorted(by_date.items()))


def upsert_row_pm(sb, row_id: str, pm_data: dict, prev_data: dict) -> bool:
    """Patch the backtest_sim_runs row with PM + prev-day IB data."""
    patch = {**pm_data, **prev_data}
    patch = {k: v for k, v in patch.items() if v is not None}
    if not patch:
        return False
    try:
        sb.table("backtest_sim_runs").update(patch).eq("id", row_id).execute()
        return True
    except Exception as e:
        log.warning(f"Upsert failed for id={row_id}: {e}")
        return False


# ── Checkpointing ──────────────────────────────────────────────────────────

def load_checkpoint() -> set:
    if not os.path.exists(CHECKPOINT_FILE):
        return set()
    try:
        with open(CHECKPOINT_FILE) as f:
            data = json.load(f)
        return set(data.get("completed_dates", []))
    except Exception:
        return set()


def save_checkpoint(completed_dates: set) -> None:
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({
            "completed_dates": sorted(completed_dates),
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }, f, indent=2)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Backfill pre-market + prior-day IB data in backtest_sim_runs"
    )
    p.add_argument("--start",            metavar="YYYY-MM-DD", default=None)
    p.add_argument("--end",              metavar="YYYY-MM-DD", default=None)
    p.add_argument("--dry-run",          action="store_true")
    p.add_argument("--force",            action="store_true",
                   help="Re-fill already populated rows")
    p.add_argument("--max-dates",        type=int, default=None,
                   help="Process at most this many dates (useful for incremental runs)")
    p.add_argument("--reset-checkpoint", action="store_true")
    args = p.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL / SUPABASE_KEY not set.")
        sys.exit(1)
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        log.error("ALPACA_API_KEY / ALPACA_SECRET_KEY not set.")
        sys.exit(1)

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # ── Migration guard ───────────────────────────────────────────────────
    if not check_columns_exist(sb):
        log.warning(
            "\n"
            "═══════════════════════════════════════════════════════════════\n"
            "  PM/IB columns are MISSING in backtest_sim_runs.\n"
            "  Run this SQL in Supabase → SQL Editor, then re-run:\n\n"
            f"  {MIGRATION_SQL}\n\n"
            "═══════════════════════════════════════════════════════════════"
        )
        sys.exit(1)
    log.info("PM/IB columns present ✅")

    # ── Checkpoint ────────────────────────────────────────────────────────
    if args.reset_checkpoint and os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        log.info("Checkpoint reset.")
    completed_dates = load_checkpoint()
    if completed_dates:
        log.info(f"Resuming: {len(completed_dates)} dates already completed.")

    # ── Fetch rows ────────────────────────────────────────────────────────
    rows = fetch_null_rows(sb, args.start, args.end, args.force)
    by_date = group_by_date(rows)

    dates = [d for d in sorted(by_date.keys()) if d not in completed_dates]
    if args.max_dates:
        dates = dates[:args.max_dates]

    log.info(f"Dates to process: {len(dates):,} of {len(by_date):,} total")
    if args.dry_run:
        log.info("DRY RUN — no writes will be made.")
        for d in dates[:10]:
            log.info(f"  {d}: {len(by_date[d])} rows — {[r['ticker'] for r in by_date[d][:5]]}")
        return

    rows_updated = 0
    rows_skipped  = 0

    for i, date_str in enumerate(dates, 1):
        date_rows = by_date[date_str]
        log.info(f"[{i}/{len(dates)}] {date_str}: {len(date_rows)} tickers")

        for row in date_rows:
            ticker   = row["ticker"]
            open_px  = row["open_price"]
            row_id   = row["id"]

            pm_data   = fetch_pm_data(ticker, date_str, open_px)
            time.sleep(ALPACA_SLEEP)
            prev_data = fetch_prev_day_ib(ticker, date_str)
            time.sleep(ALPACA_SLEEP)

            ok = upsert_row_pm(sb, row_id, pm_data, prev_data)
            if ok:
                rows_updated += 1
            else:
                rows_skipped += 1

        completed_dates.add(date_str)

        if i % SAVE_EVERY == 0:
            save_checkpoint(completed_dates)
            log.info(f"  Checkpoint saved ({len(completed_dates)} dates done, {rows_updated} rows updated)")

    save_checkpoint(completed_dates)
    log.info(
        f"\nDone. Dates processed: {len(dates)}, "
        f"rows updated: {rows_updated}, skipped: {rows_skipped}"
    )


if __name__ == "__main__":
    main()
