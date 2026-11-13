"""
ActivityWatch event fetching and bucket management.

Provides low-level access to ActivityWatch buckets and events, with proper
error handling and logging. Replaces ad-hoc print-to-stderr patterns with
typed exceptions and structured logging.
"""

import logging
import platform
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TypeVar, overload

from aw_client import ActivityWatchClient
from aw_core.models import Event

from tw_report.exceptions import ActivityWatchConnectionError

logger = logging.getLogger(__name__)

E = TypeVar("E", bound=Event)

# Padding around each range so boundary events are not cut off.
RANGE_PADDING = timedelta(seconds=1)


def get_bucket_id(bucket_name: str, hostname: str | None = None) -> str:
    """Construct the full bucket ID from its name and the machine's hostname.

    Args:
        bucket_name: Bucket name (e.g., "window", "afk", "taskwarrior")

    Returns:
        Full bucket ID (e.g., "aw-watcher-window_hostname")
    """
    if hostname is None:
        hostname = platform.node()
    return f"aw-watcher-{bucket_name}_{hostname}"


@overload
def get_events[E: Event](
    client: ActivityWatchClient,
    bucket_id: str,
    start: datetime,
    end: datetime,
    event_cls: type[E],
    strict: bool = False,
) -> list[E]: ...


@overload
def get_events(
    client: ActivityWatchClient,
    bucket_id: str,
    start: datetime,
    end: datetime,
    event_cls: None = None,
    strict: bool = False,
) -> list[Event]: ...


def get_events(
    client: ActivityWatchClient,
    bucket_id: str,
    start: datetime,
    end: datetime,
    event_cls: type[Event] | None = None,
    strict: bool = False,
) -> list[Event] | list[E]:
    """Fetch events from a specific bucket within a time range.

    Gracefully degrades on connection errors: logs a warning and returns an
    empty list, allowing the report to continue with partial data rather than
    failing completely.

    Args:
        client: ActivityWatch client instance
        bucket_id: Full bucket ID to fetch from (use get_bucket_id() to construct)
        start: Start of time range (inclusive)
        end: End of time range (inclusive)
        event_cls: Optional Event subclass to re-wrap the results into (e.g., WindowEvent,
                   AFKEvent, TaskWarriorEvent). If provided, each event returned by the
                   client (plain Event instances) is re-wrapped as an instance of this class.
        strict: If True, connection failures raise ActivityWatchConnectionError instead
                of degrading to an empty list (used by the `fetch` subcommand).

    Returns:
        List of Event objects (or instances of event_cls if provided) from the bucket.
        Empty list if the bucket is unreachable (connection error, server offline, etc.).
        Logs a warning when degrading.

    Notes:
        - Does NOT raise ActivityWatchConnectionError (logs + degrades instead)
        - Preserves backward compatibility: continues generating reports with
          partial data (window events only, no AFK/task info) when AW is down
        - Use limit=-1 to fetch all events without pagination
    """
    try:
        events = client.get_events(bucket_id, start=start, end=end, limit=-1)
        logger.debug(f"Fetched {len(events)} events from bucket '{bucket_id}'")

        # Re-wrap events in the specified subclass if provided
        if event_cls is not None and event_cls is not Event:
            events = [
                event_cls(
                    id=e.id,
                    timestamp=e.timestamp,
                    duration=e.duration,
                    data=e.data,
                )
                for e in events
            ]

        return events
    except Exception as e:
        if strict:
            raise ActivityWatchConnectionError(
                f"Could not fetch events for bucket '{bucket_id}': {e}"
            ) from e
        logger.warning(
            f"Could not fetch events for bucket '{bucket_id}': {e} (continuing with partial data)",
            exc_info=True,
        )
        return []


@overload
def fetch_events[E: Event](
    client: ActivityWatchClient,
    bucket_id: str,
    start: datetime,
    end: datetime,
    ranges: list[tuple[datetime, datetime]] | None = None,
    *,
    event_cls: type[E],
    strict: bool = False,
) -> list[E]: ...


@overload
def fetch_events(
    client: ActivityWatchClient,
    bucket_id: str,
    start: datetime,
    end: datetime,
    ranges: list[tuple[datetime, datetime]] | None = None,
    event_cls: None = None,
    strict: bool = False,
) -> list[Event]: ...


def fetch_events(
    client: ActivityWatchClient,
    bucket_id: str,
    start: datetime,
    end: datetime,
    ranges: list[tuple[datetime, datetime]] | None = None,
    event_cls: type[Event] | None = None,
    strict: bool = False,
) -> Sequence[Event]:
    """Fetch a bucket's events for [start, end], or only for the given ranges.

    Without ``ranges`` the whole window is queried. With ``ranges`` each range is
    queried with RANGE_PADDING on both sides, and events returned by more than
    one padded range are kept once (events without an ID are never merged).
    """
    if not ranges:
        return get_events(client, bucket_id, start, end, event_cls=event_cls, strict=strict)

    seen_ids: set[object] = set()
    result: list[Event] = []
    for r_start, r_end in ranges:
        for event in get_events(
            client,
            bucket_id,
            r_start - RANGE_PADDING,
            r_end + RANGE_PADDING,
            event_cls=event_cls,
            strict=strict,
        ):
            if event.id is not None:
                if event.id in seen_ids:
                    continue
                seen_ids.add(event.id)
            result.append(event)
    return result
