"""The route a recall's rounds take to its fork: Claude Code's subagent route, through
turns' `fork`, or the slot route, a fork of the session in a slot.

`Subagent` asks the session's turns endpoint for a `fork` with the brief as its first
round and a `fork.continue` for each later one, each with its ref, `keep` and the
round's bound as `round_timeout_s`; turns starts the fork inside the session, and the
harness brings each round's answer back to the main agent itself. The child reads the
fork's id from the round's start and the round's end from its delivery, and acks each
settled round's ref through turns' client, which reaches turns' state folder when the
endpoint is gone.

`Slot` forks the session into a slot through the launcher's interface 3
(`launcher.api.fork`, target `slot`, the fork stamped `SUBCONSCIOUS_FORK_ENV` with the
recall's id), waits for the fork's turns endpoint, and delivers the brief to it as a
turn with the round's ref and `keep`; each later round is the next delivery to the same
fork. The fork's id is its session id, known once the launcher returns, so a fork the
child started is named even when its first round never comes back. Each round's end,
with the fork's last text as its answer, is read from the delivery, and acked by ref
in the fork's session. Its answers go back to the main agent by comms, sent by the
child (`recall_stream`). Once the recall's last round is settled, its owner ends the
fork (`Slot.exit`, through the launcher's `exit_session`), which the launcher does once
the fork's turn has ended: the child after the stream's end, a taker after it settles
the line, and a discard of a line whose owner is gone (`end_fork`). A fork whose round is
still open at the late wait's last limit is not exited: its turn has not ended, and the
taker that settles the line ends it.

Each route names the session its rounds are delivered to (`turns_session`), where a
taker reads and acks a round's end by its ref.

`for_recall(recall)` gives the route the recall's record names (or its log line, for a
record already swept): `Slot` for `slot`, `Subagent` otherwise.

Standard library at import; turns' client is imported at use.
"""

from __future__ import annotations

import time
from typing import Any

from .recall_start import SLOT, SUBAGENT, SUBCONSCIOUS_FORK_ENV

#: The module name memory asks turns as.
BY = "memory"
#: How a slot fork's exit came out when the launcher ended it (the line's `fork_exit`).
EXITED = "exited"
#: The launcher's verdict while something still holds a session (a turn, a person).
HELD = "held"
#: An exit the launcher held is asked again every `EXIT_RETRY_S` seconds for at most
#: `EXIT_HELD_S` seconds by the recall's owner; a discard asks once.
EXIT_RETRY_S = 5
EXIT_HELD_S = 60


class _Known:
    """A round turns never took, with the end it reads as."""

    def __init__(self, value: Any):
        self.value = value
        self.id = None


class Subagent:
    """Claude Code's subagent route for one session's recall."""

    name = SUBAGENT

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._session_obj = None
        self._bound_s: float | None = None

    @property
    def turns_session(self) -> str:
        return self.session_id

    def _find(self):
        import moxi_turns as client

        if self._session_obj is None:
            self._session_obj = client.find(session_id=self.session_id)
        return self._session_obj

    def _gone(self, why: str) -> _Known:
        from moxi_turns import Lost

        return _Known(Lost(f"gone: {why}"))

    def first(self, text: str, *, ref: str, round_timeout_s: float):
        self._bound_s = round_timeout_s
        try:
            session_obj = self._find()
            if session_obj is None:
                return self._gone("no live turns endpoint for the session")
            return session_obj.fork(text, by=BY, ref=ref, keep=True,
                                    round_timeout_s=round_timeout_s)
        except Exception as exc:  # noqa: BLE001 - a fork turns refused is a lost round
            from moxi_turns import Refused

            return _Known(Refused(f"denied: {exc}"))

    def next(self, prior, text: str, *, ref: str, round_timeout_s: float):
        self._bound_s = round_timeout_s
        delivery = getattr(prior, "id", None)
        if not delivery:
            return self._gone("the fork's earlier round has no delivery id")
        try:
            session_obj = self._find()
            if session_obj is None:
                return self._gone("no live turns endpoint for the session")
            return session_obj.fork_continue(delivery, text, ref=ref, keep=True,
                                             round_timeout_s=round_timeout_s)
        except Exception as exc:  # noqa: BLE001
            from moxi_turns import Refused

            return _Known(Refused(f"denied: {exc}"))

    def started(self, handle) -> str | None:
        from moxi_turns import Started

        if isinstance(handle, _Known):
            return None
        try:
            value = handle.wait_started(self._bound_s)
        except Exception:  # noqa: BLE001 - a start not read leaves the id unknown
            return None
        return value.agent_id if isinstance(value, Started) else None

    def ended(self, handle, timeout_s: float):
        if isinstance(handle, _Known):
            return handle.value
        return handle.wait_ended(timeout_s)

    def ack(self, ref: str) -> None:
        import moxi_turns as client

        client.ack(ref, session_id=self.session_id)

    def close(self) -> None:
        if self._session_obj is not None:
            self._session_obj.close()
            self._session_obj = None


class Slot:
    """The slot route for one session's recall: a fork of the session in a slot."""

    name = SLOT

    def __init__(self, session_id: str, recall_id: str, *, fork: str | None = None,
                 launcher: Any = None):
        self.session_id = session_id
        self.recall_id = recall_id
        self.fork = fork
        self._launcher = launcher
        self._session_obj = None

    @property
    def turns_session(self) -> str:
        return self.fork or self.session_id

    def _api(self):
        if self._launcher is None:
            from . import links

            link = links.launcher_forks()
            if not link.ready:
                raise RuntimeError(link.reason or "launcher without interface 3")
            self._launcher = link.api
        return self._launcher

    def _gone(self, why: str) -> _Known:
        from moxi_turns import Lost

        return _Known(Lost(f"gone: {why}"))

    def _deliver(self, text: str, *, ref: str, ready_s: float):
        import moxi_turns as client

        if self._session_obj is None:
            self._session_obj = client.wait_ready(session_id=self.fork, timeout_s=ready_s)
        return self._session_obj.deliver(text, by=BY, ref=ref, keep=True)

    def first(self, text: str, *, ref: str, round_timeout_s: float):
        """Fork the session into a slot and deliver the brief as the fork's first turn.
        The round's bound runs from the delivery, which this returns after."""
        try:
            result = self._api().fork(self.session_id, target="slot",
                                      stamps={SUBCONSCIOUS_FORK_ENV: self.recall_id})
        except Exception as exc:  # noqa: BLE001 - a fork the launcher refused is lost
            from moxi_turns import Refused

            return _Known(Refused(f"denied: the launcher: {exc}"))
        self.fork = getattr(result, "session_id", None) or None
        if self.fork is None:
            return self._gone("the launcher started no fork with a session id")
        return self._send(text, ref=ref, ready_s=round_timeout_s)

    def next(self, prior, text: str, *, ref: str, round_timeout_s: float):
        if self.fork is None:
            return self._gone("the recall has no slot fork")
        return self._send(text, ref=ref, ready_s=round_timeout_s)

    def _send(self, text: str, *, ref: str, ready_s: float):
        try:
            return self._deliver(text, ref=ref, ready_s=ready_s)
        except TimeoutError:
            return self._gone("the slot fork's turns endpoint was not ready in time")
        except Exception as exc:  # noqa: BLE001 - a delivery turns refused is lost
            from moxi_turns import Refused

            return _Known(Refused(f"denied: {exc}"))

    def started(self, handle) -> str | None:
        return self.fork

    def ended(self, handle, timeout_s: float):
        if isinstance(handle, _Known):
            return handle.value
        return handle.wait_ended(timeout_s)

    def ack(self, ref: str) -> None:
        import moxi_turns as client

        client.ack(ref, session_id=self.turns_session)

    def close(self) -> None:
        if self._session_obj is not None:
            self._session_obj.close()
            self._session_obj = None

    def exit(self, *, held_s: float = EXIT_HELD_S, sleep=time.sleep,
             clock=time.monotonic) -> str | None:
        """End the slot fork through the launcher's `exit_session`, and return how it
        came out: `exited`, `held: <what holds it>` once `held_s` has passed with the
        launcher still holding it, another verdict, or the refusal; None when the recall
        has no fork. Never raises."""
        if self.fork is None:
            return None
        began = clock()
        while True:
            try:
                result = self._api().exit_session(self.fork)
            except Exception as exc:  # noqa: BLE001 - a refusal is noted, never raised
                code = getattr(exc, "code", None) or type(exc).__name__
                return f"refused ({code}): {getattr(exc, 'problem', None) or exc}"
            data = getattr(result, "data", None) or {}
            outcome = str(data.get("outcome") or "unknown")
            if outcome != HELD:
                return outcome
            if clock() - began + EXIT_RETRY_S > held_s:
                return f"{HELD}: {data.get('word') or 'no word'}"
            sleep(EXIT_RETRY_S)


def end_fork(recall, route, *, held_s: float = EXIT_HELD_S, sleep=time.sleep) -> str | None:
    """End `route`'s fork when it is a slot fork, and note how on recall `recall`'s line
    (`fork_exit`). None, writing nothing, on a route with no fork of its own to end.
    Never raises."""
    leave = getattr(route, "exit", None)
    if leave is None:
        return None
    outcome = leave(held_s=held_s, sleep=sleep)
    if outcome is not None:
        try:
            recall.put(fork_exit=outcome)
        except Exception:  # noqa: BLE001 - a line not written leaves the outcome unsaid
            pass
    return outcome


def for_recall(recall) -> Subagent | Slot:
    """The route recall `recall`'s record names, else its log line."""
    record = getattr(recall, "record", None) or {}
    line = getattr(recall, "line", None) or {}
    name = record.get("route") or line.get("route")
    if name == SLOT:
        fork = record.get("fork") or line.get("fork")
        return Slot(recall.session_id, recall.recall_id, fork=fork)
    return Subagent(recall.session_id)


__all__ = ["BY", "EXITED", "HELD", "EXIT_RETRY_S", "EXIT_HELD_S", "Subagent", "Slot",
           "end_fork", "for_recall"]
