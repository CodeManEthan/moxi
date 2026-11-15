"""comms' page's acts, `/api/check` and `/api/send`, through loopback's strict app over a
rig's daemon and record.

Each act runs the send verb's own path as the operator's lane, so these tests pin what the
page adds: his user id (the page server's `MOXI_COMMS_OPERATOR`, else the directory's one
operator row), the body's fields reaching the verb, `new_name` set, and answers that carry
an outcome and a code but no text of the verb's. The rig runs on the local backend, whose
token store holds his credential; the strict app fails a value outside its declared shape.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

import pytest
from claim_kit import World
from conftest import wait_until
from moxi.store import transaction
from moxi_comms import api, machine_line, page, pageacts, store
from moxi_comms.paths import comms_db

from test_page import PORT, TOKEN, run, served
from test_page_messages import MAIN, backend  # noqa: F401 -- a fixture, found by name

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="comms runs on Linux only")

KEYS = ["outcome", "message_id", "recipients", "refusal"]
RECIPIENT = ["qualified", "delivery", "cold_at"]


@pytest.fixture(autouse=True)
def fresh_log(monkeypatch):
    monkeypatch.setattr(pageacts, "_logged", set())
    monkeypatch.delenv("MOXI_COMMS_OPERATOR", raising=False)


def act_scope(path: str, size: int) -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "server": ("127.0.0.1", PORT),
        "client": ("127.0.0.1", 50000), "scheme": "http", "method": "POST",
        "root_path": "", "path": path, "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [(b"host", b"127.0.0.1:%d" % PORT), (b"x-moxi-token", TOKEN.encode()),
                    (b"content-type", b"application/json"),
                    (b"content-length", str(size).encode())],
        "state": {},
    }


async def post_raw(path: str, raw: bytes) -> tuple[int, dict | None]:
    sent = {"status": 0, "body": b""}
    asked = [False]

    async def receive():
        if not asked[0]:
            asked[0] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    await served()(act_scope(path, len(raw)), receive, send)
    return sent["status"], json.loads(sent["body"]) if sent["body"] else None


def body(to=MAIN, text="hello", posture="none", *, held=False, **more) -> dict:
    return {"to": to, "text": text, "posture": posture, "held": held, **more}


def act(path: str, **fields) -> dict:
    status, answer = run(post_raw(path, json.dumps(body(**fields)).encode()))
    assert status == 200, answer
    assert list(answer) == KEYS
    for r in answer["recipients"]:
        assert list(r) == RECIPIENT
    return answer


def check(**fields) -> dict:
    return act("/api/check", **fields)


def send(**fields) -> dict:
    return act("/api/send", **fields)


def newest(rig) -> str | None:
    found = rig.record.history(rig.names_channel)
    return found[-1].id if found else None


def operators(*user_ids: str) -> None:
    """Make the directory's operator rows exactly `user_ids`."""
    conn = store.open_write()
    try:
        with transaction(conn):
            row = conn.execute("SELECT * FROM operators LIMIT 1").fetchone()
            cols = [d[0] for d in conn.execute("SELECT * FROM operators LIMIT 0").description]
            conn.execute("DELETE FROM operators")
            for i, user_id in enumerate(user_ids):
                values = dict(zip(cols, row, strict=True))
                values.update(user_id=user_id, name=f"{values['name']}-{i}" if i else
                              values["name"])
                marks = ", ".join("?" * len(cols))
                conn.execute(f"INSERT INTO operators ({', '.join(cols)}) VALUES ({marks})",
                             [values[c] for c in cols])
    finally:
        conn.close()


# --- the table ---------------------------------------------------------------------------

def test_the_acts_declare_their_body_and_answer_and_are_not_the_gateways():
    routes = {r.path: r for r in page.PAGE.routes}
    for path in ("/api/check", "/api/send"):
        route = routes[path]
        assert (route.kind, route.gateway, route.confirmed) == ("act", False, False)
        assert list(route.body) == ["to", "text", "posture", "held", "reply_to"]
        assert [k for k, f in route.body.items() if not f.required] == ["reply_to"]
        assert route.body["to"].max == 200 and route.body["text"].max == 16_000
        assert route.body["posture"].values == ("none", "optional", "once", "open")
        assert route.body_cap == 128 * 1024
        assert dict(route.returns) == page.SENT and list(route.returns) == KEYS
    assert pageacts.ACT_TIMEOUT_S == 10.0


# --- check ---------------------------------------------------------------------------------

def test_check_posts_nothing_and_says_how_it_would_go(rig):
    before = newest(rig)
    answer = check(posture="once")
    assert (answer["outcome"], answer["message_id"], answer["refusal"]) == (
        "would-send", None, None)
    assert [(r["qualified"], r["delivery"]) for r in answer["recipients"]] == [
        (MAIN, "unbound")]
    assert newest(rig) == before


def test_check_with_the_daemon_stopped_is_offline_with_a_code(rig):
    rig.daemon.stop()
    answer = check()
    assert answer["outcome"] == "offline" and answer["refusal"]["code"] == "offline"
    assert answer["recipients"] == [] and answer["message_id"] is None


# --- send ----------------------------------------------------------------------------------

def test_send_posts_as_him_with_the_posture_and_no_hold(rig):
    answer = send(text="ship it", posture="once")
    assert answer["outcome"] == "sent" and answer["refusal"] is None
    assert answer["message_id"] == newest(rig) and answer["message_id"].isdigit()
    assert [(r["qualified"], r["delivery"]) for r in answer["recipients"]] == [
        (MAIN, "unbound")]
    line = machine_line.parse(rig.record.get_message(answer["message_id"]).content)
    assert (line.recipients, line.text, line.posture, line.held) == (
        (MAIN,), "ship it", "once", False)
    found = api.attributed(answer["message_id"])
    assert found["author"] == {"name": "operator", "tier": "operator",
                               "attribution": "verified"}


def test_held_puts_the_hold_on_line_zero_only_when_asked(rig):
    rig.shim(name="main", scope="proj-a")
    assert wait_until(lambda: MAIN in rig.daemon.bindings)
    for held, delivery in ((True, "held-flag"), (False, "live")):
        answer = send(held=held)
        line = machine_line.parse(rig.record.get_message(answer["message_id"]).content)
        assert line.held is held
        assert [r["delivery"] for r in answer["recipients"]] == [delivery]


def test_reply_to_reaches_the_line(rig):
    first = send(text="first")["message_id"]
    answer = send(text="again", reply_to=first)
    line = machine_line.parse(rig.record.get_message(answer["message_id"]).content)
    assert line.reply_to == first


def test_a_name_never_bound_is_sent_since_the_page_sets_new_name(rig):
    to = "newcomer@proj-a"
    bare = api.send(api.Sender.operator(rig.prov.operator_user_id), [to], "hi",
                    posture="none")
    assert (bare.outcome, bare.refusal.code) == ("refused", "never-bound")
    answer = send(to=to)
    assert answer["outcome"] == "sent"
    assert [(r["qualified"], r["delivery"]) for r in answer["recipients"]] == [
        (to, "unbound")]


def test_a_refusal_answers_its_code_and_no_text(rig):
    before = newest(rig)
    long = "x" * 16_000
    for answer in (check(text=long), send(text=long)):
        assert answer == {"outcome": "refused", "message_id": None, "recipients": [],
                          "refusal": {"code": "too-long"}}
    assert newest(rig) == before


# --- his user id ---------------------------------------------------------------------------

@pytest.mark.parametrize("rows", [(), ("900001", "900002")])
def test_no_operator_row_or_two_is_operator_unknown_and_posts_nothing(rig, rows):
    operators(*rows)
    before = newest(rig)
    for answer in (check(), send()):
        assert answer == pageacts.refused("operator-unknown")
    assert newest(rig) == before


def test_the_page_servers_operator_variable_wins_over_the_operator_rows(rig, monkeypatch):
    operators("900001", "900002")
    monkeypatch.setenv("MOXI_COMMS_OPERATOR", rig.prov.operator_user_id)
    assert send()["outcome"] == "sent"
    monkeypatch.setenv("MOXI_COMMS_OPERATOR", "  ")
    assert send()["refusal"] == {"code": "operator-unknown"}, "a blank one is not set"


def test_a_blank_variable_falls_back_to_the_one_operator_row(rig, monkeypatch):
    monkeypatch.setenv("MOXI_COMMS_OPERATOR", "")
    assert pageacts.operator_id() == rig.prov.operator_user_id
    assert send()["outcome"] == "sent"


def test_no_store_is_operator_unknown_and_makes_no_file():
    assert check() == pageacts.refused("operator-unknown")
    assert not comms_db().exists()


def test_a_store_that_cannot_be_read_fails_with_a_code():
    comms_db().parent.mkdir(parents=True, exist_ok=True)
    comms_db().write_bytes(b"not a database " * 100)
    assert send() == pageacts.refused("record-unreadable", "failed")


# --- inside a session ----------------------------------------------------------------------

def test_inside_an_agents_session_both_acts_refuse_his_lane(rig):
    world = World()
    try:
        world.real("alice")          # this process is inside alice's session
        before = newest(rig)
        for answer in (check(), send()):
            assert answer == pageacts.refused("operator-in-session")
        assert newest(rig) == before
    finally:
        world.stop()


def test_an_agents_claim_beside_his_lane_is_operator_and_claim(rig, monkeypatch):
    monkeypatch.setenv("MOXI_COMMS_NAME", "alice")
    for answer in (check(), send()):
        assert answer == pageacts.refused("operator-and-claim")


# --- the body ------------------------------------------------------------------------------

@pytest.mark.parametrize("fields", [
    {"text": "x" * 16_001}, {"to": "x" * 201}, {"posture": "loud"}, {"held": "yes"},
    {"reply_to": 5},
])
def test_a_body_the_page_never_sends_is_refused_by_the_route(rig, fields):
    before = newest(rig)
    for path in ("/api/check", "/api/send"):
        status, _ = run(post_raw(path, json.dumps(body(**fields)).encode()))
        assert status == 400
    assert newest(rig) == before


def test_a_body_without_held_or_posture_is_refused_by_the_route(rig):
    for missing in ("held", "posture", "to", "text"):
        fields = body()
        del fields[missing]
        status, _ = run(post_raw("/api/send", json.dumps(fields).encode()))
        assert status == 400, missing


def test_a_body_over_the_cap_is_refused_before_the_handler(rig, monkeypatch):
    called = []
    monkeypatch.setattr(pageacts, "operator_id", lambda: called.append(1))
    raw = json.dumps(body(text="é" * 16_000, extra="y" * 40_000)).encode()
    assert len(raw) > page.SEND_BODY_CAP
    status, _ = run(post_raw("/api/send", raw))
    assert status == 413 and called == []


def test_sixteen_thousand_escaped_characters_fit_the_cap(rig):
    raw = json.dumps(body(text=" " * 16_000)).encode()
    assert len(raw) == len(json.dumps(body(text="")).encode()) + 6 * 16_000
    status, answer = run(post_raw("/api/check", raw))
    assert status == 200 and answer["refusal"] == {"code": "too-long"}


def test_a_blank_to_is_refused_as_a_request_the_page_never_sends(rig):
    assert check(to="  ") == pageacts.refused("request-shape")


# --- failures and secrets ------------------------------------------------------------------

def test_a_failure_past_the_verb_answers_a_code_and_logs_no_text(rig, monkeypatch, caplog):
    secret = rig.operator_token

    def broken(*a, **kw):
        raise RuntimeError(f"broke holding {secret}")

    monkeypatch.setattr(api, "check", broken)
    monkeypatch.setattr(api, "send", broken)
    with caplog.at_level(logging.DEBUG):
        assert check() == pageacts.refused("page-error", "failed")
        assert send() == pageacts.refused("page-error", "unknown")
    assert "RuntimeError" in caplog.text and secret not in caplog.text
    assert len([r for r in caplog.records if "send box failed" in r.getMessage()]) == 1


def test_his_credential_is_in_no_answer_and_no_log_line(rig, monkeypatch, caplog, capsys):
    secret = rig.operator_token
    assert secret
    rig.shim(name="main", scope="proj-a")
    with caplog.at_level(logging.DEBUG):
        answers = [check(), send(), send(text="x" * 16_000), check(to="nobody@proj-a")]
        operators("900001", "900002")
        answers.append(send())
        operators(rig.prov.operator_user_id)
        rig.daemon.stop()
        answers.append(send())
    assert [a["outcome"] for a in answers] == [
        "would-send", "sent", "refused", answers[3]["outcome"], "refused", "offline"]
    said = json.dumps(answers) + caplog.text + "".join(capsys.readouterr())
    assert secret not in said
