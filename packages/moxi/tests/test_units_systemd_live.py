"""The wrap proof on this box's own systemd user manager.

Not part of the suite: it runs only with `MOXI_TEST_REAL_SUPERVISOR=1` on a system with a
systemd user manager, and is skipped everywhere else. It writes a throwaway timer and
service under test names, `moxitest-wrap-<8 hex>.timer` and `.service`, into the user unit
directory, and deletes them at its end with their drop-in folder and a `daemon-reload`. It
never names another unit, and it lists the unit directory before and after and fails if
anything else changed. The service's command is a script in the test's tmp that appends
its arguments to a file there.

The rows that run the gate's wrap forms (`moxi run --condition` and `--result`) need the
gate; on a tree without it they are skipped with that reason.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

import moxi.paths
from moxi.units import _systemd
from moxi.units._fence import REAL_SUPERVISOR, Fence
from moxi.units._systemd import SystemdBackend, command_line, quote
from moxi.units._types import Wrapped

MODULE, KEY, NAME = "wraptest", "backup.enabled", "backup"


def _user_manager() -> bool:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return bool(shutil.which("systemctl") and os.path.isdir("/run/systemd/system")
                and runtime and os.path.exists(os.path.join(runtime, "systemd", "private")))


pytestmark = pytest.mark.skipif(
    os.environ.get(REAL_SUPERVISOR) != "1" or not _user_manager(),
    reason=f"a proof row on the real user manager: set {REAL_SUPERVISOR}=1 on a system "
           "with a systemd user manager")


def _listing(root: Path) -> dict[str, tuple]:
    """Every entry under `root` with what would show a change: a file's size and mtime,
    a link's target. A directory counts by its name only (adding an entry changes its
    mtime)."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out[rel] = ("link", os.readlink(path))
        elif path.is_dir():
            out[rel] = ("dir",)
        else:
            st = path.stat()
            out[rel] = ("file", st.st_size, st.st_mtime_ns)
    return out


class Row:
    """The throwaway pair and what the row reads back."""

    def __init__(self, tmp: Path):
        self.id = os.urandom(4).hex()
        self.stem = f"moxitest-wrap-{self.id}"
        self.timer, self.service = f"{self.stem}.timer", f"{self.stem}.service"
        self.dir = _systemd.unit_dir()
        self.tmp = tmp
        self.home = tmp / "home"
        self.out = tmp / "out.txt"
        self.code = tmp / "code"
        self.code.write_text("0")
        self.fence = Fence("systemd")
        self.backend = SystemdBackend()
        self.env = {"MOXI_HOME": str(self.home)}
        self.runs = moxi.paths.Layout(MODULE).state_dir(self.env) / "runs.jsonl"
        self.config = moxi.paths.Layout(MODULE).config_file(self.env)

    def script(self, name: str, tag: str) -> Path:
        path = self.tmp / name
        path.write_text(f'#!/bin/sh\nprintf "{tag} %s\\n" "$@" >> {shlex.quote(str(self.out))}\n'
                        f'exit "$(cat {shlex.quote(str(self.code))})"\n')
        return path

    def write(self, argv: list[str]) -> None:
        (self.dir / self.service).write_text(
            "[Unit]\nDescription=moxi test: a throwaway service for the wrap proof\n\n"
            "[Service]\nType=oneshot\n"
            f"Environment={quote('MOXI_HOME=' + str(self.home))}\n"
            f"ExecStart={command_line(argv)}\n")
        (self.dir / self.timer).write_text(
            "[Unit]\nDescription=moxi test: a throwaway timer for the wrap proof\n\n"
            f"[Timer]\nOnActiveSec=1d\nUnit={self.service}\n")
        assert self.fence.run("daemon-reload").ok

    def start(self):
        return self.fence.run("start", self.service)

    def show(self, *props: str) -> dict[str, str]:
        ran = self.fence.run("show", "--property=" + ",".join(props), self.service)
        assert ran.ok, ran.failure()
        return dict(line.partition("=")[::2] for line in ran.stdout.splitlines() if line)

    def lines(self) -> list[dict]:
        if not self.runs.exists():
            return []
        return [json.loads(line) for line in self.runs.read_text().splitlines() if line]

    def output(self) -> list[str]:
        return self.out.read_text().splitlines() if self.out.exists() else []

    def switch(self, on: bool) -> None:
        self.config.parent.mkdir(parents=True, exist_ok=True)
        table, _, key = KEY.partition(".")
        self.config.write_text(f"[{table}]\n{key} = {'true' if on else 'false'}\n")

    def clean(self) -> None:
        for name in (self.timer, self.service):
            try:
                (self.dir / name).unlink()
            except FileNotFoundError:
                pass
        shutil.rmtree(self.dir / f"{self.service}.d", ignore_errors=True)
        # Cleanup only: forget a failed run of the throwaway service.
        subprocess.run(["systemctl", "--user", "reset-failed", self.service, self.timer],
                       capture_output=True, timeout=30)
        self.fence.run("daemon-reload")


def _gate() -> bool:
    return importlib.util.find_spec("moxi.run") is not None


def test_wrap_on_this_boxs_user_manager(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "home"))
    row = Row(tmp_path)
    before = _listing(row.dir)
    assert not any(k.startswith("moxitest-") for k in before)
    report: list[str] = []
    try:
        first = row.script("first.sh", "first")
        odd = ["a b", "50%", "$HOME", 'q"x', "back\\slash", ";", "it's"]
        row.write(["/bin/sh", str(first), *odd])

        # The service runs as written, its arguments quoted by the backend's rules.
        assert row.start().ok
        assert row.output() == [f"first {a}" for a in odd]
        report.append(f"quoting: {row.output()}")
        row.out.unlink()

        acted = row.backend.wrap(row.timer, module=MODULE, key=KEY, name=NAME,
                                 fence=row.fence)
        report.append(f"wrap: ok={acted.ok} lines={acted.lines}")
        assert acted.ok, acted.lines
        dropin = row.dir / f"{row.service}.d" / f"50-moxi-{MODULE}.conf"
        text = dropin.read_text()
        assert str(dropin) in row.show("DropInPaths")["DropInPaths"].split()
        unit = row.backend.foreign(row.timer, None)
        [state] = row.backend.status([unit], row.fence)
        assert state.wrapped == Wrapped(MODULE, KEY, NAME)
        report.append(f"status: {state.state} wrapped={state.wrapped} last={state.last}")

        if _gate():
            # Off (no config file): skipped, not failed, the script not run, one `off` line.
            assert row.start().ok
            shown = row.show("Result", "ActiveState")
            report.append(f"off: {shown} lines={row.lines()}")
            assert shown["ActiveState"] != "failed" and shown["Result"] == "exec-condition"
            assert row.output() == []
            assert [r["result"] for r in row.lines()] == ["off"]

            # On: the script runs and one `ran` line carries its exit and seconds.
            row.switch(True)
            assert row.start().ok
            assert row.output() == [f"first {a}" for a in odd]
            ran = row.lines()[-1]
            report.append(f"on: {ran}")
            assert (ran["result"], ran["exit"]) == ("ran", 0) and "seconds" in ran
            assert len(row.lines()) == 2

            # Exit 4: `ran`, exit 4, and the unit failed as the foreign unit makes it.
            row.code.write_text("4")
            assert not row.start().ok
            ran = row.lines()[-1]
            shown = row.show("Result", "ActiveState")
            report.append(f"exit 4: {ran} {shown}")
            assert (ran["result"], ran["exit"]) == ("ran", 4)
            assert shown["ActiveState"] == "failed"
            row.code.write_text("0")

            # A changed ExecStart runs through the same, unchanged drop-in.
            second = row.script("second.sh", "second")
            row.write(["/bin/sh", str(second)])
            row.out.unlink()
            assert row.start().ok
            assert row.output() == ["second "]
            assert dropin.read_text() == text
            assert row.lines()[-1]["result"] == "ran" and len(row.lines()) == 4
            report.append(f"changed ExecStart: {row.output()} {row.lines()[-1]}")
        else:
            report.append("gate rows skipped: this tree has no gate (moxi run)")

        acted = row.backend.unwrap(row.timer, module=MODULE, fence=row.fence)
        assert acted.ok and not dropin.exists() and not dropin.parent.exists()
        assert str(dropin) not in row.show("DropInPaths")["DropInPaths"]
        count = len(row.lines())
        assert row.start().ok
        assert len(row.lines()) == count
        report.append(f"unwrap: ok={acted.ok}, a start after it wrote no run line")
    finally:
        row.clean()
        after = _listing(row.dir)
        with capsys.disabled():
            print("\n" + "\n".join(f"  {line}" for line in report))
            print(f"  unit directory unchanged: {after == before}")
    assert after == before
    if not _gate():
        pytest.skip("the gate's wrap forms (moxi run --condition, --result) are not in "
                    "this tree; the drop-in, status and unwrap rows passed")
