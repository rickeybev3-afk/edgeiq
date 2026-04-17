# EdgeIQ — Professional Trading Terminal

## What This Is

**EdgeIQ — "Find your edge, then automate it."**

A Python Streamlit trading terminal (port 8080) for Volume Profile / IB structure analysis of small-cap stocks. Built around the Alpaca API (SIP feed) + Supabase (multi-user auth + cloud data). Dark-mode, Plotly charts.

**Core thesis:** IB (Initial Balance — first hour of trading 9:30–10:30 AM) breakouts on high-TCS small-cap setups have asymmetric reward profiles. The edge is structural (Market Profile theory, decades old) — not curve-fitted. Institutions use IB levels. That's why it works.

**Long-term vision:** Cognitive profiling software product. Combined exit ceiling: $500M–$1.2B.

**Roadmap:** Personal calibration engine → Paper automation → Paper execution (Alpaca) → Live autonomous trading → Meta-brain marketplace → Asset class expansion → Institutional data licensing

**Pricing tiers:** $49 / $99 / $199 / $999 / $5K–15K/mo (institutional)

---

## ⚠️ ABSOLUTE CODE RULES — NEVER VIOLATE THESE

### HARD PRESERVATION — NEVER MODIFY THESE 3 FUNCTIONS (EVER):
- `compute_buy_sell_pressure` (backend.py)
- `classify_day_structure` (backend.py)
- `compute_structure_probabilities` (backend.py)

These are the core IP. Any change breaks the entire system. If a task seems to require changing them — it doesn't. Find another way.

### Architecture Rules:
- **Math/logic → `backend.py` only. UI/rendering → `app.py` only.**
- **`_go` variable:** Reserved as `plotly.graph_objects` alias — never reuse as local var.
- **Plotly/HTML:** 6-digit hex or `rgba()` only. No HTML comments in f-strings. No backslashes in f-string expressions (Python 3.11).
- **`app.py` USER_ID:** Must use `st.session_state.get("auth_user_id", "")` inside render functions — NOT global `USER_ID`.
- **SIP:** Paid real-time subscription active — no delay cap on `fetch_bars`. Historical bars always available.

### Data Column Semantics (CRITICAL — wrong assumptions cause silent bugs):
- **`predicted` column** = normally stores the **structure TYPE** ("Neutral", "Ntrl Extreme", "Normal", "Nrml Var", "Dbl Dist", "Non-Trend", "Trend"). In the live bot, directional scans can also store "Bullish Break" / "Bearish Break" here when structure classification produces a directional signal. **Do NOT substring-match for "bull"/"bear" in `predicted` — use exact "Bullish Break"/"Bearish Break" matching, or prefer `actual_outcome` first.**
- **`actual_outcome`** = stores the **real market direction** (e.g. "Bullish Break", "Bearish Break", "Range-Bound", "Both Sides") — set by EOD outcome logic based on whether price broke IB high/low, **regardless of what was predicted**.
- **`win_loss`** = "Win" / "Loss" based on structure prediction accuracy — NOT trade P&L.
- **`follow_thru_pct`** = **MFE** (how far the HIGH went above IB high for bullish, or LOW below IB low for bearish) — **ALWAYS POSITIVE for Bullish Break, ALWAYS NEGATIVE for Bearish Break by definition**. It is NOT the EOD close vs entry. Using `follow_thru_pct × direction` = always positive = fake 100% win rate. Use `win_loss` for win/loss determination in P&L simulations, and `abs(follow_thru_pct)` for the magnitude.
- **`pnl_r_sim`** = sim P&L from `compute_trade_sim()` — **MFE is the INTENDED permanent basis, not a fallback**. It measures the strategy's theoretical ceiling (how far the setup moved in your favor). The adaptive exit layer being built will try to capture as much of this MFE ceiling as possible by detecting volume decay, zone confluence failure, RVOL fade, etc. DO NOT switch to EOD close — MFE is correct by design. High win rate (~80-90%) is expected because MFE only goes negative on `false_break` stop-outs.
- **`close_price`** = **intentionally left NULL** — not used for `pnl_r_sim`. The `compute_trade_sim()` close_price path exists for `eod_pnl_r` only.
- **`eod_pnl_r`** = hold-to-close P&L, **intentionally uncapped** (no stop applied — can exceed -1R). Requires `close_price` column to be populated.
- **`tiered_pnl_r`** = 50% at 1R → stop to BE → 25% at 2R → 25% runner to close (bar-by-bar replay, cannot backfill without bars).

---

## Phases & Current Status

### Phase 1 — Calibration ✅ COMPLETE (ongoing data collection)
Bot runs 100% autonomously. Logs every setup to Supabase. EOD resolves outcomes.
Structure predictions, TCS scoring, and brain recalibration all running live.

### Phase 1.5 — Paper Execution ✅ ACTIVE (activated April 15, 2026)
Alpaca bracket orders live on paper account (`paper-api.alpaca.markets`) — zero real risk.
`LIVE_ORDERS_ENABLED=true` set in Replit Secrets. Bot placing real bracket orders each scan.

**Phase Gate to Phase 2 (Live Money):**
- 30 settled paper trades ✅ PASSED (111/30 as of April 17)
- 60% win rate ✅ PASSED (67.6% as of April 17)
- 30 days of operation 🔴 IN PROGRESS (11/30 days — unlocks May 6, 2026)
- Target: May 6, 2026 (on track)

### Phase 2 — Live Execution 🔲 NEXT
One config change: `IS_PAPER_ALPACA=false` in Replit Secrets.
Switches endpoint from `paper-api.alpaca.markets` → `api.alpaca.markets`.

### Phase 3 — Scale + Distribution 🔲 FUTURE
Compound 1% risk/trade up to $2,000 max. Spread across ALL simultaneous signals.

### Phase 4 — Meta-Brain Marketplace 🔲 FUTURE
Beta users contribute outcomes. Collective brain layer across users.

---

## Bot Schedule (ET — all times Eastern)

| Time | Job |
|------|-----|
| 9:15 AM | Finviz watchlist refresh → save to Supabase |
| 10:47 AM | Morning scan + Telegram alerts + Alpaca bracket orders |
| 11:45 AM | Midday watchlist refresh |
| 2:00 PM | Intraday scan + alerts + Alpaca bracket orders |
| 4:20 PM | Cancel unfilled orders → EOD outcome update → Reconcile fills |
| 4:25 PM | Auto-verify watchlist predictions |
| 4:30 PM | Nightly brain recalibration |
| 11:59 PM | Daily PDF summary (optional) |

Bot is started via `deploy_server.py` which spawns all scheduled jobs.

---

## Two-Brain System

**Two separate brain files — never conflate them:**
- `brain_weights.json` = live personal brain (updated nightly via recalibration)
- `brain_weights_historical.json` = historical prior (5-year backtest weights)
- Blended at runtime via `blend_brain_weights()` in backend.py

**TCS threshold file:** `tcs_thresholds.json` = per-structure TCS cutoffs. Written nightly after recalibration and after batch backtest runs.

**Current live weights (as of April 16, 2026 recalibration):**
- `neutral` = 1.0013, `ntrl_extreme` = 0.9932, `normal` = 1.3334
- All others = 1.0 (baseline)

---

## Env Vars (Alpaca Execution Layer)

| Var | Current | Purpose |
|-----|---------|---------|
| `LIVE_ORDERS_ENABLED` | `true` | Master switch for order placement |
| `IS_PAPER_ALPACA` | `true` | `true` = paper endpoint, `false` = live |
| `RISK_PER_TRADE` | `500` | Fallback. Live = 1% of equity, capped $250–$2,000 |

---

## Confirmed Financial Projections (5-Year Backtest — April 2026)

### Priority Tier System (~14,000 setups across 5 years)

| Tier | Scan | TCS | Setups | Break Rate | Avg R/Trade | True Exp/Setup |
|------|------|-----|--------|------------|-------------|----------------|
| 🔴 P1 | Intraday | 70+ | 940 | 82.6% | +4.60R | +3.78R |
| 🟠 P2 | Intraday | 50–69 | 1,487 | 81.2% | +2.17R | +2.02R |
| 🟡 P3 | Morning | 70+ | 126 | 74.6% | +7.69R | +6.71R |
| 🟢 P4 | Morning | 50–69 | 590 | 69.5% | +1.80R | +1.32R |

~497 breakout trades/year across all 4 tiers = ~2 executable trades/day.

### Financial Model — $500 Risk Per Trade

| Scenario | Per Year |
|----------|---------|
| All 4 tiers, flat $500 sizing | +$690,355 |
| Conservative (25% haircut) | +$517,766 |
| 1% compounding + $2k cap | ~$150k–$300k yr 1 from $50k |

### Personal Account Projection ($7k start, May 6 2026, 1% compounding, $2k cap at $200k)

| Date | Conservative (0.5R/trade) | Expected (0.804R/trade) |
|---|---|---|
| Dec 2026 | $25,800 | $51,400 |
| Dec 2027 | $241,800 | $675,200 |
| Dec 2028 | $733,800 | $1,466,400 |
| May 2031 (5yr) | $1,922,800 | $3,378,300 |

---

## Three P&L Simulation Scenarios (Task #45, April 2026)

Built in `compute_trade_sim()` and `compute_trade_sim_tiered()` in backend.py.

| Scenario | Column | Description | Capped? |
|----------|--------|-------------|---------|
| MFE (Best Possible) | `pnl_r_sim` | Max intraday excursion — theoretical ceiling | Loss capped at -1R |
| EOD Hold | `eod_pnl_r` | Raw hold-to-close, no stop applied | **Uncapped** (can exceed -1R) |
| 50/25/25 Ladder | `tiered_pnl_r` | 50% at 1R → stop to BE → 25% at 2R → 25% runner | Bar-by-bar replay only |

**Tiered sim key mechanics:**
- `entered` flag: position only active once bar crosses entry level (ib_high for bull, ib_low for bear)
- Stop-priority convention: stop checked before targets on every bar (worst-case assumption)
- Cannot backfill tiered from stored data — requires live bar replay

---

## Files

| File | Purpose |
|---|---|
| `app.py` | All UI/rendering — Streamlit tabs, charts, widgets |
| `backend.py` | All math/logic — IB engine, TCS, probabilities, backtest, order placement |
| `paper_trader_bot.py` | Autonomous daily bot — scheduler, alerts, EOD, order placement, recalibration |
| `batch_backtest.py` | Historical backtest runner (currently configured: 1,260 days / all scan types) |
| `run_sim_backfill.py` | Recomputes pnl_r_sim (MFE-based) on all existing breakout rows — safe to re-run if compute_trade_sim() logic changes |
| `deploy_server.py` | Starts all bots on deployment |
| `brain_weights.json` | Live adaptive brain multipliers — DO NOT MODIFY DIRECTLY |
| `brain_weights_historical.json` | Historical prior weights — DO NOT MODIFY |
| `tcs_thresholds.json` | Per-structure TCS cutoffs — written by recalibration + batch backtest |
| `.local/build_notes.md` | Public build notes |
| `.local/build_notes_private.md` | Private build notes (personal info, strategy, roadmap) |
| `.local/ip_documentation.md` | Proprietary systems documentation |
| `.local/rls_setup.sql` | Supabase RLS policies |
| `.streamlit/config.toml` | enableCORS=false, enableXsrfProtection=false, port 8080 |

**Architecture rule:** Math/logic → `backend.py` only. UI/rendering → `app.py` only.

---

## Supabase

- **Project:** `kqrwrvtelexylqonsjsl`
- **SQL Editor:** https://supabase.com/dashboard/project/kqrwrvtelexylqonsjsl/sql/new
- **User ID:** `a5e1fcab-8369-42c4-8550-a8a19734510c`
- **RLS:** ON permanently — all queries need `.eq("user_id", user_id)`

### `paper_trades` columns
`id, user_id, trade_date, ticker, tcs, predicted, ib_low, ib_high, open_price, actual_outcome, follow_thru_pct, win_loss, false_break_up, false_break_down, min_tcs_filter, created_at, alert_price, alert_time, post_alert_move_pct, structure_conf, regime_tag, rvol, gap_pct, mae, mfe, entry_time, exit_trigger, entry_ib_distance, scan_type, sim_outcome, pnl_r_sim, pnl_pct_sim, entry_price_sim, stop_price_sim, stop_dist_pct, target_price_sim, alpaca_order_id, alpaca_qty, order_placed_at, alpaca_fill_price, close_price, eod_pnl_r, tiered_pnl_r, ib_range_pct, vwap_at_ib`

**New columns (added Task #205, April 2026):**
- `ib_range_pct` — `(ib_high - ib_low) / open_price * 100`. Filter: skip order if ≥ 10%. Populated at `log_paper_trades()` insert. Retroactively backfilled 142 rows April 17.
- `vwap_at_ib` — VWAP at IB close time (~10:30 AM ET). Patched by `log_context_levels()`. Filter: close_price must be on correct VWAP side for the predicted direction. Retroactively backfilled 110 rows via `vwap_replay.py --patch` April 17.

### `backtest_sim_runs` columns
`id, user_id, sim_date, ticker, tcs, predicted, actual_outcome, ib_low, ib_high, follow_thru_pct, false_break_up, false_break_down, scan_type, sim_outcome, pnl_r_sim, pnl_pct_sim, entry_price_sim, stop_price_sim, stop_dist_pct, target_price_sim, close_price, eod_pnl_r, tiered_pnl_r, open_price, rvol, poc_price, ib_range_pct, gap_pct, gap_vs_ib_pct, day_of_week, entry_hour`

**Live counts (as of April 17, 2026):**
- `backtest_sim_runs`: 33,776+ rows | 16,766 Bullish/Bearish Break with pnl_r_sim
- `paper_trades`: 143 total rows | 111 settled (win_loss set) | 74 with pnl_r_sim
- Paper trade WR (all settled): **67.6%** | TCS≥50: **84.6%** | Full filter intraday: **92.3%**
- Full filter backtest target: **95.7% WR, +2.46R expectancy** (TCS≥50 + IB<10% + VWAP)
- Total R logged live (74 priced trades): **+58.6R** avg **+0.79R/trade**

### SQL Migrations — ALL COMPLETED ✅
All columns listed below are confirmed present in both tables. No pending migrations.

---

## Alpaca Execution — How Bracket Orders Work

```
Morning scan (10:47 AM) → qualifying setup (TCS ≥ 50, Bullish Break)
  └─ place_alpaca_bracket_order() called:
       qty = int($500 risk ÷ IB range per share)
       Entry:       stop buy triggered at IB high
       Stop loss:   stop sell at IB low       (−1R = −$500)
       Take profit: limit sell at IB high + 2×IB range  (+2R = +$1,000)
       → Alpaca manages all 3 legs automatically

4:20 PM → cancel_alpaca_day_orders()  (removes unfilled entries)
        → reconcile_alpaca_fills()     (patches actual fill prices to Supabase)
```

Bearish Break = mirror image (sell short at IB low, stop at IB high, target = IB low − 2×range).

---

## Standalone Pages (no login required — URL param gated)

| URL Param | Access Key | Purpose |
|---|---|---|
| `/?notes=<USER_ID>` | User ID | Public build notes viewer |
| `/?private=<PRIVATE_KEY>` | `7c3f9b2a-4e1d-4a8c-b05f-3d8e6f1a9c4b` | Private build notes |
| `/?journal=<USER_ID>` | User ID | Trade Journal Logger |
| `/?beta=<BETA_USER_ID>` | Beta user's ID | Beta tester portal |

## Streamlit Multipage App (/pages/)

| File | Path | Purpose |
|---|---|---|
| `pages/build_notes.py` | `/build_notes` | Public build notes markdown viewer |
| `pages/build_notes_private.py` | `/build_notes_private` | Private build notes (passcode gated) |
| `pages/filter_sim.py` | `/filter_sim` | **Interactive filter simulation** — dial TCS/IB/VWAP sliders, see live WR & expectancy from 33k+ historical trades. Filter funnel, IB bucket chart, scan type split, full combo matrix. Cache 60 min. |

## Analysis / Utility Scripts

| Script | Purpose | Key Flags |
|---|---|---|
| `filter_validation_backtest.py` | Validate IB+VWAP filters against full historical dataset | `--user-id`, `--max-rows` |
| `vwap_replay.py` | Retroactive VWAP replay on live paper_trades — fetches Alpaca 1-min bars, computes VWAP at IB close, applies full filter | `--patch` (writes vwap_at_ib back to DB), `--user-id` |
| `adaptive_exit_backtest.py` | Exit strategy backtesting | — |
| `batch_backtest.py` | Batch simulation run | `--days`, `--screener`, `--dry-run`, `--user-id` |
| `backfill_close_prices.py` | Fill missing close prices | — |
| `run_sim_backfill.py` | Backfill simulation outcomes | — |

---

## Known Issues / Pending Work

### Active / Near-term
- `alert_price` and `structure_conf` are NULL for current paper_trades rows (not captured at alert time)
- `tiered_pnl_r` for paper trades never populates — EOD bot doesn't replay intraday bars (needs bar fetch at 4:20 PM)
- Replay Min TCS selectbox options (Any/≥40/≥50/≥60/≥65/≥70/≥75/≥80/≥90) — Best TCS buttons snap to nearest; fine for now
- Alpaca fill reconciliation matches on ticker+date only — add order_id matching once fills confirmed live

### Phase 2 (after 30-trade gate)
- Adaptive exit layer: detect volume decay, zone confluence failure, RVOL fade → exit early to capture more of MFE ceiling
- Pattern discovery engine (~500 rows needed for statistical significance)
- `iwm_day_type` per paper_trade row (market regime context)
- Inside bar flag at IB close per paper trade row

### Phase 3+
- Collective brain layer (multi-user anonymized outcomes → baseline signal)
- backend.py split → brain.py / data.py / trades.py / auth.py (maintenance window)
- Multi-timeframe IB detection (morning/midday/EOD evolving structure)

---

## Code Rules

- **Plotly/HTML:** 6-digit hex or `rgba()` only. No HTML comments in f-strings. No backslashes in f-string expressions (Python 3.11).
- **`_go` variable:** Reserved as `plotly.graph_objects` alias — never reuse as local var.
- **SIP:** Paid real-time subscription active — no delay cap on `fetch_bars`.
- **`app.py` USER_ID:** Must use `st.session_state.get("auth_user_id", "")` inside render functions — NOT global `USER_ID`.
- **Standing rule:** Always update `.local/ip_documentation.md` when new proprietary systems/methods are built.
- **Standing rule:** All personal info stays in `.local/build_notes_private.md` ONLY.

---

## Stack

- Python 3.11, Streamlit, Plotly, Pandas, NumPy, PyTZ, Alpaca-py, Supabase-py
- pnpm monorepo (legacy from template — ignore for trading terminal work)
- Port: 8080
