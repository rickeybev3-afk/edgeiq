"""
run_tcs_floor_backfill.py
─────────────────────────
Backfills tcs_floor on existing paper_trades rows that were logged before
Task #910 (which added live tcs_floor persistence to new trades).

Without tcs_floor populated the "Marginal vs Comfortable" breakdown in the
sweep tier cards is hidden by the _has_marg_data_sweep guard in app.py.  Run
this script once to unlock that breakdown for all historical trades.

How the floor is reconstructed
────────────────────────────────
The live bot computes tcs_floor as:

    wk        = label_to_weight_key(predicted)          # structure → weight key
    cal_tcs   = tcs_thresholds.get(wk, MIN_TCS)         # per-structure calibrated bar
    tcs_floor = max(cal_tcs, regime_floor)               # macro safety net

where:
  • predicted        — structure label already stored on every paper_trades row
  • tcs_thresholds   — loaded from tcs_thresholds.json (nightly recalibrated;
                       used here as the best available proxy for historical values)
  • regime_floor     — min_tcs_filter already stored per-row (set at scan time to
                       effective_min_tcs = MIN_TCS + tcs_adj)

All inputs are either on the row or on disk; no external API calls are needed.

Target rows
────────────
  paper_trades WHERE tcs_floor IS NULL

Idempotency
────────────
The WHERE clause filters on tcs_floor IS NULL, so re-running the script after a
successful backfill is safe — already-populated rows are never touched.

Usage
─────
  python run_tcs_floor_backfill.py                    # all users
  python run_tcs_floor_backfill.py <uid1> [uid2] …   # specific user(s) only
  python run_tcs_floor_backfill.py --dry-run          # preview changes, no writes
  python run_tcs_floor_backfill.py --all-rows         # include rows where predicted IS NULL
                                                      # (floor = max(MIN_TCS, min_tcs_filter))

Results of the initial run — fill in after execution:
  paper_trades: __ rows qualified, __ updated, __ skipped (no predicted), __ errors
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend

# ── Constants ─────────────────────────────────────────────────────────────────

PAGE_SZ    = 500   # rows per Supabase fetch
UPSERT_CHK = 500   # rows per batch upsert

MIN_TCS = int(os.getenv("PAPER_TRADE_MIN_TCS", "50"))


# ── Column check / migration ──────────────────────────────────────────────────

_TCS_FLOOR_MIGRATION = (
    "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS tcs_floor SMALLINT"
)


def _ensure_tcs_floor_column() -> bool:
    """Probe for the tcs_floor column; add it if missing.

    Returns True when the column is ready for use, False on failure.
    """
    try:
        backend.supabase.table("paper_trades").select("tcs_floor").limit(1).execute()
        print("  tcs_floor column present — OK")
        return True
    except Exception as probe_err:
        err_s = str(probe_err).lower()
        if "does not exist" in err_s or "42703" in err_s or "column" in err_s:
            print("  tcs_floor column missing — running migration…")
            try:
                backend.supabase.rpc("run_sql", {"query": _TCS_FLOOR_MIGRATION}).execute()
                print("  tcs_floor column created via RPC.")
                return True
            except Exception:
                # PostgREST RPC may not expose run_sql; fall through to direct hint.
                pass
            # Final fallback: ask the operator to add the column manually.
            print(
                "\n  ERROR: tcs_floor column does not exist and could not be added automatically.\n"
                "  Run the following SQL in your Supabase SQL Editor and retry:\n\n"
                f"      {_TCS_FLOOR_MIGRATION};\n"
            )
            return False
        # Some other error (network, auth) — surface it.
        print(f"  Column probe error: {probe_err}")
        return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_floor(predicted: str, min_tcs_filter: int, tcs_thresholds: dict) -> int:
    """Reconstruct the tcs_floor value using the same logic as the live bot.

    Mirrors paper_trader_bot._struct_tcs_floor().
    Falls back gracefully when predicted is empty or unrecognised.
    """
    wk      = backend.label_to_weight_key(predicted.strip()) if predicted and predicted.strip() else ""
    cal_tcs = tcs_thresholds.get(wk, MIN_TCS) if wk else MIN_TCS
    return max(cal_tcs, min_tcs_filter)


def _batch_update(patches: list[dict], table: str = "paper_trades") -> tuple[int, list]:
    """Update rows one by one.  Each patch must contain 'id' + fields to write.

    Uses row-by-row .update().eq("id") which is the most reliable path for
    partial-column patches on Supabase's PostgREST client.

    Returns (n_updated, failed_ids) so callers can quarantine persistent failures.
    """
    updated = 0
    failed_ids: list = []
    for patch in patches:
        row_id = patch.pop("id")
        try:
            backend.supabase.table(table).update(patch).eq("id", row_id).execute()
            updated += 1
        except Exception as e:
            print(f"    DB update error id={row_id}: {e}")
            failed_ids.append(row_id)
    return updated, failed_ids


# ── Core backfill ─────────────────────────────────────────────────────────────

def backfill(
    user_ids: list[str],
    dry_run: bool,
    all_rows: bool,
) -> dict:
    """Backfill tcs_floor for NULL rows.

    Parameters
    ──────────
    user_ids  — if non-empty only these users are processed; else all users
    dry_run   — print what would be written without touching the DB
    all_rows  — when True also process rows where predicted IS NULL (floor
                defaults to max(MIN_TCS, min_tcs_filter))
    """
    stats = {
        "fetched":            0,
        "updated":            0,
        "would_update":       0,   # dry-run only: rows that would have been written
        "skipped_no_pred":    0,
        "errors":             0,
    }

    if not backend.supabase:
        print("ERROR: No Supabase connection — cannot run backfill.")
        return stats

    # Ensure the column exists (add it if the migration hasn't run yet).
    if not _ensure_tcs_floor_column():
        return stats

    # Load the best available per-structure TCS thresholds.
    # tcs_thresholds.json is recalibrated nightly, so the current file is the
    # closest proxy we have to the historical values.
    tcs_thresholds = backend.load_tcs_thresholds(default=MIN_TCS)
    print(f"Loaded tcs_thresholds: { {k: v for k, v in tcs_thresholds.items()} }")

    if user_ids:
        # Specific users requested — filter per user_id using exact semantics.
        # Each uid is treated as a literal string (eq) rather than IS NULL to
        # avoid accidentally matching rows with a different user_id variant.
        print(f"Processing {len(user_ids)} user(s) from CLI: {user_ids}")
        for uid in user_ids:
            print(f"\n── User: {uid!r} ──")
            _backfill_slice(
                uid, filter_by_user=True,
                dry_run=dry_run, all_rows=all_rows,
                tcs_thresholds=tcs_thresholds, stats=stats,
            )
    else:
        # No user filter — process every NULL row in one pass.
        # This avoids '' vs IS NULL ambiguity: we simply don't partition by user_id.
        print("Processing all users (no user_id filter).")
        _backfill_slice(
            None, filter_by_user=False,
            dry_run=dry_run, all_rows=all_rows,
            tcs_thresholds=tcs_thresholds, stats=stats,
        )

    return stats


def _apply_user_filter(q, user_id: str | None):
    """Apply the correct Supabase user_id filter for a given user_id value.

    Handles three distinct cases so no rows are accidentally skipped:
      None   → user_id IS NULL in the database
      ""     → user_id = '' (empty string, written by log_paper_trades when no uid)
      other  → user_id = <value>
    """
    if user_id is None:
        return q.is_("user_id", "null")
    return q.eq("user_id", user_id)


def _backfill_slice(
    user_id,              # the value to filter on (only used when filter_by_user=True)
    filter_by_user: bool, # whether to apply a user_id filter at all
    dry_run: bool,
    all_rows: bool,
    tcs_thresholds: dict,
    stats: dict,
) -> None:
    """Paginate through qualifying paper_trades rows and write tcs_floor.

    When filter_by_user is False all NULL rows are processed without any
    user_id predicate, which is safe for the "all users" mode and avoids
    the '' vs IS NULL ambiguity.

    When filter_by_user is True, _apply_user_filter() maps the user_id value
    to the correct PostgREST predicate (IS NULL for None, eq for any string).
    """
    skipped_ids: list = []
    iteration = 0

    while True:
        iteration += 1

        try:
            q = (
                backend.supabase.table("paper_trades")
                .select("id,ticker,trade_date,predicted,min_tcs_filter")
                .is_("tcs_floor", "null")
            )
            if filter_by_user:
                q = _apply_user_filter(q, user_id)
            if not all_rows:
                # Only rows that have a predicted label — required to derive the
                # per-structure calibrated threshold.
                q = q.not_.is_("predicted", "null")
            if skipped_ids:
                q = q.not_.in_("id", skipped_ids)
            resp = q.limit(PAGE_SZ).execute()
        except Exception as e:
            print(f"  Fetch error (iteration {iteration}): {e}")
            stats["errors"] += 1
            break

        rows = resp.data or []
        if not rows:
            if iteration == 1:
                print("  No qualifying NULL rows found — nothing to do.")
            break

        stats["fetched"] += len(rows)
        print(f"\n  Iteration {iteration}: {len(rows)} qualifying row(s)")

        patches: list[dict] = []

        for row in rows:
            row_id         = row["id"]
            ticker         = row.get("ticker") or "?"
            trade_date     = row.get("trade_date") or "?"
            predicted      = row.get("predicted") or ""
            min_tcs_filter = int(row.get("min_tcs_filter") or MIN_TCS)

            if not all_rows and not predicted.strip():
                # Should not reach here given the NOT NULL filter above, but
                # guard defensively so we never write a nonsensical floor.
                print(f"    [{ticker}] {trade_date}  predicted=NULL — skipping")
                stats["skipped_no_pred"] += 1
                skipped_ids.append(row_id)
                continue

            floor = _compute_floor(predicted, min_tcs_filter, tcs_thresholds)
            print(
                f"    [{ticker}] {trade_date}  predicted={predicted!r:30s} "
                f"min_tcs_filter={min_tcs_filter}  → tcs_floor={floor}"
                + (" [DRY RUN]" if dry_run else "")
            )
            patches.append({"id": row_id, "tcs_floor": floor})

        if patches and not dry_run:
            n_updated, failed_ids = _batch_update(patches)
            stats["updated"] += n_updated
            if failed_ids:
                # Quarantine persistently failing rows so the loop doesn't churn
                # on them indefinitely.
                skipped_ids.extend(failed_ids)
                print(
                    f"  Updated {n_updated}/{len(patches)} rows; "
                    f"{len(failed_ids)} update failure(s) quarantined."
                )
            else:
                print(f"  Updated {n_updated}/{len(patches)} rows.")
        elif patches and dry_run:
            stats["would_update"] += len(patches)
            print(f"  [dry-run] Would update {len(patches)} rows.")

        if dry_run:
            print("\n  [dry-run] Stopping after first page to avoid infinite loop.")
            break

        # Progress guard: if every row in this page failed to update it means
        # the entire page was quarantined without any net progress.  Break to
        # avoid an infinite loop (all rows remain IS NULL but are in skipped_ids).
        if patches and stats["updated"] == 0 and len(skipped_ids) >= len(rows):
            print(
                "\n  WARNING: No net progress — all rows in this page failed to update.\n"
                "  Check DB permissions and retry.  Stopping to avoid an infinite loop."
            )
            break

        # Rows updated this iteration drop out of the IS NULL filter on the next
        # query.  Quarantined rows are excluded via skipped_ids.
        if len(rows) < PAGE_SZ:
            break   # fewer rows than the page limit — we're done


# ── Verification helper ───────────────────────────────────────────────────────

def verify() -> None:
    """Print the residual NULL count after the backfill."""
    if not backend.supabase:
        return
    try:
        resp = (
            backend.supabase.table("paper_trades")
            .select("id", count="exact")
            .is_("tcs_floor", "null")
            .execute()
        )
        remaining = resp.count if resp.count is not None else len(resp.data or [])
        print(f"\nVerification: {remaining} paper_trades row(s) still have tcs_floor IS NULL.")
        if remaining == 0:
            print("  PASS — all rows now have tcs_floor populated.")
        else:
            print("  Some rows remain NULL (may lack a predicted label).")
            print("  Re-run with --all-rows to also backfill rows without a predicted label.")
    except Exception as e:
        print(f"Verification query failed: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill tcs_floor for existing paper_trades rows."
    )
    parser.add_argument(
        "user_ids",
        nargs="*",
        help="Optional user_id(s) to restrict the backfill (default: all users).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview computed floors without writing to the database.",
    )
    parser.add_argument(
        "--all-rows",
        action="store_true",
        help=(
            "Also process rows where predicted IS NULL "
            "(floor = max(MIN_TCS, min_tcs_filter))."
        ),
    )
    args = parser.parse_args()

    print("=" * 60)
    print("run_tcs_floor_backfill.py")
    print("=" * 60)
    if args.dry_run:
        print("DRY-RUN MODE — no changes will be written to the database.\n")

    stats = backfill(
        user_ids=args.user_ids,
        dry_run=args.dry_run,
        all_rows=args.all_rows,
    )

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Rows fetched (qualifying NULL rows) : {stats['fetched']}")
    if args.dry_run:
        print(f"  Rows that would be updated [DRY RUN]: {stats['would_update']}")
    else:
        print(f"  Rows updated                        : {stats['updated']}")
    print(f"  Rows skipped (no predicted label)   : {stats['skipped_no_pred']}")
    print(f"  Errors                              : {stats['errors']}")

    if not args.dry_run:
        verify()

    print("\nDone.")


if __name__ == "__main__":
    main()
