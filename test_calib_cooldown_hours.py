"""Unit tests for _get_calib_cooldown_hours() in nightly_tiered_pnl_refresh.py.

Covers:
  - Unset env var returns the 23-hour default.
  - Valid positive-integer override returns that value.
  - Non-numeric string (e.g. "23h") returns the default and emits a warning.
  - Zero returns the default and emits a warning.
  - Negative integer returns the default and emits a warning.

Run with:  python test_calib_cooldown_hours.py
"""
import importlib.util
import os
import sys
import unittest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Load nightly_tiered_pnl_refresh without triggering its __main__ block.
# The module uses only stdlib, so no heavy stubs are needed.
# ---------------------------------------------------------------------------

def _load_module():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "nightly_tiered_pnl_refresh.py")
    spec = importlib.util.spec_from_file_location("nightly_tiered_pnl_refresh", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("nightly_tiered_pnl_refresh", mod)
    spec.loader.exec_module(mod)
    return mod


_nightly = _load_module()
_get_calib_cooldown_hours = _nightly._get_calib_cooldown_hours
_DEFAULT = _nightly._DEFAULT_CALIB_COOLDOWN_HOURS  # 23


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env_for(screener_key: str, value: str) -> dict:
    """Build the env-var dict that _get_calib_cooldown_hours looks up."""
    env_key = f"CALIB_COOLDOWN_HOURS_{screener_key.upper()}"
    return {env_key: value}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetCalibCooldownHours(unittest.TestCase):

    def test_unset_returns_default(self):
        with patch.dict(os.environ, {}, clear=False):
            env_key = "CALIB_COOLDOWN_HOURS_SQUEEZE"
            os.environ.pop(env_key, None)
            result = _get_calib_cooldown_hours("squeeze")
        self.assertEqual(result, _DEFAULT)

    def test_valid_override_returns_value(self):
        with patch.dict(os.environ, _env_for("squeeze", "8")):
            result = _get_calib_cooldown_hours("squeeze")
        self.assertEqual(result, 8)

    def test_valid_override_large_value(self):
        with patch.dict(os.environ, _env_for("momentum", "48")):
            result = _get_calib_cooldown_hours("momentum")
        self.assertEqual(result, 48)

    def test_invalid_string_returns_default_and_warns(self):
        with patch.dict(os.environ, _env_for("squeeze", "23h")):
            with self.assertLogs("nightly_tiered_pnl", level="WARNING") as cm:
                result = _get_calib_cooldown_hours("squeeze")
        self.assertEqual(result, _DEFAULT)
        self.assertTrue(
            any("23h" in line for line in cm.output),
            "Expected the bad value to appear in the warning log.",
        )

    def test_zero_returns_default_and_warns(self):
        with patch.dict(os.environ, _env_for("squeeze", "0")):
            with self.assertLogs("nightly_tiered_pnl", level="WARNING") as cm:
                result = _get_calib_cooldown_hours("squeeze")
        self.assertEqual(result, _DEFAULT)
        self.assertTrue(any("0" in line for line in cm.output))

    def test_negative_returns_default_and_warns(self):
        with patch.dict(os.environ, _env_for("squeeze", "-5")):
            with self.assertLogs("nightly_tiered_pnl", level="WARNING") as cm:
                result = _get_calib_cooldown_hours("squeeze")
        self.assertEqual(result, _DEFAULT)
        self.assertTrue(any("-5" in line for line in cm.output))

    def test_whitespace_only_returns_default(self):
        with patch.dict(os.environ, _env_for("squeeze", "   ")):
            result = _get_calib_cooldown_hours("squeeze")
        self.assertEqual(result, _DEFAULT)

    def test_key_is_uppercased(self):
        with patch.dict(os.environ, _env_for("SQUEEZE", "12")):
            result = _get_calib_cooldown_hours("squeeze")
        self.assertEqual(result, 12)

    def test_hyphen_in_key_converted_to_underscore(self):
        env = {"CALIB_COOLDOWN_HOURS_MY_SCREENER": "6"}
        with patch.dict(os.environ, env):
            result = _get_calib_cooldown_hours("my-screener")
        self.assertEqual(result, 6)


if __name__ == "__main__":
    unittest.main()
