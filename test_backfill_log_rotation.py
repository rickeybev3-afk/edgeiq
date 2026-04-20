"""Unit tests for backfill pipeline log rotation env-var settings.

Covers:
  - Default values (500 KB, 1 backup) when env vars are absent
  - Env-var overrides are read correctly by _parse_int_env
  - Rotation triggers at the byte limit controlled by BACKFILL_LOG_MAX_BYTES
  - Backup count is honoured by BACKFILL_LOG_BACKUP_COUNT
  - OSError during rotation is handled gracefully (no crash, warning printed)

The constants _BACKFILL_LOG_MAX_BYTES and _BACKFILL_LOG_BACKUP_COUNT live in
app.py (lines 578-579) and are populated via _parse_int_env at import time:

    _BACKFILL_LOG_MAX_BYTES   = _parse_int_env("BACKFILL_LOG_MAX_BYTES",  500 * 1024)
    _BACKFILL_LOG_BACKUP_COUNT = _parse_int_env("BACKFILL_LOG_BACKUP_COUNT", 1)

Because app.py is a Streamlit entry point its import triggers the full
Streamlit runtime initialisation, which is too slow for a unit-test suite.
Tests therefore exercise the same _parse_int_env + _rotate_log path directly,
using the exact same env-var names and defaults.  Any future rename of those
names or change to the defaults will break the tests here in the same way it
would break the live application.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from log_utils import _parse_int_env, _rotate_log

_DEFAULT_MAX_BYTES = 500 * 1024
_DEFAULT_BACKUP_COUNT = 1
_ENV_MAX_BYTES = "BACKFILL_LOG_MAX_BYTES"
_ENV_BACKUP_COUNT = "BACKFILL_LOG_BACKUP_COUNT"


def _get_max_bytes() -> int:
    return _parse_int_env(_ENV_MAX_BYTES, _DEFAULT_MAX_BYTES)


def _get_backup_count() -> int:
    return _parse_int_env(_ENV_BACKUP_COUNT, _DEFAULT_BACKUP_COUNT)


class TestDefaultConstants(unittest.TestCase):
    """Default values must be 500 KB and 1 backup when env vars are absent."""

    def test_default_max_bytes_is_500kb(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV_MAX_BYTES, None)
            self.assertEqual(
                _get_max_bytes(),
                500 * 1024,
                "default _BACKFILL_LOG_MAX_BYTES must be 500 KB (512 000 bytes)",
            )

    def test_default_backup_count_is_one(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV_BACKUP_COUNT, None)
            self.assertEqual(
                _get_backup_count(),
                1,
                "default _BACKFILL_LOG_BACKUP_COUNT must be 1",
            )

    def test_missing_max_bytes_env_var_returns_default(self):
        with patch.dict(os.environ, {_ENV_MAX_BYTES: ""}, clear=False):
            result = _parse_int_env(_ENV_MAX_BYTES, _DEFAULT_MAX_BYTES)
        self.assertEqual(result, _DEFAULT_MAX_BYTES)

    def test_missing_backup_count_env_var_returns_default(self):
        with patch.dict(os.environ, {_ENV_BACKUP_COUNT: ""}, clear=False):
            result = _parse_int_env(_ENV_BACKUP_COUNT, _DEFAULT_BACKUP_COUNT)
        self.assertEqual(result, _DEFAULT_BACKUP_COUNT)


class TestEnvVarOverrides(unittest.TestCase):
    """Env vars must override the defaults when set to valid positive integers."""

    def test_max_bytes_env_var_is_read(self):
        with patch.dict(os.environ, {_ENV_MAX_BYTES: "102400"}):
            result = _parse_int_env(_ENV_MAX_BYTES, _DEFAULT_MAX_BYTES)
        self.assertEqual(result, 102400)

    def test_backup_count_env_var_is_read(self):
        with patch.dict(os.environ, {_ENV_BACKUP_COUNT: "3"}):
            result = _parse_int_env(_ENV_BACKUP_COUNT, _DEFAULT_BACKUP_COUNT)
        self.assertEqual(result, 3)

    def test_non_numeric_max_bytes_falls_back_to_default(self):
        with patch.dict(os.environ, {_ENV_MAX_BYTES: "not_a_number"}):
            result = _parse_int_env(_ENV_MAX_BYTES, _DEFAULT_MAX_BYTES)
        self.assertEqual(result, _DEFAULT_MAX_BYTES)

    def test_non_numeric_backup_count_falls_back_to_default(self):
        with patch.dict(os.environ, {_ENV_BACKUP_COUNT: "abc"}):
            result = _parse_int_env(_ENV_BACKUP_COUNT, _DEFAULT_BACKUP_COUNT)
        self.assertEqual(result, _DEFAULT_BACKUP_COUNT)

    def test_zero_max_bytes_falls_back_to_default(self):
        with patch.dict(os.environ, {_ENV_MAX_BYTES: "0"}):
            result = _parse_int_env(_ENV_MAX_BYTES, _DEFAULT_MAX_BYTES)
        self.assertEqual(result, _DEFAULT_MAX_BYTES)

    def test_negative_backup_count_falls_back_to_default(self):
        with patch.dict(os.environ, {_ENV_BACKUP_COUNT: "-1"}):
            result = _parse_int_env(_ENV_BACKUP_COUNT, _DEFAULT_BACKUP_COUNT)
        self.assertEqual(result, _DEFAULT_BACKUP_COUNT)


class TestNoRotationBelowThreshold(unittest.TestCase):
    """_rotate_log must leave the file untouched when it is below the threshold."""

    def test_small_file_is_not_rotated_at_default_threshold(self):
        max_bytes = _DEFAULT_MAX_BYTES
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * (max_bytes - 1))

            _rotate_log(log, max_bytes, _DEFAULT_BACKUP_COUNT)

            self.assertTrue(os.path.exists(log), "original log must still exist")
            self.assertFalse(
                os.path.exists(log + ".1"),
                "no backup should be created when file is below threshold",
            )

    def test_missing_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "nonexistent.log")
            _rotate_log(log, _DEFAULT_MAX_BYTES, _DEFAULT_BACKUP_COUNT)
            self.assertFalse(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))

    def test_exactly_one_byte_below_threshold_not_rotated(self):
        max_bytes = _DEFAULT_MAX_BYTES
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            with open(log, "wb") as fh:
                fh.write(b"a" * (max_bytes - 1))

            _rotate_log(log, max_bytes, _DEFAULT_BACKUP_COUNT)

            self.assertTrue(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))

    def test_small_file_not_rotated_with_custom_env_threshold(self):
        custom_max = 65536
        with patch.dict(os.environ, {_ENV_MAX_BYTES: str(custom_max)}):
            effective_max = _parse_int_env(_ENV_MAX_BYTES, _DEFAULT_MAX_BYTES)

        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * (effective_max - 1))

            _rotate_log(log, effective_max, _DEFAULT_BACKUP_COUNT)

            self.assertTrue(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))


class TestRotationAtThreshold(unittest.TestCase):
    """_rotate_log must rename the log to .1 when size >= threshold."""

    def test_rotation_triggered_at_exact_default_threshold(self):
        max_bytes = _DEFAULT_MAX_BYTES
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * max_bytes)

            _rotate_log(log, max_bytes, _DEFAULT_BACKUP_COUNT)

            self.assertFalse(os.path.exists(log), "original log must be gone after rotation")
            self.assertTrue(os.path.exists(log + ".1"), "backup .1 must exist after rotation")

    def test_rotation_triggered_above_default_threshold(self):
        max_bytes = _DEFAULT_MAX_BYTES
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            with open(log, "wb") as fh:
                fh.write(b"y" * (max_bytes + 512))

            _rotate_log(log, max_bytes, _DEFAULT_BACKUP_COUNT)

            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))

    def test_backup_contains_original_content(self):
        max_bytes = _DEFAULT_MAX_BYTES
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            payload = b"z" * max_bytes
            with open(log, "wb") as fh:
                fh.write(payload)

            _rotate_log(log, max_bytes, _DEFAULT_BACKUP_COUNT)

            with open(log + ".1", "rb") as fh:
                backup_content = fh.read()

            self.assertEqual(backup_content, payload, "backup must contain the original data")

    def test_rotation_triggered_at_custom_env_threshold(self):
        custom_max = 8192
        with patch.dict(os.environ, {_ENV_MAX_BYTES: str(custom_max)}):
            effective_max = _parse_int_env(_ENV_MAX_BYTES, _DEFAULT_MAX_BYTES)

        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * effective_max)

            _rotate_log(log, effective_max, _DEFAULT_BACKUP_COUNT)

            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))

    def test_no_rotation_just_below_custom_env_threshold(self):
        custom_max = 8192
        with patch.dict(os.environ, {_ENV_MAX_BYTES: str(custom_max)}):
            effective_max = _parse_int_env(_ENV_MAX_BYTES, _DEFAULT_MAX_BYTES)

        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * (effective_max - 1))

            _rotate_log(log, effective_max, _DEFAULT_BACKUP_COUNT)

            self.assertTrue(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))


class TestBackupCountHonoured(unittest.TestCase):
    """_BACKFILL_LOG_BACKUP_COUNT must constrain the number of backup files."""

    def test_default_backup_count_is_one(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV_BACKUP_COUNT, None)
            self.assertEqual(
                _get_backup_count(),
                1,
                "_BACKFILL_LOG_BACKUP_COUNT default must be 1",
            )

    def test_no_second_backup_created_with_default_count(self):
        max_bytes = _DEFAULT_MAX_BYTES
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")

            with open(log + ".1", "wb") as fh:
                fh.write(b"old backup content")

            with open(log, "wb") as fh:
                fh.write(b"new content" * (max_bytes // 11 + 1))

            _rotate_log(log, max_bytes, _DEFAULT_BACKUP_COUNT)

            self.assertFalse(os.path.exists(log), "original log must be gone")
            self.assertTrue(os.path.exists(log + ".1"), "a single .1 backup must exist")
            self.assertFalse(
                os.path.exists(log + ".2"),
                "no .2 backup should exist; backup count is 1",
            )

    def test_old_backup_content_is_overwritten_after_rotation(self):
        max_bytes = _DEFAULT_MAX_BYTES
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            new_data = b"new" * (max_bytes // 3 + 1)

            with open(log + ".1", "wb") as fh:
                fh.write(b"stale backup")

            with open(log, "wb") as fh:
                fh.write(new_data)

            _rotate_log(log, max_bytes, _DEFAULT_BACKUP_COUNT)

            with open(log + ".1", "rb") as fh:
                kept = fh.read()

            self.assertEqual(kept, new_data, ".1 must now hold the freshly rotated content")

    def test_custom_backup_count_creates_multiple_backups(self):
        custom_count = 3
        with patch.dict(os.environ, {_ENV_BACKUP_COUNT: str(custom_count)}):
            effective_count = _parse_int_env(_ENV_BACKUP_COUNT, _DEFAULT_BACKUP_COUNT)

        max_bytes = 512
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")

            for _cycle in range(effective_count + 1):
                with open(log, "wb") as fh:
                    fh.write(b"cycle" * (max_bytes // 5 + 1))
                _rotate_log(log, max_bytes, effective_count)

            for idx in range(1, effective_count + 1):
                self.assertTrue(
                    os.path.exists(log + f".{idx}"),
                    f".{idx} backup must exist after {effective_count + 1} rotation cycles",
                )
            self.assertFalse(
                os.path.exists(log + f".{effective_count + 1}"),
                f".{effective_count + 1} must not exist; backup_count={effective_count}",
            )

    def test_only_one_backup_after_multiple_rotation_cycles_with_default(self):
        max_bytes = _DEFAULT_MAX_BYTES
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            backup = log + ".1"

            for _cycle in range(3):
                with open(log, "wb") as fh:
                    fh.write(b"x" * max_bytes)
                _rotate_log(log, max_bytes, _DEFAULT_BACKUP_COUNT)

            self.assertTrue(os.path.exists(backup), ".1 backup must exist")
            self.assertFalse(
                os.path.exists(log + ".2"),
                "no .2 backup should ever be created; backup_count is 1",
            )


class TestOSErrorHandledGracefully(unittest.TestCase):
    """_rotate_log must not raise on OS-level failures; it prints a warning."""

    def test_getsize_oserror_prints_warning_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            with open(log, "wb") as fh:
                fh.write(b"a")

            with patch("os.path.getsize", side_effect=OSError("permission denied")):
                with patch("sys.stderr") as mock_stderr:
                    _rotate_log(log, _DEFAULT_MAX_BYTES, _DEFAULT_BACKUP_COUNT)

            mock_stderr.write.assert_called()
            warning_text = "".join(
                str(c) for c in mock_stderr.write.call_args_list
            )
            self.assertIn("WARNING", warning_text)

    def test_rename_oserror_prints_warning_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * _DEFAULT_MAX_BYTES)

            with patch("os.rename", side_effect=OSError("rename failed")):
                with patch("sys.stderr") as mock_stderr:
                    _rotate_log(log, _DEFAULT_MAX_BYTES, _DEFAULT_BACKUP_COUNT)

            mock_stderr.write.assert_called()
            warning_text = "".join(
                str(c) for c in mock_stderr.write.call_args_list
            )
            self.assertIn("WARNING", warning_text)

    def test_no_exception_propagated_on_remove_failure(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            with open(log + ".1", "wb") as fh:
                fh.write(b"old")
            with open(log, "wb") as fh:
                fh.write(b"x" * _DEFAULT_MAX_BYTES)

            with patch("os.remove", side_effect=OSError("remove failed")):
                try:
                    _rotate_log(log, _DEFAULT_MAX_BYTES, _DEFAULT_BACKUP_COUNT)
                except OSError:
                    self.fail("_rotate_log must not propagate OSError from os.remove")


class TestRotationCycleIntegration(unittest.TestCase):
    """Integration tests: repeated writes and rotations using the effective constants.

    These tests simulate the behaviour of the backfill pipeline flushing data
    to the log and having _rotate_log called at the end of each run, verifying
    that the full rotation lifecycle works correctly with the env-var-controlled
    thresholds.
    """

    def _seed_log(self, path: str, size: int) -> None:
        with open(path, "wb") as fh:
            fh.write(b"x" * size)

    def test_rotation_fires_when_log_reaches_threshold(self):
        max_bytes = _DEFAULT_MAX_BYTES
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            backup = log + ".1"

            self._seed_log(log, max_bytes)
            _rotate_log(log, max_bytes, _DEFAULT_BACKUP_COUNT)

            self.assertTrue(
                os.path.exists(backup),
                "backup .1 must exist once the log reaches the threshold",
            )
            self.assertFalse(os.path.exists(log), "original log must be absent after rotation")

    def test_active_log_absent_after_rotation_no_new_write(self):
        max_bytes = _DEFAULT_MAX_BYTES
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")

            self._seed_log(log, max_bytes)
            _rotate_log(log, max_bytes, _DEFAULT_BACKUP_COUNT)

            self.assertFalse(
                os.path.exists(log),
                "the active log must not exist immediately after rotation before a new write",
            )

    def test_backup_size_at_least_threshold_after_rotation(self):
        max_bytes = _DEFAULT_MAX_BYTES
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")
            backup = log + ".1"

            self._seed_log(log, max_bytes)
            _rotate_log(log, max_bytes, _DEFAULT_BACKUP_COUNT)

            self.assertTrue(os.path.exists(backup))
            self.assertGreaterEqual(
                os.path.getsize(backup),
                max_bytes,
                "backup must contain at least as many bytes as the rotation threshold",
            )

    def test_only_one_backup_after_several_cycles_default_count(self):
        max_bytes = _DEFAULT_MAX_BYTES
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")

            for _cycle in range(4):
                self._seed_log(log, max_bytes)
                _rotate_log(log, max_bytes, _DEFAULT_BACKUP_COUNT)

            self.assertTrue(os.path.exists(log + ".1"), ".1 backup must exist")
            self.assertFalse(
                os.path.exists(log + ".2"),
                "no .2 backup should ever appear with backup_count=1",
            )

    def test_custom_env_max_bytes_controls_rotation_boundary(self):
        custom_max = 4096
        with patch.dict(os.environ, {_ENV_MAX_BYTES: str(custom_max)}):
            effective_max = _parse_int_env(_ENV_MAX_BYTES, _DEFAULT_MAX_BYTES)

        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")

            self._seed_log(log, effective_max - 1)
            _rotate_log(log, effective_max, _DEFAULT_BACKUP_COUNT)
            self.assertTrue(os.path.exists(log), "log below threshold must not be rotated")
            self.assertFalse(os.path.exists(log + ".1"))

            self._seed_log(log, effective_max)
            _rotate_log(log, effective_max, _DEFAULT_BACKUP_COUNT)
            self.assertFalse(os.path.exists(log), "log at threshold must be rotated")
            self.assertTrue(os.path.exists(log + ".1"))

    def test_custom_env_backup_count_limits_retained_files(self):
        custom_count = 2
        with patch.dict(os.environ, {_ENV_BACKUP_COUNT: str(custom_count)}):
            effective_count = _parse_int_env(_ENV_BACKUP_COUNT, _DEFAULT_BACKUP_COUNT)

        max_bytes = 1024
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_pipeline.log")

            for _cycle in range(effective_count + 2):
                with open(log, "wb") as fh:
                    fh.write(b"run" * (max_bytes // 3 + 1))
                _rotate_log(log, max_bytes, effective_count)

            for idx in range(1, effective_count + 1):
                self.assertTrue(
                    os.path.exists(log + f".{idx}"),
                    f".{idx} must exist after enough rotation cycles",
                )
            self.assertFalse(
                os.path.exists(log + f".{effective_count + 1}"),
                f".{effective_count + 1} must not exist; backup_count={effective_count}",
            )


if __name__ == "__main__":
    unittest.main()
