"""Standard-library client for the ActivityWatch REST API.

Replaces ``aw_client.ActivityWatchClient``, whose import pulls in requests, persistqueue and
asyncio (about 0.6 s per run). Only the read the report needs is implemented:
``get_events(bucket_id, start, end, limit)``. The events are ``aw_core`` Event objects, so the
rest of the pipeline is unchanged.
"""

import json
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from aw_core.models import Event

DEFAULT_TIMEOUT_SECONDS = 10.0


class ActivityWatchClient:
    """Read-only ActivityWatch client over HTTP (``/api/0``)."""

    def __init__(
        self,
        client_name: str,
        host: str = "localhost",
        port: str | int = 5600,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.client_name = client_name
        self.base_url = f"http://{host}:{port}/api/0"
        self.timeout = timeout

    def get_events(
        self,
        bucket_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = -1,
    ) -> list[Event]:
        """Events of one bucket, optionally within [start, end]. ``limit=-1`` means all."""
        params: dict[str, Any] = {"limit": limit}
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()
        url = f"{self.base_url}/buckets/{quote(bucket_id, safe='')}/events?{urlencode(params)}"
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 (http, localhost)
            rows = json.load(response)
        return [
            Event(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                duration=timedelta(seconds=row["duration"]),
                data=row["data"],
            )
            for row in rows
        ]
