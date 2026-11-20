"""Coverage tests for timeslot-builder edge paths and the UUID-filter fallback."""

import sys
from datetime import datetime, timedelta, timezone

import aw_client  # noqa: F401  (import the real client chain before tests poison aw_transform)
import pytest
from aw_core.models import Event

import aw_report.core.events  # noqa: F401
from aw_report.core.aw_events import AFKEvent, WindowEvent
from aw_report.core.task_uuid_filtering import get_events_by_uuid
from aw_report.core.timeslot_builder import build_timeslot_timeline

UTC = timezone.utc
T0 = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)


def afk_event(offset_s, duration_s, status):
    return AFKEvent(
        timestamp=T0 + timedelta(seconds=offset_s),
        duration=timedelta(seconds=duration_s),
        data={"status": status},
    )


class _FakeClient:
    def __init__(self, events):
        self._events = events

    def get_events(self, bucket_id, start=None, end=None, limit=-1):
        return list(self._events)


def test_uuid_filter_keeps_only_the_matching_uuid():
    events = [
        Event(timestamp=T0, duration=timedelta(minutes=1), data={"uuid": "abc"}),
        Event(timestamp=T0, duration=timedelta(minutes=1), data={"uuid": "other"}),
    ]
    result = get_events_by_uuid(_FakeClient(events), "tw", T0, T0 + timedelta(hours=1), uuid="abc")
    assert [e.data["uuid"] for e in result] == ["abc"]


def test_builder_accepts_missing_task_events():
    slots = build_timeslot_timeline(
        afk_events=[afk_event(0, 600, "not-afk")],
        window_events=[],
        task_events=None,
    )
    assert slots
    assert all(s.task_event is None for s in slots)


def test_span_between_afk_events_is_untracked_time():
    # Window activity and AFK cover 0-10 min; AFK resumes at 30 min. The 10-30 min span lies
    # between two AFK events of one day, so it is untracked time: slots, not invented data.
    afk = [
        afk_event(0, 600, "not-afk"),
        afk_event(1800, 600, "not-afk"),
    ]
    window = [
        WindowEvent(
            timestamp=T0, duration=timedelta(seconds=600), data={"app": "VSCode", "title": "x"}
        ),
    ]
    slots = build_timeslot_timeline(afk_events=afk, window_events=window, task_events=[])
    gap_start, gap_end = T0 + timedelta(minutes=10), T0 + timedelta(minutes=30)
    gap_slots = [s for s in slots if s.start >= gap_start and s.end <= gap_end]
    assert gap_slots
    assert all(s.afk_state == "untracked" for s in gap_slots)
    assert sum((s.duration for s in gap_slots), timedelta(0)) == gap_end - gap_start


def test_builder_returns_nothing_for_sub_millisecond_only_input():
    tiny = [afk_event(0, 0.0005, "not-afk")]
    assert build_timeslot_timeline(afk_events=tiny, window_events=[], task_events=[]) == []


def test_builder_with_no_events_returns_empty_list():
    assert build_timeslot_timeline(afk_events=[], window_events=[], task_events=[]) == []
