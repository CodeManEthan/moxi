"""Real processes for the settle step's tests: a job-control shell, an install's parent and
a settle (or teardown) child, all on the fake backend.

    python settle_procs.py shell ARGV...            the session leader on a terminal
    python settle_procs.py parent MODULE... [--replace] [--skills] [--teardown]
    python settle_procs.py child ARGS...            `python -m moxi._settle ARGS`

The shell takes the terminal as its controlling terminal, starts ARGV in a process group
of its own and makes that group the terminal's foreground group, as a shell runs a
foreground job. It stays alive after the job ends (until it is killed), so the end of
the job never hangs the terminal up.

The parent runs `moxi install`'s settle loop (`Installer._setup`) under the install's own
hold of the core lock, with each module's settle step in a child started by
`moxi._settle.run_child`, as the install starts it. With `--teardown` it runs `moxi
uninstall` of the first module instead (`Uninstaller._run`, under the same hold), its
teardown in a child; the environment is a fake plain one whose package step only logs.

The child runs steps 4 to 6 only when `MOXITEST_LATER` is set; otherwise it logs that it
reached them.

The child is `python -m moxi._settle` with the fake backend put in place first: its unit
files under the box's `units` directory, its supervisor's state in a JSON file, so the
test reads what every process did to it.

Every process appends what it sees to one JSON-lines log (`MOXITEST_LOG`): who it is,
its pid, and the event. The box's layout is in the JSON file `MOXITEST_BOX` names.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from moxi.units._fake import FakeBackend, FakeSupervisor

HERE = Path(__file__).resolve()
LOG = "MOXITEST_LOG"
BOX = "MOXITEST_BOX"


def note(who: str, **fields) -> None:
    """Append one event to the log, in one write."""
    line = json.dumps({"who": who, "pid": os.getpid(), **fields}) + "\n"
    fd = os.open(os.environ[LOG], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def events(path) -> list[dict]:
    """Every event in the log at `path`, in order."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in text.splitlines() if line.endswith("}")]


class Supervisor(FakeSupervisor):
    """The fake supervisor with its state in a JSON file, so every process sees one
    supervisor. The file holds `units`, `loaded`, `extra`, `fail` (a list of `[verb,
    unit, stderr]`), `sleep` (`"<verb> <unit>"` or `"<verb>"` to seconds a call takes)
    and `probe` (verbs before which a process is spawned and its ignored signals
    logged)."""

    def __init__(self, root, path):
        self.path = Path(path)
        self.sleep: dict[str, float] = {}
        self.probe: list[str] = []
        super().__init__(root)
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        self.units = data.get("units", {})
        self.loaded = set(data.get("loaded", ()))
        self.extra = set(data.get("extra", ()))
        self.fail = {(verb, unit): text for verb, unit, text in data.get("fail", ())}
        self.sleep = data.get("sleep", {})
        self.probe = data.get("probe", [])

    def save(self) -> None:
        data = {"units": self.units, "loaded": sorted(self.loaded),
                "extra": sorted(self.extra),
                "fail": [[verb, unit, text] for (verb, unit), text in self.fail.items()],
                "sleep": self.sleep, "probe": self.probe}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, self.path)

    def __call__(self, cmd, *, env=None, timeout=None):
        self.load()
        args = ([a for a in cmd[2:] if a != "--"] if cmd[0] != "loginctl"
                else list(cmd))
        if LOG in os.environ:
            note("sup", begin=args)
        if args and args[0] in self.probe:
            note("sup", ignored=_ignored_in_a_spawned_process())
        delay = self.sleep.get(" ".join(args), self.sleep.get(args[0] if args else ""))
        if delay:
            time.sleep(delay)
        done = super().__call__(cmd, env=env, timeout=timeout)
        self.save()
        if LOG in os.environ:
            note("sup", call=args, code=done.returncode)
        return done


def _ignored_in_a_spawned_process() -> list[int]:
    """The signals a program started from here has ignored, from its own status."""
    done = subprocess.run(["cat", "/proc/self/status"], capture_output=True, text=True,
                          timeout=30)
    for line in done.stdout.splitlines():
        if line.startswith("SigIgn:"):
            mask = int(line.split()[1], 16)
            return [n for n in range(1, 65) if mask & (1 << (n - 1))]
    return [-1]


def box() -> dict:
    return json.loads(Path(os.environ[BOX]).read_text())


def backend() -> FakeBackend:
    b = box()
    return FakeBackend(Path(b["units"]), runner=Supervisor(b["units"], b["supervisor"]))


# -- the shell -------------------------------------------------------------------------------

def shell(argv: list[str]) -> int:
    import fcntl
    import termios

    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    # Python handlers, so the job starts with default dispositions whatever this process
    # inherited, and a hang-up of the terminal doesn't end the shell.
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(signum, lambda *_: None)
    job = subprocess.Popen(argv, process_group=0)
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
    try:
        os.tcsetpgrp(0, job.pid)
    except OSError:
        pass
    note("shell", job=job.pid)
    code = job.wait()
    note("shell", exit=code)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        time.sleep(0.5)
    return code


# -- the parent ------------------------------------------------------------------------------

def parent(argv: list[str]) -> int:
    from types import SimpleNamespace

    from moxi import _settle, install

    if os.isatty(0):
        deadline = time.monotonic() + 10
        while os.tcgetpgrp(0) != os.getpgrp() and time.monotonic() < deadline:
            time.sleep(0.02)
    replace = "--replace" in argv
    teardown = "--teardown" in argv
    names = [a for a in argv if not a.startswith("--")]

    real_send = subprocess.Popen.send_signal

    def send_signal(self, signum):
        note("parent", sent=int(signum))
        return real_send(self, signum)

    subprocess.Popen.send_signal = send_signal

    def run_settle(python, module, *, replace=False, teardown=False, skills=None):
        child = [sys.executable, str(HERE), "child"]
        child += ["--teardown", module] if teardown else [module]
        child += ["--replace"] if replace else []
        child += ["--skills"] if skills else []
        note("parent", settle=module)
        outcome = _settle.run_child(child)
        note("parent", outcome=outcome.code, signals=[int(s) for s in outcome.signals])
        return outcome

    install.run_settle = run_settle
    note("parent", pgid=os.getpgrp(), sid=os.getsid(0))
    with install._core_lock():
        note("parent", locked=True)
        if teardown:
            from moxi import uninstall
            from moxi.units import _backend

            _backend._override.append(backend())
            install.environment = lambda: install.Environment(
                install.PLAIN, Path(sys.prefix), sys.executable)
            install.run = lambda argv, env=None: note("parent", ran=argv) or 0
            uninstall.run_teardown = (
                lambda python, module: run_settle(python, module, teardown=True))
            args = uninstall._parser("moxi uninstall").parse_args([names[0]])
            try:
                code = uninstall.Uninstaller(args)._run(names[0])
            except install.InstallError as exc:
                print(f"moxi uninstall: {exc}", file=sys.stderr)
                code = exc.exit_code
        else:
            args = install._parser("moxi install").parse_args(
                [names[0], "--none"] + (["--replace"] if replace else [])
                + (["--skills"] if "--skills" in argv else []))
            installer = install.Installer(args)
            installer.env = SimpleNamespace(python=sys.executable)
            try:
                installer._setup([{"name": n, "state": "present"} for n in names])
                code = 1 if installer.setup_failed else 0
            except install.InstallError as exc:
                print(f"moxi install: {exc}", file=sys.stderr)
                code = exc.exit_code
    note("parent", exit=code)
    return code


# -- the child -------------------------------------------------------------------------------

def child(argv: list[str]) -> int:
    from moxi import _settle, files, paths
    from moxi.units import _backend

    b = box()
    paths._scripts = lambda: Path(b["scripts"])
    _backend._override.append(backend())
    if not os.environ.get("MOXITEST_LATER"):
        _settle._later_steps = lambda step: note("child", later=step.module) or 0
    real = _settle._on_signal

    def logged(signum, frame):
        note("child", got=int(signum), phase=_settle._state.phase)
        return real(signum, frame)

    def before():
        for signum in _settle.signals():
            if signal.getsignal(signum) is real:
                signal.signal(signum, logged)
        note("child", started=argv, variable=os.environ.get(files.CLAIM_VARIABLE),
             claim=files._claim is not None, pgid=os.getpgrp())

    try:
        code = _settle.main(argv, before=before)
    except BaseException as exc:
        note("child", raised=repr(exc))
        raise
    # The stop signals ignored by now: a late copy can't kill the child as it shuts down.
    note("child", exit=code, ignored=[int(s) for s in _settle.signals()
                                      if signal.getsignal(s) == signal.SIG_IGN])
    return code


def main() -> int:
    mode, argv = sys.argv[1], sys.argv[2:]
    try:
        return {"shell": shell, "parent": parent, "child": child}[mode](argv)
    except BaseException as exc:
        if not isinstance(exc, SystemExit):
            note(mode, raised=repr(exc))
        raise


if __name__ == "__main__":
    sys.exit(main())
