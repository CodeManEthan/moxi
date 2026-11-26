"""The announcement and interface 1, as other modules reach them through `moxi.modules`."""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
from importlib import metadata
from pathlib import Path

import pytest

from moxi import modules
from moxi.config import Schema
from moxi_cache import api, claude, clock, module, timer
from moxi_sessions import api as sessions_api

from conftest import claude_config, write_cache_config
from moxi_dev.testing import toml_string

SRC = Path(api.__file__).parent


def test_the_announcement_offers_interface_1():
    assert module.INTERFACES == frozenset({1})
    assert module.API == "moxi_cache.api"
    assert module.CLI == "moxi_cache.cli:main"
    assert module.SETTINGS == "settings.toml"
    assert module.GATED == {} and module.INTERNAL_ENV == {}
    assert set(module.OPTIONAL) == {"ui"}


def test_load_finds_the_module_and_its_api():
    modules.reset()
    found = modules.load("cache", interface=1)
    assert found.ok, found
    assert found.api is api
    modules.reset()
    assert not modules.load("cache", interface=2).ok


def test_interface_1_holds_its_names():
    assert {"read", "KINDS", "CODES", "MISSING", "open_store", "projects_root",
            "clock_records", "asking"} <= set(
        api.__all__)
    assert api.KINDS == ("warm", "cold", "fresh", "no-cache", "unknown")
    assert repr(api.MISSING) == "MISSING"


def test_the_read_signature_is_fixed():
    sig = inspect.signature(api.read)
    assert list(sig.parameters) == [
        "harness", "session_id", "now", "projects_root", "turned", "cache_source",
        "new_session", "agent_model", "bound_at", "store"]
    params = sig.parameters
    assert all(params[n].kind is inspect.Parameter.KEYWORD_ONLY
               for n in list(params)[2:])
    assert (params["turned"].default, params["new_session"].default,
            params["agent_model"].default, params["store"].default) == (
        True, False, api.MISSING, None)
    assert list(inspect.signature(api.open_store).parameters) == ["learn"]


def test_projects_root_is_sessions_rule_re_exported(tmp_path):
    env = {"HOME": "/home/someone"}
    assert api.projects_root(env) == str(Path("/home/someone/.claude/projects"))
    env = {"HOME": "/home/someone", "CLAUDE_CONFIG_DIR": "~/elsewhere"}
    assert api.projects_root(env) == str(Path("/home/someone/elsewhere/projects"))
    assert api.projects_root(env) == str(sessions_api.projects_root(env))
    assert api.projects_root(None) == str(claude_config() / "projects")


def test_an_unknown_harness_reads_no_cache():
    for harness in ("claude-shim", "speech", "", None):
        got = api.read(harness, "aaaaaaaa-1111-4111-8111-111111111111")
        assert (got.kind, got.code) == ("no-cache", "unknown-harness"), harness
        assert got.reason == f"cache has no clock for {harness or 'unknown'} sessions"


def test_read_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("inside")

    for name in ("claude", "codex", "opencode"):
        target = getattr(api, name)
        monkeypatch.setattr(target, "read", boom)
        got = api.read(name, "x")
        assert (got.kind, got.code) == ("unknown", "read-failed")
        assert got.reason == "the cache read failed (RuntimeError: inside)"


def test_every_code_the_readers_give_is_in_codes():
    """A code is given only by name from `clock`; each such name is in CODES."""
    used = set()
    for path in SRC.glob("*.py"):
        used |= set(re.findall(r"_clock\.([A-Z_]+)\b", path.read_text()))
    used -= {"CLOCK_BLOCK", "CLOCK_SCAN_LIMIT"}
    assert used, "the readers name their codes through `clock`"
    for name in used:
        assert getattr(clock, name) in api.CODES, name


def test_a_clock_is_json(tmp_path):
    got = api.read("claude", "", new_session=True)
    doc = json.loads(json.dumps(got.as_dict()))
    assert doc["kind"] == "fresh" and set(doc) >= {"code", "ttl_source", "lowering"}


def test_the_schema_holds_the_first_releases_keys():
    schema = Schema.from_resource("moxi_cache", "settings.toml")
    assert [key.name for key in schema.live()] == ["claude.ttl_s", "lifetime", "watch.tick_s",
                                                   "segment.warn_before_s", "timer.subscriber"]
    subscribers = schema.keys["timer.subscriber"]
    assert (subscribers.type, subscribers.default, subscribers.read) == ("list", [], "use")
    ttl = schema.keys["claude.ttl_s"]
    assert (ttl.type, ttl.default, ttl.read) == ("int", 3600, "use")
    rows = schema.keys["lifetime"]
    assert (rows.type, rows.default, rows.read) == ("list", [], "use")
    tick = schema.keys["watch.tick_s"]
    assert (tick.type, tick.default, tick.read) == ("int", 10, "use")
    warn = schema.keys["segment.warn_before_s"]
    assert (warn.type, warn.default, warn.read) == ("int", 900, "use")


def test_the_tiers_are_one_hour_and_five_minutes():
    assert claude.TIER_1H == ("cache_creation_1h_input_tokens", 3600)
    assert claude.TIER_5M == ("cache_creation_5m_input_tokens", 300)


def test_the_version_is_0_1_7():
    assert metadata.version("moxi-cache") == "0.1.7"
    assert module.INTERFACES == frozenset({1})


# --- asking -----------------------------------------------------------------------------------

@pytest.fixture
def subscribed(own_roots, tmp_path):
    """Four config subscriptions: one plain, one needing the request stamp, one needing a
    value, and one `load` refuses, since its run[0] is not executable."""
    exe = tmp_path / "bin" / "verb"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    write_cache_config(own_roots, f"""
[[timer.subscriber]]
name = "plain"
at = 0
run = [{toml_string(exe)}]

[[timer.subscriber]]
name = "stamped"
at = -900
requires_env = {{ MOXI_LAUNCHER_REQUEST = "*" }}
run = [{toml_string(exe)}, "{{session}}"]

[[timer.subscriber]]
name = "valued"
at = 0
requires_env = {{ V = "on" }}
run = [{toml_string(exe)}]

[[timer.subscriber]]
name = "refused"
at = 0
run = [{toml_string(tmp_path / 'missing')}]
""")
    return exe


#: Where a process's environment can't be read (Windows), `asking` is None, which
#: `test_asking_is_none_where_an_env_cannot_be_read` covers on every system.
READS_ENV = pytest.mark.skipif(not timer.env_checkable(),
                               reason="Windows: a process's environment can't be read "
                                      "there, so asking is None")


@READS_ENV
def test_asking_names_what_the_env_meets(subscribed):
    assert api.asking({"MOXI_LAUNCHER_REQUEST": "doctor"}) == ("plain", "stamped")
    assert api.asking({"MOXI_LAUNCHER_REQUEST": "doctor", "V": "on"}) == (
        "plain", "stamped", "valued")
    assert api.asking({"MOXI_LAUNCHER_REQUEST": "", "V": "off"}) == ("plain",)
    assert api.asking({}) == ("plain",)


@READS_ENV
def test_asking_reads_the_config_as_it_is_now(subscribed, own_roots):
    assert "stamped" in api.asking({"MOXI_LAUNCHER_REQUEST": "x"})
    write_cache_config(own_roots, "")
    assert api.asking({"MOXI_LAUNCHER_REQUEST": "x"}) == ()


def test_asking_is_none_where_an_env_cannot_be_read(subscribed, monkeypatch):
    monkeypatch.setattr(timer, "env_checkable", lambda: False)
    assert api.asking({"MOXI_LAUNCHER_REQUEST": "doctor"}) is None


@READS_ENV
def test_asking_starts_no_process_and_writes_no_file(subscribed, monkeypatch, tmp_path):
    def refuse(*a, **k):
        raise AssertionError("asking started a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(timer, "spawn", refuse)
    monkeypatch.setattr(timer._proc, "spawn", refuse)

    def files():
        return sorted((root, name, os.stat(os.path.join(root, name)).st_mtime_ns)
                      for root, _, names in os.walk(tmp_path) for name in names)

    before = files()
    assert api.asking({"MOXI_LAUNCHER_REQUEST": "doctor"}) == ("plain", "stamped")
    assert files() == before
