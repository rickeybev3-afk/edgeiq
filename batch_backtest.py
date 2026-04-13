#!/usr/bin/env python3
"""
batch_backtest.py — Historical watchlist reconstruction + structure backtest

Reconstructs what would have been on the daily Finviz watchlist for each
historical trading day (using Alpaca daily OHLCV data to apply gap%/RVOL/price
filters against a static small-cap universe pulled from Finviz today), runs the
full IB/Volume Profile backtest engine on each qualifying ticker, and saves
results to Supabase.

The Finviz float filter is today's snapshot (static approximation — acceptable
over a 60-90 day lookback window since small-float status rarely changes fast).

Usage:
  python batch_backtest.py                          # last 60 trading days
  python batch_backtest.py --days 30               # last 30 trading days
  python batch_backtest.py --start 2026-02-01      # from specific date
  python batch_backtest.py --start 2026-02-01 --end 2026-03-15
  python batch_backtest.py --feed sip              # use SIP feed (paid)
  python batch_backtest.py --dry-run               # skip Supabase save
  python batch_backtest.py --gap 5.0               # 5% gap minimum
  python batch_backtest.py --user-id <supabase_id> # scope to specific user
"""

import os
import sys
import time
import argparse
import logging
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend


# ─────────────────────────────────────────────────────────────────────────────
# US market holidays (major closures only, 2025–2026)
# ─────────────────────────────────────────────────────────────────────────────

_MARKET_HOLIDAYS = {
    date(2025, 1, 1),  date(2025, 1, 20),  date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26),  date(2025, 6, 19),
    date(2025, 7, 4),  date(2025, 9, 1),   date(2025, 11, 27),
    date(2025, 12, 25),
    date(2026, 1, 1),  date(2026, 1, 19),  date(2026, 2, 16),
    date(2026, 4, 3),  date(2026, 5, 25),  date(2026, 6, 19),
    date(2026, 7, 3),  date(2026, 9, 7),   date(2026, 11, 26),
    date(2026, 12, 25),
}


def _is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _MARKET_HOLIDAYS


def get_trading_days(start: date, end: date) -> list:
    """Return all trading days in [start, end) — never includes today."""
    today = date.today()
    days, cur = [], start
    while cur <= end:
        if _is_trading_day(cur) and cur < today:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def walk_back_trading_days(from_date: date, n: int) -> date:
    """Return the start date that gives n trading days ending at from_date."""
    count, cur = 0, from_date
    while count < n:
        if _is_trading_day(cur):
            count += 1
        cur -= timedelta(days=1)
    return cur + timedelta(days=1)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Finviz small-cap universe (no gap%/RVOL filters)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_smallcap_universe(
    float_max_m: float = 100.0,
    price_min:   float = 1.0,
    price_max:   float = 20.0,
    max_tickers: int   = 3000,
) -> list:
    """Scrape Finviz for all small-float US stocks without gap%/RVOL day-filters.

    NOTE: We intentionally do NOT call `backend.fetch_finviz_watchlist()` here.
    That function always bakes in a `ta_change_uX` (daily change%) and `sh_relvol_o1`
    (relative volume ≥ 1x) filter — both of which are day-specific and would give us
    only TODAY's movers instead of the full static small-cap pool.  Since we cannot
    modify backend.py (architecture rule), this local function drops those two filters
    and returns the full universe that COULD appear on any given scan day.
    The gap% and RVOL filters are re-applied per-day using Alpaca historical data.

    Keeps only:  geo_usa | float ≤ float_max_m M | avg_vol ≥ 1M | price $1–$20
    Returns a deduplicated list of uppercase tickers, up to max_tickers.
    Returns [] on error (script will exit with a clear message).
    """
    import re
    import requests
    from bs4 import BeautifulSoup

    float_filter = f"sh_float_u{int(float_max_m)}"
    price_lo     = f"sh_price_o{int(price_min)}"
    price_hi     = f"sh_price_u{int(price_max)}"

    filters = ",".join([
        "geo_usa",
        float_filter,
        "sh_avgvol_o1000",
        price_lo,
        price_hi,
    ])

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://finviz.com/",
    })

    tickers, page = [], 0
    while len(tickers) < max_tickers:
        start_row = page * 20 + 1
        url = (
            f"https://finviz.com/screener.ashx"
            f"?v=111&f={filters}&o=-avgvol&r={start_row}"
        )
        try:
            resp = sess.get(url, timeout=15, allow_redirects=True)
            resp.raise_for_status()
            soup  = BeautifulSoup(resp.text, "html.parser")
            links = soup.find_all("a", href=re.compile(r"quote\.ashx\?t="))
            page_tix = list(dict.fromkeys([
                lnk.text.strip().upper()
                for lnk in links
                if lnk.text.strip().isalpha() and len(lnk.text.strip()) <= 5
            ]))
            prev_n = len(tickers)
            for t in page_tix:
                if t not in tickers:
                    tickers.append(t)
            if not page_tix or len(tickers) == prev_n:
                break
        except Exception as e:
            print(f"  [WARN] Finviz page {page+1} error: {e}")
            break
        page += 1
        time.sleep(0.5)

    return tickers[:max_tickers]


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Alpaca daily bars (batched multi-ticker request)
# ─────────────────────────────────────────────────────────────────────────────

def _to_date(ts) -> date:
    """Safely extract a date from a Timestamp, tz-aware or not."""
    try:
        if hasattr(ts, "date"):
            return ts.date()
        return pd.Timestamp(ts).date()
    except Exception:
        return ts


def fetch_daily_bars_batch(
    api_key:        str,
    secret_key:     str,
    tickers:        list,
    start_date:     date,
    end_date:       date,
    lookback_extra: int = 50,
    feed:           str = "iex",
) -> dict:
    """Fetch daily OHLCV for a batch of tickers over the full period + lookback.

    `feed` defaults to "iex" regardless of the --feed CLI flag because daily bars
    are used only for universe filtering (gap%, price, RVOL) — not for the actual
    backtest.  IEX daily data is free, complete, and has no SIP recency restriction.
    The --feed CLI arg is passed through to _backtest_single() for intraday bars only.

    Returns {ticker: pd.DataFrame(index=date, columns=[open,high,low,close,volume])}.
    """
    import pandas as pd
    import pytz
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    EASTERN = pytz.timezone("America/New_York")

    fetch_from = EASTERN.localize(
        datetime(start_date.year, start_date.month, start_date.day)
        - timedelta(days=lookback_extra)
    )
    fetch_to = EASTERN.localize(
        datetime(end_date.year, end_date.month, end_date.day)
        + timedelta(days=1)
    )

    client = StockHistoricalDataClient(api_key, secret_key)
    req = StockBarsRequest(
        symbol_or_symbols=tickers,
        timeframe=TimeFrame.Day,
        start=fetch_from,
        end=fetch_to,
        feed=feed,
    )
    try:
        bars = client.get_stock_bars(req)
        raw  = bars.df
    except Exception as e:
        print(f"  [WARN] Alpaca batch error ({len(tickers)} tickers): {e}")
        return {}

    if raw is None or (hasattr(raw, "empty") and raw.empty):
        return {}

    result = {}
    import pandas as pd  # re-import inside function scope for safety

    if isinstance(raw.index, pd.MultiIndex):
        for sym in tickers:
            try:
                sym_df = raw.xs(sym, level="symbol").copy()
                sym_df.index = [_to_date(ts) for ts in sym_df.index]
                sym_df = sym_df.sort_index()
                if not sym_df.empty:
                    result[sym] = sym_df
            except KeyError:
                pass
    else:
        if tickers:
            raw2 = raw.copy()
            raw2.index = [_to_date(ts) for ts in raw2.index]
            raw2 = raw2.sort_index()
            if not raw2.empty:
                result[tickers[0]] = raw2

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Reconstruct daily watchlist from daily bars
# ─────────────────────────────────────────────────────────────────────────────

def reconstruct_daily_watchlist(
    daily_bars:    dict,
    scan_date:     date,
    gap_min_pct:   float = 3.0,
    price_min:     float = 1.0,
    price_max:     float = 20.0,
    rvol_min:      float = 1.0,
    avg_vol_days:  int   = 30,
) -> list:
    """Return tickers that would have appeared on the Finviz scan for scan_date.

    Applies:  gap% ≥ gap_min_pct  |  price $price_min–$price_max  |  RVOL ≥ rvol_min

    RVOL note: we use EOD total volume (not 10:30 AM cutoff volume) because
    we only have daily bars.  This is a good proxy and still filters out flat days.
    """
    qualifying = []
    for sym, df in daily_bars.items():
        if scan_date not in df.index:
            continue
        dates_before = [d for d in df.index if d < scan_date]
        if not dates_before:
            continue

        prev_date  = max(dates_before)
        prev_close = float(df.loc[prev_date, "close"])
        today_open = float(df.loc[scan_date, "open"])
        today_vol  = float(df.loc[scan_date, "volume"])

        if prev_close <= 0 or today_open <= 0:
            continue

        gap_pct = (today_open - prev_close) / prev_close * 100.0
        if gap_pct < gap_min_pct:
            continue
        if not (price_min <= today_open <= price_max):
            continue

        lookback_idx = [d for d in df.index if d < scan_date][-avg_vol_days:]
        if lookback_idx:
            avg_vol = float(df.loc[lookback_idx, "volume"].mean())
            rvol    = today_vol / avg_vol if avg_vol > 0 else 0.0
        else:
            rvol = 0.0

        if rvol < rvol_min:
            continue

        qualifying.append(sym)

    return qualifying


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Load existing records for deduplication
# ─────────────────────────────────────────────────────────────────────────────

def load_existing_pairs(user_id: str = "") -> set:
    """Return set of (ticker, sim_date_str) already in backtest_sim_runs.

    Paginates in 1000-row chunks directly against Supabase (avoids the
    backend helper's 5000-row cap so dedup is always complete regardless
    of how many runs are stored).
    """
    if not backend.supabase:
        return set()
    pairs: set = set()
    chunk_size = 1000
    offset = 0
    while True:
        try:
            q = (
                backend.supabase
                .table("backtest_sim_runs")
                .select("ticker, sim_date")
                .range(offset, offset + chunk_size - 1)
            )
            if user_id:
                q = q.eq("user_id", user_id)
            rows = q.execute().data
            if not rows:
                break
            for r in rows:
                pairs.add((str(r.get("ticker", "")), str(r.get("sim_date", ""))))
            if len(rows) < chunk_size:
                break
            offset += chunk_size
        except Exception as e:
            print(f"  [WARN] Dedup query error at offset {offset}: {e}")
            break
    return pairs


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Batch backtest — historical Finviz watchlist reconstruction",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--start",     type=str,   help="Start date YYYY-MM-DD")
    parser.add_argument("--end",       type=str,   help="End date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--days",      type=int,   default=60,   help="Lookback in trading days if --start omitted (default: 60)")
    parser.add_argument("--feed",      type=str,   default="iex", choices=["iex", "sip"],
                        help="Alpaca data feed for intraday bars (default: iex)")
    parser.add_argument("--gap",       type=float, default=3.0,  help="Min gap%% to qualify (default: 3.0)")
    parser.add_argument("--price-min", type=float, default=1.0,  help="Min open price (default: 1.0)")
    parser.add_argument("--price-max", type=float, default=20.0, help="Max open price (default: 20.0)")
    parser.add_argument("--rvol-min",  type=float, default=1.0,  help="Min relative volume (default: 1.0)")
    parser.add_argument("--float-max", type=float, default=100.0, help="Max float in millions (default: 100)")
    parser.add_argument("--workers",   type=int,   default=8,    help="Parallel backtest workers per day (default: 8)")
    parser.add_argument("--batch",     type=int,   default=50,   help="Ticker batch size for Alpaca daily bars (default: 50)")
    parser.add_argument("--dry-run",   action="store_true",      help="Skip Supabase save (test mode)")
    parser.add_argument("--user-id",   type=str,   default="",   help="Supabase user_id for data scoping")
    args = parser.parse_args()

    api_key    = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        print("ERROR: ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in environment.")
        sys.exit(1)

    # ── Date range ─────────────────────────────────────────────────────────
    today    = date.today()
    end_date = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
    if args.start:
        start_date = date.fromisoformat(args.start)
    else:
        start_date = walk_back_trading_days(end_date, args.days)

    trading_days = get_trading_days(start_date, end_date)
    if not trading_days:
        print("No trading days found in the specified range. Check your dates.")
        sys.exit(0)

    bar  = "=" * 62
    dash = "-" * 62
    print(f"\n{bar}")
    print(f"  EdgeIQ  |  Batch Historical Backtest")
    print(f"{bar}")
    print(f"  Date range  : {start_date}  →  {end_date}")
    print(f"  Trade days  : {len(trading_days)}")
    print(f"  Feed        : {args.feed.upper()}")
    print(f"  Filters     : gap ≥ {args.gap}%  |  ${args.price_min}–${args.price_max}  "
          f"|  RVOL ≥ {args.rvol_min}x  |  float ≤ {args.float_max}M")
    print(f"  Workers     : {args.workers} per day")
    print(f"  Dry run     : {'YES — Supabase save disabled' if args.dry_run else 'No'}")
    print(f"{bar}\n")

    # ─────────────────────────────────────────────────────────────────────
    # STEP 1 — Finviz small-cap universe
    # ─────────────────────────────────────────────────────────────────────
    print("[ 1/4 ]  Fetching Finviz small-cap universe...", flush=True)
    universe = fetch_smallcap_universe(
        float_max_m=args.float_max,
        price_min=args.price_min,
        price_max=args.price_max,
        max_tickers=3000,
    )
    if not universe:
        print("\nERROR: Finviz returned 0 tickers. Check your network or Finviz status.")
        sys.exit(1)
    print(f"       → {len(universe)} tickers in universe\n")

    # ─────────────────────────────────────────────────────────────────────
    # STEP 2 — Alpaca daily bars (batched)
    # ─────────────────────────────────────────────────────────────────────
    print(f"[ 2/4 ]  Fetching Alpaca daily bars (batches of {args.batch})...", flush=True)
    all_daily_bars: dict = {}
    batches = [universe[i : i + args.batch] for i in range(0, len(universe), args.batch)]
    for idx, batch in enumerate(batches):
        n = len(batches)
        print(f"         Batch {idx+1:3d}/{n}  ({len(batch)} tickers)", end="  ", flush=True)
        batch_bars = fetch_daily_bars_batch(
            api_key, secret_key, batch, start_date, end_date,
        )
        all_daily_bars.update(batch_bars)
        print(f"→  {len(batch_bars)} with data", flush=True)
        if idx < n - 1:
            time.sleep(0.35)
    print(f"\n       → Daily bars for {len(all_daily_bars)} tickers total\n")

    # ─────────────────────────────────────────────────────────────────────
    # STEP 3 — Load existing pairs for deduplication
    # ─────────────────────────────────────────────────────────────────────
    print("[ 3/4 ]  Loading existing backtest records for dedup...", flush=True)
    existing_pairs = load_existing_pairs(user_id=args.user_id)
    print(f"       → {len(existing_pairs)} (ticker, date) pairs already in Supabase\n")

    # ─────────────────────────────────────────────────────────────────────
    # STEP 4 — Per-day backtest
    # ─────────────────────────────────────────────────────────────────────
    print(f"[ 4/4 ]  Running per-day backtest ({len(trading_days)} days)...")
    print(dash)

    split_idx  = max(1, int(len(trading_days) * 0.70))
    train_days = set(str(d) for d in trading_days[:split_idx])

    all_new_rows: list = []
    total_qualified = 0
    total_run       = 0

    for day in trading_days:
        day_str   = str(day)
        split_tag = "TRAIN" if day_str in train_days else " TEST"

        watchlist = reconstruct_daily_watchlist(
            all_daily_bars, day,
            gap_min_pct=args.gap,
            price_min=args.price_min,
            price_max=args.price_max,
            rvol_min=args.rvol_min,
        )
        total_qualified += len(watchlist)

        new_tickers = [t for t in watchlist if (t, day_str) not in existing_pairs]
        if not new_tickers:
            print(f"  {day_str} [{split_tag}]  {len(watchlist):3d} qualified  "
                  f"all already in DB — skipped")
            continue

        # Run backtest concurrently
        day_results: list = []
        with ThreadPoolExecutor(max_workers=min(args.workers, len(new_tickers))) as ex:
            futures = {
                ex.submit(
                    backend._backtest_single,
                    api_key, secret_key, sym,
                    day, args.feed, args.price_min, args.price_max,
                ): sym
                for sym in new_tickers
            }
            for fut in as_completed(futures):
                r = fut.result()
                if r is not None:
                    r["sim_date"] = day_str
                    r["split"]    = "train" if day_str in train_days else "test"
                    day_results.append(r)

        total_run += len(new_tickers)
        all_new_rows.extend(day_results)

        day_wins = sum(1 for r in day_results if r.get("win_loss") == "Win")
        day_wr   = f"{round(day_wins / len(day_results) * 100, 1):.1f}%" if day_results else "  n/a"
        print(
            f"  {day_str} [{split_tag}]  "
            f"{len(watchlist):3d} qualified  "
            f"{len(new_tickers):3d} new  "
            f"{len(day_results):3d} results  "
            f"WR: {day_wr}"
        )

    print(dash + "\n")

    # ─────────────────────────────────────────────────────────────────────
    # Save to Supabase
    # ─────────────────────────────────────────────────────────────────────
    if all_new_rows:
        if args.dry_run:
            print(f"[DRY RUN] Would save {len(all_new_rows)} rows — Supabase write skipped.\n")
        else:
            print(f"Saving {len(all_new_rows)} new rows to Supabase...", flush=True)
            backend.save_backtest_sim_runs(all_new_rows, user_id=args.user_id)
            print("  → Saved.\n")
    else:
        print("No new rows to save (all dates already in Supabase).\n")

    # ─────────────────────────────────────────────────────────────────────
    # Final summary
    # ─────────────────────────────────────────────────────────────────────
    print(bar)
    print("  SUMMARY")
    print(bar)
    print(f"  Universe size       : {len(universe):,} tickers")
    print(f"  Tickers with data   : {len(all_daily_bars):,} tickers")
    print(f"  Trading days        : {len(trading_days)}")
    print(f"  Total qualified     : {total_qualified:,}  (gap/price/RVOL filter)")
    print(f"  New runs attempted  : {total_run:,}")
    print(f"  New results saved   : {len(all_new_rows):,}")

    if all_new_rows:
        wins_all = sum(1 for r in all_new_rows if r.get("win_loss") == "Win")
        wr_all   = round(wins_all / len(all_new_rows) * 100, 1)
        tr_rows  = [r for r in all_new_rows if r.get("split") == "train"]
        te_rows  = [r for r in all_new_rows if r.get("split") == "test"]

        print(f"\n  Win rate  (ALL)     : {wr_all:.1f}%  ({wins_all} / {len(all_new_rows)})")
        if tr_rows:
            tw = sum(1 for r in tr_rows if r.get("win_loss") == "Win")
            print(f"  Win rate  (TRAIN)   : {round(tw/len(tr_rows)*100,1):.1f}%  "
                  f"({tw} / {len(tr_rows)})")
        if te_rows:
            tw = sum(1 for r in te_rows if r.get("win_loss") == "Win")
            print(f"  Win rate  (TEST)    : {round(tw/len(te_rows)*100,1):.1f}%  "
                  f"({tw} / {len(te_rows)})")

        struct_counts: dict = {}
        for r in all_new_rows:
            s = r.get("actual_outcome", "?")
            struct_counts[s] = struct_counts.get(s, 0) + 1

        print(f"\n  Actual outcome breakdown:")
        for label, cnt in sorted(struct_counts.items(), key=lambda x: -x[1]):
            pct = round(cnt / len(all_new_rows) * 100, 1)
            print(f"    {label:<22}  {cnt:5,}  ({pct:.1f}%)")

    print(bar)
    if not args.dry_run and all_new_rows:
        print("\n  Open the Backtest tab in EdgeIQ to see the uploaded results.\n")
    print()


if __name__ == "__main__":
    main()
