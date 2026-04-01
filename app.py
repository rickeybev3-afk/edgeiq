import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, date, timedelta, time as dtime
import pytz
import threading
import queue
import time
import json
import csv
import os
import math
from collections import deque

STATE_FILE   = "trade_state.json"
TRACKER_FILE = "accuracy_tracker.csv"
WEIGHTS_FILE = "brain_weights.json"   # adaptive per-structure multipliers
HICONS_FILE  = "high_conviction_log.csv"   # tickers where top prob ≥ 75%
HICONS_THRESHOLD = 75.0                    # % above which we flag high conviction

# ── Structures the Brain tracks — maps display keyword → weight key ────────────
_BRAIN_WEIGHT_KEYS = [
    "trend_bull", "trend_bear", "double_dist",
    "non_trend",  "normal",     "neutral",
    "ntrl_extreme", "nrml_variation",
]
_RECALIBRATE_EVERY = 10   # re-learn every N new comparison rows

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
    # Last analysis snapshot — used by Live Pulse header + Log Entry
    "last_analysis_state": None,
    # Trade journal active tab state
    "active_tab": 0,
    # Position tracking
    "position_in":          False,
    "position_avg_entry":   0.0,
    "position_peak_price":  0.0,
    "position_ticker":      "",
    "position_shares":      0,
    "position_structure":   "",
    # MarketBrain — stores the live predicted structure between reruns
    "brain_session_correct":  0,    # correct predictions this session
    "brain_session_total":    0,    # total comparisons this session
    "brain_last_compared":    "",   # "TICKER_YYYY-MM-DD" — dedup key
    "brain_predicted":        None,
    "brain_ib_high":        0.0,
    "brain_ib_low":         float("inf"),
    "brain_ib_set":         False,
    "brain_high_touched":   False,
    "brain_low_touched":    False,
    # Replay mode
    "replay_bars":          None,   # full-day DataFrame (all bars)
    "replay_bar_idx":       0,      # index of current visible bar (0-based)
    "replay_playing":       False,  # auto-advance in progress
    "replay_speed":         1,      # bars advanced per step
    "replay_ticker":        "",
    "replay_date":          None,
    "replay_avg_vol":       None,
    "replay_intraday_curve": None,
    "replay_sector_bonus":  0.0,
    # Daily level confluence cache
    "daily_levels_cache":   {},
    # StockTwits sentiment cache — keyed by ticker, refreshed per user analysis trigger
    # (prevents repeated API calls during Live/Replay auto-reruns)
    "stocktwits_cache":     {},
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Restore today's brain accuracy counters from CSV on first load ─────────────
# Uses load_accuracy_tracker() so the CSV migration runs first, ensuring
# pandas always sees a clean, uniform column layout.
if st.session_state.brain_session_total == 0 and os.path.exists(TRACKER_FILE):
    try:
        _migrate_tracker_csv()   # fix column mismatch before reading
        _restore_df = pd.read_csv(TRACKER_FILE, encoding="utf-8")
        if "timestamp" in _restore_df.columns and not _restore_df.empty:
            _today_str = datetime.now(EASTERN).strftime("%Y-%m-%d")
            _today_rows = _restore_df[
                _restore_df["timestamp"].astype(str).str.startswith(_today_str)
            ]
            if not _today_rows.empty and "correct" in _today_rows.columns:
                _r_total   = len(_today_rows)
                _r_correct = int((_today_rows["correct"] == "✅").sum())
                st.session_state.brain_session_total   = int(_r_total)
                st.session_state.brain_session_correct = _r_correct
                # Restore last compare_key so we don't re-log on first run
                if "compare_key" in _today_rows.columns:
                    _non_empty = _today_rows["compare_key"].dropna()
                    _non_empty = _non_empty[_non_empty.astype(str).str.strip() != ""]
                    if not _non_empty.empty:
                        st.session_state.brain_last_compared = str(_non_empty.iloc[-1])
    except Exception:
        pass  # safe fallback — counters stay at 0

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

_TARGET_JS = """(function(){
  try{
    var C=new(window.AudioContext||window.webkitAudioContext)();
    [[1174.66,0],[1318.51,0.14],[1568.0,0.28],[1318.51,0.42]].forEach(function(fd){
      var o=C.createOscillator(),g=C.createGain();
      o.type='triangle'; o.frequency.value=fd[0];
      o.connect(g); g.connect(C.destination);
      var t=C.currentTime+fd[1];
      g.gain.setValueAtTime(0.001,t);
      g.gain.linearRampToValueAtTime(0.30,t+0.03);
      g.gain.exponentialRampToValueAtTime(0.001,t+0.55);
      o.start(t); o.stop(t+0.56);
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
    # When fetching today's intraday data cap end to now so the API doesn't
    # get a future end time. If we're before market open, nothing to fetch yet.
    now_et = datetime.now(EASTERN)
    if trade_date >= now_et.date():
        if now_et <= mo:
            return pd.DataFrame()   # pre-market — no bars yet
        if now_et < mc:
            mc = now_et             # mid-session — cap end to current time
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


def compute_atr(df, period=14):
    """Average True Range over `period` bars (or full session when fewer bars available)."""
    if df.empty:
        return 0.01
    if len(df) < 2:
        return max(0.01, float(df["high"].iloc[0] - df["low"].iloc[0]))
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return max(0.01, float(tr.rolling(period, min_periods=1).mean().iloc[-1]))


# ══════════════════════════════════════════════════════════════════════════════
# MARKET BRAIN  — real-time IB tracker + structure predictor
# ══════════════════════════════════════════════════════════════════════════════

class MarketBrain:
    """Runs alongside classify_day_structure to predict structure mid-session.

    Call update(df, rvol) on each refresh; read `.prediction` for the live call.
    After the actual structure is classified, call log_accuracy() to record
    Predicted vs Actual in accuracy_tracker.csv.
    """

    _STRUCTURE_COLORS = {
        "Trend Day":            "#ff9800",
        "Double Distribution":  "#00bcd4",
        "Non-Trend":            "#78909c",
        "Normal":               "#66bb6a",
        "Normal Variation":     "#aed581",
        "Neutral":              "#80cbc4",
        "Neutral Extreme":      "#7e57c2",
        "Analyzing IB…":        "#888888",
    }

    def __init__(self):
        self.ib_high        = 0.0
        self.ib_low         = float("inf")
        self.ib_set         = False
        self.high_touched   = False
        self.low_touched    = False
        self.prediction     = "Analyzing IB…"

    # ── Restore from session state so we survive Streamlit reruns ──────────────
    def load_from_session(self):
        self.ib_high      = st.session_state.brain_ib_high
        self.ib_low       = st.session_state.brain_ib_low
        self.ib_set       = st.session_state.brain_ib_set
        self.high_touched = st.session_state.brain_high_touched
        self.low_touched  = st.session_state.brain_low_touched
        self.prediction   = st.session_state.brain_predicted or "Analyzing IB…"

    def save_to_session(self):
        st.session_state.brain_ib_high      = self.ib_high
        st.session_state.brain_ib_low       = self.ib_low
        st.session_state.brain_ib_set       = self.ib_set
        st.session_state.brain_high_touched = self.high_touched
        st.session_state.brain_low_touched  = self.low_touched
        st.session_state.brain_predicted    = self.prediction

    # ── Main update call ───────────────────────────────────────────────────────
    def update(self, df, rvol=None, ib_vol_pct=None, poc_price=None, has_double_dist=False):
        """Ingest fresh bar data, update IB, and re-predict.

        The 7-structure framework (Dalton / volume profile):
        ┌─────────────────────────────────────────────────────────────────────┐
        │  IB interaction          │  Close position    │  Structure          │
        ├──────────────────────────┼────────────────────┼─────────────────────┤
        │  Neither side broken     │  IB was wide       │  Normal             │
        │  Neither side broken     │  IB was narrow/low │  Non-Trend          │
        │  BOTH sides broken       │  Near middle/IB    │  Neutral            │
        │  BOTH sides broken       │  Near day extreme  │  Neutral Extreme    │
        │  ONE side only broken    │  Moderate move     │  Normal Variation   │
        │  ONE side only broken    │  Two vol clusters  │  Double Distribution│
        │  ONE side only broken    │  Dominant/early    │  Trend Day          │
        └─────────────────────────────────────────────────────────────────────┘

        Parameters
        ----------
        df              : OHLCV DataFrame (ET-indexed, may contain NaN reindex rows)
        rvol            : relative volume vs expected; None → 0.0
        ib_vol_pct      : fraction of total session volume traded inside IB (0–1)
        poc_price       : Point of Control from volume profile
        has_double_dist : True when _detect_double_distribution() found two peaks
        """
        if df.empty:
            return
        # Strip NaN rows inserted by the chart reindex grid
        _df = df.dropna(subset=["open", "high", "low", "close"])
        if _df.empty:
            return
        rvol = rvol or 0.0
        ib_end = _df.index[0].replace(hour=10, minute=30, second=0)

        # Accumulate IB extremes over the first hour (9:30–10:30)
        ib_df = _df[_df.index <= ib_end]
        if not ib_df.empty:
            self.ib_high = max(self.ib_high, float(ib_df["high"].max()))
            self.ib_low  = min(self.ib_low,  float(ib_df["low"].min()))

        last_time = _df.index[-1].time()
        if last_time > dtime(10, 30):
            self.ib_set = True

        if self.ib_set and self.ib_high > 0 and self.ib_low < float("inf"):
            current_price = float(_df["close"].iloc[-1])
            day_high      = float(_df["high"].max())
            day_low       = float(_df["low"].min())
            ib_range      = self.ib_high - self.ib_low

            if day_high >= self.ib_high:  self.high_touched = True
            if day_low  <= self.ib_low:   self.low_touched  = True

            # ── IB interaction buckets (the core 3-way split) ─────────────────
            no_break     = not self.high_touched and not self.low_touched
            both_broken  = self.high_touched and self.low_touched
            one_side_up  = self.high_touched and not self.low_touched
            one_side_dn  = self.low_touched  and not self.high_touched
            one_side     = one_side_up or one_side_dn

            # ── Derived signals ───────────────────────────────────────────────
            _ivp            = ib_vol_pct if ib_vol_pct is not None else 0.5
            directional_vol = _ivp < 0.35   # <35% of volume in IB → directional
            balanced_vol    = _ivp > 0.62   # >62% of volume in IB → rotational

            poc_outside_ib  = (poc_price is not None
                               and (poc_price > self.ib_high or poc_price < self.ib_low))

            total_range      = day_high - day_low
            range_expansion  = total_range / ib_range if ib_range > 0 else 1.0

            # Where did price close in today's range? (0.0 = at day low, 1.0 = at day high)
            close_pct        = ((current_price - day_low) / total_range
                                if total_range > 0 else 0.5)
            # "Near extreme" = closing in the top 20% or bottom 20% of day range
            close_at_extreme = close_pct >= 0.80 or close_pct <= 0.20
            # "In the middle" = closing within IB range or close to it
            close_near_ib    = self.ib_low <= current_price <= self.ib_high

            # ── BRANCH 1: Neither IB side violated ───────────────────────────
            # Both Normal and Non-Trend have no break. The difference is IB SIZE:
            #   Normal   → wide IB set by large players early; price stays inside
            #   Non-Trend → narrow IB, no volume/interest (holiday, eve-of-news, etc.)
            if no_break:
                # < 1.5% of price AND balanced vol AND minimal range expansion = Non-Trend
                is_narrow = ib_range < 0.015 * self.ib_high
                if is_narrow and balanced_vol and range_expansion <= 1.25:
                    self.prediction = "Non-Trend"
                else:
                    self.prediction = "Normal"

            # ── BRANCH 2: BOTH IB sides violated → always Neutral family ─────
            # Transcript: "both sides violated → EITHER closes in middle (Neutral)
            # OR one side dominates and closes near an extreme (Neutral Extreme)"
            elif both_broken:
                if close_at_extreme:
                    self.prediction = "Neutral Extreme"
                else:
                    self.prediction = "Neutral"

            # ── BRANCH 3: ONE side only violated → Trend / Dbl Dist / Nrml Var
            # Transcript: Trend = "pretty much from the open, very dominant, ONE side only"
            # Double Dist = two distinct volume clusters; a thin LVN in the middle
            # Normal Variation = one side broken but NOT dominant/early
            else:  # one_side is True
                # Double Distribution: bimodal profile detected OR
                # POC migrated out of IB but IB still has meaningful volume
                # (volume stayed in 2 places, not fully directional)
                is_double = has_double_dist or (poc_outside_ib and not directional_vol)

                # Trend: POC fully migrated + all volume directional, OR
                # strong early break + close firmly at the extreme
                is_trend = (
                    (poc_outside_ib and directional_vol)
                    or (close_at_extreme and range_expansion >= 2.0)
                    or (close_at_extreme and rvol >= 2.0)
                    or (close_at_extreme and directional_vol)
                )

                if is_trend:
                    self.prediction = "Trend Day"
                elif is_double:
                    self.prediction = "Double Distribution"
                else:
                    self.prediction = "Normal Variation"
        else:
            self.prediction = "Analyzing IB…"

        self.save_to_session()

    def color(self):
        return self._STRUCTURE_COLORS.get(self.prediction, "#888")


# ── Accuracy tracker persistence ──────────────────────────────────────────────

def _migrate_tracker_csv():
    """One-time fix: rebuild the CSV so every row has the same columns.

    The file may have an 8-column header (pre-compare_key) with some rows
    that have 9 values — pandas chokes on this.  We read line-by-line with
    csv.reader (which never errors on column count mismatches), normalise
    every row to the full column set, then rewrite the file cleanly.
    """
    if not os.path.exists(TRACKER_FILE):
        return
    full_cols = ["timestamp", "symbol", "predicted", "actual", "correct",
                 "entry_price", "exit_price", "mfe", "compare_key"]
    try:
        rows = []
        with open(TRACKER_FILE, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            raw_header = next(reader, None)
            if raw_header is None:
                return
            # Add compare_key to old header if missing
            if "compare_key" not in raw_header:
                header = raw_header + ["compare_key"]
            else:
                header = raw_header
            for row in reader:
                # Pad short rows, truncate long rows to header length
                while len(row) < len(header):
                    row.append("")
                rows.append(row[:len(header)])

        # Re-map any missing full_cols
        h_idx = {c: i for i, c in enumerate(header)}
        out_rows = []
        for row in rows:
            out = [row[h_idx[c]] if c in h_idx else "" for c in full_cols]
            out_rows.append(out)

        with open(TRACKER_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(full_cols)
            writer.writerows(out_rows)
    except Exception:
        pass   # leave file untouched on any error


def load_accuracy_tracker():
    """Return a DataFrame from accuracy_tracker.csv (or empty if none)."""
    cols = ["timestamp", "symbol", "predicted", "actual", "correct",
            "entry_price", "exit_price", "mfe", "compare_key"]
    if not os.path.exists(TRACKER_FILE):
        return pd.DataFrame(columns=cols)
    # Ensure the file has a consistent column structure before pandas reads it
    _migrate_tracker_csv()
    try:
        df = pd.read_csv(TRACKER_FILE, encoding="utf-8")
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        return df
    except Exception:
        # Last-resort: row-by-row manual parse
        try:
            rows = []
            with open(TRACKER_FILE, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append({c: row.get(c, "") for c in cols})
            return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
        except Exception:
            return pd.DataFrame(columns=cols)


def log_accuracy_entry(symbol, predicted, actual, compare_key="",
                       entry_price=0.0, exit_price=0.0, mfe=0.0):
    """Append one Predicted vs Actual row to accuracy_tracker.csv.

    compare_key is stored so dedup checks survive page reloads.
    """
    correct = "✅" if _strip_emoji(predicted) in _strip_emoji(actual) or \
                     _strip_emoji(actual) in _strip_emoji(predicted) else "❌"
    file_exists = os.path.isfile(TRACKER_FILE)
    with open(TRACKER_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["timestamp", "symbol", "predicted", "actual", "correct",
                        "entry_price", "exit_price", "mfe", "compare_key"])
        w.writerow([datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S"),
                    symbol, predicted, actual, correct,
                    round(entry_price, 4), round(exit_price, 4), round(mfe, 4),
                    compare_key])

    # ── Adaptive learning: recalibrate every N entries ────────────────────────
    try:
        _n_rows = sum(1 for _ in open(TRACKER_FILE)) - 1  # subtract header
        if _n_rows > 0 and _n_rows % _RECALIBRATE_EVERY == 0:
            recalibrate_brain_weights()
    except Exception:
        pass


def log_high_conviction(ticker, trade_date, structure, prob,
                        ib_high=None, ib_low=None, poc_price=None):
    """Append a row to high_conviction_log.csv when top prob ≥ HICONS_THRESHOLD.

    Deduplication: one row per ticker+date combination — existing row is
    updated (overwritten) if prob is higher than what was previously recorded.
    """
    _cols = ["timestamp", "ticker", "date", "structure", "prob_pct",
             "ib_high", "ib_low", "poc_price"]
    _row  = {
        "timestamp": datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S"),
        "ticker":    ticker,
        "date":      str(trade_date),
        "structure": structure,
        "prob_pct":  round(prob, 1),
        "ib_high":   round(ib_high, 4) if ib_high else "",
        "ib_low":    round(ib_low, 4)  if ib_low  else "",
        "poc_price": round(poc_price, 4) if poc_price else "",
    }
    # Load existing, drop any previous row for same ticker+date, then append
    if os.path.exists(HICONS_FILE):
        try:
            _df = pd.read_csv(HICONS_FILE, encoding="utf-8")
            _mask = ~((_df["ticker"] == ticker) & (_df["date"] == str(trade_date)))
            _df = _df[_mask]
        except Exception:
            _df = pd.DataFrame(columns=_cols)
    else:
        _df = pd.DataFrame(columns=_cols)
    _new = pd.concat([_df, pd.DataFrame([_row])], ignore_index=True)
    _new.to_csv(HICONS_FILE, index=False, encoding="utf-8")


def load_high_conviction_log():
    """Return the high conviction log as a DataFrame, newest entries first."""
    if not os.path.exists(HICONS_FILE):
        return pd.DataFrame()
    try:
        _df = pd.read_csv(HICONS_FILE, encoding="utf-8")
        if _df.empty:
            return _df
        return _df.sort_values("prob_pct", ascending=False).reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _strip_emoji(s):
    """Rough emoji stripper for fuzzy structure matching."""
    import re
    return re.sub(r"[^\w\s/()]", "", str(s)).strip().lower()


# ══════════════════════════════════════════════════════════════════════════════
# ADAPTIVE BRAIN LEARNING — per-structure accuracy weights
# ══════════════════════════════════════════════════════════════════════════════

def _label_to_weight_key(label: str) -> str:
    """Map a raw structure label to one of the canonical weight keys."""
    s = label.lower()
    if "bear" in s or "down" in s:          return "trend_bear"
    if "trend" in s:                         return "trend_bull"
    if "double" in s or "dbl" in s:         return "double_dist"
    if "non" in s:                           return "non_trend"
    if "variation" in s or "var" in s:       return "nrml_variation"
    if "extreme" in s:                       return "ntrl_extreme"
    if "neutral" in s:                       return "neutral"
    if "normal" in s or "balance" in s:      return "normal"
    return "normal"   # safe default


def load_brain_weights() -> dict:
    """Load adaptive calibration weights from disk (defaults to 1.0 for all)."""
    defaults = {k: 1.0 for k in _BRAIN_WEIGHT_KEYS}
    if not os.path.exists(WEIGHTS_FILE):
        return defaults
    try:
        import json
        with open(WEIGHTS_FILE) as f:
            stored = json.load(f)
        # Merge: keep stored values, fill any missing keys with 1.0
        return {k: float(stored.get(k, 1.0)) for k in _BRAIN_WEIGHT_KEYS}
    except Exception:
        return defaults


def _save_brain_weights(weights: dict) -> None:
    import json
    with open(WEIGHTS_FILE, "w") as f:
        json.dump({k: round(float(v), 4) for k, v in weights.items()}, f, indent=2)


def recalibrate_brain_weights() -> dict:
    """Read the accuracy tracker, compute per-structure accuracy, and update weights.

    Learning rule (smoothed exponential moving average):
      target = 1.5 if acc ≥ 70% | 1.0 if 50-70% | 0.75 if 30-50% | 0.5 if < 30%
      new_weight = old_weight × 0.70  +  target × 0.30   (30% learning rate)

    Structures with fewer than 5 samples are left unchanged (avoid overfitting).
    Returns the updated weights dict.
    """
    weights = load_brain_weights()
    if not os.path.exists(TRACKER_FILE):
        return weights
    try:
        df = pd.read_csv(TRACKER_FILE)
        if df.empty or "predicted" not in df.columns or "correct" not in df.columns:
            return weights

        # Group by predicted structure
        for raw_label, grp in df.groupby("predicted"):
            if len(grp) < 5:
                continue   # too few samples — skip
            acc = (grp["correct"] == "✅").sum() / len(grp)
            wk  = _label_to_weight_key(str(raw_label))

            # Target weight based on accuracy band
            if acc >= 0.70:   target = 1.50
            elif acc >= 0.50: target = 1.00
            elif acc >= 0.30: target = 0.75
            else:             target = 0.50

            # Smooth update (EMA-style, 30% learning rate)
            old = weights.get(wk, 1.0)
            weights[wk] = round(old * 0.70 + target * 0.30, 4)

        _save_brain_weights(weights)
    except Exception:
        pass
    return weights


def brain_weights_summary() -> list[dict]:
    """Return a list of dicts for displaying the learned weight table."""
    weights  = load_brain_weights()
    if not os.path.exists(TRACKER_FILE):
        return []
    try:
        df = pd.read_csv(TRACKER_FILE)
        if df.empty or "predicted" not in df.columns:
            return []
        rows = []
        for raw_label, grp in df.groupby("predicted"):
            wk   = _label_to_weight_key(str(raw_label))
            n    = len(grp)
            acc  = (grp["correct"] == "✅").sum() / n if n > 0 else 0
            w    = weights.get(wk, 1.0)
            rows.append({
                "Structure":  raw_label,
                "Samples":    n,
                "Accuracy":   round(acc * 100, 1),
                "Multiplier": w,
                "Status": ("✅ Trusted" if w >= 1.3 else
                           "🟢 Good"    if w >= 1.0 else
                           "🟡 Reduced" if w >= 0.7 else
                           "🔴 Low Confidence"),
            })
        rows.sort(key=lambda r: r["Multiplier"], reverse=True)
        return rows
    except Exception:
        return []


# ── Position state persistence ────────────────────────────────────────────────

def load_position_state():
    """Load persisted position from trade_state.json into session state."""
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        for k in ("position_in", "position_avg_entry", "position_peak_price",
                  "position_ticker", "position_shares", "position_structure"):
            if k in data:
                st.session_state[k] = data[k]
    except Exception:
        pass


def save_position_state():
    """Persist current position session state to trade_state.json."""
    data = {k: st.session_state.get(k)
            for k in ("position_in", "position_avg_entry", "position_peak_price",
                      "position_ticker", "position_shares", "position_structure")}
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)


def enter_position(ticker, avg_entry, shares, structure):
    st.session_state.position_in        = True
    st.session_state.position_avg_entry = float(avg_entry)
    st.session_state.position_peak_price = float(avg_entry)
    st.session_state.position_ticker    = ticker
    st.session_state.position_shares    = int(shares)
    st.session_state.position_structure = structure
    save_position_state()


def exit_position(exit_price, actual_structure=""):
    """Record the exit, log to accuracy tracker, clear position."""
    entry   = st.session_state.position_avg_entry
    mfe     = st.session_state.position_peak_price
    sym     = st.session_state.position_ticker
    pred    = st.session_state.position_structure
    shares  = st.session_state.position_shares
    if entry > 0:
        log_accuracy_entry(sym, pred, actual_structure or pred,
                           entry_price=entry, exit_price=float(exit_price), mfe=mfe)
    st.session_state.position_in        = False
    st.session_state.position_avg_entry = 0.0
    st.session_state.position_peak_price = 0.0
    st.session_state.position_ticker    = ""
    st.session_state.position_shares    = 0
    st.session_state.position_structure = ""
    save_position_state()
    pnl = (float(exit_price) - entry) * shares if shares > 0 else 0
    return pnl


# Load persisted position on startup (only runs once per session via default init)
if not st.session_state.get("_position_loaded"):
    load_position_state()
    st.session_state["_position_loaded"] = True


def compute_ib_volume_stats(df, ib_high, ib_low):
    """Return (ib_vol_pct, ib_range_ratio) — both in [0, 1].

    ib_vol_pct  : fraction of total session volume traded while close was inside [ib_low, ib_high]
    ib_range_ratio : IB range / day range  — how much of the day was captured in the opening hour
    """
    if df.empty or ib_high is None or ib_low is None:
        return 0.5, 0.5
    total_vol = float(df["volume"].sum())
    if total_vol <= 0:
        return 0.5, 0.5
    inside_mask = (df["close"] >= ib_low) & (df["close"] <= ib_high)
    ib_vol  = float(df.loc[inside_mask, "volume"].sum())
    ib_vol_pct = ib_vol / total_vol
    day_range = float(df["high"].max()) - float(df["low"].min())
    ib_range  = ib_high - ib_low
    ib_range_ratio = (ib_range / day_range) if day_range > 0 else 0.5
    return round(ib_vol_pct, 3), round(ib_range_ratio, 3)


def classify_day_structure(df, bin_centers, vap, ib_high, ib_low, poc_price,
                           avg_daily_vol=None):
    """7-structure classification using the exact IB-interaction decision tree.

    Decision tree (mirrors the video framework):
      1. Double Distribution  — bimodal volume profile (always wins if detected)
      2. No IB break          — Normal (wide IB) or Non-Trend (narrow/low-vol IB)
      3. Both sides broken    — Neutral Extreme (close at day extreme) or Neutral
      4. One side only broken — Trend Day (dominant early move) or Normal Variation

    Returns (label, color, detail, insight).
    """
    day_high    = float(df["high"].max())
    day_low     = float(df["low"].min())
    total_range = day_high - day_low
    ib_range    = (ib_high - ib_low) if (ib_high is not None and ib_low is not None) else 0.0
    final_price = float(df["close"].iloc[-1])

    ib_vol_pct, ib_range_ratio = compute_ib_volume_stats(df, ib_high, ib_low)

    if total_range == 0 or ib_range == 0:
        return ("⚖️ Normal / Balanced", "#66bb6a",
                "Insufficient range data.",
                "Not enough price movement to classify structure reliably.")

    atr = compute_atr(df)

    # IB boundary flags
    ib_high_touched = day_high >= ib_high
    ib_low_touched  = day_low  <= ib_low
    both_touched    = ib_high_touched and ib_low_touched
    one_side_up     = ib_high_touched and not ib_low_touched
    one_side_down   = ib_low_touched  and not ib_high_touched
    no_break        = not ib_high_touched and not ib_low_touched

    # Distance of close from IB boundary
    if final_price > ib_high:
        dist_from_ib = final_price - ib_high
    elif final_price < ib_low:
        dist_from_ib = ib_low - final_price
    else:
        dist_from_ib = 0.0

    # Where did the close land in the day's range? (0 = at low, 1 = at high)
    close_pct       = (final_price - day_low) / total_range if total_range > 0 else 0.5
    # "At extreme" = top or bottom 20% of day range
    at_high_extreme = close_pct >= 0.80
    at_low_extreme  = close_pct <= 0.20
    at_extreme      = at_high_extreme or at_low_extreme

    # Early IB violation (first 2 hrs of regular session)
    two_hr_end = df.index[0].replace(hour=11, minute=30)
    early_df   = df[df.index <= two_hr_end]
    early_high = float(early_df["high"].max()) if not early_df.empty else day_high
    early_low  = float(early_df["low"].min())  if not early_df.empty else day_low
    viol_early_up   = early_high > ib_high
    viol_early_down = early_low  < ib_low

    # Directional-volume signal (IB vol% < 0.40 = volume moved outside IB = directional)
    directional_vol  = ib_vol_pct < 0.40 and ib_range_ratio < 0.40
    balanced_vol     = ib_vol_pct > 0.65

    # ── STEP 1: Double Distribution (volume-based, always wins if detected) ───
    dd = _detect_double_distribution(bin_centers, vap)
    if dd is not None:
        pk1, pk2, vi = dd
        sep_price = bin_centers[pk2] - bin_centers[pk1]
        lvn_price = float(bin_centers[vi])
        pct1 = vap[max(0,pk1-2):min(len(vap),pk1+3)].sum() / vap.sum() * 100
        pct2 = vap[max(0,pk2-2):min(len(vap),pk2+3)].sum() / vap.sum() * 100
        detail  = (f"HVNs at ${bin_centers[pk1]:.2f} ({pct1:.0f}% vol) & "
                   f"${bin_centers[pk2]:.2f} ({pct2:.0f}% vol). "
                   f"LVN at ${lvn_price:.2f} (${sep_price:.2f} gap).")
        insight = (f"Two separate auctions detected. LVN at ${lvn_price:.2f} separates the "
                   f"two value areas — expect rapid, high-momentum moves through it. "
                   f"Gap Fill toward the opposing HVN is the primary target.")
        return ("⚡ Double Distribution", "#00bcd4", detail, insight)

    # ── STEP 2: No IB break → Normal or Non-Trend ─────────────────────────────
    if no_break:
        # Non-Trend: narrow IB + low volume interest (holiday, eve-of-news, etc.)
        is_narrow_ib = ib_range < 0.20 * total_range
        total_vol = float(df["volume"].sum())
        if avg_daily_vol and avg_daily_vol > 0:
            pace     = (total_vol / max(1, len(df))) * 390.0
            is_low_vol = (pace / avg_daily_vol) < 0.80
        else:
            is_low_vol = ib_range / max(0.001, day_high) < 0.005
        ib_vol_confirms_nontrend = ib_vol_pct > 0.72 and ib_range_ratio < 0.25
        if is_narrow_ib and (is_low_vol or ib_vol_confirms_nontrend):
            detail  = (f"IB ${ib_range:.2f} = {ib_range/total_range*100:.0f}% of day range. "
                       f"IB volume {ib_vol_pct*100:.0f}% of session total. "
                       f"Volume participation is anemic — no institutional interest.")
            insight = (f"Tight initial balance with {ib_vol_pct*100:.0f}% of session volume "
                       f"inside the opening range signals no institutional interest. "
                       f"Avoid chasing breakouts. Wait for a volume-backed catalyst.")
            return ("😴 Non-Trend", "#78909c", detail, insight)

        # Normal: wide IB set by large players in first hour, never violated
        pct_inside = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean()) * 100
        ib_vol_str = (f"IB absorbed {ib_vol_pct*100:.0f}% of volume — "
                      f"{'strong balance' if ib_vol_pct > 0.60 else 'moderate balance'}.")
        detail  = (f"IB ${ib_high:.2f}–${ib_low:.2f} never violated. "
                   f"Price inside IB for {pct_inside:.0f}% of session. {ib_vol_str}")
        insight = (f"Classic Normal day — large players set a wide range early and left. "
                   f"{ib_vol_pct*100:.0f}% of volume stayed inside the 9:30–10:30 range. "
                   f"No directional conviction. Fade the extremes and target POC ${poc_price:.2f}.")
        return ("⚖️ Normal", "#66bb6a", detail, insight)

    # ── STEP 3: BOTH sides broken → always Neutral family ─────────────────────
    # Per the video: "both sides violated" means EITHER:
    #   • Neutral Extreme: one side ultimately dominated, close near the day extreme
    #   • Neutral: coast-to-coast but closes back in the middle area
    if both_touched:
        if at_extreme:
            side        = "high" if at_high_extreme else "low"
            extreme_lvl = ib_high if at_high_extreme else ib_low
            detail  = (f"Both IB extremes tested. Price closing at day's {side} "
                       f"(${final_price:.2f}, top {close_pct*100:.0f}% of range) — "
                       f"late-session dominance confirmed.")
            insight = (f"Both sides of the IB were probed, then one side took over. "
                       f"Late-session conviction pushed the close to the "
                       f"{'top' if at_high_extreme else 'bottom'} 20% of the day range. "
                       f"This pattern frequently resolves with a "
                       f"{'gap up' if at_high_extreme else 'gap down'} next morning. "
                       f"Key level: ${extreme_lvl:.2f}.")
            return ("⚡ Neutral Extreme", "#7e57c2", detail, insight)
        else:
            # Closes anywhere that is NOT at the extreme = Neutral
            # (back inside IB, between IB and extreme band, or middle of range)
            pct_inside = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean()) * 100
            detail  = (f"Both IB extremes tested. Close at ${final_price:.2f} "
                       f"({close_pct*100:.0f}% of day range) — neither side dominated.")
            insight = (f"Coast-to-coast action with no winner — a classic Neutral day. "
                       f"Large players on both sides active but not far off on value. "
                       f"Price gravitates back toward POC ${poc_price:.2f}. "
                       f"Fade the extremes; avoid chasing direction into the close.")
            return ("🔄 Neutral", "#80cbc4", detail, insight)

    # ── STEP 4: ONE side only broken → Trend Day or Normal Variation ──────────
    # Per the video: Trend = "dominated from pretty much the open, only one side violated"
    # Normal Variation = one side breached but NOT dominant/sustained
    bullish = one_side_up
    dist_atr = dist_from_ib / atr if atr > 0 else 0

    # Trend Day: early violation + close firmly outside IB + directional volume OR 2× ATR move
    is_trend = (
        ((viol_early_up   and at_high_extreme and bullish) or
         (viol_early_down and at_low_extreme  and not bullish))
        and (dist_from_ib > 1.0 * atr or directional_vol)
    )

    if is_trend:
        direction = "Bullish" if bullish else "Bearish"
        confirmed = " ✅ IB vol confirms" if directional_vol else ""
        detail  = (f"{direction} Trend — IB {'High' if bullish else 'Low'} violated early, "
                   f"price {dist_atr:.1f}× ATR outside IB. "
                   f"{ib_vol_pct*100:.0f}% of volume inside IB — directional flow.{confirmed}")
        insight = (f"Strong directional conviction from the open — only ONE IB side ever touched. "
                   f"{'Buyers' if bullish else 'Sellers'} dominated all session. "
                   f"Trend continuation is the high-probability path. "
                   f"Add on pullbacks to POC ${poc_price:.2f}; avoid fading.")
        lbl = "📈 Trend Day" if bullish else "📉 Trend Day (Bear)"
        return (lbl, "#ff9800", detail, insight)

    # Normal Variation: one side broken, but not a full trend
    direction = "Up" if bullish else "Down"
    detail  = (f"IB {'High' if bullish else 'Low'} "
               f"${ib_high if bullish else ib_low:.2f} breached; "
               f"opposite side ${ib_low if bullish else ib_high:.2f} held. "
               f"Close at ${final_price:.2f} ({close_pct*100:.0f}% of range).")
    insight = (f"{'Buyers' if bullish else 'Sellers'} pushed outside the opening range "
               f"but didn't sustain a full trend. "
               f"New value area forming {'above' if bullish else 'below'} "
               f"${ib_high if bullish else ib_low:.2f}. "
               f"Watch for acceptance or rejection at that level.")
    return (f"📊 Normal Variation ({direction})", "#aed581" if bullish else "#ffab91",
            detail, insight)


def compute_structure_probabilities(df, bin_centers, vap, ib_high, ib_low, poc_price):
    """Score each of the 7 structures using the same IB-interaction decision tree
    as classify_day_structure.  Scores are converted to percentages at the end.

    Key invariant (mirrors the video framework):
      • no_break       → only Normal / Non-Trend get high scores
      • both_hit       → only Neutral / Neutral Extreme get high scores
      • one_side_only  → only Trend / Normal Variation / Dbl Dist get high scores
    """
    day_high    = float(df["high"].max())
    day_low     = float(df["low"].min())
    total_range = day_high - day_low
    ib_range    = (ib_high - ib_low) if (ib_high is not None and ib_low is not None) else 0.0
    final_price = float(df["close"].iloc[-1])
    fallback    = {"Non-Trend": 14.0, "Normal": 14.0, "Trend": 14.0,
                   "Ntrl Extreme": 14.0, "Neutral": 14.0, "Nrml Var": 15.0, "Dbl Dist": 15.0}
    if total_range == 0 or ib_range == 0:
        return fallback

    ib_vol_pct, ib_range_ratio = compute_ib_volume_stats(df, ib_high, ib_low)

    rr          = total_range / ib_range
    pct_inside  = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean())

    # IB boundary state (the core 3-way split)
    ib_high_hit  = day_high >= ib_high
    ib_low_hit   = day_low  <= ib_low
    both_hit     = ib_high_hit and ib_low_hit
    one_side     = ib_high_hit ^ ib_low_hit   # XOR: exactly one side broken
    no_break     = not ib_high_hit and not ib_low_hit

    atr = compute_atr(df)
    if final_price > ib_high:
        dist_ib = final_price - ib_high
    elif final_price < ib_low:
        dist_ib = ib_low - final_price
    else:
        dist_ib = 0.0

    # Close position in day range (0 = at low, 1 = at high)
    close_pct   = (final_price - day_low) / total_range if total_range > 0 else 0.5
    at_extreme  = close_pct >= 0.80 or close_pct <= 0.20   # top/bottom 20% of range

    # Early IB violation — only meaningful for one-side-only days
    two_hr_end  = df.index[0].replace(hour=11, minute=30)
    early_df    = df[df.index <= two_hr_end]
    early_high  = float(early_df["high"].max()) if not early_df.empty else day_high
    early_low   = float(early_df["low"].min())  if not early_df.empty else day_low
    viol_early  = (early_high > ib_high) or (early_low < ib_low)

    directional_vol = ib_vol_pct < 0.40 and ib_range_ratio < 0.40
    has_dd          = _detect_double_distribution(bin_centers, vap) is not None

    # ── Volume multipliers ────────────────────────────────────────────────────
    ib_balance_boost = max(0.5, ib_vol_pct * 2.0)           # high → balanced day
    ib_trend_boost   = max(0.5, (1.0 - ib_vol_pct) * 2.0)  # low  → directional day

    # ── Scores gated by IB-interaction bucket ─────────────────────────────────
    # Non-Trend / Normal → only score when no IB break
    if no_break:
        is_narrow = ib_range < 0.20 * total_range
        s_nontrend = max(2.0, (1.0 - rr) * 40.0 * ib_balance_boost) if is_narrow else 2.0
        s_normal   = (5.0 + pct_inside * 60.0) * ib_balance_boost
    else:
        s_nontrend = 2.0
        s_normal   = 2.0

    # Neutral / Neutral Extreme → only score when BOTH sides broken
    if both_hit:
        s_ntrl_extreme = 70.0 if at_extreme else 4.0
        s_neutral      = 4.0  if at_extreme else 70.0
    else:
        s_ntrl_extreme = 2.0
        s_neutral      = 2.0

    # Trend / Normal Variation / Dbl Dist → only score when ONE side broken
    if one_side:
        # Trend: early break, close at extreme, directional volume
        trend_strength = 5.0 + max(0.0, (dist_ib / max(atr, 0.01) - 1.0) * 25.0)
        is_trend_day   = viol_early and at_extreme
        s_trend   = trend_strength * ib_trend_boost if is_trend_day else 4.0
        s_nrml_var= 4.0 if is_trend_day else (40.0 * (0.7 + 0.6 * (1.0 - ib_vol_pct)))
        s_dbl_dist= 70.0 if has_dd else 4.0
    else:
        s_trend    = 2.0
        s_nrml_var = 2.0
        s_dbl_dist = 70.0 if has_dd else 2.0   # DD can still override on both-hit days

    scores = {
        "Non-Trend":    s_nontrend,
        "Normal":       s_normal,
        "Trend":        s_trend,
        "Ntrl Extreme": s_ntrl_extreme,
        "Neutral":      s_neutral,
        "Nrml Var":     s_nrml_var,
        "Dbl Dist":     s_dbl_dist,
    }

    # ── Apply adaptive learned weights ─────────────────────────────────────────
    # Maps probability-engine keys → canonical weight keys
    _score_to_wkey = {
        "Non-Trend":    "non_trend",
        "Normal":       "normal",
        "Trend":        "trend_bull",
        "Ntrl Extreme": "ntrl_extreme",
        "Neutral":      "neutral",
        "Nrml Var":     "nrml_variation",
        "Dbl Dist":     "double_dist",
    }
    try:
        _w = load_brain_weights()
        scores = {k: v * _w.get(_score_to_wkey.get(k, "normal"), 1.0)
                  for k, v in scores.items()}
    except Exception:
        pass   # weights unavailable — use raw scores

    total = sum(scores.values())
    return {k: round(v / total * 100, 1) for k, v in scores.items()}


def fetch_avg_daily_volume(api_key, secret_key, ticker, trade_date, lookback_days=50):
    """Return the average total daily volume for ticker over the last N trading days before trade_date.
    Default is 50 days to provide a robust, statistically stable baseline."""
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
                               lookback_days=50, feed="iex"):
    """Build a 390-element list of average cumulative volume at each minute from open.

    Each element i represents the expected cumulative volume after (i+1) minutes of
    trading, averaged across the last lookback_days sessions before trade_date.
    Uses 50-day default for a statistically robust baseline.
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


def compute_buy_sell_pressure(df,
                               lookback_len=10,
                               baseline_weight=0.5,
                               sell_pct_floor=0.0,
                               sell_pct_ceiling=1.0):
    """Estimate session-cumulative buy vs sell volume using the blended CLV+Tick method.

    Mirrors the ThinkScript Blended split formula:
        sellPctCLV  = (high − close) / (high − low)     ← close location value
        sellPctTick = 1 if close < close[1]              ← up/down tick
                      0 if close > close[1]
                      0.5 otherwise
        sellPctRaw      = (sellPctCLV + sellPctTick) / 2
        sellPctBaseline = rolling mean of sellPctRaw over lookback_len bars
        sellPctBlended  = (1−baseline_weight)×sellPctRaw + baseline_weight×sellPctBaseline
        sellPct         = clamp(sellPctBlended, floor, ceiling)
        buyPct          = 1 − sellPct

    Momentum compares last 5 bars vs prior 5 bars (RSI-style ramping detection).
    Returns dict with keys: buy_pct, sell_pct, trend_now, trend_prev,
                            total_buy, total_sell — or None if insufficient data.
    """
    if df.empty or len(df) < 2:
        return None
    _df = df.dropna(subset=["open", "high", "low", "close", "volume"]).copy()
    if len(_df) < 2:
        return None

    # ── CLV component ─────────────────────────────────────────────────────────
    hl = (_df["high"] - _df["low"]).replace(0, np.nan)
    sell_pct_clv = (((_df["high"] - _df["close"]) / hl).fillna(0.5)).clip(0, 1)

    # ── Up/Down Tick component ────────────────────────────────────────────────
    close_prev = _df["close"].shift(1)
    sell_pct_tick = np.where(
        _df["close"] < close_prev, 1.0,
        np.where(_df["close"] > close_prev, 0.0, 0.5)
    )
    sell_pct_tick = pd.Series(sell_pct_tick, index=_df.index).fillna(0.5)

    # ── Blend CLV + Tick → apply baseline smoothing → clamp ──────────────────
    sell_pct_raw      = (sell_pct_clv + sell_pct_tick) / 2.0
    sell_pct_baseline = sell_pct_raw.rolling(window=max(1, lookback_len),
                                              min_periods=1).mean()
    sell_pct_blended  = ((1.0 - baseline_weight) * sell_pct_raw
                         + baseline_weight * sell_pct_baseline)
    sell_pct          = sell_pct_blended.clip(sell_pct_floor, sell_pct_ceiling)
    buy_pct_series    = 1.0 - sell_pct

    _df["buy_vol"]  = _df["volume"] * buy_pct_series
    _df["sell_vol"] = _df["volume"] * sell_pct

    total_buy  = float(_df["buy_vol"].sum())
    total_sell = float(_df["sell_vol"].sum())
    total_vol  = total_buy + total_sell
    if total_vol == 0:
        return None

    buy_pct_session = total_buy / total_vol * 100.0

    def _pct(sub):
        b = float(sub["buy_vol"].sum())
        s = float(sub["sell_vol"].sum())
        return b / (b + s) * 100.0 if (b + s) > 0 else 50.0

    # Momentum: last 5 bars vs prior 5 bars
    recent5    = _df.tail(5)
    prior5     = _df.iloc[-10:-5] if len(_df) >= 10 else _df.head(max(1, len(_df) // 2))
    trend_now  = _pct(recent5)
    trend_prev = _pct(prior5)

    return {
        "buy_pct":    round(buy_pct_session, 1),
        "sell_pct":   round(100.0 - buy_pct_session, 1),
        "trend_now":  round(trend_now, 1),
        "trend_prev": round(trend_prev, 1),
        "total_buy":  total_buy,
        "total_sell": total_sell,
    }


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


# ── Round Number Magnet ────────────────────────────────────────────────────────

def compute_round_number_magnetism(price):
    """Round Number Magnet score (0-100).

    Whole dollars and half dollars act as psychological magnets for retail
    traders — price often stalls or reverses at these levels.

    Returns a dict:
        whole_dollar, half_dollar, dist_whole_pct, dist_half_pct,
        score (0-100, higher = price closer to a magnet),
        badge ("Strong" / "Moderate" / "Weak" / None),
        at_ceiling (bool — price within 0.5% below a whole dollar)
    """
    if price is None or price <= 0:
        return {"score": 0, "badge": None, "at_ceiling": False,
                "whole_dollar": None, "half_dollar": None,
                "dist_whole_pct": None, "dist_half_pct": None}

    price = float(price)

    # Nearest whole dollar
    whole_lo = math.floor(price)
    whole_hi = whole_lo + 1
    dist_whole_lo = abs(price - whole_lo)
    dist_whole_hi = abs(price - whole_hi)
    dist_whole = min(dist_whole_lo, dist_whole_hi)
    nearest_whole = whole_lo if dist_whole_lo <= dist_whole_hi else whole_hi
    dist_whole_pct = dist_whole / price * 100.0

    # Nearest half dollar (x.00 or x.50)
    half_lo = math.floor(price * 2) / 2.0
    half_hi = half_lo + 0.5
    dist_half_lo = abs(price - half_lo)
    dist_half_hi = abs(price - half_hi)
    dist_half = min(dist_half_lo, dist_half_hi)
    nearest_half = half_lo if dist_half_lo <= dist_half_hi else half_hi
    dist_half_pct = dist_half / price * 100.0

    best_dist_pct = min(dist_whole_pct, dist_half_pct)
    if best_dist_pct <= 0.25:
        score = 100
    elif best_dist_pct <= 0.5:
        score = int(80 + (0.5 - best_dist_pct) / 0.25 * 20)
    elif best_dist_pct <= 1.0:
        score = int(50 + (1.0 - best_dist_pct) / 0.5 * 30)
    elif best_dist_pct <= 2.0:
        score = int(20 + (2.0 - best_dist_pct) / 1.0 * 30)
    else:
        score = 0

    if score >= 80:
        badge = "Strong"
    elif score >= 50:
        badge = "Moderate"
    elif score >= 20:
        badge = "Weak"
    else:
        badge = None

    # Ceiling risk: price approaching a whole dollar from below within 0.5%
    at_ceiling = (price < nearest_whole and (nearest_whole - price) / price * 100.0 <= 0.5)

    return {
        "whole_dollar":   nearest_whole,
        "half_dollar":    nearest_half,
        "dist_whole_pct": round(dist_whole_pct, 2),
        "dist_half_pct":  round(dist_half_pct, 2),
        "score":          score,
        "badge":          badge,
        "at_ceiling":     at_ceiling,
    }


# ── Lunar Phase ───────────────────────────────────────────────────────────────

def get_lunar_phase(trade_date):
    """Lunar phase via Julian Date calculation — no external library.

    Returns (icon, phase_name, is_retail_mania).
    is_retail_mania is True on Full Moon and New Moon — historically elevated
    retail participation windows.
    """
    y, m, d = trade_date.year, trade_date.month, trade_date.day
    if m <= 2:
        y -= 1
        m += 12
    a = int(y / 100)
    b = 2 - a + int(a / 4)
    jd = int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5

    known_new_moon_jd = 2451549.5   # Jan 6, 2000 was a confirmed new moon
    lunar_cycle = 29.53058867
    moon_age = (jd - known_new_moon_jd) % lunar_cycle
    if moon_age < 0:
        moon_age += lunar_cycle

    if moon_age < 1.85 or moon_age >= 27.68:
        return "🌑", "New Moon", True
    elif moon_age < 7.38:
        return "🌒", "Waxing Crescent", False
    elif moon_age < 9.22:
        return "🌓", "First Quarter", False
    elif moon_age < 14.77:
        return "🌔", "Waxing Gibbous", False
    elif moon_age < 16.61:
        return "🌕", "Full Moon", True
    elif moon_age < 22.15:
        return "🌖", "Waning Gibbous", False
    elif moon_age < 23.99:
        return "🌗", "Last Quarter", False
    else:
        return "🌘", "Waning Crescent", False


# ── Daily Level Confluence ────────────────────────────────────────────────────

def fetch_daily_levels(api_key, secret_key, ticker, trade_date):
    """Fetch the prior trading day's high and low from Alpaca daily bars.

    Returns (prev_high, prev_low) or (None, None) on failure.
    trade_date is the CURRENT session date — we want the session BEFORE this.
    """
    if not api_key or not secret_key:
        return None, None
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = StockHistoricalDataClient(api_key, secret_key)
        end_dt   = datetime.combine(trade_date, dtime(0, 0))
        start_dt = end_dt - timedelta(days=10)   # cover weekends/holidays

        req = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Day,
            start=start_dt,
            end=end_dt,
        )
        bars_resp = client.get_stock_bars(req)
        df_day = bars_resp.df

        if df_day is None or df_day.empty:
            return None, None

        if isinstance(df_day.index, pd.MultiIndex):
            if ticker in df_day.index.get_level_values(0):
                df_day = df_day.xs(ticker, level=0)
            elif ticker in df_day.index.get_level_values("symbol"):
                df_day = df_day.xs(ticker, level="symbol")

        df_day.index = pd.to_datetime(df_day.index)
        df_day = df_day[df_day.index.date < trade_date]
        if df_day.empty:
            return None, None

        prev_high = float(df_day["high"].iloc[-1])
        prev_low  = float(df_day["low"].iloc[-1])
        return prev_high, prev_low
    except Exception:
        return None, None


# ── Runner Archetype Similarity ───────────────────────────────────────────────
# Each archetype is a 20-element vector of relative volume weights, index 0 = lowest
# price bin, index 19 = highest price bin. All values are proportional (not normalized).

_RUNNER_ARCHETYPES = {
    "Gap & Go":          [0.18, 0.15, 0.12, 0.09, 0.07, 0.06, 0.05, 0.04, 0.04, 0.03,
                          0.03, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01],
    "Bull Flag Runner":  [0.05, 0.05, 0.08, 0.09, 0.07, 0.06, 0.04, 0.04, 0.05, 0.05,
                          0.04, 0.04, 0.04, 0.05, 0.06, 0.08, 0.09, 0.06, 0.04, 0.02],
    "VWAP Reclaim":      [0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.09, 0.10, 0.11, 0.10,
                          0.09, 0.08, 0.07, 0.05, 0.04, 0.03, 0.02, 0.02, 0.02, 0.01],
    "Afternoon Momentum":[0.02, 0.02, 0.03, 0.03, 0.04, 0.04, 0.04, 0.05, 0.05, 0.06,
                          0.06, 0.07, 0.08, 0.09, 0.10, 0.09, 0.08, 0.07, 0.05, 0.03],
    "Morning Star":      [0.14, 0.13, 0.12, 0.10, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03,
                          0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01],
    "V-Shape Recovery":  [0.09, 0.08, 0.06, 0.04, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03,
                          0.03, 0.04, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.08, 0.07],
    "Steady Grinder":    [0.04, 0.04, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.06,
                          0.06, 0.06, 0.06, 0.06, 0.06, 0.06, 0.05, 0.05, 0.05, 0.04],
    "Breakout Bomb":     [0.01, 0.01, 0.02, 0.02, 0.02, 0.02, 0.03, 0.03, 0.04, 0.05,
                          0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.10, 0.07, 0.05, 0.03],
    "IB Extension":      [0.10, 0.11, 0.12, 0.11, 0.10, 0.08, 0.07, 0.06, 0.05, 0.04,
                          0.04, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01],
    "Double Cluster":    [0.06, 0.08, 0.09, 0.10, 0.09, 0.06, 0.03, 0.02, 0.02, 0.02,
                          0.02, 0.02, 0.03, 0.06, 0.09, 0.10, 0.09, 0.08, 0.06, 0.04],
}

_SQUEEZE_ARCHETYPE = [0.01, 0.01, 0.02, 0.02, 0.02, 0.03, 0.03, 0.03, 0.03, 0.04,
                      0.04, 0.05, 0.06, 0.07, 0.09, 0.11, 0.12, 0.11, 0.08, 0.03]

_DUMP_ARCHETYPE    = [0.01, 0.01, 0.02, 0.03, 0.04, 0.05, 0.07, 0.09, 0.11, 0.12,
                      0.11, 0.10, 0.08, 0.06, 0.04, 0.03, 0.02, 0.01, 0.01, 0.01]


def _normalize_archetype(vec):
    total = sum(vec)
    return [v / total for v in vec] if total > 0 else vec


def _cosine_sim(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if (na > 0 and nb > 0) else 0.0


def compute_runner_similarity(bin_centers, vap):
    """Cosine-compare today's VP against 10 runner archetypes plus squeeze/dump.

    Resamples the VP to a 20-bin normalized vector then computes cosine similarity
    against every pre-defined archetype.

    Returns a dict:
        best_match    — name of closest runner archetype
        runner_pct    — similarity % (0-100) for best runner match
        squeeze_pct   — similarity % vs short-squeeze signature
        dump_pct      — similarity % vs dump signature
    """
    if len(vap) < 2 or float(np.sum(vap)) == 0:
        return {"best_match": "—", "runner_pct": 0.0, "squeeze_pct": 0.0, "dump_pct": 0.0}

    N = 20
    n_bins = len(vap)

    if n_bins >= N:
        bucket = n_bins / N
        vec = [float(np.sum(vap[int(i * bucket):int((i + 1) * bucket)])) for i in range(N)]
    else:
        vec = [float(vap[int(i * n_bins / N)]) for i in range(N)]

    total = sum(vec)
    if total == 0:
        return {"best_match": "—", "runner_pct": 0.0, "squeeze_pct": 0.0, "dump_pct": 0.0}
    vec_norm = [v / total for v in vec]

    best_match = "—"
    best_sim   = 0.0
    for name, archetype in _RUNNER_ARCHETYPES.items():
        arch_norm = _normalize_archetype(archetype)
        sim = _cosine_sim(vec_norm, arch_norm)
        if sim > best_sim:
            best_sim   = sim
            best_match = name

    runner_pct  = round(best_sim * 100.0, 1)
    squeeze_pct = round(_cosine_sim(vec_norm, _normalize_archetype(_SQUEEZE_ARCHETYPE)) * 100.0, 1)
    dump_pct    = round(_cosine_sim(vec_norm, _normalize_archetype(_DUMP_ARCHETYPE)) * 100.0, 1)

    return {
        "best_match":  best_match,
        "runner_pct":  runner_pct,
        "squeeze_pct": squeeze_pct,
        "dump_pct":    dump_pct,
    }


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
# TARGET ZONES
# ══════════════════════════════════════════════════════════════════════════════

def compute_target_zones(df, ib_high, ib_low, bin_centers, vap, tcs):
    """Return a list of dynamic target zone dicts based on structure.

    Each dict: {type, price, label, color, description, [lvn_price, lvn_idx]}
    """
    targets = []
    if df.empty or ib_high is None or ib_low is None:
        return targets
    ib_range = ib_high - ib_low
    if ib_range <= 0:
        return targets

    final_price  = float(df["close"].iloc[-1])
    day_high     = float(df["high"].max())
    day_low      = float(df["low"].min())

    ib_high_violated = bool((df["high"] >= ib_high).any())
    ib_low_violated  = bool((df["low"]  <= ib_low).any())
    price_back_inside = ib_low < final_price < ib_high

    # ── Coast-to-Coast ────────────────────────────────────────────────────────
    if ib_high_violated and price_back_inside:
        targets.append({
            "type": "coast_to_coast",
            "price": ib_low,
            "label": "🎯 C2C Target",
            "color": "#ff5252",
            "description": (f"IB High violated → price returned inside → "
                            f"Coast-to-Coast target: IB Low ${ib_low:.2f}"),
        })
    if ib_low_violated and price_back_inside:
        targets.append({
            "type": "coast_to_coast",
            "price": ib_high,
            "label": "🎯 C2C Target",
            "color": "#00e676",
            "description": (f"IB Low violated → price returned inside → "
                            f"Coast-to-Coast target: IB High ${ib_high:.2f}"),
        })

    # ── Range Extension  (TCS > 70 %) ────────────────────────────────────────
    if tcs > 70 and ib_range > 0:
        bullish = ib_high_violated and not ib_low_violated
        bearish = ib_low_violated  and not ib_high_violated
        if bullish:
            ext15 = ib_high + 1.5 * ib_range
            ext20 = ib_high + 2.0 * ib_range
            targets.append({"type": "trend_extension", "price": ext15,
                            "label": "🎯 1.5× Ext", "color": "#26a69a",
                            "description": f"Bullish 1.5× IB extension: ${ext15:.2f}"})
            targets.append({"type": "trend_extension", "price": ext20,
                            "label": "🎯 2.0× Ext", "color": "#4caf50",
                            "description": f"Bullish 2.0× IB extension: ${ext20:.2f}"})
        elif bearish:
            ext15 = ib_low - 1.5 * ib_range
            ext20 = ib_low - 2.0 * ib_range
            targets.append({"type": "trend_extension", "price": ext15,
                            "label": "🎯 1.5× Ext", "color": "#ef5350",
                            "description": f"Bearish 1.5× IB extension: ${ext15:.2f}"})
            targets.append({"type": "trend_extension", "price": ext20,
                            "label": "🎯 2.0× Ext", "color": "#c62828",
                            "description": f"Bearish 2.0× IB extension: ${ext20:.2f}"})

    # ── Gap Fill (Double Distribution LVN) ───────────────────────────────────
    dd = _detect_double_distribution(bin_centers, vap)
    if dd is not None:
        pk1, pk2, vi = dd
        lvn_price = float(bin_centers[vi])
        hvn1 = float(bin_centers[pk1])
        hvn2 = float(bin_centers[pk2])
        target_hvn = hvn2 if final_price < lvn_price else hvn1
        targets.append({
            "type": "gap_fill",
            "price": target_hvn,
            "lvn_price": lvn_price,
            "lvn_idx": int(vi),
            "label": "🎯 Gap Fill",
            "color": "#ffd700",
            "description": (f"DD LVN at ${lvn_price:.2f} → "
                            f"Gap Fill target ${target_hvn:.2f}"),
        })

    return targets


def compute_low_volume_nodes(bin_centers, vap, threshold_pct=0.20):
    """Identify Low Volume Nodes (LVNs) — price levels where volume is thin.

    LVNs are 'Turbo Zones': price tends to move rapidly through them because
    there is little historical supply or demand to slow it down.

    Returns a list of dicts (sorted ascending by price) with keys:
        price, volume, strength (0=weak LVN, 1=completely empty), index
    threshold_pct: bins below this fraction of avg session volume are LVNs.
    """
    if len(vap) == 0 or np.sum(vap) == 0:
        return []
    avg_vol   = float(np.mean(vap))
    if avg_vol == 0:
        return []
    threshold = avg_vol * threshold_pct
    lvns = []
    for i, (price, vol) in enumerate(zip(bin_centers, vap)):
        fvol = float(vol)
        if fvol < threshold:
            strength = 1.0 - (fvol / threshold) if threshold > 0 else 1.0
            lvns.append({
                "price":    round(float(price), 4),
                "volume":   round(fvol, 2),
                "strength": round(min(1.0, max(0.0, strength)), 3),
                "index":    int(i),
            })
    return sorted(lvns, key=lambda x: x["price"])


# ── StockTwits Social Sentiment ───────────────────────────────────────────────

def fetch_stocktwits_sentiment(ticker):
    """Fetch social sentiment from StockTwits public API (no auth key required).

    Parses up to the last 30 messages for bull/bear/neutral sentiment tags.
    Computes:
      • msg_count    — messages within the last hour (from the newest timestamp)
      • msg_velocity — messages-per-hour based on the one-hour window
      • trending     — True when velocity >= 20 msg/hr

    Returns None on any error or timeout.
    """
    import urllib.request

    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
        req = urllib.request.Request(url, headers={"User-Agent": "VolumeProfileBot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        messages = data.get("messages", [])[:30]   # API default ~30; guard explicitly
        if not messages:
            return None

        bull = bear = neutral_count = 0
        timestamps = []

        for msg in messages:
            # Sentiment tag lives in entities.sentiment.basic
            sentiment_tag = None
            entities = msg.get("entities") or {}
            raw_sent = entities.get("sentiment")
            if raw_sent and isinstance(raw_sent, dict):
                sentiment_tag = raw_sent.get("basic", "")
            # Older API shape: top-level sentiment field
            if not sentiment_tag:
                raw_top = msg.get("sentiment")
                if raw_top and isinstance(raw_top, dict):
                    sentiment_tag = raw_top.get("basic", "")

            if sentiment_tag == "Bullish":
                bull += 1
            elif sentiment_tag == "Bearish":
                bear += 1
            else:
                neutral_count += 1

            ts_str = msg.get("created_at", "")
            if ts_str:
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
                    timestamps.append(ts)
                except Exception:
                    pass

        total = bull + bear + neutral_count
        bull_pct    = round(bull / total * 100, 1) if total > 0 else 0.0
        bear_pct    = round(bear / total * 100, 1) if total > 0 else 0.0
        neutral_pct = round(max(0.0, 100.0 - bull_pct - bear_pct), 1)

        # ── msg_count = messages published in the last hour (anchored to utcnow) ─
        msg_count = 0
        msg_velocity = 0.0
        now_utc = datetime.utcnow()
        one_hour_ago = now_utc - timedelta(hours=1)
        if timestamps:
            msg_count = sum(1 for t in timestamps if t >= one_hour_ago)
            # velocity = count over the 1-hr fixed window
            msg_velocity = float(msg_count)

        trending = msg_velocity >= 20.0

        return {
            "bull_pct":     bull_pct,
            "bear_pct":     bear_pct,
            "neutral_pct":  neutral_pct,
            "msg_count":    msg_count,
            "msg_velocity": msg_velocity,
            "trending":     trending,
        }
    except Exception:
        return None


def check_target_alerts(price, targets, audio_enabled):
    """Fire a unique 'Target Reached' sound when price touches a target zone (0.5% tol)."""
    import streamlit.components.v1 as components
    if not audio_enabled or not targets or price is None:
        return
    tol = price * 0.005
    for tz in targets:
        key = f"target_fired_{tz['type']}_{tz['price']:.2f}"
        if abs(price - tz["price"]) <= tol and not st.session_state.get(key, False):
            st.session_state[key] = True
            st.toast(f"🎯 Target Reached — {tz['label']} at ${tz['price']:.2f}", icon="🎯")
            n = st.session_state.get("sound_trigger", 0) + 1
            st.session_state["sound_trigger"] = n
            components.html(
                f'<script>/* tr:{n} */{_TARGET_JS}</script>',
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
        # _run_forever() is the actual coroutine; stream.run() wraps it in
        # asyncio.run() which would conflict with our already-running loop.
        task = asyncio.ensure_future(stream._run_forever())
        while not stop_event.is_set():
            await asyncio.sleep(0.3)
            if task.done():
                break
        stream.stop()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception:
            pass
        finally:
            if not task.done():
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
# TRADE JOURNAL
# ══════════════════════════════════════════════════════════════════════════════

import csv
import os

JOURNAL_PATH = "trade_journal.csv"
_JOURNAL_COLS = [
    "timestamp", "ticker", "price", "structure", "tcs", "rvol",
    "ib_high", "ib_low", "notes", "grade", "grade_reason",
    "social_bull_pct", "social_bear_pct", "social_msg_count",
]


def load_journal() -> "pd.DataFrame":
    if not os.path.exists(JOURNAL_PATH):
        return pd.DataFrame(columns=_JOURNAL_COLS)
    try:
        df = pd.read_csv(JOURNAL_PATH)
        for col in _JOURNAL_COLS:
            if col not in df.columns:
                df[col] = ""
        return df[_JOURNAL_COLS]
    except Exception:
        return pd.DataFrame(columns=_JOURNAL_COLS)


def save_journal_entry(entry: dict):
    exists = os.path.exists(JOURNAL_PATH)

    # ── One-time schema migration ─────────────────────────────────────────────
    # If the CSV exists but was written with an older (narrower) column set,
    # rewrite it with the current _JOURNAL_COLS header before appending.
    # This prevents mixed-width rows that break pandas CSV parsing.
    if exists:
        try:
            _existing = pd.read_csv(JOURNAL_PATH)
            if any(col not in _existing.columns for col in _JOURNAL_COLS):
                for col in _JOURNAL_COLS:
                    if col not in _existing.columns:
                        _existing[col] = ""
                _existing[_JOURNAL_COLS].to_csv(JOURNAL_PATH, index=False)
        except Exception:
            pass

    with open(JOURNAL_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_JOURNAL_COLS)
        if not exists:
            writer.writeheader()
        row = {k: entry.get(k, "") for k in _JOURNAL_COLS}
        writer.writerow(row)


def compute_trade_grade(rvol, tcs, price, ib_high, ib_low, structure_label):
    """Return (grade, reason) based on RVOL, TCS, price relative to IB."""
    rvol_val = rvol if rvol is not None else 0.0
    is_trend  = "trend" in structure_label.lower()
    price_inside_ib = (
        (ib_low is not None and ib_high is not None) and (ib_low < price < ib_high)
    )
    price_above_ib = (ib_high is not None) and (price > ib_high)

    # F — disqualifying conditions first
    if rvol_val < 1.0:
        return "F", f"Grade F: Low-volume setup (RVOL {rvol_val:.1f}×) — unfavorable odds."
    if is_trend and price_inside_ib:
        return "F", "Grade F: Trend attempt but price is still inside IB — no breakout confirmation."

    # A — ideal setup
    if rvol_val > 4.0 and tcs > 70 and price_above_ib:
        return "A", (f"Grade A: RVOL {rvol_val:.1f}×, TCS {tcs:.0f}%, price above IB High — "
                     f"elite, high-conviction setup.")

    # B — solid
    if rvol_val > 2.0 and tcs > 50:
        return "B", (f"Grade B: RVOL {rvol_val:.1f}×, TCS {tcs:.0f}% — solid participation "
                     f"with reasonable confidence.")

    # C — moderate
    if (1.0 <= rvol_val <= 2.0) or (30 <= tcs <= 50):
        return "C", (f"Grade C: Moderate quality (RVOL {rvol_val:.1f}×, TCS {tcs:.0f}%) — "
                     f"acceptable but below ideal thresholds.")

    # F — catch-all low confidence
    return "F", (f"Grade F: Low confidence (RVOL {rvol_val:.1f}×, TCS {tcs:.0f}%) — "
                 f"avoid or reduce size significantly.")


_GRADE_COLORS = {"A": "#4caf50", "B": "#26a69a", "C": "#ffa726", "F": "#ef5350"}
_GRADE_SCORE  = {"A": 4, "B": 3, "C": 2, "F": 1}


def render_log_entry_ui():
    """Show the Notes box + LOG ENTRY button below the chart."""
    state = st.session_state.get("last_analysis_state")
    if not state:
        return
    with st.expander("💾 Log This Trade Entry", expanded=False):
        notes = st.text_input(
            "Mental State / Notes",
            placeholder="e.g. Calm, FOMO, Greed, Hesitated...",
            key="journal_notes_input",
        )
        if st.button("💾 LOG ENTRY", use_container_width=True, key="journal_log_btn"):
            grade, reason = compute_trade_grade(
                state.get("rvol"), state.get("tcs"), state.get("price"),
                state.get("ib_high"), state.get("ib_low"), state.get("structure"),
            )
            entry = {
                "timestamp": datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S"),
                "ticker":    state.get("ticker", ""),
                "price":     round(state.get("price", 0.0), 4),
                "structure": state.get("structure", ""),
                "tcs":       round(state.get("tcs", 0.0), 1),
                "rvol":      round(state.get("rvol") or 0.0, 2),
                "ib_high":   round(state.get("ib_high") or 0.0, 4),
                "ib_low":    round(state.get("ib_low") or 0.0, 4),
                "notes":     notes,
                "grade":     grade,
                "grade_reason": reason,
                "social_bull_pct":  state.get("social_bull_pct", ""),
                "social_bear_pct":  state.get("social_bear_pct", ""),
                "social_msg_count": state.get("social_msg_count", ""),
            }
            save_journal_entry(entry)
            gc = _GRADE_COLORS.get(grade, "#aaa")
            st.success(f"Logged! **Grade {grade}** — {reason}")
            st.markdown(
                f'<div style="display:inline-block; background:{gc}22; border:2px solid {gc}; '
                f'border-radius:50%; width:52px; height:52px; line-height:52px; '
                f'text-align:center; font-size:24px; font-weight:900; color:{gc};">'
                f'{grade}</div>',
                unsafe_allow_html=True,
            )


def render_journal_tab():
    """Render the 📖 My Journal tab."""
    df = load_journal()

    cola, colb = st.columns([1, 1])
    with cola:
        st.subheader("📖 My Trade Journal")
    with colb:
        if not df.empty:
            csv_bytes = df.to_csv(index=False).encode()
            st.download_button(
                "⬇️ Download Journal (CSV)",
                data=csv_bytes,
                file_name=f"trade_journal_{date.today()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

    if df.empty:
        st.info("No entries yet. Run an analysis and click **💾 LOG ENTRY** under the chart.")
        return

    # Grade badges + table
    st.markdown("---")
    for _, row in df.iterrows():
        grade = str(row.get("grade", "?"))
        gc = _GRADE_COLORS.get(grade, "#aaaaaa")
        reason = row.get("grade_reason", "")
        ts = row.get("timestamp", "")
        sym = row.get("ticker", "")
        price = row.get("price", "")
        struct = row.get("structure", "")
        tcs_v = row.get("tcs", "")
        rvol_v = row.get("rvol", "")
        notes_v = row.get("notes", "")
        s_bull = row.get("social_bull_pct", "")
        s_bear = row.get("social_bear_pct", "")
        s_msgs = row.get("social_msg_count", "")

        # Build optional Sentiment at Entry line
        if s_bull != "" and s_bear != "":
            try:
                _sb = float(s_bull)
                _se = float(s_bear)
                _sc = int(s_msgs) if str(s_msgs).strip() not in ("", "nan") else 0
                _sentiment_row = (
                    f'<div style="font-size:11px;color:#90caf9;margin-top:3px;">'
                    f'💬 Sentiment at Entry: '
                    f'<span style="color:#26a69a;font-weight:700;">🐂 {_sb:.0f}%</span>'
                    f' / <span style="color:#ef5350;font-weight:700;">{_se:.0f}% 🐻</span>'
                    f' · {_sc} msgs</div>'
                )
            except Exception:
                _sentiment_row = ""
        else:
            _sentiment_row = ""

        st.markdown(f"""
        <div style="display:flex; gap:16px; align-items:center; background:#12122288;
                    border:1px solid #2a2a4a; border-radius:10px;
                    padding:12px 18px; margin:8px 0;">
            <div style="flex-shrink:0; width:52px; height:52px; border-radius:50%;
                        background:{gc}22; border:2.5px solid {gc};
                        display:flex; align-items:center; justify-content:center;
                        font-size:24px; font-weight:900; color:{gc};">{grade}</div>
            <div style="flex:1; min-width:0;">
                <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:baseline;">
                    <span style="font-size:20px; font-weight:800; color:#e0e0e0;">{sym}</span>
                    <span style="font-size:13px; color:#aaa;">${price}</span>
                    <span style="font-size:11px; color:#666;">{ts}</span>
                </div>
                <div style="font-size:12px; color:#90caf9; margin:2px 0;">{struct}</div>
                <div style="font-size:11px; color:#888;">
                    TCS {tcs_v}%  ·  RVOL {rvol_v}×
                    {f'  ·  <em>{notes_v}</em>' if notes_v else ''}
                </div>
                {_sentiment_row}
                <div style="font-size:12px; color:{gc}; margin-top:4px;">{reason}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # Equity curve — grade average over entries
    st.markdown("---")
    st.markdown("**Grade Discipline Curve**")
    df2 = df.copy()
    df2["grade_score"] = df2["grade"].map(_GRADE_SCORE).fillna(1)
    df2["entry_num"]   = range(1, len(df2) + 1)
    df2["rolling_avg"] = df2["grade_score"].expanding().mean()

    import plotly.graph_objects as _go
    fig = _go.Figure()
    fig.add_trace(_go.Scatter(
        x=df2["entry_num"], y=df2["rolling_avg"],
        mode="lines+markers",
        line=dict(color="#00bcd4", width=2.5),
        marker=dict(size=7, color=df2["grade_score"].map(
            {4: "#4caf50", 3: "#26a69a", 2: "#ffa726", 1: "#ef5350"}
        ).fillna("#aaa")),
        name="Grade Average",
        hovertemplate="Entry %{x} — Avg %{y:.2f}<extra></extra>",
    ))
    fig.add_hline(y=3.0, line=dict(color="#4caf5066", dash="dot"),
                  annotation_text="B threshold", annotation_font_color="#4caf50")
    fig.update_layout(
        paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"), height=220,
        xaxis=dict(title="Entry #", gridcolor="#2a2a4a"),
        yaxis=dict(title="Avg Grade (F=1 \u2013 A=4)", gridcolor="#2a2a4a",
                   tickvals=[1, 2, 3, 4], ticktext=["F", "C", "B", "A"],
                   range=[0.5, 4.5]),
        margin=dict(l=10, r=10, t=20, b=40),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# CHART & RENDER
# ══════════════════════════════════════════════════════════════════════════════

def build_chart(df, ib_high, ib_low, bin_centers, vap, poc_price, title,
                target_zones=None, position=None, tcs=None, label="", rvol_val=None):
    """Build a TradingView-style chart using streamlit-lightweight-charts + CDN overlay.

    Candlestick chart rendered via lightweight-charts v4 (from CDN for full plugin
    support).  Volume Profile heatmap is drawn on a transparent canvas overlaid
    directly on the price chart — NOT in a separate panel — using the series'
    priceToCoordinate() API so bars are price-aligned to the Y axis.

    LVN Turbo Zones are rendered as full-width shaded horizontal bands with
    "⚡ Turbo Zone $price" text labels on the chart canvas.

    Glassmorphism HUD (TCS, RVOL, R/R) floats top-right.
    compute_buy_sell_pressure (Blended CLV+Tick Split) is untouched.
    """
    from streamlit_lightweight_charts import renderLightweightCharts as _rlc  # noqa (dep declared)
    import streamlit.components.v1 as _comp

    # ── Prepare candlestick data ───────────────────────────────────────────────
    _rdf       = df.copy()
    _cur_price = float(_rdf["close"].dropna().iloc[-1]) if not _rdf.empty else None

    try:
        _first = df.index[0]
        _last  = df.index[-1]
        _d     = _first.date()
        _ot    = EASTERN.localize(datetime(_d.year, _d.month, _d.day, 9, 30))
        _ct    = EASTERN.localize(datetime(_d.year, _d.month, _d.day, 16, 0))
        _end   = min(_last, _ct)
        df     = df.reindex(pd.date_range(_ot, _end, freq="1min"))
    except Exception:
        pass

    candles = []
    for ts, row in df.iterrows():
        try:
            if pd.isna(row["open"]) or pd.isna(row["close"]):
                continue
            candles.append({
                "time":  int(pd.Timestamp(ts).timestamp()),
                "open":  round(float(row["open"]),  4),
                "high":  round(float(row["high"]),  4),
                "low":   round(float(row["low"]),   4),
                "close": round(float(row["close"]), 4),
            })
        except Exception:
            continue

    # ── Volume Profile heatmap bands (blue→orange gradient, POC=gold) ─────────
    max_vap = float(np.max(vap)) if len(vap) > 0 and np.max(vap) > 0 else 1.0
    bin_w   = float(bin_centers[1] - bin_centers[0]) if len(bin_centers) > 1 else 0.01
    vp_bands = []
    for price, vol in zip(bin_centers, vap):
        fprice, fvol = float(price), float(vol)
        norm   = fvol / max_vap
        is_poc = abs(fprice - float(poc_price)) < bin_w * 0.6
        if is_poc:
            color = "rgba(255,215,0,0.92)"
        else:
            r = int(80  + norm * 175)
            g = int(130 + norm * -10)
            b = int(220 + norm * -220)
            a = round(0.05 + norm * 0.65, 2)
            color = f"rgba({r},{g},{b},{a})"
        vp_bands.append({"price": round(fprice, 4), "norm": round(norm, 4),
                         "color": color, "isPoc": is_poc})

    # ── LVNs (Turbo Zones) ────────────────────────────────────────────────────
    lvns = compute_low_volume_nodes(bin_centers, vap)

    # ── Price lines: IB, POC, current, target zones, LVNs, position ──────────
    plines = []
    if ib_high is not None:
        plines.append({"price": float(ib_high), "color": "#00e676",
                       "width": 2, "style": 1, "title": f"IB Hi ${float(ib_high):.2f}"})
    if ib_low is not None:
        plines.append({"price": float(ib_low),  "color": "#ff5252",
                       "width": 2, "style": 1, "title": f"IB Lo ${float(ib_low):.2f}"})
    plines.append({"price": float(poc_price), "color": "#ffd700",
                   "width": 2.5, "style": 0, "title": f"POC ${float(poc_price):.2f}"})
    if _cur_price is not None:
        plines.append({"price": float(_cur_price), "color": "#e0e0e0",
                       "width": 1.4, "style": 2, "title": f"\u25b6 ${float(_cur_price):.2f}"})
    for tz in (target_zones or []):
        plines.append({"price": float(tz["price"]), "color": tz["color"],
                       "width": 1.6, "style": 2, "title": tz["label"]})

    # ── R/R projection when TCS ≥ 75 ─────────────────────────────────────────
    tcs_val = float(tcs) if tcs is not None else 0.0
    rr_data = None
    if tcs_val >= 75 and _cur_price is not None:
        cp = float(_cur_price)
        above     = sorted([tz for tz in (target_zones or []) if tz["price"] > cp],
                           key=lambda x: x["price"])
        lvns_up   = [l for l in lvns if l["price"] > cp]
        target_p  = (float(above[0]["price"]) if above
                     else float(lvns_up[0]["price"]) if lvns_up
                     else round(cp * 1.05, 4))
        cands = ([float(ib_low)]    if (ib_low    and float(ib_low)    < cp) else []) + \
                ([float(poc_price)] if (poc_price  and float(poc_price) < cp) else [])
        stop_p        = max(cands) if cands else round(cp * 0.97, 4)
        reward, risk  = target_p - cp, cp - stop_p
        ratio         = round(reward / risk, 2) if risk > 0 else 0.0
        plines.append({"price": target_p, "color": "#76ff03",
                       "width": 2.5, "style": 0, "title": f"\U0001f3af Target ${target_p:.2f}"})
        plines.append({"price": stop_p,   "color": "#ef5350",
                       "width": 2.5, "style": 0, "title": f"\U0001f6d1 Stop ${stop_p:.2f}"})
        rr_data = {"ratio": ratio,
                   "reward_pct": round(reward / cp * 100, 1),
                   "risk_pct":   round(risk   / cp * 100, 1)}

    # Position overlay
    if position and position.get("in"):
        ae, pk = float(position["avg_entry"]), float(position["peak_price"])
        plines.append({"price": ae, "color": "#ffffff",
                       "width": 2, "style": 0, "title": f"\U0001f4cd Entry ${ae:.2f}"})
        if pk > ae:
            plines.append({"price": pk, "color": "#00bcd4",
                           "width": 1.5, "style": 1, "title": f"\u2b06 MFE ${pk:.2f}"})

    # ── HUD styling ───────────────────────────────────────────────────────────
    lbl_lo = (label or "").lower()
    if tcs_val >= 75:
        hud_color, hud_glow = "#76ff03", "0 0 18px rgba(118,255,3,0.55)"
        hud_label = "HIGH CONVICTION"
    elif "trend" in lbl_lo and "double" not in lbl_lo and "non" not in lbl_lo:
        hud_color, hud_glow = "#ff6d00", "0 0 14px rgba(255,109,0,0.45)"
        hud_label = "TREND DAY"
    elif "double" in lbl_lo:
        hud_color, hud_glow = "#00e5ff", "0 0 14px rgba(0,229,255,0.45)"
        hud_label = "DOUBLE DIST"
    else:
        hud_color, hud_glow = "#5c6bc0", "none"
        hud_label = (label or "\u2014")[:18].upper()

    rvol_str = f"{rvol_val:.1f}\u00d7" if rvol_val is not None else "\u2014"

    rr_html = ""
    if rr_data:
        rc = "#76ff03" if rr_data["ratio"] >= 2 else "#ffa726" if rr_data["ratio"] >= 1 else "#ef5350"
        rr_html = (
            f'<div class="hud-div"></div>'
            f'<div class="hud-row"><span>R/R</span>'
            f'<span class="hud-val" style="color:{rc};">{rr_data["ratio"]:.1f}:1</span></div>'
            f'<div style="font-size:9px;color:#4caf50;text-align:right;">'
            f'\U0001f3af+{rr_data["reward_pct"]}%</div>'
            f'<div style="font-size:9px;color:#ef5350;text-align:right;">'
            f'\U0001f6d1-{rr_data["risk_pct"]}%</div>'
        )

    # ── Serialise to JSON for inline JS ──────────────────────────────────────
    j_candles = json.dumps(candles)
    j_plines  = json.dumps(plines)
    j_vpbands = json.dumps(vp_bands)
    j_lvns    = json.dumps(lvns)
    safe_title = title.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')

    # ── HTML: chart + transparent VP overlay canvas + glassmorphism HUD ───────
    # The VP canvas is position:absolute over the chart div; it uses
    # series.priceToCoordinate() so bars are price-axis aligned.
    # LVN Turbo Zone bands are full-width with text on the chart.
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#16213e;overflow:hidden;font-family:'Inter','Segoe UI',sans-serif}}
#wrap{{position:relative;width:100%;height:600px;
       border:1px solid #252540;border-radius:6px;overflow:hidden;background:#16213e}}
#chart{{width:100%;height:600px;position:absolute;top:0;left:0}}
#vp-overlay{{position:absolute;top:0;left:0;width:100%;height:600px;
             pointer-events:none;z-index:10}}
#hud{{position:absolute;top:10px;right:10px;z-index:200;
      background:rgba(8,12,28,.82);
      backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
      border:1px solid {hud_color}55;border-radius:10px;padding:10px 14px;
      min-width:110px;pointer-events:none;
      box-shadow:{hud_glow},0 4px 24px rgba(0,0,0,.55)}}
.hud-title{{font-size:9px;letter-spacing:1.4px;text-transform:uppercase;
            color:{hud_color};font-weight:800;margin-bottom:7px;
            text-shadow:0 0 8px {hud_color}88}}
.hud-row{{display:flex;justify-content:space-between;align-items:center;
          font-size:11px;color:#aaa;margin-bottom:4px}}
.hud-val{{font-size:15px;font-weight:900;color:{hud_color};
          font-family:'SF Mono','Fira Code',monospace}}
.hud-div{{height:1px;background:rgba(255,255,255,.1);margin:6px 0}}
#ctitle{{position:absolute;top:10px;left:10px;z-index:200;font-size:12px;
         font-weight:600;color:#b0b0c8;pointer-events:none;
         max-width:58%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
</style></head><body>
<div id="wrap">
  <div id="chart"></div>
  <canvas id="vp-overlay"></canvas>
  <div id="ctitle">{safe_title}</div>
  <div id="hud">
    <div class="hud-title">{hud_label}</div>
    <div class="hud-row"><span>TCS</span><span class="hud-val">{tcs_val:.0f}%</span></div>
    <div class="hud-row"><span>RVOL</span><span class="hud-val">{rvol_str}</span></div>
    {rr_html}
  </div>
</div>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<script>
(function(){{
  const candleData = {j_candles};
  const plines     = {j_plines};
  const vpBands    = {j_vpbands};
  const lvns       = {j_lvns};

  const CHART_H    = 600;

  // ── Create chart ─────────────────────────────────────────────────────────
  const chartEl = document.getElementById('chart');
  const chart = LightweightCharts.createChart(chartEl, {{
    width:  chartEl.clientWidth,
    height: CHART_H,
    layout:{{background:{{type:'solid',color:'#16213e'}},textColor:'#b0b0c8'}},
    grid:{{vertLines:{{color:'#1c2235'}},horzLines:{{color:'#1c2235'}}}},
    crosshair:{{
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine:{{color:'#5c6bc066',width:1,
                 style:LightweightCharts.LineStyle.Dashed,
                 labelBackgroundColor:'#0f3460'}},
      horzLine:{{color:'#5c6bc066',width:1,
                 style:LightweightCharts.LineStyle.Dashed,
                 labelBackgroundColor:'#0f3460'}},
    }},
    rightPriceScale:{{borderColor:'#252540',scaleMargins:{{top:.06,bottom:.06}}}},
    timeScale:{{borderColor:'#252540',timeVisible:true,secondsVisible:false,
                rightOffset:8,fixLeftEdge:true}},
  }});

  // ── Candlestick series ────────────────────────────────────────────────────
  const LS = LightweightCharts.LineStyle;
  const cs = chart.addCandlestickSeries({{
    upColor:'#26a69a',  downColor:'#ef5350',
    borderUpColor:'#26a69a',borderDownColor:'#ef5350',
    wickUpColor:'#26a69a',  wickDownColor:'#ef5350',
  }});
  if (candleData.length > 0) cs.setData(candleData);

  // ── Price lines: IB, POC, targets, current price, position ───────────────
  const styleMap = [LS.Solid, LS.Dashed, LS.Dotted, LS.LargeDashed];
  for (const pl of plines) {{
    cs.createPriceLine({{
      price:            pl.price,
      color:            pl.color,
      lineWidth:        pl.width || 1.5,
      lineStyle:        styleMap[pl.style] ?? LS.Dashed,
      axisLabelVisible: true,
      title:            pl.title || '',
    }});
  }}
  chart.timeScale().fitContent();

  // ── Volume Profile + LVN Turbo Zone overlay on the chart canvas ───────────
  // The canvas sits on top of the chart (pointer-events:none).
  // priceToCoordinate() maps each bin's price to the correct Y pixel position,
  // so the VP heatmap bars align with the chart's price axis at any zoom level.
  const vpCanvas  = document.getElementById('vp-overlay');
  const binCount  = vpBands.length;

  function drawVPOverlay() {{
    const W = chartEl.clientWidth;
    const H = CHART_H;
    vpCanvas.width  = W;
    vpCanvas.height = H;
    const ctx = vpCanvas.getContext('2d');
    ctx.clearRect(0, 0, W, H);

    if (vpBands.length === 0) return;

    const maxNorm  = Math.max(...vpBands.map(b => b.norm), 0.001);
    const barMaxW  = Math.min(W * 0.20, 110);  // VP bars: right 20% of chart

    // ── Draw VP heatmap bars (right side, extending left) ────────────────
    for (const band of vpBands) {{
      const y = cs.priceToCoordinate(band.price);
      if (y === null || y === undefined || y < 0 || y > H) continue;
      const barW = (band.norm / maxNorm) * barMaxW;
      const halfH = Math.max(H / Math.max(binCount, 1) * 0.45, 1.5);

      ctx.fillStyle = band.color;
      ctx.fillRect(W - barW, y - halfH, barW, halfH * 2);

      if (band.isPoc) {{
        // Gold border + "POC" label to the left of bar
        ctx.strokeStyle = 'rgba(255,215,0,0.95)';
        ctx.lineWidth   = 1.5;
        ctx.strokeRect(W - barW - 1, y - halfH - 1, barW + 2, halfH * 2 + 2);
        ctx.fillStyle = '#ffd700';
        ctx.font      = 'bold 9px monospace';
        const lx = Math.max(0, W - barW - 34);
        ctx.fillText('POC', lx, y + 3);
      }}
    }}

    // ── Draw LVN Turbo Zone bands (full-width, labeled) ───────────────────
    const binH = H / Math.max(binCount, 1);
    for (const lvn of lvns) {{
      const y = cs.priceToCoordinate(lvn.price);
      if (y === null || y === undefined || y < 0 || y > H) continue;
      const bandH = Math.max(binH, 5);

      // Semi-transparent amber fill across full chart width
      ctx.fillStyle = 'rgba(255,143,0,0.07)';
      ctx.fillRect(0, y - bandH / 2, W, bandH);

      // Dashed amber border
      ctx.strokeStyle = 'rgba(255,143,0,0.40)';
      ctx.lineWidth   = 1;
      ctx.setLineDash([5, 4]);
      ctx.strokeRect(1, y - bandH / 2, W - 2, bandH);
      ctx.setLineDash([]);

      // ⚡ Turbo Zone label at left edge of band
      ctx.fillStyle = '#ff8f00';
      ctx.font      = 'bold 9px monospace';
      ctx.fillText('\u26a1 Turbo Zone $' + lvn.price.toFixed(2), 6, y - 2);
    }}
  }}

  // Initial draw after layout settles
  requestAnimationFrame(() => {{
    setTimeout(drawVPOverlay, 80);
  }});

  // Redraw whenever price scale or time range changes
  chart.timeScale().subscribeVisibleTimeRangeChange(drawVPOverlay);
  chart.subscribeCrosshairMove(drawVPOverlay);

  // Resize handler
  new ResizeObserver(() => {{
    chart.resize(chartEl.clientWidth, CHART_H);
    drawVPOverlay();
  }}).observe(document.getElementById('wrap'));

}})();
</script></body></html>"""

    _comp.html(html, height=614, scrolling=False)


def render_structure_banner(label, color, detail, probs, tcs,
                            is_runner=False, sector_bonus=0.0, insight=None):
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

    # Key Insights box — separate call so Streamlit's markdown parser doesn't escape inner HTML
    if insight:
        st.markdown(
            f'<div style="margin-top:6px; background:#1a2744; border-left:3px solid {color}99;'
            f' border-radius:5px; padding:9px 14px;">'
            f'<span style="font-size:10px; color:#888; text-transform:uppercase;'
            f' letter-spacing:1px; font-weight:600;">KEY INSIGHTS</span><br>'
            f'<span style="font-size:13px; color:#d0d8f0; line-height:1.55;">{insight}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )


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


def render_buy_sell_widget(bsp, rvol_val=None):
    """Buy/Sell Volume Pressure oscillator — 0-100 gauge with momentum arrow.

    Uses the blended CLV+Tick formula (same as ThinkScript Blended split).
    Fires a HIGH CONVICTION alert when RVOL > 3 AND buy pressure is ramping.
    """
    if bsp is None:
        return
    buy_pct    = bsp["buy_pct"]
    trend_now  = bsp["trend_now"]
    trend_prev = bsp["trend_prev"]
    delta      = trend_now - trend_prev

    if buy_pct >= 65:
        bar_color, label = "#4caf50", "🔼 BUY DOMINANT"
    elif buy_pct >= 57:
        bar_color, label = "#8bc34a", "↑ Buy Leaning"
    elif buy_pct >= 43:
        bar_color, label = "#ffa726", "⇔ Balanced"
    elif buy_pct >= 35:
        bar_color, label = "#ef9a9a", "↓ Sell Leaning"
    else:
        bar_color, label = "#ef5350", "🔽 SELL DOMINANT"

    if delta > 3:
        momentum, mom_color = f"▲ Ramping +{delta:.0f}%", "#4caf50"
    elif delta < -3:
        momentum, mom_color = f"▼ Cooling {delta:.0f}%", "#ef5350"
    else:
        momentum, mom_color = "→ Flat", "#aaaaaa"

    # ── High-conviction signal: RVOL > 3 + buy pressure ramping ──────────────
    rvol_gt3     = rvol_val is not None and rvol_val >= 3.0
    buy_ramping  = delta > 3 and buy_pct >= 55
    sell_ramping = delta < -3 and buy_pct <= 45
    hc_alert     = ""
    if rvol_gt3 and buy_ramping:
        hc_alert = (
            f'<div style="background:#4caf5022; border:1px solid #4caf5088; '
            f'border-radius:6px; padding:6px 12px; margin-bottom:6px; '
            f'font-size:12px; font-weight:700; color:#4caf50; text-align:center;">'
            f'🚀 HIGH CONVICTION BUY — RVOL {rvol_val:.1f}× + Buy Ramping'
            f'</div>'
        )
    elif rvol_gt3 and sell_ramping:
        hc_alert = (
            f'<div style="background:#ef535022; border:1px solid #ef535088; '
            f'border-radius:6px; padding:6px 12px; margin-bottom:6px; '
            f'font-size:12px; font-weight:700; color:#ef5350; text-align:center;">'
            f'🔻 HIGH CONVICTION SELL — RVOL {rvol_val:.1f}× + Sell Ramping'
            f'</div>'
        )

    st.markdown(f"""
    <div style="background:#1a1a2e; border:1px solid {bar_color}55; border-radius:8px;
                padding:10px 16px; margin:4px 0 6px 0;">
      {hc_alert}
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
        <span style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.8px;">
          Buy / Sell Pressure &nbsp;<span style="color:#555;">(CLV+Tick Blended)</span>
        </span>
        <span style="font-size:12px; font-weight:700; color:{bar_color};">{label}</span>
        <span style="font-size:11px; color:{mom_color};">{momentum}</span>
      </div>
      <div style="background:#333; border-radius:4px; height:14px; width:100%;
                  position:relative; overflow:hidden;">
        <div style="position:absolute; left:0; top:0; height:100%; width:{buy_pct:.1f}%;
                    background:{bar_color}; border-radius:4px;"></div>
        <div style="position:absolute; left:50%; top:0; height:100%;
                    width:2px; background:#ffffff44;"></div>
      </div>
      <div style="display:flex; justify-content:space-between; margin-top:5px;">
        <span style="font-size:11px; color:#ef5350;">🔴 Sell {bsp["sell_pct"]:.0f}%</span>
        <span style="font-size:11px; color:{bar_color}; font-weight:700;">🟢 Buy {buy_pct:.0f}%</span>
      </div>
    </div>
    """, unsafe_allow_html=True)


def render_social_sentiment_widget(sentiment, rvol_val, bsp):
    """Render StockTwits social sentiment card below the buy/sell pressure widget.

    Args:
        sentiment  — dict from fetch_stocktwits_sentiment(), or None
        rvol_val   — float RVOL (used for HERD alert threshold ≥ 3)
        bsp        — dict from compute_buy_sell_pressure() — used for momentum
                     (trend_now vs trend_prev: a delta > 3 = ramping buy,
                      delta < -3 = falling buy)
    """
    if sentiment is None:
        st.markdown(
            '<div style="background:#12122288;border:1px solid #2a2a4a;border-radius:10px;'
            'padding:10px 14px;margin-bottom:8px;font-size:12px;color:#555;">'
            '💬 Sentiment unavailable</div>',
            unsafe_allow_html=True,
        )
        return

    bull_pct     = sentiment.get("bull_pct", 0.0)
    bear_pct     = sentiment.get("bear_pct", 0.0)
    neutral_pct  = sentiment.get("neutral_pct", 0.0)
    msg_count    = sentiment.get("msg_count", 0)
    msg_velocity = sentiment.get("msg_velocity", 0.0)
    trending     = sentiment.get("trending", False)

    # ── Buy pressure momentum (ramping / falling) ─────────────────────────────
    # trend_now and trend_prev are the last-5 vs prior-5 bar buy percentages
    # produced by compute_buy_sell_pressure().  A delta > 3 = buy ramping;
    # a delta < -3 = buy falling.
    trend_now  = (bsp or {}).get("trend_now",  50.0)
    trend_prev = (bsp or {}).get("trend_prev", 50.0)
    buy_delta  = trend_now - trend_prev   # positive = ramping, negative = falling

    velocity_arrow = "⬆️" if msg_velocity >= 20 else ("➡️" if msg_velocity >= 8 else "⬇️")

    # ── Alert detection (momentum-based, not static level) ────────────────────
    buy_ramping = buy_delta > 3.0
    buy_falling = buy_delta < -3.0
    herd_piling = trending and (rvol_val or 0.0) >= 3.0 and buy_ramping
    crowd_trap  = (not herd_piling) and trending and buy_falling

    alert_html = ""
    if herd_piling:
        alert_html = (
            '<div style="margin-top:8px;padding:5px 10px;border-radius:6px;'
            'background:#00e67622;border:1px solid #00e67688;font-size:11px;'
            'font-weight:700;color:#00e676;letter-spacing:.5px;'
            'box-shadow:0 0 8px #00e67644;">'
            '🐂 HERD PILING IN — RVOL + crowd + buy momentum all surging</div>'
        )
    elif crowd_trap:
        alert_html = (
            '<div style="margin-top:8px;padding:5px 10px;border-radius:6px;'
            'background:#ff572222;border:1px solid #ff572288;font-size:11px;'
            'font-weight:700;color:#ff7043;letter-spacing:.5px;">'
            '⚠️ CROWD TRAP — social buzz spiking but buy pressure fading</div>'
        )

    sentiment_html = f"""
    <div style="background:#12122288;border:1px solid #2a2a4a;border-radius:10px;
                padding:12px 14px;margin-bottom:8px;">
        <div style="font-size:10px;letter-spacing:1.2px;text-transform:uppercase;
                    color:#90caf9;font-weight:700;margin-bottom:8px;">
            💬 StockTwits Sentiment
        </div>
        <div style="font-size:11px;color:#aaa;margin-bottom:6px;">
            {msg_count} msgs in last hr &nbsp;·&nbsp; {velocity_arrow}
        </div>
        <!-- Bull/Bear bar -->
        <div style="display:flex;gap:0;border-radius:4px;overflow:hidden;height:14px;">
            <div style="width:{bull_pct}%;background:#26a69a;min-width:2px;"></div>
            <div style="width:{neutral_pct}%;background:#37474f;"></div>
            <div style="width:{bear_pct}%;background:#ef5350;min-width:2px;"></div>
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:4px;
                    font-size:11px;color:#aaa;">
            <span style="color:#26a69a;font-weight:700;">🐂 {bull_pct:.0f}%</span>
            <span style="color:#666;">Neutral {neutral_pct:.0f}%</span>
            <span style="color:#ef5350;font-weight:700;">{bear_pct:.0f}% 🐻</span>
        </div>
        {alert_html}
    </div>
    """
    st.markdown(sentiment_html, unsafe_allow_html=True)


def render_round_number_widget(mag):
    """Round Number Magnet badge — shows proximity to whole/half dollar levels."""
    if mag is None or mag.get("badge") is None:
        return

    badge      = mag["badge"]
    score      = mag["score"]
    whole      = mag["whole_dollar"]
    half_d     = mag["half_dollar"]
    dw         = mag["dist_whole_pct"]
    dh         = mag["dist_half_pct"]
    at_ceiling = mag["at_ceiling"]

    badge_colors = {
        "Strong":   ("#ff6d00", "#ff6d0022"),
        "Moderate": ("#ffa726", "#ffa72618"),
        "Weak":     ("#5c6bc0", "#5c6bc018"),
    }
    bc, bg = badge_colors.get(badge, ("#555", "#11111118"))

    ceil_html = ""
    if at_ceiling:
        ceil_html = (
            '<div style="font-size:11px; color:#ef5350; margin-top:4px;">'
            f'⚠️ Approaching ${whole:.0f} ceiling — breakout needed to clear resistance'
            '</div>'
        )

    st.markdown(
        f'<div style="background:{bg}; border:1px solid {bc}55; border-radius:8px; '
        f'padding:8px 16px; margin:4px 0 6px 0;">'
        f'<div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">'
        f'<span style="font-size:11px; color:#888; text-transform:uppercase; '
        f'letter-spacing:.8px;">🧲 Round Number Magnet</span>'
        f'<span style="font-size:12px; font-weight:700; color:{bc}; background:{bc}22; '
        f'padding:1px 8px; border-radius:4px; border:1px solid {bc}55;">{badge}</span>'
        f'<span style="font-size:12px; color:#aaa;">${whole:.0f}: {dw:.2f}% away</span>'
        f'<span style="color:#2a2a4a;">|</span>'
        f'<span style="font-size:12px; color:#aaa;">${half_d:.2f}: {dh:.2f}% away</span>'
        f'<span style="font-size:12px; color:{bc}; font-family:monospace; margin-left:auto;">'
        f'Score&nbsp;{score}</span>'
        f'</div>'
        f'{ceil_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_runner_dna_widget(sim):
    """Runner DNA similarity badges — shows archetype match + squeeze/dump signals."""
    if sim is None:
        return
    runner_pct  = sim["runner_pct"]
    best_match  = sim["best_match"]
    squeeze_pct = sim["squeeze_pct"]
    dump_pct    = sim["dump_pct"]

    if runner_pct < 60 and squeeze_pct < 60 and dump_pct < 60:
        return

    parts = []
    if runner_pct >= 60:
        rc = "#76ff03" if runner_pct >= 80 else "#4caf50"
        parts.append(
            f'<div style="background:{rc}11; border:1px solid {rc}44; border-radius:6px; '
            f'padding:6px 12px; display:inline-flex; align-items:center; gap:8px;">'
            f'<span style="font-size:10px; color:#888; text-transform:uppercase; '
            f'letter-spacing:.6px;">Runner DNA</span>'
            f'<span style="font-size:15px; font-weight:800; color:{rc}; '
            f'font-family:monospace;">{runner_pct:.0f}%</span>'
            f'<span style="font-size:11px; color:{rc}; font-weight:600;">{best_match}</span>'
            f'</div>'
        )
    if squeeze_pct >= 60:
        sc = "#ff6d00" if squeeze_pct >= 80 else "#ffa726"
        parts.append(
            f'<div style="background:{sc}11; border:1px solid {sc}44; border-radius:6px; '
            f'padding:6px 12px; display:inline-flex; align-items:center; gap:8px;">'
            f'<span style="font-size:10px; color:#888; text-transform:uppercase; '
            f'letter-spacing:.6px;">⚡ Short Squeeze</span>'
            f'<span style="font-size:15px; font-weight:800; color:{sc}; '
            f'font-family:monospace;">{squeeze_pct:.0f}%</span>'
            f'</div>'
        )
    if dump_pct >= 60:
        dc = "#ef5350"
        parts.append(
            f'<div style="background:{dc}11; border:1px solid {dc}44; border-radius:6px; '
            f'padding:6px 12px; display:inline-flex; align-items:center; gap:8px;">'
            f'<span style="font-size:10px; color:#888; text-transform:uppercase; '
            f'letter-spacing:.6px;">🔻 Dump Pattern</span>'
            f'<span style="font-size:15px; font-weight:800; color:{dc}; '
            f'font-family:monospace;">{dump_pct:.0f}%</span>'
            f'</div>'
        )

    if parts:
        st.markdown(
            '<div style="display:flex; gap:8px; flex-wrap:wrap; margin:4px 0 6px 0;">'
            + "".join(parts)
            + '</div>',
            unsafe_allow_html=True,
        )


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
    label, color, detail, insight = classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price, avg_daily_vol=avg_daily_vol
    )
    probs = compute_structure_probabilities(df, bin_centers, vap, ib_high, ib_low, poc_price)
    tcs = compute_tcs(df, ib_high, ib_low, poc_price, sector_bonus=sector_bonus)
    target_zones = compute_target_zones(df, ib_high, ib_low, bin_centers, vap, tcs)

    # ── High Conviction logger ─────────────────────────────────────────────────
    try:
        _top_struct, _top_prob = max(probs.items(), key=lambda x: x[1])
        if _top_prob >= HICONS_THRESHOLD and ib_high is not None and ib_low is not None:
            log_high_conviction(ticker, selected_date, _top_struct, _top_prob,
                                ib_high=ib_high, ib_low=ib_low, poc_price=poc_price)
    except Exception:
        pass
    audio_enabled = st.session_state.get("audio_alerts_enabled", True)
    check_tcs_alerts(tcs, audio_enabled)

    # ── MarketBrain — real-time structure prediction ───────────────────────────
    brain = MarketBrain()
    brain.load_from_session()
    rvol_pre = compute_rvol(df, intraday_curve=intraday_curve, avg_daily_vol=avg_daily_vol)
    try:
        _brain_ivp, _ = compute_ib_volume_stats(df, ib_high, ib_low) if (
            ib_high is not None and ib_low is not None) else (None, None)
    except Exception:
        _brain_ivp = None
    _brain_has_dd = _detect_double_distribution(bin_centers, vap) is not None
    brain.update(df, rvol=rvol_pre, ib_vol_pct=_brain_ivp,
                 poc_price=poc_price, has_double_dist=_brain_has_dd)
    st.session_state.brain_predicted = brain.prediction

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

    # ── IB Volume Stats widget ─────────────────────────────────────────────────
    ib_vol_pct_disp, ib_range_ratio_disp = compute_ib_volume_stats(df, ib_high, ib_low)
    _ivp_pct = ib_vol_pct_disp * 100
    _irr_pct = ib_range_ratio_disp * 100
    # Color coding: balanced (green) vs directional (orange/red)
    _ivp_color = ("#4caf50" if _ivp_pct >= 60 else "#ffa726" if _ivp_pct >= 35 else "#ef5350")
    _irr_color = ("#4caf50" if _irr_pct >= 50 else "#ffa726" if _irr_pct >= 25 else "#ef5350")
    _ivp_label = "Balanced" if _ivp_pct >= 60 else ("Neutral" if _ivp_pct >= 35 else "Directional")
    _irr_label = "Contained" if _irr_pct >= 50 else ("Moderate" if _irr_pct >= 25 else "Expanded")
    st.markdown(
        f'<div style="background:#0f3460; border:1px solid #1a3a6e; border-radius:6px; '
        f'padding:8px 16px; margin:4px 0 6px 0; display:flex; align-items:center; gap:20px; flex-wrap:wrap;">'
        f'<span style="font-size:11px; color:#5c6bc0; text-transform:uppercase; '
        f'letter-spacing:1px; white-space:nowrap;">📐 IB Structure</span>'
        f'<span style="font-size:12px; color:#aaa;">IB Vol%: '
        f'<b style="color:{_ivp_color};">{_ivp_pct:.0f}%</b> '
        f'<span style="color:{_ivp_color}; font-size:11px;">({_ivp_label})</span></span>'
        f'<span style="color:#2a2a4a;">|</span>'
        f'<span style="font-size:12px; color:#aaa;">IB/Day Range: '
        f'<b style="color:{_irr_color};">{_irr_pct:.0f}%</b> '
        f'<span style="color:{_irr_color}; font-size:11px;">({_irr_label})</span></span>'
        f'<span style="color:#2a2a4a;">|</span>'
        f'<span style="font-size:11px; color:#555; white-space:nowrap;">'
        f'IB ${ib_high:.2f} – ${ib_low:.2f} &nbsp;|&nbsp; POC ${poc_price:.2f}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    render_velocity_widget(df)
    render_rvol_widget(rvol_val, rvol_lbl, rvol_color, is_runner)
    _bsp_data = compute_buy_sell_pressure(df)
    render_buy_sell_widget(_bsp_data, rvol_val=rvol_val)

    # ── StockTwits Social Sentiment (cache-backed) ────────────────────────────
    # Fetched at most ONCE per ticker per session: on the first render for that
    # ticker regardless of mode (historical, scanner, live first entry).
    # Subsequent Live/Replay auto-reruns are cache hits → zero API calls.
    _st_cache = st.session_state.get("stocktwits_cache", {})
    if ticker not in _st_cache:
        _st_cache[ticker] = fetch_stocktwits_sentiment(ticker)
        st.session_state.stocktwits_cache = _st_cache
    _sentiment = _st_cache.get(ticker)
    render_social_sentiment_widget(_sentiment, rvol_val, _bsp_data)

    # ── Round Number Magnet ───────────────────────────────────────────────────
    magnetism = compute_round_number_magnetism(price_now)
    # Visual-only TCS penalty: approaching whole-dollar ceiling reduces displayed TCS
    tcs_display = max(0.0, tcs - 10.0) if magnetism.get("at_ceiling") else tcs
    render_round_number_widget(magnetism)

    # ── Daily Level Confluence — evaluate BEFORE structure banner so penalty is real ──
    _confluence_active = False
    _confluence_prev_high = None
    try:
        _dl = st.session_state.get("daily_levels_cache", {})
        _prev_high_chk = _dl.get("prev_high")
        _dl_ticker_chk = _dl.get("ticker", "")
        # Guard: only apply if cache matches current ticker (stale-date guard via ticker key)
        if _prev_high_chk and _dl_ticker_chk == ticker and ib_high is not None and price_now > ib_high:
            _prev_cl2 = df["close"].shift(1)
            _tr2 = pd.concat([
                df["high"] - df["low"],
                (df["high"] - _prev_cl2).abs(),
                (df["low"]  - _prev_cl2).abs(),
            ], axis=1).max(axis=1)
            _atr2 = float(_tr2.rolling(window=min(14, len(df))).mean().iloc[-1])
            if _atr2 > 0 and abs(price_now - _prev_high_chk) <= _atr2:
                _confluence_active   = True
                _confluence_prev_high = _prev_high_chk
                tcs_display = max(0.0, tcs_display - 10.0)
    except Exception:
        pass

    render_structure_banner(label, color, detail, probs, tcs_display,
                            is_runner=is_runner, sector_bonus=sector_bonus,
                            insight=insight)

    # ── Daily Confluence warning banner (shown after banner so context is clear) ──
    if _confluence_active and _confluence_prev_high is not None:
        st.markdown(
            f'<div style="background:#ef535022; border:1px solid #ef5350; '
            f'border-radius:6px; padding:8px 16px; margin:4px 0 6px 0;">'
            f'<span style="font-size:12px; font-weight:700; color:#ef5350;">'
            f'⚠️ DAILY CONFLUENCE ZONE — IB breakout running into prior-day high '
            f'${_confluence_prev_high:.2f} (within 1 ATR). Resistance overhead — '
            f'TCS display reduced by 10 pts.</span></div>',
            unsafe_allow_html=True,
        )

    render_model_prediction(pred_outcome, pred_reasoning)

    # ── MarketBrain: compare prediction vs actual + running counter ───────────
    bc = brain.color()
    _brain_correct_now = False
    _brain_newly_logged = False

    # Auto-compare once IB is complete and brain has a real prediction
    if brain.ib_set and brain.prediction != "Analyzing IB…" \
            and ib_high is not None and ib_low is not None \
            and ib_high != float("inf") and ib_low != float("inf"):
        _today_str   = datetime.now(EASTERN).strftime("%Y-%m-%d")
        _compare_key = f"{ticker}_{_today_str}_{float(ib_high):.4f}_{float(ib_low):.4f}"

        # Dedup: check both session state AND the CSV (survives reloads)
        _already_in_csv = False
        if st.session_state.brain_last_compared != _compare_key:
            try:
                _chk = pd.read_csv(TRACKER_FILE) if os.path.exists(TRACKER_FILE) else pd.DataFrame()
                if "compare_key" in _chk.columns:
                    _already_in_csv = (_chk["compare_key"] == _compare_key).any()
            except Exception:
                pass

        if st.session_state.brain_last_compared != _compare_key and not _already_in_csv:
            st.session_state.brain_last_compared = _compare_key
            # Fuzzy match: strip emojis/punctuation and compare core words
            _pred_clean   = _strip_emoji(brain.prediction)
            _actual_clean = _strip_emoji(label)
            _brain_correct_now = (
                _pred_clean in _actual_clean or _actual_clean in _pred_clean
                or any(w in _actual_clean for w in _pred_clean.split() if len(w) > 4)
            )
            st.session_state.brain_session_total   += 1
            if _brain_correct_now:
                st.session_state.brain_session_correct += 1
            # Log to CSV with the compare_key stored for reload dedup
            log_accuracy_entry(ticker, brain.prediction, label,
                               compare_key=_compare_key)
            _brain_newly_logged = True
        elif st.session_state.brain_last_compared != _compare_key and _already_in_csv:
            # Already in CSV from a previous session — just sync the session key
            st.session_state.brain_last_compared = _compare_key

    _b_corr  = st.session_state.brain_session_correct
    _b_total = st.session_state.brain_session_total
    _b_rate  = (_b_corr / _b_total * 100) if _b_total > 0 else 0
    _counter_str = (f"Today: {_b_corr}/{_b_total} ({_b_rate:.0f}%)"
                    if _b_total > 0 else "Today: —")
    _counter_col = "#4caf50" if _b_rate >= 60 else "#ffa726" if _b_rate >= 40 else "#ef5350"

    # ── All-time win rate — reads directly from CSV, never resets ─────────────
    _at_total, _at_correct, _at_rate = 0, 0, None
    try:
        if os.path.exists(TRACKER_FILE):
            _at_df = pd.read_csv(TRACKER_FILE)
            _at_total = int(len(_at_df))
            if "correct" in _at_df.columns and _at_total > 0:
                _at_correct = int((_at_df["correct"] == "✅").sum())
                _at_rate    = round(float(_at_correct) / float(_at_total) * 100.0, 1)
    except Exception:
        _at_total, _at_correct, _at_rate = 0, 0, None

    _at_is_valid = (_at_rate is not None and isinstance(_at_rate, (int, float))
                    and _at_rate == _at_rate)   # NaN check
    _at_col = ("#4caf50" if _at_is_valid and _at_rate >= 60
               else "#ffa726" if _at_is_valid and _at_rate >= 40
               else "#ef5350" if _at_is_valid else "#555")
    _at_str  = f"{_at_rate:.0f}%" if _at_is_valid else "—"
    _at_lbl  = f"All-time: <b style='font-size:18px; color:{_at_col};'>{_at_str}</b>"
    if _at_is_valid:
        _at_lbl += f" <span style='font-size:10px; color:#555;'>({_at_correct}/{_at_total})</span>"

    st.markdown(
        f'<div style="background:{bc}11; border-left:3px solid {bc}; border-radius:6px; '
        f'padding:10px 16px; margin:6px 0 4px 0; display:flex; align-items:center; gap:16px; flex-wrap:wrap;">'
        f'<span style="font-size:11px; color:#888; text-transform:uppercase; '
        f'letter-spacing:1px; white-space:nowrap;">🧠 Brain</span>'
        f'<span style="font-size:15px; font-weight:700; color:{bc};">{brain.prediction}</span>'
        f'<span style="font-size:11px; color:#555;">vs <span style="color:{color};">{label}</span></span>'
        f'<span style="font-size:11px; color:#444; margin-left:auto;">|</span>'
        f'<span style="font-size:12px; color:#aaa;">{_at_lbl}</span>'
        f'<span style="font-size:11px; font-weight:600; color:{_counter_col}; '
        f'background:{_counter_col}22; padding:2px 8px; border-radius:4px; '
        f'border:1px solid {_counter_col}44; white-space:nowrap;">{_counter_str}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Flash notification when brain is newly correct / newly wrong ──────────
    if _brain_newly_logged:
        if _brain_correct_now:
            st.markdown(
                f'<div style="background:#4caf5022; border:1px solid #4caf50; border-radius:6px; '
                f'padding:8px 16px; margin:4px 0; font-size:13px; font-weight:700; color:#4caf50;">'
                f'✅ Brain Correct! Predicted <em>{brain.prediction}</em> — matches <em>{label}</em>. '
                f'Running accuracy: {_b_corr}/{_b_total} ({_b_rate:.0f}%)</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="background:#ffa72622; border:1px solid #ffa726; border-radius:6px; '
                f'padding:8px 16px; margin:4px 0; font-size:13px; color:#ffa726;">'
                f'🔄 Brain predicted <em>{brain.prediction}</em> — actual was <em>{label}</em>. '
                f'Data logged for learning. Running: {_b_corr}/{_b_total} ({_b_rate:.0f}%)</div>',
                unsafe_allow_html=True,
            )

    # ── Runner DNA Similarity ─────────────────────────────────────────────────
    try:
        sim_data = compute_runner_similarity(bin_centers, vap)
        render_runner_dna_widget(sim_data)
    except Exception:
        pass

    # ── Persist snapshot for Live Pulse header + Log Entry ────────────────────
    st.session_state.last_analysis_state = {
        "ticker":    ticker,
        "price":     price_now,
        "structure": label,
        "tcs":       tcs,
        "rvol":      rvol_val,
        "ib_high":   ib_high,
        "ib_low":    ib_low,
        "rvol_color": rvol_color,
        "is_runner": is_runner,
        "label_color": color,
        "vol_velocity_str": "",
        "brain_predicted": brain.prediction,
        "social_bull_pct":  (_sentiment or {}).get("bull_pct", ""),
        "social_bear_pct":  (_sentiment or {}).get("bear_pct", ""),
        "social_msg_count": (_sentiment or {}).get("msg_count", ""),
    }

    # ── Update peak price + auto-alerts for open position ────────────────────
    if st.session_state.position_in and st.session_state.position_ticker == ticker:
        avg_entry = st.session_state.position_avg_entry

        # Track MFE
        if price_now > st.session_state.position_peak_price:
            st.session_state.position_peak_price = price_now
            save_position_state()

        # 1. Breakeven alert — price reached +2% from entry
        if avg_entry > 0 and price_now >= avg_entry * 1.02:
            be_pct = (price_now - avg_entry) / avg_entry * 100
            st.markdown(
                f'<div style="background:#ff980022; border:1px solid #ff9800; border-radius:6px; '
                f'padding:10px 18px; margin:6px 0; font-size:14px; font-weight:700; color:#ff9800;">'
                f'⚡ MOVE STOP TO BREAKEVEN — Price is +{be_pct:.1f}% from entry '
                f'(${avg_entry:.2f} → ${price_now:.2f}). '
                f'Set stop at ${avg_entry:.2f} to lock in a risk-free trade.</div>',
                unsafe_allow_html=True,
            )

        # 2. Auto Take-Profit — price hit IB High on Neutral or Normal structure
        _tp_structures = {"Neutral", "Neutral Extreme", "Normal", "Normal Variation"}
        _label_base    = label.split(" ")[0] if label else ""
        _tp_triggered  = (
            avg_entry > 0
            and ib_high is not None
            and price_now >= ib_high
            and any(s in label for s in _tp_structures)
        )
        if _tp_triggered:
            _mfe      = st.session_state.position_peak_price   # capture before exit clears it
            _realized = exit_position(price_now, actual_structure=label)
            _pnl_col  = "#4caf50" if _realized >= 0 else "#ef5350"
            st.markdown(
                f'<div style="background:{_pnl_col}22; border:1px solid {_pnl_col}; '
                f'border-radius:6px; padding:10px 18px; margin:6px 0; font-size:14px; '
                f'font-weight:700; color:{_pnl_col};">'
                f'🎯 AUTO TAKE PROFIT — Price reached IB High ${ib_high:.2f} on a '
                f'<em>{label}</em> day. '
                f'Position closed at ${price_now:.2f}. '
                f'Realized: ${_realized:+.2f} | MFE: ${_mfe:.2f}</div>',
                unsafe_allow_html=True,
            )
            time.sleep(0.5)
            st.rerun()

    # ── Target zone alerts + sidebar "Distance to Target" ─────────────────────
    check_target_alerts(price_now, target_zones, audio_enabled)
    if target_zones:
        with st.sidebar:
            st.markdown("---")
            st.markdown("**🎯 Distance to Target**")
            for tz in target_zones:
                dist_pct = abs(price_now - tz["price"]) / price_now * 100
                arrow = "▲" if tz["price"] > price_now else "▼"
                tc = tz["color"]
                st.markdown(
                    f'<div style="background:#1a2744; border-left:3px solid {tc}; '
                    f'border-radius:4px; padding:6px 10px; margin:4px 0; font-size:12px;">'
                    f'<span style="color:{tc}; font-weight:700;">{tz["label"]}</span> '
                    f'<span style="color:#ccc;">${tz["price"]:.2f}</span> '
                    f'<span style="color:#888;">{arrow} {dist_pct:.1f}% away</span></div>',
                    unsafe_allow_html=True,
                )

    pos_state = {
        "in":         st.session_state.position_in,
        "avg_entry":  st.session_state.position_avg_entry,
        "peak_price": st.session_state.position_peak_price,
        "shares":     st.session_state.position_shares,
    }
    build_chart(df, ib_high, ib_low, bin_centers, vap, poc_price, chart_title,
                target_zones=target_zones, position=pos_state,
                tcs=tcs, label=label, rvol_val=rvol_val)

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
    mode = st.radio("Mode", ["📅 Historical", "🎬 Replay", "🔴 Live Stream"], index=0)

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

    run_button = start_live = stop_live = scan_button = replay_load = False
    selected_date = date.today()
    data_feed = "sip"
    watchlist_raw = ""
    scan_feed = "iex"

    if mode == "📅 Historical":
        today = date.today()
        # Default to today if it's a weekday, otherwise roll back to last Friday
        if today.weekday() < 5:
            def_d = today
        elif today.weekday() == 5:   # Saturday → Friday
            def_d = today - timedelta(days=1)
        else:                         # Sunday → Friday
            def_d = today - timedelta(days=2)
        selected_date = st.date_input("Trading Date", value=def_d, max_value=today,
                                       help="Pick a weekday (Mon–Fri). Today's intraday data is supported.")
        data_feed = st.selectbox("Data Feed", ["iex", "sip"], index=0,
                                  help="IEX = free, works on all accounts. SIP = full tape, requires a paid Alpaca data subscription.")
        run_button = st.button("🚀 Fetch & Analyze", use_container_width=True, type="primary")

    elif mode == "🎬 Replay":
        today = date.today()
        if today.weekday() < 5:
            def_d = today
        elif today.weekday() == 5:
            def_d = today - timedelta(days=1)
        else:
            def_d = today - timedelta(days=2)
        selected_date = st.date_input("Trading Date", value=def_d, max_value=today,
                                       help="Pick a trading day to replay.", key="replay_date_input")
        data_feed = st.selectbox("Data Feed", ["iex", "sip"], index=0,
                                  help="IEX = free. SIP = full tape, needs subscription.", key="replay_feed_sel")
        replay_load = st.button("📥 Load Day for Replay", use_container_width=True, type="primary")

        # ── Replay controls (shown once bars are loaded) ───────────────────────
        if st.session_state.replay_bars is not None:
            _rb      = st.session_state.replay_bars
            _max_idx = len(_rb) - 1
            _cur_idx = st.session_state.replay_bar_idx

            # Current bar's ET time
            _cur_ts  = _rb.index[_cur_idx]
            _cur_et  = pd.Timestamp(_cur_ts).tz_convert(EASTERN)
            _total_et = pd.Timestamp(_rb.index[_max_idx]).tz_convert(EASTERN)

            st.markdown(
                f'<div style="background:#16213e; border:1px solid #5c6bc0; border-radius:6px; '
                f'padding:8px 14px; margin:6px 0; font-family:monospace; font-size:16px; '
                f'color:#90caf9; text-align:center; letter-spacing:1px;">'
                f'🕐 {_cur_et.strftime("%H:%M")} ET &nbsp;|&nbsp; '
                f'Bar {_cur_idx + 1} / {_max_idx + 1}</div>',
                unsafe_allow_html=True,
            )

            # Bar slider
            replay_bar_idx = st.slider(
                "Bar (time)", min_value=0, max_value=_max_idx,
                value=_cur_idx, step=1,
                format="%d",
                key="replay_slider",
            )
            if replay_bar_idx != _cur_idx:
                st.session_state.replay_bar_idx = replay_bar_idx
                st.session_state.replay_playing = False
                st.rerun()

            # Playback controls
            _sp_label = {"Slow (1 bar/step)": 1, "Normal (2 bars/step)": 2,
                         "Fast (5 bars/step)": 5, "Turbo (10 bars/step)": 10}
            _sp_sel = st.selectbox("Speed", list(_sp_label.keys()), index=1,
                                    key="replay_speed_sel")
            st.session_state.replay_speed = _sp_label[_sp_sel]

            rc1, rc2, rc3, rc4 = st.columns(4)
            if rc1.button("⏮", help="Jump to start"):
                st.session_state.replay_bar_idx = 0
                st.session_state.replay_playing = False
                st.rerun()
            if rc2.button("◀", help="Step back"):
                st.session_state.replay_bar_idx = max(0, _cur_idx - st.session_state.replay_speed)
                st.session_state.replay_playing = False
                st.rerun()
            if rc3.button("▶" if not st.session_state.replay_playing else "⏸",
                          help="Play / Pause"):
                st.session_state.replay_playing = not st.session_state.replay_playing
                st.rerun()
            if rc4.button("▶▶", help="Step forward"):
                st.session_state.replay_bar_idx = min(_max_idx, _cur_idx + st.session_state.replay_speed)
                st.session_state.replay_playing = False
                st.rerun()

            if st.button("🗑 Clear / Load new day", use_container_width=True):
                st.session_state.replay_bars    = None
                st.session_state.replay_bar_idx = 0
                st.session_state.replay_playing = False
                st.rerun()

    else:
        live_feed = st.selectbox("Data Feed", ["iex", "sip"], index=0,
                                  help="IEX works on all accounts. SIP needs a subscription.")
        if not st.session_state.live_active:
            start_live = st.button("▶ Start Live Stream", use_container_width=True, type="primary")
        else:
            stop_live = st.button("⏹ Stop", use_container_width=True)
            st.success(f"🔴 Live: **{st.session_state.live_ticker}**")

    st.markdown("---")

    # ── Lunar Phase ───────────────────────────────────────────────────────────
    try:
        _l_icon, _l_phase, _l_mania = get_lunar_phase(date.today())
        _l_color = "#ff6d00" if _l_mania else "#5c6bc0"
        _l_note  = "🔥 Retail Mania Window" if _l_mania else "Neutral"
        st.markdown(
            f'<div style="background:{_l_color}11; border:1px solid {_l_color}55; '
            f'border-radius:6px; padding:6px 12px; margin:2px 0 6px 0; '
            f'display:flex; align-items:center; gap:8px;">'
            f'<span style="font-size:18px;">{_l_icon}</span>'
            f'<div><div style="font-size:11px; color:{_l_color}; font-weight:700;">'
            f'{_l_phase}</div>'
            f'<div style="font-size:10px; color:#888;">{_l_note}</div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    # ── Position Management ────────────────────────────────────────────────────
    st.header("📍 Position")
    _snap   = st.session_state.get("last_analysis_state") or {}
    _p_in   = st.session_state.position_in
    _p_tkr  = st.session_state.position_ticker
    _p_ent  = st.session_state.position_avg_entry
    _p_mfe  = st.session_state.position_peak_price
    _p_shr  = st.session_state.position_shares
    _p_strc = st.session_state.position_structure
    _cur    = _snap.get("price", 0.0)

    if _p_in:
        pnl_pct = (_cur - _p_ent) / _p_ent * 100 if _p_ent > 0 else 0
        pnl_dol = (_cur - _p_ent) * _p_shr if _p_shr > 0 else 0
        pnl_col = "#4caf50" if pnl_pct >= 0 else "#ef5350"
        st.markdown(
            f'<div style="background:{pnl_col}11; border:1px solid {pnl_col}55; '
            f'border-radius:6px; padding:10px 14px; margin-bottom:8px;">'
            f'<div style="font-size:11px; color:#888; margin-bottom:4px;">OPEN — {_p_tkr} × {_p_shr} sh</div>'
            f'<div style="font-size:13px; color:#ccc;">Entry: <b>${_p_ent:.2f}</b> &nbsp;|&nbsp; '
            f'MFE: <b>${_p_mfe:.2f}</b></div>'
            f'<div style="font-size:20px; font-weight:800; color:{pnl_col}; margin-top:4px;">'
            f'{"▲" if pnl_pct>=0 else "▼"} {abs(pnl_pct):.2f}%'
            f'{"  ($" + f"{pnl_dol:+.0f}" + ")" if _p_shr > 0 else ""}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        exit_px = st.number_input("Exit Price", value=_cur if _cur else _p_ent,
                                   step=0.01, format="%.2f", key="pos_exit_px")
        exit_struct = st.text_input("Actual Structure (opt.)", value="", key="pos_exit_struct",
                                    placeholder="e.g. Trend Day")
        if st.button("🔴 Exit Position", use_container_width=True, key="pos_exit_btn"):
            _realized = exit_position(exit_px, actual_structure=exit_struct or _p_strc)
            st.success(f"✅ Exited — Realized P&L: ${_realized:+.2f}" if _p_shr > 0
                       else "✅ Position closed.")
            st.rerun()
    else:
        _default_tkr = _snap.get("ticker", "")
        _default_strc = _snap.get("structure", "")
        _default_price = _snap.get("price", 0.0)
        e_tkr = st.text_input("Ticker", value=_default_tkr, key="pos_entry_tkr")
        e_px  = st.number_input("Entry Price", value=float(_default_price) if _default_price else 0.0,
                                 step=0.01, format="%.2f", key="pos_entry_px")
        e_shr = st.number_input("Shares", value=100, step=1, min_value=1, key="pos_entry_shr")
        e_strc = st.text_input("Structure at Entry", value=_default_strc, key="pos_entry_strc")
        if st.button("🟢 Enter Position", use_container_width=True,
                     key="pos_enter_btn", type="primary"):
            if e_tkr and e_px > 0:
                enter_position(e_tkr.upper(), e_px, e_shr, e_strc)
                st.success(f"✅ Entered {e_tkr.upper()} × {e_shr} sh @ ${e_px:.2f}")
                st.rerun()
            else:
                st.error("Enter a valid ticker and price.")

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

st.title("📊 Volume Profile Dashboard — Small Cap Stocks")

# ── Live Pulse Header ──────────────────────────────────────────────────────────
_las = st.session_state.get("last_analysis_state")
if _las:
    _lbl  = _las.get("structure", "")
    _tcs  = _las.get("tcs", 0.0)
    _rvol = _las.get("rvol")
    _sym  = _las.get("ticker", "")
    _pr   = _las.get("price", 0.0)
    _lc   = _las.get("label_color", "#90caf9")
    _rc   = _las.get("rvol_color", "#aaa")
    _runner = _las.get("is_runner", False)

    _rvol_str = f"{_rvol:.1f}×" if _rvol is not None else "—"
    _tcs_fill = ("linear-gradient(90deg,#FFD700,#00BFFF)" if _runner
                 else f"linear-gradient(90deg,#4caf50,#4caf50)" if _tcs >= 70
                 else f"linear-gradient(90deg,#ef5350,#ef5350)" if _tcs <= 30
                 else "linear-gradient(90deg,#ffa726,#ffa726)")

    st.markdown(f"""
    <div style="display:flex; gap:16px; flex-wrap:wrap; margin:0 0 4px 0;">
        <div style="flex:1; min-width:220px; background:linear-gradient(135deg,{_lc}22,{_lc}0a);
                    border-left:4px solid {_lc}; border-radius:8px; padding:12px 18px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase;
                        letter-spacing:1px; margin-bottom:4px;">Structure</div>
            <div style="font-size:20px; font-weight:800; color:{_lc};">{_lbl}</div>
            <div style="font-size:12px; color:#aaa; margin-top:2px;">{_sym} · ${_pr:.2f}</div>
        </div>
        <div style="flex:1; min-width:180px; background:#12122288;
                    border-left:4px solid #90caf9; border-radius:8px; padding:12px 18px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase;
                        letter-spacing:1px; margin-bottom:6px;">Trend Confidence (TCS)</div>
            <div style="background:#2a2a4a; border-radius:6px; height:10px; overflow:hidden; margin-bottom:6px;">
                <div style="width:{min(_tcs,100):.0f}%; background:{_tcs_fill};
                            height:100%; border-radius:6px;"></div>
            </div>
            <div style="font-size:22px; font-weight:900; color:{'#FFD700' if _runner else '#90caf9'};">{_tcs:.0f}%</div>
        </div>
        <div style="flex:1; min-width:180px; background:#12122288;
                    border-left:4px solid {_rc}; border-radius:8px; padding:12px 18px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase;
                        letter-spacing:1px; margin-bottom:4px;">RVOL</div>
            <div style="font-size:22px; font-weight:900; color:{_rc};">{_rvol_str}</div>
            <div style="font-size:11px; color:#666; margin-top:2px;">
                {'⚡ RUNNER MODE' if _runner else ('🔥 In Play' if _rvol and _rvol > 3 else '— Normal')}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Alert Banner
    if _runner or _tcs >= 80:
        st.markdown(
            '<div style="background:#FFD70022; border:1px solid #FFD700; border-radius:6px; '
            'padding:8px 18px; font-size:14px; font-weight:700; color:#FFD700; margin:4px 0 8px 0;">'
            f'🚀 STOCK IN PLAY — {_sym} | TCS {_tcs:.0f}% | RVOL {_rvol_str}</div>',
            unsafe_allow_html=True
        )
    elif _tcs <= 30:
        st.markdown(
            '<div style="background:#ef535022; border:1px solid #ef5350; border-radius:6px; '
            'padding:8px 18px; font-size:14px; font-weight:700; color:#ef5350; margin:4px 0 8px 0;">'
            f'⚠ CAUTION — Low Conviction | TCS {_tcs:.0f}% | Chop Risk Active</div>',
            unsafe_allow_html=True
        )

_STRUCTURE_COLORS_MAP = {
    "trend":       "#ff9800",
    "bear":        "#ff5722",
    "double":      "#00bcd4",
    "non":         "#78909c",
    "normal var":  "#aed581",
    "variation":   "#aed581",
    "neutral ext": "#7e57c2",
    "neutral":     "#80cbc4",
    "normal":      "#66bb6a",
    "balanced":    "#66bb6a",
}

def _structure_color(label_str):
    """Return a color for a structure label string."""
    s = label_str.lower()
    for key, col in _STRUCTURE_COLORS_MAP.items():
        if key in s:
            return col
    return "#5c6bc0"

def _clean_structure_label(raw):
    """Strip emojis + extra words for a readable short label."""
    import re
    s = re.sub(r"[^\w\s()/\-]", "", str(raw)).strip()
    # Trim very long labels
    return s[:30] if len(s) > 30 else s


_BATCH_DEFAULT = """\
3/30: ANNA, SST, ASTC, BFRG, UGRO, JCSE, EEIQ, ELAB
3/27: VSA, ARTL, GVH, GCTK
3/26: AIFF, EEIQ, GLND, FCHL, VSA
3/25: VCX, RMSG, UGRO, SATL, CODX, QNRX, FEED
3/24: SATL, FEED, RBNE, PAVS, VCX, UGRO, ANNA
3/23: AHMA, UGRO, VCX, BIAF, PTLE
3/20: ARTL, ANNA, CODX
3/19: LNKS, SWMR, ACXP, GOAI, SER, VCX, CHNR
3/18: ARTL, AIM, SWMR, MTVA
3/17: UCAR, LNAI, BIAF, CREG, EDSA, SWMR
3/16: WNW, HCWB"""


def _parse_batch_pairs(text: str) -> list[tuple]:
    """Parse 'M/D: T1, T2, ...' lines into [(ticker, date), ...] for year 2026."""
    import re
    pairs = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        date_part, tickers_part = line.split(":", 1)
        date_part = date_part.strip()
        m = re.match(r"(\d{1,2})/(\d{1,2})", date_part)
        if not m:
            continue
        mo, dy = int(m.group(1)), int(m.group(2))
        try:
            trade_date = date(2026, mo, dy)
        except ValueError:
            continue
        for t in tickers_part.split(","):
            t = t.strip().upper()
            if t:
                pairs.append((t, trade_date))
    return pairs


def run_single_backtest(api_key, secret_key, ticker, trade_date, feed="iex", num_bins=100):
    """Full pipeline for one ticker/date: fetch → classify → brain → log."""
    result = {"ticker": ticker, "date": str(trade_date),
              "predicted": "—", "actual": "—", "correct": "—", "status": "OK"}
    try:
        df = fetch_bars(api_key, secret_key, ticker, trade_date, feed=feed)
        if df.empty or len(df) < 10:
            result["status"] = "No data"
            return result

        bin_centers, vap, poc_price = compute_volume_profile(df, num_bins)
        ib_high, ib_low = compute_initial_balance(df)
        if ib_high is None or ib_low is None:
            result["status"] = "No IB data"
            return result

        label, color, detail, insight = classify_day_structure(
            df, bin_centers, vap, ib_high, ib_low, poc_price
        )
        result["actual"] = label

        # Simulate MarketBrain with the full day's bars + rich signals
        brain = MarketBrain()
        try:
            _bt_ivp, _ = compute_ib_volume_stats(df, ib_high, ib_low)
        except Exception:
            _bt_ivp = None
        _bt_has_dd = _detect_double_distribution(bin_centers, vap) is not None
        brain.update(df, ib_vol_pct=_bt_ivp, poc_price=poc_price,
                     has_double_dist=_bt_has_dd)
        prediction = brain.prediction
        result["predicted"] = prediction

        if not brain.ib_set or prediction == "Analyzing IB…":
            result["status"] = "IB incomplete"
            return result

        # Build compare_key and dedup check
        ck = f"{ticker}_{trade_date}_{float(ib_high):.4f}_{float(ib_low):.4f}"
        if os.path.exists(TRACKER_FILE):
            try:
                _chk = pd.read_csv(TRACKER_FILE, encoding="utf-8")
                if "compare_key" in _chk.columns and (_chk["compare_key"] == ck).any():
                    result["status"] = "Already logged"
                    # Still show correct/wrong from existing row
                    _row = _chk[_chk["compare_key"] == ck]
                    if not _row.empty and "correct" in _row.columns:
                        result["correct"] = str(_row["correct"].iloc[0])
                    return result
            except Exception:
                pass

        log_accuracy_entry(ticker, prediction, label, compare_key=ck)
        result["correct"] = ("✅" if _strip_emoji(prediction) in _strip_emoji(label)
                             or _strip_emoji(label) in _strip_emoji(prediction) else "❌")
    except Exception as e:
        result["status"] = f"Error: {str(e)[:60]}"
    return result


def render_tracker_tab():
    """Render the Accuracy Tracker tab — structure distribution + Predicted vs Actual history."""
    st.markdown("## 🧠 MarketBrain — Accuracy Tracker")
    st.caption("All-time structure distribution and brain prediction accuracy.")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 0 — Batch Backtest Runner
    # ══════════════════════════════════════════════════════════════════════════
    with st.expander("🔬 Batch Backtest — Feed Historical Tickers", expanded=False):
        st.caption(
            "Fetches each ticker/date pair via Alpaca, classifies structure, records Brain "
            "prediction vs actual. Uses credentials entered in the sidebar. "
            "Skips any pair already in the tracker. Add more lines in M/D: T1, T2 format."
        )
        _bt_pairs_text = st.text_area(
            "Ticker / Date pairs", value=_BATCH_DEFAULT, height=220, key="bt_pairs_input"
        )
        _bt_feed = st.selectbox(
            "Data feed", ["iex", "sip"], index=0, key="bt_feed_select",
            help="IEX = free tier (recommended for backtests). SIP = full tape."
        )
        _bt_run = st.button("▶ Run Batch Backtest", type="primary",
                            use_container_width=True, key="bt_run_btn")

        if _bt_run:
            # api_key and secret_key come from the outer sidebar scope
            if not api_key or not secret_key:
                st.error("Enter your Alpaca API Key and Secret Key in the sidebar first.")
            else:
                _pairs = _parse_batch_pairs(_bt_pairs_text)
                if not _pairs:
                    st.warning("No valid ticker/date pairs found. Use format: 3/30: ANNA, SST, BFRG")
                else:
                    st.info(f"Processing {len(_pairs)} pairs… this may take 1–2 minutes.")
                    _prog   = st.progress(0.0, text="Starting…")
                    _bt_results = []
                    for _i, (_tk, _dt) in enumerate(_pairs):
                        _prog.progress((_i + 1) / len(_pairs),
                                       text=f"Fetching {_tk} {_dt} ({_i+1}/{len(_pairs)})…")
                        _r = run_single_backtest(api_key, secret_key, _tk, _dt,
                                                 feed=_bt_feed, num_bins=100)
                        _bt_results.append(_r)
                    _prog.empty()
                    # Persist results so they survive the page rerun
                    st.session_state["bt_last_results"] = _bt_results

        # ── Always render results if available ────────────────────────────────
        if st.session_state.get("bt_last_results"):
            _rdf = pd.DataFrame(st.session_state["bt_last_results"])
            _ok      = (_rdf["status"] == "OK").sum()
            _dup     = (_rdf["status"] == "Already logged").sum()
            _no_data = _rdf["status"].str.startswith("No data").sum()
            _no_ib   = _rdf["status"].str.startswith("IB").sum()
            _errs    = len(_rdf) - _ok - _dup - _no_data - _no_ib
            _correct = (_rdf["correct"] == "✅").sum()
            _wrong   = (_rdf["correct"] == "❌").sum()
            _logged  = _ok + _dup

            _acc_str = (f"  •  Batch accuracy: **{_correct}/{_correct+_wrong}** "
                        f"({_correct/((_correct+_wrong) or 1)*100:.0f}%)"
                        if (_correct + _wrong) > 0 else "")
            st.success(
                f"✅ **{_ok}** new logged  •  "
                f"🔁 **{_dup}** already existed  •  "
                f"📭 **{_no_data}** no data  •  "
                f"⏱ **{_no_ib}** IB incomplete  •  "
                f"⚠️ **{_errs}** errors"
                + _acc_str
            )
            st.caption(
                "💡 'No data' means Alpaca IEX doesn't carry historical minute bars for that "
                "ticker on that date. Very small OTC stocks often only appear on SIP. "
                "Try switching the feed to **sip** if you have an Alpaca paid subscription."
            )

            # Color rows by result
            def _bt_style(row):
                s = row.get("correct", "—")
                if s == "✅":  return ["background-color:#4caf5022"] * len(row)
                if s == "❌":  return ["background-color:#ef535022"] * len(row)
                return [""] * len(row)

            try:
                st.dataframe(
                    _rdf[["ticker","date","predicted","actual","correct","status"]]
                      .style.apply(_bt_style, axis=1),
                    use_container_width=True, hide_index=True
                )
            except Exception:
                st.dataframe(
                    _rdf[["ticker","date","predicted","actual","correct","status"]],
                    use_container_width=True, hide_index=True
                )
            if st.button("🗑 Clear batch results", key="bt_clear_btn"):
                st.session_state.pop("bt_last_results", None)
                st.rerun()

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 0b — High Conviction Watchlist (top prob ≥ 80%)
    # ══════════════════════════════════════════════════════════════════════════
    _STRUCT_COLORS_HC = {
        "Trend":        "#ff9800",
        "Dbl Dist":     "#00bcd4",
        "Non-Trend":    "#78909c",
        "Normal":       "#66bb6a",
        "Nrml Var":     "#aed581",
        "Neutral":      "#80cbc4",
        "Ntrl Extreme": "#7e57c2",
    }

    st.markdown("### 🎯 High Conviction Calls  <span style='font-size:13px;color:#888;font-weight:400'>≥ 75% structure probability</span>",
                unsafe_allow_html=True)
    _hc_df = load_high_conviction_log()

    if _hc_df.empty:
        st.info(
            "No high-conviction calls logged yet. "
            "Run a historical or live analysis on any ticker — any day where the "
            "top structure probability ≥ 75% is automatically captured here."
        )
    else:
        # ── Summary pills row ─────────────────────────────────────────────────
        _hc_total   = len(_hc_df)
        _hc_structs = _hc_df["structure"].value_counts()
        _pill_html  = ""
        for _s, _cnt in _hc_structs.items():
            _c = _STRUCT_COLORS_HC.get(_s, "#888888")
            _pill_html += (
                f'<span style="background:{_c}33;color:{_c};border:1px solid {_c}66;'
                f'border-radius:12px;padding:3px 10px;margin:3px;'
                f'font-size:13px;font-weight:600;display:inline-block;">'
                f'{_s} ({_cnt})</span>'
            )
        st.markdown(
            f'<div style="margin-bottom:8px;">'
            f'<b style="color:#ccc;">{_hc_total} total</b> &nbsp;·&nbsp; '
            + _pill_html + "</div>",
            unsafe_allow_html=True,
        )

        # ── Table ─────────────────────────────────────────────────────────────
        def _hc_row_style(row):
            _c = _STRUCT_COLORS_HC.get(row.get("structure", ""), "#888888")
            return [f"background-color:{_c}18;"] * len(row)

        _hc_display = _hc_df[["ticker","date","structure","prob_pct",
                               "ib_high","ib_low","poc_price"]].copy()
        _hc_display.columns = ["Ticker","Date","Structure","Probability %",
                                "IB High","IB Low","POC"]
        _hc_display["Probability %"] = _hc_display["Probability %"].apply(
            lambda x: f"{x:.1f}%"
        )

        try:
            st.dataframe(
                _hc_display.style.apply(_hc_row_style, axis=1),
                use_container_width=True, hide_index=True
            )
        except Exception:
            st.dataframe(_hc_display, use_container_width=True, hide_index=True)

        c1, c2 = st.columns([3, 1])
        with c2:
            if st.button("🗑 Clear list", key="hc_clear_btn"):
                try:
                    os.remove(HICONS_FILE)
                except Exception:
                    pass
                st.rerun()

    st.markdown("---")
    df = load_accuracy_tracker()

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — All-Time Structure Distribution
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 📊 All-Time Structure Distribution")
    st.caption("Every structure classified since you started using the dashboard (from accuracy tracker log).")

    if df.empty or "actual" not in df.columns:
        st.info("No data yet — run analyses to start populating the distribution.")
    else:
        _dist = df["actual"].dropna()
        _dist = _dist[_dist.str.strip() != ""]

        if _dist.empty:
            st.info("No actual structure data logged yet.")
        else:
            # Count + percent
            _counts = _dist.value_counts().reset_index()
            _counts.columns = ["structure", "count"]
            _counts["pct"] = (_counts["count"] / _counts["count"].sum() * 100).round(1)
            _counts["label_clean"] = _counts["structure"].apply(_clean_structure_label)
            _counts["color"] = _counts["structure"].apply(_structure_color)
            _counts = _counts.sort_values("pct", ascending=True)   # horizontal bar → ascending

            # ── Pill badges row ────────────────────────────────────────────────
            pills_html = '<div style="display:flex; flex-wrap:wrap; gap:8px; margin:8px 0 14px 0;">'
            for _, row in _counts.sort_values("pct", ascending=False).iterrows():
                c = row["color"]
                pills_html += (
                    f'<span style="background:{c}22; border:1px solid {c}55; border-radius:20px; '
                    f'padding:4px 12px; font-size:12px; color:{c}; white-space:nowrap;">'
                    f'<b>{row["pct"]:.0f}%</b> {row["label_clean"]} '
                    f'<span style="color:#555; font-size:10px;">({int(row["count"])})</span></span>'
                )
            pills_html += "</div>"
            st.markdown(pills_html, unsafe_allow_html=True)

            # ── Horizontal bar chart ──────────────────────────────────────────
            fig_dist = go.Figure(go.Bar(
                x=_counts["pct"],
                y=_counts["label_clean"],
                orientation="h",
                marker_color=_counts["color"].tolist(),
                text=[f"  {p:.1f}%  ({n})" for p, n in zip(_counts["pct"], _counts["count"])],
                textposition="outside",
                cliponaxis=False,
            ))
            fig_dist.update_layout(
                paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                font=dict(color="#e0e0e0"),
                height=max(240, len(_counts) * 44 + 60),
                xaxis=dict(range=[0, min(100, _counts["pct"].max() * 1.25)],
                           gridcolor="#2a2a4a", title="% of all sessions",
                           ticksuffix="%"),
                yaxis=dict(gridcolor="#2a2a4a", tickfont=dict(size=12)),
                margin=dict(l=10, r=80, t=20, b=40),
            )
            st.plotly_chart(fig_dist, use_container_width=True)

            # ── Trend vs Balance split ────────────────────────────────────────
            _directional_keys = ["trend", "bear", "double", "variation"]
            _balanced_keys    = ["normal", "neutral", "non", "balanced"]

            def _classify_side(s):
                sl = s.lower()
                if any(k in sl for k in _directional_keys):
                    return "Directional"
                if any(k in sl for k in _balanced_keys):
                    return "Balanced"
                return "Other"

            _sides = _dist.apply(_classify_side).value_counts()
            _total_sides = _sides.sum()
            dir_pct = _sides.get("Directional", 0) / _total_sides * 100
            bal_pct = _sides.get("Balanced", 0) / _total_sides * 100

            d1, d2, d3 = st.columns(3)
            d1.metric("📈 Directional Days", f"{dir_pct:.0f}%",
                      help="Trend Day, Trend Bear, Double Distribution, Normal Variation")
            d2.metric("⚖️ Balanced Days",    f"{bal_pct:.0f}%",
                      help="Normal, Neutral, Neutral Extreme, Non-Trend")
            d3.metric("📋 Total Sessions",   int(_total_sides))

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — Brain Accuracy
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 🎯 Brain Prediction Accuracy")

    if df.empty:
        st.info("No accuracy data yet — run analyses after 10:30 ET to start logging brain predictions.")
        return

    total    = len(df)
    correct  = (df["correct"] == "✅").sum()
    acc_rate = correct / total * 100 if total > 0 else 0
    wrong    = total - correct

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Predictions", total)
    c2.metric("Correct", f"{correct}  ({acc_rate:.0f}%)")
    c3.metric("Wrong",   wrong)
    c4.metric("Accuracy Rate", f"{acc_rate:.1f}%")

    # ── Accuracy by predicted structure ──────────────────────────────────────
    if "predicted" in df.columns and "correct" in df.columns:
        grouped = df.groupby("predicted").apply(
            lambda g: pd.Series({
                "total":   len(g),
                "correct": (g["correct"] == "✅").sum(),
                "acc":     (g["correct"] == "✅").sum() / len(g) * 100,
            })
        ).reset_index()
        grouped = grouped.sort_values("acc", ascending=False)

        st.markdown("**Accuracy by Predicted Structure**")
        bar_colors = [
            "#4caf50" if a >= 60 else "#ffa726" if a >= 40 else "#ef5350"
            for a in grouped["acc"]
        ]
        fig_acc = go.Figure(go.Bar(
            x=grouped["predicted"].apply(_clean_structure_label),
            y=grouped["acc"].round(1),
            marker_color=bar_colors,
            text=[f"{a:.0f}%<br>({int(c)}/{int(t)})"
                  for a, c, t in zip(grouped["acc"], grouped["correct"], grouped["total"])],
            textposition="outside",
        ))
        fig_acc.update_layout(
            paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"), height=320,
            yaxis=dict(range=[0, 115], gridcolor="#2a2a4a", title="Accuracy %"),
            xaxis=dict(gridcolor="#2a2a4a"),
            margin=dict(t=20, b=60, l=50, r=20),
        )
        st.plotly_chart(fig_acc, use_container_width=True)

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 3 — Adaptive Learning Status
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 🔬 Adaptive Learning Status")
    st.caption(
        f"Brain recalibrates its probability weights every {_RECALIBRATE_EVERY} comparisons. "
        f"Structures with ≥5 samples are eligible. Multiplier > 1.0 = trusted; < 1.0 = confidence reduced."
    )

    _ws_rows = brain_weights_summary()
    _raw_w   = load_brain_weights()

    if not _ws_rows:
        st.info(f"Learning begins once at least 5 comparisons are logged for any structure. "
                f"Full recalibration fires every {_RECALIBRATE_EVERY} entries.")
    else:
        # ── Weight bar chart ──────────────────────────────────────────────────
        _wdf = pd.DataFrame(_ws_rows)
        _bar_colors = [
            "#4caf50" if m >= 1.3 else "#26a69a" if m >= 1.0 else
            "#ffa726" if m >= 0.7 else "#ef5350"
            for m in _wdf["Multiplier"]
        ]
        fig_w = go.Figure(go.Bar(
            x=_wdf["Multiplier"],
            y=_wdf["Structure"].apply(_clean_structure_label),
            orientation="h",
            marker_color=_bar_colors,
            text=[f"  {m:.2f}×  ({a:.0f}% acc / {n} samples)"
                  for m, a, n in zip(_wdf["Multiplier"], _wdf["Accuracy"], _wdf["Samples"])],
            textposition="outside",
            cliponaxis=False,
        ))
        fig_w.add_vline(x=1.0, line_dash="dash", line_color="#5c6bc0",
                        annotation_text="Baseline", annotation_position="top")
        fig_w.update_layout(
            paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"),
            height=max(220, len(_wdf) * 44 + 60),
            xaxis=dict(range=[0.0, 1.8], gridcolor="#2a2a4a",
                       title="Probability Multiplier"),
            yaxis=dict(gridcolor="#2a2a4a"),
            margin=dict(l=10, r=120, t=20, b=40),
        )
        st.plotly_chart(fig_w, use_container_width=True)

        # ── Summary table ──────────────────────────────────────────────────────
        _wdf_disp = _wdf[["Structure", "Samples", "Accuracy", "Multiplier", "Status"]].copy()
        _wdf_disp["Accuracy"] = _wdf_disp["Accuracy"].apply(lambda x: f"{x:.1f}%")
        _wdf_disp["Multiplier"] = _wdf_disp["Multiplier"].apply(lambda x: f"{x:.3f}×")
        st.dataframe(_wdf_disp, use_container_width=True, hide_index=True)

        # ── Next recalibration countdown ──────────────────────────────────────
        _current_n = len(df) if not df.empty else 0
        _next_recal = _RECALIBRATE_EVERY - (_current_n % _RECALIBRATE_EVERY)
        if _next_recal == _RECALIBRATE_EVERY:
            st.success(f"✅ Weights just recalibrated at {_current_n} entries.")
        else:
            _entry_word = "entry" if _next_recal == 1 else "entries"
            st.info(f"🔄 Next recalibration in **{_next_recal}** more {_entry_word} "
                    f"({_current_n} logged so far).")

        # ── Manual recalibrate button ─────────────────────────────────────────
        if st.button("⚡ Recalibrate Now", help="Force immediate weight update from all logged data"):
            _new_w = recalibrate_brain_weights()
            st.success("Weights updated! Brain probabilities will use the new calibration on next analysis.")
            st.rerun()

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 4 — Full history table
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### 📋 Full History")
    display_cols = ["timestamp", "symbol", "predicted", "actual", "correct",
                    "entry_price", "exit_price", "mfe"]
    disp = df[[c for c in display_cols if c in df.columns]].copy()
    if "timestamp" in disp.columns:
        disp = disp.sort_values("timestamp", ascending=False)

    def _style_row(row):
        color = "#4caf5022" if row.get("correct") == "✅" else "#ef535022"
        return [f"background-color: {color}"] * len(row)

    try:
        styled = disp.style.apply(_style_row, axis=1)
        st.dataframe(styled, use_container_width=True, height=320)
    except Exception:
        st.dataframe(disp, use_container_width=True, height=320)

    csv_str = df.to_csv(index=False)
    st.download_button(
        "⬇ Download Tracker CSV", data=csv_str,
        file_name="accuracy_tracker.csv", mime="text/csv"
    )


tab_chart, tab_scan, tab_journal, tab_tracker = st.tabs(
    ["📈 Main Chart", "🔍 Scanner", "📖 Journal", "🧠 Tracker"]
)

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

with tab_chart:
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
                            now_et = datetime.now(EASTERN)
                            pre_mkt = (selected_date >= now_et.date() and
                                       now_et <= EASTERN.localize(datetime(selected_date.year, selected_date.month, selected_date.day, 9, 30)))
                            if pre_mkt:
                                st.warning("Market hasn't opened yet (9:30 AM ET). Come back once trading starts and bars are available.")
                            elif data_feed == "sip":
                                st.warning(f"No data for **{ticker}** on {selected_date} via SIP. Try switching to IEX, or confirm the date was a trading day.")
                            else:
                                st.warning(f"No data for **{ticker}** on {selected_date} via IEX. Small-caps may be absent on IEX — try SIP.")
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
                                    lookback_days=50, feed=data_feed)
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

                            # ── Daily level confluence (prior-day H/L) ─────────────
                            try:
                                _ph, _pl = fetch_daily_levels(
                                    api_key, secret_key, ticker, selected_date)
                                st.session_state.daily_levels_cache = {
                                    "ticker":    ticker,
                                    "prev_high": _ph,
                                    "prev_low":  _pl,
                                }
                            except Exception:
                                st.session_state.daily_levels_cache = {}

                            # ── Invalidate StockTwits cache so fresh sentiment
                            #    is fetched for this Fetch & Analyze trigger ──
                            st.session_state.stocktwits_cache.pop(ticker, None)
                            render_analysis(df, num_bins, ticker,
                                            f"{ticker} — Volume Profile | {selected_date.strftime('%B %d, %Y')}",
                                            avg_daily_vol=avg_vol,
                                            sector_bonus=sector_bonus,
                                            sector_etf=sector_etf,
                                            intraday_curve=curve,
                                            is_live=False)
                            render_log_entry_ui()
                    except Exception as e:
                        err = str(e)
                        if "forbidden" in err.lower() or "403" in err or "unauthorized" in err.lower():
                            st.error("Authentication failed — check your API Key and Secret Key.")
                        elif "subscription" in err.lower() or "not entitled" in err.lower() or "422" in err:
                            if data_feed == "sip":
                                st.warning("SIP feed requires a paid Alpaca subscription. Retrying with IEX…")
                                try:
                                    df2 = fetch_bars(api_key, secret_key, ticker, selected_date, feed="iex")
                                    if df2.empty:
                                        st.error(f"No data for **{ticker}** on {selected_date} via IEX either. Confirm the date was a trading day and the ticker is valid.")
                                    else:
                                        st.success(f"Loaded **{len(df2)}** 1-min bars via IEX (auto-switched from SIP).")
                                        try:
                                            _fph, _fpl = fetch_daily_levels(
                                                api_key, secret_key, ticker, selected_date)
                                            st.session_state.daily_levels_cache = {
                                                "ticker":    ticker,
                                                "prev_high": _fph,
                                                "prev_low":  _fpl,
                                            }
                                        except Exception:
                                            st.session_state.daily_levels_cache = {}
                                        # IEX fallback also counts as a fresh user trigger
                                        st.session_state.stocktwits_cache.pop(ticker, None)
                                        render_analysis(df2, num_bins, ticker,
                                                        f"{ticker} — Volume Profile | {selected_date.strftime('%B %d, %Y')} (IEX)",
                                                        avg_daily_vol=None, sector_bonus=0.0,
                                                        sector_etf=sector_etf, intraday_curve=None,
                                                        is_live=False)
                                        render_log_entry_ui()
                                except Exception as e2:
                                    st.error(f"IEX fallback also failed: {e2}")
                            else:
                                st.error("Not subscribed to IEX feed — check your Alpaca account.")
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

    # ── Replay mode ────────────────────────────────────────────────────────────
    elif mode == "🎬 Replay":
        # ── Load full-day bars on demand ──────────────────────────────────────
        if replay_load:
            if not api_key or not secret_key:
                st.error("Enter your Alpaca credentials in the sidebar.")
            elif not ticker:
                st.error("Enter a ticker symbol.")
            elif selected_date.weekday() >= 5:
                st.error("Selected date is a weekend. Pick a weekday.")
            else:
                with st.spinner(f"Loading full day for **{ticker}** on {selected_date} ({data_feed.upper()})..."):
                    try:
                        _rdf = fetch_bars(api_key, secret_key, ticker, selected_date, feed=data_feed)
                        if _rdf.empty:
                            st.error("No bars returned. Check the ticker/date/feed.")
                        else:
                            st.session_state.replay_bars    = _rdf
                            st.session_state.replay_bar_idx = 0
                            st.session_state.replay_playing = False
                            st.session_state.replay_ticker  = ticker
                            st.session_state.replay_date    = selected_date
                            # Pre-fetch baselines for the replay session
                            try:
                                st.session_state.replay_avg_vol = fetch_avg_daily_volume(
                                    api_key, secret_key, ticker, selected_date)
                            except Exception:
                                st.session_state.replay_avg_vol = None
                            try:
                                st.session_state.replay_intraday_curve = build_rvol_intraday_curve(
                                    api_key, secret_key, ticker, selected_date,
                                    lookback_days=50, feed=data_feed)
                            except Exception:
                                st.session_state.replay_intraday_curve = None
                            try:
                                _etf = fetch_etf_pct_change(
                                    api_key, secret_key, sector_etf, selected_date, feed=data_feed)
                                st.session_state.replay_sector_bonus = 10.0 if _etf > 1.0 else 0.0
                            except Exception:
                                st.session_state.replay_sector_bonus = 0.0
                            # ── Daily level confluence (prior-day H/L) ───────────
                            try:
                                _rph, _rpl = fetch_daily_levels(
                                    api_key, secret_key, ticker, selected_date)
                                st.session_state.daily_levels_cache = {
                                    "ticker":    ticker,
                                    "prev_high": _rph,
                                    "prev_low":  _rpl,
                                }
                            except Exception:
                                st.session_state.daily_levels_cache = {}
                            # Reset Brain state for fresh replay
                            for _bk in ("brain_ib_high", "brain_ib_low", "brain_ib_set",
                                        "brain_high_touched", "brain_low_touched", "brain_predicted"):
                                st.session_state[_bk] = _DEFAULTS[_bk]
                            st.success(f"✅ Loaded {len(_rdf)} bars — use the slider and controls in the sidebar to replay.")
                            st.rerun()
                    except Exception as _e:
                        st.error(f"Load error: {_e}")

        # ── Run analysis for current replay bar ───────────────────────────────
        if st.session_state.replay_bars is not None:
            _rdf    = st.session_state.replay_bars
            _ridx   = st.session_state.replay_bar_idx
            _rtk    = st.session_state.replay_ticker
            _rdate  = st.session_state.replay_date
            _df_now = _rdf.iloc[:_ridx + 1]   # only bars up to current time

            _cur_et  = pd.Timestamp(_rdf.index[_ridx]).tz_convert(EASTERN)
            _total_et = pd.Timestamp(_rdf.index[-1]).tz_convert(EASTERN)

            # Clock banner
            st.markdown(
                f'<div style="background:#0f3460; border:1px solid #5c6bc0; border-radius:8px; '
                f'padding:10px 20px; margin-bottom:10px; display:flex; align-items:center; gap:16px;">'
                f'<span style="font-size:22px; font-family:monospace; color:#90caf9; font-weight:700;">'
                f'🎬 {_cur_et.strftime("%H:%M")} ET</span>'
                f'<span style="font-size:12px; color:#888;">Bar {_ridx + 1} / {len(_rdf)} &nbsp;|&nbsp; '
                f'{_rtk} &nbsp;|&nbsp; {_rdate}</span>'
                f'<span style="font-size:11px; color:#555; margin-left:auto;">'
                f'through {_total_et.strftime("%H:%M")} ET total</span></div>',
                unsafe_allow_html=True,
            )

            if len(_df_now) < 2:
                st.info("Need at least 2 bars for a full analysis — advance the slider forward.")
            else:
                render_analysis(
                    _df_now, num_bins, _rtk,
                    f"🎬 Replay — {_rtk} | {_cur_et.strftime('%H:%M ET')} | {_rdate}",
                    is_ib_live=(_cur_et.time() <= dtime(10, 30)),
                    avg_daily_vol=st.session_state.replay_avg_vol,
                    sector_bonus=st.session_state.replay_sector_bonus,
                    sector_etf=sector_etf,
                    intraday_curve=st.session_state.replay_intraday_curve,
                    is_live=False,
                )
                render_log_entry_ui()
        else:
            st.info("👈 Pick a date and click **📥 Load Day for Replay** in the sidebar.")
            rc1, rc2 = st.columns(2)
            with rc1:
                st.markdown("**How Replay works**")
                st.markdown(
                    "- Fetches the full day's bars once\n"
                    "- Shows only bars up to the selected time\n"
                    "- All analysis (Volume Profile, Structure, TCS, Brain) updates in real-time\n"
                    "- Use the slider to jump to any moment, or ▶ Play to auto-advance"
                )
            with rc2:
                st.markdown("**Uses**")
                st.markdown(
                    "- Review your trade decisions at the exact moment you entered\n"
                    "- See how the IB formed bar by bar\n"
                    "- Practice reading structure before it becomes obvious\n"
                    "- Study how RVOL evolved during the session"
                )

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
                            lookback_days=50, feed=live_feed)
                    except Exception:
                        curve = None
                    st.session_state.rvol_intraday_curve = curve

                    try:
                        etf_chg = fetch_etf_pct_change(
                            api_key, secret_key, sector_etf, today, feed=live_feed)
                    except Exception:
                        etf_chg = 0.0
                    st.session_state.sector_pct_chg = etf_chg

                    # ── Daily level confluence (prior-day H/L) ────────────────
                    try:
                        _lph, _lpl = fetch_daily_levels(api_key, secret_key, ticker, today)
                        st.session_state.daily_levels_cache = {
                            "ticker":    ticker,
                            "prev_high": _lph,
                            "prev_low":  _lpl,
                        }
                    except Exception:
                        st.session_state.daily_levels_cache = {}

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
                render_log_entry_ui()
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

# ── Journal tab ───────────────────────────────────────────────────────────────
with tab_journal:
    render_journal_tab()

# ── Tracker tab ───────────────────────────────────────────────────────────────
with tab_tracker:
    render_tracker_tab()

# ── Auto-refresh loop for live mode ───────────────────────────────────────────
if mode == "🔴 Live Stream" and st.session_state.live_active:
    time.sleep(2)
    st.rerun()

# ── Replay auto-advance loop ──────────────────────────────────────────────────
if (mode == "🎬 Replay"
        and st.session_state.replay_playing
        and st.session_state.replay_bars is not None):
    _rdf_all = st.session_state.replay_bars
    _max     = len(_rdf_all) - 1
    _nxt     = min(_max, st.session_state.replay_bar_idx + st.session_state.replay_speed)
    if _nxt >= _max:
        st.session_state.replay_playing = False   # reached end, stop
    st.session_state.replay_bar_idx = _nxt
    time.sleep(0.5)   # ~2 bars/sec at Normal speed
    st.rerun()
