"""The projects link: interface 2, the topic kind and its check, prepare's defaults and
the remove refusal, each by local reads only; and the same through projects' own verbs."""

from __future__ import annotations

import subprocess

import pytest

from moxi_notes import projects_link
from moxi_notes.projects_link import prepare, refuse_remove

from notes_kit import add_project, configure, git, git_env, home, moxi_cmd, repo

check_topic = projects_link.KINDS["topic"][1]


class Project:
    """projects' `Project` as the link reads it."""

    def __init__(self, key, root, kind="project", notes_root=None):
        self.key, self.root, self.kind, self.notes_root = key, str(root), kind, (
            None if notes_root is None else str(notes_root))


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)


@pytest.fixture
def no_subprocess(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the projects link started a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)


def topics():
    return home() / "notes" / "topics"


def test_the_link_is_interface_2_with_a_dir_minted_topic():
    assert projects_link.INTERFACE == 2
    doc, check, mint = projects_link.KINDS["topic"]
    assert isinstance(doc, str) and callable(check) and mint == "dir"


# --- the topic check --------------------------------------------------------------------

def test_a_topic_under_topics_whose_notes_folder_is_its_root_passes(no_subprocess):
    root = str(topics() / "gardening")
    assert check_topic({"key": "gardening", "root": root, "notes_root": root}) is None


def test_a_topic_outside_topics_is_refused(no_subprocess):
    root = str(home() / "gardening")
    said = check_topic({"key": "gardening", "root": root, "notes_root": root})
    assert "under" in said and root in said
    assert check_topic({"root": str(topics()), "notes_root": str(topics())}) is not None


def test_a_topic_whose_notes_folder_is_not_its_root_is_refused(no_subprocess):
    root = str(topics() / "gardening")
    assert "notes folder is its root" in check_topic(
        {"root": root, "notes_root": str(home() / "notes" / "other")})
    assert check_topic({"root": root, "notes_root": None}) is not None


# --- prepare ----------------------------------------------------------------------------

def test_prepare_gives_a_topic_its_root_and_notes_folder(no_subprocess):
    said = prepare({"key": "gardening", "kind": "topic"})
    assert said == {"root": str(topics() / "gardening"),
                    "notes_root": str(topics() / "gardening")}
    assert all(isinstance(v, str) for v in said.values())


def test_prepare_fills_only_the_fields_left_unset(no_subprocess):
    root = str(topics() / "roses")
    assert prepare({"key": "gardening", "kind": "topic", "root": root}) == {
        "notes_root": root}
    assert prepare({"key": "g", "kind": "topic", "root": root, "notes_root": root}) is None
    assert prepare({"key": "alpha", "kind": "project",
                    "root": str(home() / "code" / "alpha"),
                    "notes_root": str(home() / "x")}) is None


def test_prepare_follows_the_prefix(no_subprocess):
    configure(prefix="team")
    assert prepare({"key": "g", "kind": "topic"})["root"] == str(
        home() / "notes" / "team" / "topics" / "g")


def test_prepare_pins_a_project_to_its_computed_folder(no_subprocess):
    said = prepare({"key": "alpha", "kind": "project",
                    "root": str(home() / "code" / "alpha")})
    assert said == {"notes_root": str(home() / "notes" / "code" / "alpha")}


def test_prepare_gives_no_default_where_none_is_computed(tmp_path, no_subprocess):
    assert prepare({"key": "alpha", "kind": "project",
                    "root": str(tmp_path / "srv" / "alpha")}) is None
    assert prepare({"key": "me", "kind": "agent", "root": str(home())}) is None
    assert prepare({"key": "alpha", "kind": "project"}) is None


# --- through projects' add --------------------------------------------------------------

def test_add_kind_topic_create_makes_a_plain_folder():
    code, out, err = moxi_cmd("projects", "add", "gardening", "--kind", "topic", "--create",
                              "--json")
    assert code == 0, out + err
    folder = topics() / "gardening"
    assert folder.is_dir() and not (folder / ".git").exists()
    assert str(folder) in out


def test_add_of_a_project_stores_its_computed_notes_folder():
    alpha = home() / "code" / "alpha"
    alpha.mkdir(parents=True)
    add_project("alpha", alpha)
    code, out, _err = moxi_cmd("projects", "show", "alpha", "--json")
    assert code == 0
    assert str(home() / "notes" / "code" / "alpha") in out


def test_add_refuses_a_topic_rooted_outside_topics():
    elsewhere = home() / "gardening"
    elsewhere.mkdir()
    code, _out, err = moxi_cmd("projects", "add", "gardening", "--kind", "topic",
                               "--root", str(elsewhere))
    assert code == 3 and "under" in err


# --- refuse_remove ----------------------------------------------------------------------

def test_a_set_notes_root_with_files_is_refused(no_subprocess):
    alpha = home() / "code" / "alpha"
    alpha.mkdir(parents=True)
    kept = home() / "notes" / "alpha-notes"
    (kept / "plans").mkdir(parents=True)
    (kept / "a.md").write_text("a\n")
    (kept / "plans" / "b.md").write_text("b\n")
    said = refuse_remove(Project("alpha", alpha, notes_root=kept))
    assert said == (f"project alpha's notes folder {kept} holds 2 files; removing alpha "
                    f"moves its sessions' notes to {home() / 'notes' / 'code' / 'alpha'}; "
                    "move them, or clear it first: moxi projects set alpha --no-notes-root")


def test_a_set_notes_root_that_is_empty_or_missing_is_allowed(no_subprocess):
    alpha = home() / "code" / "alpha"
    kept = home() / "notes" / "alpha-notes"
    assert refuse_remove(Project("alpha", alpha, notes_root=kept)) is None
    kept.mkdir(parents=True)
    assert refuse_remove(Project("alpha", alpha, notes_root=kept)) is None


def test_a_notes_root_equal_to_the_computed_folder_is_allowed(no_subprocess):
    alpha = home() / "code" / "alpha"
    computed = home() / "notes" / "code" / "alpha"
    computed.mkdir(parents=True)
    (computed / "a.md").write_text("a\n")
    assert refuse_remove(Project("alpha", alpha, notes_root=computed)) is None


def test_a_nested_project_with_files_is_refused_with_its_notes_root_unset():
    outer = repo(home() / "code" / "alpha")
    inner = outer / "tools" / "beta"
    inner.mkdir(parents=True)
    folder = home() / "notes" / "code" / "alpha" / "tools" / "beta"
    folder.mkdir(parents=True)
    (folder / "plan.md").write_text("p\n")
    said = refuse_remove(Project("beta", inner))
    assert said == (f"project beta's notes folder {folder} holds 1 file; removing beta "
                    f"moves its sessions' notes to {home() / 'notes' / 'code' / 'alpha'}; "
                    "move them first")


def test_a_project_at_its_repo_top_with_its_notes_root_unset_is_allowed(no_subprocess):
    alpha = home() / "code" / "alpha"
    (alpha / ".git").mkdir(parents=True)
    folder = home() / "notes" / "code" / "alpha"
    folder.mkdir(parents=True)
    (folder / "plan.md").write_text("p\n")
    assert refuse_remove(Project("alpha", alpha)) is None


def test_a_project_at_a_linked_worktree_is_refused_as_its_main_work_tree_would_answer():
    alpha = repo(home() / "code" / "alpha", commit=True)
    linked = home() / "work" / "alpha-feature"
    git("worktree", "add", "-q", str(linked), "-b", "feature", cwd=alpha)
    folder = home() / "notes" / "work" / "alpha-feature"
    folder.mkdir(parents=True)
    (folder / "plan.md").write_text("p\n")
    said = refuse_remove(Project("feature", linked))
    assert said == (f"project feature's notes folder {folder} holds 1 file; removing "
                    f"feature moves its sessions' notes to "
                    f"{home() / 'notes' / 'code' / 'alpha'}; move them first")


def test_a_topic_is_never_refused(no_subprocess):
    folder = topics() / "gardening"
    folder.mkdir(parents=True)
    (folder / "plan.md").write_text("p\n")
    assert refuse_remove(Project("gardening", folder, "topic", folder)) is None


def test_a_project_off_home_with_a_notes_root_says_its_sessions_lose_it(tmp_path,
                                                                        no_subprocess):
    kept = home() / "notes" / "srv-notes"
    kept.mkdir(parents=True)
    (kept / "a.md").write_text("a\n")
    said = refuse_remove(Project("srv", tmp_path / "srv", notes_root=kept))
    assert "leaves its sessions with no notes folder" in said


def test_projects_remove_asks_the_link():
    alpha = home() / "code" / "alpha"
    alpha.mkdir(parents=True)
    kept = home() / "notes" / "alpha-notes"
    kept.mkdir(parents=True)
    (kept / "a.md").write_text("a\n")
    add_project("alpha", alpha, "--notes-root", str(kept))
    code, _out, err = moxi_cmd("projects", "remove", "alpha")
    assert code == 3 and "holds 1 file" in err
    assert moxi_cmd("projects", "set", "alpha", "--no-notes-root")[0] == 0
    code, out, err = moxi_cmd("projects", "remove", "alpha")
    assert code == 0, out + err
