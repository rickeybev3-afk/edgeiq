-- Migration: add nearest_resistance and nearest_support columns to paper_trades
-- Run ONCE in Supabase SQL Editor (or via the run_pending_migrations() API call).
--
-- nearest_resistance = closest key level (prev_day_high, prev_day_low, VWAP) above the IB break price
-- nearest_support    = closest key level (prev_day_high, prev_day_low, VWAP) below the IB break price
--
-- Both are computed by log_context_levels() at scan time and patched onto the row
-- so that _monitor_trailing_stops() can read them directly without a secondary
-- lookup against backtest_context_levels (which is only populated by the nightly
-- backfill script and returns NULL for today's live trades).
--
-- Resolves the bug where v6 trail-tightening always defaulted to 1R on live
-- trades placed today because backtest_context_levels had no intraday entry yet.

ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS nearest_resistance REAL;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS nearest_support    REAL;
