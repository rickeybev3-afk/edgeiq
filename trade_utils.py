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


# ── Screener-pass position-size multiplier ─────────────────────────────────
# Derived from live backtest data (calibrate_sp_mult.py, 2021-2026):
#   'other'  (< 3% daily change, same screener pool): 87% WR / +0.622R avg → 1.15×
#   'gap'    (≥ 3% daily change):                      65% WR / +0.327R avg → 1.00×
#   'trend'  (1-3% + above SMA20/50):                  only 12 trades       → 0.85×
#   'gap_down' (Bearish Break, ≥3% gap-down universe): calibrated 2026-04-20
#              via `python calibrate_sp_mult.py --pass gap_down`; 6 settled
#              gap_down Bearish Break trades in paper_trades — insufficient for
#              deviation (min 30); 1.00× is the data-confirmed baseline.
#              Re-run the script once ≥30 gap_down rows have tiered_pnl_r
#              populated; it will print the exact line to paste here.
#   'squeeze' (2026-04-21 → 2026-04-22): 36 trades, 88.9% WR / +0.009R avg → 0.70×
# Applied AFTER IB-range, RVOL and P-tier mults as a final expectancy layer.
SP_MULT_TABLE: dict[str, float] = {
    "other":    1.15,
    "gap":      1.00,
    "trend":    0.85,
    "gap_down": 1.00,   # Bearish Break universe — calibrated 2026-04-20 (6 settled trades, n<30 → baseline confirmed); re-run calibrate_sp_mult.py --pass gap_down once ≥30 settle
    "squeeze":  0.70,   # 36 trades 2026-04-21 → 2026-04-22, 88.9% WR / +0.009R → 0.70×
}

SP_CALIB_DATES: dict[str, str] = {
    "gap_down": "2026-04-20",
    "squeeze":  "2026-04-22",
}


def sp_size_mult(screener_pass: str | None) -> float:
    """Return position-size multiplier for a given screener_pass label.

    Derived from live backtest calibration (calibrate_sp_mult.py, 2021-2026).
    'other' stocks (smaller-move days, tighter IB) consistently outperform
    'gap' stocks on every metric in every year — 87% vs 65% WR — because
    smaller gaps produce cleaner, less volatile initial balance structures.
    'gap_down' (Bearish Break) calibrated 2026-04-20 → 1.00× baseline.
    Returns 1.0 for unknown / unclassified passes (safe baseline).
    """
    return SP_MULT_TABLE.get((screener_pass or "").lower().strip(), 1.00)
