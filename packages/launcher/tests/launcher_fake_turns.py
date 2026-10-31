"""A stand-in for turns' API, as the launcher calls it: `launch_additions` and
`codex_host_available` for the composer, and for the first-turn sender `wait_ready`, a
`Session` with a plain `deliver(text, ref=)`, and the `Delivery` whose `wait_started`
gives `Started`, `Refused` or `Lost`, or raises `TimeoutError`.

The launcher reaches it through its test seam, `MOXI_LAUNCHER_TURNS=launcher_fake_turns`.
What it does is scripted by `script.json` in the directory `LAUNCHER_FAKE_TURNS` names, read
at each call, and every call it gets is appended to `calls.jsonl` there, so a test reads the
same record whether the sender ran in its own process or in a detached deliverer.

The script's keys, each with its default:

- `codex_host`: true, or false when turns' Codex host can't start here.
- `additions`: `ok`, `error` (raises `TurnsError`, as when turns has no endpoint
  command) or `value` (raises `ValueError`, as for a command turns does not know).
- `ready`: `ok`, `timeout` (raises `TimeoutError`) or `error` (raises `TurnsError`).
- `session_id`: the id the ready `Session` names (a Codex thread id), default the asked id.
- `deliver`: `answered` (the endpoint answers with a delivery id), `unanswered` (the
  request was written and no answer came: a `Delivery` whose `id` is None, as turns'
  library gives one), or `raise`.
- `connection`: for an unanswered hand-in, `open` (the library resolves it by `ref` on the
  same connection) or `closed` (the library connects once more and resolves it there).
- `reconnect`: `ok`, or `refused` (nothing serves any more: `Lost("unanswered")`).
- `started`: the value the delivery settles to, `{"kind": "started" | "refused" | "lost",
  ...}`, or `timeout` (raises `TimeoutError`), or `raise`, or `unknown` (the endpoint holds
  no delivery with that `ref`: `Lost("unknown delivery")`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DIR = "LAUNCHER_FAKE_TURNS"


class TurnsError(Exception):
    pass


@dataclass(frozen=True)
class Started:
    KIND = "started"
    turn_id: str | None
    delivery: str | None = None
    ref: str | None = None


@dataclass(frozen=True)
class Refused:
    KIND = "refused"
    reason: str
    delivery: str | None = None
    ref: str | None = None


@dataclass(frozen=True)
class Lost:
    KIND = "lost"
    reason: str
    delivery: str | None = None
    ref: str | None = None


def _dir() -> Path:
    return Path(os.environ[DIR])


def script() -> dict:
    path = _dir() / "script.json"
    return json.loads(path.read_text()) if path.exists() else {}


def record(call: str, **detail) -> None:
    with open(_dir() / "calls.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps({"call": call, "pid": os.getpid(), **detail}) + "\n")


#: Set by `hide`, so a launch a test runs in a process of its own (`launcher_child`) finds
#: no turns either.
HIDDEN = "LAUNCHER_NO_TURNS"
#: The launcher's seam (`moxi_launcher.first.TURNS_SEAM`), named here so this module
#: imports nothing of the launcher's.
SEAM = "MOXI_LAUNCHER_TURNS"


def hide(monkeypatch) -> None:
    """turns not installed, whatever this environment holds, unless a test fakes it on the
    seam. A launch that reached the installed turns would have it write its Claude plugin's
    endpoint file inside its own package, outside the test's tmp, and would wait on a
    session that never comes."""
    monkeypatch.setenv(HIDDEN, "1")
    hide_here(monkeypatch.setattr)


def hide_here(setattr_=setattr) -> None:
    """Hide the installed turns in this process (`moxi.modules.available` answers False
    for it) while the seam names no fake."""
    from moxi import modules

    real = modules.available

    def available(name: str) -> bool:
        if name == "turns" and not os.environ.get(SEAM):
            return False
        return real(name)

    setattr_(modules, "available", available)


def calls(directory: Path) -> list[dict]:
    path = Path(directory) / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


#: The words `launch_additions` gives a Claude launch's command line.
ADDITIONS = ("--plugin-dir", "/fake/turns/runner")
#: The endpoint command `launch_additions` names in a Claude launch's environment, as
#: turns names its own in `MOXI_TURNS_ENDPOINT`.
ENDPOINT = "/fake/turns/moxi-turns-endpoint"


@dataclass(frozen=True)
class LaunchAdditions:
    harness: str
    argv: tuple
    env: dict
    options: dict
    notes: tuple


def launch_additions(harness, *, cwd, env, session_id=None, commands=()):
    record("launch_additions", harness=harness, cwd=cwd, session_id=session_id,
           commands=list(commands))
    additions = script().get("additions", "ok")
    if additions == "error":
        raise TurnsError("the endpoint file can't be written")
    if additions == "value":
        raise ValueError("unknown command 'exit'")
    return LaunchAdditions(harness, ADDITIONS, {"MOXI_TURNS_COMMANDS": ",".join(commands),
                                                "MOXI_TURNS_ENDPOINT": ENDPOINT}, {}, ())


def codex_host_available():
    """turns' Codex host can start, unless the script's `codex_host` key is false."""
    return bool(script().get("codex_host", True))


def wait_ready(*, session_id=None, pid=None, timeout):
    record("wait_ready", session_id=session_id, pid=pid, timeout=timeout)
    ready = script().get("ready", "ok")
    if ready == "timeout":
        raise TimeoutError(f"no session ready after {timeout} s")
    if ready == "error":
        raise TurnsError("the endpoint's directory can't be read")
    harness = "codex" if pid is not None else "claude"
    return Session(harness, pid or 4242, script().get("session_id", session_id))


class Session:
    def __init__(self, harness, pid, session_id):
        self.harness = harness
        self.pid = pid
        self.session_id = session_id

    def deliver(self, text, *, only_if_idle=False, if_turn_number=None, refuse_draft=False,
                refuse_agents=False, ref=None):
        record("deliver", text=text, ref=ref, only_if_idle=only_if_idle,
               if_turn_number=if_turn_number, refuse_draft=refuse_draft,
               refuse_agents=refuse_agents, session_id=self.session_id)
        how = script().get("deliver", "answered")
        if how == "raise":
            raise TurnsError("the caller socket broke")
        return Delivery("d-1" if how == "answered" else None, ref)


class Delivery:
    def __init__(self, delivery_id, ref):
        self.id = delivery_id
        self.ref = ref

    def wait_started(self, timeout):
        record("wait_started", delivery=self.id, ref=self.ref, timeout=timeout)
        plan = script()
        if self.id is None:
            if plan.get("connection", "open") == "closed":
                record("reconnect", ref=self.ref)
                if plan.get("reconnect", "ok") == "refused":
                    return Lost("unanswered", None, self.ref)
            record("resolve", ref=self.ref)
            if plan.get("started") == "unknown":
                return Lost(f"unknown delivery: {self.ref}", None, self.ref)
            self.id = "d-1"
        settled = plan.get("started", {"kind": "started", "turn_id": "t-1"})
        if settled == "timeout":
            raise TimeoutError(f"delivery {self.id} has not started after {timeout} s")
        if settled == "raise":
            raise TurnsError("the wait's answer could not be read")
        kind = settled["kind"]
        if kind == "started":
            return Started(settled.get("turn_id"), self.id, self.ref)
        if kind == "refused":
            return Refused(settled["reason"], self.id, self.ref)
        return Lost(settled["reason"], self.id, self.ref)
