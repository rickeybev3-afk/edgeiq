"""
Unit tests for Adaptive Position Management helpers in paper_trader_bot.py.

Tests cover:
  - _compute_adaptive_adjustments: all directional branches, edge cases,
    missing-data guards
  - _pre_open_position_review: toggle guard, no-position path, graceful
    failure paths (DB error, Alpaca error) — no live credentials required
  - _ensure_adaptive_mgmt_columns: column-present and column-missing paths
  - _fetch_pm_last_price: API success/failure/empty paths
"""
import sys
import datetime as _real_dt
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported without live credentials
# ---------------------------------------------------------------------------
_STUB_MODULES = [
    "requests", "pytz", "supabase",
    "backend", "log_utils", "log_config",
]
for _mod in _STUB_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytz as _pytz_mock
_pytz_mock.timezone = MagicMock(return_value=MagicMock())

import os
os.environ.setdefault("ALPACA_API_KEY",    "test_key")
os.environ.setdefault("ALPACA_SECRET_KEY", "test_secret")
os.environ["ADAPTIVE_POSITION_MGMT"] = "1"
# Must be set before paper_trader_bot is imported so IB_CONTEXT_ENABLED=True
# in the cached module object (prevents test_ib_context.py interference).
os.environ.setdefault("IB_CONTEXT_ENABLED", "1")

import paper_trader_bot as _ptb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_datetime_mock(date_str: str = "2026-04-21") -> MagicMock:
    """Return a mock that mimics datetime.now(tz) and datetime.strptime."""
    mock_now = MagicMock()
    mock_now.strftime.return_value = date_str
    mock_now.isoformat.return_value = f"{date_str}T08:30:00-04:00"
    mock_dt = MagicMock()
    mock_dt.now.return_value = mock_now
    mock_dt.strptime.side_effect = _real_dt.datetime.strptime
    return mock_dt


# ---------------------------------------------------------------------------
# _compute_adaptive_adjustments
# ---------------------------------------------------------------------------

class TestComputeAdaptiveAdjustments(unittest.TestCase):
    """Tests for _compute_adaptive_adjustments() — pure function, no I/O."""

    def _call(self, **kw):
        defaults = dict(
            direction="Bullish Break",
            entry=100.0,
            stop=98.0,
            target=104.0,
            ib_high=101.0,
            ib_low=99.0,
            pm_last=102.0,
        )
        defaults.update(kw)
        return _ptb._compute_adaptive_adjustments(**defaults)

    # ── missing / invalid data guards ───────────────────────────────────────

    def test_returns_none_when_entry_zero(self):
        self.assertIsNone(self._call(entry=0))

    def test_returns_none_when_stop_zero(self):
        self.assertIsNone(self._call(stop=0))

    def test_returns_none_when_target_zero(self):
        self.assertIsNone(self._call(target=0))

    def test_returns_none_when_pm_last_zero(self):
        self.assertIsNone(self._call(pm_last=0))

    def test_returns_none_when_ib_high_zero(self):
        self.assertIsNone(self._call(ib_high=0))

    def test_returns_none_when_ib_low_zero(self):
        self.assertIsNone(self._call(ib_low=0))

    def test_returns_none_when_ib_inverted(self):
        self.assertIsNone(self._call(ib_high=99.0, ib_low=101.0))

    def test_returns_none_when_stop_equals_entry(self):
        self.assertIsNone(self._call(entry=100.0, stop=100.0))

    # ── Bullish Break: PM above IB high ─────────────────────────────────────

    def test_bullish_pm_above_ib_raises_tp(self):
        # entry=100, stop=98, target=104, ib_high=101, pm_last=102 (> ib_high)
        # stop_dist = 2.0 → new_tp = 104 + 0.5*2 = 105
        result = self._call(
            direction="Bullish Break",
            entry=100.0, stop=98.0, target=104.0,
            ib_high=101.0, ib_low=99.0, pm_last=102.0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "TP_RAISED")
        self.assertAlmostEqual(result["new_tp"],   105.0, places=2)
        self.assertAlmostEqual(result["new_stop"],  98.0, places=2)  # unchanged
        # tp_adjusted_r = (105 - 100) / 2 = 2.5
        self.assertAlmostEqual(result["tp_adjusted_r"], 2.5, places=2)

    def test_bullish_pm_above_ib_tp_r_correct(self):
        # Verify R calculation with non-round numbers
        result = self._call(
            direction="Bullish Break",
            entry=50.0, stop=49.0, target=52.0,
            ib_high=51.0, ib_low=49.5, pm_last=51.5,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "TP_RAISED")
        # stop_dist=1.0, new_tp = 52 + 0.5*1 = 52.5
        self.assertAlmostEqual(result["new_tp"], 52.5, places=2)
        # tp_r = (52.5 - 50) / 1 = 2.5
        self.assertAlmostEqual(result["tp_adjusted_r"], 2.5, places=2)

    # ── Bullish Break: PM inside IB ──────────────────────────────────────────

    def test_bullish_pm_inside_ib_tightens_stop(self):
        # pm_last=100.5 (inside IB, above ib_mid=100) → tighten stop to IB mid
        # ib_mid = (101 + 99) / 2 = 100.0; ib_mid < pm_last → valid
        result = self._call(
            direction="Bullish Break",
            entry=100.0, stop=98.0, target=104.0,
            ib_high=101.0, ib_low=99.0, pm_last=100.5,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "STOP_TIGHTENED")
        self.assertAlmostEqual(result["new_stop"], 100.0, places=2)
        self.assertAlmostEqual(result["new_tp"],   104.0, places=2)  # unchanged
        # tp_r = (104 - 100) / 2 = 2.0
        self.assertAlmostEqual(result["tp_adjusted_r"], 2.0, places=2)

    def test_bullish_stop_tighten_skipped_when_pm_below_ib_mid(self):
        # pm_last=99.5 < ib_mid=100 → stop at 100 would be ABOVE market price
        # (triggers immediately for long) → None
        result = self._call(
            direction="Bullish Break",
            entry=100.0, stop=98.0, target=104.0,
            ib_high=101.0, ib_low=99.0, pm_last=99.5,
        )
        self.assertIsNone(result)

    def test_bullish_pm_exactly_at_ib_high_returns_none(self):
        # pm_last == ib_high → neither branch → None
        result = self._call(
            direction="Bullish Break",
            entry=100.0, stop=98.0, target=104.0,
            ib_high=101.0, ib_low=99.0, pm_last=101.0,
        )
        self.assertIsNone(result)

    def test_bullish_stop_unchanged_when_tp_raised(self):
        result = self._call(
            direction="Bullish Break",
            entry=200.0, stop=196.0, target=208.0,
            ib_high=202.0, ib_low=198.0, pm_last=204.0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "TP_RAISED")
        self.assertAlmostEqual(result["new_stop"], 196.0, places=2)  # unchanged

    # ── Bearish Break: PM below IB low ───────────────────────────────────────

    def test_bearish_pm_below_ib_lowers_tp(self):
        # entry=100, stop=102, target=96, ib_low=99, pm_last=98 (< ib_low)
        # stop_dist = |100 - 102| = 2.0 → new_tp = 96 - 0.5*2 = 95
        result = self._call(
            direction="Bearish Break",
            entry=100.0, stop=102.0, target=96.0,
            ib_high=101.0, ib_low=99.0, pm_last=98.0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "TP_RAISED")
        self.assertAlmostEqual(result["new_tp"],   95.0, places=2)
        self.assertAlmostEqual(result["new_stop"], 102.0, places=2)  # unchanged
        # tp_r = (100 - 95) / 2 = 2.5
        self.assertAlmostEqual(result["tp_adjusted_r"], 2.5, places=2)

    def test_bearish_pm_inside_ib_tightens_stop(self):
        # pm_last=99.7 (inside IB, below ib_mid=100) → tighten stop to IB mid
        # ib_mid = (101 + 99) / 2 = 100.0; ib_mid > pm_last → valid for short
        result = self._call(
            direction="Bearish Break",
            entry=100.0, stop=102.0, target=96.0,
            ib_high=101.0, ib_low=99.0, pm_last=99.7,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "STOP_TIGHTENED")
        self.assertAlmostEqual(result["new_stop"], 100.0, places=2)
        self.assertAlmostEqual(result["new_tp"],    96.0, places=2)  # unchanged
        # tp_r = (100 - 96) / 2 = 2.0
        self.assertAlmostEqual(result["tp_adjusted_r"], 2.0, places=2)

    def test_bearish_stop_tighten_skipped_when_pm_above_ib_mid(self):
        # pm_last=100.5 > ib_mid=100 → stop at 100 would be BELOW market price
        # (triggers immediately for short) → None
        result = self._call(
            direction="Bearish Break",
            entry=100.0, stop=102.0, target=96.0,
            ib_high=101.0, ib_low=99.0, pm_last=100.5,
        )
        self.assertIsNone(result)

    def test_bearish_pm_exactly_at_ib_low_returns_none(self):
        result = self._call(
            direction="Bearish Break",
            entry=100.0, stop=102.0, target=96.0,
            ib_high=101.0, ib_low=99.0, pm_last=99.0,
        )
        self.assertIsNone(result)

    def test_bearish_stop_unchanged_when_tp_raised(self):
        result = self._call(
            direction="Bearish Break",
            entry=50.0, stop=53.0, target=44.0,
            ib_high=51.5, ib_low=49.0, pm_last=47.0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "TP_RAISED")
        self.assertAlmostEqual(result["new_stop"], 53.0, places=2)  # unchanged

    # ── Non-directional structures ────────────────────────────────────────────

    def test_non_directional_returns_none(self):
        for direction in ("Neutral", "Double Distribution", "NTRL Extreme", ""):
            with self.subTest(direction=direction):
                self.assertIsNone(self._call(direction=direction))

    # ── Result dict has all required keys ────────────────────────────────────

    def test_result_has_required_keys(self):
        result = self._call()
        self.assertIn("action",        result)
        self.assertIn("new_stop",      result)
        self.assertIn("new_tp",        result)
        self.assertIn("tp_adjusted_r", result)


# ---------------------------------------------------------------------------
# _pre_open_position_review — integration-level unit tests (mocked deps)
# ---------------------------------------------------------------------------

class TestPreOpenPositionReview(unittest.TestCase):
    """Tests for _pre_open_position_review() with mocked Alpaca and Supabase."""

    def setUp(self):
        self._orig_toggle  = _ptb.ADAPTIVE_POSITION_MGMT
        self._orig_api_key = _ptb.ALPACA_API_KEY
        _ptb.ADAPTIVE_POSITION_MGMT = True
        _ptb.ALPACA_API_KEY = "test_key"

    def tearDown(self):
        _ptb.ADAPTIVE_POSITION_MGMT = self._orig_toggle
        _ptb.ALPACA_API_KEY = self._orig_api_key

    # A shared context manager that patches datetime.now for EASTERN
    def _dt_ctx(self):
        return patch("paper_trader_bot.datetime", _make_datetime_mock())

    def test_noop_when_toggle_disabled(self):
        _ptb.ADAPTIVE_POSITION_MGMT = False
        with patch.object(_ptb, "_alpaca_get_positions") as mock_pos:
            _ptb._pre_open_position_review()
            mock_pos.assert_not_called()

    def test_noop_when_no_open_positions(self):
        with self._dt_ctx():
            with patch.object(_ptb, "_alpaca_get_positions", return_value=[]):
                with patch.object(_ptb, "_fetch_pm_last_price") as mock_pm:
                    _ptb._pre_open_position_review()
                    mock_pm.assert_not_called()

    def test_skips_position_when_no_paper_trade_row(self):
        """If there is no open paper_trade for the ticker today, skip quietly."""
        mock_sb = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = []
        mock_sb.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .is_.return_value.order.return_value.limit.return_value.execute.return_value = resp_mock

        with self._dt_ctx():
            with patch.object(_ptb, "_supabase_client", mock_sb):
                with patch.object(_ptb, "_alpaca_get_positions", return_value=[
                    {"symbol": "AAPL", "side": "long", "qty": "10"}
                ]):
                    with patch.object(_ptb, "_fetch_pm_last_price") as mock_pm:
                        _ptb._pre_open_position_review()
                        mock_pm.assert_not_called()

    def test_skips_when_incomplete_trade_data(self):
        """Rows missing ib_high/entry/etc cause a quiet skip, no order changes."""
        mock_sb = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = [{
            "id": "1", "ib_high": None, "ib_low": None,
            "entry_price_sim": 0, "stop_price_sim": 0,
            "target_price_sim": 0, "predicted": "",
        }]
        mock_sb.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .is_.return_value.order.return_value.limit.return_value.execute.return_value = resp_mock

        with self._dt_ctx():
            with patch.object(_ptb, "_supabase_client", mock_sb):
                with patch.object(_ptb, "_alpaca_get_positions", return_value=[
                    {"symbol": "TSLA", "side": "long", "qty": "5"}
                ]):
                    with patch.object(_ptb, "_alpaca_cancel_orders_for_ticker") as mock_cancel:
                        _ptb._pre_open_position_review()
                        mock_cancel.assert_not_called()

    def test_skips_when_pm_price_unavailable(self):
        """When _fetch_pm_last_price returns 0, position is left unchanged."""
        mock_sb = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = [{
            "id": "1",
            "ib_high": 101.0, "ib_low": 99.0,
            "entry_price_sim": 100.0, "stop_price_sim": 98.0,
            "target_price_sim": 104.0,
            "predicted": "Bullish Break",
        }]
        mock_sb.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .is_.return_value.order.return_value.limit.return_value.execute.return_value = resp_mock

        with self._dt_ctx():
            with patch.object(_ptb, "_supabase_client", mock_sb):
                with patch.object(_ptb, "_alpaca_get_positions", return_value=[
                    {"symbol": "MSFT", "side": "long", "qty": "3"}
                ]):
                    with patch.object(_ptb, "_fetch_pm_last_price", return_value=0.0):
                        with patch.object(_ptb, "_alpaca_cancel_orders_for_ticker") as mock_cancel:
                            _ptb._pre_open_position_review()
                            mock_cancel.assert_not_called()

    def test_raises_tp_when_bullish_pm_above_ib(self):
        """Full happy-path: PM above IB high → old IDs snapshotted and cancelled
        first (cancel-first), then OCO placed with raised TP."""
        mock_sb = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = [{
            "id": "row-1",
            "ib_high": 101.0, "ib_low": 99.0,
            "entry_price_sim": 100.0, "stop_price_sim": 98.0,
            "target_price_sim": 104.0,
            "predicted": "Bullish Break",
        }]
        mock_sb.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .is_.return_value.order.return_value.limit.return_value.execute.return_value = resp_mock

        old_ids = ["old-stop-1", "old-tp-1"]

        with self._dt_ctx():
            with patch.object(_ptb, "_supabase_client", mock_sb):
                with patch.object(_ptb, "_alpaca_get_positions", return_value=[
                    {"symbol": "AAPL", "side": "long", "qty": "5"}
                ]):
                    with patch.object(_ptb, "_fetch_pm_last_price", return_value=102.0):
                        with patch.object(_ptb, "_alpaca_get_open_order_ids", return_value=old_ids):
                            with patch.object(
                                _ptb, "_alpaca_place_oco_exit",
                                return_value={"ok": True, "order_id": "abc123"},
                            ) as mock_oco:
                                with patch.object(_ptb, "_alpaca_cancel_orders_for_ticker", return_value=2) as mock_cancel:
                                    with patch.object(_ptb, "tg_send"):
                                        _ptb._pre_open_position_review()

        # Cancel called FIRST (cancel-first), then OCO placed with raised TP
        mock_cancel.assert_called_once_with("AAPL", specific_ids=old_ids)
        mock_oco.assert_called_once()
        # TP should be 104 + 0.5*2 = 105; stop unchanged = 98
        kw = mock_oco.call_args.kwargs
        self.assertAlmostEqual(kw["tp_price"],   105.0, places=2)
        self.assertAlmostEqual(kw["stop_price"],  98.0, places=2)
        self.assertEqual(kw["exit_side"], "sell")

    def test_tightens_stop_when_bearish_pm_inside_ib(self):
        """Bearish position with PM inside IB (below ib_mid) → cancel-first,
        OCO placed with stop at IB mid, only old IDs cancelled."""
        mock_sb = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = [{
            "id": "row-3",
            "ib_high": 101.0, "ib_low": 99.0,
            "entry_price_sim": 100.0, "stop_price_sim": 102.0,
            "target_price_sim": 96.0,
            "predicted": "Bearish Break",
        }]
        mock_sb.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .is_.return_value.order.return_value.limit.return_value.execute.return_value = resp_mock

        old_ids = ["old-stop-spy-1", "old-limit-spy-1"]

        with self._dt_ctx():
            with patch.object(_ptb, "_supabase_client", mock_sb):
                with patch.object(_ptb, "_alpaca_get_positions", return_value=[
                    {"symbol": "SPY", "side": "short", "qty": "10"}
                ]):
                    # pm_last=99.7 < ib_mid=100 → valid for bearish stop-tighten
                    with patch.object(_ptb, "_fetch_pm_last_price", return_value=99.7):
                        with patch.object(_ptb, "_alpaca_get_open_order_ids", return_value=old_ids):
                            with patch.object(_ptb, "_alpaca_cancel_orders_for_ticker", return_value=1) as mock_cancel:
                                with patch.object(
                                    _ptb, "_alpaca_place_oco_exit",
                                    return_value={"ok": True, "order_id": "xyz456"},
                                ) as mock_oco:
                                    with patch.object(_ptb, "tg_send"):
                                        _ptb._pre_open_position_review()

        # Cancel called FIRST with old IDs, then OCO placed
        mock_cancel.assert_called_once_with("SPY", specific_ids=old_ids)
        mock_oco.assert_called_once()
        kw = mock_oco.call_args.kwargs
        # ib_mid = (101+99)/2 = 100; stop → 100
        self.assertAlmostEqual(kw["stop_price"], 100.0, places=2)
        self.assertAlmostEqual(kw["tp_price"],    96.0, places=2)  # unchanged
        self.assertEqual(kw["exit_side"], "buy")  # cover short

    def test_oco_failure_triggers_rollback_bracket_restored(self):
        """When adaptive OCO fails, a rollback OCO is placed with original
        stop/target; Telegram warns about the restored bracket; DB is NOT updated."""
        mock_sb = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = [{
            "id": "row-2",
            "ib_high": 101.0, "ib_low": 99.0,
            "entry_price_sim": 100.0, "stop_price_sim": 98.0,
            "target_price_sim": 104.0,
            "predicted": "Bullish Break",
        }]
        mock_sb.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .is_.return_value.order.return_value.limit.return_value.execute.return_value = resp_mock

        # OCO call 1 = adaptive fails; OCO call 2 = rollback succeeds
        oco_side_effects = [
            {"ok": False, "error": "HTTP 422"},
            {"ok": True, "order_id": "rollback-555"},
        ]
        with self._dt_ctx():
            with patch.object(_ptb, "_supabase_client", mock_sb):
                with patch.object(_ptb, "_alpaca_get_positions", return_value=[
                    {"symbol": "AAPL", "side": "long", "qty": "5"}
                ]):
                    with patch.object(_ptb, "_fetch_pm_last_price", return_value=102.0):
                        with patch.object(_ptb, "_alpaca_get_open_order_ids", return_value=["old-1"]):
                            with patch.object(_ptb, "_alpaca_cancel_orders_for_ticker", return_value=1) as mock_cancel:
                                with patch.object(
                                    _ptb, "_alpaca_place_oco_exit",
                                    side_effect=oco_side_effects,
                                ) as mock_oco:
                                    with patch.object(_ptb, "tg_send") as mock_tg:
                                        _ptb._pre_open_position_review()

        # Cancel called FIRST (cancel-first pattern)
        mock_cancel.assert_called_once_with("AAPL", specific_ids=["old-1"])
        # OCO called twice: adaptive attempt + rollback
        self.assertEqual(mock_oco.call_count, 2)
        # Second call must use ORIGINAL stop and target
        rollback_kw = mock_oco.call_args_list[1].kwargs
        self.assertAlmostEqual(rollback_kw["stop_price"],  98.0, places=2)
        self.assertAlmostEqual(rollback_kw["tp_price"],   104.0, places=2)
        # Telegram should mention the bracket was restored
        tg_calls = " ".join(str(c) for c in mock_tg.call_args_list)
        self.assertIn("Bracket Restored", tg_calls)
        # DB update must NOT be called (adjustment was not applied)
        mock_sb.table.return_value.update.assert_not_called()

    def test_oco_failure_rollback_also_fails_sends_critical_alert(self):
        """When both adaptive OCO and rollback OCO fail, a CRITICAL Telegram
        alert is sent warning that the position may be unprotected."""
        mock_sb = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = [{
            "id": "row-9",
            "ib_high": 101.0, "ib_low": 99.0,
            "entry_price_sim": 100.0, "stop_price_sim": 98.0,
            "target_price_sim": 104.0,
            "predicted": "Bullish Break",
        }]
        mock_sb.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .is_.return_value.order.return_value.limit.return_value.execute.return_value = resp_mock

        # Both OCO calls fail
        oco_side_effects = [
            {"ok": False, "error": "HTTP 422"},
            {"ok": False, "error": "HTTP 422"},
        ]
        with self._dt_ctx():
            with patch.object(_ptb, "_supabase_client", mock_sb):
                with patch.object(_ptb, "_alpaca_get_positions", return_value=[
                    {"symbol": "AAPL", "side": "long", "qty": "5"}
                ]):
                    with patch.object(_ptb, "_fetch_pm_last_price", return_value=102.0):
                        with patch.object(_ptb, "_alpaca_get_open_order_ids", return_value=["old-1"]):
                            with patch.object(_ptb, "_alpaca_cancel_orders_for_ticker", return_value=1):
                                with patch.object(
                                    _ptb, "_alpaca_place_oco_exit",
                                    side_effect=oco_side_effects,
                                ) as mock_oco:
                                    with patch.object(_ptb, "tg_send") as mock_tg:
                                        _ptb._pre_open_position_review()

        # Both OCO attempts called
        self.assertEqual(mock_oco.call_count, 2)
        # Telegram must fire the CRITICAL / UNPROTECTED alert
        tg_calls = " ".join(str(c) for c in mock_tg.call_args_list)
        self.assertIn("CRITICAL", tg_calls)
        self.assertIn("UNPROTECTED", tg_calls)
        # DB update must NOT be called
        mock_sb.table.return_value.update.assert_not_called()

    def test_positions_fetch_error_is_graceful(self):
        """Exception from _alpaca_get_positions should not propagate."""
        with self._dt_ctx():
            with patch.object(_ptb, "_alpaca_get_positions", side_effect=RuntimeError("network")):
                _ptb._pre_open_position_review()  # must not raise

    def test_db_update_written_on_success(self):
        """On OCO success the paper_trades row is updated with adaptive fields."""
        mock_sb = MagicMock()
        resp_mock = MagicMock()
        resp_mock.data = [{
            "id": "row-4",
            "ib_high": 101.0, "ib_low": 99.0,
            "entry_price_sim": 100.0, "stop_price_sim": 98.0,
            "target_price_sim": 104.0,
            "predicted": "Bullish Break",
        }]
        mock_sb.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .is_.return_value.order.return_value.limit.return_value.execute.return_value = resp_mock

        with self._dt_ctx():
            with patch.object(_ptb, "_supabase_client", mock_sb):
                with patch.object(_ptb, "_alpaca_get_positions", return_value=[
                    {"symbol": "NVDA", "side": "long", "qty": "2"}
                ]):
                    with patch.object(_ptb, "_fetch_pm_last_price", return_value=102.0):
                        with patch.object(_ptb, "_alpaca_get_open_order_ids", return_value=["nvda-old-1"]):
                            with patch.object(_ptb, "_alpaca_cancel_orders_for_ticker", return_value=2):
                                with patch.object(
                                    _ptb, "_alpaca_place_oco_exit",
                                    return_value={"ok": True, "order_id": "def789"},
                                ):
                                    with patch.object(_ptb, "tg_send"):
                                        _ptb._pre_open_position_review()

        # Verify DB update was called with mgmt_mode='adaptive'
        mock_sb.table.return_value.update.assert_called_once()
        update_payload = mock_sb.table.return_value.update.call_args.args[0]
        self.assertEqual(update_payload["mgmt_mode"], "adaptive")
        self.assertIn("tp_adjusted_r", update_payload)


# ---------------------------------------------------------------------------
# _ensure_adaptive_mgmt_columns
# ---------------------------------------------------------------------------

class TestEnsureAdaptiveMgmtColumns(unittest.TestCase):

    def test_returns_true_when_no_supabase(self):
        orig = _ptb._supabase_client
        try:
            _ptb._supabase_client = None
            self.assertTrue(_ptb._ensure_adaptive_mgmt_columns())
        finally:
            _ptb._supabase_client = orig

    def test_returns_true_when_columns_present(self):
        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock()
        orig = _ptb._supabase_client
        try:
            _ptb._supabase_client = mock_sb
            self.assertTrue(_ptb._ensure_adaptive_mgmt_columns())
        finally:
            _ptb._supabase_client = orig

    def test_returns_false_when_columns_missing(self):
        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.limit.return_value.execute.side_effect = \
            Exception("column mgmt_mode does not exist")
        orig = _ptb._supabase_client
        orig_toggle = _ptb.ADAPTIVE_POSITION_MGMT
        try:
            _ptb._supabase_client = mock_sb
            _ptb.ADAPTIVE_POSITION_MGMT = True
            self.assertFalse(_ptb._ensure_adaptive_mgmt_columns())
        finally:
            _ptb._supabase_client = orig
            _ptb.ADAPTIVE_POSITION_MGMT = orig_toggle

    def test_returns_true_on_unexpected_error(self):
        """Non-column errors should not block startup — return True."""
        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.limit.return_value.execute.side_effect = \
            Exception("connection refused")
        orig = _ptb._supabase_client
        try:
            _ptb._supabase_client = mock_sb
            self.assertTrue(_ptb._ensure_adaptive_mgmt_columns())
        finally:
            _ptb._supabase_client = orig


# ---------------------------------------------------------------------------
# _fetch_pm_last_price — graceful-fallback tests
# ---------------------------------------------------------------------------

class TestFetchPmLastPrice(unittest.TestCase):
    """Tests for _fetch_pm_last_price — patches datetime and requests."""

    def _dt_ctx(self):
        return patch("paper_trader_bot.datetime", _make_datetime_mock())

    def test_returns_zero_when_request_fails(self):
        import requests as _req
        with self._dt_ctx():
            with patch.object(_req, "get", side_effect=OSError("timeout")):
                result = _ptb._fetch_pm_last_price("AAPL")
        self.assertEqual(result, 0.0)

    def test_returns_zero_when_no_bars(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"bars": []}
        import requests as _req
        with self._dt_ctx():
            with patch.object(_req, "get", return_value=mock_resp):
                result = _ptb._fetch_pm_last_price("AAPL")
        self.assertEqual(result, 0.0)

    def test_returns_last_bar_close(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "bars": [
                {"t": "2026-04-21T04:01:00Z", "o": 100.0, "h": 101.0, "l": 99.5, "c": 100.5},
                {"t": "2026-04-21T04:02:00Z", "o": 100.5, "h": 102.0, "l": 100.0, "c": 101.75},
            ]
        }
        import requests as _req
        with self._dt_ctx():
            with patch.object(_req, "get", return_value=mock_resp):
                result = _ptb._fetch_pm_last_price("AAPL")
        self.assertAlmostEqual(result, 101.75, places=2)

    def test_returns_zero_on_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        import requests as _req
        with self._dt_ctx():
            with patch.object(_req, "get", return_value=mock_resp):
                result = _ptb._fetch_pm_last_price("AAPL")
        self.assertEqual(result, 0.0)

    def test_returns_single_bar_close(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "bars": [{"t": "2026-04-21T04:01:00Z", "o": 55.0, "h": 56.0, "l": 54.5, "c": 55.5}]
        }
        import requests as _req
        with self._dt_ctx():
            with patch.object(_req, "get", return_value=mock_resp):
                result = _ptb._fetch_pm_last_price("TSLA")
        self.assertAlmostEqual(result, 55.5, places=2)


if __name__ == "__main__":
    unittest.main()
