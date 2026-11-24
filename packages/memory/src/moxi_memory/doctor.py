"""memory's doctor checks: `rows()`, which the announcement's `DOCTOR` names.

- index: the current generation, its schema and tokenizer, which secrets `redact` stored
  its rows (`redact version N`), rows per tier, whether the first full read is still
  filling it, a rebuild running, older generations left to remove and the sessions the
  last rebuild dropped and kept. `warn` with no index yet, when a rebuild was killed
  before it finished and none has finished since, and while the redact version is below
  the installed secrets' (rows stored before may hold secrets today's `redact` catches,
  until `moxi memory reindex` redacts them again). It says how many rows record no
  writer, told apart: those stored before rows recorded one, which `moxi memory
  reindex` reads again (or a generation at schema 1 that memory's next write brings up
  to date); those of compressed Codex rollouts, which no reindex reads; and how many
  sessions wait on a folder that didn't answer, which each walk reads again. The chat's
  `search_memory` leaves out every row with no writer.
- ingest: each harness's last catch-up walk, the sources changed since it (behind, the
  same count as `moxi memory status`'s backlog), the compressed Codex rollouts memory
  can't read, and a request waiting for the writer lock. A request queued behind a
  running rebuild, or one younger than `WAITING_WARN`, is said and needs nothing; an
  older one is a `warn` whose fix is `moxi memory ingest`, since any child that takes
  and frees the lock starts it. A failed child's message is the core's record row
  `record ingest`, not this one.
- folders: each folder of each harness as the last run found it, from the index's
  `meta`, testing none itself. `warn` for one that didn't answer (since when and why:
  offline, failed during the run with its first error's path, or lists nothing with which
  fact holds), for an archive folder never listed whole, and for each folder walked before that sessions'
  config no longer names.
- sets: memory's redaction sets load in secrets. `fail` when one can't run: every child
  stops on it.
- start: memory is on turns' start list for each harness it reads, and its listener rows
  import. `warn` when it is off the list (no start line, no lists); `fail` when a row
  doesn't import.
- sessions: rows in the session table, with an identity, settled, and with each launch
  kind. `warn` when memory's state store can't be opened or read.
- links: whether comms, the launcher and notes load, and what memory does without each.
- retention: each harness's transcript retention, from sessions. `warn` where sessions
  warns.
- subconscious: for each live session whose launch turned the subconscious on, whether a
  recall route can run in it now, checked as a recall's own try checks it (a Codex
  session, or a Claude Code session whose turns offers no `fork`, has it off), naming the
  reason only while it holds; while memory's config file is in error, each recall it
  stopped (its line `lost` with the reason `config`) and the error; and each recall line
  still open or kept whose child and taker are gone, with its age, until a taker settles
  it. `warn` for each of those. With the subconscious on in a live session it also says
  that a recall fork's permission asks are refused. Recall runs on Claude Code's subagent
  route only.

No row writes anything: the index is opened to read, and the state store only when it
is there.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import partial

from moxi.doctor import Row

MODULE = "memory"
CONFIG = "moxi config memory"
REINDEX = "moxi memory reindex"
#: A request waiting this many seconds for the writer lock, with no rebuild running, is
#: a `warn`: the child that held the lock didn't start it on its way out.
WAITING_WARN = 600.0


def rows() -> Iterator[Row]:
    """Every check's row."""
    from moxi.config import ConfigError

    from . import settings

    try:
        config = settings.read()
    except ConfigError as exc:
        yield Row(MODULE, "config", "fail", f"memory's config can't be read: {exc}", CONFIG)
        config = None
    harnesses = tuple(config["harnesses"]) if config else settings.HARNESSES
    checks = (("index", _index, ()), ("ingest", _ingest, (harnesses,)),
              ("folders", _roots_row, (harnesses,)), ("sets", _sets, (config,)),
              ("start", _start, (harnesses,)),
              ("sessions", _sessions, ()), ("links", _links, ()),
              ("retention", _retention, (harnesses,)),
              ("subconscious",
               partial(_subconscious, config_in_error=config is None), ()))
    for name, check, args in checks:
        try:
            yield check(*args)
        except Exception as exc:  # noqa: BLE001 - one broken check never hides the rest
            yield Row(MODULE, name, "fail", f"the check failed: {exc}")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# -- index ------------------------------------------------------------------------------

def _index() -> Row:
    import sqlite3

    from . import index, ingest
    from . import state as state_mod
    from .ingest import REPLACED_KEY

    try:
        reader = index.open_read()
    except index.IndexMissing:
        return Row(MODULE, "index", "warn", "no index yet; the first ingest makes it",
                   REINDEX)
    except index.IndexUnreadable as exc:
        return Row(MODULE, "index", "fail", f"the index can't be read: {exc}", REINDEX)
    with reader as r:
        conn = r.conn
        tiers = conn.execute("SELECT count(*) FROM rows").fetchone()[0]
        tails = conn.execute("SELECT count(*) FROM tails").fetchone()[0]
        schema = index.meta_get(conn, "schema")
        tokenizer = index.meta_get(conn, "tokenizer")
        filling = str(index.meta_get(conn, "first_read") or "0") in ("0", "")
        dropped = index.meta_get(conn, "dropped")
        kept = index.meta_get(conn, "kept")
        replaced = index.meta_get(conn, REPLACED_KEY)
        stored = index.stored_redact_version(conn)
        try:     # a state store that can't be read is the sessions row's to say
            alias = ("st" if r.attach_state("st") and state_mod.has_waiting(conn, "st")
                     else None)
        except sqlite3.DatabaseError:
            alias = None
        unwritten = ingest.writerless(conn, alias)
        waiting = state_mod.waiting_sessions(conn, alias) if alias else 0
        generation = r.generation
    parts = [f"generation {generation}", f"schema {schema}", f"tokenizer {tokenizer}",
             f"redact version {stored}", f"{tiers} messages and calls",
             f"{tails} result tails"]
    state = "ok"
    from .rows import redact_version

    now = redact_version()
    if stored < now:
        parts.append(f"rows stored under redact version {stored} may hold secrets that "
                     f"secrets' redact version {now} catches; run `{REINDEX}` once to "
                     "redact them again")
        state = "warn"
    if unwritten is None:
        parts.append("its rows record no writer until memory next writes to the index")
    else:
        if unwritten.fixable:
            parts.append(f"{_plural(unwritten.fixable, 'row')} stored before rows "
                         "recorded their writer, left out of the chat's search until "
                         f"`{REINDEX}`")
        if unwritten.compressed:
            parts.append(f"{_plural(unwritten.compressed, 'row')} of compressed Codex "
                         "rollouts, which sessions can't read: no reindex gives them a "
                         "writer, so the chat's search never finds them")
    if waiting:
        parts.append(f"{_plural(waiting, 'session')} waiting on a folder that didn't "
                     "answer: each walk reads them again until they read cleanly")
    if filling:
        parts.append("still filling: the first full read hasn't finished")
    running = index.rebuild_running()
    if running is not None:
        from .clock import show

        parts.append(f"a rebuild is running since {show(running.started)}"
                     if running.started else "a rebuild is running")
    older = [n for n in index.generations() if n != generation]
    if older:
        parts.append(f"{_plural(len(older), 'older generation')} left to remove")
    if dropped not in (None, "", 0, "0"):
        parts.append(f"the last rebuild dropped {dropped} sessions whose transcripts are "
                     "gone")
    if replaced is not None:
        parts.append(f"the last rebuild redacted again the rows stored under redact "
                     f"version {replaced}: it dropped no session, and kept what it couldn't "
                     f"read to its end of {kept or 0} sessions")
    elif kept not in (None, "", 0, "0"):
        parts.append(f"the last rebuild kept {kept} sessions it couldn't read, from folders "
                     "that didn't answer it or compressed rollouts")
    from . import state as store

    _, unfinished = store.peek()
    if unfinished is not None:
        from .clock import show

        parts.append(f"a rebuild started at {show(unfinished)} did not finish")
        state = "warn"
    return Row(MODULE, "index", state, "; ".join(parts),
               REINDEX if state == "warn" else None)


# -- ingest -----------------------------------------------------------------------------

def _ingest(harnesses) -> Row:
    import os

    from moxi_sessions import api

    from . import index, ingest, status
    from .clock import show

    waiting, waiting_warn = _waiting()
    try:
        reader = index.open_read()
    except (index.IndexMissing, index.IndexUnreadable):
        return Row(MODULE, "ingest", "warn", "; ".join(
            ["no index yet, so nothing is ingested", *waiting]), REINDEX)
    with reader as r:
        walks = {h: index.meta_get(r.conn, f"last_walk.{h}") for h in harnesses}
        compressed = ingest.not_read(r.conn)
        # the count `moxi memory status` shows as its backlog
        backlog = status.backlog(r.conn) or {}
    parts = []
    state = "ok"
    for harness, last in walks.items():
        root = api.root(harness)
        if not os.path.lexists(root):
            # a walk counts such a harness as read: it has nothing to read
            parts.append(f"{harness}: nothing to read, no root at {root}")
            continue
        if last is None:
            parts.append(f"{harness}: no walk yet")
            state = "warn"
            continue
        behind = backlog.get(harness)
        said = (f"{_plural(behind, 'source')} changed since" if behind is not None
                else "its root can't be listed")
        parts.append(f"{harness}: last walk {show(float(last))}, {said}")
    if compressed:
        parts.append(f"{_plural(compressed, 'Codex session')} not read: Codex compressed "
                     "the rollout, which sessions can't read")
        state = "warn"
    parts += waiting
    if waiting_warn:
        state = "warn"
    return Row(MODULE, "ingest", state, "; ".join(parts) or "no harness configured",
               "moxi memory ingest" if state == "warn" else None)


#: The fix for an archive folder that didn't answer, or was never listed whole.
ANSWER_FIX = ("make it answer, or take it off sessions' config; its sessions then leave at "
              "the next `moxi memory reindex`")
#: The fix for a live root that didn't answer: it can't be taken off sessions' config.
LIVE_FIX = ("a live root answers again once a session lands there; to stop reading that "
            "harness, take it out of memory's `harnesses`")
#: The fix for an archive folder that lists nothing, though seen holding something.
EMPTIED_FIX = ("an archive folder emptied on purpose: take it off sessions' config, and its "
               "sessions leave at the next `moxi memory reindex`")
#: The fix for a folder walked before that sessions' config no longer names.
REMOVED_FIX = "put it back in sessions' config, or let them go"


def _roots_row(harnesses) -> Row:
    """Each folder as the last run found it, from the index; tests no folder."""
    from . import roots, index, status

    try:
        with index.open_read() as reader:
            records = roots.read_all(reader.conn)
    except (index.IndexMissing, index.IndexUnreadable):
        return Row(MODULE, "folders", "off", "no index yet, so no folder has been walked")
    records = status.against_config({h: records.get(h, []) for h in harnesses
                                     if h in records})
    lines = roots.lines(records)
    count = sum(len(recs) for recs in records.values())
    if not lines:
        return Row(MODULE, "folders", "ok",
                   f"{_plural(count, 'folder')} answered the last run" if count
                   else "no walk yet")
    fixes = []
    stuck = [r for recs in records.values() for r in recs
             if r.in_config and (not r.answered or (r.is_archive and not r.listed_whole)
                                 or r.missed_rebuild_at is not None)]
    if any(r.is_archive for r in stuck):
        fixes.append(ANSWER_FIX)
    if any(not r.is_archive for r in stuck):
        fixes.append(LIVE_FIX)
    if any(r.in_config and r.is_archive and not r.answered and r.why == roots.EMPTY
           for recs in records.values() for r in recs):
        fixes.append(EMPTIED_FIX)
    if any(not r.in_config for recs in records.values() for r in recs):
        fixes.append(f"for a folder no longer named: {REMOVED_FIX}")
    return Row(MODULE, "folders", "warn", "; ".join(lines), "; ".join(fixes) or None)


def _waiting(now: float | None = None) -> tuple[list[str], bool]:
    """The ingest row's words for a request waiting for the writer lock, and whether it
    is a `warn`."""
    import time

    from . import index, state
    from .clock import show

    request, _ = state.peek()
    if request is None:
        return [], False
    what = f"a {request.kind} request waiting since {show(request.asked)}"
    running = index.rebuild_running()
    if running is not None:
        return [f"{what}, queued behind the rebuild running since "
                f"{show(running.started) if running.started else 'a moment ago'}; "
                "it runs when that rebuild ends"], False
    now = time.time() if now is None else now
    if now - request.asked < WAITING_WARN:
        return [f"{what}, for the ingest holding the writer lock"], False
    return [f"{what}: no child has started it; any child that takes and frees the "
            "writer lock does"], True


# -- sets -------------------------------------------------------------------------------

def _sets(config) -> Row:
    import moxi_secrets

    if config is None:
        return Row(MODULE, "sets", "off", "memory's config can't be read", CONFIG)
    sets = tuple(config["redact.sets"])
    unknown = [s for s in sets if s not in moxi_secrets.SETS]
    if unknown:
        # Remove one unknown name; with no known name left, go back to the default sets.
        fix = ("moxi config unset memory redact.sets" if len(unknown) == len(sets) else
               f"moxi config set memory redact.sets --remove {unknown[0]}")
        known = ", ".join(moxi_secrets.SETS)
        return Row(MODULE, "sets", "fail", f"secrets has no set {', '.join(unknown)}; "
                   f"every ingest stops on it. Secrets' sets: {known}", fix)
    try:
        moxi_secrets.redact("memory doctor", sets)
    except moxi_secrets.SetUnavailable as exc:
        return Row(MODULE, "sets", "fail", f"a redaction set can't run: {exc}; every "
                   "ingest stops on it", "moxi doctor secrets")
    return Row(MODULE, "sets", "ok", f"{', '.join(sets)} load in secrets")


# -- start ------------------------------------------------------------------------------

def _start(harnesses) -> Row:
    import importlib

    import moxi_turns

    from .listeners import LISTENERS

    broken = []
    for _events, target in LISTENERS:
        module, _, name = target.partition(":")
        try:
            getattr(importlib.import_module(module), name)
        except Exception as exc:  # noqa: BLE001 - a row that doesn't import is said
            broken.append(f"{target}: {exc}")
    if broken:
        return Row(MODULE, "start", "fail", "listener rows don't import: "
                   + "; ".join(broken), "reinstall memory: moxi install memory")
    off = []
    for harness in harnesses:
        plan = moxi_turns.start_plan(harness)
        if not any(e.name == MODULE for e in plan.entries):
            why = dict(plan.skipped).get(MODULE, "not on start.order")
            off.append(f"{harness} ({why})")
    if off:
        return Row(MODULE, "start", "warn", "memory adds no start line or lists for "
                   + ", ".join(off), "moxi config set turns start.order --add memory")
    return Row(MODULE, "start", "ok", f"on turns' start list; {len(LISTENERS)} listener "
               "rows import")


# -- sessions ---------------------------------------------------------------------------

def _sessions() -> Row:
    import sqlite3

    from moxi.store import StoreError

    from . import state

    if not state.path().exists():
        return Row(MODULE, "sessions", "ok", "no session table yet: no session has "
                   "started or been read since install")
    try:
        conn = state.connect(timeout=2.0, create=False)
    except (StoreError, state.Late, sqlite3.DatabaseError) as exc:
        return Row(MODULE, "sessions", "warn", f"the session table can't be opened: {exc}")
    try:
        total, with_identity, settled = conn.execute(
            "SELECT count(*), count(identity), coalesce(sum(settled), 0) FROM sessions"
        ).fetchone()
        kinds = conn.execute("SELECT coalesce(kind, 'none'), count(*) FROM sessions "
                             "GROUP BY 1 ORDER BY 1").fetchall()
    except sqlite3.DatabaseError as exc:
        return Row(MODULE, "sessions", "warn", f"the session table can't be read: {exc}")
    finally:
        conn.close()
    by_kind = ", ".join(f"{kind} {n}" for kind, n in kinds) or "none"
    return Row(MODULE, "sessions", "ok", f"{_plural(total, 'session')}, "
               f"{with_identity} with an identity, {settled} settled; kinds: {by_kind}")


# -- links ------------------------------------------------------------------------------

_WITHOUT = {
    "comms": "no identities: no agent list, and the project list names harnesses",
    "launcher": "no launch kinds: every session is searched by default and may be listed",
    "notes": "a recall reads past sessions only, no notes",
}


def _links() -> Row:
    from . import links

    parts = []
    state = "ok"
    for name, link in (("comms", links.comms()), ("launcher", links.launcher()),
                       ("notes", links.notes())):
        if link.ready:
            parts.append(f"{name} loads")
        elif link.state == links.FAILING:
            parts.append(f"{name} fails to load ({link.reason}): {_WITHOUT[name]}")
            state = "warn"
        else:
            parts.append(f"{name} not linked ({link.reason}): {_WITHOUT[name]}")
    return Row(MODULE, "links", state, "; ".join(parts))


# -- retention --------------------------------------------------------------------------

def _retention(harnesses) -> Row:
    from moxi_sessions import api

    items = [item for item in api.retention() if item.harness in harnesses]
    warn = any(item.warn for item in items)
    return Row(MODULE, "retention", "warn" if warn else "ok",
               "; ".join(item.brief for item in items) or "no harness configured",
               "moxi doctor sessions" if warn else None)


# -- the subconscious --------------------------------------------------------------------

#: The most recalls the subconscious row names in one of its lists.
SUBCONSCIOUS_SHOWN = 5
#: A line this young whose owner is gone is not listed: its child may not have written
#: its identity yet.
UNTAKEN_GRACE_S = 30.0
SUBCONSCIOUS_LIST = "moxi memory subconscious list"


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _shown(items: list[str]) -> str:
    more = len(items) - SUBCONSCIOUS_SHOWN
    return ", ".join(items[:SUBCONSCIOUS_SHOWN]) + (f" and {more} more" if more > 0 else "")


def live_subconscious() -> list[tuple[str, str, str | None]]:
    """(session id, mode, the reason no route can run, or None) for each live session
    turns serves whose launch turned the subconscious on."""
    import moxi_turns as client

    from . import recall_start

    out = []
    for session in client.sessions():
        with session:
            try:
                stamps = session.env([recall_start.SUBCONSCIOUS_ENV])
            except Exception:  # noqa: BLE001 - an endpoint that can't read it is passed by
                continue
            run_mode = recall_start.mode({k: v for k, v in stamps.items() if v is not None})
            if run_mode is None or session.session_id is None:
                continue
            harness = session.harness
            if harness == "codex":
                why = recall_start.CODEX
            elif harness != "claude" or "fork" not in session.capabilities:
                why = recall_start.NO_FORK
            else:
                why = None
            out.append((session.session_id, run_mode, why))
    return out


def _never_ran(lines: list[dict], *, now: float) -> str | None:
    """The recalls whose child never ran: lines lost with reason `spawn` in the last day
    (the child could not be started), and records `starting` or `hurried` with no fork
    and no line naming a child past `records.STARTING_GRACE_S` (the child ended before
    it wrote its line, or found no identity of its own to write), which the recall
    guards no longer wait on. None when there are none."""
    from . import recall_guard, recall_start, recalls, records

    said = []
    spawn = [line for line in lines if line.get("state") == recalls.LOST
             and line.get("reason") == recall_start.SPAWN
             and now - float(line.get("at") or 0) < records.SWEEP_AFTER_S]
    if spawn:
        said.append(f"{_plural(len(spawn), 'recall')} in the last day whose child could "
                    f"not start ({_shown([str(x.get('recall')) for x in spawn[::-1]])}): "
                    f"{spawn[-1].get('error') or 'no error recorded'}")
    named = {line.get("recall") for line in lines if recall_guard.line_child(line)}
    silent = [record for session_id in records.session_ids()
              for record in records.session_records(session_id)
              if record.get("state") in (records.STARTING, records.HURRIED)
              and not record.get("fork") and record.get("recall") not in named
              and now - float(record.get("at") or now) >= records.STARTING_GRACE_S]
    if silent:
        ages = [f"{r.get('recall')} ({r.get('state')}, "
                f"{_age(now - float(r.get('at') or now))} old)" for r in silent[::-1]]
        said.append(f"{_plural(len(silent), 'recall')} with no log line naming its child "
                    f"(gone before it wrote one, or with no identity to write): "
                    f"{_shown(ages)}")
    return "; ".join(said) or None


def _config_losses(lines: list[dict]) -> list[dict]:
    """The lines lost with reason `config` since memory's config last read cleanly for a
    recall: those opened after the newest line whose child got past its config read (its
    first search noted on it). A loss to an error fixed since is left out. `lines` are
    oldest first."""
    from . import recall_child, recalls

    out: list[dict] = []
    for line in lines:
        if line.get("state") == recalls.LOST and line.get("reason") == recall_child.CONFIG:
            out.append(line)
        elif line.get("prefetch_hits") is not None:
            out = []
    return out


def _subconscious(*, config_in_error: bool, now: float | None = None) -> Row:
    import time

    from . import recall_take, recalls

    now = time.time() if now is None else now
    parts: list[str] = []
    warn = False
    try:
        live = live_subconscious()
    except Exception as exc:  # noqa: BLE001 - turns that can't list its sessions
        live = []
        parts.append(f"turns can't list its live sessions ({exc}), so none is checked")
    on = [sid for sid, _mode, why in live if why is None]
    off = [f"{sid} ({why})" for sid, _mode, why in live if why is not None]
    if on:
        parts.append(f"on in {_plural(len(on), 'live session')}, on Claude Code's "
                     "subagent route; a recall fork's permission asks are refused")
    if off:
        warn = True
        parts.append(f"off for want of a route in {_shown(off)}")
    lines = recalls.all_lines()
    if config_in_error:
        lost = _config_losses(lines)
        if lost:
            warn = True
            newest = lost[-1]
            parts.append(f"{_plural(len(lost), 'recall')} stopped while memory's config "
                         f"is in error ({_shown([str(x.get('recall')) for x in lost[::-1]])}"
                         f"): {newest.get('error') or 'no error recorded'}")
    untaken = [line for line in lines
               if now - float(line.get("updated") or line.get("at") or 0) >= UNTAKEN_GRACE_S
               and recall_take.takeable(line)]
    if untaken:
        warn = True
        named = [f"{line.get('recall')} ({line.get('state')}, "
                 f"{_age(now - float(line.get('updated') or line.get('at') or now))} old)"
                 for line in untaken[::-1]]
        parts.append(f"{_plural(len(untaken), 'recall line')} not yet taken, child gone: "
                     f"{_shown(named)}; the next recall child or session end takes it")
    never_ran = _never_ran(lines, now=now)
    if never_ran:
        warn = True
        parts.append(never_ran)
    if not parts:
        parts.append("on in no live session")
    fix = CONFIG if config_in_error and warn else (SUBCONSCIOUS_LIST if warn else None)
    return Row(MODULE, "subconscious", "warn" if warn else "ok", "; ".join(parts), fix)


__all__ = ["rows", "live_subconscious"]
