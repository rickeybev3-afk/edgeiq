"""
calibrate_sp_mult.py
--------------------
Unified position-size multiplier calibration for any screener pass.

Replaces calibrate_gap_down_mult.py and calibrate_squeeze_mult.py.

Usage:
  python calibrate_sp_mult.py                      # list available passes
  python calibrate_sp_mult.py --pass squeeze        # calibrate the squeeze pass
  python calibrate_sp_mult.py --pass gap_down       # calibrate the gap_down pass
  python calibrate_sp_mult.py --pass squeeze --apply  # calibrate AND patch paper_trader_bot.py
  python calibrate_sp_mult.py --self-test           # run deterministic unit tests

Methodology (mirrors the 5-year backtest used across passes):
  1. Pull all settled rows for the chosen pass (tiered_pnl_r IS NOT NULL).
  2. Compute win rate (win_loss='Win') and average R (tiered_pnl_r mean).
  3. Compute R-expectancy = WR * avg_win_R  +  (1-WR) * avg_loss_R.
  4. Compare against the 'gap' pass (>=3% gap universe) which anchors at 1.00x.
  5. Apply a sqrt-dampened ratio so one outlier week can't swing sizing by >+/-30%.
     mult = max(0.70, min(1.30, sqrt(expectancy_pass / expectancy_gap)))
  6. Round to nearest 0.05 for a clean table entry.
  7. Print a data-citation block ready to paste above _SP_MULT_TABLE.
     With --apply: patch paper_trader_bot.py directly (backup created first).

Requirements:
  SUPABASE_URL, SUPABASE_KEY environment variables must be set (same as main app).
  The backend.py module must be importable from the project root.

Minimum sample: 30 settled trades. Script exits early with a status message
if the count is below that floor.
"""

import argparse
import difflib
import math
import os
import re
import shutil
import statistics
import sys

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

ANCHOR_PASS = "gap"

CONTEXT_PASSES = ["other", "trend"]

PASS_CONFIG: dict[str, dict] = {
    "gap_down": {
        "screener_pass": "gap_down",
        "predicted": "Bearish Break",
        # header printed at the top of the output
        "title": "Bearish Break (gap_down) Position-Size Multiplier Calibration",
        # label shown in the trade-count and statistics tables
        "count_label": "gap_down (Bearish Break)",
        "stat_label": "gap_down",
        # text inserted inside the parentheses in the data-citation line, e.g.:
        #   'gap_down' (Bearish Break, 2024-01-03 → 2024-12-31): ...
        # Use "" to omit the inner parenthetical (see squeeze below).
        "citation_inner": "Bearish Break, ",
        # optional prefix for the comment on the "Next step" line
        "next_step_comment_prefix": "Bearish Break — ",
    },
    "squeeze": {
        "screener_pass": "squeeze",
        "predicted": None,
        "title": "Squeeze Pass Position-Size Multiplier Calibration",
        "count_label": "squeeze",
        "stat_label": "squeeze",
        # squeeze citation: 'squeeze' (2024-01-03 → 2024-12-31): ...
        "citation_inner": "",
        "next_step_comment_prefix": "",
    },
}


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


def _recommend_mult(pass_exp: float, gap_exp: float) -> float:
    """
    Recommend a multiplier anchored to 'gap' = 1.00×.

    Uses a sqrt-dampened ratio so a 2× expectancy advantage only becomes 1.41×,
    preventing one good/bad month from swinging sizing to an extreme.
    Clamped to [0.70, 1.30] and rounded to nearest 0.05.

    Edge-case handling (returns conservative fixed values, never crashes):
      gap_exp  <= 0 → can't anchor; return 1.00 (baseline, no change)
      pass_exp <= 0 → strategy has negative/zero expectancy; return 0.70
                      (minimum clamp — reduce sizing conservatively)
    """
    if gap_exp <= 0:
        return 1.00
    if pass_exp <= 0:
        return 0.70
    raw_ratio = pass_exp / gap_exp
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
        ("zero pass exp",              0.000, 0.327, 0.70),
        ("negative pass exp",         -0.100, 0.327, 0.70),
        ("zero gap anchor",            0.400, 0.000, 1.00),
        ("negative gap anchor",        0.400,-0.100, 1.00),
    ]
    all_ok = True
    for label, ps_exp, ga_exp, expected in cases:
        result = _recommend_mult(ps_exp, ga_exp)
        ok = abs(result - expected) < 0.001
        print(f"  {'OK  ' if ok else 'FAIL'} {label}: _recommend_mult({ps_exp}, {ga_exp}) = {result} (expected {expected})")
        if not ok:
            all_ok = False
    if all_ok:
        print("All self-tests passed.")
    else:
        print("SELF-TEST FAILURES — do not trust the recommendation above.")
        sys.exit(1)


_APPLY_FIXTURE = """\
# preceding source

_SP_MULT_TABLE: dict[str, float] = {
    "other":    1.15,
    "gap":      1.00,
    "trend":    0.85,
    "squeeze":  1.00,   # baseline; recalibrate once >=30 trades settle
    "gap_down": 1.00,   # Bearish Break — baseline; recalibrate once >=30 trades settle
}

def _sp_size_mult(screener_pass):
    return _SP_MULT_TABLE.get(screener_pass, 1.00)
"""


def _self_test_apply() -> None:
    """Deterministic unit tests for _apply_to_bot() using an in-memory fixture."""
    import tempfile

    all_ok = True

    def _run(label: str, pass_name: str, new_mult: float, comment: str,
             expect_value: float, expect_comment_fragment: str) -> None:
        nonlocal all_ok
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
            tmp = tf.name
            tf.write(_APPLY_FIXTURE)
        try:
            _apply_to_bot(pass_name, new_mult, comment, bot_path=tmp)
            with open(tmp) as fh:
                patched = fh.read()
            # Find the patched entry line
            pat = re.compile(r'"' + re.escape(pass_name) + r'"\s*:\s*([\d.]+)')
            m = pat.search(patched)
            if not m:
                print(f"  FAIL {label}: '{pass_name}' entry not found after patch")
                all_ok = False
                return
            got_value = float(m.group(1))
            value_ok = abs(got_value - expect_value) < 0.001
            comment_ok = expect_comment_fragment in patched
            ok = value_ok and comment_ok
            print(
                f"  {'OK  ' if ok else 'FAIL'} {label}: "
                f"value={got_value:.2f} (expected {expect_value:.2f}), "
                f"comment_fragment={'found' if comment_ok else 'MISSING'}"
            )
            if not ok:
                all_ok = False
            # Verify other entries are untouched
            other_pat = re.compile(r'"other"\s*:\s*([\d.]+)')
            om = other_pat.search(patched)
            if om and abs(float(om.group(1)) - 1.15) > 0.001:
                print(f"  FAIL {label}: 'other' entry was corrupted (got {om.group(1)})")
                all_ok = False
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            try:
                os.unlink(tmp + ".bak")
            except OSError:
                pass

    _run(
        "squeeze baseline→1.15",
        "squeeze", 1.15, "47 trades, 72.3% WR / +0.411R → 1.15×",
        expect_value=1.15, expect_comment_fragment="72.3% WR",
    )
    _run(
        "gap_down baseline→0.85",
        "gap_down", 0.85, "33 trades, 58.1% WR / +0.290R → 0.85×",
        expect_value=0.85, expect_comment_fragment="58.1% WR",
    )
    _run(
        "squeeze idempotent re-apply",
        "squeeze", 1.15, "47 trades, 72.3% WR / +0.411R → 1.15×",
        expect_value=1.15, expect_comment_fragment="1.15×",
    )

    if all_ok:
        print("All _apply_to_bot self-tests passed.")
    else:
        print("SELF-TEST FAILURES in _apply_to_bot — check the output above.")
        sys.exit(1)


def _list_passes() -> None:
    """Print available pass names and exit."""
    print("Available screener passes for calibration:")
    for name, cfg in PASS_CONFIG.items():
        predicted = cfg.get("predicted")
        detail = f"  (predicted='{predicted}')" if predicted else ""
        print(f"  --pass {name:<12}  screener_pass='{cfg['screener_pass']}'{detail}")
    print()
    print("Usage:  python calibrate_sp_mult.py --pass <pass_name>")


_BOT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trader_bot.py")


def _apply_to_bot(pass_name: str, rec_mult: float, inline_comment: str, bot_path: str = _BOT_FILE) -> None:
    """Patch _SP_MULT_TABLE[pass_name] in paper_trader_bot.py in place.

    Steps:
      1. Read the file and locate the _SP_MULT_TABLE block.
      2. Replace the numeric value and trailing comment for pass_name.
      3. Write a .bak backup of the original.
      4. Print a unified diff of the change.
      5. Write the patched file.

    Exits with a non-zero status if the table or entry cannot be found.
    """
    try:
        with open(bot_path) as fh:
            original = fh.read()
    except OSError as exc:
        print(f"ERROR: cannot read {bot_path} — {exc}", file=sys.stderr)
        sys.exit(1)

    table_start = original.find("_SP_MULT_TABLE")
    if table_start == -1:
        print("ERROR: _SP_MULT_TABLE not found in paper_trader_bot.py", file=sys.stderr)
        sys.exit(1)

    brace_open = original.find("{", table_start)
    brace_close = original.find("}", brace_open)
    if brace_open == -1 or brace_close == -1:
        print("ERROR: could not locate _SP_MULT_TABLE braces in paper_trader_bot.py", file=sys.stderr)
        sys.exit(1)

    block = original[brace_open : brace_close + 1]

    entry_pat = re.compile(
        r'("' + re.escape(pass_name) + r'"(\s*):(\s*))([\d.]+)(,[ \t]*)([^\n]*)?',
    )
    if not entry_pat.search(block):
        print(
            f"ERROR: could not find '{pass_name}' entry inside _SP_MULT_TABLE in paper_trader_bot.py",
            file=sys.stderr,
        )
        sys.exit(1)

    def _replacer(m: re.Match) -> str:
        key_part = m.group(1)
        comma_spaces = m.group(5)
        return f"{key_part}{rec_mult:.2f}{comma_spaces}# {inline_comment}"

    new_block, count = entry_pat.subn(_replacer, block, count=1)
    if count == 0:
        print(
            f"ERROR: replacement failed for '{pass_name}' in _SP_MULT_TABLE",
            file=sys.stderr,
        )
        sys.exit(1)

    new_content = original[:brace_open] + new_block + original[brace_close + 1 :]

    backup_path = bot_path + ".bak"
    try:
        shutil.copy2(bot_path, backup_path)
    except OSError as exc:
        print(f"ERROR: could not create backup {backup_path} — {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Backup written → {backup_path}")

    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile="paper_trader_bot.py (original)",
            tofile="paper_trader_bot.py (patched)",
            n=3,
        )
    )
    if diff_lines:
        print("\nDiff:")
        print("".join(diff_lines))
    else:
        print("\n(No change — value was already set to the recommended multiplier.)")

    try:
        with open(bot_path, "w") as fh:
            fh.write(new_content)
    except OSError as exc:
        print(f"ERROR: could not write patched file — {exc}", file=sys.stderr)
        print(f"The original is preserved in {backup_path}.", file=sys.stderr)
        sys.exit(1)

    print(f"\nApplied: _SP_MULT_TABLE['{pass_name}'] = {rec_mult:.2f}")


def main(pass_name: str, apply: bool = False) -> None:
    cfg = PASS_CONFIG[pass_name]
    screener_pass = cfg["screener_pass"]
    predicted = cfg.get("predicted")
    title = cfg["title"]
    count_label = cfg["count_label"]
    stat_label = cfg["stat_label"]
    citation_inner = cfg["citation_inner"]
    next_step_comment_prefix = cfg["next_step_comment_prefix"]

    print("=" * 60)
    print(title)
    print("=" * 60)

    target_rows = _fetch_settled(screener_pass, predicted)
    gap_rows = _fetch_settled(ANCHOR_PASS)
    ctx_stats: list[tuple[str, dict]] = []
    for ctx_pass in CONTEXT_PASSES:
        ctx_rows = _fetch_settled(ctx_pass)
        ctx_stats.append((ctx_pass, _stats(ctx_rows)))

    tgt = _stats(target_rows)
    ga = _stats(gap_rows)

    print(f"\nSettled trade counts (tiered_pnl_r NOT NULL):")
    all_count_rows = [(count_label, tgt), (ANCHOR_PASS, ga)] + [(l, s) for l, s in ctx_stats]
    col_w = max(len(l) for l, _ in all_count_rows) + 1
    for lbl, s in all_count_rows:
        print(f"  {lbl + ':':<{col_w}}  {s['n']}")

    if tgt["n"] < MIN_TRADES:
        print(
            f"\n⛔  Only {tgt['n']} settled {count_label} trades found — "
            f"minimum is {MIN_TRADES}.\n"
            f"    Re-run this script once {MIN_TRADES - tgt['n']} more trades settle.\n"
            f"    _SP_MULT_TABLE['{pass_name}'] remains at 1.00× (safe baseline)."
        )
        sys.exit(0)

    def _fmt(v: float | None) -> str:
        return f"{v:+.3f}" if v is not None else "N/A"

    print(f"\nStatistics:")
    all_stat_rows = [(stat_label, tgt), (ANCHOR_PASS, ga)] + list(ctx_stats)
    for lbl, s in all_stat_rows:
        if s["n"] > 0 and s["wr"] is not None:
            print(
                f"  {lbl:<10}  n={s['n']:>4}  WR={s['wr']:.1%}  "
                f"avg_R={_fmt(s['avg_r'])}  "
                f"avg_win_R={_fmt(s['avg_win_r'])}  avg_loss_R={_fmt(s['avg_loss_r'])}  "
                f"expectancy={_fmt(s['expectancy'])}"
            )
        else:
            print(f"  {lbl:<10}  n={s['n']:>4}  (no settled data)")

    ga_exp = ga["expectancy"]
    tgt_exp = tgt["expectancy"]

    if ga_exp is None:
        print(f"\nWARNING: no settled '{ANCHOR_PASS}' trades — cannot anchor multiplier. Using 1.00×.")
        rec_mult = 1.00
    else:
        rec_mult = _recommend_mult(tgt_exp, ga_exp)

    date_range = ""
    if target_rows:
        dates = sorted(r.get("trade_date", "") for r in target_rows if r.get("trade_date"))
        if dates:
            date_range = f"{dates[0]} → {dates[-1]}"

    print(f"\n{'='*60}")
    print(f"RECOMMENDATION:  _SP_MULT_TABLE['{pass_name}'] = {rec_mult:.2f}")
    print(f"{'='*60}")
    wr_str = f"{tgt['wr']:.1%}" if tgt["wr"] is not None else "N/A"

    # Citation line — format matches originals:
    #   gap_down: #   'gap_down' (Bearish Break, <dates>): N trades, ...
    #   squeeze:  #   'squeeze' (<dates>): N trades, ...
    if citation_inner:
        citation_parens = f"({citation_inner}{date_range})"
    else:
        citation_parens = f"({date_range})" if date_range else ""

    print(
        f"\nData citation to paste above _SP_MULT_TABLE in paper_trader_bot.py:\n"
        f"\n"
        f"#   '{pass_name}' {citation_parens}: "
        f"{tgt['n']} trades, {wr_str} WR / {_fmt(tgt['avg_r'])}R avg → "
        f"{rec_mult:.2f}×"
    )

    if ga_exp is not None and ga_exp > 0 and tgt_exp is not None and tgt_exp > 0:
        raw = tgt_exp / ga_exp
        print(
            f"\nMethodology: sqrt-dampened ratio vs '{ANCHOR_PASS}' expectancy "
            f"({tgt_exp:+.3f}R ÷ {ga_exp:+.3f}R = "
            f"{raw:.3f} → sqrt → {math.sqrt(raw):.3f} → "
            f"clamped [0.70, 1.30] → rounded to nearest 0.05 → {rec_mult:.2f}×)"
        )
    else:
        print(
            f"\nMethodology: edge-case path (pass_exp={tgt_exp}, gap_exp={ga_exp}) — "
            f"conservative fixed value {rec_mult:.2f}× returned."
        )

    wr_comment = f"{next_step_comment_prefix}{tgt['n']} trades {date_range}, {wr_str} WR / {_fmt(tgt['avg_r'])}R"

    if apply:
        print(f"\n{'='*60}")
        print("Applying change to paper_trader_bot.py  (--apply flag set)")
        print(f"{'='*60}")
        inline_comment = f"{wr_comment} → {rec_mult:.2f}×"
        _apply_to_bot(pass_name, rec_mult, inline_comment)
    else:
        print(
            f"\nNext step: manually update paper_trader_bot.py, or re-run with --apply to patch automatically:\n"
            f"    \"{pass_name}\": {rec_mult:.2f},   "
            f"# {wr_comment}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Calibrate _SP_MULT_TABLE for any screener pass.",
        add_help=True,
    )
    parser.add_argument(
        "--pass",
        dest="pass_name",
        metavar="PASS",
        help=f"Screener pass to calibrate. Available: {', '.join(PASS_CONFIG)}",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run deterministic unit tests on _recommend_mult() and exit.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Patch _SP_MULT_TABLE in paper_trader_bot.py in place. "
            "A .bak backup is created and a diff is printed before writing."
        ),
    )
    args = parser.parse_args()

    if args.self_test:
        print("Running _recommend_mult() self-tests...")
        _self_test()
        print("\nRunning _apply_to_bot() self-tests...")
        _self_test_apply()
        sys.exit(0)

    if not args.pass_name:
        _list_passes()
        sys.exit(0)

    if args.pass_name not in PASS_CONFIG:
        known = ", ".join(PASS_CONFIG)
        print(f"ERROR: unknown pass '{args.pass_name}'. Known passes: {known}", file=sys.stderr)
        sys.exit(1)

    main(args.pass_name, apply=args.apply)
