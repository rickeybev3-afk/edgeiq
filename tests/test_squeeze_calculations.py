"""Unit tests for squeeze-specific win/loss and R-value calculations.

Covers three bug-fixes merged into the squeeze trade pipeline:

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

All three sections import the *real* functions from their source modules so
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
