"""Unit tests for opt-in stage profiling and debug output setup."""

import sys
from datetime import datetime, timedelta, timezone

from tw_report.utils import profiling
from tw_report.utils.formatting import to_local_time
from tw_report.utils.profiling import StageProfiler, enable_unbuffered_output


class _Clock:
    def __init__(self, *values):
        self._values = list(values)

    def __call__(self):
        return self._values.pop(0)


def test_disabled_profiler_never_touches_the_file(tmp_path):
    log = tmp_path / "profile.log"
    profiler = StageProfiler(enabled=False, log_path=str(log), clock=_Clock(0.0))
    profiler.checkpoint("anything")
    assert not log.exists()
    assert profiler.enabled is False


def test_enabled_profiler_truncates_then_appends(tmp_path):
    log = tmp_path / "profile.log"
    # Stale content from an earlier run must be replaced on the first checkpoint.
    log.write_text("stale\n", encoding="utf-8")
    profiler = StageProfiler(enabled=True, log_path=str(log), clock=_Clock(10.0, 11.5, 13.25))
    profiler.checkpoint("first")
    profiler.checkpoint("second")
    # creation consumes 10.0; checkpoints read 11.5 and 13.25
    assert log.read_text(encoding="utf-8") == ("[   1.50s] first\n[   3.25s] second\n")


def test_checkpoint_elapsed_is_relative_to_creation(tmp_path):
    log = tmp_path / "profile.log"
    # creation at t=100, checkpoint at t=102.5
    profiler = StageProfiler(enabled=True, log_path=str(log), clock=_Clock(100.0, 102.5))
    profiler.checkpoint("stage")
    assert log.read_text(encoding="utf-8") == "[   2.50s] stage\n"


def test_from_env_reads_profile_variable():
    assert StageProfiler.from_env({"TW_REPORT_PROFILE": "1"}).enabled is True
    assert StageProfiler.from_env({}).enabled is False
    assert StageProfiler.from_env({"TW_REPORT_PROFILE": ""}).enabled is False


def test_default_log_path_is_the_documented_location():
    assert profiling.PROFILE_LOG_PATH == "/tmp/tw_report_profile.log"


def test_unbuffered_output_only_when_debug_set(monkeypatch):
    original_out, original_err = sys.stdout, sys.stderr
    calls = []

    def fake_fdopen(fd, mode, buffering):
        calls.append((fd, mode, buffering))
        return original_out

    monkeypatch.setattr(profiling.os, "fdopen", fake_fdopen)
    try:
        enable_unbuffered_output({})
        assert calls == []
        enable_unbuffered_output({"TW_REPORT_DEBUG": "1"})
        assert [c[1:] for c in calls] == [("w", 1), ("w", 1)]
    finally:
        sys.stdout, sys.stderr = original_out, original_err


def test_to_local_time_treats_naive_as_utc_and_converts():
    naive = datetime(2026, 8, 30, 12, 0)
    converted = to_local_time(naive)
    assert converted.tzinfo is not None
    assert converted == naive.replace(tzinfo=timezone.utc)


def test_to_local_time_passes_through_non_utc_aware_datetimes():
    other = timezone(timedelta(hours=-6))
    aware = datetime(2026, 8, 30, 12, 0, tzinfo=other)
    assert to_local_time(aware) is aware
