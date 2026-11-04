"""The page's job table on its own: the end state each exit code gives, the hold on the
idle exit from submit to end, the order jobs run in, what is kept, and an act that
raises. The hold is a fake that counts what is open."""

from __future__ import annotations

import contextlib
import threading
import time

import pytest

from moxi_services import jobs
from moxi_services.acts import Outcome


class Holds:
    """A stand-in for loopback's `busy`: how many holds of each name are open."""

    def __init__(self):
        self.open: dict[str, int] = {}
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def __call__(self, name: str):
        with self._lock:
            self.open[name] = self.open.get(name, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                self.open[name] -= 1

    def held(self) -> int:
        with self._lock:
            return self.open.get(jobs.HOLD, 0)


@pytest.fixture
def table():
    holds = Holds()
    found = jobs.Jobs(holds)
    found.holds = holds
    yield found
    found.close()


def done(ident: str = "services/web", act: str = "stop", code: int = 0, **extra) -> Outcome:
    return Outcome(ident, act, code, **extra)


def until(check, within: float = 10.0) -> None:
    deadline = time.monotonic() + within
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def ended(table: jobs.Jobs, job: str) -> jobs.Job:
    until(lambda: table.get(job) is not None and table.get(job).state != "running")
    return table.get(job)


def test_an_exit_code_maps_to_its_end_state():
    assert [jobs.state_of(c) for c in (0, 1, 2, 3, 75, 70)] == [
        "done", "failed", "refused", "refused", "refused", "failed"]


def test_a_job_is_running_from_submit_and_ends_with_its_outcome(table):
    gate = threading.Event()
    record, new = table.submit("j", "stop", "services/web",
                               lambda: gate.wait(30) and done(lines=("web stopped",),
                                                              said=("core said",)))
    assert new and record.state == "running"
    assert table.get("j").as_dict() == {"job": "j", "act": "stop", "id": "services/web",
                                        "state": "running", "code": None, "lines": [],
                                        "fix": None}
    gate.set()
    found = ended(table, "j")
    assert (found.state, found.code, found.lines) == ("done", 0,
                                                      ("core said", "web stopped"))


def test_a_refusal_keeps_its_reason_and_fix(table):
    table.submit("j", "start", "services/web",
                 lambda: done(code=3, lines=("no supervisor",), fix="install one"))
    found = ended(table, "j")
    assert (found.state, found.code, found.lines, found.fix) == (
        "refused", 3, ("no supervisor",), "install one")


def test_each_job_holds_the_idle_exit_from_submit_until_it_ends(table):
    first, second = threading.Event(), threading.Event()
    table.submit("a", "stop", "services/a", lambda: first.wait(30) and done())
    table.submit("b", "stop", "services/b", lambda: second.wait(30) and done())
    # The second waits behind the first and holds the exit already.
    assert table.holds.held() == 2
    first.set()
    ended(table, "a")
    until(lambda: table.holds.held() == 1)
    second.set()
    ended(table, "b")
    until(lambda: table.holds.held() == 0)


def test_jobs_run_one_at_a_time_in_the_order_they_came(table):
    order, running, most = [], [0], [0]
    lock = threading.Lock()

    def work(n):
        def run():
            with lock:
                running[0] += 1
                most[0] = max(most[0], running[0])
            time.sleep(0.01)
            order.append(n)
            with lock:
                running[0] -= 1
            return done()
        return run

    for n in range(5):
        table.submit(f"j{n}", "stop", "services/web", work(n))
    ended(table, "j4")
    assert order == [0, 1, 2, 3, 4] and most[0] == 1


def test_a_second_submit_of_a_held_id_queues_nothing(table):
    ran = []
    _, new = table.submit("j", "stop", "services/web", lambda: ran.append(1) or done())
    again, second = table.submit("j", "start", "services/api",
                                 lambda: ran.append(2) or done())
    assert (new, second) == (True, False) and again.act == "stop"
    ended(table, "j")
    assert ran == [1]


def test_the_table_keeps_the_newest_ended_jobs_and_every_running_one():
    table = jobs.Jobs(Holds(), keep=2)
    gate = threading.Event()
    try:
        table.submit("slow", "stop", "services/web", lambda: gate.wait(30) and done())
        for n in range(4):
            table.submit(f"j{n}", "stop", "services/web", done)
        assert [j for j in ("slow", "j0", "j1", "j2", "j3") if table.get(j)] == [
            "slow", "j0", "j1", "j2", "j3"]
        gate.set()
        ended(table, "j3")
        assert [j for j in ("slow", "j0", "j1", "j2", "j3") if table.get(j)] == [
            "j2", "j3"]
    finally:
        table.close()


def test_an_act_that_raises_ends_failed_and_the_thread_runs_on(table, caplog):
    def broken():
        raise RuntimeError("/home/someone/secret")

    table.submit("bad", "stop", "services/web", broken)
    table.submit("next", "stop", "services/web", done)
    found = ended(table, "bad")
    assert (found.state, found.code) == ("failed", 1)
    assert found.lines == (jobs.RAISED.format("RuntimeError"),)
    assert ended(table, "next").state == "done"
    until(lambda: table.holds.held() == 0)
