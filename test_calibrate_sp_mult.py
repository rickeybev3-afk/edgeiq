"""
Tests for calibrate_sp_mult.py.

Three complementary layers:
  1. test_self_test — subprocess wrapper for the script's built-in --self-test
     suite (covers _recommend_mult and _apply_to_bot via the existing
     deterministic harness inside the script).
  2. Direct unit tests for _stats() and _recommend_mult() that use small
     synthetic fixture rows and never touch Supabase.
  3. TestApplyToBot — end-to-end tests for the full calibration pipeline:
     recommend multiplier → call _apply_to_bot against a tempfile fixture →
     read back the patched _SP_MULT_TABLE entry → assert the value is correct.
     Self-contained: uses tempfile, never touches real trade_utils.py or Supabase.
"""
import importlib
import math
import os
import re
import subprocess
import sys
import tempfile
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


# ---------------------------------------------------------------------------
# 4. TestApplyToBot — end-to-end: recommend → patch tempfile → read back
# ---------------------------------------------------------------------------

def _read_table_entry(path: str, pass_name: str) -> float:
    """Read _SP_MULT_TABLE[pass_name] from a patched file. Raises if not found."""
    with open(path) as fh:
        content = fh.read()
    pat = re.compile(r'"' + re.escape(pass_name) + r'"\s*:\s*([\d.]+)')
    m = pat.search(content)
    if not m:
        raise AssertionError(
            f"'{pass_name}' entry not found in _SP_MULT_TABLE after patching"
        )
    return float(m.group(1))


class TestApplyToBot:
    """
    End-to-end tests for _apply_to_bot().

    Each test writes the shared _APPLY_FIXTURE to a NamedTemporaryFile, calls
    _apply_to_bot() with a specific (pass_name, multiplier, comment) triple,
    then reads the _SP_MULT_TABLE entry back from disk and asserts it matches
    the intended value.  No real trade_utils.py is ever modified, and no
    Supabase connection is required.
    """

    @staticmethod
    def _run_apply(csm, pass_name, new_mult, comment, fixture=None, citation_line=""):
        """
        Write fixture to a temp file, call _apply_to_bot(), return the temp
        file path (caller is responsible for cleanup via try/finally).
        """
        if fixture is None:
            fixture = csm._APPLY_FIXTURE
        tf = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        try:
            tf.write(fixture)
            tf.close()
            csm._apply_to_bot(pass_name, new_mult, comment,
                               citation_line=citation_line, bot_path=tf.name)
            return tf.name
        except Exception:
            try:
                os.unlink(tf.name)
            except OSError:
                pass
            try:
                os.unlink(tf.name + ".bak")
            except OSError:
                pass
            raise

    @staticmethod
    def _cleanup(path):
        for p in (path, path + ".bak"):
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_apply_squeeze_writes_correct_value(self, csm):
        """
        Full pipeline: _apply_to_bot writes the recommended multiplier for
        'squeeze' into the fixture file and the value reads back correctly.
        """
        rec_mult = csm._recommend_mult(0.411, 0.327)
        tmp = self._run_apply(
            csm, "squeeze", rec_mult,
            f"47 trades, 72.3% WR / +0.411R → {rec_mult:.2f}×",
        )
        try:
            got = _read_table_entry(tmp, "squeeze")
            assert abs(got - rec_mult) < 0.001, (
                f"Expected squeeze={rec_mult:.2f}, got {got:.2f} after patching"
            )
        finally:
            self._cleanup(tmp)

    def test_apply_gap_down_writes_correct_value(self, csm):
        """_apply_to_bot correctly patches 'gap_down' from 1.00 to 0.85."""
        rec_mult = 0.85
        tmp = self._run_apply(
            csm, "gap_down", rec_mult,
            "33 trades, 58.1% WR / +0.290R → 0.85×",
        )
        try:
            got = _read_table_entry(tmp, "gap_down")
            assert abs(got - 0.85) < 0.001, (
                f"Expected gap_down=0.85, got {got:.2f} after patching"
            )
        finally:
            self._cleanup(tmp)

    def test_apply_gap_writes_correct_value(self, csm):
        """_apply_to_bot correctly patches 'gap' to a new calibrated value."""
        rec_mult = 1.10
        tmp = self._run_apply(
            csm, "gap", rec_mult,
            "55 trades, 67.3% WR / +0.350R → 1.10×",
        )
        try:
            got = _read_table_entry(tmp, "gap")
            assert abs(got - 1.10) < 0.001, (
                f"Expected gap=1.10, got {got:.2f} after patching"
            )
        finally:
            self._cleanup(tmp)

    def test_apply_other_entries_untouched(self, csm):
        """
        Patching 'squeeze' must not change any other _SP_MULT_TABLE entry.
        The fixture has other=1.15, gap=1.00, trend=0.85, gap_down=1.00.
        """
        rec_mult = 1.15
        tmp = self._run_apply(
            csm, "squeeze", rec_mult,
            "47 trades, 72.3% WR / +0.411R → 1.15×",
        )
        try:
            assert abs(_read_table_entry(tmp, "other") - 1.15) < 0.001
            assert abs(_read_table_entry(tmp, "gap") - 1.00) < 0.001
            assert abs(_read_table_entry(tmp, "trend") - 0.85) < 0.001
            assert abs(_read_table_entry(tmp, "gap_down") - 1.00) < 0.001
        finally:
            self._cleanup(tmp)

    def test_apply_inline_comment_is_updated(self, csm):
        """The inline comment on the table entry line is replaced with the new text."""
        rec_mult = 1.15
        comment = "47 trades, 72.3% WR / +0.411R → 1.15×"
        tmp = self._run_apply(csm, "squeeze", rec_mult, comment)
        try:
            with open(tmp) as fh:
                content = fh.read()
            assert "72.3% WR" in content, (
                "Inline comment fragment '72.3% WR' not found in patched file"
            )
        finally:
            self._cleanup(tmp)

    def test_apply_with_citation_line_updates_header_comment(self, csm):
        """When citation_line is supplied, the header comment block is updated."""
        rec_mult = 1.15
        citation = "#   'squeeze' (2024-01-03 → 2024-12-31): 47 trades, 72.3% WR / +0.411R avg → 1.15×"
        tmp = self._run_apply(
            csm, "squeeze", rec_mult,
            "47 trades, 72.3% WR / +0.411R → 1.15×",
            citation_line=citation,
        )
        try:
            with open(tmp) as fh:
                content = fh.read()
            assert "47 trades, 72.3% WR / +0.411R avg → 1.15×" in content, (
                "Citation fragment not found in patched header comment"
            )
            assert "'squeeze':   0 settled trades as of 2026-04-20" not in content, (
                "Stale comment was not removed after citation_line was applied"
            )
        finally:
            self._cleanup(tmp)

    def test_apply_idempotent(self, csm):
        """Re-applying the same multiplier to the same file is safe and stable."""
        rec_mult = 1.15
        comment = "47 trades, 72.3% WR / +0.411R → 1.15×"
        citation = "#   'squeeze' (2024-01-03 → 2024-12-31): 47 trades, 72.3% WR / +0.411R avg → 1.15×"
        tmp = self._run_apply(
            csm, "squeeze", rec_mult, comment, citation_line=citation,
        )
        try:
            csm._apply_to_bot("squeeze", rec_mult, comment,
                               citation_line=citation, bot_path=tmp)
            got = _read_table_entry(tmp, "squeeze")
            assert abs(got - rec_mult) < 0.001, (
                f"Value drifted after idempotent re-apply: expected {rec_mult:.2f}, got {got:.2f}"
            )
        finally:
            self._cleanup(tmp)

    def test_apply_recommend_mult_then_write_round_trip(self, csm):
        """
        Full round-trip: _recommend_mult() produces a value → _apply_to_bot()
        writes it → the file is read back → the stored value equals the
        recommended value exactly (within float tolerance).

        This is the core regression guard: any break in the regex, rounding, or
        file-write chain causes this assertion to fail before bad data reaches
        live sizing in trade_utils.py.
        """
        pass_exp = 0.500
        gap_exp = 0.327
        rec_mult = csm._recommend_mult(pass_exp, gap_exp)
        comment = f"60 trades, 80.0% WR / +0.500R → {rec_mult:.2f}×"
        tmp = self._run_apply(csm, "other", rec_mult, comment)
        try:
            got = _read_table_entry(tmp, "other")
            assert abs(got - rec_mult) < 0.001, (
                f"Round-trip mismatch: _recommend_mult({pass_exp}, {gap_exp}) = {rec_mult:.2f} "
                f"but _SP_MULT_TABLE['other'] read back as {got:.2f}"
            )
        finally:
            self._cleanup(tmp)

    def test_apply_backup_file_is_created(self, csm):
        """_apply_to_bot must create a .bak file alongside the patched target."""
        rec_mult = 1.15
        tmp = self._run_apply(
            csm, "squeeze", rec_mult,
            "47 trades, 72.3% WR / +0.411R → 1.15×",
        )
        try:
            assert os.path.exists(tmp + ".bak"), (
                ".bak backup file was not created by _apply_to_bot()"
            )
        finally:
            self._cleanup(tmp)


# ---------------------------------------------------------------------------
# 5. TestResetPassToBaseline — end-to-end: reset → 1.00× baseline
# ---------------------------------------------------------------------------


class TestResetPassToBaseline:
    """
    End-to-end tests for _reset_pass_to_baseline().

    Each test writes the shared _APPLY_FIXTURE to a NamedTemporaryFile, calls
    _reset_pass_to_baseline() for a specific pass_name, then reads back the
    _SP_MULT_TABLE entry and asserts:
      - the stored value equals 1.00
      - the stale inline comment ("baseline; recalibrate once >=30 trades settle")
        is present on the table entry line
    Self-contained: uses tempfile, never touches real trade_utils.py or Supabase.
    """

    @staticmethod
    def _run_reset(csm, pass_name, fixture=None):
        """
        Write fixture to a temp file, call _reset_pass_to_baseline(), return the
        temp file path (caller is responsible for cleanup via try/finally).
        """
        if fixture is None:
            fixture = csm._APPLY_FIXTURE
        tf = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        try:
            tf.write(fixture)
            tf.close()
            csm._reset_pass_to_baseline(pass_name, bot_path=tf.name)
            return tf.name
        except Exception:
            try:
                os.unlink(tf.name)
            except OSError:
                pass
            try:
                os.unlink(tf.name + ".bak")
            except OSError:
                pass
            raise

    @staticmethod
    def _cleanup(path):
        for p in (path, path + ".bak"):
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_reset_trend_value_is_1_00(self, csm):
        """
        Resetting 'trend' (was 0.85 in _APPLY_FIXTURE) writes 1.00 to the table.
        """
        tmp = self._run_reset(csm, "trend")
        try:
            got = _read_table_entry(tmp, "trend")
            assert abs(got - 1.00) < 0.001, (
                f"Expected trend=1.00 after reset, got {got:.2f}"
            )
        finally:
            self._cleanup(tmp)

    def test_reset_trend_stale_inline_comment_present(self, csm):
        """
        After resetting 'trend', the stale inline comment is on the table entry line.
        """
        tmp = self._run_reset(csm, "trend")
        try:
            inline = csm._read_inline_comment("trend", bot_path=tmp)
            assert inline is not None, (
                "No inline comment found for 'trend' after reset"
            )
            assert "baseline; recalibrate once >=30 trades settle" in inline, (
                f"Stale inline comment not found for 'trend' after reset; got: {inline!r}"
            )
        finally:
            self._cleanup(tmp)

    def test_reset_other_value_is_1_00(self, csm):
        """
        Resetting 'other' (was 1.15 in _APPLY_FIXTURE) writes 1.00 to the table.
        """
        tmp = self._run_reset(csm, "other")
        try:
            got = _read_table_entry(tmp, "other")
            assert abs(got - 1.00) < 0.001, (
                f"Expected other=1.00 after reset, got {got:.2f}"
            )
        finally:
            self._cleanup(tmp)

    def test_reset_other_stale_inline_comment_present(self, csm):
        """
        After resetting 'other', the stale inline comment is on the table entry line.
        """
        tmp = self._run_reset(csm, "other")
        try:
            inline = csm._read_inline_comment("other", bot_path=tmp)
            assert inline is not None, (
                "No inline comment found for 'other' after reset"
            )
            assert "baseline; recalibrate once >=30 trades settle" in inline, (
                f"Stale inline comment not found for 'other' after reset; got: {inline!r}"
            )
        finally:
            self._cleanup(tmp)

    def test_reset_other_leaves_other_entries_untouched(self, csm):
        """
        Resetting 'other' must not change any other _SP_MULT_TABLE entry.
        The fixture has gap=1.00, trend=0.85, squeeze=1.00, gap_down=1.00.
        """
        tmp = self._run_reset(csm, "other")
        try:
            assert abs(_read_table_entry(tmp, "gap") - 1.00) < 0.001
            assert abs(_read_table_entry(tmp, "trend") - 0.85) < 0.001
            assert abs(_read_table_entry(tmp, "squeeze") - 1.00) < 0.001
            assert abs(_read_table_entry(tmp, "gap_down") - 1.00) < 0.001
        finally:
            self._cleanup(tmp)

    def test_reset_idempotent_already_stale_trend(self, csm):
        """
        Resetting a pass already at 1.00 with a stale comment is safe: the value
        stays 1.00 and the stale inline comment remains after the re-reset.
        """
        tmp = self._run_reset(csm, "trend", fixture=csm._APPLY_FIXTURE_TREND_STALE)
        try:
            got = _read_table_entry(tmp, "trend")
            assert abs(got - 1.00) < 0.001, (
                f"Expected trend=1.00 after idempotent re-reset, got {got:.2f}"
            )
            inline = csm._read_inline_comment("trend", bot_path=tmp)
            assert inline is not None, (
                "No inline comment found for 'trend' after idempotent re-reset"
            )
            assert "baseline; recalibrate once >=30 trades settle" in inline, (
                f"Stale inline comment missing after idempotent re-reset; got: {inline!r}"
            )
        finally:
            self._cleanup(tmp)
