"""Equivalence tests: EventIndex must return exactly what the brute-force scans return."""

import random
from datetime import datetime, timedelta, timezone

from aw_core.models import Event

from aw_report.core.event_index import EventIndex

T0 = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)


def _ev(eid, offset_s, dur_s):
    return Event(id=eid, timestamp=T0 + timedelta(seconds=offset_s),
                 duration=timedelta(seconds=dur_s), data={})


def _brute_active(events, t):
    ordered = sorted(events, key=lambda e: e.timestamp)  # stable, like the builder
    return [e for e in ordered if e.timestamp <= t < e.timestamp + e.duration]


def _random_events(rng, n, span_s=10_000, max_dur_s=600):
    return [_ev(i, rng.randint(0, span_s), rng.randint(0, max_dur_s)) for i in range(n)]


class TestActiveAt:
    def test_matches_brute_force_on_random_data(self):
        rng = random.Random(1234)
        events = _random_events(rng, 300)
        index = EventIndex(events)
        for _ in range(400):
            t = T0 + timedelta(seconds=rng.randint(-100, 10_700))
            assert [e.id for e in index.active_at(t)] == [e.id for e in _brute_active(events, t)]

    def test_half_open_boundaries(self):
        index = EventIndex([_ev(1, 10, 5)])  # covers [10, 15)
        assert index.active_at(T0 + timedelta(seconds=10))
        assert index.active_at(T0 + timedelta(seconds=14, microseconds=999999))
        assert index.active_at(T0 + timedelta(seconds=15)) == []
        assert index.active_at(T0 + timedelta(seconds=9)) == []

    def test_zero_duration_events_never_active(self):
        index = EventIndex([_ev(1, 10, 0)])
        assert index.active_at(T0 + timedelta(seconds=10)) == []

    def test_one_long_event_does_not_break_correctness(self):
        events = [_ev(1, 0, 86_400)] + [_ev(i, i * 100, 30) for i in range(2, 50)]
        index = EventIndex(events)
        for t_s in (0, 5, 500, 40_000, 86_399, 86_400):
            t = T0 + timedelta(seconds=t_s)
            assert [e.id for e in index.active_at(t)] == [e.id for e in _brute_active(events, t)]

    def test_ties_keep_input_order(self):
        a, b = _ev("a", 10, 60), _ev("b", 10, 60)
        index = EventIndex([b, a])
        assert [e.id for e in index.active_at(T0 + timedelta(seconds=20))] == ["b", "a"]

    def test_empty_index(self):
        assert EventIndex([]).active_at(T0) == []
        assert EventIndex([]).overlapping(T0, T0 + timedelta(hours=1)) == []


class TestOverlapping:
    def _brute(self, events, start, end):
        return [e for e in events if e.timestamp < end and e.timestamp + e.duration > start]

    def test_matches_brute_force_and_keeps_input_order(self):
        rng = random.Random(99)
        events = _random_events(rng, 300)
        rng.shuffle(events)  # newest-first / arbitrary input order, like AW
        index = EventIndex(events)
        for _ in range(300):
            s = rng.randint(-100, 10_000)
            e = s + rng.randint(1, 2000)
            got = index.overlapping(T0 + timedelta(seconds=s), T0 + timedelta(seconds=e))
            want = self._brute(events, T0 + timedelta(seconds=s), T0 + timedelta(seconds=e))
            assert [x.id for x in got] == [x.id for x in want]

    def test_touching_spans_do_not_overlap(self):
        index = EventIndex([_ev(1, 0, 10)])  # [0, 10)
        assert index.overlapping(T0 + timedelta(seconds=10), T0 + timedelta(seconds=20)) == []
        assert index.overlapping(T0 + timedelta(seconds=-5), T0) == []
        assert index.overlapping(T0 + timedelta(seconds=9), T0 + timedelta(seconds=20))

    def test_zero_duration_event_inside_span_is_included(self):
        # Mirrors the categories predicate: ws < end and we > start, with we == ws
        index = EventIndex([_ev(1, 5, 0)])
        assert index.overlapping(T0, T0 + timedelta(seconds=10))
        assert index.overlapping(T0 + timedelta(seconds=5), T0 + timedelta(seconds=10)) == []
