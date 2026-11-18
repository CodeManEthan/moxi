"""What `enable` and `refresh` would write, read before they write (`api.plan_enable`,
`api.plan_refresh`), and each verb writing exactly that plan when it is given the plan's
digest."""

from __future__ import annotations

from pathlib import Path

import pytest

from moxi_repo import api, gitconf
from tests.conftest import POSIX_ONLY, git
from tests.test_commit_rules import write_config
from tests.test_plan_sync import changed, held, snapshot, touched  # noqa: F401 -- held is a fixture

pytestmark = POSIX_ONLY

TAG = r"\bTAG-[0-9]+\b"


def config_bytes(repo: Path) -> bytes:
    return (repo / ".git" / "config").read_bytes()


def acted(repo: Path, plan: dict, verb) -> api.Report:
    """Make the act with the plan's digest, and check it wrote exactly what the plan
    named."""
    assert plan["refusal"] is None, plan["refusal"]
    where = gitconf.resolve(repo)
    before = snapshot(where)
    report = verb(expect=plan["digest"])
    assert changed(before, snapshot(where)) == touched(plan["writes"])
    return report


# -- enable ---------------------------------------------------------------------------------

def test_turning_a_piece_on_plans_its_flag_and_its_hook_and_writes_them(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate", "demo/watch"], mode="shim")
    api.disable(repo, "demo/watch")
    plan = api.plan_enable(repo, "demo/watch")
    assert plan["pieces"] == [{"id": "demo/watch", "event": "post-commit", "new": False,
                               "implied_by": None, "seeds": []}]
    assert [(i["place"], i["key"], i["from"], i["to"]) for i in plan["writes"]] == [
        ("config", "moxi-repo.piece.demo/watch.enabled", "false", "true"),
        ("hooks", "shim", None, str(repo / ".git" / "hooks" / "post-commit"))]
    report = acted(repo, plan, lambda **kw: api.enable(repo, "demo/watch", **kw))
    assert report.lines[-1] == f"enabled demo/watch in {repo}" and not report.already
    config = config_bytes(repo)
    again = api.enable(repo, "demo/watch", expect=plan["digest"])
    assert again.already and again.lines == [] and config_bytes(repo) == config
    assert api.plan_enable(repo, "demo/watch")["writes"] == []


def test_enable_without_a_digest_reads_as_before(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="config")
    api.disable(repo, "demo/gate")
    assert api.enable(repo, "demo/gate").lines == [f"enabled demo/gate in {repo}"]
    # On a piece already on it writes nothing and says the same.
    config = config_bytes(repo)
    assert api.enable(repo, "demo/gate").lines == [f"enabled demo/gate in {repo}"]
    assert config_bytes(repo) == config


def test_a_mode_change_between_plan_and_turn_on_is_changed_and_writes_nothing(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate", "demo/watch"], mode="config")
    api.disable(repo, "demo/watch")
    plan = api.plan_enable(repo, "demo/watch")
    api.add(repo, mode="shim")
    config = config_bytes(repo)
    with pytest.raises(api.Changed) as caught:
        api.enable(repo, "demo/watch", expect=plan["digest"])
    assert config_bytes(repo) == config
    assert caught.value.plan == api.plan_enable(repo, "demo/watch")


def test_turning_on_a_piece_not_registered_is_refused_and_a_held_repo_is_busy(demo, held):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="config")
    plan = api.plan_enable(repo, "demo/watch")
    assert plan["refusal"]["code"] == "not-registered" and plan["writes"] == []
    with pytest.raises(api.Usage) as caught:
        api.enable(repo, "demo/watch")
    assert caught.value.reason == "not-registered"
    assert caught.value.text == plan["refusal"]["text"]
    held(repo)
    with pytest.raises(api.Busy) as caught:
        api.enable(repo, "demo/gate", expect=api.plan_enable(repo, "demo/gate")["digest"])
    assert caught.value.reason == "busy"


# -- refresh --------------------------------------------------------------------------------

def test_a_refresh_plans_what_was_changed_by_hand_back_and_writes_it(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate", "demo/lint"], mode="config")
    git(repo, "config", "moxi-repo.piece.demo/lint.fail", "closed")
    git(repo, "config", "--remove-section", "hook.moxi-repo-pre-commit")
    plan = api.plan_refresh(repo)
    assert plan["pieces"] == [{"id": "demo/lint", "event": "pre-commit", "new": False,
                               "implied_by": None, "seeds": []}]
    assert [(i["key"], i["from"], i["to"]) for i in plan["writes"]][0] == (
        "moxi-repo.piece.demo/lint.fail", "closed", "open")
    assert {i["key"] for i in plan["writes"][1:]} == {"hook.moxi-repo-pre-commit.command",
                                                    "hook.moxi-repo-pre-commit.event"}
    report = acted(repo, plan, lambda **kw: api.refresh(repo, **kw))
    assert report.lines == [f"refreshed {repo}"]
    again = api.refresh(repo, expect=plan["digest"])
    assert again.already and again.lines == []
    assert api.plan_refresh(repo)["writes"] == []


def test_a_refresh_plans_an_implied_piece_with_its_seed_and_a_mode_change(demo):
    repo = demo.repo()
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 56, 0))
    api.add(repo, ["demo/gate"])
    write_config(f"[commit_rules]\neverywhere = true\ntag_patterns = ['{TAG}']\n")
    demo.mp.setattr(gitconf, "git_version", lambda: (2, 43, 0))
    plan = api.plan_refresh(repo)
    assert plan["pieces"] == [{"id": api.COMMIT_RULES, "event": "commit-msg", "new": True,
                               "implied_by": "commit_rules.everywhere",
                               "seeds": ["tag-pattern"]}]
    assert plan["mode_change"] == {"from": "config", "to": "shim"}
    [seed] = [i for i in plan["writes"] if i["seed"]]
    assert seed["to"] == TAG
    report = acted(repo, plan, lambda **kw: api.refresh(repo, **kw))
    assert report.lines[0] == (f"added {api.COMMIT_RULES} (commit-msg, closed) in {repo} "
                               "(commit_rules.everywhere)")
    assert report.lines[1] == f"{repo}: config mode to shim (git 2.43.0)"
    assert report.lines[-1] == f"refreshed {repo}"


def test_a_settings_change_between_plan_and_refresh_is_changed_and_writes_nothing(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="config")
    plan = api.plan_refresh(repo)
    assert plan["writes"] == []
    write_config("[commit_rules]\neverywhere = true\n")
    config = config_bytes(repo)
    with pytest.raises(api.Changed):
        api.refresh(repo, expect=plan["digest"])
    assert config_bytes(repo) == config


def test_a_digest_names_one_repo_so_a_refresh_of_every_repo_refuses_one(demo):
    with pytest.raises(api.Usage):
        api.refresh(expect="0" * 64)


def test_a_git_failure_midway_raises_partial_with_a_digest_and_is_a_problem_without(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate", "demo/lint"], mode="config")
    for key in ("demo/gate.fail", "demo/lint.fail"):
        git(repo, "config", f"moxi-repo.piece.{key}", "open" if "gate" in key else "closed")
    put = gitconf._put_values
    calls: list[str] = []

    def failing(where, key, values):
        calls.append(key)
        if len(calls) == 2:
            raise gitconf.GitError(f"git config could not set {key}: no room")
        put(where, key, values)

    demo.mp.setattr(gitconf, "_put_values", failing)
    with pytest.raises(api.Failure) as caught:
        api.refresh(repo, expect=api.plan_refresh(repo)["digest"])
    assert caught.value.partial and "no room" in caught.value.text
    git(repo, "config", "moxi-repo.piece.demo/gate.fail", "open")
    calls.clear()
    report = api.refresh(repo)
    assert report.lines == [] and report.problems == [
        f"{repo}: git config could not set moxi-repo.piece.demo/lint.fail: no room"]
