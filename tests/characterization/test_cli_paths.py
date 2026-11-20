"""CLI path tests: task lookups, default periods, error reporting and data edge cases.

Drives the real main() through the same deterministic harness as the golden tests.
TaskWarrior lookups are stubbed at subprocess.run so no real `task` binary is called.
"""

import logging
import runpy
import subprocess
import sys
import warnings
from datetime import datetime, timezone

import pytest

from tests.characterization.test_cli_golden import (
    DAY,
    HOST,
    deterministic_environment,  # noqa: F401  (autouse fixture re-exported for this module)
    run_cli,
    scenario_buckets,
)
from tests.support.fake_aw import FakeActivityWatchClient, make_event
from tw_report.cli import main as cli_main

UUID = "6f1c9c0e-2b8a-4c1e-9d3a-111122223333"


def buckets_with_uuid():
    """Scenario data where the 'Refactor' task carries a TaskWarrior uuid."""
    buckets = scenario_buckets()
    tasks = buckets[f"aw-watcher-taskwarrior_{HOST}"]
    for event in tasks:
        if event.data.get("task") == "Refactor":
            event.data["uuid"] = UUID
    return buckets


class _Stub:
    """Stands in for subprocess.run for `task <id> export`."""

    def __init__(self, known):
        self.known = known  # identifier(str) -> uuid
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        identifier = args[1]
        if identifier in self.known:
            payload = [
                {"uuid": self.known[identifier], "project": "Higuera", "description": "Refactor"}
            ]
            return subprocess.CompletedProcess(args, 0, stdout=__import__("json").dumps(payload))
        raise subprocess.CalledProcessError(1, args)


@pytest.fixture
def stub_taskwarrior(monkeypatch):
    stub = _Stub({"7": UUID})
    monkeypatch.setattr(subprocess, "run", stub)
    return stub


def test_numeric_task_value_is_resolved_through_taskwarrior(monkeypatch, capsys, stub_taskwarrior):
    client = FakeActivityWatchClient(buckets_with_uuid())
    out, code, err = run_cli(monkeypatch, capsys, ["--task", "7", DAY], client)
    assert code == 0
    assert ["task", "7", "export"] in stub_taskwarrior.calls


def test_uuid_task_value_filters_by_uuid(monkeypatch, capsys):
    stub = _Stub({UUID: UUID})
    monkeypatch.setattr(subprocess, "run", stub)
    client = FakeActivityWatchClient(buckets_with_uuid())
    out, code, err = run_cli(monkeypatch, capsys, ["--task", UUID, DAY], client)
    assert code == 0
    assert "Refactor" in out


def test_task_id_flag_resolves_uuid_for_filtering(monkeypatch, capsys, stub_taskwarrior):
    client = FakeActivityWatchClient(buckets_with_uuid())
    out, code, err = run_cli(monkeypatch, capsys, ["--task-id", "7", DAY], client)
    assert code == 0
    assert "Refactor" in out


def test_task_id_flag_reports_unknown_task(monkeypatch, capsys, stub_taskwarrior):
    client = FakeActivityWatchClient(buckets_with_uuid())
    out, code, err = run_cli(monkeypatch, capsys, ["--task-id", "999", DAY], client)
    assert code == 1
    assert "Task 999 not found" in err


def test_unknown_numeric_task_value_is_reported(monkeypatch, capsys, stub_taskwarrior):
    client = FakeActivityWatchClient(buckets_with_uuid())
    out, code, err = run_cli(monkeypatch, capsys, ["--task", "999", DAY], client)
    assert code == 1
    assert err.strip() != ""


def test_unknown_numeric_project_value_is_reported(monkeypatch, capsys, stub_taskwarrior):
    client = FakeActivityWatchClient(buckets_with_uuid())
    out, code, err = run_cli(monkeypatch, capsys, ["--project", "999", DAY], client)
    assert code == 1
    assert err.strip() != ""


def test_no_taskwarrior_flag_never_queries_the_taskwarrior_bucket(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, err = run_cli(monkeypatch, capsys, ["--no-taskwarrior", DAY], client)
    assert code == 0
    queried = {call["bucket_id"] for call in client.calls}
    assert f"aw-watcher-taskwarrior_{HOST}" not in queried


@pytest.mark.parametrize(
    "flag, expected_period",
    [("--by-week", ":week"), ("--by-month", ":month"), ("--by-year", ":year")],
)
def test_grouping_without_explicit_period_defaults_to_matching_window(
    monkeypatch, capsys, flag, expected_period
):
    seen = []
    original = cli_main.parse_period

    def spy(period, *args, **kwargs):
        seen.append(period)
        return original(period, *args, **kwargs)

    monkeypatch.setattr(cli_main, "parse_period", spy)
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, err = run_cli(monkeypatch, capsys, [flag], client)
    assert code == 0
    assert seen and seen[0] == expected_period


def test_duplicate_taskwarrior_heartbeats_are_collapsed_with_a_warning(monkeypatch, capsys, caplog):
    # Project-filtered fetches return typed TaskWarriorEvents, whose uuid is readable, so the
    # duplicate heartbeat is collapsed (keeping the longest) and a warning is logged.
    buckets = scenario_buckets()
    tasks_key = f"aw-watcher-taskwarrior_{HOST}"
    base = buckets[tasks_key][0]
    shorter = make_event(base.timestamp, 30, dict(base.data, uuid=UUID))
    longer = make_event(base.timestamp, 90, dict(base.data, uuid=UUID))
    buckets[tasks_key] = [shorter, longer] + buckets[tasks_key][1:]
    client = FakeActivityWatchClient(buckets)
    with caplog.at_level(logging.WARNING, logger="tw_report.cli.main"):
        out, code, err = run_cli(monkeypatch, capsys, ["--project", "Climb", DAY], client)
    assert code == 0
    assert any("Deduplicated 1 duplicate task events" in r.getMessage() for r in caplog.records)


def test_app_filter_matching_nothing_still_renders(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, err = run_cli(monkeypatch, capsys, ["--app", "NoSuchApplication", DAY], client)
    assert code == 0


def test_module_entry_point_runs_main_when_executed_as_script(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    import aw_client

    monkeypatch.setattr(aw_client, "ActivityWatchClient", lambda *a, **k: client)
    monkeypatch.setattr(sys, "argv", ["aw-report", DAY])
    # The module is already imported by this test session, so runpy warns about re-executing it.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*found in sys.modules")
        runpy.run_module("tw_report.cli.main", run_name="__main__")
    assert "Timeline Report" in capsys.readouterr().out


def test_multiple_project_filters_use_the_project_pre_fetch(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, err = run_cli(
        monkeypatch, capsys, ["--project", "Climb", "--project", "Higuera", DAY], client
    )
    assert code == 0
    assert "Climb" in out and "Higuera" in out


def test_multiple_task_filters_use_the_task_pre_fetch(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, err = run_cli(
        monkeypatch, capsys, ["--task", "Task1", "--task", "Refactor", DAY], client
    )
    assert code == 0
    assert "Task1" in out and "Refactor" in out


def test_app_filter_outside_project_time_falls_back_to_project_ranges(monkeypatch, capsys):
    # VSCode windows (09:00-10:00) never overlap Higuera's time (10:45-11:30): the clipped
    # envelope is empty and the project ranges are kept.
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, err = run_cli(
        monkeypatch, capsys, ["--project", "Higuera", "--app", "VSCode", DAY], client
    )
    assert code == 0


def test_app_filter_with_project_and_no_matching_app(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, err = run_cli(
        monkeypatch, capsys, ["--project", "Higuera", "--app", "NoSuchApp", DAY], client
    )
    assert code == 0


def test_empty_afk_bucket_produces_no_break_metrics(monkeypatch, capsys):
    buckets = scenario_buckets()
    buckets[f"aw-watcher-afk_{HOST}"] = []
    client = FakeActivityWatchClient(buckets)
    out, code, err = run_cli(monkeypatch, capsys, [DAY], client)
    assert code == 0


def test_window_without_app_name_is_ignored_by_app_grouping(monkeypatch, capsys):
    buckets = scenario_buckets()
    key = f"aw-watcher-window_{HOST}"
    buckets[key] = buckets[key] + [
        make_event(
            datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
            5,
            {"app": "", "title": "untitled", "class": ""},
        ),
    ]
    client = FakeActivityWatchClient(buckets)
    out, code, err = run_cli(monkeypatch, capsys, ["--app", "VSCode", DAY], client)
    assert code == 0


def test_reversed_duplicate_heartbeats_keep_the_longest_event(monkeypatch, capsys, caplog):
    buckets = scenario_buckets()
    tasks_key = f"aw-watcher-taskwarrior_{HOST}"
    base = buckets[tasks_key][0]
    longer = make_event(base.timestamp, 90, dict(base.data, uuid=UUID))
    shorter = make_event(base.timestamp, 30, dict(base.data, uuid=UUID))
    buckets[tasks_key] = [longer, shorter] + buckets[tasks_key][1:]
    client = FakeActivityWatchClient(buckets)
    with caplog.at_level(logging.WARNING, logger="tw_report.cli.main"):
        out, code, err = run_cli(monkeypatch, capsys, ["--project", "Climb", DAY], client)
    assert code == 0
    assert any("Deduplicated" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def test_matches_any_supports_substring_and_exact_modes():
    assert cli_main._matches_any("Climb Project", ["climb"], exact=False) is True
    assert cli_main._matches_any("Climb Project", ["climb"], exact=True) is False
    assert cli_main._matches_any("Climb", ["CLIMB"], exact=True) is True
    assert cli_main._matches_any("anything", None, exact=False) is False
    assert cli_main._matches_any("anything", [], exact=False) is False


def test_excluded_is_a_case_insensitive_substring_check():
    assert cli_main._excluded("Climb Project", ["project"]) is True
    assert cli_main._excluded("Climb", ["Higuera"]) is False
    assert cli_main._excluded("Climb", None) is False


def test_time_ranges_from_no_events_is_empty():
    assert cli_main._get_time_ranges_from_events([]) == []


def test_time_ranges_merge_overlapping_and_adjacent_events():
    events = [
        make_event(datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc), 60, {}),
        make_event(datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc), 30, {}),  # adjacent
        make_event(datetime(2026, 8, 30, 10, 20, tzinfo=timezone.utc), 60, {}),  # overlapping
        make_event(datetime(2026, 8, 30, 13, 0, tzinfo=timezone.utc), 15, {}),  # separate
    ]
    ranges = cli_main._get_time_ranges_from_events(events)
    assert ranges == [
        (
            datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 30, 11, 20, tzinfo=timezone.utc),
        ),
        (
            datetime(2026, 8, 30, 13, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 30, 13, 15, tzinfo=timezone.utc),
        ),
    ]


def test_app_bounded_ranges_without_matching_windows_keep_task_ranges():
    ranges = [
        (
            datetime(2026, 8, 30, 9, tzinfo=timezone.utc),
            datetime(2026, 8, 30, 10, tzinfo=timezone.utc),
        )
    ]
    assert cli_main._compute_app_bounded_ranges(ranges, []) == ranges
    assert cli_main._compute_app_bounded_ranges(None, []) == []


def test_back_to_back_not_afk_periods_have_no_break_between_them(monkeypatch, capsys):
    buckets = scenario_buckets()
    key = f"aw-watcher-afk_{HOST}"
    # Two not-afk periods that touch exactly: the previous end equals the last start.
    buckets[key] = [
        make_event(datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc), 60, {"status": "not-afk"}),
        make_event(datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc), 60, {"status": "not-afk"}),
    ]
    client = FakeActivityWatchClient(buckets)
    out, code, err = run_cli(monkeypatch, capsys, [DAY], client)
    assert code == 0
    assert "Last Break" not in out


def test_empty_app_name_in_a_slot_category_is_ignored(monkeypatch, capsys):
    buckets = scenario_buckets()
    key = f"aw-watcher-window_{HOST}"
    # Overlaps the Climb/Task1 slot (09:00-10:30): the unnamed app becomes a category entry.
    buckets[key] = buckets[key] + [
        make_event(
            datetime(2026, 8, 30, 9, 30, tzinfo=timezone.utc),
            5,
            {"app": "", "title": "unnamed", "class": ""},
        ),
    ]
    client = FakeActivityWatchClient(buckets)
    out, code, err = run_cli(monkeypatch, capsys, ["--app", "VSCode", DAY], client)
    assert code == 0


def test_app_envelope_keeps_only_the_task_ranges_it_overlaps():
    t = lambda h: datetime(2026, 8, 30, h, tzinfo=timezone.utc)  # noqa: E731
    task_ranges = [(t(9), t(10)), (t(13), t(14))]
    window = make_event(t(9), 10, {"app": "VSCode"})  # 09:00-09:10 overlaps only the first range
    clipped = cli_main._compute_app_bounded_ranges(task_ranges, [window])
    assert clipped == [(t(9), t(9).replace(minute=10))]


def test_app_names_are_collected_from_category_entries_and_empty_names_skipped():
    """--app matches the apps of a slot's categories; empty names and missing apps are skipped."""
    from tests.support.slots import slot
    from tw_report.core.filtering import EventFilter

    cats = [
        {"category": "Coding", "apps": [{"app": "VSCode"}, {"app": ""}, {"app": "VSCode"}]},
        {"category": "Misc"},  # no apps key at all
    ]
    with_apps = slot(0, 10, "not-afk", categories=cats)
    assert EventFilter(app_patterns=["VSCode"]).accepts_slot(with_apps)
    assert not EventFilter(app_patterns=["Firefox"]).accepts_slot(with_apps)

    no_apps = slot(0, 10, "not-afk", categories=None)
    assert not EventFilter(app_patterns=["VSCode"]).accepts_slot(no_apps)


def _write_config(tmp_path, text):
    config_dir = tmp_path / "xdg" / "tw-report"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(text, encoding="utf-8")
    return str(tmp_path / "xdg")


def test_configured_bucket_ids_are_used_and_match_the_default_report(monkeypatch, capsys, tmp_path):
    baseline, _, _ = run_cli(
        monkeypatch, capsys, [DAY], FakeActivityWatchClient(scenario_buckets())
    )
    buckets = scenario_buckets()
    renamed = {
        "aw-watcher-afk_custom": buckets[f"aw-watcher-afk_{HOST}"],
        "aw-watcher-window_custom": buckets[f"aw-watcher-window_{HOST}"],
        "aw-watcher-taskwarrior_custom": buckets[f"aw-watcher-taskwarrior_{HOST}"],
    }
    xdg = _write_config(
        tmp_path,
        '[buckets]\nafk = "aw-watcher-afk_custom"\nwindow = "aw-watcher-window_custom"\n'
        'tasks = "aw-watcher-taskwarrior_custom"\n',
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
    client = FakeActivityWatchClient(renamed)
    out, code, err = run_cli(monkeypatch, capsys, [DAY], client)
    assert code == 0
    assert out == baseline
    assert {c["bucket_id"] for c in client.calls} == set(renamed)


def test_invalid_bucket_config_reports_an_error_and_exits_with_1(monkeypatch, capsys, tmp_path):
    xdg = _write_config(tmp_path, '[buckets]\nwindw = "typo"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, err = run_cli(monkeypatch, capsys, [DAY], client)
    assert code == 1
    assert "Unknown key(s) in [buckets]: windw" in err
    assert client.calls == []
