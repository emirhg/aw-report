"""Unit tests for the standard-library ActivityWatch client (aw_report.core.aw_http).

``http.client.HTTPConnection`` is replaced, so no test talks to a live server.
"""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

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


class _Response:
    def __init__(self, status, body):
        self.status = status
        self.reason = "OK" if status == 200 else "Not Found"
        self._body = body

    def read(self):
        return self._body


@pytest.fixture
def connections(monkeypatch):
    """Replace HTTPConnection; record each request, the close, and answer with ROWS."""
    record = {
        "requests": [],
        "closed": 0,
        "status": 200,
        "body": json.dumps(ROWS).encode(),
        "error": None,
    }

    class FakeConnection:
        def __init__(self, host, port, timeout):
            record["connect"] = (host, port, timeout)

        def request(self, method, path, headers):
            if record["error"] is not None:
                raise record["error"]
            record["requests"].append({"method": method, "path": path, "headers": headers})

        def getresponse(self):
            return _Response(record["status"], record["body"])

        def close(self):
            record["closed"] += 1

    monkeypatch.setattr(aw_http.http.client, "HTTPConnection", FakeConnection)
    return record


class TestGetEvents:
    def test_connects_to_the_configured_host_and_port(self, connections):
        ActivityWatchClient("tw-report", host="10.0.0.5", port="5601", timeout=3.0).get_events("b")
        assert connections["connect"] == ("10.0.0.5", 5601, 3.0)

    def test_builds_the_events_path_with_the_window(self, connections):
        start = datetime(2026, 11, 9, tzinfo=UTC)
        end = datetime(2026, 11, 16, tzinfo=UTC)

        ActivityWatchClient("tw-report").get_events("aw-watcher-afk_host", start, end, limit=-1)

        request = connections["requests"][0]
        assert request["method"] == "GET"
        assert request["headers"] == {"Accept": "application/json"}
        url = urlsplit(request["path"])
        assert url.path == "/api/0/buckets/aw-watcher-afk_host/events"
        query = parse_qs(url.query)
        assert query["limit"] == ["-1"]
        assert datetime.fromisoformat(query["start"][0]) == start
        assert datetime.fromisoformat(query["end"][0]) == end

    def test_omits_start_and_end_when_not_given(self, connections):
        ActivityWatchClient("tw-report").get_events("bucket", limit=5)
        query = parse_qs(urlsplit(connections["requests"][0]["path"]).query)
        assert query == {"limit": ["5"]}

    def test_quotes_the_bucket_id(self, connections):
        ActivityWatchClient("tw-report").get_events("odd bucket/id")
        assert "/buckets/odd%20bucket%2Fid/events" in connections["requests"][0]["path"]

    def test_returns_events_with_parsed_fields(self, connections):
        events = ActivityWatchClient("tw-report").get_events("bucket")

        assert all(isinstance(e, Event) for e in events)
        first, second = events
        assert first.id == 7
        # Event keeps timestamps to the millisecond
        assert first.timestamp == datetime(2026, 11, 9, 1, 4, 2, 123000, tzinfo=UTC)
        assert first.duration == timedelta(seconds=61.5)
        assert first.data == {"status": "not-afk"}
        assert second.duration == timedelta(0)

    def test_empty_bucket_gives_an_empty_list(self, connections):
        connections["body"] = b"[]"
        assert ActivityWatchClient("tw-report").get_events("bucket") == []

    def test_connection_is_closed_after_a_normal_read(self, connections):
        ActivityWatchClient("tw-report").get_events("bucket")
        assert connections["closed"] == 1

    def test_non_200_answer_raises_and_still_closes_the_connection(self, connections):
        connections["status"] = 404
        with pytest.raises(OSError, match="404 Not Found for bucket 'missing'"):
            ActivityWatchClient("tw-report").get_events("missing")
        assert connections["closed"] == 1

    def test_transport_errors_propagate_and_close_the_connection(self, connections):
        connections["error"] = OSError("connection refused")
        with pytest.raises(OSError, match="connection refused"):
            ActivityWatchClient("tw-report").get_events("bucket")
        assert connections["closed"] == 1
