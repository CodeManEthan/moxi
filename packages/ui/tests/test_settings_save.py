"""The settings save: one module's changes, each its own `moxi config set --json` or
`unset --json`, judged against the shell's own read of the module's schema; and the
terminal mark refused on every write route."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
import tomllib

import pytest
from moxi import config as core_config
from moxi.paths import Layout
from moxi_ui import settings
from moxi_ui.loopback.routes import project

import fixture_kit
from test_settings import SCHEMA, Run, _key, _request, _Shell

TERMINAL = {"on": "moxi ui gateway enable", "off": "moxi ui gateway disable"}


def _save_one(key, value="", *, reset=False, was="null", keys=SCHEMA, run=None, **kwargs):
    run = run or Run(stdout=json.dumps({"changed": True, "problem": None, "fix": None,
                                        "units": []}).encode())
    return settings.save_one("demo", key, value=value, reset=reset, was=was, keys=keys,
                             cwd="/rt", env=None, run=run, **kwargs), run


def _was(name, keys=SCHEMA):
    return json.dumps(next(item["value"] for item in keys if item["name"] == name))


# --- one change, recorded ------------------------------------------------------------------


def test_the_shells_read_decides_the_type_and_the_spelling():
    for name, value, spelled in (("count", "5", "5"), ("name", '"-x y"', "-x y"),
                                 ("on", "false", "false"), ("ratio", "2", "2"),
                                 ("tags", '["a", "b c"]', '["a", "b c"]'),
                                 ("mode", '"slow"', "slow"), ("dir", '"/x"', "/x"),
                                 ("dir", "null", ""),
                                 ("extra", '{"a": 1}', "{a = 1}"),
                                 ("extra", '"{a = 2}"', "{a = 2}")):
        result, run = _save_one(name, value, was=_was(name))
        assert result["outcome"] == "saved", (name, result)
        argv = run.writes[-1][0]
        assert argv[5:] == ["set", "--by", "ui", "--json", "--", "demo", name, spelled]
    # A string sent for a table is the TOML text the page's field holds, passed as it is;
    # so is a string sent for an int, the text typed. moxi config judges both.
    result, run = _save_one("count", '"84x60"', was=_was("count"))
    assert run.writes[-1][0][-1] == "84x60"
    result, run = _save_one("name", "not json", was=_was("name"))
    assert result["outcome"] == "refused" and run.writes == []


def test_text_typed_for_a_number_or_a_switch_is_spelled_as_typed():
    for type_ in ("int", "float", "bool"):
        assert settings.spell("84x60", type_) == "84x60"
    assert settings.spell(84, "int") == "84" and settings.spell(True, "bool") == "true"
    # A string in a list is still one TOML string.
    assert settings.spell(["84x60"], "list") == '["84x60"]'


def test_a_reset_unsets_the_key_and_an_unset_of_a_key_the_file_does_not_hold_is_unchanged():
    answer = json.dumps({"changed": False, "problem": None, "fix": None, "units": []})
    result, run = _save_one("count", reset=True, was=_was("count"),
                            run=Run(stdout=answer.encode()))
    assert run.writes[-1][0][5:] == ["unset", "--by", "ui", "--json", "--", "demo", "count"]
    assert result["outcome"] == "unchanged"


def test_a_key_the_read_does_not_list_is_refused_and_nothing_runs():
    for key in ("nosuch", "count.x", "jobsx.c", "on.x"):
        result, run = _save_one(key, "1")
        assert result == {"key": key, "outcome": "refused",
                          "problem": "is not a setting of demo", "fix": None, "mark": None,
                          "warning": None, "lines": [], "finished": True}
        assert run.writes == []


def test_a_terminal_key_is_refused_before_any_write_whatever_the_value():
    for value, reset, command in (("true", False, TERMINAL["on"]),
                                  ("false", False, TERMINAL["off"]),
                                  ("", True, TERMINAL["off"]),
                                  ('"yes"', False, f"{TERMINAL['on']} or {TERMINAL['off']}")):
        result, run = _save_one("gateway.enabled", value, reset=reset, was="false")
        assert result["outcome"] == "refused" and run.writes == []
        assert result["problem"] == f"is changed at the terminal: {command}"
    # The read decides: a key the request names as a plain switch is still refused.
    result, run = _save_one("gateway.enabled", "true", was="true")
    assert result["outcome"] == "refused" and run.writes == []


def test_a_locked_key_is_refused_before_any_write():
    for locked, key in (({"demo.count"}, "count"), ({"demo.jobs"}, "jobs.c"),
                        ({"demo"}, "tags")):
        result, run = _save_one(key, "1", was=_was(key) if key != "jobs.c" else "false",
                                locked=locked)
        assert result["outcome"] == "refused" and "locked" in result["problem"]
        assert run.writes == []


def test_a_change_whose_was_differs_from_the_read_is_moved_and_not_written():
    # A scalar, a list (sent whole, so an item added elsewhere is never lost), and a
    # switch table's entry.
    for key, was, now in (("count", "4", "3"), ("tags", '["a"]', '["a", "b c"]'),
                          ("jobs.c", "true", "false"), ("on", "1", "true")):
        result, run = _save_one(key, "true" if key == "jobs.c" else "5", was=was)
        assert result["outcome"] == "moved" and run.writes == [], key
        assert json.loads(result["problem"]) == json.loads(now)
    # The same value, however it is written, is not moved.
    result, _ = _save_one("ratio", "2", was="0.50")
    assert result["outcome"] == "saved"
    result, _ = _save_one("tags", "[]", was='[ "a", "b c" ]')
    assert result["outcome"] == "saved"
    result, run = _save_one("count", "5", was="not json")
    assert result["outcome"] == "refused" and run.writes == []


def test_a_switch_tables_entry_is_a_bool_of_its_own_with_its_tables_mark():
    result, run = _save_one("jobs.c", "true", was="false")
    assert result["outcome"] == "saved" and result["mark"] == settings.MARK_SWITCH
    assert run.writes[-1][0][-2:] == ["jobs.c", "true"]
    result, run = _save_one("adopted.new", "true", was="false")
    assert result["outcome"] == "saved" and result["mark"] == settings.MARK_NOW


def test_each_mark_is_the_keys_own():
    result, _ = _save_one("count", "5", was=_was("count"))
    assert result["mark"] == settings.MARK_NEXT_START
    result, _ = _save_one("on", "false", was=_was("on"), own={"daemons": {
        "watch": {"switch": "on"}}})
    assert result["mark"] == settings.MARK_SWITCH


def _answer(*, changed=False, problem=None, fix=None, units=()):
    return json.dumps({"changed": changed, "problem": problem, "fix": fix,
                       "units": list(units)}).encode()


@pytest.mark.parametrize("code, stdout, stderr, expected", [
    (0, _answer(changed=True), b"", {"outcome": "saved"}),
    (0, _answer(), b"", {"outcome": "unchanged"}),
    (2, _answer(problem="is 'x', not an int", fix="set it to an int"), b"",
     {"outcome": "refused", "problem": "is 'x', not an int", "fix": "set it to an int"}),
    (3, _answer(problem="demo: no distribution", fix="moxi install demo"), b"",
     {"outcome": "refused", "problem": "demo: no distribution", "fix": "moxi install demo"}),
    (75, _answer(problem="/c/config.toml: is locked by pid 7", fix="try again"), b"",
     {"outcome": "busy", "problem": "/c/config.toml: is locked by pid 7"}),
    (1, _answer(changed=True, units=["watch: restart failed"]), b"",
     {"outcome": "failed", "warning": "the value is written, and applying the module's units "
      "failed", "fix": "moxi install demo", "lines": ["watch: restart failed"]}),
    (2, b"", b"usage: moxi config set ...\nmoxi: error: the following arguments are required\n",
     {"outcome": "failed", "problem": "moxi: error: the following arguments are required"}),
    (0, b"[1]", b"", {"outcome": "failed", "problem": "moxi config exited 0 with no answer"}),
], ids=["saved", "unchanged", "refused", "absent", "busy", "units", "usage", "not-an-object"])
def test_each_exit_of_the_command_answers_its_outcome(code, stdout, stderr, expected):
    result, _ = _save_one("count", "5", was=_was("count"),
                          run=Run(code=code, stdout=stdout, stderr=stderr))
    base = {"key": "count", "outcome": None, "problem": None, "fix": None,
            "mark": settings.MARK_NEXT_START, "warning": None, "lines": [], "finished": True}
    assert result == {**base, **expected}
    project(result, settings.RESULT_SHAPE, strict=True)


def test_a_run_that_does_not_end_is_failed_and_not_finished():
    result, run = _save_one("count", "5", was=_was("count"),
                            run=Run(raises=subprocess.TimeoutExpired("x", 20)))
    assert result["outcome"] == "failed" and result["finished"] is False
    assert result["problem"] is None and result["warning"] is None
    project(result, settings.RESULT_SHAPE, strict=True)
    result, _ = _save_one("count", "5", was=_was("count"), run=Run(raises=OSError("gone")))
    assert result["outcome"] == "failed" and "could not run: gone" in result["problem"]
    assert result["finished"] is True


def test_a_write_may_run_past_the_files_lock_wait_and_a_read_may_not():
    _, run = _save_one("count", "5", was=_was("count"))
    assert run.writes[-1][1]["timeout_s"] == settings.WRITE_TIMEOUT_S
    assert settings.WRITE_TIMEOUT_S > core_config.LOCK_TIMEOUT
    assert settings.CONFIG_TIMEOUT == 10.0
    run = Run()
    settings.change("demo", "count", value="5", cwd="/rt", env=None, run=run)
    assert [kwargs["timeout_s"] for _, kwargs in run.calls] == [
        settings.CONFIG_TIMEOUT, settings.WRITE_TIMEOUT_S]


# --- set and unset read the schema first ---------------------------------------------------


@pytest.mark.parametrize("write, command", [
    ({"value": "true"}, TERMINAL["on"]), ({"value": "false"}, TERMINAL["off"]),
    ({"unset": True}, TERMINAL["off"]), ({"value": "maybe"},
                                         f"{TERMINAL['on']} or {TERMINAL['off']}")])
def test_set_and_unset_refuse_a_terminal_key_before_any_write(write, command):
    run = Run()
    with pytest.raises(settings.Refused) as refused:
        settings.change("ui", "gateway.enabled", cwd="/rt", env=None, run=run, **write)
    assert refused.value.text == f"ui gateway.enabled is changed at the terminal: {command}"
    assert run.writes == []


def test_set_and_unset_refuse_a_key_the_read_does_not_list_and_a_failed_read():
    run = Run()
    with pytest.raises(settings.Refused) as refused:
        settings.change("demo", "nosuch", value="1", cwd="/rt", env=None, run=run)
    assert refused.value.text == "nosuch is not a setting of demo" and run.writes == []
    gone = Run(code=2, stderr=b"moxi: demo is not installed\n", schema=None)
    with pytest.raises(settings.Refused) as refused:
        settings.change("demo", "count", value="1", cwd="/rt", env=None, run=gone)
    assert refused.value.text == "demo is not installed" and len(gone.calls) == 1
    slow = Run(raises=subprocess.TimeoutExpired("x", 10), schema=None)
    with pytest.raises(settings.Conflict):
        settings.change("demo", "count", value="1", cwd="/rt", env=None, run=slow)
    broken = Run(code=1, stdout=json.dumps({"error": "bad TOML", "fix": "mend it"}).encode(),
                 schema=None)
    with pytest.raises(settings.Refused) as refused:
        settings.change("demo", "count", value="1", cwd="/rt", env=None, run=broken)
    assert refused.value.text == "bad TOML; fix: mend it"


# --- through a real shell, over the fixture module ------------------------------------------


@pytest.fixture
def shell(tmp_path, monkeypatch):
    site = fixture_kit.install(tmp_path / "site")
    monkeypatch.setenv("PYTHONPATH", str(site))
    monkeypatch.delenv(settings.LOCKED_ENV, raising=False)
    Layout("ui").runtime_dir(create=True)
    with _Shell(site) as serving:
        yield serving


def _save(shell, module, changes):
    """POST a save of `changes`, each `(key, value, reset, was)`."""
    body = {"module": module, "keys": [c[0] for c in changes],
            "values": [c[1] for c in changes], "resets": [c[2] for c in changes],
            "was": [c[3] for c in changes]}
    return _request(shell.shell.listeners.port, "POST", "/m/ui/api/settings/save",
                    shell.browser, body)


def _file(module="fixture"):
    path = Layout(module).config_file()
    return tomllib.loads(path.read_text()) if path.exists() else {}


def _outcomes(data):
    return [(r["key"], r["outcome"]) for r in data["results"]]


def test_a_save_runs_in_order_and_a_refusal_does_not_stop_the_next_change(shell):
    before = time.time()
    status, data = _save(shell, "fixture", [
        ("greeting", '"hi"', False, '"hello"'), ("limit", '"nope"', False, "10"),
        ("workers", "3", False, "2"), ("rows.backup", "true", False, "false"),
        ("tags", '["a", "b c"]', False, "[]")])
    assert status == 200, data
    assert _outcomes(data) == [("greeting", "saved"), ("limit", "refused"),
                               ("workers", "saved"), ("rows.backup", "saved"),
                               ("tags", "saved")]
    refused = data["results"][1]
    assert refused["problem"] and refused["fix"] and "nope" not in refused["fix"]
    assert [r["mark"] for r in data["results"]] == [
        "applies now", "applies now", "applies at next start", "applies now", "applies now"]
    assert before <= data["saved_at"] <= time.time()
    assert _file() == {"greeting": "hi", "workers": 3, "rows": {"backup": True},
                       "tags": ["a", "b c"]}
    # A reset unsets the key; an unset of a key the file doesn't hold is unchanged.
    status, data = _save(shell, "fixture", [("greeting", "", True, '"hi"'),
                                            ("limit", "", True, "10")])
    assert _outcomes(data) == [("greeting", "saved"), ("limit", "unchanged")]
    assert "greeting" not in _file()


def test_a_pair_a_check_across_keys_refuses_saves_on_a_second_save(shell):
    # Lowering limit under workers is refused by the module's own check until workers
    # is lowered too.
    status, data = _save(shell, "fixture", [("limit", "1", False, "10"),
                                            ("workers", "1", False, "2")])
    assert _outcomes(data) == [("limit", "refused"), ("workers", "saved")]
    refused = data["results"][0]
    assert "is 2, more than limit (1)" in refused["problem"]
    assert refused["fix"] == "set workers to limit or less"
    status, data = _save(shell, "fixture", [("limit", "1", False, "10")])
    assert _outcomes(data) == [("limit", "saved")]
    assert _file() == {"limit": 1, "workers": 1}


def test_a_value_changed_behind_the_page_is_moved_and_kept(shell):
    path = Layout("fixture").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('limit = 20\ntags = ["kept"]\n')
    status, data = _save(shell, "fixture", [("limit", "30", False, "10"),
                                            ("tags", '["mine"]', False, "[]"),
                                            ("workers", "4", False, "2")])
    assert _outcomes(data) == [("limit", "moved"), ("tags", "moved"), ("workers", "saved")]
    assert [json.loads(r["problem"]) for r in data["results"][:2]] == [20, ["kept"]]
    assert _file() == {"limit": 20, "tags": ["kept"], "workers": 4}


def test_a_terminal_key_is_refused_on_save_set_and_unset_before_any_write(shell):
    from test_settings import _history

    config = Layout("ui").config_file()
    status, data = shell.post("set", key="idle_s", value="60")
    assert status == 200
    file, history = config.read_bytes(), _history()
    status, data = _save(shell, "ui", [("gateway.enabled", "true", False, "false")])
    assert data["results"][0]["outcome"] == "refused"
    assert data["results"][0]["problem"] == f"is changed at the terminal: {TERMINAL['on']}"
    for what, body in (("set", {"value": "true"}), ("set", {"value": "false"}),
                       ("unset", {})):
        status, data = shell.post(what, key="gateway.enabled", **body)
        assert status == 422 and "is changed at the terminal" in data["refused"]
    status, data = shell.post("set", module="connector", key="enabled", value="true")
    assert status == 422 and data["refused"].endswith("moxi connector enable")
    assert config.read_bytes() == file and _history() == history


def test_a_module_the_listing_does_not_hold_runs_nothing(shell, monkeypatch):
    ran = []
    monkeypatch.setattr(settings, "read_schema", lambda *a, **k: ran.append(a))
    status, data = _save(shell, "nosuch", [("a", "1", False, "0")])
    assert status == 200 and ran == []
    assert data["results"] == [{"key": "a", "outcome": "refused",
                                "problem": "is not a setting of nosuch", "fix": None,
                                "mark": None, "warning": None, "lines": [], "finished": True}]


def test_a_failed_schema_read_fails_every_change_and_writes_nothing(shell, monkeypatch):
    monkeypatch.setattr(settings, "read_schema", lambda *a, **k: settings.SchemaRead(
        None, "moxi config fixture show ran past 10 s", ended=False))
    status, data = _save(shell, "fixture", [("limit", "1", False, "10"),
                                            ("workers", "1", False, "2")])
    assert _outcomes(data) == [("limit", "failed"), ("workers", "failed")]
    assert data["results"][0]["problem"] == "moxi config fixture show ran past 10 s"
    assert _file() == {}


def test_the_body_is_capped_and_its_lists_are_of_one_length(shell):
    port, browser = shell.shell.listeners.port, shell.browser
    path = "/m/ui/api/settings/save"
    many = [("limit", "1", False, "10")] * 65
    status, data = _save(shell, "fixture", many)
    assert status == 400 and "keys" in data["error"]
    for short in ("values", "resets", "was"):
        body = {"module": "fixture", "keys": ["limit", "workers"], "values": ["1", "1"],
                "resets": [False, False], "was": ["10", "2"]}
        body[short] = body[short][:1]
        status, data = _request(port, "POST", path, browser, body)
        assert status == 400 and data["error"].endswith(": keys"), short
    big = "x" * 65537
    status, data = _request(port, "POST", path, browser, {
        "module": "fixture", "keys": ["greeting"], "values": [json.dumps(big)],
        "resets": [False], "was": ['"hello"']})
    assert status == 400
    # Under each item's cap the whole body is still capped, as set's is: the base answers
    # a larger one 413 before the handler runs.
    route = next(r for r in settings.ROUTES if r.path == "/api/settings/save")
    assert route.body_cap == 256 * 1024
    assert _file() == {}


HOLDER = textwrap.dedent("""
    import sys, time
    from moxi import files
    with files.lock(sys.argv[1]):
        print("held", flush=True)
        time.sleep(60)
""")


def test_a_write_waiting_on_the_files_lock_answers_busy_not_failed(shell):
    lock = Layout("fixture").config_file().with_name("config.toml.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(lock)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        status, data = _save(shell, "fixture", [("limit", "12", False, "10")])
    finally:
        holder.kill()
        holder.wait()
    assert _outcomes(data) == [("limit", "busy")]
    assert str(holder.pid) in data["results"][0]["problem"]


def test_the_save_answer_holds_its_declared_shape():
    keys = [_key("count", "int", 3, 1)]
    result, _ = _save_one("count", "5", was="3", keys=keys)
    project({"saved_at": 1.5, "results": [result]},
            settings.ROUTES[-1].shape, strict=True)
