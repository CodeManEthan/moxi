"""What a list of sessions answers, built on the catalog: the filters and their counts,
the groups a list is cut into, the next rows of a group, the folders, and one session
opened. Plain functions that return plain data (dicts, lists, numbers, strings, None).

**The filters.** `harness`, `since` (changed at or after it) and `until` (changed before
it) are tested on a catalog entry and open no file. `folder` (a session whose own head names exactly that folder) and `find` (the
session's id starts with the text, or the last segment of its folder contains it, without
regard to case) need every session's head: asking for either reads each head not read
yet, once for the catalog's life. Nothing else reads every head. Every count an answer
gives is exact for the sessions it stands for.

**The groups.** A list with no `find` is cut into:

* `recent`: the sessions a person started that changed within `recent_s` seconds of
  `now`. A session a program started is not in it, however lately it changed.
* one group per calendar day, for the newest seven days that hold a session not in
  `recent`. A day's rows are the sessions a person started; the ones a program started
  are counted beside them and listed only when asked for.
* `earlier`: every other session, of either kind, as a count.

A session is a program's only where its head says so (`by_program` is True); one whose
head does not say, or was not read, is listed as a person's, so a missing fact hides no
session. A session is in exactly one group of an answer.

Days are calendar days in the process's local time zone, so a day the clocks change on,
of 23 or 25 hours, is one day. A session with no change time (an OpenCode row without
one) has no day: it sorts last, is counted in `earlier`, and is listed under the key
`undated` when the earlier days are asked for.

With `find`, the list is one group, `found`, holding the matches of both kinds in the
list's order.

**Which heads a list reads.** With no `folder` and no `find`: the heads of the sessions
that changed within `recent_s` and of the sessions of the days the answer returns, and
the last turn of each row it returns. Codex heads are read by the catalog's gather.

**Positions.** A `before` is a place in the list's order: an entry's last change in
nanoseconds, its harness and its id, joined by colons. Asking for more after one answers
the sessions strictly after that place. So a session that does not change between two
calls is answered once and never skipped; one that changes moves to the front of the
list, is not answered again by a call for more, and is in the next list. A session that
is in `recent` only because of the time (it has not changed, and `now` has moved past
its hour) leaves `recent` for its day between two calls; the next list places it.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from . import catalog as catalog_module
from .catalog import Catalog, Details, Entry, Gathered, Said
from .errors import Unreadable
from .find import LISTED, NONE, subagents
from .read import window
from .resume import resume
from .shape import HARNESSES, Message

#: How many rows a list carries for `recent`, for a day, and for `found`.
RECENT_ROWS = 30
DAY_ROWS = 8
FOUND_ROWS = 30

#: How many rows one call for more answers, and how many sessions the earlier days it
#: answers hold between them at least.
MORE_ROWS = 30
EARLIER_SESSIONS = 30

#: How many rows the ungrouped list carries unless asked.
NEWEST_ROWS = 30

#: How many days a list shows after `recent`.
DAYS = 7

#: The most folders a folders answer lists.
FOLDERS_MAX = 200

#: How many subagents an opened session lists.
SUBAGENTS_MAX = 8

#: The fewest characters a `find` has.
FIND_MIN = 2

#: Group keys that are not a date.
RECENT = "recent"
FOUND = "found"
EARLIER = "earlier"
UNDATED = "undated"

#: The kinds of a group's rows.
PERSON = "person"
PROGRAM = "program"

BEFORE_IS = ("a `before` is a place in the list as an answer gives it: a session's last "
             "change in nanoseconds, its harness and its id, joined by colons")


# --- the filters ------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Filters:
    harness: str | None = None
    since: float | None = None
    until: float | None = None
    folder: str | None = None
    find: str | None = None


def _number(name: str, value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} is a time in epoch seconds, not {value!r}")
    return float(value)


def _filters(harness: Any = None, since: Any = None, until: Any = None,
             folder: Any = None, find: Any = None) -> _Filters:
    if harness is not None and harness not in HARNESSES:
        raise ValueError(f"harness is one of {', '.join(HARNESSES)}, not {harness!r}")
    if folder is not None and not (isinstance(folder, str) and folder.startswith("/")):
        raise ValueError(f"folder is an absolute path, not {folder!r}")
    if find is not None:
        if not isinstance(find, str) or len(find.strip()) < FIND_MIN:
            raise ValueError(f"find is text of {FIND_MIN} characters or more, "
                             f"not {find!r}")
        find = find.strip().lower()
    return _Filters(harness, _number("since", since), _number("until", until), folder,
                    find)


def _in_time(entry: Entry, f: _Filters) -> bool:
    if f.since is None and f.until is None:
        return True
    changed = entry.changed
    if changed is None:
        return False
    return ((f.since is None or changed >= f.since)
            and (f.until is None or changed < f.until))


def _found(entry: Entry, folder: str | None, needle: str) -> bool:
    if entry.session.lower().startswith(needle):
        return True
    if not folder:
        return False
    return needle in folder.rstrip("/").rsplit("/", 1)[-1].lower()


def _pool(cat: Catalog, gathered: Gathered, f: _Filters, *,
          folder: bool = True) -> list[Entry]:
    """The entries that pass every filter but the harness (and, with `folder` false, the
    folder), in the list's order."""
    pool = [e for e in gathered.entries if _in_time(e, f)]
    by_folder = f.folder if folder else None
    if by_folder is None and f.find is None:
        return pool
    cat.read_heads()
    out = []
    for entry in pool:
        head = cat.kept_head(entry)
        where = head.folder if head is not None else None
        if by_folder is not None and where != by_folder:
            continue
        if f.find is not None and not _found(entry, where, f.find):
            continue
        out.append(entry)
    return out


def _of(pool: Sequence[Entry], harness: str | None) -> list[Entry]:
    return list(pool) if harness is None else [e for e in pool if e.harness == harness]


# --- places and days --------------------------------------------------------------------------

def position(entry: Entry) -> str:
    """The entry's place in the list, as a `before`."""
    return f"{entry.changed_ns or 0}:{entry.harness}:{entry.session}"


def parse_position(before: Any) -> tuple[int, str, str]:
    """A `before` as an `Entry.order`. Raises ValueError for one that does not parse."""
    if isinstance(before, str):
        parts = before.split(":", 2)
        if (len(parts) == 3 and parts[0].isascii() and parts[0].isdigit()
                and parts[1] in HARNESSES and parts[2]):
            return (-int(parts[0]), parts[1], parts[2])
    raise ValueError(f"{BEFORE_IS}; {before!r} is not one")


def _after(entries: Iterable[Entry], before: Any) -> list[Entry]:
    if before is None:
        return list(entries)
    place = parse_position(before)
    return [e for e in entries if e.order > place]


def local_day(seconds: float) -> str:
    """The calendar date of a time in the process's local time zone, as `YYYY-MM-DD`."""
    at = time.localtime(seconds)
    return f"{at.tm_year:04d}-{at.tm_mon:02d}-{at.tm_mday:02d}"


def _day(entry: Entry) -> str:
    if entry.changed_ns is None:
        return UNDATED
    return local_day(entry.changed_ns // 1_000_000_000)


def _is_day(group: Any) -> bool:
    if group == UNDATED:
        return True
    if not isinstance(group, str) or len(group) != 10:
        return False
    try:
        time.strptime(group, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _by_day(entries: Iterable[Entry]) -> list[tuple[str, list[Entry]]]:
    """Entries by their day, newest day first; the sessions with no change time last."""
    days: dict[str, list[Entry]] = {}
    for entry in entries:
        days.setdefault(_day(entry), []).append(entry)
    keys = sorted((k for k in days if k != UNDATED), reverse=True)
    if UNDATED in days:
        keys.append(UNDATED)
    return [(key, days[key]) for key in keys]


def _cutoff(now: float, recent_s: float) -> int:
    if isinstance(recent_s, bool) or not isinstance(recent_s, (int, float)):
        raise ValueError(f"recent_s is a number of seconds, not {recent_s!r}")
    return int((now - recent_s) * 1_000_000_000)


def _program(cat: Catalog, entry: Entry) -> bool:
    head = cat.kept_head(entry)
    return head is not None and head.by_program is True


def _split_recent(cat: Catalog, entries: Sequence[Entry], cutoff: int
                  ) -> tuple[list[Entry], list[Entry]]:
    """`entries` as the ones in `recent` and the rest, each in the list's order. Reads
    the heads of the sessions that changed since `cutoff`."""
    fresh = [e for e in entries if e.changed_ns is not None and e.changed_ns >= cutoff]
    cat.read_heads(fresh)
    recent = [e for e in fresh if not _program(cat, e)]
    inside = {(e.harness, e.session) for e in recent}
    return recent, [e for e in entries if (e.harness, e.session) not in inside]


def _split_kinds(cat: Catalog, entries: Sequence[Entry]
                 ) -> tuple[list[Entry], list[Entry]]:
    """A day's entries as a person's and a program's. Reads their heads."""
    cat.read_heads(entries)
    person, program = [], []
    for entry in entries:
        (program if _program(cat, entry) else person).append(entry)
    return person, program


# --- rows ---------------------------------------------------------------------------------------

def _said(said: Said | None, who: bool) -> dict[str, Any] | None:
    if said is None:
        return None
    out: dict[str, Any] = {"at": said.at}
    if who:
        out["who"] = said.who
    out["text"] = said.text
    return out


def _row(cat: Catalog, entry: Entry, cutoff: int) -> dict[str, Any]:
    details: Details = cat.details(entry)
    return {
        "harness": entry.harness, "session": entry.session, "path": entry.path,
        "changed": entry.changed, "size": entry.size, "subagents": entry.subagents,
        "folder": details.folder, "started": details.started,
        "by_program": details.by_program, "name": details.name, "model": details.model,
        "activity": _said(details.activity, True),
        "prompt": _said(details.prompt, False),
        "recent": (details.by_program is not True and entry.changed_ns is not None
                   and entry.changed_ns >= cutoff),
    }


def _page(cat: Catalog, entries: Sequence[Entry], limit: int, cutoff: int
          ) -> tuple[list[dict[str, Any]], str | None]:
    """The first `limit` of `entries` as rows, and the place to ask for more after: None
    when the rows hold them all."""
    shown = entries[:limit]
    before = position(shown[-1]) if len(entries) > len(shown) and shown else None
    return [_row(cat, e, cutoff) for e in shown], before


def _day_group(cat: Catalog, key: str, entries: Sequence[Entry], cutoff: int
               ) -> dict[str, Any]:
    person, program = _split_kinds(cat, entries)
    rows, before = _page(cat, person, DAY_ROWS, cutoff)
    return {"key": key, "count": len(person), "rows": rows, "before": before,
            "program": {"count": len(program)}}


# --- the list -----------------------------------------------------------------------------------

def _catalog(cat: Catalog | None) -> Catalog:
    return catalog_module.shared() if cat is None else cat


def _now(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _counted(cat: Catalog, f: _Filters, now: float
             ) -> tuple[dict[str, Any], list[Entry]]:
    """What every list answers before its rows (`now`, `today`, the counts, the roots
    that failed), and the entries under the filters, in the list's order."""
    gathered = cat.gather()
    pool = _pool(cat, gathered, f)
    entries = _of(pool, f.harness)
    counts = Counter(e.harness for e in pool)
    return {
        "now": now, "today": local_day(now), "all": len(gathered.entries),
        "total": len(entries), "unreadable": gathered.unreadable,
        "roots": [{"harness": r.harness, "root": r.root, "reason": r.reason,
                   "parts": r.parts} for r in gathered.roots],
        "harnesses": [{"harness": h, "count": counts[h]} for h in gathered.listed],
    }, entries


def list_sessions(cat: Catalog | None = None, *, recent_s: float,
                  now: float | None = None, harness: str | None = None,
                  since: float | None = None, until: float | None = None,
                  folder: str | None = None, find: str | None = None) -> dict[str, Any]:
    """The list: its counts and its groups, with the first rows of each (see the module).

    `cat` is the catalog to read through (the process's own when None); `now` is the time
    the answer is cut at, in epoch seconds (the clock's when None). The answer:

    * `now`; `today`, the local date of `now` as `YYYY-MM-DD`.
    * `all`, the sessions before the filters, and `total`, the ones after them; both
      count sessions of both kinds.
    * `unreadable`, the compressed Codex rollouts left out, None when Codex's root was
      not listed.
    * `roots`: `{harness, root, reason, parts}` per harness whose root was not read
      whole. `reason` says why a root could not be listed, and `parts` is then None.
      For a root that was listed, `reason` is None and `parts` is how many folders and
      files under it could not be listed: their sessions are in no count of the answer,
      so `all`, `total` and that harness's count are short by them.
    * `harnesses`: `{harness, count}` for each harness whose root was listed, counted
      under every filter but the harness. A harness that is not on the machine (its root
      is the default one and does not exist) is in neither `roots` nor `harnesses`; one
      whose root failed is in `roots` alone. With no harness there and nothing failing,
      `all` is 0 and both are empty.
    * `groups`, in order. With no `find`: `recent` as `{key, count, rows, before}`; each
      day as `{key, count, rows, before, program}`, where `count` and `rows` are the
      sessions a person started and `program` is `{count}`; then, when any session is
      left, `earlier` as `{key, count, oldest, before}` with `oldest` the oldest change
      time in it. With `find`: `found` alone, as `{key, count, rows, before}`. A `before`
      is None when the rows hold the whole group.

    A row is `{harness, session, path, changed, size, subagents, folder, started,
    by_program, name, model, activity, prompt, recent}`: `subagents` is how many the
    session has, None where its subagents folder could not be listed; `activity` is
    `{at, who, text}` or None, `prompt` is `{at, text}` or None, and `recent` is true for
    a session a person started that changed within `recent_s` of `now`. A session with no
    change time sorts last and is counted in `earlier`.

    Raises ValueError for a filter that is not one."""
    cat = _catalog(cat)
    f = _filters(harness, since, until, folder, find)
    now = _now(now)
    cutoff = _cutoff(now, recent_s)
    answer, entries = _counted(cat, f, now)
    if f.find is not None:
        rows, before = _page(cat, entries, FOUND_ROWS, cutoff)
        answer["groups"] = [{"key": FOUND, "count": len(entries), "rows": rows,
                             "before": before}]
        return answer
    recent, rest = _split_recent(cat, entries, cutoff)
    rows, before = _page(cat, recent, RECENT_ROWS, cutoff)
    groups: list[dict[str, Any]] = [{"key": RECENT, "count": len(recent), "rows": rows,
                                     "before": before}]
    days = _by_day(rest)
    shown = [day for day in days if day[0] != UNDATED][:DAYS]
    later = [e for key, es in days[len(shown):] for e in es]
    groups += [_day_group(cat, key, es, cutoff) for key, es in shown]
    if later:
        times = [e.changed for e in later if e.changed is not None]
        last = max((e for _, es in shown for e in es), key=lambda e: e.order,
                   default=None)
        groups.append({"key": EARLIER, "count": len(later),
                       "oldest": min(times) if times else None,
                       "before": None if last is None else position(last)})
    answer["groups"] = groups
    return answer


def newest_sessions(cat: Catalog | None = None, *, recent_s: float,
                    now: float | None = None, limit: int = NEWEST_ROWS,
                    harness: str | None = None, since: float | None = None,
                    until: float | None = None, folder: str | None = None
                    ) -> dict[str, Any]:
    """The sessions under the filters as one run, of both kinds, newest change first,
    with no groups: what `list_sessions` answers before its groups (`now`, `today`,
    `all`, `total`, `unreadable`, `roots`, `harnesses`), then `rows`, the first `limit`
    of them as a list's rows, and `before`, the place after them, None when `rows` holds
    them all. It reads the head and the last turn of the rows it returns and of no other
    session, unless `folder` is asked.

    Raises ValueError for a filter that is not one, and for a `limit` under 1."""
    cat = _catalog(cat)
    f = _filters(harness, since, until, folder, None)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError(f"limit is a number of sessions, 1 or more, not {limit!r}")
    now = _now(now)
    answer, entries = _counted(cat, f, now)
    answer["rows"], answer["before"] = _page(cat, entries, limit,
                                             _cutoff(now, recent_s))
    return answer


def more_sessions(cat: Catalog | None = None, *, group: str, recent_s: float,
                  kind: str | None = None, before: str | None = None,
                  now: float | None = None, harness: str | None = None,
                  since: float | None = None, until: float | None = None,
                  folder: str | None = None, find: str | None = None) -> dict[str, Any]:
    """The next rows of a group of the list, under the same filters.

    `group` is `recent`, `found`, a day's key (`YYYY-MM-DD`, or `undated`) or `earlier`.
    `before` is the place to go on from, as an answer gave it; None starts at the group's
    first session.

    * `recent`, a day, `found`: `{group, kind, rows, before}`, the next 30 rows and the
      place after them, None when none is left. `kind` is `person` (the default) or
      `program`; `program` is a day's alone and answers the sessions a program started
      that day. For `found`, whose rows are of both kinds, `kind` is None.
    * `earlier`: `{groups, before, left}`: the next older days, as many as hold 30
      sessions of either kind between them, each in the list's day shape; `left` is how
      many sessions remain after them, and `before` the place to go on from, None when
      none remain. The sessions with no change time come last, as the day `undated`.

    Raises ValueError for a `before` that does not parse, a group or kind that is not
    one, `found` without `find`, and any other group with it."""
    cat = _catalog(cat)
    f = _filters(harness, since, until, folder, find)
    if before is not None:
        parse_position(before)
    if kind is not None and kind not in (PERSON, PROGRAM):
        raise ValueError(f"kind is {PERSON} or {PROGRAM}, not {kind!r}")
    if not (group in (RECENT, FOUND, EARLIER) or _is_day(group)):
        raise ValueError(f"group is {RECENT}, {FOUND}, {EARLIER}, {UNDATED} or a day as "
                         f"YYYY-MM-DD, not {group!r}")
    if kind == PROGRAM and not _is_day(group):
        raise ValueError(f"only a day has rows of kind {PROGRAM}, not {group}")
    if (group == FOUND) != (f.find is not None):
        raise ValueError(f"{FOUND} is the one group of a list with `find`")
    cutoff = _cutoff(_now(now), recent_s)
    entries = _of(_pool(cat, cat.gather(), f), f.harness)

    if group == FOUND:
        rows, after = _page(cat, _after(entries, before), MORE_ROWS, cutoff)
        return {"group": group, "kind": None, "rows": rows, "before": after}
    recent, rest = _split_recent(cat, entries, cutoff)
    if group == RECENT:
        rows, after = _page(cat, _after(recent, before), MORE_ROWS, cutoff)
        return {"group": group, "kind": PERSON, "rows": rows, "before": after}
    if group == EARLIER:
        older = _after(rest, before)
        taken: list[tuple[str, list[Entry]]] = []
        held = 0
        for key, es in _by_day(older):
            if held >= EARLIER_SESSIONS:
                break
            taken.append((key, es))
            held += len(es)
        left = len(older) - held
        last = max((e for _, es in taken for e in es), key=lambda e: e.order,
                   default=None)
        return {"groups": [_day_group(cat, key, es, cutoff) for key, es in taken],
                "before": position(last) if left and last is not None else None,
                "left": left}
    person, program = _split_kinds(cat, [e for e in rest if _day(e) == group])
    kind = kind or PERSON
    rows, after = _page(cat, _after(program if kind == PROGRAM else person, before),
                        MORE_ROWS, cutoff)
    return {"group": group, "kind": kind, "rows": rows, "before": after}


def list_folders(cat: Catalog | None = None, *, harness: str | None = None,
                 since: float | None = None, until: float | None = None,
                 find: str | None = None, limit: int = FOLDERS_MAX) -> dict[str, Any]:
    """The folders the sessions under the filters ran in: `{folders, more}`.

    `folders` is `{folder, count, changed}` per folder, `count` its sessions of both
    kinds and `changed` the newest change among them, newest first, at most `limit`;
    `more` is how many folders were left off. A session whose head names no folder is in
    no folder's count. Reads every session's head not read yet.

    Raises ValueError for a filter that is not one."""
    cat = _catalog(cat)
    f = _filters(harness, since, until, None, find)
    gathered = cat.gather()
    cat.read_heads()
    entries = _of(_pool(cat, gathered, f, folder=False), f.harness)
    counts: Counter[str] = Counter()
    newest: dict[str, int] = {}
    for entry in entries:
        head = cat.kept_head(entry)
        where = head.folder if head is not None else None
        if not where:
            continue
        counts[where] += 1
        if entry.changed_ns is not None and entry.changed_ns > newest.get(where, -1):
            newest[where] = entry.changed_ns
    order = sorted(counts, key=lambda where: (-newest.get(where, -1), where))
    return {"folders": [{"folder": where, "count": counts[where],
                         "changed": (newest[where] / 1_000_000_000
                                     if where in newest else None)}
                        for where in order[:limit]],
            "more": max(0, len(order) - limit)}


# --- one session, opened ----------------------------------------------------------------------

def _spoken(record: Any) -> bool:
    """A message with text that is not the model's thinking and not a line the harness
    wrote in the model's place."""
    return (isinstance(record, Message) and not record.reasoning
            and bool(record.text.strip())
            and not (record.role == "assistant" and record.synthetic is True))


def _time(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _label(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _reply(entry: Entry) -> dict[str, Any] | None:
    """The model's newest message of the last turn, when no user message of any kind
    follows it; None when there is none, or the turn could not be read."""
    try:
        records = window(entry.listed, turns=1).records
    except (Unreadable, OSError, ValueError):
        return None
    for record in reversed(records):
        if not _spoken(record):
            continue
        if record.role == "assistant":
            return {"at": _time(record.time), "text": record.text}
        if record.role == "user":
            return None
    return None


def _subagent(sub: Any) -> dict[str, Any]:
    """One subagent's line: its labels, its start from the head of its record, and from
    its last turn the time of its newest record and the text of its newest message."""
    out: dict[str, Any] = {"description": _label(sub.description),
                           "agent_type": _label(sub.agent_type), "started": None,
                           "last_at": None, "last": None}
    try:
        page = window(sub, turns=1)
    except (Unreadable, OSError, ValueError):
        return out
    for record in reversed(page.records):
        if out["last_at"] is None:
            out["last_at"] = _time(record.time)
        if out["last"] is None and _spoken(record):
            out["last"] = record.text
        if out["last_at"] is not None and out["last"] is not None:
            break
    try:
        out["started"] = _time(page.header.started)
    except (Unreadable, OSError, ValueError):
        pass
    return out


def open_session(cat: Catalog | None = None, *, harness: str, session: str,
                 recent_s: float, now: float | None = None) -> dict[str, Any] | None:
    """One main session, opened: its row (as a list's), plus

    * `reply`: `{at, text}`, the model's newest message after the newest user message of
      any kind in the session's last turn; None when there is none. It says nothing of
      whether the session is working.
    * `subagents`: up to 8 of its subagents, in their listing's order, each
      `{description, agent_type, started, last_at, last}`: `started` from the head of its
      record, `last_at` the time of its newest record and `last` the text of its newest
      message. `more` is how many were not listed. Both are None when the session's
      subagents could not be listed; `more` alone is None when some of them could not
      be, since how many there are is then not known.
    * `resume`: `{command, folder}`, the harness's command for picking the session up
      again and where to run it; None for a harness with none.

    None when the catalog holds no such main session."""
    cat = _catalog(cat)
    cutoff = _cutoff(_now(now), recent_s)
    entry = next((e for e in cat.gather().entries
                  if e.harness == harness and e.session == session), None)
    if entry is None:
        return None
    row = _row(cat, entry, cutoff)
    row["reply"] = _reply(entry)
    listing = subagents(entry.listed)
    if listing.state in (LISTED, NONE):
        row["subagents"] = [_subagent(s) for s in listing.entries[:SUBAGENTS_MAX]]
        row["more"] = (None if listing.unreadable_parts
                       else max(0, len(listing.entries) - SUBAGENTS_MAX))
    else:
        row["subagents"] = None
        row["more"] = None
    row["resume"] = resume(entry.harness, entry.session, row["folder"])
    return row


__all__ = ["RECENT_ROWS", "DAY_ROWS", "FOUND_ROWS", "MORE_ROWS", "EARLIER_SESSIONS",
           "NEWEST_ROWS", "DAYS", "FOLDERS_MAX", "SUBAGENTS_MAX", "FIND_MIN", "RECENT",
           "FOUND", "EARLIER", "UNDATED", "PERSON", "PROGRAM", "position",
           "parse_position", "local_day", "list_sessions", "newest_sessions",
           "more_sessions", "list_folders", "open_session"]
