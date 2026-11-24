"""A table a module keeps in a file of its own (`CONFIG_TABLES`): `moxi config show` lists
its rows after the module's keys, and `set` and `unset` of `<table>.<row>` go to the
module's hook with a config key's exits, `--json` answer and history line."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from moxi import cli, config

SCHEMA = """
[block]
type = "bool"
default = false
doc = "Refuse every send while true."
read = "use"
"""

#: The hook: rows kept as one JSON object in the file `DEMO_ROWS_FILE` names. A value
#: `bad` is refused, `held` meets a held lock, `warn` is written with a warning line; an
#: unset answers a line for each other row the file still holds.
HOOK = '''
import json
from pathlib import Path

from moxi import files
from moxi.config import ConfigError


class _Rows:
    title = "Demo rows"
    line = "Every use starts from these."
    applies = "Applies to uses after this."

    def file(self, env):
        return Path(env["DEMO_ROWS_FILE"])

    def _read(self, env):
        path = self.file(env)
        if not path.exists():
            return {}
        text = path.read_text()
        if text == "broken":
            raise ConfigError(path, None, "is not valid", "correct the file")
        return json.loads(text)

    def keys(self, env):
        data = self._read(env)
        out = [{"name": "mode", "type": "enum", "values": ["a", "b"], "default": "a",
                "doc": "The mode.", "group": "start", "value": data.get("mode", "a"),
                "source": "file" if "mode" in data else "default"}]
        for name, value in data.items():
            if name != "mode":
                out.append({"name": name, "doc": "Held.", "value": value,
                            "source": "file", "refused": f"{name} is not a row",
                            "clear_only": True})
        return out

    def change(self, row, op, value, *, by, env):
        if value == "bad":
            raise ConfigError(self.file(env), row, f"cannot be {value!r}", "give a or b")
        if value == "held":
            raise files.LockTimeout(self.file(env).with_name("rows.lock"), 4242)
        data = self._read(env)
        if op == "unset":
            if row not in data:
                return False, []
            del data[row]
            self.file(env).write_text(json.dumps(data))
            return True, [f"{name} is not a row" for name in data if name != "mode"]
        else:
            data[row] = value
        self.file(env).write_text(json.dumps(data))
        return True, ["a use with no preset refuses it"] if value == "warn" else []


TABLE = _Rows()
'''


@pytest.fixture
def tabled(site, tmp_path, monkeypatch):
    """Module `demo` with one key of its own and the table `rows`, its config root and
    the core's state in the test's tmp. Returns the rows' file."""
    site.write("moxi_demo/settings.toml", SCHEMA)
    site.write("moxi_demo/rows.py", HOOK)
    site.module("demo", SETTINGS="'settings.toml'",
                CONFIG_TABLES="{'rows': 'moxi_demo.rows:TABLE'}")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MOXI_DEMO_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "core"))
    rows = tmp_path / "rows.json"
    monkeypatch.setenv("DEMO_ROWS_FILE", str(rows))
    return rows


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def show_json(capsys, module="demo"):
    code, out, _ = run(capsys, "config", module, "show", "--json")
    return code, json.loads(out)


def change_json(capsys, *argv):
    code, out, err = run(capsys, "config", *argv, "--json")
    assert out.count("\n") == 1
    return code, json.loads(out), err


def stored(rows: Path) -> dict:
    return json.loads(rows.read_text())


# -- show ----------------------------------------------------------------------------------

def test_show_json_lists_the_rows_after_the_modules_own_keys(tabled, capsys):
    tabled.write_text(json.dumps({"mode": "b"}))
    code, keys = show_json(capsys)
    assert code == 0
    assert [key["name"] for key in keys] == ["block", "rows.mode"]
    own, row = keys
    assert "table_title" not in own
    assert row["table_title"] == "Demo rows"
    assert row["table_line"] == "Every use starts from these."
    assert row["table_file"] == str(tabled)
    assert row["applies"] == "Applies to uses after this."
    assert (row["type"], row["values"], row["value"], row["source"]) == (
        "enum", ["a", "b"], "b", "file")
    assert (row["warning"], row["refused"], row["clear_only"]) == (None, None, False)
    assert set(own) <= set(row)


def test_show_json_carries_a_rows_refusal_and_clear_only(tabled, capsys):
    tabled.write_text(json.dumps({"typo": 1}))
    _, keys = show_json(capsys)
    typo = keys[-1]
    assert typo["name"] == "rows.typo"
    assert (typo["refused"], typo["clear_only"], typo["value"]) == (
        "typo is not a row", True, 1)
    assert typo["type"] == "string" and typo["read"] == "use"


def test_show_prints_the_rows_under_the_tables_title(tabled, capsys):
    run(capsys, "config", "set", "demo", "rows.mode", "warn")
    code, out, _ = run(capsys, "config", "demo")
    assert code == 0
    assert out.index("block = false") < out.index(f"# Demo rows: {tabled}")
    assert "#   Every use starts from these.\n#   Applies to uses after this.\n" in out
    assert 'rows.mode = "warn"    [file]\n    The mode.\n' in out
    assert "    last set by cli at " in out


def test_a_module_without_tables_shows_what_it_always_has(site, tmp_path, monkeypatch,
                                                         capsys):
    site.write("moxi_plain/settings.toml", SCHEMA)
    site.module("plain", SETTINGS="'settings.toml'")
    monkeypatch.setenv("MOXI_PLAIN_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "core"))
    code, keys = show_json(capsys, "plain")
    assert code == 0 and [key["name"] for key in keys] == ["block"]
    assert not any(field.startswith("table_") or field in ("applies", "clear_only")
                   for field in keys[0])
    path = tmp_path / "config" / "plain" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("block = [\n")
    code, error = show_json(capsys, "plain")
    assert code == 1 and set(error) == {"error", "fix"}


# -- one file can't be read ----------------------------------------------------------------

def test_a_table_file_that_cannot_be_read_is_one_item_and_the_keys_stay(tabled, capsys):
    tabled.write_text("broken")
    code, keys = show_json(capsys)
    assert code == 1
    assert [key["name"] for key in keys] == ["block", "rows"]
    assert keys[1] == {"name": "rows", "table_title": "Demo rows",
                       "table_line": "Every use starts from these.",
                       "table_file": str(tabled), "table_error": "is not valid",
                       "table_fix": "correct the file"}
    code, out, _ = run(capsys, "config", "demo")
    assert code == 1
    assert "block = false" in out
    assert "    error: is not valid; fix: correct the file\n" in out
    code, out, _ = run(capsys, "config", "set", "demo", "block", "true")
    assert code == 0


def test_a_config_file_that_cannot_be_read_leaves_the_rows_listed_and_set(tabled,
                                                                         tmp_path, capsys):
    path = tmp_path / "config" / "demo" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("block = [\n")
    code, error = show_json(capsys)
    assert code == 1
    assert set(error) == {"error", "fix", "keys"}
    assert [key["name"] for key in error["keys"]] == ["rows.mode"]
    code, out, _ = change_json(capsys, "set", "demo", "rows.mode", "b")
    assert code == 0 and out["changed"] is True
    assert stored(tabled) == {"mode": "b"}
    assert path.read_text() == "block = [\n"


# -- set and unset -------------------------------------------------------------------------

def test_set_and_unset_go_to_the_hook_and_append_one_history_line_each(tabled, capsys):
    code, out, err = run(capsys, "config", "set", "demo", "rows.mode", "b", "--by", "ui")
    assert (code, out, err) == (0, "demo: rows.mode set (by ui)\n", "")
    assert stored(tabled) == {"mode": "b"}
    code, out, _ = run(capsys, "config", "unset", "demo", "rows.mode")
    assert (code, out) == (0, "demo: rows.mode unset (by cli)\n")
    assert stored(tabled) == {}
    code, out, _ = run(capsys, "config", "unset", "demo", "rows.mode")
    assert (code, out) == (0, "demo: rows.mode is not set in the file; nothing changed\n")
    records = config.history("demo", "rows.mode")
    assert [(r["op"], r["by"]) for r in records] == [("set", "ui"), ("unset", "cli")]
    assert all(set(r) == {"module", "key", "op", "by", "at"} for r in records)
    assert not (tabled.parent / "config" / "demo" / "config.toml").exists()


def test_set_prints_the_hooks_warning_lines_after_its_own(tabled, capsys):
    code, out, _ = run(capsys, "config", "set", "demo", "rows.mode", "warn")
    assert code == 0
    assert out == ("demo: rows.mode set (by cli)\n"
                   "warning: a use with no preset refuses it\n")
    code, answer, err = change_json(capsys, "set", "demo", "rows.mode", "warn")
    assert (code, answer) == (0, {"changed": True, "problem": None, "fix": None,
                                  "units": []})
    assert err == "warning: a use with no preset refuses it\n"


def test_unset_prints_the_lines_the_table_carries_after_it(tabled, capsys):
    tabled.write_text('{"mode": "b", "stray": 1, "other": 2, "third": 3}')
    code, out, _ = run(capsys, "config", "unset", "demo", "rows.stray")
    assert code == 0
    assert out == ("demo: rows.stray unset (by cli)\n"
                   "warning: other is not a row\n"
                   "warning: third is not a row\n")
    code, answer, err = change_json(capsys, "unset", "demo", "rows.other")
    assert (code, answer) == (0, {"changed": True, "problem": None, "fix": None,
                                  "units": []})
    assert err == "warning: third is not a row\n"
    code, out, _ = run(capsys, "config", "unset", "demo", "rows.third")
    assert (code, out) == (0, "demo: rows.third unset (by cli)\n")


def test_a_row_name_holding_a_dot_reaches_the_hook_whole(tabled, capsys):
    code, _, _ = run(capsys, "config", "set", "demo", "rows.two.parts", "x")
    assert code == 0
    assert stored(tabled) == {"two.parts": "x"}
    assert config.history("demo", "rows.two.parts")[0]["op"] == "set"


@pytest.mark.parametrize("argv, code, problem, fix", [
    (("set", "demo", "rows.mode", "bad"), 2, "cannot be 'bad'", "give a or b"),
    (("set", "demo", "rows.mode", "--add", "x"), 2,
     "is a row of demo's rows table, which takes a whole value", "change the whole value"),
    (("set", "demo", "rows.mode", "--remove", "x"), 2,
     "is a row of demo's rows table, which takes a whole value", "change the whole value"),
    (("set", "demo", "rows.mode", "held"), 75, None, "try again once it is free"),
])
def test_a_refused_row_change_keeps_a_config_keys_exit_and_answer(tabled, capsys, argv,
                                                                  code, problem, fix):
    got, answer, _ = change_json(capsys, *argv)
    assert got == code
    assert answer["changed"] is False and answer["fix"] == fix
    if problem is not None:
        assert answer["problem"] == problem
    else:
        assert answer["problem"].endswith("is locked by pid 4242")
    assert not tabled.exists()
    assert config.history("demo") == []


def test_the_table_alone_names_no_row(tabled, capsys):
    code, answer, _ = change_json(capsys, "set", "demo", "rows.", "a")
    assert code == 2 and answer["fix"] == "name one row: rows.<row>"


def test_a_module_that_is_absent_or_a_hook_that_cannot_run_exits_3(site, tabled, capsys):
    code, _, err = run(capsys, "config", "set", "nothere", "rows.mode", "a")
    assert code == 3 and "moxi install nothere" in err
    site.module("lost", CONFIG_TABLES="{'rows': 'moxi_lost.rows:TABLE'}")
    code, answer, _ = change_json(capsys, "set", "lost", "rows.mode", "a")
    assert code == 3
    assert "cannot run lost's CONFIG_TABLES moxi_lost.rows:TABLE" in answer["problem"]


def test_a_table_named_as_a_top_level_setting_is_refused(site, tabled, capsys):
    site.write("moxi_clash/settings.toml", '[rows.mode]\ntype = "bool"\ndefault = false\n'
                                           'doc = "A row."\nread = "use"\n')
    site.write("moxi_clash/rows.py", HOOK)
    site.module("clash", SETTINGS="'settings.toml'",
                CONFIG_TABLES="{'rows': 'moxi_clash.rows:TABLE'}")
    code, answer, _ = change_json(capsys, "set", "clash", "rows.mode", "a")
    assert code == 3
    assert "declares the table 'rows' in CONFIG_TABLES" in answer["problem"]
    code, keys = show_json(capsys, "clash")
    assert code == 1
    assert keys[-1]["name"] == "rows" and "settings schema also uses" in keys[-1][
        "table_error"]
    assert not tabled.exists()


# -- moxi modules --------------------------------------------------------------------------

def test_moxi_modules_json_lists_the_tables(tabled):
    listed = {item["name"]: item for item in cli.describe()["modules"]}
    assert listed["demo"]["config_tables"] == {"rows": "moxi_demo.rows:TABLE"}
