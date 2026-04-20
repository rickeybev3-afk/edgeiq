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

if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
    forwarded = [sys.executable, _script, "--self-test"]
else:
    forwarded = [sys.executable, _script, "--pass", "gap_down"] + sys.argv[1:]

sys.exit(subprocess.run(forwarded).returncode)
