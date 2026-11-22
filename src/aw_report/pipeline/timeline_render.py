"""Timeline report rendering (print to stdout).

Renders a detailed timeline of work sessions organized by date and week,
with support for consolidation and detail levels.

CRITICAL HISTORY (Phases 5-8, 2026-07-02):
===========================================

Phase 5 refactoring (commit 12def2d) partially extracted this module but left
it incomplete: only ~140 lines of the 615-line print_timeline_report() were
extracted. Missing logic included:
  - Time range calculation and formatting (START - END)
  - Task name extraction with ▶▶ separator
  - Project name abbreviation
  - Productivity percentage calculation per entry
  - Full AFK and OFFLINE slot rendering
  - Metrics aggregation (AFK time, project tracking %, focus time)
  - _render_slot_detail() helper for detail_level >= 3

This caused the broken behavior reported on 2026-07-02:
  - Online Time showed 8:27:21 instead of 8:50:56 (missing 0:23:34 AFK)
  - Project Tracking showed 0.0% instead of 89.0%
  - AFK time metric not displayed
  - Focus time metric missing
  - Timeline entries showed "00:00-..." instead of "00:00 - 04:36"
  - No task names (▶▶ Corregir el reporte...) displayed
  - No per-entry productivity percentages ([prod 48%])

RESTORATION (commit 1b35699):
=============================
The complete implementation was restored from commit 69aeca3 (last working
state before refactoring). This includes:
  - Full print_timeline_report() with consolidated/rollup rendering engine
  - _render_slot_detail() for hierarchical category/app/title sub-rows
  - split_slots_spanning_days() extracted as module-level function
  - Original format_timeline_line() using terminal-width alignment

All metrics are now calculated correctly, time ranges display properly,
and consolidated/detail rendering works as originally designed.

FORMATTING BEHAVIOR:
====================
format_timeline_line() uses ljust(terminal_width - len(duration) - 1)
to pad the left content. This creates lines that:
  - Use full terminal width for dynamic alignment
  - Right-align the duration column
  - Work correctly with both simple and consolidated rendering

Example (terminal width 120):
  Input:  left="     00:00 - 04:36  ▶ Project", duration="4:36:02"
  Output: "     00:00 - 04:36  ▶ Project       4:36:02"
                                        ^~105 chars to 120~^

This approach was chosen over fixed-column padding because:
  1. Consolidation generates different content lengths per entry
  2. Terminal width can vary by user environment
  3. Dynamic padding maintains alignment without truncation
"""

import contextlib
from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, overload

from aw_report.core.event import Event
from aw_report.core.filtering import NO_PROJECT
from aw_report.core.period import local_zoneinfo, logical_date
from aw_report.core.report_slot import dash_width, right_section_width
from aw_report.pipeline.metrics import state_totals
from aw_report.pipeline.row_merge import merge_display_rows
from aw_report.utils.deprecation import deprecated

if TYPE_CHECKING:
    from aw_report.core.report_slot import ReportTimelineSlot
    from aw_report.pipeline.processors import ProductivityBreakdown
from aw_report.pipeline.models import PeriodMetrics, TimeslotDuration
from aw_report.pipeline.report_render import print_report_summary, print_report_totals
from aw_report.utils.formatting import (
    format_duration,
    format_duration_tracked_prod,
    get_terminal_width,
    to_local_time,
    truncate_title,
)

# BREAKS COLUMN FEATURE (Phase 3)
# Functions for calculating and rendering break durations between work slots

MIN_BREAK_DURATION = timedelta(minutes=5)  # Breaks < 5 min are tracking noise


def _pct_of(part, whole) -> float:
    """Percentage of ``whole`` (both timedeltas) covered by ``part``; 0 when ``whole`` is empty."""
    return (part.total_seconds() / whole.total_seconds() * 100) if whole.total_seconds() > 0 else 0


@deprecated("Not used by the report pipeline; only deprecated get_gap_between_slots() calls it.")
def calculate_break_duration(slot_end: datetime, next_slot_start: datetime) -> timedelta:
    """Calculate break duration between two slots.

    Args:
        slot_end: End time of the current slot
        next_slot_start: Start time of the next slot

    Returns:
        Break duration (clamped to 0 minimum for overlapping slots)
    """
    gap = next_slot_start - slot_end
    return max(gap, timedelta(0))


@deprecated("Not used by the report pipeline.")
def get_gap_between_slots(slot1: dict[str, Any], slot2: dict[str, Any]) -> timedelta:
    """Extract gap time between two slots.

    Args:
        slot1: First slot dict with 'start' and 'end' keys
        slot2: Second slot dict with 'start' and 'end' keys

    Returns:
        Gap duration between slot1.end and slot2.start
    """
    return calculate_break_duration(slot1["end"], slot2["start"])


@deprecated("Not used by the report pipeline.")
def format_break_column(break_duration: timedelta | None) -> str:
    """Format break duration for timeline column display.

    Args:
        break_duration: Break duration (None or timedelta)

    Returns:
        Formatted string "HH:MM:SS" (8 chars) or blank (8 spaces)
    """
    if break_duration is None or break_duration < MIN_BREAK_DURATION:
        return " " * 8  # 8 spaces for consistent column width

    return format_duration(break_duration)


# MODULE-LEVEL SLOT GROUPING FUNCTIONS (extracted from print_timeline_report)
# These are separated to enable unit testing and reuse


def _separator_line(width: int, show_no_afk: bool) -> str:
    """Underline under the duration columns, aligned with DisplayColumns (no productivity)."""
    left_width = 87  # indent + time range + project + task (DisplayColumns left section)
    right_width = right_section_width(show_no_afk)
    padding = max(width - left_width - right_width - 2, 0)
    dashes = dash_width(show_no_afk)
    return " " * left_width + " " * padding + "  " + "-" * dashes + " " * (right_width - dashes)


def compute_afk_offline_totals(slots: list[ReportTimelineSlot]) -> tuple[timedelta, timedelta]:
    """Total AFK and Offline duration of the given slots, from their breakdowns.

    Used by both timeline and hierarchical reports so their TOTALS agree.

    Returns:
        (total_afk_time, total_offline_time) tuple of timedeltas
    """
    totals = state_totals(slots)
    return totals.afk, totals.offline


def get_slot_week_key(slot: dict | ReportTimelineSlot, day_start_hour: int = 4) -> str:
    """Get ISO week key for a slot based on its logical date.

    Calculates which ISO week a slot belongs to, accounting for day_start_hour.
    This ensures consistent week assignment when logical day != calendar day.

    Args:
        slot: Timeline slot (dict or ReportTimelineSlot object)
        day_start_hour: Hour when logical day starts (0-23, default 4)

    Returns:
        ISO week key string in format 'YYYY-Www' (e.g., '2026-W35')

    Example:
        >>> from datetime import datetime
        >>> slot = {"start": datetime(2026, 8, 31, 22, 0)}
        >>> get_slot_week_key(slot, day_start_hour=4)
        '2026-W36'
    """
    start_time = slot.get("start") if isinstance(slot, dict) else slot.start
    assert start_time is not None, "slot must have a start time"

    logical_dt = logical_date(start_time, day_start_hour)
    iso_year, iso_week, iso_weekday = logical_dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def get_slot_logical_date(slot: dict | ReportTimelineSlot, day_start_hour: int = 4):
    """Get logical date for a slot.

    Determines which logical day a slot belongs to, accounting for day_start_hour.
    Logical days start at day_start_hour instead of midnight.

    Args:
        slot: Timeline slot (dict or ReportTimelineSlot object)
        day_start_hour: Hour when logical day starts (0-23, default 4)

    Returns:
        datetime.date object representing the logical day

    Example:
        >>> from datetime import datetime
        >>> slot = {"start": datetime(2026, 9, 1, 2, 30)}
        >>> get_slot_logical_date(slot, day_start_hour=4)
        datetime.date(2026, 8, 31)  # Still in previous logical day
    """
    start_time = slot.get("start") if isinstance(slot, dict) else slot.start
    assert start_time is not None, "slot must have a start time"

    return logical_date(start_time, day_start_hour)


def _get_displayed_duration(slot: ReportTimelineSlot) -> timedelta:
    """The duration shown for a slot: its wall-clock duration (its breakdown sums to it)."""
    return slot.duration


@overload
def split_slots_spanning_days(
    slots: list[dict[str, Any]], day_start_hour: int = 4
) -> list[dict[str, Any]]: ...


@overload
def split_slots_spanning_days(
    slots: list[ReportTimelineSlot], day_start_hour: int = 4
) -> list[ReportTimelineSlot]: ...


def split_slots_spanning_days(
    slots: list[dict[str, Any]] | list[ReportTimelineSlot], day_start_hour: int = 4
) -> list[dict[str, Any]] | list[ReportTimelineSlot]:
    """Split slots that span multiple days into single-day pieces.

    Accepts both dicts and ReportTimelineSlot objects for backward compatibility.
    Uses ReportTimelineSlot.split_at_boundaries("day") for unified splitting logic.
    See core/report_slot.py for the actual implementation.

    Args:
        slots: List of slots (dicts or ReportTimelineSlot objects) that may span multiple days
        day_start_hour: Hour when a logical day starts (0-23, default 4)

    Returns:
        List of slots, with multi-day slots split into single-day pieces (same type as input)
    """
    from aw_report.core.report_slot import ReportTimelineSlot

    # Output takes the form of the input: dict slots in, dict slots out (per the first element)
    input_is_dict = bool(slots) and isinstance(slots[0], dict)

    report_slots: list[ReportTimelineSlot] = []
    for item in slots:
        if isinstance(item, dict):
            try:
                report_slots.append(ReportTimelineSlot.from_dict(item))
            except Exception:
                # Backward compatibility: skip malformed slots
                continue
        else:
            report_slots.append(item)

    # Split each slot at day boundaries
    split_slots: list[ReportTimelineSlot] = []
    for slot in report_slots:
        split_slots.extend(slot.split_at_boundaries("day", day_start_hour))

    # Convert back to dicts if input was dicts
    if input_is_dict:
        return [s.to_dict() for s in split_slots]
    return split_slots


def _render_slot_detail(slot: dict | ReportTimelineSlot, detail_level: int, width: int) -> None:
    """Render category/app/title sub-rows for a slot according to detail_level.

    detail_level controls depth:
      1 = Project only          (no sub-rows)
      2 = Project + Task        (no sub-rows)
      3 = + Category
      4 = + App (indented under category)
      5 = + Title (indented under app)

    Note: AFK and OFFLINE slots may have categorized window activity recorded
    during those periods, so we render their details just like regular slots.
    """
    if detail_level >= 3:
        categories = slot.get("categories", []) if isinstance(slot, dict) else slot.categories
        for cat_info in categories:
            cat_dur_str = format_duration(cat_info["duration"])
            left = " " * 21 + f"- {cat_info['category']}"
            print(left.ljust(width - len(cat_dur_str) - 1) + " " + cat_dur_str)

            # Apps nested under category (level 4+)
            if detail_level >= 4:
                for app_info in cat_info.get("apps", []):
                    app_dur_str = format_duration(app_info["duration"])
                    left = " " * 25 + f"• {app_info['app']}"
                    print(left.ljust(width - len(app_dur_str) - 1) + " " + app_dur_str)

                    # Titles nested under app (level 5+)
                    if detail_level >= 5:
                        for title_info in app_info.get("titles", []):
                            title_dur_str = format_duration(title_info["duration"])
                            clean = truncate_title(title_info["title"], 75)
                            left = " " * 29 + clean
                            print(left.ljust(width - len(title_dur_str) - 1) + " " + title_dur_str)


def format_and_print_day_total_displayed(
    displayed_offline: timedelta,
    displayed_afk: timedelta,
    displayed_active: timedelta,
    productive_duration: timedelta | None,
    width: int = 120,
    displayed_break: timedelta | None = None,
    displayed_no_afk: timedelta | None = None,
    show_no_afk: bool = False,
) -> None:
    """Format and print day total using actual displayed column values.

    This is the accurate version that sums the actual values displayed in the timeline,
    rather than relying on potentially inflated accumulated metrics.

    Args:
        displayed_offline: Sum of OFFLINE column values displayed for the day
        displayed_afk: Sum of AFK column values displayed for the day
        displayed_active: Sum of ACTIVE column values displayed for the day
        productive_duration: Productive time in the day
        width: Terminal width for formatting
        displayed_break: Sum of break durations (gaps > 5 minutes) for the day
    """
    from aw_report.utils.formatting import display_width, ljust_display

    indent = " " * 7
    day_total_label = "Day total:   "

    # Format BREAK column (12 chars, matching OFFLINE and AFK width)
    break_col = ljust_display(
        format_duration(displayed_break)
        if displayed_break and displayed_break.total_seconds() > 0
        else "",
        12,
    )

    # Format each column using the actual displayed values
    offline_col = ljust_display(
        format_duration(displayed_offline)
        if displayed_offline and displayed_offline.total_seconds() > 0
        else "",
        12,
    )
    afk_col = ljust_display(
        format_duration(displayed_afk)
        if displayed_afk and displayed_afk.total_seconds() > 0
        else "",
        12,
    )
    active_col = ljust_display(
        format_duration(displayed_active)
        if displayed_active and displayed_active.total_seconds() > 0
        else "",
        12,
    )

    # Calculate online time as sum of afk + active
    total_online = (displayed_afk or timedelta(0)) + (displayed_active or timedelta(0))
    productivity_col = ljust_display(
        "  " + (f"[prod {_pct_of(productive_duration, total_online):>3.0f}%]")
        if productive_duration and productive_duration.total_seconds() > 0
        else "",
        14,
    )

    no_afk_col = (
        ljust_display(
            format_duration(displayed_no_afk)
            if displayed_no_afk and displayed_no_afk.total_seconds() > 0
            else "",
            12,
        )
        if show_no_afk
        else ""
    )
    right_section = f"{break_col}{offline_col}{afk_col}{active_col}{no_afk_col}{productivity_col}"
    left_part = ljust_display(f"{indent}{day_total_label}", 87)
    left_part_width = display_width(left_part)
    right_section_width_value = display_width(right_section)
    dynamic_left_padding = width - left_part_width - right_section_width_value - 2

    if dynamic_left_padding < 0:
        full_line = left_part + "  " + right_section
    else:
        full_line = left_part + (" " * dynamic_left_padding) + "  " + right_section
    print(full_line)


def print_timeline_report(
    slots: list[dict | ReportTimelineSlot],
    period: str,
    start_time: datetime,
    end_time: datetime,
    detail_level: int = 1,
    non_afk_time: timedelta | None = None,
    productive_time: timedelta | None = None,
    productive_task_time: timedelta | None = None,
    first_event_time: datetime | None = None,
    last_event_time: datetime | None = None,
    task_based: bool = True,
    distracting_time: timedelta | None = None,
    unscored_time: timedelta | None = None,
    current_session_start: datetime | None = None,
    current_session_end: datetime | None = None,
    current_session_duration: timedelta | None = None,
    last_break_start: datetime | None = None,
    last_break_end: datetime | None = None,
    last_break_duration: timedelta | None = None,
    afk_events: list[Event] | None = None,
    day_start_hour: int = 4,
    total_break: timedelta | None = None,
    tracked_activity: timedelta | None = None,
    productivity: ProductivityBreakdown | None = None,
    tz: tzinfo | None = None,
):
    """Print a timeline report: continuous time slots with date/week headers and totals.

    CRITICAL METRICS CALCULATION (Phase 5 bug fix, 2026-07-02):
    ===========================================================

    This function calculates and displays the following metrics:
      - Online Time: Total time from first to last activity (AFK + non-AFK combined)
      - Active Time: Total focused work time (non-AFK only)
      - AFK time: Total time away from keyboard
      - Project Tracking: % of online time on tracked (non-"No project assigned") tasks
      - Focus time: % time on high-priority tasks
      - Untracked productivity: % of untracked time on productive activities
      - Overall productivity: Total productive time / online time
      - Distracting time: Total time on distracting activities
      - Unscored time: Total time unscored

    The broken Phase 5 version (commit 12def2d) failed to:
      - Calculate AFK time separately (showed as reduced Online Time)
      - Include AFK metrics in display
      - Calculate project tracking percentage (showed 0% instead of 89%)
      - Include focus time metric

    This version correctly:
      - Separates AFK time from active time calculation
      - Includes AFK metrics in the report header
      - Properly calculates project tracking percentage
      - Includes all required metrics

    CONSOLIDATION RENDERING (when --consolidate flag used):
    ========================================================
    When slots are consolidated (merged same project/task entries), this
    function detects consolidation via the presence of "afk_duration" field
    and adjusts metrics calculation accordingly:
      - has_consolidated_afk flag: True if consolidation merged AFK gaps
      - Consolidated AFK time is already in the afk_duration field
      - Regular (non-consolidated) AFK time is calculated from type="afk" slots

    DETAIL LEVEL RENDERING:
    =======================
    detail_level controls rendering depth:
      1 = Project only (no sub-rows)
      2 = Project + Task (no sub-rows, but shows task name)
      3 = + Category details (sub-rows showing category breakdown)
      4 = + App (indented under category)
      5 = + Title (window titles under app)

    Each level is rendered via _render_slot_detail() which shows indented
    breakdowns of how time was distributed across categories/apps/titles.

    GAP DETECTION & BREAK RENDERING (2026-07-23, refined 2026-09-18):
    =================================================================
    Blank lines appear between work sessions with gaps >= 1 minute, showing the
    break duration. This improves readability by visually separating work sessions
    from breaks and helps users identify distinct work periods.

    Implementation:
      - Track last_slot_end time as slots are rendered (initialized to None)
      - Before rendering each slot, compute gap from last_slot_end to current slot.start
      - Use day-aware gap calculation to prevent breaks from spanning logical day boundaries
      - If gap >= 1 minute and within same logical day, render a break row with duration
      - Update last_slot_end after rendering each slot
      - Applied to all rendering paths: offline_task singletons, single-slot inline, multi-slot loop

    PARAMETER NOTES:
      slots: Pre-filtered timeline slots from main() (EventFilter already applied)
      period: Human-readable period description (e.g., ":yesterday", "2026-07-01")
      detail_level: See above (1-5, typically 1-2 for most users)
      non_afk_time: Total Active Time (non-AFK) from metrics (for % calculations)
      productive_time: Total productive time from metrics
      (other metric parameters used for header display)
    """

    # Normalise every input to ReportTimelineSlot. Dict slots that cannot be built are skipped.
    from aw_report.core.report_slot import ReportTimelineSlot

    normalized: list[ReportTimelineSlot] = []
    for slot_item in slots:
        if isinstance(slot_item, dict):
            # Skip malformed slots
            with contextlib.suppress(Exception):
                normalized.append(ReportTimelineSlot.from_dict(slot_item))
        else:
            normalized.append(slot_item)

    # The NO AFK column is drawn only when some slot has time without AFK data
    show_no_afk = any(s.breakdown.no_afk_data > timedelta(0) for s in normalized)

    width = get_terminal_width()
    is_single_day = logical_date(start_time, day_start_hour) == logical_date(
        end_time, day_start_hour
    )

    # Use actual_duration for merged slots, duration for others
    # Include all slots (offline, regular, afk, offline_task) in totals
    # OFFLINE gaps represent system-off time and should be counted
    all_regular_slots = normalized
    # Project-tracked time (excluding "No project assigned")
    tracked_slots = [s for s in all_regular_slots if s.project != NO_PROJECT]

    total_duration = sum(
        (_get_displayed_duration(slot) for slot in tracked_slots),
        timedelta(0),
    )
    # Total time including untracked (for "Total Time" display)
    # Use displayed durations: slot.duration for regular/AFK, actual_duration for OFFLINE
    total_time_all = sum(
        (_get_displayed_duration(slot) for slot in all_regular_slots),
        timedelta(0),
    )
    # Total productive time for all slots (including untracked)
    total_productive_all = sum(
        (slot.productive_duration for slot in all_regular_slots),
        timedelta(0),
    )

    # Calculate total AFK and OFFLINE time using the shared calculation
    # This ensures both timeline and hierarchical reports show identical TOTALS
    # CRITICAL: Calculate totals AFTER period-based consolidation, not here
    # Early calculation on pre-consolidation slots counts duplicates multiple times
    # See line ~1036 for the CORRECT calculation on deduplicated slots
    # Placeholder values will be replaced after consolidation
    total_afk_time = timedelta(0)
    total_offline_time = timedelta(0)

    # Calculate total_time_including_offline for Project Tracking percentage denominator
    # Project Tracking should be calculated as % of total time (online + offline)
    # This ensures the percentage matches the "Total Time" value displayed
    total_time_including_offline = total_time_all + total_offline_time

    # Print SUMMARY at top
    # Use passed tracked_activity from slots (source of truth), or calculate from event times
    tracked_activity_span = tracked_activity
    if tracked_activity_span is None and first_event_time and last_event_time:
        tracked_activity_span = last_event_time - first_event_time

    print_report_summary(
        title=" Timeline Report ",
        period=period,
        start_time=start_time,
        end_time=end_time,
        total_duration=total_duration,
        task_based=task_based,
        non_afk_time=non_afk_time,
        productive_time=productive_time,
        productive_task_time=productive_task_time,
        first_event_time=first_event_time,
        last_event_time=last_event_time,
        tracked_activity=tracked_activity_span,
        distracting_time=distracting_time,
        unscored_time=unscored_time,
        current_session_start=current_session_start,
        current_session_end=current_session_end,
        current_session_duration=current_session_duration,
        last_break_start=last_break_start,
        last_break_end=last_break_end,
        last_break_duration=last_break_duration,
        total_offline_time=total_offline_time,
        afk_time=total_afk_time,
        total_time_all=total_time_including_offline,
    )

    if not normalized:
        print("No activity found for the specified period.")

    # CRITICAL: Print date/week header with duration column labels on same line
    # ============================================================================
    # ALIGNMENT ISSUES FIXED (2026-07-28):
    # 1. Header must use IDENTICAL structure to DisplayColumns.format()
    # 2. Terminal width must be dynamic, not fixed positions
    # 3. Right section width MUST include ALL columns (even empty ones)
    #
    # PAST BUGS (lessons learned):
    # - Bug 1: Using fixed byte positions (89, 101, 113) instead of dynamic
    #   terminal_width padding. On wider terminals (150+ chars), columns
    #   appeared completely misaligned because labels stayed at 89 but
    #   durations moved to position 150+.
    #   Fix: Use same terminal_width calculation as DisplayColumns
    #
    # - Bug 2: Header missing PRODUCTIVITY column (14 chars) that DisplayColumns
    #   includes. This caused 14-char difference in right_section_width:
    #   - DisplayColumns: 46 width (12+12+8+14)
    #   - Header (broken): 32 width (12+12+8)
    #   Result: Different left_padding calculations = misalignment on all widths
    #   Fix: Include productivity column (14 empty spaces) in header
    #
    # - Bug 3: Using ljust() instead of ljust_display() for multi-byte UTF-8.
    #   ljust() counts bytes, not display width. With UTF-8 chars (▶, ñ, etc.),
    #   columns drifted. Fix: Use ljust_display() everywhere.
    # ============================================================================

    from aw_report.utils.formatting import display_width, ljust_display

    header_text = "Wk  Date       Day"

    # Match DisplayColumns.format() structure EXACTLY:
    # Left section: 7 indent + 13 time + 2 sep + 28 project + 2 sep + 35 task = 87
    header_left_visual_width = 87
    header_text_width = display_width(header_text)
    padding_to_left_section = header_left_visual_width - header_text_width

    header_left_section = header_text + (" " * padding_to_left_section)

    # CRITICAL: Right section MUST include ALL columns DisplayColumns uses
    # Format: BREAK(12) + OFFLINE(12) + AFK(12) + ACTIVE(12) + PRODUCTIVITY(14) = 62 total
    # If you add/remove/resize any duration column in DisplayColumns, update here too!
    header_right_section = (
        ljust_display("UNTRACKED", 12)
        + ljust_display("OFFLINE", 12)
        + ljust_display("AFK", 12)
        + ljust_display("ACTIVE", 12)
        + (ljust_display("NO AFK", 12) if show_no_afk else "")
        + ljust_display("", 14)
    )

    # Use SAME fixed column positioning logic as DisplayColumns.format()
    # This ensures header columns align with data columns
    terminal_width = width
    total_right_width = right_section_width(show_no_afk)  # matches DisplayColumns
    column_start_pos = terminal_width - total_right_width

    left_section_width = display_width(header_left_section)

    # Build header using IDENTICAL logic to DisplayColumns.format()
    if left_section_width >= column_start_pos:
        # Left section too long, just append right section
        header_line = header_left_section + " " + header_right_section
    else:
        # Pad to position right section at fixed column start position
        left_padding = column_start_pos - left_section_width
        header_line = header_left_section + (" " * left_padding) + header_right_section

    print(header_line)

    # BUG: Day/Week grouping inconsistency with day_start_hour (Session 2026-08-31)
    # ============================================================================
    # When day_start_hour is set (default 4), logical days start at 4 AM instead of midnight.
    # Currently: slot_week_key uses ISO calendar (strftime "%G-W%V"), but slot_date uses
    # logical_date(day_start_hour).
    # Problem: These two calculations are INDEPENDENT, causing:
    #   1. Same calendar date split into multiple logical days → appears multiple times with
    # separate totals
    #   2. Week numbers flip for same calendar date (W35, W36, W35, W36 for 2026-08-30)
    #   3. "Day total" lines don't accumulate properly — each logical day shown separately
    #
    # Example from session 2026-08-31 output:
    #   W35 2026-08-30 Sun [entries] Day total: 03:02:36
    #   W36 2026-08-30 Sun [entries] Day total: 03:18:44  ← WRONG: same calendar date, different
    # totals!
    #   W35 2026-08-30 Sun [entries] Day total: 03:18:44
    #   W36 2026-08-30 Sun [entries] Day total: 03:51:56
    #
    # Root cause: slot_week_key uses calendar dates (via ISO calendar), not logical dates.
    # When logical day != calendar day, week numbers can differ for entries within same calendar
    # day.
    #
    # Solution: Calculate week key from logical_date, not calendar strftime:
    #   OLD: slot.start.strftime("%G-W%V")  # Uses calendar date
    #   NEW: Derive week from logical_date(slot.start, day_start_hour) using Monday-start logic
    #        iso_week = logical_date.isocalendar()[1]
    #        week_key = f"{logical_date.year}-W{iso_week:02d}"
    #
    # This ensures:
    #   - Same logical day always in same week (no W35/W36 flipping)
    #   - Entries consolidated by logical day boundary, not calendar boundary
    #   - Week totals accumulate correctly
    #
    # Migration: Update slot_week_key() to use logical_date; update slot_week assignment at line
    # 953.
    # Test: Run "tw-report --by-day :today" with day_start_hour=4; verify no day repeats with
    # different weeks.

    # Use module-level grouping functions (extracted for testability)
    def slot_week_key(slot):
        """Return ISO week key based on LOGICAL date: 'YYYY-Www' (e.g., '2026-W17')"""
        return get_slot_week_key(slot, day_start_hour)

    def slot_date(slot):
        """Return slot logical date"""
        return get_slot_logical_date(slot, day_start_hour)

    # Split slots spanning multiple days
    normalized = split_slots_spanning_days(normalized, day_start_hour)

    # Filter to only include slots within the requested date range
    # After splitting, we should only show portions that fall within [start_time, end_time)
    normalized = [
        s for s in normalized if s.start < end_time and (s.start + s.duration) > start_time
    ]

    # Recalculate total_time_all after filtering to match the displayed slots
    # Include OFFLINE gaps in totals (they represent system-off time)
    total_time_all = sum(
        (_get_displayed_duration(slot) for slot in normalized),
        timedelta(0),
    )
    total_productive_all = sum(
        (slot.productive_duration for slot in normalized),
        timedelta(0),
    )

    # Sort slots by start time
    normalized = sorted(normalized, key=lambda s: s.start)

    # Filter out slots where display time range equals zero (confusing display like "11:19 - 11:19")
    # These occur when slot["duration"] represents wall-clock time but is zero
    normalized = [
        s
        for s in normalized
        if s.duration > timedelta(milliseconds=100)  # > 100ms to handle rounding
    ]

    # ============================================================================
    # PERIOD-AWARE CONSOLIDATION
    # ============================================================================
    # Consolidate slots by (project, task) within each logical period.
    # This ensures consolidation respects the rendering granularity:
    # - --by-day: consolidate within each day only
    # - --by-week: consolidate within each week only
    # - --by-month: consolidate within each month only
    # This prevents cross-period consolidation that creates 24+ hour entries.

    # Period grouping was write-only and has been removed; consolidation happens in main.py.
    # NOTE: Consolidation now happens in main.py before rendering,
    # so we skip it here to avoid double-consolidation which causes
    # small discrepancies in OFFLINE time calculations (17 sec differences).
    # The slots arriving here are already deduplicated and consolidated.
    # Just convert dicts to objects if needed and sort
    normalized = sorted(normalized, key=lambda s: s.start)
    # Display rows: touching slots of the same task and AFK state become one row (totals unchanged)
    normalized = merge_display_rows(normalized, day_start_hour, tz or local_zoneinfo())

    # CRITICAL: Recalculate total_time_all after all filtering/deduplication
    # The previous calculation (line 555) included slots that are now filtered out.
    # This must happen AFTER zero-duration filtering and OFFLINE deduplication.
    # Include OFFLINE gaps in totals (they represent system-off time)
    total_time_all = sum(
        (_get_displayed_duration(slot) for slot in normalized),
        timedelta(0),
    )
    total_productive_all = sum(
        (slot.productive_duration for slot in normalized),
        timedelta(0),
    )

    # RECALCULATE AFK and OFFLINE TOTALS POST-consolidation
    # Use consolidated slots for accuracy (avoids double-counting duplicates)
    # This replaces the early placeholder calculation at line 707

    # AFK and OFFLINE totals (shared with the hierarchical report)
    total_afk_time, total_offline_time = compute_afk_offline_totals(normalized)

    # ============================================================================
    # DAY/WEEK METRICS ACCUMULATION
    # ============================================================================
    # This section tracks metrics for each day and week as we render the timeline.
    # The current implementation uses 8 separate variables (day_duration, day_afk_duration,
    # day_offline_duration, day_productive, and their week_ equivalents).
    #
    # FUTURE REFACTORING (Phase 4+):
    # These variables should be replaced with PeriodMetrics objects:
    #
    #   daily_metrics = PeriodMetrics()  # replaces: day_duration, day_afk_duration, etc.
    #   weekly_metrics = PeriodMetrics() # replaces: week_duration, week_afk_duration, etc.
    #
    # Migration pattern for accumulation sites:
    #
    #   OLD:  day_duration += online; day_afk_duration += afk; ...
    #   NEW:  daily_metrics.add(online=online, afk=afk, offline=offline, productive=prod)
    #
    # Or for TimeslotDuration objects:
    #
    #   NEW:  daily_metrics.add_timeslot(slot_duration, productive=prod)
    #
    # Benefits of refactoring:
    #   - Single source of truth for metrics (PeriodMetrics object)
    #   - Clearer intent: grouped metrics rather than scattered variables
    #   - Type safety: invalid metric combinations prevented at construction
    #   - Easier to extend: add new metrics without adding new variables
    #   - Self-documenting: properties like .total_duration and .active_duration
    #
    # Call sites to refactor:
    #   - Line 817-823: offline_task accumulation
    #   - Line 1076-1082: regular_slot accumulation loop
    #   - Line 1090-1100: day/week totals display and reset
    #
    # ============================================================================

    # Group by week, then by date within week
    current_week_key = None
    current_date = None

    # Use PeriodMetrics for cleaner day/week accumulation
    # These replace: week_duration, day_duration, week_productive, day_productive,
    #               week_afk_duration, day_afk_duration, week_offline_duration, day_offline_duration
    daily_metrics = PeriodMetrics()
    weekly_metrics = PeriodMetrics()

    # Track displayed column values separately by summing the actual rendered values
    # This avoids metric calculation bugs and uses the source of truth: what's actually displayed
    total_break_time = timedelta(0)  # Accumulated across entire report
    daily_displayed_offline = timedelta(0)
    daily_displayed_afk = timedelta(0)
    daily_displayed_active = timedelta(0)
    daily_displayed_untracked = timedelta(0)
    weekly_displayed_offline = timedelta(0)
    weekly_displayed_afk = timedelta(0)
    weekly_displayed_active = timedelta(0)
    weekly_displayed_untracked = timedelta(0)
    daily_displayed_no_afk = timedelta(0)
    weekly_displayed_no_afk = timedelta(0)

    # Track pending week total to print AFTER we have authoritative online_time from AFK bucket
    # This ensures week total uses same data source (AFK bucket) as TOTALS section
    pending_week_total_data = None  # Will store (productive_duration, offline_gap) when week ends

    # Slots are already sorted and filtered by this point (line 729).
    # Render each slot individually — no grouping.
    pending_date_prefix = None  # Date header held until the next slot prints

    # BUG: Intercalated day rendering in --by-day mode (Session 2026-08-31)
    # ================================================================
    # The grouping logic here uses slot_week (calendar-based ISO week) and slot_date_val (logical
    # day).
    # When these two calculations are misaligned (due to day_start_hour), we get:
    #   1. Multiple entries for same calendar date (2026-08-30 appears 5+ times)
    #   2. Each appearance gets its own "Day total" line (should consolidate into ONE)
    #   3. Week numbers alternate (W35, W36, W35, W36) for same calendar date
    #
    # Current flow:
    #   - slot_week = slot.start.strftime("%G-W%V")  ← Uses calendar date
    #   - slot_date_val = slot_date(slot)            ← Uses logical date
    #   - When week changes: print week total, reset metrics, show new date header
    #   - When date changes (but week same): print day total, reset daily metrics only
    #   - But slot_week and slot_date_val can DISAGREE on boundaries
    #
    # Example: 2026-08-30 23:42-00:14 (spans logical day boundary at 04:00)
    #   - Calendar: both in 2026-08-30, both in W35
    #   - Logical (day_start_hour=4): split into 2026-08-30 (W35) and 2026-08-31 (W36)
    #   - Result: Same calendar date, different weeks → renders twice!
    #
    # Fix: Use logical_date for BOTH week and date calculations.
    # See slot_week_key() BUG comment above for implementation details.

    # Helper to check if slot has event_duration (field-driven offline_task detection)
    # Process each slot individually
    for slot in normalized:
        slot_week = slot_week_key(slot)  # Now uses logical_date (fixed 2026-08-31)
        slot_date_val = slot_date(slot)

        if slot_week != current_week_key:
            # Week changed: print previous week's closing totals
            if current_week_key is not None:
                print(_separator_line(width, show_no_afk))
                # Use displayed values for accurate day totals instead of accumulated metrics
                format_and_print_day_total_displayed(
                    daily_displayed_offline,
                    daily_displayed_afk,
                    daily_displayed_active,
                    daily_metrics.productive_duration,
                    width,
                    daily_displayed_untracked,
                    displayed_no_afk=daily_displayed_no_afk,
                    show_no_afk=show_no_afk,
                )
                # DEFER week total printing until after we have authoritative online_time from
                # AFK bucket
                # Save the data for printing after the render loop
                pending_week_total_data = (
                    weekly_metrics.productive_duration,
                    weekly_metrics.offline_gap,
                )
                print()
            current_week_key = slot_week
            # Extract week number from week_key (format: 'YYYY-Www')
            week_number = int(current_week_key.split("-W")[1])
            week_str = f"W{week_number}"
            date_str = slot_date_val.strftime("%Y-%m-%d")
            day_str = slot_date_val.strftime("%a")
            pending_date_prefix = f"{week_str} {date_str} {day_str}"
            current_date = slot_date_val
            daily_metrics = PeriodMetrics()
            weekly_metrics = PeriodMetrics()
        elif slot_date_val != current_date:
            # Date changed within same week: close previous day. (current_date is always set by
            # the first slot's week-change branch, so there is no 'previous day' guard here.)
            print(_separator_line(width, show_no_afk))
            # Use displayed values for accurate day totals instead of accumulated metrics
            format_and_print_day_total_displayed(
                daily_displayed_offline,
                daily_displayed_afk,
                daily_displayed_active,
                daily_metrics.productive_duration,
                width,
                daily_displayed_untracked,
                displayed_no_afk=daily_displayed_no_afk,
                show_no_afk=show_no_afk,
            )
            print()
            daily_metrics = PeriodMetrics()
            daily_displayed_offline = timedelta(0)
            daily_displayed_afk = timedelta(0)
            daily_displayed_active = timedelta(0)
            daily_displayed_untracked = timedelta(0)
            daily_displayed_no_afk = timedelta(0)
            date_str = slot_date_val.strftime("%Y-%m-%d")
            day_str = slot_date_val.strftime("%a")
            # Align same-week dates: 4 spaces + date + day
            pending_date_prefix = f"    {date_str} {day_str}"
            current_date = slot_date_val

        # Flush pending date header before rendering this slot
        if pending_date_prefix is not None:
            print(pending_date_prefix)
            pending_date_prefix = None

        # UNIFIED SLOT RENDERING (2026-07-30):
        # Single code path for ALL slot types (offline_task, regular, afk).
        # All slots are treated uniformly based on their properties, not special-cased by type.
        # This ensures consistent accumulation of OFFLINE/AFK/ACTIVE columns regardless of source.

        # Extract slot properties uniformly (works for dict or object)
        def get_slot_attr(s, attr, default=None):
            return getattr(s, attr, default)

        slot_start = slot.start
        slot_end = slot.end if hasattr(slot, "end") else slot.start + slot.duration
        slot_productive = get_slot_attr(slot, "productive_duration")

        # Render slot with DisplayColumns (works for all slot types)
        slot_start_local = to_local_time(slot_start)
        slot_end_local = to_local_time(slot_end)

        from aw_report.core.report_slot import DisplayColumns

        cols = DisplayColumns.from_slot_dict(slot, slot_start_local, slot_end_local)
        print(cols.format(width, show_no_afk))

        # Render detail (categories/apps/titles for detail_level >= 3)
        # Skip for offline_task slots (no window data) - field-driven check
        if slot.entry_type != "offline_task":
            _render_slot_detail(slot.to_dict(), detail_level, width)

        # UNIFIED METRICS & ACCUMULATION (field-driven, no type discrimination):
        # All slots contribute their duration components to metrics.
        # Key insight: online_duration and offline_gap are computed from fields, not from type.
        # Consolidation allows a single slot to have multiple components (ACTIVE + AFK + OFFLINE).

        # Online/offline/AFK time comes from the slot's breakdown. Untracked and no-AFK-data
        # slots have neither online nor offline time, so they add no TimeslotDuration here.
        breakdown = slot.breakdown
        online_duration = breakdown.online if breakdown.online > timedelta(0) else None
        offline_gap = breakdown.offline if breakdown.offline > timedelta(0) else None
        slot_afk = breakdown.afk if breakdown.afk > timedelta(0) else None
        if online_duration is not None or offline_gap is not None:
            slot_duration_obj = TimeslotDuration(
                online_duration=online_duration,
                offline_gap=offline_gap,
                afk_portion=slot_afk,
            )
            daily_metrics.add_timeslot(slot_duration_obj, productive=slot_productive)
            weekly_metrics.add_timeslot(slot_duration_obj, productive=slot_productive)

        # COLUMN ACCUMULATION: the same breakdown values the row displays
        daily_displayed_offline += breakdown.offline
        weekly_displayed_offline += breakdown.offline
        daily_displayed_afk += breakdown.afk
        weekly_displayed_afk += breakdown.afk
        daily_displayed_active += breakdown.active
        weekly_displayed_active += breakdown.active
        daily_displayed_untracked += breakdown.untracked
        weekly_displayed_untracked += breakdown.untracked
        daily_displayed_no_afk += breakdown.no_afk_data
        weekly_displayed_no_afk += breakdown.no_afk_data
        total_break_time += breakdown.untracked

    # Print final totals
    if normalized:
        print(_separator_line(width, show_no_afk))
        # CRITICAL FIX (2026-08-31): Use displayed values accumulated during rendering, not metrics
        # daily_metrics has ambiguous semantics; use the values actually accumulated in the display
        # loop
        # daily_displayed_offline, daily_displayed_afk, daily_displayed_active are the canonical
        # display values
        # BUG FIX: Use daily_displayed_active, NOT non_afk_time (which is the entire week's active
        # time)
        daily_active = daily_displayed_active
        format_and_print_day_total_displayed(
            daily_displayed_offline,
            daily_displayed_afk,
            daily_active,
            daily_metrics.productive_duration,
            width,
            daily_displayed_untracked,
            displayed_no_afk=daily_displayed_no_afk,
            show_no_afk=show_no_afk,
        )
        print()
        # Save final week's totals (flush at end of render loop)
        if not is_single_day:
            pending_week_total_data = (
                weekly_metrics.productive_duration,
                weekly_metrics.offline_gap,
            )

    # Calculate authoritative online time from AFK bucket BEFORE printing week total
    # This ensures week total uses same data source (AFK bucket) as TOTALS section
    # Online time must satisfy: Online = Active Time + AFK Time
    # Active Time comes from non_afk_time (AFK bucket not-afk events)
    # AFK Time comes from total_afk_time (from final slots or AFK bucket afk events)
    #
    # Note: We use non_afk_time from AFK bucket (parameter) which is the authoritative
    # source, matching what the summary uses. This may differ from what slots show
    # if there's untracked/gap time in "No project assigned" slots.

    # Use non_afk_time parameter (from AFK bucket) as Active Time
    active_time_final = non_afk_time if non_afk_time else timedelta(0)

    # Online = Active + AFK (this ensures the mathematical relationship holds)
    online_time_final = active_time_final + total_afk_time

    # NOW print deferred week total using authoritative online_time_final
    if not is_single_day and pending_week_total_data is not None:
        productive_duration, offline_gap = pending_week_total_data
        # Format offline time in gap notation if present
        if offline_gap and offline_gap > timedelta(0):
            gaps_str = f"({format_duration(offline_gap)} OFF)"
        else:
            gaps_str = ""
        # Use authoritative online_time_final from AFK bucket (matches TOTALS section)
        base_duration = format_duration_tracked_prod(online_time_final, productive_duration)
        right_part = f"{gaps_str}  {base_duration}" if gaps_str else base_duration
        # Week total aligned with week header (0 spaces) for pyramid shape
        left_part = "Week total (tracked):  "
        full_line = left_part.ljust(width - len(right_part) - 2) + "  " + right_part
        print(full_line.rstrip())
        print()

    print_report_totals(
        total_time_all=online_time_final,
        total_productive_all=total_productive_all,
        total_afk=total_afk_time if total_afk_time > timedelta(0) else None,
        total_offline=total_offline_time if total_offline_time > timedelta(0) else None,
        total_non_afk=active_time_final if active_time_final > timedelta(0) else None,
        total_break=total_break
        if total_break is not None
        else (total_break_time if total_break_time > timedelta(0) else None),
        tracked_activity=tracked_activity_span,
        first_event_time=first_event_time,
        last_event_time=last_event_time,
        productivity=productivity,
    )
