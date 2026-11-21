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
from itertools import accumulate
from typing import Literal

from aw_core.models import Event

from aw_report.core.filtering import NO_PROJECT, NO_TASK
from aw_report.core.period import day_boundary, next_bucket_start
from aw_report.core.report_slot import ReportTimelineSlot
from aw_report.utils.formatting import display_width, ljust_display

Mode = Literal["day", "week", "month", "year"]
# minutes (default), seconds (--seconds) or nanoseconds (--nanoseconds, one value per line)
Precision = Literal["minutes", "seconds", "nanoseconds"]

DEFAULT_WIDTH = 78  # the width used when the output is not a terminal (pipes, files)
MIN_PROJECT = 12  # narrowest project column before a name wraps
MIN_TITLE = 16  # narrowest title column before a task name wraps
CONTINUATION = "  "  # indent of a wrapped line, under the line it continues
TABLE_MARGIN = 4  # the table keeps a right margin as wide as its left indent
# Hours column: HH:MM (6), HH:MM:SS (9), HH:MM:SS.nnnnnnnnn (19)
HOURS_WIDTH = {"minutes": 6, "seconds": 9, "nanoseconds": 19}
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
    check_in: datetime  # exact start of the first machine span in the period
    check_out: datetime  # exact end of the last machine span in the period
    active_seconds: float | None  # None when the afk source is not selected
    tracked_seconds: float
    untracked_seconds: float
    wall_seconds: float
    tasks: tuple[TimesheetTask, ...]


def clock(seconds: float, precision: Precision = "minutes") -> str:
    """A duration as HH:MM, HH:MM:SS or HH:MM:SS.nnnnnnnnn, rounded (halves up) to the unit."""
    if precision == "nanoseconds":
        total = round(seconds * 1_000_000_000)
        hours, rest = divmod(total, 3600 * 1_000_000_000)
        minutes, rest = divmod(rest, 60 * 1_000_000_000)
        secs, nanos = divmod(rest, 1_000_000_000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{nanos:09d}"
    if precision == "seconds":
        total = math.floor(seconds + 0.5)
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
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
    check_in = min(begin for begin, _ in clipped)
    check_out = max(end for _, end in clipped)
    return TimesheetPeriod(
        label=_label(key, mode),
        check_in=check_in,
        check_out=check_out,
        active_seconds=active if known else None,
        tracked_seconds=tracked,
        untracked_seconds=max((check_out - check_in).total_seconds() - tracked, 0.0),
        wall_seconds=(check_out - check_in).total_seconds(),
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


def _clock(moment: datetime, tz: tzinfo, precision: Precision = "minutes") -> str:
    """A clock time in the report zone, 24-hour: 09:00, 09:00:12, or 09:00:12.000000000.

    Event timestamps have microsecond precision, so the nanosecond digits end in 000.
    """
    local = moment.astimezone(tz)
    if precision == "nanoseconds":
        return f"{local:%H:%M:%S}.{local.microsecond * 1000:09d}"
    if precision == "seconds":
        return local.strftime("%H:%M:%S")
    return local.strftime("%H:%M")


def layout(columns: int | None) -> int:
    """The report width: the whole terminal, or DEFAULT_WIDTH when there is no terminal."""
    return DEFAULT_WIDTH if columns is None else columns


def _pack(tokens: list[str], width: int, indent: str, joiner: str) -> list[str]:
    """Join ``tokens`` with ``joiner`` into lines of at most ``width`` columns.

    A token that does not fit on a line starts the next one. A token longer than a whole line is
    cut, so no text is lost: the continuation lines begin with ``indent``. Widths are display
    columns, so wide characters are counted correctly.
    """
    lines: list[str] = []
    current = ""
    pending = list(tokens)
    while pending:
        token = pending.pop(0)
        budget = width if not lines else width - display_width(indent)
        trial = f"{current}{joiner}{token}" if current else token
        if display_width(trial) <= budget:
            current = trial
        elif current:
            lines.append(current)
            current = ""
            pending.insert(0, token)
        else:
            head = _take(token, budget)
            lines.append(head)
            pending.insert(0, token[len(head) :])  # an empty rest is a no-op
    if current:
        lines.append(current)
    if not lines:
        return [""]
    return [lines[0]] + [indent + line for line in lines[1:]]


def table_columns(width: int, precision: Precision, longest_project: int) -> tuple[int, int]:
    """Project and title column widths; with the fixed columns they add up to ``width``.

    The project column is as wide as its longest name when there is room (at most 45% of the
    free space); the title takes the rest, at least MIN_TITLE columns.
    """
    free = width - (18 + HOURS_WIDTH[precision])  # 4 indent, 3 separators, Count 5
    project = max(MIN_PROJECT, min(max(longest_project, MIN_PROJECT), round(0.45 * free)))
    # Below the supported minimum (see the README) the columns shrink to 1 and lines overflow
    project = max(1, min(project, free - MIN_TITLE))
    return project, max(1, free - project)


def _take(text: str, width: int, from_end: bool = False) -> str:
    """The longest start (or end) of ``text`` that fits ``width`` display columns."""
    chars = list(reversed(text)) if from_end else list(text)
    # Widths only grow along the prefix, so the characters that fit are a prefix of ``chars``
    fits = sum(1 for used in accumulate(display_width(c) for c in chars) if used <= width)
    kept = chars[:fits]
    return "".join(reversed(kept)) if from_end else "".join(kept)


def _amount(seconds: float | None, precision: Precision = "minutes") -> str:
    """The clock value, or "--" when the value is unknown (the afk source is not selected)."""
    return "--" if seconds is None else clock(seconds, precision)


def _aligned(rows: list[list[str]], width: int) -> list[str]:
    """Rows of "Label: value" parts, with the values of each column lined up on their right edge.

    The spaces go between a label and its value, so every column of the block has the width of its
    widest part; the separators line up. If the aligned rows would not fit ``width``, each row is
    wrapped on its own instead.
    """
    pairs = [[tuple(part.split(": ", 1)) for part in row] for row in rows]
    columns = max(len(row) for row in pairs)
    # the width of a column: the widest "Label: value" in it
    widths = [
        max(
            len(label) + 2 + len(value)
            for row in pairs
            if i < len(row)
            for label, value in [row[i]]
        )
        for i in range(columns)
    ]
    lines = []
    for row in pairs:
        cells = []
        for i, (label, value) in enumerate(row):
            gap = widths[i] - len(label) - 1 - len(value)  # at least one space after the colon
            cells.append(f"{label}:{' ' * gap}{value}")
        lines.append("   " + " | ".join(cells))
    if any(display_width(line) > width for line in lines):
        return [line for row in rows for line in _flow(row, width)]
    return lines


def _period_lines(
    period: TimesheetPeriod, tz: tzinfo, precision: Precision, width: int
) -> list[str]:
    """A period's times: Check-in, Check-out, Wall Clock, then Tracked, Untracked, Active."""
    first = [
        f"Check-in: {_clock(period.check_in, tz, precision)}",
        f"Check-out: {_clock(period.check_out, tz, precision)}",
        f"Wall Clock: {clock(period.wall_seconds, precision)}",
    ]
    second = [
        f"Tracked: {clock(period.tracked_seconds, precision)}",
        f"Untracked: {clock(period.untracked_seconds, precision)}",
        f"Active: {_amount(period.active_seconds, precision)}",
    ]
    return _aligned([first, second], width)


def _flow(parts: list[str], width: int) -> list[str]:
    """Labelled values on lines of ``width`` columns, joined by " | ", wrapped with indentation."""
    lines = _pack(parts, width - 3, " " * 5, " | ")
    return ["   " + lines[0], *lines[1:]]


def _total_parts(periods: list[TimesheetPeriod], precision: Precision) -> list[str]:
    def total(values: list[float | None]) -> float | None:
        if not values or any(v is None for v in values):
            return None
        return sum(v for v in values if v is not None)

    active = total([p.active_seconds for p in periods])
    tracked = sum(p.tracked_seconds for p in periods)
    untracked = sum(p.untracked_seconds for p in periods)
    wall = sum(p.wall_seconds for p in periods)
    return [
        f"Tracked: {clock(tracked, precision)}",
        f"Untracked: {clock(untracked, precision)}",
        f"Active: {_amount(active, precision)}",
        f"Wall Clock: {clock(wall, precision)}",
    ]


def _task_rows(
    task: TimesheetTask, project_width: int, title_width: int, precision: Precision
) -> list[str]:
    """One task as one or more lines: the first carries the hours and count, the rest continue."""
    hours_width = HOURS_WIDTH[precision]
    projects = _pack(_dotted(task.project), project_width, CONTINUATION, "")
    titles = _pack(task.title.split(" "), title_width, CONTINUATION, " ")
    rows = []
    for index in range(max(len(projects), len(titles))):
        project = projects[index] if index < len(projects) else ""
        title = titles[index] if index < len(titles) else ""
        if index == 0:
            hours = f"{clock(task.seconds, precision):>{hours_width}}"
            count = f"{task.count:>5}"
        else:
            hours, count = " " * hours_width, " " * 5
        rows.append(
            f"    {ljust_display(project, project_width)} | "
            f"{ljust_display(title, title_width)} | {hours} | {count}"
        )
    return rows


def _dotted(project: str) -> list[str]:
    """Project levels as tokens that keep their dots (so wrapping happens at a dot)."""
    levels = project.split(".")
    return [f"{level}." for level in levels[:-1]] + [levels[-1]]


def format_timesheet(
    periods: list[TimesheetPeriod],
    mode: Mode,
    tz: tzinfo,
    width: int = DEFAULT_WIDTH,
    precision: Precision = "minutes",
) -> list[str]:
    """The report as lines, at most ``width`` columns wide: names and times wrap, never cut."""
    longest = max((len(t.project) for p in periods for t in p.tasks), default=0)
    project_width, title_width = table_columns(width - TABLE_MARGIN, precision, longest)
    hours_width = HOURS_WIDTH[precision]
    rule = "=" * width
    lines = [rule, f"{HEADER:^{width}}", rule]
    for period in periods:
        lines.append("")
        lines.append(f"{DAY_MARKER} {period.label}")
        lines.extend(_period_lines(period, tz, precision, width))
        lines.append("-" * width)
        lines.append(
            f"    {'Project':<{project_width}} | {'Task Title':<{title_width}} | "
            f"{'Hours':<{hours_width}} | Count"
        )
        lines.append(
            f"    {'-' * project_width} | {'-' * title_width} | {'-' * hours_width} | {'-' * 5}"
        )
        for task in period.tasks:
            lines.extend(_task_rows(task, project_width, title_width, precision))
    lines.append("")
    lines.append(rule)
    lines.append(" GRAND TOTALS")
    lines.extend(_flow(_total_parts(periods, precision), width))
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
    machine: Iterable[MachineSlot] | None = None,
    columns: int | None = None,
    precision: Precision = "minutes",
) -> None:
    """Print the report for a terminal of ``columns`` (None: not a terminal, 78 wide)."""
    periods = build_timesheet(events, mode, start, end, day_start_hour, tz, machine)
    for line in format_timesheet(periods, mode, tz, layout(columns), precision):
        print(line)
