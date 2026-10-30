"""Synthetic fixtures for sessions' suite, built from the shapes Claude Code and Codex
write. No line here is cut from a real record.

Every test runs with sessions' config root pointed at a directory of its own, so no test
reads the config file of the user running it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def own_config(tmp_path, monkeypatch) -> Path:
    """sessions' config root, empty, for every test. Write `config.toml` into
    `<own_config>/sessions/` to set a key."""
    root = tmp_path / "config-home"
    root.mkdir()
    monkeypatch.setenv("MOXI_SESSIONS_CONFIG_HOME", str(root))
    return root


def write_config(own_config: Path, text: str) -> Path:
    directory = own_config / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


# --- Claude transcripts ---------------------------------------------------------------

def assistant_line(*, request_id: str = "req_A", uuid: str = "a1",
                   ts: str = "2026-08-12T11:00:00.000Z", model: str = "claude-model-x",
                   sidechain: bool = False, session_id: str = "sess",
                   usage: dict | None = None) -> str:
    """One `assistant` line in the shape Claude Code writes."""
    return json.dumps({
        "parentUuid": None,
        "isSidechain": sidechain,
        "message": {
            "model": model,
            "id": f"msg_{uuid}",
            "type": "message",
            "role": "assistant",
            "stop_reason": "end_turn",
            "usage": usage if usage is not None else {
                "input_tokens": 2,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 10,
                "output_tokens": 100,
            },
        },
        "requestId": request_id,
        "type": "assistant",
        "uuid": uuid,
        "timestamp": ts,
        "sessionId": session_id,
        "cwd": "/work/project",
    })


def user_line(*, uuid: str = "u1", ts: str = "2026-08-12T11:00:00.000Z",
              session_id: str = "sess") -> str:
    return json.dumps({"type": "user", "uuid": uuid, "timestamp": ts,
                       "isSidechain": False, "sessionId": session_id,
                       "message": {"role": "user", "content": "hi"}})


def write_transcript(projects_root: Path, project: str, session_id: str,
                     lines: list[str], *, newline_at_end: bool = True) -> Path:
    directory = projects_root / project
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.jsonl"
    path.write_text("\n".join(lines) + ("\n" if newline_at_end else ""), encoding="utf-8")
    return path


@pytest.fixture
def projects(tmp_path) -> Path:
    """A projects root the test owns and passes in."""
    root = tmp_path / "projects"
    root.mkdir()
    return root


# --- Codex rollouts -------------------------------------------------------------------

THREAD = "0190a000-0000-7000-8000-000000000001"
THREAD_B = "0190a000-0000-7000-8000-000000000002"
ROLLOUT_ID = "0190a000-1111-7000-8000-000000000001"


def rollout_lines(thread: str = THREAD) -> list[str]:
    """A short rollout: line 1's `session_meta`, a turn, its usage record."""
    stamp = "2026-10-06T10:00:00.000Z"
    return [
        json.dumps({"timestamp": stamp, "type": "session_meta",
                    "payload": {"id": thread, "session_id": thread,
                                "history_mode": "paginated", "cli_version": "0.0.0"}}),
        json.dumps({"timestamp": stamp, "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "t1"}}),
        json.dumps({"timestamp": stamp, "type": "turn_context",
                    "payload": {"turn_id": "t1", "model": "model-x"}}),
        json.dumps({"timestamp": stamp, "type": "token_usage_record",
                    "payload": {"thread_id": thread, "session_id": thread,
                                "turn_id": "t1", "usage": {"input_tokens": 100}}}),
    ]


def write_rollout(sessions_root: Path, thread: str = THREAD, *, day: str = "2026/10/06",
                  stamp: str = "2026-10-06T10-00-00", suffix: str = "",
                  zst: bool = False, lines: list[str] | None = None) -> Path:
    directory = sessions_root / day
    directory.mkdir(parents=True, exist_ok=True)
    name = f"rollout-{stamp}-{thread}{suffix}.jsonl" + (".zst" if zst else "")
    path = directory / name
    body = "\n".join(lines if lines is not None else rollout_lines(thread)) + "\n"
    path.write_bytes(body.encode("utf-8"))
    return path


@pytest.fixture
def sessions(tmp_path) -> Path:
    root = tmp_path / "codex-sessions"
    root.mkdir()
    return root
