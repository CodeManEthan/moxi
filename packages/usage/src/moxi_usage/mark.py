"""The warn-at mark: turns' after-tool listener that says when a context passes its mark.

turns calls `on_tool(event)` after each tool call it lets reach usage's row, in the
session's endpoint, and `floor(event)` at each `ready` and after each call of the row, for
the context below which the main loop's calls skip the row. `gone(event)` is called when
the endpoint is ending.

**Readers and purposes.** A reader is the main session or one of its agents; each has
its own context and its own marks. A reader carries up to two purposes, each with a mark
(`1x`, `1.5x`, `2x`, a multiple of the restart point, `moxi_usage.thresholds`):

* the **notice**, one line to the model. Its mark is the launch's `MOXI_USAGE_WARN_AT`;
  with nothing stamped, config `marks.default` (default `off`). A stamp of `off`, or one
  that names no mark, is off.
* the **crossing**, for the main session only: handlers other modules register on the
  group `moxi.usage.crossings` are called with a `Crossing`. Its mark is
  `MOXI_USAGE_CROSSING_AT`, which the launcher stamps when its recycle at limit is on;
  there is no config default.

With both off the listener returns at once, having imported nothing beyond this module.

**The measure.** The reader's newest request, by sessions' `last_request`: its input
tokens are the whole prompt. turns hands the file the harness writes (`record_path`);
the main session's source is built from it, or found by session id where a harness names
no file. An agent's source is its own file: on Codex the event's `record_path` already
is; elsewhere it is the session's `subagents()` entry for the agent. Each source is held
per reader, with no size bound, so each later read sees the file as it has grown. The
threshold is the mark's for the request's model, in the `main` population for the main
session and `subagent` for an agent; thresholds are kept per (model, mark, population)
and worked out again when the measure, the model table or usage's config changes.

**Firing.** A purpose fires the first time its reader's context is at or above its
threshold while it is armed, and is then fired. It is armed again by a later reading of
the same reader below the threshold (after a compaction, or a clear that keeps the
session id). So a mark fires once per rise past its threshold, not once per session.

**Delivery.** The answer is the notice when one fired, then the lines crossing handlers
returned. A notice for an agent is the agent's own line, and leaves one line for its
parent, `subagent agent-<id> <line>`; the main session's next call adds its agents'
waiting lines after its own. Waiting lines live in memory only.

**State.** In memory per session, in the endpoint's process. Only the fired purposes are
kept on disk, in `STATE/usage/marks/<harness>-<session_id>.json` (written with
`moxi.files.update` on a firing and on a re-arm), so an endpoint that starts again does
not fire twice; `gone` deletes the files of the sessions the endpoint held, and the
doctor's `marks` row sweeps any older than `marks.max_age_days`.

**The floor.** For an event with an `agent_id` it is 0: turns gates only the main loop by
it, and after an agent's call the parent's next call must come, to take its line. For
the main session it is 0 while a line waits or a main purpose is fired (so the reading
that re-arms it is seen); otherwise the smallest threshold among the main session's armed
purposes for the models it has used, or over every model the measure has a row for when
it has used none yet. With no threshold known it is 0; with both purposes off it is
`NEVER`, since the listener has nothing to do. turns reads the floor again at each
`ready` and after each call, so a change to `marks.default` reaches a main loop held off
by `NEVER` at its next start, resume, clear or compaction, or after an agent's call.

**Fail open.** Every error is logged once per kind and the listener answers None. A
crossing handler's error, or one that runs past `marks.handler_wait_ms`, costs only its
own line.
"""

from __future__ import annotations

import _thread
import os

#: The launcher's stamps.
WARN_AT = "MOXI_USAGE_WARN_AT"
CROSSING_AT = "MOXI_USAGE_CROSSING_AT"
#: The selectable marks (`moxi_usage.thresholds.MARKS`, repeated so the off path imports
#: nothing), and the absence of one.
MARKS = ("1x", "1.5x", "2x")
OFF = "off"
#: The purposes a reader can carry.
NOTICE = "notice"
CROSSING = "crossing"
PURPOSES = (NOTICE, CROSSING)
#: The group crossing handlers register on.
GROUP = "moxi.usage.crossings"
#: The fired purposes' directory under usage's state directory, and the file's schema.
STATE_DIR = "marks"
SCHEMA = 1
#: The floor when the listener has nothing to do for the main loop: the largest integer
#: a JSON reader with doubles keeps exact.
NEVER = 2 ** 53 - 1
#: The reader key of the main session.
MAIN_READER = "main"
#: How a line left for a parent names its agent: this many characters of the id.
AGENT_SHOWN = 8
#: The logger a problem goes to.
LOGGER = "moxi.usage"


# --- selection --------------------------------------------------------------------------

_UNSTAMPED = object()


def _stamped(value) -> object:
    """A stamp's mark, None for off (or a value that names no mark), `_UNSTAMPED` for no
    stamp."""
    if value is None or value == "":
        return _UNSTAMPED
    return value if value in MARKS else None


def _env(event) -> dict:
    env = getattr(event, "env", None)
    return env if isinstance(env, dict) else os.environ


def selected(env) -> tuple[str | None, str | None]:
    """The notice's mark and the crossing's, each None for off, from a session's
    environment and, for a notice nothing stamped, config `marks.default`."""
    notice = _stamped(env.get(WARN_AT))
    if notice is _UNSTAMPED:
        notice = config_default()
    crossing = _stamped(env.get(CROSSING_AT))
    if crossing is _UNSTAMPED:
        crossing = None
    return notice, crossing


_default_cache: list = [None, None]


def config_default() -> str | None:
    """Config `marks.default` as a mark, None for off; read again only when usage's
    config file changes. A config that cannot be read is off, logged once."""
    from moxi.paths import Layout

    path = Layout("usage").config_file()
    key = (str(path), _stat(path))
    if _default_cache[0] != key:
        try:
            from .settings import current

            value = current()["marks.default"]
        except Exception:  # noqa: BLE001 - fail open: a config usage can't read is off
            _fail("config", "usage's config could not be read; marks.default is off")
            value = OFF
        _default_cache[:] = [key, value if value in MARKS else None]
    return _default_cache[1]


def _stat(path) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns, st.st_ino)


# --- the crossing -----------------------------------------------------------------------

class Crossing:
    """One rise of the main session's context past its crossing mark.

    `mark` is the crossing's mark, `tokens` the context that crossed, `threshold` the
    mark's threshold in tokens for `model`, and `at` the time of the reading that crossed
    (epoch seconds)."""

    __slots__ = ("harness", "session_id", "mark", "tokens", "threshold", "model", "at")

    def __init__(self, harness: str, session_id: str, mark: str, tokens: int,
                 threshold: int, model: str | None, at: float):
        self.harness = harness
        self.session_id = session_id
        self.mark = mark
        self.tokens = tokens
        self.threshold = threshold
        self.model = model
        self.at = at

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__slots__}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Crossing):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __repr__(self) -> str:
        return "Crossing(" + ", ".join(f"{k}={v!r}" for k, v in self.as_dict().items()) + ")"


# --- state ------------------------------------------------------------------------------

class _Purpose:
    """One purpose of one reader: armed (`fired_at` None) or fired."""

    __slots__ = ("mark", "fired_at", "tokens", "threshold", "model")

    def __init__(self, mark: str):
        self.mark = mark
        self.fired_at: float | None = None
        self.tokens: int | None = None
        self.threshold: int | None = None
        self.model: str | None = None

    def as_record(self) -> dict:
        return {"mark": self.mark, "fired_at": self.fired_at, "tokens": self.tokens,
                "threshold": self.threshold, "model": self.model}


class _Reader:
    __slots__ = ("agent_id", "source", "purposes")

    def __init__(self, agent_id: str | None):
        self.agent_id = agent_id
        self.source = None
        self.purposes: dict[str, _Purpose] = {}

    def purpose(self, name: str, mark: str) -> _Purpose:
        held = self.purposes.get(name)
        if held is None or held.mark != mark:
            held = self.purposes[name] = _Purpose(mark)
        return held

    def fired(self) -> dict[str, dict]:
        return {name: p.as_record() for name, p in self.purposes.items()
                if p.fired_at is not None}


class _Session:
    __slots__ = ("harness", "session_id", "lock", "source", "readers", "pending", "models")

    def __init__(self, harness: str, session_id: str):
        self.harness = harness
        self.session_id = session_id
        self.lock = _thread.allocate_lock()
        self.source = None
        self.readers: dict[str | None, _Reader] = {}
        self.pending: list[str] = []
        self.models: set[str] = set()

    def reader(self, agent_id: str | None) -> _Reader:
        found = self.readers.get(agent_id)
        if found is None:
            found = self.readers[agent_id] = _Reader(agent_id)
        return found


_sessions: dict[tuple[str, str], _Session] = {}
_sessions_lock = _thread.allocate_lock()


def _session(harness: str, session_id: str) -> _Session:
    key = (harness, session_id)
    with _sessions_lock:
        found = _sessions.get(key)
        if found is not None:
            return found
        found = _Session(harness, session_id)
        _load(found)                     # before any other thread can see the session
        _sessions[key] = found
    return found


def reset() -> None:
    """Forget every session this process holds (tests, and `gone`)."""
    with _sessions_lock:
        _sessions.clear()
    _thresholds.clear()
    _thresholds_stamp[:] = [None]
    _said.clear()
    _handlers_cache[:] = [None]
    _default_cache[:] = [None, None]


# --- the file of fired purposes ---------------------------------------------------------

def safe_id(session_id: object) -> bool:
    """True when `session_id` can name a file: letters, digits, `.`, `_`, `-`, not
    starting with a dot."""
    return (isinstance(session_id, str) and 0 < len(session_id) <= 200
            and session_id[0] != "." and all(c.isalnum() or c in "._-" for c in session_id))


def state_dir(env=None):
    """`STATE/usage/marks`."""
    from moxi.paths import Layout

    return Layout("usage").state_dir(env) / STATE_DIR


def state_file(harness: str, session_id: str, env=None):
    """The file of one session's fired purposes, or None when the id can't name a file."""
    if harness not in ("claude", "codex", "opencode") or not safe_id(session_id):
        return None
    return state_dir(env) / f"{harness}-{session_id}.json"


def read_record(path) -> dict | None:
    """A fired-purposes file as a dict, or None when it is missing or not one."""
    import json

    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return None
    return doc


def _load(session: _Session) -> None:
    """Take the fired purposes an earlier endpoint of this session left on disk."""
    try:
        path = state_file(session.harness, session.session_id)
        doc = read_record(path) if path is not None else None
        if doc is None:
            return
        readers = doc.get("readers")
        if not isinstance(readers, dict):
            return
        with session.lock:
            for key, purposes in readers.items():
                if not isinstance(purposes, dict):
                    continue
                reader = session.reader(None if key == MAIN_READER else key)
                for name, rec in purposes.items():
                    if name not in PURPOSES or not isinstance(rec, dict):
                        continue
                    if rec.get("mark") not in MARKS or not _number(rec.get("fired_at")):
                        continue
                    held = reader.purpose(name, rec["mark"])
                    held.fired_at = float(rec["fired_at"])
                    held.tokens, held.threshold = rec.get("tokens"), rec.get("threshold")
                    held.model = rec.get("model")
    except Exception:  # noqa: BLE001 - fail open: the file is a guard, not the state
        _fail("load", "the fired marks on disk could not be read; this session starts "
                      "with none")


def _persist(session: _Session, reader: _Reader, marks: dict, now: float) -> None:
    """Write `reader`'s fired purposes into the session's file."""
    path = state_file(session.harness, session.session_id)
    if path is None:
        return
    import json

    from moxi.files import update

    key = MAIN_READER if reader.agent_id is None else reader.agent_id
    with session.lock:
        fired = reader.fired()

    def change(old: bytes | None) -> bytes:
        doc = None
        if old is not None:
            try:
                doc = json.loads(old)
            except ValueError:
                doc = None
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            doc = {}
        readers = doc.get("readers") if isinstance(doc.get("readers"), dict) else {}
        if fired:
            readers[key] = fired
        else:
            readers.pop(key, None)
        doc.update({"schema": SCHEMA, "harness": session.harness,
                    "session_id": session.session_id, "marks": marks,
                    "updated_at": now, "readers": readers})
        return (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8")

    update(path, change)


# --- thresholds -------------------------------------------------------------------------

_thresholds: dict[tuple, object] = {}
_thresholds_stamp: list = [None]
_thresholds_lock = _thread.allocate_lock()


def _fresh_thresholds() -> None:
    """Drop the kept thresholds when the measure, the table or the config changed."""
    from . import thresholds

    now = thresholds.stamp()
    with _thresholds_lock:
        if _thresholds_stamp[0] != now:
            _thresholds.clear()
            _thresholds_stamp[0] = now


def _threshold(model: str | None, mark: str, population: str) -> int | None:
    """The mark's threshold in tokens for `model`, or None when it is unavailable."""
    key = (model, mark, population)
    with _thresholds_lock:
        if key in _thresholds:
            return _thresholds[key]
    from . import thresholds

    found = thresholds.threshold(model, mark, population).tokens
    with _thresholds_lock:
        _thresholds[key] = found
    return found


def _lowest(mark: str, models: set[str]) -> int | None:
    """The smallest main threshold of `mark` over `models`, or over every model the
    measure has a row for when `models` is empty; None when none is known."""
    from . import thresholds

    if not models:
        key = ("*", mark, thresholds.MAIN)
        with _thresholds_lock:
            if key in _thresholds:
                return _thresholds[key]
        try:
            doc = thresholds.params()
        except thresholds.ParamsError:
            doc = None
        rows = doc.get("models") if isinstance(doc, dict) else None
        found = [_threshold(m, mark, thresholds.MAIN)
                 for m in (rows if isinstance(rows, dict) else ())]
        known = [t for t in found if t is not None]
        value = min(known) if known else None
        with _thresholds_lock:
            _thresholds[key] = value
        return value
    known = [t for t in (_threshold(m, mark, thresholds.MAIN) for m in models)
             if t is not None]
    return min(known) if known else None


# --- reading a reader's context ---------------------------------------------------------

def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _main_source(session: _Session, event):
    """The main session's source: built from the event's `record_path`, else found by
    session id. Held once found."""
    if session.source is not None:
        return session.source
    from moxi_sessions.api import Source

    path = _text(getattr(event, "record_path", None))
    if path is not None and getattr(event, "agent_id", None) is None:
        session.source = Source(session.harness, path, session.session_id)
        return session.source
    from moxi_sessions import api as sessions

    from .context import _source

    found, _held_by, _why = _source(sessions, session.session_id, session.harness)
    if found is not None:
        session.source = found
    return session.source


def _agent_source(session: _Session, reader: _Reader, event):
    """The agent's own source, held once found: on Codex the event's `record_path` is
    the agent's own rollout; elsewhere the session's `subagents()` entry for it."""
    if reader.source is not None:
        return reader.source
    from moxi_sessions.api import Source

    agent_id = reader.agent_id
    path = _text(getattr(event, "record_path", None))
    if session.harness == "codex" and path is not None:
        reader.source = Source("codex", path, agent_id, agent_id)
        return reader.source
    main = _main_source(session, event)
    if main is None and path is not None:
        main = Source(session.harness, path, session.session_id)
    if main is None:
        return None
    from moxi_sessions import api as sessions

    from .context import _agent

    entry, _why = _agent(sessions, main, agent_id)
    if entry is not None:
        reader.source = entry.source         # the tuple: no size bound
    return reader.source


def _reading(source) -> tuple[int, str | None, float | None] | None:
    """(tokens, model, time) of the source's newest request, or None."""
    from moxi_sessions import api as sessions

    request = sessions.last_request(source).request
    if request is None:
        return None
    tokens = request.input_tokens
    if isinstance(tokens, bool) or not isinstance(tokens, int):
        return None
    model = _text(request.model) or _text(request.response_model)
    at = request.time if _number(request.time) else None
    return tokens, model, at


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# --- the listener -----------------------------------------------------------------------

def notice_line(tokens: int, mark: str, threshold: int, model: str | None) -> str:
    """`context 412,330 passed the 1x restart point (398,112) for claude-opus-5-5`."""
    return (f"context {tokens:,} passed the {mark} restart point ({threshold:,}) for "
            f"{model or 'an unnamed model'}")


def agent_label(agent_id: str) -> str:
    short = agent_id[:AGENT_SHOWN] + "..." if len(agent_id) > AGENT_SHOWN + 1 else agent_id
    return f"agent-{short}"


def on_tool(event) -> str | None:
    """turns' after-tool listener: the notice when one fired, the crossing handlers'
    lines, and for the main session its agents' waiting lines; else None."""
    try:
        notice, crossing = selected(_env(event))
        if notice is None and crossing is None:
            return None
        if getattr(event, "agent_id", None) is not None and notice is None:
            return None                          # an agent has no crossing
        return observe(event, notice=notice, crossing=crossing)
    except Exception:  # noqa: BLE001 - the listener fails open
        _fail("listener", "the warn-at mark failed on a tool call; it adds nothing")
        return None


def observe(event, *, notice: str | None, crossing: str | None,
            now: float | None = None) -> str | None:
    """One after-tool event with the marks in force (`on_tool` without the selection
    and the fail-open wrap). `now` stands in for the clock."""
    harness = _text(getattr(event, "harness", None))
    session_id = _text(getattr(event, "session_id", None))
    if harness is None or session_id is None:
        return None
    if now is None:
        import time

        now = time.time()
    agent_id = _text(getattr(event, "agent_id", None))
    session = _session(harness, session_id)
    with session.lock:
        reader = session.reader(agent_id)
    try:
        source = (_main_source(session, event) if agent_id is None
                  else _agent_source(session, reader, event))
        reading = _reading(source) if source is not None else None
    except Exception:  # noqa: BLE001 - no reading; waiting lines still go out
        _fail("read", "a session's records could not be read for the warn-at mark")
        reading = None
    wanted = {NOTICE: notice} if agent_id is not None else {NOTICE: notice,
                                                           CROSSING: crossing}
    wanted = {name: mark for name, mark in wanted.items() if mark is not None}
    own: str | None = None
    crossed: Crossing | None = None
    changed = False
    if reading is not None:
        tokens, model, at = reading
        when = now if at is None else at
        from .thresholds import MAIN, SUBAGENT, base_model

        population = MAIN if agent_id is None else SUBAGENT
        _fresh_thresholds()
        limits = {name: _threshold(model, mark, population) for name, mark in wanted.items()}
        shown = base_model(model) or model
        with session.lock:
            if agent_id is None and shown:
                session.models.add(model)
            for name, mark in wanted.items():
                held = reader.purpose(name, mark)
                limit = limits[name]
                if limit is None:
                    continue
                if tokens >= limit:
                    if held.fired_at is None:
                        held.fired_at, held.tokens, held.threshold = when, tokens, limit
                        held.model = shown
                        changed = True
                        if name == NOTICE:
                            own = notice_line(tokens, mark, limit, shown)
                        else:
                            crossed = Crossing(harness, session_id, mark, tokens, limit,
                                               shown, when)
                elif held.fired_at is not None:
                    held.fired_at = held.tokens = held.threshold = held.model = None
                    changed = True
            if own is not None and agent_id is not None:
                session.pending.append(f"subagent {agent_label(agent_id)} {own}")
    if changed:
        try:
            _persist(session, reader, {NOTICE: notice or OFF, CROSSING: crossing or OFF},
                     now)
        except Exception:  # noqa: BLE001 - the mark still counts in memory
            _fail("persist", "the fired marks could not be written; a restarted endpoint "
                             "may say a mark again")
    lines = [own] if own else []
    if crossed is not None:
        lines.extend(run_handlers(crossed))
    if agent_id is None:
        with session.lock:
            lines.extend(session.pending)
            session.pending.clear()
    return "\n".join(lines) if lines else None


def floor(event) -> int:
    """turns' floor for the after-tool row: the context below which the main loop's
    calls have nothing for the listener to do. Never raises: a fault reads 0."""
    try:
        if getattr(event, "agent_id", None) is not None:
            return 0
        notice, crossing = selected(_env(event))
        if notice is None and crossing is None:
            return NEVER
        harness = _text(getattr(event, "harness", None))
        session_id = _text(getattr(event, "session_id", None))
        if harness is None or session_id is None:
            return 0
        session = _session(harness, session_id)
        wanted = [(name, mark) for name, mark in ((NOTICE, notice), (CROSSING, crossing))
                  if mark is not None]
        with session.lock:
            if session.pending:
                return 0
            main = session.readers.get(None)
            if main is not None and any(
                    (p := main.purposes.get(name)) is not None and p.mark == mark
                    and p.fired_at is not None for name, mark in wanted):
                return 0
            models = set(session.models)
        _fresh_thresholds()
        known = [t for t in (_lowest(mark, models) for _name, mark in wanted)
                 if t is not None]
        return min(known) if known else 0
    except Exception:  # noqa: BLE001 - a floor's fault is "always call"
        _fail("floor", "the warn-at mark's floor could not be worked out; it reads 0")
        return 0


def gone(event) -> None:
    """The endpoint is ending: delete the fired-purposes files of every session it held,
    and forget them."""
    try:
        held = set()
        with _sessions_lock:
            held.update(_sessions)
            _sessions.clear()
        harness = _text(getattr(event, "harness", None))
        session_id = _text(getattr(event, "session_id", None))
        if harness is not None and session_id is not None:
            held.add((harness, session_id))
        for h, sid in held:
            path = state_file(h, sid)
            if path is None:
                continue
            for target in (path, path.with_name(path.name + ".lock")):
                try:
                    os.remove(target)
                except FileNotFoundError:
                    pass
    except Exception:  # noqa: BLE001 - fail open
        _fail("gone", "the fired marks could not be deleted; the doctor sweeps them")
    return None


# --- crossing handlers ------------------------------------------------------------------

_handlers_cache: list = [None]


def handlers() -> list:
    """The `moxi.usage.crossings` entries, read once per process, in name order."""
    if _handlers_cache[0] is None:
        import moxi.modules

        _handlers_cache[0] = list(moxi.modules.extensions(GROUP))
    return _handlers_cache[0]


def handler_wait() -> float:
    """`marks.handler_wait_ms` in seconds; the default when the config can't be read."""
    try:
        from .settings import current

        return max(0, int(current()["marks.handler_wait_ms"])) / 1000.0
    except Exception:  # noqa: BLE001 - fail open to the default
        _fail("wait", "usage's config could not be read; crossing handlers get 200 ms")
        return 0.2


def run_handlers(crossing: Crossing, *, wait: float | None = None) -> list[str]:
    """Call each crossing handler with `crossing`, one after another, each on a thread of
    its own waited on for at most `wait` seconds (`marks.handler_wait_ms`). Returns the
    lines they gave, in order. A handler that raises, overruns or answers something other
    than text costs only its own line, and is logged."""
    import threading

    try:
        entries = handlers()
    except Exception:  # noqa: BLE001 - no handlers is no lines
        _fail("handlers", "the crossing handlers could not be listed")
        return []
    if not entries:
        return []
    bound = handler_wait() if wait is None else wait
    lines: list[str] = []
    for entry in entries:
        name = getattr(entry, "name", "?")
        box: list = []

        def call(entry=entry, box=box) -> None:
            try:
                box.append((True, entry.load()(crossing)))
            except BaseException as exc:  # noqa: BLE001 - a handler's fault is its own
                box.append((False, exc))

        worker = threading.Thread(target=call, daemon=True,
                                  name=f"usage-crossing-{name}")
        worker.start()
        worker.join(bound)
        if not box:
            _warn(f"crossing handler {name} did not answer within {int(bound * 1000)} ms; "
                  "usage answers without its line")
            continue
        ok, value = box[0]
        if not ok:
            _warn(f"crossing handler {name} raised {type(value).__name__}: {value}; "
                  "its line is left out")
        elif value is None:
            continue
        elif isinstance(value, str):
            if value.strip():
                lines.append(value.strip())
        else:
            _warn(f"crossing handler {name} answered a {type(value).__name__}, not text; "
                  "its line is left out")
    return lines


# --- logging ----------------------------------------------------------------------------

_said: set[str] = set()


def _fail(kind: str, message: str) -> None:
    """Log `message` with the exception being handled, once per kind per process."""
    if kind in _said:
        return
    _said.add(kind)
    import logging

    logging.getLogger(LOGGER).warning(message, exc_info=True)


def _warn(message: str) -> None:
    import logging

    logging.getLogger(LOGGER).warning(message)


__all__ = ["WARN_AT", "CROSSING_AT", "MARKS", "OFF", "NOTICE", "CROSSING", "PURPOSES",
           "GROUP", "STATE_DIR", "SCHEMA", "NEVER", "MAIN_READER", "Crossing", "selected",
           "config_default", "on_tool", "observe", "floor", "gone", "handlers",
           "handler_wait", "run_handlers", "notice_line", "agent_label", "safe_id",
           "state_dir", "state_file", "read_record", "reset"]
