"""Unit tests for shared position-sizing calculations in trade_utils.py.

Covers ib_size_mult() at each IB bucket boundary to catch regressions that
would silently affect both app.py and paper_trader_bot.py.

Bucket table (from IB_RANGE_MULT):
  ib_pct < 2%   → 2.00
  2% <= ib_pct < 4%  → 1.30
  4% <= ib_pct < 6%  → 1.00
  6% <= ib_pct < 8%  → 0.75
  8% <= ib_pct < 10% → 0.80
  ib_pct >= 10%      → 0.80  (fallback)
"""

import pytest
from trade_utils import ib_size_mult


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
