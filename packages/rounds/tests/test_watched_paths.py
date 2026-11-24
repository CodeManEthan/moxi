"""The watched-paths guard: which writes it matches (folder patterns, `**`, case,
`only_new`, a Codex patch's sections, the project's root in each layout of a tree), the
line it adds once per agent, that it never refuses and fails open, and its switch."""

from __future__ import annotations

import importlib
import os
import types
from pathlib import Path

import pytest

from moxi_rounds import settings as own
from moxi_rounds import watching

turns = pytest.importorskip("moxi_turns")

WATCHED = """\
    [projects.proj-a]
    watched_paths = [
      { pattern = "packages/*/", only_new = true, note = "A new package folder.", skills = ["layout-review"] },
      { pattern = "packages/*/pyproject.toml", note = "A package's pyproject.", skills = ["layout-review", "kit:deps"] },
      { pattern = "packages/core/src/**", only_new = true, note = "A new file in the core.", skills = ["core-review"] },
    ]
    """


@pytest.fixture(autouse=True)
def fresh():
    watching.forget()
    yield
    watching.forget()


@pytest.fixture(name="root")
def root_fixture(add_project, write_config) -> Path:
    """proj-a, a plain folder with one package, `packages/core`, watching three
    entries."""
    root = add_project("proj-a")
    (root / "packages" / "core" / "src").mkdir(parents=True)
    (root / "packages" / "core" / "pyproject.toml").write_text("[project]\n",
                                                               encoding="utf-8")
    (root / "packages" / "core" / "src" / "old.py").write_text("", encoding="utf-8")
    write_config(WATCHED)
    return root


def write(path, *, agent=None, cwd=None, tool="Write"):
    field = {"Write": "content", "Edit": "new_string"}[tool]
    call = turns.ToolCall(harness="claude", session_id="s", agent_id=agent, tool=tool,
                          input={"file_path": str(path), field: "x"},
                          cwd=None if cwd is None else str(cwd))
    return watching.judge(call)


def patch(text, *, cwd, agent=None):
    call = turns.ToolCall(harness="codex", session_id="s", agent_id=agent,
                          tool="apply_patch", input={"command": text}, cwd=str(cwd))
    return watching.judge(call)


def line_of(answer) -> str | None:
    if answer is None:
        return None
    assert isinstance(answer, turns.Allow)
    return answer.line


# -- matching ----------------------------------------------------------------------------

@pytest.mark.parametrize("pattern, parts, expected", [
    ("packages/*", ["packages", "core"], True),
    ("packages/*", ["packages", "core", "x"], False),
    ("a/?.py", ["a", "b.py"], True),
    ("a/[bc].py", ["a", "c.py"], True),
    ("a/[bc].py", ["a", "d.py"], False),
    ("src/**", ["src"], True),
    ("src/**", ["src", "a", "b", "c.py"], True),
    ("**/x.toml", ["x.toml"], True),
    ("a/**/z", ["a", "b", "c", "z"], True),
    ("Packages/CORE", ["packages", "core"], True),
])
def test_patterns_match_by_parts_case_blind(pattern, parts, expected):
    assert watching.matched(pattern.split("/"), parts) is expected


def test_a_new_package_gets_the_line_with_its_note_and_skills(root):
    said = line_of(write(root / "packages" / "fresh" / "README.md"))

    assert said == ("rounds: watched path of proj-a: packages/fresh/ - A new package "
                    "folder. Load: layout-review.")


def test_a_write_into_a_package_that_was_there_is_not_new(root):
    assert write(root / "packages" / "core" / "README.md") is None


def test_every_edit_of_a_matched_file_counts_and_each_agent_hears_it_once(root):
    pyproject = root / "packages" / "core" / "pyproject.toml"

    said = line_of(write(pyproject, tool="Edit"))
    assert said == ("rounds: watched path of proj-a: packages/core/pyproject.toml - A "
                    "package's pyproject. Load: layout-review, kit:deps.")
    assert write(pyproject, tool="Edit") is None
    assert line_of(write(pyproject, agent="sub-1", tool="Edit")) == said
    assert write(pyproject, agent="sub-1", tool="Edit") is None


def test_a_new_file_deep_in_a_tree_matches_a_double_star_and_an_old_one_does_not(root):
    assert "packages/core/src/a/new.py" in line_of(
        write(root / "packages" / "core" / "src" / "a" / "new.py"))
    assert write(root / "packages" / "core" / "src" / "old.py") is None


def test_an_only_new_folder_pattern_counts_a_new_folder_at_any_depth_it_matches(
        root, write_config):
    write_config("""\
        [projects.proj-a]
        watched_paths = [
          { pattern = "src/**/", only_new = true, note = "A new source folder.", skills = ["s"] },
        ]
        """)
    (root / "src" / "old").mkdir(parents=True)

    said = line_of(write(root / "src" / "old" / "fresh" / "a.py"))
    assert said == ("rounds: watched path of proj-a: src/old/fresh/ - A new source "
                    "folder. Load: s.")
    assert write(root / "src" / "old" / "b.py") is None


def test_a_path_relative_to_the_call_s_cwd_and_in_another_case_matches(root):
    said = line_of(write(Path("PACKAGES") / "Core" / "pyproject.toml", cwd=root))

    assert said is not None and "PACKAGES/Core/pyproject.toml" in said


@pytest.mark.skipif(os.name != "nt", reason="a backslash separates parts on Windows only")
def test_a_written_path_with_backslashes_matches_on_windows(root):
    said = line_of(write(f"{root}\\packages\\core\\pyproject.toml"))

    assert said is not None and "packages/core/pyproject.toml" in said


def test_a_codex_patch_names_each_written_file_once_and_never_a_deleted_one(root):
    text = ("*** Begin Patch\n"
            "*** Add File: packages/core/src/added.py\n+x\n"
            "*** Update File: packages/core/pyproject.toml\n@@\n+y\n"
            "*** Delete File: packages/gone/pyproject.toml\n"
            "*** Update File: packages/core/src/old.py\n*** Move to: packages/moved/old.py\n"
            "@@\n+z\n"
            "*** End Patch\n")

    said = line_of(patch(text, cwd=root))

    assert said == (
        "rounds: watched path of proj-a: packages/core/src/added.py - A new file in the "
        "core.; packages/core/pyproject.toml - A package's pyproject.; packages/moved/ - "
        "A new package folder. Load: core-review, layout-review, kit:deps.")
    assert "gone" not in said


def test_an_add_file_section_is_new_by_its_own_word(root):
    text = ("*** Begin Patch\n*** Add File: packages/core/src/old.py\n+x\n"
            "*** End Patch\n")

    assert "packages/core/src/old.py" in line_of(patch(text, cwd=root))


def test_outside_every_project_or_with_no_entries_it_says_nothing(root, tmp_path,
                                                                 add_project):
    other = add_project("proj-b")

    assert write(tmp_path / "elsewhere" / "packages" / "x" / "a.md") is None
    assert write(other / "packages" / "x" / "a.md") is None


def test_it_never_refuses_a_call(root):
    for answer in (write(root / "packages" / "fresh" / "a.md"),
                   write(root / "elsewhere.md"),
                   watching.judge(turns.ToolCall(harness="claude", session_id="s",
                                                 agent_id=None, tool="Bash",
                                                 input={"command": "mkdir x"}))):
        assert answer is None or isinstance(answer, turns.Allow)


# -- the project's root as it stands in the file's tree ----------------------------------

def _main_tree(top: Path) -> None:
    (top / ".git" / "worktrees").mkdir(parents=True)


def _linked(main: Path, at: Path, name: str) -> None:
    """A linked worktree of `main` at `at`, as git writes one: a `.git` file naming
    its git dir, whose `commondir` names the main `.git`."""
    gitdir = main / ".git" / "worktrees" / name
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    at.mkdir(parents=True)
    (at / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")


def test_a_linked_worktree_inside_the_root_is_matched_from_its_own_top(root):
    _main_tree(root)
    worktree = root / ".claude" / "worktrees" / "x"
    _linked(root, worktree, "x")

    said = line_of(write(worktree / "packages" / "fresh" / "a.md"))

    assert said is not None and "of proj-a: packages/fresh/ -" in said


def test_a_linked_worktree_elsewhere_takes_the_root_s_place_below_its_git_top(
        tmp_path, add_project, write_config):
    from moxi import cli

    main = tmp_path / "main"
    (main / "sub").mkdir(parents=True)
    _main_tree(main)
    assert cli.main(["projects", "add", "proj-a", "--root", str(main / "sub")]) == 0
    write_config(WATCHED)
    worktree = tmp_path / "trees" / "w"
    _linked(main, worktree, "w")

    said = line_of(write(worktree / "sub" / "packages" / "fresh" / "a.md"))
    assert said is not None and "packages/fresh/" in said
    assert write(worktree / "packages" / "other" / "a.md") is None


def test_a_root_below_its_git_top_is_the_base(tmp_path, write_config):
    from moxi import cli

    main = tmp_path / "main"
    (main / "sub").mkdir(parents=True)
    _main_tree(main)
    assert cli.main(["projects", "add", "proj-a", "--root", str(main / "sub")]) == 0
    write_config(WATCHED)

    assert "packages/fresh/" in line_of(write(main / "sub" / "packages" / "fresh" / "a"))
    assert write(main / "packages" / "fresh" / "a") is None


def test_a_root_with_no_git_is_the_base(root):
    assert watching.base_of(os.path.join(root, "packages", "x"), str(root)) == str(root)


# -- failing open ------------------------------------------------------------------------

def test_a_config_that_will_not_load_lets_the_call_run_and_logs_once(root, monkeypatch,
                                                                     caplog):
    from moxi.config import ConfigError

    def broken():
        raise ConfigError(None, "projects", "is broken", "fix it")

    monkeypatch.setattr(own, "load", broken)

    assert write(root / "packages" / "fresh" / "a.md") is None
    assert write(root / "packages" / "fresh2" / "a.md") is None
    assert len([one for one in caplog.records if "watched-paths" in one.getMessage()]) == 1


def test_a_projects_store_that_can_t_be_read_lets_the_call_run(root, monkeypatch):
    monkeypatch.setattr(watching, "_project_of",
                        lambda file: (_ for _ in ()).throw(OSError("unreadable")))

    assert write(root / "packages" / "fresh" / "a.md") is None


def test_the_config_is_read_again_when_it_changes(root, write_config):
    assert write(root / "packages" / "core" / "src" / "a.py") is not None
    write_config("")

    assert write(root / "packages" / "core" / "src" / "b.py") is None


# -- the guard as turns loads it ---------------------------------------------------------

def test_the_guard_value_and_its_switch(write_config):
    from moxi.units import switch_problem

    from moxi_rounds import guards

    read_switch = importlib.import_module("moxi_turns.guards").read_switch
    (guard,) = guards.GUARDS
    assert guard.problems() == []
    assert (guard.name, guard.switch, guard.judge, guard.on_error, guard.who) == (
        "watched-paths", "watched_paths_guard.enabled", "moxi_rounds.watching:judge",
        "allow", "any")
    assert switch_problem(own.schema(), "watched_paths_guard.enabled") is None
    assert read_switch("rounds", "watched_paths_guard.enabled") == (False, None)
    write_config("[watched_paths_guard]\nenabled = true\n")
    assert read_switch("rounds", "watched_paths_guard.enabled") == (True, None)


@pytest.mark.parametrize("on, switch, runs", [
    ([], False, False), ([], True, True), (["rounds.watched-paths"], False, True)])
def test_it_runs_by_the_switch_or_by_guards_run_and_is_off_by_default(on, switch, runs):
    from moxi_rounds import guards

    entry = types.SimpleNamespace(name="rounds", load=lambda: guards)
    loaded = turns.guards.load(on, entries=[entry],
                               switches=lambda module, key: (switch, None))

    assert (loaded.get("rounds.watched-paths") is not None) is runs
    assert not loaded.problems
