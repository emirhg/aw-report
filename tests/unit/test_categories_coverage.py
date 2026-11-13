"""Coverage tests for category rule loading, compilation, matching and aggregation."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from tw_report.core.aw_events import WindowEvent
from tw_report.core.categories import (
    build_categories_from_window_events,
    categorize_event,
    compile_category_rules,
    get_category_score,
    load_categories,
)
from tw_report.exceptions import CategoryValidationError, ConfigParsingError

UTC = timezone.utc
T0 = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def window(start_min, dur_min, app="VSCode", title="tw-report.py"):
    return WindowEvent(
        timestamp=at(start_min),
        duration=timedelta(minutes=dur_min),
        data={"app": app, "title": title},
    )


# ---------------------------------------------------------------------------
# load_categories
# ---------------------------------------------------------------------------


def test_load_categories_reads_classes_list(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"classes": [{"name": ["Work"]}]}), encoding="utf-8")
    assert load_categories(str(path)) == [{"name": ["Work"]}]


def test_load_categories_without_classes_key_is_empty(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    assert load_categories(str(path)) == []


def test_load_categories_malformed_json_raises(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigParsingError, match="Malformed JSON"):
        load_categories(str(path))


def test_load_categories_unreadable_path_raises(tmp_path):
    # A directory cannot be opened as a file: OSError -> ConfigParsingError.
    with pytest.raises(ConfigParsingError, match="Cannot read categories file"):
        load_categories(str(tmp_path))


# ---------------------------------------------------------------------------
# compile_category_rules
# ---------------------------------------------------------------------------


def test_compile_empty_rules_returns_empty_structures():
    assert compile_category_rules([]) == ([], {})


def test_compile_skips_rules_without_name_for_score_map():
    rules = [{"name": [], "rule": {"type": "none"}, "data": {"score": 5}}]
    compiled, scores = compile_category_rules(rules)
    assert compiled == []
    assert scores == {}


def test_compile_rule_with_empty_name_is_kept_but_never_matches():
    rules = [{"name": [], "rule": {"type": "regex", "regex": "vscode"}, "data": {"score": 3}}]
    compiled, _ = compile_category_rules(rules)
    assert len(compiled) == 1
    assert compiled[0][0] == []


def test_compile_parent_score_is_inherited_by_child_rules_listed_first():
    rules = [
        {"name": ["Work", "Coding"], "rule": {"type": "regex", "regex": "vscode"},
         "data": {"score": 10}},
        {"name": ["Work"], "rule": {"type": "regex", "regex": "office"}, "data": {"score": 4}},
    ]
    _, scores = compile_category_rules(rules)
    assert scores["Work > Coding"] == 10.0
    assert scores["Coding"] == 10.0
    assert scores["Work"] == 4.0


def test_compile_parent_declared_with_type_none_sets_score_directly():
    rules = [
        {"name": ["Work"], "rule": {"type": "none"}, "data": {"score": 2}},
        {"name": ["Work", "Coding"], "rule": {"type": "regex", "regex": "code"},
         "data": {"score": 10}},
    ]
    _, scores = compile_category_rules(rules)
    assert scores["Work"] == 2.0


def test_compile_rule_without_score_is_matched_with_zero_and_not_scored():
    rules = [{"name": ["Misc"], "rule": {"type": "regex", "regex": "x"}, "data": {}}]
    compiled, scores = compile_category_rules(rules)
    assert compiled[0][2] == 0.0
    assert "Misc" not in scores


def test_compile_case_sensitive_when_ignore_case_disabled():
    rules = [{"name": ["Exact"], "rule": {"type": "regex", "regex": "VSCode",
              "ignore_case": False}, "data": {"score": 1}}]
    compiled, _ = compile_category_rules(rules)
    assert compiled[0][1].search("vscode") is None
    assert compiled[0][1].search("VSCode") is not None


def test_compile_invalid_regex_raises_category_validation_error():
    rules = [{"name": ["Bad"], "rule": {"type": "regex", "regex": "("}, "data": {"score": 1}}]
    with pytest.raises(CategoryValidationError, match="Invalid regex"):
        compile_category_rules(rules)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known bug: the explicit-score pass converts score with float() outside any try, so a "
        "non-numeric score leaks a raw ValueError instead of CategoryValidationError (the "
        "validation clause that would have caught it was dead and has been removed). Fix in "
        "Phase 3 by validating in the first pass."
    ),
)
def test_compile_non_numeric_score_raises_category_validation_error():
    rules = [{"name": ["Bad"], "rule": {"type": "regex", "regex": "x"},
              "data": {"score": "high"}}]
    with pytest.raises(CategoryValidationError, match="Invalid category rule structure"):
        compile_category_rules(rules)


# ---------------------------------------------------------------------------
# get_category_score and categorize_event
# ---------------------------------------------------------------------------


def test_get_category_score_defaults_to_zero():
    assert get_category_score("Unknown", {"Work": 5.0}) == 0.0
    assert get_category_score("Work", {"Work": 5.0}) == 5.0


def test_categorize_event_prefers_most_specific_match():
    rules = [
        {"name": ["Work"], "rule": {"type": "regex", "regex": "tw-report"}, "data": {"score": 1}},
        {"name": ["Work", "Coding"], "rule": {"type": "regex", "regex": "VSCode"},
         "data": {"score": 10}},
    ]
    compiled, _ = compile_category_rules(rules)
    event = window(0, 5, app="VSCode", title="tw-report.py")
    categorize_event(event, compiled)
    assert event.data["$category"] == ["Work > Coding"]


def test_categorize_event_matches_app_or_title_alone():
    rules = [
        {"name": ["Browsing"], "rule": {"type": "regex", "regex": "^Firefox$"}, "data": {"score": 0}},
        {"name": ["Docs"], "rule": {"type": "regex", "regex": "readme"}, "data": {"score": 0}},
    ]
    compiled, _ = compile_category_rules(rules)
    by_app = window(0, 5, app="Firefox", title="unrelated")
    categorize_event(by_app, compiled)
    assert by_app.data["$category"] == ["Browsing"]
    by_title = window(0, 5, app="Terminal", title="README.md")
    categorize_event(by_title, compiled)
    assert by_title.data["$category"] == ["Docs"]


def test_categorize_event_ignores_rules_with_no_name_and_defaults_to_uncategorized():
    rules = [{"name": [], "rule": {"type": "regex", "regex": "VSCode"}, "data": {"score": 1}}]
    compiled, _ = compile_category_rules(rules)
    event = window(0, 5)
    categorize_event(event, compiled)
    assert event.data["$category"] == ["Uncategorized"]


# ---------------------------------------------------------------------------
# build_categories_from_window_events
# ---------------------------------------------------------------------------


def test_build_categories_empty_input_returns_empty_list():
    assert build_categories_from_window_events([], at(0), at(60)) == []


def test_build_categories_ignores_events_outside_the_period():
    outside = window(120, 10)
    assert build_categories_from_window_events([outside], at(0), at(60)) == []


def _categorized(events):
    """Production categorizes window events before aggregating; mirror that here."""
    for event in events:
        categorize_event(event, [])
    return events


def test_build_categories_clips_durations_to_the_period_and_sums_per_app():
    events = _categorized([
        window(-10, 30, app="VSCode", title="a.py"),   # 20 minutes inside [0, 60)
        window(50, 30, app="VSCode", title="b.py"),    # 10 minutes inside
    ])
    cats = build_categories_from_window_events(events, at(0), at(60))
    assert len(cats) == 1
    category = cats[0]
    assert category["category"] == "Uncategorized"
    assert category["duration"] == timedelta(minutes=30)
    assert category["start"] == at(0)
    assert category["end"] == at(60)
    apps = category["apps"]
    assert len(apps) == 1
    assert apps[0]["app"] == "VSCode"
    titles = {t["title"]: t["duration"] for t in apps[0]["titles"]}
    assert titles == {"a.py": timedelta(minutes=20), "b.py": timedelta(minutes=10)}


def test_build_categories_widens_ranges_for_later_and_earlier_occurrences():
    # Same category/app/title appears three times at different offsets; the aggregated
    # ranges must span the earliest start and the latest end.
    events = _categorized([
        window(20, 5, app="Firefox", title="docs"),
        window(10, 5, app="Firefox", title="docs"),    # earlier start
        window(40, 5, app="Firefox", title="docs"),    # later end
    ])
    cats = build_categories_from_window_events(events, at(0), at(60))
    app = cats[0]["apps"][0]
    assert app["start"] == at(10)
    assert app["end"] == at(45)
    title = app["titles"][0]
    assert title["start"] == at(10)
    assert title["end"] == at(45)
    assert title["duration"] == timedelta(minutes=15)
    assert len(title["events"]) == 3


def test_build_categories_groups_apps_under_categories_per_rule():
    rules = [
        {"name": ["Work", "Coding"], "rule": {"type": "regex", "regex": "VSCode"},
         "data": {"score": 10}},
    ]
    compiled, _ = compile_category_rules(rules)
    events = [window(0, 10, app="VSCode"), window(10, 10, app="Firefox", title="news")]
    for event in events:
        categorize_event(event, compiled)
    cats = build_categories_from_window_events(events, at(0), at(60))
    names = {c["category"] for c in cats}
    assert names == {"Work > Coding", "Uncategorized"}
    for cat in cats:
        assert cat["duration"] == timedelta(minutes=10)


def test_later_starting_app_does_not_move_category_start_back():
    events = _categorized([
        window(0, 5, app="VSCode", title="a.py"),        # earliest, category start
        window(30, 5, app="Firefox", title="docs"),      # later app in same category
    ])
    cats = build_categories_from_window_events(events, at(0), at(60))
    assert cats[0]["start"] == at(0)
    assert cats[0]["end"] == at(35)


def test_later_starting_title_does_not_move_app_start_back():
    events = _categorized([
        window(0, 5, app="VSCode", title="a.py"),        # earliest, app start
        window(30, 5, app="VSCode", title="b.py"),       # later title, same app
    ])
    cats = build_categories_from_window_events(events, at(0), at(60))
    app = cats[0]["apps"][0]
    assert app["start"] == at(0)
    assert app["end"] == at(35)


def test_later_app_ending_earlier_does_not_shrink_category_end():
    events = _categorized([
        window(0, 40, app="VSCode", title="a.py"),       # ends at 40
        window(30, 2, app="Firefox", title="docs"),      # starts later, ends at 32
    ])
    cats = build_categories_from_window_events(events, at(0), at(60))
    assert cats[0]["end"] == at(40)


def test_later_title_ending_earlier_does_not_shrink_app_end():
    events = _categorized([
        window(0, 30, app="VSCode", title="a.py"),       # ends at 30
        window(5, 5, app="VSCode", title="b.py"),        # starts later, ends at 10
    ])
    cats = build_categories_from_window_events(events, at(0), at(60))
    assert cats[0]["apps"][0]["end"] == at(30)
