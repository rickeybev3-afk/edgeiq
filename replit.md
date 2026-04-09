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

### P&L Log — $1,000/trade fixed sizing, 1.5% round-trip slippage

| Date | Ticker | Follow-thru | Net % | Net $ | Running Total |
|---|---|---|---|---|---|
| 2026-04-06 | MIGI | +33.51% | +32.01% | +$320 | +$320 |
| 2026-04-06 | AIB | −4.55% | −6.05% | −$61 | +$259 |
| 2026-04-07 | CYCU | +6.25% | +4.75% | +$48 | +$307 |
| 2026-04-07 | AIB | +3.64% | +2.14% | +$21 | +$328 |
| 2026-04-07 | AGPU | 0% | −1.5% | −$15 | +$313 |
| 2026-04-08 | SKYQ | +12.79% | +11.29% | +$113 | +$426 |
| 2026-04-08 | CLIK | 0% | −1.5% | −$15 | +$411 |

**Total: +$411 on $7,000 deployed = +5.9% in 3 days**
Note: follow_thru% = max move past IB level (theoretical). Realistic capture ~40–60% of this = +$165–$245 actual.
Note: MIGI is carrying most of the weight (+$320 of $411 total). Outlier winner — normal for breakout strategies.
Note: AIB Apr 6 "Win" but negative P&L — brain correctly predicted structure (Both Sides / Ntrl Extreme) but the down move reversed the long entry. Phase 4 stop at opposite IB level handles this.

### Daily Scan Universe Observations

**2026-04-08 (macro sell-off day — strong trending conditions):**
- Full scan: 21 setups, 47.6% win rate (10W/8L/3 pending), avg TCS 42.8
- Profit factor (full scan): ~9.1 (avg win 10.9% vs avg loss 1.5% slippage)
- Alerted trades (TCS≥50): SKYQ +11.3%, CLIK −1.5% → +$489 theoretical on $10k
- Brain recalibration: `normal` ↑ 1.175→1.289 (100% acc/68 samples), `ntrl_extreme` ↓ 1.325→1.211 (56.6%/53), `neutral` ↓ 1.325→1.211 (59.1%/67)

### Early Performance Context (user conversation — 2026-04-08)
- +5.9% in 3 days sounds impressive but is NOT representative yet (7 trades, no statistical weight)
- MIGI single trade (+33.5%) is an outlier — these happen in small-caps but not every week
- Apr 6–8 had strong macro sell-off conditions — favorable for directional IB breakouts
- The EDGE is confirmed in the STRUCTURE: avg win (10.9%) >> avg loss (1.5%) = profit factor ~9
- That asymmetry is what matters more than win rate at this stage
- Goal: 60+ trades across varied market conditions before drawing conclusions
- At 1–3 alerts/day pace → ~3–6 weeks to hit 60 trades
- Choppy/low-vol days will bring win rate down — that's when the real calibration happens
- User noted: "thats crazy for how early this is" — acknowledged but cautioned appropriately
- User confirmed understanding of macro context need: IWM, breadth, Fed regime
- follow_thru% = MAX move past IB (theoretical ceiling). Realistic capture = 40–60% of that
- Losses on range-bound days = essentially free (just 1.5% slippage, no real drawdown)
- Key insight: the ASYMMETRY (big wins, tiny losses) is the actual edge — more important than win rate

---

## Macro Context Layer — Roadmap (user discussion 2026-04-08)

The two-layer brain architecture eventually incorporates macro/tape context as Layer 2 on top of price structure (Layer 1). Priority order:

### Phase 2 — IWM Day Type Classifier (highest priority macro input)
- IWM is the direct proxy for small-cap tape quality
- Classify each day: Trending Up / Trending Down / Range-Bound
- Wire into TCS scoring as a multiplier (e.g. trending day +5–10 TCS points, range-bound day −5 points)
- Store `iwm_day_type` per paper_trade row for future regression analysis
- Expected impact: highest single improvement available after more trade data

### Phase 2/3 — Market Breadth Score (Stockbees-style)
- Count: stocks up >4% on day, new highs, momentum thrusters
- "Tape quality score" — multiplier on top of TCS
- High breadth day: borderline TCS setups get a boost (everything is moving)
- Low breadth day: even clean setups get penalized
- Data source needed: Finviz screener or paid breadth API
- Store `breadth_score` per paper_trade row

### Phase 3 — Regime Filter (Fed rates / risk-on vs risk-off)
- Moves slowly (months between changes) — not a daily signal
- Affects sector rotation within small-caps (growth vs value)
- Use as a long-term regime flag, not a per-trade weight
- Lowest urgency — implement after Phase 2 macro inputs are stable

### Combined scoring vision (Phase 3 target):
```
Final TCS = Base TCS (price structure)
          × Tape Quality Multiplier (IWM day type + breadth)
          × Regime Filter (macro environment)
```
Brain Layer 2 goal: learn separate weights per structure per tape condition
e.g. "Ntrl Extreme on strong IWM day" vs "Ntrl Extreme on flat IWM day" = distinct entries
This is the defensible data moat — no one else has your structure + tape + outcome dataset.

---

## Technical Debt / Architecture (Phase 2 plan)

### backend.py Split Plan — Phase 2 (DO NOT DO during Phase 1 calibration)
backend.py is 7,006 lines — a god file. Safe to run in Phase 1 but a maintenance liability.
Planned split when Phase 1 is complete and bot is paused for maintenance window:
- `brain.py` — brain weights, recalibration, `load_brain_weights`, `_save_brain_weights`, `recalibrate_from_supabase`
- `data.py` — Alpaca fetch, `fetch_bars`, `run_historical_backtest`, `fetch_finviz_watchlist`
- `trades.py` — `log_paper_trades`, `update_paper_trade_outcomes`, `log_accuracy_entry`, position tracking
- `auth.py` — `auth_login`, `auth_signup`, `auth_signout`, `set_user_session`, session cache
- `backend.py` — kept as thin orchestration layer that imports from the above + UI-facing functions
- ⛔ DO NOT split mid-Phase 1 — too many inter-dependencies, bot must keep running

### Streamlit Import in backend.py — FIXED (2026-04-09)
- Problem: `import streamlit as st` at line 13 + module-level `st.session_state` call at import time
- Bot was loading all Streamlit infrastructure just to run scheduled tasks
- Line 1849 `if not st.session_state.get("_position_loaded"):` ran at every bot startup
- Fix: conditional import with `try/except`, guarded module-level call with `_ST_AVAILABLE` flag
- `_ST_AVAILABLE = True` when running as Streamlit app (UI); `= True` but guarded when bot
- Bot no longer at risk of session_state errors on startup

### TICKERS initialization — FIXED (2026-04-09)
- Problem: `TICKERS = _resolve_tickers()` at module import → Supabase call on every bot startup
- If Supabase was down at 9:13 AM, bot started with stale/wrong ticker list silently
- Fix 1: `TICKERS = [defaults]` at import — safe 14-ticker baseline, no network call
- Fix 2: `_run_scan()` now calls `_resolve_tickers()` fresh at scan time (10:47 AM, 2:00 PM, EOD)
- Even if bot restarts after 9:15 AM, scans use the live Supabase list, not startup defaults

---

## Known Issues / Pending

- ~~`accuracy_tracker.correct` field is NULL for all 181 rows~~ — **CONFIRMED NOT NULL**: 132 ✅ + 49 ❌ across 181 rows. Old scratchpad note was inaccurate. Data is fine.
- `alert_price` and `structure_conf` are NULL for current paper_trades rows (not captured at alert time — needs investigation)
- Inside bar flag at IB close per paper trade row (Phase 2)
- `gap_pct` per paper trade row (Phase 2 — extra API call for prior close)
- `rvol_at_ib` per paper trade row (Phase 2 — needs daily volume curve)
- `iwm_day_type` per paper_trade row (Phase 2 — add to morning scan)
- Pattern discovery engine (Phase 2, ~500 rows needed)
- Collective brain layer (Phase 2/3)
- WebSocket key-level triggers (Phase 4 only)
- Webull CSV import pipeline (pending)
- Clean accuracy_tracker of out-of-universe tickers (Unknown, —, etc.)
- backend.py split → brain.py / data.py / trades.py / auth.py (Phase 2 maintenance window)

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
