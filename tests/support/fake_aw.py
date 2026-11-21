"""Deterministic stand-ins for the ActivityWatch client, used by characterization tests.

``FakeActivityWatchClient`` mirrors the one method tw-report calls on the real
``aw_report.core.aw_http.ActivityWatchClient``: ``get_events(bucket_id, start=, end=, limit=)``.
Buckets are plain lists of events keyed by full bucket id, so tests control the
exact input without a running ActivityWatch server.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from aw_report.core.event import Event


class FakeActivityWatchClient:
    """In-memory replacement for ``aw_report.core.aw_http.ActivityWatchClient``."""

    def __init__(self, buckets: Optional[Dict[str, List[Event]]] = None, fail: bool = False):
        self.buckets: Dict[str, List[Event]] = dict(buckets or {})
        self.fail = fail  # When True every call raises, simulating an unreachable server
        self.calls: List[dict] = []  # Recorded requests, for asserting fetch ranges

    def get_events(
        self,
        bucket_id: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = -1,
    ) -> List[Event]:
        self.calls.append({"bucket_id": bucket_id, "start": start, "end": end, "limit": limit})
        if self.fail:
            raise ConnectionError("fake ActivityWatch server is down")
        events = self.buckets.get(bucket_id, [])
        selected = [e for e in events if _overlaps(e, start, end)]
        selected.sort(key=lambda e: e.timestamp)
        if limit is not None and limit >= 0:
            selected = selected[:limit]
        # Return copies so callers that re-wrap events cannot mutate the fixture.
        return [
            Event(id=e.id, timestamp=e.timestamp, duration=e.duration, data=dict(e.data))
            for e in selected
        ]


def _overlaps(event: Event, start: Optional[datetime], end: Optional[datetime]) -> bool:
    event_end = event.timestamp + event.duration
    if start is not None and event_end < start:
        return False
    if end is not None and event.timestamp > end:
        return False
    return True


def make_event(start: datetime, minutes: float, data: dict) -> Event:
    """Build an Event starting at ``start`` lasting ``minutes`` minutes."""
    return Event(timestamp=start, duration=timedelta(minutes=minutes), data=data)
