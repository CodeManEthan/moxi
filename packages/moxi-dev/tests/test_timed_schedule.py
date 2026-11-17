"""`moxi-dev timed schedule` and `unschedule` (`moxi_dev.schedule`).

Every run uses an environment whose PATH holds one directory: stand-ins for `systemctl`,
`systemd-analyze`, `loginctl`, `uv` and the Python it finds, which log each call, and a
link to the real git. The unit and cache directories are under tmp, so no test reaches the
user's own manager, units or cache.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from moxi_dev import schedule, tick, timed
from test_check import git

FAKE = f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
here = Path(__file__).parent
prog = Path(sys.argv[0]).name
args = sys.argv[1:]
conf = json.loads((here / "fake.json").read_text()).get(prog, {{}})
with open(here / "calls.jsonl", "a") as fh:
    fh.write(json.dumps({{"prog": prog, "args": args, "cwd": os.getcwd()}}) + "\\n")
active_file = here / "active.json"
active = set(json.loads(active_file.read_text())) if active_file.exists() else set()
if prog == "systemctl" and "is-active" in args:
    unit = args[-1]
    print("active" if unit in active else "inactive")
    sys.exit(0 if unit in active else 3)
if prog == "systemctl" and args[1:3] in (["enable", "--now"], ["disable", "--now"]):
    units = set(args[3:])
    active = active | units if args[1] == "enable" else active - units
    active_file.write_text(json.dumps(sorted(active)))
if prog == "uv" and args[:2] == ["python", "find"]:
    print(conf.get("python", str(here / "python3.12")))
    sys.exit(0)
if prog.startswith("python") and args == ["--version"]:
    print("Python " + conf.get("version", "3.12.99"))
    sys.exit(0)
if prog == "uv" and args[:1] == ["sync"]:
    conf = conf.get("sync", {{}})
sys.stdout.write(conf.get("stdout", ""))
sys.stderr.write(conf.get("stderr", ""))
sys.exit(conf.get("exit", 0))
"""
PROGRAMS = ("systemctl", "systemd-analyze", "loginctl", "uv", "python3.12")


class Fakes:
    def __init__(self, bin_dir: Path):
        self.bin_dir = bin_dir
        self.conf: dict = {"loginctl": {"stdout": "Linger=yes\n"}}
        self.save()

    def save(self):
        (self.bin_dir / "fake.json").write_text(json.dumps(self.conf))

    def answer(self, prog, reply):
        """What the stand-in `prog` answers from now on, merged into what it answered."""
        self.conf[prog] = {**self.conf.get(prog, {}), **reply}
        self.save()

    def calls(self, prog=None):
        log = self.bin_dir / "calls.jsonl"
        rows = [json.loads(line) for line in log.read_text().splitlines()] \
            if log.exists() else []
        return [row for row in rows if prog is None or row["prog"] == prog]

    def clear(self):
        (self.bin_dir / "calls.jsonl").unlink(missing_ok=True)

    def active(self):
        held = self.bin_dir / "active.json"
        return set(json.loads(held.read_text())) if held.exists() else set()


@pytest.fixture
def fakes(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "fake"
    script.write_text(FAKE)
    script.chmod(0o755)
    for name in PROGRAMS:
        (bin_dir / name).symlink_to(script)
    (bin_dir / "git").symlink_to(shutil.which("git"))
    return Fakes(bin_dir)


@pytest.fixture
def env(tmp_path, fakes):
    return {"PATH": str(fakes.bin_dir), "HOME": str(tmp_path / "home"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"), "USER": "tester",
            "GIT_CONFIG_NOSYSTEM": "1"}


WORKSPACE = '[project]\nname = "ws"\nversion = "0"\n\n[tool.uv.workspace]\nmembers = []\n'


@pytest.fixture
def clone(tmp_path):
    root = tmp_path / "clone"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text("/.moxi-dev/\n/.moxi-dev-stamp.json\n")
    (root / "pyproject.toml").write_text(WORKSPACE)
    (root / "a.txt").write_text("alpha\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    return root


@pytest.fixture
def synced(tmp_path, clone):
    """A copy of the clone with no git, holding the clone's stamp."""
    tick.write_stamp("git", clone)
    root = tmp_path / "synced"
    shutil.copytree(clone, root, ignore=shutil.ignore_patterns(".git", ".moxi-dev"))
    return root


def units_dir(env):
    return Path(env["XDG_CONFIG_HOME"]) / "systemd" / "user"


def unit_files(env):
    where = units_dir(env)
    return sorted(path.name for path in where.iterdir() if path.is_file()) \
        if where.exists() else []


def run_dir(source, env):
    return schedule.run_dir_of(source, env)


def do_schedule(source, env, *args, machine="host-a"):
    return schedule.schedule_main(["--machine", machine, *args], env=env, start=source)


def unit_values(text, key):
    return [line.split("=", 1)[1] for line in text.splitlines() if line.startswith(key + "=")]


def exec_argv(value):
    """An `ExecStart=` value as systemd splits it: words quoted with `"` and backslash
    escapes, and `$$` and `%%` read back as one character."""
    words = []
    for quoted, bare in re.findall(r'"((?:[^"\\]|\\.)*)"|(\S+)', value):
        word = re.sub(r"\\(.)", r"\1", quoted) if quoted or not bare else bare
        words.append(word.replace("$$", "$").replace("%%", "%"))
    return words


def snapshot(*paths):
    """Each file's bytes and mtime under each path, to show nothing changed."""
    out = {}
    for root in paths:
        if not root.exists():
            continue
        for path in sorted([root, *root.rglob("*")]):
            if path.is_file() and not path.is_symlink():
                stat = path.stat()
                out[str(path)] = (path.read_bytes(), stat.st_mtime_ns)
    return out


@pytest.fixture(autouse=True)
def linux(monkeypatch):
    monkeypatch.setattr(schedule, "_system", lambda: "linux")


# --- schedule --------------------------------------------------------------------------

def test_schedule_on_a_clone_makes_the_worktree_and_writes_the_timer_and_service(
        clone, env, fakes, capsys):
    assert do_schedule(clone, env) == 0
    out = capsys.readouterr().out
    where = run_dir(clone, env)
    tree = where / "tree"
    assert where.parent == Path(env["XDG_CACHE_HOME"]) / "moxi-dev" / "timed"
    assert re.fullmatch(r"[0-9a-f]{12}", where.name)
    assert str(tree) in git(clone, "worktree", "list")
    assert git(tree, "rev-parse", "HEAD") == git(clone, "rev-parse", "main")
    assert unit_files(env) == [schedule.SERVICE, schedule.TIMER]
    timer = (units_dir(env) / schedule.TIMER).read_text()
    assert timer.startswith(f"{schedule.MARK}{where}\n")
    assert unit_values(timer, "OnCalendar") == ["*-*-* 08..11:00:00"]
    assert unit_values(timer, "Persistent") == ["false"]
    service = (units_dir(env) / schedule.SERVICE).read_text()
    assert unit_values(service, "Type") == ["oneshot"]
    assert unit_values(service, "WorkingDirectory") == [str(where)]
    assert unit_values(service, "SuccessExitStatus") == ["3"]
    assert unit_values(service, "TimeoutStartSec") == ["40min"]
    argv = exec_argv(unit_values(service, "ExecStart")[0])
    python = str(fakes.bin_dir / "python3.12")
    assert argv[:2] == ["/bin/sh", "-c"]
    assert argv[3:] == [python, str(where / "tick.py"), "host-a", "3.12.99"]
    assert (where / "tick.py").read_bytes() == Path(tick.__file__).read_bytes()
    sync = [row for row in fakes.calls("uv") if row["args"][:1] == ["sync"]]
    assert [(row["args"], row["cwd"]) for row in sync] == [(["sync", "--locked"], str(tree))]
    verified = [row["args"][-1] for row in fakes.calls("systemd-analyze")]
    assert sorted(verified) == sorted(str(units_dir(env) / name)
                                      for name in (schedule.TIMER, schedule.SERVICE))
    assert all(row["args"][:2] == ["--user", "verify"] for row in fakes.calls("systemd-analyze"))
    assert [row["args"] for row in fakes.calls("systemctl")] == [
        ["--user", "daemon-reload"], ["--user", "enable", "--now", schedule.TIMER]]
    assert fakes.active() == {schedule.TIMER}
    assert "systemctl --user start --no-block moxi-dev-timed.service" in out
    assert "systemctl --user is-active moxi-dev-timed.service" in out
    assert "journalctl --user -u moxi-dev-timed -n 5" in out.splitlines()[-1]


def test_schedule_json_holds_the_ticks_fields_and_reads_back_through_the_tick(
        clone, env, fakes, tmp_path):
    results = tmp_path / "results-b"
    results.mkdir()
    assert do_schedule(clone, env, "--slack", "2.5", "--idle-line", "1.5",
                       "--take", f"host-b={results}") == 0
    where = run_dir(clone, env)
    data = json.loads((where / tick.SCHEDULE_JSON).read_text())
    assert set(data) == {"format", "machine", "source", "has_git", "record_dir", "slack",
                         "idle_line", "takes", "stamp", "uv", "git", "python",
                         "python_version"}
    assert data["python_version"] == "3.12.99"
    assert data["python"] == str(fakes.bin_dir / "python3.12")
    assert data["uv"] == str(fakes.bin_dir / "uv")
    assert data["git"] == str(fakes.bin_dir / "git")
    settings = tick.read_settings(where)
    assert settings.machine == "host-a" and settings.source == clone
    assert settings.has_git and settings.record_dir is None
    assert settings.slack == 2.5 and settings.idle_line == 1.5
    assert settings.takes == {"host-b": results}
    assert not settings.stamp
    assert settings.tree == where / "tree"


def test_tick_and_settings_are_written_part_file_then_rename(clone, env, monkeypatch):
    renamed = []
    real_replace = os.replace
    monkeypatch.setattr(schedule.os, "replace",
                        lambda src, dst: (renamed.append((Path(src).name, Path(dst))),
                                          real_replace(src, dst))[1])
    assert do_schedule(clone, env) == 0
    where = run_dir(clone, env)
    pid = os.getpid()
    assert (f"tick.py.{pid}.part", where / "tick.py") in renamed
    assert (f"schedule.json.{pid}.part", where / "schedule.json") in renamed
    assert not list(where.glob("*.part"))


def test_schedule_stamp_adds_the_path_unit_on_mains_reflog(clone, env, fakes):
    assert do_schedule(clone, env, "--stamp") == 0
    where = run_dir(clone, env)
    assert unit_files(env) == sorted(schedule.UNITS)
    path_unit = (units_dir(env) / schedule.STAMP_PATH).read_text()
    common = Path(git(clone, "rev-parse", "--path-format=absolute",
                      "--git-common-dir").strip())
    assert unit_values(path_unit, "PathChanged") == [
        str(common / "logs" / "refs" / "heads" / "main")]
    assert unit_values(path_unit, "Unit") == [schedule.STAMP_SERVICE]
    stamp_service = (units_dir(env) / schedule.STAMP_SERVICE).read_text()
    argv = exec_argv(unit_values(stamp_service, "ExecStart")[0])
    assert argv[2].endswith('exec "$0" -I "$1" --stamp-only')
    assert argv[4] == str(where / "tick.py")
    assert fakes.active() == {schedule.TIMER, schedule.STAMP_PATH}
    assert tick.read_settings(where).stamp


def test_schedule_on_a_tree_with_no_git_makes_the_first_verified_copy(
        synced, clone, env, tmp_path):
    record = tmp_path / "results"
    assert do_schedule(synced, env, "--record-dir", str(record)) == 0
    where = run_dir(synced, env)
    tree = where / "tree"
    commit = git(clone, "rev-parse", "main").strip()
    assert json.loads((tree / tick.TREE_COMMIT).read_text()) == {"format": 1,
                                                                 "commit": commit}
    assert (tree / "a.txt").read_text() == "alpha\n"
    assert not (tree / tick.STAMP_NAME).exists()
    settings = tick.read_settings(where)
    assert not settings.has_git and settings.git is None
    assert settings.record_dir == record
    assert unit_files(env) == [schedule.SERVICE, schedule.TIMER]


@pytest.mark.parametrize("args, says", [
    ((), "--record-dir"),
    (("--record-dir", "results", "--stamp"), "--stamp needs a git clone"),
])
def test_a_tree_with_no_git_requires_record_dir_and_refuses_stamp(
        synced, env, fakes, capsys, args, says):
    assert do_schedule(synced, env, *args) == 2
    assert says in capsys.readouterr().err
    assert unit_files(env) == [] and not run_dir(synced, env).exists()
    assert fakes.calls("systemctl") == []


def test_a_synced_tree_off_its_stamp_exits_2_and_leaves_nothing(synced, env, fakes, capsys,
                                                                tmp_path):
    (synced / "a.txt").write_text("half synced\n")
    assert do_schedule(synced, env, "--record-dir", str(tmp_path / "r")) == 2
    err = capsys.readouterr().err
    assert "the synced tree doesn't match its stamp yet (differs a.txt)" in err
    assert "run schedule again once the sync is idle" in err
    assert unit_files(env) == [] and not run_dir(synced, env).exists()


def _no_workspace(clone, env, fakes, tmp_path):
    (clone / "pyproject.toml").write_text('[project]\nname = "x"\n')
    return clone


def _below_top(clone, env, fakes, tmp_path):
    inner = clone / "inner"
    inner.mkdir()
    (inner / "pyproject.toml").write_text(WORKSPACE)
    return inner


def _no_uv(clone, env, fakes, tmp_path):
    (fakes.bin_dir / "uv").unlink()
    return clone


def _no_git(clone, env, fakes, tmp_path):
    (fakes.bin_dir / "git").unlink()
    return clone


def _python_311(clone, env, fakes, tmp_path):
    fakes.answer("python3.12", {"version": "3.11.9"})
    return clone


def _no_reflog(clone, env, fakes, tmp_path):
    shutil.rmtree(clone / ".git" / "logs")
    return clone


def _uv_sync_fails(clone, env, fakes, tmp_path):
    fakes.answer("uv", {"sync": {"exit": 1, "stderr": "error: lock out of date\n"}})
    return clone


def _verify_fails(clone, env, fakes, tmp_path):
    fakes.answer("systemd-analyze", {"exit": 1, "stderr": "bad unit\n"})
    return clone


@pytest.mark.parametrize("arrange, args, says", [
    (_no_workspace, (), "is not a uv workspace root"),
    (_below_top, (), "is not its clone's top level"),
    (_no_uv, (), "uv is not on PATH"),
    (_no_git, (), "git is not on PATH"),
    (_python_311, (), "is not Python 3.12"),
    (_no_reflog, ("--stamp",), "this clone has none"),
    (None, ("--take", "host-b=/nonexistent/results"), "is not a folder"),
    (_uv_sync_fails, (), "lock out of date"),
    (_verify_fails, ("--stamp",), "bad unit"),
])
def test_each_check_that_fails_exits_2_and_leaves_no_unit(
        clone, env, fakes, tmp_path, capsys, arrange, args, says):
    source = arrange(clone, env, fakes, tmp_path) if arrange else clone
    assert do_schedule(source, env, *args) == 2
    assert says in capsys.readouterr().err
    assert unit_files(env) == []
    assert not run_dir(source, env).exists()
    assert str(run_dir(source, env)) not in git(clone, "worktree", "list")
    assert [row for row in fakes.calls("systemctl")] == []


def test_a_failed_verify_on_schedule_again_puts_the_units_back(clone, env, fakes):
    assert do_schedule(clone, env) == 0
    before = snapshot(units_dir(env))
    fakes.answer("systemd-analyze", {"exit": 1, "stderr": "bad unit\n"})
    fakes.clear()
    assert do_schedule(clone, env, "--slack", "3", "--stamp") == 2
    assert {path: data for path, (data, _) in snapshot(units_dir(env)).items()} == \
        {path: data for path, (data, _) in before.items()}
    assert tick.read_settings(run_dir(clone, env)).slack == 1
    assert fakes.calls("systemctl") == []


@pytest.mark.parametrize("held, says", [("Linger=no\n", True), ("Linger=yes\n", False)])
def test_linger_off_prints_its_line_and_goes_on(clone, env, fakes, capsys, held, says):
    fakes.answer("loginctl", {"stdout": held})
    assert do_schedule(clone, env) == 0
    assert (schedule.LINGER_LINE in capsys.readouterr().out) is says
    assert fakes.calls("loginctl")[0]["args"] == ["show-user", "tester", "--property=Linger"]


@pytest.mark.parametrize("system, says", [
    ("darwin", "on macOS run `moxi-dev timed` by hand"),
    ("win32", "a Windows machine's scheduled run"),
])
@pytest.mark.parametrize("verb", ["schedule", "unschedule"])
def test_macos_and_windows_refuse(clone, env, fakes, monkeypatch, capsys, system, says, verb):
    monkeypatch.setattr(schedule, "_system", lambda: system)
    if verb == "schedule":
        code = do_schedule(clone, env)
    else:
        code = schedule.unschedule_main([], env=env, start=clone)
    assert code == 2
    err = capsys.readouterr().err
    assert "the timer is Linux only" in err and says in err
    assert fakes.calls() == [] and unit_files(env) == []


def test_schedule_again_rewrites_its_own_files_and_leaves_a_drop_in(clone, env, fakes):
    assert do_schedule(clone, env, "--stamp") == 0
    drop_in = units_dir(env) / f"{schedule.SERVICE}.d"
    drop_in.mkdir()
    (drop_in / "50-note.conf").write_text("[Unit]\nOnFailure=note.service\n")
    held = snapshot(drop_in)
    where = run_dir(clone, env)
    (where / "tick.py").write_text("# an older tick\n")
    fakes.clear()
    assert do_schedule(clone, env, "--slack", "3.1") == 0
    assert (where / "tick.py").read_bytes() == Path(tick.__file__).read_bytes()
    assert tick.read_settings(where).slack == 3.1
    assert snapshot(drop_in) == held
    # without --stamp, the stamp units it wrote before are stopped and removed
    assert unit_files(env) == [schedule.SERVICE, schedule.TIMER]
    assert ["--user", "disable", "--now", schedule.STAMP_PATH] in [
        row["args"] for row in fakes.calls("systemctl")]
    assert fakes.active() == {schedule.TIMER}


def test_schedule_from_another_source_takes_the_timer_over_and_says_so(
        clone, synced, env, capsys, tmp_path):
    assert do_schedule(synced, env, "--record-dir", str(tmp_path / "results")) == 0
    old = run_dir(synced, env)
    capsys.readouterr()
    assert do_schedule(clone, env) == 0
    out = capsys.readouterr().out
    assert f"the timer ran from {old}" in out
    service = (units_dir(env) / schedule.SERVICE).read_text()
    assert service.startswith(f"{schedule.MARK}{run_dir(clone, env)}\n")
    assert old.exists()


def hold_run_lock(source, env, *, has_git):
    if has_git:
        lock = Path(git(source, "rev-parse", "--path-format=absolute",
                        "--git-common-dir").strip()) / "moxi-dev" / "timed" / ".run-lock"
    else:
        lock = run_dir(source, env) / "tree" / timed.TREE_TIMED_DIR / timed.RUN_LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


@pytest.mark.parametrize("kind", ["clone", "synced"])
def test_schedule_again_with_the_run_lock_held_exits_2_and_writes_nothing(
        request, env, fakes, capsys, tmp_path, kind):
    source = request.getfixturevalue(kind)
    extra = () if kind == "clone" else ("--record-dir", str(tmp_path / "results"))
    assert do_schedule(source, env, *extra) == 0
    where = run_dir(source, env)
    fd = hold_run_lock(source, env, has_git=kind == "clone")
    before = snapshot(where, units_dir(env))
    fakes.clear()
    capsys.readouterr()
    try:
        assert do_schedule(source, env, *extra, "--slack", "2") == 2
    finally:
        os.close(fd)
    assert "a timed run is measuring here" in capsys.readouterr().err
    assert snapshot(where, units_dir(env)) == before
    assert [row for row in fakes.calls() if row["prog"] != "loginctl"
            and row["args"][:2] != ["python", "find"]
            and row["args"] != ["--version"]] == []
    assert fakes.active() == {schedule.TIMER}


def test_the_service_runs_the_tick_and_with_its_interpreter_gone_says_so(
        clone, env, fakes, tmp_path):
    assert do_schedule(clone, env) == 0
    where = run_dir(clone, env)
    service = (units_dir(env) / schedule.SERVICE).read_text()
    argv = exec_argv(unit_values(service, "ExecStart")[0])
    fakes.clear()
    ran = subprocess.run(argv, cwd=where, capture_output=True, text=True, env=env,
                         check=False)
    assert ran.returncode == 0
    assert [row["args"] for row in fakes.calls("python3.12")] == [
        ["-I", str(where / "tick.py")]]
    python = fakes.bin_dir / "python3.12"
    python.unlink()
    ran = subprocess.run(argv, cwd=where, capture_output=True, text=True, env=env,
                         check=False)
    assert ran.returncode == 2
    assert ran.stdout == (f"moxi-dev timed tick: host-a: interpreter missing: {python} "
                          f"(Python 3.12.99); run schedule again\n")


def test_unit_words_keep_spaces_quotes_and_systemd_marks():
    tricky = '/opt/a dir/100% "py"$HOME\\x'
    line = schedule.exec_line(tricky, Path("/r/tick.py"), "host-a", "3.12.1")
    assert exec_argv(line)[3] == tricky


# --- unschedule ------------------------------------------------------------------------

def test_unschedule_removes_the_units_and_the_run_dir_and_leaves_the_record(
        clone, env, fakes, capsys):
    assert do_schedule(clone, env, "--stamp") == 0
    common = Path(git(clone, "rev-parse", "--path-format=absolute",
                      "--git-common-dir").strip())
    record = common / "moxi-dev" / "timed" / "host-a" / ("0" * 40 + ".json")
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("{}")
    drop_in = units_dir(env) / f"{schedule.SERVICE}.d" / "50-note.conf"
    drop_in.parent.mkdir()
    drop_in.write_text("[Unit]\n")
    where = run_dir(clone, env)
    fakes.clear()
    assert schedule.unschedule_main([], env=env, start=clone) == 0
    assert unit_files(env) == []
    assert not where.exists()
    assert str(where) not in git(clone, "worktree", "list")
    assert record.read_text() == "{}" and drop_in.read_text() == "[Unit]\n"
    assert (clone / "a.txt").read_text() == "alpha\n"
    calls = [row["args"] for row in fakes.calls("systemctl")]
    assert calls == [["--user", "disable", "--now", schedule.TIMER, schedule.STAMP_PATH],
                     ["--user", "daemon-reload"]]
    assert fakes.active() == set()


def test_unschedule_on_a_tree_with_no_git_removes_the_copy(synced, env, tmp_path):
    results = tmp_path / "results"
    assert do_schedule(synced, env, "--record-dir", str(results)) == 0
    assert schedule.unschedule_main([], env=env, start=synced) == 0
    assert not run_dir(synced, env).exists() and unit_files(env) == []
    assert (synced / "a.txt").exists()


@pytest.mark.parametrize("kind", ["clone", "synced"])
def test_unschedule_with_the_run_lock_held_exits_2_and_leaves_the_timer_active(
        request, env, fakes, capsys, tmp_path, kind):
    source = request.getfixturevalue(kind)
    extra = () if kind == "clone" else ("--record-dir", str(tmp_path / "results"))
    assert do_schedule(source, env, *extra) == 0
    where = run_dir(source, env)
    fd = hold_run_lock(source, env, has_git=kind == "clone")
    before = snapshot(where, units_dir(env))
    fakes.clear()
    try:
        assert schedule.unschedule_main([], env=env, start=source) == 2
    finally:
        os.close(fd)
    assert "a timed run is measuring here" in capsys.readouterr().err
    assert fakes.calls("systemctl") == []
    is_active = subprocess.run([str(fakes.bin_dir / "systemctl"), "--user", "is-active",
                                schedule.TIMER], capture_output=True, text=True, check=False)
    assert is_active.stdout.strip() == "active"
    assert snapshot(where, units_dir(env)) == before


def test_unschedule_leaves_units_another_source_wrote(clone, synced, env, tmp_path, capsys):
    assert do_schedule(synced, env, "--record-dir", str(tmp_path / "results")) == 0
    assert schedule.unschedule_main([], env=env, start=clone) == 0
    assert "nothing is scheduled from" in capsys.readouterr().out
    assert unit_files(env) == [schedule.SERVICE, schedule.TIMER]
