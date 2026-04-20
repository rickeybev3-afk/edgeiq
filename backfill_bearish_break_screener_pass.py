"""
backfill_bearish_break_screener_pass.py
───────────────────────────────────────
ONE-TIME HISTORICAL MIGRATION — no longer needed for new trades.

As of 2026-04-20, paper_trader_bot.py automatically stamps
screener_pass = 'gap_down' on every new Bearish Break order at placement
time, so future settled trades are immediately visible to
calibrate_sp_mult.py --pass gap_down without any backfill.

This script exists solely to retroactively tag pre-migration rows (those
placed before the auto-tagging was added).  Run it once to bring historical
data in line, then it can be archived.

Backfills screener_pass = 'gap_down' on paper_trades rows where:
  - predicted = 'Bearish Break'
  - screener_pass IS NULL  OR  screener_pass != 'gap_down'
    (i.e. any row not already correctly tagged, including 'other', 'trend',
    'gap', 'bearish_break_filtered', or NULL)

Why 'gap_down'?
  Bearish Break orders were historically logged before the gap_down screener
  pass was introduced, so those rows were stamped with whatever pass was active
  at the time ('other', 'trend', 'gap', etc.). Stamping them 'gap_down' is
  accurate because every Bearish Break candidate was a downward-gap stock by
  construction. This makes the rows visible to per-pass win-rate analytics and
  multiplier-calibration queries that filter on screener_pass='gap_down'.

The script only touches rows whose outcome is already settled (tiered_pnl_r IS
NOT NULL) so in-flight paper trades are never accidentally modified.

Usage:
  python backfill_bearish_break_screener_pass.py             # stamp all non-gap_down
  python backfill_bearish_break_screener_pass.py --dry-run   # print plan, no writes
  python backfill_bearish_break_screener_pass.py --include-unsettled
                                                             # also stamp open rows
"""

import sys
import os
import time
import argparse
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend
from backfill_utils import append_backfill_history

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger(__name__)

PAGE_SZ         = 1000
UPSERT_BATCH_SZ = 2000
TABLE           = "paper_trades"
DATE_FIELD      = "trade_date"
TARGET_PASS     = "gap_down"


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Stamp screener_pass='gap_down' on historical Bearish Break rows"
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be updated without writing to the DB")
    p.add_argument("--include-unsettled", action="store_true",
                   help="Also update rows where result IS NULL (open trades)")
    p.add_argument("--start", metavar="YYYY-MM-DD",
                   help="Only process rows with trade_date >= this date")
    p.add_argument("--end",   metavar="YYYY-MM-DD",
                   help="Only process rows with trade_date <= this date")
    return p.parse_args()


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_rows(include_unsettled: bool, start_date=None, end_date=None) -> list[dict]:
    if not backend.supabase:
        print("No Supabase connection — aborting.")
        sys.exit(1)

    rows   = []
    offset = 0
    print(f"  Fetching settled Bearish Break rows not yet tagged 'gap_down'…",
          end="", flush=True)

    while True:
        q = (
            backend.supabase.table(TABLE)
            .select(f"id,ticker,{DATE_FIELD},predicted,screener_pass,tiered_pnl_r")
            .eq("predicted", "Bearish Break")
            .or_("screener_pass.is.null,screener_pass.neq.gap_down")
        )
        if not include_unsettled:
            q = q.not_.is_("tiered_pnl_r", "null")
        if start_date:
            q = q.gte(DATE_FIELD, start_date)
        if end_date:
            q = q.lte(DATE_FIELD, end_date)

        resp  = q.range(offset, offset + PAGE_SZ - 1).execute()
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SZ:
            break
        offset += PAGE_SZ
        print(".", end="", flush=True)

    print(f" {len(rows)} rows found.")
    return rows


# ── Upsert ────────────────────────────────────────────────────────────────────

def upsert_rows(updates: list[dict], dry_run: bool) -> int:
    """Write screener_pass on existing rows via individual updates.

    We deliberately avoid upsert here: all rows were fetched from the DB so
    they are guaranteed to exist. Using update (not upsert) prevents any risk
    of accidentally inserting a sparse row if an ID were somehow stale.
    Rows are processed in chunks to limit per-request payload size.
    """
    if dry_run or not updates:
        return len(updates)

    success = 0
    for i in range(0, len(updates), UPSERT_BATCH_SZ):
        chunk = updates[i : i + UPSERT_BATCH_SZ]
        ids   = [row["id"] for row in chunk]
        try:
            backend.supabase.table(TABLE).update(
                {"screener_pass": TARGET_PASS}
            ).in_("id", ids).execute()
            success += len(chunk)
        except Exception as e:
            _log.warning(f"  Batch update failed ({len(chunk)} rows): {e}")
            for row in chunk:
                try:
                    backend.supabase.table(TABLE).update(
                        {"screener_pass": row["screener_pass"]}
                    ).eq("id", row["id"]).execute()
                    success += 1
                except Exception as e2:
                    _log.warning(f"  Row update failed id={row['id']}: {e2}")
    return success


# ── Summary helpers ───────────────────────────────────────────────────────────

def _fmt_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _sample_rows(rows: list[dict], n: int = 5) -> None:
    """Print a short sample of rows that will be updated."""
    sample = rows[:n]
    for r in sample:
        print(f"    id={r['id']}  ticker={r['ticker']}  "
              f"date={str(r.get(DATE_FIELD, ''))[:10]}  "
              f"screener_pass={r.get('screener_pass')!r}  "
              f"tiered_pnl_r={r.get('tiered_pnl_r')!r}")
    if len(rows) > n:
        print(f"    … and {len(rows) - n} more")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    print("=" * 64)
    print("  EdgeIQ — Bearish Break screener_pass Backfill")
    print(f"  Target value : screener_pass = '{TARGET_PASS}'")
    print(f"  Filter       : predicted='Bearish Break'")
    print(f"                 AND (screener_pass IS NULL")
    print(f"                      OR screener_pass != 'gap_down')")
    if not args.include_unsettled:
        print(f"                 AND tiered_pnl_r IS NOT NULL  (settled trades only)")
    if args.dry_run:
        print("  *** DRY RUN — no writes ***")
    print("=" * 64)

    rows = fetch_rows(
        include_unsettled=args.include_unsettled,
        start_date=args.start,
        end_date=args.end,
    )

    if not rows:
        print("\n  Nothing to update — no matching rows found.")
        print("=" * 64)
        return

    from collections import Counter
    pass_counts = Counter(r.get("screener_pass") for r in rows)
    tickers     = sorted({r["ticker"] for r in rows})
    dates       = sorted({str(r.get(DATE_FIELD, ""))[:10] for r in rows if r.get(DATE_FIELD)})

    print(f"\n  Rows to update : {len(rows):,}")
    print(f"  Current screener_pass breakdown:")
    for pass_val, count in sorted(pass_counts.items(), key=lambda x: -(x[1])):
        print(f"    {pass_val!r:<30}: {count:,}")
    print(f"  Unique tickers : {len(tickers)}")
    print(f"  Date range     : {dates[0] if dates else 'N/A'} → {dates[-1] if dates else 'N/A'}")
    print(f"\n  Sample rows:")
    _sample_rows(rows)
    print()

    updates = [{"id": r["id"], "screener_pass": TARGET_PASS} for r in rows]

    t0      = time.monotonic()
    written = upsert_rows(updates, dry_run=args.dry_run)
    elapsed = time.monotonic() - t0

    print("=" * 64)
    if args.dry_run:
        print(f"  DRY RUN — would have written : {written:,} rows")
    else:
        print(f"  screener_pass written : {written:,} rows  ({_fmt_eta(elapsed)})")
    print("=" * 64)

    if not args.dry_run:
        append_backfill_history(
            script="backfill_bearish_break_screener_pass",
            health={
                "rows_written":            written,
                "pass_counts_before":      dict(pass_counts),
                "target_screener_pass":    TARGET_PASS,
                "unique_tickers":          len(tickers),
                "date_range_start":        dates[0]  if dates else None,
                "date_range_end":          dates[-1] if dates else None,
                "elapsed_s":               round(elapsed, 2),
                "include_unsettled":       args.include_unsettled,
            },
            logger=_log,
        )


if __name__ == "__main__":
    main()
