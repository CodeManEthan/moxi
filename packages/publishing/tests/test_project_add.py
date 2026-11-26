"""publishing's questions at `moxi projects add`: put only for a root in a git repo, the
`project_add.*` settings as presets, and the acts on each answer."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import write_config

from moxi_repo.api import quote_arg

from moxi_publishing import approval, cli, flag
from moxi_publishing import projects_link as link


def fields(root, mint=None) -> dict:
    return {"key": "proj-a", "root": str(root), "kind": "project", "lead": None,
            "notes_root": None, "mint": mint}


def by_key(put) -> dict:
    return {q["key"]: q for q in put}


def project(root) -> SimpleNamespace:
    return SimpleNamespace(key="proj-a", root=str(root))


def test_no_question_for_a_root_in_no_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert link.questions(fields(plain)) is None
    assert link.questions(fields(tmp_path / "not-yet", mint="dir")) is None


def test_a_root_in_a_repo_or_one_create_makes_a_repo_gets_both_questions(repo, tmp_path):
    put = by_key(link.questions(fields(repo / "src")))
    assert list(put) == ["clean_history", "push_approval", "approval_branches"]
    assert put["clean_history"]["text"] == ("Keep this repo's history publishable (clean "
                                            "commit messages, no private text)?")
    assert put["clean_history"]["suggested"] == "yes"
    assert put["clean_history"]["later"] == (
        f"moxi publishing clean-history enable {repo / 'src'}")
    assert put["push_approval"]["text"] == ("Ask for approval before agents push to some "
                                            "branches?")
    assert put["push_approval"]["suggested"] == "no"
    assert put["approval_branches"]["after"] == ["push_approval", "yes"]
    assert put["approval_branches"].get("suggested") is None
    assert all(q["preset"] is None for q in put.values())
    assert link.questions(fields(tmp_path / "new", mint="git")) is not None


def test_a_root_with_a_space_is_quoted_in_the_command(repo, tmp_path):
    spaced = tmp_path / "my proj"
    spaced.mkdir()
    (spaced / ".git").mkdir()
    put = by_key(link.questions(fields(spaced)))
    assert put["clean_history"]["later"] == (
        f"moxi publishing clean-history enable {quote_arg(str(spaced))}")
    assert quote_arg(str(spaced)) != str(spaced)


@pytest.mark.parametrize("text, presets", [
    ('[project_add]\nclean_history = "yes"\npush_approval = "no"\n',
     {"clean_history": "yes", "push_approval": "no", "approval_branches": None}),
    ('[project_add]\nclean_history = "no"\npush_approval = "yes"\n'
     'approval_branches = ["main", "release/*"]\n',
     {"clean_history": "no", "push_approval": "yes", "approval_branches": "main release/*"}),
    ('[project_add]\npush_approval = "yes"\n',
     {"clean_history": None, "push_approval": "yes", "approval_branches": None}),
    ('[project_add]\npush_approval = "ask"\napproval_branches = ["main"]\n',
     {"clean_history": None, "push_approval": None, "approval_branches": None}),
])
def test_the_settings_answer_for_the_machine(repo, text, presets):
    write_config("publishing", text)
    put = by_key(link.questions(fields(repo)))
    assert {key: q["preset"] for key, q in put.items()} == presets


def test_a_config_that_cant_be_read_asks_both(repo):
    write_config("publishing", '[project_add]\nclean_history = "always"\n')
    put = by_key(link.questions(fields(repo)))
    assert put["clean_history"]["preset"] is None and put["push_approval"]["preset"] is None


def test_yes_to_clean_history_turns_it_on_and_no_leaves_it_off(repo, names):
    assert link.answer(project(repo), "clean_history", "no") == "clean history left off"
    assert not flag.is_clean(str(repo))
    said = link.answer(project(repo), "clean_history", "yes")
    assert said == f"{repo} keeps a clean history ({flag.CLEAN})"
    assert flag.is_clean(str(repo))
    assert link.answer(project(repo), "clean_history", "yes") == (
        f"{repo} already keeps a clean history")


def test_the_branches_named_turn_push_approval_on_and_none_leaves_it_off(repo):
    assert link.answer(project(repo), "push_approval", "yes") is None
    assert link.answer(project(repo), "approval_branches", "  ") == (
        "push approval left off: no branch was named")
    assert flag.piece_settings(str(repo), approval.PIECE_ID) is None
    said = link.answer(project(repo), "approval_branches", "main, release/*")
    assert said.startswith("an agent's push of main release/*")
    assert flag.piece_settings(str(repo), approval.PIECE_ID)[approval.BRANCH] == [
        "main", "release/*"]


def test_a_branch_approval_refuses_raises_with_its_text(repo):
    with pytest.raises(flag.Failure, match="--branch: "):
        link.answer(project(repo), "approval_branches", "refs/heads/main")
    assert flag.piece_settings(str(repo), approval.PIECE_ID) is None


def test_the_questions_fit_projects_contract():
    contributions = pytest.importorskip("moxi_projects.contributions")
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    put = contributions._read_questions("publishing", link.questions(fields(here)))
    assert [q.key for q in put] == ["clean_history", "push_approval", "approval_branches"]


def test_a_project_add_names_the_commands_without_answers_and_applies_a_yes_setting(
        repo, names, tmp_path):
    acts = pytest.importorskip("moxi_projects.acts")
    added = acts.add("proj-a", root=str(repo))
    assert added.asked == [
        "publishing asks: Keep this repo's history publishable (clean commit messages, no "
        f"private text)? (suggested: yes); to set it later: moxi publishing clean-history "
        f"enable {repo}",
        "publishing asks: Ask for approval before agents push to some branches? "
        f"(suggested: no); to set it later: moxi publishing approval enable {repo} "
        "--branch PATTERN"]
    assert not flag.is_clean(str(repo))
    write_config("publishing", '[project_add]\nclean_history = "yes"\n'
                               'push_approval = "yes"\napproval_branches = ["main"]\n')
    other = tmp_path / "proj-b"
    import shutil

    shutil.copytree(repo, other, symlinks=True)
    added = acts.add("proj-b", root=str(other))
    assert added.asked == [] and added.warnings == []
    assert flag.is_clean(str(other))
    assert flag.piece_settings(str(other), approval.PIECE_ID)[approval.BRANCH] == ["main"]


# -- a repo left out of project add ------------------------------------------------------

def test_a_left_out_repo_answered_yes_turns_nothing_on_and_says_why(repo, names, capsys):
    assert cli.main(["project-add", "disable", str(repo)]) == 0
    assert "is left out of project add" in capsys.readouterr().out
    assert flag.is_left_out(str(repo))
    said = link.answer(project(repo), "clean_history", "yes")
    assert said == (f"clean history left off: {repo} is left out of project add "
                    f"({flag.LEFT_OUT}); to turn it on there, moxi publishing "
                    f"clean-history enable {quote_arg(str(repo))}")
    assert not flag.is_clean(str(repo))
    said = link.answer(project(repo), "push_approval", "yes")
    assert said.startswith("push approval left off: ") and "approval enable" in said
    assert link.answer(project(repo), "approval_branches", "main") is None
    assert flag.piece_settings(str(repo), approval.PIECE_ID) is None
    # The verbs themselves still act there.
    assert link.answer(project(repo), "push_approval", "no") is None


def test_an_unmarked_repo_answered_yes_turns_both_on(repo, names, capsys):
    assert cli.main(["project-add", "disable", str(repo)]) == 0
    assert cli.main(["project-add", "enable", str(repo)]) == 0
    assert capsys.readouterr().out.strip().endswith("is no longer left out of project add")
    assert not flag.is_left_out(str(repo))
    assert cli.main(["project-add", "enable", str(repo)]) == 0
    assert "nothing changed" in capsys.readouterr().out
    link.answer(project(repo), "clean_history", "yes")
    link.answer(project(repo), "approval_branches", "main")
    assert flag.is_clean(str(repo))
    assert flag.piece_settings(str(repo), approval.PIECE_ID)[approval.BRANCH] == ["main"]


def test_yes_presets_skip_a_left_out_repo_and_act_in_an_unmarked_one(repo, names,
                                                                      tmp_path):
    acts = pytest.importorskip("moxi_projects.acts")
    import shutil

    write_config("publishing", '[project_add]\nclean_history = "yes"\n'
                               'push_approval = "yes"\napproval_branches = ["*"]\n')
    other = tmp_path / "proj-b"
    shutil.copytree(repo, other, symlinks=True)
    link.leave_out(str(repo))
    added = acts.add("proj-a", root=str(repo))
    assert added.asked == [] and added.warnings == []
    assert not flag.is_clean(str(repo))
    assert flag.piece_settings(str(repo), approval.PIECE_ID) is None
    said = [note for note in added.notes if note.startswith("publishing: ")]
    assert [note.split(" left off: ")[0] for note in said] == [
        "publishing: clean history", "publishing: push approval"]
    assert all(f"is left out of project add ({flag.LEFT_OUT})" in n for n in said)
    added = acts.add("proj-b", root=str(other))
    assert added.asked == [] and added.warnings == []
    assert flag.is_clean(str(other))
    assert flag.piece_settings(str(other), approval.PIECE_ID)[approval.BRANCH] == ["*"]
