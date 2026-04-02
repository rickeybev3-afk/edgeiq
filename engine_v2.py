import pandas as pd
import numpy as np


def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Accept both Title-Case (High/Low/…) and lowercase (high/low/…) column names."""
    rename = {}
    for col in df.columns:
        if col.lower() in ('high', 'low', 'open', 'close', 'volume'):
            rename[col] = col.lower()
    return df.rename(columns=rename) if rename else df.copy()


def calculate_v2_metrics(df: pd.DataFrame, atr_period: int = 14):
    """The Master Calculation Engine.

    Accepts a DataFrame with OHLCV data (column names may be Title-Case or
    lowercase).  Returns (enriched_df, bin_size).
    """
    df = _normalize_cols(df)

    # ── 1. DYNAMIC BINNING (ATR-BASED) ─────────────────────────────────────────
    df['high_low']    = df['high'] - df['low']
    df['high_close']  = np.abs(df['high'] - df['close'].shift())
    df['low_close']   = np.abs(df['low']  - df['close'].shift())
    df['tr']          = df[['high_low', 'high_close', 'low_close']].max(axis=1)
    atr               = df['tr'].rolling(window=atr_period).mean().iloc[-1]

    bin_size = max(0.01, atr * 0.1)          # resolution scaled for small-caps

    # ── 2. TIME-SEGMENTED Z-SCORE VOLUME ───────────────────────────────────────
    df.index = pd.to_datetime(df.index)
    idx_name = df.index.name or 'Date'
    df.index.name = idx_name

    df['hour']   = df.index.hour
    df['minute'] = df.index.minute

    stats = (
        df.groupby(['hour', 'minute'])['volume']
        .agg(['mean', 'std'])
        .reset_index()
    )

    df = (
        df.reset_index()
          .merge(stats, on=['hour', 'minute'], how='left')
          .set_index(idx_name)
    )

    df['vol_z_score'] = (df['volume'] - df['mean']) / (df['std'] + 0.1)

    # ── 3. ORDER FLOW DELTA (The Muscle) ────────────────────────────────────────
    df['delta'] = (
        (df['close'] - df['open']) / (df['high'] - df['low'] + 0.001)
    ) * df['volume']
    df['cvd'] = df['delta'].cumsum()

    # Divergence tracking — trap detector
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


def get_volume_profile_v2(df: pd.DataFrame, bin_size: float):
    """The Skull (Structure).

    Returns (profile_df, poc_price, void_zones).
    """
    df = _normalize_cols(df)

    price_min = df['low'].min()
    price_max = df['high'].max()
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

    poc_price = float(profile.loc[profile['volume'].idxmax(), 'price'])

    poc_vol    = profile['volume'].max()
    void_zones = profile[profile['volume'] < (poc_vol * 0.2)]['price'].tolist()

    return profile, poc_price, void_zones


def v2_execution_logic(row, void_zones: list) -> int:
    """The Brain (Confluence).  Returns a numeric score."""
    score = 0

    # A: Institutional Z-Score
    z = row.get('vol_z_score', 0)
    if pd.notna(z) and z > 2.5:
        score += 40

    # B: Delta / CVD Divergence
    div = row.get('divergence', 'Neutral')
    if div == 'Bullish_Abs':
        score += 30
    elif div == 'Bearish_Dist':
        score -= 50

    # C: Velocity — liquidity voids accelerate price
    close_price = row.get('close', row.get('Close', 0))
    is_in_void  = any(abs(close_price - v) < 0.05 for v in void_zones)
    if is_in_void:
        score += 20

    return score
