-- Migration: add close_price column to backtest_sim_runs and paper_trades
-- Run ONCE in Supabase SQL Editor (or via the run_pending_migrations() API call).
--
-- close_price stores the EOD closing price for each trade date.
-- It is required by compute_trade_sim_tiered() to compute eod_pnl_r
-- (the uncapped hold-to-close P&L in R-multiples).
--
-- After applying this migration, re-run run_sim_backfill.py to back-fill
-- eod_pnl_r on all existing historical rows.

ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS close_price NUMERIC;
ALTER TABLE paper_trades       ADD COLUMN IF NOT EXISTS close_price NUMERIC;
