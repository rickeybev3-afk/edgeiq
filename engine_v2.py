import pandas as pd
import numpy as np


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
    """Calculate ATR-based binning, time-segmented z-score volume, and CVD.

    Returns (enriched_df, bin_size).
    Works with both Title-Case and lowercase OHLCV column names.
    """
    df = _normalize_cols(df)

    # ── 1. Dynamic Binning (ATR-based) ─────────────────────────────────────────
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            np.abs(df['high'] - df['close'].shift()),
            np.abs(df['low']  - df['close'].shift()),
        ),
    )
    df['atr'] = df['tr'].rolling(window=atr_period).mean()
    atr = df['atr'].iloc[-1] if not pd.isna(df['atr'].iloc[-1]) else 0.1
    bin_size = max(0.01, atr * 0.1)          # higher resolution for small-caps

    # ── 2. Time-Segmented Z-Score Volume ───────────────────────────────────────
    df.index = pd.to_datetime(df.index)
    idx_name = df.index.name or 'Date'
    df.index.name = idx_name

    df['hour']   = df.index.hour
    df['minute'] = df.index.minute

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

    # ── 3. Order Flow Delta & CVD ───────────────────────────────────────────────
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
# PHASE 2 — THE SKULL (Volume Profile + Shape Classification)
# ══════════════════════════════════════════════════════════════════════════════

def get_profile_and_shape(df: pd.DataFrame, bin_size: float):
    """Build ATR-binned volume profile and classify its shape.

    Profile shapes (The Thirds Rule):
    ┌───────────────┬────────────────────────────────────────────────────┐
    │  P-Shape      │  Volume heavy in top third  → Short covering /     │
    │               │  aggressive buying; bulls trapped at top           │
    │  b-Shape      │  Volume heavy in bottom third → Long liquidation / │
    │               │  aggressive selling; bears trapped at bottom       │
    │  D-Shape      │  Volume heavy in middle third → Balanced / mean-   │
    │               │  reversion day; market found fair value            │
    │  B-Shape      │  High volume in top AND bottom thirds → Double     │
    │               │  distribution / trending with two value areas      │
    │  Thin/I-Shape │  None of the above → Parabolic / emotional trend   │
    │               │  (Ross Cameron "Runner"); no overhead resistance   │
    └───────────────┴────────────────────────────────────────────────────┘

    Returns (profile_df, poc_price, shape_str, void_zones).
    """
    df = _normalize_cols(df)

    price_min = float(df['low'].min())
    price_max = float(df['high'].max())
    bins      = np.arange(price_min, price_max + bin_size, bin_size)

    df = df.copy()
    df['price_bin'] = pd.cut(df['close'], bins=bins, labels=bins[:-1])
    profile = (
        df.groupby('price_bin', observed=True)['volume']
        .sum()
        .reset_index()
    )
    profile.columns = ['price', 'volume']
    profile['price'] = profile['price'].astype(float)

    poc_price    = float(profile.loc[profile['volume'].idxmax(), 'price'])
    total_range  = price_max - price_min

    top_third    = price_max - (total_range / 3)
    bottom_third = price_min + (total_range / 3)

    vol_top = profile[profile['price'] >= top_third]['volume'].sum()
    vol_mid = profile[(profile['price'] < top_third) & (profile['price'] > bottom_third)]['volume'].sum()
    vol_bot = profile[profile['price'] <= bottom_third]['volume'].sum()

    if vol_top > (vol_mid + vol_bot) * 0.6:
        shape = "P-Shape"       # short covering / aggressive buying
    elif vol_bot > (vol_top + vol_mid) * 0.6:
        shape = "b-Shape"       # long liquidation / aggressive selling
    elif vol_mid > (vol_top + vol_bot) * 0.7:
        shape = "D-Shape"       # balanced / mean reversion
    elif vol_top > vol_mid and vol_bot > vol_mid:
        shape = "B-Shape"       # double distribution / trending
    else:
        shape = "Thin/I-Shape"  # parabolic / emotional runner

    poc_vol    = profile['volume'].max()
    void_zones = profile[profile['volume'] < (poc_vol * 0.2)]['price'].tolist()

    return profile, poc_price, shape, void_zones


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — THE BRAIN (Confluence Engine v3)
# ══════════════════════════════════════════════════════════════════════════════

def v2_brain_v3(row, shape: str, void_zones: list, pillar_score: int = 0) -> int:
    """Five-factor confluence score.

    Factors
    -------
    1. Profile shape bias   (structure)
    2. Institutional z-score (hidden buying/selling at specific time slots)
    3. CVD / order-flow divergence (effort vs result trap detector)
    4. Liquidity void velocity (price in thin zone → acceleration likely)
    5. Ross Cameron pillar feed (low float + catalyst — caller supplies value)

    Returns an integer score:
      >= 75  → SNIPER BUY — all frequencies aligned
      <= -35 → NO TRADE   — distribution / high risk
    """
    score = 0

    # ── 1. Structural Bias ─────────────────────────────────────────────────────
    shape_scores = {
        "P-Shape":      20,   # aggressive buying trapped at top
        "Thin/I-Shape": 15,   # runner — no overhead resistance
        "D-Shape":     -10,   # choppy balanced day, reduce aggression
        "b-Shape":     -30,   # aggressive selling
        "B-Shape":       0,   # double dist — neutral until confirmed
    }
    score += shape_scores.get(shape, 0)

    # ── 2. Institutional Aggression (time-segmented z-score) ──────────────────
    z = row.get('vol_z_score', row.get('Vol_Z_Score', 0))
    if pd.notna(z):
        if z > 2.5:
            score += 40
        elif z > 1.5:
            score += 15

    # ── 3. Order Flow Divergence ───────────────────────────────────────────────
    div = row.get('divergence', 'Neutral')
    if div == 'Bullish_Abs':
        score += 25
    elif div == 'Bearish_Dist':
        score -= 40

    # ── 4. Liquidity Void Velocity ─────────────────────────────────────────────
    close_price = float(row.get('close', row.get('Close', 0)))
    if any(abs(close_price - float(v)) < 0.05 for v in void_zones):
        score += 20

    # ── 5. Ross Cameron Pillar Feed ────────────────────────────────────────────
    score += int(pillar_score)

    return score


# ══════════════════════════════════════════════════════════════════════════════
# BACKWARD-COMPAT ALIASES (keep old callers working)
# ══════════════════════════════════════════════════════════════════════════════

def get_volume_profile_v2(df: pd.DataFrame, bin_size: float):
    """Legacy alias → returns (profile, poc_price, void_zones)."""
    profile, poc_price, _shape, void_zones = get_profile_and_shape(df, bin_size)
    return profile, poc_price, void_zones


def v2_execution_logic(row, void_zones: list) -> int:
    """Legacy alias → calls v2_brain_v3 with no shape bias or pillar score."""
    return v2_brain_v3(row, shape="", void_zones=void_zones, pillar_score=0)
