"""loadout's page server in process, through loopback's strict app.

Loopback is reached as the page reaches it, through `moxi.modules.load`: loadout's
distribution doesn't require it, so these tests don't import it either. The app runs
behind `guard` with an upstream profile, as the runner serves it, with strict projection,
so a handler that sends a key its route doesn't declare, or a value of another type, fails
here. No act starts a process: the job's spawn is stubbed, and a test that needs the job's
end runs the runner in this process.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import loadout_kit as kit
import pytest

from moxi import modules
from moxi_loadout import cli, homes, install, ledger, page, pagehandlers, pagejob

TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321
KEY = "K" * 43
OTHER_KEY = "L" * 43


def loopback():
    found = modules.load("loopback", interface=1)
    assert found.ok, found.reason
    return found.api


LB = loopback()


@pytest.fixture(autouse=True)
def fresh_log(monkeypatch):
    """Each test logs each failure class anew."""
    monkeypatch.setattr(pagehandlers, "_logged", set())


def served():
    inner = LB.app(page.PAGE, module="loadout", server_id=SERVER_ID, strict=True)
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


def printed(argv: list[str]):
    """What `moxi loadout <argv>` prints, read as JSON."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(argv)
    return json.loads(out.getvalue())


@pytest.fixture
def lo(folder):
    """A configured loadout folder with one skill, listed for the user."""
    kit.configure(folder)
    kit.skill(folder / "skills", "review")
    kit.manifest(folder, '[user]\nskills = ["review"]\n')
    return folder


@pytest.fixture
def spawned(monkeypatch):
    """The job's spawn, stubbed: each call's command line, answered with this process's
    pid, so a job reads as running until its end is written."""
    calls: list[list[str]] = []

    def spawn(argv, *, cwd, output):
        calls.append(argv)
        return os.getpid()

    monkeypatch.setattr(pagejob, "_spawn", spawn)
    return calls


def job_of(answer: dict) -> str:
    path, _, job = answer["status"].partition("?job=")
    assert path == "/api/job"
    return job


def verb_of(command: list[str]) -> list[str]:
    """The verb a runner's command line runs."""
    at = command.index("moxi_loadout.pagejob")
    return command[at + 2:]


# -- the table ------------------------------------------------------------------------------

def test_the_gate_matrix_passes():
    assert LB.gate_matrix(page.PAGE)


def test_the_page_declares_its_reads_and_acts_none_the_gateways():
    exported = LB.export(page.PAGE)
    routes = {r["path"]: r for r in exported["routes"]}
    assert {p for p, r in routes.items() if r["kind"] == "read"} == {
        "/api/status", "/api/list", "/api/global", "/api/projects", "/api/drift", "/api/job"}
    assert {p for p, r in routes.items() if r["kind"] == "act"} == {
        "/api/add", "/api/remove", "/api/enable", "/api/disable", "/api/global-mode",
        "/api/install", "/api/replace", "/api/global-off"}
    assert {p for p, r in routes.items() if r.get("confirmed")} == {
        "/api/replace", "/api/global-off"}
    assert all(routes[p]["status"] == "/api/job" for p in ("/api/replace", "/api/global-off"))
    assert not any(r["gateway"] or r["stream"] for r in routes.values())


def test_the_export_carries_the_measured_budget():
    exported = LB.export(page.PAGE)
    assert exported["budget"] == {"rss": "80M", "fds": 40} == page.BUDGET


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
    marks: dict[str, str] = {}
    for route in LB.export(page.PAGE)["routes"]:
        for key, shape in route["returns"].items():
            marks.update({f"{route['path']} {k}": v for k, v in _marks(shape, key).items()})
    paths = {k for k, v in marks.items() if v == "path"}
    assert paths == {
        "/api/status rows[].target", "/api/job outcome.rows[].target",
        "/api/list manifest", "/api/list sections[].root", "/api/global files[].file",
        "/api/projects projects[].root"}
    for said in ("/api/status problem", "/api/status rows[].reason", "/api/status rows[].name",
                 "/api/status rows[].scope", "/api/status lines[]", "/api/status notes[]",
                 "/api/list items[].source", "/api/list items[].scope",
                 "/api/list sections[].name", "/api/global files[].diff",
                 "/api/projects reason", "/api/drift note", "/api/job lines[]",
                 "/api/job argv[]", "/api/job outcome.lines[]"):
        assert marks[said] == "redact", said


def test_the_page_names_kinds_modes_and_states_as_loadout_does():
    from moxi_loadout import manifest, planner

    assert page.KINDS == manifest.KINDS
    assert page.MODES == manifest.MODES
    assert page.STATES == planner.STATES


def test_the_announcement_offers_the_page_and_imports_none_of_it():
    from moxi_loadout import module

    assert module.PAGE == "moxi_loadout.page:PAGE"
    assert "ui" in module.OPTIONAL
    probe = ("import sys, moxi_loadout.module, moxi_loadout.cli, moxi_loadout.listener, "
             "moxi_loadout.gitpiece\n"
             "print(sorted(m for m in sys.modules if m.startswith(('moxi_loopback', "
             "'moxi_loadout.page'))))")
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          timeout=60, check=True)
    assert done.stdout.strip() == "[]"


# -- reads ----------------------------------------------------------------------------------

def test_status_answers_what_status_json_prints(lo):
    body = read("/api/status")
    assert body.pop("problem") is None
    # The homes note is worded for the page; everything else is what a terminal prints.
    with homes.as_page():
        assert body == printed(["status", "--json"])
    terminal = printed(["status", "--json"])
    assert {k: v for k, v in body.items() if k != "notes"} == {
        k: v for k, v in terminal.items() if k != "notes"}
    assert len(body["notes"]) == len(terminal["notes"])
    assert {(r["name"], r["harness"]) for r in body["rows"]} >= {("review", "claude")}


def test_list_answers_the_items_list_json_prints_and_the_sections(lo, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    kit.manifest(lo, f'[user]\nskills = ["review"]\n[project."{proj}"]\nagents = []\n')
    body = read("/api/list")
    assert body["problem"] is None
    assert body["items"] == printed(["list", "--json"])
    assert body["manifest"] == str(lo / "loadout.toml")
    assert body["sections"] == [{"name": "user", "where": "user", "root": None},
                                {"name": str(proj), "where": "path", "root": str(proj)}]


def test_global_answers_what_global_show_json_prints_and_whether_the_mode_is_chosen(lo):
    (lo / "global.md").write_text("Be kind.\n")
    body = read("/api/global")
    shown = printed(["global", "show", "--json"])
    assert (body["problem"], body["mode"], body["chosen"]) == (None, shown["mode"], False)
    assert len(body["files"]) == len(shown["files"])
    kit.manifest(lo, '[global]\nmode = "managed"\n[user]\nskills = ["review"]\n')
    body = read("/api/global")
    shown = printed(["global", "show", "--json"])
    assert (body["mode"], body["chosen"]) == ("managed", True)
    assert [{k: f[k] for k in ("file", "changes", "diff")} for f in body["files"]] == [
        {k: f[k] for k in ("file", "changes", "diff")} for f in shown["files"]]
    assert any(f["changes"] for f in body["files"])


def test_drift_counts_the_items_not_ok_and_notes_other_homes_without_counting_them(
        lo, monkeypatch, tmp_path):
    # review is missing for claude and for codex: one item, however many harnesses.
    assert read("/api/status")["counts"] == {"missing": 2}
    assert read("/api/drift")["count"] == 1
    assert install.install().code == 0
    assert read("/api/drift") == {"problem": None, "count": 0, "note": None}
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "elsewhere"))
    body = read("/api/drift")
    assert "Codex" in body["note"] and "CODEX_HOME" in body["note"]
    assert body["count"] == len({(r["scope"], r["kind"], r["name"])
                                 for r in read("/api/status")["rows"]
                                 if r["state"] not in ("ok", "off")})
    # The page's reads word the note for the page, as a job's refusal is worded.
    assert body["note"].endswith("then run moxi ui stop, or run the command in a terminal")
    assert body["note"] in read("/api/status")["notes"]


def test_a_manifest_that_does_not_read_is_each_reads_problem(lo):
    kit.manifest(lo, "[user]\nnonsense = 1\n")
    for path in ("/api/status", "/api/list", "/api/global", "/api/drift"):
        body = read(path)
        assert "loadout.toml" in body["problem"] and "nonsense" in body["problem"], path


def test_a_failure_of_another_kind_names_only_its_class(lo, monkeypatch, caplog):
    def broken(*a, **kw):
        raise RuntimeError("a secret detail")

    monkeypatch.setattr(install, "status", broken)
    body = read("/api/status")
    assert "RuntimeError" in body["problem"] and "secret" not in body["problem"]
    assert "a secret detail" in caplog.text


def test_status_gives_git_the_doctors_time(lo, monkeypatch):
    from moxi_loadout import doctor

    seen = {}
    real = install.status

    def spy(*a, git=None, git_seconds=None, **kw):
        seen.update(git=git, git_seconds=git_seconds)
        return real(*a, git=git, git_seconds=git_seconds, **kw)

    monkeypatch.setattr(install, "status", spy)
    read("/api/status")
    assert seen["git"].timeout == doctor.GIT_SECONDS <= 2
    assert seen["git"].budget == doctor.GIT_BUDGET
    assert seen["git_seconds"] == doctor.PLACE_GIT_SECONDS <= 2


@pytest.mark.timed
@pytest.mark.skipif(os.name != "posix", reason="the stalled git is a shell script")
def test_a_read_returns_while_git_stalls(lo, monkeypatch, tmp_path):
    stalled = tmp_path / "stalled-bin"
    stalled.mkdir()
    (stalled / "git").write_text("#!/bin/sh\nexec sleep 60\n")
    (stalled / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{stalled}{os.pathsep}{os.environ['PATH']}")
    start = time.monotonic()
    body = read("/api/status")
    assert time.monotonic() - start < 15
    assert body["problem"] is None


# -- projects -------------------------------------------------------------------------------

PROJECTS_API = '''
import os


class ProjectsError(Exception):
    pass


class NotFound(ProjectsError):
    pass


class Unreadable(ProjectsError):
    pass


class TooNew(Unreadable):
    pass


class Busy(Unreadable):
    pass


class Project:
    def __init__(self, key, root):
        self.key, self.root = key, root


def list_projects(kind=None):
    error = os.environ.get("FAKE_PROJECTS_ERROR")
    if error == "busy":
        raise Busy("the projects store stayed locked")
    if error == "unreadable":
        raise Unreadable("the projects store can't be read; move it aside")
    return [Project("alpha", "/srv/alpha"), Project("beta", "/srv/beta")]
'''


def test_projects_absent_is_the_pickers_reason(site):
    from moxi_loadout import projects_link

    assert read("/api/projects") == {"projects": [], "reason": projects_link.ABSENT}


@pytest.mark.parametrize("error, reason", [
    (None, None),
    ("busy", "projects is busy; try again"),
    ("unreadable", "projects can't be read: the projects store can't be read; move it aside"),
])
def test_projects_lists_by_key_or_says_why_not(site, monkeypatch, error, reason):
    site.module("projects", API="'moxi_projects.api'", files={"api.py": PROJECTS_API})
    if error:
        monkeypatch.setenv("FAKE_PROJECTS_ERROR", error)
    body = read("/api/projects")
    assert body["reason"] == reason
    assert body["projects"] == ([] if error else [{"key": "alpha", "root": "/srv/alpha"},
                                                  {"key": "beta", "root": "/srv/beta"}])


# -- acts -----------------------------------------------------------------------------------

@pytest.mark.parametrize("path, body, verb", [
    ("/api/add", {"kind": "skill", "name": "lint"}, ["add", "skill", "lint"]),
    ("/api/add", {"kind": "agent", "name": "helper", "project": "/srv/alpha"},
     ["add", "agent", "helper", "--project=/srv/alpha"]),
    ("/api/remove", {"kind": "skill", "name": "review"}, ["remove", "skill", "review"]),
    ("/api/enable", {"id": "skill:review@codex"}, ["enable", "skill:review@codex"]),
    ("/api/disable", {"id": "skill:review@*", "user": True},
     ["disable", "skill:review@*", "--user"]),
    ("/api/disable", {"id": "skill:review@claude", "project": "alpha"},
     ["disable", "skill:review@claude", "--project=alpha"]),
    ("/api/global-mode", {"mode": "managed"}, ["global", "mode", "managed"]),
    ("/api/install", {}, ["install"]),
    ("/api/install", {"user": True, "projects": ["alpha", "/srv/beta"]},
     ["install", "--user", "--project=alpha", "--project=/srv/beta"]),
])
def test_an_act_starts_its_verb_as_a_job_and_answers_202(lo, spawned, path, body, verb):
    status, answer = post(path, body)
    assert status == 202, answer
    job = job_of(answer)
    [command] = spawned
    assert command == pagejob.command(job, verb)
    record = json.loads(pagejob.record_path(job).read_text())
    assert record["argv"] == verb and record["pid"] == os.getpid()
    assert read("/api/job", f"job={job}") == {
        "job": job, "argv": verb, "state": "running", "exit": None, "outcome": None,
        "lines": []}


@pytest.mark.parametrize("path, body, says", [
    ("/api/add", {"kind": "skill", "name": "-x"}, "a name is letters"),
    ("/api/add", {"kind": "skill", "name": "lint", "project": "../elsewhere"},
     "by an absolute path"),
    ("/api/add", {"kind": "skill", "name": "lint", "project": "~/elsewhere"},
     "by an absolute path"),
    ("/api/add", {"kind": "skill", "name": "lint", "project": " "}, "name a project section"),
    ("/api/enable", {"id": "review"}, "is not <kind>:<name>@<harness>"),
    ("/api/enable", {"id": "skill:review@claude", "user": True, "project": "alpha"},
     "not both"),
    ("/api/install", {"projects": ["sub/dir"]}, "by an absolute path"),
    ("/api/replace", {"replace": []}, "name what to replace"),
    ("/api/replace", {"replace": ["skills/review"]}, "name a path absolutely"),
])
def test_an_act_the_verb_would_misread_is_refused_before_anything_is_written(
        lo, spawned, path, body, says):
    before = (lo / "loadout.toml").read_bytes()
    key = KEY if path == "/api/replace" else None
    status, answer = post(path, body, key=key)
    assert status == 422, answer
    assert says in answer["refused"]
    assert spawned == []
    assert not pagejob.jobs_dir().exists() or list(pagejob.jobs_dir().iterdir()) == []
    assert (lo / "loadout.toml").read_bytes() == before


def test_a_relative_section_the_manifest_names_exactly_goes_as_its_root(lo, spawned):
    kit.manifest(lo, '[user]\n[project."../proj"]\nskills = ["review"]\n')
    status, answer = post("/api/install", {"projects": ["../proj"]})
    assert status == 202, answer
    root = os.path.normpath(lo / ".." / "proj")
    assert verb_of(spawned[0]) == ["install", f"--project={root}"]


def test_global_mode_off_is_only_the_confirmed_act(lo, spawned):
    status, answer = post("/api/global-mode", {"mode": "off"})
    assert status == 400 and spawned == []


def test_a_job_that_does_not_start_is_refused_and_leaves_no_record(lo, monkeypatch, caplog):
    def spawn(argv, *, cwd, output):
        raise OSError("no such program")

    monkeypatch.setattr(pagejob, "_spawn", spawn)
    status, answer = post("/api/install", {})
    assert status == 422
    assert "didn't start (OSError)" in answer["refused"]
    assert list(pagejob.jobs_dir().iterdir()) == []


def test_a_job_whose_pid_cant_be_recorded_is_still_accepted(lo, spawned, monkeypatch):
    from moxi import proc

    def broken(pid):
        raise OSError("sample identity failure")

    monkeypatch.setattr(proc, "identity", broken)
    status, answer = post("/api/install", {})
    assert status == 202, answer
    assert len(spawned) == 1
    assert read("/api/job", f"job={job_of(answer)}")["state"] == "running"


# -- confirmed acts -------------------------------------------------------------------------

@pytest.mark.parametrize("path, body, verb", [
    ("/api/replace", {"replace": ["skill:review@claude", "/srv/alpha/.claude/skills/x"],
                      "projects": ["alpha"]},
     ["install", "--replace=skill:review@claude", "--replace=/srv/alpha/.claude/skills/x",
      "--project=alpha"]),
    ("/api/global-off", {}, ["global", "off"]),
])
def test_a_confirmed_act_runs_as_the_job_its_key_names(lo, spawned, path, body, verb):
    status, answer = post(path, body, key=KEY)
    assert (status, answer) == (202, {"status": f"/api/job?job={KEY}"})
    assert spawned == [pagejob.command(KEY, verb)]


def test_a_confirmed_act_needs_its_key(lo, spawned):
    status, _ = post("/api/global-off", {})
    assert status == 400 and spawned == []


def test_a_confirmed_repeat_gets_the_first_answer_and_starts_nothing_more(lo, spawned):
    app = served()
    first = post("/api/global-off", {}, key=KEY, app=app)
    assert post("/api/global-off", {}, key=KEY, app=app) == first
    assert len(spawned) == 1


def test_a_key_sent_with_another_body_is_refused(lo, spawned):
    app = served()
    assert post("/api/replace", {"replace": ["skill:review@claude"]}, key=KEY, app=app)[0] == 202
    status, _ = post("/api/replace", {"replace": ["skill:review@codex"]}, key=KEY, app=app)
    assert status == 422 and len(spawned) == 1


def test_a_repeat_while_the_first_still_runs_is_409(lo, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def spawn(argv, *, cwd, output):
        entered.set()
        release.wait(30)
        return os.getpid()

    monkeypatch.setattr(pagejob, "_spawn", spawn)

    async def both():
        app = served()
        first = asyncio.create_task(_send(app, "POST", "/api/global-off", body={}, key=KEY))
        while not entered.is_set():
            await asyncio.sleep(0.01)
        try:
            second = await _send(app, "POST", "/api/global-off", body={}, key=KEY)
        finally:
            release.set()
        return await first, second

    first, second = asyncio.run(both())
    assert first[0] == 202 and second[0] == 409


def test_a_retry_after_a_restart_finds_its_job(lo, spawned):
    body = {"replace": ["skill:review@claude"]}
    first = post("/api/replace", body, key=KEY)
    assert first[0] == 202
    # A new app is the page server after a restart: its key map is empty.
    assert post("/api/replace", body, key=KEY) == first
    assert len(spawned) == 1
    status, answer = post("/api/replace", {"replace": ["skill:review@codex"]}, key=KEY)
    assert status == 422 and "other arguments" in answer["refused"]
    assert len(spawned) == 1


# -- /api/job -------------------------------------------------------------------------------

def test_a_job_read_through_to_its_end_does_what_the_verb_does(lo, spawned, monkeypatch):
    monkeypatch.delenv(homes.MARKER, raising=False)
    assert install.install().code == 0           # the user's own run records the homes
    monkeypatch.setenv(homes.MARKER, "test")      # set back after the test
    kit.skill(lo / "skills", "lint")
    status, answer = post("/api/add", {"kind": "skill", "name": "lint"})
    assert status == 202
    job = job_of(answer)
    assert pagejob.run(job, verb_of(spawned[0])) == 0
    body = read("/api/job", f"job={job}")
    assert (body["state"], body["exit"], body["argv"]) == ("done", 0, ["add", "skill", "lint"])
    assert "listed skill lint in user" in body["outcome"]["lines"]
    assert body["outcome"]["counts"] == {"ok": 2}     # lint for claude and codex
    assert os.readlink(kit.claude() / "lint") == str(lo / "skills" / "lint")
    assert 'skills = ["review", "lint"]' in (lo / "loadout.toml").read_text()


def test_a_refused_job_reads_done_with_its_line(lo, spawned):
    status, answer = post("/api/install", {})
    job = job_of(answer)
    assert pagejob.run(job, ["install"]) == 3      # no homes recorded yet
    body = read("/api/job", f"job={job}")
    assert (body["state"], body["exit"], body["outcome"]) == ("done", 3, None)
    assert "moxi loadout install once in a terminal" in body["lines"][0]


def test_a_job_whose_process_is_gone_reads_interrupted(lo, monkeypatch):
    def spawn(argv, *, cwd, output):
        return 2 ** 22 + 7                          # no process has this pid

    monkeypatch.setattr(pagejob, "_spawn", spawn)
    job = job_of(post("/api/install", {})[1])
    body = read("/api/job", f"job={job}")
    assert body["state"] == "interrupted" and body["lines"] == [pagejob.INTERRUPTED]


def test_an_id_that_names_no_job_is_refused(lo):
    assert get("/api/job", "job=nothing-here")[0] == 422
    assert get("/api/job", "job=..%2Fx")[0] == 422
