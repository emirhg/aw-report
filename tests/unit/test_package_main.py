"""``python -m aw_report`` runs the same CLI as the ``aw-report`` command."""

import runpy
import sys

import pytest


def test_python_dash_m_runs_the_cli(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["aw-report", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("aw_report", run_name="__main__")
    assert exit_info.value.code == 0
    assert "usage: aw-report" in capsys.readouterr().out
