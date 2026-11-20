"""Unit tests for core/afk_gaps.py: untracked time is the gap between two AFK events of one day."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aw_core.models import Event

from tw_report.core.afk_gaps import logical_day, untracked_gaps

TZ = ZoneInfo("America/Mexico_City")  # UTC-6, no DST since 2022


def afk(local_start: datetime, minutes: float) -> Event:
    return Event(timestamp=local_start, duration=timedelta(minutes=minutes), data={"status": "afk"})


def local(*args) -> datetime:
    return datetime(*args, tzinfo=TZ)


def test_gap_between_two_afk_events_in_the_same_day_is_untracked():
    events = [afk(local(2026, 11, 2, 9), 60), afk(local(2026, 11, 2, 11), 30)]
    assert untracked_gaps(events, 0, TZ) == [(local(2026, 11, 2, 10), local(2026, 11, 2, 11))]


def test_time_before_the_first_or_after_the_last_afk_event_is_not_untracked():
    events = [afk(local(2026, 11, 2, 9), 60)]
    assert untracked_gaps(events, 0, TZ) == []


def test_overlapping_events_leave_no_gap():
    events = [afk(local(2026, 11, 2, 9), 60), afk(local(2026, 11, 2, 9, 30), 60)]
    assert untracked_gaps(events, 0, TZ) == []


def test_the_example_from_the_design_gives_only_the_midnight_half_hour():
    # 23:00-00:00 and 00:30-01:00 with a 04:00 day cut: the 00:00-00:30 gap is untracked,
    # while 01:00-04:00 (no AFK event after it) is not.
    events = [
        afk(local(2026, 11, 1, 23), 60),
        afk(local(2026, 11, 2, 0, 30), 30),
    ]
    assert untracked_gaps(events, 4, TZ) == [(local(2026, 11, 2, 0), local(2026, 11, 2, 0, 30))]


def test_a_gap_spanning_the_day_cut_is_not_untracked():
    # 03:00 belongs to the previous logical day (cut at 04:00); 05:00 to the next one.
    events = [afk(local(2026, 11, 2, 2), 60), afk(local(2026, 11, 2, 5), 60)]
    assert untracked_gaps(events, 4, TZ) == []


def test_events_are_taken_in_time_order_not_input_order():
    events = [afk(local(2026, 11, 2, 11), 30), afk(local(2026, 11, 2, 9), 60)]
    assert untracked_gaps(events, 0, TZ) == [(local(2026, 11, 2, 10), local(2026, 11, 2, 11))]


def test_no_events_gives_no_gaps():
    assert untracked_gaps([], 0, TZ) == []


def test_logical_day_respects_the_day_start_and_the_zone():
    assert logical_day(local(2026, 11, 2, 3, 59), 4, TZ).isoformat() == "2026-11-01"
    assert logical_day(local(2026, 11, 2, 4), 4, TZ).isoformat() == "2026-11-02"
    utc_instant = datetime(2026, 11, 2, 5, 0, tzinfo=ZoneInfo("UTC"))  # 23:00 local, Nov 1
    assert logical_day(utc_instant, 0, TZ).isoformat() == "2026-11-01"


def test_an_event_nested_inside_an_earlier_one_does_not_shrink_the_coverage():
    # 09:00-11:00 covers 09:30-10:00 entirely, so the gap is 11:00-12:00 and not 10:00-12:00.
    events = [
        afk(local(2026, 11, 2, 9), 120),
        afk(local(2026, 11, 2, 9, 30), 30),
        afk(local(2026, 11, 2, 12), 30),
    ]
    assert untracked_gaps(events, 0, TZ) == [(local(2026, 11, 2, 11), local(2026, 11, 2, 12))]
