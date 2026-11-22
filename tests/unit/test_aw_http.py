"""Unit tests for the standard-library ActivityWatch client (aw_report.core.aw_http).

``socket.create_connection`` is replaced by a fake socket that serves canned HTTP bytes, so no
test talks to a live server.
"""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import pytest

import aw_report.core.aw_http as aw_http
from aw_report.core.aw_http import ActivityWatchClient
from aw_report.core.event import Event

ROWS = [
    {
        "id": 7,
        "timestamp": "2026-11-09T01:04:02.123456+00:00",
        "duration": 61.5,
        "data": {"status": "not-afk"},
    },
    {
        "id": 8,
        "timestamp": "2026-11-09T01:05:03+00:00",
        "duration": 0,
        "data": {},
    },
]


def _http(status_line: str, body: bytes, headers: str = "") -> bytes:
    return f"{status_line}\r\n{headers}\r\n".encode("iso-8859-1") + body


def _content_length(body: bytes) -> str:
    return f"Content-Length: {len(body)}\r\nConnection: close\r\n"


@pytest.fixture
def server(monkeypatch):
    """Fake socket: records what was sent and closed; serves ``record['raw']`` in small chunks."""
    body = json.dumps(ROWS).encode()
    record = {
        "sent": [],
        "closed": 0,
        "connect": None,
        "raw": _http("HTTP/1.1 200 OK", body, _content_length(body)),
        "chunk": 17,
        "error": None,
    }

    class FakeSocket:
        def __init__(self):
            self._pending = record["raw"]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            record["closed"] += 1
            return False

        def sendall(self, data):
            record["sent"].append(data.decode("ascii"))

        def recv(self, size):
            if record["error"] is not None:
                raise record["error"]
            chunk = self._pending[: min(size, record["chunk"])]
            self._pending = self._pending[len(chunk) :]
            return chunk

    def fake_create_connection(address, timeout):
        record["connect"] = (address, timeout)
        return FakeSocket()

    monkeypatch.setattr(aw_http.socket, "create_connection", fake_create_connection)
    return record


def _serve(server, status_line, body, headers=""):
    server["raw"] = _http(status_line, body, headers)


class TestRequest:
    def test_connects_to_the_configured_host_and_port(self, server):
        ActivityWatchClient("tw-report", host="10.0.0.5", port="5601", timeout=3.0).get_events("b")
        assert server["connect"] == (("10.0.0.5", 5601), 3.0)

    def test_sends_an_http_1_1_get_for_the_events_path(self, server):
        start = datetime(2026, 11, 9, tzinfo=UTC)
        end = datetime(2026, 11, 16, tzinfo=UTC)

        ActivityWatchClient("tw-report").get_events("aw-watcher-afk_host", start, end, limit=-1)

        request = server["sent"][0]
        request_line, *headers = request.split("\r\n")
        method, target, version = request_line.split(" ")
        assert (method, version) == ("GET", "HTTP/1.1")
        assert "Accept: application/json" in headers
        assert "Connection: close" in headers
        assert "Host: localhost:5600" in headers
        url = urlsplit(target)
        assert url.path == "/api/0/buckets/aw-watcher-afk_host/events"
        query = parse_qs(url.query)
        assert query["limit"] == ["-1"]
        assert datetime.fromisoformat(query["start"][0]) == start
        assert datetime.fromisoformat(query["end"][0]) == end

    def test_omits_start_and_end_when_not_given(self, server):
        ActivityWatchClient("tw-report").get_events("bucket", limit=5)
        target = server["sent"][0].split(" ")[1]
        assert parse_qs(urlsplit(target).query) == {"limit": ["5"]}

    def test_quotes_the_bucket_id(self, server):
        ActivityWatchClient("tw-report").get_events("odd bucket/id")
        assert "/buckets/odd%20bucket%2Fid/events" in server["sent"][0]

    @pytest.mark.parametrize(
        "text",
        [
            "2026-11-09T01:04:02.123456+00:00",
            "a b&c=d/e?f#g~h_i.j-k",
            "ñandú",
        ],
    )
    def test_encoding_matches_urllib_quote(self, server, text):
        from aw_report.core.aw_http import _encode

        assert _encode(text) == quote(text, safe="")
        assert urlencode({"q": text}, quote_via=quote, safe="") == f"q={_encode(text)}"


class TestResponse:
    def test_returns_events_with_parsed_fields(self, server):
        events = ActivityWatchClient("tw-report").get_events("bucket")

        assert all(isinstance(e, Event) for e in events)
        first, second = events
        assert first.id == 7
        # Event keeps timestamps to the millisecond
        assert first.timestamp == datetime(2026, 11, 9, 1, 4, 2, 123000, tzinfo=UTC)
        assert first.duration == timedelta(seconds=61.5)
        assert first.data == {"status": "not-afk"}
        assert second.duration == timedelta(0)

    def test_empty_bucket_gives_an_empty_list(self, server):
        _serve(server, "HTTP/1.1 200 OK", b"[]", _content_length(b"[]"))
        assert ActivityWatchClient("tw-report").get_events("bucket") == []

    def test_chunked_body_is_joined(self, server):
        body = json.dumps(ROWS).encode()
        pieces = [body[:10], body[10:37], body[37:]]
        chunked = b"".join(f"{len(p):x}\r\n".encode() + p + b"\r\n" for p in pieces) + b"0\r\n\r\n"
        _serve(
            server,
            "HTTP/1.1 200 OK",
            chunked,
            "Transfer-Encoding: chunked\r\nConnection: close\r\n",
        )
        assert [e.id for e in ActivityWatchClient("tw-report").get_events("bucket")] == [7, 8]

    def test_body_without_length_is_read_to_the_close(self, server):
        body = json.dumps(ROWS).encode()
        _serve(server, "HTTP/1.1 200 OK", body, "Connection: close\r\n")
        assert len(ActivityWatchClient("tw-report").get_events("bucket")) == 2

    def test_small_reads_are_reassembled(self, server):
        server["chunk"] = 1
        assert len(ActivityWatchClient("tw-report").get_events("bucket")) == 2

    def test_non_200_answer_raises_and_closes_the_socket(self, server):
        _serve(server, "HTTP/1.1 404 Not Found", b"{}", _content_length(b"{}"))
        with pytest.raises(OSError, match="404 Not Found for bucket 'missing'"):
            ActivityWatchClient("tw-report").get_events("missing")
        assert server["closed"] == 1

    def test_short_body_raises(self, server):
        body = json.dumps(ROWS).encode()
        raw = _http("HTTP/1.1 200 OK", body[:-5], _content_length(body))
        server["raw"] = raw
        with pytest.raises(OSError, match="incomplete response"):
            ActivityWatchClient("tw-report").get_events("bucket")

    def test_truncated_head_raises(self, server):
        server["raw"] = b"HTTP/1.1 200 OK\r\nContent-Length: 2"
        with pytest.raises(OSError, match="incomplete response"):
            ActivityWatchClient("tw-report").get_events("bucket")

    def test_malformed_status_line_raises(self, server):
        server["raw"] = b"garbage\r\n\r\n"
        with pytest.raises(OSError, match="malformed status line"):
            ActivityWatchClient("tw-report").get_events("bucket")

    def test_truncated_chunked_body_raises(self, server):
        server["raw"] = _http("HTTP/1.1 200 OK", b"5\r\nabc", "Transfer-Encoding: chunked\r\n")
        with pytest.raises(OSError, match="incomplete chunked response"):
            ActivityWatchClient("tw-report").get_events("bucket")

    def test_chunked_body_without_a_line_end_raises(self, server):
        server["raw"] = _http("HTTP/1.1 200 OK", b"5", "Transfer-Encoding: chunked\r\n")
        with pytest.raises(OSError, match="incomplete chunked response"):
            ActivityWatchClient("tw-report").get_events("bucket")

    def test_bad_chunk_size_raises(self, server):
        server["raw"] = _http("HTTP/1.1 200 OK", b"zz\r\nabc\r\n", "Transfer-Encoding: chunked\r\n")
        with pytest.raises(OSError, match="malformed chunk size"):
            ActivityWatchClient("tw-report").get_events("bucket")

    def test_connection_errors_propagate_and_close_the_socket(self, server):
        server["error"] = OSError("connection refused")
        with pytest.raises(OSError, match="connection refused"):
            ActivityWatchClient("tw-report").get_events("bucket")
        assert server["closed"] == 1

    def test_socket_is_closed_after_a_normal_read(self, server):
        ActivityWatchClient("tw-report").get_events("bucket")
        assert server["closed"] == 1
