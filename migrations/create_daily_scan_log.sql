-- Migration: create daily_scan_log table for Finviz scanner funnel persistence
-- Run ONCE in Supabase SQL Editor.
--
-- daily_scan_log stores every ticker the bot's Finviz screener finds during
-- its morning (9:35 AM ET) and midday (11:45 AM ET) watchlist refresh passes.
-- Each row records one ticker on one scan date with its screener pass
-- (gap | trend | squeeze) and which slot fired it (morning | midday).
--
-- A ticker appearing in multiple passes is stored once (first-pass priority:
-- gap > trend > squeeze). The save_daily_scan_log() helper in backend.py
-- deletes existing rows for (scan_date, slot) before inserting fresh results
-- so re-runs don't duplicate data.
--
-- Used by:
--   paper_trader_bot.py — writes after every watchlist_refresh() call
--   app.py             — "Today's Scanner Funnel" panel in the paper trades tab
--   backend.py         — load_daily_scan_log(scan_date) helper

CREATE TABLE IF NOT EXISTS daily_scan_log (
    id            BIGSERIAL   PRIMARY KEY,
    scan_date     DATE        NOT NULL,
    ticker        VARCHAR(10) NOT NULL,
    screener_pass VARCHAR(20) NOT NULL,
    slot          VARCHAR(10) NOT NULL DEFAULT 'morning',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS daily_scan_log_date_idx
    ON daily_scan_log (scan_date DESC);
