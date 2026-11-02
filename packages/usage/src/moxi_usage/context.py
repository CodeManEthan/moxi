"""How full a session's context is: `context(session_id)`, read through sessions alone.

sessions finds the session's records (`find`), and for an agent its own file among the
session's subagents (`subagents`); `last_request` reads that source's newest request back
from its end, a synthetic request passed over. On a main session that is the newest
request on its own chain; on an agent's file, the agent's own newest request. Its input
tokens are the whole prompt, so they are the context. The window is the one the harness
reported on that request when it wrote one, else the model table's (`models.context_window`).

This needs no turns: the read is a few blocks from the end of one file.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from moxi_sessions.api import UNKNOWN

#: The harnesses asked, in order, when the caller names none.
HARNESSES = ("claude", "codex", "opencode")
#: How deep `context` looks for an agent among nested subagents (Codex and OpenCode list
#: only a session's direct children).
AGENT_DEPTH = 8


@dataclass(frozen=True)
class Context:
    """One session's or agent's context.

    `tokens` is the newest request's whole prompt, `window` the model's context window,
    `percent` the share of the window used (0-100); each is `UNKNOWN` when it cannot be
    said, and `reason` then says why. `model`, `request_time` (epoch seconds) and `path`
    (the source read) describe what was read, and `harness` is the harness that held
    the session (None when none did)."""

    session_id: str
    harness: str | None
    agent_id: str | None = None
    tokens: Any = UNKNOWN
    window: Any = UNKNOWN
    percent: Any = UNKNOWN
    model: Any = UNKNOWN
    request_time: Any = UNKNOWN
    path: str | None = None
    reason: str | None = None

    @property
    def known(self) -> bool:
        """True when the context's size is known."""
        return self.tokens is not UNKNOWN

    def text(self) -> str:
        """`context 412,330 / 1,000,000 (41%)`, the denominator always printed."""
        if self.tokens is UNKNOWN:
            return f"context unknown: {self.reason}"
        if self.window is UNKNOWN:
            return f"context {self.tokens:,} / window unknown"
        return f"context {self.tokens:,} / {self.window:,} ({self.percent:.0f}%)"

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready: an `UNKNOWN` fact is left out and named in `"unknown"`."""
        out: dict[str, Any] = {"session_id": self.session_id, "harness": self.harness,
                               "agent_id": self.agent_id, "path": self.path}
        unknown = []
        for name in ("tokens", "window", "percent", "model", "request_time"):
            value = getattr(self, name)
            if value is UNKNOWN:
                unknown.append(name)
            else:
                out[name] = value
        if self.reason is not None:
            out["reason"] = self.reason
        out["text"] = self.text()
        if unknown:
            out["unknown"] = unknown
        return out


def _known_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _source(sessions: Any, session_id: str, harness: str | None) -> tuple[Any, str | None, str]:
    """The session's source, the harness that held it, and why there is none."""
    asked = HARNESSES if harness is None else (harness,)
    reasons = []
    for name in asked:
        found = sessions.find(name, session_id)
        hits = [hit for hit in found.hits if not hit.compressed]
        if hits:
            return hits[0].source, name, ""
        if found.hits:
            reasons.append(f"{name} holds it only compressed, which sessions does not read")
            continue
        said = sorted({probe.outcome for probe in found.probes if probe.outcome != "miss"})
        if said:
            reasons.append(f"{name}: {'; '.join(said)}")
    where = harness or ", ".join(HARNESSES)
    why = f"no session {session_id} found in {where}"
    if reasons:
        why += f" ({'; '.join(reasons)})"
    return None, None, why


def _agent(sessions: Any, source: Any, agent_id: str) -> tuple[Any, str]:
    """The agent's own source among the session's subagents, or None and why."""
    seen: set[str] = set()
    level = [source]
    problems: list[str] = []
    for _ in range(AGENT_DEPTH):
        nested = []
        for parent in level:
            listing = sessions.subagents(parent)
            if listing.state in ("unreadable", "unsupported"):
                problems.append(f"{listing.state}: {listing.reason}")
                continue
            for entry in listing:
                if entry.agent_id == agent_id:
                    return entry, ""
                if entry.agent_id not in seen:
                    seen.add(entry.agent_id)
                    nested.append(entry)
        if not nested:
            break
        level = nested
    why = f"no agent {agent_id} found among the session's subagents"
    if problems:
        why += f" ({'; '.join(problems)})"
    return None, why


def context(session_id: str, *, harness: str | None = None, agent_id: str | None = None,
            env: Mapping[str, str] | None = None) -> Context:
    """The context of `session_id` (or of its agent `agent_id`), from its newest request.

    `harness` is `claude`, `codex` or `opencode`; None asks each in that order. A session
    or agent that cannot be found or read, or a source with no request yet, gives a
    `Context` whose `tokens` is `UNKNOWN`, with the reason. Raises
    `moxi.config.ConfigError` when sessions' or usage's config cannot be read."""
    from moxi_sessions import api as sessions

    from .models import context_window

    if harness is not None and harness not in HARNESSES:
        raise ValueError(f"{harness!r} is not a harness ({', '.join(HARNESSES)})")
    source, held_by, why = _source(sessions, session_id, harness)
    if source is None:
        return Context(session_id, None, agent_id, reason=why)
    if agent_id is not None:
        source, why = _agent(sessions, source, agent_id)
        if source is None:
            return Context(session_id, held_by, agent_id, reason=why)
    path = getattr(getattr(source, "source", source), "path", None)
    try:
        last = sessions.last_request(source)
    except (sessions.Unreadable, OSError) as exc:
        return Context(session_id, held_by, agent_id, path=path,
                       reason=f"the session's records cannot be read: {exc}")
    request = last.request
    if request is None:
        said = ("the read reached its limit before a request" if last.ended == "limit"
                else "the session holds no request yet")
        return Context(session_id, held_by, agent_id, path=path, reason=said)
    model = _known_text(request.model) or _known_text(request.response_model)
    tokens = request.input_tokens
    if not isinstance(tokens, int) or isinstance(tokens, bool):
        return Context(session_id, held_by, agent_id, model=model or UNKNOWN,
                       request_time=request.time, path=path,
                       reason="the newest request does not say its input tokens")
    window = context_window(model or UNKNOWN, request.context_window,
                            provider=request.provider, env=env)
    percent = UNKNOWN
    reason = None
    if window is UNKNOWN:
        reason = (f"the model table gives no window for {model}" if model
                  else "the newest request names no model, so its window is unknown")
    else:
        percent = 100.0 * tokens / window
    return Context(session_id, held_by, agent_id, tokens=tokens, window=window,
                   percent=percent, model=model or UNKNOWN, request_time=request.time,
                   path=path, reason=reason)


__all__ = ["Context", "context", "HARNESSES"]
