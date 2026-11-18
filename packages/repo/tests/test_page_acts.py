"""repo's page server's plan and its three acts, in process through loopback's strict app.

Each test runs as a copy of Moxi would: `MOXI_ANCHOR_HOME` names a folder under the
test's tmp (`copy`), the repos it acts on are made inside it, and the core's record of a
terminal whose environment is this process's is written there, so ui's check passes. A
repo made outside that folder is each act's refusal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
from pathlib import Path

import pytest

from moxi_repo import api, gitconf, page, pagehandlers
from tests.conftest import POSIX_ONLY, git
from tests.test_page import PORT, TOKEN, loopback, served
from tests.test_plan_sync import held  # noqa: F401 -- a fixture

pytestmark = POSIX_ONLY

ACTS = ("add", "enable", "refresh")


@pytest.fixture(autouse=True)
def fresh_log(monkeypatch):
    monkeypatch.setattr(pagehandlers, "_logged", set())


@pytest.fixture
def copy(demo, terminal_record) -> Path:
    """The copy's folder, named by `MOXI_ANCHOR_HOME`, with a terminal's record in it."""
    copy_dir = demo.tmp / "copy"
    copy_dir.mkdir()
    demo.mp.setenv("MOXI_ANCHOR_HOME", str(copy_dir))
    terminal_record()
    return copy_dir


async def _send(app, method: str, path: str, *, query: str = "",
                body: dict | None = None) -> tuple[int, dict | None]:
    headers = [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode())]
    raw = b""
    if method == "POST":
        raw = json.dumps(body or {}).encode()
        headers += [(b"content-type", b"application/json"),
                    (b"content-length", str(len(raw)).encode())]
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


def planned(act: str, root, pieces: list[str] = (), *, status: int = 200) -> dict:
    query = {"act": act, "root": str(root)}
    if pieces:
        query["pieces"] = ",".join(pieces)
    got, body = asyncio.run(_send(served(), "GET", "/api/plan",
                                  query=urllib.parse.urlencode(query)))
    assert got == status, body
    return body


def plan_of(act: str, root, pieces: list[str] = ()) -> dict:
    body = planned(act, root, pieces)
    assert body["state"] == "up" and body["detail"] is None, body
    return body["plan"]


def sent(act: str, root, digest: str | None, pieces: list[str] = ()) -> dict:
    body = {"root": str(root), "digest": digest or "0" * 64}
    if act == "add":
        body["pieces"] = list(pieces)
    elif act == "enable":
        body["piece"] = pieces[0]
    got, answer = asyncio.run(_send(served(), "POST", f"/api/{act}", body=body))
    assert got == 200, answer
    assert set(answer) == {"outcome", "lines", "problems", "chain", "plan", "refusal"}
    return answer


#: Each act's pieces in a repo `ready` made.
PIECES = {"add": ["demo/lint"], "enable": ["demo/watch"], "refresh": []}


def ready(repo: Path) -> None:
    """Pieces registered as the terminal registers them, one off and one hand-edited, so
    each act has something to write; then no lock file."""
    api.add(repo, ["demo/gate", "demo/watch"], mode="config")
    api.disable(repo, "demo/watch")
    git(repo, "config", "moxi-repo.piece.demo/gate.fail", "open")
    lock_of(repo).unlink()


def lock_of(repo: Path) -> Path:
    return gitconf.resolve(repo).own_dir / api.HOOKS_LOCK


def written(repo: Path) -> tuple:
    where = gitconf.resolve(repo)
    index = api.index_path()
    return ((where.common_dir / "config").read_bytes(),
            {str(d): sorted(p.name for p in d.iterdir())
             for d in {where.hooks_dir, where.common_dir / "hooks"} if d.is_dir()},
            index.read_bytes() if index.exists() else None,
            lock_of(repo).exists())


# --- the table ------------------------------------------------------------------------------

def test_the_plan_reads_and_the_three_acts_are_plain_acts():
    routes = {r["path"]: r for r in loopback().export(page.PAGE)["routes"]}
    assert {path: (r["kind"], r["gateway"], r["stream"]) for path, r in routes.items()} == {
        "/api/repos": ("read", False, False), "/api/plan": ("read", False, False),
        "/api/add": ("act", False, False), "/api/enable": ("act", False, False),
        "/api/refresh": ("act", False, False)}


def test_every_path_in_a_plan_or_an_answer_carries_a_mark():
    from tests.test_page import _marks

    routes = {r["path"]: r for r in loopback().export(page.PAGE)["routes"]}
    plan = {"plan.root": "path", "plan.name": "path", "plan.writes[].from": "redact",
            "plan.writes[].to": "redact", "plan.refusal.text": "redact"}
    assert {k: v for key, shape in routes["/api/plan"]["returns"].items()
            for k, v in _marks(shape, key).items()} == {"detail": "redact", **plan}
    for act in ACTS:
        marks = {k: v for key, shape in routes[f"/api/{act}"]["returns"].items()
                 for k, v in _marks(shape, key).items()}
        assert marks == {"lines[]": "redact", "problems[]": "redact",
                         "chain[].hook": "path", "refusal.text": "redact", **plan}


# --- /api/plan ------------------------------------------------------------------------------

def test_each_plan_is_the_verbs_plan_kept_to_the_copy(demo, copy):
    repo = demo.repo("copy/alpha-notes")
    ready(repo)
    assert plan_of("add", repo, ["demo/lint"]) == api.plan_add(repo, ["demo/lint"],
                                                                within=str(copy))
    assert plan_of("enable", repo, ["demo/watch"]) == api.plan_enable(
        repo, "demo/watch", within=str(copy))
    assert plan_of("refresh", repo) == api.plan_refresh(repo, within=str(copy))
    assert plan_of("add", repo)["writes"] == []


def test_a_tilde_root_is_the_home_this_server_places(demo, copy):
    repo = demo.repo("copy/alpha-notes")
    demo.mp.setenv("HOME", str(copy))
    plan = plan_of("add", "~/alpha-notes", ["demo/gate"])
    assert plan["root"] == str(repo) and plan["refusal"] is None


def test_turning_on_names_one_piece(demo, copy):
    repo = demo.repo("copy/alpha-notes")
    assert "refused" in planned("enable", repo, status=422)
    assert "refused" in planned("enable", repo, ["demo/gate", "demo/watch"], status=422)


def test_a_plan_that_fails_unexpectedly_is_an_error_logged_once(demo, copy, monkeypatch,
                                                                 caplog):
    repo = demo.repo("copy/alpha-notes")

    def broken(*args, **kwargs):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(api, "plan_refresh", broken)
    with caplog.at_level(logging.ERROR, logger="moxi.repo.page"):
        for _ in range(2):
            body = planned("refresh", repo)
            assert body == {"state": "error", "plan": None,
                            "detail": f"{pagehandlers.FAILED} (RuntimeError)"}
    assert len([r for r in caplog.records if "planning an act" in r.message]) == 1


# --- the acts ---------------------------------------------------------------------------------

@pytest.mark.parametrize("act", ACTS)
def test_an_act_writes_its_plan_and_a_repeated_send_writes_nothing(demo, copy, act):
    repo = demo.repo("copy/alpha-notes")
    ready(repo)
    plan = plan_of(act, repo, PIECES[act])
    answer = sent(act, repo, plan["digest"], PIECES[act])
    assert answer["outcome"] == "done" and answer["refusal"] is None, answer
    assert answer["lines"] and answer["plan"] is None
    assert plan_of(act, repo, PIECES[act])["writes"] == []
    before = written(repo)
    again = sent(act, repo, plan["digest"], PIECES[act])
    assert again["outcome"] == "nothing" and again["lines"] == []
    assert written(repo) == before


def test_an_add_writes_the_pieces_into_git_config(demo, copy):
    repo = demo.repo("copy/alpha-notes")
    api.add(repo)
    plan = plan_of("add", repo, ["demo/gate"])
    assert sent("add", repo, plan["digest"], ["demo/gate"])["outcome"] == "done"
    assert git(repo, "config", "moxi-repo.piece.demo/gate.event").stdout.strip() == \
        "pre-commit"


def test_an_add_in_launcher_mode_answers_the_chain_line_he_adds(demo, copy):
    repo = demo.repo("copy/alpha-notes")
    (repo / ".githooks").mkdir()
    git(repo, "config", "core.hooksPath", ".githooks")
    hook = repo / ".githooks" / "pre-commit"
    api.add(repo, mode="shim")
    plan = plan_of("add", repo, ["demo/gate"])
    answer = sent("add", repo, plan["digest"], ["demo/gate"])
    assert answer["outcome"] == "done"
    [chain] = answer["chain"]
    assert (chain["event"], chain["hook"], chain["present"]) == (
        "pre-commit", str(hook), False)
    assert chain["line"] in [i["to"] for i in plan["writes"] if i["place"] == "chain"]


def test_a_cli_add_between_plan_and_act_is_changed_and_writes_nothing(demo, copy):
    repo = demo.repo("copy/alpha-notes")
    api.add(repo)
    plan = plan_of("add", repo, ["demo/gate"])
    api.add(repo, ["demo/lint"])
    before = written(repo)
    answer = sent("add", repo, plan["digest"], ["demo/gate"])
    assert answer["outcome"] == "changed" and answer["refusal"] is None
    assert answer["plan"] == plan_of("add", repo, ["demo/gate"])
    assert written(repo) == before


def test_a_refused_act_names_its_code_and_a_held_repo_is_busy(demo, copy, held):
    repo = demo.repo("copy/alpha-notes")
    ready(repo)
    answer = sent("enable", repo, None, ["demo/nothing"])
    assert answer["outcome"] == "refused"
    assert answer["refusal"] == {"code": "not-registered",
                                 "text": f"demo/nothing is not registered in {repo}"}
    answer = sent("add", "relative/alpha-notes", None, ["demo/gate"])
    assert answer["refusal"]["code"] == "not-absolute"
    held(repo)
    plan = plan_of("enable", repo, ["demo/watch"])
    answer = sent("enable", repo, plan["digest"], ["demo/watch"])
    assert (answer["outcome"], answer["refusal"]["code"]) == ("refused", "busy")


def test_a_git_failure_after_the_first_write_is_partial(demo, copy):
    repo = demo.repo("copy/alpha-notes")
    api.add(repo)
    put = gitconf._put_values
    calls: list[str] = []

    def failing(where, key, values):
        calls.append(key)
        if len(calls) == 2:
            raise gitconf.GitError(f"git config could not set {key}: no room")
        put(where, key, values)

    plan = plan_of("add", repo, ["demo/gate"])
    demo.mp.setattr(gitconf, "_put_values", failing)
    answer = sent("add", repo, plan["digest"], ["demo/gate"])
    assert answer["outcome"] == "partial" and "no room" in answer["refusal"]["text"]


def test_an_act_that_fails_unexpectedly_says_only_that_and_logs_once(demo, copy,
                                                                     monkeypatch, caplog):
    repo = demo.repo("copy/alpha-notes")

    def broken(*args, **kwargs):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(api, "refresh", broken)
    with caplog.at_level(logging.ERROR, logger="moxi.repo.page"):
        for _ in range(2):
            answer = sent("refresh", repo, None)
            assert (answer["outcome"], answer["refusal"]) == (
                "failed", {"code": None, "text": pagehandlers.FAILED})
    assert len([r for r in caplog.records if "an act failed" in r.message]) == 1


# --- a copy of Moxi -----------------------------------------------------------------------------

def outside_cases(demo, copy) -> dict[str, tuple[Path, str]]:
    """Each repo the copy's acts refuse: the path sent, and what the words name."""
    plain = demo.repo("alpha-notes")
    ready(plain)
    sibling = demo.repo("copy-2")
    ready(sibling)
    main = demo.repo("beta-notes")
    ready(main)
    linked = copy / "wt"
    git(main, "worktree", "add", "-q", "-b", "side", str(linked))
    hooked = demo.repo("copy/gamma-notes")
    hooks = demo.tmp / "elsewhere-hooks"
    hooks.mkdir()
    git(hooked, "config", "core.hooksPath", str(hooks))
    ready(hooked)
    return {"outside": (plain, str(plain)), "sibling": (sibling, str(sibling)),
            "worktree": (linked, str(main)),
            "hooks": (hooked, f"the hooks folder of {hooked}, {hooks},")}


def test_a_repo_outside_the_copy_is_refused_by_every_plan_and_act_before_uis_check(
        demo, copy):
    # No terminal's record either: ui's check would refuse, but the copy's comes first.
    for record in (copy / ".local").rglob("terminal.json"):
        record.unlink()
    for case, (path, named) in outside_cases(demo, copy).items():
        text = (f"This page runs in a copy of Moxi whose home is {copy}. It changes no "
                f"repo outside that folder, and {named} is outside it.")
        before = written(path)
        for act in ACTS:
            plan = plan_of(act, path, PIECES[act])
            assert plan["refusal"] == {"code": "outside-copy", "text": text}, case
            answer = sent(act, path, None, PIECES[act])
            assert answer["outcome"] == "refused", (case, act)
            assert answer["refusal"] == {"code": "outside-copy", "text": text}
            assert written(path) == before, (case, act)


def test_a_copy_whose_home_isnt_a_full_path_refuses_every_plan_and_act(demo, copy):
    repo = demo.repo("copy/alpha-notes")
    ready(repo)
    demo.mp.chdir(demo.tmp)
    demo.mp.setenv("MOXI_ANCHOR_HOME", "copy")
    before = written(repo)
    for act in ACTS:
        assert plan_of(act, repo, PIECES[act])["refusal"]["code"] == "outside-copy"
        assert sent(act, repo, None, PIECES[act])["refusal"]["code"] == "outside-copy"
    assert written(repo) == before


def test_without_a_copy_a_repo_anywhere_is_planned_and_written(demo, monkeypatch):
    # ui's check would read the account's own record without the variable; the check is
    # stood in for here, so the test reads nothing outside its tmp.
    monkeypatch.delenv("MOXI_ANCHOR_HOME")
    monkeypatch.setattr(pagehandlers, "_terminal_line", lambda: None)
    repo = demo.repo("alpha-notes")
    ready(repo)
    plan = plan_of("add", repo, ["demo/lint"])
    assert plan["refusal"] is None
    assert sent("add", repo, plan["digest"], ["demo/lint"])["outcome"] == "done"


# --- ui's check ------------------------------------------------------------------------------

def test_with_no_terminal_record_an_act_is_refused_with_uis_line_and_no_anchor_clause(
        demo, copy):
    repo = demo.repo("copy/alpha-notes")
    ready(repo)
    for record in (copy / ".local").rglob("terminal.json"):
        record.unlink()
    before = written(repo)
    for act in ACTS:
        plan = plan_of(act, repo, PIECES[act])
        assert plan["refusal"] is None
        answer = sent(act, repo, plan["digest"], PIECES[act])
        assert answer["outcome"] == "refused" and answer["refusal"]["code"] is None
        text = answer["refusal"]["text"]
        assert text.startswith("Moxi has no record of the environment your terminal uses")
        assert "MOXI_ANCHOR_HOME" not in text, text
    assert written(repo) == before


def test_an_index_placed_elsewhere_than_the_terminals_is_refused(demo, copy, terminal_record):
    repo = demo.repo("copy/alpha-notes")
    ready(repo)
    env = dict(os.environ, MOXI_REPO_STATE_HOME=str(demo.tmp / "elsewhere"))
    terminal_record(env)
    before = written(repo)
    plan = plan_of("refresh", repo)
    answer = sent("refresh", repo, plan["digest"])
    assert answer["outcome"] == "refused"
    assert answer["refusal"]["text"].startswith("repo's index folder here is ")
    assert written(repo) == before
