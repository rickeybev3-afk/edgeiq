"""
backfill_ib_vwap.py
───────────────────
Backfills ib_range_pct and vwap_at_ib for paper_trades rows that were
inserted before those columns were added.

  ib_range_pct  = (ib_high - ib_low) / open_price * 100
                  Computable entirely from data already stored in the row.

  vwap_at_ib    = VWAP of 5-min bars up to the IB signal cut-off time.
                  Strategy (fastest-first):
                    1. Copy vwap_at_signal from backtest_context_levels if a
                       matching row (ticker + trade_date + scan_type) exists.
                    2. Fetch intraday bars from Alpaca (IEX) and compute VWAP
                       directly, using the same _bars_before_signal + _compute_vwap
                       helpers as paper_trader_bot.py.

After this script finishes, --source paper_trades filter validation will
reflect the full historical sample size instead of only the most recent rows.

Usage
─────
  python backfill_ib_vwap.py                          # auto-discover all users
  python backfill_ib_vwap.py <uid1> [uid2] [uid3]...  # explicit user IDs
  python backfill_ib_vwap.py --retry-vwap             # second-pass VWAP retry

The --retry-vwap flag targets rows where ib_range_pct is already filled but
vwap_at_ib is still NULL (e.g. due to a transient Alpaca API error on the
initial run).  It leaves ib_range_pct untouched and only writes vwap_at_ib.
"""

import sys, os, time, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pytz
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend
from backfill_utils import append_backfill_history

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

ALPACA_KEY    = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET = os.environ.get('ALPACA_SECRET_KEY', '')
DATA_BASE     = 'https://data.alpaca.markets'
ET            = pytz.timezone('America/New_York')

PAGE_SZ     = 1000
MAX_WORKERS = 10   # concurrent update threads; keep low to avoid Alpaca rate limits
SLEEP_AFTER_ALPACA = 0.3   # seconds between per-ticker Alpaca calls


# ─────────────────────────────────────────────────────────────────────────────
# Alpaca intraday helpers (mirrors paper_trader_bot.py logic)
# ─────────────────────────────────────────────────────────────────────────────

def _alpaca_headers() -> dict:
    return {
        'APCA-API-KEY-ID':     ALPACA_KEY,
        'APCA-API-SECRET-KEY': ALPACA_SECRET,
    }


def _fetch_intraday_5min(ticker: str, trade_date_str: str) -> list:
    """Return list of 5-min bar dicts for the regular session on trade_date."""
    dt    = datetime.strptime(trade_date_str, '%Y-%m-%d')
    start = ET.localize(dt.replace(hour=9, minute=30)).isoformat()
    end   = ET.localize(dt.replace(hour=16, minute=0)).isoformat()
    try:
        r = requests.get(
            f'{DATA_BASE}/v2/stocks/{ticker}/bars',
            headers=_alpaca_headers(),
            params={'start': start, 'end': end, 'timeframe': '5Min',
                    'feed': 'iex', 'limit': 1000},
            timeout=20,
        )
        if r.status_code == 422:
            return []
        r.raise_for_status()
        return r.json().get('bars') or []
    except Exception as exc:
        log.warning(f'  intraday bars error {ticker} {trade_date_str}: {exc}')
        return []


def _bars_before_signal(bars: list, trade_date_str: str, scan_type: str) -> list:
    """Filter bars up to the IB signal cut-off (mirrors paper_trader_bot.py)."""
    sig = '09:35:00' if scan_type == 'morning' else '10:47:00'
    dt  = datetime.strptime(trade_date_str, '%Y-%m-%d')
    h, m, s = [int(x) for x in sig.split(':')]
    cutoff  = ET.localize(dt.replace(hour=h, minute=m, second=s))
    result  = []
    for b in bars:
        try:
            bar_dt = datetime.fromisoformat(b['t'].replace('Z', '+00:00'))
            if bar_dt <= cutoff:
                result.append(b)
        except Exception:
            continue
    return result


def _compute_vwap(bars: list) -> float | None:
    """Volume-weighted average price of the given bars (mirrors paper_trader_bot.py)."""
    total_vol = sum(b['v'] for b in bars if b.get('v', 0) > 0)
    if not total_vol:
        return None
    return sum(b.get('vw', b['c']) * b['v'] for b in bars if b.get('v', 0) > 0) / total_vol


# ─────────────────────────────────────────────────────────────────────────────
# backtest_context_levels cache (loaded once per run)
# ─────────────────────────────────────────────────────────────────────────────

def _load_context_levels_cache() -> dict:
    """
    Return dict keyed by (ticker, trade_date, scan_type) -> vwap_at_signal.
    Fetches all rows from backtest_context_levels that have a non-null
    vwap_at_signal in a single paginated scan.
    """
    if not backend.supabase:
        return {}
    cache: dict = {}
    offset = 0
    print("  Loading backtest_context_levels cache…", end="", flush=True)
    while True:
        try:
            resp = (
                backend.supabase.table('backtest_context_levels')
                .select('ticker,trade_date,scan_type,vwap_at_signal')
                .not_.is_('vwap_at_signal', 'null')
                .range(offset, offset + PAGE_SZ - 1)
                .execute()
            )
        except Exception as exc:
            print(f"\n  WARNING: could not load context levels cache: {exc}")
            return {}
        rows = resp.data or []
        for row in rows:
            key = (
                row.get('ticker', ''),
                str(row.get('trade_date', '')),
                row.get('scan_type', 'morning'),
            )
            cache[key] = row.get('vwap_at_signal')
        if len(rows) < PAGE_SZ:
            break
        offset += PAGE_SZ
    print(f" {len(cache)} rows cached.")
    return cache


# ─────────────────────────────────────────────────────────────────────────────
# User discovery (same pattern as run_sim_backfill.py)
# ─────────────────────────────────────────────────────────────────────────────

def discover_user_ids() -> list[str]:
    if not backend.supabase:
        return []
    uid_set: set[str] = set()
    offset = 0
    print("  Scanning paper_trades for user IDs…", end="", flush=True)
    rows_scanned = 0
    try:
        while True:
            resp = (
                backend.supabase.table('paper_trades')
                .select('user_id')
                .range(offset, offset + PAGE_SZ - 1)
                .execute()
            )
            rows = resp.data or []
            for row in rows:
                uid = row.get('user_id')
                if uid:
                    uid_set.add(uid)
            rows_scanned += len(rows)
            if len(rows) < PAGE_SZ:
                break
            offset += PAGE_SZ
    except Exception as exc:
        print(f"\n  ERROR scanning for user IDs: {exc}")
        sys.exit(1)
    print(f" {rows_scanned} rows scanned.")
    return sorted(uid_set)


# ─────────────────────────────────────────────────────────────────────────────
# Core patch computation
# ─────────────────────────────────────────────────────────────────────────────

def _compute_patch(row: dict, context_cache: dict) -> dict | None:
    """
    Compute the ib_range_pct and/or vwap_at_ib patch for a single paper_trades row.

    Returns a dict with whichever fields could be computed, or None when neither
    field can be filled.  Uncomputable rows are NOT written to the database so
    ib_range_pct stays NULL — this keeps them out of filter validation cohorts.
    """
    patch: dict = {}

    # ── ib_range_pct ──────────────────────────────────────────────────────────
    ib_h   = float(row.get('ib_high')    or 0)
    ib_l   = float(row.get('ib_low')     or 0)
    open_p = float(row.get('open_price') or 0)
    if ib_h > ib_l > 0 and open_p > 0:
        patch['ib_range_pct'] = round((ib_h - ib_l) / open_p * 100, 4)

    # ── vwap_at_ib ────────────────────────────────────────────────────────────
    ticker     = row.get('ticker', '')
    trade_date = str(row.get('trade_date', ''))
    scan_type  = row.get('scan_type') or 'morning'

    # Strategy 1: copy from backtest_context_levels cache (free — no API call)
    ctx_key  = (ticker, trade_date, scan_type)
    ctx_vwap = context_cache.get(ctx_key)
    if ctx_vwap is not None:
        patch['vwap_at_ib'] = round(float(ctx_vwap), 4)
    elif ticker and trade_date:
        # Strategy 2: fetch intraday bars from Alpaca and compute
        bars    = _fetch_intraday_5min(ticker, trade_date)
        time.sleep(SLEEP_AFTER_ALPACA)
        bars_at = _bars_before_signal(bars, trade_date, scan_type)
        vwap    = _compute_vwap(bars_at)
        if vwap is not None:
            patch['vwap_at_ib'] = round(vwap, 4)

    return patch if patch else None


def _update_one(row_id, patch: dict):
    backend.supabase.table('paper_trades').update(patch).eq('id', row_id).execute()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Main backfill loop
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_all_null_rows(user_id: str) -> list:
    """
    Pre-load all paper_trades rows where ib_range_pct IS NULL into memory
    before any updates are made.  This allows standard offset-based pagination
    (which is stable when the underlying set does not change during iteration)
    and means ib_range_pct stays NULL for truly uncomputable rows — no sentinel
    values are written, so filter validation cohorts remain uncontaminated.
    """
    all_rows: list = []
    offset = 0
    print("  Pre-loading candidate rows (ib_range_pct IS NULL)…", end="", flush=True)
    while True:
        try:
            resp = (
                backend.supabase.table('paper_trades')
                .select('id,ticker,trade_date,scan_type,ib_high,ib_low,open_price')
                .eq('user_id', user_id)
                .is_('ib_range_pct', 'null')
                .order('id')
                .range(offset, offset + PAGE_SZ - 1)
                .execute()
            )
        except Exception as exc:
            print(f"\n  Fetch error at offset {offset}: {exc}")
            break
        rows = resp.data or []
        all_rows.extend(rows)
        if len(rows) < PAGE_SZ:
            break
        offset += PAGE_SZ
    print(f" {len(all_rows)} rows.")
    return all_rows


def backfill_user(user_id: str, context_cache: dict):
    if not backend.supabase:
        print("No Supabase connection.")
        return 0

    print(f"\n{'='*60}")
    print(f"  Backfilling paper_trades  (user {user_id})")
    print(f"{'='*60}")

    # Phase 1: Pre-load the entire candidate set before making any writes.
    # Pagination is safe here because we are only reading.
    candidate_rows = _fetch_all_null_rows(user_id)
    if not candidate_rows:
        print("  Nothing to backfill.")
        return 0

    # Phase 2: Compute patches (Alpaca calls are made sequentially to stay
    # within rate limits; context-cache hits need no API call at all).
    updates: list[tuple] = []
    skipped = 0
    for row in candidate_rows:
        patch = _compute_patch(row, context_cache)
        if patch:
            updates.append((row['id'], patch))
        else:
            skipped += 1

    print(f"  {len(updates)} rows have computable data | {skipped} rows skipped "
          f"(missing IB or VWAP data — ib_range_pct stays NULL)")

    # Phase 3: Send DB updates concurrently.
    total_updated = 0
    total_errors  = 0
    BATCH = PAGE_SZ  # send updates in chunks to avoid overwhelming the thread pool

    for i in range(0, len(updates), BATCH):
        chunk = updates[i:i + BATCH]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(_update_one, row_id, patch): row_id
                for row_id, patch in chunk
            }
            for fut in as_completed(futures):
                try:
                    fut.result()
                    total_updated += 1
                except Exception as exc:
                    total_errors += 1
                    if total_errors <= 5:
                        print(f"  Update error: {exc}")
        print(f"  [{i + len(chunk):5d}/{len(updates)}] rows updated…")

    print(f"\n  Total: {total_updated} updated | {total_errors} errors | {skipped} skipped")
    return total_updated


def _count_null(user_id: str, column: str) -> int:
    """Return the count of paper_trades rows where column IS NULL for this user."""
    offset, total = 0, 0
    while True:
        resp = (
            backend.supabase.table('paper_trades')
            .select('id')
            .eq('user_id', user_id)
            .is_(column, 'null')
            .range(offset, offset + PAGE_SZ - 1)
            .execute()
        )
        rows = resp.data or []
        total += len(rows)
        if len(rows) < PAGE_SZ:
            break
        offset += PAGE_SZ
    return total


def _count_total(user_id: str) -> int:
    """Return the total row count in paper_trades for this user."""
    offset, total = 0, 0
    while True:
        resp = (
            backend.supabase.table('paper_trades')
            .select('id')
            .eq('user_id', user_id)
            .range(offset, offset + PAGE_SZ - 1)
            .execute()
        )
        rows = resp.data or []
        total += len(rows)
        if len(rows) < PAGE_SZ:
            break
        offset += PAGE_SZ
    return total


def print_summary(user_id: str):
    """Print server-side null counts after backfill for easy verification."""
    if not backend.supabase:
        return
    print(f"\n{'='*60}")
    print(f"  POST-BACKFILL SUMMARY  (user {user_id})")
    print(f"{'='*60}")
    try:
        total        = _count_total(user_id)
        null_ibpct   = _count_null(user_id, 'ib_range_pct')
        null_vwap    = _count_null(user_id, 'vwap_at_ib')
        filled_ibpct = total - null_ibpct
        filled_vwap  = total - null_vwap
        print(f"  paper_trades total rows  : {total}")
        print(f"  ib_range_pct  filled     : {filled_ibpct} / {total}  ({null_ibpct} still NULL)")
        print(f"  vwap_at_ib    filled     : {filled_vwap}  / {total}  ({null_vwap}  still NULL)")
        if null_ibpct > 0:
            print(f"\n  NOTE: {null_ibpct} rows could not be computed (degenerate IB or "
                  f"missing open_price). These are excluded from filter validation "
                  f"cohorts, which is the correct behaviour.")
        if null_vwap > 0:
            print(f"\n  NOTE: {null_vwap} rows have vwap_at_ib=NULL (Alpaca data unavailable "
                  f"or transient error). Re-run backfill_ib_vwap.py to retry: these rows "
                  f"will be re-attempted if ib_range_pct IS NULL still (i.e. the row was "
                  f"skipped entirely); rows where only vwap is missing are not retried "
                  f"automatically.")
    except Exception as exc:
        print(f"  Summary error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def backfill_vwap_only(user_id: str, context_cache: dict) -> int:
    """
    Second-pass mode: retry vwap_at_ib for rows where ib_range_pct is already
    filled but vwap_at_ib is still NULL (e.g. due to a transient Alpaca error
    during the main run).

    Activated via the --retry-vwap CLI flag.
    """
    if not backend.supabase:
        return 0

    print(f"\n{'='*60}")
    print(f"  VWAP-only retry  (user {user_id})")
    print(f"{'='*60}")

    # Pre-load rows where ib_range_pct filled but vwap_at_ib still NULL.
    all_rows: list = []
    offset = 0
    print("  Pre-loading rows with vwap_at_ib IS NULL…", end="", flush=True)
    while True:
        try:
            resp = (
                backend.supabase.table('paper_trades')
                .select('id,ticker,trade_date,scan_type')
                .eq('user_id', user_id)
                .not_.is_('ib_range_pct', 'null')
                .is_('vwap_at_ib', 'null')
                .order('id')
                .range(offset, offset + PAGE_SZ - 1)
                .execute()
            )
        except Exception as exc:
            print(f"\n  Fetch error at offset {offset}: {exc}")
            break
        rows = resp.data or []
        all_rows.extend(rows)
        if len(rows) < PAGE_SZ:
            break
        offset += PAGE_SZ
    print(f" {len(all_rows)} rows.")

    if not all_rows:
        print("  Nothing to retry.")
        return 0

    updates: list[tuple] = []
    for row in all_rows:
        ticker     = row.get('ticker', '')
        trade_date = str(row.get('trade_date', ''))
        scan_type  = row.get('scan_type') or 'morning'

        ctx_vwap = context_cache.get((ticker, trade_date, scan_type))
        if ctx_vwap is not None:
            vwap = round(float(ctx_vwap), 4)
        else:
            bars    = _fetch_intraday_5min(ticker, trade_date)
            time.sleep(SLEEP_AFTER_ALPACA)
            bars_at = _bars_before_signal(bars, trade_date, scan_type)
            raw     = _compute_vwap(bars_at)
            vwap    = round(raw, 4) if raw is not None else None

        if vwap is not None:
            updates.append((row['id'], {'vwap_at_ib': vwap}))

    still_missing = len(all_rows) - len(updates)
    print(f"  {len(updates)} rows have VWAP data | {still_missing} still unresolvable")

    total_updated = 0
    total_errors  = 0
    for i in range(0, len(updates), PAGE_SZ):
        chunk = updates[i:i + PAGE_SZ]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(_update_one, row_id, patch): row_id
                for row_id, patch in chunk
            }
            for fut in as_completed(futures):
                try:
                    fut.result()
                    total_updated += 1
                except Exception as exc:
                    total_errors += 1
                    if total_errors <= 5:
                        print(f"  Update error: {exc}")

    print(f"\n  VWAP retry: {total_updated} updated | {total_errors} errors")
    return total_updated


if __name__ == '__main__':
    print("EdgeIQ — IB Range % & VWAP Backfill for paper_trades")
    print("=" * 60)

    args      = sys.argv[1:]
    retry_vwap = '--retry-vwap' in args
    if retry_vwap:
        args = [a for a in args if a != '--retry-vwap']
        print("Mode: --retry-vwap (second pass — fill vwap_at_ib for rows with ib_range_pct already set)")
    else:
        print("Mode: full backfill (ib_range_pct IS NULL candidates)")

    if args:
        seen: dict = {}
        for uid in args:
            seen[uid] = None
        user_ids = list(seen)
        print(f"Using {len(user_ids)} user ID(s) from command-line arguments.")
    else:
        print("No user IDs specified — querying database for all distinct users…")
        user_ids = discover_user_ids()
        if not user_ids:
            print("No users found in paper_trades. Nothing to backfill.")
            sys.exit(0)
        print(f"Found {len(user_ids)} user(s): {user_ids}")

    # Load context-levels VWAP cache once (shared across all users)
    context_cache = _load_context_levels_cache()

    t0 = time.time()

    for uid in user_ids:
        print(f"\n{'#'*60}")
        print(f"  Processing user: {uid}")
        print(f"{'#'*60}")
        if retry_vwap:
            backfill_vwap_only(uid, context_cache)
        else:
            backfill_user(uid, context_cache)

    elapsed = time.time() - t0

    for uid in user_ids:
        print_summary(uid)

    mode_label = "VWAP retry" if retry_vwap else "Backfill"
    print(f"\n✅ {mode_label} complete for {len(user_ids)} user(s) in {elapsed:.0f}s")

    append_backfill_history(
        script='backfill_ib_vwap',
        health={
            'mode': 'retry_vwap' if retry_vwap else 'full',
            'users_processed': len(user_ids),
            'elapsed_s': round(elapsed, 1),
        },
        logger=log,
    )
