"""The detached starter: every recall child and taker is started through it, so it runs
outside every session; a child it can't start ends the recall `lost` (`spawn`)."""

import os
import signal
import subprocess
import sys
import time

import moxi.proc

from moxi_memory import recall_child, recall_start, recall_take, recalls, records

SESSION = "0190b000-0000-7000-8000-0000000005a1"


def spawns(monkeypatch):
    calls = []
    monkeypatch.setattr(moxi.proc, "spawn", lambda argv, **kw: calls.append((argv, kw)) or 7)
    return calls


def test_a_recall_child_and_a_taker_start_through_the_starter(monkeypatch):
    calls = spawns(monkeypatch)
    recall_start.spawn_child(SESSION, "r1")
    recall_take.start_taker(SESSION)
    (child, child_kw), (taker, taker_kw) = calls
    assert os.path.basename(child[0]).startswith("moxi-memory-subconscious")
    assert child[1:] == ["--detach", "--session", SESSION, "--recall", "r1"]
    assert taker[1:] == ["--detach", "--take", "--session", SESSION]
    for kw in (child_kw, taker_kw):
        assert kw["detached"] is True and kw["output"] == os.devnull


def test_the_starter_starts_the_rest_detached_and_exits(monkeypatch):
    calls = spawns(monkeypatch)
    argv = ["--session", SESSION, "--recall", "r1"]
    assert recall_child.main(["--detach", *argv]) == 0
    ((started, kw),) = calls
    assert started[1:] == argv and kw["detached"] is True


def test_a_child_the_starter_cant_start_loses_the_recall(monkeypatch):
    record = recall_start.begin(SESSION, "claude", start="prompt", text="ingest lock",
                                senders=(), settings=recall_start.default_settings(),
                                cwd=None, notes_dir=None, route_name="subagent",
                                spawn=lambda s, r: None)

    def refuse(argv):
        raise FileNotFoundError("moxi-memory-subconscious")

    assert recall_child.detach(["--session", SESSION, "--recall", record["recall"]],
                               spawn=refuse) == 2
    after = records.read(SESSION, record["recall"])
    line = recalls.find_line(SESSION, record["recall"])
    assert (after["state"], after["reason"]) == ("lost", "spawn")
    assert (line["state"], line["reason"], line["head"]) == ("lost", "spawn", "ingest lock")
    assert "FileNotFoundError" in line["error"]
    # a taker it can't start has no recall to lose
    assert recall_child.detach(["--take", "--session", SESSION], spawn=refuse) == 2


def _ancestors(pid):
    seen = []
    while pid and pid not in seen and len(seen) < 64:
        seen.append(pid)
        pid = moxi.proc.parent(pid)
    return seen


def test_a_process_started_through_the_starter_is_no_descendant_of_its_caller(tmp_path):
    out = tmp_path / "child.pid"
    child = tmp_path / "child.py"
    child.write_text("import os, sys, time\n"
                     "open(sys.argv[1] + '.tmp', 'w').write(str(os.getpid()))\n"
                     "os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n"
                     "time.sleep(20)\n")
    starter = ("import sys, os, moxi.proc\n"
               "from moxi_memory import recall_child\n"
               "sys.exit(recall_child.detach(sys.argv[1:], spawn=lambda a: moxi.proc.spawn("
               "[sys.executable, *a], detached=True, output=os.devnull, cwd=os.sep)))\n")
    done = subprocess.run([sys.executable, "-c", starter, str(child), str(out)],
                          timeout=30, check=False)
    assert done.returncode == 0
    deadline = time.monotonic() + 15
    while not out.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    pid = int(out.read_text())
    try:
        assert os.getpid() not in _ancestors(pid)
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
