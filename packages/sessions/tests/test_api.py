"""The announcement and the interfaces, as other modules reach them through `moxi.modules`."""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys
from importlib import metadata

import pytest

from conftest import write_config

from moxi import modules
from moxi.config import Schema
from moxi_sessions import api, module

CLAUDE_NAMES = {"parse_line", "iter_entries", "stream_entries", "parse_timestamp",
                "MALFORMED", "SAFE_ID_RE", "MISS"}
CODEX_NAMES = {"TOP_KINDS", "EVENT_KINDS", "RESPONSE_KINDS", "META_KEYS", "HISTORY_MODES",
               "CHECKED_VERSIONS"}


def test_the_announcement_offers_interfaces_2_3_and_4():
    assert module.INTERFACES == frozenset({2, 3, 4})
    assert module.API == "moxi_sessions.api"
    assert module.CLI == "moxi_sessions.cli:main"
    assert module.SETTINGS == "settings.toml"
    assert module.SETUP == "moxi_sessions.setup:setup"
    target, _, name = module.SETUP.partition(":")
    assert callable(getattr(importlib.import_module(target), name))
    assert set(module.OPTIONAL) == {"ui", "secrets", "turns"}
    assert module.GATED == {} and module.INTERNAL_ENV == {}


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
    assert modules.load("sessions", interface=3).ok
    assert modules.load("sessions", interface=4).ok
    assert not modules.load("sessions", interface=1).ok
    assert not modules.load("sessions", interface=5).ok


def test_interface_3_adds_retention():
    from moxi_sessions import retention
    assert {"retention", "Retention"} <= set(api.__all__)
    assert api.retention is retention.retention and api.Retention is retention.Retention
    assert list(inspect.signature(api.retention).parameters) == ["env"]


LIST_READS = ("sessions_list", "sessions_more", "sessions_newest", "sessions_folders",
              "session_opened")


def test_interface_4_adds_the_lists_reads_and_nothing_they_are_built_from():
    from moxi_sessions import lists

    before = {"find", "sources", "subagents", "window", "retention", "Retention"}
    assert before | set(LIST_READS) <= set(api.__all__)
    for name in ("Catalog", "list_recent_s", "catalog", "lists"):
        assert name not in api.__all__ and not hasattr(api, name), name
    for name in LIST_READS:
        read = getattr(api, name)
        assert read is getattr(lists, name)
        parameters = inspect.signature(read).parameters
        assert "cat" not in parameters, name
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters.values())
        if "recent_s" in parameters:
            assert parameters["recent_s"].default is None, name
    assert list(inspect.signature(api.sessions_list).parameters) == [
        "recent_s", "now", "harness", "since", "until", "folder", "find"]
    assert list(inspect.signature(api.session_opened).parameters) == [
        "harness", "session", "recent_s", "now"]
    assert "recent_s" not in inspect.signature(api.sessions_folders).parameters


def test_the_list_answers_through_the_api_over_the_roots_the_process_names(
        tmp_path, monkeypatch, own_config):
    from moxi_sessions import catalog

    claude_home, codex_home, data = (tmp_path / name for name in ("claude", "codex",
                                                                  "data"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    t = api.testing.ClaudeTranscript(claude_home / "projects", cwd="/work/proj-a")
    t.user("Do the thing.", origin="human")
    t.assistant("Done.")
    t.write()
    catalog.reset()
    try:
        answer = api.sessions_list()
        row = answer["groups"][0]["rows"][0]
        newest = api.sessions_newest(now=answer["now"])
        more = api.sessions_more(group="recent", now=answer["now"])
        folders = api.sessions_folders()
        opened = api.session_opened(harness="claude", session=row["session"],
                                    now=answer["now"])
        write_config(own_config, "[list]\nrecent_s = 0\n")
        by_setting = api.sessions_list(now=answer["now"] + 60)
        by_caller = api.sessions_list(now=answer["now"] + 60, recent_s=3600)
    finally:
        catalog.reset()

    assert (answer["all"], answer["total"]) == (1, 1)
    assert answer["harnesses"] == [{"harness": "claude", "count": 1}]
    assert answer["groups"][0]["key"] == "recent" and row["folder"] == "/work/proj-a"
    assert row["recent"] is True                  # the setting's hour
    assert newest["rows"] == [row] and more["rows"] == [row]
    assert folders["folders"][0]["folder"] == "/work/proj-a"
    assert opened["reply"]["text"] == "Done."
    assert by_setting["groups"][0]["count"] == 0  # no session is recent at 0 seconds
    assert by_caller["groups"][0]["count"] == 1


def test_the_schema_holds_the_roots_the_guards_switch_and_escapes_and_the_recent_time():
    schema = Schema.from_resource("moxi_sessions", "settings.toml")
    assert list(schema.keys) == ["claude.projects", "codex.sessions", "opencode.database",
                                 "guard.transcript_read", "guard.transcript_read_escapes",
                                 "list.recent_s"]
    key = schema.keys["list.recent_s"]
    assert (key.type, key.default, key.read, key.env) == ("int", 3600, "use", None)
    for name in ("claude.projects", "codex.sessions", "opencode.database"):
        key = schema.keys[name]
        assert (key.type, key.default, key.read, key.env) == ("path", "", "use", None)
    key = schema.keys["guard.transcript_read"]
    assert (key.type, key.default, key.read, key.env) == ("bool", False, "use", None)
    key = schema.keys["guard.transcript_read_escapes"]
    assert (key.type, key.items, key.read, key.env) == ("list", "str", "use", None)
    assert list(key.default) == ["sessions"]


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


def test_the_builders_are_reached_through_the_api():
    """Another module's tests import only sessions' API module, so the builders are a name
    on it; importing the API alone loads none of them (the test above)."""
    from moxi_sessions import testing
    assert "testing" in api.__all__
    assert api.testing is testing
    assert {"ClaudeTranscript", "CodexRollout", "OpenCodeDb"} <= set(dir(api.testing))
