"""
calibrate_gap_mult.py  —  thin shim
-------------------------------------
Routes to the unified calibrate_sp_mult.py for the 'gap' (Bullish Break) pass.

    python calibrate_sp_mult.py --pass gap

Note: 'gap' is the anchor pass (1.00× baseline from the 5-year backtest).
Calibration is supported but the multiplier is expected to remain near 1.00×.
"""

import os
import sys
import subprocess

_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibrate_sp_mult.py")


def _build_forwarded(argv: list) -> list:
    """Return the command list that should be executed for the given argv."""
    if len(argv) > 1 and argv[1] == "--self-test":
        return [sys.executable, _script, "--self-test"]
    return [sys.executable, _script, "--pass", "gap"] + argv[1:]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        print("Running calibrate_gap_mult.py routing self-tests...")
        all_ok = True

        target_ok = os.path.isfile(_script)
        print(f"  {'OK  ' if target_ok else 'FAIL'} target script exists: {_script}")
        if not target_ok:
            all_ok = False

        fwd = _build_forwarded(["calibrate_gap_mult.py"])
        expected = [sys.executable, _script, "--pass", "gap"]
        routing_ok = fwd == expected
        print(f"  {'OK  ' if routing_ok else 'FAIL'} bare invocation routes to --pass gap: {fwd[2:]}")
        if not routing_ok:
            all_ok = False

        print()
        if all_ok:
            print("All gap shim self-tests passed.")
        else:
            print("SELF-TEST FAILURES — routing may be broken.")
            sys.exit(1)
        sys.exit(0)

    sys.exit(subprocess.run(_build_forwarded(sys.argv)).returncode)
