"""Opt-in stage timing for the CLI, controlled by environment variables.

- ``TW_REPORT_PROFILE`` set: write ``[elapsed] stage`` checkpoints to the profile log.
- unset: checkpoints are no-ops and no file is ever touched.

Nothing here runs at import time. The log is truncated on the first checkpoint and
appended to afterwards; no file handle is kept open between checkpoints.
"""

import os
import sys
import time
from typing import Callable, Mapping, Optional

PROFILE_LOG_PATH = "/tmp/tw_report_profile.log"
PROFILE_ENV_VAR = "TW_REPORT_PROFILE"


class StageProfiler:
    """Writes timing checkpoints for named stages of a run."""

    def __init__(
        self,
        enabled: bool,
        log_path: str = PROFILE_LOG_PATH,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._enabled = enabled
        self._log_path = log_path
        self._clock = clock
        self._start = clock()
        self._wrote_any = False

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None, **kwargs) -> "StageProfiler":
        env = os.environ if environ is None else environ
        return cls(enabled=bool(env.get(PROFILE_ENV_VAR)), **kwargs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def checkpoint(self, stage_name: str) -> None:
        """Record that ``stage_name`` was reached, with seconds since profiler creation."""
        if not self._enabled:
            return
        elapsed = self._clock() - self._start
        mode = "a" if self._wrote_any else "w"
        with open(self._log_path, mode, encoding="utf-8") as sink:
            sink.write(f"[{elapsed:7.2f}s] {stage_name}\n")
        self._wrote_any = True


def enable_unbuffered_output(environ: Optional[Mapping[str, str]] = None) -> None:
    """Make stdout/stderr unbuffered when ``TW_REPORT_DEBUG`` is set (for long runs)."""
    env = os.environ if environ is None else environ
    if env.get("TW_REPORT_DEBUG"):
        sys.stdout = os.fdopen(sys.stdout.fileno(), "w", 1)
        sys.stderr = os.fdopen(sys.stderr.fileno(), "w", 1)
