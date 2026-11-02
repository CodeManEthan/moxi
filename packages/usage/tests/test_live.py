"""Live parity: this machine's per-day tokens from usage's `daily`, against a reference.

A `live` test, run by hand: `MOXI_TEST_LIVE=1` and `MOXI_USAGE_PARITY_REFERENCE=<file>`
in the environment pytest starts in, then `pytest -m live -s`. Without the reference it
is skipped. It reads the real harness roots, the ones the harness rules give in that
environment (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `XDG_DATA_HOME`, `HOME`), and nothing
else of the machine: usage's index and every other write stay in the test's tmp, under
the fence.

The reference is a JSON file made outside this repository by another counter of the same
corpus:

    {"tz": "UTC",                  # the zone its days are cut in
     "harnesses": ["claude", "codex"],
     "through": 1791318903.7,      # its newest request; that day and later are not compared
     "days": {"2026-07-01": {"claude": {"requests": 812, "fresh_input": 9120,
                                        "cache_read": 41000000, "cache_write": 1900000,
                                        "output": 310000},
                             "codex": {...}}}}

`fresh_input` is the prompt less what was read from or written to the cache. Every day
from the reference's first up to the day of `through` is compared, by harness and token
class. `harnesses` (optional; every harness when absent) names the ones the reference
counted: a harness it never read is shown, not compared. The test prints each day that
differs, with the difference (usage less the reference), and fails when any does.

The Claude limits reading through turns: one real Claude Code session, run under turns'
runner and gate plugins (the folders and endpoint command `launch_additions` gives, with
every other module's contribution refused), asked one short turn on the small model. At
the turn's end the runner reads the session's usage and posts it; turns' endpoint, a
child of that session, hands it to usage's `usage` listener, which stores the rate-limit
windows as `session-usage` readings. The test waits for them in the test's own store.
It needs `claude` on PATH, signed in to a subscription (a session off one reports no
windows), and turns installed; it is skipped without either. Claude Code runs with the
real home, so it finds its sign-in, and with `--setting-sources project`, so the user's
own settings, hooks and plugins stay out; it keeps its record of the session there, as of
any other, and answering its folder-trust screen for the test's work folder is recorded
there too. Every Moxi home, the endpoint's among them, stays in the test's tmp, so no
service or unit of the machine is reached. It costs one short turn.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import signal
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from moxi import modules
from moxi.paths import Layout
from moxi_sessions import api as sessions
from moxi_usage import index, readings, reports, store

pytestmark = pytest.mark.live

#: The variable naming the reference file.
REFERENCE_ENV = "MOXI_USAGE_PARITY_REFERENCE"
CLASSES = ("requests", "fresh_input", "cache_read", "cache_write", "output")
HARNESSES = ("claude", "codex", "opencode")

#: The environment pytest started in, read at import, before any test's fence empties it.
_STARTED_IN = dict(os.environ)


def _day(epoch: float, zone: dt.tzinfo) -> str:
    return dt.datetime.fromtimestamp(epoch, zone).strftime("%Y-%m-%d")


def _days(first: str, stop: str) -> list[str]:
    day, end, out = dt.date.fromisoformat(first), dt.date.fromisoformat(stop), []
    while day < end:
        out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def test_per_day_tokens_match_the_reference_on_this_machine(fence):
    reference_path = _STARTED_IN.get(REFERENCE_ENV)
    if not reference_path:
        pytest.skip(f"{REFERENCE_ENV} names no reference file")
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    tz = reference.get("tz", "UTC")
    zone = dt.timezone.utc if tz.upper() == "UTC" else ZoneInfo(tz)
    roots = {h: sessions.root(h, _STARTED_IN) for h in HARNESSES}
    if not any(path.exists() for path in roots.values()):
        pytest.skip("no harness root on this machine")
    config = Layout("sessions").config_file()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("".join(f'[{h}]\n{key} = {json.dumps(str(roots[h]))}\n'
                              for h, key in (("claude", "projects"), ("codex", "sessions"),
                                             ("opencode", "database"))), encoding="utf-8")

    started = time.perf_counter()
    result = index.pass_(projects=None)
    took = time.perf_counter() - started
    print(f"\npass over the real roots: {took:.1f} s, read {result.read}, excluded "
          f"{result.excluded}, held {result.held}, unreadable {len(result.unreadable)}")
    assert store.index_path().is_relative_to(fence.short)

    first = min(reference["days"])
    stop = _day(float(reference["through"]), zone)
    days = _days(first, stop)
    conn = store.connect(create=False)
    try:
        ours: dict[str, dict[str, dict[str, int]]] = {}
        for harness in HARNESSES:
            report = reports.daily(conn, since=first, until=days[-1], harness=harness, tz=tz)
            for row in report.rows:
                cell = ours.setdefault(row["day"], {}).setdefault(
                    harness, dict.fromkeys(CLASSES, 0))
                for name in CLASSES:
                    cell[name] += row[name] or 0
    finally:
        conn.close()

    counted = tuple(reference.get("harnesses", HARNESSES))
    differ: list[str] = []
    totals = {h: dict.fromkeys(CLASSES, 0) for h in HARNESSES}
    deltas = {h: dict.fromkeys(CLASSES, 0) for h in HARNESSES}
    for day in days:
        theirs = reference["days"].get(day, {})
        for harness in HARNESSES:
            mine = ours.get(day, {}).get(harness, dict.fromkeys(CLASSES, 0))
            other = theirs.get(harness, {})
            delta = {name: mine[name] - other.get(name, 0) for name in CLASSES}
            for name in CLASSES:
                totals[harness][name] += mine[name]
                deltas[harness][name] += delta[name]
            if harness in counted and any(delta.values()):
                differ.append(f"{day} {harness:<8} " + " ".join(
                    f"{name} {delta[name]:+,} ({other.get(name, 0):,})" for name in CLASSES))
    print(f"compared {len(days)} days, {days[0]} to {days[-1]} ({tz}); "
          f"{len(differ)} day-harness cells differ")
    for harness in HARNESSES:
        what = "less the reference" if harness in counted else "(not in the reference)"
        mine = " ".join(f"{n} {v:,}" for n, v in totals[harness].items())
        delta = " ".join(f"{n} {v:+,}" for n, v in deltas[harness].items())
        print(f"{harness:<8} usage {mine}; {what} {delta}")
    print("\n".join(differ))
    assert not differ


# --- the Claude limits reading through turns --------------------------------------------

#: `claude` as the environment pytest started in finds it.
CLAUDE = shutil.which("claude", path=_STARTED_IN.get("PATH"))
#: How long the session gets to answer its turn and the reading to be stored.
READING_S = 240.0
#: The one turn: a word, so the turn is short.
PROMPT = "Reply with the single word ready, and nothing else."
#: The variables that point a harness's home or Moxi's away from the user's: the fence
#: points them into the test's tmp, and Claude needs the real home.
HOMES = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
         "XDG_STATE_HOME", "XDG_CACHE_HOME")
#: Claude Code's folder-trust screen, with the spaces taken out (the terminal draws them
#: as cursor moves): its question, its option and its last line.
TRUST_SCREEN = ("Accessingworkspace:", "Yes,Itrustthisfolder", "Entertoconfirm")
_ESCAPES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


class Terminal:
    """`argv` in a pseudo-terminal of its own, which the test reads and types into."""

    def __init__(self, argv: list[str], env: dict[str, str], cwd: Path):
        import fcntl
        import pty
        import struct
        import termios

        self.out = bytearray()
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # the child: become the command
            try:
                os.chdir(cwd)
                os.execve(argv[0], argv, env)
            finally:
                os._exit(127)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            self.out += data

    def text(self) -> str:
        return _ESCAPES.sub("", bytes(self.out).decode(errors="replace"))

    def press(self, keys: bytes) -> None:
        os.write(self.fd, keys)

    def ended(self) -> bool:
        try:
            done, _status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return True
        return done != 0

    def close(self) -> None:
        try:
            os.killpg(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass
        os.close(self.fd)


def _stop_marked(marker: str) -> list[int]:
    """Stop every process of this user whose environment holds `marker` (the endpoint,
    and anything else the session started that outlived it). Returns their pids."""
    stopped = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            with open(f"/proc/{entry}/environ", "rb") as fh:
                held = fh.read()
        except OSError:
            continue
        if marker.encode() in held:
            try:
                os.kill(int(entry), signal.SIGTERM)
                stopped.append(int(entry))
            except OSError:
                pass
    return stopped


def _session_usage(session_id: str) -> list[dict]:
    path = readings.path()
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM readings WHERE source = ? AND session_id = ? ORDER BY id",
            (readings.SESSION_USAGE, session_id))]
    except sqlite3.OperationalError:
        return []                         # the store is being made
    finally:
        conn.close()


def test_a_claude_sessions_usage_through_turns_is_stored_as_a_session_usage_reading(
        fence, tmp_path):
    if CLAUDE is None:
        pytest.skip("claude is not on PATH")
    if "HOME" not in _STARTED_IN:
        pytest.skip("no HOME to find Claude's sign-in in")
    turns = modules.load("turns")
    if not turns.ok or turns.api is None:
        pytest.skip(f"needs turns, which is {turns.state}")
    work = tmp_path / "work"
    work.mkdir()
    moxi_home = fence.short / "moxi"
    moxi_home.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in HOMES}
    env.update({k: _STARTED_IN[k] for k in ("HOME", "USER", "LOGNAME", "CLAUDE_CONFIG_DIR")
                if k in _STARTED_IN})
    env.update({"MOXI_HOME": str(moxi_home), "TERM": "xterm-256color", "LANG": "C.UTF-8"})
    session_id = str(uuid.uuid4())
    added = turns.api.launch_additions(
        "claude", cwd=work, env=env, session_id=session_id,
        refuse=lambda fragment: "this test runs turns' own additions alone")
    env.update(added.env)
    argv = [CLAUDE, *added.argv, "--session-id", session_id, "--setting-sources",
            "project", "--model", "haiku", PROMPT]
    assert readings.path().is_relative_to(fence.short)
    started = time.time()
    term = Terminal(argv, env, work)
    trusted = False
    found: list[dict] = []
    try:
        deadline = time.monotonic() + READING_S
        while time.monotonic() < deadline and not found:
            if not trusted and all(p in "".join(term.text().split()) for p in TRUST_SCREEN):
                time.sleep(1.0)           # a key pressed before Claude reads keys is lost
                term.press(b"\x1b[B")
                time.sleep(0.5)
                term.press(b"\r")
                trusted = True
            assert not term.ended(), f"claude ended: {term.text()[-2000:]}"
            found = _session_usage(session_id)
            time.sleep(0.5)
    finally:
        term.close()
        left = _stop_marked(str(moxi_home))
    print(f"\nsession {session_id}: {len(found)} session-usage readings "
          f"(processes stopped after: {len(left)})")
    for row in found:
        print("  " + ", ".join(f"{k} {row[k]}" for k in (
            "limit_id", "window_minutes", "used_percent", "resets_at", "observed_at")))
    turns_state = Layout("turns").state_dir()
    assert found, (f"no session-usage reading in {READING_S:g} s; the screen ended: "
                   f"{term.text()[-1500:]}; turns' state: "
                   f"{sorted(str(p) for p in turns_state.rglob('*'))[:40]}")
    assert all(row["harness"] == "claude" for row in found)
    assert all(started - 5 <= row["observed_at"] <= time.time() + 5 for row in found)
    assert any(row["used_percent"] is not None for row in found)
