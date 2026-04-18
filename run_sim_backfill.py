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

  run_vwap_backfill.py — backfills vwap_at_ib for backtest_sim_runs rows that
    pre-date Task 1226.  After running, the pace target in
    get_backtest_pace_target() drops from ~1.5/day to ~0.81/day because the
    VWAP-alignment filter now applies to the full historical dataset.  Run once
    (repeat until "0 NULL rows remain" is printed):
      python run_vwap_backfill.py
      python run_vwap_backfill.py --no-ratelimit  # faster on paid Alpaca plan

Uses concurrent threads to run Supabase updates in parallel — much faster
than sequential updates.

Usage
─────
  python run_sim_backfill.py                               # full recompute, all users
  python run_sim_backfill.py --skip-existing               # only rows missing sim data
  python run_sim_backfill.py --dry-run                     # preview changes, no writes
  python run_sim_backfill.py --dry-run --skip-existing     # preview only missing rows
  python run_sim_backfill.py --dry-run --out=report.json   # save dry-run report to file
  python run_sim_backfill.py <uid1> [uid2] [uid3]...       # explicit user IDs
  python run_sim_backfill.py --skip-existing <uid1>        # skip-existing + explicit IDs
  python run_sim_backfill.py --dry-run <uid1>              # dry-run for specific user

Flags
─────
  --skip-existing   Skip rows where sim_outcome and eod_pnl_r are already
                    populated AND sim_version matches SIM_VERSION AND
                    tiered_sim_version matches TIERED_SIM_VERSION (both
                    constants in backend.py).  Rows missing any field or
                    stamped with an older version are re-processed
                    automatically — no need to drop the flag after a formula
                    change; just bump the relevant version and re-run.
                    Omit to force a full recompute of every breakout row.
  --dry-run         Count and print the rows that would be changed without writing
                    anything to the database.  Reads are still performed so the
                    script can inspect each row via compute_trade_sim(), but no
                    UPDATE calls are issued.  Can be combined with --skip-existing.
  --out=<file>      Only valid with --dry-run.  Write a JSON summary of the
                    dry-run results to <file>.  The report contains one entry per
                    (table, user_id, direction) with would_update and unfillable
                    counts, plus top-level totals and metadata (generated_at,
                    sim_version, skip_existing flag).  If --out is omitted the
                    report is written to a timestamped file such as
                    dry_run_2026-04-18_123456.json in the current directory.
                    Two reports can be compared with `diff`, `jq`, or any JSON
                    diffing tool to see exactly which rows shifted between runs.
"""

import sys, os, time, json, datetime
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

    When skip_existing=True, only counts rows that are missing sim_outcome,
    eod_pnl_r, or have a stale sim_version (i.e. rows that actually need work).
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
                        # Count rows that are missing a sim field OR have a stale version.
                        # sim_version.neq.<ver> also matches NULLs in PostgREST, so the
                        # explicit sim_version.is.null guard is included for clarity.
                        # tiered_sim_version guards are added so rows with a stale
                        # eod_pnl_r formula are also re-processed after logic changes.
                        q = q.or_(
                            f"sim_outcome.is.null,eod_pnl_r.is.null,"
                            f"sim_version.is.null,sim_version.neq.{backend.SIM_VERSION},"
                            f"tiered_sim_version.is.null,"
                            f"tiered_sim_version.neq.{backend.TIERED_SIM_VERSION}"
                        )
                    resp = q.limit(0).execute()
                    total += resp.count or 0
                except Exception:
                    return None   # signal "count unavailable" rather than returning 0
    return total


# ──────────────────────────────────────────────────────────────────────────────
# Context levels cache — keyed (ticker, trade_date, scan_type)
# Loaded once per backfill_table() call so _sim_patch() can enrich rows with
# nearest_resistance / nearest_support needed for the v6 trail-tightening sim.
# ──────────────────────────────────────────────────────────────────────────────

_CTX_LEVELS: dict = {}   # module-level cache; reset per call


def _load_context_levels() -> None:
    """Load ALL backtest_context_levels rows into _CTX_LEVELS via keyset pagination.

    Keys are normalized: ticker.upper().strip(), trade_date as-is, scan_type.strip().
    This avoids silent cache misses from inconsistent casing in the source data.
    """
    global _CTX_LEVELS
    _CTX_LEVELS = {}
    try:
        last_id = None
        while True:
            q = (
                backend.supabase.table("backtest_context_levels")
                .select("id,ticker,trade_date,scan_type,nearest_resistance,nearest_support")
                .order("id")
                .limit(1000)
            )
            if last_id is not None:
                q = q.gt("id", last_id)
            resp = q.execute()
            batch = resp.data or []
            if not batch:
                break
            for row in batch:
                key = (
                    (row["ticker"] or "").upper().strip(),
                    row["trade_date"],
                    (row["scan_type"] or "").strip(),
                )
                _CTX_LEVELS[key] = row
            last_id = batch[-1]["id"]
            if len(batch) < 1000:
                break
        print(f"  [v6] Context levels loaded: {len(_CTX_LEVELS)} rows (paginated)")
    except Exception as e:
        print(f"  [v6] Could not load context levels (non-fatal — v6 falls back to 1R trail): {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Core backfill helpers
# ──────────────────────────────────────────────────────────────────────────────

def _sim_patch(r: dict) -> dict | None:
    # Compute adaptive target_r per row — same 3-layer logic as paper_trader_bot.
    # Layer 1: structure override, Layer 2: scan_type + TCS, Layer 3: TCS fallback.
    _tcs       = float(r.get("tcs") or 0)
    _scan      = (r.get("scan_type") or "").strip()
    _structure = (r.get("predicted") or r.get("actual_outcome") or "").strip()
    _target_r  = backend.adaptive_target_r(_tcs, scan_type=_scan, structure=_structure)

    # Enrich row with S/R context levels for v6 trail-tightening logic.
    # sim_date (backtest_sim_runs) or trade_date (paper_trades) is the date key.
    # Keys are normalized identically to _load_context_levels() to avoid silent misses.
    _trade_date = (r.get("sim_date") or r.get("trade_date") or "")
    _ticker     = (r.get("ticker") or "").upper().strip()
    _scan_norm  = _scan.strip()
    if _CTX_LEVELS and _trade_date and _ticker and _scan_norm:
        _ctx = _CTX_LEVELS.get((_ticker, _trade_date, _scan_norm))
        if _ctx:
            r = dict(r)  # shallow copy — do not mutate original row
            r["nearest_resistance"] = _ctx.get("nearest_resistance")
            r["nearest_support"]    = _ctx.get("nearest_support")

    _sim_raw = backend.compute_trade_sim(r, target_r=_target_r)
    if _sim_raw.get("sim_outcome") in ("no_trade", "missing_data", "invalid_ib", None):
        return None

    # Apply RVOL bonus sizing multiplier so backfill rows model the same
    # dollar-scaled R-contribution as the live bot and batch_backtest paths.
    # Delegates to backend.apply_rvol_sizing_to_sim() — the shared helper used
    # by all compute_trade_sim() call sites that write pnl_r_sim to the DB.
    sim = backend.apply_rvol_sizing_to_sim(_sim_raw, r.get("rvol"))

    patch = {
        "sim_outcome":      sim["sim_outcome"],
        "pnl_r_sim":        sim.get("pnl_r_sim"),
        "pnl_pct_sim":      sim.get("pnl_pct_sim"),
        "entry_price_sim":  sim.get("entry_price_sim"),
        "stop_price_sim":   sim.get("stop_price_sim"),
        "stop_dist_pct":    sim.get("stop_dist_pct"),
        "target_price_sim": sim.get("target_price_sim"),
        "sim_version":      sim.get("sim_version"),
    }
    if sim.get("rvol_mult") is not None:
        patch["rvol_mult"] = sim["rvol_mult"]
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
        _eod_r = tiered.get("eod_pnl_r")
        if _eod_r is not None:
            _rvol_raw = r.get("rvol")
            if _rvol_raw is not None:
                try:
                    _rvol_mult = backend.rvol_size_mult(float(_rvol_raw))
                    if _rvol_mult != 1.0:
                        _eod_r = round(float(_eod_r) * _rvol_mult, 4)
                except (TypeError, ValueError):
                    pass
            patch["eod_pnl_r"] = _eod_r
            patch["tiered_sim_version"] = backend.TIERED_SIM_VERSION
    return patch


def _update_one(table: str, id_col: str, row_id, patch: dict):
    backend.supabase.table(table).update(patch).eq(id_col, row_id).execute()
    return True


def backfill_table(table: str, id_col: str, user_id: str,
                   skip_existing: bool = False, dry_run: bool = False):
    """Recompute sim fields for all breakout rows belonging to *user_id*.

    Args:
        skip_existing: When True, only fetch rows where sim_outcome IS NULL,
                       eod_pnl_r IS NULL, sim_version IS NULL, sim_version
                       does not match the current SIM_VERSION constant,
                       tiered_sim_version IS NULL, or tiered_sim_version does
                       not match the current TIERED_SIM_VERSION constant.
                       Stale rows (written by an older formula) are
                       automatically re-processed even in skip-existing mode —
                       operators no longer need to remember to drop the flag
                       after a formula change.
                       When False (default), every breakout row is recomputed
                       and overwritten.
        dry_run:       When True, rows are fetched and inspected via _sim_patch()
                       so the script can report exactly what would change, but no
                       UPDATE calls are issued.  Returns the total number of rows
                       that would have been updated (same type as normal mode).
    """
    if not backend.supabase:
        print("No Supabase connection.")
        return 0

    dry_label  = " [DRY RUN — no writes]" if dry_run else ""
    mode_label = "incremental (skip-existing)" if skip_existing else "full recompute"
    print(f"\n{'='*60}")
    print(f"  {'DRY RUN — ' if dry_run else ''}Backfilling: {table}  (user {user_id})  [{mode_label}]{dry_label}")
    print(f"{'='*60}")

    # Pre-load S/R context levels for v6 trail-tightening sim.
    _load_context_levels()

    # Date column name differs by table: backtest_sim_runs → sim_date, paper_trades → trade_date.
    _date_col = "trade_date" if table == "paper_trades" else "sim_date"

    total_updated = 0
    total_errors  = 0
    dry_run_counts: dict[str, dict] = {}  # populated only when dry_run=True

    for direction in ("Bullish Break", "Bearish Break"):
        dir_updated    = 0
        dir_errors     = 0
        dir_unfillable = 0   # rows skipped because IB data is missing (dry-run tracking)

        # ── Pagination strategy ────────────────────────────────────────────────
        # Full-recompute:  offset-based — safe because the filter set is stable
        #                  (we're matching on actual_outcome which never changes).
        # Skip-existing:   keyset (cursor) — offset-based pagination is UNSAFE
        #                  here because updating a row removes it from the
        #                  "sim_outcome IS NULL OR eod_pnl_r IS NULL OR stale
        #                  sim_version" filter set, causing the next offset window
        #                  to skip forward over rows not yet processed.
        #                  Keyset: ORDER BY id + id > last_seen_id keeps the
        #                  cursor stable regardless of filter-set mutations.
        offset   = 0        # used in full-recompute mode
        last_id  = None     # used in skip-existing mode (keyset cursor)

        # PostgREST or_ string shared between main and fallback query paths.
        # Catches rows missing any sim field, or with a stale sim_version OR
        # tiered_sim_version so eod_pnl_r is re-computed after formula changes.
        _SKIP_FILTER = (
            f"sim_outcome.is.null,eod_pnl_r.is.null,"
            f"sim_version.is.null,sim_version.neq.{backend.SIM_VERSION},"
            f"tiered_sim_version.is.null,"
            f"tiered_sim_version.neq.{backend.TIERED_SIM_VERSION}"
        )

        while True:
            # Build base query — fetch fields needed by _sim_patch / compute_trade_sim.
            # Includes close_price and sim_version if the columns exist — falls back gracefully.
            try:
                q = (
                    backend.supabase.table(table)
                    .select(f"{id_col},ticker,predicted,actual_outcome,ib_low,ib_high,"
                            f"follow_thru_pct,false_break_up,false_break_down,close_price,"
                            f"tcs,scan_type,mfe,mae,rvol,{_date_col},"
                            f"sim_outcome,eod_pnl_r,sim_version,tiered_sim_version")
                    .eq("user_id", user_id)
                    .eq("actual_outcome", direction)
                )
                if skip_existing:
                    # Only return rows where a sim field is missing OR sim_version
                    # is stale — automatically re-processes rows after formula changes.
                    # Use keyset pagination to avoid offset drift on a mutating set.
                    q = q.or_(_SKIP_FILTER).order(id_col)
                    if last_id is not None:
                        q = q.gt(id_col, last_id)
                    resp = q.limit(PAGE_SZ).execute()
                else:
                    resp = q.range(offset, offset + PAGE_SZ - 1).execute()
            except Exception as e:
                err = str(e)
                if ("close_price" in err or "sim_version" in err
                        or "tiered_sim_version" in err or "column" in err.lower()):
                    # close_price / sim_version (or sim_outcome/eod_pnl_r) column not yet added
                    print(f"  ⚠  Column missing in query — falling back to minimal select")
                    try:
                        q2 = (
                            backend.supabase.table(table)
                            .select(f"{id_col},ticker,predicted,actual_outcome,ib_low,ib_high,"
                                    f"follow_thru_pct,false_break_up,false_break_down,"
                                    f"tcs,scan_type,rvol,{_date_col}")
                            .eq("user_id", user_id)
                            .eq("actual_outcome", direction)
                        )
                        if skip_existing:
                            # Preserve skip-existing semantics even in the fallback path.
                            # sim_outcome/eod_pnl_r/sim_version are not in SELECT but
                            # the DB can still filter on them.
                            q2 = q2.or_(_SKIP_FILTER).order(id_col)
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

            if dry_run:
                # Dry-run: tally what would be updated without issuing any writes.
                dir_updated    += len(updates)
                total_updated  += len(updates)
                dir_unfillable += skipped_no_ib
            else:
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

            skip_note  = f"{skipped_no_ib} skipped (no IB data)"
            action_lbl = "would update" if dry_run else "updated"
            print(f"  [{direction:15s}] {pos_label} | {len(rows):4d} rows fetched | "
                  f"{len(updates)} {action_lbl} | {skip_note}")

            if len(rows) < PAGE_SZ:
                break

        if dry_run:
            dry_run_counts[direction] = {
                "would_update": dir_updated,
                "unfillable":   dir_unfillable,
            }
            print(f"  [{direction}] DRY RUN — would update {dir_updated:,} row(s), "
                  f"{dir_unfillable:,} unfillable (no IB data)")
        else:
            print(f"  [{direction}] done — {dir_updated} updated, {dir_errors} errors")

    if dry_run:
        bullish_u  = dry_run_counts.get("Bullish Break", {}).get("would_update", 0)
        bearish_u  = dry_run_counts.get("Bearish Break", {}).get("would_update", 0)
        total_skip = sum(v.get("unfillable", 0) for v in dry_run_counts.values())
        skip_note  = "  (rows already filled excluded)" if skip_existing else ""
        print(f"\n  DRY RUN total for {table}: {total_updated:,} row(s) would be updated"
              f"  (Bullish Break: {bullish_u:,} | Bearish Break: {bearish_u:,})"
              f"  + {total_skip:,} unfillable skipped{skip_note}")
        return {"total_updated": total_updated, "per_direction": dry_run_counts}
    elif skip_existing:
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


def backfill_rvol_size_mult(user_ids: list[str], dry_run: bool = False) -> int:
    """Copy `rvol` → `rvol_size_mult` for paper_trades rows that have RVOL data
    but no rvol_size_mult stored yet.  Safe to run multiple times (IS NULL guard).

    Returns the number of rows updated (or that would be updated in dry-run mode).
    """
    if not backend.supabase:
        print("  [rvol_backfill] No Supabase connection — skipping.")
        return 0

    dry_label = " [DRY RUN]" if dry_run else ""
    print(f"\n{'='*60}")
    print(f"  RVOL size-mult backfill — paper_trades{dry_label}")
    print(f"{'='*60}")

    total = 0
    PAGE  = 1000
    for uid in user_ids:
        last_id = None
        while True:
            try:
                q = (
                    backend.supabase.table("paper_trades")
                    .select("id,rvol")
                    .eq("user_id", uid)
                    .not_.is_("rvol", "null")
                    .is_("rvol_size_mult", "null")
                    .order("id")
                )
                if last_id is not None:
                    q = q.gt("id", last_id)
                resp = q.limit(PAGE).execute()
            except Exception as e:
                print(f"  [rvol_backfill] Fetch error for user {uid}: {e}")
                break

            rows = resp.data or []
            if not rows:
                break

            if not dry_run:
                for row in rows:
                    try:
                        backend.supabase.table("paper_trades").update(
                            {"rvol_size_mult": row["rvol"]}
                        ).eq("id", row["id"]).execute()
                    except Exception as e:
                        print(f"  [rvol_backfill] Update error id={row['id']}: {e}")
                        continue

            total   += len(rows)
            last_id  = rows[-1]["id"]
            action   = "would update" if dry_run else "updated"
            print(f"  user={uid} | {action} {total} row(s) so far …")

            if len(rows) < PAGE:
                break

    action = "Would update" if dry_run else "Updated"
    print(f"  {action} {total} row(s) total.")
    return total


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("EdgeIQ — Paper Trade Simulation Backfill (concurrent)")
    print("=" * 60)

    # ── Parse flags ────────────────────────────────────────────────────────────
    # Flags
    #   --rvol-only      Run ONLY the rvol_size_mult backfill and exit.
    #                    Accepts --dry-run.  Ignores --skip-existing / --out.
    #   --context-only   Run ONLY the context-level backfill (S/R, VWAP, MACD) and exit.
    #                    Skips the full sim pipeline entirely.
    #   --skip-existing  Skip rows that already have sim_outcome + eod_pnl_r set.
    #   --skip-context   Skip the context-level backfill (S/R, VWAP, MACD) at the end
    #                    of a full sim run.  Useful when context data was already
    #                    refreshed via --context-only.
    #   --skip-rvol      Skip the RVOL size-mult backfill at the end of a full sim run.
    #                    Useful when this step was already run separately.
    #   --dry-run        Inspect rows without writing to the database.
    #   --out=<file>     (dry-run only) Save a JSON report to the given path.
    raw_args      = sys.argv[1:]
    skip_existing = "--skip-existing" in raw_args
    skip_context  = "--skip-context"  in raw_args
    skip_rvol     = "--skip-rvol"     in raw_args
    dry_run       = "--dry-run"       in raw_args
    rvol_only     = "--rvol-only"     in raw_args
    context_only  = "--context-only"  in raw_args

    # --out=<file>  (dry-run only — ignored in live mode)
    _out_flag = next((a for a in raw_args if a.startswith("--out=")), None)
    out_file  = _out_flag[len("--out="):] if _out_flag else None

    uid_args  = [a for a in raw_args
                 if not a.startswith("--")]

    if not rvol_only:
        if not dry_run and out_file:
            print("⚠  --out is only valid with --dry-run and will be ignored in live mode.",
                  file=sys.stderr)
            out_file = None

        if dry_run:
            print("Mode: DRY RUN — rows will be inspected but NO database writes will occur.")
            if skip_existing:
                print("       Combined with --skip-existing: only rows missing sim data will be counted.")
            if out_file:
                print(f"       Report will be saved to: {out_file}")
            else:
                _ts       = datetime.datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
                out_file  = f"dry_run_{_ts}.json"
                print(f"       No --out specified — report will be saved to: {out_file}")
        elif skip_existing:
            print("Mode: incremental — rows with sim_outcome AND eod_pnl_r already set will be skipped.")
        else:
            print("Mode: full recompute — all breakout rows will be processed (use --skip-existing for incremental).")

    # ── context-only short-circuit ─────────────────────────────────────────────
    if context_only:
        print("Mode: CONTEXT-ONLY — running S/R, VWAP, and MACD context backfill.")
        if uid_args:
            _ctx_uids = list(dict.fromkeys(uid_args))
            print(f"  Scoped to {len(_ctx_uids)} user ID(s): {_ctx_uids}")
        else:
            _ctx_uids = None
            print("  No user IDs specified — running for all users.")
        print("=" * 60)
        try:
            import backfill_context_levels as _ctx
            _ctx.main(user_ids=_ctx_uids)
        except Exception as _ctx_err:
            print(f"  Context backfill error: {_ctx_err}")
            sys.exit(1)
        sys.exit(0)

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

    # ── RVOL-only fast path ────────────────────────────────────────────────────
    # When --rvol-only is set, skip the full simulation backfill entirely and
    # run just backfill_rvol_size_mult(), then exit.
    if rvol_only:
        if skip_rvol:
            print("\nMode: --rvol-only + --skip-rvol — nothing to do.")
            print("      --skip-rvol suppresses the rvol_size_mult backfill, which is the")
            print("      only step that --rvol-only would run.  Exiting without changes.")
            sys.exit(0)
        print("\nMode: --rvol-only — running ONLY the rvol_size_mult backfill.")
        if dry_run:
            print("      Combined with --dry-run: no database writes will occur.")
        backfill_rvol_size_mult(user_ids, dry_run=dry_run)
        sys.exit(0)

    t0 = time.time()

    # Pre-count so the sidebar can display a meaningful progress bar.
    # In skip-existing mode the count only covers rows that actually need work.
    # count_rows_to_process() returns None when a query error occurs, so we can
    # distinguish "no rows to process" from "count unavailable".
    if dry_run and skip_existing:
        count_label = "candidate rows to inspect (missing sim data)"
    elif dry_run:
        count_label = "candidate rows to inspect"
    elif skip_existing:
        count_label = "rows needing update"
    else:
        count_label = "rows to recompute"
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
            if dry_run and out_file:
                _zero_report = {
                    "generated_at":  datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "mode":          "dry-run",
                    "skip_existing": skip_existing,
                    "sim_version":   getattr(backend, "SIM_VERSION", None),
                    "rows":          [],
                    "totals":        {"would_update": 0, "unfillable": 0},
                }
                try:
                    with open(out_file, "w") as _f:
                        json.dump(_zero_report, _f, indent=2, sort_keys=True)
                    print(f"  Report saved → {out_file}  (zero rows to update)")
                except Exception as _we:
                    print(f"  ⚠  Could not write report to {out_file}: {_we}")
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

    grand_total_would_update = 0
    _report_rows: list[dict] = []   # populated in dry-run mode

    for uid in user_ids:
        print(f"\n{'#'*60}")
        if dry_run:
            print(f"  DRY RUN — inspecting user: {uid}")
        else:
            print(f"  Processing user: {uid}")
        print(f"{'#'*60}")
        for table, id_col in BACKFILL_TABLES:
            result = backfill_table(table, id_col, uid,
                                    skip_existing=skip_existing, dry_run=dry_run)
            if dry_run:
                if isinstance(result, dict):
                    n = result.get("total_updated", 0)
                    for direction, counts in result.get("per_direction", {}).items():
                        _report_rows.append({
                            "table":        table,
                            "user_id":      uid,
                            "direction":    direction,
                            "would_update": counts.get("would_update", 0),
                            "unfillable":   counts.get("unfillable",   0),
                        })
                else:
                    n = result or 0
                grand_total_would_update += n

    elapsed = time.time() - t0

    if dry_run:
        skip_note = " (--skip-existing: already-filled rows excluded)" if skip_existing else ""
        print(f"\n  Flags in effect:")
        print(f"    --dry-run        : yes")
        print(f"    --skip-existing  : {'yes' if skip_existing else 'no'}")
        print(f"    --skip-context   : {'yes' if skip_context  else 'no'}")
        if out_file:
            _out_label = out_file if _out_flag else f"{out_file}  (auto-generated)"
            print(f"    --out            : {_out_label}")
        print(f"\n{'='*60}")
        print(f"  DRY RUN COMPLETE — {elapsed:.0f}s elapsed")
        print(f"  {grand_total_would_update:,} row(s) across all users/tables would be updated{skip_note}")
        print(f"  No database writes were performed.")
        print(f"{'='*60}")

        # ── Write JSON report ──────────────────────────────────────────────────
        if out_file:
            report = {
                "generated_at":  datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mode":          "dry-run",
                "skip_existing": skip_existing,
                "sim_version":   getattr(backend, "SIM_VERSION", None),
                "rows": _report_rows,
                "totals": {
                    "would_update": grand_total_would_update,
                    "unfillable":   sum(r["unfillable"] for r in _report_rows),
                },
            }
            try:
                with open(out_file, "w") as _f:
                    json.dump(report, _f, indent=2, sort_keys=True)
                print(f"\n  Report saved → {out_file}")
            except Exception as _write_err:
                print(f"\n  ⚠  Could not write report to {out_file}: {_write_err}")
    else:
        for uid in user_ids:
            print_summary(uid)

        print(f"\n  Flags in effect:")
        print(f"    --dry-run        : no")
        print(f"    --skip-existing  : {'yes' if skip_existing else 'no'}")
        print(f"    --skip-context   : {'yes' if skip_context  else 'no'}")

        print(f"\n✅ Backfill complete for {len(user_ids)} user(s) in {elapsed:.0f}s")

        # ── RVOL size-mult backfill — paper_trades only ────────────────────────
        # Copies rvol → rvol_size_mult for historical rows where RVOL data exists
        # but rvol_size_mult was not yet recorded (rows predating this feature).
        if skip_rvol:
            print("\n  --skip-rvol passed — RVOL size-mult backfill skipped.")
        else:
            backfill_rvol_size_mult(user_ids, dry_run=False)

        # ── Context level backfill (S/R, VWAP, MACD for adaptive exit analysis) ──
        if skip_context:
            print("\n  --skip-context passed — context-level backfill skipped.")
        else:
            print("\n" + "=" * 60)
            print("  Context Level Backfill (S/R, VWAP, MACD)")
            print("=" * 60)
            try:
                import backfill_context_levels as _ctx
                _ctx.main()
            except Exception as _ctx_err:
                print(f"  Context backfill error (non-critical): {_ctx_err}")
