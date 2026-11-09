"""The snapshot spec, one pass over a real notes repo, and the settle steps run for real."""

from __future__ import annotations

import json
import sys
import threading
import time
import types
from pathlib import Path

import moxi.paths
import pytest
from moxi_repo.api import Prepared, saved_spec

from moxi_notes import settle, snapshot, spec

from notes_kit import configure, git, git_env, home, moxi_cmd, repo


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def prepare(monkeypatch):
    """A stand-in for notes' prepare step that drops nothing; `delay` holds the pass."""
    module = types.ModuleType("moxi_notes.prepare")
    module.delay = 0.0
    module.calls = 0

    def step(ctx):
        module.calls += 1
        time.sleep(module.delay)
        return Prepared()

    module.prepare = step
    monkeypatch.setitem(sys.modules, "moxi_notes.prepare", module)
    return module


@pytest.fixture
def notes() -> Path:
    return repo(home() / "notes", commit=True)


def device() -> str:
    return moxi.paths.device_id()[:8]


def records() -> dict:
    path = moxi.paths.Layout("notes").state_dir() / "doctor.json"
    return json.loads(path.read_text()) if path.exists() else {}


def log(root) -> list[str]:
    return git("log", "--format=%an <%ae> %s", cwd=root).splitlines()


# --- the spec ----------------------------------------------------------------------------

def test_the_spec_is_built_from_config(notes):
    built = spec.build()
    assert (built.name, built.root, built.paths) == ("notes/snapshot", notes, (".",))
    assert built.author == ("moxi notes", f"notes+{device()}@moxi.invalid")
    assert (built.min_interval_s, built.message, built.prepare, built.push) == \
        (15 * 60, None, "moxi_notes.prepare:prepare", None)
    assert (built.push_backoff_s, built.max_bytes, built.trailers) == (300, 10 * 2**20, None)
    configure(snapshot__interval_minutes=5, snapshot__push="origin", max_mib=2,
              snapshot__author_name="sample-bot", snapshot__author_email="bot@example.invalid")
    built = spec.build()
    assert built.author == ("sample-bot", "bot@example.invalid")
    assert (built.min_interval_s, built.push, built.max_bytes) == (300, "origin", 2 * 2**20)


def test_the_spec_is_saved_only_when_it_differs(notes):
    assert spec.ensure_saved(spec.build()) is True
    assert spec.ensure_saved(spec.build()) is False
    configure(snapshot__interval_minutes=5)
    assert spec.ensure_saved(spec.build()) is True
    assert saved_spec(notes, "notes/snapshot").min_interval_s == 300


def test_a_root_spelled_through_a_link_is_the_same_spec(notes):
    spec.ensure_saved(spec.build())
    link = home() / "notes-link"
    link.symlink_to(notes)
    configure(root=str(link))
    assert spec.ensure_saved(spec.build()) is False


def test_repo_commit_runs_the_spec_as_last_saved(notes):
    configure(snapshot__interval_minutes=0)
    (notes / "a.md").write_text("alpha\n", encoding="utf-8")
    result, _, _ = snapshot.one_pass()
    assert result.status == "committed"
    configure(snapshot__author_name="sample-bot")
    (notes / "b.md").write_text("beta\n", encoding="utf-8")
    code, out, err = moxi_cmd("repo", "commit", str(notes), "--spec", "notes/snapshot")
    assert code == 0, out + err
    assert "committed" in out
    assert log(notes)[0] == (f"moxi notes <notes+{device()}@moxi.invalid> "
                             "notes/snapshot: 1 path")
    assert len(log(notes)) == 3
    (notes / "c.md").write_text("gamma\n", encoding="utf-8")
    result, _, _ = snapshot.one_pass()
    assert result.status == "committed"
    assert log(notes)[0].startswith(f"sample-bot <notes+{device()}@moxi.invalid>")


# --- one pass ----------------------------------------------------------------------------

def test_a_pass_commits_as_the_device_and_a_second_is_throttled(notes):
    (notes / "a.md").write_text("alpha\n", encoding="utf-8")
    first, built, ran = snapshot.one_pass()
    assert (first.status, ran) == ("committed", True)
    assert log(notes)[0] == f"moxi notes <notes+{device()}@moxi.invalid> notes/snapshot: 1 path"
    (notes / "b.md").write_text("beta\n", encoding="utf-8")
    assert snapshot.one_pass()[0].status == "throttled"


def test_two_passes_at_once_give_one_committed_and_one_busy(notes, prepare):
    spec.ensure_saved(spec.build())
    (notes / "a.md").write_text("alpha\n", encoding="utf-8")
    prepare.delay = 0.5
    results = []
    threads = [threading.Thread(target=lambda: results.append(snapshot.one_pass()[0]))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(r.status for r in results) == ["busy", "committed"]


def test_a_failure_before_the_pass_is_the_snapshot_record_until_a_pass_runs():
    root = home() / "notes"
    root.mkdir()
    result, built, ran = snapshot.one_pass()
    assert (result.status, ran) == ("error", False)
    assert "could not start" in records()["snapshot"]["text"]
    repo(root, commit=True)
    assert snapshot.one_pass()[0].status in ("committed", "clean")
    assert "snapshot" not in records()


def test_the_snapshot_verb_runs_one_pass_and_prints_repos_line(notes):
    (notes / "a.md").write_text("alpha\n", encoding="utf-8")
    code, out, err = moxi_cmd("notes", "snapshot")
    assert (code, err) == (0, "")
    assert out.startswith("repo: notes/snapshot committed ")


# --- the settle steps for real -----------------------------------------------------------

def test_an_ended_sessions_edit_is_committed_when_the_interval_ends(notes, monkeypatch):
    monkeypatch.setattr(spec, "MINUTE", 1)  # the interval in seconds
    configure(snapshot__interval_minutes=2)
    (notes / "a.md").write_text("alpha\n", encoding="utf-8")
    assert snapshot.one_pass()[0].status == "committed"
    (notes / "b.md").write_text("beta\n", encoding="utf-8")
    ops = snapshot.Real()
    started = time.time()
    settle.run(settle.settle(ops), sleep=lambda s: time.sleep(min(s, 0.05)))
    assert time.time() - started >= 1
    assert git("status", "--porcelain", cwd=notes) == ""
    assert len(log(notes)) == 3
    assert ops.read()[1] == 1
    assert "settle" not in records()


def test_a_second_settler_exits_while_one_holds_the_lock(notes, monkeypatch):
    monkeypatch.setattr(spec, "MINUTE", 1)
    configure(snapshot__interval_minutes=60)
    (notes / "a.md").write_text("alpha\n", encoding="utf-8")
    snapshot.one_pass()
    holder = snapshot.Real()
    assert holder.try_lock()
    try:
        (notes / "b.md").write_text("beta\n", encoding="utf-8")
        second = snapshot.Real()
        settle.run(settle.settle(second), sleep=lambda s: pytest.fail("it waited"))
        assert second.read()[1] == 1
    finally:
        holder.release()


def test_the_timer_and_snapshot_take_no_duty(notes, monkeypatch, capsys):
    (notes / "a.md").write_text("alpha\n", encoding="utf-8")
    assert snapshot.main([]) == 0
    (notes / "b.md").write_text("beta\n", encoding="utf-8")
    assert snapshot.main([]) == 0
    assert "throttled" in capsys.readouterr().out.splitlines()[-1]
    assert snapshot.Real().read() == (None, 0)


def test_the_counter_keeps_its_generation_across_raises(notes):
    ops = snapshot.Real()
    first = ops.raise_request()
    second = ops.raise_request()
    assert first[0] == second[0] and (first[1], second[1]) == (1, 2)
    (ops.dir / settle.REQUESTS).write_text("not json", encoding="utf-8")
    assert ops.read() == (None, 0)
    third = ops.raise_request()
    assert third[0] != first[0] and third[1] == 1


def test_a_held_save_counts_as_busy(notes, monkeypatch):
    import moxi.files

    def held(*a, **kw):
        raise moxi.files.LockTimeout(Path("lock"), 1)

    monkeypatch.setattr(spec, "ensure_saved", held)
    result, built, ran = snapshot.one_pass()
    assert (result.status, ran) == ("busy", False)
    ops = snapshot.Real(pass_fn=lambda: (result, built, ran))
    p = settle.run(ops.run_pass())
    assert (p.status, p.held_lock) == ("busy", False)
