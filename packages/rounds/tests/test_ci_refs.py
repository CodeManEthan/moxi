"""CI's own refs: their removal from the remote when a build finishes or is dropped,
under the keep rule, and the account of stale ones that `drop --leftovers --refs`
gives."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from moxi_rounds import (builds, checking, ci, ci_refs, commissioning, dropping,
                         listener, log, opening, queue_file, settings, verdicts)
from moxi_rounds import state as states
from moxi_rounds.cli import main

ROUND = "proj-a/a-round"
OTHER_ROUND = "proj-a/b-round"
PY = sys.executable
FLOW = ".github/workflows/ci.yml"
ARMED = "[sweep]\narmed = true\n"


@pytest.fixture
def repo(git_repo, tmp_path):
    from moxi import cli

    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    git_repo.git("config", "user.name", "coder")
    git_repo.git("config", "user.email", "coder@example.test")
    return git_repo


@pytest.fixture
def bare(repo, tmp_path) -> Path:
    found = tmp_path / "remote.git"
    repo.git("init", "-q", "--bare", str(found))
    repo.git("remote", "add", "origin", str(found))
    return found


def remote_refs(repo, bare: Path) -> list[str]:
    listed = repo.git("--git-dir", str(bare), "for-each-ref", "--format=%(refname)")
    return sorted(listed.split())


@pytest.fixture
def configure(write_config, tmp_path):
    """`configure(ref="ci/{branch}", full_ref=None, extra="")`: the config with proj-a's
    check and a `ci` that pushes to `origin` and reads no run."""
    ids = shlex.join([PY, "-c", f"print({json.dumps(json.dumps([{'id': 11, 'path': FLOW}]))})"])
    status = shlex.join([PY, "-c", "print('[]')"])

    def write(ref: str | None = "ci/{branch}", extra: str = "", *,
              full_ref: str | None = None) -> None:
        full = shlex.join([PY, "-c", "print('full ran')"])
        table = (f'workflows = ["{FLOW}"], local = "none", remote = "origin", '
                 f"status = {json.dumps(shlex.split(status))}, "
                 f"ids = {json.dumps(shlex.split(ids))}")
        if ref is not None:
            table += f", ref = {json.dumps(ref)}"
        if full_ref is not None:
            table += f", full_ref = {json.dumps(full_ref)}"
        write_config(f"{extra}[projects.proj-a]\ncheck = {{ command = {json.dumps(full)} }}\n"
                     f"ci = {{ {table} }}\n")

    write()
    return write


def open_with(name: str, tmp_path: Path, stages: dict[str, str]) -> Path:
    opening.open_round(name, description="a placeholder round", by="planner", flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / name.partition("/")[2])
    for stage, branch in stages.items():
        log.append(path, "stage.declared",
                   {"id": stage, "repo": "proj-a", "branch": branch, "after": []},
                   by="planner")
    return path


def commissioned(repo, stage: str, *, name: str = ROUND, work: bool = True):
    made = commissioning.commission(name, stage, builder="coder", by="planner")
    tree = type(repo)(made.worktree)
    if work:
        tree.commit(f"{stage}.txt", "the work")
    return tree


@pytest.fixture
def pushed(repo, bare, configure, tmp_path, capsys):
    """B1 on `work`, checked by CI so its tip went to `ci/work`. Returns `(log,
    worktree repo)`."""
    path = open_with(ROUND, tmp_path, {"B1": "work"})
    tree = commissioned(repo, "B1")
    checking.check(ROUND, "B1", by="coder")
    capsys.readouterr()
    return path, tree


# -- removal at the build's end ----------------------------------------------------------

def test_a_dropped_build_s_ci_ref_is_removed_and_its_branch_stays(pushed, repo, bare,
                                                                  capsys):
    _path, tree = pushed
    tip = tree.rev()
    assert remote_refs(repo, bare) == ["refs/heads/ci/work"]

    assert main(["drop", ROUND, "B1", "--by", "operator", "--record", "comms:1234567"]) == 0

    assert remote_refs(repo, bare) == []
    assert "ref removed origin ci/work" in capsys.readouterr().out
    assert repo.rev("work") == tip


def test_a_dropped_round_removes_the_ci_ref_of_its_build_and_no_other_round_s(
        pushed, repo, bare, tmp_path):
    open_with(OTHER_ROUND, tmp_path, {"B1": "other"})
    commissioned(repo, "B1", name=OTHER_ROUND)
    checking.check(OTHER_ROUND, "B1", by="coder")
    assert remote_refs(repo, bare) == ["refs/heads/ci/other", "refs/heads/ci/work"]

    done = dropping.drop(ROUND, by="operator", record="comms:1234567", run_by="lead")

    assert [(one.ref, one.fate) for one in done.refs] == [("ci/work", ci_refs.REMOVED)]
    assert remote_refs(repo, bare) == ["refs/heads/ci/other"]


def verify(path: Path, head: str) -> None:
    report = path.parent / "build-report.md"
    report.write_text("# report\n\n```toml rounds\n" + "\n".join([
        'kind = "build"', 'verdict = "publishable"', 'pass = "full"', 'by = "critic"',
        'model = "a-model"', 'stage = "B1"', f'head = "{head}"']) + "\n```\n",
        encoding="utf-8")
    verdicts.record(ROUND, report, by="critic")


def test_a_finished_build_s_ci_ref_is_removed_after_the_finish(pushed, repo, bare,
                                                               capsys):
    path, tree = pushed
    checking.check(ROUND, "B1", by="coder", full=True)
    verify(path, tree.rev())
    capsys.readouterr()

    assert main(["finish", ROUND, "B1"]) == 0

    assert "ref removed origin ci/work" in capsys.readouterr().out
    assert remote_refs(repo, bare) == []
    assert "finish.done" in [one["type"] for one in log.read_round(path)]


def test_a_ref_pushed_under_an_older_setting_is_removed_with_the_one_given_now(
        pushed, repo, bare, configure):
    configure(ref="ci-new/{branch}")
    repo.git("push", "-q", "origin", "work:refs/heads/ci-new/work")

    done = dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567",
                               run_by="lead")

    assert [(one.ref, one.fate) for one in done.refs] == [
        ("ci/work", ci_refs.REMOVED), ("ci-new/work", ci_refs.REMOVED)]
    assert remote_refs(repo, bare) == []


def test_a_push_that_went_out_unrecorded_is_removed_and_one_not_there_reads_absent(
        repo, bare, configure, tmp_path):
    open_with(ROUND, tmp_path, {"B1": "work"})
    tree = commissioned(repo, "B1")
    repo.git("push", "-q", "origin", f"{tree.rev()}:refs/heads/ci/work")

    done = dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567",
                               run_by="lead")

    assert [(one.ref, one.fate) for one in done.refs] == [("ci/work", ci_refs.REMOVED)]
    open_with(OTHER_ROUND, tmp_path, {"B1": "gone"})
    commissioned(repo, "B1", name=OTHER_ROUND)
    done = dropping.drop_build(OTHER_ROUND, "B1", by="operator", record="comms:1234567",
                               run_by="lead")
    assert [(one.ref, one.fate) for one in done.refs] == [("ci/gone", ci_refs.ABSENT)]


def test_with_no_ref_now_only_the_recorded_ref_is_removed(pushed, repo, bare, configure):
    configure(ref=None)
    repo.git("push", "-q", "origin", "work:refs/heads/work")

    done = dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567",
                               run_by="lead")

    assert [(one.ref, one.fate) for one in done.refs] == [("ci/work", ci_refs.REMOVED)]
    assert remote_refs(repo, bare) == ["refs/heads/work"]


def test_a_removal_that_fails_leaves_the_drop_standing_and_names_the_way_on(
        pushed, repo, bare, tmp_path):
    path, _tree = pushed
    repo.git("remote", "set-url", "origin", str(tmp_path / "nowhere.git"))

    done = dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567",
                               run_by="lead")

    [ref] = done.refs
    assert (ref.ref, ref.fate) == ("ci/work", ci_refs.LEFT)
    assert ci_refs.REMOVE in ref.why
    assert log.read_round(path)[-1]["type"] == "build.dropped"


def test_a_guard_that_refuses_the_deletion_leaves_the_ref(pushed, repo, bare):
    hooks = Path(repo.git("rev-parse", "--git-common-dir"))
    hooks = (hooks if hooks.is_absolute() else repo.root / hooks) / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-push").write_text("#!/bin/sh\necho 'no word' >&2\nexit 1\n")
    (hooks / "pre-push").chmod(0o755)

    done = dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567",
                               run_by="lead")

    [ref] = done.refs
    assert ref.fate == ci_refs.LEFT and "no word" in ref.why
    assert remote_refs(repo, bare) == ["refs/heads/ci/work"]


def test_a_ref_a_new_open_build_on_the_branch_owns_is_kept(pushed, repo, bare, tmp_path):
    dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567", run_by="lead")
    open_with(OTHER_ROUND, tmp_path, {"B1": "work"})
    commissioning.commission(OTHER_ROUND, "B1", builder="coder", by="planner",
                             existing=True)
    repo.git("push", "-q", "origin", "work:refs/heads/ci/work")
    folded = states.fold(log.read_round(pushed[0]))

    # A removal run again for the dropped build, as a stopped drop's rerun would.
    refs = ci_refs.remove(folded, builds.current(folded, "B1"), settings.load())

    [ref] = refs
    assert ref.fate == ci_refs.KEPT and f"{OTHER_ROUND} B1" in ref.why
    assert remote_refs(repo, bare) == ["refs/heads/ci/work"]


def test_a_ref_under_the_prefix_it_was_pushed_under_is_kept_for_a_new_open_build(
        pushed, repo, bare, tmp_path, configure):
    dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567", run_by="lead")
    open_with(OTHER_ROUND, tmp_path, {"B1": "work"})
    commissioning.commission(OTHER_ROUND, "B1", builder="coder", by="planner",
                             existing=True)
    repo.git("push", "-q", "origin", "work:refs/heads/ci/work")
    configure(ref="ci-new/{branch}")
    folded = states.fold(log.read_round(pushed[0]))

    refs = ci_refs.remove(folded, builds.current(folded, "B1"), settings.load())

    assert [(one.ref, one.fate) for one in refs] == [
        ("ci/work", ci_refs.KEPT), ("ci-new/work", ci_refs.ABSENT)]
    assert f"{OTHER_ROUND} B1" in refs[0].why
    assert remote_refs(repo, bare) == ["refs/heads/ci/work"]


def test_every_ref_is_kept_while_a_round_s_log_can_t_be_read(pushed, repo, bare,
                                                              tmp_path):
    other = open_with(OTHER_ROUND, tmp_path, {})
    with other.open("a", encoding="utf-8") as fh:
        fh.write("not an event\n")

    done = dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567",
                               run_by="lead")

    [ref] = done.refs
    assert ref.fate == ci_refs.KEPT and "can't be read" in ref.why
    assert remote_refs(repo, bare) == ["refs/heads/ci/work"]


# -- the account of stale refs -----------------------------------------------------------

def stale_and_owned(repo) -> None:
    """On the remote: ci/work (B1's, open), ci/main (the target's), ci/stale and
    ci/a/b (no build's), and other (not under the prefix)."""
    for ref in ("ci/main", "ci/stale", "ci/a/b", "other"):
        repo.git("push", "-q", "origin", f"main:refs/heads/{ref}")


def test_the_account_keeps_owned_refs_and_names_the_rest(pushed, repo, bare, tmp_path,
                                                         capsys):
    stale_and_owned(repo)

    assert main(["drop", "--leftovers", "--refs"]) == 0

    said = capsys.readouterr().out
    assert "CI's own refs on the remotes (dry run: sweep.armed is false)" in said
    assert f"ref kept origin ci/work: a build that has not ended pushed it: {ROUND} B1" in said
    assert "ref kept origin ci/main: it is the ref of the target main" in said
    assert "ref would go origin ci/stale" in said and "ref would go origin ci/a/b" in said
    assert " other" not in said
    assert f"then: set armed = true under [sweep] in rounds' config, then: {ci_refs.REMOVE}" in said
    assert len(remote_refs(repo, bare)) == 5


def test_an_armed_sweep_with_refs_removes_the_stale_ones(pushed, repo, bare, tmp_path,
                                                         configure, capsys):
    configure(extra=ARMED)
    stale_and_owned(repo)

    assert main(["drop", "--leftovers", "--refs", "--remove", "--json"]) == 0

    found = json.loads(capsys.readouterr().out)["refs"]
    assert sorted((one["ref"], one["fate"]) for one in found["refs"]) == [
        ("ci/a/b", ci_refs.REMOVED), ("ci/main", ci_refs.KEPT),
        ("ci/stale", ci_refs.REMOVED), ("ci/work", ci_refs.KEPT)]
    assert remote_refs(repo, bare) == ["refs/heads/ci/main", "refs/heads/ci/work",
                                       "refs/heads/other"]


def test_an_unarmed_sweep_with_refs_and_remove_removes_nothing(pushed, repo, bare,
                                                               capsys):
    stale_and_owned(repo)

    assert main(["drop", "--leftovers", "--refs", "--remove"]) != 0

    assert "ref would go origin ci/stale" in capsys.readouterr().out
    assert len(remote_refs(repo, bare)) == 5


def batch_built(batch_id: str) -> None:
    queue_file.append("proj-a", {"event": queue_file.BATCH, "state": queue_file.BUILT,
                                 "id": batch_id, "n": 1, "main": "0" * 40,
                                 "branch": queue_file.BRANCH_PREFIX + batch_id,
                                 "members": [], "target": "main"})


def test_a_running_candidate_s_ref_is_kept_and_an_ended_one_s_is_swept(
        pushed, repo, bare, configure, capsys):
    configure(extra=ARMED)
    batch_built("aaaaaaaaaaaa-1")
    batch_built("bbbbbbbbbbbb-1")
    queue_file.append("proj-a", {"event": queue_file.BATCH, "state": queue_file.ENDED,
                                 "id": "bbbbbbbbbbbb-1", "why": "landed"})
    for ref in ("ci/queue-aaaaaaaaaaaa-1", "ci/queue-bbbbbbbbbbbb-1"):
        repo.git("push", "-q", "origin", f"main:refs/heads/{ref}")
    capsys.readouterr()

    assert main(["drop", "--leftovers", "--refs", "--remove", "--json"]) == 0

    found = {one["ref"]: (one["fate"], one["why"])
             for one in json.loads(capsys.readouterr().out)["refs"]["refs"]}
    assert found["ci/queue-aaaaaaaaaaaa-1"] == (
        ci_refs.KEPT, "it is the ref of a candidate the queue runs: proj-a's batch "
        "aaaaaaaaaaaa-1")
    assert found["ci/queue-bbbbbbbbbbbb-1"][0] == ci_refs.REMOVED
    assert remote_refs(repo, bare) == ["refs/heads/ci/queue-aaaaaaaaaaaa-1",
                                       "refs/heads/ci/work"]


def test_while_the_queue_file_can_t_be_read_only_candidates_refs_are_kept(repo, configure):
    path = queue_file.queue_file("proj-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"event": "batch", "state": "bui', encoding="utf-8")

    rule = ci_refs.keep("proj-a", settings.load())

    why = rule.why("ci/queue-cccccccccccc-2")
    assert why is not None and "queue file of proj-a can't be read" in why
    assert rule.why("ci/stale") is None


def test_a_branch_ls_remote_lists_by_its_tail_alone_is_never_swept(pushed, repo, bare,
                                                                  configure, capsys):
    configure(extra=ARMED)
    repo.git("push", "-q", "origin", "main:refs/heads/x/refs/heads/ci/y")
    capsys.readouterr()

    assert main(["drop", "--leftovers", "--refs", "--remove", "--json"]) == 0

    found = json.loads(capsys.readouterr().out)["refs"]["refs"]
    assert [one["ref"] for one in found] == ["ci/work"]
    assert remote_refs(repo, bare) == ["refs/heads/ci/work",
                                       "refs/heads/x/refs/heads/ci/y"]


def test_with_no_into_and_no_open_build_the_target_s_ref_is_kept(pushed, repo, bare,
                                                                 configure, capsys):
    dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567", run_by="lead")
    configure(extra=ARMED)
    repo.git("push", "-q", "origin", "main:refs/heads/ci/main")
    capsys.readouterr()

    assert main(["drop", "--leftovers", "--refs", "--remove", "--json"]) == 0

    [ref] = json.loads(capsys.readouterr().out)["refs"]["refs"]
    assert (ref["ref"], ref["fate"]) == ("ci/main", ci_refs.KEPT)
    assert "the target main" in ref["why"]
    assert remote_refs(repo, bare) == ["refs/heads/ci/main"]


def test_every_ref_is_kept_while_the_target_can_t_be_read(repo, configure, monkeypatch):
    from moxi_rounds import gitreads

    def fails(self):
        raise gitreads.GitError(["git"], "exited 128")

    monkeypatch.setattr(gitreads.Git, "checked_out", fails)

    why = ci_refs.keep("proj-a", settings.load()).why("ci/anything")

    assert why is not None and "target can't be read" in why


def test_a_sweep_without_refs_reaches_no_remote(pushed, repo, bare, tmp_path, configure,
                                                capsys):
    configure(extra=ARMED)
    stale_and_owned(repo)

    assert main(["drop", "--leftovers", "--remove", "--json"]) == 0

    assert "refs" not in json.loads(capsys.readouterr().out)
    assert len(remote_refs(repo, bare)) == 5


def test_refs_is_refused_without_leftovers(capsys):
    assert main(["drop", ROUND, "--by", "lead", "--record", "comms:1234567",
                 "--refs"]) != 0

    assert "--refs" in capsys.readouterr().err


def test_the_session_start_sweep_never_reaches_a_remote():
    assert "--refs" not in listener.ARGV


# -- the second template ----------------------------------------------------------------

FULL = "ci-full/{branch}"


def test_a_build_s_end_removes_the_name_full_ref_gives_its_branch_and_nothing_else(
        pushed, repo, bare, configure, capsys):
    configure(full_ref=FULL)
    _path, tree = pushed
    repo.git("push", "-q", "origin", f"{tree.rev()}:refs/heads/ci-full/work")
    for ref in ("refs/heads/main", "refs/heads/ci/main", "refs/heads/ci-full/main",
                "refs/heads/other", "refs/tags/ci-full/work"):
        repo.git("push", "-q", "origin", f"main:{ref}")
    capsys.readouterr()

    assert main(["drop", ROUND, "B1", "--by", "operator", "--record", "comms:1234567"]) == 0

    said = capsys.readouterr().out
    assert "ref removed origin ci/work" in said and "ref removed origin ci-full/work" in said
    assert remote_refs(repo, bare) == [
        "refs/heads/ci-full/main", "refs/heads/ci/main", "refs/heads/main",
        "refs/heads/other", "refs/tags/ci-full/work"]


def test_a_finished_build_s_ci_full_ref_goes_too_and_one_never_pushed_reads_absent(
        pushed, repo, bare, configure, capsys):
    path, tree = pushed
    configure(full_ref=FULL)
    checking.check(ROUND, "B1", by="coder", full=True)
    verify(path, tree.rev())
    assert remote_refs(repo, bare) == ["refs/heads/ci/work"]
    capsys.readouterr()

    assert main(["finish", ROUND, "B1"]) == 0

    said = capsys.readouterr().out
    assert "ref removed origin ci/work" in said
    assert "ref absent origin ci-full/work" in said
    assert remote_refs(repo, bare) == []


def test_the_name_full_ref_gives_a_new_open_build_s_branch_is_kept(pushed, repo, bare,
                                                                   tmp_path, configure):
    configure(full_ref=FULL)
    dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567", run_by="lead")
    open_with(OTHER_ROUND, tmp_path, {"B1": "work"})
    commissioning.commission(OTHER_ROUND, "B1", builder="coder", by="planner",
                             existing=True)
    repo.git("push", "-q", "origin", "work:refs/heads/ci-full/work")
    folded = states.fold(log.read_round(pushed[0]))

    refs = ci_refs.remove(folded, builds.current(folded, "B1"), settings.load())

    assert [(one.ref, one.fate) for one in refs] == [
        ("ci/work", ci_refs.ABSENT), ("ci-full/work", ci_refs.KEPT)]
    assert f"{OTHER_ROUND} B1" in refs[1].why
    assert remote_refs(repo, bare) == ["refs/heads/ci-full/work"]


def test_with_no_into_and_no_open_build_both_templates_keep_the_target_s_ref(
        pushed, repo, bare, configure, capsys):
    dropping.drop_build(ROUND, "B1", by="operator", record="comms:1234567", run_by="lead")
    configure(extra=ARMED, full_ref=FULL)
    for ref in ("ci/main", "ci-full/main"):
        repo.git("push", "-q", "origin", f"main:refs/heads/{ref}")
    capsys.readouterr()

    assert main(["drop", "--leftovers", "--refs", "--remove", "--json"]) == 0

    found = json.loads(capsys.readouterr().out)["refs"]["refs"]
    assert sorted((one["ref"], one["fate"]) for one in found) == [
        ("ci-full/main", ci_refs.KEPT), ("ci/main", ci_refs.KEPT)]
    assert all("the target main" in one["why"] for one in found)
    assert remote_refs(repo, bare) == ["refs/heads/ci-full/main", "refs/heads/ci/main"]


def both_prefixes(repo) -> None:
    """On the remote, beside B1's ci/work: under each prefix the target's ref, a stale
    ref and a nested stale one; ci-full/work for B1; and refs under neither prefix: a
    look-alike prefix, two whose tail holds one, main, and a tag under one."""
    for ref in ("heads/ci/main", "heads/ci/stale", "heads/ci-full/main",
                "heads/ci-full/stale", "heads/ci-full/a/b", "heads/ci-full/work",
                "heads/ci-fullx/y", "heads/x/ci-full/y", "heads/x/refs/heads/ci-full/y",
                "heads/other", "heads/main", "tags/ci-full/stale"):
        repo.git("push", "-q", "origin", f"main:refs/{ref}")


def test_the_sweep_lists_both_prefixes_and_judges_each_by_the_keep_rule(
        pushed, repo, bare, configure, capsys):
    configure(full_ref=FULL)
    both_prefixes(repo)

    assert main(["drop", "--leftovers", "--refs"]) == 0

    said = capsys.readouterr().out
    assert "ref kept origin ci-full/main: it is the ref of the target main" in said
    assert ("ref kept origin ci-full/work: it is the ref of a build that has not ended: "
            f"{ROUND} B1") in said
    for ref in ("ci/stale", "ci-full/stale", "ci-full/a/b"):
        assert f"ref would go origin {ref}\n" in said
    assert "ci-fullx" not in said and "x/ci-full" not in said and " other" not in said
    assert len(remote_refs(repo, bare)) == 13


def test_an_armed_sweep_removes_stale_refs_under_both_prefixes_and_nothing_else(
        pushed, repo, bare, configure, capsys):
    configure(extra=ARMED, full_ref=FULL)
    both_prefixes(repo)

    assert main(["drop", "--leftovers", "--refs", "--remove", "--json"]) == 0

    found = json.loads(capsys.readouterr().out)["refs"]
    assert sorted((one["ref"], one["fate"]) for one in found["refs"]) == [
        ("ci-full/a/b", ci_refs.REMOVED), ("ci-full/main", ci_refs.KEPT),
        ("ci-full/stale", ci_refs.REMOVED), ("ci-full/work", ci_refs.KEPT),
        ("ci/main", ci_refs.KEPT), ("ci/stale", ci_refs.REMOVED),
        ("ci/work", ci_refs.KEPT)]
    assert remote_refs(repo, bare) == [
        "refs/heads/ci-full/main", "refs/heads/ci-full/work", "refs/heads/ci-fullx/y",
        "refs/heads/ci/main", "refs/heads/ci/work", "refs/heads/main",
        "refs/heads/other", "refs/heads/x/ci-full/y", "refs/heads/x/refs/heads/ci-full/y",
        "refs/tags/ci-full/stale"]


def test_with_no_full_ref_the_sweep_lists_only_ref_s_prefix(pushed, repo, bare,
                                                             configure, capsys):
    configure(extra=ARMED)
    both_prefixes(repo)

    assert main(["drop", "--leftovers", "--refs", "--remove", "--json"]) == 0

    found = json.loads(capsys.readouterr().out)["refs"]["refs"]
    assert sorted(one["ref"] for one in found) == ["ci/main", "ci/stale", "ci/work"]
    assert "refs/heads/ci-full/stale" in remote_refs(repo, bare)


def test_an_unreachable_remote_is_named_and_not_swept(pushed, repo, tmp_path, capsys):
    repo.git("remote", "set-url", "origin", str(tmp_path / "nowhere.git"))

    found = ci_refs.account(settings.load(), removing=False)

    assert found.refs == () and "refs of proj-a not listed from origin" in found.unlisted[0]
    assert ci.ref_for("ci/{branch}", "x") == "ci/x"
