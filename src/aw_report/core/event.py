"""ActivityWatch event model, local copy of the behaviour the report relies on.

``aw_core.models.Event`` is used for one class only, and importing its package costs about
0.17 s per run (its ``__init__`` loads the config, logging and TOML modules). This module
keeps the same behaviour without that import:

- a ``dict`` subclass with the keys in the order id, timestamp, duration, data;
- timestamps are stored in UTC, truncated to milliseconds; a naive string is taken as UTC;
  a naive datetime is taken as UTC with a warning;
- durations are timedeltas; ints and floats are seconds;
- ``to_json_dict`` / ``to_json_str`` produce the same JSON as aw_core.
"""

import json
import numbers
from datetime import UTC, datetime, timedelta
from typing import Any


def _timestamp_parse(ts_in: datetime | str) -> datetime:
    ts = datetime.fromisoformat(ts_in) if isinstance(ts_in, str) else ts_in
    # Millisecond resolution, as aw_core stores it (mongodb and unix-time software)
    ts = ts.replace(microsecond=int(ts.microsecond / 1000) * 1000)
    if not ts.tzinfo:
        if isinstance(ts_in, str):
            # aw_core parses strings with iso8601, which defaults to UTC without a warning
            return ts.replace(tzinfo=UTC)
        ts = ts.replace(tzinfo=UTC)
    return ts


class Event(dict):
    """One ActivityWatch event: id, timestamp (UTC), duration and data."""

    def __init__(
        self,
        id: int | str | None = None,
        timestamp: datetime | str | None = None,
        duration: timedelta | float = 0,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.id = id
        if timestamp is None:
            self.timestamp = datetime.now(UTC)
        else:
            self.timestamp = _timestamp_parse(timestamp)
        self.duration = duration
        self.data = data or {}

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Event):
            return (
                self.timestamp == other.timestamp
                and self.duration == other.duration
                and self.data == other.data
            )
        raise TypeError(
            f"operator not supported between instances of '{type(self)}' and '{type(other)}'"
        )

    def __lt__(self, other: object) -> bool:
        if isinstance(other, Event):
            return self.timestamp < other.timestamp
        raise TypeError(
            f"operator not supported between instances of '{type(self)}' and '{type(other)}'"
        )

    def to_json_dict(self) -> dict:
        """JSON-ready copy: timestamp as an ISO string in UTC, duration in seconds."""
        json_data = self.copy()
        json_data["timestamp"] = self.timestamp.astimezone(UTC).isoformat()
        json_data["duration"] = self.duration.total_seconds()
        return json_data

    def to_json_str(self) -> str:
        return json.dumps(self.to_json_dict())

    def _hasprop(self, propname: str) -> bool:
        return propname in self and self[propname] is not None

    @property
    def id(self) -> int | str | None:
        return self["id"] if self._hasprop("id") else None

    @id.setter
    def id(self, id: int | str | None) -> None:
        self["id"] = id

    @property
    def data(self) -> dict:
        return self["data"] if self._hasprop("data") else {}

    @data.setter
    def data(self, data: dict) -> None:
        self["data"] = data

    @property
    def timestamp(self) -> datetime:
        return self["timestamp"]

    @timestamp.setter
    def timestamp(self, timestamp: datetime | str) -> None:
        self["timestamp"] = _timestamp_parse(timestamp).astimezone(UTC)

    @property
    def duration(self) -> timedelta:
        return self["duration"] if self._hasprop("duration") else timedelta(0)

    @duration.setter
    def duration(self, duration: timedelta | float) -> None:
        if isinstance(duration, timedelta):
            self["duration"] = duration
        elif isinstance(duration, numbers.Real):
            self["duration"] = timedelta(seconds=duration)
        else:
            raise TypeError(f"Couldn't parse duration of invalid type {type(duration)}")
