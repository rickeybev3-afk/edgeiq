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
import json
import csv
import os
from collections import deque

STATE_FILE   = "trade_state.json"
TRACKER_FILE = "accuracy_tracker.csv"
WEIGHTS_FILE = "brain_weights.json"   # adaptive per-structure multipliers

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
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Restore today's brain accuracy counters from CSV on first load ─────────────
# This makes the session badge survive page reloads while keeping the counter
# scoped to today (so every new trading day starts fresh automatically).
if st.session_state.brain_session_total == 0 and os.path.exists(TRACKER_FILE):
    try:
        _restore_df = pd.read_csv(TRACKER_FILE)
        if "timestamp" in _restore_df.columns and not _restore_df.empty:
            _today_str = datetime.now(EASTERN).strftime("%Y-%m-%d")
            _today_rows = _restore_df[
                _restore_df["timestamp"].astype(str).str.startswith(_today_str)
            ]
            if not _today_rows.empty and "correct" in _today_rows.columns:
                _r_total   = len(_today_rows)
                _r_correct = (_today_rows["correct"] == "✅").sum()
                st.session_state.brain_session_total   = int(_r_total)
                st.session_state.brain_session_correct = int(_r_correct)
                # Restore last compare_key so we don't re-log on first run
                if "compare_key" in _today_rows.columns:
                    _last_key = _today_rows["compare_key"].dropna().iloc[-1] \
                                if not _today_rows["compare_key"].dropna().empty else ""
                    st.session_state.brain_last_compared = str(_last_key)
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
    def update(self, df, rvol=None):
        """Ingest fresh bar data, update IB, and re-predict."""
        if df.empty:
            return
        rvol = rvol or 0.0
        ib_end = df.index[0].replace(hour=10, minute=30, second=0)

        # Track IB extremes minute-by-minute during first hour
        ib_df = df[df.index <= ib_end]
        if not ib_df.empty:
            self.ib_high = max(self.ib_high, float(ib_df["high"].max()))
            self.ib_low  = min(self.ib_low,  float(ib_df["low"].min()))

        last_time = df.index[-1].time()
        if last_time > dtime(10, 30):
            self.ib_set = True

        if self.ib_set and self.ib_high > 0 and self.ib_low < float("inf"):
            current_price = float(df["close"].iloc[-1])
            day_high      = float(df["high"].max())
            day_low       = float(df["low"].min())
            ib_range      = self.ib_high - self.ib_low

            if day_high >= self.ib_high:
                self.high_touched = True
            if day_low <= self.ib_low:
                self.low_touched = True

            # ── Priority order mirrors classify_day_structure ──────────────────
            # 1. Trend Day: early aggressive break + high RVOL
            if current_price > self.ib_high and rvol > 2.0:
                self.prediction = "Trend Day"
            elif current_price < self.ib_low and rvol > 2.0:
                self.prediction = "Trend Day"

            # 2. Non-Trend: very narrow IB + low RVOL
            elif ib_range < 0.005 * self.ib_high and rvol < 1.0:
                self.prediction = "Non-Trend"

            # 3. Neutral Extreme: both sides hit
            elif self.high_touched and self.low_touched:
                total_range = day_high - day_low
                extreme_band = 0.10 * total_range if total_range > 0 else 0
                if current_price >= day_high - extreme_band or current_price <= day_low + extreme_band:
                    self.prediction = "Neutral Extreme"
                else:
                    self.prediction = "Neutral"

            # 4. Normal Variation: one side breached only
            elif self.high_touched or self.low_touched:
                self.prediction = "Normal Variation"

            # 5. Normal: price stayed inside IB
            else:
                self.prediction = "Normal"
        else:
            self.prediction = "Analyzing IB…"

        self.save_to_session()

    def color(self):
        return self._STRUCTURE_COLORS.get(self.prediction, "#888")


# ── Accuracy tracker persistence ──────────────────────────────────────────────

def load_accuracy_tracker():
    """Return a DataFrame from accuracy_tracker.csv (or empty if none)."""
    cols = ["timestamp", "symbol", "predicted", "actual", "correct",
            "entry_price", "exit_price", "mfe", "compare_key"]
    if not os.path.exists(TRACKER_FILE):
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(TRACKER_FILE)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        return df
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
    """7-structure classification.  Returns (label, color, detail, insight)."""
    day_high = float(df["high"].max())
    day_low  = float(df["low"].min())
    total_range = day_high - day_low
    ib_range = (ib_high - ib_low) if (ib_high is not None and ib_low is not None) else 0.0
    final_price = float(df["close"].iloc[-1])

    # ── IB volume stats — the most important structural signal ─────────────────
    ib_vol_pct, ib_range_ratio = compute_ib_volume_stats(df, ib_high, ib_low)
    # High ib_vol_pct (>0.65) → balanced/Normal; low (<0.35) → directional/Trend

    if total_range == 0 or ib_range == 0:
        return ("⚖️ Normal / Balanced", "#66bb6a",
                "Insufficient range data.",
                "Not enough price movement to classify structure reliably.")

    atr = compute_atr(df)

    ib_high_touched = day_high >= ib_high
    ib_low_touched  = day_low  <= ib_low
    both_touched    = ib_high_touched and ib_low_touched

    two_hr_end = df.index[0].replace(hour=11, minute=30)
    early_df   = df[df.index <= two_hr_end]
    early_high = float(early_df["high"].max()) if not early_df.empty else day_high
    early_low  = float(early_df["low"].min())  if not early_df.empty else day_low
    ib_violated_early_up   = early_high > ib_high
    ib_violated_early_down = early_low  < ib_low

    if final_price > ib_high:
        dist_from_ib = final_price - ib_high
    elif final_price < ib_low:
        dist_from_ib = ib_low - final_price
    else:
        dist_from_ib = 0.0

    extreme_band = 0.10 * total_range

    # ── 1. Double Distribution ─────────────────────────────────────────────────
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
        insight = (f"Two separate auctions detected. Lower HVN = sellers' value area, "
                   f"upper HVN = buyers' territory. LVN at ${lvn_price:.2f} is the "
                   f"magnet zone — expect rapid, high-momentum moves through it. "
                   f"Gap Fill toward the next HVN is the primary target.")
        return ("⚡ Double Distribution", "#00bcd4", detail, insight)

    # ── 2. Non-Trend ──────────────────────────────────────────────────────────
    is_narrow_ib = ib_range < 0.20 * total_range
    total_vol = float(df["volume"].sum())
    if avg_daily_vol and avg_daily_vol > 0:
        pace = (total_vol / max(1, len(df))) * 390.0
        is_anemic = (pace / avg_daily_vol) < 0.80
    else:
        is_anemic = ib_range / max(0.001, day_high) < 0.003
    # IB vol % reinforces Non-Trend: most volume staying inside a tight IB = no conviction
    ib_vol_confirms_nontrend = ib_vol_pct > 0.72 and ib_range_ratio < 0.25
    if is_narrow_ib and (is_anemic or ib_vol_confirms_nontrend):
        detail  = (f"IB ${ib_range:.2f} = {ib_range/total_range*100:.0f}% of day range. "
                   f"IB volume {ib_vol_pct*100:.0f}% of session total. "
                   f"Volume participation is anemic.")
        insight = (f"Tight initial balance with {ib_vol_pct*100:.0f}% of session volume "
                   f"trapped inside the opening range signals no institutional interest. "
                   f"Day-traders are in control; avoid chasing breakouts. "
                   f"Wait for a volume-backed catalyst before committing size.")
        return ("😴 Non-Trend", "#78909c", detail, insight)

    # ── 3. Normal ─────────────────────────────────────────────────────────────
    if not ib_high_touched and not ib_low_touched:
        pct_inside = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean()) * 100
        # High IB vol% on a Normal day = very strong balance signal
        ib_vol_str = (f"IB absorbed {ib_vol_pct*100:.0f}% of volume — "
                      f"{'strong balance' if ib_vol_pct > 0.60 else 'moderate balance'}. ")
        detail  = (f"IB ${ib_high:.2f}–${ib_low:.2f} never violated. "
                   f"Price inside IB for {pct_inside:.0f}% of session. {ib_vol_str}")
        insight = (f"Classic balanced day — {ib_vol_pct*100:.0f}% of volume traded inside the "
                   f"9:30–10:30 range. Both buyers and sellers accept value here. "
                   f"{'High IB volume confirms strong rotational acceptance — ' if ib_vol_pct > 0.60 else ''}"
                   f"No directional conviction from either side. "
                   f"Fade the extremes and target POC at ${poc_price:.2f}.")
        return ("⚖️ Normal", "#66bb6a", detail, insight)

    # ── 4. Trend Day ──────────────────────────────────────────────────────────
    # IB vol% < 0.40 AND ib_range_ratio < 0.40 are strong Trend Day signals:
    # most volume printed outside the opening range → directional conviction confirmed by flow
    ib_vol_trend_signal = ib_vol_pct < 0.40 and ib_range_ratio < 0.40
    is_trend_up   = ib_violated_early_up   and (dist_from_ib > 2.0 * atr) and (final_price > ib_high)
    is_trend_down = ib_violated_early_down and (dist_from_ib > 2.0 * atr) and (final_price < ib_low)
    # Softer Trend trigger when IB vol stats confirm directional flow even if ATR threshold not met
    is_trend_up_soft   = (ib_violated_early_up   and ib_vol_trend_signal
                          and dist_from_ib > 1.0 * atr and final_price > ib_high)
    is_trend_down_soft = (ib_violated_early_down and ib_vol_trend_signal
                          and dist_from_ib > 1.0 * atr and final_price < ib_low)
    if is_trend_up or is_trend_down or is_trend_up_soft or is_trend_down_soft:
        bullish    = is_trend_up or is_trend_up_soft
        direction  = "Bullish" if bullish else "Bearish"
        dist_atr   = dist_from_ib / atr if atr > 0 else 0
        confirmed  = "✅ IB vol confirms" if ib_vol_trend_signal else ""
        detail  = (f"{direction} — IB violated within first 2 hrs, "
                   f"price {dist_atr:.1f}× ATR from IB. "
                   f"Only {ib_vol_pct*100:.0f}% of volume inside IB — directional flow. "
                   f"IB captured {ib_range_ratio*100:.0f}% of day range. {confirmed}")
        insight = (f"Strong directional conviction: only {ib_vol_pct*100:.0f}% of session volume "
                   f"stayed inside the 9:30–10:30 range (IB), meaning "
                   f"{'buyers' if bullish else 'sellers'} pushed hard outside it immediately. "
                   f"{'Buyers' if bullish else 'Sellers'} established control before noon "
                   f"and held ground. Trend continuation is the high-probability path — "
                   f"add on pullbacks toward POC ${poc_price:.2f}.")
        lbl = "📈 Trend Day" if bullish else "📉 Trend Day (Bear)"
        return (lbl, "#ff9800", detail, insight)

    # ── 5. Neutral Extreme ────────────────────────────────────────────────────
    if both_touched:
        at_high = final_price >= day_high - extreme_band
        at_low  = final_price <= day_low  + extreme_band
        if at_high or at_low:
            side         = "high" if at_high else "low"
            extreme_lvl  = ib_high if at_high else ib_low
            detail  = (f"Both IB extremes hit. Price closing at day's {side} "
                       f"(${final_price:.2f}) — late-session momentum bias building.")
            insight = (f"Both sides were tested — late session committed to the "
                       f"{'upside' if at_high else 'downside'}. "
                       f"This pattern frequently resolves with a "
                       f"{'gap up' if at_high else 'gap down'} the next morning. "
                       f"Watch for {'resistance' if at_high else 'support'} "
                       f"at ${extreme_lvl:.2f}.")
            return ("⚡ Neutral Extreme", "#7e57c2", detail, insight)

    # ── 6. Neutral ────────────────────────────────────────────────────────────
    if both_touched and (ib_low <= final_price <= ib_high):
        pct_inside = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean()) * 100
        detail  = (f"Both IB extremes tested, price now back inside IB at ${final_price:.2f}. "
                   f"IB inside-time {pct_inside:.0f}%.")
        insight = (f"Both sides of the IB were accepted and rejected — a hallmark Neutral day. "
                   f"Price is gravitating back to POC ${poc_price:.2f}. "
                   f"Expect choppy, rotational action into the close. "
                   f"Avoid directional trades; fade the extremes.")
        return ("🔄 Neutral", "#80cbc4", detail, insight)

    # ── 7. Normal Variation ───────────────────────────────────────────────────
    if ib_high_touched and not ib_low_touched:
        detail  = (f"IB High ${ib_high:.2f} breached; Low ${ib_low:.2f} held. "
                   f"Price building new belly above at ${final_price:.2f}.")
        insight = (f"Buyers absorbed all supply above IB High — a bullish sign. "
                   f"New value area forming above ${ib_high:.2f}. "
                   f"If price holds above IB High on pullbacks, "
                   f"next target is the 1.5× IB extension.")
        return ("📊 Normal Variation (Up)", "#aed581", detail, insight)
    if ib_low_touched and not ib_high_touched:
        detail  = (f"IB Low ${ib_low:.2f} breached; High ${ib_high:.2f} held. "
                   f"Price building new belly below at ${final_price:.2f}.")
        insight = (f"Sellers absorbed all demand below IB Low — a bearish sign. "
                   f"New value area forming below ${ib_low:.2f}. "
                   f"If price holds below IB Low on bounces, "
                   f"next target is the 1.5× IB extension downward.")
        return ("📊 Normal Variation (Down)", "#ffab91", detail, insight)

    # ── Fallback ──────────────────────────────────────────────────────────────
    pct = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean()) * 100
    detail  = f"Price inside IB for {pct:.0f}% of session — balanced, rotational day."
    insight = "No dominant structure emerging. Monitor volume for a directional signal."
    return ("⚖️ Normal / Balanced", "#66bb6a", detail, insight)


def compute_structure_probabilities(df, bin_centers, vap, ib_high, ib_low, poc_price):
    day_high = float(df["high"].max())
    day_low  = float(df["low"].min())
    total_range = day_high - day_low
    ib_range = (ib_high - ib_low) if (ib_high is not None and ib_low is not None) else 0.0
    final_price = float(df["close"].iloc[-1])
    fallback = {"Non-Trend": 14.0, "Normal": 14.0, "Trend": 14.0,
                "Ntrl Extreme": 14.0, "Neutral": 14.0, "Nrml Var": 15.0, "Dbl Dist": 15.0}
    if total_range == 0 or ib_range == 0:
        return fallback

    # ── IB volume stats (primary signal) ──────────────────────────────────────
    ib_vol_pct, ib_range_ratio = compute_ib_volume_stats(df, ib_high, ib_low)
    # ib_vol_pct:  >0.65 = balanced, <0.35 = directional
    # ib_range_ratio: >0.60 = tight/Normal, <0.30 = wide Trend expansion

    rr = total_range / ib_range
    poc_pos = (poc_price - day_low) / total_range
    pct_inside = float(((df["close"] >= ib_low) & (df["close"] <= ib_high)).mean())

    ib_high_hit = day_high >= ib_high
    ib_low_hit  = day_low  <= ib_low
    both_hit    = ib_high_hit and ib_low_hit

    atr = compute_atr(df)
    if final_price > ib_high:
        dist_ib = final_price - ib_high
    elif final_price < ib_low:
        dist_ib = ib_low - final_price
    else:
        dist_ib = 0.0

    two_hr_end = df.index[0].replace(hour=11, minute=30)
    early_df   = df[df.index <= two_hr_end]
    early_high = float(early_df["high"].max()) if not early_df.empty else day_high
    early_low  = float(early_df["low"].min())  if not early_df.empty else day_low
    viol_early = (early_high > ib_high) or (early_low < ib_low)

    extreme_band = 0.10 * total_range
    at_extreme   = (final_price >= day_high - extreme_band) or (final_price <= day_low + extreme_band)

    has_dd = _detect_double_distribution(bin_centers, vap) is not None

    # ── IB-volume multipliers ─────────────────────────────────────────────────
    # Normal / Non-Trend boosted by high IB vol; Trend boosted by low IB vol
    ib_balance_boost = max(0.5, ib_vol_pct * 2.0)          # 0.5 → 2.0 as ib_vol_pct goes 0→1
    ib_trend_boost   = max(0.5, (1.0 - ib_vol_pct) * 2.0) # mirrors above

    scores = {
        "Non-Trend":    max(2.0, (1.0 - rr) * 40.0 * ib_balance_boost)
                        if ib_range < 0.20 * total_range else 2.0,
        "Normal":       (5.0 + pct_inside * 60.0) * ib_balance_boost
                        if not ib_high_hit and not ib_low_hit else 3.0,
        "Trend":        (5.0 + max(0.0, (dist_ib / max(atr, 0.01) - 1.0) * 25.0)) * ib_trend_boost
                        if viol_early else 3.0,
        "Ntrl Extreme": 50.0 if both_hit and at_extreme else 3.0,
        "Neutral":      50.0 if both_hit and (ib_low <= final_price <= ib_high) else 3.0,
        "Nrml Var":     40.0 * (0.7 + 0.6 * (1.0 - ib_vol_pct))  # stronger when low IB vol
                        if (ib_high_hit ^ ib_low_hit) else 4.0,
        "Dbl Dist":     60.0 if has_dd else 3.0,
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
                target_zones=None, position=None):
    # ── Convert df.index to ET time-label strings ────────────────────────────
    def _et_label(ts):
        try:
            return pd.Timestamp(ts).tz_convert(EASTERN).strftime("%H:%M ET")
        except Exception:
            return str(ts)[-8:]

    x_labels = [_et_label(ts) for ts in df.index]
    x0, x1   = x_labels[0], x_labels[-1]

    fig = make_subplots(rows=1, cols=2, column_widths=[0.75, 0.25],
                        shared_yaxes=True, horizontal_spacing=0.01)
    fig.add_trace(go.Candlestick(
        x=x_labels, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="Price", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350",
    ), row=1, col=1)

    if ib_high is not None and ib_low is not None:
        fig.add_trace(go.Scatter(x=[x0, x1], y=[ib_high, ib_high], mode="lines",
            name=f"IB High ({ib_high:.2f})",
            line=dict(color="#00e676", width=1.8, dash="dash")), row=1, col=1)
        fig.add_trace(go.Scatter(x=[x0, x1], y=[ib_low, ib_low], mode="lines",
            name=f"IB Low ({ib_low:.2f})",
            line=dict(color="#ff5252", width=1.8, dash="dash")), row=1, col=1)

    fig.add_trace(go.Scatter(x=[x0, x1], y=[poc_price, poc_price], mode="lines",
        name=f"POC ({poc_price:.2f})", line=dict(color="gold", width=2.5)), row=1, col=1)

    # ── Dynamic Target Zone overlay ───────────────────────────────────────────
    lvn_idx_to_highlight = None
    if target_zones:
        for tz in target_zones:
            tp = tz["price"]
            tc = tz["color"]
            tl = tz["label"]
            # Annotated dotted horizontal line
            fig.add_trace(go.Scatter(
                x=[x0, x1], y=[tp, tp], mode="lines+text",
                name=tl,
                line=dict(color=tc, width=1.6, dash="dot"),
                text=["", f" {tl}"],
                textposition="top right",
                textfont=dict(color=tc, size=11),
            ), row=1, col=1)
            # Shaded band (±1% of price) for trend extensions
            if tz["type"] == "trend_extension":
                band = tp * 0.005
                fig.add_shape(
                    type="rect", xref="paper", x0=0, x1=0.75,
                    y0=tp - band, y1=tp + band,
                    fillcolor=tc + "30",
                    line=dict(width=0),
                    row=1, col=1,
                )
            # Track LVN index for volume profile highlighting
            if tz["type"] == "gap_fill" and "lvn_idx" in tz:
                lvn_idx_to_highlight = tz["lvn_idx"]

    # ── Volume Profile bars (LVN highlighted in yellow for Double Distribution) ─
    bw = float(bin_centers[1] - bin_centers[0]) if len(bin_centers) > 1 else 0
    colors = []
    for i, p in enumerate(bin_centers):
        if abs(p - poc_price) < bw * 0.5:
            colors.append("gold")
        elif lvn_idx_to_highlight is not None and i == lvn_idx_to_highlight:
            colors.append("#ffeb3b")
        else:
            colors.append("#5c6bc0")
    fig.add_trace(go.Bar(x=vap, y=bin_centers, orientation="h",
        name="Volume Profile", marker_color=colors, opacity=0.85), row=1, col=2)

    # ── Position overlay ──────────────────────────────────────────────────────
    if position and position.get("in"):
        avg_entry  = position["avg_entry"]
        peak_price = position["peak_price"]
        price_now  = float(df["close"].iloc[-1])
        shares     = position.get("shares", 0)
        pnl_pct    = (price_now - avg_entry) / avg_entry * 100 if avg_entry > 0 else 0
        pnl_dol    = (price_now - avg_entry) * shares if shares > 0 else 0
        pnl_color  = "#4caf50" if pnl_pct >= 0 else "#ef5350"

        # Entry line — solid white
        fig.add_trace(go.Scatter(
            x=[x0, x1], y=[avg_entry, avg_entry], mode="lines+text",
            name=f"Entry ${avg_entry:.2f}",
            line=dict(color="#ffffff", width=2.0, dash="solid"),
            text=["", f" 📍 ENTRY ${avg_entry:.2f}"],
            textposition="top right",
            textfont=dict(color="#ffffff", size=12),
        ), row=1, col=1)

        # MFE (peak) line — cyan dashed
        if peak_price > avg_entry:
            fig.add_trace(go.Scatter(
                x=[x0, x1], y=[peak_price, peak_price], mode="lines+text",
                name=f"MFE ${peak_price:.2f}",
                line=dict(color="#00bcd4", width=1.5, dash="dash"),
                text=["", f" ⬆ MFE ${peak_price:.2f}"],
                textposition="top right",
                textfont=dict(color="#00bcd4", size=11),
            ), row=1, col=1)

        # P&L annotation badge at the right edge
        pnl_txt = (f"{'▲' if pnl_pct>=0 else '▼'} {abs(pnl_pct):.1f}%"
                   f"  (${pnl_dol:+.0f})" if shares > 0 else
                   f"{'▲' if pnl_pct>=0 else '▼'} {abs(pnl_pct):.1f}%")
        fig.add_annotation(
            xref="paper", yref="y",
            x=0.74, y=avg_entry,
            text=f"<b>{pnl_txt}</b>",
            showarrow=False,
            font=dict(color=pnl_color, size=13),
            bgcolor=pnl_color + "22",
            bordercolor=pnl_color,
            borderwidth=1,
            borderpad=4,
        )

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
    audio_enabled = st.session_state.get("audio_alerts_enabled", True)
    check_tcs_alerts(tcs, audio_enabled)

    # ── MarketBrain — real-time structure prediction ───────────────────────────
    brain = MarketBrain()
    brain.load_from_session()
    rvol_pre = compute_rvol(df, intraday_curve=intraday_curve, avg_daily_vol=avg_daily_vol)
    brain.update(df, rvol=rvol_pre)
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
    render_structure_banner(label, color, detail, probs, tcs,
                            is_runner=is_runner, sector_bonus=sector_bonus,
                            insight=insight)
    render_model_prediction(pred_outcome, pred_reasoning)

    # ── MarketBrain: compare prediction vs actual + running counter ───────────
    bc = brain.color()
    _brain_correct_now = False
    _brain_newly_logged = False

    # Auto-compare once IB is complete and brain has a real prediction
    if brain.ib_set and brain.prediction != "Analyzing IB…":
        _today_str   = datetime.now(EASTERN).strftime("%Y-%m-%d")
        _compare_key = f"{ticker}_{_today_str}_{ib_high:.4f}_{ib_low:.4f}"

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
    _counter_str = (f"Session: {_b_corr}/{_b_total} ({_b_rate:.0f}%)"
                    if _b_total > 0 else "No comparisons yet")
    _counter_col = "#4caf50" if _b_rate >= 60 else "#ffa726" if _b_rate >= 40 else "#ef5350"

    st.markdown(
        f'<div style="background:{bc}11; border-left:3px solid {bc}; border-radius:6px; '
        f'padding:8px 16px; margin:6px 0 4px 0; display:flex; align-items:center; gap:14px; flex-wrap:wrap;">'
        f'<span style="font-size:11px; color:#888; text-transform:uppercase; '
        f'letter-spacing:1px; white-space:nowrap;">🧠 Brain Prediction</span>'
        f'<span style="font-size:15px; font-weight:700; color:{bc};">{brain.prediction}</span>'
        f'<span style="font-size:11px; color:#666;">vs actual: '
        f'<span style="color:{color};">{label}</span></span>'
        f'<span style="font-size:11px; font-weight:700; color:{_counter_col}; '
        f'margin-left:auto; background:{_counter_col}22; padding:2px 8px; border-radius:4px; '
        f'border:1px solid {_counter_col}55;">{_counter_str}</span>'
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
    fig = build_chart(df, ib_high, ib_low, bin_centers, vap, poc_price, chart_title,
                      target_zones=target_zones, position=pos_state)
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


def render_tracker_tab():
    """Render the Accuracy Tracker tab — structure distribution + Predicted vs Actual history."""
    st.markdown("## 🧠 MarketBrain — Accuracy Tracker")
    st.caption("All-time structure distribution and brain prediction accuracy.")

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
            st.info(f"🔄 Next recalibration in **{_next_recal}** more {"entry" if _next_recal == 1 else "entries"} "
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
                                    lookback_days=5, feed=data_feed)
                            except Exception:
                                st.session_state.replay_intraday_curve = None
                            try:
                                _etf = fetch_etf_pct_change(
                                    api_key, secret_key, sector_etf, selected_date, feed=data_feed)
                                st.session_state.replay_sector_bonus = 10.0 if _etf > 1.0 else 0.0
                            except Exception:
                                st.session_state.replay_sector_bonus = 0.0
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
