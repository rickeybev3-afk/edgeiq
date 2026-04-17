-- Migration: create app_config table for durable app-level configuration storage
-- Run ONCE in Supabase SQL Editor.
--
-- app_config stores arbitrary application configuration blobs keyed by a
-- plain-text identifier.  The first consumer is TCS alert preferences
-- (key = 'tcs_alert_config'), which were previously kept only in the local
-- file tcs_alert_config.json and lost on container restart or redeployment.
--
-- After applying this migration, the next time alert preferences are saved
-- from the UI they will be written to this table and will survive server
-- restarts automatically.  Reads fall back to tcs_alert_config.json when
-- the table is absent or when Supabase is unavailable.

CREATE TABLE IF NOT EXISTS app_config (
    key        TEXT        PRIMARY KEY,
    value      JSONB       NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
