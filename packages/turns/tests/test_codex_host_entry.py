"""The Codex host entry, `moxi-turns-codex-host`: its flags, the settings check it keeps,
and the host as a process of its own over the stub `codex` binary."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

import pytest

from moxi import proc as moxi_proc
from moxi_turns import client as turns_client
from moxi_turns import codex as codex_module
from moxi_turns import codex_host
from moxi_turns.codex import CodexHarness, launch_args, verify_options
from moxi_turns.codex_host import UsageError, main
from moxi_turns.endpoint import Endpoint

from fake_codex import calls, events, install
from test_codex_host import FakeAppServer, FakeClient

LINUX = pytest.mark.skipif(not sys.platform.startswith("linux"),
                           reason="the host's process tests read /proc and use a pty")
HOST = Path(sys.executable).parent / "moxi-turns-codex-host"
#: How long a host gets to come up, or to end once asked.
WAIT = 30.0


# -- the flags ---------------------------------------------------------------------------

def _args(tmp_path, *flags) -> dict:
    return launch_args(["--cwd", str(tmp_path), *flags])


def _roster_flags(roots, *, network=True):
    return ["--option", "agents.enabled=false",
            "--option", f"sandbox_workspace_write.writable_roots={json.dumps(roots)}",
            "--option", f"sandbox_workspace_write.network_access={str(network).lower()}"]


# Today's roster shapes: the flags comms' wrapper passes for each row, and the options
# its `launch_options()` builds for that row, which the flags must give unchanged.
ROSTER_ROWS = {
    "default row with the record's root added": (
        ["--sandbox", "workspace-write", *_roster_flags(["/srv/record"])],
        {"sandbox": "workspace-write", "config": {
            "agents": {"enabled": False},
            "sandbox_workspace_write": {"writable_roots": ["/srv/record"],
                                        "network_access": True}}}),
    "roots, a model and the network off": (
        ["--sandbox", "workspace-write", "--model", "gpt-sample",
         *_roster_flags(["/srv/row", "/srv/record"], network=False)],
        {"sandbox": "workspace-write", "model": "gpt-sample", "config": {
            "agents": {"enabled": False},
            "sandbox_workspace_write": {"writable_roots": ["/srv/row", "/srv/record"],
                                        "network_access": False}}}),
    "read-only": (
        ["--sandbox", "read-only", *_roster_flags([])],
        {"sandbox": "read-only", "config": {
            "agents": {"enabled": False},
            "sandbox_workspace_write": {"writable_roots": [], "network_access": True}}}),
    "danger-full-access": (
        ["--sandbox", "danger-full-access", *_roster_flags(["/srv/record"])],
        {"sandbox": "danger-full-access", "config": {
            "agents": {"enabled": False},
            "sandbox_workspace_write": {"writable_roots": ["/srv/record"],
                                        "network_access": True}}}),
    "a permission profile with its table": (
        ["--permissions", "locked", "--model", "gpt-sample",
         "--option", "agents.enabled=false",
         "--option", "permissions.locked={network = {enabled = true}}"],
        {"permissions": "locked", "model": "gpt-sample", "config": {
            "agents": {"enabled": False},
            "permissions": {"locked": {"network": {"enabled": True}}}}}),
    "a permission profile alone": (
        ["--permissions", "locked", "--option", "agents.enabled=false"],
        {"permissions": "locked", "config": {"agents": {"enabled": False}}}),
}


@pytest.mark.parametrize("row", sorted(ROSTER_ROWS))
def test_the_flags_for_each_roster_row_give_the_options_comms_builds(tmp_path, row):
    flags, options = ROSTER_ROWS[row]
    got = _args(tmp_path, *flags)
    assert got["options"] == options
    assert got == {"cwd": str(tmp_path), "options": options, "instructions": None,
                   "resume": None, "window": True}


@pytest.mark.parametrize("row", sorted(ROSTER_ROWS))
def test_an_options_file_carries_the_same_options_whole(tmp_path, row):
    options = ROSTER_ROWS[row][1]
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    assert _args(tmp_path, "--options-file", str(path))["options"] == options


def test_flags_beside_an_options_file_replace_its_keys(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"permissions": "locked", "model": "old", "config": {
        "agents": {"enabled": True}, "keep": {"me": 1}}}))
    got = _args(tmp_path, "--options-file", str(path), "--sandbox", "workspace-write",
                "--model", "new", "--option", "agents.enabled=false")
    # --sandbox replaces the file's choice: a profile and a sandbox are one choice.
    assert got["options"] == {"sandbox": "workspace-write", "model": "new", "config": {
        "agents": {"enabled": False}, "keep": {"me": 1}}}


@pytest.mark.parametrize(("text", "value"), [
    ("true", True), ("3", 3), ("1.5", 1.5), ('"quoted"', "quoted"),
    ('["/a", "/b"]', ["/a", "/b"]), ("{ x = 1 }", {"x": 1}),
    ("gpt-5", "gpt-5"), ("'single'", "single"), ("plain words", "plain words"),
])
def test_an_option_value_is_read_as_codex_reads_a_config_override(tmp_path, text, value):
    got = _args(tmp_path, "--option", f"a.b={text}")["options"]
    assert got == {"config": {"a": {"b": value}}}


@pytest.mark.parametrize(("key", "path"), [
    ('projects."/home/x/my dir.d".trust_level',
     ["projects", "/home/x/my dir.d", "trust_level"]),
    ("sandbox_workspace_write.network_access", ["sandbox_workspace_write", "network_access"]),
    ("projects.'/srv/a.b'.trust_level", ["projects", "/srv/a.b", "trust_level"]),
    ('a . "b.c" . d', ["a", "b.c", "d"]),
    ('"x=y".z', ["x=y", "z"]),
    ("model", ["model"]),
])
def test_an_option_key_is_read_as_a_toml_dotted_key(tmp_path, key, path):
    config = _args(tmp_path, "--option", f'{key}="trusted"')["options"]["config"]
    for part in path:
        assert list(config) == [part]
        config = config[part]
    assert config == "trusted"


def test_options_under_one_quoted_key_share_its_table(tmp_path):
    cwd = "/home/x/my.dir"
    got = _args(tmp_path, "--option", f'projects."{cwd}".trust_level="trusted"',
                "--option", f'projects."{cwd}".note=1')["options"]
    assert got == {"config": {"projects": {cwd: {"trust_level": "trusted", "note": 1}}}}


def test_the_rest_of_the_flags(tmp_path):
    text = "You are the planner.\n"
    path = tmp_path / "instructions.md"
    path.write_text(text)
    got = _args(tmp_path, "--instructions-file", str(path), "--resume", "thread-9",
                "--no-window")
    assert got == {"cwd": str(tmp_path), "options": {}, "instructions": text,
                   "resume": "thread-9", "window": False}


@pytest.mark.parametrize(("flags", "words"), [
    (["--option", "sandbox_mode=workspace-write"], "--sandbox"),
    (["--sandbox", "read-only", "--permissions", "locked"], "not allowed with"),
    (["--sandbox", "open"], "invalid choice"),
    (["--option", "no-equals"], "KEY=VALUE"),
    (["--option", "a..b=1"], "KEY=VALUE"),
    (["--option", "=1"], "KEY=VALUE"),
    (["--option", 'projects."/open.trust_level=1'], "KEY=VALUE"),
    (["--option", "projects./home/x.trust_level=1"], "KEY=VALUE"),
    (["--option", "a.=1"], "KEY=VALUE"),
    (["--option", "[t]\nb=1"], "KEY=VALUE"),
    (["--option", '"a\nb"=1'], "KEY=VALUE"),
    (["--option", "when=1979-05-27"], "date"),
    (["--resume", " "], "thread id"),
    (["--surprise"], "unrecognized"),
])
def test_a_refused_flag_exits_2_before_anything_starts(tmp_path, capsys, flags, words):
    assert main(["--cwd", str(tmp_path), *flags]) == 2
    err = capsys.readouterr().err
    assert err.startswith("moxi-turns-codex-host: ") and words in err


@pytest.mark.parametrize(("options", "words"), [
    ({"approvalPolicy": "on-request"}, "keys turns owns: approvalPolicy"),
    ({"cwd": "/elsewhere", "threadId": "t"}, "keys turns owns: cwd, threadId"),
    ({"sandbox": "read-only", "permissions": "locked"}, "one choice"),
    ({"sandbox": "open"}, "is not one of"),
    ({"config": {"sandbox_mode": "read-only"}}, "--sandbox"),
    ({"config": []}, "config is a JSON object"),
    ([], "not a JSON object"),
])
def test_an_options_file_the_host_refuses_exits_2(tmp_path, capsys, options, words):
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    assert main(["--cwd", str(tmp_path), "--options-file", str(path)]) == 2
    assert words in capsys.readouterr().err


def test_missing_files_and_folders_are_usage_errors(tmp_path):
    with pytest.raises(UsageError, match="not a directory"):
        launch_args(["--cwd", str(tmp_path / "nowhere")])
    with pytest.raises(UsageError, match="--options-file"):
        _args(tmp_path, "--options-file", str(tmp_path / "none.json"))
    with pytest.raises(UsageError, match="--instructions-file"):
        _args(tmp_path, "--instructions-file", str(tmp_path / "none.md"))
    with pytest.raises(UsageError, match="--cwd"):
        launch_args([])


# -- the settings check ------------------------------------------------------------------

def _options(tmp_path, roots=(), *, network=True):
    return _args(tmp_path, "--sandbox", "workspace-write",
                 *_roster_flags([str(r) for r in roots], network=network))["options"]


@pytest.mark.parametrize(("reported", "reason"), [
    ({"type": "readOnly", "networkAccess": True, "writableRoots": []},
     "sandbox is 'readOnly', not 'workspaceWrite'"),
    ({"type": "workspaceWrite", "networkAccess": False, "writableRoots": []},
     "network access is off, not on"),
    ({"type": "workspaceWrite", "networkAccess": True, "writableRoots": []},
     "writable roots lack"),
])
def test_the_check_names_each_mismatch(tmp_path, reported, reason):
    options = _options(tmp_path, [tmp_path / "row"])
    answer = {"cwd": str(tmp_path), "sandbox": reported}
    assert reason in verify_options(options, answer, tmp_path)


def test_the_check_counts_the_working_directory_as_writable(tmp_path):
    options = _options(tmp_path, [tmp_path])
    answer = {"sandbox": {"type": "workspaceWrite", "networkAccess": True,
                          "writableRoots": []}}
    assert verify_options(options, answer, tmp_path) is None
    assert verify_options(options, {**answer, "cwd": str(tmp_path)}, "/elsewhere") is None


def test_a_permission_profile_or_an_unreported_sandbox_is_not_checked(tmp_path):
    profile = _args(tmp_path, "--permissions", "locked")["options"]
    assert verify_options(profile, {"sandbox": {"type": "readOnly"}}, tmp_path) is None
    assert verify_options(_options(tmp_path, [tmp_path / "x"]), {}, tmp_path) is None


class _Reporting(FakeClient):
    """The fake client, whose load answers report `report` as the loaded sandbox."""

    report: dict = {}

    def call(self, method, params=None, timeout=None):
        result = super().call(method, params, timeout)
        if method in ("thread/start", "thread/resume"):
            result = {**result, "sandbox": self.report, "cwd": self.harness.cwd}
        return result


def _checked_host(turns_env, report, **kwargs):
    made = []
    harness = None

    def clients(**kw):
        client = type("Client", (_Reporting,), {"report": report})(harness, **kw)
        made.append(client)
        return client

    harness = CodexHarness(turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
                           client_factory=clients, app_server_factory=FakeAppServer,
                           backoff_factory=lambda: iter(lambda: 60.0, None), **kwargs)
    endpoint = Endpoint(harness, session_id=None).open()
    harness.start()
    return endpoint, harness, made


OFF = {"type": "workspaceWrite", "networkAccess": False, "writableRoots": []}


def test_a_resume_whose_answer_reports_other_settings_fails_its_load(turns_env):
    options = _options(turns_env)              # asks for the network on
    endpoint, harness, made = _checked_host(turns_env, OFF, options=options,
                                            resume="thread-1")
    try:
        assert made[0].calls[1] == ("thread/resume", {
            **options, "threadId": "thread-1", "excludeTurns": True,
            "approvalPolicy": "never"})
        assert harness.trouble == "network access is off, not on"
        assert not harness.app_server.alive        # the load failed; the server stopped
        assert endpoint.facts().ready is None
    finally:
        endpoint.stop("the test ended")


def test_a_new_threads_other_settings_are_logged_and_the_load_stands(turns_env, caplog):
    options = _options(turns_env)
    with caplog.at_level(logging.WARNING, logger="moxi.turns.codex"):
        endpoint, harness, made = _checked_host(turns_env, OFF, options=options)
    try:
        assert "thread/start reports different settings: network access is off" \
            in caplog.text
        assert harness.session_id == "thread-1" and harness.app_server.alive
        assert endpoint.facts().ready is not None
    finally:
        endpoint.stop("the test ended")


def test_a_resume_that_matches_loads_the_named_thread(turns_env):
    options = _options(turns_env)
    report = {"type": "workspaceWrite", "networkAccess": True, "writableRoots": []}
    endpoint, harness, made = _checked_host(turns_env, report, options=options,
                                            resume="thread-1")
    try:
        assert harness.trouble is None and harness.session_id == "thread-1"
        assert endpoint.facts().ready.source == "resume"
        assert [m for m, _ in made[0].calls].count("thread/start") == 0
    finally:
        endpoint.stop("the test ended")


def test_a_given_verify_replaces_the_default_check(turns_env):
    seen = []

    def verify(answer):
        seen.append(answer)
        return None

    endpoint, harness, _ = _checked_host(turns_env, OFF, options=_options(turns_env),
                                         resume="thread-1", verify=verify)
    try:
        assert harness.trouble is None and seen
    finally:
        endpoint.stop("the test ended")
    profile = CodexHarness(turns_env, window=False, options={"permissions": "locked"})
    assert profile.verify is None


def test_resume_must_name_a_thread(turns_env):
    with pytest.raises(ValueError, match="thread id"):
        CodexHarness(turns_env, window=False, resume="")


def test_the_entry_is_declared_and_gated_by_the_codex_extra():
    from importlib.metadata import entry_points

    from moxi_turns import module

    found = {e.name: e.value for e in entry_points(group="console_scripts")}
    assert found["moxi-turns-codex-host"] == "moxi_turns.codex_host:main"
    assert module.GATED["moxi-turns-codex-host"] == "codex"
    # The host reads the core's lifeline variable by its own name for it.
    assert codex_host._LIFELINE_ENV == moxi_proc.LIFELINE_ENV in module.INTERNAL_ENV
    assert codex_module.launch_args is launch_args


# -- the host as a process ---------------------------------------------------------------

class Rig:
    def __init__(self, tmp_path: Path):
        self.records = tmp_path / "records"
        self.records.mkdir()
        self.work = tmp_path / "work"
        self.work.mkdir()
        self.logs = tmp_path / "logs"
        self.logs.mkdir()
        binary = install(tmp_path / "bin")
        self.env = dict(os.environ)
        self.env.update({"PATH": f"{binary.parent}{os.pathsep}{self.env.get('PATH', '')}",
                         "FAKE_CODEX_DIR": str(self.records)})
        self.procs: list[subprocess.Popen] = []
        self.started = 0

    def argv(self, *flags) -> list[str]:
        return [str(HOST), "--cwd", str(self.work), *flags]

    def log(self) -> Path:
        self.started += 1
        return self.logs / f"host-{self.started}.log"

    def spawn(self, *flags, lifeline=None, env=None, **kwargs) -> subprocess.Popen:
        out = self.log().open("wb")
        if lifeline is not None:
            proc = moxi_proc.spawn(self.argv(*flags), lifeline=lifeline,
                                   env=env or self.env, stdout=out, stderr=out)
        else:
            proc = subprocess.Popen(self.argv(*flags), env=env or self.env, stdout=out,
                                    stderr=out, **kwargs)
        out.close()
        proc.out = self.logs / f"host-{self.started}.log"
        self.procs.append(proc)
        return proc

    def app_server(self, n: int = -1) -> dict:
        found = _until(lambda: events(self.records, "app-server"))
        return found[n]

    def close(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=WAIT)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        for found in events(self.records):
            if found["event"] in ("app-server", "window") and not _gone(found["pid"]):
                os.kill(found["pid"], signal.SIGKILL)


@pytest.fixture
def rig(turns_env, tmp_path):
    made = Rig(tmp_path)
    yield made
    made.close()


def _until(read, timeout: float = WAIT):
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(0.05)


def _gone(pid: int) -> bool:
    """Whether `pid` has ended (an ended child not yet reaped counts)."""
    found = moxi_proc.identity(pid)
    return found is None or not moxi_proc.alive(found)


def _exit(proc, timeout: float = WAIT) -> int:
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pytest.fail(f"the host did not end: {proc.out.read_text()}")


@LINUX
def test_a_host_spawned_with_a_lifeline_is_found_by_pid_and_ends_with_the_pipe(rig):
    instructions = rig.work.parent / "instructions.md"
    instructions.write_text("Answer in one line.")
    env = {**rig.env, "FAKE_CODEX_ECHO": "MOXI_TEST_STAMP", "MOXI_TEST_STAMP": "agent-7"}
    host = rig.spawn("--no-window", "--sandbox", "workspace-write",
                     *_roster_flags([str(rig.work)]), "--model", "gpt-sample",
                     "--instructions-file", str(instructions), lifeline="fd", env=env)
    session = turns_client.wait_ready(pid=host.pid, timeout=WAIT)
    try:
        assert session.pid == session.endpoint_pid == host.pid
        assert session.harness == "codex" and session.session_id
        server = rig.app_server()
        # A watched child: the host's own, in its process group and session, with
        # /dev/null for stdin, and the host's environment passed through.
        assert (server["ppid"], server["pgid"], server["sid"]) == (
            host.pid, os.getpgid(host.pid), os.getsid(host.pid))
        assert server["stdin_tty"] is False and server["echo"] == "agent-7"
        start = next(c for c in calls(rig.records) if c["method"] == "thread/start")
        assert start["params"]["developerInstructions"] == "Answer in one line."
        assert start["params"]["sandbox"] == "workspace-write"
        assert start["params"]["model"] == "gpt-sample"
        assert start["params"]["config"]["agents"] == {"enabled": False}
    finally:
        session.close()
    host.lifeline.close()
    assert _exit(host) == 0
    assert "the Codex host was stopped (its lifeline closed)" in host.out.read_text()
    assert _gone(server["pid"]) and events(rig.records, "app-server-exit")
    assert turns_client.find(pid=host.pid) is None


@LINUX
def test_a_host_without_a_lifeline_ignores_its_stdin_and_resumes_its_thread(rig):
    host = rig.spawn("--no-window", stdin=subprocess.PIPE)
    session = turns_client.wait_ready(pid=host.pid, timeout=WAIT)
    thread = session.session_id
    session.close()
    host.stdin.close()                      # the end of stdin is not a lifeline
    time.sleep(1.0)
    assert host.poll() is None
    host.send_signal(signal.SIGTERM)
    assert _exit(host) == 0
    assert "the Codex host was stopped (SIGTERM)" in host.out.read_text()

    again = rig.spawn("--no-window", "--resume", thread, stdin=subprocess.DEVNULL)
    session = turns_client.wait_ready(pid=again.pid, timeout=WAIT)
    try:
        assert session.session_id == thread
        assert session.facts().ready.source == "resume"
        resumes = [c for c in calls(rig.records) if c["method"] == "thread/resume"]
        assert resumes and resumes[0]["pid"] == rig.app_server()["pid"]
        assert resumes[0]["params"]["threadId"] == thread
        assert not [c for c in calls(rig.records)
                    if c["method"] == "thread/start" and c["pid"] == resumes[0]["pid"]]
    finally:
        session.close()
    again.send_signal(signal.SIGTERM)
    assert _exit(again) == 0


@LINUX
def test_the_app_server_ends_when_its_host_is_killed(rig):
    host = rig.spawn("--no-window")
    turns_client.wait_ready(pid=host.pid, timeout=WAIT).close()
    server = rig.app_server()
    host.kill()
    host.wait()
    # No SIGTERM from the host, no parent-death signal: the watchdog ends it.
    assert _until(lambda: _gone(server["pid"]), 15.0)


@LINUX
def test_a_launch_that_fails_exits_1_with_the_reason_on_stderr(rig):
    host = rig.spawn("--no-window", env={**rig.env, "FAKE_CODEX_UNTRUSTED": "1"})
    assert _exit(host) == 1
    err = host.out.read_text()
    assert "moxi-turns-codex-host: the Codex launch failed: " in err
    assert "not trusted" in err
    server = rig.app_server()
    assert _until(lambda: _gone(server["pid"]), 15.0)


@LINUX
def test_the_window_shares_the_hosts_terminal_and_its_close_ends_the_host(rig):
    master, slave = os.openpty()
    out = bytearray()

    def drain():
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                return
            if not chunk:
                return
            out.extend(chunk)

    host = subprocess.Popen(rig.argv(), env=rig.env, stdin=slave, stdout=slave,
                            stderr=slave, start_new_session=True,
                            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
    host.out = rig.logs / "unused"
    rig.procs.append(host)
    os.close(slave)
    threading.Thread(target=drain, daemon=True).start()
    try:
        window = _until(lambda: events(rig.records, "window"))
        assert window, bytes(out).decode(errors="replace")
        window = window[0]
        server = rig.app_server()
        # The window and the app-server stay in the host's group and session; the
        # window has the terminal as the host has it, and the foreground is not moved.
        assert (window["ppid"], window["pgid"], window["sid"]) == (
            host.pid, host.pid, host.pid)
        assert window["foreground"] is True and window["stdin_tty"] is True
        assert (server["pgid"], server["sid"], server["stdin_tty"]) == (
            host.pid, host.pid, False)
        session = turns_client.find(pid=host.pid)
        assert session is not None and window["thread"] == session.session_id
        session.close()
        (rig.records / "quit").touch()
        assert _exit(host) == 0
        assert _until(lambda: _gone(server["pid"]), 15.0)
        assert turns_client.find(pid=host.pid) is None
    finally:
        os.close(master)
