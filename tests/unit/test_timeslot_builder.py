"""
Tests for the unified sweep-line timeslot builder (timeslot_builder.py).

Verifies that build_timeslot_timeline() produces guaranteed non-overlapping,
properly-classified slots with correct duration breakdowns (actual/afk/offline).
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from tw_report.core.aw_events import AFKEvent, WindowEvent, TaskWarriorEvent
from tw_report.core.report_slot import ReportTimelineSlot
from tw_report.core.timeslot_builder import build_timeslot_timeline
from tw_report.pipeline.row_merge import merge_display_rows


# ============================================================================
# Test Fixtures: Event Construction Helpers
# ============================================================================


def make_datetime(
    year: int, month: int, day: int, hour: int, minute: int, second: int = 0
) -> datetime:
    """Create a UTC datetime for testing."""
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def make_afk_event(timestamp: datetime, duration: timedelta, status: str = "afk") -> AFKEvent:
    """Create an AFKEvent for testing."""
    event = AFKEvent(timestamp=timestamp, duration=duration)
    event.data["status"] = status
    return event


def make_window_event(
    timestamp: datetime,
    duration: timedelta,
    app: str = "test_app",
    title: str = "Test Window",
    category: Optional[List[str]] = None,
) -> WindowEvent:
    """Create a WindowEvent for testing."""
    if category is None:
        category = ["Test"]
    event = WindowEvent(timestamp=timestamp, duration=duration)
    event.data["app"] = app
    event.data["title"] = title
    event.data["$category"] = category
    return event


def make_task_event(
    timestamp: datetime,
    duration: timedelta,
    project: str = "TestProject",
    task: str = "TestTask",
    uuid: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> TaskWarriorEvent:
    """Create a TaskWarriorEvent for testing."""
    if tags is None:
        tags = []
    event = TaskWarriorEvent(timestamp=timestamp, duration=duration)
    event.data["project"] = project
    event.data["task"] = task
    if uuid:
        event.data["uuid"] = uuid
    event.data["tags"] = tags
    return event


# ============================================================================
# Invariant Tests: Generic, Run Against All Scenarios
# ============================================================================


def assert_no_overlaps(slots: List[ReportTimelineSlot]) -> None:
    """Verify no two slots in output ever overlap in [start, end) time."""
    if not slots:
        return
    ordered = sorted(slots, key=lambda s: s.start)
    for i, (a, b) in enumerate(zip(ordered, ordered[1:])):
        assert a.end <= b.start, (
            f"Overlap detected at slot {i}/{len(ordered)}: "
            f"slot[{i}] ({a.start} to {a.end}) overlaps slot[{i + 1}] ({b.start} to {b.end})"
        )


def assert_full_span_coverage(
    slots: List[ReportTimelineSlot],
    afk_events: List[AFKEvent],
    window_events: List[WindowEvent],
    task_events: List[TaskWarriorEvent],
) -> None:
    """Verify total output duration equals full wall-clock span of input events."""
    if not (afk_events + window_events + task_events):
        return

    all_events = afk_events + window_events + task_events
    span_start = min(e.timestamp for e in all_events)
    span_end = max(e.timestamp + e.duration for e in all_events)
    expected_span = span_end - span_start

    total_output = sum((s.duration for s in slots), timedelta(0)) if slots else timedelta(0)
    assert total_output == expected_span, (
        f"Full-span-coverage violated: total output {total_output} != expected span {expected_span}"
    )


def assert_task_time_conserved(
    slots: List[ReportTimelineSlot], task_event: TaskWarriorEvent
) -> None:
    """Verify task event's duration is fully represented in output (not silently dropped)."""
    # Find all slots attributed to this task
    matching = [
        s
        for s in slots
        if s.task_event is task_event
        or (
            s.task_event
            and s.task_event.uuid
            and task_event.uuid
            and s.task_event.uuid == task_event.uuid
        )
    ]

    if not matching:
        pytest.fail(f"Task event {task_event.task} not found in any output slot")

    total = timedelta(0)
    for s in matching:
        total += (
            s.actual_duration
            + (s.afk_duration or timedelta(0))
            + (s.offline_extension_duration or timedelta(0))
        )

    assert total == task_event.duration, (
        f"Task time not conserved for {task_event.task}: "
        f"expected {task_event.duration}, got {total} from {len(matching)} matching slots"
    )


# ============================================================================
# Primary Regression Test: Audited Scenario
# ============================================================================


class TestRegressionAuditedScenario:
    """Test the exact scenario from the 2026-08-30 audit."""

    def test_afk_no_project_overlapping_task_preparar_masa(self):
        """
        Regression test for the audited bug: AFK (No project) 17:48-18:20 overlapping
        task (Ecosistema, Preparar masa) 17:54-18:45.

        Expected output:
        - One AFK slot: 17:48-17:54 (the non-overlapping remainder)
        - One task slot: 17:54-18:45 with:
            - actual_duration: time with keyboard activity
            - afk_duration: embedded AFK (17:54-18:20)
            - (no offline, since AFK coverage exists)
        """
        # AFK event: 17:48-18:20, status "afk"
        afk_event = make_afk_event(
            timestamp=make_datetime(2026, 6, 27, 17, 48),
            duration=timedelta(minutes=32),
            status="afk",
        )

        # Task event: 17:54-18:45 (overlaps with AFK 17:54-18:20)
        task_event = make_task_event(
            timestamp=make_datetime(2026, 6, 27, 17, 54),
            duration=timedelta(minutes=51),
            project="Ecosistema",
            task="Preparar masa",
            uuid="task-uuid-123",
        )

        # not-afk AFK event covering the end of the task (18:20-18:45)
        # This ensures the task's tail is marked "online" (user active)
        not_afk_event = make_afk_event(
            timestamp=make_datetime(2026, 6, 27, 18, 20),
            duration=timedelta(minutes=25),
            status="not-afk",
        )

        slots = build_timeslot_timeline(
            afk_events=[afk_event, not_afk_event],
            window_events=[],
            task_events=[task_event],
        )

        # Verify no overlaps
        assert_no_overlaps(slots)

        # Verify full span coverage
        assert_full_span_coverage(
            slots,
            afk_events=[afk_event, not_afk_event],
            window_events=[],
            task_events=[task_event],
        )

        # Verify task time is conserved
        assert_task_time_conserved(slots, task_event)

        # Verify structure
        assert len(slots) >= 2, f"Expected at least 2 slots (AFK + task), got {len(slots)}"

        # Find the AFK-only slot (17:48-17:54)
        afk_only = [s for s in slots if s.task_event is None and s.afk_duration is not None]
        assert len(afk_only) >= 1, "Expected at least one AFK-only slot"
        afk_slot = afk_only[0]
        assert afk_slot.start == make_datetime(2026, 6, 27, 17, 48)
        assert afk_slot.end == make_datetime(2026, 6, 27, 17, 54)

        # Find the task slots (17:54-18:45)
        # With chronological timeline, AFK and active periods are separate slots
        task_slots = [s for s in slots if s.task_event is not None]
        assert len(task_slots) >= 2, f"Expected 2+ task slots (AFK + active), got {len(task_slots)}"

        # Slot 1: AFK period (17:54-18:20 = 26 min)
        afk_task_slot = task_slots[0]
        assert afk_task_slot.start == make_datetime(2026, 6, 27, 17, 54)
        assert afk_task_slot.end == make_datetime(2026, 6, 27, 18, 20)
        assert afk_task_slot.afk_duration == timedelta(minutes=26), (
            f"AFK task slot should have 26min (17:54-18:20), got {afk_task_slot.afk_duration}"
        )
        assert afk_task_slot.actual_duration == timedelta(0), (
            f"AFK slot should have 0 actual_duration, got {afk_task_slot.actual_duration}"
        )

        # Slot 2: Active period (18:20-18:45 = 25 min)
        active_task_slot = task_slots[1]
        assert active_task_slot.start == make_datetime(2026, 6, 27, 18, 20)
        assert active_task_slot.end == make_datetime(2026, 6, 27, 18, 45)
        assert active_task_slot.actual_duration == timedelta(minutes=25), (
            f"Active task slot should have 25min (18:20-18:45), got {active_task_slot.actual_duration}"
        )
        assert active_task_slot.afk_duration is None, (
            f"Active slot should have no AFK, got {active_task_slot.afk_duration}"
        )


# ============================================================================
# Edge Cases
# ============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_task_starting_exactly_at_afk_boundary(self):
        """Task event starting exactly when AFK event ends."""
        afk_event = make_afk_event(
            timestamp=make_datetime(2026, 6, 27, 10, 0),
            duration=timedelta(minutes=30),
            status="afk",
        )
        task_event = make_task_event(
            timestamp=make_datetime(2026, 6, 27, 10, 30),  # Exactly at AFK end
            duration=timedelta(minutes=20),
            uuid="task-1",
        )
        not_afk_event = make_afk_event(
            timestamp=make_datetime(2026, 6, 27, 10, 30),
            duration=timedelta(minutes=20),
            status="not-afk",
        )

        slots = build_timeslot_timeline(
            afk_events=[afk_event, not_afk_event],
            window_events=[],
            task_events=[task_event],
        )

        assert_no_overlaps(slots)
        assert_full_span_coverage(
            slots,
            afk_events=[afk_event, not_afk_event],
            window_events=[],
            task_events=[task_event],
        )

    def test_zero_gap_adjacent_tasks_same_uuid(self):
        """Two task events with same UUID, zero gap between them (should merge)."""
        task1 = make_task_event(
            timestamp=make_datetime(2026, 6, 27, 10, 0),
            duration=timedelta(minutes=30),
            uuid="task-shared",
            project="Proj",
            task="Task",
        )
        task2 = make_task_event(
            timestamp=make_datetime(2026, 6, 27, 10, 30),
            duration=timedelta(minutes=20),
            uuid="task-shared",
            project="Proj",
            task="Task",
        )
        not_afk = make_afk_event(
            timestamp=make_datetime(2026, 6, 27, 10, 0),
            duration=timedelta(minutes=50),
            status="not-afk",
        )

        slots = build_timeslot_timeline(
            afk_events=[not_afk],
            window_events=[],
            task_events=[task1, task2],
        )

        assert_no_overlaps(slots)
        # The builder keeps one slot per interval: the task boundary at 10:30 cuts it in two.
        task_slots = [s for s in slots if s.task_event is not None]
        assert len(task_slots) == 2
        # The display merge joins them into one row covering the full 50 minutes (30 + 20).
        rows = merge_display_rows(task_slots)
        assert len(rows) == 1
        assert rows[0].start == make_datetime(2026, 6, 27, 10, 0)
        assert rows[0].breakdown.active == timedelta(minutes=50)
        assert rows[0].end == make_datetime(2026, 6, 27, 10, 50)

    def test_empty_input_lists(self):
        """Builder should handle empty inputs gracefully."""
        slots = build_timeslot_timeline(
            afk_events=[],
            window_events=[],
            task_events=[],
        )
        assert slots == []

    def test_only_zero_duration_events(self):
        """Builder should skip zero-duration events and return empty."""
        afk_event = AFKEvent(
            timestamp=make_datetime(2026, 6, 27, 10, 0),
            duration=timedelta(0),  # Zero duration
        )
        task_event = TaskWarriorEvent(
            timestamp=make_datetime(2026, 6, 27, 10, 0),
            duration=timedelta(0),  # Zero duration
        )

        slots = build_timeslot_timeline(
            afk_events=[afk_event],
            window_events=[],
            task_events=[task_event],
        )
        assert slots == []

    def test_two_overlapping_different_tasks_tie_break(self):
        """Two different tasks overlapping — verify deterministic tie-break."""
        # Both tasks cover the same time period; earlier start should win
        task1 = make_task_event(
            timestamp=make_datetime(2026, 6, 27, 10, 0),
            duration=timedelta(minutes=30),
            uuid="task-1",
            project="Proj",
            task="Task1",
        )
        task2 = make_task_event(
            timestamp=make_datetime(2026, 6, 27, 10, 5),  # Starts 5 min later
            duration=timedelta(minutes=20),
            uuid="task-2",
            project="Proj",
            task="Task2",
        )
        not_afk = make_afk_event(
            timestamp=make_datetime(2026, 6, 27, 10, 0),
            duration=timedelta(minutes=30),
            status="not-afk",
        )

        slots = build_timeslot_timeline(
            afk_events=[not_afk],
            window_events=[],
            task_events=[task1, task2],
        )

        assert_no_overlaps(slots)
        # task1 should win (earlier start), so its duration should be fully represented
        assert_task_time_conserved(slots, task1)


# ============================================================================
# Task-Only Mode (No Window Events)
# ============================================================================


class TestTaskOnlyMode:
    """Test builder with empty window_events (e.g., --task-id mode)."""

    def test_task_only_with_afk_offline_classification(self):
        """Task-only mode should classify offline/online based on AFK bucket only."""
        task = make_task_event(
            timestamp=make_datetime(2026, 6, 27, 10, 0),
            duration=timedelta(minutes=60),
            uuid="task-1",
        )
        # AFK: 10:00-10:30 (offline period)
        # No AFK: 10:30-11:00 (online period)
        afk_offline = make_afk_event(
            timestamp=make_datetime(2026, 6, 27, 10, 0),
            duration=timedelta(minutes=30),
            status="afk",
        )
        afk_online = make_afk_event(
            timestamp=make_datetime(2026, 6, 27, 10, 30),
            duration=timedelta(minutes=30),
            status="not-afk",
        )

        slots = build_timeslot_timeline(
            afk_events=[afk_offline, afk_online],
            window_events=[],  # Empty!
            task_events=[task],
        )

        assert_no_overlaps(slots)
        task_slots = [s for s in slots if s.task_event is not None]
        # With chronological timeline, different AFK states = separate slots
        assert len(task_slots) == 2, f"Expected 2 slots (AFK + online), got {len(task_slots)}"

        # Slot 1: 10:00-10:30 (AFK period)
        afk_slot = task_slots[0]
        assert afk_slot.start == make_datetime(2026, 6, 27, 10, 0)
        assert afk_slot.end == make_datetime(2026, 6, 27, 10, 30)
        assert afk_slot.afk_duration == timedelta(minutes=30)
        assert afk_slot.actual_duration == timedelta(0)

        # Slot 2: 10:30-11:00 (online period)
        online_slot = task_slots[1]
        assert online_slot.start == make_datetime(2026, 6, 27, 10, 30)
        assert online_slot.end == make_datetime(2026, 6, 27, 11, 0)
        assert online_slot.actual_duration == timedelta(minutes=30)
        assert online_slot.afk_duration is None


# ============================================================================
# Integration: Multiple Event Sources
# ============================================================================


class TestMultipleEventSources:
    """Test builder with complex combinations of all three event types."""

    def test_task_with_window_and_afk_events(self):
        """Complete scenario: task + windows + AFK."""
        task = make_task_event(
            timestamp=make_datetime(2026, 6, 27, 10, 0),
            duration=timedelta(minutes=60),
            uuid="task-1",
            project="WebDev",
            task="Fix bug",
        )
        # Window events showing app activity
        window1 = make_window_event(
            timestamp=make_datetime(2026, 6, 27, 10, 0),
            duration=timedelta(minutes=30),
            app="vim",
            category=["Development"],
        )
        window2 = make_window_event(
            timestamp=make_datetime(2026, 6, 27, 10, 35),
            duration=timedelta(minutes=25),
            app="Chrome",
            category=["Communication"],
        )
        # AFK: idle 10:30-10:35
        afk_idle = make_afk_event(
            timestamp=make_datetime(2026, 6, 27, 10, 30),
            duration=timedelta(minutes=5),
            status="afk",
        )
        # Not-afk for the rest
        afk_active1 = make_afk_event(
            timestamp=make_datetime(2026, 6, 27, 10, 0),
            duration=timedelta(minutes=30),
            status="not-afk",
        )
        afk_active2 = make_afk_event(
            timestamp=make_datetime(2026, 6, 27, 10, 35),
            duration=timedelta(minutes=25),
            status="not-afk",
        )

        slots = build_timeslot_timeline(
            afk_events=[afk_idle, afk_active1, afk_active2],
            window_events=[window1, window2],
            task_events=[task],
        )

        assert_no_overlaps(slots)
        assert_full_span_coverage(
            slots,
            afk_events=[afk_idle, afk_active1, afk_active2],
            window_events=[window1, window2],
            task_events=[task],
        )
        assert_task_time_conserved(slots, task)

        # Verify the task slots have correct breakdown
        # With chronological timeline, different AFK states = separate slots
        task_slots = [s for s in slots if s.task_event is not None]
        assert len(task_slots) >= 3, (
            f"Expected at least 3 task slots (active+afk+active), got {len(task_slots)}"
        )

        # Calculate totals across all task slots
        total_afk = sum((s.afk_duration or timedelta(0)).total_seconds() for s in task_slots)
        total_active = sum((s.actual_duration or timedelta(0)).total_seconds() for s in task_slots)

        # Verify totals are correct
        assert total_afk == timedelta(minutes=5).total_seconds()  # Idle period
        assert total_active == timedelta(minutes=55).total_seconds()  # Active portions

        # Verify at least one slot has categories attached (from window events)
        has_categories = any(len(s.categories) > 0 for s in task_slots)
        assert has_categories, "Expected at least one slot with categories"


# ============================================================================
# State Continuity Micro-Slot Merging Tests (2026-09-02)
# ============================================================================


# ============================================================================
# Window-Event Proof-of-Activity Tests (2026-09-02)
# ============================================================================
