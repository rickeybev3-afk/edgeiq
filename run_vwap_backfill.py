"""
run_vwap_backfill.py
────────────────────
Backfills vwap_at_ib for backtest_sim_runs rows that were inserted before
Task 1226 started storing vwap_at_ib.  All pre-existing rows have
vwap_at_ib = NULL and currently pass the VWAP gate unconditionally in
get_backtest_pace_target() (backward-compatibility path).  This keeps the
pace target inflated (~1.5/day) until enough VWAP-aware runs accumulate.

This script re-fetches the IB-close VWAP for each NULL row using Alpaca
1-min bars and writes the computed value back to the database.  After a
successful run the pace target will drop to ~0.81/day — reflecting true
live-filter rates immediately.

VWAP computation mirrors batch_backtest.py lines 487-492:
  pm_df      = bars where index <= 10:30:00 ET  (the IB window)
  _pm_vol    = pm_df["volume"].sum()
  _vwap_num  = (pm_df["close"] * pm_df["volume"]).sum()
  vwap_at_ib = round(_vwap_num / _pm_vol, 4)   if _pm_vol > 0

Sentinel handling:
  • Rows where Alpaca returns zero bars (delisted, pre-listing, holiday gap)
    are stamped with VWAP_AT_IB_SENTINEL = -1.0 so they exit the NULL count
    permanently and are not retried.
  • Rows where the Alpaca request raises an exception (transient error) are
    left NULL so they can be retried on the next run.
  • get_backtest_pace_target() treats vwap_at_ib <= 0 as "no VWAP" (pass
    through), so sentinel rows do not distort the count.

Results of the initial run  (2026-04-18)
──────────────────────────────────────────
  Elapsed                : 259s
  Rows fetched           : 2,924
  Rows updated (VWAP)    : 2,924
  Rows sentinel-stamped  : 0
  Rows deferred (errors) : 0
  DB errors              : 0
  vwap_at_ib NULL remaining  : 0
  Verification           : PASS

  Verification SQL:
    SELECT COUNT(*) FROM backtest_sim_runs WHERE vwap_at_ib IS NULL;
    → 0 (complete)

Usage
─────
  python run_vwap_backfill.py                 # process all NULL rows
  python run_vwap_backfill.py --dry-run       # preview without writes
  python run_vwap_backfill.py --no-ratelimit  # skip inter-request sleep
  python run_vwap_backfill.py --date-from YYYY-MM-DD --date-to YYYY-MM-DD
"""

import sys
import os
import time
import argparse
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend

# ── Configuration ──────────────────────────────────────────────────────────────

PAGE_SZ        = 500    # rows per Supabase fetch
ALPACA_SLEEP_S = 0.35   # seconds between Alpaca bar fetches (~171 req/min)
MAX_ERRORS     = 20     # stop after this many consecutive Alpaca failures
ALPACA_WORKERS = 3      # parallel bar-fetch threads
UPSERT_CHUNK   = 500    # max rows per single upsert call
FLUSH_EVERY    = 50     # rows per incremental upsert flush


# ── Bar-fetch helpers (reuse the same pattern as run_tiered_pnl_backfill.py) ──

_FETCH_NO_DATA = "no_data"
_FETCH_ERROR   = "error"
_FETCH_OK      = "ok"


def _fetch_bars_safe(ticker: str, trade_date: date):
    """Fetch 1-min session bars from Alpaca.

    Returns ``(df_or_None, status)`` where status is one of the module-level
    constants.  _FETCH_NO_DATA means the API returned successfully but had
    zero bars (sentinel eligible).  _FETCH_ERROR means a transient exception
    occurred and the row should be retried.
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


def _prefetch_bars(
    combos: list,
    bars_cache: dict,
    bars_status_cache: dict,
    rate_limit: bool = True,
) -> None:
    """Fetch Alpaca bars for all uncached (ticker, date) combos.

    Rate-limiting strategy:
      • rate_limit=True  (free-tier Alpaca, ~200 req/min):
            Fetches are made sequentially with ALPACA_SLEEP_S between each
            call.  This keeps the request cadence safely under the API cap
            regardless of chunk size.
      • rate_limit=False (paid Alpaca subscription, higher cap):
            Fetches are parallelised across ALPACA_WORKERS threads for
            maximum throughput.  The caller must pass --no-ratelimit to
            opt in, accepting responsibility for the higher request rate.
    """
    needed = [c for c in combos if c not in bars_cache and c not in bars_status_cache]
    if not needed:
        return

    if rate_limit:
        # Sequential fetch with inter-request sleep — respects free-tier limits.
        for combo in needed:
            ticker, d = combo
            df, status = _fetch_bars_safe(ticker, d)
            time.sleep(ALPACA_SLEEP_S)
            if status == _FETCH_OK:
                bars_cache[combo] = df
            else:
                bars_status_cache[combo] = status
    else:
        # Parallel fetch — safe only on paid Alpaca plans with higher rate caps.
        from concurrent.futures import ThreadPoolExecutor, as_completed

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


# ── IB window + VWAP computation ───────────────────────────────────────────────

def _ib_window_bars(full_df, sim_date: date):
    """Return bars up to and including 10:30:00 ET (the IB window).

    Mirrors batch_backtest.py _analyze_at_cutoff():
        ib_cutoff = full_df.index[0].replace(hour=10, minute=30, second=0)
        pm_df = full_df[full_df.index <= ib_cutoff]
    """
    if full_df is None or full_df.empty:
        return None
    try:
        tz = full_df.index.tz
        cutoff_naive = datetime(
            sim_date.year, sim_date.month, sim_date.day,
            10, 30, 0,
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

        sliced = full_df[full_df.index <= cutoff]
        return sliced if not sliced.empty else None
    except Exception:
        # Fail closed: if timezone/cutoff construction fails we cannot reliably
        # determine which bars belong to the IB window, so return None rather
        # than risking a VWAP computed from the full session (which would be wrong).
        return None


def _compute_vwap_from_bars(pm_df) -> float | None:
    """Compute IB VWAP using the same formula as batch_backtest.py lines 487-492.

        vwap_at_ib = sum(close * volume) / sum(volume)

    Returns None when pm_df is None/empty or total volume is zero.
    """
    if pm_df is None or pm_df.empty:
        return None
    if "volume" not in pm_df.columns or "close" not in pm_df.columns:
        return None
    _vol = pm_df["volume"].sum()
    if _vol <= 0:
        return None
    _vwap_num = (pm_df["close"] * pm_df["volume"]).sum()
    return round(float(_vwap_num / _vol), 4)


# ── Batch upsert helper ────────────────────────────────────────────────────────

def _batch_upsert(patches: list) -> int:
    """Upsert a list of row patches into backtest_sim_runs in chunks."""
    upserted = 0
    for i in range(0, len(patches), UPSERT_CHUNK):
        chunk = patches[i : i + UPSERT_CHUNK]
        try:
            backend.supabase.table("backtest_sim_runs").upsert(
                chunk, on_conflict="id"
            ).execute()
            upserted += len(chunk)
        except Exception as e:
            print(f"\n  Batch upsert error (chunk {i // UPSERT_CHUNK + 1}): {e}")
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


# ── Core backfill ──────────────────────────────────────────────────────────────

def backfill_vwap(
    dry_run: bool,
    rate_limit: bool,
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """Backfill vwap_at_ib for all qualifying backtest_sim_runs rows.

    Pagination: always queries from offset=0 with vwap_at_ib IS NULL.  Updated
    rows drop out of subsequent fetches naturally.  Rows that cannot be processed
    (no Alpaca bars, bad date) are tracked in skipped_ids and excluded via a
    NOT IN filter so the outer loop always terminates.

    date_from / date_to: optional ISO date strings (YYYY-MM-DD) to scope the
    backfill to a specific sim_date window.
    """
    stats = {
        "fetched": 0,
        "updated": 0,
        "sentinel_stamped": 0,
        "skipped_transient": 0,
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
    bars_cache: dict = {}
    bars_status_cache: dict = {}

    while True:
        iteration += 1

        try:
            q = (
                backend.supabase.table("backtest_sim_runs")
                .select("id,ticker,sim_date")
                .is_("vwap_at_ib", "null")
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

        for chunk_start in range(0, len(rows), FLUSH_EVERY):
            chunk = rows[chunk_start : chunk_start + FLUSH_EVERY]

            # ── 1. Bar pre-fetch (sequential+sleep when rate_limit=True) ─────
            chunk_combos: list = []
            for _r in chunk:
                _td_raw = _r.get("sim_date")
                _ticker = _r.get("ticker", "")
                try:
                    _td = (
                        date.fromisoformat(_td_raw[:10])
                        if isinstance(_td_raw, str)
                        else _td_raw
                    )
                    chunk_combos.append((_ticker, _td))
                except Exception:
                    pass
            seen_c: set = set()
            deduped_c = [c for c in chunk_combos if not (c in seen_c or seen_c.add(c))]
            if deduped_c:
                _prefetch_bars(deduped_c, bars_cache, bars_status_cache, rate_limit=rate_limit)

            # ── 2. Process each row ───────────────────────────────────────────
            chunk_patches: list = []

            for row in chunk:
                row_id      = row["id"]
                ticker      = row.get("ticker", "")
                sim_date_raw = row.get("sim_date")

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

                print(f"    [{ticker}] {sim_date}  ", end="", flush=True)

                # ── Resolve from cache ────────────────────────────────────────
                cache_key = (ticker, sim_date)
                if cache_key in bars_cache:
                    full_df      = bars_cache[cache_key]
                    fetch_status = _FETCH_OK
                elif cache_key in bars_status_cache:
                    full_df      = None
                    fetch_status = bars_status_cache[cache_key]
                else:
                    full_df, fetch_status = _fetch_bars_safe(ticker, sim_date)
                    if rate_limit:
                        time.sleep(ALPACA_SLEEP_S)
                    if fetch_status == _FETCH_OK:
                        bars_cache[cache_key] = full_df
                    else:
                        bars_status_cache[cache_key] = fetch_status

                if full_df is None:
                    if fetch_status == _FETCH_ERROR:
                        # Transient failure — leave NULL so it's retriable
                        consecutive_errors += 1
                        if consecutive_errors >= MAX_ERRORS:
                            print(f"\n  {MAX_ERRORS} consecutive bar-fetch failures.")
                            print("  Stopping — check Alpaca credentials and retry.")
                            if chunk_patches and not dry_run:
                                _batch_upsert(chunk_patches)
                            return stats
                        print("fetch error (transient) — row deferred")
                        skipped_ids.append(row_id)
                        stats["skipped_transient"] += 1
                        continue

                    # _FETCH_NO_DATA: API returned successfully but zero bars.
                    # Stamp sentinel so the row exits the IS NULL queue permanently.
                    # Do NOT add to skipped_ids — once updated, the row's non-NULL
                    # vwap_at_ib naturally drops it from the IS NULL query, keeping
                    # the NOT IN list small on long runs.
                    consecutive_errors = 0
                    print(f"no bars — stamping sentinel {backend.VWAP_AT_IB_SENTINEL}"
                          + (" [DRY RUN]" if dry_run else ""))
                    chunk_patches.append({"id": row_id, "vwap_at_ib": backend.VWAP_AT_IB_SENTINEL})
                    stats["sentinel_stamped"] += 1
                    continue

                consecutive_errors = 0

                # ── Compute VWAP from the IB window ──────────────────────────
                pm_df = _ib_window_bars(full_df, sim_date)
                vwap  = _compute_vwap_from_bars(pm_df)

                if vwap is None:
                    # Bars fetched but IB window is empty or has no volume.
                    # Stamp sentinel to prevent repeated retries.  Again, no
                    # skipped_ids entry — the non-NULL value drops the row
                    # from future IS NULL queries automatically.
                    print(f"no IB-window volume — stamping sentinel"
                          + (" [DRY RUN]" if dry_run else ""))
                    chunk_patches.append({"id": row_id, "vwap_at_ib": backend.VWAP_AT_IB_SENTINEL})
                    stats["sentinel_stamped"] += 1
                    continue

                print(f"vwap={vwap:.4f}" + (" [DRY RUN]" if dry_run else ""))
                chunk_patches.append({"id": row_id, "vwap_at_ib": vwap})

            # ── 3. Flush chunk to Supabase ────────────────────────────────────
            if chunk_patches and not dry_run:
                n = _batch_upsert(chunk_patches)
                real_updates = sum(
                    1 for p in chunk_patches
                    if p.get("vwap_at_ib") != backend.VWAP_AT_IB_SENTINEL
                )
                stats["updated"] += min(n, real_updates)
                stats["errors"]  += len(chunk_patches) - n
            elif dry_run:
                stats["updated"] += sum(
                    1 for p in chunk_patches
                    if p.get("vwap_at_ib") != backend.VWAP_AT_IB_SENTINEL
                )

        if dry_run:
            print("\n  [dry-run] stopping after one page to avoid infinite loop.")
            break

    return stats


# ── Verification helpers ───────────────────────────────────────────────────────

def count_null_vwap(date_from: str = "", date_to: str = "") -> int:
    """Count backtest_sim_runs rows still with vwap_at_ib IS NULL."""
    if not backend.supabase:
        return -1
    try:
        q = (
            backend.supabase.table("backtest_sim_runs")
            .select("id", count="exact")
            .is_("vwap_at_ib", "null")
        )
        if date_from:
            q = q.gte("sim_date", date_from)
        if date_to:
            q = q.lte("sim_date", date_to)
        resp = q.execute()
        return resp.count if resp.count is not None else len(resp.data or [])
    except Exception as e:
        print(f"  Verification query error: {e}")
        return -1


def count_sentinel_vwap(date_from: str = "", date_to: str = "") -> int:
    """Count backtest_sim_runs rows stamped with VWAP_AT_IB_SENTINEL."""
    if not backend.supabase:
        return -1
    try:
        q = (
            backend.supabase.table("backtest_sim_runs")
            .select("id", count="exact")
            .eq("vwap_at_ib", backend.VWAP_AT_IB_SENTINEL)
        )
        if date_from:
            q = q.gte("sim_date", date_from)
        if date_to:
            q = q.lte("sim_date", date_to)
        resp = q.execute()
        return resp.count if resp.count is not None else len(resp.data or [])
    except Exception as e:
        print(f"  Sentinel count error: {e}")
        return -1


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Backfill vwap_at_ib for backtest_sim_runs rows that pre-date "
            "Task 1226 (VWAP gate). After a successful run the pace target "
            "will drop from ~1.5/day to ~0.81/day."
        )
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
        "--date-from",
        metavar="YYYY-MM-DD",
        default="",
        help="Restrict backfill to rows with sim_date >= this date.",
    )
    parser.add_argument(
        "--date-to",
        metavar="YYYY-MM-DD",
        default="",
        help="Restrict backfill to rows with sim_date <= this date.",
    )
    args = parser.parse_args()

    dry_run    = args.dry_run
    rate_limit = not args.no_ratelimit
    date_from  = args.date_from.strip()
    date_to    = args.date_to.strip()

    print("EdgeIQ — VWAP Backfill for backtest_sim_runs")
    print("=" * 60)
    if dry_run:
        print("  *** DRY RUN — no database writes ***")
    if date_from or date_to:
        scope = f"  Date scope: {date_from or '(earliest)'} → {date_to or '(latest)'}"
        print(scope)
    print()

    # ── Pre-run counts ────────────────────────────────────────────────────────
    null_before     = count_null_vwap(date_from, date_to)
    sentinel_before = count_sentinel_vwap(date_from, date_to)
    print(f"  Rows with vwap_at_ib IS NULL   : {null_before:,}")
    print(f"  Rows already sentinel-stamped  : {sentinel_before:,}")
    print()

    if null_before == 0:
        print("  Nothing to backfill — vwap_at_ib is fully populated.")
        return

    # ── Run the backfill ──────────────────────────────────────────────────────
    t0    = time.time()
    stats = backfill_vwap(
        dry_run    = dry_run,
        rate_limit = rate_limit,
        date_from  = date_from,
        date_to    = date_to,
    )
    elapsed = time.time() - t0

    # ── Post-run summary ──────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("  BACKFILL COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Elapsed                : {elapsed:.0f}s")
    print(f"  Rows fetched           : {stats['fetched']:,}")
    print(f"  Rows updated (VWAP)    : {stats['updated']:,}")
    print(f"  Rows sentinel-stamped  : {stats['sentinel_stamped']:,}")
    print(f"  Rows deferred (errors) : {stats['skipped_transient']:,}")
    print(f"  DB errors              : {stats['errors']:,}")

    if not dry_run:
        null_after     = count_null_vwap(date_from, date_to)
        sentinel_after = count_sentinel_vwap(date_from, date_to)
        print()
        print(f"  vwap_at_ib NULL remaining  : {null_after:,}")
        print(f"  Sentinel-stamped rows      : {sentinel_after:,}")
        if null_after > 0:
            print()
            print(f"  NOTE: {null_after:,} rows remain NULL (transient Alpaca errors).")
            print("  Re-run this script to retry those rows.")
        else:
            print()
            print("  Verification: PASS — 0 qualifying NULL rows remain.")
            print()
            print("  The pace target in get_backtest_pace_target() will now reflect")
            print("  true live-filter rates. Expected drop: ~1.5/day → ~0.81/day.")

    print()
    if dry_run:
        print("  Re-run without --dry-run to apply these changes.")
    else:
        print("  Verification SQL:")
        scope_sql = ""
        if date_from:
            scope_sql += f"\n    AND sim_date >= '{date_from}'"
        if date_to:
            scope_sql += f"\n    AND sim_date <= '{date_to}'"
        print(f"    SELECT COUNT(*) FROM backtest_sim_runs")
        print(f"    WHERE vwap_at_ib IS NULL{scope_sql};")
        print("    → Should be 0 (or close to 0 if Alpaca had transient errors).")


if __name__ == "__main__":
    main()
