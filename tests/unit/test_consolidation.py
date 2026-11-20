"""Session consolidation (--consolidate) on slot objects: runs of same-task slots become one slot."""

from datetime import timedelta

from tests.support.slots import BASE, slot
from aw_report.core.event_slot import DataValue
from aw_report.core.time_breakdown import TimeBreakdown
from aw_report.pipeline.consolidation import consolidate_sessions


def test_empty_input_gives_nothing():
    assert consolidate_sessions([]) == []


def test_a_single_slot_is_returned_unchanged():
    only = slot(0, 60, "not-afk")
    assert consolidate_sessions([only]) == [only]


def test_consecutive_same_task_slots_merge_into_one_span():
    merged = consolidate_sessions([slot(0, 60, "not-afk"), slot(60, 30, "not-afk")])
    assert len(merged) == 1
    assert merged[0].start == BASE
    assert merged[0].end == BASE + timedelta(minutes=90)
    assert merged[0].is_consolidated
    assert merged[0].afk_state is None


def test_the_span_bridges_an_afk_gap_between_same_task_slots():
    merged = consolidate_sessions(
        [slot(0, 60, "not-afk"), slot(60, 15, "afk"), slot(75, 30, "not-afk")]
    )
    assert len(merged) == 1
    assert merged[0].duration == timedelta(minutes=105)
    assert merged[0].breakdown == TimeBreakdown(
        active=timedelta(minutes=90), afk=timedelta(minutes=15)
    )


def test_a_different_task_breaks_the_run():
    merged = consolidate_sessions(
        [slot(0, 60, "not-afk"), slot(60, 30, "not-afk", task="Other"), slot(90, 10, "not-afk")]
    )
    assert [(m.task, m.duration) for m in merged] == [
        ("Task1", timedelta(minutes=60)),
        ("Other", timedelta(minutes=30)),
        ("Task1", timedelta(minutes=10)),
    ]


def test_a_different_project_with_the_same_task_name_is_not_merged():
    merged = consolidate_sessions(
        [slot(0, 60, "not-afk"), slot(60, 30, "not-afk", project="Other")]
    )
    assert len(merged) == 2


def test_offline_time_stays_offline_after_merging():
    merged = consolidate_sessions([slot(0, 60, "not-afk"), slot(60, 30, "untracked", offline=True)])
    assert merged[0].breakdown == TimeBreakdown(
        active=timedelta(minutes=60), offline=timedelta(minutes=30)
    )


def test_productive_time_and_tagged_data_are_summed_or_kept():
    first = slot(0, 60, "not-afk")
    second = slot(60, 30, "not-afk")
    first.productive_duration = timedelta(minutes=40)
    second.productive_duration = timedelta(minutes=10)
    first.data = {"app": [DataValue("window", "Code", BASE, BASE + timedelta(minutes=60))]}
    second.data = {"app": [DataValue("tmux", "tmux", BASE, BASE + timedelta(minutes=30))]}
    merged = consolidate_sessions([first, second])[0]
    assert merged.productive_duration == timedelta(minutes=50)
    assert [v.value for v in merged.data["app"]] == ["Code", "tmux"]


def test_the_merged_slot_keeps_the_first_slots_categories():
    first = slot(0, 60, "not-afk", categories=[{"category": "Dev", "apps": []}])
    second = slot(60, 30, "not-afk", categories=[{"category": "Other", "apps": []}])
    merged = consolidate_sessions([first, second])[0]
    assert merged.categories == [{"category": "Dev", "apps": []}]
