"""The transcript-read guard: turns' guard that keeps agents out of harness transcripts.

turns calls `judge(call)` before a covered tool call, in the session's endpoint, when the
guard `sessions.transcript-read` is on: by sessions' switch `guard.transcript_read`, which
the install asks about, or by naming the guard on turns' `guards.on`. It answers None (let
the call run) or turns' `Deny(reason)`, whose reason names `moxi sessions read <session>`.

**What it refuses.** A call that names a path under a transcript root:

- Claude Code's `Read` by its `file_path`; `Grep` by its `path`, or the call's working
  directory when it names none; `Glob` by its `pattern` joined to its `path` (or the
  working directory), up to the pattern's first wildcard.
- A shell command (Claude Code's `Bash`, and Codex's shell tool, which Codex's hooks also
  name `Bash`): each simple command is judged on its own, and one is refused when one of
  its words is such a path. A word is read as a path when it starts with `/`, `~`, `$HOME`
  or `${HOME}`, or holds a `/` (then it is joined to the working directory); a word
  `--name=value` or `NAME=value` is read by its value too.

The roots are each harness's `source.root()`: the configured root when a config key names
one, and the harness's own rule over the launch's environment.

**The escape.** A simple command that runs this environment's own `moxi sessions` or
`moxi memory` (as `moxi`, or by the absolute path of this environment's `moxi`) passes
whatever paths its words name. The command after it on the same line is judged on its own,
so `moxi sessions root; cat <transcript>` is refused. sessions' own reads happen in its own
process, which is no tool call, so the guard never sees them.

**A nudge, not a boundary.** A relative path after `cd`, a variable, a path built at run
time, or a script the command runs gets past it.

**Fail open.** Every error is logged once per kind and the call is allowed; the guard's
`on_error` is `allow` as well, so one fault never refuses every read everywhere. sessions'
doctor shows the fault instead.

Importing this module imports only the standard library; the core and sessions' own
modules load at the first call that needs them.
"""

from __future__ import annotations

import _thread
import os
import posixpath
import sysconfig
from collections.abc import Iterable, Iterator, Mapping

#: The guard's name in sessions' `GUARDS`, its id, and its switch in sessions' settings.
NAME = "transcript-read"
GUARD_ID = f"sessions.{NAME}"
SWITCH = "guard.transcript_read"
#: The tools it covers, per harness.
CALLS = {"claude": ("Read", "Grep", "Glob", "Bash"), "codex": ("Bash",)}
#: The logger a problem goes to.
LOGGER = "moxi.sessions"
#: The verbs of this environment's `moxi` that pass whatever paths they name.
ESCAPES = frozenset({"sessions", "memory"})
#: Shell names whose `-c` script is read as commands of its own.
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "mksh", "ash"})
#: How deep a `-c` script inside a `-c` script is read.
DEPTH = 3

DENY = (
    "Refused before it ran (sessions transcript-read guard): {what} {path}, which is under "
    "the {harness} transcript root {root}. Agents don't read harness transcripts directly. "
    "Read a session with `moxi sessions read <session>` (add `--harness codex` or "
    "`--harness opencode` for those), which prints its turns the way the sessions page "
    "shows them; `moxi sessions find <session>` says where its records are."
)

_GLOB = "*?["
_HOME_PREFIXES = ("${HOME}", "$HOME", "~")
_said: set[str] = set()
_lock = _thread.allocate_lock()
_roots_cache: list = [None, None]


def reset() -> None:
    """Forget the logged kinds and the cached roots (tests)."""
    with _lock:
        _said.clear()
        _roots_cache[:] = [None, None]


# --- the judge --------------------------------------------------------------------------

def judge(call):
    """turns' judge for `sessions.transcript-read`: None, or turns' `Deny(reason)`."""
    try:
        reason = decide(call)
    except Exception:  # noqa: BLE001 - fail open
        _fail("judge", "the transcript-read guard could not judge a call; it was allowed")
        return None
    if reason is None:
        return None
    try:
        return _turns().Deny(reason)
    except Exception:  # noqa: BLE001 - fail open
        _fail("turns", "the transcript-read guard could not reach turns; the call was "
                       "allowed")
        return None


def decide(call, *, roots: Iterable[tuple[str, str]] | None = None) -> str | None:
    """The refusal for `call`, or None to let it run, as `judge` gives it but letting
    errors out. `roots` is `(harness, root)` pairs, `transcript_roots(env)` when None."""
    tool = getattr(call, "tool", None)
    harness = getattr(call, "harness", None)
    if not isinstance(tool, str) or tool not in CALLS.get(harness, ()):
        return None
    tool_input = getattr(call, "input", None)
    if not isinstance(tool_input, Mapping):
        return None
    env = _env(getattr(call, "env", None))
    cwd = _absolute(getattr(call, "cwd", None))
    home = _absolute(env.get("HOME"))
    if roots is None:
        roots = transcript_roots(env)
    known = _known(roots)
    if not known:
        return None
    for what, path in _named(tool, tool_input, cwd, home):
        hit = _under_root(path, known)
        if hit is not None:
            return DENY.format(what=what, path=path, harness=hit[0], root=hit[1])
    return None


def _named(tool: str, tool_input: Mapping, cwd: str | None,
           home: str | None) -> Iterator[tuple[str, str]]:
    """What the call names, as `(what it is, absolute path)` pairs."""
    if tool == "Read":
        path = _path(tool_input.get("file_path"), cwd, home)
        if path is not None:
            yield "this Read names", path
    elif tool == "Grep":
        given = tool_input.get("path")
        path = _path(given, cwd, home) if given else cwd
        if path is not None:
            yield "this Grep searches", path
    elif tool == "Glob":
        given = tool_input.get("path")
        base = _path(given, cwd, home) if given else cwd
        pattern = tool_input.get("pattern")
        if isinstance(pattern, str) and pattern:
            path = _path(pattern, base, home)
        else:
            path = base
        if path is not None:
            yield "this Glob searches", path
    elif tool == "Bash":
        for words in commands(tool_input.get("command")):
            if escaped(words, home):
                continue
            named = (_past_assignments(words) or words)[0]
            for word in words:
                for value in _values(word):
                    if not _pathlike(value):
                        continue
                    path = _path(value, cwd, home)
                    if path is not None:
                        yield f"the shell command `{named}` names", path


# --- the roots --------------------------------------------------------------------------

def transcript_roots(env: Mapping[str, str] | None = None) -> tuple[tuple[str, str], ...]:
    """Each harness's transcript roots as `(harness, root)`: the configured root when a
    config key names one, and the harness's own rule over `env` (this process's
    environment when None). Raises `moxi.config.ConfigError` when the config file cannot
    be read."""
    from .shape import HARNESSES
    from .source import root

    environ = dict(os.environ if env is None else env)
    out: list[tuple[str, str]] = list(_configured_roots())
    for harness in HARNESSES:
        out.append((harness, str(root(harness, environ))))
    return tuple(dict.fromkeys(out))


def _configured_roots() -> tuple[tuple[str, str], ...]:
    """The roots sessions' config names, read again only when its file changes."""
    from moxi.paths import Layout

    from .settings import MODULE, current
    from .source import ROOT_KEYS

    path = Layout(MODULE).config_file()
    key = (str(path), _stat(path))
    with _lock:
        if _roots_cache[0] == key:
            return _roots_cache[1]
    settings = current()
    found = tuple((harness, str(settings[k])) for harness, k in ROOT_KEYS.items()
                  if settings[k] is not None)
    with _lock:
        _roots_cache[:] = [key, found]
    return found


def _stat(path) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _known(roots: Iterable[tuple[str, str]]) -> tuple[tuple[str, str, str], ...]:
    """Each root as `(harness, as named, the form compared)`, both its lexical form and
    its real path."""
    out = []
    for harness, r in roots:
        if not isinstance(r, str) or not r.startswith("/"):
            continue
        lexical = _normpath(r)
        out.append((harness, lexical, lexical))
        real = os.path.realpath(lexical)
        if real != lexical:
            out.append((harness, lexical, real))
    return tuple(out)


def _under_root(path: str, known) -> tuple[str, str] | None:
    """`(harness, root)` of the first root `path` is or lies under, by its lexical form or
    its real path; None when none."""
    forms = {path, os.path.realpath(path)}
    for harness, named, form in known:
        prefix = form.rstrip("/") + "/"
        if any(p == form or p.startswith(prefix) for p in forms):
            return harness, named
    return None


# --- paths ------------------------------------------------------------------------------

def _normpath(p: str) -> str:
    return "/" + posixpath.normpath(p).lstrip("/")


def _absolute(p: object) -> str | None:
    if isinstance(p, str) and p.startswith("/"):
        return _normpath(p)
    return None


def _path(value: object, base: str | None, home: str | None) -> str | None:
    """`value` as an absolute path: `~`, `$HOME` and `${HOME}` taken from `home`, a
    relative one joined to `base`, cut at its first wildcard to the directory before it.
    None when it can't be placed."""
    if not isinstance(value, str) or not value:
        return None
    for prefix in _HOME_PREFIXES:
        if value == prefix or value.startswith(prefix + "/"):
            if home is None:
                return None
            value = home + value[len(prefix):]
            break
    cut = min((i for i in (value.find(c) for c in _GLOB) if i >= 0), default=-1)
    if cut >= 0:
        value = value[:cut]
        value = value[:value.rfind("/") + 1] if "/" in value else ""
    if not value.startswith("/"):
        if base is None:
            return None
        value = base + "/" + value
    return _normpath(value)


def _pathlike(word: str) -> bool:
    return (word.startswith(("/", "~", "$HOME", "${HOME}")) or "/" in word
            or word in (".", ".."))


def _values(word: str) -> Iterator[str]:
    """The word, and the value of a `--name=value` or `NAME=value` word."""
    yield word
    name, eq, value = word.partition("=")
    if eq and value and "/" not in name:
        yield value


# --- the shell --------------------------------------------------------------------------

def commands(command: object, depth: int = DEPTH) -> list[list[str]]:
    """The simple commands of a shell call's `command`, each as its words with quotes
    taken off. A list of words is one command. A shell's `-c` script adds its own
    commands, down to `depth`."""
    if isinstance(command, (list, tuple)) and all(isinstance(w, str) for w in command):
        found = [list(command)] if command else []
    elif isinstance(command, str):
        found = split(command)
    else:
        return []
    out = []
    for words in found:
        at = _script(words)
        if at is None or depth <= 0:
            out.append(words)
            continue
        out.append(words[:at] + words[at + 1:])
        out.extend(commands(words[at], depth - 1))
    return out


def _script(words: list[str]) -> int | None:
    """Where the `-c` script of a command that runs a shell is among its words, or
    None."""
    skip = len(words) - len(_past_assignments(words))
    if skip >= len(words) or posixpath.basename(words[skip]) not in SHELLS:
        return None
    for i in range(skip + 1, len(words)):
        word = words[i]
        if not word.startswith("-") or word.startswith("--"):
            return None
        if "c" in word[1:] and i + 1 < len(words):
            return i + 1
    return None


def split(text: str) -> list[list[str]]:
    """`text` split into simple commands of words, quotes taken off.

    A simple command ends at `;`, `&`, `|`, `(`, `)`, a newline, a backtick and `$(`
    outside quotes, so a substitution's commands are commands of their own. A word ends at
    a space, a tab, `<` or `>` (a redirection's file is a word of its command). Single
    quotes keep everything; double quotes keep everything but a backslash's escape; a
    backslash outside quotes keeps the next character, and before a newline joins the
    lines. A `#` that starts a word comments out the rest of its line."""
    out: list[list[str]] = []
    words: list[str] = []
    word: list[str] = []
    started = False
    i, n = 0, len(text)

    def end_word():
        nonlocal started
        if started:
            words.append("".join(word))
        word.clear()
        started = False

    def end_command():
        end_word()
        if words:
            out.append(list(words))
        words.clear()

    while i < n:
        c = text[i]
        if c == "'":
            j = text.find("'", i + 1)
            j = n if j < 0 else j
            word.append(text[i + 1:j])
            started = True
            i = j + 1
            continue
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n and text[i + 1] in '"\\$`\n':
                    if text[i + 1] != "\n":
                        word.append(text[i + 1])
                    i += 2
                    continue
                word.append(text[i])
                i += 1
            started = True
            i += 1
            continue
        if c == "\\":
            if i + 1 < n and text[i + 1] != "\n":
                word.append(text[i + 1])
                started = True
            i += 2
            continue
        if c == "#" and not started:
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "$" and text[i + 1:i + 2] == "(":
            end_command()
            i += 2
            continue
        if c in ";&|()\n`":
            end_command()
            i += 1
            continue
        if c in " \t\r<>":
            end_word()
            i += 1
            continue
        word.append(c)
        started = True
        i += 1
    end_command()
    return out


def _past_assignments(words: list[str]) -> list[str]:
    """The words from the command word on, past leading `NAME=value` assignments."""
    i = 0
    while i < len(words):
        name, eq, _ = words[i].partition("=")
        if not (eq and name and (name[0].isalpha() or name[0] == "_")
                and all(ch.isalnum() or ch == "_" for ch in name)):
            break
        i += 1
    return words[i:]


def escaped(words: list[str], home: str | None = None) -> bool:
    """Whether the simple command runs this environment's own `moxi sessions` or `moxi
    memory`: `moxi`, or the absolute path (or `~/` path, with `home`) of this
    environment's `moxi`, then one of those verbs."""
    words = _past_assignments(words)
    if len(words) < 2 or words[1] not in ESCAPES:
        return False
    first = words[0]
    if first == "moxi":
        return True
    if first.startswith("~/") and home is not None:
        first = home + first[1:]
    if not first.startswith("/"):
        return False
    own = own_moxi()
    if own is None:
        return False
    return (_normpath(first) == own
            or os.path.realpath(first) == os.path.realpath(own))


def own_moxi() -> str | None:
    """This environment's `moxi` script, as an absolute path, or None when it has no
    scripts directory."""
    scripts = sysconfig.get_path("scripts")
    if not scripts:
        return None
    return _normpath(os.path.join(os.path.abspath(scripts), "moxi"))


# --- the rest ---------------------------------------------------------------------------

def _env(value) -> Mapping[str, str]:
    if isinstance(value, Mapping) and value:
        return value
    return os.environ


def _turns():
    from moxi import modules

    found = modules.load("turns")
    if not found.ok or found.api is None:
        raise LookupError(f"turns is {found.state}: {found.reason}")
    return found.api


def _fail(kind: str, message: str) -> None:
    import logging

    log = logging.getLogger(LOGGER)
    with _lock:
        first = kind not in _said
        _said.add(kind)
    if first:
        log.warning(message, exc_info=True)


__all__ = ["NAME", "GUARD_ID", "SWITCH", "CALLS", "ESCAPES", "DENY", "judge", "decide",
           "transcript_roots", "commands", "split", "escaped", "own_moxi", "reset"]
