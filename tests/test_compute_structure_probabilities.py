"""Unit tests for compute_structure_probabilities() — one test per major branch.

Covered branches (mirrors the IB-interaction decision tree in backend.py):
  1. Fallback       — zero total range or zero IB range returns flat distribution
  2. Normal         — no IB break, high pct_inside, balanced volume
  3. Ntrl Extreme   — both IB sides broken, close at day extreme
  4. Neutral        — both IB sides broken, close in middle
  5. Trend          — one side broken early, close at extreme (> 2× ATR extension)
  6. Nrml Var       — one side broken, close not at extreme, no early violation
  7. Dbl Dist       — bimodal VAP triggers double-distribution regardless of IB state

All tests assert:
  • scores sum to 100 (within floating-point rounding tolerance)
  • the highest-scoring label matches the expected structure
"""

import sys
import types
import importlib

import numpy as np
import pandas as pd
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: supply the real backend module regardless of MagicMock stubs
# (same pattern used by test_classify_day_structure.py)
# ─────────────────────────────────────────────────────────────────────────────

def _is_real_backend(mod) -> bool:
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
# Helpers (same as test_classify_day_structure.py)
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
    """Bimodal VAP that passes _detect_double_distribution checks.

    Two strong HVN peaks at bin indices 20 and 60 (40 bins apart >> 15 min gap).
    """
    bin_centers = np.linspace(100.0, 110.0, 100)
    vap = np.ones(100, dtype=float)
    vap[18:23] = 200.0
    vap[58:63] = 200.0
    return bin_centers, vap


def _top_label(scores: dict) -> str:
    """Return the key with the highest score."""
    return max(scores, key=lambda k: scores[k])


def _assert_sums_to_100(scores: dict):
    total = sum(scores.values())
    assert abs(total - 100.0) < 0.5, f"Scores sum to {total:.2f}, expected ~100"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Fallback — degenerate input (zero total range)
# ─────────────────────────────────────────────────────────────────────────────

def test_fallback_zero_range(real_backend):
    """All bars at the same price → total_range == 0 → flat fallback distribution."""
    bin_centers, vap = _flat_vap()
    df = _uniform_bars(n=78, bar_high=100.0, bar_low=100.0, close=100.0)

    scores = real_backend.compute_structure_probabilities(
        df, bin_centers, vap, ib_high=100.5, ib_low=99.5, poc_price=100.0
    )

    assert set(scores.keys()) == {
        "Non-Trend", "Normal", "Trend", "Ntrl Extreme", "Neutral", "Nrml Var", "Dbl Dist"
    }
    _assert_sums_to_100(scores)


def test_fallback_zero_ib_range(real_backend):
    """IB high == IB low → ib_range == 0 → flat fallback distribution."""
    bin_centers, vap = _flat_vap()
    df = _uniform_bars(n=78, bar_high=101.0, bar_low=100.0, close=100.5)

    scores = real_backend.compute_structure_probabilities(
        df, bin_centers, vap, ib_high=100.5, ib_low=100.5, poc_price=100.5
    )

    _assert_sums_to_100(scores)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Normal — no IB break, wide IB, all closes inside IB
# ─────────────────────────────────────────────────────────────────────────────

def test_normal_wins_on_no_break(real_backend):
    """No IB break + all closes inside wide IB → Normal should score highest."""
    bin_centers, vap = _flat_vap()
    # Day range stays well inside IB; IB is wider than the day
    df = _uniform_bars(n=78, bar_high=101.0, bar_low=100.0, close=100.5, vol=2000)
    ib_high, ib_low = 102.0, 99.0

    assert df["high"].max() < ib_high, "Setup: must not break ib_high"
    assert df["low"].min() > ib_low,   "Setup: must not break ib_low"

    scores = real_backend.compute_structure_probabilities(
        df, bin_centers, vap, ib_high=ib_high, ib_low=ib_low, poc_price=100.5
    )

    _assert_sums_to_100(scores)
    assert _top_label(scores) == "Normal", (
        f"Expected Normal to lead; scores={scores}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Neutral Extreme — both IB sides broken, close at top of day range
# ─────────────────────────────────────────────────────────────────────────────

def test_ntrl_extreme_wins_when_both_hit_and_close_at_extreme(real_backend):
    """Both IB sides broken + close in top 10 % of range → Ntrl Extreme leads."""
    bin_centers, vap = _flat_vap()

    day_low, day_high = 99.0, 105.0
    final_close = 104.6  # top 10 % of range
    close_pct = (final_close - day_low) / (day_high - day_low)
    assert close_pct >= 0.90, f"Setup: close_pct={close_pct:.3f} must be ≥ 0.90"

    ib_high, ib_low = 101.0, 100.0
    assert day_high > ib_high and day_low < ib_low, "Setup: both sides must be broken"

    n = 78
    df = _make_bars(
        highs=[day_high] * n,
        lows=[day_low] * n,
        closes=[day_low] * (n - 1) + [final_close],
        volumes=[1000] * n,
    )

    scores = real_backend.compute_structure_probabilities(
        df, bin_centers, vap, ib_high=ib_high, ib_low=ib_low, poc_price=102.0
    )

    _assert_sums_to_100(scores)
    assert _top_label(scores) == "Ntrl Extreme", (
        f"Expected Ntrl Extreme to lead; scores={scores}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Neutral — both IB sides broken, close in middle of range
# ─────────────────────────────────────────────────────────────────────────────

def test_neutral_wins_when_both_hit_and_close_in_middle(real_backend):
    """Both IB sides broken + close between 10 %–90 % of range → Neutral leads."""
    bin_centers, vap = _flat_vap()

    day_low, day_high = 99.0, 105.0
    final_close = 102.0  # middle of range
    close_pct = (final_close - day_low) / (day_high - day_low)
    assert 0.10 < close_pct < 0.90, f"Setup: close_pct={close_pct:.3f} must not be extreme"

    ib_high, ib_low = 101.0, 100.0
    assert day_high > ib_high and day_low < ib_low, "Setup: both sides must be broken"

    n = 78
    df = _make_bars(
        highs=[day_high] * n,
        lows=[day_low] * n,
        closes=[day_high] * (n - 1) + [final_close],
        volumes=[1000] * n,
    )

    scores = real_backend.compute_structure_probabilities(
        df, bin_centers, vap, ib_high=ib_high, ib_low=ib_low, poc_price=100.5
    )

    _assert_sums_to_100(scores)
    assert _top_label(scores) == "Neutral", (
        f"Expected Neutral to lead; scores={scores}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Trend — one side broken early, close at extreme, strong ATR extension
# ─────────────────────────────────────────────────────────────────────────────

def test_trend_wins_on_early_break_and_close_at_extreme(real_backend):
    """One IB side (upside) broken early + close at top extreme → Trend leads."""
    bin_centers, vap = _flat_vap()

    ib_high, ib_low = 100.5, 99.5
    n_early = 24    # bars before 11:30 cutoff
    n_late = 54
    n = n_early + n_late

    step = (104.0 - 100.5) / (n - 1)
    bar_range = 0.05
    highs, lows, closes, volumes = [], [], [], []
    for i in range(n):
        c = 100.5 + step * i
        highs.append(round(c + bar_range, 4))
        lows.append(round(c - bar_range, 4))
        closes.append(round(c, 4))
        volumes.append(500)

    df = _make_bars(highs, lows, closes, volumes)

    day_high, day_low = max(highs), min(lows)
    close_pct = (closes[-1] - day_low) / (day_high - day_low)
    assert close_pct >= 0.90, f"Setup: close_pct={close_pct:.3f} must be ≥ 0.90"
    assert day_high > ib_high, "Setup: must break ib_high"
    assert day_low > ib_low,   "Setup: must NOT touch ib_low"

    early_df = df[df.index <= df.index[0].replace(hour=11, minute=30)]
    assert float(early_df["high"].max()) > ib_high, "Setup: early violation required"

    scores = real_backend.compute_structure_probabilities(
        df, bin_centers, vap, ib_high=ib_high, ib_low=ib_low, poc_price=102.0
    )

    _assert_sums_to_100(scores)
    assert _top_label(scores) == "Trend", (
        f"Expected Trend to lead; scores={scores}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. Normal Variation — one side broken, close not at extreme, no early violation
# ─────────────────────────────────────────────────────────────────────────────

def test_nrml_var_wins_on_one_side_no_trend(real_backend):
    """One IB side broken + close not at extreme + no early violation → Nrml Var leads."""
    bin_centers, vap = _flat_vap()

    ib_high, ib_low = 100.5, 99.5
    day_high, day_low = 101.2, 100.0
    final_close = 100.8  # ~65 % of range — not extreme

    close_pct = (final_close - day_low) / (day_high - day_low)
    assert 0.10 < close_pct < 0.90, f"Setup: close_pct={close_pct:.3f} must not be extreme"
    assert day_high > ib_high, "Setup: must break ib_high"
    assert day_low > ib_low,   "Setup: must NOT touch ib_low"

    n = 78
    # Bars from 09:30 to 11:30 (the 2-hour "early" window used by the scorer)
    # must stay at or below ib_high so viol_early stays False.
    # start="2024-01-10 09:30", freq="5min": bar 0=09:30, bar 24=11:30.
    # We keep bars 0-24 (25 bars) just below ib_high and let bars 25+ breach it.
    n_early = 25   # covers the full early window including 11:30
    highs   = [100.4] * n_early + [day_high] * (n - n_early)
    lows    = [day_low] * n
    closes  = [final_close] * n
    volumes = [1000] * n
    df = _make_bars(highs, lows, closes, volumes)

    # Confirm no early IB violation
    two_hr_end = df.index[0].replace(hour=11, minute=30)
    early_df = df[df.index <= two_hr_end]
    assert float(early_df["high"].max()) <= ib_high, (
        "Setup: early bars must not exceed ib_high"
    )

    scores = real_backend.compute_structure_probabilities(
        df, bin_centers, vap, ib_high=ib_high, ib_low=ib_low, poc_price=100.5
    )

    _assert_sums_to_100(scores)
    assert _top_label(scores) == "Nrml Var", (
        f"Expected Nrml Var to lead; scores={scores}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. Double Distribution — bimodal VAP overrides IB-interaction bucket
# ─────────────────────────────────────────────────────────────────────────────

def test_dbl_dist_wins_with_bimodal_vap(real_backend):
    """Bimodal VAP triggers Dbl Dist score (70) regardless of IB interaction."""
    bin_centers, vap = _bimodal_vap()

    # Use a one-side-broken scenario so the Dbl Dist branch (one_side path) fires
    ib_high, ib_low = 104.0, 102.0
    day_high = 110.0
    day_low  = 102.5   # strictly above ib_low → only the high side is broken
    final_close = 106.0  # middle of range — not at extreme

    n = 78
    df = _make_bars(
        highs=[day_high] * n,
        lows=[day_low] * n,
        closes=[final_close] * n,
        volumes=[1000] * n,
    )

    assert day_high > ib_high, "Setup: must break ib_high"
    assert day_low > ib_low,   "Setup: must NOT touch ib_low"

    scores = real_backend.compute_structure_probabilities(
        df, bin_centers, vap, ib_high=ib_high, ib_low=ib_low, poc_price=105.0
    )

    _assert_sums_to_100(scores)
    assert _top_label(scores) == "Dbl Dist", (
        f"Expected Dbl Dist to lead; scores={scores}"
    )


def test_dbl_dist_wins_on_both_hit_day(real_backend):
    """Bimodal VAP also scores Dbl Dist high on both-IB-sides-broken days."""
    bin_centers, vap = _bimodal_vap()

    ib_high, ib_low = 104.0, 102.0
    day_high, day_low = 110.0, 99.0   # breaks both sides
    final_close = 105.0               # middle — not at extreme

    n = 78
    df = _make_bars(
        highs=[day_high] * n,
        lows=[day_low] * n,
        closes=[final_close] * n,
        volumes=[1000] * n,
    )

    assert day_high > ib_high and day_low < ib_low, "Setup: both sides must be broken"

    scores = real_backend.compute_structure_probabilities(
        df, bin_centers, vap, ib_high=ib_high, ib_low=ib_low, poc_price=105.0
    )

    _assert_sums_to_100(scores)
    assert _top_label(scores) == "Dbl Dist", (
        f"Expected Dbl Dist to lead; scores={scores}"
    )
