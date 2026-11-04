"""comms' provider for the connector, against a real daemon on the rig: the four tools,
only the identity's mail, the identity bound as `connector`, and the provider through
the connector's own seam. Nothing here starts the connector's listener."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from moxi_comms import directory, kinds
from moxi_comms.client import DaemonClient
from moxi_comms.connector import tools as tools_mod
from moxi_comms.connector.link import BOUND, HELD, Link
from moxi_comms.paths import comms_db, daemon_socket

from conftest import wait_until, write_transcript

provider_api = pytest.importorskip("moxi_connector.provider")
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
    assert link.status() == [("comms identity", "ok", f"bound as {ME}, adapter connector")]
    assert rig.daemon.kinds.get("connector").cache == kinds.CACHE_PERSON


def test_a_held_name_makes_the_tools_refuse_and_the_status_name_the_holder(rig):
    other = DaemonClient(daemon_socket()).connect()
    other.bind(name=ME, global_=True, adapter="claude-shim")
    link = Link(ME, backoff=fast, held_retry=0.1)
    link.start()
    try:
        assert wait_until(lambda: link.state == HELD)
        [row] = link.status()
        assert row[:2] == ("comms identity", "fail") and "claude-shim" in row[2]
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


def test_send_and_wait_finds_the_reply_in_the_record_past_a_full_window(rig, tools, link):
    shim = planner(rig)
    for i in range(64):
        post(rig, [ME], f"other {i}")
    assert wait_until(lambda: len(link.pending()) == 64)

    def reply():
        mid = sent_id(rig)
        assert wait_until(lambda: any(p["message_id"] == mid for p in shim.queue))
        post(rig, [ME], "found", reply_to=mid)

    got = wait_call(tools, {"to": "planner@proj-a", "text": "ok?", "wait_seconds": 5},
                    reply)
    assert got.structured["outcome"] == "replied" and got.structured["reply"]["text"] == "found"
    assert got.structured["other_new"] == 64
    assert "64 other new messages." in got.text
    reply_id = got.structured["reply"]["id"]
    for _ in range(3):                              # drain the window; the reply follows
        got, _ = call(tools, "check_messages", {"limit": 50})
        hit = [m for m in got.structured["messages"] if m["id"] == reply_id]
        if hit:
            assert hit[0]["already_shown"] and "already shown in a wait" in got.text
            break
        wait_until(lambda: link.pending())
    else:
        pytest.fail("the reply never came through check_messages")


def test_send_and_wait_to_a_recipient_that_cant_read_now_answers_at_once(rig, tools):
    got = wait_call(tools, {"to": "main@proj-b", "text": "ok?", "wait_seconds": 5})
    assert got.structured["outcome"] == "not-delivered"
    assert got.text.endswith(tools_mod.LATER)


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


def test_a_wait_over_wait_max_is_refused(rig, tools):
    got = wait_call(tools, {"to": "planner@proj-a", "text": "x", "wait_seconds": 6})
    assert got.is_error and got.refusal == "wait-shape"


# --------------------------------------------------------------------------- only its mail

def test_mail_to_the_operator_alone_is_never_returned(rig, tools, link):
    shim = planner(rig)
    post(rig, ["operator"], "for the human")

    def reply_to_operator():
        mid = sent_id(rig)
        assert wait_until(lambda: any(p["message_id"] == mid for p in shim.queue))
        post(rig, ["operator"], "not yours", reply_to=mid)

    got = wait_call(tools, {"to": "planner@proj-a", "text": "ok?", "wait_seconds": 2},
                    reply_to_operator)
    assert got.structured["outcome"] == "no-reply"
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


def test_nothing_else_in_comms_imports_the_provider():
    for path in SRC.rglob("*.py"):
        if path.parent.name == "connector" and path.parent.parent == SRC:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [f"{'.' * node.level}{node.module or ''}"]
                names += [f"{names[0]}.{a.name}" for a in node.names]
            assert not any("connector" in n.split(".")[-2:] and "moxi_connector" not in n
                           for n in names if "connector" in n), (path, names)
            assert not any(n.startswith("moxi_connector") for n in names), (path, names)


def test_the_connector_loads_comms_provider_and_calls_a_tool_through_its_seam(rig, link):
    providers = pytest.importorskip("moxi_connector.providers")
    app = pytest.importorskip("moxi_connector.app")
    from moxi_comms.connector import PROVIDER, build

    [entry] = [e for e in providers.load() if e.module == "comms"]
    assert entry.state == "loaded" and entry.provider is PROVIDER
    assert [t.name for t in PROVIDER.tools] == list(tools_mod.NAMES)
    assert PROVIDER.gate is not None

    comms, provider = build(provider_api, make_link=lambda identity, operator: link,
                            record=rig.record)
    registry = providers.Registry.of({"comms": provider})

    async def go():
        await registry.start({"MOXI_COMMS_HOME": str(Path(comms_db()).parents[1])})
        handlers = app.Tools(app.Host(registry))
        listed = (await handlers.list_tools(None, None)).tools
        got = await handlers.call_tool(None, SimpleNamespace(name="agents", arguments={}))
        bad = await handlers.call_tool(None, SimpleNamespace(
            name="check_messages", arguments={"limit": 99}))
        return listed, got, bad

    import os
    listed, got, bad = run(go())
    assert {t.name: t.annotations.read_only_hint for t in listed} == {
        "agents": True, "send": False, "check_messages": False, "send_and_wait": False}
    assert not got.is_error and ME in {r["name"] for r in got.structured_content["agents"]}
    assert bad.is_error                         # the connector's schema check refuses it
    assert [g[0] for g in registry.gates()] == ["comms"]
    assert os.environ.get("MOXI_COMMS_HOME")
