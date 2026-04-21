"""
filter_grid_search.py — Phase 3: exhaustive filter combination grid search.

Vectorized pandas/numpy implementation.  All row-by-row loops have been
replaced with boolean mask operations.  A single mask AND + stats call
on the full 130K-row DataFrame takes ~0.05 ms.

Phase 3 default mode: ~14 million combinations (127 structure subsets ×
216 base-dim combos × 512 binary new-dim combos), completing in ~10-15 min.
Use --full to enable all 3+ option values per new dimension (~600M, ~8 h).

Phase 3 additions vs Phase 1
──────────────────────────────
 • All 127 non-empty subsets of the 7 IB structures (vs. 5 broad families)
 • 9 new filter dimensions: scan type, gap direction, VWAP position,
   screener pass, IB size tier, MFE min, MAE max, RVOL upper cap,
   day-of-week group

Output files
────────────
  filter_grid_results_v3.json        — all combos meeting minimum N
  filter_grid_top100.json            — top 100 by Sharpe (N >= min_n)
  filter_grid_dimension_summary.json — dimension-value frequency in top 20/100
  filter_grid_results.json           — legacy (same as v3, for backward compat)
  filter_grid_top20.json             — legacy top-20 slice
  filter_grid_summary.json           — run metadata

Usage
─────
  python filter_grid_search.py                        # full history, Phase 3
  python filter_grid_search.py --start 2026-01-26 --end 2026-04-17
  python filter_grid_search.py --min-n 75 --top 100
  python filter_grid_search.py --phase 1              # legacy 6-dim mode
  python filter_grid_search.py --full                 # all 3+ dim options (~8h)
"""

from __future__ import annotations

import os, re, sys, json, math, argparse
from datetime import datetime
from itertools import product as iproduct

import numpy as np
import pandas as pd
from supabase import create_client

# ── Supabase connection ──────────────────────────────────────────────────────
raw_url = os.environ.get("SUPABASE_URL", "")
_m = re.search(r"supabase\.com/dashboard/project/([a-z0-9]+)", raw_url)
if _m:
    SUPABASE_URL = f"https://{_m.group(1)}.supabase.co"
elif ".supabase.co" in raw_url:
    _pid = raw_url.split(".supabase.co")[0].split("https://")[-1]
    SUPABASE_URL = f"https://{_pid}.supabase.co"
else:
    SUPABASE_URL = raw_url

SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
USER_ID = "a5e1fcab-8369-42c4-8550-a8a19734510c"

# ── TCS per-structure baselines ──────────────────────────────────────────────
TCS_THRESHOLDS_FILE = os.path.join(os.path.dirname(__file__), "tcs_thresholds.json")
with open(TCS_THRESHOLDS_FILE) as _f:
    _RAW_TCS = json.load(_f)

_PREDICTED_MAP = [
    ("double_dist",    "dbl dist",      _RAW_TCS.get("double_dist",    49)),
    ("ntrl_extreme",   "ntrl extreme",  _RAW_TCS.get("ntrl_extreme",   53)),
    ("trend_bull",     "bullish break", _RAW_TCS.get("trend_bull",     57)),
    ("trend_bear",     "bearish break", _RAW_TCS.get("trend_bull",     57)),
    ("nrml_variation", "nrml var",      _RAW_TCS.get("nrml_variation", 57)),
    ("neutral",        "neutral",       _RAW_TCS.get("neutral",        60)),
    ("normal",         "normal",        _RAW_TCS.get("normal",         64)),
    ("non_trend",      "non_trend",     _RAW_TCS.get("non_trend",      65)),
]

def _tcs_floor(predicted: str) -> int:
    p = (predicted or "").lower()
    for _key, token, floor in _PREDICTED_MAP:
        if token in p:
            return floor
    return 65

# ── 7 IB structure definitions ──────────────────────────────────────────────
# Each is a (label, token_in_predicted) pair.
# Detection: token must appear in lowercase predicted field.
# Order matters for non-overlapping assignment.
STRUCTURES_7 = [
    ("Bullish Break",        "bullish break"),
    ("Bearish Break",        "bearish break"),
    ("Normal Variation",     "nrml var"),
    ("Neutral Extreme",      "ntrl extreme"),
    ("Neutral",              "neutral"),
    ("Double Distribution",  "dbl dist"),
    ("Non-Trend",            "non_trend"),
]

# ── Grid dimensions — Phase 1 (original 6) ─────────────────────────────────
P1_TCS_OFFSETS  = [0, 5, 10, 15]
P1_RVOL_MINS    = [0.0, 1.0, 1.5, 2.0, 2.5, 3.0]
P1_GAP_MINS     = [0.0, 1.0, 2.0, 3.0, 5.0]
P1_FOLLOW_MINS  = [-999.0, 0.0, 0.5, 1.0]
P1_STRUCT_FILTERS = ["all", "neutral", "trend", "extreme", "no_extreme"]
P1_FALSE_BREAK_EX = [False, True]

# ── Grid dimensions — Phase 3 (reduced originals + 9 new) ──────────────────
P3_TCS_OFFSETS  = [0, 5, 10, 15]
P3_RVOL_MINS    = [0.0, 1.5, 2.5]        # reduced from 6 to stay feasible
P3_GAP_MINS     = [0.0, 2.0, 5.0]        # reduced from 5
P3_FOLLOW_MINS  = [-999.0, 0.0, 1.0]     # reduced from 4

# Structure: all 127 non-empty subsets of the 7 structures (generated at runtime)
P3_FALSE_BREAK_EX = [False, True]

# New dim 1 — scan type (2 options in default mode, 3 in --full mode)
SCAN_OPTS = [
    ("any",     None),
    ("morning", ["morning", "trend_morning"]),
]
SCAN_OPTS_FULL = [
    ("any",      None),
    ("morning",  ["morning", "trend_morning"]),
    ("intraday", ["intraday", "trend_intraday"]),
]

# New dim 2 — gap direction (sign of gap_pct)
GAP_DIR_OPTS = [
    ("any", None),
    ("up",  "up"),
]
GAP_DIR_OPTS_FULL = [
    ("any",  None),
    ("up",   "up"),
    ("down", "down"),
]

# New dim 3 — VWAP position (open_price vs vwap_at_ib)
VWAP_POS_OPTS = [
    ("any",   None),
    ("above", "above"),
]
VWAP_POS_OPTS_FULL = [
    ("any",   None),
    ("above", "above"),
    ("below", "below"),
]

# New dim 4 — screener pass gate (2 options only — "fail" rarely useful)
SCREENER_OPTS = [
    ("any",  None),
    ("pass", True),
]

# New dim 5 — IB size tier ( (ib_high-ib_low)/open_price*100 )
IB_SIZE_OPTS = [
    ("any",    None),
    ("narrow", "narrow"),   # < 1.5%
]
IB_SIZE_OPTS_FULL = [
    ("any",    None),
    ("narrow", "narrow"),   # < 1.5%
    ("wide",   "wide"),     # > 3%
]

# New dim 6 — MFE minimum (R units)
MFE_MIN_OPTS = [
    ("any",  None),
    ("0.5r", 0.5),
]
MFE_MIN_OPTS_FULL = [
    ("any",  None),
    ("0.5r", 0.5),
    ("1r",   1.0),
]

# New dim 7 — MAE maximum (R units)
MAE_MAX_OPTS = [
    ("any",  None),
    ("0.5r", 0.5),
]
MAE_MAX_OPTS_FULL = [
    ("any",  None),
    ("0.5r", 0.5),
    ("1r",   1.0),
]

# New dim 8 — RVOL upper cap (excludes blow-off tops)
RVOL_CAP_OPTS = [
    ("none", None),
    ("lt5",  5.0),
]
RVOL_CAP_OPTS_FULL = [
    ("none", None),
    ("lt5",  5.0),
    ("lt10", 10.0),
]

# New dim 9 — day-of-week group (Mon=0 … Fri=4)
DOW_OPTS = [
    ("any",     None),
    ("mon_thu", frozenset([0, 1, 2, 3])),
]
DOW_OPTS_FULL = [
    ("any",      None),
    ("mon_thu",  frozenset([0, 1, 2, 3])),
    ("tue_thu",  frozenset([1, 2, 3])),
    ("no_fri",   frozenset([0, 1, 2, 3, 4]) - frozenset([4])),
    ("mon_only", frozenset([0])),
]

# ── Labels ───────────────────────────────────────────────────────────────────
LABEL_FOLLOW = {-999.0: "any", 0.0: "≥0%", 0.5: "≥0.5%", 1.0: "≥1%"}
LABEL_STRUCT_P1 = {
    "all":        "All structures",
    "neutral":    "Neutral-family only",
    "trend":      "Trend-directional only",
    "extreme":    "Extremes only",
    "no_extreme": "Exclude extremes",
}
_NEUTRAL_FAMILY = {"neutral", "ntrl extreme", "nrml var", "normal", "non_trend"}
_TREND_FAMILY   = {"bullish break", "bearish break"}
_EXTREME_FAMILY = {"ntrl extreme", "dbl dist"}

def _structure_group(p: str) -> str:
    if any(t in p for t in _TREND_FAMILY):   return "trend"
    if any(t in p for t in _EXTREME_FAMILY): return "extreme"
    return "neutral"

# ── Minimum sample guard ─────────────────────────────────────────────────────
DEFAULT_MIN_N = 30
DEFAULT_TOP   = 100

# ── Supabase fields ──────────────────────────────────────────────────────────
FIELDS_P1 = (
    "tcs,rvol,gap_pct,follow_thru_pct,predicted,"
    "false_break_up,false_break_down,"
    "tiered_pnl_r,sim_date"
)
FIELDS_P3 = (
    "tcs,rvol,gap_pct,follow_thru_pct,predicted,"
    "false_break_up,false_break_down,"
    "tiered_pnl_r,sim_date,"
    "scan_type,gap_vs_ib_pct,vwap_at_ib,ib_low,ib_high,open_price,"
    "screener_pass,mfe,mae"
)
PAGE_SIZE = 1000


def fetch_all_rows(sb, start_date, end_date, fields: str) -> list[dict]:
    rows, offset = [], 0
    while True:
        q = (
            sb.table("backtest_sim_runs")
            .select(fields)
            .eq("user_id", USER_ID)
            .order("sim_date")
            .range(offset, offset + PAGE_SIZE - 1)
        )
        if start_date:
            q = q.gte("sim_date", start_date)
        if end_date:
            q = q.lte("sim_date", end_date)
        res   = q.execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        pct = min(100, int(len(rows) / 132000 * 100))
        print(f"\r  Fetched {len(rows):,} rows... ({pct}%)", end="", flush=True)
    print(f"\r  Fetched {len(rows):,} rows total.            ")
    return rows


# ── Stats helpers (numpy-based) ──────────────────────────────────────────────

def _profit_factor(wins_sum: float, losses_sum: float) -> float:
    if losses_sum == 0:
        return float("inf") if wins_sum > 0 else 1.0
    return round(wins_sum / losses_sum, 3)


def _sharpe(mean_r: float, std_r: float) -> float:
    if std_r == 0:
        return 0.0
    return round(mean_r / std_r * math.sqrt(252), 3)


def _max_drawdown(r_arr: np.ndarray) -> float:
    cum  = np.cumsum(r_arr)
    peak = np.maximum.accumulate(cum)
    dd   = peak - cum
    return round(float(dd.max()) if len(dd) else 0.0, 3)


def _compute_stats_np(r_arr: np.ndarray, n_scanned: int, n_trading_days: int) -> dict:
    n = len(r_arr)
    if n == 0:
        return {}
    wins   = r_arr[r_arr > 0]
    losses = r_arr[r_arr <= 0]
    avg_r  = float(r_arr.mean())
    wr     = len(wins) / n
    total_r = float(r_arr.sum())
    wins_sum  = float(wins.sum())
    losses_sum = float(-losses.sum())
    std_r  = float(r_arr.std(ddof=1)) if n > 1 else 0.0
    trades_per_day  = n / max(n_trading_days, 1)
    trades_per_week = trades_per_day * 5
    dollar_per_week = trades_per_week * avg_r * 150.0
    return {
        "n_trades":          n,
        "n_scanned":         n_scanned,
        "scan_to_trade_pct": round(n / max(n_scanned, 1) * 100, 1),
        "win_rate":          round(wr * 100, 1),
        "avg_r":             round(avg_r, 4),
        "total_r":           round(total_r, 3),
        "profit_factor":     _profit_factor(wins_sum, losses_sum),
        "sharpe":            _sharpe(avg_r, std_r),
        "max_drawdown_r":    _max_drawdown(r_arr),
        "avg_win_r":         round(float(wins.mean()),  3) if len(wins)   else 0.0,
        "avg_loss_r":        round(float(losses.mean()), 3) if len(losses) else 0.0,
        "trades_per_week":   round(trades_per_week, 2),
        "proj_weekly_usd":   round(dollar_per_week, 2),
        "low_sample":        n < 75,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — original 6-dimensional search (row-by-row, kept for compatibility)
# ══════════════════════════════════════════════════════════════════════════════

def _apply_combo_p1(
    all_rows, tcs_offset, rvol_min, gap_min, follow_min, struct_filter, excl_fb
):
    r_vals, n_scanned = [], 0
    for row in all_rows:
        tcs  = row.get("tcs")
        rvol = row.get("rvol")
        gap  = row.get("gap_pct")
        ft   = row.get("follow_thru_pct")
        pred = row.get("predicted") or ""
        fb_u = row.get("false_break_up")
        fb_d = row.get("false_break_down")
        pnl  = row.get("tiered_pnl_r")
        if tcs is None:                                             continue
        if tcs < _tcs_floor(pred) + tcs_offset:                    continue
        if rvol_min > 0 and (rvol is None or rvol < rvol_min):     continue
        if gap_min  > 0 and (gap  is None or abs(gap) < gap_min):  continue
        if follow_min > -900 and (ft is None or ft < follow_min):  continue
        grp = _structure_group(pred.lower())
        if struct_filter == "neutral"    and grp != "neutral":      continue
        if struct_filter == "trend"      and grp != "trend":        continue
        if struct_filter == "extreme"    and grp != "extreme":      continue
        if struct_filter == "no_extreme" and grp == "extreme":      continue
        if excl_fb and (fb_u or fb_d):                             continue
        n_scanned += 1
        if pnl is not None:
            r_vals.append(pnl)
    return r_vals, n_scanned


def run_grid_search_p1(all_rows, min_n=DEFAULT_MIN_N, top_n=DEFAULT_TOP):
    trading_days = len(set(r["sim_date"] for r in all_rows if r.get("sim_date")))
    total_combos = (
        len(P1_TCS_OFFSETS) * len(P1_RVOL_MINS) * len(P1_GAP_MINS)
        * len(P1_FOLLOW_MINS) * len(P1_STRUCT_FILTERS) * len(P1_FALSE_BREAK_EX)
    )
    print(f"  Phase 1: {total_combos:,} filter combinations across {trading_days:,} trading days...")

    all_results, done = [], 0
    for tcs_off, rvol_min, gap_min, ft_min, struct, excl_fb in iproduct(
        P1_TCS_OFFSETS, P1_RVOL_MINS, P1_GAP_MINS, P1_FOLLOW_MINS,
        P1_STRUCT_FILTERS, P1_FALSE_BREAK_EX
    ):
        r_vals, n_scanned = _apply_combo_p1(all_rows, tcs_off, rvol_min, gap_min, ft_min, struct, excl_fb)
        done += 1
        if done % 200 == 0:
            print(f"\r  Progress: {done:,}/{total_combos:,} ({done/total_combos*100:.0f}%)", end="", flush=True)
        if not r_vals:
            continue
        qualifies = len(r_vals) >= min_n
        stats = _compute_stats_np(np.array(r_vals, dtype=float), n_scanned, trading_days)
        combo = {
            "phase":          1,
            "tcs_offset":     tcs_off,
            "tcs_label":      f"+{tcs_off} above baseline" if tcs_off else "Baseline",
            "rvol_min":       rvol_min,
            "gap_min":        gap_min,
            "follow_min":     ft_min,
            "follow_label":   LABEL_FOLLOW.get(ft_min, f"≥{ft_min}%"),
            "struct_filter":  struct,
            "struct_label":   LABEL_STRUCT_P1.get(struct, struct),
            "excl_false_break": excl_fb,
            "qualifies":      qualifies,
            **stats,
        }
        all_results.append(combo)

    qualifying = [c for c in all_results if c.get("qualifies")]
    print(f"\r  Done: {done:,} combos, {len(qualifying):,} met N≥{min_n}.   ")
    all_results.sort(key=lambda x: (x.get("qualifies",False), x.get("sharpe",0)), reverse=True)
    qualifying.sort(key=lambda x: x.get("sharpe", 0), reverse=True)
    return all_results, qualifying[:top_n]


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — vectorized 15-dimensional search
# ══════════════════════════════════════════════════════════════════════════════

def _build_structure_subsets():
    """Return list of (label, list_of_token_strings) for all 127 non-empty subsets."""
    n = len(STRUCTURES_7)
    subsets = []
    for bits in range(1, 2**n):
        selected = [(lbl, tok) for i, (lbl, tok) in enumerate(STRUCTURES_7) if bits & (1 << i)]
        label    = " + ".join(lbl for lbl, _ in selected)
        tokens   = [tok for _, tok in selected]
        subsets.append((label, tokens))
    return subsets


def _build_masks_p3(df: pd.DataFrame) -> dict:
    """Pre-compute all boolean numpy arrays needed for Phase 3.
    Returns a dict of mask_name -> np.ndarray[bool].
    """
    masks = {}
    pred_lower = df["predicted"].fillna("").str.lower()

    # ── Structure masks (one per structure) ──────────────────────────────────
    for lbl, tok in STRUCTURES_7:
        key = f"struct_{lbl.replace(' ', '_').lower()}"
        masks[key] = pred_lower.str.contains(tok, regex=False, na=False).values

    # ── TCS floor masks — one per per-structure baseline ─────────────────────
    # Each row has a per-structure TCS floor; precompute tcs_floor as a Series
    tcs_floor_series = pred_lower.map(lambda p: next(
        (floor for _, token, floor in _PREDICTED_MAP if token in p), 65
    ))
    tcs_arr   = df["tcs"].values.astype(float)
    floor_arr = tcs_floor_series.values.astype(float)
    for offset in P3_TCS_OFFSETS:
        masks[f"tcs_off_{offset}"] = tcs_arr >= (floor_arr + offset)

    # ── RVOL min masks ────────────────────────────────────────────────────────
    rvol_arr = pd.to_numeric(df["rvol"], errors="coerce").values
    masks["rvol_valid"] = ~np.isnan(rvol_arr)
    for v in P3_RVOL_MINS:
        masks[f"rvol_min_{v}"] = (rvol_arr >= v) | (v == 0.0)

    # ── Gap min masks ─────────────────────────────────────────────────────────
    gap_arr = pd.to_numeric(df["gap_pct"], errors="coerce").values
    masks["gap_valid"] = ~np.isnan(gap_arr)
    for v in P3_GAP_MINS:
        masks[f"gap_min_{v}"] = (np.abs(gap_arr) >= v) | (v == 0.0)

    # ── Follow-through masks ──────────────────────────────────────────────────
    ft_arr = pd.to_numeric(df["follow_thru_pct"], errors="coerce").values
    for v in P3_FOLLOW_MINS:
        if v <= -900:
            masks[f"follow_{v}"] = np.ones(len(df), dtype=bool)
        else:
            masks[f"follow_{v}"] = ft_arr >= v

    # ── False-break masks ─────────────────────────────────────────────────────
    fb_u = df["false_break_up"].fillna(False).astype(bool).values
    fb_d = df["false_break_down"].fillna(False).astype(bool).values
    masks["fb_any"]     = np.ones(len(df), dtype=bool)
    masks["fb_excl"]    = ~(fb_u | fb_d)

    # ── Valid R mask ──────────────────────────────────────────────────────────
    r_arr = pd.to_numeric(df["tiered_pnl_r"], errors="coerce").values
    masks["r_valid"] = ~np.isnan(r_arr)

    # ── Scan-type masks ───────────────────────────────────────────────────────
    scan_col = df["scan_type"].fillna("").str.lower()
    masks["scan_any"]      = np.ones(len(df), dtype=bool)
    masks["scan_morning"]  = scan_col.isin(["morning", "trend_morning"]).values
    masks["scan_intraday"] = scan_col.isin(["intraday", "trend_intraday"]).values

    # ── Gap direction masks ───────────────────────────────────────────────────
    masks["gap_dir_any"]  = np.ones(len(df), dtype=bool)
    masks["gap_dir_up"]   = (gap_arr > 0) & ~np.isnan(gap_arr)
    masks["gap_dir_down"] = (gap_arr < 0) & ~np.isnan(gap_arr)

    # ── VWAP position masks ───────────────────────────────────────────────────
    vwap_arr  = pd.to_numeric(df["vwap_at_ib"], errors="coerce").values
    open_arr  = pd.to_numeric(df["open_price"], errors="coerce").values
    vwap_ok   = ~np.isnan(vwap_arr) & ~np.isnan(open_arr)
    masks["vwap_any"]   = np.ones(len(df), dtype=bool)
    masks["vwap_above"] = vwap_ok & (open_arr > vwap_arr)
    masks["vwap_below"] = vwap_ok & (open_arr < vwap_arr)

    # ── Screener pass masks ───────────────────────────────────────────────────
    scrn_arr = df["screener_pass"].fillna(False).astype(bool).values
    masks["screener_any"]  = np.ones(len(df), dtype=bool)
    masks["screener_pass"] = scrn_arr

    # ── IB size tier masks ────────────────────────────────────────────────────
    ib_high_arr = pd.to_numeric(df["ib_high"], errors="coerce").values
    ib_low_arr  = pd.to_numeric(df["ib_low"],  errors="coerce").values
    ib_ok = ~np.isnan(ib_high_arr) & ~np.isnan(ib_low_arr) & ~np.isnan(open_arr) & (open_arr > 0)
    ib_pct = np.where(ib_ok, (ib_high_arr - ib_low_arr) / open_arr * 100.0, np.nan)
    masks["ib_size_any"]    = np.ones(len(df), dtype=bool)
    masks["ib_size_narrow"] = ib_ok & (ib_pct < 1.5)
    masks["ib_size_medium"] = ib_ok & (ib_pct >= 1.5) & (ib_pct <= 3.0)
    masks["ib_size_wide"]   = ib_ok & (ib_pct > 3.0)

    # ── MFE minimum masks (in R; skip -9999 sentinel) ────────────────────────
    mfe_arr = pd.to_numeric(df["mfe"], errors="coerce").values
    mfe_ok  = ~np.isnan(mfe_arr) & (mfe_arr > -9000)
    masks["mfe_any"]  = np.ones(len(df), dtype=bool)
    masks["mfe_0.5r"] = mfe_ok & (mfe_arr >= 0.5)
    masks["mfe_1r"]   = mfe_ok & (mfe_arr >= 1.0)

    # ── MAE maximum masks ─────────────────────────────────────────────────────
    mae_arr = pd.to_numeric(df["mae"], errors="coerce").values
    mae_ok  = ~np.isnan(mae_arr) & (mae_arr > -9000)
    masks["mae_any"]  = np.ones(len(df), dtype=bool)
    masks["mae_0.5r"] = mae_ok & (mae_arr <= 0.5)
    masks["mae_1r"]   = mae_ok & (mae_arr <= 1.0)

    # ── RVOL upper cap masks ──────────────────────────────────────────────────
    masks["rvol_cap_none"] = np.ones(len(df), dtype=bool)
    masks["rvol_cap_lt5"]  = (rvol_arr < 5.0)  | np.isnan(rvol_arr)
    masks["rvol_cap_lt10"] = (rvol_arr < 10.0) | np.isnan(rvol_arr)

    # ── Day-of-week masks ─────────────────────────────────────────────────────
    try:
        dow_arr = pd.to_datetime(df["sim_date"], errors="coerce").dt.weekday.values
    except Exception:
        dow_arr = np.full(len(df), -1, dtype=int)
    masks["dow_any"]      = np.ones(len(df), dtype=bool)
    masks["dow_mon_thu"]  = np.isin(dow_arr, [0, 1, 2, 3])
    masks["dow_tue_thu"]  = np.isin(dow_arr, [1, 2, 3])
    masks["dow_no_fri"]   = dow_arr != 4
    masks["dow_mon_only"] = dow_arr == 0

    return masks, r_arr


def _struct_subset_mask(tokens: list[str], struct_masks: dict) -> np.ndarray:
    """OR together all structure masks for the given token list."""
    first = True
    combined = None
    for lbl, tok in STRUCTURES_7:
        if tok in tokens:
            key = f"struct_{lbl.replace(' ', '_').lower()}"
            if first:
                combined = struct_masks[key].copy()
                first = False
            else:
                combined |= struct_masks[key]
    if combined is None:
        return np.zeros(len(next(iter(struct_masks.values()))), dtype=bool)
    return combined


def run_grid_search_p3(
    all_rows: list[dict],
    min_n: int = DEFAULT_MIN_N,
    top_n: int = DEFAULT_TOP,
    full_mode: bool = False,
):
    """Vectorized Phase 3 grid search — all 15 dimensions.

    full_mode=False (default): ~14M combos, ~10-15 min.
    full_mode=True:            ~600M combos, ~8 h.
    """
    print("  Building DataFrame...")
    df = pd.DataFrame(all_rows)

    trading_days = df["sim_date"].dropna().nunique()

    print("  Pre-computing filter masks...")
    masks, r_arr = _build_masks_p3(df)

    struct_subsets = _build_structure_subsets()

    # ── Build iteration lists for 9 new dimensions ────────────────────────────
    # Map label → mask key
    def _mk(lbl, key):
        return (lbl, key)

    scan_list     = ([_mk("any","scan_any"), _mk("morning","scan_morning"), _mk("intraday","scan_intraday")]
                     if full_mode else
                     [_mk("any","scan_any"), _mk("morning","scan_morning")])
    gap_dir_list  = ([_mk("any","gap_dir_any"), _mk("up","gap_dir_up"), _mk("down","gap_dir_down")]
                     if full_mode else
                     [_mk("any","gap_dir_any"), _mk("up","gap_dir_up")])
    vwap_list     = ([_mk("any","vwap_any"), _mk("above","vwap_above"), _mk("below","vwap_below")]
                     if full_mode else
                     [_mk("any","vwap_any"), _mk("above","vwap_above")])
    screener_list = [_mk("any","screener_any"), _mk("pass","screener_pass")]
    ib_size_list  = ([_mk("any","ib_size_any"), _mk("narrow","ib_size_narrow"), _mk("wide","ib_size_wide")]
                     if full_mode else
                     [_mk("any","ib_size_any"), _mk("narrow","ib_size_narrow")])
    mfe_list      = ([_mk("any","mfe_any"), _mk("0.5r","mfe_0.5r"), _mk("1r","mfe_1r")]
                     if full_mode else
                     [_mk("any","mfe_any"), _mk("0.5r","mfe_0.5r")])
    mae_list      = ([_mk("any","mae_any"), _mk("0.5r","mae_0.5r"), _mk("1r","mae_1r")]
                     if full_mode else
                     [_mk("any","mae_any"), _mk("0.5r","mae_0.5r")])
    rvol_cap_list = ([_mk("none","rvol_cap_none"), _mk("lt5","rvol_cap_lt5"), _mk("lt10","rvol_cap_lt10")]
                     if full_mode else
                     [_mk("none","rvol_cap_none"), _mk("lt5","rvol_cap_lt5")])
    dow_list      = ([_mk("any","dow_any"), _mk("mon_thu","dow_mon_thu"), _mk("tue_thu","dow_tue_thu"),
                      _mk("no_fri","dow_no_fri"), _mk("mon_only","dow_mon_only")]
                     if full_mode else
                     [_mk("any","dow_any"), _mk("mon_thu","dow_mon_thu")])

    # Count combos
    n_struct     = len(struct_subsets)
    n_new_dims   = (len(scan_list) * len(gap_dir_list) * len(vwap_list) *
                    len(screener_list) * len(ib_size_list) * len(mfe_list) *
                    len(mae_list) * len(rvol_cap_list) * len(dow_list))
    n_base_dims  = len(P3_TCS_OFFSETS) * len(P3_RVOL_MINS) * len(P3_GAP_MINS) * len(P3_FOLLOW_MINS) * len(P3_FALSE_BREAK_EX)
    total_combos = n_struct * n_new_dims * n_base_dims

    print(f"  Phase 3: {total_combos:,} combinations ({n_struct} struct subsets × "
          f"{n_base_dims} base dims × {n_new_dims} new-dim combos) "
          f"across {trading_days:,} trading days...")

    all_results = []
    done        = 0
    report_every = max(1, total_combos // 200)

    # ── STRUCTURE SUBSET LOOP ─────────────────────────────────────────────────
    for struct_label, struct_tokens in struct_subsets:
        struct_mask = _struct_subset_mask(struct_tokens, masks)
        if not struct_mask.any():
            done += n_new_dims * n_base_dims
            continue

        # ── BASE DIMENSION LOOPS ──────────────────────────────────────────────
        for tcs_off in P3_TCS_OFFSETS:
            tcs_mask = masks[f"tcs_off_{tcs_off}"]
            m_tcs_struct = tcs_mask & struct_mask

            for rvol_min in P3_RVOL_MINS:
                m_rvol = masks[f"rvol_min_{rvol_min}"]

                for gap_min in P3_GAP_MINS:
                    m_gap = masks[f"gap_min_{gap_min}"]

                    for ft_min in P3_FOLLOW_MINS:
                        m_ft = masks[f"follow_{ft_min}"]

                        for excl_fb in P3_FALSE_BREAK_EX:
                            m_fb = masks["fb_excl"] if excl_fb else masks["fb_any"]

                            # Base mask (without new dims)
                            base_mask = m_tcs_struct & m_rvol & m_gap & m_ft & m_fb

                            # ── NEW DIMENSION LOOPS ───────────────────────────
                            for scan_lbl, scan_key in scan_list:
                                m_scan = masks[scan_key]
                                m1 = base_mask & m_scan
                                if not m1.any():
                                    done += (len(gap_dir_list)*len(vwap_list)*len(screener_list)*
                                             len(ib_size_list)*len(mfe_list)*len(mae_list)*
                                             len(rvol_cap_list)*len(dow_list))
                                    continue

                                for gdir_lbl, gdir_key in gap_dir_list:
                                    m_gdir = masks[gdir_key]
                                    m2 = m1 & m_gdir
                                    if not m2.any():
                                        done += (len(vwap_list)*len(screener_list)*len(ib_size_list)*
                                                 len(mfe_list)*len(mae_list)*len(rvol_cap_list)*len(dow_list))
                                        continue

                                    for vwap_lbl, vwap_key in vwap_list:
                                        m_vwap = masks[vwap_key]
                                        m3 = m2 & m_vwap
                                        if not m3.any():
                                            done += (len(screener_list)*len(ib_size_list)*
                                                     len(mfe_list)*len(mae_list)*len(rvol_cap_list)*len(dow_list))
                                            continue

                                        for scrn_lbl, scrn_key in screener_list:
                                            m_scrn = masks[scrn_key]
                                            m4 = m3 & m_scrn
                                            if not m4.any():
                                                done += (len(ib_size_list)*len(mfe_list)*
                                                         len(mae_list)*len(rvol_cap_list)*len(dow_list))
                                                continue

                                            for ibs_lbl, ibs_key in ib_size_list:
                                                m_ibs = masks[ibs_key]
                                                m5 = m4 & m_ibs
                                                if not m5.any():
                                                    done += (len(mfe_list)*len(mae_list)*
                                                             len(rvol_cap_list)*len(dow_list))
                                                    continue

                                                for mfe_lbl, mfe_key in mfe_list:
                                                    m_mfe = masks[mfe_key]
                                                    m6 = m5 & m_mfe
                                                    if not m6.any():
                                                        done += len(mae_list)*len(rvol_cap_list)*len(dow_list)
                                                        continue

                                                    for mae_lbl, mae_key in mae_list:
                                                        m_mae = masks[mae_key]
                                                        m7 = m6 & m_mae
                                                        if not m7.any():
                                                            done += len(rvol_cap_list)*len(dow_list)
                                                            continue

                                                        for rcap_lbl, rcap_key in rvol_cap_list:
                                                            m_rcap = masks[rcap_key]
                                                            m8 = m7 & m_rcap
                                                            if not m8.any():
                                                                done += len(dow_list)
                                                                continue

                                                            for dow_lbl, dow_key in dow_list:
                                                                m_dow = masks[dow_key]
                                                                final_mask = m8 & m_dow & masks["r_valid"]
                                                                done += 1

                                                                r_sub = r_arr[final_mask]
                                                                n_sub = final_mask.sum()

                                                                if done % report_every == 0:
                                                                    pct = done / total_combos * 100
                                                                    print(f"\r  Progress: {done:,}/{total_combos:,} ({pct:.1f}%) | results so far: {len(all_results):,}", end="", flush=True)

                                                                if len(r_sub) == 0:
                                                                    continue

                                                                qualifies = len(r_sub) >= min_n
                                                                stats = _compute_stats_np(r_sub, int(n_sub), trading_days)
                                                                if not stats:
                                                                    continue

                                                                combo = {
                                                                    "phase":           3,
                                                                    "struct_label":    struct_label,
                                                                    "struct_tokens":   struct_tokens,
                                                                    "tcs_offset":      tcs_off,
                                                                    "tcs_label":       f"+{tcs_off} above baseline" if tcs_off else "Baseline",
                                                                    "rvol_min":        rvol_min,
                                                                    "gap_min":         gap_min,
                                                                    "follow_min":      ft_min,
                                                                    "follow_label":    LABEL_FOLLOW.get(ft_min, f"≥{ft_min}%"),
                                                                    "excl_false_break": excl_fb,
                                                                    "scan_type":       scan_lbl,
                                                                    "gap_direction":   gdir_lbl,
                                                                    "vwap_position":   vwap_lbl,
                                                                    "screener":        scrn_lbl,
                                                                    "ib_size":         ibs_lbl,
                                                                    "mfe_min":         mfe_lbl,
                                                                    "mae_max":         mae_lbl,
                                                                    "rvol_cap":        rcap_lbl,
                                                                    "day_of_week":     dow_lbl,
                                                                    "qualifies":       qualifies,
                                                                    **stats,
                                                                }
                                                                all_results.append(combo)

    print(f"\r  Done: {done:,} combinations evaluated, {len(all_results):,} had ≥1 trade.   ")
    qualifying = [c for c in all_results if c.get("qualifies")]
    print(f"  {len(qualifying):,} combos met N≥{min_n}.")

    qualifying.sort(key=lambda x: (x.get("sharpe", 0), x.get("avg_r", 0)), reverse=True)
    all_results.sort(key=lambda x: (x.get("qualifies", False), x.get("sharpe", 0)), reverse=True)

    return all_results, qualifying[:top_n]


# ── Dimension summary ────────────────────────────────────────────────────────

_DIMENSION_KEYS = [
    ("struct_label",    "Structure subset"),
    ("tcs_offset",      "TCS offset"),
    ("rvol_min",        "RVOL min"),
    ("gap_min",         "Gap min %"),
    ("follow_min",      "Follow-through min"),
    ("excl_false_break","Excl false-break"),
    ("scan_type",       "Scan type"),
    ("gap_direction",   "Gap direction"),
    ("vwap_position",   "VWAP position"),
    ("screener",        "Screener pass"),
    ("ib_size",         "IB size tier"),
    ("mfe_min",         "MFE minimum"),
    ("mae_max",         "MAE maximum"),
    ("rvol_cap",        "RVOL upper cap"),
    ("day_of_week",     "Day of week"),
]


def compute_dimension_summary(qualifying: list[dict]) -> dict:
    """For each dimension value, count appearances in top-20 and top-100."""
    top20  = qualifying[:20]
    top100 = qualifying[:100]
    summary = {}
    for key, label in _DIMENSION_KEYS:
        vals_20  = [c.get(key) for c in top20  if key in c]
        vals_100 = [c.get(key) for c in top100 if key in c]
        if not vals_20 and not vals_100:
            continue
        all_vals = set(str(v) for v in vals_20 + vals_100)
        breakdown = {}
        for v in sorted(all_vals):
            breakdown[v] = {
                "in_top_20":  sum(1 for x in vals_20  if str(x) == v),
                "in_top_100": sum(1 for x in vals_100 if str(x) == v),
            }
        summary[key] = {"label": label, "values": breakdown}
    return summary


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="EdgeIQ Filter Grid Search")
    parser.add_argument("--start",  default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",    default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--min-n",  type=int, default=DEFAULT_MIN_N)
    parser.add_argument("--top",    type=int, default=DEFAULT_TOP)
    parser.add_argument("--phase",  type=int, default=3, choices=[1, 3],
                        help="1=original 6-dim search, 3=exhaustive 15-dim (default)")
    parser.add_argument("--full",   action="store_true",
                        help="Enable all 3+ options per new dimension (~600M combos, ~8h runtime)")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_KEY must be set.", file=sys.stderr)
        sys.exit(1)

    print(f"\n=== EdgeIQ Filter Grid Search — Phase {args.phase} ===")
    print(f"  Date range : {args.start or 'all history'} → {args.end or 'latest'}")
    print(f"  Min trades : {args.min_n}")
    print(f"  Top N      : {args.top}")
    print()

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Step 1: Fetching rows from Supabase...")
    fields = FIELDS_P3 if args.phase == 3 else FIELDS_P1
    all_rows = fetch_all_rows(sb, args.start, args.end, fields)
    if not all_rows:
        print("No rows returned — check credentials and date range.")
        sys.exit(1)

    traded = [r for r in all_rows if r.get("tiered_pnl_r") is not None]
    print(f"  Total rows: {len(all_rows):,}  |  With P&L: {len(traded):,}")
    print()

    print(f"Step 2: Running Phase {args.phase} grid search...")
    if args.phase == 3 and args.full:
        print("  ⚠ Full mode enabled — all 3+ options per new dimension (~600M combos). This will take ~8h.")
    t_start = datetime.utcnow()
    if args.phase == 3:
        all_results, top_results = run_grid_search_p3(
            all_rows, min_n=args.min_n, top_n=args.top, full_mode=getattr(args, "full", False)
        )
    else:
        all_results, top_results = run_grid_search_p1(all_rows, min_n=args.min_n, top_n=args.top)
    elapsed = (datetime.utcnow() - t_start).total_seconds()
    print(f"  Grid search complete in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print()

    qualifying = [c for c in all_results if c.get("qualifies")]

    # ── Write output files ────────────────────────────────────────────────────
    print("Step 3: Writing result files...")

    # Phase 3 output
    if args.phase == 3:
        with open("filter_grid_results_v3.json", "w") as f:
            json.dump(qualifying, f, indent=2)
        with open("filter_grid_top100.json", "w") as f:
            json.dump(top_results[:100], f, indent=2)

        dim_summary = compute_dimension_summary(qualifying)
        with open("filter_grid_dimension_summary.json", "w") as f:
            json.dump(dim_summary, f, indent=2)

        print(f"  filter_grid_results_v3.json      ({len(qualifying):,} qualifying combos)")
        print(f"  filter_grid_top100.json           (top {min(len(top_results),100)} combos)")
        print(f"  filter_grid_dimension_summary.json")

    # Legacy files (always written for Phase 1; written as Phase-3 slice for Phase 3)
    top20 = top_results[:20]
    with open("filter_grid_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    with open("filter_grid_top20.json", "w") as f:
        json.dump(top20, f, indent=2)

    n_struct_opts = len(_build_structure_subsets()) if args.phase == 3 else len(P1_STRUCT_FILTERS)
    if args.phase == 3:
        _sl  = _DEFAULT_SCAN_LIST
        _gdl = _DEFAULT_GAP_DIR_LIST
        _vl  = _DEFAULT_VWAP_LIST
        _scl = _DEFAULT_SCREENER_LIST
        _ibl = _DEFAULT_IB_SIZE_LIST
        _mfl = _DEFAULT_MFE_LIST
        _mal = _DEFAULT_MAE_LIST
        _rcl = _DEFAULT_RVOL_CAP_LIST
        _dl  = _DEFAULT_DOW_LIST
        n_combos = (
            len(P3_TCS_OFFSETS) * len(P3_RVOL_MINS) * len(P3_GAP_MINS) * len(P3_FOLLOW_MINS)
            * len(P3_FALSE_BREAK_EX) * n_struct_opts
            * len(_sl) * len(_gdl) * len(_vl)
            * len(_scl) * len(_ibl) * len(_mfl)
            * len(_mal) * len(_rcl) * len(_dl)
        )
    else:
        n_combos = (
            len(P1_TCS_OFFSETS) * len(P1_RVOL_MINS) * len(P1_GAP_MINS)
            * len(P1_FOLLOW_MINS) * len(P1_STRUCT_FILTERS) * len(P1_FALSE_BREAK_EX)
        )

    summary = {
        "run_at":                 datetime.utcnow().isoformat() + "Z",
        "phase":                  args.phase,
        "date_range":             {"start": args.start or "all", "end": args.end or "latest"},
        "total_rows":             len(all_rows),
        "traded_rows":            len(traded),
        "min_n":                  args.min_n,
        "top_n":                  args.top,
        "combos_tested":          n_combos,
        "combos_with_any_trade":  len(all_results),
        "combos_qualifying":      len(qualifying),
        "elapsed_seconds":        round(elapsed, 1),
        "best_combo":             top_results[0] if top_results else None,
    }
    with open("filter_grid_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  filter_grid_results.json  ({len(all_results):,} combos)")
    print(f"  filter_grid_top20.json    (top 20 slice)")
    print(f"  filter_grid_summary.json")
    print()

    # ── Console table ─────────────────────────────────────────────────────────
    print(f"=== TOP 10 COMBOS (by Sharpe) — Phase {args.phase} ===")
    hdr = f"{'#':<3} {'N':>5} {'WR%':>6} {'AvgR':>6} {'PF':>7} {'Sharpe':>7} {'MaxDD':>6}  Label"
    print(hdr); print("-" * len(hdr))
    for i, c in enumerate(top_results[:10], 1):
        if args.phase == 3:
            lbl = f"{c['struct_label']} | TCS+{c['tcs_offset']} | {c['scan_type']} | gap-{c['gap_direction']}"
        else:
            parts = []
            if c.get("tcs_offset"):     parts.append(f"TCS+{c['tcs_offset']}")
            if c.get("rvol_min"):       parts.append(f"RVOL≥{c['rvol_min']}")
            if c.get("gap_min"):        parts.append(f"|gap|≥{c['gap_min']}%")
            if (c.get("follow_min") or -999) > -900: parts.append(f"FT{c.get('follow_label','?')}")
            if c.get("struct_filter","all") != "all": parts.append(c.get("struct_label",""))
            if c.get("excl_false_break"): parts.append("no-FB")
            lbl = " | ".join(parts) if parts else "TCS baseline only"
        pf = c.get("profit_factor", 0)
        pf_str = "   ∞" if pf == float("inf") else f"{pf:7.2f}"
        print(f"{i:<3} {c['n_trades']:>5} {c['win_rate']:>6.1f} "
              f"{c['avg_r']:>6.3f} {pf_str} "
              f"{c['sharpe']:>7.3f} {c['max_drawdown_r']:>6.2f}  {lbl}")

    if top_results:
        best = top_results[0]
        print()
        print("=== BEST COMBO DETAIL ===")
        print(f"  Trades       : {best['n_trades']} ({best['trades_per_week']:.1f}/week)")
        print(f"  Win Rate     : {best['win_rate']}%")
        print(f"  Avg R        : {best['avg_r']:.4f}R")
        pf = best.get("profit_factor", 0)
        print(f"  Profit Factor: {'∞' if pf == float('inf') else f'{pf:.3f}'}")
        print(f"  Sharpe       : {best['sharpe']}")
        print(f"  Max Drawdown : {best['max_drawdown_r']}R")
        print(f"  Proj weekly  : ${best['proj_weekly_usd']:.0f} at $150 risk")
        if args.phase == 3:
            print(f"  Structure    : {best.get('struct_label','?')}")
            print(f"  Scan type    : {best.get('scan_type','?')}")
            print(f"  Gap direction: {best.get('gap_direction','?')}")
            print(f"  VWAP pos     : {best.get('vwap_position','?')}")
            print(f"  Screener     : {best.get('screener','?')}")
            print(f"  IB size      : {best.get('ib_size','?')}")
            print(f"  MFE min      : {best.get('mfe_min','?')}")
            print(f"  MAE max      : {best.get('mae_max','?')}")
            print(f"  RVOL cap     : {best.get('rvol_cap','?')}")
            print(f"  Day of week  : {best.get('day_of_week','?')}")

    print()
    print("Done.")


# Default (binary) dimension lists — used for combo count in summary
_DEFAULT_SCAN_LIST     = [("any", "scan_any"), ("morning", "scan_morning")]
_DEFAULT_GAP_DIR_LIST  = [("any", "gap_dir_any"), ("up", "gap_dir_up")]
_DEFAULT_VWAP_LIST     = [("any", "vwap_any"), ("above", "vwap_above")]
_DEFAULT_SCREENER_LIST = [("any", "screener_any"), ("pass", "screener_pass")]
_DEFAULT_IB_SIZE_LIST  = [("any", "ib_size_any"), ("narrow", "ib_size_narrow")]
_DEFAULT_MFE_LIST      = [("any", "mfe_any"), ("0.5r", "mfe_0.5r")]
_DEFAULT_MAE_LIST      = [("any", "mae_any"), ("0.5r", "mae_0.5r")]
_DEFAULT_RVOL_CAP_LIST = [("none", "rvol_cap_none"), ("lt5", "rvol_cap_lt5")]
_DEFAULT_DOW_LIST      = [("any", "dow_any"), ("mon_thu", "dow_mon_thu")]

if __name__ == "__main__":
    main()
