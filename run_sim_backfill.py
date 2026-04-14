"""
run_sim_backfill.py
───────────────────
Backfills pnl_r_sim, pnl_pct_sim, sim_outcome, entry_price_sim, stop_price_sim,
stop_dist_pct, and target_price_sim on all existing backtest_sim_runs and
paper_trades records.

Run once after adding the sim columns in Supabase:

    ALTER TABLE backtest_sim_runs
      ADD COLUMN IF NOT EXISTS sim_outcome TEXT,
      ADD COLUMN IF NOT EXISTS pnl_r_sim FLOAT,
      ADD COLUMN IF NOT EXISTS pnl_pct_sim FLOAT,
      ADD COLUMN IF NOT EXISTS entry_price_sim FLOAT,
      ADD COLUMN IF NOT EXISTS stop_price_sim FLOAT,
      ADD COLUMN IF NOT EXISTS stop_dist_pct FLOAT,
      ADD COLUMN IF NOT EXISTS target_price_sim FLOAT;

    ALTER TABLE paper_trades
      ADD COLUMN IF NOT EXISTS scan_type TEXT DEFAULT 'morning',
      ADD COLUMN IF NOT EXISTS sim_outcome TEXT,
      ADD COLUMN IF NOT EXISTS pnl_r_sim FLOAT,
      ADD COLUMN IF NOT EXISTS pnl_pct_sim FLOAT,
      ADD COLUMN IF NOT EXISTS entry_price_sim FLOAT,
      ADD COLUMN IF NOT EXISTS stop_price_sim FLOAT,
      ADD COLUMN IF NOT EXISTS stop_dist_pct FLOAT,
      ADD COLUMN IF NOT EXISTS target_price_sim FLOAT;

Then run:
    python run_sim_backfill.py
"""

import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend

USER_ID   = "a5e1fcab-8369-42c4-8550-a8a19734510c"
BATCH_SZ  = 200   # records per Supabase update call
PAGE_SZ   = 1000  # records per fetch


def _sim_patch(r: dict) -> dict | None:
    """Return a patch dict with sim fields, or None if no_trade / error."""
    ft = r.get("follow_thru_pct") or r.get("aft_move_pct")
    row = {**r, "follow_thru_pct": ft}
    sim = backend.compute_trade_sim(row)
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


def backfill_table(table: str, id_col: str, date_col: str):
    """Fetch all rows without sim_outcome, compute, and update in batches."""
    if not backend.supabase:
        print("No Supabase connection — check secrets.")
        return

    print(f"\n{'='*60}")
    print(f"  Backfilling: {table}")
    print(f"{'='*60}")

    offset    = 0
    processed = 0
    updated   = 0
    skipped   = 0
    errors    = 0

    while True:
        try:
            resp = (
                backend.supabase.table(table)
                .select(f"{id_col},{date_col},predicted,actual_outcome,ib_low,ib_high,follow_thru_pct,false_break_up,false_break_down,sim_outcome")
                .eq("user_id", USER_ID)
                .is_("sim_outcome", "null")          # only unprocessed rows
                .range(offset, offset + PAGE_SZ - 1)
                .execute()
            )
        except Exception as e:
            print(f"  Fetch error at offset {offset}: {e}")
            break

        rows = resp.data or []
        if not rows:
            break

        patches = []
        for row in rows:
            processed += 1
            patch = _sim_patch(row)
            if patch:
                patches.append((row[id_col], patch))
            else:
                skipped += 1

        # Update in batches
        for i in range(0, len(patches), BATCH_SZ):
            chunk = patches[i : i + BATCH_SZ]
            for rec_id, patch in chunk:
                try:
                    (
                        backend.supabase.table(table)
                        .update(patch)
                        .eq(id_col, rec_id)
                        .execute()
                    )
                    updated += 1
                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        print(f"  Update error ({rec_id}): {e}")
                        if "column" in str(e).lower():
                            print("  → Sim columns may not exist yet. Run the SQL above first.")
                            return
            time.sleep(0.05)   # avoid rate limits

        print(f"  Offset {offset:6d} | fetched {len(rows):4d} | updated {updated:5d} | "
              f"skipped {skipped:5d} | errors {errors}")
        offset += PAGE_SZ

        if len(rows) < PAGE_SZ:
            break

    print(f"\n  Done — {processed} rows scanned, {updated} updated, "
          f"{skipped} no_trade, {errors} errors")
    return updated


def print_summary():
    """Print quick P&L stats from the backfilled data."""
    if not backend.supabase:
        return

    print(f"\n{'='*60}")
    print("  SIMULATION SUMMARY (backtest_sim_runs)")
    print(f"{'='*60}")

    try:
        resp = (
            backend.supabase.table("backtest_sim_runs")
            .select("scan_type,sim_outcome,pnl_r_sim,predicted")
            .eq("user_id", USER_ID)
            .not_.is_("sim_outcome", "null")
            .neq("sim_outcome", "no_trade")
            .limit(5000)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            print("  No sim data found yet.")
            return

        from collections import defaultdict
        by_scan = defaultdict(list)
        for r in rows:
            by_scan[r.get("scan_type", "morning")].append(r.get("pnl_r_sim") or 0.0)

        all_r = [r.get("pnl_r_sim") or 0.0 for r in rows]
        wins  = [x for x in all_r if x > 0]
        losses = [x for x in all_r if x <= 0]
        total_r = sum(all_r)
        win_rate = len(wins) / len(all_r) * 100 if all_r else 0
        avg_win  = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        expectancy = (len(wins)/len(all_r) * avg_win + len(losses)/len(all_r) * avg_loss) if all_r else 0

        print(f"\n  OVERALL ({len(all_r)} simulated trades)")
        print(f"  Win Rate (pnl > 0):  {win_rate:.1f}%")
        print(f"  Avg Winner:          +{avg_win:.2f}R")
        print(f"  Avg Loser:           {avg_loss:.2f}R")
        print(f"  Expectancy:          {expectancy:.3f}R / trade")
        print(f"  Total R earned:      {total_r:.1f}R")

        print(f"\n  BY SCAN TYPE:")
        for st in ["morning", "intraday", "eod"]:
            rs = by_scan.get(st, [])
            if not rs:
                continue
            w = [x for x in rs if x > 0]
            wr = len(w) / len(rs) * 100
            exp = sum(rs) / len(rs)
            print(f"  {st.upper():12s}: {wr:.1f}% win rate | {exp:+.3f}R expectancy | {len(rs)} trades")

        # Outcome breakdown
        from collections import Counter
        outcomes = Counter(r.get("sim_outcome") for r in rows)
        print(f"\n  OUTCOME BREAKDOWN:")
        for k, v in sorted(outcomes.items(), key=lambda x: -x[1]):
            print(f"    {k:20s}: {v} ({100*v/len(rows):.1f}%)")

    except Exception as e:
        print(f"  Summary error: {e}")


if __name__ == "__main__":
    print("EdgeIQ — Paper Trade Simulation Backfill")
    print("=" * 60)

    backfill_table("backtest_sim_runs", "id", "sim_date")
    backfill_table("paper_trades",      "id", "trade_date")
    print_summary()

    print("\n✅ Backfill complete. Re-run after adding more backtest data.")
