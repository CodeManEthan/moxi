"""The ONE rendering of the roster, in a module that pulls nothing behind it.

The `agents` verb and the session-start snapshot call the same function, so the injected
snapshot is the verb's output verbatim by construction rather than by a second rendering
kept in sync.

**Why it lives here rather than in `cli`.** If `adapters/shim_roster` imported it from
`cli`, then `cli`, and everything `cli` imports (the card reader among them), would sit
inside an adapter's import closure. Here, `cli` is a leaf -- nothing in the package
imports it -- and a shared renderer is substrate both surfaces use, while a CLI is not
something an adapter should have to import at all. Pinned by
`tests/test_cards.py::test_only_the_cli_imports_the_card_reader`.
"""

from __future__ import annotations

import time

#: The empty roster: no binding anyone can message.
NO_BINDINGS = "no live bindings — nothing is addressable right now."

#: The kind a reserved name has in the `agents` answer's `standing` list.
SEND_ONLY = "send-only"

#: A reserved name no module owns: one an older comms kept.
UNOWNED_ROW = "send-only, no module holds it (kept from an older comms)"

#: The adapter kind of an OpenCode binding (`adapters.opencode.ADAPTER_KIND`, spelled
#: here so this module imports no adapter).
OPENCODE = "opencode"

#: What `--seats` adds to an OpenCode binding's row: its adapter posts only when the
#: session reads idle, so mail sent `--interrupt` waits for idle there like any other.
IDLE_INTERRUPT = "interrupt delivers at idle"

#: The `--seats` waiting block's header: what one of its rows means.
WAITING_HEADER = ("waiting (queued for each name until its next session start, /clear, "
                  "compaction, or a turn of its own on a warm cache):")


def render_agents(info, *, seats: bool = False, now: float | None = None) -> str:
    """The ONE rendering of the roster: the `agents` verb and the launch snapshot
    (`moxi-comms-shim-roster`) both call this, so the injected snapshot is the verb's
    output verbatim by construction — one rendering exists and cannot drift from
    what an agent sees when it re-runs the verb.

    The default view is the names an agent can message, one row each, then one row for
    each reserved send-only name (`standing` entries of kind `send-only`): a module
    sends under it and nobody mails it, and the row says which module. `seats=True` (the
    `--seats` flag, the operator's view) lists the same rows, then the mail waiting for
    each name (`waiting`) and the unanswered rows. On `--seats` an OpenCode binding's row
    also says `interrupt delivers at idle`. The snapshot never asks for `--seats`. `now`
    (default: the wall clock) ages the waiting rows.
    """
    rows = info["bindings"]
    lines = [] if rows else [NO_BINDINGS]
    for b in rows:
        row = f"{b['qualified']:<24} {b['tier']:<10} adapter {b['adapter']}"
        if seats and b["adapter"] == OPENCODE:
            row += f", {IDLE_INTERRUPT}"
        lines.append(row)
    standing = info.get("standing")
    for s in standing if isinstance(standing, list) else ():
        if isinstance(s, dict) and s.get("kind") == SEND_ONLY:
            module = s.get("module")
            what = f"send-only, reserved for {module}" if module else UNOWNED_ROW
            lines.append(f"{s['qualified']:<24} {what}")
    if not seats:
        return "\n".join(lines)
    now = time.time() if now is None else now
    # One queue, one block: a row is the count waiting for the name, for whatever
    # reason (offline, cold, or flagged `--held`; none is stored), and the oldest's wait.
    if info["waiting"]:
        lines.append("")
        lines.append(WAITING_HEADER)
        for name, row in sorted(info["waiting"].items()):
            lines.append(f"  {name:<24} {row['count']} message(s), oldest waiting "
                         f"{waited(now - row['oldest'])}")
    # Ignored reminders: recorded where the owed party sees them, with no timer.
    for row in info["unanswered"]:
        lines.append(f"unanswered: {row['owing']} owes {row['author']} msg "
                     f"{row['message_id']}, {waited(row['for'])}")
    return "\n".join(lines)


def waited(seconds: float) -> str:
    """`42m`, `5h 12m`, `2d 3h`: how long the oldest waiting message has waited."""
    minutes = int(max(0.0, seconds) // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"
