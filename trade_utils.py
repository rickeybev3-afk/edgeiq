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


# P-tier position-sizing multiplier table.
# Each entry: (scan_type, tcs_min, multiplier)
#   P1: Morning  TCS≥70 → 1.50×
#   P2: Intraday TCS≥70 → 1.25×
#   P3: Morning  TCS50-69 → blocked by MORNING_TCS_FLOOR before sizing; 1.00× baseline
#   P4: Intraday TCS50-69 → 1.00× baseline
P_TIER_MULT: list[tuple[str, float, float]] = [
    ("morning",  70, 1.50),
    ("morning",  50, 1.00),   # P3 — blocked by MORNING_TCS_FLOOR; baseline if floor overridden
    ("intraday", 70, 1.25),
    ("intraday", 50, 1.00),
]


def p_tier_size_mult(tcs: float, scan_type: str) -> float:
    """Return P-tier position-size multiplier for a given TCS and scan type.

    Walks the P_TIER_MULT breakpoint table and returns the multiplier for the
    first row where *scan_type* matches and *tcs* meets or exceeds *tcs_min*.
    Falls back to 1.00 for unknown tier combinations.
    """
    st = str(scan_type or "").lower()
    for s, tcs_min, mult in P_TIER_MULT:
        if s == st and tcs >= tcs_min:
            return mult
    return 1.00  # fallback — unknown tier, baseline sizing
