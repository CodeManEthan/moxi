"""Contributions through the `moxi.projects` group: kinds, defaults and remove refusals,
from fake distributions on a site the test installs."""

from __future__ import annotations

import os
import sys

import pytest

from moxi_projects import api, contributions, store
from moxi_projects.errors import Broken, Refused, Usage

TOPIC = '''
INTERFACE = 1

def check(fields):
    if not fields["root"].endswith("/notes/" + fields["key"]):
        return "a topic's root is its notes folder"
    return None

KINDS = {"topic": ("a notes topic", check)}
'''

HOLDS = '''
INTERFACE = 1

def refuse_remove(project):
    if project.key == "held":
        return "services still run for held; stop them with `moxi services stop held`"
    return None
'''


def prepare_source(defaults: str, kinds: str = "{}", interface: int = 2) -> str:
    return f'''
INTERFACE = {interface}
KINDS = {kinds}
SEEN = []

def prepare(fields):
    SEEN.append(dict(fields))
    return {defaults}
'''


def add(key, **kwargs):
    """Plan an add and write it, as the add verb will."""
    planned = contributions.plan_add(key, **kwargs)
    return store.write(planned.insert)


# --- kinds -------------------------------------------------------------------------------

def test_a_contributed_kind_is_listed_with_its_module_and_mint(site):
    site.contribution("notes", TOPIC)
    kinds = api.kinds()
    topic = [k for k in kinds.found if k.name == "topic"]
    assert topic == [api.Kind(name="topic", doc="a notes topic", module="notes", mint="git")]
    assert kinds.broken == ()


def test_a_kinds_check_sees_the_row_and_its_refusal_is_exit_3(site, tmp_path):
    site.contribution("notes", TOPIC)
    with pytest.raises(Refused) as caught:
        contributions.plan_add("t1", kind="topic", root=str(tmp_path / "t1"))
    assert caught.value.exit_code == 3
    assert "notes" in caught.value.reason and "notes folder" in caught.value.reason
    good = str(tmp_path / "notes" / "t1")
    assert add("t1", kind="topic", root=good).kind == "topic"


def test_an_unknown_kind_is_refused_with_exit_3_when_every_entry_loaded(site):
    site.contribution("notes", TOPIC)
    with pytest.raises(Refused) as caught:
        contributions.plan_add("x", kind="nosuch", root="/srv/x")
    assert caught.value.exit_code == 3
    assert "moxi projects kinds" in caught.value.fix


def test_a_kind_two_modules_declare_blocks_that_kind_only(site, golden_copy):
    site.contribution("notes", TOPIC)
    site.contribution("other", TOPIC)
    site.contribution("svc", HOLDS)
    with pytest.raises(Broken) as caught:
        contributions.plan_add("t", kind="topic", root="/srv/notes/t")
    assert caught.value.exit_code == 1
    assert set(caught.value.modules) == {"notes", "other"}
    assert "uninstall all but one" in caught.value.fix
    with pytest.raises(Broken):
        contributions.plan_set("gamma", kind="topic")
    assert add("plain", root="/srv/plain").kind == "project"
    assert contributions.load().remove_refusals(api.get("gamma")) == []
    kinds = api.kinds()
    assert "topic" not in {k.name for k in kinds.found}
    assert {m for m, _r in kinds.broken} == {"notes", "other"}


def test_redeclaring_a_built_in_kind_blocks_it(site):
    site.contribution("mine", '''
INTERFACE = 1
KINDS = {"agent": ("my agents", lambda fields: None)}
''')
    with pytest.raises(Broken, match="built in") as caught:
        contributions.plan_add("a", kind="agent", root="/srv/a")
    assert caught.value.modules == ("mine",)
    assert "agent" not in {k.name for k in api.kinds().found}
    assert add("p", root="/srv/p").kind == "project"


def test_a_check_that_raises_is_exit_1_naming_the_module(site):
    site.contribution("notes", '''
INTERFACE = 1
def check(fields):
    raise RuntimeError("boom")
KINDS = {"topic": ("a topic", check)}
''')
    with pytest.raises(Broken) as caught:
        contributions.plan_add("t", kind="topic", root="/srv/t")
    assert caught.value.modules == ("notes",) and "boom" in caught.value.reason


def test_a_check_answering_out_of_shape_is_exit_1(site):
    site.contribution("notes", '''
INTERFACE = 1
KINDS = {"topic": ("a topic", lambda fields: 42)}
''')
    with pytest.raises(Broken):
        contributions.plan_add("t", kind="topic", root="/srv/t")


@pytest.mark.parametrize("value", ['("doc",)', '("doc", None)', '(1, lambda f: None)',
                                   '("doc", lambda f: None, "zip")',
                                   '("doc", lambda f: None, "git", "extra")'])
def test_a_malformed_kinds_value_blocks_that_kind_only(site, value):
    site.contribution("notes", f'''
INTERFACE = 2
KINDS = {{"topic": {value}, "good": ("fine", lambda f: None, "dir")}}
''')
    with pytest.raises(Broken) as caught:
        contributions.plan_add("t", kind="topic", root="/srv/t")
    assert caught.value.modules == ("notes",)
    kinds = api.kinds()
    assert api.Kind(name="good", doc="fine", module="notes", mint="dir") in kinds.found
    assert [m for m, _r in kinds.broken] == ["notes"]
    assert add("g", kind="good", root="/srv/g").kind == "good"


def test_a_three_element_value_under_interface_1_is_the_wrong_length(site):
    site.contribution("notes", '''
INTERFACE = 1
KINDS = {"topic": ("doc", lambda f: None, "dir")}
''')
    with pytest.raises(Broken, match="interface 1"):
        contributions.plan_add("t", kind="topic", root="/srv/t")


@pytest.mark.parametrize("mint", ["git", "dir", "refuse"])
def test_a_kind_carries_its_mint(site, mint):
    site.contribution("notes", f'''
INTERFACE = 2
KINDS = {{"topic": ("doc", lambda f: None, "{mint}")}}
''')
    planned = contributions.plan_add("t", kind="topic", root="/srv/t")
    assert planned.kind.kind.mint == mint


# --- entries that won't load -------------------------------------------------------------

BROKEN_ENTRIES = {
    "import": "import no_such_module_anywhere\nINTERFACE = 1\n",
    "interface": "INTERFACE = 7\n",
    "no interface": "KINDS = {}\n",
    "bool interface": "INTERFACE = True\n",
    "kinds not a mapping": "INTERFACE = 1\nKINDS = [('topic', 'doc')]\n",
    "refuse_remove not callable": "INTERFACE = 1\nrefuse_remove = 'no'\n",
    "prepare not callable": "INTERFACE = 2\nprepare = 3\n",
}


@pytest.mark.parametrize("source", BROKEN_ENTRIES.values(), ids=BROKEN_ENTRIES.keys())
def test_an_entry_that_wont_load_is_named_by_kinds(site, source):
    site.contribution("bad", source)
    kinds = api.kinds()
    assert {k.name for k in kinds.found} == {"project", "agent"}
    assert [m for m, _r in kinds.broken] == ["bad"]


def test_a_load_failure_reason_keeps_every_line_on_one(site):
    site.contribution("bad", "raise ImportError('first line\\n  second line')\n")
    [(module, reason)] = contributions.load().unloadable
    assert module == "bad" and "\n" not in reason
    assert reason.startswith("the entry bad won't load: ")
    assert reason.endswith("first line second line")


def test_a_load_failure_that_is_no_value_error_keeps_every_line_on_one(site, monkeypatch):
    site.contribution("bad", "INTERFACE = 1\n")

    def read(ext):
        raise RuntimeError("first line\n\tsecond line\n")

    monkeypatch.setattr(contributions, "_read", read)
    [(module, reason)] = contributions.load().unloadable
    assert module == "bad"
    assert reason == "the entry bad won't load: RuntimeError: first line second line"


def test_an_entry_that_wont_load_blocks_remove_but_not_a_set_of_a_known_kind(
        site, golden_copy):
    site.contribution("bad", BROKEN_ENTRIES["import"])
    with pytest.raises(Broken) as caught:
        contributions.load().remove_refusals(api.get("gamma"))
    assert caught.value.exit_code == 1 and caught.value.modules == ("bad",)
    assert "moxi uninstall bad" in caught.value.fix
    assert contributions.plan_set("gamma", kind="agent", lead="someone").name == "agent"
    assert contributions.plan_set("gamma", root="/srv/elsewhere") is not None


@pytest.mark.parametrize("source", BROKEN_ENTRIES.values(), ids=BROKEN_ENTRIES.keys())
def test_an_add_of_a_built_in_kind_skips_an_entry_that_wont_load_with_a_warning(
        site, store_path, source):
    site.contribution("bad", source)
    planned = contributions.plan_add("new", root="/srv/new")
    assert planned.row["root"] == "/srv/new" and planned.kind.name == "project"
    [warning] = planned.warnings
    assert warning.startswith("skipped bad, which won't load (bad: the entry bad won't load")
    assert "its defaults and checks were not applied" in warning
    assert "fix: repair bad or uninstall it (`moxi uninstall bad`)" in warning
    assert store.write(planned.insert).key == "new"


def test_an_add_of_a_loaded_modules_kind_skips_an_entry_that_wont_load(site, tmp_path):
    site.contribution("notes", TOPIC)
    site.contribution("bad", BROKEN_ENTRIES["import"])
    planned = contributions.plan_add("t", kind="topic", root=str(tmp_path / "notes" / "t"))
    assert planned.kind.module == "notes"
    assert len(planned.warnings) == 1 and "skipped bad" in planned.warnings[0]
    with pytest.raises(Refused, match="notes folder"):
        contributions.plan_add("u", kind="topic", root=str(tmp_path / "elsewhere"))


def test_the_warning_names_every_entry_skipped(site):
    site.contribution("bad", BROKEN_ENTRIES["import"])
    site.contribution("worse", BROKEN_ENTRIES["interface"])
    [warning] = contributions.plan_add("new", root="/srv/new").warnings
    assert warning.startswith("skipped bad and worse, which won't load (bad: ")
    assert "worse: the entry worse won't load" in warning
    assert "their defaults and checks were not applied" in warning
    assert "`moxi uninstall bad`" in warning and "`moxi uninstall worse`" in warning


def test_a_loaded_entrys_prepare_still_runs_past_a_skipped_one(site):
    entry = site.contribution("svc", prepare_source('{"root": "/srv/from-svc"}'))
    site.contribution("bad", BROKEN_ENTRIES["import"])
    planned = contributions.plan_add("k")
    assert planned.row["root"] == "/srv/from-svc" and planned.supplied
    assert sys.modules[entry].SEEN == [{"key": "k", "kind": "project"}]
    assert planned.warnings


def test_a_skipped_entrys_prepare_is_not_called_and_fills_nothing(site):
    entry = site.contribution("bad", prepare_source('{"root": "/srv/from-bad"}',
                                                    interface=7))
    planned = contributions.plan_add("typed", root="/srv/typed")
    assert planned.row["root"] == "/srv/typed" and planned.supplied == {}
    with pytest.raises(Usage) as caught:
        contributions.plan_add("k")
    assert "bad won't load, and may be the one that would" in caught.value.reason
    assert sys.modules[entry].SEEN == []


def test_no_warning_while_every_entry_loads(site):
    site.contribution("svc", HOLDS)
    assert contributions.plan_add("new", root="/srv/new").warnings == []


@pytest.mark.parametrize("root", ["/srv/new", None])
def test_an_add_of_an_unknown_kind_past_an_entry_that_wont_load_is_exit_1(site, root):
    site.contribution("bad", BROKEN_ENTRIES["import"])
    with pytest.raises(Broken) as caught:
        contributions.plan_add("new", kind="topic", root=root)
    assert caught.value.exit_code == 1 and caught.value.modules == ("bad",)
    assert "may be the kind's home" in caught.value.reason
    assert "moxi uninstall bad" in caught.value.fix


def test_a_blocked_kind_still_blocks_an_add_past_an_entry_that_wont_load(site):
    site.contribution("notes", TOPIC)
    site.contribution("other", TOPIC)
    site.contribution("bad", BROKEN_ENTRIES["import"])
    with pytest.raises(Broken) as caught:
        contributions.plan_add("t", kind="topic", root="/srv/t")
    assert set(caught.value.modules) == {"notes", "other"}
    assert contributions.plan_add("p", root="/srv/p").warnings


def test_a_set_of_an_unknown_kind_past_an_entry_that_wont_load_is_exit_1(site, golden_copy):
    site.contribution("bad", BROKEN_ENTRIES["interface"])
    with pytest.raises(Broken) as caught:
        contributions.plan_set("gamma", kind="topic")
    assert caught.value.exit_code == 1 and caught.value.modules == ("bad",)


def test_a_set_of_an_unknown_kind_is_exit_3_when_every_entry_loaded(site, golden_copy):
    with pytest.raises(Refused) as caught:
        contributions.plan_set("gamma", kind="topic")
    assert caught.value.exit_code == 3


def test_a_set_runs_the_kinds_check_on_the_row_as_it_would_stand(site, tmp_path):
    site.contribution("notes", TOPIC)
    add("t", kind="topic", root=str(tmp_path / "notes" / "t"))
    with pytest.raises(Refused, match="notes folder"):
        contributions.plan_set("t", root=str(tmp_path / "elsewhere"))
    assert contributions.plan_set("t", lead="someone").name == "topic"


def test_a_row_whose_kinds_module_is_gone_stays_a_label(site, tmp_path):
    entry = site.contribution("notes", TOPIC)
    add("t", kind="topic", root=str(tmp_path / "notes" / "t"))
    (site.root / entry.replace(".", "/")).with_suffix(".py").unlink()
    for info in site.root.glob("fake_notes-*.dist-info"):
        for f in info.iterdir():
            f.unlink()
        info.rmdir()
    assert api.get("t").kind == "topic"
    assert contributions.plan_set("t", lead="someone") is None


def topic_row(tmp_path) -> str:
    """A row of kind topic, written straight to the store, since an add of a blocked kind
    is refused; returns its key."""
    root = str(tmp_path / "notes" / "t")
    store.write(lambda conn: store.insert(conn, key="t", root=root, kind="topic"))
    return "t"


def test_a_set_on_a_row_whose_kind_is_blocked_is_exit_1(site, tmp_path):
    site.contribution("notes", TOPIC)
    site.contribution("other", TOPIC)
    key = topic_row(tmp_path)
    for change in ({"root": str(tmp_path / "elsewhere")}, {"lead": "someone"}):
        with pytest.raises(Broken) as caught:
            contributions.plan_set(key, **change)
        assert caught.value.exit_code == 1
        assert set(caught.value.modules) == {"notes", "other"}
        assert "uninstall all but one" in caught.value.fix
    assert contributions.plan_set(key, kind="project").name == "project"


def test_a_set_on_a_row_of_an_unknown_kind_while_an_entry_wont_load_is_exit_1(
        site, tmp_path):
    site.contribution("bad", BROKEN_ENTRIES["import"])
    key = topic_row(tmp_path)
    with pytest.raises(Broken) as caught:
        contributions.plan_set(key, root=str(tmp_path / "elsewhere"))
    assert caught.value.exit_code == 1 and caught.value.modules == ("bad",)
    assert "may be the kind's home" in caught.value.reason
    assert "moxi uninstall bad" in caught.value.fix
    assert contributions.plan_set(key, kind="project").name == "project"


def test_a_set_on_a_row_of_an_unknown_kind_while_every_entry_loads_runs_no_check(
        site, tmp_path):
    site.contribution("svc", HOLDS)
    key = topic_row(tmp_path)
    assert contributions.plan_set(key, root=str(tmp_path / "elsewhere")) is None


def test_an_entry_installed_after_the_first_call_is_seen_by_the_next(site):
    assert {k.name for k in api.kinds().found} == {"project", "agent"}
    site.contribution("notes", TOPIC)
    assert "topic" in {k.name for k in api.kinds().found}


def test_the_read_calls_never_read_the_group(site, golden_copy, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the group was read")

    monkeypatch.setattr(contributions, "load", refuse)
    api.list_projects()
    api.get("alpha")
    api.for_path(os.path.join(golden_copy, "alpha"))


# --- remove refusals ---------------------------------------------------------------------

def test_a_refusal_names_what_holds_the_project(site, tmp_path):
    site.contribution("svc", HOLDS)
    site.contribution("notes", '''
INTERFACE = 1
def refuse_remove(project):
    return f"{project.key}'s notes folder holds 3 notes; move them first"
''')
    add("held", root=str(tmp_path / "held"))
    said = contributions.load().remove_refusals(api.get("held"))
    assert said == [("notes", "held's notes folder holds 3 notes; move them first"),
                    ("svc", "services still run for held; stop them with "
                            "`moxi services stop held`")]


def test_no_refusal_lets_remove_go_ahead(site, tmp_path):
    site.contribution("svc", HOLDS)
    add("free", root=str(tmp_path / "free"))
    assert contributions.load().remove_refusals(api.get("free")) == []


@pytest.mark.parametrize("body", ['raise RuntimeError("down")', 'return 5', 'return "  "'])
def test_a_refusal_that_raises_or_answers_out_of_shape_is_exit_1(site, tmp_path, body):
    site.contribution("svc", f'''
INTERFACE = 1
def refuse_remove(project):
    {body}
''')
    add("p", root=str(tmp_path / "p"))
    with pytest.raises(Broken) as caught:
        contributions.load().remove_refusals(api.get("p"))
    assert caught.value.modules == ("svc",)


# --- interface 2: defaults ---------------------------------------------------------------

def test_a_default_fills_an_unset_field_and_not_a_passed_one(site, tmp_path):
    site.contribution("notes", prepare_source(
        f'{{"root": "{tmp_path}/default/" + fields["key"]}}'))
    planned = contributions.plan_add("auto")
    assert planned.row["root"] == str(tmp_path / "default" / "auto")
    assert planned.supplied == {"root": (str(tmp_path / "default" / "auto"), "notes")}
    typed = contributions.plan_add("typed", root=str(tmp_path / "mine"))
    assert typed.row["root"] == str(tmp_path / "mine") and typed.supplied == {}


def test_prepare_sees_only_the_users_fields(site):
    site.contribution("notes", prepare_source("None"))
    contributions.plan_add("k", root="/srv/k", lead="boss")
    assert sys.modules["fake_notes.projects_link"].SEEN == [
        {"key": "k", "kind": "project", "root": "/srv/k", "lead": "boss"}]


def test_a_kinds_check_sees_the_defaulted_row_and_names_the_supplier(site, tmp_path):
    site.contribution("notes", prepare_source(
        f'{{"root": "{tmp_path}/wrong/" + fields["key"]}}', kinds="{}"))
    site.contribution("topics", TOPIC)
    with pytest.raises(Refused) as caught:
        contributions.plan_add("t", kind="topic")
    assert "root supplied by notes" in caught.value.reason
    assert "pass them yourself" in caught.value.fix


def test_two_modules_defaulting_a_field_differently_is_exit_1_naming_both(site):
    site.contribution("one", prepare_source('{"root": "/srv/one"}'))
    site.contribution("two", prepare_source('{"root": "/srv/two"}'))
    with pytest.raises(Broken) as caught:
        contributions.plan_add("k")
    assert caught.value.exit_code == 1
    assert caught.value.modules == ("one", "two")
    assert "--root" in caught.value.fix
    assert contributions.plan_add("k", root="/srv/mine").row["root"] == "/srv/mine"


def test_two_modules_defaulting_a_field_alike_agree(site):
    site.contribution("one", prepare_source('{"root": "/srv/same"}'))
    site.contribution("two", prepare_source('{"root": "/srv/same/."}'))
    assert contributions.plan_add("k").row["root"] == "/srv/same"


def test_a_default_naming_another_field_is_exit_1(site):
    site.contribution("notes", prepare_source('{"lead": "boss"}'))
    with pytest.raises(Broken, match="only root and notes_root") as caught:
        contributions.plan_add("k", root="/srv/k")
    assert caught.value.modules == ("notes",)


def test_a_default_naming_another_field_refuses_even_when_typed(site):
    site.contribution("notes", prepare_source('{"kind": "topic"}'))
    with pytest.raises(Broken):
        contributions.plan_add("k", root="/srv/k")


@pytest.mark.parametrize("value", ["5", "b'/srv/x'", '["/srv/x"]'])
def test_a_default_that_is_not_a_string_is_exit_1(site, value):
    site.contribution("notes", prepare_source(f'{{"root": {value}}}'))
    with pytest.raises(Broken, match="not a string"):
        contributions.plan_add("k")


def test_a_none_default_is_no_default(site):
    site.contribution("notes", prepare_source('{"root": None}'))
    with pytest.raises(Usage):
        contributions.plan_add("k")


def test_a_relative_default_root_is_exit_1_naming_the_module_and_fix(site):
    site.contribution("notes", prepare_source('{"root": "relative/k"}'))
    with pytest.raises(Broken, match="not an absolute path") as caught:
        contributions.plan_add("k")
    assert caught.value.modules == ("notes",)
    assert "moxi uninstall notes" in caught.value.fix and "--root" in caught.value.fix


@pytest.mark.parametrize("answer", ['"/srv/x"', "['/srv/x']", "5"])
def test_prepare_answering_other_than_a_dict_or_none_is_exit_1(site, answer):
    site.contribution("notes", prepare_source(answer))
    with pytest.raises(Broken, match="not a dict"):
        contributions.plan_add("k", root="/srv/k")


def test_a_prepare_that_raises_is_exit_1_naming_the_module(site):
    site.contribution("notes", '''
INTERFACE = 2
def prepare(fields):
    raise KeyError("root")
''')
    with pytest.raises(Broken, match="prepare raised") as caught:
        contributions.plan_add("k", root="/srv/k")
    assert caught.value.modules == ("notes",)


def test_differing_defaults_for_a_passed_field_raise_nothing(site):
    site.contribution("one", prepare_source('{"root": "/srv/one"}'))
    site.contribution("two", prepare_source('{"root": "relative"}'))
    planned = contributions.plan_add("k", root="/srv/typed")
    assert planned.row["root"] == "/srv/typed" and planned.supplied == {}


def test_a_notes_root_default_is_dropped_while_notes_is_absent(site):
    site.contribution("notes", prepare_source('{"notes_root": "/srv/notes/k"}'))
    planned = contributions.plan_add("k", root="/srv/k")
    assert planned.row["notes_root"] is None and planned.supplied == {}


def test_a_notes_root_default_is_kept_while_notes_is_installed(site):
    site.module("notes")
    site.contribution("notes", prepare_source('{"notes_root": "/srv/notes/k"}'),
                      package="fake_notes_link")
    planned = contributions.plan_add("k", root="/srv/k")
    assert planned.row["notes_root"] == "/srv/notes/k"
    assert planned.supplied == {"notes_root": ("/srv/notes/k", "notes")}
    assert store.write(planned.insert).notes_root == "/srv/notes/k"


@pytest.mark.parametrize("value", ['"relative/notes"', "5"])
def test_a_notes_root_default_is_dropped_unchecked_while_notes_is_absent(site, value):
    site.contribution("notes", prepare_source(f'{{"notes_root": {value}}}'))
    planned = contributions.plan_add("k", root="/srv/k")
    assert planned.row["notes_root"] is None and planned.supplied == {}


def test_a_relative_notes_root_default_is_exit_1_while_notes_is_installed(site):
    site.module("notes")
    site.contribution("notes", prepare_source('{"notes_root": "relative/notes"}'),
                      package="fake_notes_link")
    with pytest.raises(Broken, match="not an absolute path") as caught:
        contributions.plan_add("k", root="/srv/k")
    assert caught.value.exit_code == 1 and caught.value.modules == ("notes",)


def test_a_typed_notes_root_is_refused_while_notes_is_absent(site):
    with pytest.raises(Refused, match="notes") as caught:
        contributions.plan_add("k", root="/srv/k", notes_root="/srv/n")
    assert caught.value.exit_code == 3
    assert "moxi install notes" in caught.value.fix
    with pytest.raises(Refused, match="notes"):
        contributions.plan_set("k", notes_root="/srv/n")


def test_clearing_a_notes_root_needs_no_notes(site, golden_copy):
    assert contributions.plan_set("gamma", notes_root=None) is not None


def test_add_without_a_root_and_no_default_is_usage(site):
    with pytest.raises(Usage) as caught:
        contributions.plan_add("k")
    assert caught.value.exit_code == 2
    assert "--root" in caught.value.fix


def test_a_defaulted_root_that_is_taken_names_its_supplier(site, golden_copy):
    taken = os.path.join(golden_copy, "alpha")
    site.contribution("notes", prepare_source(f'{{"root": "{taken}"}}'))
    planned = contributions.plan_add("k")
    with pytest.raises(Refused) as caught:
        store.write(planned.insert)
    assert "already project 'alpha'" in caught.value.reason
    assert "root supplied by notes" in caught.value.reason


def test_an_interface_1_entry_beside_an_interface_2_entry(site, tmp_path):
    site.contribution("old", '''
INTERFACE = 1
KINDS = {"legacy": ("old kind", lambda f: None)}
def prepare(fields):
    raise AssertionError("an interface 1 prepare is never read")
''')
    site.contribution("new", prepare_source(f'{{"root": "{tmp_path}/" + fields["key"]}}',
                                            kinds='{"fresh": ("new kind", lambda f: None, '
                                                  '"dir")}'))
    planned = contributions.plan_add("k", kind="legacy")
    assert planned.row["root"] == str(tmp_path / "k")
    assert planned.kind.kind == api.Kind(name="legacy", doc="old kind", module="old",
                                         mint="git")
    assert contributions.plan_add("j", kind="fresh").kind.kind.mint == "dir"


def test_set_takes_no_defaults(site, golden_copy):
    site.contribution("notes", prepare_source('{"root": "/srv/elsewhere"}'))
    contributions.plan_set("gamma", lead="someone")
    assert sys.modules["fake_notes.projects_link"].SEEN == []


def test_set_of_an_unknown_key_is_not_found(site, golden_copy):
    with pytest.raises(api.NotFound):
        contributions.plan_set("nope", lead="x")


def test_set_of_an_unknown_field_is_usage(site, golden_copy):
    with pytest.raises(Usage):
        contributions.plan_set("gamma", tier="full")


def test_the_add_rules_apply_before_anything_is_written(site, store_path):
    for bad in (dict(key="con", root="/srv/x"), dict(key="ok", root="relative"),
                dict(key="ok", root="/srv/x", lead="two words"),
                dict(key="ok", root="/srv/x", kind="bad kind")):
        with pytest.raises(Refused):
            contributions.plan_add(**bad)
    assert not store_path.exists()
