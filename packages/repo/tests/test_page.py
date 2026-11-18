"""repo's page server in process, through loopback's strict app.

Loopback is reached as the page reaches it, through `moxi.modules.load`: repo's
distribution doesn't require it, so its tests don't import it either. The app runs behind
`guard` with an upstream profile, as the runner serves it, with strict projection, so a
handler that sends a key its route doesn't declare, or a value of another type, fails here.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time

import pytest

import moxi_repo
from moxi import modules
from moxi_repo import _records, api, committer, gitconf, page, pagehandlers, projects, shim
from tests.conftest import NEEDS_CONFIG_GIT, POSIX_ONLY, git
from tests.test_commit_records import cgd, failed_push, push_key
from tests.test_committer import PREPARE, spec, write
from tests.test_projects import Projects

pytestmark = POSIX_ONLY

TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321


def loopback():
    found = modules.load("ui", interface=3)
    assert found.ok, found.reason
    return found.api.loopback


@pytest.fixture(autouse=True)
def fresh_log(monkeypatch):
    """Each test logs each failure class anew."""
    monkeypatch.setattr(pagehandlers, "_logged", set())


def served():
    lb = loopback()
    inner = lb.app(page.PAGE, module="repo", server_id=SERVER_ID, strict=True)
    profile = lb.upstream(lb.build_table(page.PAGE)[0], token=TOKEN, port=PORT)
    return lb.guard(inner, lambda: profile)


async def _get(app, path: str) -> tuple[int, dict | None]:
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "server": ("127.0.0.1", PORT),
        "client": ("127.0.0.1", 50000), "scheme": "http", "method": "GET",
        "root_path": "", "path": path, "raw_path": path.encode("ascii"), "query_string": b"",
        "headers": [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode())],
        "state": {},
    }
    sent = {"status": 0, "body": b""}
    asked = [False]

    async def receive():
        if not asked[0]:
            asked[0] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    await app(scope, receive, send)
    return sent["status"], json.loads(sent["body"]) if sent["body"] else None


def read() -> dict:
    before = time.time()
    status, body = asyncio.run(_get(served(), "/api/repos"))
    assert status == 200, body
    assert set(body) == {"now", "today", "state", "detail", "repos", "offered", "refused",
                         "unindexed"}
    assert before <= body["now"] <= time.time()
    assert body["today"] == dt.date.today().isoformat()
    return body


def offered_now() -> tuple[list[dict], list[dict]]:
    """`offered` and `refused` as the route gives them, from `api.available()`."""
    found = api.available()
    offered = [{"id": p["id"], "module": p["module"], "name": p["id"].split("/", 1)[1],
                "event": p["event"], "fail": p["fail"], "order": p["order"], "doc": p["doc"],
                "settings": list(p["settings"])} for p in found["pieces"]]
    return offered, [{"id": pid, "why": why} for pid, why in found["refused"].items()]


# --- the table ------------------------------------------------------------------------------

def test_the_gate_matrix_passes():
    assert loopback().gate_matrix(page.PAGE)


def test_no_declared_route_is_the_gateways():
    declared = [route.path for route in page.PAGE.routes]
    exported = loopback().export(page.PAGE)["routes"]
    assert declared and [route["path"] for route in exported] == declared
    assert [route.path for route in page.PAGE.routes if route.gateway] == []
    assert [route["path"] for route in exported if route["gateway"]] == []


def test_the_page_declares_one_read_and_its_built_assets():
    exported = loopback().export(page.PAGE)
    [route] = exported["routes"]
    assert (route["path"], route["kind"], route["stream"]) == ("/api/repos", "read", False)
    assert exported["assets"] == ["build-stamp.txt", "page.js"]


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


def test_every_value_that_can_hold_a_path_carries_a_mark():
    [route] = loopback().export(page.PAGE)["routes"]
    marks: dict[str, str] = {}
    for key, shape in route["returns"].items():
        marks.update(_marks(shape, key))
    said = ["detail", "repos[].specs_problem", "repos[].commands[].command",
            "repos[].events[].existing", "repos[].pieces[].settings[].values[]",
            "repos[].pieces[].toggle.command", "repos[].pieces[].remove.command",
            "repos[].problems[].detail", "repos[].problems[].fix",
            "repos[].specs[].problem", "repos[].specs[].line", "repos[].specs[].remote",
            "repos[].specs[].push_failed[].error", "repos[].specs[].run.command",
            "repos[].specs[].forget.command", "repos[].records[].text",
            "repos[].records[].fix", "repos[].rules[]", "offered[].doc", "refused[].why"]
    assert marks == {"repos[].root": "path", "repos[].events[].hook": "path",
                     "repos[].events[].existing_hook": "path", "unindexed[].root": "path",
                     **dict.fromkeys(said, "redact")}


def test_the_announcement_offers_the_page_and_nothing_imports_it():
    from moxi_repo import module

    assert module.PAGE == "moxi_repo.page:PAGE"
    assert module.COMPONENTS["page"]["place"] == "page"
    assert set(module.OPTIONAL) == {"ui", "projects"}


# --- /api/repos -----------------------------------------------------------------------------

def test_with_no_repo_the_read_is_up_and_empty(demo):
    offered, refused = offered_now()
    assert read() == {"now": pytest.approx(time.time(), abs=60),
                      "today": dt.date.today().isoformat(), "state": "up", "detail": None,
                      "repos": [], "offered": offered, "refused": refused, "unindexed": []}


def test_the_read_offers_each_declared_piece_and_names_each_refused_one(demo):
    body = read()
    offered = {p["id"]: p for p in body["offered"]}
    assert offered["demo/gate"] == {
        "id": "demo/gate", "module": "demo", "name": "gate", "event": "pre-commit",
        "fail": "closed", "order": 20, "doc": "A closed gate.",
        "settings": ["refused-name", "label"]}
    refused = {r["id"]: r["why"] for r in body["refused"]}
    assert "demo/camel" in refused and "demo/camel" not in offered
    assert (body["offered"], body["refused"]) == offered_now()


def test_offered_pieces_that_cant_be_read_leave_both_lists_empty_and_the_repos(
        demo, monkeypatch, caplog):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="shim")

    def broken():
        raise OSError("no metadata")

    monkeypatch.setattr(api, "available", broken)
    caplog.set_level(logging.ERROR, logger="moxi.repo.page")
    for _ in range(2):
        body = read()
        assert (body["state"], body["offered"], body["refused"]) == ("up", [], [])
        assert [r["root"] for r in body["repos"]] == [str(repo)]
    assert [r.message for r in caplog.records] == [
        "listing the offered pieces failed (OSError)"]


@NEEDS_CONFIG_GIT
def test_a_repo_reads_back_with_its_pieces_events_specs_records_and_commands(demo, clock):
    repo = demo.repo("alpha notes")
    api.add(repo, ["demo/gate", "demo/lint", "demo/watch"], sets=["label=first"])
    api.disable(repo, "demo/watch")
    failed_push(demo, repo, clock)
    moxi_repo.save_spec(spec(repo, push="origin"))
    body = read()
    assert (body["state"], body["detail"]) == ("up", None)
    [listed] = body["repos"]
    assert listed["root"] == str(repo) and listed["project"] is None
    assert listed["mode"] == "config" and listed["pinned"] is False
    assert listed["git_version"] == api.status(repo)[0]["git_version"]
    assert [(e["event"], e["via"], e["pieces"]) for e in listed["events"]] == [
        ("post-commit", None, ["demo/watch"]),
        ("pre-commit", "config entry", ["demo/gate", "demo/lint"])]
    pieces = {p["id"]: p for p in listed["pieces"]}
    assert [p["id"] for p in listed["pieces"]] == ["demo/watch", "demo/gate", "demo/lint"]
    gate = pieces["demo/gate"]
    assert (gate["event"], gate["fail"], gate["order"], gate["enabled"]) == (
        "pre-commit", "closed", 20, True)
    assert gate["settings"] == [{"key": "label", "values": ["first"]}]
    quoted = "'" + str(repo) + "'"
    assert gate["toggle"] == {"label": "Disable",
                              "command": f"moxi repo git-hooks disable {quoted} demo/gate"}
    assert gate["remove"]["command"] == f"moxi repo git-hooks remove {quoted} demo/gate"
    assert pieces["demo/watch"]["toggle"]["command"] == (
        f"moxi repo git-hooks enable {quoted} demo/watch")
    assert listed["problems"] == []
    [snap] = listed["specs"]
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert (snap["name"], snap["saved"], snap["status"], snap["remote"]) == (
        "demo/snap", True, "committed", "origin")
    assert snap["last_commit"] == head and snap["pushed"] == []
    [failed] = snap["push_failed"]
    assert failed["branch"] == "main" and "remote says no" in failed["error"]
    assert snap["unpushed"] == [{"branch": "main", "ahead": None}]
    assert (snap["error_since"], snap["error_passes"], snap["index_busy_since"],
            snap["index_stale"]) == (None, None, None, False)
    assert snap["run"]["command"] == f"moxi repo commit {quoted} --spec demo/snap"
    assert snap["forget"]["command"] == f"moxi repo specs remove {quoted} demo/snap"
    [record] = listed["records"]
    assert (record["kind"], record["spec"], record["branch"]) == ("push", "demo/snap", "main")
    assert "remote says no" in record["text"]
    assert [c["command"] for c in listed["commands"]] == [
        f"moxi repo git-hooks list {quoted}", f"moxi repo git-hooks refresh {quoted}",
        f"moxi repo specs {quoted}"]


def test_a_launcher_repo_shows_the_chain_line_needed_and_then_present(demo):
    repo = demo.repo()
    tracked = repo / ".githooks"
    tracked.mkdir()
    git(repo, "config", "core.hooksPath", ".githooks")
    report = api.add(repo, ["demo/gate"], mode="shim")
    [(_, _, hook, line, _)] = report.chain
    [listed] = read()["repos"]
    [event] = listed["events"]
    assert (event["via"], event["chain_line"], event["hook"], event["line"]) == (
        "launcher", "needed", hook, line)
    assert listed["pinned"] is True
    (tracked / "pre-commit").write_text(f"#!/bin/sh\n{line}\n")
    (tracked / "pre-commit").chmod(0o755)
    [event] = read()["repos"][0]["events"]
    assert (event["chain_line"], event["line"]) == ("present", None)
    assert event["existing"] == str(tracked / "pre-commit")


def test_a_problem_carries_its_state_detail_and_fix(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="shim")
    (repo / ".git" / "hooks" / "pre-commit").unlink()
    [problem] = read()["repos"][0]["problems"]
    assert problem == {"state": "fail",
                       "detail": "pre-commit: repo's shim is missing or another tool replaced it",
                       "fix": f"moxi repo git-hooks refresh {repo}", "code": "shim-missing",
                       "piece": None, "event": "pre-commit", "refresh": True}


def test_a_problem_names_its_piece_and_one_not_fixed_by_refresh_says_so(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="shim")
    for var, value in (("event", "pre-commit"), ("target", "gone.mod:fn"),
                       ("fail", "open"), ("order", "10"), ("enabled", "false")):
        git(repo, "config", gitconf.piece_key("demo/gone", var), value)
    [problem] = read()["repos"][0]["problems"]
    assert (problem["code"], problem["piece"], problem["event"], problem["refresh"]) == (
        "no-module", "demo/gone", "pre-commit", False)


def test_a_gone_repo_reads_as_indexed_with_no_rules(demo):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="shim")
    import shutil

    shutil.rmtree(repo)
    [listed] = read()["repos"]
    assert (listed["indexed"], listed["rules"], listed["proof"], listed["own_hooks"]) == (
        True, None, False, [])
    [problem] = listed["problems"]
    assert (problem["code"], problem["refresh"]) == ("gone", False)


def test_an_event_names_his_own_hook_and_the_repo_his_hooks_with_no_piece(demo):
    repo = demo.repo()
    hooks = repo / ".git" / "hooks"
    for name in ("pre-commit", "commit-msg"):
        (hooks / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (hooks / name).chmod(0o755)
    api.add(repo, ["demo/gate"], mode="shim")
    [listed] = read()["repos"]
    [event] = listed["events"]
    own = api.status(repo)[0]["events"]["pre-commit"]["existing_hook"]
    assert own is not None and event["existing_hook"] == own
    assert shim.shim_event(hooks / "pre-commit") == "pre-commit"
    assert listed["own_hooks"] == ["commit-msg"] and listed["indexed"] is True


def test_a_repos_rules_read_without_the_proof_line_and_nothing_is_written(demo):
    repo = demo.repo()
    api.add(repo, ["repo/commit-rules"], sets=["rule=one change per commit"], mode="shim")
    [listed] = read()["repos"]
    assert (listed["rules"], listed["proof"]) == (["- one change per commit"], False)
    api.add(repo, ["repo/commit-proof"], mode="shim")
    objects = sorted(p.name for p in (cgd(repo) / "objects").rglob("*"))
    [listed] = read()["repos"]
    assert (listed["rules"], listed["proof"]) == (["- one change per commit"], True)
    assert sorted(p.name for p in (cgd(repo) / "objects").rglob("*")) == objects


def test_the_read_writes_nothing(demo, clock):
    repo = demo.repo()
    api.add(repo, ["demo/gate"])
    failed_push(demo, repo, clock)
    # With its state file gone and the spec not saved, a sweep would clear the record.
    (cgd(repo) / "moxi-repo" / "state.json").unlink()
    ledger = _records.ledger_path().read_bytes()
    config = (cgd(repo) / "config").read_bytes()
    [listed] = read()["repos"]
    assert listed["specs"] == [] and [r["kind"] for r in listed["records"]] == ["push"]
    assert _records.ledger_path().read_bytes() == ledger
    assert (cgd(repo) / "config").read_bytes() == config
    assert push_key(repo) in _records.ledger()


def test_a_repo_whose_specs_cant_be_listed_keeps_its_git_hooks(demo, monkeypatch):
    repo = demo.repo()
    api.add(repo, ["demo/gate"])

    def broken(path=None, *, sweep=True):
        raise api.Failure("a spec section can't be read")

    monkeypatch.setattr(api, "specs", broken)
    [listed] = read()["repos"]
    assert listed["specs"] == [] and listed["specs_problem"] == "a spec section can't be read"
    assert [p["id"] for p in listed["pieces"]] == ["demo/gate"]


def test_an_unexpected_spec_listing_failure_stays_with_its_repo_and_is_logged_once(
        demo, monkeypatch, caplog):
    broken_repo, fine_repo = demo.repo("broken"), demo.repo("fine")
    api.add(broken_repo, ["demo/gate"])
    api.add(fine_repo, ["demo/gate"])
    moxi_repo.save_spec(spec(fine_repo))
    listing = api.specs

    def specs(path=None, *, sweep=True):
        if path == str(broken_repo):
            raise PermissionError(13, "Permission denied", str(broken_repo / "state.json"))
        return listing(path, sweep=sweep)

    monkeypatch.setattr(api, "specs", specs)
    caplog.set_level(logging.ERROR, logger="moxi.repo.page")
    for _ in range(2):
        body = read()
        assert (body["state"], body["detail"]) == ("up", None)
        listed = {r["root"]: r for r in body["repos"]}
        assert set(listed) == {str(broken_repo), str(fine_repo)}
        broken, fine = listed[str(broken_repo)], listed[str(fine_repo)]
        assert broken["specs"] == [] and broken["specs_problem"] == (
            "an unexpected error (PermissionError); the page server's log has the details")
        assert [p["id"] for p in broken["pieces"]] == ["demo/gate"]
        assert fine["specs_problem"] is None and [s["name"] for s in fine["specs"]] == [
            spec(fine_repo).name]
    assert [r.message for r in caplog.records] == [
        "listing a repo's specs failed (PermissionError)"]


def test_an_unreadable_index_is_state_error_with_repos_own_words(demo):
    api.index_path().parent.mkdir(parents=True, exist_ok=True)
    api.index_path().write_text("[1, 2]", encoding="utf-8")
    body = read()
    assert (body["state"], body["repos"]) == ("error", [])
    with pytest.raises(api.IndexUnreadable) as caught:
        api.status()
    assert body["detail"] == str(caught.value)


def test_an_unexpected_failure_is_state_error_with_a_fixed_detail_logged_once(
        demo, monkeypatch, caplog):
    monkeypatch.setattr(api, "status", lambda path=None, *, with_rules=False: 1 / 0)
    caplog.set_level(logging.ERROR, logger="moxi.repo.page")
    for _ in range(2):
        body = read()
        assert (body["state"], body["repos"]) == ("error", [])
        assert body["detail"] == ("repo's page server hit an unexpected error "
                                  "(ZeroDivisionError); its log has the details")
    assert [r.message for r in caplog.records] == ["listing the repos failed (ZeroDivisionError)"]


@pytest.fixture
def clock(monkeypatch):
    from moxi_repo import committer

    now = [time.time()]
    monkeypatch.setattr(committer, "_now", lambda: now[0])
    return now


def test_a_spec_that_has_never_run_but_is_saved_shows_no_pass(demo):
    repo = demo.repo()
    api.add(repo)
    moxi_repo.save_spec(spec(repo))
    write(repo, "notes/a.md")
    [listed] = read()["repos"]
    [snap] = listed["specs"]
    assert (snap["status"], snap["time"], snap["last_commit"], snap["remote"]) == (
        None, None, None, None)


def test_the_page_states_its_group_and_order_among_the_pages():
    from moxi_repo import module

    page_decl = module.COMPONENTS["page"]
    assert (page_decl["group"], page_decl["order"]) == ("setup", 20)


def test_a_spec_carries_its_error_run_and_its_index_busy_run(demo, monkeypatch, clock):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="shim")
    write(repo, "notes/a.md")
    start = clock[0]
    monkeypatch.setenv("DEMO_PREPARE", "raise")
    assert moxi_repo.commit(spec(repo, prepare=PREPARE)).status == "error"
    [snap] = read()["repos"][0]["specs"]
    assert (snap["error_since"], snap["error_passes"], snap["refused_since"]) == (
        start, 1, None)
    assert snap["unpushed"] == []
    monkeypatch.delenv("DEMO_PREPARE")
    lock = cgd(repo) / "index.lock"
    lock.write_text("", encoding="utf-8")
    try:
        for n in range(1, committer.STALE_INDEX_PASSES + 1):
            clock[0] += 60
            assert moxi_repo.commit(spec(repo)).status == "index-busy"
            [snap] = read()["repos"][0]["specs"]
            assert snap["index_busy_since"] == start + 60
            assert snap["index_stale"] is (n == committer.STALE_INDEX_PASSES)
    finally:
        lock.unlink()


# --- projects -------------------------------------------------------------------------------

@pytest.fixture
def with_projects(capsys, monkeypatch):
    import moxi.modules

    yield Projects(capsys, monkeypatch)
    moxi.modules.reset()


def test_without_projects_unindexed_is_null_and_no_repo_has_a_key(demo, monkeypatch):
    repo = demo.repo()
    api.add(repo, ["demo/gate"], mode="shim")
    monkeypatch.setattr(projects, "api", lambda: None)
    body = read()
    assert body["unindexed"] is None
    assert [(r["root"], r["project"]) for r in body["repos"]] == [(str(repo), None)]


def test_a_project_repo_with_no_piece_is_unindexed_and_one_with_pieces_a_row(
        demo, with_projects):
    keyed, bare, held = demo.repo("keyed"), demo.repo("bare-proj"), demo.repo("held")
    plain = demo.tmp / "not-a-repo"
    plain.mkdir()
    (bare / "sub").mkdir()
    api.add(keyed, ["demo/gate"], mode="shim")
    api.add(held, ["demo/gate"], mode="shim")
    # Its git config keeps the piece, but the index forgot it.
    [key] = api.indexed_at(held)
    api._index_drop(key)
    for key, root in (("keyed", keyed), ("bare", bare / "sub"), ("held", held),
                      ("plain", plain), ("gone", demo.tmp / "nowhere")):
        with_projects.add(key, root)
    body = read()
    assert body["state"] == "up"
    assert body["unindexed"] == [{"project": "bare", "root": str(bare)}]
    rows = {r["root"]: r for r in body["repos"]}
    assert set(rows) == {str(keyed), str(held)}
    assert (rows[str(keyed)]["project"], rows[str(keyed)]["indexed"]) == ("keyed", True)
    assert (rows[str(held)]["project"], rows[str(held)]["indexed"]) == ("held", False)
    assert [p["id"] for p in rows[str(held)]["pieces"]] == ["demo/gate"]
    assert rows[str(held)]["rules"] == []


def test_project_repos_that_cant_be_listed_leave_the_indexed_repos_keyed(
        demo, with_projects, monkeypatch, caplog):
    repo, other = demo.repo("keyed"), demo.repo("other")
    api.add(repo, ["demo/gate"], mode="shim")
    with_projects.add("keyed", repo)
    with_projects.add("other", other)
    status = api.status

    def broken(path=None, *, with_rules=False):
        if path is not None:
            raise PermissionError(13, "Permission denied")
        return status(path, with_rules=with_rules)

    monkeypatch.setattr(api, "status", broken)
    caplog.set_level(logging.ERROR, logger="moxi.repo.page")
    body = read()
    assert (body["state"], body["unindexed"]) == ("up", [])
    assert [(r["root"], r["project"]) for r in body["repos"]] == [(str(repo), "keyed")]
    assert [r.message for r in caplog.records] == [
        "listing the project repos failed (PermissionError)"]


def test_the_cli_and_the_page_list_the_same_project_repos(demo, with_projects, capsys):
    from moxi_repo import cli

    keyed, bare = demo.repo("keyed"), demo.repo("bare-proj")
    api.add(keyed, ["demo/gate"], mode="shim")
    with_projects.add("keyed", keyed)
    with_projects.add("bare", bare)
    assert cli.main(["git-hooks", "list", "--projects", "--json"]) == 0
    printed = {r["root"]: r["project"] for r in json.loads(capsys.readouterr().out)}
    body = read()
    assert printed == {**{r["root"]: r["project"] for r in body["repos"]},
                       **{u["root"]: u["project"] for u in body["unindexed"]}}
