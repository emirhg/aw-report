"""Unit tests for the local ActivityWatch event model (aw_report.core.event).

Each expectation here matches what aw_core.models.Event does; the equivalence was checked on
random events before this model replaced aw_core.
"""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from aw_report.core.event import Event


class TestTimestamp:
    def test_string_is_parsed_and_kept_in_utc(self):
        event = Event(timestamp="2026-11-09T01:04:02+02:00")
        assert event.timestamp == datetime(2026, 11, 8, 23, 4, 2, tzinfo=UTC)
        assert event.timestamp.utcoffset() == timedelta(0)

    def test_truncated_to_milliseconds(self):
        event = Event(timestamp="2026-11-09T01:04:02.123456+00:00")
        assert event.timestamp.microsecond == 123000

    def test_datetime_in_another_zone_is_converted(self):
        local = datetime(2026, 11, 9, 1, 0, tzinfo=timezone(timedelta(hours=-6)))
        assert Event(timestamp=local).timestamp == datetime(2026, 11, 9, 7, 0, tzinfo=UTC)

    def test_naive_string_is_taken_as_utc(self):
        event = Event(timestamp="2026-11-09T01:04:02")
        assert event.timestamp == datetime(2026, 11, 9, 1, 4, 2, tzinfo=UTC)

    def test_naive_datetime_is_taken_as_utc(self):
        event = Event(timestamp=datetime(2026, 11, 9, 1, 4, 2))
        assert event.timestamp == datetime(2026, 11, 9, 1, 4, 2, tzinfo=UTC)

    def test_missing_timestamp_uses_now(self):
        before = datetime.now(UTC)
        event = Event()
        # truncated to milliseconds, so the lower bound is "before" truncated the same way
        floor = before.replace(microsecond=before.microsecond // 1000 * 1000)
        assert floor <= event.timestamp <= datetime.now(UTC)


class TestDuration:
    def test_timedelta_is_kept(self):
        assert Event(duration=timedelta(minutes=2)).duration == timedelta(minutes=2)

    @pytest.mark.parametrize("seconds", [0, 61, 61.5])
    def test_numbers_are_seconds(self, seconds):
        assert Event(duration=seconds).duration == timedelta(seconds=seconds)

    def test_default_is_zero(self):
        assert Event().duration == timedelta(0)

    def test_other_types_are_rejected(self):
        with pytest.raises(TypeError, match="invalid type"):
            Event(duration="10")


class TestData:
    def test_default_is_an_empty_dict(self):
        assert Event().data == {}

    def test_given_data_is_kept(self):
        assert Event(data={"app": "Code"}).data == {"app": "Code"}


class TestEquality:
    def test_same_timestamp_duration_and_data_are_equal_whatever_the_id(self):
        a = Event(id=1, timestamp="2026-11-09T01:00:00+00:00", duration=5, data={"x": 1})
        b = Event(id=2, timestamp="2026-11-09T01:00:00+00:00", duration=5, data={"x": 1})
        assert a == b

    def test_different_data_is_not_equal(self):
        a = Event(timestamp="2026-11-09T01:00:00+00:00", data={"x": 1})
        b = Event(timestamp="2026-11-09T01:00:00+00:00", data={"x": 2})
        assert a != b

    def test_comparing_with_a_non_event_raises(self):
        with pytest.raises(TypeError):
            Event() == {"id": None}

    def test_ordering_by_timestamp(self):
        early = Event(timestamp="2026-11-09T01:00:00+00:00")
        late = Event(timestamp="2026-11-09T02:00:00+00:00")
        assert early < late
        with pytest.raises(TypeError):
            early < 3  # noqa: B015


class TestJson:
    def test_json_dict_has_utc_iso_timestamp_and_seconds(self):
        event = Event(
            id=7,
            timestamp="2026-11-09T01:04:02+02:00",
            duration=timedelta(seconds=61, milliseconds=500),
            data={"app": "Code"},
        )
        assert event.to_json_dict() == {
            "id": 7,
            "timestamp": "2026-11-08T23:04:02+00:00",
            "duration": 61.5,
            "data": {"app": "Code"},
        }

    def test_json_keys_keep_the_aw_core_order(self):
        event = Event(id=1, timestamp="2026-11-09T01:00:00+00:00", duration=1, data={})
        assert list(event.to_json_dict()) == ["id", "timestamp", "duration", "data"]

    def test_json_string_is_the_json_dict(self):
        event = Event(id=1, timestamp="2026-11-09T01:00:00+00:00", duration=1, data={"a": 1})
        assert json.loads(event.to_json_str()) == event.to_json_dict()

    def test_the_event_stays_a_dict_with_no_aw_core_dependency(self):
        event = Event(id=1, timestamp="2026-11-09T01:00:00+00:00", duration=1, data={})
        assert isinstance(event, dict)
        assert set(event) == {"id", "timestamp", "duration", "data"}
