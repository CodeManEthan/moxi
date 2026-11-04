"""comms' provider for the connector, against a real daemon on the rig: the four tools,
only the identity's mail, the identity bound as `connector`, and the provider through
the connector's own seam. Nothing here starts the connector's listener."""

from __future__ import annotations

import ast
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import modules

from moxi_comms import directory, kinds
from moxi_comms.config import CONNECTOR_WAIT_CEILING, ConnectorConfig
from moxi_comms.client import DaemonClient, DaemonError, DaemonOffline
from moxi_comms.connector import tools as tools_mod
from moxi_comms.connector.link import BOUND, HELD, Link
from moxi_comms.paths import comms_db, daemon_socket

from conftest import wait_until, write_transcript

_host = modules.load("connector", interface=1)
if not _host.ok:
    pytest.skip(f"the connector module is not installed: {_host.reason}",
                allow_module_level=True)
provider_api = _host.api
Result = provider_api.Result
SRC = Path(__file__).resolve().parents[1] / "src" / "moxi_comms"
ME = "claude-app"


class Ctx:
    """What the connector gives one call: `after_answer` and `log`, recorded."""

    def __init__(self):
        self.after, self.fields = [], {}
        self.grant_id, self.grant_label, self.request_id = "g1", "phone", "r1"

    def after_answer(self, fn):
        self.after.append(fn)

    def log(self, **fields):
        bad = provider_api.refused_field(fields)
        assert bad is None, bad
        self.fields.update(fields)

    async def answered(self, written=True):
        for fn in self.after:
            await fn(written)


def fast():
    return iter(lambda: 0.05, None)


@pytest.fixture
def link(rig):
    got = Link(ME, backoff=fast, held_retry=0.2)
    got.start()
    yield got
    got.stop()


@pytest.fixture
def tools(rig, link):
    assert wait_until(lambda: link.bound)
    return tools_mod.Tools(link, result=Result, refused=Result.refused, wait_default=2,
                           wait_max=5, record=rig.record)


def run(coro):
    return asyncio.run(coro)


def planner(rig):
    return rig.shim(name="planner", scope="proj-a", project="proj-a")


def post(rig, to, text="hi", *, author="planner@proj-a", reply_to=None):
    return str(rig.post_as(author, to, text, reply_to=reply_to).id)


def unread(rig):
    """The ids the daemon handed the identity's binding and has not had acked."""
    b = rig.daemon.bindings.get(ME)
    return set() if b is None else {str(i) for i in b.delivered - b.handled}


def call(tools, name, args=None, *, written=True):
    async def go():
        ctx = Ctx()
        got = await getattr(tools, name)(args or {}, ctx)
        await ctx.answered(written)
        return got, ctx
    return run(go())


# --------------------------------------------------------------------------- the identity

def test_the_identity_is_provisioned_at_start_and_bound_as_connector(rig, link):
    assert wait_until(lambda: link.state == BOUND)
    b = rig.daemon.bindings[ME]
    assert (b.adapter, b.tier) == ("connector", "standing")
    db = directory.connect(comms_db(), read_only=True)
    try:
        assert directory.standing_row(db, ME, "global") is not None
    finally:
        db.close()
    assert link.status() == [("identity", "ok", f"bound as {ME}, adapter connector")]
    assert rig.daemon.kinds.get("connector").cache == kinds.CACHE_PERSON


def test_a_held_name_makes_the_tools_refuse_and_the_status_name_the_holder(rig):
    other = DaemonClient(daemon_socket()).connect()
    other.bind(name=ME, global_=True, adapter="claude-shim")
    link = Link(ME, backoff=fast, held_retry=0.1)
    link.start()
    try:
        assert wait_until(lambda: link.state == HELD)
        [row] = link.status()
        assert row[:2] == ("identity", "fail") and "claude-shim" in row[2]
        t = tools_mod.Tools(link, result=Result, refused=Result.refused, wait_default=2,
                            wait_max=5, record=rig.record)
        got, _ = call(t, "send", {"to": "planner@proj-a", "text": "x"})
        assert got.is_error and got.refusal == "name-held" and "claude-shim" in got.text
        other.close()
        assert wait_until(lambda: link.bound)
        assert rig.daemon.bindings[ME].adapter == "connector"
    finally:
        other.close()
        link.stop()


def test_the_provider_never_binds_the_operator():
    with pytest.raises(ValueError):
        Link("operator")


# --------------------------------------------------------------------------- send

def test_send_says_each_recipients_state(rig, tools):
    planner(rig)
    sid = "cold-session"
    write_transcript(sid, age=7200)
    rig.shim(name="lead", scope="proj-a", project="proj-a", session_id=sid)
    got, ctx = call(tools, "send", {"to": ["planner@proj-a", "lead@proj-a", "main@proj-b"],
                                    "text": "status?", "posture": "none"})
    assert not got.is_error, got.text
    states = {r["name"]: r["delivery"] for r in got.structured["recipients"]}
    assert states == {"planner@proj-a": "live", "lead@proj-a": "held-cold",
                      "main@proj-b": "unbound"}
    assert "planner@proj-a: delivered now" in got.text
    assert "lead@proj-a: held for its next turn" in got.text
    assert "main@proj-b: waiting" in got.text
    msg = rig.record.get_message(got.structured["message_id"])
    assert msg.author_id == ME
    assert ctx.fields["outcome"] == "sent"


def test_send_refusal_is_an_error_result(rig, tools):
    got, _ = call(tools, "send", {"to": "nobody-ever", "text": "x"})
    assert got.is_error and got.refusal


# --------------------------------------------------------------------------- check_messages

def test_check_messages_oldest_first_with_limit_and_read_means_written(rig, tools, link):
    ids = [post(rig, [ME], f"m{i}") for i in range(3)]
    assert wait_until(lambda: len(link.pending()) == 3)
    got, _ = call(tools, "check_messages", {"limit": 2})
    assert [m["id"] for m in got.structured["messages"]] == ids[:2]
    assert got.structured["more_waiting"] == 1
    assert got.text.startswith("2 new messages.") and got.text.endswith("1 more waiting.")
    assert "From planner@proj-a, just now, msg" in got.text
    assert wait_until(lambda: ME not in rig.daemon.queue
                      or set(rig.daemon.queue[ME]) == {ids[2]})
    got, _ = call(tools, "check_messages")
    assert [m["id"] for m in got.structured["messages"]] == ids[2:]
    got, _ = call(tools, "check_messages")
    assert got.text == "No new messages." and got.structured["more_waiting"] == 0


def test_a_failed_write_leaves_the_messages_unread(rig, tools, link):
    ids = [post(rig, [ME], f"m{i}") for i in range(2)]
    assert wait_until(lambda: len(link.pending()) == 2)
    got, _ = call(tools, "check_messages", written=False)
    assert [m["id"] for m in got.structured["messages"]] == ids
    assert wait_until(lambda: len(link.pending()) == 2)
    got, _ = call(tools, "check_messages")
    assert [m["id"] for m in got.structured["messages"]] == ids


def test_a_failed_count_read_answers_an_error_and_reads_nothing(rig, tools, link):
    ids = [post(rig, [ME], f"m{i}") for i in range(2)]
    assert wait_until(lambda: len(link.pending()) == 2)
    real = link.read

    def failing(method, params=None):
        if method == "agents":
            raise DaemonOffline("the short read failed")
        return real(method, params)

    link.read = failing
    try:
        got, ctx = call(tools, "check_messages")     # the error answer was written
    finally:
        link.read = real
    assert got.is_error and got.refusal == "offline" and "Nothing was" in got.text
    assert ctx.after == [] and link.pending() and unread(rig) == set(ids)
    got, _ = call(tools, "check_messages")
    assert [m["id"] for m in got.structured["messages"]] == ids


def test_a_cancel_while_the_digest_opens_leaves_the_mail_unread(rig, tools, link):
    """The connector cancels a call whose caller hung up. A cancel that lands while the
    daemon opens the digest must not leave the messages in a digest nothing settles."""
    ids = [post(rig, [ME], f"m{i}") for i in range(2)]
    assert wait_until(lambda: len(link.pending()) == 2)
    real = link.open_digest
    inside, go_on = threading.Event(), threading.Event()

    def slow(asked):
        answer = real(asked)                         # the daemon has opened the digest
        inside.set()
        go_on.wait(timeout=5)                        # and the call is cancelled now
        return answer

    link.open_digest = slow

    async def go():
        ctx = Ctx()
        task = asyncio.ensure_future(tools.check_messages({}, ctx))
        assert await asyncio.to_thread(inside.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        go_on.set()
        await asyncio.to_thread(wait_until, lambda: len(link.pending()) == 2)
        return ctx

    try:
        ctx = run(go())
    finally:
        link.open_digest = real
    assert ctx.after == []
    assert sorted(p["message_id"] for p in link.pending()) == sorted(ids)
    got, _ = call(tools, "check_messages")
    assert [m["id"] for m in got.structured["messages"]] == ids
    assert wait_until(lambda: not unread(rig))


def test_two_overlapping_checks_never_return_the_same_mail(rig, tools, link):
    """Two clients of one identity (the desktop and the phone), or parallel tool calls."""
    ids = [post(rig, [ME], f"m{i}") for i in range(2)]
    assert wait_until(lambda: len(link.pending()) == 2)
    real = link.open_digest
    inside, go_on, opens = threading.Event(), threading.Event(), []

    def slow(asked):
        opens.append(list(asked))
        inside.set()
        go_on.wait(timeout=5)                        # the second call starts meanwhile
        return real(asked)

    link.open_digest = slow

    async def go():
        c1, c2 = Ctx(), Ctx()
        first = asyncio.ensure_future(tools.check_messages({}, c1))
        assert await asyncio.to_thread(inside.wait, 5)
        second = asyncio.ensure_future(tools.check_messages({}, c2))
        await asyncio.sleep(0.3)                     # its count read, then the lock
        go_on.set()
        r1, r2 = await asyncio.gather(first, second)
        await c1.answered(True)
        await c2.answered(True)
        return r1, r2

    try:
        r1, r2 = run(go())
    finally:
        link.open_digest = real
    assert [m["id"] for m in r1.structured["messages"]] == ids
    assert r2.structured["messages"] == [] and opens == [ids]
    assert wait_until(lambda: not unread(rig))


def test_more_waiting_comes_from_the_daemons_count_past_its_window(rig, tools, link):
    for i in range(70):
        post(rig, [ME], f"m{i}")
    assert wait_until(lambda: len(link.pending()) == 64)
    assert wait_until(lambda: len(rig.daemon.bindings[ME].delivered) == 70)
    got, _ = call(tools, "check_messages", {"limit": 50})
    assert len(got.structured["messages"]) == 50
    assert got.structured["more_waiting"] == 20 and got.text.endswith("20 more waiting.")


def test_bodies_are_scrubbed_and_the_description_says_whose_text_it_is(rig, tools, link):
    post(rig, [ME], "hello\x1b[31m‮world")
    assert wait_until(lambda: link.pending())
    got, _ = call(tools, "check_messages")
    assert got.structured["messages"][0]["text"] == "hello[31mworld"
    for name in tools_mod.NAMES:
        assert tools_mod.UNTRUSTED in tools_mod.DESCRIPTIONS[name]


# --------------------------------------------------------------------------- send_and_wait

def wait_call(tools, args, during=None):
    async def go():
        ctx = Ctx()
        task = asyncio.ensure_future(tools.send_and_wait(args, ctx))
        if during is not None:
            await asyncio.to_thread(during)
        got = await task
        await ctx.answered(True)
        return got
    return run(go())


def sent_id(rig):
    return wait_until(lambda: next((str(m.id) for m in reversed(
        rig.record.history(rig.names_channel)) if m.author_id == ME), None))


def spy_replies_to(rig, monkeypatch):
    """What the daemon's `replies_to` read takes from the record, call by call: the ids it
    fetches, and `history` for any channel read. Only reads made inside that query
    count, so the daemon's own routing reads are not mistaken for it."""
    inside, fetched, answers = threading.local(), [], []
    real_query = rig.daemon._replies_to_query
    real_get, real_history = rig.record.get_message, rig.record.history

    def query(frame):
        inside.on = True
        try:
            got = real_query(frame)
            answers.append(got)
            return got
        finally:
            inside.on = False

    def get_message(mid):
        if getattr(inside, "on", False):
            fetched.append(str(mid))
        return real_get(mid)

    def history(*a, **kw):
        if getattr(inside, "on", False):
            fetched.append("history")
        return real_history(*a, **kw)

    monkeypatch.setattr(rig.daemon, "_replies_to_query", query)
    monkeypatch.setattr(rig.record, "get_message", get_message)
    monkeypatch.setattr(rig.record, "history", history)
    return fetched, answers


def ask_replies_to(params):
    client = DaemonClient(daemon_socket()).connect()
    try:
        assert "connector" in client.capabilities
        return client.call("replies_to", params)
    finally:
        client.close()


def test_the_replies_to_read_returns_only_replies_addressed_to_the_identity(rig, monkeypatch):
    planner(rig)
    sent = post(rig, ["planner@proj-a"], "ok?", author=ME)
    to_operator = post(rig, ["operator"], "for the human", reply_to=sent)
    other_author = post(rig, [ME], "not the one asked", author="main@proj-b",
                        reply_to=sent)
    unrelated = post(rig, [ME], "no re:")
    to_both = post(rig, ["operator", ME], "to both", reply_to=sent)
    to_me = post(rig, [ME], "yours", reply_to=sent)
    assert wait_until(lambda: to_me in rig.daemon.sent_by_id)
    fetched, _ = spy_replies_to(rig, monkeypatch)

    got = ask_replies_to({"identity": ME, "reply_to": sent, "author": "planner@proj-a"})
    assert [m["message_id"] for m in got["messages"]] == [to_both, to_me]
    assert got["messages"][1]["text"] == "yours"
    assert got["messages"][1]["author"]["name"] == "planner@proj-a"
    assert got["messages"][1]["reply_to"] == sent
    # Only the matching ids were read from the record; mail to the operator alone, the
    # other author's and the unrelated message never were, and no channel was read.
    assert fetched == [to_both, to_me]
    assert not {to_operator, other_author, unrelated, "history"} & set(fetched)
    # Nothing at or below the asked id, and nothing for a name with no reply.
    assert ask_replies_to({"identity": ME, "reply_to": to_me,
                      "author": "planner@proj-a"})["messages"] == []
    assert ask_replies_to({"identity": "lead@proj-a", "reply_to": sent,
                      "author": "planner@proj-a"})["messages"] == []


def test_the_replies_to_read_refuses_a_malformed_ask(rig):
    for params in ({"identity": ME, "reply_to": "x", "author": "planner@proj-a"},
                   {"identity": "", "reply_to": "1", "author": "planner@proj-a"},
                   {"identity": ME, "reply_to": "1"}):
        with pytest.raises(DaemonError):
            ask_replies_to(params)


def test_the_replies_to_read_answers_at_most_its_limit(rig):
    from moxi_comms.daemon import REPLY_QUERY_LIMIT

    planner(rig)
    sent = post(rig, ["planner@proj-a"], "ok?", author=ME)
    ids = [post(rig, [ME], f"r{i}", reply_to=sent) for i in range(REPLY_QUERY_LIMIT + 3)]
    assert wait_until(lambda: ids[-1] in rig.daemon.sent_by_id)
    got = ask_replies_to({"identity": ME, "reply_to": sent, "author": "planner@proj-a"})
    assert [m["message_id"] for m in got["messages"]] == ids[:REPLY_QUERY_LIMIT]


def test_send_and_wait_gets_a_reply_during_the_wait(rig, tools):
    shim = planner(rig)

    def reply():
        mid = sent_id(rig)
        assert wait_until(lambda: any(p["message_id"] == mid for p in shim.queue))
        post(rig, [ME], "yes", reply_to=mid)

    got = wait_call(tools, {"to": "planner@proj-a", "text": "ok?", "wait_seconds": 5},
                    reply)
    assert got.structured["outcome"] == "replied", got.text
    assert got.structured["reply"]["text"] == "yes"
    assert got.structured["other_new"] == 0
    assert got.text.startswith("Reply from planner@proj-a:")


def test_send_and_wait_finds_a_reply_that_came_before_the_wait(rig, tools, link):
    planner(rig)
    real = tools._send

    async def send_then_reply(*a, **kw):
        result = await real(*a, **kw)
        post(rig, [ME], "early", reply_to=result.message_id)
        await asyncio.to_thread(wait_until, lambda: link.pending())
        return result

    tools._send = send_then_reply
    got = wait_call(tools, {"to": "planner@proj-a", "text": "ok?", "wait_seconds": 1})
    assert got.structured["outcome"] == "replied"
    assert got.structured["reply"]["text"] == "early"


def test_send_and_wait_times_out(rig, tools):
    planner(rig)
    got = wait_call(tools, {"to": "planner@proj-a", "text": "ok?", "wait_seconds": 1})
    assert got.structured["outcome"] == "no-reply"
    assert got.text.startswith("No reply within 1 second. The message was sent, msg ")
    assert tools.waiting == 0


def test_send_and_wait_finds_the_reply_in_the_record_past_a_full_window(rig, tools, link,
                                                                       monkeypatch):
    shim = planner(rig)
    for i in range(64):
        post(rig, [ME], f"other {i}")
    assert wait_until(lambda: len(link.pending()) == 64)
    fetched, answers = spy_replies_to(rig, monkeypatch)
    to_operator = []

    def reply():
        mid = sent_id(rig)
        assert wait_until(lambda: any(p["message_id"] == mid for p in shim.queue))
        to_operator.append(post(rig, ["operator"], "for the human", reply_to=mid))
        post(rig, [ME], "found", reply_to=mid)

    got = wait_call(tools, {"to": "planner@proj-a", "text": "ok?", "wait_seconds": 5},
                    reply)
    assert got.structured["outcome"] == "replied" and got.structured["reply"]["text"] == "found"
    assert got.structured["other_new"] == 64
    assert "64 other new messages." in got.text
    reply_id = got.structured["reply"]["id"]
    # Found through the daemon's `replies_to` read, which never read the operator's mail.
    assert any(m["message_id"] == reply_id for a in answers for m in a["messages"])
    assert to_operator and to_operator[0] not in fetched and "history" not in fetched
    for _ in range(3):                              # drain the window; the reply follows
        got, _ = call(tools, "check_messages", {"limit": 50})
        hit = [m for m in got.structured["messages"] if m["id"] == reply_id]
        if hit:
            assert hit[0]["already_shown"] and "already shown in a wait" in got.text
            break
        wait_until(lambda: link.pending())
    else:
        pytest.fail("the reply never came through check_messages")


def test_send_and_wait_never_shows_a_reply_withdrawn_before_the_open(rig, tools, link):
    shim = planner(rig)
    real, dropped = link.open_digest, []

    def withdraw_first(asked):
        if not dropped:                              # the author withdraws it just now
            dropped.extend(asked)
            for mid in asked:
                rig.daemon.books.withdraw(mid)
        return real(asked)

    link.open_digest = withdraw_first

    def replies():
        mid = sent_id(rig)
        assert wait_until(lambda: any(p["message_id"] == mid for p in shim.queue))
        post(rig, [ME], "withdrawn", reply_to=mid)
        assert wait_until(lambda: dropped)
        post(rig, [ME], "kept", reply_to=mid)

    try:
        got = wait_call(tools, {"to": "planner@proj-a", "text": "ok?", "wait_seconds": 5},
                        replies)
    finally:
        link.open_digest = real
    assert got.structured["outcome"] == "replied", got.text
    assert got.structured["reply"]["text"] == "kept"
    assert got.structured["reply"]["id"] not in dropped


def test_send_and_wait_to_a_recipient_that_cant_read_now_answers_at_once(rig, tools):
    got = wait_call(tools, {"to": "main@proj-b", "text": "ok?", "wait_seconds": 5})
    assert got.structured["outcome"] == "not-delivered"
    assert got.text.endswith(tools_mod.LATER)


def test_send_and_wait_to_a_held_recipient_answers_at_once(rig, tools):
    sid = "cold-session"
    write_transcript(sid, age=7200)
    rig.shim(name="lead", scope="proj-a", project="proj-a", session_id=sid)
    got = wait_call(tools, {"to": "lead@proj-a", "text": "ok?", "wait_seconds": 5})
    assert got.structured["outcome"] == "not-delivered"
    assert "lead@proj-a: held for its next turn" in got.text
    assert got.text.endswith(tools_mod.LATER)


def test_other_new_messages_never_count_the_reply_found_in_the_pushes(rig, tools, link):
    shim = planner(rig)
    post(rig, [ME], "one")
    post(rig, [ME], "two")
    assert wait_until(lambda: len(link.pending()) == 2)

    def reply():
        mid = sent_id(rig)
        assert wait_until(lambda: any(p["message_id"] == mid for p in shim.queue))
        post(rig, [ME], "yes", reply_to=mid)

    got = wait_call(tools, {"to": "planner@proj-a", "text": "ok?", "wait_seconds": 5},
                    reply)
    assert got.structured["outcome"] == "replied", got.text
    assert got.structured["other_new"] == 2
    assert got.text.endswith("2 other new messages.")


def test_a_dropped_caller_ends_the_wait_and_the_late_reply_stays_unread(rig, tools, link):
    planner(rig)

    async def go():
        task = asyncio.ensure_future(tools.send_and_wait(
            {"to": "planner@proj-a", "text": "ok?", "wait_seconds": 5}, Ctx()))
        mid = await asyncio.to_thread(sent_id, rig)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return mid

    mid = run(go())
    assert tools.waiting == 0
    late = post(rig, [ME], "late", reply_to=mid)
    assert wait_until(lambda: link.pending())
    got, _ = call(tools, "check_messages")
    assert [m["id"] for m in got.structured["messages"]] == [late]


def test_a_fifth_wait_is_refused(rig, tools):
    tools.waiting = tools_mod.WAITERS
    got = wait_call(tools, {"to": "planner@proj-a", "text": "x"})
    assert got.is_error and got.refusal == "busy"
    assert not [m for m in rig.record.history(rig.names_channel) if m.author_id == ME]


def test_a_wait_over_wait_max_waits_wait_max_and_the_schema_says_so(rig, tools):
    planner(rig)
    tools.wait_max = 1
    got = wait_call(tools, {"to": "planner@proj-a", "text": "x", "wait_seconds": 6})
    assert got.structured["outcome"] == "no-reply"
    assert got.text.startswith("No reply within 1 second.")
    schema = tools_mod.inputs()["send_and_wait"]["properties"]["wait_seconds"]
    assert schema["maximum"] == CONNECTOR_WAIT_CEILING
    assert f"longest wait is {ConnectorConfig.wait_max} seconds" in schema["description"]


def test_a_wait_under_one_second_is_refused(rig, tools):
    got = wait_call(tools, {"to": "planner@proj-a", "text": "x", "wait_seconds": 0})
    assert got.is_error and got.refusal == "wait-shape"
    assert not [m for m in rig.record.history(rig.names_channel) if m.author_id == ME]


# --------------------------------------------------------------------------- only its mail

def test_mail_to_the_operator_alone_is_never_returned(rig, tools, link, monkeypatch):
    shim = planner(rig)
    fetched, answers = spy_replies_to(rig, monkeypatch)
    post(rig, ["operator"], "for the human")

    def reply_to_operator():
        mid = sent_id(rig)
        assert wait_until(lambda: any(p["message_id"] == mid for p in shim.queue))
        post(rig, ["operator"], "not yours", reply_to=mid)

    got = wait_call(tools, {"to": "planner@proj-a", "text": "ok?", "wait_seconds": 2},
                    reply_to_operator)
    assert got.structured["outcome"] == "no-reply"
    assert answers and all(a["messages"] == [] for a in answers)
    assert fetched == []                            # the record look read nothing
    got, _ = call(tools, "check_messages")
    assert got.text == "No new messages."
    assert "operator" not in rig.daemon.bindings


def test_a_message_to_both_names_comes_once(rig, tools, link):
    mid = post(rig, ["operator", ME], "both")
    assert wait_until(lambda: link.pending())
    got, _ = call(tools, "check_messages")
    assert [m["id"] for m in got.structured["messages"]] == [mid]
    got, _ = call(tools, "check_messages")
    assert got.structured["messages"] == []


# --------------------------------------------------------------------------- agents, seam

def test_agents_lists_who_can_be_messaged_without_send_only_names(rig, tools):
    planner(rig)
    got, _ = call(tools, "agents")
    names = {r["name"] for r in got.structured["agents"]}
    assert {"planner@proj-a", ME, "main@proj-b"} <= names
    assert "planner@proj-a" in got.text


def test_the_annotations_are_truthful():
    assert tools_mod.ANNOTATIONS["agents"]["readOnlyHint"] is True
    for name in ("check_messages", "send", "send_and_wait"):
        assert tools_mod.ANNOTATIONS[name]["readOnlyHint"] is False
        assert tools_mod.ANNOTATIONS[name]["destructiveHint"] is False


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [f"{'.' * node.level}{node.module or ''}"]
            yield names + [f"{names[0]}.{a.name}" for a in node.names]


def test_nothing_else_in_comms_imports_the_provider():
    for path in SRC.rglob("*.py"):
        if path.parent.name == "connector" and path.parent.parent == SRC:
            continue
        for names in _imports(path):
            assert not any("connector" in n.split(".")[-2:] and "moxi_connector" not in n
                           for n in names if "connector" in n), (path, names)


def test_comms_reaches_the_host_only_through_moxi_modules_load():
    """No import statement in comms names the host's package, and the provider imports
    nothing by a name it builds: the host's interface comes from `moxi.modules.load`."""
    for path in SRC.rglob("*.py"):
        for names in _imports(path):
            assert not any(n.startswith("moxi_connector") for n in names), (path, names)
    for path in (SRC / "connector").glob("*.py"):
        text = path.read_text()
        assert "importlib" not in text and "__import__" not in text, path
    from moxi_comms import connector
    assert connector._interface() is provider_api


def test_a_missing_host_is_an_import_error_with_the_reason(monkeypatch):
    from moxi_comms import connector
    absent = SimpleNamespace(ok=False, api=None, state="absent",
                             reason="moxi-connector is not installed")
    monkeypatch.setattr(connector.modules, "load", lambda name, interface=None: absent)
    with pytest.raises(ImportError, match="moxi-connector is not installed"):
        connector._interface()


def test_the_host_finds_comms_provider_and_its_tools_answer_through_it(rig, link):
    """The provider is the entry the host loads from its group, and its tools answer
    through the interface's own types. The host's schema check and gate list are the
    host's to test, against this provider, where both are installed."""
    from importlib.metadata import entry_points

    from moxi_comms.connector import PROVIDER, build

    [entry] = [e for e in entry_points(group=provider_api.GROUP) if e.name == "comms"]
    assert entry.load() is PROVIDER
    assert [t.name for t in PROVIDER.tools] == list(tools_mod.NAMES)
    assert isinstance(PROVIDER.gate, provider_api.Gate)

    comms, provider = build(provider_api, make_link=lambda identity, operator: link,
                            record=rig.record)
    assert all(isinstance(t, provider_api.Tool) for t in provider.tools)
    by_name = {t.name: t for t in provider.tools}
    assert {n: t.annotations["readOnlyHint"] for n, t in by_name.items()} == {
        "agents": True, "send": False, "check_messages": False, "send_and_wait": False}

    async def go():
        await provider.start({"MOXI_COMMS_HOME": str(Path(comms_db()).parents[1])})
        ctx = provider_api.Ctx(grant_id="g1", grant_label="phone", request_id="r1")
        return await by_name["agents"].call({}, ctx)

    got = run(go())
    assert isinstance(got, provider_api.Result) and not got.is_error
    assert ME in {r["name"] for r in got.structured["agents"]}
    rows = provider.status()
    assert all(isinstance(r, provider_api.StatusRow) for r in rows)
