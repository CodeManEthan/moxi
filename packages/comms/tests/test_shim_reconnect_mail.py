"""Mail waiting for a Claude session when its comms shim reconnects to the daemon is
injected once the new connection is installed, and only once.

At a re-register the daemon answers `bind` and hands the waiting mail right after the
answer. The shim's reader thread queues it, and that can happen before the registering
thread installs the new client. A flush in that gap (a turn end, the idle tick, or the
tick steering interrupt mail into a running turn) holds while the shim is offline, so
the digest is opened on the new client. Opened on the old, closed one it would be
dropped, and the daemon, which counts the id as handed to the live binding, would not
hand it again until the next registration.

The test forces that order with no load: it holds the supervisor for a second between
the bind's answer and the install. While it is held, once the re-handed message is
queued, the flush runs on another thread. After the install the test ticks as the Claude
adapter's idle loop would.
"""

from __future__ import annotations

import itertools
import threading
import time

import pytest
from conftest import WAIT, wait_until

from moxi_comms import daemon as daemon_mod
from moxi_comms.adapters import binding

FAST = 0.05
HOLD_S = 1.0
AFTER_S = 3.0                     # ticks after the install, looking for the message
TICK_S = 0.1                      # the Claude adapter's idle poll
MARK = "WAITING-AT-RECONNECT"


def _turn_end(shim):
    return shim.on_turn_boundary()


def _idle(shim):
    return shim.on_turn_boundary(idle=True)


def _interrupt(shim):
    return shim.flush_interrupt()


#: The flush that runs in the held window, and the one the adapter's tick runs after the
#: install: the idle boundary, or for interrupt mail in a running turn, the steer.
FLUSHES = {
    "turn_end": (_turn_end, _idle),
    "idle": (_idle, _idle),
    "interrupt": (_interrupt, _interrupt),
}


class InstallHold:
    """Holds a register for `HOLD_S` between the daemon's answer and the install, once
    armed. While held, once `MARK` is queued, it runs `flush` on another thread.
    `released_at` is when the held register went on to install."""

    def __init__(self, monkeypatch, shim, flush):
        self.armed = threading.Event()
        self.released_at: float | None = None
        self.queued_in_window = False
        self.offline_in_window = False
        original = binding.register_and_install

        def register_and_install(owner, *, accept=None, **kw):
            inner = accept

            def held_accept(resp):
                if inner is not None:
                    inner(resp)
                if not self.armed.is_set():          # read at the answer, not at the call
                    return
                self.armed.clear()
                started = time.monotonic()
                self.queued_in_window = wait_until(
                    lambda: any(MARK in str(p) for p in list(shim.queue)), HOLD_S)
                self.offline_in_window = shim.offline
                runner = threading.Thread(target=flush, args=(shim,),
                                          name="turns-watch-stand-in")
                runner.start()
                runner.join(HOLD_S)
                left = HOLD_S - (time.monotonic() - started)
                if left > 0:
                    time.sleep(left)
                self.released_at = time.monotonic()

            return original(owner, accept=held_accept, **kw)

        monkeypatch.setattr(binding, "register_and_install", register_and_install)


@pytest.mark.parametrize("way", list(FLUSHES))
def test_mail_waiting_at_a_reconnect_is_injected_once_after_the_install(
        rig, monkeypatch, way):
    """A bound shim holds one handed, unhandled message when the daemon restarts. The
    new daemon hands it again at the bind, and the install is held a second past the
    answer while a flush runs. The message is injected once, after the install."""
    in_window, after = FLUSHES[way]
    shim = rig.shim(name="reviewer", scope="proj-a", autoreconnect=True,
                    backoff=itertools.repeat(FAST))
    assert wait_until(lambda: not shim.offline, WAIT), "the shim bound"
    injected_at: list = []

    def injector(text):
        shim.digests.append(text)
        if MARK in text:
            injected_at.append(time.monotonic())

    shim.injector = injector
    hold = InstallHold(monkeypatch, shim, in_window)

    flags = ("--interrupt",) if way == "interrupt" else ()
    code, out = rig.send("reviewer@proj-a", MARK, *flags,
                         sender=dict(name="main", scope="proj-a"))
    assert code == 0, out
    # Handed and queued; no boundary runs, so its row stays unhandled.
    assert wait_until(lambda: shim.queue, WAIT), "the daemon handed the message"

    rig.daemon.stop()
    assert wait_until(lambda: shim.offline, WAIT), "the shim saw the daemon go"
    hold.armed.set()
    rig.daemon = daemon_mod.Daemon(rig.record, clock=rig.clock,
                                   start_trim_thread=False).start()

    assert wait_until(lambda: hold.released_at is not None and not shim.offline, WAIT), \
        "the shim reconnected after the held install"
    assert hold.queued_in_window and hold.offline_in_window, \
        "the message was queued while the shim was offline, before the install"

    deadline = time.monotonic() + AFTER_S
    while time.monotonic() < deadline and not injected_at:
        after(shim)
        time.sleep(TICK_S)

    assert injected_at, "the message waiting at the reconnect was injected"
    assert injected_at[0] >= hold.released_at, \
        "injected on the installed connection, not before it"
    assert sum(MARK in d for d in shim.digests) == 1, "injected once"
