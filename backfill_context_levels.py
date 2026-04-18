"""
backfill_context_levels.py
──────────────────────────
Pulls intraday context levels for every settled breakout in backtest_sim_runs:
  - prev_day_high / prev_day_low  (S/R from prior session)
  - premarket_high / premarket_low (gap fill levels, SIP-only; NULL on IEX)
  - vwap_at_signal                (VWAP from 9:30 AM to signal time)
  - macd_line / macd_signal_line / macd_histogram / macd_direction
  - nearest_resistance / nearest_support (closest level above/below IB break)

Uses Alpaca historical data API (IEX free feed).
Run via: python backfill_context_levels.py
"""

import os, sys, time, math, logging
from datetime import datetime, date, timedelta
from collections import defaultdict
import requests
import pytz


class RateLimitExhausted(Exception):
    """Raised when all retry attempts for an Alpaca 429 response are exhausted."""

sys.path.insert(0, '.')
import backend

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

ALPACA_KEY    = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET = os.environ.get('ALPACA_SECRET_KEY', '')
DATA_BASE     = 'https://data.alpaca.markets'
ET            = pytz.timezone('America/New_York')
SUPABASE      = backend.supabase
USER_ID       = os.environ.get('OWNER_USER_ID', '')   # emergency fallback only — do not set for normal runs

MORNING_SIGNAL_ET  = '09:35:00'
INTRADAY_SIGNAL_ET = '10:47:00'

BATCH_SIZE    = 10   # tickers per Alpaca multi-symbol request
# Recommended sleep between Alpaca API calls:
#   Free IEX feed  → 0.4 s (≈ 150 req/min, well under the 200 req/min limit)
#   Paid IEX feed  → 0.1 s (higher rate-limit allowance)
SLEEP_BETWEEN = 0.4

RETRY_ATTEMPTS    = 3   # number of retries on HTTP 429
RETRY_BASE_DELAY  = 2   # initial back-off in seconds; doubles each attempt (2 s, 4 s, 8 s)

# ─────────────────────────────────────────────────────────────────────────────
# Alpaca helpers
# ─────────────────────────────────────────────────────────────────────────────

def _headers():
    return {
        'APCA-API-KEY-ID':     ALPACA_KEY,
        'APCA-API-SECRET-KEY': ALPACA_SECRET,
    }

def _get_with_retry(url, **kwargs):
    """
    GET *url* with exponential back-off on HTTP 429 (Too Many Requests).
    Raises RateLimitExhausted after RETRY_ATTEMPTS unsuccessful attempts.
    All other non-2xx responses are propagated immediately via raise_for_status.
    """
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        r = requests.get(url, **kwargs)
        if r.status_code == 429:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log.warning(
                f'Alpaca 429 rate-limit on {url!r} '
                f'(attempt {attempt}/{RETRY_ATTEMPTS}) — sleeping {delay}s …'
            )
            time.sleep(delay)
            if attempt == RETRY_ATTEMPTS:
                raise RateLimitExhausted(
                    f'HTTP 429 persisted after {RETRY_ATTEMPTS} retries for {url!r}'
                )
            continue
        return r
    raise RateLimitExhausted(f'HTTP 429 persisted after {RETRY_ATTEMPTS} retries for {url!r}')

def get_daily_bars(symbols, start, end):
    """Return dict[ticker] = list of daily bar dicts."""
    params = {
        'symbols':   ','.join(symbols),
        'start':     start,
        'end':       end,
        'timeframe': '1Day',
        'feed':      'iex',
        'limit':     10000,
    }
    r = _get_with_retry(f'{DATA_BASE}/v2/stocks/bars', headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    out = data.get('bars', {}) or {}
    # handle pagination (next_page_token)
    while data.get('next_page_token'):
        params['page_token'] = data['next_page_token']
        r = _get_with_retry(f'{DATA_BASE}/v2/stocks/bars', headers=_headers(), params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for sym, bars in (data.get('bars') or {}).items():
            out.setdefault(sym, []).extend(bars)
    return out

def get_intraday_bars(symbol, trade_date_str, timeframe='5Min'):
    """
    Return list of intraday bar dicts for one ticker on one date.
    Covers 9:30 AM – 4:00 PM ET (regular session) using IEX feed.
    Raises RateLimitExhausted if Alpaca returns HTTP 429 on all retry attempts.
    """
    dt = datetime.strptime(trade_date_str, '%Y-%m-%d')
    start_utc = ET.localize(dt.replace(hour=9, minute=30)).isoformat()
    end_utc   = ET.localize(dt.replace(hour=16, minute=0)).isoformat()
    params = {
        'start':     start_utc,
        'end':       end_utc,
        'timeframe': timeframe,
        'feed':      'iex',
        'limit':     10000,
    }
    r = _get_with_retry(f'{DATA_BASE}/v2/stocks/{symbol}/bars',
                        headers=_headers(), params=params, timeout=30)
    if r.status_code == 422:
        return []
    r.raise_for_status()
    data = r.json()
    bars = data.get('bars') or []
    while data.get('next_page_token'):
        params['page_token'] = data['next_page_token']
        r = _get_with_retry(f'{DATA_BASE}/v2/stocks/{symbol}/bars',
                            headers=_headers(), params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        bars.extend(data.get('bars') or [])
    return bars

# ─────────────────────────────────────────────────────────────────────────────
# VWAP
# ─────────────────────────────────────────────────────────────────────────────

def compute_vwap(bars):
    """Cumulative VWAP from a list of bar dicts (each has 'vw', 'v')."""
    total_vol = sum(b['v'] for b in bars if b.get('v', 0) > 0)
    if total_vol == 0:
        return None
    return sum(b.get('vw', b['c']) * b['v'] for b in bars if b.get('v', 0) > 0) / total_vol

# ─────────────────────────────────────────────────────────────────────────────
# MACD
# ─────────────────────────────────────────────────────────────────────────────

def _ema(data, period):
    if not data:
        return []
    k = 2.0 / (period + 1)
    result = [data[0]]
    for price in data[1:]:
        result.append(price * k + result[-1] * (1 - k))
    return result

def compute_macd(bars, fast=12, slow=26, signal_period=9):
    """
    Compute MACD from bar list. Returns (macd_line, signal_line, histogram, direction).
    Returns (None, None, None, None) if insufficient bars.
    """
    closes = [b['c'] for b in bars]
    if len(closes) < slow + signal_period:
        return None, None, None, None
    ema_fast   = _ema(closes, fast)
    ema_slow   = _ema(closes, slow)
    macd_vals  = [f - s for f, s in zip(ema_fast, ema_slow)]
    sig_vals   = _ema(macd_vals[slow - 1:], signal_period)
    macd_line  = macd_vals[-1]
    sig_line   = sig_vals[-1]
    histogram  = macd_line - sig_line
    direction  = 'bullish' if histogram > 0 else ('bearish' if histogram < 0 else 'neutral')
    return macd_line, sig_line, histogram, direction

# ─────────────────────────────────────────────────────────────────────────────
# Bars up to signal time
# ─────────────────────────────────────────────────────────────────────────────

def bars_up_to_signal(bars, trade_date_str, scan_type):
    """Filter bars to only those before/at signal time."""
    signal_time_str = MORNING_SIGNAL_ET if scan_type == 'morning' else INTRADAY_SIGNAL_ET
    dt      = datetime.strptime(trade_date_str, '%Y-%m-%d')
    h, m, s = [int(x) for x in signal_time_str.split(':')]
    cutoff  = ET.localize(dt.replace(hour=h, minute=m, second=s))

    result = []
    for b in bars:
        bar_dt_str = b['t']
        try:
            bar_dt = datetime.fromisoformat(bar_dt_str.replace('Z', '+00:00'))
        except Exception:
            continue
        if bar_dt <= cutoff:
            result.append(b)
    return result

# ─────────────────────────────────────────────────────────────────────────────
# Nearest S/R from a list of key levels
# ─────────────────────────────────────────────────────────────────────────────

def nearest_levels(levels, ib_break_price):
    """Return (nearest_resistance, nearest_support) relative to IB break price."""
    valid = [l for l in levels if l and l > 0]
    above = [l for l in valid if l > ib_break_price]
    below = [l for l in valid if l <= ib_break_price]
    resistance = min(above) if above else None
    support    = max(below) if below else None
    return resistance, support

# ─────────────────────────────────────────────────────────────────────────────
# Already-processed check
# ─────────────────────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
-- Run this once in your Supabase dashboard → SQL Editor:
CREATE TABLE IF NOT EXISTS backtest_context_levels (
  id BIGSERIAL PRIMARY KEY,
  ticker TEXT NOT NULL,
  trade_date DATE NOT NULL,
  scan_type TEXT NOT NULL,
  prev_day_high NUMERIC,
  prev_day_low NUMERIC,
  premarket_high NUMERIC,
  premarket_low NUMERIC,
  vwap_at_signal NUMERIC,
  macd_line NUMERIC,
  macd_signal_line NUMERIC,
  macd_histogram NUMERIC,
  macd_direction TEXT,
  nearest_resistance NUMERIC,
  nearest_support NUMERIC,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(ticker, trade_date, scan_type)
);
"""

def _ensure_table_exists():
    """Check if table exists; if not, print the CREATE TABLE SQL and exit."""
    try:
        SUPABASE.table('backtest_context_levels').select('id').limit(1).execute()
    except Exception as e:
        if 'PGRST205' in str(e) or 'schema cache' in str(e):
            log.error('=' * 60)
            log.error('TABLE NOT FOUND: backtest_context_levels')
            log.error('Run the following SQL in your Supabase dashboard:')
            log.error('  https://supabase.com/dashboard/project/kqrwrvtelexylqonsjsl/sql/new')
            log.error('=' * 60)
            print(CREATE_TABLE_SQL)
            sys.exit(1)
        raise

def get_already_processed():
    _ensure_table_exists()
    resp = SUPABASE.table('backtest_context_levels') \
        .select('ticker, trade_date, scan_type') \
        .execute()
    return {(r['ticker'], r['trade_date'], r['scan_type']) for r in (resp.data or [])}

# ─────────────────────────────────────────────────────────────────────────────
# User discovery
# ─────────────────────────────────────────────────────────────────────────────

_PAGE_SZ = 1000

def discover_user_ids() -> list:
    """Return all distinct user_ids found in backtest_sim_runs.

    Paginates exhaustively so no user is silently missed regardless of table
    size.  Falls back to [USER_ID] (env-var) if the table cannot be scanned
    and USER_ID is set; aborts otherwise.
    """
    if not SUPABASE:
        log.error('No Supabase connection — cannot discover user IDs.')
        sys.exit(1)

    uid_set = set()
    offset = 0
    rows_scanned = 0
    log.info('Discovering user IDs from backtest_sim_runs…')
    try:
        while True:
            resp = (
                SUPABASE.table('backtest_sim_runs')
                .select('user_id')
                .range(offset, offset + _PAGE_SZ - 1)
                .execute()
            )
            rows = resp.data or []
            for row in rows:
                uid = row.get('user_id')
                if uid:
                    uid_set.add(uid)
            rows_scanned += len(rows)
            if len(rows) < _PAGE_SZ:
                break
            offset += _PAGE_SZ
    except Exception as e:
        log.error(f'ERROR: could not fully scan backtest_sim_runs for user_ids: {e}')
        if USER_ID:
            log.warning(f'Falling back to OWNER_USER_ID env-var: {USER_ID}')
            return [USER_ID]
        log.error('No OWNER_USER_ID env-var set — aborting. Pass user IDs explicitly to bypass discovery.')
        sys.exit(1)

    discovered = sorted(uid_set)
    log.info(f'  → {rows_scanned} rows scanned, {len(discovered)} distinct user(s) found: {discovered}')

    if not discovered:
        if USER_ID:
            log.warning(f'No users found in DB — falling back to OWNER_USER_ID env-var: {USER_ID}')
            return [USER_ID]
        log.error('No users found in backtest_sim_runs and no OWNER_USER_ID env-var set — nothing to do.')
        sys.exit(1)

    return discovered


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main(user_ids=None, dry_run=False):
    if not user_ids:
        user_ids = discover_user_ids()

    log.info(f'Loading backtest rows from Supabase for {len(user_ids)} user(s): {user_ids}')
    query = SUPABASE.table('backtest_sim_runs') \
        .select('ticker, sim_date, scan_type, ib_high, ib_low, actual_outcome') \
        .in_('user_id', user_ids) \
        .in_('actual_outcome', ['Bullish Break', 'Bearish Break'])
    resp = query.execute()
    rows = resp.data or []
    log.info(f'  → {len(rows)} settled breakout rows')

    already = get_already_processed()
    log.info(f'  → {len(already)} already processed, skipping those')

    todo = [r for r in rows
            if (r['ticker'], r['sim_date'], r['scan_type']) not in already]
    log.info(f'  → {len(todo)} to process')

    if dry_run:
        tickers = sorted({r['ticker'] for r in todo})
        dates   = sorted({r['sim_date'] for r in todo})
        log.info('DRY RUN — no Alpaca API calls or database writes will be performed.')
        log.info(f'  Would process {len(todo)} row(s) across {len(tickers)} ticker(s) on {len(dates)} date(s).')
        if tickers:
            log.info(f'  Tickers : {tickers}')
        if dates:
            log.info(f'  Dates   : {dates}')
        return {
            'would_process': len(todo),
            'tickers': tickers,
            'dates': dates,
            'rows': [
                {'ticker': r['ticker'], 'trade_date': r['sim_date'], 'scan_type': r['scan_type']}
                for r in todo
            ],
        }

    if not todo:
        log.info('Nothing to do — all rows already have context levels.')
        return

    # Group by date for efficient daily-bar fetching
    by_date = defaultdict(list)
    for r in todo:
        by_date[r['sim_date']].append(r)

    total_saved = 0
    total_no_bars = 0
    total_errors = 0

    for trade_date, date_rows in sorted(by_date.items()):
        tickers = list({r['ticker'] for r in date_rows})
        log.info(f'[{trade_date}] {len(tickers)} tickers …')
        date_saved   = 0
        date_no_bars = 0
        date_errors  = 0

        # ── Step 1: Prior day bars (batch) ───────────────────────────────────
        prev_date = (datetime.strptime(trade_date, '%Y-%m-%d') - timedelta(days=5)).strftime('%Y-%m-%d')
        end_date  = (datetime.strptime(trade_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        prev_day_data = {}
        for i in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[i:i+BATCH_SIZE]
            try:
                daily = get_daily_bars(batch, prev_date, end_date)
                for sym, bars in daily.items():
                    if bars:
                        last = bars[-1]
                        prev_day_data[sym] = {'high': last['h'], 'low': last['l']}
            except Exception as e:
                log.warning(f'  daily bars error for batch {batch}: {e}')
            time.sleep(SLEEP_BETWEEN)

        # ── Step 2: Intraday bars per ticker ─────────────────────────────────
        for row in date_rows:
            ticker    = row['ticker']
            scan_type = row['scan_type']
            ib_high   = row.get('ib_high')
            ib_low    = row.get('ib_low')
            outcome   = row.get('actual_outcome', '')
            ib_break  = ib_high if outcome == 'Bullish Break' else ib_low

            try:
                intraday = get_intraday_bars(ticker, trade_date, timeframe='5Min')
                time.sleep(SLEEP_BETWEEN)
            except RateLimitExhausted as e:
                log.warning(f'  {ticker} {trade_date} skipped — rate-limit retries exhausted: {e}')
                continue
            except Exception as e:
                log.warning(f'  {ticker} {trade_date} intraday error: {e}')
                date_errors  += 1
                total_errors += 1
                continue

            if not intraday:
                log.warning(f'  \u26a0 {ticker} {trade_date} no intraday bars \u2014 all context columns will be NULL')
                date_no_bars  += 1
                total_no_bars += 1
                data_quality = 'no_bars'
            else:
                data_quality = 'ok'

            bars_at_signal = bars_up_to_signal(intraday, trade_date, scan_type)

            vwap_val                           = compute_vwap(bars_at_signal) if bars_at_signal else None
            macd_line, macd_sig, macd_hist, macd_dir = compute_macd(bars_at_signal)

            # Key levels
            prev = prev_day_data.get(ticker, {})
            key_levels = [
                prev.get('high'),
                prev.get('low'),
                vwap_val,
            ]
            resistance, support = nearest_levels(key_levels, ib_break) if ib_break else (None, None)

            vwap_stored       = round(vwap_val, 4)       if vwap_val is not None else None
            macd_line_stored  = round(macd_line, 6)      if macd_line is not None else None
            macd_sig_stored   = round(macd_sig, 6)       if macd_sig is not None else None
            macd_hist_stored  = round(macd_hist, 6)      if macd_hist is not None else None
            resist_stored     = round(resistance, 4)     if resistance is not None else None
            support_stored    = round(support, 4)        if support is not None else None

            record = {
                'ticker':             ticker,
                'trade_date':         trade_date,
                'scan_type':          scan_type,
                'data_quality':       data_quality,
                'prev_day_high':      prev.get('high'),
                'prev_day_low':       prev.get('low'),
                'premarket_high':     None,   # IEX feed: no pre-market data
                'premarket_low':      None,
                'vwap_at_signal':     vwap_stored,
                'macd_line':          macd_line_stored,
                'macd_signal_line':   macd_sig_stored,
                'macd_histogram':     macd_hist_stored,
                'macd_direction':     macd_dir,
                'nearest_resistance': resist_stored,
                'nearest_support':    support_stored,
            }

            try:
                SUPABASE.table('backtest_context_levels').upsert(
                    record, on_conflict='ticker,trade_date,scan_type'
                ).execute()
                date_saved  += 1
                total_saved += 1
                _vwap_str  = f"{vwap_val:.2f}"  if vwap_val is not None else "N/A"
                _macd_str  = macd_dir            if macd_dir is not None else "N/A"
                log.info(f'  ✓ {ticker} {trade_date} {scan_type} | VWAP={_vwap_str} | MACD={_macd_str}')
            except Exception as e:
                _e_str = str(e)
                if 'data_quality' in _e_str:
                    # data_quality column not yet in Supabase schema — retry without it.
                    # Add column with: ALTER TABLE backtest_context_levels ADD COLUMN data_quality text;
                    _record_fallback = {k: v for k, v in record.items() if k != 'data_quality'}
                    try:
                        SUPABASE.table('backtest_context_levels').upsert(
                            _record_fallback, on_conflict='ticker,trade_date,scan_type'
                        ).execute()
                        date_saved  += 1
                        total_saved += 1
                        _vwap_str  = f"{vwap_val:.2f}"  if vwap_val is not None else "N/A"
                        _macd_str  = macd_dir            if macd_dir is not None else "N/A"
                        log.info(f'  ✓ {ticker} {trade_date} {scan_type} [no data_quality col] | VWAP={_vwap_str} | MACD={_macd_str}')
                    except Exception as e2:
                        log.warning(f'  ✗ {ticker} {trade_date} upsert error: {e2}')
                        date_errors  += 1
                        total_errors += 1
                else:
                    log.warning(f'  ✗ {ticker} {trade_date} upsert error: {e}')
                    date_errors  += 1
                    total_errors += 1

        log.info(f'  [{trade_date}] done — saved={date_saved} no-bars={date_no_bars} errors={date_errors}')

    log.info('=' * 60)
    log.info(f'COMPLETE — {total_saved} rows saved, {total_no_bars} no-bars, {total_errors} errors')

    from backfill_utils import append_backfill_history
    append_backfill_history(
        script='backfill_context_levels',
        health={
            'rows_saved': total_saved,
            'no_bars': total_no_bars,
            'errors': total_errors,
        },
        logger=log,
    )

    if total_errors > 0:
        _send_backfill_alert(total_saved, total_no_bars, total_errors)


def _send_backfill_alert(rows_saved: int, no_bars: int, errors: int) -> None:
    """Send a Telegram alert when a backfill run completes with errors.

    Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the environment.
    Silently skips if credentials are absent or if the operator has disabled
    backfill error alerts via the dashboard (backfill_error_alerts_enabled pref).
    """
    import json as _json2
    import urllib.request as _urllib_req
    import urllib.parse as _urllib_parse

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.info("Telegram credentials not set — skipping backfill error alert.")
        return

    _user_prefs_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".local", "user_prefs.json")
    try:
        if os.path.exists(_user_prefs_file):
            with open(_user_prefs_file) as _pf:
                _all = _json2.load(_pf)
            owner_id = os.getenv("OWNER_USER_ID", "").strip() or "anonymous"
            _prefs = _all.get(owner_id, {})
            if not _prefs.get("backfill_error_alerts_enabled", True):
                log.info("Backfill error alerts disabled by operator — skipping.")
                return
    except Exception as _pe:
        log.warning(f"Could not read owner prefs for backfill alert: {_pe}")

    message = (
        f"⚠️ <b>Backfill run completed with errors</b>\n\n"
        f"Rows saved: <b>{rows_saved}</b>\n"
        f"No-bars:    <b>{no_bars}</b>\n"
        f"Errors:     <b>{errors}</b>"
    )
    try:
        body = _urllib_parse.urlencode({
            "chat_id":    chat_id,
            "text":       message,
            "parse_mode": "HTML",
        }).encode()
        req = _urllib_req.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            method="POST",
        )
        with _urllib_req.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                log.info("Backfill error alert sent via Telegram.")
            else:
                log.warning(f"Telegram sendMessage returned HTTP {resp.status}")
    except Exception as _te:
        log.warning(f"Could not send backfill Telegram alert: {_te}")


if __name__ == '__main__':
    main()
