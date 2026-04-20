"""Shared calibration-threshold resolution utility.

Used by ``deploy_server.py`` and ``nightly_tiered_pnl_refresh.py`` so that a
threshold rule change only needs to be applied in one place.
"""

import os

_DEFAULT_CALIB_THRESHOLD = 30


def resolve_calib_threshold(screener_key: str) -> int:
    """Return the minimum-trades calibration threshold for *screener_key*.

    Resolution order:
      1. ``CALIB_MIN_TRADES_<SCREENER_KEY>`` env var (positive int required).
         The env-var name is derived by uppercasing *screener_key* and replacing
         hyphens with underscores (e.g. ``gap-down`` → ``CALIB_MIN_TRADES_GAP_DOWN``).
      2. ``SQUEEZE_CALIB_MIN_TRADES`` env var for the *squeeze* screener only
         (legacy alias kept for backward-compatibility).
      3. The module-level default of 30.
    """
    upper = screener_key.upper().replace("-", "_")
    env_key = f"CALIB_MIN_TRADES_{upper}"
    raw = os.environ.get(env_key, "").strip()
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    if screener_key == "squeeze":
        legacy = os.environ.get("SQUEEZE_CALIB_MIN_TRADES", "").strip()
        if legacy:
            try:
                v = int(legacy)
                if v > 0:
                    return v
            except ValueError:
                pass
    return _DEFAULT_CALIB_THRESHOLD
