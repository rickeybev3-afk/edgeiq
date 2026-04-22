"""
fix_squeeze_data.py
-------------------
One-off backfill to correct four data bugs introduced in squeeze paper_trades
and backtest_sim_runs rows before 2026-04-22.

Direction semantics
-------------------
For squeeze trades, the bot executes based on the PREDICTED direction:
  - predicted="Bullish Break" → long trade; entry=ib_high, stop=ib_low
  - predicted="Bearish Break" → short trade; entry=ib_low, stop=ib_high

All win/loss and pnl_r_sim corrections use `predicted` as the direction source
so the three fields are internally consistent (same directional basis).

Bugs corrected
--------------
1. Win/loss misclassification (paper_trades + backtest_sim_runs):
   Stopped-out trades were marked "Win" because classification used
   prediction-vs-outcome matching that ignored price levels.  The corrected
   logic uses predicted direction + close-vs-stop/target price comparison.
   Bullish: close <= ib_low  → "Loss"; close >= ib_high + 2R → "Win"
   Bearish: close >= ib_high → "Loss"; close <= ib_low  - 2R → "Win"

2. tiered_pnl_r sentinel (paper_trades):
   Bar-by-bar simulation returned -1R when an intraday wick hit the stop before
   a profitable EOD close.  For squeeze, volatile intraday noise is common; the
   more reliable anchor is the close-based R (eod_pnl_r).
   Fix: if tiered_pnl_r < 0 and eod_pnl_r > 0 → tiered_pnl_r = eod_pnl_r.

3. pnl_r_sim wrong direction for bearish squeeze (backtest_sim_runs):
   compute_trade_sim prioritises actual_outcome over predicted.  When
   actual_outcome = "Bullish Break" but predicted = "Bearish Break", the
   bullish-direction R formula was applied.
   Fix: force direction = predicted before calling compute_trade_sim.

4. Win/loss misclassification (backtest_sim_runs) — same as bug #1.

Usage
-----
  python fix_squeeze_data.py              # dry-run (shows what would change)
  python fix_squeeze_data.py --apply      # write corrections to DB
  python fix_squeeze_data.py --table pt   # only paper_trades
  python fix_squeeze_data.py --table bsr  # only backtest_sim_runs
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import backend

PAGE = 500


# ── Helper: price-based win/loss for squeeze ───────────────────────────────────

def _squeeze_win_loss(predicted_direction: str, close_price: float,
                      ib_high: float, ib_low: float,
                      existing_wl: str) -> str | None:
    """Return corrected win_loss based on the PREDICTED trade direction.

    Uses predicted (not actual_outcome) so the result is consistent with the
    directional R formulas used for pnl_r_sim and tiered_pnl_r.

    Returns None when no change is needed (close is between stop and target,
    meaning the existing classification stands).
    """
    ib_rng = ib_high - ib_low
    if ib_rng <= 0:
        return None

    if predicted_direction == "Bullish Break":
        entry  = ib_high
        stop   = ib_low
        target = entry + 2.0 * ib_rng
        if close_price <= stop:
            corrected = "Loss"
        elif close_price >= target:
            corrected = "Win"
        else:
            corrected = existing_wl
    elif predicted_direction == "Bearish Break":
        entry  = ib_low
        stop   = ib_high
        target = entry - 2.0 * ib_rng
        if close_price >= stop:
            corrected = "Loss"
        elif close_price <= target:
            corrected = "Win"
        else:
            corrected = existing_wl
    else:
        return None

    if corrected == existing_wl:
        return None
    return corrected


# ── Fix paper_trades ───────────────────────────────────────────────────────────

def fix_paper_trades(dry_run: bool = True) -> dict:
    stats = {"fetched": 0, "wl_changed": 0, "tiered_changed": 0, "errors": 0}
    if not backend.supabase:
        print("  ERROR: no Supabase connection.")
        return stats

    offset = 0
    while True:
        try:
            resp = (
                backend.supabase.table("paper_trades")
                .select(
                    "id,ticker,trade_date,actual_outcome,predicted,"
                    "ib_high,ib_low,close_price,eod_pnl_r,tiered_pnl_r,win_loss"
                )
                .eq("screener_pass", "squeeze")
                .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
                .not_.is_("close_price", "null")
                .not_.is_("ib_high", "null")
                .not_.is_("ib_low", "null")
                .range(offset, offset + PAGE - 1)
                .execute()
            )
        except Exception as e:
            print(f"  Fetch error (offset={offset}): {e}")
            stats["errors"] += 1
            break

        rows = resp.data or []
        if not rows:
            break
        stats["fetched"] += len(rows)
        offset += len(rows)

        for row in rows:
            row_id          = row["id"]
            ticker          = row.get("ticker", "")
            trade_date      = row.get("trade_date", "")
            predicted       = (row.get("predicted") or "").strip()
            ib_high         = row.get("ib_high")
            ib_low          = row.get("ib_low")
            close_price     = row.get("close_price")
            eod_pnl_r       = row.get("eod_pnl_r")
            tiered_pnl_r    = row.get("tiered_pnl_r")
            existing_wl     = row.get("win_loss") or ""

            if ib_high is None or ib_low is None or close_price is None:
                continue
            if predicted not in ("Bullish Break", "Bearish Break"):
                continue

            ib_high     = float(ib_high)
            ib_low      = float(ib_low)
            close_price = float(close_price)

            patch: dict = {"id": row_id}
            changed = False

            # Bug #1: win/loss price override using predicted direction
            new_wl = _squeeze_win_loss(predicted, close_price,
                                       ib_high, ib_low, existing_wl)
            if new_wl is not None:
                patch["win_loss"] = new_wl
                changed = True
                stats["wl_changed"] += 1
                print(f"  [{ticker}] {trade_date}  win_loss: '{existing_wl}' → '{new_wl}'"
                      + f"  (predicted={predicted})"
                      + (" [DRY RUN]" if dry_run else ""))

            # Bug #2: tiered_pnl_r sentinel override
            if (tiered_pnl_r is not None and float(tiered_pnl_r) < 0
                    and eod_pnl_r is not None and float(eod_pnl_r) > 0):
                new_tiered = round(float(eod_pnl_r), 4)
                patch["tiered_pnl_r"] = new_tiered
                changed = True
                stats["tiered_changed"] += 1
                print(f"  [{ticker}] {trade_date}  tiered_pnl_r: "
                      f"{float(tiered_pnl_r):+.4f}R → {new_tiered:+.4f}R (eod-based)"
                      + (" [DRY RUN]" if dry_run else ""))

            if changed and not dry_run:
                _patch = {k: v for k, v in patch.items() if k != "id"}
                try:
                    backend.supabase.table("paper_trades").update(
                        _patch
                    ).eq("id", row_id).execute()
                except Exception as e:
                    print(f"  DB update error for id={row_id}: {e}")
                    stats["errors"] += 1

        if len(rows) < PAGE:
            break

    return stats


# ── Fix backtest_sim_runs ──────────────────────────────────────────────────────

def fix_backtest_sim_runs(dry_run: bool = True) -> dict:
    stats = {"fetched": 0, "wl_changed": 0, "pnl_r_changed": 0, "errors": 0}
    if not backend.supabase:
        print("  ERROR: no Supabase connection.")
        return stats

    offset = 0
    while True:
        try:
            resp = (
                backend.supabase.table("backtest_sim_runs")
                .select(
                    "id,ticker,sim_date,actual_outcome,predicted,"
                    "ib_high,ib_low,close_price,follow_thru_pct,"
                    "false_break_up,false_break_down,tcs,scan_type,"
                    "pnl_r_sim,win_loss,rvol,mfe,mae"
                )
                .eq("screener_pass", "squeeze")
                .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
                .not_.is_("close_price", "null")
                .not_.is_("ib_high", "null")
                .not_.is_("ib_low", "null")
                .range(offset, offset + PAGE - 1)
                .execute()
            )
        except Exception as e:
            print(f"  Fetch error (offset={offset}): {e}")
            stats["errors"] += 1
            break

        rows = resp.data or []
        if not rows:
            break
        stats["fetched"] += len(rows)
        offset += len(rows)

        for row in rows:
            row_id         = row["id"]
            ticker         = row.get("ticker", "")
            sim_date       = row.get("sim_date", "")
            predicted      = (row.get("predicted") or "").strip()
            ib_high        = row.get("ib_high")
            ib_low         = row.get("ib_low")
            close_price    = row.get("close_price")
            existing_wl    = row.get("win_loss") or ""
            existing_pnl_r = row.get("pnl_r_sim")
            tcs            = row.get("tcs") or 0
            scan_type      = (row.get("scan_type") or "").strip()
            rvol_raw       = row.get("rvol")

            if ib_high is None or ib_low is None or close_price is None:
                continue
            if predicted not in ("Bullish Break", "Bearish Break"):
                continue

            ib_high     = float(ib_high)
            ib_low      = float(ib_low)
            close_price = float(close_price)

            patch: dict = {"id": row_id}
            changed = False

            # Bug #4: win/loss price override using predicted direction
            new_wl = _squeeze_win_loss(predicted, close_price,
                                       ib_high, ib_low, existing_wl)
            if new_wl is not None:
                patch["win_loss"] = new_wl
                changed = True
                stats["wl_changed"] += 1
                print(f"  [{ticker}] {sim_date}  win_loss: '{existing_wl}' → '{new_wl}'"
                      + f"  (predicted={predicted})"
                      + (" [DRY RUN]" if dry_run else ""))

            # Bug #3: pnl_r_sim direction fix — use predicted to force correct
            # directional R formula (bearish squeeze: stop above entry)
            _target_r = backend.adaptive_target_r(
                float(tcs), scan_type=scan_type, structure=predicted
            )
            enriched = dict(row)
            enriched["ib_high"]        = ib_high
            enriched["ib_low"]         = ib_low
            enriched["close_price"]    = close_price
            enriched["actual_outcome"] = predicted
            _sim_raw = backend.compute_trade_sim(enriched, target_r=_target_r)
            if _sim_raw.get("sim_outcome") not in ("no_trade", "missing_data", "invalid_ib", None):
                sim = backend.apply_rvol_sizing_to_sim(_sim_raw, rvol_raw)
                new_pnl_r = sim.get("pnl_r_sim")
                if (new_pnl_r is not None
                        and (existing_pnl_r is None
                             or abs(float(new_pnl_r) - float(existing_pnl_r)) > 0.001)):
                    patch.update({
                        "pnl_r_sim":        new_pnl_r,
                        "entry_price_sim":  sim.get("entry_price_sim"),
                        "stop_price_sim":   sim.get("stop_price_sim"),
                        "target_price_sim": sim.get("target_price_sim"),
                        "pnl_pct_sim":      sim.get("pnl_pct_sim"),
                        "sim_outcome":      sim.get("sim_outcome"),
                    })
                    changed = True
                    stats["pnl_r_changed"] += 1
                    old_r_s = (f"{float(existing_pnl_r):+.4f}R"
                               if existing_pnl_r is not None else "None")
                    print(f"  [{ticker}] {sim_date}  pnl_r_sim: {old_r_s} → "
                          f"{float(new_pnl_r):+.4f}R (predicted={predicted})"
                          + (" [DRY RUN]" if dry_run else ""))

            if changed and not dry_run:
                _patch = {k: v for k, v in patch.items() if k != "id"}
                try:
                    backend.supabase.table("backtest_sim_runs").update(
                        _patch
                    ).eq("id", row_id).execute()
                except Exception as e:
                    print(f"  DB update error for id={row_id}: {e}")
                    stats["errors"] += 1

        if len(rows) < PAGE:
            break

    return stats


# ── Entry point ────────────────────────────────────────────────────────────────

def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix squeeze trade data bugs in paper_trades and backtest_sim_runs."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write corrections to DB (default: dry-run only).",
    )
    parser.add_argument(
        "--table",
        choices=["pt", "bsr", "both"],
        default="both",
        help="Which table to fix: pt=paper_trades, bsr=backtest_sim_runs, both (default).",
    )
    args = parser.parse_args()

    dry_run = not args.apply

    if dry_run:
        print("=== fix_squeeze_data.py  [DRY RUN — pass --apply to write] ===\n")
    else:
        print("=== fix_squeeze_data.py  [APPLYING CHANGES] ===\n")

    if args.table in ("pt", "both"):
        print("── paper_trades ──────────────────────────────────────────────")
        pt_stats = fix_paper_trades(dry_run=dry_run)
        print(
            f"\n  paper_trades summary:"
            f"  fetched={pt_stats['fetched']}"
            f"  win_loss_changed={pt_stats['wl_changed']}"
            f"  tiered_changed={pt_stats['tiered_changed']}"
            f"  errors={pt_stats['errors']}\n"
        )

    if args.table in ("bsr", "both"):
        print("── backtest_sim_runs ─────────────────────────────────────────")
        bsr_stats = fix_backtest_sim_runs(dry_run=dry_run)
        print(
            f"\n  backtest_sim_runs summary:"
            f"  fetched={bsr_stats['fetched']}"
            f"  win_loss_changed={bsr_stats['wl_changed']}"
            f"  pnl_r_changed={bsr_stats['pnl_r_changed']}"
            f"  errors={bsr_stats['errors']}\n"
        )

    if dry_run:
        print("Run with --apply to commit these corrections to the database.")
    else:
        print("Done.  Next step: re-run calibration on corrected data:")
        print("  python calibrate_sp_mult.py --pass squeeze --apply")
        print("Then paste the citation block into strategy_notes.md.")


if __name__ == "__main__":
    _main()
