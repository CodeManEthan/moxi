"""turns' two contracts: the module contract, as `moxi-dev contract` runs it, and the
socket protocol, `contracts/socket-protocol.md`.

The socket protocol's table is the data below: each value's fields, each notification's
params, each error code, each capability per harness, each method. The tests hold the
table, the values in `moxi_turns.results` and the document's own tables to one another,
and run the table's wire rows against an endpoint driven by the fake harness.

The module contract runs with the package's suite, so `moxi-dev check` and CI hold every
change to it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

from fake_harness import FakeHarness
from moxi import ipc
from moxi_dev.contract import check
from moxi_turns import results as R

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "contracts" / "socket-protocol.md").read_text(encoding="utf-8")


# -- the module contract ------------------------------------------------------------------

def test_the_repo_keeps_the_module_contract():
    assert [str(finding) for finding in check(ROOT)] == []


def test_every_public_name_loads_on_first_use():
    """The package loads its names lazily; each one in `__all__` resolves to
    the object its submodule defines (a host's namespace, named as its submodule, is
    the submodule), and an unknown name is an AttributeError."""
    import importlib

    import moxi_turns

    for name in moxi_turns.__all__:
        where = moxi_turns._WHERE[name]
        module = importlib.import_module(f"moxi_turns.{where}")
        want = module if where == name else getattr(module, name)
        assert getattr(moxi_turns, name) is want, name
    assert set(moxi_turns._WHERE) == set(moxi_turns.__all__)
    with pytest.raises(AttributeError):
        moxi_turns.nonesuch


# -- the socket protocol's table ----------------------------------------------------------

#: Each value's kind and its fields on the wire. A command's `refused` writes `reason`
#: alone; a delivery's writes all three.
VALUES = {
    "facts": ("session_id", "turn_number", "turn_open", "turn", "agents", "draft", "at",
              "stale", "ready"),
    "agents": ("entries", "complete", "at"),
    "started": ("delivery", "ref", "turn_id"),
    "refused": ("delivery", "ref", "reason"),
    "lost": ("delivery", "ref", "reason"),
    "ended": ("delivery", "ref", "turn_id", "outcome"),
    "ran": ("new_session_id", "shells"),
    "failed": ("reason",),
    "passed": (),
    "stop": ("turn_id", "session_id", "stop_hook_active", "task_types", "crons"),
}

#: The values of a delivery: each names its delivery and echoes its ref.
DELIVERY_VALUES = ("started", "refused", "lost", "ended")

#: The fields of the values inside other values.
NESTED = {
    R.OpenTurn: ("turn_id", "opening", "command", "started_at"),
    R.Agent: ("id", "kind", "state", "final", "description", "parent_id"),
    R.Shells: ("stopped", "left", "complete"),
    R.LeftShell: ("id", "why"),
    R.Ready: ("source", "session_id", "at"),
}

#: Each notification on a watch and its params.
NOTIFICATIONS = {
    "session.ready": ("source", "session_id"),
    "turn.started": ("turn_id", "session_id", "turn_number", "deliveries", "command"),
    "turn.steered": ("turn_id", "session_id", "delivery"),
    "turn.ended": ("turn_id", "session_id", "turn_number", "outcome", "deliveries",
                   "command"),
    "session.gone": ("reason",),
}

#: The error codes a caller can meet: JSON-RPC's and the hello's from `moxi.ipc`, then
#: turns' own.
ERRORS = {
    -32700: "parse error",
    -32600: "invalid request",
    -32601: "method not found",
    -32602: "invalid params",
    -32001: "hello refused",
    -32002: "hello required",
    1001: "gone",
    1002: "capability",
    1003: "stop-handler-taken",
}

#: Each capability, and whether Claude Code, Codex and OpenCode offer it.
CAPABILITIES = {
    "deliver": (True, True, True),
    "only-if-idle": (True, True, True),
    "steer": (True, True, False),
    "stop-handler": (True, True, False),
    "clear": (True, False, False),
    "exit": (True, False, False),
    "compact": (True, True, True),
    "agents": (True, True, True),
    "stop-shells": (True, True, False),
    "guards": (True, True, False),
    "after-tool": (True, True, False),
    "usage": (True, True, False),
    "mcp-token": (False, True, False),
    "environ": (True, True, True),
}

#: Each method, the capability it needs (None: always there), and its result's keys
#: (a value's kind where the result is a value).
METHODS = {
    "session": (None, {"harness", "pid", "endpoint_pid", "session_id", "earlier", "cwd",
                       "nonce", "started", "endpoint_version", "mod_version"}),
    "facts": (None, "facts"),
    "agents": ("agents", "agents"),
    "deliver": ("deliver", {"delivery", "ref"}),
    "steer": ("steer", {"delivery", "ref"}),
    "wait": (None, {"result"}),
    "command": (None, {"result"}),
    "watch": (None, {"facts"}),
    "stops.handle": ("stop-handler", set()),
    "stops.answer": (None, {"taken"}),
    "environ": ("environ", {"values"}),
    "usage.codex_limits": ("usage", {"payload", "received_at"}),
    "mcp.reload": ("mcp-token", {"reloaded", "servers"}),
}

#: The fields of the `session` answer that may be absent.
SESSION_OPTIONAL = {"trouble"}

#: One sample of every value, nested values filled in.
SAMPLES = [
    R.Facts("s", 3, True, R.OpenTurn("t", R.DeliveryRef("d1", "r1"), None, 1.5), 2, False,
            2.5, True, R.Ready("startup", "s", 1.0)),
    R.Facts("s", 0, False, None, None, True, 2.5),
    R.Facts("s", 1, True, R.OpenTurn("t", None, "compact", 1.5), 0, False, 2.5),
    R.Agents((R.Agent("a", "subagent", "running", False, "look", None),
              R.Agent("b", "shell", "running", False),
              R.Agent("c", "teammate", "done", True, None, "a")), True, 3.0),
    R.Agents((), False, 3.0),
    R.Started("t", "d1", "r1"),
    R.Started("t", "d1"),
    R.Refused("draft", "d1", "r1"),
    R.Refused("rate"),
    R.Lost("expired: no word", "d1", "r1"),
    R.Ended("t", "answer", "d1", None),
    R.Ran("s2"),
    R.Ran(None, R.Shells(("b1",), (R.LeftShell("b2", "timeout"),
                                    R.LeftShell(None, R.SHELL_UNLISTED)), False)),
    R.Ran(None, R.Shells((), (), True)),
    R.Failed("expired: no word"),
    R.Passed(),
    R.StopFacts("t", "s", True, ("shell",), 2),
    R.StopFacts(),
]

NOTE_SAMPLES = [
    R.Ready("clear", "s2"),
    R.TurnStarted("t", "s", 1, (R.DeliveryRef("d1", "r1", "opened"),)),
    R.TurnStarted("t", "s", 2),
    R.TurnStarted("t", "s", 3, (), "compact"),
    R.TurnSteered("t", "s", R.DeliveryRef("d2", "r2")),
    R.TurnEnded("t", "s", 1, "answer", (R.DeliveryRef("d1", "r1", "opened"),
                                        R.DeliveryRef("d2", None, "steered"))),
    R.Gone("the session ended"),
]


def _wire(value):
    """`value` as it leaves the socket and comes back: through JSON."""
    return json.loads(json.dumps(value))


def _doc_table(*header: str) -> list[list[str]]:
    """The rows of the document's table whose header row is `header`, cells stripped."""
    want = "| " + " | ".join(header) + " |"
    lines = DOC.splitlines()
    start = lines.index(want) + 2
    rows = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _names(text: str) -> list[str]:
    """The name at the start of each code span in `text`."""
    spans = (re.match(r"[a-z_.-]+", span) for span in re.findall(r"`([^`]+)`", text))
    return [name.group() for name in spans if name]


def _fields(cell: str) -> set[str]:
    """The field names a table cell lists: its code spans outside parentheses, none for a
    cell that says `none`."""
    if cell.startswith("none"):
        return set()
    return set(_names(re.sub(r"\([^)]*\)", "", cell)))


def test_the_table_and_the_values_agree():
    for value in SAMPLES:
        wire = value.to_wire()
        fields = set(VALUES[wire.pop("kind")])
        if isinstance(value, R.Refused) and value.delivery is None:
            fields = {"reason"}
        assert set(wire) == fields, value
    assert {v.to_wire()["kind"] for v in SAMPLES} == set(VALUES)
    for cls, fields in NESTED.items():
        assert tuple(f for f in cls.__dataclass_fields__) == fields, cls
    for note in NOTE_SAMPLES:
        assert set(note.to_params()) == set(NOTIFICATIONS[note.METHOD]), note
    assert set(R.NOTIFICATIONS) == set(NOTIFICATIONS)
    assert {code: name for code, name in ERRORS.items() if code > 0} == R.ERRORS
    assert (R.ERROR_GONE, R.ERROR_CAPABILITY, R.ERROR_STOP_HANDLER_TAKEN) == (1001, 1002, 1003)
    assert tuple(CAPABILITIES) == R.CAPABILITIES
    assert {need for need, _ in METHODS.values() if need} <= set(R.CAPABILITIES)


def test_each_adapter_offers_its_column_of_the_capability_table():
    from moxi_turns.claude_endpoint import ClaudeHarness
    from moxi_turns.codex import CodexHarness
    from moxi_turns.opencode import OpenCodeHarness

    for column, adapter in enumerate((ClaudeHarness, CodexHarness, OpenCodeHarness)):
        offered = {name for name, cells in CAPABILITIES.items() if cells[column]}
        assert set(adapter.can) == offered, adapter.__name__


def test_the_table_and_the_document_agree():
    values = {row[0].strip("`"): _fields(row[1]) for row in _doc_table("kind", "fields")}
    assert values == {kind: set(fields) for kind, fields in VALUES.items()}
    notes = {row[0].strip("`"): _names(row[1])
             for row in _doc_table("notification", "params")}
    assert notes == {method: list(params) for method, params in NOTIFICATIONS.items()}
    errors = {int(code): name.strip("`") for row in _doc_table("code", "name", "when")
              for code, name in [row[:2]]}
    assert errors == ERRORS
    caps = {row[0].strip("`"): tuple(cell == "yes" for cell in row[1:4])
            for row in _doc_table("capability", "Claude Code", "Codex", "OpenCode",
                                  "what it offers")}
    assert caps == CAPABILITIES
    methods = [row[0].strip("`") for row in _doc_table("method", "params", "result")]
    assert methods == list(METHODS)
    headings = [line for line in DOC.splitlines() if line.startswith("### ")]
    for method in METHODS:
        assert any(f"`{method}`" in heading for heading in headings), method


def test_the_refusals_and_names_the_document_spells():
    delivery = DOC.split("A delivery's refusal `reason` is one of:\n", 1)[1].split("\n\n")[0]
    assert [_names(line)[0] for line in delivery.splitlines() if line.startswith("- ")] \
        == list(R.DELIVERY_REFUSALS)
    command = DOC.split("A command's refusal `reason` is one of", 1)[1].split("\n\n")[0]
    assert set(_names(command)) - {"compact", "stop_shells"} == set(R.COMMAND_REFUSALS)
    assert "`gone` is not a refusal on the wire" in DOC
    assert R.REFUSED_GONE not in R.DELIVERY_REFUSALS + R.COMMAND_REFUSALS
    outcomes = DOC.split("- `outcome` is one of", 1)[1].split("\n- ")[0]
    assert tuple(_names(outcomes)) == R.OUTCOMES
    sources = DOC.split("- `source` is one of", 1)[1].split("\n\n")[0]
    assert tuple(_names(sources)) == R.READY_SOURCES
    command_names = DOC.split("Params: `name`,", 1)[1].split(";")[0]
    assert tuple(_names(command_names)) == R.COMMANDS
    for name in R.AGENT_KINDS + R.AGENT_STATES + R.DELIVERY_HOW:
        assert f"`{name}`" in DOC, name
    for prefix in (R.LOST_UNTIED, R.LOST_EXPIRED, R.LOST_UNKNOWN, R.LOST_UNANSWERED,
                   R.FAILED_TIMEOUT, R.FAILED_UNANSWERED):
        assert f"`{prefix}`" in DOC, prefix


def test_every_value_round_trips_through_json():
    for value in SAMPLES:
        assert R.from_wire(_wire(value.to_wire())) == value, value
    for note in NOTE_SAMPLES:
        assert R.from_notification(note.METHOD, _wire(note.to_params())) == note, note


def test_every_delivery_value_names_its_delivery_and_echoes_its_ref():
    for value in SAMPLES:
        wire = value.to_wire()
        if wire["kind"] in DELIVERY_VALUES and getattr(value, "delivery", None):
            assert wire["delivery"] == "d1" and "ref" in wire, value
    assert R.Started("t", "d1").to_wire()["ref"] is None
    for note in NOTE_SAMPLES:
        params = note.to_params()
        for entry in params.get("deliveries", ()):
            assert set(entry) == {"id", "ref", "how"} and entry["how"] in R.DELIVERY_HOW
        if "delivery" in params:
            assert set(params["delivery"]) == {"id", "ref"}


def test_the_nested_shapes_on_the_wire():
    facts = SAMPLES[0].to_wire()
    assert facts["turn"] == {"turn_id": "t", "opening": {"id": "d1", "ref": "r1"},
                             "command": None, "started_at": 1.5}
    assert facts["ready"] == {"source": "startup", "session_id": "s", "at": 1.0}
    assert R.Ready("startup", "s", 1.0).to_params() == {"source": "startup",
                                                        "session_id": "s"}
    assert SAMPLES[12].to_wire()["shells"] == {
        "stopped": ["b1"], "left": [{"id": "b2", "why": "timeout"},
                                    {"id": None, "why": "unlisted"}], "complete": False}
    assert R.Ran("s2").to_wire() == {"kind": "ran", "new_session_id": "s2", "shells": None}
    assert R.Refused("rate").to_wire() == {"kind": "refused", "reason": "rate"}


def test_malformed_values_and_notifications_are_refused():
    for raw in ({"kind": "nope"}, {"kind": "turn-started"}, {"kind": "gone", "reason": "x"},
                [], None, {"kind": "facts", "session_id": "s"},
                {"kind": "ran", "shells": "all"},
                {"kind": "agents", "entries": [{"id": "a"}], "complete": True, "at": 1}):
        with pytest.raises(ValueError):
            R.from_wire(raw)
    for method, params in (("turn.begun", {}), ("turn.started", {"turn_id": "t"}),
                           ("turn.steered", {"turn_id": "t", "session_id": "s",
                                             "delivery": "d1"}),
                           ("session.gone", None)):
        with pytest.raises(ValueError):
            R.from_notification(method, params)


def test_a_ref_is_at_most_256_bytes_of_utf8():
    assert R.check_ref(None) is None
    assert R.check_ref("x" * 256) == "x" * 256
    assert R.check_ref("é" * 128) == "é" * 128
    for bad in ("x" * 257, "é" * 129, 5, b"x", ["x"], "\ud800"):
        with pytest.raises(ValueError):
            R.check_ref(bad)


def test_the_busy_count_reads_the_agents_list():
    agents = R.Agents((R.Agent("a", "subagent", "running", False),
                       R.Agent("b", "subagent", "waiting", None),
                       R.Agent("c", "subagent", "running", True),
                       R.Agent("d", "subagent", "idle", False),
                       R.Agent("e", "shell", "running", False),
                       R.Agent("f", "teammate", "pending", False)), True, 1.0)
    assert agents.busy() == 4
    assert agents.busy(shells=False) == 3
    assert R.Agents(agents.entries, False, 1.0).busy() is None
    assert R.Agents((), True, 1.0).busy() == 0


def test_lost_tells_the_endpoints_own_reasons_apart():
    assert R.Lost("untied: no turn").untied and R.Lost("expired: no word").untied
    for reason in ("unknown delivery: not made here", "unanswered", "the endpoint ended"):
        assert not R.Lost(reason).untied


# -- the table's wire rows, against an endpoint ---------------------------------------
_POSIX = pytest.mark.skipif(sys.platform == "win32",
                            reason="moxi.ipc's named pipe client comes later")


@pytest.fixture
def connect(make_endpoint):
    """`connect(can=…, hello=True)`: an endpoint on the fake harness offering `can`, and
    a client connected to it, past `rpc.hello` when `hello` is true."""
    clients = []

    def make(can=R.CAPABILITIES, *, hello=True, ep=None):
        if ep is None:
            ep, _ = make_endpoint(FakeHarness(can=can))
        client = ipc.connect(ipc.address("turns", ep.dir.name), timeout=5)
        clients.append(client)
        if hello:
            client.hello((2, 2))
        return client, ep

    yield make
    for client in clients:
        client.close()


def _code(client, method, params=None) -> ipc.RpcError:
    with pytest.raises(ipc.RpcError) as caught:
        client.call(method, params, timeout=5)
    return caught.value


#: Requests whose params break the contract: each is answered invalid params.
INVALID_PARAMS = [
    ("deliver", {}),
    ("deliver", {"text": ""}),
    ("deliver", {"text": 5}),
    ("deliver", {"text": "x", "ref": 5}),
    ("deliver", {"text": "x", "ref": "x" * 257}),
    ("deliver", {"text": "x", "ref": "é" * 129}),
    ("deliver", {"text": "x", "if_turn_number": 0}),
    ("deliver", {"text": "x", "refuse_draft": True}),
    ("deliver", {"text": "x", "refuse_agents": True}),
    ("deliver", {"text": "x", "only_if_idle": False, "refuse_draft": True}),
    ("deliver", {"text": "x", "only_if_idle": "yes"}),
    ("deliver", {"text": "x", "only_if_idle": True, "if_turn_number": "0"}),
    ("deliver", {"text": "x", "only_if_idle": True, "refuse_draft": "yes"}),
    ("steer", {}),
    ("steer", {"text": ""}),
    ("steer", {"text": "x", "ref": "x" * 257}),
    ("wait", {"until": "started", "timeout_ms": 0}),
    ("wait", {"delivery": "d", "ref": "r", "until": "started", "timeout_ms": 0}),
    ("wait", {"delivery": "d", "until": "begun", "timeout_ms": 0}),
    ("wait", {"delivery": "d", "until": "started", "timeout_ms": -1}),
    ("wait", {"delivery": "d", "until": "started", "timeout_ms": "0"}),
    ("wait", {"ref": "x" * 257, "until": "started", "timeout_ms": 0}),
    ("command", {"name": "dance"}),
    ("command", {"name": "clear", "stop_shells": "yes"}),
    ("command", {"name": "exit", "stop_shells": True}),
    ("command", {"name": "exit", "stop_shells": False}),
    ("command", {"name": "clear", "if_turn_number": "0"}),
    ("command", {"name": "clear", "timeout_ms": -1}),
    ("environ", {}),
    ("environ", {"names": "HOME"}),
    ("environ", {"names": ["HOME", 5]}),
    ("environ", {"names": ["1HOME"]}),
    ("environ", {"names": ["A-B"]}),
    ("environ", {"names": [""]}),
    ("environ", {"names": [f"V{n}" for n in range(65)]}),
    ("usage.codex_limits", {"cached": "yes"}),
]

#: Methods turns does not have, among them version 1's old ops.
UNKNOWN_METHODS = ["start", "hold", "hello", "stop-answer", "dance"]

#: A request on a session that lacks what it needs, and the capability the error names.
CAPABILITY_ROWS = [
    ("steer", {"text": "x"}, "steer"),
    ("deliver", {"text": "x", "only_if_idle": True}, "only-if-idle"),
    ("stops.handle", {}, "stop-handler"),
    ("agents", {}, "agents"),
    ("command", {"name": "clear"}, "clear"),
    ("command", {"name": "exit"}, "exit"),
    ("command", {"name": "compact"}, "compact"),
    ("command", {"name": "clear", "stop_shells": True}, "stop-shells"),
    ("command", {"name": "compact", "stop_shells": True}, "stop-shells"),
    ("environ", {"names": ["HOME"]}, "environ"),
    ("usage.codex_limits", {}, "usage"),
    ("mcp.reload", {}, "mcp-token"),
]


@_POSIX
def test_the_hello_rows(connect):
    client, ep = connect(hello=False)
    refused = _code(client, "rpc.hello", {"versions": [3, 3], "capabilities": []})
    assert refused.code == -32001
    assert refused.data == {"client": [3, 3], "server": [2, 2]}
    client, _ = connect(hello=False, ep=ep)
    assert _code(client, "session", {}).code == -32002
    client, _ = connect(hello=False, ep=ep)
    agreed = client.hello((1, 2))
    assert agreed.version == 2 and set(agreed.capabilities) == set(R.CAPABILITIES)


@_POSIX
def test_the_capabilities_a_session_offers(connect):
    client, _ = connect(can=("deliver", "only-if-idle", "compact", "agents"), hello=False)
    agreed = client.hello((2, 2))
    assert set(agreed.capabilities) == {"deliver", "only-if-idle", "compact", "agents"}


@_POSIX
@pytest.mark.parametrize("method", UNKNOWN_METHODS)
def test_an_unknown_method(connect, method):
    client, _ = connect()
    assert _code(client, method, {}).code == -32601


@_POSIX
@pytest.mark.parametrize("method, params", INVALID_PARAMS,
                         ids=[f"{m}-{i}" for i, (m, _) in enumerate(INVALID_PARAMS)])
def test_invalid_params(connect, method, params):
    client, _ = connect()
    assert _code(client, method, params).code == -32602


@_POSIX
@pytest.mark.parametrize("method, params, capability", CAPABILITY_ROWS,
                         ids=[f"{c}-{i}" for i, (_, _, c) in enumerate(CAPABILITY_ROWS)])
def test_a_capability_the_session_lacks(connect, method, params, capability):
    can = [name for name in R.CAPABILITIES if name != capability]
    client, _ = connect(can=can)
    error = _code(client, method, params)
    assert (error.code, error.data) == (R.ERROR_CAPABILITY, {"capability": capability})


@_POSIX
def test_the_result_shapes_of_the_reads(connect):
    client, ep = connect()
    session = client.call("session", {}, timeout=5)
    want = METHODS["session"][1]
    assert want <= set(session) <= want | SESSION_OPTIONAL
    assert session["harness"] == ep.harness.name and session["nonce"] == ep.nonce
    facts = R.from_wire(client.call("facts", {}, timeout=5))
    assert isinstance(facts, R.Facts) and facts.turn_number == 0 and facts.turn is None
    assert set(client.call("facts", {}, timeout=5)) == {"kind", *VALUES["facts"]}
    watch, _ = connect(ep=ep)
    first = watch.call("watch", {}, timeout=5)
    assert set(first) == METHODS["watch"][1]
    assert isinstance(R.from_wire(first["facts"]), R.Facts)


@_POSIX
def test_environ_answers_the_names_asked_from_the_endpoints_environment(
        connect, make_endpoint):
    # A session's environment is a whole one: it places the endpoint's own folders too.
    ep, _ = make_endpoint(FakeHarness(can=R.CAPABILITIES),
                          env={**os.environ, "MOXI_T_SET": "one", "MOXI_T_EMPTY": "",
                               "PATH": "/bin"})
    client, _ = connect(ep=ep)
    answer = client.call("environ", {"names": ["MOXI_T_SET", "MOXI_T_EMPTY", "MOXI_T_NO"]},
                         timeout=5)
    assert set(answer) == METHODS["environ"][1]
    assert answer["values"] == {"MOXI_T_SET": "one", "MOXI_T_EMPTY": "", "MOXI_T_NO": None}
    assert client.call("environ", {"names": []}, timeout=5) == {"values": {}}
    names = [f"MOXI_T_{n}" for n in range(R.ENVIRON_MAX_NAMES)]
    assert client.call("environ", {"names": names}, timeout=5)["values"] == \
        dict.fromkeys(names)
    assert R.ENVIRON_MAX_NAMES == 64


@_POSIX
def test_a_deliver_answers_its_id_and_echoes_its_ref(connect):
    client, _ = connect()
    answer = client.call("deliver", {"text": "x", "ref": "r1"}, timeout=5)
    assert {"delivery", "ref"} <= set(answer) <= {"delivery", "ref", "result"}
    assert answer["ref"] == "r1" and isinstance(answer["delivery"], str)
    plain = client.call("deliver", {"text": "y"}, timeout=5)
    assert plain["ref"] is None


@_POSIX
def test_a_wait_for_an_unknown_delivery(connect):
    client, _ = connect()
    by_id = client.call("wait", {"delivery": "nonesuch", "until": "started",
                                 "timeout_ms": 0}, timeout=5)
    lost = R.from_wire(by_id["result"])
    assert isinstance(lost, R.Lost) and lost.reason.startswith(R.LOST_UNKNOWN)
    by_ref = client.call("wait", {"ref": "nonesuch", "until": "started", "timeout_ms": 0},
                         timeout=5)
    assert by_ref["delivery"] is None
    lost = R.from_wire(by_ref["result"])
    assert isinstance(lost, R.Lost) and lost.reason.startswith(R.LOST_UNKNOWN)


@_POSIX
def test_one_stop_handler_at_a_time(connect):
    first, ep = connect()
    assert first.call("stops.handle", {}, timeout=5) == {}
    second, _ = connect(ep=ep)
    assert _code(second, "stops.handle", {}).code == R.ERROR_STOP_HANDLER_TAKEN
