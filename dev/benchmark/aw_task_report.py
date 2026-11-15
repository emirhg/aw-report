#!/usr/bin/env python3
"""Python replica of samples/func-aw-task-report.sh (the shell workflow).

Queries only the taskwarrior bucket through the ActivityWatch query API, one time period per
day, and prints the same JSON shape as the shell tool, so its output can be piped into
samples/timesheet_report.py unchanged.

Usage:
    aw_task_report.py [:today|:yesterday|:week|:lastweek|:month|:lastmonth]

Standard library only, so the comparison with the shell tool measures the workflow, not packages.
"""

import json
import math
import sys
import urllib.request
from calendar import timegm
from datetime import date, datetime, timedelta

SERVER = "http://localhost:5600/api/0/query/"
QUERY = [
    'task_events = query_bucket(find_bucket("aw-watcher-taskwarrior"));',
    "RETURN = sort_by_timestamp(task_events);",
]
MODES = (":today", ":yesterday", ":week", ":lastweek", ":month", ":lastmonth")


def day_range(mode: str, today: date) -> tuple[date, int]:
    """First day and number of days of a mode, matching the shell tool."""
    if mode == ":today":
        return today, 1
    if mode == ":yesterday":
        return today - timedelta(days=1), 1
    if mode == ":week":
        return today - timedelta(days=today.weekday()), 7
    if mode == ":lastweek":
        return today - timedelta(days=today.weekday() + 7), 7
    if mode == ":month":
        return today.replace(day=1), today.day
    first_this = today.replace(day=1)
    first_last = (first_this - timedelta(days=1)).replace(day=1)
    return first_last, (first_this - first_last).days


def local_midnight_iso(d: date) -> str:
    """Local midnight of ``d`` with its UTC offset, e.g. 2026-11-02T00:00:00-06:00."""
    return datetime(d.year, d.month, d.day).astimezone().isoformat(timespec="seconds")


def jq_round2(hours_times_100: float) -> float | int:
    """jq's round (half away from zero) followed by /100, as the shell tool applies it.

    jq prints whole numbers without a fraction (24, not 24.0), so integral results are ints.
    """
    rounded = math.floor(abs(hours_times_100) + 0.5) * (1 if hours_times_100 >= 0 else -1) / 100
    return int(rounded) if float(rounded).is_integer() else rounded


def epoch(timestamp: str) -> int:
    """Epoch seconds of the UTC clock time of an ActivityWatch timestamp (fraction dropped)."""
    text = timestamp.split(".")[0].split("+")[0].rstrip("Z")
    return timegm(datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").timetuple())


def summarise_period(period: list[dict]) -> dict:
    total_seconds = sum(e["duration"] for e in period)
    if not period:
        return {
            "period_total_hours": 0,
            "wall_clock_hours": 0,
            "checkin_time": None,
            "checkout_time": None,
            "tasks": [],
        }
    start = epoch(period[0]["timestamp"])
    end = epoch(period[-1]["timestamp"]) + period[-1]["duration"]
    groups: dict = {}
    for event in period:
        title = event["data"].get("title")
        groups.setdefault(title, []).append(event)
    tasks = []
    for title in sorted(groups, key=lambda t: (t is not None, t or "")):
        members = groups[title]
        tasks.append(
            {
                "title": title,
                "project": members[0]["data"].get("project"),
                "total_duration": jq_round2(sum(e["duration"] for e in members) / 3600 * 100),
                "count": len(members),
            }
        )
    return {
        "period_total_hours": jq_round2(total_seconds / 3600 * 100),
        "wall_clock_hours": jq_round2((end - start) / 3600 * 100),
        "checkin_time": start,
        "checkout_time": end,
        "tasks": tasks,
    }


def build_report(mode: str, today: date) -> dict:
    first, count = day_range(mode, today)
    days = [first + timedelta(days=i) for i in range(count)]
    timeperiods = [
        f"{local_midnight_iso(d)}/{local_midnight_iso(d + timedelta(days=1))}" for d in days
    ]
    body = json.dumps({"query": QUERY, "timeperiods": timeperiods}).encode()
    request = urllib.request.Request(
        SERVER,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        results = json.loads(response.read())
    if not isinstance(results, list):
        return {"error": "ActivityWatch API returned a non-array response", "raw_response": results}

    periods = []
    for timeperiod, period in zip(timeperiods, results):
        summary = summarise_period(period if isinstance(period, list) else [])
        periods.append({"timeperiod": timeperiod, **summary})
    grand_total = jq_round2(sum(p["period_total_hours"] for p in periods) * 100)
    return {"periods": periods, "grand_total_hours": grand_total}


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ":today"
    if mode not in MODES:
        print(
            f"Unknown mode: {mode}. Use :today, :yesterday, :week, :lastweek, :month, or :lastmonth",
            file=sys.stderr,
        )
        return 1
    report = build_report(mode, date.today())
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
