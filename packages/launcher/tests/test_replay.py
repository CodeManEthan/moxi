"""A stored launch made again (`api.ReplayRequest`): the replay's argv against a fresh
launch of the same values, the environment built from the row and the settings only, a
scratch launch's directory kept, the brief left out, a self relaunch's handoff header and
stamp, a director's posture, a resume's argv and session id, and a respawn into the slot
the relaunch holds."""

from __future__ import annotations

import dataclasses
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi.paths import Layout

import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import api, catalog, first, slots, store, targets
from moxi_launcher.api import LaunchRequest, ReplayRequest
from moxi_launcher.compose import Context, claude, codex, common

TESTS = Path(__file__).resolve().parent
SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
SID2 = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"
BRIEF = "Read the design and start the build."


@pytest.fixture(autouse=True)
def no_turns(monkeypatch):
    fake_turns.hide(monkeypatch)


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


@pytest.fixture
def work(tmp_path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def no_exec(monkeypatch):
    """exec stood in for: records what it was handed."""
    seen = []

    def execve(command, argv, env):
        seen.append({"command": command, "argv": argv, "env": env, "cwd": os.getcwd()})

    monkeypatch.setattr(targets, "_execve", execve)
    here = os.getcwd()
    yield seen
    os.chdir(here)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    directory = tmp_path / "fake-slots"
    directory.mkdir()
    monkeypatch.setenv(slots.SEAM, "launcher_fake_slots")
    monkeypatch.setenv(fake.DIR, str(directory))
    monkeypatch.syspath_prepend(str(TESTS))
    fake.reset()
    script = directory / "script.json"

    def plan(**keys):
        now = json.loads(script.read_text()) if script.exists() else {}
        script.write_text(json.dumps({**now, **keys}))

    return SimpleNamespace(dir=directory, plan=plan,
                           calls=lambda name=None: fake.calls(directory, name))


def request(work: Path, *, rid: str = "r1", sid: str | None = SID,
            **typed) -> LaunchRequest:
    return LaunchRequest(typed=typed, cwd=str(work), request_id=rid, session_id=sid,
                         actor="ann")


def row(rid: str = "r1") -> store.LaunchRow:
    conn = store.connect(create=False)
    try:
        found = store.get(conn, rid)
    finally:
        conn.close()
    assert found is not None
    return found


def ended(rid: str = "r1") -> None:
    """The launch's session has ended: its recorded process (this one, as a terminal launch
    records it) reads as not running, so a resume of it may launch."""
    conn = store.connect(create=False)
    try:
        conn.execute("UPDATE launches SET pid_started = 1.0 WHERE request_id = ?", (rid,))
    finally:
        conn.close()


def settings_file(text: str) -> None:
    path = Layout("launcher").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def without_ids(argv, *ids) -> list[str]:
    """`argv` with each id, and the system prompt file named by one, put as `<id>`.

    An id is replaced only where it stands alone, with no letter, digit or underscore on
    either side, so a short id such as `r3` is not found inside a random directory name
    like `mx-znbhhrr3` or `mx-r3_abcde`."""
    patterns = [re.compile(rf"(?<![A-Za-z0-9_]){re.escape(given)}(?![A-Za-z0-9_])")
                for given in ids]
    out = []
    for word in argv:
        for pattern in patterns:
            word = pattern.sub("<id>", word)
        out.append(word)
    return out


# -- the composers ----------------------------------------------------------------------


def ctx(**fields) -> Context:
    base = {"request_id": "r2", "session_id": SID, "actor": "ann", "runs_dir": "/state/runs",
            "moxi": "moxi"}
    return Context(**{**base, **fields})


def test_a_claude_resume_passes_resume_where_the_session_id_goes():
    got = claude.compose({"harness": "claude", "cwd": "/work", "model": "opus"},
                         ctx(resume=True))
    assert got.argv[:5] == ("claude", "--resume", SID, "--append-system-prompt-file",
                            common.prompt_path("/state/runs", "r2"))
    assert "--session-id" not in got.argv
    # The recorded values keep no owned row: a later replay resumes by its own id.
    assert "resume" not in got.values and "session_id" not in got.values


def test_a_claude_resume_with_comms_names_the_same_id_to_the_wrapper():
    got = claude.compose({"harness": "claude", "cwd": "/work", "identity": "planner"},
                         ctx(resume=True, modules=frozenset({"comms"})))
    argv = list(got.argv)
    tail = argv[argv.index("--") + 1:]
    assert argv[argv.index("--session-id") + 1] == SID
    assert tail[:2] == ["--resume", SID] and "--session-id" not in tail


def test_a_codex_resume_under_the_host_passes_the_thread_id():
    got = codex.compose({"harness": "codex", "cwd": "/work"},
                        ctx(session_id="thread-7", resume=True, mode="host",
                            host="/bin/moxi-turns-codex-host",
                            modules=frozenset({"turns"})))
    assert got.argv[-2:] == ("--resume", "thread-7")


@pytest.mark.parametrize("fields, words", [
    ({"mode": "interactive"}, "only turns' Codex host resumes"),
    ({"mode": "host", "host": "/bin/h", "modules": frozenset({"turns", "comms"}),
      "comms_codex": True}, "can't resume a thread"),
])
def test_a_codex_resume_the_host_cant_make_is_refused(fields, words):
    values = {"harness": "codex", "cwd": "/work"}
    if "comms" in fields.get("modules", ()):
        values["identity"] = "planner"
    with pytest.raises(catalog.Refused, match=words):
        codex.compose(values, ctx(session_id="thread-7", resume=True, **fields))


def test_the_posture_a_replay_gives_shows_in_the_block_and_not_in_the_values():
    got = claude.compose({"harness": "claude", "cwd": "/work", "posture": "fresh"},
                         ctx(posture="relaunched (handoff: /h/one.md)"))
    assert "start posture: relaunched (handoff: /h/one.md)" in got.prompt_file.text
    assert got.values["posture"] == "fresh"


def test_a_relaunch_header_and_its_stamp():
    got = claude.compose(
        {"harness": "claude", "cwd": "/work", "handoff": "/h/one.md"},
        ctx(handoff_text="where it stands", relaunched=True,
            extra_stamps={common.RELAUNCH: "run1"}))
    assert got.prompt == "Handoff from launcher, relaunched by ann: /h/one.md\n\n" \
                         "where it stands"
    assert got.env[common.RELAUNCH] == "run1"
    assert first.on_argv(got.argv)


def test_the_first_turn_text_of_a_relaunch(tmp_path):
    handoff = tmp_path / "h.md"
    handoff.write_text("state\n")
    assert first.text(actor="ann", handoff=handoff, relaunched=True) == \
        f"Handoff from launcher, relaunched by ann: {handoff}\n\nstate"
    assert first.text(actor="ann", handoff=handoff).startswith(
        "Handoff from launcher, launched by ann: ")


# -- the environment ----------------------------------------------------------------------


def test_every_keep_list_name_is_classed_recorded_or_credential():
    # A name part 1's scrub keeps must be classed before it ships: unclassed, a replay
    # would neither record it nor know it as a credential.
    keep = common.CLAUDE_KEEP | common.CODEX_KEEP
    unclassed = sorted(name for name in keep if common.env_class(name) is None)
    assert unclassed == []
    assert set(common.ENV_CLASS.values()) == {common.RECORDED, common.CREDENTIAL}


#: Words that make a name a credential by the class rule: its value can grant access or
#: route traffic. A name that ends in a file or a path holds where the secret is, not it.
CREDENTIAL_WORDS = ("TOKEN", "API_KEY", "SECRET", "PASSWORD", "PASSPHRASE", "ACCESS_KEY",
                    "BASE_URL", "PROXY", "ENDPOINT")


@pytest.mark.parametrize("name", sorted(common.ENV_CLASS))
def test_a_name_whose_value_can_grant_access_is_a_credential(name):
    looks = (any(word in name for word in CREDENTIAL_WORDS)
             and not name.endswith(("_FILE", "_TTL_MS", "_REFRESH_LOCK", "_CACHE",
                                       "_RESOLVES_HOSTS")))
    if looks:
        assert common.env_class(name) == common.CREDENTIAL, name


@pytest.mark.parametrize("name", ["CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
                                  "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE", "CODEX_API_KEY",
                                  "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                                  "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                                  "AWS_SECRET_ACCESS_KEY", "AWS_BEARER_TOKEN_BEDROCK"])
def test_the_credentials(name):
    assert common.env_class(name) == common.CREDENTIAL


@pytest.mark.parametrize("name", ["CLAUDE_CONFIG_DIR", "CLAUDE_CODE_USE_BEDROCK",
                                  "CODEX_HOME", "AWS_REGION", "AWS_PROFILE",
                                  "ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION",
                                  "VERTEX_REGION_CLAUDE_SONNET", "OPENAI_IDENTITY_TOKEN_FILE"])
def test_recorded_names(name):
    assert common.env_class(name) == common.RECORDED


@pytest.mark.parametrize("name", ["MOXI_HOME", "MOXI_DEVICE", "MOXI_LAUNCHER_HOME",
                                  "MOXI_COMMS_STATE_HOME", "MOXI_SLOTS_RUNTIME_DIR",
                                  "MOXI_TURNS_DEVICE", "MOXI_CACHE_CONFIG_HOME"])
def test_the_cores_locators(name):
    assert common.locator(name)


@pytest.mark.parametrize("name", ["MOXI_HOMEX", "MOXI_LAUNCHER_REQUEST", "MOXI_COMMS_NAME",
                                  "FOO_HOME", "MOXI_TURNS_ENDPOINT"])
def test_names_that_are_not_locators(name):
    assert not common.locator(name)


def test_the_recorded_base_holds_each_recorded_name_set_and_no_credential():
    child = {"HOME": "/h", "PATH": "/bin", "LC_ALL": "C", "XDG_STATE_HOME": "/s",
             "MOXI_HOME": "/m", "MOXI_COMMS_STATE_HOME": "/cs", "CLAUDE_CONFIG_DIR": "/c",
             "CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "eu-west-1", "CODEX_HOME": "/x",
             "CLAUDE_CODE_OAUTH_TOKEN": "tok", "AWS_SECRET_ACCESS_KEY": "sec",
             "OPENAI_API_KEY": "key", "ARMED": "1", "TERM": "xterm", "TMPDIR": "/t"}
    base = common.base_env(child, windows=False)
    assert base == {"HOME": "/h", "PATH": "/bin", "LC_ALL": "C", "XDG_STATE_HOME": "/s",
                    "MOXI_HOME": "/m", "MOXI_COMMS_STATE_HOME": "/cs",
                    "CLAUDE_CONFIG_DIR": "/c", "CLAUDE_CODE_USE_BEDROCK": "1",
                    "AWS_REGION": "eu-west-1", "CODEX_HOME": "/x", "TMPDIR": "/t",
                    # A credential set at launch is marked, never its value.
                    "CLAUDE_CODE_OAUTH_TOKEN": None, "AWS_SECRET_ACCESS_KEY": None,
                    "OPENAI_API_KEY": None}
    assert not {"tok", "sec", "key"} & set(base.values())


WINDOWS_CHILD = {
    "SystemRoot": "C:\\Windows", "windir": "C:\\Windows", "ComSpec": "C:\\Windows\\cmd.exe",
    "PATHEXT": ".COM;.EXE;.BAT", "USERPROFILE": "C:\\Users\\ann",
    "APPDATA": "C:\\Users\\ann\\AppData\\Roaming",
    "LOCALAPPDATA": "C:\\Users\\ann\\AppData\\Local", "TEMP": "C:\\Temp", "TMP": "C:\\Temp",
    "HOMEDRIVE": "C:", "HOMEPATH": "\\Users\\ann", "ProgramData": "C:\\ProgramData",
    "Path": "C:\\Windows\\system32", "SYSTEMDRIVE": "C:",
    "PROGRAMFILES": "C:\\Program Files", "ProgramFiles(x86)": "C:\\Program Files (x86)",
    "CommonProgramFiles": "C:\\Program Files\\Common Files", "USERNAME": "ann",
    "ALLUSERSPROFILE": "C:\\ProgramData", "PUBLIC": "C:\\Users\\Public",
    "NUMBER_OF_PROCESSORS": "8", "PROCESSOR_ARCHITECTURE": "AMD64", "OS": "Windows_NT",
    "COMPUTERNAME": "BOX", "API_KEY": "k", "CLAUDE_CODE_OAUTH_TOKEN": "secret",
    "MOXI_LAUNCHER_REQUEST": "r", "SESSIONNAME": "Console",
}


def test_on_windows_the_base_keeps_the_system_names_a_process_needs_to_start():
    base = common.base_env(WINDOWS_CHILD, windows=True)
    assert {k for k, v in base.items() if v is not None} == set(WINDOWS_CHILD) - {
        "API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "MOXI_LAUNCHER_REQUEST", "SESSIONNAME"}
    assert base["CLAUDE_CODE_OAUTH_TOKEN"] is None and "API_KEY" not in base
    # Recorded as the child's own spelling, which Windows reads without case.
    assert base["SystemRoot"] == "C:\\Windows" and base["Path"] == "C:\\Windows\\system32"


def test_a_row_with_no_base_takes_the_short_list_and_tmpdir_from_the_job():
    job = {"HOME": "/h", "PATH": "/bin", "TMPDIR": "/t", "LC_ALL": "C",
           "CLAUDE_CONFIG_DIR": "/c", "SYSTEMROOT": "C:\\Windows", "ARMED": "1"}
    assert common.short_env(job, windows=False) == {
        "HOME": "/h", "PATH": "/bin", "TMPDIR": "/t", "LC_ALL": "C"}


def test_on_windows_a_row_with_no_base_takes_the_start_names_from_the_job():
    short = common.short_env(WINDOWS_CHILD, windows=True)
    assert short["SystemRoot"] == "C:\\Windows" and short["ComSpec"].endswith("cmd.exe")
    assert short["Path"] == "C:\\Windows\\system32" and short["TEMP"] == "C:\\Temp"
    assert not {"API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "MOXI_LAUNCHER_REQUEST",
                "SESSIONNAME"} & set(short)


def test_elsewhere_the_base_leaves_the_windows_names_out():
    base = common.base_env({**WINDOWS_CHILD, "HOME": "/h", "PATH": "/bin"}, windows=False)
    assert base == {"HOME": "/h", "PATH": "/bin", "CLAUDE_CODE_OAUTH_TOKEN": None}


def test_a_replay_env_is_the_base_then_the_pass_list_and_nothing_else():
    caller = {"PATH": "/now/bin", "HOME": "/home/now", "MOXI_HOME": "/m", "TOKEN": "t",
              "ARMED": "1", "CLAUDE_CONFIG_DIR": "/c", "CLAUDECODE": "1", "TERM": "xterm"}
    base = {"PATH": "/then/bin", "HOME": "/home/then", "CLAUDE_CODE_OAUTH_TOKEN": None}
    got = common.replay_env(base, caller, ["TOKEN", "GONE"])
    assert got == {"PATH": "/then/bin", "HOME": "/home/then", "TOKEN": "t"}


def test_no_credentials_when_the_base_shows_a_sign_in_and_nothing_is_passed():
    assert common.no_credentials({"CLAUDE_CODE_OAUTH_TOKEN": None}, [])
    assert common.no_credentials({"CLAUDE_CODE_USE_VERTEX": "1"}, [])
    assert not common.no_credentials({"CLAUDE_CODE_USE_VERTEX": "0"}, [])
    assert not common.no_credentials({"CLAUDE_CODE_OAUTH_TOKEN": None}, ["TOKEN"])
    assert not common.no_credentials({"HOME": "/h", "CLAUDE_CONFIG_DIR": "/c"}, [])


# -- a replay through the launch ------------------------------------------------------------


def test_a_replay_composes_the_argv_a_fresh_launch_of_its_values_does(work, harness,
                                                                     no_exec):
    typed = {"model": "opus", "effort": "high", "add_dir": [str(work)],
             "native_name": "lead"}
    api.launch(request(work, **typed))
    replayed = api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2",
                                        actor="ann"))
    fresh = api.launch(request(work, rid="r3", sid=SID2, **typed))
    assert replayed.session_id not in (None, SID)
    assert without_ids(replayed.argv, "r2", replayed.session_id) == \
        without_ids(fresh.argv, "r3", SID2)
    new = row("r2")
    assert (new.relaunch_of, new.resume_of, new.session_id) == ("r1", None,
                                                               replayed.session_id)
    # A replay keeps the rows' values, and not the record of what the request gave.
    first = row("r1").values
    assert first["sources"]["model"] == "typed" and first["filled"] == []
    assert new.values == {k: v for k, v in first.items() if k not in ("sources", "filled")}


def test_a_replay_env_holds_no_variable_of_the_replaying_shell(work, harness, no_exec,
                                                              monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/config/then")
    api.launch(request(work))
    stored_path = row("r1").base_env["PATH"]
    located = {k: v for k, v in os.environ.items() if common.locator(k)}
    # The arming shell: a variable no scrub names, a session stamp, a changed PATH, and
    # the user's own names as they are now.
    monkeypatch.setenv("ARMED_IN_SHELL", "1")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("MOXI_LAUNCHER_REQUEST", "old")
    monkeypatch.setenv("API_TOKEN", "s3cr3t-value")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/config/now")
    monkeypatch.setenv("CODEX_HOME", "/codex/now")
    monkeypatch.setenv("PATH", "/elsewhere/bin" + os.pathsep + stored_path)
    settings_file('[relaunch]\npass_env = ["API_TOKEN"]\n')
    got = api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2",
                                   actor="ann"))
    env = no_exec[-1]["env"]
    assert "ARMED_IN_SHELL" not in env and "CLAUDECODE" not in env
    assert env.get("CODEX_HOME") != "/codex/now"
    assert env["MOXI_LAUNCHER_REQUEST"] == "r2"
    assert env["API_TOKEN"] == "s3cr3t-value"
    # The launch's own configuration, not the replaying process's.
    assert env["CLAUDE_CONFIG_DIR"] == "/config/then"
    # The test fence's own home for the modules, as the launch recorded it.
    assert located and all(env[k] == v for k, v in located.items())
    assert env["PATH"] == stored_path
    assert got.base == "row"
    # The new row records the base it used and no secret.
    new = row("r2")
    assert new.base_env["PATH"] == stored_path
    assert "s3cr3t-value" not in new.base_env_json and "API_TOKEN" not in new.env_json


def test_a_replay_writes_its_row_where_the_callers_environment_puts_the_store(
        work, harness, no_exec, tmp_path):
    api.launch(request(work))
    caller = {**os.environ, "MOXI_LAUNCHER_STATE_HOME": str(tmp_path / "other-state")}
    api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2", actor="ann"),
               env=caller)
    # The child's environment is built from the row's recorded base, which names the
    # first store; the new row still goes where the caller's environment says.
    conn = store.connect(env=caller, create=False)
    try:
        assert store.get(conn, "r2") is not None
    finally:
        conn.close()
    conn = store.connect(create=False)
    try:
        assert store.get(conn, "r2") is None
    finally:
        conn.close()


def test_a_replayed_terminal_launch_carries_the_start_mark(work, harness, no_exec,
                                                           monkeypatch):
    from moxi import terminal

    mark = terminal.STARTED_BY_ENV
    api.launch(request(work))
    assert mark not in row("r1").base_env
    # The replaying shell's own mark is not carried over; the target adds its own.
    monkeypatch.setenv(mark, "the replaying shell")
    api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2", actor="ann"))
    assert no_exec[-1]["env"][mark] == terminal.marked({})[mark] != "the replaying shell"


def test_a_token_seat_records_that_it_had_a_token_and_never_the_token(work, harness,
                                                                     no_exec, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-then")
    api.launch(request(work))
    assert no_exec[-1]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-then"
    first_row = row("r1")
    assert first_row.base_env["CLAUDE_CODE_OAUTH_TOKEN"] is None
    assert "tok-then" not in first_row.base_env_json


def test_a_director_relaunch_takes_only_the_directors_listed_names(work, harness,
                                                                   no_exec, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/config/seat")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "seat-token")
    api.launch(request(work))
    # The director's own shell.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/config/director")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "director-token")
    monkeypatch.setenv("SELF_ONLY", "s")
    monkeypatch.setenv("FOR_TARGET", "d")
    settings_file('[relaunch]\npass_env = ["SELF_ONLY"]\n'
                  'director_pass_env = ["FOR_TARGET"]\n')
    got = api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2",
                                   actor="boss", director=True))
    env = no_exec[-1]["env"]
    assert env["CLAUDE_CONFIG_DIR"] == "/config/seat"
    assert env["FOR_TARGET"] == "d"
    assert "SELF_ONLY" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env
    # A name was passed on purpose: the replay doesn't second-guess it.
    assert got.base == "row"


def test_a_token_seats_director_relaunch_with_none_passed_says_no_credentials(
        work, harness, no_exec, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "seat-token")
    api.launch(request(work))
    got = api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2",
                                   actor="boss", director=True))
    assert got.base == "no-credentials"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in no_exec[-1]["env"]
    assert api.NO_CREDENTIALS in got.warnings
    # Listed for the director, it comes back and nothing is said.
    settings_file('[relaunch]\ndirector_pass_env = ["CLAUDE_CODE_OAUTH_TOKEN"]\n')
    got = api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r3",
                                   actor="boss", director=True))
    assert got.base == "row"
    assert no_exec[-1]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "seat-token"


def test_a_row_with_no_base_takes_only_the_short_list_from_the_replaying_process(
        work, harness, no_exec, monkeypatch):
    api.launch(request(work))
    old = row("r1")
    monkeypatch.setenv("ARMED_IN_SHELL", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/config/job")
    got = api.launch(ReplayRequest(dataclasses.replace(old, base_env_json=None),
                                   relaunch_of="r1", request_id="r2", actor="ann"))
    env = no_exec[-1]["env"]
    assert got.base == "job" and "ARMED_IN_SHELL" not in env
    assert env["PATH"] == os.environ["PATH"] and env["HOME"] == os.environ["HOME"]
    assert "CLAUDE_CONFIG_DIR" not in env
    assert not [k for k in env if common.locator(k)]


TERMINAL = {"TERM": "xterm-256color", "COLORTERM": "truecolor", "TERM_PROGRAM": "kitty",
            "TERM_PROGRAM_VERSION": "0.40"}


def test_a_resume_into_this_terminal_carries_the_callers_terminal_names(
        work, harness, no_exec, monkeypatch):
    for name in TERMINAL:
        monkeypatch.delenv(name, raising=False)
    api.launch(request(work))
    assert not set(TERMINAL) & set(row("r1").base_env)
    ended()
    for name, value in TERMINAL.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ARMED_IN_SHELL", "1")
    monkeypatch.setenv("TERMINFO", "/caller/terminfo")
    api.launch(ReplayRequest(row("r1"), resume_of="r1", request_id="r2", actor="ann"))
    env = no_exec[-1]["env"]
    assert {name: env.get(name) for name in TERMINAL} == TERMINAL
    assert "ARMED_IN_SHELL" not in env and "TERMINFO" not in env


def test_a_resume_from_a_caller_with_no_terminal_names_carries_none(
        work, harness, no_exec, monkeypatch):
    for name in TERMINAL:
        monkeypatch.delenv(name, raising=False)
    api.launch(request(work))
    ended()
    api.launch(ReplayRequest(row("r1"), resume_of="r1", request_id="r2", actor="ann"))
    assert not set(TERMINAL) & set(no_exec[-1]["env"])


def test_a_relaunch_carries_no_terminal_names(work, harness, no_exec, monkeypatch):
    for name in TERMINAL:
        monkeypatch.delenv(name, raising=False)
    api.launch(request(work))
    for name, value in TERMINAL.items():
        monkeypatch.setenv(name, value)
    api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2", actor="ann"))
    assert not set(TERMINAL) & set(no_exec[-1]["env"])


def test_an_option_the_install_no_longer_offers_is_not_replayed(work, harness, no_exec):
    # Launched keeping warm and recycling on expiry; turns (which both need) is gone now.
    api.launch(request(work))
    old = row("r1")
    stored = {**old.values, "keep_warm": "on", "recycle_on_expiry": "clear"}
    got = api.launch(ReplayRequest(dataclasses.replace(old, values_json=json.dumps(stored)),
                                   relaunch_of="r1", request_id="r2", actor="ann"))
    new = row("r2").values
    assert (new["keep_warm"], new["recycle_on_expiry"]) == ("off", "off")
    assert "keep_warm on is not applied: keep_warm needs turns" in got.warnings
    assert "recycle_on_expiry clear is not applied: recycle_on_expiry needs turns" in \
        got.warnings
    env = no_exec[-1]["env"]
    assert common.KEEP_WARM not in env and common.RECYCLE_ON_EXPIRY not in env


def test_a_fresh_launch_reports_no_base(work, harness, no_exec):
    assert api.launch(request(work)).base is None


def test_a_scratch_replay_keeps_the_rows_directory(harness, tmp_path, no_exec):
    api.launch(LaunchRequest(typed={"scratch": True}, cwd=str(tmp_path), request_id="r1",
                             session_id=SID, actor="ann"))
    where = row("r1").cwd
    assert where == str(common.scratch_dir("r1"))
    ended()
    got = api.launch(ReplayRequest(row("r1"), resume_of="r1", request_id="r2",
                                   actor="ann"))
    assert row("r2").cwd == where and no_exec[-1]["cwd"] == where
    assert not common.scratch_dir("r2").exists()
    assert got.state == "started"


def test_a_replay_never_sends_the_brief(work, harness, no_exec):
    api.launch(request(work, brief=BRIEF))
    assert row("r1").brief == BRIEF
    got = api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2",
                                   actor="boss", posture="with:boss"))
    new = row("r2")
    assert new.brief is None and new.handoff_path is None
    assert not first.on_argv(got.argv)
    assert BRIEF not in " ".join(got.argv)
    # The director's posture is the row's column; the values keep the original's.
    assert new.posture == "with:boss"
    assert new.values.get("posture") == row("r1").values.get("posture")


def test_a_self_relaunch_carries_its_handoff_alone(work, harness, no_exec, tmp_path):
    handoff = tmp_path / "handoff.md"
    handoff.write_text("where the work stands\n")
    api.launch(request(work, brief=BRIEF))
    got = api.launch(ReplayRequest(
        row("r1"), relaunch_of="r1", request_id="r2", actor="planner",
        posture=common.RELAUNCHED.format(path=handoff), handoff=str(handoff),
        run="run1"))
    opening = got.argv[1]
    assert opening == (f"Handoff from launcher, relaunched by planner: {handoff}\n\n"
                       "where the work stands")
    assert BRIEF not in opening
    assert no_exec[-1]["env"][common.RELAUNCH] == "run1"
    new = row("r2")
    assert new.handoff_path == str(handoff) and new.brief is None
    assert new.posture == f"relaunched (handoff: {handoff})"
    assert new.env[common.RELAUNCH] == "run1"
    prompt = Path(common.prompt_path(targets.runs_dir(), "r2")).read_text()
    assert f"start posture: relaunched (handoff: {handoff})" in prompt


def test_a_relaunch_with_no_handoff_has_no_relaunch_stamp(work, harness, no_exec):
    api.launch(request(work))
    api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2", run="run1"))
    assert common.RELAUNCH not in no_exec[-1]["env"]


def test_a_resume_keeps_the_session_id_and_is_found_by_it(work, harness, no_exec):
    api.launch(request(work))
    ended()
    conn = store.connect()
    try:
        found = store.resolve(conn, SID)
    finally:
        conn.close()
    got = api.launch(ReplayRequest(found, resume_of="r1", request_id="r2", actor="ann"))
    assert got.session_id == SID
    assert list(got.argv[1:3]) == ["--resume", SID] and "--session-id" not in got.argv
    new = row("r2")
    assert (new.session_id, new.resume_of, new.relaunch_of) == (SID, "r1", None)
    conn = store.connect()
    try:
        assert store.resolve(conn, SID).request_id == "r2"
        assert store.session_ids(conn, "r2") == [(SID, "resume")]
    finally:
        conn.close()


def test_a_resume_takes_the_newest_id_the_launch_was_known_by(work, harness, no_exec):
    api.launch(request(work))
    ended()
    conn = store.connect()
    try:
        store.learn_session(conn, "r1", SID2, "clear")
        found = store.resolve(conn, SID)
    finally:
        conn.close()
    got = api.launch(ReplayRequest(found, resume_of="r1", request_id="r2"))
    assert got.session_id == SID2 and list(got.argv[1:3]) == ["--resume", SID2]


def test_a_resume_of_a_launch_with_no_session_id_is_refused(work, harness, no_exec):
    api.launch(request(work, sid=None, harness="codex", target="headless", brief=BRIEF))
    with pytest.raises(api.Refusal, match="no session id to resume"):
        api.launch(ReplayRequest(row("r1"), resume_of="r1", request_id="r2"))
    conn = store.connect()
    try:
        assert store.get(conn, "r2") is None
    finally:
        conn.close()


def test_a_preview_of_a_resume_shows_the_resumed_id(work, harness, no_exec):
    api.launch(request(work))
    shown = api.preview(ReplayRequest(row("r1"), resume_of="r1", request_id="r2"))
    assert list(shown.argv[1:3]) == ["--resume", SID]


def test_a_replay_of_a_request_id_already_logged_starts_nothing(work, harness, no_exec):
    api.launch(request(work))
    first_try = api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2"))
    again = api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2"))
    assert again.duplicate and again.session_id == first_try.session_id
    assert len(no_exec) == 2


def test_a_respawn_of_a_launch_not_in_a_slot_is_refused(work, harness, no_exec):
    api.launch(request(work))
    with pytest.raises(catalog.BadValue, match="only a slot launch is respawned"):
        api.launch(ReplayRequest(row("r1"), relaunch_of="r1", respawn=True))


# -- the slot target ------------------------------------------------------------------------


def slot_launch(work: Path, rig, number: int = 3) -> None:
    rig.plan(slots={str(number): {"state": "free", "launchable": True}})
    api.launch(LaunchRequest(typed={"target": "slot", "slot": str(number)}, cwd=str(work),
                             request_id="r1", session_id=SID, actor="ann"))


def ended_and_held(rig, number: int = 3, holder: str = "run1") -> None:
    rig.plan(slots={str(number): {"state": "ended", "launchable": False,
                                  "hold": {"holder": holder, "until": 1.9e9}}})


def test_a_relaunch_respawns_its_slot_under_its_hold(work, harness, rig):
    slot_launch(work, rig)
    ended_and_held(rig)
    got = api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2",
                                   hold="run1", respawn=True))
    [call] = rig.calls("respawn")
    assert call["slot"] == 3 and call["hold"] == "run1"
    assert call["meta"] == {"request_id": "r2", "session_id": got.session_id,
                            "chain": "r1"}
    assert call["label"] == "launcher:r2" and call["cwd"] == str(work)
    assert len(rig.calls("start")) == 1
    assert (got.state, got.slot) == ("started", "3")
    assert row("r2").slot == "3"


def test_the_chain_is_the_first_launch_of_the_relaunches(work, harness, rig):
    slot_launch(work, rig)
    ended_and_held(rig)
    api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2", hold="run1",
                             respawn=True))
    ended_and_held(rig, holder="run2")
    api.launch(ReplayRequest(row("r2"), relaunch_of="r2", request_id="r3", hold="run2",
                             respawn=True))
    assert [c["meta"]["chain"] for c in rig.calls("respawn")] == ["r1", "r1"]


def test_a_resume_into_a_slot_starts_a_chain_of_its_own(work, harness, rig, monkeypatch):
    for name in TERMINAL:
        monkeypatch.delenv(name, raising=False)
    slot_launch(work, rig)
    rig.plan(slots={"5": {"state": "free", "launchable": True}})
    for name, value in TERMINAL.items():
        monkeypatch.setenv(name, value)
    api.launch(ReplayRequest(row("r1"), resume_of="r1", request_id="r2", slot=5))
    start = rig.calls("start")[-1]
    # A slot gets its terminal names from slots, none of the caller's.
    assert not set(TERMINAL) & set(start["env"])
    assert start["slot"] == 5 and start["meta"]["chain"] == "r2"
    assert start["argv"][1:3] == ["--resume", SID]


def test_a_slot_held_by_another_is_refused_even_with_a_hold(work, harness, rig):
    slot_launch(work, rig)
    ended_and_held(rig, holder="someone")
    with pytest.raises(targets.SlotTaken, match="held by someone"):
        api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2",
                                 hold="run1", respawn=True))
    assert rig.calls("respawn") == []


def test_a_respawn_slots_refuses_fails_the_row(work, harness, rig):
    slot_launch(work, rig)
    ended_and_held(rig)
    rig.plan(respawn="refused", refused={"reason": "live-pane", "words": "the pane is live"})
    got = api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2",
                                   hold="run1", respawn=True))
    assert (got.state, got.reason) == ("failed", "live-pane")
    assert row("r2").state == "failed"
