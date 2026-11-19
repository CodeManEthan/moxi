"""`moxi launcher restore` and `moxi launcher unpark`: bring parked sessions back into
their slots, or give their slots up.

    restore(slots)                 # restore [SLOT ...] [--dry-run] [--json]
    unpark(slots) / unpark(every=True)   # unpark SLOT ... | --all
    resume_parked(session_id)      # a plain resume of a parked session

Each holds the park lock for its whole run, after reconcile and crash discovery
(`parks.whole`): `restore`, `unpark`, and `resume` for a plain `moxi launcher resume` of a
session that has an open park entry, so it and restore never resume the same entry.

**Restore** takes the open entries, every one or those of the slots named (a named slot
with none is `nothing parked in slot N`), and, one at a time in slot order:

- the session still live in its slot (park left it `not-exited` and no reboot came):
  `still running`, the entry left open;
- its liveness unread: `liveness unread`, the entry left open with its reservation;
- a later launch holds the session (it was resumed some other way): the entry closed
  `restored` with that launch's id, and its reservation released;
- otherwise it is resumed by the parked session's path below.

It exits 0 when every entry taken was restored, else 1. `--dry-run` takes no lock, uses
crash discovery's read-only form, writes nothing, and says what restore would do.

**The parked session's path** (`resume_entry`), for restore and a plain resume alike. The
entry's slot, made again first when it is missing, is resumed into with the reservation as
the launch's own hold, which the start ends. When the slot can't take it (occupied, held
by another) or a plain resume asked for another slot, the session is resumed there or
into the lowest slot that can take it, and once it starts the reservation is released.
Either way a placed, started launch closes the entry `restored` with the new launch's id.
A resume refused before placement (comms offline, the name still held, not resumable)
leaves the entry and its reservation as they are. A launch that fails after placement,
when its start has ended the reservation, takes it again with `expect` set to the failed
launch, and the entry then names that launch; when the slot was taken in between, the
entry is closed `dropped` and the session's `moxi launcher resume` command printed. So an
open entry always has its reservation, or is closed.

**Unpark** releases each named slot's reservation (every open entry's with `--all`) and
closes its entry `dropped`; the session stays resumable by `moxi launcher resume`.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from moxi import exit as codes

from . import catalog, first, identity, parks, relaunch, resume, slots, store, target

LOOK = relaunch.LOOK
Refused = relaunch.Refused
Result = relaunch.Result

#: What restore or unpark did with one slot: `restored`, `still-running`,
#: `liveness-unread`, `refused` and `kept` (the entry left open), `dropped` (closed with no
#: launch), `nothing-parked` (a named slot with no open entry) and `failed` (unpark could
#: not release the reservation).
OUTCOMES = ("restored", "still-running", "liveness-unread", "refused", "kept", "dropped",
            "nothing-parked", "failed")
#: The outcomes that leave the entry open.
LEFT_OPEN = ("still-running", "liveness-unread", "refused", "kept", "failed")
#: What a resume refused for want of comms says.
COMMS_OFFLINE = "comms is offline"


@dataclass(frozen=True)
class Restored:
    """What restore, unpark or a parked session's resume did with one slot's entry."""

    slot: int
    outcome: str
    why: str = ""
    session_id: str | None = None
    name: str | None = None
    request_id: str | None = None
    entry_id: str | None = None
    lines: tuple[str, ...] = ()

    def line(self) -> str:
        who = self.name or self.session_id
        head = f"slot {self.slot}: {who}: " if who else f"slot {self.slot}: "
        words = self.outcome.replace("-", " ")
        return head + (f"{words}: {self.why}" if self.why else words)

    def as_json(self) -> dict[str, Any]:
        found = asdict(self)
        found.pop("lines")
        return found


def _restored(entry: store.ParkEntry, outcome: str, why: str = "",
              request_id: str | None = None, conn: sqlite3.Connection | None = None
              ) -> Restored:
    row = store.get(conn, entry.request_id) if conn is not None else None
    return Restored(entry.slot, outcome, why, entry.session_id,
                    row.identity if row is not None else None, request_id, entry.entry_id)


# -- the parked session's path -----------------------------------------------------------


def resume_entry(conn: sqlite3.Connection, entry: store.ParkEntry, *,
                 slot: int | str | None = None, request_id: str | None = None,
                 caller_env: Mapping[str, str] | None = None, turns: Any = LOOK,
                 slots_api: Any = LOOK, launch: Callable[..., Any] | None = None,
                 warn: Callable[[str], None] | None = None,
                 reraise: bool = False) -> Restored:
    """Resume `entry`'s session by the parked session's path, under the park lock the
    caller holds. `slot` is a slot a plain resume asked for instead of the entry's. A
    refusal leaves the entry open and is said, or with `reraise` raised."""
    api = slots.api() if slots_api is LOOK else slots_api
    asked_other = slot is not None and str(slot).strip() != str(entry.slot)
    where: int | str = slot if asked_other else entry.slot
    hold: str | None = None
    why_not = "" if not asked_other else f"slot {slot} was asked for"
    if not asked_other and api is not None:
        try:
            hold, why_not = _into_reserved(api, entry)
        except (api.TryAgain, api.SlotsError) as exc:
            return _restored(entry, "refused", f"slots could not answer about slot "
                                               f"{entry.slot}: {exc}", conn=conn)
        if hold is None:
            where = ""
    try:
        result = resume.resume(entry.session_id, slot=where, request_id=request_id,
                               caller_env=caller_env, turns=turns, slots_api=api,
                               launch=launch, warn=warn, conn=conn, hold=hold)
    except (catalog.Refused, store.RunOpen) as exc:
        if reraise:
            raise
        if isinstance(exc, identity.CommsOffline):
            why = f"{COMMS_OFFLINE}: {exc.problem}"
        elif isinstance(exc, store.RunOpen):
            why = f"{exc} [run-open]"
        else:
            why = f"{exc.problem} [{exc.code}]"
        return _restored(entry, "refused", why, conn=conn)
    data = result.data
    new_id, state = data.get("request_id"), data.get("state")
    lines = tuple(result.lines)
    if state == "failed" or new_id is None:
        done = _rehold(conn, api, entry, new_id, " ".join(lines) or "the launch failed")
        return Restored(done.slot, done.outcome, done.why, done.session_id, done.name,
                        new_id, done.entry_id, lines)
    placed = data.get("slot")
    if hold is not None and str(placed) == str(entry.slot):
        store.close_entry(conn, entry.entry_id, "restored", restore_request_id=new_id)
        return Restored(entry.slot, "restored", f"launch {new_id}", entry.session_id,
                        _name(conn, entry), new_id, entry.entry_id, lines)
    released = _release(api, entry)
    why = (f"resumed in slot {placed} as launch {new_id}"
           + (f" ({why_not})" if why_not else "")
           + (f"; slot {entry.slot}'s reservation released" if released else ""))
    store.close_entry(conn, entry.entry_id, "restored", detail=why,
                      restore_request_id=new_id)
    return Restored(entry.slot, "restored", why, entry.session_id, _name(conn, entry),
                    new_id, entry.entry_id, lines)


def _into_reserved(api: Any, entry: store.ParkEntry) -> tuple[str | None, str]:
    """Whether the entry's slot takes the resume under its reservation: `(hold, "")`,
    the slot made again first when it is missing; or `(None, why not)`."""
    found = api.show(entry.slot)
    if found is None or found.created is None:
        return None, f"slot {entry.slot} is not registered"
    ours = found.hold is not None and found.hold.holder == entry.holder
    if not ours:
        held = f"held by {found.hold.holder}" if found.hold is not None else "not reserved"
        return None, f"slot {entry.slot} is {held}"
    if found.state == "missing":
        api.create([found.number])
        return entry.holder, ""
    if found.state in ("free", "ended"):
        return entry.holder, ""
    return None, f"slot {entry.slot} is {found.state}"


def _rehold(conn: sqlite3.Connection, api: Any, entry: store.ParkEntry,
            failed: str | None, said: str) -> Restored:
    """After a failed launch: keep the entry with its reservation (taken again with
    `expect` set to the failed launch when the start ended it), or close it `dropped`."""
    if api is None:
        return _restored(entry, "kept", f"the launch failed: {said}", failed, conn)
    try:
        found = api.show(entry.slot)
        if found is not None and found.hold is not None and found.hold.holder == entry.holder:
            return _restored(entry, "kept", f"the launch failed: {said}", failed, conn)
        lost = "no launch was placed" if failed is None else None
        if failed is not None:
            api.hold(entry.slot, entry.holder, None, expect=failed)
    except api.Refused as exc:
        lost = exc.words
    except (api.TryAgain, api.SlotsError) as exc:
        return _restored(entry, "kept", f"the launch failed ({said}); slots could not "
                                        f"answer about its reservation: {exc}", failed, conn)
    if lost is not None:
        why = (f"the launch failed ({said}) and slot {entry.slot} can't be reserved again: "
               f"{lost}")
        store.close_entry(conn, entry.entry_id, "dropped", detail=why)
        return _restored(entry, "dropped", f"{why}; resume it with "
                                           f"{parks.resume_command(entry.session_id)}",
                         failed, conn)
    store.set_entry(conn, entry.entry_id, request_id=failed)
    return _restored(entry, "kept", f"the launch failed: {said}; slot {entry.slot} is "
                                    "reserved again", failed, conn)


def _release(api: Any, entry: store.ParkEntry) -> bool:
    if api is None:
        return False
    try:
        return bool(api.release(entry.slot, entry.holder))
    except Exception:  # noqa: BLE001  (reconcile releases a park hold left with no entry)
        return False


def _name(conn: sqlite3.Connection, entry: store.ParkEntry) -> str | None:
    row = store.get(conn, entry.request_id)
    return row.identity if row is not None else None


def resume_parked(session_id: str, *, slot: int | str | None = None,
                  request_id: str | None = None,
                  caller_env: Mapping[str, str] | None = None, turns: Any = LOOK,
                  slots_api: Any = LOOK, launch: Callable[..., Any] | None = None,
                  warn: Callable[[str], None] | None = None,
                  conn: sqlite3.Connection | None = None) -> Result:
    """A plain `moxi launcher resume` of a session with an open park entry: the park lock
    held as `resume` for the whole run, then the parked session's path. Raises a
    refusal when the resume was refused, as `resume.resume` does."""
    env = dict(os.environ if caller_env is None else caller_env)
    api = slots.api() if slots_api is LOOK else slots_api
    turns_api = first.turns_api(quiet=True) if turns is LOOK else turns
    with relaunch._store(conn) as c, parks.whole("resume", c, api, by=first.actor(env),
                                                 turns=turns_api):
        row = store.resolve(c, session_id)
        newest = (row.newest_session_id or row.session_id) if row is not None else None
        entries = store.entries_of(c, request_ids=[row.request_id] if row else [],
                                   session_ids=[newest] if newest else [])
        if not entries:  # reconcile closed it: a plain resume
            return resume.resume(session_id, slot=slot, request_id=request_id,
                                 caller_env=caller_env, turns=turns_api, slots_api=api,
                                 launch=launch, warn=warn, conn=c)
        done = resume_entry(c, entries[0], slot=slot, request_id=request_id,
                            caller_env=caller_env, turns=turns_api, slots_api=api,
                            launch=launch, warn=warn, reraise=True)
    ok = done.outcome == "restored"
    lines = done.lines + (done.line(),)
    return Result(codes.OK if ok else codes.ERROR, lines, data={
        "outcome": "resumed" if ok else "failed", "request_id": done.request_id,
        "session_id": done.session_id, "slot": done.slot, "entry": done.as_json()})


# -- restore --------------------------------------------------------------------------------


def restore(slots_named: Iterable[int] = (), *, dry_run: bool = False,
            caller_env: Mapping[str, str] | None = None, turns: Any = LOOK,
            slots_api: Any = LOOK, launch: Callable[..., Any] | None = None,
            warn: Callable[[str], None] | None = None,
            conn: sqlite3.Connection | None = None) -> Result:
    """Restore the parked sessions, every one or those of `slots_named`. Raises
    `parks.ParkRunning` when another park, restore or unpark holds the park lock."""
    env = dict(os.environ if caller_env is None else caller_env)
    api = slots.api() if slots_api is LOOK else slots_api
    turns_api = first.turns_api(quiet=True) if turns is LOOK else turns
    named = sorted({int(n) for n in slots_named})
    by = first.actor(env)
    try:
        with relaunch._store(conn) as c:
            if dry_run:
                return _restore_dry(c, api, turns_api, named, by)
            with parks.whole("restore", c, api, by=by, turns=turns_api) as prepared:
                entries, said = _taken(c, named)
                for entry in entries:
                    said.append(_restore_one(c, entry, api, turns_api, caller_env, launch,
                                             warn))
                return _summary(prepared.lines(), said)
    except target.NotLaunched:
        said = [Restored(n, "nothing-parked", f"nothing parked in slot {n}") for n in named]
        return _summary([], said)


def _taken(conn: sqlite3.Connection, named: list[int]
           ) -> tuple[list[store.ParkEntry], list[Restored]]:
    entries = store.open_entries(conn, named or None)
    have = {e.slot for e in entries}
    return entries, [Restored(n, "nothing-parked", f"nothing parked in slot {n}")
                     for n in named if n not in have]


def _restore_one(conn: sqlite3.Connection, entry: store.ParkEntry, api: Any, turns: Any,
                 caller_env: Mapping[str, str] | None, launch, warn) -> Restored:
    before = _before(conn, entry, turns)
    if before is not None:
        outcome, why, later = before
        if outcome == "restored":
            released = _release(api, entry)
            why += f"; slot {entry.slot}'s reservation released" if released else ""
            store.close_entry(conn, entry.entry_id, "restored", detail=why,
                              restore_request_id=later)
        return _restored(entry, outcome, why, later, conn)
    return resume_entry(conn, entry, caller_env=caller_env, turns=turns, slots_api=api,
                        launch=launch, warn=warn)


def _before(conn: sqlite3.Connection, entry: store.ParkEntry,
            turns: Any) -> tuple[str, str, str | None] | None:
    """What restore finds before it resumes an entry's session: `(outcome, why, later
    launch)`, or None to resume it."""
    holder = store.resolve(conn, entry.session_id)
    if holder is not None and holder.request_id != entry.request_id:
        return "restored", f"launch {holder.request_id} took it up since", holder.request_id
    row = holder or store.get(conn, entry.request_id)
    if row is None:
        return None
    live, unread = resume.liveness(row, entry.session_id, turns)
    if live is None:
        return "liveness-unread", f"whether it is live can't be read: {unread}", None
    if live:
        return "still-running", f"it still runs in slot {entry.slot}", None
    return None


def _summary(notes: list[str], said: list[Restored], *, dry_run: bool = False) -> Result:
    if not said:
        return Result(codes.OK, (*notes, "nothing to restore"),
                      data={"outcome": "nothing", "notes": notes, "slots": []})
    lines: list[str] = list(notes)
    for one in said:
        lines.extend(one.lines)
        lines.append(one.line())
    taken = [s for s in said if s.outcome != "nothing-parked"]
    done = sum(1 for s in taken if s.outcome == "restored")
    verb = "would restore" if dry_run else "restored"
    summary = f"{verb} {done} of {len(taken)} parked session{'s' if len(taken) != 1 else ''}"
    if any(COMMS_OFFLINE in s.why for s in said):
        summary += "; run moxi launcher restore again once comms is up"
    lines.append(summary)
    ok = dry_run or (done == len(taken) and len(taken) == len(said))
    return Result(codes.OK if ok else codes.ERROR, tuple(lines), data={
        "outcome": "dry-run" if dry_run else ("restored" if ok else "partly"),
        "notes": notes, "slots": [s.as_json() for s in said]})


def _restore_dry(conn: sqlite3.Connection, api: Any, turns: Any, named: list[int],
                 by: str) -> Result:
    """Say what restore would do: no lock, crash discovery read-only, nothing written."""
    found = parks.discover(conn, api, by=by, write=False, turns=turns)
    notes = [f.line() for f in found]
    entries, said = _taken(conn, named)
    for entry in entries:
        before = _before(conn, entry, turns)
        if before is not None:
            outcome, why, later = before
            said.append(_restored(entry, outcome, why, later, conn))
        else:
            said.append(_restored(entry, "restored", f"would resume it into slot "
                                                     f"{entry.slot}", conn=conn))
    for seen in found:
        if seen.action == "reserve" and (not named or seen.slot in named):
            said = [s for s in said if s.slot != seen.slot]
            said.append(Restored(seen.slot, "restored", "crashed; would reserve it and "
                                                        "resume it into its slot",
                                 seen.session_id))
    said.sort(key=lambda s: s.slot)
    return _summary(notes, said, dry_run=True)


# -- unpark ---------------------------------------------------------------------------------


def unpark(slots_named: Iterable[int] = (), *, every: bool = False,
           caller_env: Mapping[str, str] | None = None, turns: Any = LOOK,
           slots_api: Any = LOOK, conn: sqlite3.Connection | None = None) -> Result:
    """Give up the reservations of `slots_named`, or of every open entry with `every`:
    each released and its entry closed `dropped`."""
    env = dict(os.environ if caller_env is None else caller_env)
    api = slots.api() if slots_api is LOOK else slots_api
    turns_api = first.turns_api(quiet=True) if turns is LOOK else turns
    named = sorted({int(n) for n in slots_named})
    by = first.actor(env)
    try:
        with relaunch._store(conn) as c, parks.whole("unpark", c, api, by=by,
                                                     turns=turns_api) as prepared:
            entries, said = _taken(c, [] if every else named)
            for entry in entries:
                said.append(_unpark_one(c, api, entry, by))
            notes = prepared.lines()
    except target.NotLaunched:
        notes, said = [], [Restored(n, "nothing-parked", f"nothing parked in slot {n}")
                           for n in named]
    if not said:
        return Result(codes.OK, (*notes, "nothing to unpark"),
                      data={"outcome": "nothing", "notes": notes, "slots": []})
    said.sort(key=lambda s: s.slot)
    dropped = sum(1 for s in said if s.outcome == "dropped")
    lines = (*notes, *(s.line() for s in said), f"unparked {dropped} of {len(said)}")
    ok = dropped == len(said)
    return Result(codes.OK if ok else codes.ERROR, lines, data={
        "outcome": "unparked" if ok else "partly", "notes": notes,
        "slots": [s.as_json() for s in said]})


def _unpark_one(conn: sqlite3.Connection, api: Any, entry: store.ParkEntry,
                by: str) -> Restored:
    if api is not None:
        try:
            api.release(entry.slot, entry.holder)
        except (api.TryAgain, api.SlotsError) as exc:
            return _restored(entry, "failed", f"its reservation could not be released: "
                                              f"{exc}", conn=conn)
    store.close_entry(conn, entry.entry_id, "dropped", detail=f"unparked by {by}")
    return _restored(entry, "dropped", f"resume it with "
                                       f"{parks.resume_command(entry.session_id)}",
                     conn=conn)


__all__ = ["COMMS_OFFLINE", "LEFT_OPEN", "OUTCOMES", "Restored", "restore", "resume_entry",
           "resume_parked", "unpark"]
