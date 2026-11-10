"""The settings view: descriptors from `moxi config show --json`, switch tables as one
toggle per entry, and writes through `moxi config set` and `unset` in a child, both
through the functions and through a real shell."""

from __future__ import annotations

import json
import socket
import subprocess

import pytest
from moxi_ui.loopback.api import Conflict, Refused
from moxi.paths import Layout, python
from moxi_ui import settings
from moxi_ui.page import PAGE
from moxi_ui.settings import MARK_NEXT_START, MARK_NOW, MARK_SWITCH
from test_snapshot import site  # noqa: F401 - the fixture


def _key(name, type_, value, default=None, *, read="use", source="default", **extra):
    return {"name": name, "type": type_, "default": default, "doc": f"the {name}",
            "values": None, "read": read, "env": None, "needs": None, "items": None,
            "value": value, "source": source, **extra}


#: A recorded `show --json` with a key of each of the eight types.
EIGHT = [
    _key("on", "bool", True, False, source="file"),
    _key("count", "int", 3, 1, read="start"),
    _key("ratio", "float", 0.5, 1.0),
    _key("name", "string", "-x y", "", env="MOXI_DEMO_NAME", source="env"),
    _key("dir", "path", "/a/b", None),
    _key("mode", "enum", "fast", "slow", values=["slow", "fast"]),
    _key("tags", "list", ["a", "b c"], [], items="str"),
    _key("extra", "table", {"a": 1, "b c": [True, "x"]}, {}),
]


def _by_name(keys):
    return {key["name"]: key for key in keys}


def test_each_of_the_eight_types_gives_a_descriptor_spelled_as_set_takes_it():
    keys = _by_name(settings.descriptor("demo", item, present=["demo"]) for item in EIGHT)
    assert {name: key["value"] for name, key in keys.items()} == {
        "on": "true", "count": "3", "ratio": "0.5", "name": "-x y", "dir": "/a/b",
        "mode": "fast", "tags": '["a", "b c"]', "extra": '{a = 1, "b c" = [true, "x"]}'}
    assert keys["tags"]["value_items"] == ["a", "b c"] and keys["on"]["value_items"] is None
    assert keys["dir"]["default"] is None and keys["count"]["default"] == "1"
    assert keys["mode"]["values"] == ["slow", "fast"]
    assert keys["count"]["mark"] == MARK_NEXT_START and keys["on"]["mark"] == MARK_NOW
    assert "MOXI_DEMO_NAME" in keys["name"]["note"] and keys["on"]["note"] is None
    assert not any(key["switch_table"] or key["entries"] for key in keys.values())
    # A table that is not a switch table is a TOML text box: no entries.
    assert keys["extra"]["type"] == "table" and keys["extra"]["entries"] is None
    # Every descriptor holds exactly the keys the route declares.
    assert all(set(key) == set(settings.KEY_SHAPE.items) for key in keys.values())


def test_the_marks_a_daemon_switch_the_shell_port_and_an_absent_module():
    switch = _key("enabled", "bool", False, False)
    port = _key("port", "int", 8460, 8460, read="start")
    needs = _key("hook", "bool", False, False, needs="turns")
    assert settings.descriptor("demo", switch, switches={"enabled"})["mark"] == MARK_SWITCH
    assert settings.descriptor("ui", port, platform="linux")["mark"] == \
        "applies after `moxi install ui`"
    assert settings.descriptor("ui", port, platform="win32")["mark"] == \
        "applies when the shell next starts"
    off = settings.descriptor("demo", needs, present=["demo"])
    assert off["needs_absent"] and "turns" in off["note"]
    assert not settings.descriptor("demo", needs, present=["demo", "turns"])["needs_absent"]
    assert settings.switches_of({"daemons": {"d": {"switch": "enabled"}, "e": {}}}) == {"enabled"}


def _switch_table(**extra):
    return _key("jobs", "table", {"a": True, "b": False}, {}, source="file", items="bool",
                switch_table=True, **extra)


def test_a_switch_table_is_one_toggle_per_entry_the_union_of_file_and_names():
    item = _switch_table(entries=[
        {"name": "a", "value": True, "source": "file", "named_by": ["timer:sweep"]},
        {"name": "b", "value": False, "source": "file", "named_by": []},
        {"name": "c", "value": False, "source": "default", "named_by": ["daemon:watch"]},
    ])
    key = settings.descriptor("demo", item)
    assert key["switch_table"] and key["declarations"] is None
    toggles = {entry["name"]: entry for entry in key["entries"]}
    assert [(e["key"], e["value"], e["source"]) for e in key["entries"]] == [
        ("jobs.a", True, "file"), ("jobs.b", False, "file"), ("jobs.c", False, "default")]
    assert toggles["c"]["mark"] == MARK_SWITCH
    assert toggles["a"]["mark"] == MARK_NOW and toggles["b"]["mark"] == MARK_NOW
    # A toggle writes its entry; reset unsets that entry.
    assert settings.change_argv("demo", "jobs.c", value="true")[5:] == [
        "set", "--by", "ui", "--", "demo", "jobs.c", "true"]
    assert settings.change_argv("demo", "jobs.c", unset=True)[5:] == [
        "unset", "--by", "ui", "--", "demo", "jobs.c"]


def test_a_switch_table_whose_declarations_are_unavailable_shows_the_file_and_why():
    item = _switch_table(entries=[
        {"name": "a", "value": True, "source": "file", "named_by": []},
        {"name": "b", "value": False, "source": "file", "named_by": []}],
        declarations="unavailable", reason="UNITS_FROM failed: boom")
    key = settings.descriptor("demo", item)
    assert [entry["key"] for entry in key["entries"]] == ["jobs.a", "jobs.b"]
    assert key["declarations"] == "unavailable"
    assert "UNITS_FROM failed: boom" in key["reason"] and "can't be listed" in key["reason"]


# --- the commands, recorded --------------------------------------------------------------


class Run:
    def __init__(self, code=0, stdout=b"", stderr=b"", raises=None):
        self.code, self.stdout, self.stderr, self.raises = code, stdout, stderr, raises
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.code, self.stdout, self.stderr)


def test_every_command_is_python_dash_p_dash_m_moxi_from_the_runtime_dir():
    run = Run(stdout=json.dumps(EIGHT).encode())
    section = settings.read_section("demo", cwd="/rt", env={"A": "1"}, run=run)
    assert section["error"] is None and len(section["keys"]) == 8
    argv, kwargs = run.calls[0]
    assert argv[:4] == [str(python()), "-P", "-m", "moxi"]
    assert argv[4:] == ["config", "demo", "show", "--json"]
    assert kwargs == {"timeout_s": settings.CONFIG_TIMEOUT, "cwd": "/rt", "env": {"A": "1"}}
    settings.change("demo", "count", value="-4", cwd="/rt", env=None, run=run)
    assert run.calls[-1][0][:4] == [str(python()), "-P", "-m", "moxi"]
    assert run.calls[-1][0][-3:] == ["demo", "count", "-4"]
    settings.change("demo", "tags", add="-z", cwd="/rt", env=None, run=run)
    assert "--add=-z" in run.calls[-1][0]


def test_a_file_that_wont_parse_makes_the_section_read_only_with_its_text():
    run = Run(code=1, stdout=json.dumps({"error": "bad TOML at line 2", "fix": "fix it"}).encode())
    section = settings.read_section("demo", cwd="/rt", env=None, run=run)
    assert section == {"module": "demo", "keys": [], "error": "bad TOML at line 2",
                       "fix": "fix it", "readonly": True}
    gone = settings.read_section("demo", cwd="/rt", env=None,
                                 run=Run(code=2, stderr=b"moxi: demo is not installed\n"))
    assert gone["readonly"] and gone["error"] == "demo is not installed"
    slow = settings.read_section("demo", cwd="/rt", env=None,
                                 run=Run(raises=subprocess.TimeoutExpired("x", 10)))
    assert slow["readonly"] and "ran past" in slow["error"]


def test_the_exit_codes_of_a_write():
    ok = settings.change("demo", "on", value="true", cwd="/rt", env=None,
                         run=Run(stdout=b"demo: on set (by ui)\nwatch: started\n"))
    assert ok == {"ok": True, "lines": ["demo: on set (by ui)", "watch: started"],
                  "warning": None, "fix": None}
    with pytest.raises(Refused) as refused:
        settings.change("demo", "count", value="x", cwd="/rt", env=None,
                        run=Run(code=2, stderr=b"moxi: key 'count': not an int; fix: give an int\n"))
    assert refused.value.text == "key 'count': not an int; fix: give an int"
    with pytest.raises(Conflict) as busy:
        settings.change("demo", "count", value="2", cwd="/rt", env=None,
                        run=Run(code=75, stderr=b"moxi: locked\n"))
    assert busy.value.text.startswith("settings file busy")
    applied = settings.change("demo", "on", value="false", cwd="/rt", env=None,
                              run=Run(code=1, stdout=b"demo: on set (by ui)\nwatch: stop failed\n"))
    assert applied["ok"] and applied["warning"] and applied["fix"] == "moxi install demo"
    assert applied["lines"][-1] == "watch: stop failed"
    with pytest.raises(Refused):
        settings.change("Not A Module", "on", value="x", cwd="/rt", env=None, run=Run())
    with pytest.raises(Refused):
        settings.change("demo", "on", cwd="/rt", env=None, run=Run())


# --- a real `moxi config` ----------------------------------------------------------------


def test_a_write_runs_moxi_config_in_a_child_and_a_refused_value_carries_its_text():
    cwd = str(Layout("ui").runtime_dir(create=True))
    done = settings.change("ui", "idle_s", value="1200", cwd=cwd, env=None)
    assert done["ok"] and done["lines"] == ["ui: idle_s set (by ui)"]
    assert "idle_s = 1200" in Layout("ui").config_file().read_text()
    with pytest.raises(Refused) as refused:
        settings.change("ui", "idle_s", value="many", cwd=cwd, env=None)
    assert "not an int" in refused.value.text and "fix" in refused.value.text
    section = settings.read_section("ui", cwd=cwd, env=None)
    keys = _by_name(section["keys"])
    assert keys["idle_s"]["value"] == "1200" and keys["idle_s"]["source"] == "file"
    assert keys["port"]["mark"] in ("applies after `moxi install ui`",
                                    "applies when the shell next starts")
    settings.change("ui", "idle_s", unset=True, cwd=cwd, env=None)
    keys = _by_name(settings.read_section("ui", cwd=cwd, env=None)["keys"])
    assert keys["idle_s"]["source"] == "default"


def test_the_settings_routes_are_on_the_shells_page():
    paths = {route.path: route for route in PAGE.routes}
    assert paths["/api/settings"].kind == "read"
    assert paths["/api/settings/set"].kind == "act"
    assert paths["/api/settings/unset"].kind == "act"


# --- through a real shell ----------------------------------------------------------------


def _request(port, method, path, browser, body=None):
    data = b"" if body is None else json.dumps(body).encode()
    lines = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{port}", *browser,
             f"Origin: http://127.0.0.1:{port}"]
    if body is not None:
        lines += ["Content-Type: application/json", f"Content-Length: {len(data)}"]
    head = "\r\n".join([*lines, "Connection: close", "", ""]).encode()
    with socket.create_connection(("127.0.0.1", port), timeout=30) as sock:
        sock.sendall(head + data)
        answer = b""
        while chunk := sock.recv(65536):
            answer += chunk
    status = int(answer.split(b" ", 2)[1])
    return status, json.loads(answer.split(b"\r\n\r\n", 1)[1] or b"null")


def test_the_shell_reads_and_writes_settings_and_a_refused_value_is_a_422(site):
    from moxi_ui.snapshot import ModuleSnapshot
    from test_snapshot import _serve

    shell, thread = _serve(snapshot=ModuleSnapshot(cwd=Layout("ui").runtime_dir(),
                                                   dirs=[str(site)]))
    try:
        session_id, key = shell.sessions.store.mint()
        browser = [f"Cookie: moxi-ui={session_id}", f"X-Moxi-Key: {key}",
                   "Sec-Fetch-Site: same-origin", "Sec-Fetch-Mode: cors", "Sec-Fetch-Dest: empty"]
        port = shell.listeners.port
        status, data = _request(port, "GET", "/m/ui/api/settings", browser)
        assert status == 200 and data["error"] is None
        sections = {section["module"]: section for section in data["sections"]}
        assert list(sections)[0] == "moxi" and "ui" in sections
        assert {k["name"] for k in sections["ui"]["keys"]} == {
            "port", "idle_s", "theme.mode", "theme.palette", "gateway.enabled", "gateway.port"}
        status, data = _request(port, "POST", "/m/ui/api/settings/set", browser,
                                {"module": "ui", "key": "idle_s", "value": "nope"})
        assert status == 422 and "not an int" in data["refused"]
        status, data = _request(port, "POST", "/m/ui/api/settings/set", browser,
                                {"module": "ui", "key": "idle_s", "value": "60"})
        assert status == 200 and data["ok"] and data["warning"] is None
        status, data = _request(port, "GET", "/m/ui/api/settings?module=ui", browser)
        assert [s["module"] for s in data["sections"]] == ["ui"]
        assert _by_name(data["sections"][0]["keys"])["idle_s"]["value"] == "60"
        status, data = _request(port, "POST", "/m/ui/api/settings/unset", browser,
                                {"module": "ui", "key": "idle_s"})
        assert status == 200 and data["ok"]
        # Without the key, the routes are refused.
        assert _request(port, "GET", "/m/ui/api/settings", browser[:1] + browser[2:])[0] == 403
    finally:
        shell.stop()
        thread.join(15)
        shell.close()


# --- locked settings ---------------------------------------------------------------------

LOCKED_TEXT = "{} {} is locked for this run of the shell, so it can't be changed from a page"


@pytest.mark.parametrize("text, entries", [
    (None, set()),
    ("", set()),
    (" ", set()),
    (",", set()),
    ("ui.port", {"ui.port"}),
    ("ui.port,turns.guards.run", {"ui.port", "turns.guards.run"}),
    ("ui.port turns.guards.run\tmoxi.log", {"ui.port", "turns.guards.run", "moxi.log"}),
    (" ui.port ,\n turns ", {"ui.port", "turns"}),
    ("a,,b,", {"a", "b"}),
])
def test_the_locked_list_is_split_at_commas_and_spaces_and_an_empty_piece_is_no_entry(
        text, entries):
    assert settings.locked_entries(text) == entries


def test_a_doubled_and_a_trailing_comma_lock_no_other_key():
    locked = settings.locked_entries("a,,b,")
    assert settings.is_locked("a", "x", locked) and settings.is_locked("b", "x.y", locked)
    for module, key in (("c", "x"), ("ab", "x"), ("moxi", "a"), ("ui", "port"), ("c", "")):
        assert not settings.is_locked(module, key, locked)


def test_an_entry_locks_its_key_and_the_keys_under_it_and_no_key_that_starts_alike():
    assert settings.is_locked("ui", "gateway.port", {"ui.gateway.port"})
    assert not settings.is_locked("ui", "gateway.enabled", {"ui.gateway.port"})
    for key in ("gateway.port", "gateway.enabled"):
        assert settings.is_locked("ui", key, {"ui.gateway"})
    assert not settings.is_locked("ui", "port", {"ui.gateway"})
    # Only a whole name, or a whole name and a dot, matches.
    for entry in ("ui.gateway.p", "ui.gate", "u", "ui.gateway.port.x", "UI.gateway.port",
                  "ui.gateway."):
        assert not settings.is_locked("ui", "gateway.port", {entry})
    # A module's name alone locks all of its settings, and no other module's.
    assert settings.is_locked("turns", "guards.run", {"turns"})
    assert not settings.is_locked("turnstile", "guards.run", {"turns"})
    # An entry that names no setting locks nothing.
    assert not settings.is_locked("ui", "port", {"ui.nosuch", "nosuch.port"})
    assert not settings.is_locked("ui", "port", ())


@pytest.mark.parametrize("write", [{"value": "60"}, {"add": "x"}, {"remove": "x"},
                                   {"unset": True}], ids=["value", "add", "remove", "unset"])
def test_each_form_of_write_to_a_locked_key_is_refused_before_any_command_runs(write):
    for locked, key in (({"ui.idle_s"}, "idle_s"), ({"ui.gateway"}, "gateway.port"),
                        ({"ui.gateway"}, "gateway.enabled"), ({"ui.jobs"}, "jobs.c"),
                        ({"ui"}, "port")):
        run = Run()
        with pytest.raises(Refused) as refused:
            settings.change("ui", key, cwd="/rt", env=None, locked=locked, run=run, **write)
        assert refused.value.text == LOCKED_TEXT.format("ui", key)
        assert run.calls == []


@pytest.mark.parametrize("locked", [(), settings.locked_entries(""), {"ui.gateway.p"},
                                    {"ui.idle"}, {"ui.idle_s.x"}, {"demo.idle_s"}],
                         ids=["unset", "empty", "a-longer-name", "a-shorter-name",
                              "a-key-under-it", "another-module"])
def test_a_key_no_entry_names_is_written_in_each_form(locked):
    for write in ({"value": "60"}, {"add": "x"}, {"remove": "x"}, {"unset": True}):
        run = Run()
        for key in ("idle_s", "gateway.port"):
            assert settings.change("ui", key, cwd="/rt", env=None, locked=locked, run=run,
                                   **write)["ok"]
        assert [[key for key in ("idle_s", "gateway.port") if key in argv]
                for argv, _ in run.calls] == [["idle_s"], ["gateway.port"]]


def test_the_read_marks_exactly_the_locked_keys_and_entries():
    table = _switch_table(entries=[
        {"name": "a", "value": True, "source": "file", "named_by": []},
        {"name": "b", "value": False, "source": "file", "named_by": []}])
    run = Run(stdout=json.dumps([*EIGHT, table]).encode())

    def marked(locked):
        keys = settings.read_section("demo", cwd="/rt", env=None, locked=locked,
                                     run=run)["keys"]
        return ({key["name"] for key in keys if key["locked"]},
                {entry["key"] for key in keys for entry in key["entries"] or ()
                 if entry["locked"]})

    assert marked(()) == (set(), set())
    assert marked(settings.locked_entries("")) == (set(), set())
    assert marked({"demo.count", "demo.nosuch", "other.on"}) == ({"count"}, set())
    # An entry of a switch table is locked alone, or with its table.
    assert marked({"demo.jobs.a"}) == (set(), {"jobs.a"})
    assert marked({"demo.jobs"}) == ({"jobs"}, {"jobs.a", "jobs.b"})
    assert marked({"demo"}) == ({key["name"] for key in [*EIGHT, table]}, {"jobs.a", "jobs.b"})
    assert marked({"demo.job", "demo.jobs.", "demo.co"}) == (set(), set())
    # The mark is part of the declared shapes.
    assert settings.KEY_SHAPE.items["locked"].type == "bool"
    assert settings.KEY_SHAPE.items["entries"].items.items["locked"].type == "bool"


def test_the_variable_is_one_constant_that_the_module_declares():
    from moxi_ui import module

    assert settings.LOCKED_ENV == "MOXI_UI_LOCKED_SETTINGS"
    assert list(module.INTERNAL_ENV) == [settings.LOCKED_ENV]


class _Shell:
    """A real shell over the `site` fixture, with a signed-in session's requests."""

    def __init__(self, site):
        from moxi_ui.snapshot import ModuleSnapshot
        from test_snapshot import _serve

        self.shell, self.thread = _serve(snapshot=ModuleSnapshot(
            cwd=Layout("ui").runtime_dir(), dirs=[str(site)]))
        session_id, key = self.shell.sessions.store.mint()
        self.browser = [f"Cookie: moxi-ui={session_id}", f"X-Moxi-Key: {key}",
                        "Sec-Fetch-Site: same-origin", "Sec-Fetch-Mode: cors",
                        "Sec-Fetch-Dest: empty"]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.shell.stop()
        self.thread.join(15)
        self.shell.close()

    def post(self, what, **body):
        return _request(self.shell.listeners.port, "POST", f"/m/ui/api/settings/{what}",
                        self.browser, {"module": "ui", **body})

    def keys(self):
        status, data = _request(self.shell.listeners.port, "GET",
                                "/m/ui/api/settings?module=ui", self.browser)
        assert status == 200
        return _by_name(data["sections"][0]["keys"])


def _history():
    from moxi.config import HISTORY
    from moxi.paths import core

    path = core().state_dir() / HISTORY
    return path.read_bytes() if path.exists() else None


def test_both_routes_refuse_a_locked_key_and_leave_the_file_and_the_history_alone(
        site, monkeypatch):
    monkeypatch.setenv(settings.LOCKED_ENV, "ui.idle_s,ui.gateway")
    config = Layout("ui").config_file()
    with _Shell(site) as shell:
        # A neighbour of the locked keys saves, before and after the refusals.
        status, data = shell.post("set", key="port", value="8499")
        assert status == 200 and data["ok"]
        file, history = config.read_bytes(), _history()
        assert b"8499" in file and history
        for what, body in (("set", {"key": "idle_s", "value": "60"}),
                           ("set", {"key": "idle_s", "add": "60"}),
                           ("set", {"key": "idle_s", "remove": "60"}),
                           ("unset", {"key": "idle_s"}),
                           ("set", {"key": "gateway.port", "value": "8498"}),
                           ("set", {"key": "gateway.enabled", "value": "true"}),
                           ("unset", {"key": "gateway.port"})):
            status, data = shell.post(what, **body)
            assert status == 422, (what, body, data)
            assert data == {"refused": LOCKED_TEXT.format("ui", body["key"])}
            assert config.read_bytes() == file and _history() == history
        keys = shell.keys()
        assert {name for name, key in keys.items() if key["locked"]} == {
            "idle_s", "gateway.enabled", "gateway.port"}
        assert keys["idle_s"]["source"] == "default" and keys["port"]["value"] == "8499"
        status, data = shell.post("unset", key="port")
        assert status == 200 and data["ok"]
        assert config.read_bytes() != file and _history() != history


@pytest.mark.parametrize("value", [None, "", " , "], ids=["unset", "empty", "separators"])
def test_with_no_entry_in_the_variable_nothing_is_marked_and_every_write_is_saved(
        site, monkeypatch, value):
    if value is None:
        monkeypatch.delenv(settings.LOCKED_ENV, raising=False)
    else:
        monkeypatch.setenv(settings.LOCKED_ENV, value)
    config = Layout("ui").config_file()
    with _Shell(site) as shell:
        assert not any(key["locked"] for key in shell.keys().values())
        for key, text in (("idle_s", "60"), ("gateway.port", "8498")):
            status, data = shell.post("set", key=key, value=text)
            assert status == 200 and data["ok"]
            assert shell.keys()[key]["value"] == text
            assert text in config.read_text()
            status, data = shell.post("unset", key=key)
            assert status == 200 and data["ok"]
            assert shell.keys()[key]["source"] == "default"
