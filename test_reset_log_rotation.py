"""Unit tests for _rotate_reset_log() in calibrate_sp_mult.py.

Covers:
  - No rotation when the log file is below the size threshold
  - Rotation is triggered when the file meets or exceeds the threshold
  - Only one backup file is retained (older backups are deleted)
  - OSError during rotation is handled gracefully (no crash, warning printed)
"""

import os
import tempfile
import unittest
from unittest.mock import patch


import calibrate_sp_mult as csm


class TestNoRotationBelowThreshold(unittest.TestCase):
    """_rotate_reset_log must leave the file untouched when it is small."""

    def test_small_file_is_not_rotated(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            content = b"x" * (csm._RESET_LOG_MAX_BYTES - 1)
            with open(log, "wb") as fh:
                fh.write(content)

            csm._rotate_reset_log(log)

            self.assertTrue(os.path.exists(log), "original log must still exist")
            self.assertFalse(
                os.path.exists(log + ".1"),
                "no backup should be created when file is below threshold",
            )

    def test_missing_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "nonexistent.log")
            csm._rotate_reset_log(log)
            self.assertFalse(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))

    def test_exactly_one_byte_below_threshold_not_rotated(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            with open(log, "wb") as fh:
                fh.write(b"a" * (csm._RESET_LOG_MAX_BYTES - 1))

            csm._rotate_reset_log(log)

            self.assertTrue(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))


class TestRotationAtThreshold(unittest.TestCase):
    """_rotate_reset_log must rename the log to .1 when size >= threshold."""

    def test_rotation_triggered_at_exact_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * csm._RESET_LOG_MAX_BYTES)

            csm._rotate_reset_log(log)

            self.assertFalse(os.path.exists(log), "original log must be gone after rotation")
            self.assertTrue(os.path.exists(log + ".1"), "backup .1 must exist after rotation")

    def test_rotation_triggered_above_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            with open(log, "wb") as fh:
                fh.write(b"y" * (csm._RESET_LOG_MAX_BYTES + 512))

            csm._rotate_reset_log(log)

            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))

    def test_backup_contains_original_content(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            payload = b"z" * csm._RESET_LOG_MAX_BYTES
            with open(log, "wb") as fh:
                fh.write(payload)

            csm._rotate_reset_log(log)

            with open(log + ".1", "rb") as fh:
                backup_content = fh.read()

            self.assertEqual(backup_content, payload, "backup must contain the original data")


class TestOnlyOneBackupRetained(unittest.TestCase):
    """After rotation, at most _RESET_LOG_BACKUP_COUNT backups must exist."""

    def test_previous_backup_is_replaced_not_accumulated(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            old_backup = log + ".1"

            with open(old_backup, "wb") as fh:
                fh.write(b"old backup content")

            with open(log, "wb") as fh:
                fh.write(b"new content" * (csm._RESET_LOG_MAX_BYTES // 11 + 1))

            csm._rotate_reset_log(log)

            self.assertFalse(os.path.exists(log), "original log must be gone")
            self.assertTrue(os.path.exists(old_backup), "a single .1 backup must still exist")
            self.assertFalse(
                os.path.exists(log + ".2"),
                "no .2 backup should exist; backup count is 1",
            )

    def test_backup_count_constant_is_one(self):
        self.assertEqual(
            csm._RESET_LOG_BACKUP_COUNT,
            1,
            "_RESET_LOG_BACKUP_COUNT must be 1",
        )

    def test_old_backup_content_is_overwritten_by_new_rotation(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            new_data = b"new" * (csm._RESET_LOG_MAX_BYTES // 3 + 1)

            with open(log + ".1", "wb") as fh:
                fh.write(b"stale")

            with open(log, "wb") as fh:
                fh.write(new_data)

            csm._rotate_reset_log(log)

            with open(log + ".1", "rb") as fh:
                kept = fh.read()

            self.assertEqual(kept, new_data, ".1 must now hold the freshly rotated content")


class TestOSErrorHandledGracefully(unittest.TestCase):
    """_rotate_reset_log must not raise on OS-level failures; it prints a warning."""

    def test_getsize_oserror_prints_warning_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            with open(log, "wb") as fh:
                fh.write(b"a")

            with patch("os.path.getsize", side_effect=OSError("permission denied")):
                with patch("sys.stderr") as mock_stderr:
                    csm._rotate_reset_log(log)

            mock_stderr.write.assert_called()
            warning_text = "".join(
                str(c) for c in mock_stderr.write.call_args_list
            )
            self.assertIn("WARNING", warning_text)

    def test_rename_oserror_prints_warning_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * csm._RESET_LOG_MAX_BYTES)

            with patch("os.rename", side_effect=OSError("rename failed")):
                with patch("sys.stderr") as mock_stderr:
                    csm._rotate_reset_log(log)

            mock_stderr.write.assert_called()
            warning_text = "".join(
                str(c) for c in mock_stderr.write.call_args_list
            )
            self.assertIn("WARNING", warning_text)

    def test_no_exception_propagated_on_remove_failure(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            with open(log + ".1", "wb") as fh:
                fh.write(b"old")
            with open(log, "wb") as fh:
                fh.write(b"x" * csm._RESET_LOG_MAX_BYTES)

            with patch("os.remove", side_effect=OSError("remove failed")):
                try:
                    csm._rotate_reset_log(log)
                except OSError:
                    self.fail("_rotate_reset_log must not propagate OSError from os.remove")


if __name__ == "__main__":
    unittest.main()
