"""Guard values, their declarations, the loader and its file forms, and the switches
and install questions that turn turns' own guards on."""

from __future__ import annotations

import os
import subprocess
import sys
import types

import pytest

import moxi_turns as turns
from conftest import write_config
from moxi import config as moxi_config
from moxi.units import switch_problem
from moxi_turns import config, guards, module
from moxi_turns.guards import Allow, Deny, Guard, Prompt, ToolCall

#: The removal guard's pre-filter, as the design gives it.
REMOVAL_MATCH = r"""\b(r[\\'"{}$]*m([\\'"{}$]*d[\\'"{}$]*i[\\'"{}$]*r)?)(?![A-Za-z_])"""


class Entry:
    def __init__(self, name, value):
        self.name = name
        self._value = value

    def load(self):
        if isinstance(self._value, Exception):
            raise self._value
        if isinstance(self._value, types.SimpleNamespace):
            return self._value
        return types.SimpleNamespace(GUARDS=self._value)


def data_only(name="stop", **kw):
    kw.setdefault("calls", {"claude": ("Bash",)})
    kw.setdefault("deny", "no")
    return Guard(name, **kw)


def no_switches(module, key):
    return False, None


def load(on=(), *entries, switches=no_switches):
    return guards.load(list(on), entries=list(entries), switches=switches)


# -- the values --------------------------------------------------------------------------

def test_the_values_are_plain_data_and_reach_the_package():
    call = ToolCall("claude", "s-1", None, "Bash", "tu-1", {"command": "ls"}, "/w",
                    "/r.jsonl", {"HOME": "/h"}, (Prompt("composer", "a question"),))
    assert call.agent_id is None and call.prompts[0].origin == "composer"
    assert "HOME" not in repr(call)
    short = ToolCall("codex", None, "a-1", "spawn_agent")
    assert (short.input, short.env, short.prompts, short.record_path) == ({}, {}, (), None)
    assert Allow().line is None and Allow("note").line == "note"
    assert Deny("why").reason == "why"
    for name in ("Guard", "ToolCall", "Prompt", "Allow", "Deny"):
        assert getattr(turns, name) is getattr(guards, name)
        assert name in turns.__all__


def test_the_defaults_are_the_designs():
    g = Guard("x", {"claude": ("*",)}, judge="pkg.mod:fn")
    assert (g.who, g.match, g.match_flags, g.deny, g.wants, g.on_error, g.deadline_ms,
            g.switch, g.doc) == ("any", None, "", "", (), "allow", 500, None, "")
    assert g.problems() == []
    assert g.deadline() == 500 and g.deadline(200) == 200
    assert Guard("x", {}, judge="a:b", deadline_ms=9000).deadline() == guards.MAX_WAIT_MS
    assert g.patterns("claude") == ("*",) and g.patterns("codex") == ()


def test_the_guard_module_imports_only_the_standard_library():
    code = ("import sys; import moxi_turns.guards; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in "
            "('moxi', 'moxi_turns')))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                         text=True, check=True).stdout
    assert out.strip() == "['moxi_turns', 'moxi_turns.guards']"


# -- declarations ------------------------------------------------------------------------

@pytest.mark.parametrize("kw, why", [
    ({"name": "Bad_Name"}, "name 'Bad_Name'"),
    ({"name": ""}, "name ''"),
    ({"calls": ("Bash",)}, "calls must be a table"),
    ({"calls": {"claude": "Bash"}}, "calls.claude must be a tuple"),
    ({"calls": {"cursor": ("Bash",)}}, "harness 'cursor'"),
    ({"calls": {"claude": ("",)}}, "a pattern is a tool name"),
    ({"calls": {"claude": ("Ba sh",)}}, "a pattern is a tool name"),
    ({"who": "subagents"}, "who 'subagents'"),
    ({"match": {}}, "match must be a table"),
    ({"match": {"command": "(?<=x)rm"}}, "match.command"),
    ({"match": {"": "rm"}}, "names the field ''"),
    ({"match_flags": "im"}, "match_flags"),
    ({"deny": ""}, "needs a deny text"),
    ({"deny": "  "}, "needs a deny text"),
    ({"judge": "nocolon", "deny": ""}, "judge 'nocolon'"),
    ({"judge": ":own", "deny": ""}, "judge ':own'"),
    ({"wants": ("transcript",)}, "wants"),
    ({"wants": "prompts"}, "wants"),
    ({"on_error": "ask"}, "on_error"),
    ({"deadline_ms": 0}, "deadline_ms"),
    ({"deadline_ms": True}, "deadline_ms"),
    ({"deadline_ms": 1.5}, "deadline_ms"),
    ({"switch": "removal"}, "switch 'removal'"),
    ({"switch": 1}, "switch 1"),
    ({"doc": "two\nlines"}, "one line"),
])
def test_every_field_is_checked(kw, why):
    kw = {"name": "g", "calls": {"claude": ("Bash",)}, "deny": "no", **kw}
    problems = Guard(**kw).problems()
    assert any(why in p for p in problems), problems


def test_a_sound_guard_of_every_kind_has_no_problems():
    sound = [
        data_only(),
        Guard("ceiling", {"claude": ("*",), "codex": ("*",)}, who="agents",
              judge="moxi_usage.guards:ceiling"),
        Guard("removal", {"claude": ("Bash",)}, match={"command": REMOVAL_MATCH},
              match_flags="i", judge="moxi_turns.guards.removal:judge",
              switch="guards.removal", doc="refuse shell removals"),
        Guard("calendar", {"claude": ("mcp__*Google_Calendar__*",)}, deny="not here"),
        Guard("nested", {"claude": ("Agent", "Task"), "codex": ("spawn_agent",)},
              who="agents", deny="no", switch="guards.no_nested_agents"),
        Guard("ask", {"claude": ("AskUserQuestion",)}, wants=("prompts",),
              judge="pkg.q:judge", on_error="deny", switch="rows.ask"),
    ]
    for g in sound:
        assert g.problems() == [], g


@pytest.mark.parametrize("alias, canonical", [
    ("Agent", "spawn_agent"), ("Write", "apply_patch"), ("Edit", "apply_patch")])
def test_a_codex_alias_is_refused_naming_the_canonical_name(alias, canonical):
    problems = Guard("g", {"codex": (alias,)}, deny="no").problems()
    assert len(problems) == 1
    assert f"{alias!r}" in problems[0] and f"name {canonical!r}" in problems[0]
    # The same names are Claude's own tools, and a canonical Codex name is fine.
    assert Guard("g", {"claude": (alias,), "codex": (canonical,)}, deny="no").problems() == []


def test_the_alias_list_is_codexs_and_names_its_version():
    assert guards.CODEX_ALIASES == {"Agent": "spawn_agent", "Write": "apply_patch",
                                    "Edit": "apply_patch"}
    assert guards.CODEX_ALIASES_VERSION == "0.153.0"


def test_a_codex_alias_on_the_list_fails_and_does_not_run():
    loaded = load(["mod.g"], Entry("mod", [Guard("g", {"codex": ("Agent",)}, deny="no")]))
    assert loaded.on == ()
    [p] = loaded.problems
    assert (p.id, p.kind, p.on) == ("mod.g", "shape", True) and "spawn_agent" in p.reason


@pytest.mark.parametrize("rx", [
    REMOVAL_MATCH, r"^git\s+push\b", r"(?:a|b)+", r"x(?=y)", r"x(?!y)", r"[\]a]",
    r"a{2,3}", r"\++", r"a*?b", r"[+*?]+", r"mcp__.*__send$"])
def test_a_regex_both_dialects_read_is_accepted(rx):
    assert guards.regex_problem(rx) is None


@pytest.mark.parametrize("rx, why", [
    ("(", "does not compile"),
    ("", "is not a regex"),
    (None, "is not a regex"),
    ("(?i)rm", "inline flags"),
    ("(?s:a.b)", "inline flags"),
    ("(?<=x)rm", "lookbehind"),
    ("(?<!x)rm", "lookbehind"),
    ("(?P<word>rm)", "named group"),
    ("(?>rm)", "atomic group"),
    ("a++", "possessive"),
    ("a{2}+", "possessive"),
    (r"\Arm", "JavaScript lacks"),
    (r"rm\Z", "JavaScript lacks"),
    ("[]a]", "empty class"),
    ("(?#note)rm", "comment"),
])
def test_a_regex_outside_the_shared_subset_is_refused(rx, why):
    problem = guards.regex_problem(rx)
    assert problem is not None and why in problem


def test_match_compiles_ascii_and_case_blind_under_i():
    g = Guard("removal", {"claude": ("Bash",)}, match={"command": REMOVAL_MATCH},
              match_flags="i", judge="a:b")
    rx = guards.compile_match(g)["command"]
    for text in ("rm -rf /x", "R''M x", r"r\m -rf /x", "r{}m x", "r$'m' x", "rmdir d"):
        assert rx.search(text), text
    assert not rx.search("rmx") and not rx.search("form")
    assert guards.compile_match(Guard("x", {}, deny="no")) == {}
    assert guards.compile_match(Guard("x", {}, match={"a": "A"}, deny="n"))["a"].search(
        "a") is None


# -- module guards ---------------------------------------------------------------------

def test_module_guards_take_their_module_in_the_id_and_run_only_when_listed():
    loaded = load(["mod.one"], Entry("mod", [data_only("one"), data_only("two")]))
    assert [(o.id, o.by) for o in loaded.on] == [("mod.one", ("list",))]
    assert [f.id for f in loaded.off] == ["mod.two"]
    assert loaded.problems == ()
    assert loaded.on[0].found.module == "mod" and loaded.on[0].found.source == "module"
    assert loaded.get("mod.one") is loaded.on[0] and loaded.get("mod.two") is None
    assert set(loaded.import_ms) == {"mod"}


def test_a_switch_that_reads_true_turns_a_guard_on():
    seen = []

    def switches(module, key):
        seen.append((module, key))
        return key == "rows.on", None

    loaded = load(["mod.both"], Entry("mod", [data_only("sw", switch="rows.on"),
                                              data_only("off", switch="rows.off"),
                                              data_only("both", switch="rows.on")]),
                  switches=switches)
    assert {o.id: o.by for o in loaded.on} == {"mod.sw": ("switch",),
                                               "mod.both": ("list", "switch")}
    assert [f.id for f in loaded.off] == ["mod.off"]
    assert sorted(seen) == [("mod", "rows.off"), ("mod", "rows.on"), ("mod", "rows.on")]


def test_a_switch_that_cannot_be_read_reads_off_and_is_a_problem_listed_or_not():
    def switches(module, key):
        return True, "switch rows.x must be a bool setting read at use"

    loaded = load(["mod.listed"], Entry("mod", [data_only("listed", switch="rows.x"),
                                                data_only("unlisted", switch="rows.x")]),
                  switches=switches)
    # Listed, it still runs; unlisted, it stays off. Both are problems.
    assert [(o.id, o.by) for o in loaded.on] == [("mod.listed", ("list",))]
    assert [f.id for f in loaded.off] == ["mod.unlisted"]
    assert [(p.id, p.kind, p.on) for p in loaded.problems] == [
        ("mod.listed", "switch", True), ("mod.unlisted", "switch", False)]

    def raising(module, key):
        raise RuntimeError("config gone")

    loaded = load([], Entry("mod", [data_only("g", switch="rows.x")]), switches=raising)
    assert loaded.on == () and loaded.problems[0].kind == "switch"
    assert "config gone" in loaded.problems[0].reason


def test_a_list_entry_for_a_module_not_installed_is_missing():
    loaded = load(["usage.ceiling", "mod.typo"], Entry("mod", [data_only("g")]))
    assert {(p.id, p.kind) for p in loaded.problems} == {("usage.ceiling", "missing"),
                                                        ("mod.typo", "load")}
    assert all(p.on for p in loaded.problems)
    assert "declares no such guard" in [p for p in loaded.problems
                                        if p.id == "mod.typo"][0].reason


def test_a_module_whose_guards_do_not_load_fails_its_listed_ids_only():
    loaded = load(["bad.g", "mod.g"], Entry("bad", ImportError("no such module")),
                  Entry("mod", [data_only("g")]))
    assert [o.id for o in loaded.on] == ["mod.g"]
    assert [(p.id, p.kind, p.on) for p in loaded.problems] == [
        ("bad", "load", False), ("bad.g", "load", True)]
    assert "no such module" in loaded.problems[0].reason


@pytest.mark.parametrize("value, why", [
    (types.SimpleNamespace(), "no GUARDS tuple"),
    (types.SimpleNamespace(GUARDS="x"), "no GUARDS tuple")])
def test_a_module_without_guards_is_a_problem(value, why):
    loaded = load([], Entry("mod", value))
    assert [(p.id, p.kind) for p in loaded.problems] == [("mod", "load")]
    assert why in loaded.problems[0].reason


def test_two_distributions_or_a_bad_name_cost_the_module_its_guards():
    loaded = load([], Entry("mod", [data_only()]), Entry("mod", [data_only()]),
                  Entry("Bad-Name", [data_only()]))
    assert loaded.on == () and loaded.off == ()
    reasons = {p.id: p.reason for p in loaded.problems}
    assert "uninstall all but one" in reasons["mod"]
    assert "not a Moxi module name" in reasons["Bad-Name"]


def test_a_name_declared_twice_and_a_row_that_is_no_guard_are_problems():
    loaded = load(["mod.g"], Entry("mod", [data_only("g"), data_only("g"), "not a guard",
                                           data_only("h")]))
    assert loaded.on == () and [f.id for f in loaded.off] == ["mod.h"]
    kinds = [(p.id, p.kind) for p in loaded.problems]
    assert ("mod", "load") in kinds and kinds.count(("mod.g", "shape")) == 2
    assert any("declared twice" in p.reason for p in loaded.problems)


def test_the_ceiling_surface_loads_as_usage_declares_it():
    ceiling = Guard("ceiling", {"claude": ("*",), "codex": ("*",)}, who="agents",
                    judge="listener_targets:silent")
    loaded = load(["usage.ceiling"], Entry("usage", [ceiling]))
    [on] = loaded.on
    assert on.id == "usage.ceiling" and loaded.problems == ()
    assert guards.judge_of(on.found)(ToolCall("claude", "s", "a-1", "Bash")) is None


# -- custom files -----------------------------------------------------------------------

TOML = '''
[[guard]]
name = "no-push"
calls = { claude = ["Bash"], codex = ["Bash"] }
match = { command = "\\\\bgit\\\\s+push\\\\b" }
deny = "Pushing is the person's call; ask them."
doc = "refuse git push"

[[guard]]
name = "no-calendar"
calls = { claude = ["mcp__*Google_Calendar__*"] }
who = "agents"
deny = "not from a subagent"
deadline_ms = 100
'''


def test_a_toml_file_declares_data_only_guards_all_on(tmp_path):
    path = tmp_path / "mine.toml"
    path.write_text(TOML)
    loaded = load([str(path)])
    assert [(o.id, o.by) for o in loaded.on] == [("file:mine.no-push", ("list",)),
                                                 ("file:mine.no-calendar", ("list",))]
    push = loaded.on[0].guard
    assert push.calls == {"claude": ("Bash",), "codex": ("Bash",)}
    assert guards.compile_match(push)["command"].search("git push origin")
    assert loaded.on[1].guard.who == "agents" and loaded.on[1].guard.deadline_ms == 100
    assert loaded.on[0].found.module is None and loaded.on[0].found.path == str(path)


@pytest.mark.parametrize("text, why", [
    ('[[guard]]\nname = "g"\ncalls = { claude = ["Bash"] }\njudge = "a.b:c"\n',
     "data-only and names no judge"),
    ('[[guard]]\nname = "g"\ncalls = { claude = ["Bash"] }\ndeny = "n"\n'
     'switch = "guards.x"\n', "can name no switch"),
    ('[[guard]]\nname = "g"\ncalls = { codex = ["Edit"] }\ndeny = "n"\n', "apply_patch"),
])
def test_a_toml_guard_that_names_a_judge_a_switch_or_an_alias_is_refused(tmp_path, text,
                                                                          why):
    path = tmp_path / "mine.toml"
    path.write_text(text)
    loaded = load([str(path)])
    assert loaded.on == ()
    [p] = loaded.problems
    assert (p.id, p.kind, p.on) == ("file:mine.g", "shape", True) and why in p.reason


@pytest.mark.parametrize("text, why", [
    ("not toml = = =", "does not load"),
    ('[other]\nx = 1\n', "only [[guard]] tables"),
    ('[[guard]]\nname = "g"\n', "has no calls"),
    ('[[guard]]\nname = "g"\ncalls = {}\nwhom = "any"\n', "unknown field(s) whom"),
])
def test_a_toml_file_that_does_not_read_fails_as_a_whole(tmp_path, text, why):
    path = tmp_path / "mine.toml"
    path.write_text(text)
    loaded = load([str(path)])
    assert loaded.on == ()
    [p] = loaded.problems
    assert (p.id, p.kind, p.on) == (str(path), "load", True) and why in p.reason


PY = '''
from moxi_turns.guards import Allow, Deny, Guard


def judge(call):
    if "secret" in call.input.get("file_path", ""):
        return Deny("not that file")
    return Allow("checked")


GUARDS = (
    Guard("secrets", {"claude": ("Read",)}, judge=":judge"),
    Guard("listed", {"claude": ("Bash",)}, judge="listener_targets:silent"),
)
'''


def test_a_py_file_holds_guards_whose_judge_may_be_its_own(tmp_path):
    path = tmp_path / "local_guards.py"
    path.write_text(PY)
    loaded = load([str(path)])
    assert [o.id for o in loaded.on] == ["file:local_guards.secrets",
                                         "file:local_guards.listed"]
    judge = guards.judge_of(loaded.on[0].found)
    assert judge(ToolCall("claude", "s", None, "Read", input={"file_path": "/a/secret"})
                 ) == Deny("not that file")
    assert judge(ToolCall("claude", "s", None, "Read", input={"file_path": "/a"})
                 ) == Allow("checked")
    assert guards.judge_of(loaded.on[1].found)(None) is None


def test_a_py_file_naming_a_switch_is_refused(tmp_path):
    path = tmp_path / "g.py"
    path.write_text("from moxi_turns.guards import Guard\n"
                    "GUARDS = (Guard('s', {'claude': ('Bash',)}, deny='n', "
                    "switch='guards.removal'),)\n")
    loaded = load([str(path)])
    assert loaded.on == ()
    assert [(p.id, p.kind) for p in loaded.problems] == [("file:g.s", "shape")]
    assert "no module" in loaded.problems[0].reason


def test_an_own_judge_needs_its_own_file():
    assert any("judge ':fn'" in p for p in Guard("g", {}, judge=":fn").problems())
    found = guards.Found("mod.g", Guard("g", {}, judge="a:b"), "module", "mod")
    with pytest.raises(ImportError):
        guards.judge_of(found)
    with pytest.raises(LookupError):
        guards.judge_of(guards.Found("mod.d", data_only(), "module", "mod"))


@pytest.mark.parametrize("entry, why", [
    ("rel/guards.toml", "not an absolute path"),
    ("/x/guards.yaml", "a .toml or a .py file"),
])
def test_a_file_entry_of_the_wrong_form_is_a_problem(entry, why):
    loaded = load([entry])
    assert [(p.id, p.kind, p.on) for p in loaded.problems] == [(entry, "load", True)]
    assert why in loaded.problems[0].reason


def test_a_missing_or_raising_py_file_fails_as_a_whole(tmp_path):
    raising = tmp_path / "boom.py"
    raising.write_text("raise RuntimeError('boom')\n")
    loaded = load([str(tmp_path / "gone.toml"), str(raising)])
    assert [p.id for p in loaded.problems] == [str(tmp_path / "gone.toml"), str(raising)]
    assert "boom" in loaded.problems[1].reason


def test_a_tilde_entry_is_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "g.toml").write_text('[[guard]]\nname = "g"\ncalls = {}\ndeny = "n"\n')
    assert [o.id for o in load(["~/g.toml"]).on] == ["file:g.g"]
    assert guards.is_file_entry("~/g.toml") and not guards.is_file_entry("turns.removal")


# -- switches and turns' config ------------------------------------------------------------

def test_turns_settings_declare_the_keys_with_their_defaults():
    keys = config.schema().keys
    expect = {"guards.on": ("list", [], "use"),
              "guards.removal": ("bool", False, "use"),
              "guards.no_nested_agents": ("bool", False, "use"),
              "guards.question_mode": ("bool", False, "use"),
              "guards.max_wait_ms": ("int", 3000, "use"),
              "after_tool.wait_ms": ("int", 300, "use"),
              "question_mode.origins": ("list", ["composer"], "use")}
    for name, (kind, default, read) in expect.items():
        key = keys[name]
        assert (key.type, key.default, key.read, key.env) == (kind, default, read, None)
    assert guards.MAX_WAIT_MS == keys["guards.max_wait_ms"].default


def test_questions_name_exactly_the_three_switches_each_a_switch_and_a_noun_phrase():
    assert set(module.QUESTIONS) == {"guards.removal", "guards.no_nested_agents",
                                     "guards.question_mode"}
    schema = config.schema()
    for key, text in module.QUESTIONS.items():
        assert switch_problem(schema, key) is None
        assert text == text.strip() and "\n" not in text
        assert text[0].islower() and not text.endswith((".", "?", "!"))
        # Both of the installer's forms read as sentences.
        assert f"turns: {text}? [y/N] ".count("?") == 1
        assert f"turns: {text} is off; turn it on with: moxi config set turns {key} true"


def test_the_announcement_passes_the_core_check():
    from moxi import modules

    found = modules.load("turns")
    assert found.state == modules.PRESENT, found
    assert dict(found.questions) == module.QUESTIONS


def test_a_shipped_switch_turns_its_guard_on_and_nothing_else_does(turns_env):
    entry = Entry("turns", [data_only("removal", switch="guards.removal")])
    env = dict(os.environ)
    assert guards.load([], entries=[entry], env=env).on == ()
    write_config(turns_env, "[guards]\nremoval = true\n")
    loaded = guards.load(None, entries=[entry], env=env)
    assert [(o.id, o.by) for o in loaded.on] == [("turns.removal", ("switch",))]
    write_config(turns_env, '[guards]\nremoval = false\non = ["turns.removal"]\n')
    loaded = guards.load(None, entries=[entry], env=env)
    assert [(o.id, o.by) for o in loaded.on] == [("turns.removal", ("list",))]
    assert loaded.problems == ()


def test_an_unreadable_turns_config_turns_no_guard_on_by_the_list(turns_env):
    write_config(turns_env, "guards = = =\n")
    loaded = guards.load(None, entries=[Entry("mod", [data_only("g")])],
                         switches=no_switches, env=dict(os.environ))
    assert loaded.on == () and [f.id for f in loaded.off] == ["mod.g"]


OTHER = """
[good]
type = "bool"
default = false
doc = "a switch"
read = "use"

[defaults_on]
type = "bool"
default = true
doc = "on unless set"
read = "use"

[by_env]
type = "bool"
default = false
doc = "set from the environment"
read = "use"
env = "MOXI_FAKEMOD_ON"

[rows]
type = "table"
items = "bool"
default = {}
doc = "a switch table"
read = "use"
"""


@pytest.fixture
def fakemod(tmp_path):
    cfg = tmp_path / "cfg"
    (cfg / "fakemod").mkdir(parents=True)
    env = {"MOXI_FAKEMOD_CONFIG_HOME": str(cfg), "HOME": str(tmp_path)}
    return moxi_config.Schema.parse(OTHER), env, cfg / "fakemod" / "config.toml"


def test_another_modules_switch_is_read_through_its_config(fakemod):
    schema, env, path = fakemod
    assert guards.read_switch("fakemod", "good", env=env, schema=schema) == (False, None)
    path.write_text("good = true\n[rows]\nceiling = true\n")
    assert guards.read_switch("fakemod", "good", env=env, schema=schema) == (True, None)
    # A switch table's entry is a switch too.
    assert guards.read_switch("fakemod", "rows.ceiling", env=env, schema=schema) == (
        True, None)
    assert guards.read_switch("fakemod", "rows.other", env=env, schema=schema) == (
        False, None)


@pytest.mark.parametrize("key, why", [
    ("defaults_on", "default false"), ("by_env", "no env"),
    ("missing.key", "not a setting")])
def test_a_switch_that_fails_the_core_check_reads_off(fakemod, key, why):
    schema, env, path = fakemod
    value, problem = guards.read_switch("fakemod", key, env=env, schema=schema)
    assert value is False and why in problem


def test_a_module_whose_config_does_not_load_reads_off(fakemod):
    schema, env, path = fakemod
    path.write_text("good = = true\n")
    value, problem = guards.read_switch("fakemod", "good", env=env, schema=schema)
    assert value is False and "does not load" in problem
    value, problem = guards.read_switch("notamodule", "good", env=env)
    assert value is False and "do not read" in problem
