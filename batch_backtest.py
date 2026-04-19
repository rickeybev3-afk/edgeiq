#!/usr/bin/env python3
"""
batch_backtest.py — Historical watchlist reconstruction + structure backtest

Reconstructs what would have been on the daily Finviz watchlist for each
historical trading day (using Alpaca daily OHLCV data to apply gap%/RVOL/price
filters against a static small-cap universe pulled from Finviz today), runs the
full IB/Volume Profile backtest engine on each qualifying ticker, and saves
results to Supabase.

Each ticker is snapshotted at up to 3 times per day (matching the live bot):
  morning  → 10:47 AM  (IB just formed — entry decision point)
  intraday → 14:00 PM  (position management check)
  eod      → 16:00 PM  (full-day TCS — matches what paper_trades records)

Bars are fetched ONCE per ticker per day and reused across all 3 snapshots.

Usage:
  python batch_backtest.py                                 # last 60 days, gap screener, all snapshots
  python batch_backtest.py --screener trend                # trend-continuation screener only
  python batch_backtest.py --screener both                 # gap + trend (dual screener, matches live bot)
  python batch_backtest.py --days 252                     # last year
  python batch_backtest.py --scan-type morning            # morning snapshots only
  python batch_backtest.py --scan-type intraday           # intraday only
  python batch_backtest.py --scan-type eod                # EOD only
  python batch_backtest.py --start 2026-02-01             # from specific date
  python batch_backtest.py --start 2026-02-01 --end 2026-03-15
  python batch_backtest.py --feed sip                     # use SIP feed (paid)
  python batch_backtest.py --dry-run                      # skip Supabase save
  python batch_backtest.py --gap 5.0                      # 5% gap minimum
  python batch_backtest.py --user-id <supabase_id>        # scope to specific user
  python batch_backtest.py --skip-post-backfill           # skip automatic tiered P&L backfill after save

SQL required in Supabase (run once):
  ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS scan_type TEXT DEFAULT 'morning';
  UPDATE backtest_sim_runs SET scan_type = 'morning' WHERE scan_type IS NULL;
"""

import os
import sys
import time
import argparse
import logging
import pandas as pd
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend
from run_tiered_pnl_backfill import backfill_backtest_sim_runs


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot configuration — mirrors live bot schedule (ET)
# ─────────────────────────────────────────────────────────────────────────────

SCAN_CONFIGS = {
    "morning":        {"cutoff_h": 10, "cutoff_m": 47, "label": "MORN"},
    "intraday":       {"cutoff_h": 14, "cutoff_m":  0, "label": "INTR"},
    "eod":            {"cutoff_h": 16, "cutoff_m":  0, "label": " EOD"},
    "trend_morning":  {"cutoff_h": 10, "cutoff_m": 47, "label": "TMRN"},
    "trend_intraday": {"cutoff_h": 14, "cutoff_m":  0, "label": "TITR"},
    "trend_eod":      {"cutoff_h": 16, "cutoff_m":  0, "label": "TEOD"},
}

# Base scan_type → trend variant mapping (same cutoff, different screener tag)
_TREND_SCAN_MAP = {
    "morning":  "trend_morning",
    "intraday": "trend_intraday",
    "eod":      "trend_eod",
}


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
    today = date.today()
    days, cur = [], start
    while cur <= end:
        if _is_trading_day(cur) and cur < today:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def walk_back_trading_days(from_date: date, n: int) -> date:
    count, cur = 0, from_date
    while count < n:
        if _is_trading_day(cur):
            count += 1
        cur -= timedelta(days=1)
    return cur + timedelta(days=1)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Finviz small-cap universe
# ─────────────────────────────────────────────────────────────────────────────

def fetch_smallcap_universe(
    float_max_m:   float = 100.0,
    price_min:     float = 1.0,
    price_max:     float = 50.0,
    avg_vol_min_k: int   = 1000,
    max_tickers:   int   = 3000,
) -> list:
    """Scrape Finviz for US stocks without gap%/RVOL day-filters.

    avg_vol_min_k: minimum average daily volume in thousands (default 1000 = 1M shares).
    """
    import re
    import requests
    from bs4 import BeautifulSoup

    float_filter  = f"sh_float_u{int(float_max_m)}"
    price_lo      = f"sh_price_o{int(price_min)}"
    price_hi      = f"sh_price_u{int(price_max)}"
    avgvol_filter = f"sh_avgvol_o{int(avg_vol_min_k)}"

    filters = ",".join([
        "geo_usa",
        float_filter,
        avgvol_filter,
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
# Step 2 — Alpaca daily bars (batched multi-ticker)
# ─────────────────────────────────────────────────────────────────────────────

def _to_date(ts) -> date:
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
    """Fetch daily OHLCV for a batch of tickers. Returns {ticker: DataFrame}."""
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
# Step 3 — Reconstruct daily watchlist
# ─────────────────────────────────────────────────────────────────────────────

def reconstruct_daily_watchlist(
    daily_bars:    dict,
    scan_date:     date,
    gap_min_pct:   float = 3.0,
    price_min:     float = 1.0,
    price_max:     float = 50.0,
    rvol_min:      float = 1.0,
    avg_vol_days:  int   = 30,
) -> list:
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

        qualifying.append({
            "ticker":     sym,
            "gap_pct":    round(gap_pct, 3),
            "prev_close": round(prev_close, 4),
            "rvol":       round(rvol, 2),
        })

    return qualifying


# ─────────────────────────────────────────────────────────────────────────────
# Step 3b — Reconstruct trend-continuation watchlist (mirrors live bot pass 2)
# ─────────────────────────────────────────────────────────────────────────────

def reconstruct_trend_watchlist(
    daily_bars:     dict,
    scan_date:      date,
    change_min_pct: float = 1.0,
    price_min:      float = 5.0,
    price_max:      float = 50.0,
    avg_vol_min_k:  float = 2000.0,
    avg_vol_days:   int   = 30,
    sma_short:      int   = 20,
    sma_long:       int   = 50,
) -> list:
    """Reconstruct what trend-continuation tickers would have qualified on scan_date.

    Mirrors the live bot's second Finviz pass:
      - ≥1% change from prev close to today open
      - Price $5–$50
      - 30-day avg vol ≥ 2M shares
      - Prev close above 20-day SMA AND 50-day SMA (established uptrend)
    """
    qualifying = []
    for sym, df in daily_bars.items():
        if scan_date not in df.index:
            continue
        dates_before = [d for d in df.index if d < scan_date]
        if len(dates_before) < sma_long:
            continue

        prev_date  = max(dates_before)
        prev_close = float(df.loc[prev_date, "close"])
        today_open = float(df.loc[scan_date, "open"])
        today_vol  = float(df.loc[scan_date, "volume"])

        if prev_close <= 0 or today_open <= 0:
            continue

        change_pct = (today_open - prev_close) / prev_close * 100.0
        if change_pct < change_min_pct:
            continue
        if not (price_min <= today_open <= price_max):
            continue

        lookback_idx = [d for d in df.index if d < scan_date][-avg_vol_days:]
        avg_vol = float(df.loc[lookback_idx, "volume"].mean()) if lookback_idx else 0.0
        if avg_vol < avg_vol_min_k * 1_000:
            continue

        sma_short_dates = dates_before[-sma_short:]
        sma_long_dates  = dates_before[-sma_long:]
        if len(sma_short_dates) < sma_short or len(sma_long_dates) < sma_long:
            continue
        sma20 = float(df.loc[sma_short_dates, "close"].mean())
        sma50 = float(df.loc[sma_long_dates,  "close"].mean())
        if prev_close < sma20 or prev_close < sma50:
            continue

        rvol = today_vol / avg_vol if avg_vol > 0 else 0.0
        qualifying.append({
            "ticker":     sym,
            "gap_pct":    round(change_pct, 3),
            "prev_close": round(prev_close, 4),
            "rvol":       round(rvol, 2),
        })

    return qualifying


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Deduplication (ticker, date, scan_type)
# ─────────────────────────────────────────────────────────────────────────────

def load_existing_triples(user_id: str = "") -> set:
    """Return set of (ticker, sim_date_str, scan_type) already in Supabase.

    Falls back to (ticker, date) matching with scan_type='morning' for legacy
    records that pre-date the scan_type column.
    """
    if not backend.supabase:
        return set()
    triples: set = set()
    chunk_size = 1000
    offset = 0
    while True:
        try:
            q = (
                backend.supabase
                .table("backtest_sim_runs")
                .select("ticker, sim_date, scan_type")
                .range(offset, offset + chunk_size - 1)
            )
            if user_id:
                q = q.eq("user_id", user_id)
            rows = q.execute().data
            if not rows:
                break
            for r in rows:
                st = str(r.get("scan_type") or "morning")
                triples.add((
                    str(r.get("ticker", "")),
                    str(r.get("sim_date", "")),
                    st,
                ))
            if len(rows) < chunk_size:
                break
            offset += chunk_size
        except Exception as e:
            print(f"  [WARN] Dedup query error at offset {offset}: {e}")
            break
    return triples


# ─────────────────────────────────────────────────────────────────────────────
# Core — analyze pre-fetched bars at a specific cutoff time
# ─────────────────────────────────────────────────────────────────────────────

def _analyze_at_cutoff(
    full_df,
    sym:         str,
    trade_date,
    scan_type:   str,
    cutoff_h:    int,
    cutoff_m:    int,
    price_min:   float,
    price_max:   float,
    slippage_pct: float = 0.0,
):
    """Run IB/volume structure analysis on pre-fetched intraday bars at the given cutoff.

    Returns a result dict (same shape as _backtest_single) or None.
    Uses bars fetched by backend.fetch_bars() — no additional API calls.
    """
    try:
        if full_df is None or full_df.empty or len(full_df) < 10:
            return None

        open_px = float(full_df["open"].iloc[0])
        if not (price_min <= open_px <= price_max):
            return None

        ib_cutoff = full_df.index[0].replace(
            hour=cutoff_h, minute=cutoff_m, second=0, microsecond=0
        )
        pm_df  = full_df[full_df.index <= ib_cutoff]
        aft_df = full_df[full_df.index > ib_cutoff]

        if len(pm_df) < 5:
            return None

        morning_only = len(aft_df) < 5

        ib_high, ib_low = backend.compute_initial_balance(pm_df)
        if not ib_high or not ib_low:
            return None

        bin_centers, vap, poc_price = backend.compute_volume_profile(pm_df, num_bins=30)
        tcs   = float(backend.compute_tcs(pm_df, ib_high, ib_low, poc_price))

        # ── Pattern discovery fields (zero extra API calls) ──────────────────
        ib_range_pct = round((ib_high - ib_low) / open_px * 100.0, 4) if open_px else None

        _pm_vol = pm_df["volume"].sum() if "volume" in pm_df.columns else 0
        volume_ib = int(_pm_vol)

        _vwap_num = (pm_df["close"] * pm_df["volume"]).sum() if "volume" in pm_df.columns else 0
        _vwap_den = _pm_vol
        vwap_at_ib = round(float(_vwap_num / _vwap_den), 4) if _vwap_den > 0 else None

        open_vs_poc_pct = (
            round((open_px - poc_price) / open_px * 100.0, 4)
            if poc_price and open_px else None
        )
        ib_mid = (ib_high + ib_low) / 2.0
        ib_midpoint_vs_poc_pct = (
            round((ib_mid - poc_price) / open_px * 100.0, 4)
            if poc_price and open_px else None
        )
        day_of_week = trade_date.weekday()   # 0=Mon … 4=Fri
        # ─────────────────────────────────────────────────────────────────────

        probs = backend.compute_structure_probabilities(
            pm_df, bin_centers, vap, ib_high, ib_low, poc_price
        )
        predicted  = max(probs, key=probs.get) if probs else "—"
        confidence = round(probs.get(predicted, 0.0), 1)

        if morning_only:
            aft_high       = ib_high
            aft_low        = ib_low
            close_px       = float(pm_df["close"].iloc[-1])
            actual_outcome = "Pending"
            actual_icon    = "…"
            broke_up       = False
            broke_down     = False
        else:
            aft_high   = float(aft_df["high"].max())
            aft_low    = float(aft_df["low"].min())
            close_px   = float(aft_df["close"].iloc[-1])
            broke_up   = aft_high > ib_high
            broke_down = aft_low  < ib_low

        if not morning_only:
            if broke_up and broke_down:
                actual_outcome = "Both Sides"
                actual_icon    = "↕"
            elif broke_up:
                actual_outcome = "Bullish Break"
                actual_icon    = "↑"
            elif broke_down:
                actual_outcome = "Bearish Break"
                actual_icon    = "↓"
            else:
                actual_outcome = "Range-Bound"
                actual_icon    = "—"

        if morning_only:
            win      = None
            aft_move = 0.0
        else:
            is_dir      = any(k in predicted for k in backend._BACKTEST_DIRECTIONAL)
            is_range    = any(k in predicted for k in backend._BACKTEST_RANGE)
            is_neut_ext = any(k in predicted for k in backend._BACKTEST_NEUTRAL_EXT)
            is_balanced = (not is_neut_ext and
                           any(k in predicted for k in backend._BACKTEST_BALANCED))
            is_bimodal  = any(k in predicted for k in backend._BACKTEST_BIMODAL)
            is_normal   = (not is_dir and not is_range and not is_neut_ext
                           and not is_balanced and not is_bimodal
                           and "Normal" in predicted)

            if is_dir:
                win = actual_outcome in ("Bullish Break", "Bearish Break")
            elif is_neut_ext:
                win = actual_outcome in ("Bullish Break", "Bearish Break", "Both Sides")
            elif is_range or is_normal:
                win = actual_outcome == "Range-Bound"
            elif is_balanced:
                win = actual_outcome in ("Both Sides", "Bullish Break", "Bearish Break")
            elif is_bimodal:
                win = actual_outcome in ("Bullish Break", "Bearish Break", "Both Sides")
            else:
                win = False

            if broke_up and broke_down:
                _ft_up   = (aft_high - ib_high) / ib_high * 100
                _ft_down = (ib_low   - aft_low)  / ib_low  * 100
                aft_move = _ft_up if _ft_up >= _ft_down else -_ft_down
            elif broke_up:
                aft_move = (aft_high - ib_high) / ib_high * 100
            elif broke_down:
                aft_move = -((ib_low - aft_low) / ib_low * 100)
            else:
                aft_move = 0.0

            _slip_drag = slippage_pct * 2.0
            if aft_move > 0:
                aft_move = max(0.0, aft_move - _slip_drag)
            elif aft_move < 0:
                aft_move = min(0.0, aft_move + _slip_drag)

        # ── After-IB pattern fields ───────────────────────────────────────────
        if not morning_only and "volume" in aft_df.columns:
            aft_volume = int(aft_df["volume"].sum())
            _aft_vwap_num = (aft_df["close"] * aft_df["volume"]).sum()
            _aft_vwap_den = aft_df["volume"].sum()
            _full_vwap = (
                round(float(_aft_vwap_num / _aft_vwap_den), 4)
                if _aft_vwap_den > 0 else vwap_at_ib
            )
            close_vs_vwap_pct = (
                round((close_px - _full_vwap) / _full_vwap * 100.0, 4)
                if _full_vwap else None
            )
        else:
            aft_volume        = 0
            close_vs_vwap_pct = None
        # ─────────────────────────────────────────────────────────────────────

        _aft_r = aft_df.reset_index()
        false_break_up   = False
        false_break_down = False
        if broke_up:
            _up_bars = _aft_r[_aft_r["high"] > ib_high]
            if not _up_bars.empty:
                _fi = _up_bars.index[0]
                _w  = _aft_r.loc[_fi : _fi + 6]
                false_break_up = bool((_w["close"] < ib_high).any())
        if broke_down:
            _dn_bars = _aft_r[_aft_r["low"] < ib_low]
            if not _dn_bars.empty:
                _fi = _dn_bars.index[0]
                _w  = _aft_r.loc[_fi : _fi + 6]
                false_break_down = bool((_w["close"] > ib_low).any())

        # ── Time of day fields (all EST from Alpaca index) ────────────────────
        ib_open_time_est  = pm_df.index[0].strftime("%H:%M")   # 09:30 typically
        ib_close_time_est = pm_df.index[-1].strftime("%H:%M")  # last IB bar

        breakout_time_est = None
        if not morning_only and not aft_df.empty:
            _aft_ts = aft_df.reset_index()
            _ts_col = _aft_ts.columns[0]   # 'timestamp' or 'index'
            if broke_up:
                _bo = _aft_ts[_aft_ts["high"] > ib_high]
                if not _bo.empty:
                    breakout_time_est = _bo[_ts_col].iloc[0].strftime("%H:%M")
            elif broke_down:
                _bo = _aft_ts[_aft_ts["low"] < ib_low]
                if not _bo.empty:
                    breakout_time_est = _bo[_ts_col].iloc[0].strftime("%H:%M")

        exit_time_est = (
            aft_df.index[-1].strftime("%H:%M")
            if not morning_only and not aft_df.empty else None
        )
        # ─────────────────────────────────────────────────────────────────────

        # ── Tiered exit simulation (bar-by-bar, requires afternoon bars) ─────
        _tiered = backend.compute_trade_sim_tiered(
            aft_df    = aft_df if not morning_only else None,
            ib_high   = ib_high,
            ib_low    = ib_low,
            direction = actual_outcome,
            close_px  = close_px,
        )
        _eod_pnl_r    = _tiered.get("eod_pnl_r")
        _tiered_pnl_r = _tiered.get("tiered_pnl_r")
        # ─────────────────────────────────────────────────────────────────────

        return {
            "ticker":         sym,
            "open_price":     round(open_px, 4),
            "close_price":    round(close_px, 4),
            "ib_low":         round(ib_low, 4),
            "ib_high":        round(ib_high, 4),
            "tcs":            round(tcs, 1),
            "poc_price":      round(poc_price, 4) if poc_price else None,
            "predicted":      predicted,
            "confidence":     confidence,
            "actual_outcome": actual_outcome,
            "actual_icon":    actual_icon,
            "win_loss":       ("Win" if win else "Loss") if win is not None else None,
            "aft_move_pct":   round(aft_move, 3),
            "false_break_up":   false_break_up,
            "false_break_down": false_break_down,
            "scan_type":      scan_type,
            # ── Pattern discovery fields ──────────────────────────────────
            "ib_range_pct":           ib_range_pct,
            "volume_ib":              volume_ib,
            "vwap_at_ib":             vwap_at_ib,
            "open_vs_poc_pct":        open_vs_poc_pct,
            "ib_midpoint_vs_poc_pct": ib_midpoint_vs_poc_pct,
            "day_of_week":            day_of_week,
            "aft_volume":             aft_volume,
            "close_vs_vwap_pct":      close_vs_vwap_pct,
            # ── Time of day fields (EST) ──────────────────────────────────
            "ib_open_time_est":       ib_open_time_est,
            "ib_close_time_est":      ib_close_time_est,
            "breakout_time_est":      breakout_time_est,
            "exit_time_est":          exit_time_est,
            # ── Tiered exit P&L scenarios (requires afternoon bars) ───────
            "eod_pnl_r":              _eod_pnl_r,
            "tiered_pnl_r":          _tiered_pnl_r,
        }
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Worker — fetch bars once, run all requested snapshots
# ─────────────────────────────────────────────────────────────────────────────

def _worker_multi_snapshot(
    api_key:     str,
    secret_key:  str,
    sym:         str,
    trade_date,
    feed:        str,
    price_min:   float,
    price_max:   float,
    scan_types:  list,
    gap_pct:     float = 0.0,
    rvol:        float = 0.0,
) -> list:
    """Fetch intraday bars once, run analysis at each requested cutoff.
    Returns a list of result dicts (one per scan_type that yields data).
    """
    try:
        full_df = backend.fetch_bars(api_key, secret_key, sym, trade_date, feed=feed)
    except Exception:
        return []

    if full_df is None or full_df.empty or len(full_df) < 10:
        return []

    results = []
    for st in scan_types:
        cfg = SCAN_CONFIGS[st]
        r = _analyze_at_cutoff(
            full_df, sym, trade_date,
            scan_type=st,
            cutoff_h=cfg["cutoff_h"],
            cutoff_m=cfg["cutoff_m"],
            price_min=price_min,
            price_max=price_max,
        )
        if r is not None:
            r["gap_pct"] = round(gap_pct, 3)
            r["rvol"]    = round(rvol, 2)
            # gap vs IB range — how many IB-widths wide was the gap?
            _ib_h = r.get("ib_high") or 0
            _ib_l = r.get("ib_low") or 0
            _op   = r.get("open_price") or 0
            if _ib_h > _ib_l and _op > 0:
                _ib_range_pct = (_ib_h - _ib_l) / _op * 100.0
                r["gap_vs_ib_pct"] = round(gap_pct / _ib_range_pct, 3) if _ib_range_pct > 0 else None
            else:
                r["gap_vs_ib_pct"] = None
            results.append(r)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Save — includes scan_type field
# ─────────────────────────────────────────────────────────────────────────────

def save_rows_with_scan_type(rows: list, user_id: str = ""):
    """Insert backtest rows to Supabase, including scan_type and gap columns."""
    if not backend.supabase or not rows:
        return

    def _build_record(r, include_gap: bool, include_sim: bool) -> dict:
        _scan = r.get("scan_type", "morning")
        # entry_hour: approximate hour of day the setup triggered.
        # morning IB closes at 10:30 → 10.  intraday scan runs at 1:30 PM → 13.
        _entry_hour_map = {
            "morning": 10, "intraday": 13, "eod": 16,
            "trend_morning": 10, "trend_intraday": 13, "trend_eod": 16,
        }
        rec = {
            "user_id":          user_id or "",
            "sim_date":         str(r.get("sim_date", "")),
            "ticker":           r.get("ticker", ""),
            "open_price":       r.get("open_price"),
            "close_price":      r.get("close_price"),   # EOD close — needed for realistic sim P&L
            "ib_low":           r.get("ib_low"),
            "ib_high":          r.get("ib_high"),
            "tcs":              r.get("tcs"),
            "predicted":        r.get("predicted", ""),
            "actual_outcome":   r.get("actual_outcome", ""),
            "win_loss":         r.get("win_loss", ""),
            "follow_thru_pct":  r.get("aft_move_pct"),
            "false_break_up":   bool(r.get("false_break_up", False)),
            "false_break_down": bool(r.get("false_break_down", False)),
            "scan_type":        _scan,
            "entry_hour":       _entry_hour_map.get(_scan, 10),
        }
        # Optional columns — skip gracefully if DB column missing
        _optional = [
            "rvol", "poc_price",
            "ib_range_pct", "volume_ib", "vwap_at_ib",
            "open_vs_poc_pct", "ib_midpoint_vs_poc_pct",
            "day_of_week", "aft_volume", "close_vs_vwap_pct",
            "ib_open_time_est", "ib_close_time_est",
            "breakout_time_est", "exit_time_est",
        ]
        for _f in _optional:
            if r.get(_f) is not None:
                rec[_f] = r.get(_f)
        if include_gap:
            rec["gap_pct"]       = r.get("gap_pct")
            rec["gap_vs_ib_pct"] = r.get("gap_vs_ib_pct")
        if include_sim:
            sim = backend.apply_rvol_sizing_to_sim(backend.compute_trade_sim(r), r.get("rvol"))
            if sim.get("rvol_mult") is not None:
                rec["rvol_mult"] = sim["rvol_mult"]
            if sim.get("sim_outcome") not in ("no_trade", "missing_data", "invalid_ib", None):
                rec["sim_outcome"]      = sim["sim_outcome"]
                rec["pnl_r_sim"]        = sim.get("pnl_r_sim")
                rec["pnl_pct_sim"]      = sim.get("pnl_pct_sim")
                rec["entry_price_sim"]  = sim.get("entry_price_sim")
                rec["stop_price_sim"]   = sim.get("stop_price_sim")
                rec["stop_dist_pct"]    = sim.get("stop_dist_pct")
                rec["target_price_sim"] = sim.get("target_price_sim")
            # Tiered exit P&L — pre-computed in _analyze_at_cutoff, just pass through
            if r.get("eod_pnl_r") is not None:
                rec["eod_pnl_r"] = r["eod_pnl_r"]
            if r.get("tiered_pnl_r") is not None:
                rec["tiered_pnl_r"] = r["tiered_pnl_r"]
        return rec

    chunk = 500
    include_gap = True   # fall back to False if columns missing
    include_sim = True   # fall back to False if sim columns missing

    for i in range(0, len(rows), chunk):
        batch = rows[i : i + chunk]
        records = [_build_record(r, include_gap, include_sim) for r in batch]
        try:
            backend.supabase.table("backtest_sim_runs").insert(records).execute()
        except Exception as e:
            err_str = str(e).lower()
            sim_cols  = ["sim_outcome", "pnl_r_sim", "pnl_pct_sim", "entry_price_sim",
                         "stop_price_sim", "stop_dist_pct", "target_price_sim",
                         "eod_pnl_r", "tiered_pnl_r", "rvol_mult"]
            gap_cols  = ["gap_pct", "gap_vs_ib_pct"]

            if include_sim and any(c in err_str for c in sim_cols):
                print(
                    "\n⚠️  Sim columns missing in backtest_sim_runs — run SQL below, then re-run backtest:\n"
                    "   ALTER TABLE backtest_sim_runs\n"
                    "     ADD COLUMN IF NOT EXISTS sim_outcome TEXT,\n"
                    "     ADD COLUMN IF NOT EXISTS pnl_r_sim FLOAT,\n"
                    "     ADD COLUMN IF NOT EXISTS pnl_pct_sim FLOAT,\n"
                    "     ADD COLUMN IF NOT EXISTS entry_price_sim FLOAT,\n"
                    "     ADD COLUMN IF NOT EXISTS stop_price_sim FLOAT,\n"
                    "     ADD COLUMN IF NOT EXISTS stop_dist_pct FLOAT,\n"
                    "     ADD COLUMN IF NOT EXISTS target_price_sim FLOAT,\n"
                    "     ADD COLUMN IF NOT EXISTS eod_pnl_r FLOAT,\n"
                    "     ADD COLUMN IF NOT EXISTS tiered_pnl_r FLOAT,\n"
                    "     ADD COLUMN IF NOT EXISTS rvol_mult FLOAT;\n"
                )
                include_sim = False
                records = [_build_record(r, include_gap, False) for r in batch]
                try:
                    backend.supabase.table("backtest_sim_runs").insert(records).execute()
                except Exception as e2:
                    print(f"Backtest save error (no-sim fallback): {e2}")
            elif include_gap and any(c in err_str for c in gap_cols):
                print(
                    "\n⚠️  gap_pct / gap_vs_ib_pct columns missing in Supabase.\n"
                    "   ALTER TABLE backtest_sim_runs\n"
                    "     ADD COLUMN IF NOT EXISTS gap_pct FLOAT,\n"
                    "     ADD COLUMN IF NOT EXISTS gap_vs_ib_pct FLOAT;\n"
                )
                include_gap = False
                records = [_build_record(r, False, include_sim) for r in batch]
                try:
                    backend.supabase.table("backtest_sim_runs").insert(records).execute()
                except Exception as e2:
                    print(f"Backtest save error (no-gap fallback): {e2}")
            else:
                print(f"Backtest save error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Batch backtest — 3-snapshot historical reconstruction",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--start",      type=str,   help="Start date YYYY-MM-DD")
    parser.add_argument("--end",        type=str,   help="End date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--days",       type=int,   default=60,   help="Lookback trading days if --start omitted (default: 60)")
    parser.add_argument("--scan-type",  type=str,   default="all",
                        choices=["morning", "intraday", "eod", "all",
                                 "trend_morning", "trend_intraday", "trend_eod"],
                        help="Which snapshot(s) to run (default: all)")
    parser.add_argument("--screener",   type=str,   default="gap",
                        choices=["gap", "trend", "both"],
                        help="Watchlist screener strategy: gap (default), trend, or both")
    parser.add_argument("--feed",       type=str,   default="iex", choices=["iex", "sip"],
                        help="Alpaca data feed for intraday bars (default: iex)")
    parser.add_argument("--gap",        type=float, default=3.0,  help="Min gap%% (default: 3.0)")
    parser.add_argument("--price-min",  type=float, default=1.0,  help="Min open price (default: 1.0)")
    parser.add_argument("--price-max",  type=float, default=50.0, help="Max open price (default: 50.0)")
    parser.add_argument("--rvol-min",   type=float, default=1.0,  help="Min relative volume (default: 1.0)")
    parser.add_argument("--float-max",  type=float, default=100.0, help="Max float in millions (default: 100)")
    parser.add_argument("--workers",    type=int,   default=8,    help="Parallel workers per day (default: 8)")
    parser.add_argument("--batch",      type=int,   default=50,   help="Ticker batch size for daily bars (default: 50)")
    parser.add_argument("--dry-run",    action="store_true",      help="Skip Supabase save (test mode)")
    parser.add_argument("--no-ratelimit", action="store_true",   help="Disable Alpaca API rate limiting (paid plans)")
    parser.add_argument("--user-id",    type=str,   default="",   help="Supabase user_id for data scoping")
    parser.add_argument(
        "--skip-post-backfill",
        action="store_true",
        help="Skip the automatic tiered P&L backfill that runs after rows are saved (use nightly refresh instead)",
    )
    args = parser.parse_args()

    api_key    = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        print("ERROR: ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.")
        sys.exit(1)

    # Determine which scan_types to run based on --screener and --scan-type
    _base_types  = ["morning", "intraday", "eod"]
    _trend_types = ["trend_morning", "trend_intraday", "trend_eod"]
    if args.scan_type == "all":
        gap_scan_types   = _base_types   if args.screener in ("gap",   "both") else []
        trend_scan_types = _trend_types  if args.screener in ("trend", "both") else []
    else:
        gap_scan_types   = [args.scan_type] if args.scan_type in _base_types  else []
        trend_scan_types = [args.scan_type] if args.scan_type in _trend_types else []
    scan_types = gap_scan_types + trend_scan_types  # for dedup/display

    today    = date.today()
    end_date = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
    if args.start:
        start_date = date.fromisoformat(args.start)
    else:
        start_date = walk_back_trading_days(end_date, args.days)

    trading_days = get_trading_days(start_date, end_date)
    if not trading_days:
        print("No trading days in range. Check your dates.")
        sys.exit(0)

    bar  = "=" * 66
    dash = "-" * 66
    print(f"\n{bar}")
    print(f"  EdgeIQ  |  Batch Historical Backtest  (3-Snapshot)")
    print(f"{bar}")
    print(f"  Date range  : {start_date}  →  {end_date}")
    print(f"  Trade days  : {len(trading_days)}")
    print(f"  Screener    : {args.screener.upper()}  |  Snapshots: {', '.join(scan_types)}")
    print(f"  Feed        : {args.feed.upper()}")
    print(f"  Filters     : gap ≥ {args.gap}%  |  ${args.price_min}–${args.price_max}  "
          f"|  RVOL ≥ {args.rvol_min}x  |  float ≤ {args.float_max}M")
    print(f"  Workers     : {args.workers} per day")
    print(f"  Dry run     : {'YES — Supabase save disabled' if args.dry_run else 'No'}")
    print(f"{bar}\n")

    # ── Step 1: Finviz universe(s) ─────────────────────────────────────────
    print("[ 1/4 ]  Fetching Finviz universe(s)...", flush=True)
    gap_universe   = []
    trend_universe = []

    if gap_scan_types:
        print(f"         Gap pass: float ≤100M, ${args.price_min}–${args.price_max}, avgvol ≥1M...", flush=True)
        gap_universe = fetch_smallcap_universe(
            float_max_m=args.float_max,
            price_min=args.price_min,
            price_max=args.price_max,
            avg_vol_min_k=1000,
            max_tickers=3000,
        )
        print(f"         → {len(gap_universe)} gap-screener tickers")

    if trend_scan_types:
        print("         Trend pass: float ≤500M, $5–$50, avgvol ≥2M...", flush=True)
        trend_universe = fetch_smallcap_universe(
            float_max_m=500.0,
            price_min=5.0,
            price_max=50.0,
            avg_vol_min_k=2000,
            max_tickers=3000,
        )
        print(f"         → {len(trend_universe)} trend-screener tickers")

    universe = list(dict.fromkeys(gap_universe + trend_universe))
    if not universe:
        print("\nERROR: Finviz returned 0 tickers.")
        sys.exit(1)
    print(f"       → {len(universe)} combined unique tickers in universe\n")

    # ── Step 2: Alpaca daily bars ──────────────────────────────────────────
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

    # ── Step 3: Dedup ─────────────────────────────────────────────────────
    print("[ 3/4 ]  Loading existing records for dedup...", flush=True)
    existing_triples = load_existing_triples(user_id=args.user_id)
    print(f"       → {len(existing_triples)} (ticker, date, scan_type) triples already in Supabase\n")

    # ── Step 4: Per-day backtest ───────────────────────────────────────────
    print(f"[ 4/4 ]  Running per-day backtest ({len(trading_days)} days × {len(scan_types)} snapshots)...")
    print(dash)

    split_idx  = max(1, int(len(trading_days) * 0.70))
    train_days = set(str(d) for d in trading_days[:split_idx])

    all_new_rows: list = []
    total_qualified = 0
    total_run       = 0

    for day in trading_days:
        day_str   = str(day)
        split_tag = "TRAIN" if day_str in train_days else " TEST"

        # ── Build combined item list: {ticker, gap_pct, rvol, _scan_types} ──
        # ticker_items is keyed by ticker so the same ticker in both screeners
        # gets all applicable scan_types merged (avoids duplicate intraday fetches)
        ticker_items: dict = {}

        if gap_scan_types:
            gap_wl = reconstruct_daily_watchlist(
                all_daily_bars, day,
                gap_min_pct=args.gap,
                price_min=args.price_min,
                price_max=args.price_max,
                rvol_min=args.rvol_min,
            )
            for item in gap_wl:
                t = item["ticker"]
                ticker_items[t] = {**item, "_scan_types": list(gap_scan_types)}

        if trend_scan_types:
            trend_wl = reconstruct_trend_watchlist(all_daily_bars, day)
            for item in trend_wl:
                t = item["ticker"]
                if t in ticker_items:
                    # Already in gap list — add trend scan_types to avoid dual fetch
                    ticker_items[t]["_scan_types"] = (
                        ticker_items[t]["_scan_types"] + trend_scan_types
                    )
                else:
                    ticker_items[t] = {**item, "_scan_types": list(trend_scan_types)}

        all_day_items = list(ticker_items.values())
        total_qualified += len(all_day_items)

        # Find items that still need at least one snapshot
        new_items = [
            item for item in all_day_items
            if any(
                (item["ticker"], day_str, st) not in existing_triples
                for st in item["_scan_types"]
            )
        ]
        if not new_items:
            print(f"  {day_str} [{split_tag}]  {len(all_day_items):3d} qualified  "
                  f"all snapshots already in DB — skipped")
            continue

        # Per-ticker: determine which scan_types are still needed
        def _needed_for_item(item, _day_str=day_str):
            return [
                st for st in item["_scan_types"]
                if (item["ticker"], _day_str, st) not in existing_triples
            ]

        day_results: list = []
        with ThreadPoolExecutor(max_workers=min(args.workers, len(new_items))) as ex:
            futures = {
                ex.submit(
                    _worker_multi_snapshot,
                    api_key, secret_key, item["ticker"],
                    day, args.feed, args.price_min, args.price_max,
                    _needed_for_item(item),
                    item["gap_pct"],
                    item.get("rvol", 0.0),
                ): item["ticker"]
                for item in new_items
            }
            for fut in as_completed(futures):
                rows = fut.result()
                for r in rows:
                    r["sim_date"] = day_str
                    r["split"]    = "train" if day_str in train_days else "test"
                    day_results.append(r)

        total_run += len(new_items)
        all_new_rows.extend(day_results)

        # Win rate across all "morning-equivalent" snapshots for display
        morn_results = [r for r in day_results if r.get("scan_type") in ("morning", "trend_morning")]
        day_wins = sum(1 for r in morn_results if r.get("win_loss") == "Win")
        day_wr   = (f"{round(day_wins / len(morn_results) * 100, 1):.1f}%"
                    if morn_results else "  n/a")
        snap_counts = {st: sum(1 for r in day_results if r.get("scan_type") == st)
                       for st in scan_types}
        snap_str = "  ".join(f"{SCAN_CONFIGS[st]['label']}:{snap_counts[st]}" for st in scan_types)
        print(
            f"  {day_str} [{split_tag}]  "
            f"{len(all_day_items):3d} qualified  "
            f"{len(new_items):3d} new  "
            f"[{snap_str}]  "
            f"WR(morn): {day_wr}"
        )

    print(dash + "\n")

    # ── Save ──────────────────────────────────────────────────────────────
    if all_new_rows:
        if args.dry_run:
            print(f"[DRY RUN] Would save {len(all_new_rows)} rows — skipped.\n")
        else:
            print(f"Saving {len(all_new_rows)} new rows to Supabase...", flush=True)
            save_rows_with_scan_type(all_new_rows, user_id=args.user_id)
            print("  → Saved.\n")
    else:
        print("No new rows to save (all snapshots already in Supabase).\n")

    # ── Summary ───────────────────────────────────────────────────────────
    print(bar)
    print("  SUMMARY")
    print(bar)
    print(f"  Universe size       : {len(universe):,} tickers")
    print(f"  Tickers with data   : {len(all_daily_bars):,} tickers")
    print(f"  Trading days        : {len(trading_days)}")
    print(f"  Snapshots run       : {', '.join(scan_types)}")
    print(f"  Total qualified     : {total_qualified:,}  (gap/price/RVOL filter)")
    print(f"  New runs attempted  : {total_run:,}")
    print(f"  New rows saved      : {len(all_new_rows):,}")

    if all_new_rows:
        def _wr_block(label: str, rows: list):
            if not rows:
                return
            wins = sum(1 for r in rows if r.get("win_loss") == "Win")
            print(f"\n  Structure Win Rate ({label}):")
            print(f"    ALL    : {round(wins/len(rows)*100,1):.1f}%  ({wins}/{len(rows)})")
            tr = [r for r in rows if r.get("split") == "train"]
            te = [r for r in rows if r.get("split") == "test"]
            if tr:
                tw = sum(1 for r in tr if r.get("win_loss") == "Win")
                print(f"    TRAIN  : {round(tw/len(tr)*100,1):.1f}%  ({tw}/{len(tr)})")
            if te:
                tw = sum(1 for r in te if r.get("win_loss") == "Win")
                print(f"    TEST   : {round(tw/len(te)*100,1):.1f}%  ({tw}/{len(te)})")
            struct_counts: dict = {}
            for r in rows:
                s = r.get("actual_outcome", "?")
                struct_counts[s] = struct_counts.get(s, 0) + 1
            print(f"  Outcome breakdown:")
            for lbl, cnt in sorted(struct_counts.items(), key=lambda x: -x[1]):
                pct = round(cnt / len(rows) * 100, 1)
                print(f"    {lbl:<22}  {cnt:5,}  ({pct:.1f}%)")

        morning_all       = [r for r in all_new_rows if r.get("scan_type") == "morning"]
        trend_morning_all = [r for r in all_new_rows if r.get("scan_type") == "trend_morning"]

        _wr_block("gap morning snapshots",   morning_all)
        _wr_block("trend morning snapshots", trend_morning_all)

        eod_scans = ("eod", "trend_eod")
        eod_all = [r for r in all_new_rows if r.get("scan_type") in eod_scans]
        if eod_all:
            tcs_vals = [r["tcs"] for r in eod_all if r.get("tcs", 0) > 0]
            if tcs_vals:
                print(f"\n  EOD TCS stats (full-day, matches paper_trades):")
                print(f"    Avg TCS : {round(sum(tcs_vals)/len(tcs_vals),1)}")
                print(f"    Median  : {sorted(tcs_vals)[len(tcs_vals)//2]}")

    print(bar)
    if not args.dry_run and all_new_rows:
        print("\n  Open the Backtest tab → Paper Trade Replay to see results.\n")

        # ── Auto-recalibrate both brains after new data is saved ──────────────
        # New backtest rows shift per-structure accuracy → thresholds must update.
        # recalibrate_from_history() saves brain_weights_historical.json AND
        # tcs_thresholds.json so the bot uses fresh cutoffs at next scan.
        print("  Recalibrating historical brain + TCS thresholds from new data...")
        try:
            result = backend.recalibrate_from_history(user_id=args.user_id)
            deltas = result.get("deltas", [])
            if deltas:
                top = sorted(deltas, key=lambda x: abs(x["delta"]), reverse=True)[:3]
                for d in top:
                    print(f"    {d['key']:20s}  {d['old']:.4f} → {d['new']:.4f}  (Δ{d['delta']:+.4f})")
            else:
                print("    No weight changes (thresholds stable).")
            print("  ✓ tcs_thresholds.json updated — bot will use fresh cutoffs.\n")
        except Exception as _rc_err:
            print(f"  ⚠ Recalibration failed (non-fatal): {_rc_err}\n")

        # ── Targeted tiered P&L backfill for the just-processed date window ──
        # Rows inserted above may have tiered_pnl_r=NULL if the Alpaca bars
        # fetch inside _analyze_at_cutoff returned fewer than 5 bars or the
        # schema-error fallback path was triggered.  Running backfill_backtest_
        # sim_runs() scoped to [start_date, end_date] closes this gap without
        # rescanning the entire table.
        if not args.skip_post_backfill:
            _dry_run_prefix = "[DRY RUN] " if args.dry_run else ""
            print(f"  {_dry_run_prefix}Running targeted tiered P&L backfill for this date window...")
            try:
                bstats = backfill_backtest_sim_runs(
                    dry_run=args.dry_run,
                    rate_limit=not args.no_ratelimit,
                    date_from=str(start_date),
                    date_to=str(end_date),
                )
                _dry_run_note = " (dry run — no rows written)" if args.dry_run else ""
                print(
                    f"  ✓ {_dry_run_prefix}Tiered P&L backfill complete{_dry_run_note} — "
                    f"{bstats['updated']} row(s) updated, "
                    f"{bstats['skipped_no_bars']} skipped (no bars), "
                    f"{bstats['errors']} error(s).\n"
                )
            except Exception as _bf_err:
                print(f"  ⚠ Tiered P&L backfill failed (non-fatal): {_bf_err}\n")
        else:
            print("  Post-backfill skipped (--skip-post-backfill). "
                  "Nightly refresh will handle any remaining NULLs.\n")

    print()


if __name__ == "__main__":
    main()
