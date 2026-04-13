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
  PAPER_TRADE_USER_ID   — EdgeIQ user ID (defaults below)
  PAPER_TRADE_MIN_TCS   — minimum TCS threshold (default: 50)
  PAPER_TRADE_FEED      — sip or iex (default: sip)
  PAPER_TRADE_PRICE_MIN — min price filter (default: 1.0)
  PAPER_TRADE_PRICE_MAX — max price filter (default: 20.0)
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

EASTERN = pytz.timezone("America/New_York")

# ── Config from environment ───────────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
USER_ID           = os.getenv("PAPER_TRADE_USER_ID", "a5e1fcab-8369-42c4-8550-a8a19734510c")
MIN_TCS           = int(os.getenv("PAPER_TRADE_MIN_TCS", "50"))
FEED              = os.getenv("PAPER_TRADE_FEED", "sip")
PRICE_MIN         = float(os.getenv("PAPER_TRADE_PRICE_MIN", "1.0"))
PRICE_MAX         = float(os.getenv("PAPER_TRADE_PRICE_MAX", "20.0"))

TG_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

_DEFAULT_TICKERS = (
    "SATL,UGRO,ANNA,VCX,CODX,ARTL,SWMR,FEED,RBNE,PAVS,LNKS,BIAF,ACXP,GOAI"
)

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
        recalibrate_from_supabase,
        verify_watchlist_predictions,
        verify_ticker_rankings,
        ensure_ticker_rankings_table,
        ensure_telegram_columns,
        save_telegram_trade,
        save_beta_chat_id,
        get_beta_chat_ids,
        get_breadth_regime,
    )
except ImportError as e:
    log.error(f"Cannot import backend: {e}")
    raise


# ── Telegram helpers ──────────────────────────────────────────────────────────
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

                if not text.startswith("/log") and not text.startswith("/start"):
                    continue

                # ── /start USER_ID — beta tester connection via deep link ──
                if text.startswith("/start"):
                    parts = text.split(maxsplit=1)
                    payload = parts[1].strip() if len(parts) > 1 else ""
                    if payload:
                        # Save chat_id linked to the user_id from the deep link.
                        # Portal links are personally distributed by the owner, so
                        # possession of a valid link is treated as authorization.
                        _saved = False
                        try:
                            _saved = save_beta_chat_id(payload, chat_id)
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


def _structure_emoji(predicted: str) -> str:
    p = (predicted or "").lower()
    if "trend" in p and ("up" in p or "bull" in p):
        return "🟢"
    if "trend" in p and ("down" in p or "bear" in p):
        return "🔴"
    if "double" in p:
        return "🟡"
    if "neutral" in p or "ntrl" in p:
        return "🔵"
    if "normal" in p or "nrml" in p:
        return "⚪"
    return "⚫"


def _alert_setup(r: dict, trade_date: date):
    """Send a Telegram alert for a single qualifying setup."""
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

    # Entry logic hint based on structure
    p_lower = predicted.lower()
    if "trend" in p_lower and ("up" in p_lower or "bull" in p_lower):
        entry_hint = f"🎯 <b>LONG</b> above IB high ${above_ib:.2f} | Target: IB extension"
    elif "trend" in p_lower and ("down" in p_lower or "bear" in p_lower):
        entry_hint = f"🎯 <b>SHORT</b> below IB low ${below_ib:.2f} | Target: IB extension"
    elif "double" in p_lower:
        entry_hint = f"🎯 Watch <b>both sides</b> — double distribution. Fade false breaks."
    elif "ntrl extreme" in p_lower or "ntrl_extreme" in p_lower:
        entry_hint = f"🎯 <b>Mean revert</b> to IB mid ${ib_mid:.2f} | Fade extremes"
    elif "neutral" in p_lower:
        entry_hint = f"🎯 <b>Range trade</b> — IB ${ib_low:.2f}–${ib_high:.2f} | Fade both ends"
    else:
        entry_hint = f"🎯 Watch IB range ${ib_low:.2f}–${ib_high:.2f} for directional break"

    msg = (
        f"{emoji} <b>EdgeIQ Setup — {ticker}</b>\n"
        f"⏰ {scan_time}  ·  📅 {trade_date}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price at IB close: <b>${cur_px:.2f}</b>  "
        f"({chg_arrow}{abs(chg_pct):.1f}% from open ${open_px:.2f})\n"
        f"📊 Structure: <b>{predicted}</b>  ({conf:.0f}% conf)\n"
        f"⚡ TCS Score: <b>{tcs:.0f} / 100</b>\n"
        f"📦 IB Range:  ${ib_low:.2f} – ${ib_high:.2f}  (mid ${ib_mid:.2f})\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{entry_hint}\n"
        f"🔑 Key levels:\n"
        f"  Break above → ${above_ib:.2f}\n"
        f"  IB Mid      → ${ib_mid:.2f}\n"
        f"  Break below → ${below_ib:.2f}"
    )
    sent = tg_send(msg)
    if sent:
        log.info(f"  📱 Telegram alert sent: {ticker}")
    return sent


def _alert_morning_summary(
    qualified: list, total_scanned: int, trade_date: date, effective_tcs: int = None
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

    if not qualified:
        tg_send(
            f"🔍 <b>EdgeIQ Morning Scan — {trade_date}</b>\n"
            f"No setups met TCS ≥ {tcs_threshold} today out of {total_scanned} scanned.\n"
            f"Watching for intraday opportunities..."
            + _regime_line
        )
        return
    tg_send(
        f"🔔 <b>EdgeIQ Morning Scan — {trade_date}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <b>{len(qualified)} setup(s)</b> qualified (TCS ≥ {tcs_threshold})\n"
        f"📋 Scanned {total_scanned} tickers from your Finviz watchlist"
        + _regime_line
        + "\nSending individual alerts now..."
    )


def _alert_eod_summary(results: list, updated: int, trade_date: date):
    """Send EOD outcome summary."""
    wins   = [r for r in results if r.get("win_loss") == "Win"]
    losses = [r for r in results if r.get("win_loss") == "Loss"]
    best   = max(results, key=lambda r: float(r.get("aft_move_pct", 0)), default=None)

    lines = [
        f"📈 <b>EdgeIQ EOD Summary — {trade_date}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"✅ Wins: {len(wins)}   ❌ Losses: {len(losses)}   📋 Updated: {updated}",
    ]
    if best and best.get("aft_move_pct"):
        lines.append(
            f"🏆 Best mover: <b>{best['ticker']}</b> "
            f"{float(best['aft_move_pct']):+.1f}% ({best.get('win_loss','?')})"
        )
    if wins or losses:
        wr = round(100 * len(wins) / max(1, len(wins) + len(losses)), 1)
        lines.append(f"📊 Today's win rate: <b>{wr}%</b>")
    tg_send("\n".join(lines))


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
def watchlist_refresh():
    """9:15 AM ET — pull today's movers from Finviz, save to Supabase."""
    global TICKERS
    log.info("=" * 60)
    log.info("WATCHLIST REFRESH — fetching from Finviz")
    log.info("=" * 60)
    try:
        tickers = fetch_finviz_watchlist(
            change_min_pct=3.0,
            float_max_m=100.0,
            price_min=PRICE_MIN,
            price_max=PRICE_MAX,
            max_tickers=100,
        )
        if tickers:
            saved = save_watchlist(tickers, user_id=USER_ID)
            if saved:
                TICKERS = tickers
                log.info(f"Watchlist updated: {len(tickers)} tickers → {', '.join(tickers)}")
                tg_send(
                    f"📋 <b>Watchlist Refreshed — {date.today()}</b>\n"
                    f"Fetched <b>{len(tickers)} tickers</b> from Finviz "
                    f"(% Change ≥3% · Float ≤100M · Vol ≥1M · US)\n"
                    f"Morning scan at 10:47 AM ET..."
                )
            else:
                log.warning("Finviz returned tickers but Supabase save failed — keeping existing watchlist")
        else:
            log.warning("Finviz returned 0 tickers — keeping existing watchlist")
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
    """Send a clean morning setup list to all beta subscribers."""
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
    _broadcast_to_subscribers("\n".join(lines))


def _broadcast_eod_to_subscribers(results: list, today) -> None:
    """Send a clean EOD outcome summary to all beta subscribers."""
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
    _broadcast_to_subscribers("\n".join(lines))


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

    # Tag every result with the current macro regime
    regime_tag = regime.get("regime_tag", "")
    for r in results:
        r["regime_tag"] = regime_tag
        r["sim_date"]   = str(today)  # ensure sim_date present for dedup

    # Log ALL scan results to paper_trades with regime_tag attached.
    # min_tcs_filter records the effective threshold for this session;
    # analytics can use it to distinguish qualified vs below-threshold rows.
    all_results_logged = log_paper_trades(results, user_id=USER_ID, min_tcs=effective_min_tcs)
    log.info(
        f"Session logged: {all_results_logged.get('saved', 0)} new | "
        f"{all_results_logged.get('skipped', 0)} skipped | regime: {regime_tag or 'none'}"
    )

    qualified = [
        r for r in results
        if float(r.get("tcs", 0)) >= effective_min_tcs
    ]
    log.info(
        f"{len(qualified)} setups qualified TCS ≥ {effective_min_tcs} "
        f"(of {len(results)} scanned)"
    )

    # Telegram: summary header
    _alert_morning_summary(qualified, len(results), today, effective_tcs=effective_min_tcs)

    if qualified:
        log.info(f"Sending {len(qualified)} Telegram setup alerts...")
        # Telegram: one alert per setup
        for r in qualified:
            log.info(
                f"  {r['ticker']:6s} | TCS {r.get('tcs', 0):5.0f} | "
                f"predicted: {r.get('predicted', '—'):20s} | "
                f"IB {r.get('ib_low', 0):.2f}–{r.get('ib_high', 0):.2f}"
            )
            _alert_setup(r, today)
            time.sleep(0.3)  # Telegram rate limit buffer
    else:
        log.info("No setups met TCS threshold today.")

    # ── Beta subscriber broadcast (clean — no TCS/brain language) ─────────
    _broadcast_morning_to_subscribers(results, today)


def intraday_scan():
    """2:00 PM ET — re-scan for fresh setups that developed through midday."""
    today = date.today()
    log.info("=" * 60)
    log.info("INTRADAY SCAN — checking for midday setups")
    log.info("=" * 60)

    results = _run_scan(today, cutoff_h=13, cutoff_m=30)
    if not results:
        log.info("No intraday results.")
        return

    qualified = [r for r in results if float(r.get("tcs", 0)) >= MIN_TCS]
    log.info(f"{len(qualified)} intraday setups at TCS ≥ {MIN_TCS} (of {len(results)} scanned)")

    if qualified:
        tg_send(
            f"🔄 <b>Intraday Scan — {today} (2 PM)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>{len(qualified)} setup(s)</b> still active/developing:"
        )
        for r in qualified:
            _alert_setup(r, today)
            time.sleep(0.3)
    else:
        log.info("No intraday setups above threshold.")


def eod_update():
    """4:20 PM ET — update paper trades with full-day outcomes + send EOD summary."""
    today = date.today()
    log.info("=" * 60)
    log.info("EOD UPDATE — resolving outcomes with full-day bar data")
    log.info("=" * 60)

    results = _run_scan(today, cutoff_h=15, cutoff_m=55)
    if not results:
        log.warning("No results from EOD scan — cannot update outcomes.")
        tg_send(f"⚠️ <b>EOD Update Failed</b> — {today}\nNo bar data returned.")
        return

    upd = update_paper_trade_outcomes(str(today), results, user_id=USER_ID)
    updated_count = upd.get("updated", 0)
    log.info(f"Updated {updated_count} paper trade outcome(s) for {today}")

    for r in results:
        log.info(
            f"  {r['ticker']:6s} | {r.get('win_loss', '?'):4s} | "
            f"actual: {r.get('actual_outcome', '—'):18s} | "
            f"FT {r.get('aft_move_pct', 0):+.1f}%"
        )

    # Telegram EOD summary
    qualified_results = [r for r in results if float(r.get("tcs", 0)) >= MIN_TCS]
    _alert_eod_summary(qualified_results, updated_count, today)

    # ── Beta subscriber broadcast (clean — no TCS/brain language) ─────────
    _broadcast_eod_to_subscribers(results, today)


def _send_rankings_summary(rows: list, rating_date) -> None:
    """Send a Telegram message summarising nightly ranking performance by rank tier."""
    if not rows:
        return
    from collections import defaultdict
    tier_rows = defaultdict(list)
    for r in rows:
        tier_rows[r["rank"]].append(r)

    # Rank 5/4/3 = bullish (win = positive chg)
    # Rank 2/1   = bearish/fade (win = negative chg)
    # Rank 0     = watch only (no directional call)
    def _is_win(rank, chg):
        if rank >= 3:
            return chg > 0
        elif rank in (1, 2):
            return chg < 0
        return None  # rank 0 = no call

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
        label   = "★" * min(tier, 5) if tier > 0 else "⬜ Watch"
        bias    = " (bearish)" if bearish else (" (neutral)" if tier == 3 else "")
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
        return True
    except Exception as _exc:
        log.error(f"update_daily_build_notes: write failed: {_exc}")
        return False


def nightly_recalibration():
    """4:30 PM ET — read all Supabase outcome data, update brain weights."""
    log.info("=" * 60)
    log.info("NIGHTLY RECALIBRATION — updating brain weights from live data")
    log.info("=" * 60)
    try:
        cal = recalibrate_from_supabase(user_id=USER_ID)
        src = cal.get("sources", {})
        log.info(
            f"Data sources — accuracy_tracker: {src.get('accuracy_tracker', 0)} rows | "
            f"paper_trades: {src.get('paper_trades', 0)} rows | "
            f"total: {src.get('total', 0)}"
        )
        if not cal.get("calibrated"):
            log.info("Not enough data yet (need ≥5 samples per structure). Weights unchanged.")
            _alert_recalibration(cal)
            return
        deltas = cal.get("deltas", [])
        log.info(f"Brain weights updated — {len(deltas)} structure(s) adjusted:")
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
        log.error(f"Nightly recalibration failed: {exc}")
        tg_send(f"⚠️ <b>Recalibration Error</b>\n{exc}")


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    log.info("EdgeIQ Paper Trader Bot starting up...")

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
    log.info(f"User: {USER_ID}")
    log.info(
        "Schedule: 9:15 AM ET → watchlist refresh | 10:47 AM ET → morning scan | "
        "11:45 AM ET → midday watchlist refresh | 2:00 PM ET → intraday scan | "
        "4:20 PM ET → EOD update | 4:25 PM ET → auto-verify | 4:30 PM ET → recalibration"
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

    # Ensure trade_journal has Telegram-logging columns
    ensure_telegram_columns()

    # Start Telegram listener in background daemon thread
    import threading as _threading
    _tg_thread = _threading.Thread(target=telegram_listener, daemon=True, name="TelegramListener")
    _tg_thread.start()
    log.info("Telegram listener thread started — send /log commands to the bot to log trades")

    _watchlist_done        = False
    _midday_watchlist_done = False
    _morning_done          = False
    _intraday_done         = False
    _eod_done              = False
    _verify_done           = False
    _recalibration_done    = False

    while True:
        now_et = datetime.now(EASTERN)
        today  = now_et.date()

        # Reset flags at midnight
        if now_et.hour == 0 and now_et.minute == 0:
            _watchlist_done        = False
            _midday_watchlist_done = False
            _morning_done          = False
            _intraday_done         = False
            _eod_done              = False
            _verify_done           = False
            _recalibration_done    = False

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
            time.sleep(60)
            continue

        # 9:15 AM — Finviz watchlist refresh
        if (
            not _watchlist_done
            and now_et.hour == 9
            and now_et.minute >= 15
        ):
            watchlist_refresh()
            _watchlist_done = True

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
            watchlist_refresh()
            _midday_watchlist_done = True

        # 2:00 PM — intraday scan
        if (
            not _intraday_done
            and now_et.hour == 14
            and now_et.minute >= 0
        ):
            intraday_scan()
            _intraday_done = True

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

        time.sleep(30)


if __name__ == "__main__":
    main()
