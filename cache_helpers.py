"""Cached data-loader wrappers for EdgeIQ.

All ``@st.cache_data`` wrappers that were previously defined at the top of
``app.py`` live here so they can be re-used by future sub-modules without
importing the entire app.
"""
from __future__ import annotations

import streamlit as st

from backend import *  # noqa: F401,F403  — exposes supabase + all backend callables
from backend import (
    load_journal,
    load_accuracy_tracker,
    load_paper_trades,
    load_user_prefs,
    load_watchlist,
    load_watchlist_predictions,
    load_eod_notes,
    load_tcs_alert_structures,
    get_kalshi_performance_summary,
    load_tcs_alert_thresholds,
    load_ib_range_pct_threshold,
    load_squeeze_calib_min_trades,
    resolve_squeeze_calib_min_trades_effective,
    load_tcs_threshold_history,
    load_tcs_thresholds,
    load_high_conviction_log,
    load_brain_weights,
    get_backtest_pace_target,
    compute_adaptive_weights,
    get_mgmt_mode_ab_stats,
    load_sa_journal,
    load_ranking_accuracy,
    count_missing_close_price_in_range,
    load_ticker_rankings,
    load_cognitive_delta_today,
    load_cognitive_delta_analysis,
    compute_bot_vs_trader_stats,
    check_db_connection,
    get_breadth_regime_history,
    get_tcs_alert_config_last_saved,
    get_lunar_phase,
    get_recent_env_stats,
    load_backtest_sim_history,
    load_backtest_saved_dates,
    load_backtest_rows_for_dates,
    get_intraday_closed_paper_trades,
    check_credential_match_sync,
    compute_structure_tcs_thresholds,
    compute_r_trend_history,
    get_ladder_pnl_summary,
)

# ── Domain constants (shared with app.py) ─────────────────────────────────────

RESOLVED_OUTCOMES = [
    "Bullish Break", "Bearish Break", "Range-Bound", "Both Sides",
    "Neutral", "Ntrl Extreme", "Normal Var", "Nrml Var",
]

SCAN_TYPES = ["morning", "intraday"]

BREAK_OUTCOMES = ("Bullish Break", "Bearish Break")

# ── Cached DB-loader wrappers (ttl=300 s ≈ 5 min) ────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_journal(user_id: str = ""):
    return load_journal(user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_accuracy_tracker(user_id: str = ""):
    return load_accuracy_tracker(user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False, max_entries=10)
def _cached_load_paper_trades(user_id: str = "", days: int = 365, date_from: str = "", date_to: str = ""):
    return load_paper_trades(user_id=user_id, days=days, date_from=date_from, date_to=date_to)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_user_prefs(user_id: str = ""):
    return load_user_prefs(user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_watchlist(user_id: str = ""):
    return load_watchlist(user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False, max_entries=30)
def _cached_load_watchlist_predictions(user_id: str = "", pred_date=None):
    return load_watchlist_predictions(user_id=user_id, pred_date=pred_date)

@st.cache_data(ttl=300, show_spinner=False, max_entries=10)
def _cached_load_eod_notes(user_id: str = "", limit: int = 60):
    return load_eod_notes(user_id=user_id, limit=limit)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_tcs_alert_structures():
    return load_tcs_alert_structures()

@st.cache_data(ttl=300, show_spinner=False)
def _cached_get_kalshi_performance_summary(user_id: str = "") -> dict:
    return get_kalshi_performance_summary(user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_tcs_alert_thresholds():
    return load_tcs_alert_thresholds()

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_ib_range_pct_threshold():
    return load_ib_range_pct_threshold()

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_squeeze_calib_min_trades():
    return load_squeeze_calib_min_trades()

@st.cache_data(ttl=300, show_spinner=False)
def _cached_resolve_squeeze_calib_effective():
    return resolve_squeeze_calib_min_trades_effective()

@st.cache_data(ttl=300, show_spinner=False, max_entries=10)
def _cached_load_tcs_threshold_history(days: int = 14):
    return load_tcs_threshold_history(days=days)

@st.cache_data(ttl=300, show_spinner=False, max_entries=5)
def _cached_load_tcs_thresholds(default: int = 50):
    return load_tcs_thresholds(default=default)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_high_conviction_log():
    return load_high_conviction_log()

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_brain_weights(user_id: str = ""):
    return load_brain_weights(user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False, max_entries=128)
def _cached_get_backtest_pace_target(
    user_id: str = "",
    ticker: str = "",
    start_date: str = "",
    end_date: str = "",
) -> dict:
    return get_backtest_pace_target(
        user_id=user_id,
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
    )

@st.cache_data(ttl=300, show_spinner=False)
def _cached_compute_adaptive_weights(user_id: str = ""):
    return compute_adaptive_weights(user_id)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_get_mgmt_mode_ab_stats(
    user_id: str = "",
    date_from: str | None = None,
    date_to: str | None = None,
    min_trades: int = 10,
) -> dict:
    return get_mgmt_mode_ab_stats(user_id=user_id, date_from=date_from, date_to=date_to, min_trades=min_trades)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_sa_journal():
    return load_sa_journal()

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_ranking_accuracy(user_id: str = ""):
    return load_ranking_accuracy(user_id=user_id)

@st.cache_data(ttl=60, show_spinner=False, max_entries=50)
def _cached_count_missing_close_price_in_range(
    start_date: str | None,
    end_date: str | None,
    user_id: str,
    table: str | None,
) -> int:
    return count_missing_close_price_in_range(
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
        table=table,
    )

@st.cache_data(ttl=300, show_spinner=False, max_entries=30)
def _cached_load_ticker_rankings(user_id: str = "", rating_date=None):
    return load_ticker_rankings(user_id=user_id, rating_date=rating_date)

@st.cache_data(ttl=300, show_spinner=False, max_entries=30)
def _cached_load_cognitive_delta_today(user_id: str = "", trade_date=None):
    return load_cognitive_delta_today(user_id=user_id, trade_date=trade_date)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_cognitive_delta_analysis(user_id: str = ""):
    return load_cognitive_delta_analysis(user_id=user_id)

@st.cache_data(ttl=600, show_spinner=False)
def _cached_compute_bot_vs_trader_stats(user_id: str = "", days: int = 90):
    return compute_bot_vs_trader_stats(user_id=user_id, days=days)

@st.cache_data(ttl=30, show_spinner=False)
def _cached_check_db_connection() -> tuple[bool, str]:
    # Equivalent to the db_reachable field in /api/health: both perform a HEAD
    # request to {SUPABASE_URL}/rest/v1/ and treat HTTP 200/404 as reachable.
    # Using a direct call here (rather than hitting /api/health over HTTP) avoids
    # a loopback round-trip while keeping the same reachability semantics.
    return check_db_connection()

@st.cache_data(ttl=300, show_spinner=False)
def _cached_get_breadth_regime_history(days: int = 30, user_id: str = "") -> list:
    return get_breadth_regime_history(days=days, user_id=user_id)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_get_tcs_alert_config_last_saved() -> str | None:
    return get_tcs_alert_config_last_saved()

@st.cache_data(ttl=3600, show_spinner=False)
def _cached_get_lunar_phase(today: "date" = None) -> dict:
    return get_lunar_phase(today)

@st.cache_data(ttl=300, show_spinner=False)
def _cached_get_recent_env_stats(user_id: str = "", days: int = 5) -> dict:
    return get_recent_env_stats(user_id=user_id, days=days)

@st.cache_data(ttl=300, show_spinner=False)
def _load_ls_bt_sim_history(uid):
    return load_backtest_sim_history(user_id=uid)

@st.cache_data(ttl=300, show_spinner=False)
def _load_bt_saved_dates(uid):
    return load_backtest_saved_dates(user_id=uid)

@st.cache_data(ttl=60, show_spinner=False)
def _load_bt_rows_for_dates(uid, dates_key: str):
    dates = dates_key.split("|")
    return load_backtest_rows_for_dates(user_id=uid, dates=dates)

@st.cache_data(ttl=300, show_spinner=False)
def _load_xref_bt_hist(uid):
    return load_backtest_sim_history(user_id=uid)

@st.cache_data(ttl=300, show_spinner=False)
def _load_bt_sim_history(uid):
    return load_backtest_sim_history(user_id=uid)

@st.cache_data(ttl=60, show_spinner=False)
def _load_intraday_closed(uid: str):
    return get_intraday_closed_paper_trades(user_id=uid)

@st.cache_data(ttl=300, show_spinner=False)
def _load_pace_target_alltime(uid):
    return get_backtest_pace_target(user_id=uid)

@st.cache_data(ttl=300, max_entries=50, show_spinner=False)
def _load_pace_target_scoped(uid, start_date: str, end_date: str):
    return get_backtest_pace_target(user_id=uid, start_date=start_date, end_date=end_date)

@st.cache_data(ttl=3600, show_spinner=False)
def _load_backtest_grid(uid, start_date=None, end_date=None):
    grid = []
    for st_name in SCAN_TYPES:
        for lo, hi in [(0, 30), (30, 40), (40, 50), (50, 60), (60, 70), (70, 80), (80, 101)]:
            try:
                rows, offset = [], 0
                while True:
                    q = (
                        supabase.table("backtest_sim_runs")
                        .select("actual_outcome,pnl_r_sim")
                        .eq("user_id", uid)
                        .eq("scan_type", st_name)
                        .gte("tcs", lo)
                        .lt("tcs", min(hi, 100) if hi < 101 else 200)
                        .in_("actual_outcome", RESOLVED_OUTCOMES)
                    )
                    if start_date:
                        q = q.gte("sim_date", str(start_date))
                    if end_date:
                        q = q.lte("sim_date", str(end_date))
                    batch = (
                        q.range(offset, offset + 999)
                        .execute()
                        .data or []
                    )
                    if not batch:
                        break
                    rows += batch
                    if len(batch) < 1000:
                        break
                    offset += 1000
                if not rows:
                    continue
                breaks = [
                    r for r in rows
                    if r.get("actual_outcome") in BREAK_OUTCOMES
                    and r.get("pnl_r_sim") is not None
                ]
                p_break = len(breaks) / len(rows) if rows else 0
                avg_r   = sum(float(r["pnl_r_sim"]) for r in breaks) / len(breaks) if breaks else 0
                true_e  = p_break * avg_r
                label   = f"{lo}–{min(hi-1,99)}+" if hi > 99 else f"{lo}–{hi-1}"
                grid.append({
                    "scan_type":   st_name,
                    "tcs_label":   label,
                    "tcs_lo":      lo,
                    "tcs_hi":      hi,
                    "setups":      len(rows),
                    "breaks":      len(breaks),
                    "p_break_pct": round(p_break * 100, 1),
                    "avg_r":       round(avg_r, 3),
                    "true_exp":    round(true_e, 3),
                })
            except Exception:
                continue
    return grid

@st.cache_data(ttl=300, show_spinner=False)
def _cached_cred_check(_key: str, _secret: str, _is_paper: bool) -> dict:
    return check_credential_match_sync(_key, _secret, _is_paper)

@st.cache_data(ttl=3600, show_spinner=False)
def _cached_tcs_thresholds():
    try:
        return compute_structure_tcs_thresholds()
    except Exception:
        return []

@st.cache_data(ttl=300, show_spinner=False)
def _load_r_trend(uid, src):
    return compute_r_trend_history(user_id=uid, r_source=src)

@st.cache_data(ttl=300, show_spinner=False)
def _load_ladder_pnl_cache(uid):
    return get_ladder_pnl_summary(user_id=uid)

@st.cache_data(ttl=3600, show_spinner=False)
def _load_screener_pass_grid(uid, start_date=None, end_date=None, by_year=False):
    """Single-pass fetch of backtest_sim_runs; groups by screener_pass in memory."""
    from datetime import date as _date_cls
    all_rows, offset, fetch_error = [], 0, None
    try:
        while True:
            q = (
                supabase.table("backtest_sim_runs")
                .select("actual_outcome,pnl_r_sim,sim_date,screener_pass")
                .eq("user_id", uid)
                .in_("actual_outcome", RESOLVED_OUTCOMES)
            )
            if start_date:
                q = q.gte("sim_date", str(start_date))
            if end_date:
                q = q.lte("sim_date", str(end_date))
            batch = q.range(offset, offset + 999).execute().data or []
            if not batch:
                break
            all_rows += batch
            if len(batch) < 1000:
                break
            offset += 1000
    except Exception as exc:
        fetch_error = str(exc)

    if fetch_error:
        return [{"_error": fetch_error}]
    if not all_rows:
        return []

    # Estimate years from actual date span in the fetched rows
    dates = []
    for r in all_rows:
        raw = r.get("sim_date")
        if raw:
            try:
                dates.append(_date_cls.fromisoformat(str(raw)[:10]))
            except Exception:
                pass
    if len(dates) >= 2:
        _years = max((max(dates) - min(dates)).days / 365.25, 0.1)
    else:
        _years = 5.0

    def _stats(subset):
        if not subset:
            return None
        breaks = [
            r for r in subset
            if r.get("actual_outcome") in BREAK_OUTCOMES
            and r.get("pnl_r_sim") is not None
        ]
        p_break = len(breaks) / len(subset)
        avg_r   = (
            sum(float(r["pnl_r_sim"]) for r in breaks) / len(breaks)
            if breaks else 0.0
        )
        return {
            "trades":        len(subset),
            "trades_per_yr": round(len(subset) / _years, 1),
            "wr_pct":        round(p_break * 100, 1),
            "avg_r":         round(avg_r, 3),
            "true_exp":      round(p_break * avg_r, 3),
        }

    results = []
    by_pass: dict = {}
    for r in all_rows:
        sp = (r.get("screener_pass") or "other").strip().lower()
        by_pass.setdefault(sp, []).append(r)

    for spass in ["gap", "trend", "gap_down"]:
        s = _stats(by_pass.get(spass, []))
        if s:
            results.append({"screener_pass": spass, **s})
    all_stats = _stats(all_rows)
    if all_stats:
        results.append({"screener_pass": "all", **all_stats})

    if not by_year:
        return results

    # ── Year-by-year breakdown (only when by_year=True) ─────────────────
    def _yr_stats(subset):
        """Like _stats but without trades_per_yr (scoped to one calendar year)."""
        if not subset:
            return None
        breaks = [
            r for r in subset
            if r.get("actual_outcome") in BREAK_OUTCOMES
            and r.get("pnl_r_sim") is not None
        ]
        p_break = len(breaks) / len(subset)
        avg_r = (
            sum(float(r["pnl_r_sim"]) for r in breaks) / len(breaks)
            if breaks else 0.0
        )
        return {
            "trades":   len(subset),
            "wr_pct":   round(p_break * 100, 1),
            "avg_r":    round(avg_r, 3),
            "true_exp": round(p_break * avg_r, 3),
        }

    # Bucket rows by (year, month) to determine full-year coverage
    by_year_months: dict = {}
    by_year_pass: dict = {}
    for r in all_rows:
        raw = r.get("sim_date")
        if not raw:
            continue
        try:
            parsed = _date_cls.fromisoformat(str(raw)[:10])
            yr, mo = parsed.year, parsed.month
        except Exception:
            continue
        by_year_months.setdefault(yr, set()).add(mo)
        sp = (r.get("screener_pass") or "other").strip().lower()
        by_year_pass.setdefault(yr, {}).setdefault("all", []).append(r)
        by_year_pass[yr].setdefault(sp, []).append(r)

    # A calendar year is "full" if data covers both H1 (Jan-Jun) and H2 (Jul-Dec)
    FIRST_HALF  = {1, 2, 3, 4, 5, 6}
    SECOND_HALF = {7, 8, 9, 10, 11, 12}
    full_year_count = sum(
        1 for yr, months in by_year_months.items()
        if months & FIRST_HALF and months & SECOND_HALF
    )
    partial_years = sorted(
        yr for yr, months in by_year_months.items()
        if not (months & FIRST_HALF and months & SECOND_HALF)
    )

    yearly_results = []
    for yr in sorted(by_year_pass.keys()):
        yr_data = by_year_pass[yr]
        row = {"year": yr}
        for spass in ["gap", "trend", "gap_down", "all"]:
            s = _yr_stats(yr_data.get(spass, []))
            if s:
                for k, v in s.items():
                    row[f"{spass}_{k}"] = v
            else:
                for k in ["trades", "wr_pct", "avg_r", "true_exp"]:
                    row[f"{spass}_{k}"] = None
        yearly_results.append(row)

    return {"aggregate": results, "by_year": yearly_results, "full_year_count": full_year_count, "partial_years": partial_years}


@st.cache_data(ttl=3600, show_spinner=False)
def _slp_fetch(_uid):
    _rows, _off = [], 0
    while True:
        _batch = (
            supabase.table("backtest_sim_runs")
            .select(
                "sim_date,predicted,tcs,scan_type,gap_pct,"
                "entry_price_sim,ib_high,ib_low,pnl_r_sim"
            )
            .eq("user_id", _uid)
            .not_.is_("entry_price_sim", "null")
            .not_.is_("pnl_r_sim", "null")
            .range(_off, _off + 999)
            .execute()
            .data or []
        )
        _rows.extend(_batch)
        if len(_batch) < 1000:
            break
        _off += 1000
    return _rows


@st.cache_data(ttl=3600, show_spinner=False)
def _ac_fetch(_uid):
    _rows, _off = [], 0
    while True:
        _batch = (
            supabase.table("backtest_sim_runs")
            .select(
                "sim_date,predicted,tcs,gap_pct,scan_type,"
                "entry_price_sim,ib_high,ib_low,pnl_r_sim"
            )
            .eq("user_id", _uid)
            .not_.is_("entry_price_sim", "null")
            .not_.is_("pnl_r_sim", "null")
            .range(_off, _off + 999)
            .execute()
            .data or []
        )
        _rows.extend(_batch)
        if len(_batch) < 1000:
            break
        _off += 1000
    return _rows


@st.cache_data(ttl=3600, show_spinner=False)
def _ex_fetch(_uid):
    _rows, _off = [], 0
    while True:
        _batch = (
            supabase.table("backtest_sim_runs")
            .select(
                "sim_date,predicted,tcs,scan_type,"
                "pnl_r_sim,eod_pnl_r,tiered_pnl_r"
            )
            .eq("user_id", _uid)
            .not_.is_("pnl_r_sim", "null")
            .range(_off, _off + 999)
            .execute()
            .data or []
        )
        _rows.extend(_batch)
        if len(_batch) < 1000:
            break
        _off += 1000
    return _rows


@st.cache_data(ttl=3600, show_spinner=False)
def _load_tier_screener_pass_data(uid, start_date=None, end_date=None):
    """Paginated fetch of backtest_sim_runs for the per-tier screener-pass table.

    Returns a list of row dicts with keys: screener_pass, tcs, scan_type, pnl_r_sim.
    Fetches the full history (not capped at 5,000 rows).
    Only resolved outcomes are included (RESOLVED_OUTCOMES), matching
    _load_screener_pass_grid for consistent sample sizes between the top-level
    card and the per-tier table.

    Screener passes covered:
      - "gap"      : ≥3% close-to-close daily gap (positive, directional)
      - "trend"    : ≥1% change + close above SMA20 & SMA50
      - "gap_down" : Bearish Break universe, bot-tagged at order placement;
                     actual_outcome = "Bearish Break" (in RESOLVED_OUTCOMES)
      - "other"    : everything else

    gap_down rows use the same scan_type ("morning" / "intraday") and TCS
    fields as gap/trend rows, so they map naturally to the P1–P4 tier grid.
    """
    all_rows, offset, fetch_error = [], 0, None
    try:
        while True:
            q = (
                supabase.table("backtest_sim_runs")
                .select("screener_pass,tcs,scan_type,pnl_r_sim,actual_outcome,sim_date")
                .eq("user_id", uid)
                .in_("actual_outcome", RESOLVED_OUTCOMES)
            )
            if start_date:
                q = q.gte("sim_date", str(start_date))
            if end_date:
                q = q.lte("sim_date", str(end_date))
            batch = q.range(offset, offset + 999).execute().data or []
            if not batch:
                break
            all_rows += batch
            if len(batch) < 1000:
                break
            offset += 1000
    except Exception as exc:
        fetch_error = str(exc)

    if fetch_error:
        return [{"_error": fetch_error}]
    return all_rows
