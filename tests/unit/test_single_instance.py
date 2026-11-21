"""Unit tests for the single-instance lock (aw_report.core.single_instance).

POSIX record locks belong to a process, so the "second run" is a real child process.
"""

import subprocess
import sys

import pytest

import aw_report.cli.main as cli_main
from aw_report.core.single_instance import (
    AlreadyRunningError,
    _default_lock_dir,
    single_instance,
)

HOLDER = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "from aw_report.core.single_instance import single_instance\n"
    "with single_instance('aw-report', Path(sys.argv[1])):\n"
    "    print('locked', flush=True)\n"
    "    time.sleep(30)\n"
)


def _stop(child):
    child.kill()
    child.wait()
    child.stdout.close()


def _hold_lock_in_child(lock_dir):
    child = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(lock_dir)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout.readline().strip() == "locked"
    return child


def test_second_process_is_refused(tmp_path):
    child = _hold_lock_in_child(tmp_path)
    try:
        with pytest.raises(AlreadyRunningError), single_instance("aw-report", tmp_path):
            pass
    finally:
        _stop(child)


def test_lock_is_free_again_after_the_holder_exits(tmp_path):
    child = _hold_lock_in_child(tmp_path)
    _stop(child)

    with single_instance("aw-report", tmp_path):
        pass


def test_lock_is_released_when_the_block_ends(tmp_path):
    with single_instance("aw-report", tmp_path):
        pass
    with single_instance("aw-report", tmp_path):
        pass


def test_lock_directory_is_created(tmp_path):
    target = tmp_path / "nested" / "locks"
    with single_instance("aw-report", target):
        assert (target / "aw-report").exists()


def test_default_lock_dir_follows_xdg_cache_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert _default_lock_dir() == tmp_path / "aw-report" / "locks"


def test_default_lock_dir_falls_back_to_home_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _default_lock_dir() == tmp_path / ".cache" / "aw-report" / "locks"


def test_main_refuses_a_second_run_with_exit_code_1(monkeypatch, capsys):
    class _Held:
        def __enter__(self):
            raise AlreadyRunningError("aw-report")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli_main, "single_instance", lambda name: _Held())
    monkeypatch.setattr(sys, "argv", ["aw-report", ":lastweek"])

    assert cli_main.main() == 1
    assert "Another instance is already running, quitting." in capsys.readouterr().err
