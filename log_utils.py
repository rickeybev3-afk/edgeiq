"""log_utils.py — shared log-rotation helper.

Single source of truth for the rolling-file rotation logic used by
app.py, backend.py, and calibrate_sp_mult.py.
"""

import os
import sys


# ── Registry of all recognised integer env vars ───────────────────────────────
# Maps env-var name → (default_value, human description).
# Used by validate_env_config() to perform a startup sanity check.
_INT_ENV_REGISTRY: dict[str, tuple[int, str]] = {
    # log_config.py — TCS threshold-history log
    "TCS_HISTORY_MAX_BYTES":          (500 * 1024, "TCS history log max size in bytes"),
    "TCS_HISTORY_BACKUP_COUNT":       (1,           "TCS history log backup count"),
    # log_config.py — backfill run-history log
    "BACKFILL_RUN_HISTORY_MAX_BYTES": (100 * 1024, "backfill run-history log max size in bytes"),
    "BACKFILL_RUN_HISTORY_BACKUP_COUNT": (1,        "backfill run-history log backup count"),
    # log_config.py — backfill pipeline log
    "BACKFILL_LOG_MAX_BYTES":         (500 * 1024, "backfill pipeline log max size in bytes"),
    "BACKFILL_LOG_BACKUP_COUNT":      (1,           "backfill pipeline log backup count"),
    # backend.py — TCS history pruning / scoring
    "TCS_HISTORY_RETENTION_DAYS":     (90,          "days of TCS threshold history to retain"),
    "TCS_BASE_SCORE":                 (65,          "baseline TCS gate used in threshold calibration"),
    # calibrate_sp_mult.py — calibration reset log
    "RESET_LOG_MAX_BYTES":            (100 * 1024, "calibration reset log max size in bytes"),
    "RESET_LOG_BACKUP_COUNT":         (1,           "calibration reset log backup count"),
}


def _parse_int_env(name: str, default: int) -> int:
    """Read *name* from the environment as a positive integer.

    Returns *default* when the variable is absent, empty, non-numeric, or <= 0.
    Warnings for misconfigured values are emitted by ``validate_env_config()``
    rather than here, so that running that single startup pass is the only place
    operators see diagnostics (no duplicated output from module-level imports).
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = int(raw)
        return val if val > 0 else default
    except (TypeError, ValueError):
        return default


def validate_env_config(strict: bool = False) -> list[str]:
    """Check all documented integer env vars for valid values.

    For each variable that is set to a non-numeric or non-positive value a
    WARNING line is printed to stderr naming the variable, its bad value, and
    the default that will be used instead.

    When *strict* is True (or ``--strict`` appears in ``sys.argv``, or the
    ``STRICT_CONFIG_VALIDATION`` env var is set to ``1`` / ``true``) the
    process exits with code 1 if any misconfigured variable is found.

    Returns the list of variable names that had invalid values (empty list
    when everything is fine).
    """
    _strict = (
        strict
        or "--strict" in sys.argv
        or os.environ.get("STRICT_CONFIG_VALIDATION", "").lower() in ("1", "true", "yes")
    )

    bad: list[str] = []
    for name, (default, description) in _INT_ENV_REGISTRY.items():
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            continue
        try:
            val = int(raw)
            if val <= 0:
                print(
                    f"CONFIG WARNING: {name}={raw!r} — {description} must be a positive integer; "
                    f"using default ({default})",
                    file=sys.stderr,
                )
                bad.append(name)
        except (TypeError, ValueError):
            print(
                f"CONFIG WARNING: {name}={raw!r} — {description} is not a valid integer; "
                f"using default ({default})",
                file=sys.stderr,
            )
            bad.append(name)

    if bad and _strict:
        print(
            f"ERROR: Aborting startup — {len(bad)} misconfigured env var(s): {', '.join(bad)}",
            file=sys.stderr,
        )
        sys.exit(1)

    return bad


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
