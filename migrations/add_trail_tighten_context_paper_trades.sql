-- Migration: persist trail-tightening context on paper_trades rows
-- Run ONCE in Supabase SQL Editor (or via the run_pending_migrations() API call).
--
-- When _monitor_trailing_stops() tightens the trail to 0.5R because the price
-- is running into an S/R wall, these columns capture *why* it tightened so the
-- reason survives a bot restart and can appear in subsequent close-out alerts.
--
-- trail_activated   — TRUE once a trailing stop has been placed for this trade.
--                     Used on restart to rebuild _TRAILING_STOP_ACTIVATED without
--                     requiring the bot to be running continuously.
-- trail_sr_level    — The S/R price level (resistance or support) that was within
--                     0.3R of the current price when T1 was hit, triggering the
--                     tighter 0.5R trail.  NULL when the default 1R trail is used.
-- trail_sr_distance — Dollar distance between current price and trail_sr_level at
--                     the moment of activation (informational, for analysis).
-- trail_sr_source   — Where the S/R level came from: "paper_trades" (columns
--                     populated at scan time) or "backtest_context_levels" (nightly
--                     backfill).  NULL when no tightening occurred.
-- trail_size_r      — Human-readable trail size used: "0.5R" or "1R".
--                     Stored so post-trade analysis never has to re-derive it.

ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trail_activated  BOOLEAN DEFAULT FALSE;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trail_sr_level   REAL;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trail_sr_distance REAL;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trail_sr_source  TEXT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trail_size_r     TEXT;
