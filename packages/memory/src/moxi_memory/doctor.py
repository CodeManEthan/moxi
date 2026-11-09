"""memory's doctor checks: `rows()`, which the announcement's `DOCTOR` names.

- index: the current generation, its schema and tokenizer, rows per tier, whether the
  first full read is still filling it, a rebuild running, older generations left to
  remove and the sessions the last rebuild dropped. `warn` with no index yet.
- ingest: each harness's last catch-up walk, the sources changed since it (behind), and
  the compressed Codex rollouts memory can't read. A failed child's message is the
  core's record row `record ingest`, not this one.
- sets: memory's redaction sets load in secrets. `fail` when one can't run: every child
  stops on it.
- start: memory is on turns' start list for each harness it reads, and its listener rows
  import. `warn` when it is off the list (no start line, no lists); `fail` when a row
  doesn't import.
- sessions: rows in the session table, with an identity, settled, and with each launch
  kind.
- links: whether comms and the launcher load, and what memory does without each.
- retention: each harness's transcript retention, from sessions. `warn` where sessions
  warns.

No row writes anything: the index is opened to read, and the state store only when it
is there.
"""

from __future__ import annotations

from collections.abc import Iterator

from moxi.doctor import Row

MODULE = "memory"
INGEST_ALL = "moxi memory ingest --all"
CONFIG = "moxi config memory"


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
              ("sets", _sets, (config,)), ("start", _start, (harnesses,)),
              ("sessions", _sessions, ()), ("links", _links, ()),
              ("retention", _retention, (harnesses,)))
    for name, check, args in checks:
        try:
            yield check(*args)
        except Exception as exc:  # noqa: BLE001 - one broken check never hides the rest
            yield Row(MODULE, name, "fail", f"the check failed: {exc}")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# -- index ------------------------------------------------------------------------------

def _index() -> Row:
    from . import index

    try:
        reader = index.open_read()
    except index.IndexMissing:
        return Row(MODULE, "index", "warn", "no index yet; the first ingest makes it",
                   INGEST_ALL)
    except index.IndexUnreadable as exc:
        return Row(MODULE, "index", "fail", f"the index can't be read: {exc}",
                   "moxi memory reindex")
    with reader as r:
        conn = r.conn
        tiers = conn.execute("SELECT count(*) FROM rows").fetchone()[0]
        tails = conn.execute("SELECT count(*) FROM tails").fetchone()[0]
        schema = index.meta_get(conn, "schema")
        tokenizer = index.meta_get(conn, "tokenizer")
        filling = str(index.meta_get(conn, "first_read") or "0") in ("0", "")
        dropped = index.meta_get(conn, "dropped")
        generation = r.generation
    parts = [f"generation {generation}", f"schema {schema}", f"tokenizer {tokenizer}",
             f"{tiers} messages and calls", f"{tails} result tails"]
    state = "ok"
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
    return Row(MODULE, "index", state, "; ".join(parts))


# -- ingest -----------------------------------------------------------------------------

def _ingest(harnesses) -> Row:
    from moxi_sessions import api

    from . import index, ingest
    from .clock import show

    try:
        reader = index.open_read()
    except (index.IndexMissing, index.IndexUnreadable):
        return Row(MODULE, "ingest", "warn", "no index yet, so nothing is ingested",
                   INGEST_ALL)
    with reader as r:
        walks = {h: index.meta_get(r.conn, f"last_walk.{h}") for h in harnesses}
        compressed = ingest.not_read(r.conn)
    parts = []
    state = "ok"
    for harness, last in walks.items():
        if last is None:
            parts.append(f"{harness}: no walk yet")
            state = "warn"
            continue
        try:
            listing = api.sources(harness, modified_since=float(last), heads=False)
            behind = (f"{_plural(len(listing), 'source')} changed since"
                      if listing.ok else f"root not listed ({listing.reason})")
        except Exception as exc:  # noqa: BLE001 - a root that can't be listed is said
            behind = f"root not listed ({exc})"
        parts.append(f"{harness}: last walk {show(float(last))}, {behind}")
    if compressed:
        parts.append(f"{_plural(compressed, 'Codex session')} not read: Codex compressed "
                     "the rollout, which sessions can't read")
        state = "warn"
    return Row(MODULE, "ingest", state, "; ".join(parts) or "no harness configured",
               "moxi memory ingest" if state == "warn" else None)


# -- sets -------------------------------------------------------------------------------

def _sets(config) -> Row:
    import moxi_secrets

    if config is None:
        return Row(MODULE, "sets", "off", "memory's config can't be read", CONFIG)
    sets = tuple(config["redact.sets"])
    unknown = [s for s in sets if s not in moxi_secrets.SETS]
    if unknown:
        return Row(MODULE, "sets", "fail", f"secrets has no set {', '.join(unknown)}; "
                   "every ingest stops on it", "moxi config set memory redact.sets ...")
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
    from moxi.store import StoreError

    from . import state

    if not state.path().exists():
        return Row(MODULE, "sessions", "ok", "no session table yet: no session has "
                   "started or been read since install")
    try:
        conn = state.connect(timeout=2.0, create=False)
    except (StoreError, state.Late) as exc:
        return Row(MODULE, "sessions", "warn", f"the session table can't be opened: {exc}")
    try:
        total, with_identity, settled = conn.execute(
            "SELECT count(*), count(identity), coalesce(sum(settled), 0) FROM sessions"
        ).fetchone()
        kinds = conn.execute("SELECT coalesce(kind, 'none'), count(*) FROM sessions "
                             "GROUP BY 1 ORDER BY 1").fetchall()
    finally:
        conn.close()
    by_kind = ", ".join(f"{kind} {n}" for kind, n in kinds) or "none"
    return Row(MODULE, "sessions", "ok", f"{_plural(total, 'session')}, "
               f"{with_identity} with an identity, {settled} settled; kinds: {by_kind}")


# -- links ------------------------------------------------------------------------------

_WITHOUT = {
    "comms": "no identities: no agent list, and the project list names harnesses",
    "launcher": "no launch kinds: every session is searched by default and may be listed",
}


def _links() -> Row:
    from . import links

    parts = []
    state = "ok"
    for name, link in (("comms", links.comms()), ("launcher", links.launcher())):
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


__all__ = ["rows"]
