-- Migration: add sim_version column to backtest_sim_runs and paper_trades
-- Run ONCE in Supabase SQL Editor (or via the run_pending_migrations() API call).
--
-- sim_version stores a short formula-version string (e.g. "v1") stamped on
-- each row by _sim_patch() so that --skip-existing can detect stale rows
-- after a compute_trade_sim() logic change and automatically re-process them.
--
-- Before this column existed, --skip-existing only skipped rows where both
-- sim_outcome and eod_pnl_r were populated, requiring operators to manually
-- omit the flag after every formula update.  With sim_version, the check is:
--   skip if sim_outcome IS NOT NULL
--          AND eod_pnl_r IS NOT NULL
--          AND sim_version = <current SIM_VERSION>
--
-- After applying this migration, run run_sim_backfill.py (without
-- --skip-existing) to stamp sim_version on all existing rows.

ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS sim_version TEXT;
ALTER TABLE paper_trades       ADD COLUMN IF NOT EXISTS sim_version TEXT;
