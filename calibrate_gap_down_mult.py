"""
calibrate_gap_down_mult.py  —  thin shim
-----------------------------------------
This script has been merged into calibrate_sp_mult.py.
Run the unified script instead:

    python calibrate_sp_mult.py --pass gap_down

This shim forwards all arguments so existing invocations keep working.
"""

import os
import sys
import subprocess

_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibrate_sp_mult.py")


def _build_forwarded(argv: list) -> list:
    """Return the command list that should be executed for the given argv."""
    if len(argv) > 1 and argv[1] == "--self-test":
        return [sys.executable, _script, "--self-test"]
    return [sys.executable, _script, "--pass", "gap_down"] + argv[1:]


def _self_test() -> None:
    """Verify that this shim routes correctly to calibrate_sp_mult.py --pass gap_down."""
    all_ok = True

    def _check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal all_ok
        print(f"  {'OK  ' if ok else 'FAIL'} {label}{(': ' + detail) if detail else ''}")
        if not ok:
            all_ok = False

    # Test 1: target script exists on disk
    _check("target script exists", os.path.isfile(_script), _script)

    # Test 2: normal invocation injects --pass gap_down
    fwd = _build_forwarded(["calibrate_gap_down_mult.py"])
    expected = [sys.executable, _script, "--pass", "gap_down"]
    _check(
        "bare invocation routes to --pass gap_down",
        fwd == expected,
        str(fwd[2:]),
    )

    # Test 3: extra user args are appended after --pass gap_down
    fwd = _build_forwarded(["calibrate_gap_down_mult.py", "--foo", "bar"])
    expected = [sys.executable, _script, "--pass", "gap_down", "--foo", "bar"]
    _check(
        "extra args appended after --pass gap_down",
        fwd == expected,
        str(fwd[2:]),
    )

    # Test 4: --self-test is routed as --self-test, NOT through --pass gap_down
    fwd = _build_forwarded(["calibrate_gap_down_mult.py", "--self-test"])
    expected = [sys.executable, _script, "--self-test"]
    _check(
        "--self-test routed without injecting --pass gap_down",
        fwd == expected,
        str(fwd[2:]),
    )

    # Test 5: downstream calibrate_sp_mult.py --self-test exits 0
    result = subprocess.run(
        [sys.executable, _script, "--self-test"],
        capture_output=True,
        text=True,
    )
    _check(
        "downstream calibrate_sp_mult.py --self-test exits 0",
        result.returncode == 0,
    )
    for line in (result.stdout or "").strip().splitlines():
        print(f"         {line}")

    print()
    if all_ok:
        print("All gap_down shim self-tests passed.")
    else:
        print("SELF-TEST FAILURES — routing may be broken.")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        print("Running calibrate_gap_down_mult.py routing self-tests...")
        _self_test()
        sys.exit(0)

    sys.exit(subprocess.run(_build_forwarded(sys.argv)).returncode)
