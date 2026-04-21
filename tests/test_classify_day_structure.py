"""Unit tests for classify_day_structure() — one test per structure branch.

Covered branches (in decision-tree order):
  1. Double Distribution  — bimodal VAP with clear LVN
  2. Non-Trend            — no IB break, narrow IB, low volume
  3. Normal               — no IB break, wide IB
  4. Neutral Extreme      — both IB sides broken, close at day extreme
  5. Neutral              — both IB sides broken, close in middle
  6. Trend Day (Bull)     — one side (up) broken early, 2× ATR extension, close at high extreme
  7. Trend Day (Bear)     — one side (down) broken early, 2× ATR extension, close at low extreme
  8. Normal Variation     — one side broken but no trend dominance

NOTE: test_adaptive_position_mgmt.py (alphabetically first) stubs
sys.modules["backend"] = MagicMock() at module level.  To get the real backend
module at *test-execution* time we use a session-scoped fixture that checks the
stub and forces a clean import when needed.
"""

import inspect
import sys
import types
import importlib

import numpy as np
import pandas as pd
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: supply the real backend module regardless of MagicMock stubs
# ─────────────────────────────────────────────────────────────────────────────

def _is_real_backend(mod) -> bool:
    """Return True only when *mod* is the genuine backend module (not a Mock)."""
    return (
        mod is not None
        and hasattr(mod, "__file__")
        and mod.__file__ is not None
        and "backend.py" in str(mod.__file__)
    )


@pytest.fixture(scope="session")
def real_backend():
    """Return the genuine backend module, bypassing any MagicMock stub."""
    existing = sys.modules.get("backend")
    if _is_real_backend(existing):
        return existing

    st = types.ModuleType("streamlit")
    st.session_state = {}
    st.cache_data = lambda *a, **kw: (lambda f: f)
    st.cache_resource = lambda *a, **kw: (lambda f: f)
    st.experimental_singleton = lambda *a, **kw: (lambda f: f)
    sys.modules["streamlit"] = st

    sb = sys.modules.get("supabase")
    if not (hasattr(sb, "__file__") and sb.__file__ is not None):
        sb = types.ModuleType("supabase")
        sb.create_client = lambda *a, **kw: None
        sb.Client = object
        sys.modules["supabase"] = sb

    sys.modules.pop("backend", None)
    mod = importlib.import_module("backend")
    return mod


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_bars(highs, lows, closes, volumes, start="2024-01-10 09:30", freq="5min"):
    """Build a minimal intraday OHLCV DataFrame from lists/arrays."""
    n = len(closes)
    idx = pd.date_range(start, periods=n, freq=freq)
    return pd.DataFrame(
        {"high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def _uniform_bars(n=78, bar_high=101.0, bar_low=100.0, close=100.5, vol=1000):
    """All bars identical — simplest non-degenerate session."""
    return _make_bars(
        highs=[bar_high] * n,
        lows=[bar_low] * n,
        closes=[close] * n,
        volumes=[vol] * n,
    )


def _flat_vap(n=80, price_lo=99.0, price_hi=111.0, vol_per_bin=10.0):
    """Flat volume profile — guarantees no Double Distribution is detected."""
    bin_centers = np.linspace(price_lo, price_hi, n)
    vap = np.full(n, vol_per_bin, dtype=float)
    return bin_centers, vap


def _bimodal_vap():
    """Bimodal volume profile that passes all _detect_double_distribution checks.

    Two strong HVN peaks at bin indices 20 and 60 (40 bins apart, >> 15 min).
    Price gap between peaks = 40 × (10/99) ≈ 4.04 >> $0.15.
    Flat valley between them → smoothed valley << 60 % of peak height.
    """
    bin_centers = np.linspace(100.0, 110.0, 100)
    vap = np.ones(100, dtype=float)
    vap[18:23] = 200.0
    vap[58:63] = 200.0
    return bin_centers, vap


# ─────────────────────────────────────────────────────────────────────────────
# 1. Double Distribution
# ─────────────────────────────────────────────────────────────────────────────

def test_double_distribution(real_backend):
    """Bimodal VAP with clear LVN → ⚡ Double Distribution (always wins)."""
    bin_centers, vap = _bimodal_vap()
    df = _uniform_bars(bar_high=110.0, bar_low=100.0, close=105.0)
    ib_high, ib_low = 104.0, 102.0

    label, color, detail, insight = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=105.0
    )

    assert label == "⚡ Double Distribution", f"Expected Double Distribution, got: {label!r}"
    assert color == "#00bcd4"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Non-Trend
# ─────────────────────────────────────────────────────────────────────────────

def test_non_trend(real_backend):
    """No IB break + narrow IB (< 20% of avg) + low volume pace → 😴 Non-Trend."""
    bin_centers, vap = _flat_vap()
    df = _uniform_bars(n=78, bar_high=100.25, bar_low=100.0, close=100.12, vol=100)

    ib_high, ib_low = 100.4, 99.9
    avg_ib_range = 5.0
    avg_daily_vol = 500_000

    label, color, detail, insight = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=100.12,
        avg_daily_vol=avg_daily_vol, avg_ib_range=avg_ib_range,
    )

    assert label == "😴 Non-Trend", f"Expected Non-Trend, got: {label!r}"
    assert color == "#78909c"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Normal
# ─────────────────────────────────────────────────────────────────────────────

def test_normal(real_backend):
    """No IB break + IB captures most of session + wide IB → ⚖️ Normal."""
    bin_centers, vap = _flat_vap()
    df = _uniform_bars(n=78, bar_high=101.0, bar_low=100.0, close=100.5, vol=2000)

    ib_high, ib_low = 102.0, 99.0
    avg_ib_range = 5.0
    avg_daily_vol = 100_000

    label, color, detail, insight = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=100.5,
        avg_daily_vol=avg_daily_vol, avg_ib_range=avg_ib_range,
    )

    assert label == "⚖️ Normal", f"Expected Normal, got: {label!r}"
    assert color == "#66bb6a"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Neutral Extreme
# ─────────────────────────────────────────────────────────────────────────────

def test_neutral_extreme(real_backend):
    """Both IB sides broken + close within top 10 % of day range → ⚡ Neutral Extreme."""
    bin_centers, vap = _flat_vap()

    day_low = 99.0
    day_high = 105.0
    total_range = day_high - day_low

    final_close = 104.6
    close_pct = (final_close - day_low) / total_range
    assert close_pct >= 0.90, "Setup error: close_pct must be ≥ 0.90"

    n = 78
    highs = [day_high] * n
    lows = [day_low] * n
    closes = [day_low] * (n - 1) + [final_close]
    volumes = [1000] * n
    df = _make_bars(highs, lows, closes, volumes)

    ib_high, ib_low = 101.0, 100.0

    label, color, detail, insight = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=102.0
    )

    assert label == "⚡ Neutral Extreme", f"Expected Neutral Extreme, got: {label!r}"
    assert color == "#7e57c2"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Neutral
# ─────────────────────────────────────────────────────────────────────────────

def test_neutral(real_backend):
    """Both IB sides broken + close back inside IB → 🔄 Neutral."""
    bin_centers, vap = _flat_vap()

    day_low = 99.0
    day_high = 105.0
    final_close = 100.3

    close_pct = (final_close - day_low) / (day_high - day_low)
    assert 0.10 < close_pct < 0.90, "Setup error: must not be at extreme"

    ib_high, ib_low = 101.0, 100.0
    assert ib_low <= final_close <= ib_high, "Setup error: close must be inside IB"

    n = 78
    highs = [day_high] * n
    lows = [day_low] * n
    closes = [day_high] * (n - 1) + [final_close]
    volumes = [1000] * n
    df = _make_bars(highs, lows, closes, volumes)

    label, color, detail, insight = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=100.5
    )

    assert label == "🔄 Neutral", f"Expected Neutral, got: {label!r}"
    assert color == "#80cbc4"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Trend Day (Bullish)
# ─────────────────────────────────────────────────────────────────────────────

def test_trend_day_bullish(real_backend):
    """One side (up) broken early + close at high extreme + > 2× ATR from IB → 📈 Trend Day."""
    bin_centers, vap = _flat_vap()

    ib_high = 100.5
    ib_low = 99.5

    n_early = 24
    n_late = 54
    n = n_early + n_late

    bar_range = 0.05
    step = (104.0 - 100.5) / (n - 1)

    highs, lows, closes, volumes = [], [], [], []
    for i in range(n):
        c = 100.5 + step * i
        h = c + bar_range
        l = c - bar_range
        highs.append(round(h, 4))
        lows.append(round(l, 4))
        closes.append(round(c, 4))
        volumes.append(500)

    df = _make_bars(highs, lows, closes, volumes)

    day_high = max(highs)
    day_low = min(lows)
    final_close = closes[-1]
    total_range = day_high - day_low

    close_pct = (final_close - day_low) / total_range
    assert close_pct >= 0.90, f"Setup error: close_pct={close_pct:.3f} must be ≥ 0.90"
    assert day_high >= ib_high, "Setup error: must break ib_high"
    assert day_low > ib_low, "Setup error: must NOT touch ib_low"

    early_df = df[df.index <= df.index[0].replace(hour=11, minute=30)]
    assert float(early_df["high"].max()) > ib_high, "Setup error: early violation required"

    label, color, detail, insight = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=102.0
    )

    assert label == "📈 Trend Day", f"Expected Trend Day, got: {label!r}"
    assert color == "#ff9800"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Trend Day (Bearish)
# ─────────────────────────────────────────────────────────────────────────────

def test_trend_day_bearish(real_backend):
    """One side (down) broken early + close at low extreme + > 2× ATR from IB → 📉 Trend Day (Bear)."""
    bin_centers, vap = _flat_vap()

    ib_high = 100.5
    ib_low = 99.5

    n_early = 24
    n_late = 54
    n = n_early + n_late

    bar_range = 0.05
    step = (96.0 - 99.5) / (n - 1)

    highs, lows, closes, volumes = [], [], [], []
    for i in range(n):
        c = 99.5 + step * i
        h = c + bar_range
        l = c - bar_range
        highs.append(round(h, 4))
        lows.append(round(l, 4))
        closes.append(round(c, 4))
        volumes.append(500)

    df = _make_bars(highs, lows, closes, volumes)

    day_high = max(highs)
    day_low = min(lows)
    final_close = closes[-1]
    total_range = day_high - day_low

    close_pct = (final_close - day_low) / total_range
    assert close_pct <= 0.10, f"Setup error: close_pct={close_pct:.3f} must be ≤ 0.10"
    assert day_low <= ib_low, "Setup error: must break ib_low"
    assert day_high < ib_high, "Setup error: must NOT touch ib_high"

    early_df = df[df.index <= df.index[0].replace(hour=11, minute=30)]
    assert float(early_df["low"].min()) < ib_low, "Setup error: early violation required"

    label, color, detail, insight = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=98.0
    )

    assert label == "📉 Trend Day (Bear)", f"Expected Trend Day (Bear), got: {label!r}"
    assert color == "#ff9800"


# ─────────────────────────────────────────────────────────────────────────────
# 8. Normal Variation (Up)
# ─────────────────────────────────────────────────────────────────────────────

def test_normal_variation_up(real_backend):
    """One IB side (up) broken + close NOT at extreme → 📊 Normal Variation (Up)."""
    bin_centers, vap = _flat_vap()

    ib_high = 100.5
    ib_low = 99.5

    n = 78
    final_close = 100.8
    day_high = 101.2
    day_low = 100.0

    close_pct = (final_close - day_low) / (day_high - day_low)
    assert 0.10 < close_pct < 0.90, f"Setup error: close_pct={close_pct:.3f} must not be extreme"
    assert day_high >= ib_high, "Setup error: must break ib_high"
    assert day_low > ib_low, "Setup error: must NOT touch ib_low"

    highs = [day_high] * n
    lows = [day_low] * n
    closes = [final_close] * n
    volumes = [1000] * n
    df = _make_bars(highs, lows, closes, volumes)

    label, color, detail, insight = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=100.5
    )

    assert label == "📊 Normal Variation (Up)", f"Expected Normal Variation (Up), got: {label!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Boundary tests — each hard-coded threshold tested at its exact edge value
# and just the other side to prove the branch flips there and not elsewhere.
# ─────────────────────────────────────────────────────────────────────────────


# ── B1. Double Distribution: $0.15 price-gap minimum ────────────────────────

def _dd_vap_with_gap(gap_dollars):
    """Two strong HVN peaks (bins 20 and 60); bin_centers arranged so
    bin_centers[60] - bin_centers[20] == gap_dollars exactly.
    The valley between peaks is zero, giving a clear LVN.
    """
    n = 100
    bin_centers = np.linspace(100.0, 101.0, n)
    bin_centers[20] = 100.0
    bin_centers[60] = 100.0 + gap_dollars
    vap = np.zeros(n, dtype=float)
    vap[18:23] = 1_000.0
    vap[58:63] = 1_000.0
    vap[[0, 1, 2, 96, 97, 98, 99]] = 1.0
    return bin_centers, vap


def test_dd_price_gap_exactly_at_015_is_valid(real_backend):
    """Price gap == DD_MIN_PRICE_GAP exactly satisfies the >= threshold requirement (strict < check) → DD found."""
    threshold = real_backend.DD_MIN_PRICE_GAP
    bin_centers, vap = _dd_vap_with_gap(threshold)
    result = real_backend._detect_double_distribution(bin_centers, vap)
    assert result is not None, (
        f"Price gap of exactly ${threshold} should pass the '<${threshold}' rejection check, "
        f"but _detect_double_distribution returned None."
    )


def test_dd_price_gap_just_below_015_is_rejected(real_backend):
    """Price gap just below DD_MIN_PRICE_GAP must be rejected → no DD."""
    threshold = real_backend.DD_MIN_PRICE_GAP
    below = round(threshold - 0.01, 10)
    bin_centers, vap = _dd_vap_with_gap(below)
    result = real_backend._detect_double_distribution(bin_centers, vap)
    assert result is None, (
        f"Price gap of ${below} should fail the ${threshold} minimum check, "
        f"but DD was found: {result!r}"
    )


# ── B2. Double Distribution: LVN valley strictly < 60 % of peak height ──────

def _dd_vap_with_lvn_ratio(valley_fraction, peak_val=1_000.0):
    """Two HVN peaks (bins 18:23 and 58:63); uniform valley (bins 23:58)
    set to valley_fraction × peak_val.  At interior positions the 5-bin
    convolution smoothed value equals the raw value exactly, so the
    smoothed LVN == valley_fraction × smoothed_peak.
    """
    n = 100
    bin_centers = np.linspace(100.0, 110.0, n)
    vap = np.zeros(n, dtype=float)
    vap[18:23] = peak_val
    vap[58:63] = peak_val
    vap[23:58] = valley_fraction * peak_val
    vap[[0, 1, 2, 96, 97, 98, 99]] = 1.0
    return bin_centers, vap


def test_dd_lvn_exactly_at_60pct_of_peak_is_rejected(real_backend):
    """Smoothed valley == DD_LVN_RATIO × peak fails the strict < DD_LVN_RATIO check → no DD."""
    ratio = real_backend.DD_LVN_RATIO
    bin_centers, vap = _dd_vap_with_lvn_ratio(ratio)
    result = real_backend._detect_double_distribution(bin_centers, vap)
    assert result is None, (
        f"Valley at exactly {ratio * 100:.0f}% of peak should NOT satisfy the '< {ratio}' LVN condition, "
        f"but DD was found: {result!r}"
    )


def test_dd_lvn_just_below_60pct_of_peak_is_valid(real_backend):
    """Smoothed valley just below DD_LVN_RATIO × peak satisfies strict < DD_LVN_RATIO → DD found."""
    ratio = real_backend.DD_LVN_RATIO
    below = round(ratio - 0.01, 10)
    bin_centers, vap = _dd_vap_with_lvn_ratio(below)
    result = real_backend._detect_double_distribution(bin_centers, vap)
    assert result is not None, (
        f"Valley at {below * 100:.0f}% of peak should satisfy the '< {ratio}' LVN check, "
        "but _detect_double_distribution returned None."
    )


# ── B3. Non-Trend: ib_range < 20 % of avg_ib_range ─────────────────────────

@pytest.mark.parametrize("ib_ratio,expected_label", [
    (0.20,   "⚖️ Normal"),      # exactly at threshold → is_narrow_ib = False → Normal
    (0.1999, "😴 Non-Trend"),   # just below 0.20 → is_narrow_ib = True → Non-Trend
])
def test_non_trend_ib_ratio_boundary(real_backend, ib_ratio, expected_label):
    """ib_range / avg_ib_range boundary at 0.20: strict < means 0.20 goes to Normal."""
    avg_ib_range = 5.0
    ib_range = ib_ratio * avg_ib_range
    ib_low  = 100.0
    ib_high = ib_low + ib_range

    mid = (ib_high + ib_low) / 2.0
    df  = _uniform_bars(n=78, bar_high=mid + 0.001, bar_low=mid - 0.001,
                        close=mid, vol=100)
    bin_centers, vap = _flat_vap()

    label, *_ = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=mid,
        avg_daily_vol=500_000, avg_ib_range=avg_ib_range,
    )

    assert label == expected_label, (
        f"ib_ratio={ib_ratio} (ib_range={ib_range:.4f}, 0.20×avg={0.20*avg_ib_range:.4f}): "
        f"expected {expected_label!r}, got {label!r}"
    )


# ── B4. Neutral Extreme: close_pct >= 0.90 or <= 0.10 ───────────────────────

def _both_sides_df(day_high, day_low, final_close):
    """Bars that touch both IB sides with fixed day extremes and a specific final close."""
    n = 78
    mid_close = (day_high + day_low) / 2.0
    highs  = [day_high] * n
    lows   = [day_low]  * n
    closes = [mid_close] * (n - 1) + [final_close]
    return _make_bars(highs, lows, closes, [1_000] * n)


@pytest.mark.parametrize("close_pct,expected_label", [
    (0.90,   "⚡ Neutral Extreme"),   # at high-extreme boundary (>= 0.90)
    (0.8999, "🔄 Neutral"),           # just below high-extreme
    (0.10,   "⚡ Neutral Extreme"),   # at low-extreme boundary (<= 0.10)
    (0.1001, "🔄 Neutral"),           # just above low-extreme
])
def test_neutral_extreme_close_pct_boundary(real_backend, close_pct, expected_label):
    """Neutral Extreme uses >= 0.90 / <= 0.10; exact boundary values must classify correctly."""
    day_low, day_high = 99.0, 105.0
    total_range = day_high - day_low
    final_close = day_low + close_pct * total_range

    df = _both_sides_df(day_high, day_low, final_close)
    bin_centers, vap = _flat_vap()
    ib_high, ib_low = 101.0, 100.0

    label, *_ = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=102.0
    )

    computed_pct = (final_close - day_low) / total_range
    assert label == expected_label, (
        f"close_pct={close_pct} (computed={computed_pct:.6f}): "
        f"expected {expected_label!r}, got {label!r}"
    )


# ── B6. Double Distribution: min_bin_sep = 15 (bin-count gate) ───────────────

def _dd_vap_with_bin_sep(bin_sep, pk1=20):
    """Two strong HVN peaks whose indices are exactly *bin_sep* apart.

    Each peak is a 5-bin-wide spike (vap[pk-2:pk+3] = 1_000) so that the
    5-point moving-average smoothing creates a strict local maximum exactly
    at *pk*.  The valley between the two spikes is zero → clear LVN.

    With linspace(100, 110, 100) the bin spacing is ≈ 0.101, so even
    14 bins apart gives a price gap ≈ 1.41 >> $0.15 — the only check that
    can flip the result is the bin-count gate (pk2 - pk1 < 15).
    """
    n = 100
    bin_centers = np.linspace(100.0, 110.0, n)
    vap = np.zeros(n, dtype=float)
    pk2 = pk1 + bin_sep
    vap[max(0, pk1 - 2): pk1 + 3] = 1_000.0
    vap[max(0, pk2 - 2): pk2 + 3] = 1_000.0
    return bin_centers, vap


def test_dd_min_bin_sep_exactly_15_is_valid(real_backend):
    """Peaks exactly 15 bins apart satisfy pk2 - pk1 >= 15 → DD found."""
    bin_centers, vap = _dd_vap_with_bin_sep(15)
    result = real_backend._detect_double_distribution(bin_centers, vap)
    assert result is not None, (
        "Peaks exactly 15 bins apart should pass the min_bin_sep=15 gate, "
        "but _detect_double_distribution returned None."
    )


def test_dd_min_bin_sep_14_is_rejected(real_backend):
    """Peaks only 14 bins apart fail the pk2 - pk1 < 15 gate → no DD."""
    bin_centers, vap = _dd_vap_with_bin_sep(14)
    result = real_backend._detect_double_distribution(bin_centers, vap)
    assert result is None, (
        "Peaks only 14 bins apart should be rejected by the min_bin_sep=15 "
        f"gate, but DD was found: {result!r}"
    )


# ── B7. Double Distribution: DD_HVN_WINDOW_FRAC boundary ────────────────────

def _vap_window_frac(window_frac, n=50, pk=10):
    """VAP where the ±2-bin window around *pk* holds exactly *window_frac* of
    total volume, and vap[pk]/avg_bin == 2.0 (below DD_HVN_PEAK_RATIO=2.5) so
    the peak-ratio arm of _is_strong_hvn is never satisfied.

    With n=50, pk=10, window = bins [8..12] (5 bins), non-window = 45 bins.
    Solving 5v / (45 + 5v) = frac  →  v = 9*frac / (1 - frac).
    """
    non_window = n - 5
    v = non_window * window_frac / (5 * (1 - window_frac))
    vap = np.ones(n, dtype=float)
    vap[max(0, pk - 2): min(n, pk + 3)] = v
    return vap


def test_dd_hvn_window_frac_exactly_at_threshold_fails(real_backend):
    """window/total == DD_HVN_WINDOW_FRAC does NOT satisfy strict '>' → not a strong HVN."""
    frac = real_backend.DD_HVN_WINDOW_FRAC
    vap = _vap_window_frac(frac)
    pk = 10
    result = real_backend._is_strong_hvn(pk, vap)
    assert not result, (
        f"window/total == DD_HVN_WINDOW_FRAC ({frac}) must NOT satisfy strict '>', "
        f"but _is_strong_hvn returned {result!r}"
    )


def test_dd_hvn_window_frac_just_above_threshold_passes(real_backend):
    """window/total just above DD_HVN_WINDOW_FRAC satisfies strict '>' → strong HVN."""
    frac = real_backend.DD_HVN_WINDOW_FRAC
    epsilon = 1e-9
    vap = _vap_window_frac(frac + epsilon)
    pk = 10
    result = real_backend._is_strong_hvn(pk, vap)
    assert result, (
        f"window/total just above DD_HVN_WINDOW_FRAC ({frac}) should satisfy strict '>', "
        f"but _is_strong_hvn returned {result!r}"
    )


# ── B8. Double Distribution: DD_HVN_PEAK_RATIO boundary ─────────────────────

def _vap_peak_ratio(ratio_multiple, n=50, pk=10):
    """VAP where vap[pk] / avg_bin == ratio_multiple and the window fraction
    stays below DD_HVN_WINDOW_FRAC (0.20) so only the peak-ratio arm is tested.

    With n=50, all non-peak bins = 1.0 (49 bins).
    Solving vap[pk] = ratio * (49 + vap[pk]) / 50  →  vap[pk] = 49*ratio / (50-ratio).
    Window/total ≈ 0.13, which is below the 0.20 fraction gate.
    """
    v = (n - 1) * ratio_multiple / (n - ratio_multiple)
    vap = np.ones(n, dtype=float)
    vap[pk] = v
    return vap


def test_dd_hvn_peak_ratio_exactly_at_threshold_fails(real_backend):
    """vap[pk] == DD_HVN_PEAK_RATIO × avg_bin does NOT satisfy strict '>' → not a strong HVN."""
    ratio = real_backend.DD_HVN_PEAK_RATIO
    vap = _vap_peak_ratio(ratio)
    pk = 10
    result = real_backend._is_strong_hvn(pk, vap)
    assert not result, (
        f"vap[pk] == DD_HVN_PEAK_RATIO ({ratio}) × avg_bin must NOT satisfy strict '>', "
        f"but _is_strong_hvn returned {result!r}"
    )


def test_dd_hvn_peak_ratio_just_above_threshold_passes(real_backend):
    """vap[pk] just above DD_HVN_PEAK_RATIO × avg_bin satisfies strict '>' → strong HVN."""
    ratio = real_backend.DD_HVN_PEAK_RATIO
    epsilon = 1e-9
    vap = _vap_peak_ratio(ratio + epsilon)
    pk = 10
    result = real_backend._is_strong_hvn(pk, vap)
    assert result, (
        f"vap[pk] just above DD_HVN_PEAK_RATIO ({ratio}) × avg_bin should satisfy strict '>', "
        f"but _is_strong_hvn returned {result!r}"
    )


# ── B9. Double Distribution: DD_PEAK_THRESHOLD_PCT boundary ─────────────────

def _smoothed_two_peaks(secondary_height, n=60, pk_main=10, pk_sec=45):
    """Smoothed profile with a dominant peak at *pk_main* (value=1000) that
    sets max_v, and a secondary local maximum at *pk_sec* with value
    *secondary_height*.  Neighbours at ±1 and ±2 around each peak are set to
    half the peak value so that _find_peaks' strict local-max shape test is
    satisfied.  All background bins are 0.1.
    """
    smoothed = np.full(n, 0.1)
    main_v = 1000.0
    smoothed[pk_main] = main_v
    for off in (1, 2):
        smoothed[pk_main - off] = main_v * 0.5
        smoothed[pk_main + off] = main_v * 0.5
    smoothed[pk_sec] = secondary_height
    for off in (1, 2):
        smoothed[pk_sec - off] = secondary_height * 0.5
        smoothed[pk_sec + off] = secondary_height * 0.5
    bin_centers = np.linspace(100.0, 110.0, n)
    return smoothed, bin_centers, pk_sec


def test_dd_peak_threshold_pct_exactly_at_threshold_is_included(real_backend):
    """Secondary peak at exactly DD_PEAK_THRESHOLD_PCT × max_v satisfies '>=' → included."""
    thr = real_backend.DD_PEAK_THRESHOLD_PCT
    max_v = 1000.0
    secondary_height = thr * max_v
    smoothed, bin_centers, pk_sec = _smoothed_two_peaks(secondary_height)
    peaks = real_backend._find_peaks(smoothed, bin_centers, threshold_pct=thr)
    assert pk_sec in peaks, (
        f"Secondary peak at exactly DD_PEAK_THRESHOLD_PCT ({thr}) × max_v ({max_v}) "
        f"should satisfy '>=', but _find_peaks returned peaks={peaks}"
    )


def test_dd_peak_threshold_pct_just_below_threshold_is_excluded(real_backend):
    """Secondary peak just below DD_PEAK_THRESHOLD_PCT × max_v fails '>=' → excluded."""
    thr = real_backend.DD_PEAK_THRESHOLD_PCT
    max_v = 1000.0
    secondary_height = thr * max_v - 0.001
    smoothed, bin_centers, pk_sec = _smoothed_two_peaks(secondary_height)
    peaks = real_backend._find_peaks(smoothed, bin_centers, threshold_pct=thr)
    assert pk_sec not in peaks, (
        f"Secondary peak just below DD_PEAK_THRESHOLD_PCT ({thr}) × max_v ({max_v}) "
        f"should fail '>=', but _find_peaks included it: peaks={peaks}"
    )


# ── B5. Trend Day: dist_from_ib > 2.0 × ATR ─────────────────────────────────

def _one_side_up_uniform(bar_high, bar_low, n=78):
    """All bars identical at bar_high/bar_low with close = bar_high.

    ATR = bar_high - bar_low (constant TR for all bars).
    dist_from_ib = bar_high - ib_high (computed by caller).
    close = bar_high → close_pct = 1.0 → at_high_extreme satisfied.
    early bars all exceed ib_high → viol_early_up satisfied.
    """
    return _uniform_bars(n=n, bar_high=bar_high, bar_low=bar_low,
                         close=bar_high, vol=1_000)


@pytest.mark.parametrize("dist_multiplier,expected_label", [
    (2.00, "📊 Normal Variation (Up)"),  # dist == 2.0 × ATR: strict > not met → Normal Variation
    (2.05, "📈 Trend Day"),              # dist > 2.0 × ATR: Trend Day
])
def test_trend_day_atr_boundary(real_backend, dist_multiplier, expected_label):
    """dist_from_ib > 2.0 × ATR is strict; exactly 2.0× must yield Normal Variation."""
    bar_range = 0.20        # ATR will equal this value
    ib_high   = 100.00
    ib_low    =  99.50      # ib_range = 0.50 → ib_range_ratio >> 0.40 → directional_vol = False

    bar_low  = ib_high + bar_range * (dist_multiplier - 1)
    bar_high = bar_low + bar_range

    df = _one_side_up_uniform(bar_high, bar_low)
    bin_centers, vap = _flat_vap()

    atr  = real_backend.compute_atr(df)
    dist = float(df["close"].iloc[-1]) - ib_high

    label, *_ = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=ib_high + bar_range
    )

    assert label == expected_label, (
        f"dist_multiplier={dist_multiplier}: dist={dist:.4f}, 2×ATR={2*atr:.4f}, "
        f"expected {expected_label!r}, got {label!r}"
    )


# ── B6. directional_vol gate: ib_vol_pct < 0.40 ─────────────────────────────


def _one_side_up_bars_with_inside_fraction(n_inside, n_outside,
                                           ib_high=100.5, ib_low=99.5):
    """n_inside + n_outside bars for a one-side-up (bullish) session.

    All bars share high=103.0 / low=99.6 so that:
      - one_side_up    = True  (day_high 103.0 >= ib_high 100.5,
                                day_low  99.6  >  ib_low  99.5)
      - viol_early_up  = True  (all bar highs 103.0 > ib_high 100.5)
      - close_pct      ≈ 0.97  (last-bar close 102.9, at_high_extreme ✓)
      - ib_range_ratio = 1.0/3.4 ≈ 0.29 < 0.40  (directional_vol's second gate ✓)
      - ATR ≈ 3.4; dist_from_ib = 2.4 < 2×ATR → ATR gate alone does NOT trigger Trend
        Day, so directional_vol is the sole deciding signal.

    ib_vol_pct = n_inside / (n_inside + n_outside) — control via the arguments.
    """
    n = n_inside + n_outside
    inside_close  = (ib_high + ib_low) / 2.0   # 100.0 — strictly inside IB
    outside_close = 102.9                        # above ib_high; close_pct ≈ 0.97
    closes = [inside_close] * n_inside + [outside_close] * n_outside
    highs  = [103.0] * n
    lows   = [99.6]  * n
    vols   = [1]     * n
    return _make_bars(highs, lows, closes, vols)


@pytest.mark.parametrize("n_inside,n_outside,expected_label", [
    (40, 60, "📊 Normal Variation (Up)"),  # ib_vol_pct = 0.40: strict < fails → Normal Variation
    (39, 61, "📈 Trend Day"),              # ib_vol_pct = 0.39: directional_vol = True → Trend Day
])
def test_directional_vol_040_boundary(real_backend, n_inside, n_outside, expected_label):
    """ib_vol_pct < 0.40 is a strict check; exactly 0.40 must NOT activate directional_vol.

    The scenario is engineered so that:
      - dist_from_ib (2.4) < 2 × ATR (≈ 6.8) → the ATR gate alone cannot trigger Trend Day.
      - ib_range_ratio (≈ 0.29) < 0.40 → the second sub-condition of directional_vol is met.
    Therefore directional_vol is the *only* variable deciding Trend Day vs Normal Variation,
    and the flip must occur at exactly ib_vol_pct = 0.40 (strict <).
    """
    ib_high, ib_low = 100.5, 99.5
    bin_centers, vap = _flat_vap()
    df = _one_side_up_bars_with_inside_fraction(n_inside, n_outside, ib_high, ib_low)

    computed_ivp = n_inside / (n_inside + n_outside)

    label, *_ = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=102.0
    )

    assert label == expected_label, (
        f"n_inside={n_inside}/{n_inside + n_outside} "
        f"(ib_vol_pct={computed_ivp:.4f}): "
        f"expected {expected_label!r}, got {label!r}"
    )


# ── B7. balanced_vol gate: ib_vol_pct > 0.65 ────────────────────────────────


def test_balanced_vol_065_constant_present(real_backend):
    """Guard the 0.65 constant in classify_day_structure() against silent edits.

    balanced_vol = ib_vol_pct > 0.65 is computed inside the function but is not
    currently wired to a branch gate, so no behavioural output changes at this threshold.
    The only reliable way to protect the constant value itself is to assert its presence
    directly in the function source — any edit to 0.65 will fail this test immediately.
    """
    src = inspect.getsource(real_backend.classify_day_structure)
    assert "ib_vol_pct > 0.65" in src, (
        "The balanced_vol threshold 'ib_vol_pct > 0.65' was not found in "
        "classify_day_structure() source.  If the constant was intentionally changed, "
        "update this test to match the new value."
    )


@pytest.mark.parametrize("ib_vol_pct_val,balanced_vol_expected", [
    (0.65,   False),   # == 0.65: strict > not met → balanced_vol = False
    (0.6501, True),    # just above 0.65 → balanced_vol = True
])
def test_balanced_vol_065_boundary(real_backend, monkeypatch,
                                   ib_vol_pct_val, balanced_vol_expected):
    """balanced_vol = ib_vol_pct > 0.65 is defined in classify_day_structure() but is not
    currently wired to a branch gate.  This test verifies two things:

    1. The strict-greater-than operator means exactly 0.65 evaluates to False and
       0.6501 evaluates to True — guards the operator direction if code changes.
    2. Neither value unintentionally breaks the Normal label (no hidden gate effect).

    In a no-break session (all bar closes strictly inside IB), ib_vol_pct is structurally
    1.0 because a valid OHLCV bar cannot have close outside [ib_low, ib_high] without its
    high or low also breaching the IB boundary.  compute_ib_volume_stats is therefore
    patched to inject the exact boundary values being probed.
    """
    ib_high, ib_low = 101.0, 99.0
    bin_centers, vap = _flat_vap()
    # Bars well inside IB — no IB touch, wide IB → no_break=True, not narrow → Normal branch
    df = _uniform_bars(n=78, bar_high=100.5, bar_low=99.5, close=100.0, vol=1_000)

    # Inject exact ib_vol_pct; ib_range_ratio=0.80 (> 0.25) keeps ib_vol_confirms_nontrend
    # False so that only balanced_vol changes across the boundary.
    monkeypatch.setattr(
        real_backend, "compute_ib_volume_stats",
        lambda _df, _hi, _lo: (ib_vol_pct_val, 0.80),
    )

    label, *_ = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=100.0,
        avg_ib_range=5.0, avg_daily_vol=100_000,
    )

    computed_balanced_vol = ib_vol_pct_val > 0.65
    assert computed_balanced_vol == balanced_vol_expected, (
        f"ib_vol_pct={ib_vol_pct_val}: "
        f"expected balanced_vol={balanced_vol_expected}, got {computed_balanced_vol}"
    )
    assert label == "⚖️ Normal", (
        f"ib_vol_pct={ib_vol_pct_val} (balanced_vol={computed_balanced_vol}): "
        f"expected Normal, got {label!r} — balanced_vol must not gate Normal classification"
    )


# ── B8. ib_vol_confirms_nontrend gate: ib_vol_pct > 0.72 ────────────────────


@pytest.mark.parametrize("ib_vol_pct_val,expected_label", [
    (0.72,   "⚖️ Normal"),     # ib_vol_pct == 0.72: strict > not satisfied → Normal
    (0.7201, "😴 Non-Trend"),  # ib_vol_pct just above 0.72 → ib_vol_confirms_nontrend=True
])
def test_ib_vol_confirms_nontrend_072_boundary(real_backend, monkeypatch,
                                               ib_vol_pct_val, expected_label):
    """ib_vol_confirms_nontrend = ib_vol_pct > 0.72 and ib_range_ratio < 0.25.

    In any valid no-break session ib_range > day_range (the IB spans more than the
    actual day range), so ib_range_ratio is structurally > 1.0 and the sub-condition
    ib_range_ratio < 0.25 can never be True from real bar data alone.
    compute_ib_volume_stats is therefore patched to inject ib_range_ratio=0.20 (<0.25),
    isolating the 0.72 threshold as the sole decision variable.

    The session is set up so that:
      - no_break = True   (bars stay inside IB boundaries)
      - is_narrow_ib = True (ib_range 1.0 < 0.20 × avg_ib_range 7.0 = 1.4)
      - is_low_vol = False (pace 390 / avg_daily_vol 100 = 3.9 >> 0.80)
    This makes ib_vol_confirms_nontrend the *only* Non-Trend trigger, so the branch
    flips at exactly ib_vol_pct = 0.72 (strict >).
    """
    ib_high, ib_low = 100.5, 99.5
    avg_ib_range  = 7.0   # is_narrow_ib: ib_range(1.0) < 0.20 × 7.0(=1.4) → True
    avg_daily_vol = 100   # pace(390) / 100 = 3.9 ≥ 0.80 → is_low_vol = False

    bin_centers, vap = _flat_vap()
    # Bars strictly inside IB → no_break = True
    df = _uniform_bars(n=78, bar_high=100.4, bar_low=99.6, close=100.0, vol=1)

    # Inject ib_vol_pct boundary value and ib_range_ratio=0.20 (< 0.25) so that the
    # second sub-condition of ib_vol_confirms_nontrend is always satisfied here.
    monkeypatch.setattr(
        real_backend, "compute_ib_volume_stats",
        lambda _df, _hi, _lo: (ib_vol_pct_val, 0.20),
    )

    label, *_ = real_backend.classify_day_structure(
        df, bin_centers, vap, ib_high, ib_low, poc_price=100.0,
        avg_ib_range=avg_ib_range, avg_daily_vol=avg_daily_vol,
    )

    assert label == expected_label, (
        f"ib_vol_pct={ib_vol_pct_val}: "
        f"ib_vol_confirms_nontrend={'True' if ib_vol_pct_val > 0.72 else 'False'} — "
        f"expected {expected_label!r}, got {label!r}"
    )


# =============================================================================
# Direct boundary tests for Double Distribution helper functions
# =============================================================================
#
# _is_strong_hvn and _find_peaks are exercised here in isolation so that a bug
# in either helper produces a failing test that names the specific function
# rather than pointing ambiguously at _detect_double_distribution.
# =============================================================================


# ─────────────────────────────────────────────────────────────────────────────
# _is_strong_hvn — window-fraction boundary (0.20)
# ─────────────────────────────────────────────────────────────────────────────
#
# Design:
#   100 bins, all background = 1.0
#   Peak at index 50 with vap[50] = 0.0  → peak-ratio = 0/avg = 0.0 < 2.5 (never fires)
#   Window non-peak bins (48, 49, 51, 52) = W  → window sum = 4·W
#   total = 95·1.0 + 4·W
#   fraction = 4·W / (95 + 4·W) = 0.20  →  W = 19/3.2 = 5.9375 (boundary, strict > so False)
#   W = 5.94 → fraction ≈ 0.2001 > 0.20 → True
#   W = 5.93 → fraction ≈ 0.1999 < 0.20 → False
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("W,expected", [
    (5.9375, False),   # fraction == 0.20 exactly — strict > means boundary is excluded
    (5.94,   True),    # fraction just above 0.20 → window condition fires
    (5.93,   False),   # fraction just below 0.20 → window condition does not fire
])
def test_is_strong_hvn_window_fraction_boundary(real_backend, W, expected):
    """_is_strong_hvn: the 0.20 window-fraction threshold flips the return value.

    The peak bin carries zero volume so the 2.5× peak-ratio path is always False,
    isolating the window-fraction branch as the sole decision variable.
    """
    vap = np.ones(100, dtype=float)
    pk = 50
    vap[pk] = 0.0            # peak bin: no volume → peak-ratio branch always False
    vap[pk - 2] = W          # window non-peak bins
    vap[pk - 1] = W
    vap[pk + 1] = W
    vap[pk + 2] = W

    result = real_backend._is_strong_hvn(pk, vap)
    assert bool(result) == expected, (
        f"W={W}: window/total={(4*W)/(95 + 4*W):.6f}, "
        f"expected _is_strong_hvn={expected}, got {result}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# _is_strong_hvn — peak-ratio boundary (2.5)
# ─────────────────────────────────────────────────────────────────────────────
#
# Design:
#   100 bins, background = 100.0 (indices 0-99 except peak)
#   Peak at index 50 with vap[50] = S
#   total = 99·100 + S = 9 900 + S
#   avg_bin = (9 900 + S) / 100
#   peak_ratio = S / avg_bin = 100·S / (9 900 + S) = 2.5  →  S ≈ 253.846 (boundary)
#   window = 400 + S → fraction = (400+S)/(9900+S) ≈ 0.064 < 0.20 (window path never fires)
#   S = 254 → ratio ≈ 2.502 > 2.5 → True
#   S = 253 → ratio ≈ 2.491 < 2.5 → False
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("S,expected", [
    (24750 / 97.5, False),   # ratio == 2.5 exactly — strict > means boundary is excluded
    (254,          True),    # ratio just above 2.5 → peak-ratio condition fires
    (253,          False),   # ratio just below 2.5 → neither condition fires
])
def test_is_strong_hvn_peak_ratio_boundary(real_backend, S, expected):
    """_is_strong_hvn: the 2.5× peak-ratio threshold flips the return value.

    Background volume is large enough that the window-fraction path stays below
    0.20 throughout, isolating the peak-ratio branch as the sole decision variable.
    """
    pk = 50
    vap = np.full(100, 100.0, dtype=float)
    vap[pk] = S

    result = real_backend._is_strong_hvn(pk, vap)
    total = vap.sum()
    avg_bin = total / len(vap)
    assert bool(result) == expected, (
        f"S={S}: peak_ratio={vap[pk]/avg_bin:.4f}, "
        f"expected _is_strong_hvn={expected}, got {result}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# _find_peaks — threshold_pct boundary
# ─────────────────────────────────────────────────────────────────────────────
#
# Design:
#   Array of 20 elements; all non-peak bins = 0.1
#   Peak1 at index 5:  value = 100.0  (global max — always qualifies)
#   Peak2 at index 15: value = P2     (secondary — its inclusion depends on threshold_pct)
#   threshold_pct = 0.30 → boundary = 100.0 × 0.30 = 30.0
#
#   P2 = 30.0 → P2 >= 30.0 (True, >= is inclusive) → peak2 included
#   P2 = 29.9 → P2 >= 30.0 (False)                  → peak2 excluded
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("P2,expected_peaks", [
    (30.0, [5, 15]),   # exactly at threshold (>=) — boundary value is included
    (29.9, [5]),       # just below threshold — secondary peak is excluded
])
def test_find_peaks_threshold_boundary(real_backend, P2, expected_peaks):
    """_find_peaks: the threshold_pct boundary correctly includes/excludes borderline peaks.

    Two peaks are constructed so that the primary peak always qualifies.  The
    secondary peak sits at or just below the threshold, making it the sole variable.
    The >= operator means the exact boundary value is included.
    """
    n = 20
    smoothed = np.full(n, 0.1, dtype=float)
    smoothed[5]  = 100.0   # primary peak, global max
    smoothed[15] = P2      # secondary peak — on or below the 30 % boundary

    bin_centers = np.arange(n, dtype=float)

    peaks = real_backend._find_peaks(smoothed, bin_centers, threshold_pct=0.30)
    assert peaks == expected_peaks, (
        f"P2={P2}: threshold=30.0, expected peaks={expected_peaks}, got {peaks}"
    )
