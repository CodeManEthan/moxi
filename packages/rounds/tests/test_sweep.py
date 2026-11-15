"""The sweep of leftover folders: candidates only from ended builds' records on this
machine, the removal rule's two halves and the sweep's own rules (every log read, no
process using the folder, quiet for the grace), a dry run until armed, one run at a
time, and a log line for each removal and each trash rename and delete."""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from moxi import files

from moxi_rounds import (builds, checking, cli, commissioning, doctor, log, opening, places,
                         removal, sweeping)
from moxi_rounds import settings as own
from moxi_rounds import state as states

ROUND = "proj-a/a-round"
OTHER = "proj-a/b-round"
POINTER = "comms:1234567"
ARMED = "[sweep]\narmed = true\n"


@pytest.fixture
def repo(git_repo, tmp_path):
    from moxi import cli as moxi_cli

    assert moxi_cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert moxi_cli.main(["projects", "set", "proj-a", "--notes-root",
                          str(tmp_path / "notes")]) == 0
    exclude = git_repo.root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(".venv\n__pycache__\n.env\n", encoding="utf-8")
    return git_repo


@pytest.fixture
def later(monkeypatch):
    """Read the sweep's clock three hours on, so what a test just made is quiet."""
    monkeypatch.setattr(sweeping, "_now", lambda: time.time() + 3 * 3600)


@pytest.fixture
def made(repo, tmp_path, write_config):
    """B1 commissioned on `work` with one commit of work, still open. Returns `(log,
    worktree repo, commission event)`."""
    write_config("")
    opening.open_round(ROUND, description="a placeholder round", actor="planner",
                       flow="fix")
    path = log.round_log(tmp_path / "notes" / "rounds" / "a-round")
    log.append(path, "stage.declared",
               {"id": "B1", "repo": "proj-a", "branch": "work", "after": []},
               actor="planner")
    commissioning.commission(ROUND, "B1", builder="coder", actor="planner")
    event = commission(path)
    tree = type(repo)(Path(event["worktree"]))
    tree.commit("work.txt", "the work")
    return path, tree, event


@pytest.fixture
def ended(made, later):
    """`made`, dropped by a drop that stopped before it removed anything: both folders
    are left."""
    path, _tree, _event = made
    log.append(path, "build.dropped", {"stage": "B1", "by": "lead", "record": POINTER},
               actor="planner")
    return made


def commission(path: Path) -> dict:
    state = states.fold(log.read_round(path))
    return checking.commission_of(state, builds.current(state, "B1"))


def dry():
    return sweeping.account(settings=own.load())


def fates(swept) -> dict[str, tuple]:
    """Each leftover by kind: its fate and the check that kept it."""
    return {one.candidate.kind: (one.fate, None if one.kept is None else one.kept.check)
            for one in swept.leftovers}


def lines():
    return files.read_records(sweeping.log_file())


# -- what is a candidate ---------------------------------------------------------------------

def test_the_dry_run_names_both_folders_of_an_ended_build_and_changes_nothing(ended):
    _path, _tree, event = ended

    swept = dry()

    assert fates(swept) == {"worktree": ("would go", None), "scratch": ("would go", None)}
    assert Path(event["worktree"]).is_dir() and Path(event["scratch"]).is_dir()
    assert not sweeping.log_file().exists() and not sweeping.lock_file().exists()


def test_an_open_build_is_never_a_candidate(made, later, write_config):
    write_config(ARMED)
    swept = sweeping.run(remove=True)
    assert swept.leftovers == () and swept.clear
    assert Path(made[2]["worktree"]).is_dir() and Path(made[2]["scratch"]).is_dir()


def test_an_open_build_recording_the_same_place_keeps_an_ended_ones_folder(ended,
                                                                              tmp_path):
    _path, _tree, event = ended
    opening.open_round(OTHER, description="a placeholder round", actor="planner",
                       flow="fix")
    fields = {key: value for key, value in event.items()
              if key not in ("v", "n", "at", "actor", "type")}
    log.append(log.round_log(tmp_path / "notes" / "rounds" / "b-round"),
               "build.commissioned", fields, actor="planner")

    found = fates(dry())

    assert found == {"worktree": ("kept", "A1"), "scratch": ("kept", "A1")}


def test_a_record_from_another_host_is_kept_and_named(ended, monkeypatch):
    monkeypatch.setattr(places, "host", lambda: "box-1")
    assert fates(dry()) == {"worktree": ("kept", "A1"), "scratch": ("kept", "A1")}


def test_a_folder_no_record_names_is_never_a_candidate(ended, write_config):
    _path, _tree, event = ended
    write_config(ARMED)
    stray = Path(event["worktree"]).parent / "not-recorded"
    stray.mkdir()
    (stray / ".rounds-build").write_text(
        (Path(event["scratch"]) / ".rounds-build").read_text(encoding="utf-8"),
        encoding="utf-8")

    swept = sweeping.run(remove=True)

    assert {one.place for one in swept.leftovers} == {Path(event["worktree"]),
                                                      Path(event["scratch"])}
    assert (stray / ".rounds-build").is_file()


def test_a_folder_already_gone_is_not_in_the_account(ended):
    _path, _tree, event = ended
    removal.delete(Path(event["scratch"]))
    assert set(fates(dry())) == {"worktree"}


# -- the removal rule's halves, in the account ------------------------------------------------

def test_an_unmarked_folder_is_named_and_never_removed(ended, write_config):
    _path, _tree, event = ended
    write_config(ARMED)
    (Path(event["scratch"]) / ".rounds-build").unlink()

    swept = sweeping.run(remove=True)

    [scratch] = [one for one in swept.leftovers if one.candidate.kind == "scratch"]
    assert (scratch.fate, scratch.kept.check) == ("kept", "A4")
    assert "unmarked" in scratch.line()
    assert Path(event["scratch"]).is_dir()


def test_a_worktree_holding_an_ignored_env_is_kept_and_its_paths_named(ended,
                                                                         write_config):
    _path, tree, event = ended
    write_config(ARMED)
    (tree.root / ".env").write_text("TOKEN=only here\n", encoding="utf-8")

    swept = sweeping.run(remove=True)

    [kept] = [one for one in swept.leftovers if one.candidate.kind == "worktree"]
    assert (kept.kept.check, kept.kept.work) == ("B3", (".env",))
    assert (tree.root / ".env").is_file()


def test_a_finish_with_no_merge_keeps_the_worktree_until_its_branch_is_on_the_target(
        made, later, repo):
    path, _tree, event = made
    log.append(path, "finish.done", {"stage": "B1", "tip": repo.rev("work")},
               actor="planner")

    assert fates(dry())["worktree"] == ("kept", "B6")

    repo.merge("work")
    assert fates(dry())["worktree"] == ("would go", None)


# -- C, D, E ---------------------------------------------------------------------------------

def test_a_log_that_cant_be_read_keeps_every_folder_and_is_named(ended, tmp_path,
                                                                 write_config):
    _path, _tree, event = ended
    write_config(ARMED)
    opening.open_round(OTHER, description="a placeholder round", actor="planner",
                       flow="fix")
    with log.round_log(tmp_path / "notes" / "rounds" / "b-round").open("ab") as file:
        file.write(b"not an event\n")

    swept = sweeping.run(remove=True)

    assert fates(swept) == {"worktree": ("kept", "C"), "scratch": ("kept", "C")}
    assert len(swept.unreadable) == 1 and "b-round" in swept.unreadable[0]
    assert Path(event["worktree"]).is_dir() and Path(event["scratch"]).is_dir()


_USERS = {
    "working folder": ("import os, sys, time; os.chdir(sys.argv[1]); print('up', "
                       "flush=True); time.sleep(60)", {}),
    "open file": ("import sys, time; held = open(sys.argv[1] + '/held.txt', 'w'); "
                  "print('up', flush=True); time.sleep(60)", {}),
    "argument word": ("import time; print('up', flush=True); time.sleep(60)", {}),
    "environment value": ("import time; print('up', flush=True); time.sleep(60)",
                          {"PLACE_FOR_TEST": "{place}/bin"}),
}
_LINUX_USERS = {
    "mapped file": ("import mmap, sys, time; f = open(sys.argv[1] + '/mapped.bin', 'r+b'); "
                    "m = mmap.mmap(f.fileno(), 0); f.close(); print('up', flush=True); "
                    "time.sleep(60)", {}),
}


@pytest.mark.parametrize("use", [*_USERS, *(_LINUX_USERS if sys.platform == "linux"
                                             else {})])
def test_a_process_using_the_folder_keeps_it(ended, use):
    _path, _tree, event = ended
    place = Path(event["scratch"])
    (place / "mapped.bin").write_bytes(b"\0" * 4096)
    code, more = {**_USERS, **_LINUX_USERS}[use]
    env = {**os.environ, **{key: value.format(place=place) for key, value in more.items()}}
    argv = [sys.executable, "-c", code, str(place)]
    if use == "environment value":
        argv = argv[:3]
    process = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "up"
        [scratch] = [one for one in dry().leftovers if one.candidate.kind == "scratch"]
    finally:
        process.kill()
        process.communicate(timeout=30)
    assert (scratch.fate, scratch.kept.check) == ("kept", "D")
    assert f"pid {process.pid}" in scratch.kept.work[0]


def test_a_file_changed_within_the_grace_keeps_the_folder(ended):
    _path, _tree, event = ended
    written = Path(event["scratch"]) / "out.log"
    written.write_text("still being written\n", encoding="utf-8")
    soon = time.time() + 2.5 * 3600
    os.utime(written, (soon, soon))

    [scratch] = [one for one in dry().leftovers if one.candidate.kind == "scratch"]

    assert scratch.kept.check == "E"
    assert scratch.kept.why == f"{written} changed within the grace of 2 hours"


def test_a_marker_written_within_the_grace_keeps_the_folder(ended):
    _path, _tree, event = ended
    marker = Path(event["scratch"]) / ".rounds-build"
    data = json.loads(marker.read_text(encoding="utf-8"))
    data["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 2.5 * 3600))
    marker.write_text(json.dumps(data) + "\n", encoding="utf-8")

    [scratch] = [one for one in dry().leftovers if one.candidate.kind == "scratch"]

    assert scratch.kept.check == "E" and "marker was written" in scratch.kept.why


@pytest.mark.skipif(sys.platform == "win32", reason="the change time is POSIX's")
def test_a_modified_time_set_back_still_reads_by_the_change_time(made, monkeypatch):
    path, _tree, event = made
    log.append(path, "build.dropped", {"stage": "B1", "by": "lead", "record": POINTER},
               actor="planner")
    place = Path(event["scratch"])
    marker = place / ".rounds-build"
    data = json.loads(marker.read_text(encoding="utf-8"))
    data["at"] = "2020-01-01T00:00:00Z"
    marker.write_text(json.dumps(data) + "\n", encoding="utf-8")
    (place / "out.log").write_text("written now\n", encoding="utf-8")
    old = time.time() - 86400
    for one in (place / "out.log", marker, place):
        os.utime(one, (old, old))
    monkeypatch.setattr(sweeping, "_now", time.time)

    [scratch] = [one for one in dry().leftovers if one.candidate.kind == "scratch"]

    assert scratch.kept.check == "E" and "changed within the grace" in scratch.kept.why


# -- the arm ---------------------------------------------------------------------------------

def test_unarmed_remove_prints_the_dry_run_and_refuses_and_the_doctor_lists_the_same(
        ended, capsys):
    _path, _tree, event = ended

    assert cli.main(["drop", "--leftovers", "--remove"]) == 3

    said = capsys.readouterr()
    assert "dry run: sweep.armed is false" in said.out
    assert f"would go {event['worktree']}" in said.out
    assert f"would go {event['scratch']}" in said.out
    assert "sweep.armed is false" in said.err and "armed = true" in said.err
    assert Path(event["worktree"]).is_dir() and Path(event["scratch"]).is_dir()
    row = next(row for row in doctor.rows() if row.name == "leftovers")
    assert row.state == "warn"
    assert f"would go {event['worktree']}" in row.detail
    assert f"would go {event['scratch']}" in row.detail
    assert "armed = true" in row.fix and sweeping.REMOVE in row.fix


def test_the_doctor_row_is_ok_with_nothing_left(made):
    row = next(row for row in doctor.rows() if row.name == "leftovers")
    assert (row.state, row.fix) == ("ok", None)


def test_armed_a_worktree_goes_by_git_its_branch_stays_and_scratch_goes_through_the_trash(
        ended, repo, write_config):
    _path, _tree, event = ended
    write_config(ARMED)

    swept = sweeping.run(remove=True)

    assert fates(swept) == {"worktree": ("removed", None), "scratch": ("removed", None)}
    assert not os.path.lexists(event["worktree"]) and not os.path.lexists(event["scratch"])
    assert "work" in repo.git("branch", "--list", "work")
    assert repo.git("worktree", "list", "--porcelain").count("worktree ") == 1
    assert os.listdir(removal.trash_dir()) == []
    acts = [(one["act"], one["kind"], one.get("entry")) for one in lines()]
    assert acts == [("removed", "worktree", None),
                    ("renamed", "scratch", event["build"]),
                    ("deleted", "scratch", event["build"])]
    for one in lines():
        assert (one["round"], one["stage"], one["repo"], one["branch"], one["build"]) == (
            ROUND, "B1", "proj-a", "work", event["build"])
        assert one["proven"] == ["A", "B", "C", "D", "E"]
    assert sweeping.started_file().is_file()


def test_the_command_removes_and_prints_json(ended, write_config, capsys):
    _path, _tree, event = ended
    write_config(ARMED)

    assert cli.main(["drop", "--leftovers", "--remove", "--json"]) == 0

    found = json.loads(capsys.readouterr().out)
    assert (found["armed"], found["removing"]) == (True, True)
    assert {(one["kind"], one["fate"], one["place"]) for one in found["leftovers"]} == {
        ("worktree", "removed", event["worktree"]), ("scratch", "removed", event["scratch"])}


def test_a_rename_into_a_trash_on_another_disk_keeps_the_folder(ended, write_config,
                                                                monkeypatch):
    _path, _tree, event = ended
    write_config(ARMED)

    def across(*_args):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(removal.os, "rename", across)
    swept = sweeping.run(remove=True)

    assert fates(swept)["scratch"] == ("kept", "removal")
    assert Path(event["scratch"]).is_dir()


def test_a_trash_entry_a_stopped_run_left_is_deleted_by_the_next_and_others_are_left(
        made, write_config):
    write_config(ARMED)
    trash = removal.trash_dir()
    (trash / "0123456789abcdef" / "inner").mkdir(parents=True)
    (trash / "0123456789abcdef" / "inner" / "out.log").write_text("x", encoding="utf-8")
    (trash / "not-the-sweeps").mkdir()
    files.append_record(sweeping.log_file(), {
        "at": "2026-01-01T00:00:00Z", "act": "renamed", "place": "/nowhere",
        "round": ROUND, "stage": "B1", "repo": "proj-a", "branch": "work",
        "build": "0123456789abcdef", "kind": "scratch", "entry": "0123456789abcdef"})

    assert {one.name: one.fate for one in dry().trash} == {
        "0123456789abcdef": "would delete", "not-the-sweeps": "left"}
    swept = sweeping.run(remove=True)

    assert {one.name: one.fate for one in swept.trash} == {
        "0123456789abcdef": "deleted", "not-the-sweeps": "left"}
    assert sorted(os.listdir(trash)) == ["not-the-sweeps"]
    assert lines()[-1]["act"] == "deleted" and lines()[-1]["entry"] == "0123456789abcdef"


def test_a_second_run_while_one_holds_the_lock_stops(ended, holder, capsys):
    sweeping.lock_file().parent.mkdir(parents=True, exist_ok=True)
    held = holder(sweeping.lock_file())

    assert cli.main(["drop", "--leftovers"]) == 3

    assert f"pid {held.pid}" in capsys.readouterr().err


def test_a_run_starts_no_removal_after_its_time_limit(ended, write_config, monkeypatch):
    _path, _tree, event = ended
    write_config(ARMED)
    clock = iter([0.0] + [sweeping.TIME_LIMIT_S + 1] * 10)
    monkeypatch.setattr(sweeping, "_monotonic", lambda: next(clock))

    swept = sweeping.run(remove=True)

    assert swept.timed_out
    assert fates(swept) == {"worktree": ("left", None), "scratch": ("left", None)}
    assert Path(event["worktree"]).is_dir() and Path(event["scratch"]).is_dir()


def test_the_sweep_takes_no_round_and_no_pointer(capsys):
    assert cli.main(["drop", "proj-a/a-round", "--leftovers"]) == 2
    assert cli.main(["drop", "--leftovers", "--by", "lead", "--record", POINTER]) == 2
    assert cli.main(["drop", "--remove"]) == 2
    assert "--leftovers" in capsys.readouterr().err


# -- the settings ----------------------------------------------------------------------------

def test_the_sweep_settings_default_to_unarmed_and_two_hours(write_config):
    write_config("")
    found = own.load()
    assert (found["sweep.armed"], found["sweep.at_start"], found["sweep.grace_hours"]) == (
        False, False, 2)


@pytest.mark.parametrize("hours", [0, -1])
def test_a_grace_below_one_hour_is_refused(hours):
    from moxi.config import ConfigError

    with pytest.raises(ConfigError, match="sweep.grace_hours"):
        own.check_text(f"[sweep]\ngrace_hours = {hours}\n")
