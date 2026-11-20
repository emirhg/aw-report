"""Every production-unused API is marked deprecated and warns when it is called.

The marker is PEP 702 (`__deprecated__`), set by typing_extensions / warnings.deprecated.
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from tw_report.cli import main as cli_main
from tw_report.core.aw_events import TaskWarriorEvent
from tw_report.core.report_slot import ReportEntries, ReportTimelineSlot, _merge_categories_full
from tw_report.core.task_matching import task_has_offline_tag
from tw_report.core.timeline import Timeline, TimelineSlot
from tw_report.pipeline import report_render, timeline_render

UTC = UTC
T0 = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)


def _work() -> TimelineSlot:
    d = timedelta(hours=1)
    return TimelineSlot(
        type="regular",
        start=T0,
        end=T0 + d,
        duration=d,
        actual_duration=d,
        productive_duration=d,
        project="P",
        task="T",
        categories=[],
        tags=[],
    )


def _report_slot() -> ReportTimelineSlot:
    return ReportTimelineSlot.from_timeline_slot(_work())


def _h(hours):
    return timedelta(hours=hours)


def _print_report_header_args() -> dict[str, Any]:
    return dict(
        title="Hdr",
        period="p",
        start_time=T0,
        end_time=T0 + _h(1),
        total_duration=_h(1),
        task_based=True,
    )


# (name, marked object, call that must warn)
CASES = [
    (
        "TimelineSlot",
        TimelineSlot,
        lambda: TimelineSlot(
            type="regular",
            start=datetime(2026, 8, 30, 9, tzinfo=timezone.utc),
            end=datetime(2026, 8, 30, 10, tzinfo=timezone.utc),
            project="P",
            task="T",
            duration=timedelta(hours=1),
            actual_duration=timedelta(hours=1),
        ),
    ),
    ("Timeline", Timeline, lambda: Timeline()),
    (
        "ReportEntries.as_dicts",
        ReportEntries.as_dicts,
        lambda: ReportEntries().as_dicts(),
    ),
    (
        "ReportEntries.combine_work_with_embedded_afk",
        ReportEntries.combine_work_with_embedded_afk,
        lambda: ReportEntries().combine_work_with_embedded_afk(),
    ),
    (
        "ReportEntries.consolidate_consecutive",
        ReportEntries.consolidate_consecutive,
        lambda: ReportEntries().consolidate_consecutive(),
    ),
    (
        "ReportEntries.grouped_by_project_date",
        ReportEntries.grouped_by_project_date,
        lambda: ReportEntries().grouped_by_project_date(),
    ),
    ("ReportEntries.bucket", ReportEntries.bucket, lambda: ReportEntries().bucket("day")),
    (
        "ReportEntries.collapse_to_project",
        ReportEntries.collapse_to_project,
        lambda: ReportEntries().collapse_to_project(),
    ),
    (
        "ReportEntries.consolidate_by_task",
        ReportEntries.consolidate_by_task,
        lambda: ReportEntries().consolidate_by_task(),
    ),
    (
        "ReportEntries._merge_overlapping_work_slots",
        ReportEntries._merge_overlapping_work_slots,
        lambda: ReportEntries()._merge_overlapping_work_slots([]),
    ),
    (
        "ReportEntries._deduplicate_slots",
        ReportEntries._deduplicate_slots,
        lambda: ReportEntries()._deduplicate_slots(),
    ),
    (
        "ReportTimelineSlot.from_timeline_slots",
        ReportTimelineSlot.from_timeline_slots,
        lambda: ReportTimelineSlot.from_timeline_slots([_work()]),
    ),
    (
        "ReportTimelineSlot.from_work_slot_with_embedded_afk",
        ReportTimelineSlot.from_work_slot_with_embedded_afk,
        lambda: ReportTimelineSlot.from_work_slot_with_embedded_afk(_work(), []),
    ),
    (
        "ReportTimelineSlot.bucket_key",
        ReportTimelineSlot.bucket_key,
        lambda: _report_slot().bucket_key("day"),
    ),
    (
        "ReportTimelineSlot.is_offline_gap",
        ReportTimelineSlot.is_offline_gap.fget,
        lambda: _report_slot().is_offline_gap,
    ),
    ("_merge_categories_full", _merge_categories_full, lambda: _merge_categories_full([])),
    (
        "task_has_offline_tag",
        task_has_offline_tag,
        lambda: task_has_offline_tag(TaskWarriorEvent(timestamp=T0, duration=_h(1), data={})),
    ),
    (
        "report_render.print_summary_total",
        report_render.print_summary_total,
        lambda: report_render.print_summary_total(_h(1)),
    ),
    (
        "report_render.print_report_header",
        report_render.print_report_header,
        lambda: report_render.print_report_header(**_print_report_header_args()),
    ),
    (
        "timeline_render.calculate_break_duration",
        timeline_render.calculate_break_duration,
        lambda: timeline_render.calculate_break_duration(T0, T0 + _h(1)),
    ),
    (
        "timeline_render.get_gap_between_slots",
        timeline_render.get_gap_between_slots,
        lambda: timeline_render.get_gap_between_slots({"end": T0}, {"start": T0 + _h(1)}),
    ),
    (
        "timeline_render.format_break_column",
        timeline_render.format_break_column,
        lambda: timeline_render.format_break_column(None),
    ),
    (
        "cli.main._matches_any",
        cli_main._matches_any,
        lambda: cli_main._matches_any("x", None, False),
    ),
    ("cli.main._excluded", cli_main._excluded, lambda: cli_main._excluded("x", None)),
]


@pytest.mark.parametrize("name, target, _call", CASES, ids=[c[0] for c in CASES])
def test_target_carries_the_pep702_marker(name, target, _call):
    message = getattr(target, "__deprecated__", None)
    assert isinstance(message, str) and message, f"{name} is not marked deprecated"


@pytest.mark.parametrize("name, _target, call", CASES, ids=[c[0] for c in CASES])
def test_calling_the_target_emits_a_deprecation_warning(name, _target, call):
    with pytest.deprecated_call():
        call()


def test_category_layout_warns_from_both_report_entry_points(capsys):
    summary_args = dict(
        title="Report",
        period="p",
        start_time=T0,
        end_time=T0 + _h(1),
        total_duration=_h(1),
        task_based=False,
    )
    with pytest.deprecated_call(match="Category-based layout"):
        report_render.print_report_summary(**summary_args)
    with pytest.deprecated_call(match="Category-based layout"):
        report_render.print_report(
            report_data={},
            period="p",
            start_time=T0,
            end_time=T0 + _h(1),
            task_based=False,
        )
    capsys.readouterr()
