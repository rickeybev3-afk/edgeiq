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
