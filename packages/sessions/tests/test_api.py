"""The announcement and interface 1, as other modules reach them through `moxi.modules`."""

from __future__ import annotations

import inspect

from moxi import modules
from moxi.config import Schema
from moxi_sessions import api, module

CLAUDE_NAMES = {"parse_line", "iter_entries", "stream_entries", "parse_timestamp",
                "find_for_session", "probe_root", "search_roots", "MALFORMED", "RootProbe"}
CODEX_NAMES = {"find_rollout", "stream_rollout", "NAME_RE"}


def test_the_announcement_offers_interface_1():
    assert module.INTERFACES == frozenset({1})
    assert module.API == "moxi_sessions.api"
    assert module.CLI == "moxi_sessions.cli:main"
    assert module.SETTINGS == "settings.toml"
    assert module.OPTIONAL == {} and module.GATED == {} and module.INTERNAL_ENV == {}


def test_load_finds_the_module_and_its_api():
    modules.reset()
    found = modules.load("sessions", interface=1)
    assert found.ok, found
    assert found.api is api


def test_interface_1_holds_its_names():
    assert set(api.__all__) == {"claude", "codex", "read_back", "projects_root"}
    assert CLAUDE_NAMES <= set(dir(api.claude))
    assert CODEX_NAMES <= set(dir(api.codex))


def test_the_interface_signatures_hold_their_parameters():
    sig = inspect.signature(api.read_back)
    assert list(sig.parameters) == ["path", "block", "limit", "head"]
    assert sig.parameters["block"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["block"].default is inspect.Parameter.empty
    assert sig.parameters["limit"].default is inspect.Parameter.empty
    assert sig.parameters["head"].default is False
    assert list(inspect.signature(api.projects_root).parameters) == ["environ"]
    assert list(inspect.signature(api.codex.find_rollout).parameters) == [
        "sessions_root", "thread_id"]


def test_the_schema_holds_claude_projects_only():
    schema = Schema.from_resource("moxi_sessions", "settings.toml")
    assert list(schema.keys) == ["claude.projects"]
    key = schema.keys["claude.projects"]
    assert (key.type, key.default, key.read, key.env) == ("path", "", "use", None)
