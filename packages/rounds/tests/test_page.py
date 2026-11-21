"""rounds' page server in process, through loopback's strict app: every route's answer
fits its declared shape, on a board that holds each kind of thing the page draws.

Loopback is reached as the page reaches it, through `moxi.modules.load`: rounds'
distribution doesn't require it, so these tests skip without it. The app runs behind
`guard` with an upstream profile, as the runner serves it, with strict projection, so a
handler that sends a key its route doesn't declare, or a value of another type, fails
here.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlencode

import pytest

from moxi import modules
from moxi_rounds import (asking, board, freezing, log, opening, ruling, verdicts)

ph = pytest.importorskip("moxi_rounds.pagehandlers")
page = pytest.importorskip("moxi_rounds.page")

BUSY = "kestrel/billing-export"
FRESH = "kestrel/ledger"
SHUT = "kestrel/archive"
BROKEN = "kestrel/broken"
PTR = "comms:1234567"
BASE, TIP = "0" * 40, "1" * 40
TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321

FLOWS = """\
[flows.staged]
steps = [ { step = "design", by = "lead" },
          { step = "verify-design", by = "checker" },
          { step = "build", by = "builder" },
          { step = "staging", does = "command", before = "finish", by = "builder", argv = ["true"] },
          { step = "verify-build", by = "checker" },
          { step = "finish", by = "builder" },
          { step = "retro", by = "worker" } ]
"""


def loopback():
    found = modules.load("ui", interface=3)
    assert found.ok, found.reason
    return found.api.loopback


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Each test gathers on a board of its own, logs each failure class anew and starts
    its own feed."""
    monkeypatch.setattr(ph, "BOARD", ph.Board())
    monkeypatch.setattr(ph, "_logged", set())
    monkeypatch.setattr(ph, "_current", None)
    ph._cards.clear()
    yield
    ph._cards.clear()


def append(round_dir, event_type: str, **fields) -> None:
    log.append(log.round_log(round_dir), event_type, fields, by="lead")


def design(round_dir, name: str, entries: str) -> None:
    (round_dir / "design.md").write_text(
        f"# A design\n\nQ1 is asked here: which name does the export keep?\n\n"
        f"```toml rounds\n{entries}\n```\n", encoding="utf-8")
    freezing.freeze(name, by="lead")
    report = round_dir / "report-1.md"
    report.write_text('# Report\n\n```toml rounds\nverdict = "publishable"\npass = "full"\n'
                      'by = "checker"\nmodel = "m"\nkind = "design"\ndesign = 1\n```\n',
                      encoding="utf-8")
    verdicts.record(name, report, by="checker")


def stage(id_: str) -> str:
    return (f'[[stage]]\nid = "{id_}"\nrepo = "kestrel"\nbranch = "branch-{id_.lower()}"\n'
            "after = []\n")


@pytest.fixture
def kit(tmp_path, add_project, write_config, capsys):
    """The board the shapes are read on, in the project `kestrel`:

    - `billing-export` on the `staged` flow (a step of the user's own): stages B1 to B6,
      so its other stages fold; a question with options and a huge one, a ruling not yet
      shown, B1 commissioned with its check failed, CI red, a verdict sent back with an
      open finding, a major decision that forecloses and a job for him; B2 commissioned
      and dropped.
    - `ledger`, fresh: no stage in its plan.
    - `archive`, closed.
    - `broken`, whose log can't be read.
    - a question for the project itself.
    """
    add_project("kestrel", notes_root=tmp_path / "notes")
    write_config(FLOWS)
    rounds_dir = tmp_path / "notes" / "rounds"
    opening.open_round(BUSY, description="billing export", by="planner", flow="staged")
    for name in (FRESH, SHUT, BROKEN):
        opening.open_round(name, description="a placeholder round", by="planner")
    busy = rounds_dir / "billing-export"
    design(busy, BUSY, "\n".join(stage(f"B{n}") for n in range(1, 7)))
    asking.ask(BUSY, "Which name does the export keep?", by="scout",
               origin="file:design.md#which-name",
               options=("old=the old name", "new=the new name"))
    asking.ask(BUSY, "Why? " * 900, by="scout", origin=PTR)
    asking.ask("kestrel", "Which repo keeps the ledgers?", by="scout", origin=PTR)
    ruling.rule(BUSY, text="Exports keep a header row.", record=PTR, by="lead",
                at="2026-01-02T00:00:00Z")
    for one in ("B1", "B2"):
        append(busy, "build.commissioned", stage=one, repo="kestrel",
               branch=f"branch-{one.lower()}", base=BASE, target="main",
               builder="builder-1", build="0123456789abcdef", worktree="/w/" + one)
    append(busy, "build.dropped", stage="B2")
    append(busy, "check.ran", stage="B1", step="check", commit=TIP, argv=["false"],
           exit_code=1, duration_s=2.5, output="/scratch/check-1.log")
    append(busy, "ci.read", stage="B1", commit=TIP, reading="red", workflows=[
        {"path": ".github/workflows/ci.yml", "reading": "red", "url": "https://ci/1",
         "jobs": [{"name": "linux", "reading": "red", "url": "https://ci/1/1"},
                  {"name": "macos", "reading": "green", "url": "https://ci/1/2"}]}])
    append(busy, "verdict.recorded", kind="build", verdict="fold-first", stage="B1",
           head=TIP, by="checker", model="m", report="report-2.md",
           findings=[{"id": "F1", "severity": "major", "state": "open",
                     "text": "The header is lost."}],
           decisions=[{"id": "M1", "text": "Exports drop the old format.",
                       "weight": "major", "forecloses": True}],
           waits=[{"id": "W1", "text": "Say whether the old format may go."}])
    append(rounds_dir / "archive", "round.closed")
    with log.round_log(rounds_dir / "broken").open("ab") as fh:
        fh.write(b"not an event\n")
    capsys.readouterr()
    return rounds_dir


# -- driving the app ---------------------------------------------------------------------

def served():
    lb = loopback()
    inner = lb.app(page.PAGE, module="rounds", server_id=SERVER_ID, strict=True)
    profile = lb.upstream(lb.build_table(page.PAGE)[0], token=TOKEN, port=PORT)
    return lb.guard(inner, lambda: profile)


def scope(path: str, query: bytes = b"") -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "server": ("127.0.0.1", PORT),
        "client": ("127.0.0.1", 50000), "scheme": "http", "method": "GET",
        "root_path": "", "path": path, "raw_path": path.encode("ascii"),
        "query_string": query,
        "headers": [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode())],
        "state": {},
    }


async def get(app, path: str, query: bytes = b"") -> tuple[int, dict | None]:
    sent = {"status": 0, "body": b""}
    asked = [False]

    async def receive():
        if not asked[0]:
            asked[0] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    await app(scope(path, query), receive, send)
    return sent["status"], json.loads(sent["body"]) if sent["body"] else None


def run(*calls) -> list[tuple[int, dict | None]]:
    """Each `(path, query)` read through one app, in order."""
    async def main():
        app = served()
        return [await get(app, path, urlencode(query or {}).encode()) for path, query in calls]
    return asyncio.run(main())


def conforms(sent, shape) -> bool:
    """Whether `sent` is of the declared `shape`: every key declared, every value of its
    type. A declared key may be absent only where the shape is a union of kinds."""
    if sent is None:
        return shape.nullable
    if shape.type == "table":
        return (isinstance(sent, dict) and set(sent) <= set(shape.items)
                and all(conforms(sent[k], shape.items[k]) for k in sent))
    if shape.type == "list":
        return isinstance(sent, list) and all(conforms(v, shape.items) for v in sent)
    if shape.type == "float":
        return isinstance(sent, (int, float)) and not isinstance(sent, bool)
    return isinstance(sent, {"string": str, "int": int, "bool": bool}[shape.type])


def board_value(kit) -> dict:
    [(status, value)] = run(("/api/rounds", None))
    assert status == 200, value
    return value


# -- the board ---------------------------------------------------------------------------

def test_the_board_fits_its_shape_and_holds_every_kind_of_row(kit):
    value = board_value(kit)
    lb = loopback()
    assert conforms(value, lb.Out("table", items={"now": lb.Out("float"), **page.BOARD}))
    assert set(value) == {"now", *page.BOARD}
    assert value["state"] == "up"
    assert {one["name"] for one in value["rounds"]} == {BUSY, FRESH}
    assert [one["name"] for one in value["unreadable"]] == [BROKEN]
    assert [one["name"] for one in value["closed"]] == [SHUT]
    busy = next(one for one in value["rounds"] if one["name"] == BUSY)
    assert "staging" in [one["id"] for one in value["columns"]]
    assert busy["folds"]["landed"] + busy["folds"]["not_started"]["n"] == 5
    fresh = next(one for one in value["rounds"] if one["name"] == FRESH)
    assert len(fresh["lines"]) == 1
    waiting = value["waiting"]
    assert {one["holder"] for one in waiting["questions"]} == {BUSY, "kestrel"}
    assert [one["id"] for one in waiting["decisions"]] == ["B1.M1"]
    assert [one["id"] for one in waiting["waits"]] == ["B1.W1"]
    assert waiting["not_shown"][0]["rows"]
    assert [(one["stage"], one["act"]) for one in value["recent"]][0][0] == "B1"


def test_a_huge_question_is_cut_on_the_board(kit):
    value = board_value(kit)
    longest = max(len(one["text"]) for one in value["waiting"]["questions"])
    assert longest == ph.TEXT_MAX_CHARS


def test_the_topic_is_the_board_without_now(kit):
    lb = loopback()
    route = next(one for one in page.PAGE.routes if one.path == "/api/events")
    assert set(route.topics) == {ph.TOPIC}
    assert conforms(ph.BOARD.current(), route.topics[ph.TOPIC])
    assert "now" not in route.topics[ph.TOPIC].items


# -- the records -------------------------------------------------------------------------

def _calls(value: dict) -> list[dict]:
    """A record query for every marker on the board, every card and every item."""
    calls = []
    for one in value["rounds"]:
        for line in one["lines"]:
            for column, cell in zip(value["columns"], line["cells"], strict=True):
                if cell is None:
                    continue
                query = {"what": "step", "round": one["name"], "column": column["id"]}
                if column["part"] == "stage":
                    query["stage"] = line["stage"]
                calls.append(query)
        calls.append({"what": "card", "round": one["name"]})
    calls += [{"what": "card", "round": one["name"]} for one in value["closed"]]
    waiting = value["waiting"]
    calls += [{"what": "question", "round": one["holder"], "id": one["id"]}
              for one in waiting["questions"]]
    calls += [{"what": "decision", "round": one["holder"], "id": one["id"]}
              for one in waiting["decisions"]]
    calls += [{"what": "wait", "round": one["round"], "id": one["id"]}
              for one in waiting["waits"]]
    calls += [{"what": "shown", "round": row["holder"], "id": row["id"]}
              for back in waiting["not_shown"] for row in back["rows"]]
    return calls


def test_every_record_fits_its_shape(kit):
    value = board_value(kit)
    calls = _calls(value)
    answers = run(("/api/rounds", None), *(("/api/record", one) for one in calls))[1:]
    lb = loopback()
    shape = lb.Out("table", items={"gone": lb.Out("bool"), "record": page.RECORD})
    whats = set()
    for query, (status, sent) in zip(calls, answers, strict=True):
        assert status == 200, (query, sent)
        assert sent["gone"] is False, query
        assert conforms(sent, shape), query
        whats.add(query["what"])
    assert whats == set(board.WHATS)


def test_the_records_hold_what_the_page_draws(kit):
    board_value(kit)
    [_, (_, check), (_, verify), (_, question), (_, closed)] = run(
        ("/api/rounds", None),
        ("/api/record", {"what": "step", "round": BUSY, "column": "check", "stage": "B1"}),
        ("/api/record", {"what": "step", "round": BUSY, "column": "verify-build",
                         "stage": "B1"}),
        ("/api/record", {"what": "question", "round": BUSY, "id": "Q1"}),
        ("/api/record", {"what": "card", "round": SHUT}))
    check = check["record"]
    assert [one["name"] for one in check["ci"]["jobs"]] == ["linux", "macos"]
    assert check["runs"][0]["output"] == "/scratch/check-1.log"
    assert verify["record"]["findings"]["open"][0]["id"] == "B1.F1"
    question = question["record"]
    assert [one["key"] for one in question["item"]["options"]] == ["old", "new"]
    assert "--chose K" in question["answer"]
    assert "Q1 is asked here" in question["passage"]
    assert closed["record"]["ended"] is not None


def test_a_record_of_a_round_no_longer_open_is_gone(kit):
    board_value(kit)
    [_, (status, sent)] = run(("/api/rounds", None),
                              ("/api/record", {"what": "step", "round": "kestrel/nowhere",
                                               "column": "design"}))
    assert (status, sent) == (200, {"gone": True, "record": None})


@pytest.mark.parametrize("query, key", [
    ({"round": BUSY}, "what"),
    ({"what": "nothing", "round": BUSY}, "what"),
    ({"what": "step"}, "round"),
    ({"what": "step", "round": BUSY}, "column"),
    ({"what": "question", "round": BUSY}, "id"),
])
def test_a_record_query_that_names_too_little_is_refused(kit, query, key):
    [(status, sent)] = run(("/api/record", query))
    assert status == 400
    assert key in json.dumps(sent)


def test_a_record_query_too_long_is_refused(kit):
    [(status, _sent)] = run(("/api/record", {"what": "card",
                                             "round": "k/" + "x" * page.QUERY_MAX}))
    assert status == 400
