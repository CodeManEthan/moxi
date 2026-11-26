"""The wake guard (`launcher.wake`): a shell call that would end a process under another
live session's harness process is refused when cache reads that session `cold`.

From outside a session, the only way to end its background shells or its subagents' work
is to kill processes under its harness process, and ending them gives the session a turn
on a cold cache: it wakes. The guard closes that way in.

**What it covers.** Claude Code's `Bash` and `PowerShell` tools and Codex's `Bash`, on the
main loop and in subagents. turns' case-blind pre-filter (`MATCH`) passes only a command
holding a kill-family word, so other calls never reach the judge.

**Which commands it reads** (`targets`). turns' shell reader reads the command, as
PowerShell for Claude's `PowerShell` tool and Codex's `Bash` on Windows and as `sh`
otherwise; this module reads no shell text itself. Every command `read_shell` gives is
read for a kill, its command word found by `command_word_index` (past assignments,
reserved words and the wrappers turns names, with their options). So is the text of each
substitution in it (a word's, a redirect's, an unquoted heredoc body's), read again in the
same dialect, and each script it hands a shell (`shell_scripts`: a `-c`, `-Command` or
`/c` argument, a heredoc or here-string on a shell, or literal text piped into one), read
in that shell's dialect, three shells deep at most (`SCRIPT_DEPTH`). A `cmd` script's
commands are read for `taskkill`. What the reader doesn't read (a value, an escape a
program processes, `eval`, an option or a program its tables don't name, a script file)
goes through.

**Which processes a command ends**:

- `kill`: numeric pids, and `-PGID` process groups after a signal or `--`. `kill -0`,
  `kill -l` and `kill -L` end nothing. `%job`, `$!` and `$$` are the caller's own shell
  and are skipped. `kill $(pgrep ...)` and `` kill `pgrep ...` `` resolve as `pgrep`
  would; so does `pidof`.
- `pkill` and `pgrep ... | xargs kill`: the pattern, matched the way the tool matches it
  (a regular expression searched in the process name, or in the whole command line with
  `-f`; `-x` for an exact match, `-i` case-blind), narrowed by `-P`, `-u`, `-g` and `-s`.
- `killall`: the name, matched exactly against the process name (`-r`, and macOS's `-m`,
  a regular expression, `-I` case-blind). `-l`, and macOS's dry runs `-s` and `-d`, end
  nothing.
- `taskkill /PID` and `/IM` (wildcards as Windows reads them), written `//PID` and `//IM`
  too as Git Bash needs them, and in PowerShell `Stop-Process` (and its aliases `spps`
  and `kill`) by `-Id` or `-Name`.

An argument word holding a `${...}` default or alternate that bash splits stands for the
words bash gives in its place (its `fields`), so `kill ${x:- 211 }` names 211. A
redirection (`2>/dev/null`, `2>&1`, `>&2`, `&>log`) is no target: the reader keeps it
apart from the words.

**On Windows** Claude's `Bash` tool runs Git Bash, whose `kill` takes the MSYS runtime's
own pids, not the Windows pids the process table holds; `pkill` and `killall` there read
MSYS processes too. So on Windows the judge resolves only `taskkill` on the shell, and a
`kill`, `pkill`, `killall` or `xargs kill` there is let through unjudged rather than read
against the wrong processes. Codex's shell tool runs PowerShell on Windows, so a Codex
`Bash` call there is read as PowerShell.

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

Importing this module imports only the standard library; the core, psutil, turns and the
launcher's other modules load at the first call that needs them.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
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

_POWERSHELL_TOOLS = frozenset({"PowerShell"})
#: The platforms whose shells read differently (`sys.platform`'s names).
WINDOWS, MACOS = "win32", "darwin"
#: The kill commands a Windows shell call isn't judged by: Git Bash's take MSYS pids.
_MSYS_KILLS = frozenset({"kill", "pkill", "killall"})
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
           look: Callable[[Live, Mapping[str, str]], Looked] | None = None,
           system: str | None = None) -> str | None:
    """The refusal for `call`, or None to let it run. `procs` is the process table,
    `sessions` lists the live sessions and `look` reads one's cache and name; each is the
    real one when None. `system` is the platform as `sys.platform` names it, this one's
    when None."""
    tool, harness = getattr(call, "tool", None), getattr(call, "harness", None)
    if tool not in CALLS.get(harness, ()):
        return None
    tool_input = getattr(call, "input", None) or {}
    command = tool_input.get("command") if isinstance(tool_input, Mapping) else None
    if not isinstance(command, str):
        return None
    system = system or sys.platform
    table = procs if procs is not None else Procs()
    powershell = tool in _POWERSHELL_TOOLS or (harness == "codex" and system == WINDOWS)
    pids = targets(command, table, powershell=powershell, system=system)
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
    said = ("Refused before it ran (launcher wake guard): this would end processes of "
            + "; ".join(parts) + ".")
    commands = [f"moxi launcher relaunch {looked.name or session.session_id}"
                for session, looked in cold]
    if len(cold) == 1:
        return (f"{said} Ending its processes would give it a turn on a cold cache; "
                f"relaunch it ({commands[0]}, as its director), or leave it.")
    return (f"{said} Ending their processes would give each a turn on a cold cache; "
            f"relaunch each, as its director ({'; '.join(commands)}), or leave each.")


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

#: How many shells deep a script handed to a shell is read: a script inside a script
#: inside a script is read, and one deeper goes through.
SCRIPT_DEPTH = 3
_TASKKILL = ("taskkill", "taskkill.exe")


def targets(command: str, procs: Procs, *, powershell: bool = False,
            system: str | None = None) -> list[int]:
    """The pids `command` would end, as far as they can be resolved, each once. It is read
    by turns' shell reader as PowerShell with `powershell`, as `sh` otherwise. `system` is
    the platform as `sys.platform` names it, this one's when None."""
    found: list[int] = []
    _read(command, "powershell" if powershell else "sh", SCRIPT_DEPTH, procs,
          system or sys.platform, found)
    return found


def _read(text: str, dialect: str, depth: int, procs: Procs, system: str,
          found: list[int]) -> None:
    """Add to `found` the pids each command of `text`, read in `dialect`, ends; then those
    of each substitution in it, read in the same dialect, and while `depth` is above 0
    those of each script it hands a shell, read in that shell's dialect at `depth - 1`."""
    turns = _turns()
    if turns is None:
        return
    shell = turns.read_shell(text, dialect=dialect)
    scripts = turns.shell_scripts(shell, dialect=dialect) if depth > 0 else ()
    commands = shell.commands
    for index, command in enumerate(commands):
        before = commands[index - 1] if index and commands[index - 1].ended_by == "|" \
            else None
        ends = _command(command, dialect, procs, before, system)
        found.extend(pid for pid in ends if pid not in found)
        for sub in _subs(command):
            _read(sub, dialect, depth, procs, system, found)
        for script in scripts:
            if script.command_index == index:
                _read(script.text, script.dialect, depth - 1, procs, system, found)


#: What the guard reads commands with: turns' shell reader (moxi-turns 0.2.8 and later).
_READER = ("read_shell", "shell_scripts", "command_word_index")


def _turns() -> Any:
    """turns' API, or None when turns can't be loaded or has no shell reader."""
    from moxi import modules

    found = modules.load("turns")
    if not found.ok or not all(hasattr(found.api, name) for name in _READER):
        return None
    return found.api


def _subs(command: Any) -> Iterator[str]:
    """The text of each substitution in `command`: in its words, in its redirects' targets
    and in an unquoted heredoc's body."""
    for word in command.words:
        yield from word.subs
    for redirect in command.redirects:
        yield from redirect.subs
        if redirect.target is not None:
            yield from redirect.target.subs


def _command(command: Any, dialect: str, procs: Procs, before: Any,
             system: str) -> list[int]:
    """The pids one command ends. `before` is the command piped into it, or None."""
    words = _command_words(command, dialect)
    if not words:
        return []
    if dialect == "powershell":
        return _ps_command(words, procs)
    if dialect == "cmd":
        return _taskkill(words[1:], procs) if _word_base(words[0]).lower() in _TASKKILL else []
    return _sh_command(words, procs, before, system)


def _sh_command(words: list[Any], procs: Procs, before: Any, system: str) -> list[int]:
    name = _word_base(words[0])
    if name in _TASKKILL:
        return _taskkill(words[1:], procs)
    if system == WINDOWS and (name in _MSYS_KILLS or name == "xargs"):
        # Git Bash: these read the MSYS runtime's pids and processes, not the table's.
        log.info("the wake guard does not judge %s in a Windows shell", name)
        return []
    if name == "kill":
        return _kill(_args(words[1:]), procs)
    if name == "pkill":
        return _pgrep(_args(words[1:]), procs)
    if name == "killall":
        return _killall(_args(words[1:]), procs, system)
    if name == "xargs" and before is not None:
        rest = _past_xargs(words[1:])
        if rest and _word_base(rest[0]) == "kill":
            return _found_by(_command_words(before, "sh"), procs) or []
    return []


def _args(words: Sequence[Any]) -> list[Any]:
    """A command's argument words as bash gives them: a word with `fields` (a `${...}`
    default or alternate bash splits) stands for those words."""
    out: list[Any] = []
    for word in words:
        if word.fields:
            out += [replace(word, text=text, subs=(), is_sub=False, fields=())
                    for text in word.fields]
        else:
            out.append(word)
    return out


def _kill(args: list[Any], procs: Procs) -> list[int]:
    out: list[int] = []
    options, signal = True, False
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if word.is_sub:
            out += _substituted(word, procs)
            continue
        text = word.text
        if options:
            if text == "--":
                options = False
                continue
            if text in ("-l", "-L", "--list", "--table"):
                return []
            if text in ("-s", "-n", "--signal") or text.startswith("--signal="):
                value = text.partition("=")[2] if "=" in text else \
                    (args[i].text if i < len(args) else "")
                i += 0 if "=" in text else 1
                if _null_signal(value):
                    return []
                signal = True
                continue
            if text.startswith("-") and len(text) > 1 and not signal:
                # The first option is the signal: -9, -KILL, -SIGTERM.
                if _null_signal(text[1:]):
                    return []
                signal = True
                continue
        if _PID.fullmatch(text):
            if int(text) > 0:  # kill 0 is the caller's own process group
                out.append(int(text))
        elif _GROUP.fullmatch(text):
            out += _group(int(text[1:]), procs)
        # Anything else (%job, $!, $$, a variable) is the caller's own or unresolved.
    return out


def _null_signal(value: object) -> bool:
    """Whether `value` names signal 0, which ends nothing."""
    return isinstance(value, str) and value.upper().removeprefix("SIG") in ("0", "NULL")


def _substituted(word: Any, procs: Procs) -> list[int]:
    """The pids a substitution prints when it is one `pgrep` or `pidof`; none otherwise."""
    turns = _turns()
    if turns is None:
        return []
    inner = [c for c in turns.read_shell(word.subs[0]).commands if c.words]
    if len(inner) != 1:
        return []
    return _found_by(_command_words(inner[0], "sh"), procs) or []


def _group(pgid: int, procs: Procs) -> list[int]:
    if pgid == 1:  # kill -1: every process the caller may signal
        return [p.pid for p in procs.all()]
    return [p.pid for p in procs.all() if p.pgid == pgid]


def _found_by(words: list[Any], procs: Procs) -> list[int] | None:
    """The pids a `pgrep` or `pidof` command prints; None for any other command."""
    if not words:
        return None
    name = _word_base(words[0])
    if name == "pgrep":
        return _pgrep(_args(words[1:]), procs)
    if name == "pidof":
        names = [w.text for w in _args(words[1:])
                 if not w.is_sub and not w.text.startswith("-")]
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


def _pgrep(args: list[Any], procs: Procs) -> list[int]:
    """What `pgrep` with `args` prints (and `pkill` with them ends), as pids."""
    flags: set[str] = set()
    values: dict[str, str] = {}
    pattern: str | None = None
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if word.is_sub:
            return []
        text = word.text
        if text.startswith("--") and len(text) > 2:
            key, eq, value = text.partition("=")
            if key == "--signal":
                i += 0 if eq else 1
                continue
            short = _PGREP_LONG.get(key)
            if short is None:
                return []
            if short in _PGREP_VALUED:
                if not eq:
                    if i >= len(args) or args[i].is_sub:
                        return []
                    value, i = args[i].text, i + 1
                values[short] = value
            else:
                flags.add(short)
            continue
        if text.startswith("-") and len(text) > 1 and pattern is None:
            body = text[1:]
            if _SIGNAL_NAME.fullmatch(body) and (body.isdigit() or len(body) > 1):
                continue  # a signal: -9, -KILL, -SIGTERM
            for j, ch in enumerate(body):
                if ch in _PGREP_VALUED:
                    value = body[j + 1:]
                    if not value:
                        if i >= len(args) or args[i].is_sub:
                            return []
                        value, i = args[i].text, i + 1
                    values[ch] = value
                    break
                flags.add(ch)
            continue
        if pattern is not None:
            return []
        pattern = text
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


def _killall(args: list[Any], procs: Procs, system: str) -> list[int]:
    names: list[str] = []
    regex = ignore = False
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if word.is_sub:
            return []
        text = word.text
        if text in ("-l", "--list") or (system == MACOS and text in ("-s", "-d")):
            return []  # lists signals; on macOS -s and -d only show what would be done
        if text in ("-r", "--regexp", "-m"):
            regex = True  # -m is macOS's regular expression
        elif text in ("-I", "--ignore-case"):
            ignore = True
        elif text in ("-s", "--signal"):
            i += 1
        elif text in ("-u", "--user", "-o", "--older-than", "-y", "--younger-than",
                      "-n", "--ns", "-Z", "--context", "-c", "-t", "-j"):
            return []  # a narrowing the judge doesn't read
        elif not text.startswith("-"):
            names.append(text)
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


def _taskkill(args: list[Any], procs: Procs) -> list[int]:
    out: list[int] = []
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if word.is_sub or word.text[:1] not in ("/", "-"):
            continue
        # Git Bash turns a lone /PID into a path, so a switch there is written //PID.
        text = word.text
        switch = (text[2:] if text.startswith("//") else text[1:]).lower()
        if switch in ("pid", "im") and i < len(args) and not args[i].is_sub:
            value, i = args[i].text, i + 1
            if switch == "pid":
                out += [int(v) for v in value.split(",") if _PID.fullmatch(v)]
            else:
                out += [p.pid for p in procs.all() if _wild(value, p.name)]
        elif switch == "fi":
            return []  # a filter: not read
    return out


def _wild(pattern: str, name: str) -> bool:
    return fnmatch.fnmatchcase(name.lower(), pattern.lower())


def _past_xargs(words: list[Any]) -> list[Any]:
    """The command `xargs` runs: its words past xargs' own options."""
    valued = {"-n", "-L", "-P", "-I", "-d", "-E", "-s", "-a", "--max-args", "--max-procs",
              "--delimiter", "--arg-file", "--max-lines", "--replace"}
    i = 0
    while i < len(words):
        word = words[i]
        if word.is_sub or not word.text.startswith("-"):
            break
        i += 2 if word.text in valued else 1
    return words[i:]


def _command_words(command: Any, dialect: str) -> list[Any]:
    """`command`'s words from the one that names what it runs (turns'
    `command_word_index`) on; none when no word does."""
    turns = _turns()
    at = None if turns is None else turns.command_word_index(command, dialect=dialect)
    return [] if at is None else list(command.words[at:])


def _word_base(word: Any) -> str:
    """The last part of a word's path, across `/` and `\\`; empty for a substitution."""
    return "" if word.is_sub else _base(word.text)


def _base(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


# -- PowerShell --------------------------------------------------------------------------

def _ps_command(words: list[Any], procs: Procs) -> list[int]:
    name = _word_base(words[0]).lower()
    if name in _TASKKILL:
        return _taskkill(words[1:], procs)
    if name not in ("stop-process", "spps", "kill"):
        return []
    ids: list[int] = []
    names: list[str] = []
    wanted: list | None = None
    for word in words[1:]:
        if word.is_sub:
            wanted = None
            continue
        text = word.text
        if text.startswith("-") and len(text) > 1 and not text[1:].isdigit():
            # `-Id:211` binds its value as `-Id 211` does; `-Id:` takes the next word.
            param, colon, value = text[1:].partition(":")
            param = param.lower()
            if "whatif".startswith(param) and len(param) >= 2:
                if not colon or value.lower() == "$true":
                    return []
                wanted = None
                continue
            wanted = ids if "id".startswith(param) else \
                names if "name".startswith(param) or "processname".startswith(param) \
                else None
            if not value:
                continue
            if wanted is None:
                continue  # another parameter's value, as `-Confirm:$false`
            text = value
        values = [v for v in text.split(",") if v]
        if wanted is None and not ids and not names:
            wanted = ids  # the first positional is -Id
        if wanted is ids:
            ids += [int(v) for v in values if _PID.fullmatch(v)]
        elif wanted is names:
            names += values
        wanted = wanted if text.endswith(",") else None
    out = list(ids)
    for p in procs.all() if names else ():
        bare = p.name[:-4] if p.name.lower().endswith(".exe") else p.name
        if any(_wild(n, bare) for n in names):
            out.append(p.pid)
    return out


__all__ = ["CALLS", "GUARD_ID", "Live", "Looked", "MATCH", "NAME", "Proc", "Procs",
           "SETTING", "decide", "judge", "look_session", "on_for", "owner_sessions",
           "refusal", "targets"]
