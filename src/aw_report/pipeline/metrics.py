"""Totals over slots, computed from each slot's breakdown (see core/time_breakdown.py)."""

from collections.abc import Iterable

from aw_report.core.report_slot import ReportTimelineSlot
from aw_report.core.time_breakdown import TimeBreakdown, sum_breakdowns


def state_totals(slots: Iterable[ReportTimelineSlot]) -> TimeBreakdown:
    """Active, AFK, untracked, offline and no-AFK-data time of the given slots."""
    return sum_breakdowns(slot.breakdown for slot in slots)
