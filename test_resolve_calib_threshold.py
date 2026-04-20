"""Unit tests for _resolve_calib_threshold() in deploy_server.py.

Covers:
  - Returns the env-var value when CALIB_MIN_TRADES_<KEY> is set to a valid
    positive integer.
  - Falls back to 30 when the primary env var is absent.
  - Falls back to 30 when the primary env var is invalid (zero, negative,
    non-numeric, whitespace-only).
  - For the 'squeeze' key: the SQUEEZE_CALIB_MIN_TRADES legacy alias is
    consulted when the primary var is absent or invalid.
  - For other keys: the legacy alias is NOT consulted.
  - Hyphens in the key are converted to underscores when building the env-var
    name.

Run with:  python test_resolve_calib_threshold.py
"""
import importlib.util
import os
import sys
import unittest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Load deploy_server without triggering its __main__ block.
# The module uses only stdlib, so no heavy stubs are needed.
# ---------------------------------------------------------------------------

def _load_module():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "deploy_server.py")
    spec = importlib.util.spec_from_file_location("deploy_server", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("deploy_server", mod)
    spec.loader.exec_module(mod)
    return mod


_server = _load_module()
_resolve_calib_threshold = _server._resolve_calib_threshold
_DEFAULT = _server._DEFAULT_CALIB_THRESHOLD  # 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _primary_env(screener_key: str, value: str) -> dict:
    """Build the primary env-var dict for the given screener key."""
    upper = screener_key.upper().replace("-", "_")
    return {f"CALIB_MIN_TRADES_{upper}": value}


_LEGACY_KEY = "SQUEEZE_CALIB_MIN_TRADES"

_ALL_CALIB_KEYS = [
    "CALIB_MIN_TRADES_SQUEEZE",
    _LEGACY_KEY,
    "CALIB_MIN_TRADES_MOMENTUM",
    "CALIB_MIN_TRADES_MY_SCREENER",
]


def _clean_env() -> dict:
    """Return a patch dict that removes all relevant env vars."""
    return {k: "" for k in _ALL_CALIB_KEYS}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResolveCalibThreshold(unittest.TestCase):

    # --- primary env var: valid values ---

    def test_primary_var_valid_positive_int(self):
        with patch.dict(os.environ, _primary_env("squeeze", "50")):
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, 50)

    def test_primary_var_valid_for_non_squeeze_key(self):
        with patch.dict(os.environ, _primary_env("momentum", "25")):
            result = _resolve_calib_threshold("momentum")
        self.assertEqual(result, 25)

    def test_primary_var_value_of_one_is_valid(self):
        with patch.dict(os.environ, _primary_env("squeeze", "1")):
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, 1)

    # --- primary env var: invalid / absent → fallback ---

    def test_absent_primary_var_returns_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CALIB_MIN_TRADES_SQUEEZE", None)
            os.environ.pop(_LEGACY_KEY, None)
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, _DEFAULT)

    def test_zero_primary_var_returns_default(self):
        with patch.dict(os.environ, {**_primary_env("squeeze", "0"),
                                      _LEGACY_KEY: ""}):
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, _DEFAULT)

    def test_negative_primary_var_returns_default(self):
        with patch.dict(os.environ, {**_primary_env("squeeze", "-10"),
                                      _LEGACY_KEY: ""}):
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, _DEFAULT)

    def test_non_numeric_primary_var_returns_default(self):
        with patch.dict(os.environ, {**_primary_env("squeeze", "abc"),
                                      _LEGACY_KEY: ""}):
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, _DEFAULT)

    def test_whitespace_only_primary_var_returns_default(self):
        with patch.dict(os.environ, {**_primary_env("squeeze", "   "),
                                      _LEGACY_KEY: ""}):
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, _DEFAULT)

    # --- squeeze: legacy alias SQUEEZE_CALIB_MIN_TRADES ---

    def test_squeeze_legacy_alias_used_when_primary_absent(self):
        env = {_LEGACY_KEY: "20"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("CALIB_MIN_TRADES_SQUEEZE", None)
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, 20)

    def test_squeeze_primary_takes_precedence_over_legacy(self):
        env = {**_primary_env("squeeze", "40"), _LEGACY_KEY: "99"}
        with patch.dict(os.environ, env):
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, 40)

    def test_squeeze_legacy_invalid_falls_back_to_default(self):
        env = {_LEGACY_KEY: "0"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("CALIB_MIN_TRADES_SQUEEZE", None)
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, _DEFAULT)

    def test_squeeze_legacy_non_numeric_falls_back_to_default(self):
        env = {_LEGACY_KEY: "bad"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("CALIB_MIN_TRADES_SQUEEZE", None)
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, _DEFAULT)

    def test_squeeze_legacy_used_when_primary_is_invalid(self):
        env = {**_primary_env("squeeze", "0"), _LEGACY_KEY: "20"}
        with patch.dict(os.environ, env):
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, 20)

    def test_squeeze_legacy_used_when_primary_is_non_numeric(self):
        env = {**_primary_env("squeeze", "abc"), _LEGACY_KEY: "15"}
        with patch.dict(os.environ, env):
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, 15)

    # --- non-squeeze keys: legacy alias NOT consulted ---

    def test_non_squeeze_ignores_legacy_alias(self):
        env = {_LEGACY_KEY: "99"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("CALIB_MIN_TRADES_MOMENTUM", None)
            result = _resolve_calib_threshold("momentum")
        self.assertEqual(result, _DEFAULT)

    def test_another_non_squeeze_key_ignores_legacy_alias(self):
        env = {_LEGACY_KEY: "77"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("CALIB_MIN_TRADES_TREND", None)
            result = _resolve_calib_threshold("trend")
        self.assertEqual(result, _DEFAULT)

    # --- key normalisation ---

    def test_key_with_hyphen_maps_to_underscore_env_var(self):
        env = {"CALIB_MIN_TRADES_MY_SCREENER": "15"}
        with patch.dict(os.environ, env):
            result = _resolve_calib_threshold("my-screener")
        self.assertEqual(result, 15)

    def test_key_is_uppercased(self):
        with patch.dict(os.environ, _primary_env("SQUEEZE", "35")):
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, 35)

    # --- cross-key isolation: multiple screener vars set simultaneously ---

    def test_momentum_var_does_not_influence_squeeze_resolution(self):
        """CALIB_MIN_TRADES_MOMENTUM must not bleed into squeeze resolution."""
        env = {
            "CALIB_MIN_TRADES_MOMENTUM": "77",
            "CALIB_MIN_TRADES_SQUEEZE": "42",
        }
        with patch.dict(os.environ, env):
            squeeze_result = _resolve_calib_threshold("squeeze")
            momentum_result = _resolve_calib_threshold("momentum")
        self.assertEqual(squeeze_result, 42)
        self.assertEqual(momentum_result, 77)

    def test_squeeze_var_does_not_influence_momentum_resolution(self):
        """CALIB_MIN_TRADES_SQUEEZE must not bleed into momentum resolution."""
        env = {
            "CALIB_MIN_TRADES_SQUEEZE": "55",
            "CALIB_MIN_TRADES_MOMENTUM": "10",
        }
        with patch.dict(os.environ, env):
            momentum_result = _resolve_calib_threshold("momentum")
            squeeze_result = _resolve_calib_threshold("squeeze")
        self.assertEqual(momentum_result, 10)
        self.assertEqual(squeeze_result, 55)

    def test_each_screener_key_returns_only_its_own_value(self):
        """Three distinct keys set at once each resolve independently."""
        env = {
            "CALIB_MIN_TRADES_SQUEEZE": "11",
            "CALIB_MIN_TRADES_MOMENTUM": "22",
            "CALIB_MIN_TRADES_MY_SCREENER": "33",
        }
        with patch.dict(os.environ, env):
            self.assertEqual(_resolve_calib_threshold("squeeze"), 11)
            self.assertEqual(_resolve_calib_threshold("momentum"), 22)
            self.assertEqual(_resolve_calib_threshold("my-screener"), 33)

    def test_momentum_invalid_does_not_affect_squeeze(self):
        """An invalid CALIB_MIN_TRADES_MOMENTUM must not change squeeze's result."""
        env = {
            "CALIB_MIN_TRADES_MOMENTUM": "bad",
            "CALIB_MIN_TRADES_SQUEEZE": "60",
        }
        with patch.dict(os.environ, env):
            self.assertEqual(_resolve_calib_threshold("squeeze"), 60)
            self.assertEqual(_resolve_calib_threshold("momentum"), _DEFAULT)

    def test_squeeze_invalid_does_not_affect_momentum(self):
        """An invalid CALIB_MIN_TRADES_SQUEEZE must not change momentum's result."""
        env = {
            "CALIB_MIN_TRADES_SQUEEZE": "-5",
            "CALIB_MIN_TRADES_MOMENTUM": "45",
            _LEGACY_KEY: "",
        }
        with patch.dict(os.environ, env):
            self.assertEqual(_resolve_calib_threshold("momentum"), 45)
            self.assertEqual(_resolve_calib_threshold("squeeze"), _DEFAULT)

    # --- edge case: both primary and legacy squeeze vars set simultaneously ---

    def test_primary_squeeze_wins_when_legacy_also_set(self):
        """CALIB_MIN_TRADES_SQUEEZE (primary) must win over SQUEEZE_CALIB_MIN_TRADES."""
        env = {
            "CALIB_MIN_TRADES_SQUEEZE": "88",
            _LEGACY_KEY: "99",
        }
        with patch.dict(os.environ, env):
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, 88)

    def test_primary_squeeze_wins_even_when_legacy_is_larger(self):
        """Primary beats legacy regardless of which value is numerically larger."""
        env = {
            "CALIB_MIN_TRADES_SQUEEZE": "5",
            _LEGACY_KEY: "200",
        }
        with patch.dict(os.environ, env):
            result = _resolve_calib_threshold("squeeze")
        self.assertEqual(result, 5)

    def test_legacy_alias_only_used_for_squeeze_not_other_keys_even_when_many_set(self):
        """Legacy alias must not bleed into any non-squeeze key when many vars are set."""
        env = {
            _LEGACY_KEY: "99",
            "CALIB_MIN_TRADES_MOMENTUM": "",
            "CALIB_MIN_TRADES_TREND": "",
            "CALIB_MIN_TRADES_SQUEEZE": "",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("CALIB_MIN_TRADES_MOMENTUM", None)
            os.environ.pop("CALIB_MIN_TRADES_TREND", None)
            os.environ.pop("CALIB_MIN_TRADES_SQUEEZE", None)
            self.assertEqual(_resolve_calib_threshold("momentum"), _DEFAULT)
            self.assertEqual(_resolve_calib_threshold("trend"), _DEFAULT)
            self.assertEqual(_resolve_calib_threshold("squeeze"), 99)

    # --- default constant sanity check ---

    def test_default_is_30(self):
        self.assertEqual(_DEFAULT, 30)


if __name__ == "__main__":
    unittest.main()
