"""The shell's own page: the doctor route's run, and its rows from a real shell."""

from __future__ import annotations

import asyncio
import sys
import threading
import time

import psutil
import pytest
from moxi.paths import python

from moxi_ui import doctor, health, page


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


class _Call:
    def __init__(self, **query):
        self.query = query


def test_the_doctor_route_runs_moxi_doctor_from_the_runtime_dir_without_the_log_variable(
        tmp_path, monkeypatch):
    from moxi.paths import Layout

    from moxi_ui.starter import LOG_FILE_VARIABLE, Starters

    monkeypatch.setenv(LOG_FILE_VARIABLE, str(tmp_path / "shell.log"))
    layout = Layout("ui")
    starters = Starters(runtime_dir=layout.runtime_dir(create=True),
                        state_dir=layout.state_dir(), log=tmp_path / "shell.log")
    shell = type("Shell", (), {"layout": layout, "starters": starters})()
    runs = []

    async def run_doctor(argv, *, cwd, env, timeout):
        runs.append((argv, cwd, env, timeout))
        return 0, b'[{"module": "ui", "name": "shell", "state": "ok", "detail": "running"}]'

    monkeypatch.setattr(page, "run_doctor", run_doctor)

    async def ask(**query):
        token = page._shell.set(shell)
        try:
            return await page.doctor(_Call(**query))
        finally:
            page._shell.reset(token)

    answer = asyncio.run(ask(module="ui"))
    assert answer == {"rows": [{"module": "ui", "name": "shell", "state": "ok",
                                "detail": "running", "fix": None}], "error": None}
    argv, cwd, env, timeout = runs.pop()
    assert argv == [str(python()), "-P", "-m", "moxi", "doctor", "ui", "--json"]
    assert argv == page.doctor_argv("ui") and page.doctor_argv() == argv[:5] + ["--json"]
    assert cwd == str(layout.runtime_dir()) and timeout == page.DOCTOR_TIMEOUT
    assert LOG_FILE_VARIABLE not in env and env == starters.child_env()
    assert asyncio.run(ask(module="Not A Name")) == {"rows": [], "error": "not a module name"}
    assert runs == []


def test_the_page_rows_come_from_a_real_shells_servers_data(ui_shell):
    session = ui_shell.session()
    assert session.get("/m/fixture/api/summary")[0] == 200
    ui_shell.wait_server("fixture", "running")
    status, data = ui_shell.call("GET", "/m/ui/api/servers")
    assert status == 200
    for row in data["servers"]:
        assert set(row) == set(page.SERVER_ROW.items), "the row keeps its declared shape"
    found = health.Probe("running", servers=data, port=data["shell"]["port"])
    rows = {row.name: row for row in doctor.page_rows(found, now=time.time())}
    fixture = rows["page:fixture"]
    assert fixture.module == "ui" and fixture.state == "ok", fixture
    assert fixture.detail.startswith("running")
