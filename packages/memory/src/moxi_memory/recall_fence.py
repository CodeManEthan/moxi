"""The read-only fence of a chat recall's agent, and the probe that checks it on this
machine.

The fence's rule: a chat recall's agent can read and nothing else, with nothing installed
that could widen that, and no person needed to keep it so. Every launch of a chat recall,
first round and resumes alike, is a launcher headless launch of `claude -p` composed by
`request`, which pins every launcher row so nothing of the user's `[defaults]`, a preset
or the name template reaches it:

- it can read: `--tools Read,Grep,Glob` (and `WebFetch,WebSearch`, with an
  `--allowedTools` rule for the two, only where the web is allowed), and
  `--disallowedTools mcp__*`, which removes every MCP tool;
- and nothing else: no tool that runs a command or writes is in the session;
- nothing installed widens it: `--safe-mode` (no CLAUDE.md, skills, plugins, hooks, MCP
  servers, commands, agents or styles) and `--restricted` (no user, project or local
  settings files); no `--mcp-config`;
- no person keeps it so: `--permission-mode dontAsk` and `--permission-prompts none`,
  and the limits `--max-budget-usd BUDGET_USD` and `--max-turns` (the recall's
  `subconscious.max_tools` plus 1).

What Claude Code's docs leave unconfirmed is probed on the machine (`ensure`), at the
first chat recall after each change of the `claude` binary the launcher would start:
`flags`, that this Claude Code takes every flag above together (`FENCE_MIN_VERSION` at
least); `tools`, that the session's tools are within `fence_tools` and include the read
tools; and `mcp`, that no MCP server is connected. The probe composes the recall's own request with the brief
`PROBE_PROMPT` and stream output that keeps no transcript, runs it detached in a session
of its own, reads its output to the first `system/init` object, at most `PROBE_S`
seconds, ends its process tree, and judges that object.

The outcome is kept in `subconscious/chat/fence.json` for the binary, named by its
resolved path, size and modification time, so an update, a reinstall or another `claude`
on the launcher's `PATH` is a change and is probed again. One probe runs at a time, under
`subconscious/chat/fence.lock`. A failed outcome stands until the binary changes.

The rest of memory asks this module only whether a chat recall may start (`verdict`,
which starts no process), and with what request (`request`); the probe runs in a recall's
child (`ensure`). Adding a tool or an MCP server the fence allows by name is a change
here alone. Standard library at import; the core and the launcher are reached at use.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

#: The name of the chat recall's folder under the subconscious's directory: the agent's
#: working folder, the fence record and its lock, and the probe's output.
CHAT_DIR = "chat"
FENCE_RECORD = "fence.json"
FENCE_LOCK = "fence.lock"
PROBE_OUTPUT = "probe.log"

#: The harness and target every chat recall launches.
HARNESS = "claude"
TARGET = "headless"
#: The built-in tools a chat recall's agent reads with.
READ_TOOLS = ("Read", "Grep", "Glob")
#: The two tools it has as well where the web is allowed, each allowed by a rule.
WEB_TOOLS = ("WebFetch", "WebSearch")
#: The tool no flag can remove; it only ends the session.
END_TOOL = "EndConversation"
#: Claude Code's built-in tools a session may have beside the fence's own, each harmless:
#: none reads, runs or writes anything. A session may lack any of them.
HARMLESS_TOOLS = (END_TOOL,)
#: The deny rule that removes every MCP tool from the session.
NO_MCP = "mcp__*"
#: The permission mode in which every call that would ask is denied.
PERMISSION_MODE = "dontAsk"
#: The most one launch of a chat recall may spend, in US dollars.
BUDGET_USD = 0.50
#: The oldest Claude Code that takes every fence flag: `--permission-prompts` came last.
FENCE_MIN_VERSION = "2.1.259"
#: How long the probe waits for the session's `system/init`, and how long another
#: child waits for the probe that runs, in seconds.
PROBE_S = 30.0
#: How long `claude --version` may take, in seconds.
VERSION_S = 10.0
#: The probe's brief.
PROBE_PROMPT = "Reply with the single word OK."
#: How long the probe's process tree has between SIGTERM and SIGKILL, in seconds.
KILL_GRACE_S = 2.0

#: The output flags of a chat recall's launch, and the probe's in their place.
JSON_OUTPUT = ("--output-format", "json")
PROBE_OUTPUT_FLAGS = ("--output-format", "stream-json", "--verbose",
                      "--no-session-persistence")

#: The parts of the probe, in the order it judges them.
FLAGS = "flags"
TOOLS = "tools"
MCP = "mcp"
PARTS = (FLAGS, TOOLS, MCP)

#: Why `verdict` says a chat recall can't start.
NO_HARNESS = "no-harness"
FENCE = "fence"


def chat_dir(env: Mapping[str, str] | None = None) -> Path:
    """The chat recall's folder, `subconscious/chat` in memory's state directory."""
    from . import recalls

    return recalls.subconscious_dir(env) / CHAT_DIR


def fence_tools(*, web: bool = False) -> frozenset[str]:
    """The bound on a chat recall's session's tools: a session passes with any of these
    and no other, as long as it has every one of `READ_TOOLS`."""
    return frozenset((*READ_TOOLS, *HARMLESS_TOOLS, *(WEB_TOOLS if web else ())))


def fence_flags(*, max_tools: int) -> tuple[str, ...]:
    """The fence's flags that have no row in the launcher's catalog, passed through."""
    return ("--permission-prompts", "none", "--restricted", "--safe-mode",
            "--max-budget-usd", f"{BUDGET_USD:.2f}", "--max-turns", str(max_tools + 1))


@dataclass(frozen=True)
class Spec:
    """One launch of a chat recall: its brief, its working folder, its typed name, the
    ids memory minted for it, the notes folders its tools may read beside the working
    folder, its model (empty: the harness's default), its tool limit
    (`subconscious.max_tools`) and whether the web is allowed."""

    brief: str
    cwd: str
    name: str
    request_id: str
    session_id: str
    max_tools: int
    add_dirs: Sequence[str] = ()
    model: str = ""
    web: bool = False


def request(launcher: Any, spec: Spec, *, output: Sequence[str] = JSON_OUTPUT) -> Any:
    """The launcher's `LaunchRequest` for `spec`: every row the fence needs typed, and
    every other settable row of the launcher's catalog and Claude's left out, read from
    the catalogs now, so each holds its shipped default whatever `[defaults]` says.
    `output` is the output flags, the probe's in its place."""
    tools = [*READ_TOOLS, *(WEB_TOOLS if spec.web else ())]
    typed: dict[str, object] = {
        "harness": HARNESS, "target": TARGET, "cwd": spec.cwd, "name": spec.name,
        "brief": spec.brief, "tools": tools, "disallowed_tools": [NO_MCP],
        "permission_mode": PERMISSION_MODE}
    if spec.web:
        typed["allowed_tools"] = list(WEB_TOOLS)
    if spec.model:
        typed["model"] = spec.model
    if spec.add_dirs:
        typed["add_dir"] = [str(d) for d in spec.add_dirs]
    extra = (*output, *fence_flags(max_tools=spec.max_tools))
    kept = {*typed, "extra_flags"}
    left_out = tuple(name for name in launcher.settable_rows(HARNESS) if name not in kept)
    fields = dict(typed=typed, extra=extra, left_out=left_out, cwd=spec.cwd,
                  request_id=spec.request_id, session_id=spec.session_id, actor="memory",
                  off_posture=True)
    return launcher.LaunchRequest(**fields)


# -- the binary --------------------------------------------------------------------------

@dataclass(frozen=True)
class Binary:
    """The `claude` a chat recall's launch starts: its resolved path, size and
    modification time."""

    path: str
    size: int
    mtime_ns: int

    def as_dict(self) -> dict:
        return {"path": self.path, "size": self.size, "mtime_ns": self.mtime_ns}


def binary(launcher: Any, req: Any, *, env: Mapping[str, str] | None = None) -> Binary | None:
    """The binary `req`'s composition starts, as the launcher's `PATH` finds it, read
    with one stat; None when the launcher finds none. Starts no process."""
    try:
        composition = launcher.preview(req, env=env)
        command = launcher.prepare(req, composition, env=env)
        path = os.path.realpath(command)
        st = os.stat(path)
    except (launcher.Refusal, OSError):
        return None
    return Binary(path, st.st_size, st.st_mtime_ns)


# -- the record --------------------------------------------------------------------------

@dataclass(frozen=True)
class Outcome:
    """The probe's outcome for one binary: its version, whether it passed, and the part
    that failed with its detail."""

    binary: Binary
    version: str
    passed: bool
    part: str | None = None
    detail: str = ""
    at: float = 0.0

    def as_dict(self) -> dict:
        return {"binary": self.binary.as_dict(), "version": self.version,
                "passed": self.passed, "part": self.part, "detail": self.detail,
                "at": self.at}


def _outcome(raw: object) -> Outcome | None:
    try:
        b = raw["binary"]
        return Outcome(Binary(str(b["path"]), int(b["size"]), int(b["mtime_ns"])),
                       str(raw.get("version") or ""), bool(raw["passed"]),
                       raw.get("part"), str(raw.get("detail") or ""),
                       float(raw.get("at") or 0.0))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _read_record() -> list[Outcome]:
    try:
        raw = json.loads((chat_dir() / FENCE_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("outcomes") if isinstance(raw, dict) else None
    return [o for o in map(_outcome, items or []) if o is not None]


def recorded(found: Binary) -> Outcome | None:
    """The record's outcome for `found`, or None when it holds none for it."""
    for item in _read_record():
        if item.binary == found:
            return item
    return None


def _write_outcome(outcome: Outcome) -> None:
    """Keep `outcome` on the record, in place of any for the same path. The caller holds
    the fence lock."""
    from moxi import files

    kept = [o for o in _read_record() if o.binary.path != outcome.binary.path]
    data = {"outcomes": [o.as_dict() for o in (*kept, outcome)]}
    files.private_dir(chat_dir())
    files.atomic_write(chat_dir() / FENCE_RECORD, json.dumps(data, indent=1) + "\n")


# -- whether a chat recall may start -----------------------------------------------------

@dataclass(frozen=True)
class Verdict:
    """Whether a chat recall may start: `reason` None when it may (`outcome` the
    binary's passed outcome, or None when its child probes first), else `no-harness` or
    `fence`, with `detail` naming the failed part."""

    reason: str | None
    detail: str = ""
    binary: Binary | None = None
    outcome: Outcome | None = None

    @property
    def ok(self) -> bool:
        return self.reason is None


def verdict(launcher: Any, spec: Spec, *, env: Mapping[str, str] | None = None) -> Verdict:
    """Whether a chat recall of `spec` may start on this machine: the launcher finds
    `claude` (else `no-harness`), and the record holds no failed outcome for it (else
    `fence`). A binary the record holds no outcome for may start: its child probes
    before its first round. Starts no process."""
    found = binary(launcher, request(launcher, spec), env=env)
    if found is None:
        return Verdict(NO_HARNESS, "the launcher can't find claude on its PATH")
    outcome = recorded(found)
    if outcome is not None and not outcome.passed:
        return Verdict(FENCE, f"{outcome.part}: {outcome.detail}", found, outcome)
    return Verdict(None, "", found, outcome)


# -- the probe ---------------------------------------------------------------------------

def ensure(launcher: Any, spec: Spec, *, env: Mapping[str, str] | None = None,
           wait_s: float = PROBE_S) -> Verdict:
    """The verdict a recall's child goes on by: `verdict`, after probing the binary when
    the record holds no outcome for it. One probe runs at a time; a child that waits for
    another's (at most `wait_s`) reads its outcome. A probe with no outcome (no
    `system/init` for a reason the fence doesn't judge) records nothing, and the verdict
    is `no-outcome` with what happened."""
    from moxi import files

    first = verdict(launcher, spec, env=env)
    if not first.ok or first.outcome is not None:
        return first
    files.private_dir(chat_dir())
    try:
        with files.lock(chat_dir() / FENCE_LOCK, timeout_s=wait_s):
            found = first.binary
            outcome = recorded(found)
            if outcome is None:
                outcome, why = probe(launcher, spec, found, env=env)
                if outcome is None:
                    return Verdict(NO_OUTCOME, why, found)
                _write_outcome(outcome)
    except files.LockTimeout:
        return Verdict(NO_OUTCOME, f"another probe ran past {wait_s:.0f} s", first.binary)
    if outcome.passed:
        return Verdict(None, "", found, outcome)
    return Verdict(FENCE, f"{outcome.part}: {outcome.detail}", found, outcome)


#: The verdict of a probe that gave no outcome.
NO_OUTCOME = "no-outcome"


def probe(launcher: Any, spec: Spec, found: Binary, *,
          env: Mapping[str, str] | None = None) -> tuple[Outcome | None, str]:
    """Probe `found` with `spec`'s own request: its outcome, or None and why there is
    none. Records nothing."""
    from moxi import proc

    req = request(launcher, replace(spec, brief=PROBE_PROMPT), output=PROBE_OUTPUT_FLAGS)
    composition = launcher.preview(req, env=env)
    child_env = launcher.child_env(req, composition, env=env)
    now = time.time()
    try:
        ran = proc.run([found.path, "--version"], timeout_s=VERSION_S, env=child_env,
                       cwd=spec.cwd)
        version = _version((ran.stdout or b"").decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - a version that can't be read is no outcome
        return None, f"claude --version failed: {exc}"
    if version is None:
        return None, "claude --version printed no version"
    if _older(version, FENCE_MIN_VERSION):
        return Outcome(found, version, passed=False, part=FLAGS,
                       detail=f"Claude Code {version} is older than {FENCE_MIN_VERSION}, "
                              "the first that takes every fence flag", at=now), ""
    output = chat_dir() / PROBE_OUTPUT
    prompt = getattr(composition, "prompt_file", None)
    try:
        output.unlink(missing_ok=True)
        if prompt is not None:
            _write_prompt(prompt)
        init, text, exited = _run_to_init(found, composition, child_env, spec.cwd, output)
    finally:
        if prompt is not None:
            Path(prompt.path).unlink(missing_ok=True)
    part, detail = judge(init, text, exited=exited, web=spec.web)
    if part is None and init is None:
        return None, detail
    return Outcome(found, version, passed=part is None, part=part, detail=detail,
                   at=now), ""


def _write_prompt(prompt: Any) -> None:
    """Write the system prompt file the composition's argv names, as a launch would."""
    from moxi import files

    files.private_dir(Path(prompt.path).parent)
    files.atomic_write(prompt.path, prompt.text)


def _run_to_init(found: Binary, composition: Any, child_env: Mapping[str, str],
                 cwd: str, output: Path) -> tuple[dict | None, str, bool]:
    """Run the composition's argv detached, read its output until the first
    `system/init`, at most `PROBE_S`, and end its process tree: the init (None when
    none came), the output read, and whether the process exited before its end."""
    from moxi import proc

    argv = [found.path, *composition.argv[1:]]
    pid = proc.spawn(argv, detached=True, output=output, env=dict(child_env), cwd=cwd)
    me = proc.identity(pid)
    deadline = time.monotonic() + PROBE_S
    init, text, exited = None, "", False
    try:
        while True:
            text = _read(output)
            init = _first_init(text)
            if init is not None:
                break
            if _ended(pid):
                text, exited = _read(output), True
                init = _first_init(text)
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
    finally:
        if not exited and me is not None:
            end_tree(me)
        _ended(pid)
    return init, text, exited


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""


def _ended(pid: int) -> bool:
    from moxi import proc

    try:
        return proc.status(pid) is not None
    except LookupError:
        return True


def _first_init(text: str) -> dict | None:
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("type") == "system" \
                and item.get("subtype") == "init":
            return item
    return None


_UNKNOWN_OPTION = re.compile(r"unknown option '?(--?[A-Za-z][\w-]*)")


def judge(init: dict | None, text: str, *, exited: bool,
          web: bool = False) -> tuple[str | None, str]:
    """The failed part and its detail for a probe that read `init` (None when it read
    none) and `text`; `(None, "")` for a pass, and `(None, why)` for no outcome."""
    if init is None:
        found = _UNKNOWN_OPTION.search(text)
        if exited and found:
            return FLAGS, f"this Claude Code refuses {found.group(1)}"
        last = next((line for line in reversed(text.splitlines()) if line.strip()), "")
        why = "it exited" if exited else f"none came within {PROBE_S:.0f} s"
        return None, f"no system/init: {why}" + (f" ({last.strip()[:200]})" if last else "")
    tools = {str(t) for t in init.get("tools") or ()}
    bound = fence_tools(web=web)
    extra, missing = sorted(tools - bound), sorted(set(READ_TOOLS) - tools)
    if extra or missing:
        words = [f"extra {', '.join(extra)}"] if extra else []
        words += [f"missing {', '.join(missing)}"] if missing else []
        return TOOLS, "the session's tools are not the fence's: " + "; ".join(words)
    servers = init.get("mcp_servers") or ()
    if servers:
        names = sorted(str(s.get("name") if isinstance(s, dict) else s) for s in servers)
        return MCP, f"MCP servers are connected: {', '.join(names)}"
    return None, ""


def _version(text: str) -> str | None:
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return found.group(0) if found else None


def _older(version: str, floor: str) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(p) for p in v.split("."))

    return parts(version) < parts(floor)


def end_tree(root: Any) -> None:
    """End the process `root` names (a `moxi.proc.ProcId`) and every descendant, TERM
    first and KILL to what is left after `KILL_GRACE_S`; nothing when its pid no longer
    names that process. POSIX: the probe runs in a session of its own, so its process
    group is signalled, then each descendant found that left it. Windows: each
    descendant is ended, the root first so it starts no more."""
    import psutil
    from moxi import proc

    if not proc.alive(root):
        return
    try:
        below = psutil.Process(root.pid).children(recursive=True)
    except psutil.Error:
        below = []
    if sys.platform != "win32":
        _signal_group(root.pid, signal.SIGTERM)
    for item in [psutil.Process(root.pid)] if proc.alive(root) else []:
        _quiet(item.terminate)
    for item in below:
        _quiet(item.terminate)
    deadline = time.monotonic() + KILL_GRACE_S
    while time.monotonic() < deadline and (
            proc.alive(root) or any(_quiet(item.is_running) for item in below)):
        time.sleep(0.05)
    if sys.platform != "win32":
        _signal_group(root.pid, signal.SIGKILL)
    if proc.alive(root):
        _quiet(psutil.Process(root.pid).kill)
    for item in below:
        _quiet(item.kill)


def _signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except OSError:
        pass


def _quiet(call):
    import psutil

    try:
        return call()
    except psutil.Error:
        return None


__all__ = ["BUDGET_USD", "CHAT_DIR", "END_TOOL", "FENCE", "FENCE_LOCK", "FENCE_MIN_VERSION",
           "FENCE_RECORD", "FLAGS", "HARMLESS_TOOLS", "HARNESS", "JSON_OUTPUT", "MCP", "NO_HARNESS", "NO_MCP",
           "NO_OUTCOME", "PARTS", "PERMISSION_MODE", "PROBE_OUTPUT_FLAGS", "PROBE_PROMPT",
           "PROBE_S", "READ_TOOLS", "TARGET", "TOOLS", "WEB_TOOLS", "Binary", "Outcome",
           "Spec", "Verdict", "binary", "chat_dir", "end_tree", "ensure", "fence_flags",
           "fence_tools", "judge", "probe", "recorded", "request", "verdict"]
