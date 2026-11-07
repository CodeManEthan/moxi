"""projects' page server in process, through loopback's strict app.

Loopback is reached as the page reaches it, through `moxi.modules.load`: projects'
distribution doesn't require it, so these tests don't import it either. The app runs
behind `guard` with an upstream profile, as the runner serves it, with strict projection,
so a handler that sends a key its route doesn't declare, or a value of another type, fails
here. A new app stands for the page server after a restart: its key table is empty.

Every test runs with the site fixture, so the only contributions are the ones a test
installs.
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

import pytest

from moxi import modules
from moxi_projects import acts, api, page, pagehandlers, rules, store

TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321
KEY = "K" * 43
OTHER_KEY = "L" * 43

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


def fake_git(directory, body: str) -> str:
    """A `git` script holding `body`. Returns PATH with its directory first."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "git"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return os.pathsep.join([str(directory), "/usr/bin", "/bin"])


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
        "/api/projects", "/api/project", "/api/kinds", "/api/where", "/api/remove-status"}
    assert {p for p, r in routes.items() if r["kind"] == "act"} == {
        "/api/add", "/api/set", "/api/agents-add", "/api/agents-remove", "/api/remove"}
    assert {p for p, r in routes.items() if r.get("confirmed")} == {"/api/remove"}
    assert routes["/api/remove"]["status"] == "/api/remove-status"
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
        "/api/where store", "/api/where git"}
    assert {k for k, v in marks.items() if v == "redact"} == {
        "/api/kinds broken[].reason", "/api/add notes[]", "/api/add warnings[]",
        "/api/set notes[]",
        "/api/remove-status reason", "/api/remove-status fix",
        "/api/remove-status sentences[]"}


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
    assert body == {"projects": [p.as_dict() for p in api.list_projects()]}
    assert [p["key"] for p in body["projects"]] == ["alpha", "Beta", "delta.web", "gamma"]
    alpha = body["projects"][0]
    assert alpha["agents"] == ["lead-a", "helper", "scout"]
    assert alpha["root"] == os.path.join(golden_copy, "alpha")


def test_projects_by_kind(golden_copy):
    assert [p["key"] for p in read("/api/projects", "kind=agent")["projects"]] == ["Beta"]
    assert read("/api/projects", "kind=nothing") == {"projects": []}


def test_no_store_yet_lists_nothing_and_makes_no_file(store_path):
    assert read("/api/projects") == {"projects": []}
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
    assert body == {"store": store.path(), "git": str(tmp_path / "bin" / "git")}
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
    holder = sqlite3.connect(store_path, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        status, body = post("/api/agents-add", {"key": "gamma", "name": "scout"})
    finally:
        holder.execute("ROLLBACK")
        holder.close()
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


@pytest.mark.parametrize("root", ["relative/web", "~/web", "~"])
def test_a_root_that_is_relative_or_starts_with_a_tilde_is_refused(root, tmp_path,
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


def test_a_set_to_a_relative_root_is_refused(golden_copy):
    said = refused(post("/api/set", {"key": "gamma", "root": "~/elsewhere"}))
    assert "is not an absolute path" in said
    assert api.get("gamma").root == os.path.join(golden_copy, "gamma")


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

def status_of(key: str = KEY, app=None) -> dict:
    status, body = get("/api/remove-status", f"job={key}", app=app)
    assert status == 200, body
    return body


def accepted(answer: tuple[int, dict | None], key: str = KEY) -> None:
    assert answer == (202, {"status": f"/api/remove-status?job={key}"})


def test_a_remove_answers_202_and_its_status_says_removed(golden_copy):
    accepted(post("/api/remove", {"key": "GAMMA"}, key=KEY))
    with pytest.raises(api.NotFound):
        api.get("gamma")
    body = status_of()
    assert body == {"job": KEY, "project": "gamma", "created": body["created"],
                    "state": "removed", "key": "gamma", "reason": None, "fix": None,
                    "sentences": [], "exit_code": None}


def test_a_project_with_agents_is_refused_through_the_status_link(golden_copy):
    accepted(post("/api/remove", {"key": "alpha"}, key=KEY))
    body = status_of()
    assert body["state"] == "refused" and body["exit_code"] == 3
    assert "helper scout" in body["sentences"][0] and "agents remove alpha" in body["fix"]
    assert api.get("alpha")


def test_every_contribution_sentence_comes_through_the_status_link(site, golden_copy):
    site.contribution("svc", holds("services run for it; `moxi services stop` frees it"))
    site.contribution("links", holds("two watches name it; `moxi links clear` frees it"))
    accepted(post("/api/remove", {"key": "gamma"}, key=KEY))
    assert sorted(status_of()["sentences"]) == [
        "links: two watches name it; `moxi links clear` frees it",
        "svc: services run for it; `moxi services stop` frees it"]
    assert api.get("gamma")


def test_a_contribution_that_wont_load_is_refused_through_the_status_link(site,
                                                                          golden_copy):
    site.contribution("broken", "raise ImportError('gone')\n")
    accepted(post("/api/remove", {"key": "gamma"}, key=KEY))
    body = status_of()
    assert body["state"] == "refused" and body["exit_code"] == 1
    assert "won't load" in body["sentences"][0] and "moxi uninstall broken" in body["fix"]
    assert api.get("gamma")


def test_an_unknown_key_in_a_store_that_exists_is_refused_through_the_status_link(
        golden_copy):
    accepted(post("/api/remove", {"key": "nosuch"}, key=KEY))
    body = status_of()
    assert body["state"] == "refused" and "no project is named 'nosuch'" in body["reason"]


def test_no_store_file_is_answered_at_once_and_makes_no_file(store_path):
    said = refused(post("/api/remove", {"key": "nosuch"}, key=KEY))
    assert "no project is named 'nosuch'" in said
    assert not store_path.exists() and not store_path.parent.exists()
    assert "no remove request" in refused(get("/api/remove-status", f"job={KEY}"))


def test_a_newer_store_is_answered_at_once_and_left_as_it_was(golden_copy, store_path):
    _set_user_version(store_path, api._VERSION + 1)
    before = store_path.read_bytes()
    assert "newer than" in refused(post("/api/remove", {"key": "gamma"}, key=KEY))
    assert store_path.read_bytes() == before


def test_an_unreadable_store_is_answered_at_once_and_left_as_it_was(store_path):
    store_path.parent.mkdir(parents=True)
    store_path.write_bytes(b"this is not a database, not at all, " * 200)
    before = store_path.read_bytes()
    assert refused(post("/api/remove", {"key": "gamma"}, key=KEY))
    assert store_path.read_bytes() == before


def test_a_store_locked_past_the_wait_is_409_and_keeps_nothing(golden_copy, store_path,
                                                               monkeypatch):
    monkeypatch.setattr(store, "TIMEOUT", 0.05)
    holder = sqlite3.connect(store_path, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        removed = post("/api/remove", {"key": "gamma"}, key=KEY)
        # A refusal about the project whose own write is locked out is the lock's answer.
        held = post("/api/remove", {"key": "alpha"}, key=OTHER_KEY)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert removed[0] == 409 and held[0] == 409
    assert api.get("gamma") and _status_rows(store_path) == []


def test_a_repeat_while_the_first_still_runs_is_409(golden_copy, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real = acts.remove

    def slow(key, **named):
        entered.set()
        release.wait(30)
        return real(key, **named)

    monkeypatch.setattr(acts, "remove", slow)

    async def both():
        app = served()
        first = asyncio.create_task(_send(app, "POST", "/api/remove", body={"key": "gamma"},
                                          key=KEY))
        for _ in range(1000):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        try:
            second = await _send(app, "POST", "/api/remove", body={"key": "gamma"},
                                 key=KEY)
        finally:
            release.set()
        return await first, second

    first, second = asyncio.run(both())
    assert first[0] == 202 and second[0] == 409


def test_a_key_sent_with_another_project_is_422_within_a_server_and_after_a_restart(
        golden_copy):
    app = served()
    accepted(post("/api/remove", {"key": "gamma"}, key=KEY, app=app))
    assert post("/api/remove", {"key": "Beta"}, key=KEY, app=app)[0] == 422
    # A fresh app has no key table: the kept row answers.
    said = refused(post("/api/remove", {"key": "Beta"}, key=KEY))
    assert said == pagehandlers.KEY_REUSED
    assert api.get("Beta")


def test_a_repeat_after_a_restart_reads_the_kept_row_and_acts_no_more(golden_copy,
                                                                      tmp_path):
    accepted(post("/api/remove", {"key": "gamma"}, key=KEY))
    first = status_of()
    # gamma is back: a second run of the act would remove it again.
    acts.add("gamma", root=str(tmp_path / "gamma"))
    accepted(post("/api/remove", {"key": "GAMMA"}, key=KEY))
    assert api.get("gamma") and status_of() == first
    # A refused one, too.
    accepted(post("/api/remove", {"key": "alpha"}, key=OTHER_KEY), OTHER_KEY)
    refusal = status_of(OTHER_KEY)
    acts.remove_agent("alpha", "helper")
    acts.remove_agent("alpha", "scout")
    accepted(post("/api/remove", {"key": "alpha"}, key=OTHER_KEY), OTHER_KEY)
    assert api.get("alpha") and status_of(OTHER_KEY) == refusal


def test_a_repeat_past_a_day_is_a_new_act(golden_copy, monkeypatch):
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(time.time() - store.STATUS_KEPT - 60))
    with monkeypatch.context() as patched:
        patched.setattr(store, "now", lambda: old)
        accepted(post("/api/remove", {"key": "gamma"}, key=KEY))
    # The day's row is ignored, so the act runs again and finds no gamma.
    accepted(post("/api/remove", {"key": "gamma"}, key=KEY))
    body = status_of()
    assert body["state"] == "refused" and "no project is named 'gamma'" in body["reason"]


def test_a_remove_without_its_key_is_refused_before_the_handler(golden_copy):
    status, _ = post("/api/remove", {"key": "gamma"})
    assert status == 400
    assert api.get("gamma")


@pytest.mark.parametrize("outcome", [{"key": "gamma"}, {"state": "removed"}, {}])
def test_a_kept_outcome_without_its_state_or_key_is_unreadable(outcome, golden_copy,
                                                               store_path):
    accepted(post("/api/remove", {"key": "gamma"}, key=KEY))
    conn = sqlite3.connect(store_path)
    try:
        conn.execute("UPDATE remove_status SET outcome = ? WHERE key = ?",
                     (json.dumps(outcome), KEY))
        conn.commit()
    finally:
        conn.close()
    for answer in (get("/api/remove-status", f"job={KEY}"),
                   post("/api/remove", {"key": "gamma"}, key=KEY)):
        said = refused(answer)
        assert f"the projects store {store_path} can't be read" in said
        assert "fix: move the file aside" in said


def test_a_status_read_of_a_version_1_store_answers_no_such_request(v1_copy, store_path):
    before = store_path.read_bytes()
    assert "no remove request" in refused(get("/api/remove-status", f"job={KEY}"))
    assert store_path.read_bytes() == before


def test_the_store_the_page_acts_on_is_the_verbs(golden_copy):
    assert read("/api/where")["store"] == api._store_path()
    assert shutil.which("git") == read("/api/where")["git"]
