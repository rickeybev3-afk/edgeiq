"""
EdgeIQ Autonomous Paper Trader Bot
===================================
Runs independently all day without the browser open.

Schedule (ET):
   9:15 AM  — Auto-fetch watchlist from Finviz (your exact filter settings) → save to Supabase
  10:47 AM  — IB close + 17 min buffer → scan watchlist, filter TCS ≥ MIN_TCS, log entries + Telegram alerts
   2:00 PM  — Intraday key-level alert scan (re-scans for fresh setups mid-day)
   4:20 PM  — Market closes → update outcomes with full-day data (SIP 16-min delay)
   4:30 PM  — Nightly brain recalibration

Telegram Alerts (requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID secrets):
  • Morning scan: each qualifying setup → immediate alert with structure, IB range, key levels
  • Key-level alerts: price within X% of POC/VAH/VAL/target → actionable entry cue
  • EOD summary: win/loss count, biggest mover of the day
  • Brain recalibration: weight changes logged

Required environment secrets:
  ALPACA_API_KEY        — Alpaca API key
  ALPACA_SECRET_KEY     — Alpaca secret key
  TELEGRAM_BOT_TOKEN    — from @BotFather
  TELEGRAM_CHAT_ID      — your chat ID from @userinfobot

Optional env vars:
  PAPER_TRADE_USER_ID        — EdgeIQ user ID (defaults below)
  PAPER_TRADE_MIN_TCS        — minimum TCS threshold (default: 50)
  PAPER_TRADE_FEED           — sip or iex (default: sip)
  PAPER_TRADE_PRICE_MIN      — min price filter (default: 1.0)
  PAPER_TRADE_PRICE_MAX      — max price filter (default: 50.0)
  SWEEP_ALERT_MAX_TICKERS    — max tickers shown in the close-price sweep Telegram alert (default: 10)
  BACKTEST_CLOSE_LOOKBACK_DAYS — how many calendar days the nightly backtest close-price sweep
                                 covers (default: 60)
  PAPER_CLOSE_LOOKBACK_DAYS    — how many calendar days the nightly paper-trades close-price sweep
                                 covers (default: 60); tune independently of the backtest sweep
"""

import os
import time
import logging
from datetime import date, datetime

import pytz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("paper_trader_bot")


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


def _did_you_mean(unknown_key: str, valid_keys: list, display_plain: dict, max_dist: int = 2) -> str:
    best_key, best_dist = None, max_dist + 1
    for vk in valid_keys:
        d = _levenshtein(unknown_key, vk)
        if d < best_dist:
            best_dist, best_key = d, vk
    if best_key is not None and best_dist <= max_dist:
        friendly = display_plain.get(best_key, best_key)
        return f'Did you mean <code>{best_key}</code> ({friendly})?'
    return ""


EASTERN = pytz.timezone("America/New_York")

# ── Config from environment ───────────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
USER_ID           = os.getenv("PAPER_TRADE_USER_ID", "a5e1fcab-8369-42c4-8550-a8a19734510c")
_PAPER_MIN_TCS    = int(os.getenv("PAPER_TRADE_MIN_TCS", "60"))   # paper mode floor
_LIVE_MIN_TCS     = int(os.getenv("LIVE_MIN_TCS",        "70"))   # live real-money floor
FEED              = os.getenv("PAPER_TRADE_FEED", "sip")
PRICE_MIN               = float(os.getenv("PAPER_TRADE_PRICE_MIN", "1.0"))
PRICE_MAX               = float(os.getenv("PAPER_TRADE_PRICE_MAX", "50.0"))
SWEEP_ALERT_MAX_TICKERS      = int(os.getenv("SWEEP_ALERT_MAX_TICKERS", "10"))
BACKTEST_CLOSE_LOOKBACK_DAYS    = int(os.getenv("BACKTEST_CLOSE_LOOKBACK_DAYS", "60"))
BACKTEST_STALE_THRESHOLD_DAYS   = int(os.getenv("BACKTEST_STALE_THRESHOLD_DAYS", "3"))
PAPER_CLOSE_LOOKBACK_DAYS       = int(os.getenv("PAPER_CLOSE_LOOKBACK_DAYS", "60"))

_USER_PREFS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".local", "user_prefs.json")
_OWNER_USER_ID        = os.getenv("OWNER_USER_ID", "").strip() or "anonymous"

# Known NYSE market holidays (observed dates) for 2024-2028.
# When an official holiday falls on a Saturday the exchange is closed the
# preceding Friday; when it falls on a Sunday it is observed the following
# Monday.  This list is sourced from NYSE's published holiday schedule and
# serves as a fallback when the live Alpaca calendar fetch fails.
_NYSE_MARKET_HOLIDAYS: frozenset = frozenset({
    # 2024
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29",
    "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02",
    "2024-11-28", "2024-12-25",
    # 2025
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17",
    "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
    "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
    "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
    # 2028
    "2028-01-03", "2028-01-17", "2028-02-21", "2028-04-14",
    "2028-05-29", "2028-06-19", "2028-07-04", "2028-09-04",
    "2028-11-23", "2028-12-25",
})

# Process-level cache for Alpaca /v2/calendar results.
#
# Successful fetches (frozenset) are stored permanently for the process
# lifetime — a year's trading days never change once published.
#
# Failed fetches are stored as a negative-cache entry (None) with an
# expiry timestamp so transient API outages self-heal: the entry is
# evicted after _CALENDAR_RETRY_SECS seconds and the next call retries.
#
# Layout: {year: frozenset} for hits, {year: (None, expire_at)} for misses.
_alpaca_calendar_cache: dict = {}
_CALENDAR_RETRY_SECS   = 15 * 60   # retry failed fetches after 15 minutes


def _get_alpaca_trading_days_for_year(year: int):
    """Return a frozenset of ISO date strings on which NYSE is open for *year*.

    Fetches Alpaca's /v2/calendar endpoint using the correct paper/live base URL
    per IS_PAPER_ALPACA.  Successful results are cached permanently for the
    process lifetime.  Failed results are cached for _CALENDAR_RETRY_SECS so
    transient outages self-heal without a restart.  Returns None on failure so
    the caller can fall back to the hardcoded list.
    """
    cached = _alpaca_calendar_cache.get(year)
    if cached is not None and not isinstance(cached, tuple):
        return cached                            # valid frozenset from prior fetch
    if isinstance(cached, tuple):
        _none, expire_at = cached
        if time.monotonic() < expire_at:
            return None                          # still within retry back-off window
        # Back-off expired — evict stale failure and try again
        del _alpaca_calendar_cache[year]

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        _alpaca_calendar_cache[year] = (None, time.monotonic() + _CALENDAR_RETRY_SECS)
        return None

    try:
        import requests as _req
        base = "https://paper-api.alpaca.markets" if IS_PAPER_ALPACA else "https://api.alpaca.markets"
        r = _req.get(
            f"{base}/v2/calendar",
            params={"start": f"{year}-01-01", "end": f"{year}-12-31"},
            headers={
                "APCA-API-KEY-ID":     ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            },
            timeout=10,
        )
        if r.status_code == 200:
            trading_days = frozenset(c["date"] for c in r.json())
            _alpaca_calendar_cache[year] = trading_days
            return trading_days
        log.warning(
            "NYSE calendar live fetch returned HTTP %d for %d — falling back to hardcoded list",
            r.status_code, year,
        )
    except Exception as _exc:
        log.warning("NYSE calendar live fetch failed for %d: %s — falling back to hardcoded list", year, _exc)

    _alpaca_calendar_cache[year] = (None, time.monotonic() + _CALENDAR_RETRY_SECS)
    return None


def _is_nyse_trading_day(d) -> bool:
    """Return True if *d* is a NYSE equity market trading day.

    Checks weekends first, then queries Alpaca's /v2/calendar endpoint for a
    live, authoritative answer so the check stays correct without manual code
    updates (handles ad-hoc closures and years beyond 2028).  Falls back to the
    hardcoded _NYSE_MARKET_HOLIDAYS set when credentials are absent or the
    request fails so the guard never silently disappears.
    """
    if hasattr(d, "date"):
        d = d.date()
    if d.weekday() >= 5:
        return False

    iso = d.isoformat()

    trading_days = _get_alpaca_trading_days_for_year(d.year)
    if trading_days is not None:
        return iso in trading_days

    return iso not in _NYSE_MARKET_HOLIDAYS


def _get_recalc_zero_alerts_enabled() -> bool:
    """Return the recalc_zero_alerts_enabled preference (default True).

    Reads from .local/user_prefs.json using the same pattern as other alert
    preferences so deploy_server.py can toggle it from the Settings page.
    """
    try:
        import json as _json
        if os.path.exists(_USER_PREFS_FILE_PATH):
            with open(_USER_PREFS_FILE_PATH) as _f:
                _all = _json.load(_f)
            _owner_prefs = _all.get(_OWNER_USER_ID, {})
            return _owner_prefs.get("recalc_zero_alerts_enabled", True) is not False
    except Exception:
        pass
    return True


def _get_effective_paper_lookback_days() -> int:
    """Return the effective paper-trades close-price look-back window.

    Checks the dashboard prefs store (.local/user_prefs.json, written by
    deploy_server.py) for a 'paper_close_lookback_days' override first.
    Falls back to PAPER_CLOSE_LOOKBACK_DAYS (the env-var constant) when
    no override is present or the file cannot be read.
    """
    try:
        import json as _json
        if os.path.exists(_USER_PREFS_FILE_PATH):
            with open(_USER_PREFS_FILE_PATH) as _f:
                all_prefs = _json.load(_f)
            owner_prefs = all_prefs.get(_OWNER_USER_ID, {})
            if "paper_close_lookback_days" in owner_prefs:
                return int(owner_prefs["paper_close_lookback_days"])
    except Exception:
        pass
    return PAPER_CLOSE_LOOKBACK_DAYS


# ── Alpaca live execution config ───────────────────────────────────────────────
# Set LIVE_ORDERS_ENABLED=true in env to actually place orders on Alpaca.
# IS_PAPER_ALPACA=true  → paper-api.alpaca.markets  (safe, simulated fills)
# IS_PAPER_ALPACA=false → api.alpaca.markets        (real money — flip when ready)
LIVE_ORDERS_ENABLED     = os.getenv("LIVE_ORDERS_ENABLED", "false").lower() == "true"
# Never place real Alpaca orders from the dev environment — only production
# (deploy_server.py injects EDGEIQ_PRODUCTION=1 when spawning bots in prod)
if os.getenv("EDGEIQ_PRODUCTION", "").strip() != "1":
    LIVE_ORDERS_ENABLED = False
IS_PAPER_ALPACA         = os.getenv("IS_PAPER_ALPACA",     "true").lower()  == "true"
MIN_TCS                 = _PAPER_MIN_TCS if IS_PAPER_ALPACA else _LIVE_MIN_TCS
RISK_PER_TRADE          = float(os.getenv("RISK_PER_TRADE", "500"))   # dollars risked per trade (= 1R)
# PDT guard: block new orders when day-trade count >= this limit (FINRA: 3 in rolling 5 days)
PDT_MAX_DAY_TRADES       = int(os.getenv("PDT_MAX_DAY_TRADES", "3"))
# Concurrent position cap: block new orders when open positions >= this limit
# Paper mode: high cap (20) so all qualifying signals are taken, not just top 2.
# Live mode: default 2 to protect capital. Override via MAX_CONCURRENT_POSITIONS env var.
_default_pos_cap = "20" if os.getenv("IS_PAPER_ALPACA", "true").lower() == "true" else "2"
MAX_CONCURRENT_POSITIONS = int(os.getenv("MAX_CONCURRENT_POSITIONS", _default_pos_cap))
# PDT equity floor: fire a Telegram warning when live account equity drops below this level
# Default $26k gives a ~$1k buffer above the $25k PDT threshold (5 losses of $500 each = $2,500 drawdown cushion)
PDT_EQUITY_FLOOR         = float(os.getenv("PDT_EQUITY_FLOOR", "26000"))
# Cooldown between repeated PDT floor warnings (seconds) — default 4 hours
PDT_FLOOR_WARN_COOLDOWN  = int(os.getenv("PDT_FLOOR_WARN_COOLDOWN", "14400"))
# Tracks the date a PDT-block Telegram alert was last sent — prevents N duplicate
# alerts when N setups all hit the same block in one scan session.
_PDT_BLOCK_ALERTED_DATE: "date | None" = None

TG_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Suppress Telegram in dev — only fire from the deployed production server.
# EDGEIQ_PRODUCTION=1 is injected by deploy_server.py when it spawns this bot,
# so it is ONLY set when running in the production deployment, never in dev.
_IS_PRODUCTION = os.getenv("EDGEIQ_PRODUCTION", "").strip() == "1"
if not _IS_PRODUCTION:
    TG_TOKEN   = ""
    TG_CHAT_ID = ""

_DEFAULT_TICKERS = (
    "SATL,UGRO,ANNA,VCX,CODX,ARTL,SWMR,FEED,RBNE,PAVS,LNKS,BIAF,ACXP,GOAI"
)

# ── Configurable alert types (drives /settings display + command dispatch) ────
# Add a new entry here to surface a new alert type in /settings and enable its
# on/off toggle — no other changes required.
# Each entry: key = /settings sub-command, pref_key = user_prefs field,
#             default = value when unset, label + description shown in /settings.
_ALERT_REGISTRY = [
    {
        "key":         "morning_alerts",
        "pref_key":    "morning_alerts_enabled",
        "default":     True,
        "label":       "Morning setup alerts",
        "description": "Sends a pre-market summary of qualifying setups before the open.",
    },
    {
        "key":         "eod_alerts",
        "pref_key":    "eod_alerts_enabled",
        "default":     True,
        "label":       "End-of-day result summaries",
        "description": "Sends a daily outcome recap after market close.",
    },
    {
        "key":            "tcs_alerts",
        "pref_key":       "tcs_alerts_enabled",
        "default":        True,
        "label":          "TCS threshold shift alerts",
        "description":    "Notifies you when a structure's TCS threshold crosses a significant level.",
        "show_structures": True,
    },
    {
        "key":         "credential_alerts",
        "pref_key":    "credential_alerts_enabled",
        "default":     True,
        "label":       "Credential failure alerts",
        "description": "Notifies you when an API credential (Alpaca, Supabase) fails or recovers.",
    },
]

# ── Import backend functions ──────────────────────────────────────────────────
try:
    from backend import (
        run_historical_backtest,
        log_paper_trades,
        update_paper_trade_outcomes,
        ensure_paper_trades_table,
        load_watchlist,
        save_watchlist,
        fetch_finviz_watchlist,
        fetch_premarket_gappers,
        recalibrate_from_supabase,
        recalibrate_from_history,
        verify_watchlist_predictions,
        verify_ticker_rankings,
        ensure_ticker_rankings_table,
        ensure_telegram_columns,
        save_telegram_trade,
        save_beta_chat_id,
        get_beta_chat_ids,
        get_breadth_regime,
        ensure_cognitive_delta_table,
        verify_cognitive_delta,
        place_alpaca_bracket_order,
        cancel_alpaca_day_orders,
        reconcile_alpaca_fills,
        get_alpaca_account_equity,
        fetch_alpaca_fills,
        match_fills_to_roundtrips,
        supabase as _supabase_client,
        load_tcs_thresholds,
        append_tcs_threshold_history,
        label_to_weight_key,
        WK_DISPLAY,
        load_ib_range_pct_threshold,
        send_divergence_alert,
        save_daily_scan_log,
        ensure_daily_scan_log_table,
    )
except ImportError as e:
    log.error(f"Cannot import backend: {e}")
    raise


# ── Per-structure TCS thresholds (calibrated nightly) ─────────────────────────
def _struct_tcs_floor(r: dict, tcs_thresholds: dict, regime_floor: int) -> int:
    """Return the effective TCS floor for a scan result.

    For INTRADAY scans: always returns max(MIN_TCS, regime_floor).
    5-yr data shows intraday TCS 50-59 is +1.265R / 74.8% WR across ALL
    structures — per-structure calibration adds no value here and would be
    overwritten nightly to 62-65, blocking profitable setups.

    For MORNING / EOD scans: uses calibrated per-structure threshold from
    tcs_thresholds.json (written nightly by the recalibration), then applies
    the macro regime adjustment on top.  Falls back to MIN_TCS if no match.
    The separate MORNING_TCS_FLOOR=60 hard gate in _place_order_for_setup
    acts as the primary morning filter independent of this function.
    """
    scan_type = str(r.get("scan_type") or "").lower()
    if scan_type == "intraday":
        # Bypass per-structure calibration for intraday — use global floor only
        return max(MIN_TCS, regime_floor)
    predicted = str(r.get("predicted") or "").strip()
    wk        = label_to_weight_key(predicted) if predicted else ""
    cal_tcs   = tcs_thresholds.get(wk, MIN_TCS) if wk else MIN_TCS
    # regime_floor already incorporates tcs_adj; combine with calibrated threshold
    # take the higher of the two so macro fear never lets a weak structure through
    return max(cal_tcs, regime_floor)


# ── Dynamic position sizing ────────────────────────────────────────────────────
def _compute_risk_dollars() -> float:
    """Return 2.1% of current Alpaca account equity, capped at $2,000.

    Falls back to RISK_PER_TRADE env var if account fetch fails.
    """
    equity = get_alpaca_account_equity(
        is_paper   = IS_PAPER_ALPACA,
        api_key    = ALPACA_API_KEY,
        secret_key = ALPACA_SECRET_KEY,
    )
    if equity and equity > 0:
        dynamic = equity * 0.021         # 2.1% of account
        risk    = min(dynamic, 2000.0)   # cap $2,000
        log.info(f"  Account equity: ${equity:,.0f} → 2.1% = ${dynamic:,.0f} → risk/trade: ${risk:,.0f}")
        return risk
    log.warning(f"  Could not fetch account equity — using fallback ${RISK_PER_TRADE:.0f}/trade")
    return RISK_PER_TRADE


# ── P1–P4 tier priority ordering ───────────────────────────────────────────────
# Expected R per tier (5-yr backtest, 33,773 rows, April 2026):
#   P3: Morning  TCS≥70   → +6.102R / 79.7% WR  (67/yr  — NEVER miss)
#   P1: Intraday TCS≥70   → +3.715R / 89.8% WR  (631/yr)
#   P2: Intraday TCS50-69 → +1.265R / 74.8% WR  (979/yr — take with 1.0× size)
#   P4: Morning  TCS60-69 → +0.366R / 36.9% WR  (203/yr — marginal but +EV)
#   BLOCKED: Morning TCS<60 → negative expectancy (do not trade)
_TIER_EXPECTED_R = {
    "P3": 6.102,
    "P1": 3.715,
    "P2": 1.265,
    "P4": 0.366,
}

# ── Morning-scan TCS floor ──────────────────────────────────────────────────────
# 5-yr data: morning TCS 60-69 → +0.366R / 36.9% WR on 203 trades/yr (+$111K/yr at $1500R).
# Morning TCS 50-59 → +0.386R / 42.8% WR but only 217/yr and collapses in weak regimes.
# Morning TCS<60 is net negative expectancy — hard block.
# Intraday TCS 50-59 → +1.265R / 74.8% WR (979/yr, +$1.86M/yr) — allowed via MIN_TCS.
MORNING_TCS_FLOOR = int(os.environ.get("MORNING_TCS_FLOOR", "60"))

# ── Lunch blackout window ───────────────────────────────────────────────────────
# Intraday setups firing 11:30 AM–1:30 PM ET have structurally lower follow-through
# due to light volume, compressed spreads, and absent institutional participation.
# Configurable via env vars (24-h HH:MM format, ET). Defaults: 11:30–13:30.
LUNCH_BLACKOUT_START = os.environ.get("LUNCH_BLACKOUT_START", "11:30")
LUNCH_BLACKOUT_END   = os.environ.get("LUNCH_BLACKOUT_END",   "13:30")

def _in_lunch_blackout() -> bool:
    """Return True if the current ET time falls inside the configurable lunch blackout."""
    import datetime as _dt
    try:
        _now_et = _dt.datetime.now(EASTERN).strftime("%H:%M")
        return LUNCH_BLACKOUT_START <= _now_et < LUNCH_BLACKOUT_END
    except Exception:
        return False

# IB-range position-sizing multiplier table
# Source: 5-yr backtest, TCS≥50 + IB<10% universe (Apr 2026).
# Each bucket's half-Kelly fraction normalised to the 4-6% base tier,
# then blended with Expected-R ratio so the 0-2% tier (4.32R, 89.5% WR)
# gets genuinely large size while 6-10% buckets get modest reduction.
#   0–2%:  89.5% WR, +4.32R  → 2.00×  premium  (up to 2× base risk)
#   2–4%:  85.3% WR, +1.24R  → 1.30×  strong
#   4–6%:  83.6% WR, +0.99R  → 1.00×  standard (base tier)
#   6–8%:  82.9% WR, +0.77R  → 0.75×  reduced
#   8–10%: 82.6% WR, +0.88R  → 0.80×  reduced
_IB_RANGE_MULT = [
    (2.0,  2.00),   # IB pct < 2%
    (4.0,  1.30),   # 2% ≤ IB pct < 4%
    (6.0,  1.00),   # 4% ≤ IB pct < 6%
    (8.0,  0.75),   # 6% ≤ IB pct < 8%
    (10.0, 0.80),   # 8% ≤ IB pct < 10%
]

def _ib_size_mult(ib_pct: float) -> float:
    """Return position-size multiplier for a given IB range %."""
    for ceiling, mult in _IB_RANGE_MULT:
        if ib_pct < ceiling:
            return mult
    return 0.80  # ≥10% shouldn't pass IB filter, safe fallback


# ── P-tier position-sizing multiplier ─────────────────────────────────────────
# Calibrated from v5 trailing-stop sim (33,773 rows, April 2026).
# Applied AFTER the IB-range mult so the two stack multiplicatively.
# Net max exposure: 2.00× (IB) × 1.50× (P3) = 3.00× base risk (never exceeds
# the $2,000 risk cap enforced in _compute_risk_dollars).
#   P3: Morning  TCS≥70   → +4.607R / 81.9% WR → 1.50× (premium runners)
#   P1: Intraday TCS≥70   → +2.998R / 88.8% WR → 1.25× (high-frequency edge)
#   P2: Intraday TCS50-69 → +0.947R / 75.1% WR → 1.00× (baseline, acceptable)
#   P4: Morning  TCS50-69 → blocked by MORNING_TCS_FLOOR before sizing; 1.0× baseline
#                            if somehow reaches sizing after floor override.
_PTIER_MULT = [
    # (scan_type, tcs_min, multiplier)
    ("morning",  70, 1.50),
    ("morning",  50, 1.00),   # P4 — blocked by MORNING_TCS_FLOOR; baseline if floor overridden
    ("intraday", 70, 1.25),
    ("intraday", 50, 1.00),
]

def _ptier_size_mult(tcs: float, scan_type: str) -> float:
    """Return P-tier position-size multiplier for a given TCS and scan type."""
    st = (scan_type or "").lower()
    for s, tcs_min, mult in _PTIER_MULT:
        if s == st and tcs >= tcs_min:
            return mult
    return 1.00  # fallback — unknown tier, baseline sizing


# ── Screener-pass position-size multiplier ─────────────────────────────────
# Derived from 5-year backtest (33,776 simulated trades, 2021-2026):
#   'other'  (< 3% daily change, same screener pool): 87% WR / +0.622R avg → 1.15×
#   'gap'    (≥ 3% daily change):                      65% WR / +0.327R avg → 1.00×
#   'trend'  (1-3% + above SMA20/50):                  only 12 trades       → 0.85×
#   'squeeze' / unclassified:                           no backtest data     → 1.00×
# Applied AFTER IB-range, RVOL and P-tier mults as a final expectancy layer.
_SP_MULT_TABLE: dict[str, float] = {
    "other":   1.15,
    "gap":     1.00,
    "trend":   0.85,
    "squeeze": 1.00,
}

def _sp_size_mult(screener_pass: str | None) -> float:
    """Return position-size multiplier for a given screener_pass label.

    Derived from 5-year backtest (33,776 simulated trades across 2021-2026).
    'other' stocks (smaller-move days, tighter IB) consistently outperform
    'gap' stocks on every metric in every year — 87% vs 65% WR — because
    smaller gaps produce cleaner, less volatile initial balance structures.
    Returns 1.0 for unknown / unclassified passes (safe baseline).
    """
    return _SP_MULT_TABLE.get((screener_pass or "").lower().strip(), 1.00)


# ── RVOL bonus position-size multiplier ────────────────────────────────────
# High RVOL confirms institutional participation and momentum follow-through.
# Tiers sourced from adaptive_exits.json (rvol_size_tiers), sorted descending.
#   RVOL ≥ 3.5× → 1.50×  (exceptional momentum)
#   RVOL ≥ 2.5× → 1.25×  (strong momentum)
#   RVOL <  2.5 → 1.00×  (baseline — no bonus)
# Applied AFTER IB-range and P-tier mults; only when RVOL data is present.

# ── RVOL minimum entry floor ───────────────────────────────────────────────────
# v5 data: RVOL 0-1.0 → 28.2% WR / -0.513R (85 trades). Clear negative edge.
# All other RVOL bands ≥ 1.0 are profitable within P1/P3 universe.
# Hard block setups with RVOL < RVOL_MIN_FLOOR when RVOL data is available.
RVOL_MIN_FLOOR = float(os.environ.get("RVOL_MIN_FLOOR", "1.0"))

_ADAPTIVE_EXIT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "adaptive_exits.json")
_RVOL_SIZE_TIERS_DEFAULT = [
    {"rvol_min": 3.5, "multiplier": 1.5},
    {"rvol_min": 2.5, "multiplier": 1.25},
]

def _rvol_size_mult(rvol: float | None) -> float:
    """Return position-size multiplier based on RVOL bonus tiers.

    Tiers are loaded fresh from adaptive_exits.json on each call so that
    changes saved via the dashboard take effect without a bot restart.
    Tiers are evaluated highest rvol_min first; first match wins.
    Returns 1.0 (baseline) when rvol is None or below all thresholds.
    """
    if rvol is None or rvol != rvol:  # None or NaN
        return 1.0
    try:
        import json as _json
        with open(_ADAPTIVE_EXIT_CONFIG_PATH) as _f:
            cfg = _json.load(_f)
        tiers = cfg.get("rvol_size_tiers", _RVOL_SIZE_TIERS_DEFAULT)
    except Exception:
        tiers = _RVOL_SIZE_TIERS_DEFAULT
    for tier in sorted(tiers, key=lambda t: t["rvol_min"], reverse=True):
        if rvol >= tier["rvol_min"]:
            return float(tier["multiplier"])
    return 1.0

_ADAPTIVE_EXIT_CONFIG: dict = {}
try:
    import json as _json
    with open(os.path.join(os.path.dirname(__file__), "adaptive_exits.json")) as _aef:
        _ADAPTIVE_EXIT_CONFIG = _json.load(_aef)
except Exception:
    pass

_ADAPTIVE_FALLBACK_TARGET_R = 1.0

def _adaptive_target_r(tcs: float, scan_type: str = "", structure: str = "") -> float:
    """Return MFE-calibrated exit target in R-units.

    3-layer lookup (most specific wins):
      1. Structure override — Bearish Break (short setups, p50 MFE 0.47R) → 0.5R
                              Dbl Dist (strongest setup) → 1.5R
      2. Scan type + TCS band — morning/intraday × TCS tier
      3. TCS-only fallback

    Derived from 24,837 qualified backtest rows + 314 live paper_trades.
    Config in adaptive_exits.json. Updated 2026-04-18.
    """
    cfg = _ADAPTIVE_EXIT_CONFIG
    if not cfg:
        return _ADAPTIVE_FALLBACK_TARGET_R

    # Layer 1: structure override
    overrides = cfg.get("structure_overrides", {})
    for struct_key, target in overrides.items():
        if struct_key.lower() in structure.lower():
            return float(target)

    # Layer 2: scan_type + TCS band
    for band in cfg.get("scan_and_tcs", []):
        if band["scan_type"] == scan_type and band["tcs_min"] <= tcs < band["tcs_max"]:
            return float(band["target_r"])

    # Layer 3: TCS-only fallback
    for band in cfg.get("tcs_fallback", []):
        if band["tcs_min"] <= tcs < band["tcs_max"]:
            return float(band["target_r"])

    return float(cfg.get("global_fallback_target_r", _ADAPTIVE_FALLBACK_TARGET_R))


def _tier_priority_key(r: dict) -> tuple:
    """Return sort key (priority_int, -tcs) so highest-R tier comes first.
    P3 (Morning 70+) → P1 (Intraday 70+) → P2 (Intraday 50-69) → P4 (Morning 50-69).
    Within each tier, higher TCS sorts first.
    """
    tcs      = float(r.get("tcs", 0))
    scan     = r.get("scan_type", "morning")
    is_morn  = scan == "morning"
    is_intra = scan == "intraday"
    if is_morn  and tcs >= 70: return (0, -tcs)   # P3 — highest R
    if is_intra and tcs >= 70: return (1, -tcs)   # P1
    if is_intra and tcs >= 50: return (2, -tcs)   # P2
    return (3, -tcs)                               # P4 — or unclassified


# ── Context level logging (S/R, VWAP, MACD) ───────────────────────────────────
def _fetch_prev_day_bars(tickers: list, trade_date_str: str) -> dict:
    """Return dict[ticker] = {high, low} for the previous trading session."""
    from datetime import datetime, timedelta
    import requests as _req
    prev = (datetime.strptime(trade_date_str, '%Y-%m-%d') - timedelta(days=5)).strftime('%Y-%m-%d')
    end  = (datetime.strptime(trade_date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    result = {}
    for i in range(0, len(tickers), 10):
        batch = tickers[i:i+10]
        try:
            r = _req.get(
                'https://data.alpaca.markets/v2/stocks/bars',
                headers={'APCA-API-KEY-ID': ALPACA_API_KEY, 'APCA-API-SECRET-KEY': ALPACA_SECRET_KEY},
                params={'symbols': ','.join(batch), 'start': prev, 'end': end,
                        'timeframe': '1Day', 'feed': 'iex', 'limit': 1000},
                timeout=20,
            )
            r.raise_for_status()
            for sym, bars in (r.json().get('bars') or {}).items():
                if bars:
                    result[sym] = {'high': bars[-1]['h'], 'low': bars[-1]['l']}
        except Exception as e:
            log.warning(f'  context levels: prev-day bars error {e}')
        time.sleep(0.3)
    return result

def _fetch_intraday_5min(ticker: str, trade_date_str: str) -> list:
    """Return list of 5-min bar dicts from 9:30 AM–4:00 PM ET on trade_date."""
    import requests as _req
    import pytz
    from datetime import datetime
    ET = pytz.timezone('America/New_York')
    dt = datetime.strptime(trade_date_str, '%Y-%m-%d')
    start = ET.localize(dt.replace(hour=9, minute=30)).isoformat()
    end   = ET.localize(dt.replace(hour=16, minute=0)).isoformat()
    try:
        r = _req.get(
            f'https://data.alpaca.markets/v2/stocks/{ticker}/bars',
            headers={'APCA-API-KEY-ID': ALPACA_API_KEY, 'APCA-API-SECRET-KEY': ALPACA_SECRET_KEY},
            params={'start': start, 'end': end, 'timeframe': '5Min', 'feed': 'iex', 'limit': 1000},
            timeout=20,
        )
        if r.status_code == 422:
            return []
        r.raise_for_status()
        return r.json().get('bars') or []
    except Exception as e:
        log.warning(f'  context levels: intraday bars error {ticker}: {e}')
        return []

def _compute_vwap(bars: list) -> float | None:
    total_vol = sum(b['v'] for b in bars if b.get('v', 0) > 0)
    if not total_vol:
        return None
    return sum(b.get('vw', b['c']) * b['v'] for b in bars if b.get('v', 0) > 0) / total_vol

def _compute_macd(bars: list, fast=12, slow=26, sig=9):
    closes = [b['c'] for b in bars]
    if len(closes) < slow + sig:
        return None, None, None, None
    def ema(data, p):
        k, v = 2.0 / (p + 1), data[0]
        out = [v]
        for x in data[1:]: v = x * k + v * (1 - k); out.append(v)
        return out
    macd_vals = [f - s for f, s in zip(ema(closes, fast), ema(closes, slow))]
    sig_vals  = ema(macd_vals[slow - 1:], sig)
    ml, sl    = macd_vals[-1], sig_vals[-1]
    hist      = ml - sl
    direction = 'bullish' if hist > 0 else ('bearish' if hist < 0 else 'neutral')
    return ml, sl, hist, direction

def _bars_before_signal(bars: list, trade_date_str: str, scan_type: str) -> list:
    import pytz
    from datetime import datetime
    ET = pytz.timezone('America/New_York')
    sig = '09:35:00' if scan_type == 'morning' else '10:47:00'
    dt  = datetime.strptime(trade_date_str, '%Y-%m-%d')
    h, m, s = [int(x) for x in sig.split(':')]
    cutoff = ET.localize(dt.replace(hour=h, minute=m, second=s))
    result = []
    for b in bars:
        try:
            bar_dt = datetime.fromisoformat(b['t'].replace('Z', '+00:00'))
            if bar_dt <= cutoff:
                result.append(b)
        except Exception:
            continue
    return result

def log_context_levels(results: list, trade_date_str: str) -> None:
    """
    After a scan is logged, pull and store context levels (S/R, VWAP, MACD)
    for each ticker in results. Upserts to backtest_context_levels table.
    Runs in background — failures are logged but never raise.
    """
    if not results:
        return
    tickers = list({r.get('ticker') for r in results if r.get('ticker')})
    prev_day = _fetch_prev_day_bars(tickers, trade_date_str)
    rows_to_upsert = []
    for r in results:
        ticker    = r.get('ticker')
        scan_type = r.get('scan_type', 'morning')
        if not ticker:
            continue
        try:
            intraday = _fetch_intraday_5min(ticker, trade_date_str)
            time.sleep(0.25)
            bars_at  = _bars_before_signal(intraday, trade_date_str, scan_type)
            vwap     = _compute_vwap(bars_at)
            ml, sl, hist, direction = _compute_macd(bars_at)
            pd       = prev_day.get(ticker, {})
            ib_high  = float(r.get('ib_high') or 0) or None
            ib_low   = float(r.get('ib_low')  or 0) or None
            predicted = r.get('predicted', '')
            ib_break = ib_high if predicted == 'Bullish Break' else ib_low
            key_lvls = [l for l in [pd.get('high'), pd.get('low'), vwap] if l]
            above    = [l for l in key_lvls if ib_break and l > ib_break]
            below    = [l for l in key_lvls if ib_break and l <= ib_break]
            nearest_res = round(min(above), 4) if above else None
            nearest_sup = round(max(below), 4) if below else None
            # Write VWAP back to r so _place_order_for_setup can use it as an
            # entry quality filter (VWAP directional alignment).
            r['vwap_at_ib'] = round(vwap, 4) if vwap else None
            # Also store S/R levels on r so they are available to callers.
            r['nearest_resistance'] = nearest_res
            r['nearest_support']    = nearest_sup
            # Patch vwap_at_ib, nearest_resistance and nearest_support onto the
            # paper_trades row (inserted before this function runs, so we update
            # rather than insert).  All three are written in a single round-trip
            # so the trailing-stop monitor can read S/R levels directly from the
            # paper_trades row instead of doing a secondary backtest_context_levels
            # lookup that returns NULL for today's live trades (the nightly backfill
            # hasn't run yet).
            if _supabase_client:
                _pt_patch: dict = {}
                if r['vwap_at_ib'] is not None:
                    _pt_patch['vwap_at_ib'] = r['vwap_at_ib']
                if nearest_res is not None:
                    _pt_patch['nearest_resistance'] = nearest_res
                if nearest_sup is not None:
                    _pt_patch['nearest_support'] = nearest_sup
                if _pt_patch:
                    try:
                        _supabase_client.table('paper_trades').update(
                            _pt_patch
                        ).eq('user_id', USER_ID).eq('trade_date', trade_date_str).eq(
                            'ticker', ticker).eq('scan_type', scan_type).execute()
                        log.info(
                            f'  context levels: {ticker} patched paper_trades '
                            f'vwap={r["vwap_at_ib"]} res={nearest_res} sup={nearest_sup}'
                        )
                    except Exception as _pt_patch_err:
                        log.warning(f'  context levels: {ticker} paper_trades patch failed: {_pt_patch_err}')
            rows_to_upsert.append({
                'ticker':             ticker,
                'trade_date':         trade_date_str,
                'scan_type':          scan_type,
                'prev_day_high':      pd.get('high'),
                'prev_day_low':       pd.get('low'),
                'premarket_high':     None,
                'premarket_low':      None,
                'vwap_at_signal':     round(vwap, 4) if vwap else None,
                'macd_line':          round(ml, 6) if ml is not None else None,
                'macd_signal_line':   round(sl, 6) if sl is not None else None,
                'macd_histogram':     round(hist, 6) if hist is not None else None,
                'macd_direction':     direction,
                'nearest_resistance': nearest_res,
                'nearest_support':    nearest_sup,
            })
        except Exception as e:
            log.warning(f'  context levels: {ticker} error: {e}')
    if rows_to_upsert:
        try:
            _supabase_client.table('backtest_context_levels').upsert(rows_to_upsert).execute()
            log.info(f'  Context levels saved: {len(rows_to_upsert)} rows')
        except Exception as e:
            log.warning(f'  context levels upsert failed: {e}')


# ── Order placement ────────────────────────────────────────────────────────────
def _alpaca_get(endpoint: str) -> dict:
    """GET from Alpaca REST API, returns parsed JSON or {} on failure."""
    import requests as _req
    base    = "https://paper-api.alpaca.markets" if IS_PAPER_ALPACA else "https://api.alpaca.markets"
    headers = {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    try:
        r = _req.get(f"{base}{endpoint}", headers=headers, timeout=8)
        return r.json() if r.content else {}
    except Exception as _e:
        log.warning(f"[Alpaca GET {endpoint}] failed: {_e}")
        return {}


def _check_pdt_guard() -> tuple[bool, int, bool]:
    """Return (blocked, daytrade_count, pdt_in_effect).

    blocked        — True when daytrade_count >= PDT_MAX_DAY_TRADES (live + <$25k only).
    daytrade_count — rolling 5-day count from Alpaca (0 for paper accounts).
    pdt_in_effect  — True when account is live AND equity < $25k (PDT rules actively apply).
    Paper accounts are never blocked — PDT is a real-money brokerage rule.
    """
    if IS_PAPER_ALPACA:
        return False, 0, False
    acct = _alpaca_get("/v2/account")
    if not acct:
        log.warning("[PDT guard] Could not fetch account info — allowing order through")
        return False, 0, False
    equity      = float(acct.get("equity", 0) or 0)
    dt_count    = int(acct.get("daytrade_count", 0) or 0)
    pdt_flagged = acct.get("pattern_day_trader", False)
    if equity >= 25_000:
        return False, dt_count, False  # above PDT threshold — no restriction
    blocked = dt_count >= PDT_MAX_DAY_TRADES or pdt_flagged
    return blocked, dt_count, True


def _check_concurrent_positions_guard() -> tuple[bool, int]:
    """Return (blocked, open_position_count).

    Blocked = True when open positions >= MAX_CONCURRENT_POSITIONS.
    Counts only real open equity positions (not cash or closed).
    """
    positions = _alpaca_get("/v2/positions")
    if not isinstance(positions, list):
        log.warning("[Concurrent guard] Could not fetch positions — allowing order through")
        return False, 0
    count = len(positions)
    return count >= MAX_CONCURRENT_POSITIONS, count


def _warn_pdt_equity_floor(equity: float | None = None) -> None:
    """Fire a Telegram warning if live account equity is below PDT_EQUITY_FLOOR.

    Fetches equity from Alpaca if not supplied (saves an extra API call when
    the caller already has the account data).  Silently skips on paper accounts.
    Applies a cooldown (PDT_FLOOR_WARN_COOLDOWN seconds) to avoid spam.
    """
    if IS_PAPER_ALPACA:
        return

    if equity is None:
        acct   = _alpaca_get("/v2/account")
        equity = float(acct.get("equity", 0) or 0) if acct else 0.0

    if equity <= 0 or equity >= PDT_EQUITY_FLOOR:
        return   # no alert needed

    # Cooldown: only warn once per PDT_FLOOR_WARN_COOLDOWN seconds
    _flag     = "/tmp/.edgeiq_pdt_floor_warned"
    _now      = time.time()
    _last     = 0.0
    try:
        if os.path.exists(_flag):
            _last = float(open(_flag).read().strip())
    except Exception:
        pass
    if (_now - _last) < PDT_FLOOR_WARN_COOLDOWN:
        log.info(
            f"[PDT floor] Equity ${equity:,.2f} < floor ${PDT_EQUITY_FLOOR:,.0f} "
            f"— alert suppressed (cooldown {int(PDT_FLOOR_WARN_COOLDOWN / 3600):.0f}h)"
        )
        return
    try:
        with open(_flag, "w") as _f:
            _f.write(str(_now))
    except Exception:
        pass

    _gap       = PDT_EQUITY_FLOOR - equity
    _losses_to = int(_gap / RISK_PER_TRADE) + 1   # approx losses until PDT re-engages
    log.warning(f"[PDT floor] ⚠️  Equity ${equity:,.2f} — ${_gap:,.2f} below PDT floor (${PDT_EQUITY_FLOOR:,.0f})")
    tg_send(
        f"⚠️ <b>PDT Equity Floor Warning</b>\n"
        f"Account equity: <b>${equity:,.2f}</b>\n"
        f"PDT floor: <b>${PDT_EQUITY_FLOOR:,.0f}</b> "
        f"(${_gap:,.2f} below — ~{_losses_to} more loss{'es' if _losses_to != 1 else ''} "
        f"to PDT re-engagement at $25k)\n"
        f"<i>Consider pausing or reducing size until equity recovers above ${PDT_EQUITY_FLOOR:,.0f}.</i>"
    )


def _patch_skip_reason(r: dict, ticker: str, reason: str) -> None:
    """Write skip_reason onto the matching paper_trades row.

    Matches on (user_id, trade_date, ticker) — the same key the order-metadata
    patch uses.  Failures are silently swallowed so the main scan loop is never
    blocked by a DB write error.
    """
    if not _supabase_client:
        return
    try:
        _supabase_client.table("paper_trades").update(
            {"skip_reason": reason}
        ).eq("user_id", USER_ID).eq(
            "trade_date", str(r.get("sim_date") or r.get("trade_date") or "")
        ).eq("ticker", ticker.upper()).execute()
    except Exception as _sr_err:
        log.debug(f"  [{ticker}] skip_reason patch failed (non-fatal): {_sr_err}")


def _place_order_for_setup(r: dict, scan_label: str = "morning") -> None:
    """Place a bracket order on Alpaca for a qualified setup and log the order ID.

    Only runs when LIVE_ORDERS_ENABLED=true.  Skips non-directional predictions.
    Sizes at 1% of current account equity (capped $250–$2,000).
    Patches the paper_trades row with alpaca_order_id, alpaca_qty, order_placed_at,
    and skip_reason (always written so the funnel panel can count every row).
    """
    _ticker_raw = r.get("ticker", "unknown")

    if not LIVE_ORDERS_ENABLED:
        _patch_skip_reason(r, _ticker_raw, "orders_disabled")
        return

    direction = r.get("predicted", "")
    if direction not in ("Bullish Break", "Bearish Break"):
        log.info(f"  [{_ticker_raw}] skip order — prediction is '{direction}' (not directional)")
        _patch_skip_reason(r, _ticker_raw, "non_directional")
        return

    # ── Universe-alignment filter ─────────────────────────────────────────────
    # Our screener is a gap-UP universe (Finviz gap ≥ 3%, trend continuation).
    # Backtest of 111 settled trades shows:
    #   Bullish Break on gap-up stock → 71%+ WR (80.8% at TCS ≥ 50)
    #   Bearish Break on gap-up stock →  40% WR  (below random, do not trade)
    # Skip all Bearish Break signals until we build a dedicated gap-down universe.
    if direction == "Bearish Break":
        log.info(
            f"  [{_ticker_raw}] skip order — Bearish Break filtered: "
            f"gap-up universe hist WR 40% (vs 71% Bullish). Re-enable with gap-down screener."
        )
        _patch_skip_reason(r, _ticker_raw, "bearish_break_filtered")
        return

    # ── Morning TCS floor ─────────────────────────────────────────────────────
    # 5-yr data: Morning TCS<60 → net negative expectancy across all structures.
    # Morning TCS 60-69 → +0.366R / 36.9% WR (203/yr) — positive, take it.
    # Intraday TCS 50-59 handled by MIN_TCS + per-structure floor (not blocked here).
    _tcs_val     = float(r.get("tcs", 0))
    _scan_type_v = (r.get("scan_type") or scan_label or "").lower()
    if _scan_type_v == "morning" and _tcs_val < MORNING_TCS_FLOOR:
        log.info(
            f"  [{_ticker_raw}] skip order — morning TCS {_tcs_val:.0f} < floor {MORNING_TCS_FLOOR} "
            f"(hist: negative expectancy below TCS {MORNING_TCS_FLOOR})"
        )
        tg_send(
            f"⛔ <b>{_ticker_raw} Blocked — Morning TCS too low</b>\n"
            f"TCS <b>{_tcs_val:.0f}</b> < floor <b>{MORNING_TCS_FLOOR}</b>\n"
            f"Morning TCS &lt;{MORNING_TCS_FLOOR} hist: negative expectancy — skipping"
        )
        _patch_skip_reason(r, _ticker_raw, "morning_tcs_below_floor")
        return

    # ── Lunch blackout (intraday only) ─────────────────────────────────────────
    # Intraday setups firing during 11:30 AM–1:30 PM ET (default) are skipped.
    # Morning setups already fire at ~10:00 AM so are never in the window.
    if _scan_type_v == "intraday" and _in_lunch_blackout():
        log.info(
            f"  [{_ticker_raw}] skip order — inside lunch blackout "
            f"({LUNCH_BLACKOUT_START}–{LUNCH_BLACKOUT_END} ET)"
        )
        tg_send(
            f"⏸ <b>{_ticker_raw} Blocked — Lunch Blackout</b>\n"
            f"Intraday setup arrived during {LUNCH_BLACKOUT_START}–{LUNCH_BLACKOUT_END} ET.\n"
            f"Low-volume window — skipping to protect fill quality."
        )
        _patch_skip_reason(r, _ticker_raw, "lunch_blackout")
        return

    ticker = r.get("ticker", "").upper()

    # ── PDT guard (live accounts only, <$25k equity) ───────────────────────────
    _pdt_blocked, _dt_count, _pdt_in_effect = _check_pdt_guard()
    if _pdt_blocked:
        log.warning(
            f"  [{ticker}] ORDER BLOCKED — PDT limit reached "
            f"({_dt_count} day trades in rolling 5 days, max {PDT_MAX_DAY_TRADES})"
        )
        # Send Telegram only once per calendar day — prevents N duplicate alerts
        # when N setups all hit the same PDT block in one scan session.
        global _PDT_BLOCK_ALERTED_DATE
        _today_pdt = datetime.now(EASTERN).date()
        if _PDT_BLOCK_ALERTED_DATE != _today_pdt:
            _PDT_BLOCK_ALERTED_DATE = _today_pdt
            tg_send(
                f"🚫 <b>PDT Limit Reached — All Orders Blocked</b>\n"
                f"Day trades used: <b>{_dt_count}/{PDT_MAX_DAY_TRADES}</b> in rolling 5-day window\n"
                f"No new orders will be placed until a trade day rolls off.\n"
                f"<i>FINRA PDT rule: &lt;$25k accounts limited to 3 round-trips / 5 days.</i>"
            )
        else:
            log.info(f"  [{ticker}] PDT block alert suppressed — already sent today")
        _patch_skip_reason(r, ticker, "pdt_blocked")
        return

    # ── PDT equity floor warning (fires if equity near $25k boundary) ──────────
    _warn_pdt_equity_floor()

    # ── Concurrent position cap ────────────────────────────────────────────────
    _pos_blocked, _pos_count = _check_concurrent_positions_guard()
    if _pos_blocked:
        log.warning(
            f"  [{ticker}] ORDER BLOCKED — concurrent position cap reached "
            f"({_pos_count} open positions, max {MAX_CONCURRENT_POSITIONS})"
        )
        tg_send(
            f"🚫 <b>{ticker} Order Blocked — Max Positions Open</b>\n"
            f"Open positions: <b>{_pos_count}/{MAX_CONCURRENT_POSITIONS}</b>\n"
            f"Waiting for an existing position to close before adding new exposure."
        )
        _patch_skip_reason(r, ticker, "concurrent_cap")
        return

    ib_high = float(r.get("ib_high") or 0)
    ib_low  = float(r.get("ib_low")  or 0)
    if ib_high <= 0 or ib_low <= 0 or ib_high <= ib_low:
        log.warning(f"  [{ticker}] skip order — invalid IB ({ib_low}–{ib_high})")
        _patch_skip_reason(r, ticker, "invalid_ib")
        return

    risk_dollars = _compute_risk_dollars()
    _ib_mult     = 1.0          # default if open_px unavailable

    # ── Entry quality filter 1: IB range % ────────────────────────────────────
    # Wide IBs (>= 10% of open price) signal chaotic, non-directional days.
    # Historical: IB >= 10% → 54-68% WR; IB < 10% → 72-86% WR (at TCS >= 50).
    open_px = float(r.get("open_price") or 0)
    if open_px > 0:
        ib_range_pct_val = (ib_high - ib_low) / open_px * 100
        r["ib_range_pct"] = round(ib_range_pct_val, 4)
        _ib_threshold = load_ib_range_pct_threshold()
        if ib_range_pct_val >= _ib_threshold:
            log.info(
                f"  [{ticker}] skip order — IB range {ib_range_pct_val:.1f}% of price "
                f"(>= {_ib_threshold:.1f}% threshold, chaotic structure, hist WR 54-68%)"
            )
            tg_send(
                f"⛔ <b>{ticker} Order blocked — IB too wide</b>\n"
                f"IB range <b>{ib_range_pct_val:.1f}%</b> of price (≥ {_ib_threshold:.1f}% threshold)\n"
                f"Chaotic structure — hist WR 54-68% vs 72-86% when narrow"
            )
            _patch_skip_reason(r, ticker, "ib_too_wide")
            return

        # ── IB-range position-size multiplier ─────────────────────────────────
        # Tighter IB → higher Expected R and WR → scale up; wider → scale down.
        # 0-2%: 2.00×  |  2-4%: 1.30×  |  4-6%: 1.00×  |  6-8%: 0.75×  |  8-10%: 0.80×
        _ib_mult     = _ib_size_mult(ib_range_pct_val)
        risk_dollars = round(risk_dollars * _ib_mult, 2)
        log.info(
            f"  [{ticker}] IB range {ib_range_pct_val:.1f}% "
            f"→ size mult {_ib_mult:.2f}× → risk ${risk_dollars:,.0f}"
        )

    # ── Entry quality filter 3: RVOL minimum floor ────────────────────────────
    # v5 data: RVOL 0-1.0 → 28.2% WR / -0.513R (85 trades). Clear negative edge.
    # Only apply when rvol is available (may be None for new tickers or data gaps).
    _rvol_val  = r.get("rvol")
    _rvol_mult = None  # None = RVOL data unavailable; updated below when data is present
    if _rvol_val is not None:
        _rvol_float = float(_rvol_val)
        # Block rvol=0 too — 0 means data is present but volume is near-zero;
        # only None (no data at all) is the bypass case, guarded by outer if-not-None.
        if _rvol_float < RVOL_MIN_FLOOR:
            log.info(
                f"  [{ticker}] skip order — RVOL {_rvol_float:.2f} < floor {RVOL_MIN_FLOOR:.1f} "
                f"(hist WR 28.2% / -0.513R at RVOL 0-1.0)"
            )
            tg_send(
                f"⛔ <b>{ticker} Blocked — Low RVOL</b>\n"
                f"RVOL <b>{_rvol_float:.2f}×</b> < floor <b>{RVOL_MIN_FLOOR:.1f}×</b>\n"
                f"Low-participation setup — hist 28.2% WR / -0.513R at RVOL &lt;1.0"
            )
            _patch_skip_reason(r, ticker, "rvol_below_floor")
            return

        # ── RVOL bonus position-size multiplier ───────────────────────────────
        # RVOL ≥ 2.5× confirms strong momentum — scale up size to compound edge.
        # Tiers in adaptive_exits.json (rvol_size_tiers): 2.5×→1.25×, 3.5×→1.50×.
        # Applied only when RVOL data is present and setup already passed the floor.
        _rvol_mult = _rvol_size_mult(_rvol_float)
        if _rvol_mult != 1.0:
            risk_dollars = round(risk_dollars * _rvol_mult, 2)
            log.info(
                f"  [{ticker}] RVOL {_rvol_float:.2f}× → RVOL bonus mult "
                f"{_rvol_mult:.2f}× → risk ${risk_dollars:,.0f}"
            )

    # ── P-tier position-size multiplier ───────────────────────────────────────
    # Stack on top of IB-range mult: P3 (morning 70+) → 1.50×; P1 (intraday 70+)
    # → 1.25×; P2 (intraday 50-69) → 1.00×.  P4 morning is already blocked above.
    _ptier_mult  = _ptier_size_mult(_tcs_val, _scan_type_v)
    risk_dollars = round(risk_dollars * _ptier_mult, 2)
    if _ptier_mult != 1.0:
        log.info(
            f"  [{ticker}] P-tier mult {_ptier_mult:.2f}× "
            f"(TCS {_tcs_val:.0f}, {_scan_type_v}) → risk ${risk_dollars:,.0f}"
        )

    # ── Screener-pass position-size multiplier ────────────────────────────────
    # 5-yr backtest (33,776 trades, 2021-2026) shows consistent WR difference:
    #   other 87% WR / +0.622R  →  1.15×   gap 65% WR / +0.327R  →  1.00×
    #   trend only 12 trades    →  0.85×   squeeze no data        →  1.00×
    _sp_tag  = _TICKER_SCREENER_PASS.get(ticker) or _TICKER_SCREENER_PASS.get(ticker.upper())
    _sp_mult = _sp_size_mult(_sp_tag)
    risk_dollars = round(risk_dollars * _sp_mult, 2)
    if _sp_mult != 1.0:
        log.info(
            f"  [{ticker}] screener-pass mult {_sp_mult:.2f}× "
            f"({_sp_tag or 'unclassified'}) → risk ${risk_dollars:,.0f}"
        )

    # ── Entry quality filter 2: VWAP directional alignment ────────────────────
    # DISABLED 2026-04-18: backtest showed removing this filter nearly doubled
    # annual return (+20%+ weekly) — counter-VWAP setups that pass TCS≥50 +
    # IB<10% are profitable and were being over-filtered.  The simulation toggle
    # confirmed it.  Left here as a reference; re-enable by uncommenting.
    #
    # vwap_val  = float(r.get("vwap_at_ib") or 0)
    # close_val = float(r.get("close_price") or 0)
    # if vwap_val > 0 and close_val > 0:
    #     aligned = (
    #         (direction == "Bullish Break" and close_val >= vwap_val) or
    #         (direction == "Bearish Break" and close_val <= vwap_val)
    #     )
    #     if not aligned:
    #         _side = "<" if direction == "Bullish Break" else ">"
    #         log.info(
    #             f"  [{ticker}] skip order — VWAP misaligned: {direction} "
    #             f"but close {close_val:.2f} {_side} VWAP {vwap_val:.2f} "
    #             f"(hist WR 71.8% vs 97.6% when aligned)"
    #         )
    #         _side_word = "below" if direction == "Bullish Break" else "above"
    #         tg_send(
    #             f"⛔ <b>{ticker} Order blocked — VWAP misaligned</b>\n"
    #             f"{direction}: close <b>${close_val:.2f}</b> is {_side_word} VWAP <b>${vwap_val:.2f}</b>\n"
    #             f"Hist WR 71.8% misaligned vs 97.6% aligned — skipping"
    #         )
    #         _patch_skip_reason(r, ticker, "vwap_misaligned")
    #         return

    _tcs_for_exit  = float(r.get("tcs", 0))
    _scan_for_exit = r.get("scan_type") or scan_label or ""
    _struct_for_exit = direction or ""
    _target_r      = _adaptive_target_r(_tcs_for_exit, scan_type=_scan_for_exit, structure=_struct_for_exit)
    log.info(
        f"  [{ticker}] Adaptive exit target: {_target_r:.1f}R "
        f"(TCS {_tcs_for_exit:.0f}, scan={_scan_for_exit}, struct={_struct_for_exit})"
    )

    # ── Smart stop-widening: false shakeout protection ─────────────────────────
    # 5-year backtest (33k rows) shows high-TCS + tight-IB + high-RVOL setups
    # have 2× the false-shakeout rate vs clean stops (MAE>=1R then MFE avg 6R+).
    # Widening the stop below IB low survives the shakeout and captures the run.
    # Share sizing adjusts automatically (wider stop → fewer shares = same $ risk).
    _ib_range          = ib_high - ib_low
    _ib_rng_pct_stop   = float(r.get("ib_range_pct") or 999)
    _rvol_for_stop     = float(_rvol_val) if _rvol_val is not None else 0.0
    _effective_stop    = ib_low
    _stop_reason       = None

    if direction == "Bullish Break":
        if _tcs_for_exit >= 70 and _ib_rng_pct_stop <= 3.0 and _rvol_for_stop >= 3.0:
            # Tight IB coil + high TCS + strong RVOL — classic false-shakeout profile.
            # 5yr data: 55% of TCS70+ false shakeouts had IB<=3% and RVOL>=3.
            _effective_stop = round(ib_low - 0.5 * _ib_range, 4)
            _stop_reason = (f"TCS {_tcs_for_exit:.0f}≥70 + IB {_ib_rng_pct_stop:.1f}%≤3 "
                            f"+ RVOL {_rvol_for_stop:.1f}≥3.0 → -0.5R buffer")
        elif _tcs_for_exit >= 65 and _scan_for_exit == "intraday" and _rvol_for_stop >= 2.5:
            # High-TCS intraday + decent RVOL — moderate shakeout risk.
            _effective_stop = round(ib_low - 0.25 * _ib_range, 4)
            _stop_reason = (f"TCS {_tcs_for_exit:.0f}≥65 + intraday + "
                            f"RVOL {_rvol_for_stop:.1f}≥2.5 → -0.25R buffer")

    if _stop_reason:
        log.info(
            f"  [{ticker}] 🛡️ Smart stop: IB low ${ib_low:.2f} → ${_effective_stop:.2f} "
            f"({_stop_reason})"
        )

    result = place_alpaca_bracket_order(
        ticker       = ticker,
        ib_high      = ib_high,
        ib_low       = _effective_stop,
        direction    = direction,
        risk_dollars = risk_dollars,
        target_r     = _target_r,
        is_paper     = IS_PAPER_ALPACA,
        api_key      = ALPACA_API_KEY,
        secret_key   = ALPACA_SECRET_KEY,
    )

    acct_type = "PAPER" if IS_PAPER_ALPACA else "LIVE"
    if result.get("ok"):
        order_id = result["order_id"]
        qty      = result["qty"]
        log.info(
            f"  ✅ [{ticker}] {acct_type} bracket order placed | "
            f"qty={qty} | entry=${result['entry']} | stop=${result['stop']} | "
            f"target=${result['target']} | id={order_id}"
        )
        _rvol_bonus_label = (
            f" · {_rvol_mult:.2f}× RVOL⚡"
            if _rvol_mult is not None and _rvol_mult != 1.0
            else ""
        )
        _sp_label = (
            f" · {_sp_mult:.2f}× {_sp_tag or 'unk'}"
            if _sp_mult != 1.0
            else ""
        )
        tg_send(
            f"📋 <b>{acct_type} Order Placed — {ticker}</b>\n"
            f"{'🟡' if direction == 'Bullish Break' else '🔴'} {direction}\n"
            f"Entry: ${result['entry']} | Stop: ${result['stop']} | "
            f"Target: ${result['target']}\n"
            f"Qty: {qty} shares | Risk: ${risk_dollars:,.0f} "
            f"({_ib_mult:.2f}× IB · {_ptier_mult:.2f}× P-tier{_rvol_bonus_label}{_sp_label} · 1R base)\n"
            f"<code>{order_id[:8]}…</code>"
        )
        # Patch Supabase paper_trades row with order metadata + skip_reason
        if _supabase_client:
            try:
                _sp = _TICKER_SCREENER_PASS.get(ticker) or _TICKER_SCREENER_PASS.get(ticker.upper())
                _order_patch = {
                    "alpaca_order_id":  order_id,
                    "alpaca_qty":       qty,
                    "order_placed_at":  datetime.utcnow().isoformat(),
                    "skip_reason":      "order_placed",
                    "rvol_mult":        _rvol_mult,
                    "sp_mult":          _sp_mult,
                }
                if _sp:
                    _order_patch["screener_pass"] = _sp
                _supabase_client.table("paper_trades").update(_order_patch).eq("user_id", USER_ID).eq("trade_date", str(r.get("sim_date") or r.get("trade_date") or "")).eq("ticker", ticker).execute()
            except Exception as _patch_err:
                log.warning(f"  [{ticker}] Could not patch order_id to paper_trades: {_patch_err}")
        # ── Register zone invalidation watch ──────────────────────────────────
        # Use raw ib_low (not _effective_stop) as the invalidation level —
        # if price drops below the IB low the structure is broken regardless
        # of how wide the stop is widened for false-shakeout protection.
        _zw_date = str(r.get("sim_date") or r.get("trade_date") or datetime.now(EASTERN).date())
        _register_zone_watch(
            ticker      = ticker,
            trade_date  = _zw_date,
            order_id    = order_id,
            direction   = direction,
            invalidation_price = ib_low,   # IB low = structure invalidation level
            entry       = result["entry"],
            stop        = result["stop"],
            target      = result["target"],
            qty         = qty,
        )
    else:
        log.warning(f"  ❌ [{ticker}] Order failed: {result.get('error')}")
        tg_send(f"⚠️ <b>{acct_type} Order Failed — {ticker}</b>\n{result.get('error','unknown error')}")
        _patch_skip_reason(r, ticker, "order_failed")


# ── Trailing stop monitoring ──────────────────────────────────────────────────
# Guard set: (ticker, trade_date) pairs that already have trailing stop active.
# Prevents re-triggering on every 30-second loop iteration.
_TRAILING_STOP_ACTIVATED: set = set()

# ── Screener pass registry ─────────────────────────────────────────────────────
# Maps ticker → pass name ('gap' | 'trend' | 'squeeze').  Populated by
# watchlist_refresh() after each Finviz scan; queried by _place_order_for_setup()
# so the paper_trades row records which screener produced the signal.
_TICKER_SCREENER_PASS: dict[str, str] = {}
_SCREENER_PASS_CACHE_FILE = ".ticker_screener_pass.json"


def _save_screener_pass_cache():
    """Persist _TICKER_SCREENER_PASS to disk so it survives bot restarts."""
    try:
        import json as _json
        with open(_SCREENER_PASS_CACHE_FILE, "w") as _f:
            _json.dump(_TICKER_SCREENER_PASS, _f)
    except Exception as _e:
        log.warning(f"[screener_pass] cache save failed: {_e}")


def _load_screener_pass_cache():
    """Load _TICKER_SCREENER_PASS from disk on startup (survives restarts)."""
    global _TICKER_SCREENER_PASS
    try:
        import json as _json, os as _os
        if _os.path.exists(_SCREENER_PASS_CACHE_FILE):
            _loaded = _json.load(open(_SCREENER_PASS_CACHE_FILE))
            _TICKER_SCREENER_PASS.update(_loaded)
            log.info(f"[screener_pass] Loaded {len(_loaded)} cached ticker→pass entries from disk")
    except Exception as _e:
        log.warning(f"[screener_pass] cache load failed: {_e}")


# S/R context stored when a trailing stop is tightened due to a nearby wall.
# Maps guard_key → {"level_type": str, "level": float, "gap": float}
# Read by the stop-out detector to include wall detail in the close alert.
_TRAILING_STOP_SR_CONTEXT: dict = {}

# Guard set: (ticker, trade_date) pairs for which the stop-out Telegram alert
# has already been sent.  Prevents duplicate alerts on successive 30-s loops.
_TRAILING_STOP_STOPOUT_ALERTED: set = set()


def _restore_trailing_stop_guard() -> None:
    """Re-populate _TRAILING_STOP_ACTIVATED from DB on startup.

    Queries paper_trades for today's rows that already have trail_activated=TRUE
    AND win_loss IS NULL (i.e. the position is still open).  Excluding rows that
    have already been closed (win_loss patched) ensures that a same-day re-entry
    on the same ticker is not incorrectly blocked on restart — only genuinely
    active trailing stops are restored.

    Silently skips if the trail_activated column doesn't exist yet (migration not
    yet applied) so the bot continues to function without it.
    """
    if not _supabase_client:
        return
    today_str = datetime.now(EASTERN).strftime("%Y-%m-%d")
    try:
        rows = (
            _supabase_client
            .table("paper_trades")
            .select("ticker")
            .eq("user_id", USER_ID)
            .eq("trade_date", today_str)
            .eq("trail_activated", True)
            .is_("win_loss", "null")
            .execute()
        ).data or []
        for row in rows:
            ticker = (row.get("ticker") or "").upper()
            if ticker:
                _TRAILING_STOP_ACTIVATED.add((ticker, today_str))
        if rows:
            log.info(
                f"[TrailingStop] Restored {len(rows)} guard key(s) from DB on startup: "
                f"{[r.get('ticker') for r in rows]}"
            )
    except Exception as _e:
        _es = str(_e)
        if "trail_activated" in _es or "PGRST" in _es:
            log.info(
                "[TrailingStop] trail_activated column not in DB yet — "
                "run migration add_trail_tighten_context_paper_trades.sql. "
                "Guard set will be empty (no trailing-stop re-guard on restart)."
            )
        else:
            log.warning(f"[TrailingStop] Could not restore guard set from DB: {_e}")

# Session-open equity: date_str → equity float captured at ~9:35 AM ET each day.
# Used by _force_close_all_positions() to compute equity change vs session open.
_SESSION_OPEN_EQUITY: dict = {}

# ── Zone invalidation watch ───────────────────────────────────────────────────
# Maps (ticker, trade_date) → {order_id, invalidation_price, direction,
#                               entry, stop, target, qty}
# Populated by _register_zone_watch(); cleared when order is cancelled or fills.
import threading as _zone_threading
_ZONE_WATCH:      dict               = {}
_ZONE_WATCH_LOCK: _zone_threading.Lock = _zone_threading.Lock()
_ZONE_LAST_CHECK_TS: float           = 0.0
ZONE_CHECK_INTERVAL  = 300  # seconds between price checks (5 min)


def _alpaca_get_order_status(order_id: str) -> str:
    """Return the Alpaca order status string for a given order ID.

    Possible values: 'new', 'accepted', 'pending_new', 'partially_filled',
    'filled', 'canceled', 'expired', 'replaced', or '' on error.
    Only the PARENT entry order should be watched — once it is filled or
    cancelled/expired the child legs (TP/SL) are handled by the bracket.
    """
    data = _alpaca_get(f"/v2/orders/{order_id}")
    return data.get("status", "")


def _alpaca_get_5min_bar_close(tickers: list) -> dict:
    """Return the close price of the last COMPLETED 5-min bar for each ticker.

    Fetches a 20-minute look-back window ending 6 minutes ago so the most
    recent bar is guaranteed to be fully closed (no partial candle noise).
    Returns {TICKER: float_close}.  Silently skips tickers with no bar data.
    """
    import requests as _req
    from datetime import timedelta
    if not tickers or not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return {}
    headers = {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    symbols = ",".join(t.upper() for t in tickers)
    # end = 6 min ago guarantees the 5-min bar is fully closed
    now_utc = datetime.utcnow()
    end_dt  = now_utc - timedelta(minutes=6)
    start_dt = now_utc - timedelta(minutes=20)  # wide window to ensure we get at least 1 bar
    try:
        resp = _req.get(
            "https://data.alpaca.markets/v2/stocks/bars",
            headers=headers,
            params={
                "symbols":    symbols,
                "timeframe":  "5Min",
                "start":      start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end":        end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit":      3,
                "feed":       "sip",
                "adjustment": "raw",
            },
            timeout=8,
        )
        if resp.status_code != 200:
            log.warning(f"[ZoneWatch] Bar fetch HTTP {resp.status_code}")
            return {}
        bars_by_sym = resp.json().get("bars", {})
        result = {}
        for sym, bars in bars_by_sym.items():
            if bars:
                result[sym.upper()] = float(bars[-1]["c"])  # last completed bar close
        return result
    except Exception as _e:
        log.warning(f"[ZoneWatch] Bar fetch error: {_e}")
        return {}


def _register_zone_watch(
    ticker: str,
    trade_date: str,
    order_id: str,
    direction: str,
    invalidation_price: float,
    entry: float,
    stop: float,
    target: float,
    qty: int,
) -> None:
    """Register a newly placed bracket order for zone-invalidation monitoring."""
    with _ZONE_WATCH_LOCK:
        _ZONE_WATCH[(ticker.upper(), trade_date)] = {
            "order_id":          order_id,
            "invalidation_price": invalidation_price,
            "direction":         direction,
            "entry":             entry,
            "stop":              stop,
            "target":            target,
            "qty":               qty,
        }
    if direction == "Bearish Break":
        _watch_desc = f"cancel if price > ${invalidation_price:.2f} (IB high) before entry fills"
    else:
        _watch_desc = f"cancel if price < ${invalidation_price:.2f} (IB low) before entry fills"
    log.info(f"[ZoneWatch] 👀 Watching {ticker} — {_watch_desc}")


def _restore_zone_watch_from_db() -> None:
    """Re-populate _ZONE_WATCH from today's open paper_trades on startup.

    Queries rows with alpaca_order_id set and win_loss NULL, then verifies
    each order's status against Alpaca before registering — skipping any that
    are already filled/cancelled (i.e. the entry fired or was externally removed).
    Silently skips if LIVE_ORDERS_ENABLED is False (dev mode) or DB is unavailable.
    """
    if not LIVE_ORDERS_ENABLED or not _supabase_client:
        return
    today_str = str(datetime.now(EASTERN).date())
    try:
        rows = (
            _supabase_client
            .table("paper_trades")
            .select(
                "ticker,predicted,ib_low,ib_high,"
                "entry_price_sim,stop_price_sim,target_price_sim,"
                "alpaca_order_id,alpaca_qty"
            )
            .eq("user_id", USER_ID)
            .eq("trade_date", today_str)
            .not_.is_("alpaca_order_id", "null")
            .is_("win_loss", "null")
            .execute()
        ).data or []
        restored = skipped = 0
        # Pre-fill states that mean the entry order is still waiting (not yet filled)
        _OPEN_STATUSES = {"new", "accepted", "pending_new", "accepted_for_bidding"}
        for row in rows:
            ticker    = (row.get("ticker") or "").upper()
            direction = row.get("predicted") or ""
            ib_low    = float(row.get("ib_low") or 0)
            order_id  = row.get("alpaca_order_id") or ""
            if not ticker or not direction or not order_id or ib_low <= 0:
                continue
            if direction != "Bullish Break":
                continue
            # Verify the PARENT entry order is still truly open on Alpaca
            status = _alpaca_get_order_status(order_id)
            if status and status not in _OPEN_STATUSES:
                log.info(
                    f"[ZoneWatch] Skip restore {ticker} — "
                    f"order {order_id[:8]} status='{status}' (filled/cancelled)"
                )
                skipped += 1
                continue
            _register_zone_watch(
                ticker, today_str, order_id, direction, ib_low,
                float(row.get("entry_price_sim") or 0),
                float(row.get("stop_price_sim")  or 0),
                float(row.get("target_price_sim") or 0),
                int(row.get("alpaca_qty") or 0),
            )
            restored += 1
        if restored or skipped:
            log.info(
                f"[ZoneWatch] ♻️ Restored {restored} zone watch entry(s) from DB on restart "
                f"({skipped} skipped — already filled/cancelled)"
            )
    except Exception as _e:
        log.warning(f"[ZoneWatch] Startup restore failed (non-fatal): {_e}")


def _monitor_zone_invalidation() -> None:
    """Check open bracket orders — cancel if their IB zone has been broken.

    Runs every ZONE_CHECK_INTERVAL seconds (5 min) during market hours.
    For Bullish Break setups: cancelled if the last completed 5-min bar closed
    below the IB low (invalidation_price).
    For Bearish Break setups: cancelled if the last completed 5-min bar closed
    above the IB high (invalidation_price).
    Uses candle-close (not tick) to avoid noise from intraday wicks.

    Before acting, verifies each order's Alpaca status:
    - filled/partially_filled → entry triggered; remove from watch silently
    - canceled/expired        → already gone; remove from watch silently
    - new/accepted            → entry still pending; check bar close
    Cancels ONLY the specific parent order_id, not all orders for the ticker,
    preventing accidental cancellation of TP/SL legs on a filled position.

    Sends a Telegram alert and patches skip_reason='zone_cancelled' in
    paper_trades on cancellation.  The 2 PM intraday scan re-evaluates naturally.
    """
    global _ZONE_LAST_CHECK_TS
    if not LIVE_ORDERS_ENABLED:
        return

    with _ZONE_WATCH_LOCK:
        if not _ZONE_WATCH:
            return

    # Only run during market hours 9:35 → 15:55
    now_et  = datetime.now(EASTERN)
    cur_min = now_et.hour * 60 + now_et.minute
    if cur_min < 9 * 60 + 35 or cur_min > 15 * 60 + 55:
        return

    # Rate-limit to once every ZONE_CHECK_INTERVAL seconds
    import time as _time
    if _time.time() - _ZONE_LAST_CHECK_TS < ZONE_CHECK_INTERVAL:
        return
    _ZONE_LAST_CHECK_TS = _time.time()

    today_str = str(now_et.date())

    with _ZONE_WATCH_LOCK:
        keys_today = [(t, d) for t, d in _ZONE_WATCH if d == today_str]
    if not keys_today:
        return

    # ── Step 1: Check order status for each entry; prune filled/cancelled ──────
    # This MUST run before the bar-price check so we never act on an entry that
    # has already filled — touching the ticker's orders after fill could cancel
    # the live stop-loss or take-profit legs of the active bracket position.
    _OPEN_STATUSES   = {"new", "accepted", "pending_new", "accepted_for_bidding"}
    _FILLED_STATUSES = {"filled", "partially_filled"}
    _DONE_STATUSES   = {"canceled", "expired", "replaced"}

    still_pending = []      # (key, watch) — confirmed still open, safe to check price
    to_remove     = []      # (key, reason) — clean up from watch, no price action needed

    for key in list(keys_today):
        with _ZONE_WATCH_LOCK:
            watch = _ZONE_WATCH.get(key)
        if watch is None:
            continue
        order_id = watch["order_id"]
        status   = _alpaca_get_order_status(order_id)

        if not status:
            # API error — keep in watch, try again next cycle
            still_pending.append((key, watch))
            continue
        if status in _OPEN_STATUSES:
            still_pending.append((key, watch))
        elif status in _FILLED_STATUSES:
            to_remove.append((key, f"entry filled (status={status})"))
        elif status in _DONE_STATUSES:
            to_remove.append((key, f"order gone (status={status})"))
        else:
            # Unknown status — leave in watch, log for visibility
            log.info(f"[ZoneWatch] {key[0]} unknown order status '{status}' — leaving in watch")
            still_pending.append((key, watch))

    for key, reason in to_remove:
        log.info(f"[ZoneWatch] Removing {key[0]} from watch — {reason}")
        with _ZONE_WATCH_LOCK:
            _ZONE_WATCH.pop(key, None)

    if not still_pending:
        return

    # ── Step 2: Fetch last completed 5-min bar close for pending tickers ───────
    tickers   = list({t for t, _ in still_pending})
    bar_close = _alpaca_get_5min_bar_close(tickers)

    if not bar_close:
        log.warning("[ZoneWatch] No 5-min bar data returned — skipping invalidation check")
        return

    log.info(
        f"[ZoneWatch] 5-min bar close check — "
        + ", ".join(
            f"{t}=${bar_close.get(t, '?')}"
            f"(inval=${dict(still_pending).get((t, today_str), {}).get('invalidation_price', '?') if (t, today_str) in dict(still_pending) else '?'})"
            for t, _ in still_pending
        )
    )

    # ── Step 3: Cancel orders whose zone is confirmed broken on candle close ───
    to_cancel = []
    for key, watch in still_pending:
        ticker      = key[0]
        bar_c       = bar_close.get(ticker)
        inval_price = watch["invalidation_price"]
        direction   = watch["direction"]
        if bar_c is None:
            log.info(f"[ZoneWatch] {ticker}: no bar data this cycle — skipping")
            continue
        # Bullish Break: IB zone broken when 5-min candle closes below IB low
        if direction == "Bullish Break" and bar_c < inval_price:
            to_cancel.append((key, watch.copy(), bar_c))
        # Bearish Break: IB zone broken when 5-min candle closes above IB high
        elif direction == "Bearish Break" and bar_c > inval_price:
            to_cancel.append((key, watch.copy(), bar_c))

    for key, watch, bar_c in to_cancel:
        ticker      = key[0]
        order_id    = watch["order_id"]
        inval_price = watch["invalidation_price"]
        trade_date  = key[1]

        direction   = watch["direction"]
        if direction == "Bearish Break":
            _inval_desc = f"${bar_c:.2f} > IB high ${inval_price:.2f}"
        else:
            _inval_desc = f"${bar_c:.2f} < IB low ${inval_price:.2f}"

        log.warning(
            f"[ZoneWatch] ⚠️ {ticker} zone BROKEN — "
            f"5-min bar closed {_inval_desc} — "
            f"cancelling parent order {order_id[:8]}…"
        )

        # Cancel ONLY the specific parent entry order (not all orders for the ticker)
        # so that any other open orders (e.g. a separate position's legs) are safe.
        _alpaca_base = "https://paper-api.alpaca.markets" if IS_PAPER_ALPACA else "https://api.alpaca.markets"
        _alp_headers = {
            "APCA-API-KEY-ID":     ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        }
        _del_resp  = None   # init before try so the else-branch is always safe
        _cancel_ok = False
        try:
            import requests as _req
            _del_resp  = _req.delete(
                f"{_alpaca_base}/v2/orders/{order_id}",
                headers=_alp_headers, timeout=8,
            )
            _cancel_ok = _del_resp.status_code in (200, 204)
        except Exception as _ce:
            log.warning(f"[ZoneWatch] Cancel request error for {ticker}: {_ce}")

        if _cancel_ok:
            log.info(f"[ZoneWatch] ✅ {ticker}: parent order {order_id[:8]} cancelled")
            tg_send(
                f"⚠️ <b>{ticker} Bracket Cancelled — Zone Broken</b>\n"
                f"5-min bar closed <b>{_inval_desc}</b>\n"
                f"Entry order cancelled — structure invalidated before trigger.\n"
                f"<i>Bot will re-evaluate at 2 PM scan if setup recovers.</i>"
            )
            # Patch paper_trades row so the dashboard can show why this setup was cleared
            if _supabase_client:
                try:
                    _supabase_client.table("paper_trades").update({
                        "skip_reason": "zone_cancelled",
                    }).eq("user_id", USER_ID).eq("trade_date", trade_date).eq(
                        "ticker", ticker
                    ).eq("alpaca_order_id", order_id).execute()
                except Exception as _pe:
                    log.warning(f"[ZoneWatch] skip_reason patch failed (non-fatal): {_pe}")
        else:
            _status_code = getattr(_del_resp, "status_code", "network-error")
            log.warning(
                f"[ZoneWatch] {ticker}: cancel returned {_status_code} "
                f"— removing from watch anyway"
            )

        with _ZONE_WATCH_LOCK:
            _ZONE_WATCH.pop(key, None)


def _alpaca_get_positions() -> list:
    """Return list of open Alpaca position dicts, or [] on error."""
    base = "https://paper-api.alpaca.markets" if IS_PAPER_ALPACA else "https://api.alpaca.markets"
    headers = {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    try:
        import requests as _req
        resp = _req.get(f"{base}/v2/positions", headers=headers, timeout=8)
        if resp.status_code == 200:
            return resp.json() or []
    except Exception as _e:
        log.warning(f"[TrailingStop] positions fetch error: {_e}")
    return []


def _alpaca_cancel_orders_for_ticker(ticker: str) -> int:
    """Cancel all open orders for a ticker. Returns count cancelled."""
    base = "https://paper-api.alpaca.markets" if IS_PAPER_ALPACA else "https://api.alpaca.markets"
    headers = {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    try:
        import requests as _req
        resp = _req.get(
            f"{base}/v2/orders",
            headers=headers,
            params={"status": "open", "symbols": ticker.upper(), "limit": 50},
            timeout=8,
        )
        if resp.status_code != 200:
            return 0
        orders = resp.json() or []
        cancelled = 0
        for o in orders:
            oid = o.get("id", "")
            if not oid:
                continue
            dr = _req.delete(f"{base}/v2/orders/{oid}", headers=headers, timeout=8)
            if dr.status_code in (200, 204):
                cancelled += 1
        return cancelled
    except Exception as _e:
        log.warning(f"[TrailingStop] cancel orders error for {ticker}: {_e}")
    return 0


def _alpaca_place_trailing_stop(
    ticker: str,
    qty: int,
    trail_price: float,
    side: str = "sell",
) -> dict:
    """Place a trailing stop order.

    trail_price = fixed dollar trail amount (e.g. original stop distance = 1R).
    When price hits T1 and we trail, the stop follows the price up by trail_price.
    Returns {'ok': bool, 'order_id': str, 'error': str}.
    """
    base = "https://paper-api.alpaca.markets" if IS_PAPER_ALPACA else "https://api.alpaca.markets"
    headers = {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        "Content-Type":        "application/json",
    }
    payload = {
        "symbol":        ticker.upper(),
        "qty":           str(qty),
        "side":          side,
        "type":          "trailing_stop",
        "time_in_force": "day",
        "trail_price":   f"{trail_price:.4f}",
    }
    try:
        import requests as _req
        resp = _req.post(f"{base}/v2/orders", headers=headers, json=payload, timeout=10)
        data = resp.json() if resp.content else {}
        if resp.status_code in (200, 201):
            return {"ok": True, "order_id": data.get("id", ""), "error": ""}
        return {"ok": False, "order_id": "", "error": data.get("message", str(resp.status_code))}
    except Exception as _e:
        return {"ok": False, "order_id": "", "error": str(_e)}


def _monitor_trailing_stops() -> None:
    """Check all open Alpaca positions — if any have hit T1 (target_r), cancel
    the bracket and replace with a trailing stop so runners can extend.

    Runs every 30 seconds during market hours from the main scheduling loop.

    Logic:
      1. Fetch open paper_trades today with alpaca_order_id set and no fill yet.
      2. Fetch current Alpaca positions.
      3. Cross-reference by ticker.
      4. Compute unrealized R = unrealized_pl_per_share / stop_dist.
      5. If unrealized_R >= target_r (T1): cancel bracket, place trailing stop.
      6. Guard via _TRAILING_STOP_ACTIVATED to avoid double-firing.
    """
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return

    now_et = datetime.now(EASTERN)
    # Only run during market hours 9:35 → 15:58
    mkt_open_min  = 9 * 60 + 35
    mkt_close_min = 15 * 60 + 58
    cur_min       = now_et.hour * 60 + now_et.minute
    if cur_min < mkt_open_min or cur_min > mkt_close_min:
        return

    today_str = str(now_et.date())

    # Fetch open paper_trades for today.
    # nearest_resistance and nearest_support are written by log_context_levels()
    # at scan time so the v6 trail-tightening logic below can read them without
    # a secondary backtest_context_levels lookup (which returns NULL for today's
    # live trades until the nightly backfill runs).
    # The select is tried with the new S/R columns first; if the schema migration
    # has not yet been applied (column unknown → PostgREST 400) we fall back to
    # the legacy column list so trailing-stop monitoring is never disabled solely
    # by a missing migration.
    _BASE_SELECT = (
        "ticker,predicted,scan_type,"
        "entry_price_sim,stop_price_sim,target_price_sim,"
        "alpaca_order_id,alpaca_qty,alpaca_fill_price,win_loss,trade_date"
    )
    _SR_COLS  = ",nearest_resistance,nearest_support"
    try:
        open_trades = (
            _supabase_client
            .table("paper_trades")
            .select(_BASE_SELECT + _SR_COLS)
            .eq("user_id", USER_ID)
            .eq("trade_date", today_str)
            .not_.is_("alpaca_order_id", "null")
            .is_("win_loss", "null")
            .execute()
        ).data or []
    except Exception as _e:
        _es = str(_e)
        # Column-not-found error from PostgREST (schema cache miss or missing
        # migration) — retry without the new S/R columns so monitoring continues.
        if "nearest_resistance" in _es or "nearest_support" in _es or "PGRST" in _es:
            log.warning(
                f"[TrailingStop] S/R columns not in DB schema yet — retrying without them. "
                f"Run migration add_sr_levels_paper_trades.sql to enable v6 trail-tightening "
                f"from paper_trades. Error: {_e}"
            )
            try:
                open_trades = (
                    _supabase_client
                    .table("paper_trades")
                    .select(_BASE_SELECT)
                    .eq("user_id", USER_ID)
                    .eq("trade_date", today_str)
                    .not_.is_("alpaca_order_id", "null")
                    .is_("win_loss", "null")
                    .execute()
                ).data or []
            except Exception as _e2:
                log.warning(f"[TrailingStop] DB fetch error (fallback): {_e2}")
                return
        else:
            log.warning(f"[TrailingStop] DB fetch error: {_e}")
            return

    if not open_trades:
        return

    # Fetch live Alpaca positions
    positions = _alpaca_get_positions()
    pos_by_ticker = {p["symbol"].upper(): p for p in positions}

    for row in open_trades:
        ticker    = (row.get("ticker") or "").upper()
        guard_key = (ticker, today_str)

        pos = pos_by_ticker.get(ticker)

        if guard_key in _TRAILING_STOP_ACTIVATED:
            # Trailing stop was activated earlier for this ticker.
            # If the position is still open in Alpaca, nothing more to do yet.
            if pos is not None:
                continue

            # ── Position is gone → trailing stop filled intraday ─────────────
            # Patch paper_trades immediately with the realized P&L so the row
            # doesn't sit with win_loss=NULL until 3:30 PM.
            # _TRAILING_STOP_STOPOUT_ALERTED guards against double-processing on
            # subsequent 30-second monitoring cycles before the DB row is patched.
            if guard_key in _TRAILING_STOP_STOPOUT_ALERTED:
                continue
            _TRAILING_STOP_STOPOUT_ALERTED.add(guard_key)
            # Clear any lingering zone-watch entry for this ticker — entry has filled,
            # so zone-invalidation monitoring is no longer relevant.
            _zw_key = (ticker, today_str)
            with _ZONE_WATCH_LOCK:
                if _zw_key in _ZONE_WATCH:
                    _ZONE_WATCH.pop(_zw_key)
                    log.info(f"[ZoneWatch] Cleared {ticker} on trailing-stop fill detection")
            log.info(
                f"[TrailingStop] {ticker} — position no longer in Alpaca: "
                f"trailing stop filled intraday. Patching paper_trades now."
            )
            try:
                fills, fills_err = fetch_alpaca_fills(
                    api_key=ALPACA_API_KEY,
                    secret_key=ALPACA_SECRET_KEY,
                    is_paper=IS_PAPER_ALPACA,
                    trade_date=today_str,
                )
                if fills_err:
                    log.warning(
                        f"[TrailingStop] {ticker} — fill fetch error: {fills_err}"
                    )
                else:
                    roundtrips = match_fills_to_roundtrips(fills)
                    rt = next(
                        (r for r in roundtrips if (r.get("symbol") or "").upper() == ticker),
                        None,
                    )
                    if not rt:
                        log.warning(
                            f"[TrailingStop] {ticker} — no matching roundtrip in fills yet; "
                            f"will be patched at 3:30 PM sweep."
                        )
                    else:
                        avg_entry   = float(rt.get("avg_entry")   or 0)
                        avg_exit    = float(rt.get("avg_exit")    or 0)
                        rt_qty      = float(rt.get("qty")         or 0)
                        pnl_dollars = float(rt.get("pnl_dollars") or 0)
                        wl          = str(rt.get("win_loss", ""))

                        # For a long (Bullish Break), the trailing stop sells
                        # → avg_exit is the exit fill.  For a short (Bearish
                        # Break), the trailing stop buys-to-cover → avg_entry
                        # is the exit fill (same convention as ForceClose).
                        direction   = row.get("predicted", "")
                        exit_fill   = avg_exit if direction == "Bullish Break" else avg_entry

                        # R calculation: prefer actual Alpaca entry fill over sim
                        entry_sim         = float(row.get("entry_price_sim")  or 0)
                        stop_sim          = float(row.get("stop_price_sim")   or 0)
                        alpaca_fill_entry = float(row.get("alpaca_fill_price") or 0)
                        effective_entry   = (
                            alpaca_fill_entry if alpaca_fill_entry > 0 else entry_sim
                        )
                        stop_dist = (
                            abs(effective_entry - stop_sim)
                            if (stop_sim > 0 and effective_entry > 0) else 0
                        )
                        pnl_r: float | None = None
                        if stop_dist > 0 and rt_qty > 0:
                            pnl_r = round(pnl_dollars / (stop_dist * rt_qty), 3)

                        patch: dict = {
                            "win_loss":               wl,
                            "alpaca_exit_fill_price": exit_fill,
                        }
                        if pnl_r is not None:
                            patch["pnl_r_actual"] = pnl_r
                        patch["notes"] = (
                            f"Trailing stop filled intraday at "
                            f"{now_et.strftime('%H:%M ET')} on {today_str} | "
                            f"exit fill: {exit_fill:.4f} | P&L: ${pnl_dollars:+.2f}"
                            + (f" | {pnl_r:+.2f}R" if pnl_r is not None else "")
                        )

                        # Capture S/R context before the DB-patch block so it is
                        # still available for the alert even if the guard is cleared.
                        sr_ctx = _TRAILING_STOP_SR_CONTEXT.get(guard_key)

                        order_id = row.get("alpaca_order_id")
                        try:
                            if order_id:
                                _supabase_client.table("paper_trades").update(patch).eq(
                                    "user_id", USER_ID
                                ).eq("trade_date", today_str).eq(
                                    "alpaca_order_id", order_id
                                ).execute()
                            else:
                                _supabase_client.table("paper_trades").update(patch).eq(
                                    "user_id", USER_ID
                                ).eq("trade_date", today_str).eq(
                                    "ticker", ticker
                                ).not_.is_("alpaca_order_id", "null").execute()
                            log.info(
                                f"[TrailingStop] {ticker} — paper_trades patched intraday: "
                                f"{wl} | exit={exit_fill:.4f} | ${pnl_dollars:+.2f}"
                                + (f" | {pnl_r:+.2f}R" if pnl_r is not None else "")
                            )
                            # ── Clear guard only after DB patch confirms stop-out ──
                            # Fills matched and win_loss is now written.  Remove the
                            # guard key from all module-level sets so that a same-day
                            # re-entry on this ticker can activate its own trailing
                            # stop.  Clearing is intentionally skipped if fills are
                            # unavailable or the DB update fails — the guard stays
                            # in place and the 3:30 PM sweep will close the row.
                            _TRAILING_STOP_ACTIVATED.discard(guard_key)
                            _TRAILING_STOP_SR_CONTEXT.pop(guard_key, None)
                            _TRAILING_STOP_STOPOUT_ALERTED.discard(guard_key)
                            log.info(
                                f"[TrailingStop] {ticker} — guard cleared after confirmed "
                                f"stop-out patch; same-day re-entry eligible for trailing stop."
                            )
                        except Exception as _patch_err:
                            log.warning(
                                f"[TrailingStop] {ticker} — paper_trades patch failed: {_patch_err}"
                            )

                        # Build enriched stop-out alert; include S/R context if available
                        wl_emoji = "🟢" if wl == "Win" else ("🔴" if wl == "Loss" else "⬜")
                        r_line   = (
                            f"\nRealized R: <b>{pnl_r:+.2f}R</b>" if pnl_r is not None else ""
                        )
                        if sr_ctx:
                            sr_line = (
                                f"\n🎯 S/R wall: {sr_ctx['level_type']} @ "
                                f"<b>${sr_ctx['level']:.2f}</b>"
                            )
                        else:
                            sr_line = ""
                        tg_send(
                            f"{wl_emoji} <b>Trailing Stop Filled — {ticker}</b>\n"
                            f"Exit fill: <b>${exit_fill:.2f}</b>"
                            f" | Qty: {int(rt_qty)} shares\n"
                            f"P&amp;L: <b>${pnl_dollars:+.2f}</b>{r_line}{sr_line}\n"
                            f"Filled at {now_et.strftime('%H:%M ET')} — DB updated ✅"
                        )
            except Exception as _fill_err:
                log.warning(
                    f"[TrailingStop] {ticker} — intraday fill reconciliation failed: {_fill_err}"
                )
            continue

        if not pos:
            continue

        entry_sim  = float(row.get("entry_price_sim") or 0)
        stop_sim   = float(row.get("stop_price_sim")  or 0)
        target_sim = float(row.get("target_price_sim") or 0)
        qty        = int(row.get("alpaca_qty") or 0)
        direction  = row.get("predicted", "")
        scan_type  = (row.get("scan_type") or "").strip()

        if not entry_sim or not stop_sim or qty <= 0:
            continue

        stop_dist = abs(entry_sim - stop_sim)
        if stop_dist <= 0:
            continue

        unrealized_pl = float(pos.get("unrealized_pl", 0) or 0)
        unrealized_r  = unrealized_pl / (stop_dist * qty) if qty > 0 else 0

        # T1 target in R (use stored target_price_sim to back-calculate)
        if target_sim and entry_sim and stop_dist:
            target_r_actual = abs(target_sim - entry_sim) / stop_dist
        else:
            target_r_actual = 1.0

        if unrealized_r < target_r_actual:
            continue

        # ── T1 HIT — convert to trailing stop ────────────────────────────────
        log.info(
            f"[TrailingStop] {ticker} hit T1 — unrealized={unrealized_r:.2f}R "
            f"(target={target_r_actual:.1f}R) — converting bracket → trailing stop"
        )

        cancelled = _alpaca_cancel_orders_for_ticker(ticker)
        log.info(f"[TrailingStop] {ticker} — cancelled {cancelled} open bracket order(s)")

        # ── v6 S/R-aware trail tightening ────────────────────────────────────
        # At T1 hit, if nearest S/R is within 0.3R of the CURRENT PRICE,
        # the trade has run into a known wall → tighten trail to 0.5R to lock
        # in more gain before a potential reversal at the S/R level.
        # Falls back to 1.0R (standard) when no context data is available.
        #
        # Level source priority:
        #   1. nearest_resistance / nearest_support on the paper_trades row itself —
        #      written by log_context_levels() at scan time so it is always fresh
        #      for today's live trades.
        #   2. backtest_context_levels table — populated by the nightly backfill;
        #      used as a fallback in case the paper_trades columns are NULL (e.g.
        #      for older rows inserted before this feature was deployed).
        cur_px         = float(pos.get("current_price", 0) or pos.get("lastday_price", 0) or 0)
        trail_size     = stop_dist       # default: 1R trail
        trail_r_label  = "1R"
        trail_tightened = False
        _sr_level: float | None = None   # S/R level that triggered tightening (persisted to DB)
        _sr_dist:  float | None = None   # distance from cur_px to that level (persisted to DB)
        _sr_label: str   | None = None   # "Resistance" or "Support"
        _ctx_source: str | None = None   # which table the level came from (persisted to DB)
        try:
            # ── Source 1: paper_trades columns (always current) ───────────────
            _pt_res = row.get("nearest_resistance")
            _pt_sup = row.get("nearest_support")
            if _pt_res is not None or _pt_sup is not None:
                ctx = {"nearest_resistance": _pt_res, "nearest_support": _pt_sup}
                _ctx_source = "paper_trades"
                log.info(
                    f"[TrailingStop] {ticker} S/R from paper_trades: "
                    f"res={_pt_res} sup={_pt_sup}"
                )
            else:
                # ── Source 2: backtest_context_levels (nightly backfill) ──────
                ctx_resp = (
                    _supabase_client
                    .table("backtest_context_levels")
                    .select("nearest_resistance,nearest_support")
                    .eq("ticker", ticker)
                    .eq("trade_date", today_str)
                    .eq("scan_type", scan_type)
                    .limit(1)
                    .execute()
                )
                ctx_rows = ctx_resp.data or []
                ctx = ctx_rows[0] if ctx_rows else {}
                if ctx:
                    _ctx_source = "backtest_context_levels"
                    log.info(
                        f"[TrailingStop] {ticker} S/R from backtest_context_levels "
                        f"(paper_trades columns were NULL): "
                        f"res={ctx.get('nearest_resistance')} sup={ctx.get('nearest_support')}"
                    )
                else:
                    log.info(
                        f"[TrailingStop] {ticker} no S/R context found in either source "
                        f"— using default 1R trail"
                    )

            if _ctx_source and cur_px > 0:
                if direction == "Bullish Break":
                    _nearest = ctx.get("nearest_resistance")
                    # Tighten when resistance is within 0.3R above current price at T1
                    if (_nearest is not None
                            and float(_nearest) >= cur_px
                            and (float(_nearest) - cur_px) <= 0.3 * stop_dist):
                        trail_size    = stop_dist * 0.5
                        trail_r_label = "0.5R"
                        trail_tightened = True
                        _sr_level = float(_nearest)
                        _sr_dist  = _sr_level - cur_px
                        _sr_label = "Resistance"
                        _TRAILING_STOP_SR_CONTEXT[guard_key] = {
                            "level_type": "resistance",
                            "level":      _sr_level,
                            "gap":        round(_sr_dist, 2),
                        }
                        log.info(
                            f"[TrailingStop] {ticker} v6 tighten [{_ctx_source}]: "
                            f"resistance=${_sr_level:.2f} "
                            f"is {_sr_dist:.2f} above cur_px=${cur_px:.2f} "
                            f"(within 0.3R={0.3*stop_dist:.2f}) → trail={trail_r_label}"
                        )
                else:  # Bearish Break
                    _nearest = ctx.get("nearest_support")
                    # Tighten when support is within 0.3R below current price at T1
                    if (_nearest is not None
                            and float(_nearest) <= cur_px
                            and (cur_px - float(_nearest)) <= 0.3 * stop_dist):
                        trail_size    = stop_dist * 0.5
                        trail_r_label = "0.5R"
                        trail_tightened = True
                        _sr_level = float(_nearest)
                        _sr_dist  = cur_px - _sr_level
                        _sr_label = "Support"
                        _TRAILING_STOP_SR_CONTEXT[guard_key] = {
                            "level_type": "support",
                            "level":      _sr_level,
                            "gap":        round(_sr_dist, 2),
                        }
                        log.info(
                            f"[TrailingStop] {ticker} v6 tighten [{_ctx_source}]: "
                            f"support=${_sr_level:.2f} "
                            f"is {_sr_dist:.2f} below cur_px=${cur_px:.2f} "
                            f"(within 0.3R={0.3*stop_dist:.2f}) → trail={trail_r_label}"
                        )
        except Exception as _ctx_e:
            log.warning(f"[TrailingStop] {ticker} S/R context fetch failed (using 1R trail): {_ctx_e}")

        ts_side   = "sell" if direction == "Bullish Break" else "buy"
        ts_result = _alpaca_place_trailing_stop(ticker, qty, trail_size, side=ts_side)

        if ts_result["ok"]:
            _TRAILING_STOP_ACTIVATED.add(guard_key)
            # Entry confirmed filled (trailing stop now live) — remove from zone watch
            _zw_key2 = (ticker, today_str)
            with _ZONE_WATCH_LOCK:
                if _zw_key2 in _ZONE_WATCH:
                    _ZONE_WATCH.pop(_zw_key2)
                    log.info(f"[ZoneWatch] Cleared {ticker} — trailing stop activated (entry filled)")

            # ── Persist trail context to paper_trades ─────────────────────────
            # Stored so the reason for tightening survives a bot restart and is
            # available for post-trade analysis and close-out Telegram alerts.
            if _supabase_client:
                _trail_patch: dict = {
                    "trail_activated": True,
                    "trail_size_r":    trail_r_label,
                }
                if _sr_level is not None:
                    _trail_patch["trail_sr_level"]    = round(_sr_level, 4)
                if _sr_dist is not None:
                    _trail_patch["trail_sr_distance"] = round(_sr_dist, 4)
                if _ctx_source is not None:
                    _trail_patch["trail_sr_source"]   = _ctx_source
                _trail_order_id = row.get("alpaca_order_id")
                try:
                    if _trail_order_id:
                        _supabase_client.table("paper_trades").update(_trail_patch).eq(
                            "user_id", USER_ID
                        ).eq("trade_date", today_str).eq(
                            "alpaca_order_id", _trail_order_id
                        ).execute()
                    else:
                        _supabase_client.table("paper_trades").update(_trail_patch).eq(
                            "user_id", USER_ID
                        ).eq("trade_date", today_str).eq("ticker", ticker).not_.is_(
                            "alpaca_order_id", "null"
                        ).execute()
                    log.info(
                        f"[TrailingStop] {ticker} — trail context persisted to paper_trades "
                        f"(trail={trail_r_label}, sr_level={_sr_level}, source={_ctx_source})"
                    )
                except Exception as _persist_e:
                    log.warning(
                        f"[TrailingStop] {ticker} — could not persist trail context "
                        f"(non-critical, likely missing migration): {_persist_e}"
                    )

            if trail_tightened and _sr_level is not None and _sr_dist is not None:
                _dir_word = "above" if _sr_label == "Resistance" else "below"
                _src_tag  = f" [{_ctx_source}]" if _ctx_source else ""
                tighten_note = (
                    f" 🎯 Tight trail — {_sr_label} at <b>${_sr_level:.2f}</b>"
                    f" ({_sr_dist:.2f} {_dir_word} price){_src_tag}"
                )
            else:
                tighten_note = " — runners run 🚀"
            tg_send(
                f"🔄 <b>Trailing Stop Activated — {ticker}</b>\n"
                f"T1 hit: <b>{unrealized_r:.2f}R</b> unrealized\n"
                f"Current: <b>${cur_px:.2f}</b> | Qty: {qty} shares\n"
                f"Trailing by <b>${trail_size:.2f}/share</b> ({trail_r_label}){tighten_note}\n"
                f"<code>{ts_result['order_id'][:8]}…</code>"
            )
            log.info(f"[TrailingStop] {ticker} — trailing stop placed ✅ trail={trail_r_label} (order {ts_result['order_id'][:8]})")
        else:
            log.warning(f"[TrailingStop] {ticker} — trailing stop FAILED: {ts_result['error']}")
            tg_send(
                f"⚠️ <b>Trailing Stop Failed — {ticker}</b>\n"
                f"T1 hit at {unrealized_r:.2f}R but couldn't place trailing order.\n"
                f"Error: {ts_result['error']}"
            )


def _force_close_all_positions() -> None:
    """Market-close all open Alpaca positions at 3:30 PM ET.

    Runs once per trading day from the main scheduling loop.  Prevents holding
    positions through the final 30-minute close auction where spreads widen and
    fills become unpredictable on paper accounts.

    Strategy: use Alpaca's DELETE /v2/positions/{symbol} endpoint, which
    atomically cancels any open bracket/exit orders for the symbol AND submits
    a market order to flatten the position — avoiding the order-rejection race
    condition that occurs when a trailing stop or bracket order is still active.

    After the first pass a 90-second validation loop re-checks for any surviving
    positions and retries once per symbol.

    After closure: computes per-trade realized P&L and R, patches paper_trades
    rows with win_loss and pnl_r_actual, and sends a day-end P&L card to Telegram.
    """
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return

    positions = _alpaca_get_positions()
    if not positions:
        log.info("[ForceClose] 3:30 PM sweep — no open positions, nothing to close.")
        return

    import requests as _req
    import time as _time

    base = "https://paper-api.alpaca.markets" if IS_PAPER_ALPACA else "https://api.alpaca.markets"
    headers = {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        "Content-Type":        "application/json",
    }

    # ── Snapshot pre-close state ──────────────────────────────────────────────
    today_str = datetime.now(EASTERN).strftime("%Y-%m-%d")

    # Session-open equity captured at 9:35 AM by the main scheduling loop.
    # Used for the equity change line in the P&L summary.
    equity_open: float | None = _SESSION_OPEN_EQUITY.get(today_str)

    # Snapshot position data: actual entry price and side from Alpaca (keyed by symbol).
    # avg_entry_price from Alpaca is correct for both long (buy entry) and short (sell entry).
    # This is used as the fallback effective_entry when computing stop distance for R.
    pos_snapshot: dict[str, dict] = {}
    for pos in positions:
        sym = (pos.get("symbol") or "").upper()
        if sym:
            pos_snapshot[sym] = {
                "avg_entry_price": float(pos.get("avg_entry_price") or 0),
                "qty":             float(pos.get("qty") or 0),
                "unrealized_pl":   float(pos.get("unrealized_pl") or 0),
                "side":            str(pos.get("side", "long")).lower(),
            }

    # Fetch today's paper_trades rows for the symbols being closed so we have
    # entry_price_sim and stop_price_sim for R calculations (stop distance = 1R).
    pt_rows_by_ticker: dict[str, dict] = {}
    if _supabase_client:
        _fc_base_cols = "ticker,entry_price_sim,stop_price_sim,alpaca_fill_price,alpaca_qty,alpaca_order_id"
        _fc_trail_cols = ",trail_size_r,trail_sr_level,trail_sr_distance,trail_sr_source"
        try:
            pt_data = (
                _supabase_client
                .table("paper_trades")
                .select(_fc_base_cols + _fc_trail_cols)
                .eq("user_id", USER_ID)
                .eq("trade_date", today_str)
                .in_("ticker", list(pos_snapshot.keys()))
                .not_.is_("alpaca_order_id", "null")
                .execute()
            ).data or []
            for row in pt_data:
                sym = (row.get("ticker") or "").upper()
                if sym:
                    pt_rows_by_ticker[sym] = row
        except Exception as _pt_err:
            _pt_es = str(_pt_err)
            if any(c in _pt_es for c in ("trail_size_r", "trail_sr_level", "trail_sr_distance", "trail_sr_source", "PGRST")):
                log.info(
                    "[ForceClose] Trail context columns not in DB yet — retrying without them. "
                    "Run migration add_trail_tighten_context_paper_trades.sql to enable trail context in close-out alerts."
                )
                try:
                    pt_data = (
                        _supabase_client
                        .table("paper_trades")
                        .select(_fc_base_cols)
                        .eq("user_id", USER_ID)
                        .eq("trade_date", today_str)
                        .in_("ticker", list(pos_snapshot.keys()))
                        .not_.is_("alpaca_order_id", "null")
                        .execute()
                    ).data or []
                    for row in pt_data:
                        sym = (row.get("ticker") or "").upper()
                        if sym:
                            pt_rows_by_ticker[sym] = row
                except Exception as _pt_err2:
                    log.warning(f"[ForceClose] Could not fetch paper_trades rows (fallback): {_pt_err2}")
            else:
                log.warning(f"[ForceClose] Could not fetch paper_trades rows: {_pt_err}")

    closed   = []
    failed   = []

    def _close_one(ticker: str) -> bool:
        """Use Alpaca's DELETE /v2/positions/{symbol} — cancels open orders + closes position atomically."""
        try:
            resp = _req.delete(
                f"{base}/v2/positions/{ticker}",
                params={"percentage": "1"},
                headers=headers,
                timeout=10,
            )
            if resp.status_code in (200, 201, 204):
                log.info(f"[ForceClose] {ticker} — position close submitted via DELETE /v2/positions")
                return True
            elif resp.status_code == 404:
                log.info(f"[ForceClose] {ticker} — already flat (404)")
                return True
            else:
                log.warning(f"[ForceClose] {ticker} — DELETE failed: {resp.status_code} {resp.text[:200]}")
                return False
        except Exception as _e:
            log.warning(f"[ForceClose] {ticker} — exception: {_e}")
            return False

    # ── First pass ────────────────────────────────────────────────────────────
    symbols_attempted = []
    for pos in positions:
        ticker = pos.get("symbol", "").upper()
        if not ticker:
            continue
        symbols_attempted.append(ticker)
        if _close_one(ticker):
            closed.append(ticker)
        else:
            failed.append(ticker)

    # ── Validation pass (90 s later) ─────────────────────────────────────────
    if symbols_attempted:
        _time.sleep(90)
        still_open = [p.get("symbol", "").upper() for p in (_alpaca_get_positions() or [])]
        still_open = [s for s in still_open if s in symbols_attempted]
        for ticker in still_open:
            log.warning(f"[ForceClose] {ticker} still open after first pass — retrying")
            if _close_one(ticker):
                if ticker in failed:
                    failed.remove(ticker)
                    closed.append(ticker)
            else:
                log.error(f"[ForceClose] {ticker} — retry FAILED, manual close required")

    # ── P&L computation and paper_trades update ───────────────────────────────
    # Fetch today's filled orders from Alpaca to get actual exit prices for the
    # force-close market orders that just filled.
    trade_summaries: list[dict] = []
    if closed and _supabase_client:
        try:
            fills, fills_err = fetch_alpaca_fills(
                api_key=ALPACA_API_KEY,
                secret_key=ALPACA_SECRET_KEY,
                is_paper=IS_PAPER_ALPACA,
                trade_date=today_str,
            )
            if fills_err:
                log.warning(f"[ForceClose] Could not fetch fills for P&L: {fills_err}")
            else:
                roundtrips = match_fills_to_roundtrips(fills)
                closed_set = set(t.upper() for t in closed)
                for rt in roundtrips:
                    sym = (rt.get("symbol") or "").upper()
                    if sym not in closed_set:
                        continue

                    avg_entry    = float(rt.get("avg_entry") or 0)
                    avg_exit     = float(rt.get("avg_exit")  or 0)
                    qty          = float(rt.get("qty")        or 0)
                    pnl_dollars  = float(rt.get("pnl_dollars") or 0)
                    wl           = str(rt.get("win_loss", ""))

                    # Compute R using stop distance from paper_trades if available.
                    # pnl_dollars from match_fills_to_roundtrips is correctly signed for
                    # both long (buy-sell) and short (sell-buy) positions because the
                    # function labels the sell leg as avg_exit and the buy leg as avg_entry,
                    # so (avg_exit - avg_entry) * qty is always positive for a winning trade.
                    #
                    # effective_entry MUST use avg_entry_price from the Alpaca position
                    # snapshot (captured before the close) — this is the actual entry price
                    # for both long (buy price) and short (sell price).  Using avg_entry from
                    # match_fills_to_roundtrips would be wrong for shorts because the function
                    # labels the buy-to-cover (exit) as avg_entry.
                    pnl_r: float | None = None
                    pt_row     = pt_rows_by_ticker.get(sym, {})
                    stop_sim   = float(pt_row.get("stop_price_sim")  or 0)
                    alpaca_fill = float(pt_row.get("alpaca_fill_price") or 0)
                    snap_entry  = float((pos_snapshot.get(sym) or {}).get("avg_entry_price") or 0)

                    # Priority: actual Alpaca fill entry > position snapshot entry > sim entry
                    effective_entry = (
                        alpaca_fill if alpaca_fill > 0
                        else snap_entry if snap_entry > 0
                        else float(pt_row.get("entry_price_sim") or 0)
                    )
                    stop_dist = abs(effective_entry - stop_sim) if (stop_sim > 0 and effective_entry > 0) else 0

                    if stop_dist > 0 and qty > 0:
                        pnl_r = round(pnl_dollars / (stop_dist * qty), 3)

                    # Determine the actual exit fill price for this position.
                    # match_fills_to_roundtrips labels sells as avg_exit and buys as avg_entry
                    # regardless of strategy direction.  For a long position the force-close
                    # order is a sell, so avg_exit is the correct exit fill.  For a short
                    # position the force-close order is a buy-to-cover, so avg_entry (the buy
                    # leg price from the roundtrip) is the correct exit fill.
                    pos_side = (pos_snapshot.get(sym) or {}).get("side", "long")
                    exit_fill = avg_exit if pos_side == "long" else avg_entry

                    trade_summaries.append({
                        "symbol":          sym,
                        "avg_entry":       snap_entry if snap_entry > 0 else avg_entry,
                        "avg_exit":        exit_fill,
                        "qty":             qty,
                        "pnl_dollars":     pnl_dollars,
                        "pnl_r":           pnl_r,
                        "win_loss":        wl,
                        "trail_size_r":    pt_row.get("trail_size_r"),
                        "trail_sr_level":  pt_row.get("trail_sr_level"),
                        "trail_sr_source": pt_row.get("trail_sr_source"),
                    })

                    # Patch paper_trades row with win_loss, alpaca_exit_fill_price, pnl_r_actual.
                    # Use alpaca_order_id as the unique key when available to prevent accidental
                    # multi-row updates (e.g. if the same ticker appears twice in one day).
                    patch: dict = {"win_loss": wl, "alpaca_exit_fill_price": exit_fill}
                    if pnl_r is not None:
                        patch["pnl_r_actual"] = pnl_r
                    close_note = (
                        f"Force-closed at 3:30 PM ET on {today_str} | "
                        f"exit fill: {exit_fill:.4f} | P&L: ${pnl_dollars:+.2f}"
                        + (f" | {pnl_r:+.2f}R" if pnl_r is not None else "")
                    )
                    patch["notes"] = close_note

                    order_id = pt_row.get("alpaca_order_id")
                    try:
                        if order_id:
                            _supabase_client.table("paper_trades").update(patch).eq(
                                "user_id", USER_ID
                            ).eq("trade_date", today_str).eq("alpaca_order_id", order_id).execute()
                        else:
                            _supabase_client.table("paper_trades").update(patch).eq(
                                "user_id", USER_ID
                            ).eq("trade_date", today_str).eq("ticker", sym).not_.is_(
                                "alpaca_order_id", "null"
                            ).execute()
                        log.info(
                            f"[ForceClose] {sym} — patched paper_trades: "
                            f"{wl} | exit={exit_fill:.4f} | ${pnl_dollars:+.2f}"
                            + (f" | {pnl_r:+.2f}R" if pnl_r is not None else "")
                        )
                    except Exception as _patch_err:
                        log.warning(f"[ForceClose] {sym} — paper_trades patch failed: {_patch_err}")

        except Exception as _pnl_err:
            log.warning(f"[ForceClose] P&L computation failed (non-critical): {_pnl_err}")

    # Capture account equity after closing (post-force-close = end-of-session equity)
    equity_close: float | None = None
    try:
        equity_close = get_alpaca_account_equity(
            is_paper=IS_PAPER_ALPACA,
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )
    except Exception as _eq_err:
        log.warning(f"[ForceClose] Could not fetch post-close equity: {_eq_err}")

    # ── Build and send Telegram P&L summary ──────────────────────────────────
    if closed or failed:
        lines = ["🔔 <b>3:30 PM — Positions Force-Closed</b>"]

        if failed:
            lines.append(f"⚠️ Failed after retry: {', '.join(sorted(set(failed)))} — check manually")

        if trade_summaries:
            total_r       = sum(t["pnl_r"] for t in trade_summaries if t["pnl_r"] is not None)
            total_dollars = sum(t["pnl_dollars"] for t in trade_summaries)
            r_known       = [t for t in trade_summaries if t["pnl_r"] is not None]

            lines.append("")
            lines.append("<b>Day-End P&amp;L Summary</b>")

            for t in sorted(trade_summaries, key=lambda x: x["symbol"]):
                sym = t["symbol"]
                wl  = t["win_loss"]
                usd = t["pnl_dollars"]
                r   = t["pnl_r"]
                emoji = "🟢" if wl == "Win" else ("🔴" if wl == "Loss" else "⬜")
                r_str = f" | {r:+.2f}R" if r is not None else ""
                # Trail context note: prefer persisted columns; fall back to in-memory dict.
                _t_trail = t.get("trail_size_r")
                _t_level = t.get("trail_sr_level")
                sr_tag = ""
                if _t_trail:
                    sr_tag = f" 🎯 trail {_t_trail}"
                    if _t_level is not None:
                        sr_tag += f" (S/R ${float(_t_level):.2f})"
                elif _TRAILING_STOP_SR_CONTEXT.get((sym, today_str)):
                    sr_tag = " 🎯 S/R-tightened"
                lines.append(f"  {emoji} {sym}: ${usd:+.2f}{r_str}{sr_tag}")

            lines.append("")
            if r_known:
                r_emoji = "📈" if total_r > 0 else ("📉" if total_r < 0 else "➡️")
                lines.append(f"{r_emoji} <b>Total realized R: {total_r:+.2f}R</b>")
            lines.append(f"💵 Net P&amp;L: <b>${total_dollars:+.2f}</b>")

            # equity_open = session-open equity (captured at 9:35 AM market open).
            # equity_close = post-force-close equity (filled orders settled ~90 s after 3:30 PM).
            if equity_open is not None and equity_close is not None:
                equity_chg = equity_close - equity_open
                lines.append(
                    f"🏦 Equity: ${equity_open:,.2f} (open) → ${equity_close:,.2f} (close) "
                    f"({equity_chg:+.2f})"
                )
            elif equity_close is not None:
                lines.append(f"🏦 Closing equity: ${equity_close:,.2f}")
        else:
            # No fill data — just report closed/failed tickers
            if closed:
                lines.append(f"✅ Closed: {', '.join(sorted(set(closed)))}")
            if equity_close is not None:
                lines.append(f"🏦 Closing equity: ${equity_close:,.2f}")

        lines.append("")
        lines.append("<i>Avoids holding through close-auction spread widening</i>")
        tg_send("\n".join(lines))


def tg_send(message: str) -> bool:
    """Send a Telegram message. Returns True on success, False on failure.
    Silently skips if credentials are not configured.
    """
    if not TG_TOKEN or not TG_CHAT_ID:
        return False
    try:
        import requests as _req
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        resp = _req.post(url, json={
            "chat_id":    TG_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }, timeout=10)
        if resp.status_code == 200:
            return True
        else:
            log.warning(f"Telegram send failed: {resp.status_code} {resp.text[:100]}")
            return False
    except Exception as exc:
        log.warning(f"Telegram send error: {exc}")
        return False


def tg_reply(chat_id, text: str) -> None:
    """Send a reply to a specific Telegram chat."""
    if not TG_TOKEN:
        return
    try:
        import requests as _req
        _req.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as exc:
        log.warning(f"tg_reply error: {exc}")


def _parse_exitobs_command(text: str):
    """Parse /exitobs TICKER observation text...

    Returns (ticker, obs) or None on failure.
    Example: /exitobs SIDU exited at .47 when volume dried up under .50
    """
    parts = text.strip().split(maxsplit=2)
    if len(parts) < 3:
        return None
    cmd = parts[0].lower()
    if cmd not in ("/exitobs", "/exitobs@edgeiqbot"):
        return None
    ticker = parts[1].upper()
    obs    = parts[2].strip()
    if not obs:
        return None
    return ticker, obs


def _parse_log_command(text: str):
    """Parse /log TICKER win|loss entry exit [optional note...]

    Returns (ticker, win_loss, entry_price, exit_price, notes) or None on failure.
    Accepted formats:
      /log MIGI win 1.94 2.85
      /log MIGI loss 2.85 1.94 stop hit, lost discipline
      /log ARAI win 3.10 4.25 add on breakout, tp at r3
    """
    parts = text.strip().split()
    if len(parts) < 5:
        return None
    cmd = parts[0].lower()
    if cmd not in ("/log", "/log@edgeiqbot"):
        return None
    ticker   = parts[1].upper()
    wl_raw   = parts[2].lower()
    if wl_raw not in ("win", "loss", "w", "l"):
        return None
    win_loss = "Win" if wl_raw in ("win", "w") else "Loss"
    try:
        entry_price = float(parts[3])
        exit_price  = float(parts[4])
    except ValueError:
        return None
    notes = " ".join(parts[5:]) if len(parts) > 5 else ""
    return ticker, win_loss, entry_price, exit_price, notes


def telegram_listener() -> None:
    """Long-poll Telegram for incoming /log commands.
    Runs as a daemon thread — survives market hours, exits when bot exits.
    """
    if not TG_TOKEN:
        log.info("Telegram listener: no token, skipping.")
        return

    import requests as _req
    base   = f"https://api.telegram.org/bot{TG_TOKEN}"
    offset = None
    log.info("Telegram listener: started (polling for /log commands)")

    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            resp = _req.get(f"{base}/getUpdates", params=params, timeout=40)
            if resp.status_code != 200:
                time.sleep(5)
                continue
            updates = resp.json().get("result", [])
            for upd in updates:
                offset = upd["update_id"] + 1
                msg    = upd.get("message", {})
                text   = (msg.get("text") or "").strip()
                chat_id = msg.get("chat", {}).get("id")
                if not text or not chat_id:
                    continue

                if (not text.startswith("/log")
                        and not text.startswith("/start")
                        and not text.startswith("/exitobs")
                        and not text.startswith("/settings")):
                    continue

                # ── /start USER_ID — beta tester connection via deep link ──
                if text.startswith("/start"):
                    parts = text.split(maxsplit=1)
                    payload = parts[1].strip() if len(parts) > 1 else ""
                    if payload:
                        # Save chat_id linked to the user_id from the deep link.
                        # Portal links are personally distributed by the owner, so
                        # possession of a valid link is treated as authorization.
                        from_user = msg.get("from", {})
                        _tg_username = from_user.get("username", "")
                        _tg_first = from_user.get("first_name", "")
                        _tg_last = from_user.get("last_name", "")
                        if _tg_username:
                            _tg_name = f"@{_tg_username}"
                        elif _tg_first or _tg_last:
                            _tg_name = f"{_tg_first} {_tg_last}".strip()
                        else:
                            _tg_name = ""
                        _saved = False
                        try:
                            _saved = save_beta_chat_id(payload, chat_id, tg_name=_tg_name)
                        except Exception as _se:
                            log.warning(f"save_beta_chat_id failed: {_se}")
                        if _saved:
                            tg_reply(chat_id,
                                "✅ <b>You're connected to the EdgeIQ Scanner!</b>\n\n"
                                "You'll get morning setups and end-of-day results here "
                                "each trading day.\n\n"
                                "Keep logging your trades at your portal — "
                                "the scanner gets sharper the more data it has.")
                            log.info(f"Beta subscriber connected: user_id={payload} chat_id={chat_id}")
                        else:
                            tg_reply(chat_id,
                                "⚠️ <b>Connection failed.</b>\n\n"
                                "Please try tapping the button in your portal again.")
                            log.warning(f"save_beta_chat_id returned False for user_id={payload}")
                    else:
                        tg_reply(chat_id,
                            "👋 <b>EdgeIQ Scanner</b>\n\n"
                            "Open your personal portal link to connect your account for alerts.")
                    continue

                # ── /exitobs TICKER observation... ──
                if text.startswith("/exitobs"):
                    obs_parsed = _parse_exitobs_command(text)
                    if obs_parsed is None:
                        tg_reply(chat_id,
                            "⚠️ Bad format. Use:\n"
                            "<code>/exitobs TICKER your observation here</code>\n"
                            "Example: <code>/exitobs SIDU exited at .47, volume dried up under .50</code>")
                        continue
                    obs_ticker, obs_text = obs_parsed
                    from backend import patch_exit_obs
                    ok = patch_exit_obs(obs_ticker, None, obs_text, user_id=USER_ID)
                    if ok:
                        tg_reply(chat_id,
                            f"✅ <b>Exit note saved</b> — {obs_ticker}\n"
                            f"💬 {obs_text}")
                        log.info(f"exitobs saved: {obs_ticker} | {obs_text}")
                    else:
                        tg_reply(chat_id,
                            f"❌ Couldn't save note for {obs_ticker}. "
                            "Check that the trade exists in your journal and the DB migration has been run.")
                    continue

                # ── /settings [morning_alerts on|off] [eod_alerts on|off] [tcs_alerts on|off] [credential_alerts on|off] ──
                if text.startswith("/settings"):
                    from backend import (
                        get_user_id_by_chat_id,
                        load_user_prefs,
                        save_user_prefs,
                    )
                    parts = text.split()
                    sub_uid = get_user_id_by_chat_id(chat_id)
                    if not sub_uid:
                        tg_reply(chat_id,
                            "⚠️ Your account isn't linked yet. "
                            "Tap your portal link and use /start to connect.")
                        continue

                    sub_prefs = load_user_prefs(sub_uid)

                    if len(parts) == 1:
                        from backend import (
                            load_tcs_alert_structures,
                            WK_DISPLAY_PLAIN,
                        )
                        lines = [
                            "⚙️ <b>Your EdgeIQ Alert Settings</b>",
                            "━━━━━━━━━━━━━━━━━━━━━",
                        ]
                        toggle_cmds = []
                        for a in _ALERT_REGISTRY:
                            is_on = sub_prefs.get(a["pref_key"], a["default"]) is not False
                            status_icon = "✅ On" if is_on else "❌ Off"
                            lines.append(f"<b>{a['label']}</b>: {status_icon}")
                            lines.append(f"  ↳ {a['description']}")
                            if a.get("show_structures"):
                                user_structs = sub_prefs.get("tcs_alert_structures")
                                if user_structs is not None:
                                    try:
                                        watched: set | None = set(user_structs)
                                    except (TypeError, ValueError):
                                        watched = load_tcs_alert_structures()
                                else:
                                    watched = load_tcs_alert_structures()
                                lines.append("  ↳ Active structures:")
                                for sk in sorted(WK_DISPLAY.keys()):
                                    struct_on = (
                                        watched is None or sk in watched
                                    )
                                    struct_icon = "✅" if struct_on else "❌"
                                    lines.append(
                                        f"    {struct_icon} {WK_DISPLAY.get(sk, sk)}"
                                    )
                                lines.append(
                                    "  ↳ Use <code>/settings tcs_structures KEY1 KEY2 ... on|off</code> to toggle one or more structures "
                                    "(e.g. <code>/settings tcs_structures trend_bull trend_bear off</code>), "
                                    "<code>/settings tcs_structures all on|off</code> to toggle all at once, "
                                    "or <code>/settings tcs_structures reset</code> to restore all structures"
                                )
                                valid_keys = ", ".join(
                                    f"<code>{k}</code> ({WK_DISPLAY_PLAIN.get(k, k)})"
                                    for k in sorted(WK_DISPLAY.keys())
                                )
                                lines.append(f"  ↳ Valid keys: {valid_keys}")
                            toggle_cmds.append(
                                f"  <code>/settings {a['key']} on</code> | "
                                f"<code>/settings {a['key']} off</code>"
                            )
                        lines.append("━━━━━━━━━━━━━━━━━━━━━")
                        lines.append("To change:")
                        lines.extend(toggle_cmds)
                        tg_reply(chat_id, "\n".join(lines))
                    elif (
                        len(parts) == 3
                        and parts[2].lower() in ("on", "off")
                        and any(a["key"] == parts[1] for a in _ALERT_REGISTRY)
                    ):
                        alert_def = next(a for a in _ALERT_REGISTRY if a["key"] == parts[1])
                        enabled = parts[2].lower() == "on"
                        sub_prefs[alert_def["pref_key"]] = enabled
                        saved = save_user_prefs(sub_uid, sub_prefs)
                        if saved:
                            status = "✅ enabled" if enabled else "❌ disabled"
                            tg_reply(chat_id,
                                f"⚙️ {alert_def['label']} {status}.\n"
                                "You can change this any time with <code>/settings</code>.")
                            log.info(f"settings: user_id={sub_uid} {alert_def['pref_key']}={enabled}")
                        else:
                            tg_reply(chat_id,
                                "❌ Couldn't save your preference. Please try again later.")
                            log.warning(f"settings: save_user_prefs failed for user_id={sub_uid}")
                    elif (
                        len(parts) >= 4
                        and parts[1] == "tcs_structures"
                        and parts[-1].lower() in ("on", "off")
                    ):
                        from backend import (
                            load_tcs_alert_structures,
                            save_tcs_alert_structures,
                            WK_DISPLAY,
                            WK_DISPLAY_PLAIN,
                        )
                        requested_keys = [p.lower() for p in parts[2:-1]]
                        enable = parts[-1].lower() == "on"
                        if requested_keys == ["all"]:
                            all_keys = list(WK_DISPLAY.keys())
                            new_set = set(all_keys) if enable else set()
                            saved = save_tcs_alert_structures(new_set)
                            if saved:
                                if "tcs_alert_structures" in sub_prefs:
                                    sub_prefs.pop("tcs_alert_structures")
                                    save_user_prefs(sub_uid, sub_prefs)
                                icon = "✅" if enable else "❌"
                                action = "enabled" if enable else "disabled"
                                tg_reply(chat_id,
                                    f"{icon} All structures {action}.\n"
                                    "Use <code>/settings</code> to review all preferences.")
                                log.info(
                                    f"settings: tcs_structures all={'on' if enable else 'off'}"
                                )
                            else:
                                tg_reply(chat_id,
                                    "❌ Couldn't save your change. Please try again later.")
                                log.warning("settings: save_tcs_alert_structures failed for all")
                        else:
                            unknown_keys = [k for k in requested_keys if k not in WK_DISPLAY]
                            valid_requested = [k for k in requested_keys if k in WK_DISPLAY]
                            if unknown_keys:
                                valid_keys_list = sorted(WK_DISPLAY.keys())
                                valid_keys = ", ".join(
                                    f"<code>{k}</code>" for k in valid_keys_list
                                )
                                unknown_parts = []
                                for uk in unknown_keys:
                                    hint = _did_you_mean(uk, valid_keys_list, WK_DISPLAY_PLAIN)
                                    part = f"<code>{uk}</code>"
                                    if hint:
                                        part += f" — {hint}"
                                    unknown_parts.append(part)
                                unknown_fmt = ", ".join(unknown_parts)
                                tg_reply(chat_id,
                                    f"⚠️ Unknown structure(s): {unknown_fmt}. No changes were made.\n"
                                    f"Valid keys: {valid_keys}")
                            elif valid_requested:
                                current = load_tcs_alert_structures()
                                if current is None:
                                    current = set(WK_DISPLAY.keys())
                                for struct_key in valid_requested:
                                    if enable:
                                        current.add(struct_key)
                                    else:
                                        current.discard(struct_key)
                                saved = save_tcs_alert_structures(current)
                                if saved:
                                    # Clear any per-user override so the /settings display
                                    # reads from the app-level config we just wrote.
                                    if "tcs_alert_structures" in sub_prefs:
                                        sub_prefs.pop("tcs_alert_structures")
                                        save_user_prefs(sub_uid, sub_prefs)
                                    icon = "✅" if enable else "❌"
                                    action = "added to" if enable else "removed from"
                                    if len(valid_requested) == 1:
                                        struct_label = WK_DISPLAY_PLAIN.get(valid_requested[0], valid_requested[0])
                                        changed_summary = f"<b>{struct_label}</b> {action} TCS watching list."
                                    else:
                                        labels = ", ".join(
                                            f"<b>{WK_DISPLAY_PLAIN.get(k, k)}</b>"
                                            for k in valid_requested
                                        )
                                        changed_summary = f"{labels} — all {action} TCS watching list."
                                    if current:
                                        now_watching = ", ".join(
                                            WK_DISPLAY_PLAIN.get(k, k) for k in sorted(current)
                                        )
                                    else:
                                        now_watching = "none (all alerts silenced)"
                                    tg_reply(chat_id,
                                        f"{icon} {changed_summary}\n"
                                        f"Now watching: {now_watching}\n"
                                        "Use <code>/settings</code> to review all preferences.")
                                    log.info(
                                        f"settings: tcs_structures "
                                        f"{','.join(valid_requested)}="
                                        f"{'on' if enable else 'off'}"
                                    )
                            else:
                                tg_reply(chat_id,
                                    "❌ Couldn't save your change. Please try again later.")
                                log.warning(
                                    f"settings: save_tcs_alert_structures failed for "
                                    f"{','.join(valid_requested)}"
                                )
                    elif (
                        len(parts) == 3
                        and parts[1] == "tcs_structures"
                        and parts[2].lower() == "reset"
                    ):
                        from backend import (
                            save_tcs_alert_structures,
                            WK_DISPLAY,
                            WK_DISPLAY_PLAIN,
                        )
                        all_structs = set(WK_DISPLAY.keys())
                        saved = save_tcs_alert_structures(all_structs)
                        if saved:
                            if "tcs_alert_structures" in sub_prefs:
                                sub_prefs.pop("tcs_alert_structures")
                                save_user_prefs(sub_uid, sub_prefs)
                            all_names = ", ".join(
                                WK_DISPLAY_PLAIN.get(k, k) for k in sorted(all_structs)
                            )
                            tg_reply(chat_id,
                                "✅ TCS watching list reset to all structures.\n"
                                f"Now watching: {all_names}\n"
                                "Use <code>/settings</code> to review all preferences.")
                            log.info("settings: tcs_structures reset to all")
                        else:
                            tg_reply(chat_id,
                                "❌ Couldn't save your change. Please try again later.")
                            log.warning("settings: save_tcs_alert_structures failed on reset")
                    else:
                        tg_reply(chat_id,
                            "⚠️ Unknown setting.\n"
                            "Use <code>/settings</code> to see all available preferences and toggle commands.")
                    continue

                parsed = _parse_log_command(text)
                if parsed is None:
                    tg_reply(chat_id,
                        "⚠️ Bad format. Use:\n"
                        "<code>/log TICKER win|loss entry exit [note]</code>\n"
                        "Example: <code>/log MIGI win 1.94 2.85 broke above VWAP</code>")
                    continue

                ticker, win_loss, entry_price, exit_price, notes = parsed
                result = save_telegram_trade(
                    ticker=ticker,
                    win_loss=win_loss,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    notes=notes,
                    user_id=USER_ID,
                )

                if result.get("duplicate"):
                    tg_reply(chat_id,
                        f"⚠️ <b>Duplicate skipped</b> — {ticker} {entry_price}→{exit_price} "
                        f"already in journal.")
                elif result.get("error"):
                    tg_reply(chat_id, f"❌ Save failed: {result['error']}")
                else:
                    pnl   = result["pnl_pct"]
                    emoji = "🟢" if win_loss == "Win" else "🔴"
                    sign  = "+" if pnl >= 0 else ""
                    reply = (
                        f"📝 <b>Logged:</b> {ticker} | {emoji} {win_loss.upper()} | "
                        f"${entry_price} → ${exit_price} | {sign}{pnl:.1f}%"
                    )
                    if notes:
                        reply += f"\n💬 {notes}"
                    tg_reply(chat_id, reply)
                    log.info(f"Telegram log: {ticker} {win_loss} {entry_price}→{exit_price} "
                             f"({sign}{pnl:.1f}%) note='{notes}'")

        except Exception as exc:
            log.warning(f"Telegram listener error: {exc}")
            time.sleep(10)


def _subscriber_direction(predicted: str) -> str:
    """Map predicted structure to a clean direction label for beta subscribers."""
    p = (predicted or "").lower()
    if "bullish" in p or ("trend" in p and ("up" in p or "bull" in p)):
        return "📈 Bullish"
    if "bearish" in p or ("trend" in p and ("down" in p or "bear" in p)):
        return "📉 Bearish"
    return "◾ Watch"


def _broadcast_to_subscribers(message: str) -> int:
    """Send message to all beta subscribers. Returns count sent."""
    try:
        pairs = get_beta_chat_ids(exclude_user_id=USER_ID)
    except Exception as exc:
        log.warning(f"get_beta_chat_ids failed: {exc}")
        return 0
    sent = 0
    for _uid, cid in pairs:
        try:
            tg_reply(cid, message)
            sent += 1
            time.sleep(0.1)
        except Exception as exc:
            log.warning(f"broadcast to {cid} failed: {exc}")
    if sent:
        log.info(f"Broadcast sent to {sent} subscriber(s)")
    return sent


# ── Human-readable labels for each weight key in tcs_thresholds.json ──────────
# Keep in sync with WK_DISPLAY in backend.py
_WEIGHT_KEY_DISPLAY = {
    "trend_bull":     "📈 Trend Bull",
    "trend_bear":     "📉 Trend Bear",
    "double_dist":    "🔀 Double Dist",
    "non_trend":      "➡️ Non-Trend",
    "normal":         "🔔 Normal",
    "neutral":        "⚖️ Neutral",
    "ntrl_extreme":   "⚡ Ntrl Extreme",
    "nrml_variation": "〰️ Nrml Variation",
}


def _structure_emoji(predicted: str) -> str:
    p = (predicted or "").lower()
    if "bullish break" in p or ("trend" in p and ("up" in p or "bull" in p)):
        return "🟢"
    if "bearish break" in p or ("trend" in p and ("down" in p or "bear" in p)):
        return "🔴"
    if "double" in p:
        return "🟡"
    if "neutral" in p or "ntrl" in p:
        return "🔵"
    if "normal" in p or "nrml" in p:
        return "⚪"
    return "⚫"


def _alert_setup(r: dict, trade_date: date, context: dict | None = None):
    """Send a Telegram alert for a single qualifying setup.

    context (optional): {prev_high, prev_low} from a batch prev-day fetch.
    When provided a '📡 Context:' section is appended with S/R distance,
    RVOL, and gap data so the recipient has actionable edge info at a glance.
    """
    now_et    = datetime.now(EASTERN)
    scan_time = now_et.strftime("%I:%M %p ET").lstrip("0")

    ticker    = r.get("ticker", "?")
    tcs       = float(r.get("tcs", 0))
    predicted = r.get("predicted", "Unknown")
    conf      = float(r.get("confidence", 0))
    ib_low    = float(r.get("ib_low", 0))
    ib_high   = float(r.get("ib_high", 0))
    open_px   = float(r.get("open_price", 0))
    # close_price = last bar fetched = price at IB close ≈ current price at alert time
    cur_px    = float(r.get("close_price") or ib_high)
    emoji     = _structure_emoji(predicted)

    # Price move from open to IB close
    chg_pct   = ((cur_px - open_px) / open_px * 100) if open_px else 0
    chg_arrow = "▲" if chg_pct >= 0 else "▼"

    # Key entry levels
    ib_mid   = round((ib_high + ib_low) / 2, 2)
    above_ib = round(ib_high * 1.005, 2)
    below_ib = round(ib_low  * 0.995, 2)

    # IB range % quality filter display
    ib_range_pct = ((ib_high - ib_low) / open_px * 100) if open_px > 0 else None
    _ib_disp_threshold = load_ib_range_pct_threshold()
    if ib_range_pct is not None:
        ib_pct_icon = "✅" if ib_range_pct < _ib_disp_threshold else "⚠️"
        ib_pct_str  = f"  ·  <b>{ib_range_pct:.1f}%</b> of price {ib_pct_icon}"
    else:
        ib_pct_str  = ""

    # VWAP alignment quality filter display
    # Use close_price specifically (same basis as the order gate), not the cur_px
    # fallback that substitutes ib_high when close_price is missing.
    vwap_at_ib = float(r.get("vwap_at_ib") or 0)
    close_px   = float(r.get("close_price") or 0)
    vwap_line  = ""
    if vwap_at_ib > 0:
        _pl = predicted.lower()
        if "bullish break" in _pl and close_px > 0:
            _vwap_aligned = close_px >= vwap_at_ib
            _vwap_side    = "above" if close_px >= vwap_at_ib else "below"
            _vwap_icon    = "✅" if _vwap_aligned else "⛔"
            vwap_line = f"\n📐 VWAP at IB: <b>${vwap_at_ib:.2f}</b> — close {_vwap_side} {_vwap_icon}"
        elif "bearish break" in _pl and close_px > 0:
            _vwap_aligned = close_px <= vwap_at_ib
            _vwap_side    = "below" if close_px <= vwap_at_ib else "above"
            _vwap_icon    = "✅" if _vwap_aligned else "⛔"
            vwap_line = f"\n📐 VWAP at IB: <b>${vwap_at_ib:.2f}</b> — close {_vwap_side} {_vwap_icon}"
        else:
            vwap_line = f"\n📐 VWAP at IB: <b>${vwap_at_ib:.2f}</b> — n/a for non-directional"

    # Entry logic hint based on structure
    p_lower = predicted.lower()
    if "bullish break" in p_lower or ("trend" in p_lower and ("up" in p_lower or "bull" in p_lower)):
        entry_hint = f"🎯 <b>LONG</b> above IB high ${above_ib:.2f} | Target: IB extension"
    elif "bearish break" in p_lower or ("trend" in p_lower and ("down" in p_lower or "bear" in p_lower)):
        entry_hint = f"🎯 <b>SHORT</b> below IB low ${below_ib:.2f} | Target: IB extension"
    elif "double" in p_lower:
        entry_hint = f"🎯 Watch <b>both sides</b> — double distribution. Fade false breaks."
    elif "ntrl extreme" in p_lower or "ntrl_extreme" in p_lower:
        entry_hint = f"🎯 <b>Mean revert</b> to IB mid ${ib_mid:.2f} | Fade extremes"
    elif "neutral" in p_lower:
        entry_hint = f"🎯 <b>Range trade</b> — IB ${ib_low:.2f}–${ib_high:.2f} | Fade both ends"
    else:
        entry_hint = f"🎯 Watch IB range ${ib_low:.2f}–${ib_high:.2f} for directional break"

    priority_line = ""
    if r.get("_priority_tier"):
        priority_line = f"<b>{r['_priority_tier']}</b>\n"

    # Per-structure TCS threshold line
    struct_floor = int(r.get("_struct_tcs_floor") or 0)
    if struct_floor:
        _wk = label_to_weight_key(predicted) if predicted else ""
        _display_name = _WEIGHT_KEY_DISPLAY.get(_wk, predicted or "structure")
        tcs_line = f"⚡ TCS Score: <b>{tcs:.0f} ≥ {struct_floor}</b> ({_display_name} threshold) ✅"
    else:
        tcs_line = f"⚡ TCS Score: <b>{tcs:.0f} / 100</b>"

    # ── Context block (S/R, RVOL, gap) ────────────────────────────────────────
    context_lines = ""
    try:
        _rvol    = r.get("rvol")
        _gap_pct = r.get("gap_pct")
        _rvol_str = f"{float(_rvol):.1f}x" if _rvol not in (None, "") else "—"
        _gap_str  = f"{float(_gap_pct):+.1f}%" if _gap_pct not in (None, "") else "—"

        _ctx_parts = [f"📈 RVOL: <b>{_rvol_str}</b>  ·  Gap: <b>{_gap_str}</b>"]

        if context:
            _ph = context.get("high")
            _pl = context.get("low")
            if _ph and _pl:
                _ctx_parts.append(f"🏗 Prev Day: H <b>${_ph:.2f}</b>  /  L <b>${_pl:.2f}</b>")
            if _ph and cur_px:
                _r_pct = (_ph - cur_px) / cur_px * 100
                _s_pct = (cur_px - _pl) / cur_px * 100 if _pl else None
                _r_str = f"${_ph:.2f} ({_r_pct:+.1f}%)" if _r_pct > 0 else f"${_ph:.2f} (below — broken out)"
                _s_str = f"${_pl:.2f} (-{_s_pct:.1f}%)" if (_pl and _s_pct and _s_pct > 0) else (f"${_pl:.2f}" if _pl else "—")
                _ctx_parts.append(f"🎯 R: <b>{_r_str}</b>  ·  S: <b>{_s_str}</b>")

        if _ctx_parts:
            context_lines = "\n━━━━━━━━━━━━━━━━━━━━━\n📡 Context:\n  " + "\n  ".join(_ctx_parts)
    except Exception:
        context_lines = ""

    msg = (
        f"{emoji} <b>EdgeIQ Setup — {ticker}</b>\n"
        f"⏰ {scan_time}  ·  📅 {trade_date}\n"
        f"{priority_line}"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price at IB close: <b>${cur_px:.2f}</b>  "
        f"({chg_arrow}{abs(chg_pct):.1f}% from open ${open_px:.2f})\n"
        f"📊 Structure: <b>{predicted}</b>  ({conf:.0f}% conf)\n"
        f"{tcs_line}\n"
        f"📦 IB Range:  ${ib_low:.2f} – ${ib_high:.2f}  (mid ${ib_mid:.2f}){ib_pct_str}"
        f"{vwap_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{entry_hint}\n"
        f"🔑 Key levels:\n"
        f"  Break above → ${above_ib:.2f}\n"
        f"  IB Mid      → ${ib_mid:.2f}\n"
        f"  Break below → ${below_ib:.2f}"
        f"{context_lines}"
    )
    sent = tg_send(msg)
    if sent:
        log.info(f"  📱 Telegram alert sent: {ticker}")
    return sent


def _build_threshold_legend(tcs_thresholds: dict, tcs_threshold: int) -> str:
    """Return the per-structure threshold legend line (or empty string if no thresholds).

    Sorted by effective required TCS ascending so the tightest bar appears first.
    Uses max(calibrated, regime_floor) so the legend matches the actual qualification bar.
    """
    if not tcs_thresholds:
        return ""
    _parts = []
    for _wk, _cal_floor in sorted(tcs_thresholds.items(), key=lambda x: x[1]):
        _label = _WEIGHT_KEY_DISPLAY.get(_wk, _wk)
        _effective_floor = max(_cal_floor, tcs_threshold)
        _parts.append(f"{_label} requires TCS {_effective_floor}")
    if not _parts:
        return ""
    return "\n📐 " + " · ".join(_parts)


def _count_filter_blocks(qualified: list, ib_pct_threshold: float = 10.0) -> dict:
    """Count how many qualified setups are blocked by quality filters.

    Returns dict with keys:
      ib_wide          — IB range >= ib_pct_threshold % of open price
      vwap_misaligned  — close price is on the wrong side of VWAP at IB close
    """
    ib_wide = 0
    vwap_misaligned = 0
    for r in qualified:
        open_px  = float(r.get("open_price") or 0)
        ib_high  = float(r.get("ib_high")    or 0)
        ib_low   = float(r.get("ib_low")     or 0)
        if open_px > 0 and ib_high > ib_low:
            ib_range_pct = (ib_high - ib_low) / open_px * 100
            if ib_range_pct >= ib_pct_threshold:
                ib_wide += 1
                continue  # no point checking VWAP if already blocked

        vwap_val  = float(r.get("vwap_at_ib")   or 0)
        close_val = float(r.get("close_price")   or 0)
        direction = r.get("predicted", "")
        if vwap_val > 0 and close_val > 0 and direction in ("Bullish Break", "Bearish Break"):
            aligned = (
                (direction == "Bullish Break" and close_val >= vwap_val) or
                (direction == "Bearish Break" and close_val <= vwap_val)
            )
            if not aligned:
                vwap_misaligned += 1

    return {"ib_wide": ib_wide, "vwap_misaligned": vwap_misaligned}


def _alert_morning_summary(
    qualified: list, total_scanned: int, trade_date: date,
    effective_tcs: int = None, tcs_thresholds: dict = None,
    filter_blocks: dict = None, ib_range_pct_threshold: float = None,
):
    """Send a summary header before individual setup alerts."""
    tcs_threshold = effective_tcs if effective_tcs is not None else MIN_TCS

    # Load macro regime for context line
    _regime_line = ""
    try:
        _rg = get_breadth_regime(user_id=USER_ID)
        if _rg and _rg.get("regime_tag", "unknown") != "unknown":
            _mode_map = {"home_run": "🔥 Home Run", "singles": "🟡 Singles", "caution": "❄️ Caution"}
            _mode_str = _mode_map.get(_rg.get("mode", ""), "")
            _adj = _rg.get("tcs_floor_adj", 0)
            _adj_str = f" (TCS adj {_adj:+d})" if _adj != 0 else ""
            _regime_line = f"\n🌡️ Tape: {_rg['label']} · {_mode_str}{_adj_str}"
    except Exception:
        pass

    _threshold_legend = _build_threshold_legend(tcs_thresholds, tcs_threshold)

    # Build IB range filter line
    _ib_filter_line = ""
    if ib_range_pct_threshold is not None:
        _ib_filter_line = f"\n📐 IB filter: < {ib_range_pct_threshold:.1f}% of open price"

    # Build filter summary line
    _fb = filter_blocks or {}
    _ib_wide         = _fb.get("ib_wide", 0)
    _vwap_misaligned = _fb.get("vwap_misaligned", 0)
    if _ib_wide or _vwap_misaligned:
        _filter_parts = []
        if _ib_wide:
            _filter_parts.append(f"{_ib_wide} IB-wide")
        if _vwap_misaligned:
            _filter_parts.append(f"{_vwap_misaligned} VWAP-misaligned")
        _filter_line = "\n🔍 Filters blocked: " + ", ".join(_filter_parts)
    else:
        _filter_line = "\n🔍 Filters: all passed ✅" if qualified else ""

    if not qualified:
        tg_send(
            f"🔍 <b>EdgeIQ Morning Scan — {trade_date}</b>\n"
            f"No setups met per-structure TCS thresholds today out of {total_scanned} scanned.\n"
            f"Watching for intraday opportunities..."
            + _threshold_legend
            + _regime_line
            + _ib_filter_line
            + _filter_line
        )
        return
    tg_send(
        f"🔔 <b>EdgeIQ Morning Scan — {trade_date}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <b>{len(qualified)} setup(s)</b> qualified (per-structure thresholds)\n"
        f"📋 Scanned {total_scanned} tickers from your Finviz watchlist"
        + _threshold_legend
        + _regime_line
        + _ib_filter_line
        + _filter_line
        + "\nSending individual alerts now..."
    )


def _alert_eod_summary(
    results: list, updated: int, trade_date: date,
    global_filtered: int = 0, struct_filtered: int = 0,
):
    """Send EOD outcome summary."""
    wins   = [r for r in results if r.get("win_loss") == "Win"]
    losses = [r for r in results if r.get("win_loss") == "Loss"]
    best   = max(results, key=lambda r: float(r.get("aft_move_pct", 0)), default=None)
    _ib_threshold = load_ib_range_pct_threshold()

    lines = [
        f"📈 <b>EdgeIQ EOD Summary — {trade_date}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"✅ Wins: {len(wins)}   ❌ Losses: {len(losses)}   📋 Updated: {updated}",
        f"📐 IB filter: < {_ib_threshold:.1f}% of open price",
    ]
    if best and best.get("aft_move_pct"):
        lines.append(
            f"🏆 Best mover: <b>{best['ticker']}</b> "
            f"{float(best['aft_move_pct']):+.1f}% ({best.get('win_loss','?')})"
        )
    if wins or losses:
        wr = round(100 * len(wins) / max(1, len(wins) + len(losses)), 1)
        lines.append(f"📊 Today's structure win rate: <b>{wr}%</b>")
    # Show per-structure vs global filter breakdown
    if global_filtered or struct_filtered:
        filter_parts = []
        if struct_filtered:
            filter_parts.append(f"{struct_filtered} filtered by structure threshold")
        if global_filtered:
            filter_parts.append(f"{global_filtered} below global floor")
        lines.append(f"🚫 Filtered: " + " · ".join(filter_parts))

    # Health check: flag any today's paper_trades rows missing screener_pass
    try:
        if _supabase_client:
            _null_sp = (
                _supabase_client.table("paper_trades")
                .select("id", count="exact")
                .eq("trade_date", str(trade_date))
                .is_("screener_pass", "null")
                .execute()
            ).count or 0
            if _null_sp > 0:
                lines.append(
                    f"⚠️ <b>screener_pass NULL</b>: {_null_sp} trade(s) today "
                    f"missing pass tag — run backfill_screener_pass.py --table paper_trades"
                )
    except Exception as _hce:
        log.warning(f"[screener_pass] EOD health check failed: {_hce}")

    tg_send("\n".join(lines))


def _alert_tcs_threshold_changes(old: dict, new: dict, min_delta: int = 3) -> None:
    """Fire a Telegram alert if any structure's TCS threshold shifted by ≥ min_delta pts."""
    changes = []
    for k, new_val in new.items():
        old_val = old.get(k)
        if old_val is None:
            continue
        if abs(new_val - old_val) >= min_delta:
            label = WK_DISPLAY.get(k, k)
            changes.append(f"{label} {old_val}→{new_val}")
    if changes:
        tg_send("🔧 <b>Threshold update:</b> " + " · ".join(changes))


def _alert_recalibration(cal: dict):
    """Send brain recalibration summary."""
    deltas = cal.get("deltas", [])
    if not deltas:
        tg_send(
            "🧠 <b>Brain Recalibration</b>\n"
            "Not enough data yet (need ≥5 samples per structure). Weights unchanged."
        )
        return
    lines = ["🧠 <b>Brain Recalibration Complete</b>", "━━━━━━━━━━━━━━━━━━━━━"]
    for d in deltas:
        arrow = "▲" if d["delta"] > 0 else "▼"
        lines.append(
            f"  {d['key']}: {d['old']:.3f} → <b>{d['new']:.3f}</b> "
            f"({arrow}{abs(d['delta']):.3f}) | {d.get('blended_acc','?')}% / "
            f"{(d.get('journal_n') or 0) + (d.get('bot_n') or 0)} trades"
        )
    tg_send("\n".join(lines))


# ── Ticker resolution ─────────────────────────────────────────────────────────
def _resolve_tickers() -> list:
    env_override = os.getenv("PAPER_TRADE_TICKERS", "").strip()
    if env_override:
        tickers = [t.strip().upper() for t in env_override.split(",") if t.strip()]
        log.info(f"Tickers from PAPER_TRADE_TICKERS env var: {len(tickers)}")
        return tickers

    try:
        wl = load_watchlist(user_id=USER_ID)
        if wl:
            tickers = [t.strip().upper() for t in wl if t.strip()]
            log.info(f"Tickers from Supabase watchlist: {len(tickers)} → {', '.join(tickers)}")
            return tickers
        else:
            log.warning("Supabase watchlist is empty — falling back to default 14 tickers")
    except Exception as exc:
        log.warning(f"Could not load Supabase watchlist ({exc}) — falling back to default 14 tickers")

    tickers = [t.strip().upper() for t in _DEFAULT_TICKERS.split(",") if t.strip()]
    log.info(f"Using default fallback tickers: {len(tickers)}")
    return tickers


# Initialize with safe defaults at import time — no Supabase call on startup.
# watchlist_refresh() at 9:15 AM will fetch the live list from Supabase/Finviz
# and overwrite this. If watchlist_refresh() fails, the bot falls back here.
TICKERS = [t.strip().upper() for t in _DEFAULT_TICKERS.split(",") if t.strip()]


def _market_is_open(now_et: datetime) -> bool:
    if now_et.weekday() >= 5:
        return False
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now_et <= market_close


# ── Scheduled jobs ────────────────────────────────────────────────────────────
def premarket_scan():
    """9:10 AM ET — scan Alpaca SIP for large pre-market gappers.

    Catches dormant stocks with sudden catalysts that Finviz misses
    (no historical avg-vol requirement, price range up to $50).
    Results are MERGED into the watchlist — the 9:35 AM Finviz refresh
    then adds its own tickers on top.
    """
    global TICKERS
    log.info("=" * 60)
    log.info("PRE-MARKET SCAN — fetching large gappers via SIP")
    log.info("=" * 60)
    try:
        gappers, err = fetch_premarket_gappers(
            api_key     = ALPACA_API_KEY,
            secret_key  = ALPACA_SECRET_KEY,
            min_gap_pct = 15.0,
            price_min   = 1.0,
            price_max   = 50.0,
            min_pm_vol  = 100_000,
            top         = 100,
        )
        if err:
            log.warning(f"Pre-market scan warning: {err}")

        if not gappers:
            log.info("Pre-market scan: no large gappers found (market may not be open yet or no big movers today)")
            return

        new_tickers = [g["ticker"] for g in gappers]
        log.info(f"Pre-market gappers found: {len(new_tickers)} — {', '.join(new_tickers)}")
        for g in gappers:
            log.info(f"  {g['ticker']:6s}  ${g['price']:.2f}  {g['gap_pct']:+.1f}%  vol:{g['pm_vol']:,}")

        # Merge into current watchlist (deduplicated)
        existing = list(TICKERS)
        merged   = existing + [t for t in new_tickers if t not in existing]
        saved    = save_watchlist(merged, user_id=USER_ID)
        if saved:
            TICKERS = merged
            log.info(f"Watchlist updated: {len(merged)} total tickers (added {len(new_tickers)} pre-market gappers)")

        # Telegram alert
        lines = [f"<b>Pre-Market Gappers — {date.today()}</b>"]
        lines.append(f"SIP scan: {len(gappers)} stocks gapping ≥15% with PM vol ≥100K\n")
        for g in gappers[:8]:
            lines.append(f"  <b>{g['ticker']}</b>  ${g['price']:.2f}  {g['gap_pct']:+.1f}%  vol:{g['pm_vol']:,}")
        if len(gappers) > 8:
            lines.append(f"  ...and {len(gappers)-8} more")
        lines.append("\nWatchlist refresh at 9:35 AM ET...")
        tg_send("\n".join(lines))

    except Exception as exc:
        log.warning(f"Pre-market scan failed (non-fatal): {exc}")


def watchlist_refresh(midday: bool = False):
    """9:35 AM ET (or 11:45 AM midday) — pull today's movers from Finviz, save to Supabase.

    Runs THREE screener passes and merges them:
      Pass 1 — Gap-of-day plays: ≥3% change · Float ≤100M · $1–$20
               Catches high-momentum small-float catalysts.
      Pass 2 — Trend continuation plays: ≥1% change · Float ≤500M · $5–$50
               Above 20-day AND 50-day SMA · Avg vol ≥2M
               Catches institutional-quality stocks extending multi-week trends.
               These produce cleaner Bullish/Bearish Break IB structure vs
               gap-and-stall small-floats that tend to read as Neutral/Ntrl Extreme.
      Pass 3 — Short squeeze candidates: Short float ≥15% · Float ≤50M · ≥1% chg
               High short interest + low float = covering pressure amplifies IB breaks.
               These are layered behind gap/trend fills; capped at 30 tickers.

    Gap plays take priority (listed first), then trend, then squeeze.
    Combined list is capped at 100 tickers.

    ``midday=True`` is the 11:45 AM pass.  Each pass has its own daily lock file
    in /tmp so bot restarts can't fire a duplicate Telegram notification.
    """
    import os as _os
    _wl_slot   = "midday" if midday else "morning"
    _wl_et_date = datetime.now(EASTERN).date()   # use ET date so lock rolls over at ET midnight
    _wl_lock   = f"/tmp/wl_{_wl_slot}_{_wl_et_date}.lock"
    if _os.path.exists(_wl_lock):
        log.info(f"[watchlist_refresh] {_wl_slot} already completed today (ET {_wl_et_date}) — skipping duplicate run")
        return

    global TICKERS
    log.info("=" * 60)
    log.info("WATCHLIST REFRESH — fetching from Finviz (gap + trend + squeeze passes)")
    log.info("=" * 60)
    try:
        # ── Pass 1: gap-of-day (existing behaviour) ───────────────────────────
        gap_tickers = fetch_finviz_watchlist(
            change_min_pct=3.0,
            float_max_m=100.0,
            price_min=PRICE_MIN,
            price_max=PRICE_MAX,
            avg_vol_min_k=1000,
            max_tickers=60,
        )
        log.info(f"Gap-of-day screener: {len(gap_tickers)} tickers")

        # ── Pass 2: trend continuation ────────────────────────────────────────
        # Stocks in established uptrends on elevated volume → cleaner IB structure
        # and more Bullish Break / Bearish Break outcomes vs gap-and-stall noise.
        trend_tickers = fetch_finviz_watchlist(
            change_min_pct=1.0,
            float_max_m=500.0,
            price_min=5.0,
            price_max=50.0,
            avg_vol_min_k=2000,
            max_tickers=60,
            extra_filters=["ta_sma20_pa", "ta_sma50_pa"],
        )
        log.info(f"Trend-continuation screener: {len(trend_tickers)} tickers")

        # ── Pass 3: short squeeze candidates ─────────────────────────────────
        # High short interest (≥15% float short) + low float → covering pressure
        # amplifies IB breakouts. When a heavily shorted stock clears IB high,
        # shorts are forced to cover into the move on top of buyer demand.
        squeeze_tickers = fetch_finviz_watchlist(
            change_min_pct=1.0,
            float_max_m=50.0,
            price_min=1.0,
            price_max=50.0,
            avg_vol_min_k=500,
            max_tickers=30,
            extra_filters=["sh_short_o15"],  # short float > 15%
        )
        log.info(f"Short-squeeze screener: {len(squeeze_tickers)} tickers")

        # ── Merge: gap → trend → squeeze (deduped), cap at 100 ───────────────
        merged: list[str] = list(gap_tickers)
        for t in trend_tickers:
            if t not in merged:
                merged.append(t)
        for t in squeeze_tickers:
            if t not in merged:
                merged.append(t)
        merged = merged[:100]

        # ── Tag each ticker with the pass that first claimed it ───────────────
        global _TICKER_SCREENER_PASS
        _TICKER_SCREENER_PASS.clear()
        for t in gap_tickers:
            _TICKER_SCREENER_PASS[t] = "gap"
        for t in trend_tickers:
            if t not in _TICKER_SCREENER_PASS:
                _TICKER_SCREENER_PASS[t] = "trend"
        for t in squeeze_tickers:
            if t not in _TICKER_SCREENER_PASS:
                _TICKER_SCREENER_PASS[t] = "squeeze"
        _save_screener_pass_cache()  # persist to disk — survives bot restarts

        if merged:
            saved = save_watchlist(merged, user_id=USER_ID)
            if saved:
                TICKERS = merged
                log.info(
                    f"Watchlist updated: {len(merged)} total tickers "
                    f"({len(gap_tickers)} gap · {len(trend_tickers)} trend · "
                    f"{len(squeeze_tickers)} squeeze) → "
                    f"{', '.join(merged)}"
                )
                _scan_note = "Midday refresh complete." if midday else "Morning scan at 10:47 AM ET..."
                tg_send(
                    f"📋 <b>Watchlist Refreshed — {date.today()}</b>\n"
                    f"<b>{len(merged)} tickers</b> ({len(gap_tickers)} gap-of-day · "
                    f"{len(trend_tickers)} trend · {len(squeeze_tickers)} squeeze)\n"
                    f"Gap: ≥3% chg · Float ≤100M · $1–$20\n"
                    f"Trend: ≥1% chg · Float ≤500M · $5–$50 · Above 20+50 SMA\n"
                    f"Squeeze: Short float ≥15% · Float ≤50M · ≥1% chg\n"
                    f"{_scan_note}"
                )
                # Persist scan results to daily_scan_log table for dashboard visibility
                try:
                    save_daily_scan_log(
                        gap_tickers=gap_tickers,
                        trend_tickers=trend_tickers,
                        squeeze_tickers=squeeze_tickers,
                        scan_date=_wl_et_date,
                        slot=_wl_slot,
                    )
                except Exception as _sl_err:
                    log.warning(f"[watchlist_refresh] save_daily_scan_log failed (non-fatal): {_sl_err}")
                # Mark this slot done — prevents duplicate TG if bot restarts today
                try:
                    open(_wl_lock, 'w').close()
                except Exception:
                    pass
            else:
                log.warning("Finviz returned tickers but Supabase save failed — keeping existing watchlist")
        else:
            log.warning("All Finviz screeners returned 0 tickers — keeping existing watchlist")
    except Exception as exc:
        log.warning(f"Watchlist refresh failed: {exc} — keeping existing watchlist")


def _run_scan(trade_date: date, cutoff_h: int = 10, cutoff_m: int = 30) -> list:
    """Fetch bars and run IB engine. Returns all results (unfiltered by TCS)."""
    # Always resolve tickers fresh at scan time so bot restarts after 9:15 AM
    # still pick up the full Supabase/Finviz watchlist, not just the startup defaults.
    scan_tickers = _resolve_tickers()
    log.info(
        f"Running scan for {trade_date} | cutoff {cutoff_h:02d}:{cutoff_m:02d} "
        f"| {len(scan_tickers)} tickers | feed: {FEED}"
    )
    results, summary = run_historical_backtest(
        ALPACA_API_KEY, ALPACA_SECRET_KEY,
        trade_date=trade_date,
        tickers=scan_tickers,
        feed=FEED,
        price_min=PRICE_MIN,
        price_max=PRICE_MAX,
        cutoff_hour=cutoff_h,
        cutoff_minute=cutoff_m,
        slippage_pct=0.0,
    )
    if summary.get("error"):
        log.warning(f"Scan error: {summary['error']}")
        return []
    log.info(
        f"Scan complete — {summary.get('total', 0)} setups | "
        f"win rate {summary.get('win_rate', 0)}% | avg TCS {summary.get('avg_tcs', 0)}"
    )
    return results


def _broadcast_morning_to_subscribers(results: list, today) -> None:
    """Send a clean morning setup list to beta subscribers who have not opted out."""
    if not results:
        return
    sorted_r = sorted(results, key=lambda x: float(x.get("tcs", 0)), reverse=True)
    top = sorted_r[:7]
    date_str = today.strftime("%b %-d") if hasattr(today, "strftime") else str(today)
    lines = [f"🔍 <b>Scanner — {date_str}</b>", "━━━━━━━━━━━━━━━━"]
    for r in top:
        ticker = r.get("ticker", "?")
        direction = _subscriber_direction(r.get("predicted", ""))
        lines.append(f"{direction} — <b>{ticker}</b>")
    remaining = len(results) - len(top)
    if remaining > 0:
        lines.append(f"+ {remaining} more on radar")
    lines.append("")
    lines.append("Log your best trade today via your portal.")
    message = "\n".join(lines)
    try:
        pairs = get_beta_chat_ids(exclude_user_id=USER_ID, morning_alerts_only=True)
    except Exception as exc:
        log.warning(f"get_beta_chat_ids failed in morning broadcast: {exc}")
        return
    sent = 0
    for _uid, cid in pairs:
        try:
            tg_reply(cid, message)
            sent += 1
            time.sleep(0.1)
        except Exception as exc:
            log.warning(f"morning broadcast send error chat_id={cid}: {exc}")
    log.info(f"morning broadcast sent to {sent} subscriber(s)")


def _broadcast_eod_to_subscribers(results: list, today) -> None:
    """Send a clean EOD outcome summary to opted-in beta subscribers."""
    if not results:
        return
    wins   = [r for r in results if (r.get("win_loss") or "").lower() == "win"]
    total  = len(results)
    date_str = today.strftime("%b %-d") if hasattr(today, "strftime") else str(today)
    top_movers = sorted(
        results,
        key=lambda x: abs(float(x.get("aft_move_pct") or x.get("follow_thru_pct") or 0)),
        reverse=True,
    )[:3]
    lines = [f"📊 <b>Results — {date_str}</b>", "━━━━━━━━━━━━━━━━",
             f"{len(wins)} of {total} setups played out today"]
    if top_movers:
        mover_parts = []
        for r in top_movers:
            pct = float(r.get("aft_move_pct") or r.get("follow_thru_pct") or 0)
            sign = "+" if pct >= 0 else ""
            mover_parts.append(f"{r.get('ticker','?')} {sign}{pct:.1f}%")
        lines.append("Top: " + " · ".join(mover_parts))
    message = "\n".join(lines)
    try:
        pairs = get_beta_chat_ids(exclude_user_id=USER_ID, eod_alerts_only=True)
    except Exception as exc:
        log.warning(f"get_beta_chat_ids failed in EOD broadcast: {exc}")
        return
    sent = 0
    for _uid, cid in pairs:
        try:
            tg_reply(cid, message)
            sent += 1
            time.sleep(0.1)
        except Exception as exc:
            log.warning(f"EOD broadcast send error chat_id={cid}: {exc}")
    log.info(f"EOD broadcast sent to {sent} subscriber(s)")


def morning_scan():
    """10:47 AM ET — log IB entries, send Telegram alerts per qualifying setup."""
    today = date.today()
    log.info("=" * 60)
    log.info("MORNING SCAN — logging IB entries + sending Telegram alerts")
    log.info("=" * 60)

    # ── Load macro regime — adjust TCS floor and tag every result ──────────
    regime = {}
    effective_min_tcs = MIN_TCS
    try:
        regime = get_breadth_regime(user_id=USER_ID) or {}
        tcs_adj = regime.get("tcs_floor_adj", 0)
        if tcs_adj:
            effective_min_tcs = max(30, MIN_TCS + tcs_adj)  # never go below 30
            log.info(
                f"Regime: {regime.get('label','?')} → TCS floor adjusted "
                f"{MIN_TCS} + ({tcs_adj:+d}) = {effective_min_tcs}"
            )
    except Exception as exc:
        log.warning(f"Could not load macro regime: {exc}")

    results = _run_scan(today, cutoff_h=10, cutoff_m=30)
    if not results:
        log.warning("No results from morning scan.")
        tg_send(f"⚠️ <b>Morning Scan Failed</b> — {today}\nNo bar data returned. Check Alpaca connection.")
        return

    # Tag every result with the current macro regime + scan type
    regime_tag = regime.get("regime_tag", "")
    for r in results:
        r["regime_tag"] = regime_tag
        r["sim_date"]   = str(today)  # ensure sim_date present for dedup
        r["scan_type"]  = "morning"

    # Log ALL scan results to paper_trades with regime_tag attached.
    # min_tcs_filter records the effective threshold for this session;
    # analytics can use it to distinguish qualified vs below-threshold rows.
    all_results_logged = log_paper_trades(results, user_id=USER_ID, min_tcs=effective_min_tcs)
    log.info(
        f"Session logged: {all_results_logged.get('saved', 0)} new | "
        f"{all_results_logged.get('skipped', 0)} skipped | regime: {regime_tag or 'none'}"
    )

    # Log context levels (S/R, VWAP, MACD) for adaptive exit analysis
    try:
        log_context_levels(results, str(today))
    except Exception as _ctx_err:
        log.warning(f"Context level logging failed (non-critical): {_ctx_err}")

    # Load per-structure calibrated TCS thresholds (from nightly recalibration)
    _tcs_thresholds = load_tcs_thresholds(default=MIN_TCS)
    qualified = [
        r for r in results
        if float(r.get("tcs", 0)) >= _struct_tcs_floor(r, _tcs_thresholds, effective_min_tcs)
    ]
    for r in qualified:
        r["_struct_tcs_floor"] = _struct_tcs_floor(r, _tcs_thresholds, effective_min_tcs)
    log.info(
        f"{len(qualified)} setups qualified (per-structure TCS, regime floor={effective_min_tcs}) "
        f"of {len(results)} scanned"
    )

    # Load active IB range % threshold (used for both filter counting and the Telegram message)
    _ib_pct_threshold = load_ib_range_pct_threshold()

    # Count how many qualified setups are blocked by quality filters
    _filter_blocks = _count_filter_blocks(qualified, ib_pct_threshold=_ib_pct_threshold)
    log.info(
        f"Filter blocks: {_filter_blocks['ib_wide']} IB-wide, "
        f"{_filter_blocks['vwap_misaligned']} VWAP-misaligned"
    )

    # Telegram: summary header
    _alert_morning_summary(
        qualified, len(results), today,
        effective_tcs=effective_min_tcs,
        tcs_thresholds=_tcs_thresholds,
        filter_blocks=_filter_blocks,
        ib_range_pct_threshold=_ib_pct_threshold,
    )

    if qualified:
        # Sort by tier priority: P3 first → P4.  Within tier: higher TCS first.
        qualified = sorted(qualified, key=_tier_priority_key)
        log.info(f"Sending {len(qualified)} Telegram setup alerts (P3 first, then P4)...")

        # Batch-fetch prev-day levels for S/R context in alerts (one call for all tickers)
        _alert_tickers = [r.get("ticker") for r in qualified if r.get("ticker")]
        try:
            _prev_day_map = _fetch_prev_day_bars(_alert_tickers, str(today))
        except Exception as _pde:
            log.warning(f"Prev-day context fetch failed (alerts will send without S/R): {_pde}")
            _prev_day_map = {}

        for r in qualified:
            tcs_val = float(r.get("tcs", 0))
            if tcs_val >= 70:
                r["_priority_tier"] = "🟡 P3 — Morning 70+  (avg +7.58R — HIGHEST PRIORITY)"
            elif tcs_val >= 50:
                r["_priority_tier"] = "🟢 P4 — Morning 50+  (avg +1.90R — core system)"
            else:
                r["_priority_tier"] = "⚪ Watch Only"
            log.info(
                f"  {r['ticker']:6s} | TCS {tcs_val:5.0f} | "
                f"predicted: {r.get('predicted', '—'):20s} | "
                f"IB {r.get('ib_low', 0):.2f}–{r.get('ib_high', 0):.2f}"
            )
            _ctx = _prev_day_map.get(r.get("ticker"), {})
            _alert_setup(r, today, context=_ctx if _ctx else None)
            _place_order_for_setup(r, "morning")
            time.sleep(0.3)  # Telegram rate limit buffer
    else:
        log.info("No setups met TCS threshold today.")

    if LIVE_ORDERS_ENABLED:
        acct = "PAPER" if IS_PAPER_ALPACA else "LIVE"
        log.info(f"Order placement complete ({acct} account, 1% account equity / trade, cap $2,000)")
    else:
        log.info("Order placement disabled (LIVE_ORDERS_ENABLED=false) — set to true to activate")

    # ── Beta subscriber broadcast (clean — no TCS/brain language) ─────────
    _broadcast_morning_to_subscribers(results, today)


def intraday_scan():
    """2:00 PM ET — re-scan for fresh setups that developed through midday."""
    today = date.today()
    log.info("=" * 60)
    log.info("INTRADAY SCAN — checking for midday setups")
    log.info("=" * 60)

    # ── Load macro regime (same as morning scan) ──────────────────────────────
    regime = {}
    effective_min_tcs = MIN_TCS
    try:
        regime = get_breadth_regime(user_id=USER_ID) or {}
        tcs_adj = regime.get("tcs_floor_adj", 0)
        if tcs_adj:
            effective_min_tcs = max(30, MIN_TCS + tcs_adj)
            log.info(
                f"Regime: {regime.get('label','?')} → TCS floor adjusted "
                f"{MIN_TCS} + ({tcs_adj:+d}) = {effective_min_tcs}"
            )
    except Exception as exc:
        log.warning(f"Could not load macro regime: {exc}")

    results = _run_scan(today, cutoff_h=13, cutoff_m=30)
    if not results:
        log.info("No intraday results.")
        return

    regime_tag = regime.get("regime_tag", "")
    for r in results:
        r["scan_type"]  = "intraday"
        r["regime_tag"] = regime_tag
        r["sim_date"]   = str(today)

    # ── Log ALL intraday results to paper_trades (dedup by ticker+date+scan_type)
    logged = log_paper_trades(results, user_id=USER_ID, min_tcs=effective_min_tcs)
    log.info(f"Intraday paper trades logged: {logged.get('saved',0)} new, {logged.get('skipped',0)} already existed")

    # Log context levels (S/R, VWAP, MACD) for adaptive exit analysis
    try:
        log_context_levels(results, str(today))
    except Exception as _ctx_err:
        log.warning(f"Context level logging failed (non-critical): {_ctx_err}")

    _tcs_thresholds = load_tcs_thresholds(default=MIN_TCS)
    qualified = [
        r for r in results
        if float(r.get("tcs", 0)) >= _struct_tcs_floor(r, _tcs_thresholds, effective_min_tcs)
    ]
    log.info(
        f"{len(qualified)} intraday setups qualified (per-structure TCS, regime floor={effective_min_tcs}) "
        f"of {len(results)} scanned"
    )

    if qualified:
        # Sort by tier priority: P1 (Intraday 70+) first → P2 (Intraday 50-69).
        qualified = sorted(qualified, key=_tier_priority_key)
        _threshold_legend = _build_threshold_legend(_tcs_thresholds, effective_min_tcs)
        tg_send(
            f"🔄 <b>Intraday Scan — {today} (2 PM)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>{len(qualified)} setup(s)</b> still active/developing:"
            + _threshold_legend
        )

        # Batch-fetch prev-day levels for S/R context in alerts (one call for all tickers)
        _alert_tickers = [r.get("ticker") for r in qualified if r.get("ticker")]
        try:
            _prev_day_map = _fetch_prev_day_bars(_alert_tickers, str(today))
        except Exception as _pde:
            log.warning(f"Prev-day context fetch failed (alerts will send without S/R): {_pde}")
            _prev_day_map = {}

        for r in qualified:
            r["_struct_tcs_floor"] = _struct_tcs_floor(r, _tcs_thresholds, effective_min_tcs)
            tcs_val = float(r.get("tcs", 0))
            if tcs_val >= 70:
                tier = "🔴 P1 — Intraday 70+  (avg +4.44R — ACT ON THIS)"
            elif tcs_val >= 50:
                tier = "🟠 P2 — Intraday 50-69  (avg +2.15R — core setup)"
            else:
                tier = "⚪ Watch Only"
            r["_priority_tier"] = tier
            _ctx = _prev_day_map.get(r.get("ticker"), {})
            _alert_setup(r, today, context=_ctx if _ctx else None)
            _place_order_for_setup(r, "intraday")
            time.sleep(0.3)
    else:
        log.info("No intraday setups above threshold.")


def _eod_collect_close_prices(lookback_days: int = PAPER_CLOSE_LOOKBACK_DAYS) -> dict:
    """Fetch and store EOD close prices for all paper_trades rows that still
    have NULL close_price, covering today's trades and any recent missed days.

    Called at the end of eod_update() so gaps self-heal automatically without
    manual backfill runs.  backfill_close_prices.py remains a one-time recovery
    tool for rows older than lookback_days.

    Strategy (mirrors backfill_close_prices.py):
    - Query all user's paper_trades with close_price IS NULL and trade_date
      within the past `lookback_days` calendar days.
    - Group rows by trade_date so we do one Alpaca call per date (batch of
      tickers) rather than one call per row.
    - Write results back to Supabase row-by-row.

    Returns {"written": N, "skipped": M}.
    """
    from collections import defaultdict
    from datetime import timedelta

    if not _supabase_client:
        log.warning("_eod_collect_close_prices: No Supabase connection — skipping.")
        return {"written": 0, "skipped": 0}
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        log.warning("_eod_collect_close_prices: Alpaca keys not set — skipping.")
        return {"written": 0, "skipped": 0}

    log.info(
        f"EOD close-price sweep: querying paper_trades with NULL close_price "
        f"(last {lookback_days} days, user {USER_ID})…"
    )

    cutoff = str(date.today() - timedelta(days=lookback_days))
    _PAGE = 1000
    rows: list = []
    offset = 0
    try:
        while True:
            resp = (
                _supabase_client.table("paper_trades")
                .select("id,ticker,trade_date")
                .eq("user_id", USER_ID)
                .is_("close_price", "null")
                .gte("trade_date", cutoff)
                # Newest dates first so today's rows are always prioritised
                # and processed even when the historical backlog is large.
                # Secondary order by id (desc) makes pagination deterministic
                # when many rows share the same trade_date.
                .order("trade_date", desc=True)
                .order("id", desc=True)
                .range(offset, offset + _PAGE - 1)
                .execute()
            )
            batch = resp.data or []
            rows.extend(batch)
            if len(batch) < _PAGE:
                break
            offset += _PAGE
    except Exception as _qe:
        log.warning(f"_eod_collect_close_prices: DB query failed: {_qe}")
        return {"written": 0, "skipped": 0}

    if not rows:
        log.info("No paper_trades rows with NULL close_price in the look-back window — nothing to collect.")
        return {"written": 0, "skipped": 0}

    # Group by date for efficient batched Alpaca calls
    by_date: dict[str, list] = defaultdict(list)
    for r in rows:
        td = (r.get("trade_date") or "")[:10]
        if td:
            by_date[td].append(r)

    log.info(
        f"  Found {len(rows)} row(s) across {len(by_date)} date(s) "
        f"with NULL close_price."
    )

    try:
        from backfill_close_prices import fetch_closes_for_date
    except Exception as _ie:
        log.warning(f"_eod_collect_close_prices: Cannot import fetch_closes_for_date: {_ie}")
        return {"written": 0, "skipped": 0}

    written = 0
    skipped = 0
    patched: list[str] = []  # "TICKER (date) @ price" entries for the success alert
    skipped_by_date: dict[str, list[str]] = {}  # date → [ticker, …] for the warning alert

    for trade_date_str in sorted(by_date.keys(), reverse=True):
        date_rows = by_date[trade_date_str]
        tickers = sorted({r["ticker"] for r in date_rows if r.get("ticker")})

        log.info(
            f"  {trade_date_str}: {len(tickers)} ticker(s) — "
            f"{', '.join(tickers[:SWEEP_ALERT_MAX_TICKERS])}{'…' if len(tickers) > SWEEP_ALERT_MAX_TICKERS else ''}"
        )

        try:
            closes = fetch_closes_for_date(trade_date_str, tickers)
        except Exception as _fe:
            log.warning(f"  Alpaca fetch failed for {trade_date_str}: {_fe}")
            skipped += len(date_rows)
            for row in date_rows:
                _t = row.get("ticker", "?")
                skipped_by_date.setdefault(trade_date_str, []).append(_t)
            continue

        for row in date_rows:
            ticker = row.get("ticker", "")
            cp = closes.get(ticker)
            if cp is None:
                log.debug(f"    No Alpaca data for {ticker} on {trade_date_str} — skipping.")
                skipped += 1
                skipped_by_date.setdefault(trade_date_str, []).append(ticker)
                continue
            try:
                _supabase_client.table("paper_trades").update(
                    {"close_price": round(float(cp), 4)}
                ).eq("id", row["id"]).execute()
                log.info(f"    {ticker}: close_price → {cp:.4f}")
                written += 1
                patched.append(f"{ticker} ({trade_date_str}) @ ${cp:.2f}")
            except Exception as _ue:
                log.warning(
                    f"    DB update failed for {ticker} (id={row['id']}): {_ue}"
                )
                skipped += 1
                skipped_by_date.setdefault(trade_date_str, []).append(ticker)

    log.info(
        f"EOD close-price sweep done — {written} written, "
        f"{skipped} skipped (no Alpaca data or update error)."
    )

    if written > 0:
        _display_entries = patched[:SWEEP_ALERT_MAX_TICKERS]
        _overflow = len(patched) - len(_display_entries)
        _ticker_lines = "\n".join(f"  • {entry}" for entry in _display_entries)
        if _overflow > 0:
            _ticker_lines += f"\n  …and {_overflow} more"
        _sweep_msg = (
            f"🔧 <b>Close-Price Sweep</b> — {written} row(s) backfilled\n"
            f"{_ticker_lines}\n"
            f"<i>Housekeeping: these close prices were missing after EOD and have been patched automatically.</i>"
        )
        try:
            tg_send(_sweep_msg)
        except Exception as _tge:
            log.warning(f"_eod_collect_close_prices: Telegram alert failed: {_tge}")

    if skipped > 0:
        _skipped_lines = "\n".join(
            f"  • {date}: {', '.join(sorted(set(tks)))}"
            for date, tks in sorted(skipped_by_date.items(), reverse=True)
        )
        _warn_msg = (
            f"⚠️ <b>Close-Price Sweep — {skipped} ticker(s) could not be filled</b>\n"
            f"{_skipped_lines}\n"
            f"<i>These close prices could not be filled automatically (no Alpaca data or update error). Manual investigation may be needed.</i>"
        )
        try:
            tg_send(_warn_msg)
        except Exception as _tge:
            log.warning(f"_eod_collect_close_prices: Telegram warning failed: {_tge}")

    return {"written": written, "skipped": skipped}


def _recalc_eod_pnl_r_recent(lookback_days: int | None = None) -> dict:
    """Compute eod_pnl_r for paper_trades rows that now have close_price but
    still have NULL eod_pnl_r.

    When ``lookback_days`` is None (the default), ALL historical rows are
    considered — this ensures that close prices backfilled via the manual Retry
    button or via _eod_collect_close_prices() for any date are always picked up
    by the nightly run.  Pass an integer to restrict the sweep to the most
    recent N calendar days (useful for ad-hoc targeted runs).

    Called immediately after _eod_collect_close_prices() during nightly
    recalibration so any rows whose close_price was just filled also get their
    EOD hold P&L computed without waiting for a manual run_sim_backfill.py run.

    Only processes confirmed breakout rows (actual_outcome in
    'Bullish Break' / 'Bearish Break') since the EOD P&L formula requires a
    directional break to have occurred.

    Returns {"written": N, "skipped": M}.
    """
    from datetime import timedelta

    if not _supabase_client:
        log.warning("_recalc_eod_pnl_r_recent: No Supabase connection — skipping.")
        return {"written": 0, "skipped": 0}

    try:
        from backend import compute_trade_sim_tiered as _compute_tiered
    except Exception as _ie:
        log.warning(f"_recalc_eod_pnl_r_recent: Cannot import compute_trade_sim_tiered: {_ie}")
        return {"written": 0, "skipped": 0}

    cutoff = (
        str(date.today() - timedelta(days=lookback_days))
        if lookback_days is not None
        else None
    )
    _PAGE = 1000
    rows: list = []
    offset = 0

    for direction in ("Bullish Break", "Bearish Break"):
        offset = 0
        while True:
            try:
                query = (
                    _supabase_client.table("paper_trades")
                    .select("id,actual_outcome,ib_high,ib_low,close_price")
                    .eq("user_id", USER_ID)
                    .eq("actual_outcome", direction)
                    .is_("eod_pnl_r", "null")
                    .not_.is_("close_price", "null")
                    .order("id", desc=True)
                    .range(offset, offset + _PAGE - 1)
                )
                if cutoff is not None:
                    query = query.gte("trade_date", cutoff)
                resp = query.execute()
            except Exception as _qe:
                log.warning(
                    f"_recalc_eod_pnl_r_recent: DB query failed ({direction}): {_qe}"
                )
                break
            batch = resp.data or []
            rows.extend(batch)
            if len(batch) < _PAGE:
                break
            offset += _PAGE

    scope_desc = f"last {lookback_days} days" if lookback_days is not None else "all time"
    if not rows:
        log.info(
            "EOD close-price catch-up: no paper_trades rows need eod_pnl_r "
            "recalculation (%s).", scope_desc
        )
        return {"written": 0, "skipped": 0}

    log.info(
        f"EOD eod_pnl_r recalc: {len(rows)} paper_trades row(s) have "
        f"close_price but NULL eod_pnl_r ({scope_desc}) — computing now…"
    )

    written = 0
    skipped = 0

    for row in rows:
        close_price = row.get("close_price")
        ib_high = row.get("ib_high")
        ib_low = row.get("ib_low")
        direction = (row.get("actual_outcome") or "").strip()
        row_id = row.get("id")

        if close_price is None or ib_high is None or ib_low is None:
            skipped += 1
            continue

        try:
            tiered = _compute_tiered(
                aft_df=None,
                ib_high=ib_high,
                ib_low=ib_low,
                direction=direction,
                close_px=close_price,
            )
        except Exception as _te:
            log.debug(f"  compute_trade_sim_tiered failed for id={row_id}: {_te}")
            skipped += 1
            continue

        eod_pnl_r = tiered.get("eod_pnl_r")
        if eod_pnl_r is None:
            skipped += 1
            continue

        try:
            _supabase_client.table("paper_trades").update(
                {"eod_pnl_r": round(float(eod_pnl_r), 6)}
            ).eq("id", row_id).execute()
            log.info(f"    id={row_id}: eod_pnl_r → {eod_pnl_r:.4f}R")
            written += 1
        except Exception as _ue:
            log.warning(f"    DB update failed for id={row_id}: {_ue}")
            skipped += 1

    log.info(
        f"EOD eod_pnl_r recalc done — {written} written, {skipped} skipped."
    )
    return {"written": written, "skipped": skipped}


def _eod_collect_close_prices_backtest(lookback_days: int = BACKTEST_CLOSE_LOOKBACK_DAYS) -> dict:
    """Fetch and store EOD close prices for backtest_sim_runs rows that still
    have NULL close_price, covering the past ``lookback_days`` calendar days.

    Mirrors _eod_collect_close_prices() but targets the backtest_sim_runs table
    (date field: sim_date).  Called during nightly_recalibration() so the
    backtest history self-heals without manual backfill_close_prices.py runs.

    Returns {"written": N, "skipped": M}.
    """
    from collections import defaultdict
    from datetime import timedelta

    if not _supabase_client:
        log.warning("_eod_collect_close_prices_backtest: No Supabase connection — skipping.")
        return {"written": 0, "skipped": 0}
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        log.warning("_eod_collect_close_prices_backtest: Alpaca keys not set — skipping.")
        return {"written": 0, "skipped": 0}

    log.info(
        f"EOD close-price sweep (backtest): querying backtest_sim_runs with NULL close_price "
        f"(last {lookback_days} days, user {USER_ID})…"
    )

    cutoff = str(date.today() - timedelta(days=lookback_days))
    _PAGE = 1000
    rows: list = []
    offset = 0
    try:
        while True:
            resp = (
                _supabase_client.table("backtest_sim_runs")
                .select("id,ticker,sim_date")
                .eq("user_id", USER_ID)
                .is_("close_price", "null")
                .gte("sim_date", cutoff)
                .order("sim_date", desc=True)
                .order("id", desc=True)
                .range(offset, offset + _PAGE - 1)
                .execute()
            )
            batch = resp.data or []
            rows.extend(batch)
            if len(batch) < _PAGE:
                break
            offset += _PAGE
    except Exception as _qe:
        log.warning(f"_eod_collect_close_prices_backtest: DB query failed: {_qe}")
        return {"written": 0, "skipped": 0}

    if not rows:
        log.info(
            "No backtest_sim_runs rows with NULL close_price in the look-back window — nothing to collect."
        )
        return {"written": 0, "skipped": 0}

    by_date: dict[str, list] = defaultdict(list)
    for r in rows:
        sd = (r.get("sim_date") or "")[:10]
        if sd:
            by_date[sd].append(r)

    log.info(
        f"  Found {len(rows)} backtest row(s) across {len(by_date)} date(s) "
        f"with NULL close_price."
    )

    try:
        from backfill_close_prices import fetch_closes_for_date
    except Exception as _ie:
        log.warning(f"_eod_collect_close_prices_backtest: Cannot import fetch_closes_for_date: {_ie}")
        return {"written": 0, "skipped": 0}

    written = 0
    skipped = 0
    patched: list[str] = []  # "TICKER (date) @ price" entries for the success alert
    skipped_by_date: dict[str, list[str]] = {}  # date → [ticker, …] for the warning alert

    for sim_date_str in sorted(by_date.keys(), reverse=True):
        date_rows = by_date[sim_date_str]
        tickers = sorted({r["ticker"] for r in date_rows if r.get("ticker")})

        log.info(
            f"  {sim_date_str}: {len(tickers)} ticker(s) — "
            f"{', '.join(tickers[:SWEEP_ALERT_MAX_TICKERS])}{'…' if len(tickers) > SWEEP_ALERT_MAX_TICKERS else ''}"
        )

        try:
            closes = fetch_closes_for_date(sim_date_str, tickers)
        except Exception as _fe:
            log.warning(f"  Alpaca fetch failed for {sim_date_str}: {_fe}")
            skipped += len(date_rows)
            for row in date_rows:
                _t = row.get("ticker", "?")
                skipped_by_date.setdefault(sim_date_str, []).append(_t)
            continue

        for row in date_rows:
            ticker = row.get("ticker", "")
            cp = closes.get(ticker)
            if cp is None:
                log.debug(f"    No Alpaca data for {ticker} on {sim_date_str} — skipping.")
                skipped += 1
                skipped_by_date.setdefault(sim_date_str, []).append(ticker)
                continue
            try:
                _supabase_client.table("backtest_sim_runs").update(
                    {"close_price": round(float(cp), 4)}
                ).eq("id", row["id"]).execute()
                log.info(f"    {ticker}: close_price → {cp:.4f}")
                written += 1
                patched.append(f"{ticker} ({sim_date_str}) @ ${cp:.2f}")
            except Exception as _ue:
                log.warning(
                    f"    DB update failed for {ticker} (id={row['id']}): {_ue}"
                )
                skipped += 1
                skipped_by_date.setdefault(sim_date_str, []).append(ticker)

    log.info(
        f"EOD close-price sweep (backtest) done — {written} written, "
        f"{skipped} skipped (no Alpaca data or update error)."
    )

    if written > 0:
        _display_entries = patched[:SWEEP_ALERT_MAX_TICKERS]
        _overflow = len(patched) - len(_display_entries)
        _ticker_lines = "\n".join(f"  • {entry}" for entry in _display_entries)
        if _overflow > 0:
            _ticker_lines += f"\n  …and {_overflow} more"
        _sweep_msg = (
            f"🔧 <b>Close-Price Sweep (Backtest)</b> — {written} row(s) backfilled\n"
            f"{_ticker_lines}\n"
            f"<i>Housekeeping: these backtest close prices were missing and have been patched automatically.</i>"
        )
        try:
            tg_send(_sweep_msg)
        except Exception as _tge:
            log.warning(f"_eod_collect_close_prices_backtest: Telegram alert failed: {_tge}")

    if skipped > 0:
        _skipped_lines = "\n".join(
            f"  • {date}: {', '.join(sorted(set(tks)))}"
            for date, tks in sorted(skipped_by_date.items(), reverse=True)
        )
        _warn_msg = (
            f"⚠️ <b>Close-Price Sweep (Backtest) — {skipped} ticker(s) could not be filled</b>\n"
            f"{_skipped_lines}\n"
            f"<i>These backtest close prices could not be filled automatically (no Alpaca data or update error). Manual investigation may be needed.</i>"
        )
        try:
            tg_send(_warn_msg)
        except Exception as _tge:
            log.warning(f"_eod_collect_close_prices_backtest: Telegram warning failed: {_tge}")

    return {"written": written, "skipped": skipped}


def _check_stale_backtest_rows(threshold_days: int = BACKTEST_STALE_THRESHOLD_DAYS) -> None:
    """After the nightly backtest close-price sweep, check whether any
    ``backtest_sim_runs`` rows still have ``close_price IS NULL`` and are older
    than ``threshold_days`` calendar days.  If any exist, log a warning and send
    a Telegram alert listing the affected tickers and dates so traders know there
    are permanent data gaps that the sweep cannot auto-heal.

    ``threshold_days`` defaults to ``BACKTEST_STALE_THRESHOLD_DAYS`` (env var
    ``BACKTEST_STALE_THRESHOLD_DAYS``, default 3).
    """
    from datetime import timedelta

    if not _supabase_client:
        log.warning("_check_stale_backtest_rows: No Supabase connection — skipping.")
        return

    stale_cutoff = str(date.today() - timedelta(days=threshold_days))
    _PAGE = 1000
    rows: list = []
    offset = 0
    try:
        while True:
            resp = (
                _supabase_client.table("backtest_sim_runs")
                .select("ticker,sim_date")
                .eq("user_id", USER_ID)
                .is_("close_price", "null")
                .lt("sim_date", stale_cutoff)
                .order("sim_date", desc=True)
                .range(offset, offset + _PAGE - 1)
                .execute()
            )
            batch = resp.data or []
            rows.extend(batch)
            if len(batch) < _PAGE:
                break
            offset += _PAGE
    except Exception as _qe:
        log.warning(f"_check_stale_backtest_rows: DB query failed: {_qe}")
        return

    if not rows:
        log.info(
            f"_check_stale_backtest_rows: No stale NULL close_price rows older than "
            f"{threshold_days} day(s) — all clear."
        )
        return

    by_date: dict[str, list[str]] = {}
    for r in rows:
        sd = (r.get("sim_date") or "")[:10]
        ticker = r.get("ticker", "?")
        if sd:
            by_date.setdefault(sd, []).append(ticker)

    total = sum(len(tks) for tks in by_date.values())
    log.warning(
        f"_check_stale_backtest_rows: {total} backtest row(s) across "
        f"{len(by_date)} date(s) still have NULL close_price after "
        f"{threshold_days}+ day(s) — permanent data gap suspected."
    )

    _date_lines = "\n".join(
        f"  • {d}: {', '.join(sorted(set(tks))[:SWEEP_ALERT_MAX_TICKERS])}"
        + (f" …+{len(set(tks)) - SWEEP_ALERT_MAX_TICKERS} more" if len(set(tks)) > SWEEP_ALERT_MAX_TICKERS else "")
        for d, tks in sorted(by_date.items(), reverse=True)
    )
    _msg = (
        f"🚨 <b>Stale Backtest Close Prices</b>\n"
        f"{total} row(s) across {len(by_date)} date(s) still have no close price "
        f"after {threshold_days}+ day(s) — Alpaca data may be permanently unavailable "
        f"for these entries:\n"
        f"{_date_lines}\n"
        f"<i>Set BACKTEST_STALE_THRESHOLD_DAYS to adjust the staleness window. "
        f"Manual backfill or exclusion may be needed to avoid skewing R-stats.</i>"
    )
    try:
        tg_send(_msg)
    except Exception as _tge:
        log.warning(f"_check_stale_backtest_rows: Telegram alert failed: {_tge}")


def _recalc_eod_pnl_r_recent_backtest(lookback_days: int | None = None) -> dict:
    """Compute eod_pnl_r for backtest_sim_runs rows that now have close_price
    but still have NULL eod_pnl_r.

    When ``lookback_days`` is None (default) all historical rows are swept with
    no date filter.  Pass an integer to restrict the sweep to the most recent N
    days.

    Mirrors _recalc_eod_pnl_r_recent() but targets backtest_sim_runs (date
    field: sim_date).  Called immediately after
    _eod_collect_close_prices_backtest() during nightly recalibration.

    Returns {"written": N, "skipped": M}.
    """
    from datetime import timedelta

    if not _supabase_client:
        log.warning("_recalc_eod_pnl_r_recent_backtest: No Supabase connection — skipping.")
        return {"written": 0, "skipped": 0}

    try:
        from backend import compute_trade_sim_tiered as _compute_tiered
    except Exception as _ie:
        log.warning(f"_recalc_eod_pnl_r_recent_backtest: Cannot import compute_trade_sim_tiered: {_ie}")
        return {"written": 0, "skipped": 0}

    cutoff = str(date.today() - timedelta(days=lookback_days)) if lookback_days is not None else None
    _PAGE = 1000
    rows: list = []

    for direction in ("Bullish Break", "Bearish Break"):
        offset = 0
        while True:
            try:
                q = (
                    _supabase_client.table("backtest_sim_runs")
                    .select("id,actual_outcome,ib_high,ib_low,close_price")
                    .eq("user_id", USER_ID)
                    .eq("actual_outcome", direction)
                    .is_("eod_pnl_r", "null")
                    .not_.is_("close_price", "null")
                )
                if cutoff is not None:
                    q = q.gte("sim_date", cutoff)
                resp = (
                    q
                    .order("id", desc=True)
                    .range(offset, offset + _PAGE - 1)
                    .execute()
                )
            except Exception as _qe:
                log.warning(
                    f"_recalc_eod_pnl_r_recent_backtest: DB query failed ({direction}): {_qe}"
                )
                break
            batch = resp.data or []
            rows.extend(batch)
            if len(batch) < _PAGE:
                break
            offset += _PAGE

    if not rows:
        scope = f"last {lookback_days} days" if lookback_days is not None else "all-time"
        log.info(
            "EOD close-price catch-up (backtest): no backtest_sim_runs rows need eod_pnl_r "
            "recalculation (%s).", scope
        )
        return {"written": 0, "skipped": 0}

    sweep_scope = f"last {lookback_days} days" if lookback_days is not None else "all-time"
    log.info(
        f"EOD eod_pnl_r recalc (backtest, {sweep_scope}): {len(rows)} backtest_sim_runs row(s) have "
        f"close_price but NULL eod_pnl_r — computing now…"
    )

    written = 0
    skipped = 0

    for row in rows:
        close_price = row.get("close_price")
        ib_high = row.get("ib_high")
        ib_low = row.get("ib_low")
        direction = (row.get("actual_outcome") or "").strip()
        row_id = row.get("id")

        if close_price is None or ib_high is None or ib_low is None:
            skipped += 1
            continue

        try:
            tiered = _compute_tiered(
                aft_df=None,
                ib_high=ib_high,
                ib_low=ib_low,
                direction=direction,
                close_px=close_price,
            )
        except Exception as _te:
            log.debug(f"  compute_trade_sim_tiered failed for id={row_id}: {_te}")
            skipped += 1
            continue

        eod_pnl_r = tiered.get("eod_pnl_r")
        if eod_pnl_r is None:
            skipped += 1
            continue

        try:
            _supabase_client.table("backtest_sim_runs").update(
                {"eod_pnl_r": round(float(eod_pnl_r), 6)}
            ).eq("id", row_id).execute()
            log.info(f"    id={row_id}: eod_pnl_r → {eod_pnl_r:.4f}R")
            written += 1
        except Exception as _ue:
            log.warning(f"    DB update failed for id={row_id}: {_ue}")
            skipped += 1

    log.info(
        f"EOD eod_pnl_r recalc (backtest) done — {written} written, {skipped} skipped."
    )
    return {"written": written, "skipped": skipped}


def _append_eod_recalc_history(path_label: str, written: int, skipped: int, elapsed_s: float) -> None:
    """Append one EOD P&L recalc run to the JSON history file read by the dashboard.

    Keeps the most recent 50 entries.  Silently swallows all errors so a
    file-write problem never interrupts the EOD job.

    Args:
        path_label: Short string identifying which code path triggered the
            recalc — "main", "already-resolved", or "scan-failed".
        written:   Number of rows updated in this run.
        skipped:   Number of rows skipped (no qualifying data).
        elapsed_s: Wall-clock time the recalc took, in seconds.
    """
    import json as _json

    _default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eod_recalc_history.json")
    history_path = os.environ.get("EOD_RECALC_HISTORY_PATH", _default_path)
    try:
        try:
            with open(history_path) as _f:
                _history = _json.load(_f)
            if not isinstance(_history, list):
                _history = []
        except FileNotFoundError:
            _history = []

        _entry = {
            "completed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "path": path_label,
            "written": written,
            "skipped": skipped,
            "elapsed_s": round(elapsed_s, 3),
        }
        _history.append(_entry)
        _history = _history[-50:]

        with open(history_path, "w") as _f:
            _json.dump(_history, _f)
    except Exception as _he:
        log.warning(f"_append_eod_recalc_history: could not write history: {_he}")


def eod_update():
    """4:20 PM ET — update paper trades with full-day outcomes + send EOD summary."""
    today = date.today()
    log.info("=" * 60)
    log.info("EOD UPDATE — resolving outcomes with full-day bar data")
    log.info("=" * 60)

    # Guard: check DB to see if EOD already ran today (prevents duplicate on restart)
    try:
        from backend import supabase as _sb
        existing = _sb.table("paper_trades").select("actual_outcome").eq(
            "user_id", USER_ID).eq("trade_date", str(today)).neq(
            "actual_outcome", "Pending").limit(1).execute()
        if existing.data:
            log.info(f"EOD already resolved for {today} — skipping to prevent duplicate.")
            # Still run the close-price sweep in case it was missed on the prior
            # run (e.g. the sweep itself failed or the bot was restarted mid-EOD).
            try:
                _eod_collect_close_prices()
            except Exception as _cpe_guard:
                log.warning(f"EOD close-price sweep (already-resolved path) failed: {_cpe_guard}")
            try:
                _t0_rpnl = time.monotonic()
                _rpnl_guard_res = _recalc_eod_pnl_r_recent(lookback_days=1)
                _elapsed_rpnl = time.monotonic() - _t0_rpnl
                log.info(
                    f"EOD P&L recalc (already-resolved path): "
                    f"{_rpnl_guard_res.get('written', 0)} row(s) updated, "
                    f"{_rpnl_guard_res.get('skipped', 0)} skipped — "
                    f"{_elapsed_rpnl:.2f}s"
                )
                _append_eod_recalc_history(
                    "already-resolved",
                    _rpnl_guard_res.get("written", 0),
                    _rpnl_guard_res.get("skipped", 0),
                    _elapsed_rpnl,
                )
                _written_guard = _rpnl_guard_res.get("written", 0)
                _skipped_guard = _rpnl_guard_res.get("skipped", 0)
                if (
                    _written_guard == 0
                    and _skipped_guard > 0
                    and _is_nyse_trading_day(today)
                    and _get_recalc_zero_alerts_enabled()
                ):
                    log.warning(
                        f"EOD P&L recalc (already-resolved path): zero rows written on a trading day "
                        f"({_skipped_guard} skipped) — sending Telegram alert."
                    )
                    tg_send(
                        f"⚠️ <b>EOD Recalc — Zero Rows Written</b>\n"
                        f"Date: {today}\n"
                        f"Path: already-resolved\n"
                        f"Written: 0 · Skipped: {_skipped_guard}\n"
                        f"The nightly P&amp;L recalculation updated no rows on a trading day. "
                        f"Check for stale data or a Supabase issue."
                    )
            except Exception as _rpnl_guard:
                log.warning(f"EOD P&L recalc (already-resolved path) failed: {_rpnl_guard}")
            return
    except Exception as _ge:
        log.warning(f"EOD guard check failed (proceeding anyway): {_ge}")

    # ── Step 1: Cancel any unfilled Alpaca orders before market closes ────────
    if LIVE_ORDERS_ENABLED:
        acct = "PAPER" if IS_PAPER_ALPACA else "LIVE"
        log.info(f"Cancelling unfilled {acct} orders for {today}...")
        cancel_result = cancel_alpaca_day_orders(
            is_paper   = IS_PAPER_ALPACA,
            api_key    = ALPACA_API_KEY,
            secret_key = ALPACA_SECRET_KEY,
        )
        log.info(
            f"  Orders cancelled: {cancel_result.get('cancelled', 0)} | "
            f"errors: {cancel_result.get('errors', 0)}"
        )

    results = _run_scan(today, cutoff_h=15, cutoff_m=55)
    if not results:
        log.warning("No results from EOD scan — cannot update outcomes.")
        tg_send(f"⚠️ <b>EOD Update Failed</b> — {today}\nNo bar data returned.")
        # Still run close-price sweep so existing paper_trades rows don't go NULL
        # even when the scan itself comes back empty.
        try:
            _eod_collect_close_prices()
        except Exception as _cpe_early:
            log.warning(f"EOD close-price sweep (scan-failed path) failed: {_cpe_early}")
        try:
            _t0_rpnl = time.monotonic()
            _rpnl_early_res = _recalc_eod_pnl_r_recent(lookback_days=1)
            _elapsed_rpnl = time.monotonic() - _t0_rpnl
            log.info(
                f"EOD P&L recalc (scan-failed path): "
                f"{_rpnl_early_res.get('written', 0)} row(s) updated, "
                f"{_rpnl_early_res.get('skipped', 0)} skipped — "
                f"{_elapsed_rpnl:.2f}s"
            )
            _append_eod_recalc_history(
                "scan-failed",
                _rpnl_early_res.get("written", 0),
                _rpnl_early_res.get("skipped", 0),
                _elapsed_rpnl,
            )
            _written_early = _rpnl_early_res.get("written", 0)
            _skipped_early = _rpnl_early_res.get("skipped", 0)
            if (
                _written_early == 0
                and _skipped_early > 0
                and _is_nyse_trading_day(today)
                and _get_recalc_zero_alerts_enabled()
            ):
                log.warning(
                    f"EOD P&L recalc (scan-failed path): zero rows written on a trading day "
                    f"({_skipped_early} skipped) — sending Telegram alert."
                )
                tg_send(
                    f"⚠️ <b>EOD Recalc — Zero Rows Written</b>\n"
                    f"Date: {today}\n"
                    f"Path: scan-failed\n"
                    f"Written: 0 · Skipped: {_skipped_early}\n"
                    f"The nightly P&amp;L recalculation updated no rows on a trading day. "
                    f"Check for stale data or a Supabase issue."
                )
        except Exception as _rpnl_early:
            log.warning(f"EOD P&L recalc (scan-failed path) failed: {_rpnl_early}")
        return

    for r in results:
        r.setdefault("scan_type", "eod")  # only set if not already tagged (morning record keeps its tag)

    upd = update_paper_trade_outcomes(str(today), results, user_id=USER_ID)
    updated_count = upd.get("updated", 0)
    log.info(f"Updated {updated_count} paper trade outcome(s) for {today}")

    for r in results:
        log.info(
            f"  {r['ticker']:6s} | {r.get('win_loss', '?'):4s} | "
            f"actual: {r.get('actual_outcome', '—'):18s} | "
            f"FT {r.get('aft_move_pct', 0):+.1f}%"
        )

    # ── Step 2: Reconcile Alpaca fills → patch paper_trades with fill prices ──
    if LIVE_ORDERS_ENABLED:
        log.info("Reconciling Alpaca fills with paper_trades...")
        rec = reconcile_alpaca_fills(
            trade_date = str(today),
            user_id    = USER_ID,
            is_paper   = IS_PAPER_ALPACA,
            api_key    = ALPACA_API_KEY,
            secret_key = ALPACA_SECRET_KEY,
        )
        log.info(
            f"  Fills matched: {rec.get('matched', 0)} | "
            f"unmatched: {rec.get('unmatched', 0)} | "
            f"errors: {rec.get('errors', 0)}"
        )

    # Telegram EOD summary — compute per-structure filter breakdown.
    # EOD uses the base MIN_TCS as global floor (regime not re-fetched here).
    # Counts are baseline-floor accounting; small discrepancy vs morning/intraday
    # regime-adjusted sessions is acceptable for end-of-day reporting.
    _tcs_thresholds_eod = load_tcs_thresholds(default=MIN_TCS)
    _eod_global_floor   = MIN_TCS
    _global_filtered    = [r for r in results if float(r.get("tcs", 0)) < _eod_global_floor]
    _struct_filtered    = [
        r for r in results
        if float(r.get("tcs", 0)) >= _eod_global_floor
        and float(r.get("tcs", 0)) < _struct_tcs_floor(r, _tcs_thresholds_eod, _eod_global_floor)
    ]
    qualified_results = [
        r for r in results
        if float(r.get("tcs", 0)) >= _struct_tcs_floor(r, _tcs_thresholds_eod, _eod_global_floor)
    ]
    _alert_eod_summary(
        qualified_results, updated_count, today,
        global_filtered=len(_global_filtered),
        struct_filtered=len(_struct_filtered),
    )

    # ── Beta subscriber broadcast (clean — no TCS/brain language) ─────────
    _broadcast_eod_to_subscribers(results, today)

    # ── Sweep: fill close_price for any paper_trades rows still NULL ──────────
    # update_paper_trade_outcomes() only writes close_price for tickers that
    # appear in the EOD scan results.  Any rows logged earlier that didn't make
    # it into the scan (or from days when the EOD job failed) stay NULL without
    # this self-healing sweep, which covers the last 60 days automatically.
    try:
        _cp = _eod_collect_close_prices()
        if _cp.get("written", 0):
            log.info(
                f"EOD close-price sweep: {_cp['written']} row(s) updated "
                f"({_cp.get('skipped', 0)} skipped — no Alpaca data)."
            )
    except Exception as _cpe:
        log.warning(f"EOD close-price sweep failed (non-critical): {_cpe}")
    try:
        _t0_rpnl = time.monotonic()
        _rpnl_res = _recalc_eod_pnl_r_recent(lookback_days=1)
        _elapsed_rpnl = time.monotonic() - _t0_rpnl
        log.info(
            f"EOD P&L recalc (main path): "
            f"{_rpnl_res.get('written', 0)} row(s) updated, "
            f"{_rpnl_res.get('skipped', 0)} skipped — "
            f"{_elapsed_rpnl:.2f}s"
        )
        _append_eod_recalc_history(
            "main",
            _rpnl_res.get("written", 0),
            _rpnl_res.get("skipped", 0),
            _elapsed_rpnl,
        )
        _written_main  = _rpnl_res.get("written", 0)
        _skipped_main  = _rpnl_res.get("skipped", 0)
        if (
            _written_main == 0
            and _skipped_main > 0
            and _is_nyse_trading_day(today)
            and _get_recalc_zero_alerts_enabled()
        ):
            log.warning(
                f"EOD P&L recalc (main path): zero rows written on a trading day "
                f"({_skipped_main} skipped) — sending Telegram alert."
            )
            tg_send(
                f"⚠️ <b>EOD Recalc — Zero Rows Written</b>\n"
                f"Date: {today}\n"
                f"Path: main\n"
                f"Written: 0 · Skipped: {_skipped_main}\n"
                f"The nightly P&amp;L recalculation updated no rows on a trading day. "
                f"Check for stale data or a Supabase issue."
            )
    except Exception as _rpnl:
        log.warning(f"EOD P&L recalc (main path) failed (non-critical): {_rpnl}")


def _send_rankings_summary(rows: list, rating_date) -> None:
    """Send a Telegram message summarising nightly ranking performance by rank tier."""
    if not rows:
        return
    from collections import defaultdict
    tier_rows = defaultdict(list)
    for r in rows:
        tier_rows[r["rank"]].append(r)

    # Rank 5/4 = bullish (win = positive chg)
    # Rank 3   = neutral (not scored)
    # Rank 2/1 = bearish/fade (win = negative chg)
    # Rank 0   = don't take the trade (excluded from scoring)
    def _is_win(rank, chg):
        if rank in (4, 5):
            return chg > 0
        elif rank in (1, 2):
            return chg < 0
        return None  # rank 3 = neutral, rank 0 = skip

    correct = sum(1 for r in rows if _is_win(r["rank"], r["chg"]) is True)
    called  = sum(1 for r in rows if _is_win(r["rank"], r["chg"]) is not None)

    lines = [f"📊 <b>Nightly Rankings — {rating_date}</b>", ""]
    lines.append(f"Accuracy: {correct}/{called} = {100*correct/called:.0f}%  "
                 f"(R1/R2=bearish wins if red; R3-5=bullish wins if green)")
    lines.append("")

    for tier in sorted(tier_rows.keys(), reverse=True):
        tier_data = tier_rows[tier]
        wins = sum(1 for r in tier_data if _is_win(r["rank"], r["chg"]) is True)
        avg  = sum(r["chg"] for r in tier_data) / len(tier_data)
        bearish = tier in (1, 2)
        skip    = tier == 0
        label   = "★" * tier if tier > 0 else "⏭ Skip"
        bias    = " (bearish)" if bearish else (" (neutral)" if tier == 3 else "") if not skip else " (no trade)"
        lines.append(f"<b>Rank {tier}</b> {label}{bias} — {wins}/{len(tier_data)} wins | avg {avg:+.1f}%")
        sort_key = (lambda x: x["chg"]) if bearish else (lambda x: -x["chg"])
        for r in sorted(tier_data, key=sort_key):
            won   = _is_win(r["rank"], r["chg"])
            arrow = "🟢" if won else ("🔴" if won is False else "⬜")
            note  = r.get("notes", "").strip()
            note_line = f" <i>{note[:80]}{'…' if len(note) > 80 else ''}</i>" if note else ""
            lines.append(f"  {arrow} <b>{r['ticker']}</b> {r['chg']:+.1f}%{note_line}")
        lines.append("")

    tg_send("\n".join(lines))
    log.info(f"Rankings summary sent — {correct}/{called} correct")


def nightly_verify():
    """4:25 PM ET — auto-run Verify Date for today so brain gets fresh signal
    without requiring manual button press in the UI."""
    log.info("=" * 60)
    log.info("AUTO VERIFY — running end-of-day prediction verification")
    log.info("=" * 60)
    try:
        result = verify_watchlist_predictions(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            user_id=USER_ID,
        )
        if result.get("error") and result.get("verified", 0) == 0:
            log.warning(f"Auto-verify skipped: {result['error']}")
            return
        verified  = result.get("verified", 0)
        correct   = result.get("correct", 0)
        accuracy  = result.get("accuracy", 0.0)
        bar_date  = result.get("bar_date", "—")
        log.info(f"Verified {verified} prediction(s) for {bar_date} — "
                 f"{correct} correct ({accuracy:.1f}% accuracy)")
        if verified > 0:
            tg_send(
                f"✅ <b>Auto-Verify Complete</b> — {bar_date}\n"
                f"Verified: {verified} | Correct: {correct} | "
                f"Accuracy: {accuracy:.1f}%"
            )
    except Exception as e:
        log.error(f"Auto-verify failed: {e}")

    try:
        if ensure_ticker_rankings_table():
            from datetime import timedelta
            today     = datetime.now(EASTERN).date()
            yesterday = (datetime.now(EASTERN) - timedelta(days=1)).date()

            # Verify yesterday's ratings using today's data (standard next-day logic)
            _rk_yes = verify_ticker_rankings(ALPACA_API_KEY, ALPACA_SECRET_KEY, USER_ID, yesterday)
            if _rk_yes["verified"] > 0:
                log.info(f"Auto-verified {_rk_yes['verified']} ticker rankings from {yesterday}")
            elif _rk_yes["errors"] > 0:
                log.warning(f"Ticker ranking verify ({yesterday}): {_rk_yes['errors']} errors")

            # Verify today's ratings using today's data (same-day — for ratings made
            # during the night/early morning of the same trading session)
            _rk_today = verify_ticker_rankings(
                ALPACA_API_KEY, ALPACA_SECRET_KEY, USER_ID, today, same_day=True
            )
            if _rk_today["verified"] > 0:
                log.info(f"Same-day verified {_rk_today['verified']} ticker rankings for {today}")
                _send_rankings_summary(_rk_today["rows"], today)
            elif _rk_today["errors"] > 0:
                log.warning(f"Ticker ranking verify ({today} same-day): {_rk_today['errors']} errors")
    except Exception as _rk_e:
        log.warning(f"Ticker ranking auto-verify failed (non-fatal): {_rk_e}")

    # Cognitive delta — fill in actual changes for today's logged decisions
    try:
        _cd_updated = verify_cognitive_delta(ALPACA_API_KEY, ALPACA_SECRET_KEY, USER_ID, today)
        if _cd_updated > 0:
            log.info(f"Cognitive delta: verified {_cd_updated} entries for {today}")
    except Exception as _cd_e:
        log.warning(f"Cognitive delta verify failed (non-fatal): {_cd_e}")


def update_daily_build_notes() -> bool:
    """Append today's EOD results to .local/build_notes.md.
    Called after nightly_recalibration() finishes.  Non-fatal — bot continues
    normally if this function fails for any reason.
    """
    import json as _json

    today_str   = datetime.now(EASTERN).strftime("%Y-%m-%d")
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    BUILD_NOTES = os.path.join(_script_dir, ".local", "build_notes.md")

    log.info("=" * 60)
    log.info(f"BUILD NOTES UPDATE — appending EOD results for {today_str}")
    log.info("=" * 60)

    # ── Read current file ─────────────────────────────────────────────────────
    try:
        with open(BUILD_NOTES, "r", encoding="utf-8") as _f:
            content = _f.read()
    except FileNotFoundError:
        log.warning(f"update_daily_build_notes: {BUILD_NOTES} not found, skipping")
        return False

    # ── Fetch today's paper trades ─────────────────────────────────────────────
    import pandas as _pd
    df_today = _pd.DataFrame()
    try:
        from backend import load_paper_trades as _lpt
        df_all = _lpt(user_id=USER_ID, days=1)
        if not df_all.empty and "trade_date" in df_all.columns:
            df_today = df_all[df_all["trade_date"].astype(str).str.startswith(today_str)]
    except Exception as _exc:
        log.warning(f"update_daily_build_notes: could not load paper trades: {_exc}")

    # ── Load current brain weights ────────────────────────────────────────────
    weights: dict = {}
    try:
        _bw_path = os.path.join(_script_dir, "brain_weights.json")
        with open(_bw_path, "r", encoding="utf-8") as _f:
            weights = _json.load(_f)
    except Exception as _exc:
        log.warning(f"update_daily_build_notes: could not load brain_weights.json: {_exc}")

    # ── Fetch structure win rates from accuracy_tracker ───────────────────────
    struct_overall_n = struct_overall_c = 0
    struct_bot_n = struct_bot_c = 0
    struct_breakdown: dict = {}
    try:
        from backend import supabase as _sb
        from collections import defaultdict as _dd
        if _sb:
            _at_rows = (
                _sb.table("accuracy_tracker")
                .select("predicted,correct,compare_key")
                .eq("user_id", USER_ID)
                .execute()
                .data or []
            )
            struct_overall_n = len(_at_rows)
            struct_overall_c = sum(1 for r in _at_rows if r.get("correct") == "✅")
            bot_at = [r for r in _at_rows if r.get("compare_key") == "watchlist_pred"]
            struct_bot_n = len(bot_at)
            struct_bot_c = sum(1 for r in bot_at if r.get("correct") == "✅")
            _by = _dd(lambda: {"t": 0, "c": 0})
            for r in bot_at:
                _lbl = str(r.get("predicted") or "—").strip()
                if _lbl in ("—", "", "Unknown"):
                    continue
                _by[_lbl]["t"] += 1
                if r.get("correct") == "✅":
                    _by[_lbl]["c"] += 1
            struct_breakdown = dict(_by)
    except Exception as _exc:
        log.warning(f"update_daily_build_notes: could not load accuracy_tracker: {_exc}")

    # ── Compute day stats ─────────────────────────────────────────────────────
    total_scanned = len(df_today)
    if total_scanned and "min_tcs_filter" in df_today.columns:
        _mtf = df_today["min_tcs_filter"].dropna()
        min_tcs_used = int(_mtf.max()) if not _mtf.empty else MIN_TCS
    else:
        min_tcs_used = MIN_TCS

    if total_scanned:
        qual_df  = df_today[df_today["tcs"].astype(float) >= min_tcs_used]
        _wl_col  = df_today["win_loss"].astype(str) if "win_loss" in df_today.columns else _pd.Series([""] * total_scanned)
        res_df   = df_today[_wl_col.isin(["Win", "Loss"])]
        wins_df  = res_df[res_df["win_loss"] == "Win"]
        loss_df  = res_df[res_df["win_loss"] == "Loss"]
        win_n    = len(wins_df)
        loss_n   = len(loss_df)
        total_r  = win_n + loss_n
        win_rate = round(100 * win_n / total_r, 1) if total_r else None
        avg_tcs  = round(df_today["tcs"].astype(float).mean(), 1)
        avg_wft  = round(wins_df["follow_thru_pct"].astype(float).mean(), 1) if win_n else None
        avg_lft  = round(loss_df["follow_thru_pct"].astype(float).mean(), 1) if loss_n else None
        alerted  = ", ".join(qual_df["ticker"].tolist()) if len(qual_df) else "—"
    else:
        qual_df = res_df = wins_df = loss_df = _pd.DataFrame()
        win_n = loss_n = total_r = 0
        win_rate = avg_tcs = avg_wft = avg_lft = None
        alerted = "—"

    # Simulated $ P&L: 100 shares × open_price × (follow_thru_pct / 100)
    sim_pnl = 0.0
    if total_scanned and not res_df.empty:
        for _, _row in res_df.iterrows():
            try:
                _ft = float(_row.get("follow_thru_pct") or 0)
                _op = float(_row.get("open_price") or 0)
                sim_pnl += (_ft / 100) * _op * 100
            except Exception:
                pass
    sim_pnl = round(sim_pnl, 2)

    # ── Build new row strings ─────────────────────────────────────────────────
    trade_rows: list = []
    if not df_today.empty:
        for _, _row in df_today.iterrows():
            _tk  = str(_row.get("ticker", "?"))
            _tcs = f"{float(_row.get('tcs', 0)):.0f}"
            _pred = str(_row.get("predicted") or "?")
            _act  = str(_row.get("actual_outcome") or "—")
            _wl   = str(_row.get("win_loss") or "—")
            _ft   = _row.get("follow_thru_pct")
            _fts  = f"{float(_ft):+.1f}%" if _ft is not None and str(_ft) not in ("", "None", "nan") else "—"
            trade_rows.append(f"| {today_str} | {_tk} | {_tcs} | {_pred} | {_act} | {_wl} | {_fts} |")
    else:
        trade_rows.append(f"| {today_str} | — | — | No setups logged | — | — | — |")

    _wr   = f"{win_rate}%" if win_rate is not None else "—"
    _awf  = f"+{avg_wft}%" if avg_wft is not None else "—"
    _alf  = f"{avg_lft}%" if avg_lft is not None else "—"
    _sign = "+" if sim_pnl >= 0 else ""
    pnl_row = f"| {today_str} | {win_n} | {loss_n} | {_wr} | {_awf} | {_alf} | {_sign}${sim_pnl:.2f} |"

    _W_KEYS = ["trend_bull", "trend_bear", "normal", "neutral", "ntrl_extreme",
               "nrml_variation", "non_trend", "double_dist"]
    _bw_vals = " | ".join(f"{weights.get(k, 1.0):.4f}" for k in _W_KEYS)
    bw_row   = f"| {today_str} | {_bw_vals} |"

    _avg_tcs_s = f"{avg_tcs}" if avg_tcs is not None else "—"
    scan_row   = f"| {today_str} | {total_scanned} | {len(qual_df)} | {_wr} | {_avg_tcs_s} | {alerted} |"

    # Structure win rate row
    def _spct(c: int, t: int) -> str:
        return f"{c/t*100:.1f}% ({c}/{t})" if t else "—"
    _neu  = struct_breakdown.get("Neutral",      {"t": 0, "c": 0})
    _ntx  = struct_breakdown.get("Ntrl Extreme", {"t": 0, "c": 0})
    struct_row = (
        f"| {today_str} | {_spct(struct_bot_c, struct_bot_n)} | {struct_bot_n} "
        f"| {_spct(_neu['c'], _neu['t'])} | {_neu['t']} "
        f"| {_spct(_ntx['c'], _ntx['t'])} | {_ntx['t']} |"
    )

    # ── Section headings and table headers ────────────────────────────────────
    _TRADE_H = "## 📊 BOT PAPER TRADE LOG"
    _PNL_H   = "## 💰 BOT P&L LOG"
    _BRAIN_H = "## 🧠 BRAIN WEIGHT HISTORY"
    _SCAN_H  = "## 🔍 DAILY SCAN OBSERVATIONS"

    _TRADE_INIT = (
        f"{_TRADE_H}\n\n"
        "| Date | Ticker | TCS | Predicted | Actual | W/L | Follow-thru % |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    _PNL_INIT = (
        f"{_PNL_H}\n\n"
        "| Date | Wins | Losses | Win Rate | Avg Win FT% | Avg Loss FT% | Sim P&L (100sh) |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    _BRAIN_INIT = (
        f"{_BRAIN_H}\n\n"
        "| Date | trend_bull | trend_bear | normal | neutral |"
        " ntrl_extreme | nrml_variation | non_trend | double_dist |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    _SCAN_INIT = (
        f"{_SCAN_H}\n\n"
        "| Date | Total Scanned | Qualified | Win Rate | Avg TCS | Alerted Tickers |\n"
        "|---|---|---|---|---|---|\n"
    )
    _STRUCT_H    = "## 📈 STRUCTURE WIN RATE LOG"
    _STRUCT_INIT = (
        f"{_STRUCT_H}\n\n"
        "| Date | Bot Overall | Bot N | Neutral | Neutral N | Ntrl Extreme | Ntrl Extreme N |\n"
        "|---|---|---|---|---|---|---|\n"
    )

    def _section_slice(text: str, heading: str) -> str:
        """Return the text belonging to the section (from heading to next ## or EOF)."""
        if heading not in text:
            return ""
        idx   = text.index(heading)
        after = text[idx + len(heading):]
        next_h = after.find("\n## ")
        return after[:next_h] if next_h != -1 else after

    def _already_logged(text: str, heading: str) -> bool:
        """Return True if today's date already appears in the section — idempotency guard."""
        return f"| {today_str} |" in _section_slice(text, heading)

    def _parse_running_total(text: str) -> float:
        """Parse the most recent running-total value from the P&L log table."""
        section = _section_slice(text, _PNL_H)
        data_rows = [
            l.strip() for l in section.split("\n")
            if l.strip().startswith("|")
            and "|---|" not in l
            and "Date" not in l
            and l.strip() != "|"
        ]
        if not data_rows:
            return 0.0
        cols = [c.strip() for c in data_rows[-1].split("|") if c.strip()]
        try:
            return float(cols[-1].replace("$", "").replace("+", ""))
        except Exception:
            return 0.0

    def _append_rows_to_section(text: str, heading: str, init_block: str, new_rows: list) -> str:
        """Append new_rows inside the section identified by heading.
        Creates the section at the bottom if it doesn't exist yet.
        Never deletes or reformats any existing content.
        Caller is responsible for checking _already_logged() before calling this.
        """
        if heading not in text:
            sep = "\n\n---\n\n"
            return text.rstrip("\n") + sep + init_block + "\n".join(new_rows) + "\n"

        idx   = text.index(heading)
        after = text[idx + len(heading):]
        next_h = after.find("\n## ")
        if next_h == -1:
            return text.rstrip("\n") + "\n" + "\n".join(new_rows) + "\n"
        insert_at = idx + len(heading) + next_h
        return (
            text[:insert_at].rstrip("\n")
            + "\n" + "\n".join(new_rows)
            + "\n\n"
            + text[insert_at:].lstrip("\n")
        )

    # Compute running P&L total (prior total + today's sim P&L)
    prior_total  = _parse_running_total(content)
    running_total = round(prior_total + sim_pnl, 2)
    _rt_sign      = "+" if running_total >= 0 else ""
    pnl_row = (
        f"| {today_str} | {win_n} | {loss_n} | {_wr} | {_awf} | {_alf} "
        f"| {_sign}${sim_pnl:.2f} | {_rt_sign}${running_total:.2f} |"
    )

    # Update P&L table header to include Running Total column
    _PNL_INIT = (
        f"{_PNL_H}\n\n"
        "| Date | Wins | Losses | Win Rate | Avg Win FT% | Avg Loss FT% "
        "| Sim P&L (100sh) | Running Total |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )

    # Append each section — idempotency: skip if today already logged
    if not _already_logged(content, _TRADE_H):
        content = _append_rows_to_section(content, _TRADE_H, _TRADE_INIT, trade_rows)
    else:
        log.info(f"Build notes: {_TRADE_H} already has {today_str} entry, skipping")

    if not _already_logged(content, _PNL_H):
        content = _append_rows_to_section(content, _PNL_H, _PNL_INIT, [pnl_row])
    else:
        log.info(f"Build notes: {_PNL_H} already has {today_str} entry, skipping")

    if not _already_logged(content, _BRAIN_H):
        content = _append_rows_to_section(content, _BRAIN_H, _BRAIN_INIT, [bw_row])
    else:
        log.info(f"Build notes: {_BRAIN_H} already has {today_str} entry, skipping")

    if not _already_logged(content, _SCAN_H):
        content = _append_rows_to_section(content, _SCAN_H, _SCAN_INIT, [scan_row])
    else:
        log.info(f"Build notes: {_SCAN_H} already has {today_str} entry, skipping")

    if not _already_logged(content, _STRUCT_H):
        content = _append_rows_to_section(content, _STRUCT_H, _STRUCT_INIT, [struct_row])
    else:
        log.info(f"Build notes: {_STRUCT_H} already has {today_str} entry, skipping")

    # ── Write back ────────────────────────────────────────────────────────────
    try:
        with open(BUILD_NOTES, "w", encoding="utf-8") as _f:
            _f.write(content)
        log.info(
            f"Build notes updated: {len(trade_rows)} trade row(s) | "
            f"wins={win_n} losses={loss_n} | sim P&L {_sign}${abs(sim_pnl):.2f}"
        )
        # ── Refresh timestamps in all 4 doc files ────────────────────────────
        _refresh_all_doc_timestamps()
        return True
    except Exception as _exc:
        log.error(f"update_daily_build_notes: write failed: {_exc}")
        return False


def _refresh_all_doc_timestamps() -> None:
    """Update the 'Last updated / Updated / Created' date stamp in all 4 build
    note files so every nightly PDF export shows the correct date.

    Patterns handled per file:
      build_notes.md        — *Last updated: Month D, YYYY*
      build_notes_private.md — *Last updated: Month D, YYYY — <suffix>*
      ip_documentation.md   — **Created:** Month D, YYYY
      PRIORITIES.md         — _Updated: Month D, YYYY. <suffix>_
    """
    import re as _re

    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _today_long = datetime.now(EASTERN).strftime("%B %-d, %Y")   # e.g. "April 19, 2026"

    _FILES = {
        "build_notes.md": (
            r"(\*Last updated: )[A-Za-z]+ \d+, \d+(\*)",
            r"\g<1>" + _today_long + r"\g<2>",
        ),
        "build_notes_private.md": (
            r"(\*Last updated: )[A-Za-z]+ \d+, \d+",
            r"\g<1>" + _today_long,
        ),
        "ip_documentation.md": (
            r"(\*\*Created:\*\* )[A-Za-z]+ \d+, \d+",
            r"\g<1>" + _today_long,
        ),
        "PRIORITIES.md": (
            r"(_Updated: )[A-Za-z]+ \d+, \d+(\. )",
            r"\g<1>" + _today_long + r"\g<2>",
        ),
    }

    for _fname, (_pattern, _repl) in _FILES.items():
        _path = os.path.join(_script_dir, ".local", _fname)
        if not os.path.exists(_path):
            log.info(f"[DocTimestamp] {_fname} not found — skipping")
            continue
        try:
            with open(_path, "r", encoding="utf-8") as _f:
                _text = _f.read()
            _new_text, _n = _re.subn(_pattern, _repl, _text, count=1)
            if _n:
                with open(_path, "w", encoding="utf-8") as _f:
                    _f.write(_new_text)
                log.info(f"[DocTimestamp] {_fname} — timestamp updated to {_today_long}")
            else:
                log.info(f"[DocTimestamp] {_fname} — no timestamp pattern found, skipped")
        except Exception as _exc:
            log.warning(f"[DocTimestamp] {_fname} failed: {_exc}")


def nightly_recalibration():
    """4:30 PM ET — update BOTH brains: live personal + historical prior."""
    log.info("=" * 60)
    log.info("NIGHTLY RECALIBRATION — live brain + historical brain")
    log.info("=" * 60)

    old_tcs = load_tcs_thresholds(default=MIN_TCS)

    # ── Live personal brain (accuracy_tracker + paper_trades) ─────────────────
    try:
        cal = recalibrate_from_supabase(user_id=USER_ID)
        src = cal.get("sources", {})
        log.info(
            f"Live brain — accuracy_tracker: {src.get('accuracy_tracker', 0)} | "
            f"paper_trades: {src.get('paper_trades', 0)} | "
            f"total: {src.get('total', 0)}"
        )
        if not cal.get("calibrated"):
            log.info("Live brain: not enough data yet (need ≥5 samples). Weights unchanged.")
        else:
            deltas = cal.get("deltas", [])
            log.info(f"Live brain updated — {len(deltas)} structure(s) adjusted:")
            for d in deltas:
                direction = "▲" if d["delta"] > 0 else ("▼" if d["delta"] < 0 else "—")
                total_n = (d.get("journal_n") or 0) + (d.get("bot_n") or 0)
                log.info(
                    f"  {d['key']:16s} | {d['old']:.4f} → {d['new']:.4f} "
                    f"({direction}{abs(d['delta']):.4f}) | "
                    f"acc {d.get('blended_acc', '?')}% over {total_n} samples"
                )
        _alert_recalibration(cal)
    except Exception as exc:
        log.error(f"Live brain recalibration failed: {exc}")
        tg_send(f"⚠️ <b>Live Brain Recalibration Error</b>\n{exc}")

    # ── Historical brain (backtest_sim_runs — statistical prior) ──────────────
    try:
        hist = recalibrate_from_history(user_id=USER_ID)
        h_src = hist.get("sources", {})
        log.info(
            f"Historical brain — backtest_sim_runs: {h_src.get('backtest_sim_runs', 0):,} rows"
        )
        if not hist.get("calibrated"):
            log.info("Historical brain: no resolved backtest rows found.")
        else:
            h_deltas = hist.get("deltas", [])
            log.info(f"Historical brain updated — {len(h_deltas)} structure(s) calibrated:")
            for d in h_deltas:
                direction = "▲" if d["delta"] > 0 else ("▼" if d["delta"] < 0 else "—")
                log.info(
                    f"  {d['key']:16s} | {d['old']:.4f} → {d['new']:.4f} "
                    f"({direction}{abs(d['delta']):.4f}) | "
                    f"hist acc {d.get('hist_acc', '?')}% over {d.get('hist_n', 0):,} samples"
                )
    except Exception as exc:
        log.error(f"Historical brain calibration failed: {exc}")

    # ── TCS threshold change alert + persistent history ────────────────────────
    try:
        new_tcs = load_tcs_thresholds(default=MIN_TCS)
        _alert_tcs_threshold_changes(old_tcs, new_tcs)
        # Persist exactly one history event per nightly recalibration cycle using
        # the true before/after snapshots captured around both brain updates.
        append_tcs_threshold_history(old_tcs, new_tcs)
    except Exception as exc:
        log.warning(f"TCS threshold change alert/history failed: {exc}")

    # ── Close-price catch-up sweep (PAPER_CLOSE_LOOKBACK_DAYS) ───────────────
    # The 4:20 PM eod_update() sweep covers today's rows.  Any day the bot was
    # down or the sweep itself failed will leave NULLs behind.  Re-running with
    # a look-back window here heals those stragglers automatically every evening.
    # Window is read at runtime via _get_effective_paper_lookback_days() so that
    # a value saved in the dashboard settings panel takes effect immediately on
    # the next nightly run without requiring a server restart.
    try:
        _effective_lookback = _get_effective_paper_lookback_days()
        log.info(
            f"Nightly close-price catch-up sweep: "
            f"checking last {_effective_lookback} days…"
        )
        cp_result = _eod_collect_close_prices(lookback_days=_effective_lookback)
        log.info(
            f"Nightly close-price catch-up: "
            f"{cp_result['written']} filled, {cp_result['skipped']} skipped."
        )
    except Exception as exc:
        log.warning(f"Nightly close-price catch-up sweep failed: {exc}")

    # ── eod_pnl_r recalculation for newly-filled close prices ────────────────
    # After filling close_price, immediately compute eod_pnl_r for any
    # breakout rows that are still missing it (avoids a manual backfill run).
    # No lookback window is applied here — the sweep covers ALL historical
    # paper_trades rows so that close prices backfilled via the manual Retry
    # button (or any previous nightly run that lacked a close price) are
    # always picked up, not just those from the last 7 days.
    pr_result: dict = {"written": 0, "skipped": 0}
    try:
        pr_result = _recalc_eod_pnl_r_recent()
        if pr_result["written"]:
            log.info(
                f"Nightly eod_pnl_r recalc: "
                f"{pr_result['written']} row(s) updated, "
                f"{pr_result['skipped']} skipped."
            )
    except Exception as exc:
        log.warning(f"Nightly eod_pnl_r recalculation failed: {exc}")

    # ── Backtest close-price catch-up sweep ────────────────────────────────────
    # backtest_sim_runs can also accumulate NULL close_price rows when the bot
    # was down or Alpaca data was unavailable.  Running the same look-back sweep
    # here keeps both tables self-healing without manual backfill_close_prices.py
    # invocations.  Window is controlled by BACKTEST_CLOSE_LOOKBACK_DAYS (default 60).
    try:
        log.info(
            f"EOD close-price catch-up sweep (backtest): "
            f"checking last {BACKTEST_CLOSE_LOOKBACK_DAYS} days…"
        )
        bcp_result = _eod_collect_close_prices_backtest(lookback_days=BACKTEST_CLOSE_LOOKBACK_DAYS)
        log.info(
            f"EOD close-price catch-up (backtest): "
            f"{bcp_result['written']} filled, {bcp_result['skipped']} skipped."
        )
    except Exception as exc:
        log.warning(f"EOD close-price catch-up sweep (backtest) failed: {exc}")

    # ── Stale-row warning: flag any backtest rows that are still NULL after N days ─
    # Rows that survive BACKTEST_STALE_THRESHOLD_DAYS without a close price are
    # unlikely to self-heal (e.g. Alpaca data permanently absent for that date).
    # This check runs after the sweep so the warning only fires for genuinely
    # persistent gaps, not rows that were just collected above.
    try:
        _check_stale_backtest_rows(threshold_days=BACKTEST_STALE_THRESHOLD_DAYS)
    except Exception as exc:
        log.warning(f"Stale backtest-row check failed: {exc}")

    # ── eod_pnl_r recalculation for newly-filled backtest close prices ─────────
    bpr_result: dict = {"written": 0, "skipped": 0}
    try:
        _t0_bpr = time.monotonic()
        bpr_result = _recalc_eod_pnl_r_recent_backtest()
        _elapsed_bpr = time.monotonic() - _t0_bpr
        log.info(
            f"EOD eod_pnl_r recalc (backtest): "
            f"{bpr_result.get('written', 0)} row(s) updated, "
            f"{bpr_result.get('skipped', 0)} skipped — "
            f"{_elapsed_bpr:.2f}s"
        )
    except Exception as exc:
        log.warning(f"EOD eod_pnl_r recalculation (backtest) failed: {exc}")

    # ── Count paper_trades breakout rows still missing eod_pnl_r after heal ──
    # Split into two buckets so the Telegram message is accurate:
    #   - no_price_count:  eod_pnl_r NULL AND close_price NULL  → awaiting price data
    #   - no_pnl_count:    eod_pnl_r NULL AND close_price NOT NULL → have price, still unfilled (anomaly)
    # None means the count query failed; used to avoid falsely reporting "fully filled".
    no_price_count: int | None = None
    no_pnl_count: int | None = None
    try:
        if _supabase_client:
            _np_total = 0
            _anomaly_total = 0
            for _direction in ("Bullish Break", "Bearish Break"):
                _resp_np = (
                    _supabase_client.table("paper_trades")
                    .select("id", count="exact")
                    .eq("user_id", USER_ID)
                    .eq("actual_outcome", _direction)
                    .is_("eod_pnl_r", "null")
                    .is_("close_price", "null")
                    .limit(1)
                    .execute()
                )
                _np_total += (_resp_np.count or 0)

                _resp_an = (
                    _supabase_client.table("paper_trades")
                    .select("id", count="exact")
                    .eq("user_id", USER_ID)
                    .eq("actual_outcome", _direction)
                    .is_("eod_pnl_r", "null")
                    .not_.is_("close_price", "null")
                    .limit(1)
                    .execute()
                )
                _anomaly_total += (_resp_an.count or 0)
            no_price_count = _np_total
            no_pnl_count = _anomaly_total
            log.info(
                f"Nightly EOD P&L heal: still-missing breakdown — "
                f"{no_price_count} awaiting close price, "
                f"{no_pnl_count} have price but no P&L (anomaly)."
            )
    except Exception as exc:
        log.warning(f"Still-missing eod_pnl_r count failed: {exc}")

    # ── Telegram summary: how many old paper trades had P&L healed overnight ──
    try:
        paper_written = pr_result.get("written", 0)
        backtest_written = bpr_result.get("written", 0)
        total_written = paper_written + backtest_written
        if total_written:
            log.info(
                f"Nightly EOD P&L heal summary: "
                f"{paper_written} paper-trade row(s) + "
                f"{backtest_written} backtest row(s) = "
                f"{total_written} total updated."
            )
            lines = ["🌙 <b>Nightly EOD P&amp;L Sweep</b>"]
            if paper_written:
                lines.append(f"  • Paper trades healed: <b>{paper_written}</b>")
            if backtest_written:
                lines.append(f"  • Backtest rows healed: <b>{backtest_written}</b>")
            lines.append(f"  • Total rows updated: <b>{total_written}</b>")
        else:
            log.info("Nightly EOD P&L heal summary: no rows needed healing — all P&L already filled.")
            lines = [
                "🌙 <b>Nightly EOD P&amp;L Sweep</b>",
                "  • No rows needed healing tonight — all P&amp;L already filled.",
            ]
        if no_price_count is None or no_pnl_count is None:
            lines.append("  ⚠️ Still-missing P&amp;L count unavailable (query failed).")
        elif no_price_count or no_pnl_count:
            if no_price_count:
                lines.append(
                    f"  ⚠️ Still missing P&amp;L: <b>{no_price_count}</b> row(s) awaiting close price."
                )
            if no_pnl_count:
                lines.append(
                    f"  ⚠️ Still missing P&amp;L: <b>{no_pnl_count}</b> row(s) have price but no P&amp;L computed."
                )
        else:
            lines.append("  ✅ All paper-trade P&amp;L rows are fully filled.")
        tg_send("\n".join(lines))
    except Exception as exc:
        log.warning(f"Nightly eod_pnl_r Telegram summary failed: {exc}")

    # ── Persist sweep result to disk so the dashboard can surface it ──────────
    try:
        import datetime as _dt
        import json as _json
        _sweep_payload = {
            "ran_at": _dt.datetime.utcnow().isoformat() + "Z",
            "paper_healed": paper_written,
            "backtest_healed": backtest_written,
            "total_healed": total_written,
        }
        import os as _os
        _default_sweep_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "eod_sweep_status.json"
        )
        _sweep_path = _os.environ.get("EOD_SWEEP_STATUS_PATH", _default_sweep_path)
        with open(_sweep_path, "w") as _sf:
            _json.dump(_sweep_payload, _sf)
        log.info(f"EOD sweep status written to {_sweep_path}")

        # Append to persistent history file next to deploy_server.py
        import os as _os
        _history_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)),
            "eod_sweep_history.json",
        )
        _history: list = []
        if _os.path.exists(_history_path):
            try:
                with open(_history_path) as _hf:
                    _history = _json.load(_hf)
                if not isinstance(_history, list):
                    _history = []
            except Exception:
                _history = []
        _history.append(_sweep_payload)
        with open(_history_path, "w") as _hf:
            _json.dump(_history, _hf)
        log.info(f"EOD sweep history appended to {_history_path} ({len(_history)} entries)")
    except Exception as exc:
        log.warning(f"Could not write EOD sweep status file: {exc}")


# ── Main loop ─────────────────────────────────────────────────────────────────
def _ensure_alpaca_columns() -> bool:
    """Add alpaca tracking columns to paper_trades if they are missing.

    Uses a SELECT probe to detect missing columns; logs the migration SQL and
    returns False if columns need to be added manually via Supabase SQL Editor.
    Returns True if all columns are present (or on any unexpected error — don't
    block startup over tracking columns).
    """
    if not _supabase_client:
        return True
    try:
        _supabase_client.table("paper_trades").select(
            "alpaca_order_id, alpaca_qty, order_placed_at, alpaca_fill_price"
        ).limit(1).execute()
        log.info("Alpaca order-tracking columns present ✅")
        return True
    except Exception as e:
        err = str(e).lower()
        if "column" in err or "does not exist" in err or "42703" in err:
            log.warning(
                "\n"
                "══════════════════════════════════════════════════════════\n"
                "  Alpaca tracking columns are MISSING in paper_trades.\n"
                "  Orders will still be placed — tracking just won't be logged.\n"
                "  To enable full tracking, go to Supabase → SQL Editor → run:\n\n"
                "  ALTER TABLE paper_trades\n"
                "    ADD COLUMN IF NOT EXISTS alpaca_order_id   TEXT,\n"
                "    ADD COLUMN IF NOT EXISTS alpaca_qty        INTEGER,\n"
                "    ADD COLUMN IF NOT EXISTS order_placed_at   TEXT,\n"
                "    ADD COLUMN IF NOT EXISTS alpaca_fill_price REAL;\n\n"
                "  Then restart the Paper Trader Bot workflow.\n"
                "══════════════════════════════════════════════════════════"
            )
            return False
        # Unknown error — don't block startup
        log.debug(f"_ensure_alpaca_columns check: {e}")
        return True


def _dispatch_scheduled_divergence_alert() -> None:
    """Read divergence_alert_state.json and dispatch the end-of-session divergence alert.

    Called once per trading day at ~4:35 PM ET from the main scheduling loop.
    This is the *autonomous* path — it fires even when no dashboard session is open,
    which satisfies the goal of risk managers receiving alerts without anyone being
    present at the dashboard.
    """
    import json as _json
    from datetime import date as _date

    _state_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "divergence_alert_state.json"
    )
    try:
        with open(_state_path) as _sf:
            _state = _json.load(_sf)
    except FileNotFoundError:
        log.info("[DivAlert] divergence_alert_state.json not found — no scheduled dispatch today")
        return
    except Exception as _e:
        log.warning(f"[DivAlert] Failed to read state file: {_e}")
        return

    if not _state.get("auto_send_enabled"):
        log.info("[DivAlert] Auto-send disabled in state file — skipping scheduled dispatch")
        return

    if _state.get("trigger_mode") != "Once per day":
        log.info(
            f"[DivAlert] Trigger mode is '{_state.get('trigger_mode')}' — "
            "bot only handles 'Once per day' dispatches"
        )
        return

    _today = _date.today().isoformat()

    # Guard: if we already sent today (e.g. after a mid-day bot restart) skip
    if _state.get("last_bot_sent_date") == _today:
        log.info(f"[DivAlert] Already dispatched today ({_today}) — skipping")
        return

    # Guard: require the state file to have been written today so we don't
    # dispatch stale data from a previous session
    _last_updated = str(_state.get("last_updated", ""))[:10]
    if _last_updated != _today:
        log.info(
            f"[DivAlert] State file is from {_last_updated or 'unknown date'} "
            f"(expected {_today}) — skipping to avoid stale dispatch"
        )
        return

    _flagged_rows = _state.get("flagged_rows", [])
    _n = len(_flagged_rows)
    if _n == 0:
        log.info("[DivAlert] No flagged tickers in today's state file — skipping scheduled dispatch")
        return

    _threshold = float(_state.get("threshold", 0.0))

    # Honour per-user Telegram credentials stored in the state file when present,
    # falling back to the global env-var token so existing deployments are unaffected.
    _user_tg_token   = str(_state.get("user_tg_token",   "") or "").strip()
    _user_tg_chat_id = str(_state.get("user_tg_chat_id", "") or "").strip()
    if _user_tg_token:
        log.info("[DivAlert] Using per-user Telegram token for scheduled dispatch")

    log.info(
        f"[DivAlert] Dispatching scheduled end-of-session divergence alert — "
        f"{_n} flagged ticker{'s' if _n != 1 else ''}, threshold={_threshold}"
    )
    try:
        _result = send_divergence_alert(
            flagged_rows=_flagged_rows,
            threshold=_threshold,
            tg_token=_user_tg_token,
            tg_chat_id=_user_tg_chat_id,
        )
        _sent   = [ch for ch, ok in _result.items() if ok]
        _failed = [ch for ch, ok in _result.items() if not ok]
        log.info(f"[DivAlert] Dispatch result — sent: {_sent}  failed: {_failed}")
        if _sent:
            _state["last_bot_sent_date"] = _today
            try:
                with open(_state_path, "w") as _sf:
                    _json.dump(_state, _sf, indent=2)
                log.info("[DivAlert] State file updated with today's sent date")
            except Exception as _we:
                log.warning(f"[DivAlert] Could not update state file after dispatch: {_we}")
    except Exception as _e:
        log.error(f"[DivAlert] Scheduled dispatch raised an exception: {_e}")


def main():
    log.info("EdgeIQ Paper Trader Bot starting up...")
    _load_screener_pass_cache()   # restore ticker→pass mapping that survived restart

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        log.error(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set as Replit Secrets. "
            "Go to the Secrets tab and add them, then restart this workflow."
        )
        return

    if TG_TOKEN and TG_CHAT_ID:
        log.info("Telegram alerts: ENABLED")
    else:
        log.warning("Telegram alerts: DISABLED (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set)")

    log.info(f"Watching {len(TICKERS)} tickers | TCS ≥ {MIN_TCS} | feed: {FEED.upper()}")
    log.info(
        f"Look-back windows — paper close: {PAPER_CLOSE_LOOKBACK_DAYS} days "
        f"(PAPER_CLOSE_LOOKBACK_DAYS) | backtest close: {BACKTEST_CLOSE_LOOKBACK_DAYS} days "
        f"(BACKTEST_CLOSE_LOOKBACK_DAYS)"
    )
    log.info(f"User: {USER_ID}")
    log.info(
        "Schedule: 9:10 AM ET → pre-market gap scan (SIP) | 9:35 AM ET → watchlist refresh | "
        "10:47 AM ET → morning scan | 11:45 AM ET → midday watchlist refresh | "
        "2:00 PM ET → intraday scan | 4:20 PM ET → EOD update | "
        "4:25 PM ET → auto-verify | 4:30 PM ET → recalibration | "
        "11:59 PM ET → PDF documentation export"
    )

    _table_ok = ensure_paper_trades_table()
    if not _table_ok:
        log.error(
            "\n"
            "══════════════════════════════════════════════════════════\n"
            "  paper_trades table is MISSING in your Supabase database.\n"
            "  Go to your Supabase project → SQL Editor → run:\n\n"
            "  CREATE TABLE IF NOT EXISTS paper_trades (\n"
            "    id SERIAL PRIMARY KEY,\n"
            "    user_id TEXT, trade_date DATE, ticker TEXT, tcs FLOAT,\n"
            "    predicted TEXT, ib_low FLOAT, ib_high FLOAT, open_price FLOAT,\n"
            "    actual_outcome TEXT, follow_thru_pct FLOAT, win_loss TEXT,\n"
            "    false_break_up BOOLEAN DEFAULT FALSE,\n"
            "    false_break_down BOOLEAN DEFAULT FALSE,\n"
            "    min_tcs_filter INT DEFAULT 50,\n"
            "    created_at TIMESTAMPTZ DEFAULT NOW()\n"
            "  );\n\n"
            "  Then restart the Paper Trader Bot workflow.\n"
            "══════════════════════════════════════════════════════════"
        )
        return

    # Ensure Alpaca order-tracking columns exist in paper_trades
    _ensure_alpaca_columns()

    # Log Alpaca activation status clearly
    acct_label = "PAPER (Alpaca paper-api)" if IS_PAPER_ALPACA else "⚠️  LIVE (real money)"
    if LIVE_ORDERS_ENABLED:
        log.info(f"Alpaca order placement: ENABLED ✅  [{acct_label}]")

        # ── Startup cooldown: suppress notification if bot restarted recently ──
        _start_flag = "/tmp/.edgeiq_last_start"
        _now_ts     = time.time()
        _last_ts    = 0.0
        try:
            if os.path.exists(_start_flag):
                _last_ts = float(open(_start_flag).read().strip())
        except Exception:
            pass
        _cooldown_secs = 600  # 10 minutes
        _suppress_startup_tg = (_now_ts - _last_ts) < _cooldown_secs
        try:
            with open(_start_flag, "w") as _f:
                _f.write(str(_now_ts))
        except Exception:
            pass

        if _suppress_startup_tg:
            log.info("[STARTUP] Recent restart detected — suppressing duplicate Telegram startup notification")
        else:
            # Compute the actual next scan time dynamically
            _now_et    = datetime.now(EASTERN)
            _hm        = _now_et.hour * 60 + _now_et.minute
            _is_market = _now_et.weekday() < 5
            if not _is_market or _hm >= 16 * 60:
                _next_scan = "10:47 AM ET tomorrow (morning IB close)"
            elif _hm < 10 * 60 + 47:
                _next_scan = "10:47 AM ET (morning IB close)"
            elif _hm < 14 * 60:
                _next_scan = "2:00 PM ET (intraday)"
            elif _hm < 15 * 60 + 45:
                _next_scan = "3:45 PM ET (EOD)"
            else:
                _next_scan = "10:47 AM ET tomorrow (morning IB close)"
            # For live accounts show live PDT + position status at startup
            _startup_extra = ""
            if not IS_PAPER_ALPACA:
                _pdt_blk, _pdt_n, _ = _check_pdt_guard()
                _pos_blk, _pos_n = _check_concurrent_positions_guard()
                _pdt_warn  = " 🚫 LIMIT REACHED" if _pdt_blk else ""
                _pos_warn  = " 🚫 AT CAP" if _pos_blk else ""
                # Equity floor check at startup
                _acct_s    = _alpaca_get("/v2/account")
                _equity_s  = float(_acct_s.get("equity", 0) or 0) if _acct_s else 0.0
                _floor_warn = ""
                if 0 < _equity_s < PDT_EQUITY_FLOOR:
                    _gap_s = PDT_EQUITY_FLOOR - _equity_s
                    _floor_warn = f"\n⚠️ Equity ${_equity_s:,.2f} — ${_gap_s:,.2f} below PDT floor (${PDT_EQUITY_FLOOR:,.0f})"
                    _warn_pdt_equity_floor(equity=_equity_s)
                _startup_extra = (
                    f"\n📊 PDT: <b>{_pdt_n}/{PDT_MAX_DAY_TRADES}</b> day trades used{_pdt_warn}"
                    f"\n📌 Positions: <b>{_pos_n}/{MAX_CONCURRENT_POSITIONS}</b> open{_pos_warn}"
                    f"{_floor_warn}"
                )
            tg_send(
                f"🟢 <b>EdgeIQ Paper Trader Bot started</b>\n"
                f"Alpaca bracket orders: <b>ACTIVE [{acct_label}]</b>\n"
                f"Next scan: {_next_scan}"
                f"{_startup_extra}"
            )
    else:
        log.info("Alpaca order placement: DISABLED (LIVE_ORDERS_ENABLED=false) — scanning/alerting only")

    # Ensure trade_journal has Telegram-logging columns
    ensure_telegram_columns()

    # Ensure cognitive delta log table exists
    ensure_cognitive_delta_table()

    # Ensure daily scan log table exists (for Finviz scan funnel visibility)
    ensure_daily_scan_log_table()

    # Restore trailing-stop guard set from DB so trades already converted to a
    # trailing stop before this restart are not re-triggered on the next loop.
    _restore_trailing_stop_guard()

    # Restore zone-invalidation watch from today's open paper_trades so orders
    # placed before this restart are still monitored for zone breaks.
    _restore_zone_watch_from_db()

    # Start Telegram listener in background daemon thread
    import threading as _threading
    _tg_thread = _threading.Thread(target=telegram_listener, daemon=True, name="TelegramListener")
    _tg_thread.start()
    log.info("Telegram listener thread started — send /log commands to the bot to log trades")

    _premarket_done        = False
    _watchlist_done        = False
    _midday_watchlist_done = False
    _morning_done          = False
    _intraday_done         = False
    _force_close_done      = False
    _eod_done              = False
    _verify_done           = False
    _recalibration_done    = False
    _pdf_export_done       = False
    _div_alert_done        = False

    # ── Startup catch-up: recover scans missed due to mid-day restart ─────────
    # Task merges / workflow restarts can happen during market hours.
    # Run any scheduled job that should have already fired today.
    _su = datetime.now(EASTERN)
    _su_hm = _su.hour * 60 + _su.minute          # minutes since midnight ET
    _su_weekday = _su.weekday() < 5
    if _su_weekday:
        if _su_hm >= 9 * 60 + 10:
            _premarket_done = True
            log.info("[Catch-up] Started after 9:10 AM — pre-market gap scan skipped (data stale)")
        if _su_hm >= 9 * 60 + 35 and not _watchlist_done:
            log.info("[Catch-up] Started after 9:35 AM — running watchlist refresh now...")
            try:
                watchlist_refresh()
            except Exception as _cue:
                log.warning(f"[Catch-up] Watchlist refresh failed: {_cue}")
            _watchlist_done = True
            # Clear stale S/R-tightened context on restart so yesterday's tightened
            # tickers never bleed into today's stop logic or P&L summary.
            _su_date = _su.strftime("%Y-%m-%d")
            _stale_sr_keys = [k for k in _TRAILING_STOP_SR_CONTEXT if k[1] != _su_date]
            for _k in _stale_sr_keys:
                del _TRAILING_STOP_SR_CONTEXT[_k]
            if _stale_sr_keys:
                log.info(
                    f"[Catch-up] Cleared {len(_stale_sr_keys)} stale S/R-tightened "
                    f"context entry(ies) from previous session(s) on restart."
                )
            # Capture session-open equity even on restart so the EOD P&L card has a baseline.
            try:
                if _su_date not in _SESSION_OPEN_EQUITY:
                    _su_eq = get_alpaca_account_equity(
                        is_paper=IS_PAPER_ALPACA,
                        api_key=ALPACA_API_KEY,
                        secret_key=ALPACA_SECRET_KEY,
                    )
                    if _su_eq is not None:
                        _SESSION_OPEN_EQUITY[_su_date] = _su_eq
                        log.info(f"[Catch-up] Session-open equity captured on restart: ${_su_eq:,.2f}")
            except Exception as _su_seq_err:
                log.warning(f"[Catch-up] Session-open equity capture failed: {_su_seq_err}")
        if 10 * 60 + 47 <= _su_hm < 14 * 60 and not _morning_done:
            log.info("[Catch-up] Started after 10:47 AM — running morning scan now...")
            try:
                morning_scan()
            except Exception as _cue:
                log.warning(f"[Catch-up] Morning scan failed: {_cue}")
            _morning_done = True
        if _su_hm >= 11 * 60 + 45:
            _midday_watchlist_done = True
        if 14 * 60 <= _su_hm < 16 * 60 and not _intraday_done:
            log.info("[Catch-up] Started after 2:00 PM — running intraday scan now...")
            try:
                intraday_scan()
            except Exception as _cue:
                log.warning(f"[Catch-up] Intraday scan failed: {_cue}")
            _intraday_done = True
        if _su_hm >= 16 * 60 + 20 and not _eod_done:
            log.info("[Catch-up] Past 4:20 PM — running EOD update (idempotent guard inside)...")
            try:
                eod_update()
            except Exception as _cue:
                log.warning(f"[Catch-up] EOD update failed: {_cue}")
            _eod_done = True
        if _su_hm >= 16 * 60 + 25 and not _verify_done:
            log.info("[Catch-up] Past 4:25 PM — running nightly verify (idempotent guard inside)...")
            try:
                nightly_verify()
            except Exception as _cue:
                log.warning(f"[Catch-up] Nightly verify failed: {_cue}")
            _verify_done = True
        if _su_hm >= 16 * 60 + 30 and not _recalibration_done:
            log.info("[Catch-up] Past 4:30 PM — running recalibration (idempotent guard inside)...")
            try:
                nightly_recalibration()
                update_daily_build_notes()
            except Exception as _cue:
                log.warning(f"[Catch-up] Recalibration failed: {_cue}")
            _recalibration_done = True
        if _su_hm >= 16 * 60 + 35:
            # Past 4:35 PM — attempt dispatch now; _dispatch_scheduled_divergence_alert
            # has its own idempotency guard (last_bot_sent_date) so it will skip safely
            # if the alert was already sent earlier today.
            log.info("[Catch-up] Past 4:35 PM — attempting divergence alert dispatch in case it was missed...")
            try:
                _dispatch_scheduled_divergence_alert()
            except Exception as _cue:
                log.warning(f"[Catch-up] Divergence alert dispatch failed (non-fatal): {_cue}")
            _div_alert_done = True
        log.info(f"[Catch-up] Done — premarket={_premarket_done} watchlist={_watchlist_done} "
                 f"morning={_morning_done} intraday={_intraday_done} eod={_eod_done}")
    # ─────────────────────────────────────────────────────────────────────────

    while True:
        now_et = datetime.now(EASTERN)
        today  = now_et.date()

        # Reset flags at midnight
        if now_et.hour == 0 and now_et.minute == 0:
            _premarket_done        = False
            _watchlist_done        = False
            _midday_watchlist_done = False
            _morning_done          = False
            _intraday_done         = False
            _force_close_done      = False
            _eod_done              = False
            _verify_done           = False
            _recalibration_done    = False
            _pdf_export_done       = False
            _div_alert_done        = False

        if not _market_is_open(now_et):
            # EOD outcome update — 4:20 PM ET (SIP free tier needs data >16 min old;
            # market close is 4:00 PM so the 4:00 PM bars are safe by 4:16 PM)
            if (
                not _eod_done
                and now_et.weekday() < 5
                and now_et.hour == 16
                and now_et.minute >= 20
            ):
                eod_update()
                _eod_done = True
            # 4:25 PM — auto-verify today's watchlist predictions
            # Runs AFTER EOD data is safe (SIP 16-min delay) and BEFORE recalibration
            # so the brain gets fresh verified signal in tonight's weight update.
            if (
                not _verify_done
                and now_et.weekday() < 5
                and now_et.hour == 16
                and now_et.minute >= 25
            ):
                nightly_verify()
                _verify_done = True
            # Recalibration runs after EOD outcomes + verify are written (4:30 PM ET)
            if (
                not _recalibration_done
                and now_et.weekday() < 5
                and now_et.hour == 16
                and now_et.minute >= 30
            ):
                nightly_recalibration()
                _recalibration_done = True
                try:
                    update_daily_build_notes()
                except Exception as _bne:
                    log.warning(f"Build notes update failed (non-fatal): {_bne}")

            # 4:35 PM — scheduled divergence alert dispatch
            # Fires even when no dashboard session is open so risk managers
            # receive the flagged-tickers CSV without anyone being present.
            if (
                not _div_alert_done
                and now_et.weekday() < 5
                and now_et.hour == 16
                and now_et.minute >= 35
            ):
                _dispatch_scheduled_divergence_alert()
                _div_alert_done = True

            # 9:10 AM — pre-market gap scanner (SIP, before Finviz refresh)
            if (
                not _premarket_done
                and now_et.weekday() < 5
                and now_et.hour == 9
                and now_et.minute >= 10
                and now_et.minute < 30
            ):
                premarket_scan()
                _premarket_done = True

            # 11:59 PM — regenerate PDF exports + update build notes (every day)
            if (
                not _pdf_export_done
                and now_et.hour == 23
                and now_et.minute >= 59
            ):
                log.info("11:59 PM — Regenerating PDF documentation exports...")
                try:
                    from generate_pdfs import generate_all_pdfs
                    results = generate_all_pdfs()
                    for r in results:
                        log.info(f"[PDF] {r}")
                except Exception as _pdfe:
                    log.warning(f"PDF export failed (non-fatal): {_pdfe}")
                try:
                    update_daily_build_notes()
                    log.info("11:59 PM — Build notes updated.")
                except Exception as _bne:
                    log.warning(f"Build notes update (11:59 PM) failed (non-fatal): {_bne}")
                _pdf_export_done = True

            time.sleep(60)
            continue

        # 9:35 AM — Finviz watchlist refresh (post-open so volume filters work)
        if (
            not _watchlist_done
            and now_et.hour == 9
            and now_et.minute >= 35
        ):
            watchlist_refresh()
            _watchlist_done = True
            # Clear stale S/R-tightened context from previous sessions so yesterday's
            # tightened tickers never bleed into today's P&L card or stop alerts.
            _today_str = str(today)
            stale_sr_keys = [k for k in _TRAILING_STOP_SR_CONTEXT if k[1] != _today_str]
            for _k in stale_sr_keys:
                del _TRAILING_STOP_SR_CONTEXT[_k]
            if stale_sr_keys:
                log.info(
                    f"[SessionOpen] Cleared {len(stale_sr_keys)} stale S/R-tightened "
                    f"context entry(ies) from previous session(s)."
                )
            # Capture session-open equity for the force-close P&L card.
            try:
                _eq = get_alpaca_account_equity(
                    is_paper=IS_PAPER_ALPACA,
                    api_key=ALPACA_API_KEY,
                    secret_key=ALPACA_SECRET_KEY,
                )
                if _eq is not None:
                    _SESSION_OPEN_EQUITY[str(today)] = _eq
                    log.info(f"[ForceClose] Session-open equity captured: ${_eq:,.2f}")
            except Exception as _seq_err:
                log.warning(f"[ForceClose] Could not capture session-open equity: {_seq_err}")

        # 10:47 AM — morning scan + Telegram alerts
        # (IB closes 10:30; SIP free tier needs >15 min delay → 10:47 is safe)
        if (
            not _morning_done
            and now_et.hour == 10
            and now_et.minute >= 47
        ):
            morning_scan()
            _morning_done = True

        # 11:45 AM — midday watchlist refresh
        # Catches late movers that weren't active at 9:15 AM open.
        # Adds fresh tickers to the watchlist so the 2:00 PM scan has more targets.
        if (
            not _midday_watchlist_done
            and now_et.hour == 11
            and now_et.minute >= 45
        ):
            log.info("Midday watchlist refresh — catching late movers for 2 PM scan")
            watchlist_refresh(midday=True)
            _midday_watchlist_done = True

        # 2:00 PM — intraday scan
        if (
            not _intraday_done
            and now_et.hour == 14
            and now_et.minute >= 0
        ):
            intraday_scan()
            _intraday_done = True

        # 3:30 PM — force-close all open positions before close-auction chaos
        # Avoids holding through the final 30 min where spreads widen and paper
        # fills become unpredictable.  Fires on weekdays only.
        if (
            not _force_close_done
            and now_et.weekday() < 5
            and now_et.hour == 15
            and now_et.minute >= 30
        ):
            log.info("[ForceClose] 3:30 PM — closing all open positions before EOD...")
            try:
                _force_close_all_positions()
            except Exception as _fce:
                log.warning(f"[ForceClose] Force-close sweep failed (non-fatal): {_fce}")
            # Clear zone watch — any remaining entries are stale (past entry window)
            with _ZONE_WATCH_LOCK:
                cleared = len(_ZONE_WATCH)
                _ZONE_WATCH.clear()
            if cleared:
                log.info(f"[ZoneWatch] EOD clear — removed {cleared} stale watch entry(s)")
            _force_close_done = True

        # 4:20 PM — EOD update (only reachable if market extended session; normally
        # handled in the after-close block above)
        if (
            not _eod_done
            and now_et.hour == 16
            and now_et.minute >= 20
        ):
            eod_update()
            _eod_done = True

        # 4:30 PM — brain recalibration (only reachable if market extended session)
        if (
            not _recalibration_done
            and now_et.hour == 16
            and now_et.minute >= 30
        ):
            nightly_recalibration()
            _recalibration_done = True
            try:
                update_daily_build_notes()
            except Exception as _bne:
                log.warning(f"Build notes update failed (non-fatal): {_bne}")

        # Every 30-second cycle — check if any open positions hit T1 and need
        # the bracket order swapped for a trailing stop.
        try:
            _monitor_trailing_stops()
        except Exception as _tse:
            log.warning(f"[TrailingStop] monitor error (non-fatal): {_tse}")

        # Every 5 minutes — cancel open entry orders whose IB zone has broken.
        try:
            _monitor_zone_invalidation()
        except Exception as _zwe:
            log.warning(f"[ZoneWatch] monitor error (non-fatal): {_zwe}")

        time.sleep(30)


if __name__ == "__main__":
    main()
