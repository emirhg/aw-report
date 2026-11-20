"""Unit tests for the text timesheet renderer (pipeline/timesheet_render.py).

Everything is pinned to an explicit zone (America/Mexico_City, UTC-6, no DST since 2022), so the
results do not depend on the machine's TZ.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from aw_core.models import Event

from tw_report.core.filtering import NO_TASK
from tw_report.pipeline.timesheet_render import (
    bucket_key,
    bucket_starts,
    build_timesheet,
    format_timesheet,
    print_timesheet_report,
    round_hours,
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


class TestRoundHours:
    def test_halves_round_away_from_zero(self):
        assert round_hours(450) == 0.13  # 0.125 h
        assert round_hours(0) == 0

    def test_two_decimal_hours(self):
        assert round_hours(3600 * 2.5) == 2.5
        assert round_hours(3600 * 1.004) == 1.0


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
        assert [p.active_hours for p in periods] == [1.0, 1.0]
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
        periods = build_timesheet(
            events, "day", local(2026, 11, 2), local(2026, 11, 4, 23), 0, TZ
        )
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
        periods = build_timesheet(
            events, "day", local(2026, 11, 2), local(2026, 11, 2, 23), 0, TZ
        )
        rule = "=" * 78
        assert format_timesheet(periods, "day", TZ) == [
            rule,
            f"{'TIMESHEET DAILY ACTIVITY REPORT':^78}",
            rule,
            "",
            "\U0001f4c5 Monday, Nov 02, 2026",
            "   Check-in: 03:00 PM | Check-out: 03:30 PM | Active: 0.50 hrs | Wall Clock: 0.50 hrs",
            "-" * 78,
            "    Project              | Task Title                             | Hours  | Count",
            "    -------------------- | -------------------------------------- | ------ | -----",
            "    Proj                 | Write docs                             |   0.50 |     1",
            "",
            rule,
            " GRAND TOTALS -- Active: 0.50 hrs | Wall Clock: 0.50 hrs",
            " Across 1 active days",
            rule,
        ]

    def test_long_titles_are_cut_to_35_characters_and_dots(self):
        title = "x" * 50
        events = [task(utc(2026, 11, 2, 21, 0), 30, title=title)]
        periods = build_timesheet(
            events, "day", local(2026, 11, 2), local(2026, 11, 2, 23), 0, TZ
        )
        (row,) = [line for line in format_timesheet(periods, "day", TZ) if "xxx" in line]
        assert "x" * 35 + "..." in row
        assert "x" * 36 not in row

    def test_footer_noun_follows_the_mode(self):
        events = [task(utc(2026, 11, 2, 21, 0), 30)]
        periods = build_timesheet(
            events, "week", local(2026, 11, 2), local(2026, 11, 8, 23), 0, TZ
        )
        assert format_timesheet(periods, "week", TZ)[-2] == " Across 1 active weeks"

    def test_no_activity_prints_header_and_zero_totals(self):
        lines = format_timesheet([], "day", TZ)
        assert lines[-5:] == [
            "",
            "=" * 78,
            " GRAND TOTALS -- Active: 0.00 hrs | Wall Clock: 0.00 hrs",
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
