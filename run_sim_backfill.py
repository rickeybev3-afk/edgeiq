"""
run_sim_backfill.py
───────────────────
HISTORICAL BACKFILL SCRIPT — executed on 2026-04-16 (initial run).

Backfills sim P&L fields on existing backtest_sim_runs and paper_trades.
New rows inserted after that date get sim computed automatically on insert,
so this script should NOT need to be run again under normal circumstances.

If new compute_trade_sim() logic is deployed (e.g. a formula change) and a
full recompute is needed, it is safe to re-run — it overwrites all rows whose
actual_outcome is "Bullish Break" or "Bearish Break".

Re-run required after the close_price migration (2026-04-16):
  The close_price column was absent from backtest_sim_runs and paper_trades
  during the initial run, so eod_pnl_r could not be computed for pre-existing
  rows.  After running the SQL migration (migrations/add_close_price_column.sql
  or via the /api/run-migrations endpoint), re-run this script to back-fill
  eod_pnl_r on all historical rows that have a stored close_price.

Results of the 2026-04-16 run (verified via null-count queries post-run):
  backtest_sim_runs: 13 575 / 13 798 breakout rows filled (98.4%)
                     223 rows remain null — all return invalid_ib (IB range
                     is degenerate, e.g. ib_high <= ib_low); unfillable by design.
  paper_trades     :     22 /     22 breakout rows filled (100%)
  eod_pnl_r        : skipped for all rows (close_price column was missing).
                     Re-run after migration to populate.

Results of the 2026-04-16 close_price migration + re-run:
  Migration applied : ADD COLUMN close_price NUMERIC to both tables (IF NOT EXISTS).
  backtest_sim_runs : 105 breakout rows with close_price → 105 eod_pnl_r filled (100%).
                      139 rows have close_price but no eod_pnl_r — all are non-breakout
                      (Pending / Both Sides / Range-Bound); unfillable by design.
  paper_trades      :   0 rows with close_price → 0 eod_pnl_r (no historical close data).
  Verified          : 0 rows have close_price IS NOT NULL AND eod_pnl_r IS NULL for
                      any Bullish Break / Bearish Break row. Backfill complete.

Re-run required after close_price historical backfill (run backfill_close_prices.py first):
  backfill_close_prices.py now populates close_price for all historical rows
  (backtest_sim_runs AND paper_trades) that had close_price = NULL by fetching
  EOD daily bars from Alpaca IEX.  After that script finishes, re-run this
  script to compute eod_pnl_r for the ~13,800 newly-populated rows.
  Steps:
    1. python backfill_close_prices.py
    2. python run_sim_backfill.py

Related backfill scripts:
  backfill_ib_vwap.py — backfills ib_range_pct and vwap_at_ib for paper_trades
    rows inserted before those columns were added (2026-04-17).  Run once:
      python backfill_ib_vwap.py

Uses concurrent threads to run Supabase updates in parallel — much faster
than sequential updates.

Usage
─────
  python run_sim_backfill.py                           # full recompute, all users
  python run_sim_backfill.py --skip-existing           # only rows missing sim data
  python run_sim_backfill.py <uid1> [uid2] [uid3]...   # explicit user IDs
  python run_sim_backfill.py --skip-existing <uid1>    # skip-existing + explicit IDs

Flags
─────
  --skip-existing   Skip rows where both sim_outcome and eod_pnl_r are already
                    populated.  Rows missing either field are still processed.
                    Use this for incremental runs after the initial full backfill.
                    Omit to force a full recompute (e.g. after a formula change).
"""

import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend
from concurrent.futures import ThreadPoolExecutor, as_completed

PAGE_SZ     = 1000
MAX_WORKERS = 20   # concurrent update threads

# Module-level progress tracking — updated by backfill_table(), read by __main__
_PROGRESS = {"current": 0, "total": 0}

BACKFILL_TABLES = [
    ("backtest_sim_runs", "id"),
    ("paper_trades",      "id"),
]


# ──────────────────────────────────────────────────────────────────────────────
# User discovery
# ──────────────────────────────────────────────────────────────────────────────

def discover_user_ids() -> list[str]:
    """Return all distinct user_ids found across the tables being backfilled.

    Paginates through every table exhaustively so no user is silently missed,
    regardless of table size.
    """
    if not backend.supabase:
        return []

    uid_set: set[str] = set()
    for table, _ in BACKFILL_TABLES:
        offset     = 0
        rows_scanned = 0
        print(f"  Scanning {table} for user IDs…", end="", flush=True)
        try:
            while True:
                resp = (
                    backend.supabase.table(table)
                    .select("user_id")
                    .range(offset, offset + PAGE_SZ - 1)
                    .execute()
                )
                rows = resp.data or []
                for row in rows:
                    uid = row.get("user_id")
                    if uid:
                        uid_set.add(uid)
                rows_scanned += len(rows)
                if len(rows) < PAGE_SZ:
                    break
                offset += PAGE_SZ
        except Exception as e:
            print(f"\n  ERROR: could not fully scan {table} for user_ids: {e}")
            print("  Aborting — pass user IDs explicitly via CLI args to bypass discovery.")
            sys.exit(1)
        print(f" {rows_scanned} rows scanned.")

    return sorted(uid_set)


# ──────────────────────────────────────────────────────────────────────────────
# Pre-count helpers
# ──────────────────────────────────────────────────────────────────────────────

def count_rows_to_process(user_ids: list[str], skip_existing: bool = False) -> int | None:
    """Return total breakout rows across all users and tables that will be
    processed.  Used to set _PROGRESS["total"] before the main loop starts.

    Returns None when any count query fails so callers can distinguish a
    genuine zero-work result from a count error (which would also return 0
    and could otherwise cause a silent no-op in --skip-existing mode).

    When skip_existing=True, only counts rows that are missing sim_outcome
    or eod_pnl_r (i.e. rows that actually need work).
    """
    if not backend.supabase:
        return None
    total = 0
    for table, id_col in BACKFILL_TABLES:
        for uid in user_ids:
            for direction in ("Bullish Break", "Bearish Break"):
                try:
                    q = (
                        backend.supabase.table(table)
                        .select(id_col, count="exact")
                        .eq("user_id", uid)
                        .eq("actual_outcome", direction)
                    )
                    if skip_existing:
                        # Only count rows where at least one sim field is missing
                        q = q.or_("sim_outcome.is.null,eod_pnl_r.is.null")
                    resp = q.limit(0).execute()
                    total += resp.count or 0
                except Exception:
                    return None   # signal "count unavailable" rather than returning 0
    return total


# ──────────────────────────────────────────────────────────────────────────────
# Core backfill helpers
# ──────────────────────────────────────────────────────────────────────────────

def _sim_patch(r: dict) -> dict | None:
    sim = backend.compute_trade_sim(r)
    if sim.get("sim_outcome") in ("no_trade", "missing_data", "invalid_ib", None):
        return None
    patch = {
        "sim_outcome":      sim["sim_outcome"],
        "pnl_r_sim":        sim.get("pnl_r_sim"),
        "pnl_pct_sim":      sim.get("pnl_pct_sim"),
        "entry_price_sim":  sim.get("entry_price_sim"),
        "stop_price_sim":   sim.get("stop_price_sim"),
        "stop_dist_pct":    sim.get("stop_dist_pct"),
        "target_price_sim": sim.get("target_price_sim"),
    }
    # EOD Hold P&L from stored close_price (no bars needed — computable from DB data).
    # Tiered P&L cannot be backfilled (requires intraday bars that aren't stored).
    # New batch backtest runs will populate tiered_pnl_r going forward.
    close_price    = r.get("close_price")
    ib_high        = r.get("ib_high")
    ib_low         = r.get("ib_low")
    actual_outcome = (r.get("actual_outcome") or "").strip()
    if (close_price is not None and ib_high is not None and ib_low is not None
            and actual_outcome in ("Bullish Break", "Bearish Break")):
        tiered = backend.compute_trade_sim_tiered(
            aft_df    = None,
            ib_high   = ib_high,
            ib_low    = ib_low,
            direction = actual_outcome,
            close_px  = close_price,
        )
        if tiered.get("eod_pnl_r") is not None:
            patch["eod_pnl_r"] = tiered["eod_pnl_r"]
    return patch


def _update_one(table: str, id_col: str, row_id, patch: dict):
    backend.supabase.table(table).update(patch).eq(id_col, row_id).execute()
    return True


def backfill_table(table: str, id_col: str, user_id: str, skip_existing: bool = False):
    """Recompute sim fields for all breakout rows belonging to *user_id*.

    Args:
        skip_existing: When True, only fetch rows where sim_outcome IS NULL or
                       eod_pnl_r IS NULL.  Rows that already have both fields
                       populated are not re-fetched or re-written, making
                       incremental runs significantly faster on large datasets.
                       When False (default), every breakout row is recomputed
                       and overwritten — use this after a formula change.
    """
    if not backend.supabase:
        print("No Supabase connection.")
        return 0

    mode_label = "incremental (skip-existing)" if skip_existing else "full recompute"
    print(f"\n{'='*60}")
    print(f"  Backfilling: {table}  (user {user_id})  [{mode_label}]")
    print(f"{'='*60}")

    total_updated = 0
    total_errors  = 0

    for direction in ("Bullish Break", "Bearish Break"):
        dir_updated = 0
        dir_errors  = 0

        # ── Pagination strategy ────────────────────────────────────────────────
        # Full-recompute:  offset-based — safe because the filter set is stable
        #                  (we're matching on actual_outcome which never changes).
        # Skip-existing:   keyset (cursor) — offset-based pagination is UNSAFE
        #                  here because updating a row removes it from the
        #                  "sim_outcome IS NULL OR eod_pnl_r IS NULL" filter set,
        #                  causing the next offset window to skip forward over
        #                  rows that haven't been processed yet.
        #                  Keyset: ORDER BY id + id > last_seen_id keeps the
        #                  cursor stable regardless of filter-set mutations.
        offset   = 0        # used in full-recompute mode
        last_id  = None     # used in skip-existing mode (keyset cursor)

        while True:
            # Build base query — fetch fields needed by _sim_patch / compute_trade_sim.
            # Includes close_price if the column exists — falls back gracefully.
            try:
                q = (
                    backend.supabase.table(table)
                    .select(f"{id_col},predicted,actual_outcome,ib_low,ib_high,"
                            f"follow_thru_pct,false_break_up,false_break_down,close_price,"
                            f"sim_outcome,eod_pnl_r")
                    .eq("user_id", user_id)
                    .eq("actual_outcome", direction)
                )
                if skip_existing:
                    # Only return rows where at least one sim field still needs filling.
                    # Use keyset pagination to avoid offset drift on a mutating set.
                    q = q.or_("sim_outcome.is.null,eod_pnl_r.is.null").order(id_col)
                    if last_id is not None:
                        q = q.gt(id_col, last_id)
                    resp = q.limit(PAGE_SZ).execute()
                else:
                    resp = q.range(offset, offset + PAGE_SZ - 1).execute()
            except Exception as e:
                err = str(e)
                if "close_price" in err or "column" in err.lower():
                    # close_price (or sim_outcome/eod_pnl_r) column not yet added
                    print(f"  ⚠  Column missing in query — falling back to minimal select")
                    try:
                        q2 = (
                            backend.supabase.table(table)
                            .select(f"{id_col},predicted,actual_outcome,ib_low,ib_high,"
                                    f"follow_thru_pct,false_break_up,false_break_down")
                            .eq("user_id", user_id)
                            .eq("actual_outcome", direction)
                        )
                        if skip_existing:
                            # Preserve skip-existing semantics even in the fallback
                            # path.  sim_outcome/eod_pnl_r are not in SELECT but
                            # the DB can still filter on them.
                            q2 = q2.or_("sim_outcome.is.null,eod_pnl_r.is.null").order(id_col)
                            if last_id is not None:
                                q2 = q2.gt(id_col, last_id)
                            resp = q2.limit(PAGE_SZ).execute()
                        else:
                            resp = q2.range(offset, offset + PAGE_SZ - 1).execute()
                    except Exception as e2:
                        print(f"  Fetch error: {e2}")
                        break
                else:
                    print(f"  Fetch error: {e}")
                    break

            rows = resp.data or []
            if not rows:
                break

            # Build list of (id, patch) for rows with a valid sim result.
            # In skip_existing mode the DB filter already excludes fully-filled rows,
            # so every row in `rows` genuinely needs work.  We still call _sim_patch
            # to check for missing IB data (returns None → skip that row).
            updates = []
            for row in rows:
                patch = _sim_patch(row)
                if patch:
                    updates.append((row[id_col], patch))

            skipped_no_ib = len(rows) - len(updates)

            # Run updates concurrently
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures = {
                    pool.submit(_update_one, table, id_col, row_id, patch): row_id
                    for row_id, patch in updates
                }
                for fut in as_completed(futures):
                    try:
                        fut.result()
                        dir_updated  += 1
                        total_updated += 1
                    except Exception as e:
                        dir_errors  += 1
                        total_errors += 1
                        if total_errors <= 3:
                            print(f"  Update error: {e}")
                            if "column" in str(e).lower():
                                print("  → Columns missing — run the SQL migrations first.")
                                return 0

            # Emit progress for the sidebar to parse
            _PROGRESS["current"] += len(rows)
            _prog_total = _PROGRESS["total"]
            if _prog_total > 0:
                print(f"PROGRESS: {min(_PROGRESS['current'], _prog_total)}/{_prog_total}", flush=True)

            # Advance cursor for next page
            if skip_existing:
                last_id = rows[-1][id_col]
                pos_label = f"cursor>{last_id}"
            else:
                pos_label = f"+{offset:5d}"
                offset += PAGE_SZ

            skip_note = f"{skipped_no_ib} skipped (no IB data)"
            print(f"  [{direction:15s}] {pos_label} | {len(rows):4d} rows fetched | "
                  f"{len(updates)} updated | {skip_note}")

            if len(rows) < PAGE_SZ:
                break

        print(f"  [{direction}] done — {dir_updated} updated, {dir_errors} errors")

    if skip_existing:
        print(f"\n  Total: {total_updated} updated | {total_errors} errors"
              f"  (rows already filled were not fetched)")
    else:
        print(f"\n  Total: {total_updated} updated | {total_errors} errors")
    return total_updated


def print_summary(user_id: str):
    if not backend.supabase:
        return
    print(f"\n{'='*60}")
    print(f"  SIMULATION SUMMARY  (user {user_id})")
    print(f"{'='*60}")

    for table in ("backtest_sim_runs", "paper_trades"):
        try:
            resp = (
                backend.supabase.table(table)
                .select("scan_type,sim_outcome,pnl_r_sim")
                .eq("user_id", user_id)
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


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("EdgeIQ — Paper Trade Simulation Backfill (concurrent)")
    print("=" * 60)

    # ── Parse flags ────────────────────────────────────────────────────────────
    raw_args     = sys.argv[1:]
    skip_existing = "--skip-existing" in raw_args
    uid_args      = [a for a in raw_args if not a.startswith("--")]

    if skip_existing:
        print("Mode: incremental — rows with sim_outcome AND eod_pnl_r already set will be skipped.")
    else:
        print("Mode: full recompute — all breakout rows will be processed (use --skip-existing for incremental).")

    # ── Resolve user IDs ───────────────────────────────────────────────────────
    if uid_args:
        seen: dict[str, None] = {}
        for uid in uid_args:
            seen[uid] = None
        user_ids = list(seen)
        if len(user_ids) < len(uid_args):
            print(f"  Note: {len(uid_args) - len(user_ids)} duplicate user ID(s) removed.")
        print(f"Using {len(user_ids)} user ID(s) from command-line arguments.")
    else:
        print("No user IDs specified — querying database for all distinct users...")
        user_ids = discover_user_ids()
        if not user_ids:
            print("No users found in the database. Nothing to backfill.")
            sys.exit(0)
        print(f"Found {len(user_ids)} user(s): {user_ids}")

    t0 = time.time()

    # Pre-count so the sidebar can display a meaningful progress bar.
    # In skip-existing mode the count only covers rows that actually need work.
    # count_rows_to_process() returns None when a query error occurs, so we can
    # distinguish "no rows to process" from "count unavailable".
    count_label = "rows needing update" if skip_existing else "rows to recompute"
    print(f"\nCounting {count_label}…", flush=True)
    _total = count_rows_to_process(user_ids, skip_existing=skip_existing)

    if _total is None:
        # Count failed — warn and continue; progress bar will be unavailable.
        # In skip-existing mode we do NOT exit early because the count failure
        # could mask real work that still needs doing.
        print("⚠  Could not determine row count — progress bar will be unavailable.", flush=True)
        _PROGRESS["total"]   = 0
        _PROGRESS["current"] = 0
    elif _total == 0:
        if skip_existing:
            print("No rows need updating — all breakout rows are already fully populated.", flush=True)
            sys.exit(0)
        else:
            print("Row count returned 0 — nothing to recompute (or count unavailable).", flush=True)
        _PROGRESS["total"]   = 0
        _PROGRESS["current"] = 0
    else:
        _PROGRESS["total"]   = _total
        _PROGRESS["current"] = 0
        print(f"PROGRESS: 0/{_total}", flush=True)
        print(f"Total {count_label}: {_total:,}", flush=True)

    for uid in user_ids:
        print(f"\n{'#'*60}")
        print(f"  Processing user: {uid}")
        print(f"{'#'*60}")
        for table, id_col in BACKFILL_TABLES:
            backfill_table(table, id_col, uid, skip_existing=skip_existing)

    elapsed = time.time() - t0

    for uid in user_ids:
        print_summary(uid)

    print(f"\n✅ Backfill complete for {len(user_ids)} user(s) in {elapsed:.0f}s")

    # ── Context level backfill (S/R, VWAP, MACD for adaptive exit analysis) ──
    print("\n" + "=" * 60)
    print("  Context Level Backfill (S/R, VWAP, MACD)")
    print("=" * 60)
    try:
        import backfill_context_levels as _ctx
        _ctx.main()
    except Exception as _ctx_err:
        print(f"  Context backfill error (non-critical): {_ctx_err}")
