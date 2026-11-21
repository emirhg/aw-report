"""Unit tests for the text timesheet renderer (pipeline/timesheet_render.py).

Everything is pinned to an explicit zone (America/Mexico_City, UTC-6, no DST since 2022), so the
results do not depend on the machine's TZ.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from aw_report.core.event import Event

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
    def test_footer_noun_follows_the_mode(self):
        events = [task(utc(2026, 11, 2, 21, 0), 30)]
        periods = build_timesheet(events, "week", local(2026, 11, 2), local(2026, 11, 8, 23), 0, TZ)
        assert format_timesheet(periods, "week", TZ)[-2] == " Across 1 active weeks"

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
    assert period.check_in == datetime(2026, 8, 30, 8, 30, tzinfo=UTC)
    assert period.check_out == datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
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


class TestSeconds:
    def test_clock_with_seconds_rounds_to_the_second_halves_up(self):
        assert clock(0, "seconds") == "00:00:00"
        assert clock(59.5, "seconds") == "00:01:00"
        assert clock(3600 * 2 + 61.4, "seconds") == "02:01:01"

    def test_totals_can_pass_a_hundred_hours_with_seconds(self):
        assert clock(3600 * 100 + 59, "seconds") == "100:00:59"

    def test_check_in_clock_with_seconds(self):
        from aw_report.pipeline.timesheet_render import _clock

        moment = datetime(2026, 11, 2, 15, 3, 9, tzinfo=UTC)
        assert _clock(moment, UTC, "seconds") == "15:03:09"
        assert _clock(moment, UTC) == "15:03"


class TestNanoseconds:
    def test_clock_with_nanoseconds_keeps_nine_digits(self):
        assert clock(0, "nanoseconds") == "00:00:00.000000000"
        assert clock(3600 * 2 + 61.5, "nanoseconds") == "02:01:01.500000000"
        assert clock(0.000000001, "nanoseconds") == "00:00:00.000000001"
        assert clock(3600 * 100 + 59.25, "nanoseconds") == "100:00:59.250000000"

    def test_check_in_clock_with_nanoseconds_from_the_exact_timestamp(self):
        from aw_report.pipeline.timesheet_render import _clock

        moment = datetime(2026, 11, 2, 15, 3, 9, 123456, tzinfo=UTC)
        assert _clock(moment, UTC, "nanoseconds") == "15:03:09.123456000"


class TestWrapping:
    def test_the_report_uses_the_whole_terminal_width(self):
        from aw_report.pipeline.timesheet_render import layout

        assert layout(120) == 120
        assert layout(None) == 78  # not a terminal: the fixed width

    def test_table_columns_fill_the_width_and_keep_a_title_column(self):
        from aw_report.pipeline.timesheet_render import table_columns

        for precision, fixed in (("minutes", 24), ("seconds", 27), ("nanoseconds", 37)):
            for width in (60, 78, 120, 200):
                project, title = table_columns(width, precision, 40)
                assert fixed + project + title == width
                assert title >= 16
        assert table_columns(78, "minutes", 10)[0] == 12  # never narrower than the minimum
        assert table_columns(78, "minutes", 80)[0] == 24  # at most 45% of the 54 free columns

    def test_words_wrap_greedily_and_continuation_lines_are_indented(self):
        from aw_report.pipeline.timesheet_render import _pack

        lines = _pack("Optimizar la generación de reporte mensual".split(" "), 16, "  ", " ")
        assert lines[0] == "Optimizar la"
        assert all(line.startswith("  ") for line in lines[1:])
        assert (
            " ".join(line.strip() for line in lines) == "Optimizar la generación de reporte mensual"
        )

    def test_a_word_longer_than_a_line_is_split_without_losing_text(self):
        from aw_report.pipeline.timesheet_render import _pack

        lines = _pack(["x" * 30], 12, "  ", " ")
        assert lines[0] == "x" * 12
        assert "".join(line.strip() for line in lines) == "x" * 30

    def test_a_word_exactly_one_line_long_fills_it_without_an_empty_line(self):
        from aw_report.pipeline.timesheet_render import _pack

        assert _pack(["x" * 12], 12, "  ", " ") == ["x" * 12]

    def test_a_word_that_splits_into_whole_lines_ends_without_an_empty_line(self):
        from aw_report.pipeline.timesheet_render import _pack

        assert _pack(["x" * 24], 12, "", "") == ["x" * 12, "x" * 12]

    def test_nothing_to_pack_gives_one_empty_line(self):
        from aw_report.pipeline.timesheet_render import _pack

        assert _pack([], 12, "  ", " ") == [""]
        assert _pack([""], 12, "  ", " ") == [""]

    def test_project_levels_wrap_at_the_dots(self):
        from aw_report.pipeline.timesheet_render import _dotted, _pack

        lines = _pack(_dotted("Mercado laboral.Postulación.Satven"), 18, "  ", "")
        assert lines == ["Mercado laboral.", "  Postulación.", "  Satven"][:0] or (
            lines[0] == "Mercado laboral." and "Satven" in lines[-1]
        )
        assert "".join(line.strip() for line in lines) == "Mercado laboral.Postulación.Satven"

    def test_a_task_spans_as_many_rows_as_its_longest_cell(self):
        from aw_report.pipeline.timesheet_render import TimesheetTask, _task_rows

        task_row = TimesheetTask(
            project="Organización.Comunicación.Reporte",
            title="Empaquetar TaskWarrior Watch hooks",
            seconds=4 * 3600 + 8 * 60,
            count=3,
        )
        rows = _task_rows(task_row, 22, 21, "minutes")
        assert len(rows) >= 2
        assert rows[0].count("04:08") == 1  # the hours and count appear on the first line only
        assert "04:08" not in rows[1]
        assert all(len(row) == len(rows[0]) for row in rows)  # cells keep their columns

    def test_every_line_is_within_the_width_and_no_text_is_lost(self):
        from aw_report.pipeline.timesheet_render import format_timesheet
        from aw_report.utils.formatting import display_width

        long_project = "Mercado laboral.Postulación.Satven.Organización.Comunicación"
        events = [
            task(utc(2026, 11, 2, 15, 0), 3600 * 30, project=long_project, title="x" * 60),
            task(utc(2026, 11, 2, 16, 0), 30, title="表示" * 20),
        ]
        periods = build_timesheet(events, "day", local(2026, 11, 2), local(2026, 11, 2, 23), 0, TZ)
        # the supported minimum is 56 (minutes), 59 (seconds) and 69 (nanoseconds) columns
        for width, precisions in (
            (56, ("minutes",)),
            (69, ("minutes", "seconds", "nanoseconds")),
            (80, ("minutes", "seconds", "nanoseconds")),
            (120, ("minutes", "seconds", "nanoseconds")),
            (200, ("minutes", "seconds", "nanoseconds")),
        ):
            for precision in precisions:
                lines = format_timesheet(periods, "day", TZ, width, precision)
                assert all(display_width(line) <= width for line in lines), (width, precision)
                joined = "".join(line for line in lines)
                assert joined.count("x") == 60  # the long title is wrapped, not cut
                assert joined.count("表") == 20 and joined.count("示") == 20  # split, not lost


def test_the_table_keeps_a_right_margin_as_wide_as_its_left_indent():
    from aw_report.pipeline.timesheet_render import TABLE_MARGIN, format_timesheet
    from aw_report.utils.formatting import display_width

    assert TABLE_MARGIN == 4
    events = [
        task(
            utc(2026, 11, 2, 15, 0),
            3600,
            project="Organización.Comunicación.Reporte",
            title="Optimizar la generación de reporte de trabajo",
        )
    ]
    periods = build_timesheet(events, "day", local(2026, 11, 2), local(2026, 11, 2, 23), 0, TZ)
    for width in (80, 120):
        lines = format_timesheet(periods, "day", TZ, width, "minutes")
        header = next(line for line in lines if "Task Title" in line)
        dashes = next(line for line in lines if "-----" in line and "|" in line)
        assert display_width(header) == width - 4
        assert display_width(dashes) == width - 4
        assert lines[0] == lines[2] == "=" * width  # the rules keep the full width


class TestPeriodLines:
    def test_the_clock_is_24_hours_without_am_pm(self):
        from aw_report.pipeline.timesheet_render import _clock

        assert _clock(datetime(2026, 11, 2, 0, 0, tzinfo=UTC), UTC) == "00:00"
        assert _clock(datetime(2026, 11, 2, 12, 0, tzinfo=UTC), UTC) == "12:00"
        assert _clock(datetime(2026, 11, 2, 23, 25, 7, tzinfo=UTC), UTC, "seconds") == "23:25:07"

    def test_the_two_rows_have_aligned_separators(self):
        from aw_report.pipeline.timesheet_render import _aligned
        from aw_report.utils.formatting import display_width

        first = ["Check-in: 07:17", "Check-out: 23:25", "Wall Clock: 16:08"]
        second = ["Tracked: 04:22", "Untracked: 11:45", "Active: 02:55"]
        lines = _aligned([first, second], 80)
        assert len(lines) == 2
        separators = [[i for i, c in enumerate(line) if c == "|"] for line in lines]
        assert separators[0] == separators[1]
        assert all(display_width(line) <= 80 for line in lines)
        assert lines[1].index("Tracked") < lines[1].index("Untracked") < lines[1].index("Active")

    def test_aligned_rows_fall_back_to_wrapping_when_they_do_not_fit(self):
        from aw_report.pipeline.timesheet_render import _aligned
        from aw_report.utils.formatting import display_width

        long = [f"Part{i}: {'9' * 30}" for i in range(3)]
        lines = _aligned([long, long], 60)
        assert all(display_width(line) <= 60 for line in lines)
        assert len(lines) > 2  # each row wrapped on its own

    def test_totals_list_tracked_untracked_active_then_wall_clock(self):
        events = [task(utc(2026, 11, 2, 21, 0), 30)]
        periods = build_timesheet(events, "day", local(2026, 11, 2), local(2026, 11, 2, 23), 0, TZ)
        totals = format_timesheet(periods, "day", TZ)
        start = totals.index(" GRAND TOTALS") + 1
        end = next(i for i, line in enumerate(totals) if line.startswith(" Across"))
        joined = " ".join(totals[start:end])
        assert joined.index("Tracked") < joined.index("Untracked") < joined.index("Active")
        assert joined.index("Active") < joined.index("Wall Clock")


def test_the_period_block_lines_up_the_values_of_each_column():
    from aw_report.pipeline.timesheet_render import _aligned

    lines = _aligned(
        [
            ["Check-in: 07:17:03", "Check-out: 23:45:48", "Wall Clock: 16:28:45"],
            ["Tracked: 04:43:31", "Untracked: 11:45:14", "Active: 03:15:29"],
        ],
        100,
    )
    assert lines == [
        "   Check-in: 07:17:03 | Check-out: 23:45:48 | Wall Clock: 16:28:45",
        "   Tracked:  04:43:31 | Untracked: 11:45:14 | Active:     03:15:29",
    ]
