"""Text timesheet report: check-in, check-out, active hours and per-task totals per period.

Same layout and numbers as samples/timesheet_report.py (text mode). It is built from the raw
taskwarrior events, clipped to each period, instead of from builder slots: slots lose the task
uuid and can split one event into several pieces. See CLAUDE.md section 13.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Literal

from aw_core.models import Event

from tw_report.core.filtering import NO_PROJECT, NO_TASK
from tw_report.core.period import day_boundary, next_bucket_start

Mode = Literal["day", "week", "month", "year"]

RULE_WIDTH = 78
TITLE_WIDTH = 38
PROJECT_WIDTH = 20
HEADER = "TIMESHEET DAILY ACTIVITY REPORT"
UNITS = {"day": "days", "week": "weeks", "month": "months", "year": "years"}
DAY_MARKER = "\U0001f4c5"  # calendar emoji, as in the shell tool's output


@dataclass
class _Group:
    project: str | None
    seconds: float = 0.0
    count: int = 0


@dataclass(frozen=True)
class TimesheetTask:
    project: str
    title: str
    hours: float
    count: int


@dataclass(frozen=True)
class TimesheetPeriod:
    label: str
    check_in: int  # epoch seconds, whole (fraction dropped, as the shell tool does)
    check_out: float  # epoch seconds of the last event's clipped end
    active_hours: float
    wall_hours: float
    tasks: tuple[TimesheetTask, ...]


def round_hours(seconds: float) -> float:
    """Hours to 2 decimals, halves away from zero (jq's round, as the shell tool applies it)."""
    scaled = seconds / 3600 * 100
    rounded = math.floor(abs(scaled) + 0.5) * (1 if scaled >= 0 else -1)
    return rounded / 100


def bucket_key(moment: datetime, mode: Mode, day_start_hour: int) -> date:
    """Start date of the bucket holding ``moment``, which must already be in the report zone.

    The logical day starts at ``day_start_hour``. Weeks start on Monday, months on the 1st.
    """
    logical = (moment - timedelta(hours=day_start_hour)).date()
    if mode == "day":
        return logical
    if mode == "week":
        return logical - timedelta(days=logical.weekday())
    if mode == "month":
        return logical.replace(day=1)
    return logical.replace(month=1, day=1)


def bucket_starts(start: datetime, end: datetime, mode: Mode, day_start_hour: int) -> list[date]:
    """Every bucket start from the one holding ``start`` to the one holding ``end``."""
    current = bucket_key(start, mode, day_start_hour)
    last = bucket_key(end, mode, day_start_hour)
    starts = []
    while current <= last:
        starts.append(current)
        current = next_bucket_start(current, mode)
    return starts


def _label(key: date, mode: Mode) -> str:
    first = key.strftime("%A, %b %d, %Y")
    if mode == "day":
        return first
    last = next_bucket_start(key, mode) - timedelta(days=1)
    return f"{first} - {last.strftime('%A, %b %d, %Y')}"


def _summarise(
    ordered: list[Event], lo: datetime, hi: datetime, key: date, mode: Mode
) -> TimesheetPeriod | None:
    """One period's summary; None when no event with time in [lo, hi) remains."""
    groups: dict[str | None, _Group] = {}
    total_seconds = 0.0
    first_begin: datetime | None = None
    last_begin: datetime | None = None
    last_seconds = 0.0
    for event in ordered:
        begin = max(event.timestamp, lo)
        finish = min(event.timestamp + event.duration, hi)
        if finish <= begin:
            continue
        seconds = (finish - begin).total_seconds()
        total_seconds += seconds
        if first_begin is None:
            first_begin = begin
        last_begin = begin
        last_seconds = seconds
        title = event.data.get("title")
        group = groups.setdefault(title, _Group(project=event.data.get("project")))
        group.seconds += seconds
        group.count += 1

    active = round_hours(total_seconds)
    if first_begin is None or last_begin is None or active == 0:
        return None
    tasks = []
    for title in sorted(groups, key=lambda t: (t is not None, t or "")):
        group = groups[title]
        tasks.append(
            TimesheetTask(
                project=_text(group.project, NO_PROJECT),
                title=_text(title, NO_TASK),
                hours=round_hours(group.seconds),
                count=group.count,
            )
        )
    # Whole-second start, plus the clipped duration: the shell tool's arithmetic, kept so the
    # check-in and check-out clock times match it exactly.
    check_in = int(first_begin.timestamp())
    check_out = int(last_begin.timestamp()) + last_seconds
    return TimesheetPeriod(
        label=_label(key, mode),
        check_in=check_in,
        check_out=check_out,
        active_hours=active,
        wall_hours=round_hours(check_out - check_in),
        tasks=tuple(tasks),
    )


def _text(value: object, fallback: str) -> str:
    return str(value) if value else fallback


def build_timesheet(
    events: Iterable[Event],
    mode: Mode,
    start: datetime,
    end: datetime,
    day_start_hour: int,
    tz: tzinfo,
) -> list[TimesheetPeriod]:
    """Summaries of every period between ``start`` and ``end``, empty periods omitted."""
    ordered = sorted(events, key=lambda e: e.timestamp)
    periods = []
    for key in bucket_starts(start.astimezone(tz), end.astimezone(tz), mode, day_start_hour):
        lo = day_boundary(key, day_start_hour, tz)
        hi = day_boundary(next_bucket_start(key, mode), day_start_hour, tz)
        period = _summarise(ordered, lo, hi, key, mode)
        if period is not None:
            periods.append(period)
    return periods


def _clock(epoch: float, tz: tzinfo) -> str:
    return datetime.fromtimestamp(epoch, tz).strftime("%I:%M %p")


def format_timesheet(periods: list[TimesheetPeriod], mode: Mode, tz: tzinfo) -> list[str]:
    """The report as lines, in the shell tool's layout."""
    rule = "=" * RULE_WIDTH
    lines = [rule, f"{HEADER:^{RULE_WIDTH}}", rule]
    for period in periods:
        lines.append("")
        lines.append(f"{DAY_MARKER} {period.label}")
        lines.append(
            f"   Check-in: {_clock(period.check_in, tz):<8} | "
            f"Check-out: {_clock(period.check_out, tz):<8} | "
            f"Active: {period.active_hours:.2f} hrs | "
            f"Wall Clock: {period.wall_hours:.2f} hrs"
        )
        lines.append("-" * RULE_WIDTH)
        lines.append(
            f"    {'Project':<{PROJECT_WIDTH}} | {'Task Title':<{TITLE_WIDTH}} | "
            f"{'Hours':<6} | Count"
        )
        lines.append(f"    {'-' * PROJECT_WIDTH} | {'-' * TITLE_WIDTH} | {'-' * 6} | {'-' * 5}")
        for task in period.tasks:
            title = task.title
            if len(title) > TITLE_WIDTH:
                title = title[:35] + "..."
            lines.append(
                f"    {task.project:<{PROJECT_WIDTH}} | {title:<{TITLE_WIDTH}} | "
                f"{task.hours:>6.2f} | {task.count:>5}"
            )
    total_active = sum(p.active_hours for p in periods)
    total_wall = sum(p.wall_hours for p in periods)
    lines.append("")
    lines.append(rule)
    lines.append(
        f" GRAND TOTALS -- Active: {total_active:.2f} hrs | Wall Clock: {total_wall:.2f} hrs"
    )
    lines.append(f" Across {len(periods)} active {UNITS[mode]}")
    lines.append(rule)
    return lines


def print_timesheet_report(
    events: Iterable[Event],
    mode: Mode,
    start: datetime,
    end: datetime,
    day_start_hour: int,
    tz: tzinfo,
) -> None:
    periods = build_timesheet(events, mode, start, end, day_start_hour, tz)
    for line in format_timesheet(periods, mode, tz):
        print(line)
