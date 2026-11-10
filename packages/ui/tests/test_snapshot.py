"""The module snapshot, the shell's own routes, its health and its announcement.

The shell tests run a real shell on a real listener with the snapshot on, over test page
modules in a site directory on `PYTHONPATH` (the starter tests' helpers).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from moxi import files, modules
from moxi.paths import Layout
from moxi_ui.loopback.api import call, read_runtime, write_runtime

from moxi_ui import health, listen
from moxi_ui.page import PAGE
from moxi_ui.serve import Shell
from moxi_ui.snapshot import ModuleSnapshot, check_components, modules_value
from test_starter import _module

ASSETS = ["page.js", "today.js"]


def _ok(**extra):
    return {"script": "today.js", "title": "Today", "doc": "One line", "contract": 1,
            "place": "block", **extra}


@pytest.mark.parametrize("name, decl, reason", [
    ("Bad", _ok(), "lower-case words"),
    ("today", _ok(script="nope.js"), "not an asset"),
    ("today", _ok(title="two\nlines"), "title is not one line"),
    ("today", _ok(contract=2), "upgrade moxi-ui"),
    ("today", _ok(contract=True), "not a version number"),
    ("today", _ok(place="side"), "its place is"),
    ("today", _ok(place="page"), "only the component named page"),
    ("page", _ok(script="page.js"), "only the component named page"),
    ("today", _ok(attributes={"Day": {"type": "string"}}), "lower-case attribute"),
    ("today", _ok(attributes={"day": {"type": "date"}}), "has type"),
    ("today", _ok(attributes={"day": {"type": "enum"}}), "needs values"),
    ("today", _ok(attributes={"day": {"type": "int", "values": ["a"]}}), "only an enum"),
    ("today", _ok(attributes={"day": {"type": "int", "required": "yes"}}), "required"),
    ("today", _ok(home=True, attributes={"day": {"type": "int", "required": True}}),
     "required attribute"),
])
def test_a_component_breaking_a_rule_is_refused_with_the_reason(name, decl, reason):
    placed, refused = check_components("usage", {name: decl}, page=True, assets=ASSETS)
    assert placed == [] and reason in refused[0]["reason"]


def test_good_components_are_placed_and_a_module_without_a_page_places_none():
    decl = {"page": _ok(script="page.js", place="page"),
            "today": _ok(home=True, future_field=1,
                         attributes={"day": {"type": "enum", "values": ["a", "b"]}})}
    placed, refused = check_components("usage", decl, page=True, assets=ASSETS)
    assert refused == [] and [c["element"] for c in placed] == ["moxi-usage-page",
                                                                 "moxi-usage-today"]
    assert placed[1]["url"] == "/m/usage/today.js" and placed[1]["attributes"][0]["values"] == ["a", "b"]
    assert check_components("usage", decl, page=True, assets=None)[0] == []
    placed, refused = check_components("usage", decl, page=False, assets=ASSETS)
    assert placed == [] and "declares no PAGE" in refused[0]["reason"]


def _one(decl, name="today"):
    placed, refused = check_components("usage", {name: decl}, page=True, assets=ASSETS)
    assert refused == []
    return placed[0]


def test_a_page_keeps_its_group_and_order_and_a_wrong_one_reads_as_the_default():
    def page(**extra):
        return _one(_ok(script="page.js", place="page", **extra), name="page")

    kept = page(group="daily", order=0)
    assert (kept["group"], kept["order"], kept["home_as"]) == ("daily", 0, None)
    assert (page(order=999)["order"], page(group="setup")["group"]) == (999, "setup")
    absent = page()
    assert (absent["group"], absent["order"]) == ("setup", 500)
    for group in ("Daily", "dayly", "", 3, None, ["daily"]):
        assert page(group=group, order=7)["group"] == "setup"
    for order in (-1, 1000, 2.0, "10", True, None, [1]):
        assert page(group="daily", order=order)["order"] == 500


@pytest.mark.parametrize("home_as", ["main", "row"])
def test_a_home_block_keeps_its_home_as_and_order(home_as):
    kept = _one(_ok(home=True, home_as=home_as, order=10))
    assert (kept["home_as"], kept["order"], kept["group"]) == (home_as, 10, None)
    assert _one(_ok(home=True, home_as=home_as))["order"] == 500
    assert _one(_ok(home=True, home_as=home_as, order="first"))["order"] == 500


def test_a_component_the_fields_do_not_apply_to_carries_none():
    plain = _one(_ok(home=True, group="daily", order=3))
    assert (plain["home_as"], plain["group"], plain["order"]) == (None, None, None)
    assert _one(_ok(home=True, home_as=None))["home_as"] is None


@pytest.mark.parametrize("decl, reason", [
    (_ok(home=True, home_as="side"), "its home_as is 'side'"),
    (_ok(home=True, home_as=True), "its home_as is True"),
    (_ok(home=True, home_as=["row"]), "its home_as is"),
    (_ok(home_as="row"), "not a block placed at home"),
    (_ok(home=False, home_as="main"), "not a block placed at home"),
    (_ok(home=True, home_as="row", place="inline"), "not a block placed at home"),
])
def test_a_home_as_that_does_not_fit_refuses_the_component(decl, reason):
    placed, refused = check_components("usage", {"today": decl}, page=True, assets=ASSETS)
    assert placed == [] and reason in refused[0]["reason"]


def test_the_modules_shape_sends_the_three_fields():
    from moxi_ui.page import _COMPONENT

    row = _one(_ok(home=True, home_as="row", order=900))
    assert set(row) == set(_COMPONENT.items)
    assert {"home_as", "group", "order"} <= set(_COMPONENT.items)


def _fake_run(listing, exports, calls):
    def run(argv, *, timeout_s, cwd, env=None):
        calls.append((argv, cwd, env))
        out = listing if argv[3] == "moxi" else {m: exports[m] for m in argv[4:]}
        return SimpleNamespace(stdout=json.dumps(out), returncode=0)
    return run


def test_a_refresh_reads_both_commands_in_fresh_processes_and_leaves_out_what_it_is_told(tmp_path):
    listing = {"modules": [
        {"name": "ui", "state": "present", "page": "moxi_ui.page:PAGE", "version": "0.1.0"},
        {"name": "usage", "state": "present", "page": "moxi_usage.page:PAGE", "version": "1.2.0",
         "components": {"today": _ok()}},
        {"name": "gone", "state": "broken", "page": "x.y:Z", "reason": "it broke"},
        {"name": "cache", "state": "present", "page": "moxi_cache.page:PAGE", "version": "1"},
        {"name": "Bad!", "state": "present"}]}
    export = {"format": 1, "routes": [], "assets": ["today.js"], "budget": {"rss": "120M", "fds": 64}}
    calls = []
    snap = ModuleSnapshot(cwd=tmp_path, env={"A": "1"}, leave_out=["cache"],
                          run=_fake_run(listing, {"usage": {"ok": True, "export": export}}, calls))
    view = snap.read()
    assert view.pages == ("usage",) and view.versions == {"usage": "1.2.0"}
    assert [c[0][1:4] for c in calls] == [["-P", "-m", "moxi"], ["-P", "-m", "moxi_ui.loopback.export"]]
    assert all(c[1] == str(tmp_path) and c[2] == {"A": "1"} for c in calls)
    names = [m["name"] for m in view.value["modules"]]
    assert names == ["gone", "ui", "usage"]
    usage = view.value["modules"][2]
    assert usage["mounted"] and usage["budget"] == {"rss": "120M", "fds": 64}
    assert usage["components"][0]["element"] == "moxi-usage-today"


def test_a_failed_listing_keeps_the_last_view_and_says_why(tmp_path):
    good = {"modules": [{"name": "usage", "state": "present", "page": "p.q:R", "version": "1"}]}
    exports = {"usage": {"ok": False, "error": "its page raised"}}
    snap = ModuleSnapshot(cwd=tmp_path, run=_fake_run(good, exports, []))
    snap.current = first = snap.read()
    assert first.exports.errors == {"usage": "its page raised"}
    snap.run = lambda argv, **kw: SimpleNamespace(stdout="", returncode=1)
    kept = snap.read()
    assert kept.pages == first.pages and "exited 1" in kept.error and kept.value["error"] == kept.error



def test_a_failed_refresh_is_run_again_on_a_look_with_a_growing_wait(tmp_path, monkeypatch):
    import moxi_ui.snapshot as snapshot_mod

    monkeypatch.setattr(snapshot_mod, "RETRY_MAX", 8.0)
    listing = {"modules": [{"name": "usage", "state": "present", "page": "p.q:R",
                            "version": "1"}]}
    export = {"format": 1, "routes": [], "assets": []}
    now = [100.0]
    answers = []

    def run(argv, *, timeout_s, cwd, env=None):
        answer = answers.pop(0) if answers else "ok"
        if answer == "slow":
            raise snapshot_mod.subprocess.TimeoutExpired(argv, timeout_s)
        out = listing if argv[3] == "moxi" else {"usage": {"ok": True, "export": export}}
        return SimpleNamespace(stdout=json.dumps(out), returncode=0)

    snap = ModuleSnapshot(cwd=tmp_path, dirs=[str(tmp_path)], every=2.0,
                          clock=lambda: now[0], run=run)

    async def refreshed(wait):
        now[0] += wait
        before = snap.refreshes
        snap.touch()
        if snap._task is not None:
            await snap._task
        return snap.refreshes > before

    async def retried():
        """Looks 2 s apart until one runs the refresh again: the wait it took."""
        start = now[0]
        while not await refreshed(2.0):
            assert now[0] - start < 100
        return now[0] - start

    async def body():
        answers.append("slow")  # The first listing times out, as at a busy logon.
        snap.start()
        await snap.ready()
        assert snap.current.error and snap.current.value["modules"] == []
        answers.append("slow")
        assert await retried() == 2.0 and snap.current.error
        answers.extend(["ok", "slow"])  # The listing answers; the export times out.
        assert await retried() == 4.0 and snap.current.error is None
        assert snap.current.exports.errors and not snap.current.exports.mounts
        answers.extend(["ok", "slow"])
        assert await retried() == 8.0
        answers.extend(["ok", "slow"])
        assert await retried() == 8.0, "the wait doubles up to its longest"
        assert await retried() == 8.0 and snap.current.exports.mounts == {"usage": export}
        assert not await refreshed(60.0), "a refresh that succeeded is not run again"

    asyncio.run(asyncio.wait_for(body(), 10))


# --- a real shell ------------------------------------------------------------------------


@pytest.fixture
def site(tmp_path, monkeypatch):
    root = tmp_path / "site"
    root.mkdir()
    _module(root, "snone")
    monkeypatch.setenv("PYTHONPATH", str(root))
    Layout("ui").runtime_dir(create=True)
    return root


def _serve(**kwargs):
    shell = Shell(listen.bind(0), epoch=None, **kwargs)
    shell.start()
    thread = threading.Thread(target=lambda: asyncio.run(shell.run()), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while shell._should_exit is None and time.monotonic() < deadline:
        time.sleep(0.01)
    return shell, thread


def _get(port, path, lines=()):
    head = "\r\n".join([f"GET {path} HTTP/1.1", f"Host: 127.0.0.1:{port}", *lines,
                        "Connection: close", "", ""])
    with socket.create_connection(("127.0.0.1", port), timeout=15) as sock:
        sock.sendall(head.encode())
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    status = int(data.split(b" ", 2)[1])
    return status, data.split(b"\r\n\r\n", 1)[1]


def test_the_shell_mounts_a_new_module_without_a_restart_and_serves_its_own_routes(
        site, monkeypatch):
    shell, thread = _serve(snapshot=ModuleSnapshot(cwd=Layout("ui").runtime_dir(),
                                                   dirs=[str(site)], every=0.05))
    try:
        session_id, key = shell.sessions.store.mint()
        browser = [f"Cookie: moxi-ui={session_id}", f"X-Moxi-Key: {key}",
                   "Sec-Fetch-Site: same-origin", "Sec-Fetch-Mode: cors", "Sec-Fetch-Dest: empty"]
        port = shell.listeners.port
        assert _get(port, "/m/snone/api/echo?n=1", browser) == (200, b'{"n":1}')
        assert _get(port, "/m/sntwo/api/echo?n=2", browser)[0] == 404
        _module(site, "sntwo")
        os.utime(site)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if _get(port, "/m/sntwo/api/echo?n=2", browser) == (200, b'{"n":2}'):
                break
            time.sleep(0.1)
        else:
            pytest.fail("the new module was never mounted")
        status, body = _get(port, "/m/ui/api/modules", browser)
        listed = {m["name"]: m for m in json.loads(body)["modules"]}
        assert status == 200 and listed["sntwo"]["mounted"] and listed["ui"]["page"]
        # The token routes take the runtime file's token and nothing a browser sends.
        info = read_runtime("ui")
        status, data = call(info, "GET", "/m/ui/api/servers")
        assert status == 200 and data["shell"]["pid"] == os.getpid()
        assert {row["module"] for row in data["servers"]} >= {"snone", "sntwo"}
        assert data["shell"]["budget"] == {"rss": "80M", "fds": 64}
        assert _get(port, "/m/ui/api/servers", browser)[0] == 403
        with files.lock(Layout("ui").runtime_dir() / "page.lock", timeout_s=0):
            found = health.probe(wait=2)
        assert found.state == "running" and health.shell_row(found).state in ("ok", "warn")
        status, _ = call(info, "POST", "/m/ui/api/stop", {})
        assert status == 200
        thread.join(15)
        assert not thread.is_alive()
    finally:
        shell.stop()
        thread.join(15)
        shell.close()
    # The probe's last step connects to the port setting: point it at this shell's own
    # port, so another shell on the default port can't answer in its place.
    monkeypatch.setattr(health.config, "port", lambda settings=None: port)
    assert health.probe(wait=0.2).state == "down"


def test_the_stop_route_refuses_a_cookie_or_a_fetch_header(site):
    shell, thread = _serve()
    try:
        info = read_runtime("ui")
        for extra in ({"Cookie": "a=b"}, {"Sec-Fetch-Site": "same-origin"}):
            lines = [f"POST /m/ui/api/stop HTTP/1.1", f"Host: 127.0.0.1:{info.port}",
                     f"X-Moxi-Token: {info.token}", "Content-Type: application/json",
                     "Content-Length: 2", *(f"{k}: {v}" for k, v in extra.items()),
                     "Connection: close", "", "{}"]
            with socket.create_connection(("127.0.0.1", info.port), timeout=10) as sock:
                sock.sendall("\r\n".join(lines).encode())
                assert sock.recv(64).split(b" ")[1] == b"403"
        assert thread.is_alive()
    finally:
        shell.stop()
        thread.join(15)
        shell.close()


# --- health without a shell --------------------------------------------------------------


def _units(monkeypatch, rows):
    monkeypatch.setattr(health, "_units_status", lambda: rows)


def _no_connect(monkeypatch):
    seen = []
    monkeypatch.setattr(health, "_accepts", lambda port: seen.append(port) or False)
    return seen


def test_a_held_lock_with_no_file_is_starting_never_another_program(site, monkeypatch):
    seen = _no_connect(monkeypatch)
    _units(monkeypatch, [SimpleNamespace(module="ui", name="shell", kind="socket",
                                         state="active", detail="", backend="systemd")])
    with files.lock(Layout("ui").runtime_dir() / "page.lock", timeout_s=0):
        found = health.probe(wait=0.3)
    assert found.state == "starting" and health.shell_row(found).state == "warn" and seen == []


def test_a_free_lock_ignores_a_left_runtime_file_and_a_listening_socket_is_not_connected(site, monkeypatch):
    write_runtime("ui", transport="tcp", address="127.0.0.1", port=1, version="0")
    seen = _no_connect(monkeypatch)
    _units(monkeypatch, [SimpleNamespace(module="ui", name="shell", kind="socket",
                                         state="active", detail="", backend="systemd")])
    found = health.probe()
    assert found.state == "idle" and health.shell_row(found).state == "off" and seen == []
    _units(monkeypatch, [SimpleNamespace(module="ui", name="shell", kind="socket",
                                         state="failed", detail="moxi-ui-shell.socket",
                                         backend="systemd")])
    assert health.shell_row(health.probe()).state == "fail" and seen == []
    _units(monkeypatch, [SimpleNamespace(module="ui", name="shell", kind="daemon",
                                         state="unknown", detail="read failed", backend="systemd")])
    assert health.shell_row(health.probe()).state == "warn" and seen == []
    _units(monkeypatch, [])
    assert health.probe().state == "down" and seen == [8460]
    monkeypatch.setattr(health, "_accepts", lambda port: True)
    assert health.shell_row(health.probe()).state == "fail"



def test_a_held_lock_reads_a_runtime_file_whose_start_is_off_the_pids_and_compares_no_clock(
        site, monkeypatch):
    import psutil
    from datetime import UTC, datetime

    info = write_runtime("ui", transport="tcp", address="127.0.0.1", port=8460, version="0")
    path = Layout("ui").runtime_dir() / "page.json"
    data = json.loads(path.read_text())
    created = psutil.Process(os.getpid()).create_time()
    data["started_at"] = datetime.fromtimestamp(created + 5, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    files.atomic_write(path, json.dumps(data), mode=0o600)
    called = []
    monkeypatch.setattr(health, "_servers", lambda found, timeout: called.append(found) or {
        "shell": {"pid": os.getpid(), "port": 8460}, "servers": []})
    with files.lock(Layout("ui").runtime_dir() / "page.lock", timeout_s=0):
        found = health.probe(wait=0.3)
    assert found.state == "running" and [c.server_id for c in called] == [info.server_id]


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("written", [False, True])
def test_the_lock_is_tried_before_the_runtime_file_is_read(site, monkeypatch, held, written):
    if written:
        write_runtime("ui", transport="tcp", address="127.0.0.1", port=8460, version="0")
    order = []
    lock_held, read = health.cli.lock_held, health.read_runtime
    monkeypatch.setattr(health.cli, "lock_held", lambda: order.append("lock") or lock_held())
    monkeypatch.setattr(health, "read_runtime", lambda module: order.append("file") or read(module))
    monkeypatch.setattr(health, "_servers", lambda found, timeout: None)
    _units(monkeypatch, [])
    _no_connect(monkeypatch)
    if held:
        with files.lock(Layout("ui").runtime_dir() / "page.lock", timeout_s=0):
            health.probe(wait=0.1)
    else:
        health.probe(wait=0.1)
    assert order[0] == "lock" and (order[1:2] == ["file"]) == held
    assert held or "file" not in order, "a free lock reads no runtime file"


def test_the_look_is_every_2_s_over_site_packages_and_the_childrens_pythonpath(tmp_path):
    import sysconfig

    import moxi_ui.snapshot as snapshot_mod

    extra = tmp_path / "extra"
    extra.mkdir()
    paths = sysconfig.get_paths()
    env = {"PYTHONPATH": os.pathsep.join([str(extra), paths["purelib"], str(extra)])}
    assert snapshot_mod.watched_dirs(env) == list(dict.fromkeys(
        [paths["purelib"], paths["platlib"], str(extra)]))
    now = [100.0]
    snap = ModuleSnapshot(cwd=tmp_path, env=env, clock=lambda: now[0])
    assert snap.every == snapshot_mod.EVERY == 2.0 and snap.dirs() == snapshot_mod.watched_dirs(env)
    snap._marks = snapshot_mod._mtimes(snap.dirs())
    asked = []
    snap.request = lambda: asked.append(now[0])
    snap.touch()
    os.utime(extra, (1, 1))
    for step in (1.0, 0.9, 0.1):
        now[0] += step
        snap.touch()
    assert asked == [102.0], "the change is seen at the first look 2 s after the last"

@pytest.mark.parametrize("task, state", [
    (None, "warn"),
    (SimpleNamespace(state="inactive", enabled=False, last=None), "warn"),
    (SimpleNamespace(state="failed", enabled=True, last={"exit": 1}), "warn"),
    (SimpleNamespace(state="inactive", enabled=True, last=None), "off"),
])
def test_the_windows_task_states_give_the_shell_row(monkeypatch, task, state):
    monkeypatch.setattr(health, "_platform", lambda: "win32")
    row = health.shell_row(health.Probe("down", task=task))
    assert row.state == state and (state != "warn" or task is None or row.fix)


def test_a_sample_over_the_budget_is_named():
    assert health.over_budget({"rss": 90 * 1024**2, "fds": 65}, health.SHELL_BUDGET) == [
        "RSS 90 MB over its 80M", "65 open fds over its 64"]
    assert health.over_budget({"rss": 10, "fds": 1}, {"rss": "120M", "fds": 64}) == []


# --- the announcement ---------------------------------------------------------------------


def test_the_announcement_declares_the_shell_its_page_and_components():
    found = modules.load("ui")
    assert found.state == modules.PRESENT, found.reason
    assert found.interfaces == frozenset({1, 2, 3}) and found.page == "moxi_ui.page:PAGE"
    shell = found.daemons["shell"]
    assert "win32" in shell["platforms"] and shell["socket"]["setting"] == "port"
    assert found.health == {"shell": "moxi_ui.health:check"} and not found.budgets
    placed, refused = check_components("ui", found.components, page=True,
                                       assets=["page.js", "servers.js", "health.js"])
    assert refused == [] and {c["element"] for c in placed} == {
        "moxi-ui-page", "moxi-ui-servers", "moxi-ui-health"}
    health = next(c for c in placed if c["name"] == "health")
    assert (health["home_as"], health["order"]) == ("row", 900)
    assert {route.path for route in PAGE.routes} >= {"/api/servers", "/api/stop", "/api/modules"}


@pytest.mark.skipif(sys.platform != "win32", reason="the logon task is planned on Windows")
def test_on_windows_the_shell_is_planned_as_a_logon_task_with_no_socket():
    from moxi import units

    changes = units.plan("ui")
    assert [c.kind for c in changes] == ["daemon"]
    assert "moxi-ui-shell" in changes[0].text and "--restart" in changes[0].text


def test_the_modules_value_holds_only_declared_keys():
    from moxi_ui.page import MODULES_SHAPE

    value = modules_value([{"name": "usage", "state": "present", "page": "p.q:R",
                            "components": {"today": _ok()}}],
                          {"usage": {"assets": ["today.js"]}}, {}, at="now")
    assert set(value) == set(MODULES_SHAPE)
    assert set(value["modules"][0]) == set(MODULES_SHAPE["modules"].items.items)
