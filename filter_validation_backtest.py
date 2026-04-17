"""
filter_validation_backtest.py
──────────────────────────────
Validates the IB range % + VWAP alignment entry filters against historical data.

Default source: backtest_sim_runs  (~34k rows, full historical dataset)
Live source   : paper_trades       (live-logged trades; use --source paper_trades)

Dataset: trades where IB was actually broken (actual_outcome in Bullish/Bearish
Break) AND pnl_r_sim is computed.  Win = pnl_r_sim > 0 (bracket trade hit target).

Expected filter progression (from deep data-mining pass):
  TCS >= 50 baseline                        → ~83-84% WR
  + IB range < 10% of open price            → ~91-93% WR
  + VWAP aligned (close on correct VWAP side) → ~96-98% WR, ~+2.4R

Run via:
  python filter_validation_backtest.py                          # backtest history
  python filter_validation_backtest.py --source paper_trades    # live paper trades
"""

import sys
import argparse
sys.path.insert(0, '.')
import backend

SUPABASE         = backend.supabase
DEFAULT_USER_ID  = 'a5e1fcab-8369-42c4-8550-a8a19734510c'
DEFAULT_MAX_ROWS = 0   # 0 = no cap (full history)

PAGE = 1000   # rows per Supabase page

def _parse_args():
    p = argparse.ArgumentParser(
        description='Validate IB range + VWAP alignment entry filters against historical breakout data.'
    )
    p.add_argument('--user-id',  default=DEFAULT_USER_ID,
                   help='Supabase user_id to query (default: hard-coded account)')
    p.add_argument('--max-rows', type=int, default=DEFAULT_MAX_ROWS,
                   help='Cap total rows loaded (0 = full history, e.g. 1000 for quick sanity check)')
    p.add_argument('--source', choices=['backtest', 'paper_trades'], default='backtest',
                   help='Data source: "backtest" uses backtest_sim_runs (default); '
                        '"paper_trades" uses the live paper_trades table')
    return p.parse_args()

# ─────────────────────────────────────────────────────────────────────────────
# Load (paginated — ~34k rows)
# ─────────────────────────────────────────────────────────────────────────────

def load_rows(user_id: str, max_rows: int = 0):
    """
    Fetch settled breakout trades from backtest_sim_runs.
    Filters:
      actual_outcome in ('Bullish Break', 'Bearish Break')
      pnl_r_sim is not null
    user_id : Supabase user_id to filter by
    max_rows: if > 0, cap total rows loaded (useful for quick sanity checks)
    """
    cols = (
        'actual_outcome,tcs,ib_range_pct,close_vs_vwap_pct,'
        'pnl_r_sim,scan_type'
    )
    all_rows = []
    offset   = 0
    while True:
        page_size = PAGE if (max_rows == 0) else min(PAGE, max_rows - len(all_rows))
        if page_size <= 0:
            break
        resp = (
            SUPABASE.table('backtest_sim_runs')
            .select(cols)
            .eq('user_id', user_id)
            .in_('actual_outcome', ['Bullish Break', 'Bearish Break'])
            .not_.is_('pnl_r_sim', 'null')
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = resp.data or []
        all_rows.extend(batch)
        if len(batch) < page_size or (max_rows > 0 and len(all_rows) >= max_rows):
            break
        offset += page_size
    return all_rows


def load_rows_live(user_id: str, max_rows: int = 0):
    """
    Fetch settled breakout trades from paper_trades (live-mode validation).

    paper_trades stores vwap_at_ib (raw VWAP price) and alert_price (close at
    signal) rather than a pre-computed close_vs_vwap_pct.  This function derives
    close_vs_vwap_pct on the fly so the rest of the analysis code is unchanged:

        close_vs_vwap_pct = (alert_price - vwap_at_ib) / vwap_at_ib * 100

    Filters:
      actual_outcome in ('Bullish Break', 'Bearish Break')
      pnl_r_sim is not null
    user_id : Supabase user_id to filter by
    max_rows: if > 0, cap total rows loaded
    """
    cols = (
        'actual_outcome,tcs,ib_range_pct,vwap_at_ib,alert_price,'
        'pnl_r_sim,scan_type'
    )
    all_rows = []
    offset   = 0
    while True:
        page_size = PAGE if (max_rows == 0) else min(PAGE, max_rows - len(all_rows))
        if page_size <= 0:
            break
        resp = (
            SUPABASE.table('paper_trades')
            .select(cols)
            .eq('user_id', user_id)
            .in_('actual_outcome', ['Bullish Break', 'Bearish Break'])
            .not_.is_('pnl_r_sim', 'null')
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = resp.data or []
        all_rows.extend(batch)
        if len(batch) < page_size or (max_rows > 0 and len(all_rows) >= max_rows):
            break
        offset += page_size

    # Derive close_vs_vwap_pct from raw columns so vwap_aligned() works unchanged.
    for row in all_rows:
        vwap       = row.get('vwap_at_ib')
        alert_px   = row.get('alert_price')
        if vwap and alert_px and float(vwap) != 0:
            row['close_vs_vwap_pct'] = (float(alert_px) - float(vwap)) / float(vwap) * 100
        else:
            row['close_vs_vwap_pct'] = None

    return all_rows

# ─────────────────────────────────────────────────────────────────────────────
# Stats helpers
# ─────────────────────────────────────────────────────────────────────────────

def stats(rows, label):
    """Print win-rate and expectancy for a set of rows."""
    n = len(rows)
    if not n:
        print(f'\n  ── {label} (0 trades) ── NO DATA')
        return {'wr': 0.0, 'expectancy': 0.0, 'n': 0, 'total_r': 0.0}

    pnl_rs  = [r['pnl_r_sim'] for r in rows]
    wins    = [p for p in pnl_rs if p > 0]
    losses  = [p for p in pnl_rs if p <= 0]
    wr      = len(wins) / n * 100
    exp     = sum(pnl_rs) / n
    total_r = sum(pnl_rs)

    print(f'\n  ── {label} ──')
    print(f'  N              : {n}')
    print(f'  Win rate       : {wr:.1f}%  ({len(wins)}W / {len(losses)}L)')
    print(f'  Expectancy     : {exp:+.4f}R / trade')
    print(f'  Total R        : {total_r:+.2f}R')
    print(f'  Avg winner     : {sum(wins)/len(wins):+.4f}R' if wins else '  Avg winner     : n/a')
    print(f'  Avg loser      : {sum(losses)/len(losses):+.4f}R' if losses else '  Avg loser      : n/a')

    return {'wr': wr, 'expectancy': exp, 'n': n, 'total_r': total_r}

# ─────────────────────────────────────────────────────────────────────────────
# VWAP alignment
# ─────────────────────────────────────────────────────────────────────────────

def vwap_aligned(row):
    """
    close_vs_vwap_pct = (close_price - vwap_at_ib) / vwap_at_ib * 100
      >= 0  → close >= VWAP  (bullish alignment for Bullish Break)
      <= 0  → close <= VWAP  (bearish alignment for Bearish Break)
    Missing data → pass through (no penalty).
    """
    cvv = row.get('close_vs_vwap_pct')
    if cvv is None:
        return True
    outcome = row.get('actual_outcome', '')
    if outcome == 'Bullish Break':
        return cvv >= 0.0
    else:
        return cvv <= 0.0

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    args = _parse_args()
    user_id  = args.user_id
    max_rows = args.max_rows
    source   = args.source

    cap_note   = f' (capped at {max_rows})' if max_rows else ' (full history)'
    table_name = 'paper_trades' if source == 'paper_trades' else 'backtest_sim_runs'
    print(f'Loading breakout trades from {table_name}{cap_note}…')
    if source == 'paper_trades':
        all_rows = load_rows_live(user_id=user_id, max_rows=max_rows)
    else:
        all_rows = load_rows(user_id=user_id, max_rows=max_rows)
    print(f'  → {len(all_rows)} settled breakout trades (actual_outcome = Bullish/Bearish Break, pnl_r_sim not null)')

    # ── Layer 0: no filters ───────────────────────────────────────────────────
    s0 = stats(all_rows, 'ALL breakout trades (no TCS filter)')

    # ── Layer 1: TCS >= 50 baseline ──────────────────────────────────────────
    tcs50 = [r for r in all_rows if (r.get('tcs') or 0) >= 50]
    s1 = stats(tcs50, f'TCS >= 50  (baseline — n={len(tcs50)})')

    # ── Layer 2: + IB range < 10% ────────────────────────────────────────────
    ib_ok   = [r for r in tcs50
               if r.get('ib_range_pct') is not None and r['ib_range_pct'] < 10.0]
    ib_wide = [r for r in tcs50
               if r.get('ib_range_pct') is not None and r['ib_range_pct'] >= 10.0]
    s2 = stats(ib_ok, f'TCS >= 50 + IB < 10%  (n={len(ib_ok)})')

    # ── Layer 3: + VWAP directional alignment ────────────────────────────────
    full_filter    = [r for r in ib_ok if vwap_aligned(r)]
    vwap_misalign  = [r for r in ib_ok if not vwap_aligned(r)]
    s3 = stats(full_filter, f'TCS >= 50 + IB < 10% + VWAP aligned  *** LIVE FILTER ***  (n={len(full_filter)})')

    # ── What we're rejecting ─────────────────────────────────────────────────
    print('\n  ── REJECTED TRADES (what the filters block) ──')
    if ib_wide:
        stats(ib_wide, f'Wide IB (>= 10%) — filtered out  (n={len(ib_wide)})')
    if vwap_misalign:
        stats(vwap_misalign, f'VWAP misaligned — filtered out  (n={len(vwap_misalign)})')

    # ── By scan type ─────────────────────────────────────────────────────────
    print('\n  ── FULL-FILTER TRADES BY SCAN TYPE ──')
    for st in ('morning', 'intraday'):
        subset = [r for r in full_filter if r.get('scan_type') == st]
        if subset:
            stats(subset, f'{st}  (n={len(subset)})')

    # ── By IB range bucket (informational) ───────────────────────────────────
    print('\n  ── TCS>=50: WR BY IB RANGE BUCKET ──')
    buckets = [(0, 3), (3, 5), (5, 8), (8, 10), (10, 15), (15, 999)]
    for lo, hi in buckets:
        bucket = [r for r in tcs50
                  if r.get('ib_range_pct') is not None
                  and lo <= r['ib_range_pct'] < hi]
        if bucket:
            pnls = [r['pnl_r_sim'] for r in bucket]
            wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            exp = sum(pnls) / len(pnls)
            print(f'  IB {lo:3.0f}-{hi:3.0f}%: {wr:5.1f}% WR  {exp:+.3f}R  (n={len(bucket)})')

    # ── Summary ──────────────────────────────────────────────────────────────
    print('\n' + '=' * 60)
    print('  FILTER VALIDATION SUMMARY')
    print('=' * 60)
    rows_fmt = [
        ('Baseline (TCS>=50)',               s1),
        ('+ IB < 10%',                       s2),
        ('+ VWAP aligned  [LIVE FILTER]',    s3),
    ]
    for lbl, s in rows_fmt:
        if s['n']:
            print(f'  {lbl:42s}: {s["wr"]:5.1f}% WR  {s["expectancy"]:+.3f}R  (n={s["n"]})')

    print()
    if s1['n'] and s3['n']:
        delta_wr = s3['wr'] - s1['wr']
        delta_r  = s3['expectancy'] - s1['expectancy']
        print(f'  WR lift (baseline → full filter) : {delta_wr:+.1f}%')
        print(f'  Expectancy lift                  : {delta_r:+.3f}R / trade')

    # ── Coverage (how much of the qualifying universe passes both filters) ────
    if tcs50:
        n_passing = len(full_filter)
        pct = n_passing / len(tcs50) * 100
        print(f'\n  Filter coverage: {n_passing} of {len(tcs50)} TCS>=50 trades pass both gates ({pct:.1f}%)')
        print(f'  (Remaining {100-pct:.1f}% are filtered out as chaotic/misaligned)')

    # ── Gate checks ──────────────────────────────────────────────────────────
    gate_ok = True
    if s1['n'] and not (75.0 <= s1['wr'] <= 95.0):
        print(f'\n  ⚠ WARNING: Baseline WR {s1["wr"]:.1f}% outside expected range 75-95%')
        gate_ok = False
    if s3['n'] and not (88.0 <= s3['wr'] <= 100.0):
        print(f'\n  ⚠ WARNING: Full-filter WR {s3["wr"]:.1f}% outside expected range 88-100%')
        gate_ok = False
    if s3['n'] and s1['n'] and s3['wr'] <= s1['wr']:
        print(f'\n  ⚠ WARNING: Full filter did not improve WR over baseline ({s3["wr"]:.1f}% <= {s1["wr"]:.1f}%)')
        gate_ok = False
    if gate_ok and s3['n']:
        print('\n  ✅ Filter validation PASSED — WR improvement confirmed')

    print('=' * 60)


if __name__ == '__main__':
    main()
