import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, timedelta, time as dtime
import pytz
import threading
import queue
import time
from collections import deque

st.set_page_config(page_title="Volume Profile Dashboard", page_icon="📊", layout="wide")

# ── Session state ──────────────────────────────────────────────────────────────
_DEFAULTS = {
    "live_active": False,
    "live_bars": [],
    "live_current_bar": None,
    "live_trades": deque(maxlen=3000),
    "live_thread": None,
    "live_stop_event": None,
    "live_queue": None,
    "live_ticker": "",
    "live_error": None,
    # Alert state
    "tcs_fired_high": False,   # True once ≥ 80% crossed this session
    "tcs_was_high": False,     # True while TCS was ≥ 60% (for chop-drop detection)
    "sound_trigger": 0,        # Incremented to force fresh audio iframe
    # RVOL / Sector cache — pre-fetched once per analysis session
    "rvol_avg_vol": None,          # Average daily volume (5-day lookback)
    "sector_pct_chg": 0.0,         # Sector ETF % change for current date
    "rvol_intraday_curve": None,   # Per-minute cumulative vol curve (390 elements)
    # Ticker widget state (explicit key so scanner can override)
    "ticker_input": "AAPL",
    # Auto-run flag: scanner sets this to True to trigger historical analysis on next rerun
    "auto_run": False,
    # Gap Scanner results cache
    "scanner_results": [],         # [{ticker, price, gap_pct, pm_vol, avg_pm_vol, pm_rvol}]
    "scanner_last_run": None,      # datetime of last successful scan
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Audio JS (Web Audio API, synthesised tones — no external files) ────────────
_CHIME_JS = """(function(){
  try{
    var C=new(window.AudioContext||window.webkitAudioContext)();
    [[523.25,0],[659.25,0.18],[783.99,0.36],[1046.50,0.54]].forEach(function(fd){
      var o=C.createOscillator(),g=C.createGain();
      o.type='sine'; o.frequency.value=fd[0];
      o.connect(g); g.connect(C.destination);
      var t=C.currentTime+fd[1];
      g.gain.setValueAtTime(0.001,t);
      g.gain.linearRampToValueAtTime(0.26,t+0.04);
      g.gain.exponentialRampToValueAtTime(0.001,t+0.42);
      o.start(t); o.stop(t+0.43);
    });
  }catch(e){}
})();"""

_LOW_TONE_JS = """(function(){
  try{
    var C=new(window.AudioContext||window.webkitAudioContext)();
    [[240,0],[200,0.26],[155,0.52]].forEach(function(fd){
      var o=C.createOscillator(),g=C.createGain();
      o.type='triangle'; o.frequency.value=fd[0];
      o.connect(g); g.connect(C.destination);
      var t=C.currentTime+fd[1];
      g.gain.setValueAtTime(0.001,t);
      g.gain.linearRampToValueAtTime(0.32,t+0.05);
      g.gain.exponentialRampToValueAtTime(0.001,t+0.40);
      o.start(t); o.stop(t+0.41);
    });
  }catch(e){}
})();"""

EASTERN = pytz.timezone("America/New_York")

# ══════════════════════════════════════════════════════════════════════════════
# CORE MATH
# ══════════════════════════════════════════════════════════════════════════════

def fetch_bars(api_key, secret_key, ticker, trade_date, feed="sip"):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client = StockHistoricalDataClient(api_key, secret_key)
    mo = EASTERN.localize(datetime(trade_date.year, trade_date.month, trade_date.day, 9, 30))
    mc = EASTERN.localize(datetime(trade_date.year, trade_date.month, trade_date.day, 16, 0))
    req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
                           start=mo, end=mc, feed=feed)
    bars = client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level="symbol")
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(EASTERN)
    df = df.sort_index()
    df = df[(df.index.time >= dtime(9, 30)) & (df.index.time <= dtime(16, 0))]
    return df


def compute_initial_balance(df):
    ib_end = df.index[0].replace(hour=10, minute=30, second=0)
    ib_data = df[df.index <= ib_end]
    if ib_data.empty:
        return None, None
    return float(ib_data["high"].max()), float(ib_data["low"].min())


def compute_volume_profile(df, num_bins):
    price_min = df["low"].min()
    price_max = df["high"].max()
    bins = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bins[:-1] + bins[1:]) / 2
    vap = np.zeros(num_bins)
    for _, row in df.iterrows():
        lo, hi, vol = row["low"], row["high"], row["volume"]
        i0 = max(0, int(np.searchsorted(bins, lo, side="left")) - 1)
        i1 = min(num_bins, int(np.searchsorted(bins, hi, side="right")))
        sp = i1 - i0
        if sp > 0:
            vap[i0:i1] += vol / sp
    poc_idx = int(np.argmax(vap))
    return bin_centers, vap, float(bin_centers[poc_idx])


def _find_peaks(smoothed, bin_centers, threshold_pct=0.30):
    """Return indices of local maxima that exceed threshold_pct of the profile max."""
    n = len(smoothed)
    max_v = smoothed.max()
    peaks = []
    for i in range(3, n - 3):
        if (smoothed[i] >= max_v * threshold_pct and
                smoothed[i] > smoothed[i-1] and smoothed[i] > smoothed[i+1] and
                smoothed[i] > smoothed[i-2] and smoothed[i] > smoothed[i+2]):
            # Deduplicate: require at least 3 bins from the previous accepted peak
            if not peaks or (i - peaks[-1]) >= 3:
                peaks.append(i)
    return peaks


def _is_strong_hvn(pk, vap):
    """True if peak qualifies as an HVN by small-cap DD criteria.

    Either:
      • Volume in ±2-bin window around peak > 20 % of total session volume, OR
      • Peak bin volume > 2.5× the average bin volume.
    """
    total_vol = vap.sum()
    if total_vol == 0:
        return False
    avg_bin = total_vol / len(vap)
    window = vap[max(0, pk-2): min(len(vap), pk+3)].sum()
    return (window / total_vol > 0.20) or (vap[pk] > 2.5 * avg_bin)


def _detect_double_distribution(bin_centers, vap, min_bin_sep=15):
    """Return (pk1_idx, pk2_idx, lvn_idx) if a valid Double Distribution is found, else None."""
    smoothed = np.convolve(vap.astype(float), np.ones(5)/5, mode="same")
    peaks = _find_peaks(smoothed, bin_centers, threshold_pct=0.25)
    for j in range(len(peaks) - 1):
        pk1, pk2 = peaks[j], peaks[j+1]
        # Must be at least 15 bins apart
        if (pk2 - pk1) < min_bin_sep:
            continue
        # Both peaks must qualify as strong HVNs
        if not (_is_strong_hvn(pk1, vap) and _is_strong_hvn(pk2, vap)):
            continue
        # Must have a clear LVN valley between them
        vi = int(np.argmin(smoothed[pk1:pk2+1])) + pk1
        if smoothed[vi] < 0.60 * min(smoothed[pk1], smoothed[pk2]):
            return pk1, pk2, vi
    return None


def classify_day_structure(df, bin_centers, vap, ib_high, ib_low, poc_price):
    day_high = float(df["high"].max())
    day_low = float(df["low"].min())
    total_range = day_high - day_low
    ib_range = ib_high - ib_low
    final_price = float(df["close"].iloc[-1])
    if total_range == 0 or ib_range == 0:
        return "⚖️ Normal / Balanced", "#66bb6a", "Insufficient range data."
    poc_pos = (poc_price - day_low) / total_range

    # ── 1. Double Distribution (prioritised — check before single-direction labels) ──
    dd = _detect_double_distribution(bin_centers, vap)
    if dd is not None:
        pk1, pk2, vi = dd
        sep_bins = pk2 - pk1
        sep_price = bin_centers[pk2] - bin_centers[pk1]
        pct1 = vap[max(0,pk1-2):min(len(vap),pk1+3)].sum() / vap.sum() * 100
        pct2 = vap[max(0,pk2-2):min(len(vap),pk2+3)].sum() / vap.sum() * 100
        return ("⚡ Double Distribution", "#00bcd4",
                f"HVNs at ${bin_centers[pk1]:.2f} ({pct1:.0f}% vol) & "
                f"${bin_centers[pk2]:.2f} ({pct2:.0f}% vol) — "
                f"{sep_bins}-bin / ${sep_price:.2f} gap. "
                f"LVN at ${bin_centers[vi]:.2f}. Two distinct auctions.")

    # ── 2. Trend Day ──────────────────────────────────────────────────────────
    if total_range > 2.5 * ib_range:
        bull = final_price > day_low + total_range / 2
        return ("📈 Trend Day", "#ff9800",
                f"{'Bullish' if bull else 'Bearish'} — range ${total_range:.2f} is "
                f"{total_range/ib_range:.1f}× the IB (${ib_range:.2f}). Strong directional conviction.")

    # ── 3. P-Shape ────────────────────────────────────────────────────────────
    if poc_pos >= 0.75 and final_price > ib_high:
        return ("🅟 P-Shape (Short Covering)", "#ce93d8",
                f"POC ${poc_price:.2f} in top {100*(1-poc_pos):.0f}% of range, "
                f"close ${final_price:.2f} > IB High ${ib_high:.2f}. Shorts covering into strength.")

    # ── 4. b-Shape ────────────────────────────────────────────────────────────
    if poc_pos <= 0.25 and final_price < ib_low:
        return ("🅑 b-Shape (Long Liquidation)", "#ef5350",
                f"POC ${poc_price:.2f} in bottom {100*poc_pos:.0f}% of range, "
                f"close ${final_price:.2f} < IB Low ${ib_low:.2f}. Longs liquidating into weakness.")

    # ── 5. Normal / Balanced ──────────────────────────────────────────────────
    pct = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean()) * 100
    return ("⚖️ Normal / Balanced", "#66bb6a",
            f"Price inside IB for {pct:.0f}% of session — balanced, rotational day.")


def compute_structure_probabilities(df, bin_centers, vap, ib_high, ib_low, poc_price):
    day_high = float(df["high"].max())
    day_low = float(df["low"].min())
    total_range = day_high - day_low
    ib_range = ib_high - ib_low
    final_price = float(df["close"].iloc[-1])
    if total_range == 0 or ib_range == 0:
        return {"Trend Day": 20.0, "P-Shape": 20.0, "b-Shape": 20.0, "Dbl Dist": 20.0, "Normal": 20.0}

    rr = total_range / ib_range
    poc_pos = (poc_price - day_low) / total_range
    pct_inside = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean())

    td = 5.0 + max(0.0, (rr - 1.0) * 28.0)
    td = min(td, 90.0)

    ps = 5.0 + max(0.0, (poc_pos - 0.50) * 65.0)
    if final_price > ib_high:
        ps += 18.0
    ps = min(ps, 80.0)

    bs = 5.0 + max(0.0, (0.50 - poc_pos) * 65.0)
    if final_price < ib_low:
        bs += 18.0
    bs = min(bs, 80.0)

    # Use the strict DD detector — bump score significantly if it fires
    has_dd = _detect_double_distribution(bin_centers, vap) is not None
    dd = 55.0 if has_dd else 5.0

    nm = 8.0 + pct_inside * 50.0

    scores = {"Trend Day": td, "P-Shape": ps, "b-Shape": bs, "Dbl Dist": dd, "Normal": nm}
    total = sum(scores.values())
    return {k: round(v / total * 100, 1) for k, v in scores.items()}


def fetch_avg_daily_volume(api_key, secret_key, ticker, trade_date, lookback_days=5):
    """Return the average total daily volume for ticker over the last N trading days before trade_date."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client = StockHistoricalDataClient(api_key, secret_key)
    start = EASTERN.localize(
        datetime(trade_date.year, trade_date.month, trade_date.day) - timedelta(days=lookback_days * 2)
    )
    end = EASTERN.localize(datetime(trade_date.year, trade_date.month, trade_date.day))
    req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Day, start=start, end=end)
    bars = client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        return None
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level="symbol")
    df = df.sort_index()
    # Keep only the last lookback_days complete days
    df = df.tail(lookback_days)
    if df.empty:
        return None
    return float(df["volume"].mean())


def fetch_etf_pct_change(api_key, secret_key, etf, trade_date, feed="iex"):
    """Return today's open-to-close percent change for the given ETF ticker."""
    try:
        df = fetch_bars(api_key, secret_key, etf, trade_date, feed=feed)
        if df.empty:
            return 0.0
        open_price = float(df["open"].iloc[0])
        close_price = float(df["close"].iloc[-1])
        if open_price == 0:
            return 0.0
        return (close_price - open_price) / open_price * 100.0
    except Exception:
        return 0.0


def is_market_open():
    """True if the current EST clock is within regular session hours (9:30–16:00)."""
    t = datetime.now(EASTERN).time()
    return dtime(9, 30) <= t <= dtime(16, 0)


def build_rvol_intraday_curve(api_key, secret_key, ticker, trade_date,
                               lookback_days=5, feed="iex"):
    """Build a 390-element list of average cumulative volume at each minute from open.

    Each element i represents the expected cumulative volume after (i+1) minutes of
    trading, averaged across the last lookback_days sessions before trade_date.
    Returns None if insufficient data.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(api_key, secret_key)
    start_dt = EASTERN.localize(
        datetime(trade_date.year, trade_date.month, trade_date.day)
        - timedelta(days=lookback_days * 3)   # extra buffer for weekends/holidays
    )
    end_dt = EASTERN.localize(
        datetime(trade_date.year, trade_date.month, trade_date.day)
    )
    req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
                           start=start_dt, end=end_dt, feed=feed)
    bars = client.get_stock_bars(req)
    df_all = bars.df
    if df_all.empty:
        return None

    if isinstance(df_all.index, pd.MultiIndex):
        df_all = df_all.xs(ticker, level="symbol")
    df_all.index = pd.to_datetime(df_all.index)
    if df_all.index.tz is None:
        df_all.index = df_all.index.tz_localize("UTC")
    df_all.index = df_all.index.tz_convert(EASTERN)
    df_all = df_all.sort_index()

    # Keep only market hours
    df_all = df_all[(df_all.index.time >= dtime(9, 30)) &
                    (df_all.index.time <= dtime(16, 0))]
    if df_all.empty:
        return None

    # Vectorised minutes-from-open (9:30 = minute 0)
    df_all = df_all.copy()
    df_all["_mins"] = df_all.index.hour * 60 + df_all.index.minute - (9 * 60 + 30)
    df_all["_date"] = pd.to_datetime(df_all.index.date)

    day_curves = []
    for day, grp in df_all.groupby("_date"):
        if day.date() >= trade_date:           # exclude the analysis date itself
            continue
        cv = np.zeros(390)
        for _, row in grp.iterrows():
            m = int(row["_mins"])
            if 0 <= m < 390:
                cv[m] = float(row["volume"])
        day_curves.append(np.cumsum(cv))

    if not day_curves:
        return None

    day_curves = day_curves[-lookback_days:]   # keep most recent N days
    return np.mean(day_curves, axis=0).tolist()


def compute_rvol(df, intraday_curve=None, avg_daily_vol=None):
    """Time-segmented RVOL (preferred) with pace-adjusted fallback.

    Time-segmented: compare cumulative volume at current elapsed minute to the
    historical average cumulative volume at the same minute of day.
    Fallback: extrapolate current pace to full session / full-day average.
    Returns None if no baseline is available.
    """
    if df.empty:
        return None
    current_vol = float(df["volume"].sum())
    elapsed_bars = max(1, len(df))

    # ── Time-segmented RVOL ───────────────────────────────────────────────────
    if intraday_curve is not None and len(intraday_curve) >= elapsed_bars:
        idx = min(elapsed_bars - 1, len(intraday_curve) - 1)
        expected_vol = float(intraday_curve[idx])
        if expected_vol > 0:
            return round(current_vol / expected_vol, 2)

    # ── Pace-adjusted fallback ────────────────────────────────────────────────
    if avg_daily_vol is not None and avg_daily_vol > 0:
        pace = (current_vol / elapsed_bars) * 390   # 390-minute session
        return round(pace / avg_daily_vol, 2)

    return None


def rvol_classify(rvol, pct_chg_today, elapsed_bars=None, price_now=None):
    """Time-aware RVOL label.

    elapsed_bars — minutes since 9:30 AM open (None = historical full-day view)
    price_now    — current last price (for small-cap volatility adjustment)
    Returns (label | None, color, is_runner, is_play)
    """
    if rvol is None:
        return None, "#aaaaaa", False, False

    # ── Runner tiers (highest priority) ───────────────────────────────────────
    if rvol > 5.5:
        return "🚀 MULTI-DAY RUNNER POTENTIAL", "#FFD700", True, True
    if rvol > 4.0:
        return "🔥 STOCK IN PLAY", "#FF6B35", False, True

    # ── 9:30–10:00 AM "Fuel Check" (first 30 minutes of session) ─────────────
    is_open_window = elapsed_bars is not None and 1 <= elapsed_bars <= 30
    if is_open_window and rvol > 3.0:
        return "🔥 HIGH CONVICTION OPEN", "#FF9500", False, True

    # ── Fake-out with small-cap volatility adjustment ─────────────────────────
    if rvol < 1.2 and pct_chg_today > 0.5:
        # For stocks priced $2–$20 (small-cap / low-float) noise threshold is 1%
        # — only flag as fake-out if divergence is meaningful (> 1% move)
        if price_now is not None and 2.0 <= price_now <= 20.0:
            if pct_chg_today < 1.0:          # within noise band → ignore
                return None, "#aaaaaa", False, False
        return "⚠️ DEAD CAT / FAKE-OUT RISK", "#ef5350", False, False

    return None, "#aaaaaa", False, False


def compute_model_prediction(df, rvol, tcs, sector_bonus, market_open=True):
    """Classify move as Fake-out / High Conviction / Consolidation.

    market_open=False → returns ('Market Closed', '') so the renderer shows
    the sleep-mode info box instead of a directional warning.
    """
    if not market_open:
        return "Market Closed", ""

    if len(df) < 2:
        return "Consolidation", "Insufficient bars for prediction."

    price_start = float(df["open"].iloc[0])
    price_now   = float(df["close"].iloc[-1])
    pct_chg = (price_now - price_start) / price_start * 100.0 if price_start > 0 else 0.0

    # Fake-out: price up but volume weak
    if rvol is not None and rvol < 1.2 and pct_chg > 0.5:
        return ("Fake-out",
                f"Price +{pct_chg:.1f}% on anemic RVOL {rvol:.1f}× — volume is NOT confirming "
                "the move. High reversal risk. Wait for RVOL > 2.0 before trusting direction.")

    # High Conviction: strong RVOL + strong TCS
    if rvol is not None and rvol > 4.0 and tcs >= 60:
        tail = " Sector tailwind adds confirmation." if sector_bonus > 0 else ""
        return ("High Conviction",
                f"RVOL {rvol:.1f}× surge confirms directional participation. "
                f"TCS {tcs:.0f}% — institutional footprint visible. "
                f"Trend continuation is the high-probability path.{tail}")

    # Consolidation: low TCS
    if tcs < 35:
        return ("Consolidation",
                f"TCS {tcs:.0f}% — low trend energy. Price coiling inside range. "
                "Watch for a Volume Velocity spike to signal the next push.")

    # Moderate high conviction
    if abs(pct_chg) > 0.5 and (rvol is None or rvol >= 1.5):
        direction = f"+{pct_chg:.1f}%" if pct_chg > 0 else f"{pct_chg:.1f}%"
        bias = "upward" if pct_chg > 0 else "downward"
        return ("High Conviction",
                f"Price {direction} with TCS {tcs:.0f}% and volume not diverging. "
                f"Structure supports {bias} continuation.")

    return ("Consolidation",
            f"Mixed signals — TCS {tcs:.0f}%, "
            f"RVOL {'N/A' if rvol is None else f'{rvol:.1f}×'}. "
            "Price and volume not clearly aligned; range-bound action expected.")


def compute_tcs(df, ib_high, ib_low, poc_price, sector_bonus=0.0):
    """Trend Confidence Score (0–100).

    Three equally-weighted factors:
      • Range Factor    (40 pts) — day range vs IB range
      • Velocity Factor (30 pts) — current vol/min vs session avg vol/min
      • Structure Factor (30 pts) — price > 1 ATR from POC and trending away
    Optional sector_bonus: +10 pts if sector ETF is up > 1%.
    """
    tcs = 0.0

    day_high = float(df["high"].max())
    day_low = float(df["low"].min())
    total_range = day_high - day_low
    ib_range = (ib_high - ib_low) if (ib_high and ib_low) else 0.0
    final_price = float(df["close"].iloc[-1])

    # ── Range Factor (40 pts) ─────────────────────────────────────────────────
    if ib_range > 0:
        rr = total_range / ib_range
        if rr >= 2.5:
            tcs += 40.0
        elif rr > 1.1:
            tcs += 40.0 * (rr - 1.1) / (2.5 - 1.1)

    # ── Velocity Factor (30 pts) ──────────────────────────────────────────────
    if len(df) >= 6:
        w = min(3, len(df) // 2)
        current_vel = float(df["volume"].iloc[-w:].mean())
        avg_vel = float(df["volume"].mean())
        if avg_vel > 0:
            vr = current_vel / avg_vel
            if vr >= 2.0:
                tcs += 30.0
            elif vr > 1.0:
                tcs += 30.0 * (vr - 1.0) / (2.0 - 1.0)

    # ── Structure Factor (30 pts) ─────────────────────────────────────────────
    if len(df) >= 3:
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)
        atr = float(tr.rolling(window=min(14, len(df))).mean().iloc[-1])

        if atr > 0 and abs(final_price - poc_price) > atr:
            # "Moving" = last 3 closes trending further from POC
            if len(df) >= 4:
                poc_side = 1 if final_price > poc_price else -1
                move = float(df["close"].iloc[-1]) - float(df["close"].iloc[-4])
                if move * poc_side > 0:
                    tcs += 30.0          # trending away — full credit
                else:
                    tcs += 15.0          # beyond ATR but stalling
            else:
                tcs += 20.0

    # ── Sector Tailwind bonus (+10 pts if sector ETF up > 1%) ────────────────
    tcs += sector_bonus

    return round(min(100.0, tcs), 1)


def compute_volume_velocity(df):
    if len(df) < 4:
        return None, None, None
    w = min(3, len(df) // 2)
    recent = float(df["volume"].iloc[-w:].mean())
    if len(df) < 2 * w:
        return recent, None, None
    prev = float(df["volume"].iloc[-2*w:-w].mean())
    if prev == 0:
        return recent, None, None
    chg = (recent - prev) / prev * 100
    return recent, abs(chg), ("↑" if chg >= 0 else "↓")


def check_tcs_alerts(tcs: float, audio_enabled: bool):
    """Fire visual toast + audio when TCS crosses key thresholds."""
    import streamlit.components.v1 as components

    # ── HIGH CONVICTION: TCS ≥ 80%, fires only once per session ───────────────
    if tcs >= 80 and not st.session_state.tcs_fired_high:
        st.session_state.tcs_fired_high = True
        st.session_state.tcs_was_high = True
        st.toast("🚀 HIGH CONVICTION TREND DETECTED", icon="🚀")
        if audio_enabled:
            n = st.session_state.sound_trigger + 1
            st.session_state.sound_trigger = n
            components.html(
                f'<script>/* hc:{n} */{_CHIME_JS}</script>',
                height=0, scrolling=False
            )

    # Track whether TCS has been "high" (≥ 60%) so we can detect a drop
    elif tcs >= 60:
        st.session_state.tcs_was_high = True

    # ── CHOP RISK: TCS drops below 30 after being high ────────────────────────
    if tcs < 30 and st.session_state.tcs_was_high:
        st.session_state.tcs_was_high = False
        st.toast("⚠️ CHOP RISK INCREASED", icon="⚠️")
        if audio_enabled:
            n = st.session_state.sound_trigger + 1
            st.session_state.sound_trigger = n
            components.html(
                f'<script>/* cr:{n} */{_LOW_TONE_JS}</script>',
                height=0, scrolling=False
            )


# ══════════════════════════════════════════════════════════════════════════════
# LIVE STREAM
# ══════════════════════════════════════════════════════════════════════════════

def _stream_worker(api_key, secret_key, ticker, feed_str, data_queue, stop_event):
    import asyncio
    from alpaca.data.live import StockDataStream
    from alpaca.data.enums import DataFeed

    feed_enum = DataFeed.SIP if feed_str == "sip" else DataFeed.IEX
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stream = StockDataStream(api_key, secret_key, feed=feed_enum)

    async def on_trade(trade):
        try:
            data_queue.put_nowait({"t": "trade", "p": float(trade.price),
                                   "s": float(trade.size), "ts": trade.timestamp})
        except Exception:
            pass

    async def on_bar(bar):
        try:
            data_queue.put_nowait({"t": "bar", "o": float(bar.open), "h": float(bar.high),
                                   "l": float(bar.low), "c": float(bar.close),
                                   "v": float(bar.volume), "ts": bar.timestamp})
        except Exception:
            pass

    stream.subscribe_trades(on_trade, ticker)
    stream.subscribe_bars(on_bar, ticker)

    async def run_until_stopped():
        task = asyncio.create_task(stream.run())
        while not stop_event.is_set():
            await asyncio.sleep(0.3)
        stream.stop()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except Exception:
            task.cancel()

    try:
        loop.run_until_complete(run_until_stopped())
    except Exception as e:
        try:
            data_queue.put_nowait({"t": "error", "msg": str(e)})
        except Exception:
            pass
    finally:
        try:
            loop.close()
        except Exception:
            pass


def start_stream(api_key, secret_key, ticker, feed_str):
    q = queue.Queue(maxsize=10000)
    ev = threading.Event()
    t = threading.Thread(target=_stream_worker,
                         args=(api_key, secret_key, ticker, feed_str, q, ev),
                         daemon=True)
    t.start()
    st.session_state.live_queue = q
    st.session_state.live_stop_event = ev
    st.session_state.live_thread = t
    st.session_state.live_active = True
    st.session_state.live_bars = []
    st.session_state.live_current_bar = None
    st.session_state.live_trades = deque(maxlen=3000)
    st.session_state.live_ticker = ticker
    st.session_state.live_error = None
    # Reset alert state for the new session
    st.session_state.tcs_fired_high = False
    st.session_state.tcs_was_high = False


def stop_stream():
    if st.session_state.live_stop_event:
        st.session_state.live_stop_event.set()
    st.session_state.live_active = False
    st.session_state.live_queue = None
    st.session_state.live_stop_event = None
    st.session_state.live_thread = None


def drain_queue():
    q = st.session_state.live_queue
    if q is None:
        return
    cur = st.session_state.live_current_bar or {}
    processed = 0
    while processed < 1000:
        try:
            item = q.get_nowait()
            processed += 1
        except queue.Empty:
            break
        t = item.get("t")
        if t == "error":
            st.session_state.live_error = item.get("msg", "Unknown error")
            st.session_state.live_active = False
        elif t == "bar":
            st.session_state.live_bars.append(
                {"open": item["o"], "high": item["h"], "low": item["l"],
                 "close": item["c"], "volume": item["v"], "timestamp": item["ts"]}
            )
            cur = {}
        elif t == "trade":
            p, s, ts = item["p"], item["s"], item["ts"]
            st.session_state.live_trades.append({"price": p, "size": s, "ts": ts})
            if not cur:
                cur = {"open": p, "high": p, "low": p, "close": p, "volume": s, "timestamp": ts}
            else:
                cur["high"] = max(cur["high"], p)
                cur["low"] = min(cur["low"], p)
                cur["close"] = p
                cur["volume"] = cur.get("volume", 0) + s
                cur["timestamp"] = ts
    st.session_state.live_current_bar = cur if cur else None


def build_live_df():
    rows = list(st.session_state.live_bars)
    if st.session_state.live_current_bar:
        rows.append(st.session_state.live_current_bar)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["timestamp"])
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(EASTERN)
    df = df.drop(columns=["timestamp"], errors="ignore")
    needed = ["open", "high", "low", "close", "volume"]
    if not all(c in df.columns for c in needed):
        return pd.DataFrame()
    df = df[needed].sort_index()
    df = df[(df.index.time >= dtime(9, 30)) & (df.index.time <= dtime(16, 0))]
    return df


# ══════════════════════════════════════════════════════════════════════════════
# CHART & RENDER
# ══════════════════════════════════════════════════════════════════════════════

def build_chart(df, ib_high, ib_low, bin_centers, vap, poc_price, title):
    fig = make_subplots(rows=1, cols=2, column_widths=[0.75, 0.25],
                        shared_yaxes=True, horizontal_spacing=0.01)
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="Price", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350",
    ), row=1, col=1)

    x0, x1 = df.index[0], df.index[-1]
    if ib_high is not None and ib_low is not None:
        fig.add_trace(go.Scatter(x=[x0, x1], y=[ib_high, ib_high], mode="lines",
            name=f"IB High ({ib_high:.2f})",
            line=dict(color="#00e676", width=1.8, dash="dash")), row=1, col=1)
        fig.add_trace(go.Scatter(x=[x0, x1], y=[ib_low, ib_low], mode="lines",
            name=f"IB Low ({ib_low:.2f})",
            line=dict(color="#ff5252", width=1.8, dash="dash")), row=1, col=1)

    fig.add_trace(go.Scatter(x=[x0, x1], y=[poc_price, poc_price], mode="lines",
        name=f"POC ({poc_price:.2f})", line=dict(color="gold", width=2.5)), row=1, col=1)

    bw = float(bin_centers[1] - bin_centers[0]) if len(bin_centers) > 1 else 0
    colors = ["gold" if abs(p - poc_price) < bw * 0.5 else "#5c6bc0" for p in bin_centers]
    fig.add_trace(go.Bar(x=vap, y=bin_centers, orientation="h",
        name="Volume Profile", marker_color=colors, opacity=0.85), row=1, col=2)

    fig.update_layout(
        title=dict(text=title, font=dict(size=17, color="white")),
        paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e", font=dict(color="#e0e0e0"),
        height=660,
        xaxis=dict(rangeslider=dict(visible=False), gridcolor="#2a2a4a",
                   showgrid=True, type="category"),
        yaxis=dict(gridcolor="#2a2a4a", showgrid=True, tickformat=".2f"),
        xaxis2=dict(gridcolor="#2a2a4a", showgrid=True, title="Volume"),
        legend=dict(bgcolor="#0f3460", bordercolor="#5c6bc0", borderwidth=1,
                    font=dict(color="white"), x=0.01, y=0.99),
        margin=dict(l=10, r=10, t=55, b=40),
    )
    fig.update_xaxes(nticks=20, tickangle=-45, row=1, col=1)
    return fig


def render_structure_banner(label, color, detail, probs, tcs,
                            is_runner=False, sector_bonus=0.0):
    top3 = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:3]
    prob_pills = "".join(
        f'<span style="display:inline-block; background:{color}33; border:1px solid {color}66; '
        f'border-radius:4px; padding:2px 8px; margin:2px 4px; font-size:13px; color:#eee;">'
        f'<b>{n}</b> {p}%</span>'
        for n, p in top3
    )

    # ── TCS gauge colour logic ─────────────────────────────────────────────────
    if is_runner:
        # Gold → Electric Blue gradient for MULTI-DAY RUNNER / STOCK IN PLAY
        gauge_fill = "linear-gradient(90deg,#FFD700,#00BFFF)"
        gauge_color = "#FFD700"
        badge = ('<span style="background:#FFD70033; border:1px solid #FFD700; border-radius:4px; '
                 'padding:2px 10px; font-size:12px; font-weight:700; color:#FFD700; '
                 'text-transform:uppercase; letter-spacing:1px; '
                 'box-shadow:0 0 8px #FFD70066;">⚡ RUNNER MODE</span>')
    elif tcs >= 70:
        gauge_fill = f"linear-gradient(90deg,#4caf5099,#4caf50)"
        gauge_color = "#4caf50"
        badge = ('<span style="background:#4caf5033; border:1px solid #4caf50; border-radius:4px; '
                 'padding:2px 10px; font-size:12px; font-weight:700; color:#4caf50; '
                 'text-transform:uppercase; letter-spacing:1px;">🔥 HIGH CONVICTION</span>')
    elif tcs <= 30:
        gauge_fill = f"linear-gradient(90deg,#ef535099,#ef5350)"
        gauge_color = "#ef5350"
        badge = ('<span style="background:#ef535033; border:1px solid #ef5350; border-radius:4px; '
                 'padding:2px 10px; font-size:12px; font-weight:700; color:#ef5350; '
                 'text-transform:uppercase; letter-spacing:1px;">⚠ CHOP RISK</span>')
    else:
        gauge_fill = f"linear-gradient(90deg,#ffa72699,#ffa726)"
        gauge_color = "#ffa726"
        badge = ""

    # Sector tailwind badge
    sector_badge = ""
    if sector_bonus > 0:
        sector_badge = ('<span style="background:#26a69a33; border:1px solid #26a69a; '
                        'border-radius:4px; padding:2px 10px; font-size:12px; font-weight:700; '
                        'color:#26a69a; text-transform:uppercase; letter-spacing:1px; '
                        'margin-left:6px;">🌊 SECTOR TAILWIND +10</span>')

    tcs_label = f"{tcs:.0f}%"
    if sector_bonus > 0:
        tcs_label += f" (+{sector_bonus:.0f} sector)"

    tcs_bar = f"""
    <div style="margin-top:10px; display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
        <span style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:1px; white-space:nowrap;">
            Trend Confidence
        </span>
        <div style="flex:1; min-width:120px; max-width:220px; background:#2a2a4a;
                    border-radius:6px; height:12px; overflow:hidden;">
            <div style="width:{min(tcs,100)}%; background:{gauge_fill};
                        height:100%; border-radius:6px; transition:width 0.4s;"></div>
        </div>
        <span style="font-size:18px; font-weight:800; color:{gauge_color}; min-width:44px;">{tcs_label}</span>
        {badge}{sector_badge}
    </div>
    """

    # Runner glow border
    glow = f"box-shadow:0 0 18px {gauge_color}55;" if is_runner else ""

    st.markdown(f"""
    <div style="background:linear-gradient(135deg,{color}22,{color}0a);
                border-left:5px solid {color}; border-radius:8px;
                padding:14px 22px; margin:10px 0 4px 0; {glow}">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;">
            <div>
                <div style="font-size:26px; font-weight:800; color:{color}; letter-spacing:0.5px;">{label}</div>
                <div style="font-size:13px; color:#cccccc; margin-top:4px;">{detail}</div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:11px; color:#888; text-transform:uppercase;
                            letter-spacing:1px; margin-bottom:4px;">Structure Probability</div>
                <div>{prob_pills}</div>
            </div>
        </div>
        {tcs_bar}
    </div>
    """, unsafe_allow_html=True)


def render_rvol_widget(rvol_val, label_str, label_color, is_runner):
    """Show the RVOL reading with appropriate colour and optional glow for runner stocks."""
    if rvol_val is None:
        return
    display_label = label_str if label_str else f"RVOL {rvol_val:.1f}×  — Normal Activity"
    if label_str is None:
        label_color = "#aaaaaa"

    runner_style = ""
    if is_runner:
        runner_style = ("box-shadow:0 0 16px #FFD70077; "
                        "border-color:#FFD700 !important; "
                        "animation:rvol-pulse 1.8s ease-in-out infinite;")

    st.markdown(f"""
    <style>
    @keyframes rvol-pulse {{
        0%   {{ box-shadow: 0 0 8px #FFD70044; }}
        50%  {{ box-shadow: 0 0 22px #FFD700cc; }}
        100% {{ box-shadow: 0 0 8px #FFD70044; }}
    }}
    </style>
    <div style="display:inline-flex; align-items:center; background:{label_color}11;
                border:2px solid {label_color}77; border-radius:8px;
                padding:8px 20px; margin:4px 0 6px 0; gap:16px; {runner_style}">
        <span style="font-size:12px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">RVOL</span>
        <span style="font-size:24px; font-weight:900; color:{label_color};">{rvol_val:.1f}×</span>
        <span style="font-size:14px; font-weight:700; color:{label_color};">{display_label}</span>
    </div>
    """, unsafe_allow_html=True)


def render_model_prediction(outcome, reasoning):
    """Show the volume-price divergence model prediction in a styled text box.

    'Market Closed' outcome → shows a blue sleep-mode info box instead of
    a directional warning so stale/historical analysis isn't misread as live signal.
    """
    # ── Sleep mode: market is closed, suppress directional warnings ───────────
    if outcome == "Market Closed":
        st.markdown("""
        <div style="background:#0d47a114; border:1px solid #1565c066;
                    border-radius:8px; padding:12px 20px; margin:8px 0 4px 0;">
            <div style="font-size:10px; color:#5c8ecc; text-transform:uppercase;
                        letter-spacing:1.2px; margin-bottom:6px;">
                🤖 Model Prediction — Volume-Price Divergence
            </div>
            <div style="font-size:20px; font-weight:800; color:#64b5f6; margin-bottom:4px;">
                💤 MARKET CLOSED — Analyzing Historical Data
            </div>
            <div style="font-size:13px; color:#90caf9; line-height:1.6;">
                Live pattern alerts are suppressed outside 9:30 AM – 4:00 PM EST.
                Use the volume profile structure and TCS score for session-level context.
            </div>
        </div>
        """, unsafe_allow_html=True)
        return

    # ── Live / historical session directional signal ───────────────────────────
    _colors = {"Fake-out": "#ef5350", "High Conviction": "#4caf50", "Consolidation": "#ffa726"}
    _icons  = {"Fake-out": "⚠️", "High Conviction": "🎯", "Consolidation": "📊"}
    c    = _colors.get(outcome, "#aaaaaa")
    icon = _icons.get(outcome, "📊")
    st.markdown(f"""
    <div style="background:{c}0e; border:1px solid {c}44; border-radius:8px;
                padding:12px 20px; margin:8px 0 4px 0;">
        <div style="font-size:10px; color:#666; text-transform:uppercase;
                    letter-spacing:1.2px; margin-bottom:6px;">
            🤖 Model Prediction — Volume-Price Divergence
        </div>
        <div style="font-size:20px; font-weight:800; color:{c}; margin-bottom:4px;">
            {icon}&nbsp;{outcome}
        </div>
        <div style="font-size:13px; color:#c0c0c0; line-height:1.6;">
            {reasoning}
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_velocity_widget(df):
    vol, chg, direction = compute_volume_velocity(df)
    if vol is None:
        return
    color = "#26a69a" if direction == "↑" else "#ef5350" if direction == "↓" else "#aaa"
    chg_str = f"{direction} {chg:.1f}%" if chg is not None else "—"

    intensity = ""
    if chg is not None:
        if chg > 50:
            intensity = " — 🔥 Surging"
        elif chg > 20:
            intensity = " — ⬆ Accelerating"
        elif chg < -50:
            intensity = " — 🧊 Drying up"
        elif chg < -20:
            intensity = " — ⬇ Fading"

    st.markdown(f"""
    <div style="display:inline-flex; align-items:center; background:#16213e;
                border:1px solid #2a2a4a; border-radius:6px; padding:8px 18px; margin:4px 0 8px 0; gap:14px;">
        <span style="font-size:12px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">⚡ Vol Velocity</span>
        <span style="font-size:19px; font-weight:700; color:#e0e0e0;">{vol:,.0f}/min</span>
        <span style="font-size:16px; font-weight:600; color:{color};">{chg_str}{intensity}</span>
    </div>
    """, unsafe_allow_html=True)


def render_analysis(df, num_bins, ticker, chart_title, is_ib_live=False,
                    avg_daily_vol=None, sector_bonus=0.0, sector_etf="IWM",
                    intraday_curve=None, is_live=False):
    ib_high, ib_low = compute_initial_balance(df)
    bin_centers, vap, poc_price = compute_volume_profile(df, num_bins)
    label, color, detail = classify_day_structure(df, bin_centers, vap, ib_high, ib_low, poc_price)
    probs = compute_structure_probabilities(df, bin_centers, vap, ib_high, ib_low, poc_price)
    tcs = compute_tcs(df, ib_high, ib_low, poc_price, sector_bonus=sector_bonus)
    audio_enabled = st.session_state.get("audio_alerts_enabled", True)
    check_tcs_alerts(tcs, audio_enabled)

    # ── Time context ───────────────────────────────────────────────────────────
    # elapsed_bars = minutes of session captured so far (used for time-seg RVOL + Fuel Check)
    elapsed_bars = len(df)
    # market_open:
    #   • Live mode  → check actual clock (suppress fake-out if market is closed)
    #   • Historical → True — session data is self-contained, show all signals
    market_open = is_market_open() if is_live else True

    # ── RVOL + Pattern Label ───────────────────────────────────────────────────
    price_start = float(df["open"].iloc[0]) if len(df) else 0.0
    price_now   = float(df["close"].iloc[-1]) if len(df) else 0.0
    pct_chg_today = (price_now - price_start) / price_start * 100.0 if price_start > 0 else 0.0

    rvol_val = compute_rvol(df, intraday_curve=intraday_curve, avg_daily_vol=avg_daily_vol)
    rvol_lbl, rvol_color, is_runner, is_play = rvol_classify(
        rvol_val, pct_chg_today,
        elapsed_bars=elapsed_bars if is_live else None,   # open-window check only in live mode
        price_now=price_now
    )

    # ── Model prediction ───────────────────────────────────────────────────────
    pred_outcome, pred_reasoning = compute_model_prediction(
        df, rvol_val, tcs, sector_bonus, market_open=market_open
    )

    day_high = float(df["high"].max())
    day_low  = float(df["low"].min())
    ib_range = (ib_high - ib_low) if ib_high and ib_low else 0.0

    # IB status badge for live mode
    if is_ib_live and ib_high and ib_low:
        now_est = datetime.now(EASTERN)
        mins_left = max(0, int((datetime.combine(date.today(), dtime(10, 30)) -
                                now_est.replace(tzinfo=None)).total_seconds() // 60))
        st.markdown(
            f'<div style="display:inline-block; background:#ff980033; border:1px solid #ff9800; '
            f'border-radius:4px; padding:3px 10px; font-size:13px; color:#ff9800; margin-bottom:8px;">'
            f'📐 IB FORMING — {mins_left} min remaining until 10:30 EST</div>',
            unsafe_allow_html=True
        )

    # ── Top metrics row ───────────────────────────────────────────────────────
    rvol_display = f"{rvol_val:.1f}×" if rvol_val is not None else "—"
    sector_display = (f"{sector_etf} +{sector_bonus:.0f}pts" if sector_bonus > 0
                      else f"{sector_etf} —")
    col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(8)
    col1.metric("Bars", len(df))
    col2.metric("IB High", f"${ib_high:.2f}" if ib_high else "—")
    col3.metric("IB Low",  f"${ib_low:.2f}"  if ib_low  else "—")
    col4.metric("IB Range", f"${ib_range:.2f}")
    col5.metric("Day Range", f"${day_high - day_low:.2f}")
    col6.metric("POC", f"${poc_price:.2f}")
    col7.metric("RVOL", rvol_display)
    col8.metric("Sector", sector_display)

    render_velocity_widget(df)
    render_rvol_widget(rvol_val, rvol_lbl, rvol_color, is_runner)
    render_structure_banner(label, color, detail, probs, tcs,
                            is_runner=is_runner, sector_bonus=sector_bonus)
    render_model_prediction(pred_outcome, pred_reasoning)

    fig = build_chart(df, ib_high, ib_low, bin_centers, vap, poc_price, chart_title)
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("📋 Raw Bar Data"):
        disp = df[["open", "high", "low", "close", "volume"]].copy()
        disp.index = disp.index.strftime("%H:%M")
        disp.columns = ["Open", "High", "Low", "Close", "Volume"]
        st.dataframe(disp, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PRE-MARKET GAP SCANNER
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_WATCHLIST = (
    "MSTR,COIN,SOFI,LCID,SIRI,SPCE,FFIE,WKHS,NKLA,MVIS,"
    "CLOV,BB,MMAT,SNDL,TLRY,AFRM,UPST,DKNG,BYND,PLTR,"
    "RIVN,CHPT,BLNK,ASTS,ACHR,JOBY,HOOD,OPEN,PSFE,NRDS"
)


def fetch_snapshots_bulk(api_key, secret_key, tickers, feed="iex"):
    """Batch-fetch latest price + previous day's close for a list of tickers.

    Returns {sym: {"price": float, "prev_close": float}} for tickers that
    have data; silently skips tickers with no snapshot.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockSnapshotRequest

    client = StockHistoricalDataClient(api_key, secret_key)
    req = StockSnapshotRequest(symbol_or_symbols=list(tickers), feed=feed)
    snaps = client.get_stock_snapshot(req)

    result = {}
    for sym, snap in snaps.items():
        try:
            price = None
            if getattr(snap, "latest_trade", None):
                price = float(snap.latest_trade.price)
            if price is None and getattr(snap, "daily_bar", None):
                price = float(snap.daily_bar.close)

            prev_close = None
            if getattr(snap, "prev_daily_bar", None):
                prev_close = float(snap.prev_daily_bar.close)

            if price is not None and prev_close is not None:
                result[sym] = {"price": price, "prev_close": prev_close}
        except Exception:
            pass
    return result


def fetch_premarket_vols(api_key, secret_key, ticker, trade_date,
                         lookback_days=10, feed="iex"):
    """Fetch today's pre-market volume + 10-day historical average.

    Pre-market window = 4:00 AM – 9:29 AM EST (regular extended hours).
    Returns (today_pm_vol: float, avg_hist_pm_vol: float | None).
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(api_key, secret_key)
    start_dt = EASTERN.localize(
        datetime(trade_date.year, trade_date.month, trade_date.day)
        - timedelta(days=lookback_days * 3)   # buffer for weekends / holidays
    )
    # Include up to 9:30 AM today to capture this morning's pre-market bars
    end_dt = EASTERN.localize(
        datetime(trade_date.year, trade_date.month, trade_date.day, 9, 30)
    )
    req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
                           start=start_dt, end=end_dt, feed=feed)
    bars = client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        return 0.0, None

    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(ticker, level="symbol")
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(EASTERN)
    df = df.sort_index()

    # Filter to pre-market window: 4:00 AM – 9:29 AM
    df = df[(df.index.time >= dtime(4, 0)) & (df.index.time < dtime(9, 30))]
    df["_date"] = df.index.date
    daily_vols = df.groupby("_date")["volume"].sum()

    today_vol = float(daily_vols.get(trade_date, 0.0))
    hist_vols = daily_vols[daily_vols.index < trade_date].tail(lookback_days)
    avg_vol = float(hist_vols.mean()) if not hist_vols.empty else None

    return today_vol, avg_vol


def run_gap_scanner(api_key, secret_key, watchlist, trade_date, feed="iex"):
    """Run the full gap-scanner pipeline and return the top 3 tickers by PM RVOL.

    Pipeline:
      1. Batch-fetch snapshots (price + prev_close)
      2. Filter to $2–$20 price range
      3. Fetch pre-market volumes + 10-day historical average per qualifying ticker
      4. Compute Gap % and Pre-Market RVOL
      5. Sort by RVOL descending, return top 3

    Returns list of dicts: [{ticker, price, gap_pct, pm_vol, avg_pm_vol, pm_rvol}]
    """
    # Step 1 — batch snapshots
    try:
        snaps = fetch_snapshots_bulk(api_key, secret_key, watchlist, feed=feed)
    except Exception:
        snaps = {}

    if not snaps:
        return []

    # Step 2 — filter by price
    qualifying = {
        sym: d for sym, d in snaps.items()
        if d.get("price") is not None and 2.0 <= d["price"] <= 20.0
    }
    if not qualifying:
        return []

    # Step 3 & 4 — pre-market volume + compute metrics
    rows = []
    for sym, snap_data in qualifying.items():
        try:
            pm_vol, avg_pm_vol = fetch_premarket_vols(
                api_key, secret_key, sym, trade_date,
                lookback_days=10, feed=feed)
        except Exception:
            pm_vol, avg_pm_vol = 0.0, None

        price      = snap_data["price"]
        prev_close = snap_data["prev_close"]
        gap_pct    = ((price - prev_close) / prev_close * 100.0
                      if prev_close and prev_close > 0 else 0.0)
        pm_rvol    = (round(pm_vol / avg_pm_vol, 2)
                      if avg_pm_vol and avg_pm_vol > 0 else None)

        rows.append({
            "ticker":     sym,
            "price":      round(price, 2),
            "gap_pct":    round(gap_pct, 2),
            "pm_vol":     int(pm_vol),
            "avg_pm_vol": round(avg_pm_vol, 0) if avg_pm_vol else None,
            "pm_rvol":    pm_rvol,
        })

    # Step 5 — sort by RVOL, top 3
    rows.sort(key=lambda r: r["pm_rvol"] if r["pm_rvol"] is not None else -1,
              reverse=True)
    return rows[:3]


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.header("🔑 Alpaca Credentials")
    api_key = st.text_input("API Key", type="password", placeholder="Alpaca API Key")
    secret_key = st.text_input("Secret Key", type="password", placeholder="Alpaca Secret Key")

    st.markdown("---")
    mode = st.radio("Mode", ["📅 Historical", "🔴 Live Stream"], index=0)

    st.markdown("---")
    st.header("📈 Settings")
    ticker = st.text_input("Ticker Symbol", key="ticker_input",
                           placeholder="e.g. AAPL, GME").upper().strip()
    num_bins = st.slider("Volume Profile Bins", min_value=20, max_value=200, value=100, step=10)
    sector_etf = st.selectbox(
        "Sector ETF (for Tailwind)",
        ["IWM", "XBI", "SMH", "QQQ", "SPY", "XLF", "XLE"],
        index=0,
        help="If this ETF is up > 1% on the day, TCS gets a +10 pt Sector Tailwind bonus."
    )

    run_button = start_live = stop_live = scan_button = False
    selected_date = date.today()
    data_feed = "sip"
    watchlist_raw = ""
    scan_feed = "iex"

    if mode == "📅 Historical":
        today = date.today()
        def_d = today - timedelta(days=1)
        if def_d.weekday() == 6:
            def_d -= timedelta(days=2)
        elif def_d.weekday() == 5:
            def_d -= timedelta(days=1)
        selected_date = st.date_input("Trading Date", value=def_d, max_value=today,
                                       help="Pick a weekday (Mon–Fri)")
        data_feed = st.selectbox("Data Feed", ["sip", "iex"], index=0,
                                  help="SIP = full tape. IEX = IEX exchange only.")
        run_button = st.button("🚀 Fetch & Analyze", use_container_width=True, type="primary")
    else:
        live_feed = st.selectbox("Data Feed", ["iex", "sip"], index=0,
                                  help="IEX works on all accounts. SIP needs a subscription.")
        if not st.session_state.live_active:
            start_live = st.button("▶ Start Live Stream", use_container_width=True, type="primary")
        else:
            stop_live = st.button("⏹ Stop", use_container_width=True)
            st.success(f"🔴 Live: **{st.session_state.live_ticker}**")

    st.markdown("---")

    # ── Audio alert controls ───────────────────────────────────────────────────
    st.header("🔔 Alerts")
    audio_alerts_enabled = st.checkbox(
        "Enable Audio Alerts",
        value=True,
        key="audio_alerts_enabled",
        help="Play sounds when TCS crosses 80% (chime) or drops below 30% (low tone)."
    )

    if audio_alerts_enabled:
        import streamlit.components.v1 as _comp
        _comp.html(
            """
            <style>
              .ab{background:#16213e;border:1px solid #5c6bc0;color:#aaa;
                  padding:5px 12px;border-radius:5px;cursor:pointer;
                  font-size:12px;width:100%;margin:2px 0;transition:all 0.2s;}
              .ab:hover{border-color:#90caf9;color:#e0e0e0;}
              .ab.ok{border-color:#4caf50!important;color:#4caf50!important;}
              small{color:#555;font-size:10px;line-height:1.4;display:block;margin-top:4px;}
            </style>
            <button class="ab" id="ab" onclick="
              var C=new(window.AudioContext||window.webkitAudioContext)();
              var o=C.createOscillator(),g=C.createGain();
              o.type='sine'; o.frequency.value=880;
              o.connect(g); g.connect(C.destination);
              g.gain.setValueAtTime(0.12,C.currentTime);
              g.gain.exponentialRampToValueAtTime(0.001,C.currentTime+0.3);
              o.start(); o.stop(C.currentTime+0.3);
              this.textContent='✓ Audio Ready';
              this.classList.add('ok');
            ">🔊 Enable Browser Audio</button>
            <small>Browsers block auto-play until you click above once per session.</small>
            """,
            height=68,
            scrolling=False,
        )

    st.markdown("---")

    # ── Gap Scanner Controls ───────────────────────────────────────────────────
    st.header("🔍 Gap Scanner")
    st.caption("Enter tickers to watch. Scan fetches pre-market volume and gap data.")
    watchlist_raw = st.text_area(
        "Watchlist (comma-separated)",
        value=_DEFAULT_WATCHLIST,
        height=110,
        help="Only tickers priced $2–$20 at scan time will be analysed.",
        key="watchlist_raw",
    )
    scan_feed = st.selectbox("Scanner Feed", ["iex", "sip"], index=0, key="scan_feed_select",
                             help="IEX = free tier. SIP = full tape (subscription needed).")
    scan_button = st.button("🔍 Scan Gap Plays", use_container_width=True)

    st.markdown("---")
    st.caption("SIP = full national tape (small-caps need this). IEX = IEX exchange only.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN AREA
# ══════════════════════════════════════════════════════════════════════════════

if mode == "🔴 Live Stream" and st.session_state.live_active:
    st.title("📊 Volume Profile Dashboard")
    st.markdown(f"🔴 **Live tape** — `{st.session_state.live_ticker}` — chart refreshes every 2 s")
else:
    st.title("📊 Volume Profile Dashboard — Small Cap Stocks")
    st.markdown("Visualize Volume Profile structures with Point of Control (POC) and Initial Balance (IB).")

tab_vp, tab_scan = st.tabs(["📊 Volume Profile", "🔍 Top 3 Stocks In Play"])

# ── Scanner tab ────────────────────────────────────────────────────────────────
with tab_scan:
    # ── Run scanner if button clicked ──────────────────────────────────────────
    if scan_button:
        if not api_key or not secret_key:
            st.error("Enter your Alpaca credentials in the sidebar first.")
        else:
            watchlist = [t.strip().upper() for t in watchlist_raw.split(",") if t.strip()]
            if not watchlist:
                st.warning("Watchlist is empty — add some tickers and try again.")
            else:
                with st.spinner(f"Scanning {len(watchlist)} tickers for pre-market gaps…"):
                    try:
                        results = run_gap_scanner(
                            api_key, secret_key, watchlist, date.today(), feed=scan_feed)
                        st.session_state.scanner_results = results
                        st.session_state.scanner_last_run = datetime.now(EASTERN)
                    except Exception as e:
                        st.error(f"Scanner error: {e}")

    # ── Display results ────────────────────────────────────────────────────────
    results = st.session_state.scanner_results
    last_run = st.session_state.scanner_last_run

    if last_run:
        st.caption(f"Last scan: {last_run.strftime('%H:%M:%S')} EST  ·  "
                   f"showing tickers priced $2–$20 sorted by Pre-Market RVOL")

    if not results:
        st.info("👈 Click **🔍 Scan Gap Plays** in the sidebar to populate this panel.\n\n"
                "The scanner checks every ticker in your watchlist, filters to the $2–$20 "
                "price range, and ranks them by today's pre-market volume relative to their "
                "10-day historical pre-market average.")
    else:
        _gap_colors = {
            "up":   ("#4caf50", "#1b5e20"),   # (text, bg-tint)
            "down": ("#ef5350", "#4e1111"),
            "flat": ("#aaaaaa", "#1a1a2e"),
        }
        _rvol_color = lambda r: (
            "#FFD700" if r is not None and r > 5.5 else
            "#FF6B35" if r is not None and r > 4.0 else
            "#FF9500" if r is not None and r > 3.0 else
            "#26a69a" if r is not None and r >= 1.2 else
            "#ef5350"
        )

        for row in results:
            sym   = row["ticker"]
            price = row["price"]
            gap   = row["gap_pct"]
            rvol  = row["pm_rvol"]
            pm_v  = row["pm_vol"]
            avg_v = row["avg_pm_vol"]

            gap_dir    = "up" if gap > 0.2 else "down" if gap < -0.2 else "flat"
            gap_txt    = f"+{gap:.2f}%" if gap >= 0 else f"{gap:.2f}%"
            gap_clr, gap_bg = _gap_colors[gap_dir]
            rc         = _rvol_color(rvol)
            rvol_str   = f"{rvol:.1f}×" if rvol is not None else "N/A"
            pm_str     = f"{pm_v:,}"
            avg_str    = f"{avg_v:,.0f}" if avg_v else "—"

            st.markdown(f"""
            <div style="background:#12122288; border:1px solid #2a2a4a;
                        border-radius:10px; padding:16px 20px; margin:10px 0;">
                <div style="display:flex; align-items:center; justify-content:space-between;
                            flex-wrap:wrap; gap:12px;">

                    <!-- Ticker + Price -->
                    <div>
                        <div style="font-size:26px; font-weight:900; color:#e0e0e0;
                                    letter-spacing:1px;">{sym}</div>
                        <div style="font-size:13px; color:#888;">${price:.2f}</div>
                    </div>

                    <!-- Gap % -->
                    <div style="text-align:center;">
                        <div style="font-size:10px; color:#666; text-transform:uppercase;
                                    letter-spacing:1px; margin-bottom:3px;">Gap</div>
                        <div style="font-size:22px; font-weight:800; color:{gap_clr};">
                            {gap_txt}
                        </div>
                    </div>

                    <!-- PM RVOL -->
                    <div style="text-align:center;">
                        <div style="font-size:10px; color:#666; text-transform:uppercase;
                                    letter-spacing:1px; margin-bottom:3px;">PM RVOL</div>
                        <div style="font-size:22px; font-weight:800; color:{rc};">
                            {rvol_str}
                        </div>
                        <div style="font-size:11px; color:#555;">
                            {pm_str} vs avg {avg_str}
                        </div>
                    </div>

                    <!-- Load button -->
                    <div style="text-align:right; min-width:140px;">
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Clickable button — loads Volume Profile for this ticker
            if st.button(f"📊 Load {sym} Volume Profile", key=f"load_{sym}",
                         use_container_width=False):
                st.session_state.ticker_input = sym
                st.session_state.auto_run = True
                st.rerun()

        st.caption("Click a **Load** button to auto-populate the ticker and run the analysis.")

# ── Volume Profile tab: auto_run + all historical/live content ─────────────────
# Consume the auto-run flag set by scanner ticker buttons (runs before tab renders)
auto_trigger = st.session_state.get("auto_run", False)
if auto_trigger:
    st.session_state.auto_run = False

with tab_vp:
    # ── Historical mode ────────────────────────────────────────────────────────
    if mode == "📅 Historical":
        if run_button or auto_trigger:
            if not api_key or not secret_key:
                st.error("Enter your Alpaca credentials in the sidebar.")
            elif not ticker:
                st.error("Enter a ticker symbol.")
            elif selected_date.weekday() >= 5:
                st.error("Selected date is a weekend. Pick a weekday (Mon–Fri).")
            else:
                # Fresh analysis — reset alert state so alerts fire for this data
                st.session_state.tcs_fired_high = False
                st.session_state.tcs_was_high = False
                with st.spinner(f"Fetching 1-min bars for **{ticker}** on {selected_date} ({data_feed.upper()})..."):
                    try:
                        df = fetch_bars(api_key, secret_key, ticker, selected_date, feed=data_feed)
                        if df.empty:
                            if data_feed == "sip":
                                st.warning(f"No data for **{ticker}** on {selected_date} via SIP. Try IEX, or confirm the date was a trading day.")
                            else:
                                st.warning(f"No data for **{ticker}** on {selected_date} via IEX. Small-caps are often absent on IEX — try SIP.")
                        else:
                            st.success(f"Loaded **{len(df)}** 1-min bars via {data_feed.upper()}.")
                            # ── Pre-fetch RVOL baseline (5-day avg daily volume) ──────
                            try:
                                avg_vol = fetch_avg_daily_volume(
                                    api_key, secret_key, ticker, selected_date)
                            except Exception:
                                avg_vol = None
                            st.session_state.rvol_avg_vol = avg_vol

                            # ── Time-segmented RVOL intraday curve ───────────────────
                            try:
                                curve = build_rvol_intraday_curve(
                                    api_key, secret_key, ticker, selected_date,
                                    lookback_days=5, feed=data_feed)
                            except Exception:
                                curve = None
                            st.session_state.rvol_intraday_curve = curve

                            # ── Sector ETF % change for that date ────────────────────
                            try:
                                etf_chg = fetch_etf_pct_change(
                                    api_key, secret_key, sector_etf, selected_date, feed=data_feed)
                            except Exception:
                                etf_chg = 0.0
                            sector_bonus = 10.0 if etf_chg > 1.0 else 0.0
                            st.session_state.sector_pct_chg = etf_chg

                            render_analysis(df, num_bins, ticker,
                                            f"{ticker} — Volume Profile | {selected_date.strftime('%B %d, %Y')}",
                                            avg_daily_vol=avg_vol,
                                            sector_bonus=sector_bonus,
                                            sector_etf=sector_etf,
                                            intraday_curve=curve,
                                            is_live=False)
                    except Exception as e:
                        err = str(e)
                        if "forbidden" in err.lower() or "403" in err or "unauthorized" in err.lower():
                            st.error("Authentication failed — check your API Key and Secret Key.")
                        elif "subscription" in err.lower() or "not entitled" in err.lower() or "422" in err:
                            st.error(f"Not subscribed to {data_feed.upper()} feed. Switch to IEX or upgrade your Alpaca plan.")
                        else:
                            st.error(f"Error: {err}")
        else:
            st.info("👈 Enter credentials and settings in the sidebar, then click **Fetch & Analyze**.")
            st.markdown("### How it works")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**📊 Volume Profile**")
                st.markdown("Bins the day's 1-min bars to show where volume concentrated.")
            with c2:
                st.markdown("**🎯 Point of Control**")
                st.markdown("Gold line — the price level with the highest volume. Price gravitates here.")
            with c3:
                st.markdown("**📐 Initial Balance**")
                st.markdown("9:30–10:30 EST High/Low — the key reference range for day structure.")

    # ── Live mode ──────────────────────────────────────────────────────────────
    else:
        if start_live:
            if not api_key or not secret_key:
                st.error("Enter your Alpaca credentials first.")
            elif not ticker:
                st.error("Enter a ticker symbol.")
            else:
                # Pre-fetch RVOL baseline and sector ETF change before starting stream
                today = date.today()
                with st.spinner("Computing RVOL baseline & sector data…"):
                    try:
                        avg_vol = fetch_avg_daily_volume(api_key, secret_key, ticker, today)
                    except Exception:
                        avg_vol = None
                    st.session_state.rvol_avg_vol = avg_vol

                    try:
                        curve = build_rvol_intraday_curve(
                            api_key, secret_key, ticker, today,
                            lookback_days=5, feed=live_feed)
                    except Exception:
                        curve = None
                    st.session_state.rvol_intraday_curve = curve

                    try:
                        etf_chg = fetch_etf_pct_change(
                            api_key, secret_key, sector_etf, today, feed=live_feed)
                    except Exception:
                        etf_chg = 0.0
                    st.session_state.sector_pct_chg = etf_chg

                start_stream(api_key, secret_key, ticker, live_feed)
                st.rerun()

        if stop_live:
            stop_stream()
            st.rerun()

        if st.session_state.live_error:
            err = st.session_state.live_error
            if "forbidden" in err.lower() or "unauthorized" in err.lower():
                st.error(f"Auth error: {err}. Check your API credentials.")
            elif "subscription" in err.lower() or "entitled" in err.lower():
                st.error("Subscription error: SIP requires an Alpaca data plan. Switch to IEX.")
            else:
                st.error(f"Stream error: {err}")

        if st.session_state.live_active:
            drain_queue()
            df = build_live_df()
            now_est = datetime.now(EASTERN)
            is_ib_live = now_est.time() <= dtime(10, 30)

            if df.empty:
                if now_est.time() < dtime(9, 30):
                    mins = int((datetime.combine(date.today(), dtime(9, 30)) -
                                now_est.replace(tzinfo=None)).total_seconds() // 60)
                    st.info(f"⏳ Market opens in ~{mins} min (9:30 AM EST). WebSocket connected, waiting...")
                elif now_est.time() > dtime(16, 0):
                    st.warning("Market is closed. No new data will arrive until tomorrow's session.")
                else:
                    st.info(f"🔌 Connected. Waiting for first trade on **{st.session_state.live_ticker}**... "
                            f"({now_est.strftime('%H:%M:%S')} EST)")
            else:
                chart_title = (f"🔴 LIVE — {st.session_state.live_ticker} | "
                               f"{now_est.strftime('%H:%M:%S')} EST"
                               + (" | 📐 IB FORMING" if is_ib_live else ""))
                live_sector_bonus = (10.0
                                     if st.session_state.get("sector_pct_chg", 0.0) > 1.0
                                     else 0.0)
                render_analysis(df, num_bins, st.session_state.live_ticker,
                                chart_title, is_ib_live=is_ib_live,
                                avg_daily_vol=st.session_state.get("rvol_avg_vol"),
                                sector_bonus=live_sector_bonus,
                                sector_etf=sector_etf,
                                intraday_curve=st.session_state.get("rvol_intraday_curve"),
                                is_live=True)
        else:
            if not st.session_state.live_error:
                st.info("👈 Enter credentials and ticker, then click **▶ Start Live Stream**.")
                st.markdown("### What Live Mode does")
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.markdown("**🔌 WebSocket Feed**")
                    st.markdown("Subscribes to real-time trades + 1-min bars from Alpaca.")
                with c2:
                    st.markdown("**📐 Dynamic IB**")
                    st.markdown("IB High/Low lines expand in real time from 9:30–10:30 AM EST.")
                with c3:
                    st.markdown("**⚡ Vol Velocity**")
                    st.markdown("Compares recent vol/min to prior bars — early breakout signal.")
                with c4:
                    st.markdown("**🎯 Probabilities**")
                    st.markdown("Every structure type scored continuously as the tape develops.")

# ── Auto-refresh loop for live mode ───────────────────────────────────────────
if mode == "🔴 Live Stream" and st.session_state.live_active:
    time.sleep(2)
    st.rerun()
