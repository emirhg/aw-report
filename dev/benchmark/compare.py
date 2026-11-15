#!/usr/bin/env python3
"""Benchmark: shell workflow versus its Python replica, same period and same conditions.

Both pipelines run end to end: query ActivityWatch (taskwarrior bucket only), build the JSON,
and format it with samples/timesheet_report.py --format csv. Each pipeline runs one warm-up and
then REPEATS measured runs (default 10); the min, max and average wall time are reported, and the
CSV output of both is compared byte for byte.

Usage:
    dev/benchmark/compare.py [MODE] [REPEATS]     (defaults: :lastweek 10)

Caveat: modes that include today (:today, :week, :month, :year) read live data. ActivityWatch keeps
recording between the two pipelines, so their CSV can differ for that reason alone. Use a
completed period (:lastweek, :lastmonth) for an exact output comparison.
"""

import hashlib
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
PY_QUERY = ROOT / "dev" / "benchmark" / "aw_task_report.py"
REPORT = SAMPLES / "timesheet_report.py"


def shell_pipeline(mode: str, query_only: bool) -> list[str]:
    query = f"source {SAMPLES}/func-aw-task-report.sh && aw-task-report {mode}"
    if query_only:
        return ["bash", "-c", f"{query} > /dev/null"]
    return ["bash", "-c", f"{query} | python3 {REPORT} --format csv"]


def python_pipeline(mode: str, query_only: bool) -> list[str]:
    if query_only:
        return ["bash", "-c", f"python3 {PY_QUERY} {mode} > /dev/null"]
    return ["bash", "-c", f"python3 {PY_QUERY} {mode} | python3 {REPORT} --format csv"]


def run(cmd: list[str]) -> tuple[float, bytes]:
    start = time.perf_counter()
    done = subprocess.run(cmd, capture_output=True, check=False)
    elapsed = time.perf_counter() - start
    if done.returncode != 0:
        raise SystemExit(f"command failed ({done.returncode}): {' '.join(cmd)}\n{done.stderr.decode()}")
    return elapsed, done.stdout


def measure(build, mode: str, repeats: int, query_only: bool) -> tuple[list[float], bytes]:
    cmd = build(mode, query_only)
    run(cmd)  # warm-up, not measured
    times, output = [], b""
    for _ in range(repeats):
        elapsed, output = run(cmd)
        times.append(elapsed)
    return times, output


def report_line(label: str, times: list[float]) -> str:
    return (
        f"{label:<22} avg {statistics.mean(times):6.3f} s   "
        f"min {min(times):6.3f} s   max {max(times):6.3f} s   (n={len(times)})"
    )


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ":lastweek"
    repeats = int(argv[2]) if len(argv) > 2 else 10
    if repeats < 10:
        raise SystemExit("use at least 10 measured runs")
    for tool in ("bash", "python3", "jq", "curl"):
        if shutil.which(tool) is None:
            raise SystemExit(f"missing tool: {tool}")

    print(f"mode {mode}, {repeats} measured runs after one warm-up\n")
    results = {}
    for label, build in (("shell (jq + bash)", shell_pipeline), ("python (stdlib)", python_pipeline)):
        query_times, _ = measure(build, mode, repeats, query_only=True)
        full_times, csv_output = measure(build, mode, repeats, query_only=False)
        results[label] = (query_times, full_times, csv_output)

    print("query only (JSON to /dev/null)")
    for label, (query_times, _, _) in results.items():
        print("  " + report_line(label, query_times))
    print("\nend to end (query + report + CSV)")
    for label, (_, full_times, _) in results.items():
        print("  " + report_line(label, full_times))

    digests = {label: hashlib.sha256(out).hexdigest()[:16] for label, (_, _, out) in results.items()}
    same = len(set(digests.values())) == 1
    print("\nCSV output identical: " + ("yes" if same else "NO") + "  " + str(digests))
    if not same:
        print("If the mode includes today, live data may explain it; re-run with a completed period.")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
