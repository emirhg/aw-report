"""
`aw-report fetch` subcommand: dump the raw ActivityWatch events of the role buckets as JSON.

Usage:
    aw-report fetch [PERIOD]

PERIOD is a period token (:today, :yesterday, :lastweek, ...), an ISO date, or a
date/datetime range, as accepted by the reports. Boundaries are computed in the
configured timezone and queried from ActivityWatch in UTC.

Output is a JSON array with one entry per role bucket, in the order afk, window, tasks.
Each entry is the array of that bucket's events for the period:

    [
      [ {"id": 1, "timestamp": "2026-11-12T06:00:00+00:00", "duration": 12.5, "data": {...}}, ... ],
      [ ... ],
      [ ... ]
    ]
"""

import argparse
import json
import sys

from aw_report.config import load_user_config, resolve_settings
from aw_report.core.aw_http import ActivityWatchClient
from aw_report.core.buckets import resolve_buckets
from aw_report.core.events import fetch_events
from aw_report.core.period import local_zoneinfo, period_to_utc
from aw_report.exceptions import TwReportError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aw-report fetch",
        description="Print the afk, window and tasks bucket events for a period as JSON.",
    )
    parser.add_argument(
        "period",
        nargs="*",
        default=[":today"],
        help="Period token or date/datetime range (default: :today).",
    )
    return parser


def run_fetch(argv: list[str] | None = None) -> int:
    """Run the fetch subcommand. Returns a process exit code."""
    args = _build_parser().parse_args(argv)
    period = " ".join(args.period) if args.period else ":today"

    try:
        user_config = load_user_config()
        buckets = resolve_buckets(user_config.get("buckets"))
        settings = resolve_settings(user_config=user_config)
        start_utc, end_utc = period_to_utc(
            period,
            day_start_hour=settings.day_start_hour,
            tz=local_zoneinfo(settings.timezone),
        )
        client = ActivityWatchClient("tw-report", host=settings.aw_host, port=str(settings.aw_port))
        payload = [
            [
                event.to_json_dict()
                for event in fetch_events(client, bucket_id, start_utc, end_utc, strict=True)
            ]
            for bucket_id in (buckets.afk, buckets.window, buckets.tasks)
        ]
    except TwReportError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # server unreachable or unexpected response
        print(f"Error: could not query ActivityWatch: {e}", file=sys.stderr)
        return 1

    json.dump(payload, sys.stdout, ensure_ascii=True)
    sys.stdout.write("\n")
    return 0
