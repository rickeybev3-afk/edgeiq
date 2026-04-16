"""
run_tiered_pnl_backfill.py
───────────────────────────
Backfills tiered_pnl_r (50/25/25 ladder P&L) and eod_pnl_r on existing
paper_trades rows that were logged before the tiered-exit bot was deployed.

Target rows
───────────
  paper_trades WHERE
      actual_outcome IN ('Bullish Break', 'Bearish Break')
      AND tiered_pnl_r IS NULL
      AND close_price   IS NOT NULL
      AND ib_high       IS NOT NULL
      AND ib_low        IS NOT NULL

For each qualifying row the script:
  1. Fetches 1-minute bars from Alpaca for the trade_date / ticker.
  2. Slices to post-IB bars (> 10:30:59 ET) — same window as the live bot.
  3. Calls compute_trade_sim_tiered() with the post-IB bars + close_price.
  4. Writes tiered_pnl_r (and eod_pnl_r if still NULL) back to the DB.

Pagination strategy
───────────────────
The query filters on tiered_pnl_r IS NULL.  As rows are updated their
tiered_pnl_r becomes non-NULL, so the script always queries from offset=0 —
updated rows fall out of the result set and new qualifying rows surface.
Rows that cannot be processed (no Alpaca bars, unparseable date) are tracked in
a local skipped_ids set and excluded from subsequent fetches so the loop always
terminates.

Rate limiting
─────────────
Alpaca's market data API allows ~200 requests/minute on the free tier.
The script sleeps ALPACA_SLEEP_S between bar fetches (default 0.35 s ≈ 171 req/
min) to stay comfortably under the limit.  Use --no-ratelimit to disable (safe
on paid Alpaca subscriptions).

Usage
─────
  python run_tiered_pnl_backfill.py                          # all users
  python run_tiered_pnl_backfill.py <uid1> [uid2] ...        # specific users
  python run_tiered_pnl_backfill.py --no-ratelimit           # skip sleep
  python run_tiered_pnl_backfill.py --dry-run                # preview only
"""

import sys
import os
import time
import argparse
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend

# ── Configuration ─────────────────────────────────────────────────────────────

PAGE_SZ        = 100    # rows per Supabase fetch
ALPACA_SLEEP_S = 0.35   # seconds between Alpaca bar fetches (~171 req/min)
MAX_ERRORS     = 10     # stop a user after this many consecutive Alpaca failures


# ── User discovery ─────────────────────────────────────────────────────────────

def discover_user_ids() -> list[str]:
    """Return distinct user_ids that have at least one qualifying NULL row."""
    if not backend.supabase:
        return []

    uid_set: set[str] = set()
    offset = 0
    rows_scanned = 0
    print("  Scanning paper_trades for qualifying user IDs…", end="", flush=True)
    try:
        while True:
            resp = (
                backend.supabase.table("paper_trades")
                .select("user_id")
                .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
                .is_("tiered_pnl_r", "null")
                .not_.is_("close_price", "null")
                .not_.is_("ib_high", "null")
                .not_.is_("ib_low", "null")
                .range(offset, offset + 999)
                .execute()
            )
            rows = resp.data or []
            for row in rows:
                uid = row.get("user_id")
                if uid:
                    uid_set.add(uid)
            rows_scanned += len(rows)
            if len(rows) < 1000:
                break
            offset += 1000
    except Exception as e:
        print(f"\n  ERROR scanning for user IDs: {e}")
        print("  Pass user IDs explicitly as CLI args to bypass discovery.")
        sys.exit(1)

    print(f" {rows_scanned} rows scanned.")
    return sorted(uid_set)


# ── Bar helpers ────────────────────────────────────────────────────────────────

def _fetch_bars_safe(ticker: str, trade_date: date):
    """Fetch 1-min session bars from Alpaca.  Returns None on error."""
    try:
        df = backend.fetch_bars(
            backend.ALPACA_API_KEY,
            backend.ALPACA_SECRET_KEY,
            ticker,
            trade_date,
        )
        return df if (df is not None and len(df) > 0) else None
    except Exception:
        return None


def _post_ib_bars(full_df, trade_date: date):
    """Slice full-session bars to the post-IB window (> 10:30:59 ET).

    Matches the cutoff used by the live paper-trade bot (backend.py ~line 7540).
    Returning None tells compute_trade_sim_tiered that no post-IB bars are
    available; it will still compute eod_pnl_r from close_px.
    """
    if full_df is None or len(full_df) == 0:
        return None
    try:
        tz = full_df.index.tz
        cutoff_naive = datetime(
            trade_date.year, trade_date.month, trade_date.day,
            10, 30, 59,
        )
        if tz is not None:
            try:
                import pytz
                eastern = pytz.timezone("US/Eastern")
                cutoff = eastern.localize(cutoff_naive)
            except Exception:
                import pandas as pd
                cutoff = pd.Timestamp(cutoff_naive).tz_localize(tz)
        else:
            cutoff = cutoff_naive

        sliced = full_df[full_df.index > cutoff]
        return sliced if not sliced.empty else None
    except Exception:
        return full_df if len(full_df) > 0 else None


# ── Core backfill ──────────────────────────────────────────────────────────────

def backfill_user(user_id: str, dry_run: bool, rate_limit: bool) -> dict:
    """Backfill tiered_pnl_r for all qualifying paper_trades rows for one user.

    Pagination: always queries from offset=0 with tiered_pnl_r IS NULL.  Updated
    rows drop out of subsequent fetches naturally.  Rows that cannot be processed
    (no Alpaca bars, bad date) are added to skipped_ids and excluded via a NOT IN
    filter so the outer loop always terminates.
    """
    stats = {
        "fetched": 0,
        "updated": 0,
        "skipped_no_bars": 0,
        "skipped_no_tiered": 0,
        "errors": 0,
    }

    if not backend.supabase:
        print("  No Supabase connection.")
        return stats

    if not backend.ALPACA_API_KEY or not backend.ALPACA_SECRET_KEY:
        print("  ERROR: ALPACA_API_KEY / ALPACA_SECRET_KEY not set — cannot fetch bars.")
        return stats

    skipped_ids: list = []          # IDs we've tried but cannot process (no bars etc.)
    consecutive_errors = 0
    iteration = 0

    while True:
        iteration += 1

        try:
            q = (
                backend.supabase.table("paper_trades")
                .select("id,ticker,trade_date,actual_outcome,ib_high,ib_low,close_price,eod_pnl_r")
                .eq("user_id", user_id)
                .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
                .is_("tiered_pnl_r", "null")
                .not_.is_("close_price", "null")
                .not_.is_("ib_high", "null")
                .not_.is_("ib_low", "null")
            )
            # Exclude IDs we've already tried and couldn't handle
            if skipped_ids:
                q = q.not_.in_("id", skipped_ids)
            resp = q.limit(PAGE_SZ).execute()
        except Exception as e:
            print(f"  Fetch error (iteration {iteration}): {e}")
            stats["errors"] += 1
            break

        rows = resp.data or []
        if not rows:
            break  # no more qualifying rows — done

        stats["fetched"] += len(rows)
        print(f"\n  Iteration {iteration}: {len(rows)} qualifying rows")

        for row in rows:
            row_id         = row["id"]
            ticker         = row.get("ticker", "")
            trade_date_raw = row.get("trade_date")
            direction      = row.get("actual_outcome", "")
            ib_high        = row.get("ib_high")
            ib_low         = row.get("ib_low")
            close_price    = row.get("close_price")
            existing_eod   = row.get("eod_pnl_r")

            # ── Parse trade_date ──────────────────────────────────────────────
            try:
                if isinstance(trade_date_raw, str):
                    trade_date = date.fromisoformat(trade_date_raw[:10])
                elif isinstance(trade_date_raw, date):
                    trade_date = trade_date_raw
                else:
                    raise ValueError(f"unexpected type {type(trade_date_raw)}")
            except Exception:
                print(f"    [{ticker}] bad trade_date={trade_date_raw!r} — skipping permanently")
                stats["errors"] += 1
                skipped_ids.append(row_id)
                continue

            print(f"    [{ticker}] {trade_date} {direction}  ", end="", flush=True)

            # ── Fetch Alpaca bars ─────────────────────────────────────────────
            full_df = _fetch_bars_safe(ticker, trade_date)
            if rate_limit:
                time.sleep(ALPACA_SLEEP_S)

            if full_df is None:
                print("no bars — eod_pnl_r only, row deferred")
                consecutive_errors += 1
                if consecutive_errors >= MAX_ERRORS:
                    print(f"\n  {MAX_ERRORS} consecutive bar-fetch failures.")
                    print("  Stopping this user — check Alpaca credentials and retry.")
                    return stats
                # Still write eod_pnl_r if it's NULL (bars-free formula).
                # tiered_pnl_r stays NULL (honest: no bar data for the ladder sim).
                # Row is added to skipped_ids so the loop doesn't revisit it.
                eod_only = backend.compute_trade_sim_tiered(
                    aft_df    = None,
                    ib_high   = ib_high,
                    ib_low    = ib_low,
                    direction = direction,
                    close_px  = close_price,
                )
                if not dry_run and existing_eod is None and eod_only.get("eod_pnl_r") is not None:
                    try:
                        backend.supabase.table("paper_trades").update(
                            {"eod_pnl_r": eod_only["eod_pnl_r"]}
                        ).eq("id", row_id).execute()
                    except Exception as e:
                        print(f"\n    eod_pnl_r update error: {e}")
                stats["skipped_no_bars"] += 1
                skipped_ids.append(row_id)  # don't revisit — tiered stays NULL
                continue

            consecutive_errors = 0

            # ── Slice to post-IB bars (> 10:30:59 ET) ────────────────────────
            aft_df = _post_ib_bars(full_df, trade_date)

            # ── Compute tiered P&L ────────────────────────────────────────────
            result = backend.compute_trade_sim_tiered(
                aft_df    = aft_df,
                ib_high   = ib_high,
                ib_low    = ib_low,
                direction = direction,
                close_px  = close_price,
            )

            tiered_pnl_r = result.get("tiered_pnl_r")
            eod_pnl_r    = result.get("eod_pnl_r")

            if tiered_pnl_r is None:
                # Price never crossed the entry level in the post-IB window.
                # The live write-path also leaves tiered_pnl_r NULL in this
                # case — we match that behaviour to avoid inconsistency.
                # eod_pnl_r is still valid; write it if missing, then add this
                # row to skipped_ids so the outer loop doesn't revisit it.
                print("no entry cross — tiered stays NULL (matches live path)")
                stats["skipped_no_tiered"] += 1
                if not dry_run and existing_eod is None and eod_pnl_r is not None:
                    try:
                        backend.supabase.table("paper_trades").update(
                            {"eod_pnl_r": eod_pnl_r}
                        ).eq("id", row_id).execute()
                    except Exception as e:
                        print(f"\n    eod_pnl_r update error: {e}")
                skipped_ids.append(row_id)
                continue
            else:
                print(f"tiered={tiered_pnl_r:+.4f}R  eod={eod_pnl_r:+.4f}R"
                      + (" [DRY RUN]" if dry_run else ""))
                patch = {"tiered_pnl_r": tiered_pnl_r}

            if existing_eod is None and eod_pnl_r is not None:
                patch["eod_pnl_r"] = eod_pnl_r

            if not dry_run:
                try:
                    backend.supabase.table("paper_trades").update(patch).eq("id", row_id).execute()
                    stats["updated"] += 1
                except Exception as e:
                    print(f"\n    DB update error for id={row_id}: {e}")
                    stats["errors"] += 1
                    skipped_ids.append(row_id)
            else:
                stats["updated"] += 1

        # In dry-run mode rows are never updated, so the loop would be infinite.
        # Stop after the first page so the user sees a representative preview.
        if dry_run:
            print("\n  [dry-run] stopping after one page to avoid infinite loop.")
            break

    return stats


# ── Post-run verification ──────────────────────────────────────────────────────

def verify_residual_nulls(user_id: str) -> int:
    """Count paper_trades rows that still have tiered_pnl_r IS NULL after the run.

    A non-zero count indicates rows that had no Alpaca bars available and could
    not be backfilled.  These rows will show "no data" in the 50/25/25 Ladder tab.
    """
    if not backend.supabase:
        return -1
    try:
        resp = (
            backend.supabase.table("paper_trades")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
            .is_("tiered_pnl_r", "null")
            .not_.is_("close_price", "null")
            .not_.is_("ib_high", "null")
            .not_.is_("ib_low", "null")
            .execute()
        )
        return resp.count if resp.count is not None else len(resp.data or [])
    except Exception as e:
        print(f"  Verification query error: {e}")
        return -1


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Backfill 50/25/25 ladder tiered_pnl_r on historical paper_trades"
    )
    parser.add_argument(
        "user_ids",
        nargs="*",
        metavar="UID",
        help="Optional user IDs to process (default: auto-discover all).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print results without writing to the database.",
    )
    parser.add_argument(
        "--no-ratelimit",
        action="store_true",
        help="Skip the inter-request sleep (safe on paid Alpaca data plans).",
    )
    args = parser.parse_args()

    dry_run    = args.dry_run
    rate_limit = not args.no_ratelimit

    print("EdgeIQ — Tiered P&L Backfill (50/25/25 ladder)")
    print("=" * 60)
    if dry_run:
        print("  *** DRY RUN — no database writes ***")
    if not rate_limit:
        print("  Rate limiting disabled.")
    print()

    if args.user_ids:
        user_ids = list(dict.fromkeys(args.user_ids))
        print(f"Using {len(user_ids)} user ID(s) from command-line arguments.")
    else:
        print("No user IDs specified — querying database for qualifying users…")
        user_ids = discover_user_ids()
        if not user_ids:
            print("No qualifying rows found (tiered_pnl_r already populated or no data).")
            sys.exit(0)
        print(f"Found {len(user_ids)} user(s) with NULL tiered_pnl_r rows: {user_ids}")

    t0 = time.time()
    grand = {"fetched": 0, "updated": 0, "skipped_no_bars": 0, "skipped_no_tiered": 0, "errors": 0}

    for uid in user_ids:
        print(f"\n{'#'*60}")
        print(f"  User: {uid}")
        print(f"{'#'*60}")
        stats = backfill_user(uid, dry_run=dry_run, rate_limit=rate_limit)
        for k in grand:
            grand[k] += stats[k]

        print(f"\n  --- User {uid} summary ---")
        print(f"  Rows processed          : {stats['fetched']}")
        print(f"  Rows updated            : {stats['updated']}"
              + (" (dry run)" if dry_run else ""))
        print(f"  Skipped (no Alpaca bars): {stats['skipped_no_bars']}"
              + "  [tiered_pnl_r stays NULL — bars unavailable for these dates]"
              if stats["skipped_no_bars"] else f"  Skipped (no Alpaca bars): {stats['skipped_no_bars']}")
        print(f"  Flat (no entry cross)   : {stats['skipped_no_tiered']}"
              + "  [tiered_pnl_r=0.0 — price never broke entry in post-IB window]"
              if stats["skipped_no_tiered"] else f"  Flat (no entry cross)   : {stats['skipped_no_tiered']}")
        print(f"  Errors                  : {stats['errors']}")

        if not dry_run:
            residual = verify_residual_nulls(uid)
            if residual < 0:
                print("  Verification           : unable to query (check manually)")
            elif residual == 0:
                print("  Verification           : PASS — 0 qualifying NULL rows remain")
            else:
                print(f"  Verification           : {residual} NULL rows remain (no Alpaca bars)")
                print("  These rows cannot be backfilled — bars are unavailable for those dates.")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  GRAND TOTAL across {len(user_ids)} user(s)")
    print(f"  Rows processed          : {grand['fetched']}")
    print(f"  Rows updated            : {grand['updated']}"
          + (" (dry run)" if dry_run else ""))
    print(f"  Skipped (no Alpaca bars): {grand['skipped_no_bars']}")
    print(f"  Flat (no entry cross)   : {grand['skipped_no_tiered']}")
    print(f"  Errors                  : {grand['errors']}")
    print(f"  Elapsed                 : {elapsed:.0f}s")
    if dry_run:
        print("\n  *** DRY RUN complete — re-run without --dry-run to write to DB ***")
    else:
        print("\n  Backfill complete.")


if __name__ == "__main__":
    main()
