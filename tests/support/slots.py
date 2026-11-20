"""Builders for ReportTimelineSlot objects used by the intersection-slot tests."""

from datetime import UTC, datetime, timedelta

from tw_report.core.aw_events import TaskWarriorEvent
from tw_report.core.report_slot import ReportTimelineSlot
from tw_report.core.time_breakdown import AfkState

BASE = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def slot(
    start_minute: int,
    minutes: int,
    state: AfkState | None = None,
    *,
    project: str = "Climb",
    task: str = "Task1",
    offline: bool = False,
    categories: list[dict] | None = None,
) -> ReportTimelineSlot:
    """A slot starting ``start_minute`` after 09:00 UTC on 2026-11-02, with the given AFK state."""
    duration = timedelta(minutes=minutes)
    start = BASE + timedelta(minutes=start_minute)
    tags = ["offline"] if offline else []
    task_event = TaskWarriorEvent(
        timestamp=start,
        duration=duration,
        data={"project": project, "title": task, "tags": tags},
    )
    return ReportTimelineSlot(
        start=start,
        end=start + duration,
        duration=duration,
        actual_duration=duration,
        task_event=task_event,
        afk_state=state,
        categories=categories or [],
    )
