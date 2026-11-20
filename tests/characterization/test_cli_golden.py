"""Characterization (golden-output) tests for the tw-report CLI.

These drive the real ``aw_report.cli.main.main()`` end to end with a deterministic
in-memory ActivityWatch server and check stdout against checked-in snapshots. They
pin *current* behavior so the upcoming refactor cannot change output silently.

Regenerate snapshots only when a change is intended:
    pytest tests/characterization --update-golden
"""

import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.support.fake_aw import FakeActivityWatchClient, make_event
from aw_report.cli import main as cli_main

GOLDEN_DIR = Path(__file__).parent / "golden"
HOST = "testhost"
DAY = "2026-08-30"  # Explicit day: no dependence on the wall clock


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 30, hour, minute, tzinfo=timezone.utc)


def scenario_buckets():
    """One work day with active, AFK, offline-tagged and untracked time.

    Timeline (UTC):
      09:00-10:30 Climb > Task1 (active, VSCode)
      10:30-10:45 AFK
      10:45-11:30 Higuera > Refactor (active, Firefox)
      11:30-12:00 offline-tagged task (system off, no window events)
      12:00-12:10 AFK only (no task)
    """
    afk = [
        make_event(_utc(9), 90, {"status": "not-afk"}),
        make_event(_utc(10, 30), 15, {"status": "afk"}),
        make_event(_utc(10, 45), 45, {"status": "not-afk"}),
        make_event(_utc(12), 10, {"status": "afk"}),
    ]
    window = [
        make_event(_utc(9), 60, {"app": "VSCode", "title": "tw-report.py", "class": "VSCode"}),
        make_event(_utc(10), 30, {"app": "Firefox", "title": "docs", "class": "Firefox"}),
        make_event(_utc(10, 45), 45, {"app": "Firefox", "title": "PR review", "class": "Firefox"}),
    ]
    tasks = [
        make_event(
            _utc(9),
            90,
            {
                "project": "Climb",
                "task": "Task1",
                "label": "Task1",
                "tags": ["work"],
                "title": "Climb > Task1",
            },
        ),
        make_event(
            _utc(10, 45),
            45,
            {
                "project": "Higuera",
                "task": "Refactor",
                "label": "Refactor",
                "tags": ["work"],
                "title": "Higuera > Refactor",
            },
        ),
        make_event(
            _utc(11, 30),
            30,
            {
                "project": "Climb",
                "task": "Writing",
                "label": "Writing",
                "tags": ["offline"],
                "title": "Climb > Writing",
            },
        ),
    ]
    return {
        f"aw-watcher-afk_{HOST}": afk,
        f"aw-watcher-window_{HOST}": window,
        f"aw-watcher-taskwarrior_{HOST}": tasks,
    }


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch, tmp_path):
    """Pin timezone, hostname, config location and profiling so output is reproducible."""
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setattr(time, "tzname", ("UTC", "UTC"), raising=False)
    if hasattr(time, "tzset"):
        time.tzset()
    monkeypatch.setattr(platform, "node", lambda: HOST)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))  # no user config file
    monkeypatch.delenv("TW_REPORT_DEBUG", raising=False)
    monkeypatch.delenv("TW_REPORT_PROFILE", raising=False)
    yield
    if hasattr(time, "tzset"):
        os.environ.pop("TZ", None)
        time.tzset()


def run_cli(monkeypatch, capsys, argv, client):
    """Run main() with ``argv`` against ``client``.

    Returns (stdout, exit_code, stderr).
    """
    monkeypatch.setattr(cli_main, "ActivityWatchClient", lambda *a, **k: client)
    monkeypatch.setattr(sys, "argv", ["aw-report", *argv])
    # The console script is `sys.exit(main())`, so the return value is the exit status.
    exit_code = 0
    try:
        result = cli_main.main()
        exit_code = result if isinstance(result, int) else 0
    except SystemExit as exc:  # argparse errors exit directly
        exit_code = exc.code if isinstance(exc.code, int) else 1
    captured = capsys.readouterr()
    return captured.out, exit_code, captured.err


def assert_golden(name, actual, request):
    path = GOLDEN_DIR / f"{name}.txt"
    if request.config.getoption("--update-golden"):
        path.write_text(actual, encoding="utf-8")
        return
    assert path.exists(), f"missing golden file {path}; run with --update-golden"
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, f"output for {name} changed (see {path})"


CASES = [
    ("timeline_day", [DAY]),
    ("by_project_day", ["--by-project", DAY]),
    ("by_day_period", ["--by-day", DAY]),
    ("by_week_period", ["--by-week", DAY]),
    ("by_month_period", ["--by-month", DAY]),
    ("by_year_period", ["--by-year", DAY]),
    ("consolidate_flag", ["--consolidate", DAY]),
    ("detail_level_3", ["--detail-level", "3", DAY]),
    ("detail_level_5", ["--detail-level", "5", DAY]),
    ("project_filter", ["--project", "Climb", DAY]),
    ("task_filter", ["--task", "Refactor", DAY]),
    ("exclude_project", ["--exclude-project", "Climb", DAY]),
    ("exclude_task", ["--exclude-task", "Refactor", DAY]),
    ("exclude_non_project", ["--exclude-non-project", DAY]),
    ("exclude_offline", ["--exclude-offline", DAY]),
    ("exclude_afk", ["--exclude-afk", DAY]),
    ("exclude_online", ["--exclude-online", DAY]),
    ("include_afk", ["--include-afk", DAY]),
    ("app_filter", ["--app", "Firefox", DAY]),
    ("exclude_app", ["--exclude-app", "Firefox", DAY]),
    ("sort_by_duration", ["--by-project", "--sort-by-duration", DAY]),
    ("sort_alphabetically", ["--by-project", "--sort-alphabetically", DAY]),
    ("no_productivity", ["--no-productivity", DAY]),
    ("day_start_hour_6", ["--day-start-hour", "6", DAY]),
    ("app_and_project", ["--app", "Firefox", "--project", "Higuera", DAY]),
    ("app_and_task_by_project", ["--app", "VSCode", "--by-project", DAY]),
    ("app_and_task_by_week", ["--app", "VSCode", "--by-week", DAY]),
    ("project_and_task_by_month", ["--project", "Climb", "--task", "Task1", "--by-month", DAY]),
    ("exclude_app_by_year", ["--exclude-app", "VSCode", "--by-year", DAY]),
]


@pytest.mark.parametrize("name,argv", CASES, ids=[c[0] for c in CASES])
def test_golden_output(name, argv, monkeypatch, capsys, request):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, _err = run_cli(monkeypatch, capsys, argv, client)
    assert code == 0
    assert_golden(name, out, request)


def test_unreachable_server_degrades_without_crashing(monkeypatch, capsys):
    """events.get_events swallows connection errors; the report renders an empty period."""
    client = FakeActivityWatchClient(fail=True)
    out, code, _err = run_cli(monkeypatch, capsys, [DAY], client)
    assert code == 0
    assert "No activity found for the specified period." in out
    assert "Total Time                                      00:00:00" in out


def test_project_flag_swallowing_period_token_is_rejected(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, err = run_cli(monkeypatch, capsys, ["--project", ":week"], client)
    assert code == 1
    assert "--project requires an argument" in err


def test_unknown_task_id_reports_not_found(monkeypatch, capsys):
    import subprocess

    def fake_run(*a, **k):
        raise subprocess.CalledProcessError(1, "task")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, err = run_cli(monkeypatch, capsys, ["--task-id", "99999", DAY], client)
    assert code == 1
    assert "Task 99999 not found" in err
