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
    _key("on", "bool", value=True, default=False, source="file"),
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
    switch = _key("enabled", "bool", value=False, default=False)
    port = _key("port", "int", 8460, 8460, read="start")
    needs = _key("hook", "bool", value=False, default=False, needs="turns")
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


# --- what the page reads beside each key --------------------------------------------------


PAGE_FIELDS = {"label": "Port", "line": "Where it listens.", "common": True, "group": "Waits",
               "unit": "seconds", "pick": True, "elsewhere": None,
               "choices": [{"value": "slow", "words": "Slow"}],
               "hurts": {"when": "off", "says": "turns it off", "confirm": "It goes.",
                         "stays": False},
               "terminal": {"on": "moxi demo enable", "off": "moxi demo disable"}}


def test_a_key_carries_its_page_fields_and_an_older_core_without_them_reads_as_none():
    key = settings.descriptor("demo", _key("count", "int", 3, 1, **PAGE_FIELDS))
    assert {field: key[field] for field in PAGE_FIELDS} == PAGE_FIELDS
    bare = settings.descriptor("demo", _key("count", "int", 3, 1))
    assert {field: bare[field] for field in PAGE_FIELDS} == {
        "label": None, "line": None, "common": False, "group": None, "unit": None,
        "pick": False, "elsewhere": None, "choices": None, "hurts": None, "terminal": None}
    # A field of the wrong shape reads as absent, so the answer keeps its declared shape.
    odd = settings.descriptor("demo", _key("count", "int", 3, 1, label=4, common="yes",
                                           hurts={"when": "off"}, terminal={"on": "x"},
                                           choices=[{"value": "a"}, "b"]))
    assert odd["label"] is None and odd["common"] is False and odd["hurts"] is None
    assert odd["terminal"] is None and odd["choices"] == []
    from moxi_ui.loopback.routes import project
    project(key, settings.KEY_SHAPE, strict=True)
    project(bare, settings.KEY_SHAPE, strict=True)


def test_each_key_carries_its_value_as_json_and_whether_it_is_not_the_default():
    keys = _by_name(settings.descriptor("demo", item) for item in EIGHT)
    assert {name: json.loads(key["json"]) for name, key in keys.items()} == {
        item["name"]: item["value"] for item in EIGHT}
    assert {name: json.loads(key["default_json"]) for name, key in keys.items()} == {
        item["name"]: item["default"] for item in EIGHT}
    assert {name for name, key in keys.items() if key["changed"]} == {
        "on", "count", "ratio", "name", "dir", "mode", "tags", "extra"}
    same = [_key("on", "bool", value=False, default=False), _key("count", "int", 1, 1),
            _key("ratio", "float", 1.0, 1), _key("tags", "list", ["a"], ["a"]),
            _key("extra", "table", {"a": [1]}, {"a": [1]}),
            # A path whose default is empty reads none.
            _key("dir", "path", None, ""),
            _key("mode", "enum", "slow", "slow", values=["slow", "fast"])]
    assert not any(settings.descriptor("demo", item)["changed"] for item in same)
    assert settings.descriptor("demo", _key("dir", "path", "/a", ""))["json"] == '"/a"'
    assert settings.descriptor("demo", _key("dir", "path", None, ""))["json"] == "null"
    # A path default under the home folder reads as the value the home gives it.
    env = {"HOME": "/h"}
    assert not settings.descriptor("demo", _key("dir", "path", "/h/x", "~/x"), env=env)["changed"]
    assert settings.descriptor("demo", _key("dir", "path", "/h/y", "~/x"), env=env)["changed"]
    # A list of tables travels whole, so the page never parses TOML.
    rows = [{"name": "claude", "skills": ""}, {"name": "codex", "skills": "/s"}]
    assert json.loads(settings.descriptor("moxi", _key("harnesses", "list", rows, []))["json"]) \
        == rows
    assert json.loads(settings.descriptor("moxi", _key("harnesses", "list", [], rows))[
        "default_json"]) == rows
    # A value JSON can't hold reads null.
    assert settings.descriptor("demo", _key("ratio", "float", float("nan"), 1.0))["json"] == "null"


def test_a_switch_table_is_changed_when_any_entry_is_on():
    def changed(*values):
        return settings.descriptor("demo", _switch_table(entries=[
            {"name": n, "value": v, "source": "file", "named_by": []}
            for n, v in zip("abc", values, strict=False)]))["changed"]

    on, off = True, False
    assert changed(on, off) and changed(off, off, on)
    assert not changed(off, off) and not changed()


def test_a_sections_count_leaves_out_what_the_page_does_not_draw_among_its_rows():
    items = [_key("a", "bool", value=False, default=False), _key("b", "int", 1, 1),
             _key("hook", "bool", value=False, default=False, needs="turns"),
             _key("name.template", "string", "x", "x", elsewhere="Edited in Home."),
             _key("gateway.port", "int", 1, 1), _key("gateway.enabled", "bool", value=False,
                                                       default=False)]
    listing = {"demo": {"state": "present"}, "ui": {"state": "present"}}
    run = Run(stdout=json.dumps(items).encode(), schema=None)
    demo = settings.read_section("demo", cwd="/rt", env=None, listing=listing, run=run)
    # Another module's gateway keys are its own rows.
    assert demo["count"] == 4 and len(demo["keys"]) == 6
    ui = settings.read_section("ui", cwd="/rt", env=None, listing=listing, run=run)
    assert ui["count"] == 2
    # A key needing a present module is drawn.
    listing["turns"] = {"state": "present"}
    assert settings.read_section("demo", cwd="/rt", env=None, listing=listing,
                                 run=run)["count"] == 5
    assert not settings.is_drawn("demo", {"name": "a", "needs_absent": True})
    assert settings.is_drawn("demo", {"name": "a", "needs_absent": False, "elsewhere": None})


def test_a_sections_file_writes_the_home_as_a_tilde(tmp_path):
    env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / ".config")}
    assert settings.file_of("demo", env) == "~/.config/moxi/demo/config.toml"
    assert settings.file_of("moxi", env) == "~/.config/moxi/moxi/config.toml"
    elsewhere = {"HOME": str(tmp_path / "h"), "XDG_CONFIG_HOME": str(tmp_path / "conf")}
    assert settings.file_of("demo", elsewhere) == str(tmp_path / "conf/moxi/demo/config.toml")
    # A home that is only a prefix of the path's folder is not the home folder.
    near = {"HOME": str(tmp_path / "c"), "XDG_CONFIG_HOME": str(tmp_path / "conf")}
    assert not settings.file_of("demo", near).startswith("~")
    assert settings.file_of("Not A Module", env) is None
    run = Run(stdout=b"[]", schema=None)
    assert settings.read_section("demo", cwd="/rt", env=env, run=run)["file"] == \
        "~/.config/moxi/demo/config.toml"


# --- the commands, recorded --------------------------------------------------------------


#: The schema read a recorded write is checked against: every key the recorded writes
#: name, and two switch tables.
SCHEMA = [*EIGHT, *(_key(name, "int", 1, 1) for name in (
    "idle_s", "port", "gateway.port", "adopt", "adopted_too", "gateway")),
    _key("gateway.enabled", "bool", value=False, default=False,
         terminal={"on": "moxi ui gateway enable", "off": "moxi ui gateway disable"}),
    _key("jobs", "table", {}, {}, items="bool", switch_table=True, entries=[
        {"name": "c", "value": False, "source": "default", "named_by": ["daemon:watch"]}]),
    _key("adopted", "table", {}, {}, items="bool", switch_table=True, entries=[])]


class Run:
    """A recorded `moxi config`: a `show` answers `schema` (when it is not None), and
    every other run answers `code`, `stdout` and `stderr`, or raises `raises`."""

    def __init__(self, code=0, stdout=b"", stderr=b"", raises=None, schema=SCHEMA):
        self.code, self.stdout, self.stderr, self.raises = code, stdout, stderr, raises
        self.schema = schema
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.schema is not None and "show" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.schema).encode(), b"")
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.code, self.stdout, self.stderr)

    @property
    def writes(self):
        """The runs that were not a schema read."""
        return [(argv, kwargs) for argv, kwargs in self.calls if "show" not in argv]


def test_every_command_is_python_dash_p_dash_m_moxi_from_the_runtime_dir():
    run = Run(stdout=json.dumps(EIGHT).encode(), schema=None)
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
    run = Run(code=1, stdout=json.dumps({"error": "bad TOML at line 2", "fix": "fix it"}).encode(),
              schema=None)
    section = settings.read_section("demo", cwd="/rt", env=None, run=run)
    assert section == {"module": "demo", "keys": [], "error": "bad TOML at line 2",
                       "fix": "fix it", "readonly": True,
                       "file": settings.file_of("demo"), "count": 0}
    gone = settings.read_section("demo", cwd="/rt", env=None, run=Run(
        code=2, stderr=b"moxi: demo is not installed\n", schema=None))
    assert gone["readonly"] and gone["error"] == "demo is not installed"
    slow = settings.read_section("demo", cwd="/rt", env=None, run=Run(
        raises=subprocess.TimeoutExpired("x", 10), schema=None))
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
    assert keys["idle_s"]["changed"] and keys["idle_s"]["json"] == "1200"
    assert keys["port"]["label"] == "Port" and not keys["port"]["changed"]
    assert keys["theme.palette"]["choices"][1] == {"value": "midnight-neon",
                                                   "words": "Midnight neon"}
    assert keys["gateway.enabled"]["terminal"]["off"] == "moxi ui gateway disable"
    # Every key but the two gateway keys is drawn among ui's rows.
    assert section["count"] == 4 and section["file"] == settings.file_of("ui")
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
        assert data["listed"] == list(sections)
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
        assert data["listed"][0] == "moxi" and "ui" in data["listed"]
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
    for entry in ("ui.gateway.p", "ui.gate", "u", "UI.gateway.port", "ui.gateway.",
                  "ui.gateway.portal.x"):
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


def test_an_entry_locks_the_keys_above_it_and_no_key_beside_it():
    locked = {"services.adopted.zz"}
    assert settings.is_locked("services", "adopted", locked)
    assert settings.is_locked("services", "adopted.zz", locked)
    # A key beside the locked one, and one that shares letters without a dot.
    for key in ("adopted.yy", "adopted.z", "adopt", "adopted_too", "adopte", "other"):
        assert not settings.is_locked("services", key, locked)
    assert not settings.is_locked("service", "adopted", locked)
    # Every key above it, however deep the entry sits.
    assert settings.is_locked("ui", "gateway", {"ui.gateway.port.extra"})
    assert settings.is_locked("ui", "gateway.port", {"ui.gateway.port.extra"})
    assert not settings.is_locked("ui", "gateway.enabled", {"ui.gateway.port.extra"})


@pytest.mark.parametrize("write", [{"value": "{zz = true}"}, {"add": "x"}, {"remove": "x"},
                                   {"unset": True}], ids=["value", "add", "remove", "unset"])
def test_each_form_of_write_to_a_key_that_holds_a_locked_entry_is_refused(write):
    locked = {"services.adopted.zz"}
    run = Run()
    with pytest.raises(Refused) as refused:
        settings.change("services", "adopted", cwd="/rt", env=None, locked=locked, run=run,
                        **write)
    # The refusal names the key the write named.
    assert refused.value.text == LOCKED_TEXT.format("services", "adopted")
    assert run.calls == []
    for key in ("adopted.yy", "adopt", "adopted_too"):
        assert settings.change("services", key, cwd="/rt", env=None, locked=locked, run=run,
                               **write)["ok"]
    assert len(run.writes) == 3


@pytest.mark.parametrize("locked", [(), settings.locked_entries(""), {"ui.gateway.p"},
                                    {"ui.idle"}, {"ui.idle_sx.x"}, {"demo.idle_s"}],
                         ids=["unset", "empty", "a-longer-name", "a-shorter-name",
                              "a-key-under-a-longer-name", "another-module"])
def test_a_key_no_entry_names_is_written_in_each_form(locked):
    for write in ({"value": "60"}, {"add": "x"}, {"remove": "x"}, {"unset": True}):
        run = Run()
        for key in ("idle_s", "gateway.port"):
            assert settings.change("ui", key, cwd="/rt", env=None, locked=locked, run=run,
                                   **write)["ok"]
        assert [[key for key in ("idle_s", "gateway.port") if key in argv]
                for argv, _ in run.writes] == [["idle_s"], ["gateway.port"]]


def test_the_read_marks_exactly_the_locked_keys_and_entries():
    table = _switch_table(entries=[
        {"name": "a", "value": True, "source": "file", "named_by": []},
        {"name": "b", "value": False, "source": "file", "named_by": []}])
    run = Run(stdout=json.dumps([*EIGHT, table]).encode(), schema=None)

    def marked(locked):
        keys = settings.read_section("demo", cwd="/rt", env=None, locked=locked,
                                     run=run)["keys"]
        return ({key["name"] for key in keys if key["locked"]},
                {entry["key"] for key in keys for entry in key["entries"] or ()
                 if entry["locked"]})

    assert marked(()) == (set(), set())
    assert marked(settings.locked_entries("")) == (set(), set())
    assert marked({"demo.count", "demo.nosuch", "other.on"}) == ({"count"}, set())
    # An entry of a switch table locked alone: its table is marked too, for the controls
    # that write the whole table, and the entry beside it is not.
    assert marked({"demo.jobs.a"}) == ({"jobs"}, {"jobs.a"})
    assert marked({"demo.extra.a"}) == ({"extra"}, set())
    assert marked({"demo.jobs"}) == ({"jobs"}, {"jobs.a", "jobs.b"})
    assert marked({"demo"}) == ({key["name"] for key in [*EIGHT, table]}, {"jobs.a", "jobs.b"})
    assert marked({"demo.job", "demo.jobsx.a", "demo.co"}) == (set(), set())
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


def test_both_routes_refuse_a_write_to_a_key_that_holds_a_locked_entry(site, monkeypatch):
    monkeypatch.delenv(settings.LOCKED_ENV, raising=False)
    config = Layout("ui").config_file()
    with _Shell(site) as shell:
        status, data = shell.post("set", key="gateway.port", value="8498")
        assert status == 200 and data["ok"]
        monkeypatch.setenv(settings.LOCKED_ENV, "ui.gateway.port")
        file, history = config.read_bytes(), _history()
        for what, body in (("set", {"key": "gateway", "value": "{port = 8497}"}),
                           ("set", {"key": "gateway", "add": "x"}),
                           ("set", {"key": "gateway", "remove": "x"}),
                           ("unset", {"key": "gateway"})):
            status, data = shell.post(what, **body)
            assert status == 422, (what, body, data)
            assert data == {"refused": LOCKED_TEXT.format("ui", "gateway")}
            assert config.read_bytes() == file and _history() == history
        # The locked entry keeps its value. The key beside it is not locked, and is
        # changed at the terminal.
        status, data = shell.post("set", key="gateway.enabled", value="true")
        assert status == 422 and data["refused"].endswith("moxi ui gateway enable")
        keys = shell.keys()
        assert keys["gateway.port"]["value"] == "8498" and keys["gateway.port"]["locked"]
        assert keys["gateway.enabled"]["value"] == "false"
        assert not keys["gateway.enabled"]["locked"]
        # A key that only shares letters with the locked one is not locked.
        monkeypatch.setenv(settings.LOCKED_ENV, "ui.idle_sx.x,ui.por.t")
        for key, text in (("idle_s", "60"), ("port", "8499")):
            status, data = shell.post("set", key=key, value=text)
            assert status == 200 and data["ok"]
        assert not any(key["locked"] for key in shell.keys().values())


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


def test_the_shipped_schema_passes_the_cores_check_and_carries_the_page_fields():
    from importlib import resources

    from moxi.config import Schema

    text = resources.files("moxi_ui").joinpath("settings.toml").read_text()
    keys = Schema.parse(text, source="moxi_ui/settings.toml").keys
    assert {name: key.label for name, key in keys.items()} == {
        "port": "Port", "idle_s": "Idle shutdown", "theme.mode": "Light or dark",
        "theme.palette": "Look", "gateway.enabled": "Phone access",
        "gateway.port": "Phone port"}
    assert [name for name, key in keys.items() if key.common] == [
        "theme.palette", "theme.mode", "port"]
    assert keys["theme.palette"].pick and not keys["theme.mode"].pick
    assert keys["theme.palette"].choices == {"plain": "Plain", "midnight-neon": "Midnight neon"}
    assert keys["theme.mode"].choices["system"] == "follow the browser"
    assert keys["idle_s"].unit == "seconds"
    assert keys["port"].line == "Where this page is served, on this computer only."
    assert keys["gateway.enabled"].terminal == {"on": "moxi ui gateway enable",
                                                "off": "moxi ui gateway disable"}


@pytest.mark.parametrize("module", ["nosuch", "Not A Module", "changed"])
def test_a_module_the_listing_does_not_hold_gets_no_section_and_runs_nothing(
        site, monkeypatch, module):
    from urllib.parse import quote

    read = []
    real = settings.read_section
    monkeypatch.setattr(settings, "read_section",
                        lambda name, **kwargs: read.append(name) or real(name, **kwargs))
    with _Shell(site) as shell:
        status, data = _request(shell.shell.listeners.port, "GET",
                                f"/m/ui/api/settings?module={quote(module)}", shell.browser)
        assert status == 200 and data["sections"] == [] and read == []
        assert data["listed"][0] == "moxi" and "ui" in data["listed"]
        assert module not in data["listed"]
        status, data = _request(shell.shell.listeners.port, "GET",
                                "/m/ui/api/settings?module=moxi", shell.browser)
        assert [s["module"] for s in data["sections"]] == ["moxi"] and read == ["moxi"]
