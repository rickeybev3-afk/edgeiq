"""
Unit tests for IB Context Enrichment helpers in paper_trader_bot.py.

Tests cover:
  - _ib_context_score_delta: scoring logic, edge cases, toggle guard
  - _enrich_with_ib_context: field mutation, graceful failure paths
  - _fetch_prev_ib / _fetch_premarket_range: graceful fallback when
    data is unavailable (no Supabase/Alpaca connection required)
"""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported without live credentials
# ---------------------------------------------------------------------------
_STUB_MODULES = [
    "requests", "pytz", "supabase",
    "backend",
]
for _mod in _STUB_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytz as _pytz_mock
_pytz_mock.timezone = MagicMock(return_value=MagicMock())

import os
os.environ.setdefault("ALPACA_API_KEY", "test_key")
os.environ.setdefault("ALPACA_SECRET_KEY", "test_secret")

os.environ["IB_CONTEXT_ENABLED"] = "1"

import paper_trader_bot as _ptb


class TestIbContextScoreDelta(unittest.TestCase):
    """Tests for _ib_context_score_delta()."""

    def _call(self, **kw):
        defaults = dict(
            today_ib_range=1.0,
            prev_ib_range=1.0,
            pm_range_pct=0.5,
            direction="Bullish Break",
            pm_high=11.0,
            pm_low=10.5,
            prev_ib_high=12.0,
            prev_ib_low=10.0,
        )
        defaults.update(kw)
        return _ptb._ib_context_score_delta(**defaults)

    # ── toggle guard ────────────────────────────────────────────────────────

    def test_returns_zero_when_disabled(self):
        original = _ptb.IB_CONTEXT_ENABLED
        try:
            _ptb.IB_CONTEXT_ENABLED = False
            self.assertEqual(0, self._call())
        finally:
            _ptb.IB_CONTEXT_ENABLED = original

    # ── missing data guards ─────────────────────────────────────────────────

    def test_returns_zero_when_no_prev_ib_range(self):
        self.assertEqual(0, self._call(prev_ib_range=0.0))

    def test_returns_zero_when_no_today_ib_range(self):
        self.assertEqual(0, self._call(today_ib_range=0.0))

    # ── neutral ratio (0.70 ≤ ratio ≤ 1.30) ────────────────────────────────

    def test_neutral_ratio_no_pm_penalty(self):
        delta = self._call(
            today_ib_range=1.0, prev_ib_range=1.0, pm_range_pct=0.5,
            pm_high=11.0, pm_low=10.5, prev_ib_high=12.0, prev_ib_low=10.0,
        )
        self.assertEqual(0, delta)

    def test_neutral_ratio_with_large_pm_penalty(self):
        delta = self._call(
            today_ib_range=1.0, prev_ib_range=1.0, pm_range_pct=2.5,
            pm_high=11.0, pm_low=10.5, prev_ib_high=12.0, prev_ib_low=10.0,
        )
        self.assertEqual(-2, delta)

    # ── compression path ────────────────────────────────────────────────────

    def test_compression_pm_bullish_acceptance_gives_plus5(self):
        # pm_high >= prev_ib_high → bullish acceptance past prior IB → +5
        delta = self._call(
            today_ib_range=0.65,
            prev_ib_range=1.0,
            pm_range_pct=0.8,
            direction="Bullish Break",
            pm_high=12.0,       # pm_high >= prev_ib_high (12.0)
            pm_low=11.0,
            prev_ib_high=12.0,
            prev_ib_low=10.0,
        )
        self.assertEqual(5, delta)

    def test_compression_pm_bearish_acceptance_gives_plus5(self):
        # pm_low <= prev_ib_low → bearish acceptance below prior IB → +5
        delta = self._call(
            today_ib_range=0.65,
            prev_ib_range=1.0,
            pm_range_pct=0.8,
            direction="Bearish Break",
            pm_high=11.0,
            pm_low=10.0,        # pm_low <= prev_ib_low (10.0)
            prev_ib_high=12.0,
            prev_ib_low=10.0,
        )
        self.assertEqual(5, delta)

    def test_compression_pm_inside_prev_gives_plus3(self):
        # pm_high < prev_ib_high AND pm_low > prev_ib_low → contained → +3
        delta = self._call(
            today_ib_range=0.5,
            prev_ib_range=1.0,
            pm_range_pct=0.5,
            direction="Bullish Break",
            pm_high=11.5,       # < prev_ib_high (12.0)
            pm_low=10.5,        # > prev_ib_low  (10.0)
            prev_ib_high=12.0,
            prev_ib_low=10.0,
        )
        self.assertEqual(3, delta)

    def test_compression_no_pm_gives_plus2(self):
        # pm_range_pct == 0 → no pre-market data → base compression bonus +2
        delta = self._call(
            today_ib_range=0.5,
            prev_ib_range=1.0,
            pm_range_pct=0.0,
            pm_high=0.0,
            pm_low=0.0,
            prev_ib_high=12.0,
            prev_ib_low=10.0,
        )
        self.assertEqual(2, delta)

    def test_compression_pm_partial_gives_plus2(self):
        # PM straddles prior IB low but not high (not accepted, not inside) → +2
        delta = self._call(
            today_ib_range=0.5,
            prev_ib_range=1.0,
            pm_range_pct=0.8,
            direction="Bullish Break",
            pm_high=11.5,       # < prev_ib_high=12.0 → not bullish acceptance
            pm_low=9.5,         # < prev_ib_low=10.0  → not inside (pm_low < prev_ib_low)
            prev_ib_high=12.0,
            prev_ib_low=10.0,
        )
        self.assertEqual(2, delta)

    def test_compression_bullish_acceptance_plus_large_pm_penalty(self):
        # +5 (accepted) − 2 (pm_range_pct ≥ 2%) = +3
        delta = self._call(
            today_ib_range=0.65,
            prev_ib_range=1.0,
            pm_range_pct=3.0,
            direction="Bullish Break",
            pm_high=12.0,
            pm_low=11.0,
            prev_ib_high=12.0,
            prev_ib_low=10.0,
        )
        self.assertEqual(3, delta)

    def test_compression_inside_pm_large_penalty_net(self):
        # +3 (inside prev) − 2 (large pm) = +1
        delta = self._call(
            today_ib_range=0.65,
            prev_ib_range=1.0,
            pm_range_pct=3.0,
            direction="Bullish Break",
            pm_high=11.5,       # inside: < prev_ib_high=12.0
            pm_low=10.5,        # inside: > prev_ib_low=10.0
            prev_ib_high=12.0,
            prev_ib_low=10.0,
        )
        self.assertEqual(1, delta)

    # ── expansion path ──────────────────────────────────────────────────────

    def test_expansion_gives_minus3(self):
        delta = self._call(
            today_ib_range=1.5, prev_ib_range=1.0, pm_range_pct=0.5,
            pm_high=11.0, pm_low=10.5, prev_ib_high=12.0, prev_ib_low=10.0,
        )
        self.assertEqual(-3, delta)

    def test_expansion_plus_large_pm_clamped_to_minus5(self):
        delta = self._call(
            today_ib_range=1.5, prev_ib_range=1.0, pm_range_pct=3.0,
            pm_high=11.0, pm_low=10.5, prev_ib_high=12.0, prev_ib_low=10.0,
        )
        self.assertEqual(-5, delta)

    # ── clamp assertions ────────────────────────────────────────────────────

    def test_result_never_exceeds_plus5(self):
        delta = self._call(
            today_ib_range=0.65, prev_ib_range=1.0, pm_range_pct=0.8,
            direction="Bullish Break",
            pm_high=12.0, pm_low=11.0, prev_ib_high=12.0, prev_ib_low=10.0,
        )
        self.assertLessEqual(delta, 5)

    def test_result_never_below_minus5(self):
        delta = self._call(
            today_ib_range=2.0, prev_ib_range=1.0, pm_range_pct=5.0,
            pm_high=11.0, pm_low=10.5, prev_ib_high=12.0, prev_ib_low=10.0,
        )
        self.assertGreaterEqual(delta, -5)


class TestEnrichWithIbContext(unittest.TestCase):
    """Tests for _enrich_with_ib_context()."""

    def _make_result(self, **kw):
        base = dict(
            ticker="TEST",
            open_price=10.0,
            ib_high=11.0,
            ib_low=10.0,
            predicted="Bullish Break",
            tcs=60.0,
        )
        base.update(kw)
        return base

    def test_noop_when_disabled(self):
        original = _ptb.IB_CONTEXT_ENABLED
        try:
            _ptb.IB_CONTEXT_ENABLED = False
            r = self._make_result()
            _ptb._enrich_with_ib_context([r], "2026-04-20")
            self.assertNotIn("prev_ib_high", r)
        finally:
            _ptb.IB_CONTEXT_ENABLED = original

    def test_fields_set_to_none_when_no_data(self):
        with (
            patch.object(_ptb, "_fetch_prev_ib", return_value=(0.0, 0.0)),
            patch.object(_ptb, "_fetch_premarket_range", return_value=(0.0, 0.0, 0.0)),
            patch("time.sleep"),
        ):
            r = self._make_result()
            _ptb._enrich_with_ib_context([r], "2026-04-20")
        self.assertIsNone(r["prev_ib_high"])
        self.assertIsNone(r["prev_ib_low"])
        self.assertIsNone(r["pm_range_pct"])
        self.assertIsNone(r["ib_vs_prev_ib_pct"])

    def test_tcs_unchanged_when_no_prev_ib(self):
        with (
            patch.object(_ptb, "_fetch_prev_ib", return_value=(0.0, 0.0)),
            patch.object(_ptb, "_fetch_premarket_range", return_value=(0.0, 0.0, 0.0)),
            patch("time.sleep"),
        ):
            r = self._make_result(tcs=62.0)
            _ptb._enrich_with_ib_context([r], "2026-04-20")
        self.assertAlmostEqual(62.0, r["tcs"])

    def test_tcs_adjusted_on_compression_with_acceptance(self):
        # prev IB: 9.0–12.0 (range 3.0); today IB: 10.0–11.0 (range 1.0 → ratio=0.33 → compression)
        # pm_high=12.0 >= prev_ib_high=12.0 → bullish acceptance → +5
        with (
            patch.object(_ptb, "_fetch_prev_ib", return_value=(12.0, 9.0)),
            patch.object(_ptb, "_fetch_premarket_range", return_value=(0.8, 12.0, 10.5)),
            patch("time.sleep"),
        ):
            r = self._make_result(ib_high=11.0, ib_low=10.0, tcs=60.0, predicted="Bullish Break")
            _ptb._enrich_with_ib_context([r], "2026-04-20")
        self.assertGreater(r["tcs"], 60.0)

    def test_graceful_failure_leaves_nones(self):
        with (
            patch.object(_ptb, "_fetch_prev_ib", side_effect=RuntimeError("boom")),
            patch("time.sleep"),
        ):
            r = self._make_result()
            _ptb._enrich_with_ib_context([r], "2026-04-20")
        self.assertIsNone(r.get("prev_ib_high"))

    def test_ib_vs_prev_pct_computed_correctly(self):
        # today IB = 1.0, prev IB = 2.0 → ratio = 50%
        with (
            patch.object(_ptb, "_fetch_prev_ib", return_value=(12.0, 10.0)),
            patch.object(_ptb, "_fetch_premarket_range", return_value=(0.5, 11.0, 10.5)),
            patch("time.sleep"),
        ):
            r = self._make_result(ib_high=11.0, ib_low=10.0)
            _ptb._enrich_with_ib_context([r], "2026-04-20")
        self.assertAlmostEqual(50.0, r["ib_vs_prev_ib_pct"], places=1)


class TestFetchHelpersGracefulFallback(unittest.TestCase):
    """_fetch_prev_ib and _fetch_premarket_range must never raise."""

    def test_fetch_prev_ib_returns_zeros_on_supabase_error(self):
        original_client = _ptb._supabase_client
        try:
            _ptb._supabase_client = MagicMock()
            _ptb._supabase_client.table.side_effect = RuntimeError("db down")
            with patch("requests.get", side_effect=RuntimeError("net down")):
                result = _ptb._fetch_prev_ib("AAPL", "2026-04-20")
        finally:
            _ptb._supabase_client = original_client
        self.assertEqual((0.0, 0.0), result)

    def test_fetch_premarket_range_returns_zeros_on_error(self):
        with patch("requests.get", side_effect=RuntimeError("net down")):
            result = _ptb._fetch_premarket_range("AAPL", "2026-04-20", 10.0)
        self.assertEqual((0.0, 0.0, 0.0), result)

    def test_fetch_premarket_range_returns_zeros_when_open_zero(self):
        result = _ptb._fetch_premarket_range("AAPL", "2026-04-20", 0.0)
        self.assertEqual((0.0, 0.0, 0.0), result)

    def test_fetch_premarket_range_returns_tuple_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "bars": [{"h": 11.0, "l": 10.0}, {"h": 11.5, "l": 10.2}]
        }
        with patch("requests.get", return_value=mock_resp):
            pct, pm_high, pm_low = _ptb._fetch_premarket_range("AAPL", "2026-04-20", 10.0)
        self.assertAlmostEqual(11.5, pm_high)
        self.assertAlmostEqual(10.0, pm_low)
        self.assertGreater(pct, 0)

    def test_fetch_premarket_range_returns_zeros_on_empty_bars(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"bars": []}
        with patch("requests.get", return_value=mock_resp):
            result = _ptb._fetch_premarket_range("AAPL", "2026-04-20", 10.0)
        self.assertEqual((0.0, 0.0, 0.0), result)

    def test_fetch_prev_ib_returns_zeros_on_empty_alpaca_bars(self):
        original_client = _ptb._supabase_client
        try:
            _ptb._supabase_client = None
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"bars": []}
            with patch("requests.get", return_value=mock_resp):
                result = _ptb._fetch_prev_ib("AAPL", "2026-04-20")
        finally:
            _ptb._supabase_client = original_client
        self.assertEqual((0.0, 0.0), result)

    def test_fetch_prev_ib_uses_alpaca_bars_when_supabase_empty(self):
        original_client = _ptb._supabase_client
        try:
            _ptb._supabase_client = None
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"bars": [{"h": 15.5, "l": 14.0}]}
            with patch("requests.get", return_value=mock_resp):
                h, l = _ptb._fetch_prev_ib("AAPL", "2026-04-20")
        finally:
            _ptb._supabase_client = original_client
        self.assertAlmostEqual(15.5, h)
        self.assertAlmostEqual(14.0, l)


if __name__ == "__main__":
    unittest.main()
