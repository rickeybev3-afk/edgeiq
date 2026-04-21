-- Migration: IB Context Enrichment columns
-- Task: #1782 — IB Context Enrichment toggle
--
-- Run once in Supabase → SQL Editor before setting IB_CONTEXT_ENABLED=1.
-- All columns are nullable so existing rows are unaffected and the toggle
-- can be enabled/disabled at any time without data loss.
--
-- prev_ib_high / prev_ib_low : prior session's Initial Balance extremes
-- pm_range_pct               : pre-market (4–9:30 AM ET) range as % of open
-- ib_vs_prev_ib_pct          : today's IB range / yesterday's IB range × 100

ALTER TABLE paper_trades
  ADD COLUMN IF NOT EXISTS prev_ib_high      REAL,
  ADD COLUMN IF NOT EXISTS prev_ib_low       REAL,
  ADD COLUMN IF NOT EXISTS pm_range_pct      REAL,
  ADD COLUMN IF NOT EXISTS ib_vs_prev_ib_pct REAL;
