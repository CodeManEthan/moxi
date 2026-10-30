"""The committer: one pass at a time, named paths only, what it leaves out and why, the
prepare step, the paths it hands git, git hooks on its commits, and the push part."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import moxi_repo
from moxi import files
from moxi_repo import api, cli, committer, gitrun
from tests.conftest import POSIX_ONLY, git, head

pytestmark = POSIX_ONLY

AUTHOR = ("snap-bot", "snap@example.invalid")


def spec(repo: Path, **kw) -> moxi_repo.Spec:
    kw.setdefault("name", "demo/snap")
    kw.setdefault("paths", ("notes",))
    kw.setdefault("author", AUTHOR)
    return moxi_repo.Spec(root=repo, **kw)


def write(repo: Path, name: str, text: str = "a line\n") -> Path:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def staged(repo: Path) -> list[str]:
    out = git(repo, "diff", "--cached", "--name-only", "-z").stdout
    return sorted(x for x in out.split("\0") if x)


def in_head(repo: Path) -> list[str]:
    out = git(repo, "ls-tree", "-r", "--name-only", "-z", "HEAD").stdout
    return sorted(x for x in out.split("\0") if x)


def last_files(repo: Path) -> list[str]:
    out = git(repo, "show", "--name-only", "-z", "--format=", "HEAD").stdout
    return sorted(x for x in out.split("\0") if x.strip("\n"))


@pytest.fixture
def clock(monkeypatch):
    """The committer's clock, which a test moves on by hand."""
    now = [time.time()]
    monkeypatch.setattr(committer, "_now", lambda: now[0])
    return now


# -- the git wrapper ------------------------------------------------------------------------

WRAPPER = '''#!{python} -I
import json, os, subprocess, sys
REAL = {real!r}
argv = sys.argv[1:]
data = None
if "--pathspec-from-file=-" in argv:
    data = sys.stdin.buffer.read()
names = ("GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS",
         "GIT_ICASE_PATHSPECS")
record = {{"argv": argv,
          "stdin": None if data is None else [os.fsdecode(x) for x in data.split(b"\\0") if x],
          "env": {{k: v for k, v in os.environ.items() if k in names}},
          "committer": os.environ.get("MOXI_REPO_COMMITTER"),
          "hook": "GIT_INDEX_FILE" in os.environ or "GIT_EXEC_PATH" in os.environ}}
with open(os.environ["GITWRAP_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(record) + "\\n")
if data is None:
    os.execv(REAL, [REAL, *argv])
sys.exit(subprocess.run([REAL, *argv], input=data).returncode)
'''

GLOBAL_OPTIONS = ("--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
                  "--icase-pathspecs")


@pytest.fixture
def calls(demo, tmp_path, monkeypatch):
    """Put a git first on PATH that logs each call's argv, stdin and pathspec variables;
    return a function that reads (and empties) the log."""
    real = shutil.which("git")
    bin_dir = tmp_path / "wrap"
    bin_dir.mkdir()
    wrapper = bin_dir / "git"
    wrapper.write_text(WRAPPER.format(python=sys.executable, real=real), encoding="utf-8")
    wrapper.chmod(0o755)
    log = tmp_path / "git-calls.log"
    monkeypatch.setenv("GITWRAP_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(gitrun, "_local_vars", None)

    def read() -> list[dict]:
        if not log.exists():
            return []
        out = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x]
        log.unlink()
        return out

    return read


def paths_handed(call: dict) -> list[str]:
    """Every path a call hands git: argv after `--`, and stdin entries."""
    argv = call["argv"]
    out = list(argv[argv.index("--") + 1:]) if "--" in argv else []
    return out + list(call["stdin"] or [])


# -- one at a time ----------------------------------------------------------------------------

STORM = 8
CHILD = """
import json, pathlib, sys
import moxi_repo
root, n, wait = pathlib.Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])
(root / "notes" / ("storm-%s.md" % n)).write_text("# storm %s\\n" % n)
result = moxi_repo.commit(moxi_repo.Spec(
    name="demo/storm", root=root, paths=("notes",),
    author=("snap-bot", "snap@example.invalid")), wait=wait)
print(json.dumps(result.as_dict()))
raise SystemExit(result.exit_code)
"""


@pytest.mark.parametrize("wait", [20, 0])
def test_eight_parallel_passes_each_land_or_read_busy_and_none_fails(demo, wait):
    repo = demo.repo()
    (repo / "notes").mkdir()
    procs = [subprocess.Popen([sys.executable, "-c", CHILD, str(repo), str(n), str(wait)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for n in range(STORM)]
    results = []
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        results.append((proc.returncode, out, err))
    assert all(code == 0 for code, _, _ in results), results
    blob = "\n".join(out + err for _, out, err in results)
    for poison in ("index.lock", "Traceback", "fatal:"):
        assert poison not in blob, blob
    statuses = [json.loads(out)["status"] for _, out, _ in results]
    allowed = {"committed", "clean"} if wait else {"committed", "clean", "busy"}
    assert set(statuses) <= allowed, statuses
    if not wait:
        # The holder commits what the busy ones wrote, or the next pass does.
        moxi_repo.commit(spec(repo, name="demo/storm"))
    assert all(f"notes/storm-{n}.md" in in_head(repo) for n in range(STORM))
    assert staged(repo) == []
    for sha in git(repo, "rev-list", "HEAD").stdout.split():
        assert git(repo, "show", "--name-only", "--format=", sha).stdout.strip()


def test_a_held_lock_is_busy_and_wait_takes_it_once_freed(demo):
    repo = demo.repo()
    write(repo, "notes/a.md")
    lock = repo / ".git" / "moxi-repo" / "lock"
    with files.lock(lock):
        result = moxi_repo.commit(spec(repo))
    assert result.status == "busy" and result.exit_code == 0
    assert staged(repo) == [] and "notes/a.md" not in in_head(repo)
    assert moxi_repo.commit(spec(repo), wait=5).status == "committed"


# -- what a pass takes ---------------------------------------------------------------------------

def test_cadence_reads_only_this_authors_commits(demo):
    repo = demo.repo()
    write(repo, "notes/a.md")
    assert moxi_repo.commit(spec(repo, min_interval=3600)).status == "committed"
    write(repo, "notes/b.md")
    result = moxi_repo.commit(spec(repo, min_interval=3600))
    assert result.status == "throttled" and "interval is 3600 s" in result.detail
    # An author whose name is a prefix of that one, or whose email differs only where a
    # regex dot would match, has no commit yet.
    other = moxi_repo.commit(spec(repo, min_interval=3600, author=("snap", "snap@example.invalid")))
    assert other.status == "committed"
    write(repo, "notes/c.md")
    dotted = moxi_repo.commit(spec(repo, min_interval=3600,
                                   author=("snap-bot", "snap@exampleXinvalid")))
    assert dotted.status == "committed"


def test_named_paths_only(demo):
    repo = demo.repo()
    write(repo, "notes/a.md")
    write(repo, "other.txt")
    result = moxi_repo.commit(spec(repo))
    assert result.status == "committed" and result.files == ("notes/a.md",)
    assert git(repo, "status", "--porcelain").stdout == "?? other.txt\n"
    assert git(repo, "log", "-1", "--format=%an <%ae>|%cn <%ce>|%s").stdout.strip() == (
        "snap-bot <snap@example.invalid>|snap-bot <snap@example.invalid>|demo/snap: 1 path")


def test_a_path_someone_else_staged_waits_and_is_named(demo):
    repo = demo.repo()
    write(repo, "notes/x.md")
    moxi_repo.commit(spec(repo))
    write(repo, "notes/x.md", "someone's work in progress\n")
    git(repo, "add", "notes/x.md")
    write(repo, "notes/y.md")
    result = moxi_repo.commit(spec(repo))
    assert result.status == "committed" and result.files == ("notes/y.md",)
    assert result.left == (("notes/x.md", "staged by someone else"),)
    assert staged(repo) == ["notes/x.md"]
    assert "left out 1: notes/x.md (staged by someone else)" in result.line()


def test_an_oversize_file_and_a_nested_repo_are_left_out(demo):
    repo = demo.repo()
    write(repo, "notes/big.md", "x" * 50)
    write(repo, "notes/small.md", "x\n")
    git(repo / "notes", "init", "-q", "inner")
    write(repo, "notes/inner/f.txt")
    result = moxi_repo.commit(spec(repo, max_bytes=10))
    assert result.status == "committed" and result.files == ("notes/small.md",)
    assert dict(result.left) == {"notes/big.md": "over 10 bytes",
                                 "notes/inner/": "a nested repository"}


def test_a_first_commit_on_an_unborn_branch(demo):
    repo = demo.repo(commit=False)
    write(repo, "notes/a.md")
    result = moxi_repo.commit(spec(repo))
    assert result.status == "committed" and result.files == ("notes/a.md",)
    assert in_head(repo) == ["notes/a.md"]


def test_trailers_are_written_and_parse_as_trailers(demo):
    repo = demo.repo()
    write(repo, "notes/a.md")
    write(repo, "notes/b.md")
    result = moxi_repo.commit(spec(repo, trailers="demo_pieces.committing:trailers",
                                   message="a snapshot of the notes"))
    assert result.status == "committed"
    got = git(repo, "log", "-1", "--format=%s|%(trailers:key=Demo-Paths,valueonly)"
              "|%(trailers:key=Demo-Since,valueonly)").stdout
    assert got.rstrip("\n").split("|") == ["a snapshot of the notes", "2\n", "no"]


# -- prepare --------------------------------------------------------------------------------------

def test_prepare_sees_our_changes_with_their_blobs_and_drops_a_plain_path(demo, monkeypatch):
    repo = demo.repo()
    write(repo, "notes/keep.md")
    write(repo, "notes/key.txt", "a secret\n")
    log = demo.tmp / "prepare.log"
    monkeypatch.setenv("DEMO_PREPARE_LOG", str(log))
    monkeypatch.setenv("DEMO_DROP", "notes/key.txt")
    result = moxi_repo.commit(spec(repo, prepare="demo_pieces.committing:prepare"))
    assert result.status == "committed" and result.files == ("notes/keep.md",)
    assert result.left == (("notes/key.txt", "dropped: demo says so"),)
    assert "left out 1: notes/key.txt (dropped: demo says so)" in result.line()
    [seen] = [json.loads(x) for x in log.read_text().splitlines()]
    blob = git(repo, "hash-object", "notes/keep.md").stdout.strip()
    assert ["A", "notes/keep.md", None, blob] in seen["changes"]
    assert seen["spec"] == "demo/snap" and seen["root"] == str(repo)
    assert staged(repo) == []
    assert git(repo, "status", "--porcelain").stdout == "?? notes/key.txt\n"


def test_a_dropped_rename_is_dropped_whole(demo):
    repo = demo.repo()
    write(repo, "notes/old.md", "the same text, long enough to pair as a rename\n")
    moxi_repo.commit(spec(repo))
    (repo / "notes/old.md").rename(repo / "notes/new.md")
    seen = []

    def prepare(ctx):
        seen.extend(ctx.changes)
        return moxi_repo.Prepared(drop=[("notes/new.md", "no")])

    result = moxi_repo.commit(spec(repo, prepare=prepare))
    assert [(c.status, c.path, c.old_path) for c in seen] == [
        ("R", "notes/new.md", "notes/old.md")]
    assert result.status == "clean"
    assert dict(result.left) == {"notes/new.md": "dropped: no",
                                 "notes/old.md": "dropped with its rename to notes/new.md: no"}
    assert staged(repo) == [] and "notes/old.md" in in_head(repo)


def test_dropping_an_added_file_holds_back_every_deletion(demo):
    repo = demo.repo()
    write(repo, "notes/gone.md", "one text\n")
    write(repo, "notes/edit.md", "two\n")
    moxi_repo.commit(spec(repo))
    (repo / "notes/gone.md").unlink()
    write(repo, "notes/edit.md", "two, edited\n")
    write(repo, "notes/fresh.md", "an unrelated new file\n")
    result = moxi_repo.commit(spec(repo, prepare=lambda ctx: moxi_repo.Prepared(
        drop=[("notes/fresh.md", "no")])))
    assert result.status == "committed" and result.files == ("notes/edit.md",)
    assert dict(result.left) == {"notes/fresh.md": "dropped: no",
                                 "notes/gone.md": "held back: an added file was dropped"}
    assert "notes/gone.md" in in_head(repo) and staged(repo) == []


def test_a_drop_that_does_not_take_is_an_error(demo, monkeypatch):
    repo = demo.repo()
    write(repo, "notes/a.md")
    write(repo, "notes/b.md")
    real = committer._g

    def no_reset(p, *args, **kw):
        if args[0] == "reset":
            return subprocess.CompletedProcess(args, 0, "", "")
        return real(p, *args, **kw)

    monkeypatch.setattr(committer, "_g", no_reset)
    result = moxi_repo.commit(spec(repo, prepare=lambda ctx: moxi_repo.Prepared(
        drop=[("notes/a.md", "no")])))
    assert result.status == "error" and "notes/a.md" in result.detail
    assert result.exit_code == 1


def test_a_prepare_that_refuses_or_raises_unstages_everything(demo, monkeypatch):
    repo = demo.repo()
    write(repo, "notes/a.md")
    for action, status in (("refuse", "refused"), ("raise", "error")):
        monkeypatch.setenv("DEMO_PREPARE", action)
        result = moxi_repo.commit(spec(repo, prepare="demo_pieces.committing:prepare"))
        assert result.status == status, result
        assert staged(repo) == []
        if action == "refuse":
            assert result.detail == "prepare refused: demo prepare says no"
    monkeypatch.delenv("DEMO_PREPARE")
    bad = moxi_repo.commit(spec(repo, prepare="demo_pieces.nowhere:prepare"))
    assert bad.status == "error" and "can't be loaded" in bad.detail
    assert staged(repo) == []


# -- git hooks on the committer's commits ------------------------------------------------------

def test_a_refusing_pre_commit_piece_gives_refused_and_a_clean_index(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"])
    demo.act(gate="refuse")
    write(repo, "notes/a.md")
    result = moxi_repo.commit(spec(repo))
    assert result.status == "refused" and result.exit_code == 3
    assert any("gate says no" in x for x in result.hook_lines)
    assert "gate says no" in result.line()
    assert staged(repo) == [] and "notes/a.md" not in in_head(repo)
    [run] = demo.runs()
    assert run["committer"] == "demo/snap"


def test_lines_a_piece_prints_on_ok_reach_the_result(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"])
    demo.act(gate="print")
    write(repo, "notes/a.md")
    result = moxi_repo.commit(spec(repo))
    assert result.status == "committed"
    assert "gate printed this" in result.hook_lines


def test_glob_pathspecs_in_a_piece_and_an_existing_hook_see_every_staged_md(demo, monkeypatch):
    repo = demo.repo()
    api.add(repo, ["demo/gate"])
    demo.act(gate="glob")
    out = demo.tmp / "hook-saw.txt"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ngit diff --cached --name-only -- '*.md' > '{out}'\n"
                    f"env | grep PATHSPECS >> '{out}' || true\n", encoding="utf-8")
    hook.chmod(0o755)
    monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    monkeypatch.setenv("GIT_ICASE_PATHSPECS", "1")
    for name in ("notes/one.md", "notes/two[x].md", "notes/other.txt"):
        write(repo, name)
    result = moxi_repo.commit(spec(repo))
    assert result.status == "committed", result
    [run] = demo.runs()
    assert run["glob"] == ["notes/one.md", "notes/two[x].md"]
    assert out.read_text().split("\n") == ["notes/one.md", "notes/two[x].md", ""]


def test_what_prepare_did_not_judge_is_named_committed_unjudged(demo, monkeypatch):
    repo = demo.repo()
    api.add(repo, ["demo/gate"])
    demo.act(gate="stage")
    monkeypatch.setenv("DEMO_PREPARE", "edit")
    write(repo, "notes/a.md")
    result = moxi_repo.commit(spec(repo, prepare="demo_pieces.committing:prepare"))
    assert result.status == "committed"
    # notes/a.md was edited after prepare judged it; the piece staged a file of its own.
    assert result.unjudged == ("notes/a.md", "staged-by-piece.txt")
    assert "committed unjudged 2: notes/a.md, staged-by-piece.txt" in result.line()
    [run] = demo.runs()
    assert run["committer"] == "demo/snap"


# -- the paths it hands git -------------------------------------------------------------------

ODD = ("notes/a[1].md", "notes/a1.md", "notes/st*r.md", "notes/q?.md", "notes/:colon.md",
       "notes/new\nline.md")


def test_odd_names_commit_by_name_and_every_path_is_literal(demo, calls):
    repo = demo.repo()
    for name in ODD:
        write(repo, name)
    calls()
    result = moxi_repo.commit(spec(repo, prepare=lambda ctx: moxi_repo.Prepared(
        drop=[("notes/a1.md", "no")])))
    assert result.status == "committed", result
    assert set(result.files) == set(ODD) - {"notes/a1.md"}
    assert set(last_files(repo)) == set(ODD) - {"notes/a1.md"}
    assert result.left == (("notes/a1.md", "dropped: no"),)
    assert git(repo, "status", "--porcelain", "-z").stdout == "?? notes/a1.md\0"
    log = calls()
    handed = [x for call in log for x in paths_handed(call)]
    assert handed and all(x.startswith(":(literal)") for x in handed), handed
    assert any(c["stdin"] for c in log if c["argv"][:3] == ["-C", str(repo), "commit"])


def test_no_call_holds_a_pathspec_variable_or_option(demo, calls, monkeypatch):
    repo = demo.repo()
    api.add(repo, ["demo/gate"])
    log = demo.tmp / "prepare.log"
    monkeypatch.setenv("DEMO_PREPARE_LOG", str(log))
    monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    monkeypatch.setenv("GIT_ICASE_PATHSPECS", "1")
    write(repo, "notes/a.md")
    write(repo, "notes/B.md")
    calls()
    result = moxi_repo.commit(spec(repo, prepare="demo_pieces.committing:prepare"))
    assert result.status == "committed", result
    log_calls = calls()
    assert len(log_calls) > 8
    for call in log_calls:
        assert call["env"] == {}, call
        assert not set(call["argv"]) & set(GLOBAL_OPTIONS), call
    # The prepare step's own git saw both files through a glob: no pathspec variable.
    [seen] = [json.loads(x) for x in log.read_text().splitlines()]
    assert seen["glob"] == ["notes/B.md", "notes/a.md"]
    # The commit, and the git hook it ran, carried the spec's name.
    assert any(c["committer"] == "demo/snap" for c in log_calls)


def test_thousands_of_changed_paths_never_reach_argv(demo, calls):
    repo = demo.repo()
    names = [f"notes/n{i:04d}.md" for i in range(3000)]
    for name in names:
        write(repo, name, "x\n")
    calls()
    result = moxi_repo.commit(spec(repo))
    assert result.status == "committed" and len(result.files) == 3000
    for call in calls():
        assert not any(name in arg for name in ("n0001.md", "n2999.md")
                       for arg in call["argv"]), call["argv"][:12]


def test_a_spec_over_the_bound_is_an_error(demo):
    repo = demo.repo()
    many = moxi_repo.commit(spec(repo, paths=tuple(f"p{i}" for i in range(65))))
    assert many.status == "error" and "65 paths" in many.detail
    long = moxi_repo.commit(spec(repo, paths=("x" * 9000,)))
    assert long.status == "error" and "bytes of path text" in long.detail
    with pytest.raises(ValueError):
        moxi_repo.changes(repo, paths=[f"p{i}" for i in range(65)])


def test_a_candidate_deleted_between_status_and_add_retries_once(demo, monkeypatch):
    repo = demo.repo()
    write(repo, "notes/a.md")
    vanish = write(repo, "notes/vanish.md")
    real = committer._dirty
    seen = []

    def dirty(p, paths):
        out = real(p, paths)
        seen.append(out)
        if len(seen) == 1:
            vanish.unlink()
        return out

    monkeypatch.setattr(committer, "_dirty", dirty)
    result = moxi_repo.commit(spec(repo))
    assert len(seen) == 2
    assert result.status == "committed" and result.files == ("notes/a.md",)


def test_changes_reads_staged_and_ranged_changes(demo):
    repo = demo.repo()
    write(repo, "notes/a[1].md", "one\n")
    write(repo, "notes/b.md", "the same text, long enough to pair as a rename\n")
    git(repo, "add", "notes")
    got = moxi_repo.changes(repo, paths=["notes/a[1].md"])
    assert [(c.status, c.path) for c in got] == [("A", "notes/a[1].md")]
    first = head(repo)
    git(repo, "commit", "-q", "-m", "two")
    git(repo, "mv", "notes/b.md", "notes/c.md")
    git(repo, "commit", "-q", "-m", "moved")
    ranged = moxi_repo.changes(repo, range=(first, "HEAD"))
    assert {(c.status, c.path, c.old_path) for c in ranged} == {
        ("A", "notes/a[1].md", None), ("A", "notes/c.md", None)}
    moved = moxi_repo.changes(repo, range=("HEAD~1", "HEAD"))
    assert [(c.status, c.path, c.old_path) for c in moved] == [
        ("R", "notes/c.md", "notes/b.md")]
    raw = moxi_repo.git(repo, "cat-file", "blob", moved[0].blob, binary=True)
    assert raw.stdout == b"the same text, long enough to pair as a rename\n"


# -- push ----------------------------------------------------------------------------------------

def make_remote(demo, repo: Path) -> Path:
    bare = demo.tmp / f"{repo.name}-remote.git"
    git(demo.tmp, "init", "-q", "--bare", str(bare))
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nif [ -e deny ]; then echo 'remote says no' >&2; exit 1; fi\n",
                    encoding="utf-8")
    hook.chmod(0o755)
    git(repo, "remote", "add", "origin", str(bare))
    return bare


def remote_head(bare: Path, branch: str = "main") -> str | None:
    done = git(bare, "rev-parse", "-q", "--verify", f"refs/heads/{branch}", check=False)
    return done.stdout.strip() or None


def deny(bare: Path, on: bool) -> None:
    flag = bare / "deny"
    if on:
        flag.write_text("", encoding="utf-8")
    elif flag.exists():
        flag.unlink()


def test_a_failed_push_keeps_the_commit_and_throttled_and_clean_passes_retry(demo, clock):
    repo = demo.repo()
    bare = make_remote(demo, repo)
    deny(bare, True)
    write(repo, "notes/a.md")
    s = spec(repo, push="origin", push_backoff=300, min_interval=3600)
    first = moxi_repo.commit(s)
    assert first.status == "committed" and first.push_error
    assert "remote says no" in first.push_error and first.exit_code == 1
    assert head(repo) == first.sha and remote_head(bare) is None
    deny(bare, False)
    clock[0] += 10
    write(repo, "notes/b.md")
    early = moxi_repo.commit(s)
    assert early.status == "throttled" and early.pushed == () and early.push_error is None
    assert remote_head(bare) is None
    clock[0] += 400
    late = moxi_repo.commit(s)
    assert late.status == "throttled" and late.pushed == ("main",)
    assert remote_head(bare) == first.sha
    # The same with a clean pass.
    deny(bare, True)
    s0 = spec(repo, push="origin", push_backoff=300)
    second = moxi_repo.commit(s0)
    assert second.status == "committed" and second.push_error
    deny(bare, False)
    clock[0] += 10
    assert moxi_repo.commit(s0).status == "clean" and remote_head(bare) == first.sha
    clock[0] += 400
    again = moxi_repo.commit(s0)
    assert again.status == "clean" and again.pushed == ("main",)
    assert remote_head(bare) == second.sha


def test_a_busy_pass_never_pushes_and_an_index_busy_pass_does(demo, clock):
    repo = demo.repo()
    bare = make_remote(demo, repo)
    deny(bare, True)
    write(repo, "notes/a.md")
    s = spec(repo, push="origin", push_backoff=300)
    assert moxi_repo.commit(s).push_error
    deny(bare, False)
    clock[0] += 400
    with files.lock(repo / ".git" / "moxi-repo" / "lock"):
        busy = moxi_repo.commit(s)
    assert busy.status == "busy" and busy.pushed == () and remote_head(bare) is None
    write(repo, "notes/b.md")
    (repo / ".git" / "index.lock").write_text("", encoding="utf-8")
    try:
        held = moxi_repo.commit(s)
    finally:
        (repo / ".git" / "index.lock").unlink()
    assert held.status == "index-busy" and held.exit_code == 0
    assert held.pushed == ("main",) and remote_head(bare) == head(repo)


def test_a_failed_push_of_one_branch_is_retried_from_another_and_from_detached(demo, clock):
    repo = demo.repo()
    bare = make_remote(demo, repo)
    s = spec(repo, push="origin", push_backoff=300)
    git(repo, "switch", "-q", "-c", "a")
    deny(bare, True)
    write(repo, "notes/a.md")
    assert moxi_repo.commit(s).push_error
    a_sha = head(repo)
    deny(bare, False)
    git(repo, "switch", "-q", "-c", "b")
    write(repo, "notes/b.md")
    clock[0] += 400
    from_b = moxi_repo.commit(s)
    assert from_b.status == "committed" and set(from_b.pushed) == {"a", "b"}
    assert remote_head(bare, "a") == a_sha
    # Again, from a detached HEAD.
    git(repo, "switch", "-q", "a")
    deny(bare, True)
    write(repo, "notes/c.md")
    assert moxi_repo.commit(s).push_error
    a_sha = head(repo)
    git(repo, "switch", "-q", "--detach")
    deny(bare, False)
    clock[0] += 400
    detached = moxi_repo.commit(s)
    assert detached.detached and detached.pushed == ("a",)
    assert remote_head(bare, "a") == a_sha and "HEAD is detached" in detached.line()


# -- saved specs and the CLI --------------------------------------------------------------------

def test_a_saved_spec_round_trips_through_git_config(demo):
    repo = demo.repo()
    s = spec(repo, paths=("notes", "a[1].md", "with space.md"), min_interval=90.5,
             message="snapshot", trailers="demo_pieces.committing:trailers",
             prepare="demo_pieces.committing:prepare", push="origin", push_backoff=60,
             max_bytes=None)
    moxi_repo.save_spec(s)
    back = committer.saved_spec(repo, "demo/snap")
    assert back == s
    assert git(repo, "config", "moxi-repo.spec.demo/snap.min-interval").stdout == "90.5\n"
    with pytest.raises(committer.SpecError):
        moxi_repo.save_spec(spec(repo, prepare=lambda ctx: None))


def test_moxi_repo_commit_runs_saved_specs_and_gives_exit_codes(demo, capsys):
    repo = demo.repo()
    moxi_repo.save_spec(spec(repo))
    moxi_repo.save_spec(spec(repo, name="demo/other", paths=("other",)))
    write(repo, "notes/a.md")
    assert cli.main(["commit", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "repo: demo/snap committed" in out and "repo: demo/other nothing to commit" in out
    write(repo, "notes/b.md")
    assert cli.main(["commit", str(repo), "--spec", "demo/snap", "--json"]) == 0
    [one] = json.loads(capsys.readouterr().out)
    assert one["status"] == "committed" and one["files"] == ["notes/b.md"]
    assert cli.main(["commit", str(repo), "--spec", "demo/none"]) == 2
    with files.lock(repo / ".git" / "moxi-repo" / "lock"):
        assert cli.main(["commit", str(repo), "--spec", "demo/snap"]) == 0
        assert "busy" in capsys.readouterr().out
        assert cli.main(["commit", str(repo), "--spec", "demo/snap", "--wait", "0.2"]) == 75
    (repo / ".git" / "index.lock").write_text("", encoding="utf-8")
    write(repo, "notes/c.md")
    try:
        assert cli.main(["commit", str(repo), "--spec", "demo/snap"]) == 0
    finally:
        (repo / ".git" / "index.lock").unlink()
    assert "index-busy" in capsys.readouterr().out
    api.add(repo, ["demo/gate"])
    demo.act(gate="refuse")
    assert cli.main(["commit", str(repo), "--spec", "demo/snap"]) == 3
    err = capsys.readouterr().err
    assert "refused" in err and "gate says no" in err
