"""
backfill_screener_pass.py
─────────────────────────
Backfills the `screener_pass` column on backtest_sim_runs and paper_trades.

Classification rules (applied at row level using Alpaca daily bars):
  - 'gap'     : abs(daily change) ≥ 3%  (gap-of-day)
  - 'trend'   : abs(daily change) ≥ 1% AND close > SMA20 AND close > SMA50
  - 'squeeze' : falls back to 'other' (no short-interest data available historically)
  - 'other'   : everything else

Two-pass approach
─────────────────
Pass 1 (no API): 98%+ of rows have gap_pct already stored in the DB from the
  batch backtest pipeline. Any row with abs(gap_pct) ≥ 3 → 'gap'; rows with
  abs(gap_pct) < 1 → 'other'. Both cases are classified with zero API calls.

Pass 2 (API): Only rows where 1 ≤ abs(gap_pct) < 3 need Alpaca daily bars
  to compute SMA20/SMA50 and determine 'trend' vs 'other'. Historically this
  is ~1-2% of rows. Alpaca calls are rate-limited to ≤ MAX_ALPACA_CALLS_PER_MIN
  and batched at 50 tickers per call.

DB writes: batch-upserted in chunks of UPSERT_BATCH_SZ (500 rows per call)
for fast throughput. Falls back to individual row updates on upsert error.

Usage:
  python backfill_screener_pass.py                     # fill NULLs only
  python backfill_screener_pass.py --force             # re-classify all rows
  python backfill_screener_pass.py --dry-run           # print plan, no writes
  python backfill_screener_pass.py --table paper_trades
  python backfill_screener_pass.py --start 2025-01-01 --end 2025-12-31
"""

import sys, os, time, argparse, logging
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend
from backfill_utils import append_backfill_history

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger(__name__)

PAGE_SZ               = 1000
BATCH_TICKERS         = 50
BAR_LOOKBACK_DAYS     = 80    # calendar days before date to cover SMA50
UPSERT_BATCH_SZ       = 2000  # rows per Supabase upsert call
MAX_ALPACA_CALLS_PER_MIN = 195  # stay under Alpaca 200 req/min limit

TABLES = [
    ("backtest_sim_runs", "sim_date"),
    ("paper_trades",      "trade_date"),
]


# ── Rate limiter ──────────────────────────────────────────────────────────────

class _RateLimiter:
    """Token-bucket rate limiter for Alpaca API calls (calls/minute)."""
    def __init__(self, calls_per_min: int):
        self._interval = 60.0 / calls_per_min
        self._last_call = 0.0

    def acquire(self):
        now  = time.monotonic()
        wait = self._interval - (now - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()


_alpaca_rl = _RateLimiter(MAX_ALPACA_CALLS_PER_MIN)


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

    print(f" {len(rows)} rows.")
    return rows


# ── Step 2: Alpaca bar fetch (Pass 2 — ambiguous rows only) ──────────────────

def fetch_bar_stats_for_date(trade_date_str: str, tickers: list[str]) -> dict[str, dict]:
    """Fetch daily bars for a specific date (Pass 2 fallback for 1-3% gap rows).

    Returns {ticker: {change_pct, close, sma20, sma50}}.
    Rate-limited via _alpaca_rl.acquire() — one token per 50-ticker batch.
    """
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
    start_dt   = datetime(trade_date.year, trade_date.month, trade_date.day) - timedelta(days=BAR_LOOKBACK_DAYS)
    end_dt     = datetime(trade_date.year, trade_date.month, trade_date.day) + timedelta(days=1)

    result: dict[str, dict] = {}

    for i in range(0, len(tickers), BATCH_TICKERS):
        batch = tickers[i : i + BATCH_TICKERS]
        _alpaca_rl.acquire()
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


# ── Step 3: classify ──────────────────────────────────────────────────────────

def classify_pass(ticker: str, row_gap_pct, bar_stats: dict) -> str:
    """Return 'gap' | 'trend' | 'other'.

    squeeze can't be inferred historically (no short-interest data),
    so squeeze tickers fall through to 'gap'/'trend'/'other'.

    Priority: gap (abs ≥ 3%) → trend (abs ≥ 1% + close > SMA20 & SMA50) → other.

    Note: live paper_trades rows for squeeze candidates are tagged 'squeeze' by
    the bot at order placement — this backfill only applies 'gap'/'trend'/'other'.
    """
    stats = bar_stats.get(ticker) or bar_stats.get(ticker.upper()) or {}
    chg   = float(row_gap_pct) if row_gap_pct is not None else stats.get("change_pct", 0.0)

    if abs(chg) >= 3.0:
        return "gap"

    close = stats.get("close")
    sma20 = stats.get("sma20")
    sma50 = stats.get("sma50")

    if (abs(chg) >= 1.0
            and close is not None and sma20 is not None and sma50 is not None
            and close > sma20 and close > sma50):
        return "trend"

    return "other"


# ── Step 4: batch upsert ──────────────────────────────────────────────────────

def upsert_passes(table: str, updates: list[dict], dry_run: bool) -> int:
    """Write {id, screener_pass} records to Supabase via batched upsert."""
    if dry_run or not updates:
        return len(updates)
    success = 0
    for i in range(0, len(updates), UPSERT_BATCH_SZ):
        chunk = updates[i : i + UPSERT_BATCH_SZ]
        try:
            backend.supabase.table(table).upsert(chunk, on_conflict="id").execute()
            success += len(chunk)
        except Exception as e:
            _log.warning(f"  upsert failed ({len(chunk)} rows): {e}")
            for row in chunk:
                try:
                    backend.supabase.table(table).update(
                        {"screener_pass": row["screener_pass"]}
                    ).eq("id", row["id"]).execute()
                    success += 1
                except Exception as e2:
                    _log.warning(f"  row update failed id={row['id']}: {e2}")
    return success


# ── Per-table processing ──────────────────────────────────────────────────────

def _fmt_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def process_table(table: str, date_field: str, dry_run: bool, force: bool,
                  start_date=None, end_date=None) -> tuple[int, int, int]:
    print(f"\n{'─'*64}")
    print(f"  Table: {table}")
    print(f"{'─'*64}")

    try:
        rows = fetch_rows(table, date_field, start_date, end_date, force)
    except Exception as e:
        _log.warning(f"  Could not fetch rows: {e}")
        print(f"  ⚠  screener_pass column not found in {table}.")
        print(f"  Run the migration SQL in Supabase first, then re-run this script.")
        return 0, 0, 0

    if not rows:
        print(f"  Nothing to classify — all rows already have screener_pass.")
        return 0, 0, 0

    # ── Pass 1: classify using gap_pct (no API needed for 98%+ of rows) ──────
    pass1_updates: list[dict] = []
    pass2_rows:    list[dict] = []   # rows needing SMA confirmation (1-3% gap)

    for r in rows:
        gp = r.get("gap_pct")
        if gp is not None:
            gp_f = float(gp)
            if abs(gp_f) >= 3.0:
                pass1_updates.append({"id": r["id"], "screener_pass": "gap"})
                continue
            if abs(gp_f) < 1.0:
                pass1_updates.append({"id": r["id"], "screener_pass": "other"})
                continue
            # 1 ≤ abs(gap_pct) < 3 — need SMA data
            pass2_rows.append(r)
        else:
            # No gap_pct stored — need bars to compute change_pct
            pass2_rows.append(r)

    n_pass1 = len(pass1_updates)
    n_pass2 = len(pass2_rows)
    n_total  = len(rows)

    print(f"  Pass 1 (gap_pct only, no API): {n_pass1:,} rows  →  "
          f"{sum(1 for u in pass1_updates if u['screener_pass']=='gap'):,} gap / "
          f"{sum(1 for u in pass1_updates if u['screener_pass']=='other'):,} other")
    print(f"  Pass 2 (SMA fetch via Alpaca): {n_pass2:,} rows  (1–3% gap zone)")
    print()

    t0 = time.monotonic()

    # Write Pass 1 results
    written1 = upsert_passes(table, pass1_updates, dry_run)
    print(f"  Pass 1 upserted: {written1:,} rows  ({time.monotonic()-t0:.1f}s)", flush=True)

    # ── Pass 2: fetch SMA data for ambiguous rows ─────────────────────────────
    written2 = 0
    no_data2 = 0

    if pass2_rows:
        by_date: dict[str, list[dict]] = defaultdict(list)
        for r in pass2_rows:
            d = (r.get(date_field) or "")[:10]
            if d:
                by_date[d].append(r)
        dates = sorted(by_date.keys())

        print(f"  Pass 2: {len(dates)} unique dates to fetch")
        print(f"  Rate:   ≤{MAX_ALPACA_CALLS_PER_MIN} Alpaca calls/min (within API limit)")
        print()

        pass2_updates: list[dict] = []
        t_pass2 = time.monotonic()

        for di, trade_date_str in enumerate(dates, 1):
            date_rows = by_date[trade_date_str]
            tickers   = sorted({r["ticker"] for r in date_rows})

            elapsed   = time.monotonic() - t_pass2
            eta_str   = ""
            if di > 1 and elapsed > 0:
                rate    = elapsed / (di - 1)
                eta_s   = rate * (len(dates) - di + 1)
                eta_str = f"  ETA {_fmt_eta(eta_s)}"

            print(f"  P2 [{di:4d}/{len(dates)}]  {trade_date_str}  "
                  f"{len(tickers):3d} tickers{eta_str}", end="  ", flush=True)

            bar_stats = fetch_bar_stats_for_date(trade_date_str, tickers)
            if not bar_stats:
                no_data2 += len(date_rows)
                for r in date_rows:
                    pass2_updates.append({"id": r["id"], "screener_pass": "other"})
                print("no bars → 'other'")
                continue

            for r in date_rows:
                sp = classify_pass(r["ticker"], r.get("gap_pct"), bar_stats)
                pass2_updates.append({"id": r["id"], "screener_pass": sp})
            print(f"→ classified")

        written2 = upsert_passes(table, pass2_updates, dry_run)
        print(f"\n  Pass 2 upserted: {written2:,} rows  ({no_data2} no-bars → 'other')")

    return written1 + written2, no_data2, 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    print("=" * 64)
    print("  EdgeIQ — Screener Pass Backfill")
    print(f"  Strategy: gap_pct fast-path + Alpaca SMA for 1–3% zone")
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
    print(f"  DONE in {_fmt_eta(elapsed)}")
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
