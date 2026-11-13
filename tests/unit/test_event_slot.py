"""Unit tests for the event-map helpers used by slots (merge_event_maps, collect_span_events)."""

from datetime import datetime, timedelta, timezone

from aw_core.models import Event

from tw_report.core.event_index import EventIndex
from tw_report.core.event_slot import (
    BUCKET_AFK,
    BUCKET_WINDOW,
    collect_span_events,
    merge_event_maps,
)

T0 = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)


def _ev(eid, offset_min=0, minutes=1):
    return Event(
        id=eid,
        timestamp=T0 + timedelta(minutes=offset_min),
        duration=timedelta(minutes=minutes),
        data={},
    )


class TestMergeEventMaps:
    def test_concatenates_per_bucket_in_order(self):
        merged = merge_event_maps({"afk": [_ev(1)]}, None, {"afk": [_ev(2)], "window": [_ev(3)]})
        assert [e.id for e in merged["afk"]] == [1, 2]
        assert [e.id for e in merged["window"]] == [3]

    def test_empty_inputs_give_empty_map(self):
        assert merge_event_maps() == {}
        assert merge_event_maps(None, {}) == {}

    def test_result_is_a_new_dict(self):
        source = {"afk": [_ev(1)]}
        merged = merge_event_maps(source)
        merged["afk"].append(_ev(2))
        assert len(source["afk"]) == 1

    def test_repeated_event_objects_are_kept_once(self):
        shared = _ev(1)
        merged = merge_event_maps({"afk": [shared]}, {"afk": [shared, _ev(2)]})
        assert [e.id for e in merged["afk"]] == [1, 2]


class TestCollectSpanEvents:
    def test_collects_only_overlapping_events_per_bucket(self):
        afk = EventIndex([_ev("a", 0, 100), _ev("b", 500, 10)])
        window = EventIndex([_ev("w", 50, 5)])
        events = collect_span_events(
            T0 + timedelta(minutes=40),
            T0 + timedelta(minutes=120),
            {BUCKET_AFK: afk, BUCKET_WINDOW: window},
        )
        assert [e.id for e in events[BUCKET_AFK]] == ["a"]
        assert [e.id for e in events[BUCKET_WINDOW]] == ["w"]

    def test_empty_buckets_are_omitted(self):
        events = collect_span_events(T0, T0 + timedelta(minutes=10), {"afk": EventIndex([])})
        assert events == {}
