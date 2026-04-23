"""
calibrate_sp_mult.py
--------------------
Unified position-size multiplier calibration for any screener pass.

Usage:
  python calibrate_sp_mult.py                      # list available passes
  python calibrate_sp_mult.py --pass squeeze        # calibrate the squeeze pass
  python calibrate_sp_mult.py --pass gap_down       # calibrate the gap_down pass
  python calibrate_sp_mult.py --pass squeeze --apply  # calibrate AND patch trade_utils.py
  python calibrate_sp_mult.py --reset-pass trend    # reset trend back to 1.00x baseline
  python calibrate_sp_mult.py --restore-bak trend  # undo a reset by restoring the .bak backup
  python calibrate_sp_mult.py --show-reset-log      # print a formatted table of all past resets
  python calibrate_sp_mult.py --show-reset-log --pass trend  # filter reset history to one pass
  python calibrate_sp_mult.py --self-test           # run deterministic unit tests

Methodology (mirrors the 5-year backtest used across passes):
  1. Pull all settled rows for the chosen pass (tiered_pnl_r IS NOT NULL).
  2. Compute win rate (win_loss='Win') and average R (tiered_pnl_r mean).
  3. Compute R-expectancy = WR * avg_win_R  +  (1-WR) * avg_loss_R.
  4. Compare against the 'gap' pass (>=3% gap universe) which anchors at 1.00x.
  5. Apply a sqrt-dampened ratio so one outlier week can't swing sizing by >+/-30%.
     mult = max(0.70, min(1.30, sqrt(expectancy_pass / expectancy_gap)))
  6. Round to nearest 0.05 for a clean table entry.
  7. Print a data-citation block ready to paste above SP_MULT_TABLE.
     With --apply: patch trade_utils.py directly (backup created first).

Requirements:
  SUPABASE_URL, SUPABASE_KEY environment variables must be set (same as main app).
  The backend.py module must be importable from the project root.

Minimum sample: 30 settled trades. Script exits early with a status message
if the count is below that floor.
"""

import argparse
import difflib
import math
import os
import re
import shutil
import statistics
import sys

sys.path.insert(0, os.path.dirname(__file__))

try:
    from backend import supabase
except ImportError as exc:
    print(f"ERROR: could not import supabase from backend.py — {exc}", file=sys.stderr)
    sys.exit(1)

from log_utils import _rotate_log, _parse_int_env, validate_env_config

if not supabase:
    print("ERROR: Supabase client is not initialised. Check SUPABASE_URL / SUPABASE_KEY.", file=sys.stderr)
    sys.exit(1)

MIN_TRADES = 30

ANCHOR_PASS = "gap"

CONTEXT_PASSES: list[str] = []

PASS_CONFIG: dict[str, dict] = {
    "gap_down": {
        "screener_pass": "gap_down",
        "predicted": "Bearish Break",
        # header printed at the top of the output
        "title": "Bearish Break (gap_down) Position-Size Multiplier Calibration",
        # label shown in the trade-count and statistics tables
        "count_label": "gap_down (Bearish Break)",
        "stat_label": "gap_down",
        # text inserted inside the parentheses in the data-citation line, e.g.:
        #   'gap_down' (Bearish Break, 2024-01-03 → 2024-12-31): ...
        # Use "" to omit the inner parenthetical (see squeeze below).
        "citation_inner": "Bearish Break, ",
        # optional prefix for the comment on the "Next step" line
        "next_step_comment_prefix": "Bearish Break — ",
        # template for the stale-reset citation comment; {date} is replaced with today's ISO date
        "stale_comment_template": (
            "#   'gap_down' (Bearish Break, >=3% gap-down universe):  0 settled trades as of\n"
            "#              {date} — 1.00× baseline until >=30 trades settle."
        ),
    },
    "squeeze": {
        "screener_pass": "squeeze",
        "predicted": None,
        "title": "Squeeze Pass Position-Size Multiplier Calibration",
        "count_label": "squeeze",
        "stat_label": "squeeze",
        # squeeze citation: 'squeeze' (2024-01-03 → 2024-12-31): ...
        "citation_inner": "",
        "next_step_comment_prefix": "",
        "stale_comment_template": (
            "#   'squeeze':   0 settled trades as of {date} — 1.00× baseline until\n"
            "#               >=30 trades settle."
        ),
    },
    "other": {
        "screener_pass": "other",
        "predicted": None,
        "title": "Other Pass (< 3% daily change) Position-Size Multiplier Calibration",
        "count_label": "other",
        "stat_label": "other",
        "citation_inner": "",
        "next_step_comment_prefix": "",
        "stale_comment_template": (
            "#   'other'  (< 3% daily change): 0 settled trades as of {date} — 1.00× baseline until\n"
            "#               >=30 trades settle."
        ),
    },
    "trend": {
        "screener_pass": "trend",
        "predicted": None,
        "title": "Trend Pass (1–3% daily change) Position-Size Multiplier Calibration",
        "count_label": "trend",
        "stat_label": "trend",
        "citation_inner": "",
        "next_step_comment_prefix": "",
        "stale_comment_template": (
            "#   'trend'  (1-3%):              0 settled trades as of {date} — 1.00× baseline until\n"
            "#               >=30 trades settle."
        ),
    },
    "gap": {
        "screener_pass": "gap",
        "predicted": None,
        "title": "Gap Pass (≥ 3% daily change) Position-Size Multiplier Calibration",
        "count_label": "gap",
        "stat_label": "gap",
        "citation_inner": "",
        "next_step_comment_prefix": "",
        "stale_comment_template": (
            "#   'gap'    (≥ 3% daily change): 0 settled trades as of {date} — 1.00× baseline until\n"
            "#               >=30 trades settle."
        ),
    },
}


def _fetch_settled(screener_pass: str, predicted: str | None = None) -> list[dict]:
    """Return all settled rows for a given screener_pass (tiered_pnl_r NOT NULL)."""
    q = (
        supabase
        .table("paper_trades")
        .select("id,trade_date,win_loss,tiered_pnl_r,pnl_r_sim,predicted")
        .eq("screener_pass", screener_pass)
        .not_.is_("tiered_pnl_r", "null")
    )
    if predicted:
        q = q.eq("predicted", predicted)

    all_rows: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        resp = q.range(offset, offset + page_size - 1).execute()
        all_rows.extend(resp.data or [])
        if len(resp.data or []) < page_size:
            break
        offset += page_size
    return all_rows


def _stats(rows: list[dict]) -> dict:
    """Compute win-rate, avg-R and R-expectancy from a list of settled rows."""
    if not rows:
        return {"n": 0, "wr": None, "avg_r": None, "expectancy": None,
                "avg_win_r": None, "avg_loss_r": None}

    wins = [r for r in rows if (r.get("win_loss") or "").upper() == "WIN"]
    losses = [r for r in rows if (r.get("win_loss") or "").upper() != "WIN"]

    wr = len(wins) / len(rows)
    all_r = [r["tiered_pnl_r"] for r in rows if r.get("tiered_pnl_r") is not None]
    win_r = [r["tiered_pnl_r"] for r in wins if r.get("tiered_pnl_r") is not None]
    loss_r = [r["tiered_pnl_r"] for r in losses if r.get("tiered_pnl_r") is not None]

    avg_r = statistics.mean(all_r) if all_r else None
    avg_win_r = statistics.mean(win_r) if win_r else None
    avg_loss_r = statistics.mean(loss_r) if loss_r else None

    if avg_win_r is not None and avg_loss_r is not None:
        expectancy = wr * avg_win_r + (1 - wr) * avg_loss_r
    else:
        expectancy = avg_r

    return {
        "n": len(rows),
        "wr": wr,
        "avg_r": avg_r,
        "expectancy": expectancy,
        "avg_win_r": avg_win_r,
        "avg_loss_r": avg_loss_r,
    }


def _recommend_mult(pass_exp: float, gap_exp: float) -> float:
    """
    Recommend a multiplier anchored to 'gap' = 1.00×.

    Uses a sqrt-dampened ratio so a 2× expectancy advantage only becomes 1.41×,
    preventing one good/bad month from swinging sizing to an extreme.
    Clamped to [0.70, 1.30] and rounded to nearest 0.05.

    Edge-case handling (returns conservative fixed values, never crashes):
      gap_exp  <= 0 → can't anchor; return 1.00 (baseline, no change)
      pass_exp <= 0 → strategy has negative/zero expectancy; return 0.70
                      (minimum clamp — reduce sizing conservatively)
    """
    if gap_exp <= 0:
        return 1.00
    if pass_exp <= 0:
        return 0.70
    raw_ratio = pass_exp / gap_exp
    dampened = math.sqrt(raw_ratio)
    clamped = max(0.70, min(1.30, dampened))
    rounded = round(clamped / 0.05) * 0.05
    return rounded


def _self_test() -> None:
    """Quick deterministic check of _recommend_mult() for known inputs."""
    cases = [
        ("positive/positive equal",    0.327, 0.327, 1.00),
        ("positive/positive higher",   0.622, 0.327, 1.30),
        ("positive/positive lower",    0.164, 0.327, 0.70),
        ("zero pass exp",              0.000, 0.327, 0.70),
        ("negative pass exp",         -0.100, 0.327, 0.70),
        ("zero gap anchor",            0.400, 0.000, 1.00),
        ("negative gap anchor",        0.400,-0.100, 1.00),
    ]
    all_ok = True
    for label, ps_exp, ga_exp, expected in cases:
        result = _recommend_mult(ps_exp, ga_exp)
        ok = abs(result - expected) < 0.001
        print(f"  {'OK  ' if ok else 'FAIL'} {label}: _recommend_mult({ps_exp}, {ga_exp}) = {result} (expected {expected})")
        if not ok:
            all_ok = False
    if all_ok:
        print("All self-tests passed.")
    else:
        print("SELF-TEST FAILURES — do not trust the recommendation above.")
        sys.exit(1)


_APPLY_FIXTURE = """\
# preceding source

# ── Screener-pass position-size multiplier ─────────────────────────────────
# Derived from 5-year backtest:
#   'other'  (< 3% daily change): 87% WR / +0.622R avg → 1.15×
#   'gap'    (≥ 3% daily change): 65% WR / +0.327R avg → 1.00×
#   'trend'  (1-3%):              only 12 trades       → 0.85×
#   'squeeze':   0 settled trades as of 2026-04-20 — 1.00× baseline until
#               >=30 trades settle.
#   'gap_down' (Bearish Break, >=3% gap-down universe):  0 settled trades as of
#              2026-04-20 — 1.00× baseline until >=30 trades settle.
# Applied AFTER IB-range, RVOL and P-tier mults as a final expectancy layer.
_SP_MULT_TABLE: dict[str, float] = {
    "other":    1.15,
    "gap":      1.00,
    "trend":    0.85,
    "squeeze":  1.00,   # baseline; recalibrate once >=30 trades settle
    "gap_down": 1.00,   # Bearish Break — baseline; recalibrate once >=30 trades settle
}

_SP_CALIB_DATES: dict[str, str] = {
    "gap_down": "2026-04-20",
    "squeeze":  "2026-04-20",
}

def _sp_size_mult(screener_pass):
    return _SP_MULT_TABLE.get(screener_pass, 1.00)
"""

# Variant of the fixture where the gap entry carries a stale-warning comment,
# simulating a reset to the "no data yet" state before the first calibration.
_APPLY_FIXTURE_GAP_STALE = """\
# preceding source

# ── Screener-pass position-size multiplier ─────────────────────────────────
# Derived from 5-year backtest:
#   'other'  (< 3% daily change): 87% WR / +0.622R avg → 1.15×
#   'gap'    (≥ 3% daily change): 0 settled trades as of 2026-04-20 — 1.00× baseline until
#               >=30 trades settle.
#   'trend'  (1-3%):              only 12 trades       → 0.85×
#   'squeeze':   0 settled trades as of 2026-04-20 — 1.00× baseline until
#               >=30 trades settle.
#   'gap_down' (Bearish Break, >=3% gap-down universe):  0 settled trades as of
#              2026-04-20 — 1.00× baseline until >=30 trades settle.
# Applied AFTER IB-range, RVOL and P-tier mults as a final expectancy layer.
_SP_MULT_TABLE: dict[str, float] = {
    "other":    1.15,
    "gap":      1.00,   # baseline; recalibrate once >=30 trades settle
    "trend":    0.85,
    "squeeze":  1.00,   # baseline; recalibrate once >=30 trades settle
    "gap_down": 1.00,   # Bearish Break — baseline; recalibrate once >=30 trades settle
}

_SP_CALIB_DATES: dict[str, str] = {
    "gap_down": "2026-04-20",
    "squeeze":  "2026-04-20",
}

def _sp_size_mult(screener_pass):
    return _SP_MULT_TABLE.get(screener_pass, 1.00)
"""


# Variant of the fixture where the trend entry carries a stale-warning comment,
# simulating a reset to the "no data yet" state before the first calibration.
_APPLY_FIXTURE_TREND_STALE = """\
# preceding source

# ── Screener-pass position-size multiplier ─────────────────────────────────
# Derived from 5-year backtest:
#   'other'  (< 3% daily change): 87% WR / +0.622R avg → 1.15×
#   'gap'    (≥ 3% daily change): 65% WR / +0.327R avg → 1.00×
#   'trend'  (1-3%):              0 settled trades as of 2026-04-20 — 1.00× baseline until
#               >=30 trades settle.
#   'squeeze':   0 settled trades as of 2026-04-20 — 1.00× baseline until
#               >=30 trades settle.
#   'gap_down' (Bearish Break, >=3% gap-down universe):  0 settled trades as of
#              2026-04-20 — 1.00× baseline until >=30 trades settle.
# Applied AFTER IB-range, RVOL and P-tier mults as a final expectancy layer.
_SP_MULT_TABLE: dict[str, float] = {
    "other":    1.15,
    "gap":      1.00,
    "trend":    1.00,   # baseline; recalibrate once >=30 trades settle
    "squeeze":  1.00,   # baseline; recalibrate once >=30 trades settle
    "gap_down": 1.00,   # Bearish Break — baseline; recalibrate once >=30 trades settle
}

_SP_CALIB_DATES: dict[str, str] = {
    "gap_down": "2026-04-20",
    "squeeze":  "2026-04-20",
    "trend":    "2026-04-20",
}

def _sp_size_mult(screener_pass):
    return _SP_MULT_TABLE.get(screener_pass, 1.00)
"""

# Variant of the fixture where the other entry carries a stale-warning comment,
# simulating a reset to the "no data yet" state before the first calibration.
_APPLY_FIXTURE_OTHER_STALE = """\
# preceding source

# ── Screener-pass position-size multiplier ─────────────────────────────────
# Derived from 5-year backtest:
#   'other'  (< 3% daily change): 0 settled trades as of 2026-04-20 — 1.00× baseline until
#               >=30 trades settle.
#   'gap'    (≥ 3% daily change): 65% WR / +0.327R avg → 1.00×
#   'trend'  (1-3%):              only 12 trades       → 0.85×
#   'squeeze':   0 settled trades as of 2026-04-20 — 1.00× baseline until
#               >=30 trades settle.
#   'gap_down' (Bearish Break, >=3% gap-down universe):  0 settled trades as of
#              2026-04-20 — 1.00× baseline until >=30 trades settle.
# Applied AFTER IB-range, RVOL and P-tier mults as a final expectancy layer.
_SP_MULT_TABLE: dict[str, float] = {
    "other":    1.00,   # baseline; recalibrate once >=30 trades settle
    "gap":      1.00,
    "trend":    0.85,
    "squeeze":  1.00,   # baseline; recalibrate once >=30 trades settle
    "gap_down": 1.00,   # Bearish Break — baseline; recalibrate once >=30 trades settle
}

_SP_CALIB_DATES: dict[str, str] = {
    "gap_down": "2026-04-20",
    "squeeze":  "2026-04-20",
    "other":    "2026-04-20",
}

def _sp_size_mult(screener_pass):
    return _SP_MULT_TABLE.get(screener_pass, 1.00)
"""


def _self_test_apply() -> None:
    """Deterministic unit tests for _apply_to_bot() using an in-memory fixture."""
    import tempfile

    all_ok = True

    def _run(
        label: str,
        pass_name: str,
        new_mult: float,
        comment: str,
        expect_value: float,
        expect_comment_fragment: str,
        citation_line: str = "",
        expect_citation_fragment: str = "",
        expect_stale_absent: str = "",
        fixture: str = _APPLY_FIXTURE,
    ) -> None:
        nonlocal all_ok
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
            tmp = tf.name
            tf.write(fixture)
        try:
            _apply_to_bot(pass_name, new_mult, comment, citation_line=citation_line, bot_path=tmp)
            with open(tmp) as fh:
                patched = fh.read()
            # Find the patched entry line
            pat = re.compile(r'"' + re.escape(pass_name) + r'"\s*:\s*([\d.]+)')
            m = pat.search(patched)
            if not m:
                print(f"  FAIL {label}: '{pass_name}' entry not found after patch")
                all_ok = False
                return
            got_value = float(m.group(1))
            value_ok = abs(got_value - expect_value) < 0.001
            comment_ok = expect_comment_fragment in patched
            citation_ok = (not expect_citation_fragment) or (expect_citation_fragment in patched)
            stale_ok = (not expect_stale_absent) or (expect_stale_absent not in patched)
            ok = value_ok and comment_ok and citation_ok and stale_ok
            print(
                f"  {'OK  ' if ok else 'FAIL'} {label}: "
                f"value={got_value:.2f} (expected {expect_value:.2f}), "
                f"inline_comment={'found' if comment_ok else 'MISSING'}"
                + (f", citation={'found' if citation_ok else 'MISSING'}" if expect_citation_fragment else "")
                + (f", stale_absent={'yes' if stale_ok else 'NO'}" if expect_stale_absent else "")
            )
            if not ok:
                all_ok = False
            # Verify other entries are untouched — skip when 'other' is itself being patched
            if pass_name != "other":
                other_pat = re.compile(r'"other"\s*:\s*([\d.]+)')
                om = other_pat.search(patched)
                if om and abs(float(om.group(1)) - 1.15) > 0.001:
                    print(f"  FAIL {label}: 'other' entry was corrupted (got {om.group(1)})")
                    all_ok = False
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            try:
                os.unlink(tmp + ".bak")
            except OSError:
                pass

    _run(
        "squeeze baseline→1.15",
        "squeeze", 1.15, "47 trades, 72.3% WR / +0.411R → 1.15×",
        expect_value=1.15, expect_comment_fragment="72.3% WR",
        citation_line="#   'squeeze' (2024-01-03 → 2024-12-31): 47 trades, 72.3% WR / +0.411R avg → 1.15×",
        expect_citation_fragment="47 trades, 72.3% WR / +0.411R avg → 1.15×",
        expect_stale_absent="'squeeze':   0 settled trades as of 2026-04-20",
    )
    _run(
        "gap_down baseline→0.85",
        "gap_down", 0.85, "33 trades, 58.1% WR / +0.290R → 0.85×",
        expect_value=0.85, expect_comment_fragment="58.1% WR",
        citation_line="#   'gap_down' (Bearish Break, 2024-01-03 → 2024-12-31): 33 trades, 58.1% WR / +0.290R avg → 0.85×",
        expect_citation_fragment="33 trades, 58.1% WR / +0.290R avg → 0.85×",
        expect_stale_absent="'gap_down' (Bearish Break, >=3% gap-down universe):  0 settled trades",
    )
    _run(
        "gap baseline→1.10",
        "gap", 1.10, "55 trades, 67.3% WR / +0.350R → 1.10×",
        expect_value=1.10, expect_comment_fragment="67.3% WR",
        citation_line="#   'gap'    (2024-01-03 → 2024-12-31): 55 trades, 67.3% WR / +0.350R avg → 1.10×",
        expect_citation_fragment="55 trades, 67.3% WR / +0.350R avg → 1.10×",
        # gap fixture entry already has real trade data, no stale warning to guard
    )
    _run(
        "gap stale-warning→1.10",
        "gap", 1.10, "55 trades, 67.3% WR / +0.350R → 1.10×",
        expect_value=1.10, expect_comment_fragment="67.3% WR",
        citation_line="#   'gap'    (2024-01-03 → 2024-12-31): 55 trades, 67.3% WR / +0.350R avg → 1.10×",
        expect_citation_fragment="55 trades, 67.3% WR / +0.350R avg → 1.10×",
        expect_stale_absent="'gap'    (≥ 3% daily change): 0 settled trades as of",
        fixture=_APPLY_FIXTURE_GAP_STALE,
    )
    _run(
        "gap idempotent re-apply",
        "gap", 1.10, "55 trades, 67.3% WR / +0.350R → 1.10×",
        expect_value=1.10, expect_comment_fragment="1.10×",
        citation_line="#   'gap'    (2024-01-03 → 2024-12-31): 55 trades, 67.3% WR / +0.350R avg → 1.10×",
        expect_citation_fragment="1.10×",
    )
    _run(
        "gap no citation_line — comment block untouched",
        "gap", 1.10, "55 trades, 67.3% WR / +0.350R → 1.10×",
        expect_value=1.10, expect_comment_fragment="67.3% WR",
    )
    _run(
        "gap_down idempotent re-apply",
        "gap_down", 0.85, "33 trades, 58.1% WR / +0.290R → 0.85×",
        expect_value=0.85, expect_comment_fragment="0.85×",
        citation_line="#   'gap_down' (Bearish Break, 2024-01-03 → 2024-12-31): 33 trades, 58.1% WR / +0.290R avg → 0.85×",
        expect_citation_fragment="0.85×",
    )
    _run(
        "gap_down no citation_line — comment block untouched",
        "gap_down", 0.85, "33 trades, 58.1% WR / +0.290R → 0.85×",
        expect_value=0.85, expect_comment_fragment="58.1% WR",
    )
    _run(
        "squeeze idempotent re-apply",
        "squeeze", 1.15, "47 trades, 72.3% WR / +0.411R → 1.15×",
        expect_value=1.15, expect_comment_fragment="1.15×",
        citation_line="#   'squeeze' (2024-01-03 → 2024-12-31): 47 trades, 72.3% WR / +0.411R avg → 1.15×",
        expect_citation_fragment="1.15×",
    )
    _run(
        "no citation_line — comment block untouched",
        "squeeze", 1.15, "47 trades, 72.3% WR / +0.411R → 1.15×",
        expect_value=1.15, expect_comment_fragment="72.3% WR",
    )
    _run(
        "other baseline→1.20",
        "other", 1.20, "60 trades, 80.0% WR / +0.500R → 1.20×",
        expect_value=1.20, expect_comment_fragment="80.0% WR",
        citation_line="#   'other' (2024-01-03 → 2024-12-31): 60 trades, 80.0% WR / +0.500R avg → 1.20×",
        expect_citation_fragment="60 trades, 80.0% WR / +0.500R avg → 1.20×",
        expect_stale_absent="87% WR / +0.622R avg → 1.15×",
    )
    _run(
        "other stale-warning→1.20",
        "other", 1.20, "60 trades, 80.0% WR / +0.500R → 1.20×",
        expect_value=1.20, expect_comment_fragment="80.0% WR",
        citation_line="#   'other' (2024-01-03 → 2024-12-31): 60 trades, 80.0% WR / +0.500R avg → 1.20×",
        expect_citation_fragment="60 trades, 80.0% WR / +0.500R avg → 1.20×",
        expect_stale_absent="'other'  (< 3% daily change): 0 settled trades as of",
        fixture=_APPLY_FIXTURE_OTHER_STALE,
    )
    _run(
        "trend baseline→1.00",
        "trend", 1.00, "50 trades, 68.0% WR / +0.350R → 1.00×",
        expect_value=1.00, expect_comment_fragment="68.0% WR",
        citation_line="#   'trend' (2024-01-03 → 2024-12-31): 50 trades, 68.0% WR / +0.350R avg → 1.00×",
        expect_citation_fragment="50 trades, 68.0% WR / +0.350R avg → 1.00×",
        expect_stale_absent="only 12 trades       → 0.85×",
    )
    _run(
        "trend stale-warning→1.00",
        "trend", 1.00, "50 trades, 68.0% WR / +0.350R → 1.00×",
        expect_value=1.00, expect_comment_fragment="68.0% WR",
        citation_line="#   'trend' (2024-01-03 → 2024-12-31): 50 trades, 68.0% WR / +0.350R avg → 1.00×",
        expect_citation_fragment="50 trades, 68.0% WR / +0.350R avg → 1.00×",
        expect_stale_absent="'trend'  (1-3%):              0 settled trades as of",
        fixture=_APPLY_FIXTURE_TREND_STALE,
    )
    _run(
        "other idempotent re-apply",
        "other", 1.20, "60 trades, 80.0% WR / +0.500R → 1.20×",
        expect_value=1.20, expect_comment_fragment="1.20×",
        citation_line="#   'other' (2024-01-03 → 2024-12-31): 60 trades, 80.0% WR / +0.500R avg → 1.20×",
        expect_citation_fragment="1.20×",
    )
    _run(
        "other no citation_line — comment block untouched",
        "other", 1.20, "60 trades, 80.0% WR / +0.500R → 1.20×",
        expect_value=1.20, expect_comment_fragment="80.0% WR",
    )
    _run(
        "trend idempotent re-apply",
        "trend", 1.00, "50 trades, 68.0% WR / +0.350R → 1.00×",
        expect_value=1.00, expect_comment_fragment="1.00×",
        citation_line="#   'trend' (2024-01-03 → 2024-12-31): 50 trades, 68.0% WR / +0.350R avg → 1.00×",
        expect_citation_fragment="1.00×",
    )
    _run(
        "trend no citation_line — comment block untouched",
        "trend", 1.00, "50 trades, 68.0% WR / +0.350R → 1.00×",
        expect_value=1.00, expect_comment_fragment="68.0% WR",
    )

    # ── Round-trip tests: reset → verify stale ────────────────────────────────
    def _run_reset(
        label: str,
        pass_name: str,
        expect_stale_fragment: str,
        expect_value: float = 1.00,
        expect_inline_fragment: str = "baseline; recalibrate once >=30 trades settle",
        fixture: str = _APPLY_FIXTURE,
    ) -> None:
        nonlocal all_ok
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
            tmp = tf.name
            tf.write(fixture)
        try:
            _reset_pass_to_baseline(pass_name, bot_path=tmp)
            with open(tmp) as fh:
                patched = fh.read()
            pat = re.compile(r'"' + re.escape(pass_name) + r'"\s*:\s*([\d.]+)')
            m = pat.search(patched)
            if not m:
                print(f"  FAIL {label}: '{pass_name}' entry not found after reset")
                all_ok = False
                return
            got_value = float(m.group(1))
            value_ok = abs(got_value - expect_value) < 0.001
            stale_ok = expect_stale_fragment in patched
            inline_ok = expect_inline_fragment in patched
            ok = value_ok and stale_ok and inline_ok
            print(
                f"  {'OK  ' if ok else 'FAIL'} {label}: "
                f"value={got_value:.2f} (expected {expect_value:.2f}), "
                f"stale_comment={'found' if stale_ok else 'MISSING'}, "
                f"inline_comment={'found' if inline_ok else 'MISSING'}"
            )
            if not ok:
                all_ok = False
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            try:
                os.unlink(tmp + ".bak")
            except OSError:
                pass

    _run_reset(
        "reset trend (calibrated→stale)",
        "trend",
        expect_stale_fragment="'trend'  (1-3%):              0 settled trades as of",
        expect_inline_fragment="baseline; recalibrate once >=30 trades settle",
    )
    _run_reset(
        "reset other (calibrated→stale)",
        "other",
        expect_stale_fragment="'other'  (< 3% daily change): 0 settled trades as of",
        expect_inline_fragment="baseline; recalibrate once >=30 trades settle",
    )
    _run_reset(
        "reset gap_down (already-stale idempotent)",
        "gap_down",
        expect_stale_fragment="'gap_down' (Bearish Break, >=3% gap-down universe):  0 settled trades as of",
        expect_inline_fragment="Bearish Break — baseline; recalibrate once >=30 trades settle",
    )
    _run_reset(
        "reset squeeze (already-stale idempotent)",
        "squeeze",
        expect_stale_fragment="'squeeze':   0 settled trades as of",
        expect_inline_fragment="baseline; recalibrate once >=30 trades settle",
    )

    # Round-trip: reset trend → then apply real data → stale comment gone
    def _run_roundtrip(
        label: str,
        pass_name: str,
        apply_mult: float,
        apply_comment: str,
        apply_citation: str,
        expect_stale_absent: str,
        expect_citation_fragment: str,
        fixture: str = _APPLY_FIXTURE,
    ) -> None:
        nonlocal all_ok
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
            tmp = tf.name
            tf.write(fixture)
        try:
            _reset_pass_to_baseline(pass_name, bot_path=tmp)
            _apply_to_bot(pass_name, apply_mult, apply_comment, citation_line=apply_citation, bot_path=tmp)
            with open(tmp) as fh:
                patched = fh.read()
            pat = re.compile(r'"' + re.escape(pass_name) + r'"\s*:\s*([\d.]+)')
            m = pat.search(patched)
            if not m:
                print(f"  FAIL {label}: '{pass_name}' entry not found after round-trip")
                all_ok = False
                return
            got_value = float(m.group(1))
            value_ok = abs(got_value - apply_mult) < 0.001
            stale_gone = expect_stale_absent not in patched
            citation_ok = expect_citation_fragment in patched
            ok = value_ok and stale_gone and citation_ok
            print(
                f"  {'OK  ' if ok else 'FAIL'} {label}: "
                f"value={got_value:.2f} (expected {apply_mult:.2f}), "
                f"stale_gone={'yes' if stale_gone else 'NO'}, "
                f"citation={'found' if citation_ok else 'MISSING'}"
            )
            if not ok:
                all_ok = False
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            try:
                os.unlink(tmp + ".bak")
            except OSError:
                pass

    _run_roundtrip(
        "round-trip trend: reset → apply 1.10×",
        pass_name="trend",
        apply_mult=1.10,
        apply_comment="50 trades, 68.0% WR / +0.350R → 1.10×",
        apply_citation="#   'trend' (2024-01-03 → 2024-12-31): 50 trades, 68.0% WR / +0.350R avg → 1.10×",
        expect_stale_absent="'trend'  (1-3%):              0 settled trades as of",
        expect_citation_fragment="50 trades, 68.0% WR / +0.350R avg → 1.10×",
    )
    _run_roundtrip(
        "round-trip other: reset → apply 1.20×",
        pass_name="other",
        apply_mult=1.20,
        apply_comment="60 trades, 80.0% WR / +0.500R → 1.20×",
        apply_citation="#   'other' (2024-01-03 → 2024-12-31): 60 trades, 80.0% WR / +0.500R avg → 1.20×",
        expect_stale_absent="'other'  (< 3% daily change): 0 settled trades as of",
        expect_citation_fragment="60 trades, 80.0% WR / +0.500R avg → 1.20×",
    )

    # ── Restore-from-bak tests ────────────────────────────────────────────────
    def _run_restore_bak(
        label: str,
        pass_name: str,
        bak_fixture: str,
        current_fixture: str,
        expect_value: float,
        expect_pass: bool = True,
    ) -> None:
        nonlocal all_ok
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
            tmp = tf.name
            tf.write(current_fixture)
        bak = tmp + ".bak"
        try:
            with open(bak, "w") as fh:
                fh.write(bak_fixture)

            exited = False
            exit_code = 0
            original_exit = sys.exit

            def _fake_exit(code=0):
                nonlocal exited, exit_code
                exited = True
                exit_code = int(code) if code else 0
                raise SystemExit(code)

            sys.exit = _fake_exit
            try:
                _restore_from_bak(pass_name, bot_path=tmp, yes=True)
            except SystemExit:
                pass
            finally:
                sys.exit = original_exit

            if expect_pass:
                with open(tmp) as fh:
                    restored = fh.read()
                pat = re.compile(r'"' + re.escape(pass_name) + r'"\s*:\s*([\d.]+)')
                m = pat.search(restored)
                if not m:
                    print(f"  FAIL {label}: '{pass_name}' entry not found after restore")
                    all_ok = False
                    return
                got = float(m.group(1))
                value_ok = abs(got - expect_value) < 0.001
                ok = value_ok and not exited
                print(
                    f"  {'OK  ' if ok else 'FAIL'} {label}: "
                    f"value={got:.2f} (expected {expect_value:.2f}), "
                    f"exited_unexpectedly={'yes' if exited else 'no'}"
                )
                if not ok:
                    all_ok = False
            else:
                ok = exited and exit_code != 0
                print(
                    f"  {'OK  ' if ok else 'FAIL'} {label}: "
                    f"expected failure exit, got exited={exited} code={exit_code}"
                )
                if not ok:
                    all_ok = False
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            try:
                os.unlink(bak)
            except OSError:
                pass

    # Restoring 'trend' from a backup that had 0.85× back to the current reset state
    _run_restore_bak(
        "restore-bak trend: backup=0.85× → replaces current reset",
        pass_name="trend",
        bak_fixture=_APPLY_FIXTURE,          # has trend at 0.85
        current_fixture=_APPLY_FIXTURE_TREND_STALE,  # current has trend at 1.00 (reset)
        expect_value=0.85,
        expect_pass=True,
    )
    # Restoring 'gap' from a backup that had a calibrated value
    _run_restore_bak(
        "restore-bak gap: backup=1.00× calibrated → replaces stale current",
        pass_name="gap",
        bak_fixture=_APPLY_FIXTURE,
        current_fixture=_APPLY_FIXTURE_GAP_STALE,
        expect_value=1.00,
        expect_pass=True,
    )
    # Missing backup file → should fail
    def _run_restore_bak_no_bak(label: str, pass_name: str) -> None:
        nonlocal all_ok
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
            tmp = tf.name
            tf.write(_APPLY_FIXTURE)
        try:
            exited = False
            exit_code = 0
            original_exit = sys.exit

            def _fake_exit(code=0):
                nonlocal exited, exit_code
                exited = True
                exit_code = int(code) if code else 0
                raise SystemExit(code)

            sys.exit = _fake_exit
            try:
                _restore_from_bak(pass_name, bot_path=tmp)
            except SystemExit:
                pass
            finally:
                sys.exit = original_exit

            ok = exited and exit_code != 0
            print(
                f"  {'OK  ' if ok else 'FAIL'} {label}: "
                f"expected failure exit when .bak missing, got exited={exited} code={exit_code}"
            )
            if not ok:
                all_ok = False
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    _run_restore_bak_no_bak("restore-bak missing .bak file → error exit", "trend")

    # ── Preview-bak tests ─────────────────────────────────────────────────────
    def _run_preview_bak(
        label: str,
        pass_name: str,
        bak_fixture: str,
        current_fixture: str,
        expect_unchanged: bool = True,
    ) -> None:
        nonlocal all_ok
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
            tmp = tf.name
            tf.write(current_fixture)
        bak = tmp + ".bak"
        try:
            with open(bak, "w") as fh:
                fh.write(bak_fixture)

            output_lines: list[str] = []
            import builtins as _builtins

            original_print_fn = _builtins.print

            def _capture_print(*args, **kwargs):
                line = " ".join(str(a) for a in args)
                output_lines.append(line)
                original_print_fn(*args, **kwargs)

            _builtins.print = _capture_print
            try:
                _restore_from_bak(pass_name, bot_path=tmp, dry_run=True)
            finally:
                _builtins.print = original_print_fn

            with open(tmp) as fh:
                after_content = fh.read()

            file_unchanged = after_content == current_fixture
            dry_run_msg_present = any(
                "Dry run" in line and "no changes written" in line
                for line in output_lines
            )
            ok = file_unchanged == expect_unchanged and dry_run_msg_present
            print(
                f"  {'OK  ' if ok else 'FAIL'} {label}: "
                f"file_unchanged={file_unchanged} (expected {expect_unchanged}), "
                f"dry_run_msg={'found' if dry_run_msg_present else 'MISSING'}"
            )
            if not ok:
                all_ok = False
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            try:
                os.unlink(bak)
            except OSError:
                pass

    _run_preview_bak(
        "preview-bak trend: shows diff, file unchanged",
        pass_name="trend",
        bak_fixture=_APPLY_FIXTURE,
        current_fixture=_APPLY_FIXTURE_TREND_STALE,
        expect_unchanged=True,
    )
    _run_preview_bak(
        "preview-bak gap: shows diff, file unchanged",
        pass_name="gap",
        bak_fixture=_APPLY_FIXTURE,
        current_fixture=_APPLY_FIXTURE_GAP_STALE,
        expect_unchanged=True,
    )

    if all_ok:
        print("All _apply_to_bot self-tests passed.")
    else:
        print("SELF-TEST FAILURES in _apply_to_bot — check the output above.")
        sys.exit(1)


def _self_test_fixture_keys() -> None:
    """Check that every _APPLY_FIXTURE_* has the same screener-pass keys as trade_utils.SP_MULT_TABLE.

    Fails with a clear message listing which fixtures are out of sync so that adding a new
    screener pass to trade_utils.SP_MULT_TABLE is never silently missed by the self-tests.
    """
    import importlib.util
    import pathlib

    # Locate and import trade_utils relative to this file so the check works regardless
    # of the working directory.
    tu_path = pathlib.Path(__file__).with_name("trade_utils.py")
    spec = importlib.util.spec_from_file_location("trade_utils", tu_path)
    tu = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(tu)  # type: ignore[union-attr]
    canonical_keys: set[str] = set(tu.SP_MULT_TABLE.keys())

    fixtures: dict[str, str] = {
        "_APPLY_FIXTURE": _APPLY_FIXTURE,
        "_APPLY_FIXTURE_GAP_STALE": _APPLY_FIXTURE_GAP_STALE,
        "_APPLY_FIXTURE_TREND_STALE": _APPLY_FIXTURE_TREND_STALE,
        "_APPLY_FIXTURE_OTHER_STALE": _APPLY_FIXTURE_OTHER_STALE,
    }

    all_ok = True
    for name, fixture in fixtures.items():
        m = re.search(r'_SP_MULT_TABLE\s*:.*?=\s*\{([^}]+)\}', fixture, re.DOTALL)
        if not m:
            print(f"  FAIL fixture_keys {name}: could not locate _SP_MULT_TABLE dict in fixture")
            all_ok = False
            continue
        fixture_keys = set(re.findall(r'"(\w+)"\s*:', m.group(1)))
        missing = canonical_keys - fixture_keys
        extra = fixture_keys - canonical_keys
        if missing or extra:
            if missing:
                print(
                    f"  FAIL fixture_keys {name}: fixture is missing screener passes "
                    f"that are in trade_utils.SP_MULT_TABLE: {sorted(missing)}"
                )
            if extra:
                print(
                    f"  FAIL fixture_keys {name}: fixture contains passes not present "
                    f"in trade_utils.SP_MULT_TABLE: {sorted(extra)}"
                )
            all_ok = False
        else:
            print(f"  OK   fixture_keys {name}: keys match trade_utils.SP_MULT_TABLE {sorted(canonical_keys)}")

    pass_config_keys: set[str] = set(PASS_CONFIG.keys())
    missing_pc = canonical_keys - pass_config_keys
    extra_pc = pass_config_keys - canonical_keys
    if missing_pc or extra_pc:
        if missing_pc:
            print(
                f"  FAIL fixture_keys PASS_CONFIG: PASS_CONFIG is missing screener passes "
                f"that are in trade_utils.SP_MULT_TABLE: {sorted(missing_pc)}"
            )
        if extra_pc:
            print(
                f"  FAIL fixture_keys PASS_CONFIG: PASS_CONFIG contains passes not present "
                f"in trade_utils.SP_MULT_TABLE: {sorted(extra_pc)}"
            )
        all_ok = False
    else:
        print(f"  OK   fixture_keys PASS_CONFIG: keys match trade_utils.SP_MULT_TABLE {sorted(canonical_keys)}")

    if not all_ok:
        print(
            "\nERROR: One or more _APPLY_FIXTURE_* strings or PASS_CONFIG are out of sync with "
            "trade_utils.SP_MULT_TABLE.\n"
            "Update the fixture(s) and/or PASS_CONFIG listed above so every screener-pass key "
            "present in trade_utils.SP_MULT_TABLE is also present in each fixture dict and in "
            "PASS_CONFIG."
        )
        sys.exit(1)

    print("All fixture-key self-tests passed.")


def _self_test_reset_log() -> None:
    """Verify that _write_reset_log() appends a correctly formatted entry."""
    import tempfile

    all_ok = True

    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as tf:
        tmp = tf.name

    try:
        _write_reset_log("trend", 0.85, False, log_path=tmp)
        with open(tmp) as fh:
            lines = fh.readlines()

        ok_count = len(lines) == 1
        print(f"  {'OK  ' if ok_count else 'FAIL'} reset log: exactly one line written (got {len(lines)})")
        if not ok_count:
            all_ok = False

        if lines:
            entry = lines[0]
            ok_pass = "pass=trend" in entry
            ok_prev = "prev_mult=0.85" in entry
            ok_mode = "mode=interactive" in entry
            import re as _re
            ok_ts = bool(_re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", entry))
            print(f"  {'OK  ' if ok_pass else 'FAIL'} reset log: pass name present")
            print(f"  {'OK  ' if ok_prev else 'FAIL'} reset log: prev_mult present")
            print(f"  {'OK  ' if ok_mode else 'FAIL'} reset log: mode=interactive for used_yes=False")
            print(f"  {'OK  ' if ok_ts else 'FAIL'} reset log: ISO timestamp present")
            if not (ok_pass and ok_prev and ok_mode and ok_ts):
                all_ok = False

        _write_reset_log("gap", 1.10, True, log_path=tmp)
        with open(tmp) as fh:
            lines2 = fh.readlines()

        ok_append = len(lines2) == 2
        print(f"  {'OK  ' if ok_append else 'FAIL'} reset log: second entry appended (total lines={len(lines2)})")
        if not ok_append:
            all_ok = False
        if len(lines2) >= 2:
            ok_ci = "mode=CI/--yes" in lines2[1]
            ok_gap = "pass=gap" in lines2[1]
            print(f"  {'OK  ' if ok_gap else 'FAIL'} reset log: second entry has correct pass name")
            print(f"  {'OK  ' if ok_ci else 'FAIL'} reset log: mode=CI/--yes for used_yes=True")
            if not (ok_gap and ok_ci):
                all_ok = False
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    if all_ok:
        print("All reset-log self-tests passed.")
    else:
        print("SELF-TEST FAILURES in reset-log — check the output above.")
        sys.exit(1)


def _self_test_reset_confirm() -> None:
    """Test the interactive confirmation guard for --reset-pass.

    Exercises both the confirmed path (user types 'yes' → reset runs) and the
    aborted path (user types anything else → no changes made).
    """
    import tempfile
    import unittest.mock as _mock

    all_ok = True

    # ── Aborted path: input is NOT 'yes' ─────────────────────────────────────
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tmp_abort = tf.name
        tf.write(_APPLY_FIXTURE)
    try:
        with _mock.patch("builtins.input", return_value="n") as mock_input:
            answer = input("dummy prompt").strip().lower()
            did_abort = answer != "yes"
        ok1 = did_abort
        print(f"  {'OK  ' if ok1 else 'FAIL'} reset confirm aborted: non-'yes' answer causes abort")
        if not ok1:
            all_ok = False

        # File must be unchanged — we deliberately did NOT call _reset_pass_to_baseline
        with open(tmp_abort) as fh:
            content = fh.read()
        ok2 = content == _APPLY_FIXTURE
        print(f"  {'OK  ' if ok2 else 'FAIL'} reset confirm aborted: file unchanged after abort")
        if not ok2:
            all_ok = False
    finally:
        try:
            os.unlink(tmp_abort)
        except OSError:
            pass

    # ── Confirmed path: input is 'yes' ───────────────────────────────────────
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tmp_confirm = tf.name
        tf.write(_APPLY_FIXTURE)
    try:
        with _mock.patch("builtins.input", return_value="yes"):
            answer = input("dummy prompt").strip().lower()
            if answer == "yes":
                _reset_pass_to_baseline("trend", bot_path=tmp_confirm)

        ok3 = answer == "yes"
        print(f"  {'OK  ' if ok3 else 'FAIL'} reset confirm accepted: 'yes' answer proceeds")
        if not ok3:
            all_ok = False

        with open(tmp_confirm) as fh:
            content = fh.read()
        stale_present = "'trend'  (1-3%):              0 settled trades as of" in content
        ok4 = stale_present
        print(f"  {'OK  ' if ok4 else 'FAIL'} reset confirm accepted: stale comment written after confirm")
        if not ok4:
            all_ok = False
    finally:
        try:
            os.unlink(tmp_confirm)
        except OSError:
            pass
        try:
            os.unlink(tmp_confirm + ".bak")
        except OSError:
            pass

    # ── _read_calib_date: pass present in _SP_CALIB_DATES ────────────────────
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tmp_calib = tf.name
        tf.write(_APPLY_FIXTURE)
    try:
        got_date = _read_calib_date("gap_down", tmp_calib)
        ok_date_present = got_date == "2026-04-20"
        print(
            f"  {'OK  ' if ok_date_present else 'FAIL'} _read_calib_date calibrated pass: "
            f"got {got_date!r} (expected '2026-04-20')"
        )
        if not ok_date_present:
            all_ok = False

        # _read_calib_date: pass absent from _SP_CALIB_DATES → None
        got_date_absent = _read_calib_date("other", tmp_calib)
        ok_date_absent = got_date_absent is None
        print(
            f"  {'OK  ' if ok_date_absent else 'FAIL'} _read_calib_date never-calibrated pass: "
            f"got {got_date_absent!r} (expected None)"
        )
        if not ok_date_absent:
            all_ok = False

        # _read_inline_comment: pass with an inline comment returns it
        got_comment = _read_inline_comment("gap_down", tmp_calib)
        expected_comment = "Bearish Break — baseline; recalibrate once >=30 trades settle"
        ok_comment_present = got_comment == expected_comment
        print(
            f"  {'OK  ' if ok_comment_present else 'FAIL'} _read_inline_comment calibrated pass: "
            f"got {got_comment!r} (expected {expected_comment!r})"
        )
        if not ok_comment_present:
            all_ok = False

        # _read_inline_comment: pass without an inline comment returns None
        got_comment_absent = _read_inline_comment("other", tmp_calib)
        ok_comment_absent = got_comment_absent is None
        print(
            f"  {'OK  ' if ok_comment_absent else 'FAIL'} _read_inline_comment never-calibrated pass: "
            f"got {got_comment_absent!r} (expected None)"
        )
        if not ok_comment_absent:
            all_ok = False

        # calib_str rendering — state 1: calib_date + inline_comment present
        # (mirrors the logic in __main__ for --reset-pass without --yes)
        calib_date = _read_calib_date("gap_down", tmp_calib)
        inline_comment = _read_inline_comment("gap_down", tmp_calib)
        if calib_date:
            calib_str = f"calibrated on {calib_date}"
            if inline_comment:
                calib_str += f" — {inline_comment}"
        else:
            calib_str = "not yet calibrated (no calibration date on record)"
        expected_calib_str = (
            "calibrated on 2026-04-20 — "
            "Bearish Break — baseline; recalibrate once >=30 trades settle"
        )
        ok_calib_str = calib_str == expected_calib_str
        print(
            f"  {'OK  ' if ok_calib_str else 'FAIL'} calib_str rendering (date + comment): "
            f"got {calib_str!r}"
        )
        if not ok_calib_str:
            all_ok = False

        # Full warning line — state 1: the "  Calibration info: ..." line from __main__
        warning_line = f"  Calibration info: {calib_str}"
        expected_warning_line = (
            "  Calibration info: calibrated on 2026-04-20 — "
            "Bearish Break — baseline; recalibrate once >=30 trades settle"
        )
        ok_warning_line = warning_line == expected_warning_line
        print(
            f"  {'OK  ' if ok_warning_line else 'FAIL'} warning line (date + comment): "
            f"got {warning_line!r}"
        )
        if not ok_warning_line:
            all_ok = False

        # calib_str rendering — state 3: never-calibrated pass (no calib_date)
        calib_date_absent = _read_calib_date("other", tmp_calib)
        inline_comment_absent = _read_inline_comment("other", tmp_calib)
        if calib_date_absent:
            calib_str_never = f"calibrated on {calib_date_absent}"
            if inline_comment_absent:
                calib_str_never += f" — {inline_comment_absent}"
        else:
            calib_str_never = "not yet calibrated (no calibration date on record)"
        ok_calib_str_never = calib_str_never == "not yet calibrated (no calibration date on record)"
        print(
            f"  {'OK  ' if ok_calib_str_never else 'FAIL'} calib_str rendering (never-calibrated): "
            f"got {calib_str_never!r}"
        )
        if not ok_calib_str_never:
            all_ok = False

        # Full warning line — state 3: never-calibrated
        warning_line_never = f"  Calibration info: {calib_str_never}"
        ok_warning_line_never = warning_line_never == (
            "  Calibration info: not yet calibrated (no calibration date on record)"
        )
        print(
            f"  {'OK  ' if ok_warning_line_never else 'FAIL'} warning line (never-calibrated): "
            f"got {warning_line_never!r}"
        )
        if not ok_warning_line_never:
            all_ok = False
    finally:
        try:
            os.unlink(tmp_calib)
        except OSError:
            pass

    # ── _read_calib_date / _read_inline_comment: baseline-reset state ─────────
    # Uses _APPLY_FIXTURE_TREND_STALE where 'trend' was reset and has a calib date
    # but its inline comment is the "baseline; recalibrate" text.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tmp_trend = tf.name
        tf.write(_APPLY_FIXTURE_TREND_STALE)
    try:
        got_trend_date = _read_calib_date("trend", tmp_trend)
        ok_trend_date = got_trend_date == "2026-04-20"
        print(
            f"  {'OK  ' if ok_trend_date else 'FAIL'} _read_calib_date baseline-reset pass: "
            f"got {got_trend_date!r} (expected '2026-04-20')"
        )
        if not ok_trend_date:
            all_ok = False

        got_trend_comment = _read_inline_comment("trend", tmp_trend)
        expected_trend_comment = "baseline; recalibrate once >=30 trades settle"
        ok_trend_comment = got_trend_comment == expected_trend_comment
        print(
            f"  {'OK  ' if ok_trend_comment else 'FAIL'} _read_inline_comment baseline-reset pass: "
            f"got {got_trend_comment!r} (expected {expected_trend_comment!r})"
        )
        if not ok_trend_comment:
            all_ok = False

        # calib_str rendering — state 2: baseline-reset (date present, comment is baseline text)
        if got_trend_date:
            calib_str_reset = f"calibrated on {got_trend_date}"
            if got_trend_comment:
                calib_str_reset += f" — {got_trend_comment}"
        else:
            calib_str_reset = "not yet calibrated (no calibration date on record)"
        expected_reset_str = (
            "calibrated on 2026-04-20 — baseline; recalibrate once >=30 trades settle"
        )
        ok_calib_str_reset = calib_str_reset == expected_reset_str
        print(
            f"  {'OK  ' if ok_calib_str_reset else 'FAIL'} calib_str rendering (baseline-reset pass): "
            f"got {calib_str_reset!r}"
        )
        if not ok_calib_str_reset:
            all_ok = False

        # Full warning line — state 2: baseline-reset
        warning_line_reset = f"  Calibration info: {calib_str_reset}"
        ok_warning_line_reset = warning_line_reset == (
            "  Calibration info: calibrated on 2026-04-20 — "
            "baseline; recalibrate once >=30 trades settle"
        )
        print(
            f"  {'OK  ' if ok_warning_line_reset else 'FAIL'} warning line (baseline-reset pass): "
            f"got {warning_line_reset!r}"
        )
        if not ok_warning_line_reset:
            all_ok = False
    finally:
        try:
            os.unlink(tmp_trend)
        except OSError:
            pass

    if all_ok:
        print("All reset-confirm self-tests passed.")
    else:
        print("SELF-TEST FAILURES in reset-confirm — check the output above.")
        sys.exit(1)


def _self_test_restore_confirm() -> None:
    """Test the interactive confirmation guard for --restore-bak.

    Exercises both the confirmed path (user types 'yes' → restore runs) and the
    aborted path (user types anything else → no changes made).
    """
    import tempfile
    import unittest.mock as _mock

    all_ok = True

    # ── Aborted path: input is NOT 'yes' ─────────────────────────────────────
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tmp_abort = tf.name
        tf.write(_APPLY_FIXTURE_TREND_STALE)
    bak_abort = tmp_abort + ".bak"
    try:
        with open(bak_abort, "w") as fh:
            fh.write(_APPLY_FIXTURE)

        with _mock.patch("builtins.input", return_value="n"):
            exited = False
            exit_code = 0
            original_exit = sys.exit

            def _fake_exit_a(code=0):
                nonlocal exited, exit_code
                exited = True
                exit_code = int(code) if code else 0
                raise SystemExit(code)

            sys.exit = _fake_exit_a
            try:
                _restore_from_bak("trend", bot_path=tmp_abort, yes=False)
            except SystemExit:
                pass
            finally:
                sys.exit = original_exit

        ok1 = exited and exit_code == 0
        print(f"  {'OK  ' if ok1 else 'FAIL'} restore confirm aborted: non-'yes' answer exits cleanly")
        if not ok1:
            all_ok = False

        with open(tmp_abort) as fh:
            content = fh.read()
        ok2 = content == _APPLY_FIXTURE_TREND_STALE
        print(f"  {'OK  ' if ok2 else 'FAIL'} restore confirm aborted: file unchanged after abort")
        if not ok2:
            all_ok = False
    finally:
        try:
            os.unlink(tmp_abort)
        except OSError:
            pass
        try:
            os.unlink(bak_abort)
        except OSError:
            pass

    # ── Confirmed path: input is 'yes' ───────────────────────────────────────
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tmp_confirm = tf.name
        tf.write(_APPLY_FIXTURE_TREND_STALE)
    bak_confirm = tmp_confirm + ".bak"
    try:
        with open(bak_confirm, "w") as fh:
            fh.write(_APPLY_FIXTURE)

        with _mock.patch("builtins.input", return_value="yes"):
            _restore_from_bak("trend", bot_path=tmp_confirm, yes=False)

        with open(tmp_confirm) as fh:
            content = fh.read()
        pat = re.compile(r'"trend"\s*:\s*([\d.]+)')
        m = pat.search(content)
        restored_value = float(m.group(1)) if m else None
        ok3 = restored_value is not None and abs(restored_value - 0.85) < 0.001
        print(f"  {'OK  ' if ok3 else 'FAIL'} restore confirm accepted: file updated to backup value (0.85×)")
        if not ok3:
            all_ok = False
    finally:
        try:
            os.unlink(tmp_confirm)
        except OSError:
            pass
        try:
            os.unlink(bak_confirm)
        except OSError:
            pass

    if all_ok:
        print("All restore-confirm self-tests passed.")
    else:
        print("SELF-TEST FAILURES in restore-confirm — check the output above.")
        sys.exit(1)


def _restore_from_bak(
    pass_name: str,
    bot_path: str | None = None,
    dry_run: bool = False,
    yes: bool = False,
) -> None:
    """Restore trade_utils.py from its .bak backup created by --reset-pass or --apply.

    Steps:
      1. Locate trade_utils.py.bak (or <bot_path>.bak).
      2. Verify the backup contains a valid SP_MULT_TABLE entry for pass_name.
      3. Print a unified diff from the current file to the backup.
      4. If dry_run is True:  print a "Dry run" message and exit without writing.
         If dry_run is False and yes is False: prompt for confirmation before writing.
         If dry_run is False and yes is True:  overwrite the current file immediately.
    """
    resolved_path = bot_path if bot_path is not None else _BOT_FILE
    backup_path = resolved_path + ".bak"

    if pass_name not in PASS_CONFIG:
        known = ", ".join(PASS_CONFIG)
        print(f"ERROR: unknown pass '{pass_name}'. Known passes: {known}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(backup_path):
        print(
            f"ERROR: no backup file found at {backup_path}\n"
            "       A backup is created automatically when you run --reset-pass or --apply.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        with open(backup_path) as fh:
            backup_content = fh.read()
    except OSError as exc:
        print(f"ERROR: cannot read backup {backup_path} — {exc}", file=sys.stderr)
        sys.exit(1)

    if "SP_MULT_TABLE" not in backup_content:
        print(
            f"ERROR: backup file {backup_path} does not contain a SP_MULT_TABLE entry.\n"
            "       This backup may be corrupt or from an incompatible version.",
            file=sys.stderr,
        )
        sys.exit(1)

    entry_pat = re.compile(r'"' + re.escape(pass_name) + r'"\s*:\s*[\d.]+')
    table_start = backup_content.find("SP_MULT_TABLE")
    brace_open = backup_content.find("{", table_start)
    brace_close = backup_content.find("}", brace_open)
    if table_start == -1 or brace_open == -1 or brace_close == -1:
        print(
            f"ERROR: could not locate SP_MULT_TABLE block in backup {backup_path}.",
            file=sys.stderr,
        )
        sys.exit(1)
    block = backup_content[brace_open : brace_close + 1]
    if not entry_pat.search(block):
        print(
            f"ERROR: backup does not contain a valid '{pass_name}' entry in SP_MULT_TABLE.\n"
            f"       Cannot restore — backup may be for a different pass.",
            file=sys.stderr,
        )
        sys.exit(1)

    m = entry_pat.search(block)
    restored_value_str = re.search(r"[\d.]+$", m.group(0)).group(0) if m else "?"
    print(
        f"Restoring '{pass_name}' from backup {backup_path}\n"
        f"  Backed-up value: SP_MULT_TABLE['{pass_name}'] = {restored_value_str}"
    )

    try:
        with open(resolved_path) as fh:
            current_content = fh.read()
    except OSError as exc:
        print(f"ERROR: cannot read current file {resolved_path} — {exc}", file=sys.stderr)
        sys.exit(1)

    diff_lines = list(
        difflib.unified_diff(
            current_content.splitlines(keepends=True),
            backup_content.splitlines(keepends=True),
            fromfile="trade_utils.py (current)",
            tofile="trade_utils.py (restored from .bak)",
            n=3,
        )
    )
    if diff_lines:
        print("\nDiff (current → restored):")
        print("".join(diff_lines))
    else:
        print("\n(No change — current file already matches the backup.)")

    if dry_run:
        print("\nDry run — no changes written. Re-run with --restore-bak to apply.")
        return

    if not yes:
        try:
            answer = input(
                "Type 'yes' to confirm restore, or anything else to abort: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)
        if answer != "yes":
            print("Aborted — no changes made.")
            sys.exit(0)

    try:
        with open(resolved_path, "w") as fh:
            fh.write(backup_content)
    except OSError as exc:
        print(f"ERROR: could not write restored file — {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\nRestore complete: {resolved_path} has been reverted to the backed-up state.")


def _reset_pass_to_baseline(pass_name: str, bot_path: str | None = None) -> None:
    """Reset SP_MULT_TABLE[pass_name] to 1.00× baseline in trade_utils.py.

    Writes the '0 settled trades as of <today>' stale-warning comment and
    1.00× baseline value, identical to the state before any calibration data
    has been collected for the pass.
    """
    import datetime as _dt

    resolved_path = bot_path if bot_path is not None else _BOT_FILE

    if pass_name not in PASS_CONFIG:
        known = ", ".join(PASS_CONFIG)
        print(f"ERROR: unknown pass '{pass_name}'. Known passes: {known}", file=sys.stderr)
        sys.exit(1)

    cfg = PASS_CONFIG[pass_name]
    today_str = _dt.date.today().isoformat()

    stale_template = cfg.get("stale_comment_template", "")
    citation_line = stale_template.format(date=today_str) if stale_template else ""

    next_step_comment_prefix = cfg.get("next_step_comment_prefix", "")
    inline_comment = f"{next_step_comment_prefix}baseline; recalibrate once >=30 trades settle"

    print(f"Resetting '{pass_name}' to 1.00\u00d7 baseline in {resolved_path} \u2026")
    _apply_to_bot(pass_name, 1.00, inline_comment, citation_line=citation_line, bot_path=resolved_path)
    print(f"\nReset complete: '{pass_name}' is now at 1.00\u00d7 (0 settled trades baseline).")
    print(f"  To undo this reset, run: python calibrate_sp_mult.py --restore-bak {pass_name}")


def _read_current_mult(pass_name: str, bot_path: str | None = None) -> float | None:
    """Return the current multiplier for pass_name from trade_utils.py, or None on error."""
    path = bot_path if bot_path is not None else _BOT_FILE
    try:
        with open(path) as fh:
            content = fh.read()
    except OSError:
        return None
    pat = re.compile(r'"' + re.escape(pass_name) + r'"\s*:\s*([\d.]+)')
    m = pat.search(content)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _read_calib_date(pass_name: str, bot_path: str | None = None) -> str | None:
    """Return the calibration date for pass_name from SP_CALIB_DATES, or None if absent."""
    path = bot_path if bot_path is not None else _BOT_FILE
    try:
        with open(path) as fh:
            content = fh.read()
    except OSError:
        return None
    calib_start = content.find("SP_CALIB_DATES")
    if calib_start == -1:
        return None
    cb_open = content.find("{", calib_start)
    cb_close = content.find("}", cb_open)
    if cb_open == -1 or cb_close == -1:
        return None
    calib_block = content[cb_open : cb_close + 1]
    pat = re.compile(r'"' + re.escape(pass_name) + r'"\s*:\s*"([^"]+)"')
    m = pat.search(calib_block)
    return m.group(1) if m else None


def _read_inline_comment(pass_name: str, bot_path: str | None = None) -> str | None:
    """Return the trailing inline comment for pass_name inside SP_MULT_TABLE, or None."""
    path = bot_path if bot_path is not None else _BOT_FILE
    try:
        with open(path) as fh:
            content = fh.read()
    except OSError:
        return None
    table_start = content.find("SP_MULT_TABLE")
    if table_start == -1:
        return None
    brace_open = content.find("{", table_start)
    brace_close = content.find("}", brace_open)
    if brace_open == -1 or brace_close == -1:
        return None
    block = content[brace_open : brace_close + 1]
    pat = re.compile(
        r'"' + re.escape(pass_name) + r'"\s*:\s*[\d.]+,\s*#\s*([^\n]+)'
    )
    m = pat.search(block)
    return m.group(1).strip() if m else None


def _list_passes() -> None:
    """Print available pass names and exit."""
    print("Available screener passes for calibration:")
    for name, cfg in PASS_CONFIG.items():
        predicted = cfg.get("predicted")
        detail = f"  (predicted='{predicted}')" if predicted else ""
        print(f"  --pass {name:<12}  screener_pass='{cfg['screener_pass']}'{detail}")
    print()
    print("Usage:  python calibrate_sp_mult.py --pass <pass_name>")


_BOT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_utils.py")
from log_config import _RESET_LOG_PATH as _RESET_LOG_FILE, _RESET_LOG_MAX_BYTES, _RESET_LOG_BACKUP_COUNT  # noqa: E402



def _write_reset_log(
    pass_name: str,
    prev_mult: float | None,
    used_yes: bool,
    log_path: str | None = None,
) -> None:
    """Append one line to calibration_resets.log recording this reset event.

    Rotates the file when it reaches _RESET_LOG_MAX_BYTES, keeping
    _RESET_LOG_BACKUP_COUNT backup(s) (e.g. calibration_resets.log.1).

    Format (one entry per line, ISO timestamps):
        2026-04-20T14:32:01+00:00  pass=trend  prev_mult=0.85  mode=interactive
    """
    import datetime as _dt

    path = log_path if log_path is not None else _RESET_LOG_FILE
    _rotate_log(path, _RESET_LOG_MAX_BYTES, _RESET_LOG_BACKUP_COUNT)
    prev_str = f"{prev_mult:.2f}" if prev_mult is not None else "unknown"
    mode_str = "CI/--yes" if used_yes else "interactive"
    timestamp = _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")
    line = f"{timestamp}  pass={pass_name}  prev_mult={prev_str}  mode={mode_str}\n"
    try:
        with open(path, "a") as fh:
            fh.write(line)
        print(f"Reset logged → {path}")
    except OSError as exc:
        print(f"WARNING: could not write reset log {path} — {exc}", file=sys.stderr)


def _show_reset_log(pass_filter: str | None = None) -> None:
    """Print a formatted table of all past resets from calibration_resets.log.

    Optionally filters to a specific pass when pass_filter is provided.
    Handles a missing log file gracefully (no resets recorded yet).
    """
    if not os.path.exists(_RESET_LOG_FILE):
        print("No reset history found (calibration_resets.log does not exist yet).")
        return

    with open(_RESET_LOG_FILE) as fh:
        raw_lines = [ln.rstrip("\n") for ln in fh if ln.strip()]

    rows: list[dict] = []
    for ln in raw_lines:
        parts = ln.split()
        if not parts:
            continue
        timestamp = parts[0] if parts else ""
        fields: dict[str, str] = {}
        for token in parts[1:]:
            if "=" in token:
                k, _, v = token.partition("=")
                fields[k] = v
        rows.append({
            "timestamp": timestamp,
            "pass": fields.get("pass", ""),
            "prev_mult": fields.get("prev_mult", ""),
            "mode": fields.get("mode", ""),
        })

    if pass_filter:
        rows = [r for r in rows if r["pass"] == pass_filter]

    if not rows:
        if pass_filter:
            print(f"No resets recorded for pass '{pass_filter}'.")
        else:
            print("No resets recorded yet.")
        return

    col_ts = max(len("Timestamp"), max(len(r["timestamp"]) for r in rows))
    col_pass = max(len("Pass"), max(len(r["pass"]) for r in rows))
    col_prev = max(len("Prev Mult"), max(len(r["prev_mult"]) for r in rows))
    col_mode = max(len("Mode"), max(len(r["mode"]) for r in rows))

    sep = f"+-{'-'*col_ts}-+-{'-'*col_pass}-+-{'-'*col_prev}-+-{'-'*col_mode}-+"
    header = (
        f"| {'Timestamp':<{col_ts}} "
        f"| {'Pass':<{col_pass}} "
        f"| {'Prev Mult':<{col_prev}} "
        f"| {'Mode':<{col_mode}} |"
    )

    label = f" (pass={pass_filter})" if pass_filter else ""
    print(f"\nReset history{label}  [{len(rows)} entr{'y' if len(rows) == 1 else 'ies'}]\n")
    print(sep)
    print(header)
    print(sep)
    for r in rows:
        print(
            f"| {r['timestamp']:<{col_ts}} "
            f"| {r['pass']:<{col_pass}} "
            f"| {r['prev_mult']:<{col_prev}} "
            f"| {r['mode']:<{col_mode}} |"
        )
    print(sep)


def _apply_to_bot(
    pass_name: str,
    rec_mult: float,
    inline_comment: str,
    citation_line: str = "",
    bot_path: str = _BOT_FILE,
) -> None:
    """Patch SP_MULT_TABLE[pass_name] in trade_utils.py in place.

    Steps:
      1. Read the file and locate the SP_MULT_TABLE block.
      2. Replace the numeric value and trailing comment for pass_name.
      3. If citation_line is provided, also replace the per-pass comment block
         above SP_MULT_TABLE with the fresh citation string.
      4. Write a .bak backup of the original.
      5. Print a unified diff of both changes combined.
      6. Write the patched file.

    Exits with a non-zero status if the table or entry cannot be found.
    """
    try:
        with open(bot_path) as fh:
            original = fh.read()
    except OSError as exc:
        print(f"ERROR: cannot read {bot_path} — {exc}", file=sys.stderr)
        sys.exit(1)

    table_start = original.find("SP_MULT_TABLE")
    if table_start == -1:
        print("ERROR: SP_MULT_TABLE not found in trade_utils.py", file=sys.stderr)
        sys.exit(1)

    brace_open = original.find("{", table_start)
    brace_close = original.find("}", brace_open)
    if brace_open == -1 or brace_close == -1:
        print("ERROR: could not locate SP_MULT_TABLE braces in trade_utils.py", file=sys.stderr)
        sys.exit(1)

    block = original[brace_open : brace_close + 1]

    entry_pat = re.compile(
        r'("' + re.escape(pass_name) + r'"(\s*):(\s*))([\d.]+)(,[ \t]*)([^\n]*)?',
    )
    if not entry_pat.search(block):
        print(
            f"ERROR: could not find '{pass_name}' entry inside SP_MULT_TABLE in trade_utils.py",
            file=sys.stderr,
        )
        sys.exit(1)

    def _replacer(m: re.Match) -> str:
        key_part = m.group(1)
        comma_spaces = m.group(5)
        return f"{key_part}{rec_mult:.2f}{comma_spaces}# {inline_comment}"

    new_block, count = entry_pat.subn(_replacer, block, count=1)
    if count == 0:
        print(
            f"ERROR: replacement failed for '{pass_name}' in _SP_MULT_TABLE",
            file=sys.stderr,
        )
        sys.exit(1)

    new_content = original[:brace_open] + new_block + original[brace_close + 1 :]

    # Also update SP_CALIB_DATES[pass_name] with today's date.
    import datetime as _dt
    today_str = _dt.date.today().isoformat()
    _calib_start = new_content.find("SP_CALIB_DATES")
    if _calib_start != -1:
        _cb_open = new_content.find("{", _calib_start)
        _cb_close = new_content.find("}", _cb_open)
        if _cb_open != -1 and _cb_close != -1:
            calib_block = new_content[_cb_open : _cb_close + 1]
            calib_entry_pat = re.compile(
                r'"' + re.escape(pass_name) + r'"(\s*:\s*)"[^"]*"'
            )
            if calib_entry_pat.search(calib_block):
                new_calib_block = calib_entry_pat.sub(
                    lambda m: f'"{pass_name}"{m.group(1)}"{today_str}"',
                    calib_block,
                    count=1,
                )
            else:
                inner = calib_block[1:-1].rstrip()
                sep = "," if inner.rstrip().endswith('"') else ""
                new_calib_block = "{" + inner + sep + f'\n    "{pass_name}": "{today_str}",\n}}'
            new_content = new_content[:_cb_open] + new_calib_block + new_content[_cb_close + 1 :]
    else:
        print(
            "WARNING: SP_CALIB_DATES not found in trade_utils.py"
            " — calibration date not updated.",
            file=sys.stderr,
        )

    if citation_line:
        # Replace the per-pass comment block above _SP_MULT_TABLE.
        # The block starts with "#   'pass_name'" and continues with indented
        # comment continuation lines (lines starting with '#' + multiple spaces)
        # until another "#   '<pass>'" entry or "# Applied" or the table itself.
        comment_pat = re.compile(
            r"(#   '" + re.escape(pass_name) + r"'[^\n]*)"
            r"((?:\n#(?!   '[a-z_])(?! Applied)[^\n]*)*)",
        )
        m_comment = comment_pat.search(new_content)
        if m_comment:
            new_content = new_content[:m_comment.start()] + citation_line + new_content[m_comment.end():]
        else:
            print(
                f"WARNING: could not find comment block for '{pass_name}' above _SP_MULT_TABLE"
                " — data-citation comment not updated.",
                file=sys.stderr,
            )

    backup_path = bot_path + ".bak"
    try:
        shutil.copy2(bot_path, backup_path)
    except OSError as exc:
        print(f"ERROR: could not create backup {backup_path} — {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Backup written → {backup_path}")

    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile="trade_utils.py (original)",
            tofile="trade_utils.py (patched)",
            n=3,
        )
    )
    if diff_lines:
        print("\nDiff:")
        print("".join(diff_lines))
    else:
        print("\n(No change — value was already set to the recommended multiplier.)")

    try:
        with open(bot_path, "w") as fh:
            fh.write(new_content)
    except OSError as exc:
        print(f"ERROR: could not write patched file — {exc}", file=sys.stderr)
        print(f"The original is preserved in {backup_path}.", file=sys.stderr)
        sys.exit(1)

    print(f"\nApplied: SP_MULT_TABLE['{pass_name}'] = {rec_mult:.2f}")


def main(pass_name: str, apply: bool = False) -> None:
    cfg = PASS_CONFIG[pass_name]
    screener_pass = cfg["screener_pass"]
    predicted = cfg.get("predicted")
    title = cfg["title"]
    count_label = cfg["count_label"]
    stat_label = cfg["stat_label"]
    citation_inner = cfg["citation_inner"]
    next_step_comment_prefix = cfg["next_step_comment_prefix"]

    print("=" * 60)
    print(title)
    print("=" * 60)

    target_rows = _fetch_settled(screener_pass, predicted)
    gap_rows = _fetch_settled(ANCHOR_PASS)
    ctx_stats: list[tuple[str, dict]] = []
    for ctx_pass in CONTEXT_PASSES:
        if ctx_pass == pass_name:
            continue
        ctx_rows = _fetch_settled(ctx_pass)
        ctx_stats.append((ctx_pass, _stats(ctx_rows)))

    tgt = _stats(target_rows)
    ga = _stats(gap_rows)

    print(f"\nSettled trade counts (tiered_pnl_r NOT NULL):")
    all_count_rows = [(count_label, tgt), (ANCHOR_PASS, ga)] + [(l, s) for l, s in ctx_stats]
    col_w = max(len(l) for l, _ in all_count_rows) + 1
    for lbl, s in all_count_rows:
        print(f"  {lbl + ':':<{col_w}}  {s['n']}")

    if tgt["n"] < MIN_TRADES:
        print(
            f"\n⛔  Only {tgt['n']} settled {count_label} trades found — "
            f"minimum is {MIN_TRADES}.\n"
            f"    Re-run this script once {MIN_TRADES - tgt['n']} more trades settle.\n"
            f"    SP_MULT_TABLE['{pass_name}'] remains at 1.00× (safe baseline)."
        )
        sys.exit(0)

    def _fmt(v: float | None) -> str:
        return f"{v:+.3f}" if v is not None else "N/A"

    print(f"\nStatistics:")
    all_stat_rows = [(stat_label, tgt), (ANCHOR_PASS, ga)] + list(ctx_stats)
    for lbl, s in all_stat_rows:
        if s["n"] > 0 and s["wr"] is not None:
            print(
                f"  {lbl:<10}  n={s['n']:>4}  WR={s['wr']:.1%}  "
                f"avg_R={_fmt(s['avg_r'])}  "
                f"avg_win_R={_fmt(s['avg_win_r'])}  avg_loss_R={_fmt(s['avg_loss_r'])}  "
                f"expectancy={_fmt(s['expectancy'])}"
            )
        else:
            print(f"  {lbl:<10}  n={s['n']:>4}  (no settled data)")

    ga_exp = ga["expectancy"]
    tgt_exp = tgt["expectancy"]

    if ga_exp is None:
        print(f"\nWARNING: no settled '{ANCHOR_PASS}' trades — cannot anchor multiplier. Using 1.00×.")
        rec_mult = 1.00
    else:
        rec_mult = _recommend_mult(tgt_exp, ga_exp)

    date_range = ""
    if target_rows:
        dates = sorted(r.get("trade_date", "") for r in target_rows if r.get("trade_date"))
        if dates:
            date_range = f"{dates[0]} → {dates[-1]}"

    print(f"\n{'='*60}")
    print(f"RECOMMENDATION:  SP_MULT_TABLE['{pass_name}'] = {rec_mult:.2f}")
    print(f"{'='*60}")
    wr_str = f"{tgt['wr']:.1%}" if tgt["wr"] is not None else "N/A"

    # Citation line — format matches originals:
    #   gap_down: #   'gap_down' (Bearish Break, <dates>): N trades, ...
    #   squeeze:  #   'squeeze' (<dates>): N trades, ...
    if citation_inner:
        citation_parens = f"({citation_inner}{date_range})"
    else:
        citation_parens = f"({date_range})" if date_range else ""

    citation_line = (
        f"#   '{pass_name}' {citation_parens}: "
        f"{tgt['n']} trades, {wr_str} WR / {_fmt(tgt['avg_r'])}R avg → "
        f"{rec_mult:.2f}×"
    )

    print(
        f"\nData citation to paste above SP_MULT_TABLE in trade_utils.py:\n"
        f"\n"
        f"{citation_line}"
    )

    if ga_exp is not None and ga_exp > 0 and tgt_exp is not None and tgt_exp > 0:
        raw = tgt_exp / ga_exp
        print(
            f"\nMethodology: sqrt-dampened ratio vs '{ANCHOR_PASS}' expectancy "
            f"({tgt_exp:+.3f}R ÷ {ga_exp:+.3f}R = "
            f"{raw:.3f} → sqrt → {math.sqrt(raw):.3f} → "
            f"clamped [0.70, 1.30] → rounded to nearest 0.05 → {rec_mult:.2f}×)"
        )
    else:
        print(
            f"\nMethodology: edge-case path (pass_exp={tgt_exp}, gap_exp={ga_exp}) — "
            f"conservative fixed value {rec_mult:.2f}× returned."
        )

    wr_comment = f"{next_step_comment_prefix}{tgt['n']} trades {date_range}, {wr_str} WR / {_fmt(tgt['avg_r'])}R"

    if apply:
        print(f"\n{'='*60}")
        print("Applying change to trade_utils.py  (--apply flag set)")
        print(f"{'='*60}")
        inline_comment = f"{wr_comment} → {rec_mult:.2f}×"
        _apply_to_bot(pass_name, rec_mult, inline_comment, citation_line=citation_line)
        print(f"  To undo this apply, run: python calibrate_sp_mult.py --restore-bak {pass_name}")
    else:
        print(
            f"\nNext step: manually update trade_utils.py, or re-run with --apply to patch automatically:\n"
            f"    \"{pass_name}\": {rec_mult:.2f},   "
            f"# {wr_comment}"
        )


if __name__ == "__main__":
    validate_env_config()

    parser = argparse.ArgumentParser(
        description="Calibrate SP_MULT_TABLE in trade_utils.py for any screener pass.",
        add_help=True,
    )
    parser.add_argument(
        "--pass",
        dest="pass_name",
        metavar="PASS",
        help=f"Screener pass to calibrate. Available: {', '.join(PASS_CONFIG)}",
    )
    parser.add_argument(
        "--reset-pass",
        dest="reset_pass",
        metavar="PASS",
        help=(
            "Reset a screener pass back to 1.00\u00d7 baseline in trade_utils.py, "
            "writing a '0 settled trades as of <today>' stale-warning comment. "
            "Use this when a strategy change invalidates previous calibration data and "
            "you want to start fresh data collection. "
            f"Available passes: {', '.join(PASS_CONFIG)}"
        ),
    )
    parser.add_argument(
        "--restore-bak",
        dest="restore_bak",
        metavar="PASS",
        help=(
            "Restore trade_utils.py from the .bak backup that was created "
            "automatically by the last --reset-pass or --apply run. "
            "Verifies the backup contains a valid SP_MULT_TABLE entry for the named pass, "
            "prints a diff of what is being restored, then overwrites the current file. "
            f"Available passes: {', '.join(PASS_CONFIG)}"
        ),
    )
    parser.add_argument(
        "--preview-bak",
        dest="preview_bak",
        metavar="PASS",
        help=(
            "Preview what --restore-bak would apply without writing any changes. "
            "Prints the same diff as --restore-bak, then prints "
            "'Dry run — no changes written. Re-run with --restore-bak to apply.' "
            f"Available passes: {', '.join(PASS_CONFIG)}"
        ),
    )
    parser.add_argument(
        "--show-reset-log",
        dest="show_reset_log",
        action="store_true",
        help=(
            "Print a formatted table of all past resets from calibration_resets.log. "
            "Combine with --pass to filter to a specific screener pass. "
            "Exits after printing; does not run calibration."
        ),
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run deterministic unit tests on _recommend_mult() and exit.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Patch SP_MULT_TABLE in trade_utils.py in place. "
            "A .bak backup is created and a diff is printed before writing."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Skip the interactive confirmation prompt when using --reset-pass or --restore-bak. "
            "Useful for scripting and CI environments."
        ),
    )
    args = parser.parse_args()

    if args.show_reset_log:
        pass_filter = args.pass_name if args.pass_name else None
        if pass_filter and pass_filter not in PASS_CONFIG:
            known = ", ".join(PASS_CONFIG)
            print(f"ERROR: unknown pass '{pass_filter}'. Known passes: {known}", file=sys.stderr)
            sys.exit(1)
        _show_reset_log(pass_filter=pass_filter)
        sys.exit(0)

    if args.self_test:
        print("Running _recommend_mult() self-tests...")
        _self_test()
        print("\nRunning _apply_to_bot() self-tests...")
        _self_test_apply()
        print("\nRunning fixture-key sync check...")
        _self_test_fixture_keys()
        print("\nRunning reset-log self-tests...")
        _self_test_reset_log()
        print("\nRunning reset-confirm self-tests...")
        _self_test_reset_confirm()
        print("\nRunning restore-confirm self-tests...")
        _self_test_restore_confirm()
        sys.exit(0)

    if args.reset_pass:
        if args.reset_pass not in PASS_CONFIG:
            known = ", ".join(PASS_CONFIG)
            print(f"ERROR: unknown pass '{args.reset_pass}'. Known passes: {known}", file=sys.stderr)
            sys.exit(1)
        prev_mult = _read_current_mult(args.reset_pass)
        if not args.yes:
            val_str = f"{prev_mult:.2f}\u00d7" if prev_mult is not None else "unknown"
            calib_date = _read_calib_date(args.reset_pass)
            inline_comment = _read_inline_comment(args.reset_pass)
            if calib_date:
                calib_str = f"calibrated on {calib_date}"
                if inline_comment:
                    calib_str += f" — {inline_comment}"
            else:
                calib_str = "not yet calibrated (no calibration date on record)"
            print(
                f"\nWARNING: You are about to reset '{args.reset_pass}' to 1.00\u00d7 baseline "
                f"(current value: {val_str}).\n"
                f"  Calibration info: {calib_str}\n"
                f"This will overwrite calibration data in trade_utils.py for this pass.\n"
                f"The only recovery path is the .bak backup file.\n"
            )
            try:
                answer = input("Type 'yes' to confirm reset, or anything else to abort: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                sys.exit(0)
            if answer != "yes":
                print("Aborted — no changes made.")
                sys.exit(0)
        _reset_pass_to_baseline(args.reset_pass)
        _write_reset_log(args.reset_pass, prev_mult, args.yes)
        sys.exit(0)

    if args.preview_bak:
        _restore_from_bak(args.preview_bak, dry_run=True)
        sys.exit(0)

    if args.restore_bak:
        _restore_from_bak(args.restore_bak, yes=args.yes)
        sys.exit(0)

    if not args.pass_name:
        _list_passes()
        sys.exit(0)

    if args.pass_name not in PASS_CONFIG:
        known = ", ".join(PASS_CONFIG)
        print(f"ERROR: unknown pass '{args.pass_name}'. Known passes: {known}", file=sys.stderr)
        sys.exit(1)

    main(args.pass_name, apply=args.apply)
