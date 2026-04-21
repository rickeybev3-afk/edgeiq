"""
calibrate_adaptive_mgmt.py
--------------------------
Auto-calibrate the TP-raise multiplier and stop-tighten fraction used by
adaptive position management once enough adaptive trades have settled.

Usage:
  python calibrate_adaptive_mgmt.py            # dry-run: show recommendation
  python calibrate_adaptive_mgmt.py --apply    # write optimal values to adaptive_exits.json
  python calibrate_adaptive_mgmt.py --self-test  # run deterministic unit tests

Methodology — TP_RAISED calibration:
  1. Pull all settled adaptive trades from paper_trades
     (mgmt_mode='adaptive', tiered_pnl_r NOT NULL, tp_adjusted_r NOT NULL,
     plus entry_price_sim, stop_price_sim, target_price_sim, ib_high, ib_low).

  2. Guard: exit if fewer than 50 total settled adaptive trades are found.

  3. Identify TP_RAISED rows: rows where the TP was actually moved up (or down
     for Bearish Break) vs the original target.  A row is classified as
     TP_RAISED when:
       tp_adjusted_r  >  original_target_r + 0.10
     where original_target_r = |target_price_sim - entry_price_sim|
                                / |entry_price_sim - stop_price_sim|

  4. Counterfactual baseline: pull fixed-mode same-day trades and compute E[R].

  5. For each candidate multiplier in [0.25, 0.50, 0.75, 1.00]:
       sim_tp_r = base_r + m
       Wins: sim_r = min(tiered_pnl_r, sim_tp_r)
       Losses: sim_r = tiered_pnl_r
  6. Pick the multiplier with the highest expected R.
  7. With --apply: patch adaptive_exits.json with {"tp_raise_mult": <optimal>}.

Methodology — STOP_TIGHTENED calibration:
  1. Identify STOP_TIGHTENED rows: rows where tp_adjusted_r ≈ original_target_r
     (the TP was unchanged; only the stop moved to the IB midpoint).
     Requires ib_high and ib_low to simulate alternate stop placements.

  2. For each candidate fraction in [0.25, 0.50, 0.75, 1.00] (fraction of the
     distance from entry to IB midpoint where the new stop is placed):
       ib_mid_dist_r = |entry - ib_mid| / |entry - stop|
       Wins:  sim_r = tiered_pnl_r  (stop position doesn't affect winners)
       Losses: sim_r = max(tiered_pnl_r, -(frac * ib_mid_dist_r))
         (A tighter stop caps the maximum loss; if the observed loss was already
          smaller — e.g. manual exit — keep the observed value.)
  3. Pick the fraction with the highest expected R.
  4. With --apply: patch adaptive_exits.json with {"stop_tighten_frac": <optimal>}.
     frac=1.0 reproduces the current hard-coded ib_mid behaviour.

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

# Grid and default for stop-tighten fraction calibration.
# frac=1.0 = stop moves to exact IB midpoint (current hardcoded behaviour).
STOP_TIGHTEN_FRAC_GRID = [0.25, 0.50, 0.75, 1.00]
DEFAULT_STOP_TIGHTEN_FRAC = 1.0

# Minimum delta between tp_adjusted_r and original_target_r to classify a row
# as TP_RAISED rather than STOP_TIGHTENED (where the TP is unchanged).
TP_RAISED_DELTA_THRESHOLD = 0.10

ADAPTIVE_EXITS_PATH = os.path.join(os.path.dirname(__file__), "adaptive_exits.json")

# mgmt_mode values that represent fixed (non-adaptive) exits for the baseline.
FIXED_MODE_VALUES = ("fixed", "adaptive_eligible")


# ── Data fetching ──────────────────────────────────────────────────────────────

def _fetch_adaptive_settled() -> list[dict]:
    """Return all settled adaptive rows with price and IB columns.

    Fetches ib_high and ib_low in addition to price columns so that both the
    TP-raise and stop-tighten grid searches can operate on the same dataset.
    """
    sb = _require_supabase()
    q = (
        sb
        .table("paper_trades")
        .select(
            "id,trade_date,win_loss,tiered_pnl_r,"
            "tp_adjusted_r,entry_price_sim,stop_price_sim,target_price_sim,"
            "ib_high,ib_low"
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


# ── STOP_TIGHTENED classification and simulation ───────────────────────────────

def _filter_stop_tightened(rows: list[dict]) -> list[dict]:
    """Return only rows where the stop was tightened (TP was NOT raised).

    A row is classified as STOP_TIGHTENED when:
      tp_adjusted_r  <=  original_target_r + TP_RAISED_DELTA_THRESHOLD

    Additionally, ib_high and ib_low must be present (needed to compute the
    IB midpoint distance for the simulation).  Rows with missing price or IB
    columns are excluded.
    """
    result = []
    for row in rows:
        tp_adj = row.get("tp_adjusted_r")
        if tp_adj is None:
            continue
        orig_r = _original_target_r(row)
        if orig_r is None:
            continue
        if float(tp_adj) > orig_r + TP_RAISED_DELTA_THRESHOLD:
            continue
        # Require IB bounds so we can simulate alternate stop placements.
        try:
            ib_high = float(row.get("ib_high") or 0)
            ib_low  = float(row.get("ib_low")  or 0)
        except (TypeError, ValueError):
            continue
        if ib_high <= 0 or ib_low <= 0 or ib_high <= ib_low:
            continue
        result.append(row)
    return result


def _ib_mid_dist_r(row: dict) -> float | None:
    """Compute the IB-midpoint stop distance expressed in R units.

    ib_mid_dist_r = |entry - ib_mid| / |entry - stop|

    This is the loss (in R) that would be realised if the stop were placed
    exactly at the IB midpoint (stop_tighten_frac = 1.0).  Returns None when
    any required field is missing or numerically invalid.
    """
    try:
        entry   = float(row["entry_price_sim"] or 0)
        stop    = float(row["stop_price_sim"]  or 0)
        ib_high = float(row["ib_high"]         or 0)
        ib_low  = float(row["ib_low"]          or 0)
    except (TypeError, KeyError, ValueError):
        return None

    if entry <= 0 or stop <= 0 or ib_high <= 0 or ib_low <= 0:
        return None
    if ib_high <= ib_low:
        return None

    stop_dist = abs(entry - stop)
    if stop_dist < 1e-9:
        return None

    ib_mid = (ib_high + ib_low) / 2.0
    return abs(entry - ib_mid) / stop_dist


def _simulate_stop_tighten_outcome(
    tiered_pnl_r: float,
    ib_mid_dist_r: float,
    frac: float,
) -> float:
    """Simulate the trade outcome under a candidate stop-tighten fraction.

    frac       = fraction of the distance from entry to IB mid where the
                 tightened stop is placed (1.0 = current behaviour: stop
                 moves to exact IB midpoint).
    ib_mid_dist_r = loss in R if stopped out at the IB midpoint (frac=1.0),
                    computed from price columns.

    Wins  (tiered_pnl_r > 0): stop placement has no effect — return unchanged.
    Losses (tiered_pnl_r <= 0): with a tighter stop the maximum loss is capped
      at -(frac * ib_mid_dist_r).  If the observed loss was already smaller
      (e.g. manual exit before the stop triggered), keep the observed value.

      sim_r = max(tiered_pnl_r, -(frac * ib_mid_dist_r))

    Note: at frac=1.0 the formula reproduces the actual observed outcome
    whenever the IB-mid stop was the exit trigger (tiered_pnl_r ≈ -ib_mid_dist_r).
    """
    if tiered_pnl_r > 0:
        return tiered_pnl_r
    sim_stop_r = frac * ib_mid_dist_r
    return max(tiered_pnl_r, -sim_stop_r)


def _grid_search_stop_tighten(stop_tightened_rows: list[dict]) -> dict:
    """Run the stop-tighten fraction grid search and return per-fraction stats.

    Returns:
        {
            0.25: {"n": ..., "expected_r": ..., "wr": ...},
            0.50: {...},
            ...
        }
    """
    results = {}
    for frac in STOP_TIGHTEN_FRAC_GRID:
        simulated = []
        wins = 0
        for row in stop_tightened_rows:
            pnl = row.get("tiered_pnl_r")
            if pnl is None:
                continue
            mid_dist = _ib_mid_dist_r(row)
            if mid_dist is None:
                continue
            sim = _simulate_stop_tighten_outcome(float(pnl), mid_dist, frac)
            simulated.append(sim)
            if sim > 0:
                wins += 1

        n = len(simulated)
        if n == 0:
            results[frac] = {"n": 0, "expected_r": None, "wr": None}
            continue

        expected_r = statistics.mean(simulated)
        wr = wins / n
        results[frac] = {"n": n, "expected_r": expected_r, "wr": wr}

    return results


def _best_stop_tighten_frac(grid_results: dict) -> float:
    """Return the stop-tighten fraction with the highest expected R.

    Ties are broken by choosing the smaller fraction (tighter stop = less risk
    when expectancies are equal).
    """
    best_frac = DEFAULT_STOP_TIGHTEN_FRAC
    best_e    = None
    for frac in sorted(grid_results.keys()):
        stats = grid_results[frac]
        e = stats.get("expected_r")
        if e is None:
            continue
        if best_e is None or e > best_e:
            best_e    = e
            best_frac = frac
        elif abs(e - best_e) < 1e-6 and frac < best_frac:
            best_frac = frac
    return best_frac


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

    # ── _ib_mid_dist_r tests ───────────────────────────────────────────────────
    # Bullish Break: entry=105, stop=103, ib_high=104, ib_low=100 → ib_mid=102
    # stop_dist=2, ib_mid_dist=|105-102|=3 → dist_r=3/2=1.5
    row_ib = {
        "entry_price_sim": 105.0, "stop_price_sim": 103.0,
        "ib_high": 104.0, "ib_low": 100.0,
    }
    mid_dist = _ib_mid_dist_r(row_ib)
    ok = mid_dist is not None and abs(mid_dist - 1.5) < 1e-9
    print(f"  {'OK  ' if ok else 'FAIL'} _ib_mid_dist_r(valid) = {mid_dist}  (expected 1.5)")
    if not ok:
        all_ok = False

    row_ib_bad = {"entry_price_sim": 0, "stop_price_sim": 103.0, "ib_high": 104.0, "ib_low": 100.0}
    ok = _ib_mid_dist_r(row_ib_bad) is None
    print(f"  {'OK  ' if ok else 'FAIL'} _ib_mid_dist_r(zero entry) = None")
    if not ok:
        all_ok = False

    # ── _simulate_stop_tighten_outcome tests ───────────────────────────────────
    # Win: sim_r always = tiered_pnl_r regardless of frac
    cases_st = [
        # (tiered_pnl_r, ib_mid_dist_r, frac, expected)
        # Win → unchanged
        (1.5, 1.5, 0.25, 1.5),
        (1.5, 1.5, 1.00, 1.5),
        # Loss at frac=1.0, stop exactly at ib_mid: max(-1.5, -1.0*1.5)=-1.5
        (-1.5, 1.5, 1.00, -1.5),
        # Loss at frac=0.50: max(-1.5, -0.5*1.5)=max(-1.5, -0.75)=-0.75 (tighter stop)
        (-1.5, 1.5, 0.50, -0.75),
        # Loss at frac=0.25: max(-1.5, -0.25*1.5)=max(-1.5, -0.375)=-0.375
        (-1.5, 1.5, 0.25, -0.375),
        # Loss already smaller than tighter stop: max(-0.3, -0.5*1.5)=max(-0.3, -0.75)=-0.3
        (-0.3, 1.5, 0.50, -0.3),
    ]
    for pnl, mid_r, frac, expected in cases_st:
        result = _simulate_stop_tighten_outcome(pnl, mid_r, frac)
        ok = abs(result - expected) < 1e-9
        print(
            f"  {'OK  ' if ok else 'FAIL'} _simulate_stop_tighten_outcome"
            f"({pnl}, ib_mid_r={mid_r}, frac={frac}) = {result:.4f}  (expected {expected:.4f})"
        )
        if not ok:
            all_ok = False

    # ── _filter_stop_tightened tests ──────────────────────────────────────────
    # TP unchanged, IB data present → should be kept
    st_row_valid = {
        "entry_price_sim": 100.0, "stop_price_sim": 98.0, "target_price_sim": 103.0,
        "tp_adjusted_r": 1.5, "tiered_pnl_r": -1.0,
        "ib_high": 101.0, "ib_low": 99.0,
    }
    # TP raised → must be excluded
    tp_raised_excl = {
        "entry_price_sim": 100.0, "stop_price_sim": 98.0, "target_price_sim": 103.0,
        "tp_adjusted_r": 2.0, "tiered_pnl_r": 2.0,
        "ib_high": 101.0, "ib_low": 99.0,
    }
    # IB columns missing → must be excluded
    no_ib_row = {
        "entry_price_sim": 100.0, "stop_price_sim": 98.0, "target_price_sim": 103.0,
        "tp_adjusted_r": 1.5, "tiered_pnl_r": -1.0,
    }
    filtered_st = _filter_stop_tightened([st_row_valid, tp_raised_excl, no_ib_row])
    ok = len(filtered_st) == 1 and filtered_st[0] is st_row_valid
    print(
        f"  {'OK  ' if ok else 'FAIL'} _filter_stop_tightened: "
        f"{len(filtered_st)} row(s) kept  (expected 1, st_row_valid only)"
    )
    if not ok:
        all_ok = False

    # ── _best_stop_tighten_frac tests ─────────────────────────────────────────
    # Tie: smaller fraction wins (tighter stop preferred on equal expectancy)
    tie_st = {0.25: {"expected_r": 0.10}, 0.50: {"expected_r": 0.10}}
    best_frac = _best_stop_tighten_frac(tie_st)
    ok = best_frac == 0.25
    print(f"  {'OK  ' if ok else 'FAIL'} _best_stop_tighten_frac(tie) = {best_frac}  (expected 0.25)")
    if not ok:
        all_ok = False

    clear_st = {0.25: {"expected_r": 0.05}, 0.50: {"expected_r": 0.15}, 0.75: {"expected_r": 0.10}}
    best_frac = _best_stop_tighten_frac(clear_st)
    ok = best_frac == 0.50
    print(
        f"  {'OK  ' if ok else 'FAIL'} _best_stop_tighten_frac(clear 0.50) = {best_frac}  (expected 0.50)"
    )
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
    stop_tightened_rows: list[dict] | None = None,
    st_grid_results: dict | None = None,
    optimal_frac: float | None = None,
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

    # ── Stop-tighten calibration block (optional) ─────────────────────────────
    if stop_tightened_rows is not None and st_grid_results is not None and optimal_frac is not None:
        n_st = len(stop_tightened_rows)
        st_date_vals = sorted(r["trade_date"] for r in stop_tightened_rows if r.get("trade_date"))
        st_date_range = (
            f"{st_date_vals[0]} → {st_date_vals[-1]}"
            if len(st_date_vals) >= 2
            else (st_date_vals[0] if st_date_vals else "n/a")
        )
        print("=" * 66)
        print("  Adaptive Stop-Tighten Fraction Calibration")
        print(
            f"  STOP_TIGHTENED: {n_st} settled adaptive rows ({st_date_range})"
        )
        print(
            "  Sim model: Wins unchanged; Losses capped at -(frac × ib_mid_dist_r)."
        )
        print("=" * 66)
        print()
        print(f"  {'Frac':>6}  {'N':>5}  {'WR':>7}  {'Exp R':>9}")
        print(f"  {'-'*6}  {'-'*5}  {'-'*7}  {'-'*9}")
        for frac in STOP_TIGHTEN_FRAC_GRID:
            s = st_grid_results.get(frac, {})
            n = s.get("n", 0)
            wr = s.get("wr")
            e  = s.get("expected_r")
            marker = " ← optimal" if abs(frac - optimal_frac) < 1e-9 else ""
            wr_str = f"{wr*100:6.1f}%" if wr is not None else "    n/a"
            e_str  = f"{e:+.4f}R"      if e  is not None else "       n/a"
            print(f"  {frac:>6.2f}   {n:>5}  {wr_str}  {e_str}{marker}")
        print()
        print(
            f"  Recommendation: stop_tighten_frac = {optimal_frac:.2f}"
            f"  (was {DEFAULT_STOP_TIGHTEN_FRAC:.2f}; 1.0 = exact IB mid)"
        )
        print()


def _print_stop_tighten_only(
    stop_tightened_rows: list[dict],
    st_grid_results: dict,
    optimal_frac: float,
) -> None:
    """Print a standalone stop-tighten calibration report (when TP-raise was skipped)."""
    today = date.today().isoformat()
    n_st = len(stop_tightened_rows)
    st_date_vals = sorted(r["trade_date"] for r in stop_tightened_rows if r.get("trade_date"))
    st_date_range = (
        f"{st_date_vals[0]} → {st_date_vals[-1]}"
        if len(st_date_vals) >= 2
        else (st_date_vals[0] if st_date_vals else "n/a")
    )
    print()
    print("=" * 66)
    print("  Adaptive Stop-Tighten Fraction Calibration")
    print(f"  Run date      : {today}")
    print(f"  STOP_TIGHTENED: {n_st} settled adaptive rows ({st_date_range})")
    print(
        "  Sim model: Wins unchanged; Losses capped at -(frac × ib_mid_dist_r)."
    )
    print("=" * 66)
    print()
    print(f"  {'Frac':>6}  {'N':>5}  {'WR':>7}  {'Exp R':>9}")
    print(f"  {'-'*6}  {'-'*5}  {'-'*7}  {'-'*9}")
    for frac in STOP_TIGHTEN_FRAC_GRID:
        s = st_grid_results.get(frac, {})
        n = s.get("n", 0)
        wr = s.get("wr")
        e  = s.get("expected_r")
        marker = " ← optimal" if abs(frac - optimal_frac) < 1e-9 else ""
        wr_str = f"{wr*100:6.1f}%" if wr is not None else "    n/a"
        e_str  = f"{e:+.4f}R"      if e  is not None else "       n/a"
        print(f"  {frac:>6.2f}   {n:>5}  {wr_str}  {e_str}{marker}")
    print()
    print(
        f"  Recommendation: stop_tighten_frac = {optimal_frac:.2f}"
        f"  (was {DEFAULT_STOP_TIGHTEN_FRAC:.2f}; 1.0 = exact IB mid)"
    )
    print()


# ── JSON patching ──────────────────────────────────────────────────────────────

def _apply_to_config(
    optimal: float | None,
    tp_raised_rows: list[dict],
    fixed_rows: list[dict],
    optimal_frac: float | None = None,
    stop_tightened_rows: list[dict] | None = None,
) -> None:
    """Write calibrated values to adaptive_exits.json.

    optimal      — optimal tp_raise_mult, or None if TP calibration was skipped.
    optimal_frac — optimal stop_tighten_frac, or None if STOP calibration was skipped.
    At least one must be non-None.
    """
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

    # Patch TP-raise fields (only when TP calibration produced a result)
    if optimal is not None:
        old_mult = cfg.get("tp_raise_mult", DEFAULT_MULT)
        cfg["tp_raise_mult"] = optimal
        cfg["_tp_raise_mult_note"] = (
            f"Auto-calibrated from {n} settled TP_RAISED adaptive trades "
            f"({date_range}). {fixed_note}Updated: {today}."
        )

    # Patch stop-tighten fields (when calibration produced a result)
    if optimal_frac is not None:
        n_st = len(stop_tightened_rows) if stop_tightened_rows else 0
        st_date_vals = (
            sorted(r["trade_date"] for r in stop_tightened_rows if r.get("trade_date"))
            if stop_tightened_rows
            else []
        )
        st_date_range = (
            f"{st_date_vals[0]} → {st_date_vals[-1]}"
            if len(st_date_vals) >= 2
            else (st_date_vals[0] if st_date_vals else "n/a")
        )
        old_frac = cfg.get("stop_tighten_frac", DEFAULT_STOP_TIGHTEN_FRAC)
        cfg["stop_tighten_frac"] = optimal_frac
        cfg["_stop_tighten_frac_note"] = (
            f"Auto-calibrated from {n_st} settled STOP_TIGHTENED adaptive trades "
            f"({st_date_range}). 1.0=exact IB mid (previous default). Updated: {today}."
        )

    cfg["last_updated"] = today

    with open(ADAPTIVE_EXITS_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")

    if optimal is not None:
        print(f"  adaptive_exits.json updated: tp_raise_mult {old_mult:.2f} → {optimal:.2f}")
    if optimal_frac is not None:
        print(
            f"  adaptive_exits.json updated: stop_tighten_frac {old_frac:.2f} → {optimal_frac:.2f}"
        )


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Calibrate the adaptive TP-raise multiplier and stop-tighten fraction "
            "from settled paper trades."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the optimal values to adaptive_exits.json.",
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
            f"  No calibration performed — keeping current defaults "
            f"(tp_raise_mult={DEFAULT_MULT:.2f}, "
            f"stop_tighten_frac={DEFAULT_STOP_TIGHTEN_FRAC:.2f}).\n"
            f"  Re-run once ≥ {MIN_TRADES} adaptive trades have settled."
        )
        sys.exit(0)

    tp_raised_rows      = _filter_tp_raised(all_adaptive)
    stop_tightened_rows = _filter_stop_tightened(all_adaptive)
    n_tp_raised         = len(tp_raised_rows)
    n_stop_tightened    = len(stop_tightened_rows)

    print(
        f"  Found {n_all} settled adaptive row(s) total; "
        f"{n_tp_raised} classified as TP_RAISED, "
        f"{n_stop_tightened} classified as STOP_TIGHTENED."
    )

    if n_tp_raised == 0 and n_stop_tightened == 0:
        print(
            "\n  ⏸  No TP_RAISED or STOP_TIGHTENED rows could be classified "
            "(price/IB columns missing).\n"
            "  No calibration performed."
        )
        sys.exit(0)

    # ── TP-raise calibration (when TP_RAISED rows exist) ─────────────────────
    grid_results: dict    = {}
    optimal: float        = DEFAULT_MULT
    fixed_rows: list[dict] = []

    if n_tp_raised > 0:
        # Counterfactual baseline: same-day fixed-mode trades
        adaptive_dates = {r["trade_date"] for r in tp_raised_rows if r.get("trade_date")}
        print(
            f"  Fetching fixed-mode baseline trades for "
            f"{len(adaptive_dates)} overlapping date(s)..."
        )
        fixed_rows = _fetch_fixed_settled(adaptive_dates)
        print(f"  Fixed-mode baseline: {len(fixed_rows)} settled trade(s) on matching dates.")

        print(f"  Running TP-raise grid search over {GRID}...")
        grid_results = _grid_search(tp_raised_rows)
        optimal      = _best_mult(grid_results)
    else:
        print(
            "  ⏸  No TP_RAISED rows found (price columns missing or no TP-raise trades) — "
            "skipping TP-raise calibration."
        )

    # ── Stop-tighten calibration (when STOP_TIGHTENED rows exist) ────────────
    st_grid_results: dict | None = None
    optimal_frac: float | None   = None

    if n_stop_tightened > 0:
        print(
            f"  Running stop-tighten grid search over {STOP_TIGHTEN_FRAC_GRID} "
            f"({n_stop_tightened} STOP_TIGHTENED rows)..."
        )
        st_grid_results = _grid_search_stop_tighten(stop_tightened_rows)
        optimal_frac    = _best_stop_tighten_frac(st_grid_results)
    else:
        print(
            "  ⏸  No STOP_TIGHTENED rows found (or IB columns missing) — "
            "skipping stop-tighten calibration."
        )

    # Only print the TP-raise table when we actually ran that calibration.
    if n_tp_raised > 0:
        _print_report(
            tp_raised_rows, fixed_rows, grid_results, optimal,
            stop_tightened_rows=stop_tightened_rows if n_stop_tightened > 0 else None,
            st_grid_results=st_grid_results,
            optimal_frac=optimal_frac,
        )
    elif n_stop_tightened > 0:
        # TP calibration skipped — print only the stop-tighten section.
        _print_stop_tighten_only(stop_tightened_rows, st_grid_results, optimal_frac)

    if args.apply:
        _apply_to_config(
            optimal  if n_tp_raised      > 0 else None,
            tp_raised_rows,
            fixed_rows,
            optimal_frac=optimal_frac,
            stop_tightened_rows=stop_tightened_rows if optimal_frac is not None else None,
        )
    else:
        print(
            "  Dry-run mode — no changes written.\n"
            "  Re-run with --apply to update adaptive_exits.json."
        )


if __name__ == "__main__":
    main()
