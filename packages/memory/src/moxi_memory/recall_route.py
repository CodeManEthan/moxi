"""The route a recall's rounds take to its fork: Claude Code's subagent route, through
turns' `fork`, Codex's host fork, through the same requests, the slot route, a fork of
the session in a slot, or a chat recall's headless route.

`Subagent` asks the session's turns endpoint for a `fork` with the brief as its first
round and a `fork.continue` for each later one, each with its ref, `keep` and the
round's bound as `round_timeout_s`; turns starts the fork inside the session, and the
harness brings each round's answer back to the main agent itself. The child reads the
fork's id from the round's start and the round's end from its delivery, and acks each
settled round's ref through turns' client, which reaches turns' state folder when the
endpoint is gone.

`HostFork` is Codex's host fork: the same `fork` and `fork.continue` requests on the
Codex session, whose turns host forks the main thread and runs each round as a turn on
the forked thread, the round's end carrying that thread's last agent message as its
answer. Codex gives the forked thread no way back to its parent, so the answers go back
by comms, sent by the child (`recall_stream`), as on the slot route; and as there the
child waits each round's bound itself, so a late answer is still read: turns is given
its largest round bound (`HOST_FORK_TURNS_BOUND_S`), and a round past memory's bound
stays open until the forked thread's turn ends or the session's endpoint does. A host
fork ends when it is given no further turn, so it has no exit of its own. turns' Codex
host offers no `fork` yet, and a Codex session's recall is off unless its launch picks
the slot route (`recall_start.route`), so no recall runs on it today; a recall whose
record names it runs it.

`Slot` forks the session into a slot through the launcher's interface 3
(`launcher.api.fork`, target `slot`, the fork stamped `SUBCONSCIOUS_FORK_ENV` with the
recall's id), waits for the fork's turns endpoint, and delivers the brief to it as a
turn with the round's ref and `keep`; each later round is the next delivery to the same
fork. The fork's id is its session id, known once the launcher returns (`launch`), and
the stream writes it on the record and the line then, before the fork's endpoint is
waited for and the brief delivered: a fork the child started is named even when the
child dies before its first round is delivered, so a taker reads the round in the
fork's session and exits the fork. Each round's end,
with the fork's last text as its answer, is read from the delivery, and acked by ref
in the fork's session. Its answers go back to the main agent by comms, sent by the
child (`recall_stream`). Once the recall's last round is settled, its owner ends the
fork (`Slot.exit`, through the launcher's `exit_session`), which the launcher does once
the fork's turn has ended: the child after the stream's end, a taker after it settles
the line, and a discard of a line whose owner is gone (`end_fork`). A fork whose round is
still open at the late wait's last limit is not exited: its turn has not ended, and the
taker that settles the line ends it.

`Headless` is a chat recall's route: each round a launcher headless launch of the
recall's own agent through the launcher's interface 4, under the read-only fence
(`moxi_memory.recall_fence.request`, stamped as the recall's fork), the first round a
fresh launch with the session id memory minted, each later one a headless resume of that
session (`continue_headless`). Each round's request id is minted and handed to the
caller (`on_request`, which writes it on the line) before the round is launched. A
round's end is read once its process has exited, from the last `result` object of its
run log: its text is the answer and its `total_cost_usd` the round's cost
(`round_fields`); no result, or an error result, is `Lost` (`gone`). Past the round's
bound the route ends the launch's process tree (`end_headless`) and answers `Lost`
(`gone`). No turns endpoint keeps a headless round's end, so `ack` does nothing, and a
taker reads an unsettled round's end from the run log its line's `request` names
(`read_end`), leaving the line while that launch still runs. `exit` ends the current
round's launch through `end_headless`.

Each route names the session its rounds are delivered to (`turns_session`), where a
taker reads and acks a round's end by its ref.

`for_recall(recall)` gives the route the recall's record names (or its log line, for a
record already swept): `Slot` for `slot`, `HostFork` for `host-fork`, `Headless` for
`headless` (a taker's, built from the line), `Subagent` otherwise.

Standard library at import; turns' client is imported at use.
"""

from __future__ import annotations

import time
from typing import Any

from .recall_start import HEADLESS, HOST_FORK, SLOT, SUBAGENT, SUBCONSCIOUS_FORK_ENV

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
#: The round bound memory gives turns on Codex's host fork, in seconds: the largest turns
#: takes (`2**31 - 1` ms, whole seconds), so turns never ends a round memory still
#: waits on or a late answer could still settle. A copy, since turns exports no
#: maximum: turns' own tests pin the value this copies.
HOST_FORK_TURNS_BOUND_S = (2**31 - 1) // 1000
#: How often the headless route looks whether a round's process has exited, in seconds.
HEADLESS_POLL_S = 0.2


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

    def _turns_bound(self, round_timeout_s: float) -> float:
        """The round bound turns is given for a round memory bounds at `round_timeout_s`."""
        return round_timeout_s

    def first(self, text: str, *, ref: str, round_timeout_s: float):
        self._bound_s = round_timeout_s
        try:
            session_obj = self._find()
            if session_obj is None:
                return self._gone("no live turns endpoint for the session")
            return session_obj.fork(text, by=BY, ref=ref, keep=True,
                                    round_timeout_s=self._turns_bound(round_timeout_s))
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
            return session_obj.fork_continue(
                delivery, text, ref=ref, keep=True,
                round_timeout_s=self._turns_bound(round_timeout_s))
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


class HostFork(Subagent):
    """Codex's host fork for one session's recall: turns' `fork` and `fork.continue` on
    the Codex session, each round a turn on the forked thread, its answer sent by comms.
    The child waits each round's bound itself; turns is given its largest."""

    name = HOST_FORK

    def _turns_bound(self, round_timeout_s: float) -> float:
        return float(HOST_FORK_TURNS_BOUND_S)


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
        self._unlaunched = None

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

    def launch(self) -> str | None:
        """Fork the session into a slot, once, and return the fork's session id as soon
        as the launcher returns, before its endpoint is waited for: None when the
        launcher refused or started no fork with a session id, which `first` then
        answers. A route with a fork already does nothing."""
        if self.fork is not None or self._unlaunched is not None:
            return self.fork
        try:
            result = self._api().fork(self.session_id, target="slot",
                                      stamps={SUBCONSCIOUS_FORK_ENV: self.recall_id})
        except Exception as exc:  # noqa: BLE001 - a fork the launcher refused is lost
            from moxi_turns import Refused

            self._unlaunched = _Known(Refused(f"denied: the launcher: {exc}"))
            return None
        self.fork = getattr(result, "session_id", None) or None
        if self.fork is None:
            self._unlaunched = self._gone("the launcher started no fork with a session id")
        return self.fork

    def first(self, text: str, *, ref: str, round_timeout_s: float):
        """Fork the session into a slot (`launch`, when the caller hasn't) and deliver
        the brief as the fork's first turn. The round's bound runs from the delivery,
        which this returns after."""
        self.launch()
        if self._unlaunched is not None:
            return self._unlaunched
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


class _Launched:
    """A headless round the launcher started: its request id, its process (None when
    it had exited before it was read) and its bound's deadline on the monotonic clock."""

    def __init__(self, request_id: str, root: Any, deadline: float):
        self.request_id = request_id
        self.root = root
        self.deadline = deadline


class Headless:
    """The headless route for one chat recall: each round a launcher headless launch of
    its own agent (the launcher's interface 4), the first fresh and each later one a
    headless resume of the same session, the answer read from the round's run log.

    `spec` is the recall's `recall_fence.Spec` with its minted session id (its brief and
    request id are each round's own); `on_request` is called with each round's minted
    request id before that round is launched, so the line names it first. A taker builds
    the route from the line alone (`session`, `request`), to read and end the round the
    line names."""

    name = HEADLESS

    def __init__(self, session_id: str, recall_id: str, *, spec: Any = None,
                 session: str | None = None, request: str | None = None,
                 on_request=None, launcher: Any = None, clock=time.monotonic,
                 sleep=time.sleep):
        self.session_id = session_id
        self.recall_id = recall_id
        self.spec = spec
        self.session = session or (spec.session_id if spec is not None else None)
        self.request = request
        self.on_request = on_request
        self._launcher = launcher
        self._clock = clock
        self._sleep = sleep
        self._fields: dict = {}

    @property
    def turns_session(self) -> str:
        return self.session_id

    def _api(self):
        if self._launcher is None:
            from . import links

            link = links.launcher_headless()
            if not link.ready:
                raise RuntimeError(link.reason or "launcher without interface 4")
            self._launcher = link.api
        return self._launcher

    def _mint(self) -> str:
        import uuid

        rid = str(uuid.uuid4())
        if self.on_request is not None:
            self.on_request(rid)
        self.request = rid
        self._fields = {"request": rid}
        return rid

    def _started(self, result, rid: str, round_timeout_s: float):
        from moxi import proc
        from moxi_turns import Lost

        pid = getattr(result, "pid", None)
        if pid is None:
            why = getattr(result, "reason", None) or getattr(result, "state", None)
            return _Known(Lost(f"gone: the launcher started no process ({why})"))
        return _Launched(rid, proc.identity(int(pid)), self._clock() + round_timeout_s)

    def first(self, text: str, *, ref: str, round_timeout_s: float):
        """Launch the recall's agent fresh, with `text` as its brief."""
        from dataclasses import replace

        from moxi_turns import Refused

        from . import recall_fence

        try:
            launcher = self._api()
            rid = self._mint()
            req = recall_fence.request(launcher, replace(self.spec, brief=text,
                                                         request_id=rid))
            result = launcher.launch(req)
        except Exception as exc:  # noqa: BLE001 - a launch the launcher refused is lost
            return _Known(Refused(f"denied: the launcher: {exc}"))
        return self._started(result, rid, round_timeout_s)

    def next(self, prior, text: str, *, ref: str, round_timeout_s: float):
        """Resume the agent's session headless with `text` as its prompt."""
        from moxi_turns import Refused

        from . import recall_fence

        if self.session is None:
            return self._gone("the recall has no headless session")
        try:
            launcher = self._api()
            rid = self._mint()
            result = launcher.continue_headless(self.session, text, request_id=rid,
                                                by=recall_fence.ACTOR)
        except Exception as exc:  # noqa: BLE001 - a resume the launcher refused is lost
            return _Known(Refused(f"denied: the launcher: {exc}"))
        return self._started(result, rid, round_timeout_s)

    def _gone(self, why: str) -> _Known:
        from moxi_turns import Lost

        return _Known(Lost(f"gone: {why}"))

    def started(self, handle) -> str | None:
        return None if isinstance(handle, _Known) else self.session

    def ended(self, handle, timeout_s: float):
        """The round's end: wait for its process to exit, at most its bound, and read
        the run log. Past the bound, end the launch's process tree and answer `Lost`."""
        from moxi import proc

        if isinstance(handle, _Known):
            return handle.value
        while handle.root is not None and proc.alive(handle.root):
            if self._clock() >= handle.deadline:
                self._api().end_headless(handle.request_id)
                return self._gone("the round's bound passed with no answer").value
            self._sleep(HEADLESS_POLL_S)
        return self._read(handle.request_id)

    def _read(self, request_id: str):
        """The end the run log of `request_id` holds: turns' `Ended` with the last
        `result` object's text (its cost kept for the round), else `Lost`."""
        from moxi_turns import Ended

        self._fields = {"request": request_id}
        try:
            text = self._api().run_log(request_id).read_bytes().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - a run log not read gives no answer
            return self._gone(f"the run log can't be read: {exc}").value
        found = last_result(text)
        if found is None:
            return self._gone("the agent exited with no result").value
        cost = found.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            self._fields = {**self._fields, "cost_usd": float(cost)}
        if found.get("is_error") or not isinstance(found.get("result"), str):
            why = found.get("subtype") or "an error"
            return self._gone(f"the agent ended with {why}").value
        return Ended(turn_id=None, outcome="completed", answer=found["result"])

    def round_fields(self) -> dict:
        """What the round just settled keeps on its entry: its request id and cost."""
        return dict(self._fields)

    def ack(self, ref: str) -> None:
        """Nothing: no turns endpoint keeps a headless round's end."""

    def read_end(self, ref: str):
        """A taker's read of the round the line names, without waiting: its end from the
        run log of the line's `request` once its launch no longer runs. Raises
        `TimeoutError` while it runs."""
        if self.request is None:
            return self._gone("the round was never launched").value
        try:
            live = {r.request_id for r in self._api().running()}
        except Exception:  # noqa: BLE001 - a launch log not read: left for the next taker
            raise TimeoutError("the launcher can't say whether the round runs") from None
        if self.request in live:
            raise TimeoutError("the round's agent still runs")
        return self._read(self.request)

    def read_start(self, ref: str):
        """A taker's read of the round's start: the agent's session, as turns' value."""
        from moxi_turns import Started

        if self.session is None:
            raise LookupError("the line names no headless session")
        return Started(turn_id=None, ref=ref, agent_id=self.session)

    def exit(self, *, held_s: float = EXIT_HELD_S, sleep=time.sleep) -> str | None:
        """End the current round's launch through the launcher's `end_headless`, and
        return how it came out (`ended`, or `failed: <why>`); None when the recall
        launched nothing or its launch has already ended. Never raises."""
        if self.request is None:
            return None
        try:
            done = self._api().end_headless(self.request)
        except Exception as exc:  # noqa: BLE001 - a refusal is noted, never raised
            return f"failed: {exc}"
        outcome = str(getattr(done, "outcome", "") or "")
        if outcome == "gone":
            return None
        error = getattr(done, "error", "")
        return f"{outcome}: {error}" if error else outcome

    def close(self) -> None:
        pass


def last_result(text: str) -> dict | None:
    """The last JSON object of type `result` in a run log's text: one per line, or a
    list of them on one line."""
    import json

    found = None
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw.startswith(("{", "[")):
            continue
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        for one in item if isinstance(item, list) else [item]:
            if isinstance(one, dict) and one.get("type") == "result":
                found = one
    return found


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


class ForkEnder:
    """Ends one recall's fork (`end_fork`) the first time it is called, and does nothing
    after: called when comms refuses the send of the recall's last answer, so the slot
    isn't held while comms refuses, and again once the recall ends."""

    def __init__(self, recall, route, *, sleep=time.sleep):
        self.recall = recall
        self.route = route
        self.sleep = sleep
        self.done = False

    def __call__(self) -> None:
        if self.done:
            return
        self.done = True
        end_fork(self.recall, self.route, sleep=self.sleep)


def for_recall(recall) -> Subagent | HostFork | Slot | Headless:
    """The route recall `recall`'s record names, else its log line. A headless route
    built here is a taker's: it reads and ends the round the line names."""
    record = getattr(recall, "record", None) or {}
    line = getattr(recall, "line", None) or {}
    name = record.get("route") or line.get("route")
    if name == HEADLESS:
        return Headless(recall.session_id, recall.recall_id,
                        session=line.get("headless_session")
                        or record.get("headless_session"),
                        request=line.get("request"))
    if name == SLOT:
        fork = record.get("fork") or line.get("fork")
        return Slot(recall.session_id, recall.recall_id, fork=fork)
    if name == HOST_FORK:
        return HostFork(recall.session_id)
    return Subagent(recall.session_id)


__all__ = ["BY", "EXITED", "HELD", "EXIT_RETRY_S", "EXIT_HELD_S", "HOST_FORK_TURNS_BOUND_S",
           "HEADLESS_POLL_S", "Subagent", "HostFork", "Slot", "Headless", "last_result",
           "end_fork", "ForkEnder", "for_recall"]
