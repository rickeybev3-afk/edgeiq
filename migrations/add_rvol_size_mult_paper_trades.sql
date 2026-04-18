-- Migration: add rvol_size_mult column to paper_trades
-- Run ONCE in Supabase SQL Editor (or via the run_pending_migrations() API call).
--
-- rvol_size_mult = the Relative Volume value that was present when the live order
--                 was placed.  NULL means RVOL data was unavailable at order time
--                 (e.g. new ticker or data gap).  Stored as NUMERIC so it can be
--                 sorted and filtered without casting.
--
-- Populated by:
--   paper_trader_bot.py  — written alongside alpaca_order_id in the metadata patch
--   run_sim_backfill.py  — can back-fill historical paper_trades rows where rvol
--                          IS NOT NULL but rvol_size_mult IS NULL.

ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS rvol_size_mult NUMERIC;
