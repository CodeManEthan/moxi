"""The wake guard (`launcher.wake`): a shell call that would end a process under another
live session's harness process is refused when cache reads that session `cold`.

From outside a session, the only way to end its background shells or its subagents' work
is to kill processes under its harness process, and ending them gives the session a turn
on a cold cache: it wakes. The guard closes that way in.

**What it covers.** Claude Code's `Bash` and `PowerShell` tools and Codex's `Bash`, on the
main loop and in subagents. turns' case-blind pre-filter (`MATCH`) passes only a command
holding a kill-family word, so other calls never reach the judge.

**Which processes a command ends** (`targets`). The judge lexes the command into
pipelines of simple commands:

- `kill`: numeric pids, and `-PGID` process groups after a signal or `--`. `kill -0`,
  `kill -l` and `kill -L` end nothing. `%job`, `$!` and `$$` are the caller's own shell
  and are skipped. `kill $(pgrep ...)` and `` kill `pgrep ...` `` resolve as `pgrep`
  would; so does `pidof`.
- `pkill` and `pgrep ... | xargs kill`: the pattern, matched the way the tool matches it
  (a regular expression searched in the process name, or in the whole command line with
  `-f`; `-x` for an exact match, `-i` case-blind), narrowed by `-P`, `-u`, `-g` and `-s`.
- `killall`: the name, matched exactly against the process name (`-r` a regular
  expression, `-I` case-blind).
- `taskkill /PID` and `/IM` (wildcards as Windows reads them), and in PowerShell
  `Stop-Process` (and its aliases `spps` and `kill`) by `-Id` or `-Name`.

Any other target (a variable, a file read, an option the judge doesn't read) can't be
resolved, and is let through. A kill written in another language is not seen.

**Whose processes they are.** Each target's parents are walked up to a harness process
that turns lists (`turns.sessions()`). A target under the caller's own session, a target
that is a harness process itself (killing it ends the session without a turn), and a
target under no listed session are not this guard's.

**Whether that session is cold.** cache's read of the session, under the launch's own
config folder when the launcher started it (`recycle.cache_read`). `cold` refuses;
`warm`, `fresh`, `unknown` and `no-cache` pass.

**On by default** (`on_for`), where cache loads and the launcher's setting
`wake_guard.enabled` isn't false; naming `launcher.wake` on turns' `guards.run` turns it
on too. A judge that breaks or runs late lets the call through (`on_error = "allow"`).

Importing this module imports only the standard library; the core, psutil and the
launcher's other modules load at the first call that needs them.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("moxi.launcher.wake")

#: The guard's name in the launcher's `GUARDS`, its id, and the setting that turns it off.
NAME = "wake"
GUARD_ID = f"launcher.{NAME}"
SETTING = "wake_guard.enabled"
#: The tools it covers, per harness.
CALLS = {"claude": ("Bash", "PowerShell"), "codex": ("Bash",)}
#: turns' pre-filter, read case-blind: a kill-family word somewhere in the command.
MATCH = {"command": "kill|taskkill|stop-process|spps"}
#: The judge's bound, in milliseconds (clamped to turns' `guards.max_wait_ms`).
DEADLINE_MS = 2000
#: The cache kind that refuses.
COLD = "cold"
#: How far up a target's parents are walked before it counts as under no session.
WALK_LIMIT = 64

_SHELL_TOOLS = frozenset({"Bash"})
_POWERSHELL_TOOLS = frozenset({"PowerShell"})
#: Words that run the command after them (and their options), skipped to the command.
_PREFIXES = frozenset({"sudo", "command", "builtin", "exec", "nohup", "env", "time", "doas"})
#: `sudo`'s options that take a value, so the value is not the command.
_SUDO_VALUED = frozenset({"-u", "-g", "-h", "-p", "-C", "-U", "-r", "-t", "-D", "-R"})
_OWN_SHELL = frozenset({"$!", "$$", "${!}", "${$}"})
_PID = re.compile(r"\d+")
_GROUP = re.compile(r"-\d+")


# -- the judge ---------------------------------------------------------------------------

def on_for(env: Mapping[str, str]) -> bool:
    """turns' `sessions` function: whether the guard runs in a session started with `env`.
    True when cache loads and `wake_guard.enabled` isn't false; a setting that can't be
    read leaves it on."""
    from moxi import modules

    if not modules.available("cache") or not modules.load("cache", interface=1).ok:
        return False
    try:
        from . import settings

        return settings.current()[SETTING] is not False
    except Exception:  # noqa: BLE001 - an unreadable config leaves the shipped default
        return True


def judge(call: Any) -> Any:
    """turns' judge for `launcher.wake`: None, or turns' `Deny(reason)`."""
    reason = decide(call)
    if reason is None:
        return None
    from moxi import modules

    return modules.load("turns").api.Deny(reason)


def decide(call: Any, *, procs: Procs | None = None,
           sessions: Callable[[], Sequence[Live]] | None = None,
           look: Callable[[Live, Mapping[str, str]], Looked] | None = None) -> str | None:
    """The refusal for `call`, or None to let it run. `procs` is the process table,
    `sessions` lists the live sessions and `look` reads one's cache and name; each is the
    real one when None."""
    tool, harness = getattr(call, "tool", None), getattr(call, "harness", None)
    if tool not in CALLS.get(harness, ()):
        return None
    tool_input = getattr(call, "input", None) or {}
    command = tool_input.get("command") if isinstance(tool_input, Mapping) else None
    if not isinstance(command, str):
        return None
    table = procs if procs is not None else Procs()
    pids = targets(command, table, powershell=tool in _POWERSHELL_TOOLS)
    if not pids:
        return None
    live = list(sessions() if sessions is not None else live_sessions())
    owners = owner_sessions(pids, live, table)
    mine = getattr(call, "session_id", None)
    env = getattr(call, "env", None) or {}
    reader = look if look is not None else look_session
    cold = []
    for session in owners:
        if mine and (session.session_id == mine or mine in session.earlier):
            continue
        try:
            looked = reader(session, env)
        except Exception:  # noqa: BLE001 - an unread cache is no verdict: the call runs
            log.info("the wake guard could not read session %s's cache",
                     session.session_id, exc_info=True)
            continue
        if looked.kind == COLD:
            cold.append((session, looked))
    return refusal(cold) if cold else None


def refusal(cold: Sequence[tuple[Live, Looked]]) -> str:
    """The words a refused call reads: each cold session, its name and its cache's age,
    and what to do instead."""
    parts = []
    for session, looked in cold:
        who = f"session {session.session_id}"
        if looked.name:
            who += f" ({looked.name})"
        parts.append(f"{who}, whose cache is cold{_age(looked.age_s)}")
    which = cold[0][1].name or cold[0][0].session_id
    it = "it" if len(cold) == 1 else "each"
    return ("Refused before it ran (launcher wake guard): this would end processes of "
            + "; ".join(parts) + ". Ending its processes would give it a turn on a cold "
            f"cache; relaunch {it} (moxi launcher relaunch {which}, as its director), "
            f"or leave {it}.")


def _age(age_s: float | None) -> str:
    if age_s is None:
        return ""
    minutes = int(age_s // 60)
    if minutes < 60:
        return f" ({minutes} min since its last request)"
    return f" ({minutes // 60} h {minutes % 60} min since its last request)"


# -- whose processes ---------------------------------------------------------------------

@dataclass(frozen=True)
class Live:
    """A live session as turns lists it: its harness process, its ids and its harness."""

    pid: int
    session_id: str
    harness: str
    earlier: tuple[str, ...] = ()


@dataclass(frozen=True)
class Looked:
    """A session's cache kind and age (seconds since its last request, None when the read
    has none), and its comms name when its launch has one."""

    kind: str
    age_s: float | None = None
    name: str | None = None


def owner_sessions(pids: Iterable[int], live: Sequence[Live], procs: Procs) -> list[Live]:
    """The sessions whose harness process has one of `pids` under it, each once, in the
    order first met. A pid that is a harness process itself, or under none, is left out."""
    by_pid = {s.pid: s for s in live}
    out: dict[int, Live] = {}
    for pid in pids:
        if pid in by_pid:
            continue
        at, steps = procs.parent(pid), 0
        while at is not None and at > 0 and steps < WALK_LIMIT:
            if at in by_pid:
                out.setdefault(at, by_pid[at])
                break
            at, steps = procs.parent(at), steps + 1
    return list(out.values())


def live_sessions() -> list[Live]:
    """Every live session turns lists; none when turns can't be loaded."""
    from moxi import modules

    found = modules.load("turns")
    if not found.ok or found.api is None:
        return []
    out = []
    for session in found.api.sessions():
        try:
            info = session.info
            if isinstance(session.pid, int) and session.session_id:
                out.append(Live(session.pid, session.session_id, str(info.get("harness")),
                                tuple(info.get("earlier") or ())))
        finally:
            session.close()
    return out


def look_session(session: Live, env: Mapping[str, str]) -> Looked:
    """cache's read of `session` and its launch's comms name. A session the launcher
    started is read as its launch is (`recycle.cache_read`), one it didn't under cache's
    own configured places."""
    from moxi import modules

    from . import recycle, store

    row = None
    try:
        with store.reading(timeout=0.5) as conn:
            row = None if conn is None else store.resolve(conn, session.session_id)
    except Exception:  # noqa: BLE001 - an unread launch log: read the session as unlaunched
        row = None
    if row is not None:
        clock = recycle.cache_read(recycle.LOOK, row, session.session_id, env)
    else:
        found = modules.load("cache", interface=1)
        clock = found.api.read(session.harness, session.session_id) \
            if found.ok and found.api is not None else None
    if clock is None:
        return Looked("no-cache")
    age = getattr(clock, "age_s", None)
    return Looked(str(clock.kind),
                  float(age) if isinstance(age, (int, float)) and not isinstance(age, bool)
                  else None, _name(row))


def _name(row: Any) -> str | None:
    identity = getattr(row, "identity", None)
    if not identity:
        return None
    scope = getattr(row, "scope", None)
    return f"{identity}@{scope}" if scope else identity


# -- the process table -------------------------------------------------------------------

@dataclass(frozen=True)
class Proc:
    """One process: its pid, parent, name, command line, owner, and its process group and
    session where the system has them."""

    pid: int
    ppid: int | None
    name: str
    cmdline: tuple[str, ...] = ()
    user: str | None = None
    pgid: int | None = None
    sid: int | None = None


class Procs:
    """The process table, read through psutil once, at the first ask that needs it. A
    test passes `table`, the processes it stands for."""

    def __init__(self, table: Iterable[Proc] | None = None):
        self._table = None if table is None else list(table)

    def all(self) -> list[Proc]:
        if self._table is None:
            self._table = list(_read_table())
        return self._table

    def parent(self, pid: int) -> int | None:
        if self._table is not None:
            for proc in self._table:
                if proc.pid == pid:
                    return proc.ppid
            return None
        from moxi import proc

        return proc.parent(pid)


def _read_table() -> Iterator[Proc]:
    import psutil

    for p in psutil.process_iter(["pid", "ppid", "name", "cmdline", "username"]):
        info = p.info
        pgid = sid = None
        if hasattr(os, "getpgid"):
            try:
                pgid, sid = os.getpgid(info["pid"]), os.getsid(info["pid"])
            except OSError:
                pass
        yield Proc(info["pid"], info.get("ppid"), info.get("name") or "",
                   tuple(info.get("cmdline") or ()), info.get("username"), pgid, sid)


# -- which processes a command ends ------------------------------------------------------

def targets(command: str, procs: Procs, *, powershell: bool = False) -> list[int]:
    """The pids `command` would end, as far as they can be resolved, each once."""
    found: list[int] = []
    pipelines = _ps_split(command) if powershell else _sh_split(command)
    for pipeline in pipelines:
        for i, words in enumerate(pipeline):
            words = _command_words(words)
            if not words:
                continue
            ends = (_ps_command if powershell else _sh_command)(
                words, procs, pipeline[i - 1] if i else None)
            found.extend(pid for pid in ends if pid not in found)
    return found


def _sh_command(words: list[Word], procs: Procs, before: list[Word] | None) -> list[int]:
    name = _base(words[0])
    if name == "kill":
        return _kill(words[1:], procs)
    if name == "pkill":
        return _pgrep(words[1:], procs)
    if name == "killall":
        return _killall(words[1:], procs)
    if name in ("taskkill", "taskkill.exe"):
        return _taskkill(words[1:], procs)
    if name == "xargs" and before is not None:
        rest = _past_xargs(words[1:])
        if rest and _base(rest[0]) == "kill":
            return _found_by(_command_words(before), procs) or []
    return []


def _kill(args: list[Word], procs: Procs) -> list[int]:
    out: list[int] = []
    options, signal = True, False
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if isinstance(word, Sub):
            out += _substituted(word, procs)
            continue
        if options:
            if word == "--":
                options = False
                continue
            if word in ("-l", "-L", "--list", "--table"):
                return []
            if word in ("-s", "-n", "--signal") or word.startswith("--signal="):
                value = word.partition("=")[2] if "=" in word else \
                    (args[i] if i < len(args) else "")
                i += 0 if "=" in word else 1
                if _null_signal(value):
                    return []
                signal = True
                continue
            if word.startswith("-") and len(word) > 1 and not signal:
                # The first option is the signal: -9, -KILL, -SIGTERM.
                if _null_signal(word[1:]):
                    return []
                signal = True
                continue
        if _PID.fullmatch(word):
            if int(word) > 0:  # kill 0 is the caller's own process group
                out.append(int(word))
        elif _GROUP.fullmatch(word):
            out += _group(int(word[1:]), procs)
        # Anything else (%job, $!, $$, a variable) is the caller's own or unresolved.
    return out


def _null_signal(value: object) -> bool:
    """Whether `value` names signal 0, which ends nothing."""
    return isinstance(value, str) and value.upper().removeprefix("SIG") in ("0", "NULL")


def _substituted(word: Sub, procs: Procs) -> list[int]:
    """The pids a substitution prints when it is one `pgrep` or `pidof`; none otherwise."""
    inner = _sh_split(word.text)
    if len(inner) != 1 or len(inner[0]) != 1:
        return []
    return _found_by(_command_words(inner[0][0]), procs) or []


def _group(pgid: int, procs: Procs) -> list[int]:
    if pgid == 1:  # kill -1: every process the caller may signal
        return [p.pid for p in procs.all()]
    return [p.pid for p in procs.all() if p.pgid == pgid]


def _found_by(words: list[Word], procs: Procs) -> list[int] | None:
    """The pids a `pgrep` or `pidof` command prints; None for any other command."""
    if not words:
        return None
    name = _base(words[0])
    if name == "pgrep":
        return _pgrep(words[1:], procs)
    if name == "pidof":
        names = [w for w in words[1:] if isinstance(w, str) and not w.startswith("-")]
        return [p.pid for p in procs.all()
                if any(n in (p.name, _base(p.cmdline[0]) if p.cmdline else "")
                       for n in names)]
    return None


#: pgrep's and pkill's options that take a value; of them the judge reads `-P`, `-u`, `-g`
#: and `-s`, and `-d` changes only how pgrep prints.
_PGREP_VALUED = frozenset("dgGPsuUtF")
_PGREP_READ = frozenset("Pugsd")
_PGREP_LONG = {"--full": "f", "--exact": "x", "--ignore-case": "i", "--parent": "P",
               "--euid": "u", "--uid": "U", "--pgroup": "g", "--session": "s",
               "--group": "G", "--terminal": "t", "--pidfile": "F", "--delimiter": "d",
               "--newest": "n", "--oldest": "o", "--inverse": "v", "--echo": "e",
               "--count": "c", "--list-name": "l", "--list-full": "a"}
#: Flags that pick processes the judge can't tell: newest, oldest, inverted.
_PGREP_UNREAD = frozenset("nov")
_SIGNAL_NAME = re.compile(r"(SIG)?[A-Z][A-Z0-9+-]+|\d+")


def _pgrep(args: list[Word], procs: Procs) -> list[int]:
    """What `pgrep` with `args` prints (and `pkill` with them ends), as pids."""
    flags: set[str] = set()
    values: dict[str, str] = {}
    pattern: str | None = None
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if isinstance(word, Sub):
            return []
        if word.startswith("--") and len(word) > 2:
            key, eq, value = word.partition("=")
            if key == "--signal":
                i += 0 if eq else 1
                continue
            short = _PGREP_LONG.get(key)
            if short is None:
                return []
            if short in _PGREP_VALUED:
                if not eq:
                    if i >= len(args) or isinstance(args[i], Sub):
                        return []
                    value, i = args[i], i + 1
                values[short] = value
            else:
                flags.add(short)
            continue
        if word.startswith("-") and len(word) > 1 and pattern is None:
            body = word[1:]
            if _SIGNAL_NAME.fullmatch(body) and (body.isdigit() or len(body) > 1):
                continue  # a signal: -9, -KILL, -SIGTERM
            for j, ch in enumerate(body):
                if ch in _PGREP_VALUED:
                    value = body[j + 1:]
                    if not value:
                        if i >= len(args) or isinstance(args[i], Sub):
                            return []
                        value, i = args[i], i + 1
                    values[ch] = value
                    break
                flags.add(ch)
            continue
        if pattern is not None:
            return []
        pattern = word
    if flags & _PGREP_UNREAD or set(values) - _PGREP_READ:
        return []
    if pattern is None and not set(values) - {"d"}:
        return []
    try:
        regex = re.compile(pattern or "", re.IGNORECASE if "i" in flags else 0)
    except re.error:
        return []
    out = []
    for p in procs.all():
        text = " ".join(p.cmdline) if "f" in flags and p.cmdline else p.name
        hit = regex.fullmatch(text) if "x" in flags else regex.search(text)
        if hit and _narrowed(p, values):
            out.append(p.pid)
    return out


def _narrowed(p: Proc, values: Mapping[str, str]) -> bool:
    for key, value in values.items():
        wanted = value.split(",")
        if key == "P" and str(p.ppid) not in wanted:
            return False
        if key == "u" and (p.user or "") not in wanted:
            return False
        if key == "g" and str(p.pgid) not in wanted:
            return False
        if key == "s" and str(p.sid) not in wanted:
            return False
    return True


def _killall(args: list[Word], procs: Procs) -> list[int]:
    names: list[str] = []
    regex = ignore = False
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if isinstance(word, Sub):
            return []
        if word in ("-r", "--regexp"):
            regex = True
        elif word in ("-I", "--ignore-case"):
            ignore = True
        elif word in ("-s", "--signal"):
            i += 1
        elif word in ("-u", "--user", "-o", "--older-than", "-y", "--younger-than",
                      "-n", "--ns", "-Z", "--context"):
            return []  # a narrowing the judge doesn't read
        elif not word.startswith("-"):
            names.append(word)
    out = []
    for p in procs.all():
        own = (p.name, _base(p.cmdline[0]) if p.cmdline else p.name)
        for name in names:
            if regex:
                try:
                    hit = any(re.search(name, n, re.IGNORECASE if ignore else 0)
                              for n in own)
                except re.error:
                    return []
            elif "/" in name:
                hit = bool(p.cmdline) and p.cmdline[0] == name
            else:
                hit = any(name.lower() == n.lower() if ignore else name == n for n in own)
            if hit:
                out.append(p.pid)
                break
    return out


def _taskkill(args: list[Word], procs: Procs) -> list[int]:
    out: list[int] = []
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if isinstance(word, Sub) or not word[:1] in ("/", "-"):
            continue
        switch = word[1:].lower()
        if switch in ("pid", "im") and i < len(args) and isinstance(args[i], str):
            value, i = args[i], i + 1
            if switch == "pid":
                out += [int(v) for v in value.split(",") if _PID.fullmatch(v)]
            else:
                out += [p.pid for p in procs.all() if _wild(value, p.name)]
        elif switch == "fi":
            return []  # a filter: not read
    return out


def _wild(pattern: str, name: str) -> bool:
    return fnmatch.fnmatchcase(name.lower(), pattern.lower())


def _past_xargs(words: list[Word]) -> list[Word]:
    """The command `xargs` runs: its words past xargs' own options."""
    valued = {"-n", "-L", "-P", "-I", "-d", "-E", "-s", "-a", "--max-args", "--max-procs",
              "--delimiter", "--arg-file", "--max-lines", "--replace"}
    i = 0
    while i < len(words):
        word = words[i]
        if not isinstance(word, str) or not word.startswith("-"):
            break
        i += 2 if word in valued else 1
    return words[i:]


def _command_words(words: list[Word]) -> list[Word]:
    """`words` from the command word on: past `NAME=value` assignments and words that run
    the command after them (`sudo`, `env`, `nohup`, ...) with their options."""
    i = 0
    while i < len(words):
        word = words[i]
        if not isinstance(word, str):
            break
        name, eq, _ = word.partition("=")
        if eq and name and (name[0].isalpha() or name[0] == "_") and \
                all(ch.isalnum() or ch == "_" for ch in name):
            i += 1
            continue
        if _base(word) in _PREFIXES:
            prefix = _base(word)
            i += 1
            while i < len(words) and isinstance(words[i], str) and \
                    words[i].startswith("-"):
                i += 2 if prefix in ("sudo", "doas") and words[i] in _SUDO_VALUED else 1
            continue
        break
    return words[i:]


def _base(word: Word) -> str:
    if not isinstance(word, str):
        return ""
    return word.replace("\\", "/").rsplit("/", 1)[-1]


# -- PowerShell --------------------------------------------------------------------------

def _ps_command(words: list[Word], procs: Procs, before: list[Word] | None) -> list[int]:
    name = _base(words[0]).lower()
    if name in ("taskkill", "taskkill.exe"):
        return _taskkill(words[1:], procs)
    if name not in ("stop-process", "spps", "kill"):
        return []
    ids: list[int] = []
    names: list[str] = []
    wanted: list | None = None
    for word in words[1:]:
        if isinstance(word, Sub):
            wanted = None
            continue
        if word.startswith("-") and len(word) > 1 and not word[1:].isdigit():
            param = word[1:].rstrip(":").lower()
            if "whatif".startswith(param) and len(param) >= 2:
                return []
            wanted = ids if "id".startswith(param) else \
                names if "name".startswith(param) or "processname".startswith(param) \
                else None
            continue
        values = [v for v in word.split(",") if v]
        if wanted is None and not ids and not names:
            wanted = ids  # the first positional is -Id
        if wanted is ids:
            ids += [int(v) for v in values if _PID.fullmatch(v)]
        elif wanted is names:
            names += values
        wanted = wanted if word.endswith(",") else None
    out = list(ids)
    for p in procs.all() if names else ():
        bare = p.name[:-4] if p.name.lower().endswith(".exe") else p.name
        if any(_wild(n, bare) for n in names):
            out.append(p.pid)
    return out


# -- lexing ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Sub:
    """A word that is one command substitution, `$(...)` or a backtick pair: its text."""

    text: str


Word = str | Sub


@dataclass
class _Lexer:
    text: str
    pipelines: list[list[list[Word]]] = field(default_factory=list)
    pipeline: list[list[Word]] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)
    word: list[str] = field(default_factory=list)
    sub: Sub | None = None
    started: bool = False

    def end_word(self) -> None:
        if self.sub is not None:
            self.words.append(self.sub)
        elif self.started:
            self.words.append("".join(self.word))
        self.word.clear()
        self.sub, self.started = None, False

    def mixed(self) -> None:
        """Text joins a word that holds a substitution, or a substitution joins text: the
        word resolves as nothing."""
        self.sub = None
        self.word.append("\0")
        self.started = True

    def end_command(self) -> None:
        self.end_word()
        if self.words:
            self.pipeline.append(list(self.words))
        self.words.clear()

    def end_pipeline(self) -> None:
        self.end_command()
        if self.pipeline:
            self.pipelines.append(list(self.pipeline))
        self.pipeline.clear()


def _closing(text: str, i: int) -> int:
    """Where the `)` closing a `$(` that opened before `i` is, or len(text)."""
    depth, n = 1, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _sh_split(text: str) -> list[list[list[Word]]]:
    """`text` as pipelines of simple commands of words, quotes taken off. A pipeline ends
    at `;`, `&`, `&&`, `||`, `(`, `)` and a newline outside quotes; `|` joins its
    commands. A word that is one `$(...)` or backtick substitution, bare or alone in
    double quotes, is a `Sub`; a word with one inside other text holds a NUL, so it
    resolves as nothing."""
    lx = _Lexer(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "'":
            j = text.find("'", i + 1)
            j = n if j < 0 else j
            if lx.sub is not None:
                lx.mixed()
            lx.word.append(text[i + 1:j])
            lx.started, i = True, j + 1
            continue
        if c == '"':
            whole = not lx.started and lx.sub is None
            if lx.sub is not None:
                lx.mixed()
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "$" and text[i + 1:i + 2] == "(":
                    j = _closing(text, i + 2)
                    if whole and not lx.word and text[j + 1:j + 2] == '"':
                        lx.sub = Sub(text[i + 2:j])  # "$(...)": the word is the substitution
                    else:
                        lx.mixed()
                    i = j + 1
                    continue
                if text[i] == "\\" and i + 1 < n and text[i + 1] in '"\\$`\n':
                    if text[i + 1] != "\n":
                        lx.word.append(text[i + 1])
                    i += 2
                    continue
                lx.word.append(text[i])
                i += 1
            if lx.sub is None:
                lx.started = True
            i += 1
            continue
        if c == "\\":
            if i + 1 < n and text[i + 1] != "\n":
                if lx.sub is not None:
                    lx.mixed()
                lx.word.append(text[i + 1])
                lx.started = True
            i += 2
            continue
        if c == "#" and not lx.started and lx.sub is None:
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "$" and text[i + 1:i + 2] == "(":
            j = _closing(text, i + 2)
            if lx.started or lx.sub is not None:
                lx.mixed()
            else:
                lx.sub = Sub(text[i + 2:j])
            i = j + 1
            continue
        if c == "`":
            j = text.find("`", i + 1)
            j = n if j < 0 else j
            if lx.started or lx.sub is not None:
                lx.mixed()
            else:
                lx.sub = Sub(text[i + 1:j])
            i = j + 1
            continue
        if c == "|" and text[i + 1:i + 2] != "|":
            lx.end_command()
            i += 1
            continue
        if c in ";&|()\n":
            lx.end_pipeline()
            i += 2 if text[i:i + 2] in ("&&", "||") else 1
            continue
        if c in " \t\r<>":
            lx.end_word()
            i += 1
            continue
        if lx.sub is not None:
            lx.mixed()
        lx.word.append(c)
        lx.started = True
        i += 1
    lx.end_pipeline()
    return lx.pipelines


def _ps_split(text: str) -> list[list[list[Word]]]:
    """`text` read as PowerShell: pipelines end at `;` and a newline, `|` joins commands,
    a word ends at a space or tab; quotes are taken off; a `(...)` or `$(...)` group is a
    `Sub`; a backtick escapes the next character."""
    lx = _Lexer(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "'":
            j = text.find("'", i + 1)
            j = n if j < 0 else j
            if lx.sub is not None:
                lx.mixed()
            lx.word.append(text[i + 1:j])
            lx.started, i = True, j + 1
            continue
        if c == '"':
            j = text.find('"', i + 1)
            j = n if j < 0 else j
            if lx.sub is not None:
                lx.mixed()
            lx.word.append(text[i + 1:j])
            lx.started, i = True, j + 1
            continue
        if c == "`" and i + 1 < n:
            lx.word.append(text[i + 1])
            lx.started, i = True, i + 2
            continue
        if c == "#" and not lx.started:
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "(" or (c == "$" and text[i + 1:i + 2] == "("):
            start = i + (2 if c == "$" else 1)
            j = _closing(text, start)
            if lx.started or lx.sub is not None:
                lx.mixed()
            else:
                lx.sub = Sub(text[start:j])
            i = j + 1
            continue
        if c == "|" and text[i + 1:i + 2] != "|":
            lx.end_command()
            i += 1
            continue
        if c in ";\n" or text[i:i + 2] in ("&&", "||"):
            lx.end_pipeline()
            i += 2 if text[i:i + 2] in ("&&", "||") else 1
            continue
        if c in " \t\r":
            lx.end_word()
            i += 1
            continue
        if lx.sub is not None:
            lx.mixed()
        lx.word.append(c)
        lx.started = True
        i += 1
    lx.end_pipeline()
    return lx.pipelines


__all__ = ["CALLS", "GUARD_ID", "Live", "Looked", "MATCH", "NAME", "Proc", "Procs",
           "SETTING", "decide", "judge", "look_session", "on_for", "owner_sessions",
           "refusal", "targets"]
