"""Shared calculation utilities used by app.py and paper_trader_bot."""

# IB-range position-sizing multiplier breakpoints.
# 0-2%: 2.00× | 2-4%: 1.30× | 4-6%: 1.00× | 6-8%: 0.75× | 8-10%: 0.80×
IB_RANGE_MULT: list[tuple[float, float]] = [
    (2.0,  2.00),   # IB pct < 2%
    (4.0,  1.30),   # 2% ≤ IB pct < 4%
    (6.0,  1.00),   # 4% ≤ IB pct < 6%
    (8.0,  0.75),   # 6% ≤ IB pct < 8%
    (10.0, 0.80),   # 8% ≤ IB pct < 10%
]


def ib_size_mult(ib_pct: float) -> float:
    """Return position-size multiplier for a given IB range %.

    Walks the IB_RANGE_MULT breakpoint table and returns the multiplier for
    the first ceiling that exceeds *ib_pct*.  Falls back to 0.80 for values
    ≥ 10% (which should not normally pass the IB filter).
    """
    for ceiling, mult in IB_RANGE_MULT:
        if ib_pct < ceiling:
            return mult
    return 0.80  # ≥10% shouldn't pass IB filter, safe fallback
