"""Central configuration for log-rotation thresholds.

All rotation constants that were previously scattered across backend.py and
app.py are defined here.  Import from this module instead of defining the
values inline so that operators have a single place to audit and tune them.
"""

from log_utils import _parse_int_env

# ── TCS threshold-history log (backend.py) ────────────────────────────────────
_TCS_HISTORY_MAX_BYTES  = _parse_int_env("TCS_HISTORY_MAX_BYTES", 500 * 1024)  # rotate at 500 KB; override via env var
_TCS_HISTORY_BACKUP_COUNT = _parse_int_env("TCS_HISTORY_BACKUP_COUNT", 1)       # keep one .1 backup; override via env var

# ── Backfill run-history log (app.py) ─────────────────────────────────────────
_BACKFILL_RUN_HISTORY_MAX_BYTES    = _parse_int_env("BACKFILL_RUN_HISTORY_MAX_BYTES", 100 * 1024)  # rotate at 100 KB; override via env var
_BACKFILL_RUN_HISTORY_BACKUP_COUNT = _parse_int_env("BACKFILL_RUN_HISTORY_BACKUP_COUNT", 1)         # keep one .1 backup; override via env var

# ── Backfill pipeline log (app.py) ────────────────────────────────────────────
_BACKFILL_LOG_MAX_BYTES    = _parse_int_env("BACKFILL_LOG_MAX_BYTES", 500 * 1024)  # rotate pipeline log at 500 KB; override via env var
_BACKFILL_LOG_BACKUP_COUNT = _parse_int_env("BACKFILL_LOG_BACKUP_COUNT", 1)         # keep one .1 backup; override via env var
