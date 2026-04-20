"""Unit tests for _rotate_log() (log_utils.py) exercised via calibrate_sp_mult constants.

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
from log_utils import _rotate_log


class TestNoRotationBelowThreshold(unittest.TestCase):
    """_rotate_log must leave the file untouched when it is small."""

    def test_small_file_is_not_rotated(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            content = b"x" * (csm._RESET_LOG_MAX_BYTES - 1)
            with open(log, "wb") as fh:
                fh.write(content)

            _rotate_log(log, csm._RESET_LOG_MAX_BYTES, csm._RESET_LOG_BACKUP_COUNT)

            self.assertTrue(os.path.exists(log), "original log must still exist")
            self.assertFalse(
                os.path.exists(log + ".1"),
                "no backup should be created when file is below threshold",
            )

    def test_missing_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "nonexistent.log")
            _rotate_log(log, csm._RESET_LOG_MAX_BYTES, csm._RESET_LOG_BACKUP_COUNT)
            self.assertFalse(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))

    def test_exactly_one_byte_below_threshold_not_rotated(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            with open(log, "wb") as fh:
                fh.write(b"a" * (csm._RESET_LOG_MAX_BYTES - 1))

            _rotate_log(log, csm._RESET_LOG_MAX_BYTES, csm._RESET_LOG_BACKUP_COUNT)

            self.assertTrue(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))


class TestRotationAtThreshold(unittest.TestCase):
    """_rotate_log must rename the log to .1 when size >= threshold."""

    def test_rotation_triggered_at_exact_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            with open(log, "wb") as fh:
                fh.write(b"x" * csm._RESET_LOG_MAX_BYTES)

            _rotate_log(log, csm._RESET_LOG_MAX_BYTES, csm._RESET_LOG_BACKUP_COUNT)

            self.assertFalse(os.path.exists(log), "original log must be gone after rotation")
            self.assertTrue(os.path.exists(log + ".1"), "backup .1 must exist after rotation")

    def test_rotation_triggered_above_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            with open(log, "wb") as fh:
                fh.write(b"y" * (csm._RESET_LOG_MAX_BYTES + 512))

            _rotate_log(log, csm._RESET_LOG_MAX_BYTES, csm._RESET_LOG_BACKUP_COUNT)

            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))

    def test_backup_contains_original_content(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            payload = b"z" * csm._RESET_LOG_MAX_BYTES
            with open(log, "wb") as fh:
                fh.write(payload)

            _rotate_log(log, csm._RESET_LOG_MAX_BYTES, csm._RESET_LOG_BACKUP_COUNT)

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

            _rotate_log(log, csm._RESET_LOG_MAX_BYTES, csm._RESET_LOG_BACKUP_COUNT)

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

            _rotate_log(log, csm._RESET_LOG_MAX_BYTES, csm._RESET_LOG_BACKUP_COUNT)

            with open(log + ".1", "rb") as fh:
                kept = fh.read()

            self.assertEqual(kept, new_data, ".1 must now hold the freshly rotated content")


class TestOSErrorHandledGracefully(unittest.TestCase):
    """_rotate_log must not raise on OS-level failures; it prints a warning."""

    def test_getsize_oserror_prints_warning_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            with open(log, "wb") as fh:
                fh.write(b"a")

            with patch("os.path.getsize", side_effect=OSError("permission denied")):
                with patch("sys.stderr") as mock_stderr:
                    _rotate_log(log, csm._RESET_LOG_MAX_BYTES, csm._RESET_LOG_BACKUP_COUNT)

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
                    _rotate_log(log, csm._RESET_LOG_MAX_BYTES, csm._RESET_LOG_BACKUP_COUNT)

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
                    _rotate_log(log, csm._RESET_LOG_MAX_BYTES, csm._RESET_LOG_BACKUP_COUNT)
                except OSError:
                    self.fail("_rotate_log must not propagate OSError from os.remove")


class TestWriteThenRotateIntegration(unittest.TestCase):
    """Integration tests: _write_reset_log drives rotation internally.

    These tests verify the full write-then-rotate path: repeated calls to
    _write_reset_log eventually push the log past _RESET_LOG_MAX_BYTES, at
    which point rotation fires automatically and subsequent writes land in a
    fresh log file.
    """

    def _seed_log(self, path: str, size: int) -> None:
        """Pre-fill *path* with *size* bytes of filler so threshold is near."""
        with open(path, "wb") as fh:
            fh.write(b"x" * size)

    def test_rotation_fires_during_write_session(self):
        """Backup .1 must appear once cumulative writes cross the threshold."""
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            backup = log + ".1"

            entry_estimate = 90
            seed_size = csm._RESET_LOG_MAX_BYTES - entry_estimate * 3
            self._seed_log(log, seed_size)

            for _ in range(10):
                csm._write_reset_log("trend", 0.85, False, log_path=log)
                if os.path.exists(backup):
                    break

            self.assertTrue(
                os.path.exists(backup),
                "backup .1 must exist once writes push the log past the threshold",
            )

    def test_active_log_is_small_after_rotation(self):
        """The active log must restart small (one entry only) after rotation."""
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")

            seed_size = csm._RESET_LOG_MAX_BYTES
            self._seed_log(log, seed_size)

            csm._write_reset_log("gap", 1.10, True, log_path=log)

            active_size = os.path.getsize(log)
            self.assertLess(
                active_size,
                csm._RESET_LOG_MAX_BYTES,
                "active log must be well below the threshold right after rotation",
            )

    def test_new_entry_is_correctly_formatted_after_rotation(self):
        """The entry written after rotation must follow the expected format."""
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")

            seed_size = csm._RESET_LOG_MAX_BYTES
            self._seed_log(log, seed_size)

            csm._write_reset_log("momentum", 0.75, False, log_path=log)

            with open(log) as fh:
                lines = [l for l in fh.readlines() if l.strip()]

            self.assertEqual(len(lines), 1, "exactly one entry expected in the fresh log")
            entry = lines[0]
            self.assertIn("pass=momentum", entry)
            self.assertIn("prev_mult=0.75", entry)
            self.assertIn("mode=interactive", entry)
            self.assertRegex(
                entry,
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
                "entry must start with an ISO timestamp",
            )

    def test_backup_holds_pre_rotation_content(self):
        """The .1 backup must contain the filler written before rotation."""
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            backup = log + ".1"

            seed_size = csm._RESET_LOG_MAX_BYTES
            self._seed_log(log, seed_size)

            csm._write_reset_log("trend", 0.50, True, log_path=log)

            self.assertTrue(os.path.exists(backup), "backup .1 must exist")
            backup_size = os.path.getsize(backup)
            self.assertGreaterEqual(
                backup_size,
                csm._RESET_LOG_MAX_BYTES,
                "backup must contain at least as many bytes as the rotation threshold",
            )

    def test_only_one_backup_after_multiple_rotation_cycles(self):
        """At most one .1 backup must exist even after several rotation cycles."""
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "resets.log")
            backup = log + ".1"

            for _cycle in range(3):
                seed_size = csm._RESET_LOG_MAX_BYTES
                self._seed_log(log, seed_size)
                csm._write_reset_log("trend", 1.0, False, log_path=log)

            self.assertTrue(os.path.exists(backup), ".1 backup must exist")
            self.assertFalse(
                os.path.exists(log + ".2"),
                "no .2 backup should ever be created; limit is one backup",
            )


if __name__ == "__main__":
    unittest.main()
