"""Tests for startup config validation in log_utils.

Covers:
- _parse_int_env: warning on non-numeric values, warning on non-positive values,
  silence when absent/empty, correct return of valid values.
- validate_env_config: warnings for bad vars, returns list of bad names,
  strict mode exits the process, clean env produces no warnings.
"""

import os
import sys
import unittest
from io import StringIO
from unittest.mock import patch

from log_utils import _parse_int_env, validate_env_config, _INT_ENV_REGISTRY


class TestParseIntEnv(unittest.TestCase):
    """_parse_int_env must return correct values and never emit its own warnings.

    Warnings are the responsibility of validate_env_config(), not _parse_int_env().
    This avoids duplicate diagnostic output when module-level code calls _parse_int_env
    and then validate_env_config() is also called at startup.
    """

    def _call(self, name, raw_value, default):
        env = {name: raw_value} if raw_value is not None else {}
        env_without = {k: v for k, v in os.environ.items() if k != name}
        env_without.update(env)
        with patch.dict(os.environ, env_without, clear=True):
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                result = _parse_int_env(name, default)
                return result, mock_err.getvalue()

    def test_valid_value_returns_parsed_int(self):
        result, stderr = self._call("TCS_HISTORY_MAX_BYTES", "102400", 512 * 1024)
        self.assertEqual(result, 102400)
        self.assertEqual(stderr, "")

    def test_absent_var_returns_default_no_warning(self):
        key = "TCS_HISTORY_MAX_BYTES"
        env_without_key = {k: v for k, v in os.environ.items() if k != key}
        with patch.dict(os.environ, env_without_key, clear=True):
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                result = _parse_int_env(key, 99)
                self.assertEqual(result, 99)
                self.assertEqual(mock_err.getvalue(), "")

    def test_empty_string_returns_default_no_warning(self):
        result, stderr = self._call("TCS_HISTORY_MAX_BYTES", "", 77)
        self.assertEqual(result, 77)
        self.assertEqual(stderr, "")

    def test_non_numeric_value_returns_default_no_warning(self):
        result, stderr = self._call("TCS_HISTORY_MAX_BYTES", "abc", 512)
        self.assertEqual(result, 512)
        self.assertEqual(stderr, "")

    def test_zero_value_returns_default_no_warning(self):
        result, stderr = self._call("TCS_HISTORY_BACKUP_COUNT", "0", 1)
        self.assertEqual(result, 1)
        self.assertEqual(stderr, "")

    def test_negative_value_returns_default_no_warning(self):
        result, stderr = self._call("BACKFILL_LOG_MAX_BYTES", "-100", 500 * 1024)
        self.assertEqual(result, 500 * 1024)
        self.assertEqual(stderr, "")

    def test_float_string_returns_default_no_warning(self):
        result, stderr = self._call("RESET_LOG_MAX_BYTES", "3.14", 100 * 1024)
        self.assertEqual(result, 100 * 1024)
        self.assertEqual(stderr, "")

    def test_whitespace_only_returns_default_no_warning(self):
        result, stderr = self._call("RESET_LOG_BACKUP_COUNT", "   ", 1)
        self.assertEqual(result, 1)
        self.assertEqual(stderr, "")


class TestValidateEnvConfig(unittest.TestCase):
    """validate_env_config must surface all bad vars, return their names, and exit in strict mode."""

    def _run_with_env(self, env_overrides: dict, strict: bool = False):
        clean_env = {k: v for k, v in os.environ.items() if k not in _INT_ENV_REGISTRY}
        clean_env.update(env_overrides)
        with patch.dict(os.environ, clean_env, clear=True):
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                result = validate_env_config(strict=strict)
                return result, mock_err.getvalue()

    def test_clean_env_returns_empty_list_no_output(self):
        bad, stderr = self._run_with_env({})
        self.assertEqual(bad, [])
        self.assertEqual(stderr, "")

    def test_single_bad_var_returns_name_and_warns(self):
        bad, stderr = self._run_with_env({"TCS_HISTORY_MAX_BYTES": "notanumber"})
        self.assertIn("TCS_HISTORY_MAX_BYTES", bad)
        self.assertIn("CONFIG WARNING", stderr)
        self.assertIn("TCS_HISTORY_MAX_BYTES", stderr)
        self.assertIn("notanumber", stderr)

    def test_multiple_bad_vars_all_reported(self):
        bad, stderr = self._run_with_env({
            "TCS_HISTORY_MAX_BYTES": "abc",
            "RESET_LOG_BACKUP_COUNT": "0",
            "BACKFILL_LOG_MAX_BYTES": "-1",
        })
        self.assertIn("TCS_HISTORY_MAX_BYTES", bad)
        self.assertIn("RESET_LOG_BACKUP_COUNT", bad)
        self.assertIn("BACKFILL_LOG_MAX_BYTES", bad)
        self.assertEqual(len(bad), 3)

    def test_warning_includes_default_value(self):
        bad, stderr = self._run_with_env({"TCS_HISTORY_BACKUP_COUNT": "bad"})
        self.assertIn("1", stderr)

    def test_valid_override_is_not_flagged(self):
        bad, stderr = self._run_with_env({"TCS_HISTORY_MAX_BYTES": "204800"})
        self.assertNotIn("TCS_HISTORY_MAX_BYTES", bad)
        self.assertEqual(stderr, "")

    def test_strict_mode_exits_on_bad_var(self):
        clean_env = {k: v for k, v in os.environ.items() if k not in _INT_ENV_REGISTRY}
        clean_env["TCS_HISTORY_MAX_BYTES"] = "bad"
        with patch.dict(os.environ, clean_env, clear=True):
            with patch("sys.stderr", new_callable=StringIO):
                with self.assertRaises(SystemExit) as ctx:
                    validate_env_config(strict=True)
                self.assertEqual(ctx.exception.code, 1)

    def test_strict_mode_clean_env_does_not_exit(self):
        bad, stderr = self._run_with_env({}, strict=True)
        self.assertEqual(bad, [])

    def test_strict_via_env_var_exits_on_bad_config(self):
        clean_env = {k: v for k, v in os.environ.items() if k not in _INT_ENV_REGISTRY}
        clean_env["TCS_HISTORY_MAX_BYTES"] = "bad"
        clean_env["STRICT_CONFIG_VALIDATION"] = "1"
        with patch.dict(os.environ, clean_env, clear=True):
            with patch("sys.stderr", new_callable=StringIO):
                with self.assertRaises(SystemExit) as ctx:
                    validate_env_config()
                self.assertEqual(ctx.exception.code, 1)

    def test_strict_via_sys_argv_exits_on_bad_config(self):
        clean_env = {k: v for k, v in os.environ.items() if k not in _INT_ENV_REGISTRY}
        clean_env["BACKFILL_LOG_BACKUP_COUNT"] = "xyz"
        with patch.dict(os.environ, clean_env, clear=True):
            with patch.object(sys, "argv", ["prog", "--strict"]):
                with patch("sys.stderr", new_callable=StringIO):
                    with self.assertRaises(SystemExit) as ctx:
                        validate_env_config()
                    self.assertEqual(ctx.exception.code, 1)

    def test_absent_vars_not_flagged(self):
        clean_env = {k: v for k, v in os.environ.items() if k not in _INT_ENV_REGISTRY}
        with patch.dict(os.environ, clean_env, clear=True):
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                bad = validate_env_config()
                self.assertEqual(bad, [])
                self.assertEqual(mock_err.getvalue(), "")

    def test_strict_exit_message_names_bad_vars(self):
        clean_env = {k: v for k, v in os.environ.items() if k not in _INT_ENV_REGISTRY}
        clean_env["TCS_BASE_SCORE"] = "not_a_number"
        with patch.dict(os.environ, clean_env, clear=True):
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                with self.assertRaises(SystemExit):
                    validate_env_config(strict=True)
                output = mock_err.getvalue()
                self.assertIn("TCS_BASE_SCORE", output)


class TestRegistryCompleteness(unittest.TestCase):
    """Sanity-check that all known env vars are in the registry with positive defaults."""

    EXPECTED_VARS = {
        "TCS_HISTORY_MAX_BYTES",
        "TCS_HISTORY_BACKUP_COUNT",
        "BACKFILL_RUN_HISTORY_MAX_BYTES",
        "BACKFILL_RUN_HISTORY_BACKUP_COUNT",
        "BACKFILL_LOG_MAX_BYTES",
        "BACKFILL_LOG_BACKUP_COUNT",
        "TCS_HISTORY_RETENTION_DAYS",
        "TCS_BASE_SCORE",
        "RESET_LOG_MAX_BYTES",
        "RESET_LOG_BACKUP_COUNT",
    }

    def test_all_expected_vars_present(self):
        for name in self.EXPECTED_VARS:
            self.assertIn(name, _INT_ENV_REGISTRY, f"{name} missing from _INT_ENV_REGISTRY")

    def test_all_defaults_are_positive(self):
        for name, (default, _desc) in _INT_ENV_REGISTRY.items():
            self.assertGreater(default, 0, f"Default for {name} must be positive, got {default}")

    def test_all_descriptions_are_non_empty(self):
        for name, (_default, desc) in _INT_ENV_REGISTRY.items():
            self.assertTrue(desc.strip(), f"Description for {name} must not be empty")


if __name__ == "__main__":
    unittest.main()
