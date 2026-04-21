"""
calibrate_adaptive_mgmt.py
--------------------------
Auto-calibrate the TP adjustment multiplier used by adaptive position
management once enough adaptive trades have settled.

Usage:
  python calibrate_adaptive_mgmt.py            # dry-run: show recommendation
  python calibrate_adaptive_mgmt.py --apply    # write optimal value to adaptive_exits.json
  python calibrate_adaptive_mgmt.py --self-test  # run deterministic unit tests

Methodology:
  1. Pull all settled adaptive trades from paper_trades
     (mgmt_mode='adaptive', tiered_pnl_r NOT NULL, tp_adjusted_r NOT NULL,
     plus entry_price_sim, stop_price_sim, target_price_sim to derive the
     original base target-R and identify true TP_RAISED rows).

  2. Guard: exit if fewer than 50 total settled adaptive trades are found
     (same threshold stated in the task spec; matches the spirit of "enough
     adaptive trades have settled").  This is checked before filtering.

  3. Identify TP_RAISED rows: rows where the TP was actually moved up (or down
     for Bearish Break) vs the original target.  A row is classified as
     TP_RAISED when:
       tp_adjusted_r  >  original_target_r + 0.10
     where original_target_r = |target_price_sim - entry_price_sim|
                                / |entry_price_sim - stop_price_sim|
     (the 0.10 buffer filters floating-point noise and stop-tighten rows
     where the TP is unchanged and therefore tp_adjusted_r ≈ original_target_r).

  4. Counterfactual baseline: pull fixed-mode same-day trades
     (mgmt_mode IN ('fixed', 'adaptive_eligible') or mgmt_mode IS NULL,
     tiered_pnl_r NOT NULL) from trading days that overlap the TP_RAISED
     date window, and compute their expected R.  This answers: "Is adaptive
     TP-raising adding edge at all over fixed exits?"

  5. For each candidate multiplier in [0.25, 0.50, 0.75, 1.00]:
       a. Derive base_r per trade from actual price columns:
            base_r = original_target_r  (from entry/stop/target_price_sim)
          This is robust to future multiplier changes — it does not assume
          a fixed 0.50R was applied historically.
       b. Simulate outcome at candidate multiplier m:
            sim_tp_r  = base_r + m
            If trade was a WIN (tiered_pnl_r > 0):
              sim_r = min(tiered_pnl_r, sim_tp_r)
              (Cap at observed tiered_pnl_r so we never extrapolate past
               observed MFE for higher multipliers.)
            If trade was a LOSS (tiered_pnl_r <= 0):
              sim_r = tiered_pnl_r  (stop hit regardless of TP level)
       c. Compute expected R = mean(sim_r).
  6. Pick the multiplier with the highest expected R.
  7. With --apply: patch adaptive_exits.json with {"tp_raise_mult": <optimal>}.

Minimum sample: 50 total settled adaptive trades (same threshold as the task spec).
Script exits gracefully if count is below that floor.

Requirements:
  SUPABASE_URL, SUPABASE_KEY environment variables must be set (same as main app).
  The backend.py module must be importable from the project root.
"""

import argparse
import json
import os
import shutil
import statistics
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

# ── Supabase is only needed for live calibration runs, not --self-test ────────
# Import it lazily so the deterministic unit tests can run offline without DB
# credentials.  `supabase` is set here to None and resolved inside `main()`
# when actually needed.
supabase = None  # populated in main() unless running --self-test

def _require_supabase():
    """Import and return the Supabase client, exiting if unavailable."""
    global supabase
    if supabase is not None:
        return supabase
    try:
        from backend import supabase as _sb  # noqa: PLC0415
    except ImportError as exc:
        print(f"ERROR: could not import supabase from backend.py — {exc}", file=sys.stderr)
        sys.exit(1)
    if not _sb:
        print(
            "ERROR: Supabase client is not initialised. "
            "Check SUPABASE_URL / SUPABASE_KEY.",
            file=sys.stderr,
        )
        sys.exit(1)
    supabase = _sb
    return supabase

# ── Constants ──────────────────────────────────────────────────────────────────

MIN_TRADES = 50

GRID = [0.25, 0.50, 0.75, 1.00]

DEFAULT_MULT = 0.50

# Minimum delta between tp_adjusted_r and original_target_r to classify a row
# as TP_RAISED rather than STOP_TIGHTENED (where the TP is unchanged).
TP_RAISED_DELTA_THRESHOLD = 0.10

ADAPTIVE_EXITS_PATH = os.path.join(os.path.dirname(__file__), "adaptive_exits.json")

# mgmt_mode values that represent fixed (non-adaptive) exits for the baseline.
FIXED_MODE_VALUES = ("fixed", "adaptive_eligible")


# ── Data fetching ──────────────────────────────────────────────────────────────

def _fetch_adaptive_settled() -> list[dict]:
    """Return all settled adaptive rows with price columns for TP-raise detection."""
    sb = _require_supabase()
    q = (
        sb
        .table("paper_trades")
        .select(
            "id,trade_date,win_loss,tiered_pnl_r,"
            "tp_adjusted_r,entry_price_sim,stop_price_sim,target_price_sim"
        )
        .eq("mgmt_mode", "adaptive")
        .not_.is_("tiered_pnl_r", "null")
        .not_.is_("tp_adjusted_r", "null")
    )

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


def _fetch_fixed_settled(trade_dates: set[str]) -> list[dict]:
    """Return settled fixed-mode trades from the given set of trading dates.

    Used as the counterfactual baseline: what would E[R] look like if no
    adaptive TP adjustment had been applied?
    """
    if not trade_dates:
        return []

    sb = _require_supabase()
    all_rows: list[dict] = []
    page_size = 1000

    for mode in FIXED_MODE_VALUES:
        offset = 0
        q = (
            sb
            .table("paper_trades")
            .select("id,trade_date,win_loss,tiered_pnl_r")
            .eq("mgmt_mode", mode)
            .not_.is_("tiered_pnl_r", "null")
        )
        while True:
            resp = q.range(offset, offset + page_size - 1).execute()
            batch = resp.data or []
            # Filter client-side to matching dates (avoids long IN clause issues)
            all_rows.extend(r for r in batch if r.get("trade_date") in trade_dates)
            if len(batch) < page_size:
                break
            offset += page_size

    # Also include rows with no mgmt_mode set (original fixed-bracket trades)
    offset = 0
    q_null = (
        sb
        .table("paper_trades")
        .select("id,trade_date,win_loss,tiered_pnl_r")
        .is_("mgmt_mode", "null")
        .not_.is_("tiered_pnl_r", "null")
    )
    while True:
        resp = q_null.range(offset, offset + page_size - 1).execute()
        batch = resp.data or []
        all_rows.extend(r for r in batch if r.get("trade_date") in trade_dates)
        if len(batch) < page_size:
            break
        offset += page_size

    return all_rows


# ── TP_RAISED classification ───────────────────────────────────────────────────

def _original_target_r(row: dict) -> float | None:
    """Compute the original target-R from price columns.

    Returns None when any required price field is missing or invalid.
    original_target_r = |target_price_sim - entry_price_sim| / |entry_price_sim - stop_price_sim|
    """
    try:
        entry  = float(row["entry_price_sim"]  or 0)
        stop   = float(row["stop_price_sim"]   or 0)
        target = float(row["target_price_sim"] or 0)
    except (TypeError, KeyError, ValueError):
        return None

    if entry <= 0 or stop <= 0 or target <= 0:
        return None

    stop_dist = abs(entry - stop)
    if stop_dist < 1e-9:
        return None

    return abs(target - entry) / stop_dist


def _filter_tp_raised(rows: list[dict]) -> list[dict]:
    """Return only rows where the TP was actually raised (not STOP_TIGHTENED).

    A row is classified as TP_RAISED when:
      tp_adjusted_r  >  original_target_r + TP_RAISED_DELTA_THRESHOLD

    Rows where price columns are missing fall back to the simpler heuristic:
    if original_target_r cannot be computed, the row is excluded to be safe.
    """
    result = []
    for row in rows:
        tp_adj = row.get("tp_adjusted_r")
        if tp_adj is None:
            continue
        orig_r = _original_target_r(row)
        if orig_r is None:
            # Price columns absent — cannot reliably classify; skip.
            continue
        if float(tp_adj) > orig_r + TP_RAISED_DELTA_THRESHOLD:
            result.append(row)
    return result


# ── Grid search ────────────────────────────────────────────────────────────────

def _simulate_outcome(
    tiered_pnl_r: float,
    base_r: float,
    candidate_mult: float,
) -> float:
    """Simulate the trade outcome under a candidate TP multiplier.

    base_r     = the original target R computed from price columns
                 (|target_price_sim - entry_price_sim| / |entry_price_sim - stop_price_sim|).
                 Using price columns makes this robust to any past or future
                 applied multiplier value — we never assume +0.50 was used.
    sim_tp_r   = base_r + candidate_mult  (what the raised TP would be)

    Wins: cap simulated R at min(tiered_pnl_r, sim_tp_r) — never extrapolate
          above the observed actual R since we don't have raw MFE data.
    Losses: stop was hit regardless of TP level; keep tiered_pnl_r unchanged.
    """
    sim_tp_r = base_r + candidate_mult

    if tiered_pnl_r > 0:
        # Trade was a winner: limit simulated R to observed outcome (can't know
        # if MFE would have reached a higher TP than what actually happened).
        return min(tiered_pnl_r, sim_tp_r)
    else:
        # Trade was a loser: stop was hit; TP level is irrelevant.
        return tiered_pnl_r


def _grid_search(tp_raised_rows: list[dict]) -> dict:
    """Run the grid search and return per-multiplier stats.

    base_r is derived from each row's price columns (not from tp_adjusted_r),
    making the calibration robust regardless of which multiplier was applied
    when the trade was taken.

    Returns:
        {
            0.25: {"n": ..., "expected_r": ..., "wr": ...},
            0.50: {...},
            ...
        }
    """
    results = {}
    for m in GRID:
        simulated = []
        wins = 0
        for row in tp_raised_rows:
            pnl = row.get("tiered_pnl_r")
            if pnl is None:
                continue
            base_r = _original_target_r(row)
            if base_r is None:
                # Price columns unavailable — already filtered in _filter_tp_raised,
                # but guard here for safety.
                continue
            sim = _simulate_outcome(float(pnl), base_r, m)
            simulated.append(sim)
            if sim > 0:
                wins += 1

        n = len(simulated)
        if n == 0:
            results[m] = {"n": 0, "expected_r": None, "wr": None}
            continue

        expected_r = statistics.mean(simulated)
        wr = wins / n
        results[m] = {"n": n, "expected_r": expected_r, "wr": wr}

    return results


def _expected_r(rows: list[dict]) -> float | None:
    """Compute mean tiered_pnl_r for a list of settled trade rows."""
    vals = [float(r["tiered_pnl_r"]) for r in rows if r.get("tiered_pnl_r") is not None]
    return statistics.mean(vals) if vals else None


def _best_mult(grid_results: dict) -> float:
    """Return the multiplier with the highest expected R.

    Ties are broken by choosing the larger multiplier (more aggressive = higher
    upside when expectancies are equal).
    """
    best_m = DEFAULT_MULT
    best_e = None
    for m in sorted(grid_results.keys()):
        stats = grid_results[m]
        e = stats.get("expected_r")
        if e is None:
            continue
        if best_e is None or e > best_e:
            best_e = e
            best_m = m
        elif abs(e - best_e) < 1e-6 and m > best_m:
            best_m = m
    return best_m


# ── Self-test ──────────────────────────────────────────────────────────────────

def _self_test() -> None:
    """Deterministic unit tests for simulation, classification, and grid-search logic."""
    print("Running self-tests...")
    all_ok = True

    # ── _simulate_outcome tests ────────────────────────────────────────────────
    # Signature: _simulate_outcome(tiered_pnl_r, base_r, candidate_mult)
    # base_r is derived from price columns, NOT from tp_adjusted_r - some_mult.
    # Example: entry=100, stop=98, target=103 → base_r = 1.5
    cases_sim = [
        # (tiered_pnl_r, base_r, candidate_mult, expected)
        # Win, sim at 0.25: sim_tp=1.75, pnl=1.5 → min(1.5, 1.75)=1.5
        (1.5, 1.5, 0.25, 1.5),
        # Win, sim at 0.50: sim_tp=2.0, pnl=1.5 → min(1.5, 2.0)=1.5
        (1.5, 1.5, 0.50, 1.5),
        # Win, sim at 0.75: sim_tp=2.25, pnl=1.5 → min(1.5, 2.25)=1.5
        (1.5, 1.5, 0.75, 1.5),
        # Win, sim at 1.00: sim_tp=2.5, pnl=1.5 → min(1.5, 2.5)=1.5
        (1.5, 1.5, 1.00, 1.5),
        # Win, base_r=2.0, sim at 0.25: sim_tp=2.25, pnl=1.8 → min(1.8, 2.25)=1.8
        (1.8, 2.0, 0.25, 1.8),
        # Win, base_r=1.0, sim at 0.25: sim_tp=1.25, pnl=1.8 → min(1.8, 1.25)=1.25
        (1.8, 1.0, 0.25, 1.25),
        # Loss → stays loss regardless of multiplier
        (-1.0, 1.5, 0.25, -1.0),
        (-1.0, 1.5, 1.00, -1.0),
    ]

    for pnl, base_r, m, expected in cases_sim:
        result = _simulate_outcome(pnl, base_r, m)
        ok = abs(result - expected) < 1e-9
        print(
            f"  {'OK  ' if ok else 'FAIL'} _simulate_outcome({pnl}, base_r={base_r}, m={m})"
            f" = {result:.4f}  (expected {expected:.4f})"
        )
        if not ok:
            all_ok = False

    # ── _original_target_r tests ───────────────────────────────────────────────
    row_ok = {"entry_price_sim": 100.0, "stop_price_sim": 98.0, "target_price_sim": 103.0}
    otr = _original_target_r(row_ok)
    expected_otr = 1.5  # |103-100| / |100-98| = 3/2 = 1.5
    ok = otr is not None and abs(otr - expected_otr) < 1e-9
    print(f"  {'OK  ' if ok else 'FAIL'} _original_target_r(valid row) = {otr}  (expected {expected_otr})")
    if not ok:
        all_ok = False

    row_bad = {"entry_price_sim": 0, "stop_price_sim": 98.0, "target_price_sim": 103.0}
    otr_bad = _original_target_r(row_bad)
    ok = otr_bad is None
    print(f"  {'OK  ' if ok else 'FAIL'} _original_target_r(zero entry) = {otr_bad}  (expected None)")
    if not ok:
        all_ok = False

    # ── _filter_tp_raised tests ────────────────────────────────────────────────
    # TP was raised by 0.5R: tp_adjusted_r = 1.5 = 1.5 (original) + 0.5 → qualifies
    tp_raised_row = {
        "entry_price_sim": 100.0, "stop_price_sim": 98.0, "target_price_sim": 103.0,
        "tp_adjusted_r": 2.0, "tiered_pnl_r": 2.0,
    }
    # TP unchanged (STOP_TIGHTENED): tp_adjusted_r = original = 1.5
    stop_tighten_row = {
        "entry_price_sim": 100.0, "stop_price_sim": 98.0, "target_price_sim": 103.0,
        "tp_adjusted_r": 1.5, "tiered_pnl_r": 1.5,
    }
    # Missing price columns
    missing_price_row = {"tp_adjusted_r": 1.5, "tiered_pnl_r": 1.5}

    filtered = _filter_tp_raised([tp_raised_row, stop_tighten_row, missing_price_row])
    ok = len(filtered) == 1 and filtered[0] is tp_raised_row
    print(
        f"  {'OK  ' if ok else 'FAIL'} _filter_tp_raised: "
        f"{len(filtered)} row(s) kept  (expected 1, tp_raised_row only)"
    )
    if not ok:
        all_ok = False

    # ── _best_mult tests ───────────────────────────────────────────────────────
    tie_grid = {0.25: {"expected_r": 0.30}, 0.50: {"expected_r": 0.30}}
    best = _best_mult(tie_grid)
    ok = best == 0.50
    print(f"  {'OK  ' if ok else 'FAIL'} _best_mult(tie) = {best}  (expected 0.50)")
    if not ok:
        all_ok = False

    clear_winner = {0.25: {"expected_r": 0.20}, 0.50: {"expected_r": 0.35}, 0.75: {"expected_r": 0.28}}
    best = _best_mult(clear_winner)
    ok = best == 0.50
    print(f"  {'OK  ' if ok else 'FAIL'} _best_mult(clear winner 0.50) = {best}  (expected 0.50)")
    if not ok:
        all_ok = False

    if all_ok:
        print("All self-tests passed.")
    else:
        print("SELF-TEST FAILURES — do not trust the recommendation above.")
        sys.exit(1)


# ── Report printing ────────────────────────────────────────────────────────────

def _print_report(
    tp_raised_rows: list[dict],
    fixed_rows: list[dict],
    grid_results: dict,
    optimal: float,
) -> None:
    today = date.today().isoformat()
    n_adaptive = len(tp_raised_rows)
    n_fixed = len(fixed_rows)

    date_vals = sorted(r["trade_date"] for r in tp_raised_rows if r.get("trade_date"))
    date_range = (
        f"{date_vals[0]} → {date_vals[-1]}"
        if len(date_vals) >= 2
        else (date_vals[0] if date_vals else "n/a")
    )

    fixed_exp = _expected_r(fixed_rows)

    print()
    print("=" * 66)
    print("  Adaptive TP-Raise Multiplier Calibration")
    print(f"  Run date   : {today}")
    print(f"  TP_RAISED  : {n_adaptive} settled adaptive rows ({date_range})")
    print(
        f"  Fixed base : {n_fixed} matched fixed-mode rows, "
        f"E[R] = {fixed_exp:+.4f}R" if fixed_exp is not None
        else f"  Fixed base : {n_fixed} matched fixed-mode rows  (none — baseline unavailable)"
    )
    print("=" * 66)
    print()
    print(f"  {'Mult':>6}  {'N':>5}  {'WR':>7}  {'Exp R':>9}  {'vs Fixed':>9}")
    print(f"  {'-'*6}  {'-'*5}  {'-'*7}  {'-'*9}  {'-'*9}")
    for m in GRID:
        s = grid_results.get(m, {})
        n = s.get("n", 0)
        wr = s.get("wr")
        e  = s.get("expected_r")
        marker = " ← optimal" if abs(m - optimal) < 1e-9 else ""
        wr_str = f"{wr*100:6.1f}%" if wr is not None else "    n/a"
        e_str  = f"{e:+.4f}R"      if e  is not None else "       n/a"
        if e is not None and fixed_exp is not None:
            vs_str = f"{e - fixed_exp:+.4f}R"
        else:
            vs_str = "      n/a"
        print(f"  {m:>+6.2f}R  {n:>5}  {wr_str}  {e_str}  {vs_str}{marker}")

    print()
    print(f"  Recommendation: tp_raise_mult = {optimal:.2f}  (was {DEFAULT_MULT:.2f})")
    print()


# ── JSON patching ──────────────────────────────────────────────────────────────

def _apply_to_config(optimal: float, tp_raised_rows: list[dict], fixed_rows: list[dict]) -> None:
    """Write the optimal tp_raise_mult into adaptive_exits.json."""
    today = date.today().isoformat()
    n = len(tp_raised_rows)
    n_fixed = len(fixed_rows)
    date_vals = sorted(r["trade_date"] for r in tp_raised_rows if r.get("trade_date"))
    date_range = (
        f"{date_vals[0]} → {date_vals[-1]}"
        if len(date_vals) >= 2
        else (date_vals[0] if date_vals else "n/a")
    )
    fixed_exp = _expected_r(fixed_rows)
    fixed_note = (
        f"Fixed-mode baseline {fixed_exp:+.4f}R ({n_fixed} trades). "
        if fixed_exp is not None
        else ""
    )

    # Load existing config
    try:
        with open(ADAPTIVE_EXITS_PATH) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}

    # Back up before writing
    bak_path = ADAPTIVE_EXITS_PATH + ".bak"
    try:
        shutil.copy2(ADAPTIVE_EXITS_PATH, bak_path)
        print(f"  Backup written → {bak_path}")
    except Exception as _e:
        print(f"  WARNING: could not create backup: {_e}")

    old_mult = cfg.get("tp_raise_mult", DEFAULT_MULT)

    # Patch the relevant fields
    cfg["tp_raise_mult"] = optimal
    cfg["_tp_raise_mult_note"] = (
        f"Auto-calibrated from {n} settled TP_RAISED adaptive trades "
        f"({date_range}). {fixed_note}Updated: {today}."
    )
    cfg["last_updated"] = today

    with open(ADAPTIVE_EXITS_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")

    print(f"  adaptive_exits.json updated: tp_raise_mult {old_mult:.2f} → {optimal:.2f}")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate the adaptive TP-raise multiplier from settled paper trades."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the optimal multiplier to adaptive_exits.json.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        dest="self_test",
        help="Run deterministic unit tests and exit.",
    )
    args = parser.parse_args()

    if args.self_test:
        _self_test()
        return

    print("Fetching settled adaptive trades from paper_trades...")
    all_adaptive = _fetch_adaptive_settled()
    n_all = len(all_adaptive)

    # ── Minimum data guard (task spec: ≥ 50 adaptive trades settled) ──────────
    if n_all < MIN_TRADES:
        print(
            f"\n  ⏸  Only {n_all} settled adaptive trade(s) found "
            f"(minimum required: {MIN_TRADES}).\n"
            f"  No calibration performed — keeping current default "
            f"tp_raise_mult = {DEFAULT_MULT:.2f}.\n"
            f"  Re-run once ≥ {MIN_TRADES} adaptive trades have settled."
        )
        sys.exit(0)

    tp_raised_rows = _filter_tp_raised(all_adaptive)
    n_tp_raised = len(tp_raised_rows)

    print(
        f"  Found {n_all} settled adaptive row(s) total; "
        f"{n_tp_raised} classified as TP_RAISED (used for grid search)."
    )

    if n_tp_raised == 0:
        print(
            "\n  ⏸  No TP_RAISED rows could be classified (price columns missing).\n"
            "  No calibration performed."
        )
        sys.exit(0)

    # ── Counterfactual baseline: same-day fixed-mode trades ───────────────────
    adaptive_dates = {r["trade_date"] for r in tp_raised_rows if r.get("trade_date")}
    print(
        f"  Fetching fixed-mode baseline trades for "
        f"{len(adaptive_dates)} overlapping date(s)..."
    )
    fixed_rows = _fetch_fixed_settled(adaptive_dates)
    print(f"  Fixed-mode baseline: {len(fixed_rows)} settled trade(s) on matching dates.")

    # ── Grid search ───────────────────────────────────────────────────────────
    print(f"  Running grid search over {GRID}...")
    grid_results = _grid_search(tp_raised_rows)
    optimal = _best_mult(grid_results)

    _print_report(tp_raised_rows, fixed_rows, grid_results, optimal)

    if args.apply:
        _apply_to_config(optimal, tp_raised_rows, fixed_rows)
    else:
        print(
            "  Dry-run mode — no changes written.\n"
            "  Re-run with --apply to update adaptive_exits.json."
        )


if __name__ == "__main__":
    main()
