"""Coverage tests for pipeline.processors: filtering, metric scoring and AFK merging."""

from datetime import datetime, timedelta, timezone

from aw_core.models import Event

from aw_report.core.filtering import EventFilter
from aw_report.core.timeline import TimelineSlot
from aw_report.core.report_slot import ReportTimelineSlot
from aw_report.pipeline.processors import (
    _split_by_window_categories,
    aggregate_hierarchy_from_slots,
    compute_metrics,
    merge_overlapping_afk_periods,
)
from aw_report.utils.formatting import normalize_title

UTC = timezone.utc
T0 = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def slot(
    h0, h1, project="P", task="T", categories=None, with_task_event=True
) -> ReportTimelineSlot:
    """A not-AFK slot of the given hours, built on the state model (no legacy converter)."""
    from aw_report.core.aw_events import TaskWarriorEvent

    d = timedelta(hours=h1 - h0)
    task_event = (
        TaskWarriorEvent(timestamp=at(h0), duration=d, data={"project": project, "title": task})
        if with_task_event
        else None
    )
    return ReportTimelineSlot(
        start=at(h0),
        end=at(h1),
        duration=d,
        actual_duration=d,
        productive_duration=d,
        task_event=task_event,
        categories=categories or [],
        afk_state="not-afk",
    )


def score_by_name(scores):
    return lambda category, _cat_score_map: scores.get(category, 0.0)


def _metrics(slots, scores, detail_level=3):
    return compute_metrics(
        consolidated_slots=slots,
        cat_score_map={},
        get_category_score=score_by_name(scores),
        non_afk_time=timedelta(0),
        first_event_time=None,
        last_event_time=None,
        current_session_start=None,
        current_session_end=None,
        current_session_duration=None,
        last_break_start=None,
        last_break_end=None,
        last_break_duration=None,
        detail_level=detail_level,
    )


# ---------------------------------------------------------------------------
# aggregate_hierarchy_from_slots
# ---------------------------------------------------------------------------


def _aggregate(slots, task_based=True, event_filter=None):
    return aggregate_hierarchy_from_slots(
        consolidated_slots=slots,
        task_based=task_based,
        cat_score_map={},
        get_category_score=score_by_name({}),
        normalize_title=normalize_title,
        event_filter=event_filter,
    )


def test_aggregate_applies_event_filter_before_grouping():
    slots = [slot(0, 1, project="P"), slot(1, 2, project="Q")]
    report, task_based = _aggregate(slots, event_filter=EventFilter(project_patterns=["P"]))
    assert task_based is True
    assert set(report) == {"P"}


def test_aggregate_without_filter_keeps_every_project():
    report, _ = _aggregate([slot(0, 1, project="P"), slot(1, 2, project="Q")])
    assert set(report) == {"P", "Q"}


def test_aggregate_reports_task_based_for_non_empty_input_even_when_not_requested():
    report, task_based = _aggregate([slot(0, 1)], task_based=False)
    assert task_based is True
    assert set(report) == {"P"}


def test_aggregate_empty_input_returns_requested_mode():
    assert _aggregate([], task_based=False) == ({}, False)
    assert _aggregate([], task_based=True) == ({}, True)


def test_aggregate_title_and_category_totals_accumulate_per_project_task():
    categories = [
        {
            "category": "Coding",
            "apps": [
                {
                    "app": "VSCode",
                    "duration": timedelta(hours=1),
                    "titles": [{"title": "tw-report.py", "duration": timedelta(hours=1)}],
                }
            ],
        }
    ]
    report, _ = aggregate_hierarchy_from_slots(
        consolidated_slots=[slot(0, 1, categories=categories)],
        task_based=True,
        cat_score_map={},
        get_category_score=score_by_name({"Coding": 10.0}),
        normalize_title=normalize_title,
    )
    project = report["P"]
    assert project["total_duration"] == timedelta(hours=1)
    task = project["tasks"]["T"]
    assert task["total_duration"] == timedelta(hours=1)


# ---------------------------------------------------------------------------
# compute_metrics: productivity classification of scored categories
# ---------------------------------------------------------------------------


def test_compute_metrics_classifies_by_category_score_sign():
    productive_cat = [{"category": "Coding", "apps": []}]
    distracting_cat = [{"category": "Social", "apps": []}]
    neutral_cat = [{"category": "Misc", "apps": []}]
    slots = [
        slot(0, 1, categories=productive_cat),  # score > 0, with task
        slot(1, 2, categories=productive_cat, with_task_event=False),  # score > 0, no task
        slot(2, 3, categories=distracting_cat),  # score < 0
        slot(3, 4, categories=neutral_cat),  # score == 0
    ]
    metrics = _metrics(slots, {"Coding": 10.0, "Social": -5.0, "Misc": 0.0})
    assert metrics.productive_time == timedelta(hours=2)
    assert metrics.productive_task_time == timedelta(hours=1)
    assert metrics.distracting_time == timedelta(hours=1)
    assert metrics.unscored_time == timedelta(hours=1)


def test_compute_metrics_scores_nothing_below_detail_level_three():
    # Categories are not shown below level 3, so their productivity is not computed.
    cats = [{"category": "Coding", "apps": []}]
    metrics = _metrics([slot(0, 1, categories=cats)], {"Coding": 10.0}, detail_level=2)
    assert metrics.productive_time == timedelta(0)


def test_compute_metrics_ignores_slots_without_categories():
    metrics = _metrics([slot(0, 1, categories=[])], {})
    assert metrics.productive_time == timedelta(0)
    assert metrics.unscored_time == timedelta(0)


# ---------------------------------------------------------------------------
# merge_overlapping_afk_periods
# ---------------------------------------------------------------------------


def _afk_event(start_offset_s, duration_s, status):
    return Event(
        timestamp=at(0) + timedelta(seconds=start_offset_s),
        duration=timedelta(seconds=duration_s),
        data={"status": status},
    )


def test_merge_afk_returns_short_inputs_unchanged():
    assert merge_overlapping_afk_periods([]) == []
    single = [_afk_event(0, 60, "not-afk")]
    assert merge_overlapping_afk_periods(single) is single


def test_merge_afk_joins_overlapping_same_status_periods():
    merged = merge_overlapping_afk_periods(
        [
            _afk_event(0, 3600, "not-afk"),
            _afk_event(1800, 3600, "not-afk"),
        ]
    )
    assert len(merged) == 1
    assert merged[0].duration == timedelta(seconds=5400)


def test_merge_afk_joins_micro_gap_under_two_seconds():
    merged = merge_overlapping_afk_periods(
        [
            _afk_event(0, 60, "not-afk"),
            _afk_event(61, 60, "not-afk"),  # 1 second gap
        ]
    )
    assert len(merged) == 1
    assert merged[0].duration == timedelta(seconds=121)


def test_merge_afk_keeps_status_changes_and_real_gaps_separate():
    merged = merge_overlapping_afk_periods(
        [
            _afk_event(0, 60, "not-afk"),
            _afk_event(60, 60, "afk"),  # status change, no gap
            _afk_event(300, 60, "not-afk"),  # same status as first but a real gap
        ]
    )
    assert [e.data["status"] for e in merged] == ["not-afk", "afk", "not-afk"]


# ---------------------------------------------------------------------------
# Window-category split edge case
# ---------------------------------------------------------------------------


def test_split_by_window_categories_zero_span_is_undefined():
    shares = _split_by_window_categories(
        start=at(0),
        end=at(0),
        duration=timedelta(0),
        window_events=[],
        cat_score_map={},
    )
    assert sum(shares.values(), timedelta(0)) == timedelta(0)
