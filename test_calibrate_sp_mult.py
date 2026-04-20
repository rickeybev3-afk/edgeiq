"""
Pytest wrapper for calibrate_sp_mult.py --self-test.

Runs the script's built-in self-test suite (deterministic, no Supabase needed)
and asserts it exits cleanly.
"""
import subprocess
import sys


def test_self_test():
    result = subprocess.run(
        [sys.executable, "calibrate_sp_mult.py", "--self-test"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"calibrate_sp_mult.py --self-test exited with code {result.returncode}.\n"
        f"Output:\n{output}"
    )
    assert "FAIL" not in result.stdout, (
        f"One or more self-test cases reported FAIL:\n{result.stdout}"
    )
    assert "All self-tests passed." in result.stdout
    assert "All _apply_to_bot self-tests passed." in result.stdout
    assert "All reset-confirm self-tests passed." in result.stdout
