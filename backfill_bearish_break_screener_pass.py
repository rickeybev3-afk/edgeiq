"""
backfill_bearish_break_screener_pass.py
───────────────────────────────────────
Backfills screener_pass = 'gap_down' on paper_trades rows where:
  - predicted = 'Bearish Break'
  - screener_pass IS NULL  OR  screener_pass = 'bearish_break_filtered'

Why 'gap_down'?
  Bearish Break orders were disabled (never placed) before the screener was
  introduced, so these rows never received a live screener_pass tag. Stamping
  them 'gap_down' is accurate for the pre-screener era because every Bearish
  Break candidate was a downward-gap stock by construction. This makes the rows
  visible to per-pass win-rate analytics and multiplier-calibration queries that
  filter out NULL / 'bearish_break_filtered' rows.

The script only touches rows whose outcome is already settled (result IS NOT
NULL) so in-flight paper trades are never accidentally modified.

Usage:
  python backfill_bearish_break_screener_pass.py             # stamp NULLs + filtered
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
    print(f"  Fetching Bearish Break rows with NULL/'bearish_break_filtered' screener_pass…",
          end="", flush=True)

    while True:
        q = (
            backend.supabase.table(TABLE)
            .select(f"id,ticker,{DATE_FIELD},predicted,screener_pass,result")
            .eq("predicted", "Bearish Break")
            .or_("screener_pass.is.null,screener_pass.eq.bearish_break_filtered")
        )
        if not include_unsettled:
            q = q.not_.is_("result", "null")
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
              f"result={r.get('result')!r}")
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
    print(f"                      OR screener_pass='bearish_break_filtered')")
    if not args.include_unsettled:
        print(f"                 AND result IS NOT NULL  (settled trades only)")
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

    n_null     = sum(1 for r in rows if r.get("screener_pass") is None)
    n_filtered = sum(1 for r in rows if r.get("screener_pass") == "bearish_break_filtered")
    tickers    = sorted({r["ticker"] for r in rows})
    dates      = sorted({str(r.get(DATE_FIELD, ""))[:10] for r in rows if r.get(DATE_FIELD)})

    print(f"\n  Rows to update : {len(rows):,}")
    print(f"    screener_pass IS NULL          : {n_null:,}")
    print(f"    screener_pass='bearish_break_filtered' : {n_filtered:,}")
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
                "rows_null_before":        n_null,
                "rows_filtered_before":    n_filtered,
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
