"""
filter_grid_search.py — exhaustive filter combination grid search.

Fetches ALL rows from backtest_sim_runs (full history, all years), runs a grid
search across 6 filter dimensions, and writes ranked results to JSON.

Usage:
    python filter_grid_search.py                  # full history
    python filter_grid_search.py --start 2026-01-26 --end 2026-04-17  # date window
    python filter_grid_search.py --min-n 50       # custom minimum sample guard
    python filter_grid_search.py --top 30         # show top 30 combos

Output files:
    filter_grid_results.json   — all combinations that meet the minimum N guard
    filter_grid_top20.json     — top combos by Sharpe (N >= min_n)
    filter_grid_summary.json   — metadata: run timestamp, rows fetched, params
"""

import os
import re
import sys
import json
import math
import argparse
import statistics
from datetime import datetime, date

from supabase import create_client

# ── Supabase connection ─────────────────────────────────────────────────────
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

# ── TCS per-structure baselines ────────────────────────────────────────────
TCS_THRESHOLDS_FILE = os.path.join(os.path.dirname(__file__), "tcs_thresholds.json")
with open(TCS_THRESHOLDS_FILE) as _f:
    _RAW_TCS = json.load(_f)

# Map predicted → tcs_floor (case-insensitive substring match)
_PREDICTED_MAP = [
    ("double_dist",    "dbl dist",      _RAW_TCS.get("double_dist", 49)),
    ("ntrl_extreme",   "ntrl extreme",  _RAW_TCS.get("ntrl_extreme", 53)),
    ("trend_bull",     "bullish break", _RAW_TCS.get("trend_bull", 57)),
    ("trend_bear",     "bearish break", _RAW_TCS.get("trend_bull", 57)),
    ("nrml_variation", "nrml var",      _RAW_TCS.get("nrml_variation", 57)),
    ("neutral",        "neutral",       _RAW_TCS.get("neutral", 60)),
    ("normal",         "normal",        _RAW_TCS.get("normal", 64)),
    ("non_trend",      "non_trend",     _RAW_TCS.get("non_trend", 65)),
]

def _tcs_floor(predicted: str) -> int:
    p = (predicted or "").lower()
    for _key, token, floor in _PREDICTED_MAP:
        if token in p:
            return floor
    return 65  # conservative default

# ── Structure groupings ─────────────────────────────────────────────────────
_NEUTRAL_FAMILY = {"neutral", "ntrl extreme", "nrml var", "normal", "non_trend"}
_TREND_FAMILY   = {"bullish break", "bearish break"}
_EXTREME_FAMILY = {"ntrl extreme", "dbl dist"}

def _structure_group(predicted: str) -> str:
    p = (predicted or "").lower()
    if any(t in p for t in _TREND_FAMILY):
        return "trend"
    if any(t in p for t in _EXTREME_FAMILY):
        return "extreme"
    return "neutral"

# ── Grid dimensions ─────────────────────────────────────────────────────────
TCS_OFFSETS     = [0, 5, 10, 15]          # added to per-structure baseline
RVOL_MINS       = [0.0, 1.0, 1.5, 2.0, 2.5]
GAP_MINS        = [0.0, 1.0, 2.0, 3.0, 5.0]   # abs(gap_pct) must be >= this
FOLLOW_MINS     = [-999.0, 0.0, 0.5, 1.0]      # -999 = no filter
STRUCT_FILTERS  = ["all", "neutral", "trend", "extreme", "no_extreme"]
FALSE_BREAK_EX  = [False, True]           # True = exclude false-break rows

LABEL_FOLLOW = {-999.0: "any", 0.0: "≥0%", 0.5: "≥0.5%", 1.0: "≥1%"}
LABEL_STRUCT = {
    "all":        "All structures",
    "neutral":    "Neutral-family only",
    "trend":      "Trend-directional only",
    "extreme":    "Extremes only",
    "no_extreme": "Exclude extremes",
}

# ── Minimum sample guard ────────────────────────────────────────────────────
DEFAULT_MIN_N = 50
DEFAULT_TOP   = 20

# ── Fetch all rows ─────────────────────────────────────────────────────────
FIELDS = (
    "tcs,rvol,gap_pct,follow_thru_pct,predicted,"
    "false_break_up,false_break_down,"
    "tiered_pnl_r,sim_date"
)
PAGE_SIZE = 1000


def fetch_all_rows(sb, start_date: str | None, end_date: str | None) -> list[dict]:
    """Paginate through backtest_sim_runs and return all matching rows."""
    rows = []
    offset = 0
    while True:
        q = (
            sb.table("backtest_sim_runs")
            .select(FIELDS)
            .eq("user_id", USER_ID)
            .order("sim_date")
            .range(offset, offset + PAGE_SIZE - 1)
        )
        if start_date:
            q = q.gte("sim_date", start_date)
        if end_date:
            q = q.lte("sim_date", end_date)
        res = q.execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        pct = min(100, int(len(rows) / 132000 * 100))
        print(f"\r  Fetched {len(rows):,} rows... ({pct}%)", end="", flush=True)
    print(f"\r  Fetched {len(rows):,} rows total.            ")
    return rows


# ── Stats helpers ──────────────────────────────────────────────────────────

def _profit_factor(r_vals: list[float]) -> float:
    gross_win  = sum(v for v in r_vals if v > 0)
    gross_loss = sum(-v for v in r_vals if v < 0)
    if gross_loss == 0:
        return float("inf") if gross_win > 0 else 1.0
    return round(gross_win / gross_loss, 3)


def _sharpe(r_vals: list[float]) -> float:
    """Daily R-unit Sharpe (mean / stdev), annualised by *252."""
    if len(r_vals) < 2:
        return 0.0
    mu = statistics.mean(r_vals)
    sd = statistics.stdev(r_vals)
    if sd == 0:
        return 0.0
    return round(mu / sd * math.sqrt(252), 3)


def _max_drawdown(r_vals: list[float]) -> float:
    """Max cumulative R drawdown (worst peak-to-trough)."""
    cum = 0.0
    peak = 0.0
    worst = 0.0
    for v in r_vals:
        cum += v
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > worst:
            worst = dd
    return round(worst, 3)


def _compute_stats(r_vals: list[float], n_scanned: int, n_trading_days: int) -> dict:
    n = len(r_vals)
    if n == 0:
        return {}
    wins   = [v for v in r_vals if v > 0]
    losses = [v for v in r_vals if v <= 0]
    avg_r  = statistics.mean(r_vals)
    wr     = len(wins) / n
    trades_per_day  = n / max(n_trading_days, 1)
    trades_per_week = trades_per_day * 5
    dollar_per_week = trades_per_week * avg_r * 150.0  # $150 risk per trade
    return {
        "n_trades":           n,
        "n_scanned":          n_scanned,
        "scan_to_trade_pct":  round(n / max(n_scanned, 1) * 100, 1),
        "win_rate":           round(wr * 100, 1),
        "avg_r":              round(avg_r, 4),
        "total_r":            round(sum(r_vals), 3),
        "profit_factor":      _profit_factor(r_vals),
        "sharpe":             _sharpe(r_vals),
        "max_drawdown_r":     _max_drawdown(r_vals),
        "avg_win_r":          round(statistics.mean(wins),  3) if wins   else 0.0,
        "avg_loss_r":         round(statistics.mean(losses), 3) if losses else 0.0,
        "trades_per_week":    round(trades_per_week, 2),
        "proj_weekly_usd":    round(dollar_per_week, 2),
        "low_sample":         n < 75,
    }


# ── Apply one filter combo ─────────────────────────────────────────────────

def _apply_combo(
    all_rows: list[dict],
    tcs_offset: int,
    rvol_min: float,
    gap_min: float,
    follow_min: float,
    struct_filter: str,
    excl_false_break: bool,
) -> tuple[list[float], int]:
    """Return (r_values for traded rows, n_scanned_rows_passing_filter)."""
    r_vals    = []
    n_scanned = 0
    for row in all_rows:
        tcs  = row.get("tcs")
        rvol = row.get("rvol")
        gap  = row.get("gap_pct")
        ft   = row.get("follow_thru_pct")
        pred = row.get("predicted") or ""
        fb_u = row.get("false_break_up")
        fb_d = row.get("false_break_down")
        pnl  = row.get("tiered_pnl_r")

        # --- TCS gate ---
        if tcs is None:
            continue
        floor = _tcs_floor(pred) + tcs_offset
        if tcs < floor:
            continue

        # --- RVOL gate ---
        if rvol_min > 0 and (rvol is None or rvol < rvol_min):
            continue

        # --- Gap gate ---
        if gap_min > 0 and (gap is None or abs(gap) < gap_min):
            continue

        # --- Follow-through gate ---
        if follow_min > -900 and (ft is None or ft < follow_min):
            continue

        # --- Structure gate ---
        grp = _structure_group(pred)
        if struct_filter == "neutral"    and grp != "neutral":
            continue
        if struct_filter == "trend"      and grp != "trend":
            continue
        if struct_filter == "extreme"    and grp != "extreme":
            continue
        if struct_filter == "no_extreme" and grp == "extreme":
            continue

        # --- False-break exclusion ---
        if excl_false_break and (fb_u or fb_d):
            continue

        n_scanned += 1
        if pnl is not None:
            r_vals.append(pnl)

    return r_vals, n_scanned


# ── Main grid search ──────────────────────────────────────────────────────

def run_grid_search(
    all_rows: list[dict],
    min_n: int = DEFAULT_MIN_N,
    top_n: int = DEFAULT_TOP,
) -> tuple[list[dict], list[dict]]:
    """Return (all_results, top_results)."""
    # Count trading days for frequency calculation
    trading_days = len(set(r["sim_date"] for r in all_rows if r.get("sim_date")))

    total_combos = (
        len(TCS_OFFSETS) * len(RVOL_MINS) * len(GAP_MINS)
        * len(FOLLOW_MINS) * len(STRUCT_FILTERS) * len(FALSE_BREAK_EX)
    )
    print(f"  Running {total_combos:,} filter combinations across {trading_days:,} trading days...")

    results = []
    done = 0

    for tcs_off in TCS_OFFSETS:
        for rvol_min in RVOL_MINS:
            for gap_min in GAP_MINS:
                for ft_min in FOLLOW_MINS:
                    for struct in STRUCT_FILTERS:
                        for excl_fb in FALSE_BREAK_EX:
                            r_vals, n_scanned = _apply_combo(
                                all_rows, tcs_off, rvol_min, gap_min,
                                ft_min, struct, excl_fb
                            )
                            done += 1
                            if done % 200 == 0:
                                pct = done / total_combos * 100
                                print(f"\r  Progress: {done:,}/{total_combos:,} ({pct:.0f}%)", end="", flush=True)

                            if len(r_vals) < min_n:
                                continue

                            stats = _compute_stats(r_vals, n_scanned, trading_days)
                            combo = {
                                "tcs_offset":        tcs_off,
                                "tcs_label":         f"+{tcs_off} above baseline" if tcs_off else "Baseline",
                                "rvol_min":          rvol_min,
                                "gap_min":           gap_min,
                                "follow_min":        ft_min,
                                "follow_label":      LABEL_FOLLOW.get(ft_min, f"≥{ft_min}%"),
                                "struct_filter":     struct,
                                "struct_label":      LABEL_STRUCT.get(struct, struct),
                                "excl_false_break":  excl_fb,
                                **stats,
                            }
                            results.append(combo)

    print(f"\r  Done: {done:,} combinations evaluated, {len(results):,} met minimum N={min_n}.   ")

    results.sort(key=lambda x: (x.get("sharpe", 0), x.get("avg_r", 0)), reverse=True)
    top_results = [r for r in results if not r.get("low_sample")][:top_n]

    return results, top_results


# ── Entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Filter combination grid search on backtest_sim_runs")
    parser.add_argument("--start",  default=None,             help="Start date YYYY-MM-DD (default: all history)")
    parser.add_argument("--end",    default=None,             help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--min-n",  type=int, default=DEFAULT_MIN_N,  help=f"Minimum trades for a combo to qualify (default: {DEFAULT_MIN_N})")
    parser.add_argument("--top",    type=int, default=DEFAULT_TOP,    help=f"How many top combos to save (default: {DEFAULT_TOP})")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_KEY must be set.", file=sys.stderr)
        sys.exit(1)

    print(f"\n=== EdgeIQ Filter Grid Search ===")
    print(f"  Date range : {args.start or 'all history'} → {args.end or 'latest'}")
    print(f"  Min trades : {args.min_n}")
    print()

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Step 1: Fetching rows from Supabase...")
    all_rows = fetch_all_rows(sb, args.start, args.end)
    if not all_rows:
        print("No rows returned — check credentials and date range.")
        sys.exit(1)

    traded = [r for r in all_rows if r.get("tiered_pnl_r") is not None]
    print(f"  Total rows: {len(all_rows):,}  |  Traded (has tiered_pnl_r): {len(traded):,}")
    print()

    print("Step 2: Running grid search...")
    all_results, top_results = run_grid_search(all_rows, min_n=args.min_n, top_n=args.top)
    print()

    # ── Write output files ─────────────────────────────────────────────────
    out_all = "filter_grid_results.json"
    out_top = "filter_grid_top20.json"
    out_sum = "filter_grid_summary.json"

    with open(out_all, "w") as f:
        json.dump(all_results, f, indent=2)

    with open(out_top, "w") as f:
        json.dump(top_results, f, indent=2)

    summary = {
        "run_at":       datetime.utcnow().isoformat() + "Z",
        "date_range":   {"start": args.start or "all", "end": args.end or "latest"},
        "total_rows":   len(all_rows),
        "traded_rows":  len(traded),
        "min_n":        args.min_n,
        "top_n":        args.top,
        "combos_tested": (
            len(TCS_OFFSETS) * len(RVOL_MINS) * len(GAP_MINS)
            * len(FOLLOW_MINS) * len(STRUCT_FILTERS) * len(FALSE_BREAK_EX)
        ),
        "combos_qualifying": len(all_results),
        "best_combo": top_results[0] if top_results else None,
    }
    with open(out_sum, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"Step 3: Results written.")
    print(f"  {out_all}  ({len(all_results):,} qualifying combos)")
    print(f"  {out_top}  (top {len(top_results)} combos)")
    print(f"  {out_sum}  (run metadata)")
    print()

    # ── Print top 10 to console ────────────────────────────────────────────
    print("=== TOP 10 FILTER COMBINATIONS (by Sharpe) ===")
    header = f"{'#':<3} {'N':>5} {'WR%':>6} {'AvgR':>6} {'PF':>6} {'Sharpe':>7} {'MaxDD':>6}  Label"
    print(header)
    print("-" * len(header))
    for i, c in enumerate(top_results[:10], 1):
        lbl_parts = []
        if c["tcs_offset"]:
            lbl_parts.append(f"TCS+{c['tcs_offset']}")
        if c["rvol_min"]:
            lbl_parts.append(f"RVOL≥{c['rvol_min']}")
        if c["gap_min"]:
            lbl_parts.append(f"|gap|≥{c['gap_min']}%")
        if c["follow_min"] > -900:
            lbl_parts.append(f"FT{c['follow_label']}")
        if c["struct_filter"] != "all":
            lbl_parts.append(c["struct_label"])
        if c["excl_false_break"]:
            lbl_parts.append("no-FB")
        label = " | ".join(lbl_parts) if lbl_parts else "TCS baseline only"
        print(
            f"{i:<3} {c['n_trades']:>5} {c['win_rate']:>6.1f} "
            f"{c['avg_r']:>6.3f} {c['profit_factor']:>6.2f} "
            f"{c['sharpe']:>7.3f} {c['max_drawdown_r']:>6.2f}  {label}"
        )

    if top_results:
        best = top_results[0]
        print()
        print("=== BEST COMBO DETAIL ===")
        print(f"  Trades       : {best['n_trades']} ({best['trades_per_week']:.1f}/week)")
        print(f"  Win Rate     : {best['win_rate']}%")
        print(f"  Avg R        : {best['avg_r']:.4f}R")
        print(f"  Profit Factor: {best['profit_factor']}")
        print(f"  Sharpe       : {best['sharpe']}")
        print(f"  Max Drawdown : {best['max_drawdown_r']}R")
        print(f"  Proj weekly  : ${best['proj_weekly_usd']:.0f} at $150 risk")
        print(f"  Filters      :")
        print(f"    TCS offset    : +{best['tcs_offset']} above per-structure baseline")
        print(f"    RVOL min      : {best['rvol_min'] or 'none'}")
        print(f"    Gap min       : {best['gap_min'] or 'none'}%")
        print(f"    Follow-thru   : {best['follow_label']}")
        print(f"    Structure     : {best['struct_label']}")
        print(f"    Excl fb       : {best['excl_false_break']}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
