"""apply_best_grid_combo.py — Parse Phase 3 grid search results and auto-apply best combo
to filter_config.json while preserving ALL existing keys not in the grid-derived set.

Usage:
    python3 apply_best_grid_combo.py                # uses default paths
    python3 apply_best_grid_combo.py --dry-run      # show what would be written, don't write
    python3 apply_best_grid_combo.py --min-n 30     # require at least N trades (default: 30)
    python3 apply_best_grid_combo.py --min-sharpe 2 # require Sharpe >= 2.0 (default: 2.0)

Selection logic:
    1. Read filter_grid_top100.json (sorted by Sharpe) — pick highest Sharpe with N>=min_n
    2. Fall back to filter_grid_summary.json best_combo if top100 is empty
    3. If no qualifying combo: preserve current filter_config.json unchanged (exit 0)

Merge semantics:
    - Load existing filter_config.json first
    - Apply ONLY the grid-derived keys on top (tcs_offset, rvol_min, gap_min, etc.)
    - ALL other existing keys are preserved verbatim (tcs_intraday_min, drawdown_*,
      applied_at, source_*, _note fields, custom user keys, etc.)
    - This ensures no existing configuration is accidentally lost on apply
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

TOP100_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "filter_grid_top100.json")
SUMMARY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "filter_grid_summary.json")
CONFIG_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "filter_config.json")

# Keys that are derived from the grid search best combo and overwritten on apply.
# Every OTHER key in filter_config.json is preserved verbatim.
_GRID_DERIVED_KEYS = {
    "tcs_offset",
    "rvol_min",
    "gap_min",
    "gap_direction",
    "follow_min_pct",
    "struct_filter",
    "struct_tokens",
    "struct_label",
    "excl_false_break",
    "scan_type",
    "screener",
    "vwap_position",
    "ib_size",
    "mfe_min",
    "mae_max",
    "rvol_cap",
    "day_of_week",
    "pm_range_floor",
    "pm_ib_dir",
    # Apply metadata (overwritten on each apply)
    "applied_at",
    "applied_from",
    "source_phase",
    "source_combo_rank",
    "source_sharpe",
    "source_weekly_expectancy_r",
    "source_n_trades",
}


def _load_json(path: str) -> list | dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        print(f"[apply_best_grid_combo] WARNING: could not load {path}: {exc}", file=sys.stderr)
        return None


def _load_existing_config() -> dict:
    cfg = _load_json(CONFIG_PATH)
    return cfg if isinstance(cfg, dict) else {}


def _grid_values_from_combo(combo: dict, source_label: str, rank: int) -> dict:
    """Extract ONLY the grid-derived keys from a combo dict."""
    return {
        "tcs_offset":       int(combo.get("tcs_offset", 0)),
        "rvol_min":         float(combo.get("rvol_min", 0.0)),
        "gap_min":          float(combo.get("gap_min", 0.0)),
        "gap_direction":    combo.get("gap_direction", "any"),
        "follow_min_pct":   float(combo.get("follow_min", combo.get("follow_min_pct", -999.0))),
        "struct_filter":    "custom" if combo.get("struct_tokens") else combo.get("struct_filter", "all"),
        "struct_tokens":    combo.get("struct_tokens", []),
        "struct_label":     combo.get("struct_label", ""),
        "excl_false_break": bool(combo.get("excl_false_break", False)),
        "scan_type":        combo.get("scan_type", "any"),
        "screener":         combo.get("screener", "any"),
        "vwap_position":    combo.get("vwap_position", "any"),
        "ib_size":          combo.get("ib_size", "any"),
        "mfe_min":          combo.get("mfe_min", "any"),
        "mae_max":          combo.get("mae_max", "any"),
        "rvol_cap":         combo.get("rvol_cap", "none"),
        "day_of_week":      combo.get("day_of_week", "any"),
        "pm_range_floor":   float(combo.get("pm_range_floor") or 0.0),
        "pm_ib_dir":        combo.get("pm_ib_dir", "any"),
        "applied_at":       datetime.datetime.utcnow().isoformat() + "Z",
        "applied_from":     source_label,
        "source_phase":     3,
        "source_combo_rank": rank,
        "source_sharpe":    combo.get("sharpe"),
        "source_weekly_expectancy_r": combo.get("weekly_expectancy_r"),
        "source_n_trades":  combo.get("n_trades"),
    }


def select_best_combo(
    top100: list | None, summary: dict | None, min_n: int, min_sharpe: float
) -> tuple[dict | None, str, int]:
    """Select the best combo. Returns (combo_dict, source_label, rank_1indexed)."""
    if top100 and isinstance(top100, list):
        for i, combo in enumerate(top100):
            if combo.get("n_trades", 0) >= min_n and combo.get("sharpe", 0) >= min_sharpe:
                return combo, TOP100_PATH, i + 1

    if summary and isinstance(summary, dict):
        best = summary.get("best_combo")
        if best and isinstance(best, dict):
            if best.get("n_trades", 0) >= min_n and best.get("sharpe", 0) >= min_sharpe:
                return best, SUMMARY_PATH + " best_combo", 1

    return None, "", 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auto-apply best Phase 3 grid combo to filter_config.json"
    )
    parser.add_argument("--dry-run",    action="store_true", help="Print without writing")
    parser.add_argument("--min-n",      type=int,   default=30,  help="Min trade count (default: 30)")
    parser.add_argument("--min-sharpe", type=float, default=2.0, help="Min Sharpe (default: 2.0)")
    args = parser.parse_args()

    top100   = _load_json(TOP100_PATH)
    summary  = _load_json(SUMMARY_PATH)
    existing = _load_existing_config()

    combo, source, rank = select_best_combo(
        top100     = top100  if isinstance(top100,  list) else None,
        summary    = summary if isinstance(summary, dict) else None,
        min_n      = args.min_n,
        min_sharpe = args.min_sharpe,
    )

    if combo is None:
        print(
            f"[apply_best_grid_combo] No qualifying combo (min_n={args.min_n}, "
            f"min_sharpe={args.min_sharpe}). filter_config.json unchanged."
        )
        # Ensure tcs_intraday_min default is present even without a new combo
        if "tcs_intraday_min" not in existing:
            merged = dict(existing)
            merged["tcs_intraday_min"] = 35
            if not args.dry_run:
                with open(CONFIG_PATH, "w") as f:
                    json.dump(merged, f, indent=2)
                print("[apply_best_grid_combo] Added default tcs_intraday_min=35.")
            else:
                print("[apply_best_grid_combo] DRY-RUN: would add tcs_intraday_min=35.")
        return 0

    # Merge: start from existing config, update ONLY grid-derived keys
    merged = dict(existing)
    grid_vals = _grid_values_from_combo(combo, source, rank)
    merged.update(grid_vals)

    print(
        f"[apply_best_grid_combo] Best combo — source: {source} rank #{rank}\n"
        f"  structures : {combo.get('struct_label', '?')}\n"
        f"  tcs_offset : +{combo.get('tcs_offset', 0)}\n"
        f"  gap_min    : {combo.get('gap_min', 0)}%  gap_dir: {combo.get('gap_direction', 'any')}\n"
        f"  scan_type  : {combo.get('scan_type', 'any')}\n"
        f"  n_trades   : {combo.get('n_trades','?')}  sharpe: {combo.get('sharpe','?')}  WR: {combo.get('win_rate','?')}%\n"
        f"  Preserved (not overwritten): {sorted(set(existing.keys()) - _GRID_DERIVED_KEYS)}"
    )

    if args.dry_run:
        print("\n[apply_best_grid_combo] DRY-RUN — would write:")
        print(json.dumps(merged, indent=2))
        return 0

    with open(CONFIG_PATH, "w") as f:
        json.dump(merged, f, indent=2)

    print(f"[apply_best_grid_combo] filter_config.json updated from {source}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
