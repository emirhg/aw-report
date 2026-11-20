"""
Intersection-slot builder: one slot per interval where the events of every bucket are constant.

Every start and end of every event (AFK, window, TaskWarrior) is a cut. Between two consecutive
cuts the set of active events does not change, so each interval becomes one slot that carries:

- ``afk_state``: the AFK status active in the interval (``afk`` or ``not-afk``); ``untracked``
  when the interval lies in an untracked gap (between two AFK events of the same logical day);
  ``None`` when no AFK data covers it. See core/afk_gaps.py.
- ``data``: the value of each active event, tagged with the bucket that set it and the interval
  it applies to (core/event_slot.py::DataValue). Keys set by two buckets keep both values.
- ``task_event``: the primary active TaskWarrior event (earliest start, then longest). Other
  overlapping tasks stay in ``data``.
- ``events`` and ``categories`` for the window events inside the slot (categories only for
  task-covered slots, as before).

Display rows are merged afterwards (pipeline/row_merge.py); the slots themselves stay exact.
"""

from bisect import bisect_right
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from tw_report.core.afk_gaps import untracked_gaps
from tw_report.core.aw_events import AFKEvent, TaskWarriorEvent, WindowEvent
from tw_report.core.categories import build_categories_from_window_events
from tw_report.core.event_index import EventIndex
from tw_report.core.event_slot import (
    BUCKET_AFK,
    BUCKET_TASKWARRIOR,
    BUCKET_WINDOW,
    DataValue,
    EventMap,
    collect_span_events,
)
from tw_report.core.report_slot import ReportTimelineSlot
from tw_report.core.time_breakdown import AfkState

_DEGENERATE = timedelta(milliseconds=1)


def build_timeslot_timeline(
    afk_events: list[AFKEvent],
    window_events: list[WindowEvent],
    task_events,  # Can be List[TaskWarriorEvent] or List[Event]
    day_start_hour: int = 0,
    tz: tzinfo = UTC,
) -> list[ReportTimelineSlot]:
    """Build the intersection slots of the three sources, sorted by start.

    Pure and deterministic. Callers pre-filter the events to the period. ``day_start_hour`` and
    ``tz`` define the logical days that bound the untracked gaps.

    Returns slots that are non-overlapping and cover every interval where any source has data
    (plus every untracked gap, even when it holds no data).
    """
    if task_events is None:
        task_events = []
    task_events = [_ensure_taskwarrior_event(ev) for ev in task_events]

    gaps = sorted(untracked_gaps(afk_events, day_start_hour, tz))
    gap_starts = [start for start, _ in gaps]

    cuts: set[datetime] = set()
    for ev in [*afk_events, *window_events, *task_events]:
        if ev.duration <= timedelta(0):
            continue  # Skip degenerate/zero-length events
        cuts.add(ev.timestamp)
        cuts.add(ev.timestamp + ev.duration)
    for gap_start, gap_end in gaps:
        cuts.add(gap_start)
        cuts.add(gap_end)

    if len(cuts) < 2:
        return []

    afk_index = EventIndex[AFKEvent](afk_events)
    task_index = EventIndex[TaskWarriorEvent](task_events)
    window_index = EventIndex[WindowEvent](window_events)
    indexes: dict[str, EventIndex[Any]] = {
        BUCKET_AFK: afk_index,
        BUCKET_WINDOW: window_index,
        BUCKET_TASKWARRIOR: task_index,
    }

    ordered = sorted(cuts)
    slots: list[ReportTimelineSlot] = []
    for t_i, t_next in zip(ordered, ordered[1:], strict=False):
        active_afk = afk_index.active_at(t_i)
        active_tasks = task_index.active_at(t_i)
        active_window = window_index.active_at(t_i)
        in_gap = _in_gap(t_i, gaps, gap_starts)

        if t_next - t_i <= _DEGENERATE:
            continue  # Sub-millisecond artifacts at state boundaries carry no meaning
        if not (active_afk or active_tasks or active_window or in_gap):
            continue  # No source has data here, and it is not untracked time

        afk_state = _afk_state(active_afk, in_gap)
        primary_task = _primary_task(active_tasks)
        data = _tagged_data(active_afk, active_window, active_tasks, t_i, t_next)
        span_events = collect_span_events(t_i, t_next, indexes)
        categories = (
            build_categories_from_window_events(span_events.get(BUCKET_WINDOW, []), t_i, t_next)
            if primary_task is not None
            else []
        )
        slots.append(
            _slot(
                t_i,
                t_next,
                afk_state,
                primary_task,
                span_events,
                categories,
                data,
            )
        )
    return slots


def _ensure_taskwarrior_event(ev: Any) -> TaskWarriorEvent:
    if isinstance(ev, TaskWarriorEvent):
        return ev
    tw_event = TaskWarriorEvent(timestamp=ev.timestamp, duration=ev.duration)
    tw_event.data = ev.data.copy() if ev.data else {}
    return tw_event


def _in_gap(
    moment: datetime, gaps: list[tuple[datetime, datetime]], starts: list[datetime]
) -> bool:
    index = bisect_right(starts, moment) - 1
    return index >= 0 and moment < gaps[index][1]


def _afk_state(active_afk: list[AFKEvent], in_gap: bool) -> AfkState | None:
    if active_afk:
        return "afk" if active_afk[0].status == "afk" else "not-afk"
    if in_gap:
        return "untracked"
    return None


def _primary_task(active_tasks: list[TaskWarriorEvent]) -> TaskWarriorEvent | None:
    if not active_tasks:
        return None
    return min(
        active_tasks,
        key=lambda t: (t.timestamp, -t.duration.total_seconds(), t.uuid or f"_id_{id(t)}"),
    )


def _tagged_data(
    active_afk: list[AFKEvent],
    active_window: list[WindowEvent],
    active_tasks: list[TaskWarriorEvent],
    start: datetime,
    end: datetime,
) -> dict[str, list[DataValue]]:
    """Carry the values of every active event; a key set by several buckets keeps all of them."""
    data: dict[str, list[DataValue]] = {}

    def add(key: str, bucket: str, value: Any) -> None:
        if value is None:
            return
        data.setdefault(key, []).append(DataValue(bucket, value, start, end))

    for afk_ev in active_afk:
        add("status", BUCKET_AFK, afk_ev.status)
    for win_ev in active_window:
        add("app", BUCKET_WINDOW, win_ev.data.get("app"))
        add("title", BUCKET_WINDOW, win_ev.data.get("title"))
    for task_ev in active_tasks:
        add("project", BUCKET_TASKWARRIOR, task_ev.project)
        add("task", BUCKET_TASKWARRIOR, task_ev.task)
    return data


def _slot(
    start: datetime,
    end: datetime,
    afk_state: AfkState | None,
    task_event: TaskWarriorEvent | None,
    events: EventMap,
    categories: list[dict],
    data: dict[str, list[DataValue]],
) -> ReportTimelineSlot:
    duration = end - start
    slot = ReportTimelineSlot(
        start=start,
        end=end,
        duration=duration,
        actual_duration=duration,
        task_event=task_event,
        events=events,
        categories=categories,
        afk_state=afk_state,
        data=data,
        tags=list(task_event.tags) if task_event is not None else [],
    )
    # Legacy fields, kept until the consumers move to the breakdown (removed in piece 3H).
    breakdown = slot.breakdown
    slot.actual_duration = (
        breakdown.active
        if task_event is not None
        else (timedelta(0) if afk_state == "afk" else duration)
    )
    slot.afk_duration = breakdown.afk if breakdown.afk > timedelta(0) else None
    slot.offline_extension_duration = (
        breakdown.offline if breakdown.offline > timedelta(0) else None
    )
    slot.event_duration = slot.actual_duration if slot.offline_extension_duration else None
    return slot
