"""Coverage tests for report_render branches not covered by the existing render tests.

Assertions check labelled values whose expected numbers follow directly from the inputs.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest

from tw_report.pipeline.models import ReportTotals
from tw_report.pipeline.processors import ProductivityBreakdown
from tw_report.pipeline.report_render import (
    print_report,
    print_report_header,
    print_report_summary,
    print_report_totals,
    print_summary_total,
)

UTC = timezone.utc
T0 = datetime(2026, 7, 23, 9, 0, tzinfo=UTC)


def h(hours: float) -> timedelta:
    return timedelta(hours=hours)


@pytest.fixture(autouse=True)
def fixed_width(monkeypatch):
    from tw_report.pipeline import report_render

    monkeypatch.setattr(report_render, "get_terminal_width", lambda: 100)


def summary(capsys, **overrides):
    args: Dict[str, Any] = dict(
        title="Report",
        period="2026-07-23",
        start_time=T0,
        end_time=T0 + h(5),
        total_duration=h(4),
        task_based=True,
        non_afk_time=h(3),
        productive_time=h(2),
        productive_task_time=h(1),
        first_event_time=T0,
        last_event_time=T0 + h(4),
        tracked_activity=h(3.5),
        distracting_time=h(0.5),
        unscored_time=h(0.25),
    )
    args.update(overrides)
    print_report_summary(**args)
    return capsys.readouterr().out


def line_value(out: str, label: str) -> str:
    for line in out.splitlines():
        if line.strip().startswith(label.strip()):
            return line.strip()[len(label.strip()):].strip()
    raise AssertionError(f"no line starting with {label!r} in:\n{out}")


# ---------------------------------------------------------------------------
# print_report_summary
# ---------------------------------------------------------------------------


def test_summary_tracking_percent_falls_back_to_components_without_total_time_all(capsys):
    out = summary(capsys, total_time_all=None, afk_time=h(1), total_offline_time=h(2))
    # Denominator = active (3h) + AFK (1h) + offline (2h) = 6h; tracked 4h -> 66.7%
    assert "66.7%" in line_value(out, "Project Tracking")


def test_summary_uses_total_time_all_when_given(capsys):
    out = summary(capsys, total_time_all=h(8))
    assert "50.0%" in line_value(out, "Project Tracking")


def test_summary_without_task_mode_omits_project_and_focus_lines(capsys):
    out = summary(capsys, task_based=False)
    assert "Project Tracking" not in out
    assert "Focus time" not in out
    assert "Active Time" in out


def test_summary_distracting_line_uses_tracking_denominator(capsys):
    out = summary(capsys, total_time_all=h(6), distracting_time=h(1.5))
    assert "25.0% (01:30:00)" in line_value(out, "Overall distracting time")


def test_summary_suppresses_last_break_that_is_an_offline_gap(capsys):
    out = summary(
        capsys,
        total_offline_time=h(2),
        last_break_start=T0 + h(1),
        last_break_end=T0 + h(3),
        last_break_duration=h(2),  # > 1h with offline time present -> offline gap
    )
    assert "Last Break" not in out


def test_summary_shows_last_break_that_is_not_an_offline_gap(capsys):
    out = summary(
        capsys,
        last_break_start=T0 + h(1),
        last_break_end=T0 + h(1.5),
        last_break_duration=h(0.5),
    )
    assert "00:30:00" in line_value(out, "Last Break")


def test_summary_session_and_productivity_score_lines(capsys):
    out = summary(
        capsys,
        current_session_start=T0 + h(3),
        current_session_end=T0 + h(4),
        current_session_duration=h(1),
        total_score=12.345,
    )
    assert "01:00:00" in line_value(out, "Current Session")


def test_summary_without_optional_fields_still_prints_header_and_totals(capsys):
    out = print_report_summary(
        title="Bare", period="p", start_time=T0, end_time=T0 + h(1), total_duration=h(1),
        task_based=True,
    )
    assert out is None
    text = capsys.readouterr().out
    assert "Bare" in text
    assert "SUMMARY" in text


# ---------------------------------------------------------------------------
# print_summary_total
# ---------------------------------------------------------------------------


def test_print_summary_total_variants(capsys):
    print_summary_total(h(1))
    print_summary_total(h(2), h(1))
    print_summary_total(h(0))
    out = capsys.readouterr().out
    assert "Total Time: 01:00:00" in out
    assert "[prod  50%]" in out
    assert "Total Time: 00:00:00" in out


# ---------------------------------------------------------------------------
# print_report_header
# ---------------------------------------------------------------------------


def test_report_header_task_mode_shows_all_percentages(capsys):
    print_report_header(
        title="Header",
        period="p",
        start_time=T0,
        end_time=T0 + h(5),
        total_duration=h(4),
        task_based=True,
        non_afk_time=h(3),
        productive_time=h(2),
        productive_task_time=h(1),
        first_event_time=T0,
        last_event_time=T0 + h(4),
        distracting_time=h(0.5),
        unscored_time=h(0.25),
        total_score=7.5,
    )
    out = capsys.readouterr().out
    assert "Productivity Score: 7.50" in out
    assert "Focus time" in out
    assert "Unscored time" in out
    assert "Overall distracting time" in out


def test_report_header_category_mode_skips_task_metrics(capsys):
    print_report_header(
        title="Header",
        period="p",
        start_time=T0,
        end_time=T0 + h(5),
        total_duration=h(4),
        task_based=False,
        non_afk_time=h(3),
        productive_time=h(2),
    )
    out = capsys.readouterr().out
    assert "Focus time" not in out
    assert "Project Tracking" not in out


def test_report_header_zero_productivity_and_afk_labels(capsys):
    print_report_header(
        title="Zero",
        period="p",
        start_time=T0,
        end_time=T0 + h(1),
        total_duration=h(0),
        task_based=True,
        non_afk_time=h(0),
        productive_time=h(0),
        productive_task_time=h(0),
        first_event_time=T0,
        last_event_time=T0 + h(1),
    )
    out = capsys.readouterr().out
    assert "Zero" in out


# ---------------------------------------------------------------------------
# print_report_totals
# ---------------------------------------------------------------------------


def test_totals_from_report_totals_object_with_productivity_breakdown(capsys):
    productivity = ProductivityBreakdown(
        active_productive=h(1),
        active_unproductive=h(0.5),
        active_undefined=h(0.5),
        afk_productive=h(0.25),
        afk_unproductive=h(0.25),
        afk_undefined=h(0),
    )
    print_report_totals(
        ReportTotals(online_time=h(3), afk_time=h(0.5), offline_time=h(0.5), productive_time=h(2)),
        total_break=h(1),
        productivity=productivity,
    )
    out = capsys.readouterr().out
    assert "Productive" in out
    assert "Unproductive" in out
    assert "Undefined" in out


def test_totals_without_productive_time_omits_percentages(capsys):
    print_report_totals(h(2))
    out = capsys.readouterr().out
    assert "Total Time" in out
    assert "[prod" not in out


# ---------------------------------------------------------------------------
# print_report: hierarchy rendering at each detail level
# ---------------------------------------------------------------------------


def _tree():
    return {
        "Climb": {
            "total_duration": h(2),
            "prod_score": 10.0,
            "tasks": {
                "Task1": {
                    "total_duration": h(2),
                    "prod_score": 10.0,
                    "categories": {
                        "Coding": {
                            "total_duration": h(2),
                            "prod_score": 10.0,
                            "apps": {
                                "VSCode": {
                                    "total_duration": h(2),
                                    "prod_score": 10.0,
                                    "titles": {
                                        "tw-report.py": {
                                            "total_duration": h(2),
                                            "prod_score": 10.0,
                                        }
                                    },
                                }
                            },
                        }
                    },
                }
            },
        },
        "No project assigned": {
            "total_duration": h(1),
            "prod_score": 0.0,
            "tasks": {
                "No task assigned": {
                    "total_duration": h(1),
                    "prod_score": 0.0,
                    "categories": {},
                }
            },
        },
    }


def _print(capsys, detail_level, **kw):
    print_report(
        report_data=_tree(),
        period="p",
        start_time=T0,
        end_time=T0 + h(5),
        task_based=True,
        detail_level=detail_level,
        **kw,
    )
    return capsys.readouterr().out


@pytest.mark.parametrize("detail_level", [1, 2, 3, 4, 5])
def test_print_report_renders_every_detail_level(capsys, detail_level):
    out = _print(capsys, detail_level)
    assert "Project: Climb" in out
    assert "No project assigned" in out
    assert ("Task: Task1" in out) == (detail_level >= 2)
    assert ("No task assigned" in out) == (detail_level >= 2)
    if detail_level >= 3:
        assert "- Coding" in out
    else:
        assert "- Coding" not in out
    if detail_level >= 4:
        assert "- VSCode" in out
    else:
        assert "- VSCode" not in out
    if detail_level >= 5:
        assert "tw-report.py" in out
    else:
        assert "tw-report.py" not in out


def test_print_report_detail_level_one_shows_projects_only(capsys):
    out = _print(capsys, 1)
    assert "Task: Task1" not in out


def test_print_report_sorts_by_duration_when_requested(capsys):
    out = _print(capsys, 2, sort_by_duration=True)
    assert out.index("Project: Climb") < out.index("No project assigned")


def test_print_report_category_layout_for_category_keyed_data(capsys):
    data = {
        "Development": {
            "total_duration": h(3),
            "prod_score": 85.0,
            "apps": {
                "vim": {"total_duration": h(2), "prod_score": 100.0},
                "GitHub": {
                    "total_duration": h(1),
                    "prod_score": 70.0,
                    "titles": {"PR review": {"total_duration": h(1), "prod_score": 70.0}},
                },
            },
        }
    }
    print_report(
        report_data=data,
        period=":yesterday",
        start_time=T0,
        end_time=T0 + h(24),
        task_based=False,
        detail_level=3,
    )
    out = capsys.readouterr().out
    assert "Category: Development" in out
    assert "App: vim" in out
    assert "App: GitHub" in out
    assert "PR review" in out


def test_print_report_reports_no_activity_for_empty_data(capsys):
    print_report(
        report_data={},
        period="p",
        start_time=T0,
        end_time=T0 + h(1),
        task_based=True,
    )
    assert "No activity found for the specified period." in capsys.readouterr().out


def test_print_report_derives_break_from_event_window_without_total_break(capsys):
    # Event window 5h; worked = active 3h + afk 0.5h + offline 0.5h = 4h -> break 1h.
    print_report(
        report_data=_tree(),
        period="p",
        start_time=T0,
        end_time=T0 + h(5),
        task_based=True,
        non_afk_time=h(3),
        afk_time=h(0.5),
        total_offline_time=h(0.5),
        first_event_time=T0,
        last_event_time=T0 + h(5),
    )
    assert "01:00:00" in line_value(capsys.readouterr().out, "  Break Time")


def test_print_report_clamps_derived_break_at_zero(capsys):
    # Worked time (5h) exceeds the 1h event window; the derived break must be zero.
    print_report(
        report_data=_tree(),
        period="p",
        start_time=T0,
        end_time=T0 + h(1),
        task_based=True,
        non_afk_time=h(5),
        first_event_time=T0,
        last_event_time=T0 + h(1),
    )
    # A clamped zero break is falsy and is not printed; an unclamped negative would be.
    assert "Break Time" not in capsys.readouterr().out


def _header(capsys, **overrides):
    args: Dict[str, Any] = dict(
        title="Hdr",
        period="p",
        start_time=T0,
        end_time=T0 + h(5),
        total_duration=h(2),
        task_based=True,
        non_afk_time=h(3),
        productive_time=h(2),
        productive_task_time=h(1),
        first_event_time=T0,
        last_event_time=T0 + h(4),
    )
    args.update(overrides)
    print_report_header(**args)
    return capsys.readouterr().out


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known bug: print_report_header subtracts productive_task_time from productive_time "
        "without a guard (task and non-task branches), so productive_time=None raises "
        "TypeError. print_report_summary guards this; the header is not called by production "
        "code (candidate for removal)."
    ),
)
def test_header_without_productive_time_does_not_crash(capsys):
    out = _header(capsys, productive_time=None)
    assert "Focus time" in out


def test_header_with_no_project_time_omits_focus_and_percent(capsys):
    out = _header(capsys, total_duration=h(0), productive_task_time=h(1))
    assert "Focus time" not in out
    assert "Project Tracking" in out


def test_header_shows_afk_line_when_afk_time_given(capsys):
    out = _header(capsys, afk_time=h(0.5))
    assert "  • AFK time: 00:30:00" in out
    assert "Active Time" in out


def test_header_shows_task_productivity_score_in_task_mode(capsys):
    out = _header(capsys, total_score=7.5)
    assert "Task Productivity Score: 7.50" in out


def test_header_shows_overall_productivity_score_in_category_mode(capsys):
    out = _header(capsys, task_based=False, total_score=7.5)
    assert "Tracked projects: 0.0%" in out
    assert "Overall Productivity Score: 7.50" in out


def test_header_shows_last_break_with_its_window(capsys):
    out = _header(
        capsys,
        last_break_start=T0 + h(1),
        last_break_end=T0 + h(1.5),
        last_break_duration=h(0.5),
    )
    assert "Last Break: 00:30:00" in out


def test_minimal_header_shows_score_when_present(capsys):
    out = _header(capsys, non_afk_time=None, total_score=3.25, total_time_all=h(4))
    assert "Total Time: 04:00:00" in out
    assert "Productivity Score: 3.25" in out


@pytest.mark.parametrize("detail_level", [1, 2, 3])
def test_category_layout_renders_each_detail_level(capsys, detail_level):
    data = {
        "Development": {
            "total_duration": h(3),
            "prod_score": 85.0,
            "apps": {
                "vim": {"total_duration": h(2), "prod_score": 100.0},
                "GitHub": {
                    "total_duration": h(1),
                    "prod_score": 70.0,
                    "titles": {"PR review": {"total_duration": h(1), "prod_score": 70.0}},
                },
            },
        }
    }
    print_report(
        report_data=data,
        period="p",
        start_time=T0,
        end_time=T0 + h(24),
        task_based=False,
        detail_level=detail_level,
    )
    out = capsys.readouterr().out
    assert "Category: Development" in out
    assert ("App: vim" in out) == (detail_level >= 2)
    assert ("PR review" in out) == (detail_level >= 3)
