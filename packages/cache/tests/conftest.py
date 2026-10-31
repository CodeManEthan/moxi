"""Shared fixtures for cache's suite. Every record here is synthetic, built from the shapes
Claude Code, Codex and OpenCode write; none is cut from a real record. Claude transcripts
and Codex rollouts are written through sessions' builders (`moxi_sessions.api.testing`).

Every test runs inside the kit's fence (the package's root `conftest.py`): HOME, the XDG
homes (`XDG_CONFIG_HOME` is where systemd user units live), Claude Code's config
directory and every module's home are the test's own, so no test reads or writes the
files of the user running it. On top of that, cache's home and sessions' config root are
directories a test can name (`own_roots`), and the lifetime table is read from a copy of
the shipped one.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from moxi_cache import table, timer
from moxi_sessions.api import testing

HOUR = 3600


@pytest.fixture(autouse=True)
def own_roots(tmp_path, monkeypatch) -> Path:
    """cache's five roots under one directory and sessions' config root empty, both in
    tmp where a test can write a config file (`write_cache_config`,
    `write_sessions_config`). Returns cache's home."""
    home = tmp_path / "cache-home"
    home.mkdir()
    monkeypatch.setenv("MOXI_CACHE_HOME", str(home))
    sessions_config = tmp_path / "sessions-config"
    sessions_config.mkdir()
    monkeypatch.setenv("MOXI_SESSIONS_CONFIG_HOME", str(sessions_config))
    return home


def claude_config() -> Path:
    """Claude Code's config directory, as the fence set it for this test."""
    return Path(os.environ["CLAUDE_CONFIG_DIR"])


@pytest.fixture
def installed_modules() -> None:
    """The installed modules' timer subscriptions, as a real install finds them: a test
    that asks for this fixture reads the real scan, which `no_module_subscriptions` then
    leaves alone."""


@pytest.fixture(autouse=True)
def no_module_subscriptions(request, monkeypatch) -> None:
    """No module installed beside cache declares a timer subscription here, so what the
    watcher, `timers` and `doctor` find is the test's own config. A test that reads
    modules' subscriptions hands `from_modules` its entry points, or asks for
    `installed_modules` to read the real scan."""
    if "installed_modules" in request.fixturenames:
        return
    real = timer.from_modules

    def from_modules(entry_points=None, find=None):
        return real([] if entry_points is None else entry_points, find)

    monkeypatch.setattr(timer, "from_modules", from_modules)


@pytest.fixture(autouse=True)
def lifetime_table(monkeypatch, tmp_path_factory) -> Path:
    """The lifetime table at a path of the test's own: a copy of the shipped table. A
    test that needs another table writes it to `table.TABLE_PATH`."""
    path = tmp_path_factory.mktemp("table") / "lifetimes.toml"
    path.write_text(Path(table.__file__).with_name("lifetimes.toml").read_text())
    monkeypatch.setattr(table, "TABLE_PATH", path)
    return path


def write_cache_config(home: Path, text: str) -> Path:
    """cache's config file under the test's cache home."""
    path = home / "config" / "cache" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_sessions_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sessions-config" / "sessions" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- Claude transcripts ---------------------------------------------------------------
#
# Transcripts come from sessions' `ClaudeTranscript`, one builder per transcript: the
# builder numbers each request's `requestId`, and two builders would give two requests the
# same id, which the backward read takes for one request.

ClaudeTranscript = testing.ClaudeTranscript
usage = testing.usage


def stamp(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


#: The usage block each `tier` of `assistant_line` writes: the cache write, and the part of
#: it written for an hour.
TIERS = {
    "1h": dict(cache_write=500, cache_write_1h=500),
    "5m": dict(cache_write=500),
    "both": dict(cache_write=1000, cache_write_1h=500),
    "none": dict(cache_write=0),
    None: dict(cache_write=0, breakdown=False),
}


def assistant_line(age: float, *, now: float | None = None, tier: str | None = None,
                   without: tuple[str, ...] = (), **fields):
    """One request that meets the clock's rule, `age` seconds old, as a step that adds it
    to a builder. `tier` is `1h`, `5m`, `both`, `none` (a `cache_creation` that wrote
    nothing) or None (no `cache_creation` at all). `fields` are set on the line, and the
    keys named in `without` are taken off it."""
    now = time.time() if now is None else now
    block = usage(**TIERS[tier])
    fields = {"timestamp": stamp(now - age), **fields}

    def add(t: ClaudeTranscript) -> int:
        number = t.request(usage_block=block).text(**fields)
        if without:
            line = json.loads(t.lines[number - 1])
            for key in without:
                line.pop(key, None)
            t.lines[number - 1] = json.dumps(line)
        return number

    return add


def user_line():
    """A user prompt, as a step that adds it to a builder."""
    return lambda t: t.user("hello")


def transcript(session_id: str, *, root: Path, project: str = "proj") -> ClaudeTranscript:
    """A builder for `<root>/<project>/<session_id>.jsonl`."""
    return ClaudeTranscript(root, session_id=session_id, project=project)


def add_lines(t: ClaudeTranscript, lines: list) -> ClaudeTranscript:
    """Add `lines` to `t`: a step (`assistant_line`, `user_line`) is applied to it, a dict
    is added as it is, and a string is written as one line as it is."""
    for line in lines:
        if callable(line):
            line(t)
        elif isinstance(line, str):
            t.add_raw(line)
        else:
            t.add(line)
    return t


def write_transcript(session_id: str, age: float | None = 0, *, root: Path,
                     lines: list | None = None, project: str = "proj",
                     newline_at_end: bool = True) -> Path:
    """`<root>/<project>/<session_id>.jsonl`. With `lines`, exactly those (see
    `add_lines`); otherwise one user line and, unless `age` is None, one request `age`
    seconds old."""
    if lines is None:
        lines = [user_line()]
        if age is not None:
            lines.append(assistant_line(age))
    t = add_lines(transcript(session_id, root=Path(root), project=project), lines)
    return t.write(terminated=newline_at_end)


# --- Codex rollouts -------------------------------------------------------------------
#
# Rollouts come from sessions' `CodexRollout`, one builder per rollout file.

CodexRollout = testing.CodexRollout
token_usage = testing.token_usage
