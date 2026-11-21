"""Unit tests for the standard-library ActivityWatch client (aw_report.core.aw_http).

``urlopen`` is replaced, so no test talks to a live server.
"""

import io
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from aw_core.models import Event

import aw_report.core.aw_http as aw_http
from aw_report.core.aw_http import ActivityWatchClient

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


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def captured(monkeypatch):
    """Replace urlopen; record each request and answer with ROWS (or a given body)."""
    calls = []
    state = {"body": json.dumps(ROWS).encode(), "error": None}

    def fake_urlopen(request, timeout):
        calls.append({"url": request.full_url, "timeout": timeout, "headers": request.headers})
        if state["error"] is not None:
            raise state["error"]
        return _Response(state["body"])

    monkeypatch.setattr(aw_http, "urlopen", fake_urlopen)
    return calls, state


class TestGetEvents:
    def test_builds_the_events_url_with_the_window(self, captured):
        calls, _ = captured
        client = ActivityWatchClient("tw-report", host="10.0.0.5", port="5601", timeout=3.0)
        start = datetime(2026, 11, 9, tzinfo=UTC)
        end = datetime(2026, 11, 16, tzinfo=UTC)

        client.get_events("aw-watcher-afk_host", start=start, end=end, limit=-1)

        url = urlsplit(calls[0]["url"])
        assert (url.scheme, url.netloc) == ("http", "10.0.0.5:5601")
        assert url.path == "/api/0/buckets/aw-watcher-afk_host/events"
        query = parse_qs(url.query)
        assert query["limit"] == ["-1"]
        assert datetime.fromisoformat(query["start"][0]) == start
        assert datetime.fromisoformat(query["end"][0]) == end
        assert calls[0]["timeout"] == 3.0

    def test_omits_start_and_end_when_not_given(self, captured):
        calls, _ = captured
        ActivityWatchClient("tw-report").get_events("bucket", limit=5)
        query = parse_qs(urlsplit(calls[0]["url"]).query)
        assert query == {"limit": ["5"]}

    def test_quotes_the_bucket_id(self, captured):
        calls, _ = captured
        ActivityWatchClient("tw-report").get_events("odd bucket/id")
        assert "/buckets/odd%20bucket%2Fid/events" in calls[0]["url"]

    def test_returns_aw_core_events_with_parsed_fields(self, captured):
        events = ActivityWatchClient("tw-report").get_events("bucket")

        assert all(isinstance(e, Event) for e in events)
        first, second = events
        assert first.id == 7
        # aw_core keeps timestamps to the millisecond, for aw_client and for this client alike
        assert first.timestamp == datetime(2026, 11, 9, 1, 4, 2, 123000, tzinfo=UTC)
        assert first.duration == timedelta(seconds=61.5)
        assert first.data == {"status": "not-afk"}
        assert second.duration == timedelta(0)

    def test_empty_bucket_gives_an_empty_list(self, captured):
        _, state = captured
        state["body"] = b"[]"
        assert ActivityWatchClient("tw-report").get_events("bucket") == []

    def test_transport_errors_propagate_to_the_caller(self, captured):
        _, state = captured
        state["error"] = OSError("connection refused")
        with pytest.raises(OSError, match="connection refused"):
            ActivityWatchClient("tw-report").get_events("bucket")
