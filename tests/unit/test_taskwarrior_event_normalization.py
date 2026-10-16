"""TaskWarriorEvent reports untracked time with the standard sentinels."""

from datetime import datetime, timedelta, timezone

from tw_report.core.aw_events import TaskWarriorEvent
from tw_report.core.filtering import NO_PROJECT, NO_TASK


def _event(data):
    return TaskWarriorEvent(
        timestamp=datetime(2026, 10, 15, 3, 59, tzinfo=timezone.utc),
        duration=timedelta(minutes=3),
        data=data,
    )


def test_missing_project_defaults_to_no_project():
    assert _event({"title": "Task A"}).project == NO_PROJECT


def test_raw_no_project_string_normalized():
    assert _event({"project": "No project", "title": "Task A"}).project == NO_PROJECT


def test_no_task_tracked_title_normalized_to_no_task():
    assert _event({"title": "No task tracked"}).task == NO_TASK


def test_missing_title_and_uuid_is_no_task():
    assert _event({}).task == NO_TASK


def test_real_task_and_project_unchanged():
    event = _event({"project": "Climb", "title": "Merge vs Rebase"})
    assert event.project == "Climb"
    assert event.task == "Merge vs Rebase"
