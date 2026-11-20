"""Session-merging consolidation for the --consolidate flag.

Consecutive slots of the same (project, task) are merged into one slot, even when an AFK gap or
another slot lies between them of a different state; a different task breaks the group.

The merged slot spans from the start of the first slot to the end of the last. Its breakdown is
the sum of the breakdowns of the slots it joins, so totals are unchanged by consolidation. Its
AFK state is None, because the joined slots may differ; the breakdown carries the split.

Example:
  Input:  Task A (1h) -> Task A (gap 30m) -> Task A (1h) -> Task B (1h)
  Output: Task A (2.5h) -> Task B (1h)  [gap is bridged]
"""

from collections.abc import Sequence
from datetime import timedelta

from aw_report.core.event_slot import merge_event_maps
from aw_report.core.report_slot import ReportTimelineSlot
from aw_report.core.time_breakdown import sum_breakdowns
from aw_report.pipeline.row_merge import merge_tagged_data


def consolidate_sessions(slots: Sequence[ReportTimelineSlot]) -> list[ReportTimelineSlot]:
    """Merge runs of consecutive slots that share project and task.

    ``slots`` must be chronologically ordered. Returns one slot per run.
    """
    groups: list[list[ReportTimelineSlot]] = []
    for slot in slots:
        if groups and _same_group(groups[-1][-1], slot):
            groups[-1].append(slot)
        else:
            groups.append([slot])
    return [_merge_group(group) for group in groups]


def _same_group(prev: ReportTimelineSlot, slot: ReportTimelineSlot) -> bool:
    return prev.project == slot.project and prev.task == slot.task


def _merge_group(group: list[ReportTimelineSlot]) -> ReportTimelineSlot:
    if len(group) == 1:
        return group[0]
    first, last = group[0], group[-1]
    return ReportTimelineSlot(
        start=first.start,
        end=last.end,
        duration=last.end - first.start,
        actual_duration=sum((s.actual_duration for s in group), timedelta(0)),
        productive_duration=sum((s.productive_duration for s in group), timedelta(0)),
        task_event=first.task_event,
        events=merge_event_maps(*(s.events for s in group)),
        tags=first.tags,
        categories=first.categories,
        afk_state=None,
        data=merge_tagged_data(s.data for s in group),
        stored_breakdown=sum_breakdowns(s.breakdown for s in group),
        is_consolidated=True,
    )
