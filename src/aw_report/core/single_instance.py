"""One running aw-report at a time, as aw_client enforced for the old client.

A lock file in the user's cache directory is held with a non-blocking POSIX record lock for
the whole run. A second run finds the lock taken and gets AlreadyRunningError.
"""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

LOCK_DIR_NAME = "aw-report/locks"


class AlreadyRunningError(RuntimeError):
    """Another aw-report run holds the lock."""


def _default_lock_dir() -> Path:
    cache_home = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache_home) / LOCK_DIR_NAME


@contextmanager
def single_instance(name: str, lock_dir: Path | None = None) -> Iterator[None]:
    """Hold the named lock for the duration of the block.

    Raises:
        AlreadyRunningError: If another process holds the lock.
    """
    directory = lock_dir if lock_dir is not None else _default_lock_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handle: IO[str] = open(directory / name, "w")  # noqa: SIM115 (kept open for the lock)
    try:
        fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise AlreadyRunningError(name) from error
    try:
        yield
    finally:
        handle.close()
