"""
Unit tests for the period -> UTC -> ActivityWatch fetch pipeline and `tw-report fetch`.

Time-sensitive assertions pass explicit timezones and reference instants, so the
results do not depend on the machine's TZ.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from aw_core.models import Event

from aw_report.cli.fetch import run_fetch
from aw_report.config import (
    load_aw_server_address,
    resolve_settings,
    validate_activitywatch_section,
)
from aw_report.core.buckets import resolve_buckets
from aw_report.core.events import fetch_events
from aw_report.core.period import period_to_utc
from aw_report.exceptions import ActivityWatchConnectionError, ConfigParsingError

T0 = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)


def _event(eid, start, minutes=1):
    return Event(id=eid, timestamp=start, duration=timedelta(minutes=minutes), data={})


class TestServerAddress:
    def test_activitywatch_table_wins(self, tmp_path):
        aw_cfg = tmp_path / "aw-client.toml"
        aw_cfg.write_text('[server]\nhostname = "10.0.0.5"\nport = "5601"\n')
        user = {"activitywatch": {"host": "192.168.1.9", "port": 5700}}
        assert load_aw_server_address(user, aw_cfg) == ("192.168.1.9", 5700)

    def test_falls_back_to_aw_client_toml(self, tmp_path):
        aw_cfg = tmp_path / "aw-client.toml"
        aw_cfg.write_text('[server]\nhostname = "10.0.0.5"\nport = "5601"\n')
        assert load_aw_server_address({}, aw_cfg) == ("10.0.0.5", 5601)

    def test_defaults_when_nothing_configured(self, tmp_path):
        assert load_aw_server_address({}, tmp_path / "missing.toml") == ("127.0.0.1", 5600)

    def test_invalid_port_raises(self, tmp_path):
        with pytest.raises(ConfigParsingError):
            load_aw_server_address({"activitywatch": {"port": "abc"}}, tmp_path / "x.toml")

    def test_unknown_activitywatch_key_is_rejected(self):
        with pytest.raises(ConfigParsingError, match="windw|buckets"):
            validate_activitywatch_section({"buckets": ["aw-watcher-*"]})

    def test_non_table_section_is_rejected(self):
        with pytest.raises(ConfigParsingError):
            validate_activitywatch_section("host")

    def test_resolve_settings_carries_host_port_timezone(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # no aw-client.toml here
        settings = resolve_settings(
            user_config={"activitywatch": {"host": "10.1.1.1", "port": 5601, "timezone": "UTC"}}
        )
        assert (settings.aw_host, settings.aw_port, settings.timezone) == ("10.1.1.1", 5601, "UTC")
        assert settings.day_start_hour == 0


class TestRoleBuckets:
    def test_defaults_are_watcher_names_on_hostname(self):
        ids = resolve_buckets(None, hostname="laptop")
        assert (ids.afk, ids.window, ids.tasks) == (
            "aw-watcher-afk_laptop",
            "aw-watcher-window_laptop",
            "aw-watcher-taskwarrior_laptop",
        )

    def test_exact_override_of_one_role(self):
        ids = resolve_buckets({"window": "aw-watcher-tmux"}, hostname="laptop")
        assert ids.window == "aw-watcher-tmux"
        assert ids.afk == "aw-watcher-afk_laptop"


class TestFetchEvents:
    def test_ranged_fetch_pads_ranges_and_deduplicates(self):
        shared = _event(7, T0)
        client = MagicMock()
        client.get_events.side_effect = [[shared, _event(1, T0)], [shared]]
        ranges = [
            (T0, T0 + timedelta(minutes=5)),
            (T0 + timedelta(minutes=5), T0 + timedelta(minutes=10)),
        ]
        events = fetch_events(client, "b", T0, T0 + timedelta(hours=1), ranges=ranges)
        assert [e.id for e in events] == [7, 1]
        first = client.get_events.call_args_list[0]
        assert first.kwargs["start"] == T0 - timedelta(seconds=1)
        assert first.kwargs["end"] == T0 + timedelta(minutes=5, seconds=1)

    def test_events_without_id_are_never_merged(self):
        a = Event(id=None, timestamp=T0, duration=timedelta(minutes=1), data={})
        b = Event(id=None, timestamp=T0, duration=timedelta(minutes=1), data={})
        client = MagicMock()
        client.get_events.side_effect = [[a], [b]]
        ranges = [(T0, T0 + timedelta(minutes=1)), (T0, T0 + timedelta(minutes=1))]
        assert len(fetch_events(client, "b", T0, T0 + timedelta(hours=1), ranges=ranges)) == 2

    def test_no_ranges_queries_whole_period(self):
        client = MagicMock()
        client.get_events.return_value = [_event(1, T0)]
        end = T0 + timedelta(days=1)
        fetch_events(client, "b", T0, end, ranges=None)
        client.get_events.assert_called_once_with("b", start=T0, end=end, limit=-1)

    def test_strict_mode_raises_on_connection_error(self):
        client = MagicMock()
        client.get_events.side_effect = ConnectionError("down")
        with pytest.raises(ActivityWatchConnectionError):
            fetch_events(client, "b", T0, T0 + timedelta(days=1), strict=True)

    def test_lenient_mode_degrades_to_empty(self):
        client = MagicMock()
        client.get_events.side_effect = ConnectionError("down")
        assert fetch_events(client, "b", T0, T0 + timedelta(days=1)) == []


class TestPeriodToUtc:
    def test_lastweek_utc_with_injected_now(self):
        tz = ZoneInfo("America/Mexico_City")  # UTC-6, no DST since 2022
        now = datetime(2026, 11, 13, 12, 0, tzinfo=tz)  # Friday
        start, end = period_to_utc(":lastweek", day_start_hour=0, tz=tz, now=now)
        assert start == datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 11, 9, 5, 59, 59, 999999, tzinfo=timezone.utc)

    def test_month_across_dst_switch_uses_real_zone_offsets(self):
        # New York: DST starts 2026-03-08, so 2026-03-01 is EST (UTC-5)
        tz = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 20, 12, 0, tzinfo=tz)
        start, _ = period_to_utc(":month", day_start_hour=0, tz=tz, now=now)
        assert start == datetime(2026, 3, 1, 5, 0, tzinfo=timezone.utc)

    def test_day_start_hour_applies(self):
        tz = ZoneInfo("America/Mexico_City")
        now = datetime(2026, 11, 13, 2, 0, tzinfo=tz)  # before 04:00 -> previous logical day
        start, _ = period_to_utc(":today", day_start_hour=4, tz=tz, now=now)
        assert start == datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)


class TestFetchCommand:
    def _config(self, tmp_path, body):
        d = tmp_path / "tw-report"
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.toml").write_text(body)
        return tmp_path

    def test_outputs_three_bucket_arrays_in_role_order(self, tmp_path, capsys, monkeypatch):
        xdg = self._config(tmp_path, "[buckets]\nhostname = \"h\"\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        client = MagicMock()
        ids_seen = []

        def fake_events(bucket_id, start, end, limit):
            ids_seen.append(bucket_id)
            return [_event(1, T0)] if bucket_id == "aw-watcher-afk_h" else []

        client.get_events.side_effect = fake_events
        monkeypatch.setattr("aw_report.cli.fetch.ActivityWatchClient", lambda *a, **k: client)
        assert run_fetch([":today"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert ids_seen == ["aw-watcher-afk_h", "aw-watcher-window_h", "aw-watcher-taskwarrior_h"]
        assert len(payload) == 3
        assert payload[0][0]["timestamp"].endswith("+00:00")
        assert payload[1] == [] and payload[2] == []

    def test_connection_failure_exits_nonzero(self, tmp_path, capsys, monkeypatch):
        xdg = self._config(tmp_path, "")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        client = MagicMock()
        client.get_events.side_effect = ConnectionError("refused")
        monkeypatch.setattr("aw_report.cli.fetch.ActivityWatchClient", lambda *a, **k: client)
        assert run_fetch([":today"]) == 1
        assert "Error" in capsys.readouterr().err


class TestLocalZoneinfo:
    def test_explicit_name_wins(self, monkeypatch):
        from aw_report.core.period import local_zoneinfo

        monkeypatch.setenv("TZ", "Asia/Tokyo")
        assert local_zoneinfo("America/Mexico_City") == ZoneInfo("America/Mexico_City")

    def test_unknown_explicit_name_raises(self):
        from aw_report.core.period import local_zoneinfo

        with pytest.raises(ValueError, match="Unknown timezone"):
            local_zoneinfo("Mars/Olympus")

    def test_uses_tz_environment_variable(self, monkeypatch):
        from aw_report.core.period import local_zoneinfo

        monkeypatch.setenv("TZ", "Asia/Tokyo")
        assert local_zoneinfo() == ZoneInfo("Asia/Tokyo")

    def test_invalid_tz_environment_falls_back_to_system_zone(self, monkeypatch):
        from aw_report.core.period import local_zoneinfo

        monkeypatch.setenv("TZ", "Not/AZone")
        assert local_zoneinfo() is not None

    def test_no_name_and_no_tz_uses_system_zone(self, monkeypatch):
        from aw_report.core.period import local_zoneinfo

        monkeypatch.delenv("TZ", raising=False)
        assert local_zoneinfo() is not None


class TestDispatchAndDefaults:
    def test_main_dispatches_fetch_subcommand(self, monkeypatch):
        from aw_report.cli import main as cli_main
        from aw_report.cli import fetch as fetch_mod

        seen = []
        monkeypatch.setattr(fetch_mod, "run_fetch", lambda argv: seen.append(argv) or 7)
        monkeypatch.setattr("sys.argv", ["aw-report", "fetch", ":lastweek"])
        assert cli_main.main() == 7
        assert seen == [[":lastweek"]]

    def test_server_address_defaults_to_user_config_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert load_aw_server_address() == ("127.0.0.1", 5600)

    def test_unexpected_error_during_fetch_exits_nonzero(self, tmp_path, capsys, monkeypatch):
        d = tmp_path / "tw-report"
        d.mkdir()
        (d / "config.toml").write_text("")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        def boom(*a, **k):
            raise RuntimeError("unexpected")

        monkeypatch.setattr("aw_report.cli.fetch.ActivityWatchClient", boom)
        assert run_fetch([":today"]) == 1
        assert "could not query ActivityWatch" in capsys.readouterr().err


class TestIsoPeriodZones:
    def test_naive_iso_is_read_in_the_given_zone(self):
        from aw_report.core.period import parse_period

        tokyo = ZoneInfo("Asia/Tokyo")
        start, end = parse_period("2026-06-15T10:30:00 2026-06-16T08:00:00", tz=tokyo)
        assert start == datetime(2026, 6, 15, 10, 30, tzinfo=tokyo)
        assert end == datetime(2026, 6, 16, 8, 0, tzinfo=tokyo)

    def test_aware_iso_is_converted_to_the_given_zone(self):
        from aw_report.core.period import parse_period

        mexico = ZoneInfo("America/Mexico_City")
        start, end = parse_period(
            "2026-06-15T16:30:00+00:00 2026-06-16T14:00:00+00:00", tz=mexico
        )
        assert start == datetime(2026, 6, 15, 10, 30, tzinfo=mexico)
        assert end == datetime(2026, 6, 16, 8, 0, tzinfo=mexico)
