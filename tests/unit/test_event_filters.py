"""Unit tests for core/event_filters.py (the replacement of aw_transform.filter_keyvals)."""

from datetime import UTC, datetime, timedelta

from aw_core.models import Event

from aw_report.core.event_filters import filter_by_value

T0 = datetime(2026, 11, 12, 6, 0, tzinfo=UTC)


def _ev(data):
    return Event(timestamp=T0, duration=timedelta(minutes=1), data=data)


def test_keeps_events_whose_value_is_listed_in_order():
    events = [_ev({"status": "afk"}), _ev({"status": "not-afk"}), _ev({"status": "not-afk"})]
    kept = filter_by_value(events, "status", ["not-afk"])
    assert kept == [events[1], events[2]]
    assert kept[0] is events[1]  # the same objects, not copies


def test_an_event_without_the_key_does_not_match():
    events = [_ev({"status": "afk"}), _ev({"other": 1})]
    assert filter_by_value(events, "status", ["afk"]) == [events[0]]


def test_exclude_inverts_the_match_and_keeps_events_without_the_key():
    events = [_ev({"status": "afk"}), _ev({"status": "not-afk"}), _ev({"other": 1})]
    kept = filter_by_value(events, "status", ["not-afk"], exclude=True)
    assert kept == [events[0], events[2]]


def test_empty_values_match_nothing():
    events = [_ev({"status": "afk"})]
    assert filter_by_value(events, "status", []) == []
    assert filter_by_value(events, "status", [], exclude=True) == events


def test_accepts_any_iterable_of_events():
    events = [_ev({"uuid": "a"}), _ev({"uuid": "b"})]
    assert [e.data["uuid"] for e in filter_by_value(iter(events), "uuid", ["b"])] == ["b"]
