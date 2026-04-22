"""
backfill_pending_sim_rows.py
────────────────────────────
Backfills the 14,594 backtest_sim_runs rows whose actual_outcome = 'Pending'
and entry_price_sim IS NULL.  These rows were logged by the scanner but never
had full-day Alpaca bar data fetched (API gaps/timeouts during the batch run).

For each qualifying row the script:
  1. Fetches 1-minute session bars from Alpaca (free IEX feed).
  2. Computes IB high/low from the 9:30–10:30 window (falls back to stored
     ib_high/ib_low if bars are missing).
  3. Slices post-IB bars (> 10:30:59 ET) to simulate the afternoon session.
  4. Determines actual_outcome (Bullish Break / Bearish Break / Both Sides /
     Range-Bound) from whether day price broke above ib_high or below ib_low.
  5. Derives win_loss from predicted vs actual_outcome (same logic as
     batch_backtest.py).
  6. Computes follow_thru_pct, false_break_up, false_break_down, mfe, mae.
  7. Runs compute_trade_sim() for entry_price_sim / stop_price_sim /
     target_price_sim / pnl_r_sim / sim_outcome / sim_version.
  8. Runs compute_trade_sim_tiered() for tiered_pnl_r and eod_pnl_r.
  9. Batch-writes all computed fields back to Supabase (FLUSH_EVERY rows per
     upsert call) so progress is committed incrementally.

Pagination strategy
───────────────────
The query always filters on  actual_outcome = 'Pending' AND entry_price_sim IS NULL.
Once a row is written — whether as a breakout (entry_price_sim set) or a
non-directional outcome (actual_outcome changed to Range-Bound / Both Sides) —
it drops out of subsequent fetches naturally.  This is the same approach used
by run_tiered_pnl_backfill.py and avoids cursor skew.

Rows that cannot be processed in the current run (transient Alpaca API errors)
are added to a skipped_ids set and excluded from the remaining fetches via a NOT IN
guard so the loop always terminates.  They will be re-attempted on the next run.

Rows with a permanent data gap (API returns no bars AND stored ib_high/ib_low are
absent) have their actual_outcome changed to 'Range-Bound' and tiered_pnl_r stamped
with the sentinel so they exit the IS NULL filter permanently.

Resumable
─────────
Because processed rows leave the filter set automatically, it is safe to kill and
restart at any time.  The script picks up exactly where it left off without any
checkpoint file.  Use --date-from / --date-to to scope a run to a specific year or
quarter if needed.

Rate limiting
─────────────
Alpaca's free-tier limit is ~200 requests/minute.  The script sleeps
ALPACA_SLEEP_S between per-ticker bar fetches (default 0.35 s ≈ 171 req/min).
Use --no-ratelimit to disable the sleep on paid Alpaca plans.

Progress log
────────────
Append-only progress is written to /tmp/backfill_pending.log so you can
tail it in another terminal:  tail -f /tmp/backfill_pending.log

Usage
─────
  python backfill_pending_sim_rows.py
  python backfill_pending_sim_rows.py --dry-run
  python backfill_pending_sim_rows.py --no-ratelimit
  python backfill_pending_sim_rows.py --date-from 2022-01-01 --date-to 2022-12-31
  python backfill_pending_sim_rows.py --dry-run --date-from 2022-01-01
"""

import sys
import os
import time
import logging
import argparse
from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend

# ── Configuration ──────────────────────────────────────────────────────────────

PAGE_SZ        = 500     # rows fetched per Supabase query
FLUSH_EVERY    = 50      # rows per Supabase upsert call (batch commit granularity)
ALPACA_SLEEP_S = 0.35    # seconds between bar prefetch batches (~171 req/min)
ALPACA_WORKERS = 3       # parallel Alpaca fetch threads per chunk
MAX_ERRORS     = 20      # stop after this many consecutive Alpaca failures
LOG_PATH       = "/tmp/backfill_pending.log"

# ── Logging setup ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_log_file = None


def _log(msg: str) -> None:
    """Print to stdout and append to the progress log file."""
    print(msg, flush=True)
    global _log_file
    if _log_file:
        try:
            _log_file.write(msg + "\n")
            _log_file.flush()
        except Exception:
            pass


# ── Bar fetch helpers (mirrors run_tiered_pnl_backfill.py) ────────────────────

_FETCH_NO_DATA = "no_data"
_FETCH_ERROR   = "error"
_FETCH_OK      = "ok"


def _fetch_bars_safe(ticker: str, trade_date: date):
    """Fetch 1-min session bars from Alpaca.

    Returns (df_or_None, status) where status is _FETCH_OK, _FETCH_NO_DATA,
    or _FETCH_ERROR.  Callers must distinguish no_data (stamp sentinel) from
    error (transient — retry next run).
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
    combos: list[tuple],
    bars_cache: dict,
    bars_status_cache: dict,
) -> None:
    """Fetch Alpaca bars for all uncached (ticker, date) combos in parallel."""
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
    """Slice full-session bars to the post-IB window (> 10:30:59 ET)."""
    if full_df is None or len(full_df) == 0:
        return None
    try:
        import pytz
        tz = full_df.index.tz
        cutoff_naive = datetime(
            trade_date.year, trade_date.month, trade_date.day,
            10, 30, 59,
        )
        if tz is not None:
            try:
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


# ── Win/loss classification (mirrors batch_backtest.py) ───────────────────────

def _determine_win_loss(predicted: str, actual_outcome: str) -> str:
    """Return 'Win' or 'Loss' given prediction vs outcome."""
    if not predicted or actual_outcome == "Pending":
        return "Loss"

    if predicted in ("Bullish Break", "Bearish Break"):
        win = actual_outcome == predicted
    else:
        is_dir      = any(k in predicted for k in backend._BACKTEST_DIRECTIONAL)
        is_range    = any(k in predicted for k in backend._BACKTEST_RANGE)
        is_neut_ext = any(k in predicted for k in backend._BACKTEST_NEUTRAL_EXT)
        is_balanced = (not is_neut_ext and
                       any(k in predicted for k in backend._BACKTEST_BALANCED))
        is_bimodal  = any(k in predicted for k in backend._BACKTEST_BIMODAL)
        is_normal   = (not is_dir and not is_range and not is_neut_ext
                       and not is_balanced and not is_bimodal
                       and "Normal" in predicted)

        if is_dir:
            win = actual_outcome in ("Bullish Break", "Bearish Break")
        elif is_neut_ext:
            win = actual_outcome in ("Bullish Break", "Bearish Break", "Both Sides")
        elif is_range or is_normal:
            win = actual_outcome == "Range-Bound"
        elif is_balanced:
            win = actual_outcome in ("Both Sides", "Bullish Break", "Bearish Break")
        elif is_bimodal:
            win = actual_outcome in ("Bullish Break", "Bearish Break", "Both Sides")
        else:
            win = False

    return "Win" if win else "Loss"


# ── Squeeze-specific win/loss price override ──────────────────────────────────

def _squeeze_tiered_sentinel(
    is_squeeze: bool,
    tiered_pnl_r: float | None,
    eod_pnl_r: float | None,
) -> float | None:
    """Fall back to eod_pnl_r when tiered_pnl_r is the intraday-stop sentinel.

    Volatile squeeze stocks can trigger an intraday stop-hit (-1R) that
    contradicts a profitable EOD close.  The close-based R (eod_pnl_r) is the
    more reliable anchor so tiered is replaced when:
      - is_squeeze is True
      - tiered_pnl_r is not None and is negative
      - eod_pnl_r is not None and is positive

    Returns the (possibly updated) tiered_pnl_r.
    """
    if (
        is_squeeze
        and tiered_pnl_r is not None and tiered_pnl_r < 0
        and eod_pnl_r is not None and eod_pnl_r > 0
    ):
        return eod_pnl_r
    return tiered_pnl_r


def _squeeze_win_loss_override(
    screener_pass: str | None,
    predicted: str,
    close_price: float | None,
    ib_high: float,
    ib_low: float,
    win_loss: str,
) -> str:
    """Apply squeeze price-based win/loss override and return updated win_loss.

    For squeeze screener trades the predicted direction (how the bot traded) is
    used together with close-vs-stop/target price comparison, replacing the
    default prediction-vs-outcome matching.

    Bullish (predicted="Bullish Break"): entry=ib_high, stop=ib_low, target=entry+2R
      close <= stop  → Loss
      close >= target → Win
      else            → win_loss unchanged

    Bearish (predicted="Bearish Break"): entry=ib_low, stop=ib_high, target=entry-2R
      close >= stop  → Loss
      close <= target → Win
      else            → win_loss unchanged

    Rows whose screener_pass is not "squeeze" or whose close_price is None are
    returned unchanged.
    """
    if (screener_pass or "").strip().lower() != "squeeze" or close_price is None:
        return win_loss

    _ib_rng = ib_high - ib_low
    if predicted == "Bullish Break":
        _sq_entry  = ib_high
        _sq_stop   = ib_low
        _sq_target = _sq_entry + 2.0 * _ib_rng
        if close_price <= _sq_stop:
            win_loss = "Loss"
        elif close_price >= _sq_target:
            win_loss = "Win"
    elif predicted == "Bearish Break":
        _sq_entry  = ib_low
        _sq_stop   = ib_high
        _sq_target = _sq_entry - 2.0 * _ib_rng
        if close_price >= _sq_stop:
            win_loss = "Loss"
        elif close_price <= _sq_target:
            win_loss = "Win"
    return win_loss


# ── Outcome classification from post-IB bars ──────────────────────────────────

def _classify_outcome(aft_df, ib_high: float, ib_low: float) -> tuple:
    """Classify actual_outcome from post-IB bars and compute auxiliary fields.

    Returns (actual_outcome, aft_move_pct, false_break_up, false_break_down,
              close_price).
    """
    if aft_df is None or len(aft_df) == 0:
        return "Range-Bound", 0.0, False, False, None

    aft_high = float(aft_df["high"].max())
    aft_low  = float(aft_df["low"].min())
    close_px = float(aft_df["close"].iloc[-1])

    broke_up   = aft_high > ib_high
    broke_down = aft_low  < ib_low

    if broke_up and broke_down:
        actual_outcome = "Both Sides"
    elif broke_up:
        actual_outcome = "Bullish Break"
    elif broke_down:
        actual_outcome = "Bearish Break"
    else:
        actual_outcome = "Range-Bound"

    # Follow-through percentage (same formula as batch_backtest.py)
    if broke_up and broke_down:
        _ft_up   = (aft_high - ib_high) / ib_high * 100
        _ft_down = (ib_low   - aft_low)  / ib_low  * 100
        aft_move_pct = _ft_up if _ft_up >= _ft_down else -_ft_down
    elif broke_up:
        aft_move_pct = (aft_high - ib_high) / ib_high * 100
    elif broke_down:
        aft_move_pct = -((ib_low - aft_low) / ib_low * 100)
    else:
        aft_move_pct = 0.0

    # Slippage drag (0.05% each side — matches batch_backtest.py default)
    _slip_drag = 0.05 * 2.0
    if aft_move_pct > 0:
        aft_move_pct = max(0.0, aft_move_pct - _slip_drag)
    elif aft_move_pct < 0:
        aft_move_pct = min(0.0, aft_move_pct + _slip_drag)

    # False break detection (price closed back inside IB within 30 min of break)
    _aft_r = aft_df.reset_index()
    false_break_up   = False
    false_break_down = False
    if broke_up:
        _up_bars = _aft_r[_aft_r["high"] > ib_high]
        if not _up_bars.empty:
            _fi = _up_bars.index[0]
            _w  = _aft_r.loc[_fi : _fi + 6]
            false_break_up = bool((_w["close"] < ib_high).any())
    if broke_down:
        _dn_bars = _aft_r[_aft_r["low"] < ib_low]
        if not _dn_bars.empty:
            _fi = _dn_bars.index[0]
            _w  = _aft_r.loc[_fi : _fi + 6]
            false_break_down = bool((_w["close"] > ib_low).any())

    return actual_outcome, round(aft_move_pct, 3), false_break_up, false_break_down, round(close_px, 4)


# ── MFE / MAE computation from post-IB bars ───────────────────────────────────

def _compute_mfe_mae(aft_df, ib_high: float, ib_low: float, direction: str):
    """Compute MFE and MAE in R-units from post-IB bars.

    Long:  MFE = (max_high - entry) / ib_range
           MAE = (entry - min_low)  / ib_range
    Short: MFE = (entry - min_low)  / ib_range
           MAE = (max_high - entry) / ib_range

    Returns (mfe_r, mae_r) or (None, None) on failure.
    Values are floored at 0.0 (negative excursions are clamped).
    """
    if aft_df is None or len(aft_df) == 0:
        return None, None
    ib_range = ib_high - ib_low
    if ib_range <= 0:
        return None, None
    try:
        aft_max_high = float(aft_df["high"].max())
        aft_min_low  = float(aft_df["low"].min())
        if direction == "Bullish Break":
            entry = ib_high
            mfe_r = max(0.0, (aft_max_high - entry) / ib_range)
            mae_r = max(0.0, (entry - aft_min_low)  / ib_range)
        elif direction == "Bearish Break":
            entry = ib_low
            mfe_r = max(0.0, (entry - aft_min_low)  / ib_range)
            mae_r = max(0.0, (aft_max_high - entry) / ib_range)
        else:
            return None, None
        return round(mfe_r, 4), round(mae_r, 4)
    except Exception:
        return None, None


# ── Batch upsert (mirrors run_tiered_pnl_backfill.py) ─────────────────────────

UPSERT_CHUNK = 500


def _batch_upsert(patches: list[dict]) -> int:
    """Upsert a list of row patches into backtest_sim_runs in chunks.

    Each patch dict must contain 'id' plus the fields to update.
    Returns the number of rows successfully upserted.
    """
    upserted = 0
    for i in range(0, len(patches), UPSERT_CHUNK):
        chunk = [dict(p) for p in patches[i : i + UPSERT_CHUNK]]
        try:
            backend.supabase.table("backtest_sim_runs").upsert(
                chunk, on_conflict="id"
            ).execute()
            upserted += len(chunk)
        except Exception as e:
            _log(f"\n  Batch upsert error (chunk {i//UPSERT_CHUNK + 1}): {e}")
            _log(f"  Falling back to row-by-row updates for {len(chunk)} rows…")
            for patch in chunk:
                row_id = patch.pop("id", None)
                if row_id is None:
                    continue
                try:
                    backend.supabase.table("backtest_sim_runs").update(
                        patch
                    ).eq("id", row_id).execute()
                    upserted += 1
                except Exception as e2:
                    _log(f"  Row update error id={row_id}: {e2}")
    return upserted


# ── Count helper ───────────────────────────────────────────────────────────────

def count_pending(date_from: str = "", date_to: str = "") -> int:
    """Return count of rows with actual_outcome = 'Pending' AND entry_price_sim IS NULL."""
    if not backend.supabase:
        return -1
    try:
        q = (
            backend.supabase.table("backtest_sim_runs")
            .select("id", count="exact")
            .eq("actual_outcome", "Pending")
            .is_("entry_price_sim", "null")
        )
        if date_from:
            q = q.gte("sim_date", date_from)
        if date_to:
            q = q.lte("sim_date", date_to)
        resp = q.limit(0).execute()
        return resp.count if resp.count is not None else -1
    except Exception as e:
        _log(f"  Count query error: {e}")
        return -1


# ── Core backfill logic ────────────────────────────────────────────────────────

def backfill_pending(
    dry_run: bool = False,
    rate_limit: bool = True,
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """Backfill all backtest_sim_runs rows with actual_outcome = 'Pending'.

    Pagination strategy: always queries from offset=0 with the same
    actual_outcome='Pending' AND entry_price_sim IS NULL filter.  Processed
    rows naturally drop out of subsequent fetches once their actual_outcome
    or entry_price_sim is written.  Rows with transient failures are tracked
    in skipped_ids and excluded via NOT IN so the loop always terminates.

    Returns a stats dict summarising the run.
    """
    stats = {
        "fetched":              0,
        "updated":              0,
        "skipped_no_bars":      0,
        "skipped_no_ib":        0,
        "non_directional":      0,   # Both Sides / Range-Bound — sentinel stamped
        "errors":               0,
    }

    if not backend.supabase:
        _log("  No Supabase connection — aborting.")
        return stats

    if not backend.ALPACA_API_KEY or not backend.ALPACA_SECRET_KEY:
        _log("  ERROR: ALPACA_API_KEY / ALPACA_SECRET_KEY not set — cannot fetch bars.")
        return stats

    # Load context levels for v6 trail-tightening sim (same as run_sim_backfill.py)
    import run_sim_backfill as _rsb
    _rsb._load_context_levels()

    # skipped_ids: rows deferred for this run (transient fetch errors).
    # These are re-attempted on the next restart — they do NOT exit the filter
    # set because we don't write to them.  The NOT IN guard below prevents
    # the loop from spinning on the same deferred rows indefinitely.
    skipped_ids: list = []
    consecutive_errors = 0
    iteration = 0

    # Bars cache: (ticker, sim_date) → df  (only _FETCH_OK entries)
    bars_cache: dict = {}
    # Status cache: (ticker, sim_date) → _FETCH_NO_DATA | _FETCH_ERROR
    bars_status_cache: dict = {}

    while True:
        iteration += 1

        # Always query from the beginning — processed rows (actual_outcome changed)
        # drop out of the result set naturally.
        try:
            q = (
                backend.supabase.table("backtest_sim_runs")
                .select(
                    "id,ticker,sim_date,predicted,actual_outcome,"
                    "ib_high,ib_low,tcs,scan_type,rvol,ib_range_pct,"
                    "follow_thru_pct,false_break_up,false_break_down,"
                    "close_price,mfe,mae,entry_price_sim,eod_pnl_r,screener_pass"
                )
                .eq("actual_outcome", "Pending")
                .is_("entry_price_sim", "null")
                .order("sim_date")
                .order("id")
            )
            if date_from:
                q = q.gte("sim_date", date_from)
            if date_to:
                q = q.lte("sim_date", date_to)
            if skipped_ids:
                q = q.not_.in_("id", skipped_ids)
            resp = q.limit(PAGE_SZ).execute()
        except Exception as e:
            _log(f"  Fetch error (iteration {iteration}): {e}")
            stats["errors"] += 1
            break

        rows = resp.data or []
        if not rows:
            # No more qualifying rows — done.
            break

        stats["fetched"] += len(rows)
        _log(f"\n  Iteration {iteration}: {len(rows)} qualifying Pending rows")

        for chunk_start in range(0, len(rows), FLUSH_EVERY):
            chunk = rows[chunk_start : chunk_start + FLUSH_EVERY]

            # ── 1. Parallel bar pre-fetch for this chunk ──────────────────────
            chunk_combos: list[tuple] = []
            for _r in chunk:
                _td_raw = _r.get("sim_date")
                _ticker = _r.get("ticker", "")
                try:
                    _td = (
                        date.fromisoformat(_td_raw[:10])
                        if isinstance(_td_raw, str) else _td_raw
                    )
                    chunk_combos.append((_ticker, _td))
                except Exception:
                    pass
            seen_c: set = set()
            deduped_c = [c for c in chunk_combos if not (c in seen_c or seen_c.add(c))]
            if deduped_c:
                _prefetch_bars(deduped_c, bars_cache, bars_status_cache)
                if rate_limit:
                    time.sleep(ALPACA_SLEEP_S * max(1, len(deduped_c)))

            # ── 2. Process each row using the warm cache ──────────────────────
            chunk_patches: list[dict] = []

            for row in chunk:
                row_id        = row["id"]
                ticker        = row.get("ticker", "")
                sim_date_raw  = row.get("sim_date")
                predicted     = (row.get("predicted") or "").strip()
                stored_ib_hi  = row.get("ib_high")
                stored_ib_lo  = row.get("ib_low")
                tcs           = row.get("tcs") or 0
                scan_type     = (row.get("scan_type") or "").strip()
                rvol_raw      = row.get("rvol")
                existing_eod  = row.get("eod_pnl_r")
                screener_pass = row.get("screener_pass")

                _prefix = f"    [{ticker}] {sim_date_raw}  "

                # Parse sim_date
                try:
                    if isinstance(sim_date_raw, str):
                        sim_date = date.fromisoformat(sim_date_raw[:10])
                    elif isinstance(sim_date_raw, date):
                        sim_date = sim_date_raw
                    else:
                        raise ValueError(f"unexpected type {type(sim_date_raw)}")
                except Exception:
                    _log(_prefix + "bad sim_date — skipping permanently (added to skipped_ids)")
                    stats["errors"] += 1
                    skipped_ids.append(row_id)
                    continue

                # ── Resolve bars from cache ───────────────────────────────────
                cache_key = (ticker, sim_date)
                if cache_key in bars_cache:
                    full_df, fetch_status = bars_cache[cache_key], _FETCH_OK
                elif cache_key in bars_status_cache:
                    full_df, fetch_status = None, bars_status_cache[cache_key]
                else:
                    full_df, fetch_status = _fetch_bars_safe(ticker, sim_date)
                    if rate_limit:
                        time.sleep(ALPACA_SLEEP_S)
                    if fetch_status == _FETCH_OK:
                        bars_cache[cache_key] = full_df
                    else:
                        bars_status_cache[cache_key] = fetch_status

                # ── Handle missing bars ───────────────────────────────────────
                if full_df is None:
                    if fetch_status == _FETCH_ERROR:
                        # Transient API failure — skip for this run; retry next restart.
                        # Do NOT write to DB: row remains actual_outcome='Pending' and
                        # will re-appear on next run.
                        consecutive_errors += 1
                        if consecutive_errors >= MAX_ERRORS:
                            _log(f"\n  {MAX_ERRORS} consecutive bar-fetch failures — stopping.")
                            _log("  Check Alpaca credentials and rerun.")
                            if chunk_patches and not dry_run:
                                _batch_upsert(chunk_patches)
                            return stats
                        _log(_prefix + "fetch error (transient) — deferred to next run")
                        skipped_ids.append(row_id)
                        stats["skipped_no_bars"] += 1
                        continue

                    # _FETCH_NO_DATA: API returned zero bars (delisted, holiday,
                    # pre-listing).  Permanently resolve: write actual_outcome +
                    # sentinel so the row exits the IS NULL filter forever.
                    consecutive_errors = 0

                    if stored_ib_hi is None or stored_ib_lo is None:
                        # No bars AND no stored IB — cannot classify or simulate.
                        # Mark as Range-Bound with sentinel so it exits the filter.
                        _log(_prefix + "no bars & no stored IB → Range-Bound sentinel (permanent)")
                        patch_nb: dict = {
                            "id":             row_id,
                            "actual_outcome": "Range-Bound",
                            "win_loss":       _determine_win_loss(predicted, "Range-Bound"),
                            "tiered_pnl_r":   backend.TIERED_PNL_SENTINEL,
                        }
                        if not dry_run:
                            chunk_patches.append(patch_nb)
                        stats["skipped_no_ib"] += 1
                        # Do NOT add to skipped_ids — we're writing the resolution now.
                        continue

                    # Have stored IB but no intraday bars.  Classify as Range-Bound
                    # (no post-IB bars observed) and stamp sentinel.
                    _log(_prefix + "no intraday bars → Range-Bound sentinel (permanent)")
                    patch_nb2: dict = {
                        "id":             row_id,
                        "actual_outcome": "Range-Bound",
                        "win_loss":       _determine_win_loss(predicted, "Range-Bound"),
                        "ib_high":        round(float(stored_ib_hi), 4),
                        "ib_low":         round(float(stored_ib_lo), 4),
                        "tiered_pnl_r":   backend.TIERED_PNL_SENTINEL,
                    }
                    if not dry_run:
                        chunk_patches.append(patch_nb2)
                    stats["non_directional"] += 1
                    continue

                consecutive_errors = 0

                # ── Compute IB from bars (prefer stored values if available) ──
                if stored_ib_hi is not None and stored_ib_lo is not None:
                    ib_high = float(stored_ib_hi)
                    ib_low  = float(stored_ib_lo)
                else:
                    ib_high, ib_low = backend.compute_initial_balance(full_df)
                    if ib_high is None or ib_low is None:
                        _log(_prefix + "IB compute failed — marking Range-Bound (permanent)")
                        patch_noib: dict = {
                            "id":             row_id,
                            "actual_outcome": "Range-Bound",
                            "win_loss":       _determine_win_loss(predicted, "Range-Bound"),
                            "tiered_pnl_r":   backend.TIERED_PNL_SENTINEL,
                        }
                        if not dry_run:
                            chunk_patches.append(patch_noib)
                        stats["skipped_no_ib"] += 1
                        continue

                ib_range = ib_high - ib_low
                if ib_range <= 0:
                    _log(_prefix + "degenerate IB (range ≤ 0) — marking Range-Bound (permanent)")
                    patch_degen: dict = {
                        "id":             row_id,
                        "actual_outcome": "Range-Bound",
                        "win_loss":       _determine_win_loss(predicted, "Range-Bound"),
                        "ib_high":        round(ib_high, 4),
                        "ib_low":         round(ib_low, 4),
                        "tiered_pnl_r":   backend.TIERED_PNL_SENTINEL,
                    }
                    if not dry_run:
                        chunk_patches.append(patch_degen)
                    stats["skipped_no_ib"] += 1
                    continue

                # ── Post-IB bars ──────────────────────────────────────────────
                aft_df = _post_ib_bars(full_df, sim_date)

                # ── Classify actual outcome ───────────────────────────────────
                (
                    actual_outcome,
                    aft_move_pct,
                    false_break_up,
                    false_break_down,
                    close_price,
                ) = _classify_outcome(aft_df, ib_high, ib_low)

                win_loss = _determine_win_loss(predicted, actual_outcome)

                # ── Squeeze-specific win/loss override using price levels ──────
                # For squeeze screener trades, use the PREDICTED direction (which is
                # how the bot traded) and directional close-vs-stop/target comparison
                # instead of prediction-vs-outcome matching.  Using predicted keeps
                # win/loss consistent with the pnl_r_sim direction (also predicted-based).
                # Squeeze stocks often briefly cross one IB boundary then reverse —
                # actual_outcome classification of that boundary is irrelevant to PnL;
                # what matters is whether the trade direction's stop or target was reached.
                win_loss = _squeeze_win_loss_override(
                    screener_pass, predicted, close_price,
                    ib_high, ib_low, win_loss,
                )

                # ── Build base patch ──────────────────────────────────────────
                patch: dict = {
                    "id":              row_id,
                    "actual_outcome":  actual_outcome,
                    "win_loss":        win_loss,
                    "ib_high":         round(ib_high, 4),
                    "ib_low":          round(ib_low, 4),
                    "follow_thru_pct": aft_move_pct,
                    "false_break_up":  false_break_up,
                    "false_break_down": false_break_down,
                }
                if close_price is not None:
                    patch["close_price"] = close_price

                # ── Non-directional outcomes: stamp sentinel and continue ─────
                if actual_outcome not in ("Bullish Break", "Bearish Break"):
                    patch["tiered_pnl_r"] = backend.TIERED_PNL_SENTINEL
                    _log(_prefix + f"{actual_outcome} — no trade → sentinel"
                         + (" [DRY RUN]" if dry_run else ""))
                    if not dry_run:
                        chunk_patches.append(patch)
                    stats["non_directional"] += 1
                    continue

                # ── Directional: compute MFE/MAE and sim P&L fields ───────────
                mfe_r, mae_r = _compute_mfe_mae(aft_df, ib_high, ib_low, actual_outcome)
                if mfe_r is not None:
                    patch["mfe"] = mfe_r
                if mae_r is not None:
                    patch["mae"] = mae_r

                # Build enriched row dict for compute_trade_sim
                enriched = dict(row)
                enriched["actual_outcome"]   = actual_outcome
                enriched["ib_high"]          = ib_high
                enriched["ib_low"]           = ib_low
                enriched["close_price"]      = close_price
                enriched["follow_thru_pct"]  = aft_move_pct
                enriched["false_break_up"]   = false_break_up
                enriched["false_break_down"] = false_break_down
                enriched["mfe"]              = mfe_r
                enriched["mae"]              = mae_r

                # Inject S/R context levels for v6 trail-tightening
                _date_s  = sim_date_raw[:10] if isinstance(sim_date_raw, str) else str(sim_date)
                _ctx_key = (ticker.upper().strip(), _date_s, scan_type.strip())
                if _rsb._CTX_LEVELS:
                    _ctx = _rsb._CTX_LEVELS.get(_ctx_key)
                    if _ctx:
                        enriched["nearest_resistance"] = _ctx.get("nearest_resistance")
                        enriched["nearest_support"]    = _ctx.get("nearest_support")

                # compute_trade_sim → entry/stop/target/pnl_r_sim
                _structure = predicted or actual_outcome
                _target_r  = backend.adaptive_target_r(
                    float(tcs), scan_type=scan_type, structure=_structure
                )

                # For squeeze: use predicted direction for pnl_r_sim so bearish squeeze
                # trades (stop above entry) get the correct directional R calculation.
                # actual_outcome priority in compute_trade_sim would apply the wrong
                # direction when the stock briefly moves against the predicted setup.
                _is_squeeze = (screener_pass or "").strip().lower() == "squeeze"
                if _is_squeeze and predicted in ("Bullish Break", "Bearish Break"):
                    _sim_enriched = dict(enriched)
                    _sim_enriched["actual_outcome"] = predicted
                else:
                    _sim_enriched = enriched
                _sim_raw = backend.compute_trade_sim(_sim_enriched, target_r=_target_r)

                if _sim_raw.get("sim_outcome") not in ("no_trade", "missing_data", "invalid_ib", None):
                    sim = backend.apply_rvol_sizing_to_sim(_sim_raw, rvol_raw)
                    patch.update({
                        "entry_price_sim":  sim.get("entry_price_sim"),
                        "stop_price_sim":   sim.get("stop_price_sim"),
                        "target_price_sim": sim.get("target_price_sim"),
                        "pnl_r_sim":        sim.get("pnl_r_sim"),
                        "pnl_pct_sim":      sim.get("pnl_pct_sim"),
                        "sim_outcome":      sim.get("sim_outcome"),
                        "sim_version":      sim.get("sim_version"),
                        "stop_dist_pct":    sim.get("stop_dist_pct"),
                    })

                # compute_trade_sim_tiered → tiered_pnl_r, eod_pnl_r
                _tiered = backend.compute_trade_sim_tiered(
                    aft_df    = aft_df,
                    ib_high   = ib_high,
                    ib_low    = ib_low,
                    direction = actual_outcome,
                    close_px  = close_price,
                )
                tiered_pnl_r = _tiered.get("tiered_pnl_r")
                eod_pnl_r    = _tiered.get("eod_pnl_r")

                # RVOL bonus sizing multiplier (mirrors live bot)
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

                # Screener-pass multiplier on eod_pnl_r
                _SP_MULT = {"other": 1.15, "gap": 1.00, "trend": 0.85, "squeeze": 1.00}
                _sp_tag  = (screener_pass or "").strip().lower()
                _sp_mult = _SP_MULT.get(_sp_tag, 1.00)
                if eod_pnl_r is not None and _sp_mult != 1.0:
                    eod_pnl_r = round(float(eod_pnl_r) * _sp_mult, 4)

                # Squeeze tiered_pnl_r sentinel fix: bar-by-bar simulations on highly
                # volatile squeeze stocks can show an intraday stop-out (-1R) that
                # contradicts a profitable EOD close.  When tiered shows negative R but
                # eod_pnl_r is positive, fall back to the real close-based directional R:
                #   bullish: (close - entry) / (entry - stop) = eod_pnl_r
                #   bearish: (entry - close) / (stop - entry) = eod_pnl_r
                tiered_pnl_r = _squeeze_tiered_sentinel(_is_squeeze, tiered_pnl_r, eod_pnl_r)

                # tiered_pnl_r = 0.0 when bars show no entry cross (matches existing behavior)
                patch["tiered_pnl_r"] = tiered_pnl_r if tiered_pnl_r is not None else 0.0
                if existing_eod is None and eod_pnl_r is not None:
                    patch["eod_pnl_r"] = eod_pnl_r

                _tier_s = f"{patch['tiered_pnl_r']:+.4f}R"
                _eod_s  = f"{eod_pnl_r:+.4f}R" if eod_pnl_r is not None else "n/a"
                _pnl_r  = patch.get("pnl_r_sim")
                _pnl_s  = f"{_pnl_r:+.4f}R" if _pnl_r is not None else "n/a"
                _log(_prefix + f"{actual_outcome}  sim={_pnl_s}  tiered={_tier_s}  eod={_eod_s}"
                     + (" [DRY RUN]" if dry_run else ""))

                if not dry_run:
                    chunk_patches.append(patch)
                else:
                    stats["updated"] += 1

            # ── 3. Flush this chunk to Supabase ───────────────────────────────
            if chunk_patches and not dry_run:
                n = _batch_upsert(chunk_patches)
                stats["updated"] += n
                stats["errors"]  += len(chunk_patches) - n

        if dry_run:
            _log("\n  [dry-run] stopping after one page to avoid infinite loop.")
            break

    return stats


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Backfill 14,594 Pending simulation rows to complete the 4.9-year dataset"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and log results without writing to the database.",
    )
    parser.add_argument(
        "--no-ratelimit",
        action="store_true",
        help="Skip the inter-request sleep (safe on paid Alpaca plans).",
    )
    parser.add_argument(
        "--date-from",
        metavar="YYYY-MM-DD",
        default="",
        help="Only process rows with sim_date >= this date.",
    )
    parser.add_argument(
        "--date-to",
        metavar="YYYY-MM-DD",
        default="",
        help="Only process rows with sim_date <= this date.",
    )
    args = parser.parse_args()

    global _log_file
    try:
        _log_file = open(LOG_PATH, "a")
    except Exception as e:
        print(f"WARNING: could not open log file {LOG_PATH}: {e}")

    dry_label = "  *** DRY RUN — no writes ***" if args.dry_run else ""
    _log("=" * 64)
    _log("  EdgeIQ — Pending Sim Rows Backfill")
    if dry_label:
        _log(dry_label)
    if args.date_from or args.date_to:
        _log(f"  Date range: {args.date_from or 'beginning'} → {args.date_to or 'end'}")
    _log("=" * 64)

    before = count_pending(args.date_from, args.date_to)
    if before >= 0:
        _log(f"\n  Pending rows before this run: {before:,}")

    t0 = time.time()

    stats = backfill_pending(
        dry_run    = args.dry_run,
        rate_limit = not args.no_ratelimit,
        date_from  = args.date_from,
        date_to    = args.date_to,
    )

    elapsed = time.time() - t0

    _log("\n" + "=" * 64)
    _log(f"  DONE in {elapsed:.0f}s  ({elapsed/60:.1f} min)")
    _log(f"  Rows fetched                : {stats['fetched']:,}")
    _log(f"  Rows updated                : {stats['updated']:,}")
    _log(f"  Skipped (no bars, deferred) : {stats['skipped_no_bars']:,}")
    _log(f"  Skipped (no IB data)        : {stats['skipped_no_ib']:,}")
    _log(f"  Non-directional (sentinel)  : {stats['non_directional']:,}")
    _log(f"  Errors                      : {stats['errors']:,}")
    _log("=" * 64)

    if not args.dry_run:
        after = count_pending(args.date_from, args.date_to)
        if after >= 0:
            _log(f"\n  Pending rows remaining: {after:,}")
            if after == 0:
                _log("  All Pending rows resolved — dataset complete.")
            elif stats["skipped_no_bars"] > 0:
                _log(f"  {stats['skipped_no_bars']:,} rows deferred (transient API errors).")
                _log("  Re-run to process them.")
            else:
                _log("  Unexpected residual — investigate before rerunning.")

    if _log_file:
        _log_file.close()


if __name__ == "__main__":
    main()
