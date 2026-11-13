"""
Period specification parsing for tw-report.

Converts human-readable period strings (:today, :week, ISO dates, etc.) into
start/end datetime tuples for querying activity data.
"""

import os
import sys
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

PERIOD_TOKENS = (
    ":today",
    ":yesterday",
    ":week",
    ":lastweek",
    ":month",
    ":lastmonth",
    ":year",
    ":lastyear",
    ":all",
)


def local_zoneinfo(name: str | None = None) -> tzinfo:
    """Resolve the timezone used to turn period tokens into instants.

    Order: explicit ``name`` (IANA, e.g. "America/Mexico_City") -> $TZ -> system zone.
    Raises ValueError for an unknown explicit name.
    """
    if name:
        try:
            return ZoneInfo(name)
        except Exception as e:
            raise ValueError(f"Unknown timezone '{name}'") from e
    env_tz = os.environ.get("TZ")
    if env_tz:
        try:
            return ZoneInfo(env_tz)
        except Exception:
            pass
    system_zone = datetime.now().astimezone().tzinfo
    assert system_zone is not None  # astimezone() always attaches a fixed-offset zone
    return system_zone


def logical_date(dt: datetime, day_start_hour: int) -> date:
    """Get the logical day a datetime belongs to when days start at day_start_hour.

    Handles both naive and timezone-aware datetimes correctly by normalizing
    to local timezone before applying day_start_hour offset.

    Args:
        dt: Datetime to get logical date for (naive or tz-aware)
        day_start_hour: Hour when logical day starts (0-23)

    Returns:
        Date object representing the logical day

    BUG FIX (2026-08-31):
    ====================
    Previously, this function would give different logical dates for the same
    instant in time if represented in different timezones. Example:
      - 2026-09-01 04:00 UTC → logical date 2026-09-01
      - 2026-08-31 22:00-06:00 (same instant) → logical date 2026-08-31

    Fix: Convert timezone-aware datetimes to local timezone first.
    This ensures the same instant always gets the same logical date.
    """
    # If timezone-aware, convert to local timezone to normalize
    if dt.tzinfo is not None:
        dt = dt.astimezone()

    return (dt - timedelta(hours=day_start_hour)).date()


def day_boundary(d: date, day_start_hour: int, tzinfo) -> datetime:
    """Get the datetime marking the start of a logical day."""
    return datetime.combine(d, time(hour=day_start_hour), tzinfo=tzinfo)


def _iso_in_zone(text: str, tz: tzinfo) -> datetime:
    """Parse an ISO datetime; naive values are read in ``tz``, aware values are converted to it."""
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def parse_period(
    period_str: str,
    day_start_hour: int = 0,
    tz: tzinfo | None = None,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """
    Convert a human-readable period string into a start and end datetime tuple.

    Supported formats:
    - :today — current day (00:00 to 23:59:59.999999)
    - :yesterday — previous day
    - :week — Monday of current week to today (or Friday if today is weekend)
    - :lastweek — full previous week (Monday-Sunday)
    - :month — 1st of current month to today
    - :lastmonth — full previous month
    - :year — 1st of current year to today
    - :lastyear — full previous calendar year
    - :all — from epoch to now
    - YYYY-MM-DD — specific day
    - YYYY-MM-DD YYYY-MM-DD — date range (inclusive on both ends)
    - YYYY-MM-DDTHH:MM:SS — specific datetime
    - YYYY-MM-DDTHH:MM:SS YYYY-MM-DDTHH:MM:SS — datetime range

    Args:
        period_str: Period specification string (case-insensitive for keywords)
        day_start_hour: Hour at which a logical day starts (0 = calendar midnight)
        tz: Timezone the period is computed in (default: local zone, see local_zoneinfo)
        now: Reference instant (default: current time); injectable for tests

    Returns:
        Tuple of (start, end) datetime objects (timezone-aware, in ``tz``)

    Raises:
        SystemExit: If period format is invalid (prints error to stderr and exits)
    """
    tz = tz or local_zoneinfo()
    now = now.astimezone(tz) if now is not None else datetime.now(tz)
    # Compute the logical day in ``tz`` itself; logical_date() would convert to the system zone
    logical_today = (now - timedelta(hours=day_start_hour)).date()
    today_start = day_boundary(logical_today, day_start_hour, now.tzinfo)
    today_end = day_boundary(
        logical_today + timedelta(days=1), day_start_hour, now.tzinfo
    ) - timedelta(microseconds=1)

    period_str = period_str.lower()

    if period_str == ":today":
        start = today_start
        end = day_boundary(
            logical_today + timedelta(days=1), day_start_hour, now.tzinfo
        ) - timedelta(microseconds=1)
    elif period_str == ":yesterday":
        yesterday = logical_today - timedelta(days=1)
        start = day_boundary(yesterday, day_start_hour, now.tzinfo)
        end = today_start - timedelta(microseconds=1)
    elif period_str == ":week":
        monday_offset = logical_today.weekday()
        start = day_boundary(
            logical_today - timedelta(days=monday_offset), day_start_hour, now.tzinfo
        )
        end = today_end
    elif period_str == ":lastweek":
        monday_offset = logical_today.weekday()
        start_of_last_week = logical_today - timedelta(days=monday_offset, weeks=1)
        end_of_last_week = start_of_last_week + timedelta(days=6)
        start = day_boundary(start_of_last_week, day_start_hour, now.tzinfo)
        end = day_boundary(
            end_of_last_week + timedelta(days=1), day_start_hour, now.tzinfo
        ) - timedelta(microseconds=1)
    elif period_str == ":month":
        start = day_boundary(logical_today.replace(day=1), day_start_hour, now.tzinfo)
        end = today_end
    elif period_str == ":lastmonth":
        first_of_this_month = logical_today.replace(day=1)
        last_of_last_month = first_of_this_month - timedelta(days=1)
        first_of_last_month = last_of_last_month.replace(day=1)
        start = day_boundary(first_of_last_month, day_start_hour, now.tzinfo)
        end = day_boundary(first_of_this_month, day_start_hour, now.tzinfo) - timedelta(
            microseconds=1
        )
    elif period_str == ":year":
        start = day_boundary(logical_today.replace(month=1, day=1), day_start_hour, now.tzinfo)
        end = today_end
    elif period_str == ":lastyear":
        first_of_this_year = logical_today.replace(month=1, day=1)
        last_of_last_year = first_of_this_year - timedelta(days=1)
        first_of_last_year = last_of_last_year.replace(month=1, day=1)
        start = day_boundary(first_of_last_year, day_start_hour, now.tzinfo)
        end = day_boundary(first_of_this_year, day_start_hour, now.tzinfo) - timedelta(
            microseconds=1
        )
    elif period_str == ":all":
        start = datetime(1970, 1, 1, tzinfo=now.tzinfo)
        end = now
    else:
        parts = period_str.split()
        try:
            if len(parts) == 1:
                parsed_dt = _iso_in_zone(parts[0], tz)
                parsed_date = parsed_dt.date()
                start = day_boundary(parsed_date, day_start_hour, now.tzinfo)
                end = day_boundary(
                    parsed_date + timedelta(days=1), day_start_hour, now.tzinfo
                ) - timedelta(microseconds=1)
            elif len(parts) == 2:
                start = _iso_in_zone(parts[0], tz)
                end = _iso_in_zone(parts[1], tz)
            else:
                raise ValueError
        except ValueError:
            print(f"Error: Invalid period format '{period_str}'", file=sys.stderr)
            sys.exit(1)

    return start, end


def period_to_utc(
    period_str: str,
    day_start_hour: int = 0,
    tz: tzinfo | None = None,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Same as parse_period, but returns (start, end) as timezone-aware UTC datetimes.

    The local-time boundaries are computed in ``tz`` and then converted to UTC,
    which is the form ActivityWatch queries need.
    """
    start, end = parse_period(period_str, day_start_hour=day_start_hour, tz=tz, now=now)
    return start.astimezone(UTC), end.astimezone(UTC)
