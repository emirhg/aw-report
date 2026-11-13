"""Coverage tests for small modules: events, filtering, config, formatting and builder edges."""

import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest
from aw_core.models import Event

from tw_report.core.aw_events import AFKEvent, TaskWarriorEvent, WindowEvent
from tw_report.core.filtering import NO_PROJECT, NO_TASK, EventFilter
from tw_report.core.task_matching import task_has_offline_tag
from tw_report.core.timeline import TimelineSlot
from tw_report.core.timeslot_builder import _RunAccumulator
from tw_report.core.report_slot import ReportTimelineSlot
from tw_report.pipeline.consolidation import consolidate_sessions
from tw_report.utils.formatting import (
    display_width,
    format_timeslot_duration,
    sanitize_title,
)

UTC = timezone.utc
T0 = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Typed event subclasses
# ---------------------------------------------------------------------------


def _window(data):
    return WindowEvent(timestamp=T0, duration=timedelta(minutes=5), data=data)


def test_window_event_defaults_and_category_setter():
    event = _window({})
    assert event.app == "Unknown App"
    assert event.title == "No Title"
    assert event.category == ["Uncategorized"]
    event.category = ["Coding", "Editor"]
    assert event.category == ["Coding", "Editor"]


def test_window_event_offline_extension_getter_and_setter():
    event = _window({})
    assert event.offline_extension_duration is None
    event.offline_extension_duration = timedelta(minutes=3)
    assert event.offline_extension_duration == timedelta(minutes=3)
    event.offline_extension_duration = None
    assert "offline_extension_duration" not in event.data


def test_afk_event_status_and_is_afk():
    afk = AFKEvent(timestamp=T0, duration=timedelta(minutes=1), data={"status": "afk"})
    not_afk = AFKEvent(timestamp=T0, duration=timedelta(minutes=1), data={"status": "not-afk"})
    assert afk.is_afk is True
    assert not_afk.is_afk is False


def test_task_warrior_title_fallback_strips_leetcode_prefix():
    event = TaskWarriorEvent(
        timestamp=T0,
        duration=timedelta(minutes=1),
        data={"title": "LeetCode 42: https://leetcode.com/problems/trapping-rain-water"},
    )
    assert event.task == "LeetCode 42"


def test_task_warrior_title_fallback_truncates_long_titles():
    long_title = "x" * 80
    event = TaskWarriorEvent(
        timestamp=T0, duration=timedelta(minutes=1), data={"title": long_title}
    )
    assert event.task == "x" * 50


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known bug: the LeetCode title branch splits on ':' and so cuts a bare URL title at "
        "its scheme, returning 'https'. Intended: keep the title. Fix in Phase 3."
    ),
)
def test_task_warrior_bare_url_title_is_not_cut_at_scheme_colon():
    event = TaskWarriorEvent(
        timestamp=T0,
        duration=timedelta(minutes=1),
        data={"title": "https://leetcode.com/problems/two-sum"},
    )
    assert event.task == "https://leetcode.com/problems/two-sum"


def test_task_has_offline_tag_for_plain_event_reads_tags():
    # The annotation says TaskWarriorEvent, but plain events are supported by design (hasattr).
    plain = Event(timestamp=T0, duration=timedelta(minutes=1), data={"tags": ["Offline"]})
    assert task_has_offline_tag(plain) is True  # type: ignore[arg-type]
    untagged = Event(timestamp=T0, duration=timedelta(minutes=1), data={"tags": ["work"]})
    assert task_has_offline_tag(untagged) is False  # type: ignore[arg-type]


def test_task_has_offline_tag_accepts_a_single_string_tag():
    event = Event(timestamp=T0, duration=timedelta(minutes=1), data={"tags": "OFFLINE"})
    assert task_has_offline_tag(event) is True  # type: ignore[arg-type]


def test_task_warrior_title_with_leetcode_and_url_but_no_colon_is_kept():
    event = TaskWarriorEvent(
        timestamp=T0,
        duration=timedelta(minutes=1),
        data={"title": "leetcode daily http-link"},
    )
    assert event.task == "leetcode daily http-link"


def test_task_has_offline_tag_for_typed_event():
    typed = TaskWarriorEvent(
        timestamp=T0, duration=timedelta(minutes=1), data={"tags": ["offline"]}
    )
    assert task_has_offline_tag(typed) is True


# ---------------------------------------------------------------------------
# EventFilter
# ---------------------------------------------------------------------------


def test_search_term_is_prepended_to_project_and_task_patterns():
    event_filter = EventFilter(
        project_patterns=["Climb"], task_patterns=["Task1"], search_term="web"
    )
    assert event_filter.project_patterns == ["web", "Climb"]
    assert event_filter.task_patterns == ["web", "Task1"]


def test_empty_pattern_list_accepts_everything():
    event_filter = EventFilter()
    assert event_filter._matches_any(["anything"], []) is True


def test_unknown_entry_type_is_not_subject_to_untracked_exclusion():
    event_filter = EventFilter(exclude_non_project=True)
    entry = {"project": NO_PROJECT, "task": NO_TASK}
    assert event_filter.should_include_entry(entry, entry_type="gap") is False
    assert event_filter.should_include_entry(entry, entry_type="custom") is True


# ---------------------------------------------------------------------------
# Config path resolution and TOML module selection
# ---------------------------------------------------------------------------


def test_config_path_raises_when_home_directory_is_missing(monkeypatch, tmp_path):
    from tw_report import config as config_module

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(config_module.Path, "home", staticmethod(lambda: tmp_path / "absent"))
    with pytest.raises(ValueError, match="home directory"):
        config_module.get_config_path()


def test_toml_module_is_stdlib_tomllib_on_python_311_and_newer(monkeypatch):
    import tomli

    from tw_report import config as config_module

    monkeypatch.setitem(sys.modules, "tomllib", tomli)
    monkeypatch.setattr(sys, "version_info", (3, 11, 0, "final", 0))
    reloaded = importlib.reload(config_module)
    try:
        assert reloaded.tomllib is tomli
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


# ---------------------------------------------------------------------------
# Timeline slot serialization
# ---------------------------------------------------------------------------


def test_timeline_slot_to_dict_includes_every_optional_field():
    slot = TimelineSlot(
        type="regular",
        start=T0,
        end=T0 + timedelta(hours=1),
        duration=timedelta(hours=1),
        actual_duration=timedelta(minutes=50),
        productive_duration=timedelta(minutes=40),
        project="P",
        task="T",
        categories=[{"category": "Coding"}],
        tags=["work"],
        afk_duration=timedelta(minutes=10),
        offline_extension_duration=timedelta(minutes=5),
        event_duration=timedelta(0),
        apps=[{"app": "VSCode"}],
    )
    as_dict = slot.to_dict()
    assert as_dict["categories"] == [{"category": "Coding"}]
    assert as_dict["tags"] == ["work"]
    assert as_dict["afk_duration"] == timedelta(minutes=10)
    assert as_dict["offline_extension_duration"] == timedelta(minutes=5)
    assert as_dict["event_duration"] == timedelta(0)
    assert as_dict["apps"] == [{"app": "VSCode"}]


def test_timeline_slot_from_afk_dict_infers_afk_duration_from_actual():
    slot = TimelineSlot.from_dict(
        {
            "type": "afk",
            "start": T0,
            "end": T0 + timedelta(minutes=30),
            "duration": timedelta(minutes=30),
            "actual_duration": timedelta(minutes=30),
            "project": NO_PROJECT,
            "task": NO_TASK,
        }
    )
    assert slot.afk_duration == timedelta(minutes=30)


# ---------------------------------------------------------------------------
# Consolidation helpers
# ---------------------------------------------------------------------------


def test_consolidate_sessions_empty_input():
    assert consolidate_sessions([]) == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known bug: _to_dict copies event_duration via getattr, so a ReportTimelineSlot whose "
        "event_duration is None makes the merge sum fail with TypeError. The production path "
        "passes dicts (to_dict omits None fields), so it is unaffected. Fix in Phase 3."
    ),
)
def test_consolidate_sessions_accepts_report_slot_objects():
    first = ReportTimelineSlot(
        start=T0,
        end=T0 + timedelta(hours=1),
        duration=timedelta(hours=1),
        actual_duration=timedelta(hours=1),
    )
    second = ReportTimelineSlot(
        start=T0 + timedelta(hours=1),
        end=T0 + timedelta(hours=2),
        duration=timedelta(hours=1),
        actual_duration=timedelta(hours=1),
    )
    merged = consolidate_sessions([first, second])
    assert len(merged) == 1
    assert merged[0]["duration"] == timedelta(hours=2)


def test_consolidate_sessions_uses_start_plus_duration_when_end_missing():
    slots = [
        {
            "start": T0,
            "duration": timedelta(hours=1),
            "actual_duration": timedelta(hours=1),
            "project": "P",
            "task": "T",
            "end": None,
        },
        {
            "start": T0 + timedelta(hours=1),
            "duration": timedelta(hours=1),
            "actual_duration": timedelta(hours=1),
            "project": "P",
            "task": "T",
            "end": None,
        },
    ]
    merged = consolidate_sessions(slots)
    assert merged[0]["duration"] == timedelta(hours=2)


def test_consolidate_sessions_preserves_offline_extension_total():
    slots = [
        {
            "start": T0,
            "end": T0 + timedelta(hours=1),
            "duration": timedelta(hours=1),
            "actual_duration": timedelta(0),
            "project": "P",
            "task": "T",
            "offline_extension_duration": timedelta(minutes=30),
        },
        {
            "start": T0 + timedelta(hours=1),
            "end": T0 + timedelta(hours=2),
            "duration": timedelta(hours=1),
            "actual_duration": timedelta(0),
            "project": "P",
            "task": "T",
            "offline_extension_duration": timedelta(minutes=15),
        },
    ]
    merged = consolidate_sessions(slots)
    assert merged[0]["offline_extension_duration"] == timedelta(minutes=45)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def test_display_width_counts_fullwidth_characters_as_two_columns():
    assert display_width("ab") == 2
    assert display_width("漢字") == 4


def test_sanitize_title_handles_empty_control_and_symbol_characters():
    assert sanitize_title("") == ""
    assert sanitize_title("ok\x01text") == "oktext"
    assert sanitize_title("Ünïcode 漢字 ★") == "Ünïcode 漢字 "


def test_offline_only_duration_with_productive_time_shows_zero_percent_label():
    text = format_timeslot_duration(
        TimeslotDuration_offline_only(),
        productive_duration=timedelta(minutes=5),
    )
    assert "OFF" in text
    assert "[prod   0%]" in text


def TimeslotDuration_offline_only():
    from tw_report.pipeline.models import TimeslotDuration

    return TimeslotDuration(offline_gap=timedelta(minutes=30))


# ---------------------------------------------------------------------------
# Timeslot builder internals
# ---------------------------------------------------------------------------


def test_run_accumulator_ignores_unknown_tick_buckets():
    acc = _RunAccumulator(
        ("generic", "active"), None, T0, T0 + timedelta(minutes=1), "online", timedelta(minutes=1)
    )
    before = (
        acc.online_duration,
        acc.embedded_afk_duration,
        acc.offline_duration,
        acc.generic_afk_duration,
    )
    acc._add_tick("not-a-bucket", timedelta(minutes=5))
    assert (
        acc.online_duration,
        acc.embedded_afk_duration,
        acc.offline_duration,
        acc.generic_afk_duration,
    ) == before
