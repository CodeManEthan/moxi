"""cache's config keys: a key renamed since an earlier release is refused, naming the new one,
and each live key carries its page fields in `moxi config cache show --json`."""

from __future__ import annotations

import json

import pytest
from moxi import cli
from moxi.config import ConfigError, Schema

from moxi_cache import settings
from conftest import write_cache_config


@pytest.mark.parametrize("text, old, new", [
    ("[claude]\nttl_seconds = 900\n", "claude.ttl_seconds", "claude.ttl_s"),
    ("[watch]\ntick = 20\n", "watch.tick", "watch.tick_s"),
    ("[segment]\nwarn_before = 300\n", "segment.warn_before", "segment.warn_before_s"),
])
def test_a_renamed_key_is_refused_naming_the_new_one(own_roots, text, old, new):
    write_cache_config(own_roots, text)
    with pytest.raises(ConfigError) as caught:
        settings.current()
    assert caught.value.key == old
    assert caught.value.fix == f"renamed: set {new} instead"


def test_the_schema_passes_the_cores_check_with_its_page_fields():
    Schema.from_resource("moxi_cache", "settings.toml")


def test_show_json_gives_every_key_a_label_and_each_time_its_unit(capsys):
    capsys.readouterr()
    assert cli.main(["config", "cache", "show", "--json"]) == 0
    keys = {key["name"]: key for key in json.loads(capsys.readouterr().out)}
    assert {name: key["label"] for name, key in keys.items()} == {
        "claude.ttl_s": "Claude's cache lifetime", "lifetime": "Your own lifetime rows",
        "watch.tick_s": "The watcher ticks every",
        "segment.warn_before_s": "The status line shows near for the last",
        "timer.subscriber": "Your own timer subscriptions"}
    assert {name: key["unit"] for name, key in keys.items() if key["unit"]} == {
        "claude.ttl_s": "seconds", "watch.tick_s": "seconds",
        "segment.warn_before_s": "seconds"}
    for key in keys.values():
        assert key["common"] is False and key["group"] is None and key["hurts"] is None
