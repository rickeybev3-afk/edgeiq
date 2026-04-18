"""Unit tests for the RVOL position-size multiplier wiring.

Tests exercise the real production functions in backend.py and
paper_trader_bot.py rather than re-implementing the logic inline.

Heavy module-level dependencies (supabase, streamlit) are mocked before
importing so the files can load without live connections.

Run with:  python test_rvol_size_mult.py
"""
import sys
import types
import math
import unittest


# ---------------------------------------------------------------------------
# Stub heavy deps so backend.py and paper_trader_bot.py load without a live DB
# ---------------------------------------------------------------------------

def _stub_modules():
    st = types.ModuleType("streamlit")
    st.session_state = {}
    st.cache_data      = lambda *a, **kw: (lambda f: f)
    st.cache_resource  = lambda *a, **kw: (lambda f: f)
    st.experimental_singleton = lambda *a, **kw: (lambda f: f)
    sys.modules["streamlit"] = st

    sb = types.ModuleType("supabase")
    sb.create_client = lambda *a, **kw: None
    sb.Client        = object
    sys.modules["supabase"] = sb


_stub_modules()

import backend  # noqa: E402  (import after stubs)


# ---------------------------------------------------------------------------
# paper_trader_bot._rvol_size_mult is loaded lazily to avoid the bot's
# expensive startup routines (Alpaca, Telegram, scheduler).  We import just
# the function via importlib so we don't trigger __main__ code.
# ---------------------------------------------------------------------------

def _load_bot_fn():
    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        "paper_trader_bot",
        os.path.join(os.path.dirname(__file__), "paper_trader_bot.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["paper_trader_bot"] = mod
    spec.loader.exec_module(mod)
    return mod._rvol_size_mult


try:
    _bot_rvol_size_mult = _load_bot_fn()
    _BOT_AVAILABLE = True
except Exception as _e:
    _BOT_AVAILABLE = False
    _bot_rvol_size_mult = None


# ---------------------------------------------------------------------------
# Tests: backend.rvol_size_mult (reads live adaptive_exits.json)
# ---------------------------------------------------------------------------

class TestBackendRvolSizeMult(unittest.TestCase):
    """Tests for backend.rvol_size_mult() — the real production function."""

    def test_below_all_thresholds_returns_baseline(self):
        self.assertAlmostEqual(backend.rvol_size_mult(1.0),  1.0)
        self.assertAlmostEqual(backend.rvol_size_mult(2.4),  1.0)
        self.assertAlmostEqual(backend.rvol_size_mult(0.0),  1.0)

    def test_at_lower_tier_boundary(self):
        self.assertAlmostEqual(backend.rvol_size_mult(2.5), 1.25)

    def test_between_tiers(self):
        self.assertAlmostEqual(backend.rvol_size_mult(3.0), 1.25)

    def test_at_upper_tier_boundary(self):
        self.assertAlmostEqual(backend.rvol_size_mult(3.5), 1.5)

    def test_above_upper_tier(self):
        self.assertAlmostEqual(backend.rvol_size_mult(5.0), 1.5)

    def test_none_returns_baseline(self):
        self.assertAlmostEqual(backend.rvol_size_mult(None), 1.0)

    def test_nan_returns_baseline(self):
        self.assertAlmostEqual(backend.rvol_size_mult(float("nan")), 1.0)

    def test_highest_tier_wins_at_boundary(self):
        # 3.5 must return the 1.5× tier, not the 1.25× tier
        self.assertAlmostEqual(backend.rvol_size_mult(3.5), 1.5)
        self.assertNotAlmostEqual(backend.rvol_size_mult(3.5), 1.25)


# ---------------------------------------------------------------------------
# Tests: backend.apply_rvol_sizing_to_sim (real production helper)
# ---------------------------------------------------------------------------

class TestApplyRvolSizingToSim(unittest.TestCase):
    """Tests for backend.apply_rvol_sizing_to_sim() — the real function."""

    def test_low_rvol_no_change(self):
        sim = {"pnl_r_sim": 1.5, "sim_outcome": "win"}
        result = backend.apply_rvol_sizing_to_sim(sim, 1.0)
        self.assertAlmostEqual(result["pnl_r_sim"], 1.5)

    def test_medium_rvol_scales_win(self):
        sim = {"pnl_r_sim": 1.5, "sim_outcome": "win"}
        result = backend.apply_rvol_sizing_to_sim(sim, 3.0)
        self.assertAlmostEqual(result["pnl_r_sim"], 1.875)  # 1.5 × 1.25

    def test_high_rvol_scales_win(self):
        sim = {"pnl_r_sim": 1.5, "sim_outcome": "win"}
        result = backend.apply_rvol_sizing_to_sim(sim, 4.0)
        self.assertAlmostEqual(result["pnl_r_sim"], 2.25)  # 1.5 × 1.5

    def test_scales_losses_too(self):
        sim = {"pnl_r_sim": -1.0, "sim_outcome": "loss"}
        result = backend.apply_rvol_sizing_to_sim(sim, 3.0)
        self.assertAlmostEqual(result["pnl_r_sim"], -1.25)  # -1.0 × 1.25

    def test_none_rvol_no_change(self):
        sim = {"pnl_r_sim": 1.5}
        result = backend.apply_rvol_sizing_to_sim(sim, None)
        self.assertAlmostEqual(result["pnl_r_sim"], 1.5)

    def test_zero_rvol_no_change(self):
        sim = {"pnl_r_sim": 1.5}
        result = backend.apply_rvol_sizing_to_sim(sim, 0)
        self.assertAlmostEqual(result["pnl_r_sim"], 1.5)

    def test_none_pnl_r_unchanged(self):
        sim = {"pnl_r_sim": None, "sim_outcome": "no_trade"}
        result = backend.apply_rvol_sizing_to_sim(sim, 3.5)
        self.assertIsNone(result["pnl_r_sim"])

    def test_original_sim_not_mutated(self):
        sim = {"pnl_r_sim": 1.5}
        backend.apply_rvol_sizing_to_sim(sim, 3.5)
        self.assertAlmostEqual(sim["pnl_r_sim"], 1.5)

    def test_other_fields_preserved(self):
        sim = {"pnl_r_sim": 1.5, "sim_outcome": "win", "entry_price_sim": 100.0}
        result = backend.apply_rvol_sizing_to_sim(sim, 3.0)
        self.assertEqual(result["sim_outcome"], "win")
        self.assertAlmostEqual(result["entry_price_sim"], 100.0)

    def test_exact_tier_boundary_3_5(self):
        sim = {"pnl_r_sim": 1.0}
        result = backend.apply_rvol_sizing_to_sim(sim, 3.5)
        self.assertAlmostEqual(result["pnl_r_sim"], 1.5)  # 1.0 × 1.5

    def test_just_below_lower_tier(self):
        sim = {"pnl_r_sim": 1.0}
        result = backend.apply_rvol_sizing_to_sim(sim, 2.49)
        self.assertAlmostEqual(result["pnl_r_sim"], 1.0)  # no bonus


# ---------------------------------------------------------------------------
# Tests: paper_trader_bot._rvol_size_mult (real production function)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_BOT_AVAILABLE, "paper_trader_bot could not be imported")
class TestBotRvolSizeMult(unittest.TestCase):
    """Tests for paper_trader_bot._rvol_size_mult() — the real bot function."""

    def test_below_all_thresholds_returns_baseline(self):
        self.assertAlmostEqual(_bot_rvol_size_mult(1.0), 1.0)
        self.assertAlmostEqual(_bot_rvol_size_mult(2.4), 1.0)

    def test_at_lower_tier_boundary(self):
        self.assertAlmostEqual(_bot_rvol_size_mult(2.5), 1.25)

    def test_between_tiers(self):
        self.assertAlmostEqual(_bot_rvol_size_mult(3.0), 1.25)

    def test_at_upper_tier_boundary(self):
        self.assertAlmostEqual(_bot_rvol_size_mult(3.5), 1.5)

    def test_above_upper_tier(self):
        self.assertAlmostEqual(_bot_rvol_size_mult(5.0), 1.5)

    def test_none_returns_baseline(self):
        self.assertAlmostEqual(_bot_rvol_size_mult(None), 1.0)

    def test_nan_returns_baseline(self):
        self.assertAlmostEqual(_bot_rvol_size_mult(float("nan")), 1.0)


# ---------------------------------------------------------------------------
# Tests: mirror contract — bot and backend agree
# ---------------------------------------------------------------------------

@unittest.skipUnless(_BOT_AVAILABLE, "paper_trader_bot could not be imported")
class TestMirrorContract(unittest.TestCase):
    """Verifies that paper_trader_bot and backend return identical multipliers."""

    def test_agree_across_rvol_range(self):
        test_values = [0.5, 1.0, 1.5, 2.0, 2.4, 2.5, 2.9, 3.0, 3.5, 4.0, 5.0]
        for rvol in test_values:
            with self.subTest(rvol=rvol):
                bot_val     = _bot_rvol_size_mult(rvol)
                backend_val = backend.rvol_size_mult(rvol)
                self.assertAlmostEqual(
                    bot_val, backend_val,
                    msg=f"bot={bot_val} vs backend={backend_val} at rvol={rvol}"
                )

    def test_both_handle_none(self):
        self.assertAlmostEqual(_bot_rvol_size_mult(None), 1.0)
        self.assertAlmostEqual(backend.rvol_size_mult(None), 1.0)


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.TestLoader().loadTestsFromModule(
        sys.modules[__name__]
    ))
    sys.exit(0 if result.wasSuccessful() else 1)
