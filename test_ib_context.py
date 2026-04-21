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
    "backend", "log_utils", "log_config",
]
for _mod in _STUB_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# pytz must expose timezone() so the module-level EASTERN constant can be set
import pytz as _pytz_mock
_pytz_mock.timezone = MagicMock(return_value=MagicMock())

# Provide os.getenv defaults so no real env is needed
import os
os.environ.setdefault("ALPACA_API_KEY", "test_key")
os.environ.setdefault("ALPACA_SECRET_KEY", "test_secret")

# Force IB_CONTEXT_ENABLED=1 for tests that exercise the active-path logic
os.environ["IB_CONTEXT_ENABLED"] = "1"

# Import only the functions under test via targeted attribute access
# (avoids triggering the full main() / startup side-effects)
import importlib
import paper_trader_bot as _ptb


class TestIbContextScoreDelta(unittest.TestCase):
    """Tests for _ib_context_score_delta()."""

    def _call(self, **kw):
        defaults = dict(
            today_ib_range=1.0,
            prev_ib_range=1.0,
            pm_range_pct=0.5,
            direction="Bullish Break",
            today_ib_high=11.0,
            today_ib_low=10.0,
            prev_ib_high=11.0,
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
        delta = self._call(today_ib_range=1.0, prev_ib_range=1.0, pm_range_pct=0.5)
        self.assertEqual(0, delta)

    def test_neutral_ratio_with_large_pm_penalty(self):
        delta = self._call(today_ib_range=1.0, prev_ib_range=1.0, pm_range_pct=2.5)
        self.assertEqual(-2, delta)

    # ── compression path ────────────────────────────────────────────────────

    def test_compression_pm_directional_bullish_gives_plus5(self):
        # today IB < 70% of yesterday IB (ratio ≈ 0.65)
        # pre-market pushed above prior IB high → +5
        delta = self._call(
            today_ib_range=0.65,
            prev_ib_range=1.0,
            pm_range_pct=0.8,
            direction="Bullish Break",
            today_ib_high=11.0,
            today_ib_low=10.35,
            prev_ib_high=11.0,  # today_ib_high >= prev_ib_high * 0.99
            prev_ib_low=10.0,
        )
        self.assertEqual(5, delta)

    def test_compression_pm_directional_bearish_gives_plus5(self):
        delta = self._call(
            today_ib_range=0.65,
            prev_ib_range=1.0,
            pm_range_pct=0.8,
            direction="Bearish Break",
            today_ib_high=11.0,
            today_ib_low=10.0,   # today_ib_low <= prev_ib_low * 1.01
            prev_ib_high=11.5,
            prev_ib_low=10.0,
        )
        self.assertEqual(5, delta)

    def test_compression_pm_inside_prev_gives_plus3(self):
        # today IB is contained inside yesterday's IB range → +3
        delta = self._call(
            today_ib_range=0.5,
            prev_ib_range=1.0,
            pm_range_pct=0.5,
            direction="Bullish Break",
            today_ib_high=10.5,   # inside [10.0, 11.0]
            today_ib_low=10.0,
            prev_ib_high=11.0,
            prev_ib_low=9.5,
        )
        self.assertEqual(3, delta)

    def test_compression_no_pm_gives_plus2(self):
        # pm_range_pct=0 means no pre-market data → +2
        delta = self._call(
            today_ib_range=0.5,
            prev_ib_range=1.0,
            pm_range_pct=0.0,
            direction="Bullish Break",
            today_ib_high=10.5,
            today_ib_low=10.0,
            prev_ib_high=10.3,   # today_ib_high exceeds prev — not inside
            prev_ib_low=9.8,
        )
        self.assertEqual(2, delta)

    def test_compression_directional_pm_penalty_net(self):
        # +5 (compression + directional alignment: today_ib_high ≥ prev_ib_high * 0.99)
        # − 2 (pm_range_pct ≥ 2%) = +3
        delta = self._call(
            today_ib_range=0.5,
            prev_ib_range=1.0,
            pm_range_pct=3.0,
            direction="Bullish Break",
            today_ib_high=10.5,   # 10.5 ≥ 10.3 * 0.99 = 10.197 → directional
            today_ib_low=10.0,
            prev_ib_high=10.3,
            prev_ib_low=9.8,
        )
        self.assertEqual(3, delta)

    def test_compression_inside_prev_pm_penalty_net(self):
        # today IB inside prev IB → +3, pm_range_pct=3.0 → −2, net = +1
        # today_ib_high=10.8 <= prev_ib_high=11.0 AND today_ib_low=9.5 >= prev_ib_low=9.0
        delta = self._call(
            today_ib_range=1.3,   # 10.8 - 9.5
            prev_ib_range=2.0,    # ratio ≈ 0.65 → compression
            pm_range_pct=3.0,
            direction="Bullish Break",
            today_ib_high=10.8,
            today_ib_low=9.5,
            prev_ib_high=11.0,    # today inside prev
            prev_ib_low=9.0,
        )
        self.assertEqual(1, delta)

    # ── expansion path ──────────────────────────────────────────────────────

    def test_expansion_gives_minus3(self):
        delta = self._call(today_ib_range=1.5, prev_ib_range=1.0, pm_range_pct=0.5)
        self.assertEqual(-3, delta)

    def test_expansion_plus_large_pm_clamped_to_minus5(self):
        delta = self._call(today_ib_range=1.5, prev_ib_range=1.0, pm_range_pct=3.0)
        self.assertEqual(-5, delta)

    # ── clamp assertions ────────────────────────────────────────────────────

    def test_result_never_exceeds_plus5(self):
        # Manually force a scenario that would overflow without clamping
        # compression (+5) — already max; no overflow needed here
        delta = self._call(
            today_ib_range=0.65, prev_ib_range=1.0, pm_range_pct=0.8,
            direction="Bullish Break",
            today_ib_high=11.0, today_ib_low=10.35,
            prev_ib_high=11.0, prev_ib_low=10.0,
        )
        self.assertLessEqual(delta, 5)

    def test_result_never_below_minus5(self):
        delta = self._call(today_ib_range=2.0, prev_ib_range=1.0, pm_range_pct=5.0)
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
            patch.object(_ptb, "_fetch_premarket_range", return_value=0.0),
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
            patch.object(_ptb, "_fetch_premarket_range", return_value=0.0),
            patch("time.sleep"),
        ):
            r = self._make_result(tcs=62.0)
            _ptb._enrich_with_ib_context([r], "2026-04-20")
        self.assertAlmostEqual(62.0, r["tcs"])

    def test_tcs_adjusted_on_compression(self):
        with (
            patch.object(_ptb, "_fetch_prev_ib", return_value=(12.0, 10.5)),
            patch.object(_ptb, "_fetch_premarket_range", return_value=0.5),
            patch("time.sleep"),
        ):
            r = self._make_result(
                ib_high=10.65, ib_low=10.0, tcs=60.0, predicted="Bullish Break"
            )
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
            patch.object(_ptb, "_fetch_premarket_range", return_value=0.5),
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

    def test_fetch_premarket_range_returns_zero_on_error(self):
        with patch("requests.get", side_effect=RuntimeError("net down")):
            result = _ptb._fetch_premarket_range("AAPL", "2026-04-20", 10.0)
        self.assertEqual(0.0, result)

    def test_fetch_premarket_range_returns_zero_when_open_zero(self):
        result = _ptb._fetch_premarket_range("AAPL", "2026-04-20", 0.0)
        self.assertEqual(0.0, result)

    def test_fetch_prev_ib_returns_zeros_on_empty_alpaca_bars(self):
        original_client = _ptb._supabase_client
        try:
            _ptb._supabase_client = None   # skip Supabase path
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
