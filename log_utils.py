"""log_utils.py — shared log-rotation helper.

Single source of truth for the rolling-file rotation logic used by
app.py, backend.py, and calibrate_sp_mult.py.
"""

import os
import sys


# ── Registry of required/optional string env vars ─────────────────────────────
# Maps env-var name → (required: bool, human description).
# validate_env_config() emits a CONFIG WARNING for every required var that is
# absent or empty, so operators see the problem immediately at startup instead
# of getting a cryptic error on the first request.
_STRING_ENV_REGISTRY: dict[str, tuple[bool, str]] = {
    # Supabase connection
    "SUPABASE_URL":           (True,  "Supabase project API URL"),
    # Alpaca brokerage credentials
    "ALPACA_API_KEY":         (True,  "Alpaca brokerage API key"),
    "ALPACA_SECRET_KEY":      (True,  "Alpaca brokerage secret key"),
    # Optional — application-level secrets / integrations
    "DASHBOARD_WRITE_SECRET": (False, "shared secret for trade-log write requests"),
    "ALERT_WEBHOOK_URL":      (False, "webhook URL for Slack/webhook alert notifications"),
    "TELEGRAM_BOT_TOKEN":     (False, "Telegram bot token for alert notifications"),
    "TELEGRAM_CHAT_ID":       (False, "Telegram chat ID to receive alert notifications"),
    "GRID_SEARCH_NOTIFY_ON":  (False, "comma-separated event types that trigger a grid-search notification: 'success', 'failure', or 'success,failure' (default: both)"),
}

# Groups where at least one member must be non-empty (acts as a single required
# credential).  Each entry is (tuple-of-var-names, synthetic-label, description-for-warning).
# The synthetic label is used in the bad-var list and strict-mode summary so
# operators see the full group name rather than just the first alternative.
_STRING_ENV_REQUIRED_ONE_OF: list[tuple[tuple[str, ...], str, str]] = [
    (
        ("SUPABASE_KEY", "SUPABASE_ANON_KEY", "VITE_SUPABASE_ANON_KEY"),
        "SUPABASE_KEY[_ANON_KEY|VITE_SUPABASE_ANON_KEY]",
        "Supabase anon/service-role key — set at least one of: "
        "SUPABASE_KEY, SUPABASE_ANON_KEY, VITE_SUPABASE_ANON_KEY",
    ),
]


# ── Registry of all recognised integer env vars ───────────────────────────────
# Maps env-var name → (default_value, human description).
# Used by validate_env_config() to perform a startup sanity check.
# Cached result of the last validate_env_config() call.  Populated at startup
# and readable via get_config_issues() without re-running the validation.
_CONFIG_ISSUES: list[str] = []
# Snapshot of the raw env-var values captured at validation time so that
# get_config_issues() always reports the exact startup-state values even if
# the process environment changes afterwards.
_CONFIG_ISSUE_SNAPSHOTS: dict[str, str] = {}

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


def get_config_issues() -> list[dict]:
    """Return details about every misconfigured env var found at startup.

    Each entry is a dict with keys:
      ``name``        — environment variable name
      ``bad_value``   — the raw string that was set at validation time (snapshot)
      ``default``     — the integer default that will be used instead
      ``description`` — human-readable description of the variable
    Returns an empty list when all variables are correctly configured.

    The ``bad_value`` is captured once during ``validate_env_config()`` so this
    function always reports the exact startup-state value even if os.environ
    changes afterwards.
    """
    result = []
    for name in _CONFIG_ISSUES:
        default, description = _INT_ENV_REGISTRY.get(name, (None, name))
        result.append(
            {
                "name": name,
                "bad_value": _CONFIG_ISSUE_SNAPSHOTS.get(name, ""),
                "default": default,
                "description": description,
            }
        )
    return result


def validate_env_config(strict: bool = False) -> list[str]:
    """Check all documented env vars for valid values at startup.

    **String vars** — emits a CONFIG WARNING for every *required* variable
    that is absent or empty, so operators see the problem before any request
    is processed.  Optional variables that are absent produce no output.

    **Integer vars** — for each variable that is set to a non-numeric or
    non-positive value a WARNING line is printed naming the variable, its bad
    value, and the default that will be used instead.

    When *strict* is True (or ``--strict`` appears in ``sys.argv``, or the
    ``STRICT_CONFIG_VALIDATION`` env var is set to ``1`` / ``true``) the
    process exits with code 1 if any misconfigured variable is found.

    Returns the list of variable names that had invalid/missing values (empty
    list when everything is fine).
    """
    _strict = (
        strict
        or "--strict" in sys.argv
        or os.environ.get("STRICT_CONFIG_VALIDATION", "").lower() in ("1", "true", "yes")
    )

    global _CONFIG_ISSUES, _CONFIG_ISSUE_SNAPSHOTS

    bad: list[str] = []
    # Snapshot the bad values at validation time so get_config_issues() always
    # reports the exact startup-state values, even if os.environ changes later.
    bad_snapshots: dict[str, str] = {}

    # ── String env var checks ─────────────────────────────────────────────────
    for name, (required, description) in _STRING_ENV_REGISTRY.items():
        value = os.environ.get(name, "").strip()
        if not value:
            if required:
                print(
                    f"CONFIG WARNING: {name} is not set — {description} is required; "
                    "server may fail on first use",
                    file=sys.stderr,
                )
                bad.append(name)

    for group_vars, label, description in _STRING_ENV_REQUIRED_ONE_OF:
        if not any(os.environ.get(v, "").strip() for v in group_vars):
            print(
                f"CONFIG WARNING: {description}",
                file=sys.stderr,
            )
            bad.append(label)

    # ── Integer env var checks ────────────────────────────────────────────────
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
                bad_snapshots[name] = raw
        except (TypeError, ValueError):
            print(
                f"CONFIG WARNING: {name}={raw!r} — {description} is not a valid integer; "
                f"using default ({default})",
                file=sys.stderr,
            )
            bad.append(name)
            bad_snapshots[name] = raw

    _CONFIG_ISSUES = bad[:]
    _CONFIG_ISSUE_SNAPSHOTS = bad_snapshots

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
