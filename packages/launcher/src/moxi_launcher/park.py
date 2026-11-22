"""`moxi launcher park`: exit every launched session in a slot, record which session was in
which slot, and reserve those slots for `moxi launcher restore`.

    park()                         # park [--wait-s SECONDS] [--dry-run] [--json]

Park holds the park lock for its whole run, and runs reconcile and crash discovery first
(`parks.whole`). Then:

1. **Find.** Every registered slot whose run pane is live and whose last launch
   (`last.meta.request_id`) is a launcher slot launch with a session id. A live run the
   launcher did not launch is listed `not launched by the launcher; left alone`; a launch
   with no session id to resume, `no session id to resume; left alone`. A live launch the
   launcher started outside a slot is listed `not in a slot; left running`: restore brings
   back slot sessions only.
2. **Reserve first.** A slot that already has an open entry for the same session (a park
   before this one left it `not-exited`) keeps it, and its exit is tried again. Any other
   is held `park-<park id>` with no end and `expect` set to the found launch, and then
   gets an entry. A `changed` refusal (the run ended and something else took the slot) is
   listed `changed; left alone`; a slot another holder holds, `held by H; left running`.
3. **Exit**, every session at once, each in its own thread with its own store connection,
   by `exits.park_exit`: it waits for quiet up to `park.wait_s` (or `--wait-s`), and
   a client's activity does not hold it.
4. **Outcomes**, on each entry: `exited`; `not-exited` with the reason when the wait ran
   out or the exit failed, the entry and reservation kept; `left-running` for a Codex
   session, which offers no exit. The session park runs in is reserved and recorded
   `not-exited: the session running park`, and not exited.

It prints one line per slot and a summary, and exits 0 when every found session got an
entry, whatever the outcomes, and 1 when a slot could not be reserved or an entry could
not be written. `--dry-run` takes no lock, finds, reads quiet once, writes nothing and
says what park would do. Park removes no slot.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from moxi import exit as codes

from . import catalog, exits, first, live, parks, relaunch, slots, store, target
from .listeners import REQUEST_ENV

LOOK = relaunch.LOOK
Refused = relaunch.Refused
Result = relaunch.Result

#: The harnesses park records and reserves but leaves running: they offer no exit.
NO_EXIT = ("codex",)
#: What park says of each slot: `exited`, `not-exited`, `left-running` (each with an
#: entry), `left-alone` (no entry, nothing done) and `failed` (no reservation or no
#: entry, though one was owed).
OUTCOMES = ("exited", "not-exited", "left-running", "left-alone", "failed")
#: The reason recorded for the session park runs in.
OWN = "the session running park"
#: The reason recorded for a session whose harness offers no exit.
LEFT_RUNNING = "Codex offers no exit; it ends with the machine"


@dataclass(frozen=True)
class Parked:
    """What park did, or would do, with one slot or one session outside a slot."""

    slot: int | None
    outcome: str
    why: str = ""
    session_id: str | None = None
    request_id: str | None = None
    harness: str | None = None
    name: str | None = None
    entry_id: str | None = None

    def line(self) -> str:
        where = f"slot {self.slot}" if self.slot is not None else "not in a slot"
        who = self.name or self.session_id or "no session"
        if self.outcome == "left-alone":
            return f"{where}: {who}: {self.why}"
        text = f"{where}: {who}: {self.outcome}"
        return f"{text}: {self.why}" if self.why else text

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Found:
    """A slot whose live run is a launcher launch of a session park can resume."""

    slot: Any
    row: store.LaunchRow
    session_id: str

    @property
    def number(self) -> int:
        return int(self.slot.number)

    def parked(self, outcome: str, why: str = "", entry: store.ParkEntry | None = None
               ) -> Parked:
        return Parked(self.number, outcome, why, self.session_id, self.row.request_id,
                      self.row.harness, self.row.identity,
                      entry.entry_id if entry is not None else None)


def park(*, wait_s: float | None = None, dry_run: bool = False,
         caller_env: Mapping[str, str] | None = None, turns: Any = LOOK,
         slots_api: Any = LOOK, settings: Mapping[str, Any] | None = None,
         conn: sqlite3.Connection | None = None,
         exit_one: Callable[..., Result] | None = None,
         clock: Callable[[], float] = time.monotonic,
         sleep: Callable[[float], None] = time.sleep) -> Result:
    """Park every launched session in a slot. Returns the lines and data; raises
    `parks.ParkRunning` when another park, restore or unpark holds the park lock.

    `wait_s` bounds each session's wait for quiet (`park.wait_s` when None). `turns` and
    `slots_api` stand in for turns' and slots' APIs; `exit_one` for `exits.park_exit`,
    called with the session id and its keywords."""
    env = dict(os.environ if caller_env is None else caller_env)
    current = settings if settings is not None else relaunch._settings()
    bound = float(wait_s if wait_s is not None else current["park.wait_s"])
    api = slots.api() if slots_api is LOOK else slots_api
    turns_api = first.turns_api(quiet=True) if turns is LOOK else turns
    by = first.actor(env)
    try:
        with relaunch._store(conn) as c:
            if dry_run:
                return _dry_run(c, api, turns_api, env, current, by)
            with parks.whole("park", c, api, by=by, turns=turns_api) as prepared:
                return _park(c, api, turns_api, env, current, by, prepared, bound,
                             exit_one=exit_one, clock=clock, sleep=sleep)
    except target.NotLaunched:
        return Result(codes.OK, ("nothing to park",), data={"outcome": "nothing",
                                                            "slots": []})


def _park(conn: sqlite3.Connection, api: Any, turns: Any, env: Mapping[str, str],
          current: Mapping[str, Any], by: str, prepared: parks.Prepared, bound: float, *,
          exit_one, clock, sleep) -> Result:
    found, said = _find(conn, api)
    park_id = store.new_park_id()
    holder = store.park_holder(park_id)
    own = _own(conn, env)
    reserved: list[tuple[_Found, store.ParkEntry]] = []
    for one in found:
        done = _reserve(conn, api, one, park_id, holder, by)
        if isinstance(done, Parked):
            said.append(done)
        else:
            reserved.append((one, done))
    deadline = clock() + bound
    exiting: list[tuple[_Found, store.ParkEntry]] = []
    outcomes: dict[int, Parked] = {}
    for one, entry in reserved:
        if one.row.request_id in own or one.session_id in own:
            outcomes[one.number] = _record(conn, one, entry, "not-exited", OWN)
        elif one.row.harness in NO_EXIT:
            outcomes[one.number] = _record(conn, one, entry, "left-running", LEFT_RUNNING)
        else:
            exiting.append((one, entry))
    for one, entry, outcome, why in _exit_all(conn, exiting, api, turns, env, current,
                                             deadline, exit_one=exit_one, clock=clock,
                                             sleep=sleep):
        outcomes[one.number] = _record(conn, one, entry, outcome, why)
    said.extend(outcomes.values())
    said.sort(key=lambda p: (p.slot is None, p.slot or 0))
    return _result(prepared.lines(), said, park_id if reserved else None)


def _find(conn: sqlite3.Connection, api: Any) -> tuple[list[_Found], list[Parked]]:
    """The slots park acts on, and what it lists and leaves: live runs the launcher did not
    launch, and live launches outside a slot."""
    said: list[Parked] = []
    found: list[_Found] = []
    if api is not None:
        listing = api.list_slots()
        if not listing.ok:
            raise Refused("slots-failed", f"slots can't be read: {listing.why}",
                          "moxi slots list shows the slots", codes.ERROR)
        for slot in listing.slots:
            if slot.state != "occupied":
                continue
            meta = slot.last.meta if slot.last is not None else None
            rid = meta.get("request_id") if isinstance(meta, Mapping) else None
            row = store.get(conn, rid) if isinstance(rid, str) and rid else None
            if row is None or row.target != "slot":
                said.append(Parked(int(slot.number), "left-alone",
                                   "not launched by the launcher; left alone"))
                continue
            known = store.session_ids(conn, row.request_id)
            sid = known[-1][0] if known else row.session_id
            if not sid:
                said.append(Parked(int(slot.number), "left-alone",
                                   "no session id to resume; left alone", None,
                                   row.request_id, row.harness, row.identity))
                continue
            found.append(_Found(slot, row, sid))
    for row in live.live(conn):
        if row.target == "slot":
            continue
        said.append(Parked(None, "left-alone", "not in a slot; left running",
                           row.newest_session_id, row.request_id, row.harness,
                           row.identity))
    return found, said


def _own(conn: sqlite3.Connection, env: Mapping[str, str]) -> set[str]:
    """The request id and session id of the launch park runs in, when it runs in one."""
    rid = (env.get(REQUEST_ENV) or "").strip()
    if not rid:
        return set()
    known = store.session_ids(conn, rid)
    return {rid} | {sid for sid, *_ in known}


def _reserve(conn: sqlite3.Connection, api: Any, one: _Found, park_id: str, holder: str,
             by: str) -> store.ParkEntry | Parked:
    """Step 2 for one slot: its open entry kept, or the reservation then a new entry; or
    what stopped it."""
    existing = store.open_entry(conn, one.number)
    if existing is not None:
        if existing.session_id == one.session_id or existing.request_id == one.row.request_id:
            return existing
        return one.parked("left-alone", f"held by {existing.holder}; left running")
    try:
        api.hold(one.number, holder, None, expect=one.row.request_id)
    except api.Refused as exc:
        if exc.reason == "changed":
            return one.parked("left-alone", "changed; left alone")
        if exc.reason == "held":
            who = exc.holder.get("holder") if isinstance(exc.holder, Mapping) else None
            return one.parked("failed", f"held by {who or 'another holder'}; left running")
        return one.parked("failed", f"slots refused the reservation: {exc.words}")
    except (api.TryAgain, api.SlotsError) as exc:
        return one.parked("failed", f"the slot could not be reserved: {exc}")
    try:
        return store.add_entry(conn, park_id=park_id, kind="park", by=by, slot=one.number,
                               request_id=one.row.request_id, session_id=one.session_id,
                               harness=one.row.harness)
    except Exception as exc:  # noqa: BLE001  (an entry not written gives its hold back)
        try:
            api.release(one.number, holder)
        except Exception:  # noqa: BLE001  (reconcile releases a hold left with no entry)
            pass
        return one.parked("failed", f"the park entry could not be written: {exc}")


def _record(conn: sqlite3.Connection, one: _Found, entry: store.ParkEntry, outcome: str,
            why: str) -> Parked:
    store.set_entry(conn, entry.entry_id, outcome=outcome, detail=why or outcome)
    return one.parked(outcome, why, entry)


def _exit_all(conn: sqlite3.Connection, exiting: list[tuple[_Found, store.ParkEntry]],
              api: Any, turns: Any, env: Mapping[str, str], current: Mapping[str, Any],
              deadline: float, *, exit_one, clock, sleep
              ) -> list[tuple[_Found, store.ParkEntry, str, str]]:
    """Step 3: every exit at once, each in its own thread with its own store connection.
    Each gives `(found, entry, outcome, why)`."""
    db = _db_path(conn)
    done: list[tuple[_Found, store.ParkEntry, str, str]] = []
    lock = threading.Lock()

    def one_exit(one: _Found, entry: store.ParkEntry) -> None:
        outcome, why = "not-exited", "the exit stopped on a fault"
        try:
            thread_conn = store.connect(db) if db else store.connect()
            try:
                outcome, why = _exit_one(one, thread_conn, api, turns, env, current,
                                         deadline, exit_one=exit_one, clock=clock,
                                         sleep=sleep)
            finally:
                thread_conn.close()
        except Exception as exc:  # noqa: BLE001  (one exit's fault leaves the others)
            why = f"the exit stopped on a fault: {exc}"
        with lock:
            done.append((one, entry, outcome, why))

    threads = [threading.Thread(target=one_exit, args=pair, name="launcher-park-exit",
                                daemon=True) for pair in exiting]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return done


def _exit_one(one: _Found, conn: sqlite3.Connection, api: Any, turns: Any,
              env: Mapping[str, str], current: Mapping[str, Any], deadline: float, *,
              exit_one, clock, sleep) -> tuple[str, str]:
    call = exit_one if exit_one is not None else exits.park_exit
    try:
        result = call(one.session_id, deadline=deadline, conn=conn, turns=turns,
                      slots_api=api, settings=current, caller_env=env, clock=clock,
                      sleep=sleep)
    except catalog.Refused as exc:
        if exc.code == "not-live":
            return "exited", "it had already ended"
        return "not-exited", f"{exc.code}: {exc.problem}"
    except store.RunOpen as exc:
        return "not-exited", f"run-open: {exc}"
    verdict = result.data.get("outcome")
    if verdict == "exited":
        return "exited", ""
    word, detail = result.data.get("word"), result.data.get("detail")
    said = f"{verdict}" + (f" ({word})" if word and word != verdict else "")
    return "not-exited", f"{said}: {detail}" if detail else said


def _db_path(conn: sqlite3.Connection) -> str | None:
    for _seq, name, path in conn.execute("PRAGMA database_list"):
        if name == "main":
            return path or None
    return None


def _result(notes: list[str], said: list[Parked], park_id: str | None, *,
            dry_run: bool = False) -> Result:
    entered = [p for p in said if p.outcome in ("exited", "not-exited", "left-running")]
    failed = [p for p in said if p.outcome == "failed"]
    if not entered and not failed:
        lines = (*notes, *(p.line() for p in said), "nothing to park")
        return Result(codes.OK, lines, data={"outcome": "nothing", "park_id": None,
                                              "notes": notes,
                                              "slots": [p.as_json() for p in said]})
    counts = {word: sum(1 for p in entered if p.outcome == word)
              for word in ("exited", "not-exited", "left-running")}
    verb = "would park" if dry_run else "parked"
    summary = (f"{verb} {len(entered)} session{'s' if len(entered) != 1 else ''}"
               + (f" as park-{park_id}" if park_id else "")
               + f": {counts['exited']} exited, {counts['not-exited']} not exited, "
                 f"{counts['left-running']} left running")
    if failed:
        summary += f"; {len(failed)} could not be reserved or entered"
    lines = (*notes, *(p.line() for p in said), summary)
    outcome = "dry-run" if dry_run else ("failed" if failed else "parked")
    return Result(codes.ERROR if failed else codes.OK, lines, data={
        "outcome": outcome, "park_id": park_id, "notes": notes,
        "slots": [p.as_json() for p in said]})


# -- the dry run --------------------------------------------------------------------------


def _dry_run(conn: sqlite3.Connection, api: Any, turns: Any, env: Mapping[str, str],
             current: Mapping[str, Any], by: str) -> Result:
    """Find, read quiet once and say what park would do; take no lock, write nothing."""
    notes = [f.line() for f in parks.discover(conn, api, by=by, write=False, turns=turns)]
    found, said = _find(conn, api)
    own = _own(conn, env)
    for one in found:
        said.append(_would(conn, api, turns, env, current, one, own))
    said.sort(key=lambda p: (p.slot is None, p.slot or 0))
    return _result(notes, said, None, dry_run=True)


def _would(conn: sqlite3.Connection, api: Any, turns: Any, env: Mapping[str, str],
           current: Mapping[str, Any], one: _Found, own: set[str]) -> Parked:
    existing = store.open_entry(conn, one.number)
    if existing is not None and existing.session_id != one.session_id \
            and existing.request_id != one.row.request_id:
        return one.parked("left-alone", f"held by {existing.holder}; left running")
    hold = one.slot.hold
    if existing is None and hold is not None:
        return one.parked("failed", f"held by {hold.holder}; left running")
    if one.row.request_id in own or one.session_id in own:
        return one.parked("not-exited", OWN)
    if one.row.harness in NO_EXIT:
        return one.parked("left-running", LEFT_RUNNING)
    try:
        read = exits.exit_session(one.session_id, dry_run=True, mode="park", conn=conn,
                                  turns=turns, slots_api=api, settings=current,
                                  caller_env=env)
    except (catalog.Refused, store.RunOpen) as exc:
        words = getattr(exc, "problem", None) or str(exc)
        return one.parked("not-exited", f"would be tried: {words}")
    if read.data.get("outcome") == "held":
        return one.parked("not-exited", f"would wait: {read.data.get('word')}: "
                                        f"{read.data.get('detail')}")
    return one.parked("exited", "would exit now")


__all__ = ["LEFT_RUNNING", "NO_EXIT", "OUTCOMES", "OWN", "Parked", "park"]
