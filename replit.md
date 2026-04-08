# EdgeIQ — Professional Trading Terminal

## What This Is

A Python Streamlit trading terminal (port 8080) for Volume Profile analysis of small-cap stocks. Built around the Alpaca API (SIP feed) + Supabase (multi-user auth + cloud data). Dark-mode, Plotly charts. $79/month SaaS pricing target.

**Core thesis:** IB (Initial Balance — first hour of trading 9:30–10:30 AM) breakouts on high-TCS small-cap setups have asymmetric reward profiles — wins are 7–9× larger than losses in magnitude. The edge is in the SIZE of wins vs losses, not just win rate.

**Autonomous paper trading → calibration → live trading pipeline.**

---

## Current Phase: Phase 1 — Calibration

Bot runs 100% autonomously. The user should only use:
- Trade Journal (read-only)
- Telegram alerts
- Analytics tab (read-only)
- Playbook tab (read-only)

**User must never touch:** orange 🔒 locked controls, brain_weights.json directly, or bot schedule.

---

## Files

| File | Purpose |
|---|---|
| `app.py` | All UI/rendering — Streamlit tabs, charts, widgets |
| `backend.py` | All math/logic — IB engine, TCS, probabilities, backtest |
| `paper_trader_bot.py` | Autonomous daily bot — scheduler, alerts, EOD, recalibration |
| `brain_weights.json` | Adaptive brain multipliers — DO NOT MODIFY DIRECTLY |
| `.local/build_notes.md` | Session build notes |
| `.local/rls_setup.sql` | Supabase RLS policies |
| `.streamlit/config.toml` | enableCORS=false, enableXsrfProtection=false, port 8080 |

**Architecture rule:** Math/logic → `backend.py` only. UI/rendering → `app.py` only.

---

## Bot Schedule (ET — all times Eastern)

| Time | Job |
|---|---|
| 9:15 AM | Finviz watchlist refresh → save to Supabase |
| 10:47 AM | Morning scan + Telegram alerts (17 min after IB close — SIP safe) |
| 2:00 PM | Intraday scan |
| 4:20 PM | EOD outcome update (SIP free tier needs 16+ min delay after 4:00 PM close) |
| 4:30 PM | Nightly brain recalibration |

**SIP free-tier rule:** Alpaca free SIP blocks queries for data <15 min old — applies even after market close. `fetch_bars` always applies a 16-min cap for today's data. EOD at 4:20 PM ensures full-day bars including the 4:00 PM close bar are safely accessible.

---

## Supabase

- **Project:** `kqrwrvtelexylqonsjsl`
- **SQL Editor:** https://supabase.com/dashboard/project/kqrwrvtelexylqonsjsl/sql/new
- **User ID:** `a5e1fcab-8369-42c4-8550-a8a19734510c`

### `paper_trades` columns
`id, user_id, trade_date, ticker, tcs, predicted, ib_low, ib_high, open_price, actual_outcome, follow_thru_pct, win_loss, false_break_up, false_break_down, min_tcs_filter, created_at, alert_price, alert_time, post_alert_move_pct, structure_conf`

### `accuracy_tracker` columns
Manual journal entries. 181 rows total. **Known issue: `correct` field is NULL for all rows** — win/loss not being stored as True/False, data quality problem to fix in Phase 2.

---

## Brain / Adaptive Layer

**HARD PRESERVATION RULE — NEVER MODIFY THESE FUNCTIONS:**
- `compute_buy_sell_pressure`
- `classify_day_structure`
- `compute_structure_probabilities`

**Baseline weights** (what the brain started at):
- `normal` = 1.0
- `neutral` = 1.4999
- `ntrl_extreme` = 1.4999

**Current weights (as of 2026-04-08 recalibration):**
- `normal` = 1.2887 (↑ — 100% accuracy over 68 samples — gaining confidence)
- `ntrl_extreme` = 1.2112 (↓ — 56.6% accuracy over 53 samples — becoming more skeptical)
- `neutral` = 1.2112 (↓ — 59.1% accuracy over 67 samples — becoming more skeptical)

**Recalibration thresholds:**
- MIN_SAMPLES: <50 rows→3, 50-200→5, 200-500→8, 500+→12
- EMA rate: <10 samples→0.10, 10-25→0.15, 25-50→0.25, 50-100→0.35, 100+→0.40
- Volume-weighted blend replaces fixed 50/50 between journal + bot data sources

---

## Slippage

- All Paper Trade sim calls: **0.75%** one-way (1.5% round-trip)
- Backtest tab: `_bt_slippage` slider (default 0.5%)
- Phase 4 live trading: 0.75%

---

## Telegram

- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (chat_id=1606612573)
- Alerts: morning scan setups, EOD summary, brain recalibration results
- Format: ticker, structure prediction, IB range, scan time, price at IB close, % change from open

---

## Trade Data Log (running)

### Paper Trades — Bot Generated
**Goal: 60+ trades for statistical confidence. Currently: 7 total (3 days of data).**

| Date | Ticker | TCS | Predicted | Actual | Result | Follow-thru |
|---|---|---|---|---|---|---|
| 2026-04-06 | AIB | 58.7 | Ntrl Extreme | Both Sides | ✅ Win | -4.55% |
| 2026-04-06 | MIGI | 52.7 | Ntrl Extreme | Bullish Break | ✅ Win | +33.51% |
| 2026-04-07 | CYCU | 60 | Ntrl Extreme | Bullish Break | ✅ Win | +6.25% |
| 2026-04-07 | AGPU | 60 | Neutral | Range-Bound | ❌ Loss | 0% |
| 2026-04-07 | AIB | 60 | Ntrl Extreme | Bullish Break | ✅ Win | +3.64% |
| 2026-04-08 | SKYQ | 60 | Ntrl Extreme | Bullish Break | ✅ Win | +12.79% |
| 2026-04-08 | CLIK | 60 | Ntrl Extreme | Range-Bound | ❌ Loss | 0% |

**Running stats: 5W / 2L = 71.4% win rate** (too small to trust — need 60+)

### Daily P&L Observations (simulated, $5k/trade equal sizing, 1.5% slippage)

**2026-04-08:**
- Full scan universe: 21 setups, 47.6% win rate (10W/8L/3 pending)
- Alerted trades (TCS≥50): SKYQ +11.3% net, CLIK -1.5% net → **+$489 theoretical max, ~$275–325 realistic** on $10k capital
- Profit factor (full scan): ~9.1 (avg win 10.9% vs avg loss 1.5%)
- Brain recalibration ran: `normal` ↑, `ntrl_extreme` ↓, `neutral` ↓
- Note: strong trending day (macro sell-off) — favorable for IB breakout strategy

---

## Known Issues / Pending

- `accuracy_tracker.correct` field is NULL for all 181 rows — needs data audit (Phase 2)
- `alert_price` and `structure_conf` are NULL for current paper_trades rows (not captured at alert time — needs investigation)
- Inside bar flag at IB close per paper trade row (Phase 2)
- `gap_pct` per paper trade row (Phase 2 — extra API call for prior close)
- `rvol_at_ib` per paper trade row (Phase 2 — needs daily volume curve)
- Pattern discovery engine (Phase 2, ~500 rows needed)
- Collective brain layer (Phase 2/3)
- WebSocket key-level triggers (Phase 4 only)
- Webull CSV import pipeline (pending)
- Clean accuracy_tracker of out-of-universe tickers (Unknown, —, etc.)

---

## Code Rules

- **Plotly/HTML:** 6-digit hex or `rgba()` only. No HTML comments in f-strings. No backslashes in f-string expressions (Python 3.11).
- **`_go` variable:** Reserved as `plotly.graph_objects` alias — never reuse as local var.
- **SIP free-tier:** `fetch_bars` caps SIP end to `now - 16min` for today's data, always (during AND after market hours).

---

## Stack

- Python 3.11, Streamlit, Plotly, Pandas, NumPy, PyTZ, Alpaca-py, Supabase-py
- pnpm monorepo (legacy from template — ignore for trading terminal work)
- Port: 8080
