"""
EdgeIQ Kalshi Prediction Market Bot
=====================================
Runs as a standalone scheduled process. Reads the Stockbee macro breadth regime
already saved by the user in EdgeIQ, maps it to open Kalshi prediction markets,
and paper-trades high-confidence opportunities.

Philosophy
----------
The founder documented 95%+ accuracy calling a regime shift 20–30 days ahead
(March 25 → April 10, 2026 — called S&P 500 bottom within 12 points). Kalshi
markets trade on exactly these macro events: S&P level outcomes, Fed decisions,
economic data releases. This bot operationalises that prediction framework.

Mode: PAPER TRADING ONLY by default. Set KALSHI_LIVE=true only after a
verified 30-day paper record. No live capital is deployed by this file.

Schedule (ET):
  9:30 AM  — fetch open Kalshi markets + run signal mapping against today's regime
  10:00 AM — log top opportunities + send Telegram alerts
   4:30 PM — check settled markets + update outcomes + send P&L summary

Required environment secrets:
  KALSHI_EMAIL        — Kalshi account email (for API auth)
  KALSHI_PASSWORD     — Kalshi account password
  TELEGRAM_BOT_TOKEN  — from @BotFather
  TELEGRAM_CHAT_ID    — your chat ID from @userinfobot
  SUPABASE_URL        — already set for EdgeIQ
  SUPABASE_KEY        — already set for EdgeIQ

Optional env vars:
  KALSHI_LIVE           — 'true' to use live API (default: demo/paper)
  KALSHI_USER_ID        — EdgeIQ user ID (defaults to paper_trader_bot USER_ID)
  KALSHI_PAPER_ACCOUNT  — virtual account size in USD (default: 10000)
  KALSHI_KELLY_FRACTION — Kelly multiplier 0–1 (default: 0.25, conservative)
  KALSHI_MIN_CONFIDENCE — min confidence to take a position (default: 0.60)
  KALSHI_MAX_MARKETS    — max markets to enter per day (default: 5)
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
log = logging.getLogger("kalshi_bot")

EASTERN = pytz.timezone("America/New_York")

# ── Config from environment ───────────────────────────────────────────────────
KALSHI_EMAIL    = os.getenv("KALSHI_EMAIL", "").strip()
KALSHI_PASSWORD = os.getenv("KALSHI_PASSWORD", "").strip()
KALSHI_LIVE     = os.getenv("KALSHI_LIVE", "false").lower() == "true"
USER_ID         = os.getenv("KALSHI_USER_ID",
                             os.getenv("PAPER_TRADE_USER_ID",
                                       "a5e1fcab-8369-42c4-8550-a8a19734510c"))

PAPER_ACCOUNT_CENTS = int(float(os.getenv("KALSHI_PAPER_ACCOUNT", "10000")) * 100)
KELLY_FRACTION      = float(os.getenv("KALSHI_KELLY_FRACTION", "0.25"))
MIN_CONFIDENCE      = float(os.getenv("KALSHI_MIN_CONFIDENCE", "0.60"))
MAX_MARKETS_PER_DAY = int(os.getenv("KALSHI_MAX_MARKETS", "5"))

TG_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

_MODE_STR = "LIVE ⚡" if KALSHI_LIVE else "PAPER 📄"

# ── Import backend functions ──────────────────────────────────────────────────
try:
    from backend import (
        get_breadth_regime,
        fetch_kalshi_markets,
        fetch_kalshi_market_by_ticker,
        kalshi_login,
        map_regime_to_kalshi,
        kalshi_kelly_size,
        log_kalshi_prediction,
        update_kalshi_outcomes,
        get_kalshi_predictions,
        get_kalshi_performance_summary,
        ensure_kalshi_tables,
    )
except ImportError as e:
    log.error(f"Cannot import backend: {e}")
    raise


# ── Telegram helpers ──────────────────────────────────────────────────────────
def tg_send(message: str) -> bool:
    if not TG_TOKEN or not TG_CHAT_ID:
        return False
    try:
        import requests as _req
        resp = _req.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as exc:
        log.warning(f"Telegram send error: {exc}")
        return False


# ── Kalshi auth token (cached per session) ────────────────────────────────────
_kalshi_token: str = ""


def _get_token() -> str:
    global _kalshi_token
    if not _kalshi_token and KALSHI_EMAIL and KALSHI_PASSWORD:
        log.info("Authenticating with Kalshi API...")
        _kalshi_token = kalshi_login(KALSHI_EMAIL, KALSHI_PASSWORD, live=KALSHI_LIVE)
        if _kalshi_token:
            log.info("Kalshi auth: OK")
        else:
            log.warning(
                "Kalshi auth failed — running in token-free mode. "
                "Public markets still accessible."
            )
    return _kalshi_token


# ── Alert formatters ──────────────────────────────────────────────────────────
def _alert_opportunities(opps: list, regime: dict, trade_date: date) -> None:
    """Send Telegram alert for top Kalshi opportunities.

    Each signal includes the concrete breadth metrics that triggered it so
    every prediction is fully auditable from the Telegram message alone.
    """
    if not opps:
        return
    regime_label = regime.get("label", "Unknown")

    # Use breadth_evidence from the first opp (all share the same regime snapshot)
    breadth_ev = opps[0].get("breadth_evidence", "") if opps else ""

    lines = [
        f"🎯 <b>Kalshi Signals — {trade_date}</b> [{_MODE_STR}]",
        f"🌡️ Regime: {regime_label}",
        f"📊 Breadth: {breadth_ev}" if breadth_ev else "",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"Top {len(opps)} signal(s) from macro breadth framework:",
        "",
    ]
    lines = [l for l in lines if l]  # remove blank lines from missing fields

    for i, opp in enumerate(opps, 1):
        title     = opp.get("title", "")[:60]
        side      = opp.get("predicted_side", "?")
        price     = opp.get("price_of_our_side", 50)
        conf      = opp.get("confidence", 0)
        edge      = opp.get("edge_score", 0)
        contracts = opp.get("_contracts", 1)
        cost      = opp.get("_cost_cents", price)
        max_win   = opp.get("_max_win_cents", 0)
        pct_gain  = round(max_win / max(cost, 1) * 100, 0) if max_win else 0
        side_emoji = "✅" if side == "YES" else "❌"

        # Per-position breadth trigger summary for auditability
        four_pct = opp.get("four_pct_count", "?")
        ratio    = opp.get("ratio_13_34", "?")
        q_r      = opp.get("q_ratio", "?")
        trigger_line = (
            f"   📐 Triggers: 4%={four_pct} · A/D={ratio}x · Q-ratio={q_r}x\n"
        ) if four_pct != "?" else ""

        lines.append(
            f"<b>{i}. {opp.get('ticker', '?')}</b> — {side_emoji} <b>{side}</b>\n"
            f"   📝 {title}\n"
            f"   💰 Price: {price}¢ · Max gain: +{pct_gain:.0f}% "
            f"({contracts} contracts · ${cost/100:.2f} cost)\n"
            f"   🧠 Confidence: {conf:.0%} · Edge: +{edge:.2%}\n"
            + trigger_line
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("All positions are PAPER trades — no live capital deployed.")
    tg_send("\n".join(lines))
    log.info(f"Telegram alert sent: {len(opps)} Kalshi opportunities")


def _alert_no_signal(regime: dict, trade_date: date) -> None:
    regime_label = regime.get("label", "Unknown")
    if regime.get("regime_tag", "unknown") == "unknown":
        tg_send(
            f"📊 <b>Kalshi Scan — {trade_date}</b>\n"
            f"⬜ No breadth regime data available for today.\n"
            f"Enter today's Stockbee numbers in the EdgeIQ sidebar to activate signals."
        )
    else:
        tg_send(
            f"📊 <b>Kalshi Scan — {trade_date}</b>\n"
            f"🌡️ Regime: {regime_label}\n"
            f"No high-confidence Kalshi opportunities found today "
            f"(min confidence: {MIN_CONFIDENCE:.0%}).\n"
            f"Watching for intraday signal changes..."
        )


def _alert_eod_summary(summary: dict, updated: int, trade_date: date) -> None:
    total   = summary.get("total", 0)
    won     = summary.get("won", 0)
    lost    = summary.get("lost", 0)
    pending = summary.get("pending", 0)
    wr      = summary.get("win_rate", 0.0)
    pnl     = summary.get("total_pnl_cents", 0)
    pnl_str = f"+${pnl/100:.2f}" if pnl >= 0 else f"-${abs(pnl)/100:.2f}"
    tg_send(
        f"📈 <b>Kalshi EOD — {trade_date}</b> [{_MODE_STR}]\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Won: {won}   ❌ Lost: {lost}   ⏳ Pending: {pending}\n"
        f"📊 Win rate (all time): {wr:.1f}%\n"
        f"💰 All-time paper P&L: {pnl_str}\n"
        f"📋 Resolved today: {updated} market(s)"
    )


# ── Live order placement (gated — activates only when KALSHI_LIVE=true) ──────

# Minimum verified paper track record required before live trading activates
_LIVE_MIN_DAYS     = int(os.getenv("KALSHI_LIVE_MIN_DAYS",     "30"))   # calendar days in paper mode
_LIVE_MIN_TRADES   = int(os.getenv("KALSHI_LIVE_MIN_TRADES",   "30"))   # settled predictions
_LIVE_MIN_WIN_RATE = float(os.getenv("KALSHI_LIVE_MIN_WIN_RATE", "60.0")) # paper win rate %


def _maybe_place_live_orders(opportunities: list, token: str) -> None:
    """Gate live order placement behind verified paper track-record criteria.

    Only places live orders when ALL THREE gates pass:
      Gate 1 (time)    — bot has been running in paper mode for >= KALSHI_LIVE_MIN_DAYS
                         calendar days since the first prediction was logged.
                         Default: 30 days. Override via KALSHI_LIVE_MIN_DAYS env var.
      Gate 2 (volume)  — >= KALSHI_LIVE_MIN_TRADES settled predictions logged.
                         Default: 30 trades. Override via KALSHI_LIVE_MIN_TRADES.
      Gate 3 (quality) — paper win rate >= KALSHI_LIVE_MIN_WIN_RATE %.
                         Default: 60%. Override via KALSHI_LIVE_MIN_WIN_RATE.

    The time gate is intentional and non-bypassable via normal operation: even a
    lucky first-day win streak cannot unlock live capital. The bot must have proven
    itself across at least `KALSHI_LIVE_MIN_DAYS` calendar days of real market
    conditions before a single live order is placed.

    Order placement via POST /portfolio/orders — resting limit order at
    the current YES/NO ask price so we don't cross the spread.
    """
    if not KALSHI_LIVE:
        return

    # ── Load performance summary (includes paper_days_elapsed) ───────────────
    perf = get_kalshi_performance_summary(user_id=USER_ID)
    settled           = perf.get("won", 0) + perf.get("lost", 0)
    win_rate          = perf.get("win_rate", 0.0)
    paper_days        = perf.get("paper_days_elapsed", 0)
    first_trade_date  = perf.get("first_trade_date", "N/A")

    # ── Gate 1: Minimum paper duration (time-based, primary gate) ────────────
    if paper_days < _LIVE_MIN_DAYS:
        days_remaining = _LIVE_MIN_DAYS - paper_days
        log.info(
            f"Live trading BLOCKED — paper period insufficient: "
            f"{paper_days}/{_LIVE_MIN_DAYS} calendar days elapsed "
            f"(first trade: {first_trade_date}, {days_remaining} days to go). "
            f"Running in observation mode."
        )
        tg_send(
            f"⚠️ <b>Kalshi Live Mode: Observation Only</b>\n"
            f"Paper period: {paper_days} days elapsed (need {_LIVE_MIN_DAYS}).\n"
            f"First trade logged: {first_trade_date}.\n"
            f"{days_remaining} more day(s) before live trading can unlock.\n"
            f"Bot is logging predictions but NOT placing live orders yet."
        )
        return

    # ── Gate 2: Minimum settled trade count ──────────────────────────────────
    if settled < _LIVE_MIN_TRADES:
        log.info(
            f"Live trading BLOCKED — insufficient settled trades: "
            f"{settled}/{_LIVE_MIN_TRADES} required. "
            f"Running in observation mode."
        )
        tg_send(
            f"⚠️ <b>Kalshi Live Mode: Observation Only</b>\n"
            f"Settled trades: {settled} (need {_LIVE_MIN_TRADES}).\n"
            f"Win rate: {win_rate:.1f}% · Paper days: {paper_days}.\n"
            f"Not placing live orders yet."
        )
        return

    # ── Gate 3: Minimum win rate ──────────────────────────────────────────────
    if win_rate < _LIVE_MIN_WIN_RATE:
        log.info(
            f"Live trading BLOCKED — win rate below threshold: "
            f"{win_rate:.1f}% < {_LIVE_MIN_WIN_RATE:.0f}% required. "
            f"Running in observation mode."
        )
        tg_send(
            f"⚠️ <b>Kalshi Live Mode: Observation Only</b>\n"
            f"Win rate: {win_rate:.1f}% (need {_LIVE_MIN_WIN_RATE:.0f}%).\n"
            f"Settled: {settled} trades · Paper days: {paper_days}.\n"
            f"Not placing live orders yet."
        )
        return

    # ── All gates passed — place live orders ─────────────────────────────────
    log.info(
        f"Live trading ACTIVE — all gates passed: "
        f"{paper_days}d paper / {settled} settled / {win_rate:.1f}% win rate"
    )
    placed = 0
    for opp in opportunities:
        contracts = opp.get("_contracts", 0)
        if contracts <= 0:
            continue
        try:
            import requests as _req
            from backend import _kalshi_base
            side = opp["predicted_side"].lower()  # "yes" or "no"
            ticker = opp["ticker"]
            price  = opp["price_of_our_side"]
            payload = {
                "ticker":    ticker,
                "client_order_id": f"edgeiq_{ticker}_{date.today().isoformat()}",
                "type":      "limit",
                "action":    "buy",
                "side":      side,
                "count":     contracts,
                "yes_price": price if side == "yes" else (100 - price),
                "no_price":  price if side == "no"  else (100 - price),
            }
            resp = _req.post(
                f"{_kalshi_base(live=True)}/portfolio/orders",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if resp.status_code in (200, 201):
                placed += 1
                log.info(
                    f"  LIVE ORDER PLACED: {ticker} {side.upper()} "
                    f"{contracts}x @ {price}¢"
                )
            else:
                log.warning(
                    f"  Live order failed for {ticker}: "
                    f"{resp.status_code} {resp.text[:100]}"
                )
        except Exception as exc:
            log.error(f"  Live order exception for {opp.get('ticker','?')}: {exc}")

    if placed:
        tg_send(
            f"⚡ <b>Kalshi Live Orders Placed — {date.today()}</b>\n"
            f"Placed {placed}/{len(opportunities)} live order(s).\n"
            f"Track record: {settled} trades / {win_rate:.1f}% win rate."
        )
    else:
        log.info("No live orders placed this morning.")


# ── Scheduled jobs ────────────────────────────────────────────────────────────
def morning_signal_scan(trade_date: date) -> None:
    """9:30 AM — fetch markets, map signals, send alerts, log paper positions."""
    log.info("=" * 60)
    log.info(f"KALSHI MORNING SCAN — {trade_date} [{_MODE_STR}]")
    log.info("=" * 60)

    # ── Load today's breadth regime ──────────────────────────────────────────
    regime = {}
    try:
        regime = get_breadth_regime(user_id=USER_ID) or {}
        log.info(f"Regime: {regime.get('label', 'unknown')} ({regime.get('regime_tag', '?')})")
    except Exception as exc:
        log.warning(f"Could not load breadth regime: {exc}")

    if not regime or regime.get("regime_tag", "unknown") == "unknown":
        log.warning("No breadth regime data. Skipping signal scan.")
        _alert_no_signal(regime, trade_date)
        return

    # ── Fetch open Kalshi macro markets ─────────────────────────────────────
    token = _get_token()
    try:
        markets = fetch_kalshi_markets(token=token, live=KALSHI_LIVE, limit=200)
        log.info(f"Fetched {len(markets)} macro-relevant open Kalshi markets")
    except Exception as exc:
        log.error(f"Failed to fetch Kalshi markets: {exc}")
        tg_send(
            f"⚠️ <b>Kalshi Bot Error — {trade_date}</b>\n"
            f"Could not fetch markets: {exc}"
        )
        return

    if not markets:
        log.warning("No macro-relevant open Kalshi markets found.")
        _alert_no_signal(regime, trade_date)
        return

    # ── Map regime signals → market opportunities ─────────────────────────
    try:
        opportunities = map_regime_to_kalshi(regime, markets)
        log.info(f"Signal mapping: {len(opportunities)} opportunities above confidence floor")
    except Exception as exc:
        log.error(f"Signal mapping failed: {exc}")
        return

    high_conf = [o for o in opportunities if o["confidence"] >= MIN_CONFIDENCE]
    high_conf = high_conf[:MAX_MARKETS_PER_DAY]
    log.info(
        f"{len(high_conf)} opportunities meet min confidence {MIN_CONFIDENCE:.0%} "
        f"(cap: {MAX_MARKETS_PER_DAY}/day)"
    )

    if not high_conf:
        _alert_no_signal(regime, trade_date)
        return

    # ── Compute Kelly sizes + log to Supabase ────────────────────────────────
    logged = 0
    skipped_zero_kelly = 0
    executed_opps = []   # only entries with contracts > 0 that were actually logged
    for opp in high_conf:
        sizing = kalshi_kelly_size(
            confidence=opp["confidence"],
            price_cents=int(opp["price_of_our_side"]),
            account_value_cents=PAPER_ACCOUNT_CENTS,
            kelly_fraction=KELLY_FRACTION,
        )
        # Skip if Kelly sizing says no edge (contracts = 0)
        if sizing["contracts"] <= 0:
            skipped_zero_kelly += 1
            log.info(
                f"  {opp['ticker']:30s} | SKIP (Kelly=0) — "
                f"conf={opp['confidence']:.2f} @ {opp['price_of_our_side']}¢ "
                f"has no positive edge after fractional Kelly"
            )
            continue

        opp["_contracts"]    = sizing["contracts"]
        opp["_cost_cents"]   = sizing["cost_cents"]
        opp["_max_win_cents"] = sizing["max_win_cents"]
        log.info(
            f"  {opp['ticker']:30s} | {opp['predicted_side']:3s} "
            f"@ {opp['price_of_our_side']:2d}¢ | "
            f"conf={opp['confidence']:.2f} | edge={opp['edge_score']:+.2f} | "
            f"{sizing['contracts']}x contracts (${sizing['cost_cents']/100:.2f} cost)"
        )
        result = log_kalshi_prediction(
            trade_date=trade_date,
            market=opp,
            regime=regime,
            sizing=sizing,
            user_id=USER_ID,
        )
        if result.get("saved"):
            logged += 1
            executed_opps.append(opp)   # track what was actually logged
        elif result.get("error"):
            log.warning(f"  Failed to log {opp['ticker']}: {result['error']}")

    log.info(
        f"Logged {logged}/{len(high_conf)} positions to Supabase "
        f"({skipped_zero_kelly} skipped: zero Kelly)"
    )

    # ── Live order placement (gated — PAPER MODE ONLY until track record met) ─
    if KALSHI_LIVE:
        _maybe_place_live_orders(executed_opps, token)

    # ── Telegram alert — only show positions that were actually logged ─────────
    if executed_opps:
        _alert_opportunities(executed_opps, regime, trade_date)
    elif skipped_zero_kelly > 0:
        # All candidates had zero Kelly — send informational message
        tg_send(
            f"📊 <b>Kalshi Scan — {trade_date}</b> [{_MODE_STR}]\n"
            f"🌡️ Regime: {regime.get('label', 'Unknown')}\n"
            f"{len(high_conf)} candidate(s) found but ALL were skipped — "
            f"fractional Kelly returned 0 contracts (no positive edge at current prices)."
        )
    else:
        _alert_no_signal(regime, trade_date)


def eod_outcome_update(trade_date: date) -> None:
    """4:30 PM — check settled Kalshi markets, update outcomes, send summary."""
    log.info("=" * 60)
    log.info(f"KALSHI EOD OUTCOME UPDATE — {trade_date}")
    log.info("=" * 60)

    token = _get_token()
    try:
        upd = update_kalshi_outcomes(
            trade_date=trade_date,
            token=token,
            user_id=USER_ID,
            live=KALSHI_LIVE,
        )
        updated = upd.get("updated", 0)
        total   = upd.get("total", 0)
        log.info(f"Outcomes updated: {updated}/{total} positions resolved")
    except Exception as exc:
        log.error(f"EOD outcome update failed: {exc}")
        tg_send(f"⚠️ <b>Kalshi EOD Error — {trade_date}</b>\n{exc}")
        return

    try:
        summary = get_kalshi_performance_summary(user_id=USER_ID)
        log.info(
            f"Performance summary — "
            f"total: {summary['total']} | "
            f"won: {summary['won']} | lost: {summary['lost']} | "
            f"pending: {summary['pending']} | "
            f"win rate: {summary['win_rate']}% | "
            f"P&L: ${summary['total_pnl_cents']/100:.2f}"
        )
        _alert_eod_summary(summary, updated, trade_date)
    except Exception as exc:
        log.warning(f"Performance summary failed: {exc}")


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info(f"EdgeIQ Kalshi Prediction Market Bot — {_MODE_STR}")
    log.info("=" * 60)

    if not KALSHI_EMAIL or not KALSHI_PASSWORD:
        log.warning(
            "KALSHI_EMAIL / KALSHI_PASSWORD not set. "
            "Bot will run without authentication (public markets only, no trade placement). "
            "Add secrets KALSHI_EMAIL and KALSHI_PASSWORD to enable full API access."
        )
    else:
        log.info(f"Kalshi account: {KALSHI_EMAIL} ({'LIVE' if KALSHI_LIVE else 'DEMO/PAPER'})")

    log.info(f"EdgeIQ user ID: {USER_ID}")
    log.info(f"Paper account:  ${PAPER_ACCOUNT_CENTS/100:,.0f}")
    log.info(f"Kelly fraction: {KELLY_FRACTION:.0%} (fractional Kelly)")
    log.info(f"Min confidence: {MIN_CONFIDENCE:.0%}")
    log.info(f"Max markets/day: {MAX_MARKETS_PER_DAY}")

    # ── One-time setup ───────────────────────────────────────────────────────
    table_ok = ensure_kalshi_tables()
    if not table_ok:
        log.error(
            "\n"
            "══════════════════════════════════════════════════════════\n"
            "  kalshi_predictions table is MISSING in Supabase.\n"
            "  Run the SQL shown above in your Supabase SQL Editor,\n"
            "  then restart the Kalshi Bot workflow.\n"
            "══════════════════════════════════════════════════════════"
        )
        # Don't return — bot can still run, logging will fail gracefully.

    if TG_TOKEN and TG_CHAT_ID:
        log.info("Telegram: ENABLED")
    else:
        log.warning("Telegram: DISABLED (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set)")

    _scan_done    = False
    _eod_done     = False

    while True:
        now_et = datetime.now(EASTERN)
        today  = now_et.date()

        # Reset flags at midnight
        if now_et.hour == 0 and now_et.minute == 0:
            _scan_done = False
            _eod_done  = False
            log.info("New trading day — flags reset")

        # Skip weekends
        if now_et.weekday() >= 5:
            time.sleep(60)
            continue

        # ── 9:30 AM — morning signal scan ────────────────────────────────────
        if (
            not _scan_done
            and now_et.hour == 9
            and now_et.minute >= 30
        ):
            morning_signal_scan(today)
            _scan_done = True

        # ── 4:30 PM — EOD outcome check ───────────────────────────────────────
        if (
            not _eod_done
            and now_et.hour == 16
            and now_et.minute >= 30
        ):
            eod_outcome_update(today)
            _eod_done = True

        time.sleep(30)


if __name__ == "__main__":
    main()
