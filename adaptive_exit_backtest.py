"""
adaptive_exit_backtest.py
──────────────────────────
Compares bracket exits vs adaptive S/R exits on the same historical trades.

Strategy logic:
  Bracket exit  → fixed 2R target, -1R stop (current system)
  Adaptive exit → exit at nearest S/R level if it's between entry and 2R target
                  AND MACD direction confirms the trade direction
                  Fall back to bracket exit if no better level found.

Run via: python adaptive_exit_backtest.py
"""

import sys, math
from collections import defaultdict
sys.path.insert(0, '.')
import backend

SUPABASE = backend.supabase
USER_ID  = 'a5e1fcab-8369-42c4-8550-a8a19734510c'

# ─────────────────────────────────────────────────────────────────────────────
# Load data
# ─────────────────────────────────────────────────────────────────────────────

def load_backtest_rows():
    resp = SUPABASE.table('backtest_sim_runs') \
        .select('ticker, sim_date, scan_type, actual_outcome, ib_high, ib_low, follow_thru_pct, pnl_r_sim') \
        .eq('user_id', USER_ID) \
        .in_('actual_outcome', ['Bullish Break', 'Bearish Break']) \
        .execute()
    return {(r['ticker'], r['sim_date'], r['scan_type']): r for r in (resp.data or [])}

def load_context_levels():
    resp = SUPABASE.table('backtest_context_levels') \
        .select('*') \
        .execute()
    return {(r['ticker'], r['trade_date'], r['scan_type']): r for r in (resp.data or [])}

# ─────────────────────────────────────────────────────────────────────────────
# Adaptive exit math
# ─────────────────────────────────────────────────────────────────────────────

def simulate_adaptive_exit(trade_row, ctx_row):
    """
    Returns adaptive_pnl_r for one trade given context levels.
    
    Logic:
    1. Compute entry, stop, bracket target from IB levels + follow_thru_pct
    2. Find nearest S/R level between entry and bracket target
    3. If MACD direction MATCHES trade direction → use nearest S/R as target
       Otherwise → stick with bracket target (MACD not confirming, be conservative)
    4. Apply same stop (-1R)
    5. Compute P&L in R
    """
    outcome = trade_row.get('actual_outcome', '')
    ib_high = trade_row.get('ib_high')
    ib_low  = trade_row.get('ib_low')
    ftp     = trade_row.get('follow_thru_pct')  # follow-through %

    if not all([ib_high, ib_low, ftp is not None]):
        return None

    ib_range = ib_high - ib_low
    if ib_range <= 0:
        return None

    is_bullish = outcome == 'Bullish Break'
    entry      = ib_high if is_bullish else ib_low
    stop       = entry - ib_range if is_bullish else entry + ib_range  # -1R
    tgt_2r     = entry + 2 * ib_range if is_bullish else entry - 2 * ib_range  # +2R

    # Actual follow-through price
    actual_exit = entry + (ftp / 100.0) * ib_range if is_bullish else entry - (ftp / 100.0) * ib_range

    # Was the stop hit before target? Simple check: did follow_thru go negative?
    stopped_out = (ftp < 0) if is_bullish else (ftp > 0)

    # Context levels
    macd_dir    = ctx_row.get('macd_direction')
    resistance  = ctx_row.get('nearest_resistance')
    support     = ctx_row.get('nearest_support')
    vwap        = ctx_row.get('vwap_at_signal')
    prev_h      = ctx_row.get('prev_day_high')
    prev_l      = ctx_row.get('prev_day_low')

    # Collect candidate adaptive targets
    if is_bullish:
        candidates = [r for r in [resistance, vwap, prev_h] if r and entry < r < tgt_2r]
        macd_confirms = (macd_dir == 'bullish')
    else:
        candidates = [s for s in [support, vwap, prev_l] if s and tgt_2r < s < entry]
        macd_confirms = (macd_dir == 'bearish')

    adaptive_target = None
    if candidates and macd_confirms:
        # Use nearest level to entry (conservative)
        adaptive_target = min(candidates, key=lambda x: abs(x - entry))

    # ── Simulate bracket exit ──────────────────────────────────────────────
    if stopped_out:
        bracket_pnl_r = -1.0
    else:
        mfe_price = entry + (ftp / 100.0) * ib_range if is_bullish else entry - (ftp / 100.0) * ib_range
        hit_target = (mfe_price >= tgt_2r) if is_bullish else (mfe_price <= tgt_2r)
        if hit_target:
            bracket_pnl_r = 2.0
        else:
            # Partial — closed somewhere between entry and target
            bracket_pnl_r = (mfe_price - entry) / ib_range if is_bullish else (entry - mfe_price) / ib_range

    # ── Simulate adaptive exit ─────────────────────────────────────────────
    if stopped_out:
        adaptive_pnl_r = -1.0
    elif adaptive_target:
        # Check if actual price reached adaptive target
        hit_adaptive = (actual_exit >= adaptive_target) if is_bullish else (actual_exit <= adaptive_target)
        if hit_adaptive:
            adaptive_pnl_r = abs(adaptive_target - entry) / ib_range
        else:
            # Closed before adaptive target — same as bracket partial
            adaptive_pnl_r = bracket_pnl_r
    else:
        # No better level found — same as bracket
        adaptive_pnl_r = bracket_pnl_r

    return {
        'bracket_pnl_r':  round(bracket_pnl_r, 4),
        'adaptive_pnl_r': round(adaptive_pnl_r, 4),
        'had_better_level': adaptive_target is not None,
        'macd_confirmed':   macd_confirms,
    }

# ─────────────────────────────────────────────────────────────────────────────
# Summary stats
# ─────────────────────────────────────────────────────────────────────────────

def summarize(results, label):
    wins   = [r for r in results if r > 0]
    losses = [r for r in results if r < 0]
    flat   = [r for r in results if r == 0]
    total  = len(results)
    wr     = len(wins) / total * 100 if total else 0
    exp    = sum(results) / total if total else 0
    avg_w  = sum(wins) / len(wins) if wins else 0
    avg_l  = sum(losses) / len(losses) if losses else 0
    print(f'\n  ── {label} ({total} trades) ──')
    print(f'  Win rate   : {wr:.1f}%  ({len(wins)}W / {len(losses)}L / {len(flat)} flat)')
    print(f'  Expectancy : {exp:+.4f}R / trade')
    print(f'  Total R    : {sum(results):+.2f}R')
    print(f'  Avg winner : {avg_w:+.4f}R')
    print(f'  Avg loser  : {avg_l:+.4f}R')
    return {'wr': wr, 'expectancy': exp, 'total_r': sum(results), 'n': total}

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print('Loading backtest rows…')
    trades = load_backtest_rows()
    print(f'  → {len(trades)} settled breakout trades')

    print('Loading context levels…')
    ctx = load_context_levels()
    print(f'  → {len(ctx)} context level rows')

    matched = [(k, trades[k], ctx[k]) for k in trades if k in ctx]
    print(f'  → {len(matched)} trades with full context data')

    if not matched:
        print('\nNo trades matched yet — run backfill_context_levels.py first.')
        return

    bracket_pnls  = []
    adaptive_pnls = []
    improved      = []
    degraded      = []
    same          = []

    by_scan   = defaultdict(lambda: {'bracket': [], 'adaptive': []})
    by_macd   = defaultdict(lambda: {'bracket': [], 'adaptive': []})

    for (ticker, trade_date, scan_type), trade_row, ctx_row in matched:
        sim = simulate_adaptive_exit(trade_row, ctx_row)
        if sim is None:
            continue

        b = sim['bracket_pnl_r']
        a = sim['adaptive_pnl_r']
        bracket_pnls.append(b)
        adaptive_pnls.append(a)
        by_scan[scan_type]['bracket'].append(b)
        by_scan[scan_type]['adaptive'].append(a)

        macd_dir = ctx_row.get('macd_direction', 'unknown')
        by_macd[macd_dir]['bracket'].append(b)
        by_macd[macd_dir]['adaptive'].append(a)

        diff = round(a - b, 4)
        if diff > 0.01:
            improved.append(diff)
        elif diff < -0.01:
            degraded.append(diff)
        else:
            same.append(diff)

    print('\n' + '=' * 60)
    print('  ADAPTIVE EXIT BACKTEST RESULTS')
    print('=' * 60)

    b_stats = summarize(bracket_pnls, 'BRACKET EXIT (current system)')
    a_stats = summarize(adaptive_pnls, 'ADAPTIVE S/R EXIT')

    exp_delta = a_stats['expectancy'] - b_stats['expectancy']
    r_delta   = a_stats['total_r'] - b_stats['total_r']
    wr_delta  = a_stats['wr'] - b_stats['wr']
    print(f'\n  ── DELTA (Adaptive − Bracket) ──')
    print(f'  Win rate   : {wr_delta:+.1f}%')
    print(f'  Expectancy : {exp_delta:+.4f}R / trade')
    print(f'  Total R    : {r_delta:+.2f}R')

    print(f'\n  ── TRADE-LEVEL IMPACT ──')
    pct_improved = len(improved) / len(bracket_pnls) * 100 if bracket_pnls else 0
    pct_degraded = len(degraded) / len(bracket_pnls) * 100 if bracket_pnls else 0
    print(f'  Improved   : {len(improved)} trades ({pct_improved:.1f}%) | avg gain {sum(improved)/len(improved):+.4f}R' if improved else '  Improved   : 0')
    print(f'  Degraded   : {len(degraded)} trades ({pct_degraded:.1f}%) | avg loss {sum(degraded)/len(degraded):+.4f}R' if degraded else '  Degraded   : 0')
    print(f'  Unchanged  : {len(same)} trades')

    print(f'\n  ── BY SCAN TYPE ──')
    for st, d in sorted(by_scan.items()):
        if d['bracket']:
            b_exp = sum(d['bracket']) / len(d['bracket'])
            a_exp = sum(d['adaptive']) / len(d['adaptive'])
            b_wr  = sum(1 for x in d['bracket'] if x > 0) / len(d['bracket']) * 100
            a_wr  = sum(1 for x in d['adaptive'] if x > 0) / len(d['adaptive']) * 100
            print(f'  {st:15s}: bracket {b_wr:.1f}% WR {b_exp:+.3f}R  →  adaptive {a_wr:.1f}% WR {a_exp:+.3f}R  ({a_exp-b_exp:+.3f}R delta)')

    print(f'\n  ── BY MACD DIRECTION AT SIGNAL ──')
    for direction, d in sorted(by_macd.items()):
        if d['bracket']:
            b_exp = sum(d['bracket']) / len(d['bracket'])
            a_exp = sum(d['adaptive']) / len(d['adaptive'])
            b_wr  = sum(1 for x in d['bracket'] if x > 0) / len(d['bracket']) * 100
            a_wr  = sum(1 for x in d['adaptive'] if x > 0) / len(d['adaptive']) * 100
            n     = len(d['bracket'])
            print(f'  MACD {direction:8s} (n={n:4d}): bracket {b_wr:.1f}% WR {b_exp:+.3f}R  →  adaptive {a_wr:.1f}% WR {a_exp:+.3f}R')

    verdict = 'ADAPTIVE EXITS ARE BETTER' if exp_delta > 0.05 else (
              'NO MEANINGFUL DIFFERENCE'   if abs(exp_delta) <= 0.05 else
              'BRACKET EXITS ARE BETTER')
    print(f'\n  VERDICT: {verdict}')
    print('=' * 60)

if __name__ == '__main__':
    main()
