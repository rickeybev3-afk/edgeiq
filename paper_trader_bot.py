"""
EdgeIQ Autonomous Paper Trader Bot
===================================
Runs independently all day without the browser open.

Schedule (ET):
  10:35 AM  — IB forms → scan watchlist, filter TCS ≥ MIN_TCS, log entries
   4:05 PM  — Market closes → re-scan and update outcomes with full-day data

Required environment secrets (set in Replit Secrets):
  ALPACA_API_KEY      — your Alpaca API key
  ALPACA_SECRET_KEY   — your Alpaca secret key
  PAPER_TRADE_USER_ID — your EdgeIQ user ID (a5e1fcab-8369-42c4-8550-a8a19734510c)

Optional env vars (set in Replit Secrets or below):
  PAPER_TRADE_TICKERS — comma-separated ticker list (defaults to watchlist below)
  PAPER_TRADE_MIN_TCS — minimum TCS threshold (default: 50)
  PAPER_TRADE_FEED    — sip or iex (default: sip)
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
        recalibrate_from_supabase,
    )
except ImportError as e:
    log.error(f"Cannot import backend: {e}. Make sure paper_trader_bot.py is in the same directory as backend.py.")
    raise


def _resolve_tickers() -> list:
    """Return tickers to scan.

    Priority:
      1. PAPER_TRADE_TICKERS env var (manual override)
      2. User's saved watchlist from Supabase
      3. Hardcoded default fallback
    """
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


TICKERS = _resolve_tickers()


def _market_is_open(now_et: datetime) -> bool:
    """Return True if now_et falls within regular market hours Mon–Fri."""
    if now_et.weekday() >= 5:
        return False
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now_et <= market_close


def _run_scan(trade_date: date, cutoff_h: int = 10, cutoff_m: int = 30) -> list:
    """Fetch bars, run IB engine, return all results (unfiltered)."""
    log.info(f"Running scan for {trade_date} | cutoff {cutoff_h:02d}:{cutoff_m:02d} | {len(TICKERS)} tickers")
    results, summary = run_historical_backtest(
        ALPACA_API_KEY, ALPACA_SECRET_KEY,
        trade_date=trade_date,
        tickers=TICKERS,
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


def morning_scan():
    """Run at 10:35 AM: log qualifying paper trade entries."""
    today = date.today()
    log.info("=" * 60)
    log.info("MORNING SCAN — logging IB entries")
    log.info("=" * 60)

    results = _run_scan(today, cutoff_h=10, cutoff_m=30)
    if not results:
        log.warning("No results from morning scan.")
        return

    qualified = [
        dict(r, sim_date=str(today))
        for r in results
        if float(r.get("tcs", 0)) >= MIN_TCS
    ]
    log.info(f"{len(qualified)} setups passed TCS ≥ {MIN_TCS} (of {len(results)} scanned)")

    if qualified:
        result = log_paper_trades(qualified, user_id=USER_ID, min_tcs=MIN_TCS)
        log.info(f"Logged: {result.get('saved', 0)} new | skipped: {result.get('skipped', 0)} (already exist)")
        for r in qualified:
            log.info(
                f"  {r['ticker']:6s} | TCS {r.get('tcs', 0):5.0f} | "
                f"predicted: {r.get('predicted', '—'):20s} | IB {r.get('ib_low', 0):.2f}–{r.get('ib_high', 0):.2f}"
            )
    else:
        log.info("No setups met TCS threshold today.")


def eod_update():
    """Run at 4:05 PM: update paper trades with full-day outcomes."""
    today = date.today()
    log.info("=" * 60)
    log.info("EOD UPDATE — resolving outcomes with full-day bar data")
    log.info("=" * 60)

    results = _run_scan(today, cutoff_h=10, cutoff_m=30)
    if not results:
        log.warning("No results from EOD scan — cannot update outcomes.")
        return

    upd = update_paper_trade_outcomes(str(today), results, user_id=USER_ID)
    log.info(f"Updated {upd.get('updated', 0)} paper trade outcome(s) for {today}")
    for r in results:
        log.info(
            f"  {r['ticker']:6s} | {r.get('win_loss', '?'):4s} | "
            f"actual: {r.get('actual_outcome', '—'):18s} | FT {r.get('aft_move_pct', 0):+.1f}%"
        )


def nightly_recalibration():
    """Run at 4:10 PM: read all Supabase outcome data, update brain weights.

    Combines:
      - accuracy_tracker  (journal-verified manual trades)
      - paper_trades      (bot paper trading outcomes)

    Uses 30% EMA learning rate with minimum 5 samples per structure.
    """
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
            return
        deltas = cal.get("deltas", [])
        log.info(f"Brain weights updated — {len(deltas)} structure(s) adjusted:")
        for d in deltas:
            direction = "▲" if d["delta"] > 0 else ("▼" if d["delta"] < 0 else "—")
            log.info(
                f"  {d['key']:16s} | {d['old']:.4f} → {d['new']:.4f} "
                f"({direction}{abs(d['delta']):.4f}) | "
                f"acc {d['accuracy']}% over {d['samples']} samples"
            )
        unchanged = [
            k for k in ["trend_bull", "trend_bear", "double_dist", "non_trend",
                         "normal", "neutral", "ntrl_extreme", "nrml_variation"]
            if k not in [d["key"] for d in deltas]
        ]
        if unchanged:
            log.info(f"  Unchanged (< 5 samples): {', '.join(unchanged)}")
    except Exception as exc:
        log.error(f"Nightly recalibration failed: {exc}")


def main():
    log.info("EdgeIQ Paper Trader Bot starting up...")

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        log.error(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set as Replit Secrets. "
            "Go to the Secrets tab and add them, then restart this workflow."
        )
        return

    log.info(f"Watching {len(TICKERS)} tickers | TCS ≥ {MIN_TCS} | feed: {FEED.upper()}")
    log.info(f"User: {USER_ID}")
    log.info("Schedule: 10:35 AM ET → morning scan | 4:05 PM ET → EOD update | 4:10 PM ET → brain recalibration")

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

    _morning_done       = False
    _eod_done           = False
    _recalibration_done = False

    while True:
        now_et = datetime.now(EASTERN)
        today  = now_et.date()

        # Reset flags at midnight
        if now_et.hour == 0 and now_et.minute == 0:
            _morning_done       = False
            _eod_done           = False
            _recalibration_done = False

        if not _market_is_open(now_et):
            # Allow recalibration after market close (4:10+ PM)
            if (
                not _recalibration_done
                and now_et.weekday() < 5
                and now_et.hour == 16
                and now_et.minute >= 10
            ):
                nightly_recalibration()
                _recalibration_done = True
            next_check = 60
            log.debug(f"Market closed. Next check in {next_check}s")
            time.sleep(next_check)
            continue

        # 10:35 AM — morning scan
        if (
            not _morning_done
            and now_et.hour == 10
            and now_et.minute >= 35
        ):
            morning_scan()
            _morning_done = True

        # 4:05 PM — EOD update
        if (
            not _eod_done
            and now_et.hour == 16
            and now_et.minute >= 5
        ):
            eod_update()
            _eod_done = True

        # 4:10 PM — brain recalibration (5 min after EOD so outcomes are settled)
        if (
            not _recalibration_done
            and now_et.hour == 16
            and now_et.minute >= 10
        ):
            nightly_recalibration()
            _recalibration_done = True

        time.sleep(30)


if __name__ == "__main__":
    main()
