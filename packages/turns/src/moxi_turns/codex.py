"""Launch a Codex session behind a turns endpoint.

``launch`` owns one app-server, one thread, its hook socket, and an optional Codex
window. The app-server link reconnects and resumes the same thread after a connection
or child failure. Calls into the endpoint settle every submitted delivery and every
steer.

A turn is open in the ledger from its start until Codex says it is over:
``turn/completed``; a ``thread/status/changed`` to a non-active status with no
``turn/completed`` within ``TURN_COMPLETED_GRACE``; a load whose answer shows it gone; or
a dead app-server. No hook closes a turn, and neither does a turn or status notification
that names another thread (a spawned subagent's, on the same connection). A delivery reads ``Refused`` only when Codex
did not take its text (the request was never sent, or Codex answered it with an error);
once the request went out it reads ``Started`` on a turn the host has just learned of,
or ``Lost("untied: ...")`` at once.

A fresh thread. Codex writes no rollout for a thread until something enters its
history, and ``thread/resume`` refuses a thread with none. So right after
``thread/start`` the host injects one developer item (``thread/inject_items``), which
writes the rollout and is not a turn: no model call is made, and the window opens on an
empty transcript. The load is accepted, and raises Ready ``startup``, once Codex takes
the item. A Codex without ``thread/inject_items`` is refused for good, with no retry and
no turn sent in its place.

Unturned threads. A thread is unturned until its first turn has run. The mark is set
when the inject is accepted, or on a resumed load whose turn list is empty. It is
cleared when the hook server has answered the thread's first ``SessionStart`` since the
load, or when that first turn ends, whichever the host sees first; ``turn/started``
never clears it. While it is set, a resumed load raises Ready ``startup``, and the hook
server reads a ``SessionStart`` ``resume`` as ``startup``: Codex says ``resume`` at the
first turn of a thread reloaded before any turn, and the listeners would otherwise never
be asked with ``startup`` for that session.

Subagents. The host lists Codex's collab subagents from the ``collabAgentToolCall``
items it sees, rebuilt from the thread's history at each resumed load. A spawn that comes
as a ``subAgentActivity`` item (Codex's ``multi_agent_v2``) carries no state turns can
follow, so from the first one, live or in the history, the list is not complete for the
session's life: the agent count reads unknown, and every check that waits for no busy
subagent (``refuse_agents``, ``compact``) refuses, even with none running.

Steers. The endpoint records a steer and answers its caller at once; ``append`` runs on
the endpoint's ordered worker, so steers reach ``turn/steer`` one at a time in the order
recorded. ``steering`` (called as the steer is recorded) puts its id in ``_steering``, and
a ``turn/completed`` that comes while any id is there is held until that steer settles:
the hold starts at record, not when the worker reaches the steer, so a steer recorded
while the turn ran is either taken into that turn or refused ``idle``, and ``turn.ended``
lists it when taken. ``turn/steer`` answered reports ``steered``; "no active turn" reports
``refused`` ``idle``; no answer within the app-server client's bound reports ``lost``. A
steer the endpoint settles meanwhile (its own bound) goes to ``expired``, which drops it
from ``_steering`` and runs the completions it held. Its ``turn/steer``, if already sent,
may still be taken by Codex: the caller read ``lost``, and Codex's late answer changes
nothing.

Compaction. ``command`` runs ``compact`` on the endpoint's worker: with ``stop_shells`` it
first lists the background terminals (a method-not-found refuses ``unsupported``; any
other failure leaves a list turns cannot vouch for), then re-checks the ledger and the
live facts, and sends ``thread/compact/start``. Codex runs the compaction as a turn of its
own, which the host reports with ``command`` ``compact``. Its ``PostCompact`` hook raises
``Ready(compact)``, which settles the command in the core; with ``stop_shells`` the host
then terminates the terminals it lists at that moment, lists them again, and reports
what it stopped (``command_ran``), all within ``SHELL_STOP_S``.

Guards and after-tool. The host loads the guards that are on at its attach and at each
``ready`` (a ``GuardRunner`` for ``codex``), and registers ``PreToolUse`` for the launch
only when some guard covers Codex, with a matcher built from their Codex patterns
(``codex_hooks``). At each ``PreToolUse`` ping it matches the call against its table
(``guards.run.decide``): a data-only guard refuses at once, judge guards are judged in this
process, and the answer goes back in Codex's hook output form. The command also carries
the cover rule of the guards that declared ``on_error = "deny"``, so a ping the host can't
answer refuses a call one of them covers (``codex_hooks``). The call's ``cwd`` is the
hook input's when absolute, else the launch directory. ``PostToolUse`` is registered only
when an installed listener row names ``after-tool``. The main loop's calls reach a row
only while its floor lets them: the main thread's context is
``tokenUsage.last.totalTokens`` of its last ``thread/tokenUsage/updated``, and a row is
skipped only while that reading is below the row's floor; with no reading yet no row is
skipped. A subagent's call always reaches every row. ``record_path`` is the thread's
rollout (``thread.path`` in the load answer, else the hook's ``transcript_path``) on the
main loop, and the subagent's own ``transcript_path`` in a subagent.

Limits and MCP servers. After each accepted load the host reads the account's rate limits
(``account/rateLimits/read``) once, and Codex pushes ``account/rateLimits/updated`` with
token counts; each reading goes to the ``codex_limits`` rows unparsed, and
``usage.codex_limits`` reads one on request. At each ``ready`` (the load's, and a
compaction's two) and on ``mcp.reload`` the host settles the MCP servers its thread
config names, reloading them once when one reads ``failed`` (``codex_mcp``).

The ending form: once a Stop is answered unblocked, Codex may complete the turn
at any moment, and a steer it takes in that gap can be written into the turn and never
answered. So the turn is marked ending: it stays open, but no ``turn/steer`` for it goes
out after the Stop answer, and a steer reads ``idle``. A ``turn/steer`` already on its
way when the hold answers is let finish first, so it reaches Codex inside the hook.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import random
import shutil
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from moxi import doctor as moxi_doctor
from moxi.paths import script

from . import client as turns_client
from . import codex_hooks
from .codex_appserver import LIMITS_READ, LIMITS_READ_S, LIMITS_UPDATED
from .codex_mcp import STARTUP_STATUS, McpSettle, rendered
from . import codex_protocol as proto
from . import protocol
from .doctor import MODULE
from .endpoint import Endpoint
from .guards import MAX_WAIT_KEY, MAX_WAIT_MS, ON_KEY, Prompt, ToolCall
from .guards.run import GuardRunner, decide
from .harness import SHELL_STOP_S, CommandRequest, Delivery, Harness, Live
from .results import (SHELL_UNLISTED, Agent, Agents, Failed, LeftShell, Refused, Shells,
                      StopFacts)

log = logging.getLogger("moxi.turns.codex")

BINARY = "codex"
STEER_ATTEMPTS = 5
TURN_STARTED_WAIT = 10.0
#: How long the ledger keeps a turn open after Codex reports the thread no longer active
#: without a ``turn/completed`` for it (an Interrupt whose completion was lost).
TURN_COMPLETED_GRACE = 5.0
#: How long an unblocked Stop answer waits for a ``turn/steer`` already sent to be
#: answered, so that Codex takes it before the answer (inside the hook).
STEER_DRAIN_WAIT = 1.0
#: The reason of a delivery Codex was sent and the host could not tie to a turn.
UNTIED = "untied: Codex took the text into a turn the host could not tie it to"
RECONNECT_WAIT = 0.5
BACKOFF_MAX = 30.0
BACKOFF_FACTOR = 2.0
BACKOFF_JITTER = 0.2
VERIFY_WAIT = 30.0
#: How long one ``thread/backgroundTerminals/list`` may take, in seconds.
TERMINALS_WAIT = 2.0
#: The developer item a fresh thread is opened with. It stays in the thread's
#: model-visible history for every later turn, so it is a plain statement, not an
#: instruction. Codex refuses an empty item list.
READY_ITEM = "This thread was opened by Moxi's turns host."
#: The trouble line of a load refused because Codex cannot inject the item.
INJECT_MISSING = ("this Codex has no thread/inject_items; Codex 0.153.0 has it. "
                  "Update Codex, then launch again.")
OWNED_OPTIONS = frozenset({
    "cwd", "developerInstructions", "threadId", "excludeTurns", "approvalPolicy",
})
#: The sandbox modes a thread may ask for, and the policy type a load answer reports
#: for each.
SANDBOX_POLICY_TYPES = {"read-only": "readOnly", "workspace-write": "workspaceWrite",
                        "danger-full-access": "dangerFullAccess"}


def verify_options(options: Mapping, answer: Mapping | None, cwd) -> str | None:
    """Why a load's answer does not report the sandbox `options` asked for, or None.

    `options` are the thread parameters the load sent, `answer` is the `thread/start`
    or `thread/resume` result, and `cwd` the thread's working directory. Only a
    top-level `sandbox` is checked: its policy type, and for `workspace-write` the
    network access and writable roots under `config["sandbox_workspace_write"]`. A
    `permissions` profile, or an answer that reports no sandbox, is not checked. The
    app-server leaves the working directory out of the writable roots, since
    workspace-write grants it, so the answer's cwd and `cwd` both count as granted.
    """
    mode = options.get("sandbox")
    reported = (answer or {}).get("sandbox")
    if not mode or not isinstance(reported, Mapping):
        return None
    want = SANDBOX_POLICY_TYPES.get(mode)
    if want and reported.get("type") != want:
        return f"sandbox is {reported.get('type')!r}, not {want!r}"
    if want != "workspaceWrite":
        return None
    settings = (options.get("config") or {}).get("sandbox_workspace_write") or {}
    if bool(reported.get("networkAccess")) != bool(settings.get("network_access")):
        return (f"network access is {'on' if reported.get('networkAccess') else 'off'}, "
                f"not {'on' if settings.get('network_access') else 'off'}")
    have = {os.path.realpath(str(root)) for root in reported.get("writableRoots") or []}
    for granted in ((answer or {}).get("cwd"), cwd):
        if granted:
            have.add(os.path.realpath(str(granted)))
    missing = [str(root) for root in settings.get("writable_roots") or []
               if os.path.realpath(str(root)) not in have]
    if missing:
        return f"writable roots lack {', '.join(missing)}"
    return None


def _backoff_delays(rng=random):
    delay = RECONNECT_WAIT
    while True:
        yield min(delay, BACKOFF_MAX) * (1 + rng.uniform(-BACKOFF_JITTER, BACKOFF_JITTER))
        delay = min(delay * BACKOFF_FACTOR, BACKOFF_MAX)


class Generation:
    """One app-server connection, from connect until close or replacement."""

    def __init__(self, number: int):
        self.number = number
        self.thread_id: str | None = None
        self.ready = threading.Event()
        self.ended = threading.Event()


class _VerifyFailed(RuntimeError):
    pass


class CodexTooOld(RuntimeError):
    """This Codex lacks a call the host needs to open a thread. The load is refused for
    good: no retry, and no turn is sent in the call's place."""


class _TurnEnding(RuntimeError):
    """The steer's turn had its Stop answered unblocked: the text is not sent."""


@dataclass
class _Compaction:
    """A ``compact`` the host sent, until its Ready."""

    request: CommandRequest
    #: The terminals listed before the send, or None when the list could not be read.
    listed: list[str] | None
    sent_at: float
    #: The turn Codex ran the compaction as, once reported.
    turn_id: str | None = None


class CodexHarness(Harness):
    name = "codex"
    can = frozenset({"deliver", "only-if-idle", "steer", "stop-handler", "compact",
                     "agents", "stop-shells", "guards", "after-tool", "usage",
                     "mcp-token", "environ"})
    mod_version = None

    @staticmethod
    def tool_points() -> dict[str, bool]:
        """Codex's `PreToolUse` and `PostToolUse` command hooks."""
        return {"pre": True, "after": True}

    def __init__(self, cwd, *, instructions: str | None = None, env=None,
                 window: bool = True, options=None, verify=None,
                 binary: str = BINARY, ping=None, client_factory=None,
                 app_server_factory=None, window_factory=None,
                 verify_wait: float = VERIFY_WAIT, backoff_factory=None,
                 resume: str | None = None, guards: GuardRunner | None = None):
        if options is not None and not isinstance(options, Mapping):
            raise TypeError("options must be a mapping")
        if resume is not None and (not isinstance(resume, str) or not resume.strip()):
            raise ValueError("resume names a Codex thread id")
        conflicts = OWNED_OPTIONS.intersection(options or ())
        if conflicts:
            names = ", ".join(sorted(conflicts))
            raise ValueError(f"Codex options name keys turns owns: {names}")
        self.cwd = str(Path(cwd).resolve())
        self.instructions = instructions
        self.options = dict(options or {})
        if verify is None and self.options.get("sandbox"):
            # The default check reads the very options each load sends.
            sent, where = self.options, self.cwd
            verify = lambda answer: verify_options(sent, answer, where)  # noqa: E731
        self.verify = verify
        self.verify_wait = verify_wait
        self.env = dict(os.environ)
        if env is not None:
            self.env.update({str(key): str(value) for key, value in env.items()})
        self.window_requested = window
        self.binary = binary
        self.ping = ping
        self._client_factory = client_factory
        self._app_server_factory = app_server_factory
        self._window_factory = window_factory
        self._backoff_factory = backoff_factory or _backoff_delays
        #: The guard runner; made at `attach` with the endpoint's environment when None.
        self.guards = guards
        #: `guards.max_wait_ms` at the launch, once read: the `PreToolUse` ping waits this
        #: long (and a margin), so a later `ready` never raises a guard's bound past it.
        self._guard_wait_ms: int | None = None
        #: The thread's rollout, from the load answer; None until a load names one.
        self.record_path: str | None = None
        #: The main thread's last token reading, `(thread id, tokens)`.
        self._usage: tuple[str, int] | None = None
        #: The current main turn's prompts, `(turn id, prompts)`, for guards that want them.
        self._prompts: tuple[str | None, tuple[Prompt, ...]] = (None, ())
        #: The last rate-limits reading the host saw, `(payload, received_at, kind)`.
        self._limits: tuple[dict, float, str] | None = None
        #: The settle of the MCP servers the thread config names, at each ready and on
        #: `mcp.reload`.
        self.mcp = McpSettle(rendered(self.options), self._app_call,
                             lambda: self.session_id)

        self.pid = os.getpid()
        #: The thread: None until a fresh thread is started; a resume names it from
        #: the start, so the first load is a `thread/resume`.
        self.session_id: str | None = resume
        self.active_turn_id: str | None = None
        self.thread_active = False
        self.ws = None
        self.app_server = None
        self.hook_server: codex_hooks.HookServer | None = None
        self.window_session = None
        self.generation: Generation | None = None
        self.app_socket: Path | None = None
        self.trouble: str | None = None

        self._lock = threading.RLock()
        self._connect_lock = threading.Lock()
        self._stop = threading.Event()
        self._started = threading.Event()
        self._started_turn: str | None = None
        self._pending_delivery: str | None = None
        self._pending_answered = False   # the waiting delivery's turn/start has answered
        self._reported_turns: set[str] = set()
        self._settled_turns: set[str] = set()
        self._steering: set[str] = set()
        self._ending: str | None = None    # the turn whose Stop was answered unblocked
        self._steers_out = 0               # turn/steer requests sent and not yet answered
        self._steers_idle = threading.Condition(self._lock)
        self._pending_completions: list[dict] = []
        self._grace: tuple[str, threading.Timer] | None = None   # the status backstop
        self._foreign_threads: set[str | None] = set()
        self._generation_number = 0
        self._supervisor: threading.Thread | None = None
        self._accepted = threading.Event()
        self._unwritten = False      # a fresh thread's rollout is not written yet
        #: The thread has had no turn yet (see "Unturned threads" above).
        self._unturned = False
        self._compacting: _Compaction | None = None
        #: The session's collab subagents by thread id, `(state, final, parent_id)`: the
        #: host's record of Codex's own notices, kept from the item stream and rebuilt from
        #: the thread's history at each resumed load.
        self._collab: dict[str, tuple[str, bool | None, str | None]] = {}
        #: Whether that record holds every subagent: False once a resumed load could not
        #: read the history, until one can.
        self._collab_whole = True
        #: Set for the session's life once a spawn came as an item turns cannot read
        #: (``subAgentActivity``), live or in the history: the record is then never whole.
        self._unreadable_spawns = False
        #: Subagents spawned from a subagent's own thread, whose last live notice was not
        #: final when a resumed load rebuilt the record: the session's history does not
        #: hold them, so the record keeps them as they stood and is not whole until a live
        #: notice names each again.
        self._unconfirmed: set[str] = set()

    def attach(self, endpoint) -> None:
        super().attach(endpoint)
        self.app_socket = Path(protocol.endpoint_socket(endpoint.dir,
                                                        protocol.CODEX_APP_SOCK))
        hook_socket = codex_hooks.hooks_socket(endpoint.dir)
        ping = self.ping
        if ping is None:
            ping = [str(script("moxi-turns-ping"))]
        if self.guards is None:
            self.guards = GuardRunner(self.name, env=endpoint.env,
                                      settings=self._guard_settings)
        self.guards.reload()
        if self._guard_wait_ms is None:
            # turns' config did not read (or a runner was handed in): the default bound.
            self._guard_wait_ms = MAX_WAIT_MS
        pre_matcher = codex_hooks.matcher(
            p for e in self.guards.entries for p in e.calls)
        flags, self.hook_keys = codex_hooks.session_flags(
            list(ping), hook_socket, hold_wait_s=endpoint.stop_wait_s,
            listen_wait_s=endpoint.listen_wait_s,
            pre_matcher=pre_matcher, guard_wait_s=self._guard_wait_ms / 1000,
            refuse=codex_hooks.refuse_table(self.guards.entries),
            after_tool=bool(endpoint.floors.rows), after_wait_s=endpoint.after_tool_wait_s,
        )

        from .codex_appserver import AppServer, listen_argv

        argv = listen_argv(self.app_socket, self.binary) + flags
        factory = self._app_server_factory or AppServer
        self.app_server = factory(
            self.app_socket, env=self.env, cwd=self.cwd, argv=argv,
        )
        self.hook_server = codex_hooks.HookServer(self, hook_socket)

    def _guard_settings(self) -> tuple[list[str], int]:
        """`guards.on` and `guards.max_wait_ms` now, the wait held to the launch's value,
        since the ping's wait is fixed for the launch. Raises `ConfigError`."""
        from . import config as turns_config

        settings = turns_config.load(self.endpoint.env)
        wait = int(settings[MAX_WAIT_KEY])
        if self._guard_wait_ms is None:
            self._guard_wait_ms = wait
        return list(settings[ON_KEY]), min(wait, self._guard_wait_ms)

    def guards_ready(self) -> list[str]:
        """A `ready`: read the guards again. Returns the start-context lines for guards
        on the list that did not load, each said once."""
        if self.guards is None:
            return []
        self.guards.reload()
        return self.guards.take_notes()

    def prompt_seen(self, turn_id, text: str | None) -> None:
        """A main-loop prompt: the current main turn's prompts, for guards that want
        them. A prompt in another turn starts the list again."""
        if not isinstance(text, str):
            return
        turn_id = turn_id if isinstance(turn_id, str) else None
        with self._lock:
            held, prompts = self._prompts
            if turn_id is None or turn_id != held:
                prompts = ()
            self._prompts = (turn_id, (*prompts, Prompt(None, text)))

    def context_tokens(self) -> int | None:
        """The main thread's context, in tokens, from its last token reading; None
        before the first, or when the reading is of an earlier thread."""
        with self._lock:
            usage, main = self._usage, self.session_id
        if usage is None or usage[0] != main:
            return None
        return usage[1]

    def _record_path(self, frame: dict) -> str | None:
        hooked = frame.get("transcript_path")
        hooked = hooked if isinstance(hooked, str) and hooked else None
        if frame.get("agent_id"):
            # A subagent writes its own rollout, which its hook names.
            return hooked
        return self.record_path or hooked

    def _call_cwd(self, frame: dict) -> str:
        """The directory a call runs in: the hook input's `cwd` when it is absolute,
        else the launch directory."""
        cwd = frame.get("cwd")
        return cwd if isinstance(cwd, str) and os.path.isabs(cwd) else self.cwd

    def pre_tool(self, frame: dict) -> dict:
        """A `PreToolUse` ping: match the call, run the judges, answer `deny`, or the
        lines for the model as `context`."""
        tool = frame.get("tool_name")
        if self.guards is None or not isinstance(tool, str) or not tool:
            return {}
        agent_id = _text(frame.get("agent_id"))
        given = frame.get("tool_input")
        given = dict(given) if isinstance(given, dict) else {}
        decision = decide(self.guards.entries, tool, agent_id, given)
        if decision.deny is not None:
            return {"deny": decision.deny}
        if not decision.ask:
            return {}
        with self._lock:
            prompts = self._prompts[1]
        call = ToolCall(self.name, self.session_id, agent_id, tool,
                        _text(frame.get("tool_use_id")), given, self._call_cwd(frame),
                        self._record_path(frame), self.endpoint.env, prompts)
        answer = self.guards.ask(call, decision.ask)
        if answer.deny is not None:
            return {"deny": answer.deny}
        lines = [*answer.lines, *answer.broken]
        return {"context": "\n".join(lines)} if lines else {}

    def post_tool(self, frame: dict) -> dict:
        """A `PostToolUse` ping: ask the after-tool rows the floors let through and
        answer their text as `context`."""
        tool = frame.get("tool_name")
        if not isinstance(tool, str) or not tool:
            return {}
        agent_id = _text(frame.get("agent_id"))
        rows = None
        if agent_id is None:
            context = self.context_tokens()
            if context is not None:
                rows = [rid for rid, floor in self.endpoint.floors.values().items()
                        if context >= floor]
                if not rows:
                    return {}
        text = self.endpoint.after_tool(
            tool, tool_use_id=_text(frame.get("tool_use_id")), agent_id=agent_id,
            point="after", rows=rows, record_path=self._record_path(frame))
        return {"context": text} if text else {}

    def start(self) -> None:
        self.hook_server.start()
        try:
            self._connect()
        except codex_hooks.HooksUntrusted:
            self.close()
            raise
        except CodexTooOld as exc:
            self._load_failed(exc)
            self.close()
            raise
        except Exception as exc:  # the first failed load follows the retry road
            self._load_failed(exc)
        self._supervisor = threading.Thread(
            target=self._supervise, daemon=True, name="turns-codex-supervisor",
        )
        self._supervisor.start()
        if self.window_requested:
            from .codex_window import WindowSession

            factory = self._window_factory or WindowSession
            self.window_session = factory(
                self, binary=self.binary, cwd=self.cwd, env=self.env,
            ).start()
            while not self._accepted.wait(0.05):
                if self.endpoint.is_gone:
                    raise RuntimeError(self.trouble or "the Codex thread did not load")

    def loading(self) -> bool:
        ws = self.ws
        return ws is None or not ws.connected or self._unwritten

    def _set_trouble(self, reason: str | None, *, release: bool = True) -> None:
        self.trouble = reason
        if hasattr(self, "endpoint"):
            self.endpoint.load_changed(release=release)

    @staticmethod
    def _failure_reason(exc: BaseException) -> str:
        text = str(exc).strip()
        return text or type(exc).__name__

    def _load_failed(self, exc: BaseException) -> None:
        reason = self._failure_reason(exc)
        self._set_trouble(reason)
        log.warning("Codex app-server load failed: %s", reason)

    def _verify_answer(self, answer: dict) -> str | None:
        if self.verify is None:
            return None
        done = threading.Event()
        outcome: list = []

        def call_verify():
            try:
                outcome.append((True, self.verify(answer)))
            except BaseException as exc:  # caller code is contained as a failed load
                outcome.append((False, exc, exc.__traceback__))
            finally:
                done.set()

        threading.Thread(target=call_verify, daemon=True,
                         name="turns-codex-verify").start()
        if not done.wait(self.verify_wait):
            raise _VerifyFailed(
                f"Codex load verification did not finish within {self.verify_wait:g} s"
            )
        if not outcome[0][0]:
            exc, tb = outcome[0][1], outcome[0][2]
            log.error("Codex load verification raised", exc_info=(type(exc), exc, tb))
            raise _VerifyFailed(f"Codex load verification raised: {exc}") from exc
        reason = outcome[0][1]
        if reason is None or reason == "":
            return None
        if not isinstance(reason, str):
            # verify is typed str | None; any other answer fails closed.
            raise _VerifyFailed(
                f"Codex load verification returned {type(reason).__name__}, not a reason"
            )
        return reason

    def _connect(self) -> None:
        try:
            from .codex_ws import AppServerClient, AppServerOffline
        except ModuleNotFoundError as exc:
            if exc.name != "websockets":
                raise
            raise RuntimeError(
                "the Codex host needs websockets; run "
                "`moxi install turns --with codex`"
            ) from None

        with self._connect_lock:
            ws = None
            generation = None
            spawned = False
            published = False
            if self._stop.is_set():
                raise AppServerOffline("the Codex host is stopping")
            try:
                old_ws = self.ws
                old_generation = self.generation
                with self._lock:
                    self.ws = None
                if old_generation is not None:
                    old_generation.ended.set()
                if old_ws is not None:
                    old_ws.close()
                if not self.app_server.alive:
                    self._settle_dead_turn()
                    if self._stop.is_set():
                        raise AppServerOffline("the Codex host is stopping")
                    self.app_server.start()
                    spawned = True
                    if not self.app_server.wait_for_socket():
                        raise AppServerOffline(
                            f"the app-server did not open {self.app_socket} "
                            f"(exit {self.app_server.returncode})"
                        )
                self._generation_number += 1
                generation = Generation(self._generation_number)
                holder: dict = {}
                pending_notifications: list[tuple] = []

                def on_notification(method, params, rid=None):
                    if not published:
                        pending_notifications.append((method, params, rid))
                    else:
                        self._on_notification(method, params, rid)

                def on_close():
                    generation.ended.set()
                    with self._lock:
                        if self.ws is holder.get("ws"):
                            self.ws = None
                    if published:
                        self.endpoint.load_changed()

                factory = self._client_factory or (
                    lambda **kwargs: AppServerClient(self.app_socket, **kwargs)
                )
                ws = factory(on_notification=on_notification, on_close=on_close)
                holder["ws"] = ws
                ws.connect()
                ws.initialize()
                problem = codex_hooks.trust_problem(
                    ws.call("hooks/list", {"cwds": [self.cwd]}), self.hook_keys
                )
                if problem:
                    raise codex_hooks.HooksUntrusted(problem)

                starting = self.session_id is None
                if starting:
                    result = ws.call("thread/start", proto.start_thread_params(
                        self.cwd, self.instructions, self.options,
                    ))
                    thread_id = (result.get("thread") or {}).get("id")
                    if not thread_id:
                        raise AppServerOffline("thread/start returned no thread id")
                else:
                    thread_id = self.session_id
                    result = ws.call("thread/resume", proto.resume_params(
                        thread_id, self.instructions, self.options,
                    ))
                warning = self._verify_answer(result)
                if warning and not starting:
                    raise _VerifyFailed(warning)
                if warning:
                    log.warning("thread/start reports different settings: %s", warning)

                thread = result.get("thread") or {}
                path = thread.get("path")
                # A fresh thread has spawned nothing yet; a resumed one may have, before
                # this connection or this host saw its items.
                collab, whole, unreadable = ({}, True, False) if starting \
                    else self._read_collab(ws, thread_id)
                active = False if starting else proto.thread_is_active(thread)
                turn_id = None
                unturned = False
                if not starting:
                    # The newest turn is enough to tell whether there was one; an active
                    # thread is listed whole, to find the turn it runs.
                    turns = ws.call("thread/turns/list", {"threadId": thread_id}
                                    if active else {"threadId": thread_id, "limit": 1})
                    unturned = proto.no_turns(turns)
                    if active:
                        turn_id = proto.active_turn_id(turns)
                if self._stop.is_set():
                    raise AppServerOffline("the Codex host is stopping")

                with self._lock:
                    old_turn = self.active_turn_id
                    self.ws = ws
                    self.generation = generation
                    self.session_id = thread_id
                    if isinstance(path, str) and path:
                        self.record_path = path
                    self.thread_active = active
                    self.active_turn_id = turn_id
                    if not starting:
                        self._carry_nested(collab)
                    self._collab, self._collab_whole = collab, whole
                    # Sticky: a history that no longer shows the item does not clear it.
                    self._unreadable_spawns = self._unreadable_spawns or unreadable
                    if active:
                        # The load shows Codex running: a status backstop timer set
                        # before the reconnect no longer speaks for it.
                        self._cancel_grace()
                    generation.thread_id = thread_id
                    published = True
                    self._unwritten = starting
                    self._unturned = unturned
                self.endpoint.identify(thread_id)
                self._set_trouble(warning, release=False)
                for notification in pending_notifications:
                    self._on_notification(*notification)
                if starting:
                    # Every fresh thread gets the item, window or not: until something
                    # enters its history it has no rollout, and `thread/resume` refuses
                    # it. The inject belongs to the load: a refusal fails it, and a
                    # windowed launch waits until it is accepted.
                    self._inject_ready_item(ws, thread_id)
                    with self._lock:
                        self._unturned = True
                generation.ready.set()
                self._unwritten = False
                # Only an accepted load raises Ready, so it follows the inject: a refused
                # inject fails the load and raises none. A thread with no turn yet is a
                # start, however it was loaded.
                self.endpoint.identify(
                    thread_id, ready="startup" if starting or unturned else "resume")
                self._accepted.set()
                self._loaded(ws)
                if old_turn and old_turn != turn_id:
                    if turn_id:
                        # The load shows another running turn. Reporting it ends the old
                        # one as `error` in the endpoint, with no idle moment between in
                        # which a delivery held at the gate would start a new turn.
                        with self._lock:
                            self._settled_turns.add(old_turn)
                    else:
                        self.settle_turn(old_turn, "error")
                if turn_id and turn_id not in self._reported_turns:
                    # A list naming an in-progress turn the host has not reported: it
                    # ties a delivery whose turn/start Codex answered and whose
                    # turn/started was lost with the connection.
                    self._report_started(turn_id, None, claim=True, answered_only=True)
                self.endpoint.load_changed()
            except BaseException as exc:
                if self._unwritten:
                    # A refused inject leaves a thread with no rollout, which
                    # `thread/resume` refuses for good. Forget it, so the retry starts a
                    # new thread, as after any failed start; its accepted load
                    # names the new id to the endpoint and in its Ready.
                    with self._lock:
                        if self.session_id == thread_id:
                            self.session_id = None
                self._unwritten = False
                if generation is not None:
                    generation.ended.set()
                if ws is not None:
                    # A failed load closes its connection, published or not:
                    # a refused inject fails the load after publication.
                    with self._lock:
                        if self.ws is ws:
                            self.ws = None
                    ws.close()
                if isinstance(exc, _VerifyFailed):
                    self.app_server.stop()
                    self._settle_dead_turn()
                if self._stop.is_set() and spawned:
                    self.app_server.stop()
                raise

    def _inject_ready_item(self, ws, thread_id: str) -> None:
        """Write a fresh thread's rollout with `READY_ITEM`. A Codex without the call
        raises `CodexTooOld`: no turn is sent in its place."""
        try:
            ws.call("thread/inject_items", proto.inject_params(thread_id, READY_ITEM))
        except proto.CodexError as exc:
            if proto.method_missing(exc):
                raise CodexTooOld(INJECT_MISSING) from exc
            raise

    def _supervise(self) -> None:
        delays = self._backoff_factory()
        wait_before_attempt = self.trouble is not None
        while not self._stop.is_set():
            ws = self.ws
            if self.app_server.alive and ws is not None and ws.connected:
                self._stop.wait(RECONNECT_WAIT)
                continue
            if wait_before_attempt and self._stop.wait(next(delays)):
                return
            try:
                self._connect()
                delays = self._backoff_factory()
                wait_before_attempt = False
            except (codex_hooks.HooksUntrusted, CodexTooOld) as exc:
                # Neither mends on a retry.
                self._load_failed(exc)
                self.endpoint.gone(self.trouble)
                return
            except Exception as exc:  # retry until the host itself is stopped
                self._load_failed(exc)
                wait_before_attempt = True

    def _settle_dead_turn(self) -> None:
        if not self.app_server.alive and self.active_turn_id:
            self.settle_turn(self.active_turn_id, "error")

    def live(self) -> Live:
        """The count derived from `agents()`'s list, read the same way. A terminal list
        that fails leaves the count unknown, never 0; the subagents alone stay known for a
        command with `stop_shells`, which leaves the terminals out."""
        subagents, whole, shells = self._read_agents()
        busy = sum(1 for a in subagents if a.busy) if whole else None
        if busy is None or shells is None:
            return Live(None, False, without_shells=busy)
        return Live(busy + len(shells), False, shells=len(shells))

    def agents(self) -> Agents:
        """The collab subagents as the host recorded Codex's notices, and the background
        terminals as `shell`, read from Codex now. Not complete while the app-server is not
        connected, when a resumed load could not read the history, or when the terminals
        could not be listed; and never again once the session spawned a subagent as a
        ``subAgentActivity`` item (seen live or in the history), whose state turns cannot
        read. The entries it can read still list."""
        subagents, whole, shells = self._read_agents()
        return Agents(tuple(subagents) + tuple(shells or ()),
                      whole and shells is not None, time.time())

    def _read_agents(self) -> tuple[list[Agent], bool, list[Agent] | None]:
        """The subagents, whether they are all of them, and the terminals (None: they
        could not be listed)."""
        with self._lock:
            ws, thread_id = self.ws, self.session_id
            whole = (self._collab_whole and not self._unreadable_spawns
                     and not self._unconfirmed
                     and ws is not None and bool(thread_id))
            subagents = [Agent(agent_id, "subagent", state, final, None, parent)
                         for agent_id, (state, final, parent) in self._collab.items()]
        try:
            shells = [Agent(pid, "shell", "running", False)
                      for pid in self._terminals(ws, thread_id)]
        except Exception as exc:  # noqa: BLE001 - an unread list is unknown, never empty
            log.info("the background terminals could not be listed: %s", exc)
            shells = None
        return subagents, whole, shells

    def _read_collab(self, ws, thread_id: str
                     ) -> tuple[dict[str, tuple[str, bool | None, str | None]], bool, bool]:
        """The collab record rebuilt from the thread's history (`thread/read`), True, and
        whether the history holds a spawn turns cannot read; on any failure the record as
        it stands, False, and False (the flag already set stays set)."""
        try:
            items = proto.thread_items(ws.call("thread/read", proto.read_params(thread_id)))
        except Exception as exc:  # noqa: BLE001 - the record is then not whole
            log.warning("the thread's history could not be read (%s); its subagent list "
                        "is not complete", exc)
            with self._lock:
                return dict(self._collab), False, False
        record: dict[str, tuple[str, bool | None, str | None]] = {}
        unreadable = False
        for item in items:
            _apply_collab(record, item, thread_id)
            unreadable = unreadable or _unreadable_spawn(item)
        if unreadable:
            log.warning("the thread's history holds a subAgentActivity spawn; its subagent "
                        "list is not complete for the session's life")
        return record, True, unreadable

    def _carry_nested(self, collab: dict) -> None:
        """Keep in a rebuilt record the subagents the host tracked from a subagent's own
        thread. The rebuild reads only the session's thread, which never names them; one
        whose last notice was not final may still run, so it stays unconfirmed. Called
        under the lock."""
        carried = {agent_id: entry for agent_id, entry in self._collab.items()
                   if entry[2] is not None and agent_id not in collab}
        collab.update(carried)
        self._unconfirmed = {agent_id for agent_id, entry in carried.items()
                             if entry[1] is not True}

    def _track_collab(self, params: dict) -> None:
        """An item's start or end: a collab tool call's notices update the record. Items
        of the session's thread count, and those of a subagent's own thread (its spawns)."""
        item = params.get("item")
        if not isinstance(item, dict) or item.get("type") not in (
                proto.COLLAB_ITEM, proto.SUBAGENT_ACTIVITY_ITEM):
            return
        thread = params.get("threadId")
        with self._lock:
            main = self.session_id
            if isinstance(thread, str) and thread != main and thread not in self._collab:
                return
            if _unreadable_spawn(item):
                if not self._unreadable_spawns:
                    log.warning("a subagent spawned as a subAgentActivity item; the "
                                "subagent list is not complete for the session's life")
                self._unreadable_spawns = True
                return
            _apply_collab(self._collab, item, main)
            self._unconfirmed.difference_update(
                agent_id for agent_id, status, _ in proto.collab_notices(item)
                if status is not None)

    def submit(self, delivery: Delivery) -> None:
        threading.Thread(
            target=self._submit, args=(delivery,), daemon=True,
            name="turns-codex-submit",
        ).start()

    def _submit(self, delivery: Delivery) -> None:
        reason = self.endpoint.entering(delivery.id)
        if reason is None and (delivery.refuse_agents or delivery.refuse_draft):
            live = self.live()
            if delivery.refuse_agents and live.busy():   # an unknown count is busy
                reason = "busy-agents"
            elif delivery.refuse_draft and live.draft:
                reason = "draft"
        if reason is not None:
            self.endpoint.refused(delivery.id, reason)
            return
        try:
            turn_id = self._start_turn(delivery.text, delivery.id)
        except proto.NoReply as exc:
            # The request went out and Codex may have taken it: not a refusal.
            if not self._drop_pending(delivery.id):
                # A turn/started claimed it before the answer was lost: Codex named the
                # turn that took the text, so the delivery stays on it.
                log.warning("Codex did not answer delivery %s: %s; its turn/started "
                            "already tied it", delivery.id, exc)
                return
            log.warning("Codex did not answer delivery %s: %s; it reads lost",
                        delivery.id, exc)
            self.endpoint.lost(delivery.id, _no_reply(exc))
            return
        except Exception as exc:
            # Never sent, or Codex answered it with an error.
            self._drop_pending(delivery.id)
            log.warning("Codex refused delivery %s: %s", delivery.id, exc)
            self.endpoint.refused(delivery.id, "unsupported")
            return
        if turn_id is None:
            log.warning("Codex took delivery %s's turn/start but named no turn the host "
                        "could tie it to; it reads lost", delivery.id)
            self.endpoint.lost(delivery.id, UNTIED)

    def _drop_pending(self, delivery_id: str) -> bool:
        """Free the waiting slot if `delivery_id` still holds it. False when a reported
        turn has already claimed the delivery."""
        with self._lock:
            if self._pending_delivery == delivery_id:
                self._pending_delivery = None
                return True
            return False

    def steering(self, delivery_id: str) -> None:
        """A steer is being recorded, under the endpoint's lock: from here a
        ``turn/completed`` waits for it. No harness call, and the host never calls the
        endpoint while it holds its own lock, so the two locks cannot deadlock."""
        with self._lock:
            self._steering.add(delivery_id)

    def append(self, delivery: Delivery) -> None:
        """On the endpoint's worker: ``turn/steer`` the text into the running turn and
        report what Codex said. Blocks the worker until Codex answers, at most the
        app-server client's request bound."""
        with self._lock:
            self._steering.add(delivery.id)
        try:
            self._inject(delivery.text)
        except (proto.NoActiveTurn, _TurnEnding):
            # Codex has no turn to steer, or its turn is ending: the text never went out.
            self.endpoint.refused(delivery.id, "idle")
        except proto.NoReply as exc:
            log.warning("Codex did not answer steer %s: %s; it reads lost",
                        delivery.id, exc)
            self.endpoint.lost(delivery.id, _no_reply(exc))
        except Exception as exc:
            log.warning("Codex could not steer delivery %s: %s", delivery.id, exc)
            self.endpoint.refused(delivery.id, "unsupported")
        else:
            self.endpoint.steered(delivery.id)
        finally:
            self._release(delivery.id)

    def expired(self, delivery_id: str) -> None:
        """The endpoint settled a steer without the host's word (its bound), or its worker
        skipped one that had settled: stop holding ``turn/completed`` for it."""
        self._release(delivery_id)

    def _release(self, delivery_id: str) -> None:
        """Drop a steer from ``_steering``; once none is left, report the completions held
        for it."""
        with self._lock:
            self._steering.discard(delivery_id)
            pending = self._pending_completions if not self._steering else []
            if pending:
                self._pending_completions = []
        for params in pending:
            self._turn_completed(params)

    def command(self, request: CommandRequest) -> None:
        """On the endpoint's worker: ``compact`` through ``thread/compact/start``. Blocks
        the worker until Codex answers the send, so a hand-off recorded after the command
        goes out after it. ``clear`` and ``exit`` are not offered."""
        if request.name != "compact":
            self.endpoint.command_settled(request.id, Failed("unsupported"))
            return
        with self._lock:
            thread_id, ws = self.session_id, self.ws
        if ws is None or not thread_id or self.loading():
            self.endpoint.command_settled(request.id, Refused("loading"))
            return
        listed = None
        if request.stop_shells:
            # Nothing is stopped yet. A Codex without the list call turns the stop down;
            # a list that fails otherwise is reported once the compaction has run.
            try:
                listed = self._terminals(ws, thread_id)
            except Exception as exc:  # noqa: BLE001 - every failure is reported
                if proto.method_missing(exc):
                    log.warning("Codex has no background terminal list (%s); /compact "
                                "with stop_shells refuses unsupported", exc)
                    self.endpoint.command_settled(request.id, Refused("unsupported"))
                    return
                log.warning("the background terminals could not be listed before "
                            "/compact (%s); none will be stopped", exc)
        # The re-check, just before the send: the ledger, the turn Codex runs, and the
        # live facts (a stop_shells command leaves the terminals out).
        if self.endpoint.command_entering(request.id) is not None:
            return
        with self._lock:
            running = self.active_turn_id is not None
        if running:
            self.endpoint.command_settled(request.id, Refused("busy-turn"))
            return
        if self.live().busy(shells=not request.stop_shells):
            self.endpoint.command_settled(request.id, Refused("busy-agents"))
            return
        compaction = _Compaction(request, listed, time.monotonic())
        with self._lock:
            self._compacting = compaction
        try:
            ws.call("thread/compact/start", proto.compact_params(thread_id))
        except proto.NoReply as exc:
            # The request went out and Codex may be compacting: its Ready settles the
            # command, or the core's bound does.
            log.warning("Codex did not answer /compact %s: %s", request.id, exc)
        except Exception as exc:  # noqa: BLE001 - the command settles on every road
            with self._lock:
                if self._compacting is compaction:
                    self._compacting = None
            if proto.method_missing(exc):
                result = Refused("unsupported")
            elif isinstance(exc, proto.CodexError):
                result = Failed(f"Codex refused the compaction: {exc}")
            elif isinstance(exc, proto.AppServerOffline):
                result = Refused("loading")     # never sent
            else:
                result = Failed(f"the compaction could not be sent: "
                                f"{self._failure_reason(exc)}")
            log.warning("/compact %s did not run: %s", request.id, exc)
            self.endpoint.command_settled(request.id, result)

    def compacted(self) -> None:
        """A Ready with source ``compact`` was raised (the core settles a plain
        compaction there). The compaction the host sent has taken effect: with
        ``stop_shells`` the stop begins, on a thread of its own, so the hook that raised
        the Ready is answered at once."""
        with self._lock:
            compaction, self._compacting = self._compacting, None
        if compaction is None or not compaction.request.stop_shells:
            return
        threading.Thread(target=self._stop_shells, args=(compaction,), daemon=True,
                         name="turns-codex-stop-shells").start()

    def _stop_shells(self, compaction: _Compaction) -> None:
        request = compaction.request
        deadline = time.monotonic() + SHELL_STOP_S
        if not self.endpoint.command_took_effect(request.id):
            log.info("/compact %s settled before its Ready; no terminal is stopped",
                     request.id)
            return
        try:
            shells = self._stop_terminals(compaction.listed, deadline)
        except Exception:  # noqa: BLE001 - an unread stop is reported as unvouched
            log.exception("stopping the background terminals failed")
            shells = None
        self.endpoint.command_ran(request.id, shells=shells)

    def _terminals(self, ws, thread_id: str | None,
                   deadline: float | None = None) -> list[str]:
        """The background terminals' process ids, read from Codex. Raises on any failure,
        a list of another shape included: never an empty list in place of an error."""
        if ws is None or not thread_id:
            raise proto.AppServerOffline("the Codex app-server is not connected")
        timeout = TERMINALS_WAIT
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
            if timeout <= 0:
                raise proto.NoReply("no time was left to list the background terminals")
        result = ws.call("thread/backgroundTerminals/list",
                         proto.terminals_params(thread_id), timeout=timeout)
        return proto.terminal_ids(result)

    def _stop_terminals(self, listed: list[str] | None, deadline: float) -> Shells:
        """Terminate each background terminal and read what is left, by `deadline`."""
        if listed is None:
            # No list before the command: nothing is stopped, and the report says so.
            return Shells((), (LeftShell(None, SHELL_UNLISTED),), False)
        with self._lock:
            thread_id, ws = self.session_id, self.ws
        complete = True
        try:
            # Read as the stop begins, after the compaction took effect.
            ids = self._terminals(ws, thread_id, deadline)
        except Exception as exc:  # noqa: BLE001
            log.warning("the background terminals could not be listed as the stop "
                        "began (%s); the list from before /compact is used", exc)
            ids, complete = list(listed), False
        terminated: dict[str, bool] = {}
        why: dict[str, str] = {}
        for process_id in ids:
            if process_id in why:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0 or ws is None:
                why[process_id] = ("timeout" if remaining <= 0
                                   else "the Codex app-server is not connected")
                continue
            try:
                result = ws.call("thread/backgroundTerminals/terminate",
                                 proto.terminate_params(thread_id, process_id),
                                 timeout=remaining)
            except Exception as exc:  # noqa: BLE001 - each entry reports its own words
                if proto.method_missing(exc):
                    for rest in ids:
                        if rest not in terminated:
                            why.setdefault(rest, "unsupported")
                    break
                why[process_id] = ("timeout" if isinstance(exc, proto.NoReply)
                                   and time.monotonic() >= deadline
                                   else self._failure_reason(exc))
                continue
            terminated[process_id] = (isinstance(result, dict)
                                      and result.get("terminated") is True)
        try:
            still: set[str] | None = set(self._terminals(ws, thread_id, deadline))
            list_error = None
        except Exception as exc:  # noqa: BLE001
            still = None
            list_error = ("timeout" if time.monotonic() >= deadline
                          else self._failure_reason(exc))
            log.warning("the background terminals could not be listed after the stop: "
                        "%s", list_error)
        stopped: list[str] = []
        left: list[LeftShell] = []
        for process_id in ids:
            if still is None:
                # No second list: Codex's own answer decides.
                if terminated.get(process_id):
                    stopped.append(process_id)
                else:
                    left.append(LeftShell(process_id, why.get(process_id) or list_error))
            elif process_id in still:
                left.append(LeftShell(process_id, why.get(process_id) or (
                    "still listed after terminate" if terminated.get(process_id)
                    else "Codex did not terminate it")))
            elif terminated.get(process_id):
                stopped.append(process_id)
            # Otherwise it ended by itself: in neither list.
        for process_id in sorted((still or set()) - set(ids)):
            left.append(LeftShell(process_id, "started during the stop"))
        if not complete:
            left.append(LeftShell(None, SHELL_UNLISTED))
        return Shells(tuple(stopped), tuple(left), complete)

    def _inject(self, text: str) -> str | None:
        with self._lock:
            thread_id, expected, ws = self.session_id, self.active_turn_id, self.ws
        if not thread_id or ws is None:
            raise RuntimeError("the Codex app-server is not connected")
        for _ in range(STEER_ATTEMPTS):
            with self._lock:
                if expected is not None and expected == self._ending:
                    # Its Stop was answered: Codex may already be completing it.
                    raise _TurnEnding(expected)
                self._steers_out += 1
            try:
                result = ws.call("turn/steer", proto.steer_params(thread_id, expected, text))
            except proto.ActiveTurnMismatch as mismatch:
                expected = mismatch.found
                with self._lock:
                    self.active_turn_id = expected
                continue
            finally:
                with self._lock:
                    self._steers_out -= 1
                    self._steers_idle.notify_all()
            turn_id = result.get("turnId")
            with self._lock:
                self.active_turn_id = turn_id
            return turn_id
        log.warning("gave up steering thread %s after %d attempts", thread_id,
                    STEER_ATTEMPTS)
        raise RuntimeError(
            f"the active Codex turn changed through {STEER_ATTEMPTS} steer attempts"
        )

    def _start_turn(self, text: str, delivery_id: str) -> str | None:
        with self._lock:
            thread_id, ws = self.session_id, self.ws
            self._pending_delivery = delivery_id
            self._pending_answered = False
            self._started_turn = None
            self._started.clear()
        if not thread_id or ws is None:
            raise RuntimeError("the Codex app-server is not connected")
        # A raise here leaves the request unsent (AppServerOffline), unanswered (NoReply)
        # or answered with an error (CodexError); the caller reads which.
        result = ws.call("turn/start", proto.start_params(thread_id, text))
        with self._lock:
            if self._pending_delivery == delivery_id:
                self._pending_answered = True
        claimed = (result.get("turn") or {}).get("id")
        if self._started.wait(TURN_STARTED_WAIT):
            with self._lock:
                turn_id = self._started_turn
                # A delivery counts as started only once a report claimed it. A turn a
                # reconnect's load reported before turn/start answered wakes the wait
                # without a claim; the list below decides that case.
                tied = self._pending_delivery != delivery_id
            if tied:
                if claimed and claimed != turn_id:
                    log.warning("turn/start returned phantom id %s; the live turn is %s",
                                claimed, turn_id)
                return turn_id
        # turn/start answered and no turn/started came: Codex took the text, perhaps into
        # a turn that was still running (it answers such a start with a phantom id). Only
        # a list naming an in-progress turn the host has not reported can tie it.
        try:
            turns = ws.call("thread/turns/list", {"threadId": thread_id})
        except Exception as exc:
            log.warning("listing turns for delivery %s failed after Codex took its "
                        "text: %s", delivery_id, exc)
            turns = None
        turn_id = proto.active_turn_id(turns)
        if turn_id and self._report_started(turn_id, delivery_id):
            return turn_id
        with self._lock:
            if self._pending_delivery == delivery_id:
                # Untied. The list named no turn, or one already reported: reporting
                # it again would close it as `error` in the endpoint. Since only Codex
                # ends a turn, the second happens only when the ledger closed a turn
                # Codex still runs (the status backstop misfiring); either way the
                # caller reads lost.
                self._pending_delivery = None
                return None
            # A turn/started came after the wait and tied the delivery.
            return self._started_turn

    def _report_started(self, turn_id: str, delivery_id: str | None = None, *,
                        claim: bool = False, answered_only: bool = False) -> bool:
        """Report a turn Codex started, tied to `delivery_id` while that delivery is
        still the one waiting (or, with `claim`, to whichever is; with `answered_only`
        too, only once its turn/start has answered, since before that the text may not
        have gone out). A turn no delivery took while a compaction the host sent waits
        for its Ready is that compaction's. False when the turn was already reported, and
        then nothing is said: a second `turn_started` for it would close it as
        `error`."""
        with self._lock:
            if turn_id in self._reported_turns:
                return False
            self._reported_turns.add(turn_id)
            self._cancel_grace()
            self.active_turn_id = turn_id
            self.thread_active = True
            if claim:
                delivery_id = self._pending_delivery
                if answered_only and not self._pending_answered:
                    delivery_id = None
            if delivery_id is not None and self._pending_delivery != delivery_id:
                delivery_id = None   # settled meanwhile: the turn counts as the person's
            if delivery_id is not None:
                self._pending_delivery = None
            command = None
            compaction = self._compacting
            if (delivery_id is None and compaction is not None
                    and compaction.turn_id is None
                    and time.monotonic() - compaction.sent_at
                    < getattr(self.endpoint, "pending_ttl_s", 300.0)):
                compaction.turn_id, command = turn_id, "compact"
            self._started_turn = turn_id
            self._started.set()
        if command is None:
            self.endpoint.turn_started(turn_id, delivery_id)
        else:
            self.endpoint.turn_started(turn_id, command=command)
        return True

    def _other_thread(self, method: str, params) -> bool:
        """Whether a turn or status notification names a thread other than the
        session's: a spawned subagent's own thread, whose turns run on the same
        connection and are never the session's turns. One that names no thread counts
        as the session's."""
        thread = params.get("threadId") if isinstance(params, dict) else None
        with self._lock:
            main = self.session_id
        if not isinstance(thread, str) or main is None or thread == main:
            return False
        self.foreign_hook(method, thread)
        return True

    def _on_notification(self, method: str, params: dict, rid=None) -> None:
        if (method in ("turn/started", "turn/completed", "thread/status/changed")
                and self._other_thread(method, params)):
            return
        if method == "turn/started":
            turn = params.get("turn") or {}
            turn_id = turn.get("id")
            if not turn_id:
                return
            self._report_started(turn_id, claim=True)
        elif method == "turn/completed":
            self._completed(params)
        elif method in ("item/started", "item/completed"):
            self._track_collab(params)
        elif method == "thread/tokenUsage/updated":
            self._token_usage(params)
        elif method == LIMITS_UPDATED:
            if isinstance(params, dict):
                self._limits_seen(params, "updated")
        elif method == STARTUP_STATUS:
            if isinstance(params, dict) and params.get("threadId") in (None,
                                                                      self.session_id):
                self.mcp.notified(params)
        elif method == "thread/status/changed":
            active = (params.get("status") or {}).get("type") == "active"
            with self._lock:
                self.thread_active = active
                if active:
                    self._cancel_grace()
                elif (self.active_turn_id is not None and self._grace is None
                        and self.active_turn_id not in self._settled_turns):
                    # Codex says the thread is idle while the ledger holds a turn open.
                    # Its turn/completed normally follows; if none comes within the
                    # grace, the turn ends `error` (the status backstop).
                    turn_id = self.active_turn_id
                    timer = threading.Timer(TURN_COMPLETED_GRACE, self._grace_expired,
                                            args=(turn_id,))
                    timer.daemon = True
                    timer.name = "turns-codex-grace"
                    self._grace = (turn_id, timer)
                    timer.start()
        elif rid is not None:
            log.warning("unanswered app-server request %s; approvals are disabled", method)

    def _loaded(self, ws) -> None:
        """An accepted load, which raised its `ready`: read the account's rate limits
        once, and settle the MCP servers, each on a thread of its own, so the load waits
        for neither."""
        threading.Thread(target=self._read_limits_at_load, args=(ws,), daemon=True,
                         name="turns-codex-limits").start()
        self.mcp.ready()

    def mcp_ready(self) -> None:
        """A `ready` a hook raised (a compaction's `PostCompact`, and its `SessionStart`
        at the next turn): settle the MCP servers, or join the settle that runs."""
        self.mcp.ready()

    def mcp_reload(self) -> dict:
        """`mcp.reload`: settle the MCP servers and answer `{reloaded, servers}`."""
        return self.mcp.reload()

    def _app_call(self, method: str, params, timeout: float):
        """One request on the current app-server connection."""
        with self._lock:
            ws = self.ws
        if ws is None:
            raise proto.AppServerOffline("the Codex app-server is not connected")
        return ws.call(method, params, timeout=timeout)

    def _read_limits_at_load(self, ws) -> None:
        try:
            payload = ws.call(LIMITS_READ, None, timeout=LIMITS_READ_S)
        except Exception as exc:  # noqa: BLE001 - a reading is information; none is logged
            log.info("the Codex rate limits could not be read after the load: %s", exc)
            return
        if isinstance(payload, dict):
            self._limits_seen(payload, "read")

    def _limits_seen(self, payload: dict, kind: str, *, tell: bool = True) -> float:
        """Keep a rate-limits reading as the last one seen, and hand it to the
        `codex_limits` rows unparsed (unless `tell` is false). Returns its time."""
        at = time.time()
        with self._lock:
            self._limits = (payload, at, kind)
        if tell:
            self.endpoint.codex_limits(payload, kind=kind, received_at=at)
        return at

    def codex_limits(self, *, cached: bool = False) -> dict:
        """`usage.codex_limits`: `{payload, received_at}` from `account/rateLimits/read`
        now, or `{payload: null, received_at: null, reason}` when it could not be read.
        With `cached`, the last reading the host saw (`{payload, received_at, kind}`, each
        null before the first) and no request. A reading asked for here is kept as the
        last one seen but not handed to the rows, which are told of the load's reading
        and of Codex's pushes."""
        if cached:
            with self._lock:
                last = self._limits
            payload, at, kind = last if last is not None else (None, None, None)
            return {"payload": payload, "received_at": at, "kind": kind}
        with self._lock:
            ws = self.ws
        try:
            if ws is None:
                raise proto.AppServerOffline("the Codex app-server is not connected")
            payload = ws.call(LIMITS_READ, None, timeout=LIMITS_READ_S)
            if not isinstance(payload, dict):
                raise proto.AppServerOffline(f"{LIMITS_READ} answered {payload!r}")
        except Exception as exc:  # noqa: BLE001 - the caller reads the reason
            reason = self._failure_reason(exc)
            log.info("the Codex rate limits could not be read: %s", reason)
            return {"payload": None, "received_at": None, "reason": reason}
        return {"payload": payload,
                "received_at": self._limits_seen(payload, "read", tell=False)}

    def _token_usage(self, params) -> None:
        """Keep the main thread's last context reading, `tokenUsage.last.totalTokens`;
        another thread's (a subagent's) is not the main loop's context."""
        if not isinstance(params, dict):
            return
        usage = params.get("tokenUsage")
        last = usage.get("last") if isinstance(usage, dict) else None
        total = last.get("totalTokens") if isinstance(last, dict) else None
        if isinstance(total, bool) or not isinstance(total, int):
            return
        thread = params.get("threadId")
        with self._lock:
            if not isinstance(thread, str) or thread != self.session_id:
                return
            self._usage = (thread, total)

    def _completed(self, params: dict) -> None:
        """A turn's end, by Codex's word: held while a steer recorded by the endpoint
        is unsettled, so that steer is taken into the turn (or refused) before the end is
        reported, and ``turn.ended`` lists it."""
        with self._lock:
            if self._steering:
                self._pending_completions.append(params)
                return
        self._turn_completed(params)

    def _cancel_grace(self) -> None:
        """Under the lock: stop the status backstop's timer, if one runs."""
        if self._grace is not None:
            self._grace[1].cancel()
            self._grace = None

    def _grace_expired(self, turn_id: str) -> None:
        with self._lock:
            if self._grace is None or self._grace[0] != turn_id:
                return
            self._grace = None
            if self.active_turn_id != turn_id or turn_id in self._settled_turns:
                return
        log.warning("Codex went idle without completing turn %s within %g s; it ends "
                    "as error", turn_id, TURN_COMPLETED_GRACE)
        # Should Codex resume the turn after all, the ledger is closed while it runs:
        # a delivery made then is absorbed into it, meets the reported-turn guard in
        # `_start_turn` and reads lost. A duplicate at worst.
        self._completed({"turn": {"id": turn_id, "status": "failed"}})

    def _turn_completed(self, params: dict) -> None:
        turn = params.get("turn") or {}
        status = turn.get("status")
        outcome = {"completed": "answer", "interrupted": "aborted",
                   "failed": "error"}.get(status, "refusal")
        self.settle_turn(turn.get("id"), outcome)

    def stop_answered(self, turn_id: str | None) -> None:
        """The hold did not block the Stop of `turn_id`, and its answer is about to go
        out: Codex may complete the turn at any moment after it. Mark the turn ending
        here and in the ledger, so no steer for it goes out from now on, and let a
        ``turn/steer`` already sent be answered first, so it reaches Codex before the
        Stop answer does. The turn stays open until Codex ends it."""
        with self._lock:
            turn = turn_id or self.active_turn_id
            if (turn is None or turn != self.active_turn_id
                    or turn in self._settled_turns):
                return
            self._ending = turn
        self.endpoint.turn_ending(turn)
        with self._lock:
            if not self._steers_idle.wait_for(lambda: self._steers_out == 0,
                                              STEER_DRAIN_WAIT):
                log.warning("a turn/steer for turn %s was not answered within %g s of its "
                            "Stop; the Stop is answered anyway", turn, STEER_DRAIN_WAIT)

    def settle_turn(self, turn_id: str | None, outcome: str) -> None:
        with self._lock:
            actual = turn_id or self.active_turn_id
            if actual is None or actual in self._settled_turns:
                return
            self._settled_turns.add(actual)
            self._unturned = False   # a turn has run
            if self._grace is not None and self._grace[0] == actual:
                self._cancel_grace()
            if self.active_turn_id == actual:
                self.active_turn_id = None
            if self._ending == actual:
                self._ending = None
            self.thread_active = False
        self.endpoint.turn_ended(actual, outcome)

    def stop_facts(self, frame: dict) -> StopFacts:
        task_types = frame.get("task_types")
        crons = frame.get("crons")
        if task_types is None:
            ws, thread_id = self.ws, self.session_id
            try:
                result = ws.call(
                    "thread/backgroundTerminals/list", {"threadId": thread_id},
                    timeout=2.0,
                )
                data = result.get("data") if isinstance(result, dict) else None
                if isinstance(data, list):
                    task_types = tuple("background-terminal" for _ in data)
            except Exception:
                task_types = None
        return StopFacts(
            turn_id=frame.get("turn_id") if isinstance(frame.get("turn_id"), str) else None,
            session_id=self.session_id,
            stop_hook_active=frame.get("stop_hook_active")
            if isinstance(frame.get("stop_hook_active"), bool) else None,
            task_types=tuple(task_types) if isinstance(task_types, (list, tuple)) else None,
            crons=crons if isinstance(crons, int) else 0,
        )

    def start_source(self, source: str | None) -> str | None:
        """A main-thread `SessionStart`'s source as the listeners are asked with it: a
        `resume` reads `startup` while the thread is unturned."""
        with self._lock:
            return "startup" if source == "resume" and self._unturned else source

    def start_answered(self) -> None:
        """The hook server answered the thread's first `SessionStart` since the load: the
        thread is no longer unturned."""
        with self._lock:
            self._unturned = False

    def foreign_hook(self, event: str, session_id) -> None:
        with self._lock:
            if session_id in self._foreign_threads:
                return
            self._foreign_threads.add(session_id)
        log.info("ignoring %s from another Codex thread %r (logged once per thread)",
                 event, session_id)

    def window_closed(self, status: int | None) -> None:
        self.endpoint.gone(f"the Codex window closed with status {status or 0}")

    def window_failed(self, reason: str) -> None:
        self._set_trouble(reason)
        self.endpoint.gone(reason)

    def generation_answers(self, generation: Generation, timeout: float) -> bool:
        ws = self.ws
        if (generation is not self.generation or generation.ended.is_set()
                or ws is None or not generation.thread_id):
            return False
        try:
            ws.call("thread/turns/list", {"threadId": generation.thread_id, "limit": 1},
                    timeout=timeout)
            return True
        except Exception:
            return False

    def drop_generation(self, generation: Generation) -> None:
        generation.ended.set()
        ws = self.ws
        if generation is self.generation and ws is not None:
            ws.close()

    def give_up_reason(self) -> str:
        if self.trouble:
            return self.trouble
        generation = self.generation
        if generation is None:
            return "the host never connected to its Codex app-server"
        if generation.thread_id and not generation.ready.is_set():
            return (f"Codex did not take the opening item into the fresh thread "
                    f"{generation.thread_id}, so its window could not attach")
        return "the Codex app-server connection never became ready"

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        with self._lock:
            self._cancel_grace()
        self.mcp.close()
        with self._connect_lock:
            generation = self.generation
            if generation is not None:
                generation.ended.set()
            if self.window_session is not None:
                self.window_session.stop()
            if self.ws is not None:
                self.ws.close()
                self.ws = None
            if self.hook_server is not None:
                self.hook_server.stop()
            if self.app_server is not None:
                self.app_server.stop()
            if self.guards is not None:
                self.guards.close()
            with contextlib.suppress(OSError):
                self.endpoint.dir.rmdir()
        if (self._supervisor is not None
                and self._supervisor is not threading.current_thread()):
            self._supervisor.join(timeout=2.0)


def _text(value) -> str | None:
    """`value` when it is a non-empty string, else None."""
    return value if isinstance(value, str) and value else None


def _apply_collab(record: dict, item, main: str | None) -> None:
    """Apply a collab item's notices to `record`, in order. An agent's latest status is
    its state; one named with no status yet is pending, with no notice to call final.
    Its parent is the thread that first named it, when that is not the session's own."""
    for agent_id, status, sender in proto.collab_notices(item):
        if agent_id == main:
            continue
        prev = record.get(agent_id)
        if status is not None:
            state, final = proto.collab_state(status)
        elif prev is not None:
            state, final = prev[0], prev[1]
        else:
            state, final = "pending", None
        parent = prev[2] if prev is not None else (
            sender if sender and sender != main else None)
        record[agent_id] = (state, final, parent)


def _unreadable_spawn(item) -> bool:
    """A ``subAgentActivity`` item: Codex's ``multi_agent_v2`` spawn, whose subagent's
    state turns cannot follow."""
    return isinstance(item, dict) and item.get("type") == proto.SUBAGENT_ACTIVITY_ITEM


def _no_reply(exc: BaseException) -> str:
    return f"untied: Codex was sent the text and did not answer ({exc})"


_HOSTS: list[tuple[Endpoint, CodexHarness]] = []
_HOSTS_LOCK = threading.Lock()


def launch(cwd, *, instructions: str | None = None, env=None,
           window: bool = True, options=None, verify=None, additions=None,
           resume: str | None = None):
    """Launch a hosted Codex thread and return its live turns ``Session``.

    ``options`` are the thread parameters each load sends: top-level ``sandbox`` or
    ``permissions`` and ``model``, and a nested ``config`` of Codex config overrides.
    ``additions`` is the launch's ``LaunchAdditions`` for ``codex``; without it the host
    asks ``launch_additions`` once itself, and with it asks nothing. Its variables go
    over ``env`` and its options under ``options``. A key turns owns in either is refused
    with ``ValueError``. ``resume`` names a thread to resume instead of starting one.

    ``verify(answer) -> str | None`` checks each load's answer; a reason fails a resume
    (the app-server is stopped and the load retried) and is logged for a new thread.
    Without it, options that name a ``sandbox`` are checked by ``verify_options``.

    The host runs inside the calling process until that process ends. The entry
    ``moxi-turns-codex-host`` runs it as a process of its own; ``launch_args`` turns
    that entry's flags into this call's keywords.
    """
    return _launch(cwd, instructions=instructions, env=env, window=window,
                   options=options, verify=verify, additions=additions,
                   resume=resume)[0]


def _launch(cwd, *, instructions=None, env=None, window=True, options=None, verify=None,
            additions=None, resume=None):
    """`launch`, returning the endpoint beside the session, for the host entry."""
    from .additions import for_host

    path = Path(cwd).resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"Codex working directory does not exist: {path}")
    env, options = for_host("codex", additions, cwd=path, env=env, options=options)
    harness = CodexHarness(path, instructions=instructions, env=env, window=window,
                           options=options, verify=verify, resume=resume)
    endpoint = Endpoint(harness, env=harness.env).open()
    try:
        harness.start()
        session = (turns_client.find(session_id=harness.session_id)
                   if harness.session_id else turns_client.find(pid=harness.pid))
        if session is None:
            raise RuntimeError("the Codex endpoint opened but could not be found")
    except BaseException:
        endpoint.stop("the Codex launch failed")
        raise
    with _HOSTS_LOCK:
        _HOSTS.append((endpoint, harness))
    return session, endpoint


def launch_args(argv) -> dict:
    """The keywords of `launch` (``cwd``, ``options``, ``instructions``, ``resume``,
    ``window``) from the flags of ``moxi-turns-codex-host``. A bad flag raises
    ``moxi_turns.codex_host.UsageError``, a ``ValueError``."""
    from .codex_host import launch_args as parse

    return parse(argv)


def rows() -> list[moxi_doctor.Row]:
    """The Codex host's doctor rows. The host is optional: a missing extra or binary is
    `off`; a missing hook command in an installed turns is a fault."""
    from .codex_host import EXTRA_IMPORT, codex_host_available

    installed = codex_host_available()
    output = [moxi_doctor.Row(
        MODULE, "Codex extra", "ok" if installed else "off",
        f"{EXTRA_IMPORT} is installed" if installed else "the Codex host is not installed",
        None if installed else "moxi install turns --with codex")]
    binary = shutil.which(BINARY)
    output.append(moxi_doctor.Row(MODULE, "Codex binary", "ok" if binary else "off",
                                  binary or "codex is not on PATH"))
    try:
        ping = script("moxi-turns-ping")
    except FileNotFoundError as exc:
        output.append(moxi_doctor.Row(MODULE, "Codex hook command", "fail", str(exc)))
    else:
        output.append(moxi_doctor.Row(MODULE, "Codex hook command", "ok", str(ping)))
    return output


def _close_hosts() -> None:
    with _HOSTS_LOCK:
        hosts = list(_HOSTS)
        _HOSTS.clear()
    for endpoint, _ in hosts:
        endpoint.stop("the launching process ended")


atexit.register(_close_hosts)

__all__ = ["OWNED_OPTIONS", "SANDBOX_POLICY_TYPES", "launch", "launch_args",
           "verify_options"]
