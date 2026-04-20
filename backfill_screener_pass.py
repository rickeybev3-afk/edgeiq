"""
backfill_screener_pass.py
─────────────────────────
Backfills the `screener_pass` column on backtest_sim_runs and paper_trades.

Classification rules (directional — positive daily change only):
  - 'gap'      : close-to-close daily change ≥ 3.0%
  - 'trend'    : close-to-close daily change ≥ 1.0% AND close > SMA20 AND close > SMA50
  - 'gap_down' : already tagged by the paper-trader bot (Bearish Break universe); preserved as-is
  - 'squeeze'  : falls back to 'other' (no short-interest data available historically)
  - 'other'    : everything else (including down days)

Note on gap_down: this pass is set by the bot at order placement for Bearish Break
tickers and cannot be inferred from price-change data alone. Rows already tagged
'gap_down' are therefore skipped during --force re-classification to preserve the
live tag.

Close-to-close change computation
──────────────────────────────────
Primary (zero API calls — 100% of current rows have all three columns):
  prev_close   = open_price / (1 + gap_pct/100)
  daily_change = (close_price - prev_close) / prev_close × 100

  close_price and open_price are sourced from Alpaca bars by the batch backtest
  pipeline and backfill_close_prices.py — so this IS Alpaca-derived data.

Pass 2 (Alpaca API — SMA confirmation for 1-3% change zone):
  Rows where daily_change falls in [1%, 3%) also need SMA20/SMA50 to distinguish
  'trend' from 'other'. For these rows a targeted Alpaca call is made to fetch the
  required moving averages. Rate-limited to ≤MAX_ALPACA_CALLS_PER_MIN.

DB writes: batch-upserted in chunks of UPSERT_BATCH_SZ.
Fallback: individual row updates if a batch upsert fails.

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
            .select(f"id,ticker,{date_field},gap_pct,open_price,close_price,screener_pass")
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


# ── Step 2a: Compute close-to-close daily change from stored DB columns ──────

def compute_daily_change(row: dict) -> float | None:
    """Compute close-to-close daily change (%) from stored DB columns.

    Formula:
        prev_close   = open_price / (1 + gap_pct / 100)
        daily_change = (close_price - prev_close) / prev_close × 100

    open_price and close_price come from Alpaca bar data stored by the batch
    backtest pipeline and backfill_close_prices.py. gap_pct is the
    open-vs-prev-close gap (stored from the same pipeline).

    Returns None if any column is missing, zero, or produces a division error.
    """
    try:
        open_p  = float(row.get("open_price") or 0)
        close_p = float(row.get("close_price") or 0)
        gp      = float(row.get("gap_pct") or 0)
        if open_p <= 0 or close_p <= 0:
            return None
        denom = 1.0 + gp / 100.0
        if denom <= 0:
            return None
        prev_close = open_p / denom
        if prev_close <= 0:
            return None
        return (close_p - prev_close) / prev_close * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# ── Step 2b: Alpaca bar fetch (Pass 2 — SMA for 1-3% change zone) ─────────────

def fetch_bar_stats_for_date(trade_date_str: str, tickers: list[str]) -> dict[str, dict]:
    """Fetch Alpaca daily bars for a specific date to compute SMA20/SMA50.

    Used only for Pass 2 rows (1% ≤ daily_change < 3%) where SMA alignment
    determines 'trend' vs 'other'.

    Rate-limited via _alpaca_rl — one token per 50-ticker API call.
    Returns {ticker: {close, sma20, sma50}}.
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
                    if len(closes) < 1:
                        continue
                    last_close = float(closes[-1])
                    sma20 = float(closes[-20:].mean()) if len(closes) >= 20 else float(closes.mean())
                    sma50 = float(closes[-50:].mean()) if len(closes) >= 50 else float(closes.mean())
                    result[sym] = {"close": last_close, "sma20": sma20, "sma50": sma50}
                except Exception:
                    pass
        except Exception as e:
            _log.warning(f"  Alpaca error for {trade_date_str}: {e}")

    return result


# ── Step 3: classify ──────────────────────────────────────────────────────────

def classify_pass(daily_change: float | None, bar_sma: dict | None) -> str:
    """Return 'gap' | 'trend' | 'other'.

    Uses directional (non-absolute) thresholds — only positive daily change
    days qualify as gap or trend. Large down days are classified as 'other'.

    Priority: gap (change ≥ 3%) → trend (change ≥ 1% + close > SMA20 & SMA50) → other.

    Arguments:
        daily_change: close-to-close % change from DB columns or None if missing.
        bar_sma:      {close, sma20, sma50} from Alpaca bars, or None for Pass 1 rows.

    Note: live paper_trades rows for squeeze candidates are tagged 'squeeze' by
    the bot at order placement — this backfill only applies 'gap'/'trend'/'other'.
    """
    chg = daily_change if daily_change is not None else 0.0

    if chg >= 3.0:
        return "gap"

    if bar_sma is not None:
        close = bar_sma.get("close")
        sma20 = bar_sma.get("sma20")
        sma50 = bar_sma.get("sma50")
        if (chg >= 1.0
                and close is not None and sma20 is not None and sma50 is not None
                and close > sma20 and close > sma50):
            return "trend"

    return "other"


# ── Step 4: batch upsert ──────────────────────────────────────────────────────

def upsert_passes(table: str, updates: list[dict], dry_run: bool) -> int:
    """Write {id, screener_pass} records via batched upsert. Falls back to single-row."""
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

    # ── Pass 0: preserve existing gap_down rows (bot-tagged, cannot be inferred) ─
    # gap_down is set at order placement for Bearish Break tickers and cannot be
    # recovered from price data alone. Skip these rows during --force runs.
    preserved_gd = 0
    rows_to_classify: list[dict] = []
    for r in rows:
        if (r.get("screener_pass") or "").strip().lower() == "gap_down":
            preserved_gd += 1
        else:
            rows_to_classify.append(r)
    if preserved_gd:
        print(f"  Pass 0 (gap_down preserved): {preserved_gd:,} rows skipped (live bot tag)")
    rows = rows_to_classify

    # ── Pass 1: classify using DB-stored close_price / open_price / gap_pct ──
    # Computes true close-to-close daily change (same data as Alpaca bars,
    # already stored by the batch backtest + backfill_close_prices pipeline).
    pass1_updates: list[dict] = []  # clearly classifiable without SMA
    pass2_rows:    list[dict] = []  # 1-3% zone — need SMA20/SMA50 check

    n_gap, n_other_p1, n_no_close = 0, 0, 0

    for r in rows:
        chg = compute_daily_change(r)
        if chg is None:
            # Missing close_price/open_price — fall through to Pass 2 for SMA + change
            n_no_close += 1
            pass2_rows.append(r)
            continue
        if chg >= 3.0:
            pass1_updates.append({"id": r["id"], "screener_pass": "gap"})
            n_gap += 1
        elif chg < 1.0:
            pass1_updates.append({"id": r["id"], "screener_pass": "other"})
            n_other_p1 += 1
        else:
            # 1% ≤ chg < 3% — need SMA20/SMA50 to determine 'trend' vs 'other'
            r["_daily_change"] = chg   # carry computed value forward
            pass2_rows.append(r)

    n_pass1 = len(pass1_updates)
    n_pass2 = len(pass2_rows)

    print(f"  Pass 1 (DB close-to-close, no API): {n_pass1:,} rows")
    print(f"    {n_gap:,} gap (change ≥ 3%) | {n_other_p1:,} other (change < 1%)")
    print(f"  Pass 2 (Alpaca SMA fetch):          {n_pass2:,} rows "
          f"(1–3% zone + {n_no_close} missing close)")
    print()

    t0 = time.monotonic()
    written1 = upsert_passes(table, pass1_updates, dry_run)
    print(f"  Pass 1 upserted: {written1:,} rows  ({time.monotonic()-t0:.1f}s)", flush=True)

    # ── Pass 2: fetch SMA for 1-3% change rows ────────────────────────────────
    written2 = 0
    no_data2 = 0

    if pass2_rows:
        by_date: dict[str, list[dict]] = defaultdict(list)
        for r in pass2_rows:
            d = (r.get(date_field) or "")[:10]
            if d:
                by_date[d].append(r)
        dates = sorted(by_date.keys())

        print(f"  Pass 2: {len(dates)} unique dates  (rate ≤{MAX_ALPACA_CALLS_PER_MIN}/min)")
        print()

        pass2_updates: list[dict] = []
        t_p2 = time.monotonic()

        for di, trade_date_str in enumerate(dates, 1):
            date_rows = by_date[trade_date_str]
            tickers   = sorted({r["ticker"] for r in date_rows})

            elapsed   = time.monotonic() - t_p2
            eta_str   = ""
            if di > 1 and elapsed > 0:
                rate    = elapsed / (di - 1)
                eta_str = f"  ETA {_fmt_eta(rate * (len(dates) - di + 1))}"

            print(f"  P2 [{di:4d}/{len(dates)}]  {trade_date_str}  "
                  f"{len(tickers):3d} tickers{eta_str}", end="  ", flush=True)

            bar_stats = fetch_bar_stats_for_date(trade_date_str, tickers)
            if not bar_stats:
                no_data2 += len(date_rows)
                for r in date_rows:
                    # Use daily_change if computed, else 'other' (no SMA available)
                    chg = r.get("_daily_change")
                    sp  = "gap" if (chg is not None and chg >= 3.0) else "other"
                    pass2_updates.append({"id": r["id"], "screener_pass": sp})
                print("no bars → classified by change only")
                continue

            for r in date_rows:
                chg     = r.get("_daily_change")   # computed in Pass 1 split
                sma_key = r["ticker"]
                sma     = bar_stats.get(sma_key) or bar_stats.get(sma_key.upper())
                sp      = classify_pass(chg, sma)
                pass2_updates.append({"id": r["id"], "screener_pass": sp})
            print("→ classified")

        written2 = upsert_passes(table, pass2_updates, dry_run)
        print(f"\n  Pass 2 upserted: {written2:,} rows  ({no_data2} no-bars fallback)")

    return written1 + written2, no_data2, 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    print("=" * 64)
    print("  EdgeIQ — Screener Pass Backfill")
    print("  Classification: directional close-to-close change from DB cols")
    print("  gap ≥ 3%  |  trend ≥ 1% + close > SMA20 & SMA50  |  other")
    print("  gap_down rows (bot-tagged Bearish Break) are preserved as-is")
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
                "rows_written":   grand_written,
                "rows_no_bars":   grand_missing,
                "elapsed_s":      round(elapsed, 1),
                "classification": "directional close-to-close from DB cols + Alpaca SMA for 1-3% zone",
            },
            logger=_log,
        )


if __name__ == "__main__":
    main()
