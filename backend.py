import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta, time as dtime
import pytz
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import csv
import os
import logging
import requests
from collections import deque
try:
    import streamlit as st
    _ST_AVAILABLE = True
except (ImportError, Exception):
    _ST_AVAILABLE = False
    st = None  # type: ignore

from supabase import create_client, Client

import re as _re
_raw_supabase_url = os.environ.get("SUPABASE_URL", "")
_url_match = _re.search(r'https://[a-z0-9]+\.supabase\.co', _raw_supabase_url)
SUPABASE_URL = _url_match.group(0) if _url_match else _raw_supabase_url
SUPABASE_KEY = (
    os.environ.get("SUPABASE_KEY") or
    os.environ.get("SUPABASE_ANON_KEY") or
    os.environ.get("VITE_SUPABASE_ANON_KEY")
)
SUPABASE_ANON_KEY = (
    os.environ.get("SUPABASE_ANON_KEY") or
    os.environ.get("VITE_SUPABASE_ANON_KEY") or
    SUPABASE_KEY
)

if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None
    print("WARNING: Supabase credentials not found in environment variables.")

# ── RLS-enforcing client (anon key + user JWT) ────────────────────────────────
# This client respects Row Level Security. After a user logs in, call
# set_user_session() to bind their JWT so all queries are user-scoped.
if SUPABASE_URL and SUPABASE_ANON_KEY:
    supabase_anon: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
else:
    supabase_anon = None


def set_user_session(access_token: str, refresh_token: str) -> None:
    """Bind a logged-in user's JWT to the RLS-enforcing client.

    Must be called after every login or session restore so that
    supabase_anon queries are automatically scoped to that user.
    The paper_trader_bot continues to use the service-key client
    (supabase) which bypasses RLS by design.
    """
    if supabase_anon and access_token:
        try:
            supabase_anon.auth.set_session(access_token, refresh_token or "")
        except Exception:
            pass

# ── Supabase Auth helpers ─────────────────────────────────────────────────────

def auth_login(email: str, password: str) -> dict:
    """Sign in via Supabase email/password auth."""
    if not supabase:
        return {"user": None, "session": None, "error": "Supabase not configured."}
    try:
        resp = supabase.auth.sign_in_with_password({"email": email, "password": password})
        return {"user": resp.user, "session": resp.session, "error": None}
    except Exception as exc:
        msg = str(exc)
        if "Invalid login credentials" in msg:
            msg = "Invalid email or password."
        elif "Email not confirmed" in msg:
            msg = "Please confirm your email before logging in."
        return {"user": None, "session": None, "error": msg}


def auth_signup(email: str, password: str) -> dict:
    """Sign up via Supabase email/password auth."""
    if not supabase:
        return {"user": None, "session": None, "error": "Supabase not configured."}
    try:
        resp = supabase.auth.sign_up({"email": email, "password": password})
        return {"user": resp.user, "session": resp.session, "error": None}
    except Exception as exc:
        return {"user": None, "session": None, "error": str(exc)}


def auth_signout() -> None:
    """Sign out the current Supabase auth session."""
    if not supabase:
        return
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    clear_session_cache()


_SESSION_CACHE = os.path.join(os.path.dirname(__file__), ".local", "session_cache.json")


def save_session_cache(user_id: str, email: str, refresh_token: str) -> None:
    """Persist the Supabase refresh token to disk so we can restore the session
    after a server restart without asking the user to log in again."""
    try:
        os.makedirs(os.path.dirname(_SESSION_CACHE), exist_ok=True)
        with open(_SESSION_CACHE, "w") as _f:
            json.dump({"user_id": user_id, "email": email,
                       "refresh_token": refresh_token}, _f)
    except Exception:
        pass


def load_session_cache() -> dict:
    """Read the persisted session cache. Returns {} if missing or corrupt."""
    try:
        if os.path.exists(_SESSION_CACHE):
            with open(_SESSION_CACHE) as _f:
                return json.load(_f)
    except Exception:
        pass
    return {}


def clear_session_cache() -> None:
    """Delete the session cache (called on explicit sign-out)."""
    try:
        if os.path.exists(_SESSION_CACHE):
            os.remove(_SESSION_CACHE)
    except Exception:
        pass


def try_restore_session() -> dict:
    """Attempt to restore a previous session from the cached refresh token.

    Returns {"user": <User>, "email": str} on success, {} on failure.
    """
    if not supabase:
        return {}
    cache = load_session_cache()
    token = cache.get("refresh_token", "")
    if not token:
        return {}
    try:
        resp = supabase.auth.refresh_session(token)
        if resp and resp.user:
            # Persist the new refresh token (it rotates on each use)
            save_session_cache(
                str(resp.user.id),
                str(resp.user.email),
                resp.session.refresh_token if resp.session else token,
            )
            return {
                "user":          resp.user,
                "email":         str(resp.user.email),
                "access_token":  resp.session.access_token  if resp.session else "",
                "refresh_token": resp.session.refresh_token if resp.session else "",
            }
    except Exception as _e:
        print(f"Session restore failed: {_e}")
        clear_session_cache()
    return {}


def check_user_id_column_exists() -> bool:
    """Return True if user_id column already exists in trade_journal."""
    if not supabase:
        return False
    try:
        supabase.table("trade_journal").select("user_id").limit(1).execute()
        return True
    except Exception as e:
        return "user_id" not in str(e)  # column error → False; other errors → assume True

from engine_v2 import (
    calculate_v2_metrics, get_profile_and_shape, calculate_historical_retention,
    identify_overhead_supply, detect_volatility_halts, v2_brain_final_boss,
    calculate_time_multiplier, v2_brain_v3, get_volume_profile_v2, v2_execution_logic
)

STATE_FILE   = "trade_state.json"
TRACKER_FILE = "accuracy_tracker.csv"
WEIGHTS_FILE = "brain_weights.json"   # ⛔ READ-ONLY — hand-calibrated signal weights; do not edit manually
HICONS_FILE  = "high_conviction_log.csv"
HICONS_THRESHOLD = 75.0
SA_JOURNAL_FILE  = "sa_journal.csv"
JOURNAL_PATH = "trade_journal.csv"
_JOURNAL_COLS = [
    "timestamp", "ticker", "price", "structure", "tcs", "rvol",
    "ib_high", "ib_low", "notes", "grade", "grade_reason",
]

_BRAIN_WEIGHT_KEYS = [
    "trend_bull", "trend_bear", "double_dist",
    "non_trend",  "normal",     "neutral",
    "ntrl_extreme", "nrml_variation",
]
_RECALIBRATE_EVERY = 10
EASTERN = pytz.timezone("America/New_York")

# ── NYSE Market Holiday Calendar ──────────────────────────────────────────────
# Standard NYSE holidays 2025–2027  (observed date when holiday falls on weekend)
_NYSE_HOLIDAYS: set = {
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
    "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
    "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
}


def is_trading_day(d: date) -> bool:
    """Return True if d is a NYSE trading day (not a weekend or known holiday)."""
    return d.weekday() < 5 and d.isoformat() not in _NYSE_HOLIDAYS


def get_last_trading_day(as_of: date = None,
                         api_key: str = "",
                         secret_key: str = "") -> date:
    """Return the most recent completed NYSE trading day on or before as_of.

    Strategy:
    1. Ask Alpaca's /v1/calendar if credentials are supplied (most accurate).
    2. Fall back to hardcoded _NYSE_HOLIDAYS list.
    3. Last resort: skip weekends only.
    """
    if as_of is None:
        as_of = date.today()

    # ── Alpaca calendar (accurate, handles early closes & ad-hoc closures) ──
    if api_key and secret_key:
        try:
            start_str = (as_of - timedelta(days=14)).isoformat()
            end_str   = as_of.isoformat()
            r = requests.get(
                "https://paper-api.alpaca.markets/v1/calendar",
                params={"start": start_str, "end": end_str},
                headers={
                    "APCA-API-KEY-ID":     api_key,
                    "APCA-API-SECRET-KEY": secret_key,
                },
                timeout=5,
            )
            if r.status_code == 200:
                cal = r.json()
                trading_dates = sorted(
                    [c["date"] for c in cal if c["date"] <= end_str],
                    reverse=True,
                )
                if trading_dates:
                    return date.fromisoformat(trading_dates[0])
        except Exception:
            pass

    # ── Hardcoded holiday fallback ──────────────────────────────────────────
    d = as_of
    for _ in range(14):
        if is_trading_day(d):
            return d
        d -= timedelta(days=1)

    # ── Absolute last resort: weekend-only ──────────────────────────────────
    d = as_of
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def fetch_bars(api_key, secret_key, ticker, trade_date, feed="sip"):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client = StockHistoricalDataClient(api_key, secret_key)
    mo = EASTERN.localize(datetime(trade_date.year, trade_date.month, trade_date.day, 9, 30))
    mc = EASTERN.localize(datetime(trade_date.year, trade_date.month, trade_date.day, 16, 0))
    # When fetching today's intraday data cap end to now so the API doesn't
    # get a future end time. If we're before market open, nothing to fetch yet.
    # For SIP feed: Alpaca free tier requires end to be >15 min old — cap to
    # now-16min so today's scans work without a paid subscription.
    # NOTE: the SIP recency restriction applies even AFTER market close, so
    # we always apply the cap for today's date regardless of market hours.
    now_et = datetime.now(EASTERN)
    if trade_date >= now_et.date():
        if now_et <= mo:
            return pd.DataFrame()   # pre-market — no bars yet
        if feed == "sip":
            sip_cap = now_et - timedelta(minutes=16)   # free-tier: must be >15 min old
            mc = min(mc, sip_cap)
            if mc <= mo:
                return pd.DataFrame()             # not enough SIP data yet
        elif now_et < mc:
            mc = now_et             # non-SIP mid-session — cap end to current time
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
    df["vwap"] = compute_vwap(df)
    return df


def compute_vwap(df: "pd.DataFrame") -> "pd.Series":
    """Compute intraday VWAP anchored to the session open.

    Typical Price = (High + Low + Close) / 3
    VWAP = cumsum(Typical Price × Volume) / cumsum(Volume)

    Returns a Series aligned to df.index, or an empty Series on failure.
    """
    try:
        tp  = (df["high"] + df["low"] + df["close"]) / 3.0
        vol = df["volume"].replace(0, float("nan"))
        cum_tpv = (tp * vol).cumsum()
        cum_vol = vol.cumsum()
        return cum_tpv / cum_vol
    except Exception:
        return pd.Series(dtype=float)


def compute_initial_balance(df):
    """Return (ib_high, ib_low) for the standard 9:30–10:30 first-hour window.

    Includes bars with timestamps from 9:30 through 10:30 (inclusive) —
    matching the industry convention used by most platforms (Webull, TOS, etc.)
    where the IB is the first 60 minutes of the regular session.
    Builds the cutoff from the date of the first bar to avoid tz-replace issues.
    """
    if df.empty:
        return None, None
    first_ts = df.index[0]
    tz = first_ts.tzinfo
    ib_end = pd.Timestamp(
        year=first_ts.year, month=first_ts.month, day=first_ts.day,
        hour=10, minute=30, second=59, tz=tz,
    )
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


def _compute_value_area(bin_centers, vap, pct=0.70):
    """Return (VAL, VAH) — the price range containing `pct` of session volume.

    Starts at the POC and expands one bin at a time (always adding whichever
    adjacent bin has more volume), until the accumulated total reaches the
    target percentage.  This is the CME / Market Profile standard method.
    """
    total = float(np.sum(vap))
    if total == 0 or len(vap) == 0:
        return None, None
    poc_idx = int(np.argmax(vap))
    acc = float(vap[poc_idx])
    lo = hi = poc_idx
    while acc / total < pct:
        can_up = hi + 1 < len(vap)
        can_dn = lo - 1 >= 0
        if not can_up and not can_dn:
            break
        uv = float(vap[hi + 1]) if can_up else -1.0
        dv = float(vap[lo - 1]) if can_dn else -1.0
        if uv >= dv:
            hi += 1; acc += uv
        else:
            lo -= 1; acc += dv
    return float(bin_centers[lo]), float(bin_centers[hi])


# ══════════════════════════════════════════════════════════════════════════════
# SMALL ACCOUNT CHALLENGE — HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def compute_macd(close_series, fast=12, slow=26, signal=9):
    """Return (macd_line, signal_line, histogram) as pandas Series."""
    ema_f = close_series.ewm(span=fast, adjust=False).mean()
    ema_s = close_series.ewm(span=slow, adjust=False).mean()
    macd  = ema_f - ema_s
    sig   = macd.ewm(span=signal, adjust=False).mean()
    return macd, sig, macd - sig



def get_whole_half_levels(price_low, price_high):
    """Return all $0.50 increment levels between price_low and price_high.
    Whole dollars are key resistance; half dollars secondary.
    """
    lo = np.floor(price_low * 2) / 2
    hi = np.ceil(price_high * 2) / 2
    return [round(x, 2) for x in np.arange(lo, hi + 0.01, 0.50)
            if price_low * 0.98 <= x <= price_high * 1.02]


def detect_poc_shift(bin_centers, vap):
    """Classify POC position relative to the full profile range.
    Upper third = Bullish (buyers in control); lower third = Bearish.
    """
    if len(bin_centers) == 0:
        return "Neutral — no data", "#ffa726"
    poc_idx = int(np.argmax(vap))
    pct = poc_idx / len(bin_centers)
    if pct >= 0.67:
        return "Bullish — POC in upper zone ↑", "#4caf50"
    if pct <= 0.33:
        return "Bearish — POC in lower zone ↓", "#ef5350"
    return "Neutral — POC mid-range", "#ffa726"


def count_consecutive_greens(df):
    """Count how many consecutive green candles appear at the tail of df."""
    closes = df["close"].values
    opens  = df["open"].values
    count  = 0
    for i in range(len(closes) - 1, -1, -1):
        if closes[i] > opens[i]:
            count += 1
        else:
            break
    return count


def compute_recovery_ratio(loss_pct):
    """Return the % gain required to recover from loss_pct% drawdown."""
    if loss_pct <= 0:
        return 0.0
    if loss_pct >= 100:
        return float("inf")
    return round((loss_pct / (100.0 - loss_pct)) * 100.0, 1)


def load_sa_journal():
    """Load the Small Account trade log from CSV."""
    if not os.path.exists(SA_JOURNAL_FILE):
        return []
    try:
        return pd.read_csv(SA_JOURNAL_FILE).to_dict("records")
    except Exception:
        return []


def save_sa_journal(entries):
    """Persist the Small Account trade log to CSV."""
    if not entries:
        return
    try:
        pd.DataFrame(entries).to_csv(SA_JOURNAL_FILE, index=False)
    except Exception:
        pass


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
# TIER 3 — CHART PATTERN DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def _resample_bars(df_1m, rule="5min"):
    """Resample 1-minute OHLCV bars to a coarser timeframe."""
    if df_1m is None or df_1m.empty:
        return pd.DataFrame()
    agg = {c: ("first" if c == "open" else "max" if c == "high"
               else "min" if c == "low" else "last" if c == "close"
               else "sum")
           for c in ["open", "high", "low", "close", "volume"] if c in df_1m.columns}
    if not agg:
        return pd.DataFrame()
    try:
        return df_1m.resample(rule).agg(agg).dropna(subset=["close"])
    except Exception:
        return pd.DataFrame()


def _find_swing_highs(df, lookback=2):
    """Return integer positions of swing high bars (local maxima ± lookback bars)."""
    highs = df["high"].values
    n = len(highs)
    out = []
    for i in range(lookback, n - lookback):
        if all(highs[i] >= highs[i - j] for j in range(1, lookback + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, lookback + 1)):
            out.append(i)
    return out


def _find_swing_lows(df, lookback=2):
    """Return integer positions of swing low bars (local minima ± lookback bars)."""
    lows = df["low"].values
    n = len(lows)
    out = []
    for i in range(lookback, n - lookback):
        if all(lows[i] <= lows[i - j] for j in range(1, lookback + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, lookback + 1)):
            out.append(i)
    return out


def detect_chart_patterns(df_1m, poc_price=None, ib_high=None, ib_low=None):
    """Detect classic chart patterns on 5m and 1hr resampled bars.

    Returns a list of pattern dicts sorted by score descending.  Each dict:
        name        — pattern name (str)
        direction   — 'Bullish' | 'Bearish'
        timeframe   — '5m' | '1hr'
        score       — 0.0–1.0 weighted confidence
        confluence  — list[str] of confluence reasons
        description — plain-language explanation
        neckline    — key price level (float | None)
    """
    if df_1m is None or df_1m.empty or len(df_1m) < 20:
        return []

    patterns = []

    for tf_label, rule in [("5m", "5min"), ("1hr", "60min")]:
        df_tf = _resample_bars(df_1m, rule)
        if df_tf is None or len(df_tf) < 8:
            continue

        # 5m: lookback=3 (15 min on each side) — filters micro-noise on fast bars
        # 1hr: lookback=2 (2 hrs on each side) — already structural
        _lb = 3 if tf_label == "5m" else 2
        sh_idx = _find_swing_highs(df_tf, lookback=_lb)
        sl_idx = _find_swing_lows(df_tf, lookback=_lb)
        atr_val = compute_atr(df_tf, period=min(14, len(df_tf)))
        close_now = float(df_tf["close"].iloc[-1])
        n = len(df_tf)

        # ── Reverse Head & Shoulders (Bullish) ────────────────────────────
        if len(sl_idx) >= 3:
            ls_i, h_i, rs_i = sl_idx[-3], sl_idx[-2], sl_idx[-1]
            p_ls = float(df_tf["low"].iloc[ls_i])
            p_h  = float(df_tf["low"].iloc[h_i])
            p_rs = float(df_tf["low"].iloc[rs_i])
            if p_h < p_ls and p_h < p_rs:
                sym = abs(p_ls - p_rs) / max(abs(p_h - (p_ls + p_rs) / 2), 0.001)
                if sym < 0.80:
                    hl = float(df_tf["high"].iloc[ls_i:h_i + 1].max()) if h_i > ls_i else p_ls
                    hr = float(df_tf["high"].iloc[h_i:rs_i + 1].max()) if rs_i > h_i else p_rs
                    neckline = round((hl + hr) / 2.0, 4)
                    score = 0.70
                    conf = []
                    if poc_price and abs(p_h - poc_price) / max(poc_price, 0.001) < 0.02:
                        score += 0.10
                        conf.append("Head at POC")
                    if ib_low and abs(p_h - ib_low) / max(ib_low, 0.001) < 0.02:
                        score += 0.10
                        conf.append("Head at IB Low")
                    if close_now >= neckline * 0.985:
                        score += 0.10
                        conf.append("Price at neckline — breakout imminent")
                    nl_str = f"${neckline:.2f}"
                    desc = (f"L-shoulder ${p_ls:.2f} → Head ${p_h:.2f} → "
                            f"R-shoulder ${p_rs:.2f}. Neckline ~{nl_str}.")
                    patterns.append({"name": "Reverse Head & Shoulders",
                                     "direction": "Bullish", "timeframe": tf_label,
                                     "score": round(min(score, 1.0), 2),
                                     "confluence": conf, "description": desc,
                                     "neckline": neckline})

        # ── Head & Shoulders (Bearish) ────────────────────────────────────
        if len(sh_idx) >= 3:
            ls_i, h_i, rs_i = sh_idx[-3], sh_idx[-2], sh_idx[-1]
            p_ls = float(df_tf["high"].iloc[ls_i])
            p_h  = float(df_tf["high"].iloc[h_i])
            p_rs = float(df_tf["high"].iloc[rs_i])
            if p_h > p_ls and p_h > p_rs:
                sym = abs(p_ls - p_rs) / max(abs(p_h - (p_ls + p_rs) / 2), 0.001)
                if sym < 0.80:
                    ll = float(df_tf["low"].iloc[ls_i:h_i + 1].min()) if h_i > ls_i else p_ls
                    lr = float(df_tf["low"].iloc[h_i:rs_i + 1].min()) if rs_i > h_i else p_rs
                    neckline = round((ll + lr) / 2.0, 4)
                    score = 0.70
                    conf = []
                    if poc_price and abs(p_h - poc_price) / max(poc_price, 0.001) < 0.02:
                        score += 0.10
                        conf.append("Head at POC")
                    if ib_high and abs(p_h - ib_high) / max(ib_high, 0.001) < 0.02:
                        score += 0.10
                        conf.append("Head at IB High")
                    if close_now <= neckline * 1.015:
                        score += 0.10
                        conf.append("Price testing neckline")
                    nl_str = f"${neckline:.2f}"
                    desc = (f"L-shoulder ${p_ls:.2f} → Head ${p_h:.2f} → "
                            f"R-shoulder ${p_rs:.2f}. Neckline ~{nl_str}.")
                    patterns.append({"name": "Head & Shoulders",
                                     "direction": "Bearish", "timeframe": tf_label,
                                     "score": round(min(score, 1.0), 2),
                                     "confluence": conf, "description": desc,
                                     "neckline": neckline})

        # ── Double Bottom (Bullish) ───────────────────────────────────────
        if len(sl_idx) >= 2:
            i1, i2 = sl_idx[-2], sl_idx[-1]
            p1 = float(df_tf["low"].iloc[i1])
            p2 = float(df_tf["low"].iloc[i2])
            mid_price = (p1 + p2) / 2.0
            diff_pct = abs(p1 - p2) / max(mid_price, 0.001)
            if diff_pct < 0.03:
                neckline = round(float(df_tf["high"].iloc[i1:i2 + 1].max()), 4)
                score = 0.65
                conf = []
                if poc_price and abs(mid_price - poc_price) / max(poc_price, 0.001) < 0.025:
                    score += 0.10
                    conf.append("Bottoms at POC")
                if ib_low and abs(mid_price - ib_low) / max(ib_low, 0.001) < 0.025:
                    score += 0.10
                    conf.append("Bottoms at IB Low")
                if close_now > neckline:
                    score += 0.15
                    conf.append("Neckline broken — confirmed")
                elif close_now >= neckline * 0.985:
                    score += 0.05
                    conf.append("Price at neckline")
                diff_pct_str = f"{diff_pct * 100:.1f}"
                neckline_str = f"${neckline:.2f}"
                desc = (f"Two lows at ${p1:.2f} / ${p2:.2f} ({diff_pct_str}% apart). "
                        f"Neckline {neckline_str}.")
                patterns.append({"name": "Double Bottom",
                                 "direction": "Bullish", "timeframe": tf_label,
                                 "score": round(min(score, 1.0), 2),
                                 "confluence": conf, "description": desc,
                                 "neckline": neckline})

        # ── Double Top (Bearish) ──────────────────────────────────────────
        if len(sh_idx) >= 2:
            i1, i2 = sh_idx[-2], sh_idx[-1]
            p1 = float(df_tf["high"].iloc[i1])
            p2 = float(df_tf["high"].iloc[i2])
            mid_price = (p1 + p2) / 2.0
            diff_pct = abs(p1 - p2) / max(mid_price, 0.001)
            if diff_pct < 0.03:
                neckline = round(float(df_tf["low"].iloc[i1:i2 + 1].min()), 4)
                score = 0.65
                conf = []
                if poc_price and abs(mid_price - poc_price) / max(poc_price, 0.001) < 0.025:
                    score += 0.10
                    conf.append("Tops at POC")
                if ib_high and abs(mid_price - ib_high) / max(ib_high, 0.001) < 0.025:
                    score += 0.10
                    conf.append("Tops at IB High")
                if close_now < neckline:
                    score += 0.15
                    conf.append("Neckline broken — confirmed")
                diff_pct_str = f"{diff_pct * 100:.1f}"
                neckline_str = f"${neckline:.2f}"
                desc = (f"Two highs at ${p1:.2f} / ${p2:.2f} ({diff_pct_str}% apart). "
                        f"Neckline {neckline_str}.")
                patterns.append({"name": "Double Top",
                                 "direction": "Bearish", "timeframe": tf_label,
                                 "score": round(min(score, 1.0), 2),
                                 "confluence": conf, "description": desc,
                                 "neckline": neckline})

        # ── Bull Flag (Bullish) ───────────────────────────────────────────
        if n >= 10:
            mid = n // 2
            pole_move = float(df_tf["close"].iloc[mid]) - float(df_tf["close"].iloc[0])
            pole_range = (float(df_tf["high"].iloc[:mid].max())
                         - float(df_tf["low"].iloc[:mid].min()))
            flag_hi = float(df_tf["high"].iloc[mid:].max())
            flag_lo = float(df_tf["low"].iloc[mid:].min())
            flag_range = flag_hi - flag_lo
            flag_slope = ((float(df_tf["close"].iloc[-1]) - float(df_tf["close"].iloc[mid]))
                         / max(n - mid, 1))
            is_pole = pole_move > atr_val * 2.5 and pole_move > 0
            is_tight = flag_range < pole_range * 0.55
            is_down_drift = flag_slope < 0
            if is_pole and is_tight and is_down_drift:
                score = 0.68
                conf = []
                if poc_price and flag_lo <= poc_price <= flag_hi:
                    score += 0.12
                    conf.append("Flag consolidating at POC")
                if ib_high and flag_lo <= ib_high <= flag_hi:
                    score += 0.10
                    conf.append("Flag at IB High")
                pole_str = f"${pole_move:.2f}"
                flag_str = f"${flag_range:.2f}"
                target_str = f"${flag_hi + pole_move:.2f}"
                desc = (f"Pole +{pole_str} → tight flag range {flag_str}. "
                        f"Breakout target ~{target_str}.")
                patterns.append({"name": "Bull Flag",
                                 "direction": "Bullish", "timeframe": tf_label,
                                 "score": round(min(score, 1.0), 2),
                                 "confluence": conf, "description": desc,
                                 "neckline": round(flag_hi, 4)})

        # ── Bear Flag (Bearish) ───────────────────────────────────────────
        if n >= 10:
            mid = n // 2
            pole_move = float(df_tf["close"].iloc[0]) - float(df_tf["close"].iloc[mid])
            pole_range = (float(df_tf["high"].iloc[:mid].max())
                         - float(df_tf["low"].iloc[:mid].min()))
            flag_hi = float(df_tf["high"].iloc[mid:].max())
            flag_lo = float(df_tf["low"].iloc[mid:].min())
            flag_range = flag_hi - flag_lo
            flag_slope = ((float(df_tf["close"].iloc[-1]) - float(df_tf["close"].iloc[mid]))
                         / max(n - mid, 1))
            is_pole = pole_move > atr_val * 2.5 and pole_move > 0
            is_tight = flag_range < pole_range * 0.55
            is_up_drift = flag_slope > 0
            if is_pole and is_tight and is_up_drift:
                score = 0.68
                conf = []
                if poc_price and flag_lo <= poc_price <= flag_hi:
                    score += 0.12
                    conf.append("Flag at POC")
                target_str = f"${flag_lo - pole_move:.2f}"
                pole_str = f"${pole_move:.2f}"
                flag_str = f"${flag_range:.2f}"
                desc = (f"Pole drop -{pole_str} → counter-rally {flag_str}. "
                        f"Breakdown target ~{target_str}.")
                patterns.append({"name": "Bear Flag",
                                 "direction": "Bearish", "timeframe": tf_label,
                                 "score": round(min(score, 1.0), 2),
                                 "confluence": conf, "description": desc,
                                 "neckline": round(flag_lo, 4)})

        # ── Cup & Handle (Bullish) ────────────────────────────────────────
        if n >= 15:
            cup_end = n * 2 // 3
            cup_df = df_tf.iloc[:cup_end]
            cup_start = float(cup_df["close"].iloc[0])
            cup_low = float(cup_df["low"].min())
            cup_end_price = float(cup_df["close"].iloc[-1])
            depth = cup_start - cup_low
            recovery = (cup_end_price - cup_low) / max(depth, 0.001)
            handle_df = df_tf.iloc[cup_end:]
            if len(handle_df) > 0:
                h_hi = float(handle_df["high"].max())
                h_lo = float(handle_df["low"].min())
                handle_depth_ratio = (h_hi - h_lo) / max(depth, 0.001)
                is_cup = recovery > 0.65 and depth > atr_val * 2
                is_handle = 0.04 < handle_depth_ratio < 0.45
                if is_cup and is_handle:
                    score = 0.72
                    conf = []
                    if poc_price and abs(cup_low - poc_price) / max(poc_price, 0.001) < 0.025:
                        score += 0.12
                        conf.append("Cup base at POC")
                    target = cup_start + depth
                    recovery_str = f"{recovery * 100:.0f}"
                    h_lo_str = f"${h_lo:.2f}"
                    h_hi_str = f"${h_hi:.2f}"
                    target_str = f"${target:.2f}"
                    desc = (f"Cup base ${cup_low:.2f} → {recovery_str}% recovered. "
                            f"Handle {h_lo_str}–{h_hi_str}. Target {target_str}.")
                    patterns.append({"name": "Cup & Handle",
                                     "direction": "Bullish", "timeframe": tf_label,
                                     "score": round(min(score, 1.0), 2),
                                     "confluence": conf, "description": desc,
                                     "neckline": round(cup_start, 4)})

    # ── Confluence boost: stacked patterns ────────────────────────────────────
    bull = [p for p in patterns if p["direction"] == "Bullish"]
    bear = [p for p in patterns if p["direction"] == "Bearish"]
    if len(bull) >= 2:
        extra = f"Stacked with {len(bull) - 1} other bullish pattern(s)"
        for p in bull:
            p["confluence"].append(extra)
            p["score"] = round(min(p["score"] * 1.15, 1.0), 2)
    if len(bear) >= 2:
        extra = f"Stacked with {len(bear) - 1} other bearish pattern(s)"
        for p in bear:
            p["confluence"].append(extra)
            p["score"] = round(min(p["score"] * 1.15, 1.0), 2)

    patterns.sort(key=lambda x: x["score"], reverse=True)
    return patterns


def scan_ticker_patterns(api_key: str, secret_key: str, ticker: str,
                         trade_date, feed: str = "iex") -> list:
    """Fetch intraday bars for a single ticker and return detected chart patterns.

    Wrapper around fetch_bars + detect_chart_patterns used by the gap scanner
    to show pattern alerts alongside each scanner card.  Returns [] on failure.
    """
    try:
        df = fetch_bars(api_key, secret_key, ticker, trade_date, feed=feed)
        if df is None or df.empty or len(df) < 20:
            return []
        return detect_chart_patterns(df)
    except Exception:
        return []


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

def load_accuracy_tracker(user_id: str = "") -> pd.DataFrame:
    """Load MarketBrain accuracy history from Supabase, optionally filtered by user_id."""
    cols = ["timestamp", "symbol", "predicted", "actual", "correct",
            "entry_price", "exit_price", "mfe", "compare_key"]
    if not supabase:
        return pd.DataFrame(columns=cols)
    try:
        q = supabase.table("accuracy_tracker").select("*")
        if user_id:
            try:
                q = q.eq("user_id", user_id)
            except Exception:
                pass
        response = q.execute()
        data = response.data
        if not data:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(data)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        return df
    except Exception as e:
        print(f"Database read error (tracker): {e}")
        return pd.DataFrame(columns=cols)


def log_accuracy_entry(symbol, predicted, actual, compare_key="",
                       entry_price=0.0, exit_price=0.0, mfe=0.0,
                       user_id: str = ""):
    """Log Predicted vs Actual structure to Supabase."""
    if not supabase:
        return
    correct = "✅" if _strip_emoji(predicted) in _strip_emoji(actual) or \
                     _strip_emoji(actual) in _strip_emoji(predicted) else "❌"
    row = {
        "timestamp":   datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol":      symbol,
        "predicted":   predicted,
        "actual":      actual,
        "correct":     correct,
        "entry_price": float(entry_price),
        "exit_price":  float(exit_price),
        "mfe":         float(mfe),
        "compare_key": compare_key,
    }
    if user_id:
        row["user_id"] = user_id
    try:
        supabase.table("accuracy_tracker").insert(row).execute()
        res = supabase.table("accuracy_tracker").select("id", count="exact").execute()
        _n_rows = res.count if res.count else 0
        if _n_rows > 0 and _n_rows % _RECALIBRATE_EVERY == 0:
            recalibrate_brain_weights()
    except Exception as e:
        print(f"Database write error (tracker): {e}")


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


def load_brain_weights(user_id: str = "") -> dict:
    """Load adaptive calibration weights — per-user from Supabase prefs, then local file.

    Per-user weights are stored inside user_preferences.prefs["brain_weights"] so no
    extra table is needed.  Falls back to the global brain_weights.json for backward
    compatibility and anonymous use.
    """
    import json as _json
    defaults = {k: 1.0 for k in _BRAIN_WEIGHT_KEYS}

    # Per-user path (Supabase prefs)
    if user_id:
        try:
            prefs  = load_user_prefs(user_id)
            stored = prefs.get("brain_weights", {})
            if stored and isinstance(stored, dict):
                return {k: float(stored.get(k, defaults.get(k, 1.0)))
                        for k in _BRAIN_WEIGHT_KEYS}
        except Exception:
            pass

    # Global file fallback
    if not os.path.exists(WEIGHTS_FILE):
        return defaults
    try:
        with open(WEIGHTS_FILE) as f:
            stored = _json.load(f)
        return {k: float(stored.get(k, 1.0)) for k in _BRAIN_WEIGHT_KEYS}
    except Exception:
        return defaults


def _save_brain_weights(weights: dict, user_id: str = "") -> None:
    """Persist weights to global file AND, if user_id supplied, to per-user Supabase prefs."""
    import json as _json
    clean = {k: round(float(v), 4) for k, v in weights.items()}

    # Always write global file (backward compat / anonymous)
    try:
        with open(WEIGHTS_FILE, "w") as f:
            _json.dump(clean, f, indent=2)
    except Exception:
        pass

    # Per-user persistence via user_preferences.prefs
    if user_id:
        try:
            prefs = load_user_prefs(user_id)
            prefs["brain_weights"] = clean
            save_user_prefs(user_id, prefs)
        except Exception:
            pass


def recalibrate_brain_weights(user_id: str = "") -> dict:
    """Read the accuracy tracker, compute per-structure accuracy, and update weights.

    Learning rule (smoothed exponential moving average):
      target = 1.5 if acc ≥ 70% | 1.0 if 50-70% | 0.75 if 30-50% | 0.5 if < 30%
      new_weight = old_weight × 0.70  +  target × 0.30   (30% learning rate)

    Structures with fewer than 5 samples are left unchanged (avoid overfitting).
    Returns the updated weights dict.
    """
    weights = load_brain_weights(user_id)
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

        _save_brain_weights(weights, user_id)
    except Exception:
        pass
    return weights


def recalibrate_from_supabase(user_id: str = "") -> dict:
    """Read ALL live outcome data from Supabase and update brain weights.

    Data sources (tracked SEPARATELY, then volume-weighted blended):
      1. accuracy_tracker table  — journal-verified trades (predicted / correct ✅/❌)
      2. paper_trades table      — bot paper trades (predicted / win_loss Win/Loss)

    Blending approach (volume-weighted, adapts with data):
      Each source's accuracy is computed independently per structure, then blended
      proportionally by sample count — NOT a fixed 50/50.
      As data grows, the source with more verified trades earns more influence.

      blend rules per structure:
        both sources have ≥MIN_SAMPLES  → acc = (j_n*j_acc + b_n*b_acc) / (j_n+b_n)
        only journal  has ≥MIN_SAMPLES  → acc = journal_acc
        only bot      has ≥MIN_SAMPLES  → acc = bot_acc
        neither has ≥MIN_SAMPLES        → skip (no update)

      MIN_SAMPLES scales with total verified data:
        <50 total rows  → MIN_SAMPLES = 3   (early days, accept thin data)
        50–200 rows     → MIN_SAMPLES = 5
        200–500 rows    → MIN_SAMPLES = 8
        500+ rows       → MIN_SAMPLES = 12

    Learning rule (adaptive EMA, rate scales with per-structure sample count):
      target = 1.5 if acc ≥ 70% | 1.0 if 50–70% | 0.75 if 30–50% | 0.5 if <30%
      EMA rate scales with n:  <10→0.10 | 10–25→0.15 | 25–50→0.25 | 50–100→0.35 | 100+→0.40
      new_weight = old_weight × (1−rate) + target × rate

    Returns dict:
      {
        "weights":      {structure_key: new_weight, …},
        "deltas":       [{key, old, new, delta, blended_acc, journal_acc, bot_acc,
                          journal_n, bot_n}, …],
        "sources":      {"accuracy_tracker": N, "paper_trades": N, "total": N},
        "calibrated":   bool,
        "timestamp":    iso string,
      }
    """
    import collections as _col

    weights = load_brain_weights(user_id)
    result  = {
        "weights":    weights,
        "deltas":     [],
        "sources":    {"accuracy_tracker": 0, "paper_trades": 0, "total": 0},
        "calibrated": False,
        "timestamp":  datetime.now(EASTERN).isoformat(),
    }

    if not supabase:
        return result

    # ── Separate accumulators per source ──────────────────────────────────
    journal_data: dict = _col.defaultdict(lambda: {"wins": 0, "total": 0})
    bot_data:     dict = _col.defaultdict(lambda: {"wins": 0, "total": 0})

    # Source 1: accuracy_tracker (journal / manual trades)
    try:
        q = supabase.table("accuracy_tracker").select("predicted,correct")
        if user_id:
            q = q.eq("user_id", user_id)
        rows = q.execute().data or []
        for r in rows:
            pred    = str(r.get("predicted", "") or "").strip()
            correct = str(r.get("correct",   "") or "").strip()
            if not pred:
                continue
            wk = _label_to_weight_key(pred)
            journal_data[wk]["total"] += 1
            if "✅" in correct:
                journal_data[wk]["wins"] += 1
        result["sources"]["accuracy_tracker"] = len(rows)
    except Exception as e:
        print(f"recalibrate_from_supabase: accuracy_tracker error: {e}")

    # Source 2: paper_trades (bot automated signals)
    try:
        q = supabase.table("paper_trades").select("predicted,win_loss")
        if user_id:
            q = q.eq("user_id", user_id)
        rows = q.execute().data or []
        for r in rows:
            pred = str(r.get("predicted", "") or "").strip()
            wl   = str(r.get("win_loss",  "") or "").strip().lower()
            if not pred or not wl or wl in ("", "none", "pending"):
                continue
            wk = _label_to_weight_key(pred)
            bot_data[wk]["total"] += 1
            if wl == "win":
                bot_data[wk]["wins"] += 1
        result["sources"]["paper_trades"] = len(rows)
    except Exception as e:
        print(f"recalibrate_from_supabase: paper_trades error: {e}")

    result["sources"]["total"] = (
        result["sources"]["accuracy_tracker"] + result["sources"]["paper_trades"]
    )

    # ── Adaptive blend and EMA update ──────────────────────────────────────
    # MIN_SAMPLES scales with total verified data — avoid overfitting on thin data
    total_verified = result["sources"]["total"]
    if   total_verified < 50:  MIN_SAMPLES = 3
    elif total_verified < 200: MIN_SAMPLES = 5
    elif total_verified < 500: MIN_SAMPLES = 8
    else:                      MIN_SAMPLES = 12

    all_keys = set(journal_data.keys()) | set(bot_data.keys())
    deltas   = []

    for wk in all_keys:
        j = journal_data[wk]
        b = bot_data[wk]

        j_ok = j["total"] >= MIN_SAMPLES
        b_ok = b["total"] >= MIN_SAMPLES

        if not j_ok and not b_ok:
            continue   # not enough data in either source — skip

        j_n   = j["total"]
        b_n   = b["total"]
        j_acc = (j["wins"] / j_n) if j_ok else None
        b_acc = (b["wins"] / b_n) if b_ok else None

        # Volume-weighted blend — sample count determines influence, not a fixed split
        if j_ok and b_ok:
            blended = (j_n * j_acc + b_n * b_acc) / (j_n + b_n)
        elif j_ok:
            blended = j_acc   # only journal has enough data
        else:
            blended = b_acc   # only bot has enough data

        if   blended >= 0.70: target = 1.50
        elif blended >= 0.50: target = 1.00
        elif blended >= 0.30: target = 0.75
        else:                 target = 0.50

        # EMA rate scales with total per-structure samples — more data = faster learning
        total_n = j_n + b_n
        if   total_n >= 100: ema_rate = 0.40
        elif total_n >=  50: ema_rate = 0.35
        elif total_n >=  25: ema_rate = 0.25
        elif total_n >=  10: ema_rate = 0.15
        else:                ema_rate = 0.10

        old_val     = weights.get(wk, 1.0)
        new_val     = round(old_val * (1 - ema_rate) + target * ema_rate, 4)
        weights[wk] = new_val

        deltas.append({
            "key":         wk,
            "old":         round(old_val, 4),
            "new":         new_val,
            "delta":       round(new_val - old_val, 4),
            "blended_acc": round(blended * 100, 1),
            "journal_acc": round(j_acc * 100, 1) if j_ok else None,
            "bot_acc":     round(b_acc * 100, 1) if b_ok else None,
            "journal_n":   j_n,
            "bot_n":       b_n,
            "ema_rate":    ema_rate,
            "min_samples": MIN_SAMPLES,
            "target":      target,
        })

    if deltas:
        _save_brain_weights(weights, user_id)
        result["calibrated"] = True

    result["weights"] = weights
    result["deltas"]  = sorted(deltas, key=lambda x: abs(x["delta"]), reverse=True)
    return result


def brain_weights_summary(user_id: str = "") -> list[dict]:
    """Return a list of dicts for displaying the learned weight table."""
    weights  = load_brain_weights(user_id)
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


# ── Predictive probability engine (signal conditions + outcomes) ──────────────
_SIGNAL_CONDITIONS_FILE = ".local/signal_conditions.json"
_SIGNAL_OUTCOMES_FILE   = ".local/signal_outcomes.json"


def _edge_band(score: float) -> str:
    if score >= 75:   return "75+"
    if score >= 65:   return "65-75"
    if score >= 50:   return "50-65"
    return "<50"


def _rvol_band(rvol: float) -> str:
    if rvol >= 3:  return "3+"
    if rvol >= 2:  return "2-3"
    if rvol >= 1:  return "1-2"
    return "<1"


def save_signal_conditions(user_id: str, ticker: str, trade_date,
                           edge_score: float, rvol: float, structure: str,
                           tcs: float = 0.0, buy_pressure: float = 0.0) -> None:
    """Store signal conditions at analysis time so they can be paired with outcomes later.

    Called from the Main Chart tab every time a full analysis runs.
    Keyed by user_id + ticker + date so repeated analyses on the same day overwrite.
    """
    import json as _json
    key = f"{user_id}_{ticker.upper()}_{str(trade_date)}"
    entry = {
        "ticker":       ticker.upper(),
        "date":         str(trade_date),
        "user_id":      user_id,
        "edge_score":   round(float(edge_score), 1),
        "edge_band":    _edge_band(float(edge_score)),
        "rvol":         round(float(rvol), 2),
        "rvol_band":    _rvol_band(float(rvol)),
        "structure":    str(structure),
        "tcs":          round(float(tcs), 1),
        "buy_pressure": round(float(buy_pressure), 1),
        "saved_at":     datetime.utcnow().isoformat(),
    }
    try:
        data: dict = {}
        os.makedirs(".local", exist_ok=True)
        if os.path.exists(_SIGNAL_CONDITIONS_FILE):
            with open(_SIGNAL_CONDITIONS_FILE) as _f:
                data = _json.load(_f)
        data[key] = entry
        with open(_SIGNAL_CONDITIONS_FILE, "w") as _f:
            _json.dump(data, _f)
    except Exception:
        pass


def get_signal_conditions(user_id: str, ticker: str, trade_date) -> dict:
    """Retrieve stored signal conditions for a specific user+ticker+date."""
    import json as _json
    key = f"{user_id}_{ticker.upper()}_{str(trade_date)}"
    try:
        if os.path.exists(_SIGNAL_CONDITIONS_FILE):
            with open(_SIGNAL_CONDITIONS_FILE) as _f:
                data = _json.load(_f)
            return data.get(key, {})
    except Exception:
        pass
    return {}


def log_signal_outcome(user_id: str, ticker: str, trade_date,
                       outcome_win: bool, outcome_pct: float = 0.0) -> None:
    """Pair stored signal conditions with a verified outcome.

    Called when the user marks a prediction correct/wrong in the EOD review.
    Deduplicates by user+ticker+date so re-marking updates the record.
    """
    import json as _json
    conditions = get_signal_conditions(user_id, ticker, str(trade_date))
    edge  = conditions.get("edge_score", 0.0)
    rvol  = conditions.get("rvol", 0.0)
    struct = conditions.get("structure", "Unknown")
    tcs   = conditions.get("tcs", 0.0)

    entry = {
        "user_id":      user_id,
        "ticker":       ticker.upper(),
        "date":         str(trade_date),
        "edge_score":   float(conditions.get("edge_score", edge)),
        "edge_band":    _edge_band(float(edge)),
        "rvol":         float(rvol),
        "rvol_band":    _rvol_band(float(rvol)),
        "structure":    str(struct),
        "tcs":          float(tcs),
        "buy_pressure": float(conditions.get("buy_pressure", 0.0)),
        "outcome_win":  bool(outcome_win),
        "outcome_pct":  round(float(outcome_pct), 2),
        "logged_at":    datetime.utcnow().isoformat(),
    }
    try:
        os.makedirs(".local", exist_ok=True)
        outcomes: list = []
        if os.path.exists(_SIGNAL_OUTCOMES_FILE):
            with open(_SIGNAL_OUTCOMES_FILE) as _f:
                outcomes = _json.load(_f)
        outcomes = [o for o in outcomes if not (
            o.get("user_id") == user_id and
            o.get("ticker")  == ticker.upper() and
            o.get("date")    == str(trade_date)
        )]
        outcomes.append(entry)
        with open(_SIGNAL_OUTCOMES_FILE, "w") as _f:
            _json.dump(outcomes, _f)
    except Exception:
        pass


def compute_win_rates(user_id: str, min_samples: int = 3) -> dict:
    """Compute historical win rates grouped by condition cluster from logged outcomes.

    Returns a dict with three sub-keys:
      "_total"    : {"n": ..., "win_rate": ...}
      "_by_edge"  : {band: {"n": ..., "win_rate": ...}, ...}
      "_by_struct": {structure: {"n": ..., "win_rate": ..., "avg_pct": ...}, ...}
      <cluster>   : {"n": ..., "wins": ..., "win_rate": ..., "avg_pct": ..., "sufficient": bool}
                    where <cluster> = "edge:<band> rvol:<band> struct:<structure>"
    """
    import json as _json
    from collections import defaultdict
    try:
        if not os.path.exists(_SIGNAL_OUTCOMES_FILE):
            return {}
        with open(_SIGNAL_OUTCOMES_FILE) as _f:
            all_outcomes = _json.load(_f)
        outcomes = [o for o in all_outcomes if o.get("user_id") == user_id]
        if not outcomes:
            return {}

        result: dict = {}

        # Full cluster grouping
        clusters: dict = defaultdict(list)
        for o in outcomes:
            k = (f"edge:{o.get('edge_band','?')} "
                 f"rvol:{o.get('rvol_band','?')} "
                 f"struct:{o.get('structure','?')}")
            clusters[k].append(o)
        for k, grp in clusters.items():
            n    = len(grp)
            wins = sum(1 for o in grp if o.get("outcome_win"))
            avg  = (sum(o.get("outcome_pct", 0) for o in grp) / n) if n else 0
            result[k] = {
                "n":          n,
                "wins":       wins,
                "win_rate":   round(wins / n, 3) if n else 0,
                "avg_pct":    round(avg, 2),
                "sufficient": n >= min_samples,
            }

        # By edge band
        by_edge: dict = defaultdict(list)
        for o in outcomes:
            by_edge[o.get("edge_band", "?")].append(o)
        result["_by_edge"] = {
            band: {
                "n":        len(g),
                "win_rate": round(sum(1 for o in g if o.get("outcome_win")) / len(g), 3),
            }
            for band, g in by_edge.items() if g
        }

        # By structure
        by_struct: dict = defaultdict(list)
        for o in outcomes:
            by_struct[o.get("structure", "?")].append(o)
        result["_by_struct"] = {}
        for struct, grp in by_struct.items():
            n    = len(grp)
            wins = sum(1 for o in grp if o.get("outcome_win"))
            avg  = (sum(o.get("outcome_pct", 0) for o in grp) / n) if n else 0
            result["_by_struct"][struct] = {
                "n":        n,
                "win_rate": round(wins / n, 3) if n else 0,
                "avg_pct":  round(avg, 2),
            }

        # Overall
        n_total = len(outcomes)
        result["_total"] = {
            "n":        n_total,
            "win_rate": round(
                sum(1 for o in outcomes if o.get("outcome_win")) / n_total, 3
            ) if n_total else 0,
        }
        return result
    except Exception:
        return {}


def get_predictive_context(user_id: str, edge_score: float,
                           rvol: float, structure: str) -> dict:
    """Return historical win-rate context for the current signal conditions.

    Tries exact cluster match first; falls back to edge-band and overall.
    Returns empty dict if no signal log exists yet.
    """
    rates = compute_win_rates(user_id, min_samples=3)
    if not rates:
        return {}

    cluster_key = (f"edge:{_edge_band(edge_score)} "
                   f"rvol:{_rvol_band(rvol)} "
                   f"struct:{structure}")
    exact      = rates.get(cluster_key, {})
    by_edge    = rates.get("_by_edge", {}).get(_edge_band(edge_score), {})
    by_struct  = rates.get("_by_struct", {}).get(structure, {})
    overall    = rates.get("_total", {})

    return {
        "cluster_key": cluster_key,
        "exact":       exact if exact.get("sufficient") else {},
        "by_edge":     by_edge,
        "by_struct":   by_struct,
        "overall":     overall,
    }


# ── Monte Carlo equity simulation ─────────────────────────────────────────────

def monte_carlo_equity_curves(
    trade_results: list,
    starting_equity: float = 10_000.0,
    n_simulations: int = 1_000,
    risk_pct: float = 0.02,
    slippage_drag_pct: float = 0.0,
) -> dict:
    """Simulate N equity curves by randomly reshuffling the trade sequence.

    Each trade risks `risk_pct` of current equity.  A win grows equity by
    (risk_pct × |aft_move_pct| / 100) and a loss shrinks it by risk_pct.
    slippage_drag_pct is subtracted from every trade (win or lose).

    Returns P10 / P50 / P90 equity curves and final-equity distribution stats.
    Empty dict if fewer than 3 trades.
    """
    import random
    import numpy as np

    outcomes = []
    for r in trade_results:
        move = r.get("aft_move_pct", 0.0)
        win  = r.get("win_loss", "") == "Win"
        ret  = (risk_pct * (abs(move) / 100.0) if win else -risk_pct) - slippage_drag_pct
        outcomes.append(float(ret))

    if len(outcomes) < 3:
        return {}

    random.seed(42)
    all_curves   = []
    final_equities = []

    for _ in range(n_simulations):
        shuffled = outcomes.copy()
        random.shuffle(shuffled)
        equity = starting_equity
        curve  = [equity]
        for ret in shuffled:
            equity = max(0.01, equity * (1.0 + ret))
            curve.append(equity)
        all_curves.append(curve)
        final_equities.append(equity)

    arr  = np.array(all_curves)
    p10  = np.percentile(arr, 10, axis=0).tolist()
    p50  = np.percentile(arr, 50, axis=0).tolist()
    p90  = np.percentile(arr, 90, axis=0).tolist()

    final_equities.sort()
    profitable = sum(1 for e in final_equities if e > starting_equity)

    return {
        "p10":            p10,
        "p50":            p50,
        "p90":            p90,
        "final_equities": final_equities,
        "pct_profitable": round(profitable / len(final_equities) * 100, 1),
        "median_final":   round(float(np.percentile(final_equities, 50)), 2),
        "p10_final":      round(float(np.percentile(final_equities, 10)), 2),
        "p90_final":      round(float(np.percentile(final_equities, 90)), 2),
        "n_trades":       len(outcomes),
        "n_simulations":  n_simulations,
        "starting":       starting_equity,
    }


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
if _ST_AVAILABLE and st is not None and not st.session_state.get("_position_loaded"):
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


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  ⛔  READ-ONLY — DO NOT MODIFY                                              ║
# ║  classify_day_structure()                                                    ║
# ║  Core 7-structure IB-interaction decision tree.  Any change here breaks     ║
# ║  the entire signal engine and all downstream scoring.                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
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


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  ⛔  READ-ONLY — DO NOT MODIFY                                              ║
# ║  compute_structure_probabilities()                                           ║
# ║  Probabilistic scorer using the same decision tree as classify_day_         ║
# ║  structure().  Weights are hand-calibrated — do not touch.                  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
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


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  ⛔  READ-ONLY — DO NOT MODIFY                                              ║
# ║  compute_buy_sell_pressure()                                                 ║
# ║  Tape-reading signal (uptick ratio, delta, absorption).  Core input to      ║
# ║  Edge Score and TCS.  Calibrated thresholds — do not touch.                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
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


def compute_order_flow_signals(df, ib_high=None, ib_low=None):
    """Tier 2 order flow proxy signals derived from 1-min OHLCV bars.

    Signals returned (all based on bar structure — no L2 data required):

    pressure_accel   : "Accelerating" | "Decelerating" | "Flat"
                       Compares 3-bar vs 10-bar buy pressure windows.
    pressure_short   : buy% for last 3 bars  (0-100)
    pressure_medium  : buy% for last 10 bars (0-100)
    pressure_long    : buy% for last 20 bars (0-100)

    bar_quality      : 0-100. % of last 10 bars where close > midpoint of bar range.
                       100 = all bars closed near high; 0 = all closed near low.
    bar_quality_label: "Buyers Dominant" | "Sellers Dominant" | "Contested"

    vol_surge_ratio  : current-bar volume / 10-bar avg volume (1.0 = baseline)
    vol_surge_label  : "Surge" | "Above Avg" | "Normal" | "Thin"

    streak           : int. +N = N consecutive bars closing higher than prior close.
                       -N = N consecutive bars closing lower than prior close.
    streak_label     : "Strong Upward Tape" | "Moderate Upward Tape" | etc.

    ib_proximity     : "At IB High" | "At IB Low" | "Mid-Range" | None
    ib_vol_confirm   : True if vol_surge_ratio >= 1.5 while at IB extreme

    composite_signal : "Strong Buy Flow" | "Moderate Buy Flow" | "Neutral" |
                       "Moderate Sell Flow" | "Strong Sell Flow"
    composite_score  : -100 to +100 (positive = bullish flow)
    """
    if df is None or df.empty or len(df) < 3:
        return None

    _df = df.dropna(subset=["open", "high", "low", "close", "volume"]).copy()
    if len(_df) < 3:
        return None

    # ── Per-bar buy fraction (reuse CLV+Tick formula) ─────────────────────────
    hl = (_df["high"] - _df["low"]).replace(0, np.nan)
    sell_clv  = ((_df["high"] - _df["close"]) / hl).fillna(0.5).clip(0, 1)
    close_prev = _df["close"].shift(1)
    sell_tick  = np.where(
        _df["close"] < close_prev, 1.0,
        np.where(_df["close"] > close_prev, 0.0, 0.5)
    )
    sell_frac = pd.Series(
        ((sell_clv + pd.Series(sell_tick, index=_df.index)) / 2.0).values,
        index=_df.index,
    ).clip(0, 1)
    buy_frac = 1.0 - sell_frac

    def _win_buy_pct(sub_buy, sub_vol):
        bv = (sub_buy * sub_vol).sum()
        tv = sub_vol.sum()
        return float(bv / tv * 100.0) if tv > 0 else 50.0

    n = len(_df)
    buy_f = buy_frac.values
    vols  = _df["volume"].values

    short_n  = min(3,  n)
    medium_n = min(10, n)
    long_n   = min(20, n)

    p_short  = _win_buy_pct(buy_f[-short_n:],  vols[-short_n:])
    p_medium = _win_buy_pct(buy_f[-medium_n:], vols[-medium_n:])
    p_long   = _win_buy_pct(buy_f[-long_n:],   vols[-long_n:])

    accel_delta = p_short - p_medium
    if accel_delta > 4:
        pressure_accel = "Accelerating"
    elif accel_delta < -4:
        pressure_accel = "Decelerating"
    else:
        pressure_accel = "Flat"

    # ── Bar quality (close vs midpoint of each bar's range) ──────────────────
    bq_n    = min(10, n)
    bq_sub  = _df.tail(bq_n)
    mid     = (bq_sub["high"] + bq_sub["low"]) / 2.0
    bar_quality = float((bq_sub["close"] > mid).sum() / bq_n * 100.0)
    if bar_quality >= 65:
        bar_quality_label = "Buyers Dominant"
    elif bar_quality <= 35:
        bar_quality_label = "Sellers Dominant"
    else:
        bar_quality_label = "Contested"

    # ── Volume surge ratio (last bar vs 10-bar avg) ───────────────────────────
    avg_vol_10 = float(np.mean(vols[-min(10, n):])) if n >= 2 else 1.0
    cur_vol    = float(vols[-1]) if n >= 1 else 0.0
    vol_surge_ratio = (cur_vol / avg_vol_10) if avg_vol_10 > 0 else 1.0
    if vol_surge_ratio >= 2.0:
        vol_surge_label = "Surge"
    elif vol_surge_ratio >= 1.3:
        vol_surge_label = "Above Avg"
    elif vol_surge_ratio >= 0.7:
        vol_surge_label = "Normal"
    else:
        vol_surge_label = "Thin"

    # ── Consecutive close streak ───────────────────────────────────────────────
    closes = _df["close"].values
    streak = 0
    if len(closes) >= 2:
        direction = 1 if closes[-1] >= closes[-2] else -1
        for i in range(len(closes) - 2, 0, -1):
            if direction == 1 and closes[i] >= closes[i - 1]:
                streak += 1
            elif direction == -1 and closes[i] <= closes[i - 1]:
                streak -= 1
            else:
                break
        if direction == 1:
            streak = max(streak, 1)
        else:
            streak = min(streak, -1)

    if streak >= 5:
        streak_label = "Strong Upward Tape"
    elif streak >= 3:
        streak_label = "Moderate Upward Tape"
    elif streak >= 1:
        streak_label = "Mild Upward Tape"
    elif streak <= -5:
        streak_label = "Strong Downward Tape"
    elif streak <= -3:
        streak_label = "Moderate Downward Tape"
    elif streak <= -1:
        streak_label = "Mild Downward Tape"
    else:
        streak_label = "Mixed Tape"

    # ── IB proximity + volume confirmation ────────────────────────────────────
    last_close  = float(closes[-1])
    ib_proximity    = None
    ib_vol_confirm  = False
    if ib_high is not None and ib_low is not None:
        ib_range = ib_high - ib_low
        if ib_range > 0:
            if last_close >= ib_high - 0.05 * ib_range:
                ib_proximity   = "At IB High"
                ib_vol_confirm = vol_surge_ratio >= 1.5
            elif last_close <= ib_low + 0.05 * ib_range:
                ib_proximity   = "At IB Low"
                ib_vol_confirm = vol_surge_ratio >= 1.5
            else:
                ib_proximity = "Mid-Range"

    # ── Composite score (-100 to +100) ────────────────────────────────────────
    # Components:
    #   pressure short vs 50  → weight 35
    #   bar quality vs 50     → weight 30
    #   streak contribution   → weight 20
    #   vol surge             → weight 15 (surge amplifies direction)
    p_score   = (p_short - 50.0) * (35.0 / 50.0)          # -35 to +35
    bq_score  = (bar_quality - 50.0) * (30.0 / 50.0)       # -30 to +30
    str_score = float(np.clip(streak, -5, 5)) / 5.0 * 20.0 # -20 to +20
    if vol_surge_ratio >= 1.5:
        vol_score = 10.0 if p_short >= 50 else -10.0
    elif vol_surge_ratio >= 1.2:
        vol_score = 5.0 if p_short >= 50 else -5.0
    else:
        vol_score = 0.0
    composite_score = float(np.clip(p_score + bq_score + str_score + vol_score, -100, 100))

    if composite_score >= 40:
        composite_signal = "Strong Buy Flow"
    elif composite_score >= 15:
        composite_signal = "Moderate Buy Flow"
    elif composite_score <= -40:
        composite_signal = "Strong Sell Flow"
    elif composite_score <= -15:
        composite_signal = "Moderate Sell Flow"
    else:
        composite_signal = "Neutral"

    return {
        "pressure_accel":    pressure_accel,
        "pressure_short":    round(p_short,  1),
        "pressure_medium":   round(p_medium, 1),
        "pressure_long":     round(p_long,   1),
        "accel_delta":       round(accel_delta, 1),
        "bar_quality":       round(bar_quality, 1),
        "bar_quality_label": bar_quality_label,
        "vol_surge_ratio":   round(vol_surge_ratio, 2),
        "vol_surge_label":   vol_surge_label,
        "streak":            streak,
        "streak_label":      streak_label,
        "ib_proximity":      ib_proximity,
        "ib_vol_confirm":    ib_vol_confirm,
        "composite_signal":  composite_signal,
        "composite_score":   round(composite_score, 1),
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


def start_stream(api_key, secret_key, ticker, feed_str,
                 historical_bars: list | None = None):
    """Start the WebSocket stream for `ticker`.

    historical_bars — optional list of bar dicts pre-loaded from today's
        session (9:30 AM to now).  If provided, live_bars is seeded with
        this data so the volume profile, IB, VWAP, and TCS are all computed
        from the full day context the moment the stream starts — not from
        scratch on the first arriving bar.

    Bar dict format:
        {"open": float, "high": float, "low": float,
         "close": float, "volume": float, "timestamp": <Timestamp>}
    """
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
    st.session_state.live_bars = list(historical_bars) if historical_bars else []
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
    df["vwap"] = compute_vwap(df)
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


def load_journal(user_id: str = "") -> "pd.DataFrame":
    """Load the trade journal from Supabase, optionally filtered by user_id."""
    if not supabase:
        return pd.DataFrame(columns=_JOURNAL_COLS)
    try:
        q = supabase.table("trade_journal").select("*")
        if user_id:
            try:
                q = q.eq("user_id", user_id)
            except Exception:
                pass
        response = q.execute()
        data = response.data
        if not data:
            return pd.DataFrame(columns=_JOURNAL_COLS)
        df = pd.DataFrame(data)
        for col in _JOURNAL_COLS:
            if col not in df.columns:
                df[col] = ""
        return df[_JOURNAL_COLS]
    except Exception as e:
        print(f"Database read error (journal): {e}")
        return pd.DataFrame(columns=_JOURNAL_COLS)


def save_journal_entry(entry: dict, user_id: str = ""):
    """Save a new trade journal entry to Supabase."""
    if not supabase:
        print("Error: Supabase not connected.")
        return
    try:
        row = {k: entry.get(k, None) for k in _JOURNAL_COLS}
        if user_id:
            row["user_id"] = user_id
        supabase.table("trade_journal").insert(row).execute()
    except Exception as e:
        print(f"Database write error (journal): {e}")


def ensure_telegram_columns() -> bool:
    """Add Telegram-logging columns to trade_journal if they don't exist.
    Safe to call on every bot startup — uses IF NOT EXISTS.
    Returns True on success."""
    if not supabase:
        return False
    cols = [
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual'",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS entry_price FLOAT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS exit_price FLOAT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS win_loss TEXT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS pnl_pct FLOAT",
        "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS dedup_key TEXT",
    ]
    try:
        for sql in cols:
            supabase.rpc("exec_sql", {"query": sql}).execute()
        return True
    except Exception:
        try:
            supabase.table("trade_journal").select("source,entry_price,exit_price,win_loss,pnl_pct,dedup_key").limit(1).execute()
            return True
        except Exception as e:
            print(f"ensure_telegram_columns warning: {e}")
            return False


def save_telegram_trade(ticker: str, win_loss: str, entry_price: float,
                        exit_price: float, notes: str = "",
                        user_id: str = "", trade_date=None) -> dict:
    """Insert a Telegram-logged trade into trade_journal with dedup protection.

    Returns dict: {saved: bool, duplicate: bool, pnl_pct: float, error: str|None}
    """
    if not supabase:
        return {"saved": False, "duplicate": False, "pnl_pct": 0.0,
                "error": "Supabase not connected"}
    try:
        from datetime import date as _date, datetime as _dt
        import math

        today_str  = str(trade_date or _date.today())
        entry_p    = round(float(entry_price), 4)
        exit_p     = round(float(exit_price), 4)
        pnl_pct    = round((exit_p - entry_p) / entry_p * 100, 2) if entry_p != 0 else 0.0
        dedup_key  = f"{ticker.upper()}_{today_str}_{entry_p}_{exit_p}"

        # Dedup check — prefer dedup_key column, fall back to grade_reason prefix
        _grade_reason_key = f"tg|{dedup_key}"
        try:
            existing = (supabase.table("trade_journal")
                        .select("id")
                        .eq("dedup_key", dedup_key)
                        .execute())
        except Exception:
            existing = (supabase.table("trade_journal")
                        .select("id")
                        .eq("grade_reason", _grade_reason_key)
                        .execute())
        if existing.data:
            return {"saved": False, "duplicate": True,
                    "pnl_pct": pnl_pct, "error": None}

        # Dedup via grade_reason when dedup_key column may not exist yet
        _grade_reason = f"tg|{dedup_key}"

        # Packed notes: "[Entry: X → Exit: Y | Win | +Z%] user note"
        sign = "+" if pnl_pct >= 0 else ""
        _packed_notes = (
            f"[Entry: {entry_p} → Exit: {exit_p} | "
            f"{'Win' if win_loss.lower()=='win' else 'Loss'} | {sign}{pnl_pct:.1f}%]"
        )
        if notes:
            _packed_notes += f" {notes}"

        # Core row using always-existing columns
        row = {
            "timestamp":    _dt.utcnow().isoformat(),
            "ticker":       ticker.upper(),
            "price":        entry_p,
            "notes":        _packed_notes,
            "structure":    "",
            "tcs":          None,
            "rvol":         None,
            "ib_high":      None,
            "ib_low":       None,
            "grade":        "W" if win_loss.lower() == "win" else "L",
            "grade_reason": _grade_reason,
        }
        if user_id:
            row["user_id"] = user_id

        # Try to add extended columns — gracefully skip if they don't exist yet
        try:
            supabase.table("trade_journal").select("source").limit(1).execute()
            row["source"]      = "telegram"
            row["entry_price"] = entry_p
            row["exit_price"]  = exit_p
            row["win_loss"]    = win_loss.capitalize()
            row["pnl_pct"]     = pnl_pct
            row["dedup_key"]   = dedup_key
        except Exception:
            pass  # Extended columns not yet added — core columns still work

        supabase.table("trade_journal").insert(row).execute()
        return {"saved": True, "duplicate": False,
                "pnl_pct": pnl_pct, "error": None}

    except Exception as e:
        return {"saved": False, "duplicate": False,
                "pnl_pct": 0.0, "error": str(e)}


def backfill_unknown_structures(api_key: str, secret_key: str, user_id: str,
                                feed: str = "iex") -> dict:
    """Re-enrich journal rows where structure is Unknown/null/empty.

    Fetches the actual bar data for each affected row, runs enrich_trade_context,
    and patches the row in Supabase with the correct structure, tcs, rvol,
    ib_high, and ib_low values.

    Returns dict: {updated: int, failed: int, skipped: int, errors: list}
    """
    if not supabase:
        return {"updated": 0, "failed": 0, "skipped": 0, "errors": ["Supabase not connected"]}

    _STALE = {"Unknown", "unknown", "", None}

    try:
        q = supabase.table("trade_journal").select("*")
        if user_id:
            q = q.eq("user_id", user_id)
        resp = q.execute()
        rows = resp.data or []
    except Exception as e:
        return {"updated": 0, "failed": 0, "skipped": 0, "errors": [str(e)]}

    targets = [r for r in rows if r.get("structure") in _STALE]
    if not targets:
        return {"updated": 0, "failed": 0, "skipped": len(rows), "errors": []}

    updated, failed, errors = 0, 0, []
    for row in targets:
        row_id   = row.get("id")
        ticker   = row.get("ticker", "")
        ts_raw   = row.get("timestamp", "")
        if not row_id or not ticker or not ts_raw:
            failed += 1
            continue
        try:
            from dateutil.parser import parse as _dp
            trade_dt = _dp(str(ts_raw)).date()
        except Exception:
            failed += 1
            continue
        try:
            ctx = enrich_trade_context(api_key, secret_key, ticker, trade_dt, feed=feed)
            if not ctx:
                failed += 1
                errors.append(f"{ticker} {trade_dt}: enrich returned empty")
                continue
            patch = {k: ctx[k] for k in ("structure", "tcs", "rvol", "ib_high", "ib_low")
                     if k in ctx and ctx[k] is not None}
            if not patch:
                failed += 1
                continue
            supabase.table("trade_journal").update(patch).eq("id", row_id).execute()
            updated += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{ticker} {trade_dt}: {exc}")

    return {"updated": updated, "failed": failed,
            "skipped": len(rows) - len(targets), "errors": errors}


def parse_webull_csv(df: "pd.DataFrame") -> list:
    """Parse a Webull order-history CSV DataFrame into round-trip trade dicts.

    Handles multiple Webull export formats (column name variations).
    Pairs Buy→Sell using FIFO per ticker. Open positions (no matching sell)
    are silently skipped — they have not yet been closed.

    Returns a list of dicts compatible with save_journal_entry().
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    cols_lower = {c.lower(): c for c in df.columns}

    def _find(candidates):
        for cand in candidates:
            if cand in cols_lower:
                return cols_lower[cand]
        for cand in candidates:
            for col_l, col in cols_lower.items():
                if cand in col_l:
                    return col
        return None

    sym_col    = _find(["symbol", "sym.", "ticker", "sym", "stock"])
    side_col   = _find(["side", "b/s", "action", "type", "order side"])
    qty_col    = _find(["filled qty", "fill qty", "qty filled", "executed qty",
                         "filled", "qty", "quantity", "shares"])
    price_col  = _find(["avg price", "avg. price", "fill price", "exec price",
                         "filled price", "executed price", "price"])
    time_col   = _find(["create time", "filled time", "time placed", "order time",
                         "time", "date", "datetime"])
    status_col = _find(["status"])

    if not sym_col or not side_col or not qty_col or not price_col or not time_col:
        return []

    if status_col:
        df = df[df[status_col].astype(str).str.lower().str.contains("fill", na=False)]

    df["_side"] = df[side_col].astype(str).str.lower().str.strip()
    df = df[df["_side"].str.contains("buy|sell", na=False)]

    df["_qty"]   = pd.to_numeric(df[qty_col],   errors="coerce")
    df["_price"] = pd.to_numeric(df[price_col], errors="coerce")
    df["_time"]  = pd.to_datetime(df[time_col], errors="coerce", infer_datetime_format=True)
    df["_sym"]   = df[sym_col].astype(str).str.upper().str.strip()

    df = df.dropna(subset=["_qty", "_price", "_time", "_sym"]).sort_values("_time")

    buy_queues: dict = {}
    trades = []

    for _, row in df.iterrows():
        sym   = row["_sym"]
        side  = row["_side"]
        qty   = float(row["_qty"])
        price = float(row["_price"])
        ts    = row["_time"]

        if "buy" in side:
            buy_queues.setdefault(sym, []).append(
                {"time": ts, "price": price, "qty": qty, "remaining": qty}
            )

        elif "sell" in side:
            queue = buy_queues.get(sym, [])
            if not queue:
                continue

            qty_left       = qty
            entry_cost     = 0.0
            entry_qty_tot  = 0.0
            entry_price_wt = 0.0
            entry_time     = None

            while qty_left > 0 and queue:
                buy     = queue[0]
                matched = min(buy["remaining"], qty_left)
                entry_cost     += buy["price"] * matched
                entry_price_wt += buy["price"] * matched
                entry_qty_tot  += matched
                if entry_time is None:
                    entry_time = buy["time"]
                buy["remaining"] -= matched
                qty_left         -= matched
                if buy["remaining"] <= 0:
                    queue.pop(0)

            if entry_qty_tot == 0:
                continue

            avg_entry  = entry_price_wt / entry_qty_tot
            sell_total = price * qty
            pnl        = sell_total - entry_cost
            pnl_pct    = pnl / entry_cost * 100 if entry_cost > 0 else 0
            shares_int = int(round(entry_qty_tot))

            if pnl_pct > 5:
                grade = "A"
            elif pnl_pct > 1:
                grade = "B"
            elif pnl_pct > -2:
                grade = "C"
            elif pnl_pct > -5:
                grade = "D"
            else:
                grade = "F"

            trades.append({
                "timestamp":   entry_time.isoformat() if hasattr(entry_time, "isoformat") else str(entry_time),
                "ticker":      sym,
                "price":       round(avg_entry, 4),
                "exit_price":  round(price, 4),       # sell price — used by analytics
                "mfe":         round(pnl, 2),         # P&L dollars — used by analytics
                "shares":      shares_int,
                "structure":   "Unknown",
                "tcs":         None,
                "rvol":        None,
                "ib_high":     None,
                "ib_low":      None,
                "exit_timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "notes": (
                    f"Webull import | Exit: ${price:.4f} | "
                    f"P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%) | "
                    f"Shares: {shares_int} | "
                    f"ExitTS: {ts.strftime('%Y-%m-%d %H:%M') if hasattr(ts, 'strftime') else ts}"
                ),
                "grade":        grade,
                "grade_reason": f"Auto-graded from P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)",
            })

    return trades


def compute_journal_model_crossref(journal_df: "pd.DataFrame",
                                   bt_df: "pd.DataFrame") -> dict:
    """Cross-reference personal trade journal against backtest model predictions.

    Joins on ticker + trade date.  Returns a dict with:
        matched_df   : rows where both journal entry and model prediction exist
        unmatched_n  : journal trades with no model prediction on that day
        by_structure : list of dicts {structure, trades, grades, d_f_pct, avg_pnl_est}
        filter_sim   : {blocked, allowed, d_f_blocked_pct, d_f_allowed_pct}
        alignment    : pct of D/F trades the model had flagged as Neutral/NtrlExtreme
    """
    import re

    empty = {
        "matched_df": pd.DataFrame(),
        "unmatched_n": 0,
        "by_structure": [],
        "filter_sim": {},
        "alignment": 0.0,
    }

    if journal_df is None or journal_df.empty:
        return empty
    if bt_df is None or bt_df.empty:
        return empty

    jdf = journal_df.copy()
    bdf = bt_df.copy()

    jdf["_ticker"] = jdf["ticker"].astype(str).str.upper().str.strip()
    jdf["_date"]   = pd.to_datetime(jdf["timestamp"], errors="coerce").dt.date.astype(str)

    bdf["_ticker"] = bdf["ticker"].astype(str).str.upper().str.strip()
    bdf["_date"]   = bdf["sim_date"].astype(str).str[:10]

    # Deduplicate backtest rows: multiple calibration runs (IEX → SIP) create
    # duplicate (ticker, date) entries.  Keep the row with the best TCS data so
    # the merge stays 1-to-1 with the journal.
    if "tcs" in bdf.columns:
        bdf = bdf.sort_values(
            by=["_ticker", "_date", "tcs"],
            ascending=[True, True, False],
            na_position="last",
        )
    else:
        bdf = bdf.sort_values(by=["_ticker", "_date"])
    bdf = bdf.drop_duplicates(subset=["_ticker", "_date"], keep="first").reset_index(drop=True)

    # Rename backtest columns that clash with journal column names (tcs, ib_high, ib_low)
    # so pandas merge doesn't silently rename them to _x/_y suffixes.
    _bt_rename = {"tcs": "bt_tcs", "ib_high": "bt_ib_high", "ib_low": "bt_ib_low"}
    bdf = bdf.rename(columns={k: v for k, v in _bt_rename.items() if k in bdf.columns})

    _PNL_RE = re.compile(r"P&L:\s*\$([\-\+]?[\d\.]+)")

    def _extract_pnl(notes_str):
        m = _PNL_RE.search(str(notes_str))
        return float(m.group(1)) if m else None

    jdf["_pnl_est"] = jdf["notes"].apply(_extract_pnl)

    _bt_cols = ["_ticker", "_date", "predicted", "bt_tcs", "win_loss",
                "follow_thru_pct", "bt_ib_high", "bt_ib_low", "open_price"]
    _bt_cols = [c for c in _bt_cols if c in bdf.columns]

    merged = jdf.merge(
        bdf[_bt_cols],
        on=["_ticker", "_date"],
        how="left",
    )

    unmatched_n = merged["predicted"].isna().sum()
    matched_df  = merged[merged["predicted"].notna()].copy()

    if matched_df.empty:
        return {**empty, "unmatched_n": int(unmatched_n)}

    _NEUTRAL_STRUCTS = {"Neutral", "Ntrl Extreme", "Ntrl_Extreme",
                        "Neutral Extreme", "NtrlExtreme"}

    by_structure = []
    for struct, grp in matched_df.groupby("predicted"):
        grades   = grp["grade"].fillna("?").tolist()
        df_count = sum(1 for g in grades if g in {"D", "F"})
        df_pct   = round(df_count / len(grades) * 100, 1) if grades else 0.0
        pnl_vals = grp["_pnl_est"].dropna().tolist()
        avg_pnl  = round(sum(pnl_vals) / len(pnl_vals), 2) if pnl_vals else None
        grade_counts = {}
        for g in grades:
            grade_counts[g] = grade_counts.get(g, 0) + 1
        by_structure.append({
            "structure":   struct,
            "trades":      len(grp),
            "grade_counts": grade_counts,
            "d_f_pct":     df_pct,
            "avg_pnl_est": avg_pnl,
        })
    by_structure.sort(key=lambda x: -x["trades"])

    is_neutral = matched_df["predicted"].isin(_NEUTRAL_STRUCTS)
    tcs_vals   = pd.to_numeric(matched_df.get("bt_tcs", pd.Series(dtype=float)),
                               errors="coerce")
    high_tcs   = tcs_vals >= 75

    would_block = is_neutral | (~high_tcs)
    blocked     = matched_df[would_block]
    allowed     = matched_df[~would_block]

    def _df_pct_of(df):
        if df.empty:
            return 0.0
        total = len(df)
        bad   = sum(1 for g in df["grade"].fillna("?") if g in {"D", "F"})
        return round(bad / total * 100, 1)

    filter_sim = {
        "blocked_n":       int(len(blocked)),
        "allowed_n":       int(len(allowed)),
        "d_f_blocked_pct": _df_pct_of(blocked),
        "d_f_allowed_pct": _df_pct_of(allowed),
        "pnl_blocked":     round(blocked["_pnl_est"].dropna().sum(), 2),
        "pnl_allowed":     round(allowed["_pnl_est"].dropna().sum(), 2),
    }

    df_trades = matched_df[matched_df["grade"].isin({"D", "F"})]
    if df_trades.empty:
        alignment = 0.0
    else:
        warned = df_trades["predicted"].isin(_NEUTRAL_STRUCTS).sum()
        alignment = round(warned / len(df_trades) * 100, 1)

    # ── Within-Neutral Quality Analysis ─────────────────────────────────────
    neutral_rows = matched_df[matched_df["predicted"].isin(_NEUTRAL_STRUCTS)].copy()
    neutral_quality: dict = {"tcs_buckets": [], "ib_position": [], "recommendation": ""}

    if not neutral_rows.empty:
        tcs_num = pd.to_numeric(neutral_rows.get("bt_tcs", pd.Series(dtype=float)),
                                errors="coerce")
        neutral_rows = neutral_rows.copy()
        neutral_rows["_tcs_num"] = tcs_num

        def _tcs_bucket(v):
            if pd.isna(v):    return "No TCS"
            if v < 40:        return "< 40 (Weak)"
            if v < 55:        return "40–55 (Moderate)"
            if v < 70:        return "55–70 (Strong)"
            return "70+ (Extreme)"

        _bucket_order = ["< 40 (Weak)", "40–55 (Moderate)", "55–70 (Strong)",
                         "70+ (Extreme)", "No TCS"]
        neutral_rows["_tcs_bucket"] = neutral_rows["_tcs_num"].apply(_tcs_bucket)

        tcs_buckets = []
        for bucket in _bucket_order:
            grp = neutral_rows[neutral_rows["_tcs_bucket"] == bucket]
            if grp.empty:
                continue
            grades = grp["grade"].fillna("?").tolist()
            ab_ct  = sum(1 for g in grades if g in {"A", "B"})
            df_ct  = sum(1 for g in grades if g in {"D", "F"})
            ab_pct = round(ab_ct / len(grades) * 100, 1)
            df_pct = round(df_ct / len(grades) * 100, 1)
            gc = {}
            for g in grades:
                gc[g] = gc.get(g, 0) + 1
            tcs_buckets.append({
                "bucket": bucket, "trades": len(grp),
                "ab_pct": ab_pct, "df_pct": df_pct, "grade_counts": gc,
            })
        neutral_quality["tcs_buckets"] = tcs_buckets

        if "bt_ib_high" in neutral_rows.columns and "bt_ib_low" in neutral_rows.columns:
            entry_price = pd.to_numeric(neutral_rows["price"], errors="coerce")
            ib_h = pd.to_numeric(neutral_rows["bt_ib_high"], errors="coerce")
            ib_l = pd.to_numeric(neutral_rows["bt_ib_low"],  errors="coerce")
            ib_range = (ib_h - ib_l).replace(0, pd.NA)

            def _ib_pos(row_tuple):
                ep, ih, il = row_tuple
                if pd.isna(ep) or pd.isna(ih) or pd.isna(il) or ih == il:
                    return "Unknown"
                margin = (ih - il) * 0.05
                if ep >= ih - margin and ep <= ih + margin:
                    return "At IB High"
                if ep >= il - margin and ep <= il + margin:
                    return "At IB Low"
                if il < ep < ih:
                    return "Inside IB"
                if ep > ih + margin:
                    return "Extended Above IB"
                return "Extended Below IB"

            neutral_rows["_ib_pos"] = list(map(
                _ib_pos,
                zip(entry_price, ib_h, ib_l),
            ))

            _ib_order = ["At IB High", "At IB Low", "Inside IB",
                         "Extended Above IB", "Extended Below IB", "Unknown"]
            ib_positions = []
            for pos in _ib_order:
                grp = neutral_rows[neutral_rows["_ib_pos"] == pos]
                if grp.empty:
                    continue
                grades = grp["grade"].fillna("?").tolist()
                ab_ct  = sum(1 for g in grades if g in {"A", "B"})
                df_ct  = sum(1 for g in grades if g in {"D", "F"})
                ab_pct = round(ab_ct / len(grades) * 100, 1)
                df_pct = round(df_ct / len(grades) * 100, 1)
                gc = {}
                for g in grades:
                    gc[g] = gc.get(g, 0) + 1
                ib_positions.append({
                    "position": pos, "trades": len(grp),
                    "ab_pct": ab_pct, "df_pct": df_pct, "grade_counts": gc,
                })
            neutral_quality["ib_position"] = ib_positions

        best_bucket = max(tcs_buckets, key=lambda x: x["ab_pct"] - x["df_pct"],
                          default=None) if tcs_buckets else None
        best_pos    = max(neutral_quality["ib_position"],
                          key=lambda x: x["ab_pct"] - x["df_pct"],
                          default=None) if neutral_quality["ib_position"] else None
        rec_parts = []
        if best_bucket:
            rec_parts.append(f"TCS in {best_bucket['bucket']} ({best_bucket['ab_pct']}% A/B rate)")
        if best_pos:
            rec_parts.append(f"entry at {best_pos['position']} ({best_pos['ab_pct']}% A/B rate)")
        if rec_parts:
            neutral_quality["recommendation"] = (
                "On Neutral days, best outcomes when: " + " AND ".join(rec_parts) + "."
            )

    return {
        "matched_df":      matched_df,
        "unmatched_n":     int(unmatched_n),
        "by_structure":    by_structure,
        "filter_sim":      filter_sim,
        "alignment":       alignment,
        "neutral_quality": neutral_quality,
    }


def fetch_live_quote(ticker: str) -> dict:
    """Fetch current price and today's volume via yfinance.
    Returns dict with keys: price, volume, error (None on success).
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.upper().strip())
        info = t.fast_info
        price  = float(info.last_price)  if info.last_price  else None
        volume = int(info.three_month_average_volume) if info.three_month_average_volume else None
        # prefer today's volume from 1d history
        hist = t.history(period="1d", interval="1m")
        if not hist.empty and "Volume" in hist.columns:
            volume = int(hist["Volume"].sum())
        if price is None:
            return {"price": None, "volume": None, "error": f"No data returned for '{ticker}'"}
        return {"price": round(price, 4), "volume": volume, "error": None}
    except Exception as e:
        return {"price": None, "volume": None, "error": str(e)}


def fetch_alpaca_fills(api_key: str, secret_key: str,
                       is_paper: bool = True,
                       trade_date: str = None) -> tuple:
    """Fetch filled orders from Alpaca Trading REST API for a given date.

    Returns (fills_list, error_string).  error_string is None on success.
    """
    base = ("https://paper-api.alpaca.markets"
            if is_paper else "https://api.alpaca.markets")
    headers = {
        "APCA-API-KEY-ID":     api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }
    if trade_date is None:
        trade_date = datetime.now(EASTERN).strftime("%Y-%m-%d")

    params = {
        "status":    "closed",
        "after":     f"{trade_date}T00:00:00Z",
        "until":     f"{trade_date}T23:59:59Z",
        "limit":     200,
        "direction": "desc",
    }
    try:
        resp = requests.get(f"{base}/v2/orders",
                            headers=headers, params=params, timeout=12)
        if resp.status_code == 401:
            return [], "Authentication failed — check your API Key and Secret Key."
        if resp.status_code == 403:
            return [], "Access forbidden — are you using a paper key on a live endpoint (or vice versa)?"
        resp.raise_for_status()
        orders = resp.json()
        if isinstance(orders, dict) and "message" in orders:
            return [], orders["message"]
        filled = [o for o in orders if o.get("status") == "filled"]
        return filled, None
    except requests.exceptions.Timeout:
        return [], "Request timed out — Alpaca API did not respond in time."
    except Exception as exc:
        return [], str(exc)


def match_fills_to_roundtrips(fills: list) -> list:
    """Match Alpaca buy+sell fills into round-trip trades.

    Groups fills by symbol, computes weighted-average entry/exit,
    and returns a list of trade summary dicts.
    """
    from collections import defaultdict
    by_sym = defaultdict(lambda: {"buys": [], "sells": []})

    for order in fills:
        sym        = (order.get("symbol") or "").upper()
        side       = order.get("side", "")
        fill_price = float(order.get("filled_avg_price") or 0)
        qty        = float(order.get("filled_qty") or 0)
        filled_at  = str(order.get("filled_at") or "")
        if fill_price <= 0 or qty <= 0:
            continue
        if side == "buy":
            by_sym[sym]["buys"].append({"price": fill_price, "qty": qty, "time": filled_at})
        elif side == "sell":
            by_sym[sym]["sells"].append({"price": fill_price, "qty": qty, "time": filled_at})

    results = []
    for sym, sides in by_sym.items():
        if not sides["buys"] or not sides["sells"]:
            continue
        total_buy_qty  = sum(b["qty"] for b in sides["buys"])
        total_sell_qty = sum(s["qty"] for s in sides["sells"])
        avg_entry = (sum(b["price"] * b["qty"] for b in sides["buys"])  / total_buy_qty)
        avg_exit  = (sum(s["price"] * s["qty"] for s in sides["sells"]) / total_sell_qty)
        matched_qty   = min(total_buy_qty, total_sell_qty)
        pnl_dollars   = (avg_exit - avg_entry) * matched_qty
        pnl_pct       = ((avg_exit - avg_entry) / avg_entry * 100) if avg_entry > 0 else 0.0
        win_loss      = "Win" if pnl_dollars > 0 else ("Loss" if pnl_dollars < 0 else "Breakeven")

        # Earliest fill time for display
        all_times = [b["time"] for b in sides["buys"]] + [s["time"] for s in sides["sells"]]
        earliest  = sorted(t for t in all_times if t)[:1]
        fill_time = earliest[0][:16].replace("T", " ") if earliest else ""

        results.append({
            "symbol":      sym,
            "avg_entry":   round(avg_entry, 4),
            "avg_exit":    round(avg_exit, 4),
            "qty":         matched_qty,
            "pnl_dollars": round(pnl_dollars, 4),
            "pnl_pct":     round(pnl_pct, 2),
            "win_loss":    win_loss,
            "fill_time":   fill_time,
        })

    results.sort(key=lambda r: r["fill_time"])
    return results


def save_trade_review(journal_row: dict, exit_price: float,
                      actual_structure: str, direction: str = "Long",
                      user_id: str = "") -> dict:
    """Calculate trade outcome and persist to accuracy_tracker.

    Parameters
    ----------
    journal_row      : row dict from trade_journal (must have 'ticker', 'price', 'structure')
    exit_price       : actual exit price entered by the user
    actual_structure : actual day structure the user observed
    direction        : "Long" or "Short"

    Returns
    -------
    dict with keys: win_loss, pnl_dollars, pnl_pct, correct_structure
    """
    entry_price = float(journal_row.get("price", 0.0))
    ticker      = str(journal_row.get("ticker", ""))
    predicted   = str(journal_row.get("structure", ""))

    if entry_price <= 0:
        return {"win_loss": "N/A", "pnl_dollars": 0.0, "pnl_pct": 0.0,
                "correct_structure": False, "error": "Invalid entry price"}

    pnl_dollars = (exit_price - entry_price) if direction == "Long" \
                  else (entry_price - exit_price)
    pnl_pct     = (pnl_dollars / entry_price) * 100
    win_loss    = "Win" if pnl_dollars > 0 else ("Loss" if pnl_dollars < 0 else "Breakeven")

    correct_structure = (
        _strip_emoji(predicted.lower()) in _strip_emoji(actual_structure.lower()) or
        _strip_emoji(actual_structure.lower()) in _strip_emoji(predicted.lower())
    )

    log_accuracy_entry(
        symbol      = ticker,
        predicted   = predicted,
        actual      = actual_structure,
        compare_key = "manual_review",
        entry_price = entry_price,
        exit_price  = exit_price,
        mfe         = round(pnl_dollars, 4),
        user_id     = user_id,
    )

    return {
        "win_loss":          win_loss,
        "pnl_dollars":       round(pnl_dollars, 4),
        "pnl_pct":           round(pnl_pct, 2),
        "correct_structure": correct_structure,
        "error":             None,
    }


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


def fetch_snapshots_bulk(api_key, secret_key, tickers, feed="iex"):
    """Batch-fetch latest price + previous day's close for a list of tickers.

    Works during market hours AND after hours / weekends by cascading through
    every available data field on the snapshot object.

    Returns {sym: {"price": float, "prev_close": float}} for qualifying tickers.
    Raises on authentication / network errors so the caller can show them.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockSnapshotRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(api_key, secret_key)

    # ── Step 1: try snapshot endpoint ─────────────────────────────────────────
    snap_result = {}
    try:
        req   = StockSnapshotRequest(symbol_or_symbols=list(tickers), feed=feed)
        snaps = client.get_stock_snapshot(req)

        for sym, snap in snaps.items():
            try:
                # Price: latest_trade → latest_quote mid → daily_bar close
                price = None
                if getattr(snap, "latest_trade", None) and snap.latest_trade.price:
                    price = float(snap.latest_trade.price)
                if price is None and getattr(snap, "latest_quote", None):
                    q = snap.latest_quote
                    ask = getattr(q, "ask_price", None)
                    bid = getattr(q, "bid_price", None)
                    if ask and bid and ask > 0 and bid > 0:
                        price = (float(ask) + float(bid)) / 2
                if price is None and getattr(snap, "daily_bar", None) and snap.daily_bar.close:
                    price = float(snap.daily_bar.close)

                # Prev close: prev_daily_bar → fall back to daily_bar open
                prev_close = None
                if getattr(snap, "prev_daily_bar", None) and snap.prev_daily_bar.close:
                    prev_close = float(snap.prev_daily_bar.close)
                if prev_close is None and getattr(snap, "daily_bar", None) and snap.daily_bar.open:
                    prev_close = float(snap.daily_bar.open)

                if price and price > 0:
                    snap_result[sym] = {
                        "price":      price,
                        "prev_close": prev_close if prev_close and prev_close > 0 else price,
                    }
            except Exception:
                pass
    except Exception as snap_err:
        # Snapshot endpoint failed entirely — fall through to daily bars
        snap_err_str = str(snap_err)
        if any(k in snap_err_str.lower() for k in ("forbidden", "unauthorized", "403", "401")):
            raise  # bad credentials — surface immediately

    # ── Step 1b: if SIP snapshot returned empty, retry with IEX ───────────────
    if not snap_result and feed != "iex":
        try:
            req   = StockSnapshotRequest(symbol_or_symbols=list(tickers), feed="iex")
            snaps = client.get_stock_snapshot(req)
            for sym, snap in snaps.items():
                if sym in snap_result:
                    continue  # already have it
                try:
                    price = None
                    if getattr(snap, "latest_trade", None) and snap.latest_trade.price:
                        price = float(snap.latest_trade.price)
                    if price is None and getattr(snap, "latest_quote", None):
                        q = snap.latest_quote
                        ask = getattr(q, "ask_price", None)
                        bid = getattr(q, "bid_price", None)
                        if ask and bid and ask > 0 and bid > 0:
                            price = (float(ask) + float(bid)) / 2
                    if price is None and getattr(snap, "daily_bar", None) and snap.daily_bar.close:
                        price = float(snap.daily_bar.close)
                    prev_close = None
                    if getattr(snap, "prev_daily_bar", None) and snap.prev_daily_bar.close:
                        prev_close = float(snap.prev_daily_bar.close)
                    if prev_close is None and getattr(snap, "daily_bar", None) and snap.daily_bar.open:
                        prev_close = float(snap.daily_bar.open)
                    if price and price > 0:
                        snap_result[sym] = {
                            "price":      price,
                            "prev_close": prev_close if prev_close and prev_close > 0 else price,
                        }
                except Exception:
                    pass
        except Exception:
            pass

    if snap_result:
        return snap_result

    # ── Step 2: fallback — fetch last 5 daily bars for each ticker ─────────────
    # This path is used when both snapshot endpoints returned empty (e.g. after hours)
    daily_result = {}
    end_dt   = datetime.now(pytz.UTC)
    start_dt = end_dt - timedelta(days=10)

    for sym in tickers:
        try:
            req = StockBarsRequest(
                symbol_or_symbols=sym,
                timeframe=TimeFrame.Day,
                start=start_dt,
                end=end_dt,
                feed="iex",  # always use IEX for daily bar fallback
            )
            bars = client.get_stock_bars(req)
            df   = bars.df
            if df.empty:
                continue
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(sym, level="symbol")
            df = df.sort_index()
            if len(df) < 1:
                continue
            price      = float(df["close"].iloc[-1])
            prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else price
            if price > 0:
                daily_result[sym] = {"price": price, "prev_close": prev_close}
        except Exception:
            pass

    return daily_result


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


def run_gap_scanner(api_key, secret_key, watchlist, trade_date, feed="iex",
                    min_price: float = 1.0, max_price: float = 50.0):
    """Run the full gap-scanner pipeline and return qualifying tickers by gap/RVOL.

    Pipeline:
      1. Batch-fetch snapshots (price + prev_close)
      2. Filter to configurable price range (default $1–$50)
      3. Fetch pre-market volumes + 10-day historical average per qualifying ticker
      4. Compute Gap % and Pre-Market RVOL
      5. Sort by absolute gap %, return all qualifying tickers (no hard cap)

    Returns list of dicts: [{ticker, price, gap_pct, pm_vol, avg_pm_vol, pm_rvol}]
    Raises exceptions so the caller can surface them to the UI.
    """
    # Step 1 — batch snapshots (let exception propagate so UI can show the message)
    snaps = fetch_snapshots_bulk(api_key, secret_key, watchlist, feed=feed)

    if not snaps:
        raise ValueError(
            "No snapshot data returned. Check your API credentials and that the "
            "tickers exist on Alpaca."
        )

    # Step 2 — filter by configurable price range
    qualifying = {
        sym: d for sym, d in snaps.items()
        if d.get("price") is not None and min_price <= d["price"] <= max_price
    }
    filtered_out = [
        f"{sym} (${d['price']:.2f})" for sym, d in snaps.items()
        if d.get("price") is not None and not (min_price <= d["price"] <= max_price)
    ]
    if not qualifying:
        out_of_range = [s for s, d in snaps.items() if d.get("price") is not None]
        raise ValueError(
            f"All {len(out_of_range)} tickers are outside the ${min_price:.0f}–${max_price:.0f} scan range "
            f"({', '.join(out_of_range[:5])}). "
            "Adjust the price range filter or add different tickers."
        )

    # Step 3 & 4 — pre-market volume + compute metrics
    # On IEX (free tier) pre-market bars are unavailable — we gracefully degrade
    # to gap-only mode after the first subscription error.
    pm_data_available = True
    rows = []
    for sym, snap_data in qualifying.items():
        pm_vol, avg_pm_vol = 0.0, None
        if pm_data_available:
            try:
                pm_vol, avg_pm_vol = fetch_premarket_vols(
                    api_key, secret_key, sym, trade_date,
                    lookback_days=10, feed=feed)
            except Exception as _pm_err:
                err_str = str(_pm_err).lower()
                if "subscription" in err_str or "permit" in err_str or "sip" in err_str:
                    # Free-tier account — skip PM vol for all remaining tickers
                    pm_data_available = False
                # Any other error: leave pm_vol/avg_pm_vol as 0/None and continue

        price      = snap_data["price"]
        prev_close = snap_data["prev_close"]
        gap_pct    = ((price - prev_close) / prev_close * 100.0
                      if prev_close and prev_close > 0 else 0.0)
        pm_rvol    = (round(pm_vol / avg_pm_vol, 2)
                      if avg_pm_vol and avg_pm_vol > 0 else None)

        rows.append({
            "ticker":          sym,
            "price":           round(price, 2),
            "gap_pct":         round(gap_pct, 2),
            "pm_vol":          int(pm_vol),
            "avg_pm_vol":      round(avg_pm_vol, 0) if avg_pm_vol else None,
            "pm_rvol":         pm_rvol,
            "pm_data_available": pm_data_available,
        })

    # Step 5 — sort by absolute gap %, then RVOL as tiebreaker
    rows.sort(key=lambda r: (
        abs(r["gap_pct"]),
        r["pm_rvol"] if r["pm_rvol"] is not None else -1,
    ), reverse=True)

    # Tag each row with whether PM data was available for this scan
    for r in rows:
        r["pm_data_available"] = pm_data_available

    return {"rows": rows, "filtered_out": filtered_out}


def compute_pretrade_quality(
    api_key: str, secret_key: str,
    sym: str,
    trade_date,
    feed: str = "sip",
) -> dict:
    """Compute real-time pre-trade quality metrics for a single ticker.

    Uses today's bars (up to now).  IB is locked at 9:30–10:30 AM per the
    standard Volume Profile protocol.

    Returns a dict with keys:
        tcs, tcs_bucket, ib_high, ib_low, current_price, ib_position,
        tcs_ok, ib_ok, go_signal, ib_formed
    or {"error": <str>} on failure.
    """
    try:
        df = fetch_bars(api_key, secret_key, sym, trade_date, feed=feed)
        if df.empty or len(df) < 5:
            return {"error": "No bar data available"}

        # IB window: 9:30–10:30 AM
        ib_cutoff = df.index[0].replace(hour=10, minute=30, second=0)
        ib_df = df[df.index <= ib_cutoff]
        ib_formed = len(ib_df) >= 5  # IB needs at least 5 bars

        if ib_formed:
            ib_high, ib_low = compute_initial_balance(ib_df)
        else:
            ib_high, ib_low = compute_initial_balance(df)

        if not ib_high or not ib_low or ib_high == ib_low:
            return {"error": "IB could not be computed (insufficient range)"}

        _, vap, poc_price = compute_volume_profile(
            ib_df if ib_formed else df, num_bins=30
        )
        tcs = float(compute_tcs(ib_df if ib_formed else df, ib_high, ib_low, poc_price))

        current_price = float(df["close"].iloc[-1])

        # IB position — 5% of IB range as "at boundary" tolerance
        margin = (ib_high - ib_low) * 0.05
        if current_price >= ib_high + margin:
            ib_pos = "Extended Above IB"
        elif current_price <= ib_low - margin:
            ib_pos = "Extended Below IB"
        elif current_price <= ib_low + margin:
            ib_pos = "At IB Low"
        elif current_price >= ib_high - margin:
            ib_pos = "At IB High"
        else:
            ib_pos = "Inside IB"

        # TCS bucket
        if tcs < 40:
            tcs_bucket = "Weak"
        elif tcs < 55:
            tcs_bucket = "Moderate"
        elif tcs < 70:
            tcs_bucket = "Strong"
        else:
            tcs_bucket = "Extreme"

        # Derived rule from calibration: TCS 55–70 AND At IB Low → best outcomes
        tcs_ok = 55 <= tcs < 70
        ib_ok  = ib_pos == "At IB Low"

        return {
            "tcs":           round(tcs, 1),
            "tcs_bucket":    tcs_bucket,
            "ib_high":       round(ib_high, 2),
            "ib_low":        round(ib_low, 2),
            "current_price": round(current_price, 2),
            "ib_position":   ib_pos,
            "ib_formed":     ib_formed,
            "tcs_ok":        tcs_ok,
            "ib_ok":         ib_ok,
            "go_signal":     tcs_ok and ib_ok,
        }
    except Exception as e:
        return {"error": str(e)}


def enrich_trade_context(api_key: str, secret_key: str, ticker: str,
                         trade_date, feed: str = "iex") -> dict:
    """Retroactively compute TCS, RVOL, IB levels, and structure for a historical trade.

    Called automatically during Webull CSV import so every journal entry has the
    same context fields as a live analysis (TCS, RVOL, IB high/low, structure).
    Feeds the Analytics calibration engine so win-rate slices by TCS bucket and
    structure are accurate across all historical trades.

    Returns a dict with keys: tcs, rvol, ib_high, ib_low, structure.
    Returns {} on any failure — safe; caller keeps whatever data it already has.
    """
    try:
        from datetime import date as _date, timedelta
        import requests as _req

        if hasattr(trade_date, "date"):
            trade_dt = trade_date.date()
        elif isinstance(trade_date, str):
            from dateutil.parser import parse as _dp
            trade_dt = _dp(trade_date).date()
        else:
            trade_dt = trade_date

        # ── TCS and IB levels via pretrade quality pipeline ───────────────────
        quality = compute_pretrade_quality(api_key, secret_key, ticker, trade_dt, feed=feed)
        if quality.get("error"):
            return {}

        tcs      = quality.get("tcs")
        ib_high  = quality.get("ib_high")
        ib_low   = quality.get("ib_low")
        ib_pos   = quality.get("ib_position", "")

        # ── RVOL — total day volume vs 10-day prior average ───────────────────
        rvol = None
        try:
            df_intra = fetch_bars(api_key, secret_key, ticker, trade_dt, feed=feed)
            today_vol = float(df_intra["volume"].sum()) if not df_intra.empty else None

            if today_vol:
                start_window = (trade_dt - timedelta(days=18)).isoformat()
                end_window   = trade_dt.isoformat()
                daily_url = (
                    f"https://data.alpaca.markets/v2/stocks/{ticker}/bars"
                    f"?timeframe=1Day&start={start_window}&end={end_window}"
                    f"&feed={feed}&limit=14"
                )
                headers = {
                    "APCA-API-KEY-ID":     api_key,
                    "APCA-API-SECRET-KEY": secret_key,
                }
                resp = _req.get(daily_url, headers=headers, timeout=8)
                daily_bars = resp.json().get("bars", [])

                prior_vols = [
                    b["v"] for b in daily_bars
                    if b.get("t", "")[:10] != trade_dt.isoformat() and "v" in b
                ]
                if prior_vols:
                    avg_vol = sum(prior_vols) / len(prior_vols)
                    rvol = round(today_vol / avg_vol, 2) if avg_vol > 0 else None
        except Exception:
            rvol = None

        # ── Structure — derived from IB position (simplified for historical) ──
        _pos_map = {
            "Extended Above IB": "Trending Up",
            "Extended Below IB": "Trending Down",
            "At IB High":        "At IB High",
            "At IB Low":         "At IB Low",
        }
        structure = _pos_map.get(ib_pos, "Inside IB")

        return {
            "tcs":      tcs,
            "rvol":     rvol,
            "ib_high":  ib_high,
            "ib_low":   ib_low,
            "structure": structure,
        }

    except Exception:
        return {}


def _prior_trading_day(d) -> "date":
    """Return the last NYSE trading day strictly before `d`."""
    from datetime import timedelta
    candidate = d - timedelta(days=1)
    for _ in range(10):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return candidate


def fetch_key_levels(api_key: str, secret_key: str, ticker: str,
                     trade_date, entry_low=None, entry_high=None,
                     current_price=None, feed: str = "iex") -> dict:
    """Fetch structural key levels for setup brief confluence detection.

    Gathers four classes of price levels and checks each against the
    entry zone ([entry_low, entry_high]) for confluence:

    1. PDH / PDL / PDC — Prior day session High / Low / Close
    2. ONH / ONL      — Overnight pre-market High / Low (4:00–9:30 AM)
    3. Round numbers  — Psychologically significant levels near current price
    4. Liquidity pools — Swing highs/lows from prior day (stop clusters)

    Returns a dict with all levels and confluence annotations.
    On any API failure, returns an empty dict (brief still works, just no levels).
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    import numpy as np

    result = {
        "pdh": None, "pdl": None, "pdc": None,
        "onh": None, "onl": None, "on_vol": 0.0,
        "round_numbers": [],
        "swing_highs": [],
        "swing_lows": [],
        "confluence_notes": [],
        "has_confluence": False,
    }

    if hasattr(trade_date, "date"):
        trade_date = trade_date.date()
    prior_day = _prior_trading_day(trade_date)
    client    = StockHistoricalDataClient(api_key, secret_key)

    # ── 1. Prior Day OHLC (regular session) ──────────────────────────────────
    try:
        pd_mo = EASTERN.localize(datetime(prior_day.year, prior_day.month, prior_day.day, 9, 30))
        pd_mc = EASTERN.localize(datetime(prior_day.year, prior_day.month, prior_day.day, 16, 0))
        req_pd = StockBarsRequest(symbol_or_symbols=ticker,
                                  timeframe=TimeFrame.Minute,
                                  start=pd_mo, end=pd_mc, feed=feed)
        bars_pd = client.get_stock_bars(req_pd)
        df_pd   = bars_pd.df
        if not df_pd.empty:
            if isinstance(df_pd.index, pd.MultiIndex):
                df_pd = df_pd.xs(ticker, level="symbol")
            df_pd.index = pd.to_datetime(df_pd.index)
            if df_pd.index.tz is None:
                df_pd.index = df_pd.index.tz_localize("UTC")
            df_pd.index = df_pd.index.tz_convert(EASTERN)
            df_pd = df_pd.sort_index()
            df_pd = df_pd[(df_pd.index.time >= dtime(9, 30)) &
                          (df_pd.index.time <= dtime(16, 0))]
            if not df_pd.empty:
                result["pdh"] = round(float(df_pd["high"].max()), 4)
                result["pdl"] = round(float(df_pd["low"].min()), 4)
                result["pdc"] = round(float(df_pd["close"].iloc[-1]), 4)

                # ── Swing highs / lows (15-min aggregation for stability) ────
                df_15 = df_pd.resample("15min").agg(
                    {"high": "max", "low": "min", "close": "last", "volume": "sum"}
                ).dropna()
                if len(df_15) >= 5:
                    highs = df_15["high"].values
                    lows  = df_15["low"].values
                    sh = [float(highs[i]) for i in range(1, len(highs) - 1)
                          if highs[i] >= highs[i-1] and highs[i] >= highs[i+1]]
                    sl = [float(lows[i]) for i in range(1, len(lows) - 1)
                          if lows[i] <= lows[i-1] and lows[i] <= lows[i+1]]
                    result["swing_highs"] = sorted(set(round(v, 4) for v in sh), reverse=True)[:3]
                    result["swing_lows"]  = sorted(set(round(v, 4) for v in sl))[:3]
    except Exception as _e:
        print(f"fetch_key_levels prior day error: {_e}")

    # ── 2. Overnight / Pre-market Bars (4:00 AM – 9:29 AM trade_date) ────────
    try:
        on_start = EASTERN.localize(
            datetime(trade_date.year, trade_date.month, trade_date.day, 4, 0))
        on_end   = EASTERN.localize(
            datetime(trade_date.year, trade_date.month, trade_date.day, 9, 30))
        now_et = datetime.now(EASTERN)
        if on_end > now_et:
            on_end = min(on_end, now_et)
        if on_start < on_end:
            req_on = StockBarsRequest(symbol_or_symbols=ticker,
                                      timeframe=TimeFrame.Minute,
                                      start=on_start, end=on_end, feed=feed)
            bars_on = client.get_stock_bars(req_on)
            df_on   = bars_on.df
            if not df_on.empty:
                if isinstance(df_on.index, pd.MultiIndex):
                    df_on = df_on.xs(ticker, level="symbol")
                df_on.index = pd.to_datetime(df_on.index)
                if df_on.index.tz is None:
                    df_on.index = df_on.index.tz_localize("UTC")
                df_on.index = df_on.index.tz_convert(EASTERN)
                df_on = df_on.sort_index()
                df_on = df_on[(df_on.index.time >= dtime(4, 0)) &
                               (df_on.index.time < dtime(9, 30))]
                if not df_on.empty:
                    result["onh"]    = round(float(df_on["high"].max()), 4)
                    result["onl"]    = round(float(df_on["low"].min()), 4)
                    result["on_vol"] = float(df_on["volume"].sum())
    except Exception as _e:
        print(f"fetch_key_levels overnight error: {_e}")

    # ── 3. Round Numbers near current price ───────────────────────────────────
    ref_price = current_price or result["pdc"] or 1.0
    if ref_price > 0:
        if ref_price < 1.0:
            step = 0.25
        elif ref_price < 5.0:
            step = 0.50
        elif ref_price < 20.0:
            step = 1.00
        elif ref_price < 50.0:
            step = 5.00
        else:
            step = 10.00
        import math
        lo_rn = math.floor(ref_price / step) * step
        rounds = [round(lo_rn + i * step, 4)
                  for i in range(-4, 6)
                  if abs(lo_rn + i * step - ref_price) / ref_price <= 0.20
                  and lo_rn + i * step > 0]
        result["round_numbers"] = rounds

    # ── 4. Confluence detection ───────────────────────────────────────────────
    if entry_low is not None and entry_high is not None:
        mid = (entry_low + entry_high) / 2.0
        tol = max((entry_high - entry_low) * 0.5, mid * 0.01)  # ±1% or half zone

        def _near(level) -> bool:
            return level is not None and abs(level - mid) <= tol

        def _zone_overlap(level) -> bool:
            """Level falls inside or very near the entry zone."""
            return level is not None and (entry_low - tol) <= level <= (entry_high + tol)

        notes = []
        if _zone_overlap(result["pdh"]):
            notes.append(f"Entry near Prior Day High ${result['pdh']:.4f} — resistance overhead")
        if _zone_overlap(result["pdl"]):
            notes.append(f"Entry at Prior Day Low ${result['pdl']:.4f} — strong support floor")
        if _zone_overlap(result["pdc"]):
            notes.append(f"Entry near Prior Day Close ${result['pdc']:.4f} — acceptance level")
        if _zone_overlap(result["onh"]):
            notes.append(f"Entry at Overnight High ${result['onh']:.4f} — pre-market resistance")
        if _zone_overlap(result["onl"]):
            notes.append(f"Entry at Overnight Low ${result['onl']:.4f} — pre-market support floor")
        for sh in result["swing_highs"]:
            if _zone_overlap(sh):
                notes.append(f"Entry at prior swing high ${sh:.4f} — liquidity pool above")
        for sl in result["swing_lows"]:
            if _zone_overlap(sl):
                notes.append(f"Entry at prior swing low ${sl:.4f} — stop cluster below")
        for rn in result["round_numbers"]:
            if _zone_overlap(rn):
                notes.append(f"Round number ${rn:.2f} inside entry zone — psychological magnet")

        result["confluence_notes"] = notes
        result["has_confluence"]   = len(notes) > 0

    return result


def compute_setup_brief(api_key: str, secret_key: str, ticker: str,
                        pred_date, user_id: str = "", feed: str = "iex") -> dict:
    """Generate a full pre-market trade plan for one ticker on pred_date.

    Synthesizes all available signals into an actionable setup brief:
      - Structure prediction + brain confidence
      - Entry zone (from IB levels and/or detected pattern neckline)
      - Entry trigger (human-readable condition: price level + RVOL + time gate)
      - Stop level (from pattern geometry or IB Low floor)
      - Price targets R1/R2/R3 (from volume profile extensions)
      - User's personal win rate for this exact condition cluster

    The win_rate_pct and win_rate_context fields update automatically every
    time the brief is regenerated — no rebuild needed as more trades are logged.

    Returns a dict on success, {"error": str} on failure.
    """
    try:
        from datetime import date as _date

        if hasattr(pred_date, "date"):
            _dt = pred_date.date()
        elif isinstance(pred_date, str):
            from dateutil.parser import parse as _dp
            _dt = _dp(pred_date).date()
        else:
            _dt = pred_date

        # ── 1. Fetch intraday bars ────────────────────────────────────────────
        df = fetch_bars(api_key, secret_key, ticker, _dt, feed=feed)
        if df is None or df.empty or len(df) < 5:
            return {"error": "Insufficient bar data"}

        # ── 2. Volume profile and IB ──────────────────────────────────────────
        bin_centers, vap, poc_price = compute_volume_profile(df, num_bins=100)
        ib_high, ib_low = compute_initial_balance(df)
        if ib_high is None or ib_low is None:
            return {"error": "IB not formed yet"}
        ib_range = ib_high - ib_low

        # ── 3. TCS and IB position ───────────────────────────────────────────
        tcs = float(compute_tcs(df, ib_high, ib_low, poc_price))
        final_price = float(df["close"].iloc[-1])
        margin      = ib_range * 0.05
        if final_price >= ib_high + margin:
            ib_pos = "Extended Above IB"
        elif final_price <= ib_low - margin:
            ib_pos = "Extended Below IB"
        elif final_price <= ib_low + margin:
            ib_pos = "At IB Low"
        elif final_price >= ib_high - margin:
            ib_pos = "At IB High"
        else:
            ib_pos = "Inside IB"

        # ── 4. Pattern detection ──────────────────────────────────────────────
        patterns    = detect_chart_patterns(df, poc_price=poc_price,
                                            ib_high=ib_high, ib_low=ib_low)
        top_pattern = patterns[0] if patterns else None
        pattern_name     = top_pattern.get("name", "")    if top_pattern else ""
        pattern_neckline = top_pattern.get("neckline")    if top_pattern else None
        pattern_conf     = top_pattern.get("score", 0)   if top_pattern else 0
        # Parse head price from description string (e.g. "Head $0.28")
        import re as _re_sb
        pattern_head = None
        if top_pattern:
            _hm = _re_sb.search(r"Head \$([\d\.]+)", top_pattern.get("description", ""))
            if _hm:
                try:
                    pattern_head = float(_hm.group(1))
                except ValueError:
                    pattern_head = None

        # ── 5. RVOL ──────────────────────────────────────────────────────────
        try:
            rvol_curve = build_rvol_intraday_curve(
                api_key, secret_key, ticker, _dt, lookback_days=10, feed=feed)
        except Exception:
            rvol_curve = None
        try:
            avg_vol = fetch_avg_daily_volume(api_key, secret_key, ticker, _dt)
        except Exception:
            avg_vol = None
        rvol = compute_rvol(df, intraday_curve=rvol_curve, avg_daily_vol=avg_vol)
        rvol_band_label = _rvol_band(float(rvol)) if rvol else "Normal"

        # ── 6. Brain model prediction ─────────────────────────────────────────
        try:
            brain_pred = compute_model_prediction(df, rvol, tcs, sector_bonus=0.0)
            predicted_structure = brain_pred.get("label", ib_pos)
            brain_confidence    = float(brain_pred.get("confidence", 0.5)) * 100
        except Exception:
            predicted_structure = ib_pos
            brain_confidence    = 50.0

        # ── 7. Entry zone ─────────────────────────────────────────────────────
        _is_pattern_entry = (
            pattern_neckline is not None and
            any(k in pattern_name.lower() for k in ("head", "h&s", "reverse", "double"))
        )
        if _is_pattern_entry:
            # Pattern-based: neckline is the trigger; enter within 1% of neckline
            entry_low  = round(pattern_neckline * 0.990, 4)
            entry_high = round(pattern_neckline * 1.010, 4)
            trigger    = (f"Neckline reclaim ${pattern_neckline:.4f} "
                          f"with RVOL > 2× after 10:30 ET")
        elif ib_pos == "At IB Low":
            entry_low  = round(ib_low - ib_range * 0.02, 4)
            entry_high = round(ib_low + ib_range * 0.08, 4)
            trigger    = (f"Hold above IB Low ${ib_low:.4f} with RVOL > 2× "
                          f"after 10:30 ET — look for reclaim candle")
        elif ib_pos == "At IB High":
            entry_low  = round(ib_high - ib_range * 0.02, 4)
            entry_high = round(ib_high + ib_range * 0.05, 4)
            trigger    = (f"IB High ${ib_high:.4f} breakout + hold "
                          f"with RVOL > 2× after 10:30 ET")
        elif ib_pos == "Extended Above IB":
            vwap_val   = float(df["vwap"].iloc[-1]) if "vwap" in df.columns else final_price
            entry_low  = round(vwap_val * 0.990, 4)
            entry_high = round(vwap_val * 1.010, 4)
            trigger    = (f"Pullback to VWAP ${vwap_val:.4f} and reclaim "
                          f"with RVOL > 1.5× — momentum continuation entry")
        else:  # Inside IB / generic
            entry_low  = round(poc_price * 0.985, 4)
            entry_high = round(poc_price * 1.015, 4)
            trigger    = (f"Wait for IB break with RVOL > 2.5× after 10:30 ET — "
                          f"no edge inside IB without volume confirmation")

        # ── 8. Stop level ─────────────────────────────────────────────────────
        if pattern_head is not None:
            stop_level = round(float(pattern_head) * 0.995, 4)  # 0.5% below head
        elif ib_pos in ("At IB Low", "Extended Below IB"):
            stop_level = round(ib_low - ib_range * 0.15, 4)
        elif ib_pos == "At IB High":
            stop_level = round(ib_high - ib_range * 0.20, 4)
        else:
            stop_level = round(entry_low - (entry_high - entry_low) * 1.5, 4)

        # ── 9. Price targets from volume profile ──────────────────────────────
        tz_list = compute_target_zones(df, ib_high, ib_low, bin_centers, vap, tcs)
        # Collect upside target prices (above entry) sorted ascending
        target_prices = sorted(
            set(round(z["price"], 4) for z in tz_list if z["price"] > entry_high),
        )[:3]
        # Fallback targets from IB extensions if volume profile gave nothing
        if not target_prices:
            target_prices = [
                round(ib_high + ib_range * 1.0, 4),
                round(ib_high + ib_range * 1.5, 4),
                round(ib_high + ib_range * 2.0, 4),
            ]

        # ── 10. Key Levels: PDH/PDL/PDC, Overnight, Round Numbers, Liq Pools ─
        key_levels = {}
        try:
            key_levels = fetch_key_levels(
                api_key, secret_key, ticker, _dt,
                entry_low=entry_low, entry_high=entry_high,
                current_price=final_price, feed=feed,
            )
            # Enhance trigger string with confluence notes (first 2 max)
            if key_levels.get("has_confluence"):
                conf_notes = key_levels.get("confluence_notes", [])[:2]
                trigger = trigger + " ⭐ Confluence: " + " | ".join(conf_notes)
        except Exception as _kle:
            print(f"fetch_key_levels skipped: {_kle}")

        # ── 11. User's personal win rate for this condition ───────────────────
        win_rate_pct     = None
        win_rate_context = "No data yet — keep trading to build calibration."
        confidence_label = "LOW"
        try:
            if user_id:
                wr_data = compute_win_rates(user_id, min_samples=1)
                tcs_bucket = (
                    "Weak" if tcs < 40 else
                    "Moderate" if tcs < 55 else
                    "Strong" if tcs < 70 else "Elite"
                )
                edge_band_label = _edge_band(tcs)
                cluster_key = (
                    f"edge:{edge_band_label} "
                    f"rvol:{rvol_band_label} "
                    f"struct:{ib_pos}"
                )
                cluster = wr_data.get(cluster_key)
                if cluster and cluster.get("n", 0) >= 1:
                    wr_pct = cluster["win_rate"] * 100
                    n      = cluster["n"]
                    win_rate_pct     = round(wr_pct, 1)
                    win_rate_context = (
                        f"{ib_pos} + TCS {tcs_bucket} + RVOL {rvol_band_label}: "
                        f"{wr_pct:.0f}% win rate ({n} trade{'s' if n!=1 else ''})"
                    )
                    if wr_pct >= 75 and n >= 5:
                        confidence_label = "HIGH"
                    elif wr_pct >= 55 and n >= 3:
                        confidence_label = "MODERATE"
                    else:
                        confidence_label = "LOW"
                else:
                    # Fall back to structure-only
                    struct_data = wr_data.get("_by_struct", {}).get(ib_pos)
                    if struct_data and struct_data.get("n", 0) >= 1:
                        wr_pct = struct_data["win_rate"] * 100
                        n      = struct_data["n"]
                        win_rate_pct     = round(wr_pct, 1)
                        win_rate_context = (
                            f"{ib_pos}: {wr_pct:.0f}% win rate ({n} trade{'s' if n!=1 else ''}) "
                            f"— building {ib_pos} + TCS history"
                        )
                        confidence_label = "MODERATE" if wr_pct >= 55 else "LOW"
        except Exception:
            pass

        return {
            "ticker":            ticker,
            "pred_date":         str(_dt),
            "predicted_structure": predicted_structure,
            "brain_confidence":  round(brain_confidence, 1),
            "ib_position":       ib_pos,
            "tcs":               round(tcs, 1),
            "rvol":              rvol,
            "rvol_band":         rvol_band_label,
            "pattern":           pattern_name,
            "pattern_neckline":  pattern_neckline,
            "pattern_confidence": pattern_conf,
            "entry_zone_low":    entry_low,
            "entry_zone_high":   entry_high,
            "entry_trigger":     trigger,
            "stop_level":        stop_level,
            "targets":           target_prices,
            "win_rate_pct":      win_rate_pct,
            "win_rate_context":  win_rate_context,
            "confidence_label":  confidence_label,
            # Key levels
            "pdh":               key_levels.get("pdh"),
            "pdl":               key_levels.get("pdl"),
            "pdc":               key_levels.get("pdc"),
            "onh":               key_levels.get("onh"),
            "onl":               key_levels.get("onl"),
            "on_vol":            key_levels.get("on_vol", 0.0),
            "round_numbers":     key_levels.get("round_numbers", []),
            "swing_highs":       key_levels.get("swing_highs", []),
            "swing_lows":        key_levels.get("swing_lows", []),
            "confluence_notes":  key_levels.get("confluence_notes", []),
            "has_confluence":    key_levels.get("has_confluence", False),
        }

    except Exception as e:
        return {"error": str(e)}


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


# ── Analytics & Edge ──────────────────────────────────────────────────────────

def compute_edge_analytics(journal_df: pd.DataFrame,
                           tracker_df: pd.DataFrame) -> dict:
    """Join trade_journal + accuracy_tracker and compute full edge stats.

    Returns
    -------
    dict with keys:
      summary            – high-level KPIs
      equity_curve       – DataFrame (timestamp, symbol, mfe, cumulative_pnl)
      daily_pnl          – DataFrame (date, pnl, cumulative_pnl)
      win_rate_by_struct – DataFrame (structure, trades, wins, win_rate, avg_pnl)
      grade_distribution – dict {grade: count}
      tcs_edge           – DataFrame (tcs_bucket, trades, win_rate)
    """
    empty = {
        "summary": {
            "win_rate": 0.0, "total_pnl": 0.0, "avg_win": 0.0,
            "avg_loss": 0.0, "profit_factor": 0.0,
            "total_trades": 0, "trade_days": 0,
        },
        "equity_curve":        pd.DataFrame(),
        "daily_pnl":           pd.DataFrame(),
        "win_rate_by_struct":  pd.DataFrame(),
        "grade_distribution":  {},
        "tcs_edge":            pd.DataFrame(),
    }

    # ── Clean tracker ──────────────────────────────────────────────────────
    tdf = tracker_df.copy() if not tracker_df.empty else pd.DataFrame()
    if tdf.empty:
        return empty

    for col in ("entry_price", "exit_price", "mfe"):
        tdf[col] = pd.to_numeric(tdf.get(col, 0), errors="coerce").fillna(0.0)
    tdf["timestamp"] = pd.to_datetime(tdf.get("timestamp", pd.NaT), errors="coerce")

    trades = tdf[(tdf["entry_price"] > 0) & (tdf["exit_price"] > 0)].copy()
    if trades.empty:
        return empty

    trades = trades.sort_values("timestamp").reset_index(drop=True)

    wins   = trades[trades["mfe"] > 0]
    losses = trades[trades["mfe"] < 0]

    total_trades  = len(trades)
    win_count     = len(wins)
    win_rate      = round(win_count / total_trades * 100, 1) if total_trades else 0.0
    total_pnl     = round(float(trades["mfe"].sum()), 2)
    avg_win       = round(float(wins["mfe"].mean()), 2)   if not wins.empty   else 0.0
    avg_loss      = round(float(losses["mfe"].mean()), 2) if not losses.empty else 0.0
    gross_win     = float(wins["mfe"].sum())              if not wins.empty   else 0.0
    gross_loss    = abs(float(losses["mfe"].sum()))       if not losses.empty else 0.0
    profit_factor = round(gross_win / gross_loss, 2)      if gross_loss > 0   else 999.0
    trade_days    = int(trades["timestamp"].dt.date.nunique())

    # ── Equity curve ────────────────────────────────────────────────────────
    trades["cumulative_pnl"] = trades["mfe"].cumsum()
    equity_curve = trades[["timestamp", "symbol", "mfe", "cumulative_pnl"]].copy()

    # ── Daily P&L ───────────────────────────────────────────────────────────
    trades["date"] = trades["timestamp"].dt.date
    daily = (trades.groupby("date")["mfe"].sum()
             .reset_index().rename(columns={"mfe": "pnl"}))
    daily["cumulative_pnl"] = daily["pnl"].cumsum()

    # ── Win rate by predicted structure ─────────────────────────────────────
    struct_rows = []
    if "predicted" in trades.columns:
        for struct, grp in trades.groupby("predicted"):
            s = str(struct).strip()
            if not s:
                continue
            tc = len(grp); wc = int((grp["mfe"] > 0).sum())
            struct_rows.append({
                "structure": s,
                "trades":    tc,
                "wins":      wc,
                "win_rate":  round(wc / tc * 100, 1) if tc else 0.0,
                "avg_pnl":   round(float(grp["mfe"].mean()), 2),
            })
    wr_struct = (pd.DataFrame(struct_rows).sort_values("win_rate", ascending=False)
                 if struct_rows else pd.DataFrame())

    # ── TCS edge ────────────────────────────────────────────────────────────
    tcs_edge = pd.DataFrame()
    if not journal_df.empty and "tcs" in journal_df.columns:
        jdf = journal_df.copy()
        jdf["tcs"] = pd.to_numeric(jdf.get("tcs", 0), errors="coerce").fillna(0)
        jdf["tcs_bucket"] = pd.cut(
            jdf["tcs"],
            bins=[0, 40, 55, 65, 75, 101],
            labels=["<40", "40–54", "55–64", "65–74", "75+"],
        )
        jdf["timestamp"] = pd.to_datetime(jdf.get("timestamp", ""), errors="coerce")
        merged = pd.merge(
            jdf[["timestamp", "ticker", "tcs", "tcs_bucket"]],
            trades[["timestamp", "symbol", "mfe"]],
            left_on="ticker", right_on="symbol", how="inner",
            suffixes=("_j", "_t"),
        )
        if not merged.empty:
            tcs_rows = []
            for bkt, grp in merged.groupby("tcs_bucket", observed=True):
                tc = len(grp); wc = int((grp["mfe"] > 0).sum())
                tcs_rows.append({
                    "tcs_bucket": str(bkt),
                    "trades":     tc,
                    "win_rate":   round(wc / tc * 100, 1) if tc else 0.0,
                })
            tcs_edge = pd.DataFrame(tcs_rows)

    # ── Grade distribution ──────────────────────────────────────────────────
    grade_dist = {}
    if not journal_df.empty and "grade" in journal_df.columns:
        grade_dist = {str(k): int(v)
                      for k, v in journal_df["grade"].value_counts().items()}

    return {
        "summary": {
            "win_rate": win_rate, "total_pnl": total_pnl,
            "avg_win": avg_win,   "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "total_trades": total_trades, "trade_days": trade_days,
        },
        "equity_curve":       equity_curve,
        "daily_pnl":          daily,
        "win_rate_by_struct": wr_struct,
        "grade_distribution": grade_dist,
        "tcs_edge":           tcs_edge,
    }


# ── Live Playbook Screener ──────────────────────────────────────────────────────
def scan_playbook(api_key: str, secret_key: str, top: int = 50) -> tuple:
    """Scan Alpaca for today's most-active and top-gaining small-cap stocks ($2–$20).

    Returns
    -------
    (rows: list[dict], error: str)
        rows — sorted by % change descending; each dict has:
            ticker, price, change_pct, volume, source
        error — non-empty string only if *both* endpoints fail
    """
    if not api_key or not secret_key:
        return [], "No API credentials provided."

    headers = {
        "APCA-API-KEY-ID":     api_key,
        "APCA-API-SECRET-KEY": secret_key,
        "accept":              "application/json",
    }
    base   = "https://data.alpaca.markets/v1beta1/screener/stocks"
    pool   = {}
    errors = []

    # ── Most Actives ─────────────────────────────────────────────────────────
    try:
        r = requests.get(
            f"{base}/most-actives",
            params={"by": "volume", "top": top},
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            for item in r.json().get("most_actives", []):
                sym        = str(item.get("symbol", "")).upper()
                price      = float(item.get("price", 0) or 0)
                change_pct = float(item.get("percent_change", 0) or 0)
                volume     = int(item.get("volume", 0) or 0)
                if sym and 2.0 <= price <= 20.0:
                    pool[sym] = {
                        "ticker":     sym,
                        "price":      price,
                        "change_pct": change_pct,
                        "volume":     volume,
                        "source":     "Active",
                    }
        else:
            errors.append(f"most-actives HTTP {r.status_code}")
    except Exception as exc:
        errors.append(f"most-actives: {exc}")

    # ── Top Gainers ───────────────────────────────────────────────────────────
    try:
        r = requests.get(
            f"{base}/movers",
            params={"market_type": "stocks", "top": top},
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            for item in r.json().get("gainers", []):
                sym        = str(item.get("symbol", "")).upper()
                price      = float(item.get("price", 0) or 0)
                change_pct = float(item.get("percent_change", 0) or 0)
                volume     = int(item.get("volume", 0) or 0)
                if sym and 2.0 <= price <= 20.0:
                    if sym in pool:
                        pool[sym]["source"] = "Active + Gainer"
                    else:
                        pool[sym] = {
                            "ticker":     sym,
                            "price":      price,
                            "change_pct": change_pct,
                            "volume":     volume,
                            "source":     "Gainer",
                        }
        elif r.status_code not in (400, 422) or not pool:
            # Only surface the error if most-actives also came up empty
            errors.append(f"movers HTTP {r.status_code}")
    except Exception as exc:
        errors.append(f"movers: {exc}")

    rows = sorted(pool.values(), key=lambda x: x["change_pct"], reverse=True)
    if errors and not rows:
        # If every failure was a 400/422, the market is simply closed/inactive
        non_auth = [e for e in errors if "400" in e or "422" in e]
        if len(non_auth) == len(errors):
            err = "market_closed"
        else:
            err = "; ".join(errors)
    else:
        err = ""
    return rows, err


# ── Historical Backtester ───────────────────────────────────────────────────────
_BACKTEST_DIRECTIONAL  = ("Trend", "Nrml Var", "Normal Var")
_BACKTEST_RANGE        = ("Non-Trend", "Non Trend")
_BACKTEST_NEUTRAL_EXT  = ("Ntrl Extreme", "Neutral Extreme")  # high-vol: any break wins
_BACKTEST_BALANCED     = ("Neutral",)                          # pure balanced: needs both sides
_BACKTEST_BIMODAL      = ("Dbl Dist", "Double")
_BACKTEST_NORMAL       = ("Normal",)   # Normal (not Var) — range-ish


def _backtest_single(api_key: str, secret_key: str, sym: str,
                     trade_date, feed: str, price_min: float, price_max: float,
                     cutoff_hour: int = 10, cutoff_minute: int = 30,
                     slippage_pct: float = 0.0):
    """Fetch one ticker's historical bars, score the morning, evaluate the afternoon.

    slippage_pct: one-way slippage as a percentage (e.g. 0.5 = 0.5%).
    Applied to both entry and exit, so total drag = slippage_pct × 2.
    Returns a result dict or None if data is insufficient / out of price range.
    """
    try:
        df = fetch_bars(api_key, secret_key, sym, trade_date, feed=feed)
        if df.empty or len(df) < 10:
            return None

        # Price range gate: use first bar open price
        open_px = float(df["open"].iloc[0])
        if not (price_min <= open_px <= price_max):
            return None

        # Split at prediction cutoff (IB always 9:30–10:30; engine sees up to cutoff)
        ib_cutoff = df.index[0].replace(hour=cutoff_hour, minute=cutoff_minute, second=0)
        pm_df  = df[df.index <= ib_cutoff]   # engine input (9:30 → cutoff)
        aft_df = df[df.index > ib_cutoff]    # actual outcome (cutoff → 4:00 PM)

        if len(pm_df) < 5:
            return None

        morning_only = len(aft_df) < 5  # live scan before afternoon data is available

        # Morning engine run
        ib_high, ib_low = compute_initial_balance(pm_df)
        if not ib_high or not ib_low:
            return None

        bin_centers, vap, poc_price = compute_volume_profile(pm_df, num_bins=30)
        tcs   = float(compute_tcs(pm_df, ib_high, ib_low, poc_price))
        probs = compute_structure_probabilities(
            pm_df, bin_centers, vap, ib_high, ib_low, poc_price
        )
        predicted = max(probs, key=probs.get) if probs else "—"
        confidence = round(probs.get(predicted, 0.0), 1)

        # Afternoon reality — placeholder when afternoon bars not yet available
        if morning_only:
            aft_high       = ib_high
            aft_low        = ib_low
            close_px       = float(pm_df["close"].iloc[-1])
            actual_outcome = "Pending"
            actual_icon    = "…"
            broke_up       = False
            broke_down     = False
        else:
            aft_high = float(aft_df["high"].max())
            aft_low  = float(aft_df["low"].min())
            close_px = float(aft_df["close"].iloc[-1])
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

        # Win/Loss: does predicted category match actual outcome?
        if morning_only:
            win      = None
            aft_move = 0.0
        else:
            is_dir      = any(k in predicted for k in _BACKTEST_DIRECTIONAL)
            is_range    = any(k in predicted for k in _BACKTEST_RANGE)
            is_neut_ext = any(k in predicted for k in _BACKTEST_NEUTRAL_EXT)
            is_balanced = (not is_neut_ext and
                           any(k in predicted for k in _BACKTEST_BALANCED))
            is_bimodal  = any(k in predicted for k in _BACKTEST_BIMODAL)
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

        # Slippage drag: entry + exit, each side costs slippage_pct
        # Applied to the magnitude (directional sign preserved)
        _slip_drag = slippage_pct * 2.0
        if aft_move > 0:
            aft_move = max(0.0, aft_move - _slip_drag)
        elif aft_move < 0:
            aft_move = min(0.0, aft_move + _slip_drag)

        # ── False break detection ────────────────────────────────────────────────
        # A false break = IB violated but price closed back inside within 30 min
        # (6 × 5-min bars). This is the classic "shake & bake" reversal trap.
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

        return {
            "ticker":           sym,
            "open_price":       round(open_px, 2),
            "ib_high":          round(ib_high, 2),
            "ib_low":           round(ib_low, 2),
            "tcs":              round(tcs, 1),
            "predicted":        predicted,
            "confidence":       confidence,
            "actual_outcome":   actual_outcome,
            "actual_icon":      actual_icon,
            "close_price":      round(close_px, 2),
            "aft_move_pct":     round(aft_move, 2),
            "win_loss":         "Pending" if win is None else ("Win" if win else "Loss"),
            "false_break_up":   false_break_up,
            "false_break_down": false_break_down,
        }
    except Exception:
        return None


def run_historical_backtest(
    api_key: str, secret_key: str,
    trade_date,
    tickers: list,
    feed: str = "sip",
    price_min: float = 2.0,
    price_max: float = 20.0,
    cutoff_hour: int = 10,
    cutoff_minute: int = 30,
    slippage_pct: float = 0.0,
) -> tuple:
    """Run the quant engine on morning-only historical data and score against afternoon.

    Returns (results: list[dict], summary: dict).
    Results are sorted by TCS descending.
    """
    if not tickers:
        return [], {"error": "No tickers provided."}
    if not api_key or not secret_key:
        return [], {"error": "Alpaca credentials missing."}

    results = []
    with ThreadPoolExecutor(max_workers=min(10, len(tickers))) as executor:
        futures = {
            executor.submit(
                _backtest_single, api_key, secret_key, sym,
                trade_date, feed, price_min, price_max,
                cutoff_hour, cutoff_minute, slippage_pct
            ): sym
            for sym in tickers
        }
        for future in as_completed(futures):
            r = future.result()
            if r is not None:
                results.append(r)

    if not results:
        return [], {"error": "No valid data returned. Check tickers and date (market must have been open)."}

    results.sort(key=lambda x: x["tcs"], reverse=True)

    wins     = sum(1 for r in results if r["win_loss"] == "Win")
    losses   = len(results) - wins
    win_rate = round(wins / len(results) * 100, 1) if results else 0.0

    # Directional breakdown — independent of structure prediction accuracy
    bull_rows  = [r for r in results if r["actual_outcome"] == "Bullish Break"]
    bear_rows  = [r for r in results if r["actual_outcome"] == "Bearish Break"]
    both_rows  = [r for r in results if r["actual_outcome"] == "Both Sides"]
    range_rows = [r for r in results if r["actual_outcome"] == "Range-Bound"]

    avg_bull_ft = (round(sum(r["aft_move_pct"] for r in bull_rows) / len(bull_rows), 1)
                   if bull_rows else 0.0)
    avg_bear_ft = (round(sum(abs(r["aft_move_pct"]) for r in bear_rows) / len(bear_rows), 1)
                   if bear_rows else 0.0)

    long_win_rate = round(len(bull_rows) / len(results) * 100, 1) if results else 0.0

    # False break stats
    fb_up   = [r for r in results if r.get("false_break_up")]
    fb_down = [r for r in results if r.get("false_break_down")]
    _breakable = len(bull_rows) + len(bear_rows) + len(both_rows)
    false_break_rate = (round((len(fb_up) + len(fb_down)) / _breakable * 100, 1)
                        if _breakable else 0.0)

    summary = {
        "win_rate":         win_rate,
        "total":            len(results),
        "wins":             wins,
        "losses":           losses,
        "highest_tcs":      round(max(r["tcs"] for r in results), 1),
        "avg_tcs":          round(sum(r["tcs"] for r in results) / len(results), 1),
        "bull_breaks":      len(bull_rows),
        "bear_breaks":      len(bear_rows),
        "both_breaks":      len(both_rows),
        "range_bound":      len(range_rows),
        "avg_bull_ft":      avg_bull_ft,
        "avg_bear_ft":      avg_bear_ft,
        "long_win_rate":    long_win_rate,
        "false_break_rate": false_break_rate,
        "fb_up_count":      len(fb_up),
        "fb_down_count":    len(fb_down),
    }
    return results, summary


def run_backtest_range(
    api_key: str, secret_key: str,
    start_date, end_date,
    tickers: list,
    feed: str = "sip",
    price_min: float = 2.0,
    price_max: float = 20.0,
    slippage_pct: float = 0.0,
) -> tuple:
    """Run the backtest across a date range (max 22 weekdays ≈ 1 month).

    Returns (all_results, agg_summary, daily_list) where:
    - all_results   : flat list of every row with 'sim_date' and 'split' ('train'/'test') added
    - agg_summary   : aggregate stats with walk-forward train/test breakdown
    - daily_list    : [(date, results, summary), ...] one entry per trading day

    Walk-forward split: first 70% of trading days = train, last 30% = test.
    This gives an honest out-of-sample win rate on dates the model never saw.
    """
    def _summarise(rows: list, label: str) -> dict:
        if not rows:
            return {"label": label, "total": 0, "win_rate": 0.0}
        total = len(rows)
        wins  = sum(1 for r in rows if r["win_loss"] == "Win")
        bull  = [r for r in rows if r["actual_outcome"] == "Bullish Break"]
        bear  = [r for r in rows if r["actual_outcome"] == "Bearish Break"]
        both  = [r for r in rows if r["actual_outcome"] == "Both Sides"]
        rng   = [r for r in rows if r["actual_outcome"] == "Range-Bound"]
        fb_u  = [r for r in rows if r.get("false_break_up")]
        fb_d  = [r for r in rows if r.get("false_break_down")]
        brk   = len(bull) + len(bear) + len(both)
        return {
            "label":            label,
            "total":            total,
            "wins":             wins,
            "losses":           total - wins,
            "win_rate":         round(wins / total * 100, 1) if total else 0.0,
            "highest_tcs":      round(max(r["tcs"] for r in rows), 1),
            "avg_tcs":          round(sum(r["tcs"] for r in rows) / total, 1),
            "bull_breaks":      len(bull),
            "bear_breaks":      len(bear),
            "both_breaks":      len(both),
            "range_bound":      len(rng),
            "avg_bull_ft":      (round(sum(r["aft_move_pct"] for r in bull) / len(bull), 1)
                                 if bull else 0.0),
            "avg_bear_ft":      (round(sum(abs(r["aft_move_pct"]) for r in bear) / len(bear), 1)
                                 if bear else 0.0),
            "long_win_rate":    round(len(bull) / total * 100, 1) if total else 0.0,
            "false_break_rate": (round((len(fb_u) + len(fb_d)) / brk * 100, 1)
                                 if brk else 0.0),
            "fb_up_count":      len(fb_u),
            "fb_down_count":    len(fb_d),
        }

    # Collect weekdays in range, cap at 22 (~1 calendar month)
    trading_days = []
    cur = start_date
    while cur <= end_date and len(trading_days) < 22:
        if cur.weekday() < 5:
            trading_days.append(cur)
        cur += timedelta(days=1)

    if not trading_days:
        return [], {"error": "No trading days in selected range."}, []

    # Walk-forward split: first 70% = train, last 30% = test
    split_idx   = max(1, int(len(trading_days) * 0.70))
    train_days  = set(str(d) for d in trading_days[:split_idx])

    daily_list = []
    for d in trading_days:
        r, s = run_historical_backtest(
            api_key, secret_key, d, tickers, feed, price_min, price_max,
            slippage_pct=slippage_pct
        )
        if not s.get("error") and r:
            split_label = "train" if str(d) in train_days else "test"
            for row in r:
                row["sim_date"] = str(d)
                row["split"]    = split_label
            daily_list.append((d, r, s))

    if not daily_list:
        return [], {"error": "No valid data for any date in range."}, []

    all_results  = []
    for _, r, _ in daily_list:
        all_results.extend(r)

    train_rows  = [r for r in all_results if r.get("split") == "train"]
    test_rows   = [r for r in all_results if r.get("split") == "test"]

    agg_summary = _summarise(all_results, "All")
    agg_summary["days_run"]    = len(daily_list)
    agg_summary["slippage_pct"] = slippage_pct
    agg_summary["train"]       = _summarise(train_rows, "Train (in-sample)")
    agg_summary["test"]        = _summarise(test_rows,  "Test  (out-of-sample)")

    return all_results, agg_summary, daily_list


# ── Backtest Supabase persistence ────────────────────────────────────────────
def save_backtest_sim_runs(rows: list, user_id: str = ""):
    """Batch-insert backtest simulation rows to Supabase."""
    if not supabase or not rows:
        return
    try:
        records = [
            {
                "user_id":        user_id or "",
                "sim_date":       str(r.get("sim_date", "")),
                "ticker":         r.get("ticker", ""),
                "open_price":     r.get("open_price"),
                "ib_low":         r.get("ib_low"),
                "ib_high":        r.get("ib_high"),
                "tcs":            r.get("tcs"),
                "predicted":      r.get("predicted", ""),
                "actual_outcome": r.get("actual_outcome", ""),
                "win_loss":       r.get("win_loss", ""),
                "follow_thru_pct": r.get("aft_move_pct"),
                "false_break_up":   bool(r.get("false_break_up", False)),
                "false_break_down": bool(r.get("false_break_down", False)),
            }
            for r in rows
        ]
        supabase.table("backtest_sim_runs").insert(records).execute()
    except Exception as e:
        print(f"Backtest save error: {e}")


def load_backtest_sim_history(user_id: str = "") -> "pd.DataFrame":
    """Load saved backtest runs from Supabase (most recent first, up to 1000 rows)."""
    if not supabase:
        return pd.DataFrame()
    try:
        q = supabase.table("backtest_sim_runs").select("*")
        if user_id:
            q = q.eq("user_id", user_id)
        data = q.order("sim_date", desc=True).limit(5000).execute().data
        return pd.DataFrame(data) if data else pd.DataFrame()
    except Exception as e:
        print(f"Backtest load error: {e}")
        return pd.DataFrame()


# ── Paper Trading ─────────────────────────────────────────────────────────────

_PAPER_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
  id             SERIAL PRIMARY KEY,
  user_id        TEXT,
  trade_date     DATE,
  ticker         TEXT,
  tcs            FLOAT,
  predicted      TEXT,
  ib_low         FLOAT,
  ib_high        FLOAT,
  open_price     FLOAT,
  actual_outcome TEXT,
  follow_thru_pct FLOAT,
  win_loss       TEXT,
  false_break_up  BOOLEAN DEFAULT FALSE,
  false_break_down BOOLEAN DEFAULT FALSE,
  min_tcs_filter  INT DEFAULT 50,
  regime_tag      TEXT,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);
"""

_PAPER_TRADES_REGIME_MIGRATION = (
    "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS regime_tag TEXT;"
)


def ensure_paper_trades_regime_column() -> bool:
    """Check if regime_tag column exists in paper_trades. Returns True if present.

    If missing, prints the migration SQL to run in Supabase SQL Editor.
    """
    if not supabase:
        return False
    try:
        supabase.table("paper_trades").select("regime_tag").limit(1).execute()
        return True
    except Exception as e:
        err = str(e).lower()
        if any(k in err for k in ("column", "not exist", "not found", "pgrst")):
            print(
                "regime_tag column missing from paper_trades.\n"
                "Run in Supabase SQL Editor:\n\n"
                + _PAPER_TRADES_REGIME_MIGRATION
            )
            return False
        print(f"ensure_paper_trades_regime_column error: {e}")
        return False


def ensure_paper_trades_table() -> bool:
    """Check if paper_trades table exists. Returns True if ready, False if missing."""
    if not supabase:
        return False
    try:
        supabase.table("paper_trades").select("id").limit(1).execute()
        return True
    except Exception as e:
        err_str = str(e).lower()
        # Supabase returns 404/relation-not-found when the table is missing
        if "404" in err_str or "relation" in err_str or "does not exist" in err_str or "not found" in err_str:
            print("paper_trades table not found. Create it in Supabase SQL editor — see Paper Trade tab for the SQL.")
            return False
        # Any other error (auth, network) — log and treat as unavailable
        print(f"paper_trades table check error: {e}")
        return False


def log_paper_trades(rows: list, user_id: str = "", min_tcs: int = 50) -> dict:
    """Save paper trade scan results to paper_trades table.
    Deduplicates by (user_id, trade_date, ticker) — won't double-log same day.
    Returns dict with saved count and skipped count."""
    if not supabase or not rows:
        return {"saved": 0, "skipped": 0, "error": "No data"}
    try:
        existing = (
            supabase.table("paper_trades")
            .select("ticker, trade_date")
            .eq("user_id", user_id)
            .execute()
            .data or []
        )
        existing_keys = {(r["ticker"], str(r["trade_date"])) for r in existing}
        records, skipped = [], 0
        for r in rows:
            key = (r.get("ticker", ""), str(r.get("sim_date", r.get("trade_date", ""))))
            if key in existing_keys:
                skipped += 1
                continue
            row_record = {
                "user_id":        user_id or "",
                "trade_date":     str(r.get("sim_date", r.get("trade_date", ""))),
                "ticker":         r.get("ticker", ""),
                "tcs":            r.get("tcs"),
                "predicted":      r.get("predicted", ""),
                "ib_low":         r.get("ib_low"),
                "ib_high":        r.get("ib_high"),
                "open_price":     r.get("open_price"),
                "alert_price":    r.get("close_price"),      # price at IB close = price when alert fires
                "alert_time":     datetime.utcnow().isoformat(),  # UTC timestamp when alert was logged
                "structure_conf": r.get("confidence"),            # brain confidence % in its own prediction
                "actual_outcome": r.get("actual_outcome", ""),
                "follow_thru_pct": r.get("aft_move_pct"),
                "win_loss":       r.get("win_loss", ""),
                "false_break_up":  bool(r.get("false_break_up", False)),
                "false_break_down": bool(r.get("false_break_down", False)),
                "min_tcs_filter": min_tcs,
            }
            # Include regime_tag if present and column exists; safe to omit if column missing
            if r.get("regime_tag"):
                row_record["regime_tag"] = r["regime_tag"]
            records.append(row_record)
        if records:
            supabase.table("paper_trades").insert(records).execute()
        return {"saved": len(records), "skipped": skipped}
    except Exception as e:
        return {"saved": 0, "skipped": 0, "error": str(e)}


def load_paper_trades(user_id: str = "", days: int = 21) -> "pd.DataFrame":
    """Load paper trades from the last N days (default 21 = 3 weeks)."""
    if not supabase:
        return pd.DataFrame()
    try:
        from datetime import date, timedelta
        cutoff = str(date.today() - timedelta(days=days + 7))
        q = (
            supabase.table("paper_trades")
            .select("*")
            .eq("user_id", user_id)
            .gte("trade_date", cutoff)
            .order("trade_date", desc=True)
        )
        data = q.execute().data
        return pd.DataFrame(data) if data else pd.DataFrame()
    except Exception as e:
        print(f"Paper trades load error: {e}")
        return pd.DataFrame()


def update_paper_trade_outcomes(trade_date: str, results: list, user_id: str = "") -> dict:
    """Update paper trades for a given date with final EOD outcomes.

    Matches on (user_id, trade_date, ticker) and patches
    actual_outcome, follow_thru_pct, win_loss, false_break_up/down,
    and post_alert_move_pct (EOD close vs alert_price at IB close).
    Returns dict with updated count.
    """
    if not supabase or not results:
        return {"updated": 0}

    # Batch-fetch stored alert_price values for this date so we can compute
    # post_alert_move_pct = (eod_close − alert_price) / alert_price × 100
    try:
        existing = (
            supabase.table("paper_trades")
            .select("ticker, alert_price")
            .eq("user_id", user_id)
            .eq("trade_date", str(trade_date))
            .execute()
            .data or []
        )
        alert_prices = {row["ticker"]: row.get("alert_price") for row in existing}
    except Exception:
        alert_prices = {}

    updated = 0
    for r in results:
        try:
            ticker    = r.get("ticker", "")
            eod_close = r.get("close_price")
            ap        = alert_prices.get(ticker)
            if ap and eod_close and float(ap) > 0:
                post_alert = round((float(eod_close) - float(ap)) / float(ap) * 100, 2)
            else:
                post_alert = None

            patch = {
                "actual_outcome":      r.get("actual_outcome", ""),
                "follow_thru_pct":     r.get("aft_move_pct"),
                "win_loss":            r.get("win_loss", ""),
                "false_break_up":      bool(r.get("false_break_up", False)),
                "false_break_down":    bool(r.get("false_break_down", False)),
                "post_alert_move_pct": post_alert,
            }
            (
                supabase.table("paper_trades")
                .update(patch)
                .eq("user_id", user_id)
                .eq("trade_date", str(trade_date))
                .eq("ticker", ticker)
                .execute()
            )
            updated += 1
        except Exception as e:
            print(f"Paper trade update error ({r.get('ticker')}): {e}")
    return {"updated": updated}


# ── Playbook Quant Scoring ──────────────────────────────────────────────────────
def _score_single_ticker(api_key: str, secret_key: str, sym: str,
                         trade_date, feed: str = "iex"):
    """Fetch intraday bars for one ticker and return (sym, tcs, top_structure, struct_conf).

    Returns (sym, None, None, 0.0) on any data or calculation failure.
    struct_conf = probability (0–100) of the top structure prediction.
    """
    try:
        df = fetch_bars(api_key, secret_key, sym, trade_date, feed=feed)
        if df.empty or len(df) < 5:
            return sym, None, None, 0.0

        ib_high, ib_low = compute_initial_balance(df)
        if not ib_high or not ib_low:
            ib_high = float(df["high"].max())
            ib_low  = float(df["low"].min())

        bin_centers, vap, poc_price = compute_volume_profile(df, num_bins=50)
        tcs   = compute_tcs(df, ib_high, ib_low, poc_price)
        probs = compute_structure_probabilities(
            df, bin_centers, vap, ib_high, ib_low, poc_price
        )
        top_struct  = max(probs, key=probs.get) if probs else "—"
        struct_conf = round(float(probs.get(top_struct, 0.0)), 1) if probs else 0.0
        return sym, round(float(tcs), 1), top_struct, struct_conf
    except Exception:
        return sym, None, None, 0.0


# ── Discord Alert Engine ─────────────────────────────────────────────────────
_discord_alert_cache: dict = {}   # {ticker_YYYY-MM-DD: timestamp_float}


def send_discord_alert(
    webhook_url: str,
    ticker: str,
    price: float,
    rvol: float,
    tcs: float,
    structure: str,
    edge_score: float = 0.0,
) -> bool:
    """Send a high-conviction signal embed to a Discord webhook.

    Returns True on success, False on failure or if the webhook URL is blank.
    Callers should check the per-day de-dup cache before calling this.
    """
    if not webhook_url or not webhook_url.startswith("http"):
        return False

    rvol_str   = f"{rvol:.1f}x" if rvol else "—"
    price_str  = f"${price:.2f}" if price else "—"
    tcs_bar    = "🟩" * int(tcs // 20) + "⬜" * (5 - int(tcs // 20))
    edge_color = 0x4CAF50 if edge_score >= 85 else (0xFFA726 if edge_score >= 75 else 0x90CAF9)

    payload = {
        "username": "VolumeProfile Bot",
        "avatar_url": "https://cdn-icons-png.flaticon.com/512/2172/2172832.png",
        "embeds": [
            {
                "title": f"🚀 HIGH CONVICTION SIGNAL — ${ticker}",
                "color": edge_color,
                "fields": [
                    {"name": "💰 Price",       "value": price_str,           "inline": True},
                    {"name": "📊 TCS",         "value": f"{tcs:.0f}/100 {tcs_bar}", "inline": True},
                    {"name": "⚡ Edge Score",  "value": f"{edge_score:.0f}/100",    "inline": True},
                    {"name": "🔥 RVOL",        "value": rvol_str,            "inline": True},
                    {"name": "🏗️ Structure",   "value": structure or "—",    "inline": True},
                    {"name": "📅 Date",        "value": date.today().strftime("%b %d, %Y"), "inline": True},
                ],
                "footer": {"text": "Volume Profile Terminal · Auto-Alert"},
            }
        ],
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=5)
        return resp.status_code in (200, 204)
    except Exception:
        return False


def _maybe_discord_alert(
    webhook_url: str,
    ticker: str,
    price: float,
    rvol: float,
    tcs: float,
    structure: str,
    edge_score: float,
) -> None:
    """Fire a Discord alert for this ticker if it hasn't been alerted today."""
    if not webhook_url:
        return
    cache_key = f"{ticker}_{date.today().isoformat()}"
    if cache_key in _discord_alert_cache:
        return
    success = send_discord_alert(
        webhook_url=webhook_url,
        ticker=ticker,
        price=price,
        rvol=rvol,
        tcs=tcs,
        structure=structure,
        edge_score=edge_score,
    )
    if success:
        _discord_alert_cache[cache_key] = True
        # Prune old keys (keep only today's entries)
        today = date.today().isoformat()
        stale = [k for k in list(_discord_alert_cache) if not k.endswith(today)]
        for k in stale:
            _discord_alert_cache.pop(k, None)


_tg_playbook_cache: dict = {}   # {ticker_YYYY-MM-DD: True}


def _maybe_telegram_playbook_alert(
    ticker: str,
    price: float,
    rvol: float,
    tcs: float,
    structure: str,
    edge_score: float,
) -> None:
    """Fire a Telegram alert for a high-conviction Playbook signal (TCS≥80, Edge≥75).
    De-duped per ticker per day. Uses TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars.
    """
    import os as _os, requests as _req
    _token   = _os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    _chat_id = _os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not _token or not _chat_id:
        return

    _cache_key = f"{ticker}_{date.today().isoformat()}"
    if _cache_key in _tg_playbook_cache:
        return

    _price_str = f"${price:.2f}" if price else "—"
    _rvol_str  = f"{rvol:.1f}×" if rvol else "—"
    _tcs_bar   = "🟩" * int(tcs // 20) + "⬜" * (5 - int(tcs // 20))
    _edge_lbl  = "🔥 ELITE" if edge_score >= 85 else "⚡ HIGH"

    _msg = (
        f"🚀 <b>HIGH CONVICTION — {ticker}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price:      <b>{_price_str}</b>\n"
        f"📊 TCS:        <b>{tcs:.0f}/100</b>  {_tcs_bar}\n"
        f"{_edge_lbl} Edge Score: <b>{edge_score:.0f}/100</b>\n"
        f"🔥 RVOL:       <b>{_rvol_str}</b>\n"
        f"🏗️ Structure:  <b>{structure or '—'}</b>\n"
        f"📅 {date.today().strftime('%b %d, %Y')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Playbook signal — review before entry."
    )
    try:
        _resp = _req.post(
            f"https://api.telegram.org/bot{_token}/sendMessage",
            json={"chat_id": _chat_id, "text": _msg, "parse_mode": "HTML"},
            timeout=8,
        )
        if _resp.status_code == 200:
            _tg_playbook_cache[_cache_key] = True
            _today = date.today().isoformat()
            for _k in [k for k in list(_tg_playbook_cache) if not k.endswith(_today)]:
                _tg_playbook_cache.pop(_k, None)
    except Exception:
        pass


def score_playbook_tickers(rows: list, api_key: str, secret_key: str,
                           feed: str = "iex", max_tickers: int = 20,
                           user_id: str = "",
                           discord_webhook_url: str = "") -> list:
    """Enrich Playbook rows with TCS, structure, and self-calibrating Edge Score.

    Edge Score (0–100) combines TCS, structure confidence, recent market
    environment, and false break rate — weights auto-calibrate from saved
    backtest history.
    """
    if not rows or not api_key or not secret_key:
        for row in rows:
            row.setdefault("tcs", None)
            row.setdefault("structure", "—")
            row.setdefault("edge_score", None)
        return rows

    # Pre-load adaptive weights + environment stats once for the whole batch
    weights  = compute_adaptive_weights(user_id)
    env_stat = get_recent_env_stats(user_id, days=5)

    # Roll back to most recent actual trading day (holiday-aware)
    trade_date = get_last_trading_day(api_key=api_key, secret_key=secret_key)

    subset = rows[:max_tickers]
    scored: dict = {}

    with ThreadPoolExecutor(max_workers=min(8, len(subset))) as executor:
        future_map = {
            executor.submit(
                _score_single_ticker, api_key, secret_key,
                r["ticker"], trade_date, feed
            ): r["ticker"]
            for r in subset
        }
        for future in as_completed(future_map):
            sym, tcs, structure, struct_conf = future.result()
            scored[sym] = (tcs, structure if structure else "—", struct_conf)

    for row in rows:
        sym = row["ticker"]
        if sym in scored:
            tcs, structure, struct_conf = scored[sym]
            row["tcs"]         = tcs
            row["structure"]   = structure
            row["struct_conf"] = struct_conf
            if tcs is not None:
                edge, breakdown = compute_edge_score(
                    tcs=tcs,
                    structure_conf=struct_conf,
                    env_long_rate=env_stat["long_rate"],
                    recent_false_brk_rate=env_stat["false_brk_rate"],
                    weights=weights,
                )
                row["edge_score"]     = edge
                row["edge_breakdown"] = breakdown
                # ── Telegram alert: TCS ≥ 80 and Edge Score ≥ 75 ────────────
                if tcs >= 80 and edge >= 75:
                    _maybe_telegram_playbook_alert(
                        ticker=sym,
                        price=float(row.get("price") or 0),
                        rvol=float(row.get("rvol") or 0),
                        tcs=tcs,
                        structure=structure,
                        edge_score=edge,
                    )
            else:
                row["edge_score"]     = None
                row["edge_breakdown"] = {}
        else:
            row["tcs"]         = None
            row["structure"]   = "—"
            row["struct_conf"] = 0.0
            row["edge_score"]  = None
            row["edge_breakdown"] = {}

    # Sort by edge score descending (None last)
    rows.sort(key=lambda r: r.get("edge_score") or -1, reverse=True)
    return rows


# ── Self-Calibrating Edge Score Engine ──────────────────────────────────────
_DEFAULT_EDGE_WEIGHTS = {
    "tcs":         0.35,
    "structure":   0.25,
    "environment": 0.25,
    "false_break": 0.15,
}


def compute_adaptive_weights(user_id: str = "") -> dict:
    """Load backtest history and compute data-calibrated signal weights.

    Requires at least 15 saved rows to calibrate. Falls back to defaults
    if there is insufficient data or Supabase is unavailable.

    Returns a dict with keys: tcs, structure, environment, false_break,
    rows_used (int), calibrated (bool).
    """
    df = load_backtest_sim_history(user_id)
    if df.empty:
        return {**_DEFAULT_EDGE_WEIGHTS, "rows_used": 0, "calibrated": False}

    try:
        # Deduplicate: keep only the most recent run for each (ticker, sim_date) pair
        # so replaying the same backtest day doesn't skew the weights
        df["sim_date"] = pd.to_datetime(df.get("sim_date", pd.NaT), errors="coerce")
        if "ticker" in df.columns and "sim_date" in df.columns:
            df = (df.sort_values("created_at", errors="ignore")
                    .drop_duplicates(subset=["ticker", "sim_date"], keep="last")
                    .reset_index(drop=True))

        if len(df) < 15:
            return {**_DEFAULT_EDGE_WEIGHTS, "rows_used": len(df), "calibrated": False}

        df["win_bin"] = (df["win_loss"] == "Win").astype(float)
        df["tcs_num"] = pd.to_numeric(df["tcs"], errors="coerce").fillna(0)

        # TCS correlation with wins (Pearson)
        tcs_corr = float(df["tcs_num"].corr(df["win_bin"]))
        if pd.isna(tcs_corr):
            tcs_corr = 0.0
        # Shift base weight by correlation signal, clamp to [0.15, 0.55]
        tcs_w = max(0.15, min(0.55, 0.35 + tcs_corr * 0.25))

        # Structure reliability: how well has the model been winning overall?
        overall_wr = float(df["win_bin"].mean())
        # Higher overall win rate → structure predictions are reliable → weight more
        struct_w = max(0.10, min(0.40, 0.25 + (overall_wr - 0.50) * 0.30))

        # Remaining weight split 60/40 between environment and false break
        remaining = max(0.10, 1.0 - tcs_w - struct_w)
        env_w = round(remaining * 0.60, 3)
        fb_w  = round(remaining * 0.40, 3)

        return {
            "tcs":         round(tcs_w, 3),
            "structure":   round(struct_w, 3),
            "environment": env_w,
            "false_break": fb_w,
            "rows_used":   len(df),
            "calibrated":  True,
        }
    except Exception:
        return {**_DEFAULT_EDGE_WEIGHTS, "rows_used": len(df), "calibrated": False}


def get_recent_env_stats(user_id: str = "", days: int = 5) -> dict:
    """Get recent market environment stats from saved backtest history.

    Returns dict with:
    - long_rate (float 0–100): % of recent setups that went bullish
    - false_brk_rate (float 0–100): % of IB breaks that reversed within 30 min
    - rows_used (int): how many rows were used
    """
    df = load_backtest_sim_history(user_id)
    if df.empty:
        return {"long_rate": 50.0, "false_brk_rate": 0.0, "rows_used": 0}

    try:
        df["sim_date"] = pd.to_datetime(df["sim_date"], errors="coerce")
        # Deduplicate replays: one row per (ticker, sim_date), most recent run
        if "ticker" in df.columns and "sim_date" in df.columns:
            df = (df.sort_values("created_at", errors="ignore")
                    .drop_duplicates(subset=["ticker", "sim_date"], keep="last")
                    .reset_index(drop=True))
        cutoff = pd.Timestamp.now(tz="UTC").tz_localize(None) - pd.Timedelta(days=days)
        recent = df[df["sim_date"] >= cutoff]
        if len(recent) < 10:
            recent = df.tail(50)   # fallback: last 50 rows regardless of date

        bull  = (recent["actual_outcome"] == "Bullish Break").sum()
        total = len(recent)
        long_rate = round(float(bull) / total * 100, 1) if total else 50.0

        fb_up   = recent["false_break_up"].fillna(False).astype(bool).sum()
        fb_down = recent["false_break_down"].fillna(False).astype(bool).sum()
        breakable = int((recent["actual_outcome"] != "Range-Bound").sum())
        false_brk_rate = (round((int(fb_up) + int(fb_down)) / breakable * 100, 1)
                          if breakable else 0.0)

        return {
            "long_rate":      long_rate,
            "false_brk_rate": false_brk_rate,
            "rows_used":      total,
        }
    except Exception:
        return {"long_rate": 50.0, "false_brk_rate": 0.0, "rows_used": 0}


def compute_edge_score(
    tcs: float,
    structure_conf: float,
    env_long_rate: float,
    recent_false_brk_rate: float,
    weights: dict,
) -> tuple:
    """Compute a composite 0–100 Edge Score for a live setup.

    Returns (score: float, breakdown: dict).

    Inputs (all 0–100):
    - tcs                  : TCS momentum score
    - structure_conf       : model's confidence in its top structure pick
    - env_long_rate        : % of recent setups that went bullish (market environment)
    - recent_false_brk_rate: % of recent IB breaks that faked out (lower = cleaner tape)
    """
    w = weights

    tcs_pts    = min(100.0, max(0.0, tcs))            * w.get("tcs",         0.35)
    struct_pts = min(100.0, max(0.0, structure_conf))  * w.get("structure",   0.25)
    env_pts    = min(100.0, max(0.0, env_long_rate))   * w.get("environment", 0.25)
    fb_clean   = max(0.0, 100.0 - recent_false_brk_rate)
    fb_pts     = fb_clean                              * w.get("false_break", 0.15)

    score = round(min(100.0, tcs_pts + struct_pts + env_pts + fb_pts), 1)
    return score, {
        "tcs_pts":    round(tcs_pts,    1),
        "struct_pts": round(struct_pts, 1),
        "env_pts":    round(env_pts,    1),
        "fb_pts":     round(fb_pts,     1),
        "total":      score,
    }


# ── Backtest Structure Analytics ─────────────────────────────────────────────
def compute_backtest_structure_stats(user_id: str = "") -> "pd.DataFrame":
    """Compute win rate, avg follow-through, and false break rate by structure type.

    Uses saved backtest_sim_runs (deduplicated by ticker+date) so the stats
    reflect unique setups only, not replay noise.

    Returns a DataFrame with columns:
      structure, trades, wins, win_rate, avg_follow_thru, false_brk_rate
    Sorted by win_rate descending.
    """
    df = load_backtest_sim_history(user_id)
    if df.empty:
        return pd.DataFrame(columns=[
            "structure", "trades", "wins", "win_rate", "avg_follow_thru", "false_brk_rate"
        ])

    try:
        df["sim_date"] = pd.to_datetime(df.get("sim_date", pd.NaT), errors="coerce")
        if "ticker" in df.columns and "sim_date" in df.columns:
            df = (df.sort_values("created_at", errors="ignore")
                    .drop_duplicates(subset=["ticker", "sim_date"], keep="last")
                    .reset_index(drop=True))

        if "predicted_structure" not in df.columns:
            return pd.DataFrame()

        df["win_bin"]   = (df["win_loss"] == "Win").astype(int)
        df["ft_num"]    = pd.to_numeric(df.get("follow_thru_pct", pd.Series(dtype=float)),
                                        errors="coerce")
        fb_up   = df.get("false_break_up",   pd.Series([False] * len(df))).fillna(False).astype(bool)
        fb_down = df.get("false_break_down",  pd.Series([False] * len(df))).fillna(False).astype(bool)
        df["false_brk"] = (fb_up | fb_down).astype(int)

        grp = df.groupby("predicted_structure", as_index=False).agg(
            trades        = ("win_bin",    "count"),
            wins          = ("win_bin",    "sum"),
            avg_follow_thru = ("ft_num",  lambda x: round(x.mean(), 2) if x.notna().any() else 0.0),
            false_brks    = ("false_brk", "sum"),
        )
        grp["win_rate"]       = (grp["wins"] / grp["trades"] * 100).round(1)
        grp["false_brk_rate"] = (grp["false_brks"] / grp["trades"] * 100).round(1)
        grp = grp.rename(columns={"predicted_structure": "structure"})
        grp = grp.sort_values("win_rate", ascending=False).reset_index(drop=True)
        return grp[["structure", "trades", "wins", "win_rate", "avg_follow_thru", "false_brk_rate"]]
    except Exception:
        return pd.DataFrame()


# ── Finviz Watchlist Fetcher ───────────────────────────────────────────────────
def fetch_finviz_watchlist(
    change_min_pct: float = 3.0,
    float_max_m:    float = 100.0,
    price_min:      float = 1.0,
    price_max:      float = 20.0,
    max_tickers:    int   = 100,
) -> list:
    """Scrape Finviz screener for the daily watchlist.

    Filters match Webull settings exactly:
      % Change ≥ 3%  |  Float ≤ 100M  |  Avg Vol ≥ 1M
      Relative Vol ≥ 1×  |  Price $1–$20  |  US only
      Sorted by volume descending

    Note: Finviz Elite uses Google OAuth — programmatic login is not possible.
    The screener URL still returns data (Finviz redirects elite.finviz.com to
    their new screener format and returns results). FINVIZ_EMAIL / FINVIZ_PASSWORD
    are stored for future use if Finviz adds a token-based API.

    Returns a deduplicated list of uppercase ticker strings (up to max_tickers).
    Returns [] on any error so the bot falls back to its stored watchlist.
    """
    import re as _re
    import requests as _req
    from bs4 import BeautifulSoup

    _change_map = {1: "u1", 2: "u2", 3: "u3", 5: "u5", 10: "u10", 15: "u15", 20: "u20"}
    _c = min(_change_map.keys(), key=lambda k: abs(k - change_min_pct))
    _change_filter = f"ta_change_{_change_map[_c]}"

    _float_filter = f"sh_float_u{int(float_max_m)}"
    _price_lo     = f"sh_price_o{int(price_min)}"
    _price_hi     = f"sh_price_u{int(price_max)}"

    _filters = ",".join([
        "geo_usa",
        _change_filter,
        _float_filter,
        "sh_avgvol_o1000",
        "sh_relvol_o1",
        _price_lo,
        _price_hi,
    ])

    _sess = _req.Session()
    _sess.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://finviz.com/",
    })

    tickers = []
    # elite.finviz.com redirects to finviz.com/screener with the same params
    # and returns real-time results regardless of auth status at the HTML level.
    # Paginate: 20 per page; up to 5 pages = 100 tickers
    _pages = [i * 20 + 1 for i in range((max_tickers // 20) + 1)]
    for _start in _pages:
        if len(tickers) >= max_tickers:
            break
        _url = (
            f"https://finviz.com/screener.ashx"
            f"?v=111&f={_filters}&o=-volume&r={_start}"
        )
        try:
            _resp = _sess.get(_url, timeout=12, allow_redirects=True)
            _resp.raise_for_status()
            _soup = BeautifulSoup(_resp.text, "html.parser")

            _links = _soup.find_all("a", href=_re.compile(r"quote\.ashx\?t="))
            _page_tickers = list(dict.fromkeys([
                lnk.text.strip().upper()
                for lnk in _links
                if lnk.text.strip().isalpha() and len(lnk.text.strip()) <= 5
            ]))
            _prev_len = len(tickers)
            for t in _page_tickers:
                if t not in tickers:
                    tickers.append(t)

            # Stop if page returned fewer than 20 unique tickers (last page)
            if len(_page_tickers) < 20 or (len(tickers) - _prev_len) == 0:
                break
            time.sleep(0.4)

        except Exception as _e:
            logging.warning(f"Finviz watchlist fetch error (r={_start}): {_e}")
            break

    logging.info(f"Finviz watchlist: fetched {len(tickers)} tickers")
    return tickers[:max_tickers]


# ── Watchlist Persistence ─────────────────────────────────────────────────────
def save_watchlist(tickers: list, user_id: str = "") -> bool:
    """Upsert a user's custom watchlist to Supabase (table: user_watchlist).

    Stores one row per user with a JSON-encoded list of tickers.
    Returns True on success, False on failure.
    """
    if not supabase:
        return False
    try:
        import json as _json
        payload = {
            "user_id":   user_id or "anonymous",
            "tickers":   _json.dumps([t.strip().upper() for t in tickers if t.strip()]),
            "updated_at": datetime.utcnow().isoformat(),
        }
        supabase.table("user_watchlist").upsert(payload, on_conflict="user_id").execute()
        return True
    except Exception:
        return False


def load_watchlist(user_id: str = "") -> list:
    """Load a user's saved watchlist from Supabase.

    Returns a list of ticker strings, or [] if not found / table missing.
    """
    if not supabase:
        return []
    try:
        import json as _json
        uid = user_id or "anonymous"
        res = (supabase.table("user_watchlist")
               .select("tickers")
               .eq("user_id", uid)
               .limit(1)
               .execute())
        if res.data:
            raw = res.data[0].get("tickers", "[]")
            return _json.loads(raw) if isinstance(raw, str) else list(raw)
        return []
    except Exception:
        return []


# ── End-of-Day Review Notes ───────────────────────────────────────────────────

def _compress_image_b64(file_bytes: bytes, max_px: int = 900) -> str:
    """Resize image to max_px on longest side and return as base64 JPEG string."""
    from PIL import Image as _Image
    import io as _io, base64 as _b64
    img = _Image.open(_io.BytesIO(file_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_px:
        scale = max_px / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), _Image.LANCZOS)
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=75, optimize=True)
    return _b64.b64encode(buf.getvalue()).decode()


_EOD_BACKUP = os.path.join(os.path.dirname(__file__), ".local", "eod_notes_backup.json")


def _load_local_eod_backup() -> list:
    """Read the local JSON backup file. Returns list of note dicts."""
    import json as _json
    try:
        if os.path.exists(_EOD_BACKUP):
            with open(_EOD_BACKUP, "r") as _f:
                data = _json.load(_f)
                return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _save_local_eod_backup(note: dict) -> None:
    """Upsert a note dict into the local backup (keyed by user_id + note_date + watch_tickers).

    Each ticker on each date is a fully independent entry — no merging.
    """
    import json as _json
    rows = _load_local_eod_backup()
    key = (note.get("user_id", ""), note.get("note_date", ""), note.get("watch_tickers", "").strip())
    rows = [r for r in rows
            if (r.get("user_id", ""), r.get("note_date", ""), r.get("watch_tickers", "").strip()) != key]
    rows.append(note)
    rows.sort(key=lambda r: (r.get("note_date", ""), r.get("watch_tickers", "")), reverse=True)
    os.makedirs(os.path.dirname(_EOD_BACKUP), exist_ok=True)
    with open(_EOD_BACKUP, "w") as _f:
        _json.dump(rows, _f)


def save_eod_note(note_date, notes: str, watch_tickers: str,
                  images_b64: list, user_id: str = "") -> tuple:
    """Upsert an end-of-day review note.

    Tries Supabase first; always also writes to local backup so data is
    never lost during outages.

    Returns (ok: bool, source: str) where source is 'supabase', 'local', or 'error'.
    """
    import json as _json
    uid  = user_id or "anonymous"
    nd   = str(note_date)
    wt   = watch_tickers.strip()
    now  = datetime.utcnow().isoformat()

    payload = {
        "user_id":       uid,
        "note_date":     nd,
        "notes":         notes.strip(),
        "watch_tickers": wt,
        "images":        images_b64,
        "updated_at":    now,
    }

    # Always persist locally first — never lost
    _save_local_eod_backup(payload)
    print(f"save_eod_note local backup: {nd} | {wt} | {len(images_b64)} images")

    # Then try Supabase using DELETE + INSERT (avoids any ON CONFLICT constraint issues)
    if supabase:
        try:
            sb_payload = {
                "user_id":       uid,
                "note_date":     nd,
                "notes":         notes.strip(),
                "watch_tickers": wt,
                "images":        _json.dumps(images_b64),
                "updated_at":    now,
            }
            # Delete existing row for this user+date+ticker, then insert fresh
            supabase.table("eod_notes").delete()\
                .eq("user_id", uid)\
                .eq("note_date", nd)\
                .eq("watch_tickers", wt)\
                .execute()
            supabase.table("eod_notes").insert(sb_payload).execute()
            print(f"save_eod_note Supabase OK: {nd} | {wt}")
            return True, "supabase"
        except Exception as e:
            print(f"save_eod_note Supabase error (local backup kept): {e}")
            return True, "local"

    return True, "local"


def delete_eod_note(note_date, watch_tickers: str, user_id: str = "") -> bool:
    """Delete a specific EOD note from both Supabase and local backup."""
    import json as _json
    uid = user_id or "anonymous"
    nd  = str(note_date)
    wt  = watch_tickers.strip()

    # Remove from local backup
    all_local = _load_local_eod_backup()
    filtered  = [r for r in all_local
                 if not (r.get("user_id") == uid
                         and str(r.get("note_date", "")) == nd
                         and r.get("watch_tickers", "").strip() == wt)]
    if len(filtered) < len(all_local):
        try:
            os.makedirs(os.path.dirname(_EOD_BACKUP), exist_ok=True)
            with open(_EOD_BACKUP, "w") as _ff:
                _json.dump(filtered, _ff)
        except Exception:
            pass

    # Remove from Supabase
    if supabase:
        try:
            supabase.table("eod_notes").delete()\
                .eq("user_id", uid)\
                .eq("note_date", nd)\
                .eq("watch_tickers", wt)\
                .execute()
        except Exception as e:
            print(f"delete_eod_note error: {e}")
    return True


def _sync_local_to_supabase(user_id: str = "") -> int:
    """Push local notes to Supabase using DELETE+INSERT. Returns count synced."""
    if not supabase:
        return 0
    import json as _json
    uid = user_id or "anonymous"
    local = [r for r in _load_local_eod_backup() if r.get("user_id") == uid]
    synced = 0
    for note in local:
        try:
            nd = str(note.get("note_date", ""))
            wt = note.get("watch_tickers", "").strip()
            sb = {
                "user_id":       uid,
                "note_date":     nd,
                "notes":         note.get("notes", ""),
                "watch_tickers": wt,
                "images":        _json.dumps(note.get("images", [])),
                "updated_at":    note.get("updated_at", datetime.utcnow().isoformat()),
            }
            supabase.table("eod_notes").delete()\
                .eq("user_id", uid).eq("note_date", nd).eq("watch_tickers", wt)\
                .execute()
            supabase.table("eod_notes").insert(sb).execute()
            synced += 1
        except Exception:
            pass
    return synced


def load_eod_notes(user_id: str = "", limit: int = 60) -> list:
    """Load EOD review notes — merges Supabase + local backup, newest first.

    Supabase records win on conflicts. Local-only records are included and
    auto-synced to Supabase in the background when it's reachable.
    """
    import json as _json
    uid = user_id or "anonymous"

    # Load local backup — include both uid-specific AND 'anonymous' entries (migration safety)
    all_local = _load_local_eod_backup()
    local_rows = [r for r in all_local
                  if r.get("user_id") == uid or r.get("user_id") == "anonymous"]
    # Re-stamp anonymous entries with the real uid so future syncs are correct
    for r in local_rows:
        if r.get("user_id") == "anonymous" and uid and uid != "anonymous":
            r["user_id"] = uid
    # Persist the re-stamped backup
    if any(r.get("user_id") == "anonymous" for r in all_local) and uid and uid != "anonymous":
        non_anon = [r for r in all_local if r.get("user_id") != "anonymous"]
        updated  = non_anon + local_rows
        try:
            import json as _jj
            os.makedirs(os.path.dirname(_EOD_BACKUP), exist_ok=True)
            with open(_EOD_BACKUP, "w") as _ff:
                _jj.dump(updated, _ff)
        except Exception:
            pass

    sb_rows = []
    sb_ok = False
    if supabase:
        try:
            res = (supabase.table("eod_notes")
                   .select("note_date,notes,watch_tickers,images,updated_at")
                   .eq("user_id", uid)
                   .order("note_date", desc=True)
                   .limit(limit)
                   .execute())
            sb_ok = True
            for r in (res.data or []):
                val = r.get("images", "[]")
                if isinstance(val, str):
                    try: val = _json.loads(val)
                    except: val = []
                r["images"] = val
                r.setdefault("outcome", {})
                sb_rows.append(r)
        except Exception as e:
            print(f"load_eod_notes Supabase error: {e}")

    if sb_ok:
        # Merge by (note_date, watch_tickers) — whichever version has the newer
        # updated_at wins.  This means a locally-saved entry (with images) beats
        # a stale Supabase row even when Supabase successfully loaded.
        _merged_dict: dict = {}
        for _r in sb_rows:
            _k = (str(_r.get("note_date", "")), _r.get("watch_tickers", "").strip())
            _merged_dict[_k] = _r
        _local_only_keys = []
        for _r in local_rows:
            _k = (str(_r.get("note_date", "")), _r.get("watch_tickers", "").strip())
            if _k in _merged_dict:
                # Both exist — prefer whichever is newer
                _local_ts = str(_r.get("updated_at", ""))
                _sb_ts    = str(_merged_dict[_k].get("updated_at", ""))
                if _local_ts > _sb_ts:
                    _merged_dict[_k] = _r  # local is newer (e.g. has images)
            else:
                _merged_dict[_k] = _r
                _local_only_keys.append(_k)
        merged = list(_merged_dict.values())
        # Auto-sync local-only entries to Supabase quietly
        if _local_only_keys:
            try:
                _sync_local_to_supabase(uid)
            except Exception:
                pass
    else:
        # Supabase down — return local backup only
        merged = local_rows

    merged.sort(key=lambda r: (r.get("note_date", ""), r.get("watch_tickers", "")), reverse=True)
    return merged[:limit]


def enrich_eod_from_journal(eod_notes: list, journal_df) -> list:
    """Merge quantitative journal data into EOD notes without duplication.

    For each EOD note, scans `journal_df` for a matching (ticker, date) entry.
    When found:
      - EOD note keeps its narrative text, images, and outcome (it is primary)
      - TCS, RVOL, IB high/low, structure are pulled from the journal row if
        the EOD note doesn't already carry them
      - A `_journal_ctx` dict is attached to the EOD note for display/analytics:
          {ticker: {tcs, rvol, ib_high, ib_low, structure, grade}}

    This prevents double-counting: the same trade is represented once, combining
    the qualitative depth of the EOD note with the quantitative precision of the
    journal row.  Analytics (win rates, brain calibration) should prefer this
    merged record over either source alone.
    """
    if not eod_notes or journal_df is None or journal_df.empty:
        return eod_notes

    import pandas as _pd

    # Build lookup: (ticker_upper, date_str) → journal row dict
    _jlookup: dict = {}
    for _, _jr in journal_df.iterrows():
        _tk = str(_jr.get("ticker", "")).upper().strip()
        _ts = str(_jr.get("timestamp", ""))[:10]
        if _tk and _ts:
            _jlookup[(_tk, _ts)] = _jr.to_dict()

    enriched = []
    for note in eod_notes:
        note = dict(note)  # copy — never mutate the original
        _nd  = str(note.get("note_date", ""))[:10]
        _wt  = str(note.get("watch_tickers", ""))
        _ctx: dict = {}

        for _tk_raw in [t.strip().upper() for t in _wt.split(",") if t.strip()]:
            _jrow = _jlookup.get((_tk_raw, _nd))
            if not _jrow:
                continue

            _entry: dict = {}
            for _field in ("tcs", "rvol", "ib_high", "ib_low", "structure", "grade"):
                _val = _jrow.get(_field)
                if _val is not None and str(_val) not in ("", "nan", "None"):
                    _entry[_field] = _val
            if _entry:
                _ctx[_tk_raw] = _entry

        if _ctx:
            note["_journal_ctx"] = _ctx
        enriched.append(note)

    return enriched


# ── EOD Prediction Verification ───────────────────────────────────────────────

def get_next_trading_day(after_date, api_key: str = "", secret_key: str = ""):
    """Return the first trading day strictly after `after_date`."""
    from datetime import timedelta
    candidate = after_date + timedelta(days=1)
    for _ in range(10):
        if is_trading_day(candidate):
            return candidate
        candidate += timedelta(days=1)
    return candidate


def verify_eod_predictions(note_date, watch_tickers_str: str, notes_text: str,
                           api_key: str, secret_key: str) -> dict:
    """Fetch next trading day's OHLC for each watched ticker and check if
    price levels mentioned in notes were touched.

    Returns dict keyed by ticker:
        {next_date, open, high, low, close,
         levels_above: [...], levels_below: [...],
         above_hit: bool, below_hit: bool}
    """
    import re as _re
    from datetime import date as _date

    if isinstance(note_date, str):
        note_date = _date.fromisoformat(note_date)

    next_day = get_next_trading_day(note_date, api_key, secret_key)

    # Parse tickers
    raw_tickers = [t.strip().upper() for t in _re.split(r"[,\s]+", watch_tickers_str) if t.strip()]

    # Parse price levels from notes (global — apply to all tickers for now)
    above_levels = [float(v.replace("$", "")) for v in
                    _re.findall(r"[Pp]rice\s+[Aa]bove\s+([\$]?[\d\.]+)", notes_text)]
    below_levels = [float(v.replace("$", "")) for v in
                    _re.findall(r"[Pp]rice\s+[Bb]elow\s+([\$]?[\d\.]+)", notes_text)]

    results = {}
    for ticker in raw_tickers:
        try:
            bars = fetch_bars(api_key, secret_key, ticker, next_day)
            if bars.empty:
                results[ticker] = {"next_date": str(next_day), "no_data": True,
                                   "levels_above": above_levels,
                                   "levels_below": below_levels}
                continue
            day_open  = float(bars["open"].iloc[0])
            day_high  = float(bars["high"].max())
            day_low   = float(bars["low"].min())
            day_close = float(bars["close"].iloc[-1])
            above_hit = any(day_high >= lv for lv in above_levels) if above_levels else None
            below_hit = any(day_low  <= lv for lv in below_levels) if below_levels else None
            results[ticker] = {
                "next_date":    str(next_day),
                "open":         round(day_open, 4),
                "high":         round(day_high, 4),
                "low":          round(day_low, 4),
                "close":        round(day_close, 4),
                "levels_above": above_levels,
                "levels_below": below_levels,
                "above_hit":    above_hit,
                "below_hit":    below_hit,
                "no_data":      False,
            }
        except Exception as e:
            results[ticker] = {"next_date": str(next_day), "error": str(e),
                               "levels_above": above_levels,
                               "levels_below": below_levels}
    return results


def save_eod_outcome(note_date, outcome: dict, user_id: str = "") -> bool:
    """Persist the verification outcome into eod_notes.outcome column."""
    if not supabase:
        return False
    try:
        import json as _json
        supabase.table("eod_notes").update(
            {"outcome": _json.dumps(outcome),
             "updated_at": datetime.utcnow().isoformat()}
        ).eq("user_id", user_id or "anonymous").eq("note_date", str(note_date)).execute()
        return True
    except Exception as e:
        print(f"save_eod_outcome error: {e}")
        return False


# ── Watchlist Prediction Engine ───────────────────────────────────────────────

def save_watchlist_predictions(predictions: list, user_id: str = "") -> bool:
    """Upsert batch structure+edge predictions for the user's watchlist.

    predictions: list of dicts with base keys:
        ticker, pred_date, predicted_structure, tcs, edge_score
    Optional setup brief keys (stored when present; ignored if schema not migrated):
        entry_zone_low, entry_zone_high, entry_trigger, stop_level,
        targets, pattern, pattern_neckline, win_rate_pct,
        win_rate_context, confidence_label
    One row per (user_id, ticker, pred_date) — safe to re-run same day.
    """
    import json as _json
    if not supabase or not predictions:
        return False

    def _build_row(p, include_brief: bool) -> dict:
        row = {
            "user_id":             user_id or "anonymous",
            "ticker":              str(p.get("ticker", "")).upper().strip(),
            "pred_date":           str(p.get("pred_date", date.today())),
            "predicted_structure": p.get("predicted_structure") or "—",
            "tcs":                 float(p.get("tcs") or 0),
            "edge_score":          float(p.get("edge_score") or 0),
            "verified":            False,
            "actual_structure":    "",
            "correct":             "",
        }
        if include_brief:
            targets_raw = p.get("targets")
            row["entry_zone_low"]   = p.get("entry_zone_low")
            row["entry_zone_high"]  = p.get("entry_zone_high")
            row["entry_trigger"]    = p.get("entry_trigger") or ""
            row["stop_level"]       = p.get("stop_level")
            row["targets"]          = (_json.dumps(targets_raw)
                                       if isinstance(targets_raw, list) else None)
            row["pattern"]          = p.get("pattern") or ""
            row["pattern_neckline"] = p.get("pattern_neckline")
            row["win_rate_pct"]     = p.get("win_rate_pct")
            row["win_rate_context"] = p.get("win_rate_context") or ""
            row["confidence_label"] = p.get("confidence_label") or "LOW"
        return row

    try:
        rows = [_build_row(p, include_brief=True) for p in predictions]
        supabase.table("watchlist_predictions").upsert(
            rows, on_conflict="user_id,ticker,pred_date"
        ).execute()
        return True
    except Exception as e1:
        # Schema not yet migrated — fall back to base columns only
        print(f"save_watchlist_predictions full schema failed ({e1}), retrying base columns")
        try:
            rows = [_build_row(p, include_brief=False) for p in predictions]
            supabase.table("watchlist_predictions").upsert(
                rows, on_conflict="user_id,ticker,pred_date"
            ).execute()
            return True
        except Exception as e2:
            print(f"save_watchlist_predictions error: {e2}")
            return False


def load_watchlist_predictions(user_id: str = "", pred_date=None) -> pd.DataFrame:
    """Load watchlist predictions from Supabase.

    If pred_date is None, loads all rows for the user sorted by date desc.
    """
    _base_cols = ["ticker", "pred_date", "predicted_structure", "tcs",
                  "edge_score", "actual_structure", "verified", "correct"]
    _brief_cols = ["entry_zone_low", "entry_zone_high", "entry_trigger",
                   "stop_level", "targets", "pattern", "pattern_neckline",
                   "win_rate_pct", "win_rate_context", "confidence_label"]
    _all_cols = _base_cols + _brief_cols
    if not supabase:
        return pd.DataFrame(columns=_all_cols)
    try:
        q = supabase.table("watchlist_predictions").select("*")
        uid = user_id or "anonymous"
        q = q.eq("user_id", uid)
        if pred_date:
            _ld_date  = str(pred_date)
            _ld_next  = str(pred_date + timedelta(days=1))
            q = q.gte("pred_date", _ld_date).lt("pred_date", _ld_next)
        q = q.order("edge_score", desc=True).limit(300)
        res = q.execute()
        if not res.data:
            return pd.DataFrame(columns=_all_cols)
        df = pd.DataFrame(res.data)
        for c in _all_cols:
            if c not in df.columns:
                df[c] = "" if c in _base_cols else None
        # Decode targets JSON string → list if needed
        if "targets" in df.columns:
            import json as _json
            def _parse_targets(v):
                if isinstance(v, list):
                    return v
                if isinstance(v, str) and v:
                    try:
                        return _json.loads(v)
                    except Exception:
                        pass
                return []
            df["targets"] = df["targets"].apply(_parse_targets)
        return df
    except Exception as e:
        print(f"load_watchlist_predictions error: {e}")
        return pd.DataFrame(columns=_all_cols)


def get_next_trading_day(as_of: date = None,
                         api_key: str = "",
                         secret_key: str = "") -> date:
    """Return the next NYSE trading day on or after as_of.

    - If as_of is already a trading day, returns as_of.
    - If it's a weekend/holiday, advances to the next open day.
    Uses Alpaca calendar when credentials available; falls back to
    weekend-skip + hardcoded holiday list.
    """
    if as_of is None:
        as_of = date.today()

    if api_key and secret_key:
        try:
            start_str = as_of.isoformat()
            end_str   = (as_of + timedelta(days=14)).isoformat()
            r = requests.get(
                "https://paper-api.alpaca.markets/v1/calendar",
                params={"start": start_str, "end": end_str},
                headers={
                    "APCA-API-KEY-ID":     api_key,
                    "APCA-API-SECRET-KEY": secret_key,
                },
                timeout=5,
            )
            if r.status_code == 200:
                cal = r.json()
                trading_dates = sorted([c["date"] for c in cal if c["date"] >= start_str])
                if trading_dates:
                    return date.fromisoformat(trading_dates[0])
        except Exception:
            pass

    # Fallback: skip weekends and hardcoded holidays
    d = as_of
    for _ in range(14):
        if is_trading_day(d):
            return d
        d += timedelta(days=1)
    return as_of


def verify_watchlist_predictions(api_key: str, secret_key: str,
                                  user_id: str = "", pred_date=None) -> dict:
    """Fetch end-of-day data and verify pending watchlist predictions.

    For each unverified prediction on pred_date (default: last trading day):
    - Re-runs the scoring engine on the full day's bars
    - Compares predicted_structure vs actual end-of-day structure
    - Updates the Supabase row with actual_structure + correct flag
    - Logs to accuracy_tracker so the brain can calibrate

    Returns a summary dict: {verified, correct, accuracy, date, error}.
    """
    if not supabase or not api_key or not secret_key:
        return {"verified": 0, "correct": 0, "accuracy": 0.0,
                "error": "No credentials"}

    # Default to last completed trading day (holiday-aware)
    if pred_date is None:
        # Start from yesterday and find the last actual trading day
        check_date = get_last_trading_day(
            as_of=date.today() - timedelta(days=1),
            api_key=api_key, secret_key=secret_key,
        )
    else:
        check_date = pred_date

    # Bar data date: if check_date is a non-trading day (weekend/holiday),
    # advance to the next actual trading day so we can still verify predictions
    # that were saved with a weekend/holiday date.
    if is_trading_day(check_date):
        bar_date = check_date
    else:
        bar_date = get_next_trading_day(
            as_of=check_date, api_key=api_key, secret_key=secret_key
        )

    # When user explicitly provides a date, fetch ALL predictions for that date
    # (including already-verified) so they can re-run verification.
    _explicit_date = pred_date is not None

    try:
        uid = user_id or "anonymous"
        _date_str  = str(check_date)
        _next_str  = str(check_date + timedelta(days=1))
        q = (supabase.table("watchlist_predictions")
             .select("*")
             .eq("user_id", uid)
             .gte("pred_date", _date_str)
             .lt("pred_date", _next_str))
        if not _explicit_date:
            q = q.eq("verified", False)
        res = q.execute()
        pending = res.data or []
    except Exception as e:
        return {"verified": 0, "correct": 0, "accuracy": 0.0, "error": str(e)}

    if not pending:
        return {"verified": 0, "correct": 0, "accuracy": 0.0,
                "date": str(check_date),
                "error": f"No predictions found for {check_date}"}

    verified_count = 0
    correct_count  = 0

    with ThreadPoolExecutor(max_workers=min(8, len(pending))) as executor:
        future_map = {
            executor.submit(
                _score_single_ticker, api_key, secret_key,
                p["ticker"], bar_date, "iex"
            ): p
            for p in pending
        }
        for future in as_completed(future_map):
            pred = future_map[future]
            try:
                sym, _tcs, actual_structure, _conf = future.result()
                if not actual_structure or actual_structure in ("—", ""):
                    continue
                predicted   = pred.get("predicted_structure", "")
                is_correct  = (
                    _strip_emoji(predicted) in _strip_emoji(actual_structure) or
                    _strip_emoji(actual_structure) in _strip_emoji(predicted)
                )
                correct_str = "✅" if is_correct else "❌"

                # Persist result back to the prediction row
                try:
                    supabase.table("watchlist_predictions").update({
                        "actual_structure": actual_structure,
                        "verified":         True,
                        "correct":          correct_str,
                    }).eq("id", pred["id"]).execute()
                except Exception:
                    pass

                # Feed into accuracy_tracker → triggers brain recalibration
                log_accuracy_entry(
                    symbol=sym,
                    predicted=predicted,
                    actual=actual_structure,
                    compare_key="watchlist_pred",
                    user_id=user_id,
                )
                verified_count += 1
                if is_correct:
                    correct_count += 1
            except Exception:
                continue

    accuracy = (correct_count / verified_count * 100) if verified_count > 0 else 0.0
    return {
        "verified":  verified_count,
        "total":     len(pending),
        "correct":   correct_count,
        "accuracy":  round(accuracy, 1),
        "date":      str(check_date),   # original pred_date (for display)
        "bar_date":  str(bar_date),     # actual trading day bars were fetched from
    }


# ── Webull Pattern Retroactive Scanner ───────────────────────────────────────

def scan_journal_patterns(
    api_key: str,
    secret_key: str,
    journal_df: "pd.DataFrame",
    feed: str = "iex",
) -> dict:
    """Retroactively detect chart patterns on every trade session in journal_df.

    For each unique (ticker, date) pair, fetches Alpaca 1-min bars and runs
    detect_chart_patterns.  Grades A/B count as wins; C/F count as losses.

    Returns a dict:
        sessions      — list of {ticker, date, grade, patterns, is_win}
        summary       — {pattern_name: {win, loss, total, win_rate}}
        by_outcome    — {"win": {pat:count}, "loss": {pat:count}}
        total_sessions— number of unique sessions attempted
        scanned       — number successfully scanned (had bar data)
        errors        — number that failed / had no data
    """
    if journal_df is None or journal_df.empty:
        return {"sessions": [], "summary": {}, "by_outcome": {"win": {}, "loss": {}},
                "total_sessions": 0, "scanned": 0, "errors": 0}

    WIN_GRADES  = {"A", "B"}

    # Build unique (ticker, date, grade) sessions — use the most recent grade per pair
    records = []
    ts_col = "timestamp"
    if ts_col not in journal_df.columns:
        return {"sessions": [], "summary": {}, "by_outcome": {"win": {}, "loss": {}},
                "total_sessions": 0, "scanned": 0, "errors": 0}

    _jdf = journal_df.copy()
    _jdf["_ts"]  = pd.to_datetime(_jdf[ts_col], errors="coerce")
    _jdf["_date"] = _jdf["_ts"].dt.date
    _jdf["_grade"] = _jdf["grade"].astype(str).str.upper().str.strip() if "grade" in _jdf.columns else "—"

    seen = {}
    for _, row in _jdf.dropna(subset=["_date"]).iterrows():
        tk = str(row.get("ticker", "")).upper().strip()
        dt = row["_date"]
        gr = row["_grade"]
        if not tk or not dt or tk == "NAN":
            continue
        key = (tk, dt)
        if key not in seen:
            seen[key] = gr
        else:
            # Prefer A > B > C > F over whatever we already have
            _rank = {"A": 0, "B": 1, "C": 2, "F": 3}
            if _rank.get(gr, 9) < _rank.get(seen[key], 9):
                seen[key] = gr

    sessions_meta = [{"ticker": k[0], "date": k[1], "grade": v,
                      "is_win": v in WIN_GRADES} for k, v in seen.items()]

    if not sessions_meta:
        return {"sessions": [], "summary": {}, "by_outcome": {"win": {}, "loss": {}},
                "total_sessions": 0, "scanned": 0, "errors": 0}

    # Batch-fetch bars + run pattern detection in parallel
    def _scan_one(meta):
        try:
            df = fetch_bars(api_key, secret_key, meta["ticker"], meta["date"], feed=feed)
            if df.empty or len(df) < 20:
                return None
            patterns = detect_chart_patterns(df)
            return {**meta, "patterns": patterns}
        except Exception:
            return None

    results = []
    with ThreadPoolExecutor(max_workers=min(10, len(sessions_meta))) as ex:
        future_map = {ex.submit(_scan_one, m): m for m in sessions_meta}
        for fut in as_completed(future_map):
            r = fut.result()
            if r is not None:
                results.append(r)

    # Aggregate pattern counts by outcome
    pat_stats: dict = {}
    win_counts: dict = {}
    loss_counts: dict = {}

    for sess in results:
        is_win = sess["is_win"]
        seen_pats = set()
        for p in sess.get("patterns", []):
            name = p["name"]
            if name in seen_pats:
                continue
            seen_pats.add(name)
            if name not in pat_stats:
                pat_stats[name] = {"win": 0, "loss": 0, "total": 0}
            if is_win:
                pat_stats[name]["win"] += 1
                win_counts[name] = win_counts.get(name, 0) + 1
            else:
                pat_stats[name]["loss"] += 1
                loss_counts[name] = loss_counts.get(name, 0) + 1
            pat_stats[name]["total"] += 1

    # Compute win rate per pattern
    total_wins   = sum(1 for s in results if s["is_win"])
    total_losses = sum(1 for s in results if not s["is_win"])

    summary = {}
    for pat, counts in pat_stats.items():
        t = counts["total"]
        summary[pat] = {
            "win":       counts["win"],
            "loss":      counts["loss"],
            "total":     t,
            "win_rate":  round(counts["win"] / t * 100, 1) if t > 0 else 0.0,
        }

    return {
        "sessions":       results,
        "summary":        summary,
        "by_outcome":     {"win": win_counts, "loss": loss_counts},
        "total_sessions": len(sessions_meta),
        "scanned":        len(results),
        "errors":         len(sessions_meta) - len(results),
        "total_wins":     total_wins,
        "total_losses":   total_losses,
    }


# ── God Mode — Live Trade Execution ──────────────────────────────────────────

def execute_alpaca_trade(
    api_key: str,
    secret_key: str,
    is_paper: bool,
    ticker: str,
    qty: int,
    side: str,
    limit_price: float = None,
) -> dict:
    """Submit a live or paper trade to Alpaca.

    Parameters
    ----------
    api_key, secret_key : Alpaca credentials entered in the sidebar.
    is_paper            : True  → paper trading endpoint
                          False → live trading endpoint
    ticker              : Stock symbol, e.g. 'GME'
    qty                 : Number of shares (whole shares only)
    side                : 'buy' or 'sell'
    limit_price         : If provided, submits a Day Limit order;
                          otherwise submits a Market order.

    Returns
    -------
    dict with keys:
        success  (bool)
        order_id (str)   — Alpaca order UUID on success
        message  (str)   — human-readable confirmation or error detail
    """
    if not api_key or not secret_key:
        return {"success": False, "order_id": None,
                "message": "No API credentials — enter your Alpaca key and secret in the sidebar."}
    if qty <= 0:
        return {"success": False, "order_id": None,
                "message": "Quantity must be at least 1 share."}
    if side not in ("buy", "sell"):
        return {"success": False, "order_id": None,
                "message": f"Invalid side '{side}' — must be 'buy' or 'sell'."}

    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        client = TradingClient(api_key, secret_key, paper=is_paper)

        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

        if limit_price is not None and limit_price > 0:
            req = LimitOrderRequest(
                symbol=ticker.upper(),
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
                limit_price=round(float(limit_price), 2),
            )
            order_type_label = f"LIMIT @ ${limit_price:.2f}"
        else:
            req = MarketOrderRequest(
                symbol=ticker.upper(),
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
            )
            order_type_label = "MARKET"

        order = client.submit_order(req)
        env_label = "PAPER" if is_paper else "LIVE"
        return {
            "success":  True,
            "order_id": str(order.id),
            "message":  (
                f"✅ {env_label} {side.upper()} {qty} {ticker.upper()} "
                f"({order_type_label}) submitted. "
                f"Order ID: {order.id} · Status: {order.status}"
            ),
        }

    except Exception as exc:
        return {"success": False, "order_id": None, "message": f"Alpaca error: {exc}"}


# ── User Preferences ──────────────────────────────────────────────────────────
_USER_PREFS_FILE = ".local/user_prefs.json"


def save_user_prefs(user_id: str, prefs: dict) -> bool:
    """Persist user preferences (API keys, webhook, etc.) to Supabase + local file."""
    import json as _json
    uid = user_id or "anonymous"

    # Always write locally first
    try:
        all_prefs: dict = {}
        if os.path.exists(_USER_PREFS_FILE):
            with open(_USER_PREFS_FILE) as _f:
                all_prefs = _json.load(_f)
        all_prefs[uid] = prefs
        os.makedirs(os.path.dirname(_USER_PREFS_FILE), exist_ok=True)
        with open(_USER_PREFS_FILE, "w") as _f:
            _json.dump(all_prefs, _f)
    except Exception:
        pass

    # Then Supabase
    if supabase:
        try:
            supabase.table("user_preferences").upsert(
                {"user_id": uid, "prefs": _json.dumps(prefs),
                 "updated_at": datetime.utcnow().isoformat()},
                on_conflict="user_id",
            ).execute()
        except Exception as e:
            print(f"save_user_prefs error: {e}")
    return True


def load_user_prefs(user_id: str) -> dict:
    """Load user preferences — Supabase first, local file fallback."""
    import json as _json
    uid = user_id or "anonymous"

    if supabase:
        try:
            res = (supabase.table("user_preferences")
                   .select("prefs")
                   .eq("user_id", uid)
                   .limit(1)
                   .execute())
            if res.data:
                raw = res.data[0].get("prefs", "{}")
                return _json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass

    try:
        if os.path.exists(_USER_PREFS_FILE):
            with open(_USER_PREFS_FILE) as _f:
                return _json.load(_f).get(uid, {})
    except Exception:
        pass
    return {}


def save_beta_chat_id(user_id: str, chat_id) -> bool:
    """Store a beta tester's Telegram chat ID in their user prefs."""
    if not user_id:
        return False
    prefs = load_user_prefs(user_id)
    prefs["tg_chat_id"] = str(chat_id)
    return save_user_prefs(user_id, prefs)


def get_beta_chat_ids(exclude_user_id: str = "") -> list:
    """Return list of (user_id, chat_id) tuples for all beta subscribers.

    Skips exclude_user_id (the owner) so they don't get duplicate messages.
    Falls back to the local prefs file when Supabase is unavailable.
    """
    import json as _json

    def _extract_pairs(rows_dict: dict) -> list:
        found = []
        for uid, prefs in rows_dict.items():
            if exclude_user_id and uid == exclude_user_id:
                continue
            cid = prefs.get("tg_chat_id") if isinstance(prefs, dict) else None
            if cid:
                try:
                    found.append((uid, int(cid)))
                except (ValueError, TypeError):
                    pass
        return found

    if supabase:
        try:
            res = supabase.table("user_preferences").select("user_id,prefs").execute()
            pairs = []
            for row in res.data:
                uid = row.get("user_id", "")
                if exclude_user_id and uid == exclude_user_id:
                    continue
                raw = row.get("prefs", "{}")
                prefs = _json.loads(raw) if isinstance(raw, str) else (raw or {})
                cid = prefs.get("tg_chat_id")
                if cid:
                    try:
                        pairs.append((uid, int(cid)))
                    except (ValueError, TypeError):
                        pass
            return pairs
        except Exception as e:
            print(f"get_beta_chat_ids Supabase error, trying local fallback: {e}")

    # Local file fallback when Supabase is unavailable
    try:
        if os.path.exists(_USER_PREFS_FILE):
            with open(_USER_PREFS_FILE) as _f:
                all_prefs = _json.load(_f)
            return _extract_pairs(all_prefs)
    except Exception as e:
        print(f"get_beta_chat_ids local fallback error: {e}")
    return []


# ══════════════════════════════════════════════════════════════════════════════
# MACRO BREADTH REGIME  (Stockbee breadth data — top-down regime filter)
# ══════════════════════════════════════════════════════════════════════════════

def classify_macro_regime(
    four_pct_count: int,
    ratio_13_34: float,
    q_up: int,
    q_down: int,
) -> dict:
    """Classify macro market regime from Stockbee breadth inputs.

    Inputs:
      four_pct_count — Stocks up 4%+ on the day (from Stockbee Market Monitor)
      ratio_13_34    — 5-day or 10-day Advance/Decline ratio (>1.0 = more advances)
      q_up           — Stocks up 25%+ in a quarter
      q_down         — Stocks down 25%+ in a quarter

    Returns:
      regime_tag:    "hot_tape" | "warm" | "cold"
      label:         display label with emoji
      color:         hex color for UI
      mode:          "home_run" | "singles" | "caution"
      tcs_floor_adj: int — TCS threshold shift (negative = lower bar on hot tape)
      description:   brief explanation string
    """
    _desc = (
        f"{four_pct_count} stocks up 4%+ · A/D {ratio_13_34:.1f}x · "
        f"Q: {q_up} up / {q_down} down"
    )

    # ── Quarterly breadth ratio (Stockbee 25%/quarter flip) ─────────────────
    # q_ratio > 1.0 = more stocks up 25%+ than down 25%+ (bullish quarterly)
    # q_ratio < 1.0 = more stocks down 25%+ than up 25%+ (bearish quarterly)
    # When no quarterly data supplied (both 0), treat as neutral (ratio = 1.0)
    q_ratio = (q_up / max(q_down, 1)) if (q_up > 0 or q_down > 0) else 1.0

    # ── Strict rule-based classification (three-signal system) ───────────────
    # All three Stockbee breadth inputs feed the regime:
    #   Signal 1: daily 4%+ count  (momentum / thrust)
    #   Signal 2: 13%/34d A/D ratio  (intermediate breadth)
    #   Signal 3: quarterly 25% flip ratio  (macro tide)
    #
    # hot  = strong daily (≥600) AND strong A/D (≥2.0) AND quarterly not bearish (≥1.0)
    # warm = good daily (≥300) AND positive A/D (≥1.0)  [quarterly neutral or better]
    # cold = everything else (weak daily, weak A/D, or deeply bearish quarterly)
    if four_pct_count >= 600 and ratio_13_34 >= 2.0 and q_ratio >= 1.0:
        return {
            "regime_tag":    "hot_tape",
            "label":         "🔥 Hot Tape",
            "color":         "#ff6b35",
            "mode":          "home_run",
            "tcs_floor_adj": -10,
            "description":   _desc,
        }
    elif four_pct_count >= 300 and ratio_13_34 >= 1.0:
        return {
            "regime_tag":    "warm",
            "label":         "🟡 Warm Tape",
            "color":         "#ffd700",
            "mode":          "singles",
            "tcs_floor_adj": 0,
            "description":   _desc,
        }
    else:
        return {
            "regime_tag":    "cold",
            "label":         "❄️ Cold Tape",
            "color":         "#5c9bd4",
            "mode":          "caution",
            "tcs_floor_adj": +10,
            "description":   _desc,
        }


_MACRO_BREADTH_SQL = """
CREATE TABLE IF NOT EXISTS macro_breadth_log (
  id              SERIAL PRIMARY KEY,
  user_id         TEXT NOT NULL DEFAULT '',
  trade_date      DATE NOT NULL,
  four_pct_count  INT NOT NULL DEFAULT 0,
  ratio_13_34     FLOAT NOT NULL DEFAULT 0.0,
  q_up            INT NOT NULL DEFAULT 0,
  q_down          INT NOT NULL DEFAULT 0,
  regime_tag      TEXT,
  mode            TEXT,
  tcs_floor_adj   INT DEFAULT 0,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(trade_date, user_id)
);
""".strip()


def ensure_macro_breadth_log_table() -> bool:
    """Check if macro_breadth_log exists in Supabase. Returns True if ready.

    If missing, prints the SQL needed and returns False.
    Create the table by pasting _MACRO_BREADTH_SQL into the Supabase SQL editor.
    """
    if not supabase:
        return False
    try:
        supabase.table("macro_breadth_log").select("id").limit(1).execute()
        return True
    except Exception as e:
        err = str(e).lower()
        if any(k in err for k in ("404", "relation", "does not exist", "not found", "pgrst205")):
            print(
                "macro_breadth_log table not found.\n"
                "Run the following SQL in your Supabase SQL Editor:\n\n"
                + _MACRO_BREADTH_SQL
            )
            return False
        print(f"ensure_macro_breadth_log_table error: {e}")
        return False


def save_breadth_regime(
    trade_date,
    four_pct: int,
    ratio_13_34: float,
    q_up: int,
    q_down: int,
    user_id: str = "",
) -> bool:
    """Persist a breadth regime snapshot to Supabase (macro_breadth_log table).

    Upserts by (trade_date, user_id) so re-logging the same day updates in-place.
    Returns True on success.
    """
    if not supabase:
        return False
    regime = classify_macro_regime(four_pct, ratio_13_34, q_up, q_down)
    row = {
        "trade_date":     str(trade_date),
        "four_pct_count": int(four_pct),
        "ratio_13_34":    float(ratio_13_34),
        "q_up":           int(q_up),
        "q_down":         int(q_down),
        "regime_tag":     regime["regime_tag"],
        "mode":           regime["mode"],
        "tcs_floor_adj":  regime["tcs_floor_adj"],
        "user_id":        user_id or "",
    }
    try:
        supabase.table("macro_breadth_log").upsert(
            row, on_conflict="trade_date,user_id"
        ).execute()
        return True
    except Exception as e:
        print(f"save_breadth_regime error: {e}")
        return False


def get_breadth_regime(trade_date=None, user_id: str = "") -> dict:
    """Retrieve the most recent breadth regime from Supabase for a specific user.

    If trade_date is given, looks up that specific date for the user.
    Otherwise returns the most recent entry for that user.
    Falls back to a neutral 'no data' dict on any error.
    user_id is required to scope results correctly; global reads are not permitted.
    """
    _neutral = {
        "regime_tag":    "unknown",
        "label":         "⬜ No Data",
        "color":         "#555555",
        "mode":          "singles",
        "tcs_floor_adj": 0,
        "description":   "No breadth data yet — enter today's numbers in the sidebar.",
        "trade_date":    "",
    }
    if not supabase:
        return _neutral
    # Require user_id to prevent cross-user data leakage
    uid = user_id or ""
    try:
        q = (
            supabase.table("macro_breadth_log")
            .select("*")
            .eq("user_id", uid)
        )
        if trade_date:
            q = q.eq("trade_date", str(trade_date))
        res = q.order("trade_date", desc=True).limit(1).execute()
        if res.data:
            row = res.data[0]
            result = classify_macro_regime(
                row.get("four_pct_count", 0),
                row.get("ratio_13_34", 0.0),
                row.get("q_up", 0),
                row.get("q_down", 0),
            )
            result["trade_date"] = row.get("trade_date", "")
            return result
    except Exception as e:
        print(f"get_breadth_regime error: {e}")
    return _neutral


def get_breadth_regime_history(days: int = 30, user_id: str = "") -> list:
    """Return up to `days` breadth regime entries from Supabase for a user, newest first.

    user_id is required to scope results to the authenticated user only.
    """
    if not supabase:
        return []
    uid = user_id or ""
    try:
        from datetime import date as _date, timedelta as _td
        cutoff = str(_date.today() - _td(days=days))
        res = (
            supabase.table("macro_breadth_log")
            .select("*")
            .eq("user_id", uid)
            .gte("trade_date", cutoff)
            .order("trade_date", desc=True)
            .limit(days)
            .execute()
        )
        raw = res.data or []
        # Enrich each row with computed regime fields
        enriched = []
        for row in raw:
            entry = classify_macro_regime(
                row.get("four_pct_count", 0),
                row.get("ratio_13_34", 0.0),
                row.get("q_up", 0),
                row.get("q_down", 0),
            )
            entry["trade_date"] = row.get("trade_date", "")
            enriched.append(entry)
        return enriched
    except Exception as e:
        print(f"get_breadth_regime_history error: {e}")
        return []


