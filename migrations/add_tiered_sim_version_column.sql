-- Migration: add tiered_sim_version column to backtest_sim_runs and paper_trades
-- Run ONCE in Supabase SQL Editor (or via the run_pending_migrations() API call).
--
-- tiered_sim_version stores a short formula-version string (e.g. "v1") stamped
-- on each row by _sim_patch() whenever eod_pnl_r is written, so that
-- --skip-existing can detect stale eod_pnl_r values after a
-- compute_trade_sim_tiered() logic change and automatically re-process them.
--
-- Without this column, changing the EOD hold P&L formula had no way to
-- invalidate existing eod_pnl_r values — rows looked fully-populated and were
-- silently skipped by --skip-existing even though their values were stale.
-- With tiered_sim_version the staleness check becomes:
--   re-process if eod_pnl_r IS NULL
--              OR tiered_sim_version IS NULL
--              OR tiered_sim_version != <current TIERED_SIM_VERSION>
--
-- After applying this migration, run run_sim_backfill.py (without
-- --skip-existing) to stamp tiered_sim_version on all existing rows.

ALTER TABLE backtest_sim_runs ADD COLUMN IF NOT EXISTS tiered_sim_version TEXT;
ALTER TABLE paper_trades       ADD COLUMN IF NOT EXISTS tiered_sim_version TEXT;
