"""
filter_correlation_report.py — Feature correlation & interaction analysis.

Fetches all backtest_sim_runs rows with tiered_pnl_r populated, then computes:
  (a) Pearson + Spearman correlation of each feature with tiered_pnl_r
  (b) Mutual information score of each feature vs win/loss (binary)
  (c) Pairwise interaction gain for the top-10 features by MI score
  (d) Regime breakdown: per-regime (bull/bear/neutral) top correlations and MI

Regime detection uses a rolling 20-trading-day win rate:
  bull    — rolling win rate ≥ 60 %
  bear    — rolling win rate ≤ 40 %
  neutral — 40 – 60 % (choppy / sideways)

Output: filter_correlation_report.json

Usage:
    python filter_correlation_report.py                        # full history, all regimes
    python filter_correlation_report.py --regime bull          # bull-regime rows only
    python filter_correlation_report.py --start 2025-01-01 --end 2025-12-31
    python filter_correlation_report.py --top-pairs 20
"""

import os
import re
import sys
import json
import math
import argparse
import logging
import datetime as _dt
from collections import defaultdict
from datetime import datetime

import numpy as np
from scipy import stats
from sklearn.feature_selection import mutual_info_classif
from sklearn.preprocessing import KBinsDiscretizer

from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Supabase connection ────────────────────────────────────────────────────
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

# ── Feature definitions ────────────────────────────────────────────────────
NUMERIC_FEATURES = [
    "tcs",
    "gap_pct",
    "gap_vs_ib_pct",
    "follow_thru_pct",
    "rvol",
    "ib_range_pct",
    "open_vs_poc_pct",
    "ib_midpoint_vs_poc_pct",
    "stop_dist_pct",
    "entry_hour",
    "day_of_week",
    "close_vs_vwap_pct",
    "volume_ib",
    "aft_volume",
    "mfe",
    "mae",
    "screener_pass",
    "false_break_up",
    "false_break_down",
]

CATEGORICAL_FEATURES = ["predicted", "sim_outcome", "scan_type"]

TARGET_CONTINUOUS = "tiered_pnl_r"
TARGET_BINARY     = "win"          # derived: tiered_pnl_r > 0

FIELDS = ",".join(
    ["tiered_pnl_r", "sim_date"] + NUMERIC_FEATURES + CATEGORICAL_FEATURES
)

PAGE_SIZE = 1000

# ── Regime thresholds ──────────────────────────────────────────────────────
REGIME_WINDOW_DAYS  = 20   # rolling window size (trading days by date count)
BULL_WIN_THRESHOLD  = 0.60 # rolling win rate ≥ this → bull
BEAR_WIN_THRESHOLD  = 0.40 # rolling win rate ≤ this → bear

# ── Descriptions ──────────────────────────────────────────────────────────
FEATURE_DESC = {
    "tcs":                    "Trade Confidence Score",
    "gap_pct":                "Gap % from prior close",
    "gap_vs_ib_pct":          "Gap as % of IB range",
    "follow_thru_pct":        "IB follow-through %",
    "rvol":                   "Relative volume (vs 20d avg)",
    "ib_range_pct":           "IB range as % of open",
    "open_vs_poc_pct":        "Open vs POC offset %",
    "ib_midpoint_vs_poc_pct": "IB midpoint vs POC %",
    "stop_dist_pct":          "Stop distance %",
    "entry_hour":             "Entry hour (ET, 24h)",
    "day_of_week":            "Day of week (0=Mon)",
    "close_vs_vwap_pct":      "Close vs VWAP %",
    "volume_ib":              "IB volume",
    "aft_volume":             "After-hours volume",
    "mfe":                    "Max Favourable Excursion (R)",
    "mae":                    "Max Adverse Excursion (R)",
    "screener_pass":          "Screener pass flag (0/1)",
    "false_break_up":         "False-break-up flag (0/1)",
    "false_break_down":       "False-break-down flag (0/1)",
    "predicted":              "Structure prediction (categorical)",
    "sim_outcome":            "Simulated outcome (categorical)",
    "scan_type":              "Scan type (categorical)",
}


# ── Data fetching ─────────────────────────────────────────────────────────

def fetch_rows(sb, start_date, end_date):
    rows = []
    offset = 0
    log.info("Fetching backtest_sim_runs rows with tiered_pnl_r populated…")
    while True:
        q = (
            sb.table("backtest_sim_runs")
            .select(FIELDS)
            .eq("user_id", USER_ID)
            .not_.is_(TARGET_CONTINUOUS, "null")
            .order("sim_date")
            .range(offset, offset + PAGE_SIZE - 1)
        )
        if start_date:
            q = q.gte("sim_date", start_date)
        if end_date:
            q = q.lte("sim_date", end_date)
        batch = q.execute().data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        if offset % 10000 == 0:
            log.info(f"  …{offset:,} rows fetched")
    log.info(f"Total rows fetched: {len(rows):,}")
    return rows


# ── Regime detection ──────────────────────────────────────────────────────

def _parse_date(s):
    try:
        return _dt.datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return _dt.date.min


def assign_regimes(rows):
    """Label every row with a market regime (bull / bear / neutral).

    Regime is determined by the rolling REGIME_WINDOW_DAYS-day win rate:
      bull    ≥ BULL_WIN_THRESHOLD
      bear    ≤ BEAR_WIN_THRESHOLD
      neutral otherwise

    Returns a parallel list of regime strings.
    """
    dated = sorted(rows, key=lambda r: _parse_date(r.get("sim_date")))

    # Map date → list of binary win values
    date_wins = defaultdict(list)
    for r in dated:
        d = _parse_date(r.get("sim_date"))
        pnl = r.get(TARGET_CONTINUOUS)
        if pnl is not None:
            try:
                date_wins[d].append(1 if float(pnl) > 0 else 0)
            except (TypeError, ValueError):
                pass

    sorted_dates = sorted(date_wins.keys())

    # Build date → regime label using rolling window
    date_to_regime = {}
    for i, d in enumerate(sorted_dates):
        start_i = max(0, i - REGIME_WINDOW_DAYS + 1)
        window_wins = []
        for wd in sorted_dates[start_i : i + 1]:
            window_wins.extend(date_wins[wd])
        if len(window_wins) < 10:
            date_to_regime[d] = "neutral"
            continue
        wr = sum(window_wins) / len(window_wins)
        if wr >= BULL_WIN_THRESHOLD:
            date_to_regime[d] = "bull"
        elif wr <= BEAR_WIN_THRESHOLD:
            date_to_regime[d] = "bear"
        else:
            date_to_regime[d] = "neutral"

    # Attach regime to each original row (preserve original ordering)
    regimes = []
    for r in rows:
        d = _parse_date(r.get("sim_date"))
        regimes.append(date_to_regime.get(d, "neutral"))

    return regimes


# ── Feature extraction ────────────────────────────────────────────────────

def _safe_float(v):
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def extract_arrays(rows):
    """Return (X_numeric, feature_names, y_cont, y_bin) as numpy arrays.

    Rows with None for tiered_pnl_r are excluded. For each numeric feature,
    missing values are imputed with the column median.
    """
    y_cont = np.array([r[TARGET_CONTINUOUS] for r in rows], dtype=float)
    y_bin  = (y_cont > 0).astype(int)

    # Encode categoricals: structure group, sim_outcome bucket
    def _struct_code(pred):
        p = (pred or "").lower()
        if any(t in p for t in ("bullish break", "bearish break")):
            return 1
        if any(t in p for t in ("ntrl extreme", "dbl dist")):
            return -1
        return 0

    _SIM_MAP = {"Win": 1, "Loss": -1, "Scratch": 0}
    def _sim_code(s):
        return _SIM_MAP.get(str(s).strip(), 0)

    feature_names_num = list(NUMERIC_FEATURES)
    feature_names_cat = ["struct_code", "sim_code"]
    all_feature_names = feature_names_num + feature_names_cat

    raw_matrix = []
    for r in rows:
        row_vals = []
        for f in feature_names_num:
            row_vals.append(_safe_float(r.get(f)))
        row_vals.append(float(_struct_code(r.get("predicted"))))
        row_vals.append(float(_sim_code(r.get("sim_outcome"))))
        raw_matrix.append(row_vals)

    X_raw = np.array(raw_matrix, dtype=object)
    n_rows, n_cols = X_raw.shape
    X = np.zeros((n_rows, n_cols), dtype=float)
    for col in range(n_cols):
        col_data = X_raw[:, col]
        valid_vals = np.array([float(v) for v in col_data if v is not None], dtype=float)
        median = float(np.median(valid_vals)) if len(valid_vals) > 0 else 0.0
        for row in range(n_rows):
            X[row, col] = float(col_data[row]) if col_data[row] is not None else median

    return X, all_feature_names, y_cont, y_bin


# ── Correlation computation ───────────────────────────────────────────────

def compute_correlations(X, feature_names, y_cont, y_bin):
    """Compute Pearson, Spearman, and point-biserial correlations per feature."""
    results = []
    for i, name in enumerate(feature_names):
        col = X[:, i]
        valid = np.isfinite(col)
        if valid.sum() < 30:
            continue
        xv, yv = col[valid], y_cont[valid]

        pearson_r, pearson_p = stats.pearsonr(xv, yv)
        spearman_r, spearman_p = stats.spearmanr(xv, yv)

        xv_bin, yv_bin = col[valid], y_bin[valid]
        pb_r, pb_p = stats.pointbiserialr(xv_bin, yv_bin)

        results.append({
            "feature":         name,
            "description":     FEATURE_DESC.get(name, name),
            "pearson_r":       round(float(pearson_r), 4),
            "pearson_p":       round(float(pearson_p), 6),
            "spearman_r":      round(float(spearman_r), 4),
            "spearman_p":      round(float(spearman_p), 6),
            "pb_r":            round(float(pb_r), 4),
            "pb_p":            round(float(pb_p), 6),
            "abs_spearman":    abs(round(float(spearman_r), 4)),
        })

    results.sort(key=lambda x: x["abs_spearman"], reverse=True)
    return results


# ── Mutual information ────────────────────────────────────────────────────

def compute_mi(X, feature_names, y_bin):
    """Mutual information of each feature vs win/loss (binary)."""
    mi_scores = mutual_info_classif(X, y_bin, random_state=42)
    mi_results = []
    for i, name in enumerate(feature_names):
        mi_results.append({
            "feature":     name,
            "description": FEATURE_DESC.get(name, name),
            "mi_score":    round(float(mi_scores[i]), 6),
        })
    mi_results.sort(key=lambda x: x["mi_score"], reverse=True)
    return mi_results


# ── Pairwise interaction analysis ─────────────────────────────────────────

def compute_pairwise_interactions(X, feature_names, y_bin, top_n=10, max_pairs=45):
    """Interaction gain for top-N feature pairs.

    Interaction gain = MI(pair_binned, y) - max(MI(a, y), MI(b, y))
    A positive gain means the pair together predicts the outcome better
    than either feature alone.
    """
    log.info(f"Computing pairwise interactions for top-{top_n} features…")
    mi_individual = mutual_info_classif(X, y_bin, random_state=42)

    top_indices = np.argsort(mi_individual)[::-1][:top_n]

    binner = KBinsDiscretizer(n_bins=5, encode="ordinal", strategy="quantile")

    results = []
    pairs_done = 0
    for ia in range(len(top_indices)):
        for ib in range(ia + 1, len(top_indices)):
            if pairs_done >= max_pairs:
                break
            idx_a, idx_b = top_indices[ia], top_indices[ib]
            col_a = X[:, idx_a].reshape(-1, 1)
            col_b = X[:, idx_b].reshape(-1, 1)
            try:
                bin_a = binner.fit_transform(col_a).astype(int).flatten()
                bin_b = binner.fit_transform(col_b).astype(int).flatten()
                pair_code = bin_a * 5 + bin_b
                pair_mi = mutual_info_classif(
                    pair_code.reshape(-1, 1), y_bin,
                    discrete_features=True, random_state=42
                )[0]
                interaction_gain = float(pair_mi) - max(
                    float(mi_individual[idx_a]),
                    float(mi_individual[idx_b])
                )
                results.append({
                    "feature_a":        feature_names[idx_a],
                    "feature_b":        feature_names[idx_b],
                    "desc_a":           FEATURE_DESC.get(feature_names[idx_a], feature_names[idx_a]),
                    "desc_b":           FEATURE_DESC.get(feature_names[idx_b], feature_names[idx_b]),
                    "mi_a":             round(float(mi_individual[idx_a]), 6),
                    "mi_b":             round(float(mi_individual[idx_b]), 6),
                    "mi_pair":          round(float(pair_mi), 6),
                    "interaction_gain": round(interaction_gain, 6),
                })
            except Exception as exc:
                log.debug(f"Pair ({feature_names[idx_a]}, {feature_names[idx_b]}): {exc}")
            pairs_done += 1

    results.sort(key=lambda x: x["interaction_gain"], reverse=True)
    return results


# ── Summary statistics ────────────────────────────────────────────────────

def compute_win_loss_summary(y_cont, y_bin):
    wins   = y_cont[y_bin == 1]
    losses = y_cont[y_bin == 0]
    return {
        "n_total":    int(len(y_cont)),
        "n_wins":     int(len(wins)),
        "n_losses":   int(len(losses)),
        "win_rate":   round(float(np.mean(y_bin)) * 100, 2),
        "avg_win_r":  round(float(np.mean(wins)),   4) if len(wins)   > 0 else 0.0,
        "avg_loss_r": round(float(np.mean(losses)),  4) if len(losses) > 0 else 0.0,
        "avg_r":      round(float(np.mean(y_cont)),  4),
        "total_r":    round(float(np.sum(y_cont)),   3),
    }


# ── Regime breakdown ──────────────────────────────────────────────────────

def compute_regime_breakdown(rows, regimes, top_n=10):
    """Return per-regime top correlations and MI scores.

    Args:
        rows:    full row list
        regimes: parallel list of regime labels (same length as rows)
        top_n:   how many top features to include per regime

    Returns:
        dict with keys 'bull', 'bear', 'neutral', each containing
        n_rows, win_loss_summary, top_correlations, top_mi.
    """
    log.info("Computing regime breakdown…")
    breakdown = {}
    for label in ("bull", "bear", "neutral"):
        subset = [r for r, reg in zip(rows, regimes) if reg == label]
        n = len(subset)
        log.info(f"  Regime '{label}': {n:,} rows")
        if n < 50:
            breakdown[label] = {
                "n_rows": n,
                "win_loss_summary": None,
                "top_correlations": [],
                "top_mi": [],
                "note": f"Too few rows ({n}) for reliable analysis — need ≥ 50.",
            }
            continue

        X, feature_names, y_cont, y_bin = extract_arrays(subset)
        summary = compute_win_loss_summary(y_cont, y_bin)
        corr    = compute_correlations(X, feature_names, y_cont, y_bin)
        mi      = compute_mi(X, feature_names, y_bin)

        breakdown[label] = {
            "n_rows":           n,
            "win_loss_summary": summary,
            "top_correlations": corr[:top_n],
            "top_mi":           mi[:top_n],
        }

    return breakdown


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Feature correlation and interaction analysis for backtest_sim_runs"
    )
    p.add_argument("--start",     metavar="YYYY-MM-DD", default=None)
    p.add_argument("--end",       metavar="YYYY-MM-DD", default=None)
    p.add_argument("--regime",    choices=["bull", "bear", "neutral"], default=None,
                   help="Filter to a single regime before computing correlations. "
                        "When omitted, all regimes are included and a regime_breakdown "
                        "section is added to the output.")
    p.add_argument("--top-pairs", type=int, default=10,
                   help="Top-N features to use in pairwise interaction analysis")
    p.add_argument("--max-pairs", type=int, default=45,
                   help="Max pairs to evaluate (default 45 = C(10,2))")
    p.add_argument("--out",       default="filter_correlation_report.json")
    args = p.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL and SUPABASE_KEY must be set.")
        sys.exit(1)

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    all_rows = fetch_rows(sb, args.start, args.end)
    if len(all_rows) < 50:
        log.error(f"Too few rows ({len(all_rows)}) to compute correlations.")
        sys.exit(1)

    # Assign regimes to the full dataset (needed for breakdown even when filtering)
    log.info("Assigning market regimes…")
    all_regimes = assign_regimes(all_rows)
    regime_counts = {lbl: all_regimes.count(lbl) for lbl in ("bull", "bear", "neutral")}
    log.info(f"  Regime distribution: {regime_counts}")

    # Compute regime breakdown from the full dataset
    regime_breakdown = compute_regime_breakdown(all_rows, all_regimes)

    # If a specific regime is requested, filter the working set
    if args.regime:
        log.info(f"Filtering to '{args.regime}' regime only…")
        rows = [r for r, reg in zip(all_rows, all_regimes) if reg == args.regime]
        if len(rows) < 50:
            log.error(
                f"Too few '{args.regime}' rows ({len(rows)}) after regime filter. "
                "Run without --regime for a full-history report."
            )
            sys.exit(1)
        log.info(f"  Using {len(rows):,} '{args.regime}' rows for main analysis.")
    else:
        rows = all_rows

    log.info("Extracting feature arrays…")
    X, feature_names, y_cont, y_bin = extract_arrays(rows)

    log.info("Computing Pearson/Spearman/point-biserial correlations…")
    correlations = compute_correlations(X, feature_names, y_cont, y_bin)

    log.info("Computing mutual information scores…")
    mi_scores = compute_mi(X, feature_names, y_bin)

    log.info("Computing pairwise interaction gains…")
    pairs = compute_pairwise_interactions(
        X, feature_names, y_bin,
        top_n=args.top_pairs,
        max_pairs=args.max_pairs,
    )

    summary = compute_win_loss_summary(y_cont, y_bin)
    date_range = {
        "start": args.start or "all",
        "end":   args.end   or "latest",
    }

    report = {
        "run_at":              datetime.utcnow().isoformat() + "Z",
        "date_range":          date_range,
        "active_regime":       args.regime or "all",
        "regime_counts":       regime_counts,
        "n_rows":              len(rows),
        "n_features":          len(feature_names),
        "win_loss_summary":    summary,
        "correlations":        correlations,
        "mi_scores":           mi_scores,
        "pairwise_interactions": pairs,
        "regime_breakdown":    regime_breakdown,
    }

    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)

    log.info(f"Report written to {args.out}")
    log.info(f"  Rows: {len(rows):,} | Win rate: {summary['win_rate']:.1f}%")
    log.info(f"  Regime filter: {args.regime or 'none (all)'}")
    log.info(f"  Top correlation (Spearman): {correlations[0]['feature']} = {correlations[0]['spearman_r']}")
    log.info(f"  Top MI feature: {mi_scores[0]['feature']} = {mi_scores[0]['mi_score']:.4f}")
    if pairs:
        log.info(
            f"  Top interaction pair: ({pairs[0]['feature_a']}, {pairs[0]['feature_b']}) "
            f"gain={pairs[0]['interaction_gain']:.4f}"
        )
    log.info(f"  Regime breakdown: bull={regime_counts['bull']:,} | "
             f"bear={regime_counts['bear']:,} | neutral={regime_counts['neutral']:,}")


if __name__ == "__main__":
    main()
