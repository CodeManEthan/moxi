"""The route a recall's rounds take to its fork: Claude Code's subagent route, through
turns' `fork`.

`Subagent` asks the session's turns endpoint for a `fork` with the brief as its first
round and a `fork.continue` for each later one, each with its ref, `keep` and the
round's bound as `round_timeout_s`; turns starts the fork inside the session, and the
harness brings each round's answer back to the main agent itself. The child reads the
fork's id from the round's start and the round's end from its delivery, and acks each
settled round's ref through turns' client, which reaches turns' state folder when the
endpoint is gone.

`for_recall(recall)` gives the route the recall's record names.

Standard library at import; turns' client is imported at use.
"""

from __future__ import annotations

from typing import Any

from .recall_start import SUBAGENT

#: The module name memory asks turns as.
BY = "memory"


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
        self._session = None
        self._bound_s: float | None = None

    def _find(self):
        from moxi_turns import client

        if self._session is None:
            self._session = client.find(session_id=self.session_id)
        return self._session

    def _gone(self, why: str) -> _Known:
        from moxi_turns.results import Lost

        return _Known(Lost(f"gone: {why}"))

    def first(self, text: str, *, ref: str, round_timeout_s: float):
        self._bound_s = round_timeout_s
        try:
            session = self._find()
            if session is None:
                return self._gone("no live turns endpoint for the session")
            return session.fork(text, by=BY, ref=ref, keep=True,
                                round_timeout_s=round_timeout_s)
        except Exception as exc:  # noqa: BLE001 - a fork turns refused is a lost round
            from moxi_turns.results import Refused

            return _Known(Refused(f"denied: {exc}"))

    def next(self, prior, text: str, *, ref: str, round_timeout_s: float):
        self._bound_s = round_timeout_s
        delivery = getattr(prior, "id", None)
        if not delivery:
            return self._gone("the fork's earlier round has no delivery id")
        try:
            session = self._find()
            if session is None:
                return self._gone("no live turns endpoint for the session")
            return session.fork_continue(delivery, text, ref=ref, keep=True,
                                         round_timeout_s=round_timeout_s)
        except Exception as exc:  # noqa: BLE001
            from moxi_turns.results import Refused

            return _Known(Refused(f"denied: {exc}"))

    def started(self, handle) -> str | None:
        from moxi_turns.results import Started

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
        from moxi_turns import client

        client.ack(ref, session_id=self.session_id)

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None


def for_recall(recall) -> Subagent:
    """The route recall `recall`'s record names."""
    return Subagent(recall.session_id)


__all__ = ["BY", "Subagent", "for_recall"]
