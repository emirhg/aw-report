"""Tests for bucket resolution from the [buckets] config table (aw_report.core.buckets)."""

import platform

import pytest

from aw_report.core.buckets import parse_sources, ROLES, BucketIds, default_bucket_id, resolve_buckets
from aw_report.core.events import get_bucket_id
from aw_report.exceptions import ConfigParsingError


def test_defaults_use_watcher_names_and_given_hostname():
    assert resolve_buckets(None, hostname="box") == BucketIds(
        afk="aw-watcher-afk_box",
        window="aw-watcher-window_box",
        tasks="aw-watcher-taskwarrior_box",
    )


def test_missing_hostname_falls_back_to_current_machine(monkeypatch):
    monkeypatch.setattr(platform, "node", lambda: "thishost")
    assert resolve_buckets({}).window == "aw-watcher-window_thishost"


def test_hostname_key_overrides_the_machine_name_for_defaults(monkeypatch):
    monkeypatch.setattr(platform, "node", lambda: "thishost")
    resolved = resolve_buckets({"hostname": "laptop"})
    assert resolved.afk == "aw-watcher-afk_laptop"
    assert resolved.tasks == "aw-watcher-taskwarrior_laptop"


def test_explicit_bucket_id_overrides_only_its_role():
    resolved = resolve_buckets({"window": "aw-watcher-tmux"}, hostname="box")
    assert resolved.window == "aw-watcher-tmux"
    assert resolved.afk == "aw-watcher-afk_box"
    assert resolved.tasks == "aw-watcher-taskwarrior_box"


def test_all_roles_can_be_overridden():
    resolved = resolve_buckets({"afk": "a", "window": "w", "tasks": "t"}, hostname="ignored")
    assert resolved == BucketIds(afk="a", window="w", tasks="t")


def test_explicit_id_wins_over_hostname_key():
    resolved = resolve_buckets({"hostname": "laptop", "afk": "fixed"})
    assert resolved.afk == "fixed"
    assert resolved.window == "aw-watcher-window_laptop"


def test_roles_are_the_three_fixed_names():
    assert ROLES == ("afk", "window", "tasks")


def test_default_bucket_id_maps_tasks_to_the_taskwarrior_watcher():
    assert default_bucket_id("tasks", hostname="h") == "aw-watcher-taskwarrior_h"
    assert default_bucket_id("afk", hostname="h") == "aw-watcher-afk_h"


def test_get_bucket_id_accepts_an_explicit_hostname():
    assert get_bucket_id("window", hostname="x") == "aw-watcher-window_x"
    assert get_bucket_id("window") == f"aw-watcher-window_{platform.node()}"


@pytest.mark.parametrize("bad", [{"windows": "x"}, {"Window": "x"}])
def test_unknown_keys_are_rejected_with_the_allowed_list(bad):
    with pytest.raises(ConfigParsingError, match="Unknown key"):
        resolve_buckets(bad)


@pytest.mark.parametrize("value", [42, ["x"], "", "   "])
def test_non_string_or_blank_values_are_rejected(value):
    with pytest.raises(ConfigParsingError, match="must be a non-empty string"):
        resolve_buckets({"afk": value})


class TestParseSources:
    def test_none_and_empty_mean_every_role(self):
        assert parse_sources(None) == frozenset(ROLES)
        assert parse_sources("") == frozenset(ROLES)

    def test_comma_list_is_parsed_case_and_space_insensitively(self):
        assert parse_sources(" Tasks , afk ") == frozenset({"tasks", "afk"})

    def test_unknown_role_is_rejected_with_the_valid_choices(self):
        with pytest.raises(ValueError, match="Unknown source\\(s\\): tmux.*afk, window, tasks"):
            parse_sources("tmux,tasks")
