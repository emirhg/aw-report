"""Standard-library client for the ActivityWatch REST API.

Replaces ``aw_client.ActivityWatchClient``, whose import pulled in requests, persistqueue and
asyncio. Only the read the report needs is implemented: ``get_events(bucket_id, start, end,
limit)``, over ``http.client`` (urllib.request costs about twice as much to import).
The events are ``aw_report.core.event.Event`` objects.
"""

import http.client
import json
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

from aw_report.core.event import Event

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
        self.host = host
        self.port = int(port)
        self.timeout = timeout

    def get_events(
        self,
        bucket_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = -1,
    ) -> list[Event]:
        """Events of one bucket, optionally within [start, end]. ``limit=-1`` means all.

        Raises:
            OSError: On a connection failure or a non-200 answer.
        """
        params: dict[str, Any] = {"limit": limit}
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()
        path = f"/api/0/buckets/{quote(bucket_id, safe='')}/events?{urlencode(params)}"

        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            connection.request("GET", path, headers={"Accept": "application/json"})
            response = connection.getresponse()
            body = response.read()
            if response.status != 200:
                raise OSError(
                    f"ActivityWatch answered {response.status} {response.reason} "
                    f"for bucket '{bucket_id}'"
                )
        finally:
            connection.close()

        return [
            Event(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                duration=row["duration"],
                data=row["data"],
            )
            for row in json.loads(body)
        ]
