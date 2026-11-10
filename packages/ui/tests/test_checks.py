"""The kept doctor run: when a run starts, what is kept and where, and how a run ends."""

from __future__ import annotations

import asyncio
import json
import sys
import time

import psutil
import pytest

from moxi_ui import page
from moxi_ui.checks import CHECKS_FILE, STALE_AFTER, Checks, read_kept

ROW = {"module": "ui", "name": "shell", "state": "ok", "detail": "running", "fix": None}
FAIL = {"module": "cache", "name": "watcher", "state": "fail", "detail": "not running",
        "fix": "moxi cache watch"}


class _Doctor:
    """A stand-in doctor: each run waits for `go`, then gives `answer`."""

    def __init__(self, answer=None):
        self.answer = answer or {"rows": [ROW], "error": None}
        self.started = 0
        self.go = asyncio.Event()

    async def __call__(self):
        self.started += 1
        await self.go.wait()
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def _ended(checks):
    while checks.running:
        await asyncio.sleep(0.01)


def test_an_ask_starts_a_run_only_when_none_is_kept_or_the_kept_one_is_old(tmp_path):
    now = [1_000_000.0]

    async def scene():
        doctor = _Doctor()
        doctor.go.set()
        checks = Checks(tmp_path / CHECKS_FILE, run=doctor, clock=lambda: now[0])
        first = checks.ask()
        assert first == {"at": None, "running": True, "rows": [], "error": None}
        await _ended(checks)
        kept = checks.value()
        assert kept == {"at": "1970-01-12T13:46:40Z", "running": False, "rows": [ROW],
                        "error": None}
        now[0] += STALE_AFTER
        assert checks.ask() == kept and doctor.started == 1, "a run 300 s old still answers"
        now[0] += 1
        assert checks.ask() == {**kept, "running": True}, "an older one answers and is run again"
        await _ended(checks)
        assert doctor.started == 2 and checks.value()["at"] == "1970-01-12T13:51:41Z"
        now[0] -= 3600
        assert checks.ask()["running"], "a kept run dated after now is run again"
        await _ended(checks)
        assert doctor.started == 3

    asyncio.run(scene())


def test_one_run_goes_at_a_time_and_each_start_and_end_is_told(tmp_path):
    told = []

    async def scene():
        doctor = _Doctor()
        checks = Checks(tmp_path / CHECKS_FILE, run=doctor, changed=told.append)
        assert checks.ask()["running"] and checks.ask()["running"]
        assert checks.run_now()["running"]
        await asyncio.sleep(0.05)
        assert doctor.started == 1 and checks.runs == 1
        doctor.go.set()
        await _ended(checks)
        assert checks.run_now()["running"], "the act starts a run however new the kept one is"
        await _ended(checks)
        assert doctor.started == 2

    asyncio.run(scene())
    assert [value["running"] for value in told] == [True, False, True, False]
    assert told[0]["at"] is None and told[1]["rows"] == [ROW] and told[1]["at"] is not None


def test_the_kept_run_is_read_back_from_its_file(tmp_path):
    path = tmp_path / "state" / CHECKS_FILE

    async def scene():
        doctor = _Doctor({"rows": [ROW, FAIL], "error": None})
        doctor.go.set()
        checks = Checks(path, run=doctor)
        checks.ask()
        await _ended(checks)
        return checks.value()

    value = asyncio.run(scene())
    on_disk = json.loads(path.read_text())
    assert on_disk == {"at": value["at"], "rows": [ROW, FAIL], "error": None}
    assert read_kept(path) == on_disk
    assert [p.name for p in path.parent.iterdir()] == [CHECKS_FILE], "no temp file is left"
    again = Checks(path, run=_Doctor())
    assert again.value() == value and not again.stale()


@pytest.mark.parametrize("text", [
    "", "not json", "[]", '{"at": "yesterday", "rows": [], "error": null}',
    '{"at": "2026-01-01T00:00:00Z", "rows": [{"module": "ui"}], "error": null}',
    '{"at": "2026-01-01T00:00:00Z", "rows": {}, "error": null}',
    '{"at": "2026-01-01T00:00:00Z", "rows": [], "error": 3}',
])
def test_a_file_that_holds_no_run_reads_as_none_kept(tmp_path, text):
    path = tmp_path / CHECKS_FILE
    path.write_text(text)
    assert read_kept(path) is None
    checks = Checks(path, run=_Doctor())
    assert checks.value() == {"at": None, "running": False, "rows": [], "error": None}
    assert checks.stale()


def test_the_value_has_the_routes_declared_shape(tmp_path):
    async def scene():
        doctor = _Doctor({"rows": [FAIL], "error": None})
        doctor.go.set()
        checks = Checks(tmp_path / CHECKS_FILE, run=doctor)
        checks.ask()
        await _ended(checks)
        return checks.value()

    value = asyncio.run(scene())
    assert set(value) == set(page.CHECKS_SHAPE)
    assert set(value["rows"][0]) == set(page.CHECKS_SHAPE["rows"].items.items)
    marks = {key: out.mark for key, out in page.CHECKS_SHAPE["rows"].items.items.items()}
    assert marks == {"module": None, "name": None, "state": None, "detail": "redact",
                     "fix": "redact"}
    assert page.CHECKS_SHAPE["error"].mark == "redact"


@pytest.mark.parametrize("answer, error", [
    ({"rows": [], "error": "moxi doctor ran past 60 s"}, "moxi doctor ran past 60 s"),
    ({"rows": "none", "error": None}, "moxi doctor gave rows the shell can't read"),
    (OSError("no such file"), "moxi doctor could not run: OSError"),
])
def test_a_run_that_ends_without_rows_is_kept_as_its_error(tmp_path, answer, error):
    async def scene():
        doctor = _Doctor(answer)
        doctor.go.set()
        checks = Checks(tmp_path / CHECKS_FILE, run=doctor)
        checks.ask()
        await _ended(checks)
        return checks.value()

    value = asyncio.run(scene())
    assert value["rows"] == [] and value["error"] == error and value["at"] is not None
    assert read_kept(tmp_path / CHECKS_FILE)["error"] == error


def test_with_the_runs_off_nothing_is_started(tmp_path):
    told = []

    async def scene():
        doctor = _Doctor()
        checks = Checks(tmp_path / CHECKS_FILE, run=doctor, changed=told.append,
                        enabled=False)
        nothing = {"at": None, "running": False, "rows": [], "error": None}
        assert checks.ask() == nothing and checks.run_now() == nothing
        await asyncio.sleep(0.05)
        assert doctor.started == 0 and checks.runs == 0

    asyncio.run(scene())
    assert told == [] and not (tmp_path / CHECKS_FILE).exists()


def _gone(pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


def test_closing_cancels_the_run_kills_its_doctor_and_keeps_the_earlier_run(tmp_path):
    path = tmp_path / CHECKS_FILE
    earlier = {"at": "2026-01-01T00:00:00Z", "rows": [ROW], "error": None}
    path.write_text(json.dumps(earlier))
    script = ("import pathlib, subprocess, sys, time\n"
              "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
              "pathlib.Path('pids').write_text(f'{child.pid}')\n"
              "time.sleep(60)\n")

    async def doctor():
        code, out = await page.run_doctor([sys.executable, "-c", script], cwd=str(tmp_path),
                                          env={}, timeout=60)
        return {"rows": [], "error": None}

    async def scene():
        checks = Checks(path, run=doctor)
        assert checks.ask()["running"]
        deadline = time.monotonic() + 10
        while not (tmp_path / "pids").exists() or not (tmp_path / "pids").read_text():
            assert time.monotonic() < deadline, "the doctor never started"
            await asyncio.sleep(0.05)
        started = time.monotonic()
        await checks.aclose()
        assert time.monotonic() - started < 5
        assert not checks.running and checks.value() == {**earlier, "running": False}
        assert checks.run_now()["running"] is False, "a closed one starts nothing"

    asyncio.run(scene())
    assert _gone(int((tmp_path / "pids").read_text())), "the doctor's child outlived the stop"
    assert read_kept(path) == earlier


# --- the gateway's view ------------------------------------------------------------------


def test_the_gateway_reaches_the_read_and_the_topic_and_gets_no_free_text():
    from moxi_ui.gateway.shapes import redact, shape_data, table
    from moxi_ui.loopback.api import export
    from moxi_ui.push import OWN_TOPICS, own_shapes

    built = table({"ui": export(page.PAGE)})
    assert built.find("GET", "/m/ui/api/checks") is not None
    assert built.find("POST", "/m/ui/api/checks/run") is None, "the phone starts no run"
    value = {"at": "2026-01-01T00:00:00Z", "running": False, "rows": [FAIL],
             "error": "moxi doctor ran past 60 s"}
    want = {"at": "2026-01-01T00:00:00Z", "running": False, "error": None,
            "rows": [{"module": "cache", "name": "watcher", "state": "fail",
                      "detail": None, "fix": None}]}
    assert redact(value, shape_data(page.CHECKS_SHAPE), b"k" * 32) == want
    assert "checks" in OWN_TOPICS
    assert own_shapes()["checks"]["items"] == shape_data(page.CHECKS_SHAPE)


# --- a real shell --------------------------------------------------------------------------

PUSH = "/m/ui/api/push?topics=ui.checks"
NOTHING = {"at": None, "running": False, "rows": [], "error": None}


def _settled(shell, *, timeout=90.0):
    """The kept run once no run is going."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, value = shell.session().get("/m/ui/api/checks")
        assert status == 200
        if not value["running"]:
            return value
        time.sleep(0.2)
    pytest.fail("the kept run never ended")


def test_a_test_shell_starts_no_doctor_unless_asked_and_shows_the_run_it_kept(ui_shells,
                                                                               tmp_path):
    from moxi_ui.testing.shell import TestShell

    old = {"at": "2020-01-01T00:00:00Z", "rows": [FAIL], "error": None}
    shell = TestShell(tmp_path)
    state = shell.state_dir("ui")
    state.mkdir(parents=True, exist_ok=True)
    (state / CHECKS_FILE).write_text(json.dumps(old))
    shell = ui_shells(home=tmp_path)
    session = shell.session()
    kept = {**old, "running": False}
    assert session.get("/m/ui/api/checks") == (200, kept)
    assert session.post("/m/ui/api/checks/run") == (200, kept)
    with session.stream(PUSH) as events:
        assert events.until(lambda name, data: name == "ui.checks")[1] == kept
    time.sleep(0.5)
    below = psutil.Process(shell.info.pid).children(recursive=True)
    assert not [p for p in below if "doctor" in p.cmdline()]
    assert session.get("/m/ui/api/checks") == (200, kept)
    assert json.loads((state / CHECKS_FILE).read_text()) == old


def test_a_shell_runs_the_doctor_once_keeps_it_and_tells_the_topic(ui_shells):
    shell = ui_shells(checks=True)
    session = shell.session()
    with session.stream(PUSH) as events:
        assert events.until(lambda name, data: name == "ui.checks")[1] == NOTHING
        first = session.get("/m/ui/api/checks")
        assert first == (200, {**NOTHING, "running": True}), "it answers before the run ends"
        assert session.get("/m/ui/api/checks")[1]["running"]
        started = events.until(lambda name, data: name == "ui.checks" and data["running"])[1]
        assert started == first[1]
        ended = events.until(lambda name, data: name == "ui.checks" and not data["running"])[1]
    assert ended["error"] is None and ended["at"] is not None
    row = next(r for r in ended["rows"] if r["module"] == "ui" and r["name"] == "shell")
    assert row["detail"].startswith("running"), row
    assert {r["module"] for r in ended["rows"]} > {"ui"}, "the run is of every module"
    assert session.get("/m/ui/api/checks") == (200, ended), "a new run answers alone"
    on_disk = json.loads((shell.state_dir("ui") / CHECKS_FILE).read_text())
    assert on_disk == {key: ended[key] for key in ("at", "rows", "error")}
    status, again = session.post("/m/ui/api/checks/run")
    assert status == 200 and again == {**ended, "running": True}
    assert _settled(shell)["rows"]


def test_a_stopping_shell_ends_its_run_and_keeps_nothing_of_it(ui_shells):
    shell = ui_shells(checks=True)
    assert shell.session().get("/m/ui/api/checks")[1]["running"]
    deadline = time.monotonic() + 10
    doctors = []
    while not doctors and time.monotonic() < deadline:
        below = psutil.Process(shell.info.pid).children(recursive=True)
        doctors = [p for p in below if "doctor" in p.cmdline()]
        time.sleep(0.02)
    assert doctors, "the run's doctor never started"
    shell.stop()
    assert all(_gone(p.pid) for p in doctors), "the doctor outlived the shell"
    assert not (shell.state_dir("ui") / CHECKS_FILE).exists()
