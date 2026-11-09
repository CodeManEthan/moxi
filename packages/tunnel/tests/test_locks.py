"""tunnel's two locks: the one order, the waits, threads taking turns, no lock while a user
is asked, and an uninstall holding the core lock beside an act, either way round."""

from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from moxi import files, paths
from moxi_tunnel import locks

#: A child that takes the locks named in argv (core, tunnel) in the one order, prints
#: "held", and lets them go when its stdin gets a line.
CHILD = """
import sys
from contextlib import ExitStack
from moxi import files, paths
from moxi_tunnel import locks
with ExitStack() as stack:
    if "core" in sys.argv[1:]:
        stack.enter_context(files.lock(paths.core_lock(), timeout_s=10))
    if "tunnel" in sys.argv[1:]:
        stack.enter_context(files.lock(locks.lock_path(), timeout_s=10))
    print("held", flush=True)
    sys.stdin.readline()
"""


def child(*which: str) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-c", CHILD, *which], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


def release(proc: subprocess.Popen) -> None:
    proc.stdin.write("\n")
    proc.stdin.flush()
    assert proc.wait(timeout=10) == 0


def test_held_takes_the_core_lock_before_tunnel_lock():
    assert not locks.holds_core() and not locks.holds_tunnel()
    with locks.held(core=True, timeout=1):
        assert locks.holds_core() and locks.holds_tunnel()
        # The core's own nested take yields to this hold, as config.set's does.
        with files.lock(paths.core_lock(), nested=True, timeout_s=0):
            pass
    with locks.held(timeout=1):
        assert locks.holds_tunnel() and not locks.holds_core()
    assert not locks.holds_core() and not locks.holds_tunnel()


def test_the_core_lock_is_never_taken_under_tunnel_lock():
    with locks.tunnel(timeout=1):
        with pytest.raises(RuntimeError, match="before tunnel.lock"):
            with locks.core(timeout=1):
                pass
        with pytest.raises(RuntimeError, match="not re-entrant"):
            with locks.tunnel(timeout=1):
                pass
    with locks.core(timeout=1):
        with locks.core(timeout=1):
            assert locks.holds_core()
        assert locks.holds_core()


def test_no_lock_is_held_while_a_user_is_asked():
    locks.none_held()
    for core in (False, True):
        with locks.held(core=core, timeout=1):
            with pytest.raises(RuntimeError, match="no lock held"):
                locks.none_held()
    with locks.core(timeout=1):
        with pytest.raises(RuntimeError):
            locks.none_held()


@pytest.mark.parametrize("which, core", [(("tunnel",), False), (("core",), True)])
def test_a_lock_held_past_the_wait_names_its_holder(which, core):
    proc = child(*which)
    try:
        with pytest.raises(files.LockTimeout) as caught:
            with locks.held(core=core, timeout=0.2):
                pass
        assert caught.value.holder == proc.pid and caught.value.exit_code == 75
        assert not locks.holds_core() and not locks.holds_tunnel()
    finally:
        release(proc)


def test_threads_of_one_process_take_turns_for_tunnel_lock():
    done = threading.Event()
    order = []

    def second():
        with locks.tunnel(timeout=5):
            order.append("second")
        done.set()

    with locks.tunnel(timeout=1):
        thread = threading.Thread(target=second)
        thread.start()
        assert not done.wait(0.1)
        order.append("first")
    assert done.wait(5)
    thread.join()
    assert order == ["first", "second"]
    with locks.tunnel(timeout=1):
        holder = []
        thread = threading.Thread(target=lambda: holder.append(_try(0.1)))
        thread.start()
        thread.join()
    assert isinstance(holder[0], files.LockTimeout)


def _try(timeout):
    try:
        with locks.tunnel(timeout=timeout):
            return None
    except files.LockTimeout as exc:
        return exc


def test_an_uninstall_holding_the_core_lock_and_an_act_wait_on_each_other_in_turn():
    # The uninstall holds the core lock, then takes tunnel.lock as its hooks do; an act
    # that needs both waits for it and then goes on.
    proc = child("core", "tunnel")
    got = []
    thread = threading.Thread(target=lambda: got.append(_act(5)))
    thread.start()
    assert not got
    release(proc)
    thread.join(10)
    assert got == ["acted"]
    # The reverse: an act holds both, and the uninstall's core lock waits for it.
    with locks.held(core=True, timeout=1):
        waiter = subprocess.Popen([sys.executable, "-c", CHILD, "core", "tunnel"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        with pytest.raises(subprocess.TimeoutExpired):
            waiter.wait(timeout=0.3)
    assert waiter.stdout.readline().strip() == "held"
    release(waiter)


def _act(timeout):
    with locks.held(core=True, timeout=timeout):
        return "acted"
