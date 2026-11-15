"""The launcher's read of what is alive, `moxi_launcher.live`: the page's handlers offer its
two functions under their own names, and it reads a launch log on an install without the UI
shell."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

from moxi_launcher import live, pagehandlers

WITHOUT_UI = textwrap.dedent("""\
    import json
    import os
    import sys

    # As if the UI shell weren't installed.
    sys.modules["moxi_ui"] = None

    from moxi import proc

    import moxi_launcher.live
    from moxi_launcher import store

    def launched(conn, request_id, at, pid=None, started=None):
        store.insert(conn, request_id=request_id, actor="alice", harness="claude",
                     target="terminal", cwd="/work", values={"model": "opus"},
                     argv=["claude"], env={}, posture="operator",
                     session_id=f"session-{request_id}", slot=None, at=at)
        facts = {k: v for k, v in (("pid", pid), ("pid_started", started)) if v is not None}
        store.set_state(conn, request_id, "started", **facts)

    me = proc.identity(os.getpid())
    conn = store.connect()
    launched(conn, "running", "2026-01-02T00:00:00.000000Z", me.pid, me.started)
    launched(conn, "reused", "2026-01-01T00:00:00.000000Z", me.pid, me.started - 1000)
    launched(conn, "no-process", "2025-12-31T00:00:00.000000Z")
    store.learn_session(conn, "running", "session-after-clear", "clear")
    found = moxi_launcher.live.live(conn)
    print(json.dumps({
        "alive": {name: moxi_launcher.live.alive(store.get(conn, name))
                  for name in ("running", "reused", "no-process")},
        "live": [[row.request_id, row.newest_session_id] for row in found],
        "loaded": sorted(name for name in sys.modules
                         if name in ("moxi_launcher.page", "moxi_launcher.pagehandlers")
                         or name.startswith("moxi_ui."))}))
""")


def test_the_page_handlers_offer_the_two_functions_of_the_live_module():
    assert pagehandlers.alive is live.alive and pagehandlers.live is live.live
    assert {"alive", "live"} <= set(pagehandlers.__all__)
    assert live.__all__ == ["alive", "live"]


def test_what_is_alive_is_read_without_the_ui(tmp_path):
    done = subprocess.run([sys.executable, "-P", "-c", WITHOUT_UI], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["alive"] == {"running": True, "reused": False, "no-process": False}
    assert out["live"] == [["running", "session-after-clear"]]
    assert out["loaded"] == []
