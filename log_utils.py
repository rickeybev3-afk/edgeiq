"""log_utils.py — shared log-rotation helper.

Single source of truth for the rolling-file rotation logic used by
app.py, backend.py, and calibrate_sp_mult.py.
"""

import os
import sys


def _rotate_log(path: str, max_bytes: int, backup_count: int = 1) -> None:
    """Roll over *path* when it exceeds *max_bytes*.

    Keeps *backup_count* rotated files (e.g. foo.log.1, foo.log.2 …).
    Older backups beyond that count are deleted automatically.
    A warning is printed to stderr if the rename fails so no data is
    silently lost.
    """
    try:
        if not os.path.exists(path) or os.path.getsize(path) < max_bytes:
            return
        for idx in range(backup_count, 0, -1):
            src = f"{path}.{idx}"
            dst = f"{path}.{idx + 1}" if idx < backup_count else None
            if dst is not None and os.path.exists(src):
                try:
                    os.remove(dst)
                except OSError:
                    pass
                os.rename(src, dst)
            elif dst is None and os.path.exists(src):
                try:
                    os.remove(src)
                except OSError:
                    pass
        backup = f"{path}.1"
        try:
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(path, backup)
        except OSError as exc:
            print(f"WARNING: could not rotate log {path} — {exc}", file=sys.stderr)
    except OSError as exc:
        print(f"WARNING: could not check log size {path} — {exc}", file=sys.stderr)
