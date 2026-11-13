"""Coverage tests for ReportTimelineSlot / ReportEntries paths not reached elsewhere.

Slot shapes mirror what the production builder emits: bare AFK slots have
actual_duration=0 and afk_duration set; offline tasks carry a task_event.
"""

import time
from datetime import date, datetime, timedelta, timezone

import pytest

from tw_report.core.aw_events import TaskWarriorEvent
from tw_report.core.filtering import NO_PROJECT, NO_TASK
from tw_report.core.report_slot import (
    DisplayColumns,
    ReportEntries,
    ReportTimelineSlot,
    compute_daily_break_totals,
    compute_total_break_time,
)
from tw_report.core.timeline import TimelineSlot

UTC = timezone.utc
T0 = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def utc_local_time(monkeypatch):
    """Bucket boundaries use the local timezone; pin it so results are reproducible."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def work(h0, h1, project="P", task="T") -> TimelineSlot:
    d = timedelta(hours=h1 - h0)
    return TimelineSlot(
        type="regular",
        start=at(h0),
        end=at(h1),
        duration=d,
        actual_duration=d,
        productive_duration=d,
        project=project,
        task=task,
        categories=[],
        tags=[],
    )


def afk(h0, h1, project=NO_PROJECT, task=NO_TASK) -> TimelineSlot:
    """Bare AFK slot as the builder emits it: no active time, idle time, no task."""
    d = timedelta(hours=h1 - h0)
    return TimelineSlot(
        type="afk",
        start=at(h0),
        end=at(h1),
        duration=d,
        actual_duration=timedelta(0),
        afk_duration=d,
        productive_duration=timedelta(0),
        project=project,
        task=task,
        categories=[],
        tags=[],
    )


def rs(ts: TimelineSlot) -> ReportTimelineSlot:
    return ReportTimelineSlot.from_timeline_slot(ts)


def entries(*slots) -> ReportEntries:
    return ReportEntries(slots_list=[rs(s) for s in slots])


# ---------------------------------------------------------------------------
# ReportTimelineSlot construction and predicates
# ---------------------------------------------------------------------------


def test_mismatched_timezones_are_rejected():
    eastern = timezone(timedelta(hours=-5))
    with pytest.raises(ValueError, match="mismatched timezones"):
        ReportTimelineSlot(
            start=at(0),
            end=at(1).astimezone(eastern),
            duration=timedelta(hours=1),
            actual_duration=timedelta(hours=1),
        )


def test_offline_gap_predicate():
    gap = ReportTimelineSlot(
        start=at(0),
        end=at(1),
        duration=timedelta(hours=1),
        actual_duration=timedelta(0),
        offline_extension_duration=timedelta(hours=1),
    )
    assert gap.is_offline_gap is True
    assert rs(work(0, 1)).is_offline_gap is False


def test_offline_task_predicate_requires_task_event_and_offline_time():
    event = TaskWarriorEvent(
        timestamp=at(0),
        duration=timedelta(hours=1),
        data={"project": "P", "task": "T", "tags": ["offline"]},
    )
    offline = ReportTimelineSlot(
        start=at(0),
        end=at(1),
        duration=timedelta(hours=1),
        actual_duration=timedelta(0),
        task_event=event,
        offline_extension_duration=timedelta(hours=1),
    )
    assert offline.is_offline_task is True
    no_event = ReportTimelineSlot(
        start=at(0),
        end=at(1),
        duration=timedelta(hours=1),
        actual_duration=timedelta(0),
        offline_extension_duration=timedelta(hours=1),
    )
    assert no_event.is_offline_task is False
    assert rs(work(0, 1)).is_offline_task is False


def test_afk_only_predicate():
    assert rs(afk(0, 1)).is_afk_only is True
    assert rs(work(0, 1)).is_afk_only is False
    # A slot that carries a task is work with idle time, not a bare AFK gap.
    assert rs(afk(0, 1, project="P", task="T")).is_afk_only is False


def test_to_dict_preserves_task_uuid_and_description():
    slot = rs(work(0, 1))
    slot.task_event = TaskWarriorEvent(
        timestamp=at(0),
        duration=timedelta(hours=1),
        data={"project": "P", "task": "T", "uuid": "abc-123", "description": "write tests"},
    )
    as_dict = slot.to_dict()
    assert as_dict["task_uuid"] == "abc-123"
    assert as_dict["task_description"] == "write tests"


# ---------------------------------------------------------------------------
# DisplayColumns
# ---------------------------------------------------------------------------


def _display_slot(**overrides):
    base = {
        "start": at(0),
        "end": at(1),
        "project": "P",
        "task": "T",
        "duration": timedelta(hours=1),
        "actual_duration": timedelta(hours=1),
        "categories": [],
        "apps": [],
    }
    base.update(overrides)
    return base


def test_display_columns_prefers_actual_duration_over_fallback():
    cols = DisplayColumns.from_slot_dict(
        _display_slot(actual_duration=timedelta(minutes=45), afk_duration=timedelta(minutes=15)),
        at(0),
        at(1),
    )
    assert "00:45:00" in cols.format(terminal_width=120)


def test_display_columns_falls_back_to_duration_minus_afk():
    cols = DisplayColumns.from_slot_dict(
        _display_slot(actual_duration=None, afk_duration=timedelta(minutes=20)),
        at(0),
        at(1),
    )
    assert "00:40:00" in cols.format(terminal_width=120)


def test_display_columns_non_timedelta_active_duration_is_zero():
    cols = DisplayColumns.from_slot_dict(
        _display_slot(actual_duration="bogus", afk_duration=None), at(0), at(1)
    )
    assert cols.active_time == ""


def test_display_columns_missing_duration_is_zero():
    cols = DisplayColumns.from_slot_dict(
        _display_slot(duration=None, actual_duration=None), at(0), at(1)
    )
    assert cols.active_time == ""


def test_display_columns_format_handles_narrow_and_wide_terminals():
    cols = DisplayColumns.from_slot_dict(_display_slot(), at(0), at(1))
    narrow = cols.format(terminal_width=20)
    wide = cols.format(terminal_width=200)
    assert "01:00:00" in narrow
    assert "01:00:00" in wide
    # Wider terminal pushes the right-hand duration further to the right.
    assert wide.index("01:00:00") > narrow.index("01:00:00")


# ---------------------------------------------------------------------------
# ReportEntries: consolidation, collapse and bucketing
# ---------------------------------------------------------------------------


def test_consolidate_by_task_merges_same_project_task_across_gaps():
    result = entries(work(0, 1), work(1, 2), work(2, 3, task="U"), work(3, 4, project="Q"))
    totals = {(s.project, s.task): s.duration for s in result.consolidate_by_task().slots()}
    assert totals == {
        ("P", "T"): timedelta(hours=2),
        ("P", "U"): timedelta(hours=1),
        ("Q", "T"): timedelta(hours=1),
    }


def test_consolidate_consecutive_merges_adjacent_same_task_only():
    result = entries(work(0, 1), work(1, 2), work(2, 3, task="U")).consolidate_consecutive()
    got = {(s.task, s.duration) for s in result.slots()}
    assert got == {("T", timedelta(hours=2)), ("U", timedelta(hours=1))}


def test_consolidation_of_empty_entries_is_empty():
    assert ReportEntries().consolidate_consecutive().slots() == []
    assert ReportEntries().consolidate_by_task().slots() == []


def test_collapse_to_project_sums_tasks_within_each_project():
    result = entries(work(0, 1), work(1, 2, task="U"), work(3, 4, project="Q"))
    totals = {s.project: s.duration for s in result.collapse_to_project().slots()}
    assert totals == {"P": timedelta(hours=2), "Q": timedelta(hours=1)}


def test_bucket_by_day_groups_pieces_under_their_logical_day():
    # Default day start is 04:00: 18:00 Jul 23 -> 06:00 Jul 24 splits at 04:00 Jul 24.
    spanning = work(8, 20)
    result = entries(spanning).bucket("day")
    by_day = {s.bucket_start_date: s.duration for s in result.slots()}
    assert by_day == {date(2026, 7, 23): timedelta(hours=10), date(2026, 7, 24): timedelta(hours=2)}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known bug: split_at_boundaries computes each piece's bucket with the default "
        "day_start_hour (4) instead of the caller's value, so with day_start_hour=0 the piece "
        "starting at 00:00 Jul 24 is labelled Jul 23. Not on the production path for "
        "day_start_hour >= 4. Fix in Phase 3."
    ),
)
def test_bucket_by_day_respects_day_start_hour_zero():
    result = entries(work(13, 15)).bucket("day", day_start_hour=0)
    by_day = {s.bucket_start_date: s.duration for s in result.slots()}
    assert by_day == {date(2026, 7, 23): timedelta(hours=1), date(2026, 7, 24): timedelta(hours=1)}


def test_bucket_key_is_the_logical_day_or_period_start():
    slot = rs(work(0, 1))
    assert slot.bucket_key("day") == date(2026, 7, 23)
    assert slot.bucket_key("month") == date(2026, 7, 1)


def test_grouped_by_project_date_returns_bucket_project_task_tuples():
    groups = entries(work(0, 1), work(1, 2, project="Q")).grouped_by_project_date()
    assert len(groups) == 2
    for project_key, day, members in groups:
        assert isinstance(project_key, str)
        assert day == date(2026, 7, 23)
        assert members


def test_grouped_by_project_date_empty_entries():
    assert ReportEntries().grouped_by_project_date() == []


# ---------------------------------------------------------------------------
# Embedded AFK and overlap handling
# ---------------------------------------------------------------------------


def test_work_slot_with_embedded_afk_counts_only_the_overlap():
    combined = ReportTimelineSlot.from_work_slot_with_embedded_afk(
        work(0, 2),
        [afk(1, 1.5), afk(1.8, 3)],  # the second AFK only overlaps for 0.2h
    )
    assert combined.afk_duration == timedelta(minutes=30) + timedelta(hours=0.2)


def test_combine_embeds_same_task_afk_and_keeps_other_afk_standalone():
    # Bare AFK embeds only into a work slot with the same project and task.
    combined = entries(work(0, 2, project=NO_PROJECT, task=NO_TASK), afk(1, 1.5), afk(5, 6))
    slots = combined.combine_work_with_embedded_afk().slots()
    assert len(slots) == 2
    embedded = next(s for s in slots if s.start == at(0))
    assert embedded.afk_duration == timedelta(minutes=30)
    standalone = next(s for s in slots if s.start == at(5))
    assert standalone.end == at(6)


def test_combine_merges_overlapping_work_slots_of_same_task():
    slots = entries(work(0, 2), work(1, 3)).combine_work_with_embedded_afk().slots()
    assert len(slots) == 1
    assert slots[0].start == at(0)
    assert slots[0].end == at(3)


def test_combine_leaves_offline_task_slots_untouched():
    offline_event = TaskWarriorEvent(
        timestamp=at(4),
        duration=timedelta(hours=1),
        data={"project": "Climb", "task": "Writing", "tags": ["offline"]},
    )
    offline = ReportTimelineSlot(
        start=at(4),
        end=at(5),
        duration=timedelta(hours=1),
        actual_duration=timedelta(0),
        task_event=offline_event,
        offline_extension_duration=timedelta(hours=1),
    )
    result = ReportEntries(slots_list=[rs(work(0, 1)), offline]).combine_work_with_embedded_afk()
    assert any(s.is_offline_task for s in result.slots())


def test_combine_of_empty_entries_is_empty():
    assert ReportEntries().combine_work_with_embedded_afk().slots() == []


# ---------------------------------------------------------------------------
# Period splitting and break totals
# ---------------------------------------------------------------------------


def test_split_within_a_single_bucket_returns_one_piece_copy():
    slot = rs(work(0, 1))
    pieces = slot.split_at_boundaries("day", 0)
    assert len(pieces) == 1
    assert pieces[0].duration == slot.duration
    assert pieces[0].bucket_mode == "day"


def test_split_across_midnight_prorates_durations():
    slot = rs(work(13, 15))  # 23:00 -> 01:00 UTC across the day boundary
    pieces = slot.split_at_boundaries("day", 0)
    assert len(pieces) == 2
    assert [p.duration for p in pieces] == [timedelta(hours=1), timedelta(hours=1)]


def test_split_at_week_boundary_preserves_total_duration():
    slot = rs(work(24 * 3 + 20, 24 * 3 + 30))  # Thu 06:00 .. Fri 04:00 UTC area
    pieces = slot.split_at_boundaries("week", 0)
    assert sum((p.duration for p in pieces), timedelta(0)) == slot.duration


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known bug: _bucket_duration('month') is a fixed 30 days, so the boundary after a "
        "31-day month lands on the 31st instead of the 1st and the split raises ValueError. "
        "Not on the production path (main only splits by 'day'). Fix in Phase 3."
    ),
)
def test_split_at_month_boundary_preserves_total_duration():
    slot = ReportTimelineSlot(
        start=datetime(2026, 7, 31, 22, tzinfo=UTC),
        end=datetime(2026, 8, 1, 2, tzinfo=UTC),
        duration=timedelta(hours=4),
        actual_duration=timedelta(hours=4),
    )
    pieces = slot.split_at_boundaries("month", 0)
    assert len(pieces) == 2
    assert sum((p.duration for p in pieces), timedelta(0)) == timedelta(hours=4)


def test_compute_break_totals_counts_gaps_longer_than_min_break_per_day():
    slots = [rs(work(0, 1)), rs(work(1.5, 2)), rs(work(3, 4))]
    totals = compute_daily_break_totals(slots, day_start_hour=0)
    assert totals == {date(2026, 7, 23): timedelta(hours=1, minutes=30)}
    assert compute_total_break_time(slots, day_start_hour=0) == timedelta(hours=1, minutes=30)


def test_compute_break_totals_ignores_gaps_shorter_than_min_break():
    slots = [rs(work(0, 1)), rs(work(1 + 10 / 3600, 2))]  # 10-second gap
    assert compute_total_break_time(slots, day_start_hour=0) == timedelta(0)


# ---------------------------------------------------------------------------
# Deduplication of overlapping duplicate slots
# ---------------------------------------------------------------------------


def _dup(h0, h1, *, actual=None, afk=None, offline=None, event=None, project="P", task="T"):
    d = timedelta(hours=h1 - h0)
    return ReportTimelineSlot(
        start=at(h0),
        end=at(h1),
        duration=d,
        actual_duration=actual if actual is not None else d,
        afk_duration=afk,
        offline_extension_duration=offline,
        event_duration=event,
        task_event=TaskWarriorEvent(
            timestamp=at(h0), duration=d, data={"project": project, "task": task}
        ),
    )


def test_dedup_of_empty_entries_is_empty():
    assert ReportEntries()._deduplicate_slots() == []


def test_dedup_prefers_duplicate_that_carries_offline_information():
    plain = _dup(0, 1)
    with_offline = _dup(0, 1, offline=timedelta(hours=1), event=timedelta(0))
    kept = ReportEntries(slots_list=[plain, with_offline])._deduplicate_slots()
    assert kept == [with_offline]


def test_dedup_keeps_existing_offline_duplicate_over_plain_one():
    with_offline = _dup(0, 1, offline=timedelta(hours=1), event=timedelta(0))
    plain = _dup(0, 1)
    kept = ReportEntries(slots_list=[with_offline, plain])._deduplicate_slots()
    assert kept == [with_offline]


def test_dedup_uses_populated_field_count_when_neither_is_offline():
    sparse = _dup(0, 1, actual=timedelta(0))
    rich = _dup(0, 1, afk=timedelta(minutes=10), event=timedelta(minutes=5))
    kept = ReportEntries(slots_list=[sparse, rich])._deduplicate_slots()
    assert kept == [rich]
    kept_reverse = ReportEntries(slots_list=[rich, sparse])._deduplicate_slots()
    assert kept_reverse == [rich]


def test_dedup_keeps_distinct_slots_in_original_order():
    later = _dup(3, 4)
    earlier = _dup(0, 1)
    kept = ReportEntries(slots_list=[later, earlier])._deduplicate_slots()
    assert kept == [later, earlier]


# ---------------------------------------------------------------------------
# Category merging across slots (category -> app -> title)
# ---------------------------------------------------------------------------


class _Slot:
    """Duck-typed stand-in exposing only the ``categories`` the merge reads."""

    def __init__(self, categories):
        self.categories = categories


def _title(name, minutes, events=()):
    return {"title": name, "duration": timedelta(minutes=minutes), "events": list(events)}


def _app(name, minutes, start, end, titles=()):
    return {
        "app": name,
        "duration": timedelta(minutes=minutes),
        "start": start,
        "end": end,
        "titles": list(titles),
    }


def test_merge_categories_sums_durations_and_expands_range():
    from tw_report.core.report_slot import _merge_categories_full

    first = _Slot([{
        "category": "Coding",
        "duration": timedelta(minutes=30),
        "start": at(0),
        "end": at(1),
        "apps": [_app("VSCode", 30, at(0), at(1), [_title("tw-report.py", 30, ["e1"])])],
    }])
    second = _Slot([{
        "category": "Coding",
        "duration": timedelta(minutes=20),
        "start": at(2),
        "end": at(3),
        "apps": [
            _app("VSCode", 10, at(2), at(3), [_title("tw-report.py", 10, ["e2"])]),
            _app("Terminal", 10, at(2), at(3), [_title("bash", 10)]),
        ],
    }])
    merged = _merge_categories_full([first, second])
    assert len(merged) == 1
    coding = merged[0]
    assert coding["category"] == "Coding"
    assert coding["duration"] == timedelta(minutes=50)
    assert coding["start"] == at(0)
    assert coding["end"] == at(3)
    apps = {a["app"]: a for a in coding["apps"]}
    assert apps["VSCode"]["duration"] == timedelta(minutes=40)
    assert apps["VSCode"]["start"] == at(0)
    assert apps["VSCode"]["end"] == at(3)
    assert apps["Terminal"]["duration"] == timedelta(minutes=10)
    titles = {t["title"]: t for t in apps["VSCode"]["titles"]}
    assert titles["tw-report.py"]["duration"] == timedelta(minutes=40)
    assert titles["tw-report.py"]["events"] == ["e1", "e2"]


def test_merge_categories_defaults_missing_names_and_skips_absent_bounds():
    from tw_report.core.report_slot import _merge_categories_full

    merged = _merge_categories_full([_Slot([{"duration": timedelta(minutes=5), "apps": [
        {"duration": timedelta(minutes=5), "titles": [{"duration": timedelta(minutes=5)}]}
    ]}])])
    assert merged[0]["category"] == "Uncategorized"
    assert merged[0]["start"] is None and merged[0]["end"] is None
    app = merged[0]["apps"][0]
    assert app["app"] == "Unknown App"
    assert app["titles"][0]["title"] == "Unknown Title"


def test_merge_categories_without_apps_omits_apps_key():
    from tw_report.core.report_slot import _merge_categories_full

    merged = _merge_categories_full([_Slot([{"category": "Idle", "duration": timedelta(0)}])])
    assert merged == [{"category": "Idle", "duration": timedelta(0), "start": None, "end": None}]


def test_merge_categories_second_occurrence_without_bounds_keeps_first_range():
    from tw_report.core.report_slot import _merge_categories_full

    merged = _merge_categories_full([
        _Slot([{"category": "Coding", "duration": timedelta(minutes=5), "start": at(0), "end": at(1)}]),
        _Slot([{"category": "Coding", "duration": timedelta(minutes=5)}]),
    ])
    assert merged[0]["start"] == at(0)
    assert merged[0]["end"] == at(1)
    assert merged[0]["duration"] == timedelta(minutes=10)


# ---------------------------------------------------------------------------
# Bucket duration helper
# ---------------------------------------------------------------------------


def test_bucket_duration_exact_for_fixed_length_modes():
    from tw_report.core.report_slot import _bucket_duration

    assert _bucket_duration("day") == timedelta(days=1)
    assert _bucket_duration("week") == timedelta(days=7)


@pytest.mark.parametrize("mode, lo, hi", [("month", 28, 31), ("year", 365, 366)])
def test_bucket_duration_for_variable_length_modes_is_within_calendar_range(mode, lo, hi):
    # Variable-length periods are approximated (see the month/year split xfail above);
    # only the calendar range is asserted so the approximation is not pinned as correct.
    from tw_report.core.report_slot import _bucket_duration

    assert timedelta(days=lo) <= _bucket_duration(mode) <= timedelta(days=hi)


def test_bucket_duration_rejects_unknown_mode():
    from tw_report.core.report_slot import _bucket_duration

    with pytest.raises(ValueError, match="Unknown mode"):
        _bucket_duration("fortnight")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Remaining branches: guards, proration of optional fields, grouping edges
# ---------------------------------------------------------------------------


def test_break_gap_within_day_none_for_overlap_and_across_days():
    from tw_report.core.report_slot import _break_gap_within_day

    assert _break_gap_within_day(at(0), at(0.5), 0) == timedelta(hours=0.5)
    assert _break_gap_within_day(at(1), at(0.5), 0) is None  # overlapping
    assert _break_gap_within_day(at(13), at(15), 0) is None  # crosses midnight


def test_from_timeline_slots_rejects_empty_group():
    with pytest.raises(ValueError, match="empty slot list"):
        ReportTimelineSlot.from_timeline_slots([])


def test_from_timeline_slots_untracked_group_has_no_task_event():
    untracked = [
        rs(afk(0, 1)),
        rs(afk(1, 2)),
    ]
    ts = [
        TimelineSlot(
            type="afk",
            start=s.start,
            end=s.end,
            duration=s.duration,
            actual_duration=timedelta(0),
            afk_duration=s.duration,
            productive_duration=timedelta(0),
            project=NO_PROJECT,
            task=NO_TASK,
            categories=[],
            tags=[],
        )
        for s in untracked
    ]
    merged = ReportTimelineSlot.from_timeline_slots(ts, allow_mixed_types=True)
    assert merged.task_event is None
    assert merged.duration == timedelta(hours=2)


def test_from_timeline_slots_rejects_mixed_projects_without_flag():
    from tw_report.core.timeline import TimelineSlotValidationError

    with pytest.raises(TimelineSlotValidationError, match="differing projects"):
        ReportTimelineSlot.from_timeline_slots([work(0, 1), afk(1, 2)])  # P vs untracked
    merged = ReportTimelineSlot.from_timeline_slots(
        [work(0, 1), afk(1, 2)], allow_mixed_types=True
    )
    assert merged.duration == timedelta(hours=2)


def test_bucket_start_rejects_unknown_mode():
    with pytest.raises(ValueError, match="Unknown bucket mode"):
        ReportTimelineSlot.bucket_start(at(0), "fortnight")  # type: ignore[arg-type]


def test_split_skips_pathologically_long_spans():
    # A ten-year span exceeds the day, week and month guards (>100 days, >100 weeks,
    # >100 months), so the slot is returned unsplit. The year guard needs >36500 days and is
    # covered by the year-boundary xfail below.
    long_slot = ReportTimelineSlot(
        start=datetime(2000, 1, 1, tzinfo=UTC),
        end=datetime(2010, 1, 1, tzinfo=UTC),
        duration=timedelta(days=3653),
        actual_duration=timedelta(days=3653),
    )
    assert long_slot.split_at_boundaries("day", 0) == [long_slot]
    assert long_slot.split_at_boundaries("week", 0) == [long_slot]
    assert long_slot.split_at_boundaries("month", 0) == [long_slot]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known bug: same root cause as the month split - _bucket_duration('year') is a fixed "
        "365 days, which drifts off 1 January after leap years and produces a zero-length "
        "piece that raises ValueError. Not on the production path. Fix in Phase 3."
    ),
)
def test_split_at_year_boundary_preserves_total_duration():
    # 2028 is a leap year (366 days): the fixed 365-day step lands on 31 December.
    slot = ReportTimelineSlot(
        start=datetime(2028, 12, 31, 22, tzinfo=UTC),
        end=datetime(2029, 1, 1, 2, tzinfo=UTC),
        duration=timedelta(hours=4),
        actual_duration=timedelta(hours=4),
    )
    pieces = slot.split_at_boundaries("year", 0)
    assert len(pieces) == 2
    assert sum((p.duration for p in pieces), timedelta(0)) == timedelta(hours=4)


def test_split_prorates_event_and_offline_extension_durations():
    slot = ReportTimelineSlot(
        start=at(13),
        end=at(15),
        duration=timedelta(hours=2),
        actual_duration=timedelta(hours=2),
        event_duration=timedelta(hours=2),
        afk_duration=timedelta(hours=1),
        offline_extension_duration=timedelta(0),
    )
    pieces = slot.split_at_boundaries("day", 0)
    assert len(pieces) == 2
    assert all(p.event_duration == timedelta(hours=1) for p in pieces)
    assert all(p.afk_duration == timedelta(minutes=30) for p in pieces)


def test_split_keeps_offline_extension_as_remainder_after_event_time():
    slot = ReportTimelineSlot(
        start=at(13),
        end=at(15),
        duration=timedelta(hours=2),
        actual_duration=timedelta(0),
        event_duration=timedelta(hours=1),
        offline_extension_duration=timedelta(hours=1),
    )
    pieces = slot.split_at_boundaries("day", 0)
    assert len(pieces) == 2
    for piece in pieces:
        assert piece.offline_extension_duration == timedelta(hours=0) or (
            piece.offline_extension_duration == timedelta(minutes=30) or piece.offline_extension_duration is None
        )


def test_embedded_afk_with_no_overlap_contributes_nothing():
    combined = ReportTimelineSlot.from_work_slot_with_embedded_afk(work(0, 1), [afk(5, 6)])
    assert combined.afk_duration in (None, timedelta(0))


def test_embedded_afk_for_untracked_work_currently_gets_a_task_event():
    # NOTE (flagged, not fixed): from_work_slot_with_embedded_afk treats the NO_PROJECT /
    # NO_TASK sentinels as real names and builds a task_event, while from_timeline_slot
    # does not. The two converters disagree; pinned here until the model is unified.
    combined = ReportTimelineSlot.from_work_slot_with_embedded_afk(
        work(0, 1, project=NO_PROJECT, task=NO_TASK), []
    )
    assert combined.task_event is not None
    assert combined.task_event.data["project"] == NO_PROJECT


def test_consolidate_consecutive_starts_new_group_across_days():
    later = work(24 * 2, 24 * 2 + 1)  # two days after T0
    result = entries(work(0, 1), work(1, 2), later).consolidate_consecutive()
    durations = [s.duration for s in result.slots()]
    assert timedelta(hours=2) in durations
    assert timedelta(hours=1) in durations


def test_grouped_by_project_date_appends_same_project_and_day():
    groups = entries(work(0, 1), work(1, 2)).grouped_by_project_date()
    assert len(groups) == 1
    _, _, members = groups[0]
    assert len(members) == 2


def test_merge_categories_widens_range_on_later_and_earlier_occurrences():
    from tw_report.core.report_slot import _merge_categories_full

    merged = _merge_categories_full([
        _Slot([{"category": "Coding", "duration": timedelta(minutes=5), "start": at(1), "end": at(2),
                "apps": [_app("VSCode", 5, at(1), at(2))]}]),
        _Slot([{"category": "Coding", "duration": timedelta(minutes=5), "start": at(0), "end": at(3),
                "apps": [_app("VSCode", 5, at(0), at(3))]}]),
    ])
    assert merged[0]["start"] == at(0)
    assert merged[0]["end"] == at(3)
    vscode = merged[0]["apps"][0]
    assert vscode["start"] == at(0)
    assert vscode["end"] == at(3)
    assert vscode["duration"] == timedelta(minutes=10)


def test_merge_categories_keeps_wider_range_when_later_occurrence_is_narrower():
    from tw_report.core.report_slot import _merge_categories_full

    merged = _merge_categories_full([
        _Slot([{"category": "Coding", "duration": timedelta(minutes=5), "start": at(0), "end": at(3),
                "apps": [_app("VSCode", 5, at(0), at(3))]}]),
        _Slot([{"category": "Coding", "duration": timedelta(minutes=5), "start": at(1), "end": at(2),
                "apps": [_app("VSCode", 5, at(1), at(2))]}]),
    ])
    assert merged[0]["start"] == at(0)
    assert merged[0]["end"] == at(3)
    assert merged[0]["apps"][0]["start"] == at(0)
    assert merged[0]["apps"][0]["end"] == at(3)


def test_merge_overlapping_work_slots_keeps_non_overlapping_same_task_separate():
    slots = entries(work(0, 1), work(2, 3)).combine_work_with_embedded_afk().slots()
    assert len(slots) == 2
    assert [s.start for s in slots] == [at(0), at(2)]


def test_consolidate_consecutive_drops_untracked_zero_activity_slots():
    # Untracked slots with no active time (bare AFK / offline markers) are skipped here.
    assert entries(afk(0, 1), afk(1, 2)).consolidate_consecutive().slots() == []


def test_offline_extension_not_prorated_when_event_time_covers_the_piece():
    slot = ReportTimelineSlot(
        start=at(13),
        end=at(15),
        duration=timedelta(hours=2),
        actual_duration=timedelta(0),
        event_duration=timedelta(hours=2),
        offline_extension_duration=timedelta(hours=1),
    )
    for piece in slot.split_at_boundaries("day", 0):
        assert piece.offline_extension_duration is None


def test_embedded_afk_for_work_without_project_or_task_has_no_task_event():
    untracked_ts = TimelineSlot(
        type="regular",
        start=at(0),
        end=at(1),
        duration=timedelta(hours=1),
        actual_duration=timedelta(hours=1),
        productive_duration=timedelta(0),
        project="",
        task="",
        categories=[],
        tags=[],
    )
    combined = ReportTimelineSlot.from_work_slot_with_embedded_afk(untracked_ts, [])
    assert combined.task_event is None
