"""The announcement and interface 2, as other modules reach them through `moxi.modules`."""

from __future__ import annotations

import importlib
import inspect
from importlib import metadata

from moxi import modules
from moxi.config import Schema
from moxi_sessions import api, module

CLAUDE_NAMES = {"parse_line", "iter_entries", "stream_entries", "parse_timestamp",
                "find_for_session", "probe_root", "search_roots", "MALFORMED", "RootProbe"}
CODEX_NAMES = {"find_rollout", "stream_rollout", "NAME_RE"}


def test_the_announcement_offers_interface_2():
    assert module.INTERFACES == frozenset({2})
    assert module.API == "moxi_sessions.api"
    assert module.CLI == "moxi_sessions.cli:main"
    assert module.SETTINGS == "settings.toml"
    assert module.OPTIONAL == {} and module.GATED == {} and module.INTERNAL_ENV == {}


def test_the_announcement_declares_its_doctor_and_no_store():
    assert module.ANNOUNCES == 1
    assert module.STORES == {}
    assert module.DOCTOR == "moxi_sessions.doctor:rows"
    target, _, name = module.DOCTOR.partition(":")
    assert callable(getattr(importlib.import_module(target), name))
    for name in ("DAEMONS", "TIMERS", "HEALTH", "BUDGETS"):
        assert not hasattr(module, name)


def test_the_package_is_version_0_2():
    assert metadata.version("moxi-sessions").startswith("0.2.")


def test_load_finds_the_module_and_its_api():
    modules.reset()
    found = modules.load("sessions", interface=2)
    assert found.ok, found
    assert found.api is api
    assert not modules.load("sessions", interface=1).ok


def test_interface_2_keeps_what_cache_reads():
    assert {"claude", "codex", "read_back", "projects_root"} <= set(api.__all__)
    assert CLAUDE_NAMES <= set(dir(api.claude))
    assert CODEX_NAMES <= set(dir(api.codex))


def test_the_interface_signatures_hold_their_parameters():
    sig = inspect.signature(api.read_back)
    assert list(sig.parameters)[:4] == ["path", "block", "limit", "head"]
    assert sig.parameters["block"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["block"].default is inspect.Parameter.empty
    assert sig.parameters["limit"].default is inspect.Parameter.empty
    assert sig.parameters["head"].default is False
    assert list(inspect.signature(api.projects_root).parameters) == ["environ"]
    assert list(inspect.signature(api.codex.find_rollout).parameters) == [
        "sessions_root", "thread_id"]


def test_interface_2_finds_and_lists():
    assert {"find", "sources", "subagents", "Found", "Probe", "Hit", "Listing", "Listed",
            "SubagentListing", "Subagent", "Source", "Header", "Unreadable", "root",
            "root_origin"} <= set(api.__all__)
    for name in api.__all__:
        assert hasattr(api, name), name
    sig = inspect.signature(api.find)
    assert list(sig.parameters) == ["harness", "session_id", "root"]
    assert sig.parameters["root"].kind is inspect.Parameter.KEYWORD_ONLY
    sig = inspect.signature(api.sources)
    assert list(sig.parameters) == ["harness", "root", "modified_since", "heads"]
    assert sig.parameters["heads"].default is True
    assert list(inspect.signature(api.subagents).parameters) == ["source"]


def test_the_schema_holds_the_three_roots():
    schema = Schema.from_resource("moxi_sessions", "settings.toml")
    assert list(schema.keys) == ["claude.projects", "codex.sessions", "opencode.database"]
    for name in schema.keys:
        key = schema.keys[name]
        assert (key.type, key.default, key.read, key.env) == ("path", "", "use", None)
