"""
backfill_utils.py
─────────────────
Shared utilities for all backfill scripts.

History / health file
─────────────────────
Each backfill script appends a small health record to a JSON file after every
run so the dashboard can show when each script last ran and how it went.

The file path is resolved once, in priority order:

  1. BACKFILL_HISTORY_PATH env var (operator-supplied absolute path)
  2. <script_dir>/backfill_history.json   (stable default — survives restarts)

If a legacy /tmp/backfill_history.json exists and the resolved path does not,
the file is migrated automatically so historical data is not lost.
"""

import os
import json
import shutil
import datetime
import logging

_log = logging.getLogger(__name__)

_LEGACY_PATH = '/tmp/backfill_history.json'


def get_history_path() -> str:
    """Return the resolved stable path for the shared backfill history file."""
    _default = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backfill_history.json')
    return os.environ.get('BACKFILL_HISTORY_PATH', _default)


def _migrate_legacy(history_path: str, logger=None) -> None:
    """Copy /tmp/backfill_history.json → history_path if needed (one-time migration)."""
    log = logger or _log
    if not os.path.exists(history_path) and os.path.exists(_LEGACY_PATH):
        try:
            shutil.copy2(_LEGACY_PATH, history_path)
            log.info(f'Migrated backfill history from {_LEGACY_PATH} to {history_path}')
        except Exception as exc:
            log.warning(f'Could not migrate backfill history from /tmp: {exc}')


def append_backfill_history(script: str, health: dict, logger=None) -> None:
    """Append *health* to the shared backfill history file.

    Parameters
    ----------
    script:
        Short identifier for the calling script, e.g. ``"backfill_close_prices"``.
        Stored in the record so the dashboard can distinguish runs.
    health:
        Arbitrary dict describing the outcome of this run.  A ``completed_at``
        key (ISO-8601 string) and a ``script`` key are added automatically if
        not already present.
    logger:
        Optional :class:`logging.Logger` to use for info/warning messages.
        Falls back to the module-level logger when not supplied.
    """
    log = logger or _log
    history_path = get_history_path()

    record = {'script': script, 'completed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    record.update(health)

    try:
        os.makedirs(os.path.dirname(os.path.abspath(history_path)), exist_ok=True)
        _migrate_legacy(history_path, logger=log)

        try:
            with open(history_path) as fh:
                history = json.load(fh)
            if not isinstance(history, list):
                history = []
        except FileNotFoundError:
            history = []
        except json.JSONDecodeError:
            log.warning('backfill_history.json was corrupt — resetting history')
            history = []

        history.append(record)

        # Keep the last 10 records per script so a frequently-running script
        # cannot crowd out the most-recent entry from a rarely-run one.
        # Walk newest-first so we can count per-script and then reverse back
        # to restore global chronological order for consumers that rely on
        # history[-1] being the latest entry.
        script_counts: dict[str, int] = {}
        kept: list = []
        for entry in reversed(history):
            if not isinstance(entry, dict):
                continue
            key = entry.get('script', '')
            script_counts[key] = script_counts.get(key, 0) + 1
            if script_counts[key] <= 10:
                kept.append(entry)
        history = list(reversed(kept))

        with open(history_path, 'w') as fh:
            json.dump(history, fh)

        log.info(f'Backfill history written to {history_path}')
    except Exception as exc:
        log.warning(f'Could not write backfill history file: {exc}')
