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
USER_ID       = 'a5e1fcab-8369-42c4-8550-a8a19734510c'

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
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info('Loading backtest rows from Supabase…')
    resp = SUPABASE.table('backtest_sim_runs') \
        .select('ticker, sim_date, scan_type, ib_high, ib_low, actual_outcome') \
        .eq('user_id', USER_ID) \
        .in_('actual_outcome', ['Bullish Break', 'Bearish Break']) \
        .execute()
    rows = resp.data or []
    log.info(f'  → {len(rows)} settled breakout rows')

    already = get_already_processed()
    log.info(f'  → {len(already)} already processed, skipping those')

    todo = [r for r in rows
            if (r['ticker'], r['sim_date'], r['scan_type']) not in already]
    log.info(f'  → {len(todo)} to process')

    if not todo:
        log.info('Nothing to do — all rows already have context levels.')
        return

    # Group by date for efficient daily-bar fetching
    by_date = defaultdict(list)
    for r in todo:
        by_date[r['sim_date']].append(r)

    total_saved = 0
    total_errors = 0

    for trade_date, date_rows in sorted(by_date.items()):
        tickers = list({r['ticker'] for r in date_rows})
        log.info(f'[{trade_date}] {len(tickers)} tickers …')

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
                total_errors += 1
                continue

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
                SUPABASE.table('backtest_context_levels').upsert(record).execute()
                total_saved += 1
                _vwap_str  = f"{vwap_val:.2f}"  if vwap_val is not None else "N/A"
                _macd_str  = macd_dir            if macd_dir is not None else "N/A"
                log.info(f'  ✓ {ticker} {trade_date} {scan_type} | VWAP={_vwap_str} | MACD={_macd_str}')
            except Exception as e:
                log.warning(f'  ✗ {ticker} {trade_date} upsert error: {e}')
                total_errors += 1

        log.info(f'  [{trade_date}] done — saved={total_saved} errors={total_errors}')

    log.info('=' * 60)
    log.info(f'COMPLETE — {total_saved} rows saved, {total_errors} errors')

if __name__ == '__main__':
    main()
