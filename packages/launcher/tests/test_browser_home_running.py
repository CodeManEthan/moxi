"""Home's Sessions row in a browser, through a real shell that starts the launcher's real
page server: no row with nothing running, the count, the list opened in place with at most
four sessions and the link to all of them, where `Open` and that link go, each session's
latest message, the three cases with their words and their order, a waiting session whose
cache nothing keeps warm raising the row above rows that need nothing, and a session that
loses its cover turning loud.

The shell runs the fake turns in turns' place and reads the real cache and sessions over
transcripts the tests write. Each launch is a slot launch whose process is this test's own
and whose claim names it; no slots answers, and the row never asks slots. A launch the
fake turns lists as waiting on a subagent waits; one it does not list is ordinary. The
shell also holds `peer`, a module of the tests' own whose one Home row needs nothing and is
ordered above the Sessions row. Each test ends its launches and waits until none runs.

The last test starts a small install: the shell lists neither sessions, cache, turns nor
comms, and its page servers can't import them.
"""

from __future__ import annotations

import re
import subprocess
import sys
import uuid

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import launcher_page_kit as kit

pytest.importorskip("moxi_cache.api", reason="cache is not installed")
pytest.importorskip("moxi_sessions.api", reason="sessions is not installed")
WAIT_S = pytest.importorskip("moxi_ui.testing", reason="ui is not installed").WAIT_S

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(sys.platform == "win32",
                                 reason="a launch's process is read through the process table")]

ROW, PEER, HEALTH = "launcher.running", "peer.row", "ui.health"
RUNNING = "/m/launcher/api/running"
#: What the seeded transcript's agent said last.
ANSWER = "The login form is fixed."
#: A time of day as a browser's locale writes it.
HOUR = r"\d{1,2}[:.]\d{2}(\s?[AaPp][Mm])?"
#: Seconds within which a joiner pass reads a change in a session (`JOIN_EVERY` and a pass).
PASS_MS = 30_000


@pytest.fixture(scope="module")
def turns(tmp_path_factory):
    return kit.FakeTurns(tmp_path_factory.mktemp("fake-turns"))


@pytest.fixture(scope="module")
def ui_shell_options(turns, tmp_path_factory):
    peer = kit.install_peer(tmp_path_factory.mktemp("peer-site"))
    options = turns.options()
    return {**options, "site": [*options["site"], str(peer)]}


class Launches:
    """The launches one test seeds in a shell, ended when the test ends."""

    def __init__(self, shell, turns, root):
        self.shell, self.turns, self.root = shell, turns, root
        self.ids: list[str] = []
        self.sessions: dict[str, dict] = {}

    def seed(self, name: str, *, said: bool = True, ago_s: float = 60.0,
             waits: bool = False, covered: bool = False) -> tuple[str, str]:
        """A live launch named `name`: with a transcript whose last message was `ago_s`
        seconds ago when `said`; waiting on one subagent when `waits`; with keep-warm on
        and the cache watcher timing it when `covered`. Returns its request and session
        ids."""
        request_id = f"{name}-{uuid.uuid4().hex[:8]}"
        config = self.root / request_id
        values = {"name": name, **({"keep_warm": "on"} if covered else {})}
        sid = kit.seed(self.shell, request_id, live=True, claimed=True, config_dir=config,
                       values=values)
        if said:
            kit.transcript(config, sid, ago_s=ago_s, answered=ANSWER)
        if covered:
            kit.timed(self.shell, sid, ago_s=ago_s)
        if waits:
            self.sessions[sid] = kit.waiting()
            self.turns.script(sessions=self.sessions)
        self.ids.append(request_id)
        return request_id, sid

    def drafted(self, sid: str) -> None:
        """The waiting session `sid` has a draft in its prompt box from now on."""
        self.sessions[sid] = kit.waiting(draft=True)
        self.turns.script(sessions=self.sessions)

    def close(self) -> None:
        self.turns.script()
        kit.end(self.shell, *self.ids)
        kit.published(self.shell, "running", lambda data: data["count"] == 0)


@pytest.fixture
def launches(ui_shell, turns, tmp_path):
    seeded = Launches(ui_shell, turns, tmp_path)
    try:
        yield seeded
    finally:
        seeded.close()


def _home(page):
    return page.locator("#moxi-panel > moxi-ui-page moxi-ui-home")


def _entry(page, key: str = ROW):
    return _home(page).locator(f".entry[data-row='{key}']")


def _line(page, key: str = ROW):
    return _entry(page, key).locator(".row")


def _component(page):
    return _entry(page).locator("moxi-launcher-running")


def _session(page, request_id: str):
    return _component(page).locator(f"moxi-ui-row[data-request='{request_id}']")


def _shown(page) -> list[str]:
    """The rows in sight, top first."""
    return _home(page).locator(".entry").evaluate_all(
        """entries => entries.filter((entry) => entry.getBoundingClientRect().height > 0)
             .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)
             .map((entry) => entry.dataset.row)""")


def _expect_order(page, keys: list[str]) -> None:
    wait_for(lambda: _shown(page) == keys, WAIT_S, interval=0.05,
             what=lambda: f"the rows {keys}; seen {_shown(page)}")


def _joined(shell, request_id: str, **want) -> dict:
    """The running entry of `request_id` once each key of `want` holds its value."""
    def matches(data: dict) -> bool:
        return any(entry["request_id"] == request_id
                   and all(entry.get(k) == v for k, v in want.items())
                   for entry in data["sessions"])

    data = kit.published(shell, "running", matches)
    return next(entry for entry in data["sessions"] if entry["request_id"] == request_id)


def _opened(page):
    """Home opened, with the Sessions row's list opened in place."""
    page.goto("/m/ui/")
    _line(page).click()
    expect(_line(page)).to_have_attribute("aria-expanded", "true")
    return _component(page)


# --- the row and the list ------------------------------------------------------------------

def test_with_nothing_running_there_is_no_row_and_a_launch_brings_it_with_its_count(
        ui_page, ui_shell, launches):
    expect(_line(ui_page, HEALTH)).to_be_visible()
    expect(_component(ui_page)).to_be_attached()
    expect(_entry(ui_page)).to_be_hidden()
    launches.seed("planner", said=False)
    expect(_line(ui_page)).to_be_visible()
    expect(_line(ui_page).locator(".label")).to_have_text("Sessions")
    expect(_line(ui_page).locator(".fact")).to_have_text("1 running")
    expect(_line(ui_page).locator(".fact .heavy")).to_have_count(0)


def test_opened_the_list_shows_four_sessions_newest_first_then_all_sessions_with_the_count(
        ui_page, ui_shell, launches):
    ids = [launches.seed(name, said=False)[0]
           for name in ("planner", "reviewer", "curator", "coder", "critic")]
    _joined(ui_shell, ids[-1])
    component = _opened(ui_page)
    expect(_line(ui_page).locator(".fact")).to_have_text("5 running")
    expect(_line(ui_page).locator(".time")).to_have_text("Hide")
    rows = component.locator("moxi-ui-row")
    expect(rows).to_have_count(4)
    assert rows.evaluate_all("rows => rows.map((row) => row.dataset.request)") == ids[:0:-1]
    expect(component.get_by_role("link", name="All sessions (5)")).to_be_visible()
    # Each session offers `Open` and nothing else; the rows themselves do not open.
    expect(component.get_by_role("link", name="Open")).to_have_count(4)
    expect(component.get_by_role("link")).to_have_count(5)
    expect(component.get_by_role("button")).to_have_count(0)
    for row in rows.all():
        assert row.get_attribute("toggles") is None and row.get_attribute("open") is None


def test_open_goes_to_the_sessions_transcript_and_all_sessions_to_its_list(
        ui_page, ui_shell, launches):
    request_id, sid = launches.seed("planner")
    _joined(ui_shell, request_id)
    _opened(ui_page)
    _session(ui_page, request_id).get_by_role("link", name="Open").click()
    expect(ui_page).to_have_url(re.compile(f"/m/sessions/claude/{sid}$"))
    _opened(ui_page).get_by_role("link", name="All sessions (1)").click()
    expect(ui_page).to_have_url(re.compile(r"/m/sessions/$"))


def test_each_session_shows_its_latest_message_and_its_time_or_when_it_was_launched(
        ui_page, ui_shell, launches):
    said, _ = launches.seed("planner")
    quiet, _ = launches.seed("reviewer", said=False)
    data = kit.published(ui_shell, "running", lambda data: any(
        entry["request_id"] == said and entry["activity"] for entry in data["sessions"]))
    assert data["count"] == 2
    _opened(ui_page)
    row = _session(ui_page, said)
    expect(row.locator(".rest")).to_have_text(ANSWER)
    expect(row.locator(".time")).to_have_text(re.compile(f"^{HOUR}$"))
    expect(row.locator(".you")).to_have_count(0)
    expect(row.locator(".fact")).to_have_text(re.compile(f"^warm until {HOUR}, est\\.$"))
    other = _session(ui_page, quiet)
    expect(other.locator(".rest")).to_have_text("launched")
    expect(other.locator(".time")).to_have_text(re.compile(f"^{HOUR}$"))
    expect(other.locator(".fact")).to_have_count(0)


# --- the three cases ---------------------------------------------------------------------

def test_the_three_cases_read_in_their_order_and_the_uncovered_one_raises_the_row(
        ui_page, ui_shell, launches):
    ordinary, _ = launches.seed("planner", ago_s=60)
    covered, covered_sid = launches.seed("reviewer", ago_s=120, waits=True, covered=True)
    loud, _ = launches.seed("curator", ago_s=180, waits=True)
    _joined(ui_shell, covered, covered=True)
    _joined(ui_shell, loud, waits={"kind": "subagents", "count": 1}, covered=False)
    _joined(ui_shell, ordinary, waits=None)

    ui_page.goto("/m/ui/")
    # Closed, the row leads with the loud session, heavy, and the count goes to the right
    # end; the row rises above the rows that need nothing.
    expect(_line(ui_page).locator(".fact .heavy")).to_have_text(
        re.compile(f"^curator goes cold at {HOUR}$"))
    expect(_line(ui_page).locator(".time")).to_have_text("3 running")
    _expect_order(ui_page, [ROW, PEER, HEALTH])

    component = _opened(ui_page)
    # Opened, the fact is the count alone.
    expect(_line(ui_page).locator(".fact")).to_have_text("3 running")
    expect(_line(ui_page).locator(".fact .heavy")).to_have_count(0)
    rows = component.locator("moxi-ui-row")
    expect(rows).to_have_count(3)
    assert rows.evaluate_all("rows => rows.map((row) => row.dataset.request)") == [
        loud, ordinary, covered]

    row = _session(ui_page, loud)
    expect(row.locator(".fact.strong")).to_have_text(
        re.compile(f"^goes cold at {HOUR}, est\\.$"))
    expect(row.locator(".lead.strong")).to_have_text("waiting on 1 subagent")
    expect(row.locator(".rest")).to_have_text(ANSWER)

    row = _session(ui_page, ordinary)
    expect(row.locator(".fact")).to_have_text(re.compile(f"^warm until {HOUR}, est\\.$"))
    expect(row.locator(".strong")).to_have_count(0)
    expect(row.locator(".lead")).to_have_count(0)

    row = _session(ui_page, covered)
    expect(row.locator(".fact")).to_have_text("kept warm while 1 subagent runs")
    expect(row.locator(".strong")).to_have_count(0)
    expect(row.locator(".lead")).to_have_count(0)

    # A draft typed into the covered session's box: keep-warm would not nudge it, so the
    # next pass reads it uncovered and it turns loud.
    launches.drafted(covered_sid)
    row = _session(ui_page, covered)
    expect(row.locator(".fact.strong")).to_have_text(
        re.compile(f"^goes cold at {HOUR}, est\\.$"), timeout=PASS_MS)
    expect(row.locator(".lead.strong")).to_have_text("waiting on 1 subagent")


def test_home_sorts_by_need_before_order_a_waiting_session_rises_above_a_row_ordered_first(
        ui_page, ui_shell, launches):
    request_id, sid = launches.seed("planner")
    _joined(ui_shell, request_id, waits=None)
    ui_page.goto("/m/ui/")
    expect(_line(ui_page).locator(".fact")).to_have_text("1 running")
    expect(_line(ui_page, PEER).locator(".fact")).to_have_text("quiet")
    # Neither needs anything: peer's row is ordered first.
    _expect_order(ui_page, [PEER, ROW, HEALTH])
    # The session waits on a subagent while its cache is warm and nothing keeps it warm:
    # the row needs the user and rises above peer's.
    launches.sessions[sid] = kit.waiting()
    launches.turns.script(sessions=launches.sessions)
    expect(_line(ui_page).locator(".fact .heavy")).to_have_text(
        re.compile(f"^planner goes cold at {HOUR}$"), timeout=PASS_MS)
    _expect_order(ui_page, [ROW, PEER, HEALTH])


# --- a small install -----------------------------------------------------------------------

SMALL = ("sessions", "cache", "turns", "comms")
_AVAILABLE = "import sys; from moxi import modules; print(*map(modules.available, sys.argv[1:]))"


@pytest.fixture(scope="module")
def small_shell(ui_shells, tmp_path_factory):
    """A shell that lists none of `SMALL`, whose page servers can't import them either."""
    hidden = kit.left_out(tmp_path_factory.mktemp("left-out"), *SMALL)
    return ui_shells(site=[str(hidden)], leave_out=list(SMALL))


def test_without_sessions_cache_turns_or_comms_a_session_shows_when_it_was_launched(
        small_shell, ui_contexts, tmp_path):
    # A process the shell starts finds none of them.
    found = subprocess.run([sys.executable, "-c", _AVAILABLE, *SMALL], env=small_shell.env,
                           capture_output=True, text=True, timeout=30)
    assert found.stdout.split() == ["False"] * len(SMALL), (found.stdout, found.stderr)
    turns = kit.FakeTurns(tmp_path / "unused")
    small = Launches(small_shell, turns, tmp_path)
    try:
        # Everything the full install would read is there: a transcript, keep-warm on and
        # a clock record.
        request_id, _ = small.seed("planner", covered=True)
        # The feed's first read of the route waits for the joiner's first pass.
        status, data = kit.session(small_shell).get(RUNNING)
        assert status == 200 and data["count"] == 1, (status, data)
        entry = data["sessions"][0]
        assert (entry["cache"], entry["waits"], entry["covered"], entry["activity"]) == (
            None, None, False, None)

        page = ui_contexts(small_shell).pages[0]
        component = _opened(page)
        expect(_line(page).locator(".fact")).to_have_text("1 running")
        row = _session(page, request_id)
        expect(row.locator(".name")).to_have_text("planner")
        expect(row.locator(".where")).to_have_text("slot 3 · page")
        expect(row.locator(".time")).to_have_text(re.compile(f"^{HOUR}$"))
        expect(row.locator(".rest")).to_have_text("launched")
        expect(row.locator(".fact")).to_have_count(0)
        expect(row.locator(".lead")).to_have_count(0)
        component.get_by_role("link", name="Open").click()
        expect(page).to_have_url(re.compile(r"/m/launcher/$"))
        _opened(page).get_by_role("link", name="All sessions (1)").click()
        expect(page).to_have_url(re.compile(r"/m/launcher/$"))
    finally:
        small.close()
