"""
Tests for calibrate_sp_mult.py.

Two complementary layers:
  1. test_self_test — subprocess wrapper for the script's built-in --self-test
     suite (covers _recommend_mult and _apply_to_bot via the existing
     deterministic harness inside the script).
  2. Direct unit tests for _stats() and _recommend_mult() that use small
     synthetic fixture rows and never touch Supabase.
"""
import importlib
import math
import subprocess
import sys
import types
import unittest.mock as mock

import pytest


# ---------------------------------------------------------------------------
# Helpers to import calibrate_sp_mult without a real Supabase connection
# ---------------------------------------------------------------------------

def _load_module():
    """
    Import calibrate_sp_mult in a way that bypasses the Supabase bootstrap.

    The module calls sys.exit(1) if `backend.supabase` is falsy, so we inject
    a fake backend and log_utils into sys.modules before the import, then
    clean up afterwards.
    """
    fake_backend = types.ModuleType("backend")
    fake_backend.supabase = mock.MagicMock()

    fake_log_utils = types.ModuleType("log_utils")
    fake_log_utils._rotate_log = mock.MagicMock()
    fake_log_utils._parse_int_env = mock.MagicMock(return_value=0)
    fake_log_utils.validate_env_config = mock.MagicMock()

    overrides = {
        "backend": fake_backend,
        "log_utils": fake_log_utils,
    }

    mod_name = "calibrate_sp_mult"
    previously_loaded = sys.modules.pop(mod_name, None)
    try:
        with mock.patch.dict(sys.modules, overrides):
            mod = importlib.import_module(mod_name)
        return mod
    finally:
        sys.modules.pop(mod_name, None)
        if previously_loaded is not None:
            sys.modules[mod_name] = previously_loaded


@pytest.fixture(scope="module")
def csm():
    """Module-scoped fixture: the imported calibrate_sp_mult module."""
    return _load_module()


# ---------------------------------------------------------------------------
# 1. Subprocess wrapper for the script's built-in --self-test
# ---------------------------------------------------------------------------

def test_self_test():
    result = subprocess.run(
        [sys.executable, "calibrate_sp_mult.py", "--self-test"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"calibrate_sp_mult.py --self-test exited with code {result.returncode}.\n"
        f"Output:\n{output}"
    )
    assert "FAIL" not in result.stdout, (
        f"One or more self-test cases reported FAIL:\n{result.stdout}"
    )
    assert "All self-tests passed." in result.stdout
    assert "All _apply_to_bot self-tests passed." in result.stdout
    assert "All reset-confirm self-tests passed." in result.stdout


# ---------------------------------------------------------------------------
# 2. _stats() — aggregation logic with synthetic fixture data
# ---------------------------------------------------------------------------

def _row(win_loss, r):
    return {"win_loss": win_loss, "tiered_pnl_r": r}


class TestStats:
    def test_empty_rows_returns_nulls(self, csm):
        s = csm._stats([])
        assert s["n"] == 0
        assert s["wr"] is None
        assert s["avg_r"] is None
        assert s["expectancy"] is None
        assert s["avg_win_r"] is None
        assert s["avg_loss_r"] is None

    def test_single_win(self, csm):
        rows = [_row("Win", 2.0)]
        s = csm._stats(rows)
        assert s["n"] == 1
        assert s["wr"] == pytest.approx(1.0)
        assert s["avg_win_r"] == pytest.approx(2.0)
        assert s["avg_loss_r"] is None
        assert s["avg_r"] == pytest.approx(2.0)
        assert s["expectancy"] == pytest.approx(2.0)

    def test_single_loss(self, csm):
        rows = [_row("Loss", -1.0)]
        s = csm._stats(rows)
        assert s["n"] == 1
        assert s["wr"] == pytest.approx(0.0)
        assert s["avg_win_r"] is None
        assert s["avg_loss_r"] == pytest.approx(-1.0)
        assert s["avg_r"] == pytest.approx(-1.0)
        assert s["expectancy"] == pytest.approx(-1.0)

    def test_win_rate_calculation(self, csm):
        rows = [_row("Win", 1.0), _row("Win", 1.0), _row("Loss", -0.5)]
        s = csm._stats(rows)
        assert s["n"] == 3
        assert s["wr"] == pytest.approx(2 / 3)

    def test_avg_win_r(self, csm):
        rows = [_row("Win", 1.0), _row("Win", 3.0), _row("Loss", -1.0)]
        s = csm._stats(rows)
        assert s["avg_win_r"] == pytest.approx(2.0)

    def test_avg_loss_r(self, csm):
        rows = [_row("Win", 2.0), _row("Loss", -1.0), _row("Loss", -3.0)]
        s = csm._stats(rows)
        assert s["avg_loss_r"] == pytest.approx(-2.0)

    def test_avg_r_across_all_trades(self, csm):
        rows = [_row("Win", 2.0), _row("Loss", -1.0)]
        s = csm._stats(rows)
        assert s["avg_r"] == pytest.approx(0.5)

    def test_expectancy_formula(self, csm):
        """R-expectancy = WR * avg_win_R + (1-WR) * avg_loss_R."""
        rows = [
            _row("Win", 2.0),
            _row("Win", 2.0),
            _row("Loss", -1.0),
        ]
        s = csm._stats(rows)
        wr = 2 / 3
        expected_exp = wr * 2.0 + (1 - wr) * (-1.0)
        assert s["expectancy"] == pytest.approx(expected_exp)

    def test_all_wins_expectancy_equals_avg_win_r(self, csm):
        rows = [_row("Win", 1.5), _row("Win", 2.5)]
        s = csm._stats(rows)
        assert s["expectancy"] == pytest.approx(s["avg_win_r"])

    def test_all_losses_expectancy_equals_avg_loss_r(self, csm):
        rows = [_row("Loss", -1.0), _row("Loss", -2.0)]
        s = csm._stats(rows)
        assert s["expectancy"] == pytest.approx(s["avg_loss_r"])

    def test_win_loss_case_insensitive(self, csm):
        """win_loss matching should be case-insensitive."""
        rows = [_row("win", 1.0), _row("WIN", 2.0), _row("loss", -1.0)]
        s = csm._stats(rows)
        assert s["n"] == 3
        assert s["wr"] == pytest.approx(2 / 3)

    def test_larger_realistic_sample(self, csm):
        """Synthetic 10-trade sample with known expected values."""
        wins = [_row("Win", r) for r in [1.0, 2.0, 1.5, 0.5, 2.5, 1.0, 3.0]]  # 7 wins
        losses = [_row("Loss", r) for r in [-1.0, -0.5, -1.5]]  # 3 losses
        rows = wins + losses
        s = csm._stats(rows)

        assert s["n"] == 10
        assert s["wr"] == pytest.approx(0.7)
        assert s["avg_win_r"] == pytest.approx(sum([1.0, 2.0, 1.5, 0.5, 2.5, 1.0, 3.0]) / 7)
        assert s["avg_loss_r"] == pytest.approx(sum([-1.0, -0.5, -1.5]) / 3)
        expected_exp = 0.7 * s["avg_win_r"] + 0.3 * s["avg_loss_r"]
        assert s["expectancy"] == pytest.approx(expected_exp)

    def test_rows_with_none_tiered_pnl_r_are_excluded(self, csm):
        """Rows where tiered_pnl_r is None must not contribute to averages."""
        rows = [
            {"win_loss": "Win", "tiered_pnl_r": 2.0},
            {"win_loss": "Win", "tiered_pnl_r": None},
            {"win_loss": "Loss", "tiered_pnl_r": -1.0},
        ]
        s = csm._stats(rows)
        assert s["n"] == 3
        assert s["wr"] == pytest.approx(2 / 3)
        assert s["avg_win_r"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 3. _recommend_mult() — multiplier selection logic
# ---------------------------------------------------------------------------

class TestRecommendMult:
    def test_equal_expectancies_gives_1_00(self, csm):
        assert csm._recommend_mult(0.327, 0.327) == pytest.approx(1.00)

    def test_much_higher_pass_exp_clamped_to_1_30(self, csm):
        result = csm._recommend_mult(0.622, 0.327)
        assert result == pytest.approx(1.30)

    def test_much_lower_pass_exp_clamped_to_0_70(self, csm):
        result = csm._recommend_mult(0.164, 0.327)
        assert result == pytest.approx(0.70)

    def test_zero_pass_exp_returns_0_70(self, csm):
        assert csm._recommend_mult(0.0, 0.327) == pytest.approx(0.70)

    def test_negative_pass_exp_returns_0_70(self, csm):
        assert csm._recommend_mult(-0.1, 0.327) == pytest.approx(0.70)

    def test_zero_gap_exp_returns_1_00(self, csm):
        assert csm._recommend_mult(0.4, 0.0) == pytest.approx(1.00)

    def test_negative_gap_exp_returns_1_00(self, csm):
        assert csm._recommend_mult(0.4, -0.1) == pytest.approx(1.00)

    def test_sqrt_dampening(self, csm):
        """
        A 2× expectancy advantage should become ~1.41× before clamping.
        For pass_exp = 2 * gap_exp the raw sqrt ratio is sqrt(2) ≈ 1.414,
        which rounds to 1.40 (nearest 0.05).
        """
        gap_exp = 0.3
        pass_exp = 0.6
        raw = math.sqrt(pass_exp / gap_exp)
        clamped = max(0.70, min(1.30, raw))
        expected = round(clamped / 0.05) * 0.05
        assert csm._recommend_mult(pass_exp, gap_exp) == pytest.approx(expected)

    def test_result_is_multiple_of_0_05(self, csm):
        """Every output must be a clean 0.05 multiple."""
        test_pairs = [
            (0.1, 0.3),
            (0.3, 0.3),
            (0.4, 0.3),
            (0.5, 0.3),
            (0.8, 0.3),
        ]
        for pass_exp, gap_exp in test_pairs:
            result = csm._recommend_mult(pass_exp, gap_exp)
            remainder = round(result / 0.05) * 0.05
            assert result == pytest.approx(remainder), (
                f"_recommend_mult({pass_exp}, {gap_exp}) = {result} is not a 0.05 multiple"
            )

    def test_output_always_within_clamp_range(self, csm):
        """Result must always lie in [0.70, 1.30]."""
        extremes = [
            (1000.0, 0.001),
            (0.001, 1000.0),
            (0.0, 0.0),
        ]
        for pass_exp, gap_exp in extremes:
            result = csm._recommend_mult(pass_exp, gap_exp)
            assert 0.70 <= result <= 1.30, (
                f"_recommend_mult({pass_exp}, {gap_exp}) = {result} outside [0.70, 1.30]"
            )
