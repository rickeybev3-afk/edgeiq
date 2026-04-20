"""
calibrate_squeeze_mult.py  —  DEPRECATED
-----------------------------------------
This script has been retired. Use the unified replacement instead:

    python calibrate_sp_mult.py --pass squeeze

Exiting with a non-zero code so CI and shell scripts surface the error clearly.
"""

import sys

print(
    "ERROR: calibrate_squeeze_mult.py is deprecated and has been retired.\n"
    "Use the unified script instead:\n\n"
    "    python calibrate_sp_mult.py --pass squeeze\n",
    file=sys.stderr,
)
sys.exit(1)
