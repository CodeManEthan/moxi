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
from moxi_dev.testing import timed_slack

from moxi import modules
from moxi_loadout import cli, homes, install, ledger, page, pagehandlers, pagejob

TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321
JOB = "0f" * 16


def loopback():
    found = modules.load("ui", interface=3)
    assert found.ok, found.reason
    return found.api.loopback


LB = loopback()


@pytest.fixture(autouse=True)
def fresh_log(monkeypatch):
    """Each test logs each failure class anew."""
    monkeypatch.setattr(pagehandlers, "_logged", set())


def served():
    inner = LB.app(page.PAGE, module="loadout", server_id=SERVER_ID, strict=True)
    profile = LB.upstream(LB.build_table(page.PAGE)[0], token=TOKEN, port=PORT)
    return LB.guard(inner, lambda: profile)


async def _send(app, method: str, path: str, *, query: str = "",
                body: dict | None = None) -> tuple[int, dict | None]:
    headers = [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode())]
    raw = b""
    if method == "POST":
        headers.append((b"content-type", b"application/json"))
        raw = json.dumps(body or {}).encode()
        headers.append((b"content-length", str(len(raw)).encode()))
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


def post(path: str, body: dict | None = None, *, app=None) -> tuple[int, dict | None]:
    return asyncio.run(_send(app or served(), "POST", path, body=body))


def printed(argv: list[str]):
    """What `moxi loadout <argv>` prints, read as JSON."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(argv)
    return json.loads(out.getvalue())


@pytest.fixture
def lo(loadout_dir):
    """A configured loadout folder with one skill, listed for the user."""
    kit.configure(loadout_dir)
    kit.skill(loadout_dir / "skills", "review")
    kit.manifest(loadout_dir, '[user]\nskills = ["review"]\n')
    return loadout_dir


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
        "/api/status", "/api/view", "/api/list", "/api/global", "/api/folder", "/api/projects",
        "/api/drift", "/api/job", "/api/replace-preview"}
    assert {p for p, r in routes.items() if r["kind"] == "act"} == {
        "/api/add", "/api/remove", "/api/enable", "/api/disable", "/api/global-mode",
        "/api/install", "/api/replace", "/api/global-remove"}
    assert not any(r.get("confirmed") for r in routes.values())
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
    marks = _all_marks()
    paths = {k for k, v in marks.items() if v == "path"}
    assert paths == {
        "/api/status rows[].target", "/api/job outcome.rows[].target",
        "/api/list manifest", "/api/list sections[].root", "/api/global files[].file",
        "/api/global files[].parts[]",
        "/api/projects projects[].root",
        "/api/view home", "/api/view folder", "/api/view manifest",
        "/api/view sections[].root", "/api/view rows[].target",
        "/api/view global.files[].file", "/api/view global.files[].parts[]",
        "/api/view global.source", "/api/view sections[].refused.last_root",
        "/api/folder unlisted[].source", "/api/status kept[].target",
        "/api/status kept[].copy", "/api/job outcome.kept[].target",
        "/api/job outcome.kept[].copy", "/api/replace-preview target",
        "/api/replace-preview kept_in", "/api/replace-preview skipped[].path"}
    for said in ("/api/status problem", "/api/status rows[].reason", "/api/status rows[].name",
                 "/api/status rows[].scope", "/api/status lines[]", "/api/status notes[]",
                 "/api/list items[].source", "/api/list items[].scope",
                 "/api/list sections[].name", "/api/global files[].diff",
                 "/api/projects reason", "/api/drift note", "/api/job lines[]",
                 "/api/job argv[]", "/api/job outcome.lines[]",
                 "/api/view problem", "/api/view lines[]", "/api/view notes[]",
                 "/api/view note", "/api/view sections[].name", "/api/view items[].scope",
                 "/api/view items[].source", "/api/view rows[].scope",
                 "/api/view rows[].name", "/api/view rows[].reason",
                 "/api/view global.files[].diff", "/api/view sections[].refused.line",
                 "/api/view items[].what.event", "/api/view items[].what.matcher",
                 "/api/view items[].what.command", "/api/view items[].what.url",
                 "/api/view items[].what.allow[]", "/api/view items[].what.ask[]",
                 "/api/view items[].what.deny[]", "/api/folder detail",
                 "/api/folder uncommitted[]", "/api/folder unlisted[].name",
                 "/api/status kept[].id", "/api/status kept[].back",
                 "/api/job outcome.kept[].id", "/api/job outcome.kept[].back",
                 "/api/replace-preview problem", "/api/replace-preview diff"):
        assert marks[said] == "redact", said
    plain = {"/api/view harnesses[]", "/api/view counts.ok", "/api/view sections[].where",
             "/api/view items[].kind", "/api/view items[].name", "/api/view items[].harnesses[]",
             "/api/view items[].disabled[]", "/api/view rows[].kind",
             "/api/view rows[].harness", "/api/view rows[].state",
             "/api/view rows[].replaceable", "/api/view rows[].form",
             "/api/view rows[].edited", "/api/view rows[].covers[]",
             "/api/view rows[].prior", "/api/view rows[].applies",
             "/api/view rows[].ignores", "/api/view resolved_all", "/api/view global.mode",
             "/api/view global.chosen", "/api/view global.files[].harness",
             "/api/view global.files[].changes", "/api/view global.files[].placed",
             "/api/view global.files[].state", "/api/view global.files[].form",
             "/api/view sections[].refused.cause", "/api/view items[].module",
             "/api/view item_notes[].kind", "/api/view item_notes[].name",
             "/api/view item_notes[].harness", "/api/view item_notes[].code",
             "/api/projects cause", "/api/folder state", "/api/folder more",
             "/api/folder unlisted[].kind", "/api/folder reinstalls",
             "/api/status kept[].how", "/api/job outcome.kept[].how",
             "/api/replace-preview case", "/api/replace-preview tracked",
             "/api/replace-preview others", "/api/replace-preview label",
             "/api/replace-preview lines", "/api/replace-preview skipped[].why"}
    assert not plain & set(marks)


#: Every mark the routes gave before this page read `/api/view`, copied from `page.py` as it
#: was then: no field may carry a weaker one now.
EARLIER_MARKS = {
    "/api/drift note": "redact", "/api/drift problem": "redact",
    "/api/global files[].diff": "redact", "/api/global files[].file": "path",
    "/api/global files[].parts[]": "path", "/api/global problem": "redact",
    "/api/job argv[]": "redact", "/api/job lines[]": "redact",
    "/api/job outcome.lines[]": "redact", "/api/job outcome.notes[]": "redact",
    "/api/job outcome.rows[].name": "redact", "/api/job outcome.rows[].reason": "redact",
    "/api/job outcome.rows[].scope": "redact", "/api/job outcome.rows[].target": "path",
    "/api/list items[].scope": "redact", "/api/list items[].source": "redact",
    "/api/list manifest": "path", "/api/list problem": "redact",
    "/api/list sections[].name": "redact", "/api/list sections[].root": "path",
    "/api/projects projects[].root": "path", "/api/projects reason": "redact",
    "/api/status lines[]": "redact", "/api/status notes[]": "redact",
    "/api/status problem": "redact", "/api/status rows[].name": "redact",
    "/api/status rows[].reason": "redact", "/api/status rows[].scope": "redact",
    "/api/status rows[].target": "path",
}
STRENGTH = {None: 0, "path": 1, "redact": 2}


def _all_marks() -> dict[str, str]:
    marks: dict[str, str] = {}
    for route in LB.export(page.PAGE)["routes"]:
        for key, shape in route["returns"].items():
            marks.update({f"{route['path']} {k}": v for k, v in _marks(shape, key).items()})
    return marks


def test_no_field_carries_a_weaker_mark_than_it_did():
    marks = _all_marks()
    weaker = {k: (v, marks.get(k)) for k, v in EARLIER_MARKS.items()
              if STRENGTH[marks.get(k)] < STRENGTH[v]}
    assert weaker == {}


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
             "print(sorted(m for m in sys.modules if m.startswith(('moxi_ui', "
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


def test_drift_counts_the_items_not_ok_and_notes_a_difference_without_counting_it(
        lo, monkeypatch, tmp_path, terminal_record):
    terminal_record()
    # review is missing for claude and for codex: one item, however many harnesses.
    assert read("/api/status")["counts"] == {"missing": 2}
    assert read("/api/drift")["count"] == 1
    assert install.install().code == 0
    assert read("/api/drift") == {"problem": None, "count": 0, "note": None}
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "elsewhere"))
    body = read("/api/drift")
    assert "Codex's home" in body["note"] and "CODEX_HOME" in body["note"]
    assert body["count"] == len({(r["scope"], r["kind"], r["name"])
                                 for r in read("/api/status")["rows"]
                                 if r["state"] not in ("ok", "off")})
    # The page's reads word the note for the page, as a job's refusal is worded.
    assert "; or run the command in a terminal." in body["note"]
    assert body["note"] in read("/api/status")["notes"]


def test_view_answers_from_the_one_plan_status_builds(lo, tmp_path, monkeypatch,
                                                     terminal_record):
    from moxi import paths, place

    terminal_record()
    proj = tmp_path / "proj"
    proj.mkdir()
    (lo / "global.md").write_text("Be kind.\n")
    kit.agent(lo / "agents" / "helper.md", "helper")
    kit.manifest(lo, '[global]\nmode = "managed"\n[user]\nskills = ["review"]\n'
                     f'[project."{proj}"]\nagents = ["helper"]\n')
    built = []
    real = install._plan

    def counted(*a, **kw):
        built.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(install, "_plan", counted)
    body = read("/api/view")
    assert len(built) == 1
    assert body["problem"] is None
    status = read("/api/status")
    facts = ("form", "edited", "covers", "prior", "applies", "ignores")
    assert [{k: v for k, v in r.items() if k not in facts} for r in body["rows"]] == \
        status["rows"]
    assert {k: body[k] for k in ("lines", "notes", "counts")} == {
        k: status[k] for k in ("lines", "notes", "counts")}
    by = {(r["kind"], r["name"], r["harness"], r["scope"]): r for r in body["rows"]}
    assert {k: by[("skill", "review", "claude", "user")][k] for k in facts} == {
        "form": "link", "edited": False, "covers": None, "prior": False,
        "applies": "section", "ignores": False}
    assert body["resolved_all"] is True
    assert any(r["scope"] == str(proj) for r in body["rows"])
    listed = read("/api/list")
    assert [{k: v for k, v in s.items() if k != "refused"} for s in body["sections"]] == \
        listed["sections"]
    assert [s["refused"] for s in body["sections"]] == [None, None]
    assert [{k: v for k, v in i.items() if k not in ("module", "what")}
            for i in body["items"]] == listed["items"]
    assert [(i["module"], i["what"]) for i in body["items"]] == [(None, None)] * 2
    assert body["manifest"] == listed["manifest"] == str(lo / "loadout.toml")
    shown = read("/api/global")
    assert body["global"]["source"] == str(lo / "global.md")
    assert {k: v for k, v in body["global"].items() if k not in ("source", "files")} == {
        k: v for k, v in shown.items() if k not in ("problem", "files")}
    assert [{k: v for k, v in f.items() if k != "form"} for f in body["global"]["files"]] \
        == shown["files"]
    assert {f["form"] for f in body["global"]["files"]} <= {"line", "block", "instructions"}
    assert body["global"]["chosen"] and body["global"]["files"]
    assert body["item_notes"] == []
    assert body["note"] == read("/api/drift")["note"] is None
    assert body["harnesses"] == list(place.harnesses()[0])
    assert "claude" in body["harnesses"]
    assert body["home"] == str(paths.home())
    assert body["folder"] == str(lo)


def test_view_notes_a_difference_from_the_terminal_in_the_pages_words(
        lo, monkeypatch, tmp_path, terminal_record):
    terminal_record()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "elsewhere"))
    body = read("/api/view")
    assert "; or run the command in a terminal." in body["note"]
    assert body["note"] == read("/api/drift")["note"]
    assert body["note"] in body["notes"]


def test_a_manifest_that_does_not_read_is_each_reads_problem(lo):
    kit.manifest(lo, "[user]\nnonsense = 1\n")
    for path in ("/api/status", "/api/view", "/api/list", "/api/global", "/api/drift"):
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


#: How long a read may take while git stalls.
STALL_S = 15.0
#: The land-check tests' bound, five times `STALL_S`: load alone doesn't fail it, and a
#: git call that was never cut off does, since the stalled git sleeps twice as long.
STALL_LOOSE_S = 5 * STALL_S
POSIX_GIT = pytest.mark.skipif(os.name != "posix", reason="the stalled git is a shell script")


def read_while_git_stalls(path: str, monkeypatch, tmp_path) -> tuple[dict, float]:
    """`path` read while git doesn't answer, and how long the read took."""
    stalled = tmp_path / "stalled-bin"
    stalled.mkdir()
    (stalled / "git").write_text("#!/bin/sh\nexec sleep 150\n")
    (stalled / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{stalled}{os.pathsep}{os.environ['PATH']}")
    start = time.monotonic()
    body = read(path)
    return body, time.monotonic() - start


@POSIX_GIT
def test_a_read_returns_while_git_stalls(lo, monkeypatch, tmp_path):
    body, took = read_while_git_stalls("/api/status", monkeypatch, tmp_path)
    assert took < STALL_LOOSE_S, took
    assert body["problem"] is None


@pytest.mark.timed
@POSIX_GIT
def test_a_read_returns_while_git_stalls_in_time(lo, monkeypatch, tmp_path, timed_reading):
    body, took = read_while_git_stalls("/api/status", monkeypatch, tmp_path)
    timed_reading(took, STALL_S, "s")
    assert took < STALL_S * timed_slack(), took
    assert body["problem"] is None


# -- the folder ---------------------------------------------------------------------------

def git(cwd, *args) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "init.defaultBranch=main", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=True, timeout=30)
    return done.stdout


@pytest.fixture
def tree(tmp_path):
    """A loadout folder two levels inside a git work tree, its one skill committed."""
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-q")
    loadout_dir = kit.configure(root / "dots" / "loadout")
    kit.skill(loadout_dir / "skills", "review")
    kit.manifest(loadout_dir, '[user]\nskills = ["review"]\n')
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    return loadout_dir


def test_source_dir_outside_a_work_tree_names_no_uncommitted_files(site, lo):
    assert read("/api/folder") == {"state": "ok", "detail": None, "uncommitted": None,
                                   "more": 0, "unlisted": [], "reinstalls": False}


def test_source_dir_in_a_clean_work_tree_has_nothing_uncommitted(site, tree):
    body = read("/api/folder")
    assert (body["state"], body["uncommitted"], body["more"]) == ("ok", [], 0)


def test_source_dir_names_each_uncommitted_file_relative_to_it(site, tree):
    (tree / "skills" / "review" / "SKILL.md").write_text("changed\n")
    kit.skill(tree / "skills", "fresh")
    (tree / "skills" / "fresh" / "notes").mkdir()
    (tree / "skills" / "fresh" / "notes" / "a.md").write_text("a\n")
    (tree.parent.parent / "elsewhere.md").write_text("not the folder's\n")
    body = read("/api/folder")
    assert body["state"] == "ok" and body["more"] == 0
    assert sorted(body["uncommitted"]) == ["skills/fresh/SKILL.md", "skills/fresh/notes/a.md",
                                           "skills/review/SKILL.md"]


def test_source_dir_names_a_renamed_file_by_its_new_path(site, tree):
    git(tree, "mv", "loadout.toml", "renamed.toml")
    assert read("/api/folder")["uncommitted"] == ["renamed.toml"]


def test_source_dir_names_twenty_files_and_counts_the_rest(site, tree):
    for n in range(23):
        (tree / f"note-{n:02}.md").write_text(f"{n}\n")
    body = read("/api/folder")
    assert body["uncommitted"] == [f"note-{n:02}.md" for n in range(20)]
    assert body["more"] == 3


def test_source_dir_read_writes_nothing_to_the_index(site, tree):
    index = tree.parent.parent / ".git" / "index"
    before = index.read_bytes()
    os.utime(tree / "loadout.toml", (1_000_000_000, 1_000_000_000))  # git would refresh it
    read("/api/folder")
    assert index.read_bytes() == before


def _says_git_is_slow(body: dict) -> None:
    assert body["state"] == "error" and body["detail"] == pagehandlers.GIT_SLOW
    assert (body["uncommitted"], body["more"]) == (None, 0)


@POSIX_GIT
def test_source_dir_whose_git_stalls_says_so_and_names_no_files(site, tree, monkeypatch, tmp_path):
    body, took = read_while_git_stalls("/api/folder", monkeypatch, tmp_path)
    assert took < STALL_LOOSE_S, took
    _says_git_is_slow(body)


@pytest.mark.timed
@POSIX_GIT
def test_source_dir_whose_git_stalls_says_so_and_names_no_files_in_time(
        site, tree, monkeypatch, tmp_path, timed_reading):
    body, took = read_while_git_stalls("/api/folder", monkeypatch, tmp_path)
    timed_reading(took, STALL_S, "s")
    assert took < STALL_S * timed_slack(), took
    _says_git_is_slow(body)


def test_source_dir_whose_git_fails_says_so_in_a_line_with_no_path(site, lo, monkeypatch):
    (lo / ".git").mkdir()  # a work tree git can't read
    body = read("/api/folder")
    assert body["state"] == "error" and body["detail"] == pagehandlers.GIT_FAILED
    assert str(lo) not in body["detail"]
    assert body["uncommitted"] is None


def test_source_dir_names_the_skills_and_agents_no_section_lists(site, lo, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    kit.skill(lo / "skills", "spare")
    kit.skill(lo / "skills", "held")
    (lo / "skills" / "empty").mkdir()
    kit.skill(lo / "skills", ".hidden")
    kit.skill(lo / "skills", "bad name")
    kit.agent(lo / "agents" / "helper.md", "helper")
    kit.agent(lo / "agents" / "loose.md", "loose")
    (lo / "agents" / "readme.txt").write_text("not an agent\n")
    kit.manifest(lo, f'[user]\nskills = ["review"]\n[project."{proj}"]\n'
                     'skills = ["held"]\nagents = ["helper"]\n')
    assert read("/api/folder")["unlisted"] == [
        {"kind": "skill", "name": "spare", "source": str(lo / "skills" / "spare")},
        {"kind": "agent", "name": "loose", "source": str(lo / "agents" / "loose.md")}]


def test_source_dir_lists_nothing_unlisted_when_the_manifest_does_not_read(site, lo):
    kit.skill(lo / "skills", "spare")
    kit.manifest(lo, "[user]\nnonsense = 1\n")
    body = read("/api/folder")
    assert (body["state"], body["unlisted"]) == ("ok", [])


REPO_API = '''
import os


class Failure(Exception):
    def __init__(self, text, *, lines=None):
        super().__init__(text)
        self.text, self.lines = text, list(lines or [])


def status(path=None):
    pieces = [p for p in os.environ.get("FAKE_REPO_PIECES", "").split(",") if p]
    return [{"root": path, "pieces": [{"id": p, "enabled": True} for p in pieces]}]
'''


@pytest.mark.parametrize("pieces, setting, reinstalls", [
    ("loadout/reinstall-commit,loadout/reinstall-merge", "true", True),
    ("loadout/reinstall-commit,loadout/reinstall-merge", "false", False),
    ("loadout/reinstall-commit", "true", False),
])
def test_source_dir_reinstalls_only_with_repo_the_setting_and_both_pieces(
        site, tree, monkeypatch, pieces, setting, reinstalls):
    from moxi import config

    assert read("/api/folder")["reinstalls"] is False  # no repo
    site.module("repo", API="'moxi_repo.api'", files={"api.py": REPO_API})
    modules.reset()
    monkeypatch.setenv("FAKE_REPO_PIECES", pieces)
    config.set_value("loadout", "reinstall_on_commit", setting, by="test")
    assert read("/api/folder")["reinstalls"] is reinstalls


def test_source_dir_with_settings_that_do_not_read_is_an_error(site, lo, monkeypatch):
    from moxi.config import ConfigError

    from moxi_loadout import settings

    def broken():
        raise ConfigError("loadout.toml", "source", "bad value", "set it again")

    monkeypatch.setattr(settings, "read", broken)
    body = read("/api/folder")
    assert body["state"] == "error" and "bad value" in body["detail"]
    assert (body["uncommitted"], body["unlisted"], body["reinstalls"]) == (None, [], False)


# -- the replace preview --------------------------------------------------------------------

def test_replace_preview_answers_what_a_replace_would_do(lo):
    from moxi_loadout import displaced, preview

    kit.claude().mkdir(parents=True, exist_ok=True)
    for path in kit.claude().iterdir():
        path.unlink()
    kit.skill(kit.claude(), "review", "mine\n")
    answer = read("/api/replace-preview", "id=skill%3Areview%40claude&section=user")
    target = kit.claude() / "review"
    assert answer["problem"] is None
    assert (answer["case"], answer["target"], answer["kept_in"]) == (
        "moves", str(target), str(displaced.root()))
    assert answer == {"problem": None, **preview.replace_preview("skill:review@claude", "user")}
    assert "-mine" in answer["diff"]


def test_replace_preview_of_a_row_not_replaceable_has_no_case(lo):
    assert install.install().code == 0
    answer = read("/api/replace-preview", "id=skill%3Areview%40claude")
    assert (answer["case"], answer["target"], answer["lines"]) == (None, None, 0)


@pytest.mark.parametrize("query", ["id=not-an-id", "id=skill%3Areview%40claude%40x",
                                   "id=.%2Frelative"])
def test_replace_preview_refuses_what_the_verb_would(lo, query):
    status, body = get("/api/replace-preview", query)
    assert status == 422, body


def test_replace_preview_that_fails_says_so(lo, monkeypatch):
    from moxi_loadout import preview

    def boom(*a, **kw):
        raise RuntimeError("secret /home/path")

    monkeypatch.setattr(preview, "replace_preview", boom)
    answer = read("/api/replace-preview", "id=skill%3Areview%40claude")
    assert answer["case"] is None and "RuntimeError" in answer["problem"]
    assert "/home/path" not in answer["problem"]


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
    if error == "other":
        raise OSError("a secret detail")
    return [Project("alpha", "/srv/alpha"), Project("beta", "/srv/beta")]
'''


def test_projects_absent_is_the_pickers_reason(site):
    from moxi_loadout import projects_link

    assert read("/api/projects") == {"projects": [], "reason": projects_link.ABSENT,
                                     "cause": "absent"}


def test_projects_that_wont_load_is_an_error(site):
    site.module("projects", API="'moxi_projects.missing'")
    body = read("/api/projects")
    assert body["cause"] == "error" and "can't be loaded" in body["reason"]


@pytest.mark.parametrize("error, reason, cause", [
    (None, None, None),
    ("busy", "projects is busy; try again", "busy"),
    ("unreadable", "projects can't be read: the projects store can't be read; move it aside",
     "unreadable"),
    ("other", "projects couldn't be listed (OSError)", "error"),
])
def test_projects_lists_by_key_or_says_why_not(site, monkeypatch, error, reason, cause):
    site.module("projects", API="'moxi_projects.api'", files={"api.py": PROJECTS_API})
    if error:
        monkeypatch.setenv("FAKE_PROJECTS_ERROR", error)
    body = read("/api/projects")
    assert (body["reason"], body["cause"]) == (reason, cause)
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
    ("/api/replace", {"replace": " ", "job": JOB}, "name what to replace"),
    ("/api/replace", {"replace": "skills/review", "job": JOB}, "name a path absolutely"),
    ("/api/replace", {"replace": "skill:review@claude", "user": True, "project": "alpha",
                      "job": JOB}, "not both"),
    ("/api/replace", {"replace": "skill:review@claude", "job": "K" * 32}, "32 hex"),
    ("/api/replace", {"replace": "skill:review@claude", "job": JOB.upper()}, "32 hex"),
    ("/api/global-remove", {"job": "x"}, "32 hex"),
])
def test_an_act_the_verb_would_misread_is_refused_before_anything_is_written(
        lo, spawned, path, body, says):
    before = (lo / "loadout.toml").read_bytes()
    status, answer = post(path, body)
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


def test_global_mode_off_is_only_global_remove(lo, spawned):
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


# -- the acts named by the job the page minted ----------------------------------------------

@pytest.mark.parametrize("path, body, verb", [
    ("/api/replace", {"replace": "skill:review@claude", "project": "alpha", "job": JOB},
     ["install", "--replace=skill:review@claude", "--project=alpha"]),
    ("/api/replace", {"replace": "/srv/alpha/.claude/skills/x", "user": True, "job": JOB},
     ["install", "--replace=/srv/alpha/.claude/skills/x", "--user"]),
    # A row a whole run applies (the global file's part): no section.
    ("/api/replace", {"replace": "global:global@claude", "job": JOB},
     ["install", "--replace=global:global@claude"]),
    ("/api/global-remove", {"job": JOB}, ["global", "remove"]),
])
def test_a_named_act_runs_its_verb_and_nothing_else_as_the_job_it_names(
        lo, spawned, path, body, verb):
    status, answer = post(path, body)
    assert (status, answer) == (202, {"status": f"/api/job?job={JOB}"})
    assert spawned == [pagejob.command(JOB, verb)]


@pytest.mark.parametrize("path, body", [
    ("/api/replace", {"replace": "skill:review@claude", "user": True}),
    ("/api/global-remove", {}),
])
def test_a_named_act_needs_its_job(lo, spawned, path, body):
    status, _ = post(path, body)
    assert status == 400 and spawned == []


@pytest.mark.parametrize("path, body", [
    ("/api/replace", {"replace": ["skill:review@claude"], "job": JOB}),
    ("/api/replace", {"replace": "skill:review@claude", "confirmed": True, "job": JOB}),
    ("/api/global-remove", {"job": JOB, "status": "/api/job"}),
])
def test_a_named_act_takes_no_list_and_no_confirmation(lo, spawned, path, body):
    status, _ = post(path, body)
    assert status == 400 and spawned == []


def test_a_repeat_finds_the_job_it_started(lo, spawned):
    body = {"replace": "skill:review@claude", "user": True, "job": JOB}
    first = post("/api/replace", body)
    assert first[0] == 202
    # Each post is a new app, as the page server after a restart.
    assert post("/api/replace", body) == first
    assert post("/api/global-remove", {"job": "1" * 32}) != first
    assert len(spawned) == 2


def test_the_same_job_with_another_body_is_refused(lo, spawned):
    assert post("/api/replace", {"replace": "skill:review@claude", "job": JOB})[0] == 202
    status, answer = post("/api/replace", {"replace": "skill:review@codex", "job": JOB})
    assert status == 422 and "other arguments" in answer["refused"]
    status, answer = post("/api/global-remove", {"job": JOB})
    assert status == 422 and "other arguments" in answer["refused"]
    assert len(spawned) == 1


def test_two_presses_at_once_start_one_job(lo, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    spawned: list[list[str]] = []

    def spawn(argv, *, cwd, output):
        spawned.append(argv)
        entered.set()
        release.wait(30)
        return os.getpid()

    monkeypatch.setattr(pagejob, "_spawn", spawn)

    async def both():
        app = served()
        body = {"job": JOB}
        first = asyncio.create_task(_send(app, "POST", "/api/global-remove", body=body))
        while not entered.is_set():
            await asyncio.sleep(0.01)
        try:
            second = await _send(app, "POST", "/api/global-remove", body=body)
        finally:
            release.set()
        return await first, second

    first, second = asyncio.run(both())
    assert first == second == (202, {"status": f"/api/job?job={JOB}"})
    assert len(spawned) == 1


# -- /api/job -------------------------------------------------------------------------------

def test_a_job_read_through_to_its_end_does_what_the_verb_does(
        lo, spawned, monkeypatch, terminal_record):
    terminal_record()                             # a command typed in the user's terminal
    assert install.install().code == 0
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


def test_a_job_names_what_its_run_kept_and_how_to_put_it_back(lo, spawned, terminal_record):
    terminal_record()
    kit.claude().mkdir(parents=True)
    kit.skill(kit.claude(), "review", "mine\n")
    target = kit.claude() / "review"
    status, answer = post("/api/replace", {"replace": "skill:review@claude", "user": True,
                                           "job": JOB})
    assert status == 202
    job = job_of(answer)
    assert pagejob.run(job, verb_of(spawned[0])) == 0
    [kept] = read("/api/job", f"job={job}")["outcome"]["kept"]
    assert (kept["target"], kept["how"], kept["id"]) == (str(target), "replaced",
                                                         "skill:review@claude")
    assert kept["back"].endswith(f"mv {kept['copy']} {target}")
    assert read("/api/status")["kept"] == []


def test_a_job_recorded_before_outcomes_named_what_they_kept_reads_none_kept(lo, spawned):
    status, answer = post("/api/install", {})
    job = job_of(answer)
    end = pagejob.end_path(job)
    os.makedirs(os.path.dirname(end), exist_ok=True)
    Path(end).write_text(json.dumps({"exit": 0, "lines": [], "outcome": {
        "rows": [], "lines": [], "notes": [], "counts": {}, "exit": 0}}))
    assert read("/api/job", f"job={job}")["outcome"]["kept"] == []


def test_a_refused_job_reads_done_with_its_line(lo, spawned):
    status, answer = post("/api/install", {})
    job = job_of(answer)
    assert pagejob.run(job, ["install"]) == 3      # no terminal recorded yet
    body = read("/api/job", f"job={job}")
    assert (body["state"], body["exit"], body["outcome"]) == ("done", 3, None)
    assert "Type moxi modules in a terminal of your own" in body["lines"][0]


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


def test_the_components_are_the_page_the_drift_block_and_the_project_facts():
    from moxi_loadout import module

    assert set(module.COMPONENTS) == {"page", "drift", "project-facts"}
    for name, decl in module.COMPONENTS.items():
        assert decl["script"] == f"{name}.js"
        assert decl["contract"] == 1
    facts = module.COMPONENTS["project-facts"]
    assert facts["place"] == "inline" and set(facts["attributes"]) == {"projects", "open"}
    assert all(spec["type"] == "string" for spec in facts["attributes"].values())


def test_the_page_states_its_group_and_order_among_the_pages():
    from moxi_loadout import module

    page_decl = module.COMPONENTS["page"]
    assert (page_decl["group"], page_decl["order"]) == ("setup", 10)
