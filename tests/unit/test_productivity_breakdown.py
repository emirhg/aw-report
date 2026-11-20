"""Tests for the default Active/AFK split by category productivity score."""

from datetime import datetime, timedelta, timezone

from tw_report.core.aw_events import WindowEvent
from tw_report.core.report_slot import ReportTimelineSlot
from tw_report.pipeline.processors import compute_productivity_breakdown

T0 = datetime(2026, 10, 15, 9, 0, 0, tzinfo=timezone.utc)
SCORES = {"Work": 2.0, "Social": -1.0, "Misc": 0.0}


def _window(offset_min, minutes, category):
    return WindowEvent(
        timestamp=T0 + timedelta(minutes=offset_min),
        duration=timedelta(minutes=minutes),
        data={"app": "app", "title": "t", "$category": [category]},
    )


def _active_slot(start_min, minutes):
    start = T0 + timedelta(minutes=start_min)
    end = start + timedelta(minutes=minutes)
    return ReportTimelineSlot(
        start=start,
        end=end,
        duration=end - start,
        actual_duration=end - start,
        afk_state="not-afk",
    )


def _afk_slot(start_min, minutes):
    start = T0 + timedelta(minutes=start_min)
    end = start + timedelta(minutes=minutes)
    return ReportTimelineSlot(
        start=start,
        end=end,
        duration=end - start,
        actual_duration=timedelta(0),
        afk_duration=end - start,
        afk_state="afk",
    )


def _total(*parts):
    return sum(parts, timedelta(0))


class TestActiveBreakdown:
    def test_positive_negative_zero_split(self):
        windows = [_window(0, 10, "Work"), _window(10, 10, "Social"), _window(20, 10, "Misc")]
        result = compute_productivity_breakdown([_active_slot(0, 30)], windows, SCORES)
        assert result.active_productive == timedelta(minutes=10)
        assert result.active_unproductive == timedelta(minutes=10)
        assert result.active_undefined == timedelta(minutes=10)

    def test_window_uncovered_time_is_undefined(self):
        windows = [_window(0, 10, "Work")]
        result = compute_productivity_breakdown([_active_slot(0, 30)], windows, SCORES)
        assert result.active_productive == timedelta(minutes=10)
        assert result.active_undefined == timedelta(minutes=20)

    def test_parts_sum_to_active_time(self):
        windows = [_window(0, 7, "Work"), _window(7, 3, "Social")]
        slot = _active_slot(0, 25)
        result = compute_productivity_breakdown([slot], windows, SCORES)
        total = _total(
            result.active_productive, result.active_unproductive, result.active_undefined
        )
        assert abs((total - slot.actual_duration).total_seconds()) < 0.001

    def test_no_windows_all_undefined(self):
        result = compute_productivity_breakdown([_active_slot(0, 30)], [], SCORES)
        assert result.active_undefined == timedelta(minutes=30)
        assert result.active_productive == timedelta(0)


class TestAfkBreakdown:
    def test_afk_split_uses_windows_over_afk_span(self):
        windows = [_window(0, 5, "Social")]
        result = compute_productivity_breakdown([_afk_slot(0, 10)], windows, SCORES)
        assert result.afk_unproductive == timedelta(minutes=5)
        assert result.afk_undefined == timedelta(minutes=5)
        assert result.active_productive == timedelta(0)

    def test_afk_only_slot_counted_once_without_consolidation(self):
        result = compute_productivity_breakdown([_afk_slot(0, 10)], [], SCORES)
        total = _total(result.afk_productive, result.afk_unproductive, result.afk_undefined)
        assert total == timedelta(minutes=10)


class TestFiltering:
    def test_offline_slots_are_excluded(self):
        slot = ReportTimelineSlot(
            start=T0,
            end=T0 + timedelta(minutes=30),
            duration=timedelta(minutes=30),
            actual_duration=timedelta(0),
            offline_extension_duration=timedelta(minutes=30),
            event_duration=timedelta(0),
        )
        result = compute_productivity_breakdown([slot], [_window(0, 30, "Work")], SCORES)
        assert result.active_productive == timedelta(0)
        assert result.afk_productive == timedelta(0)


class TestConsolidatedShapes:
    def test_offline_tagged_slot_with_online_time_is_not_skipped(self):
        """Consolidated offline-tagged slots keep their active time in the breakdown."""
        start = T0
        end = T0 + timedelta(minutes=20)
        from tw_report.core.time_breakdown import TimeBreakdown

        slot = ReportTimelineSlot(
            start=start,
            end=end,
            duration=timedelta(minutes=20),
            actual_duration=timedelta(minutes=20),
            stored_breakdown=TimeBreakdown(
                active=timedelta(minutes=20), offline=timedelta(minutes=10)
            ),
        )
        windows = [_window(0, 20, "Work")]
        result = compute_productivity_breakdown([slot], windows, SCORES)
        assert result.active_productive == timedelta(minutes=20)

    def test_active_total_rescales_parts_to_displayed_total(self):
        """Parts sum exactly to the displayed Active Time even if raw coverage differs."""
        windows = [_window(0, 10, "Work")]
        slot = _active_slot(0, 30)
        result = compute_productivity_breakdown(
            [slot], windows, SCORES, active_total=timedelta(minutes=25)
        )
        total = _total(
            result.active_productive, result.active_unproductive, result.active_undefined
        )
        assert total == timedelta(minutes=25)

    def test_afk_total_with_no_windows_is_all_undefined(self):
        """AFK time with no window coverage is reported as undefined, matching TOTALS."""
        result = compute_productivity_breakdown(
            [_afk_slot(0, 10)], [], SCORES, afk_total=timedelta(minutes=10)
        )
        assert result.afk_undefined == timedelta(minutes=10)
        assert result.afk_productive == timedelta(0)

    def test_single_state_slot_fully_covered_is_productive(self):
        """A single online slot covered by Work windows is entirely productive."""
        windows = [_window(0, 30, "Work")]
        result = compute_productivity_breakdown([_active_slot(0, 30)], windows, SCORES)
        assert result.active_productive == timedelta(minutes=30)
        assert result.active_undefined == timedelta(0)
