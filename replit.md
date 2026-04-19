# EdgeIQ — Professional Trading Terminal

## Overview

EdgeIQ is a Python Streamlit trading terminal for Volume Profile and Initial Balance (IB) structure analysis of small-cap stocks, primarily IB breakouts. Integrates Alpaca API for real-time data and trading, Supabase for multi-user auth and cloud data, Telegram for alerts, and Finviz for screener data.

Core purpose: identify and automate IB-breakout strategies in high-TCS small-cap setups. Long-term vision: cognitive profiling software and a "Brain Marketplace" where verified traders rent calibrated algorithms (50% rev share).

## Phase Gate Status (as of Apr 18, 2026)

**Phase 1 — COMPLETE.** 111 paper trades logged. TCS≥50 win rate: **80.8%** (confirmed Apr 17, 2026 after false-break tracking bug fixed).

**Phase 1.5 — IN PROGRESS.** Live Alpaca bracket orders wired and tested in paper mode. `LIVE_ORDERS_ENABLED=true`, `IS_PAPER_ALPACA=true` (paper-api.alpaca.markets). PDT protection + concurrent position cap active. First real-money attempt target: **Apr 24, 2026**.

**Phase 2 — Target: May 6, 2026.** Full live execution. $7k starting equity, $1,500 position size, 2.14% account risk. Brain rental marketplace.

### Phase 1.5 Gate Checklist
- [x] PDT day-trade counter wired (`_check_pdt_guard` — Alpaca `/v2/account` `daytrade_count`)
- [x] Concurrent position cap enforced (`_check_concurrent_positions_guard` — default 2 positions)
- [x] Startup Telegram shows live PDT/position headroom for live accounts
- [x] `LIVE_ORDERS_ENABLED=true` (paper bracket orders firing in Alpaca paper-api)
- [ ] API key swap (ALPACA_API_KEY/SECRET_KEY → live values)
- [ ] Set `IS_PAPER_ALPACA=false`
- [x] Kill switch UI built — sidebar expander "🚨 Kill Switch [PAPER]", two-step ARM → CONFIRM, calls DELETE /v2/orders + DELETE /v2/positions. Updates label to LIVE ⚠️ when live mode is active.

## User Preferences

- **Timezone: ET always** — all times, schedules, and market hours in Eastern Time. Never say UTC.
- Build mode only, concise replies, no "ready for review" sign-offs
- Mobile-friendly communication style
- PDFs + build notes regenerate **every night at 11:59 PM ET** (both via Paper Trader Bot)
- Preserve `compute_buy_sell_pressure`, `classify_day_structure`, `compute_structure_probabilities`, `brain_weights.json` — do not modify

## System Architecture

**Separation of Concerns:** Math/logic in `backend.py`. UI/rendering in `app.py`. Never mix.

**Key Files:**
- `app.py` — Streamlit UI (~25k lines). Filter Funnel ~24970. Trade-by-Trade Log ~11921. Row anchor ~17825. JS pulse inject ~18009.
- `paper_trader_bot.py` — autonomous daily bot (~4400 lines). Main loop, scheduling, all scan/order/EOD/recalibration functions.
- `backend.py` — all math, Supabase helpers, auto-migrations (~15k lines).
- `deploy_server.py` — proxy on 8080, Streamlit on 8501.
- `backfill_context_levels.py` — fills `backtest_context_levels` from history files. Has graceful fallback if `data_quality` column missing.
- `run_sim_backfill.py` — fills `sim_version`/`tiered_sim_version`/`eod_pnl_r` on `backtest_sim_runs`.
- `batch_backtest.py` — runs full backtest simulations, writes all sim columns.
- `nightly_tiered_pnl_refresh.py` — persistent workflow. Fires at 00:05 ET nightly. Calls `run_tiered_pnl_backfill.py --backtest-only`.
- `brain_weights.json` / `brain_weights_historical.json` — blended at runtime. `tcs_thresholds.json` — TCS floors.

**Port Architecture:** deploy_server.py=8080+8501 | edgeiq-old=23411 (baseUrlPath=edgeiq) | api-server=3001 | mockup-sandbox=8081

**IP Preservation:** Three functions in `backend.py` are proprietary and must NEVER be modified:
1. `compute_buy_sell_pressure`
2. `classify_day_structure`
3. `compute_structure_probabilities`

## Paper Trader Bot Schedule (ET)

| Time | Task | Catch-up on restart? |
|---|---|---|
| 9:10 AM | Pre-market gap scan (Alpaca SIP) | Skip — data stale |
| 9:35 AM | Finviz watchlist refresh (morning) | Run immediately (lock file prevents duplicate) |
| 10:47 AM | Morning scan → IB entries → Telegram → Alpaca bracket orders | Run if restarted 10:47–2 PM |
| 11:45 AM | Midday watchlist refresh | Skip — mark done, intraday uses existing list |
| 2:00 PM | Intraday scan | Run if restarted 2–4 PM |
| 4:20 PM | EOD outcome resolution (idempotent) | **Run immediately** (idempotency guard prevents duplication) |
| 4:25 PM | Auto-verify today's predictions (idempotent) | **Run immediately** |
| 4:30 PM | Brain recalibration + build notes update | **Run immediately** |
| 4:35 PM | Divergence alert dispatch | Run with idempotency guard |
| 11:59 PM | PDF exports + build notes update | Runs daily |

**Watchlist lock files:** `/tmp/wl_morning_{ET_date}.lock` and `/tmp/wl_midday_{ET_date}.lock` prevent duplicate Telegram sends on same-day restarts.

## Database Schema (Supabase)

### Tables
- `paper_trades` — live paper/live trade log. Key cols: `ticker`, `trade_date`, `scan_type`, `predicted`, `actual_outcome`, `tcs`, `ib_high`, `ib_low`, `close_price`, `eod_pnl_r`, `tiered_pnl_r`, `pnl_r_sim`, `vwap_at_ib`, `ib_range_pct`, `tcs_floor`, `alpaca_order_id`, `alpaca_qty`, `order_placed_at`, `alpaca_fill_price`, `mae`, `mfe`, `entry_time`, `exit_trigger`, `exit_obs`, `entry_ib_distance`
- `backtest_sim_runs` — backtest results. Key cols: `ticker`, `sim_date`, `scan_type`, `predicted`, `actual_outcome`, `close_price`, `eod_pnl_r`, `tiered_pnl_r`, `pnl_r_sim`, `pnl_pct_sim`, `sim_outcome`, `sim_version`, `tiered_sim_version`, `entry_price_sim`, `stop_price_sim`, `stop_dist_pct`, `target_price_sim`, `gap_pct`, `gap_vs_ib_pct`
- `backtest_context_levels` — context at signal time: `ticker`, `trade_date`, `scan_type`, `vwap_at_signal`, `macd_line/signal/histogram/direction`, `nearest_resistance`, `nearest_support`, `prev_day_high/low`, `data_quality`
- `user_watchlist` — 100-ticker daily watchlist (gap + trend + squeeze screeners)
- `ticker_rankings` — TCS, RVOL, edge_score, predicted_structure, confidence_label
- `trade_journal` — manual trade log with cognitive tags
- `cognitive_delta_log` — per-day follow/skip/override decisions logged by the trader against bot calls. Powers Bot vs You convergence tracker and Cognitive Profile Dimension 7
- `accuracy_tracker` — per-structure accuracy for brain weighting

### Auto-Migrations (run at every app startup via `backend.run_pending_migrations()`)
All column migrations use `ADD COLUMN IF NOT EXISTS` — safe to run repeatedly. Covers:
- `paper_trades`: mae, mfe, entry_time, exit_trigger, exit_obs, entry_ib_distance, close_price, ib_range_pct, vwap_at_ib, tcs_floor, pnl_r_sim
- `backtest_sim_runs`: close_price, tiered_pnl_r, eod_pnl_r, scan_type, sim_outcome, pnl_r_sim, pnl_pct_sim, entry_price_sim, stop_price_sim, stop_dist_pct, target_price_sim, gap_pct, gap_vs_ib_pct, sim_version, tiered_sim_version
- `backtest_context_levels`: data_quality
- `ticker_rankings`: tcs, rvol, edge_score, predicted_structure, confidence_label
- Performance index: `idx_bsr_outcome_tiered_date`

### Manual SQL Required (one-time, run in Supabase SQL Editor)
These are too complex for exec_sql auto-migration:
```sql
-- Materialized views for Ladder tab (nightly refresh targets)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_tiered_pnl_summary AS ...
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_paper_tiered_pnl_summary AS ...
```
(Full SQL is in `backend._ALL_PENDING_MIGRATIONS` string — extract and paste.)

User has run: `ALTER TABLE backtest_context_levels ADD COLUMN IF NOT EXISTS data_quality text` ✅
User has run: materialized view creation SQL ✅ (Apr 18, 2026)

## External Dependencies

- **Alpaca API** — SIP data feed + paper/live bracket order execution
- **Supabase** — PostgreSQL backend with RLS, exec_sql RPC function for auto-migrations
- **Finviz** — three screeners: gap, trend-continuation, short-squeeze → 100 watchlist tickers
- **Streamlit** — web UI framework
- **Plotly** — charting
- **Telegram** — bot alerts (token in env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
- **Pandas / NumPy / PyTZ** — data processing and timezone handling

## Known Open Items (as of Apr 18, 2026)

1. **Materialized views** `mv_tiered_pnl_summary` + `mv_paper_tiered_pnl_summary` — need one-time Supabase SQL paste. Without them the Ladder tab shows empty / nightly refresh logs a warning. (User may have created these Apr 18.)
2. **`IS_PAPER_ALPACA=false`** flip — needs to be done in env vars when going live (Phase 2). Must also swap ALPACA_API_KEY/SECRET_KEY to live credentials.
3. **Kill switch UI** — not yet tested end-to-end.
4. **`app_config` table** — returns 404 on GET (bot logs a warning at startup). Non-blocking.

## Recent Fix Log

- **Apr 18, 2026** — Catch-up logic: EOD/verify/recalibration now actually RUN on restart (not just silently marked done). Build notes now updated at 11:59 PM alongside PDFs.
- **Apr 18, 2026** — 13 missing `backtest_sim_runs` columns added to auto-migration list (`sim_version`, `tiered_sim_version`, `scan_type`, `sim_outcome`, and 9 others). `paper_trades.pnl_r_sim` added.
- **Apr 18, 2026** — `backfill_context_levels.py`: graceful fallback if `data_quality` column absent — retries upsert without it so rows still save.
- **Apr 17, 2026** — Duplicate Telegram watchlist alerts fixed: per-slot daily lock files in `/tmp` keyed to ET date (`wl_morning_YYYY-MM-DD.lock`, `wl_midday_YYYY-MM-DD.lock`).
- **Apr 17, 2026** — Trade-by-Trade Log date range filter (URL params `rp_log_from`/`rp_log_to`) + amber pulse highlight on hash-jumped rows (`#trade-{ticker}-{date}`).
