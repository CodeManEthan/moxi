"""Changing settings: `moxi.config.set`, `unset`, `add_item`, `remove_item`, `history`, the
`moxi config set` and `unset` verbs, item types, switch tables, `needs` and `path=`."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from moxi import cli, config
from moxi.config import ConfigError, Schema, SchemaError

SCHEMA = """
[backend]
type = "enum"
values = ["local", "remote"]
default = "local"
doc = "Where messages are recorded."
read = "start"
env = "MOXI_DEMO_BACKEND"

[block]
type = "bool"
default = false
doc = "Refuse every send while true."
read = "use"

[local.retain_days]
type = "int"
default = 30
doc = "How many days a message is kept."
read = "start"

[local.root]
type = "path"
default = ""
doc = "Where the record lives."
read = "start"

[watch.tick]
type = "float"
default = 10.0
doc = "Seconds between two looks."
read = "start"

[name]
type = "string"
default = "demo"
doc = "A display name."
read = "use"

[private_names]
type = "list"
items = "str"
default = []
doc = "Names never shown."
read = "use"

[ports]
type = "list"
items = "int"
default = [1]
doc = "Ports to try."
read = "start"

[tags]
type = "list"
default = []
doc = "Anything at all."
read = "use"

[rows]
type = "table"
items = "bool"
default = {}
doc = "A switch per row."
read = "use"

[limits]
type = "table"
items = "int"
default = {}
doc = "A limit per thing."
read = "start"

[codex.defaults]
type = "table"
default = {}
doc = "Defaults for a Codex session."
read = "start"

[old]
retired = "use backend instead"
"""

COMMENTED = textwrap.dedent("""\
    # my settings
    block = false  # keep sending

    [local]
    # a month
    retain_days = 30
""")


@pytest.fixture
def demo(site, tmp_path, monkeypatch):
    """Module `demo` with SCHEMA, its config root and the core's state in the test's tmp.
    Returns the config file's path."""
    site.write("moxi_demo/settings.toml", SCHEMA)
    site.module("demo", SETTINGS="'settings.toml'")
    site.module("bare")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MOXI_DEMO_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "core"))
    monkeypatch.delenv("MOXI_DEMO_BACKEND", raising=False)
    return tmp_path / "config" / "demo" / "config.toml"


def read(path: Path) -> dict:
    import tomllib

    return tomllib.loads(path.read_text())


def settings() -> config.Settings:
    return config.load("demo", Schema.parse(SCHEMA))


def refused(call, *args, **kwargs) -> ConfigError:
    with pytest.raises(ConfigError) as caught:
        call(*args, **kwargs)
    return caught.value


# -- set: the value by the key's type ---------------------------------------------------

@pytest.mark.parametrize("given, stored", [("yes", True), ("on", True), ("1", True),
                                           ("true", True), ("No", False), ("off", False),
                                           ("0", False), (True, True), (False, False)])
def test_a_bool_is_read_from_its_words(demo, given, stored):
    config.set("demo", "block", given, by="cli")
    assert read(demo)["block"] is stored


@pytest.mark.parametrize("key, given, stored", [
    ("local.retain_days", "7", 7), ("local.retain_days", 7, 7),
    ("watch.tick", "2.5", 2.5), ("watch.tick", 3, 3.0),
    ("name", "a name", "a name"), ("name", 12, "12"),
    ("backend", "remote", "remote"),
    ("local.root", "~/record", "~/record"), ("local.root", "/var/r", "/var/r"),
    ("local.root", "", ""),
])
def test_each_scalar_type_is_coerced(demo, key, given, stored):
    config.set("demo", key, given, by="cli")
    group, _, leaf = key.rpartition(".")
    table = read(demo)[group] if group else read(demo)
    assert table[leaf] == stored and type(table[leaf]) is type(stored)


@pytest.mark.parametrize("key, given, words", [
    ("block", "maybe", "true or false"), ("block", 1, "true or false"),
    ("local.retain_days", "seven", "an int"), ("local.retain_days", 2.5, "an int"),
    ("local.retain_days", True, "an int"), ("watch.tick", "fast", "a float"),
    ("name", True, "a string"), ("backend", "cloud", "'local', 'remote'"),
    ("local.root", "relative/path", "absolute path"),
])
def test_a_bad_value_is_refused_with_its_fix_and_writes_nothing(demo, key, given, words):
    exc = refused(config.set, "demo", key, given, by="cli")
    assert exc.key == key and words in exc.fix and exc.exit_code == 2
    assert not demo.exists()


def test_a_list_and_a_table_are_set_whole(demo):
    config.set("demo", "tags", ["a", 2, {"x": 1}], by="cli")
    config.set("demo", "codex.defaults", {"sandbox": "read-only"}, by="cli")
    assert read(demo)["tags"] == ["a", 2, {"x": 1}]
    assert read(demo)["codex"]["defaults"] == {"sandbox": "read-only"}
    assert refused(config.set, "demo", "tags", "a", by="cli").exit_code == 2
    assert refused(config.set, "demo", "codex.defaults", [1], by="cli").exit_code == 2


def test_a_typed_list_checks_every_item(demo):
    config.set("demo", "private_names", ["a", 2024], by="cli")
    assert read(demo)["private_names"] == ["a", "2024"]
    config.set("demo", "ports", ["80", 443], by="cli")
    assert read(demo)["ports"] == [80, 443]
    exc = refused(config.set, "demo", "ports", [80, "http"], by="cli")
    assert "'http'" in exc.problem and "an int" in exc.fix
    assert read(demo)["ports"] == [80, 443]


def test_a_typed_table_coerces_each_entry(demo):
    config.set("demo", "limits", {"cpu": "2", "fds": 64}, by="cli")
    assert read(demo)["limits"] == {"cpu": 2, "fds": 64}
    assert refused(config.set, "demo", "limits", {"cpu": "lots"}, by="cli").exit_code == 2


# -- add_item and remove_item -------------------------------------------------------------

def test_add_to_a_str_list_stores_the_string(demo):
    config.add_item("demo", "private_names", 2024, by="cli")
    assert read(demo)["private_names"] == ["2024"]
    assert refused(config.add_item, "demo", "ports", "http", by="cli").exit_code == 2


def test_add_starts_from_the_keys_value_now(demo):
    config.add_item("demo", "ports", 8080, by="cli")
    assert read(demo)["ports"] == [1, 8080]
    config.add_item("demo", "ports", 9090, by="cli")
    assert read(demo)["ports"] == [1, 8080, 9090]


def test_an_item_already_held_is_not_added_again(demo):
    config.add_item("demo", "ports", 8080, by="cli")
    before = demo.read_text()
    config.add_item("demo", "ports", 8080, by="cli")
    assert demo.read_text() == before
    assert [r["op"] for r in config.history("demo")] == ["add"]


def test_remove_takes_every_equal_item_and_refuses_one_not_held(demo):
    config.set("demo", "tags", ["a", "b", "a"], by="cli")
    config.remove_item("demo", "tags", "a", by="cli")
    assert read(demo)["tags"] == ["b"]
    exc = refused(config.remove_item, "demo", "tags", "zzz", by="cli")
    assert "holds no item" in exc.problem and exc.exit_code == 2


def test_remove_from_a_list_at_its_default_writes_the_rest(demo):
    config.remove_item("demo", "ports", 1, by="cli")
    assert read(demo)["ports"] == []


def test_add_and_remove_take_only_a_list(demo):
    assert "not a list" in refused(config.add_item, "demo", "name", "x", by="cli").problem
    assert "not a list" in refused(config.remove_item, "demo", "rows.a", True,
                                   by="cli").problem


def test_add_keeps_the_lists_comments(demo):
    demo.parent.mkdir(parents=True)
    demo.write_text('ports = [\n    1,  # the first\n]\n')
    config.add_item("demo", "ports", 2, by="cli")
    assert "# the first" in demo.read_text()
    assert read(demo)["ports"] == [1, 2]


# -- unset ---------------------------------------------------------------------------------

def test_unset_removes_the_key_so_it_reads_its_default(demo):
    config.set("demo", "local.retain_days", 7, by="cli")
    assert settings()["local.retain_days"] == 7
    assert config.unset("demo", "local.retain_days", by="cli") == []
    assert "retain_days" not in demo.read_text()
    assert settings()["local.retain_days"] == 30
    assert settings().source("local.retain_days") == "default"


def test_unset_of_a_key_the_file_lacks_changes_nothing(demo):
    config.unset("demo", "block", by="cli")
    assert demo.read_text() == ""


# -- the key, the module, the file ---------------------------------------------------------

def test_an_unknown_key_is_refused_with_the_nearest_one(demo):
    exc = refused(config.set, "demo", "local.retain_dayz", 1, by="cli")
    assert exc.exit_code == 2 and "not a setting of demo" in exc.problem
    assert "local.retain_days" in exc.fix
    exc = refused(config.set, "demo", "qqqqqqqq", 1, by="cli")
    assert "moxi config demo" in exc.fix


def test_a_retired_key_says_what_to_do_now(demo):
    exc = refused(config.set, "demo", "old", 1, by="cli")
    assert exc.problem == "is retired" and exc.fix == "use backend instead"


@pytest.mark.parametrize("module, words", [("nothere", "moxi install nothere"),
                                           ("bare", "nothing to set")])
def test_an_absent_module_or_one_without_settings_refuses_with_exit_3(demo, module, words):
    exc = refused(config.set, module, "x", 1, by="cli")
    assert exc.exit_code == 3 and words in exc.fix


def test_set_returns_no_applied_units_and_needs_a_by(demo):
    assert config.set("demo", "block", True, by="ui") == []
    assert refused(config.set, "demo", "block", True, by="").exit_code == 2


def test_set_keeps_comments_and_order(demo):
    demo.parent.mkdir(parents=True)
    demo.write_text(COMMENTED)
    config.set("demo", "local.retain_days", 7, by="cli")
    config.set("demo", "name", "x", by="cli")
    assert demo.read_text() == COMMENTED.replace("retain_days = 30", "retain_days = 7") \
        .replace("# keep sending\n", '# keep sending\nname = "x"\n')


def test_a_change_never_writes_a_file_the_reader_refuses(demo):
    demo.parent.mkdir(parents=True)
    demo.write_text("block = true\nstray = 1\n")
    exc = refused(config.set, "demo", "block", False, by="cli")
    assert exc.key == "stray"
    assert demo.read_text() == "block = true\nstray = 1\n"
    demo.write_text("block = \n")
    exc = refused(config.set, "demo", "block", False, by="cli")
    assert "not valid TOML" in exc.problem
    assert config.history("demo") == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_a_new_file_is_0600_and_an_existing_files_mode_is_kept(demo):
    config.set("demo", "block", True, by="cli")
    assert stat.S_IMODE(os.stat(demo).st_mode) == 0o600
    os.chmod(demo, 0o644)
    config.set("demo", "block", False, by="cli")
    assert stat.S_IMODE(os.stat(demo).st_mode) == 0o644


HOLDER = textwrap.dedent("""
    import sys, time
    from moxi import files
    with files.lock(sys.argv[1]):
        print("held", flush=True)
        time.sleep(30)
""")


def test_a_lock_held_past_the_wait_refuses_with_exit_75(demo, monkeypatch):
    monkeypatch.setattr(config, "LOCK_TIMEOUT", 0.2)
    lock = demo.with_name("config.toml.lock")
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(lock)],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        exc = refused(config.set, "demo", "block", True, by="cli")
    finally:
        proc.kill()
        proc.wait()
    assert exc.exit_code == 75 and str(proc.pid) in exc.problem
    assert not demo.exists()


# -- the history ---------------------------------------------------------------------------

def test_every_change_is_recorded_without_its_value(demo, tmp_path):
    config.set("demo", "private_names", ["secret-name"], by="ui")
    config.add_item("demo", "private_names", "other-secret", by="moxi install")
    config.remove_item("demo", "private_names", "secret-name", by="cli")
    config.unset("demo", "private_names", by="cli")
    records = config.history("demo")
    assert [(r["module"], r["key"], r["op"], r["by"]) for r in records] == [
        ("demo", "private_names", "set", "ui"),
        ("demo", "private_names", "add", "moxi install"),
        ("demo", "private_names", "remove", "cli"),
        ("demo", "private_names", "unset", "cli")]
    assert all(set(r) == {"module", "key", "op", "by", "at"} for r in records)
    assert all(len(r["at"]) == 20 and r["at"].endswith("Z") for r in records)
    files = list((tmp_path / "core").rglob(config.HISTORY))
    assert len(files) == 1
    assert "secret" not in files[0].read_text()


def test_history_filters_by_key_and_keeps_the_tail(demo):
    for n in range(5):
        config.set("demo", "local.retain_days", n, by="cli")
    config.set("demo", "block", True, by="cli")
    assert len(config.history("demo", "local.retain_days")) == 5
    assert [r["key"] for r in config.history("demo", tail=2)] == ["local.retain_days",
                                                                  "block"]
    assert config.history("other") == []


# -- switch tables -------------------------------------------------------------------------

def test_every_unset_state_of_a_switch_reads_false(demo):
    assert settings()["rows.backup"] is False             # no file
    demo.parent.mkdir(parents=True)
    demo.write_text("block = true\n")
    assert settings()["rows.backup"] is False             # no table
    demo.write_text("[rows]\nother = true\n")
    assert settings()["rows.backup"] is False             # no entry
    assert settings()["rows.other"] is True
    config.set("demo", "rows.backup", "yes", by="cli")
    assert read(demo)["rows"]["backup"] is True           # stored as a bool
    assert settings()["rows.backup"] is True
    assert settings().source("rows.backup") == "file"
    config.unset("demo", "rows.backup", by="cli")
    assert read(demo)["rows"] == {"other": True}
    assert settings()["rows.backup"] is False             # unset entry
    assert settings().source("rows.backup") == "default"
    assert "rows.backup" in settings() and "rows.Bad" not in settings()


def test_a_switch_takes_only_a_bool(demo):
    assert "true or false" in refused(config.set, "demo", "rows.backup", "3",
                                      by="cli").fix
    exc = refused(config.set, "demo", "rows", {"a": True, "b": "yes please"}, by="cli")
    assert exc.exit_code == 2
    assert refused(config.set, "demo", "rows", {"Bad": True}, by="cli").exit_code == 2
    config.set("demo", "rows", {"a": True, "b": "no"}, by="cli")
    assert read(demo)["rows"] == {"a": True, "b": False}


@pytest.mark.parametrize("text", ['[rows]\nbackup = "yes"\n', "rows = 3\n"])
def test_a_hand_written_non_bool_entry_or_non_table_fails_the_check(demo, text):
    demo.parent.mkdir(parents=True)
    demo.write_text(text)
    with pytest.raises(ConfigError) as caught:
        settings()
    assert caught.value.key == "rows"


def test_only_a_switch_tables_entries_are_keys(demo):
    assert refused(config.set, "demo", "limits.cpu", 1, by="cli").exit_code == 2
    assert refused(config.set, "demo", "rows.Backup", True, by="cli").exit_code == 2
    assert refused(config.set, "demo", "rows.a.b", True, by="cli").exit_code == 2
    with pytest.raises(KeyError):
        settings()["limits.cpu"]


def test_a_schema_declaring_a_key_under_a_switch_table_is_refused():
    text = SCHEMA + '\n[rows.backup]\ntype = "bool"\ndefault = false\ndoc = "d"\nread = "use"\n'
    with pytest.raises(SchemaError) as caught:
        Schema.parse(text)
    assert caught.value.key == "rows" and "rows.backup" in caught.value.problem
    assert "switch table" in caught.value.problem


@pytest.mark.parametrize("change, switch", [
    ({}, True), ({"read": "start"}, False), ({"default": {"a": True}}, False),
    ({"items": "int"}, False), ({"type": "list", "default": []}, False)])
def test_what_makes_a_switch_table(change, switch):
    spec = {"type": "table", "items": "bool", "default": {}, "doc": "d", "read": "use",
            **change}
    key = config.Key(name="rows", type=spec["type"], default=spec["default"], doc="d",
                     read=spec["read"], items=spec["items"])
    assert config.is_switch_table(key) is switch


# -- the schema's new fields ---------------------------------------------------------------

def test_needs_and_items_are_read():
    schema = Schema.parse('[a]\ntype = "list"\nitems = "float"\ndefault = [1]\ndoc = "d"\n'
                          'read = "use"\nneeds = "turns"\n')
    key = schema.keys["a"]
    assert (key.items, key.needs, key.default) == ("float", "turns", [1])
    assert config.load("demo", schema, path="/nonexistent")["a"] == [1.0]


@pytest.mark.parametrize("extra, words", [
    ('type = "bool"\ndefault = false\nitems = "str"', "has items but is a bool"),
    ('type = "list"\ndefault = []\nitems = "string"', "has items 'string'"),
    ('type = "list"\ndefault = ["a"]\nitems = "int"', "has a default that"),
    ('type = "bool"\ndefault = false\nneeds = "Not A Module"', "has needs"),
])
def test_bad_items_or_needs_are_refused(extra, words):
    with pytest.raises(SchemaError, match=words):
        Schema.parse(f'[a]\n{extra}\ndoc = "d"\nread = "use"\n')


def test_a_key_is_a_plain_read_only_record():
    key = config.Key(name="a", type="int", default=1, doc="d", read="use")
    assert key == config.Key("a", "int", 1, "d", "use")
    assert hash(key) == hash(config.Key("a", "int", 1, "d", "use"))
    assert (key.values, key.env, key.retired, key.needs, key.items) == (None,) * 5
    with pytest.raises(AttributeError):
        key.default = 2
    assert "Key(name='a'" in repr(key)
    import copy
    import pickle

    assert copy.deepcopy(key) == key == pickle.loads(pickle.dumps(key))


# -- path= ---------------------------------------------------------------------------------

def test_load_and_config_read_a_named_file_in_place(tmp_path):
    schema = Schema.parse(SCHEMA)
    named = tmp_path / "elsewhere.toml"
    named.write_text("block = true\n")
    s = config.load("demo", schema, path=named, env={"HOME": str(tmp_path)})
    assert s["block"] is True and s.file == named
    reader = config.Config("demo", schema, path=str(named), env={"HOME": str(tmp_path)})
    assert reader.start()["block"] is True
    named.write_text("block = 3\n")
    with pytest.raises(ConfigError) as caught:
        config.load("demo", schema, path=named)
    assert caught.value.file == str(named)


# -- the core's own settings ---------------------------------------------------------------

def test_the_cores_schema_and_its_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("MOXI_HOME", str(tmp_path))
    schema = config._core_schema()
    assert list(schema.keys) == ["skills.form", "skills.harnesses", "harnesses"]
    s = config.load("moxi", schema)
    assert s["skills.form"] == "auto"
    assert s["skills.harnesses"] == ["claude", "codex"]
    assert s["harnesses"] == [{"name": "claude", "skills": "", "agents": ""},
                              {"name": "codex", "skills": "", "agents": ""}]


def test_a_harness_row_is_added_to_the_cores_table(tmp_path, monkeypatch):
    monkeypatch.setenv("MOXI_HOME", str(tmp_path))
    config.add_item("moxi", "harnesses", {"name": "opencode", "skills": "~/s",
                                          "agents": ""}, by="cli")
    s = config.load("moxi", config._core_schema())
    assert [row["name"] for row in s["harnesses"]] == ["claude", "codex", "opencode"]
    assert "{name = \"opencode\"" in config._config_file("moxi").read_text()


def test_the_cores_settings_and_history_follow_only_moxi_home(tmp_path, monkeypatch,
                                                              capsys):
    """The core's file and history are where `paths.core()` puts them: the core's own
    layout reads no `MOXI_MOXI_*` variable."""
    from moxi import paths

    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "home"))
    for name in ("MOXI_MOXI_HOME", "MOXI_MOXI_CONFIG_HOME", "MOXI_MOXI_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / "elsewhere"))
    assert config._config_file("moxi") == paths.core().config_file()
    assert run(capsys, "config", "set", "moxi", "skills.form", "copy")[0] == 0
    assert (paths.core().state_dir() / config.HISTORY).is_file()
    assert config.load("moxi", config._core_schema())["skills.form"] == "copy"
    assert run(capsys, "config", "moxi", "path")[1].strip() == str(
        paths.core().config_file())
    assert not (tmp_path / "elsewhere").exists()


# -- the light import ----------------------------------------------------------------------

def test_importing_moxi_config_loads_nothing_heavy(tmp_path):
    small = tmp_path / "c.toml"
    small.write_text("block = true\n")
    code = textwrap.dedent(f"""
        import sys
        import moxi.config
        at_import = [m for m in ("dataclasses", "typing", "re", "tomllib", "pathlib",
                                 "threading", "copy", "moxi.paths", "moxi.names")
                     if m in sys.modules]
        import pathlib, tomllib  # what the reader needs, and what they load themselves
        before = set(sys.modules)
        schema = moxi.config.Schema.parse('[block]\\ntype = "bool"\\ndefault = false\\n'
                                          'doc = "d"\\nread = "use"\\n')
        moxi.config.load("demo", schema, path={str(small)!r})
        at_load = [m for m in ("dataclasses", "typing", "difflib", "json", "tomlkit",
                               "moxi._vendor.tomlkit", "moxi.files", "moxi.modules")
                   if m in sys.modules and m not in before]
        print(at_import, at_load)
    """)
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                          text=True, check=True)
    assert done.stdout.strip() == "[] []"


# -- the verbs -----------------------------------------------------------------------------

def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_config_set_prints_the_key_without_its_value(demo, capsys):
    code, out, err = run(capsys, "config", "set", "demo", "private_names",
                         '["hidden-one"]')
    assert (code, out, err) == (0, "demo: private_names set (by cli)\n", "")
    code, out, _ = run(capsys, "config", "set", "demo", "private_names", "--add", "2024",
                       "--by", "ui")
    assert (code, out) == (0, "demo: private_names: one item added (by ui)\n")
    assert read(demo)["private_names"] == ["hidden-one", "2024"]
    code, out, _ = run(capsys, "config", "set", "demo", "private_names", "--remove",
                       "hidden-one")
    assert (code, out) == (0, "demo: private_names: one item removed (by cli)\n")
    code, out, _ = run(capsys, "config", "unset", "demo", "private_names")
    assert (code, out) == (0, "demo: private_names unset (by cli)\n")


def test_config_set_reads_a_list_item_as_toml_when_it_parses(demo, capsys):
    run(capsys, "config", "set", "demo", "tags", "--add", "2024")
    run(capsys, "config", "set", "demo", "tags", "--add", "plain words")
    run(capsys, "config", "set", "demo", "tags", "--add", '{name = "x"}')
    assert read(demo)["tags"] == [2024, "plain words", {"name": "x"}]
    run(capsys, "config", "set", "demo", "codex.defaults", '{sandbox = "read-only"}')
    assert read(demo)["codex"]["defaults"] == {"sandbox": "read-only"}


@pytest.mark.parametrize("argv, code, words", [
    (("demo", "nope", "1"), 2, "not a setting of demo"),
    (("demo", "block", "maybe"), 2, "true or false"),
    (("demo", "tags", "[unclosed"), 2, "one TOML value"),
    (("nothere", "x", "1"), 3, "moxi install nothere"),
    (("demo", "block"), 2, "exactly one of"),
    (("demo", "block", "true", "--add", "x"), 2, "exactly one of"),
])
def test_config_set_refusals_and_their_exits(demo, capsys, argv, code, words):
    got, out, err = run(capsys, "config", "set", *argv)
    assert got == code and words in err and out == ""


def test_config_show_says_who_last_set_each_key(demo, capsys):
    run(capsys, "config", "set", "demo", "block", "true", "--by", "ui")
    run(capsys, "config", "set", "demo", "rows.backup", "on")
    out = run(capsys, "config", "demo")[1]
    lines = out.splitlines()
    block = lines.index("block = true    [file]")
    assert lines[block + 2].startswith("    last set by ui at ")
    rows = next(i for i, line in enumerate(lines) if line.startswith("rows = "))
    assert lines[rows + 2].startswith("    last set by cli at ")
    name = next(i for i, line in enumerate(lines) if line.startswith("name = "))
    assert not lines[name + 2].startswith("    last")


def test_config_moxi_reads_the_cores_own_settings(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MOXI_HOME", str(tmp_path))
    code, out, _ = run(capsys, "config", "moxi", "path")
    assert code == 0 and out.strip().endswith(os.path.join("moxi", "config.toml"))
    assert run(capsys, "config", "set", "moxi", "skills.form", "copy")[0] == 0
    code, out, _ = run(capsys, "config", "moxi")
    assert code == 0 and 'skills.form = "copy"    [file]' in out


def test_config_help_names_set_and_unset(capsys):
    code, out, _ = run(capsys, "config", "--help")
    assert code == 0 and "moxi config set" in out and "moxi config unset" in out


def test_the_history_file_holds_json_lines(demo, tmp_path):
    config.set("demo", "block", True, by="cli")
    path = next((tmp_path / "core").rglob(config.HISTORY))
    assert json.loads(path.read_text().splitlines()[0])["key"] == "block"


def test_an_untyped_list_tells_one_from_true(demo):
    config.set("demo", "tags", [True], by="cli")
    config.add_item("demo", "tags", 1, by="cli")
    assert read(demo)["tags"] == [True, 1]
    config.remove_item("demo", "tags", True, by="cli")
    assert read(demo)["tags"] == [1]
