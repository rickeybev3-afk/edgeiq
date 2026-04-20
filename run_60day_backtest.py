"""
run_60day_backtest.py
Standalone background script — runs the full 60-day IB backtest across the
user's Finviz watchlist without needing a browser session open.

Usage:
    python run_60day_backtest.py
    python run_60day_backtest.py --start 2026-02-01 --end 2026-04-18
"""

import sys, os, json, datetime, argparse
sys.path.insert(0, os.path.dirname(__file__))

import backend
from backend import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY,
    load_watchlist, save_backtest_sim_runs, run_backtest_range,
)
from datetime import date, timedelta

USER_ID = "a5e1fcab-8369-42c4-8550-a8a19734510c"

def get_last_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (default: 65 trading days ago)")
    parser.add_argument("--end",   default=None, help="End date YYYY-MM-DD (default: yesterday)")
    args = parser.parse_args()

    end_date   = date.fromisoformat(args.end)   if args.end   else get_last_weekday(date.today() - timedelta(days=1))
    start_date = date.fromisoformat(args.start) if args.start else get_last_weekday(end_date - timedelta(weeks=13))

    print("=" * 60)
    print("EdgeIQ — 60-Day Background Backtest")
    print("=" * 60)
    print(f"Date range : {start_date} → {end_date}")

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        print("ERROR: ALPACA_API_KEY / ALPACA_SECRET_KEY not set.")
        sys.exit(1)

    tickers = load_watchlist(user_id=USER_ID)
    if not tickers:
        print("ERROR: Watchlist is empty. Make sure the Finviz scan has run today.")
        sys.exit(1)
    print(f"Tickers    : {len(tickers)} from watchlist")
    print(f"  {', '.join(tickers[:10])} … and {max(0, len(tickers)-10)} more")
    print()

    print("Running simulation — this will take several minutes…")
    try:
        results, summary, daily_list = run_backtest_range(
            ALPACA_API_KEY, ALPACA_SECRET_KEY,
            start_date=start_date,
            end_date=end_date,
            tickers=tickers,
            feed="sip",
            price_min=1.0,
            price_max=50.0,
            slippage_pct=0.5,
        )
    except Exception as e:
        print(f"ERROR during simulation: {e}")
        sys.exit(1)

    if not results:
        print("No results returned — check date range or Alpaca data availability.")
        sys.exit(1)

    dates_covered = sorted(set(r.get("sim_date", "") for r in results))
    print(f"\nSimulation complete:")
    print(f"  Total rows : {len(results)}")
    print(f"  Dates      : {len(dates_covered)} ({dates_covered[0]} → {dates_covered[-1]})")
    wins = sum(1 for r in results if r.get("win_loss") == "Win")
    print(f"  Win rate   : {wins}/{len(results)} = {wins/len(results)*100:.1f}%")

    print("\nSaving to Supabase…")
    try:
        save_backtest_sim_runs(results, user_id=USER_ID)
        print(f"Saved {len(results)} rows successfully.")
    except Exception as e:
        print(f"ERROR saving to Supabase: {e}")
        sys.exit(1)

    print("\nDone. Go to the Backtest tab → Load Saved Simulation Results")
    print("→ Fetch My Saved Dates → select all → Load Selected → Run Replay.")

if __name__ == "__main__":
    main()
