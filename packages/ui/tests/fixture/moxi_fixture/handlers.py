"""The fixture's handlers.

The count and the note live in files in the fixture's state directory, so they outlast
a page server. A test controls the server through files there too:

- `fail-start`: while it exists, this module refuses to import, so a page server exits 3
  at its start;
- `acts`: one line per act the server ran;
- `hold`: while it exists, a read of `/api/wait` doesn't answer (for up to 20 s), so a
  test can keep a request open; each such read first appends its pid to `waiting`;
- `keys`: one line per run of the confirmed act, the `Idempotency-Key` it got; `jobs` the
  job ids its status route knows;
- `phone-keys`: one line per run of the gateway's confirmed act, the key it got;
- `streams`: one line per stream its stream route opened.

The confirmed act's `mode` picks its answer: none adds `by` (1 unless given) and is
accepted, `refuse` is a 422, `conflict` a 409, `fail` a 500, and `die` ends the page
server before it answers.
"""

import json
import os
import threading
import time

from moxi.paths import Layout
from moxi_loopback.api import Accepted, Conflict, Refused, Topics

STATE = Layout("fixture").state_dir()

if (STATE / "fail-start").exists():
    raise RuntimeError("the fixture's fail-start file is set")

TOPICS = Topics("count", "note", "phone")
#: The values the gateway's read and topic carry under a mark: a secret it cuts, and paths
#: it sends as handles.
SECRET = "the fixture's secret"
FILE = str(STATE / "notes" / "phone.txt")
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
    TOPICS.publish("phone", {"n": values["count"], "secret": SECRET, "file": FILE})


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


def _append(name: str, line: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / name, "a", encoding="utf-8") as out:
        out.write(f"{line}\n")


def confirm(call):
    _append("keys", call.key)
    mode = call.body.get("mode")
    if mode == "refuse":
        raise Refused("the fixture refuses this act")
    if mode == "conflict":
        raise Conflict("the fixture is busy with another act")
    if mode == "fail":
        raise RuntimeError("the fixture's act failed")
    if mode == "die":
        os._exit(1)
    with _lock:
        values = _read()
        values["count"] += call.body.get("by", 1)
        _write(values)
    _publish(values)
    job = f"j{len((STATE / 'keys').read_text().splitlines())}"
    _append("jobs", job)
    return Accepted(f"/api/job?job={job}")


def job(call):
    try:
        known = (STATE / "jobs").read_text().splitlines()
    except FileNotFoundError:
        known = []
    return {"job": call.query["job"], "state": "done" if call.query["job"] in known else "unknown"}


def phone(call):
    values = _read()
    return {"count": values["count"], "secret": SECRET, "file": FILE,
            "rows": [{"name": "one", "where": str(STATE / "rows" / "one"), "token": SECRET}]}


def phone_act(call):
    _append("phone-keys", call.key)
    with _lock:
        values = _read()
        values["count"] += call.body.get("by", 1)
        _write(values)
    _publish(values)
    job = f"p{len((STATE / 'phone-keys').read_text().splitlines())}"
    _append("jobs", job)
    return Accepted(f"/api/phone-job?job={job}")


def phone_job(call):
    return job(call)


def wait(call):
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "waiting", "a", encoding="utf-8") as out:
        out.write(f"{os.getpid()}\n")
    deadline = time.monotonic() + 20
    while (STATE / "hold").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    return {"pid": os.getpid()}


def events(call):
    _append("streams", str(os.getpid()))
    return call.stream(TOPICS)


def health():
    return (True, "fine")
