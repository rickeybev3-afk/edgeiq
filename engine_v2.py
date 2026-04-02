import pandas as pd
import numpy as np
from datetime import datetime, time


# ── Column normalizer ──────────────────────────────────────────────────────────
def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Accept Title-Case (High/Low/…) or lowercase (high/low/…) column names."""
    rename = {}
    for col in df.columns:
        if col.lower() in ('high', 'low', 'open', 'close', 'volume'):
            rename[col] = col.lower()
    return df.rename(columns=rename) if rename else df.copy()


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — METRIC ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def calculate_v2_metrics(df: pd.DataFrame, atr_period: int = 14):
    """Z-Score, Delta, ATR — tuned for small-cap volatility.

    Returns (enriched_df, bin_size).
    """
    df = _normalize_cols(df)

    # Dynamic Binning (ATR-based)
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            np.abs(df['high'] - df['close'].shift()),
            np.abs(df['low']  - df['close'].shift()),
        ),
    )
    df['atr'] = df['tr'].rolling(window=atr_period).mean()
    atr = df['atr'].iloc[-1] if not pd.isna(df['atr'].iloc[-1]) else 0.1
    bin_size = max(0.01, atr * 0.1)

    # Time-Segmented Z-Score Volume
    df.index = pd.to_datetime(df.index)
    idx_name = df.index.name or 'Date'
    df.index.name = idx_name
    df['hour'], df['minute'] = df.index.hour, df.index.minute

    stats = (
        df.groupby(['hour', 'minute'], observed=True)['volume']
        .agg(['mean', 'std'])
        .reset_index()
    )
    df = (
        df.reset_index()
          .merge(stats, on=['hour', 'minute'], how='left')
          .set_index(idx_name)
    )
    df['vol_z_score'] = (df['volume'] - df['mean']) / (df['std'] + 0.1)

    # Order Flow Delta & CVD
    df['delta'] = (
        (df['close'] - df['open']) / (df['high'] - df['low'] + 0.001)
    ) * df['volume']
    df['cvd'] = df['delta'].cumsum()

    df['cvd_slope'] = df['cvd'].rolling(window=5).apply(
        lambda x: np.polyfit(range(len(x)), x, 1)[0] if len(x) == 5 else 0,
        raw=True,
    )
    df['price_slope'] = df['close'].rolling(window=5).apply(
        lambda x: np.polyfit(range(len(x)), x, 1)[0] if len(x) == 5 else 0,
        raw=True,
    )
    df['divergence'] = np.where(
        (df['cvd_slope'] > 0) & (df['price_slope'] < 0), 'Bullish_Abs',
        np.where(
            (df['cvd_slope'] < 0) & (df['price_slope'] > 0), 'Bearish_Dist',
            'Neutral',
        ),
    )

    return df, bin_size


# ══════════════════════════════════════════════════════════════════════════════
# TIME MULTIPLIER — Spirit of the Session
# ══════════════════════════════════════════════════════════════════════════════

def calculate_time_multiplier(current_time) -> float:
    """Adjust score for market phase energy.

    9:30–10:30  → 1.2× Golden Hour  (most small-cap runners born here)
    12:00–14:00 → 0.5× Lunch Lull   (chop / fading momentum)
    15:00–16:00 → 1.0× Power Hour
    All other   → 0.7×
    """
    if time(9, 30) <= current_time <= time(10, 30):
        return 1.2
    elif time(12, 0) <= current_time <= time(14, 0):
        return 0.5
    elif time(15, 0) <= current_time <= time(16, 0):
        return 1.0
    return 0.7


# ══════════════════════════════════════════════════════════════════════════════
# 100-0 DETECTOR — Historical Gap Failure Rate
# ══════════════════════════════════════════════════════════════════════════════

def calculate_historical_retention(daily_df: pd.DataFrame) -> float:
    """Does this stock historically gap up then fail?

    Scans all days where the open gapped >15% above prior close,
    then checks whether price was below that open 3 sessions later.

    Returns failure_rate (0.0 – 1.0).  >0.60 → avoid.
    """
    daily_df = _normalize_cols(daily_df).copy()
    daily_df['gap'] = (
        (daily_df['open'] - daily_df['close'].shift(1))
        / daily_df['close'].shift(1)
    )
    gaps = daily_df[daily_df['gap'] > 0.15]
    if len(gaps) == 0:
        return 0.0

    failures = 0
    for idx in gaps.index:
        try:
            future_idx = daily_df.index.get_loc(idx) + 3
            if daily_df.iloc[future_idx]['close'] < daily_df.loc[idx]['open']:
                failures += 1
        except Exception:
            continue

    return failures / len(gaps)


# ══════════════════════════════════════════════════════════════════════════════
# OVERHEAD SUPPLY — Bag-Holder Wall Detector
# ══════════════════════════════════════════════════════════════════════════════

def identify_overhead_supply(daily_df: pd.DataFrame, current_price: float) -> list:
    """Find historical price levels with heavy trapped volume above current price.

    Uses $0.50 bins on the daily profile.  Returns a list of float price levels
    where volume exceeds 70% of the max bin — these are "bag-holder walls."
    """
    daily_df = _normalize_cols(daily_df).copy()
    price_min = float(daily_df['low'].min())
    price_max = float(daily_df['high'].max())
    bins      = np.arange(price_min, price_max + 0.50, 0.50)

    daily_df['price_bin'] = pd.cut(daily_df['close'], bins=bins, labels=bins[:-1])
    profile   = (
        daily_df.groupby('price_bin', observed=True)['volume']
        .sum()
        .reset_index()
    )
    profile.columns = ['price_bin', 'volume']
    profile['price_bin'] = profile['price_bin'].astype(float)
    max_vol = profile['volume'].max()

    walls = profile[
        (profile['price_bin'] > current_price) &
        (profile['volume']    > max_vol * 0.7)
    ]
    return walls['price_bin'].tolist()


# ══════════════════════════════════════════════════════════════════════════════
# HALT DETECTOR — Volatility Halt Fingerprint
# ══════════════════════════════════════════════════════════════════════════════

def detect_volatility_halts(df: pd.DataFrame, bar_freq_minutes: int = 1,
                             luld_threshold: float = 0.10) -> dict:
    """Detect LULD (Limit-Up / Limit-Down) volatility halt signatures.

    A halt leaves three fingerprints in intraday bar data:
      1. TIME GAP   — a span of missing bars during market hours (exchange
                      suspended trading; Alpaca returns no data for that window)
      2. PRE-HALT   — the bar immediately before the gap has an extreme price
                      move (>= luld_threshold, default 10%) AND a volume spike
                      (>= 2× the rolling average)
      3. POST-HALT  — the bar immediately after the gap is the re-open; price
                      often prints a Thin/I-Shape because trapped sellers are
                      exhausted and liquidity is thin

    Parameters
    ----------
    df              : intraday OHLCV DataFrame with a DatetimeIndex
    bar_freq_minutes: expected bar interval (1 = 1-min bars)
    luld_threshold  : single-bar % move that triggers LULD consideration (0.10 = 10%)

    Returns
    -------
    dict with keys:
      halt_zones      : list of (gap_start, gap_end, gap_minutes) tuples
      pre_halt_bars   : list of bar timestamps where the extreme move occurred
      post_halt_bars  : list of bar timestamps immediately after each gap
      halt_count      : int — total halts detected today
      is_post_halt_now: bool — True if the LAST bar is a post-halt re-open
      luld_triggers   : list of bar timestamps where LULD threshold was breached
                        (may or may not have halted, but flag as high-risk)
    """
    df = _normalize_cols(df)
    df = df.copy()
    df.index = pd.to_datetime(df.index)

    result = {
        'halt_zones':       [],
        'pre_halt_bars':    [],
        'post_halt_bars':   [],
        'halt_count':       0,
        'is_post_halt_now': False,
        'luld_triggers':    [],
    }

    if len(df) < 3:
        return result

    expected_gap = pd.Timedelta(minutes=bar_freq_minutes)
    # Only look at market hours (9:30 – 16:00 ET)
    mkt_open  = df.index[0].replace(hour=9,  minute=30, second=0)
    mkt_close = df.index[0].replace(hour=16, minute=0,  second=0)
    df_mkt = df[(df.index >= mkt_open) & (df.index <= mkt_close)]

    if df_mkt.empty:
        return result

    # ── 1. Volume rolling average (for spike detection) ────────────────────────
    vol_avg = df_mkt['volume'].rolling(window=10, min_periods=3).mean()

    # ── 2. Single-bar % move ──────────────────────────────────────────────────
    bar_move = (df_mkt['high'] - df_mkt['low']) / (df_mkt['close'].shift(1).abs() + 0.001)

    # ── 3. Scan for time gaps ──────────────────────────────────────────────────
    times = df_mkt.index.tolist()
    for i in range(1, len(times)):
        actual_gap = times[i] - times[i - 1]

        # Gap must be at least 2× the expected interval to count as a halt
        if actual_gap >= expected_gap * 2:
            gap_minutes = int(actual_gap.total_seconds() / 60)
            gap_start   = times[i - 1]
            gap_end     = times[i]

            result['halt_zones'].append((gap_start, gap_end, gap_minutes))
            result['halt_count'] += 1

            # Pre-halt bar
            result['pre_halt_bars'].append(gap_start)

            # Post-halt bar (first bar after resumption)
            result['post_halt_bars'].append(gap_end)

    # ── 4. LULD trigger scan (extreme moves — halt may not have occurred) ──────
    luld_bars = df_mkt.index[bar_move >= luld_threshold].tolist()
    # Also flag volume spikes ≥ 3× average as potential LULD approach
    spike_bars = df_mkt.index[df_mkt['volume'] >= vol_avg * 3].tolist()
    result['luld_triggers'] = sorted(set(luld_bars + spike_bars))

    # ── 5. Is the current (last) bar a post-halt re-open? ─────────────────────
    if result['post_halt_bars'] and times[-1] == result['post_halt_bars'][-1]:
        result['is_post_halt_now'] = True

    return result


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — THE SKULL (Volume Profile + Shape Classification)
# ══════════════════════════════════════════════════════════════════════════════

def get_profile_and_shape(df: pd.DataFrame, bin_size: float):
    """ATR-binned volume profile + P / b / D / B / Thin shape classification.

    ┌───────────────┬────────────────────────────────────────────────────┐
    │  P-Shape      │  Volume top-heavy → short covering / buying trapped│
    │  b-Shape      │  Volume bottom-heavy → long liquidation / selling  │
    │  D-Shape      │  Volume middle-heavy → balanced / mean-reversion   │
    │  B-Shape      │  High top + bottom → double distribution           │
    │  Thin/I-Shape │  None of above → parabolic runner, no resistance   │
    └───────────────┴────────────────────────────────────────────────────┘

    Returns (profile_df, poc_price, shape_str, void_zones).
    """
    df = _normalize_cols(df).copy()

    price_min = float(df['low'].min())
    price_max = float(df['high'].max())
    bins      = np.arange(price_min, price_max + bin_size, bin_size)

    df['price_bin'] = pd.cut(df['close'], bins=bins, labels=bins[:-1])
    profile = (
        df.groupby('price_bin', observed=True)['volume']
        .sum()
        .reset_index()
    )
    profile.columns = ['price', 'volume']
    profile['price'] = profile['price'].astype(float)

    poc_price   = float(profile.loc[profile['volume'].idxmax(), 'price'])
    total_range = price_max - price_min
    top_third   = price_max - (total_range / 3)
    bottom_third = price_min + (total_range / 3)

    vol_top = profile[profile['price'] >= top_third]['volume'].sum()
    vol_bot = profile[profile['price'] <= bottom_third]['volume'].sum()
    vol_mid = profile[(profile['price'] < top_third) & (profile['price'] > bottom_third)]['volume'].sum()

    if vol_top > (vol_mid + vol_bot) * 0.6:
        shape = "P-Shape"
    elif vol_bot > (vol_top + vol_mid) * 0.6:
        shape = "b-Shape"
    elif vol_mid > (vol_top + vol_bot) * 0.7:
        shape = "D-Shape"
    elif vol_top > vol_mid and vol_bot > vol_mid:
        shape = "B-Shape"
    else:
        shape = "Thin/I-Shape"

    void_zones = profile[
        profile['volume'] < (profile['volume'].max() * 0.2)
    ]['price'].tolist()

    return profile, poc_price, shape, void_zones


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — THE BRAIN (Final Boss Confluence Engine)
# ══════════════════════════════════════════════════════════════════════════════

def v2_brain_final_boss(row, shape: str, void_zones: list,
                         overhead_walls: list, failure_rate: float,
                         current_time) -> float:
    """All frequencies combined into one time-weighted score.

    Factors
    -------
    - Profile shape structural bias
    - Institutional z-score (time-segmented)
    - CVD order-flow divergence
    - 100-0 gap-failure penalty
    - Overhead bag-holder wall proximity penalty
    - Time multiplier (Golden Hour / Lunch Lull / Power Hour)

    Returns a float score.  >= 75 → Sniper Buy.  <= -35 → No Trade.
    """
    score = 0.0

    # 1. Structural Bias
    if shape in ("P-Shape", "Thin/I-Shape"):
        score += 25
    elif shape == "b-Shape":
        score -= 30

    # 2. Institutional Z-Score
    z = row.get('vol_z_score', row.get('Vol_Z_Score', 0))
    if pd.notna(z) and z > 2.5:
        score += 40

    # 3. Order Flow Divergence
    if row.get('divergence', 'Neutral') == 'Bullish_Abs':
        score += 20

    # 4. 100-0 Gap-Failure Penalty
    if failure_rate > 0.60:
        score -= 50

    # 5. Bag-Holder Wall Proximity Penalty
    close_price = float(row.get('close', row.get('Close', 0)))
    for wall in overhead_walls:
        if wall > 0 and abs(close_price - wall) / wall < 0.02:
            score -= 30

    # 6. Time Multiplier
    score *= calculate_time_multiplier(current_time)

    return score


# ══════════════════════════════════════════════════════════════════════════════
# BACKWARD-COMPAT ALIASES
# ══════════════════════════════════════════════════════════════════════════════

def v2_brain_v3(row, shape: str, void_zones: list, pillar_score: int = 0) -> float:
    """Legacy alias — calls final boss with no history/walls/time context."""
    return v2_brain_final_boss(
        row, shape, void_zones,
        overhead_walls=[], failure_rate=0.0,
        current_time=time(9, 31),
    )


def get_volume_profile_v2(df: pd.DataFrame, bin_size: float):
    """Legacy alias → returns (profile, poc_price, void_zones)."""
    profile, poc_price, _shape, void_zones = get_profile_and_shape(df, bin_size)
    return profile, poc_price, void_zones


def v2_execution_logic(row, void_zones: list) -> float:
    """Legacy alias → calls final boss with minimal context."""
    return v2_brain_final_boss(
        row, shape="", void_zones=void_zones,
        overhead_walls=[], failure_rate=0.0,
        current_time=time(9, 31),
    )
