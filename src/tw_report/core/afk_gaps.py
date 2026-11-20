"""Untracked time: the gaps between consecutive AFK events, inside one logical day.

A gap is untracked only when it lies between two AFK events of the same logical day. Time before
the first AFK event, after the last one, or spanning a day boundary is not untracked: it has no
AFK state at all. Logical days start at ``day_start_hour`` in the report timezone ``tz``.
"""

from collections.abc import Iterable
from datetime import date, datetime, timedelta, tzinfo

from aw_core.models import Event

ONE_MICROSECOND = timedelta(microseconds=1)


def logical_day(moment: datetime, day_start_hour: int, tz: tzinfo) -> date:
    """The logical day of ``moment`` in ``tz``, where a day starts at ``day_start_hour``."""
    return (moment.astimezone(tz) - timedelta(hours=day_start_hour)).date()


def untracked_gaps(
    afk_events: Iterable[Event], day_start_hour: int, tz: tzinfo
) -> list[tuple[datetime, datetime]]:
    """Gaps ``(start, end)`` between consecutive AFK events that stay inside one logical day.

    Events may overlap. A gap starts where the covered time ends and stops at the next event start.
    """
    ordered = sorted(afk_events, key=lambda e: e.timestamp)
    gaps: list[tuple[datetime, datetime]] = []
    covered_until: datetime | None = None
    for event in ordered:
        start = event.timestamp
        if (
            covered_until is not None
            and start > covered_until
            and logical_day(covered_until, day_start_hour, tz)
            == logical_day(start - ONE_MICROSECOND, day_start_hour, tz)
        ):
            gaps.append((covered_until, start))
        end = start + event.duration
        if covered_until is None or end > covered_until:
            covered_until = end
    return gaps
