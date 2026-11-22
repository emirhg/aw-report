"""Warnings shown to the user on stderr.

The report prints nothing on a normal run. These messages cover the cases where the output
is partial or a setting was ignored, so they must reach the user.
"""

import sys


def warn(message: str) -> None:
    """Write one warning line to stderr."""
    print(message, file=sys.stderr)
