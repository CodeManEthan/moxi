"""The gate, `moxi run`: its strict argument list, the switch read against `moxi.config`
in every state, its exit codes and run lines, `--log`, `--restart`, `--runtime`, the two
wrap forms, and the core's stores in a module's state directory."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from moxi import cli, config, files, paths, run
from moxi.config import ConfigError, Schema

SCHEMA = """
[snapshot.enabled]
type = "bool"
default = false
doc = "Take a snapshot every five minutes."
read = "use"

[rows]
type = "table"
items = "bool"
default = {}
doc = "A switch per row."
read = "use"
"""

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
#: The gate as its own process, as a unit runs it.
GATE = [sys.executable, "-c", "import sys; from moxi.run import main; sys.exit(main())"]
PY = sys.executable


@pytest.fixture
def demo(site, tmp_path, monkeypatch):
    """Module `demo` with SCHEMA, its roots in the test's tmp. Returns its config file."""
    site.write("moxi_demo/settings.toml", SCHEMA)
    site.module("demo", SETTINGS="'settings.toml'")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "moxi-home"))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    for name in ("CONFIG_HOME", "STATE_HOME", "HOME", "RUNTIME_DIR"):
        monkeypatch.delenv(f"MOXI_DEMO_{name}", raising=False)
    for name in ("SERVICE_RESULT", "EXIT_CODE", "EXIT_STATUS"):
        monkeypatch.delenv(name, raising=False)
    path = paths.Layout("demo").config_file()
    assert str(path).startswith(str(tmp_path))
    return path


def write_config(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def lines(module: str = "demo") -> list[dict]:
    return files.read_records(run.runs_path(module))


def gate(*args: str, env: dict | None = None, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run([*GATE, *args], capture_output=True, text=True, timeout=timeout,
                          env=env)


def py(code: str) -> list[str]:
    return [PY, "-c", textwrap.dedent(code)]


# -- the argument list -------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    [],
    ["--record", "demo/snapshot"],                                     # no command
    ["--record", "demo/snapshot", "--"],                               # an empty one
    ["--if", "demo.snapshot.enabled", "--", PY],                       # no --record
    ["--record", "demo", "--", PY],
    ["--record", "Demo/snapshot", "--", PY],
    ["--record", "demo/Snap_shot", "--", PY],
    ["--record", "demo/snapshot", "--if", "demo", "--", PY],
    ["--record", "demo/snapshot", "--if", "demo.", "--", PY],
    ["--record", "demo/snapshot", "--if", "demo.a..b", "--", PY],
    ["--record", "demo/snapshot", "--if", "other.snapshot.enabled", "--", PY],
    ["--record", "demo/snapshot", "--record", "demo/snapshot", "--", PY],
    ["--record", "demo/snapshot", "--iff", "demo.x", "--", PY],
    ["--record", "demo/snapshot", "--if=demo.x", "--", PY],
    ["--record", "demo/snapshot", "--log", "relative.log", "--", PY],
    ["--record", "demo/snapshot", "--stop", "tree", "--", PY],
    ["--record", "demo/snapshot", "--if", "--", PY],
    ["--record", "demo/snapshot", PY],                                  # no --
])
def test_a_bad_argument_list_exits_2_and_runs_nothing(demo, tmp_path, argv, capsys):
    marker = tmp_path / "ran"
    if argv and argv[-1] == PY:
        argv = [*argv, "-c", f"open({str(marker)!r}, 'w')"]
    assert run.main(argv) == 2
    assert not marker.exists()
    assert lines() == []
    assert "usage: moxi run" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["--record", "demo/backup", "--condition"],                         # no --if
    ["--if", "demo.rows.backup", "--record", "demo/backup", "--condition", "--restart"],
    ["--if", "demo.rows.backup", "--record", "demo/backup", "--condition", "--result"],
    ["--if", "demo.rows.backup", "--record", "demo/backup", "--condition", "--", PY],
    ["--if", "demo.rows.backup", "--record", "demo/bad name", "--condition"],
])
def test_a_bad_condition_argument_list_exits_255_with_no_run_line(demo, argv):
    assert run.main(argv) == 255
    assert lines() == []


@pytest.mark.parametrize("argv", [
    ["--if", "demo.rows.backup", "--record", "demo/backup", "--result"],
    ["--record", "demo/backup", "--result", "--log", "/tmp/x"],
    ["--record", "demo/backup", "--result", "--", PY],
    ["--record", "demo", "--result"],
])
def test_a_bad_result_argument_list_still_exits_0(demo, argv):
    assert run.main(argv) == 0
    assert lines() == []


def test_the_forms_parse():
    args = run.parse(["--if", "demo.rows.backup", "--record", "demo/backup", "--condition"])
    assert (args.condition, args.switch, args.unit) == (True, ("demo", "rows.backup"),
                                                        "demo/backup")
    args = run.parse(["--record", "demo/backup", "--result"])
    assert (args.result, args.switch) == (True, None)
    args = run.parse(["--record", "comms/daemon", "--log", str(Path("/x/daemon.log").absolute()),
                      "--restart", "--runtime", "--stop", "process", "--", "a", "--b"])
    assert (args.restart, args.runtime, args.stop, args.argv) == (True, True, "process",
                                                                  ("a", "--b"))
    assert run.parse(["--record", "a/b", "--", "c"]).stop == "group"


def test_help_runs_nothing(capsys):
    assert run.main(["--help"]) == 0
    assert "usage: moxi run" in capsys.readouterr().out


def test_moxi_run_is_the_gate(demo, capsys):
    assert cli.main(["run", "--record", "demo/snapshot", "--", PY, "-c", "pass"]) == 0
    assert [r["result"] for r in lines()] == ["ran"]
    assert cli.main(["run"]) == 2


# -- the switch, against moxi.config in every state --------------------------------------

def settings():
    return config.load("demo", Schema.parse(SCHEMA))


def both(key: str):
    """The gate's read and moxi.config's of switch `key`; config's is "fail" on a failed
    check."""
    on, _why = run.read_switch("demo", key)
    try:
        value = settings()[key]
    except ConfigError:
        value = "fail"
    return on, value


@pytest.mark.parametrize("key", ["snapshot.enabled", "rows.backup"])
def test_the_gate_and_moxi_config_agree_in_every_state(demo, key):
    assert both(key) == (False, False)                          # no file
    write_config(demo, "")
    assert both(key) == (False, False)                          # no key, no table
    if key == "rows.backup":
        write_config(demo, "[rows]\nother = true\n")
        assert both(key) == (False, False)                      # no entry
    config.set("demo", key, "yes", by="cli")
    assert both(key) == (True, True)
    config.set("demo", key, "no", by="cli")
    assert both(key) == (False, False)
    config.unset("demo", key, by="cli")
    assert both(key) == (False, False)                          # unset


@pytest.mark.parametrize("text,key", [
    ('[snapshot]\nenabled = "yes"\n', "snapshot.enabled"),
    ("snapshot = 3\n", "snapshot.enabled"),
    ('[rows]\nbackup = "yes"\n', "rows.backup"),
    ("rows = 3\n", "rows.backup"),
])
def test_a_hand_written_bad_value_is_unreadable_at_the_gate_and_fails_in_config(
        demo, text, key):
    write_config(demo, text)
    assert both(key) == (None, "fail")


def test_a_file_that_wont_parse_is_unreadable(demo):
    write_config(demo, "[snapshot\n")
    on, why = run.read_switch("demo", "snapshot.enabled")
    assert on is None and "not valid TOML" in why


# -- the first form ----------------------------------------------------------------------

def test_off_writes_one_off_line_and_runs_nothing(demo, tmp_path):
    marker = tmp_path / "ran"
    code = run.main(["--if", "demo.snapshot.enabled", "--record", "demo/snapshot", "--",
                     PY, "-c", f"open({str(marker)!r}, 'w')"])
    assert code == 0 and not marker.exists()
    [line] = lines()
    assert line["unit"] == "demo/snapshot" and line["result"] == "off"
    assert line["exit"] == 0 and set(line) == {"unit", "at", "seconds", "result", "exit"}


def test_unreadable_exits_3_with_one_line(demo, capsys):
    write_config(demo, "[snapshot]\nenabled = 1\n")
    code = run.main(["--if", "demo.snapshot.enabled", "--record", "demo/snapshot", "--",
                     PY, "-c", "pass"])
    assert code == 3
    assert [(r["result"], r["exit"]) for r in lines()] == [("unreadable", 3)]
    assert "not true or false" in capsys.readouterr().err


def test_on_runs_the_command_and_exits_with_its_code(demo):
    config.set("demo", "snapshot.enabled", True, by="cli")
    code = run.main(["--if", "demo.snapshot.enabled", "--record", "demo/snapshot", "--",
                     *py("import time, sys; time.sleep(0.2); sys.exit(4)")])
    assert code == 4
    [line] = lines()
    assert (line["result"], line["exit"]) == ("ran", 4)
    assert line["seconds"] >= 0.2
    assert line["at"].endswith("Z") and len(line["at"]) == 20


def test_without_if_the_command_just_runs(demo):
    assert run.main(["--record", "demo/user-row", "--", PY, "-c", "pass"]) == 0
    assert [(r["unit"], r["result"]) for r in lines()] == [("demo/user-row", "ran")]


def test_a_command_that_cannot_start_is_127(demo, tmp_path):
    code = run.main(["--record", "demo/snapshot", "--", str(tmp_path / "missing")])
    assert code == 127
    assert [(r["result"], r["exit"]) for r in lines()] == [("not-started", 127)]


def test_the_command_has_the_gates_stdio(demo):
    done = gate("--record", "demo/snapshot", "--",
                *py("import sys; print('out'); print('err', file=sys.stderr)"))
    assert done.returncode == 0
    assert done.stdout == "out\n" and done.stderr == "err\n"


@POSIX
def test_a_command_ended_by_a_signal_is_128_plus_its_number(demo):
    code = run.main(["--record", "demo/snapshot", "--",
                     *py("import os, signal; os.kill(os.getpid(), signal.SIGTERM)")])
    assert code == 128 + signal.SIGTERM
    assert [(r["result"], r["exit"]) for r in lines()] == [("ran", 143)]


def test_an_unwritable_run_record_changes_no_exit(demo, capsys):
    state = run.runs_path("demo")
    state.mkdir(parents=True)                  # a directory where the record would be
    assert run.main(["--record", "demo/snapshot", "--", PY, "-c", "raise SystemExit(5)"]) == 5
    assert "cannot write the run line" in capsys.readouterr().err


@POSIX
def test_sigterm_reaches_the_command_and_the_run_is_recorded(demo, tmp_path):
    ready = tmp_path / "ready"
    child = py(f"""
        import pathlib, time
        pathlib.Path({str(ready)!r}).write_text("x")
        time.sleep(30)
        """)
    proc = subprocess.Popen([*GATE, "--record", "demo/daemon", "--", *child])
    _wait_for(ready.exists)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=20) == 143
    assert [(r["result"], r["exit"]) for r in lines()] == [("ran", 143)]


def _wait_for(check, seconds: float = 20.0) -> None:
    end = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)


# -- --log -------------------------------------------------------------------------------

def test_log_takes_stdout_and_stderr(demo, tmp_path):
    log = run.log_path("demo", "daemon")
    done = gate("--record", "demo/daemon", "--log", str(log), "--",
                *py("import sys; print('out', flush=True); print('err', file=sys.stderr)"))
    assert done.returncode == 0 and done.stdout == ""
    assert sorted(log.read_text().split()) == ["err", "out"]
    assert log.parent == paths.Layout("demo").state_dir() / "logs"


def test_log_rolls_past_its_cap(demo, monkeypatch):
    monkeypatch.setattr(run, "LOG_CAP", 1000)
    log = run.log_path("demo", "daemon")
    code = run.main(["--record", "demo/daemon", "--log", str(log), "--",
                     *py("""
                         import sys
                         for i in range(30):
                             sys.stdout.write("x" * 99 + "\\n"); sys.stdout.flush()
                         """)])
    assert code == 0
    older = log.with_name("daemon.log.1")
    assert older.stat().st_size == 1000                  # rolled when it reached the cap
    assert log.stat().st_size == 1000                    # rolls on the next write


def test_a_command_that_cannot_start_says_so_in_the_log(demo, tmp_path):
    log = run.log_path("demo", "daemon")
    missing = tmp_path / "missing"
    assert run.main(["--record", "demo/daemon", "--log", str(log), "--", str(missing)]) == 127
    assert f"cannot start {missing}" in log.read_text()


# -- --runtime ---------------------------------------------------------------------------

def test_runtime_file_names_the_running_command_and_goes_with_it(demo, tmp_path):
    path = run.runtime_path("demo", "daemon")
    seen = tmp_path / "seen"
    code = run.main(["--record", "demo/daemon", "--runtime", "--",
                     *py(f"""
                         import os, pathlib, time
                         p = pathlib.Path({str(path)!r})
                         for _ in range(200):
                             if p.exists():
                                 break
                             time.sleep(0.02)
                         pathlib.Path({str(seen)!r}).write_text(p.read_text() + " " + str(os.getpid()))
                         """)])
    assert code == 0
    text, pid = seen.read_text().rsplit(" ", 1)
    recorded = json.loads(text)
    assert recorded["pid"] == int(pid) and isinstance(recorded["started"], float)
    assert not path.exists()
    assert path.parent == paths.core().runtime_dir() / "units"


# -- --restart ---------------------------------------------------------------------------

def test_restart_writes_a_line_per_exit_before_each_wait(demo, tmp_path, monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(run._Stopper, "sleep", lambda self, s: waits.append(s) or False)
    count = tmp_path / "count"
    seen = tmp_path / "seen"
    runs = run.runs_path("demo")
    child = py(f"""
        import pathlib, sys
        c = pathlib.Path({str(count)!r}); n = int(c.read_text()) if c.exists() else 0
        c.write_text(str(n + 1))
        lines = pathlib.Path({str(runs)!r})
        with open({str(seen)!r}, "a") as fh:
            fh.write(str(len(lines.read_text().splitlines()) if lines.exists() else 0) + "\\n")
        sys.exit(0 if n == 3 else 3)
        """)
    assert run.main(["--record", "demo/daemon", "--restart", "--", *child]) == 0
    assert [(r["result"], r["exit"]) for r in lines()] == [("ran", 3)] * 3 + [("ran", 0)]
    assert seen.read_text().split() == ["0", "1", "2", "3"]   # each line before the next run
    assert waits == [2.0, 4.0, 8.0]


def test_restart_waits_double_to_60_and_start_again_after_ten_minutes_up(demo, monkeypatch):
    waits: list[float] = []
    lasted = iter([1, 1, 1, 1, 1, 1, 1, 700, 1, 1, 0])
    codes = iter([1] * 10 + [0])

    def once(args, stopper):
        return next(codes), next(lasted)

    monkeypatch.setattr(run, "_once", once)
    monkeypatch.setattr(run._Stopper, "sleep", lambda self, s: waits.append(s) or False)
    assert run.main(["--record", "demo/daemon", "--restart", "--", "x"]) == 0
    assert waits == [2, 4, 8, 16, 32, 60, 60, 2, 4, 8]


def test_without_restart_a_failing_command_runs_once(demo):
    assert run.main(["--record", "demo/daemon", "--", PY, "-c", "raise SystemExit(1)"]) == 1
    assert len(lines()) == 1


@POSIX
def test_a_stop_signal_ends_the_restart_loop(demo, tmp_path):
    ready = tmp_path / "ready"
    child = py(f"""
        import pathlib, time
        pathlib.Path({str(ready)!r}).write_text("x")
        time.sleep(30)
        """)
    proc = subprocess.Popen([*GATE, "--record", "demo/daemon", "--restart", "--", *child])
    _wait_for(ready.exists)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=20) == 143
    assert [(r["result"], r["exit"]) for r in lines()] == [("ran", 143)]


@POSIX
def test_a_stop_signal_during_the_wait_ends_the_gate(demo, tmp_path):
    proc = subprocess.Popen([*GATE, "--record", "demo/daemon", "--restart", "--",
                             PY, "-c", "raise SystemExit(7)"])
    _wait_for(lambda: len(lines()) >= 1)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=20) == 7
    assert [r["exit"] for r in lines()] == [7]


# -- Windows: the job ------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows's")
@pytest.mark.parametrize("stop,survives", [("group", False), ("process", True)])
def test_ending_the_gate_ends_the_job_unless_a_child_broke_away(demo, tmp_path, stop,
                                                               survives):
    from moxi import proc as mproc

    pidfile = tmp_path / "grandchild"
    child = py(f"""
        import pathlib, sys, time
        from moxi import proc
        pid = proc.spawn([sys.executable, "-c", "import time; time.sleep(60)"],
                         detached=True)
        pathlib.Path({str(pidfile)!r}).write_text(str(pid))
        time.sleep(60)
        """)
    gate_proc = subprocess.Popen([*GATE, "--record", "demo/daemon", "--stop", stop, "--",
                                  *child])
    _wait_for(pidfile.exists)
    grandchild = mproc.identity(int(pidfile.read_text()))
    gate_proc.kill()
    gate_proc.wait(timeout=20)
    time.sleep(1)
    try:
        assert mproc.alive(grandchild) is survives
    finally:
        if mproc.alive(grandchild):
            os.kill(grandchild.pid, signal.SIGTERM)


# -- the wrap forms ----------------------------------------------------------------------

COND = ["--if", "demo.rows.backup", "--record", "demo/backup", "--condition"]
RESULT = ["--record", "demo/backup", "--result"]


def stamp() -> Path:
    return run.stamp_path("demo", "backup")


def test_condition_off_writes_off_and_exits_1(demo):
    assert run.main(COND) == 1
    assert [(r["unit"], r["result"], r["exit"]) for r in lines()] == [
        ("demo/backup", "off", 0)]
    assert not stamp().exists()


def test_condition_on_writes_the_stamp_and_exits_0(demo):
    config.set("demo", "rows.backup", True, by="cli")
    assert run.main(COND) == 0
    assert lines() == []
    held = json.loads(stamp().read_text())
    assert held["at"].endswith("Z") and isinstance(held["mono"], float)
    assert stamp().parent == paths.core().runtime_dir() / "units"


@pytest.mark.parametrize("text", ["[rows\n", '[rows]\nbackup = "yes"\n', "rows = 1\n"])
def test_condition_unreadable_writes_unreadable_and_exits_255(demo, text):
    write_config(demo, text)
    assert run.main(COND) == 255
    assert [(r["result"], r["exit"]) for r in lines()] == [("unreadable", 3)]


def test_condition_removes_a_stale_stamp_first(demo):
    files.atomic_write(stamp(), json.dumps({"at": "2026-01-01T00:00:00Z", "mono": 1.0}))
    assert run.main(COND) == 1                 # off
    assert not stamp().exists()
    assert run.main(RESULT) == 0             # so --result counts nothing more
    assert [r["result"] for r in lines()] == ["off"]


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="POSIX permission bits; root writes anyway")
def test_condition_that_cannot_write_its_stamp_is_not_started_and_255(demo):
    config.set("demo", "rows.backup", True, by="cli")
    units = stamp().parent
    units.mkdir(parents=True)
    units.chmod(0o500)
    try:
        assert run.main(COND) == 255
    finally:
        units.chmod(0o700)
    assert [(r["result"], r["exit"]) for r in lines()] == [("not-started", 127)]


def test_result_with_no_stamp_writes_nothing(demo, monkeypatch):
    monkeypatch.setenv("SERVICE_RESULT", "success")
    monkeypatch.setenv("EXIT_CODE", "exited")
    monkeypatch.setenv("EXIT_STATUS", "0")
    assert run.main(RESULT) == 0
    assert lines() == []


def started(seconds_ago: float = 0.5) -> None:
    files.atomic_write(stamp(), json.dumps({"at": "2026-10-30T09:00:00Z",
                                            "mono": time.monotonic() - seconds_ago}))


@pytest.mark.parametrize("variables,want", [
    ({"SERVICE_RESULT": "exit-code", "EXIT_CODE": "exited", "EXIT_STATUS": "4"}, ("ran", 4)),
    ({"SERVICE_RESULT": "success", "EXIT_CODE": "exited", "EXIT_STATUS": "0"}, ("ran", 0)),
    ({"SERVICE_RESULT": "signal", "EXIT_CODE": "killed", "EXIT_STATUS": "TERM"}, ("ran", 143)),
    ({"SERVICE_RESULT": "core-dump", "EXIT_CODE": "dumped", "EXIT_STATUS": "SEGV"},
     ("ran", 128 + int(signal.SIGSEGV))),
    ({"SERVICE_RESULT": "signal", "EXIT_CODE": "killed", "EXIT_STATUS": "9"}, ("ran", 137)),
    ({"SERVICE_RESULT": "resources"}, ("not-started", 127)),
])
def test_result_with_a_stamp_writes_the_line_from_systemd(demo, monkeypatch, variables,
                                                          want):
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    started(0.5)
    assert run.main(RESULT) == 0
    [line] = lines()
    assert (line["result"], line["exit"]) == want
    assert line["at"] == "2026-10-30T09:00:00Z" and line["seconds"] >= 0.5
    assert line["service_result"] == variables["SERVICE_RESULT"]
    assert not stamp().exists()


def test_result_after_a_later_condition_skipped_writes_nothing(demo, monkeypatch):
    monkeypatch.setenv("SERVICE_RESULT", "exec-condition")
    monkeypatch.setenv("EXIT_CODE", "exited")
    monkeypatch.setenv("EXIT_STATUS", "1")
    started()
    assert run.main(RESULT) == 0
    assert lines() == [] and not stamp().exists()


def test_result_with_a_stamp_it_cannot_read_writes_nothing_and_exits_0(demo):
    files.atomic_write(stamp(), "not json")
    assert run.main(RESULT) == 0
    assert lines() == [] and not stamp().exists()


def test_an_off_run_is_counted_once(demo, monkeypatch):
    assert run.main(COND) == 1
    monkeypatch.setenv("SERVICE_RESULT", "exec-condition")
    assert run.main(RESULT) == 0
    assert [r["result"] for r in lines()] == ["off"]


def test_an_on_run_is_counted_once_as_systemd_runs_the_pair(demo):
    config.set("demo", "rows.backup", True, by="cli")
    assert gate(*COND).returncode == 0
    env = {**os.environ, "SERVICE_RESULT": "exit-code", "EXIT_CODE": "exited",
           "EXIT_STATUS": "4"}
    assert gate(*RESULT, env=env).returncode == 0
    [line] = lines()
    assert (line["unit"], line["result"], line["exit"]) == ("demo/backup", "ran", 4)


# -- the core's stores, and the import cost ----------------------------------------------

def test_the_core_store_names_agree():
    from moxi import doctor, modules

    assert run.RUNS_FILE in modules.CORE_STATE_FILES
    assert run.RUNS_FILE + ".1" in modules.CORE_STATE_FILES
    assert doctor.RECORDS_FILE in modules.CORE_STATE_FILES
    assert modules.CORE_STATE_DIRS == (run.LOGS_DIR,)
    assert run.runs_path("demo") == paths.Layout("demo").state_dir() / "runs.jsonl"


def test_the_gate_imports_only_what_it_needs():
    code = textwrap.dedent("""\
        import sys
        import moxi.run
        heavy = ("psutil", "tomlkit", "moxi._vendor.tomlkit", "packaging", "asyncio",
                 "sqlite3", "moxi.config", "moxi.modules", "moxi.units", "moxi.doctor",
                 "argparse", "moxi.proc")
        print(" ".join(m for m in heavy if m in sys.modules))
        """)
    done = subprocess.run([PY, "-I", "-c", code], capture_output=True, text=True,
                          check=True)
    assert done.stdout.strip() == ""
