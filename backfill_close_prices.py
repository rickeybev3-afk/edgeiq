"""
backfill_close_prices.py
────────────────────────
Fetches EOD close prices from Alpaca for all backtest_sim_runs rows where
close_price IS NULL, then writes them back to Supabase.

After this runs, execute run_sim_backfill.py to compute eod_pnl_r for every
row that now has a close_price.

Strategy
────────
- Group null rows by scan_date so we do one Alpaca call per day (batch of
  up to 50 tickers) rather than one call per row.
- Use TimeFrame.Day bars for that calendar date only (start=date, end=date+1).
- Feed: IEX (consistent with what batch_backtest.py uses).
- Concurrent Supabase updates (same pattern as run_sim_backfill.py).

Usage
─────
  python backfill_close_prices.py          # all rows with null close_price
  python backfill_close_prices.py --dry-run  # print plan, no writes
"""

import sys, os, time, argparse
from datetime import datetime, timedelta, date as date_type
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend

PAGE_SZ      = 1000
BATCH_TICKERS = 50    # Alpaca multi-symbol batch size
MAX_WORKERS  = 20     # concurrent Supabase update threads


# ── Argument parsing ──────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Backfill close_price in backtest_sim_runs")
    p.add_argument("--dry-run", action="store_true",
                   help="Print plan without writing anything to Supabase")
    return p.parse_args()


# ── Step 1: fetch all (id, ticker, scan_date) where close_price IS NULL ───────

def fetch_null_rows() -> list[dict]:
    """Return all backtest_sim_runs rows with close_price IS NULL."""
    if not backend.supabase:
        print("No Supabase connection — aborting.")
        sys.exit(1)

    rows   = []
    offset = 0
    print("Fetching rows with null close_price…", end="", flush=True)
    while True:
        resp = (
            backend.supabase.table("backtest_sim_runs")
            .select("id,ticker,sim_date,actual_outcome")
            .is_("close_price", "null")
            .range(offset, offset + PAGE_SZ - 1)
            .execute()
        )
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SZ:
            break
        offset += PAGE_SZ
        print(".", end="", flush=True)

    print(f" {len(rows)} rows found.")
    return rows


# ── Step 2: group by date → tickers ──────────────────────────────────────────

def group_by_date(rows: list[dict]) -> dict[str, list[dict]]:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        sd = r.get("sim_date", "")[:10]   # "YYYY-MM-DD"
        if sd:
            by_date[sd].append(r)
    return dict(sorted(by_date.items()))   # chronological


# ── Step 3: fetch daily close prices from Alpaca for one date ─────────────────

def fetch_closes_for_date(trade_date_str: str, tickers: list[str]) -> dict[str, float]:
    """Return {ticker: close_price} for every ticker that had data on that date."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests  import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    import pandas as pd

    api_key    = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        print("  [WARN] ALPACA_API_KEY / ALPACA_SECRET_KEY not set — skipping date")
        return {}

    client = StockHistoricalDataClient(api_key, secret_key)

    trade_date = datetime.strptime(trade_date_str, "%Y-%m-%d").date()
    start_dt   = datetime(trade_date.year, trade_date.month, trade_date.day)
    end_dt     = start_dt + timedelta(days=1)

    closes: dict[str, float] = {}

    # Batch tickers to stay well within Alpaca limits
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
            if isinstance(df.index, pd.MultiIndex):
                for sym in batch:
                    try:
                        sym_df = df.xs(sym, level="symbol")
                        if not sym_df.empty:
                            closes[sym] = float(sym_df["close"].iloc[-1])
                    except KeyError:
                        pass
            else:
                # Single-ticker result
                if not df.empty and len(batch) == 1:
                    closes[batch[0]] = float(df["close"].iloc[-1])
        except Exception as e:
            print(f"  [WARN] Alpaca error for {trade_date_str} batch {i//BATCH_TICKERS+1}: {e}")

    return closes


# ── Step 4: write close prices back to Supabase ───────────────────────────────

def update_one(row_id: int, close_price: float):
    backend.supabase.table("backtest_sim_runs").update(
        {"close_price": round(close_price, 4)}
    ).eq("id", row_id).execute()


def write_closes(rows_for_date: list[dict], closes: dict[str, float],
                 dry_run: bool) -> tuple[int, int]:
    """Write close_price for each row whose ticker appears in closes.
    Returns (updated, skipped).
    """
    updates = []
    skipped = 0
    for r in rows_for_date:
        ticker = r.get("ticker", "")
        cp     = closes.get(ticker)
        if cp is None:
            skipped += 1
            continue
        updates.append((r["id"], cp))

    if dry_run or not updates:
        return len(updates), skipped

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(update_one, rid, cp): rid for rid, cp in updates}
        errors  = 0
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  [ERROR] update failed: {e}")

    return len(updates), skipped


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    print("=" * 64)
    print("  EdgeIQ — Close Price Backfill")
    if args.dry_run:
        print("  *** DRY RUN — no writes ***")
    print("=" * 64)

    rows     = fetch_null_rows()
    if not rows:
        print("Nothing to backfill — all rows already have a close_price.")
        return

    by_date  = group_by_date(rows)
    dates    = sorted(by_date.keys())
    print(f"Dates to process : {len(dates)}  ({dates[0]} → {dates[-1]})")
    print(f"Total null rows  : {len(rows)}")
    print()

    total_updated = 0
    total_skipped = 0
    t0 = time.time()

    for di, trade_date_str in enumerate(dates, 1):
        date_rows = by_date[trade_date_str]
        tickers   = sorted({r["ticker"] for r in date_rows})
        print(f"[{di:4d}/{len(dates)}]  {trade_date_str}  "
              f"{len(tickers):3d} tickers  {len(date_rows):4d} rows", end="  ", flush=True)

        closes  = fetch_closes_for_date(trade_date_str, tickers)
        updated, skipped = write_closes(date_rows, closes, dry_run=args.dry_run)
        total_updated += updated
        total_skipped += skipped
        print(f"→ {updated} written  {skipped} no-data")

    elapsed = time.time() - t0
    print()
    print("=" * 64)
    print(f"  DONE in {elapsed:.0f}s")
    print(f"  close_price written : {total_updated}")
    print(f"  rows without data   : {total_skipped}  (delisted / no IEX coverage)")
    print("=" * 64)

    if not args.dry_run and total_updated > 0:
        print()
        print("Next step: run  python run_sim_backfill.py")
        print("That will compute eod_pnl_r for all newly filled close prices.")


if __name__ == "__main__":
    main()
