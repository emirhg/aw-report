"""Coverage tests for timeline rendering: offline overlap rules, multi-day totals, widths.

Two documented behaviors are currently not applied (detail-level 1 task suppression and the
offline '*' prefix). They are pinned as strict xfails until fixed.
"""

from datetime import datetime, timedelta, timezone

import time

import pytest

from tw_report.core.report_slot import ReportTimelineSlot
from tw_report.pipeline import timeline_render as tr
from tw_report.pipeline.timeline_render import (
    _get_displayed_duration,
    compute_afk_offline_totals,
    format_and_print_day_total_displayed,
    print_timeline_report,
)

UTC = timezone.utc
D1 = datetime(2026, 7, 23, 0, 0, tzinfo=UTC)
D2 = datetime(2026, 7, 24, 0, 0, tzinfo=UTC)


def h(hours: float) -> timedelta:
    return timedelta(hours=hours)


def regular(day, start_h, end_h, project="Platform", task="Code"):
    d = h(end_h - start_h)
    return {
        "start": day + h(start_h),
        "duration": d,
        "actual_duration": d,
        "productive_duration": d,
        "type": "regular",
        "project": project,
        "task": task,
    }


def offline(day, start_h, end_h, project="Climb", task="Writing"):
    return {
        "start": day + h(start_h),
        "duration": h(end_h - start_h),
        "actual_duration": h(0.5),
        "event_duration": h(0.5),
        "type": "offline_task",
        "project": project,
        "task": task,
    }


def render(capsys, slots, **kw):
    args = dict(
        slots=slots,
        period="p",
        start_time=D1,
        end_time=D1 + h(24),
        task_based=True,
        detail_level=2,
        day_start_hour=0,
    )
    args.update(kw)
    print_timeline_report(**args)
    return capsys.readouterr().out


@pytest.fixture(autouse=True)
def utc_local_time(monkeypatch):
    """Logical dates are computed in local time; pin it so the expectations are reproducible."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


@pytest.fixture
def narrow_terminal(monkeypatch):
    monkeypatch.setattr(tr, "get_terminal_width", lambda: 60)


@pytest.fixture
def wide_terminal(monkeypatch):
    monkeypatch.setattr(tr, "get_terminal_width", lambda: 140)


# ---------------------------------------------------------------------------
# Offline overlap rules
# ---------------------------------------------------------------------------


def test_other_task_is_kept_next_to_offline_period(capsys):
    out = render(
        capsys,
        [
            offline(D1, 10, 12),
            regular(D1, 13, 14, project="Climb", task="Other"),
        ],
    )
    assert "Other" in out


def test_slot_touching_offline_boundary_is_kept(capsys):
    out = render(
        capsys,
        [
            offline(D1, 10, 12),
            regular(D1, 12, 13, project="No project assigned", task="No task assigned"),
        ],
    )
    assert "No project assigned" in out


def test_untracked_slot_not_overlapping_offline_is_kept(capsys):
    out = render(
        capsys,
        [
            offline(D1, 10, 12),
            regular(D1, 13, 14, project="No project assigned", task="No task assigned"),
        ],
    )
    assert "No project assigned" in out


# ---------------------------------------------------------------------------
# Input shapes: mixed dict/object lists and malformed dicts
# ---------------------------------------------------------------------------


def test_mixed_object_and_dict_slots_render_together(capsys):
    obj = ReportTimelineSlot.from_dict(regular(D1, 9, 10, project="Obj", task="T"))
    out = render(capsys, [obj, regular(D1, 11, 12, project="Dict", task="T")])
    assert "Obj" in out
    assert "Dict" in out


def test_malformed_dict_slot_is_skipped_and_others_still_render(capsys):
    # The conversion deliberately skips slots it cannot build (see print_timeline_report).
    malformed = {"start": D1 + h(9), "type": "regular"}
    out = render(capsys, [malformed, regular(D1, 11, 12, project="Good", task="T")])
    assert "Good" in out


# ---------------------------------------------------------------------------
# Multi-day layout and week totals
# ---------------------------------------------------------------------------


def test_multi_day_output_has_day_totals_and_week_total(capsys):
    out = render(
        capsys,
        [regular(D1, 9, 11), regular(D2, 9, 10, task="Other")],
        end_time=D2 + h(24),
        non_afk_time=h(3),
        productive_time=h(3),
        productive_task_time=h(3),
        first_event_time=D1 + h(9),
        last_event_time=D2 + h(10),
    )
    assert out.count("Day total:") == 2
    assert "2026-07-24" in out
    assert "Week total (tracked):" in out


def test_single_day_has_no_week_total_line(capsys):
    # end inside the same logical day; an end at exactly the next midnight would count as two days.
    out = render(capsys, [regular(D1, 9, 10)], end_time=D1 + h(23))
    assert "Week total" not in out


# ---------------------------------------------------------------------------
# Width handling
# ---------------------------------------------------------------------------


def test_narrow_terminal_still_renders_columns(narrow_terminal, capsys):
    out = render(capsys, [regular(D1, 9, 10)], detail_level=1)
    assert "Day total:" in out
    assert "Timeline Report" in out


def test_wide_terminal_renders_timeline(wide_terminal, capsys):
    out = render(capsys, [regular(D1, 9, 10)])
    assert "Day total:" in out


def test_day_total_line_uses_displayed_values(capsys):
    format_and_print_day_total_displayed(h(1), h(0.5), h(2), h(1.5), 100, h(0.25))
    out = capsys.readouterr().out
    assert "Day total:" in out
    assert "01:00:00" in out


def test_day_total_line_with_narrow_width_appends_right_section(capsys):
    format_and_print_day_total_displayed(h(1), h(0.5), h(2), h(1.5), 20, h(0.25))
    assert "Day total:" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def test_compute_afk_offline_totals_come_from_the_breakdown():
    from tests.support.slots import slot as state_slot

    offline = state_slot(0, 60, "untracked", offline=True)
    afk_slot = state_slot(60, 30, "afk")
    active = state_slot(90, 45, "not-afk")
    afk, offline_total = compute_afk_offline_totals([offline, afk_slot, active])
    assert offline_total == h(1)
    assert afk == h(0.5)


D3 = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)  # Monday: a new ISO week after D1 (Thursday)


@pytest.fixture
def very_wide_terminal(monkeypatch):
    monkeypatch.setattr(tr, "get_terminal_width", lambda: 200)


def test_very_wide_terminal_aligns_header_and_day_totals(very_wide_terminal, capsys):
    out = render(capsys, [regular(D1, 9, 10), regular(D1, 13, 14, task="Other")])
    header = next(line for line in out.splitlines() if line.startswith("Wk  Date"))
    assert "UNTRACKED" in header and "ACTIVE" in header
    assert "Day total:" in out


def test_day_total_line_with_wide_width_pads_to_the_right_section(capsys):
    format_and_print_day_total_displayed(h(1), h(0.5), h(2), h(1.5), 200, h(0.25))
    line = next(x for x in capsys.readouterr().out.splitlines() if "Day total:" in x)
    # Online = active 2h + AFK 0.5h = 2.5h; productive 1.5h -> 60%
    assert line.rstrip().endswith("[prod  60%]")


def test_week_change_prints_closing_totals_for_the_previous_week(capsys):
    out = render(
        capsys,
        [regular(D1, 9, 11), regular(D3, 9, 10, task="Other")],
        end_time=D3 + h(23),
        non_afk_time=h(3),
        productive_time=h(3),
        productive_task_time=h(3),
        first_event_time=D1 + h(9),
        last_event_time=D3 + h(10),
    )
    assert "W30" in out and "W31" in out
    assert out.count("Day total:") == 2
    assert "Week total (tracked):" in out


def test_date_change_within_a_week_separates_day_totals(capsys):
    out = render(
        capsys,
        [regular(D1, 9, 10), regular(D2, 9, 10, task="Other")],
        end_time=D2 + h(23),
    )
    assert out.count("Day total:") == 2
    assert "2026-07-24" in out


def test_slot_outside_a_single_day_period_is_not_rendered_and_no_week_total_appears(capsys):
    # Slots are restricted to [start, end) before rendering, so a slot in another week cannot
    # appear inside a single-day period and no week-change totals are needed.
    out = render(
        capsys, [regular(D1, 9, 10), regular(D3, 9, 10, task="Other")], end_time=D1 + h(23)
    )
    assert "W31" not in out
    assert "Other" not in out
    assert "Week total" not in out


def test_very_wide_week_change_uses_full_width_separator(very_wide_terminal, capsys):
    out = render(
        capsys, [regular(D1, 9, 10), regular(D3, 9, 10, task="Other")], end_time=D3 + h(23)
    )
    assert out.count("Day total:") == 2
    assert "Week total (tracked):" in out


def test_very_wide_date_change_uses_full_width_separator(very_wide_terminal, capsys):
    out = render(
        capsys, [regular(D1, 9, 10), regular(D2, 9, 10, task="Other")], end_time=D2 + h(23)
    )
    assert out.count("Day total:") == 2


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known bug: the detail-level-1 task suppression (NO_TASK) was guarded by a dict check "
        "that can never pass after slots are converted, so --detail-level 1 still prints task "
        "names. Documented intent: level 1 shows projects only. Fix in Phase 3."
    ),
)
def test_detail_level_one_hides_task_names(capsys):
    out = render(
        capsys, [regular(D1, 9, 10, project="Platform", task="Code Review")], detail_level=1
    )
    assert "Platform" in out
    assert "Code Review" not in out


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known bug: offline-task rows were meant to show a '*' before the task name (per the "
        "removed display block's comment), but that block could never run after slot "
        "conversion. Fix in Phase 3."
    ),
)
def test_offline_task_rows_are_prefixed_with_an_asterisk(capsys):
    out = render(capsys, [offline(D1, 10, 12)])
    assert "*Writing" in out


def test_displayed_duration_is_the_wall_clock_duration_of_every_slot():
    from tests.support.slots import slot as state_slot
    from tw_report.pipeline.timeline_render import _get_displayed_duration

    for state, offline in [("afk", False), ("not-afk", False), ("untracked", True), (None, False)]:
        built = state_slot(0, 40, state, offline=offline)
        assert _get_displayed_duration(built) == timedelta(minutes=40)
