-- Migration: Adaptive Position Management columns
-- Task: #1783 — Adaptive Position Management toggle
--
-- Run once in Supabase → SQL Editor before setting ADAPTIVE_POSITION_MGMT=1.
-- All columns are backward-compatible: existing rows are unaffected and the
-- toggle can be enabled/disabled at any time without data loss.
--
-- mgmt_mode    : 'fixed' (default bracket held as-is) or 'adaptive' (TP/stop
--                adjusted at 8:30 AM by _pre_open_position_review)
-- tp_adjusted_r: the adjusted take-profit expressed in R units, written when
--                mgmt_mode = 'adaptive' so you can compare outcomes vs fixed

ALTER TABLE paper_trades
  ADD COLUMN IF NOT EXISTS mgmt_mode     VARCHAR DEFAULT 'fixed',
  ADD COLUMN IF NOT EXISTS tp_adjusted_r REAL;
