-- Migration: add rvol_mult column to paper_trades
-- Run ONCE in Supabase SQL Editor (or via the run_pending_migrations() API call).
--
-- rvol_mult = RVOL size bonus multiplier applied at order time by the live bot.
--             1.00 = no bonus (RVOL < 2.0 or not available)
--             1.25 = moderate boost (RVOL 2.0–2.99)
--             1.50 = high boost (RVOL ≥ 3.0)
--
-- Default of 1.0 preserves backward-compatibility for all historical rows
-- (pre-migration rows were effectively un-boosted and should be treated as 1×).

ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS rvol_mult REAL DEFAULT 1.0;
