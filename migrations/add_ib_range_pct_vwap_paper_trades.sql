-- Migration: add ib_range_pct and vwap_at_ib columns to paper_trades
-- Run ONCE in Supabase SQL Editor (or via the run_pending_migrations() API call).
--
-- ib_range_pct  = (ib_high - ib_low) / open_price * 100
--                 Computed at insert time in log_paper_trades() so the dashboard
--                 can show which setups pass the <10% IB-width quality gate.
--
-- vwap_at_ib    = VWAP of 5-min bars up to IB close (09:35 morning / 10:47 intraday)
--                 Patched onto each row by log_context_levels() after the initial insert.
--
-- Both columns are already present in backtest_sim_runs; this migration brings
-- paper_trades into parity so live-mode filter validation works without
-- recalculating values from raw bars.

ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS ib_range_pct REAL;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS vwap_at_ib   REAL;
