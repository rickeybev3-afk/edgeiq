"""
Tests for the sample-size guard in calibrate_adaptive_mgmt.py.

Two layers:
  1. test_below_threshold_* — verify that when fewer than MIN_TRADES (50)
     settled adaptive trades are available, main() calls sys.exit(0) and
     never writes a patch to adaptive_exits.json.
  2. test_exactly/above_threshold_* — verify that MIN_TRADES or more trades
     allow calibration to proceed past the guard without a premature exit.

All tests use synthetic rows; no Supabase access is required.
"""

import importlib
import sys
import unittest.mock as mock

import pytest


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------

def _load_module():
    """
    Import calibrate_adaptive_mgmt without a live Supabase connection.

    The module uses a lazy _require_supabase() helper that is only called
    during live calibration runs, so importing the module itself is safe
    without any backend stubs.
    """
    mod_name = "calibrate_adaptive_mgmt"
    previously_loaded = sys.modules.pop(mod_name, None)
    try:
        mod = importlib.import_module(mod_name)
        return mod
    finally:
        sys.modules.pop(mod_name, None)
        if previously_loaded is not None:
            sys.modules[mod_name] = previously_loaded


@pytest.fixture(scope="module")
def cam():
    """Module-scoped fixture: the imported calibrate_adaptive_mgmt module."""
    return _load_module()


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _synthetic_adaptive_rows(n):
    """
    Return n minimal synthetic settled adaptive rows.

    Columns match what _fetch_adaptive_settled() returns.  All price /
    IB columns are None so that _filter_tp_raised() and
    _filter_stop_tightened() classify every row as unclassifiable —
    the tests that exercise the guard don't need real classification.
    """
    rows = []
    for i in range(n):
        rows.append({
            "id": i,
            "win_loss": "Win" if i % 2 == 0 else "Loss",
            "tiered_pnl_r": 1.5 if i % 2 == 0 else -1.0,
            "tp_adjusted_r": None,
            "entry_price_sim": None,
            "stop_price_sim": None,
            "target_price_sim": None,
            "ib_high": None,
            "ib_low": None,
            "trade_date": f"2024-{(i % 12) + 1:02d}-01",
        })
    return rows


# ---------------------------------------------------------------------------
# TestMinTradesGuard
# ---------------------------------------------------------------------------

class TestMinTradesGuard:
    """
    Verify the early-exit guard in main():
      n_all < MIN_TRADES (50) → sys.exit(0), _apply_to_config never called
      n_all >= MIN_TRADES     → guard passes, calibration proceeds
    """

    def test_below_threshold_exits_zero_no_patch_written(self, cam):
        """49 rows (< 50): sys.exit(0) and _apply_to_config is never called."""
        rows = _synthetic_adaptive_rows(49)
        with mock.patch.object(cam, "_fetch_adaptive_settled", return_value=rows):
            with mock.patch.object(cam, "_apply_to_config") as mock_apply:
                with mock.patch("sys.argv", ["calibrate_adaptive_mgmt.py", "--apply"]):
                    with pytest.raises(SystemExit) as exc_info:
                        cam.main()
                    assert exc_info.value.code == 0, (
                        "Expected sys.exit(0) when n_all < MIN_TRADES"
                    )
                    mock_apply.assert_not_called()

    def test_below_threshold_prints_status_message(self, cam, capsys):
        """A human-readable status message mentioning the count and threshold is printed."""
        rows = _synthetic_adaptive_rows(10)
        with mock.patch.object(cam, "_fetch_adaptive_settled", return_value=rows):
            with mock.patch("sys.argv", ["calibrate_adaptive_mgmt.py"]):
                with pytest.raises(SystemExit):
                    cam.main()
        captured = capsys.readouterr()
        assert "10" in captured.out, (
            "Output should mention the actual trade count (10)"
        )
        assert str(cam.MIN_TRADES) in captured.out, (
            f"Output should mention the minimum threshold ({cam.MIN_TRADES})"
        )

    def test_zero_rows_aborts_no_patch_written(self, cam):
        """Zero adaptive rows → sys.exit(0), _apply_to_config never called."""
        with mock.patch.object(cam, "_fetch_adaptive_settled", return_value=[]):
            with mock.patch.object(cam, "_apply_to_config") as mock_apply:
                with mock.patch("sys.argv", ["calibrate_adaptive_mgmt.py", "--apply"]):
                    with pytest.raises(SystemExit) as exc_info:
                        cam.main()
                    assert exc_info.value.code == 0
                    mock_apply.assert_not_called()

    def test_exactly_min_trades_proceeds_and_applies(self, cam):
        """
        n_all == MIN_TRADES (50): guard passes, calibration runs, and
        _apply_to_config is called when --apply is supplied.
        """
        rows = _synthetic_adaptive_rows(50)
        with mock.patch.object(cam, "_fetch_adaptive_settled", return_value=rows):
            with mock.patch.object(cam, "_filter_tp_raised", return_value=rows):
                with mock.patch.object(cam, "_filter_stop_tightened", return_value=[]):
                    with mock.patch.object(cam, "_fetch_fixed_settled", return_value=[]):
                        with mock.patch.object(cam, "_grid_search", return_value={}):
                            with mock.patch.object(cam, "_best_mult", return_value=cam.DEFAULT_MULT):
                                with mock.patch.object(cam, "_print_report"):
                                    with mock.patch.object(cam, "_apply_to_config") as mock_apply:
                                        with mock.patch("sys.argv", ["calibrate_adaptive_mgmt.py", "--apply"]):
                                            cam.main()
                                        mock_apply.assert_called_once()

    def test_above_threshold_proceeds_no_premature_exit(self, cam):
        """
        n_all > MIN_TRADES: the guard is not triggered and main() completes
        without raising SystemExit from the sample-size check.
        """
        rows = _synthetic_adaptive_rows(75)
        with mock.patch.object(cam, "_fetch_adaptive_settled", return_value=rows):
            with mock.patch.object(cam, "_filter_tp_raised", return_value=rows[:40]):
                with mock.patch.object(cam, "_filter_stop_tightened", return_value=[]):
                    with mock.patch.object(cam, "_fetch_fixed_settled", return_value=[]):
                        with mock.patch.object(cam, "_grid_search", return_value={}):
                            with mock.patch.object(cam, "_best_mult", return_value=cam.DEFAULT_MULT):
                                with mock.patch.object(cam, "_print_report"):
                                    with mock.patch("sys.argv", ["calibrate_adaptive_mgmt.py"]):
                                        cam.main()

    def test_boundary_49_exits_50_proceeds(self, cam):
        """
        Boundary sweep: 49 rows fires the guard (exit 0); 50 rows passes it.
        """
        with mock.patch.object(cam, "_fetch_adaptive_settled", return_value=_synthetic_adaptive_rows(49)):
            with mock.patch("sys.argv", ["calibrate_adaptive_mgmt.py"]):
                with pytest.raises(SystemExit) as exc_info:
                    cam.main()
                assert exc_info.value.code == 0, (
                    "49 rows should trigger sys.exit(0)"
                )

        rows_50 = _synthetic_adaptive_rows(50)
        with mock.patch.object(cam, "_fetch_adaptive_settled", return_value=rows_50):
            with mock.patch.object(cam, "_filter_tp_raised", return_value=rows_50):
                with mock.patch.object(cam, "_filter_stop_tightened", return_value=[]):
                    with mock.patch.object(cam, "_fetch_fixed_settled", return_value=[]):
                        with mock.patch.object(cam, "_grid_search", return_value={}):
                            with mock.patch.object(cam, "_best_mult", return_value=cam.DEFAULT_MULT):
                                with mock.patch.object(cam, "_print_report"):
                                    with mock.patch("sys.argv", ["calibrate_adaptive_mgmt.py"]):
                                        cam.main()
