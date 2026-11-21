"""comms' listener: the context a session `moxi comms claude` launched gets from comms.

turns asks it in the session's endpoint process (`moxi_comms.listeners` registers it),
with the session's facts and the environment the endpoint inherited from the session,
which carries the wrapper's identity stamps. What it answers is what the roster hooks
printed when comms wrote its own hooks (`adapters/shim_roster`):

    start    layer one: the context block, the line naming which comms to run (the
             command the wrapper stamped, `MOXI_COMMS_COMMAND`), then the roster
             snapshot (or the offline line)
    prompt   the roster's joins and leaves since this session last looked, and each
             ignored reminder naming this session as its author, once; or nothing

At either event, a session whose adapter is gone while it runs gets a new one
(`respawn_gone_adapter`), which binds the same name again with no relaunch, and one line
that says so, before the start's layer one and roster or the prompt's deltas, which it
gets as any session does. When no new adapter can be started, or its bind is refused,
the line says that mail has stopped and why. The daemon replaces a gone adapter first,
at once (`adapters.respawn`), and that adapter leaves the line in a one-shot note in the
session's runtime directory; the next start or prompt prints it in the same place, once
(`take_note`), unless the event started a new adapter itself and has a line of its own.

**comms' tools.** A session whose launch carries comms' MCP entry (the wrapper stamped
the token and the URL, `moxi_comms.mcp.additions.carries`) gets layer one's tools form at
`start` (`context.layer_one_form`): the tools taught as the way to send and read, the CLI
named last as the fallback, or, when its shell can't reach comms, not at all. The event's
environment is the launch's, so the form follows only what the launch stamped. A Codex
thread and an OpenCode session get the same form in the layer one their wrappers write.

A session comms did not launch has no identity stamp, and comms binds no such session, so
the listener answers None there. It answers only for Claude Code sessions: a Codex thread
gets layer one as developer instructions at its launch.

**The roster memory.** The deltas diff against what this session last saw, kept in
`roster.json` in the session's runtime directory, the one named for the key of the claim
socket the wrapper stamped (`MOXI_COMMS_CLAIM_SOCKET`): `claim-<session id>`. `start`
seeds it on a roster it could read; a session with no such directory gets no deltas,
since deltas without memory would announce the world every prompt.

**Time.** turns bounds every listener of an event together by `listen.wait_ms` (2000 ms
by default) and gives the event no context from a listener that is late. The whole answer
here is bounded at `QUERY_TIMEOUT`, below that default: the daemon query first, then a
respawn's wait for its word in what is left, so a slow respawn costs the line's news,
never layer one. turns' library gives a caller no
way to read the setting, so the bound is fixed, and `docs/configuration.md` asks for a
`listen.wait_ms` above it.
"""

from __future__ import annotations

from .constants import NAME_ENV

import os
import time
from pathlib import Path

#: The harnesses this listener answers for.
HARNESSES = ("claude",)
#: The answer's bound, inside turns' default `listen.wait_ms` of 2 s. A box that sets
#: `listen.wait_ms` at or below it loses a late answer (docs/configuration.md).
QUERY_TIMEOUT = 1.5
#: The session's roster memory, beside its claim socket.
MEMORY = "roster.json"


def memory_path(env) -> Path | None:
    """The session's roster memory file, or None when the session has no runtime
    directory of comms' own (`session_dir_of`)."""
    session_dir = session_dir_of(env)
    return None if session_dir is None else session_dir / MEMORY


def session_dir_of(env) -> Path | None:
    """The session's runtime directory, or None when it has none of comms' own (a
    session directory the wrapper made, named for its claim socket's key).

    The key is read from the socket's name, `claim-<key>.sock` in RUNTIME/comms, or,
    for a short path the core gave a socket whose path there was too long, from the
    claim file that records it (the Claude adapter's is keyed by the session id)."""
    from .adapters.session import sessions_dir
    from .claimhold import CLAIM_SOCKET_ENV, checked_socket, record_at
    from .paths import OutsideRuntime, runtime_dir

    value = env.get(CLAIM_SOCKET_ENV)
    if not value:
        return None
    try:
        sock = checked_socket(value, env)
    except OutsideRuntime:
        return None
    name = sock.name
    if os.path.realpath(sock.parent) == os.path.realpath(runtime_dir(env)):
        key = (name[len("claim-"):-len(".sock")]
               if name.startswith("claim-") and name.endswith(".sock") else None)
    else:
        found = record_at(sock, env)
        key = found[0] if found is not None else None
    if not key or key in (".", "..") or "/" in key:
        return None
    session_dir = sessions_dir(env) / key
    return session_dir if session_dir.is_dir() else None


def take_note(env) -> str | None:
    """The line a new adapter the daemon started left for this session
    (`respawn.NOTE`), taken once: the note is renamed away before it is read, so two
    events at once print it once, and removed. None when there is none."""
    from .adapters.respawn import NOTE

    session_dir = session_dir_of(env)
    if session_dir is None:
        return None
    taken = session_dir / f".{NOTE}.taken.{os.getpid()}"
    try:
        os.rename(session_dir / NOTE, taken)
    except OSError:
        return None
    try:
        return taken.read_text().strip() or None
    except OSError:
        return None
    finally:
        taken.unlink(missing_ok=True)


def answer(event) -> str | None:
    """turns' `SessionEvent` in, the added context out (None for none)."""
    from .adapters import shim_roster
    from .paths import daemon_socket

    if event.harness not in HARNESSES or event.event not in ("start", "prompt"):
        return None
    env = dict(event.env) if event.env else dict(os.environ)
    if not env.get(NAME_ENV):
        return None
    deadline = time.monotonic() + QUERY_TIMEOUT
    socket_path = daemon_socket(env)
    state = memory_path(env)
    text = None
    if event.event == "start":
        info = shim_roster.fetch(socket_path, timeout=QUERY_TIMEOUT)
        text, online = shim_roster.render_snapshot(info, env=env)
        if online and state is not None:
            shim_roster.write_state(state, shim_roster.roster_of(info))
    elif state is not None:
        text, _online = shim_roster.delta(socket_path, state, env=env,
                                          timeout=QUERY_TIMEOUT)
    line = respawn_gone_adapter(env, wait_s=max(0.0, deadline - time.monotonic()))
    note = take_note(env)
    if line is None:
        line = note
    if line is None:
        return text
    if text is None:
        return line
    return f"{line}\n\n{text}" if event.event == "start" else f"{line}\n{text}"


def respawn_gone_adapter(env, *, wait_s: float = QUERY_TIMEOUT) -> str | None:
    """Start a new adapter for this session when its adapter is gone while it runs
    (`claimfile.adapter_gone`, its claim file found by the claim socket the wrapper
    stamped), and say how that went in one line; None while the adapter serves.

    The new adapter (`adapters.respawn`) binds the same name from the claim file. Its
    start and its word are waited for at most `wait_s` together, inside turns' bound;
    one that comes later still binds. A respawn that can't run or whose bind fails leaves the claim file, so the
    next start or prompt tries again and says the same."""
    from . import claimfile
    from .adapters import respawn
    from .claimhold import CLAIM_SOCKET_ENV

    value = env.get(CLAIM_SOCKET_ENV)
    if not value:
        return None
    for key, record in claimfile.all_records(env):
        if record.get("socket") != str(value) or not claimfile.adapter_gone(record):
            continue
        who = (record.get("claim") or {}).get("qualified") or "this session"
        try:
            word = respawn.respawn_adapter(env, key, record, wait_s=wait_s)
        except respawn.CannotRespawn as exc:
            return mail_stopped_line(who, f"a new one can't be started: {exc}")
        return respawn_line(who, word)
    return None


def respawn_line(who: str, word, *, stopped_at: float | None = None) -> str:
    """The line for a session whose adapter had stopped and a new one was started, by
    the new one's word (None when it gave none in time). `stopped_at`, when the old one's
    stop is known (the daemon's), is said as the local `HH:MM`."""
    from .adapters.word import BOUND, OFFLINE, STARTING

    stopped = "had stopped" if stopped_at is None else \
        f"had stopped at {time.strftime('%H:%M', time.localtime(stopped_at))}"
    if word is None or word[0] == STARTING:
        return (f"comms: this session's comms adapter {stopped}. A new one is binding "
                f"{who} again; its mail resumes once it has.")
    code, why = word
    if code == BOUND:
        return (f"comms: this session's comms adapter {stopped}. A new one has bound "
                f"{who} again, and its mail resumes.")
    if code == OFFLINE:
        return (f"comms: this session's comms adapter {stopped}. A new one serves it "
                f"and binds {who} when the comms daemon is back.")
    return mail_stopped_line(who, "a new one could not bind it: "
                                  f"{why or 'it ended before it bound'}")


def mail_stopped_line(who: str, why: str) -> str:
    """The line for a session whose adapter is gone and could not be replaced."""
    return (f"comms: mail has stopped for {who}. This session's comms adapter is gone, "
            f"and {why}. No message reaches this session and a send from it is refused; "
            "the next prompt tries a new adapter again, or relaunch the session to get "
            "its mail back.")


__all__ = ["HARNESSES", "MEMORY", "QUERY_TIMEOUT", "answer", "mail_stopped_line",
           "memory_path", "respawn_gone_adapter", "respawn_line", "session_dir_of",
           "take_note"]
