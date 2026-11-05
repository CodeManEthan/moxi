"""The walk: what it finds above a run, over process views the tests hand in, and live.

A view here is a table of processes. Each chain is listed from the run up: the run is pid
100, its parent 101, and so on, the last one's parent 0 (no visible parent), each started
a second before the one below it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from moxi import _mark
from moxi.terminal import Evidence, Run, Undecided, walk
from moxi.terminal import _view, _walk
from moxi.terminal._view import Gone, Proc, Unread

RUN = 100
VENV = "/home/ada/.local/share/uv/tools/moxi/bin"
WIN_SCRIPTS = r"C:\Users\ada\.local\bin"
WIN_EXE = r"C:\Users\ada\AppData\Roaming\uv\tools\moxi\Scripts\python.exe"
WIN_BASE = r"C:\Users\ada\AppData\Roaming\uv\python\cpython-3.12\python.exe"
WIN_MOXI = WIN_SCRIPTS + r"\moxi.exe"


class Table:
    """A process view over a table: `read` answers each listed pid, raises `Gone` for an
    unlisted one and what `errors` names for a listed one; `parent` re-reads a pid's
    parent, from `reparented` first."""

    def __init__(self, procs, *, windows=False, loose=False, errors=None, reparented=None):
        self.procs = {p.pid: p for p in procs}
        self.windows = windows
        self.loose = loose
        self.errors = errors or {}
        self.reparented = reparented or {}
        self.reads: list[int] = []
        self.asked_slots = 0

    def read(self, pid):
        self.reads.append(pid)
        if pid in self.errors:
            raise self.errors[pid]
        if pid not in self.procs:
            raise Gone(pid)
        return self.procs[pid]

    def parent(self, pid):
        if pid in self.reparented:
            return self.reparented[pid]
        found = self.procs.get(pid)
        return None if found is None else found.parent

    def slots_loose(self):
        self.asked_slots += 1
        return self.loose


def p(argv, *, env=None, unit=None, image=None, shielded=False, name=""):
    """One process's fields but its pid, parent and start time, which `chain` gives."""
    return dict(argv=None if argv is None else tuple(argv),
                env=None if shielded else ({} if env is None else env), unit=unit,
                image=image, shielded=shielded, name=name)


def chain(*procs, first=RUN, top_parent=0):
    out = []
    for at, fields in enumerate(procs):
        pid = first + at
        parent = pid + 1 if at + 1 < len(procs) else top_parent
        out.append(Proc(pid=pid, parent=parent, started=1000.0 - at, **fields))
    return out


def run(**kw):
    values = dict(pid=RUN, executable=f"{VENV}/python", argv0=f"{VENV}/moxi",
                  started_by=None)
    values.update(kw)
    return Run(**values)


ME = p([f"{VENV}/python", f"{VENV}/moxi", "modules"])
SHELL = p(["-bash"])
TERMINAL = p(["/usr/libexec/gnome-terminal-server"])
USER_MANAGER = p(["/usr/lib/systemd/systemd", "--user"], shielded=True)
INIT = p(["/usr/lib/systemd/systemd", "rhgb", "--switched-root"], shielded=True)
PERSON = (SHELL, TERMINAL, USER_MANAGER, INIT)


def found(*procs, view_kw=None, **run_kw):
    return walk(Table(chain(*procs), **(view_kw or {})), run(**run_kw))


# -- a person's own terminal ---------------------------------------------------------------

def test_a_persons_chain_records():
    assert found(ME, *PERSON) is None


def test_an_ssh_login_and_a_sudo_hop_record():
    sshd = p(["sshd-session: ada@pts/0"], shielded=True)
    sudo = p(["sudo", "-u", "ada", "-i"], shielded=True)
    assert found(ME, SHELL, sudo, SHELL, sshd, p(["sshd: /usr/sbin/sshd -D"],
                                                    shielded=True), INIT) is None


def test_a_shielded_process_with_no_command_line_is_passed():
    assert found(ME, SHELL, p(None, shielded=True), INIT) is None


def test_uv_waiting_above_the_run_passes():
    assert found(ME, p(["uv", "run", "moxi", "modules"]), *PERSON) is None
    assert found(ME, p(["/home/ada/.cargo/bin/uvx", "moxi", "modules"]), *PERSON) is None


def test_the_run_itself_is_never_judged_by_its_command_line():
    assert found(p([f"{VENV}/python", "-m", "moxi", "modules"]), *PERSON) is None


# -- E1 ------------------------------------------------------------------------------------

@pytest.mark.parametrize("argv, detail, module", [
    ([f"{VENV}/python", f"{VENV}/moxi", "config"], "moxi", None),
    (["/usr/bin/python3", "-I", f"{VENV}/moxi-slots-server"], "moxi-slots-server", "slots"),
    ([f"{VENV}/python3.12", "-m", "moxi", "run", "x"], "-m moxi", None),
    (["python3", "-m", "moxi.doctor"], "-m moxi.doctor", None),
    (["python", "-I", "-m", "moxi_repo.run"], "-m moxi_repo.run", "repo"),
    (["python", "-Im", "moxi_loadout.pagejob"], "-m moxi_loadout.pagejob", "loadout"),
    (["python", "-W", "ignore", "-mmoxi_loadout"], "-m moxi_loadout", "loadout"),
    ([f"{VENV}/moxi-launcher-job", "x"], "moxi-launcher-job", "launcher"),
], ids=["moxi", "module-script", "m-moxi", "m-moxi-name", "m-module", "Im", "W-value",
        "script-itself"])
def test_a_moxi_program_above_the_run_stops_it(argv, detail, module):
    got = found(ME, SHELL, p(argv), *PERSON[1:])
    # The program named is the script an interpreter runs, else the interpreter.
    program = os.path.basename(argv[0]) if detail.startswith("-m") else detail
    assert got == Evidence("E1", RUN + 2, program, detail, module)


@pytest.mark.parametrize("argv", [
    ["python", "-c", "import moxi"],
    ["python", "-m", "moxie"],
    ["python", "-m", "pytest", "moxi"],
    ["python", "/home/ada/moxi-notes.py"],
    ["/usr/bin/moxie"],
    ["moxi-dev", "check"],
    ["vim", "/home/ada/moxi"],
], ids=["c", "m-other", "m-pytest", "script-other", "other", "moxi-dev", "an-argument"])
def test_what_is_not_a_moxi_program_passes(argv):
    assert found(ME, SHELL, p(argv), *PERSON[1:]) is None


def test_a_windows_moxi_exe_is_e1():
    got = walk(Table(chain(p([WIN_EXE, WIN_MOXI], image=WIN_EXE),
                           p([r"C:\WINDOWS\System32\cmd.exe"]),
                           p([r"C:\Other\Scripts\MOXI.EXE", "slots"],
                             image=r"C:\Other\Scripts\MOXI.EXE"),
                           p([r"C:\WINDOWS\explorer.exe"])), windows=True),
               run(executable=WIN_EXE, argv0=r"C:\elsewhere\moxi.exe"))
    assert got == Evidence("E1", RUN + 2, "moxi", "moxi")


# -- E2 ------------------------------------------------------------------------------------

@pytest.mark.parametrize("harness", _walk.HARNESSES)
def test_an_agent_above_the_run_stops_it(harness):
    by_program = found(ME, SHELL, p([f"/home/ada/.local/bin/{harness}", "--resume"]),
                       *PERSON[1:])
    by_title = found(ME, SHELL, p([f"{harness} --resume x"]), *PERSON[1:])
    for got in (by_program, by_title):
        assert got == Evidence("E2", RUN + 2, harness, harness)


def test_an_undumpable_codex_is_still_an_agent():
    got = found(ME, SHELL, p(["codex"], shielded=True), *PERSON[1:])
    assert got == Evidence("E2", RUN + 2, "codex", "codex")


def test_a_harness_resetting_its_tool_shells_environment_is_met_above_the_hop():
    hop = p(["env", "-i", "bash"], env={})
    got = found(ME, SHELL, hop, p(["codex", "exec"]), SHELL, *PERSON[1:])
    assert got.kind == "E2" and got.pid == RUN + 3


def test_a_windows_harness_exe_is_an_agent():
    got = walk(Table(chain(p([WIN_EXE, WIN_MOXI], image=WIN_EXE),
                           p([r"C:\Users\ada\bin\Claude.exe"]),
                           p([r"C:\WINDOWS\explorer.exe"])), windows=True),
               run(executable=WIN_EXE, argv0=WIN_MOXI))
    assert got == Evidence("E2", RUN + 1, "claude", "claude")


# -- E3 ------------------------------------------------------------------------------------

def test_a_cgroup_naming_a_moxi_unit_stops_it():
    tmux = p(["/usr/bin/tmux", "-D", "-S", "/run/user/1000/moxi/slots/tmux"],
             unit="moxi-slots-server.service")
    got = found(ME, SHELL, tmux, USER_MANAGER, INIT)
    assert got == Evidence("E3", RUN + 2, "tmux", "moxi-slots-server.service", "slots")


def test_the_runs_own_unit_stops_it():
    me = p(ME["argv"], unit="moxi-slots-server.service")
    assert found(me, *PERSON).kind == "E3"


def test_a_launchd_job_naming_the_tmux_pid_stops_it():
    listed = _view.launchd_units(textwrap.dedent("""\
        PID\tStatus\tLabel
        -\t0\tcom.apple.something
        4121\t0\tmoxi.slots.server
        77\t0\tcom.example.moxi.slots.server
        """))
    assert listed == {4121: "moxi.slots.server"}
    procs = chain(ME, SHELL, p(["tmux"], unit=listed[4121]), USER_MANAGER)
    assert walk(Table(procs), run()) == Evidence("E3", RUN + 2, "tmux", "moxi.slots.server",
                                                 "slots")


@pytest.mark.parametrize("line, unit", [
    (b"0::/user.slice/user-1000.slice/user@1000.service/app.slice/moxi-slots-server.service\n",
     "moxi-slots-server.service"),
    (b"12:pids:/x\n1:name=systemd:/user.slice/user@1000.service/moxi-ui-serve.service\n",
     "moxi-ui-serve.service"),
    (b"0::/user.slice/user-1000.slice/user@1000.service/app.slice/vte-spawn-1.scope\n", None),
    (b"0::/user.slice/user-1000.slice/user@1000.service/app.slice/moxi.service\n", None),
])
def test_the_cgroup_reader_finds_the_cores_unit(line, unit):
    assert _view._cgroup_unit(line) == unit


def test_a_unit_that_is_not_the_cores_passes():
    assert found(ME, SHELL, p(["tmux"], unit="tmux-server.service"), USER_MANAGER) is None


# -- E4 ------------------------------------------------------------------------------------

def test_an_ancestors_start_environment_with_the_mark_stops_it():
    marked = p(["/usr/bin/script", "-q"], env={_mark.NAME: "moxi-launcher-job"})
    got = found(ME, SHELL, marked, *PERSON[1:])
    assert got == Evidence("E4", RUN + 2, "script", "moxi-launcher-job")


def test_the_runs_own_environment_is_read_from_what_moxi_noted_at_import():
    # The run's own start environment in the view is not what decides; the noted value is.
    me = p(ME["argv"], env={_mark.NAME: "ignored"})
    assert found(me, *PERSON) is None
    got = found(ME, *PERSON, started_by="by hand")
    assert got == Evidence("E4", RUN, "moxi", "by hand")


def test_a_shielded_process_is_not_judged_by_the_mark():
    hidden = Proc(pid=RUN + 1, parent=0, started=1.0, argv=("sudo",),
                  env={_mark.NAME: "x"}, shielded=True)
    assert walk(Table([*chain(ME, top_parent=RUN + 1), hidden]), run()) is None


def test_on_windows_any_spelling_of_the_mark_counts():
    got = walk(Table(chain(p([WIN_EXE, WIN_MOXI], image=WIN_EXE),
                           p([r"C:\x\pwsh.exe"], env={"moxi_started_by": "m"}),
                           p([r"C:\WINDOWS\explorer.exe"])), windows=True),
               run(executable=WIN_EXE, argv0=WIN_MOXI))
    assert got == Evidence("E4", RUN + 1, "pwsh", "m")


# -- the Windows entry chain ---------------------------------------------------------------

PWSH = p([r"C:\Program Files\PowerShell\7\pwsh.exe"])
WT = p([r"C:\Program Files\WindowsApps\Microsoft.WindowsTerminal\WindowsTerminal.exe"])
EXPLORER = p([r"C:\WINDOWS\explorer.exe"])


def windows(*procs, executable=WIN_EXE, argv0=WIN_MOXI, **run_kw):
    return walk(Table(chain(*procs), windows=True),
                run(executable=executable, argv0=argv0, **run_kw))


def test_the_interpreter_under_its_own_moxi_exe_records():
    me = p([WIN_EXE, WIN_MOXI, "modules"], image=WIN_EXE)
    launcher = p([WIN_MOXI, "modules"], image=WIN_MOXI)
    assert windows(me, launcher, PWSH, WT, EXPLORER) is None


def test_the_interpreter_under_a_venv_redirector_under_its_moxi_exe_records():
    me = p([WIN_BASE, WIN_MOXI, "modules"], image=WIN_BASE)
    redirector = p([WIN_EXE, WIN_MOXI, "modules"], image=WIN_EXE)
    launcher = p([WIN_MOXI, "modules"], image=WIN_MOXI)
    assert windows(me, redirector, launcher, PWSH, WT, EXPLORER) is None


def test_images_compare_after_folding_case_and_slashes():
    me = p([WIN_EXE, WIN_MOXI], image=WIN_EXE)
    launcher = p([WIN_MOXI], image=WIN_MOXI.upper().replace("\\", "/"))
    assert windows(me, launcher, PWSH, EXPLORER) is None


def test_a_moxi_exe_that_is_not_the_runs_own_launcher_is_e1():
    me = p([WIN_EXE, WIN_MOXI], image=WIN_EXE)
    other = p([r"C:\Other\moxi.exe"], image=r"C:\Other\moxi.exe")
    got = windows(me, other, PWSH, EXPLORER)
    assert got == Evidence("E1", RUN + 1, "moxi", "moxi")


def test_a_launcher_that_is_not_the_direct_parent_is_e1():
    me = p([WIN_EXE, WIN_MOXI], image=WIN_EXE)
    between = p([r"C:\x\conhost.exe"])
    launcher = p([WIN_MOXI], image=WIN_MOXI)
    assert windows(me, between, launcher, PWSH, EXPLORER).kind == "E1"


def test_a_redirector_that_is_not_the_direct_parent_is_e1():
    me = p([WIN_BASE, WIN_MOXI], image=WIN_BASE)
    launcher = p([WIN_MOXI], image=WIN_MOXI)
    redirector = p([WIN_EXE, WIN_MOXI], image=WIN_EXE)
    # Launcher first, then a redirector: only the order redirector, launcher is the chain.
    got = windows(me, launcher, redirector, PWSH, EXPLORER)
    assert got == Evidence("E1", RUN + 2, "moxi", "moxi")


def test_no_redirector_is_skipped_when_the_run_is_its_own_image():
    me = p([WIN_EXE, WIN_MOXI], image=WIN_EXE)
    twin = p([WIN_EXE, WIN_MOXI], image=WIN_EXE)
    assert windows(me, twin, PWSH, EXPLORER).kind == "E1"


def test_a_moxi_program_above_the_runs_own_launcher_is_e1():
    me = p([WIN_EXE, WIN_MOXI], image=WIN_EXE)
    launcher = p([WIN_MOXI], image=WIN_MOXI)
    adapter = p([r"C:\x\python.exe", "-m", "moxi_comms.adapters.window"])
    got = windows(me, launcher, PWSH, adapter, EXPLORER)
    assert got == Evidence("E1", RUN + 3, "python", "-m moxi_comms.adapters.window", "comms")


def test_the_entry_chain_is_judged_by_the_mark():
    me = p([WIN_EXE, WIN_MOXI], image=WIN_EXE)
    launcher = p([WIN_MOXI], image=WIN_MOXI, env={_mark.NAME: "moxi"})
    got = windows(me, launcher, PWSH, EXPLORER)
    assert got == Evidence("E4", RUN + 1, "moxi", "moxi")


def test_on_windows_a_parent_started_after_its_child_ends_the_walk():
    procs = chain(p([WIN_EXE, WIN_MOXI], image=WIN_EXE), PWSH,
                  p([r"C:\Other\moxi.exe"], image=r"C:\Other\moxi.exe"))
    procs[2] = Proc(**{**procs[2].__dict__, "started": 5000.0})
    assert walk(Table(procs, windows=True), run(executable=WIN_EXE, argv0=WIN_MOXI)) is None


def test_on_windows_a_parent_pid_with_no_process_ends_the_walk():
    procs = chain(p([WIN_EXE, WIN_MOXI], image=WIN_EXE), PWSH, top_parent=999)
    assert walk(Table(procs, windows=True), run(executable=WIN_EXE, argv0=WIN_MOXI)) is None


# -- the walk's limits on Linux and macOS -------------------------------------------------

def test_over_posix_nothing_above_the_run_is_skipped():
    # A moxi script above the run's own process is a Moxi program, even with the run's
    # own script and interpreter: on Linux and macOS no launcher process exists.
    got = found(ME, p([f"{VENV}/python", f"{VENV}/moxi", "config"]), *PERSON)
    assert got.kind == "E1" and got.pid == RUN + 1


def test_a_parent_that_ended_during_the_walk_is_n1():
    procs = chain(ME, SHELL, TERMINAL, INIT)
    view = Table([q for q in procs if q.pid != RUN + 2], reparented={RUN + 1: 1})
    assert walk(view, run()) == Undecided("N1", "ended", RUN + 2)


def test_a_parent_started_after_its_child_is_n1():
    procs = chain(ME, SHELL, TERMINAL, INIT)
    procs[2] = Proc(**{**procs[2].__dict__, "started": 5000.0})
    assert walk(Table(procs), run()) == Undecided("N1", "ended", RUN + 2,
                                                 "gnome-terminal-server")


def test_a_parent_the_system_hides_ends_the_walk():
    procs = chain(ME, SHELL, TERMINAL, INIT)
    view = Table([q for q in procs if q.pid != RUN + 2])
    assert walk(view, run()) is None


def test_an_unreadable_process_that_is_not_shielded_is_n1():
    view = Table(chain(ME, *PERSON), errors={RUN + 2: Unread(RUN + 2, "EIO", "kitty")})
    assert walk(view, run()) == Undecided("N1", "unread", RUN + 2, "kitty", "EIO")


def test_a_loop_is_n1():
    procs = chain(ME, SHELL, TERMINAL, top_parent=RUN + 1)
    assert walk(Table(procs), run()) == Undecided("N1", "loop", RUN + 1)


@pytest.mark.parametrize("depth, result", [(64, None), (65, Undecided("N1", "deep", RUN + 64))])
def test_a_walk_reads_at_most_64_processes(depth, result):
    procs = chain(ME, *[SHELL] * (depth - 1))
    assert len(procs) == depth
    assert walk(Table(procs), run()) == result


def test_an_unreadable_run_is_n1():
    view = Table(chain(ME, *PERSON), errors={RUN: Unread(RUN, "EIO")})
    assert walk(view, run()).kind == "N1"


# -- N2 ------------------------------------------------------------------------------------

TMUX = p(["tmux", "new-session"])
TMUX_TITLE = p(["tmux: server (/tmp/tmux-1000/default)"])


@pytest.mark.parametrize("tmux", [TMUX, TMUX_TITLE], ids=["argv", "title"])
def test_a_tmux_server_nothing_places_is_n2_while_slots_runs_outside_a_unit(tmux):
    got = found(ME, SHELL, tmux, USER_MANAGER, INIT, view_kw={"loose": True})
    assert got == Undecided("N2", "tmux", RUN + 2, "tmux")


def test_the_persons_own_tmux_records_where_slots_server_runs_as_a_unit():
    view = Table(chain(ME, SHELL, TMUX, USER_MANAGER, INIT), loose=False)
    assert walk(view, run()) is None
    assert view.asked_slots == 1


def test_a_tmux_server_e3_places_is_e3_and_slots_is_not_asked():
    tmux = p(["tmux"], unit="moxi-slots-server.service")
    view = Table(chain(ME, SHELL, tmux, USER_MANAGER), loose=True)
    assert walk(view, run()).kind == "E3"
    assert view.asked_slots == 0


def test_slots_is_asked_only_at_a_tmux_server():
    view = Table(chain(ME, *PERSON), loose=True)
    assert walk(view, run()) is None
    assert view.asked_slots == 0


def test_slots_loose_without_slots_installed_is_false(monkeypatch):
    from moxi import modules

    monkeypatch.setattr(modules, "present", lambda name: False)
    assert _view._View().slots_loose() is False


@pytest.mark.parametrize("backend, state, loose", [
    (None, None, True), ("systemd", "missing", True), ("systemd", "active", False),
    ("systemd", "inactive", False), ("systemd", "unknown", True), ("systemd", "none", True)])
def test_slots_loose_asks_the_service_manager(monkeypatch, backend, state, loose):
    from moxi import modules, units

    class State:
        kind, name = "daemon", "server"

    State.state = state
    monkeypatch.setattr(modules, "present", lambda name: name == "slots")
    monkeypatch.setattr(units, "backend", lambda: backend)
    monkeypatch.setattr(units, "status",
                        lambda module: [] if state == "none" else [State()])
    assert _view._View().slots_loose() is loose


def test_slots_loose_that_fails_is_true(monkeypatch):
    from moxi import modules

    def fail(name):
        raise RuntimeError("metadata")

    monkeypatch.setattr(modules, "present", fail)
    assert _view._View().slots_loose() is True


# -- the Linux reader ----------------------------------------------------------------------

LINUX = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")


def _fake(root, pid, *, ppid=1, start=500, state="S", comm="bash", cmdline=b"bash\0",
          environ=b"A=1\0", cgroup=b"0::/user.slice/x.scope\n"):
    d = root / str(pid)
    d.mkdir()
    (d / "stat").write_bytes(f"{pid} ({comm}) {state} {ppid} ".encode()
                             + b" ".join(b"0" for _ in range(17)) + f" {start} 0\n".encode())
    for name, raw in (("cmdline", cmdline), ("environ", environ), ("cgroup", cgroup)):
        if raw is not None:
            (d / name).write_bytes(raw)
    return d


@LINUX
def test_the_linux_reader_reads_a_process(tmp_path):
    _fake(tmp_path, 12, ppid=7, start=4321, comm="tmux: server",
          cmdline=b"/usr/bin/tmux\0-D\0", environ=b"MOXI_STARTED_BY=x\0",
          cgroup=b"0::/user.slice/user@1000.service/app.slice/moxi-slots-server.service\n")
    got = _view.LinuxView(str(tmp_path), uid=os.geteuid()).read(12)
    assert got == Proc(12, 7, 4321.0, ("/usr/bin/tmux", "-D"), {"MOXI_STARTED_BY": "x"},
                       unit="moxi-slots-server.service", name="tmux: server")


@LINUX
def test_the_linux_reader_shields_another_uids_process(tmp_path):
    _fake(tmp_path, 12, cmdline=b"sshd-session: ada\0")
    got = _view.LinuxView(str(tmp_path), uid=os.geteuid() + 1).read(12)
    assert got.shielded and got.argv == ("sshd-session: ada",) and got.env is None
    assert got.unit is None


@LINUX
def test_the_linux_reader_shields_a_process_whose_environment_is_refused(tmp_path):
    d = _fake(tmp_path, 12, cmdline=b"/usr/lib/systemd/systemd\0--user\0")
    (d / "environ").chmod(0)
    if os.access(d / "environ", os.R_OK):
        pytest.skip("this account reads a file with no permissions")
    got = _view.LinuxView(str(tmp_path), uid=os.geteuid()).read(12)
    assert got.shielded and got.argv == ("/usr/lib/systemd/systemd", "--user")


@LINUX
@pytest.mark.parametrize("what", ["gone", "zombie", "no-environ"])
def test_the_linux_reader_says_gone(tmp_path, what):
    if what == "zombie":
        _fake(tmp_path, 12, state="Z", cmdline=b"", environ=b"")
    elif what == "no-environ":
        _fake(tmp_path, 12, environ=None)
    view = _view.LinuxView(str(tmp_path), uid=os.geteuid())
    with pytest.raises(Gone):
        view.read(12)


@LINUX
def test_the_linux_reader_of_a_shapeless_stat_is_unread(tmp_path):
    d = tmp_path / "12"
    d.mkdir()
    (d / "stat").write_bytes(b"12 (x) S")
    with pytest.raises(Unread):
        _view.LinuxView(str(tmp_path), uid=os.geteuid()).read(12)


@LINUX
def test_the_linux_reader_reads_this_process():
    got = _view.LinuxView().read(os.getpid())
    assert got.parent == os.getppid() and got.argv and got.env is not None


# -- live ----------------------------------------------------------------------------------

_CHILD = textwrap.dedent("""\
    import json, os, subprocess, sys
    env = {k: v for k, v in os.environ.items() if k.upper() != "MOXI_STARTED_BY"}
    code = ("import dataclasses, json, moxi.terminal as t; r = t.walk(); "
            "print(json.dumps(r and dataclasses.asdict(r)))")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                         text=True, timeout=60)
    print(out.stdout.strip() or out.stderr)
    """)


@pytest.mark.skipif(sys.platform not in ("linux", "win32", "darwin"), reason="no reader")
def test_live_a_process_started_with_the_mark_stops_its_childs_record():
    import json

    env = {**os.environ, _mark.NAME: "a test"}
    done = subprocess.run([sys.executable, "-c", _CHILD], env=env, capture_output=True,
                          text=True, timeout=90)
    got = json.loads(done.stdout.strip().splitlines()[-1])
    assert got is not None, done.stdout
    # The run started without the mark; its parent, this test's child, started with it.
    assert (got["kind"], got["detail"]) == ("E4", "a test"), got


def test_live_a_run_that_started_with_the_mark_stops_at_itself():
    env = {**os.environ, _mark.NAME: "by hand"}
    code = ("import dataclasses, json, moxi.terminal as t; r = t.walk(); "
            "print(json.dumps(r and dataclasses.asdict(r)))")
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                          text=True, timeout=60)
    import json

    got = json.loads(done.stdout)
    assert (got["kind"], got["detail"]) == ("E4", "by hand"), done.stderr


# -- live, through the real console script -------------------------------------------------

_SITE = textwrap.dedent("""\
    import atexit, dataclasses, json, os, sys

    def _walk():
        # Every interpreter started with this site runs this; only the moxi run writes.
        if not os.path.basename(sys.argv[0]).lower().startswith("moxi"):
            return
        import moxi.terminal as t
        from moxi.terminal import _view
        found = t.walk()
        view, me, chain = _view.default_view(), os.getpid(), []
        try:
            while me > 0 and len(chain) < 8:
                p = view.read(me)
                chain.append([p.pid, p.image or (p.argv or [""])[0]])
                me = p.parent
        except Exception as error:
            chain.append(repr(error))
        with open(os.environ["WALK_OUT"], "w") as f:
            json.dump({"found": found and dataclasses.asdict(found), "chain": chain,
                       "me": os.getpid()}, f)

    if os.environ.get("WALK_OUT"):
        atexit.register(_walk)
    """)

_HOP = textwrap.dedent("""\
    import os, subprocess, sys
    env = {k: v for k, v in os.environ.items() if k.upper() != "MOXI_STARTED_BY"}
    sys.exit(subprocess.run(sys.argv[1:], env=env, timeout=120).returncode)
    """)


def test_live_the_walk_skips_exactly_the_real_console_scripts_entry_chain(tmp_path):
    """`moxi version` run through the installed console script (on Windows its `.exe`
    launcher, and a venv redirector where there is one), below a process that started
    with the mark: the walk passes the run's own entry chain and finds the mark on that
    process, not E1 on the launcher."""
    import json
    import shutil

    script = shutil.which("moxi", path=os.path.dirname(sys.executable))
    if script is None:
        pytest.skip("no moxi console script beside this interpreter")
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(_SITE)
    out = tmp_path / "walk.json"
    keep = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "HOME",
            "USERPROFILE", "LOCALAPPDATA", "APPDATA", "MOXI_HOME", "XDG_DATA_HOME",
            "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(PYTHONPATH=str(site), WALK_OUT=str(out), **{_mark.NAME: "the hop"})
    done = subprocess.run([sys.executable, "-c", _HOP, script, "version"], env=env,
                          capture_output=True, text=True, timeout=150)
    assert done.returncode == 0, done.stderr
    got = json.loads(out.read_text())
    found_ = got["found"]
    assert found_ is not None, got
    print(got)
    assert (found_["kind"], found_["detail"]) == ("E4", "the hop"), got
    # Found on the process that started with the mark, above the run's entry chain.
    assert found_["pid"] != got["me"]
    assert found_["pid"] in [step[0] for step in got["chain"][1:] if isinstance(step, list)]
