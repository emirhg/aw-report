"""Bucket resolution: which ActivityWatch buckets feed each data role.

The report uses three fixed roles, each fed by one bucket:

- ``afk``: AFK status (``aw-watcher-afk``)
- ``window``: active window (``aw-watcher-window``)
- ``tasks``: TaskWarrior activity (``aw-watcher-taskwarrior``)

Each role's ID defaults to ``aw-watcher-<watcher>_<hostname>``. The ``[buckets]`` table of the
config file can override the hostname or name the exact bucket ID of a role (needed for
buckets without a hostname suffix). Unknown keys are errors, so a typo cannot silently produce
an empty report.
"""

import platform
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tw_report.core.events import get_bucket_id
from tw_report.exceptions import ConfigParsingError

ROLES: tuple[str, ...] = ("afk", "window", "tasks")
HOSTNAME_KEY = "hostname"

# ActivityWatch watcher name behind each role's default bucket.
WATCHER_FOR_ROLE: dict[str, str] = {"afk": "afk", "window": "window", "tasks": "taskwarrior"}


@dataclass(frozen=True)
class BucketIds:
    """Resolved, full bucket ID for each role."""

    afk: str
    window: str
    tasks: str


def default_bucket_id(role: str, hostname: str | None = None) -> str:
    """Default bucket ID for ``role``, built from the watcher name and hostname."""
    return get_bucket_id(WATCHER_FOR_ROLE[role], hostname=hostname)


def resolve_buckets(config: Mapping[str, Any] | None, hostname: str | None = None) -> BucketIds:
    """Resolve the bucket ID of every role from the ``[buckets]`` config table.

    Args:
        config: The ``buckets`` table of the config file, or ``None`` when absent.
        hostname: Machine name used for default IDs; ``None`` means the current host.

    Raises:
        ConfigParsingError: On unknown keys, non-string values, or empty values.
    """
    table: Mapping[str, Any] = config or {}
    allowed = {HOSTNAME_KEY, *ROLES}
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigParsingError(
            f"Unknown key(s) in [buckets]: {', '.join(unknown)} "
            f"(allowed: {', '.join(sorted(allowed))})"
        )
    for key, value in table.items():
        if not isinstance(value, str) or not value.strip():
            raise ConfigParsingError(f"[buckets] {key} must be a non-empty string")

    effective_host = table.get(HOSTNAME_KEY, hostname)
    if effective_host is None:
        effective_host = platform.node()

    def pick(role: str) -> str:
        if role in table:
            return str(table[role])
        return default_bucket_id(role, hostname=effective_host)

    return BucketIds(afk=pick("afk"), window=pick("window"), tasks=pick("tasks"))
