"""Gathering a launch's context, `build`, and `preview`: for one request, its ids given,
`preview` shows the argv the launch hands its target."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import modules
from moxi.paths import Layout
from moxi_dev.testing import wait_for

import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness, run_child
from moxi_launcher import api, compose, first, handoffs, presets
from moxi_launcher.compose import Additions, common

SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"


@dataclass(frozen=True)
class Request:
    request_id: str | None = "r1"
    session_id: str | None = SID
    actor: str | None = "ann"


@dataclass(frozen=True)
class Resolved:
    values: dict
    chosen: frozenset = frozenset()
    preset_kind: str | None = None
    preset_name: str | None = None
    project_key: str | None = None


@dataclass
class FakeTurns:
    """turns' API as the launcher calls it: the launch additions, counted."""

    calls: list = field(default_factory=list)

    def launch_additions(self, harness, *, cwd, env, session_id=None, commands=()):
        self.calls.append({"harness": harness, "cwd": cwd, "env": env,
                           "session_id": session_id, "commands": commands})
        return SimpleNamespace(harness=harness,
                               argv=["--plugin-dir", "/p/runner", "--plugin-dir", "/p/gate"],
                               env={"MOXI_TURNS_COMMANDS": ",".join(commands)},
                               options={}, notes=["mcp: skipped, no renderer"])


@pytest.fixture
def no_turns(monkeypatch):
    """The installed turns hidden: a launch that wants turns fakes it on the seam."""
    fake_turns.hide(monkeypatch)


@pytest.fixture
def turns(monkeypatch) -> FakeTurns:
    fake = FakeTurns()
    real = modules.load
    real_available = modules.available

    def load(name, *, interface=None):
        if name == "turns" and interface == compose.TURNS_INTERFACE:
            return SimpleNamespace(state=modules.PRESENT, ok=True, api=fake, reason="")
        return real(name, interface=interface)

    monkeypatch.setattr(modules, "load", load)
    monkeypatch.setattr(modules, "available", lambda name: True if name == "turns"
                        else real_available(name))
    return fake


# -- the context ----------------------------------------------------------------------------


def test_the_installed_links_count_turns_only_at_the_interface_the_launcher_calls():
    found = compose.installed()
    assert found <= set(compose.LINKS)
    turns = modules.load("turns", interface=compose.TURNS_INTERFACE)
    assert ("turns" in found) == (turns.state == modules.PRESENT)
    assert ("comms" in found) == modules.available("comms")


def test_the_context_reads_the_files_a_launch_names(tmp_path):
    (tmp_path / "brief.md").write_text("the brief")
    (tmp_path / "seat.md").write_text("the seat")
    (tmp_path / "h.md").write_text("the handoff")
    values = {"harness": "claude", "cwd": str(tmp_path),
              "brief_file": str(tmp_path / "brief.md"),
              "instructions_file": str(tmp_path / "seat.md"),
              "handoff": str(tmp_path / "h.md")}
    got = compose.context(values, request_id="r1", session_id=SID, actor="ann",
                          modules=frozenset())
    assert (got.brief, got.instructions, got.handoff_text) == (
        "the brief", "the seat", "the handoff")
    inline = compose.context({**values, "brief": "inline", "instructions": "inline seat"},
                             request_id="r1", modules=frozenset())
    assert (inline.brief, inline.instructions) == ("inline", "inline seat")


def test_a_file_the_launch_names_that_cant_be_read_is_refused(tmp_path):
    with pytest.raises(common.Unreadable, match="brief_file"):
        compose.context({"harness": "claude", "cwd": str(tmp_path),
                         "brief_file": str(tmp_path / "missing.md")},
                        request_id="r1", modules=frozenset())


def test_the_context_names_the_handoff_folder_only_with_a_key(tmp_path):
    values = {"harness": "claude", "cwd": str(tmp_path)}
    none = compose.context(values, request_id="r1", modules=frozenset())
    assert none.handoff_dir is None
    seat = compose.context(values, request_id="r1", modules=frozenset(),
                           preset_kind="seat", preset_name="lead")
    assert seat.handoff_dir == str(handoffs.own_folder(create=False))
    # An identity counts as the key only with comms.
    ignored = compose.context({**values, "identity": "planner"}, request_id="r1",
                              modules=frozenset())
    assert ignored.handoff_dir is None


def test_preview_leaves_the_handoff_folder_alone_and_a_launch_makes_it(tmp_path):
    values = {"harness": "claude", "cwd": str(tmp_path)}
    folder = handoffs.own_folder(create=False)
    compose.context(values, request_id="r1", modules=frozenset(), project_key="moxi")
    assert not folder.exists()
    compose.context(values, request_id="r1", modules=frozenset(), project_key="moxi",
                    create=True)
    assert folder.is_dir()


def test_the_prompt_file_goes_in_the_runs_store():
    got = compose.context({"harness": "claude", "cwd": "/work"}, request_id="r1",
                          modules=frozenset())
    assert got.runs_dir == str(Layout("launcher").state_dir() / "runs")


def test_claude_without_comms_asks_turns_for_its_additions_once(turns, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    values = {"harness": "claude", "cwd": "/work"}
    got = compose.context(values, request_id="r1", session_id=SID,
                          modules=frozenset({"turns"}))
    assert got.additions == Additions(
        argv=("--plugin-dir", "/p/runner", "--plugin-dir", "/p/gate"),
        env={"MOXI_TURNS_COMMANDS": "exit"}, notes=("mcp: skipped, no renderer",))
    [call] = turns.calls
    assert call["harness"] == "claude" and call["session_id"] == SID
    assert call["commands"] == ("exit",) and call["cwd"] == "/work"
    # The environment it is given is the launch's as composed so far.
    assert call["env"]["MOXI_LAUNCHER_REQUEST"] == "r1" and "CLAUDECODE" not in call["env"]


@pytest.mark.parametrize("values, links", [
    ({"harness": "claude", "cwd": "/work", "identity": "p"}, {"turns", "comms"}),
    ({"harness": "codex", "cwd": "/work"}, {"turns"}),
    ({"harness": "claude", "cwd": "/work"}, set()),
])
def test_no_one_else_asks_turns_for_additions(turns, values, links):
    got = compose.context(values, request_id="r1", session_id=SID,
                          modules=frozenset(links))
    assert got.additions is None and turns.calls == []


class FakeTurnsError(Exception):
    """turns' own error, as the fake API carries it."""


@pytest.mark.parametrize("error", [FakeTurnsError("the endpoint file can't be written"),
                                   ValueError("unknown command 'exit'")])
def test_a_failure_to_give_the_additions_is_a_turns_additions_refusal(
        turns, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(turns, "launch_additions", fail)
    monkeypatch.setattr(turns, "TurnsError", FakeTurnsError, raising=False)
    with pytest.raises(common.TurnsAdditions) as raised:
        compose.context({"harness": "claude", "cwd": "/work"}, request_id="r1",
                        session_id=SID, modules=frozenset({"turns"}))
    assert raised.value.code == "turns-additions" and raised.value.__cause__ is error
    assert str(error) in raised.value.problem


def test_another_error_from_the_additions_call_is_not_turned_into_a_refusal(turns, monkeypatch):
    def fail(*args, **kwargs):
        raise KeyError("a bug, not a refusal")

    monkeypatch.setattr(turns, "launch_additions", fail)
    with pytest.raises(KeyError):
        compose.context({"harness": "claude", "cwd": "/work"}, request_id="r1",
                        session_id=SID, modules=frozenset({"turns"}))


def test_a_codex_context_finds_the_host_and_a_claude_one_does_not(monkeypatch):
    monkeypatch.setattr(compose, "host_script", lambda: "/env/bin/moxi-turns-codex-host")
    codex = compose.context({"harness": "codex", "cwd": "/work"}, request_id="r1",
                            modules=frozenset({"turns"}))
    assert codex.host == "/env/bin/moxi-turns-codex-host"
    assert compose.context({"harness": "claude", "cwd": "/work"}, request_id="r1",
                           modules=frozenset()).host is None


def test_the_context_says_whether_the_agent_role_resolved(monkeypatch):
    monkeypatch.setattr(presets, "roles", lambda harness, cwd=None, env=None:
                        frozenset({"reviewer"}))
    values = {"harness": "claude", "cwd": "/work"}
    assert compose.context(values, request_id="r1", modules=frozenset()).role_found is None
    found = compose.context({**values, "agent": "reviewer"}, request_id="r1",
                            modules=frozenset())
    missing = compose.context({**values, "agent": "planner"}, request_id="r1",
                              modules=frozenset())
    assert (found.role_found, missing.role_found) == (True, False)


def test_the_host_script_is_looked_for_in_this_environment():
    try:
        expected = str(compose.paths.script(compose.HOST))
    except FileNotFoundError:
        expected = None
    found = modules.load("turns", interface=compose.TURNS_INTERFACE)
    if not found.ok or not found.api.codex_host_available():
        expected = None
    assert compose.host_script() == expected


# -- build and preview ----------------------------------------------------------------------


def test_build_mints_the_ids_a_request_leaves_out():
    got = compose.build(Request(request_id=None, session_id=None, actor="ann"),
                        Resolved({"harness": "claude", "cwd": "/work"}),
                        modules=frozenset())
    assert got.env["MOXI_LAUNCHER_REQUEST"]
    assert got.argv[got.argv.index("--session-id") + 1]


def test_build_passes_the_resolved_choices_and_preset_on(tmp_path):
    resolved = Resolved({"harness": "codex", "cwd": "/work", "target": "headless",
                         "approval": "never", "brief": "b"},
                        chosen=frozenset({"approval"}))
    with pytest.raises(Exception, match="approval is not taken by codex exec"):
        compose.build(Request(), resolved, modules=frozenset())
    seat = Resolved({"harness": "claude", "cwd": str(tmp_path)}, preset_kind="seat",
                    preset_name="lead", project_key="moxi")
    got = compose.build(Request(), seat, modules=frozenset())
    assert "project key:   moxi" in got.prompt_file.text
    assert "handoff dir:" in got.prompt_file.text


def test_preview_composes_what_resolve_returns(monkeypatch):
    # A stand-in for the presets resolver, which comes with the presets module.
    seen = []

    def resolve(request, environ=None):
        seen.append(request)
        return Resolved({"harness": "claude", "cwd": "/work", "model": "opus"},
                        chosen=frozenset({"model"}))

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(compose, "installed", lambda: frozenset())
    got = api.preview(Request())
    assert seen == [Request()]
    assert list(got.argv) == [
        "claude", "--session-id", SID, "--append-system-prompt-file",
        str(Layout("launcher").state_dir() / "runs" / "r1.prompt.md"),
        "--model", "opus", "--permission-mode", "default"]
    assert got.target == "terminal" and got.mode == "interactive"
    # It starts nothing and writes nothing of the launcher's.
    assert not (Layout("launcher").state_dir() / "runs").exists()


CONTRACT_REQUESTS = [
    {"harness": "claude", "cwd": "/work", "model": "opus", "brief": "b"},
    {"harness": "claude", "cwd": "/work", "target": "headless", "brief": "b"},
    {"harness": "codex", "cwd": "/work", "target": "headless", "brief": "b"},
    {"harness": "codex", "cwd": "/work", "trust_cwd": True},
]


@pytest.mark.parametrize("values", CONTRACT_REQUESTS)
def test_preview_shows_the_argv_build_composes_for_the_launch(monkeypatch, values):
    monkeypatch.setattr(api, "_resolve", lambda request, environ=None: Resolved(dict(values)))
    monkeypatch.setattr(compose, "installed", lambda: frozenset())
    shown = api.preview(Request())
    composed = compose.build(Request(), Resolved(dict(values)), create=True)
    assert shown.argv == composed.argv and shown.env == composed.env
    assert shown.values == composed.values


#: Launches for the contract: each target this launcher starts, each harness, with and
#: without turns, and the comms wrapper.
LAUNCHES = [
    ("claude in this terminal", {"brief": "b", "model": "opus"}, False),
    ("claude in this terminal, with turns", {"brief": "b", "effort": "high"}, True),
    ("claude headless", {"target": "headless", "brief": "b"}, True),
    ("codex in this terminal", {"harness": "codex", "brief": "b", "search": True}, False),
    ("codex headless, scratch", {"harness": "codex", "target": "headless", "brief": "b",
                                 "scratch": True}, False),
    ("claude under comms", {"identity": "planner", "brief": "b"}, True),
    ("claude in a slot", {"target": "slot", "brief": "b"}, False),
    ("claude in a slot, with turns", {"target": "slot", "brief": "b", "slot": "2"}, True),
    ("codex in a slot", {"harness": "codex", "target": "slot", "brief": "b"}, False),
]


@pytest.mark.skipif(sys.platform == "win32",
                    reason="the fake harness is a script run by its #! line")
@pytest.mark.parametrize("name, typed, with_turns", LAUNCHES, ids=[c[0] for c in LAUNCHES])
def test_preview_argv_is_the_argv_launch_hands_the_target(tmp_path, monkeypatch, no_turns,
                                                          name, typed, with_turns):
    """For each target, with a fake harness on PATH, turns and slots faked on their seams
    (`MOXI_LAUNCHER_TURNS`, `MOXI_LAUNCHER_SLOTS`; the fake slots runs the command it is
    handed) and comms' wrapper stood in for, the argv the launch hands
    the target equals `preview(request).argv` for the same request, its request id and
    session id given. The launch runs in a process of its own, as `moxi launcher run`
    does, since a launch in this terminal becomes the harness."""
    if "identity" in typed and not modules.available("comms"):
        pytest.skip("comms is not installed")
    rig = Harness.on_path(tmp_path, monkeypatch)
    if with_turns:
        fake = tmp_path / "fake-turns"
        fake.mkdir()
        monkeypatch.setenv(first.TURNS_SEAM, "launcher_fake_turns")
        monkeypatch.setenv("LAUNCHER_FAKE_TURNS", str(fake))
    if typed.get("target") == "slot":
        slots = tmp_path / "fake-slots"
        slots.mkdir()
        (slots / "script.json").write_text(json.dumps(
            {"run": True, "slots": {"2": {"state": "free", "launchable": True}}}))
        monkeypatch.setenv("MOXI_LAUNCHER_SLOTS", "launcher_fake_slots")
        monkeypatch.setenv("LAUNCHER_FAKE_SLOTS", str(slots))
    work = tmp_path / "work"
    work.mkdir()
    sid = None if typed.get("harness") == "codex" else SID
    req = {"typed": typed, "cwd": str(work), "request_id": "r1", "session_id": sid,
           "actor": "ann"}
    code, seen = run_child(tmp_path, req, moxi="moxi-comms-fake")
    assert code == 0, seen["stderr"]
    run = rig.one()
    handed = [Path(run["argv"][0]).name, *run["argv"][1:]]
    assert handed == seen["preview"]
    assert run["env"]["MOXI_LAUNCHER_REQUEST"] == "r1"
    if with_turns and typed.get("target") not in ("headless", "slot"):
        # The detached deliverer sends the first turn; let it finish inside the test.
        log = first.run_log("r1")
        wait_for(lambda: log.exists() and log.read_text().strip(), timeout=60)


def test_a_scratch_launch_works_in_a_folder_named_by_its_request_id(tmp_path):
    import tempfile

    assert common.scratch_dir("r1") == (
        Path(tempfile.gettempdir()) / "moxi-scratch" / "r1")
    resolved = Resolved({"harness": "codex", "cwd": "", "scratch": True,
                         "target": "headless", "brief": "b"})
    got = compose.build(Request(), resolved, modules=frozenset())
    where = str(common.scratch_dir("r1"))
    assert list(got.argv[3:7]) == ["-c", "check_for_update_on_startup=false", "-C", where]
    assert "--skip-git-repo-check" in got.argv and got.values["cwd"] == where
    # Only the path: the launch makes the folder.
    assert not common.scratch_dir("r1").exists()


def test_build_takes_the_resolvers_mode(monkeypatch):
    monkeypatch.setattr(compose, "host_script", lambda: "/env/bin/moxi-turns-codex-host")
    resolved = SimpleNamespace(values={"harness": "codex", "cwd": "/work"}, mode="host",
                               chosen=frozenset(), off_posture=True)
    got = compose.build(Request(session_id=None), resolved, modules=frozenset({"turns"}))
    assert got.mode == "host" and got.argv[0] == "/env/bin/moxi-turns-codex-host"
    assert got.values["off_posture"] is True and got.values["mode"] == "host"


# -- preview wired to the presets resolver ---------------------------------------------------


def write_presets(text: str) -> None:
    file = presets.path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)


def test_preview_resolves_through_presets(tmp_path, monkeypatch):
    monkeypatch.setattr(compose, "installed", lambda: frozenset())
    write_presets(f"""
[defaults]
permission_mode = "acceptEdits"

[seat.lead]
harness = "claude"
cwd = "{tmp_path}"
model = "opus"
add_dir = ["{tmp_path}"]
""")
    request = presets.Request(seat="lead", typed={"effort": "high"})
    request = SimpleNamespace(**vars(request), request_id="r1", session_id=SID, actor="ann")
    got = api.preview(request)
    prompt_file = str(Layout("launcher").state_dir() / "runs" / "r1.prompt.md")
    assert list(got.argv) == [
        "claude", "--session-id", SID, "--append-system-prompt-file", prompt_file,
        "--model", "opus", "--effort", "high", "--add-dir", str(tmp_path),
        "--permission-mode", "acceptEdits"]
    assert "handoff dir:" in got.prompt_file.text      # the seat's name is its key
    assert got.values["mode"] == "interactive" and got.values["off_posture"] is False
    resolved = presets.resolve(request)
    assert got.argv == compose.build(request, resolved, create=True).argv


def test_preview_of_a_scratch_codex_launch_names_the_scratch_folder():
    request = SimpleNamespace(**vars(presets.Request(
        typed={"harness": "codex", "scratch": True, "target": "headless", "brief": "b"})),
        request_id="r9", session_id=None, actor="ann")
    got = api.preview(request)
    assert str(common.scratch_dir("r9")) in got.argv
    assert not common.scratch_dir("r9").exists()


def test_preview_gives_the_resolvers_warnings_as_launch_does(tmp_path, monkeypatch,
                                                            no_turns):
    """An identity without comms is dropped with a warning from the resolver; `preview`
    carries it, as `launch` does, so the CLI shows it before anything runs."""
    real = modules.available
    monkeypatch.setattr(modules, "available", lambda name: name != "comms" and real(name))
    rig = Harness.on_path(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    req = api.LaunchRequest(typed={"identity": "planner", "target": "headless",
                                   "brief": "b"}, cwd=str(work), request_id="r1",
                            session_id=SID, actor="ann")
    line = "identity planner is ignored: comms is not installed"
    shown = api.preview(req)
    assert line in shown.warnings
    monkeypatch.setattr("moxi_launcher.targets._spawn", lambda argv, **kw: 99)
    launched = api.launch(req)
    assert shown.warnings == launched.warnings
    assert rig.runs() == []
