"""Text timesheet report: check-in, check-out, active, tracked and untracked time per period.

Check-in, check-out and the wall clock come from the machine spans: the timeslots of the selected
sources, clipped to each period. Active is the not-AFK time of those spans (needs the afk source;
shown as "--" without it). Tracked is the time on taskwarrior tasks, offline work included, outside
AFK. Untracked is the active time with no task. The per-task table is taskwarrior time, from the
raw events clipped to each period (slots lose the task uuid and can split one event). See
CLAUDE.md section 13.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Literal

from aw_core.models import Event

from aw_report.core.filtering import NO_PROJECT, NO_TASK
from aw_report.core.period import day_boundary, next_bucket_start
from aw_report.core.report_slot import ReportTimelineSlot

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
    seconds: float
    count: int


@dataclass(frozen=True)
class MachineSlot:
    """One timeslot as the timesheet sees it: its span, active time, task and AFK state."""

    start: datetime
    end: datetime
    active_seconds: float  # the slot's not-AFK time (zero without the afk source)
    has_events: bool  # the slot carries at least one event of a selected source
    has_afk_state: bool  # the afk source covers this slot


@dataclass(frozen=True)
class TimesheetPeriod:
    label: str
    check_in: int  # epoch seconds, whole (fraction dropped, as the shell tool does)
    check_out: float  # epoch seconds of the last machine span's clipped end
    active_seconds: float | None  # None when the afk source is not selected
    tracked_seconds: float
    untracked_seconds: float
    wall_seconds: float
    tasks: tuple[TimesheetTask, ...]


def clock(seconds: float) -> str:
    """A duration as HH:MM, rounded to the minute (halves up)."""
    minutes = math.floor(seconds / 60 + 0.5)
    hours, rest = divmod(minutes, 60)
    return f"{hours:02d}:{rest:02d}"


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
    ordered: list[Event],
    machine: list[MachineSlot],
    lo: datetime,
    hi: datetime,
    key: date,
    mode: Mode,
) -> TimesheetPeriod | None:
    """One period's summary; None when no machine span or task event remains in [lo, hi)."""
    active = tracked = 0.0
    clipped: list[tuple[datetime, datetime]] = []
    known = False
    for span in machine:
        begin, finish = max(span.start, lo), min(span.end, hi)
        if finish <= begin:
            continue
        clipped.append((begin, finish))
        share = (finish - begin).total_seconds() / (span.end - span.start).total_seconds()
        known = known or span.has_afk_state
        active += span.active_seconds * share
        if span.has_events:
            tracked += (finish - begin).total_seconds()

    groups: dict[str | None, _Group] = {}
    for event in ordered:
        begin = max(event.timestamp, lo)
        finish = min(event.timestamp + event.duration, hi)
        if finish <= begin:
            continue
        seconds = (finish - begin).total_seconds()
        title = event.data.get("title")
        group = groups.setdefault(title, _Group(project=event.data.get("project")))
        group.seconds += seconds
        group.count += 1

    if not clipped:
        return None
    tasks = []
    for title in sorted(groups, key=lambda t: (t is not None, t or "")):
        group = groups[title]
        tasks.append(
            TimesheetTask(
                project=_text(group.project, NO_PROJECT),
                title=_text(title, NO_TASK),
                seconds=group.seconds,
                count=group.count,
            )
        )
    # Whole-second start, as the shell tool does, so the clock times match it exactly.
    check_in = int(min(begin for begin, _ in clipped).timestamp())
    check_out = max(end for _, end in clipped).timestamp()
    return TimesheetPeriod(
        label=_label(key, mode),
        check_in=check_in,
        check_out=check_out,
        active_seconds=active if known else None,
        tracked_seconds=tracked,
        untracked_seconds=max(check_out - check_in - tracked, 0.0),
        wall_seconds=check_out - check_in,
        tasks=tuple(tasks),
    )


def _text(value: object, fallback: str) -> str:
    return str(value) if value else fallback


def machine_slots_from_timeslots(slots: Iterable[ReportTimelineSlot]) -> list[MachineSlot]:
    """Timeslots as machine slots (see MachineSlot)."""
    return [
        MachineSlot(
            start=slot.start,
            end=slot.end,
            active_seconds=slot.breakdown.active.total_seconds(),
            has_events=bool(slot.events),
            has_afk_state=slot.afk_state is not None,
        )
        for slot in slots
    ]


def machine_slots_from_events(events: Iterable[Event]) -> list[MachineSlot]:
    """Task events as machine slots: the tasks-only view (no AFK state, every span has events)."""
    return [
        MachineSlot(
            start=event.timestamp,
            end=event.timestamp + event.duration,
            active_seconds=0.0,
            has_events=True,
            has_afk_state=False,
        )
        for event in events
    ]


def build_timesheet(
    events: Iterable[Event],
    mode: Mode,
    start: datetime,
    end: datetime,
    day_start_hour: int,
    tz: tzinfo,
    machine: Iterable[MachineSlot] | None = None,
) -> list[TimesheetPeriod]:
    """Summaries of every period between ``start`` and ``end``, empty periods omitted.

    ``machine`` are the timeslots of the selected sources (see MachineSlot). When omitted, the
    task events stand in for them: the tasks-only report.
    """
    ordered = sorted(events, key=lambda e: e.timestamp)
    spans = list(machine) if machine is not None else machine_slots_from_events(ordered)
    periods = []
    for key in bucket_starts(start.astimezone(tz), end.astimezone(tz), mode, day_start_hour):
        lo = day_boundary(key, day_start_hour, tz)
        hi = day_boundary(next_bucket_start(key, mode), day_start_hour, tz)
        period = _summarise(ordered, spans, lo, hi, key, mode)
        if period is not None:
            periods.append(period)
    return periods


def _clock(epoch: float, tz: tzinfo) -> str:
    return datetime.fromtimestamp(epoch, tz).strftime("%I:%M %p")


def _amount(seconds: float | None) -> str:
    """HH:MM, or "--" when the value is unknown (the afk source is not selected)."""
    return "--" if seconds is None else clock(seconds)


def format_timesheet(periods: list[TimesheetPeriod], mode: Mode, tz: tzinfo) -> list[str]:
    """The report as lines."""
    rule = "=" * RULE_WIDTH
    lines = [rule, f"{HEADER:^{RULE_WIDTH}}", rule]
    for period in periods:
        lines.append("")
        lines.append(f"{DAY_MARKER} {period.label}")
        lines.append(
            f"   Check-in: {_clock(period.check_in, tz):<8} | "
            f"Check-out: {_clock(period.check_out, tz):<8} | "
            f"Wall Clock: {clock(period.wall_seconds)}"
        )
        lines.append(
            f"   Active: {_amount(period.active_seconds)} | "
            f"Tracked: {clock(period.tracked_seconds)} | "
            f"Untracked: {clock(period.untracked_seconds)}"
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
                f"{clock(task.seconds):>6} | {task.count:>5}"
            )
    lines.append("")
    lines.append(rule)
    lines.append(_grand_totals(periods))
    lines.append(f" Across {len(periods)} active {UNITS[mode]}")
    lines.append(rule)
    return lines


def _grand_totals(periods: list[TimesheetPeriod]) -> str:
    """Totals; Active is unknown ("--") unless every period knows it."""

    def total(values: list[float | None]) -> float | None:
        if not values or any(v is None for v in values):
            return None
        return sum(v for v in values if v is not None)

    active = total([p.active_seconds for p in periods])
    untracked = sum(p.untracked_seconds for p in periods)
    tracked = sum(p.tracked_seconds for p in periods)
    wall = sum(p.wall_seconds for p in periods)
    return (
        f" GRAND TOTALS -- Active: {_amount(active)} | Tracked: {clock(tracked)} | "
        f"Untracked: {clock(untracked)} | Wall Clock: {clock(wall)}"
    )


def print_timesheet_report(
    events: Iterable[Event],
    mode: Mode,
    start: datetime,
    end: datetime,
    day_start_hour: int,
    tz: tzinfo,
    machine: Iterable[MachineSlot] | None = None,
) -> None:
    periods = build_timesheet(events, mode, start, end, day_start_hour, tz, machine)
    for line in format_timesheet(periods, mode, tz):
        print(line)
