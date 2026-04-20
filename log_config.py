"""Central configuration for log-rotation thresholds and file paths.

All rotation constants and file paths that were previously scattered across
backend.py, app.py, and calibrate_sp_mult.py are defined here.  Import from
this module instead of defining the values inline so that operators have a
single place to audit and tune them.
"""

import os

from log_utils import _parse_int_env

_HERE = os.path.dirname(os.path.abspath(__file__))

# ── Calibration-reset log (calibrate_sp_mult.py) ──────────────────────────────
_RESET_LOG_PATH = os.environ.get(
    "RESET_LOG_PATH",
    os.path.join(_HERE, "calibration_resets.log"),
)
_RESET_LOG_MAX_BYTES  = _parse_int_env("RESET_LOG_MAX_BYTES", 100 * 1024)   # rotate at 100 KB; override via env var
_RESET_LOG_BACKUP_COUNT = _parse_int_env("RESET_LOG_BACKUP_COUNT", 1)        # keep one .1 backup; override via env var

# ── TCS threshold-history log (backend.py) ────────────────────────────────────
_TCS_HISTORY_LOG_PATH = os.environ.get(
    "TCS_HISTORY_LOG_PATH",
    "tcs_threshold_history.jsonl",
)
_TCS_HISTORY_MAX_BYTES  = _parse_int_env("TCS_HISTORY_MAX_BYTES", 500 * 1024)  # rotate at 500 KB; override via env var
_TCS_HISTORY_BACKUP_COUNT = _parse_int_env("TCS_HISTORY_BACKUP_COUNT", 1)       # keep one .1 backup; override via env var

# ── Backfill run-history log (app.py) ─────────────────────────────────────────
_BACKFILL_RUN_HISTORY_LOG_PATH = os.environ.get(
    "BACKFILL_RUN_HISTORY_LOG_PATH",
    os.path.join(_HERE, "backfill_run_history.log"),
)
_BACKFILL_RUN_HISTORY_MAX_BYTES    = _parse_int_env("BACKFILL_RUN_HISTORY_MAX_BYTES", 100 * 1024)  # rotate at 100 KB; override via env var
_BACKFILL_RUN_HISTORY_BACKUP_COUNT = _parse_int_env("BACKFILL_RUN_HISTORY_BACKUP_COUNT", 1)         # keep one .1 backup; override via env var

# ── Backfill pipeline log (app.py) ────────────────────────────────────────────
_BACKFILL_LOG_PATH = os.environ.get(
    "BACKFILL_LOG_PATH",
    "/tmp/backfill_pipeline.log",
)
_BACKFILL_LOG_MAX_BYTES    = _parse_int_env("BACKFILL_LOG_MAX_BYTES", 500 * 1024)  # rotate pipeline log at 500 KB; override via env var
_BACKFILL_LOG_BACKUP_COUNT = _parse_int_env("BACKFILL_LOG_BACKUP_COUNT", 1)         # keep one .1 backup; override via env var
