"""The fixture's handlers.

The count and the note live in files in the fixture's state directory, so they outlast
a page server. A test controls the server through files there too:

- `fail-start`: while it exists, this module refuses to import, so a page server exits 3
  at its start;
- `acts`: one line per act the server ran;
- `hold`: while it exists, a read of `/api/wait` doesn't answer (for up to 20 s), so a
  test can keep a request open; each such read first appends its pid to `waiting`.
"""

import json
import os
import threading
import time

from moxi.paths import Layout
from moxi_loopback.api import Topics

STATE = Layout("fixture").state_dir()

if (STATE / "fail-start").exists():
    raise RuntimeError("the fixture's fail-start file is set")

TOPICS = Topics("count", "note")
_lock = threading.Lock()


def _read() -> dict:
    try:
        return json.loads((STATE / "values.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"count": 0, "note": ""}


def _write(values: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    temp = STATE / "values.json.tmp"
    temp.write_text(json.dumps(values), encoding="utf-8")
    os.replace(temp, STATE / "values.json")
    with open(STATE / "acts", "a", encoding="utf-8") as out:
        out.write(f"{os.getpid()}\n")


def _publish(values: dict) -> None:
    TOPICS.publish("count", {"n": values["count"]})
    TOPICS.publish("note", {"text": values["note"]})


_publish(_read())


def summary(call):
    values = _read()
    return {"count": values["count"], "note": values["note"], "pid": os.getpid()}


def add(call):
    with _lock:
        values = _read()
        values["count"] += call.body.get("by", 1)
        if "note" in call.body:
            values["note"] = call.body["note"]
        _write(values)
    _publish(values)
    return {"count": values["count"]}


def wait(call):
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "waiting", "a", encoding="utf-8") as out:
        out.write(f"{os.getpid()}\n")
    deadline = time.monotonic() + 20
    while (STATE / "hold").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    return {"pid": os.getpid()}


def events(call):
    return call.stream(TOPICS)


def health():
    return (True, "fine")
