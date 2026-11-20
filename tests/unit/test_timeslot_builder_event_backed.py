"""
CRITICAL: Event-Backed Slot Invariants

These tests enforce the fundamental rule:
- EVERY slot must be backed by at least ONE event (AFK, WINDOW, or TASK)
- NO phantom slots invented from gaps without events
- NO slot should exist without event coverage
"""

import pytest
from datetime import datetime, timedelta, timezone
from aw_report.core.aw_events import AFKEvent, WindowEvent, TaskWarriorEvent
from aw_report.core.timeslot_builder import build_timeslot_timeline


class TestEventBackedInvariant:
    """Core invariant: Every slot must be backed by at least one event"""

    def test_gap_between_afk_events_is_untracked_not_phantom(self):
        """
        A gap between two AFK events of one logical day is untracked time (its own state),
        not a phantom slot. Its slots carry afk_state "untracked" and no task.

        Scenario:
        - AFK event: 14:00-14:03:11
        - [GAP 14:03:11-14:18:44: no events]
        - AFK event: 14:18:44-14:30:00
        """
        afk_events = [
            AFKEvent(
                timestamp=datetime(2026, 8, 30, 14, 0, 0, tzinfo=timezone.utc),
                duration=timedelta(minutes=3, seconds=11),
                data={"status": "not-afk"},
            ),
            AFKEvent(
                timestamp=datetime(2026, 8, 30, 14, 18, 44, tzinfo=timezone.utc),
                duration=timedelta(minutes=11, seconds=16),
                data={"status": "not-afk"},
            ),
        ]
        slots = build_timeslot_timeline(afk_events, [], [])
        gap_start = datetime(2026, 8, 30, 14, 3, 11, tzinfo=timezone.utc)
        gap_end = datetime(2026, 8, 30, 14, 18, 44, tzinfo=timezone.utc)
        gap_slots = [s for s in slots if s.start >= gap_start and s.end <= gap_end]

        assert gap_slots, "the gap must be represented as untracked time"
        assert all(s.afk_state == "untracked" for s in gap_slots)
        assert all(s.task_event is None for s in gap_slots)
        assert sum((s.duration for s in gap_slots), timedelta(0)) == gap_end - gap_start

    def test_every_slot_has_event_coverage(self):
        """
        Invariant: Every returned slot must have at least one event covering it.

        For each slot, verify:
        - At least one AFK event covers it, OR
        - At least one WINDOW event covers it, OR
        - At least one TASK event covers it
        """
        afk_events = [
            AFKEvent(
                timestamp=datetime(2026, 8, 30, 20, 0, 0, tzinfo=timezone.utc),
                duration=timedelta(hours=1),
                data={"status": "not-afk"},
            ),
        ]
        window_events = [
            WindowEvent(
                timestamp=datetime(2026, 8, 30, 20, 30, 0, tzinfo=timezone.utc),
                duration=timedelta(minutes=20),
                data={"app": "kitty"},
            ),
        ]

        slots = build_timeslot_timeline(afk_events, window_events, [])

        # For each slot, verify event backing
        for slot in slots:
            has_afk_coverage = any(
                e.timestamp <= slot.start < e.timestamp + e.duration
                or e.timestamp < slot.end <= e.timestamp + e.duration
                or (e.timestamp <= slot.start and slot.end <= e.timestamp + e.duration)
                for e in afk_events
            )
            has_window_coverage = any(
                e.timestamp <= slot.start < e.timestamp + e.duration
                or e.timestamp < slot.end <= e.timestamp + e.duration
                or (e.timestamp <= slot.start and slot.end <= e.timestamp + e.duration)
                for e in window_events
            )

            assert has_afk_coverage or has_window_coverage, (
                f"Slot {slot.start}-{slot.end} has no event coverage! Phantom slot!"
            )

    def test_all_events_converted_to_slots(self):
        """
        Invariant: Every event must be converted to at least one slot.

        No event should disappear or be lost in conversion.
        """
        afk_events = [
            AFKEvent(
                timestamp=datetime(2026, 8, 30, 20, 0, 0, tzinfo=timezone.utc),
                duration=timedelta(minutes=10),
                data={"status": "not-afk"},
            ),
            AFKEvent(
                timestamp=datetime(2026, 8, 30, 20, 20, 0, tzinfo=timezone.utc),
                duration=timedelta(minutes=10),
                data={"status": "afk"},
            ),
        ]

        slots = build_timeslot_timeline(afk_events, [], [])

        # Should have at least one slot per event
        assert len(slots) >= len(afk_events), (
            f"Events lost! {len(afk_events)} events but only {len(slots)} slots"
        )

    def test_no_invented_durations(self):
        """
        Slot time is exactly the event time plus the untracked gap between AFK events.

        Events: 20:00-20:05 and 20:20-20:25 (10 minutes of events).
        Untracked gap: 20:05-20:20 (15 minutes, inside one logical day).
        """
        afk_events = [
            AFKEvent(
                timestamp=datetime(2026, 8, 30, 20, 0, 0, tzinfo=timezone.utc),
                duration=timedelta(minutes=5),
                data={"status": "not-afk"},
            ),
            AFKEvent(
                timestamp=datetime(2026, 8, 30, 20, 20, 0, tzinfo=timezone.utc),
                duration=timedelta(minutes=5),
                data={"status": "not-afk"},
            ),
        ]
        total_event_duration = sum((e.duration for e in afk_events), timedelta(0))
        gap = timedelta(minutes=15)

        slots = build_timeslot_timeline(afk_events, [], [])
        total_slot_duration = sum((s.duration for s in slots), timedelta(0))

        assert total_slot_duration == total_event_duration + gap, (
            f"Events {total_event_duration} + gap {gap} != slots {total_slot_duration}"
        )

    def test_gap_detection_realistic_scenario(self):
        """
        Real-world scenario: the 20:03-20:18 gap has no AFK, window or task events.

        Expected: the gap is untracked time (afk_state "untracked") and every slot in it is
        untracked; no slot claims an AFK state or a task inside it.
        """
        afk_events = [
            AFKEvent(
                timestamp=datetime(2026, 8, 30, 20, 0, 0, tzinfo=timezone.utc),
                duration=timedelta(minutes=3, seconds=11),
                data={"status": "not-afk"},
            ),
            AFKEvent(
                timestamp=datetime(2026, 8, 30, 20, 18, 44, tzinfo=timezone.utc),
                duration=timedelta(minutes=11, seconds=16),
                data={"status": "not-afk"},
            ),
        ]
        window_events = [
            WindowEvent(
                timestamp=datetime(2026, 8, 30, 20, 0, 0, tzinfo=timezone.utc),
                duration=timedelta(minutes=3, seconds=14),
                data={"app": "kitty"},
            ),
            WindowEvent(
                timestamp=datetime(2026, 8, 30, 20, 18, 43, tzinfo=timezone.utc),
                duration=timedelta(minutes=6),
                data={"app": "kitty"},
            ),
        ]
        slots = build_timeslot_timeline(afk_events, window_events, [])

        gap_start = datetime(2026, 8, 30, 20, 3, 11, tzinfo=timezone.utc)
        gap_end = datetime(2026, 8, 30, 20, 18, 44, tzinfo=timezone.utc)
        in_gap = [s for s in slots if s.start < gap_end and s.end > gap_start]

        assert in_gap, "the gap must produce untracked slots"
        assert all(s.afk_state == "untracked" for s in in_gap)
        assert sum((s.duration for s in in_gap), timedelta(0)) == gap_end - gap_start
