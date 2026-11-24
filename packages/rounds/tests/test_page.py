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
from pathlib import Path
from urllib.parse import urlencode

import pytest

import page_kit
from moxi import modules
from moxi_rounds import board
from page_kit import BROKEN, BUSY, FRESH, SHUT, SMALL

ph = pytest.importorskip("moxi_rounds.pagehandlers")
page = pytest.importorskip("moxi_rounds.page")
TOKEN = "T" * 43
SERVER_ID = "0123456789abcdef0123456789abcdef"
PORT = 4321


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


@pytest.fixture
def kit(tmp_path, capsys):
    """The board the shapes are read on: `page_kit`'s rounds."""
    rounds_dir = page_kit.seed(tmp_path / "notes")
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
    assert {one["name"] for one in value["rounds"]} == {BUSY, FRESH, SMALL}
    assert [one["name"] for one in value["unreadable"]] == [BROKEN]
    assert [one["name"] for one in value["closed"]] == [SHUT]
    busy = next(one for one in value["rounds"] if one["name"] == BUSY)
    assert "staging" in [one["id"] for one in value["columns"]]
    assert busy["folds"]["landed"] + busy["folds"]["not_started"]["n"] == 5
    fresh = next(one for one in value["rounds"] if one["name"] == FRESH)
    assert len(fresh["lines"]) == 1
    small = next(one for one in value["rounds"] if one["name"] == SMALL)
    assert small["flow"] == "small"
    [line] = [one for one in small["lines"] if one["stage"] == "B1"]
    cells = dict(zip([one["id"] for one in value["columns"]], line["cells"], strict=True))
    assert (cells["check"]["state"], cells["check"]["word"]) == ("failed", "failed")
    assert cells["verify-design"] is None
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
    calls += [{"what": what, "round": row["holder"], "id": row["id"]}
              for back in waiting["not_shown"] for row in back["rows"]
              for what in ("shown", "ruling")]
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


def test_a_build_in_git_shows_its_state_and_its_failed_check(kit):
    board_value(kit)
    tip = page_kit.git(Path.home() / "roots" / "osprey", "rev-parse", "branch-b1")
    [_, (_, build), (_, check)] = run(
        ("/api/rounds", None),
        ("/api/record", {"what": "step", "round": SMALL, "column": "build", "stage": "B1"}),
        ("/api/record", {"what": "step", "round": SMALL, "column": "check", "stage": "B1"}))
    facts = {one["key"]: one["value"] for one in build["record"]["facts"]}
    assert facts["state"] == "building"
    assert facts["why"] == "branch-b1 holds work main does not"
    assert facts["head"] == tip
    check = check["record"]
    assert (check["state"], check["word"]) == ("failed", "failed")
    [ran] = check["runs"]
    assert (ran["exit_code"], ran["commit"]) == (1, tip)


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
    ({"what": "ruling", "round": BUSY}, "id"),
])
def test_a_record_query_that_names_too_little_is_refused(kit, query, key):
    [(status, sent)] = run(("/api/record", query))
    assert status == 400
    assert key in json.dumps(sent)


def test_a_record_query_too_long_is_refused(kit):
    [(status, _sent)] = run(("/api/record", {"what": "card",
                                             "round": "k/" + "x" * page.QUERY_MAX}))
    assert status == 400


# -- what acting on the page reads -------------------------------------------------------

def _answer_file(tmp_path, question: str = "Q1") -> str:
    """An answer prepared for `question`, recommending `new`."""
    path = tmp_path / "answer.md"
    options = "\n\n".join(f'[[option]]\nkey = "{key}"\ncosts = "a rename"\n'
                          'forecloses = "nothing"' for key in ("old", "new"))
    path.write_text(
        '# An answer\n\n```toml rounds\nkind = "answer"\n'
        f'question = "{question}"\nby = "scout"\nmodel = "a-model"\nrecommend = "new"\n'
        'sure = "medium"\nwhy = "the old name is read nowhere"\n'
        'flips = "a reader of the old name"\nread = ["file:design.md#which-name"]\n\n'
        f"{options}\n```\n", encoding="utf-8")
    return str(path)


def _round(value: dict, name: str) -> dict:
    return next(one for one in value["rounds"] if one["name"] == name)


def test_each_round_and_its_card_name_its_lead(kit):
    from moxi_rounds import leads

    value = board_value(kit)
    assert _round(value, BUSY)["lead"] is None
    leads.mark(BUSY, "kestrel-lead", by="planner")
    [(_, value), (_, card)] = run(("/api/rounds", None),
                                  ("/api/record", {"what": "card", "round": BUSY}))
    assert _round(value, BUSY)["lead"] == {"name": "kestrel-lead", "from": "round"}
    assert _round(value, FRESH)["lead"] is None
    assert card["record"]["lead"] == {"name": "kestrel-lead", "from": "round"}


def test_parked_questions_are_listed_apart_and_not_put_to_the_operator(kit):
    from moxi_rounds import ruling

    ruling.rule(BUSY, item="park:Q2", record=page_kit.PTR, by="lead")
    waiting = board_value(kit)["waiting"]
    assert [one["id"] for one in waiting["parked_questions"]] == ["Q2"]
    assert waiting["parked_questions"][0]["state"] == "parked"
    assert "until" not in waiting["parked_questions"][0]
    assert "Q2" not in [one["id"] for one in waiting["questions"] if one["holder"] == BUSY]
    assert waiting["parked"] == 1


def test_a_questions_record_holds_the_ruling_that_put_it_where_it_is(kit):
    from moxi_rounds import ruling

    ruled = ruling.rule(BUSY, item="Q1", chose="new", record=page_kit.PTR, by="lead")
    parked = ruling.rule(BUSY, item="park:Q2", record=page_kit.PTR, by="lead")
    [_, (_, q1), (_, q2), (_, project)] = run(
        ("/api/rounds", None),
        ("/api/record", {"what": "question", "round": BUSY, "id": "Q1"}),
        ("/api/record", {"what": "question", "round": BUSY, "id": "Q2"}),
        ("/api/record", {"what": "question", "round": "kestrel", "id": "P1"}))
    lb = loopback()
    shape = lb.Out("table", items={"gone": lb.Out("bool"), "record": page.RECORD})
    assert all(conforms(one, shape) for one in (q1, q2, project))
    assert q1["record"]["item"]["state"] == "ruled"
    assert q1["record"]["ruling"] == {"id": ruled.id, "text": ruled.text,
                                      "record": page_kit.PTR, "actor": "lead",
                                      "at": ruled.event["at"], "shown": False}
    assert (q2["record"]["item"]["state"], q2["record"]["ruling"]["id"]) == ("parked",
                                                                            parked.id)
    assert project["gone"] is False
    assert (project["record"]["item"]["state"], project["record"]["ruling"]) == ("open", None)


def test_a_ruled_project_question_answers_from_the_projects_log(kit):
    from moxi_rounds import ruling

    ruled = ruling.rule("kestrel", item="P1", text="The ledgers stay in kestrel.",
                        record=page_kit.PTR, by="lead")
    [_, (_, sent)] = run(("/api/rounds", None),
                         ("/api/record", {"what": "question", "round": "kestrel", "id": "P1"}))
    assert sent["record"]["item"]["state"] == "ruled"
    assert sent["record"]["ruling"]["id"] == ruled.id


def test_a_question_no_log_holds_is_gone(kit):
    [_, (_, sent)] = run(("/api/rounds", None),
                         ("/api/record", {"what": "question", "round": BUSY, "id": "Q9"}))
    assert sent == {"gone": True, "record": None}


def _ruling_records(holder: str, *ids: str) -> list[dict]:
    """The ruling records of `ids` on `holder`, read after a gather."""
    answers = run(("/api/rounds", None),
                  *(("/api/record", {"what": "ruling", "round": holder, "id": one})
                    for one in ids))[1:]
    lb = loopback()
    shape = lb.Out("table", items={"gone": lb.Out("bool"), "record": page.RECORD})
    assert all(status == 200 and conforms(sent, shape) for status, sent in answers)
    return [sent["record"] for _status, sent in answers]


def _fields(ruled, *, actor: str, shown: bool) -> dict:
    return {"id": ruled.id, "text": ruled.text, "record": ruled.event["record"],
            "actor": actor, "at": ruled.event["at"], "shown": shown}


def test_a_rulings_record_holds_the_newest_ruling_that_replaces_it_in_any_shown_state(kit):
    from moxi_rounds import ruling, shown

    [first] = _ruling_records(BUSY, "C1")
    assert (first["what"], first["holder"], first["ruling"]["id"]) == ("ruling", BUSY, "C1")
    assert (first["ruling"]["actor"], first["ruling"]["shown"]) == ("lead", False)
    assert first["replaced_by"] is None
    second = ruling.rule(BUSY, text="Exports keep no header row.", supersedes="C1",
                         record="comms:7000001", by="builder-1")
    [record] = _ruling_records(BUSY, "C1")
    assert record["replaced_by"] == _fields(second, actor="builder-1", shown=False)
    # Read back elsewhere: the replacing ruling no longer waits to be shown, and the
    # record still finds it, shown.
    shown.mark(BUSY, second.id, by="lead", record="comms:7000002")
    value = board_value(kit)
    assert second.id not in [row["id"] for back in value["waiting"]["not_shown"]
                             for row in back["rows"] if row["holder"] == BUSY]
    [record] = _ruling_records(BUSY, "C1")
    assert record["replaced_by"] == _fields(second, actor="builder-1", shown=True)
    assert record["ruling"]["shown"] is False
    # A correction of a ruling replaced already replaces the one in its place, and the
    # first ruling's record follows to the newest.
    third = ruling.rule(BUSY, text="Exports keep a header row, named.", supersedes=second.id,
                        record="comms:7000003", by="atlas")
    first, middle, newest = _ruling_records(BUSY, "C1", second.id, third.id)
    assert first["replaced_by"] == _fields(third, actor="atlas", shown=False)
    assert middle["replaced_by"] == first["replaced_by"]
    assert newest["replaced_by"] is None


def test_a_project_rulings_record_answers_from_the_projects_log(kit):
    from moxi_rounds import ruling, shown

    first = ruling.rule("kestrel", text="Ledgers stay in kestrel.", record=page_kit.PTR,
                        by="lead")
    [record] = _ruling_records("kestrel", first.id)
    assert (record["holder"], record["ruling"]["id"], record["replaced_by"]) == (
        "kestrel", first.id, None)
    second = ruling.rule("kestrel", text="Ledgers move to osprey.", supersedes=first.id,
                         record="comms:7000004", by="builder-1")
    shown.mark("kestrel", second.id, by="lead", record="comms:7000005")
    [record] = _ruling_records("kestrel", first.id)
    assert record["replaced_by"] == _fields(second, actor="builder-1", shown=True)


def test_a_ruling_no_log_holds_is_gone(kit):
    [_, (_, missing), (_, nowhere)] = run(
        ("/api/rounds", None),
        ("/api/record", {"what": "ruling", "round": BUSY, "id": "C9"}),
        ("/api/record", {"what": "ruling", "round": "kestrel/nowhere", "id": "C1"}))
    assert missing == nowhere == {"gone": True, "record": None}


def test_a_prepared_answer_rides_on_the_question_and_its_record(kit, tmp_path):
    from moxi_rounds import preparing

    assert all(one["prepared"] is None for one in board_value(kit)["waiting"]["questions"])
    preparing.prepare(BUSY, "Q1", _answer_file(tmp_path), by="scout")
    [(_, value), (_, record)] = run(("/api/rounds", None),
                                    ("/api/record", {"what": "question", "round": BUSY,
                                                     "id": "Q1"}))
    [q1] = [one for one in value["waiting"]["questions"]
            if one["holder"] == BUSY and one["id"] == "Q1"]
    assert (q1["prepared"]["recommend"], q1["prepared"]["current"]) == ("new", True)
    assert record["record"]["item"]["prepared"]["why"] == "the old name is read nowhere"


# -- the acts ----------------------------------------------------------------------------

ACTS = {"/api/answers", "/api/drop/build"}
READS = {"/api/agents", "/api/drop"}


def test_the_act_routes_are_acts_at_the_desk_and_none_is_the_gateways():
    routes = {one.path: one for one in page.PAGE.routes}

    assert {path for path, one in routes.items() if one.kind == "act"} == ACTS
    assert READS <= {path for path, one in routes.items() if one.kind == "read"}
    assert not any(one.gateway for one in page.PAGE.routes)
    receipt = page.RECEIPT.items
    assert receipt["removed"].items.mark == "path"
    assert {receipt[key].mark for key in ("reason", "fix", "not_told", "command")} == {
        "scrub"}
    assert page.DROP_FACTS["folders"].items.items["path"].mark == "path"


async def post(app, path: str, body: dict) -> tuple[int, dict | None]:
    sent = {"status": 0, "body": b""}
    data = json.dumps(body).encode()
    asked = [False]

    async def receive():
        if not asked[0]:
            asked[0] = True
            return {"type": "http.request", "body": data, "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    asked_scope = {**scope(path), "method": "POST"}
    asked_scope["headers"] = [*asked_scope["headers"], (b"content-type", b"application/json"),
                              (b"content-length", str(len(data)).encode())]
    await app(asked_scope, receive, send)
    return sent["status"], json.loads(sent["body"]) if sent["body"] else None


def run_posts(*calls) -> list[tuple[int, dict | None]]:
    async def main():
        app = served()
        return [await post(app, path, body) for path, body in calls]
    return asyncio.run(main())


@pytest.fixture
def no_comms(monkeypatch):
    monkeypatch.setattr(ph, "RECEIPTS", ph.pageacts.Receipts())
    monkeypatch.setattr(ph, "comms_api", lambda: None)


def answers_body(**more) -> dict:
    return {"act": ["A" * 43, "B" * 43], "holder": [BUSY, BUSY], "item": ["Q1", "Q2"],
            "kind": ["pick", "words"], "chose": ["new", ""], "text": ["", "keep both"],
            "tell": ["", ""], "to": "each", **more}


def test_an_answers_post_fits_its_shape_and_a_repeat_is_answered_from_the_store(
        kit, no_comms):
    lb = loopback()
    route = next(one for one in page.PAGE.routes if one.path == "/api/answers")
    shape = lb.Out("table", items=route.returns)

    [(status, first), (again, second)] = run_posts(("/api/answers", answers_body()),
                                                   ("/api/answers", answers_body()))

    assert (status, again) == (200, 200), first
    assert conforms(first, shape) and conforms(second, shape)
    assert [one["state"] for one in first["receipts"]] == ["recorded", "kept"]
    assert first["receipts"][1]["fix"].startswith("moxi rounds rule kestrel/billing-export Q2")
    assert second["receipts"][0]["repeat"]


@pytest.fixture
def stand_in_comms(monkeypatch):
    """comms' API stood in for, as the answers route loads it."""
    from test_page_answers import Comms

    comms = Comms(bound=("builder-1",))
    monkeypatch.setattr(ph, "RECEIPTS", ph.pageacts.Receipts())
    monkeypatch.setattr(ph, "comms_api", lambda: comms)
    return comms


def test_an_answers_post_with_comms_fits_its_shape_told_sent_and_not_told(
        kit, stand_in_comms):
    lb = loopback()
    route = next(one for one in page.PAGE.routes if one.path == "/api/answers")
    shape = lb.Out("table", items=route.returns)
    refused = {"act": ["D" * 43, "E" * 43], "holder": [BUSY, BUSY], "item": ["Q2", "Q2"],
               "kind": ["park", "explain"], "chose": ["", ""], "text": ["", "why two?"],
               "tell": ["", ""], "to": "builder-1"}

    [(status, told)] = run_posts(("/api/answers", answers_body(to="builder-1")))
    stand_in_comms.refuse["builder-1"] = "the daemon is down"
    [(again, not_told)] = run_posts(("/api/answers", refused))

    assert (status, again) == (200, 200), (told, not_told)
    assert conforms(told, shape) and conforms(not_told, shape)
    assert [one["state"] for one in told["receipts"]] == ["recorded", "sent"]
    assert told["receipts"][0]["message"]["id"] and told["receipts"][1]["command"]
    assert [one.get("refused") for one in told["messages"]] == [None]
    assert [one["state"] for one in not_told["receipts"]] == ["recorded-not-told", "refused"]
    assert "the daemon is down" in not_told["messages"][0]["refused"]


def test_an_answers_post_that_cant_be_read_is_refused(kit, no_comms):
    [(status, sent)] = run_posts(("/api/answers", answers_body(kind=["pick", "shout"])))

    assert status == 400, sent


def test_the_agents_and_the_drop_reads_fit_their_shapes(kit, no_comms):
    lb = loopback()
    routes = {one.path: one for one in page.PAGE.routes}

    [(status, agents), (dropped, facts)] = run(
        ("/api/agents", None), ("/api/drop", {"round": BUSY, "stage": "B1"}))

    assert (status, dropped) == (200, 200)
    assert agents == {"agents": [], "reason": "comms is not installed", "comms": False}
    assert conforms(facts, lb.Out("table", items=routes["/api/drop"].returns))
    assert facts["open"] and facts["branch"] == "branch-b1"


def test_a_drop_post_fits_its_shape(kit, no_comms):
    lb = loopback()
    route = next(one for one in page.PAGE.routes if one.path == "/api/drop/build")
    body = {"round": BUSY, "stage": "B1", "act": "C" * 43}

    [(status, sent), (again, second)] = run_posts(("/api/drop/build", body),
                                                  ("/api/drop/build", body))

    assert (status, again) == (200, 200), sent
    assert conforms(sent, lb.Out("table", items=route.returns))
    assert sent["state"] == "recorded" and second["repeat"]


def test_a_drop_receipt_that_keeps_a_worktree_fits_its_shape(kit, no_comms, monkeypatch):
    lb = loopback()
    route = next(one for one in page.PAGE.routes if one.path == "/api/drop/build")
    kept = {"act": "C" * 43, "holder": BUSY, "item": "B1", "kind": "drop",
            "state": "recorded", "removed": ["/w/scratch"],
            "kept": [{"path": "/w/B1", "why": "it holds work that is not committed (a.txt)"}],
            "refs": [], "unscanned": [], "not_told": "comms is not installed"}
    monkeypatch.setattr(ph.pageacts, "drop", lambda *args, **kwargs: kept)

    [(status, sent)] = run_posts(("/api/drop/build", {"round": BUSY, "stage": "B1",
                                                      "act": "C" * 43}))

    assert status == 200 and conforms(sent, lb.Out("table", items=route.returns))
    assert sent["kept"] == kept["kept"]
