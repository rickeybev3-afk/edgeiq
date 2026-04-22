"""Unit tests for squeeze-specific win/loss and R-value calculations.

Covers six bug-fixes / behaviour paths in the squeeze trade pipeline:

  1. Squeeze win/loss price override (_squeeze_win_loss_override in
     backfill_pending_sim_rows.py):
     — Bullish squeeze: close <= ib_low  → "Loss"
     — Bearish squeeze: close >= ib_high → "Loss"
     — Close between stop and target keeps the existing win_loss unchanged.

  2. tiered_pnl_r sentinel fix (_squeeze_tiered_sentinel in
     run_tiered_pnl_backfill.py):
     — Squeeze row with tiered<0 and eod>0 → tiered replaced by eod value.
     — Non-squeeze row with same values  → tiered stays negative (fix is squeeze-only).

  3. fix_squeeze_data._squeeze_win_loss() helper
     — Parametrized table over (predicted_direction, close, ib_high, ib_low,
       existing_wl) → expected return value (new win_loss or None = no change).

  4. Bearish pnl_r_sim direction fix (fix_squeeze_data.fix_backtest_sim_runs Bug #3):
     — Overriding actual_outcome to predicted before calling compute_trade_sim
       ensures bearish squeeze rows use the correct short-trade directional formula.
     — Bearish stopped-out trade (close > ib_high) → pnl_r_sim < 0.
     — Without the fix (wrong actual_outcome) the same row gives pnl_r_sim > 0.

  5. v6 intraday MFE/MAE path coverage for compute_trade_sim:
     — Bearish v6: mfe >= target_r → trailing_exit with captured_r = mfe - 1.0R > 0.
     — Bearish v6: mae >= 1.0 → stopped_out with pnl_r_sim = -1.0.
     — Bearish v6: tight_trail_exit when nearest_support is within 0.3R of entry.
     — Symmetric bullish MFE/MAE cases as positive control.

  6. R values survive apply_rvol_sizing_to_sim (backend.apply_rvol_sizing_to_sim):
     — Sign of pnl_r_sim is never flipped by the position-size multiplier.
     — Magnitude of pnl_r_sim is scaled by the correct rvol_size_mult() factor.
     — pnl_pct_sim is scaled by the same multiplier.
     — Bypass conditions (None rvol, rvol=0, below-threshold rvol, pnl_r_sim=None)
       leave the sim dict unmodified.

All sections import the *real* functions from their source modules so
regressions in production code are caught automatically.
"""

import sys
import types
import importlib

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Module-level stubs so we can import the pipeline modules without a live DB
# ─────────────────────────────────────────────────────────────────────────────

def _install_stubs():
    """Install lightweight stubs for streamlit, supabase, and backend.

    Called once at module load; idempotent (skips if a real module is already
    present).
    """
    if not (
        "streamlit" in sys.modules
        and hasattr(sys.modules["streamlit"], "__file__")
        and sys.modules["streamlit"].__file__
    ):
        st = types.ModuleType("streamlit")
        st.session_state = {}
        st.cache_data = lambda *a, **kw: (lambda f: f)
        st.cache_resource = lambda *a, **kw: (lambda f: f)
        st.experimental_singleton = lambda *a, **kw: (lambda f: f)
        st.error = lambda *a, **kw: None
        st.warning = lambda *a, **kw: None
        sys.modules["streamlit"] = st

    if not (
        "supabase" in sys.modules
        and hasattr(sys.modules.get("supabase"), "__file__")
        and sys.modules["supabase"].__file__
    ):
        sb = types.ModuleType("supabase")
        sb.create_client = lambda *a, **kw: None
        sb.Client = object
        sys.modules["supabase"] = sb


_install_stubs()


def _ensure_stub_backend():
    """Return a minimal stub backend module; replace any existing stub."""
    existing = sys.modules.get("backend")
    if (
        existing is not None
        and hasattr(existing, "__file__")
        and existing.__file__
        and "backend.py" in existing.__file__
    ):
        return existing

    bk = types.ModuleType("backend")
    bk.supabase = None
    bk._BACKTEST_DIRECTIONAL = []
    bk._BACKTEST_RANGE = []
    bk._BACKTEST_NEUTRAL_EXT = []
    bk._BACKTEST_BALANCED = []
    bk._BACKTEST_BIMODAL = []
    sys.modules["backend"] = bk
    return bk


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: import real source modules under stub backend
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def backfill_mod():
    """Return the real backfill_pending_sim_rows module (stub backend)."""
    _install_stubs()
    _ensure_stub_backend()
    sys.modules.pop("backfill_pending_sim_rows", None)
    return importlib.import_module("backfill_pending_sim_rows")


@pytest.fixture(scope="session")
def tiered_mod():
    """Return the real run_tiered_pnl_backfill module (stub backend)."""
    _install_stubs()
    _ensure_stub_backend()
    sys.modules.pop("run_tiered_pnl_backfill", None)
    return importlib.import_module("run_tiered_pnl_backfill")


@pytest.fixture(scope="session")
def fix_squeeze_module():
    """Return the real fix_squeeze_data module (stub backend)."""
    _install_stubs()
    _ensure_stub_backend()
    sys.modules.pop("fix_squeeze_data", None)
    return importlib.import_module("fix_squeeze_data")


@pytest.fixture(scope="session")
def real_backend():
    """Import the real backend module so compute_trade_sim can be tested directly.

    Installs supabase/streamlit stubs before import so no live DB connection is
    required.  Skips the entire test class if backend.py cannot be loaded (e.g.
    a missing system dependency in CI).
    """
    _install_stubs()
    existing = sys.modules.get("backend")
    if (
        existing is not None
        and hasattr(existing, "__file__")
        and existing.__file__
        and "backend.py" in existing.__file__
    ):
        return existing
    sys.modules.pop("backend", None)
    try:
        import backend as _backend
        return _backend
    except Exception as exc:
        pytest.skip(f"Could not import real backend.py: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Squeeze win/loss price override  (backfill_pending_sim_rows.py)
# ─────────────────────────────────────────────────────────────────────────────

class TestSqueezeWinLossOverride:
    """Verify _squeeze_win_loss_override from backfill_pending_sim_rows."""

    IB_HIGH = 105.0
    IB_LOW  = 100.0
    # IB range = 5.0 → bullish target = 115.0 (entry 105 + 2×5)
    #                 → bearish target =  90.0 (entry 100 - 2×5)

    # ── Bullish squeeze ───────────────────────────────────────────────────────

    def test_bullish_close_at_ib_low_is_loss(self, backfill_mod):
        """Close == ib_low is exactly on the stop → Loss."""
        result = backfill_mod._squeeze_win_loss_override(
            "squeeze", "Bullish Break",
            close_price=self.IB_LOW,
            ib_high=self.IB_HIGH, ib_low=self.IB_LOW,
            win_loss="Win",
        )
        assert result == "Loss"

    def test_bullish_close_below_ib_low_is_loss(self, backfill_mod):
        """Close well below ib_low → Loss, even if actual_outcome was Bullish Break."""
        result = backfill_mod._squeeze_win_loss_override(
            "squeeze", "Bullish Break",
            close_price=98.0,
            ib_high=self.IB_HIGH, ib_low=self.IB_LOW,
            win_loss="Win",
        )
        assert result == "Loss"

    def test_bullish_close_at_target_is_win(self, backfill_mod):
        """Close == entry + 2×IB_range (115.0) → Win."""
        target = self.IB_HIGH + 2.0 * (self.IB_HIGH - self.IB_LOW)  # 115.0
        result = backfill_mod._squeeze_win_loss_override(
            "squeeze", "Bullish Break",
            close_price=target,
            ib_high=self.IB_HIGH, ib_low=self.IB_LOW,
            win_loss="Loss",
        )
        assert result == "Win"

    def test_bullish_close_between_stop_and_target_unchanged(self, backfill_mod):
        """Close in the middle (between stop and target) keeps existing win_loss."""
        mid = 107.0  # above ib_low (100), below target (115)
        original = "Loss"
        result = backfill_mod._squeeze_win_loss_override(
            "squeeze", "Bullish Break",
            close_price=mid,
            ib_high=self.IB_HIGH, ib_low=self.IB_LOW,
            win_loss=original,
        )
        assert result == original

    # ── Bearish squeeze ───────────────────────────────────────────────────────

    def test_bearish_close_at_ib_high_is_loss(self, backfill_mod):
        """Close == ib_high is exactly on the (bearish) stop → Loss."""
        result = backfill_mod._squeeze_win_loss_override(
            "squeeze", "Bearish Break",
            close_price=self.IB_HIGH,
            ib_high=self.IB_HIGH, ib_low=self.IB_LOW,
            win_loss="Win",
        )
        assert result == "Loss"

    def test_bearish_close_above_ib_high_is_loss(self, backfill_mod):
        """Close well above ib_high → Loss for a bearish trade."""
        result = backfill_mod._squeeze_win_loss_override(
            "squeeze", "Bearish Break",
            close_price=108.0,
            ib_high=self.IB_HIGH, ib_low=self.IB_LOW,
            win_loss="Win",
        )
        assert result == "Loss"

    def test_bearish_close_at_target_is_win(self, backfill_mod):
        """Close == entry - 2×IB_range (90.0) → Win for bearish trade."""
        target = self.IB_LOW - 2.0 * (self.IB_HIGH - self.IB_LOW)  # 90.0
        result = backfill_mod._squeeze_win_loss_override(
            "squeeze", "Bearish Break",
            close_price=target,
            ib_high=self.IB_HIGH, ib_low=self.IB_LOW,
            win_loss="Loss",
        )
        assert result == "Win"

    def test_bearish_close_between_stop_and_target_unchanged(self, backfill_mod):
        """Close in the middle for a bearish trade keeps existing win_loss."""
        mid = 97.0  # below ib_high (105), above target (90)
        original = "Win"
        result = backfill_mod._squeeze_win_loss_override(
            "squeeze", "Bearish Break",
            close_price=mid,
            ib_high=self.IB_HIGH, ib_low=self.IB_LOW,
            win_loss=original,
        )
        assert result == original

    # ── Screener / data guards ────────────────────────────────────────────────

    def test_non_squeeze_screener_pass_not_overridden(self, backfill_mod):
        """Override only applies to squeeze rows; other passes are untouched."""
        result = backfill_mod._squeeze_win_loss_override(
            "momentum", "Bullish Break",
            close_price=98.0,
            ib_high=self.IB_HIGH, ib_low=self.IB_LOW,
            win_loss="Win",
        )
        assert result == "Win"

    def test_none_screener_pass_not_overridden(self, backfill_mod):
        """None screener_pass does not trigger the override."""
        result = backfill_mod._squeeze_win_loss_override(
            None, "Bullish Break",
            close_price=98.0,
            ib_high=self.IB_HIGH, ib_low=self.IB_LOW,
            win_loss="Win",
        )
        assert result == "Win"

    def test_none_close_price_not_overridden(self, backfill_mod):
        """close_price=None skips the override block entirely."""
        result = backfill_mod._squeeze_win_loss_override(
            "squeeze", "Bullish Break",
            close_price=None,
            ib_high=self.IB_HIGH, ib_low=self.IB_LOW,
            win_loss="Win",
        )
        assert result == "Win"


# ─────────────────────────────────────────────────────────────────────────────
# 2. tiered_pnl_r sentinel fix  (run_tiered_pnl_backfill.py)
# ─────────────────────────────────────────────────────────────────────────────

class TestTieredSentinelFixBackfill:
    """Verify _squeeze_tiered_sentinel from backfill_pending_sim_rows.

    In this module the helper takes a pre-computed bool (is_squeeze) rather
    than the raw screener_pass string, reflecting the local _is_squeeze cache
    variable already computed earlier in the processing loop.
    """

    def test_squeeze_negative_tiered_positive_eod_becomes_eod(self, backfill_mod):
        """Core fix: is_squeeze=True, tiered=-1, eod=+1.5 → tiered=+1.5."""
        result = backfill_mod._squeeze_tiered_sentinel(True, -1.0, 1.5)
        assert result == pytest.approx(1.5)

    def test_non_squeeze_negative_tiered_positive_eod_unchanged(self, backfill_mod):
        """is_squeeze=False: tiered stays -1 even when eod is positive."""
        result = backfill_mod._squeeze_tiered_sentinel(False, -1.0, 1.5)
        assert result == pytest.approx(-1.0)

    def test_squeeze_positive_tiered_not_overridden(self, backfill_mod):
        """Only negative tiered triggers the fix; positive tiered is left alone."""
        result = backfill_mod._squeeze_tiered_sentinel(True, 0.5, 1.5)
        assert result == pytest.approx(0.5)

    def test_squeeze_negative_tiered_negative_eod_not_overridden(self, backfill_mod):
        """eod must be positive for the fix to apply; negative eod → no change."""
        result = backfill_mod._squeeze_tiered_sentinel(True, -1.0, -0.5)
        assert result == pytest.approx(-1.0)

    def test_squeeze_none_tiered_not_overridden(self, backfill_mod):
        """tiered_pnl_r=None is left as None (handled downstream)."""
        result = backfill_mod._squeeze_tiered_sentinel(True, None, 1.5)
        assert result is None

    def test_squeeze_none_eod_not_overridden(self, backfill_mod):
        """eod_pnl_r=None means no reliable fallback; tiered stays negative."""
        result = backfill_mod._squeeze_tiered_sentinel(True, -1.0, None)
        assert result == pytest.approx(-1.0)


class TestTieredSentinelFix:
    """Verify _squeeze_tiered_sentinel from run_tiered_pnl_backfill."""

    def test_squeeze_negative_tiered_positive_eod_becomes_eod(self, tiered_mod):
        """Core fix: squeeze row with tiered=-1, eod=+1.5 → tiered=+1.5."""
        result = tiered_mod._squeeze_tiered_sentinel("squeeze", -1.0, 1.5)
        assert result == pytest.approx(1.5)

    def test_non_squeeze_negative_tiered_positive_eod_unchanged(self, tiered_mod):
        """Non-squeeze row: tiered stays -1 even when eod is positive."""
        result = tiered_mod._squeeze_tiered_sentinel("momentum", -1.0, 1.5)
        assert result == pytest.approx(-1.0)

    def test_squeeze_positive_tiered_not_overridden(self, tiered_mod):
        """Only negative tiered triggers the fix; positive tiered is left alone."""
        result = tiered_mod._squeeze_tiered_sentinel("squeeze", 0.5, 1.5)
        assert result == pytest.approx(0.5)

    def test_squeeze_negative_tiered_negative_eod_not_overridden(self, tiered_mod):
        """eod must be positive for the fix to apply; negative eod → no change."""
        result = tiered_mod._squeeze_tiered_sentinel("squeeze", -1.0, -0.5)
        assert result == pytest.approx(-1.0)

    def test_squeeze_none_tiered_not_overridden(self, tiered_mod):
        """tiered_pnl_r=None is left as None (handled downstream as no-entry)."""
        result = tiered_mod._squeeze_tiered_sentinel("squeeze", None, 1.5)
        assert result is None

    def test_squeeze_none_eod_not_overridden(self, tiered_mod):
        """eod_pnl_r=None means no reliable fallback; tiered stays negative."""
        result = tiered_mod._squeeze_tiered_sentinel("squeeze", -1.0, None)
        assert result == pytest.approx(-1.0)

    def test_squeeze_case_insensitive(self, tiered_mod):
        """screener_pass matching is case-insensitive (e.g. 'SQUEEZE')."""
        result = tiered_mod._squeeze_tiered_sentinel("SQUEEZE", -1.0, 2.0)
        assert result == pytest.approx(2.0)

    def test_squeeze_with_whitespace_in_screener_pass(self, tiered_mod):
        """Whitespace around 'squeeze' is stripped before comparison."""
        result = tiered_mod._squeeze_tiered_sentinel("  squeeze  ", -1.0, 3.0)
        assert result == pytest.approx(3.0)


# ─────────────────────────────────────────────────────────────────────────────
# 3. fix_squeeze_data._squeeze_win_loss() parametrized table
# ─────────────────────────────────────────────────────────────────────────────

# IB levels used across all parametrize cases: low=100, high=105, range=5
# Bullish: entry=105, stop=100, target=115  (105 + 2×5)
# Bearish: entry=100, stop=105, target=90   (100 - 2×5)

_SWL_CASES = [
    # ── Bullish Break ─────────────────────────────────────────────────────────
    # id                            predicted           close  hi    lo    existing  expected
    ("bull_close_at_stop",        "Bullish Break",   100.0,  105.0, 100.0,  "Win",    "Loss"),
    ("bull_close_below_stop",     "Bullish Break",    97.5,  105.0, 100.0,  "Win",    "Loss"),
    ("bull_close_at_target",      "Bullish Break",   115.0,  105.0, 100.0,  "Loss",   "Win"),
    ("bull_close_above_target",   "Bullish Break",   120.0,  105.0, 100.0,  "Loss",   "Win"),
    ("bull_close_middle_no_chg",  "Bullish Break",   107.0,  105.0, 100.0,  "Loss",   None),
    ("bull_close_middle_already_win", "Bullish Break", 107.0, 105.0, 100.0, "Win",   None),
    # ── Bearish Break ────────────────────────────────────────────────────────
    ("bear_close_at_stop",        "Bearish Break",   105.0,  105.0, 100.0,  "Win",    "Loss"),
    ("bear_close_above_stop",     "Bearish Break",   108.0,  105.0, 100.0,  "Win",    "Loss"),
    ("bear_close_at_target",      "Bearish Break",    90.0,  105.0, 100.0,  "Loss",   "Win"),
    ("bear_close_below_target",   "Bearish Break",    85.0,  105.0, 100.0,  "Loss",   "Win"),
    ("bear_close_middle_no_chg",  "Bearish Break",    97.0,  105.0, 100.0,  "Loss",   None),
    # ── Edge / guard cases ───────────────────────────────────────────────────
    ("zero_ib_range_returns_none","Bullish Break",   100.0,  100.0, 100.0,  "Win",    None),
    ("unknown_direction_none",    "Range-Bound",     102.0,  105.0, 100.0,  "Win",    None),
    ("existing_already_correct",  "Bullish Break",   100.0,  105.0, 100.0,  "Loss",   None),
]


@pytest.mark.parametrize(
    "case_id,predicted,close,ib_high,ib_low,existing_wl,expected",
    [(*c,) for c in _SWL_CASES],
    ids=[c[0] for c in _SWL_CASES],
)
def test_squeeze_win_loss_helper(
    fix_squeeze_module,
    case_id, predicted, close, ib_high, ib_low, existing_wl, expected,
):
    """_squeeze_win_loss returns the right correction (or None when no change needed)."""
    result = fix_squeeze_module._squeeze_win_loss(
        predicted_direction=predicted,
        close_price=close,
        ib_high=ib_high,
        ib_low=ib_low,
        existing_wl=existing_wl,
    )
    assert result == expected, (
        f"[{case_id}] predicted={predicted!r} close={close} "
        f"ib_high={ib_high} ib_low={ib_low} existing={existing_wl!r} "
        f"→ expected {expected!r}, got {result!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Bearish pnl_r_sim direction fix  (fix_squeeze_data.fix_backtest_sim_runs)
# ─────────────────────────────────────────────────────────────────────────────
#
# Bug #3 in fix_backtest_sim_runs: bearish squeeze rows had actual_outcome set
# to the wrong value, so compute_trade_sim used the bullish formula and returned
# a positive pnl_r_sim when it should have been negative (short stopped out).
#
# The fix overrides actual_outcome = predicted before calling compute_trade_sim
# so the correct directional formula is always selected.
#
# IB levels: ib_low=100, ib_high=105, range=5
#   Bearish short: entry=100 (ib_low), stop=105 (ib_high), target=90 (100−2×5)
#   Bullish long:  entry=105 (ib_high), stop=100 (ib_low),  target=115 (105+2×5)

def _sim_row(predicted, actual_outcome, close_price, ib_high=105.0, ib_low=100.0):
    """Minimal row dict for compute_trade_sim — no MFE/MAE so EOD close is used."""
    return {
        "ib_high":                ib_high,
        "ib_low":                 ib_low,
        "predicted":              predicted,
        "actual_outcome":         actual_outcome,
        "close_price":            close_price,
        "mfe":                    None,
        "mae":                    None,
        "tcs":                    50,
        "rvol":                   1.0,
        "ib_range_pct":           5.0,
        "scan_type":              "daily",
        "pnl_r_actual":           None,
        "alpaca_exit_fill_price": None,
    }


# Parametrized cases: (id, predicted, actual_outcome, close_price, expected_sign)
# actual_outcome is set equal to predicted — this mirrors the Bug #3 fix pattern
# (enriched["actual_outcome"] = predicted before calling compute_trade_sim).
_PNL_DIR_CASES = [
    # ── Bearish short (short entry at ib_low=100, stop at ib_high=105) ────────
    # close=110 > ib_high(105) → EOD above stop → stopped out → pnl_r_sim = -1.0
    ("bearish_stopped_out",  "Bearish Break", "Bearish Break", 110.0, "negative"),
    # close=88 < ib_high(105), pnl_r = (100-88)/100*100 / (5/100*100) = 2.4 → +2.4R win
    ("bearish_profitable",   "Bearish Break", "Bearish Break",  88.0, "positive"),
    # ── Bullish long (long entry at ib_high=105, stop at ib_low=100) ─────────
    # close=97 <= ib_low(100) → EOD at/below effective stop → stopped out → pnl_r_sim = -1.0
    ("bullish_stopped_out",  "Bullish Break", "Bullish Break",  97.0, "negative"),
    # close=118 > ib_low(100), pnl_r = (118-105)/105*100 / (5/105*100) ≈ +2.6R win
    ("bullish_profitable",   "Bullish Break", "Bullish Break", 118.0, "positive"),
]


@pytest.mark.parametrize(
    "case_id,predicted,actual_outcome,close_price,expected_sign",
    [(*c,) for c in _PNL_DIR_CASES],
    ids=[c[0] for c in _PNL_DIR_CASES],
)
def test_pnl_r_sim_direction_fix_parametrized(
    real_backend, case_id, predicted, actual_outcome, close_price, expected_sign,
):
    """compute_trade_sim returns the correct pnl_r_sim sign when actual_outcome
    is overridden to predicted — mirroring the Bug #3 fix in fix_backtest_sim_runs.

    Bearish short stopped out (close > ib_high) must give pnl_r_sim < 0.
    Bullish long stopped out (close < ib_low) must give pnl_r_sim < 0.
    Profitable trades in either direction give pnl_r_sim > 0.
    """
    row = _sim_row(predicted, actual_outcome, close_price)
    result = real_backend.compute_trade_sim(row, target_r=2.0)
    pnl_r = result["pnl_r_sim"]
    assert result["sim_outcome"] not in ("no_trade", "missing_data", "invalid_ib"), (
        f"[{case_id}] Expected a valid simulation, got sim_outcome="
        f"{result['sim_outcome']!r}"
    )
    assert pnl_r is not None, f"[{case_id}] pnl_r_sim should not be None"
    if expected_sign == "negative":
        assert pnl_r < 0, (
            f"[{case_id}] predicted={predicted!r} close={close_price} → "
            f"expected pnl_r_sim < 0, got {pnl_r}"
        )
    else:
        assert pnl_r > 0, (
            f"[{case_id}] predicted={predicted!r} close={close_price} → "
            f"expected pnl_r_sim > 0, got {pnl_r}"
        )


def test_bearish_wrong_direction_gives_opposite_sign(real_backend):
    """Without the Bug #3 fix, passing actual_outcome='Bullish Break' for a bearish
    squeeze row (close=110, above ib_high=105) makes compute_trade_sim use the
    bullish long formula and return pnl_r_sim > 0 — a false positive.

    The fix (actual_outcome = predicted = 'Bearish Break') correctly returns
    pnl_r_sim = -1.0 (stopped out).  This test documents both outcomes so any
    future change to compute_trade_sim that silently fixes or re-introduces the
    sign error is caught.
    """
    close = 110.0

    fixed_row = _sim_row("Bearish Break", "Bearish Break", close)
    fixed = real_backend.compute_trade_sim(fixed_row, target_r=2.0)
    assert fixed["pnl_r_sim"] < 0, (
        f"With correct direction (Bearish Break) pnl_r_sim should be < 0, "
        f"got {fixed['pnl_r_sim']}"
    )

    bug_row = _sim_row("Bearish Break", "Bullish Break", close)
    bugged = real_backend.compute_trade_sim(bug_row, target_r=2.0)
    assert bugged["pnl_r_sim"] > 0, (
        f"With wrong direction (Bullish Break) pnl_r_sim should be > 0 "
        f"(demonstrating the original bug), got {bugged['pnl_r_sim']}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. v6 intraday MFE/MAE path coverage  (backend.compute_trade_sim)
# ─────────────────────────────────────────────────────────────────────────────
#
# The EOD-close fallback path (mfe=None / mae=None) is already exercised in
# Section 4.  This section exercises the v6 intraday bracket/trail paths for
# both bearish and bullish directions so a regression in the short-side MFE/MAE
# logic is caught immediately.
#
# IB levels: ib_low=100, ib_high=105, ib_range=5, target_r=2.0
#   Bearish short: entry=100 (ib_low), stop=105 (ib_high), target=90 (100−2×5)
#                  stop_dist_pct = 5/100 × 100 = 5.0 %
#   Bullish long:  entry=105 (ib_high), stop=100 (ib_low),  target=115 (105+2×5)
#                  stop_dist_pct = 5/105 × 100 ≈ 4.762 %
#
# v6 bearish flow:
#   1. If mfe_r >= target_r (T1 hit) → trailing_exit or tight_trail_exit
#      • nearest_support within 0.3R below entry → trail_r = 0.5 (tight)
#      • otherwise                                → trail_r = 1.0 (normal)
#      captured_r = max(0, mfe_r − trail_r)
#   2. Elif mae_r >= 1.0 (stop hit before T1) → stopped_out, pnl_r = −1.0
#   3. Neither → held to EOD (close/ft_pct fallback)
#
# v6 bullish flow mirrors the bearish flow but uses nearest_resistance and
# the smart-stop-widened _eff_stop_r threshold.  For the test rows supplied
# (tcs=50, rvol=1.0, ib_range_pct=5.0, scan_type='daily') the smart buffer
# is 0.0, so _eff_stop_r = 1.0 — the same threshold as bearish.

def _intraday_sim_row(
    predicted: str,
    actual_outcome: str,
    mfe: float,
    mae: float,
    ib_high: float = 105.0,
    ib_low: float = 100.0,
    close_price: float | None = None,
    nearest_resistance: float | None = None,
    nearest_support: float | None = None,
) -> dict:
    """Minimal row dict for compute_trade_sim with real intraday MFE/MAE values.

    Supply mfe and mae so _has_clean_mfe is True and the v4/v5/v6 intraday
    paths are activated instead of falling through to the EOD close fallback.
    """
    return {
        "ib_high":                ib_high,
        "ib_low":                 ib_low,
        "predicted":              predicted,
        "actual_outcome":         actual_outcome,
        "close_price":            close_price,
        "mfe":                    mfe,
        "mae":                    mae,
        "tcs":                    50,
        "rvol":                   1.0,
        "ib_range_pct":           5.0,
        "scan_type":              "daily",
        "pnl_r_actual":           None,
        "alpaca_exit_fill_price": None,
        "nearest_resistance":     nearest_resistance,
        "nearest_support":        nearest_support,
    }


# ── Parametrized cases ────────────────────────────────────────────────────────
# Each entry: (id, direction, mfe, mae, nearest_resistance, nearest_support,
#              expected_outcome, expected_pnl_r)
#
# nearest_support=99.0  is 1.0R inside the 0.3R boundary below entry=100
#   → (entry − ns) = 1.0, 0.3 × ib_range = 1.5  → qualifies for tight trail
# nearest_resistance=106.0 is 1.0R inside the 0.3R boundary above entry=105
#   → (nr − entry) = 1.0, 0.3 × ib_range = 1.5  → qualifies for tight trail

_V6_MFE_MAE_CASES = [
    # ── Bearish short ─────────────────────────────────────────────────────────
    # T1 hit (mfe=2.5 >= target_r=2.0), no S/R nearby → normal trail (1.0R)
    # captured_r = max(0, 2.5 − 1.0) = 1.5
    (
        "bearish_t1_hit_trailing_exit",
        "Bearish Break", 2.5, 0.5, None, None,
        "trailing_exit", pytest.approx(1.5),
    ),
    # Stop hit before T1 (mae=1.2 >= 1.0, mfe=0.5 < target_r) → −1.0R
    (
        "bearish_mae_stopped_out",
        "Bearish Break", 0.5, 1.2, None, None,
        "stopped_out", pytest.approx(-1.0),
    ),
    # T1 hit AND nearest_support within 0.3R below entry → tight trail (0.5R)
    # captured_r = max(0, 2.5 − 0.5) = 2.0
    (
        "bearish_t1_hit_tight_trail_exit",
        "Bearish Break", 2.5, 0.5, None, 99.0,
        "tight_trail_exit", pytest.approx(2.0),
    ),
    # ── Bullish long (positive control) ──────────────────────────────────────
    # T1 hit (mfe=2.5 >= target_r=2.0), no S/R nearby → normal trail (1.0R)
    # captured_r = max(0, 2.5 − 1.0) = 1.5
    (
        "bullish_t1_hit_trailing_exit",
        "Bullish Break", 2.5, 0.5, None, None,
        "trailing_exit", pytest.approx(1.5),
    ),
    # Stop hit before T1 (mae=1.2 >= _eff_stop_r=1.0, mfe=0.5 < target_r) → −1.0R
    (
        "bullish_mae_stopped_out",
        "Bullish Break", 0.5, 1.2, None, None,
        "stopped_out", pytest.approx(-1.0),
    ),
    # T1 hit AND nearest_resistance within 0.3R above entry → tight trail (0.5R)
    # captured_r = max(0, 2.5 − 0.5) = 2.0
    (
        "bullish_t1_hit_tight_trail_exit",
        "Bullish Break", 2.5, 0.5, 106.0, None,
        "tight_trail_exit", pytest.approx(2.0),
    ),
]


@pytest.mark.parametrize(
    "case_id,direction,mfe,mae,nearest_resistance,nearest_support,"
    "expected_outcome,expected_pnl_r",
    [(*c,) for c in _V6_MFE_MAE_CASES],
    ids=[c[0] for c in _V6_MFE_MAE_CASES],
)
def test_v6_intraday_mfe_mae_paths(
    real_backend,
    case_id,
    direction,
    mfe,
    mae,
    nearest_resistance,
    nearest_support,
    expected_outcome,
    expected_pnl_r,
):
    """compute_trade_sim v6 intraday MFE/MAE paths produce the correct
    sim_outcome and pnl_r_sim for both bearish and bullish squeeze rows.

    Tests the three v6 intraday branches:
      • T1 hit, no tight S/R  → trailing_exit,      captured_r = mfe − 1.0R
      • MAE >= stop threshold  → stopped_out,         pnl_r_sim = −1.0R
      • T1 hit + tight S/R     → tight_trail_exit,   captured_r = mfe − 0.5R
    """
    row = _intraday_sim_row(
        predicted=direction,
        actual_outcome=direction,
        mfe=mfe,
        mae=mae,
        nearest_resistance=nearest_resistance,
        nearest_support=nearest_support,
    )
    result = real_backend.compute_trade_sim(row, target_r=2.0)

    assert result["sim_outcome"] not in ("no_trade", "missing_data", "invalid_ib"), (
        f"[{case_id}] Expected a valid simulation, got sim_outcome="
        f"{result['sim_outcome']!r}"
    )
    assert result["sim_outcome"] == expected_outcome, (
        f"[{case_id}] direction={direction!r} mfe={mfe} mae={mae} "
        f"nearest_resistance={nearest_resistance} nearest_support={nearest_support} "
        f"→ expected sim_outcome={expected_outcome!r}, got {result['sim_outcome']!r}"
    )
    assert result["pnl_r_sim"] == expected_pnl_r, (
        f"[{case_id}] direction={direction!r} mfe={mfe} mae={mae} "
        f"→ expected pnl_r_sim={expected_pnl_r}, got {result['pnl_r_sim']}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. R values survive apply_rvol_sizing_to_sim  (backend.py)
# ─────────────────────────────────────────────────────────────────────────────
#
# After compute_trade_sim returns the raw sim dict, fix_backtest_sim_runs pipes
# it through apply_rvol_sizing_to_sim (fix_squeeze_data.py lines 288-290).
# These tests confirm that the position-size multiplier never flips the sign of
# pnl_r_sim and that the magnitude is scaled by exactly rvol_size_mult(rvol).
#
# Default RVOL tiers (fallback when adaptive_exits.json is absent):
#   rvol >= 3.5 → 1.5×
#   rvol >= 2.5 → 1.25×
#   below 2.5   → 1.0× (no change)

class TestApplyRvolSizingToSim:
    """Verify backend.apply_rvol_sizing_to_sim preserves R-value sign and
    scales magnitude correctly for both bullish and bearish sim results."""

    # ── helper ────────────────────────────────────────────────────────────────

    @staticmethod
    def _make_sim(pnl_r, pnl_pct=None, sim_outcome="stopped_out"):
        return {
            "pnl_r_sim":   pnl_r,
            "pnl_pct_sim": pnl_pct,
            "sim_outcome": sim_outcome,
        }

    # ── sign preservation ─────────────────────────────────────────────────────

    def test_bearish_stopped_out_negative_sign_preserved(self, real_backend):
        """Bearish stopped-out pnl_r_sim=-1.0 must remain negative after sizing.

        This is the core invariant: a multiplier > 0 can only scale magnitude,
        never flip sign.  Uses rvol=2.5 which triggers the 1.25× default tier.
        """
        raw = self._make_sim(-1.0, pnl_pct=-0.5)
        result = real_backend.apply_rvol_sizing_to_sim(raw, rvol_raw=2.5)
        assert result["pnl_r_sim"] < 0, (
            f"Bearish stopped-out pnl_r_sim must remain negative after rvol sizing; "
            f"got {result['pnl_r_sim']}"
        )

    def test_bullish_profitable_positive_sign_preserved(self, real_backend):
        """Bullish winning pnl_r_sim=+1.5 must remain positive after sizing."""
        raw = self._make_sim(+1.5, pnl_pct=0.75)
        result = real_backend.apply_rvol_sizing_to_sim(raw, rvol_raw=2.5)
        assert result["pnl_r_sim"] > 0, (
            f"Bullish winning pnl_r_sim must remain positive after rvol sizing; "
            f"got {result['pnl_r_sim']}"
        )

    def test_bearish_profitable_positive_sign_preserved(self, real_backend):
        """Bearish profitable trade pnl_r_sim=+2.0 stays positive after sizing."""
        raw = self._make_sim(+2.0, pnl_pct=1.0, sim_outcome="target_hit")
        result = real_backend.apply_rvol_sizing_to_sim(raw, rvol_raw=3.5)
        assert result["pnl_r_sim"] > 0, (
            f"Bearish profitable pnl_r_sim must remain positive after rvol sizing; "
            f"got {result['pnl_r_sim']}"
        )

    def test_bullish_stopped_out_negative_sign_preserved(self, real_backend):
        """Bullish stopped-out pnl_r_sim=-1.0 stays negative after high rvol sizing."""
        raw = self._make_sim(-1.0, pnl_pct=-0.48)
        result = real_backend.apply_rvol_sizing_to_sim(raw, rvol_raw=3.5)
        assert result["pnl_r_sim"] < 0, (
            f"Bullish stopped-out pnl_r_sim must remain negative after rvol sizing; "
            f"got {result['pnl_r_sim']}"
        )

    # ── magnitude scaling ─────────────────────────────────────────────────────

    def test_magnitude_scales_by_rvol_multiplier_bearish_loss(self, real_backend):
        """pnl_r_sim magnitude is multiplied by rvol_size_mult(rvol_raw).

        Uses dynamic lookup of the multiplier so the test is correct regardless
        of whether adaptive_exits.json overrides the default tiers.
        """
        raw_r = -1.0
        rvol = 2.5
        raw = self._make_sim(raw_r)
        result = real_backend.apply_rvol_sizing_to_sim(raw, rvol_raw=rvol)
        expected_mult = real_backend.rvol_size_mult(rvol)
        expected_r = round(raw_r * expected_mult, 4)
        assert result["pnl_r_sim"] == pytest.approx(expected_r), (
            f"Expected pnl_r_sim={expected_r} (raw={raw_r} × mult={expected_mult}), "
            f"got {result['pnl_r_sim']}"
        )

    def test_magnitude_scales_by_rvol_multiplier_bullish_win(self, real_backend):
        """Bullish winning pnl_r_sim is scaled by the same factor."""
        raw_r = +1.5
        rvol = 3.5
        raw = self._make_sim(raw_r)
        result = real_backend.apply_rvol_sizing_to_sim(raw, rvol_raw=rvol)
        expected_mult = real_backend.rvol_size_mult(rvol)
        expected_r = round(raw_r * expected_mult, 4)
        assert result["pnl_r_sim"] == pytest.approx(expected_r), (
            f"Expected pnl_r_sim={expected_r} (raw={raw_r} × mult={expected_mult}), "
            f"got {result['pnl_r_sim']}"
        )

    def test_pnl_pct_sim_also_scaled_by_same_multiplier(self, real_backend):
        """pnl_pct_sim is scaled by the same multiplier as pnl_r_sim."""
        raw_r, raw_pct = -1.0, -0.5
        rvol = 2.5
        raw = self._make_sim(raw_r, pnl_pct=raw_pct)
        result = real_backend.apply_rvol_sizing_to_sim(raw, rvol_raw=rvol)
        mult = real_backend.rvol_size_mult(rvol)
        expected_pct = round(raw_pct * mult, 4)
        assert result.get("pnl_pct_sim") == pytest.approx(expected_pct), (
            f"pnl_pct_sim should be scaled to {expected_pct} "
            f"(raw={raw_pct} × mult={mult}), got {result.get('pnl_pct_sim')}"
        )

    def test_rvol_mult_stored_in_result(self, real_backend):
        """apply_rvol_sizing_to_sim records rvol_mult in the returned dict."""
        raw = self._make_sim(-1.0)
        rvol = 2.5
        result = real_backend.apply_rvol_sizing_to_sim(raw, rvol_raw=rvol)
        expected_mult = real_backend.rvol_size_mult(rvol)
        assert result.get("rvol_mult") == pytest.approx(expected_mult), (
            f"rvol_mult should be {expected_mult}, got {result.get('rvol_mult')}"
        )

    def test_higher_rvol_produces_larger_loss_magnitude(self, real_backend):
        """Larger rvol triggers a bigger multiplier, amplifying the loss magnitude.

        Skipped gracefully if the loaded config doesn't differentiate 2.5 vs 3.5.
        """
        raw_r = -1.0
        rvol_low, rvol_high = 2.5, 3.5
        mult_low = real_backend.rvol_size_mult(rvol_low)
        mult_high = real_backend.rvol_size_mult(rvol_high)
        if mult_high <= mult_low:
            pytest.skip(
                f"Config does not differentiate rvol={rvol_low} vs {rvol_high} "
                f"(both → {mult_low}×); skipping magnitude-ordering check."
            )
        sim_low = real_backend.apply_rvol_sizing_to_sim(
            self._make_sim(raw_r), rvol_raw=rvol_low
        )
        sim_high = real_backend.apply_rvol_sizing_to_sim(
            self._make_sim(raw_r), rvol_raw=rvol_high
        )
        assert abs(sim_high["pnl_r_sim"]) > abs(sim_low["pnl_r_sim"]), (
            f"Higher rvol ({rvol_high}) should produce a larger loss magnitude; "
            f"low={sim_low['pnl_r_sim']}, high={sim_high['pnl_r_sim']}"
        )

    # ── bypass conditions ─────────────────────────────────────────────────────

    def test_none_rvol_raw_returns_sim_unchanged(self, real_backend):
        """rvol_raw=None → function returns the original sim dict unmodified."""
        raw = self._make_sim(-1.0, pnl_pct=-0.5)
        result = real_backend.apply_rvol_sizing_to_sim(raw, rvol_raw=None)
        assert result["pnl_r_sim"] == pytest.approx(-1.0), (
            f"rvol_raw=None must leave pnl_r_sim unchanged; got {result['pnl_r_sim']}"
        )
        assert result.get("pnl_pct_sim") == pytest.approx(-0.5), (
            f"rvol_raw=None must leave pnl_pct_sim unchanged; got {result.get('pnl_pct_sim')}"
        )

    def test_zero_rvol_raw_returns_sim_unchanged(self, real_backend):
        """rvol_raw=0 (falsy) → function returns the original sim dict unmodified."""
        raw = self._make_sim(-1.0, pnl_pct=-0.5)
        result = real_backend.apply_rvol_sizing_to_sim(raw, rvol_raw=0)
        assert result["pnl_r_sim"] == pytest.approx(-1.0), (
            f"rvol_raw=0 must leave pnl_r_sim unchanged; got {result['pnl_r_sim']}"
        )

    def test_rvol_below_all_thresholds_unchanged(self, real_backend):
        """rvol below every configured tier → mult=1.0 → pnl_r_sim unchanged."""
        raw_r = -1.0
        rvol = 1.0
        raw = self._make_sim(raw_r, pnl_pct=-0.5)
        result = real_backend.apply_rvol_sizing_to_sim(raw, rvol_raw=rvol)
        mult = real_backend.rvol_size_mult(rvol)
        if mult == 1.0:
            assert result["pnl_r_sim"] == pytest.approx(raw_r), (
                f"rvol={rvol} gives mult=1.0; pnl_r_sim should be unchanged at "
                f"{raw_r}, got {result['pnl_r_sim']}"
            )
        else:
            assert result["pnl_r_sim"] < 0, (
                f"Even with non-default tier, sign must stay negative; "
                f"got {result['pnl_r_sim']}"
            )

    def test_none_pnl_r_sim_not_modified(self, real_backend):
        """pnl_r_sim=None (no_trade / missing-data row) is left as None.

        This mirrors the bypass in apply_rvol_sizing_to_sim that guards against
        None before scaling, ensuring no_trade rows propagate correctly.
        """
        raw = self._make_sim(None, sim_outcome="no_trade")
        result = real_backend.apply_rvol_sizing_to_sim(raw, rvol_raw=2.5)
        assert result["pnl_r_sim"] is None, (
            f"pnl_r_sim=None must not be modified by sizing; "
            f"got {result['pnl_r_sim']}"
        )
