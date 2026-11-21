"""Value-based event filters, shared by the report and the UUID lookup.

``filter_by_value`` keeps the events whose ``data[key]`` is one of ``values``. An event without
``key`` never matches, so it is kept only when ``exclude`` is True. It replaces the
``filter_keyvals`` helper of the aw_transform package, so tw-report has no dependency on it.
"""

from collections.abc import Collection, Iterable
from typing import Any

from aw_report.core.event import Event


def filter_by_value[E: Event](
    events: Iterable[E],
    key: str,
    values: Collection[Any],
    exclude: bool = False,
) -> list[E]:
    """Events whose ``data[key]`` is in ``values`` (or not in them, when ``exclude``).

    Events without ``key`` in their data do not match, so they are dropped by default and kept
    when ``exclude`` is True. Event order is preserved.
    """
    return [
        event for event in events if (key in event.data and event.data[key] in values) != exclude
    ]
