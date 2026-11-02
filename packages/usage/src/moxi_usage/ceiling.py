"""The worker ceiling: turns' guard that stops a subagent whose context has grown too far.

turns calls `judge(call)` before each tool call of a subagent, in the session's endpoint,
when the guard `usage.ceiling` is on: by usage's switch `ceiling.on` or by turns'
`guards.on`. It answers None (let the call run), `Allow(line)` (run it and add one line
for the model) or `Deny(reason)`, turns' own answers.

**The rule.** The ceiling acts only when it is on and both `ceiling.warn_tokens` and
`ceiling.hard_tokens` are above 0 (both default 0). On with either at 0, every call is
allowed, and the doctor's `marks` row warns.

**Growth** is the agent's current context minus its baseline: the current from sessions'
`last_request` on the agent's own source, the baseline from the agent's first
non-synthetic request, read forward once and held in memory per (session, agent).
Context is a request's input tokens, cache reads and writes included. The agent's source
is built from the call: on Codex the call's `record_path` is the agent's own rollout;
elsewhere it is the session's `subagents()` entry for the agent, found from the session's
own file.

**Bands.** The return tools (`ceiling.return_tools`) are allowed before any measurement.
At or above `warn_tokens` of growth, the call that creates the agent's warn marker gets
one added line, `ceiling.warning_text`. At or above `hard_tokens`, a hard marker latches:
from then on every tool is refused with `ceiling.deny_text`, except the return tools and
what the post-ceiling allowlist (`moxi_usage.allowlist`) accepts. The latch is on disk,
so it holds after a compaction shrinks the context and after the endpoint starts again.
Both texts may name `{growth}`, `{current}` and `{hard}`.

**Markers** are files under `STATE/usage/ceiling/<session_id>/<agent_id>/`, `warn-<n>`
and `hard-<n>` for the thresholds `n` in force, made private with `moxi.files`. Making
an agent's directory first sweeps agent directories older than `ceiling.max_age_days`.

**Fail open.** Every error is logged once per kind and the call is allowed.

Importing this module imports nothing beyond `_thread` and `os`; the core, sessions and
turns load at the first call that needs them.
"""

from __future__ import annotations

import _thread
import os

#: The guard's id, its name in usage's `GUARDS`, and its switch in usage's settings.
GUARD_ID = "usage.ceiling"
NAME = "ceiling"
SWITCH = "ceiling.on"
#: The markers' directory under usage's state directory.
STATE_DIR = "ceiling"
#: The marker kinds.
WARN, HARD = "warn", "hard"
#: The logger a problem goes to.
LOGGER = "moxi.usage"
_DAY = 86400.0


# --- the settings -----------------------------------------------------------------------

class Rules:
    """The ceiling's settings, read from usage's config: `warn` and `hard` in tokens of
    growth, the return tools, the two texts, the allowlist's roots and command prefixes,
    and the markers' age in days. `active` is true when both thresholds are above 0."""

    __slots__ = ("on", "warn", "hard", "return_tools", "warning_text", "deny_text",
                 "handoff_roots", "work_roots", "allow_commands", "max_age_days")

    def __init__(self, *, on: bool = False, warn: int = 0, hard: int = 0,
                 return_tools: tuple[str, ...] = (), warning_text: str = "",
                 deny_text: str = "", handoff_roots: tuple[str, ...] = (),
                 work_roots: tuple[str, ...] = (),
                 allow_commands: tuple[tuple[str, ...], ...] = (),
                 max_age_days: float = 7.0):
        self.on = on
        self.warn = warn
        self.hard = hard
        self.return_tools = return_tools
        self.warning_text = warning_text
        self.deny_text = deny_text
        self.handoff_roots = handoff_roots
        self.work_roots = work_roots
        self.allow_commands = allow_commands
        self.max_age_days = max_age_days

    @property
    def active(self) -> bool:
        return self.warn > 0 and self.hard > 0


def rules_of(settings, *, file=None) -> Rules:
    """`settings`' `ceiling.*` keys as `Rules`. Raises `moxi.config.ConfigError` for a
    root that is not an absolute path (after `~`) or a command prefix that is not a
    non-empty list of words; `CONFIG_CHECK` runs it, so `moxi config set` refuses them
    too."""
    from moxi.config import ConfigError

    where = file if file is not None else getattr(settings, "file", None)

    def roots(key: str) -> tuple[str, ...]:
        out = []
        for n, value in enumerate(settings[key], start=1):
            path = os.path.expanduser(value.strip()) if isinstance(value, str) else ""
            if not os.path.isabs(path):
                raise ConfigError(where, key, f"entry {n}, {value!r}, is not an absolute "
                                  "path", "give each directory from / or ~/")
            out.append(os.path.normpath(path))
        return tuple(out)

    commands = []
    for n, value in enumerate(settings["ceiling.allow_commands"], start=1):
        if (not isinstance(value, list) or not value
                or not all(isinstance(w, str) and w and not w.isspace() for w in value)):
            raise ConfigError(where, "ceiling.allow_commands", f"entry {n}, {value!r}, is "
                              "not a list of words",
                              'write each as the words a command starts with, such as '
                              '["make", "report"]')
        commands.append(tuple(value))
    return Rules(on=bool(settings[SWITCH]), warn=int(settings["ceiling.warn_tokens"]),
                 hard=int(settings["ceiling.hard_tokens"]),
                 return_tools=tuple(settings["ceiling.return_tools"]),
                 warning_text=settings["ceiling.warning_text"],
                 deny_text=settings["ceiling.deny_text"],
                 handoff_roots=roots("ceiling.handoff_roots"),
                 work_roots=roots("ceiling.work_roots"), allow_commands=tuple(commands),
                 max_age_days=float(settings["ceiling.max_age_days"]))


_rules_cache: list = [None, None]


def rules(env=None) -> Rules | None:
    """The ceiling's settings now, read again only when usage's config file changes;
    None when the config can't be read (logged once)."""
    from moxi.paths import Layout

    path = Layout("usage").config_file(env)
    key = (str(path), _stat(path))
    if _rules_cache[0] != key:
        try:
            from .settings import current

            settings = current(env)
            found = rules_of(settings)
        except Exception:  # noqa: BLE001 - fail open: a config usage can't read is off
            _fail("config", "usage's config could not be read; the worker ceiling allows "
                            "every call")
            found = None
        _rules_cache[:] = [key, found]
    return _rules_cache[1]


def _stat(path) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns, st.st_ino)


# --- per agent, in memory ---------------------------------------------------------------

class _Agent:
    """One agent as the endpoint holds it: its source, its baseline, and the hard
    threshold it latched at with the growth it latched with."""

    __slots__ = ("source", "baseline", "latched", "warned")

    def __init__(self):
        self.source = None
        self.baseline: int | None = None
        self.latched: tuple[int, int] | None = None     # (hard, growth)
        self.warned: int | None = None                  # the warn threshold, once said


_agents: dict[tuple[str, str, str], _Agent] = {}
_lock = _thread.allocate_lock()


def _agent(harness: str, session_id: str, agent_id: str) -> _Agent:
    key = (harness, session_id, agent_id)
    with _lock:
        found = _agents.get(key)
        if found is None:
            found = _agents[key] = _Agent()
        return found


def reset() -> None:
    """Forget every agent and the cached settings (tests; an endpoint never needs it)."""
    with _lock:
        _agents.clear()
    _rules_cache[:] = [None, None]
    _said.clear()


# --- the judge --------------------------------------------------------------------------

def judge(call):
    """turns' judge for `usage.ceiling`: None, `Allow(line)` or `Deny(reason)`."""
    try:
        return decide(call)
    except Exception:  # noqa: BLE001 - fail open
        _fail("judge", "the worker ceiling could not judge a call; it was allowed")
        return None


def decide(call, *, now: float | None = None, env=None):
    """The ceiling's answer for one call, as `judge` gives it but letting errors out.
    `now` is the time the markers carry and the sweep reads, the clock when None."""
    agent_id = _text(getattr(call, "agent_id", None))
    session_id = _text(getattr(call, "session_id", None))
    harness = _text(getattr(call, "harness", None))
    if agent_id is None or session_id is None or harness is None:
        return None
    r = rules(env)
    if r is None or not r.active:
        return None
    if getattr(call, "tool", None) in r.return_tools:
        return None
    if now is None:
        import time

        now = time.time()
    agent = _agent(harness, session_id, agent_id)
    where = marker_dir(session_id, agent_id, env)
    if agent.latched is None or agent.latched[0] != r.hard:
        growth = read_hard(where, r.hard) if where is not None else None
        agent.latched = (r.hard, growth) if growth is not None else None
    if agent.latched is not None:
        return _wrap_up(call, r, agent.latched[1], None)
    reading = _measure(agent, call, harness, session_id, agent_id)
    if reading is None:
        return None
    current, baseline = reading
    growth = current - baseline
    body = {"growth": growth, "current": current, "baseline": baseline, "at": now}
    if growth >= r.hard:
        if where is not None:
            _mark(where, HARD, r.hard, body, r, now=now, env=env)
        agent.latched = (r.hard, growth)
        return _wrap_up(call, r, growth, current)
    if growth >= r.warn:
        if where is not None:
            first = _mark(where, WARN, r.warn, body, r, now=now, env=env)
        else:
            first = agent.warned != r.warn
        agent.warned = r.warn
        if first:
            return _allow(fill(r.warning_text, growth=growth, current=current, hard=r.hard))
    return None


def _wrap_up(call, r: Rules, growth: int, current: int | None):
    """Past the hard mark: the allowlist's yes lets the call run; else the deny text."""
    from .allowlist import allowed

    env = getattr(call, "env", None)
    home = env.get("HOME") if isinstance(env, dict) else None
    ok, why = allowed(getattr(call, "tool", None), getattr(call, "input", None),
                      getattr(call, "cwd", None), handoff_roots=r.handoff_roots,
                      work_roots=r.work_roots, commands=r.allow_commands, home=home)
    if ok:
        return None
    text = fill(r.deny_text, growth=growth, current=current, hard=r.hard)
    return _deny(f"{text} ({why})" if why else text)


def fill(text: str, *, growth: int, current: int | None, hard: int) -> str:
    """`text` with `{growth}`, `{current}` and `{hard}` filled in, as counts with
    thousands commas; text whose braces don't read as those is given as written."""
    values = {"growth": f"{growth:,}", "hard": f"{hard:,}",
              "current": f"{current:,}" if current is not None else "unknown"}
    try:
        return text.format_map(values)
    except (KeyError, ValueError, IndexError, AttributeError):
        return text


def _turns():
    from moxi import modules

    found = modules.load("turns")
    if not found.ok or found.api is None:
        raise LookupError(f"turns is {found.state}: {found.reason}")
    return found.api


def _allow(line: str):
    return _turns().Allow(line)


def _deny(reason: str):
    return _turns().Deny(reason)


# --- reading the agent's context --------------------------------------------------------

def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _measure(agent: _Agent, call, harness: str, session_id: str,
             agent_id: str) -> tuple[int, int] | None:
    """(current, baseline) for the agent, or None when either can't be read yet."""
    from moxi_sessions import api as sessions

    source = agent.source
    if source is None:
        source = agent.source = _agent_source(sessions, call, harness, session_id,
                                              agent_id)
        if source is None:
            return None
    request = sessions.last_request(source).request
    current = getattr(request, "input_tokens", None)
    if request is None or not _count(current):
        return None
    if agent.baseline is None:
        agent.baseline = _first(sessions, source)
        if agent.baseline is None:
            return None
    return current, agent.baseline


def _agent_source(sessions, call, harness: str, session_id: str, agent_id: str):
    """The agent's own source: on Codex the call's `record_path` is its own rollout;
    elsewhere the session's `subagents()` entry for it."""
    path = _text(getattr(call, "record_path", None))
    if harness == "codex" and path is not None:
        return sessions.Source("codex", path, agent_id, agent_id)
    from .context import _agent as find_agent
    from .context import _source as find_session

    if path is not None:
        main = sessions.Source(harness, path, session_id)
    else:
        main, _held_by, _why = find_session(sessions, session_id, harness)
        if main is None:
            return None
    entry, _why = find_agent(sessions, main, agent_id)
    return entry.source if entry is not None else None


def _first(sessions, source) -> int | None:
    """The input tokens of the source's first non-synthetic request, or None."""
    with sessions.records(source, kinds={"request"}) as read:
        for record in read:
            if record.synthetic is True:
                continue
            if _count(record.input_tokens):
                return record.input_tokens
    return None


# --- markers ----------------------------------------------------------------------------

def safe_id(value: object) -> bool:
    """True when `value` can name a directory: letters, digits, `.`, `_`, `-`, not
    starting with a dot."""
    return (isinstance(value, str) and 0 < len(value) <= 200 and value[0] != "."
            and all(c.isalnum() or c in "._-" for c in value))


def state_dir(env=None):
    """`STATE/usage/ceiling`."""
    from moxi.paths import Layout

    return Layout("usage").state_dir(env) / STATE_DIR


def marker_dir(session_id: str, agent_id: str, env=None):
    """The agent's marker directory, or None when an id can't name one."""
    if not (safe_id(session_id) and safe_id(agent_id)):
        return None
    return state_dir(env) / session_id / agent_id


def read_hard(where, hard: int) -> int | None:
    """The growth a valid `hard-<hard>` marker in `where` latched with, or None."""
    import json

    try:
        with open(os.path.join(where, f"{HARD}-{hard}"), "rb") as fh:
            doc = json.loads(fh.read(4096))
    except (OSError, ValueError):
        return None
    growth = doc.get("growth") if isinstance(doc, dict) else None
    return growth if _count(growth) and growth >= hard else None


def _mark(where, kind: str, threshold: int, body: dict, r: Rules, *, now: float,
          env=None) -> bool:
    """Make the marker `<kind>-<threshold>` in `where` unless it is there; True only for
    the call that made it. Making the agent's directory sweeps old ones first."""
    import json

    from moxi import files

    if not os.path.isdir(where):
        try:
            sweep(now=now, max_age_days=r.max_age_days, env=env)
        except Exception:  # noqa: BLE001 - a failed sweep never stops a marker
            _fail("sweep", "the worker ceiling's sweep of old markers failed")
        files.private_dir(where)
    made = []

    def create(old: bytes | None) -> bytes:
        if old is not None:
            return old
        made.append(True)
        return json.dumps({**body, "kind": kind, "threshold": threshold}).encode()

    files.update(os.path.join(where, f"{kind}-{threshold}"), create)
    return bool(made)


def _newest(path: str) -> float:
    """The newest mtime of a directory and everything under it."""
    newest = os.lstat(path).st_mtime
    for root, dirs, names in os.walk(path):
        for name in (*dirs, *names):
            try:
                newest = max(newest, os.lstat(os.path.join(root, name)).st_mtime)
            except OSError:
                continue
    return newest


def sweep(*, now: float, max_age_days: float, env=None) -> tuple[int, list[str]]:
    """Delete each agent's markers, `ceiling/<session>/<agent>/`, last changed more than
    `max_age_days` days before `now`, and session directories left empty. Never follows
    a link and never raises: returns how many agents were swept and what could not be
    deleted."""
    import shutil

    root = str(state_dir(env))
    oldest = now - max_age_days * _DAY
    problems: list[str] = []
    swept = 0
    try:
        sessions = os.listdir(root)
    except FileNotFoundError:
        return 0, []
    except OSError as exc:
        return 0, [f"{root}: {exc}"]
    for session in sessions:
        top = os.path.join(root, session)
        if os.path.islink(top) or not os.path.isdir(top):
            continue
        try:
            for agent in os.listdir(top):
                where = os.path.join(top, agent)
                if os.path.islink(where) or not os.path.isdir(where):
                    continue
                if _newest(where) < oldest:
                    shutil.rmtree(where)
                    swept += 1
            if not os.listdir(top):
                os.rmdir(top)
        except FileNotFoundError:
            continue
        except OSError as exc:
            problems.append(f"{top}: {exc}")
    return swept, problems


class Latched:
    """One agent whose hard marker has latched: its session, its id, the growth and
    threshold it latched at, and when."""

    __slots__ = ("session_id", "agent_id", "growth", "threshold", "at")

    def __init__(self, session_id: str, agent_id: str, growth: int | None,
                 threshold: int | None, at: float | None):
        self.session_id = session_id
        self.agent_id = agent_id
        self.growth = growth
        self.threshold = threshold
        self.at = at

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__slots__}


def latched(env=None) -> list[Latched]:
    """Every agent with a hard marker on disk, newest first."""
    import json

    root = str(state_dir(env))
    out: list[Latched] = []
    try:
        sessions = sorted(os.listdir(root))
    except OSError:
        return out
    for session in sessions:
        try:
            agents = sorted(os.listdir(os.path.join(root, session)))
        except OSError:
            continue
        for agent in agents:
            where = os.path.join(root, session, agent)
            try:
                names = sorted(n for n in os.listdir(where) if n.startswith(f"{HARD}-")
                               and not n.endswith(".lock"))
            except OSError:
                continue
            for name in names:
                try:
                    with open(os.path.join(where, name), "rb") as fh:
                        doc = json.loads(fh.read(4096))
                except (OSError, ValueError):
                    doc = {}
                doc = doc if isinstance(doc, dict) else {}
                growth, at = doc.get("growth"), doc.get("at")
                threshold = name.partition("-")[2]
                out.append(Latched(session, agent, growth if _count(growth) else None,
                                   int(threshold) if threshold.isdigit() else None,
                                   float(at) if isinstance(at, (int, float)) else None))
    out.sort(key=lambda item: -(item.at or 0.0))
    return out


# --- logging ----------------------------------------------------------------------------

_said: set[str] = set()


def _fail(kind: str, message: str) -> None:
    """Log `message` with the exception being handled, once per kind per process."""
    if kind in _said:
        return
    _said.add(kind)
    import logging

    logging.getLogger(LOGGER).warning(message, exc_info=True)


__all__ = ["GUARD_ID", "NAME", "SWITCH", "STATE_DIR", "WARN", "HARD", "Rules", "rules",
           "rules_of", "judge", "decide", "fill", "safe_id", "state_dir", "marker_dir",
           "read_hard", "sweep", "Latched", "latched", "reset"]
