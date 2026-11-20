"""Display row merge: touching slots of the same work become one row; totals are unchanged."""

from datetime import UTC, timedelta

from tests.support.slots import BASE, slot
from tw_report.core.categories import merge_category_trees
from tw_report.core.time_breakdown import TimeBreakdown
from tw_report.pipeline.metrics import state_totals
from tw_report.pipeline.row_merge import merge_display_rows


def cat(name, minutes, app="Code", title="main.py", start=BASE, end=None):
    dur = timedelta(minutes=minutes)
    end = end or start + dur
    return {
        "category": name,
        "duration": dur,
        "start": start,
        "end": end,
        "apps": [
            {
                "app": app,
                "duration": dur,
                "start": start,
                "end": end,
                "titles": [{"title": title, "duration": dur, "events": []}],
            }
        ],
    }


def test_touching_slots_of_the_same_task_and_state_form_one_row():
    rows = merge_display_rows([slot(0, 10, "not-afk"), slot(10, 5, "not-afk")])
    assert len(rows) == 1
    assert rows[0].start == BASE
    assert rows[0].end == BASE + timedelta(minutes=15)
    assert rows[0].breakdown == TimeBreakdown(active=timedelta(minutes=15))


def test_a_single_slot_is_returned_unchanged():
    only = slot(0, 10, "not-afk")
    assert merge_display_rows([only])[0] is only


def test_a_gap_breaks_the_row():
    rows = merge_display_rows([slot(0, 10, "not-afk"), slot(12, 5, "not-afk")])
    assert len(rows) == 2


def test_another_task_or_afk_state_breaks_the_row():
    rows = merge_display_rows(
        [
            slot(0, 10, "not-afk"),
            slot(10, 5, "not-afk", task="Task2"),
            slot(15, 5, "afk", task="Task2"),
        ]
    )
    assert len(rows) == 3


def test_a_logical_day_cut_breaks_the_row():
    late = slot(14 * 60 + 50, 10, "not-afk")  # 23:50-00:00 (BASE is 09:00 UTC)
    after_midnight = slot(15 * 60, 5, "not-afk")  # 00:00-00:05 on 2026-11-03
    rows = merge_display_rows([late, after_midnight], day_start_hour=0, tz=UTC)
    assert len(rows) == 2


def test_merged_row_categories_are_summed_by_name_and_app():
    first = slot(0, 10, "not-afk", categories=[cat("Dev", 10)])
    second = slot(10, 5, "not-afk", categories=[cat("Dev", 5)])
    row = merge_display_rows([first, second])[0]
    assert len(row.categories) == 1
    assert row.categories[0]["duration"] == timedelta(minutes=15)
    assert row.categories[0]["apps"][0]["duration"] == timedelta(minutes=15)


def test_state_totals_do_not_change_when_rows_are_merged():
    slots = [slot(0, 10, "not-afk"), slot(10, 5, "afk"), slot(15, 5, "not-afk"), slot(30, 4)]
    assert state_totals(merge_display_rows(slots)) == state_totals(slots)


def test_merge_category_trees_widens_ranges_and_sorts_by_category():
    later = BASE + timedelta(minutes=30)
    merged = merge_category_trees(
        [[cat("Zeta", 5, start=later)], [cat("Alpha", 5), cat("Zeta", 5, start=BASE)]]
    )
    assert [c["category"] for c in merged] == ["Alpha", "Zeta"]
    zeta = merged[1]
    assert zeta["duration"] == timedelta(minutes=10)
    assert zeta["start"] == BASE
    assert zeta["end"] == later + timedelta(minutes=5)


def test_merge_category_trees_with_no_input_is_empty():
    assert merge_category_trees([]) == []


def test_merged_row_keeps_every_tagged_data_value_in_order():
    from tw_report.core.event_slot import DataValue

    first = slot(0, 10, "not-afk")
    second = slot(10, 5, "not-afk")
    first.data = {"app": [DataValue("window", "Code", BASE, BASE + timedelta(minutes=10))]}
    second.data = {
        "app": [DataValue("tmux", "tmux", second.start, second.end)],
        "status": [DataValue("afk", "not-afk", second.start, second.end)],
    }
    row = merge_display_rows([first, second])[0]
    assert [v.value for v in row.data["app"]] == ["Code", "tmux"]
    assert [v.bucket for v in row.data["app"]] == ["window", "tmux"]
    assert len(row.data["status"]) == 1
