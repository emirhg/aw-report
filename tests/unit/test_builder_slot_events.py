"""
Builder slot events: every slot carries the raw events overlapping its span, and
task-slot categories derive from those events. Results must equal brute-force scans.
"""

from datetime import datetime, timedelta, timezone

from aw_report.core.aw_events import AFKEvent, TaskWarriorEvent, WindowEvent
from aw_report.core.categories import build_categories_from_window_events
from aw_report.core.event_slot import BUCKET_AFK, BUCKET_WINDOW
from aw_report.core.report_slot import ReportTimelineSlot
from aw_report.core.timeslot_builder import build_timeslot_timeline
from aw_report.pipeline.processors import compute_productivity_breakdown

T0 = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
CAT_SCORES = {"Development": 1.0, "Social": -1.0}


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _afk(start, dur, status):
    ev = AFKEvent(timestamp=_at(start), duration=timedelta(minutes=dur))
    ev.data["status"] = status
    return ev


def _window(start, dur, app, category):
    ev = WindowEvent(timestamp=_at(start), duration=timedelta(minutes=dur))
    ev.data.update({"app": app, "title": app + " title", "$category": [category]})
    return ev


def _task(start, dur, project="Work", task="Coding"):
    ev = TaskWarriorEvent(timestamp=_at(start), duration=timedelta(minutes=dur))
    ev.data.update({"project": project, "task": task, "tags": []})
    return ev


def _scenario():
    """A task 10:00-11:00 with AFK, windows crossing slot boundaries, newest-first input."""
    afk = [_afk(0, 30, "not-afk"), _afk(30, 10, "afk"), _afk(40, 20, "not-afk")]
    windows = [
        _window(45, 20, "Editor", "Development"),  # crosses the AFK->active boundary
        _window(10, 15, "Chat", "Social"),
        _window(0, 10, "Editor", "Development"),
        _window(25, 10, "Browser", "Social"),  # crosses into the AFK period
    ]
    windows.reverse()  # AW returns newest first
    tasks = [_task(0, 60)]
    return afk, windows, tasks


def _ids(events):
    return [id(e) for e in events]


def _brute_overlap(events, start, end):
    return [e for e in events if e.timestamp < end and e.timestamp + e.duration > start]


class TestSlotEventsMatchBruteForce:
    def test_each_slot_holds_exactly_the_overlapping_events(self):
        afk, windows, tasks = _scenario()
        slots = build_timeslot_timeline(afk_events=afk, window_events=windows, task_events=tasks)
        assert slots
        for slot in slots:
            assert _ids(slot.events.get(BUCKET_WINDOW, [])) == _ids(
                _brute_overlap(windows, slot.start, slot.end)
            )
            assert _ids(slot.events.get(BUCKET_AFK, [])) == _ids(
                _brute_overlap(afk, slot.start, slot.end)
            )

    def test_task_slot_categories_equal_full_scan(self):
        afk, windows, tasks = _scenario()
        slots = build_timeslot_timeline(afk_events=afk, window_events=windows, task_events=tasks)
        task_slots = [s for s in slots if s.task_event is not None]
        assert task_slots
        for slot in task_slots:
            expected = build_categories_from_window_events(windows, slot.start, slot.end)
            assert slot.categories == expected

    def test_slots_without_window_coverage_have_no_window_events(self):
        afk = [_afk(0, 10, "not-afk")]
        slots = build_timeslot_timeline(afk_events=afk, window_events=[], task_events=[])
        assert all(not s.events.get(BUCKET_WINDOW) for s in slots)


class TestEventsSurviveDictRoundTrip:
    def test_to_dict_and_from_dict_keep_events(self):
        afk, windows, tasks = _scenario()
        slot = next(
            s
            for s in build_timeslot_timeline(
                afk_events=afk, window_events=windows, task_events=tasks
            )
            if s.events.get(BUCKET_WINDOW)
        )
        as_dict = slot.to_dict()
        assert as_dict["events"] is slot.events or as_dict["events"] == slot.events
        restored = ReportTimelineSlot.from_dict(as_dict)
        assert _ids(restored.events[BUCKET_WINDOW]) == _ids(slot.events[BUCKET_WINDOW])

    def test_slots_without_events_keep_the_old_dict_shape(self):
        slot = ReportTimelineSlot(
            start=T0,
            end=T0 + timedelta(minutes=5),
            duration=timedelta(minutes=5),
            actual_duration=timedelta(minutes=5),
        )
        assert "events" not in slot.to_dict()


class TestProductivityFromSlotEvents:
    def test_breakdown_identical_with_slot_events_and_global_list(self):
        afk, windows, tasks = _scenario()
        slots = build_timeslot_timeline(afk_events=afk, window_events=windows, task_events=tasks)
        with_events = compute_productivity_breakdown(slots, windows, CAT_SCORES)

        stripped = []
        for s in slots:
            copy = ReportTimelineSlot.from_dict({**s.to_dict(), "events": {}})
            stripped.append(copy)
        without_events = compute_productivity_breakdown(stripped, windows, CAT_SCORES)

        assert with_events == without_events


class TestTimelineSlotEvents:
    def test_timeline_slot_dict_round_trip_keeps_events(self):
        from aw_report.core.timeline import TimelineSlot

        afk, windows, tasks = _scenario()
        slot = next(
            s
            for s in build_timeslot_timeline(
                afk_events=afk, window_events=windows, task_events=tasks
            )
            if s.events.get(BUCKET_WINDOW)
        )
        timeline_slot = TimelineSlot(
            type="regular",
            start=slot.start,
            end=slot.end,
            project="Work",
            task="Coding",
            duration=slot.duration,
            actual_duration=slot.actual_duration,
            events=slot.events,
        )
        as_dict = timeline_slot.to_dict()
        assert as_dict["events"] == slot.events
        restored = TimelineSlot.from_dict(as_dict)
        assert _ids(restored.events[BUCKET_WINDOW]) == _ids(slot.events[BUCKET_WINDOW])

    def test_timeline_slot_without_events_omits_the_key(self):
        from aw_report.core.timeline import TimelineSlot

        slot = TimelineSlot(
            type="afk",
            start=T0,
            end=T0 + timedelta(minutes=5),
            project="",
            task="",
            duration=timedelta(minutes=5),
            actual_duration=timedelta(0),
        )
        assert "events" not in slot.to_dict()
