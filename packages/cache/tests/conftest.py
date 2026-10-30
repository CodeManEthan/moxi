"""Shared fixtures for cache's suite. Every record here is synthetic, built from the shapes
Claude Code, Codex and OpenCode write; none is cut from a real record.

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

from moxi_cache import table

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

def stamp(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def assistant_line(age: float, *, now: float | None = None, tier: str | None = None,
                   **extra) -> dict:
    """One transcript line that meets the clock's rule, `age` seconds old. `tier` is
    `1h`, `5m`, `both`, `none` (a `cache_creation` that wrote nothing) or None (no
    `cache_creation` at all)."""
    now = time.time() if now is None else now
    usage = {"input_tokens": 1, "output_tokens": 1}
    if tier is not None:
        usage["cache_creation"] = {
            "ephemeral_1h_input_tokens": 500 if tier in ("1h", "both") else 0,
            "ephemeral_5m_input_tokens": 500 if tier in ("5m", "both") else 0}
    line = {"type": "assistant", "timestamp": stamp(now - age), "sessionId": "s",
            "message": {"model": "claude-model-x", "role": "assistant", "usage": usage}}
    line.update(extra)
    return line


def user_line() -> dict:
    return {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
            "message": {"role": "user", "content": "hello"}}


def write_transcript(session_id: str, age: float | None = 0, *, root: Path,
                     lines: list | None = None, project: str = "proj",
                     newline_at_end: bool = True) -> Path:
    """`<root>/<project>/<session_id>.jsonl`. With `lines`, exactly those (dicts are
    dumped, strings written as they are); otherwise one user line and, unless `age` is
    None, one assistant line `age` seconds old."""
    path = Path(root) / project / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if lines is None:
        lines = [user_line()]
        if age is not None:
            lines.append(assistant_line(age))
    body = "\n".join(ln if isinstance(ln, str) else json.dumps(ln) for ln in lines)
    if lines and newline_at_end:
        body += "\n"
    path.write_text(body)
    return path
