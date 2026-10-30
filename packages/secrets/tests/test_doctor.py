"""The doctor rows: each row in each state, called through `rows()` and its row functions,
and no row's text holding an entry of the private names list."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys

import pytest
from moxi.doctor import Row

from moxi_secrets import doctor, engine, fallback

scan_module = importlib.import_module("moxi_secrets.scan")

NAMES = ("rules", "engine", "private-names", "private-names-location", "personal-data")

#: Made-up entries, each one a string no row would write by chance.
WORDS = ["Zorblax Quindle", "vexmoor-qat"]
ANYWHERE = ["plimtrox"]
EMAILS = ["yarrowfen@example.org"]
ALLOWED = ["zorblax-quindle-public"]
EVERY_ENTRY = WORDS + ANYWHERE + EMAILS + ALLOWED


def text_of(row: Row) -> str:
    return f"{row.detail}\n{row.fix or ''}".casefold()


def assert_no_entry(row: Row, entries) -> None:
    text = text_of(row)
    for entry in entries:
        assert entry.casefold() not in text, (row.name, row.state)


def test_rows_gives_the_five_rows_in_order():
    found = doctor.rows()
    assert [r.name for r in found] == list(NAMES)
    assert all(isinstance(r, Row) and r.module == "secrets" for r in found)


def test_the_announcement_names_rows():
    from moxi_secrets import module

    target, _, fn = module.DOCTOR.partition(":")
    assert getattr(importlib.import_module(target), fn) is doctor.rows


# -- rules ----------------------------------------------------------------------------------

def test_rules_ok_counts_the_pinned_sets():
    row = doctor.rules_row()
    assert row.state == "ok"
    assert row.detail == ("credentials 231 rules (221 from gitleaks v8.30.1, 10 local), "
                          "broad 3, 0 dropped")
    assert row.fix is None


@pytest.fixture
def rules_copy(tmp_path, monkeypatch):
    """A copy of rules/ that the scan reads instead of the package's own."""
    copy = tmp_path / "rules"
    shutil.copytree(scan_module._RULES_DIR, copy)
    monkeypatch.setattr(scan_module, "_RULES_DIR", str(copy))
    monkeypatch.setattr(scan_module, "_sets", None)
    yield copy
    scan_module._sets = None


@pytest.mark.parametrize("damage", ["missing", "not json", "wrong shape"])
def test_rules_fail_when_sets_json_will_not_load(rules_copy, damage):
    target = rules_copy / "sets.json"
    if damage == "missing":
        target.unlink()
    elif damage == "not json":
        target.write_bytes(b'{"format": 1, "upstream": ')
    else:
        target.write_text(json.dumps({"format": 1, "upstream": {}, "allowlists": []}))
    row = doctor.rules_row()
    assert row.state == "fail"
    assert row.detail.startswith("sets.json will not load (")
    assert row.fix == "reinstall moxi-secrets"


def test_rules_fail_when_the_pin_differs_from_the_upstream_bytes(rules_copy):
    upstream = rules_copy / "upstream" / "gitleaks.toml"
    upstream.write_bytes(upstream.read_bytes() + b"\n# changed\n")
    row = doctor.rules_row()
    assert row.state == "fail"
    assert "does not match" in row.detail and "v8.30.1" in row.detail
    assert row.fix == "reinstall moxi-secrets"


def test_rules_fail_when_the_upstream_file_is_missing(rules_copy):
    (rules_copy / "upstream" / "gitleaks.toml").unlink()
    row = doctor.rules_row()
    assert row.state == "fail"
    assert "cannot be read" in row.detail


def test_rules_warn_when_a_rule_is_dropped(rules_copy):
    target = rules_copy / "sets.json"
    data = json.loads(target.read_bytes())
    gone = data["sets"]["broad"]["rules"].pop()
    data["sets"]["broad"]["dropped"].append(
        {"id": gone["id"], "source": gone["source"], "reason": "made up for the test"})
    target.write_text(json.dumps(data))
    row = doctor.rules_row()
    assert row.state == "warn"
    assert row.detail.startswith("credentials 231 rules (221 from gitleaks v8.30.1, 10 "
                                 "local), broad 2, 1 dropped: ")
    assert f"broad/{gone['id']}" in row.detail


# -- engine ---------------------------------------------------------------------------------

def test_engine_ok_on_re2_names_the_binding_version():
    pytest.importorskip("re2")
    engine.reset()
    row = doctor.engine_row()
    assert row.state == "ok"
    assert row.detail.startswith("RE2 (google-re2 ") and row.detail.endswith(")")
    assert row.detail != "RE2 (google-re2 unknown)"


@pytest.fixture
def no_worker():
    fallback.shutdown()
    yield
    fallback.shutdown()


def test_engine_ok_on_the_fallback_where_no_wheel_fits(fallback_engine, no_worker,
                                                       monkeypatch):
    monkeypatch.setattr(engine, "re2_wheel_fits", lambda facts=None: False)
    row = doctor.engine_row()
    assert row == Row("secrets", "engine", "ok", "fallback: Python re with a deadline "
                      "(no google-re2 wheel for this platform)")
    assert fallback._worker is not None and fallback._worker.alive()   # it scanned


def test_engine_warn_on_the_fallback_where_a_wheel_fits(fallback_engine, no_worker,
                                                        monkeypatch):
    monkeypatch.setattr(engine, "re2_wheel_fits", lambda facts=None: True)
    row = doctor.engine_row()
    assert row == Row("secrets", "engine", "warn",
                      "fallback: large or unusual texts may come back unscanned",
                      "moxi install secrets --with re2")


def test_engine_warn_when_re2_is_installed_but_will_not_import(no_worker, monkeypatch,
                                                               tmp_path):
    # A re2 whose import raises stands for a broken install.
    (tmp_path / "re2.py").write_text("raise RuntimeError('a broken binding')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "re2", raising=False)
    engine.reset()
    monkeypatch.setattr(engine, "re2_wheel_fits", lambda facts=None: True)
    try:
        row = doctor.engine_row()
    finally:
        engine.reset()
    assert (row.state, row.fix) == ("warn", "moxi install secrets --with re2")


WORKER_FAILURES = {
    "will not start": (lambda tmp: [str(tmp / "no-such-python"), "-c", "pass"],
                       "the worker will not start"),
    "exits": (lambda tmp: [sys.executable, "-c", "import sys; sys.stdin.readline()"],
              "the worker exited"),
    "garbage": (lambda tmp: [sys.executable, "-c", "import sys; sys.stdin.readline(); "
                             "print('not json', flush=True)"],
                "the worker sent a reply that does not parse"),
}


@pytest.mark.parametrize("fits", [True, False])
@pytest.mark.parametrize("how", sorted(WORKER_FAILURES))
def test_engine_fail_names_how_the_worker_failed(fallback_engine, no_worker, monkeypatch,
                                                 tmp_path, how, fits):
    argv, said = WORKER_FAILURES[how]
    monkeypatch.setattr(fallback, "command", lambda: argv(tmp_path))
    monkeypatch.setattr(engine, "re2_wheel_fits", lambda facts=None: fits)
    row = doctor.engine_row()
    assert row.state == "fail"
    assert row.detail.startswith("fallback: its worker failed a short scan: " + said)
    assert row.fix == ("moxi install secrets --with re2" if fits else None)


def test_engine_fail_when_the_worker_misses_its_deadline(fallback_engine, no_worker,
                                                         monkeypatch):
    monkeypatch.setattr(fallback, "command", lambda: [
        sys.executable, "-c", "import sys, time; sys.stdin.readline(); time.sleep(30)"])
    monkeypatch.setattr(fallback, "BASE_DEADLINE", 0.3)
    row = doctor.engine_row()
    assert row.state == "fail"
    assert row.detail.endswith("the scan reached its deadline")


# -- private names --------------------------------------------------------------------------

def test_private_names_off_without_a_config_file():
    row = doctor.private_names_row()
    assert row == Row("secrets", "private-names", "off",
                      "the list is empty, so the private-names set finds nothing",
                      "moxi config set secrets private_names.words --add NAME")


def test_private_names_off_with_only_allowed_entries(names_config):
    names_config(allowed=ALLOWED, author_name="Zorblax Q", author_email=EMAILS[0])
    row = doctor.private_names_row()
    assert row.state == "off"
    assert_no_entry(row, EVERY_ENTRY + ["Zorblax Q"])


def test_private_names_ok_gives_the_counts(names_config):
    names_config(words=WORDS, anywhere=ANYWHERE, emails=EMAILS, allowed=ALLOWED,
                 author_name="Zorblax Q", author_email=EMAILS[0])
    row = doctor.private_names_row()
    assert row == Row("secrets", "private-names", "ok",
                      "words 2, anywhere 1, emails 1, allowed 1")


def test_private_names_warn_by_key_and_position(names_config):
    names_config(words=WORDS + ["qx", " - _ "], anywhere=["  zq  "], emails=EMAILS,
                 allowed=ALLOWED + ["_"])
    row = doctor.private_names_row()
    assert row.state == "warn"
    assert row.detail == (
        "words 4, anywhere 1, emails 1, allowed 2; "
        "private_names.words entry 3 is shorter than 3 characters; "
        "private_names.words entry 4 is empty once separators are trimmed, so it is left "
        "out; "
        "private_names.anywhere entry 1 is shorter than 3 characters; "
        "private_names.allowed entry 2 is empty once separators are trimmed, so it is left "
        "out")
    assert row.fix == "moxi config secrets"
    assert_no_entry(row, EVERY_ENTRY + ["qx", "zq"])


def test_a_three_character_entry_gets_no_warning(names_config):
    names_config(words=["-qxz-"])
    assert doctor.private_names_row().state == "ok"


def test_private_names_fail_on_an_item_that_is_not_a_string(names_config):
    names_config(raw=f'[private_names]\nwords = ["{WORDS[0]}", 7, "{WORDS[1]}"]\n'
                     f'emails = ["{EMAILS[0]}"]\n')
    row = doctor.private_names_row()
    assert row.state == "fail"
    assert "private_names.words" in row.detail and "item 2" in row.detail
    assert row.fix == "moxi config secrets check"
    assert_no_entry(row, EVERY_ENTRY)


def test_private_names_fail_when_the_config_will_not_load(names_config):
    names_config(raw=f'[private_names]\nwords = ["{WORDS[0]}", "{ANYWHERE[0]}"\n')
    row = doctor.private_names_row()
    assert row.state == "fail"
    assert row.detail.startswith("the private names list will not load")
    assert_no_entry(row, EVERY_ENTRY)


def test_private_names_fail_on_a_value_of_the_wrong_type(names_config):
    names_config(raw=f'[private_names]\nwords = "{WORDS[0]}"\n')
    row = doctor.private_names_row()
    assert row.state == "fail" and "private_names.words" in row.detail
    assert_no_entry(row, EVERY_ENTRY)


# -- the config file's location -------------------------------------------------------------

@pytest.fixture
def config_home(monkeypatch):
    """Point secrets' config root at a directory: config_home(path)."""
    def use(path):
        monkeypatch.setenv("MOXI_SECRETS_CONFIG_HOME", str(path))
        return path
    return use


def test_location_ok_outside_any_work_tree(config_home, tmp_path):
    config_home(tmp_path / "plain" / "config")
    row = doctor.private_names_location_row()
    assert row.state == "ok"
    assert "private allowlist" in row.detail


@pytest.mark.parametrize("git", ["directory", "file"])
@pytest.mark.parametrize("depth", ["the config root", "a parent", "a grandparent"])
def test_location_warn_inside_a_work_tree(config_home, tmp_path, git, depth):
    repo = tmp_path / "dotfiles"
    root = {"the config root": repo, "a parent": repo / "conf",
            "a grandparent": repo / "a" / "b" / "conf"}[depth]
    root.mkdir(parents=True)
    if git == "directory":
        (repo / ".git").mkdir()
    else:   # a linked work tree or a submodule has a .git file
        (repo / ".git").write_text("gitdir: elsewhere\n")
    config_home(root)
    row = doctor.private_names_location_row()
    assert row.state == "warn"
    assert "private names list" in row.detail and "private allowlist" in row.detail
    assert "MOXI_SECRETS_CONFIG_HOME" in row.fix and "MOXI_HOME" in row.fix


@pytest.mark.skipif(sys.platform == "win32",
                    reason="creating a directory link needs a privilege Windows runners "
                           "may lack")
def test_location_warn_when_the_config_directory_links_into_a_work_tree(config_home,
                                                                         tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "kept").mkdir()
    root = tmp_path / "plain"
    root.mkdir()
    os.symlink(repo / "kept", root / "secrets", target_is_directory=True)
    config_home(root)
    assert doctor.private_names_location_row().state == "warn"


def test_location_ok_inside_a_git_directory(config_home, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git" / "moxi").mkdir(parents=True)
    config_home(repo / ".git" / "moxi")
    assert doctor.private_names_location_row().state == "ok"


def test_no_row_prints_the_config_path(config_home, tmp_path):
    """A config path can hold a login that is on the list."""
    from moxi.paths import Layout

    login = "plimtrox"
    repo = tmp_path / login / "home"
    (repo / ".git").mkdir(parents=True)
    config_home(repo / "config")
    path = Layout("secrets").config_file()
    assert login in str(path)
    path.parent.mkdir(parents=True)
    path.write_text(f'[private_names]\nwords = ["{login}", "qq"]\n'
                    f'emails = ["{EMAILS[0]}"]\n', encoding="utf-8")
    found = doctor.rows()
    assert {r.name: r.state for r in found}["private-names-location"] == "warn"
    assert {r.name: r.state for r in found}["private-names"] == "warn"
    for row in found:
        assert_no_entry(row, [login, "qq"] + EMAILS)
        assert str(tmp_path) not in row.detail + (row.fix or "")


# -- personal data --------------------------------------------------------------------------

def test_personal_data_ok_when_stdnum_imports():
    row = doctor.personal_data_row()
    assert row.state == "ok"
    assert row.detail.startswith("python-stdnum ") and row.detail.endswith(" imports")


def test_personal_data_off_without_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "stdnum", None)
    monkeypatch.setattr(doctor, "_stdnum_version", lambda: None)
    row = doctor.personal_data_row()
    assert row == Row("secrets", "personal-data", "off",
                      "python-stdnum is not installed, so the personal-data set is off",
                      "moxi install secrets --with personal-data")


def test_personal_data_fail_when_installed_but_broken(monkeypatch):
    monkeypatch.setitem(sys.modules, "stdnum", None)
    monkeypatch.setattr(doctor, "_stdnum_version", lambda: "2.2")
    row = doctor.personal_data_row()
    assert row.state == "fail"
    # Hidden from import, the name raises ModuleNotFoundError, a kind of ImportError.
    assert row.detail.startswith("python-stdnum 2.2 is installed but will not import (")
    assert row.fix == "moxi install secrets --with personal-data"


# -- no entry in any row, in any state ------------------------------------------------------

@pytest.mark.parametrize("state", ["ok", "warn", "fail"])
def test_no_row_holds_an_entry_of_the_list(names_config, state):
    if state == "ok":
        names_config(words=WORDS, anywhere=ANYWHERE, emails=EMAILS, allowed=ALLOWED,
                     author_name="Zorblax Q", author_email=EMAILS[0])
    elif state == "warn":
        names_config(words=WORDS + ["zq"], anywhere=ANYWHERE + ["--"], emails=EMAILS,
                     allowed=ALLOWED)
    else:
        names_config(raw=f'[private_names]\nwords = ["{WORDS[0]}", ["{WORDS[1]}"]]\n'
                         f'anywhere = ["{ANYWHERE[0]}"]\nemails = ["{EMAILS[0]}"]\n')
    found = doctor.rows()
    assert {r.name: r.state for r in found}["private-names"] == state
    for row in found:
        assert_no_entry(row, EVERY_ENTRY + ["Zorblax Q", "zq"])
