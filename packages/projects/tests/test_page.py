"""projects' page server in process, through loopback's strict app.

Loopback is reached as the page reaches it, through `moxi.modules.load`: projects'
distribution doesn't require it, so these tests don't import it either. The app runs
behind `guard` with an upstream profile, as the runner serves it, with strict projection,
so a handler that sends a key its route doesn't declare, or a value of another type, fails
here. A new app stands for the page server after a restart: its key table is empty.

Every test runs with the site fixture, so the only contributions are the ones a test
installs, and with the core's record of a terminal whose environment is this process's, so
a write isn't refused for want of one; a test of the comparison writes another.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Mapping

import pytest

import moxi.modules
from moxi import modules, paths
from moxi_projects import (acts, api, cli, page, pagecheck, pagehandlers, pagestat, rules,
                           store)

TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321
KEY = "K" * 43
#: Jobs as the page mints them, one per press of its remove button.
JOB = "0123456789abcdef" * 2
OTHER_JOB = "fedcba9876543210" * 2

posix_only = pytest.mark.skipif(sys.platform == "win32",
                                reason="the fake git is a POSIX shell script")


def loopback():
    found = modules.load("ui", interface=3)
    assert found.ok, found.reason
    return found.api.loopback


LB = loopback()


@pytest.fixture(autouse=True)
def only_the_tests_contributions(site):
    """No installed module's contribution reaches these tests."""
    return site


@pytest.fixture(autouse=True)
def a_terminal_has_recorded(terminal_record):
    """The user's own terminal last ran with this process's environment, so the page
    server's places agree with it."""
    return terminal_record()


def served():
    inner = LB.app(page.PAGE, module="projects", server_id=SERVER_ID, strict=True)
    profile = LB.upstream(LB.build_table(page.PAGE)[0], token=TOKEN, port=PORT)
    return LB.guard(inner, lambda: profile)


async def _send(app, method: str, path: str, *, query: str = "", body: dict | None = None,
                key: str | None = None) -> tuple[int, dict | None]:
    headers = [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode())]
    raw = b""
    if method == "POST":
        headers.append((b"content-type", b"application/json"))
        raw = json.dumps(body or {}).encode()
        headers.append((b"content-length", str(len(raw)).encode()))
    if key is not None:
        headers.append((b"idempotency-key", key.encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "server": ("127.0.0.1", PORT),
        "client": ("127.0.0.1", 50000), "scheme": "http", "method": method,
        "root_path": "", "path": path, "raw_path": path.encode("ascii"),
        "query_string": query.encode(), "headers": headers, "state": {},
    }
    sent = {"status": 0, "body": b""}
    asked = [False]

    async def receive():
        if not asked[0]:
            asked[0] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    await app(scope, receive, send)
    return sent["status"], json.loads(sent["body"]) if sent["body"] else None


def get(path: str, query: str = "", app=None) -> tuple[int, dict | None]:
    return asyncio.run(_send(app or served(), "GET", path, query=query))


def read(path: str, query: str = "") -> dict:
    status, body = get(path, query)
    assert status == 200, body
    return body


def post(path: str, body: dict | None = None, *, key: str | None = None,
         app=None) -> tuple[int, dict | None]:
    return asyncio.run(_send(app or served(), "POST", path, body=body, key=key))


def done(path: str, body: dict) -> dict:
    status, answer = post(path, body)
    assert status == 200, answer
    return answer


def refused(answer: tuple[int, dict | None]) -> str:
    status, body = answer
    assert status == 422, body
    return body["refused"]


def holds(sentence: str) -> str:
    return f'''
INTERFACE = 1

def refuse_remove(project):
    return {sentence!r}
'''


def fake_git(bin_dir, body: str) -> str:
    """A `git` script holding `body`. Returns PATH with its directory first."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "git"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"])


def _set_user_version(path, version: int) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    finally:
        conn.close()


def _status_rows(path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT key FROM remove_status")]
    finally:
        conn.close()


# -- the table ------------------------------------------------------------------------------

def test_the_gate_matrix_passes():
    assert LB.gate_matrix(page.PAGE)


def test_the_page_declares_its_reads_and_acts_none_the_gateways():
    routes = {r["path"]: r for r in LB.export(page.PAGE)["routes"]}
    assert {p for p, r in routes.items() if r["kind"] == "read"} == {
        "/api/projects", "/api/project", "/api/folder", "/api/kinds", "/api/where",
        "/api/remove-check", "/api/remove-status"}
    assert {p for p, r in routes.items() if r["kind"] == "act"} == {
        "/api/add", "/api/set", "/api/agents-add", "/api/agents-remove", "/api/remove"}
    assert {p for p, r in routes.items() if r.get("confirmed")} == set()
    assert not any(r["gateway"] or r["stream"] for r in routes.values())


def _marks(shape: dict, at: str = "") -> dict[str, str]:
    """Each marked value of an exported shape, by its dotted place (`[]` a list's item)."""
    found = {at: shape["mark"]} if shape.get("mark") else {}
    items = shape.get("items")
    if shape["type"] == "list":
        found.update(_marks(items, at + "[]"))
    elif shape["type"] == "table":
        for key, sub in items.items():
            found.update(_marks(sub, f"{at}.{key}"))
    return found


def test_every_root_is_marked_path_and_every_sentence_redact():
    marks: dict[str, str] = {}
    for route in LB.export(page.PAGE)["routes"]:
        for key, shape in route["returns"].items():
            marks.update({f"{route['path']} {k}": v for k, v in _marks(shape, key).items()})
    roots = {f"{path} {at}.{field}"
             for path, at in (("/api/projects", "projects[]"), ("/api/project", "project"),
                              ("/api/add", "project"), ("/api/set", "project"),
                              ("/api/agents-add", "project"),
                              ("/api/agents-remove", "project"))
             for field in ("root", "notes_root")}
    assert {k for k, v in marks.items() if v == "path"} == roots | {
        "/api/projects home", "/api/folder path", "/api/where store", "/api/where git"}
    assert {k for k, v in marks.items() if v == "redact"} == {
        "/api/kinds broken[].reason", "/api/add notes[]", "/api/add warnings[]",
        "/api/set notes[]", "/api/where note",
        "/api/remove-check refusals[].sentence", "/api/remove-check refusals[].fix",
        "/api/remove-status reason", "/api/remove-status fix",
        "/api/remove-status sentences[]", "/api/remove reason", "/api/remove fix",
        "/api/remove sentences[]"}


def test_the_announcement_offers_the_page_and_imports_none_of_it():
    from moxi_projects import module

    assert module.PAGE == "moxi_projects.page:PAGE"
    assert "ui" in module.OPTIONAL
    assert not hasattr(module, "BUDGETS")
    probe = ("import sys, moxi_projects.module, moxi_projects.cli, moxi_projects.api\n"
             "print(sorted(m for m in sys.modules if m.startswith(('moxi_ui', "
             "'moxi_projects.page'))))")
    done_ = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                           timeout=60, check=True)
    assert done_.stdout.strip() == "[]"


def test_the_export_carries_the_measured_budget():
    exported = LB.export(page.PAGE)
    assert exported["budget"] == {"rss": "90M", "fds": 32} == page.BUDGET


def test_each_component_is_a_built_asset_of_the_page():
    from moxi_projects import module

    assets = set(LB.export(page.PAGE)["assets"])
    assert set(module.COMPONENTS) == {"page", "picker", "card", "add"}
    for name, decl in module.COMPONENTS.items():
        assert decl["script"] == f"{name}.js"
        assert decl["script"] in assets
        assert decl["contract"] == 1
        assert (decl["place"] == "page") == (name == "page")
    assert module.COMPONENTS["card"]["attributes"]["key"]["required"] is True
    assert "{key}" in module.COMPONENTS["picker"]["doc"]
    assert "moxi-change" in module.COMPONENTS["picker"]["doc"]


# -- reads ----------------------------------------------------------------------------------

def test_projects_lists_every_project_by_key_as_the_read_api_does(golden_copy):
    body = read("/api/projects")
    assert body["projects"] == [{**p.as_dict(), "exists": False}
                                for p in api.list_projects()]
    assert [p["key"] for p in body["projects"]] == ["alpha", "Beta", "delta.web", "gamma"]
    alpha = body["projects"][0]
    assert alpha["agents"] == ["lead-a", "helper", "scout"]
    assert alpha["root"] == os.path.join(golden_copy, "alpha")


def test_projects_by_kind(golden_copy):
    assert [p["key"] for p in read("/api/projects", "kind=agent")["projects"]] == ["Beta"]
    assert read("/api/projects", "kind=nothing")["projects"] == []


def test_no_store_yet_lists_nothing_and_makes_no_file(store_path):
    assert read("/api/projects")["projects"] == []
    assert not store_path.exists()


# -- the list's home, notes and exists ---------------------------------------------------

@pytest.fixture
def own_pool(monkeypatch):
    """A stat pool of the test's own, so no thread or pending stat leaks between tests."""
    fresh = pagestat.Pool()
    monkeypatch.setattr(pagestat, "_POOL", fresh)
    return fresh


def _exists(body: dict) -> dict[str, bool | None]:
    return {p["key"]: p["exists"] for p in body["projects"]}


def _register(*rows) -> None:
    store.write(lambda conn: [store.insert(conn, key=k, root=str(r)) for k, r in rows])


def test_exists_says_whether_each_root_is_a_dir_now(tmp_path, own_pool):
    there = tmp_path / "there"
    there.mkdir()
    a_file = tmp_path / "a-file"
    a_file.write_text("")
    _register(("there", there), ("gone", tmp_path / "gone"), ("file", a_file),
              ("under-file", a_file / "sub"))
    assert _exists(read("/api/projects")) == {"there": True, "gone": False, "file": False,
                                              "under-file": False}


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="POSIX permissions, which root ignores")
def test_exists_is_null_when_the_stat_fails_another_way(tmp_path, own_pool):
    shut = tmp_path / "shut"
    (shut / "proj").mkdir(parents=True)
    _register(("proj", shut / "proj"))
    shut.chmod(0)
    try:
        assert _exists(read("/api/projects")) == {"proj": None}
    finally:
        shut.chmod(0o755)


def test_a_stat_that_hangs_reads_null_inside_the_bound_and_is_not_asked_again(
        tmp_path, monkeypatch):
    quick, slow = tmp_path / "quick", tmp_path / "slow"
    quick.mkdir()
    slow.mkdir()
    _register(("quick", quick), ("slow", slow))
    let_go = threading.Event()
    asked: list[str] = []

    def stat(path):
        asked.append(path)
        if path == str(slow):
            let_go.wait(30)
        return os.stat(path)

    monkeypatch.setattr(pagestat, "_POOL", pagestat.Pool(stat=stat))
    try:
        start = time.monotonic()
        assert _exists(read("/api/projects")) == {"quick": True, "slow": None}
        assert time.monotonic() - start < 2
        assert _exists(read("/api/projects")) == {"quick": True, "slow": None}
        assert asked.count(str(slow)) == 1
        assert asked.count(str(quick)) == 2
    finally:
        let_go.set()
    deadline = time.monotonic() + 5
    while not pagestat._POOL._out[str(slow)].ended.is_set():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert _exists(read("/api/projects")) == {"quick": True, "slow": True}
    assert asked.count(str(slow)) == 1
    assert _exists(read("/api/projects")) == {"quick": True, "slow": True}
    assert asked.count(str(slow)) == 2


def test_a_hung_mount_holds_at_most_four_threads(tmp_path):
    let_go = threading.Event()
    pool = pagestat.Pool(stat=lambda path: let_go.wait(30) and os.stat(path))
    before = threading.active_count()
    try:
        found = pool.look([str(tmp_path / f"r{n}") for n in range(9)], timeout_s=0.05)
        assert set(found.values()) == {None}
        assert pool._threads == pagestat.WORKERS == 4
        assert threading.active_count() - before == 4
    finally:
        let_go.set()


def test_home_is_where_moxi_paths_places_it(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "the-home"))
    assert read("/api/projects")["home"] == str(paths.home())
    assert read("/api/projects")["home"] == str(tmp_path / "the-home")


@pytest.mark.skipif(sys.platform == "win32", reason="HOME and the user database")
def test_an_empty_home_reads_as_unset_and_nothing_placing_one_is_null(monkeypatch):
    monkeypatch.setenv("HOME", "")
    monkeypatch.setattr(paths, "_account_home", lambda: "/from/the/user/database")
    assert read("/api/projects")["home"] == "/from/the/user/database"
    monkeypatch.setattr(paths, "_account_home", lambda: None)
    assert read("/api/projects")["home"] is None


def test_notes_installed_says_whether_notes_is(site):
    assert read("/api/projects")["notes_installed"] is False
    site.module("notes")
    moxi.modules.reset()
    assert read("/api/projects")["notes_installed"] is True


# -- the folder read --------------------------------------------------------------------

def at_path(path) -> dict:
    from urllib.parse import quote

    return read("/api/folder", f"path={quote(str(path), safe='')}")


NOTHING = dict.fromkeys(("path", "exists", "git", "project", "how", "key", "taken"))


def test_a_git_dir_a_plain_one_and_a_missing_one(tmp_path, own_pool, store_path):
    repo = tmp_path / "finch-scripts"
    (repo / ".git").mkdir(parents=True)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert at_path(repo) == {"path": str(repo), "exists": True, "git": True,
                               "project": None, "how": None, "key": "finch-scripts",
                               "taken": False}
    assert at_path(plain)["exists"] is True and at_path(plain)["git"] is False
    gone = at_path(tmp_path / "not-yet")
    assert gone["exists"] is False and gone["git"] is None and gone["key"] == "not-yet"
    assert not store_path.exists()


def test_a_worktrees_git_file_counts_as_git(tmp_path, own_pool):
    tree = tmp_path / "wt"
    tree.mkdir()
    (tree / ".git").write_text("gitdir: /somewhere\n")
    assert at_path(tree)["git"] is True


def test_a_file_is_not_a_dir(tmp_path, own_pool):
    a_file = tmp_path / "a-file"
    a_file.write_text("")
    assert at_path(a_file)["exists"] is False and at_path(a_file)["git"] is None


def test_how_the_path_stands_to_the_project_it_is_in(tmp_path, own_pool):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    _register(("Proj", root))
    at_root = at_path(str(root) + os.sep)
    assert (at_root["path"], at_root["project"], at_root["how"]) == (str(root), "Proj",
                                                                     "root")
    assert at_root["key"] == "proj" and at_root["taken"] is True
    inside = at_path(root / "src")
    assert (inside["project"], inside["how"], inside["taken"]) == ("Proj", "inside", False)
    sibling = tmp_path / "proj2"
    sibling.mkdir()
    assert at_path(sibling)["project"] is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
def test_a_symlinked_spelling_of_a_root_is_that_root(tmp_path, own_pool):
    root = tmp_path / "real"
    root.mkdir()
    link = tmp_path / "link"
    link.symlink_to(root)
    _register(("real", root))
    assert at_path(link)["how"] == "root"


def test_a_linked_worktree_outside_any_root_is_a_worktree(tmp_path, own_pool):
    main = tmp_path / "repos" / "main"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    gitdir = main / ".git" / "worktrees" / "wt"
    (gitdir / "commondir").write_text("../..\n")
    tree = tmp_path / "scratch" / "wt"
    tree.mkdir(parents=True)
    (tree / ".git").write_text(f"gitdir: {gitdir}\n")
    _register(("main", main))
    body = at_path(tree)
    assert (body["project"], body["how"], body["git"]) == ("main", "worktree", True)


def test_a_tilde_path_is_taken_as_the_home(tmp_path, own_pool, monkeypatch):
    home = tmp_path / "home"
    (home / "code" / "finch").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    assert at_path("~/code/finch")["path"] == str(home / "code" / "finch")
    assert at_path("~/code/finch")["exists"] is True
    assert at_path("~")["path"] == str(home)
    assert at_path("~/")["path"] == str(home)


@pytest.mark.parametrize("typed", ["code/finch", "", ".", "~someone/code", "~someone"])
def test_a_relative_path_or_another_users_home_says_nothing(typed, own_pool):
    assert at_path(typed) == NOTHING


@pytest.mark.skipif(sys.platform == "win32", reason="HOME and the user database")
def test_a_tilde_path_with_no_home_says_nothing(monkeypatch, own_pool, tmp_path):
    monkeypatch.setenv("HOME", "")
    monkeypatch.setattr(paths, "_account_home", lambda: None)
    assert at_path("~/code") == NOTHING
    assert at_path(tmp_path)["path"] == str(tmp_path)


def test_a_last_segment_that_fails_the_key_rules_gives_no_key(tmp_path, own_pool):
    for name in ("has space", "con", "ends."):
        body = at_path(tmp_path / name)
        assert body["key"] is None and body["taken"] is None, name


def test_a_taken_key_is_found_ignoring_case(tmp_path, own_pool):
    _register(("Finch", tmp_path / "elsewhere"))
    body = at_path(tmp_path / "finch")
    assert (body["key"], body["taken"], body["project"]) == ("finch", True, None)


def test_a_store_that_cant_be_read_answers_the_rest(tmp_path, own_pool, store_path):
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_bytes(b"not a database at all" * 100)
    (tmp_path / "x").mkdir()
    body = at_path(tmp_path / "x")
    assert body == {"path": str(tmp_path / "x"), "exists": True, "git": False,
                    "project": None, "how": None, "key": "x", "taken": None}


def test_a_path_stat_that_hangs_reads_null_inside_the_bound_and_is_not_asked_again(
        tmp_path, monkeypatch):
    slow = tmp_path / "slow"
    slow.mkdir()
    let_go = threading.Event()
    asked: list[str] = []

    def stat(path):
        asked.append(path)
        if path == str(slow):
            let_go.wait(30)
        return os.stat(path)

    monkeypatch.setattr(pagestat, "_POOL", pagestat.Pool(stat=stat))
    try:
        start = time.monotonic()
        body = at_path(slow)
        assert time.monotonic() - start < 2
        assert body["exists"] is None and body["git"] is False and body["key"] == "slow"
        assert at_path(slow)["exists"] is None
        assert asked.count(str(slow)) == 1
    finally:
        let_go.set()


def test_a_typed_tree_whose_every_stat_hangs_is_never_looked_up(tmp_path, monkeypatch):
    root = tmp_path / "mount" / "proj"
    (root / "src").mkdir(parents=True)
    _register(("proj", root))
    hung = str(tmp_path / "mount")
    let_go = threading.Event()
    touched: list[str] = []

    def hanging(real):
        def stat(path, *args, **kwargs):
            text = os.fsdecode(os.fspath(path)) if not isinstance(path, int) else ""
            if text == hung or text.startswith(hung + os.sep):
                touched.append(text)
                let_go.wait(30)
            return real(path, *args, **kwargs)
        return stat

    monkeypatch.setattr(pagestat, "_POOL", pagestat.Pool(stat=hanging(os.stat)))
    try:
        # Every stat under the mount hangs from here, the pool's and any other's.
        monkeypatch.setattr(os, "stat", hanging(os.stat))
        monkeypatch.setattr(os, "lstat", hanging(os.lstat))
        start = time.monotonic()
        body = at_path(root / "src")
        assert time.monotonic() - start < 2
        assert (body["exists"], body["project"], body["how"]) == (None, None, None)
        assert (body["key"], body["taken"]) == ("src", False)
        # Only the pool's two stats reached the mount; nothing else waited on it.
        assert sorted(touched) == sorted([str(root / "src"), str(root / "src" / ".git")])
    finally:
        let_go.set()


def test_the_path_read_writes_nothing(golden_copy, store_path, tmp_path, own_pool):
    before = store_path.read_bytes(), store_path.stat().st_mtime_ns
    at_path(tmp_path / "new-one")
    at_path(os.path.join(golden_copy, "alpha"))
    assert (store_path.read_bytes(), store_path.stat().st_mtime_ns) == before
    assert not (tmp_path / "new-one").exists()


# -- the remove check -------------------------------------------------------------------

def check_of(key: str) -> dict:
    return read("/api/remove-check", f"key={key}")


@pytest.fixture
def unchanged(golden_copy, store_path):
    """The store's bytes and mtime, checked unchanged when the test ends."""
    before = store_path.read_bytes(), store_path.stat().st_mtime_ns
    yield golden_copy
    assert (store_path.read_bytes(), store_path.stat().st_mtime_ns) == before


def test_nothing_holding_the_project_checks_clean(unchanged):
    assert check_of("GAMMA") == {"key": "gamma", "refusals": []}


def test_associated_agents_are_projects_own_refusal(unchanged):
    body = check_of("alpha")
    assert body["refusals"] == [{"module": "projects", "fix": None,
                                 "sentence": "project 'alpha' still has associated agents: "
                                             "helper scout"}]


def test_each_contributions_sentence_is_a_row_with_no_fix(site, unchanged):
    site.contribution("svc", holds("services run for it; `moxi services stop` frees it"))
    site.contribution("links", holds("two watches name it; `moxi links clear` frees it"))
    rows = check_of("alpha")["refusals"]
    assert rows[0]["module"] == "projects"
    assert sorted((r["module"], r["sentence"], r["fix"]) for r in rows[1:]) == [
        ("links", "two watches name it; `moxi links clear` frees it", None),
        ("svc", "services run for it; `moxi services stop` frees it", None)]


def test_a_broken_contribution_is_a_row_with_its_reason_and_fix_and_the_rest_still_ask(
        site, unchanged):
    site.contribution("gone", "raise ImportError('gone')\n")
    site.contribution("raises", "INTERFACE = 1\n\ndef refuse_remove(project):\n"
                                "    raise RuntimeError('boom')\n")
    site.contribution("svc", holds("services run for it"))
    rows = {r["module"]: r for r in check_of("gamma")["refusals"]}
    assert set(rows) == {"gone", "raises", "svc"}
    assert "won't load" in rows["gone"]["sentence"]
    assert "moxi uninstall gone" in rows["gone"]["fix"]
    assert "RuntimeError: boom" in rows["raises"]["sentence"]
    assert "moxi uninstall raises" in rows["raises"]["fix"]
    assert rows["svc"] == {"module": "svc", "sentence": "services run for it", "fix": None}


def test_no_such_key_is_422_as_the_project_read_answers(unchanged):
    said = refused(get("/api/remove-check", "key=nosuch"))
    assert said == refused(get("/api/project", "key=nosuch"))


def test_the_check_on_no_store_is_422_and_makes_no_file(store_path):
    refused(get("/api/remove-check", "key=any"))
    assert not store_path.exists()


def test_project_matches_its_key_ignoring_case(golden_copy):
    assert read("/api/project", "key=ALPHA")["project"] == api.get("alpha").as_dict()
    said = refused(get("/api/project", "key=nosuch"))
    assert "no project is named 'nosuch'" in said and "fix: " in said


def test_kinds_names_what_loaded_and_what_did_not(site):
    site.contribution("notes", 'INTERFACE = 2\nKINDS = {"topic": ("a notes topic", '
                               'lambda fields: None, "dir")}\n')
    site.contribution("broken", "INTERFACE = 9\n")
    body = read("/api/kinds")
    found = {k["name"]: k for k in body["found"]}
    assert found["topic"] == {"name": "topic", "doc": "a notes topic", "module": "notes",
                              "mint": "dir"}
    assert found["project"]["module"] == "projects" and found["project"]["mint"] == "git"
    assert [b["module"] for b in body["broken"]] == ["broken"]
    assert "INTERFACE" in body["broken"][0]["reason"]


def test_where_names_the_store_file_and_the_git_found(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", fake_git(tmp_path / "bin", "exit 0"))
    body = read("/api/where")
    assert body == {"store": store.path(), "git": str(tmp_path / "bin" / "git"),
                    "note": None}
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert read("/api/where")["git"] is None


def test_a_read_writes_nothing(golden_copy, store_path):
    before = store_path.read_bytes()
    for path, query in (("/api/projects", ""), ("/api/project", "key=alpha"),
                        ("/api/kinds", ""), ("/api/where", ""),
                        ("/api/remove-status", f"job={KEY}")):
        get(path, query)
    assert store_path.read_bytes() == before


# -- the error map --------------------------------------------------------------------------

def test_a_newer_store_is_refused_with_the_upgrade(golden_copy, store_path):
    _set_user_version(store_path, api._VERSION + 1)
    for answer in (get("/api/projects"), post("/api/agents-add",
                                              {"key": "gamma", "name": "scout"})):
        said = refused(answer)
        assert "newer than" in said and "fix: upgrade it with `moxi install projects`" in said


def test_an_unreadable_store_is_refused(store_path):
    store_path.parent.mkdir(parents=True)
    store_path.write_bytes(b"this is not a database, not at all, " * 200)
    assert refused(get("/api/projects"))


def test_a_store_locked_past_the_wait_is_409(golden_copy, store_path, monkeypatch):
    monkeypatch.setattr(store, "TIMEOUT", 0.05)
    holder_conn = sqlite3.connect(store_path, isolation_level=None)
    try:
        holder_conn.execute("BEGIN IMMEDIATE")
        status, body = post("/api/agents-add", {"key": "gamma", "name": "scout"})
    finally:
        holder_conn.execute("ROLLBACK")
        holder_conn.close()
    assert status == 409 and "try again" in body["conflict"]


@pytest.mark.parametrize("body, words", [
    ({"key": "web"}, "needs a root"),                                    # exit 2
    ({"key": "bad key", "root": "/x"}, "not a valid project key"),       # exit 3
    ({"key": "web", "root": "/x", "kind": "nosuch"}, "kind 'nosuch' is unknown"),
])
def test_a_refused_add_is_422_with_its_reason_and_fix(body, words, store_path):
    said = refused(post("/api/add", body))
    assert words in said and "; fix: " in said
    assert not store_path.exists()


def test_a_contribution_that_wont_load_is_skipped_and_the_add_answer_warns(site,
                                                                          tmp_path):
    site.contribution("broken", "raise ImportError('gone')\n")
    root = str(tmp_path / "web")
    body = done("/api/add", {"key": "web", "root": root})
    assert body["project"] == api.get("web").as_dict()
    assert body["notes"] == [acts.root_note(root)]
    [warning] = body["warnings"]
    assert warning.startswith("skipped broken, which won't load")
    assert "defaults and checks were not applied" in warning
    assert "moxi uninstall broken" in warning


def test_an_add_of_an_unknown_kind_past_a_contribution_that_wont_load_is_refused(
        site, tmp_path, store_path):
    site.contribution("broken", "raise ImportError('gone')\n")
    said = refused(post("/api/add", {"key": "web", "root": str(tmp_path / "web"),
                                     "kind": "topic"}))
    assert "won't load" in said and "moxi uninstall broken" in said
    assert not store_path.exists()


def test_an_add_that_skips_nothing_answers_no_warnings(tmp_path):
    assert done("/api/add", {"key": "web", "root": str(tmp_path / "web")})["warnings"] == []


@pytest.mark.parametrize("root", ["relative/web", "~someone/web", "~someone"])
def test_a_root_that_is_relative_or_another_users_home_is_refused(root, tmp_path,
                                                                  monkeypatch, store_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    def never(*args, **kwargs):
        raise AssertionError("the page made a path absolute")

    monkeypatch.setattr(rules, "absolutize", never)
    said = refused(post("/api/add", {"key": "web", "root": root, "create": True}))
    assert "is not an absolute path" in said
    assert not store_path.exists() and list(cwd.iterdir()) == []


@pytest.mark.parametrize("root", ["elsewhere", "~someone/elsewhere"])
def test_a_set_to_a_relative_root_is_refused(root, golden_copy):
    said = refused(post("/api/set", {"key": "gamma", "root": root}))
    assert "is not an absolute path" in said
    assert api.get("gamma").root == os.path.join(golden_copy, "gamma")


# -- ~ on add and set -------------------------------------------------------------------

@pytest.fixture
def home_dir(tmp_path, monkeypatch, terminal_record) -> str:
    """HOME set to a folder of the test's own, in this process and in the terminal's
    record alike, so the comparison passes."""
    home = tmp_path / "the-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    terminal_record()
    return str(home)


def test_an_add_expands_a_tilde_root_by_the_home(home_dir, store_path):
    body = done("/api/add", {"key": "finch", "root": "~/code/finch"})
    assert body["project"]["root"] == os.path.join(home_dir, "code", "finch")
    assert api.get("finch").root == os.path.join(home_dir, "code", "finch")
    assert done("/api/add", {"key": "home-itself", "root": "~"})["project"]["root"] == \
        home_dir


@posix_only
def test_an_add_with_create_makes_the_expanded_root(home_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", fake_git(tmp_path / "bin", "exit 0"))
    done("/api/add", {"key": "made", "root": "~/made", "create": True, "kind": "project"})
    assert os.path.isdir(os.path.join(home_dir, "made"))


def test_a_notes_root_is_expanded_too(home_dir, site):
    site.module("notes")
    moxi.modules.reset()
    body = done("/api/add", {"key": "noted", "root": "~/code/noted",
                             "notes_root": "~/notes/noted"})
    assert body["project"]["notes_root"] == os.path.join(home_dir, "notes", "noted")


def test_a_set_expands_a_tilde_root(home_dir, golden_copy):
    body = done("/api/set", {"key": "gamma", "root": "~/moved"})
    assert body["project"]["root"] == os.path.join(home_dir, "moved")


@pytest.mark.skipif(sys.platform == "win32", reason="HOME and the user database")
def test_a_tilde_root_with_no_home_is_refused(monkeypatch, terminal_record, store_path):
    monkeypatch.setenv("HOME", "")
    monkeypatch.setattr(paths, "_account_home", lambda: None)
    terminal_record()
    said = refused(post("/api/add", {"key": "finch", "root": "~/code/finch"}))
    assert "is not an absolute path" in said
    assert not store_path.exists()


def test_a_tilde_root_is_refused_by_the_comparison_before_any_expansion(
        home_dir, terminal_record, monkeypatch, store_path):
    words = store_moved(terminal_record)

    def never(*args, **kwargs):
        raise AssertionError("expanded before the comparison passed")

    monkeypatch.setattr(pagehandlers, "expand_tilde", never)
    for route, sent in (("/api/add", {"key": "finch", "root": "~/code/finch"}),
                        ("/api/set", {"key": "gamma", "root": "~/moved"})):
        assert refused(post(route, sent)).startswith(words)
    assert not store_path.exists()


def test_a_home_that_differs_from_the_terminals_refuses_a_tilde_root(
        home_dir, terminal_record, store_path):
    terminal_record(terminal(HOME=os.path.join(home_dir, "other")))
    said = refused(post("/api/add", {"key": "finch", "root": "~/code/finch"}))
    assert "the home folder" in said
    assert not store_path.exists()


# -- add ------------------------------------------------------------------------------------

def test_add_writes_the_row_and_notes_a_root_that_isnt_there_yet(tmp_path):
    root = str(tmp_path / "web")
    body = done("/api/add", {"key": "web", "root": root, "lead": "lead-a"})
    assert body["project"] == api.get("web").as_dict()
    assert body["project"]["lead"] == "lead-a"
    assert body["notes"] == [acts.root_note(root)]
    assert not os.path.exists(root)


def test_add_of_a_taken_key_is_refused(golden_copy, tmp_path):
    said = refused(post("/api/add", {"key": "GAMMA", "root": str(tmp_path / "x")}))
    assert "gamma" in said


@posix_only
def test_add_with_create_makes_the_root_and_runs_git_init(tmp_path, monkeypatch):
    log = tmp_path / "git.log"
    monkeypatch.setenv("PATH", fake_git(tmp_path / "bin", f'echo "$@" >> "{log}"\n'
                                                          'mkdir -p "$2/.git"\nexit 0'))
    root = tmp_path / "a" / "web"
    body = done("/api/add", {"key": "web", "root": str(root), "create": True})
    assert (root / ".git").is_dir() and log.read_text().split() == ["init", str(root)]
    assert body["project"]["root"] == str(root)
    assert body["notes"] == [f"made {tmp_path / 'a'} and ran git init in {root}"]


def test_the_page_gives_git_init_20_seconds_and_the_verb_keeps_30(tmp_path, monkeypatch):
    assert pagehandlers.GIT_TIMEOUT == 20
    assert inspect.signature(acts.add).parameters["git_timeout"].default == 30
    seen = {}
    real = acts.add

    def spy(*args, **named):
        seen.update(named)
        return real(*args, **named)

    monkeypatch.setattr(acts, "add", spy)
    done("/api/add", {"key": "web", "root": str(tmp_path / "web")})
    assert seen["git_timeout"] == 20 and seen["create"] is False


@posix_only
def test_a_hanging_git_answers_inside_the_bound_with_what_is_left(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", fake_git(tmp_path / "bin", "sleep 30"))
    monkeypatch.setattr(pagehandlers, "GIT_TIMEOUT", 0.3)
    root = tmp_path / "slow"
    started = time.monotonic()
    said = refused(post("/api/add", {"key": "slow", "root": str(root), "create": True}))
    # Well under the 30 s a hung git would run: the bound fired, and the answer came.
    assert time.monotonic() - started < 15
    assert "ran past 0.3 s" in said and f"{root} is left as made" in said
    assert f"git init {root}" in said and f"moxi projects add slow --root {root}" in said
    with pytest.raises(api.NotFound):
        api.get("slow")


def test_create_with_no_git_names_the_lasting_fix(tmp_path, monkeypatch, store_path):
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    root = tmp_path / "web"
    said = refused(post("/api/add", {"key": "web", "root": str(root), "create": True}))
    assert "git is not on PATH" in said and "install git" in said
    assert "set PATH in the service manager's environment" in said
    assert "`moxi ui stop`" in said
    assert f"`moxi projects add web --root {root} --create` in a terminal" in said
    assert not root.exists() and not store_path.exists()


@posix_only
def test_the_lasting_fix_for_no_git_carries_the_lead_and_notes_root(site, tmp_path,
                                                                    monkeypatch, store_path):
    site.module("notes")
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    root = tmp_path / "web site"
    notes = tmp_path / "web notes"
    said = refused(post("/api/add", {"key": "web", "root": str(root), "kind": "agent",
                                     "lead": "o'neil", "notes_root": str(notes),
                                     "create": True}))
    command = (f"moxi projects add web --root '{root}' --kind agent "
               f"--lead 'o'\"'\"'neil' --notes-root '{notes}' --create")
    assert f"`{command}` in a terminal" in said
    assert not root.exists() and not store_path.exists()


def test_create_for_a_refuse_kind_is_refused_before_anything_is_made(site, tmp_path):
    site.contribution("remote", 'INTERFACE = 2\nKINDS = {"remote": ("made elsewhere", '
                                'lambda fields: None, "refuse")}\n')
    root = tmp_path / "web"
    said = refused(post("/api/add", {"key": "web", "root": str(root), "kind": "remote",
                                     "create": True}))
    assert "can't be made with --create" in said and not root.exists()


# -- set and the agents ---------------------------------------------------------------------

def test_set_changes_the_named_fields_and_notes_a_new_root(golden_copy, tmp_path):
    root = str(tmp_path / "moved")
    body = done("/api/set", {"key": "gamma", "root": root, "kind": "agent"})
    assert body["project"] == api.get("gamma").as_dict()
    assert (body["project"]["root"], body["project"]["kind"]) == (root, "agent")
    assert body["notes"] == [acts.root_note(root)]


def test_set_sets_and_clears_the_lead(golden_copy):
    assert done("/api/set", {"key": "Beta", "lead": "lead-b"})["project"]["lead"] == "lead-b"
    body = done("/api/set", {"key": "beta", "no_lead": True})
    assert body["project"]["lead"] is None and body["notes"] == []


def test_set_refuses_a_lead_named_and_cleared_and_nothing_named(golden_copy):
    assert "not both" in refused(post("/api/set", {"key": "gamma", "lead": "x",
                                                   "no_lead": True}))
    assert "nothing to change" in refused(post("/api/set", {"key": "gamma"}))
    assert "nothing to change" in refused(post("/api/set", {"key": "gamma",
                                                            "no_lead": False}))


def test_set_of_a_notes_root_without_notes_is_refused(golden_copy, tmp_path):
    said = refused(post("/api/set", {"key": "gamma", "notes_root": str(tmp_path)}))
    assert "moxi install notes" in said


def test_agents_add_and_remove(golden_copy):
    body = done("/api/agents-add", {"key": "gamma", "name": "scout"})
    assert body["project"]["associated"] == ["scout"]
    body = done("/api/agents-remove", {"key": "gamma", "name": "scout"})
    assert body["project"] == api.get("gamma").as_dict()
    assert body["project"]["associated"] == []
    assert refused(post("/api/agents-remove", {"key": "gamma", "name": "scout"}))


# -- remove ---------------------------------------------------------------------------------

def status_of(job: str = JOB, app=None) -> dict:
    status, body = get("/api/remove-status", f"job={job}", app=app)
    assert status == 200, body
    return body


def removing(key: str, job: str = JOB, app=None) -> dict:
    """A remove of `key` pressed under `job`, answered 200; its outcome."""
    status, body = post("/api/remove", {"key": key, "job": job}, app=app)
    assert status == 200, body
    assert body == status_of(job)
    return body


def test_a_remove_answers_its_outcome_at_once(golden_copy):
    body = removing("GAMMA")
    with pytest.raises(api.NotFound):
        api.get("gamma")
    assert body == {"job": JOB, "project": "gamma", "created": body["created"],
                    "state": "removed", "key": "gamma", "reason": None, "fix": None,
                    "sentences": [], "exit_code": None}


def test_a_project_with_agents_is_refused_and_the_refusal_kept(golden_copy):
    body = removing("alpha")
    assert body["state"] == "refused" and body["exit_code"] == 3
    assert "helper scout" in body["sentences"][0] and "agents remove alpha" in body["fix"]
    assert api.get("alpha")


def test_every_contribution_sentence_comes_in_the_answer(site, golden_copy):
    site.contribution("svc", holds("services run for it; `moxi services stop` frees it"))
    site.contribution("links", holds("two watches name it; `moxi links clear` frees it"))
    assert sorted(removing("gamma")["sentences"]) == [
        "links: two watches name it; `moxi links clear` frees it",
        "svc: services run for it; `moxi services stop` frees it"]
    assert api.get("gamma")


def test_a_contribution_that_wont_load_is_a_kept_refusal(site, golden_copy):
    site.contribution("broken", "raise ImportError('gone')\n")
    body = removing("gamma")
    assert body["state"] == "refused" and body["exit_code"] == 1
    assert "won't load" in body["sentences"][0] and "moxi uninstall broken" in body["fix"]
    assert api.get("gamma")


def test_an_unknown_key_in_a_store_that_exists_is_a_kept_refusal(golden_copy):
    body = removing("nosuch")
    assert body["state"] == "refused" and "no project is named 'nosuch'" in body["reason"]


def test_no_store_file_is_answered_at_once_and_makes_no_file(store_path):
    said = refused(post("/api/remove", {"key": "nosuch", "job": JOB}))
    assert "no project is named 'nosuch'" in said
    assert not store_path.exists() and not store_path.parent.exists()
    assert "no remove request" in refused(get("/api/remove-status", f"job={JOB}"))


def test_a_newer_store_is_answered_at_once_and_left_as_it_was(golden_copy, store_path):
    _set_user_version(store_path, api._VERSION + 1)
    before = store_path.read_bytes()
    assert "newer than" in refused(post("/api/remove", {"key": "gamma", "job": JOB}))
    assert store_path.read_bytes() == before


def test_an_unreadable_store_is_answered_at_once_and_left_as_it_was(store_path):
    store_path.parent.mkdir(parents=True)
    store_path.write_bytes(b"this is not a database, not at all, " * 200)
    before = store_path.read_bytes()
    assert refused(post("/api/remove", {"key": "gamma", "job": JOB}))
    assert store_path.read_bytes() == before


def test_a_store_locked_past_the_wait_is_409_and_keeps_nothing(golden_copy, store_path,
                                                               monkeypatch):
    monkeypatch.setattr(store, "TIMEOUT", 0.05)
    holder_conn = sqlite3.connect(store_path, isolation_level=None)
    try:
        holder_conn.execute("BEGIN IMMEDIATE")
        removed = post("/api/remove", {"key": "gamma", "job": JOB})
        # A refusal about the project whose own write is locked out is the lock's answer.
        held = post("/api/remove", {"key": "alpha", "job": OTHER_JOB})
    finally:
        holder_conn.execute("ROLLBACK")
        holder_conn.close()
    assert removed[0] == 409 and held[0] == 409
    assert api.get("gamma") and _status_rows(store_path) == []


def test_two_presses_sent_at_once_under_one_job_both_answer_the_first_outcome(
        golden_copy, store_path, monkeypatch):
    both_in = threading.Barrier(2, timeout=30)
    real = acts.remove

    def together(key, **named):
        both_in.wait()
        return real(key, **named)

    monkeypatch.setattr(acts, "remove", together)

    async def at_once():
        app = served()
        return await asyncio.gather(*(
            _send(app, "POST", "/api/remove", body={"key": "gamma", "job": JOB})
            for _ in range(2)))

    first, second = asyncio.run(at_once())
    assert first[0] == second[0] == 200, (first, second)
    assert first[1] == second[1] and first[1]["state"] == "removed"
    assert _status_rows(store_path) == [JOB]
    with pytest.raises(api.NotFound):
        api.get("gamma")


def test_a_job_sent_with_another_project_is_422(golden_copy):
    app = served()
    removing("gamma", app=app)
    assert refused(post("/api/remove", {"key": "Beta", "job": JOB}, app=app)) == \
        pagehandlers.KEY_REUSED
    assert refused(post("/api/remove", {"key": "Beta", "job": JOB})) == \
        pagehandlers.KEY_REUSED
    assert api.get("Beta")


def test_a_repeat_of_a_job_answers_the_kept_row_and_acts_no_more(golden_copy, tmp_path):
    first = removing("gamma")
    # gamma is back: a second run of the act would remove it again.
    acts.add("gamma", root=str(tmp_path / "gamma"))
    assert removing("GAMMA") == first
    assert api.get("gamma")
    # A refused one, too.
    refusal = removing("alpha", OTHER_JOB)
    acts.remove_agent("alpha", "helper")
    acts.remove_agent("alpha", "scout")
    assert removing("alpha", OTHER_JOB) == refusal
    assert api.get("alpha")


def test_a_repeat_past_a_day_is_a_new_act(golden_copy, monkeypatch):
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(time.time() - store.STATUS_KEPT - 60))
    with monkeypatch.context() as patched:
        patched.setattr(store, "now", lambda: old)
        post("/api/remove", {"key": "gamma", "job": JOB})
    # The day's row is ignored, so the act runs again and finds no gamma.
    body = removing("gamma")
    assert body["state"] == "refused" and "no project is named 'gamma'" in body["reason"]


def test_a_remove_without_its_job_is_refused_before_the_handler(golden_copy):
    status, _ = post("/api/remove", {"key": "gamma"})
    assert status == 400
    assert api.get("gamma")


@pytest.mark.parametrize("job", ["K" * 43, JOB.upper(), JOB[:-1], JOB + "0", ""])
def test_a_job_that_is_not_32_hex_characters_is_refused(job, golden_copy, store_path):
    said = refused(post("/api/remove", {"key": "gamma", "job": job}))
    assert "32 hexadecimal characters" in said
    assert api.get("gamma") and _status_rows(store_path) == []


def test_the_remove_is_an_ordinary_act_with_no_confirm_or_status_link():
    routes = {r["path"]: r for r in LB.export(page.PAGE)["routes"]}
    assert not routes["/api/remove"].get("confirmed")
    assert not routes["/api/remove"].get("status")


@pytest.mark.parametrize("outcome", [{"key": "gamma"}, {"state": "removed"}, {}])
def test_a_kept_outcome_without_its_state_or_key_is_unreadable(outcome, golden_copy,
                                                               store_path):
    removing("gamma")
    conn = sqlite3.connect(store_path)
    try:
        conn.execute("UPDATE remove_status SET outcome = ? WHERE key = ?",
                     (json.dumps(outcome), JOB))
        conn.commit()
    finally:
        conn.close()
    for answer in (get("/api/remove-status", f"job={JOB}"),
                   post("/api/remove", {"key": "gamma", "job": JOB})):
        said = refused(answer)
        assert f"the projects store {store_path} can't be read" in said
        assert "fix: move the file aside" in said


def test_a_status_read_of_a_version_1_store_answers_no_such_request(v1_copy, store_path):
    before = store_path.read_bytes()
    assert "no remove request" in refused(get("/api/remove-status", f"job={JOB}"))
    assert store_path.read_bytes() == before


def test_the_store_the_page_acts_on_is_the_verbs(golden_copy):
    assert read("/api/where")["store"] == api._store_path()
    assert shutil.which("git") == read("/api/where")["git"]


# --- the terminal comparison ----------------------------------------------------------------

#: Every write route, with a body it would otherwise act on in the golden store, and
#: whether it consults the contributions. The add with create names a root that doesn't
#: exist yet, so nothing being made is seen.
WRITES = (
    ("/api/add", lambda base: {"key": "new", "root": os.path.join(base, "new")}, True),
    ("/api/add", lambda base: {"key": "minted", "root": os.path.join(base, "minted"),
                               "create": True}, True),
    ("/api/set", lambda base: {"key": "gamma", "lead": "lead-b"}, True),
    ("/api/agents-add", lambda base: {"key": "gamma", "name": "helper"}, False),
    ("/api/agents-remove", lambda base: {"key": "alpha", "name": "helper"}, False),
    ("/api/remove", lambda base: {"key": "gamma"}, True),
)
WRITE_IDS = ["add", "add-create", "set", "agents-add", "agents-remove", "remove"]
#: A folder no test makes, under the file system's root.
ELSEWHERE = os.path.join(os.path.abspath(os.sep), "elsewhere")


def terminal(**changes: str | None) -> dict[str, str]:
    """This process's environment with `changes` (None takes a name out): what a terminal
    that differs in those names would have recorded."""
    env = dict(os.environ)
    for name, value in changes.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def store_moved(terminal_record) -> str:
    """A terminal whose projects store is elsewhere. Returns the line's first words."""
    terminal_record(terminal(MOXI_PROJECTS_DATA_HOME=ELSEWHERE))
    return (f"the projects store here is {store.path()}, but the last run in a terminal "
            f"used {os.path.join(ELSEWHERE, 'projects', 'projects.db')}")


def no_record(terminal_record) -> str:
    os.unlink(paths.terminal_record())
    return "Moxi has no record of the environment your terminal uses"


def home_moved(terminal_record) -> str:
    """A terminal whose home folder is elsewhere, the projects store pinned in both."""
    assert os.environ.get("MOXI_PROJECTS_HOME")
    terminal_record(terminal(HOME=ELSEWHERE))
    return f"the home folder here is {paths.home()}, but the last run in a terminal used"


def submit(route: str, body: dict) -> tuple[int, dict | None]:
    return post(route, {**body, "job": JOB} if route == "/api/remove" else body)


@pytest.mark.parametrize("differs", [store_moved, no_record, home_moved],
                         ids=["store", "no-record", "home"])
@pytest.mark.parametrize("route, body, consults", WRITES, ids=WRITE_IDS)
def test_every_write_route_refuses_with_the_line_and_writes_nothing(
        golden_copy, store_path, terminal_record, route, body, consults, differs):
    words = differs(terminal_record)
    before = store_path.read_bytes()
    sent = body(golden_copy)
    said = refused(submit(route, sent))
    assert said.startswith(words), said
    assert said == pagecheck.line()
    assert store_path.read_bytes() == before
    if "root" in sent:
        assert not os.path.lexists(sent["root"])
    # A remove refused this way keeps no row, and its repeat is answered the same.
    assert acts.remove_status(JOB) is None
    assert refused(submit(route, sent)) == said


@pytest.fixture
def services_moved(site, monkeypatch, terminal_record) -> str:
    """A contribution from `services`, whose folders a terminal with another `MOXI_HOME`
    placed elsewhere, while the projects store is pinned in both. Returns the line's
    first words."""
    site.contribution("services", "INTERFACE = 2\nKINDS = {'svc': ('a service', "
                                  "lambda fields: None, 'dir')}\n")
    monkeypatch.delenv("MOXI_SERVICES_HOME", raising=False)
    monkeypatch.setenv("MOXI_HOME", os.path.join(str(paths.home()), "page-moxi"))
    assert os.environ.get("MOXI_PROJECTS_HOME")
    terminal_record(terminal(MOXI_HOME=ELSEWHERE))
    here = os.path.join(str(paths.home()), "page-moxi", "data", "services")
    return (f"services' data folder here is {here}, but the last run in a terminal used "
            f"{os.path.join(ELSEWHERE, 'data', 'services')}")


@pytest.mark.parametrize("route, body, consults", WRITES, ids=WRITE_IDS)
def test_a_contributions_dir_that_differs_refuses_the_routes_that_consult_it(
        golden_copy, store_path, services_moved, route, body, consults):
    sent = body(golden_copy)
    if not consults:
        status, answer = submit(route, sent)
        assert status == 200, answer
        return
    before = store_path.read_bytes()
    said = refused(submit(route, sent))
    assert services_moved in said, said
    assert "Set MOXI_HOME to " + ELSEWHERE in said
    assert store_path.read_bytes() == before


def test_the_line_names_the_variable_and_the_restart(golden_copy, terminal_record):
    store_moved(terminal_record)
    said = refused(post("/api/set", {"key": "gamma", "lead": "lead-b"}))
    assert f"Set MOXI_PROJECTS_DATA_HOME to {ELSEWHERE}" in said
    assert "moxi ui stop" in said


def test_the_comparison_comes_before_a_removes_kept_row(golden_copy, terminal_record):
    removing("gamma")
    store_moved(terminal_record)
    # A repeat of the remove's job is refused now ...
    assert refused(post("/api/remove", {"key": "gamma", "job": JOB})) == pagecheck.line()
    # ... though the status read still gives the kept outcome.
    assert status_of()["state"] == "removed"


def test_a_write_goes_ahead_once_the_record_agrees_again(golden_copy, terminal_record):
    store_moved(terminal_record)
    refused(post("/api/set", {"key": "gamma", "lead": "lead-b"}))
    terminal_record()
    assert done("/api/set", {"key": "gamma", "lead": "lead-b"})["project"]["lead"] == "lead-b"


@pytest.mark.parametrize("differs", [store_moved, no_record], ids=["store", "no-record"])
def test_reads_go_on_and_where_carries_the_line_as_a_note(golden_copy, terminal_record,
                                                          differs):
    assert read("/api/where")["note"] is None
    words = differs(terminal_record)
    note = read("/api/where")["note"]
    assert note is not None and note.startswith(words)
    assert note == pagecheck.line()
    assert [p["key"] for p in read("/api/projects")["projects"]] == [
        "alpha", "Beta", "delta.web", "gamma"]
    assert read("/api/project", "key=gamma")["project"]["key"] == "gamma"


def test_where_notes_a_contributions_dir_that_differs(golden_copy, services_moved):
    assert services_moved in read("/api/where")["note"]


def test_the_page_server_never_records(golden_copy, terminal_record):
    record = paths.terminal_record()
    no_record(terminal_record)
    for path, query in (("/api/projects", ""), ("/api/where", ""), ("/api/kinds", "")):
        get(path, query)
    refused(post("/api/set", {"key": "gamma", "lead": "lead-b"}))
    assert not os.path.exists(record)


def test_the_verbs_record_nothing_themselves(tmp_path, capsys):
    record = paths.terminal_record()
    os.unlink(record)
    where = tmp_path / "termrow"
    assert cli.main(["add", "termrow", "--root", str(where)]) == 0
    assert cli.main(["set", "termrow", "--lead", "lead-a"]) == 0
    assert cli.main(["remove", "termrow"]) == 0
    assert not os.path.exists(record)
    found = modules.load("projects", interface=1)
    assert found.setup == "moxi_projects.store:setup"
    assert set(found.stores) == {"projects"} and not found.internal_env


def ui_found(**changes):
    """What loading ui gives, with `changes`."""
    found = modules.load("ui")
    assert found.ok, found.reason
    return found._replace(**changes)


@pytest.mark.parametrize("found, fix", [
    (lambda: ui_found(state=moxi.modules.BROKEN, interfaces=frozenset({1}),
                      reason="moxi-ui 0.1.0 offers interfaces {1}; the caller speaks 2"),
     "update moxi-ui to a release that offers interface 2"),
    (lambda: ui_found(state=moxi.modules.ABSENT, reason="moxi-ui is not installed"),
     "install it with moxi install ui"),
    (lambda: ui_found(state=moxi.modules.BROKEN,
                      reason="importing its API module moxi_ui.api failed: ImportError"),
     "repair or reinstall moxi-ui"),
], ids=["older", "absent", "broken"])
def test_without_ui_at_interface_2_every_write_refuses_with_the_reason(
        golden_copy, store_path, monkeypatch, found, fix):
    gives = found()
    real = moxi.modules.load
    monkeypatch.setattr(moxi.modules, "load",
                        lambda name, **kw: gives if name == "ui" else real(name, **kw))
    before = store_path.read_bytes()
    for route, body, _ in WRITES:
        said = refused(submit(route, body(golden_copy)))
        assert gives.reason in said and fix in said and "moxi ui stop" in said, said
    assert store_path.read_bytes() == before
    assert read("/api/where")["note"] == pagecheck.unchecked(gives)


def test_a_place_read_outside_the_core_set_refuses(golden_copy, store_path, monkeypatch):
    places = pagecheck.places

    def reads_too_much(env, modules=()):
        env.get("EDITOR")
        return places(env, modules)

    monkeypatch.setattr(pagecheck, "places", reads_too_much)
    before = store_path.read_bytes()
    said = refused(post("/api/set", {"key": "gamma", "lead": "lead-b"}))
    assert "EDITOR" in said and "refuses to write" in said
    assert store_path.read_bytes() == before


class Noting(Mapping):
    """A mapping that notes each name read and refuses to be listed, as ui's check hands
    a place resolver."""

    def __init__(self, data: dict[str, str]):
        self.data, self.read = data, []

    def __getitem__(self, name):
        self.read.append(name)
        return self.data[name]

    def __iter__(self):
        raise TypeError("a place resolver listed the environment")

    def __len__(self):
        raise TypeError("a place resolver measured the environment")


def test_the_places_follow_the_mapping_alone(tmp_path, monkeypatch):
    from moxi.terminal import in_set

    given = {"HOME": str(tmp_path / "h"), "USERPROFILE": str(tmp_path / "h"),
             "MOXI_HOME": str(tmp_path / "m"),
             "XDG_DATA_HOME": str(tmp_path / "xd"), "XDG_STATE_HOME": str(tmp_path / "xs"),
             "XDG_CONFIG_HOME": str(tmp_path / "xc")}
    poison = str(tmp_path / "poison")
    for name in ("HOME", "MOXI_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME",
                 "MOXI_PROJECTS_HOME", "MOXI_PROJECTS_DATA_HOME", "MOXI_NOTES_HOME",
                 "MOXI_NOTES_CONFIG_HOME", "USERPROFILE"):
        monkeypatch.setenv(name, poison)
    env = Noting(given)
    found = pagecheck.places(env, ["notes"])
    assert found == {
        "the projects store": str(tmp_path / "m" / "data" / "projects" / "projects.db"),
        "the home folder": str(tmp_path / "h"),
        "notes' data folder": str(tmp_path / "m" / "data" / "notes"),
        "notes' state folder": str(tmp_path / "m" / "state" / "notes"),
        "notes' config folder": str(tmp_path / "m" / "config" / "notes"),
    }
    assert not any(poison in path for path in found.values())
    assert all(in_set(name) for name in env.read)
    assert pagecheck.places(Noting(given), ["svc"])["svc's data folder"]


def test_the_page_states_its_group_and_order_among_the_pages():
    from moxi_projects import module

    page_decl = module.COMPONENTS["page"]
    assert (page_decl["group"], page_decl["order"]) == ("daily", 60)
