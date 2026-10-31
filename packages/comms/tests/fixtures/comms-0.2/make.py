"""Make the comms 0.2 fixture beside this script, by running comms 0.2.0 itself.

Run it with comms 0.2.0 installed and nothing newer: a virtual environment holding
`moxi-comms` 0.2.0 (the release's tag), `moxi` 0.1 and `moxi-turns` 0.1.

    python make.py [OUT]

OUT defaults to this script's directory. The script builds a comms 0.2 home in a fresh
temporary directory, through 0.2.0's own daemon start (which provisions the directory and
sets the record's conversion markers), its record, its binding history and its cursor
writer, then writes what the home holds as text:

    record.sql, directory.sql, bindings.sql   each 0.2 database as SQL (`iterdump`)
    cursors/local/<name>.json                 each cursor file, as 0.2 wrote it
    tokens/<file>                             the operator's token file
    made.json                                 the version, the time, and the ids below

The fixture's mail, by message (`made.json` names each id):

    m1  reviewer@proj-a, handled: the reviewer's cursor stands at it
    m2  reviewer@proj-a and planner, `[held]`: a queue row for each
    m3  reviewer@proj-a, handed live and not handled: above the cursor, no row
    m4  planner, handed live and not handled: planner has no cursor, no row
    m5  agent-3: an ephemeral name, never queued
    m6  herald, a send-only name: never queued
    m7  the operator to planner: no row

and the binding history holds a released interval of the reviewer's and a live one of
planner's, which the move fences. `herald` is a send-only row and `old@proj-a` a retired
one.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

VERSION = "0.2.0"


def _dump(db: Path, out: Path) -> None:
    conn = sqlite3.connect(db)
    try:
        out.write_text("\n".join(conn.iterdump()) + "\n")
    finally:
        conn.close()


def make(out: Path) -> dict:
    found = importlib.metadata.version("moxi-comms")
    if found != VERSION:
        raise SystemExit(f"this script runs comms {VERSION}; moxi-comms {found} is "
                         "installed")
    home = Path(tempfile.mkdtemp(prefix="comms-0.2-"))
    os.environ["MOXI_HOME"] = str(home / "moxi")
    os.environ["MOXI_COMMS_HOME"] = str(home / "comms")
    os.environ["XDG_RUNTIME_DIR"] = str(home / "run")
    (home / "run").mkdir(mode=0o700)
    for name in ("MOXI_COMMS_BACKEND", "MOXI_COMMS_GATEWAY", "MOXI_COMMS_OPERATOR"):
        os.environ.pop(name, None)

    from moxi_comms import bindings, cursors, directory, machine_line, migration, \
        provision, tokens
    from moxi_comms.daemon import Daemon
    from moxi_comms.local_gateway import LocalGateway
    from moxi_comms.paths import bindings_db, directory_db, record_db, state_dir, \
        tokens_dir

    store = tokens.TokenStore(None, env=os.environ)
    gateway = LocalGateway()
    daemon = Daemon(gateway, first_start=provision.first_start(store),
                    migrate=migration.startup(store), start_trim_thread=False)
    daemon.start()
    daemon.stop()

    conn = directory.connect(directory_db())
    try:
        provision.add_standing(gateway, conn, "reviewer", "proj-a")
        provision.add_standing(gateway, conn, "planner")
        provision.add_standing(gateway, conn, "herald", kind=directory.KIND_SEND_ONLY)
        provision.add_standing(gateway, conn, "old", "proj-a")
        conn.execute("UPDATE agents SET status = 'retired' WHERE name = 'old'")
        conn.commit()
        names = conn.execute("SELECT channel_id FROM rooms WHERE name = '#names'"
                             ).fetchone()[0]
    finally:
        conn.close()

    def post(author, recipients, text, **flags):
        body = machine_line.compose(recipients, text, posture="none", **flags)
        return gateway.post_as_identity(author, names, body).id

    ids = {
        "m1": post("planner", ["reviewer@proj-a"], "the first, handled"),
        "m2": post("planner", ["reviewer@proj-a", "planner"], "held for both",
                   next_turn=True),
        "m3": post("planner", ["reviewer@proj-a"], "handed live, never handled"),
        "m4": post("reviewer@proj-a", ["planner"], "to planner, never handled"),
        "m5": post("planner", ["agent-3"], "to an ephemeral name"),
        "m6": post("planner", ["herald"], "to a send-only name"),
    }
    owner = tokens.owner_for_role(tokens.ROLE_OPERATOR)
    operator_token = store.read_secret(owner, tokens.OPERATOR_SECRET_ID)
    operator_id = gateway.meta_get("operator_user_id")
    if operator_id is None:
        dconn = directory.connect(directory_db(), read_only=True)
        try:
            operator_id = dconn.execute("SELECT user_id FROM operators").fetchone()[0]
        finally:
            dconn.close()
    ids["m7"] = gateway.post_as_user(
        operator_id, names, machine_line.compose(["planner"], "from the operator"),
        token=operator_token).id
    gateway.queue_add("reviewer@proj-a", [ids["m2"]])
    gateway.queue_add("planner", [ids["m2"]])
    cursors.advance("reviewer@proj-a", ids["m1"])

    journal = bindings.Journal()
    journal.open()
    now = time.time()
    row = journal.bind(qualified="reviewer@proj-a", name="reviewer", scope="proj-a",
                       tier="standing", adapter="claude-shim", at=now - 600)
    journal.release(row, at=now - 300)
    journal.bind(qualified="planner", name="planner", scope="global", tier="standing",
                 adapter="claude-shim", at=now - 120)
    journal.close()
    gateway.close()

    out.mkdir(parents=True, exist_ok=True)
    _dump(record_db(), out / "record.sql")
    _dump(directory_db(), out / "directory.sql")
    _dump(bindings_db(), out / "bindings.sql")
    shutil.rmtree(out / "cursors", ignore_errors=True)
    shutil.copytree(state_dir() / "cursors", out / "cursors")
    shutil.rmtree(out / "tokens", ignore_errors=True)
    shutil.copytree(tokens_dir(), out / "tokens")
    made = {"version": VERSION, "made_at": int(now), "ids": ids,
            "operator_user_id": operator_id, "names_room": names}
    (out / "made.json").write_text(json.dumps(made, indent=2) + "\n")
    shutil.rmtree(home)
    return made


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    print(json.dumps(make(target), indent=2))
