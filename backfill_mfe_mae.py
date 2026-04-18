"""
backfill_mfe_mae.py
-------------------
Backfills mfe (max favorable excursion) and mae (max adverse excursion)
in R-units for all backtest_sim_runs rows that have entry_price_sim and
stop_price_sim populated.

Pulls 1-min Alpaca historical bars per (ticker, date) combo, then computes
using ONLY bars AFTER the IB close bar (strictly > entry_time, not >=):
  Long:  MFE = (max_high - entry) / stop_dist
         MAE = (entry - min_low)  / stop_dist
  Short: MFE = (entry - min_low)  / stop_dist
         MAE = (max_high - entry) / stop_dist

Values are stored as positive R multiples.
MFE of 2.0 means price moved 2R in your favor before reversing/EOD.
MAE of 0.5 means price moved 0.5R against you at worst.

Fully resumable — skips rows where mfe IS NOT NULL (unless --force-recompute).
Safe to run multiple times or kill/restart.

Usage:
  python backfill_mfe_mae.py                   # incremental (null rows only)
  python backfill_mfe_mae.py --force-recompute  # recompute ALL rows (overwrites existing)

IMPORTANT — 2026-04-18 bug fix:
  Original code used hm >= entry_hm which included the IB close bar itself.
  That bar's low == IB_LOW by definition → every trade appeared to have MAE = 1.0
  (stop-hit), inflating stop-out rate to ~64%.  Fixed to hm > entry_hm.
  Run with --force-recompute to overwrite the contaminated values.
"""

import os
import sys
import time
import logging
import requests
from datetime import datetime, date
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ALPACA_API_KEY    = os.environ["ALPACA_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_SECRET_KEY"]
ALPACA_BASE_URL   = "https://data.alpaca.markets/v2"

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

ALP_HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}

BATCH_SIZE      = 500    # rows fetched per Supabase page
UPSERT_BATCH    = 200    # rows per Supabase upsert call
ALPACA_PAUSE    = 0.35   # seconds between Alpaca API calls (stay under 200/min)
TICKERS_PER_REQ = 30     # tickers per multi-symbol Alpaca request


# ── helpers ──────────────────────────────────────────────────────────────────

def _infer_direction(row: dict) -> str:
    """Returns 'long' or 'short'. Falls back to 'long' if unclear."""
    entry  = row.get("entry_price_sim")
    target = row.get("target_price_sim")
    if entry and target:
        return "long" if float(target) >= float(entry) else "short"
    pred = (row.get("predicted") or "").lower()
    if any(w in pred for w in ("up", "bull", "trend up", "breakout up")):
        return "long"
    if any(w in pred for w in ("down", "bear", "trend down", "breakdown")):
        return "short"
    return "long"


def _entry_time_str(row: dict) -> str:
    """Returns HH:MM string for the entry bar (breakout or IB close)."""
    t = row.get("breakout_time_est") or row.get("ib_close_time_est") or "09:30"
    return t[:5]  # trim any seconds


def _fetch_bars(tickers: list, trade_date: str) -> dict:
    """
    Fetch 1-min bars for a list of tickers on a given date.
    Returns {ticker: [{t, o, h, l, c, v}, ...]}
    """
    start = f"{trade_date}T09:30:00-04:00"
    end   = f"{trade_date}T16:00:00-04:00"

    params = {
        "symbols":   ",".join(tickers),
        "timeframe": "1Min",
        "start":     start,
        "end":       end,
        "limit":     10000,
        "feed":      "iex",
        "adjustment":"raw",
    }

    result = defaultdict(list)
    url = f"{ALPACA_BASE_URL}/stocks/bars"

    while url:
        r = requests.get(url, headers=ALP_HEADERS, params=params, timeout=30)
        params = None   # only send params on first request; pagination uses next_page_token
        if r.status_code == 429:
            log.warning("Rate limited — sleeping 5s")
            time.sleep(5)
            r = requests.get(url, headers=ALP_HEADERS, params=params, timeout=30)
        if not r.ok:
            log.debug(f"Alpaca {r.status_code} for {tickers[:3]} on {trade_date}: {r.text[:120]}")
            break

        data = r.json()
        bars_map = data.get("bars") or {}
        for sym, bars in bars_map.items():
            result[sym].extend(bars)

        token = data.get("next_page_token")
        if token:
            url = f"{ALPACA_BASE_URL}/stocks/bars?next_page_token={token}&limit=10000"
        else:
            break

    return result


def _compute_mfe_mae(bars: list, entry_price: float, stop_price: float, direction: str, entry_time: str):
    """
    Compute MFE and MAE in R-units from bars at/after entry_time until EOD.
    Returns (mfe_r, mae_r) or (None, None) if insufficient data.
    """
    stop_dist = abs(entry_price - stop_price)
    if stop_dist < 0.0001:
        return None, None

    # Filter bars to those at or after entry_time
    try:
        entry_hm = int(entry_time[:2]) * 60 + int(entry_time[3:5])
    except Exception:
        entry_hm = 9 * 60 + 30

    relevant = []
    for bar in bars:
        ts = bar.get("t", "")
        # Alpaca timestamps: 2021-05-27T09:31:00-04:00
        try:
            hm_part = ts[11:16]
            hm = int(hm_part[:2]) * 60 + int(hm_part[3:5])
            if hm > entry_hm:
                relevant.append(bar)
        except Exception:
            continue

    if not relevant:
        return None, None

    highs = [float(b["h"]) for b in relevant]
    lows  = [float(b["l"]) for b in relevant]

    max_high = max(highs)
    min_low  = min(lows)

    if direction == "long":
        mfe_r = max(0.0, (max_high - entry_price) / stop_dist)
        mae_r = max(0.0, (entry_price - min_low)  / stop_dist)
    else:
        mfe_r = max(0.0, (entry_price - min_low)  / stop_dist)
        mae_r = max(0.0, (max_high - entry_price) / stop_dist)

    return round(mfe_r, 4), round(mae_r, 4)


def _fetch_pending_rows(offset: int, force: bool = False) -> list:
    """Fetch a page of rows that need MFE/MAE computed.

    force=True: fetch ALL rows with entry/stop populated (overwrites existing values).
    force=False (default): only fetch rows where mfe IS NULL.
    """
    from backend import supabase
    q = (
        supabase.table("backtest_sim_runs")
        .select("id,ticker,sim_date,entry_price_sim,stop_price_sim,target_price_sim,predicted,breakout_time_est,ib_close_time_est")
        .not_.is_("entry_price_sim", "null")
        .not_.is_("stop_price_sim", "null")
        .order("sim_date")
        .range(offset, offset + BATCH_SIZE - 1)
    )
    if not force:
        q = q.is_("mfe", "null")
    r = q.execute()
    return r.data or []


def _upsert_results(updates: list):
    """Upsert [{id, mfe, mae}, ...] back to Supabase in batches."""
    from backend import supabase
    for i in range(0, len(updates), UPSERT_BATCH):
        chunk = updates[i:i + UPSERT_BATCH]
        supabase.table("backtest_sim_runs").upsert(chunk, on_conflict="id").execute()


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    force = "--force-recompute" in sys.argv
    log.info(f"=== MFE/MAE Backfill starting === (mode: {'FORCE all rows' if force else 'incremental null-only'})")

    from backend import supabase

    # Total pending count
    count_q = (
        supabase.table("backtest_sim_runs")
        .select("id", count="exact")
        .not_.is_("entry_price_sim", "null")
        .not_.is_("stop_price_sim", "null")
    )
    if not force:
        count_q = count_q.is_("mfe", "null")
    count_r = count_q.execute()
    total_pending = count_r.count or 0
    log.info(f"Rows to process: {total_pending:,}")

    if total_pending == 0:
        log.info("Nothing to do. Exiting.")
        return

    processed   = 0
    skipped     = 0
    alpaca_calls = 0
    updates      = []

    offset = 0
    while True:
        try:
            rows = _fetch_pending_rows(offset, force=force)
        except Exception as e:
            log.error(f"Supabase fetch error at offset {offset}: {e}")
            time.sleep(5)
            continue

        if not rows:
            break

        # Group this page by date to minimize API calls
        by_date = defaultdict(list)
        for row in rows:
            by_date[row["sim_date"]].append(row)

        for trade_date, date_rows in sorted(by_date.items()):
            tickers_needed = list({r["ticker"] for r in date_rows})

            bars_cache = {}
            for i in range(0, len(tickers_needed), TICKERS_PER_REQ):
                chunk_tickers = tickers_needed[i:i + TICKERS_PER_REQ]
                time.sleep(ALPACA_PAUSE)
                alpaca_calls += 1
                try:
                    fetched = _fetch_bars(chunk_tickers, trade_date)
                    bars_cache.update(fetched)
                except Exception as e:
                    log.warning(f"Alpaca error {trade_date} {chunk_tickers[:3]}: {e}")

                if alpaca_calls % 50 == 0:
                    log.info(f"  Alpaca calls: {alpaca_calls} | processed: {processed:,} / {total_pending:,} | skipped: {skipped}")

            for row in date_rows:
                try:
                    ticker     = row["ticker"]
                    entry      = float(row["entry_price_sim"])
                    stop       = float(row["stop_price_sim"])
                    direction  = _infer_direction(row)
                    entry_time = _entry_time_str(row)
                    bars       = bars_cache.get(ticker, [])

                    mfe_r, mae_r = _compute_mfe_mae(bars, entry, stop, direction, entry_time)

                    if mfe_r is None:
                        skipped += 1
                        updates.append({"id": row["id"], "mfe": -9999.0, "mae": -9999.0})
                    else:
                        updates.append({"id": row["id"], "mfe": mfe_r, "mae": mae_r})
                except Exception as e:
                    log.warning(f"Row error {row.get('id')}: {e}")
                    skipped += 1

                processed += 1

        # Upsert batch
        if updates:
            try:
                _upsert_results(updates)
                log.info(f"✓ Upserted {len(updates)} | processed: {processed:,} / {total_pending:,} | skipped: {skipped} | alpaca_calls: {alpaca_calls}")
            except Exception as e:
                log.error(f"Upsert failed: {e}")
            updates = []

        offset += BATCH_SIZE

    log.info(f"=== Done. Processed {processed:,} | Skipped {skipped:,} | Alpaca calls {alpaca_calls} ===")

    # Quick summary stats
    log.info("Computing summary statistics...")
    r = supabase.table("backtest_sim_runs").select("mfe,mae,scan_type").not_.is_("mfe", "null").gt("mfe", 0).limit(5000).execute()
    if r.data:
        mfes = sorted([float(x["mfe"]) for x in r.data if x.get("mfe") and float(x["mfe"]) > 0])
        n = len(mfes)
        if n:
            log.info(f"MFE distribution (n={n}): p25={mfes[int(n*0.25)]:.2f}R  p50={mfes[int(n*0.50)]:.2f}R  p75={mfes[int(n*0.75)]:.2f}R  p90={mfes[int(n*0.90)]:.2f}R")


if __name__ == "__main__":
    main()
