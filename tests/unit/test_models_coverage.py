"""Coverage tests for pipeline.models value objects (validation, arithmetic, formatting)."""

from datetime import timedelta

import pytest

from tw_report.pipeline.models import PeriodMetrics, ReportTotals, TimeslotDuration


class TestTimeslotDuration:
    def test_requires_online_or_offline(self):
        with pytest.raises(ValueError, match="requires at least one"):
            TimeslotDuration()

    def test_afk_portion_requires_online_duration(self):
        with pytest.raises(ValueError, match="requires online_duration"):
            TimeslotDuration(offline_gap=timedelta(hours=1), afk_portion=timedelta(minutes=5))

    def test_afk_portion_cannot_exceed_online(self):
        with pytest.raises(ValueError, match="cannot exceed online_duration"):
            TimeslotDuration(online_duration=timedelta(hours=1), afk_portion=timedelta(hours=2))

    def test_total_duration_is_online_plus_offline(self):
        slot = TimeslotDuration(
            online_duration=timedelta(minutes=20), offline_gap=timedelta(minutes=50)
        )
        assert slot.total_duration == timedelta(hours=1, minutes=10)

    def test_total_duration_with_offline_only(self):
        assert TimeslotDuration(offline_gap=timedelta(hours=2)).total_duration == timedelta(hours=2)

    def test_has_online_and_has_offline_flags(self):
        online_only = TimeslotDuration(online_duration=timedelta(minutes=5))
        assert online_only.has_online() is True
        assert online_only.has_offline() is False
        zero_online = TimeslotDuration(online_duration=timedelta(0), offline_gap=timedelta(0))
        assert zero_online.has_online() is False
        assert zero_online.has_offline() is False
        offline = TimeslotDuration(offline_gap=timedelta(minutes=1))
        assert offline.has_offline() is True

    def test_str_lists_every_present_component(self):
        slot = TimeslotDuration(
            online_duration=timedelta(hours=1),
            afk_portion=timedelta(minutes=15),
            offline_gap=timedelta(minutes=30),
        )
        assert str(slot) == "TimeslotDuration(online=01:00:00, afk=00:15:00, offline=00:30:00)"

    def test_str_omits_absent_components(self):
        assert str(TimeslotDuration(offline_gap=timedelta(seconds=75))) == (
            "TimeslotDuration(offline=00:01:15)"
        )
        assert str(TimeslotDuration(online_duration=timedelta(0))) == (
            "TimeslotDuration(online=00:00:00)"
        )


class TestPeriodMetrics:
    def test_defaults_are_zero(self):
        metrics = PeriodMetrics()
        assert metrics.total_duration == timedelta(0)
        assert metrics.active_duration == timedelta(0)

    def test_add_accumulates_each_positive_field(self):
        metrics = PeriodMetrics()
        metrics.add(
            online=timedelta(hours=1),
            afk=timedelta(minutes=10),
            offline=timedelta(minutes=20),
            productive=timedelta(minutes=40),
            break_time=timedelta(minutes=5),
        )
        assert metrics.online_duration == timedelta(hours=1)
        assert metrics.afk_duration == timedelta(minutes=10)
        assert metrics.offline_gap == timedelta(minutes=20)
        assert metrics.productive_duration == timedelta(minutes=40)
        assert metrics.break_duration == timedelta(minutes=5)

    def test_add_ignores_none_and_non_positive_values(self):
        metrics = PeriodMetrics()
        metrics.add(
            online=None,
            afk=timedelta(0),
            offline=timedelta(seconds=-5),
            productive=None,
            break_time=timedelta(0),
        )
        assert metrics == PeriodMetrics()

    def test_add_timeslot_forwards_components(self):
        metrics = PeriodMetrics()
        slot = TimeslotDuration(
            online_duration=timedelta(hours=2),
            afk_portion=timedelta(minutes=30),
            offline_gap=timedelta(minutes=15),
        )
        metrics.add_timeslot(slot, productive=timedelta(hours=1))
        assert metrics.online_duration == timedelta(hours=2)
        assert metrics.afk_duration == timedelta(minutes=30)
        assert metrics.offline_gap == timedelta(minutes=15)
        assert metrics.productive_duration == timedelta(hours=1)

    def test_total_and_active_durations(self):
        metrics = PeriodMetrics(
            online_duration=timedelta(hours=2),
            afk_duration=timedelta(minutes=30),
            offline_gap=timedelta(hours=1),
        )
        assert metrics.total_duration == timedelta(hours=3)
        assert metrics.active_duration == timedelta(hours=1, minutes=30)

    def test_str_shows_only_non_zero_components(self):
        assert str(PeriodMetrics(online_duration=timedelta(hours=1))) == (
            "PeriodMetrics(online=01:00:00)"
        )
        full = PeriodMetrics(
            online_duration=timedelta(hours=1),
            afk_duration=timedelta(minutes=10),
            offline_gap=timedelta(minutes=5),
            productive_duration=timedelta(minutes=45),
        )
        assert str(full) == (
            "PeriodMetrics(online=01:00:00, afk=00:10:00, offline=00:05:00, productive=00:45:00)"
        )


class TestReportTotals:
    def test_active_time_none_without_online_time(self):
        assert ReportTotals().active_time is None

    def test_active_time_subtracts_afk(self):
        totals = ReportTotals(online_time=timedelta(hours=2), afk_time=timedelta(minutes=20))
        assert totals.active_time == timedelta(hours=1, minutes=40)

    def test_active_time_without_afk_equals_online(self):
        assert ReportTotals(online_time=timedelta(hours=2)).active_time == timedelta(hours=2)

    def test_total_time_none_when_nothing_recorded(self):
        assert ReportTotals().total_time is None
        assert ReportTotals(online_time=timedelta(0), offline_time=timedelta(0)).total_time is None

    def test_total_time_is_online_plus_offline(self):
        totals = ReportTotals(online_time=timedelta(hours=1), offline_time=timedelta(minutes=30))
        assert totals.total_time == timedelta(hours=1, minutes=30)

    def test_total_time_offline_only(self):
        assert ReportTotals(offline_time=timedelta(minutes=5)).total_time == timedelta(minutes=5)

    def test_str_lists_present_components(self):
        totals = ReportTotals(
            online_time=timedelta(hours=1),
            afk_time=timedelta(minutes=5),
            offline_time=timedelta(minutes=10),
            productive_time=timedelta(minutes=30),
        )
        assert str(totals) == (
            "ReportTotals(online=01:00:00, afk=00:05:00, offline=00:10:00, productive=00:30:00)"
        )
        assert str(ReportTotals()) == "ReportTotals()"

    def test_format_td_handles_none(self):
        assert ReportTotals._format_td(None) == "00:00:00"
        assert ReportTotals._format_td(timedelta(hours=3, minutes=2, seconds=1)) == "03:02:01"
