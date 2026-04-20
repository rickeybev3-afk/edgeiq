#!/usr/bin/env python3
"""
EdgeIQ — Offering Short Bot
Monitors SEC EDGAR for dilutive offering filings (424B4, S-3/A, etc.)
and automatically shorts the ticker on Alpaca paper.

Logic:
  1. Poll EDGAR atom feed every 60s for new 424B4/S-3 filings
  2. Map CIK → ticker via SEC company tickers JSON
  3. Filter: $1–$30 price, must be tradeable
  4. Short via Alpaca bracket order (market entry, +8% stop, -20% target)
  5. Telegram alert on entry and exit
"""

import logging
import os
import re
import sys
import time
import json
import requests
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("offering_short")

# ── Config ─────────────────────────────────────────────────────────────────────
IS_PAPER      = True
ALPACA_BASE   = "https://paper-api.alpaca.markets" if IS_PAPER else "https://api.alpaca.markets"
ALPACA_DATA   = "https://data.alpaca.markets"

ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

_IS_PROD   = os.getenv("EDGEIQ_PRODUCTION", "").strip() == "1"
TG_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() if _IS_PROD else ""
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID",   "").strip() if _IS_PROD else ""

ALPACA_HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type":        "application/json",
}
# SEC requires a descriptive User-Agent or requests get blocked
SEC_HEADERS = {
    "User-Agent": "EdgeIQ/1.0 contact@edgeiq.app",
    "Accept-Encoding": "gzip, deflate",
}

POLL_INTERVAL   = 60     # seconds between EDGAR polls
RISK_DOLLARS    = 150    # $ risk per trade (1R)
MIN_PRICE       = 1.00   # skip penny stocks
MAX_PRICE       = 30.00  # skip high-priced stocks unlikely to be dilutive
STOP_PCT        = 0.08   # stop loss: entry + 8%
TARGET_PCT      = 0.15   # take profit: entry - 15%

# SEC form types that signal a dilutive share offering
OFFERING_FORMS = ["424B4", "424B3", "424B2", "424B1", "S-3/A"]

# ── State ──────────────────────────────────────────────────────────────────────
_seen_accessions: set = set()
_cik_to_ticker:  dict = {}


# ── Telegram ───────────────────────────────────────────────────────────────────
def tg_send(msg: str) -> bool:
    if not TG_TOKEN or not TG_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=8,
        )
        return r.status_code == 200
    except Exception:
        return False


# ── SEC EDGAR ──────────────────────────────────────────────────────────────────
def load_company_tickers():
    """Download SEC CIK → ticker mapping. Called once at startup."""
    global _cik_to_ticker
    try:
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=SEC_HEADERS, timeout=15,
        )
        data = r.json()
        _cik_to_ticker = {
            str(v["cik_str"]).zfill(10): v["ticker"].upper()
            for v in data.values()
        }
        log.info(f"[EDGAR] Loaded {len(_cik_to_ticker):,} CIK → ticker mappings")
    except Exception as e:
        log.warning(f"[EDGAR] company tickers load failed: {e}")


def _extract_cik(text: str) -> str:
    """Pull a zero-padded 10-digit CIK from any EDGAR URL/string."""
    # Prefer /edgar/data/1234567/ path format
    m = re.search(r"/edgar/data/(\d+)/", text)
    if m:
        return m.group(1).zfill(10)
    # Fall back to CIK=1234567 query param
    m = re.search(r"CIK[=0]*(\d+)", text, re.IGNORECASE)
    if m:
        return m.group(1).zfill(10)
    return ""


def get_latest_filings(form_type: str, count: int = 20) -> list[dict]:
    """Fetch newest filings of a given form type from EDGAR atom feed."""
    url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcurrent&type={form_type}&dateb=&owner=include"
        f"&count={count}&output=atom"
    )
    try:
        r = requests.get(url, headers={**SEC_HEADERS, "Accept": "application/atom+xml"}, timeout=12)
        root = ET.fromstring(r.text)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in root.findall("a:entry", ns):
            entry_id  = entry.findtext("a:id", "", ns).strip()
            title     = entry.findtext("a:title", "", ns).strip()
            updated   = entry.findtext("a:updated", "", ns).strip()
            summary   = entry.findtext("a:summary", "", ns) or ""
            # Grab link href for CIK extraction
            link_el   = entry.find("a:link", ns)
            link_href = link_el.attrib.get("href", "") if link_el is not None else ""

            cik = _extract_cik(entry_id) or _extract_cik(link_href) or _extract_cik(summary)
            results.append({
                "accession": entry_id,
                "title":     title,
                "updated":   updated,
                "cik":       cik,
                "form":      form_type,
            })
        return results
    except Exception as e:
        log.warning(f"[EDGAR] {form_type} feed error: {e}")
        return []


# ── Alpaca ─────────────────────────────────────────────────────────────────────
def get_ask_price(ticker: str) -> float | None:
    """Return latest ask price from Alpaca SIP feed."""
    try:
        r = requests.get(
            f"{ALPACA_DATA}/v2/stocks/{ticker}/quotes/latest",
            headers=ALPACA_HEADERS,
            params={"feed": "sip"},
            timeout=5,
        )
        ask = r.json().get("quote", {}).get("ap", 0)
        return float(ask) if ask else None
    except Exception:
        return None


def is_market_open() -> bool:
    """Check Alpaca clock — only short during market hours."""
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/clock", headers=ALPACA_HEADERS, timeout=5)
        return r.json().get("is_open", False)
    except Exception:
        return False


def place_short_bracket(ticker: str, price: float) -> dict:
    """
    Market short with bracket exits.
    stop_loss  = buy stop  at price + STOP_PCT   (cap the loss)
    take_profit = buy limit at price - TARGET_PCT (lock the gain)
    """
    qty          = max(1, int(RISK_DOLLARS / (price * STOP_PCT)))
    stop_price   = round(price * (1 + STOP_PCT),   2)
    target_price = round(price * (1 - TARGET_PCT),  2)

    payload = {
        "symbol":        ticker,
        "qty":           str(qty),
        "side":          "sell",
        "type":          "market",
        "time_in_force": "day",
        "order_class":   "bracket",
        "stop_loss":     {"stop_price":  str(stop_price)},
        "take_profit":   {"limit_price": str(target_price)},
    }
    try:
        r = requests.post(
            f"{ALPACA_BASE}/v2/orders",
            headers=ALPACA_HEADERS,
            json=payload,
            timeout=10,
        )
        d = r.json() if r.content else {}
        if r.status_code in (200, 201):
            return {
                "ok":       True,
                "order_id": d.get("id", ""),
                "qty":      qty,
                "entry":    price,
                "stop":     stop_price,
                "target":   target_price,
            }
        return {"ok": False, "error": d.get("message", f"HTTP {r.status_code}")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Core processing ────────────────────────────────────────────────────────────
def process_filing(filing: dict):
    """Evaluate a new SEC filing — short if it's a qualifying offering."""
    acc = filing["accession"]
    if acc in _seen_accessions:
        return
    _seen_accessions.add(acc)

    cik    = filing["cik"]
    ticker = _cik_to_ticker.get(cik)
    if not ticker:
        log.debug(f"[Filing] CIK {cik} not in ticker map — skipping")
        return

    log.info(f"[Filing] New {filing['form']} → {ticker} (CIK {cik})")

    if not is_market_open():
        log.info(f"[Filing] {ticker} — market closed, queuing skipped")
        tg_send(
            f"📋 <b>Offering detected (pre/after hours)</b>\n"
            f"Ticker: <b>{ticker}</b> | Form: {filing['form']}\n"
            f"Market closed — no order placed"
        )
        return

    price = get_ask_price(ticker)
    if price is None:
        log.warning(f"[Filing] {ticker} — couldn't get price, skipping")
        return
    if not (MIN_PRICE <= price <= MAX_PRICE):
        log.info(f"[Filing] {ticker} @ ${price:.2f} — outside range ${MIN_PRICE}–${MAX_PRICE}, skipping")
        return

    log.info(f"[Filing] ⚡ Shorting {ticker} @ ${price:.2f} | stop ${price*(1+STOP_PCT):.2f} | target ${price*(1-TARGET_PCT):.2f}")
    result = place_short_bracket(ticker, price)

    acct_type = "PAPER" if IS_PAPER else "LIVE"
    if result["ok"]:
        qty = result["qty"]
        msg = (
            f"🩳 <b>{acct_type} Offering Short — {ticker}</b>\n"
            f"Form: {filing['form']}\n"
            f"Entry: ${result['entry']:.2f} | "
            f"Stop: ${result['stop']:.2f} (+{STOP_PCT*100:.0f}%) | "
            f"Target: ${result['target']:.2f} (-{TARGET_PCT*100:.0f}%)\n"
            f"Qty: {qty} sh | Risk: ${RISK_DOLLARS}\n"
            f"ID: {result['order_id'][:8]}…"
        )
        log.info(f"  ✅ Short placed — qty={qty} entry=${result['entry']} stop=${result['stop']} target=${result['target']}")
        tg_send(msg)
    else:
        log.warning(f"  ❌ Short failed: {result['error']}")
        tg_send(
            f"⚠️ <b>Offering short FAILED — {ticker}</b>\n"
            f"Form: {filing['form']}\n"
            f"Error: {result['error']}"
        )


# ── Main loop ──────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("EdgeIQ — Offering Short Bot")
    log.info("=" * 60)
    log.info(f"Mode    : {'PAPER' if IS_PAPER else 'LIVE'}")
    log.info(f"Forms   : {', '.join(OFFERING_FORMS)}")
    log.info(f"Range   : ${MIN_PRICE}–${MAX_PRICE}")
    log.info(f"Risk    : ${RISK_DOLLARS}/trade | Stop +{STOP_PCT*100:.0f}% | Target -{TARGET_PCT*100:.0f}%")
    log.info(f"Poll    : every {POLL_INTERVAL}s")
    log.info(f"Telegram: {'ENABLED' if TG_TOKEN else 'DISABLED (dev)'}")

    load_company_tickers()

    # Seed with existing filings so we don't act on old news at startup
    log.info("[EDGAR] Seeding existing filings to avoid acting on stale data...")
    for form in OFFERING_FORMS:
        for f in get_latest_filings(form, count=40):
            _seen_accessions.add(f["accession"])
    log.info(f"[EDGAR] Seeded {len(_seen_accessions)} existing filings — watching for NEW ones only")

    tg_send(
        f"🩳 <b>Offering Short Bot — STARTED</b>\n"
        f"Monitoring: {', '.join(OFFERING_FORMS)}\n"
        f"Price: ${MIN_PRICE}–${MAX_PRICE} | Risk: ${RISK_DOLLARS}/trade"
    )

    while True:
        try:
            for form in OFFERING_FORMS:
                filings = get_latest_filings(form, count=10)
                for f in filings:
                    process_filing(f)
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log.info("Shutting down.")
            break
        except Exception as e:
            log.error(f"[Main] Unhandled error: {e}", exc_info=True)
            time.sleep(30)


if __name__ == "__main__":
    main()
