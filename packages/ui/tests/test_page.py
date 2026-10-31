"""The shell's own page: the doctor route's run, and its rows from a real shell."""

from __future__ import annotations

import asyncio
import sys
import threading
import time

import psutil
import pytest

from moxi_ui import page


def _shell_row(answer):
    return next(row for row in answer["rows"] if row["module"] == "ui" and row["name"] == "shell")


def test_concurrent_checks_all_find_the_shell_running(ui_shell):
    # Each Check's doctor calls the shell's /servers back: more Checks at once than the
    # shell has worker threads must still each find it running.
    results = []

    def check():
        results.append(ui_shell.session(timeout=90).get("/m/ui/api/doctor?module=ui"))

    threads = [threading.Thread(target=check) for _ in range(6)]
    for thread in threads:
        thread.start()
    time.sleep(0.3)
    started = time.monotonic()
    status, _ = ui_shell.session().get("/m/ui/api/settings?module=ui")
    assert status == 200 and time.monotonic() - started < 2
    for thread in threads:
        thread.join(120)
    assert len(results) == 6 and all(status == 200 for status, _ in results)
    for _, answer in results:
        assert answer["error"] is None
        assert _shell_row(answer)["detail"].startswith("running: pid"), answer


def test_a_doctor_run_past_its_time_is_killed_with_what_it_started(tmp_path):
    script = ("import pathlib, subprocess, sys, time\n"
              "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
              "pathlib.Path('below').write_text(str(child.pid))\n"
              "time.sleep(60)\n")

    async def run():
        return await page.run_doctor([sys.executable, "-c", script], cwd=str(tmp_path),
                                     env={}, timeout=1.5)

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(run())
    assert time.monotonic() - started < 5
    below = int((tmp_path / "below").read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            if psutil.Process(below).status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the doctor's own child outlived the time limit")
