-- Migration: add data_quality column to backtest_context_levels
-- Run ONCE in Supabase SQL Editor (or via the run_pending_migrations() API call).
--
-- data_quality stores whether intraday bars were available when the row was
-- backfilled.  Values:
--   'ok'      — bars were fetched successfully; all context columns are populated
--   'no_bars' — no intraday bars were returned; context columns are NULL
--
-- Defaults to 'ok' so every existing row keeps a valid value after the migration.
-- After applying this migration, re-run backfill_context_levels.py to stamp
-- 'no_bars' on rows that had no intraday data.

ALTER TABLE backtest_context_levels ADD COLUMN IF NOT EXISTS data_quality TEXT DEFAULT 'ok';
