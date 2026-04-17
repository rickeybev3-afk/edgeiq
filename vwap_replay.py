"""
vwap_replay.py
──────────────
Retroactive VWAP replay for live paper_trades.

For each settled paper trade (April 6–16 2026):
  1. Fetches 1-min bars from Alpaca (9:30–10:30 AM ET)
  2. Computes VWAP over the IB window
  3. Applies the full filter stack: TCS>=50, IB<10%, VWAP aligned
  4. Patches vwap_at_ib back into paper_trades
  5. Prints the complete three-filter WR comparison

Run:  python vwap_replay.py [--patch] [--user-id ...]
  --patch  Write computed vwap_at_ib back to paper_trades (default: dry-run)
"""
import sys, os, time, argparse
from datetime import datetime, date, timedelta
from collections import defaultdict

sys.path.insert(0, '.')
import backend

try:
    import requests
except ImportError:
    os.system('pip install requests -q')
    import requests

SUPABASE   = backend.supabase
API_KEY    = backend.ALPACA_API_KEY
SECRET_KEY = backend.ALPACA_SECRET_KEY
DATA_URL   = 'https://data.alpaca.markets/v2/stocks'

USER_ID_DEFAULT = 'a5e1fcab-8369-42c4-8550-a8a19734510c'

# ── Argument parsing ──────────────────────────────────────────────────────────
def _parse():
    p = argparse.ArgumentParser()
    p.add_argument('--patch',   action='store_true', help='Write vwap_at_ib back to paper_trades')
    p.add_argument('--user-id', default=USER_ID_DEFAULT)
    return p.parse_args()

# ── Alpaca: fetch 1-min bars for a ticker on a single calendar date ───────────
def fetch_ib_bars(symbol: str, trade_date: str) -> list:
    """
    Fetch 1-min OHLCV bars from 9:30–10:31 AM Eastern on trade_date.
    April is EDT (UTC-4), so 9:30 ET = 13:30 UTC, 10:31 ET = 14:31 UTC.
    Returns list of bar dicts with keys: t, o, h, l, c, v
    """
    start = f"{trade_date}T13:30:00Z"
    end   = f"{trade_date}T14:31:00Z"
    url   = f"{DATA_URL}/{symbol}/bars"
    params = {
        'timeframe': '1Min',
        'start':     start,
        'end':       end,
        'feed':      'iex',       # IEX is fine for VWAP at this resolution
        'limit':     100,
    }
    headers = {
        'APCA-API-KEY-ID':     API_KEY,
        'APCA-API-SECRET-KEY': SECRET_KEY,
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json().get('bars') or []
        if resp.status_code == 422:
            return []  # symbol not found / no data
        if resp.status_code == 429:
            time.sleep(2)
            return fetch_ib_bars(symbol, trade_date)  # retry once
    except Exception:
        pass
    return []


def compute_vwap(bars: list) -> float | None:
    """Compute VWAP as sum(typical_price * volume) / sum(volume)."""
    total_tv = 0.0
    total_v  = 0.0
    for b in bars:
        if not b.get('v'):
            continue
        tp = (b['h'] + b['l'] + b['c']) / 3.0
        total_tv += tp * b['v']
        total_v  += b['v']
    if total_v == 0:
        return None
    return total_tv / total_v


def vwap_aligned(close_price: float, vwap: float, actual_outcome: str) -> bool:
    """
    Bullish Break → close_price should be >= vwap
    Bearish Break → close_price should be <= vwap
    Other outcomes → pass through
    """
    if actual_outcome == 'Bullish Break':
        return close_price >= vwap
    elif actual_outcome == 'Bearish Break':
        return close_price <= vwap
    return True  # non-directional: no gate


# ── Stats helper ──────────────────────────────────────────────────────────────
def stats(rows, label, total_n=None):
    n = len(rows)
    if n == 0:
        print(f'  {label:<48s}: 0 trades')
        return {'n': 0, 'wr': 0.0, 'exp': 0.0}
    wins  = sum(1 for r in rows if r.get('win_loss') == 'Win')
    pnls  = [r['pnl_r_sim'] for r in rows if r.get('pnl_r_sim') is not None]
    wr    = wins / n * 100
    exp   = sum(pnls) / len(pnls) if pnls else 0.0
    skip  = f'  (skips {total_n - n:+d})' if total_n and total_n != n else ''
    print(f'  {label:<48s}: {wins}W/{n-wins}L  →  {wr:5.1f}% WR  {exp:+.3f}R  n={n}{skip}')
    return {'n': n, 'wr': wr, 'exp': exp}


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    args = _parse()
    user_id = args.user_id
    do_patch = args.patch

    print('Loading paper_trades from Supabase…')
    resp = SUPABASE.table('paper_trades').select(
        'id,trade_date,ticker,tcs,ib_range_pct,ib_high,ib_low,open_price,'
        'close_price,actual_outcome,win_loss,pnl_r_sim,scan_type,vwap_at_ib'
    ).eq('user_id', user_id).in_('win_loss', ['Win', 'Loss']).execute()
    rows = resp.data or []
    print(f'  → {len(rows)} settled trades loaded')

    if not rows:
        print('No settled trades found.')
        return

    # Group by ticker+date to avoid redundant Alpaca calls
    td_map = defaultdict(list)
    for r in rows:
        if r.get('ticker') and r.get('trade_date'):
            td_map[(r['ticker'], r['trade_date'])].append(r)

    print(f'\nFetching VWAP for {len(td_map)} ticker/date pairs from Alpaca…')
    print(f'(~{len(td_map) * 0.2:.0f}s estimated at ~5 req/s)\n')

    vwap_cache = {}   # (ticker, date) → vwap float or None
    fetched = 0
    no_data = 0

    for (ticker, trade_date), _ in sorted(td_map.items()):
        bars = fetch_ib_bars(ticker, trade_date)
        vwap = compute_vwap(bars) if bars else None
        vwap_cache[(ticker, trade_date)] = vwap
        if vwap:
            fetched += 1
        else:
            no_data += 1
        time.sleep(0.15)   # ~6 req/s — comfortably under Alpaca free-tier limit

    print(f'VWAP computed: {fetched} ok, {no_data} no-data (pass-through for missing)')
    print()

    # Enrich rows with computed VWAP
    for r in rows:
        key = (r.get('ticker'), r.get('trade_date'))
        r['_vwap_computed'] = vwap_cache.get(key)

    # ── Patch back into paper_trades ─────────────────────────────────────────
    if do_patch:
        print('Patching vwap_at_ib into paper_trades…')
        patched = 0
        for r in rows:
            vwap = r.get('_vwap_computed')
            if vwap and r.get('vwap_at_ib') is None:
                SUPABASE.table('paper_trades').update(
                    {'vwap_at_ib': round(vwap, 6)}
                ).eq('id', r['id']).execute()
                patched += 1
        print(f'  → Patched {patched} rows')
        print()

    # ── Filter analysis ───────────────────────────────────────────────────────
    vwap_avail = sum(1 for r in rows if r.get('_vwap_computed'))
    print(f'VWAP coverage on live trades: {vwap_avail}/{len(rows)} ({vwap_avail/len(rows)*100:.0f}%)')
    print()

    print('=' * 68)
    print('  FULL FILTER REPLAY — if 95.7% system had run since April 6')
    print('=' * 68)

    # Layer 0: all
    s0 = stats(rows, 'All settled trades (no filter)')

    # Layer 1: TCS >= 50
    t50  = [r for r in rows if (r.get('tcs') or 0) >= 50]
    s1   = stats(t50, 'TCS >= 50  (baseline)', total_n=len(rows))

    # Layer 2: + IB < 10%
    ib_ok   = [r for r in t50 if r.get('ib_range_pct') is not None and r['ib_range_pct'] < 10.0]
    no_ib   = [r for r in t50 if r.get('ib_range_pct') is None]          # pass-through
    ib_wide = [r for r in t50 if r.get('ib_range_pct') is not None and r['ib_range_pct'] >= 10.0]
    after_ib = ib_ok + no_ib
    s2 = stats(after_ib, 'TCS>=50 + IB < 10%', total_n=len(rows))

    # Wide IB rejected trades (informational)
    if ib_wide:
        stats(ib_wide, '  (rejected by IB filter — would be skipped)')

    # Layer 3: + VWAP alignment
    full        = []
    vwap_reject = []
    vwap_skip   = 0
    for r in after_ib:
        vwap = r.get('_vwap_computed')
        cp   = r.get('close_price')
        ao   = r.get('actual_outcome', '')
        if vwap and cp:
            if vwap_aligned(cp, vwap, ao):
                full.append(r)
            else:
                vwap_reject.append(r)
        else:
            full.append(r)   # no VWAP data → pass through
            vwap_skip += 1

    s3 = stats(full, 'FULL FILTER: TCS>=50 + IB<10% + VWAP  [95.7% system]', total_n=len(rows))

    if vwap_reject:
        stats(vwap_reject, '  (rejected by VWAP filter — would be skipped)')

    print('=' * 68)
    print()

    # ── Summary lifts ─────────────────────────────────────────────────────────
    if s0['n'] and s3['n']:
        print('  Filter impact vs baseline:')
        print(f'    WR lift        : {s0["wr"]:.1f}%  →  {s3["wr"]:.1f}%   ({s3["wr"]-s0["wr"]:+.1f}pp)')
        print(f'    Expectancy lift: {s0["exp"]:+.3f}R  →  {s3["exp"]:+.3f}R   ({s3["exp"]-s0["exp"]:+.3f}R)')
        print(f'    Trades taken   : {s3["n"]} of {s0["n"]}  ({s3["n"]/s0["n"]*100:.0f}% of scanner output)')
    print()

    # ── By scan type ─────────────────────────────────────────────────────────
    print('  Full-filter trades by scan type:')
    for stype in ('morning', 'intraday'):
        sub = [r for r in full if r.get('scan_type') == stype]
        if sub:
            stats(sub, f'    {stype}')

    print()
    if vwap_skip:
        print(f'  Note: {vwap_skip} trades had no VWAP data and were passed through the VWAP gate.')
    if not do_patch:
        print('  Tip: run with --patch to write computed vwap_at_ib back to paper_trades.')
    print('=' * 68)


if __name__ == '__main__':
    main()
