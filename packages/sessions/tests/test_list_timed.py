"""The list's time targets, over roots written with the builders: 5,000 Claude Code
sessions, and eight long ones that grow between two lists. `slow` tests: a plain pytest
run leaves them out; `pytest -m slow` runs them. They are also `timed`, so `moxi-dev
check` runs them alone, after its parallel pass.

Each test prints what it measured, so `pytest -m slow -s` shows the numbers.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from moxi_sessions import catalog as catalog_module
from moxi_sessions.catalog import Catalog
from moxi_sessions.testing import ClaudeTranscript, OpenCodeDb

from test_page import read

pytestmark = [pytest.mark.slow, pytest.mark.timed]

DAY = 86400.0
SESSIONS = 5000
LATE = 300           # how many of them changed in the last seven days
FOLDERS = 40


def sid(n: int) -> str:
    return f"0190b000-0000-7000-8000-{n:012x}"


def seed_root(home: Path, now: float, *, sessions: int = SESSIONS, late: int = LATE
              ) -> dict[str, Path]:
    """`sessions` small Claude Code sessions under `home`, across `FOLDERS` folders:
    `late` of them changed in the last seven days (ten in the last hour, the rest over
    the six days before it and so on seven calendar days at most, every second one
    started by a program), the others spread over the 400 days before those. Codex's
    and OpenCode's roots are there and empty. Returns the three roots."""
    projects = home / "claude-home" / "projects"
    (home / "codex-home" / "sessions").mkdir(parents=True)
    OpenCodeDb(home / "data" / "opencode").write()
    for n in range(sessions):
        if n < 10:
            at, program = now - 60 * (n + 1), False
        elif n < late:
            at, program = now - 3600 - (n - 10) * 6 * DAY / (late - 10), n % 2 == 0
        else:
            at, program = now - 8 * DAY - (n - late) * 400 * DAY / (sessions - late), False
        folder = f"proj-{n % FOLDERS}"
        t = ClaudeTranscript(projects, session_id=sid(n), cwd=f"/work/{folder}",
                             project=f"-work-{folder}", start=at - 120,
                             entrypoint="sdk-cli" if program else "cli")
        if n % 3 == 0:
            t.title(f"task {n}")
        t.user(f"Do task {n}.", origin=None if program else "human")
        t.assistant(f"Task {n} is done.")
        stamp = int(at * 1_000_000_000)
        os.utime(t.write(), ns=(stamp, stamp))
    return {"claude": projects, "codex": home / "codex-home" / "sessions",
            "opencode": home / "data" / "opencode"}


@pytest.fixture(scope="module")
def big_home(tmp_path_factory) -> Path:
    home = tmp_path_factory.mktemp("big")
    seed_root(home, time.time())
    return home


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_the_list_answers_5000_sessions_in_3_s_cold_and_1_s_once_read(big_home,
                                                                      monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(big_home / "claude-home"))
    monkeypatch.setenv("CODEX_HOME", str(big_home / "codex-home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(big_home / "data"))
    clock = Clock()
    cat = Catalog(clock=clock)
    monkeypatch.setattr(catalog_module, "_shared", cat)

    start = time.perf_counter()
    cold = read("/api/list")
    cold_s = time.perf_counter() - start
    heads, tails, prompts = cat.head_reads, cat.tail_reads, cat.prompt_reads

    clock.now += catalog_module.REUSE_S + 1          # the gather's reuse has lapsed
    start = time.perf_counter()
    again = read("/api/list")
    again_s = time.perf_counter() - start

    print(f"\n/api/list over {SESSIONS} sessions: cold {cold_s * 1000:.0f} ms "
          f"({heads} heads, {tails} last turns read, {prompts} sessions read through "
          f"for a prompt); again after the reuse lapsed {again_s * 1000:.0f} ms "
          f"({cat.head_reads - heads} heads, {cat.tail_reads - tails} last turns, "
          f"{cat.prompt_reads - prompts} sessions read through)")
    assert cold["all"] == cold["total"] == SESSIONS
    assert [g["key"] for g in cold["groups"]][0] == "recent"
    assert cold["groups"][0]["count"] == 10
    late = sum(g["count"] + g.get("program", {}).get("count", 0)
               for g in cold["groups"][:-1])
    assert late == LATE and cold["groups"][-1]["count"] == SESSIONS - LATE
    assert heads <= LATE + 8 and tails <= 10 + 8 * 8
    assert (cat.head_reads, cat.tail_reads) == (heads, tails)
    assert 0 < prompts <= tails and cat.prompt_reads == prompts
    assert again["total"] == SESSIONS
    assert cold_s < 3.0
    assert again_s < 1.0


# --- long sessions that grow between two lists -----------------------------------------------

LONG = 8                     # how many long sessions
LONG_BYTES = 16 * 1024 * 1024
GROWTH = 100 * 1024


def working_lines(t: ClaudeTranscript, size: int, rng) -> bytes:
    """`size` bytes or a little more (20 KiB at most) of the lines a session at work
    writes, none of them typed: a task's notice, then requests that think, say something
    and call a tool, and each tool's result, a few hundred bytes to 19 KiB."""
    start = len(t.lines)
    written = 0
    while written < size:
        before = len(t.lines)
        t.user("<task-notification>a task finished</task-notification>",
               origin="task-notification")
        for _ in range(rng.randrange(2, 9)):
            request = t.request()
            request.thinking("What to read next. " * rng.randrange(1, 20))
            request.text("Reading the next file. " * rng.randrange(1, 6))
            call = request.tool_use("Read", {"file_path": "/work/proj-a/notes.md"})
            t.tool_result(call, "a line of the file\n" * rng.randrange(10, 1000))
            written += sum(len(line.encode()) + 1 for line in t.lines[before:])
            before = len(t.lines)
            if written >= size:
                break
        t.turn_duration()
    return ("\n".join(t.lines[start:]) + "\n").encode()


def test_eight_long_sessions_that_grew_are_listed_in_1_s_from_their_new_bytes(
        tmp_path, monkeypatch):
    import random
    rng = random.Random(16)
    now = time.time()
    projects = tmp_path / "claude-home" / "projects"
    (tmp_path / "codex-home" / "sessions").mkdir(parents=True)
    OpenCodeDb(tmp_path / "data" / "opencode").write()
    long = []
    for n in range(LONG):
        t = ClaudeTranscript(projects, session_id=sid(n), cwd="/work/proj-a",
                             project="-work-proj-a", start=now - 7200)
        body = working_lines(t, LONG_BYTES // 16, rng)
        t.path.parent.mkdir(parents=True, exist_ok=True)
        with open(t.path, "wb") as handle:
            for _ in range(-(-LONG_BYTES // len(body))):
                handle.write(body)
        stamp = int((now - 60 * (n + 2)) * 1_000_000_000)
        os.utime(t.path, ns=(stamp, stamp))
        long.append(t)
    sizes = [t.path.stat().st_size for t in long]
    assert min(sizes) >= LONG_BYTES
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    clock = Clock()
    cat = Catalog(clock=clock)
    monkeypatch.setattr(catalog_module, "_shared", cat)

    start = time.perf_counter()
    first = read("/api/list")
    first_s = time.perf_counter() - start
    assert [row["prompt"] for row in first["groups"][0]["rows"]] == [None] * LONG
    assert (cat.prompt_reads, cat.prompt_whole, cat.prompt_bytes) == (
        LONG, LONG, sum(sizes))

    grown = 0
    for n, t in enumerate(long):
        more = working_lines(t, GROWTH, rng)
        with open(t.path, "ab") as handle:
            handle.write(more)
        grown += len(more)
        stamp = int((now - n) * 1_000_000_000)
        os.utime(t.path, ns=(stamp, stamp))
    clock.now += catalog_module.REUSE_S + 1          # the gather's reuse has lapsed
    tails = cat.tail_reads
    start = time.perf_counter()
    again = read("/api/list")
    again_s = time.perf_counter() - start

    print(f"\n/api/list over {LONG} sessions of {min(sizes) / 1048576:.1f} MiB or more: "
          f"first {first_s * 1000:.0f} ms ({LONG} read whole, "
          f"{sum(sizes) / 1048576:.0f} MiB); after each grew by about "
          f"{GROWTH // 1024} KiB and the reuse lapsed {again_s * 1000:.0f} ms "
          f"({cat.prompt_reads - LONG} read for a prompt, {cat.prompt_whole - LONG} of "
          f"them whole, {(cat.prompt_bytes - sum(sizes)) / 1024:.0f} KiB read through, "
          f"{cat.tail_reads - tails} last turns)")
    assert [row["prompt"] for row in again["groups"][0]["rows"]] == [None] * LONG
    assert cat.tail_reads - tails == LONG            # every one of them had changed
    assert cat.prompt_reads == 2 * LONG
    assert cat.prompt_whole == LONG                  # no file was read whole again
    assert cat.prompt_bytes - sum(sizes) == grown    # only the bytes each one grew by
    assert LONG * GROWTH <= grown < LONG * GROWTH * 1.25
    assert again_s < 1.0
