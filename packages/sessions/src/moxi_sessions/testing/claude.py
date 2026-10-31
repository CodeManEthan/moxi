"""`ClaudeTranscript`: a Claude Code transcript written line by line, as Claude Code 2.1.314
writes one.

    t = ClaudeTranscript(projects_root)
    t.user("read the notes")
    req = t.request(usage=usage(input=3, cache_read=900, output=40))
    req.thinking("which file?")
    call = req.tool_use("Read", {"file_path": "/work/project/notes.md"})
    t.tool_result(call, "the notes")
    t.assistant("done", usage=usage(output=12))
    t.turn_duration()
    path = t.write()

Each content block of a request is its own line with the request's id and usage, as
Claude Code writes them, so a request with three blocks is three lines; a tool result can
fall between them. Every line gets the next time on the builder's clock (`start`, then
`step` seconds apart). Methods that add a line return its 1-based line number;
`tool_use` returns the call id. `offset(n)` is the byte offset where line `n` starts once
written.

A subagent's file comes from `subagent(...)`, which returns a builder for
`<project>/<session>/subagents/agent-<id>.jsonl` (or the workflow layout, under
`subagents/workflows/<workflow>/`) whose lines are sidechain and carry the agent's id,
and which writes its `agent-<id>.meta.json` sidecar with it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moxi.files import atomic_write

#: The session id a builder uses when none is given.
SESSION = "0190b000-0000-7000-8000-00000000c1a0"

#: 2026-10-30T10:00:00Z, the default start of a builder's clock.
START = 1793354400.0

#: The model a builder's requests name when none is given.
MODEL = "claude-model-x"


def usage(*, input: int = 2, cache_read: int = 10, cache_write: int = 0,
          cache_write_1h: int = 0, output: int = 100, thinking: int | None = None,
          service_tier: str | None = "standard", speed: str | None = "standard",
          breakdown: bool = True) -> dict:
    """A `message.usage` block. `cache_write` is the whole cache write and
    `cache_write_1h` the part of it written for an hour; `breakdown=False` leaves out
    the `cache_creation` object that splits it. `thinking` adds
    `output_tokens_details.thinking_tokens`. A None tier or speed is left out."""
    block: dict[str, Any] = {
        "input_tokens": input,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output,
    }
    if service_tier is not None:
        block["service_tier"] = service_tier
    if breakdown:
        block["cache_creation"] = {
            "ephemeral_5m_input_tokens": max(cache_write - cache_write_1h, 0),
            "ephemeral_1h_input_tokens": cache_write_1h,
        }
    if speed is not None:
        block["speed"] = speed
    if thinking is not None:
        block["output_tokens_details"] = {"thinking_tokens": thinking}
    return block


def _stamp(epoch: float) -> str:
    moment = datetime.fromtimestamp(epoch, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


class ClaudeRequest:
    """One request's lines, added to its transcript one content block at a time."""

    def __init__(self, transcript: ClaudeTranscript, *, request_id: str | None,
                 message_id: str, model: str, requested_model: str | None,
                 usage_block: dict | None):
        self.transcript = transcript
        self.request_id = request_id
        self.message_id = message_id
        self.model = model
        self.requested_model = requested_model
        self.usage = usage_block
        self.lines: list[int] = []

    def block(self, block: dict, **fields: Any) -> int:
        """One assistant line holding `block`; `fields` are set on the line itself."""
        t = self.transcript
        line = t._base("assistant")
        if self.requested_model is not None:
            line["requestedModel"] = self.requested_model
        message: dict[str, Any] = {
            "model": self.model, "id": self.message_id, "type": "message",
            "role": "assistant", "content": [block], "stop_reason": None,
            "stop_sequence": None,
        }
        if self.usage is not None:
            message["usage"] = self.usage
        line["message"] = message
        if self.request_id is not None:
            line["requestId"] = self.request_id
        line.update(fields)
        number = t.add(line)
        self.lines.append(number)
        return number

    def text(self, text: str = "Done.", **fields: Any) -> int:
        return self.block({"type": "text", "text": text}, **fields)

    def thinking(self, text: str = "Thinking it over.", **fields: Any) -> int:
        return self.block({"type": "thinking", "thinking": text, "signature": "sig"},
                          **fields)

    def tool_use(self, name: str = "Read", tool_input: dict | None = None, *,
                 call_id: str | None = None, **fields: Any) -> str:
        """One `tool_use` line; returns its call id."""
        t = self.transcript
        call_id = call_id or f"toolu_{t._serial():04d}"
        self.block({"type": "tool_use", "id": call_id, "name": name,
                    "input": tool_input if tool_input is not None else {}}, **fields)
        return call_id


class ClaudeTranscript:
    """A transcript under `root` (a projects root): `<root>/<project>/<session_id>.jsonl`.

    Lines are held until `write` (the whole file) or `flush` (only the lines added since
    the last write). Nothing is read from the environment.
    """

    def __init__(self, root: str | Path, *, session_id: str = SESSION,
                 project: str = "-work-project", cwd: str = "/work/project",
                 version: str = "2.1.314", entrypoint: str = "cli",
                 start: float = START, step: float = 1.0, path: str | Path | None = None,
                 agent_id: str | None = None):
        self.root = Path(root)
        self.session_id = session_id
        self.project = project
        self.cwd = cwd
        self.version = version
        self.entrypoint = entrypoint
        self.path = Path(path) if path is not None else (
            self.root / project / f"{session_id}.jsonl")
        self.agent_id = agent_id
        self.sidechain = agent_id is not None
        self.lines: list[str] = []
        self.clock = start
        self.step = step
        self.prompt_id: str | None = None
        self.sidecar: dict | str | None = None
        self._written = 0
        self._terminated = True
        self._last_uuid: str | None = None
        self._count = 0
        self._children: list[ClaudeTranscript] = []

    # -- the pieces of a line ------------------------------------------------------------

    def _serial(self) -> int:
        self._count += 1
        return self._count

    def _tick(self) -> str:
        stamp = _stamp(self.clock)
        self.clock += self.step
        return stamp

    def _base(self, kind: str) -> dict:
        uuid = f"00000000-0000-4000-8000-{self._serial():012d}"
        line: dict[str, Any] = {
            "parentUuid": self._last_uuid, "isSidechain": self.sidechain,
            "type": kind, "uuid": uuid, "timestamp": self._tick(),
            "sessionId": self.session_id, "cwd": self.cwd, "version": self.version,
            "entrypoint": self.entrypoint, "userType": "external",
        }
        if self.agent_id is not None:
            line["agentId"] = self.agent_id
        self._last_uuid = uuid
        return line

    # -- adding lines --------------------------------------------------------------------

    def add(self, line: dict) -> int:
        """Any line, as given. Returns its line number."""
        self.lines.append(json.dumps(line))
        return len(self.lines)

    def add_raw(self, text: str) -> int:
        """Any text as one line (a line that will not decode, say)."""
        self.lines.append(text)
        return len(self.lines)

    def user(self, text: str | list = "Hello.", *, prompt_id: str | None = None,
             new_prompt: bool = True, origin: str | None = None, **fields: Any) -> int:
        """A user line. Each starts a new prompt (`promptId`) unless `new_prompt` is false
        or `prompt_id` names one. `text` may be a list of content blocks. `origin` sets
        `origin.kind`; `fields` (`isMeta=True`, say) are set on the line."""
        if prompt_id is not None:
            self.prompt_id = prompt_id
        elif new_prompt or self.prompt_id is None:
            self.prompt_id = f"prompt-{self._serial():04d}"
        line = self._base("user")
        line["promptId"] = self.prompt_id
        line["message"] = {"role": "user", "content": text}
        if origin is not None:
            line["origin"] = {"kind": origin}
        line.update(fields)
        return self.add(line)

    def tool_result(self, call_id: str, output: str | list = "ok", *,
                    is_error: bool | None = None, **fields: Any) -> int:
        """A user line holding one tool's result, in the current prompt. `is_error`
        None leaves the key out, as Claude Code does for most results."""
        block: dict[str, Any] = {"type": "tool_result", "tool_use_id": call_id,
                                 "content": output}
        if is_error is not None:
            block["is_error"] = is_error
        line = self._base("user")
        if self.prompt_id is not None:
            line["promptId"] = self.prompt_id
        line["message"] = {"role": "user", "content": [block]}
        line["sourceToolUseID"] = call_id
        line["toolUseResult"] = output if isinstance(output, str) else {"content": output}
        line.update(fields)
        return self.add(line)

    def request(self, *, request_id: str | None = "", message_id: str | None = None,
                model: str = MODEL, requested_model: str | None = "",
                usage_block: dict | None | str = "") -> ClaudeRequest:
        """A request whose lines are added through the handle it returns.

        `request_id` "" makes one up and None leaves `requestId` out. `requested_model` ""
        is `model` and None leaves `requestedModel` out. `usage_block` "" is `usage()` and
        None leaves `message.usage` out."""
        serial = self._serial()
        return ClaudeRequest(
            self,
            request_id=f"req_{serial:04d}" if request_id == "" else request_id,
            message_id=message_id or f"msg_{serial:04d}",
            model=model,
            requested_model=model if requested_model == "" else requested_model,
            usage_block=usage() if usage_block == "" else usage_block)

    def assistant(self, text: str = "Done.", *, usage: dict | None | str = "",
                  **request: Any) -> ClaudeRequest:
        """A request of one text line. `request` takes `request()`'s arguments."""
        handle = self.request(usage_block=usage, **request)
        handle.text(text)
        return handle

    def synthetic(self, text: str = "No response requested.") -> ClaudeRequest:
        """A line Claude Code makes up itself: model `<synthetic>`, zero usage, no
        request id and no requested model."""
        handle = self.request(request_id=None, model="<synthetic>", requested_model=None,
                              usage_block=usage(input=0, cache_read=0, output=0,
                                                service_tier=None, speed=None,
                                                breakdown=False))
        handle.text(text)
        return handle

    def system(self, subtype: str, **fields: Any) -> int:
        """A `system` line of `subtype`, with `fields` set on it."""
        line = self._base("system")
        line["subtype"] = subtype
        line.update(fields)
        return self.add(line)

    def compaction(self, *, trigger: str = "auto", pre_tokens: int = 150000,
                   summary: str = "This session is being continued from an earlier one."
                   ) -> int:
        """A `compact_boundary` line, then the user line holding the summary
        (`isCompactSummary`). Returns the boundary's line number."""
        number = self.system("compact_boundary", content="Conversation compacted",
                             level="info", isMeta=False,
                             compactMetadata={"trigger": trigger, "preTokens": pre_tokens})
        self.user(summary, isCompactSummary=True, isVisibleInTranscriptOnly=True)
        return number

    def turn_duration(self, ms: int = 1200) -> int:
        """The `turn_duration` line Claude Code writes when a turn ends."""
        return self.system("turn_duration", durationMs=ms, isMeta=False)

    def title(self, text: str, *, ai: bool = False) -> int:
        """A `custom-title` line, or with `ai` an `ai-title` line."""
        if ai:
            return self.add({"type": "ai-title", "aiTitle": text,
                             "sessionId": self.session_id})
        return self.add({"type": "custom-title", "customTitle": text,
                         "sessionId": self.session_id})

    def other(self, kind: str, **fields: Any) -> int:
        """A line of a kind the mapping does not read (`attachment`, `mode`, ...)."""
        return self.add({"type": kind, "sessionId": self.session_id, **fields})

    # -- subagents -----------------------------------------------------------------------

    def subagent(self, agent_id: str = "a0190b0000000001", *,
                 agent_type: str | None = "general-purpose",
                 description: str | None = "Look into it", tool_use_id: str | None = None,
                 spawn_depth: int | None = 1, parent_agent_id: str | None = None,
                 workflow: str | None = None, sidecar: bool | str = True
                 ) -> ClaudeTranscript:
        """A builder for one subagent's file beside this transcript.

        `workflow` (`wf_<id>`) puts it under `subagents/workflows/<workflow>/`. The sidecar
        `agent-<id>.meta.json` holds the labels given (a None one is left out); `sidecar`
        False writes none, and a string writes that text as the sidecar (one that will not
        parse, say). Writing this transcript writes its subagents too."""
        directory = self.path.parent / self.path.stem / "subagents"
        if workflow is not None:
            directory = directory / "workflows" / workflow
        child = ClaudeTranscript(
            self.root, session_id=self.session_id, project=self.project, cwd=self.cwd,
            version=self.version, entrypoint=self.entrypoint, start=self.clock,
            step=self.step, path=directory / f"agent-{agent_id}.jsonl", agent_id=agent_id)
        if sidecar is True:
            labels = {"agentType": agent_type, "description": description,
                      "toolUseId": tool_use_id, "spawnDepth": spawn_depth,
                      "parentAgentId": parent_agent_id}
            child.sidecar = {key: value for key, value in labels.items()
                             if value is not None}
        elif isinstance(sidecar, str):
            child.sidecar = sidecar
        self._children.append(child)
        return child

    @property
    def sidecar_path(self) -> Path:
        return self.path.with_suffix(".meta.json")

    # -- writing -------------------------------------------------------------------------

    def text(self, *, terminated: bool = True) -> str:
        """The whole file's text as `write` would write it."""
        body = "\n".join(self.lines)
        return body + ("\n" if terminated and self.lines else "")

    def offset(self, line: int) -> int:
        """The byte offset where 1-based line `line` starts in the written file."""
        return sum(len(text.encode("utf-8")) + 1 for text in self.lines[:line - 1])

    def write(self, *, terminated: bool = True) -> Path:
        """Write the whole file, its subagents' files and their sidecars. With
        `terminated` false the last line has no newline, as a line being written."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, self.text(terminated=terminated), mode=0o644)
        self._written = len(self.lines)
        self._terminated = terminated
        if self.sidecar is not None:
            text = (self.sidecar if isinstance(self.sidecar, str)
                    else json.dumps(self.sidecar))
            atomic_write(self.sidecar_path, text, mode=0o644)
        for child in self._children:
            child.write()
        return self.path

    def flush(self, *, terminated: bool = True) -> Path:
        """Append the lines added since the last write, as Claude Code appends them. A
        last line left without its newline is ended first."""
        if not self.path.exists():
            return self.write(terminated=terminated)
        new = self.lines[self._written:]
        data = "" if self._terminated else "\n"
        data += "\n".join(new) + ("\n" if terminated and new else "")
        with self.path.open("ab") as handle:
            handle.write(data.encode("utf-8"))
        self._written = len(self.lines)
        self._terminated = terminated or not new
        return self.path
