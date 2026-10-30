"""The CLI: each verb parses, calls the library function it stands for, and exits with
the code its outcome or refusal carries."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import exit as codes
from moxi import modules
from moxi import store as core_store
from moxi.config import ConfigError

from launcher_fake_harness import Harness
from moxi_launcher import api, catalog, cli, first, handoffs, presets, slots, store, targets
from moxi_launcher.compose import common

BRIEF = "Read the design and start the build."
POSIX = pytest.mark.skipif(sys.platform == "win32",
                           reason="the fake harness is a script run by its #! line")


@pytest.fixture
def work(tmp_path, monkeypatch) -> Path:
    """A directory to launch from, the process's own."""
    path = tmp_path / "work"
    path.mkdir()
    monkeypatch.chdir(path)
    return path


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


@pytest.fixture
def no_projects(monkeypatch):
    """projects not installed, whatever this environment holds."""
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "projects" and real(name))


@pytest.fixture
def no_slots(monkeypatch):
    """slots not installed, whatever this environment holds."""
    monkeypatch.delenv(slots.SEAM, raising=False)
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "slots" and real(name))


def write_presets(text: str) -> None:
    file = presets.path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)


def rows() -> list[store.LaunchRow]:
    if not store.path().exists():
        return []
    conn = store.connect(create=False)
    try:
        return store.log(conn)
    finally:
        conn.close()


def run(argv, capsys) -> tuple[int, str, str]:
    code = cli.main(argv)
    seen = capsys.readouterr()
    return code, seen.out, seen.err


# -- parsing --------------------------------------------------------------------------------

PARSES = [
    (["run"], {"verb": "run", "seat": None, "project_preset": None, "rest": []}),
    (["run", "--seat", "planner"], {"seat": "planner"}),
    (["run", "--project-preset", "moxi", "--off-posture"],
     {"project_preset": "moxi", "off_posture": True}),
    (["run", "--model", "opus", "--seat", "planner", "--", "--verbose"],
     {"seat": "planner", "rest": ["--model", "opus", "--", "--verbose"]}),
    # Nothing is abbreviated: --project is the catalog's row, kept for it in rest.
    (["run", "--project", "moxi"], {"project_preset": None, "rest": ["--project", "moxi"]}),
    (["preview", "--off", "--seat", "s"], {"off_posture": False, "rest": ["--off"]}),
    (["preview", "--seat", "planner", "--json"], {"verb": "preview", "json": True}),
    (["log"], {"verb": "log", "limit": None, "before": None, "json": False}),
    (["log", "--limit", "5", "--before", "2026-10-30T00:00:00Z", "--json"],
     {"limit": 5, "before": "2026-10-30T00:00:00Z", "json": True}),
    (["presets"], {"verb": "presets", "action": "list", "name": None}),
    (["presets", "show", "planner"], {"action": "show", "name": "planner"}),
    (["presets", "check", "--json"], {"action": "check", "json": True}),
    (["presets", "--project", "moxi"], {"action": "list", "project": "moxi"}),
    (["catalog"], {"verb": "catalog", "harness": None, "json": False}),
    (["catalog", "codex", "--json"], {"harness": "codex", "json": True}),
    (["handoff", "dir"], {"verb": "handoff", "what": "dir"}),
    (["handoffs", "--key", "planner"], {"verb": "handoffs", "key": "planner"}),
    (["deliver", "r-1", "--pid", "4242"],
     {"verb": "deliver", "request_id": "r-1", "pid": 4242}),
]


@pytest.mark.parametrize(("argv", "expected"), PARSES, ids=[" ".join(a) for a, _ in PARSES])
def test_each_verb_parses(argv, expected):
    args = vars(cli.parse(argv))
    assert {key: args[key] for key in expected} == expected


def test_every_verb_has_a_handler():
    assert set(cli.VERBS) == {"run", "preview", "log", "presets", "catalog", "handoff",
                              "handoffs", "deliver"}


REFUSED = [
    [],
    ["nope"],
    ["run", "--seat", "a", "--project-preset", "b"],
    ["log", "--bogus"],
    ["log", "--limit", "many"],
    ["presets", "show"],
    ["presets", "list", "planner"],
    ["handoff"],
    ["handoff", "file"],
    ["deliver", "r-1"],
]


@pytest.mark.parametrize("argv", REFUSED, ids=[" ".join(a) or "(none)" for a in REFUSED])
def test_bad_arguments_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as raised:
        cli.parse(argv)
    assert raised.value.code == codes.USAGE


def test_no_verb_says_it_is_not_built(capsys):
    """Every verb is wired: none prints the skeleton's placeholder any more."""
    source = Path(cli.__file__).read_text()
    assert "not built yet" not in source


# -- how a refusal prints and exits ----------------------------------------------------------


@pytest.mark.parametrize("error, code, short", [
    (catalog.BadValue("model", "bad model", "pick another"), codes.USAGE, "bad-value"),
    (catalog.NeedsModule(catalog.row("launcher", "slot")), codes.REFUSED, "needs-module"),
    (targets.NoSlots(), codes.REFUSED, "no-slots"),
    (targets.NoCommand("claude"), codes.ERROR, "not-found"),
    (presets.ProjectsBusy("locked", "try again"), codes.TEMPFAIL, "busy"),
    (store.Busy("the launch log is locked"), codes.TEMPFAIL, "busy"),
    (store.TooLarge("brief", store.CAP + 1), codes.REFUSED, "too-large"),
    (store.SessionTaken("s-1", "r-0"), codes.REFUSED, "session-taken"),
    (core_store.StoreTooNew("launcher.db", 9, 1), codes.REFUSED, "store-too-new"),
    (ConfigError("presets.toml", None, "is not TOML", "fix the file"), codes.USAGE,
     "bad-config"),
    (handoffs.NoKey("no key here"), codes.REFUSED, "no-key"),
    (first.HandoffUnreadable("/h.md", "gone"), codes.REFUSED, "unreadable"),
])
def test_a_refusal_prints_one_line_its_code_and_a_hint(error, code, short, monkeypatch,
                                                       capsys):
    def raising(*args, **kwargs):
        raise error
    monkeypatch.setattr(api, "launch", raising)
    status, out, err = run(["run"], capsys)
    assert status == code
    line, hint = err.splitlines()
    assert line.startswith("moxi launcher run: ") and line.endswith(f"[{short}]")
    assert hint.startswith("hint: ") and len(hint) > len("hint: ")
    assert out == ""


def test_an_error_that_is_not_a_refusal_is_not_hidden(monkeypatch):
    def raising(*args, **kwargs):
        raise RuntimeError("a bug")
    monkeypatch.setattr(api, "launch", raising)
    with pytest.raises(RuntimeError):
        cli.main(["run"])


# -- run ------------------------------------------------------------------------------------


def result(**fields) -> api.LaunchResult:
    base = {"request_id": "r-1", "harness": "claude", "target": "headless",
            "state": "started", "exit_code": codes.OK, "line": "launch r-1 started"}
    return api.LaunchResult(**{**base, **fields})


def test_run_hands_launch_the_typed_flags_and_the_warn_line_printer(work, monkeypatch,
                                                                    capsys):
    seen = {}

    def launch(request, *, environ=None, warn=None):
        seen["request"] = request
        warn("identity planner is ignored")
        return result()
    monkeypatch.setattr(api, "launch", launch)
    code, out, err = run(["run", "--model", "opus", "--target", "headless", "--brief",
                          BRIEF, "--verbose", "--", "--debug"], capsys)
    request = seen["request"]
    assert code == codes.OK and out == "launch r-1 started\n"
    assert dict(request.typed) == {"model": "opus", "target": "headless", "brief": BRIEF}
    assert tuple(request.extra) == ("--verbose", "--debug")
    assert request.cwd == str(work) and request.seat is None
    assert err.splitlines() == [
        "warning: passing on, last on the argv, flags the catalog does not know: --verbose",
        "warning: identity planner is ignored"]


def test_run_reads_the_flags_against_the_presets_harness(work, monkeypatch, capsys):
    write_presets('[seat.coder]\nharness = "codex"\n')
    seen = {}
    monkeypatch.setattr(api, "launch", lambda request, **kw: (
        seen.setdefault("request", request), result(harness="codex"))[1])
    # --search is a Codex flag; read against Claude's catalog it would pass on unknown.
    code, _, err = run(["run", "--seat", "coder", "--search"], capsys)
    assert code == codes.OK and err == ""
    assert seen["request"].seat == "coder"
    assert dict(seen["request"].typed) == {"search": True}


@pytest.mark.parametrize("state, exit_code, reason", [
    ("failed", codes.ERROR, "spawn-failed"),
    ("failed", codes.REFUSED, "dropped"),
    ("failed", codes.TEMPFAIL, "try-again"),
])
def test_run_exits_with_the_rows_code_after_the_row(work, monkeypatch, capsys, state,
                                                    exit_code, reason):
    monkeypatch.setattr(api, "launch", lambda request, **kw: result(
        state=state, exit_code=exit_code, reason=reason, line=f"launch r-1 {reason}"))
    code, out, err = run(["run"], capsys)
    assert code == exit_code and out == "" and err == f"launch r-1 {reason}\n"


def test_run_prints_a_first_turn_outcome_the_launch_sent(work, monkeypatch, capsys):
    outcome = first.Outcome("r-1", "unconfirmed", "first-sent", "unconfirmed")
    monkeypatch.setattr(api, "launch", lambda request, **kw: result(
        target="slot", outcome=outcome))
    code, out, _ = run(["run"], capsys)
    assert code == codes.OK
    assert out.splitlines() == ["launch r-1 started", outcome.line()]


@POSIX
def test_run_headless_starts_the_harness_and_logs_the_launch(work, harness, capsys):
    code, out, err = run(["run", "--target", "headless", "--brief", BRIEF], capsys)
    assert code == codes.OK, err
    launched = harness.one()
    [row] = rows()
    assert f"launch {row.request_id} started headless, pid " in out
    assert (row.state, row.target, row.cwd) == ("started", "headless", str(work))
    assert launched["argv"][1:3] == ["-p", f"Brief from launcher, launched by "
                                           f"{first.actor()}\n\n{BRIEF}"]


@POSIX
def test_run_whose_start_fails_exits_1_and_the_row_says_why(work, harness, monkeypatch,
                                                            capsys):
    def no_spawn(argv, **kwargs):
        raise OSError("no room")
    monkeypatch.setattr(targets, "_spawn", no_spawn)
    code, out, err = run(["run", "--target", "headless", "--brief", BRIEF], capsys)
    [row] = rows()
    assert code == codes.ERROR and out == ""
    assert f"launch {row.request_id} failed" in err
    assert (row.state, row.reason) == ("failed", "spawn-failed")


@pytest.mark.parametrize("argv, code, short", [
    (["run", "--seat", "nobody"], codes.REFUSED, "no-preset"),
    (["run", "--effort", "lots"], codes.USAGE, "bad-value"),
    (["run", "--harness", "opencode"], codes.USAGE, "bad-value"),
    (["run", "--permission-mode", "bypassPermissions"], codes.REFUSED, "off-posture"),
    (["run", "--", "--session-id", "x"], codes.REFUSED, "owned"),
    (["run", "--cwd", "/no/such/place"], codes.REFUSED, "no-cwd"),
    (["run", "--target", "headless"], codes.REFUSED, "no-brief"),
    (["run", "--target", "slot"], codes.REFUSED, "no-slots"),
    (["run", "--project", "moxi"], codes.REFUSED, "needs-module"),
    (["run", "--harness", "codex", "--", "--permissions", "p"], codes.REFUSED, "owned"),
])
def test_run_refuses_before_the_row_and_writes_nothing(work, harness, no_projects, no_slots,
                                                       argv, code, short, capsys):
    status, out, err = run(argv, capsys)
    assert status == code, err
    assert err.splitlines()[0].endswith(f"[{short}]") and err.splitlines()[1]
    assert out == "" and rows() == [] and harness.runs() == []


def test_run_into_a_named_slot_needs_slots(work, harness, no_slots, capsys):
    status, _, err = run(["run", "--slot", "2"], capsys)
    assert status == codes.REFUSED and "slot needs slots" in err
    assert rows() == []


def test_run_with_a_harness_not_on_path_exits_1(work, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(work))
    status, _, err = run(["run", "--brief", BRIEF], capsys)
    assert status == codes.ERROR and err.splitlines()[0].endswith("[not-found]")
    assert rows() == []


def test_run_with_a_presets_file_that_cant_be_read_exits_2(work, harness, capsys):
    write_presets("[seat.a\n")
    status, _, err = run(["run"], capsys)
    assert status == codes.USAGE and "[bad-config]" in err


# -- preview --------------------------------------------------------------------------------


def test_preview_prints_the_argv_and_the_stamped_environment(work, monkeypatch, capsys):
    seen = {}
    real = api.preview

    def preview(request, **kwargs):
        seen["request"] = request
        return real(request, **kwargs)
    monkeypatch.setattr(api, "preview", preview)
    code, out, _ = run(["preview", "--model", "opus", "--brief", BRIEF], capsys)
    assert code == codes.OK
    lines = out.splitlines()
    assert lines[0] == "harness claude, mode interactive, target terminal"
    assert lines[1] == "argv:" and lines[2].startswith("  claude ")
    # A word with a newline in it is quoted across lines, as a shell reads it back.
    assert "--model opus" in out and "'Brief from launcher, launched by " in lines[2]
    assert lines[lines.index("env:") + 1].startswith("  MOXI_LAUNCHER_REQUEST=")
    assert dict(seen["request"].typed) == {"model": "opus", "brief": BRIEF}
    assert rows() == []


def test_preview_json_is_one_object_with_argv_and_env(work, capsys):
    code, out, _ = run(["preview", "--harness", "codex", "--target", "headless",
                        "--scratch", "--brief", BRIEF, "--json"], capsys)
    assert code == codes.OK
    shown = json.loads(out)
    assert (shown["harness"], shown["mode"], shown["target"]) == ("codex", "exec",
                                                                  "headless")
    assert shown["argv"][:2] == ["codex", "exec"]
    assert set(shown["env"]) == {common.REQUEST}
    assert shown["first_turn"] is None and shown["values"]["scratch"] is True
    assert rows() == [] and not common.scratch_dir(shown["env"][common.REQUEST]).exists()


def test_preview_names_the_first_turn_owed_and_the_rows_not_applied(work, monkeypatch, capsys):
    monkeypatch.setattr(api, "preview", lambda request, **kw: common.Composition(
        harness="claude", mode="interactive", target="terminal", argv=("claude",),
        env={common.REQUEST: "r-1"}, values={}, first="Brief from launcher\n\nGo.",
        not_applied=("effort",), warnings=("a warning",)))
    code, out, err = run(["preview"], capsys)
    assert code == codes.OK and err == "warning: a warning\n"
    assert out.splitlines()[-5:] == ["not applied in this mode: effort",
                                     "first turn, sent once the session is ready:",
                                     "  Brief from launcher", "", "  Go."]


@pytest.mark.parametrize("argv, code, short", [
    (["preview", "--seat", "nobody"], codes.REFUSED, "no-preset"),
    (["preview", "--model"], codes.USAGE, "bad-value"),
    (["preview", "--", "--resume", "x"], codes.REFUSED, "owned"),
    (["preview", "--harness", "codex", "--target", "headless", "--brief", "b",
      "--search"], codes.REFUSED, "not-taken"),
    (["preview", "--harness", "codex", "-a", "never"], codes.REFUSED, "owned"),
])
def test_preview_refuses_with_the_launchs_code(work, argv, code, short, capsys):
    status, out, err = run(argv, capsys)
    assert status == code, err
    assert err.splitlines()[0].endswith(f"[{short}]") and out == ""


# -- log ------------------------------------------------------------------------------------


def stored(rid: str, *, state: str = "started", created: str | None = None) -> None:
    conn = store.connect()
    try:
        store.insert(conn, request_id=rid, actor="ann", harness="codex", target="headless",
                     cwd="/work", values={"mode": "exec"}, argv=["codex", "exec", "b"],
                     env={common.REQUEST: rid}, posture="", preset_kind="seat",
                     preset_name="coder", at=created)
        if state != "spawning":
            store.set_state(conn, rid, "started")
    finally:
        conn.close()


def test_log_with_no_launch_log_lists_nothing_and_makes_nothing(capsys):
    code, out, err = run(["log"], capsys)
    assert (code, out, err) == (codes.OK, "", "no launches\n")
    assert run(["log", "--json"], capsys)[:2] == (codes.OK, "")
    assert not store.path().exists()


def test_log_lists_newest_first(capsys):
    stored("r-old", created="2026-10-29T10:00:00.000000Z")
    stored("r-new", created="2026-10-30T10:00:00.000000Z")
    code, out, _ = run(["log"], capsys)
    assert code == codes.OK
    first_line, second = out.splitlines()
    assert first_line.startswith("2026-10-30T10:00:00Z  r-new  codex/headless  started")
    assert first_line.endswith("seat.coder  /work") and "r-old" in second


def test_log_marks_a_row_stale(capsys):
    stored("r-1", state="spawning", created="2026-10-01T10:00:00.000000Z")
    assert "spawning, stale" in run(["log"], capsys)[1]


def test_log_json_and_paging_go_to_the_store(monkeypatch, capsys):
    seen = {}

    def log_lines(**kwargs):
        seen.update(kwargs)
        return ['{"request_id": "r-1"}']
    monkeypatch.setattr(store, "log_lines", log_lines)
    code, out, _ = run(["log", "--json", "--limit", "3", "--before", "2026-10-30"],
                       capsys)
    assert (code, out) == (codes.OK, '{"request_id": "r-1"}\n')
    assert seen == {"limit": 3, "before": "2026-10-30"}


def test_log_json_expands_the_values(capsys):
    stored("r-1")
    code, out, _ = run(["log", "--json"], capsys)
    [line] = out.splitlines()
    assert code == codes.OK and json.loads(line)["values_json"] == {"mode": "exec"}


@pytest.mark.parametrize("argv", [["log", "--before", "yesterday"], ["log", "--limit", "0"]])
def test_log_with_a_bad_bound_exits_2(argv, capsys):
    assert run(argv, capsys)[0] == codes.USAGE


@pytest.mark.parametrize("argv", [["log"], ["log", "--json"]])
def test_log_of_a_newer_launch_log_is_refused(argv, capsys):
    store.path().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(store.path())
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    code, _, err = run(argv, capsys)
    assert code == codes.REFUSED and "[store-too-new]" in err


# -- presets --------------------------------------------------------------------------------

PRESETS = """\
[defaults]
model = "opus"

[seat.planner]
harness = "claude"
identity = "planner"
cwd = "/work"

[project.planner]
harness = "codex"
scratch = true
"""


def test_presets_list_with_no_file(capsys):
    code, out, err = run(["presets"], capsys)
    assert code == codes.OK and out == "" and "no presets in" in err


def test_presets_list_names_each_preset_its_harness_and_place(capsys):
    write_presets(PRESETS)
    code, out, _ = run(["presets", "list"], capsys)
    assert code == codes.OK
    assert [line.split() for line in out.splitlines()] == [
        ["seat.planner", "claude", "cwd", "/work"],
        ["project.planner", "codex", "scratch"]]


def test_presets_list_json(capsys):
    write_presets(PRESETS)
    shown = json.loads(run(["presets", "--json"], capsys)[1])
    assert shown["defaults"] == {"model": "opus"}
    assert [(p["kind"], p["name"], p["harness"]) for p in shown["presets"]] == [
        ("seat", "planner", "claude"), ("project", "planner", "codex")]


def test_presets_show_a_name_shows_every_preset_of_it(capsys):
    write_presets(PRESETS)
    code, out, _ = run(["presets", "show", "planner"], capsys)
    assert code == codes.OK
    assert out.split("\n\n")[0].splitlines() == [
        "[seat.planner]", 'harness = "claude"', 'identity = "planner"', 'cwd = "/work"']
    assert "[project.planner]" in out and "scratch = true" in out


def test_presets_show_one_kind(capsys):
    write_presets(PRESETS)
    code, out, _ = run(["presets", "show", "project.planner", "--json"], capsys)
    assert code == codes.OK
    assert json.loads(out) == [{"kind": "project", "name": "planner", "harness": "codex",
                                "values": {"harness": "codex", "scratch": True}}]


@pytest.mark.parametrize("name, short", [("nobody", "no-preset"),
                                         ("seat.nobody", "no-preset")])
def test_presets_show_an_unknown_name_is_refused(name, short, capsys):
    write_presets(PRESETS)
    code, _, err = run(["presets", "show", name], capsys)
    assert code == codes.REFUSED and err.splitlines()[0].endswith(f"[{short}]")


def test_presets_check_of_a_good_file(capsys):
    write_presets('[seat.planner]\nharness = "claude"\nmodel = "opus"\n')
    code, out, _ = run(["presets", "check"], capsys)
    assert code == codes.OK and out.endswith("every preset is good\n")


@pytest.mark.parametrize("text, code, short", [
    ('[seat.planner]\neffort = "lots"\n', codes.USAGE, "bad-value"),
    ('[seat.planner]\nslot = "2"\n', codes.REFUSED, "needs-module"),
])
def test_presets_check_exits_with_its_first_errors_code(text, code, short, no_slots,
                                                         capsys):
    write_presets(text)
    status, out, _ = run(["presets", "check"], capsys)
    assert status == code
    assert out.startswith("error: seat.planner:") and out.rstrip().endswith(f"[{short}]")
    shown = json.loads(run(["presets", "check", "--json"], capsys)[1])
    assert shown["ok"] is False and shown["problems"][0]["code"] == short


def test_presets_check_with_warnings_only_exits_0(monkeypatch, capsys):
    monkeypatch.setattr(presets, "check", lambda *a, **kw: [presets.Problem(
        "seat.p", "identity", "identity is ignored", "install comms", level="warning")])
    code, out, _ = run(["presets", "check"], capsys)
    assert code == codes.OK and out.startswith("warning: seat.p: identity:")


def test_presets_of_a_file_that_is_not_toml_exits_2(capsys):
    write_presets("[seat.a\n")
    for argv in (["presets"], ["presets", "check"], ["presets", "show", "a"]):
        code, _, err = run(argv, capsys)
        assert code == codes.USAGE and "[bad-config]" in err


def test_presets_project_lists_its_agents_beside_their_seats(monkeypatch, capsys):
    seen = {}

    def project_agents(key, **kwargs):
        seen["key"] = key
        return "moxi", [presets.Agent("lead", ("planner",)), presets.Agent("tester", ())]
    monkeypatch.setattr(presets, "project_agents", project_agents)
    code, out, _ = run(["presets", "--project", "Moxi"], capsys)
    assert code == codes.OK and seen == {"key": "Moxi"}
    assert [line.split(maxsplit=1) for line in out.splitlines()] == [
        ["project", "moxi"], ["lead", "seat planner"], ["tester", "no seat"]]
    shown = json.loads(run(["presets", "--project", "moxi", "--json"], capsys)[1])
    assert shown == {"project": "moxi", "agents": [{"agent": "lead", "seats": ["planner"]},
                                                   {"agent": "tester", "seats": []}]}


def test_presets_project_without_projects_is_refused(no_projects, capsys):
    code, _, err = run(["presets", "--project", "moxi"], capsys)
    assert code == codes.REFUSED and "[needs-module]" in err


def test_presets_project_reads_the_installed_projects(tmp_path, capsys):
    projects_cli = pytest.importorskip("moxi_projects.cli", reason="projects is not "
                                       "installed")
    for argv in (["add", "Moxi", "--root", str(tmp_path), "--lead", "lead"],
                 ["agents", "add", "Moxi", "tester"]):
        assert projects_cli.main(argv, prog="moxi projects") == 0
    write_presets('[seat.planner]\nidentity = "lead@moxi"\n')
    capsys.readouterr()
    shown = json.loads(run(["presets", "--project", "moxi", "--json"], capsys)[1])
    assert shown == {"project": "Moxi", "agents": [{"agent": "lead", "seats": ["planner"]},
                                                   {"agent": "tester", "seats": []}]}
    code, _, err = run(["presets", "--project", "nope"], capsys)
    assert code == codes.REFUSED and "[no-project]" in err


def test_presets_project_whose_store_is_busy_exits_75(monkeypatch, capsys):
    def busy(key, **kwargs):
        raise presets.ProjectsBusy("the store stayed locked", "try again")
    monkeypatch.setattr(presets, "project_agents", busy)
    assert run(["presets", "--project", "moxi"], capsys)[0] == codes.TEMPFAIL


@pytest.mark.parametrize("argv", [["presets", "check", "--project", "moxi"],
                                  ["presets", "show", "a", "--project", "moxi"]])
def test_presets_project_goes_with_list_only(argv, capsys):
    assert run(argv, capsys)[0] == codes.USAGE


# -- catalog --------------------------------------------------------------------------------


def test_catalog_lists_every_group_with_its_rows(capsys):
    assert cli.main(["catalog"]) == codes.OK
    out = capsys.readouterr().out
    groups = [line for line in out.splitlines() if not line.startswith(" ")]
    assert groups == ["launcher", "claude", "codex"]
    assert "--permission-mode" in out
    assert "set by the launcher" in out


def test_catalog_json_for_one_harness(capsys):
    assert cli.main(["catalog", "codex", "--json"]) == codes.OK
    data = json.loads(capsys.readouterr().out)
    assert list(data) == ["launcher", "codex"]
    rows_ = {row["name"]: row for row in data["codex"]}
    assert rows_["approval"]["modes"] == {"interactive": "-a"}
    assert rows_["cwd"]["owned"] is True and rows_["cwd"]["option"] is None
    assert rows_["sandbox"]["option"] == "--sandbox"


def test_catalog_of_an_unknown_harness_exits_2(capsys):
    assert cli.main(["catalog", "opencode"]) == codes.USAGE
    assert "no harness 'opencode'" in capsys.readouterr().err


# -- handoff dir and handoffs ---------------------------------------------------------------


def test_handoff_dir_of_a_session_with_no_key_is_refused(capsys):
    code, out, err = run(["handoff", "dir"], capsys)
    assert code == codes.REFUSED and out == "" and "[no-key]" in err


def test_handoff_dir_prints_the_folder_of_this_sessions_key(monkeypatch, capsys):
    monkeypatch.setenv("MOXI_COMMS_NAME", "planner@moxi")
    code, out, _ = run(["handoff", "dir"], capsys)
    assert code == codes.OK and out == f"{handoffs.own_folder(create=False)}\n"
    assert handoffs.own_folder(create=False).is_dir()
    shown = json.loads(run(["handoff", "dir", "--json"], capsys)[1])
    assert shown == {"key": "planner", "folder": str(handoffs.own_folder(create=False)),
                     "request_id": None}


def test_handoff_dir_asks_handoffs_for_the_current_session(monkeypatch, capsys):
    monkeypatch.setattr(handoffs, "current", lambda *a, **kw: handoffs.Current(
        "lead", Path("/notes/handoffs"), "r-1"))
    code, out, _ = run(["handoff", "dir"], capsys)
    assert (code, out) == (codes.OK, f"{Path('/notes/handoffs')}\n")


def handoff_files(*names: str) -> Path:
    folder = handoffs.own_folder()
    for name in names:
        (folder / name).write_text("done; next\n")
    return folder


def test_handoffs_lists_a_keys_files_newest_first_and_which_wait(capsys):
    folder = handoff_files("2026-10-28-lead.md", "2026-10-29-lead.md",
                           "2026-10-29-lead-2.md", "2026-10-29-other.md")
    conn = store.connect()
    store.add_pick(conn, str(folder / "2026-10-29-lead.md"), "lead", "shown")
    conn.close()
    code, out, _ = run(["handoffs", "--key", "lead"], capsys)
    assert code == codes.OK
    assert [line.split() for line in out.splitlines()] == [
        ["2026-10-29", "waiting", str(folder / "2026-10-29-lead-2.md")],
        ["2026-10-29", "picked", str(folder / "2026-10-29-lead.md")],
        ["2026-10-28", "picked", str(folder / "2026-10-28-lead.md")]]
    shown = json.loads(run(["handoffs", "--key", "lead", "--json"], capsys)[1])
    assert [h["waiting"] for h in shown["handoffs"]] == [True, False, False]


def test_handoffs_with_no_launch_log_count_every_file_waiting(capsys):
    handoff_files("2026-10-29-lead.md")
    out = run(["handoffs", "--key", "lead"], capsys)[1]
    assert out.split()[:2] == ["2026-10-29", "waiting"]
    assert not store.path().exists()


def test_handoffs_of_this_session_by_its_comms_name(monkeypatch, capsys):
    handoff_files("2026-10-29-planner.md")
    monkeypatch.setenv("MOXI_COMMS_NAME", "planner")
    code, out, _ = run(["handoffs"], capsys)
    assert code == codes.OK and "2026-10-29-planner.md" in out


def test_handoffs_with_no_key_is_refused(capsys):
    assert run(["handoffs"], capsys)[0] == codes.REFUSED


def test_handoffs_with_a_key_that_cant_name_a_file_exits_2(capsys):
    assert run(["handoffs", "--key", "../up"], capsys)[0] == codes.USAGE


# -- deliver --------------------------------------------------------------------------------


def test_deliver_calls_the_sender_with_its_pid(monkeypatch, capsys):
    seen = {}

    def deliver(request_id, *, pid=None, out=None, turns=None):
        seen.update(request_id=request_id, pid=pid)
        return 75
    monkeypatch.setattr(first, "deliver", deliver)
    assert cli.main(["deliver", "r-1", "--pid", "4242"]) == 75
    assert seen == {"request_id": "r-1", "pid": 4242}


def test_deliver_of_an_unknown_launch_exits_3(capsys):
    code, out, _ = run(["deliver", "r-1", "--pid", "1"], capsys)
    assert code == codes.REFUSED and out == "no launch r-1 in the launch log\n"


def test_deliver_into_a_newer_launch_log_is_refused(monkeypatch, capsys):
    def too_new(*args, **kwargs):
        raise core_store.StoreTooNew("launcher.db", 9, 1)
    monkeypatch.setattr(store, "connect", too_new)
    code, _, err = run(["deliver", "r-1", "--pid", "1"], capsys)
    assert code == codes.REFUSED and "[store-too-new]" in err


def test_moxi_launcher_runs_the_deliverer_through_the_cores_command(tmp_path):
    """`moxi launcher deliver`, the command the detached deliverer runs, reaches this
    CLI through the core's dispatch."""
    moxi = shutil.which("moxi", path=str(Path(sys.executable).parent))
    if moxi is None:
        pytest.skip("this environment has no moxi script")
    done = subprocess.run([moxi, "launcher", "deliver", "r-1", "--pid", "1"],
                          capture_output=True, text=True, timeout=60, env=dict(os.environ),
                          cwd=str(tmp_path))
    assert done.returncode == codes.REFUSED, done.stderr
    assert done.stdout == "no launch r-1 in the launch log\n"


def test_the_deliverer_argv_is_a_command_this_cli_parses():
    argv = first.deliverer_argv("r-1", 4242)
    args = cli.parse(argv[2:])
    assert (args.verb, args.request_id, args.pid) == ("deliver", "r-1", 4242)
