"""The transcript-read guard: turns' guard that keeps agents out of harness transcripts.

turns calls `judge(call)` before a covered tool call, in the session's endpoint, when the
guard `sessions.transcript-read` is on: by sessions' switch `guard.transcript_read`, which
the install asks about, or by naming the guard on turns' `guards.run`. It answers None (let
the call run) or turns' `Deny(reason)`, whose reason names `moxi sessions read <session>`.

**What it refuses.** A call that names a path under a transcript root:

- Claude Code's `Read` by its `file_path`; `Grep` by its `path`, or the call's working
  directory when it names none; `Glob` by its `pattern` joined to its `path` (or the
  working directory), up to the pattern's first wildcard.
- A shell command (Claude Code's `Bash`, and Codex's shell tool, which Codex's hooks also
  name `Bash`): each simple command, as turns' shell reader (`read_shell`) reads it, is
  judged on its own, and so are the commands of each substitution in it, a `$(...)` in
  double quotes included; one is refused when one of its words, or the file one of its
  redirects names, is such a path. A word is read as a path when it starts with `/`, `~`, `$HOME`
  or `${HOME}`, or holds a `/` (then it is joined to the working directory); a word
  `--name=value` or `NAME=value` is read by its value too, and each word bash splits a
  word into where a `${...}` default or alternate outside double quotes is used (turns'
  `fields`: `cat ${x:- <path> }` names `<path>`) is judged as a word. Each script a command hands a
  shell, as turns' `shell_scripts` finds it (a `-c`, `-Command` or `/c` argument, or a
  heredoc, here-string or literal text piped into a shell), is read the same way in that
  shell's dialect, down to `DEPTH` scripts deep, and the words it was made from are not
  judged as words; past `DEPTH` they are. Codex's list of words is written back into
  shell text (`shlex.join`) and read as `sh`.

The roots are each harness's `source.root()`: the configured root when a config key names
one, and the harness's own rule over the launch's environment. Each archive folder
sessions' config names (`claude.archives`, `codex.archives`) is a transcript root too. An
archive folder is compared by its path as written (normalized by its text alone, as
`archives.archive_dirs` gives it), never resolved, and a path under it is
refused before anything resolves that path, so judging a call touches no archive folder.
A path that reaches an archive folder through a symbolic link gets past the guard.

**The escape.** A simple command that runs one of this environment's own `moxi` verbs
named on `guard.transcript_read_escapes` (`moxi sessions` by default; as `moxi`, or by the
absolute path of this environment's `moxi`) passes whatever paths its words name. The
command after it on the same line is judged on its own, so `moxi sessions root; cat
<transcript>` is refused. sessions' own reads happen in its own
process, which is no tool call, so the guard never sees them.

**A nudge, not a boundary.** A relative path after `cd`, a variable, a path built at run
time, a heredoc's body fed to anything but a shell, or a script file gets past it. Of a word that holds a
substitution, the text before its first one is judged, and so is the text after each one,
up to the next, when that starts with `/`, `~`, `$HOME` or `${HOME}`.

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
import shlex
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
#: The key naming the verbs of this environment's `moxi` that pass whatever paths they
#: name, and its shipped default (settings.toml holds the same list).
ESCAPES_KEY = "guard.transcript_read_escapes"
ESCAPES = frozenset({"sessions"})
#: How many scripts deep a script handed to a shell inside another is read.
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
#: How the text after one of a word's substitutions starts when it is judged as a path.
_TAIL_STARTS = ("/", *_HOME_PREFIXES)
_said: set[str] = set()
_lock = _thread.allocate_lock()
_config_cache: list = [None, None]


def reset() -> None:
    """Forget the logged kinds and the cached config (tests)."""
    with _lock:
        _said.clear()
        _config_cache[:] = [None, None]


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


def decide(call, *, roots: Iterable[tuple[str, str]] | None = None,
           archives: Iterable[tuple[str, str]] | None = None) -> str | None:
    """The refusal for `call`, or None to let it run, as `judge` gives it but letting
    errors out. `roots` is `(harness, root)` pairs, `transcript_roots(env)` when None;
    `archives` is `(harness, folder)` pairs compared as written, `archive_roots()` when
    None."""
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
    if archives is None:
        archives = archive_roots()
    known = _known(roots)
    kept = tuple((harness, _normpath(archive_dir)) for harness, archive_dir in archives
                 if isinstance(archive_dir, str) and archive_dir.startswith("/"))
    if not known and not kept:
        return None
    for what, path in _named(tool, tool_input, cwd, home):
        hit = _under_written(path, kept)
        if hit is None and known:
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

    roots_env = dict(os.environ if env is None else env)
    out: list[tuple[str, str]] = list(_configured_roots())
    for harness in HARNESSES:
        out.append((harness, str(root(harness, roots_env))))
    return tuple(dict.fromkeys(out))


def _configured_roots() -> tuple[tuple[str, str], ...]:
    """The roots sessions' config names, read again only when its file changes."""
    return _configured()[0]


def archive_roots() -> tuple[tuple[str, str], ...]:
    """Each archive folder sessions' config names, as `(harness, folder)`, read again only
    when its file changes. Raises `moxi.config.ConfigError` when the file cannot be
    read."""
    return _configured()[2]


def escapes() -> frozenset[str]:
    """The verbs on `guard.transcript_read_escapes`, read again only when sessions' config
    file changes. Raises `moxi.config.ConfigError` when the file cannot be read."""
    return _configured()[1]


def _configured() -> tuple:
    """The roots sessions' config names, its escape verbs and its archive folders, read
    again only when its file changes."""
    from moxi.paths import Layout

    from .archives import ARCHIVE_KEYS, archive_dirs
    from .settings import MODULE, current
    from .source import ROOT_KEYS

    path = Layout(MODULE).config_file()
    key = (str(path), _stat(path))
    with _lock:
        if _config_cache[0] == key:
            return _config_cache[1]
    settings = current()
    roots = tuple((harness, str(settings[k])) for harness, k in ROOT_KEYS.items()
                  if settings[k] is not None)
    kept = tuple((harness, archive_dir) for harness in ARCHIVE_KEYS
                 for archive_dir in archive_dirs(harness))
    found = (roots, frozenset(settings[ESCAPES_KEY]), kept)
    with _lock:
        _config_cache[:] = [key, found]
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


def _under_written(path: str, kept) -> tuple[str, str] | None:
    """`(harness, folder)` of the first archive folder `path` is or lies under, both as
    written; nothing is resolved."""
    for harness, archive_dir in kept:
        if path == archive_dir or path.startswith(archive_dir.rstrip("/") + "/"):
            return harness, archive_dir
    return None


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
    A word's substitution, which turns' shell reader gives as a NUL, cuts it there: the
    text before it is the path's start. None when it can't be placed."""
    if not isinstance(value, str) or not value:
        return None
    value = value.partition("\0")[0]
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
    """The word, and the value of a `--name=value` or `NAME=value` word; for each that
    holds a substitution (a NUL), also the text after each one, up to the next, when that
    starts like an absolute path (`$(x)/<path>`, `$(x)$HOME/<path>`,
    `$(x)/<path>$(y)`)."""
    found = [word]
    name, eq, value = word.partition("=")
    if eq and value and "/" not in name:
        found.append(value)
    for each in found:
        yield each
        for piece in each.split("\0")[1:]:
            if piece.startswith(_TAIL_STARTS):
                yield piece


# --- the shell --------------------------------------------------------------------------

def commands(text: object, dialect: str = "sh", depth: int = DEPTH) -> list[list[str]]:
    """The simple commands of a shell call's `text`, read in `dialect` by turns' shell
    reader, each as its words with quotes taken off, then the word each of its
    redirects names (so `wc -l < f` names `f`), then the words bash splits those into
    where a `${...}` default is used (each word's `fields`).

    The commands of each substitution in a command, a word's, a redirect's or an unquoted
    heredoc's body's, follow it, read at the same `depth`: a substitution runs in the same
    shell. So do the commands of each script the command hands a shell (turns'
    `shell_scripts`), read in that shell's dialect at `depth - 1` while `depth` is above
    0; the words such a script was made from are then left out of the command's own. At
    `depth` 0 a script is not read, and its words stay. A substitution stands in its word
    as a NUL. A list of words is written back into `sh` text with `shlex.join` and read
    the same way. Text the reader can't read (nested too deep) gives no commands."""
    if isinstance(text, (list, tuple)) and all(isinstance(w, str) for w in text):
        if not text:
            return []
        text, dialect = shlex.join(text), "sh"
    elif not isinstance(text, str):
        return []
    turns = _turns()
    shell = turns.read_shell(text, dialect=dialect)
    scripts = turns.shell_scripts(shell, dialect=dialect) if depth > 0 else ()
    out: list[list[str]] = []
    for index, command in enumerate(shell.commands):
        own = [script for script in scripts if script.command_index == index]
        taken = {i for script in own for i in script.word_indexes}
        words = [w.text for i, w in enumerate(command.words) if i not in taken]
        targets = [r.target for r in command.redirects if r.target is not None]
        words += [target.text for target in targets]
        words += [f for i, w in enumerate(command.words) if i not in taken
                  for f in w.fields]
        words += [f for target in targets for f in target.fields]
        if words:
            out.append(words)
        subs = [sub for w in command.words for sub in w.subs]
        for r in command.redirects:
            subs += r.subs
            if r.target is not None:
                subs += r.target.subs
        for sub in subs:
            out.extend(commands(sub, dialect, depth))
        for script in own:
            out.extend(commands(script.text, script.dialect, depth - 1))
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


def escaped(words: list[str], home: str | None = None,
            verbs: Iterable[str] | None = None) -> bool:
    """Whether the simple command runs one of this environment's own `moxi` verbs named in
    `verbs` (`escapes()` when None): `moxi`, or the absolute path (or `~/` path, with
    `home`) of this environment's `moxi`, then one of those verbs."""
    words = _past_assignments(words)
    if len(words) < 2:
        return False
    if words[1] not in (escapes() if verbs is None else frozenset(verbs)):
        return False
    first = words[0]
    if first == "moxi":
        return True
    if first.startswith("~/") and home is not None:
        first = home + first[1:]
    if not first.startswith("/") or "\0" in first:
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


__all__ = ["NAME", "GUARD_ID", "SWITCH", "CALLS", "ESCAPES_KEY", "ESCAPES", "DENY", "judge",
           "decide", "transcript_roots", "escapes", "commands", "escaped",
           "own_moxi", "reset"]
