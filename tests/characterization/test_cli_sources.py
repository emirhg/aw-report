"""Characterization tests for --source: which role buckets are read, and the validation errors.

The fake client records every request, so each test can assert which buckets were queried.
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

AFK = f"aw-watcher-afk_{HOST}"
WINDOW = f"aw-watcher-window_{HOST}"
TASKS = f"aw-watcher-taskwarrior_{HOST}"


def queried(client):
    return {call["bucket_id"] for call in client.calls}


def test_source_tasks_reads_only_the_taskwarrior_bucket(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, _err = run_cli(monkeypatch, capsys, ["--source", "tasks", DAY], client)
    assert code == 0
    assert queried(client) == {TASKS}
    assert "Timeline Report" in out


def test_default_source_reads_all_three_buckets(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    _out, code, _err = run_cli(monkeypatch, capsys, [DAY], client)
    assert code == 0
    assert {AFK, WINDOW, TASKS} <= queried(client)


def test_source_afk_and_window_skip_taskwarrior(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    _out, code, _err = run_cli(monkeypatch, capsys, ["--source", "afk,window", DAY], client)
    assert code == 0
    assert TASKS not in queried(client)
    assert {AFK, WINDOW} <= queried(client)


def test_source_names_are_case_and_space_insensitive(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    _out, code, _err = run_cli(monkeypatch, capsys, ["--source", " Tasks ", DAY], client)
    assert code == 0
    assert queried(client) == {TASKS}


def test_unknown_source_is_an_error(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    _out, code, err = run_cli(monkeypatch, capsys, ["--source", "tmux", DAY], client)
    assert code == 1
    assert "Unknown source(s): tmux" in err
    assert client.calls == []


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--source", "afk", "--project", "Climb", DAY], "--project and --task need the tasks"),
        (["--source", "afk", "--task", "Refactor", DAY], "--project and --task need the tasks"),
        (["--source", "tasks", "--app", "Firefox", DAY], "--app and --detail-level 3"),
        (["--source", "tasks", "--detail-level", "3", DAY], "--app and --detail-level 3"),
    ],
)
def test_combinations_without_their_source_are_rejected(monkeypatch, capsys, argv, message):
    client = FakeActivityWatchClient(scenario_buckets())
    _out, code, err = run_cli(monkeypatch, capsys, argv, client)
    assert code == 1
    assert message in err
    assert client.calls == []


def test_help_names_the_program_aw_report(monkeypatch, capsys):
    client = FakeActivityWatchClient(scenario_buckets())
    out, code, _err = run_cli(monkeypatch, capsys, ["--help"], client)
    assert code == 0
    assert out.startswith("usage: aw-report")


def test_fetch_help_names_the_subcommand(monkeypatch, capsys):
    from tw_report.cli.fetch import _build_parser

    assert _build_parser().prog == "aw-report fetch"
