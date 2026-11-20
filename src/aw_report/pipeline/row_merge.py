"""Display-only merge of adjacent slots into report rows.

Slots are exact: one slot per interval where the active events of every bucket are constant,
so a task split by a window change is several slots. The report shows one row per coherent
stretch of work instead. Two adjacent slots join a row when they share project, task, AFK state
and logical day, and they touch (no gap). Gaps and day cuts always break a row.

Totals are never computed from rows: the merged row carries the sum of its slots' breakdowns,
so totals stay identical with or without the merge.
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, timedelta, tzinfo

from aw_report.core.afk_gaps import logical_day
from aw_report.core.categories import merge_category_trees
from aw_report.core.event_slot import DataValue, merge_event_maps
from aw_report.core.report_slot import ReportTimelineSlot
from aw_report.core.time_breakdown import sum_breakdowns


def merge_display_rows(
    slots: Sequence[ReportTimelineSlot], day_start_hour: int = 0, tz: tzinfo = UTC
) -> list[ReportTimelineSlot]:
    """Merge touching slots that share project, task, AFK state and logical day.

    ``slots`` must be sorted by start. A slot that joins nothing is returned as it is.
    """
    groups: list[list[ReportTimelineSlot]] = []
    for slot in slots:
        if groups and _joins(groups[-1][-1], slot, day_start_hour, tz):
            groups[-1].append(slot)
        else:
            groups.append([slot])
    return [group[0] if len(group) == 1 else _merge_group(group) for group in groups]


def _joins(
    prev: ReportTimelineSlot, nxt: ReportTimelineSlot, day_start_hour: int, tz: tzinfo
) -> bool:
    return (
        prev.end == nxt.start
        and prev.project == nxt.project
        and prev.task == nxt.task
        and prev.afk_state == nxt.afk_state
        and logical_day(prev.start, day_start_hour, tz)
        == logical_day(nxt.start, day_start_hour, tz)
    )


def _merge_group(group: list[ReportTimelineSlot]) -> ReportTimelineSlot:
    first = group[0]
    return ReportTimelineSlot(
        start=first.start,
        end=group[-1].end,
        duration=sum((s.duration for s in group), timedelta(0)),
        actual_duration=sum((s.actual_duration for s in group), timedelta(0)),
        productive_duration=sum((s.productive_duration for s in group), timedelta(0)),
        task_event=first.task_event,
        events=merge_event_maps(*(s.events for s in group)),
        tags=first.tags,
        categories=merge_category_trees(s.categories for s in group),
        afk_state=first.afk_state,
        data=merge_tagged_data(s.data for s in group),
        stored_breakdown=sum_breakdowns(s.breakdown for s in group),
    )


def merge_tagged_data(maps: Iterable[dict[str, list[DataValue]]]) -> dict[str, list[DataValue]]:
    merged: dict[str, list[DataValue]] = {}
    for data in maps:
        for key, values in data.items():
            merged.setdefault(key, []).extend(values)
    return merged
