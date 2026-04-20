"""
calibrate_gap_down_mult.py
--------------------------
Analyses settled Bearish Break paper trades (screener_pass='gap_down') and
recommends a calibrated position-size multiplier for _SP_MULT_TABLE in
paper_trader_bot.py.

Run once at least 30 gap_down Bearish Break trades have settled in paper_trades.

Methodology (mirrors the 5-year backtest used for other passes):
  1. Pull all settled gap_down Bearish Break rows (tiered_pnl_r IS NOT NULL).
  2. Compute win rate (win_loss='Win') and average R (tiered_pnl_r mean).
  3. Compute R-expectancy = WR * avg_win_R  +  (1-WR) * avg_loss_R.
  4. Compare against the 'gap' pass (≥3% gap universe) which anchors at 1.00×.
  5. Apply a sqrt-dampened ratio so one outlier week can't swing sizing by >±30%.
     mult = max(0.70, min(1.30, sqrt(expectancy_gap_down / expectancy_gap)))
  6. Round to nearest 0.05 for clean table entry.
  7. Print data-citation block ready to paste above _SP_MULT_TABLE.

Usage:
  python calibrate_gap_down_mult.py

Requirements:
  SUPABASE_URL, SUPABASE_KEY environment variables must be set (same as main app).
  The backend.py module must be importable from the project root.

Minimum sample: 30 settled trades. Script exits early with a status message
if the count is below that floor.
"""

import sys
import math
import statistics
import os

sys.path.insert(0, os.path.dirname(__file__))

try:
    from backend import supabase
except ImportError as exc:
    print(f"ERROR: could not import supabase from backend.py — {exc}", file=sys.stderr)
    sys.exit(1)

if not supabase:
    print("ERROR: Supabase client is not initialised. Check SUPABASE_URL / SUPABASE_KEY.", file=sys.stderr)
    sys.exit(1)

MIN_TRADES = 30


def _fetch_settled(screener_pass: str, predicted: str | None = None) -> list[dict]:
    """Return all settled rows for a given screener_pass (tiered_pnl_r NOT NULL)."""
    q = (
        supabase
        .table("paper_trades")
        .select("id,trade_date,win_loss,tiered_pnl_r,pnl_r_sim,predicted")
        .eq("screener_pass", screener_pass)
        .not_.is_("tiered_pnl_r", "null")
    )
    if predicted:
        q = q.eq("predicted", predicted)

    all_rows: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        resp = q.range(offset, offset + page_size - 1).execute()
        all_rows.extend(resp.data or [])
        if len(resp.data or []) < page_size:
            break
        offset += page_size
    return all_rows


def _stats(rows: list[dict]) -> dict:
    """Compute win-rate, avg-R and R-expectancy from a list of settled rows."""
    if not rows:
        return {"n": 0, "wr": None, "avg_r": None, "expectancy": None,
                "avg_win_r": None, "avg_loss_r": None}

    wins = [r for r in rows if (r.get("win_loss") or "").upper() == "WIN"]
    losses = [r for r in rows if (r.get("win_loss") or "").upper() != "WIN"]

    wr = len(wins) / len(rows)
    all_r = [r["tiered_pnl_r"] for r in rows if r.get("tiered_pnl_r") is not None]
    win_r = [r["tiered_pnl_r"] for r in wins if r.get("tiered_pnl_r") is not None]
    loss_r = [r["tiered_pnl_r"] for r in losses if r.get("tiered_pnl_r") is not None]

    avg_r = statistics.mean(all_r) if all_r else None
    avg_win_r = statistics.mean(win_r) if win_r else None
    avg_loss_r = statistics.mean(loss_r) if loss_r else None

    if avg_win_r is not None and avg_loss_r is not None:
        expectancy = wr * avg_win_r + (1 - wr) * avg_loss_r
    else:
        expectancy = avg_r

    return {
        "n": len(rows),
        "wr": wr,
        "avg_r": avg_r,
        "expectancy": expectancy,
        "avg_win_r": avg_win_r,
        "avg_loss_r": avg_loss_r,
    }


def _recommend_mult(gap_down_exp: float, gap_exp: float) -> float:
    """
    Recommend a multiplier anchored to 'gap' = 1.00×.

    Uses a sqrt-dampened ratio so a 2× expectancy advantage only becomes 1.41×,
    preventing one good/bad month from swinging sizing to an extreme.
    Clamped to [0.70, 1.30] and rounded to nearest 0.05.

    Edge-case handling (returns conservative fixed values, never crashes):
      gap_exp  <= 0 → can't anchor; return 1.00 (baseline, no change)
      gap_down_exp <= 0 → strategy has negative/zero expectancy; return 0.70
                          (minimum clamp — reduce sizing conservatively)
    """
    if gap_exp <= 0:
        return 1.00
    if gap_down_exp <= 0:
        return 0.70
    raw_ratio = gap_down_exp / gap_exp
    dampened = math.sqrt(raw_ratio)
    clamped = max(0.70, min(1.30, dampened))
    rounded = round(clamped / 0.05) * 0.05
    return rounded


def _self_test() -> None:
    """Quick deterministic check of _recommend_mult() for known inputs."""
    cases = [
        ("positive/positive equal",    0.327, 0.327, 1.00),
        ("positive/positive higher",   0.622, 0.327, 1.30),
        ("positive/positive lower",    0.164, 0.327, 0.70),
        ("zero gap_down",              0.000, 0.327, 0.70),
        ("negative gap_down",         -0.100, 0.327, 0.70),
        ("zero gap anchor",            0.400, 0.000, 1.00),
        ("negative gap anchor",        0.400,-0.100, 1.00),
    ]
    all_ok = True
    for label, gd_exp, ga_exp, expected in cases:
        result = _recommend_mult(gd_exp, ga_exp)
        ok = abs(result - expected) < 0.001
        print(f"  {'OK  ' if ok else 'FAIL'} {label}: _recommend_mult({gd_exp}, {ga_exp}) = {result} (expected {expected})")
        if not ok:
            all_ok = False
    if all_ok:
        print("All self-tests passed.")
    else:
        print("SELF-TEST FAILURES — do not trust the recommendation above.")
        sys.exit(1)


def main() -> None:
    print("=" * 60)
    print("Bearish Break (gap_down) Position-Size Multiplier Calibration")
    print("=" * 60)

    gap_down_rows = _fetch_settled("gap_down", predicted="Bearish Break")
    gap_rows = _fetch_settled("gap")
    other_rows = _fetch_settled("other")
    trend_rows = _fetch_settled("trend")

    gd = _stats(gap_down_rows)
    ga = _stats(gap_rows)
    ot = _stats(other_rows)
    tr = _stats(trend_rows)

    print(f"\nSettled trade counts (tiered_pnl_r NOT NULL):")
    print(f"  gap_down (Bearish Break):  {gd['n']}")
    print(f"  gap:                       {ga['n']}")
    print(f"  other:                     {ot['n']}")
    print(f"  trend:                     {tr['n']}")

    if gd["n"] < MIN_TRADES:
        print(
            f"\n⛔  Only {gd['n']} settled gap_down Bearish Break trades found — "
            f"minimum is {MIN_TRADES}.\n"
            f"    Re-run this script once {MIN_TRADES - gd['n']} more trades settle.\n"
            f"    _SP_MULT_TABLE['gap_down'] remains at 1.00× (safe baseline)."
        )
        sys.exit(0)

    def _fmt(v: float | None) -> str:
        return f"{v:+.3f}" if v is not None else "N/A"

    print(f"\nStatistics:")
    for label, s in [("gap_down", gd), ("gap", ga), ("other", ot), ("trend", tr)]:
        if s["n"] > 0 and s["wr"] is not None:
            print(
                f"  {label:<10}  n={s['n']:>4}  WR={s['wr']:.1%}  "
                f"avg_R={_fmt(s['avg_r'])}  "
                f"avg_win_R={_fmt(s['avg_win_r'])}  avg_loss_R={_fmt(s['avg_loss_r'])}  "
                f"expectancy={_fmt(s['expectancy'])}"
            )
        else:
            print(f"  {label:<10}  n={s['n']:>4}  (no settled data)")

    ga_exp = ga["expectancy"]
    gd_exp = gd["expectancy"]

    if ga_exp is None:
        print("\nWARNING: no settled 'gap' trades — cannot anchor multiplier. Using 1.00×.")
        rec_mult = 1.00
    else:
        rec_mult = _recommend_mult(gd_exp, ga_exp)

    date_range = ""
    if gap_down_rows:
        dates = sorted(r.get("trade_date", "") for r in gap_down_rows if r.get("trade_date"))
        if dates:
            date_range = f"{dates[0]} → {dates[-1]}"

    print(f"\n{'='*60}")
    print(f"RECOMMENDATION:  _SP_MULT_TABLE['gap_down'] = {rec_mult:.2f}")
    print(f"{'='*60}")
    wr_str = f"{gd['wr']:.1%}" if gd["wr"] is not None else "N/A"
    print(
        f"\nData citation to paste above _SP_MULT_TABLE in paper_trader_bot.py:\n"
        f"\n"
        f"#   'gap_down' (Bearish Break, {date_range}): "
        f"{gd['n']} trades, {wr_str} WR / {_fmt(gd['avg_r'])}R avg → "
        f"{rec_mult:.2f}×"
    )
    if ga_exp is not None and ga_exp > 0 and gd_exp is not None and gd_exp > 0:
        raw = gd_exp / ga_exp
        print(
            f"\nMethodology: sqrt-dampened ratio vs 'gap' expectancy "
            f"({gd_exp:+.3f}R ÷ {ga_exp:+.3f}R = "
            f"{raw:.3f} → sqrt → {math.sqrt(raw):.3f} → "
            f"clamped [0.70, 1.30] → rounded to nearest 0.05 → {rec_mult:.2f}×)"
        )
    else:
        print(
            f"\nMethodology: edge-case path (gap_down_exp={gd_exp}, gap_exp={ga_exp}) — "
            f"conservative fixed value {rec_mult:.2f}× returned."
        )
    print(
        f"\nNext step: update paper_trader_bot.py:\n"
        f"    \"gap_down\": {rec_mult:.2f},   "
        f"# Bearish Break — {gd['n']} trades {date_range}, "
        f"{wr_str} WR / {_fmt(gd['avg_r'])}R"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        print("Running _recommend_mult() self-tests...")
        _self_test()
    else:
        main()
