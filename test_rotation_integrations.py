"""Integration tests for the _rotate_log call sites in backend.py and app.py.

Three layers of coverage:

1. Constant correctness — the values extracted from each module are sane
   positive integers that match expected defaults (env not set in test runner).

2. Call-site argument order — an AST walk of each module's source verifies
   that the arguments passed to _rotate_log at each call site name the correct
   constants in the correct positional slots (path, max_bytes, backup_count).
   This catches wrong-argument-order or wrong-constant bugs without importing
   the full, side-effect-heavy modules.

3. Threshold behaviour — _rotate_log honours the resolved constants from both
   modules: files below the threshold are kept, files at-or-above are rotated.
"""

import ast
import os
import sys
import tempfile
import unittest

# ---------------------------------------------------------------------------
# Constant extraction (via _parse_int_env from log_utils)
# ---------------------------------------------------------------------------
# Both modules define their rotation constants through _parse_int_env, e.g.
#   _TCS_HISTORY_MAX_BYTES = _parse_int_env("TCS_HISTORY_MAX_BYTES", 500 * 1024)
# We resolve them by evaluating only those assignment lines inside a minimal
# namespace that provides _parse_int_env (imported from log_utils) and os.
# This avoids importing streamlit, Flask, supabase, and all other heavyweight
# dependencies that the full modules pull in at import time.

from log_utils import _parse_int_env, _rotate_log


def _extract_constants(module_path: str, constant_names: list) -> dict:
    """Return {name: resolved_value} for the requested module-level constants.

    Only lines that look like simple assignments for the named constants are
    evaluated.  The eval namespace contains _parse_int_env and os so that
    both plain arithmetic and _parse_int_env(env_name, default) forms resolve
    correctly.
    """
    _eval_ns = {"_parse_int_env": _parse_int_env, "os": os}
    found = {}
    with open(module_path, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            for name in constant_names:
                if stripped.startswith(name) and "=" in stripped:
                    try:
                        _, _, expr = stripped.partition("=")
                        expr = expr.split("#")[0].strip()
                        found[name] = eval(expr, _eval_ns)  # noqa: S307
                    except Exception:
                        pass
            if len(found) == len(constant_names):
                break
    missing = set(constant_names) - set(found)
    if missing:
        raise RuntimeError(
            f"Could not extract constants from {module_path}: {missing}"
        )
    return found


_BACKEND_CONSTANTS = _extract_constants(
    "backend.py",
    ["_TCS_HISTORY_MAX_BYTES", "_TCS_HISTORY_BACKUP_COUNT"],
)
_APP_CONSTANTS = _extract_constants(
    "app.py",
    ["_BACKFILL_RUN_HISTORY_MAX_BYTES", "_BACKFILL_RUN_HISTORY_BACKUP_COUNT"],
)

_TCS_HISTORY_MAX_BYTES    = _BACKEND_CONSTANTS["_TCS_HISTORY_MAX_BYTES"]
_TCS_HISTORY_BACKUP_COUNT = _BACKEND_CONSTANTS["_TCS_HISTORY_BACKUP_COUNT"]

_BACKFILL_RUN_HISTORY_MAX_BYTES    = _APP_CONSTANTS["_BACKFILL_RUN_HISTORY_MAX_BYTES"]
_BACKFILL_RUN_HISTORY_BACKUP_COUNT = _APP_CONSTANTS["_BACKFILL_RUN_HISTORY_BACKUP_COUNT"]


# ---------------------------------------------------------------------------
# AST helpers for call-site verification
# ---------------------------------------------------------------------------

def _find_rotate_log_calls(source_path: str) -> list[ast.Call]:
    """Return all ast.Call nodes where the called name is '_rotate_log'."""
    with open(source_path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=source_path)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "_rotate_log":
                calls.append(node)
    return calls


def _arg_name(arg_node: ast.expr) -> str | None:
    """Extract the name of a simple Name or Attribute node, else None."""
    if isinstance(arg_node, ast.Name):
        return arg_node.id
    if isinstance(arg_node, ast.Attribute):
        return arg_node.attr
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: str, size: int) -> None:
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


# ===========================================================================
# 1. Constant correctness
# ===========================================================================

class TestTCSHistoryConstantsArePresent(unittest.TestCase):
    """_TCS_HISTORY_* constants extracted from backend.py are sane values."""

    def test_max_bytes_is_positive_int(self):
        self.assertIsInstance(_TCS_HISTORY_MAX_BYTES, int)
        self.assertGreater(_TCS_HISTORY_MAX_BYTES, 0)

    def test_backup_count_is_positive_int(self):
        self.assertIsInstance(_TCS_HISTORY_BACKUP_COUNT, int)
        self.assertGreater(_TCS_HISTORY_BACKUP_COUNT, 0)

    def test_max_bytes_default_is_500kb(self):
        """Default must be 500 KB unless overridden by env var."""
        self.assertEqual(_TCS_HISTORY_MAX_BYTES, _parse_int_env("TCS_HISTORY_MAX_BYTES", 500 * 1024))

    def test_backup_count_default_is_one(self):
        self.assertEqual(_TCS_HISTORY_BACKUP_COUNT, _parse_int_env("TCS_HISTORY_BACKUP_COUNT", 1))


class TestBackfillRunHistoryConstantsArePresent(unittest.TestCase):
    """_BACKFILL_RUN_HISTORY_* constants extracted from app.py are sane values."""

    def test_max_bytes_is_positive_int(self):
        self.assertIsInstance(_BACKFILL_RUN_HISTORY_MAX_BYTES, int)
        self.assertGreater(_BACKFILL_RUN_HISTORY_MAX_BYTES, 0)

    def test_backup_count_is_positive_int(self):
        self.assertIsInstance(_BACKFILL_RUN_HISTORY_BACKUP_COUNT, int)
        self.assertGreater(_BACKFILL_RUN_HISTORY_BACKUP_COUNT, 0)

    def test_max_bytes_default_is_100kb(self):
        """Default must be 100 KB unless overridden by env var."""
        self.assertEqual(
            _BACKFILL_RUN_HISTORY_MAX_BYTES,
            _parse_int_env("BACKFILL_RUN_HISTORY_MAX_BYTES", 100 * 1024),
        )

    def test_backup_count_default_is_one(self):
        self.assertEqual(
            _BACKFILL_RUN_HISTORY_BACKUP_COUNT,
            _parse_int_env("BACKFILL_RUN_HISTORY_BACKUP_COUNT", 1),
        )


# ===========================================================================
# 2. Call-site argument order (AST-based)
# ===========================================================================

class TestBackendCallSiteArgumentOrder(unittest.TestCase):
    """The _rotate_log call in backend.py must pass (path, MAX_BYTES, BACKUP_COUNT)."""

    @classmethod
    def setUpClass(cls):
        calls = _find_rotate_log_calls("backend.py")
        # Identify the TCS history call site: second positional arg should be
        # the max-bytes constant for TCS history.
        cls._tcs_call = next(
            (c for c in calls if _arg_name(c.args[1]) == "_TCS_HISTORY_MAX_BYTES"),
            None,
        )

    def test_tcs_history_call_site_exists(self):
        self.assertIsNotNone(
            self._tcs_call,
            "backend.py must contain a _rotate_log(…, _TCS_HISTORY_MAX_BYTES, …) call",
        )

    def test_tcs_history_max_bytes_is_second_argument(self):
        self.assertEqual(_arg_name(self._tcs_call.args[1]), "_TCS_HISTORY_MAX_BYTES")

    def test_tcs_history_backup_count_is_third_argument(self):
        self.assertEqual(
            _arg_name(self._tcs_call.args[2]),
            "_TCS_HISTORY_BACKUP_COUNT",
            "Third argument to _rotate_log must be _TCS_HISTORY_BACKUP_COUNT, not the max-bytes constant",
        )

    def test_tcs_call_has_exactly_three_positional_args(self):
        self.assertEqual(len(self._tcs_call.args), 3)


class TestAppCallSiteArgumentOrder(unittest.TestCase):
    """The backfill-run-history _rotate_log call in app.py must pass the correct constants."""

    @classmethod
    def setUpClass(cls):
        calls = _find_rotate_log_calls("app.py")
        cls._backfill_run_call = next(
            (c for c in calls if _arg_name(c.args[1]) == "_BACKFILL_RUN_HISTORY_MAX_BYTES"),
            None,
        )

    def test_backfill_run_history_call_site_exists(self):
        self.assertIsNotNone(
            self._backfill_run_call,
            "app.py must contain a _rotate_log(…, _BACKFILL_RUN_HISTORY_MAX_BYTES, …) call",
        )

    def test_backfill_run_history_max_bytes_is_second_argument(self):
        self.assertEqual(_arg_name(self._backfill_run_call.args[1]), "_BACKFILL_RUN_HISTORY_MAX_BYTES")

    def test_backfill_run_history_backup_count_is_third_argument(self):
        self.assertEqual(
            _arg_name(self._backfill_run_call.args[2]),
            "_BACKFILL_RUN_HISTORY_BACKUP_COUNT",
            "Third argument must be _BACKFILL_RUN_HISTORY_BACKUP_COUNT, not the max-bytes constant",
        )

    def test_backfill_run_call_has_exactly_three_positional_args(self):
        self.assertEqual(len(self._backfill_run_call.args), 3)


# ===========================================================================
# 3. Threshold behaviour
# ===========================================================================

class TestTCSHistoryRotationThreshold(unittest.TestCase):
    """_rotate_log honours _TCS_HISTORY_MAX_BYTES / _TCS_HISTORY_BACKUP_COUNT."""

    def test_no_rotation_one_byte_below_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "tcs_history.log")
            _write(log, _TCS_HISTORY_MAX_BYTES - 1)
            _rotate_log(log, _TCS_HISTORY_MAX_BYTES, _TCS_HISTORY_BACKUP_COUNT)
            self.assertTrue(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))

    def test_rotation_fires_at_exact_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "tcs_history.log")
            _write(log, _TCS_HISTORY_MAX_BYTES)
            _rotate_log(log, _TCS_HISTORY_MAX_BYTES, _TCS_HISTORY_BACKUP_COUNT)
            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))

    def test_rotation_fires_above_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "tcs_history.log")
            _write(log, _TCS_HISTORY_MAX_BYTES + 1024)
            _rotate_log(log, _TCS_HISTORY_MAX_BYTES, _TCS_HISTORY_BACKUP_COUNT)
            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))

    def test_backup_count_limits_number_of_backups(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "tcs_history.log")
            _write(log + ".1", 10)
            _write(log, _TCS_HISTORY_MAX_BYTES)
            _rotate_log(log, _TCS_HISTORY_MAX_BYTES, _TCS_HISTORY_BACKUP_COUNT)
            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))
            self.assertFalse(os.path.exists(log + ".2"),
                             "backup_count=1 must never create a .2 file")

    def test_backup_preserves_original_content(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "tcs_history.log")
            payload = b"tcs" * (_TCS_HISTORY_MAX_BYTES // 3 + 1)
            with open(log, "wb") as fh:
                fh.write(payload)
            _rotate_log(log, _TCS_HISTORY_MAX_BYTES, _TCS_HISTORY_BACKUP_COUNT)
            with open(log + ".1", "rb") as fh:
                self.assertEqual(fh.read(), payload)

    def test_missing_file_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "tcs_history.log")
            _rotate_log(log, _TCS_HISTORY_MAX_BYTES, _TCS_HISTORY_BACKUP_COUNT)
            self.assertFalse(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))


class TestBackfillRunHistoryRotationThreshold(unittest.TestCase):
    """_rotate_log honours _BACKFILL_RUN_HISTORY_MAX_BYTES / _BACKFILL_RUN_HISTORY_BACKUP_COUNT."""

    def test_no_rotation_one_byte_below_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_run_history.log")
            _write(log, _BACKFILL_RUN_HISTORY_MAX_BYTES - 1)
            _rotate_log(log, _BACKFILL_RUN_HISTORY_MAX_BYTES, _BACKFILL_RUN_HISTORY_BACKUP_COUNT)
            self.assertTrue(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))

    def test_rotation_fires_at_exact_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_run_history.log")
            _write(log, _BACKFILL_RUN_HISTORY_MAX_BYTES)
            _rotate_log(log, _BACKFILL_RUN_HISTORY_MAX_BYTES, _BACKFILL_RUN_HISTORY_BACKUP_COUNT)
            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))

    def test_rotation_fires_above_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_run_history.log")
            _write(log, _BACKFILL_RUN_HISTORY_MAX_BYTES + 512)
            _rotate_log(log, _BACKFILL_RUN_HISTORY_MAX_BYTES, _BACKFILL_RUN_HISTORY_BACKUP_COUNT)
            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))

    def test_backup_count_limits_number_of_backups(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_run_history.log")
            _write(log + ".1", 10)
            _write(log, _BACKFILL_RUN_HISTORY_MAX_BYTES)
            _rotate_log(log, _BACKFILL_RUN_HISTORY_MAX_BYTES, _BACKFILL_RUN_HISTORY_BACKUP_COUNT)
            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))
            self.assertFalse(os.path.exists(log + ".2"),
                             "backup_count=1 must never create a .2 file")

    def test_backup_preserves_original_content(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_run_history.log")
            payload = b"backfill" * (_BACKFILL_RUN_HISTORY_MAX_BYTES // 8)
            with open(log, "wb") as fh:
                fh.write(payload)
            _rotate_log(log, _BACKFILL_RUN_HISTORY_MAX_BYTES, _BACKFILL_RUN_HISTORY_BACKUP_COUNT)
            with open(log + ".1", "rb") as fh:
                self.assertEqual(fh.read(), payload)

    def test_missing_file_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "backfill_run_history.log")
            _rotate_log(log, _BACKFILL_RUN_HISTORY_MAX_BYTES, _BACKFILL_RUN_HISTORY_BACKUP_COUNT)
            self.assertFalse(os.path.exists(log))
            self.assertFalse(os.path.exists(log + ".1"))


# ===========================================================================
# 4. Cross-cutting: thresholds are distinct and ordered as expected
# ===========================================================================

class TestThresholdRelationships(unittest.TestCase):
    """Guard against accidental constant swaps between the two call sites."""

    def test_tcs_threshold_is_larger_than_backfill_threshold(self):
        self.assertGreater(
            _TCS_HISTORY_MAX_BYTES,
            _BACKFILL_RUN_HISTORY_MAX_BYTES,
            "TCS history (500 KB) must be a larger threshold than backfill run history (100 KB)",
        )

    def test_thresholds_are_not_equal(self):
        self.assertNotEqual(_TCS_HISTORY_MAX_BYTES, _BACKFILL_RUN_HISTORY_MAX_BYTES)

    def test_rotation_at_backfill_threshold_does_not_rotate_tcs_size(self):
        """A file exactly at the backfill threshold must not rotate if TCS threshold is used."""
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "crosscheck.log")
            _write(log, _BACKFILL_RUN_HISTORY_MAX_BYTES)
            _rotate_log(log, _TCS_HISTORY_MAX_BYTES, _TCS_HISTORY_BACKUP_COUNT)
            self.assertTrue(
                os.path.exists(log),
                "a file at the backfill threshold must not rotate when TCS constants are used",
            )
            self.assertFalse(os.path.exists(log + ".1"))

    def test_rotation_at_tcs_threshold_does_rotate_with_backfill_constants(self):
        """A file at the TCS threshold must rotate when backfill constants are used."""
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "crosscheck.log")
            _write(log, _TCS_HISTORY_MAX_BYTES)
            _rotate_log(log, _BACKFILL_RUN_HISTORY_MAX_BYTES, _BACKFILL_RUN_HISTORY_BACKUP_COUNT)
            self.assertFalse(os.path.exists(log))
            self.assertTrue(os.path.exists(log + ".1"))


if __name__ == "__main__":
    unittest.main()
