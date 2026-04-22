"""apply_best_grid_combo.py — Parse Phase 3 grid search results and auto-apply best combo
to filter_config.json while always preserving tcs_intraday_min.

Usage:
    python3 apply_best_grid_combo.py                # uses default paths
    python3 apply_best_grid_combo.py --dry-run      # show what would be written, don't write
    python3 apply_best_grid_combo.py --min-n 30     # require at least N trades (default: 30)
    python3 apply_best_grid_combo.py --min-sharpe 2 # require Sharpe >= 2.0 (default: 2.0)

Selection logic:
    1. Read filter_grid_top100.json (sorted by Sharpe) — pick highest Sharpe with N>=min_n
    2. Fall back to filter_grid_summary.json best_combo if top100 is empty
    3. If no qualifying combo exists, preserve current filter_config.json unchanged
       and exit with code 0 (non-fatal — this is expected when the grid is too sparse)

Preserves from existing filter_config.json:
    - tcs_intraday_min   (intraday TCS floor — set independently)
    - drawdown_lookback_n / drawdown_warning_r / drawdown_critical_r (risk management)

Applies from best combo:
    - tcs_offset, rvol_min, gap_min, gap_direction, follow_min_pct, struct_filter,
      struct_tokens, struct_label, excl_false_break, scan_type, screener,
      vwap_position, ib_size, mfe_min, mae_max, rvol_cap, day_of_week,
      pm_range_floor, pm_ib_dir
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

TOP100_PATH  = os.path.join(os.path.dirname(__file__), "filter_grid_top100.json")
SUMMARY_PATH = os.path.join(os.path.dirname(__file__), "filter_grid_summary.json")
CONFIG_PATH  = os.path.join(os.path.dirname(__file__), "filter_config.json")

# Keys to always preserve from the existing config (user-managed, not grid-search derived)
_PRESERVE_KEYS = {
    "tcs_intraday_min",
    "drawdown_lookback_n",
    "drawdown_warning_r",
    "drawdown_critical_r",
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


def _build_new_config(combo: dict, existing: dict, source_label: str) -> dict:
    """Merge combo into a new filter_config dict, preserving _PRESERVE_KEYS from existing."""
    cfg = {
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
        "source_sharpe":    combo.get("sharpe"),
        "source_n_trades":  combo.get("n_trades"),
        "source_weekly_expectancy_r": combo.get("weekly_expectancy_r"),
    }

    # Preserve user-managed keys
    for key in _PRESERVE_KEYS:
        if key in existing:
            cfg[key] = existing[key]

    # Default tcs_intraday_min to 35 if not already set
    if "tcs_intraday_min" not in cfg:
        cfg["tcs_intraday_min"] = 35

    return cfg


def select_best_combo(top100: list | None, summary: dict | None, min_n: int, min_sharpe: float) -> tuple[dict | None, str]:
    """Select the best combo from available sources. Returns (combo_dict, source_label)."""
    # 1. Try top100 (already sorted by Sharpe descending from grid search)
    if top100 and isinstance(top100, list):
        for combo in top100:
            n = combo.get("n_trades", 0)
            sharpe = combo.get("sharpe", 0)
            if n >= min_n and sharpe >= min_sharpe:
                rank = top100.index(combo) + 1
                return combo, f"filter_grid_top100.json rank={rank}"

    # 2. Try summary best_combo (non-lookahead)
    if summary and isinstance(summary, dict):
        best = summary.get("best_combo")
        if best and isinstance(best, dict):
            n = best.get("n_trades", 0)
            sharpe = best.get("sharpe", 0)
            if n >= min_n and sharpe >= min_sharpe:
                return best, "filter_grid_summary.json best_combo"

    return None, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-apply best Phase 3 grid combo to filter_config.json")
    parser.add_argument("--dry-run",    action="store_true", help="Print what would be written without writing")
    parser.add_argument("--min-n",      type=int,   default=30,  help="Minimum trade count (default: 30)")
    parser.add_argument("--min-sharpe", type=float, default=2.0, help="Minimum Sharpe ratio (default: 2.0)")
    args = parser.parse_args()

    top100  = _load_json(TOP100_PATH)
    summary = _load_json(SUMMARY_PATH)
    existing = _load_existing_config()

    combo, source = select_best_combo(
        top100  = top100  if isinstance(top100,  list) else None,
        summary = summary if isinstance(summary, dict) else None,
        min_n   = args.min_n,
        min_sharpe = args.min_sharpe,
    )

    if combo is None:
        print(
            f"[apply_best_grid_combo] No qualifying combo found "
            f"(min_n={args.min_n}, min_sharpe={args.min_sharpe}). "
            f"filter_config.json unchanged."
        )
        # Still ensure tcs_intraday_min is present in existing config
        if "tcs_intraday_min" not in existing:
            existing["tcs_intraday_min"] = 35
            if not args.dry_run:
                with open(CONFIG_PATH, "w") as f:
                    json.dump(existing, f, indent=2)
                print("[apply_best_grid_combo] Added tcs_intraday_min=35 to existing config.")
            else:
                print("[apply_best_grid_combo] DRY-RUN: would add tcs_intraday_min=35 to existing config.")
        return 0

    new_cfg = _build_new_config(combo, existing, source)

    print(
        f"[apply_best_grid_combo] Best combo selected from {source}:\n"
        f"  structures : {combo.get('struct_label', '?')}\n"
        f"  tcs_offset : +{combo.get('tcs_offset', 0)}\n"
        f"  gap_min    : {combo.get('gap_min', 0)}%\n"
        f"  gap_dir    : {combo.get('gap_direction', 'any')}\n"
        f"  scan_type  : {combo.get('scan_type', 'any')}\n"
        f"  n_trades   : {combo.get('n_trades', '?')}\n"
        f"  sharpe     : {combo.get('sharpe', '?')}\n"
        f"  win_rate   : {combo.get('win_rate', '?')}%\n"
        f"  avg_r      : {combo.get('avg_r', '?')}\n"
        f"  Preserved  : tcs_intraday_min={new_cfg.get('tcs_intraday_min', 35)} "
        f"(user-managed, not overwritten)"
    )

    if args.dry_run:
        print("\n[apply_best_grid_combo] DRY-RUN — would write:")
        print(json.dumps(new_cfg, indent=2))
        return 0

    with open(CONFIG_PATH, "w") as f:
        json.dump(new_cfg, f, indent=2)

    print(f"[apply_best_grid_combo] filter_config.json updated from {source}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
