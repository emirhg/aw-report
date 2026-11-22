"""Standard-library client for the ActivityWatch REST API.

Replaces ``aw_client.ActivityWatchClient``, whose import pulled in requests, persistqueue and
asyncio. Only the read the report needs is implemented: ``get_events(bucket_id, start, end,
limit)``. It speaks HTTP/1.1 over a plain socket, because ``http.client`` (with ``email`` and
``ssl``) costs about 50 ms more to import. The events are ``aw_report.core.event.Event`` objects.
"""

import json
import socket
from datetime import datetime
from typing import Any

from aw_report.core.event import Event

DEFAULT_TIMEOUT_SECONDS = 10.0
_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~")


def _encode(text: str) -> str:
    """Percent-encode everything outside RFC 3986 unreserved characters (as quote(safe=""))."""
    return "".join(
        chr(byte) if byte in _UNRESERVED else f"%{byte:02X}" for byte in text.encode("utf-8")
    )


def _dechunk(body: bytes, bucket_id: str) -> bytes:
    """Join the chunks of a ``Transfer-Encoding: chunked`` body."""
    out = bytearray()
    pos = 0
    while True:
        line_end = body.find(b"\r\n", pos)
        if line_end < 0:
            raise OSError(f"incomplete chunked response for bucket '{bucket_id}'")
        try:
            size = int(body[pos:line_end].split(b";")[0], 16)
        except ValueError as error:
            raise OSError(f"malformed chunk size for bucket '{bucket_id}'") from error
        pos = line_end + 2
        if size == 0:
            return bytes(out)
        if pos + size > len(body):
            raise OSError(f"incomplete chunked response for bucket '{bucket_id}'")
        out += body[pos : pos + size]
        pos += size + 2


def _response_body(raw: bytes, bucket_id: str) -> bytes:
    """Check the status line and return the body of one complete HTTP response."""
    head_end = raw.find(b"\r\n\r\n")
    if head_end < 0:
        raise OSError(f"incomplete response from ActivityWatch for bucket '{bucket_id}'")
    head_lines = raw[:head_end].decode("iso-8859-1").split("\r\n")
    status_parts = head_lines[0].split(" ", 2)
    if len(status_parts) < 2 or not status_parts[1].isdigit():
        raise OSError(f"malformed status line from ActivityWatch for bucket '{bucket_id}'")
    status = int(status_parts[1])
    if status != 200:
        reason = status_parts[2] if len(status_parts) > 2 else ""
        raise OSError(f"ActivityWatch answered {status} {reason} for bucket '{bucket_id}'")

    headers = {}
    for line in head_lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    body = raw[head_end + 4 :]
    if headers.get("transfer-encoding", "").lower() == "chunked":
        return _dechunk(body, bucket_id)
    if "content-length" in headers:
        length = int(headers["content-length"])
        if len(body) < length:
            raise OSError(f"incomplete response from ActivityWatch for bucket '{bucket_id}'")
        return body[:length]
    return body


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
            OSError: On a connection failure, a non-200 answer or an incomplete response.
        """
        params: dict[str, Any] = {"limit": limit}
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()
        query = "&".join(f"{key}={_encode(str(value))}" for key, value in params.items())
        path = f"/api/0/buckets/{_encode(bucket_id)}/events?{query}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Accept: application/json\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")

        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.sendall(request)
            chunks = []
            while data := sock.recv(65536):
                chunks.append(data)

        body = _response_body(b"".join(chunks), bucket_id)
        return [
            Event(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                duration=row["duration"],
                data=row["data"],
            )
            for row in json.loads(body)
        ]
