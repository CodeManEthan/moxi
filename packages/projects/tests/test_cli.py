"""`moxi projects`: the verbs, their output, `--json` and their exit codes."""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from moxi_projects import api, cli, store

TOPIC = '''
INTERFACE = 1
KINDS = {"topic": ("a notes topic", lambda fields: None)}
'''


def holds(sentence: str) -> str:
    return f'''
INTERFACE = 1

def refuse_remove(project):
    return {sentence!r}
'''


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def run_json(capsys, *argv: str):
    code, out, err = run(capsys, *argv, "--json")
    assert code == 0, err
    return json.loads(out)


# --- add ---------------------------------------------------------------------------------

def test_add_prints_the_row_as_written_and_notes_a_missing_root(capsys, tmp_path):
    code, out, err = run(capsys, "add", "web", "--root", str(tmp_path / "web"),
                         "--lead", "boss")
    assert code == 0
    assert out.splitlines()[0] == "web"
    assert "boss" in out and str(tmp_path / "web") in out
    assert "doesn't exist yet" in err
    assert api.get("web").lead == "boss"


def test_add_makes_a_relative_root_absolute(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "here").mkdir()
    code, _out, err = run(capsys, "add", "here", "--root", "here")
    assert code == 0 and err == ""
    assert api.get("here").root == str(tmp_path / "here")


def test_add_expands_the_home_directory(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert run(capsys, "add", "h", "--root", "~/h")[0] == 0
    assert api.get("h").root == str(tmp_path / "h")


def test_add_json_prints_the_read_api_fields(capsys, tmp_path):
    got = run_json(capsys, "add", "web", "--root", str(tmp_path), "--kind", "agent")
    assert set(got) == {"key", "root", "kind", "lead", "associated", "notes_root",
                        "created", "agents"}
    assert got["kind"] == "agent" and got["associated"] == [] and got["agents"] == []


def test_add_without_a_root_and_no_default_is_exit_2(capsys):
    code, out, err = run(capsys, "add", "web")
    assert code == 2 and out == ""
    assert "--root DIR" in err


@pytest.mark.parametrize("argv, code", [
    (("add", "bad."), 3),
    (("add", "con", "--root", "/srv/con"), 3),
    (("add", "web", "--root", "/srv/web", "--kind", "nosuch"), 3),
    (("add", "web", "--root", "/srv/web", "--notes-root", "/srv/notes"), 3),
    (("add", "web", "--root", "/srv/web", "--lead", "two words"), 3),
])
def test_add_refusals_are_exit_3_and_print_the_fix(capsys, argv, code, site):
    got, out, err = run(capsys, *argv)
    assert got == code and out == ""
    assert "fix:" in err
    assert api.list_projects() == []


def test_add_of_a_taken_key_or_root_is_exit_3(capsys, golden_copy):
    code, _out, err = run(capsys, "add", "ALPHA", "--root", "/srv/other")
    assert code == 3 and "taken" in err and "moxi projects show alpha" in err
    code, _out, err = run(capsys, "add", "new", "--root", os.path.join(golden_copy, "alpha"))
    assert code == 3 and "alpha" in err


def test_a_usage_error_from_argparse_is_exit_2(capsys):
    assert cli.main(["add"]) == 2
    assert cli.main(["nosuch"]) == 2
    assert cli.main(["set", "k", "--lead", "a", "--no-lead"]) == 2
    capsys.readouterr()


@pytest.mark.parametrize("argv", [
    ("add", "web", "--root", ""),
    ("add", "web", "--root", "/srv/web", "--notes-root", ""),
    ("set", "gamma", "--root", ""),
    ("set", "gamma", "--notes-root", ""),
])
def test_an_empty_directory_is_exit_2_not_the_working_directory(capsys, golden_copy,
                                                                 tmp_path, monkeypatch,
                                                                 argv):
    monkeypatch.chdir(tmp_path)
    before = api.list_projects()
    code, out, err = run(capsys, *argv)
    assert code == 2 and out == ""
    assert err.startswith("usage:") and "is empty; name a directory" in err
    assert api.list_projects() == before


def test_help_is_exit_0(capsys):
    assert cli.main(["--help"]) == 0
    assert "add" in capsys.readouterr().out


# --- set ---------------------------------------------------------------------------------

def test_set_changes_named_fields_and_prints_the_row(capsys, golden_copy, tmp_path):
    code, out, _err = run(capsys, "set", "gamma", "--root", str(tmp_path), "--kind", "agent")
    assert code == 0 and out.splitlines()[0] == "gamma"
    got = api.get("gamma")
    assert got.root == str(tmp_path) and got.kind == "agent" and got.lead == "lead-a"


def test_set_sets_and_clears_the_lead(capsys, golden_copy):
    assert run_json(capsys, "set", "beta", "--lead", "new")["lead"] == "new"
    assert run_json(capsys, "set", "beta", "--no-lead")["lead"] is None


def test_set_with_no_field_named_is_exit_2(capsys, golden_copy):
    code, _out, err = run(capsys, "set", "gamma")
    assert code == 2 and "--no-lead" in err


def test_set_of_an_unknown_key_is_exit_3(capsys, golden_copy):
    code, _out, err = run(capsys, "set", "nosuch", "--kind", "agent")
    assert code == 3 and "moxi projects add nosuch" in err


def test_set_refuses_a_notes_root_without_notes_and_clears_one(capsys, golden_copy, site):
    code, _out, err = run(capsys, "set", "gamma", "--notes-root", "/srv/n")
    assert code == 3 and "moxi install notes" in err
    assert run_json(capsys, "set", "gamma", "--no-notes-root")["notes_root"] is None


def test_set_of_a_lead_in_the_set_is_exit_3(capsys, golden_copy):
    code, _out, err = run(capsys, "set", "alpha", "--lead", "helper")
    assert code == 3 and "moxi projects agents remove alpha helper" in err


# --- remove ------------------------------------------------------------------------------

def test_remove_prints_the_row_as_it_was(capsys, golden_copy):
    code, out, _err = run(capsys, "remove", "Gamma")
    assert code == 0 and out.startswith("removed project gamma")
    with pytest.raises(api.NotFound):
        api.get("gamma")


def test_remove_json_prints_the_removed_row(capsys, golden_copy):
    assert run_json(capsys, "remove", "beta")["key"] == "Beta"


def test_remove_refuses_while_agents_are_associated(capsys, golden_copy):
    code, _out, err = run(capsys, "remove", "alpha")
    assert code == 3 and "moxi projects agents remove alpha" in err
    assert api.get("alpha")


def test_remove_lists_every_refusal(capsys, site, golden_copy):
    site.contribution("svc", holds("services run for it; `moxi services stop` frees it"))
    site.contribution("links", holds("two watches name it; `moxi links clear` frees it"))
    code, _out, err = run(capsys, "remove", "gamma")
    assert code == 3
    assert "svc: services run" in err and "links: two watches" in err
    assert "moxi projects remove gamma" in err
    assert api.get("gamma")


def test_remove_asks_the_contributions_outside_any_write(site, golden_copy, monkeypatch):
    site.contribution("svc", '''
import sqlite3

from moxi_projects import api

INTERFACE = 1
SEEN = []

def refuse_remove(project):
    # Take the store's write lock on a second connection without waiting: it is free
    # unless the remove asks this inside its write.
    conn = sqlite3.connect(api._store_path(), timeout=0, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
        SEEN.append(("asked, lock free", project.key))
    except sqlite3.OperationalError:
        SEEN.append(("asked, lock held", project.key))
    finally:
        conn.close()
    return None
''')
    from moxi_projects import acts, contributions

    found = contributions.load()
    seen = found.loaded[0].refuse_remove.__globals__["SEEN"]
    write = store.write

    def watched(fn, where=None):
        seen.append("write")
        return write(fn, where)

    monkeypatch.setattr(store, "write", watched)
    acts.remove("gamma", contributions=found)
    assert seen == [("asked, lock free", "gamma"), "write"]
    with pytest.raises(api.NotFound):
        api.get("gamma")


def test_remove_past_an_entry_that_wont_load_is_exit_1(capsys, site, golden_copy):
    site.contribution("broken", "raise ImportError('gone')\n")
    code, _out, err = run(capsys, "remove", "gamma")
    assert code == 1 and "broken" in err and "moxi uninstall broken" in err
    assert api.get("gamma")


def test_add_past_an_entry_that_wont_load_warns_on_one_line_and_writes_the_row(
        capsys, site, golden_copy):
    site.contribution("broken", "INTERFACE = 9\n")
    code, out, err = run(capsys, "add", "new", "--root", "/srv/new")
    assert code == 0 and out.startswith("new\n")
    [line] = [line for line in err.splitlines() if line.startswith("warning: ")]
    assert line.startswith("warning: skipped broken, which won't load")
    assert "defaults and checks were not applied" in line
    assert "moxi uninstall broken" in line
    assert api.get("new").root == "/srv/new"


def test_a_multi_line_load_failure_warns_and_refuses_on_one_line(capsys, site, golden_copy):
    site.contribution("broken", "raise ImportError('first line\\nsecond line')\n")
    code, out, err = run(capsys, "add", "new", "--root", "/srv/new")
    assert code == 0 and out.startswith("new\n")
    assert all(line.startswith(("note: ", "warning: ")) for line in err.splitlines())
    [line] = [line for line in err.splitlines() if line.startswith("warning: ")]
    assert line.startswith("warning: skipped broken") and "first line second line" in line
    assert api.get("new").root == "/srv/new"
    code, _out, err = run(capsys, "remove", "gamma")
    assert code == 1 and "first line second line" in err


def test_add_json_past_an_entry_that_wont_load_carries_the_warning(capsys, site,
                                                                   golden_copy):
    site.contribution("broken", "INTERFACE = 9\n")
    code, out, err = run(capsys, "add", "new", "--root", "/srv/new", "--json")
    assert code == 0 and "warning: skipped broken" in err
    body = json.loads(out)
    assert {k: v for k, v in body.items() if k != "warnings"} == api.get("new").as_dict()
    [warning] = body["warnings"]
    assert warning.startswith("skipped broken") and "moxi uninstall broken" in warning


def test_add_json_that_skips_nothing_is_the_project_alone(capsys, golden_copy):
    body = run_json(capsys, "add", "new", "--root", "/srv/new")
    assert body == api.get("new").as_dict()


def test_add_or_set_of_an_unknown_kind_past_an_entry_that_wont_load_is_exit_1(
        capsys, site, golden_copy):
    site.contribution("broken", "INTERFACE = 9\n")
    code, _out, err = run(capsys, "add", "new", "--root", "/srv/new", "--kind", "topic")
    assert code == 1 and "broken" in err and "moxi uninstall broken" in err
    with pytest.raises(api.NotFound):
        api.get("new")
    assert run(capsys, "set", "gamma", "--kind", "agent")[0] == 0
    assert run(capsys, "set", "gamma", "--kind", "topic")[0] == 1


def test_remove_of_an_unknown_key_is_exit_3(capsys, golden_copy):
    assert run(capsys, "remove", "nosuch")[0] == 3


def test_a_refused_remove_writes_nothing_to_the_store(capsys, site, v1_copy, store_path):
    before = store_path.read_bytes()
    assert run(capsys, "remove", "alpha")[0] == 3
    assert run(capsys, "remove", "nosuch")[0] == 3
    site.contribution("svc", holds("services run for it; `moxi services stop` frees it"))
    assert run(capsys, "remove", "gamma")[0] == 3
    assert store_path.read_bytes() == before


def test_an_unknown_key_with_no_store_is_exit_3_and_makes_no_file(capsys, store_path):
    assert run(capsys, "remove", "nosuch")[0] == 3
    assert not store_path.exists()


def test_a_remove_by_the_verb_keeps_no_status(capsys, golden_copy, store_path):
    assert run(capsys, "remove", "gamma")[0] == 0
    conn = sqlite3.connect(store_path)
    try:
        assert conn.execute("SELECT count(*) FROM remove_status").fetchone()[0] == 0
    finally:
        conn.close()


# --- list, show, find, kinds -------------------------------------------------------------

def test_list_prints_every_project_by_key(capsys, golden_copy):
    code, out, _err = run(capsys, "list")
    assert code == 0
    assert [line.split()[0] for line in out.splitlines()] == ["alpha", "Beta", "delta.web",
                                                              "gamma"]


def test_list_by_kind_and_as_json(capsys, golden_copy):
    got = run_json(capsys, "list", "--kind", "agent")
    assert [p["key"] for p in got] == ["Beta"]


def test_list_of_no_projects_says_how_to_add_one(capsys):
    code, out, _err = run(capsys, "list")
    assert code == 0 and "moxi projects add" in out
    assert run_json(capsys, "list") == []


def test_show_prints_the_project_with_its_agents(capsys, golden_copy):
    code, out, _err = run(capsys, "show", "ALPHA")
    assert code == 0 and out.splitlines()[0] == "alpha"
    assert "lead-a" in out and "helper scout" in out
    got = run_json(capsys, "show", "alpha")
    assert got["agents"] == ["lead-a", "helper", "scout"]


def test_show_of_an_unknown_key_is_exit_3(capsys, golden_copy):
    code, out, err = run(capsys, "show", "nosuch")
    assert code == 3 and out == "" and "moxi projects list" in err


def test_find_prints_the_key_or_nothing(capsys, golden_copy, tmp_path):
    inside = os.path.join(golden_copy, "alpha", "src")
    assert run(capsys, "find", inside) == (0, "alpha\n", "")
    assert run(capsys, "find", str(tmp_path / "elsewhere")) == (0, "", "")


def test_find_json_prints_the_project_or_null(capsys, golden_copy, tmp_path):
    assert run_json(capsys, "find", os.path.join(golden_copy, "gamma"))["key"] == "gamma"
    assert run_json(capsys, "find", str(tmp_path)) is None


def test_find_defaults_to_the_working_directory(capsys, tmp_path, monkeypatch):
    (tmp_path / "web" / "src").mkdir(parents=True)
    assert run(capsys, "add", "web", "--root", str(tmp_path / "web"))[0] == 0
    monkeypatch.chdir(tmp_path / "web" / "src")
    assert run(capsys, "find")[1] == "web\n"


def test_kinds_prints_found_and_broken(capsys, site):
    site.contribution("notes", TOPIC)
    site.contribution("broken", "INTERFACE = 9\n")
    code, out, _err = run(capsys, "kinds")
    assert code == 0
    assert any(line.split()[:3] == ["topic", "notes", "git"] for line in out.splitlines())
    assert "broken:" in out
    got = run_json(capsys, "kinds")
    assert {k["name"] for k in got["found"]} == {"agent", "project", "topic"}
    assert [b["module"] for b in got["broken"]] == ["broken"]


# --- agents ------------------------------------------------------------------------------

def test_agents_add_and_remove(capsys, golden_copy):
    assert run_json(capsys, "agents", "add", "gamma", "new")["associated"] == ["new"]
    assert run_json(capsys, "agents", "remove", "gamma", "new")["associated"] == []


def test_agents_remove_of_a_pair_that_isnt_there_is_exit_3(capsys, golden_copy):
    code, _out, err = run(capsys, "agents", "remove", "gamma", "nobody")
    assert code == 3 and err.startswith("moxi projects agents remove:")


def test_agents_add_of_the_lead_is_exit_3(capsys, golden_copy):
    assert run(capsys, "agents", "add", "alpha", "lead-a")[0] == 3


# --- the store's states ------------------------------------------------------------------

def _set_user_version(path, version: int) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("argv", [("list",), ("show", "alpha"), ("find", "/"),
                                  ("set", "gamma", "--kind", "agent"),
                                  ("agents", "add", "gamma", "x"),
                                  ("add", "new", "--root", "/srv/new")])
def test_a_newer_store_is_exit_3_on_a_read_and_a_write(capsys, golden_copy, store_path,
                                                       argv):
    _set_user_version(store_path, api._VERSION + 1)
    code, _out, err = run(capsys, *argv)
    assert code == 3 and "moxi install projects" in err


def test_a_store_locked_past_the_wait_is_exit_75(capsys, golden_copy, store_path,
                                                 monkeypatch):
    monkeypatch.setattr(store, "TIMEOUT", 0.05)
    holder = sqlite3.connect(store_path, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        code, _out, err = run(capsys, "agents", "add", "gamma", "x")
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert code == 75 and "try again" in err


def test_the_parser_is_built_once():
    assert cli._parser("moxi projects") is cli._parser("moxi projects")
