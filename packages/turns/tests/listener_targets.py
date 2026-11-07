"""Listener functions the listener tests register (stand-ins for a module's own)."""

import time

CALLS = []


def comms(event):
    CALLS.append(("comms", event.event, event.session_id, event.source))
    event.env["CHANGED"] = "by comms"
    return f"comms at {event.event}"


def cache(event):
    CALLS.append(("cache", event.event, event.env.get("CHANGED")))
    return "cache context"


def slow(event):
    time.sleep(1.0)
    return "too late"


def broken(event):
    raise RuntimeError("a bug in the listener")


def silent(event):
    return None


def not_text(event):
    return 42


BIG = ""


def big(event):
    return BIG


def after(event):
    CALLS.append(("after", event.tool, event.agent_id, event.is_error, event.point,
                  event.record_path, event.cwd))
    return f"after {event.tool}"


def seen(event):
    CALLS.append((event.event, event.cwd, event.text, event.origin, event.usage,
                  event.received_at))


#: What `floor` answers; a test sets it.
FLOOR = {"value": 0}
#: Set while `hang` should hold its thread.
HOLD = __import__("threading").Event()


def floor(event):
    CALLS.append(("floor", event.event))
    return FLOOR["value"]


def floor_raises(event):
    raise RuntimeError("a bug in the floor")


def floor_text(event):
    return "12"


def floor_slow(event):
    time.sleep(1.0)
    return 7


def hang(event):
    HOLD.wait(5.0)
    return "too late"


def limits(event):
    CALLS.append(("codex_limits", event.kind, event.payload, event.received_at,
                  event.harness, event.session_id))


def gone_late(event):
    time.sleep(0.3)
    CALLS.append(("gone_late", event.session_id))
