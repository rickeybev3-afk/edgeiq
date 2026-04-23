"""Unit tests for shared position-sizing calculations in trade_utils.py.

Covers all three position-sizing helpers to catch regressions that would
silently affect trade sizing across app.py and paper_trader_bot.py.

ib_size_mult bucket table (from IB_RANGE_MULT):
  ib_pct < 2%        → 2.00
  2% <= ib_pct < 4%  → 1.30
  4% <= ib_pct < 6%  → 1.00
  6% <= ib_pct < 8%  → 0.75
  8% <= ib_pct < 10% → 0.80
  ib_pct >= 10%      → 0.80  (fallback)

p_tier_size_mult table (from P_TIER_MULT):
  morning  + TCS >= 70  → 1.50  (P1)
  morning  + TCS >= 50  → 1.00  (P3 baseline)
  intraday + TCS >= 70  → 1.25  (P2)
  intraday + TCS >= 50  → 1.00  (P4 baseline)
  unknown combination   → 1.00  (fallback)

sp_size_mult table (from SP_MULT_TABLE):
  'other'    → 1.15
  'gap'      → 1.00
  'trend'    → 0.85
  'gap_down' → 1.00
  'squeeze'  → 0.70
  unknown / None / empty → 1.00  (fallback)
"""

import pytest
from trade_utils import ib_size_mult, p_tier_size_mult, sp_size_mult


# ── ib_size_mult ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ib_pct, expected", [
    (0.0,  2.00),   # bottom of bucket 1
    (1.9,  2.00),   # just below bucket 1 ceiling
    (2.0,  1.30),   # exact bucket 2 boundary (lower)
    (3.9,  1.30),   # just below bucket 2 ceiling
    (4.0,  1.00),   # exact bucket 3 boundary (lower)
    (5.9,  1.00),   # just below bucket 3 ceiling
    (6.0,  0.75),   # exact bucket 4 boundary (lower)
    (7.9,  0.75),   # just below bucket 4 ceiling
    (8.0,  0.80),   # exact bucket 5 boundary (lower)
    (9.9,  0.80),   # just below bucket 5 ceiling
    (10.0, 0.80),   # at/above 10% — hits the fallback return
    (12.0, 0.80),   # well above 10% — fallback
])
def test_ib_size_mult_bucket_boundaries(ib_pct, expected):
    assert ib_size_mult(ib_pct) == pytest.approx(expected)


# ── p_tier_size_mult ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("tcs, scan_type, expected", [
    # P1: morning high-conviction
    (70,  "morning",  1.50),
    (85,  "morning",  1.50),
    (100, "morning",  1.50),
    # P3: morning lower-conviction (TCS 50-69)
    (50,  "morning",  1.00),
    (65,  "morning",  1.00),
    (69,  "morning",  1.00),
    # P2: intraday high-conviction
    (70,  "intraday", 1.25),
    (90,  "intraday", 1.25),
    # P4: intraday lower-conviction (TCS 50-69)
    (50,  "intraday", 1.00),
    (60,  "intraday", 1.00),
    (69,  "intraday", 1.00),
    # Below minimum TCS for any tier → fallback
    (49,  "morning",  1.00),
    (49,  "intraday", 1.00),
    # Unknown scan type → fallback
    (80,  "overnight", 1.00),
    (80,  "swing",     1.00),
])
def test_p_tier_size_mult_known_tiers(tcs, scan_type, expected):
    assert p_tier_size_mult(tcs, scan_type) == pytest.approx(expected)


@pytest.mark.parametrize("scan_type", [None, "", "   "])
def test_p_tier_size_mult_none_empty_scan_type_returns_fallback(scan_type):
    assert p_tier_size_mult(75, scan_type) == pytest.approx(1.00)


def test_p_tier_size_mult_case_insensitive():
    assert p_tier_size_mult(75, "MORNING")  == pytest.approx(1.50)
    assert p_tier_size_mult(75, "Morning")  == pytest.approx(1.50)
    assert p_tier_size_mult(75, "INTRADAY") == pytest.approx(1.25)
    assert p_tier_size_mult(75, "Intraday") == pytest.approx(1.25)


# ── sp_size_mult ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("screener_pass, expected", [
    ("other",    1.15),
    ("gap",      1.00),
    ("trend",    0.85),
    ("gap_down", 1.00),
    ("squeeze",  0.70),
])
def test_sp_size_mult_known_good_inputs(screener_pass, expected):
    assert sp_size_mult(screener_pass) == pytest.approx(expected)


@pytest.mark.parametrize("screener_pass", [None, "", "unknown", "premarket", "momentum"])
def test_sp_size_mult_unknown_or_empty_returns_fallback(screener_pass):
    assert sp_size_mult(screener_pass) == pytest.approx(1.00)


@pytest.mark.parametrize("screener_pass, expected", [
    ("  other  ",    1.15),
    (" gap ",        1.00),
    ("\ttrend\t",    0.85),
    ("  gap_down  ", 1.00),
    (" squeeze ",    0.70),
])
def test_sp_size_mult_whitespace_normalisation(screener_pass, expected):
    assert sp_size_mult(screener_pass) == pytest.approx(expected)


@pytest.mark.parametrize("screener_pass, expected", [
    ("OTHER",    1.15),
    ("GAP",      1.00),
    ("TREND",    0.85),
    ("GAP_DOWN", 1.00),
    ("SQUEEZE",  0.70),
    ("Other",    1.15),
    ("Gap",      1.00),
])
def test_sp_size_mult_case_insensitive(screener_pass, expected):
    assert sp_size_mult(screener_pass) == pytest.approx(expected)
