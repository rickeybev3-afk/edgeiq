"""
backfill_sr_levels.py
─────────────────────
Backfills nearest_resistance and nearest_support onto paper_trades rows that
were inserted before those columns were added.

Strategy (fastest-first per row):
  1. Look up backtest_context_levels for the matching (ticker, trade_date,
     scan_type) key.  If found, derive nearest_resistance / nearest_support
     from prev_day_high, prev_day_low and vwap_at_signal (already computed).
  2. If the row already has vwap_at_ib filled, use that as the VWAP component
     alongside Alpaca prev-day bars (one batch call per trade date).
  3. Fall back to fetching both prev-day bars and intraday 5-min bars from
     Alpaca and computing VWAP from scratch.

The IB break price is derived from ib_high / ib_low + the predicted column:
  predicted == 'Bullish Break' → ib_break = ib_high
  anything else                → ib_break = ib_low

Usage
─────
  python backfill_sr_levels.py                          # all users auto-discovered
  python backfill_sr_levels.py <uid1> [uid2] …          # explicit user IDs
"""

import os, sys, time, logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

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

PAGE_SZ            = 1000
MAX_WORKERS        = 8
SLEEP_AFTER_ALPACA = 0.4   # seconds between per-ticker Alpaca calls
BATCH_SIZE         = 10    # tickers per Alpaca multi-symbol daily-bars request
RETRY_ATTEMPTS     = 3
RETRY_BASE_DELAY   = 2


# ─────────────────────────────────────────────────────────────────────────────
# Alpaca helpers  (mirrors backfill_context_levels.py)
# ─────────────────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        'APCA-API-KEY-ID':     ALPACA_KEY,
        'APCA-API-SECRET-KEY': ALPACA_SECRET,
    }


def _get_with_retry(url, **kwargs):
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        r = requests.get(url, **kwargs)
        if r.status_code == 429:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log.warning(
                f'Alpaca 429 on {url!r} (attempt {attempt}/{RETRY_ATTEMPTS}) '
                f'— sleeping {delay}s …'
            )
            time.sleep(delay)
            if attempt == RETRY_ATTEMPTS:
                raise RuntimeError(f'HTTP 429 persisted after {RETRY_ATTEMPTS} retries for {url!r}')
            continue
        return r
    raise RuntimeError(f'HTTP 429 persisted after {RETRY_ATTEMPTS} retries for {url!r}')


def _get_daily_bars(symbols: list, start: str, end: str) -> dict:
    """Batch daily bars for *symbols* between *start* and *end* (YYYY-MM-DD)."""
    params = {
        'symbols':   ','.join(symbols),
        'start':     start,
        'end':       end,
        'timeframe': '1Day',
        'feed':      'iex',
        'limit':     10000,
    }
    r = _get_with_retry(
        f'{DATA_BASE}/v2/stocks/bars', headers=_headers(), params=params, timeout=30
    )
    r.raise_for_status()
    data = r.json()
    out  = data.get('bars') or {}
    while data.get('next_page_token'):
        params['page_token'] = data['next_page_token']
        r = _get_with_retry(
            f'{DATA_BASE}/v2/stocks/bars', headers=_headers(), params=params, timeout=30
        )
        r.raise_for_status()
        data = r.json()
        for sym, bars in (data.get('bars') or {}).items():
            out.setdefault(sym, []).extend(bars)
    return out


def _get_intraday_bars(symbol: str, trade_date_str: str) -> list:
    """5-min bars for *symbol* on *trade_date_str* (regular session, IEX)."""
    dt    = datetime.strptime(trade_date_str, '%Y-%m-%d')
    start = ET.localize(dt.replace(hour=9,  minute=30)).isoformat()
    end   = ET.localize(dt.replace(hour=16, minute=0)).isoformat()
    params = {
        'start':     start,
        'end':       end,
        'timeframe': '5Min',
        'feed':      'iex',
        'limit':     10000,
    }
    r = _get_with_retry(
        f'{DATA_BASE}/v2/stocks/{symbol}/bars',
        headers=_headers(), params=params, timeout=30,
    )
    if r.status_code == 422:
        return []
    r.raise_for_status()
    data = r.json()
    bars = data.get('bars') or []
    while data.get('next_page_token'):
        params['page_token'] = data['next_page_token']
        r = _get_with_retry(
            f'{DATA_BASE}/v2/stocks/{symbol}/bars',
            headers=_headers(), params=params, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        bars.extend(data.get('bars') or [])
    return bars


def _bars_before_signal(bars: list, trade_date_str: str, scan_type: str) -> list:
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


def _compute_vwap(bars: list):
    total_vol = sum(b['v'] for b in bars if b.get('v', 0) > 0)
    if not total_vol:
        return None
    return sum(b.get('vw', b['c']) * b['v'] for b in bars if b.get('v', 0) > 0) / total_vol


# ─────────────────────────────────────────────────────────────────────────────
# S/R derivation  (mirrors paper_trader_bot.py log_context_levels)
# ─────────────────────────────────────────────────────────────────────────────

def _nearest_levels(levels: list, ib_break: float):
    """Return (nearest_resistance, nearest_support) from key levels + IB break."""
    valid = [l for l in levels if l and l > 0]
    above = [l for l in valid if l > ib_break]
    below = [l for l in valid if l <= ib_break]
    resistance = round(min(above), 4) if above else None
    support    = round(max(below), 4) if below else None
    return resistance, support


# ─────────────────────────────────────────────────────────────────────────────
# backtest_context_levels cache  (loaded once per run)
# ─────────────────────────────────────────────────────────────────────────────

def _load_context_levels_cache() -> dict:
    """
    Return dict keyed by (ticker, trade_date, scan_type) →
    {prev_day_high, prev_day_low, vwap_at_signal}.
    Loads all rows in a single paginated scan.
    """
    if not backend.supabase:
        return {}
    cache: dict = {}
    offset = 0
    print('  Loading backtest_context_levels cache…', end='', flush=True)
    while True:
        try:
            resp = (
                backend.supabase.table('backtest_context_levels')
                .select('ticker,trade_date,scan_type,prev_day_high,prev_day_low,vwap_at_signal')
                .range(offset, offset + PAGE_SZ - 1)
                .execute()
            )
        except Exception as exc:
            print(f'\n  WARNING: could not load context levels cache: {exc}')
            return {}
        rows = resp.data or []
        for row in rows:
            key = (
                row.get('ticker', ''),
                str(row.get('trade_date', '')),
                row.get('scan_type', 'morning'),
            )
            cache[key] = {
                'prev_day_high': row.get('prev_day_high'),
                'prev_day_low':  row.get('prev_day_low'),
                'vwap_at_signal': row.get('vwap_at_signal'),
            }
        if len(rows) < PAGE_SZ:
            break
        offset += PAGE_SZ
    print(f' {len(cache)} rows cached.')
    return cache


# ─────────────────────────────────────────────────────────────────────────────
# User discovery
# ─────────────────────────────────────────────────────────────────────────────

def _discover_user_ids() -> list:
    if not backend.supabase:
        return []
    uid_set: set = set()
    offset = 0
    print('  Scanning paper_trades for user IDs…', end='', flush=True)
    rows_scanned = 0
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
    print(f' {rows_scanned} rows scanned.')
    return sorted(uid_set)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate row fetching
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_null_rows(user_id: str) -> list:
    """
    Pre-load all paper_trades rows where nearest_resistance OR nearest_support
    is NULL, along with the columns needed to derive levels.
    """
    all_rows: list = []
    offset = 0
    print('  Pre-loading candidate rows (nearest_resistance IS NULL or nearest_support IS NULL)…',
          end='', flush=True)
    # Supabase Python client doesn't expose OR directly via .is_(); use two passes
    # (resistance null) then (support null), de-duped by id.
    seen_ids: set = set()

    for col in ('nearest_resistance', 'nearest_support'):
        offset = 0
        while True:
            try:
                resp = (
                    backend.supabase.table('paper_trades')
                    .select(
                        'id,ticker,trade_date,scan_type,'
                        'ib_high,ib_low,predicted,vwap_at_ib,'
                        'nearest_resistance,nearest_support'
                    )
                    .eq('user_id', user_id)
                    .is_(col, 'null')
                    .order('id')
                    .range(offset, offset + PAGE_SZ - 1)
                    .execute()
                )
            except Exception as exc:
                print(f'\n  Fetch error (col={col}, offset={offset}): {exc}')
                break
            rows = resp.data or []
            for row in rows:
                if row['id'] not in seen_ids:
                    seen_ids.add(row['id'])
                    all_rows.append(row)
            if len(rows) < PAGE_SZ:
                break
            offset += PAGE_SZ

    print(f' {len(all_rows)} rows.')
    return all_rows


# ─────────────────────────────────────────────────────────────────────────────
# Patch computation (per row)
# ─────────────────────────────────────────────────────────────────────────────

def _ib_break(row: dict):
    """Return the IB break price for a paper_trades row, or None."""
    predicted = str(row.get('predicted') or '').strip()
    ib_high   = float(row.get('ib_high') or 0) or None
    ib_low    = float(row.get('ib_low')  or 0) or None
    if predicted == 'Bullish Break':
        return ib_high
    return ib_low


def _compute_patch(
    row: dict,
    context_cache: dict,
    prev_day_lookup: dict,
) -> dict | None:
    """
    Return a patch dict with nearest_resistance and/or nearest_support, or None.

    *prev_day_lookup* maps ticker → {high, low} and is pre-populated for the
    trade date of this row when Alpaca data was fetched.
    """
    ticker     = row.get('ticker', '')
    trade_date = str(row.get('trade_date', ''))
    scan_type  = row.get('scan_type') or 'morning'

    break_price = _ib_break(row)
    if not break_price:
        return None

    # --- VWAP: prefer paper_trades.vwap_at_ib already filled; else cache ---
    ctx      = context_cache.get((ticker, trade_date, scan_type), {})
    vwap_val = None
    if row.get('vwap_at_ib') is not None:
        vwap_val = float(row['vwap_at_ib'])
    elif ctx.get('vwap_at_signal') is not None:
        vwap_val = float(ctx['vwap_at_signal'])
    # (If still None after Alpaca fetch below, levels use only prev-day H/L)

    # --- Prev-day H/L: prefer context cache; fall back to Alpaca lookup ---
    prev_high = ctx.get('prev_day_high') or (prev_day_lookup.get(ticker) or {}).get('high')
    prev_low  = ctx.get('prev_day_low')  or (prev_day_lookup.get(ticker) or {}).get('low')

    key_levels = [l for l in [prev_high, prev_low, vwap_val] if l]
    if not key_levels:
        return None

    resistance, support = _nearest_levels(key_levels, break_price)

    patch: dict = {}
    if row.get('nearest_resistance') is None and resistance is not None:
        patch['nearest_resistance'] = resistance
    if row.get('nearest_support') is None and support is not None:
        patch['nearest_support'] = support

    return patch if patch else None


def _update_one(row_id, patch: dict):
    backend.supabase.table('paper_trades').update(patch).eq('id', row_id).execute()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Main backfill loop (per user)
# ─────────────────────────────────────────────────────────────────────────────

def backfill_user(user_id: str, context_cache: dict) -> int:
    if not backend.supabase:
        print('No Supabase connection.')
        return 0

    print(f"\n{'='*60}")
    print(f'  Backfilling paper_trades S/R levels  (user {user_id})')
    print(f"{'='*60}")

    candidate_rows = _fetch_null_rows(user_id)
    if not candidate_rows:
        print('  Nothing to backfill.')
        return 0

    # ── Group rows by trade_date to batch-fetch prev-day bars from Alpaca ────
    # Only fetch Alpaca daily bars for rows where prev-day H/L is not already
    # in the context_cache (VWAP-only gaps are handled separately below).
    need_prev_day: dict = defaultdict(set)   # trade_date → set of tickers
    for row in candidate_rows:
        ticker     = row.get('ticker', '')
        trade_date = str(row.get('trade_date', ''))
        scan_type  = row.get('scan_type') or 'morning'
        ctx        = context_cache.get((ticker, trade_date, scan_type), {})
        has_prev   = ctx.get('prev_day_high') and ctx.get('prev_day_low')
        if not has_prev:
            need_prev_day[trade_date].add(ticker)

    # ── Alpaca prev-day bars (batch per date) ─────────────────────────────────
    # Keyed by (trade_date, ticker) → {high, low}
    alpaca_prev: dict = {}
    if need_prev_day:
        print(f'  Fetching Alpaca prev-day bars for {len(need_prev_day)} date(s) '
              f'({sum(len(v) for v in need_prev_day.values())} ticker-date combos)…')
    for trade_date, tickers in sorted(need_prev_day.items()):
        ticker_list = list(tickers)
        prev_start  = (datetime.strptime(trade_date, '%Y-%m-%d') - timedelta(days=5)).strftime('%Y-%m-%d')
        prev_end    = (datetime.strptime(trade_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        for i in range(0, len(ticker_list), BATCH_SIZE):
            batch = ticker_list[i:i + BATCH_SIZE]
            try:
                daily = _get_daily_bars(batch, prev_start, prev_end)
                for sym, bars in daily.items():
                    if bars:
                        last = bars[-1]
                        alpaca_prev[(trade_date, sym)] = {'high': last['h'], 'low': last['l']}
            except Exception as exc:
                log.warning(f'  prev-day bars error ({trade_date} batch {batch}): {exc}')
            time.sleep(SLEEP_AFTER_ALPACA)

    # ── For rows still missing VWAP, fetch intraday bars ─────────────────────
    # Identify which ticker+date pairs still need a VWAP fetch.
    need_vwap: set = set()
    for row in candidate_rows:
        ticker     = row.get('ticker', '')
        trade_date = str(row.get('trade_date', ''))
        scan_type  = row.get('scan_type') or 'morning'
        ctx        = context_cache.get((ticker, trade_date, scan_type), {})
        if row.get('vwap_at_ib') is None and ctx.get('vwap_at_signal') is None:
            need_vwap.add((ticker, trade_date, scan_type))

    # Fetch intraday bars and compute VWAP; store in a separate dict.
    # keyed by (ticker, trade_date, scan_type) → vwap float
    alpaca_vwap: dict = {}
    if need_vwap:
        print(f'  Fetching Alpaca intraday bars for {len(need_vwap)} ticker-date-scantype combos…')
    for ticker, trade_date, scan_type in sorted(need_vwap):
        try:
            bars    = _get_intraday_bars(ticker, trade_date)
            bars_at = _bars_before_signal(bars, trade_date, scan_type)
            vwap    = _compute_vwap(bars_at)
            if vwap is not None:
                alpaca_vwap[(ticker, trade_date, scan_type)] = vwap
        except Exception as exc:
            log.warning(f'  intraday bars error {ticker} {trade_date}: {exc}')
        time.sleep(SLEEP_AFTER_ALPACA)

    # ── Augment context_cache with Alpaca VWAP so _compute_patch can find it ─
    # We create a local enriched cache view (shallow copy + extras).
    enriched_ctx: dict = dict(context_cache)
    for (ticker, trade_date, scan_type), vwap in alpaca_vwap.items():
        key = (ticker, trade_date, scan_type)
        entry = dict(enriched_ctx.get(key) or {})
        entry.setdefault('vwap_at_signal', vwap)
        enriched_ctx[key] = entry

    # ── Build prev_day_lookup: trade_date → {ticker → {high, low}} ───────────
    # Merge Alpaca results so _compute_patch can consume them.
    prev_day_by_ticker: dict = defaultdict(dict)
    for (trade_date, ticker), hl in alpaca_prev.items():
        prev_day_by_ticker[f'{trade_date}:{ticker}'] = hl

    # ── Compute patches ───────────────────────────────────────────────────────
    updates: list = []
    skipped = 0
    for row in candidate_rows:
        trade_date = str(row.get('trade_date', ''))
        ticker     = row.get('ticker', '')
        prev_lookup = {ticker: prev_day_by_ticker.get(f'{trade_date}:{ticker}', {})}
        patch = _compute_patch(row, enriched_ctx, prev_lookup)
        if patch:
            updates.append((row['id'], patch))
        else:
            skipped += 1

    print(f'  {len(updates)} rows have computable S/R | {skipped} skipped (missing IB or levels)')

    if not updates:
        return 0

    # ── Write updates concurrently ────────────────────────────────────────────
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
                        print(f'  Update error: {exc}')
        print(f'  [{i + len(chunk):5d}/{len(updates)}] rows updated…')

    print(f'\n  Total: {total_updated} updated | {total_errors} errors | {skipped} skipped')
    return total_updated


# ─────────────────────────────────────────────────────────────────────────────
# Post-backfill summary
# ─────────────────────────────────────────────────────────────────────────────

def _count_null(user_id: str, col: str) -> int:
    offset, total = 0, 0
    while True:
        resp = (
            backend.supabase.table('paper_trades')
            .select('id')
            .eq('user_id', user_id)
            .is_(col, 'null')
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
    if not backend.supabase:
        return
    print(f"\n{'='*60}")
    print(f'  POST-BACKFILL SUMMARY  (user {user_id})')
    print(f"{'='*60}")
    try:
        total      = _count_total(user_id)
        null_res   = _count_null(user_id, 'nearest_resistance')
        null_sup   = _count_null(user_id, 'nearest_support')
        filled_res = total - null_res
        filled_sup = total - null_sup
        print(f'  paper_trades total rows   : {total}')
        print(f'  nearest_resistance filled : {filled_res} / {total}  ({null_res} still NULL)')
        print(f'  nearest_support    filled : {filled_sup} / {total}  ({null_sup} still NULL)')
        if null_res > 0 or null_sup > 0:
            print(
                f'\n  NOTE: remaining NULLs indicate rows where the IB break price '
                f'could not be determined or no key levels (prev-day H/L, VWAP) '
                f'were available from either the cache or Alpaca.'
            )
    except Exception as exc:
        print(f'  Summary error: {exc}')


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('EdgeIQ — nearest_resistance / nearest_support Backfill for paper_trades')
    print('=' * 72)

    args = sys.argv[1:]
    if args:
        seen: dict = {}
        for uid in args:
            seen[uid] = None
        user_ids = list(seen)
        print(f'Using {len(user_ids)} user ID(s) from command-line arguments.')
    else:
        print('No user IDs specified — querying database for all distinct users…')
        user_ids = _discover_user_ids()
        if not user_ids:
            print('No users found in paper_trades. Nothing to backfill.')
            sys.exit(0)
        print(f'Found {len(user_ids)} user(s): {user_ids}')

    context_cache = _load_context_levels_cache()

    import time as _time
    t0 = _time.time()
    grand_total = 0

    for uid in user_ids:
        print(f"\n{'#'*72}")
        print(f'  Processing user: {uid}')
        print(f"{'#'*72}")
        grand_total += backfill_user(uid, context_cache)

    elapsed = _time.time() - t0

    for uid in user_ids:
        print_summary(uid)

    print(f'\n✅ Backfill complete: {grand_total} rows updated across '
          f'{len(user_ids)} user(s) in {elapsed:.0f}s')

    append_backfill_history(
        script='backfill_sr_levels',
        health={
            'users_processed': len(user_ids),
            'rows_updated':    grand_total,
            'elapsed_s':       round(elapsed, 1),
        },
        logger=log,
    )
