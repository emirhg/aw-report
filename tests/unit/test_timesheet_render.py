"""Unit tests for the text timesheet renderer (pipeline/timesheet_render.py).

Everything is pinned to an explicit zone (America/Mexico_City, UTC-6, no DST since 2022), so the
results do not depend on the machine's TZ.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from aw_core.models import Event

from aw_report.core.filtering import NO_TASK
from aw_report.pipeline.timesheet_render import (
    bucket_key,
    bucket_starts,
    build_timesheet,
    format_timesheet,
    print_timesheet_report,
    clock,
)

TZ = ZoneInfo("America/Mexico_City")


def local(*args) -> datetime:
    return datetime(*args, tzinfo=TZ)


def task(start_utc: datetime, minutes: float, project="P", title="T") -> Event:
    return Event(
        timestamp=start_utc,
        duration=timedelta(minutes=minutes),
        data={"project": project, "title": title},
    )


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


class TestClock:
    def test_rounds_to_the_minute_halves_up(self):
        assert clock(0) == "00:00"
        assert clock(450) == "00:08"  # 7.5 min rounds up
        assert clock(449) == "00:07"

    def test_hours_and_minutes_with_two_digit_fields(self):
        assert clock(3600 * 2.5) == "02:30"
        assert clock(3600 * 1.07) == "01:04"  # the 1.07 h of the report, to the minute
        assert clock(3600 * 27 + 59 * 60) == "27:59"  # totals can pass 24 h


class TestBuckets:
    def test_day_key_respects_day_start(self):
        assert bucket_key(local(2026, 11, 2, 3, 0), "day", 4) == date(2026, 11, 1)
        assert bucket_key(local(2026, 11, 2, 5, 0), "day", 4) == date(2026, 11, 2)

    def test_week_starts_on_monday(self):
        assert bucket_key(local(2026, 11, 4), "week", 0) == date(2026, 11, 2)
        assert bucket_key(local(2026, 11, 2), "week", 0) == date(2026, 11, 2)

    def test_month_and_year_keys(self):
        assert bucket_key(local(2026, 11, 19), "month", 0) == date(2026, 11, 1)
        assert bucket_key(local(2026, 11, 19), "year", 0) == date(2026, 1, 1)

    def test_bucket_starts_cross_year_end(self):
        starts = bucket_starts(local(2026, 11, 1), local(2027, 2, 1), "month", 0)
        assert starts == [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)]


class TestBuildTimesheet:
    def test_event_crossing_midnight_is_clipped_into_both_days(self):
        # 23:00 to 01:00 local, so one hour on each side of midnight
        events = [task(utc(2026, 11, 3, 5, 0), 120)]
        periods = build_timesheet(
            events, "day", local(2026, 11, 2), local(2026, 11, 3, 23, 59), 0, TZ
        )
        assert [p.tracked_seconds for p in periods] == [3600.0, 3600.0]
        assert [p.tasks[0].count for p in periods] == [1, 1]

    def test_tasks_grouped_by_title_with_first_project_and_none_first(self):
        events = [
            task(utc(2026, 11, 2, 15, 0), 30, project="A", title="Write"),
            task(utc(2026, 11, 2, 16, 0), 30, project="B", title="Write"),
            task(utc(2026, 11, 2, 17, 0), 30, project="C", title=None),
        ]
        (period,) = build_timesheet(
            events, "day", local(2026, 11, 2), local(2026, 11, 2, 23), 0, TZ
        )
        assert [(t.project, t.title, t.count) for t in period.tasks] == [
            ("C", NO_TASK, 1),
            ("A", "Write", 2),
        ]

    def test_empty_periods_are_omitted(self):
        events = [task(utc(2026, 11, 4, 15, 0), 30)]
        periods = build_timesheet(events, "day", local(2026, 11, 2), local(2026, 11, 4, 23), 0, TZ)
        assert [p.label for p in periods] == ["Wednesday, Nov 04, 2026"]

    def test_event_outside_the_range_gives_no_period(self):
        assert build_timesheet([], "day", local(2026, 11, 2), local(2026, 11, 2, 23), 0, TZ) == []

    def test_week_label_spans_first_and_last_day(self):
        events = [task(utc(2026, 11, 2, 15, 0), 30)]
        (period,) = build_timesheet(
            events, "week", local(2026, 11, 2), local(2026, 11, 8, 23), 0, TZ
        )
        assert period.label == "Monday, Nov 02, 2026 - Sunday, Nov 08, 2026"

    def test_month_label_spans_the_whole_month(self):
        events = [task(utc(2026, 11, 2, 15, 0), 30)]
        (period,) = build_timesheet(
            events, "month", local(2026, 11, 1), local(2026, 11, 30, 23), 0, TZ
        )
        assert period.label == "Sunday, Nov 01, 2026 - Monday, Nov 30, 2026"


class TestFormat:
    def test_exact_text_of_a_single_day(self):
        events = [task(utc(2026, 11, 2, 21, 0), 30, project="Proj", title="Write docs")]
        periods = build_timesheet(events, "day", local(2026, 11, 2), local(2026, 11, 2, 23), 0, TZ)
        rule = "=" * 78
        assert format_timesheet(periods, "day", TZ) == [
            rule,
            f"{'TIMESHEET DAILY ACTIVITY REPORT':^78}",
            rule,
            "",
            "\U0001f4c5 Monday, Nov 02, 2026",
            "   Check-in: 03:00 PM | Check-out: 03:30 PM | Wall Clock: 00:30",
            "   Active: -- | Tracked: 00:30 | Untracked: 00:00",
            "-" * 78,
            "    Project              | Task Title                             | Hours  | Count",
            "    -------------------- | -------------------------------------- | ------ | -----",
            "    Proj                 | Write docs                             |  00:30 |     1",
            "",
            rule,
            " GRAND TOTALS -- Active: -- | Tracked: 00:30 | Untracked: 00:00 | Wall Clock: 00:30",
            " Across 1 active days",
            rule,
        ]

    def test_long_titles_are_cut_to_35_characters_and_dots(self):
        title = "x" * 50
        events = [task(utc(2026, 11, 2, 21, 0), 30, title=title)]
        periods = build_timesheet(events, "day", local(2026, 11, 2), local(2026, 11, 2, 23), 0, TZ)
        (row,) = [line for line in format_timesheet(periods, "day", TZ) if "xxx" in line]
        assert "x" * 35 + "..." in row
        assert "x" * 36 not in row

    def test_footer_noun_follows_the_mode(self):
        events = [task(utc(2026, 11, 2, 21, 0), 30)]
        periods = build_timesheet(events, "week", local(2026, 11, 2), local(2026, 11, 8, 23), 0, TZ)
        assert format_timesheet(periods, "week", TZ)[-2] == " Across 1 active weeks"

    def test_no_activity_prints_header_and_zero_totals(self):
        lines = format_timesheet([], "day", TZ)
        assert lines[-5:] == [
            "",
            "=" * 78,
            " GRAND TOTALS -- Active: -- | Tracked: 00:00 | Untracked: 00:00 | Wall Clock: 00:00",
            " Across 0 active days",
            "=" * 78,
        ]

    def test_print_writes_each_line(self, capsys):
        events = [task(utc(2026, 11, 2, 21, 0), 30)]
        print_timesheet_report(events, "day", local(2026, 11, 2), local(2026, 11, 2, 23), 0, TZ)
        out = capsys.readouterr().out.splitlines()
        assert out[0] == "=" * 78
        assert out[-1] == "=" * 78


@pytest.mark.parametrize("mode", ["day", "week", "month", "year"])
def test_every_mode_builds_without_error(mode):
    events = [task(utc(2026, 11, 2, 21, 0), 30)]
    periods = build_timesheet(events, mode, local(2026, 11, 2), local(2026, 11, 2, 23), 0, TZ)
    assert len(periods) == 1


def _slot(begin, end, active=0.0, events=True, afk=True):
    """A machine span: an event-bearing slot unless ``events`` is False (an empty gap slot)."""
    from aw_report.pipeline.timesheet_render import MachineSlot

    return MachineSlot(
        start=datetime(2026, 8, 30, *begin, tzinfo=UTC),
        end=datetime(2026, 8, 30, *end, tzinfo=UTC),
        active_seconds=active,
        has_events=events,
        has_afk_state=afk,
    )


def _day(machine=None, events=None):
    from aw_report.pipeline.timesheet_render import build_timesheet

    return build_timesheet(
        events or [],
        "day",
        datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 30, 23, 59, tzinfo=UTC),
        0,
        UTC,
        machine,
    )


def test_check_in_and_out_come_from_the_machine_spans_not_the_tasks():
    machine = [
        _slot((8, 30), (8, 50), active=1200.0),
        _slot((9, 0), (12, 0), active=3600.0 * 2),
    ]
    (period,) = _day(machine)
    assert period.check_in == int(datetime(2026, 8, 30, 8, 30, tzinfo=UTC).timestamp())
    assert period.check_out == datetime(2026, 8, 30, 12, 0, tzinfo=UTC).timestamp()
    assert period.wall_seconds == 3.5 * 3600


def test_tracked_is_the_event_bearing_time_and_untracked_the_empty_time():
    machine = [
        _slot((9, 0), (10, 0), active=3600.0),  # events, active
        _slot((10, 0), (10, 30), active=1800.0),  # events, active
        _slot((10, 30), (11, 30), active=0.0),  # events, AFK (no active time)
        _slot((11, 30), (12, 0), events=False),  # empty gap: no event at all
        _slot((12, 0), (12, 10), active=0.0),  # events, AFK
    ]
    (period,) = _day(machine)
    assert period.active_seconds == 3600 + 1800
    assert period.tracked_seconds == 150 * 60 + 10 * 60  # every slot with events
    assert period.untracked_seconds == 30 * 60  # the empty gap only
    assert period.wall_seconds == 190 * 60  # 09:00 to 12:10


def test_tracked_plus_untracked_is_always_the_wall_clock():
    machine = [
        _slot((9, 0), (9, 40), active=900.0),
        _slot((9, 40), (10, 0), events=False),
        _slot((10, 0), (10, 15), active=0.0, afk=False),
    ]
    for period in _day(machine):
        assert abs(period.tracked_seconds + period.untracked_seconds - period.wall_seconds) < 1


def test_tasks_only_tracked_is_the_task_time_and_untracked_the_gap_between_tasks():
    events = [task(utc(2026, 11, 2, 15, 0), 30), task(utc(2026, 11, 2, 16, 0), 30)]
    (period,) = build_timesheet(events, "day", local(2026, 11, 2), local(2026, 11, 2, 23), 0, TZ)
    assert period.active_seconds is None
    assert period.tracked_seconds == 3600
    assert period.untracked_seconds == 1800  # 15:30 to 16:00, no task
    assert period.wall_seconds == 90 * 60


def test_without_the_afk_source_active_is_unknown_but_untracked_is_not():
    machine = [
        _slot((9, 0), (9, 30), afk=False, events=True),
        _slot((9, 30), (10, 0), afk=False, events=False),
    ]
    (period,) = _day(machine)
    assert period.active_seconds is None
    assert period.untracked_seconds == 1800


def test_a_period_with_activity_but_no_task_time_is_listed_with_no_tasks():
    (period,) = _day([_slot((9, 0), (10, 0), active=3600.0)])
    assert period.active_seconds == 3600
    assert period.tracked_seconds == 3600
    assert period.untracked_seconds == 0
    assert period.tasks == ()
