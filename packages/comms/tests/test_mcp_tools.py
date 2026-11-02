"""comms' six tools (`mcp.tools`): their schemas and descriptions, the size of the whole
list, their results and refusals, the cures in the tools' words, expiry and interrupt,
delete, and one call of each over HTTP on a test daemon serving them on an ephemeral
loopback port."""

from __future__ import annotations

import asyncio
import inspect
import json
import re

import pytest
from conftest import projects_root, wait_until, warm_session

from moxi_comms import claimfile, config, sending
from moxi_comms.client import DaemonClient
from moxi_comms.daemon import Daemon
from moxi_comms.mcp import inprocess, tokens
from moxi_comms.mcp import tools as mcp_tools
from moxi_comms.membership import ProcView, Root
from moxi_comms.paths import daemon_socket

PLANNER = "planner@proj-a"
CLAIM = "tools-planner-1"


# --------------------------------------------------------------------------- the rig

@pytest.fixture
def planner(rig, bind_answered):
    """planner@proj-a bound under claim `CLAIM`, its tool token registered."""
    client, answer = bind_answered(
        name="planner", scope="proj-a", adapter="claude-shim", session_id=warm_session(),
        projects_root=str(projects_root()), turned=True, claim=CLAIM)
    token = tokens.mint()
    client.mcp_token(tokens.digest(token))
    return token


@pytest.fixture
def reviewer(rig):
    return rig.shim(name="reviewer")


@pytest.fixture
def tools(rig):
    return mcp_tools.Tools(rig.daemon)


def call(tools, name, **params):
    """One tool's body, as the call runs it on the pool, as planner."""
    return getattr(tools, name)(PLANNER, CLAIM, params)


def send(tools, to, text, *, posture="none", **kw):
    params = {"to": to, "text": text, "posture": posture, "expires": None,
              "interrupt": False, "reply_to": None, "new": False, "new_name": False}
    params.update(kw)
    return call(tools, "send", **params)


def text_of(result) -> str:
    assert len(result.content) == 1
    return result.content[0].text


def listed() -> list[dict]:
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("comms")
    mcp_tools.Tools(daemon=None).add(server)
    return [t.model_dump(mode="json", by_alias=True, exclude_none=True)
            for t in asyncio.run(server.list_tools())]


# ======================================================================= the schemas

def test_six_tools_by_their_names():
    assert [t["name"] for t in listed()] == list(mcp_tools.NAMES_OF_TOOLS) == [
        "send", "reply", "agents", "whoami", "history", "delete"]


def test_the_parameters_are_the_designed_ones():
    got = {t["name"]: t["inputSchema"] for t in listed()}
    props = {name: set(schema["properties"]) for name, schema in got.items()}
    assert props == {
        "send": {"to", "text", "posture", "expires", "interrupt", "reply_to", "new",
                 "new_name"},
        "reply": {"message_id", "text", "posture", "expires", "interrupt"},
        "agents": set(), "whoami": set(), "history": {"identity", "limit"},
        "delete": {"message_id"}}
    assert got["send"]["required"] == ["to", "text", "posture"]
    assert got["send"]["properties"]["posture"]["enum"] == ["none", "optional", "once",
                                                            "open"]
    assert got["reply"]["required"] == ["message_id", "text"]
    assert got["reply"]["properties"]["posture"] == {
        "type": "string", "enum": ["none", "optional", "once", "open"],
        "description": "unset: a reply to a once message asks nothing back; in an open "
                       "exchange it stays open"}
    assert got["delete"]["required"] == ["message_id"]
    limit = got["history"]["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 200, 20)
    for name in ("interrupt", "new", "new_name"):
        assert got["send"]["properties"][name] == {"type": "boolean", "default": False}


def test_each_signature_takes_exactly_the_published_parameters():
    """The functions validate what the schemas publish: no parameter on one side only."""
    functions = mcp_tools.Tools(daemon=None)._functions()
    for name, fn in functions.items():
        assert set(inspect.signature(fn).parameters) == set(
            mcp_tools.INPUTS[name]["properties"]), name


def test_no_tool_names_a_sender():
    def walk(schema):
        if isinstance(schema, dict):
            for key, value in schema.items():
                if key == "properties":
                    yield from value
                yield from walk(value)
        elif isinstance(schema, list):
            for value in schema:
                yield from walk(value)

    for tool in listed():
        names = set(walk(tool["inputSchema"]))
        assert not names & {"as", "from", "sender", "user"}, tool["name"]
        if tool["name"] in ("send", "reply", "delete"):
            assert "identity" not in names


def test_the_whole_list_is_at_most_6000_characters():
    size = len(json.dumps(listed()))
    assert size <= 6000, size


def test_descriptions_are_the_designed_texts_in_plain_words():
    tools = {t["name"]: t["description"] for t in listed()}
    assert tools == mcp_tools.descriptions(config.load().delivery.expiry)
    assert tools["send"].startswith("Send a message to agents or the operator by name.")
    assert ("expires after 24 hours unless you set expires"
            in mcp_tools.descriptions()["send"])
    assert "Set new_name to message a name that hasn't started yet." in tools["send"]
    assert tools["delete"].endswith("the result says who had it.")
    # An unset posture on a reply to a once message goes out asking nothing back: the
    # text must not invite setting once to keep the exchange's posture.
    assert ("A reply to a once message asks nothing back unless you set a posture; in "
            "an open exchange it stays open." in tools["reply"])
    assert "keeps the exchange's posture" not in tools["reply"]
    for name, text in tools.items():
        for word in ("gateway", "lane", "shim", "claim", "custody", "binding"):
            assert word not in text.lower(), (name, word)
    glossed = [name for name, text in tools.items()
               if "An identity is a name only one session can hold" in text]
    assert glossed == ["whoami"]


def test_the_expiry_in_the_description_follows_the_config():
    assert "expires after 6 hours unless" in mcp_tools.descriptions("6h")["send"]
    assert "expires after 1 day unless" in mcp_tools.descriptions("1d")["send"]
    assert "waits until it is delivered" in mcp_tools.descriptions("never")["send"]


def test_every_tool_declares_an_output_schema():
    for tool in listed():
        assert tool["outputSchema"] == mcp_tools.OUTPUTS[tool["name"]]
        assert tool["outputSchema"]["type"] == "object"


# ======================================================================= send, reply

def test_a_send_answers_its_fields_and_the_verbs_lines(rig, planner, reviewer, tools):
    got = send(tools, ["reviewer"], "hello", expires="6h", interrupt=True)
    assert not got.is_error, text_of(got)
    s = got.structured_content
    assert s["outcome"] == "sent" and s["message_id"].isdigit()
    assert s["recipients"] == [{"name": "reviewer", "delivery": "live"}]
    assert s["interrupt"] is True
    posted = rig.record.get_message(s["message_id"])
    assert "[interrupt]" in posted.content.splitlines()[0]
    assert abs(s["expires_at"] - (rig.clock() + 6 * 3600)) <= 2
    assert text_of(got).startswith(f"sent {s['message_id']} to reviewer")
    wait_until(lambda: any(s["message_id"] in d for d in reviewer.digests)
               or reviewer.digests)


def test_a_send_with_no_expires_takes_the_configs_and_never_has_none(rig, planner,
                                                                     reviewer, tools):
    default = send(tools, ["reviewer"], "a").structured_content
    assert config.load().delivery.expiry == "never"     # the suite's config
    assert default["expires_at"] is None and default["interrupt"] is False
    days = send(tools, ["reviewer"], "a", expires="3d").structured_content
    assert abs(days["expires_at"] - (rig.clock() + 3 * 86400)) <= 2
    never = send(tools, "reviewer", "b", expires="never").structured_content
    assert never["expires_at"] is None


def test_a_bad_expires_is_refused_in_the_tools_words(rig, planner, reviewer, tools):
    got = send(tools, ["reviewer"], "x", expires="soon")
    assert got.is_error
    assert got.structured_content["refusal"] == "expires-shape"
    assert text_of(got).startswith("expires: 'soon' is not a duration")


def test_interrupt_to_the_operator_is_noted_in_the_tools_words(rig, planner, tools):
    got = send(tools, ["operator"], "hi", interrupt=True)
    assert not got.is_error, text_of(got)
    assert sending.INTERRUPT_IGNORED_OPERATOR_TOOL in got.structured_content["notes"]
    assert sending.INTERRUPT_IGNORED_OPERATOR not in got.structured_content["notes"]
    assert got.structured_content["interrupt"] is False


def test_a_refusal_is_an_error_result_with_its_code(rig, planner, reviewer, tools):
    got = send(tools, ["reviewer"], "x" * 20000)
    assert got.is_error and got.structured_content["refusal"] == "too-long"
    assert "Nothing was sent." in text_of(got)


def test_never_bound_names_new_name_and_new_name_goes_through(rig, planner, tools):
    got = send(tools, ["nobody-yet"], "hello")
    assert got.is_error and got.structured_content["refusal"] == "never-bound"
    assert "new_name: true" in text_of(got) and "the agents tool" in text_of(got)
    assert "--" not in text_of(got)
    again = send(tools, ["nobody-yet"], "hello", new_name=True)
    assert again.structured_content["outcome"] == "sent", text_of(again)
    assert again.structured_content["recipients"][0]["delivery"] == "unbound"


def test_with_no_live_binding_the_tool_says_reconnecting(rig, planner, reviewer, tools):
    got = mcp_tools.Tools(rig.daemon).send("planner@proj-a", "no-such-claim", {
        "to": ["reviewer"], "text": "x", "posture": "none"})
    assert got.is_error and got.structured_content["refusal"] == "reconnecting"
    assert text_of(got) == mcp_tools.RECONNECTING
    assert "Nothing was sent." in text_of(got)


@pytest.mark.parametrize("backend", ["local"], indirect=True)
def test_an_open_ask_names_the_reply_tool_and_new_goes_through(rig, planner, reviewer,
                                                               tools):
    code, out = rig.send(PLANNER, "which way?", sender=dict(name="reviewer"),
                         posture="once")
    assert code == 0, out
    ask = re.search(r"sent (\d+)", out).group(1)
    got = send(tools, ["reviewer"], "something else", interrupt=True)
    assert got.is_error and got.structured_content["refusal"] == "single-reply"
    text = text_of(got)
    assert f"the reply tool with message_id: {ask}, interrupt: true, posture: none" in text
    assert "set new: true" in text and "--" not in text
    passed = send(tools, ["reviewer"], "something else", new=True)
    assert passed.structured_content["outcome"] == "sent", text_of(passed)
    answered = call(tools, "reply", message_id=ask, text="that way", posture=None,
                    expires=None, interrupt=False)
    assert answered.structured_content["outcome"] == "sent", text_of(answered)
    assert answered.structured_content["recipients"][0]["name"].startswith("reviewer")


@pytest.mark.parametrize("backend", ["local"], indirect=True)
def test_a_closed_thread_and_no_reply_name_the_send_tool(rig, planner, reviewer, tools):
    code, out = rig.send(PLANNER, "which way?", "--cap", "1", sender=dict(name="reviewer"),
                         posture="open")
    root = re.search(r"sent (\d+)", out).group(1)
    first = call(tools, "reply", message_id=root, text="one", posture=None, expires=None,
                 interrupt=False)
    assert first.structured_content["outcome"] == "sent", text_of(first)
    closed = call(tools, "reply", message_id=root, text="two", posture=None,
                  expires=None, interrupt=False)
    assert closed.is_error and closed.structured_content["refusal"] == "thread-closed"
    assert "the send tool to" in text_of(closed) and "--" not in text_of(closed)
    code, out = rig.send(PLANNER, "fyi", sender=dict(name="reviewer"), posture="none")
    fyi = re.search(r"sent (\d+)", out).group(1)
    no_reply = call(tools, "reply", message_id=fyi, text="ok", posture=None,
                    expires=None, interrupt=False)
    assert no_reply.is_error and no_reply.structured_content["refusal"] == "no-reply"
    assert "posture none, optional, once or open" in text_of(no_reply)
    assert "--" not in text_of(no_reply)


def test_an_open_send_says_the_cap_is_the_command_lines(rig, planner, reviewer, tools):
    got = send(tools, ["reviewer"], "let's talk", posture="open")
    assert not got.is_error, text_of(got)
    assert "Its reply cap is changed only from the command line" in text_of(got)
    assert "--" not in text_of(got)


def test_the_reply_to_shape_and_new_with_reply_to_in_the_tools_words(rig, planner,
                                                                     reviewer, tools):
    shape = send(tools, ["reviewer"], "x", reply_to="abc")
    assert text_of(shape).startswith("reply_to 'abc' is not a message id")
    both = send(tools, ["reviewer"], "x", reply_to="5", new=True)
    assert both.structured_content["refusal"] == "new-with-reply-to"
    assert "Drop new" in text_of(both) and "--" not in text_of(both)


# ======================================================================= cures

def tool_texts() -> dict:
    return {name: getattr(sending, name) for name in dir(sending)
            if name.endswith("_TOOL") and isinstance(getattr(sending, name), str)}


def test_no_tool_text_names_a_command_line_flag():
    texts = tool_texts()
    assert {"NEVER_BOUND_TOOL", "POSTURE_REQUIRED_TOOL", "NO_REPLY_REFUSED_TOOL",
            "ONCE_NOT_OPEN_TOOL", "CLOSED_BY_CAP_TOOL", "CLOSED_BY_IDLE_TOOL",
            "CLOSED_BY_NONE_TOOL", "OPEN_SENT_TOOL", "OPEN_SENT_NO_CAP_TOOL",
            "NO_ANSWER_TOOL", "DELETE_NO_ANSWER_TOOL", "REPLY_OWN_ALONE_TOOL",
            "REPLY_OWN_UNREADABLE_TOOL", "INTERRUPT_IGNORED_OPERATOR_TOOL"} <= set(texts)
    for name, text in texts.items():
        assert not re.search(r"--\w", text), name
        assert "moxi comms send" not in text and "moxi comms reply" not in text, name


def test_the_cure_flags_in_the_tools_words():
    from types import SimpleNamespace

    args = SimpleNamespace(held=True, standing=True, interrupt=True, expires="6h",
                           posture="once")
    assert sending._cure_flags(args, tool=True) == (
        ", interrupt: true, expires: 6h, posture: once")
    assert sending._cure_flags(args) == (
        " --held --interrupt --expires 6h --standing --posture once")


def test_a_context_takes_only_the_two_vocabularies():
    assert sending.Context(cures="tool").cures == "tool"
    assert sending.Context().cures == "cli"
    with pytest.raises(ValueError):
        sending.Context(cures="mcp")


def test_the_cli_keeps_its_own_words(rig, planner, reviewer):
    code, out = rig.send("nobody-yet", "x", sender=dict(name="reviewer"))
    assert "--new-name" in out and "new_name: true" not in out


# ======================================================================= the reads

def test_agents_lists_names_running_and_harness(rig, planner, reviewer, tools):
    from moxi_comms import reserved, secrets

    reserved.reconcile(declared={"launcher": ["console"]}, chain=secrets.from_config())
    got = call(tools, "agents")
    rows = {r["name"]: r for r in got.structured_content["agents"]}
    assert rows[PLANNER] == {"name": PLANNER, "running": True, "harness": "claude-shim",
                             "send_only": False}
    assert rows["reviewer"]["running"] is True
    assert rows["console"] == {"name": "console", "running": False, "harness": None,
                               "send_only": True}
    assert "send-only, reserved for launcher" in text_of(got)


def test_whoami_is_the_tokens_identity(rig, planner, tools):
    got = call(tools, "whoami")
    s = got.structured_content
    assert (s["identity"], s["name"], s["scope"], s["harness"]) == (
        PLANNER, "planner", "proj-a", "claude-shim")
    assert text_of(got).splitlines()[0] == f"{PLANNER} (bound)"


def test_history_shows_each_messages_state(rig, planner, reviewer, tools):
    sent = send(tools, ["reviewer"], "first").structured_content["message_id"]
    unbound = send(tools, ["later-one"], "waits", new_name=True)
    waits = unbound.structured_content["message_id"]
    gone = send(tools, ["later-two"], "withdrawn", new_name=True)
    withdrawn = gone.structured_content["message_id"]
    deleted = call(tools, "delete", message_id=withdrawn)
    assert not deleted.is_error, text_of(deleted)
    assert deleted.structured_content == {"message_id": withdrawn,
                                          "withdrawn": ["later-two"], "delivered": [],
                                          "expired": []}
    got = call(tools, "history", identity=None, limit=20)
    rows = {m["id"]: m for m in got.structured_content["messages"]}
    assert rows[sent]["from"] == PLANNER and rows[sent]["to"] == ["reviewer"]
    assert rows[sent]["attribution"] == "verified" and rows[sent]["text"] == "first"
    assert rows[waits]["state"] == "waiting"
    assert rows[withdrawn]["state"] == "withdrawn"
    assert "[withdrawn]" in text_of(got)


def test_history_lines_are_the_verbs(rig, planner, reviewer, tools):
    send(tools, ["reviewer"], "one line\nand another")
    got = call(tools, "history", identity="reviewer", limit=20)
    code, out = rig.cli(["history", "reviewer", "--limit", "20"])
    assert code == 0
    assert text_of(got).splitlines() == out.splitlines()


def test_history_refuses_a_limit_outside_its_bounds(rig, planner, tools):
    got = call(tools, "history", identity=None, limit=500)
    assert got.is_error and got.structured_content["refusal"] == "limit-shape"


def test_delete_of_a_delivered_message_says_who_had_it(rig, planner, reviewer, tools):
    mid = send(tools, ["reviewer"], "on its way").structured_content["message_id"]
    wait_until(lambda: reviewer.digests)
    got = call(tools, "delete", message_id=mid)
    s = got.structured_content
    assert s["message_id"] == mid
    if got.is_error:
        assert s["refusal"] == "already-delivered" and s["delivered"] == ["reviewer"]
    else:
        assert "reviewer" in s["withdrawn"] + s["delivered"]
    unknown = call(tools, "delete", message_id="99999")
    assert unknown.is_error and unknown.structured_content["refusal"] == "unknown-message"


def test_a_body_that_fails_answers_an_error_and_logs_no_value(rig, planner, caplog):
    def body(identity, claim, params):
        raise RuntimeError(f"secret {params['token']}")

    got = mcp_tools.Tools._guarded(body, PLANNER, CLAIM, {"token": "tok-value"})
    assert got.is_error and got.structured_content["refusal"] == "failed"
    assert "tok-value" not in text_of(got)


# ======================================================================= over HTTP

class View(ProcView):
    """A process table the test writes: the claim's root is alive."""

    def __init__(self, table):
        super().__init__()
        self.table = table

    def stat(self, pid):
        return self.table.get(int(pid))

    def gone(self, pid):
        return int(pid) not in self.table


@pytest.fixture
def serving(rig):
    """The rig's daemon started again serving comms' own tools on a free port, with
    planner bound under a live claim and its token registered."""
    root = 64000
    key = f"{root}-s{root}"
    claimfile.write(key, {"key": key, "keying": "pid", "state": "bound",
                          "roots": [Root(root, f"s{root}").as_dict()]})
    rig.daemon.stop()
    daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False,
                    serve_tools=True, mcp_port=0)
    daemon.proc_view = View({root: (1, f"s{root}")})
    rig.daemon = daemon
    daemon.start()
    token = tokens.mint()
    client = DaemonClient(daemon_socket()).connect()
    client.bind(name="planner", scope="proj-a", adapter="codex", claim=key)
    assert client.mcp_token(tokens.digest(token)) == {"served": True}
    yield daemon, token
    client.close()


def test_each_tool_answers_over_http(rig, serving):
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    daemon, token = serving
    reviewer = rig.shim(name="reviewer")
    assert isinstance(daemon.tool_calls, inprocess.Calls)

    async def run():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                      timeout=20) as http:
            async with streamable_http_client(daemon.mcp_url, http_client=http) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    names = [t.name for t in (await session.list_tools()).tools]
                    out = {"list": names}
                    sent = await session.call_tool("send", {
                        "to": ["reviewer"], "text": "over http", "posture": "once"})
                    out["send"] = sent
                    mid = sent.structured_content["message_id"]
                    out["whoami"] = await session.call_tool("whoami", {})
                    out["agents"] = await session.call_tool("agents", {})
                    out["history"] = await session.call_tool("history", {"limit": 5})
                    out["reply"] = await session.call_tool("reply", {
                        "message_id": mid, "text": "and more"})
                    out["delete"] = await session.call_tool("delete", {"message_id": mid})
                    return out

    out = asyncio.run(asyncio.wait_for(run(), 30))
    assert out["list"] == list(mcp_tools.NAMES_OF_TOOLS)
    sent = out["send"]
    assert not sent.is_error and sent.structured_content["outcome"] == "sent"
    assert out["whoami"].structured_content["identity"] == PLANNER
    assert out["whoami"].structured_content["harness"] == "codex"
    assert PLANNER in {a["name"] for a in out["agents"].structured_content["agents"]}
    assert sent.structured_content["message_id"] in {
        m["id"] for m in out["history"].structured_content["messages"]}
    assert out["reply"].structured_content["outcome"] == "sent", out["reply"].content
    assert out["delete"].structured_content["message_id"] == sent.structured_content[
        "message_id"]
    author = rig.record.get_message(sent.structured_content["message_id"])
    assert author is not None
    reviewer.stop()
