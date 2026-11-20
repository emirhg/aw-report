"""Unit tests for core/time_breakdown.py (the per-state split of a slot's time)."""

from datetime import timedelta

from tw_report.core.time_breakdown import TimeBreakdown, sum_breakdowns

TEN = timedelta(minutes=10)


def test_each_state_goes_to_its_bucket():
    assert TimeBreakdown.for_state(TEN, "not-afk") == TimeBreakdown(active=TEN)
    assert TimeBreakdown.for_state(TEN, "afk") == TimeBreakdown(afk=TEN)
    assert TimeBreakdown.for_state(TEN, "untracked") == TimeBreakdown(untracked=TEN)


def test_untracked_with_offline_tag_is_offline():
    assert TimeBreakdown.for_state(TEN, "untracked", offline_tagged=True) == TimeBreakdown(
        offline=TEN
    )


def test_offline_tag_has_no_effect_on_afk_or_active_states():
    assert TimeBreakdown.for_state(TEN, "not-afk", offline_tagged=True) == TimeBreakdown(active=TEN)
    assert TimeBreakdown.for_state(TEN, "afk", offline_tagged=True) == TimeBreakdown(afk=TEN)


def test_no_state_means_no_afk_data_even_when_offline_tagged():
    assert TimeBreakdown.for_state(TEN, None) == TimeBreakdown(no_afk_data=TEN)
    assert TimeBreakdown.for_state(TEN, None, offline_tagged=True) == TimeBreakdown(no_afk_data=TEN)


def test_derived_totals():
    b = TimeBreakdown(
        active=timedelta(hours=1),
        afk=timedelta(minutes=30),
        untracked=timedelta(minutes=15),
        offline=timedelta(minutes=45),
        no_afk_data=timedelta(minutes=5),
    )
    assert b.online == timedelta(hours=1, minutes=30)
    assert b.worked == timedelta(hours=1, minutes=45)
    assert b.total == timedelta(hours=2, minutes=35)


def test_sum_breakdowns_adds_every_bucket():
    parts = [TimeBreakdown(active=TEN), TimeBreakdown(afk=TEN), TimeBreakdown(untracked=TEN)]
    assert sum_breakdowns(parts) == TimeBreakdown(active=TEN, afk=TEN, untracked=TEN)


def test_sum_of_nothing_is_zero():
    assert sum_breakdowns([]) == TimeBreakdown()


def test_prorated_takes_the_share_of_a_part_of_the_interval():
    whole = TimeBreakdown(active=timedelta(minutes=60), offline=timedelta(minutes=30))
    share = whole.prorated(timedelta(minutes=45), timedelta(minutes=90))
    assert share == TimeBreakdown(active=timedelta(minutes=30), offline=timedelta(minutes=15))


def test_prorated_over_an_empty_interval_is_zero():
    whole = TimeBreakdown(active=timedelta(minutes=60))
    assert whole.prorated(timedelta(0), timedelta(0)) == TimeBreakdown()
