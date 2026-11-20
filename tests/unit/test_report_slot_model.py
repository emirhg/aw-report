"""Stage 2 of the intersection-slot model: AFK state, breakdown, entry type and the slot filter."""

from datetime import UTC, datetime, timedelta

from tw_report.core.aw_events import TaskWarriorEvent
from tw_report.core.filtering import EventFilter
from tw_report.core.report_slot import ReportTimelineSlot
from tw_report.core.time_breakdown import TimeBreakdown

T0 = datetime(2026, 11, 2, 9, tzinfo=UTC)
TEN = timedelta(minutes=10)


def task(tags=(), project="Climb", title="Task1"):
    return TaskWarriorEvent(
        timestamp=T0,
        duration=TEN,
        data={"project": project, "title": title, "tags": list(tags)},
    )


def slot(state=None, tags=(), project="Climb", title="Task1", categories=None):
    return ReportTimelineSlot(
        start=T0,
        end=T0 + TEN,
        duration=TEN,
        actual_duration=TEN,
        task_event=task(tags, project, title),
        afk_state=state,
        categories=categories or [],
    )


def test_breakdown_follows_the_afk_state():
    assert slot("not-afk").breakdown == TimeBreakdown(active=TEN)
    assert slot("afk").breakdown == TimeBreakdown(afk=TEN)
    assert slot("untracked").breakdown == TimeBreakdown(untracked=TEN)
    assert slot(None).breakdown == TimeBreakdown(no_afk_data=TEN)


def test_untracked_offline_tagged_task_is_offline():
    assert slot("untracked", tags=["Offline"]).breakdown == TimeBreakdown(offline=TEN)


def test_offline_tag_is_case_insensitive_and_needs_a_task():
    assert slot("untracked", tags=["OFFLINE"]).offline_tagged
    assert not slot("untracked", tags=["work"]).offline_tagged
    no_task = ReportTimelineSlot(start=T0, end=T0 + TEN, duration=TEN, actual_duration=TEN)
    assert not no_task.offline_tagged


def test_stored_breakdown_overrides_the_derived_one():
    stored = TimeBreakdown(active=TEN, afk=TEN)
    consolidated = ReportTimelineSlot(
        start=T0,
        end=T0 + timedelta(minutes=20),
        duration=timedelta(minutes=20),
        actual_duration=TEN,
        stored_breakdown=stored,
    )
    assert consolidated.breakdown == stored


def test_entry_type_for_afk_offline_and_regular():
    assert slot("afk").entry_type == "afk"
    assert slot("untracked", tags=["offline"]).entry_type == "offline_task"
    assert slot("not-afk").entry_type == "regular"


def test_dict_round_trip_keeps_state_and_stored_breakdown():
    original = ReportTimelineSlot(
        start=T0,
        end=T0 + TEN,
        duration=TEN,
        actual_duration=TEN,
        afk_state="afk",
        stored_breakdown=TimeBreakdown(afk=TEN),
    )
    restored = ReportTimelineSlot.from_dict(original.to_dict())
    assert restored.afk_state == "afk"
    assert restored.stored_breakdown == TimeBreakdown(afk=TEN)


def test_dict_without_state_keeps_the_old_shape():
    plain = ReportTimelineSlot(start=T0, end=T0 + TEN, duration=TEN, actual_duration=TEN)
    d = plain.to_dict()
    assert "afk_state" not in d
    assert "breakdown" not in d


def test_filter_accepts_slot_uses_its_apps_for_the_app_filter():
    cats = [{"category": "Dev", "apps": [{"app": "Code", "duration": TEN}]}]
    flt = EventFilter(app_patterns=["Code"])
    assert flt.accepts_slot(slot("not-afk", categories=cats))
    assert not EventFilter(app_patterns=["Firefox"]).accepts_slot(slot("not-afk", categories=cats))


def test_filter_accepts_slot_drops_excluded_projects():
    flt = EventFilter(exclude_projects=["Climb"])
    assert not flt.accepts_slot(slot("not-afk", project="Climb"))
    assert flt.accepts_slot(slot("not-afk", project="Other"))
