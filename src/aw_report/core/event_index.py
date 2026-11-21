"""
Interval index over ActivityWatch events, for fast span and instant lookups.

Replaces full linear scans such as ``[e for e in events if e.timestamp <= t < e.end]``,
which cost O(events) per query. Events are sorted once by start time; a query only visits
the events whose start lies in the window that can possibly match, bounded by the longest
event duration in the index. A single very long event widens that window for every query,
so the index degrades gracefully rather than failing.

Two queries are provided:

- ``active_at(t)``: events with ``ts <= t < ts + duration``, in start order (stable on equal
  timestamps), which is the order the slot builder relies on.
- ``overlapping(start, end)``: events with ``ts < end and ts + duration > start`` (the predicate
  of ``build_categories_from_window_events``), in the ORIGINAL input order, because category
  and app insertion order, and therefore report row order, depends on it.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from datetime import datetime, timedelta

from aw_report.core.event import Event


class EventIndex[E: Event]:
    """Immutable interval index over a list of events of one type."""

    def __init__(self, events: Iterable[E]):
        self._events: list[E] = list(events)
        # Stable sort by start: ties keep their input order (matches sorted(..., key=timestamp))
        self._order: list[int] = sorted(
            range(len(self._events)), key=lambda i: (self._events[i].timestamp, i)
        )
        self._starts: list[datetime] = [self._events[i].timestamp for i in self._order]
        # End times in the same order, computed once: the queries below run per slot tick
        # and per span, and re-reading timestamp + duration through aw_core is the hot cost.
        self._ends: list[datetime] = [
            self._events[i].timestamp + self._events[i].duration for i in self._order
        ]
        self._max_duration: timedelta = max(
            (e.duration for e in self._events), default=timedelta(0)
        )

    def active_at(self, moment: datetime) -> list[E]:
        """Events covering ``moment`` (``ts <= moment < ts + duration``), in start order."""
        lo = bisect_left(self._starts, moment - self._max_duration)
        hi = bisect_right(self._starts, moment)
        ends = self._ends
        result: list[E] = []
        for pos in range(lo, hi):
            if moment < ends[pos]:
                result.append(self._events[self._order[pos]])
        return result

    def overlapping(self, start: datetime, end: datetime) -> list[E]:
        """Events with ``ts < end and ts + duration > start``, in original input order."""
        lo = bisect_left(self._starts, start - self._max_duration)
        hi = bisect_left(self._starts, end)
        ends = self._ends
        hit_positions = []
        for pos in range(lo, hi):
            if ends[pos] > start:
                hit_positions.append(self._order[pos])
        hit_positions.sort()
        return [self._events[i] for i in hit_positions]
