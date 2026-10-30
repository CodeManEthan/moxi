"""The announcement and interface 2, as other modules reach them through `moxi.modules`."""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys
from importlib import metadata

import pytest

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


READ_NAMES = {"records", "records_back", "last_request", "records_since", "window", "Read",
              "Back", "LastRequest", "Batch", "Window", "CURSOR_VERSION", "BLOCK",
              "BACK_LIMIT", "WINDOW_LIMIT"}
SHAPE_NAMES = {"SHAPE", "UNKNOWN", "known", "Record", "Request", "Message", "ToolCall",
               "ToolResult", "Marker", "MARKERS", "LimitWindow", "Limits", "Malformed",
               "Other", "KINDS", "to_json", "from_json", "document"}


def test_interface_2_reads_and_holds_the_shape():
    from moxi_sessions import read, shape
    assert READ_NAMES | SHAPE_NAMES | {"opencode"} <= set(api.__all__)
    assert len(api.__all__) == len(set(api.__all__))
    for name in READ_NAMES:
        assert getattr(api, name) is getattr(read, name), name
    for name in SHAPE_NAMES:
        assert getattr(api, name) is getattr(shape, name), name
    assert api.SHAPE == 1
    assert set(api.KINDS) == {"request", "message", "tool_call", "tool_result", "marker",
                              "limits", "malformed", "other"}
    sig = inspect.signature(api.records_since)
    assert list(sig.parameters)[:4] == ["source", "cursor", "kinds", "max_bytes"]
    assert list(inspect.signature(api.last_request).parameters)[:3] == [
        "source", "synthetic", "limit"]
    assert list(inspect.signature(api.window).parameters)[:4] == [
        "source", "turns", "before", "limit"]


def test_the_api_lists_every_name_and_refuses_others():
    assert set(api.__all__) <= set(dir(api))
    with pytest.raises(AttributeError):
        api.no_such_name  # noqa: B018
    from moxi_sessions.api import Source, records  # noqa: F401  (resolves through import)


def test_importing_the_api_imports_none_of_its_names(tmp_path):
    probe = (
        "import sys\n"
        "import moxi_sessions.api as api\n"
        "print(sorted(m for m in sys.modules if m.startswith('moxi_sessions')))\n"
        "api.records, api.find, api.SHAPE\n"
        "print('sqlite3' in sys.modules)\n"
        "api.opencode.Snapshot\n"
        "print('sqlite3' in sys.modules)\n")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         cwd=tmp_path, check=True).stdout.splitlines()
    assert out == ["['moxi_sessions', 'moxi_sessions.api']", "False", "False"]
