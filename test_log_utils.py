"""Standalone tests for log_utils._rotate_log and log_config._ensure_log_dir.

Covers _rotate_log directly with arbitrary max_bytes and backup_count values,
independent of any other module's constants.  Includes a multi-backup
(backup_count=3) suite that verifies the full shift chain.

Also covers _ensure_log_dir: verifies that deep nested directories are created
when custom env vars (RESET_LOG_PATH, TCS_HISTORY_LOG_PATH,
BACKFILL_RUN_HISTORY_LOG_PATH) point to non-existent paths.
"""

import importlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from log_utils import _rotate_log
from log_config import _ensure_log_dir

MAX_BYTES = 1024
BACKUP_COUNT = 1


class TestNoRotationBelowThreshold(unittest.TestCase):
    """_rotate_log must not rotate when the file is below the size threshold."""

    def test_small_file_is_not_rotated(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            with open(log, "wb") as fh:
                fh.write(b"a" * (MAX_BYTES - 1))

            _rotate_log(log, MAX_BYTES, BACKUP_COUNT)

            self.assertTrue(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))

    def test_missing_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "nonexistent.log")
            _rotate_log(log, MAX_BYTES, BACKUP_COUNT)
            self.assertFalse(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))

    def test_exactly_one_byte_below_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            with open(log, "wb") as fh:
                fh.write(b"z" * (MAX_BYTES - 1))

            _rotate_log(log, MAX_BYTES, BACKUP_COUNT)

            self.assertTrue(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))


class TestRotationAtThreshold(unittest.TestCase):
    """_rotate_log must rotate when size >= max_bytes."""

    def test_rotation_triggered_at_exact_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * MAX_BYTES)

            _rotate_log(log, MAX_BYTES, BACKUP_COUNT)

            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))

    def test_rotation_triggered_above_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            with open(log, "wb") as fh:
                fh.write(b"y" * (MAX_BYTES + 512))

            _rotate_log(log, MAX_BYTES, BACKUP_COUNT)

            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))

    def test_backup_preserves_original_content(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            payload = b"data" * (MAX_BYTES // 4)
            with open(log, "wb") as fh:
                fh.write(payload)

            _rotate_log(log, MAX_BYTES, BACKUP_COUNT)

            with open(log + ".1", "rb") as fh:
                backup_content = fh.read()
            self.assertEqual(backup_content, payload)


class TestSingleBackupCount(unittest.TestCase):
    """With backup_count=1, only one backup (.1) must ever exist."""

    def test_previous_backup_is_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            old_backup = log + ".1"
            with open(old_backup, "wb") as fh:
                fh.write(b"stale content")
            new_data = b"fresh" * (MAX_BYTES // 5 + 1)
            with open(log, "wb") as fh:
                fh.write(new_data)

            _rotate_log(log, MAX_BYTES, BACKUP_COUNT)

            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))
            self.assertFalse(os.path.exists(log + ".2"))
            with open(log + ".1", "rb") as fh:
                self.assertEqual(fh.read(), new_data)

    def test_no_second_backup_created(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            with open(log + ".1", "wb") as fh:
                fh.write(b"old")
            with open(log, "wb") as fh:
                fh.write(b"x" * MAX_BYTES)

            _rotate_log(log, MAX_BYTES, BACKUP_COUNT)

            self.assertFalse(os.path.exists(log + ".2"))


class TestMultipleBackupCount(unittest.TestCase):
    """Tests with backup_count=3 verify the full shift chain.

    Before rotation the state is:
        app.log      (full — triggers rotation)
        app.log.1    (previous rotation)
        app.log.2    (older)
        app.log.3    (oldest — should be deleted)

    After rotation the expected state is:
        app.log      gone (renamed to .1)
        app.log.1    contains original app.log content
        app.log.2    contains original .1 content
        app.log.3    contains original .2 content
        app.log.4    must NOT exist
    """

    BACKUP_COUNT = 3
    MAX = 512

    def _setup_full_chain(self, td):
        log = os.path.join(td, "app.log")
        with open(log, "wb") as fh:
            fh.write(b"current" * (self.MAX // 7 + 1))
        with open(log + ".1", "wb") as fh:
            fh.write(b"backup-1")
        with open(log + ".2", "wb") as fh:
            fh.write(b"backup-2")
        with open(log + ".3", "wb") as fh:
            fh.write(b"backup-3-oldest")
        return log

    def test_shift_chain_moves_all_backups(self):
        with tempfile.TemporaryDirectory() as td:
            log = self._setup_full_chain(td)
            with open(log, "rb") as fh:
                original_data = fh.read()
            with open(log + ".1", "rb") as fh:
                backup1_data = fh.read()
            with open(log + ".2", "rb") as fh:
                backup2_data = fh.read()

            _rotate_log(log, self.MAX, self.BACKUP_COUNT)

            self.assertFalse(os.path.exists(log), "original log must be gone")
            with open(log + ".1", "rb") as fh:
                self.assertEqual(fh.read(), original_data, ".1 must hold original content")
            with open(log + ".2", "rb") as fh:
                self.assertEqual(fh.read(), backup1_data, ".2 must hold old .1 content")
            with open(log + ".3", "rb") as fh:
                self.assertEqual(fh.read(), backup2_data, ".3 must hold old .2 content")

    def test_oldest_backup_is_deleted_not_shifted(self):
        with tempfile.TemporaryDirectory() as td:
            log = self._setup_full_chain(td)

            _rotate_log(log, self.MAX, self.BACKUP_COUNT)

            self.assertFalse(
                os.path.exists(log + ".4"),
                ".4 must never be created; oldest backup is dropped",
            )

    def test_shift_from_empty_chain(self):
        """Rotation with backup_count=3 when no existing backups are present."""
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            payload = b"data" * (self.MAX // 4 + 1)
            with open(log, "wb") as fh:
                fh.write(payload)

            _rotate_log(log, self.MAX, self.BACKUP_COUNT)

            self.assertFalse(os.path.exists(log))
            with open(log + ".1", "rb") as fh:
                self.assertEqual(fh.read(), payload)
            self.assertFalse(os.path.exists(log + ".2"))
            self.assertFalse(os.path.exists(log + ".3"))

    def test_partial_chain_shift(self):
        """Only .1 exists before rotation; it should shift to .2."""
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            payload = b"x" * (self.MAX + 1)
            prev_backup = b"previous"
            with open(log, "wb") as fh:
                fh.write(payload)
            with open(log + ".1", "wb") as fh:
                fh.write(prev_backup)

            _rotate_log(log, self.MAX, self.BACKUP_COUNT)

            self.assertFalse(os.path.exists(log))
            with open(log + ".1", "rb") as fh:
                self.assertEqual(fh.read(), payload)
            with open(log + ".2", "rb") as fh:
                self.assertEqual(fh.read(), prev_backup)
            self.assertFalse(os.path.exists(log + ".3"))

    def test_multiple_rotation_cycles_respect_backup_count(self):
        """After several rotation cycles, at most backup_count files exist."""
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")

            for _cycle in range(6):
                with open(log, "wb") as fh:
                    fh.write(b"cycle" * (self.MAX // 5 + 1))
                _rotate_log(log, self.MAX, self.BACKUP_COUNT)

            for idx in range(1, self.BACKUP_COUNT + 1):
                self.assertTrue(
                    os.path.exists(log + f".{idx}"),
                    f".{idx} backup must exist after {6} rotation cycles",
                )
            self.assertFalse(
                os.path.exists(log + f".{self.BACKUP_COUNT + 1}"),
                f".{self.BACKUP_COUNT + 1} must not exist; backup_count={self.BACKUP_COUNT}",
            )


class TestParameterisedMaxBytes(unittest.TestCase):
    """Rotation threshold works correctly for several different max_bytes values."""

    def _assert_rotates_at(self, max_bytes: int) -> None:
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * max_bytes)
            _rotate_log(log, max_bytes, 1)
            self.assertTrue(os.path.exists(log + ".1"), f"should rotate at {max_bytes} bytes")
            self.assertFalse(os.path.exists(log), "original must be gone after rotation")

    def _assert_no_rotation_below(self, max_bytes: int) -> None:
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * (max_bytes - 1))
            _rotate_log(log, max_bytes, 1)
            self.assertTrue(os.path.exists(log), "original must remain when below threshold")
            self.assertFalse(os.path.exists(log + ".1"))

    def test_rotation_at_256_bytes(self):
        self._assert_rotates_at(256)

    def test_no_rotation_below_256_bytes(self):
        self._assert_no_rotation_below(256)

    def test_rotation_at_4096_bytes(self):
        self._assert_rotates_at(4096)

    def test_no_rotation_below_4096_bytes(self):
        self._assert_no_rotation_below(4096)

    def test_rotation_at_1mb(self):
        self._assert_rotates_at(1024 * 1024)

    def test_no_rotation_below_1mb(self):
        self._assert_no_rotation_below(1024 * 1024)


class TestOSErrorHandledGracefully(unittest.TestCase):
    """_rotate_log must not raise on OS-level failures; it prints a warning."""

    def test_getsize_oserror_prints_warning(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            with open(log, "wb") as fh:
                fh.write(b"a")

            with patch("os.path.getsize", side_effect=OSError("permission denied")):
                with patch("sys.stderr") as mock_stderr:
                    _rotate_log(log, MAX_BYTES, BACKUP_COUNT)

            mock_stderr.write.assert_called()
            warning_text = "".join(str(c) for c in mock_stderr.write.call_args_list)
            self.assertIn("WARNING", warning_text)

    def test_rename_oserror_prints_warning(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * MAX_BYTES)

            with patch("os.rename", side_effect=OSError("rename failed")):
                with patch("sys.stderr") as mock_stderr:
                    _rotate_log(log, MAX_BYTES, BACKUP_COUNT)

            mock_stderr.write.assert_called()
            warning_text = "".join(str(c) for c in mock_stderr.write.call_args_list)
            self.assertIn("WARNING", warning_text)

    def test_remove_oserror_does_not_propagate(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            with open(log + ".1", "wb") as fh:
                fh.write(b"old")
            with open(log, "wb") as fh:
                fh.write(b"x" * MAX_BYTES)

            with patch("os.remove", side_effect=OSError("remove failed")):
                try:
                    _rotate_log(log, MAX_BYTES, BACKUP_COUNT)
                except OSError:
                    self.fail("_rotate_log must not propagate OSError from os.remove")

    def test_no_exception_on_getsize_failure_for_multi_backup(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "app.log")
            with open(log, "wb") as fh:
                fh.write(b"b")

            with patch("os.path.getsize", side_effect=OSError("disk error")):
                try:
                    _rotate_log(log, MAX_BYTES, 3)
                except OSError:
                    self.fail("_rotate_log must not raise even with backup_count=3")


class TestEnsureLogDirDirect(unittest.TestCase):
    """_ensure_log_dir must create deeply nested directories that do not yet exist."""

    def test_single_level_nested_dir_is_created(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "subdir", "app.log")
            _ensure_log_dir(target)
            self.assertTrue(os.path.isdir(os.path.dirname(target)))

    def test_deeply_nested_dir_is_created(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "a", "b", "c", "d", "app.log")
            _ensure_log_dir(target)
            self.assertTrue(os.path.isdir(os.path.dirname(target)))

    def test_existing_dir_is_a_noop(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "app.log")
            _ensure_log_dir(target)
            _ensure_log_dir(target)
            self.assertTrue(os.path.isdir(td))

    def test_log_file_itself_is_not_created(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "subdir", "app.log")
            _ensure_log_dir(target)
            self.assertFalse(os.path.exists(target))


class TestEnsureLogDirViaEnvVars(unittest.TestCase):
    """log_config must create the parent directory for each custom log path env var."""

    def _reload_log_config(self):
        """Remove log_config from sys.modules and re-import it so module-level
        _ensure_log_dir calls run again with the currently patched env vars."""
        sys.modules.pop("log_config", None)
        importlib.import_module("log_config")

    def test_reset_log_path_env_var_creates_directory(self):
        with tempfile.TemporaryDirectory() as td:
            custom_path = os.path.join(td, "custom", "resets", "calibration_resets.log")
            with patch.dict(os.environ, {"RESET_LOG_PATH": custom_path}):
                self._reload_log_config()
            self.assertTrue(
                os.path.isdir(os.path.join(td, "custom", "resets")),
                "Parent directory for RESET_LOG_PATH must be created on import",
            )

    def test_tcs_history_log_path_env_var_creates_directory(self):
        with tempfile.TemporaryDirectory() as td:
            custom_path = os.path.join(td, "logs", "tcs", "history", "tcs_history.jsonl")
            with patch.dict(os.environ, {"TCS_HISTORY_LOG_PATH": custom_path}):
                self._reload_log_config()
            self.assertTrue(
                os.path.isdir(os.path.join(td, "logs", "tcs", "history")),
                "Parent directory for TCS_HISTORY_LOG_PATH must be created on import",
            )

    def test_backfill_run_history_log_path_env_var_creates_directory(self):
        with tempfile.TemporaryDirectory() as td:
            custom_path = os.path.join(td, "backfill", "run", "history", "run_history.log")
            with patch.dict(os.environ, {"BACKFILL_RUN_HISTORY_LOG_PATH": custom_path}):
                self._reload_log_config()
            self.assertTrue(
                os.path.isdir(os.path.join(td, "backfill", "run", "history")),
                "Parent directory for BACKFILL_RUN_HISTORY_LOG_PATH must be created on import",
            )

    def test_all_three_env_vars_create_their_directories(self):
        with tempfile.TemporaryDirectory() as td:
            reset_path = os.path.join(td, "r", "resets.log")
            tcs_path = os.path.join(td, "t", "tcs.jsonl")
            backfill_path = os.path.join(td, "b", "backfill.log")
            env_patch = {
                "RESET_LOG_PATH": reset_path,
                "TCS_HISTORY_LOG_PATH": tcs_path,
                "BACKFILL_RUN_HISTORY_LOG_PATH": backfill_path,
            }
            with patch.dict(os.environ, env_patch):
                self._reload_log_config()
            self.assertTrue(os.path.isdir(os.path.join(td, "r")))
            self.assertTrue(os.path.isdir(os.path.join(td, "t")))
            self.assertTrue(os.path.isdir(os.path.join(td, "b")))

    def tearDown(self):
        sys.modules.pop("log_config", None)


if __name__ == "__main__":
    unittest.main()
