"""A stand-in for slots' API, as the launcher calls it: `supported`, `show`, `start` (which
claims a slot and starts the command there), and the screen calls `read_screen` and
`answer_screen`, with slots' refusals and result shapes.

The launcher reaches it through its test seam, `MOXI_LAUNCHER_SLOTS=launcher_fake_slots`.
What it does is scripted by `script.json` in the directory `LAUNCHER_FAKE_SLOTS` names, read
at each call, and every call it gets is appended to `calls.jsonl` there.

The script's keys, each with its default:

- `supported`: `{"ok": true, "why": ""}`.
- `slots`: what `show` reads, by slot (`{"3": {"state": "occupied", "launchable": false,
  "hold": null, "label": ..., "last": {"at": ..., "label": ..., "meta": {...}}}}`). A slot
  not listed reads None, except the slot a `start` placed, which reads occupied with the
  pane's id and pid, and `dead_status` as the pane's exit status.
- `show`: `ok`, `try-again` (raises `TryAgain`) or `error` (raises `TmuxError`).
- `start`: `ok`, `refused` (raises `Refused` from `refused`: `reason`, `words`, and
  `holder`, made a `Hold` for `held` and the last launch for any other reason), `try-again`
  or `error`.
- `placed`: the slot a start that names none takes (1).
- `exec`: `started`, `failed` or `unconfirmed`, with `exec_line` and `dead_status`
  (null while the pane lives).
- `run`: true to really run the command, detached, with the env and cwd it was given; its
  pid is the pane's. Otherwise the pane's pid is `pane_pid` (4343).
- `frames`: what each `read_screen` sees in turn, the last repeated: `{"screen": NAME or
  null, "full": true, "dead": false}`, NAME a screen constant's name; the read gives that
  `Screen` itself, as slots does. `dead` is the pane's `pane_dead`. Default: no screen.
- `answers`: what each `answer_screen` gives in turn, the last repeated: `{"outcome": ...,
  "why": ..., "capture": ...}`, `outcome` one of slots' words (`gone`, `ended`,
  `not-ended`, `mismatch`, `ended-without-screen`, `no-pane`); `why` is words for a person.
  Default: `gone`.

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


class Refused(Exception):
    def __init__(self, reason, words, holder=None):
        super().__init__(words)
        self.reason, self.words, self.holder = reason, words, holder


class TryAgain(Exception):
    pass


class TmuxError(Exception):
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
    at: str | None
    label: str | None
    meta: dict | None


@dataclass(frozen=True)
class Slot:
    number: int
    name: str | None
    state: str
    launchable: bool
    hold: Hold | None = None
    label: str | None = None
    last: Last | None = None
    pane_id: str | None = None
    pane_pid: int | None = None
    dead_status: int | None = None


@dataclass(frozen=True)
class Placed:
    slot: int
    pane_id: str
    window_id: str
    exec: str
    exec_line: str | None = None
    dead_status: int | None = None


@dataclass(frozen=True)
class Screen:
    phrases: tuple
    option: str
    marker: str = "❯"
    items: tuple = ()
    move: bool = True
    then: str = "gone"


@dataclass(frozen=True)
class Seen:
    screen: Screen | None
    full: bool = False
    why: str = ""
    rows: tuple = ()
    dead: bool = False


@dataclass(frozen=True)
class Answer:
    outcome: str
    screen: Screen | None = None
    why: str = ""
    seen: object = None
    capture: str | None = None


CLAUDE_TRUST_FOLDER = Screen(("Accessing workspace:", "Enter to confirm"),
                             "Yes, I trust this folder")
CLAUDE_DEV_CHANNELS = Screen(("Loading development channels", "Enter to confirm"),
                             "I am using this for local development")
CLAUDE_EXIT_DIALOG = Screen(("Background work is running", "will stop when you exit"),
                            "Exit and stop tasks", items=("shell", "monitor"), move=False,
                            then="ended")
SCREENS = {"CLAUDE_TRUST_FOLDER": CLAUDE_TRUST_FOLDER,
           "CLAUDE_DEV_CHANNELS": CLAUDE_DEV_CHANNELS,
           "CLAUDE_EXIT_DIALOG": CLAUDE_EXIT_DIALOG}


def screen_name(screen: Screen) -> str:
    """The constant `screen` is declared under."""
    return next(name for name, known in SCREENS.items() if known is screen)


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
    if how == "try-again":
        raise TryAgain("the slots server is not answering yet")
    if how == "error":
        raise TmuxError("tmux list-panes failed")
    entry = plan.get("slots", {}).get(str(slot))
    if entry is not None:
        hold = Hold(**entry["hold"]) if entry.get("hold") else None
        last = Last(**entry["last"]) if entry.get("last") else None
        return Slot(int(slot) if str(slot).isdigit() else 0, entry.get("name"),
                    entry["state"], entry["launchable"], hold, entry.get("label"), last,
                    entry.get("pane_pid"))
    placed = _turns.placed.get(str(slot))
    if placed is not None:
        return Slot(int(slot), None, "occupied", False, label=placed["label"],
                    last=Last(None, placed["label"], placed["meta"]), pane_id="%7",
                    pane_pid=placed["pid"], dead_status=plan.get("dead_status"))
    return None


def start(slot, argv, *, env, cwd, label, meta=None, hold=None, create=False) -> Placed:
    record("start", slot=slot, argv=list(argv), env=dict(env), cwd=cwd, label=label,
           meta=meta, hold=hold, create=create)
    plan = script()
    how = plan.get("start", "ok")
    if how == "refused":
        given = plan.get("refused", {})
        reason, holder = given.get("reason", "occupied"), given.get("holder")
        if holder is not None:
            holder = Hold(**holder) if reason == "held" else Last(
                holder.get("at"), holder.get("label"), holder.get("meta"))
        raise Refused(reason, given.get("words", "slot taken"), holder)
    if how == "try-again":
        raise TryAgain("the slots lock was not had in 10 s")
    if how == "error":
        raise TmuxError("tmux new-window failed")
    number = slot if slot is not None else plan.get("placed", 1)
    pid = plan.get("pane_pid", 4343)
    if plan.get("run"):
        child = subprocess.Popen(list(argv), env=dict(env), cwd=cwd,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        pid = child.pid
    _turns.placed[str(number)] = {"label": label, "meta": meta, "pid": pid}
    return Placed(number, "%7", "@3", plan.get("exec", "started"), plan.get("exec_line"),
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
                frame.get("why", ""), (), bool(frame.get("dead", False)))


def answer_screen(slot, screen, wait=5) -> Answer:
    record("answer_screen", slot=slot, screen=screen_name(screen), wait=wait)
    answers = script().get("answers") or [{"outcome": "gone"}]
    answer = _nth(answers, _turns.answers)
    _turns.answers += 1
    return Answer(answer["outcome"], screen, answer.get("why", ""), None,
                  answer.get("capture"))
