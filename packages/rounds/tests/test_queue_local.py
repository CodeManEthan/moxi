"""The lander's local run (`queue_ci.QueueCi.local`) through the machine's check lock: its
ticket goes ahead of checks that asked first, it waits for a place like any run, it is
admitted beside a running check by `ci.local`'s own `memory_mib`, and it runs tagged,
named in its holder record and ended whole. Every lock is in the test's own state
folder; the memory reading is injected."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from moxi import proc
from test_check_run_ends import POSIX, command, ident, leftover, signalled
from test_check_slots import hold

from moxi_rounds import check_line, checking, ci, queue_ci

PY = sys.executable
VALUES = {"round": "queue/proj-a", "stage": "b7", "repo": "proj-a"}


def local_ci(tmp_path: Path, code: str, *, memory_mib: int | None = None,
             at_once: int = 1, said: list | None = None) -> queue_ci.QueueCi:
    """A `QueueCi` whose `ci.local` runs a script holding `code`."""
    script = tmp_path / "local.py"
    script.write_text(code, encoding="utf-8")
    found = ci.Declared(workflows=(), local=(PY, str(script)), local_timeout_s=60,
                        remote=None, status=(), ids=(), status_timeout_s=1,
                        local_memory_mib=memory_mib)
    return queue_ci.QueueCi(found, local_log=tmp_path / "local.log", at_once=at_once,
                            say=None if said is None else said.append)


def test_the_local_run_goes_ahead_of_tickets_that_came_first_and_waits_for_a_place(
        fenced, holder, tmp_path):
    lock = checking.lock_file()
    held = holder(lock)
    earlier = check_line.take(lock, {**check_line.me(), "round": "proj-a/earlier",
                                     "stage": "B1", "step": "check"})
    said: list[str] = []
    threading.Timer(1.0, held.stdin.close).start()

    done = local_ci(tmp_path, "print('local ran')", said=said).local(tmp_path, VALUES)

    assert done.exit_code == 0 and done.held == {"check_lock": 1}
    assert said[0] == (f"waiting for the machine's check lock, held by pid {held.pid}; "
                       "0 ahead of you")
    assert said[1].startswith("got the check lock after ")
    assert [one.path for one in check_line.tickets(lock)] == [earlier]
    assert not check_line.holder_file(lock).exists()


def test_the_local_run_is_admitted_beside_a_check_by_its_own_memory_mib(
        fenced, holder, tmp_path, monkeypatch):
    hold(holder, 1, memory_mib=100)
    monkeypatch.setattr(check_line, "available_mib", lambda: 300)

    done = local_ci(tmp_path, "pass", memory_mib=150, at_once=2).local(tmp_path, VALUES)

    assert done.exit_code == 0
    assert done.held == {"check_lock": 2, "available_mib": 300, "need_mib": 250}


def test_a_local_run_with_no_memory_mib_waits_until_it_would_be_alone(
        fenced, holder, tmp_path, monkeypatch):
    held = hold(holder, 1, memory_mib=100)
    monkeypatch.setattr(check_line, "available_mib", lambda: 1_000_000)
    said: list[str] = []
    threading.Timer(1.0, held.stdin.close).start()

    done = local_ci(tmp_path, "pass", at_once=2, said=said).local(tmp_path, VALUES)

    assert done.held == {"check_lock": 1}
    assert said[0] == ("check lock 2 of 2 is free, but this check declares no memory_mib, "
                       "so it runs only alone")
    assert said[-1].startswith("got check lock 1 of 2 after ") and said[-1].endswith(
        ", alone")


@POSIX
def test_the_local_run_is_tagged_named_and_ended_whole(fenced, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    holder_file = check_line.holder_file(checking.lock_file())
    code = command(out, setsid=True, then=f"""
import json
record = json.loads(pathlib.Path({str(holder_file)!r}).read_text(encoding="utf-8"))
(out / "record").write_text(json.dumps(record))
""")

    done = local_ci(tmp_path, code).local(tmp_path, VALUES)

    assert done.exit_code == 0
    left = leftover(out)
    record = leftover(out, "record")
    assert record["run_tag"] == left["tag"]
    assert (record["round"], record["stage"], record["step"], record["repo"]) == (
        "queue/proj-a", "b7", ci.LOCAL, "proj-a")
    assert record["run_pid"] == int((out / "first").read_text())
    assert record["output"] == str(tmp_path / "local.log")
    assert signalled(out) == {"signal": "SIGTERM", "held": True}
    assert not proc.alive(ident(left)) and not holder_file.exists()


def test_a_local_of_none_takes_no_place(fenced, tmp_path):
    found = ci.Declared(workflows=(), local=None, local_timeout_s=None, remote=None,
                        status=(), ids=(), status_timeout_s=1)
    queue = queue_ci.QueueCi(found, local_log=tmp_path / "local.log")

    assert queue.local(tmp_path, VALUES) is None
    assert not checking.lock_file().parent.exists() or check_line.tickets(
        checking.lock_file()) == []

