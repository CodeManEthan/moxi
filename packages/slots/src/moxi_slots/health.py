"""The server's health check, the `HEALTH` entry the doctor runs on demand.

It asks the server for its pid (`display-message -p '#{pid}'`) with a two-second bound,
and fails on no answer or on a pid other than the supervisor's: a second tmux holding the
socket is not the server the supervisor runs. Where no supervisor runs, the pid compared
is the one the wrapper wrote as it started.
"""

from __future__ import annotations

from moxi.doctor import Row

from . import places, server, support, tmux
from .errors import TmuxError

NAME = "health server"


def expected_pid() -> tuple[int | None, str]:
    """The pid the server should have, and whose word that is."""
    if server.supervised():
        state = server.unit_state()
        if state is not None and state.pid:
            return state.pid, "the supervisor's"
        if state is not None and state.state == "nobackend":
            return server.recorded_pid(), "the one the wrapper recorded"
        return None, (f"the supervisor's (its unit is {state.state})" if state is not None
                      else "the supervisor's (unread)")
    return server.recorded_pid(), "the one the wrapper recorded"


def check() -> Row:
    """One row: ok when the server answers with the expected pid."""
    if not support.system_ok():
        return Row(places.MODULE, NAME, "off", support.unsupported_words())
    socket = places.socket()
    try:
        pid = tmux.ping(tmux.PING_TIMEOUT)
    except TmuxError as exc:
        return Row(places.MODULE, NAME, "fail", str(exc), f"moxi install {places.MODULE}")
    if pid is None:
        return Row(places.MODULE, NAME, "fail",
                   f"no tmux server answered on {socket} within {tmux.PING_TIMEOUT:g} s")
    want, whose = expected_pid()
    if want is None:
        return Row(places.MODULE, NAME, "warn",
                   f"tmux (pid {pid}) answers on {socket}, but no pid to compare it with: "
                   f"{whose}")
    if pid != want:
        return Row(places.MODULE, NAME, "fail",
                   f"the tmux answering on {socket} is pid {pid}, not {whose}, {want}: "
                   "another tmux server holds the socket")
    return Row(places.MODULE, NAME, "ok", f"tmux (pid {pid}) answers on {socket}")


__all__ = ["NAME", "expected_pid", "check"]
