"""Run the user's own status line once with test input, and see which pieces it shows.

Setup and `moxi statusline use` call `detect(command)` only after the user said yes:
their script runs with made-up input and may store what it is given.

**The input.** Each piece of Claude Code's input the line may show carries a marker:

| piece | the marker |
|---|---|
| `dir` | the name of a folder made fresh for the run, given as `cwd`, `workspace.current_dir` and `workspace.project_dir` |
| `branch` | the branch of a repository made in that folder (with no `git`, none) |
| `model` | `model.display_name`, and `model.id` |
| `effort` | `effort.level`, `xhigh` |
| `context` | `context_window.used_percentage`, from 11 to 49 |
| `limits` | `five_hour` and `seven_day` `used_percentage`, each from 11 to 49; each resets 60 s ahead |
| `session` | `session_id`, a UUID's shape made of letters; found by its first 6 |
| `transcript` | the name of `transcript_path`, a file that is not made |
| `lines` | lines added and removed, each from 1000 to 9999 |
| `cost` | `total_cost_usd`, N.NN with N from 2 to 9 |

A text marker is 8 letters from `g` to `z`, so none can be part of a number or a hex
string. Every number in the input that is not a marker is 0 or a fixed size.

**A marker is found only as a whole token**: no letter, digit or `_` right before or after
it, and for a number no `.` either, so `37` is not found in `4137`, `a37f`, `_37_` or
`8.37`. The session's 6 letters need the left edge only, since a script may show 6, 8 or
all of the id.

**Numbers no clock can print.** A number marker is never a number a script could print
from the clock while the run lasts: from building the input to 10 s after it, and the
moment the limits reset, each in local time and in UTC. The two-digit markers skip the
year's last two digits, the month, the day, the hour (24- and 12-hour), the minute and the
second; the four-digit ones skip every year from 1900 to 2100 and every HHMM, MMSS,
mmdd and ddmm; the cost skips every H.MM. A countdown to the resets is under a
minute, outside every range drawn.

**The check before the run.** Before anything runs, each marker is looked for, by the
same rule the output is read with, in the whole input and the folder paths outside its
own piece's fields. Found anywhere else, the markers are drawn again, at most 10 times;
then nothing is detected.

**The run.** As Claude Code runs it, in the shell `shell.find` names (`sh -c` on POSIX;
on Windows Git Bash, else PowerShell): the command, in the made folder, the input on
stdin, at most 5 s, through the core's `run`, which keeps it in a process group (POSIX)
or a job (Windows) of its own and ends that when the time is up and once the script has
exited, so nothing it left running outlives the run. `HOME` is an empty folder inside
the throwaway folder, and every `XDG_*_HOME`, every `MOXI_*` variable but the core's start
mark, and `CLAUDE_CONFIG_DIR` are unset, so a script's writes under its home and default
folders land in the throwaway folder and go with it. A `~` or `$HOME` in the
command's own text is replaced by the real home folder first, so the command is still
found. Writes to absolute paths, and processes that leave the group, still escape. The
throwaway folder is made fresh under the system temp folder and removed every time.

**Reading the output.** Escape sequences (colours, links) are stripped, then each marker
is found as a token. Each piece found has its place and its row. What is left once the
found pieces and any path through the throwaway folder are taken out is kept as text the
line also shows. A run that times out, exits non-zero or prints nothing detects nothing,
with one line that says why.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping

from moxi import paths, proc

from . import shell

#: The pieces, in the order the summary would name them.
PIECES = ("dir", "branch", "model", "effort", "context", "limits", "session", "transcript",
          "lines", "cost")
#: The letters a text marker is drawn from: no digit, and no hex letter.
LETTERS = "ghijklmnopqrstuvwxyz"
#: The length of a text marker, and of the throwaway folder's name.
WORD = 8
#: The effort the input names, a marker of its own.
EFFORT = "xhigh"
#: How many letters of the session id find it.
SESSION_PREFIX = 6
#: How long the run may take, in seconds.
LIMIT = 5.0
#: How many times the markers are drawn again when the check before the run fails.
REDRAWS = 10
#: How far ahead the limits reset, in seconds.
RESET_AHEAD = 60
#: How long after building the input the clock rule covers, in seconds.
SPAN = 10
#: The context window's size in the input, a fixed number.
WINDOW = 200_000

_TWO = (11, 49)
_FOUR = (1000, 9999)
_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]"
                      r"|\x1b[@-Z\\-_]")


class Marker:
    """One marker: the `piece` it belongs to, its `text`, and how it is matched: `word`
    (a whole token), `number` (a whole token with no `.` beside it) or `prefix` (the left
    edge only)."""

    __slots__ = ("piece", "text", "kind", "_pattern")

    def __init__(self, piece: str, text: str, kind: str):
        self.piece = piece
        self.text = text
        self.kind = kind
        edge = r"[\w.]" if kind == "number" else r"\w"
        tail = "" if kind == "prefix" else rf"(?!{edge})"
        self._pattern = re.compile(rf"(?<!{edge}){re.escape(text)}{tail}")

    def find(self, text: str) -> list[int]:
        """Where this marker occurs in `text` as a token."""
        return [m.start() for m in self._pattern.finditer(text)]

    def __repr__(self) -> str:
        return f"Marker({self.piece!r}, {self.text!r}, {self.kind!r})"


class Draw:
    """Where the markers come from. Tests give their own to force a draw."""

    def __init__(self, rng: random.Random | None = None):
        self.rng = random.SystemRandom() if rng is None else rng

    def letters(self, n: int) -> str:
        return "".join(self.rng.choice(LETTERS) for _ in range(n))

    def number(self, low: int, high: int) -> int:
        return self.rng.randint(low, high)


class Found:
    """A piece the line shows: its name, its place in the output and its row (0 first)."""

    __slots__ = ("piece", "at", "row")

    def __init__(self, piece: str, at: int, row: int):
        self.piece = piece
        self.at = at
        self.row = row

    def __repr__(self) -> str:
        return f"Found({self.piece!r}, at={self.at}, row={self.row})"


class Detection:
    """What one detection found: `found`, the pieces in the order the line shows them;
    `also`, other text it shows, or None; `problem`, one line saying why nothing was
    detected, or None; `ran`, whether the line ran."""

    __slots__ = ("found", "also", "problem", "ran")

    def __init__(self, found: list[Found] | None = None, also: str | None = None,
                 problem: str | None = None, *, ran: bool = False):
        self.found = list(found or [])
        self.also = also
        self.problem = problem
        self.ran = ran

    def pieces(self) -> list[str]:
        return [f.piece for f in self.found]

    def rows(self) -> list[list[str]]:
        """The pieces found, row by row, rows with none left out."""
        out: dict[int, list[str]] = {}
        for item in self.found:
            out.setdefault(item.row, []).append(item.piece)
        return [out[row] for row in sorted(out)]

    def __repr__(self) -> str:
        return f"Detection({self.found!r}, also={self.also!r}, problem={self.problem!r})"


def nothing(problem: str | None = None) -> Detection:
    """A detection that found nothing, with why."""
    return Detection(problem=problem)


# -- the clock rule ----------------------------------------------------------------------

def clock_numbers(start: float, resets: float) -> tuple[set[int], set[int], set[str]]:
    """What a script could print from the clock: `(two-digit numbers, four-digit numbers,
    H.MM texts)` for every second from `start` to `SPAN` seconds after it and for
    `resets`, each in local time and in UTC."""
    two: set[int] = set()
    four: set[int] = set(range(1900, 2101))
    hm: set[str] = set()
    seconds = [*range(int(start), int(start + SPAN) + 1), int(resets)]
    for second in seconds:
        for when in (time.localtime(second), time.gmtime(second)):
            twelve = when.tm_hour % 12 or 12
            two |= {when.tm_year % 100, when.tm_mon, when.tm_mday, when.tm_hour, twelve,
                    when.tm_min, when.tm_sec}
            for hour in (when.tm_hour, twelve):
                four.add(hour * 100 + when.tm_min)
                hm.add(f"{hour}.{when.tm_min:02d}")
            four |= {when.tm_min * 100 + when.tm_sec, when.tm_mon * 100 + when.tm_mday,
                     when.tm_mday * 100 + when.tm_mon}
    return two, four, hm


# -- the input ---------------------------------------------------------------------------

class Plan:
    """One draw: the markers and the input they make, for a throwaway folder `root`."""

    def __init__(self, root: str, draw: Draw, start: float, *, taken: set[str]):
        self.root = root
        self.start = start
        self.resets = int(start) + RESET_AHEAD
        two, four, hm = clock_numbers(start, self.resets)
        words = set(taken)

        def word() -> str:
            while True:
                text = draw.letters(WORD)
                if text not in words:
                    words.add(text)
                    return text

        numbers: set[int] = set()

        def number(low: int, high: int, skip: set[int]) -> int:
            while True:
                value = draw.number(low, high)
                if value not in skip and value not in numbers:
                    numbers.add(value)
                    return value

        self.dir_name = word()
        self.branch = word()
        self.model_name = word()
        self.model_id = word()
        self.transcript_name = word()
        groups = [draw.letters(n) for n in (8, 4, 4, 4, 12)]
        self.session = "-".join(groups)
        self.context = number(*_TWO, two)
        self.five_hour = number(*_TWO, two)
        self.seven_day = number(*_TWO, two)
        self.added = number(*_FOUR, four)
        self.removed = number(*_FOUR, four)
        while True:
            whole = draw.number(2, 9)
            cents = draw.number(1, 99)
            text = f"{whole}.{cents:02d}"
            if cents % 10 and text not in hm:
                break
        self.cost_text = text
        self.cost = float(text)

        self.work_dir = os.path.join(root, self.dir_name)
        self.home = os.path.join(root, "home")
        self.transcript = os.path.join(root, self.transcript_name + ".jsonl")
        self.markers = [
            Marker("dir", self.dir_name, "word"),
            Marker("branch", self.branch, "word"),
            Marker("model", self.model_name, "word"),
            Marker("model", self.model_id, "word"),
            Marker("effort", EFFORT, "word"),
            Marker("context", str(self.context), "number"),
            Marker("limits", str(self.five_hour), "number"),
            Marker("limits", str(self.seven_day), "number"),
            Marker("session", self.session[:SESSION_PREFIX], "prefix"),
            Marker("transcript", self.transcript_name, "word"),
            Marker("lines", str(self.added), "number"),
            Marker("lines", str(self.removed), "number"),
            Marker("cost", self.cost_text, "number"),
        ]

    def payload(self, blank: str | None = None) -> dict:
        """The input Claude Code would give, with piece `blank`'s own fields emptied."""

        def own(piece: str, value):
            if piece != blank:
                return value
            return "" if isinstance(value, str) else 0

        cwd = own("dir", self.work_dir)
        return {
            "hook_event_name": "Status",
            "session_id": own("session", self.session),
            "transcript_path": own("transcript", self.transcript),
            "cwd": cwd,
            "model": {"id": own("model", self.model_id),
                      "display_name": own("model", self.model_name)},
            "workspace": {"current_dir": cwd, "project_dir": cwd},
            "effort": {"level": own("effort", EFFORT)},
            "context_window": {"used_percentage": own("context", self.context),
                               "context_window_size": WINDOW},
            "cost": {"total_cost_usd": own("cost", self.cost), "total_duration_ms": 0,
                     "total_api_duration_ms": 0,
                     "total_lines_added": own("lines", self.added),
                     "total_lines_removed": own("lines", self.removed)},
            "rate_limits": {
                "five_hour": {"used_percentage": own("limits", self.five_hour),
                              "resets_at": self.resets},
                "seven_day": {"used_percentage": own("limits", self.seven_day),
                              "resets_at": self.resets},
            },
        }

    def clash(self) -> Marker | None:
        """The first marker found outside its own piece's fields, or None. Each marker is
        looked for in the input with its piece's fields emptied, and in the folder paths
        that are not its piece's."""
        paths_ = [("dir", self.work_dir), (None, self.root), (None, self.home),
                  ("transcript", self.transcript)]
        for marker in self.markers:
            text = "\n".join([json.dumps(self.payload(marker.piece)),
                              *(path for piece, path in paths_ if piece != marker.piece)])
            if marker.find(text):
                return marker
        return None


def plan(root: str, draw: Draw | None = None, *, now: Callable[[], float] = time.time,
         redraws: int = REDRAWS) -> Plan | None:
    """Markers that pass the check before the run, drawn at most `redraws` times again,
    or None."""
    draw = Draw() if draw is None else draw
    taken = {os.path.basename(root)}
    for _ in range(redraws + 1):
        drawn = Plan(root, draw, now(), taken=taken)
        if drawn.clash() is None:
            return drawn
    return None


# -- reading the output ------------------------------------------------------------------

def strip(text: str) -> str:
    """`text` without escape sequences: colours, OSC 8 links and the rest."""
    return _ESCAPES.sub("", text)


def read(output: str, drawn: Plan, *, branch: bool = True) -> Detection:
    """What `output` shows, read against `drawn`'s markers. `branch` false: the folder is
    no repository, so no branch can be shown."""
    text = strip(output)
    if not text.strip():
        return Detection(problem="Your status line printed nothing, so nothing was detected.",
                         ran=True)
    first: dict[str, int] = {}
    cut: list[tuple[int, int]] = []
    for marker in drawn.markers:
        if marker.piece == "branch" and not branch:
            continue
        for at in marker.find(text):
            first[marker.piece] = min(at, first.get(marker.piece, at))
            end = at + len(marker.text)
            if marker.kind == "prefix":
                while (end < len(text) and end - at < len(drawn.session)
                       and text[end] == drawn.session[end - at]):
                    end += 1
            elif marker.piece == "transcript" and text.startswith(".jsonl", end):
                end += len(".jsonl")
            cut.append((at, end))
    found = sorted((Found(piece, at, text.count("\n", 0, at)) for piece, at in first.items()),
                   key=lambda f: f.at)
    return Detection(found, _also(text, cut, drawn.root), ran=True)


def _also(text: str, cut: list[tuple[int, int]], root: str) -> str | None:
    keep = [True] * len(text)
    for start, end in cut:
        for i in range(start, end):
            keep[i] = False
    left = "".join(ch for ch, kept in zip(text, keep) if kept)
    name = re.escape(os.path.basename(root))
    left = re.sub(rf"(?:[^\s/]*/)*{name}(?:/|(?![\w]))", "", left)
    rows = [" ".join(line.split()) for line in left.split("\n")]
    rows = [row for row in rows if row]
    return " | ".join(rows) if rows else None


# -- the run -----------------------------------------------------------------------------

def sandbox(base: Mapping[str, str], home: str) -> dict[str, str]:
    """The run's environment: `base` with `HOME` set to `home`, and every `XDG_*_HOME`,
    every `MOXI_*` variable and `CLAUDE_CONFIG_DIR` unset."""
    out = {key: value for key, value in base.items()
           if not (key.startswith("MOXI_") or key == "CLAUDE_CONFIG_DIR"
                   or (key.startswith("XDG_") and key.endswith("_HOME")))}
    out["HOME"] = home
    return out


def homed(command: str, home: str) -> str:
    """`command` with a leading `~` of a word and `$HOME`/`${HOME}` replaced by `home`."""
    command = re.sub(r"\$\{HOME\}|\$HOME(?!\w)", lambda _: home, command)
    return re.sub(r"(?<![^\s=:'\"])~(?=/|\s|$|['\"])", lambda _: home, command)


def run(command: str, data: bytes, *, cwd: str, env: Mapping[str, str],
        limit: float = LIMIT) -> tuple[int | None, bytes]:
    """Run `command` as Claude Code does (`shell.find`), with `data` on stdin, through the
    core's `run`: in a process group of its own on POSIX, a job on Windows, both ended when
    the time is up and once the command exits. `(exit code, stdout)`; the code is None
    when it ran out of time."""
    argv = shell.find(env).argv(command)
    try:
        done = proc.run(argv, timeout_s=limit, stdin_data=data, env=env, cwd=cwd,
                        first_signal="SIGKILL", grace_s=1.0, end_tree=True)
    except subprocess.TimeoutExpired:
        return None, b""
    return done.returncode, done.stdout or b""


def _git(repo_dir: str, branch: str, env: Mapping[str, str]) -> bool:
    """Make `repo_dir` a repository on `branch`. False without `git`, or when it fails."""
    git = shutil.which("git", path=env.get("PATH"))
    if git is None:
        return False
    try:
        done = subprocess.run([git, "init", "-q", "-b", branch], cwd=repo_dir, env=dict(env),
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=LIMIT,
                              check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def _make_root(tmp: str, draw: Draw) -> str:
    for _ in range(100):
        root = os.path.join(tmp, draw.letters(WORD))
        try:
            os.mkdir(root, 0o700)
        except FileExistsError:
            continue
        return root
    raise FileExistsError(f"no free folder name under {tmp}")


def remove(root: str) -> None:
    """Remove the throwaway folder, whatever the script left in it."""

    def again(function, path, _exc):
        try:
            os.chmod(os.path.dirname(path), stat.S_IRWXU)
            if os.path.isdir(path) and not os.path.islink(path):
                os.chmod(path, stat.S_IRWXU)
            function(path)
        except OSError:
            pass

    shutil.rmtree(root, onexc=again)


def detect(command: str, *, tmp: str | None = None, now: Callable[[], float] = time.time,
           draw: Draw | None = None, env: Mapping[str, str] | None = None,
           limit: float = LIMIT) -> Detection:
    """Run `command`, the user's status line, once with marked input, and read what it
    shows. `tmp` is where the throwaway folder is made (the system temp folder by
    default); `now` the clock; `env` the environment it runs from (this process's by
    default)."""
    draw = Draw() if draw is None else draw
    base = dict(os.environ if env is None else env)
    tmp = tempfile.gettempdir() if tmp is None else tmp
    try:
        root = _make_root(tmp, draw)
    except OSError as exc:
        return nothing(f"Moxi couldn't make a throwaway folder under {tmp} "
                       f"({exc.strerror or exc}), so nothing was detected.")
    try:
        drawn = plan(root, draw, now=now)
        if drawn is None:
            return nothing("Moxi couldn't draw test input whose markers stay apart (the "
                           "temp folder's path may hold one), so nothing was detected.")
        os.mkdir(drawn.work_dir, 0o700)
        os.mkdir(drawn.home, 0o700)
        run_env = sandbox(base, drawn.home)
        repo = _git(drawn.work_dir, drawn.branch, run_env)
        try:
            real_home = str(paths.home(base))
        except LookupError:
            real_home = None
        line = command if real_home is None else homed(command, real_home)
        data = json.dumps(drawn.payload()).encode("utf-8")
        try:
            code, out = run(line, data, cwd=drawn.work_dir, env=run_env, limit=limit)
        except OSError as exc:
            return nothing(f"Your status line couldn't be run ({exc.strerror or exc}), so "
                           "nothing was detected.")
        if code is None:
            return Detection(problem=f"Your status line didn't finish within {limit:g} s, "
                                     "so nothing was detected.", ran=True)
        if code != 0:
            return Detection(problem=f"Your status line exited with code {code}, so "
                                     "nothing was detected.", ran=True)
        return read(out.decode("utf-8", "replace"), drawn, branch=repo)
    finally:
        remove(root)


__all__ = ["detect", "read", "plan", "strip", "clock_numbers", "sandbox", "homed", "run",
           "remove", "Detection", "Found", "Marker", "Draw", "Plan", "PIECES", "LIMIT",
           "REDRAWS", "EFFORT"]
