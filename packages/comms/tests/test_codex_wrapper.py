"""`moxi comms codex` (and `moxi-comms-codex`): the Codex wrapper's own steps.

The host line the wrapper builds is read back with turns' own parser, so each test
holds what turns' host would send. The launch itself runs for real in a child process
with a stand-in host that records what it found when it started: the adapter bound and
listening before the host starts, the host keeping the wrapper's pid, the claim rooted
at it, layer one in its file, and the adapter's end when the host ends.
`test_codex_turns_host.py` runs the wrapper over turns' real host.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
from conftest import wait_until

from moxi import exit as moxi_exit
from moxi_comms import api, claimfile, cli, reserved, secrets
from moxi_comms.adapters import codex_wrapper
from moxi_comms.adapters.codex_roster import record_write_root
from moxi_comms.adapters.codex_wrapper import Refused, build_parser, plan
from moxi_comms.claimhold import CLAIM_SOCKET_ENV
from moxi_comms.context import CONTEXT_BLOCK

NAME = "coder"


def args(*argv):
    return build_parser().parse_args(list(argv))


def read_host(launch) -> dict:
    """What turns' host reads from the launch's line."""
    return codex_wrapper.launch_args(["--cwd", launch.cwd, *launch.flags])


# --------------------------------------------------------------------------- the line

def test_a_named_launch_adds_comms_settings_to_the_hosts_flags(tmp_path):
    launch = plan(args("--name", NAME, "--scope", "proj-a", "--model", "gpt-sample",
                       "--sandbox", "workspace-write"), cwd=str(tmp_path))
    root, _ = record_write_root(launch.agent.env(), str(tmp_path))
    read = read_host(launch)
    assert read["cwd"] == str(tmp_path) and read["window"] is True
    assert read["options"] == {
        "sandbox": "workspace-write", "model": "gpt-sample",
        "config": {"agents": {"enabled": False},
                   "sandbox_workspace_write": {"network_access": True,
                                               "writable_roots": [str(root)]}}}
    assert launch.agent.qualified == "coder@proj-a"
    assert launch.flags[-2:] == ["--option", codex_wrapper.MULTI_AGENT_OFF]


def test_the_launchers_line_reads_whole(tmp_path):
    """The launcher's line: no --cwd, its own options and an options file with the
    directory's trust entry and its added roots."""
    cwd = tmp_path / "my.dir"
    cwd.mkdir()
    extra = tmp_path / "extra"
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"config": {
        "projects": {str(cwd): {"trust_level": "trusted"}},
        "sandbox_workspace_write": {"writable_roots": [str(extra)]}}}))
    launch = plan(args("--name", NAME, "--scope", "proj-a", "--director", "lead",
                       "--model", "m", "--sandbox", "workspace-write",
                       "--option", "check_for_update_on_startup=false",
                       "--options-file", str(options)), cwd=str(cwd))
    root, _ = record_write_root(launch.agent.env(), str(cwd))
    config = read_host(launch)["options"]["config"]
    assert config["projects"] == {str(cwd): {"trust_level": "trusted"}}
    assert config["check_for_update_on_startup"] is False
    assert config["sandbox_workspace_write"] == {
        "writable_roots": [str(extra), str(root)], "network_access": True}
    assert launch.agent.directors == ["lead"]


def test_a_launch_that_sets_network_access_keeps_its_own(tmp_path):
    launch = plan(args("--name", NAME, "--sandbox", "workspace-write", "--option",
                       "sandbox_workspace_write.network_access=false"), cwd=str(tmp_path))
    settings = read_host(launch)["options"]["config"]["sandbox_workspace_write"]
    assert settings["network_access"] is False


def test_a_launch_naming_no_sandbox_gets_a_rows_default(tmp_path):
    read = read_host(plan(args("--name", NAME), cwd=str(tmp_path)))
    assert read["options"]["sandbox"] == "workspace-write"


def test_a_permission_profile_gets_no_sandbox_settings(tmp_path):
    read = read_host(plan(args("--name", NAME, "--permissions", "locked"),
                          cwd=str(tmp_path)))
    assert read["options"] == {"permissions": "locked",
                               "config": {"agents": {"enabled": False}}}


def test_no_window_reaches_the_host(tmp_path):
    launch = plan(args("--name", NAME, "--no-window"), cwd=str(tmp_path))
    assert read_host(launch)["window"] is False and not launch.window


def test_a_named_launch_told_to_resume_passes_the_thread_to_the_host(tmp_path):
    launch = plan(args("--name", NAME, "--model", "m", "--resume", "thread-old"),
                  cwd=str(tmp_path))
    assert read_host(launch)["resume"] == "thread-old" == launch.resume
    i = launch.flags.index("--resume")
    assert launch.flags[i + 1] == "thread-old"
    assert "--resume" in launch.host_argv("host")
    fresh = plan(args("--name", NAME), cwd=str(tmp_path))
    assert fresh.resume is None and read_host(fresh)["resume"] is None
    assert "--resume" not in fresh.flags


def test_the_plan_says_whether_the_launch_resumes_a_thread(tmp_path):
    import io

    def said(*argv):
        launch = plan(args("--name", NAME, *argv), cwd=str(tmp_path))
        out = io.StringIO()
        codex_wrapper.print_plan(launch, launch.host_argv("host"), out=out)
        (line,) = [ln for ln in out.getvalue().splitlines()
                   if ln.startswith("  thread     ")]
        return line

    assert said("--resume", "thread-old").endswith("(resumes thread thread-old)")
    assert said().endswith("(a new thread at this launch)")


def test_a_roster_launch_is_the_rows_flags_with_no_window(tmp_path, monkeypatch):
    from moxi_comms.adapters import codex_roster

    work = tmp_path / "work"
    work.mkdir()
    config = tmp_path / "roster.toml"
    config.write_text(f'[codex.agents."planner@proj-a"]\ncwd = "{work}"\n'
                      'network_access = false\nmodel = "gpt-sample"\n'
                      'directors = ["lead"]\n')
    launch = plan(args("planner@proj-a", "--config", str(config)))
    row = codex_roster.load(path=str(config)).require("planner@proj-a")
    read = read_host(launch)
    assert read["cwd"] == str(work) and read["window"] is False
    assert read["options"] == row.launch_options(launch.write_root)
    assert launch.source == str(config)
    assert plan(args("planner@proj-a", "--config", str(config), "--window")).window


@pytest.mark.parametrize(("argv", "says"), [
    (["--name", "a b"], "is not a name"),
    (["--name", NAME, "--scope", "proj-a", "--global"], "one choice"),
    ([], "pass --name"),
    (["planner@proj-a", "--name", NAME], "not both"),
    (["planner@proj-a", "--sandbox", "read-only"], "--sandbox: a roster launch"),
    (["--name", NAME, "--config", "x.toml"], "goes with a roster IDENTITY"),
    (["--name", NAME, "--option", "sandbox_mode=read-only"], "name the sandbox with"),
    (["--name", NAME, "--sandbox", "open"], "invalid choice"),
    (["--name", NAME, "--option", "no-equals"], "expected KEY=VALUE"),
    (["--name", NAME, "--director", "bad name"], "bad name"),
    (["planner@proj-a", "--resume", "thread-old"], "--resume goes with --name"),
    (["--name", NAME, "--resume", " "], "--resume names a thread id"),
])
def test_a_launch_the_host_or_comms_refuses_is_refused_before_anything(
        tmp_path, capsys, argv, says):
    with pytest.raises(Refused) as refused:
        plan(args(*argv), cwd=str(tmp_path))
    assert says in str(refused.value)
    assert refused.value.code == moxi_exit.USAGE
    assert codex_wrapper.main([*argv, "--dry-run"]) == moxi_exit.USAGE
    assert not codex_wrapper.sessions_dir().exists() or not any(
        codex_wrapper.sessions_dir().iterdir()), "nothing is written"


def test_a_roster_identity_the_roster_lacks_is_refused(tmp_path, capsys):
    config = tmp_path / "roster.toml"
    config.write_text("")
    assert codex_wrapper.main(["stranger@nowhere", "--config", str(config),
                               "--dry-run"]) == moxi_exit.USAGE
    assert "never register-triggered" in capsys.readouterr().err


def test_the_dry_run_prints_the_host_line_and_starts_nothing(tmp_path, capsys,
                                                             monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = cli.main(["codex", "--name", NAME, "--model", "m", "--dry-run"],
                    prog="moxi comms")
    out = capsys.readouterr().out
    assert code == 0, out
    (line,) = [ln for ln in out.splitlines() if ln.startswith("  launch     ")]
    words = line.split()[1:]
    assert words[0].endswith("moxi-turns-codex-host")
    assert words[1:3] == ["--cwd", str(tmp_path)]
    assert words[-2] == "--instructions-file"
    assert words[-1].endswith("/instructions.md")
    assert "identity   coder" in out and "approvals never by turns" in out
    assert not codex_wrapper.sessions_dir().exists() or not any(
        codex_wrapper.sessions_dir().iterdir())


def test_the_verb_is_listed_in_help(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "codex" in capsys.readouterr().out


# --------------------------------------------------------------------------- the env

def test_the_hosts_environment_carries_this_identity_alone_and_no_lifeline(
        tmp_path, monkeypatch):
    launch = plan(args("--name", NAME, "--scope", "proj-a"), cwd=str(tmp_path))
    base = {"PATH": "/bin", "MOXI_LIFELINE_FD": "7", "STAMPED_BY_CALLER": "kept",
            "MOXI_COMMS_DIRECTORS": "someone-else", CLAIM_SOCKET_ENV: "/parent.sock"}
    env = launch.env(base)
    assert env["PATH"] == "/bin" and env["STAMPED_BY_CALLER"] == "kept"
    assert (env["MOXI_COMMS_NAME"], env["MOXI_COMMS_SCOPE"]) == (NAME, "proj-a")
    assert env[CLAIM_SOCKET_ENV] == str(launch.claim_socket)
    assert "MOXI_COMMS_DIRECTORS" not in env, "no director is handed by proximity"
    assert "MOXI_LIFELINE_FD" not in env, "an exec'd host gets no lifeline"


# --------------------------------------------------------------------------- the TOML

@pytest.mark.parametrize("value", [
    True, 3, 0.5, "plain", 'quo"te \\ back', "line\nbreak", "tab\tdel\x7f", "ünï",
    ["a", "b c"], [], {"a": 1, "b c": {"d.e": [True]}}, {},
])
def test_a_value_reads_back_as_itself(value):
    import tomllib

    assert tomllib.loads(f"x = {codex_wrapper.toml_value(value)}")["x"] == value


def test_a_key_part_with_dots_or_quotes_reads_back_as_one_part():
    import tomllib

    key = ".".join(codex_wrapper.toml_key(p) for p in ["projects", "/x/my.dir", 'q"d'])
    assert tomllib.loads(f"{key} = 1") == {"projects": {"/x/my.dir": {'q"d': 1}}}


# --------------------------------------------------------------------------- the launch

@pytest.fixture
def fake_host(tmp_path):
    """A stand-in for turns' Codex host: it records what it found when it started and
    waits for the test's word."""
    out = tmp_path / "host.out"
    script = tmp_path / "moxi-turns-codex-host"
    script.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        if [ -S "$MOXI_COMMS_CLAIM_SOCKET" ]; then echo socket-ready >> {out}; fi
        echo "pid $$" >> {out}
        echo "name $MOXI_COMMS_NAME@$MOXI_COMMS_SCOPE" >> {out}
        echo "lifeline ${{MOXI_LIFELINE_FD:-none}}" >> {out}
        echo "args $*" >> {out}
        while [ "$1" != "--instructions-file" ]; do shift; done
        if head -c 400 "$2" | grep -q "comms"; then echo layer-one >> {out}; fi
        while [ ! -e {tmp_path}/release ]; do sleep 0.05; done
        """))
    script.chmod(0o755)
    return SimpleNamespace(script=script, out=out, release=tmp_path / "release")


def run_wrapper(fake_host, *argv, env=None):
    """The wrapper in a child process, finding the stand-in host as its host."""
    code = ("import moxi.paths, moxi_turns\n"
            f"moxi.paths.script = lambda name: __import__('pathlib').Path("
            f"{str(fake_host.script)!r})\n"
            "moxi_turns.codex_host_available = lambda: True\n"
            "from moxi_comms.adapters import codex_wrapper\n"
            f"raise SystemExit(codex_wrapper.main({list(argv)!r}))\n")
    environ = dict(os.environ if env is None else env)
    environ["MOXI_LIFELINE_FD"] = "99"
    return subprocess.Popen([sys.executable, "-c", code], env=environ,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_the_launch_binds_then_execs_the_host_which_keeps_the_pid(
        rig, fake_host, tmp_path):
    proc = run_wrapper(fake_host, "--name", NAME, "--scope", "proj-a", "--no-window")
    try:
        assert wait_until(lambda: fake_host.out.exists()
                          and "layer-one" in fake_host.out.read_text(), timeout=20), \
            proc.stderr.read() if proc.poll() is not None else "the host did not start"
        lines = fake_host.out.read_text().splitlines()
        assert lines[0] == "socket-ready", "the claim socket listened before the host"
        pid = int(lines[1].split()[1])
        assert pid == proc.pid, "the host keeps the wrapper's pid"
        assert lines[2] == "name coder@proj-a"
        assert lines[3] == "lifeline none"
        assert lines[4].startswith(f"args --cwd {os.getcwd()} --sandbox workspace-write")
        assert "--no-window" in lines[4]
        with rig.daemon._lock:
            binding = rig.daemon.bindings.get("coder@proj-a")
        assert binding is not None and binding.adapter == "codex"
        (key, record), = [(k, r) for k, r in claimfile.live_records()
                          if (r.get("claim") or {}).get("qualified") == "coder@proj-a"]
        assert [r["pid"] for r in record["roots"]] == [pid], "the host is the claim's root"
        session_dir = next(codex_wrapper.sessions_dir().glob("codex-*"))
        instructions = session_dir / "instructions.md"
        assert instructions.read_text().startswith(CONTEXT_BLOCK)
        assert instructions.stat().st_mode & 0o777 == 0o600
        assert session_dir.stat().st_mode & 0o777 == 0o700
    finally:
        fake_host.release.touch()
        proc.communicate(timeout=20)
    assert proc.returncode == 0
    assert wait_until(lambda: "coder@proj-a" not in rig.daemon.bindings, 15), \
        "the adapter ends with the host"
    assert wait_until(lambda: not any(codex_wrapper.sessions_dir().glob("codex-*")), 10)


def test_a_launch_told_to_resume_binds_the_name_to_that_thread(rig, fake_host):
    """The host gets `--resume`, and the adapter bound the name to the thread before the
    host started, as a resume and not a new session. A host that ends before its
    session is ready takes the binding with it."""
    proc = run_wrapper(fake_host, "--name", NAME, "--scope", "proj-a", "--no-window",
                       "--resume", "thread-old")
    try:
        assert wait_until(lambda: fake_host.out.exists()
                          and "layer-one" in fake_host.out.read_text(), timeout=20), \
            proc.stderr.read() if proc.poll() is not None else "the host did not start"
        (line,) = [ln for ln in fake_host.out.read_text().splitlines()
                   if ln.startswith("args ")]
        words = line.split()
        assert words[words.index("--resume") + 1] == "thread-old"
        with rig.daemon._lock:
            binding = rig.daemon.bindings.get("coder@proj-a")
        assert binding is not None
        assert (binding.session_id, binding.new_session) == ("thread-old", False)
    finally:
        fake_host.release.touch()
        proc.communicate(timeout=20)
    assert proc.returncode == 0
    assert wait_until(lambda: "coder@proj-a" not in rig.daemon.bindings, 15)


def test_a_held_name_is_refused_before_the_host_starts(rig, fake_host):
    rig.shim(name="taken")
    proc = run_wrapper(fake_host, "--name", "taken", "--no-window")
    _, err = proc.communicate(timeout=30)
    assert proc.returncode == moxi_exit.REFUSED, err
    assert "taken was not launched" in err
    assert not fake_host.out.exists(), "the host never started"


def test_a_reserved_name_reads_reserved_and_is_refused_before_the_host_starts(
        rig, fake_host):
    reserved.reconcile(declared={"launcher": ["console"]}, chain=secrets.from_config())
    state = api.identity_state("console")
    assert (state.state, state.module) == ("reserved", "launcher")
    proc = run_wrapper(fake_host, "--name", "console", "--no-window")
    _, err = proc.communicate(timeout=30)
    assert proc.returncode == moxi_exit.REFUSED, err
    assert "console was not launched" in err and "reserved for launcher" in err
    assert not fake_host.out.exists(), "the host never started"
    assert "console" not in rig.daemon.bindings


def test_a_daemon_that_is_not_running_is_tempfail_before_the_host_starts(fake_host):
    proc = run_wrapper(fake_host, "--name", NAME, "--no-window")
    _, err = proc.communicate(timeout=30)
    assert proc.returncode == moxi_exit.TEMPFAIL, err
    assert not fake_host.out.exists()
    assert not any(codex_wrapper.sessions_dir().glob("codex-*"))
