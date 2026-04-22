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
  PAPER_TRADE_MIN_TCS        — minimum TCS threshold (default: 60)
  PAPER_TRADE_FEED           — sip or iex (default: sip)
  PAPER_TRADE_PRICE_MIN      — min price filter (default: 1.0)
  PAPER_TRADE_PRICE_MAX      — max price filter (default: 50.0)
  SWEEP_ALERT_MAX_TICKERS    — max tickers shown in the close-price sweep Telegram alert (default: 10)
  BACKTEST_CLOSE_LOOKBACK_DAYS — how many calendar days the nightly backtest close-price sweep
                                 covers (default: 60)
  PAPER_CLOSE_LOOKBACK_DAYS    — how many calendar days the nightly paper-trades close-price sweep
                                 covers (default: 60); tune independently of the backtest sweep
  PDT_PRIORITY_TCS           — TCS floor applied only while account equity < $25k (default: 70).
                                 When PDT is active, only setups with TCS >= this value are traded.
                                 TCS≥70 (P1/P3 elite tier) avg 1.295R / 91% WR vs TCS<70 0.680R / 82%.
                                 Unlocks PDT ~8 weeks sooner. Set to 0 to disable.
  PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT — Pass 3 short-float threshold (default: 15.0).
                                 Must be one of 5, 10, 15, 20, 25, or 30 (Finviz breakpoints).
                                 Validated at startup; invalid values fall back to the nearest
                                 supported breakpoint and log an ERROR.
"""

import html
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
_PAPER_MIN_TCS    = int(os.getenv("PAPER_TRADE_MIN_TCS", "49"))   # paper mode floor — 49 is the lowest per-structure baseline (double_dist), letting tcs_thresholds.json govern each structure rather than a blanket override
_LIVE_MIN_TCS     = int(os.getenv("LIVE_MIN_TCS",        "70"))   # live real-money floor
FEED              = os.getenv("PAPER_TRADE_FEED", "sip")
PRICE_MIN               = float(os.getenv("PAPER_TRADE_PRICE_MIN", "1.0"))
PRICE_MAX               = float(os.getenv("PAPER_TRADE_PRICE_MAX", "50.0"))
SWEEP_ALERT_MAX_TICKERS      = int(os.getenv("SWEEP_ALERT_MAX_TICKERS", "10"))
BACKTEST_CLOSE_LOOKBACK_DAYS    = int(os.getenv("BACKTEST_CLOSE_LOOKBACK_DAYS", "60"))
BACKTEST_STALE_THRESHOLD_DAYS   = int(os.getenv("BACKTEST_STALE_THRESHOLD_DAYS", "3"))
PAPER_CLOSE_LOOKBACK_DAYS       = int(os.getenv("PAPER_CLOSE_LOOKBACK_DAYS", "60"))

# ── Pass 3 (short-squeeze) screener constants ──────────────────────────────────
# Maps numeric short-float thresholds → Finviz filter codes (sh_short_o{N}).
# Add entries here whenever Finviz introduces a new breakpoint.
_SHORT_FLOAT_FILTER_MAP: dict[float, str] = {
    5.0:  "sh_short_o5",
    10.0: "sh_short_o10",
    15.0: "sh_short_o15",
    20.0: "sh_short_o20",
    25.0: "sh_short_o25",
    30.0: "sh_short_o30",
}
# Configurable via env var — must be a key in _SHORT_FLOAT_FILTER_MAP.
_PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT: float = float(
    os.getenv("PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT", "15.0")
)

# ── Startup validation: catch misconfigured short-float threshold immediately ──
# tg_send() is defined later in the file, so we store the alert message here
# and dispatch it via watchlist_refresh() on first run instead of calling
# tg_send() directly at module level.
_SHORT_FLOAT_FALLBACK_TG_MSG: str | None = None
if _PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT not in _SHORT_FLOAT_FILTER_MAP:
    _supported_vals = sorted(_SHORT_FLOAT_FILTER_MAP)
    _nearest_val = min(_supported_vals, key=lambda v: abs(v - _PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT))
    log.error(
        f"[startup] PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT={_PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT}% "
        f"is not a supported Finviz breakpoint. "
        f"Supported values: {_supported_vals}. "
        f"Falling back to nearest supported value: {_nearest_val}%."
    )
    _SHORT_FLOAT_FALLBACK_TG_MSG = (
        f"⚠️ Short-float threshold misconfiguration\n"
        f"Configured value {_PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT}% is not a supported Finviz breakpoint.\n"
        f"Falling back to nearest supported value: {_nearest_val}%.\n"
        f"Supported breakpoints: {_supported_vals}"
    )
    _PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT = _nearest_val

_USER_PREFS_FILE_PATH        = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".local", "user_prefs.json")
_OWNER_USER_ID               = os.getenv("OWNER_USER_ID", "").strip() or "anonymous"
_DRAWDOWN_ALERT_STATE_PATH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".local", "drawdown_alert_state.json")
_ORDERGUARD_ALERTS_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".local", "orderguard_alerts.json")
_ORDERGUARD_ALERTS_MAX       = 50


def _orderguard_append_event(ticker: str, reason: str, timestamp: str) -> None:
    """Append an OrderGuard block event to .local/orderguard_alerts.json so the
    dashboard can surface it without requiring Telegram access."""
    import json as _json
    import threading as _th

    event = {"ticker": ticker, "reason": reason, "timestamp": timestamp}
    _lock = getattr(_orderguard_append_event, "_lock", None)
    if _lock is None:
        _orderguard_append_event._lock = _th.Lock()
        _lock = _orderguard_append_event._lock
    with _lock:
        try:
            if os.path.exists(_ORDERGUARD_ALERTS_FILE):
                with open(_ORDERGUARD_ALERTS_FILE) as _f:
                    events = _json.load(_f)
                if not isinstance(events, list):
                    events = []
            else:
                events = []
            events.append(event)
            if len(events) > _ORDERGUARD_ALERTS_MAX:
                events = events[-_ORDERGUARD_ALERTS_MAX:]
            os.makedirs(os.path.dirname(_ORDERGUARD_ALERTS_FILE), exist_ok=True)
            with open(_ORDERGUARD_ALERTS_FILE, "w") as _f:
                _json.dump(events, _f)
        except Exception as _exc:
            log.warning("[OrderGuard] could not write alert file: %s", _exc)

# ── IB Context Enrichment toggle ──────────────────────────────────────────────
# When enabled (IB_CONTEXT_ENABLED=1) the bot fetches the prior day's IB range
# and pre-market (4:00–9:30 AM) range for each ticker at scan time and uses
# them to apply a ±0–5 TCS adjustment.  Off by default so existing behaviour
# is completely unchanged until you are ready to compare results.
IB_CONTEXT_ENABLED = os.getenv("IB_CONTEXT_ENABLED", "0").strip() == "1"

# ── Adaptive Position Management toggle ───────────────────────────────────────
# When enabled (ADAPTIVE_POSITION_MGMT=1) the bot runs _pre_open_position_review
# at 8:30 AM ET before the regular premarket scan.  For each open Alpaca paper
# position it reads the IB levels stored at order time and the current pre-market
# price, then either raises the TP by +0.5R (when PM accepted past the IB break
# level) or tightens the stop to IB mid (when PM has pulled back inside the IB).
# Adjustments are recorded on the paper_trades row (mgmt_mode='adaptive',
# tp_adjusted_r) so you can run A/B comparisons vs the fixed-bracket baseline.
# Off by default — existing behaviour is completely unchanged when 0.
ADAPTIVE_POSITION_MGMT = os.getenv("ADAPTIVE_POSITION_MGMT", "0").strip() == "1"

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


_SQUEEZE_SHORT_FLOAT_BREAKPOINTS: tuple[float, ...] = (5.0, 10.0, 15.0, 20.0, 25.0, 30.0)
_SQUEEZE_SHORT_FLOAT_DEFAULT_PCT: float = 15.0


def _get_effective_squeeze_short_float_pct() -> float:
    """Return the active Pass 3 short-float threshold (percent).

    Checks the dashboard prefs store (.local/user_prefs.json, written by
    deploy_server.py) for a 'squeeze_short_float_pct' override first.
    Falls back to _SQUEEZE_SHORT_FLOAT_DEFAULT_PCT (15%) when no override
    is present, the file cannot be read, or the stored value is not a
    recognised Finviz breakpoint.
    """
    try:
        import json as _json
        if os.path.exists(_USER_PREFS_FILE_PATH):
            with open(_USER_PREFS_FILE_PATH) as _f:
                all_prefs = _json.load(_f)
            owner_prefs = all_prefs.get(_OWNER_USER_ID, {})
            if "squeeze_short_float_pct" in owner_prefs:
                val = float(owner_prefs["squeeze_short_float_pct"])
                if val in _SQUEEZE_SHORT_FLOAT_BREAKPOINTS:
                    return val
    except Exception:
        pass
    return _SQUEEZE_SHORT_FLOAT_DEFAULT_PCT


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
# Notional position size as a fraction of account equity.
# Default 20%: at $10k live start → $2,000/trade; compounds automatically as equity grows.
# Raise above $25k (e.g. NOTIONAL_PCT=0.40) to let the 2.1% risk formula start driving instead.
# Live hard floor: never go below $1,500 notional regardless of equity.
NOTIONAL_PCT            = float(os.getenv("NOTIONAL_PCT", "0.20"))    # fraction of equity per trade
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
# Cooldown between repeated OrderGuard Telegram alerts for the same ticker (seconds) — default 5 min.
# Only the first alert within each window is sent; the log warning is always written.
ORDERGUARD_ALERT_COOLDOWN = int(os.getenv("ORDERGUARD_ALERT_COOLDOWN", "300"))
# PDT quality gate: while equity < $25k, only spend PDT slots on TCS >= this value.
# TCS≥70 (P1/P3 elite tier): 1.295R avg, 91.4% WR — vs TCS<70: 0.680R avg, 81.9% WR.
# Prioritising elite setups during the PDT phase reaches the $25k unlock ~8 weeks sooner
# and adds ~$1.4M to Year-1 compounding vs running all S2 signals.  Set to 0 to disable.
PDT_PRIORITY_TCS         = int(os.getenv("PDT_PRIORITY_TCS", "70"))
# Slippage tolerance: if price has just crossed the IB level by ≤ this many %,
# switch to a market-order entry instead of skipping.  This handles the "price
# is $0.01 above IB high" case — the breakout just happened, fill at market.
# Set to 0 to disable (always skip if price already crossed).
SLIPPAGE_TOLERANCE_PCT = float(os.getenv("SLIPPAGE_TOLERANCE_PCT", "1.5"))
# Anti-chasing threshold (live mode only): if the current 5-min bar close
# is already this many % past the entry price (and beyond the slippage tolerance),
# skip placing the bracket because the setup has already run away.
ENTRY_ALREADY_TRIGGERED_PCT = float(os.getenv("ENTRY_ALREADY_TRIGGERED_PCT", "1.5"))
# Tracks the date a PDT-block Telegram alert was last sent — prevents N duplicate
# alerts when N setups all hit the same block in one scan session.
_PDT_BLOCK_ALERTED_DATE: "date | None" = None
# Tracks whether the PDT gate was ever observed to be in effect (True) so that the
# "gate lifted" notification only fires on a genuine sub-$25k → above-$25k transition,
# never on accounts that started above the threshold.
_PDT_WAS_IN_EFFECT: "bool | None" = None
# Set to True after the one-time "PDT Gate Lifted" Telegram message is sent.
_PDT_LIFTED_ALERTED: bool = False

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
        fetch_gap_down_watchlist,
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
        cancel_alpaca_ticker_orders,
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
        check_tcs_intraday_rolling_wr,
    )
except ImportError as e:
    log.error(f"Cannot import backend: {e}")
    raise


# ── Per-structure TCS thresholds (calibrated nightly) ─────────────────────────
def _struct_tcs_floor(r: dict, tcs_thresholds: dict, regime_floor: int) -> int:
    """Return the effective TCS floor for a scan result.

    For INTRADAY scans: returns max(tcs_intraday_min, regime_floor).
    tcs_intraday_min is read from filter_config.json (default = MIN_TCS=49).
    Setting tcs_intraday_min=35 in filter_config unlocks the 35-49 TCS band
    for intraday setups; hist data shows TCS>=35 intraday = 98% WR (107 trades)
    vs 100% WR at TCS>=50 — nearly identical edge with 2x the trade volume.
    Per-structure calibration is bypassed for intraday as before.
    LIVE_MIN_TCS (70) is always the hard floor in live mode regardless of this.

    For MORNING / EOD scans: uses calibrated per-structure threshold from
    tcs_thresholds.json (written nightly by the recalibration), then applies
    the macro regime adjustment on top.  Falls back to MIN_TCS if no match.
    The separate MORNING_TCS_FLOOR=60 hard gate in _place_order_for_setup
    acts as the primary morning filter independent of this function.
    """
    scan_type = str(r.get("scan_type") or "").lower()
    if scan_type == "intraday":
        # Use filter_config tcs_intraday_min if set; fall back to global MIN_TCS.
        # Never let paper-mode config override the live-money floor.
        try:
            _flt = _load_filter_config()
            _intraday_floor = int(_flt.get("tcs_intraday_min", MIN_TCS))
        except Exception:
            _intraday_floor = MIN_TCS
        effective = max(_intraday_floor, regime_floor)
        if IS_PAPER_ALPACA:
            return effective
        # Live mode: never go below _LIVE_MIN_TCS regardless of filter_config
        return max(effective, _LIVE_MIN_TCS)
    predicted = str(r.get("predicted") or "").strip()
    wk        = label_to_weight_key(predicted) if predicted else ""
    cal_tcs   = tcs_thresholds.get(wk, MIN_TCS) if wk else MIN_TCS
    # regime_floor already incorporates tcs_adj; combine with calibrated threshold
    # take the higher of the two so macro fear never lets a weak structure through
    return max(cal_tcs, regime_floor)


# ── Dynamic position sizing ────────────────────────────────────────────────────
def _compute_risk_dollars() -> float:
    """Return 2.1% of current Alpaca account equity, capped at $4,000.

    The $4,000 cap aligns with the Phase 3 projection model's 20× compounding
    ceiling: 10% of $2,000 position × 20× = $4,000/trade max.  This keeps
    the live bot's compound curve consistent with the projection table and
    still has negligible market impact on liquid gapping stocks.

    Falls back to RISK_PER_TRADE env var if account fetch fails.
    """
    equity = get_alpaca_account_equity(
        is_paper   = IS_PAPER_ALPACA,
        api_key    = ALPACA_API_KEY,
        secret_key = ALPACA_SECRET_KEY,
    )
    if equity and equity > 0:
        dynamic = equity * 0.021         # 2.1% of account
        risk    = min(dynamic, 4000.0)   # cap $4,000 (matches 20× projection model ceiling)
        log.info(f"  Account equity: ${equity:,.0f} → 2.1% = ${dynamic:,.0f} → risk/trade: ${risk:,.0f}")
        return risk
    log.warning(f"  Could not fetch account equity — using fallback ${RISK_PER_TRADE:.0f}/trade")
    return RISK_PER_TRADE


def _compute_trade_notional() -> float:
    """Return the target notional (dollars deployed) per trade.

    Live mode: always returns the fixed $1,500 live cap — equity-scaled notional
    is paper-only until the user explicitly goes live and adjusts the cap.

    Paper mode formula: equity × NOTIONAL_PCT (default 20%).
    At $10k live start → $2,000/trade; at $95k paper → $19,000/trade.
    Compounds automatically — as equity grows, so does per-trade size.
    Floor: $500 (avoids 1-share rounding on tiny test balances).

    Raise NOTIONAL_PCT via env var (e.g. 0.40) above $25k to let the 2.1%
    risk formula start driving instead of the notional cap.

    Falls back to NOTIONAL_PCT × $100,000 if account equity cannot be fetched.
    """
    if not IS_PAPER_ALPACA:
        # Live mode: fixed $1,500 cap until user is ready to scale up.
        log.info("  Live mode — notional capped at $1,500")
        return 1500.0

    equity = get_alpaca_account_equity(
        is_paper   = IS_PAPER_ALPACA,
        api_key    = ALPACA_API_KEY,
        secret_key = ALPACA_SECRET_KEY,
    )
    if equity and equity > 0:
        notional = max(equity * NOTIONAL_PCT, 500.0)
        log.info(
            f"  Account equity: ${equity:,.0f} → "
            f"{NOTIONAL_PCT*100:.0f}% notional = ${notional:,.0f}/trade"
        )
        return notional
    fallback = max(NOTIONAL_PCT * 100_000, 500.0)
    log.warning(f"  Could not fetch account equity — notional fallback ${fallback:,.0f}/trade")
    return fallback


# ── P1–P4 tier priority ordering ───────────────────────────────────────────────
# Expected R per tier (5-yr backtest, 33,773 rows, April 2026):
#   P3: Morning  TCS≥70   → +6.102R / 79.7% WR  (67/yr  — NEVER miss)
#   P1: Intraday TCS≥70   → +3.715R / 89.8% WR  (631/yr)
#   P2: Intraday TCS50-69 → +1.265R / 74.8% WR  (979/yr — take with 1.0× size)
#   P4: Morning  TCS55-69 → +0.366R / 36.9% WR  (203/yr — marginal but +EV)
#          ↑ floor lowered 60→55 (Task #2036): TCS 55-59 morning = 85.4% WR / 882 trades
#   BLOCKED: Morning TCS<55 → negative expectancy (do not trade; floor via filter_config)
_TIER_EXPECTED_R = {
    "P3": 6.102,
    "P1": 3.715,
    "P2": 1.265,
    "P4": 0.366,
}

# ── Morning-scan TCS floor ──────────────────────────────────────────────────────
# Env var default stays 60 for backward compat. filter_config.json morning_tcs_min
# overrides this value at the gate in _place_order_for_setup (Task #2036, 2026-04-22).
# Hist (backtest_sim_runs, 5-yr): morning TCS 55-59 = 85.4% WR / 882 trades.
#   TCS 55: 83.5% / 321 | TCS 56: 85.4% / 137 | TCS 57: 90.7% / 194 | TCS 58: 83.4% / 229
# Morning TCS 60-69 → +0.366R / 36.9% WR (203/yr) — positive, take it.
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
# the $4,000 risk cap enforced in _compute_risk_dollars).
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
# Derived from live backtest data (calibrate_sp_mult.py, 2021-2026):
#   'other'  (< 3% daily change, same screener pool): 87% WR / +0.622R avg → 1.15×
#   'gap'    (≥ 3% daily change):                      65% WR / +0.327R avg → 1.00×
#   'trend'  (1-3% + above SMA20/50):                  only 12 trades       → 0.85×
#   'gap_down' (Bearish Break, ≥3% gap-down universe): calibrated 2026-04-20
#              via `python calibrate_sp_mult.py --pass gap_down`; 6 settled
#              gap_down Bearish Break trades in paper_trades — insufficient for
#              deviation (min 30); 1.00× is the data-confirmed baseline.
#              Re-run the script once ≥30 gap_down rows have tiered_pnl_r
#              populated; it will print the exact line to paste here.
#   'squeeze' (2026-04-21 → 2026-04-22): 36 trades, 88.9% WR / +0.009R avg → 0.70×
# Applied AFTER IB-range, RVOL and P-tier mults as a final expectancy layer.
_SP_MULT_TABLE: dict[str, float] = {
    "other":    1.15,
    "gap":      1.00,
    "trend":    0.85,
    "gap_down": 1.00,   # Bearish Break universe — calibrated 2026-04-20 (6 settled trades, n<30 → baseline confirmed); re-run calibrate_sp_mult.py --pass gap_down once ≥30 settle
    "squeeze":  0.70,   # 36 trades 2026-04-21 → 2026-04-22, 88.9% WR / +0.009R → 0.70×
}

_SP_CALIB_DATES: dict[str, str] = {
    "gap_down": "2026-04-20",
    "squeeze":  "2026-04-22",
}

def _sp_size_mult(screener_pass: str | None) -> float:
    """Return position-size multiplier for a given screener_pass label.

    Derived from live backtest calibration (calibrate_sp_mult.py, 2021-2026).
    'other' stocks (smaller-move days, tighter IB) consistently outperform
    'gap' stocks on every metric in every year — 87% vs 65% WR — because
    smaller gaps produce cleaner, less volatile initial balance structures.
    'gap_down' (Bearish Break) calibrated 2026-04-20 → 1.00× baseline.
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

# When PM_IB_FILTER=1, the bot fetches the pre-market session range (4:00–9:30 AM ET)
# and gates entries on whether price accepted past the prior day's IB in the trade
# direction. Bullish: pm_high >= prev_ib_high. Bearish: pm_low <= prev_ib_low.
# Setups where PM_IB data is unavailable are passed through (defensive default).
PM_IB_FILTER = os.getenv("PM_IB_FILTER", "0").strip() == "1"

# ── filter_config.json — optimizer-derived entry gates ─────────────────────────
_FILTER_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "filter_config.json")
_FILTER_CONFIG_CACHE: dict | None = None

def _load_filter_config() -> dict:
    """Load filter_config.json, written by the Filter Optimizer in the dashboard.

    Returns a dict with keys:
        tcs_offset        int   — extra TCS points above per-structure baseline (default 0)
        tcs_intraday_min  int   — intraday TCS floor, overrides MIN_TCS for intraday scans only
                                  (default = MIN_TCS = 49 paper; live always uses _LIVE_MIN_TCS=70)
                                  Set to 35 to unlock 35-49 TCS band: 98% WR on 107 intraday trades.
        rvol_min          float — minimum RVOL (stacks on top of RVOL_MIN_FLOOR; default 0)
        gap_min           float — abs(gap_pct) must be >= this % (default 0)
        follow_min_pct    float — follow_thru_pct must be >= this (default -999 = off)
        struct_filter     str   — "all"|"neutral"|"trend"|"extreme"|"no_extreme" (default "all")
        excl_false_break  bool  — skip rows with false_break_up or false_break_down (default False)
        pm_range_floor    float — pm_range_pct must be >= this % (0 = off, default 0)
        pm_ib_dir         str   — "any"|"bullish_accepted"|"bearish_accepted" (default "any")
                                  bullish_accepted: pm_ib_high > prev_day_ib_high
                                  bearish_accepted: pm_ib_low  < prev_day_ib_low

    Falls back to all-permissive defaults when the file is absent or unreadable,
    preserving full backward compatibility.
    """
    global _FILTER_CONFIG_CACHE
    try:
        if not os.path.exists(_FILTER_CONFIG_PATH):
            return {}
        mtime = os.path.getmtime(_FILTER_CONFIG_PATH)
        if _FILTER_CONFIG_CACHE is not None:
            if abs(mtime - _FILTER_CONFIG_CACHE.get("_mtime", 0)) < 1:
                return _FILTER_CONFIG_CACHE
        with open(_FILTER_CONFIG_PATH) as _f:
            cfg = json.load(_f)
        cfg["_mtime"] = mtime
        _FILTER_CONFIG_CACHE = cfg
        return cfg
    except Exception as _fce:
        log.debug(f"filter_config.json not loaded (non-fatal): {_fce}")
        return {}

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

def _fetch_prev_ib(ticker: str, trade_date_str: str) -> tuple:
    """Return (prev_ib_high, prev_ib_low) for the prior trading session.

    First queries Supabase paper_trades for the most recent prior row with
    valid IB data for this ticker. Falls back to Alpaca daily bars (the
    prior session's high/low as a proxy) if Supabase has no record.
    Returns (0.0, 0.0) when no data is available.
    """
    from datetime import datetime, timedelta
    import requests as _req

    _look_back_start = (datetime.strptime(trade_date_str, '%Y-%m-%d') - timedelta(days=10)).strftime('%Y-%m-%d')

    if _supabase_client:
        try:
            rows = (
                _supabase_client.table("paper_trades")
                .select("ib_high, ib_low, trade_date")
                .eq("ticker", ticker)
                .lt("trade_date", trade_date_str)
                .gte("trade_date", _look_back_start)
                .order("trade_date", desc=True)
                .limit(1)
                .execute()
                .data or []
            )
            if rows:
                _h = float(rows[0].get("ib_high") or 0)
                _l = float(rows[0].get("ib_low") or 0)
                if _h > _l > 0:
                    return _h, _l
        except Exception as _e:
            log.debug(f"[IB Context] Supabase prev-IB lookup failed for {ticker}: {_e}")

    # Alpaca daily-bar fallback — treat prior session high/low as IB proxy
    _start = (datetime.strptime(trade_date_str, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
    _end   = (datetime.strptime(trade_date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    try:
        r = _req.get(
            f'https://data.alpaca.markets/v2/stocks/{ticker}/bars',
            headers={'APCA-API-KEY-ID': ALPACA_API_KEY, 'APCA-API-SECRET-KEY': ALPACA_SECRET_KEY},
            params={'start': _start, 'end': _end, 'timeframe': '1Day', 'feed': 'iex', 'limit': 5},
            timeout=10,
        )
        if r.status_code == 200:
            _bars = r.json().get('bars') or []
            if _bars:
                return float(_bars[-1]['h']), float(_bars[-1]['l'])
    except Exception as _e:
        log.debug(f"[IB Context] Alpaca prev-day fallback failed for {ticker}: {_e}")

    return 0.0, 0.0


def _fetch_premarket_range(
    ticker: str, trade_date_str: str, open_px: float
) -> tuple:
    """Return (pm_range_pct, pm_high, pm_low) for the 4:00–9:30 AM ET window.

    Fetches 1-minute SIP bars from Alpaca. pm_range_pct is (high−low)/open×100.
    pm_high and pm_low are the absolute price extremes — used to check whether
    pre-market accepted above/below the prior day's IB levels.
    Returns (0.0, 0.0, 0.0) when no data is available or open_px is zero.
    """
    if open_px <= 0:
        return (0.0, 0.0, 0.0)
    import requests as _req
    from datetime import datetime
    ET = pytz.timezone('America/New_York')
    dt    = datetime.strptime(trade_date_str, '%Y-%m-%d')
    start = ET.localize(dt.replace(hour=4, minute=0)).isoformat()
    end   = ET.localize(dt.replace(hour=9, minute=30)).isoformat()
    try:
        r = _req.get(
            f'https://data.alpaca.markets/v2/stocks/{ticker}/bars',
            headers={'APCA-API-KEY-ID': ALPACA_API_KEY, 'APCA-API-SECRET-KEY': ALPACA_SECRET_KEY},
            params={'start': start, 'end': end, 'timeframe': '1Min', 'feed': 'sip', 'limit': 400},
            timeout=10,
        )
        if r.status_code == 200:
            _bars = r.json().get('bars') or []
            if _bars:
                _pm_high = max(b['h'] for b in _bars)
                _pm_low  = min(b['l'] for b in _bars)
                _pct     = round((_pm_high - _pm_low) / open_px * 100, 4)
                return (_pct, _pm_high, _pm_low)
    except Exception as _e:
        log.debug(f"[IB Context] Pre-market range fetch failed for {ticker}: {_e}")
    return (0.0, 0.0, 0.0)


def _ib_context_score_delta(
    today_ib_range: float,
    prev_ib_range: float,
    pm_range_pct: float,
    direction: str,
    pm_high: float,
    pm_low: float,
    prev_ib_high: float,
    prev_ib_low: float,
) -> int:
    """Compute TCS adjustment (−5 to +5) from IB context. Returns 0 when disabled.

    Compression day (today IB < 70% of yesterday):
      • pre-market accepted past prior IB in the trade direction  →  +5
          Bullish: pm_high >= prev_ib_high  (market pushed above prior IB)
          Bearish: pm_low  <= prev_ib_low   (market pushed below prior IB)
      • pre-market contained entirely within prior IB range       →  +3
          (pm_high < prev_ib_high AND pm_low > prev_ib_low)
      • no useful pre-market data (pm_range_pct == 0)             →  +2
    Expansion already underway (today IB > 130% of yesterday):
      → −3  (high uncertainty; fade vs continuation unclear)
    Large pre-market range penalty (pm_range_pct ≥ 2 %):
      → additional −2  (chaotic overnight action)
    Result is clamped to [−5, +5].
    """
    if not IB_CONTEXT_ENABLED:
        return 0
    if today_ib_range <= 0 or prev_ib_range <= 0:
        return 0

    ratio = today_ib_range / prev_ib_range
    delta = 0

    if ratio < 0.70:
        # Compression — assess pre-market acceptance vs prior IB structure
        _have_pm = pm_range_pct > 0 and pm_high > 0 and prev_ib_high > 0 and prev_ib_low > 0

        if not _have_pm:
            # No pre-market data → base compression bonus
            delta = 2
        else:
            # Check directional acceptance past prior IB boundary
            _pm_accepted_direction = False
            if direction == "Bullish Break":
                _pm_accepted_direction = pm_high >= prev_ib_high
            elif direction == "Bearish Break":
                _pm_accepted_direction = pm_low <= prev_ib_low

            # Check containment: pre-market entirely inside prior IB
            _pm_inside_prev = (pm_high < prev_ib_high) and (pm_low > prev_ib_low)

            if _pm_accepted_direction:
                delta = 5
            elif _pm_inside_prev:
                delta = 3
            else:
                delta = 2

    elif ratio > 1.30:
        delta = -3

    # Chaotic pre-market penalty
    if pm_range_pct >= 2.0:
        delta -= 2

    return max(-5, min(5, delta))


def _enrich_with_ib_context(results: list, trade_date_str: str) -> None:
    """Enrich each result dict with IB context fields and apply TCS delta in-place.

    Skips silently when IB_CONTEXT_ENABLED is False. For each ticker, fetches
    prev-day IB and pre-market range, stores four new fields on the dict, and
    adjusts tcs by the ±0–5 context delta.

    New fields added to each result:
        prev_ib_high      — prior session IB high (or None)
        prev_ib_low       — prior session IB low  (or None)
        pm_range_pct      — pre-market range as % of open (or None)
        ib_vs_prev_ib_pct — today IB range / yesterday IB range × 100 (or None)
    """
    if not IB_CONTEXT_ENABLED:
        return
    log.info(f"[IB Context] Enriching {len(results)} result(s) for {trade_date_str}...")
    for r in results:
        ticker        = r.get("ticker", "")
        open_px       = float(r.get("open_price") or 0)
        ib_high       = float(r.get("ib_high") or 0)
        ib_low        = float(r.get("ib_low") or 0)
        direction     = r.get("predicted", "")
        today_ib_range = ib_high - ib_low if ib_high > ib_low > 0 else 0.0
        try:
            prev_ib_high, prev_ib_low = _fetch_prev_ib(ticker, trade_date_str)
            time.sleep(0.15)
            pm_range_pct, pm_high, pm_low = _fetch_premarket_range(ticker, trade_date_str, open_px)
            time.sleep(0.15)
        except Exception as _e:
            log.warning(f"[IB Context] Enrichment error for {ticker}: {_e}")
            prev_ib_high, prev_ib_low = 0.0, 0.0
            pm_range_pct, pm_high, pm_low = 0.0, 0.0, 0.0

        prev_ib_range     = prev_ib_high - prev_ib_low if prev_ib_high > prev_ib_low > 0 else 0.0
        ib_vs_prev_ib_pct = round(today_ib_range / prev_ib_range * 100, 2) if prev_ib_range > 0 else None

        r["prev_ib_high"]      = round(prev_ib_high, 4) if prev_ib_high else None
        r["prev_ib_low"]       = round(prev_ib_low,  4) if prev_ib_low  else None
        r["pm_range_pct"]      = round(pm_range_pct, 4) if pm_range_pct else None
        r["ib_vs_prev_ib_pct"] = ib_vs_prev_ib_pct
        r["_pm_ib_high"]       = pm_high   # in-memory only; used by filter_config PM-IB direction gate
        r["_pm_ib_low"]        = pm_low    # in-memory only; used by filter_config PM-IB direction gate

        _delta = _ib_context_score_delta(
            today_ib_range=today_ib_range,
            prev_ib_range=prev_ib_range,
            pm_range_pct=pm_range_pct or 0.0,
            direction=direction,
            pm_high=pm_high,
            pm_low=pm_low,
            prev_ib_high=prev_ib_high,
            prev_ib_low=prev_ib_low,
        )
        if _delta != 0:
            _old_tcs = float(r.get("tcs", 0))
            r["tcs"] = round(_old_tcs + _delta, 1)
            log.info(
                f"  [{ticker}] IB ctx {_delta:+d} pt(s): "
                f"ratio={ib_vs_prev_ib_pct}, pm={pm_range_pct:.2f}% | "
                f"TCS {_old_tcs:.0f} → {r['tcs']:.0f}"
            )


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


def _check_pdt_guard() -> tuple[bool, int, "bool | None"]:
    """Return (blocked, daytrade_count, pdt_in_effect).

    blocked        — True when daytrade_count >= PDT_MAX_DAY_TRADES (live + <$25k only).
    daytrade_count — rolling 5-day count from Alpaca (0 for paper accounts).
    pdt_in_effect  — True  when account is live AND equity < $25k (PDT rules actively apply).
                     False when account is live AND equity >= $25k (no restriction).
                     None  when the Alpaca account fetch failed — state is unknown; callers
                           must not treat None as a confirmed crossing in either direction.
    Paper accounts are never blocked — PDT is a real-money brokerage rule.
    """
    if IS_PAPER_ALPACA:
        return False, 0, False
    acct = _alpaca_get("/v2/account")
    if not acct:
        log.warning("[PDT guard] Could not fetch account info — allowing order through")
        return False, 0, None  # unknown state — do not infer a crossing in either direction
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


# Per-ticker timestamps of the last OrderGuard Telegram alert (epoch seconds).
# Used to enforce ORDERGUARD_ALERT_COOLDOWN so noisy scan retries don't flood the channel.
_orderguard_last_alert: dict[str, float] = {}


def _place_order_for_setup(r: dict, scan_label: str = "morning") -> str:
    """Place a bracket order on Alpaca for a qualified setup and log the order ID.

    Only runs when LIVE_ORDERS_ENABLED=true.  Skips non-directional predictions.
    Sizes at 1% of current account equity (capped $250–$2,000).
    Patches the paper_trades row with alpaca_order_id, alpaca_qty, order_placed_at,
    and skip_reason (always written so the funnel panel can count every row).

    Returns a short outcome string:
      "placed"                          — bracket order submitted successfully
      "skipped:<reason>"                — blocked before order was sent
      "skipped:<reason>:<detail>"       — blocked with extra context (e.g. slippage %)
    Skip reasons that already send their own Telegram are marked with (*) in comments.
    """
    _ticker_raw = r.get("ticker", "unknown")

    if not LIVE_ORDERS_ENABLED:
        _patch_skip_reason(r, _ticker_raw, "orders_disabled")
        return "skipped:orders_disabled"

    # ── Restart deduplication — in-process set (fast, catches rapid restarts) ──
    if _ticker_raw.upper() in _PLACED_THIS_SESSION:
        log.warning(
            f"  [{_ticker_raw}] skip order — already placed this session "
            f"(restart dedup guard). Not re-entering."
        )
        _patch_skip_reason(r, _ticker_raw, "already_placed_this_session")
        return "skipped:already_placed_this_session"

    # ── Restart deduplication — DB check (durable, catches cold restarts) ──────
    if _supabase_client:
        try:
            _today_str = str(r.get("sim_date") or r.get("trade_date") or date.today())
            _dup_res = (
                _supabase_client.table("paper_trades")
                .select("alpaca_order_id")
                .eq("user_id", USER_ID)
                .eq("trade_date", _today_str)
                .eq("ticker", _ticker_raw.upper())
                .not_.is_("alpaca_order_id", "null")
                .limit(1)
                .execute()
            )
            if _dup_res.data:
                _existing_oid = _dup_res.data[0].get("alpaca_order_id", "")[:8]
                log.warning(
                    f"  [{_ticker_raw}] skip order — order already placed today "
                    f"(order_id prefix: {_existing_oid}…). Restart dedup guard."
                )
                _PLACED_THIS_SESSION.add(_ticker_raw.upper())
                _patch_skip_reason(r, _ticker_raw, "already_placed_today")
                return "skipped:already_placed_today"
        except Exception as _dup_err:
            log.debug(f"  [{_ticker_raw}] DB dedup check failed (non-fatal): {_dup_err}")

    direction = r.get("predicted", "")
    if direction not in ("Bullish Break", "Bearish Break"):
        log.info(f"  [{_ticker_raw}] skip order — prediction is '{direction}' (not directional)")
        _patch_skip_reason(r, _ticker_raw, "non_directional")
        return "skipped:non_directional"

    # ── Universe-alignment filter ─────────────────────────────────────────────
    # Bearish Break setups require a gap-DOWN universe to have positive expectancy.
    # Backtest of 111 settled trades shows:
    #   Bullish Break on gap-up stock  → 71%+ WR (80.8% at TCS ≥ 50)
    #   Bearish Break on gap-up stock  →  40% WR  (below random, do not trade)
    #   Bearish Break on gap-down stock → expected positive edge (see gap_down pass)
    # Only allow Bearish Break for tickers sourced from the gap-down screener pass.
    if direction == "Bearish Break":
        _bb_sp_tag = _TICKER_SCREENER_PASS.get(_ticker_raw) or _TICKER_SCREENER_PASS.get(_ticker_raw.upper())
        if _bb_sp_tag != "gap_down":
            log.info(
                f"  [{_ticker_raw}] skip order — Bearish Break on non-gap-down ticker "
                f"(screener_pass={_bb_sp_tag!r}); hist WR 40% on gap-up universe — "
                f"only trading Bearish Break from gap-down screener pass."
            )
            _patch_skip_reason(r, _ticker_raw, "bearish_break_not_gap_down")
            return "skipped:bearish_break_not_gap_down"

    # ── Pre-flight price guard (ALL modes) ───────────────────────────────────
    # Alpaca 422 if stop_price < market price (buy stop) or > market price (sell stop).
    # Three-tier response based on how far past the IB level price has moved:
    #
    #   ① Within SLIPPAGE_TOLERANCE_PCT (default 0.5%):
    #      Breakout just happened — switch to market order and enter now.
    #      PLUG $2.84 IB high, price $2.85 → $0.01 slip → market fill.
    #
    #   ② Beyond tolerance, within ENTRY_ALREADY_TRIGGERED_PCT (1.5%, live only):
    #      Meaningful but not ridiculous slippage — skip to protect entry price.
    #
    #   ③ Beyond ENTRY_ALREADY_TRIGGERED_PCT (live only):
    #      Setup has run away — hard skip.
    _pf_direction    = r.get("predicted", "")
    _pf_entry        = (float(r.get("ib_high") or 0) if _pf_direction == "Bullish Break"
                        else float(r.get("ib_low") or 0))
    _use_market_entry = False   # set True below if slippage is within tolerance
    if _pf_entry > 0:
        _pf_prices = _alpaca_get_5min_bar_close([_ticker_raw])
        _pf_px     = _pf_prices.get(_ticker_raw.upper())
        if _pf_px is not None:
            _pf_crossed = (
                (_pf_direction == "Bullish Break" and _pf_px >= _pf_entry) or
                (_pf_direction == "Bearish Break" and _pf_px <= _pf_entry)
            )
            if _pf_crossed:
                # How many % past the IB level is price?
                _slip_pct = (
                    (_pf_px - _pf_entry) / _pf_entry * 100 if _pf_direction == "Bullish Break"
                    else (_pf_entry - _pf_px) / _pf_entry * 100
                )
                if SLIPPAGE_TOLERANCE_PCT > 0 and _slip_pct <= SLIPPAGE_TOLERANCE_PCT:
                    # ① Tiny slippage — the breakout literally just happened.
                    #    Switch to a market-order entry so we still get the fill.
                    _use_market_entry = True
                    log.info(
                        f"  [{_ticker_raw}] ⚡ Slippage {_slip_pct:.2f}% ≤ {SLIPPAGE_TOLERANCE_PCT}% tolerance "
                        f"(price ${_pf_px:.2f} vs entry ${_pf_entry:.2f}) — entering via MARKET order"
                    )
                else:
                    # ② / ③ Price has already run — skip entirely.
                    _pf_side_word = "above" if _pf_direction == "Bullish Break" else "below"
                    log.warning(
                        f"  [{_ticker_raw}] ⛔ ORDER SKIPPED — price ${_pf_px:.2f} already "
                        f"{_slip_pct:.2f}% {_pf_side_word} entry ${_pf_entry:.2f} "
                        f"(> {SLIPPAGE_TOLERANCE_PCT}% tolerance)"
                    )
                    _patch_skip_reason(r, _ticker_raw, "entry_already_triggered")
                    return f"skipped:entry_already_triggered:{_slip_pct:.2f}%"
            # Live-mode anti-chasing: price approaching IB level but 1.5%+ below (Bullish)
            # or above (Bearish) — stop order would still be valid but setup is stale.
            if not _pf_crossed and not IS_PAPER_ALPACA:
                _pct_thresh = ENTRY_ALREADY_TRIGGERED_PCT / 100.0
                if _pf_direction == "Bullish Break" and _pf_px > _pf_entry * (1 + _pct_thresh):
                    log.warning(
                        f"  [{_ticker_raw}] ⛔ LIVE ORDER BLOCKED — price ${_pf_px:.2f} already "
                        f"{(_pf_px/_pf_entry - 1)*100:.1f}% above entry ${_pf_entry:.2f} "
                        f"(>{ENTRY_ALREADY_TRIGGERED_PCT}% chase threshold)"
                    )
                    _patch_skip_reason(r, _ticker_raw, "entry_already_triggered")
                    return "skipped:entry_already_triggered"
                if _pf_direction == "Bearish Break" and _pf_px < _pf_entry * (1 - _pct_thresh):
                    log.warning(
                        f"  [{_ticker_raw}] ⛔ LIVE ORDER BLOCKED — price ${_pf_px:.2f} already "
                        f"{(1 - _pf_px/_pf_entry)*100:.1f}% below entry ${_pf_entry:.2f} "
                        f"(>{ENTRY_ALREADY_TRIGGERED_PCT}% chase threshold)"
                    )
                    _patch_skip_reason(r, _ticker_raw, "entry_already_triggered")
                    return "skipped:entry_already_triggered"

    # ── Morning TCS floor ─────────────────────────────────────────────────────
    # Default (env var MORNING_TCS_FLOOR=60) kept for backward compat.
    # filter_config.json morning_tcs_min overrides the env-var floor when present.
    # Hist (backtest_sim_runs, 5-yr): morning TCS 55-59 = 85.4% WR / 882 trades
    #   TCS 55: 83.5% WR / 321 trades | TCS 56: 85.4% / 137 | TCS 57: 90.7% / 194
    #   TCS 58: 83.4% / 229 — all above 75% threshold, unlocked 2026-04-22 (Task #2036).
    # Morning TCS 60-69 → +0.366R / 36.9% WR (203/yr) — positive, take it.
    # Intraday TCS 50-59 handled by MIN_TCS + per-structure floor (not blocked here).
    _tcs_val     = float(r.get("tcs", 0))
    _scan_type_v = (r.get("scan_type") or scan_label or "").lower()
    _effective_morning_floor = MORNING_TCS_FLOOR
    try:
        _flt_morning = _load_filter_config()
        if "morning_tcs_min" in _flt_morning:
            _effective_morning_floor = int(_flt_morning["morning_tcs_min"])
    except Exception:
        pass
    if _scan_type_v == "morning" and _tcs_val < _effective_morning_floor:
        log.info(
            f"  [{_ticker_raw}] skip order — morning TCS {_tcs_val:.0f} < floor {_effective_morning_floor} "
            f"(hist: negative expectancy below TCS {_effective_morning_floor})"
        )
        tg_send(
            f"⛔ <b>{_ticker_raw} Blocked — Morning TCS too low</b>\n"
            f"TCS <b>{_tcs_val:.0f}</b> < floor <b>{_effective_morning_floor}</b>\n"
            f"Morning TCS &lt;{_effective_morning_floor} hist: negative expectancy — skipping"
        )
        _patch_skip_reason(r, _ticker_raw, "morning_tcs_below_floor")
        return "skipped:morning_tcs_below_floor"  # (*) own Telegram already sent above

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
        return "skipped:lunch_blackout"  # (*) own Telegram already sent above

    ticker = r.get("ticker", "").upper()

    # ── PDT guard (live accounts only, <$25k equity) ───────────────────────────
    _pdt_blocked, _dt_count, _pdt_in_effect = _check_pdt_guard()

    # Detect sub-$25k → above-$25k transition and fire a one-time Telegram alert.
    # _PDT_WAS_IN_EFFECT starts as None; it is set to True the first time we observe
    # the gate is active so that accounts that have always been above $25k never trigger
    # a false notification.
    # We use identity checks (is True / is False) rather than truthiness so that a
    # None return from _check_pdt_guard() (Alpaca API fetch failure) is never mistaken
    # for a confirmed equity crossing.
    global _PDT_WAS_IN_EFFECT, _PDT_LIFTED_ALERTED
    if _pdt_in_effect is True:
        _PDT_WAS_IN_EFFECT = True
    elif _pdt_in_effect is False and _PDT_WAS_IN_EFFECT is True and not _PDT_LIFTED_ALERTED:
        _PDT_LIFTED_ALERTED = True
        log.info("[PDT] Account crossed $25k — PDT gate lifted. Sending Telegram notification.")
        tg_send(
            "🎉 <b>PDT Gate Lifted — Account Crossed $25k</b>\n"
            "Now trading <b>all S2 signals</b> (TCS≥50 + gap≥2%).\n"
            "PDT quality gate disabled."
        )

    if _pdt_blocked:
        log.warning(
            f"  [{ticker}] ORDER BLOCKED — PDT limit reached "
            f"({_dt_count} day trades in rolling 5 days, max {PDT_MAX_DAY_TRADES})"
        )
        # Send Telegram only once per calendar day — prevents N duplicate alerts
        # when N setups all hit the same PDT block in one scan session.
        # Return distinct outcome strings so _send_skip_outcome_tg can send a
        # brief follow-up for the subsequent setups whose Telegram was suppressed.
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
            _patch_skip_reason(r, ticker, "pdt_blocked")
            return "skipped:pdt_blocked"  # (*) own Telegram already sent above
        else:
            log.info(f"  [{ticker}] PDT block alert suppressed — already sent today")
            _patch_skip_reason(r, ticker, "pdt_blocked")
            return "skipped:pdt_blocked_silent"  # no Telegram was sent — follow-up needed

    # ── PDT quality gate (live, sub-$25k only) ────────────────────────────────
    # While PDT is in effect but not yet exhausted, reserve the limited 3-per-5-day
    # slots exclusively for TCS≥PDT_PRIORITY_TCS (P1/P3 elite tier).
    #
    # Data backing (4.9yr settled backtest, S2 universe, gap≥2%):
    #   TCS≥70 (P1/P3): 1.295R avg, 91.4% WR — elite
    #   TCS<70  (P2/P4): 0.680R avg, 81.9% WR — good but lower edge
    #   In 2026 specifically: TCS≥70 = 1.160R/96.6% WR vs TCS 60-69 = 0.348R/71.3%
    #
    # Using only TCS≥70 during the PDT phase reaches the $25k unlock ~8 weeks sooner
    # (day 77 vs day 117) and adds ~$1.4M to Year-1 compounding.  Once the account
    # clears $25k this gate is disabled and all qualifying S2 signals are taken.
    # Paper mode is exempt — PDT rules don't apply to paper accounts and we want
    # full S2 coverage for data validation regardless of TCS.
    if not IS_PAPER_ALPACA and _pdt_in_effect and PDT_PRIORITY_TCS > 0 and _tcs_val < PDT_PRIORITY_TCS:
        log.info(
            f"  [{ticker}] PDT quality gate — TCS {_tcs_val:.0f} < PDT_PRIORITY_TCS {PDT_PRIORITY_TCS} "
            f"(acct sub-$25k, reserving PDT slots for TCS≥{PDT_PRIORITY_TCS} elite tier only)"
        )
        tg_send(
            f"⏭ <b>{ticker} Deferred — PDT Quality Gate</b>\n"
            f"TCS <b>{_tcs_val:.0f}</b> &lt; PDT priority floor <b>{PDT_PRIORITY_TCS}</b>\n"
            f"Reserving PDT slots for TCS≥{PDT_PRIORITY_TCS} (P1/P3 elite) while acct &lt;$25k.\n"
            f"Will trade all S2 signals freely once account clears $25k."
        )
        _patch_skip_reason(r, ticker, "pdt_quality_gate")
        return "skipped:pdt_quality_gate"  # (*) own Telegram already sent above

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
        return "skipped:concurrent_cap"  # (*) own Telegram already sent above

    # ── Per-ticker existing-position guard ─────────────────────────────────────
    # Prevents duplicate entries on bot restart. If Alpaca already holds an open
    # position in this ticker (from a prior session or earlier today), skip the
    # new order entirely rather than pyramiding unintentionally.
    try:
        _live_positions = _alpaca_get_positions()
        _open_tickers   = {p.get("symbol", "").upper() for p in (_live_positions or [])}
        if ticker.upper() in _open_tickers:
            log.warning(
                f"  [{ticker}] skip order — Alpaca already has an open position in this ticker. "
                f"Restart/duplicate-entry guard engaged."
            )
            _patch_skip_reason(r, ticker, "position_already_open")
            return "skipped:position_already_open"
    except Exception as _guard_err:
        log.warning(f"  [{ticker}] Per-ticker position guard check failed ({_guard_err}) — allowing order through")

    ib_high = float(r.get("ib_high") or 0)
    ib_low  = float(r.get("ib_low")  or 0)
    if ib_high <= 0 or ib_low <= 0 or ib_high <= ib_low:
        log.warning(f"  [{ticker}] skip order — invalid IB ({ib_low}–{ib_high})")
        _patch_skip_reason(r, ticker, "invalid_ib")
        return "skipped:invalid_ib"

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
            return "skipped:ib_too_wide"  # (*) own Telegram already sent above

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
            return "skipped:rvol_below_floor"  # (*) own Telegram already sent above

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

    # ── PM-IB directional gate (PM_IB_FILTER=1) ────────────────────────────
    # Gate: PM must accept past the prior session's IB in the trade direction.
    # Bullish: pm_high >= prev_ib_high   Bearish: pm_low <= prev_ib_low
    # When PM or prev-IB data is unavailable the setup is passed through (defensive).
    if PM_IB_FILTER:
        _pm_dir_low   = direction.lower()
        _pm_today_str = datetime.now(EASTERN).strftime("%Y-%m-%d")
        _pm_open_px   = float(r.get("open_price") or r.get("entry_price_sim") or 0)
        _pm_data      = _fetch_premarket_range(ticker, _pm_today_str, _pm_open_px)
        _pm_rng_pct, _pm_hi, _pm_lo = _pm_data
        _prev_ib_h, _prev_ib_lo = _fetch_prev_ib(ticker, _pm_today_str)
        _pm_data_ok = _pm_hi > 0 and _prev_ib_h > _prev_ib_lo > 0
        if _pm_data_ok:
            if "bullish" in _pm_dir_low:
                _pm_accepted = _pm_hi >= _prev_ib_h
            elif "bearish" in _pm_dir_low:
                _pm_accepted = _pm_lo <= _prev_ib_lo
            else:
                _pm_accepted = True  # non-directional / unknown → pass through

            if not _pm_accepted:
                log.info(
                    f"  [{ticker}] PM-IB gate skip — PM did not accept past prior IB "
                    f"in {_pm_dir_low} direction "
                    f"(pm_hi={_pm_hi:.2f}, pm_lo={_pm_lo:.2f}, "
                    f"prev_ib_h={_prev_ib_h:.2f}, prev_ib_l={_prev_ib_lo:.2f})"
                )
                tg_send(
                    f"⛔ <b>{ticker} Blocked — PM-IB gate</b>\n"
                    f"Pre-market did not accept past prior IB in <b>{html.escape(_pm_dir_low)}</b> direction.\n"
                    f"PM: {_pm_lo:.2f}–{_pm_hi:.2f} | Prior IB: {_prev_ib_lo:.2f}–{_prev_ib_h:.2f}"
                )
                _patch_skip_reason(r, ticker, "pm_ib_filter")
                return "skipped:pm_ib_filter"  # (*) own Telegram already sent above
            else:
                log.info(
                    f"  [{ticker}] PM-IB gate PASS "
                    f"(pm_hi={_pm_hi:.2f}, pm_lo={_pm_lo:.2f}, "
                    f"prev_ib_h={_prev_ib_h:.2f}, prev_ib_l={_prev_ib_lo:.2f})"
                )
        else:
            log.debug(
                f"  [{ticker}] PM-IB gate: data unavailable — passing through "
                f"(pm_hi={_pm_hi:.2f}, prev_ib_h={_prev_ib_h:.2f})"
            )

    # ── filter_config.json additional entry gates ────────────────────────────
    # Applied after RVOL floor; all gates are optional (no-ops when config absent).
    _flt_cfg = _load_filter_config()
    if _flt_cfg:
        _direction_raw = r.get("predicted", "") or ""
        _gap_pct_v     = r.get("gap_pct")
        _ft_pct_v      = r.get("follow_thru_pct")
        _fb_up         = r.get("false_break_up")
        _fb_dn         = r.get("false_break_down")

        # 1. TCS additional offset
        _flt_tcs_offset = int(_flt_cfg.get("tcs_offset", 0))
        if _flt_tcs_offset > 0:
            import json as _flt_json
            _flt_tcs_thresholds: dict = {}
            _flt_tcs_path = os.path.join(os.path.dirname(__file__), "tcs_thresholds.json")
            try:
                with open(_flt_tcs_path) as _flt_f:
                    _flt_tcs_thresholds = _flt_json.load(_flt_f)
            except Exception:
                pass
            _flt_pred_low = _direction_raw.lower()
            _flt_base = 60
            for _flt_key, _flt_token in [
                ("double_dist","dbl dist"), ("ntrl_extreme","ntrl extreme"),
                ("trend_bull","bullish break"), ("trend_bull","bearish break"),
                ("nrml_variation","nrml var"), ("neutral","neutral"),
                ("normal","normal"), ("non_trend","non_trend"),
            ]:
                if _flt_token in _flt_pred_low:
                    _flt_base = _flt_tcs_thresholds.get(_flt_key, 60)
                    break
            _flt_tcs_required = _flt_base + _flt_tcs_offset
            if _tcs_val < _flt_tcs_required:
                log.info(
                    f"  [{ticker}] filter_config skip — TCS {_tcs_val:.0f} < "
                    f"base {_flt_base} + offset {_flt_tcs_offset} = {_flt_tcs_required}"
                )
                tg_send(
                    f"⛔ <b>{ticker} Filtered — TCS below elevated threshold</b>\n"
                    f"TCS <b>{_tcs_val:.0f}</b> < required <b>{_flt_tcs_required:.0f}</b> "
                    f"(+{_flt_tcs_offset} optimizer offset)"
                )
                _patch_skip_reason(r, ticker, "filter_config_tcs")
                return "skipped:filter_config_tcs"  # (*) own Telegram already sent above

        # 2. Gap% floor
        # Mirrors filter_grid_search._apply_combo: fail when filter active AND data is None.
        _flt_gap_min = float(_flt_cfg.get("gap_min", 0.0))
        if _flt_gap_min > 0:
            if _gap_pct_v is None or abs(float(_gap_pct_v)) < _flt_gap_min:
                _gap_desc = f"|gap%| {abs(float(_gap_pct_v)):.2f} < {_flt_gap_min}" if _gap_pct_v is not None else "gap% data unavailable"
                log.info(f"  [{ticker}] filter_config skip — {_gap_desc}")
                tg_send(
                    f"⛔ <b>{ticker} Filtered — Gap below optimizer floor</b>\n"
                    + (f"|gap%| <b>{abs(float(_gap_pct_v)):.2f}%</b> < floor <b>{_flt_gap_min}%</b>"
                       if _gap_pct_v is not None else f"gap% data unavailable, floor={_flt_gap_min}%")
                )
                _patch_skip_reason(r, ticker, "filter_config_gap")
                return "skipped:filter_config_gap"  # (*) own Telegram already sent above

        # 2b. Gap direction (up / down / any)
        # Mirrors filter_grid_conservative_mode: gap_direction="up" requires gap_pct > 0.
        # Only evaluated when gap data is present; absent data is a defensive pass-through
        # (consistent with how gap_min treats missing data above).
        _flt_gap_dir = str(_flt_cfg.get("gap_direction", "any")).lower()
        if _flt_gap_dir in ("up", "down") and _gap_pct_v is not None:
            _actual_gap = float(_gap_pct_v)
            _dir_pass = (_actual_gap > 0) if _flt_gap_dir == "up" else (_actual_gap < 0)
            if not _dir_pass:
                _dir_label = "upward (gap-up)" if _flt_gap_dir == "up" else "downward (gap-down)"
                log.info(
                    f"  [{ticker}] filter_config skip — gap direction mismatch "
                    f"(need {_flt_gap_dir}, actual gap={_actual_gap:+.2f}%)"
                )
                tg_send(
                    f"⛔ <b>{ticker} Filtered — Gap direction mismatch</b>\n"
                    f"Optimizer requires {_dir_label} gap.\n"
                    f"Actual gap: <b>{_actual_gap:+.2f}%</b>"
                )
                _patch_skip_reason(r, ticker, "filter_config_gap_direction")
                return "skipped:filter_config_gap_direction"  # (*) own Telegram already sent above

        # 3. Follow-through floor
        # Mirrors filter_grid_search._apply_combo: fail when filter active AND data is None.
        _flt_ft_min = float(_flt_cfg.get("follow_min_pct", -999.0))
        if _flt_ft_min > -900:
            if _ft_pct_v is None or float(_ft_pct_v) < _flt_ft_min:
                _ft_desc = f"follow_thru {float(_ft_pct_v):.2f}% < {_flt_ft_min}%" if _ft_pct_v is not None else "follow_thru data unavailable"
                log.info(f"  [{ticker}] filter_config skip — {_ft_desc}")
                tg_send(
                    f"⛔ <b>{ticker} Filtered — Low follow-through</b>\n"
                    + (f"Follow-thru <b>{float(_ft_pct_v):.2f}%</b> < floor <b>{_flt_ft_min}%</b>"
                       if _ft_pct_v is not None else f"Follow-thru data unavailable, floor={_flt_ft_min}%")
                )
                _patch_skip_reason(r, ticker, "filter_config_follow_thru")
                return "skipped:filter_config_follow_thru"  # (*) own Telegram already sent above

        # 4. Structure filter
        _flt_struct = str(_flt_cfg.get("struct_filter", "all"))
        if _flt_struct != "all":
            _flt_pred_low = _direction_raw.lower()
            _flt_is_trend   = any(t in _flt_pred_low for t in ("bullish break", "bearish break"))
            _flt_is_extreme = any(t in _flt_pred_low for t in ("ntrl extreme", "dbl dist"))
            # _flt_struct_grp mirrors filter_grid_search._structure_group() exactly:
            #   "trend" → bullish/bearish break families
            #   "extreme" → ntrl extreme / dbl dist families
            #   "neutral" → everything else (neither trend nor extreme)
            _flt_struct_grp = "trend" if _flt_is_trend else ("extreme" if _flt_is_extreme else "neutral")
            _flt_pass_struct = True
            if _flt_struct == "trend"      and _flt_struct_grp != "trend":
                _flt_pass_struct = False
            elif _flt_struct == "neutral"  and _flt_struct_grp != "neutral":
                # "neutral" filter = NOT trend AND NOT extreme (matches grid search exactly)
                _flt_pass_struct = False
            elif _flt_struct == "extreme"  and _flt_struct_grp != "extreme":
                _flt_pass_struct = False
            elif _flt_struct == "no_extreme" and _flt_struct_grp == "extreme":
                _flt_pass_struct = False
            if not _flt_pass_struct:
                log.info(
                    f"  [{ticker}] filter_config skip — structure '{_direction_raw}' "
                    f"excluded by struct_filter='{_flt_struct}'"
                )
                tg_send(
                    f"⛔ <b>{ticker} Filtered — Structure type excluded</b>\n"
                    f"<b>{html.escape(_direction_raw)}</b> is outside optimizer struct_filter=<b>{html.escape(_flt_struct)}</b>"
                )
                _patch_skip_reason(r, ticker, "filter_config_struct")
                return "skipped:filter_config_struct"  # (*) own Telegram already sent above

        # 5. False-break exclusion
        if _flt_cfg.get("excl_false_break") and (_fb_up or _fb_dn):
            _fb_desc = "up+down" if (_fb_up and _fb_dn) else ("up" if _fb_up else "down")
            log.info(f"  [{ticker}] filter_config skip — false_break_{_fb_desc} detected")
            tg_send(
                f"⛔ <b>{ticker} Filtered — False break detected</b>\n"
                f"False break <b>{_fb_desc}</b> — optimizer excludes these setups"
            )
            _patch_skip_reason(r, ticker, "filter_config_false_break")
            return "skipped:filter_config_false_break"  # (*) own Telegram already sent above

        # 6. PM range floor
        # Mirrors filter_grid_search: fail when filter is active AND data is None.
        _flt_pm_range_floor = float(_flt_cfg.get("pm_range_floor", 0.0))
        if _flt_pm_range_floor > 0:
            _flt_pm_rng_v = r.get("pm_range_pct")
            if _flt_pm_rng_v is None or float(_flt_pm_rng_v) < _flt_pm_range_floor:
                _flt_pm_rng_desc = (
                    f"pm_range_pct {float(_flt_pm_rng_v):.2f}% < {_flt_pm_range_floor}%"
                    if _flt_pm_rng_v is not None
                    else "pm_range_pct data unavailable"
                )
                log.info(f"  [{ticker}] filter_config skip — PM range too narrow: {_flt_pm_rng_desc}")
                tg_send(
                    f"⛔ <b>{ticker} Filtered — PM range too narrow</b>\n"
                    + (
                        f"PM range <b>{float(_flt_pm_rng_v):.2f}%</b> < floor <b>{_flt_pm_range_floor}%</b>"
                        if _flt_pm_rng_v is not None
                        else f"PM range data unavailable, floor={_flt_pm_range_floor}%"
                    )
                )
                _patch_skip_reason(r, ticker, "filter_config_pm_range")
                return "skipped:filter_config_pm_range"  # (*) own Telegram already sent above

        # 7. PM IB direction
        # Mirrors filter_grid_search: bullish_accepted → pm_ib_high > prev_day_ib_high;
        #                              bearish_accepted → pm_ib_low  < prev_day_ib_low.
        # Data unavailable → pass through (defensive, same as existing PM_IB_FILTER gate).
        _flt_pm_ib_dir = str(_flt_cfg.get("pm_ib_dir", "any"))
        if _flt_pm_ib_dir != "any":
            # Prefer data cached in r by IB context enrichment to avoid double-fetching.
            _flt_pm_hi     = r.get("_pm_ib_high")
            _flt_pm_lo     = r.get("_pm_ib_low")
            _flt_prev_hi   = r.get("prev_ib_high")
            _flt_prev_lo   = r.get("prev_ib_low")
            # Fall back to live fetch when IB_CONTEXT_ENABLED=0 (enrichment didn't run).
            if _flt_pm_hi is None or _flt_prev_hi is None:
                _flt_today_str = datetime.now(EASTERN).strftime("%Y-%m-%d")
                _flt_open_px   = float(r.get("open_price") or r.get("entry_price_sim") or 0)
                _, _flt_pm_hi, _flt_pm_lo = _fetch_premarket_range(ticker, _flt_today_str, _flt_open_px)
                _flt_prev_hi, _flt_prev_lo = _fetch_prev_ib(ticker, _flt_today_str)
            _flt_pm_data_ok = (
                _flt_pm_hi is not None and float(_flt_pm_hi) > 0
                and _flt_prev_hi is not None
                and float(_flt_prev_hi) > float(_flt_prev_lo or 0) > 0
            )
            if _flt_pm_data_ok:
                _flt_pm_hi_f   = float(_flt_pm_hi)
                _flt_pm_lo_f   = float(_flt_pm_lo or 0)
                _flt_prev_hi_f = float(_flt_prev_hi)
                _flt_prev_lo_f = float(_flt_prev_lo or 0)
                if _flt_pm_ib_dir == "bullish_accepted":
                    _flt_pm_dir_pass = _flt_pm_hi_f > _flt_prev_hi_f
                elif _flt_pm_ib_dir == "bearish_accepted":
                    _flt_pm_dir_pass = _flt_pm_lo_f < _flt_prev_lo_f
                else:
                    _flt_pm_dir_pass = True
                if not _flt_pm_dir_pass:
                    log.info(
                        f"  [{ticker}] filter_config skip — PM direction mismatch "
                        f"(pm_ib_dir={_flt_pm_ib_dir}, "
                        f"pm_hi={_flt_pm_hi_f:.2f}, pm_lo={_flt_pm_lo_f:.2f}, "
                        f"prev_ib_h={_flt_prev_hi_f:.2f}, prev_ib_l={_flt_prev_lo_f:.2f})"
                    )
                    tg_send(
                        f"⛔ <b>{ticker} Filtered — PM direction mismatch</b>\n"
                        f"Optimizer requires PM IB dir <b>{html.escape(_flt_pm_ib_dir)}</b>.\n"
                        f"PM: {_flt_pm_lo_f:.2f}–{_flt_pm_hi_f:.2f} | Prior IB: {_flt_prev_lo_f:.2f}–{_flt_prev_hi_f:.2f}"
                    )
                    _patch_skip_reason(r, ticker, "filter_config_pm_ib_dir")
                    return "skipped:filter_config_pm_ib_dir"  # (*) own Telegram already sent above
            else:
                log.debug(
                    f"  [{ticker}] filter_config PM-IB dir check: data unavailable — passing through"
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

    # ── Market-close guard — never place orders outside regular session ───────
    # Catch-up scans can start within hours but run past 4:00 PM ET.
    # Check real-time here so no order is submitted after the session ends.
    _now_for_order = datetime.now(EASTERN)
    if not _market_is_open(_now_for_order):
        _now_str = _now_for_order.strftime("%H:%M:%S ET")
        log.warning(
            f"  [{ticker}] [OrderGuard] skipping order — market is closed ({_now_str}). "
            f"Re-run tomorrow; setup: {direction}"
        )
        return "skipped:market_closed"

    # Dual-constraint sizing: notional (equity × NOTIONAL_PCT) and risk (2.1% of equity)
    # are both computed live; qty = min(qty_by_risk, qty_by_notional) picks the tighter.
    # Under $25k the notional cap (~20% of equity) almost always wins over 2.1% risk at
    # typical 5% IB stops (which would imply 42%-of-equity positions). Both compound.
    _trade_notional = _compute_trade_notional()

    # ── OrderGuard: skip if Alpaca already holds a position in this ticker ────
    _live_positions = _alpaca_get_positions()
    _live_tickers = {p["symbol"].upper() for p in _live_positions}
    if ticker.upper() in _live_tickers:
        _ts = _now_for_order.strftime("%Y-%m-%d %H:%M:%S ET")
        log.warning(
            f"  [{ticker}] [OrderGuard] skipping — already have an open Alpaca position"
        )
        _og_now = time.time()
        _og_key = ticker.upper()
        if (_og_now - _orderguard_last_alert.get(_og_key, 0.0)) >= ORDERGUARD_ALERT_COOLDOWN:
            _orderguard_last_alert[_og_key] = _og_now
            tg_send(
                f"🛡️ <b>[OrderGuard] Duplicate-Entry Blocked</b>\n"
                f"Ticker: <b>{ticker}</b>\n"
                f"Reason: existing open position\n"
                f"Time: {_ts}"
            )
        else:
            log.info(
                f"  [{ticker}] [OrderGuard] Telegram alert suppressed "
                f"(cooldown {ORDERGUARD_ALERT_COOLDOWN}s)"
            )
        _orderguard_append_event(ticker, "existing open position", _ts)
        return

    # ── OrderGuard: skip if there are pending (unfilled) orders for this ticker ─
    _open_order_ids = _alpaca_get_open_order_ids(ticker)
    if _open_order_ids:
        _ts = _now_for_order.strftime("%Y-%m-%d %H:%M:%S ET")
        log.warning(
            f"  [{ticker}] [OrderGuard] skipping — {len(_open_order_ids)} open order(s) already exist"
        )
        _og_now = time.time()
        _og_key = ticker.upper()
        if (_og_now - _orderguard_last_alert.get(_og_key, 0.0)) >= ORDERGUARD_ALERT_COOLDOWN:
            _orderguard_last_alert[_og_key] = _og_now
            tg_send(
                f"🛡️ <b>[OrderGuard] Duplicate-Entry Blocked</b>\n"
                f"Ticker: <b>{ticker}</b>\n"
                f"Reason: {len(_open_order_ids)} open order(s) already pending\n"
                f"Time: {_ts}"
            )
        else:
            log.info(
                f"  [{ticker}] [OrderGuard] Telegram alert suppressed "
                f"(cooldown {ORDERGUARD_ALERT_COOLDOWN}s)"
            )
        _orderguard_append_event(ticker, f"{len(_open_order_ids)} open order(s) already pending", _ts)
        return

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
        entry_type   = "market" if _use_market_entry else "stop",
        max_notional = _trade_notional,
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
        _position_value  = round(qty * result["entry"], 2)
        _actual_risk     = round(qty * abs(result["entry"] - result["stop"]), 2)
        # Approximate equity from notional so we can show risk as % of equity.
        # _trade_notional = equity × NOTIONAL_PCT, so equity ≈ notional / NOTIONAL_PCT.
        # Falls back to position value when NOTIONAL_PCT is 0 (shouldn't happen).
        _approx_equity   = (_trade_notional / NOTIONAL_PCT) if NOTIONAL_PCT > 0 else _position_value
        _risk_pct_equity = round(_actual_risk / _approx_equity * 100, 2) if _approx_equity > 0 else 0.0
        tg_send(
            f"📋 <b>{acct_type} Order Placed — {ticker}</b>\n"
            f"{'🟡' if direction == 'Bullish Break' else '🔴'} {direction}\n"
            f"Entry: ${result['entry']} | Stop: ${result['stop']} | "
            f"Target: ${result['target']}\n"
            f"Qty: {qty} shares @ ${result['entry']} = <b>${_position_value:,.0f} in</b>\n"
            f"Risk: ${_actual_risk:,.0f} ({_risk_pct_equity:.1f}% equity) "
            f"({_ib_mult:.2f}× IB · {_ptier_mult:.2f}× P-tier{_rvol_bonus_label}{_sp_label})\n"
            f"<code>{order_id[:8]}…</code>"
        )
        # Patch Supabase paper_trades row with order metadata + skip_reason
        if _supabase_client:
            try:
                _sp = _TICKER_SCREENER_PASS.get(ticker) or _TICKER_SCREENER_PASS.get(ticker.upper())
                # Bearish Break orders are gated exclusively to the gap_down screener
                # universe (enforced earlier in _place_order_for_setup).  Stamp
                # 'gap_down' unconditionally so settled BB trades are immediately
                # visible to calibrate_sp_mult.py --pass gap_down without requiring
                # a manual backfill run.
                if direction == "Bearish Break":
                    _sp = "gap_down"
                _order_patch = {
                    "alpaca_order_id":  order_id,
                    "alpaca_qty":       qty,
                    "order_placed_at":  datetime.utcnow().isoformat(),
                    "skip_reason":      "order_placed",
                    "rvol_mult":        _rvol_mult,
                    "sp_mult":          _sp_mult,
                }
                # Only write mgmt_mode when the adaptive feature is ON so the
                # fixed code-path remains fully backward-compatible on envs
                # where the migration hasn't been applied yet.
                if ADAPTIVE_POSITION_MGMT:
                    _order_patch["mgmt_mode"] = "adaptive_eligible"
                if _sp:
                    _order_patch["screener_pass"] = _sp
                _supabase_client.table("paper_trades").update(_order_patch).eq("user_id", USER_ID).eq("trade_date", str(r.get("sim_date") or r.get("trade_date") or "")).eq("ticker", ticker).execute()
            except Exception as _patch_err:
                log.warning(f"  [{ticker}] Could not patch order_id to paper_trades: {_patch_err}")
        # ── Register zone invalidation watch ──────────────────────────────────
        # Bullish Break: cancel if price closes below IB low (structure broken).
        # Bearish Break: cancel if price closes above IB high (structure broken).
        # Use the raw IB level (not the widened stop) as the invalidation threshold.
        _zw_inval = ib_high if direction == "Bearish Break" else ib_low
        _zw_date  = str(r.get("sim_date") or r.get("trade_date") or datetime.now(EASTERN).date())
        _register_zone_watch(
            ticker             = ticker,
            trade_date         = _zw_date,
            order_id           = order_id,
            direction          = direction,
            invalidation_price = _zw_inval,
            entry              = result["entry"],
            stop               = result["stop"],
            target             = result["target"],
            qty                = qty,
        )
        _PLACED_THIS_SESSION.add(ticker.upper())
        return "placed"
    else:
        log.warning(f"  ❌ [{ticker}] Order failed: {result.get('error')}")
        tg_send(f"⚠️ <b>{acct_type} Order Failed — {ticker}</b>\n{result.get('error','unknown error')}")
        _patch_skip_reason(r, ticker, "order_failed")
        return "skipped:order_failed"  # (*) own Telegram already sent above


# ── Order outcome follow-up Telegram ─────────────────────────────────────────
# Skip reasons that already send their own Telegram inside _place_order_for_setup.
# These count as the outcome message — no second notification needed.
_SKIP_REASONS_WITH_OWN_TG: frozenset = frozenset({
    "morning_tcs_below_floor", "lunch_blackout",
    "pdt_blocked", "pdt_quality_gate", "concurrent_cap",
    "ib_too_wide", "rvol_below_floor", "pm_ib_filter",
    "filter_config_tcs", "filter_config_gap", "filter_config_gap_direction",
    "filter_config_follow_thru",
    "filter_config_struct", "filter_config_false_break",
    "filter_config_pm_range", "filter_config_pm_ib_dir",
    "order_failed",
})


def _send_skip_outcome_tg(ticker: str, outcome: str) -> None:
    """Send a brief Telegram follow-up for skip reasons that don't already send one.

    Called after every _alert_setup + _place_order_for_setup pair so traders
    always get an outcome message: either the order-placed Telegram that
    _place_order_for_setup sent, the per-skip Telegram it already sent (*), or
    this concise ⛔ line for silent skips that had no message yet.
    """
    if not outcome or not outcome.startswith("skipped:"):
        return
    parts     = outcome.split(":", 2)
    skip_key  = parts[1] if len(parts) > 1 else ""
    if skip_key in _SKIP_REASONS_WITH_OWN_TG:
        return
    detail = parts[2] if len(parts) > 2 else ""
    _reason_map = {
        "orders_disabled":            "orders disabled (LIVE_ORDERS_ENABLED=false)",
        "non_directional":            "non-directional structure — no edge to trade",
        "bearish_break_not_gap_down": "Bearish Break on gap-up ticker — hist WR 40%, skipped",
        "entry_already_triggered":    (
            f"price already past entry ({detail} slippage)" if detail
            else "price already past entry (chase guard)"
        ),
        "position_already_open":       "position already open in this ticker",
        "invalid_ib":                  "invalid IB range data",
        "pdt_blocked_silent":          "PDT limit reached (alert already sent today)",
        "already_placed_this_session":  "already entered today (restart guard)",
        "already_placed_today":         "already entered today (restart guard)",
        "filter_config_gap_direction":  "gap direction mismatch (optimizer requires gap-up only)",
    }
    reason_text = _reason_map.get(skip_key, skip_key.replace("_", " "))
    tg_send(f"⛔ <b>{html.escape(ticker)}</b> — Not entered: {reason_text}")


# ── Restart order-deduplication guard ────────────────────────────────────────
# In-process set of tickers already ordered this session.  Populated by
# _place_order_for_setup on success; checked at the top of that function.
# Survives within a single process lifetime — covers rapid back-to-back restarts
# where Alpaca's position list hasn't caught up yet.
_PLACED_THIS_SESSION: set[str] = set()

# ── Trailing stop monitoring ──────────────────────────────────────────────────
# Guard set: (ticker, trade_date) pairs that already have trailing stop active.
# Prevents re-triggering on every 30-second loop iteration.
_TRAILING_STOP_ACTIVATED: set = set()

# ── Screener pass registry ─────────────────────────────────────────────────────
# Maps ticker → pass name ('gap' | 'trend' | 'squeeze' | 'gap_down').  Populated
# by watchlist_refresh() after each scan; queried by _place_order_for_setup() so
# the paper_trades row records which screener produced the signal.
# 'gap_down' tickers are eligible for Bearish Break bracket orders.
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

# Rate-limit for intraday fill reconciliation — runs at most once per 5 minutes
# during the 30-second monitoring loop so fill prices appear promptly after entries.
_LAST_RECONCILE_TS: float    = 0.0
RECONCILE_INTERVAL: int      = 300  # seconds (5 min)


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
            ib_high   = float(row.get("ib_high") or 0)
            order_id  = row.get("alpaca_order_id") or ""
            if not ticker or not direction or not order_id:
                continue
            if direction not in ("Bullish Break", "Bearish Break"):
                continue
            if ib_low <= 0 or ib_high <= 0:
                continue
            # Invalidation price: Bullish Break → IB low, Bearish Break → IB high
            _inval = ib_high if direction == "Bearish Break" else ib_low
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
                ticker, today_str, order_id, direction, _inval,
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


def _alpaca_get_open_order_ids(ticker: str) -> list:
    """Return a list of open Alpaca order IDs for *ticker* without cancelling them.

    Used by adaptive-management code to snapshot existing bracket-leg IDs
    before placing a replacement OCO, so the cancel step can target only
    the pre-existing legs (not the newly placed ones).
    Returns an empty list on any error.
    """
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
            return []
        return [o["id"] for o in (resp.json() or []) if o.get("id")]
    except Exception as _e:
        log.warning(f"[AdaptiveMgmt] get_open_order_ids error for {ticker}: {_e}")
    return []


def _alpaca_cancel_orders_for_ticker(ticker: str, specific_ids: list | None = None) -> int:
    """Cancel open orders for a ticker.

    When *specific_ids* is provided cancel exactly those order IDs (no extra
    API listing call).  When *specific_ids* is None, fetch all open orders for
    the ticker and cancel them all (original behaviour — used by trailing-stop
    logic).  Returns count of successfully cancelled orders.
    """
    base = "https://paper-api.alpaca.markets" if IS_PAPER_ALPACA else "https://api.alpaca.markets"
    headers = {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    try:
        import requests as _req
        if specific_ids is not None:
            ids_to_cancel = [i for i in specific_ids if i]
        else:
            resp = _req.get(
                f"{base}/v2/orders",
                headers=headers,
                params={"status": "open", "symbols": ticker.upper(), "limit": 50},
                timeout=8,
            )
            if resp.status_code != 200:
                return 0
            ids_to_cancel = [o.get("id", "") for o in (resp.json() or [])]
        cancelled = 0
        for oid in ids_to_cancel:
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



def _reconcile_open_positions() -> None:
    """Reconcile open Alpaca positions against paper_trades DB records.

    Pass 1 — win_loss reset:
      If Alpaca has an open position whose paper_trades record has win_loss
      already set (auto-verify closed it prematurely), reset win_loss=NULL
      so _monitor_trailing_stops() will actively manage it.

    Pass 2 — naked position bracket re-attachment:
      If an open Alpaca position has NO active exit orders (stop/limit), look
      up the original stop_price_sim and target_price_sim from paper_trades
      and re-attach an OCO bracket.  This handles overnight positions whose
      GTC/day brackets expired at the prior session close, as well as any
      positions left naked by a bot restart.

    Also re-populates alpaca_qty from the live Alpaca position so the
    trailing-stop math stays accurate.

    Called at startup (after LIVE_ORDERS_ENABLED confirmed) and again at
    each morning watchlist refresh so new multi-day positions are caught.
    """
    if not _supabase_client or not LIVE_ORDERS_ENABLED:
        return

    positions = _alpaca_get_positions()
    if not positions:
        return

    from datetime import timedelta as _td
    today_str    = str(datetime.now(EASTERN).date())
    lookback_str = str((datetime.now(EASTERN).date() - _td(days=10)).isoformat())

    for pos in positions:
        ticker = (pos.get("symbol") or "").upper()
        if not ticker:
            continue
        qty = int(float(pos.get("qty") or 0))
        if qty == 0:
            continue

        # ── Pass 1: win_loss reset ─────────────────────────────────────────────
        # Look for a paper_trades record that has an alpaca_order_id (was
        # placed by this bot) but win_loss is already set despite the
        # Alpaca position still being open.
        try:
            rows = (
                _supabase_client
                .table("paper_trades")
                .select("id,ticker,trade_date,win_loss,alpaca_order_id,entry_price_sim,stop_price_sim,target_price_sim")
                .eq("user_id", USER_ID)
                .eq("ticker", ticker)
                .gte("trade_date", lookback_str)
                .not_.is_("alpaca_order_id", "null")
                .not_.is_("win_loss", "null")
                .order("trade_date", desc=True)
                .limit(3)
                .execute()
            ).data or []
        except Exception as _e:
            log.warning(f"[Reconcile] DB query error for {ticker}: {_e}")
            rows = []

        if rows:
            row = rows[0]
            log.warning(
                f"[Reconcile] {ticker}: Alpaca has OPEN position ({qty} shares, avg "
                f"{pos.get('avg_entry_price','?')}) but paper_trades record from "
                f"{row['trade_date']} has win_loss='{row['win_loss']}'. "
                f"Resetting win_loss=NULL so position is actively monitored."
            )
            try:
                _supabase_client.table("paper_trades").update({
                    "win_loss":   None,
                    "alpaca_qty": qty,
                }).eq("user_id", USER_ID).eq("id", row["id"]).execute()
                log.info(f"[Reconcile] {ticker}: win_loss reset → NULL, alpaca_qty → {qty}")
            except Exception as _e:
                log.warning(f"[Reconcile] Update error for {ticker}: {_e}")

        # ── Pass 2: naked position bracket re-attachment ───────────────────────
        # If the position has no open exit orders, re-attach an OCO bracket
        # using the stop/target from the most recent paper_trades record.
        try:
            open_exit_ids = _alpaca_get_open_order_ids(ticker)
            if open_exit_ids:
                # Already has active orders — nothing to do
                continue

            # Fetch most recent paper_trades row for this ticker (any win_loss state)
            pt_rows = (
                _supabase_client
                .table("paper_trades")
                .select("id,ticker,trade_date,alpaca_order_id,entry_price_sim,stop_price_sim,target_price_sim,predicted")
                .eq("user_id", USER_ID)
                .eq("ticker", ticker)
                .gte("trade_date", lookback_str)
                .not_.is_("alpaca_order_id", "null")
                .order("trade_date", desc=True)
                .limit(1)
                .execute()
            ).data or []

            if not pt_rows:
                log.warning(f"[Reconcile] {ticker}: naked position but no paper_trades record found — skipping bracket re-attach")
                continue

            pt = pt_rows[0]
            stop_px   = float(pt.get("stop_price_sim")   or 0)
            target_px = float(pt.get("target_price_sim") or 0)
            direction = (pt.get("predicted") or "Bullish Break")
            exit_side = "sell" if "Bullish" in direction else "buy"

            if stop_px <= 0 or target_px <= 0:
                log.warning(
                    f"[Reconcile] {ticker}: naked position, but stop_price_sim={stop_px} or "
                    f"target_price_sim={target_px} is missing — cannot re-attach bracket"
                )
                continue

            log.warning(
                f"[Reconcile] {ticker}: open position ({qty} shares) has NO active exit orders. "
                f"Re-attaching OCO bracket — stop=${stop_px:.2f}, target=${target_px:.2f}"
            )
            oco_result = _alpaca_place_oco_exit(ticker, qty, exit_side, stop_px, target_px)
            if oco_result.get("ok"):
                log.info(
                    f"[Reconcile] {ticker}: OCO bracket re-attached successfully. "
                    f"order_id={oco_result.get('order_id','?')[:12]}"
                )
                tg_send(
                    f"🔁 <b>{ticker} Bracket Re-attached</b>\n"
                    f"Overnight/restart: naked position detected on startup.\n"
                    f"Qty: <b>{qty}</b> | Stop: <b>${stop_px:.2f}</b> | Target: <b>${target_px:.2f}</b>\n"
                    f"OCO order placed — position is now protected."
                )
            else:
                log.error(
                    f"[Reconcile] {ticker}: Failed to re-attach OCO bracket: {oco_result.get('error','?')}"
                )
                tg_send(
                    f"⚠️ <b>{ticker} Bracket Re-attach FAILED</b>\n"
                    f"Naked position ({qty} shares) has no exit orders.\n"
                    f"Manual intervention needed — stop=${stop_px:.2f}, target=${target_px:.2f}"
                )
        except Exception as _e:
            log.warning(f"[Reconcile] Bracket re-attach error for {ticker}: {_e}")


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

    today_str    = str(now_et.date())
    from datetime import timedelta as _td
    lookback_str = str((now_et.date() - _td(days=5)).isoformat())

    # Fetch open paper_trades for today and the past 5 days (covers multi-day positions).
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
            .gte("trade_date", lookback_str)
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
                    .gte("trade_date", lookback_str)
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
    cancelled_bracket_orders: dict[str, int] = {}

    def _close_one(ticker: str) -> bool:
        """Cancel any bracket orders then close via DELETE /v2/positions/{symbol}.

        Bracket legs hold shares as held_for_orders (available=0), which causes
        Alpaca to reject the position-close with HTTP 403.  We cancel all open
        orders for the ticker first, wait 1 s for Alpaca to release the lock,
        then retry the position close up to 3 times.
        """
        cancelled = cancel_alpaca_ticker_orders(
            ticker,
            is_paper=IS_PAPER_ALPACA,
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )
        if cancelled:
            log.info(f"[ForceClose] {ticker}: cancelled {cancelled} bracket orders before close-out")
            cancelled_bracket_orders[ticker] = cancelled
        _time.sleep(1)

        for _attempt in range(3):
            try:
                resp = _req.delete(
                    f"{base}/v2/positions/{ticker}",
                    params={"percentage": "100"},
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
                    log.warning(
                        f"[ForceClose] {ticker} — DELETE attempt {_attempt + 1}/3 failed: "
                        f"{resp.status_code} {resp.text[:200]}"
                    )
                    if _attempt < 2:
                        _time.sleep(1)
            except Exception as _e:
                log.warning(f"[ForceClose] {ticker} — exception on attempt {_attempt + 1}/3: {_e}")
                if _attempt < 2:
                    _time.sleep(1)
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

        if cancelled_bracket_orders:
            for _sym, _n in sorted(cancelled_bracket_orders.items()):
                _order_word = "order" if _n == 1 else "orders"
                lines.append(
                    f"⚠️ {_sym}: {_n} bracket {_order_word} cancelled at force-close — review bracket setup"
                )

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
    In the dev environment (REPLIT_DEPLOYMENT not set) notifications are
    suppressed unless DEV_TG_ENABLED=1 is explicitly set, preventing
    duplicate alerts when the Replit IDE is opened alongside a live deployment.
    """
    if not TG_TOKEN or not TG_CHAT_ID:
        return False
    _is_deployed = bool(os.environ.get("REPLIT_DEPLOYMENT"))
    _dev_tg_ok   = os.environ.get("DEV_TG_ENABLED", "").strip() == "1"
    if not _is_deployed and not _dev_tg_ok:
        log.debug("[tg_send] DEV mode — suppressed (set DEV_TG_ENABLED=1 to enable locally)")
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
    predicted = r.get("predicted", "Unknown") or "Unknown"
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

    _predicted_html = html.escape(predicted)
    msg = (
        f"{emoji} <b>EdgeIQ Setup — {ticker}</b>\n"
        f"⏰ {scan_time}  ·  📅 {trade_date}\n"
        f"{priority_line}"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price at IB close: <b>${cur_px:.2f}</b>  "
        f"({chg_arrow}{abs(chg_pct):.1f}% from open ${open_px:.2f})\n"
        f"📊 Structure: <b>{_predicted_html}</b>  ({conf:.0f}% conf)\n"
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

    # Per-pass breakdown from the screener tag dict (populated at watchlist refresh)
    _pass_counts = {"gap": 0, "trend": 0, "squeeze": 0, "gap_down": 0}
    for _tag in _TICKER_SCREENER_PASS.values():
        if _tag in _pass_counts:
            _pass_counts[_tag] += 1
    _pass_total = sum(_pass_counts.values())
    if _pass_total > 0:
        _pass_line = (
            f" ({_pass_counts['gap']} gap · {_pass_counts['trend']} trend · "
            f"{_pass_counts['squeeze']} squeeze · {_pass_counts['gap_down']} gap-down)"
        )
    else:
        _pass_line = ""

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
            f"No setups met per-structure TCS thresholds today out of {total_scanned} scanned{_pass_line}.\n"
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
        f"📋 Scanned {total_scanned} tickers{_pass_line}"
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
    import math as _math

    wins   = [r for r in results if r.get("win_loss") == "Win"]
    losses = [r for r in results if r.get("win_loss") == "Loss"]
    best   = max(results, key=lambda r: float(r.get("aft_move_pct", 0)), default=None)
    _ib_threshold = load_ib_range_pct_threshold()

    # ── Today's R + dollar P&L ────────────────────────────────────────────────
    _risk_per_trade = float(os.getenv("RISK_DOLLARS", "150"))
    _today_r_vals = []
    for _r in results:
        _rv = _r.get("tiered_pnl_r") or _r.get("pnl_r_sim")
        if _rv is not None:
            try:
                _today_r_vals.append(float(_rv))
            except (TypeError, ValueError):
                pass
    _today_r_total  = sum(_today_r_vals)
    _today_dollar   = _today_r_total * _risk_per_trade

    # ── Running Sharpe (all-time settled trades) ──────────────────────────────
    _sharpe_str = "n/a"
    try:
        if _supabase_client:
            _all_r_rows = (
                _supabase_client.table("paper_trades")
                .select("tiered_pnl_r")
                .eq("user_id", USER_ID)
                .not_.is_("tiered_pnl_r", "null")
                .execute()
            ).data or []
            _all_r = [float(x["tiered_pnl_r"]) for x in _all_r_rows if x.get("tiered_pnl_r") is not None]
            if len(_all_r) >= 3:
                _mean = sum(_all_r) / len(_all_r)
                _std  = (_math.sqrt(sum((x - _mean) ** 2 for x in _all_r) / len(_all_r))) or 0
                if _std > 0:
                    _sharpe = (_mean / _std) * _math.sqrt(len(_all_r))
                    _sharpe_str = f"{_sharpe:.2f}"
                else:
                    _sharpe_str = "∞ (no losses)"
            else:
                _sharpe_str = f"n/a (need ≥3, have {len(_all_r)})"
    except Exception as _se:
        log.warning(f"[EOD] Running Sharpe calc failed: {_se}")

    lines = [
        f"📈 <b>EdgeIQ EOD Summary — {trade_date}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"✅ Wins: {len(wins)}   ❌ Losses: {len(losses)}   📋 Updated: {updated}",
    ]
    if wins or losses:
        wr = round(100 * len(wins) / max(1, len(wins) + len(losses)), 1)
        lines.append(f"📊 Win rate: <b>{wr}%</b>")
    if _today_r_vals:
        _r_sign = "+" if _today_r_total >= 0 else ""
        _d_sign = "+" if _today_dollar >= 0 else ""
        lines.append(
            f"💰 Today: <b>{_r_sign}{_today_r_total:.2f}R</b> "
            f"({_d_sign}${_today_dollar:,.0f} @ ${_risk_per_trade:.0f}/R)"
        )
    lines.append(f"📐 Running Sharpe: <b>{_sharpe_str}</b>")
    if best and best.get("aft_move_pct"):
        lines.append(
            f"🏆 Best mover: <b>{best['ticker']}</b> "
            f"{float(best['aft_move_pct']):+.1f}% ({best.get('win_loss','?')})"
        )
    lines.append(f"📐 IB filter: < {_ib_threshold:.1f}% of open price")
    if global_filtered or struct_filtered:
        filter_parts = []
        if struct_filtered:
            filter_parts.append(f"{struct_filtered} by structure floor")
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

    # IB context enrichment status
    _ib_ctx_icon = "🧠" if IB_CONTEXT_ENABLED else "💤"
    lines.append(f"{_ib_ctx_icon} IB context: <b>{'ACTIVE' if IB_CONTEXT_ENABLED else 'inactive'}</b>")

    # PM-IB directional gate status
    _pm_ib_icon = "🌅" if PM_IB_FILTER else "💤"
    lines.append(f"{_pm_ib_icon} PM-IB gate: <b>{'ACTIVE' if PM_IB_FILTER else 'inactive'}</b>"
                 + (" (bullish: pm_hi≥prev_ib_hi · bearish: pm_lo≤prev_ib_lo)" if PM_IB_FILTER else ""))

    # ── Adaptive Position Management A/B stats ────────────────────────────────
    # Only shown when the feature is enabled AND enough data exists in both arms
    # to make a meaningful comparison (≥10 settled trades per mode).
    if ADAPTIVE_POSITION_MGMT and _supabase_client:
        try:
            _ab_rows = (
                _supabase_client.table("paper_trades")
                .select("mgmt_mode, tiered_pnl_r")
                .eq("user_id", USER_ID)
                .not_.is_("tiered_pnl_r", "null")
                .execute()
            ).data or []

            def _ab_stats(rows):
                rs = [float(r["tiered_pnl_r"]) for r in rows if r.get("tiered_pnl_r") is not None]
                if not rs:
                    return 0, 0.0, 0.0
                wr  = 100.0 * sum(1 for v in rs if v > 0) / len(rs)
                avg = sum(rs) / len(rs)
                return len(rs), round(wr, 1), round(avg, 2)

            # 'adaptive_eligible' rows that were never adjusted are fixed-arm;
            # normalization to 'fixed' happens in _recalc_eod_pnl_r_recent.
            _fixed_rows    = [r for r in _ab_rows if r.get("mgmt_mode") in ("fixed", "adaptive_eligible", None, "")]
            _adaptive_rows = [r for r in _ab_rows if r.get("mgmt_mode") == "adaptive"]
            _fn, _fwr, _favg = _ab_stats(_fixed_rows)
            _an, _awr, _aavg = _ab_stats(_adaptive_rows)

            if _fn >= 10 and _an >= 10:
                lines.append(
                    f"🔬 <b>A/B Mgmt:</b> "
                    f"Fixed {_fwr}%WR {_favg:+.2f}R (n={_fn}) vs "
                    f"Adaptive {_awr}%WR {_aavg:+.2f}R (n={_an})"
                )
            elif _an > 0:
                lines.append(
                    f"🔬 Adaptive mgmt: <b>{_an}</b> settled trade(s) "
                    f"(need ≥10 each arm for A/B comparison)"
                )
        except Exception as _ab_e:
            log.debug(f"[AdaptiveMgmt] A/B EOD stats failed: {_ab_e}")

    tg_send("\n".join(lines))


def _load_drawdown_alert_state() -> dict:
    """Load the persisted drawdown alert state from disk.

    Schema:
        last_alerted_threshold  float | None  — the most negative threshold level
                                                 whose alert has already been sent
                                                 (e.g. -8.0 means the warning alert
                                                 fired; -12.0 means the critical alert
                                                 fired).  None = no active alert.
        last_alerted_date       str   | None  — ISO date when that alert was sent.
    """
    import json as _json
    try:
        if os.path.exists(_DRAWDOWN_ALERT_STATE_PATH):
            with open(_DRAWDOWN_ALERT_STATE_PATH) as _f:
                return _json.load(_f)
    except Exception as _e:
        log.debug(f"[Drawdown] Could not load alert state: {_e}")
    return {"last_alerted_threshold": None, "last_alerted_date": None}


def _save_drawdown_alert_state(state: dict) -> None:
    """Persist the drawdown alert state to disk."""
    import json as _json
    try:
        os.makedirs(os.path.dirname(_DRAWDOWN_ALERT_STATE_PATH), exist_ok=True)
        with open(_DRAWDOWN_ALERT_STATE_PATH, "w") as _f:
            _json.dump(state, _f, indent=2)
    except Exception as _e:
        log.warning(f"[Drawdown] Could not save alert state: {_e}")


def _compute_rolling_drawdown(r_values: list) -> float:
    """Return the peak-to-trough drawdown (always <= 0) over the supplied R series.

    The series is ordered oldest-first.  We walk the cumulative P&L curve and
    track the running peak; the drawdown at each step is (cum_pnl - peak).  The
    function returns the *minimum* (worst) drawdown seen in the window.

    Returns 0.0 when the list is empty or contains only gains.
    """
    if not r_values:
        return 0.0
    peak = 0.0
    cum  = 0.0
    worst = 0.0
    for r in r_values:
        cum += r
        if cum > peak:
            peak = cum
        dd = cum - peak
        if dd < worst:
            worst = dd
    return worst


def _check_and_alert_rolling_drawdown() -> None:
    """Fetch the last N settled trades, compute rolling drawdown, fire rate-limited
    Telegram warnings when configurable thresholds are breached.

    Thresholds are read from filter_config.json:
        drawdown_lookback_n   int   — window size (default 20)
        drawdown_warning_r    float — warning level, e.g. -8.0  (default -8.0)
        drawdown_critical_r   float — critical level, e.g. -12.0 (default -12.0)

    Rate limiting: the alert fires at most once per threshold crossing.  When
    drawdown recovers above all thresholds the state resets so a future
    deterioration triggers fresh alerts.
    """
    if not _supabase_client:
        return

    cfg           = _load_filter_config()
    lookback_n    = int(cfg.get("drawdown_lookback_n",  20))
    warn_thresh   = float(cfg.get("drawdown_warning_r", -8.0))
    crit_thresh   = float(cfg.get("drawdown_critical_r", -12.0))

    # Ensure critical is always the more-negative of the two.
    if warn_thresh < crit_thresh:
        warn_thresh, crit_thresh = crit_thresh, warn_thresh

    try:
        rows = (
            _supabase_client.table("paper_trades")
            .select("id, tiered_pnl_r, trade_date")
            .eq("user_id", USER_ID)
            .not_.is_("tiered_pnl_r", "null")
            .order("trade_date", desc=True)
            .order("id", desc=True)
            .limit(lookback_n)
            .execute()
        ).data or []
    except Exception as _db_e:
        log.warning(f"[Drawdown] DB fetch failed: {_db_e}")
        return

    if not rows:
        log.debug("[Drawdown] No settled trades found — skipping drawdown check.")
        return

    # Reverse to chronological order (oldest first) before computing drawdown.
    r_vals = [float(r["tiered_pnl_r"]) for r in reversed(rows) if r.get("tiered_pnl_r") is not None]
    if not r_vals:
        return

    dd = _compute_rolling_drawdown(r_vals)
    n  = len(r_vals)
    log.info(f"[Drawdown] Rolling drawdown over last {n} trades: {dd:+.2f}R  "
             f"(warn={warn_thresh:+.1f}R  crit={crit_thresh:+.1f}R)")

    state = _load_drawdown_alert_state()
    last_alerted = state.get("last_alerted_threshold")  # None or a float

    # ── Recovery reset ────────────────────────────────────────────────────────
    # If drawdown is now above all thresholds, clear the alert state so future
    # crossings are treated as fresh events.
    if dd > warn_thresh and last_alerted is not None:
        log.info(f"[Drawdown] Drawdown recovered to {dd:+.2f}R — resetting alert state.")
        _save_drawdown_alert_state({"last_alerted_threshold": None, "last_alerted_date": None})
        return

    # ── Determine which level to alert (if any) ───────────────────────────────
    today_str = datetime.now(EASTERN).strftime("%Y-%m-%d")

    if dd <= crit_thresh and last_alerted != crit_thresh:
        level   = "🚨 CRITICAL"
        thresh  = crit_thresh
        advice  = "Consider pausing the bot or switching back to the conservative filter."
    elif dd <= warn_thresh and last_alerted not in (warn_thresh, crit_thresh):
        level   = "⚠️ WARNING"
        thresh  = warn_thresh
        advice  = "Monitor closely. Consider tightening filters if losses continue."
    else:
        return

    cum_r   = sum(r_vals)
    msg = (
        f"{level} — Rolling Drawdown Alert\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 Drawdown: <b>{dd:+.2f}R</b>  (threshold: {thresh:+.1f}R)\n"
        f"📊 Window: last <b>{n}</b> settled trades\n"
        f"💰 Cumulative P&L in window: <b>{cum_r:+.2f}R</b>\n"
        f"📅 Date: {today_str}\n"
        f"💡 {advice}"
    )
    sent = tg_send(msg)
    if sent:
        log.warning(f"[Drawdown] {level} alert sent (dd={dd:+.2f}R, threshold={thresh:+.1f}R)")
        _save_drawdown_alert_state({"last_alerted_threshold": thresh, "last_alerted_date": today_str})
    else:
        log.warning(f"[Drawdown] {level} alert could not be sent via Telegram.")


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

    Runs FOUR screener passes and merges them:
      Pass 1 — Gap-of-day plays: ≥_PASS1_GAP_MIN_PCT% change · Float ≤_PASS1_FLOAT_MAX_M M
               · $_PASS1_PRICE_MIN–$_PASS1_PRICE_MAX · Avg vol ≥_PASS1_AVG_VOL_MIN_K K
               Catches high-momentum small-float catalysts.
      Pass 2 — Trend continuation plays: ≥_PASS2_TREND_MIN_PCT% chg · Float ≤_PASS2_FLOAT_MAX_M M
               · $_PASS2_PRICE_MIN–$_PASS2_PRICE_MAX · Avg vol ≥_PASS2_AVG_VOL_MIN_K K
               Above 20-day AND 50-day SMA
               Catches institutional-quality stocks extending multi-week trends.
               These produce cleaner Bullish/Bearish Break IB structure vs
               gap-and-stall small-floats that tend to read as Neutral/Ntrl Extreme.
      Pass 3 — Short squeeze candidates: Short float ≥_PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT%
               · Float ≤_PASS3_FLOAT_MAX_M M · ≥_PASS3_CHANGE_MIN_PCT% chg
               · $_PASS3_PRICE_MIN–$_PASS3_PRICE_MAX · Avg vol ≥_PASS3_AVG_VOL_MIN_K K
               High short interest + low float = covering pressure amplifies IB breaks.
               These are layered behind gap/trend fills; capped at 30 tickers.
      Pass 4 — Gap-down plays: ≤_PASS4_CHANGE_MAX_PCT% change · $_PASS4_PRICE_MIN–$_PASS4_PRICE_MAX
               · Avg vol ≥_PASS4_AVG_VOL_MIN_K K
               Bearish Break universe — tickers gapping down with structure.
               Only these tickers qualify for Bearish Break bracket orders.

    Gap plays take priority (listed first), then trend, then squeeze, then gap-down.
    Combined list is capped at 200 tickers.

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
    log.info("WATCHLIST REFRESH — fetching from Finviz (gap + trend + squeeze + gap-down passes)")
    log.info("=" * 60)
    _PASS1_GAP_MIN_PCT     = 2.0    # Pass 1 gap-of-day change threshold — shown in Telegram message
    _PASS1_FLOAT_MAX_M     = 100.0  # Pass 1 float cap (millions)
    _PASS1_PRICE_MIN       = PRICE_MIN   # Pass 1 price floor — mirrors global PRICE_MIN
    _PASS1_PRICE_MAX       = PRICE_MAX   # Pass 1 price ceiling — mirrors global PRICE_MAX
    _PASS1_AVG_VOL_MIN_K   = 1000   # Pass 1 avg-volume floor (thousands)
    _PASS2_TREND_MIN_PCT   = 1.0    # Pass 2 trend-continuation change threshold — shown in Telegram message
    _PASS2_FLOAT_MAX_M     = 500.0  # Pass 2 float cap (millions)
    _PASS2_PRICE_MIN       = 5.0    # Pass 2 price floor
    _PASS2_PRICE_MAX       = 50.0   # Pass 2 price ceiling
    _PASS2_AVG_VOL_MIN_K   = 2000   # Pass 2 avg-volume floor (thousands)
    _PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT = _get_effective_squeeze_short_float_pct()  # Pass 3 short-float threshold — shown in Telegram message; editable from Settings
    _PASS3_CHANGE_MIN_PCT  = 1.0    # Pass 3 minimum change threshold
    _PASS3_FLOAT_MAX_M     = 50.0   # Pass 3 float cap (millions)
    _PASS3_PRICE_MIN       = 1.0    # Pass 3 price floor
    _PASS3_PRICE_MAX       = 50.0   # Pass 3 price ceiling
    _PASS3_AVG_VOL_MIN_K   = 500    # Pass 3 avg-volume floor (thousands)
    _PASS4_CHANGE_MAX_PCT  = -3.0   # Pass 4 gap-down change ceiling (negative)
    _PASS4_PRICE_MIN       = PRICE_MIN   # Pass 4 price floor — mirrors global PRICE_MIN
    _PASS4_PRICE_MAX       = PRICE_MAX   # Pass 4 price ceiling — mirrors global PRICE_MAX
    _PASS4_AVG_VOL_MIN_K   = 500    # Pass 4 avg-volume floor (thousands)

    # _SHORT_FLOAT_FILTER_MAP and _PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT are
    # module-level constants (validated at startup); use them directly here.
    # If the startup block detected a misconfiguration it stored a pending
    # Telegram message in _SHORT_FLOAT_FALLBACK_TG_MSG — flush it now so the
    # operator is notified at the moment the first watchlist scan runs.
    global _SHORT_FLOAT_FALLBACK_TG_MSG
    if _SHORT_FLOAT_FALLBACK_TG_MSG:
        tg_send(_SHORT_FLOAT_FALLBACK_TG_MSG)
        _SHORT_FLOAT_FALLBACK_TG_MSG = None
    _pass3_short_float_filter = _SHORT_FLOAT_FILTER_MAP[_PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT]
    try:
        # ── Pass 1: gap-of-day ────────────────────────────────────────────────
        gap_tickers = fetch_finviz_watchlist(
            change_min_pct=_PASS1_GAP_MIN_PCT,
            float_max_m=_PASS1_FLOAT_MAX_M,
            price_min=_PASS1_PRICE_MIN,
            price_max=_PASS1_PRICE_MAX,
            avg_vol_min_k=_PASS1_AVG_VOL_MIN_K,
            max_tickers=100,
        )
        log.info(f"Gap-of-day screener: {len(gap_tickers)} tickers")

        # ── Pass 2: trend continuation ────────────────────────────────────────
        # Stocks in established uptrends on elevated volume → cleaner IB structure
        # and more Bullish Break / Bearish Break outcomes vs gap-and-stall noise.
        trend_tickers = fetch_finviz_watchlist(
            change_min_pct=_PASS2_TREND_MIN_PCT,
            float_max_m=_PASS2_FLOAT_MAX_M,
            price_min=_PASS2_PRICE_MIN,
            price_max=_PASS2_PRICE_MAX,
            avg_vol_min_k=_PASS2_AVG_VOL_MIN_K,
            max_tickers=100,
            extra_filters=["ta_sma20_pa", "ta_sma50_pa"],
        )
        log.info(f"Trend-continuation screener: {len(trend_tickers)} tickers")

        # ── Pass 3: short squeeze candidates ─────────────────────────────────
        # High short interest (≥_PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT% float short) + low float →
        # covering pressure amplifies IB breakouts. When a heavily shorted stock clears IB high,
        # shorts are forced to cover into the move on top of buyer demand.
        squeeze_tickers = fetch_finviz_watchlist(
            change_min_pct=_PASS3_CHANGE_MIN_PCT,
            float_max_m=_PASS3_FLOAT_MAX_M,
            price_min=_PASS3_PRICE_MIN,
            price_max=_PASS3_PRICE_MAX,
            avg_vol_min_k=_PASS3_AVG_VOL_MIN_K,
            max_tickers=50,
            extra_filters=[_pass3_short_float_filter],
        )
        log.info(f"Short-squeeze screener: {len(squeeze_tickers)} tickers")

        # ── Pass 4: gap-down (Bearish Break universe) ─────────────────────────
        # Stocks gapping down ≥3% with meaningful volume.  These are the ONLY
        # tickers eligible for Bearish Break bracket orders — the gap-up screener
        # universe produces 40% WR on Bearish Break (below random).  Gap-down
        # stocks with IB structure breaking below IB low are the intended target.
        gap_down_tickers = fetch_gap_down_watchlist(
            change_max_pct=_PASS4_CHANGE_MAX_PCT,
            price_min=_PASS4_PRICE_MIN,
            price_max=_PASS4_PRICE_MAX,
            avg_vol_min_k=_PASS4_AVG_VOL_MIN_K,
            max_tickers=60,
        )
        log.info(f"Gap-down screener (Bearish Break universe): {len(gap_down_tickers)} tickers")

        # ── Merge: gap → trend → squeeze → gap-down (deduped), cap at 200 ────
        merged: list[str] = list(gap_tickers)
        for t in trend_tickers:
            if t not in merged:
                merged.append(t)
        for t in squeeze_tickers:
            if t not in merged:
                merged.append(t)
        for t in gap_down_tickers:
            if t not in merged:
                merged.append(t)
        merged = merged[:200]

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
        for t in gap_down_tickers:
            if t not in _TICKER_SCREENER_PASS:
                _TICKER_SCREENER_PASS[t] = "gap_down"
        _save_screener_pass_cache()  # persist to disk — survives bot restarts

        if merged:
            saved = save_watchlist(merged, user_id=USER_ID)
            if saved:
                TICKERS = merged
                log.info(
                    f"Watchlist updated: {len(merged)} total tickers "
                    f"({len(gap_tickers)} gap · {len(trend_tickers)} trend · "
                    f"{len(squeeze_tickers)} squeeze · {len(gap_down_tickers)} gap-down) → "
                    f"{', '.join(merged)}"
                )
                _scan_note = "Midday refresh complete." if midday else "Morning scan at 10:47 AM ET..."
                _gd_line = (
                    f"Gap-Down: ≤{_PASS4_CHANGE_MAX_PCT:.0f}% chg · "
                    f"${_PASS4_PRICE_MIN:.0f}–${_PASS4_PRICE_MAX:.0f} · "
                    f"vol ≥{_PASS4_AVG_VOL_MIN_K}K ({len(gap_down_tickers)} tickers)\n"
                    if gap_down_tickers else ""
                )
                tg_send(
                    f"📋 <b>Watchlist Refreshed — {date.today()}</b>\n"
                    f"<b>{len(merged)} tickers</b> ({len(gap_tickers)} gap-of-day · "
                    f"{len(trend_tickers)} trend · {len(squeeze_tickers)} squeeze · "
                    f"{len(gap_down_tickers)} gap-down)\n"
                    f"Gap ≥{_PASS1_GAP_MIN_PCT:.1f}%: Float ≤{_PASS1_FLOAT_MAX_M:.0f}M · "
                    f"${_PASS1_PRICE_MIN:.0f}–${_PASS1_PRICE_MAX:.0f} · vol ≥{_PASS1_AVG_VOL_MIN_K}K\n"
                    f"Trend: ≥{_PASS2_TREND_MIN_PCT:.0f}% chg · Float ≤{_PASS2_FLOAT_MAX_M:.0f}M · "
                    f"${_PASS2_PRICE_MIN:.0f}–${_PASS2_PRICE_MAX:.0f} · vol ≥{_PASS2_AVG_VOL_MIN_K}K · Above 20+50 SMA\n"
                    f"Squeeze: Short float ≥{_PASS3_SQUEEZE_SHORT_FLOAT_MIN_PCT:.0f}% · "
                    f"Float ≤{_PASS3_FLOAT_MAX_M:.0f}M · ≥{_PASS3_CHANGE_MIN_PCT:.0f}% chg · "
                    f"${_PASS3_PRICE_MIN:.0f}–${_PASS3_PRICE_MAX:.0f} · vol ≥{_PASS3_AVG_VOL_MIN_K}K\n"
                    f"{_gd_line}"
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
                        gap_down_tickers=gap_down_tickers,
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

    # Load TCS thresholds early so we can tag tcs_floor on every row before logging.
    _tcs_thresholds = load_tcs_thresholds(default=MIN_TCS)

    # Tag each result with screener_pass and tcs_floor before logging so the
    # initial DB row carries both fields (previously only written at order placement).
    for _r in results:
        if not _r.get("screener_pass"):
            _t = _r.get("ticker", "")
            _sp = _TICKER_SCREENER_PASS.get(_t) or _TICKER_SCREENER_PASS.get(_t.upper())
            if _sp:
                _r["screener_pass"] = _sp
        if _r.get("_struct_tcs_floor") is None:
            _r["_struct_tcs_floor"] = _struct_tcs_floor(_r, _tcs_thresholds, effective_min_tcs)

    # ── IB Context Enrichment (toggle: IB_CONTEXT_ENABLED=1) ─────────────────
    # Fetches prior-day IB and pre-market range, applies ±0–5 TCS delta.
    # Must run BEFORE log_paper_trades so enriched TCS is stored.
    try:
        _enrich_with_ib_context(results, str(today))
    except Exception as _ice:
        log.warning(f"IB context enrichment failed (non-critical, continuing): {_ice}")

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
            _outcome = _place_order_for_setup(r, "morning")
            _send_skip_outcome_tg(r.get("ticker", "?"), _outcome)
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

    # Load TCS thresholds early so tcs_floor is tagged on every row before logging.
    _tcs_thresholds = load_tcs_thresholds(default=MIN_TCS)

    # Tag screener_pass and tcs_floor before logging.
    for _r in results:
        if not _r.get("screener_pass"):
            _t = _r.get("ticker", "")
            _sp = _TICKER_SCREENER_PASS.get(_t) or _TICKER_SCREENER_PASS.get(_t.upper())
            if _sp:
                _r["screener_pass"] = _sp
        if _r.get("_struct_tcs_floor") is None:
            _r["_struct_tcs_floor"] = _struct_tcs_floor(_r, _tcs_thresholds, effective_min_tcs)

    # ── IB Context Enrichment (toggle: IB_CONTEXT_ENABLED=1) ─────────────────
    try:
        _enrich_with_ib_context(results, str(today))
    except Exception as _ice:
        log.warning(f"IB context enrichment failed (non-critical, continuing): {_ice}")

    # ── Log ALL intraday results to paper_trades (dedup by ticker+date+scan_type)
    logged = log_paper_trades(results, user_id=USER_ID, min_tcs=effective_min_tcs)
    log.info(f"Intraday paper trades logged: {logged.get('saved',0)} new, {logged.get('skipped',0)} already existed")

    # Log context levels (S/R, VWAP, MACD) for adaptive exit analysis
    try:
        log_context_levels(results, str(today))
    except Exception as _ctx_err:
        log.warning(f"Context level logging failed (non-critical): {_ctx_err}")

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
            _outcome = _place_order_for_setup(r, "intraday")
            _send_skip_outcome_tg(r.get("ticker", "?"), _outcome)
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
                    .select("id,actual_outcome,ib_high,ib_low,close_price,mgmt_mode")
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
            _eod_patch = {"eod_pnl_r": round(float(eod_pnl_r), 6)}
            # Normalize 'adaptive_eligible' to 'fixed' for unadjusted trades so
            # every settled row carries only a terminal mgmt_mode state.
            if row.get("mgmt_mode") == "adaptive_eligible":
                _eod_patch["mgmt_mode"] = "fixed"
            _supabase_client.table("paper_trades").update(
                _eod_patch
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
            f"order_id_matched: {rec.get('order_id_matched', 0)} | "
            f"exit_fills: {rec.get('exit_fills', 0)} | "
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

    # ── Rolling drawdown check ─────────────────────────────────────────────
    try:
        _check_and_alert_rolling_drawdown()
    except Exception as _dd_exc:
        log.warning(f"[Drawdown] Rolling drawdown check failed (non-critical): {_dd_exc}")

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


_TCS_INTRADAY_WR_WARN     = 0.80  # WARNING  — auto-raise tcs_intraday_min to 40
_TCS_INTRADAY_WR_CRITICAL = 0.75  # CRITICAL — auto-raise tcs_intraday_min to 45


def _check_tcs_intraday_edge_degradation():
    """Check 60-day rolling WR for TCS 35-49 intraday; alert and auto-adjust if degraded.

    Thresholds (from task spec):
      WR <  80% (WARNING):  raise tcs_intraday_min → 40 in filter_config.json + log WARNING
      WR <  75% (CRITICAL): raise tcs_intraday_min → 45 in filter_config.json + log CRITICAL

    Called nightly from nightly_recalibration() after both brain updates complete.
    """
    try:
        result = check_tcs_intraday_rolling_wr(user_id=USER_ID, lookback_days=60)
    except Exception as exc:
        log.warning(f"[TCS-IntraWR] Rolling WR check failed: {exc}")
        return

    if result.get("error"):
        log.warning(f"[TCS-IntraWR] Supabase query error: {result['error']}")
        return

    wr    = result.get("win_rate")
    total = result.get("total", 0)
    wins  = result.get("wins", 0)
    wr_pct = f"{wr * 100:.1f}%" if wr is not None else "N/A"

    log.info(
        f"[TCS-IntraWR] 60-day rolling WR for TCS 35-49 intraday: "
        f"{wr_pct} ({wins}/{total} trades)"
    )

    if wr is None:
        log.info("[TCS-IntraWR] Not enough data (<10 resolved trades in window). No action.")
        return

    if wr >= _TCS_INTRADAY_WR_WARN:
        log.info(
            f"[TCS-IntraWR] Edge healthy — {wr_pct} ≥ "
            f"{_TCS_INTRADAY_WR_WARN * 100:.0f}% threshold. No action needed."
        )
        return

    if wr < _TCS_INTRADAY_WR_CRITICAL:
        new_floor = 45
        level     = "CRITICAL"
        emoji     = "🚨"
        threshold_label = f"< {int(_TCS_INTRADAY_WR_CRITICAL * 100)}%"
        log.critical(
            f"[TCS-IntraWR] CRITICAL — 60-day intraday WR {wr_pct} < "
            f"{_TCS_INTRADAY_WR_CRITICAL * 100:.0f}%. "
            f"Degradation target floor: {new_floor} (monotonic check follows)."
        )
    else:
        new_floor = 40
        level     = "WARNING"
        emoji     = "⚠️"
        threshold_label = f"< {int(_TCS_INTRADAY_WR_WARN * 100)}%"
        log.warning(
            f"[TCS-IntraWR] WARNING — 60-day intraday WR {wr_pct} < "
            f"{_TCS_INTRADAY_WR_WARN * 100:.0f}%. "
            f"Degradation target floor: {new_floor} (monotonic check follows)."
        )

    # Update filter_config.json — monotonic: never lower an already-stricter floor
    try:
        cfg = {}
        if os.path.exists(_FILTER_CONFIG_PATH):
            with open(_FILTER_CONFIG_PATH, "r", encoding="utf-8") as _fcf:
                cfg = json.load(_fcf)
        old_floor = cfg.get("tcs_intraday_min", MIN_TCS)
        # Never decrease strictness — if the existing floor is already higher,
        # leave it in place and skip the write entirely.
        effective_floor = max(old_floor, new_floor)
        if effective_floor == old_floor and old_floor > new_floor:
            log.info(
                f"[TCS-IntraWR] Floor unchanged — existing tcs_intraday_min ({old_floor}) "
                f"is already stricter than the degradation target ({new_floor}). "
                f"No config update needed."
            )
        else:
            cfg["tcs_intraday_min"] = effective_floor
            cfg["applied_at"] = datetime.now(EASTERN).strftime("%Y-%m-%dT%H:%M:%SZ")
            cfg["_tcs_intraday_autoadjust_note"] = (
                f"Auto-raised by edge-degradation check ({level}): "
                f"60-day rolling WR {wr_pct} ({wins}/{total} trades). "
                f"Previous floor: {old_floor}. "
                f"Timestamp: {cfg['applied_at']}"
            )
            with open(_FILTER_CONFIG_PATH, "w", encoding="utf-8") as _fcf:
                json.dump(cfg, _fcf, indent=2)
            # Invalidate in-process cache so the bot picks up the new floor immediately
            global _FILTER_CONFIG_CACHE
            _FILTER_CONFIG_CACHE = None
            log.info(
                f"[TCS-IntraWR] filter_config.json updated: "
                f"tcs_intraday_min {old_floor} → {effective_floor}"
            )
            new_floor = effective_floor  # use for Telegram message below
    except Exception as exc:
        log.error(f"[TCS-IntraWR] Failed to update filter_config.json: {exc}")

    # Telegram alert
    tg_send(
        f"{emoji} <b>TCS 35-49 Intraday Edge — {level}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"60-day rolling WR: <b>{wr_pct}</b> ({wins}/{total} trades)\n"
        f"Trigger: {threshold_label}\n"
        f"Action: <b>tcs_intraday_min raised → {new_floor}</b>"
    )


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

    # ── TCS 35-49 intraday edge-degradation trip-wire ──────────────────────────
    # Checks the 60-day rolling WR for the TCS 35-49 intraday band.
    # Automatically raises tcs_intraday_min (→40 at WARNING, →45 at CRITICAL)
    # and sends a Telegram alert if the edge starts to erode.
    try:
        _check_tcs_intraday_edge_degradation()
    except Exception as exc:
        log.warning(f"TCS intraday edge-degradation check failed: {exc}")

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


# ── Adaptive Position Management helpers ──────────────────────────────────────

def _fetch_pm_last_price(ticker: str) -> float:
    """Return the most recent pre-market close price for ticker (as of call time).

    Fetches 1-minute SIP bars from 4:00 AM ET to now.  Called at ~8:30 AM so
    the window covers ~4.5 hours of pre-market action.
    Returns 0.0 on error or when no bars are available.
    """
    import requests as _req
    ET = pytz.timezone("America/New_York")
    now_et = datetime.now(ET)
    today_str = now_et.strftime("%Y-%m-%d")
    dt = datetime.strptime(today_str, "%Y-%m-%d")
    start = ET.localize(dt.replace(hour=4, minute=0)).isoformat()
    end = now_et.isoformat()
    try:
        r = _req.get(
            f"https://data.alpaca.markets/v2/stocks/{ticker}/bars",
            headers={
                "APCA-API-KEY-ID":     ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            },
            params={
                "start":     start,
                "end":       end,
                "timeframe": "1Min",
                "feed":      "sip",
                "limit":     400,
            },
            timeout=10,
        )
        if r.status_code == 200:
            bars = r.json().get("bars") or []
            if bars:
                return float(bars[-1]["c"])
    except Exception as _e:
        log.debug(f"[AdaptiveMgmt] PM last-price fetch failed for {ticker}: {_e}")
    return 0.0


_ADAPTIVE_TP_RAISE_MULT_DEFAULT = 0.5
_ADAPTIVE_STOP_TIGHTEN_FRAC_DEFAULT = 1.0  # 1.0 = stop moves to exact IB midpoint


def _load_tp_raise_mult() -> float:
    """Load the calibrated TP-raise multiplier from adaptive_exits.json.

    Returns the value of ``tp_raise_mult`` written by calibrate_adaptive_mgmt.py,
    or the hard-coded default (0.5R) when the key is absent or the file cannot
    be read.  The file is re-read on every call so that an --apply run by the
    calibration script takes effect without a bot restart.
    """
    try:
        import json as _json
        with open(_ADAPTIVE_EXIT_CONFIG_PATH) as _f:
            cfg = _json.load(_f)
        val = cfg.get("tp_raise_mult")
        if val is not None:
            return float(val)
    except Exception:
        pass
    return _ADAPTIVE_TP_RAISE_MULT_DEFAULT


def _load_stop_tighten_frac() -> float:
    """Load the calibrated stop-tighten fraction from adaptive_exits.json.

    Returns the value of ``stop_tighten_frac`` written by calibrate_adaptive_mgmt.py,
    or the hard-coded default (1.0 = exact IB midpoint) when the key is absent
    or the file cannot be read.  The file is re-read on every call so calibration
    updates take effect without a bot restart.

    The loaded value is clamped to [0.0, 1.0]; a value outside this range would
    move the stop away from the IB-midpoint direction and is treated as invalid.
    """
    try:
        import json as _json
        with open(_ADAPTIVE_EXIT_CONFIG_PATH) as _f:
            cfg = _json.load(_f)
        val = cfg.get("stop_tighten_frac")
        if val is not None:
            frac = float(val)
            if 0.0 <= frac <= 1.0:
                return frac
    except Exception:
        pass
    return _ADAPTIVE_STOP_TIGHTEN_FRAC_DEFAULT


def _compute_adaptive_adjustments(
    direction: str,
    entry: float,
    stop: float,
    target: float,
    ib_high: float,
    ib_low: float,
    pm_last: float,
    tp_raise_mult: float = _ADAPTIVE_TP_RAISE_MULT_DEFAULT,
    stop_tighten_frac: float = _ADAPTIVE_STOP_TIGHTEN_FRAC_DEFAULT,
) -> "dict | None":
    """Compute adaptive TP/stop adjustments based on pre-market acceptance.

    Pure function — no side effects.  Returns None when no adjustment is
    warranted (e.g. missing data or price exactly at the break level).

    Bullish Break logic:
      pm_last > ib_high  → PM accepted above IB → raise TP by +tp_raise_mult×R (stop unchanged)
      pm_last < ib_high  → PM pulled inside IB  → tighten stop (TP unchanged)

    Bearish Break logic (mirror):
      pm_last < ib_low   → PM accepted below IB → lower TP by tp_raise_mult×R (stop unchanged)
      pm_last > ib_low   → PM pulled inside IB  → tighten stop (TP unchanged)

    tp_raise_mult is read from adaptive_exits.json by the caller (default 0.5).
    stop_tighten_frac controls where the tightened stop is placed relative to
    the IB midpoint: new_stop = entry + frac*(ib_mid - entry).
      frac=1.0 → stop moves to exact IB midpoint (original hardcoded behaviour).
      frac=0.5 → stop moves halfway between entry and IB midpoint.
    Both values are written to adaptive_exits.json by calibrate_adaptive_mgmt.py
    once ≥ 50 adaptive trades have settled.

    Returns dict with keys: action, new_stop, new_tp, tp_adjusted_r
    """
    if entry <= 0 or stop <= 0 or target <= 0 or pm_last <= 0:
        return None
    if ib_high <= 0 or ib_low <= 0 or ib_high <= ib_low:
        return None

    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return None

    ib_mid = (ib_high + ib_low) / 2.0
    # Fractional stop placement: frac=1.0 → ib_mid, frac=0.0 → entry
    tightened_stop = entry + stop_tighten_frac * (ib_mid - entry)

    if "Bullish Break" in direction:
        if pm_last > ib_high:
            new_tp   = round(target + tp_raise_mult * stop_dist, 2)
            new_stop = stop
            action   = "TP_RAISED"
            tp_r     = round((new_tp - entry) / stop_dist, 2)
        elif pm_last < ib_high:
            # Stop-tighten is only valid if the new stop is BELOW current PM price;
            # otherwise the new stop would be at or above market (triggers immediately).
            if tightened_stop >= pm_last:
                return None
            new_stop = round(tightened_stop, 2)
            new_tp   = target
            action   = "STOP_TIGHTENED"
            tp_r     = round((target - entry) / stop_dist, 2)
        else:
            return None

    elif "Bearish Break" in direction:
        if pm_last < ib_low:
            new_tp   = round(target - tp_raise_mult * stop_dist, 2)
            new_stop = stop
            action   = "TP_RAISED"
            tp_r     = round((entry - new_tp) / stop_dist, 2)
        elif pm_last > ib_low:
            # Stop-tighten is only valid if the new stop is ABOVE current PM price;
            # otherwise the new stop would be at or below market (triggers immediately).
            if tightened_stop <= pm_last:
                return None
            new_stop = round(tightened_stop, 2)
            new_tp   = target
            action   = "STOP_TIGHTENED"
            tp_r     = round((entry - target) / stop_dist, 2)
        else:
            return None

    else:
        return None

    return {
        "action":        action,
        "new_stop":      new_stop,
        "new_tp":        new_tp,
        "tp_adjusted_r": tp_r,
    }


def _alpaca_place_oco_exit(
    ticker: str,
    qty: int,
    exit_side: str,
    stop_price: float,
    tp_price: float,
) -> dict:
    """Place an OCO (One-Cancels-Other) exit order for an already-open position.

    exit_side = "sell" for long positions, "buy" for short (cover).
    The limit leg fires at tp_price; the stop leg fires at stop_price.
    Returns {'ok': bool, 'order_id': str, 'error': str}.
    """
    import requests as _req
    import json as _json_mod
    base = "https://paper-api.alpaca.markets" if IS_PAPER_ALPACA else "https://api.alpaca.markets"
    headers = {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        "Content-Type":        "application/json",
    }
    # Alpaca OCO format: parent IS the limit (take-profit) leg, so "limit_price"
    # must be at the top level.  The "take_profit" nested key is bracket/OTO
    # syntax; including it in an OCO payload causes a 422 rejection from Alpaca.
    # Using "gtc" so exit orders survive overnight / across sessions.
    payload = {
        "symbol":        ticker.upper(),
        "qty":           str(abs(int(qty))),
        "side":          exit_side,
        "type":          "limit",
        "limit_price":   str(round(tp_price, 2)),
        "time_in_force": "gtc",
        "order_class":   "oco",
        "stop_loss":     {"stop_price": str(round(stop_price, 2))},
    }
    try:
        r = _req.post(
            f"{base}/v2/orders",
            headers=headers,
            data=_json_mod.dumps(payload),
            timeout=10,
        )
        if r.status_code in (200, 201):
            data = r.json()
            return {"ok": True, "order_id": data.get("id", ""), "error": ""}
        return {
            "ok":       False,
            "order_id": "",
            "error":    f"HTTP {r.status_code}: {r.text[:300]}",
        }
    except Exception as _e:
        return {"ok": False, "order_id": "", "error": str(_e)}


def _pre_open_position_review() -> None:
    """8:30 AM ET — adaptively adjust TP/stop for open Alpaca positions.

    For each open Alpaca paper position that was logged as a bot-managed
    paper_trade today:

      1. Reads entry/stop/target and IB levels from the paper_trades row.
      2. Fetches the current pre-market price (4:00–8:30 AM ET window).
      3. Applies the adaptive rule (tp_raise_mult read from adaptive_exits.json,
         default 0.5; auto-calibrated by calibrate_adaptive_mgmt.py):
           Bullish Break, PM above IB high → raise TP by +tp_raise_mult×R
           Bullish Break, PM inside IB     → tighten stop to IB midpoint
           Bearish Break, PM below IB low  → lower TP by tp_raise_mult×R (extends target)
           Bearish Break, PM inside IB     → tighten stop to IB midpoint
      4. Cancels the current bracket legs for the ticker.
      5. Places a new OCO exit order at the adjusted TP / stop levels.
      6. Stamps paper_trades with mgmt_mode='adaptive' and tp_adjusted_r.

    Disabled when ADAPTIVE_POSITION_MGMT=0 (default).
    All failures are caught and logged — never blocks the main scheduling loop.
    """
    if not ADAPTIVE_POSITION_MGMT:
        return

    log.info("[AdaptiveMgmt] Starting pre-open position review...")

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        log.warning("[AdaptiveMgmt] Alpaca credentials missing — skipping review")
        return

    try:
        positions = _alpaca_get_positions()
    except Exception as _pe:
        log.warning(f"[AdaptiveMgmt] Could not fetch Alpaca positions: {_pe}")
        return

    if not positions:
        log.info("[AdaptiveMgmt] No open positions — nothing to review")
        return

    adjusted = 0

    for pos in positions:
        ticker = (pos.get("symbol") or "").upper()
        if not ticker:
            continue

        side = pos.get("side", "long")
        qty  = abs(int(float(pos.get("qty") or 0)))
        if qty <= 0:
            continue

        # ── Look up the most-recent open paper_trade for this ticker ─────────
        # We query by ticker + open status (eod_pnl_r IS NULL), ordered by
        # trade_date DESC so we get the most recent carryover position even
        # if it was entered on a prior session day.
        if not _supabase_client:
            continue
        try:
            _pt_resp = (
                _supabase_client.table("paper_trades")
                .select(
                    "id, ib_high, ib_low, predicted, "
                    "entry_price_sim, stop_price_sim, target_price_sim"
                )
                .eq("user_id", USER_ID)
                .eq("ticker", ticker)
                .eq("mgmt_mode", "adaptive_eligible")
                .is_("eod_pnl_r", "null")
                .order("trade_date", desc=True)
                .limit(1)
                .execute()
            )
            pt_rows = _pt_resp.data or []
        except Exception as _qe:
            log.warning(f"[AdaptiveMgmt] {ticker} — DB lookup failed: {_qe}")
            continue

        if not pt_rows:
            log.debug(f"[AdaptiveMgmt] {ticker} — no open paper_trade row; skipping")
            continue

        row       = pt_rows[0]
        row_id    = row.get("id")
        ib_high   = float(row.get("ib_high")          or 0)
        ib_low    = float(row.get("ib_low")            or 0)
        entry     = float(row.get("entry_price_sim")   or 0)
        stop      = float(row.get("stop_price_sim")    or 0)
        target    = float(row.get("target_price_sim")  or 0)
        direction = row.get("predicted") or ""

        if not all([ib_high, ib_low, entry, stop, target, direction]):
            log.info(f"[AdaptiveMgmt] {ticker} — incomplete data (ib/entry/stop/target/direction); skipping")
            continue

        # ── Fetch current pre-market price ───────────────────────────────────
        pm_last = _fetch_pm_last_price(ticker)
        if pm_last <= 0:
            log.info(f"[AdaptiveMgmt] {ticker} — no PM price data; skipping")
            continue

        # ── Compute adjustment ────────────────────────────────────────────────
        # Both calibration params are loaded fresh on each position so that
        # calibrate_adaptive_mgmt.py --apply takes effect without a bot restart.
        _tp_mult    = _load_tp_raise_mult()
        _st_frac    = _load_stop_tighten_frac()
        adj = _compute_adaptive_adjustments(
            direction=direction,
            entry=entry,
            stop=stop,
            target=target,
            ib_high=ib_high,
            ib_low=ib_low,
            pm_last=pm_last,
            tp_raise_mult=_tp_mult,
            stop_tighten_frac=_st_frac,
        )
        if adj is None:
            log.info(
                f"[AdaptiveMgmt] {ticker} — no adjustment warranted "
                f"(direction={direction} pm={pm_last:.2f} ib={ib_low:.2f}–{ib_high:.2f})"
            )
            continue

        action   = adj["action"]
        new_stop = adj["new_stop"]
        new_tp   = adj["new_tp"]
        tp_r     = adj["tp_adjusted_r"]

        log.info(
            f"[AdaptiveMgmt] {ticker} — {action}: "
            f"stop {stop:.2f}→{new_stop:.2f}  tp {target:.2f}→{new_tp:.2f}  "
            f"tp_r={tp_r:.2f}R  (pm={pm_last:.2f} vs ib_break="
            f"{'ib_high' if 'Bullish' in direction else 'ib_low'} "
            f"{ib_high if 'Bullish' in direction else ib_low:.2f})"
        )

        exit_side = "sell" if "Bullish" in direction else "buy"

        # ── Step 1: Snapshot + cancel old bracket legs ────────────────────────
        # We cancel FIRST so Alpaca's qty-reservation for existing exit legs
        # does not cause the replacement OCO to be rejected.
        # If cancel itself fails we skip the adjustment (position remains safe).
        old_order_ids = _alpaca_get_open_order_ids(ticker)
        try:
            cancelled = _alpaca_cancel_orders_for_ticker(ticker, specific_ids=old_order_ids)
            log.info(f"[AdaptiveMgmt] {ticker} — cancelled {cancelled} old bracket order(s)")
        except Exception as _ce:
            log.warning(f"[AdaptiveMgmt] {ticker} — cancel failed: {_ce}; skipping adjustment")
            continue

        # ── Step 2: Place new OCO exit with adjusted levels ───────────────────
        oca_res = _alpaca_place_oco_exit(
            ticker=ticker,
            qty=qty,
            exit_side=exit_side,
            stop_price=new_stop,
            tp_price=new_tp,
        )

        if not oca_res.get("ok"):
            # ── Step 3: Rollback — restore original bracket on OCO failure ────
            log.warning(
                f"[AdaptiveMgmt] {ticker} — adaptive OCO failed: {oca_res.get('error')}; "
                f"attempting rollback to original bracket"
            )
            rollback_res = _alpaca_place_oco_exit(
                ticker=ticker,
                qty=qty,
                exit_side=exit_side,
                stop_price=stop,    # original stop from DB
                tp_price=target,    # original target from DB
            )
            if rollback_res.get("ok"):
                log.info(f"[AdaptiveMgmt] {ticker} — rollback OCO placed successfully")
                tg_send(
                    f"⚠️ <b>Adaptive Mgmt — OCO Failed, Bracket Restored</b>\n"
                    f"Ticker: <b>{ticker}</b> ({direction})\n"
                    f"Adaptive OCO error: {oca_res.get('error','unknown')}\n"
                    f"Original bracket restored — position is protected."
                )
            else:
                log.error(
                    f"[AdaptiveMgmt] {ticker} — CRITICAL: rollback also failed: "
                    f"{rollback_res.get('error')}. Position may be unprotected!"
                )
                tg_send(
                    f"🚨 <b>CRITICAL — {ticker} Bracket LOST</b>\n"
                    f"Adaptive OCO failed AND rollback failed.\n"
                    f"Position may be <b>UNPROTECTED</b> — check Alpaca immediately!"
                )
            continue

        new_order_id = oca_res.get("order_id", "")

        # ── Persist to paper_trades ───────────────────────────────────────────
        if row_id and _supabase_client:
            try:
                _supabase_client.table("paper_trades").update({
                    "mgmt_mode":     "adaptive",
                    "tp_adjusted_r": tp_r,
                    "alpaca_order_id": new_order_id or None,
                }).eq("id", row_id).execute()
            except Exception as _ue:
                log.warning(f"[AdaptiveMgmt] {ticker} — DB update failed (non-fatal): {_ue}")

        adjusted += 1

        # ── Telegram notification ─────────────────────────────────────────────
        _action_label = (
            f"TP raised +{_tp_mult:.2f}R" if action == "TP_RAISED" else "Stop → IB mid"
        )
        _dir_icon = "🟡" if "Bullish" in direction else "🔴"
        tg_send(
            f"🔧 <b>Adaptive Mgmt — {ticker}</b>\n"
            f"{_dir_icon} {direction}\n"
            f"PM last: <b>${pm_last:.2f}</b> | "
            f"{'IB high' if 'Bullish' in direction else 'IB low'}: "
            f"<b>${ib_high if 'Bullish' in direction else ib_low:.2f}</b>\n"
            f"Action: <b>{_action_label}</b>\n"
            f"Stop: ${stop:.2f}→<b>${new_stop:.2f}</b>  "
            f"TP: ${target:.2f}→<b>${new_tp:.2f}</b>\n"
            f"New TP: <b>{tp_r:.2f}R</b>  "
            f"<code>{new_order_id[:8] if new_order_id else 'n/a'}…</code>"
        )

    log.info(
        f"[AdaptiveMgmt] Review complete — {adjusted}/{len(positions)} position(s) adjusted"
    )


def _ensure_ib_context_columns() -> bool:
    """Add IB context columns to paper_trades if they are missing.

    Only logs the migration SQL when IB_CONTEXT_ENABLED is True — no noise
    when the feature is turned off. Returns True on success or unexpected error.
    """
    if not _supabase_client:
        return True
    try:
        _supabase_client.table("paper_trades").select(
            "prev_ib_high, prev_ib_low, pm_range_pct, ib_vs_prev_ib_pct"
        ).limit(1).execute()
        if IB_CONTEXT_ENABLED:
            log.info("IB context columns present ✅")
        return True
    except Exception as e:
        err = str(e).lower()
        if "column" in err or "does not exist" in err or "42703" in err:
            if IB_CONTEXT_ENABLED:
                log.warning(
                    "\n"
                    "══════════════════════════════════════════════════════════\n"
                    "  IB context columns are MISSING in paper_trades.\n"
                    "  IB_CONTEXT_ENABLED=1 is set but data won't be stored\n"
                    "  until you run this in Supabase → SQL Editor:\n\n"
                    "  ALTER TABLE paper_trades\n"
                    "    ADD COLUMN IF NOT EXISTS prev_ib_high      REAL,\n"
                    "    ADD COLUMN IF NOT EXISTS prev_ib_low       REAL,\n"
                    "    ADD COLUMN IF NOT EXISTS pm_range_pct      REAL,\n"
                    "    ADD COLUMN IF NOT EXISTS ib_vs_prev_ib_pct REAL;\n\n"
                    "  Then restart the Paper Trader Bot workflow.\n"
                    "══════════════════════════════════════════════════════════"
                )
            return False
        log.debug(f"_ensure_ib_context_columns check: {e}")
        return True


def _ensure_adaptive_mgmt_columns() -> bool:
    """Add Adaptive Position Management columns to paper_trades if missing.

    Only logs the migration SQL when ADAPTIVE_POSITION_MGMT is True — no noise
    when the feature is turned off.  Returns True on success or unexpected error.
    """
    if not _supabase_client:
        return True
    try:
        _supabase_client.table("paper_trades").select(
            "mgmt_mode, tp_adjusted_r"
        ).limit(1).execute()
        if ADAPTIVE_POSITION_MGMT:
            log.info("Adaptive mgmt columns present ✅")
        return True
    except Exception as e:
        err = str(e).lower()
        if "column" in err or "does not exist" in err or "42703" in err:
            if ADAPTIVE_POSITION_MGMT:
                log.warning(
                    "\n"
                    "══════════════════════════════════════════════════════════\n"
                    "  Adaptive Position Management columns are MISSING.\n"
                    "  ADAPTIVE_POSITION_MGMT=1 is set but adjustments won't\n"
                    "  be persisted until you run this in Supabase → SQL Editor:\n\n"
                    "  ALTER TABLE paper_trades\n"
                    "    ADD COLUMN IF NOT EXISTS mgmt_mode     VARCHAR DEFAULT 'fixed',\n"
                    "    ADD COLUMN IF NOT EXISTS tp_adjusted_r REAL;\n\n"
                    "  Then restart the Paper Trader Bot workflow.\n"
                    "══════════════════════════════════════════════════════════"
                )
            return False
        log.debug(f"_ensure_adaptive_mgmt_columns check: {e}")
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

    _flt_startup = _load_filter_config()
    _intraday_tcs_startup = int(_flt_startup.get("tcs_intraday_min", MIN_TCS))
    _morning_tcs_startup  = int(_flt_startup.get("morning_tcs_min", MORNING_TCS_FLOOR))
    log.info(
        f"Watching {len(TICKERS)} tickers | feed: {FEED.upper()} | "
        f"TCS floors → morning ≥ {_morning_tcs_startup} | intraday ≥ {_intraday_tcs_startup} "
        f"(from filter_config.json; live floor = {_LIVE_MIN_TCS} hard cap)"
    )
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

    # Ensure IB context columns exist (only warns when IB_CONTEXT_ENABLED=1)
    _ensure_ib_context_columns()

    # Ensure Adaptive Position Management columns exist (only warns when enabled)
    _ensure_adaptive_mgmt_columns()

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

        # Reconcile any open Alpaca positions whose DB record was prematurely closed
        _reconcile_open_positions()

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
    _eod_cancel_done       = False
    _eod_done              = False
    _verify_done           = False
    _recalibration_done    = False
    _pdf_export_done       = False
    _div_alert_done        = False
    _pre_open_review_done  = False

    # ── Startup catch-up: recover scans missed due to mid-day restart ─────────
    # Task merges / workflow restarts can happen during market hours.
    # Run any scheduled job that should have already fired today.
    _su = datetime.now(EASTERN)
    _su_hm = _su.hour * 60 + _su.minute          # minutes since midnight ET
    _su_weekday = _su.weekday() < 5
    if _su_weekday:
        if _su_hm >= 8 * 60 + 50:
            _pre_open_review_done = True
            log.info(
                "[Catch-up] Started after 8:50 AM — adaptive position review skipped "
                "(too close to market open; positions hold existing bracket orders)"
            )
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
        if 14 * 60 <= _su_hm < 15 * 60 + 30 and not _intraday_done:
            log.info("[Catch-up] Started after 2:00 PM — running intraday scan now...")
            try:
                intraday_scan()
            except Exception as _cue:
                log.warning(f"[Catch-up] Intraday scan failed: {_cue}")
            _intraday_done = True
        if 15 * 60 + 35 <= _su_hm < 16 * 60 and not _eod_cancel_done:
            log.info(
                "[Catch-up] Started after 3:35 PM — EOD cancel not yet run; "
                "will fire on first loop to clean up any remaining open orders "
                "before market close"
            )
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
            _eod_cancel_done       = False
            _eod_done              = False
            _verify_done           = False
            _recalibration_done    = False
            _pdf_export_done       = False
            _div_alert_done        = False
            _pre_open_review_done  = False

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

            # 8:30 AM — adaptive position review (ADAPTIVE_POSITION_MGMT=1 only)
            # Runs before the regular pre-market scan so adjustments are in place
            # before the market opens.  Silently no-ops when the toggle is off.
            if (
                not _pre_open_review_done
                and now_et.weekday() < 5
                and now_et.hour == 8
                and now_et.minute >= 30
                and now_et.minute < 50
            ):
                try:
                    _pre_open_position_review()
                except Exception as _apm_e:
                    log.warning(f"[AdaptiveMgmt] Pre-open review failed (non-fatal): {_apm_e}")
                _pre_open_review_done = True

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

        # 3:35 PM — cancel any lingering open bracket/stop orders that survived
        # the force-close window.  Alpaca's order ledger must be clean before the
        # 4:00 PM close auction so that reconcile_alpaca_fills doesn't pick up
        # ghost legs as fills.  Fires on weekdays only; non-fatal on failure.
        if (
            not _eod_cancel_done
            and now_et.weekday() < 5
            and now_et.hour == 15
            and now_et.minute >= 35
        ):
            log.info(
                "[EODCancel] 3:35 PM — cancelling any remaining open orders "
                "before Alpaca EOD reconciliation..."
            )
            try:
                cancel_result = cancel_alpaca_day_orders(
                    is_paper=IS_PAPER_ALPACA,
                    api_key=ALPACA_API_KEY,
                    secret_key=ALPACA_SECRET_KEY,
                )
                cancelled = cancel_result.get("cancelled", 0) if isinstance(cancel_result, dict) else 0
                errors    = cancel_result.get("errors", 0)    if isinstance(cancel_result, dict) else 0
                if cancelled:
                    log.info(
                        f"[EODCancel] Cancelled {cancelled} open order(s) — "
                        "no orphan brackets remain before market close"
                    )
                    _cancel_date = now_et.strftime("%Y-%m-%d")
                    _cancel_msg = (
                        f"🚫 <b>EdgeIQ EOD Cancel — {_cancel_date}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚠️ <b>{cancelled} open order(s)</b> were cancelled at the "
                        f"3:35 PM EOD sweep.\n"
                        f"These were orphan bracket legs that did not fill before "
                        f"market close and have been removed from the Alpaca ledger."
                    )
                    tg_send(_cancel_msg)
                if errors:
                    log.warning(f"[EODCancel] {errors} order(s) could not be cancelled")
                if not cancelled and not errors:
                    log.info("[EODCancel] No open orders found — account is clean")
            except Exception as _ece:
                log.warning(f"[EODCancel] Order cancellation failed (non-fatal): {_ece}")
            _eod_cancel_done = True

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

        # Every 5 minutes (during market hours) — reconcile Alpaca fill prices
        # back into paper_trades so alpaca_fill_price is populated promptly after
        # an entry fills, not just at 4:20 PM EOD.
        global _LAST_RECONCILE_TS
        if LIVE_ORDERS_ENABLED and ALPACA_API_KEY and ALPACA_SECRET_KEY:
            _now_et_rc = datetime.now(EASTERN)
            _rc_hm     = _now_et_rc.hour * 60 + _now_et_rc.minute
            if 9 * 60 + 30 <= _rc_hm <= 16 * 60 + 30:
                if time.monotonic() - _LAST_RECONCILE_TS >= RECONCILE_INTERVAL:
                    _LAST_RECONCILE_TS = time.monotonic()
                    try:
                        _rc = reconcile_alpaca_fills(
                            trade_date = _now_et_rc.strftime("%Y-%m-%d"),
                            user_id    = USER_ID,
                            is_paper   = IS_PAPER_ALPACA,
                            api_key    = ALPACA_API_KEY,
                            secret_key = ALPACA_SECRET_KEY,
                        )
                        _rc_total = _rc.get("matched", 0) + _rc.get("order_id_matched", 0)
                        if _rc_total or _rc.get("errors"):
                            log.info(
                                f"[Reconcile] Intraday run — matched={_rc.get('matched',0)} "
                                f"order_id_matched={_rc.get('order_id_matched',0)} "
                                f"exit_fills={_rc.get('exit_fills',0)} "
                                f"unmatched={_rc.get('unmatched',0)} "
                                f"errors={_rc.get('errors',0)}"
                            )
                    except Exception as _rce:
                        log.warning(f"[Reconcile] Intraday reconcile error (non-fatal): {_rce}")

        # Every 5 minutes — cancel open entry orders whose IB zone has broken.
        try:
            _monitor_zone_invalidation()
        except Exception as _zwe:
            log.warning(f"[ZoneWatch] monitor error (non-fatal): {_zwe}")

        time.sleep(30)


if __name__ == "__main__":
    main()
