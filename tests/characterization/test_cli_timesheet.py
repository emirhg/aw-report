"""Characterization tests for --format timesheet: task-only reads, the text report, and the
combinations it rejects.
"""

import pytest

from tests.characterization.test_cli_golden import (  # noqa: F401  (autouse fixture re-exported)
    DAY,
    HOST,
    deterministic_environment,
    run_cli,
    scenario_buckets,
)
from tests.support.fake_aw import FakeActivityWatchClient

TASKS = f"aw-watcher-taskwarrior_{HOST}"


def test_timesheet_reads_only_taskwarrior_and_prints_the_text_layout(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, _err = run_cli(monkeypatch, capsys, ["--format", "timesheet", DAY], client)
    assert code == 0
    assert {call["bucket_id"] for call in client.calls} == {TASKS}
    assert "TIMESHEET DAILY ACTIVITY REPORT" in out
    assert "GRAND TOTALS" in out
    assert "Refactor" in out  # task titles come from the taskwarrior events


def test_timesheet_by_week_labels_the_period(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, _err = run_cli(
        monkeypatch, capsys, ["--format", "timesheet", "--by-week", DAY], client
    )
    assert code == 0
    assert " - " in out  # a week label spans its first and last day
    assert "active weeks" in out


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--format", "timesheet", "--by-project", DAY], "cannot be combined with --by-project"),
        (["--format", "timesheet", "--no-taskwarrior", DAY], "cannot be combined with --no-taskwarrior"),
        (["--format", "timesheet", "--app", "Firefox", DAY], "cannot be combined with --app"),
        (["--format", "timesheet", "--source", "afk", DAY], "reads the tasks source only"),
    ],
)
def test_timesheet_rejects_combinations_it_cannot_honour(monkeypatch, capsys, argv, message):
    client = FakeActivityWatchClient(scenario_buckets())
    _out, code, err = run_cli(monkeypatch, capsys, argv, client)
    assert code == 1
    assert message in err
    assert client.calls == []


def test_unknown_format_is_an_argparse_error(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    _out, code, err = run_cli(monkeypatch, capsys, ["--format", "csv", DAY], client)
    assert code == 2
    assert "invalid choice" in err
