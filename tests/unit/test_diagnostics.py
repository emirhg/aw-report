"""Unit tests for the stderr warning helper (aw_report.utils.diagnostics)."""

from aw_report.utils.diagnostics import warn


def test_warn_writes_one_plain_line_to_stderr(capsys):
    warn("Categories file not found at 'x', using no rules")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Categories file not found at 'x', using no rules\n"
