"""
Raw events of a time span, keyed by bucket role.

A slot built by the timeline builder carries the raw events overlapping its span
(``ReportTimelineSlot.events``), so reports can derive categories and window coverage
from its own events instead of rescanning every event of the period.

Keys are role names (``BUCKET_AFK``, ``BUCKET_WINDOW``, ``BUCKET_TASKWARRIOR``) when the
builder fills a slot. The mapping itself is bucket-agnostic: any key works.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aw_report.core.event import Event
from aw_report.core.event_index import EventIndex

EventMap = dict[str, list[Event]]


@dataclass(frozen=True)
class DataValue:
    """One value of a slot key, with the bucket that set it and the interval it applies to.

    A key set by several buckets is kept as a list of these, so no value is overwritten and each
    value still says when it held.
    """

    bucket: str
    value: Any
    start: datetime
    end: datetime


BUCKET_AFK = "afk"
BUCKET_WINDOW = "window"
BUCKET_TASKWARRIOR = "taskwarrior"


def merge_event_maps(*maps: Mapping[str, Iterable[Event]] | None) -> EventMap:
    """Combine bucket -> events maps, keeping order and dropping repeated event objects.

    An event that spans two adjacent slots is referenced by both; after merging those slots
    it must appear once, or per-event totals double count it.
    """
    merged: EventMap = {}
    seen: dict[str, set[int]] = {}
    for event_map in maps:
        if not event_map:
            continue
        for bucket, events in event_map.items():
            bucket_list = merged.setdefault(bucket, [])
            bucket_seen = seen.setdefault(bucket, set())
            for event in events:
                if id(event) in bucket_seen:
                    continue
                bucket_seen.add(id(event))
                bucket_list.append(event)
    return merged


def collect_span_events(
    start: datetime, end: datetime, indexes: Mapping[str, EventIndex[Any]]
) -> EventMap:
    """Events of each indexed bucket overlapping [start, end); empty buckets are omitted."""
    events: EventMap = {}
    for bucket, index in indexes.items():
        hits = index.overlapping(start, end)
        if hits:
            events[bucket] = hits
    return events
