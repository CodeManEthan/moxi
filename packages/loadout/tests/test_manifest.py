"""Reading the manifest: sections, kinds, names, ids, unknown keys and {source}."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from moxi_loadout import manifest
from moxi_loadout.manifest import Id, ManifestError

EXAMPLE = """\
[global]
mode = "off"                            # off, managed or if-absent
[user]                                  # every session of this user
skills = ["review", "launcher/moxi-handoff"]
hooks = ["guard-rm"]
permissions = ["git-reads"]
mcp = ["docs"]
disabled = ["mcp:docs@codex"]
[project."~/code/app"]
skills = ["app-deploy"]
agents = ["app-lead"]
[project.web]
skills = ["review"]
[skill.review]
harnesses = ["claude"]
[skill."launcher/moxi-handoff"]
harnesses = ["claude", "codex"]
[agent.app-lead]
codex = { model = "o4" }
[hook.guard-rm]
event = "PreToolUse"
matcher = "Bash"
command = "python3 {source}/hooks/guard_rm.py"
timeout = 10
harnesses = ["claude", "codex"]
[permissions.git-reads]
allow = [{ shell = ["git", "status"] }]
claude = { allow = ["WebFetch(domain:docs.python.org)"] }
[mcp.docs]
command = "uvx"
args = ["docs-mcp", "--root", "{source}/docs"]
"""


def read(text: str, folder: Path) -> manifest.Manifest:
    return manifest.parse(textwrap.dedent(text), path=folder / "loadout.toml")


def problems(text: str, folder: Path) -> tuple[str, ...]:
    with pytest.raises(ManifestError) as caught:
        read(text, folder)
    assert caught.value.exit_code == 2
    return caught.value.lines


def test_the_example_reads_with_every_section_and_kind(folder):
    m = read(EXAMPLE, folder)
    assert m.exists and m.mode == "off" and m.global_file == folder / "global.md"
    assert m.user.names("skill") == ("review", "launcher/moxi-handoff")
    assert m.user.names("hook") == ("guard-rm",)
    assert m.user.names("permissions") == ("git-reads",)
    assert m.user.names("mcp") == ("docs",)
    assert m.user.disabled == (Id("mcp", "docs", "codex"),)
    app, web = m.projects
    assert (app.where, app.root) == ("path", os.path.join(os.path.expanduser("~"), "code",
                                                          "app"))
    assert app.names("agent") == ("app-lead",)
    assert (web.where, web.key, web.root) == ("key", "web", None)
    assert m.harnesses("skill", "review") == ("claude",)
    assert m.harnesses("agent", "app-lead") is None
    assert m.item("hook", "guard-rm")["timeout"] == 10
    assert [s.label() for s in m.listing("skill", "review")] == ["user", "project web"]
    assert m.refused == ()


def test_a_missing_manifest_is_empty_and_sets_no_mode(folder):
    m = manifest.load(folder)
    assert not m.exists and m.mode is None and m.global_mode == "off"
    assert m.sections() == (m.user,) and m.user.lists == {}


def test_no_global_mode_key_reads_none_so_the_question_is_asked(folder):
    assert read("[global]\nfile = 'text.md'\n", folder).mode is None
    assert read("[global]\nfile = 'text.md'\n", folder).global_file == folder / "text.md"
    assert read("[global]\nmode = 'managed'\n", folder).mode == "managed"


@pytest.mark.parametrize("text, line", [
    ("[skil.x]\n", "unknown key skil (did you mean skill?)"),
    ("[user]\nskils = ['x']\n", "unknown key user.skils (did you mean skills?)"),
    ("[project.app]\nagent = ['x']\n", "unknown key project.app.agent (did you mean agents?)"),
    ("[skill.x]\nharness = ['claude']\n",
     "unknown key skill.x.harness (did you mean harnesses?)"),
    ("[skill.'launcher/moxi-x']\nsource = 'y'\n",
     "unknown key skill.\"launcher/moxi-x\".source (did you mean harnesses?)"),
    ("[global]\nmod = 'off'\n", "unknown key global.mod (did you mean mode?)"),
    ("[hook.h]\nevent = 'E'\ncommand = 'c'\ntimeot = 3\n",
     "unknown key hook.h.timeot (did you mean timeout?)"),
    ("[permissions.p]\nclaude = { alow = ['x'] }\n",
     "unknown key permissions.p.claude.alow (did you mean allow?)"),
    ("[mcp.m]\ncommand = 'c'\nargz = []\n", "unknown key mcp.m.argz (did you mean args?)"),
])
def test_an_unknown_key_names_the_closest_known_one(folder, text, line):
    assert line in problems(text, folder)


@pytest.mark.parametrize("text, part", [
    ("[user]\nskills = 'review'\n", "user.skills: want a list of strings"),
    ("[global]\nmode = 'sometimes'\n", "global.mode: is 'sometimes'"),
    ("[user]\ndisabled = ['skill:x']\n", "is not <kind>:<name>@<harness>"),
    ("[user]\ndisabled = ['tool:x@claude']\n", "names the kind 'tool'"),
    ("[user]\ndisabled = ['skill:x@Claude']\n", "names the harness 'Claude'"),
    ("[hook.h]\nevent = 'E'\n", "hook.h: needs command"),
    ("[hook.h]\nevent = 'E'\ncommand = 'c'\ntimeout = 0\n", "hook.h.timeout"),
    ("[hook.h]\nevent = 'E'\ncommand = 'c'\ntimeout = true\n", "hook.h.timeout"),
    ("[mcp.m]\nargs = []\n", "mcp.m: needs command or url"),
    ("[mcp.m]\ncommand = 'c'\nenv = { TOKEN = 3 }\n", "mcp.m.env.TOKEN: want a string"),
    ("[permissions.p]\nallow = ['Bash(ls)']\n", "a rule in a harness's own form"),
    ("[permissions.p]\nallow = [{ shell = [] }]\n", "permissions.p.allow[1].shell"),
    ("[skill.x]\nharnesses = ['claude', 'Open Code']\n", "names the harness 'Open Code'"),
    ("[user]\nskills = ['.hidden']\n", "names skill '.hidden'"),
    ("[user]\nagents = ['comms/lead']\n", "names agent 'comms/lead'"),
    ("[user]\nskills = ['Comms/moxi-x']\n", "names the module skill 'Comms/moxi-x'"),
    ("[agent.'a/b']\n", "names agent 'a/b'"),
    ("user = 3\n", "user: want a table"),
])
def test_a_value_of_the_wrong_shape_is_a_usage_error(folder, text, part):
    assert any(part in line for line in problems(text, folder))


def test_every_problem_is_reported_at_once_each_naming_the_file(folder):
    with pytest.raises(ManifestError) as caught:
        read("[user]\nskils = []\n[skill.x]\nharness = []\n", folder)
    assert len(caught.value.lines) == 2
    assert str(caught.value).splitlines() == [
        f"{folder / 'loadout.toml'}: {line}" for line in caught.value.lines]


def test_text_that_is_not_toml_is_a_usage_error(folder):
    assert any("is not valid TOML" in line for line in problems("[user\n", folder))


def test_a_manifest_that_cannot_be_read_is_an_error(folder):
    (folder / "loadout.toml").mkdir(parents=True)
    with pytest.raises(ManifestError) as caught:
        manifest.load(folder)
    assert caught.value.exit_code == 1


@pytest.mark.parametrize("name, where", [
    ("~/code/app", "path"), ("/srv/app", "path"), ("./app", "path"), ("app\\sub", "path"),
    ("~", "path"), ("app", "key"), ("delta.web", "key"), ("Beta", "key"),
])
def test_a_section_name_is_a_path_or_a_key(folder, name, where):
    m = read(f'[project."{name.replace(chr(92), chr(92) * 2)}"]\nskills = ["x"]\n', folder)
    assert m.projects[0].where == where


def test_a_relative_path_section_is_read_from_the_loadout_folder(folder):
    m = read('[project."./app"]\nskills = ["x"]\n[project."../work/b"]\nskills = ["x"]\n',
             folder)
    assert [s.root for s in m.projects] == [str(folder / "app"),
                                             str(folder.parent / "work" / "b")]


def test_two_sections_for_one_project_are_refused(folder):
    home = os.path.expanduser("~")
    lines = problems(f'[project."~/app"]\n[project."{home}/app/"]\n', folder)
    assert any("name one project" in line for line in lines)
    lines = problems("[project.App]\n[project.app]\n", folder)
    assert any("name one project" in line for line in lines)


def test_hooks_permissions_and_mcp_in_a_project_are_refused_with_a_line(folder):
    m = read("""\
        [project.app]
        skills = ["x"]
        hooks = ["guard"]
        permissions = ["reads"]
        mcp = ["docs"]
        """, folder)
    section = m.projects[0]
    assert section.lists == {"skill": ("x",)}
    assert [(r.kind, r.name) for r in m.refused] == [("hook", "guard"),
                                                     ("permissions", "reads"),
                                                     ("mcp", "docs")]
    assert "written at user scope only" in m.refused[0].line


def test_a_name_listed_twice_is_listed_once(folder):
    assert read("[user]\nskills = ['a', 'b', 'a']\n", folder).user.names("skill") == ("a", "b")


def test_disabled_switches_only_its_own_section_and_star_means_every_harness(folder):
    m = read("""\
        [user]
        skills = ["x"]
        disabled = ["skill:x@*"]
        [project.app]
        skills = ["x"]
        disabled = ["skill:x@codex"]
        """, folder)
    assert m.user.is_disabled("skill", "x", "claude")
    assert m.user.is_disabled("skill", "x", "opencode")
    app = m.section("app")
    assert app.is_disabled("skill", "x", "codex")
    assert not app.is_disabled("skill", "x", "claude")
    assert not app.is_disabled("skill", "y", "codex")


def test_a_section_is_found_by_name_by_root_and_by_key_ignoring_case(folder):
    m = read('[project."~/code/app"]\n[project.Web]\n', folder)
    assert m.section(None) is m.user
    assert m.section("~/code/app").name == "~/code/app"
    assert m.section(os.path.expanduser("~/code/app/")).name == "~/code/app"
    assert m.section("web").name == "Web"
    assert m.section("other") is None


def test_parse_id_round_trips_and_refuses_bad_ids():
    ident = manifest.parse_id("skill:launcher/moxi-handoff@codex")
    assert ident == Id("skill", "launcher/moxi-handoff", "codex")
    assert str(ident) == "skill:launcher/moxi-handoff@codex"
    for bad in ("skill:x", "x@claude", "skill:@claude", "skill:x@", 3):
        with pytest.raises(ValueError):
            manifest.parse_id(bad)


def test_source_expands_quoted_in_a_command_and_plain_elsewhere(tmp_path):
    folder = tmp_path / "my loadout"
    command = "python3 {source}/hooks/guard.py"
    assert manifest.expand_command(command, folder, windows=False) == (
        f"python3 '{folder}'/hooks/guard.py")
    assert manifest.expand_command(command, folder, windows=True) == (
        f'python3 "{folder}"/hooks/guard.py')
    assert manifest.expand("{source}/docs", folder) == f"{folder}/docs"
    plain = tmp_path / "plain"
    assert manifest.expand_command(command, plain, windows=False) == (
        f"python3 {plain}/hooks/guard.py")


def test_an_items_own_source_is_absolute(folder):
    m = read("""\
        [skill.a]
        source = "elsewhere/a"
        [skill.b]
        source = "~/skills/b"
        [skill.c]
        source = "{source}/x/c"
        """, folder)
    assert m.source_of("skill", "a") == folder / "elsewhere" / "a"
    assert m.source_of("skill", "b") == Path(os.path.expanduser("~/skills/b"))
    assert m.source_of("skill", "c") == folder / "x" / "c"
    assert m.source_of("skill", "d") is None


def test_load_reads_the_configured_folder(folder, monkeypatch):
    from moxi_loadout import stores

    monkeypatch.setattr(stores, "source_dir", lambda: folder)
    folder.mkdir()
    (folder / "loadout.toml").write_text("[user]\nskills = ['x']\n", encoding="utf-8")
    assert manifest.load().user.names("skill") == ("x",)
