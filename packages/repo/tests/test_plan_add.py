"""What `add` would write, read before it writes (`api.plan_add`), and `add` writing
exactly that plan when it is given the plan's digest.

Each act test reads every key of repo's in the repo's config and every file in its hooks
dir before and after, and checks that what changed is what the plan named."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from moxi_repo import api, gitconf
from tests.conftest import POSIX_ONLY, git
from tests.test_commit_rules import write_config
from tests.test_plan_sync import changed, held, own_hook, snapshot, touched  # noqa: F401 -- held is a fixture

pytestmark = POSIX_ONLY

TAG = r"\bTAG-[0-9]+\b"


def config_bytes(repo: Path) -> bytes:
    return (repo / ".git" / "config").read_bytes()


def acted(repo: Path, *pids: str) -> tuple[dict, api.Report]:
    """Plan the add, make it with the plan's digest, and check it wrote exactly what the
    plan named, with nothing left to write after it."""
    plan = api.plan_add(repo, list(pids))
    assert plan["refusal"] is None, plan["refusal"]
    where = gitconf.resolve(repo)
    before = snapshot(where)
    report = api.add(repo, *pids, expect=plan["digest"])
    assert changed(before, snapshot(where)) == touched(plan["writes"])
    assert api.plan_add(repo, list(pids))["writes"] == []
    return plan, report


# -- the plan, and the act writing it -------------------------------------------------------

def test_a_repo_joins_the_index_as_an_item_and_indexing_writes_nothing_in_it(demo):
    repo = demo.repo()
    plan = api.plan_add(repo)
    assert (plan["root"], plan["name"], plan["is_repo"], plan["indexed"]) == (
        str(repo), repo.name, True, False)
    assert plan["writes"] == [{"place": "index", "piece": None, "event": None, "key": None,
                               "from": None, "to": str(repo), "seed": False}]
    assert api.add(repo, expect=plan["digest"]).lines == [f"indexed {repo}"]
    assert api.indexed_at(repo) and not (repo / ".git" / "moxi-repo").exists()
    again = api.plan_add(repo)
    assert again["indexed"] and again["writes"] == []


def test_a_new_piece_in_shim_mode_names_every_key_shim_and_moved_hook(demo):
    repo = demo.repo()
    api.add(repo, mode="shim")
    own_hook(repo / ".git" / "hooks" / "pre-commit")
    plan, report = acted(repo, "demo/gate")
    assert plan["pieces"] == [{"id": "demo/gate", "event": "pre-commit", "new": True,
                               "implied_by": None, "seeds": []}]
    assert plan["own_hooks"] == ["pre-commit"] and plan["mode"] == "shim"
    assert plan["mode_change"] is None and plan["in_progress"] is None
    keys = [i["key"] for i in plan["writes"] if i["place"] == "config"]
    assert keys == [f"moxi-repo.piece.demo/gate.{var}"
                    for var in ("event", "target", "fail", "order", "enabled")
                    ] + ["moxi-repo.moved"]
    hooks = repo / ".git" / "hooks"
    assert [(i["key"], i["from"], i["to"]) for i in plan["writes"] if i["place"] == "hooks"
            ] == [("hook", str(hooks / "pre-commit"), str(hooks / "pre-commit.local")),
                  ("shim", None, str(hooks / "pre-commit"))]
    # The act's report reads as it did before plans.
    assert report.lines[0] == f"added demo/gate (pre-commit, closed) in {repo}"
    assert any("pre-commit.local" in line for line in report.lines[1:])
    assert not report.already


def test_a_key_set_by_hand_is_an_item_back_to_its_declaration_and_the_act_writes_it(demo):
    repo = demo.repo()
    api.add(repo, ["demo/lint"], mode="config")
    git(repo, "config", "moxi-repo.piece.demo/lint.fail", "closed")
    plan, report = acted(repo, "demo/lint")
    assert plan["pieces"][0]["new"] is False
    [item] = [i for i in plan["writes"] if i["key"] == "moxi-repo.piece.demo/lint.fail"]
    assert (item["piece"], item["from"], item["to"]) == ("demo/lint", "closed", "open")
    assert [i["key"] for i in plan["writes"] if i["piece"]] == [item["key"]]
    assert report.lines[0] == f"rewrote demo/lint (pre-commit, open) in {repo}"
    assert git(repo, "config", "moxi-repo.piece.demo/lint.fail").stdout.strip() == "open"


@pytest.mark.parametrize("pid", list(api.EVERYWHERE))
def test_a_piece_its_everywhere_key_brings_in_is_planned_with_that_key(demo, pid):
    section, _, key = api.EVERYWHERE[pid].partition(".")
    write_config(f"[{section}]\n{key} = true\n")
    plan, report = acted(demo.repo(), "demo/gate")
    assert [(p["id"], p["implied_by"]) for p in plan["pieces"]] == [
        ("demo/gate", None), (pid, api.EVERYWHERE[pid])]
    assert report.lines[1].endswith(f"({api.EVERYWHERE[pid]})")


def test_seeds_are_named_and_only_the_drawn_salt_is_left_out(demo):
    from moxi_repo.commit_proof import SALT

    write_config(f"[commit_rules]\ntag_patterns = ['{TAG}']\n")
    repo = demo.repo()
    plan = api.plan_add(repo, [api.COMMIT_PROOF, api.COMMIT_RULES])
    assert [p["seeds"] for p in plan["pieces"]] == [[SALT], ["tag-pattern"]]
    seeds = {i["key"]: i for i in plan["writes"] if i["seed"]}
    assert seeds[f"moxi-repo.piece.{api.COMMIT_PROOF}.{SALT}"]["to"] is None
    assert seeds[f"moxi-repo.piece.{api.COMMIT_RULES}.tag-pattern"]["to"] == TAG
    proof = [api.plan_add(repo, [api.COMMIT_PROOF])["digest"] for _ in range(2)]
    assert proof[0] == proof[1]
    api.add(repo, api.COMMIT_PROOF, expect=proof[0])
    salt = git(repo, "config", f"moxi-repo.piece.{api.COMMIT_PROOF}.{SALT}").stdout.strip()
    assert salt and salt not in str(plan)


def test_a_mode_change_and_a_merge_in_progress_are_in_the_plan(demo):
    repo = demo.repo()
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 56, 0))
    api.add(repo, ["demo/gate"])
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 43, 0))
    (repo / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    plan = api.plan_add(repo, ["demo/gate"])
    assert plan["mode_change"] == {"from": "config", "to": "shim"} and plan["mode"] == "shim"
    assert plan["in_progress"] == "merge" and plan["git_version"] == "2.43.0"
    assert any(i["key"] == "moxi-repo.mode" and i["to"] == "shim" for i in plan["writes"])
    (repo / ".git" / "MERGE_HEAD").unlink()
    (repo / ".git" / "rebase-merge").mkdir()
    assert api.plan_add(repo, ["demo/gate"])["in_progress"] == "rebase"


# -- the act with a digest ------------------------------------------------------------------

def test_a_repeated_send_writes_nothing_and_is_already(demo):
    repo = demo.repo()
    plan = api.plan_add(repo, ["demo/gate"])
    assert not api.add(repo, ["demo/gate"], expect=plan["digest"]).already
    config = config_bytes(repo)
    again = api.add(repo, ["demo/gate"], expect=plan["digest"])
    assert again.already and again.lines == [] and config_bytes(repo) == config


@pytest.mark.parametrize("between", ["everywhere", "tag-pattern", "terminal add", "press"])
def test_a_change_between_plan_and_act_writes_nothing_and_is_changed(demo, between):
    write_config(f"[commit_rules]\ntag_patterns = ['{TAG}']\n")
    repo = demo.repo()
    api.add(repo)
    pids = [api.COMMIT_RULES] if between == "tag-pattern" else ["demo/gate"]
    plan = api.plan_add(repo, pids)
    if between == "everywhere":
        write_config("[commit_rules]\neverywhere = true\n")
    elif between == "tag-pattern":
        write_config("[commit_rules]\ntag_patterns = ['\\bNEW-[0-9]+\\b']\n")
    elif between == "terminal add":
        api.add(repo, ["demo/watch"])
    else:
        api.add(repo, ["demo/watch"], expect=api.plan_add(repo, ["demo/watch"])["digest"])
    config = config_bytes(repo)
    with pytest.raises(api.Changed) as caught:
        api.add(repo, *pids, expect=plan["digest"])
    assert config_bytes(repo) == config
    assert caught.value.plan["digest"] != plan["digest"]
    assert caught.value.plan == api.plan_add(repo, pids)


def test_two_acts_of_one_plan_at_once_write_it_once(demo):
    repo = demo.repo()
    plan = api.plan_add(repo, ["demo/gate", "demo/watch"])
    answers: list[str] = []

    def act() -> None:
        try:
            report = api.add(repo, ["demo/gate", "demo/watch"], expect=plan["digest"])
        except api.Changed:
            answers.append("changed")
        else:
            answers.append("already" if report.already else "done")

    threads = [threading.Thread(target=act) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert sorted(answers) in (["already", "done"], ["changed", "done"])


def test_a_git_error_after_the_first_write_is_partial_and_one_before_it_is_not(demo):
    repo = demo.repo()
    api.add(repo)
    put = gitconf._put_values
    calls: list[str] = []
    fail_at = [2]

    def failing(where, key, values):
        calls.append(key)
        if len(calls) == fail_at[0]:
            raise gitconf.GitError(f"git config could not set {key}: no room")
        put(where, key, values)

    demo.mp.setattr(gitconf, "_put_values", failing)
    with pytest.raises(api.Failure) as caught:
        api.add(repo, ["demo/gate"], expect=api.plan_add(repo, ["demo/gate"])["digest"])
    assert caught.value.partial and "no room" in caught.value.text
    other = demo.repo("other")
    api.add(other)
    calls.clear()
    fail_at[0] = 1
    with pytest.raises(api.Failure) as caught:
        api.add(other, ["demo/gate"], expect=api.plan_add(other, ["demo/gate"])["digest"])
    assert not caught.value.partial


# -- refusals -------------------------------------------------------------------------------

def test_each_refusal_is_named_in_the_plan_and_by_the_verb(demo, held):
    repo = demo.repo()
    plain = demo.tmp / "plain"
    plain.mkdir()
    cases = {"not-a-repo": (plain, ["demo/gate"]),
             "unknown-piece": (repo, ["demo/nothing"]),
             "declaration-refused": (repo, ["demo/reserved"])}
    for code, (where, pids) in cases.items():
        plan = api.plan_add(where, pids)
        assert plan["refusal"]["code"] == code and plan["digest"] is None
        assert plan["writes"] == []
        with pytest.raises(api.Failure) as caught:
            api.add(where, pids)
        assert caught.value.reason == code and caught.value.text == plan["refusal"]["text"]
    assert api.plan_add("relative/path", ["demo/gate"])["refusal"]["code"] == "not-absolute"
    assert not api.plan_add(plain)["is_repo"]
    # A folder that isn't there reads as one sentence, whether alone or after the verb's.
    gone = demo.tmp / "gone"
    assert api.plan_add(gone)["refusal"]["text"] == f"no git work tree at {gone}: not a directory"
    with pytest.raises(api.Usage) as caught:
        api.add(repo, api.COMMIT_RULES, sets=["tag-pattern=[unclosed"])
    assert caught.value.reason == "seed-refused"
    (repo / ".git" / "hooks").mkdir(exist_ok=True)
    (repo / ".git" / "hooks" / "pre-commit.local").write_text("#!/bin/sh\n")
    api.add(repo, mode="shim")
    assert api.plan_add(repo, ["demo/gate"])["refusal"]["code"] == "shim-place"
    (repo / ".git" / "hooks" / "pre-commit.local").unlink()
    held(repo)
    with pytest.raises(api.Busy) as caught:
        api.add(repo, ["demo/gate"])
    assert caught.value.reason == "busy"


def test_old_git_an_unreadable_index_and_config_are_refusals(demo, monkeypatch):
    repo = demo.repo()
    config = config_bytes(repo)
    version = gitconf.git_version
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 30, 0))
    plan = api.plan_add(repo, ["demo/gate"])
    assert plan["refusal"]["code"] == "git-too-old" and plan["git_version"] == "2.30.0"
    demo.mp.setattr(gitconf, "git_version", version)
    api.index_path().parent.mkdir(parents=True, exist_ok=True)
    api.index_path().write_text("[1, 2]", encoding="utf-8")
    assert api.plan_add(repo, ["demo/gate"])["refusal"]["code"] == "index-unreadable"
    with pytest.raises(api.IndexUnreadable):
        api.add(repo, ["demo/gate"])
    api.index_path().unlink()

    def unreadable(where):
        raise gitconf.GitError(f"git config could not read {where.root}: bad")

    monkeypatch.setattr(gitconf, "read", unreadable)
    assert api.plan_add(repo, ["demo/gate"])["refusal"]["code"] == "config-unreadable"
    assert config_bytes(repo) == config
