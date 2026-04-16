"""
run_sim_backfill.py
───────────────────
Backfills sim P&L fields on existing backtest_sim_runs and paper_trades.
Uses concurrent threads to run Supabase updates in parallel — much faster
than sequential updates.

Run once after the SQL migrations. Also safe to re-run (skips already-filled rows).
"""

import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend
from concurrent.futures import ThreadPoolExecutor, as_completed

USER_ID   = "a5e1fcab-8369-42c4-8550-a8a19734510c"
PAGE_SZ   = 1000
MAX_WORKERS = 20   # concurrent update threads


def _sim_patch(r: dict) -> dict | None:
    sim = backend.compute_trade_sim(r)
    if sim.get("sim_outcome") in ("no_trade", "missing_data", "invalid_ib", None):
        return None
    return {
        "sim_outcome":      sim["sim_outcome"],
        "pnl_r_sim":        sim.get("pnl_r_sim"),
        "pnl_pct_sim":      sim.get("pnl_pct_sim"),
        "entry_price_sim":  sim.get("entry_price_sim"),
        "stop_price_sim":   sim.get("stop_price_sim"),
        "stop_dist_pct":    sim.get("stop_dist_pct"),
        "target_price_sim": sim.get("target_price_sim"),
    }


def _update_one(table: str, id_col: str, row_id, patch: dict):
    backend.supabase.table(table).update(patch).eq(id_col, row_id).execute()
    return True


def backfill_table(table: str, id_col: str):
    if not backend.supabase:
        print("No Supabase connection.")
        return 0

    print(f"\n{'='*60}")
    print(f"  Backfilling: {table}")
    print(f"{'='*60}")

    total_updated = 0
    total_errors  = 0

    for direction in ("Bullish Break", "Bearish Break"):
        offset     = 0
        dir_updated = 0
        dir_errors  = 0

        while True:
            # Fetch all confirmed breakout rows (actual_outcome = direction).
            # Recompute everything to correct any stale MFE-based sim values.
            # Includes close_price if the column exists — falls back gracefully.
            try:
                resp = (
                    backend.supabase.table(table)
                    .select(f"{id_col},predicted,actual_outcome,ib_low,ib_high,follow_thru_pct,false_break_up,false_break_down,close_price")
                    .eq("user_id", USER_ID)
                    .eq("actual_outcome", direction)
                    .range(offset, offset + PAGE_SZ - 1)
                    .execute()
                )
            except Exception as e:
                err = str(e)
                if "close_price" in err or "column" in err.lower():
                    # close_price column not yet added — run SQL migration first
                    print(f"  ⚠  close_price column missing — falling back to select without it")
                    try:
                        resp = (
                            backend.supabase.table(table)
                            .select(f"{id_col},predicted,actual_outcome,ib_low,ib_high,follow_thru_pct,false_break_up,false_break_down")
                            .eq("user_id", USER_ID)
                            .eq("actual_outcome", direction)
                            .range(offset, offset + PAGE_SZ - 1)
                            .execute()
                        )
                    except Exception as e2:
                        print(f"  Fetch error: {e2}")
                        break
                else:
                    print(f"  Fetch error: {e}")
                    break

            rows = resp.data or []
            if not rows:
                break

            # Build list of (id, patch) for rows with a valid sim result
            updates = []
            for row in rows:
                patch = _sim_patch(row)
                if patch:
                    updates.append((row[id_col], patch))

            skipped = len(rows) - len(updates)

            # Run updates concurrently
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures = {
                    pool.submit(_update_one, table, id_col, row_id, patch): row_id
                    for row_id, patch in updates
                }
                for fut in as_completed(futures):
                    try:
                        fut.result()
                        dir_updated += 1
                        total_updated += 1
                    except Exception as e:
                        dir_errors += 1
                        total_errors += 1
                        if total_errors <= 3:
                            print(f"  Update error: {e}")
                            if "column" in str(e).lower():
                                print("  → Columns missing — run the SQL migrations first.")
                                return 0

            print(f"  [{direction:15s}] +{offset:5d} | {len(rows):4d} rows | "
                  f"{len(updates)} updated | {skipped} skipped (no IB data)")

            offset += PAGE_SZ
            if len(rows) < PAGE_SZ:
                break

        print(f"  [{direction}] done — {dir_updated} updated, {dir_errors} errors")
        total_updated += 0  # already counted above

    print(f"\n  Total: {total_updated} updated | {total_errors} errors")
    return total_updated


def print_summary():
    if not backend.supabase:
        return
    print(f"\n{'='*60}")
    print("  SIMULATION SUMMARY")
    print(f"{'='*60}")

    for table in ("backtest_sim_runs", "paper_trades"):
        try:
            resp = (
                backend.supabase.table(table)
                .select("scan_type,sim_outcome,pnl_r_sim")
                .eq("user_id", USER_ID)
                .not_.is_("sim_outcome", "null")
                .neq("sim_outcome", "no_trade")
                .limit(15000)
                .execute()
            )
            rows = resp.data or []
            if not rows:
                print(f"\n  {table}: no sim data yet")
                continue

            all_r  = [float(r.get("pnl_r_sim") or 0) for r in rows]
            wins   = [x for x in all_r if x > 0]
            losses = [x for x in all_r if x <= 0]
            wr     = len(wins) / len(all_r) * 100 if all_r else 0
            avg_w  = sum(wins) / len(wins) if wins else 0
            avg_l  = sum(losses) / len(losses) if losses else 0
            exp    = sum(all_r) / len(all_r) if all_r else 0
            total_r = sum(all_r)

            print(f"\n  {table.upper()} ({len(all_r)} sim trades)")
            print(f"  Win rate (R > 0) : {wr:.1f}%  ({len(wins)}W / {len(losses)}L)")
            print(f"  Avg winner       : +{avg_w:.2f}R")
            print(f"  Avg loser        :  {avg_l:.2f}R")
            print(f"  Expectancy       : {exp:+.3f}R / trade")
            print(f"  Total R          : {total_r:+.1f}R")

            from collections import defaultdict
            by_scan = defaultdict(list)
            for r in rows:
                by_scan[r.get("scan_type") or "morning"].append(float(r.get("pnl_r_sim") or 0))
            for st in ["morning", "intraday", "eod"]:
                rs = by_scan.get(st, [])
                if not rs:
                    continue
                w2  = [x for x in rs if x > 0]
                wr2 = len(w2) / len(rs) * 100
                print(f"    {st:10s}: {wr2:.1f}% win | {sum(rs)/len(rs):+.3f}R exp | {len(rs)} trades")

        except Exception as e:
            print(f"  Summary error for {table}: {e}")


if __name__ == "__main__":
    print("EdgeIQ — Paper Trade Simulation Backfill (concurrent)")
    print("=" * 60)

    t0 = time.time()
    backfill_table("backtest_sim_runs", "id")
    backfill_table("paper_trades",      "id")
    elapsed = time.time() - t0

    print_summary()
    print(f"\n✅ Backfill complete in {elapsed:.0f}s")
