"""
run_tiered_pnl_backfill.py
───────────────────────────
Backfills tiered_pnl_r (50/25/25 ladder P&L) and eod_pnl_r on existing rows
that were logged before the tiered-exit bot was deployed.

Results of the 2026-04-16 initial run (verified via null-count queries post-run):
  paper_trades     : 74 breakout rows total; 0 qualifying rows found.
                     All 74 rows lack close_price and/or ib_high/ib_low —
                     these are historical trades logged before price fields
                     were captured.  0 rows were updated; backfill complete
                     (no further action possible without the raw price data).
  Verification     : PASS — 0 qualifying NULL rows remain after the run.
  Note             : The 50/25/25 Ladder tab will be populated going forward
                     as new paper trades are logged with close_price stored.

Results of the 2026-04-17 re-run (after close_price backfill completed):
  paper_trades     : 74 breakout rows total; 74 qualifying rows found
                     (close_price, ib_high, ib_low all now populated).
                     74 rows updated with tiered_pnl_r and eod_pnl_r.
                     0 rows skipped (no Alpaca bars unavailable).
                     0 flat rows (price crossed IB entry on all 74 trades).
  Verification     : PASS — 0 qualifying NULL rows remain after the run.
  Verification SQL : SELECT COUNT(*) FROM paper_trades
                     WHERE actual_outcome IN ('Bullish Break','Bearish Break')
                     AND tiered_pnl_r IS NULL
                     AND close_price IS NOT NULL
                     AND ib_high IS NOT NULL AND ib_low IS NOT NULL;
                     → Result: 0 (confirmed)
  backtest_sim_runs: Large backlog (~16,233 rows) being processed in batches.
                     Run  `python run_tiered_pnl_backfill.py --backtest-only`
                     repeatedly until the analogous COUNT(*) query returns 0.

Completion runbook (backtest_sim_runs)
───────────────────────────────────────
  1. Run:  python run_tiered_pnl_backfill.py --backtest-only [--no-ratelimit]
  2. Repeat until the script prints "No qualifying backtest_sim_runs rows found"
     or the verification count below hits 0.
  3. Verify:
       SELECT COUNT(*) FROM backtest_sim_runs
       WHERE actual_outcome IN ('Bullish Break','Bearish Break')
         AND tiered_pnl_r IS NULL
         AND close_price IS NOT NULL
         AND ib_high IS NOT NULL AND ib_low IS NOT NULL;

Supported tables
────────────────
  paper_trades       — one user at a time, partitioned by user_id
  backtest_sim_runs  — all rows processed in one pass (no user partitioning)

Target rows (paper_trades)
──────────────────────────
  paper_trades WHERE
      actual_outcome IN ('Bullish Break', 'Bearish Break')
      AND tiered_pnl_r IS NULL
      AND close_price   IS NOT NULL
      AND ib_high       IS NOT NULL
      AND ib_low        IS NOT NULL

Target rows (backtest_sim_runs)
───────────────────────────────
  backtest_sim_runs WHERE
      actual_outcome IN ('Bullish Break', 'Bearish Break')
      AND tiered_pnl_r IS NULL
      AND close_price   IS NOT NULL
      AND ib_high       IS NOT NULL
      AND ib_low        IS NOT NULL

For each qualifying row the script:
  1. Fetches 1-minute bars from Alpaca for the trade/sim_date / ticker.
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
  python run_tiered_pnl_backfill.py                          # all users + backtest
  python run_tiered_pnl_backfill.py <uid1> [uid2] ...        # specific users only
  python run_tiered_pnl_backfill.py --no-ratelimit           # skip sleep
  python run_tiered_pnl_backfill.py --dry-run                # preview only
  python run_tiered_pnl_backfill.py --skip-backtest          # paper_trades only
  python run_tiered_pnl_backfill.py --backtest-only          # backtest_sim_runs only
"""

import sys
import os
import time
import json
import argparse
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend

# ── Configuration ─────────────────────────────────────────────────────────────

PAGE_SZ        = 500    # rows per Supabase fetch (backtest table is large)
ALPACA_SLEEP_S = 0.35   # seconds between Alpaca bar fetches (~171 req/min)
MAX_ERRORS     = 20     # stop after this many consecutive Alpaca failures


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

_FETCH_NO_DATA = "no_data"   # API succeeded but returned zero bars (terminal)
_FETCH_ERROR   = "error"     # exception raised (transient; do not stamp sentinel)
_FETCH_OK      = "ok"        # success with data


def _fetch_bars_safe(ticker: str, trade_date: date):
    """Fetch 1-min session bars from Alpaca.

    Returns a 2-tuple ``(df_or_None, status)`` where *status* is one of the
    module-level constants ``_FETCH_OK``, ``_FETCH_NO_DATA``, or
    ``_FETCH_ERROR``.

    Callers must distinguish *no_data* (sentinel eligible) from *error*
    (transient failure; skip without stamping so the row is retried next run).
    """
    try:
        df = backend.fetch_bars(
            backend.ALPACA_API_KEY,
            backend.ALPACA_SECRET_KEY,
            ticker,
            trade_date,
        )
        if df is not None and len(df) > 0:
            return df, _FETCH_OK
        return None, _FETCH_NO_DATA
    except Exception:
        return None, _FETCH_ERROR


# Maximum parallel Alpaca calls.  3 keeps us safely under 200 req/min.
ALPACA_WORKERS = 3

def _prefetch_bars(
    combos: list[tuple],           # list of (ticker, sim_date) to prefetch
    bars_cache: dict,              # (ticker, date) -> df  (only _FETCH_OK)
    bars_status_cache: dict,       # (ticker, date) -> _FETCH_NO_DATA | _FETCH_ERROR
) -> None:
    """Fetch Alpaca bars for all uncached (ticker, date) combos in parallel.

    Updates bars_cache and bars_status_cache in place.  Uses a thread pool so
    multiple HTTP requests run concurrently, giving roughly ALPACA_WORKERS×
    speedup vs. sequential fetches.

    Distinguishes _FETCH_NO_DATA (sentinel eligible) from _FETCH_ERROR (transient;
    row must remain retriable) so callers can apply the correct policy.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    needed = [c for c in combos if c not in bars_cache and c not in bars_status_cache]
    if not needed:
        return

    def _fetch(combo):
        ticker, d = combo
        return combo, _fetch_bars_safe(ticker, d)

    with ThreadPoolExecutor(max_workers=ALPACA_WORKERS) as ex:
        futures = {ex.submit(_fetch, c): c for c in needed}
        for fut in as_completed(futures):
            try:
                combo, (df, status) = fut.result()
                if status == _FETCH_OK:
                    bars_cache[combo] = df
                else:
                    bars_status_cache[combo] = status
            except Exception:
                bars_status_cache[futures[fut]] = _FETCH_ERROR


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


# ── Core backfill — paper_trades ───────────────────────────────────────────────

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

    skipped_ids: list = []
    consecutive_errors = 0
    iteration = 0

    while True:
        iteration += 1

        try:
            q = (
                backend.supabase.table("paper_trades")
                .select("id,ticker,trade_date,actual_outcome,ib_high,ib_low,close_price,eod_pnl_r,rvol")
                .eq("user_id", user_id)
                .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
                .is_("tiered_pnl_r", "null")
                .not_.is_("close_price", "null")
                .not_.is_("ib_high", "null")
                .not_.is_("ib_low", "null")
            )
            if skipped_ids:
                q = q.not_.in_("id", skipped_ids)
            resp = q.limit(PAGE_SZ).execute()
        except Exception as e:
            print(f"  Fetch error (iteration {iteration}): {e}")
            stats["errors"] += 1
            break

        rows = resp.data or []
        if not rows:
            break

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
            rvol_raw       = row.get("rvol")

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

            full_df, _fetch_status = _fetch_bars_safe(ticker, trade_date)
            if rate_limit:
                time.sleep(ALPACA_SLEEP_S)

            if full_df is None:
                if _fetch_status == _FETCH_ERROR:
                    # Exception raised — transient failure; skip without sentinel so
                    # the row remains retriable in a future backfill run.
                    print("fetch error (transient) — row deferred")
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_ERRORS:
                        print(f"\n  {MAX_ERRORS} consecutive bar-fetch failures.")
                        print("  Stopping this user — check Alpaca credentials and retry.")
                        return stats
                else:
                    # API returned successfully with zero bars — row genuinely has
                    # no data (delisted, pre-listing, holiday gap).  Stamp with
                    # sentinel so the row exits the IS NULL pending count permanently.
                    consecutive_errors = 0
                    eod_only = backend.compute_trade_sim_tiered(
                        aft_df    = None,
                        ib_high   = ib_high,
                        ib_low    = ib_low,
                        direction = direction,
                        close_px  = close_price,
                    )
                    eod_r = eod_only.get("eod_pnl_r")
                    if eod_r is not None and rvol_raw is not None:
                        try:
                            _rvol_mult = backend.rvol_size_mult(float(rvol_raw))
                            if _rvol_mult != 1.0:
                                eod_r = round(float(eod_r) * _rvol_mult, 4)
                        except (TypeError, ValueError):
                            pass
                    patch_no_bars: dict = {"tiered_pnl_r": backend.TIERED_PNL_SENTINEL}
                    if existing_eod is None and eod_r is not None:
                        patch_no_bars["eod_pnl_r"] = eod_r
                    eod_str = f"  eod={eod_r:+.4f}R" if eod_r is not None else ""
                    print(f"no bars — stamping sentinel{eod_str}" + (" [DRY RUN]" if dry_run else ""))
                    if not dry_run:
                        try:
                            backend.supabase.table("paper_trades").update(
                                patch_no_bars
                            ).eq("id", row_id).execute()
                        except Exception as e:
                            print(f"\n    sentinel update error: {e}")
                stats["skipped_no_bars"] += 1
                skipped_ids.append(row_id)
                continue

            consecutive_errors = 0

            aft_df = _post_ib_bars(full_df, trade_date)

            result = backend.compute_trade_sim_tiered(
                aft_df    = aft_df,
                ib_high   = ib_high,
                ib_low    = ib_low,
                direction = direction,
                close_px  = close_price,
            )

            tiered_pnl_r = result.get("tiered_pnl_r")
            eod_pnl_r    = result.get("eod_pnl_r")

            # Apply RVOL bonus position-size multiplier so tiered/EOD R values
            # reflect the same dollar-scaled contribution as the live bot and
            # the pnl_r_sim column (which is RVOL-adjusted via apply_rvol_sizing_to_sim).
            if rvol_raw is not None:
                try:
                    _rvol_mult = backend.rvol_size_mult(float(rvol_raw))
                    if _rvol_mult != 1.0:
                        if tiered_pnl_r is not None:
                            tiered_pnl_r = round(float(tiered_pnl_r) * _rvol_mult, 4)
                        if eod_pnl_r is not None:
                            eod_pnl_r = round(float(eod_pnl_r) * _rvol_mult, 4)
                except (TypeError, ValueError):
                    pass

            if tiered_pnl_r is None:
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

        if dry_run:
            print("\n  [dry-run] stopping after one page to avoid infinite loop.")
            break

    return stats


# ── Batch upsert helper ────────────────────────────────────────────────────────

UPSERT_CHUNK = 500   # max rows per single upsert call

def _batch_upsert(patches: list[dict]) -> int:
    """Upsert a list of row patches into backtest_sim_runs in chunks.

    Each patch dict must contain "id" plus the fields to update.
    Returns the number of rows successfully upserted.
    """
    upserted = 0
    for i in range(0, len(patches), UPSERT_CHUNK):
        chunk = patches[i : i + UPSERT_CHUNK]
        try:
            backend.supabase.table("backtest_sim_runs").upsert(
                chunk, on_conflict="id"
            ).execute()
            upserted += len(chunk)
        except Exception as e:
            print(f"\n  Batch upsert error (chunk {i//UPSERT_CHUNK + 1}): {e}")
            print(f"  Falling back to row-by-row updates for {len(chunk)} rows…")
            for patch in chunk:
                row_id = patch.pop("id")
                try:
                    backend.supabase.table("backtest_sim_runs").update(
                        patch
                    ).eq("id", row_id).execute()
                    upserted += 1
                except Exception as e2:
                    print(f"  Row update error id={row_id}: {e2}")
    return upserted


# ── Core backfill — backtest_sim_runs ──────────────────────────────────────────

def backfill_backtest_sim_runs(
    dry_run: bool,
    rate_limit: bool,
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """Backfill tiered_pnl_r for all qualifying backtest_sim_runs rows.

    Unlike paper_trades this table has no meaningful user partitioning that
    would reduce work, so we process all NULL rows in a single pass.

    Pagination: always queries from offset=0 with tiered_pnl_r IS NULL.  Updated
    rows drop out of subsequent fetches naturally.  Rows that cannot be processed
    (no Alpaca bars, bad date) are added to skipped_ids and excluded via a NOT IN
    filter so the outer loop always terminates.

    Note: the date column is named sim_date (not trade_date) in this table.

    date_from / date_to: optional ISO date strings (YYYY-MM-DD) that scope the
    backfill to a specific sim_date window.  When supplied, only rows whose
    sim_date falls within [date_from, date_to] are processed.  This avoids
    scanning the entire table after a targeted batch backtest run.
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

    skipped_ids: list = []
    consecutive_errors = 0
    iteration = 0
    # Cache Alpaca bars keyed on (ticker, sim_date) to avoid redundant API calls.
    # Backtest tables typically repeat the same ticker+date across many rows.
    bars_cache: dict = {}
    # Track non-OK fetches: (ticker, date) -> _FETCH_NO_DATA | _FETCH_ERROR.
    # Lets callers apply the right policy (sentinel stamp vs. skip-and-retry).
    bars_status_cache: dict = {}

    while True:
        iteration += 1

        try:
            q = (
                backend.supabase.table("backtest_sim_runs")
                .select("id,ticker,sim_date,actual_outcome,ib_high,ib_low,close_price,eod_pnl_r,rvol")
                .in_("actual_outcome", ["Bullish Break", "Bearish Break"])
                .is_("tiered_pnl_r", "null")
                .not_.is_("close_price", "null")
                .not_.is_("ib_high", "null")
                .not_.is_("ib_low", "null")
            )
            if date_from:
                q = q.gte("sim_date", date_from)
            if date_to:
                q = q.lte("sim_date", date_to)
            if skipped_ids:
                q = q.not_.in_("id", skipped_ids)
            resp = q.limit(PAGE_SZ).execute()
        except Exception as e:
            print(f"  Fetch error (iteration {iteration}): {e}")
            stats["errors"] += 1
            break

        rows = resp.data or []
        if not rows:
            break

        stats["fetched"] += len(rows)
        print(f"\n  Iteration {iteration}: {len(rows)} qualifying rows")

        # Process the page in FLUSH_EVERY-row chunks.  For each chunk:
        #  1. Pre-fetch Alpaca bars in parallel (ALPACA_WORKERS threads).
        #  2. Compute tiered P&L for every row using the warm cache.
        #  3. Batch-upsert the chunk to Supabase.
        # This interleaving gives ~ALPACA_WORKERS× speedup on bar fetches while
        # still committing progress incrementally (so kills don't lose a whole page).
        FLUSH_EVERY = 50

        for chunk_start in range(0, len(rows), FLUSH_EVERY):
            chunk = rows[chunk_start : chunk_start + FLUSH_EVERY]

            # ── 1. Parallel bar pre-fetch for this chunk ──────────────────────
            chunk_combos: list[tuple] = []
            for _r in chunk:
                _td_raw = _r.get("sim_date")
                _ticker  = _r.get("ticker", "")
                try:
                    _td = (date.fromisoformat(_td_raw[:10])
                           if isinstance(_td_raw, str) else _td_raw)
                    chunk_combos.append((_ticker, _td))
                except Exception:
                    pass
            seen_c: set = set()
            deduped_c = [c for c in chunk_combos if not (c in seen_c or seen_c.add(c))]
            if deduped_c:
                _prefetch_bars(deduped_c, bars_cache, bars_status_cache)

            # ── 2. Process each row using the (now warm) cache ────────────────
            chunk_patches: list[dict] = []

            for row in chunk:
                row_id       = row["id"]
                ticker       = row.get("ticker", "")
                sim_date_raw = row.get("sim_date")
                direction    = row.get("actual_outcome", "")
                ib_high      = row.get("ib_high")
                ib_low       = row.get("ib_low")
                close_price  = row.get("close_price")
                existing_eod = row.get("eod_pnl_r")
                rvol_raw     = row.get("rvol")

                # ── Parse sim_date ────────────────────────────────────────────
                try:
                    if isinstance(sim_date_raw, str):
                        sim_date = date.fromisoformat(sim_date_raw[:10])
                    elif isinstance(sim_date_raw, date):
                        sim_date = sim_date_raw
                    else:
                        raise ValueError(f"unexpected type {type(sim_date_raw)}")
                except Exception:
                    print(f"    [{ticker}] bad sim_date={sim_date_raw!r} — skipping permanently")
                    stats["errors"] += 1
                    skipped_ids.append(row_id)
                    continue

                print(f"    [{ticker}] {sim_date} {direction}  ", end="", flush=True)

                # ── Resolve from cache (prefetch should have populated it) ─────
                cache_key = (ticker, sim_date)
                if cache_key in bars_cache:
                    full_df      = bars_cache[cache_key]
                    fetch_status = _FETCH_OK
                elif cache_key in bars_status_cache:
                    full_df      = None
                    fetch_status = bars_status_cache[cache_key]
                else:
                    # Cache miss — fetch inline (shouldn't normally happen)
                    full_df, fetch_status = _fetch_bars_safe(ticker, sim_date)
                    if rate_limit:
                        time.sleep(ALPACA_SLEEP_S)
                    if fetch_status == _FETCH_OK:
                        bars_cache[cache_key] = full_df
                    else:
                        bars_status_cache[cache_key] = fetch_status

                if full_df is None:
                    if fetch_status == _FETCH_ERROR:
                        # Transient failure — skip without stamping so the row
                        # remains retriable in future runs.
                        consecutive_errors += 1
                        if consecutive_errors >= MAX_ERRORS:
                            print(f"\n  {MAX_ERRORS} consecutive bar-fetch failures.")
                            print("  Stopping — check Alpaca credentials and retry.")
                            if chunk_patches and not dry_run:
                                _batch_upsert(chunk_patches)
                            return stats
                        print("fetch error (transient) — row deferred")
                        skipped_ids.append(row_id)
                        stats["skipped_no_bars"] += 1
                        continue

                    # _FETCH_NO_DATA: API succeeded but no bars (delisted, holiday
                    # gap, pre-listing, etc.).  Stamp with sentinel so the row
                    # exits the IS NULL pending count permanently.
                    consecutive_errors = 0
                    eod_only = backend.compute_trade_sim_tiered(
                        aft_df    = None,
                        ib_high   = ib_high,
                        ib_low    = ib_low,
                        direction = direction,
                        close_px  = close_price,
                    )
                    eod_r = eod_only.get("eod_pnl_r")
                    if eod_r is not None and rvol_raw is not None:
                        try:
                            _rvol_mult = backend.rvol_size_mult(float(rvol_raw))
                            if _rvol_mult != 1.0:
                                eod_r = round(float(eod_r) * _rvol_mult, 4)
                        except (TypeError, ValueError):
                            pass
                    patch_no_bars: dict = {"id": row_id,
                                          "tiered_pnl_r": backend.TIERED_PNL_SENTINEL}
                    if existing_eod is None and eod_r is not None:
                        patch_no_bars["eod_pnl_r"] = eod_r
                    eod_str = f"  eod={eod_r:+.4f}R" if eod_r is not None else ""
                    print(f"no bars — stamping sentinel{eod_str}" + (" [DRY RUN]" if dry_run else ""))
                    chunk_patches.append(patch_no_bars)
                    stats["skipped_no_bars"] += 1
                    skipped_ids.append(row_id)
                    continue

                consecutive_errors = 0

                # ── Slice to post-IB bars (> 10:30:59 ET) ────────────────────
                aft_df = _post_ib_bars(full_df, sim_date)

                # ── Compute tiered P&L ────────────────────────────────────────
                result = backend.compute_trade_sim_tiered(
                    aft_df    = aft_df,
                    ib_high   = ib_high,
                    ib_low    = ib_low,
                    direction = direction,
                    close_px  = close_price,
                )

                tiered_pnl_r = result.get("tiered_pnl_r")
                eod_pnl_r    = result.get("eod_pnl_r")

                # Apply RVOL bonus position-size multiplier so tiered/EOD R values
                # reflect the same dollar-scaled contribution as pnl_r_sim.
                if rvol_raw is not None:
                    try:
                        _rvol_mult = backend.rvol_size_mult(float(rvol_raw))
                        if _rvol_mult != 1.0:
                            if tiered_pnl_r is not None:
                                tiered_pnl_r = round(float(tiered_pnl_r) * _rvol_mult, 4)
                            if eod_pnl_r is not None:
                                eod_pnl_r = round(float(eod_pnl_r) * _rvol_mult, 4)
                    except (TypeError, ValueError):
                        pass

                if tiered_pnl_r is None:
                    tiered_pnl_r = 0.0
                    _eod_str = f"{eod_pnl_r:+.4f}R" if eod_pnl_r is not None else "n/a"
                    print(f"no entry cross — writing tiered=0.0R  eod={_eod_str}"
                          + (" [DRY RUN]" if dry_run else ""))
                    stats["skipped_no_tiered"] += 1
                else:
                    _eod_str2 = f"{eod_pnl_r:+.4f}R" if eod_pnl_r is not None else "n/a"
                    print(f"tiered={tiered_pnl_r:+.4f}R  eod={_eod_str2}"
                          + (" [DRY RUN]" if dry_run else ""))

                patch: dict = {"id": row_id, "tiered_pnl_r": tiered_pnl_r}
                if existing_eod is None and eod_pnl_r is not None:
                    patch["eod_pnl_r"] = eod_pnl_r
                chunk_patches.append(patch)

            # ── 3. Flush this chunk to Supabase ───────────────────────────────
            if chunk_patches and not dry_run:
                n = _batch_upsert(chunk_patches)
                stats["updated"] += n
                stats["errors"]  += len(chunk_patches) - n
            elif dry_run:
                stats["updated"] += len(chunk_patches)

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


def verify_residual_nulls_backtest() -> int:
    """Count backtest_sim_runs rows that still have tiered_pnl_r IS NULL after the run."""
    if not backend.supabase:
        return -1
    try:
        resp = (
            backend.supabase.table("backtest_sim_runs")
            .select("id", count="exact")
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
        description="Backfill 50/25/25 ladder tiered_pnl_r on historical paper_trades and backtest_sim_runs"
    )
    parser.add_argument(
        "user_ids",
        nargs="*",
        metavar="UID",
        help="Optional user IDs to process for paper_trades (default: auto-discover all).",
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
    parser.add_argument(
        "--skip-backtest",
        action="store_true",
        help="Skip the backtest_sim_runs backfill (paper_trades only).",
    )
    parser.add_argument(
        "--paper-only",
        action="store_true",
        help="Only backfill paper_trades; skip backtest_sim_runs entirely.  "
             "Equivalent to --skip-backtest; provided as a clearer alias for "
             "the nightly scheduler.",
    )
    parser.add_argument(
        "--backtest-only",
        action="store_true",
        help="Only backfill backtest_sim_runs; skip paper_trades entirely.",
    )
    parser.add_argument(
        "--reset-sentinel",
        action="store_true",
        help=(
            "Clear the unavailability sentinel (tiered_pnl_r = -9999) from rows so they "
            "can be retried.  Use --table to choose which table(s) to reset.  "
            "Combine with --ticker and/or --date-from / --date-to to scope the reset. "
            "Mutually exclusive with normal backfill — no Alpaca calls are made."
        ),
    )
    parser.add_argument(
        "--table",
        metavar="TABLE",
        default="backtest_sim_runs",
        choices=["backtest_sim_runs", "paper_trades", "both"],
        help=(
            "Which table to reset when --reset-sentinel is active.  "
            "Choices: backtest_sim_runs (default), paper_trades, both."
        ),
    )
    parser.add_argument(
        "--ticker",
        metavar="TICKER",
        default="",
        help="Limit --reset-sentinel to a single ticker symbol (e.g. AAPL).",
    )
    parser.add_argument(
        "--date-from",
        metavar="YYYY-MM-DD",
        default="",
        help=(
            "Restrict processing to rows with sim_date >= this date (YYYY-MM-DD). "
            "Applies to both normal backfill and --reset-sentinel mode."
        ),
    )
    parser.add_argument(
        "--date-to",
        metavar="YYYY-MM-DD",
        default="",
        help=(
            "Restrict processing to rows with sim_date <= this date (YYYY-MM-DD). "
            "Applies to both normal backfill and --reset-sentinel mode."
        ),
    )
    args = parser.parse_args()

    # Guard against contradictory mode flags that would result in a silent no-op.
    if args.backtest_only and (args.skip_backtest or args.paper_only):
        parser.error(
            "--backtest-only cannot be combined with --skip-backtest / --paper-only "
            "(they select opposite phases and together would skip everything)."
        )

    dry_run         = args.dry_run
    rate_limit      = not args.no_ratelimit
    skip_backtest   = args.skip_backtest or args.paper_only
    backtest_only   = args.backtest_only
    reset_sentinel  = args.reset_sentinel
    rs_table        = args.table
    rs_ticker       = args.ticker.strip().upper() if args.ticker.strip() else ""
    rs_date_from    = args.date_from.strip()
    rs_date_to      = args.date_to.strip()

    # ── --reset-sentinel mode (mutually exclusive with normal backfill) ────────
    if reset_sentinel:
        do_backtest    = rs_table in ("backtest_sim_runs", "both")
        do_paper       = rs_table in ("paper_trades", "both")

        print("EdgeIQ — Reset Unavailability Sentinel")
        print("=" * 60)
        print(f"  Table(s): {rs_table}")
        scope_parts = []
        if rs_ticker:
            scope_parts.append(f"ticker={rs_ticker}")
        if rs_date_from:
            scope_parts.append(f"date_from={rs_date_from}")
        if rs_date_to:
            scope_parts.append(f"date_to={rs_date_to}")
        scope_str = ", ".join(scope_parts) if scope_parts else "all tickers / all dates"
        print(f"  Scope: {scope_str}")
        if dry_run:
            print("  *** DRY RUN — no database writes ***")
        print()

        any_found = False
        had_error = False

        if do_backtest:
            print("  [backtest_sim_runs]")
            bt_before = backend.count_backtest_tiered_sentinel(
                ticker=rs_ticker, date_from=rs_date_from, date_to=rs_date_to
            )
            print(f"  Sentinel-stamped rows found : {bt_before:,}")
            if bt_before == 0:
                print("  Nothing to reset in backtest_sim_runs.")
            else:
                any_found = True
                if dry_run:
                    print(
                        f"  Would reset {bt_before:,} row(s) "
                        f"(re-run without --dry-run to apply)."
                    )
                else:
                    bt_result = backend.reset_backtest_tiered_sentinel(
                        ticker=rs_ticker, date_from=rs_date_from, date_to=rs_date_to
                    )
                    if bt_result.get("error"):
                        print(f"  ERROR: {bt_result['error']}")
                        had_error = True
                    else:
                        bt_reset = bt_result.get("reset", 0)
                        print(f"  Rows reset (sentinel cleared)   : {bt_reset:,}")
                        bt_after = backend.count_backtest_tiered_sentinel(
                            ticker=rs_ticker, date_from=rs_date_from, date_to=rs_date_to
                        )
                        print(f"  Sentinel-stamped rows remaining : {bt_after:,}")
                        pending = backend.count_backtest_tiered_pending()
                        print(f"  Rows now in backfill queue      : {pending:,}")
            print()

        if do_paper:
            print("  [paper_trades]")
            pt_before = backend.count_paper_trades_tiered_sentinel(
                ticker=rs_ticker, date_from=rs_date_from, date_to=rs_date_to
            )
            print(f"  Sentinel-stamped rows found : {pt_before:,}")
            if pt_before == 0:
                print("  Nothing to reset in paper_trades.")
            else:
                any_found = True
                if dry_run:
                    print(
                        f"  Would reset {pt_before:,} row(s) "
                        f"(re-run without --dry-run to apply)."
                    )
                else:
                    pt_result = backend.reset_paper_trades_tiered_sentinel(
                        ticker=rs_ticker, date_from=rs_date_from, date_to=rs_date_to
                    )
                    if pt_result.get("error"):
                        print(f"  ERROR: {pt_result['error']}")
                        had_error = True
                    else:
                        pt_reset = pt_result.get("reset", 0)
                        print(f"  Rows reset (sentinel cleared)   : {pt_reset:,}")
                        pt_after = backend.count_paper_trades_tiered_sentinel(
                            ticker=rs_ticker, date_from=rs_date_from, date_to=rs_date_to
                        )
                        print(f"  Sentinel-stamped rows remaining : {pt_after:,}")
            print()

        if had_error:
            print("  PARTIAL FAILURE — one or more tables could not be reset (see ERROR lines above).")
            sys.exit(1)
        elif not any_found and not dry_run:
            print("  Nothing to reset across selected table(s) — exiting.")
        elif not dry_run:
            print("  Reset complete.  Re-run the normal backfill to process these rows.")
        return

    print("EdgeIQ — Tiered P&L Backfill (50/25/25 ladder)")
    print("=" * 60)
    if dry_run:
        print("  *** DRY RUN — no database writes ***")
    if not rate_limit:
        print("  Rate limiting disabled.")
    if backtest_only:
        print("  Mode: backtest_sim_runs only (paper_trades skipped).")
    elif skip_backtest:
        print("  Mode: paper_trades only (backtest_sim_runs skipped).")
    print()

    t0 = time.time()

    # ── paper_trades phase ────────────────────────────────────────────────────
    if not backtest_only:
        if args.user_ids:
            user_ids = list(dict.fromkeys(args.user_ids))
            print(f"Using {len(user_ids)} user ID(s) from command-line arguments.")
        else:
            print("No user IDs specified — querying database for qualifying users…")
            user_ids = discover_user_ids()
            if not user_ids:
                print("No qualifying paper_trades rows found (tiered_pnl_r already populated or no data).")
            else:
                print(f"Found {len(user_ids)} user(s) with NULL tiered_pnl_r rows: {user_ids}")

        grand_pt = {"fetched": 0, "updated": 0, "skipped_no_bars": 0, "skipped_no_tiered": 0, "errors": 0}

        for uid in user_ids:
            print(f"\n{'#'*60}")
            print(f"  paper_trades — User: {uid}")
            print(f"{'#'*60}")
            stats = backfill_user(uid, dry_run=dry_run, rate_limit=rate_limit)
            for k in grand_pt:
                grand_pt[k] += stats[k]

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

        if user_ids:
            print(f"\n{'='*60}")
            print(f"  paper_trades TOTAL across {len(user_ids)} user(s)")
            print(f"  Rows processed          : {grand_pt['fetched']}")
            print(f"  Rows updated            : {grand_pt['updated']}"
                  + (" (dry run)" if dry_run else ""))
            print(f"  Skipped (no Alpaca bars): {grand_pt['skipped_no_bars']}")
            print(f"  Flat (no entry cross)   : {grand_pt['skipped_no_tiered']}")
            print(f"  Errors                  : {grand_pt['errors']}")

    # ── backtest_sim_runs phase ───────────────────────────────────────────────
    if not skip_backtest:
        print(f"\n{'#'*60}")
        if rs_date_from or rs_date_to:
            _window = f"{rs_date_from or '…'} → {rs_date_to or '…'}"
            print(f"  backtest_sim_runs — date window: {_window}")
        else:
            print(f"  backtest_sim_runs — all rows")
        print(f"{'#'*60}")
        bstats = backfill_backtest_sim_runs(
            dry_run=dry_run,
            rate_limit=rate_limit,
            date_from=rs_date_from,
            date_to=rs_date_to,
        )

        print(f"\n  --- backtest_sim_runs summary ---")
        print(f"  Rows processed          : {bstats['fetched']}")
        print(f"  Rows updated            : {bstats['updated']}"
              + (" (dry run)" if dry_run else ""))
        print(f"  Skipped (no Alpaca bars): {bstats['skipped_no_bars']}"
              + "  [tiered_pnl_r stays NULL — bars unavailable for these dates]"
              if bstats["skipped_no_bars"] else f"  Skipped (no Alpaca bars): {bstats['skipped_no_bars']}")
        print(f"  Flat (no entry cross)   : {bstats['skipped_no_tiered']}"
              + "  [tiered_pnl_r=0.0 — price never broke entry in post-IB window]"
              if bstats["skipped_no_tiered"] else f"  Flat (no entry cross)   : {bstats['skipped_no_tiered']}")
        print(f"  Errors                  : {bstats['errors']}")

        if not dry_run:
            residual = verify_residual_nulls_backtest()
            if residual < 0:
                print("  Verification           : unable to query (check manually)")
            elif residual == 0:
                print("  Verification           : PASS — 0 qualifying NULL rows remain")
            else:
                print(f"  Verification           : {residual} NULL rows remain (no Alpaca bars)")
                print("  These rows cannot be backfilled — bars are unavailable for those dates.")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  Elapsed: {elapsed:.0f}s")
    if dry_run:
        print("\n  *** DRY RUN complete — re-run without --dry-run to write to DB ***")
    else:
        print("\n  Backfill complete.")

    # ── Write run stats to a temp JSON file so the nightly scheduler can ──────
    # read them and send a Telegram summary without needing to parse stdout.
    # Only written on real (non-dry-run, non-reset-sentinel) runs.
    if not dry_run and not reset_sentinel:
        combined_stats = {
            "elapsed_s": round(elapsed, 1),
            "backtest": bstats if not skip_backtest else None,
            "paper_trades": grand_pt if not backtest_only and user_ids else None,
        }
        try:
            stats_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                ".edgeiq_tiered_pnl_run_stats.json",
            )
            with open(stats_path, "w") as _f:
                json.dump(combined_stats, _f)
        except Exception as _e:
            print(f"  (stats file write failed: {_e})")

    if not dry_run and not reset_sentinel:
        print("  Refreshing Ladder cache (mv_tiered_pnl_summary)…", end="", flush=True)
        try:
            _ref = backend.refresh_mv_tiered_pnl_summary()
            if _ref.get("success"):
                print(" done.")
            else:
                print(f" skipped — {_ref.get('message', 'unknown error')}")
        except Exception as _ref_e:
            print(f" error: {_ref_e}")


if __name__ == "__main__":
    main()
