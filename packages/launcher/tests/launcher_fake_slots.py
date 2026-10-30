"""A stand-in for slots' API, as the launcher calls it: `supported`, `show`, `start` (which
claims a slot and starts the command there), and the screen calls `read_screen` and
`answer_screen`, with slots' errors and result shapes, field for field (`test_slot` holds
them against slots' own when slots is installed).

The launcher reaches it through its test seam, `MOXI_LAUNCHER_SLOTS=launcher_fake_slots`.
What it does is scripted by `script.json` in the directory `LAUNCHER_FAKE_SLOTS` names, read
at each call, and every call it gets is appended to `calls.jsonl` there.

The script's keys, each with its default:

- `supported`: `{"ok": true, "why": ""}`.
- `slots`: what `show` reads, by slot (`{"3": {"state": "occupied", "launchable": false,
  "hold": null, "label": ..., "last": {"at": ..., "label": ..., "meta": {...}}}}`), any
  other `Slot` field given by name. A listed slot has a registry row (`created` is a time)
  unless it gives `"created": null`, a session of a slot's name with no row, which slots
  reads not launchable. A slot not listed reads None, except the slot a `start`
  placed, which reads occupied with the pane's id and pid, and `dead_status` as the pane's
  exit status.
- `show`: `ok`, `try-again` (raises `TryAgain`), `error` (raises `TmuxError`), `registry`
  (raises `RegistryError`) or `usage` (raises `UsageError`).
- `start`: `ok`, `refused` (raises `Refused` from `refused`: `reason`, `words`, and
  `holder`, a mapping as slots gives it: `holder` and `until` for `held`, the last launch's
  `label` and `meta` for `occupied`), `try-again`, `error` or `registry`. As slots does,
  `start` raises `UsageError` for a relative `cwd` and for `create=True` with a slot
  named.
- `placed`: the slot a start that names none takes (1).
- `exec`: `started`, `failed` or `unconfirmed`, with `exec_line` and `dead_status`
  (null while the pane lives).
- `run`: true to really run the command, detached, with the env and cwd it was given; its
  pid is the pane's. Otherwise the pane's pid is `pane_pid` (4343).
- `frames`: what each `read_screen` sees in turn, the last repeated: `{"screen": NAME or
  null, "full": true, "dead": false}`, NAME a screen constant's name; the read gives that
  `Screen` itself, as slots does. `dead` is the pane's `pane_dead`. Default: no screen.
- `answers`: what each `answer_screen` gives in turn, the last repeated: `{"outcome": ...,
  "why": ..., "capture": ..., "keys": [...], "dead": ...}`, `outcome` one of slots' words
  (`gone`, `ended`, `not-ended`, `mismatch`, `ended-without-screen`, `no-pane`); `why` is
  words for a person, led by the word `not-settled` for a `mismatch` whose settle bound ran
  out; `dead` is the last read's `Seen.dead`. Default: `gone`.

The turns through `frames` and `answers` are counted in this process; `reset()` starts
them again.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DIR = "LAUNCHER_FAKE_SLOTS"

REASONS = ("occupied", "held", "missing", "in-use", "no-slot", "no-free-slot", "live-pane",
           "name-taken", "job-running", "unsupported")


class SlotsError(Exception):
    pass


class Refused(SlotsError):
    def __init__(self, reason, words, holder=None):
        if reason not in REASONS:
            raise ValueError(f"not a refusal's reason: {reason!r}")
        super().__init__(words)
        self.reason, self.words, self.holder = reason, words, holder


class TryAgain(SlotsError):
    pass


class TmuxError(SlotsError):
    pass


class RegistryError(SlotsError):
    pass


class UsageError(SlotsError, ValueError):
    pass


@dataclass(frozen=True)
class Support:
    ok: bool
    why: str = ""


@dataclass(frozen=True)
class Hold:
    holder: str
    until: float


@dataclass(frozen=True)
class Last:
    at: float
    label: str
    meta: dict | None = None


@dataclass(frozen=True)
class Slot:
    number: int
    name: str | None
    state: str
    hold: Hold | None
    launchable: bool
    idle: bool
    attached: int
    label: str | None
    pane_id: str | None
    window_id: str | None
    pane_pid: int | None
    current_command: str | None
    dead_status: int | None
    last: Last | None
    created: float | None


#: When a fake slot's registry row was made.
CREATED = 1880000000.0


def make_slot(number: int, state: str, launchable: bool = False, **fields) -> Slot:
    """A registered `Slot` with every other field not given empty."""
    blank = {"name": None, "hold": None, "idle": False, "attached": 0, "label": None,
             "pane_id": None, "window_id": None, "pane_pid": None, "current_command": None,
             "dead_status": None, "last": None, "created": CREATED}
    return Slot(number=number, state=state, launchable=launchable, **{**blank, **fields})


@dataclass(frozen=True)
class Placed:
    slot: int
    pane_id: str
    window_id: str
    exec: str
    exec_line: str
    dead_status: int | None = None


@dataclass(frozen=True)
class Items:
    after: str
    kinds: tuple[str, ...]
    sep: str = " · "


@dataclass(frozen=True)
class Screen:
    name: str
    phrases: tuple[str, ...]
    option: str
    move: bool
    then: str
    marker: str = "❯"
    items: Items | None = None


@dataclass(frozen=True)
class Seen:
    screen: object | None
    full: bool
    why: str
    rows: tuple[str, ...] = ()
    dead: bool = False
    pane_id: str | None = None


@dataclass(frozen=True)
class Answer:
    outcome: str
    screen: object
    why: str = ""
    seen: Seen | None = None
    capture: str | None = None
    keys: tuple[str, ...] = ()


CLAUDE_EXIT_DIALOG = Screen(
    name="CLAUDE_EXIT_DIALOG",
    phrases=("Background work is running", "will stop when you exit"),
    option="Exit and stop tasks", move=False, then="ended",
    items=Items(after="will stop when you exit", kinds=("shell", "monitor")))
CLAUDE_TRUST_FOLDER = Screen(
    name="CLAUDE_TRUST_FOLDER",
    phrases=("Accessing workspace:", "Enter to confirm"),
    option="Yes, I trust this folder", move=True, then="gone")
CLAUDE_DEV_CHANNELS = Screen(
    name="CLAUDE_DEV_CHANNELS",
    phrases=("Loading development channels", "Enter to confirm"),
    option="I am using this for local development", move=True, then="gone")
SCREENS = {"CLAUDE_TRUST_FOLDER": CLAUDE_TRUST_FOLDER,
           "CLAUDE_DEV_CHANNELS": CLAUDE_DEV_CHANNELS,
           "CLAUDE_EXIT_DIALOG": CLAUDE_EXIT_DIALOG}


def screen_name(screen: Screen) -> str:
    """The constant `screen` is declared under, checked to be that constant."""
    assert SCREENS[screen.name] is screen, screen
    return screen.name


@dataclass
class _Turns:
    reads: int = 0
    answers: int = 0
    placed: dict = field(default_factory=dict)


_turns = _Turns()


def reset() -> None:
    global _turns
    _turns = _Turns()


def _dir() -> Path:
    return Path(os.environ[DIR])


def script() -> dict:
    path = _dir() / "script.json"
    return json.loads(path.read_text()) if path.exists() else {}


def record(call: str, **detail) -> None:
    with open(_dir() / "calls.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps({"call": call, **detail}, default=str) + "\n")


def calls(directory: Path, name: str | None = None) -> list[dict]:
    path = Path(directory) / "calls.jsonl"
    if not path.exists():
        return []
    found = [json.loads(line) for line in path.read_text().splitlines() if line]
    return [c for c in found if name is None or c["call"] == name]


def supported() -> Support:
    record("supported")
    plan = script().get("supported", {"ok": True, "why": ""})
    return Support(plan["ok"], plan.get("why", ""))


def show(slot):
    record("show", slot=slot)
    plan = script()
    how = plan.get("show", "ok")
    _raise(how, "list-panes")
    entry = plan.get("slots", {}).get(str(slot))
    if entry is not None:
        fields = dict(entry)
        fields["hold"] = Hold(**entry["hold"]) if entry.get("hold") else None
        fields["last"] = Last(**entry["last"]) if entry.get("last") else None
        number = int(slot) if str(slot).isdigit() else fields.pop("number", 1)
        return make_slot(number, fields.pop("state"), fields.pop("launchable"), **fields)
    placed = _turns.placed.get(str(slot))
    if placed is not None:
        return make_slot(
            int(slot), "occupied", label=placed["label"],
            last=Last(1.0, placed["label"], placed["meta"]), pane_id="%7",
            window_id="@3", pane_pid=placed["pid"], dead_status=plan.get("dead_status"))
    return None


def _raise(how: str, tmux_call: str) -> None:
    if how == "try-again":
        raise TryAgain("the slots server is not answering yet")
    if how == "error":
        raise TmuxError(f"tmux {tmux_call} failed")
    if how == "registry":
        raise RegistryError("the slots registry can't be read: not JSON")
    if how == "usage":
        raise UsageError("a slot name is 1-32 lower-case letters, digits and '-'")


def start(slot, argv, *, env, cwd, label, meta=None, hold=None, create=False) -> Placed:
    record("start", slot=slot, argv=list(argv), env=dict(env), cwd=cwd, label=label,
           meta=meta, hold=hold, create=create)
    if slot is not None and create:
        raise UsageError("create=True makes a slot only for a start that names none")
    if not os.path.isabs(os.fspath(cwd)):
        raise UsageError(f"cwd is an absolute path, not {cwd!r}")
    plan = script()
    how = plan.get("start", "ok")
    if how == "refused":
        given = plan.get("refused", {})
        raise Refused(given.get("reason", "occupied"), given.get("words", "slot taken"),
                      given.get("holder"))
    if how == "try-again":
        raise TryAgain("the slots lock was not had in 10 s")
    _raise(how, "new-window")
    number = slot if slot is not None else plan.get("placed", 1)
    pid = plan.get("pane_pid", 4343)
    if plan.get("run"):
        child = subprocess.Popen(list(argv), env=dict(env), cwd=cwd,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        pid = child.pid
    _turns.placed[str(number)] = {"label": label, "meta": meta, "pid": pid}
    verdict = plan.get("exec", "started")
    return Placed(number, "%7", "@3", verdict,
                  plan.get("exec_line", "exec" if verdict == "started" else "taken"),
                  plan.get("dead_status"))


def _nth(items: list, n: int):
    return items[min(n, len(items) - 1)]


def read_screen(slot, screens) -> Seen:
    record("read_screen", slot=slot, screens=[screen_name(s) for s in screens])
    frames = script().get("frames") or [{"screen": None}]
    frame = _nth(frames, _turns.reads)
    _turns.reads += 1
    name = frame.get("screen")
    return Seen(SCREENS[name] if name else None, frame.get("full", True),
                frame.get("why", ""), (), bool(frame.get("dead", False)), "%7")


def answer_screen(slot, screen, wait=5) -> Answer:
    record("answer_screen", slot=slot, screen=screen_name(screen), wait=wait)
    answers = script().get("answers") or [{"outcome": "gone"}]
    answer = _nth(answers, _turns.answers)
    _turns.answers += 1
    seen = Seen(screen, False, answer.get("why", ""), (), bool(answer.get("dead", False)),
                "%7")
    return Answer(answer["outcome"], screen, answer.get("why", ""), seen,
                  answer.get("capture"), tuple(answer.get("keys", ())))
