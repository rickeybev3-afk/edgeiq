"""
backfill_screener_pass.py
─────────────────────────
Backfills the `screener_pass` column on backtest_sim_runs and paper_trades.

Classification rules (applied at row level using Alpaca daily bars):
  - 'gap'     : daily change ≥ 3%  (gap-of-day)
  - 'trend'   : daily change ≥ 1% AND close > SMA20 AND close > SMA50
  - 'squeeze' : falls back to 'other' (no short-interest data available historically)
  - 'other'   : everything else

For each row the script:
  1. Fetches 70-day daily bars from Alpaca (one API call per date × batch of tickers)
  2. Computes SMA20 / SMA50 on the day before the trade date (so we use prior close)
  3. Reads the daily change pct (gap_pct column if present, else computes from bars)
  4. Classifies and writes screener_pass

The script is idempotent — pass --skip-existing to only fill NULL rows (default).
It is safe to re-run; rows already tagged are skipped unless --force is given.

Usage:
  python backfill_screener_pass.py                   # fill NULL rows only
  python backfill_screener_pass.py --force           # re-classify all rows
  python backfill_screener_pass.py --dry-run         # print plan, no writes
  python backfill_screener_pass.py --table paper_trades
  python backfill_screener_pass.py --start 2025-01-01 --end 2025-12-31
"""

import sys, os, time, argparse, logging
from datetime import datetime, timedelta, date as date_type
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend
from backfill_utils import append_backfill_history

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger(__name__)

PAGE_SZ       = 1000
BATCH_TICKERS = 50
MAX_WORKERS   = 20
BAR_LOOKBACK  = 70   # days of history needed to compute SMA50


TABLES = [
    ("backtest_sim_runs", "sim_date"),
    ("paper_trades",      "trade_date"),
]


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Backfill screener_pass on backtest_sim_runs and paper_trades")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force",   action="store_true",
                   help="Re-classify rows that already have a screener_pass value")
    p.add_argument("--table", choices=["backtest_sim_runs", "paper_trades"])
    p.add_argument("--start", metavar="YYYY-MM-DD")
    p.add_argument("--end",   metavar="YYYY-MM-DD")
    return p.parse_args()


# ── Step 1: fetch rows ────────────────────────────────────────────────────────

def fetch_rows(table: str, date_field: str,
               start_date=None, end_date=None,
               force=False) -> list[dict]:
    if not backend.supabase:
        print("No Supabase connection — aborting.")
        sys.exit(1)

    rows   = []
    offset = 0
    print(f"  Fetching rows from {table}…", end="", flush=True)
    while True:
        q = (
            backend.supabase.table(table)
            .select(f"id,ticker,{date_field},gap_pct,screener_pass")
        )
        if not force:
            q = q.is_("screener_pass", "null")
        if start_date:
            q = q.gte(date_field, start_date)
        if end_date:
            q = q.lte(date_field, end_date)
        resp = q.range(offset, offset + PAGE_SZ - 1).execute()
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SZ:
            break
        offset += PAGE_SZ
        print(".", end="", flush=True)

    # Handle case where screener_pass column doesn't exist yet
    print(f" {len(rows)} rows.")
    return rows


# ── Step 2: fetch Alpaca daily bars for one date (SMA computation) ────────────

_BAR_CACHE: dict[tuple, dict[str, float]] = {}  # (date_str, "change"/"sma20"/"sma50") → {ticker: val}

def fetch_bar_stats(trade_date_str: str, tickers: list[str]) -> dict[str, dict]:
    """Return {ticker: {change_pct, close, sma20, sma50}} for tickers on trade_date."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests  import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    import pandas as pd

    api_key    = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        return {}

    client = StockHistoricalDataClient(api_key, secret_key)
    trade_date = datetime.strptime(trade_date_str, "%Y-%m-%d").date()
    # Need 70 calendar days back to cover 50 trading days for SMA50
    start_dt = datetime(trade_date.year, trade_date.month, trade_date.day) - timedelta(days=BAR_LOOKBACK)
    end_dt   = datetime(trade_date.year, trade_date.month, trade_date.day) + timedelta(days=1)

    result: dict[str, dict] = {}

    for i in range(0, len(tickers), BATCH_TICKERS):
        batch = tickers[i : i + BATCH_TICKERS]
        try:
            req  = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Day,
                start=start_dt,
                end=end_dt,
                feed="iex",
            )
            bars = client.get_stock_bars(req)
            df   = bars.df
            if df.empty:
                continue
            for sym in batch:
                try:
                    if isinstance(df.index, pd.MultiIndex):
                        sym_df = df.xs(sym, level="symbol").copy()
                    else:
                        sym_df = df.copy()
                    if sym_df.empty:
                        continue
                    sym_df = sym_df.sort_index()
                    closes = sym_df["close"].values
                    if len(closes) < 2:
                        continue
                    last_close = float(closes[-1])
                    prev_close = float(closes[-2])
                    change_pct = ((last_close - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
                    sma20 = float(closes[-20:].mean()) if len(closes) >= 20 else float(closes.mean())
                    sma50 = float(closes[-50:].mean()) if len(closes) >= 50 else float(closes.mean())
                    result[sym] = {
                        "change_pct": change_pct,
                        "close":      last_close,
                        "sma20":      sma20,
                        "sma50":      sma50,
                    }
                except Exception:
                    pass
        except Exception as e:
            _log.warning(f"  Alpaca error for {trade_date_str}: {e}")

    return result


# ── Step 3: classify one ticker ───────────────────────────────────────────────

def classify_pass(ticker: str, row_gap_pct, bar_stats: dict) -> str:
    """Return 'gap' | 'trend' | 'other'.

    squeeze can't be inferred historically (no short-interest data at that time),
    so squeeze tickers fall through to 'gap' or 'trend' or 'other'.

    Priority: gap (≥3%) → trend (≥1% + above both SMAs) → other.

    Note: live paper_trades rows for squeeze candidates are tagged 'squeeze' by
    the bot at order placement — this backfill only applies 'gap'/'trend'/'other'.
    """
    stats = bar_stats.get(ticker) or bar_stats.get(ticker.upper()) or {}

    # Prefer the stored gap_pct column (already computed by batch_backtest); fall back to bars
    chg = float(row_gap_pct) if row_gap_pct is not None else stats.get("change_pct", 0.0)

    if chg >= 3.0:
        return "gap"

    close = stats.get("close")
    sma20 = stats.get("sma20")
    sma50 = stats.get("sma50")

    if (chg >= 1.0
            and close is not None and sma20 is not None and sma50 is not None
            and close > sma20 and close > sma50):
        return "trend"

    return "other"


# ── Step 4: write screener_pass ───────────────────────────────────────────────

def update_one(table: str, row_id: int, screener_pass: str):
    backend.supabase.table(table).update(
        {"screener_pass": screener_pass}
    ).eq("id", row_id).execute()


def write_passes(table: str, rows_for_date: list[dict], bar_stats: dict,
                 dry_run: bool) -> tuple[int, int]:
    updates  = []
    no_data  = 0
    for r in rows_for_date:
        ticker    = r.get("ticker", "")
        row_gap   = r.get("gap_pct")
        stats     = bar_stats.get(ticker) or bar_stats.get(ticker.upper()) or {}
        if not stats and row_gap is None:
            no_data += 1
            updates.append((r["id"], "other"))   # safe fallback — no bar data
            continue
        sp = classify_pass(ticker, row_gap, bar_stats)
        updates.append((r["id"], sp))

    if dry_run or not updates:
        return len(updates), no_data

    success = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(update_one, table, rid, sp): rid for rid, sp in updates}
        for fut in as_completed(futures):
            try:
                fut.result()
                success += 1
            except Exception as e:
                _log.warning(f"  update failed: {e}")

    return success, no_data


# ── Per-table processing ──────────────────────────────────────────────────────

def process_table(table: str, date_field: str, dry_run: bool, force: bool,
                  start_date=None, end_date=None) -> tuple[int, int, int]:
    print(f"\n{'─'*64}")
    print(f"  Table: {table}")
    print(f"{'─'*64}")

    try:
        rows = fetch_rows(table, date_field, start_date, end_date, force)
    except Exception as e:
        _log.warning(f"  Could not fetch rows (column may not exist yet): {e}")
        print(f"  ⚠  screener_pass column not found in {table}.")
        print(f"  Run the migration SQL in Supabase first, then re-run this script.")
        return 0, 0, 0

    if not rows:
        print(f"  Nothing to classify — all rows already have screener_pass.")
        return 0, 0, 0

    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        d = (r.get(date_field) or "")[:10]
        if d:
            by_date[d].append(r)
    dates = sorted(by_date.keys())
    print(f"  Dates : {len(dates)}  ({dates[0]} → {dates[-1]})")
    print(f"  Rows  : {len(rows)}")
    print()

    total_written  = 0
    total_no_data  = 0
    total_tickers  = 0

    for di, trade_date_str in enumerate(dates, 1):
        date_rows = by_date[trade_date_str]
        tickers   = sorted({r["ticker"] for r in date_rows})
        print(f"  [{di:4d}/{len(dates)}]  {trade_date_str}  "
              f"{len(tickers):3d} tickers  {len(date_rows):4d} rows", end="  ", flush=True)

        bar_stats          = fetch_bar_stats(trade_date_str, tickers)
        written, no_data   = write_passes(table, date_rows, bar_stats, dry_run)
        total_written     += written
        total_no_data     += no_data
        total_tickers     += len(tickers)
        print(f"→ {written} classified  {no_data} bars-missing (→ 'other')")

    return total_written, total_no_data, total_tickers


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    print("=" * 64)
    print("  EdgeIQ — Screener Pass Backfill")
    if args.dry_run:
        print("  *** DRY RUN — no writes ***")
    print("=" * 64)

    tables_to_process = [
        (t, f) for t, f in TABLES
        if args.table is None or args.table == t
    ]

    grand_written = 0
    grand_missing = 0
    t0 = time.time()

    for table, date_field in tables_to_process:
        written, missing, _ = process_table(
            table, date_field,
            dry_run=args.dry_run,
            force=args.force,
            start_date=args.start,
            end_date=args.end,
        )
        grand_written += written
        grand_missing += missing

    elapsed = time.time() - t0
    print()
    print("=" * 64)
    print(f"  DONE in {elapsed:.0f}s")
    print(f"  screener_pass written : {grand_written}")
    print(f"  no bars (→ 'other')   : {grand_missing}")
    print("=" * 64)

    if not args.dry_run:
        append_backfill_history(
            script="backfill_screener_pass",
            health={
                "rows_written": grand_written,
                "rows_no_bars": grand_missing,
                "elapsed_s":    round(elapsed, 1),
            },
            logger=_log,
        )


if __name__ == "__main__":
    main()
