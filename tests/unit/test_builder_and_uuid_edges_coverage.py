"""Coverage tests for timeslot-builder edge paths and the UUID-filter fallback."""

import sys
from datetime import datetime, timedelta, timezone

import aw_client  # noqa: F401  (import the real client chain before tests poison aw_transform)
import pytest
from aw_core.models import Event

import tw_report.core.events  # noqa: F401
from tw_report.core.aw_events import AFKEvent, WindowEvent
from tw_report.core.task_uuid_filtering import get_events_by_uuid
from tw_report.core.timeslot_builder import _merge_slot_group, build_timeslot_timeline

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


def test_uuid_filter_falls_back_to_manual_filtering_without_aw_transform(monkeypatch):
    # A None entry in sys.modules makes the import inside the function raise ImportError.
    monkeypatch.setitem(sys.modules, "aw_transform", None)
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


def test_span_without_any_coverage_produces_no_slot():
    # Window activity and AFK cover 0-10 min; AFK resumes at 30 min. Nothing covers 10-30 min,
    # and the builder deliberately emits no slot for uncovered time.
    afk = [
        afk_event(0, 600, "not-afk"),
        afk_event(1800, 600, "not-afk"),
    ]
    window = [
        WindowEvent(timestamp=T0, duration=timedelta(seconds=600),
                    data={"app": "VSCode", "title": "x"}),
    ]
    slots = build_timeslot_timeline(afk_events=afk, window_events=window, task_events=[])
    gap_start, gap_end = T0 + timedelta(minutes=10), T0 + timedelta(minutes=30)
    assert all(s.end <= gap_start or s.start >= gap_end for s in slots)


def test_builder_returns_nothing_for_sub_millisecond_only_input():
    tiny = [afk_event(0, 0.0005, "not-afk")]
    assert build_timeslot_timeline(afk_events=tiny, window_events=[], task_events=[]) == []


def test_builder_with_no_events_returns_empty_list():
    assert build_timeslot_timeline(afk_events=[], window_events=[], task_events=[]) == []


def test_merge_slot_group_rejects_empty_group():
    with pytest.raises(ValueError, match="empty slot group"):
        _merge_slot_group([])
