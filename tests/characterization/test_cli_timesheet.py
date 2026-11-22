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
        (
            ["--format", "timesheet", "--no-taskwarrior", DAY],
            "cannot be combined with --no-taskwarrior",
        ),
        (["--format", "timesheet", "--app", "Firefox", DAY], "cannot be combined with --app"),
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


def test_timesheet_check_out_follows_the_machine_spans_of_the_selected_sources(monkeypatch, capsys):
    """Tasks end at 12:00 and the AFK data runs to 12:10: check-out follows the selected sources."""
    client = FakeActivityWatchClient(scenario_buckets())
    tasks_out, _code, _err = run_cli(monkeypatch, capsys, ["--format", "timesheet", DAY], client)
    assert "Check-out: 12:00" in tasks_out

    client = FakeActivityWatchClient(scenario_buckets())
    both_out, code, _err = run_cli(
        monkeypatch, capsys, ["--format", "timesheet", "--source", "tasks,afk", DAY], client
    )
    assert code == 0
    assert {call["bucket_id"] for call in client.calls} >= {TASKS, f"aw-watcher-afk_{HOST}"}
    assert "Check-out: 12:10" in both_out
    # The task hours are unchanged: the per-task table still comes from taskwarrior
    assert "Refactor" in both_out


def test_timesheet_tracked_and_untracked_cover_the_wall_clock(monkeypatch, capsys):
    """The golden day has no empty stretch (the Writing task covers 11:30-12:00), so Tracked is the
    whole 09:00-12:10 span and Untracked is 00:00; Tracked plus Untracked is the Wall Clock."""
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, _err = run_cli(
        monkeypatch, capsys, ["--format", "timesheet", "--source", "tasks,afk", DAY], client
    )
    assert code == 0
    assert "Wall Clock: 03:10" in out
    assert "Tracked: 03:10 | Untracked: 00:00" in out


def test_piped_timesheet_is_78_columns_with_no_margin(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, _err = run_cli(monkeypatch, capsys, ["--format", "timesheet", DAY], client)
    assert code == 0
    content = [line for line in out.splitlines() if line]
    assert max(len(line) for line in content) <= 78
    assert content[0] == "=" * 78  # not indented: no terminal, no margin


def test_a_terminal_gets_the_whole_width_with_no_margin(monkeypatch, capsys):
    import sys

    from aw_report.cli import main as cli_main

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    import aw_report.utils.formatting as formatting

    monkeypatch.setattr(formatting, "get_terminal_width", lambda: 120)
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, _err = run_cli(monkeypatch, capsys, ["--format", "timesheet", DAY], client)
    assert code == 0
    rule = next(line for line in out.splitlines() if line and set(line) == {"="})
    assert rule == "=" * 120  # the whole terminal, no margin


def test_timesheet_seconds_shows_hh_mm_ss(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, _err = run_cli(
        monkeypatch,
        capsys,
        ["--format", "timesheet", "--source", "tasks,afk", "--seconds", DAY],
        client,
    )
    assert code == 0
    assert "Wall Clock: 03:10:00" in out
    assert "Tracked: 03:10:00 | Untracked: 00:00:00" in out
    assert "Hours" in out
    assert "00:30:00" in out  # the Writing task row


def test_seconds_without_the_timesheet_format_is_rejected(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    _out, code, err = run_cli(monkeypatch, capsys, ["--seconds", DAY], client)
    assert code == 1
    assert "apply to --format timesheet only" in err


def test_timesheet_nanoseconds_shows_nine_digits(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, _err = run_cli(
        monkeypatch,
        capsys,
        ["--format", "timesheet", "--source", "tasks,afk", "--nanoseconds", DAY],
        client,
    )
    assert code == 0
    assert "Wall Clock: 03:10:00.000000000" in out
    assert "Untracked: 00:00:00.000000000" in out
    assert "Check-in:" in out and "Check-out:" in out
