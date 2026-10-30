"""moxi.ipc's shared wire: framing, the error types and codes, the hello, address()."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap

import pytest

import moxi.ipc as ipc
from moxi import paths
from moxi.ipc import _wire as wire

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX socket addresses")
WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows pipe addresses")


def frame(line: bytes) -> dict:
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    return json.loads(line)


def req(id, method, params=None, **extra) -> bytes:
    obj = {"jsonrpc": "2.0", "id": id, "method": method, **extra}
    if params is not None:
        obj["params"] = params
    return json.dumps(obj).encode()


def note(method, params=None) -> bytes:
    obj = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        obj["params"] = params
    return json.dumps(obj).encode()


HELLO = ipc.Hello((1, 3), {"watch", "steer"})


def hello_line(id=0, versions=(1, 3), capabilities=("watch",)) -> bytes:
    return req(id, "rpc.hello", {"versions": list(versions),
                                 "capabilities": list(capabilities)})


# -- the error types ------------------------------------------------------------------------

def test_every_error_derives_from_ipc_error():
    for cls in (ipc.RpcError, ipc.NotRpc, ipc.NotServing, ipc.AlreadyServing, ipc.Timeout,
                wire.LineTooLong):
        assert issubclass(cls, ipc.IpcError)
    assert not issubclass(ipc.NotRpc, ipc.RpcError)
    assert issubclass(ipc.Timeout, TimeoutError)


def test_rpc_error_carries_code_message_and_data():
    exc = ipc.RpcError(1001, "no such session", {"session": "x"})
    assert (exc.code, exc.message, exc.data) == (1001, "no such session", {"session": "x"})
    assert str(exc) == "no such session (code 1001)"
    assert exc.error() == {"code": 1001, "message": "no such session", "data": {"session": "x"}}
    assert ipc.RpcError(1002, "plain").error() == {"code": 1002, "message": "plain"}
    with pytest.raises(TypeError):
        ipc.RpcError(True, "a bool is no code")


def test_not_rpc_keeps_the_first_200_bytes():
    exc = ipc.NotRpc(b"x" * 500)
    assert exc.line == b"x" * 200


def test_not_serving_exits_75_and_already_serving_names_the_holder():
    assert ipc.NotServing.exit_code == 75
    exc = ipc.NotServing("/run/a.sock")
    assert exc.address == "/run/a.sock" and "nothing is serving at /run/a.sock" in str(exc)
    held = ipc.AlreadyServing("/run/a.sock", 4321)
    assert held.holder == 4321 and "pid 4321" in str(held)
    assert ipc.AlreadyServing("/run/a.sock").holder is None


# -- encoding -------------------------------------------------------------------------------

def test_a_message_is_one_line_even_when_a_string_holds_newlines():
    line = wire.encode(wire.notification("say", {"text": "one\ntwo\r\n"}))
    assert frame(line) == {"jsonrpc": "2.0", "method": "say",
                           "params": {"text": "one\ntwo\r\n"}}


def test_the_builders_make_json_rpc_2():
    assert wire.request(1, "go") == {"jsonrpc": "2.0", "id": 1, "method": "go"}
    assert wire.request("a", "go", (1, 2))["params"] == [1, 2]
    assert wire.result(3, None) == {"jsonrpc": "2.0", "id": 3, "result": None}
    assert wire.error(None, ipc.RpcError(-32700, "bad")) == {
        "jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "bad"}}


def test_params_are_an_object_or_an_array():
    with pytest.raises(TypeError):
        wire.request(1, "go", 5)
    with pytest.raises(TypeError):
        wire.notification("go", "text")


def test_nan_is_refused_on_the_way_out():
    with pytest.raises(ValueError):
        wire.encode(wire.result(1, float("nan")))


def test_the_too_long_answer():
    assert frame(wire.too_long(4 << 20)) == {
        "jsonrpc": "2.0", "id": None,
        "error": {"code": -32600, "message": "line longer than 4194304 bytes"}}


# -- the line splitter ----------------------------------------------------------------------

def test_lines_join_across_chunks():
    lines = wire.Lines()
    assert lines.feed(b'{"a"') == []
    assert lines.feed(b':1}\n{"b":2}\n{"c"') == [b'{"a":1}', b'{"b":2}']
    assert lines.feed(b":3}\n") == [b'{"c":3}']
    assert lines.feed(b"\n") == [b""]


def test_a_line_of_exactly_the_cap_passes():
    assert wire.Lines(8).feed(b"x" * 8 + b"\n") == [b"x" * 8]


def test_a_line_one_byte_over_the_cap_is_refused():
    with pytest.raises(wire.LineTooLong, match="line longer than 8 bytes"):
        wire.Lines(8).feed(b"x" * 9 + b"\n")


def test_an_unfinished_line_over_the_cap_is_refused_before_its_newline():
    lines = wire.Lines(8)
    lines.feed(b"x" * 5)
    with pytest.raises(wire.LineTooLong):
        lines.feed(b"x" * 4)


def test_the_lines_before_an_overlong_one_are_kept_on_the_error():
    with pytest.raises(wire.LineTooLong) as caught:
        wire.Lines(8).feed(b"one\ntwo\n" + b"x" * 20)
    assert caught.value.lines == [b"one", b"two"]


def test_the_default_cap_is_four_mib():
    assert wire.MAX_LINE == 4 * 1024 * 1024 == wire.Lines().max_line


# -- the server's side: framing -------------------------------------------------------------

def test_blank_lines_are_skipped():
    inbound = wire.Inbound()
    assert inbound.receive(b"") is None
    assert inbound.receive(b"  \t\r") is None
    reply = inbound.receive(b"{")
    assert reply.first  # a blank line is not the connection's first line


def test_invalid_json_is_minus_32700_with_a_null_id_and_stays_open():
    for line in (b"{", b"not json", b"\xff\xfe", b'{"a": NaN}'):
        reply = wire.Inbound().receive(line)
        assert isinstance(reply, wire.Reply) and not reply.close
        assert reply.kind == "json"
        answer = frame(reply.frame)
        assert answer["id"] is None and answer["error"]["code"] == -32700


@pytest.mark.parametrize("line", [b"5", b'"text"', b"null", b"true"])
def test_a_value_that_is_not_an_object_is_minus_32600(line):
    reply = wire.Inbound().receive(line)
    assert reply.kind == "value" and not reply.close
    assert frame(reply.frame)["error"]["code"] == -32600


@pytest.mark.parametrize("line", [b"[]", b"[" + req(1, "go") + b"]"])
def test_a_batch_is_refused(line):
    reply = wire.Inbound().receive(line)
    assert reply.kind == "batch"
    assert frame(reply.frame)["error"] == {"code": -32600,
                                           "message": "batches are not supported"}


@pytest.mark.parametrize("obj, answered_id", [
    ({"id": 1, "method": "go"}, 1),                          # no jsonrpc member
    ({"jsonrpc": "1.0", "id": 1, "method": "go"}, 1),
    ({"jsonrpc": "2.0", "id": 1}, 1),                        # no method
    ({"jsonrpc": "2.0", "id": 1, "method": 7}, 1),
    ({"jsonrpc": "2.0", "id": 1, "method": "go", "params": 5}, 1),
    ({"jsonrpc": "2.0", "id": 1.5, "method": "go"}, None),   # an id that can't be echoed
    ({"jsonrpc": "2.0", "id": True, "method": "go"}, None),
    ({"jsonrpc": "2.0", "id": [1], "method": "go"}, None),
])
def test_an_object_that_is_not_a_request_is_minus_32600(obj, answered_id):
    reply = wire.Inbound().receive(json.dumps(obj).encode())
    assert reply.kind == "request" and not reply.close
    answer = frame(reply.frame)
    assert answer["id"] == answered_id and answer["error"]["code"] == -32600


def test_an_old_clients_first_frame_is_marked_for_the_legacy_hook():
    reply = wire.Inbound().receive(b'{"id": 1, "op": "register", "name": "planner"}')
    assert (reply.first, reply.kind) == (True, "request")
    inbound = wire.Inbound()
    inbound.receive(req(1, "go"))
    assert inbound.receive(b"{").first is False


def test_a_request_and_a_notification_reach_the_handler():
    inbound = wire.Inbound()
    assert inbound.receive(req(7, "go", {"a": 1})) == wire.Message(7, "go", {"a": 1}, False)
    assert inbound.receive(req("k", "go", [1])) == wire.Message("k", "go", [1], False)
    assert inbound.receive(note("tick")) == wire.Message(None, "tick", None, True)
    assert inbound.receive(req(None, "go")) == wire.Message(None, "go", None, False)


def test_a_reserved_method_never_reaches_the_handler():
    inbound = wire.Inbound()
    reply = inbound.receive(req(1, "rpc.other"))
    assert reply.kind == "reserved" and frame(reply.frame)["error"]["code"] == -32601
    dropped = inbound.receive(note("rpc.other"))
    assert dropped.frame is None and not dropped.close


# -- the server's side: answers -------------------------------------------------------------

def test_a_result_is_answered_with_the_requests_id():
    inbound = wire.Inbound()
    message = inbound.receive(req(9, "go"))
    assert frame(inbound.answer(message, {"ok": True})) == {
        "jsonrpc": "2.0", "id": 9, "result": {"ok": True}}


def test_an_rpc_error_from_the_handler_is_sent_as_it_is():
    inbound = wire.Inbound()
    message = inbound.receive(req(9, "go"))
    sent = frame(inbound.fail(message, ipc.RpcError(1003, "busy", [1])))
    assert sent["id"] == 9 and sent["error"] == {"code": 1003, "message": "busy", "data": [1]}


def test_any_other_exception_is_minus_32603_and_its_text_is_not_sent():
    inbound = wire.Inbound()
    message = inbound.receive(req(9, "go"))
    sent = inbound.fail(message, RuntimeError("secret detail"))
    assert frame(sent)["error"] == {"code": -32603, "message": "internal error"}
    assert b"secret" not in sent


def test_a_result_json_cannot_carry_is_minus_32603():
    inbound = wire.Inbound()
    message = inbound.receive(req(9, "go"))
    assert frame(inbound.answer(message, object()))["error"]["code"] == -32603
    assert frame(inbound.fail(message, ipc.RpcError(1, "x", object())))["error"]["code"] == (
        -32603)


def test_a_notifications_result_and_failure_are_not_answered():
    inbound = wire.Inbound()
    message = inbound.receive(note("tick"))
    assert inbound.answer(message, 1) is None
    assert inbound.fail(message, ipc.RpcError(1, "x")) is None
    assert inbound.fail(message, RuntimeError()) is None


# -- the hello rule -------------------------------------------------------------------------

@pytest.mark.parametrize("client, server, version", [
    ((1, 1), (1, 1), 1),
    ((1, 5), (2, 3), 3),
    ((2, 3), (1, 5), 3),
    ((1, 2), (2, 9), 2),
    ((0, 7), (3, 3), 3),
])
def test_the_version_is_the_highest_in_both_ranges(client, server, version):
    agreed = ipc.hello({"versions": list(client)}, ipc.Hello(server))
    assert agreed == ipc.Agreed(version, frozenset())


def test_no_common_version_is_refused_with_both_ranges():
    with pytest.raises(ipc.RpcError) as caught:
        ipc.hello({"versions": [3, 4], "capabilities": []}, ipc.Hello((1, 2)))
    assert caught.value.code == -32001
    assert caught.value.data == {"client": [3, 4], "server": [1, 2]}


def test_the_servers_agreed_holds_the_set_the_client_offered():
    agreed = ipc.hello({"versions": [1, 3], "capabilities": ["watch", "environ"]}, HELLO)
    assert agreed.capabilities == frozenset({"watch", "environ"})
    # a name the server never declared is kept: the two sets are separate names
    assert ipc.hello({"versions": [1, 3], "capabilities": ["guards"]},
                     HELLO).capabilities == frozenset({"guards"})
    assert ipc.hello({"versions": [1, 3]}, HELLO).capabilities == frozenset()
    assert ipc.hello({"versions": [1, 3], "capabilities": []},
                     ipc.Hello((1, 3))).capabilities == frozenset()


@pytest.mark.parametrize("offer", [
    None, [1, 3], {"versions": [3, 1]}, {"versions": [1]}, {"versions": [1, "2"]},
    {"versions": [True, 2]}, {"versions": [-1, 2]}, {},
    {"versions": [1, 2], "capabilities": "watch"},
    {"versions": [1, 2], "capabilities": [1]},
])
def test_an_offer_of_the_wrong_shape_is_minus_32602(offer):
    with pytest.raises(ipc.RpcError) as caught:
        ipc.hello(offer, HELLO)
    assert caught.value.code == -32602


def test_hello_and_agreed_are_frozen_records():
    assert HELLO.versions == (1, 3)
    assert HELLO.capabilities == frozenset({"watch", "steer"})
    assert ipc.Hello([1, 3], ["steer", "watch"]) == HELLO
    assert hash(ipc.Hello((1, 3), {"watch", "steer"})) == hash(HELLO)
    assert repr(ipc.Agreed(2)) == "Agreed(version=2, capabilities=frozenset())"
    with pytest.raises(AttributeError):
        HELLO.versions = (1, 1)
    for bad in ((2, 1), (1,), ("1", 2)):
        with pytest.raises(ValueError):
            ipc.Hello(bad)
    with pytest.raises(ValueError):
        ipc.Hello((1, 1), "watch")


def test_the_client_reads_the_answer_through_the_same_rule():
    ours = ipc.Hello((1, 2), {"watch", "environ"})
    assert wire.hello_params(ours) == {"versions": [1, 2],
                                       "capabilities": ["environ", "watch"]}
    assert wire.agreed_from({"version": 2, "capabilities": ["watch"]}, ours) == ipc.Agreed(
        2, {"watch"})
    # the client's agreed holds the server's answered set, offered by the client or not
    assert wire.agreed_from({"version": 1, "capabilities": ["watch", "other"]},
                            ours).capabilities == frozenset({"watch", "other"})
    assert wire.agreed_from({"version": 1, "capabilities": ["deliver"]},
                            ipc.Hello((1, 2))).capabilities == frozenset({"deliver"})


@pytest.mark.parametrize("answer", [
    None, {}, {"version": "2"}, {"version": 3}, {"version": 0},
    {"version": 1, "capabilities": "watch"},
])
def test_a_bad_hello_answer_is_an_ipc_error(answer):
    with pytest.raises(ipc.IpcError):
        wire.agreed_from(answer, ipc.Hello((1, 2)))


# -- the server's side: the hello -----------------------------------------------------------

def test_a_server_with_a_hello_agrees_and_then_passes_requests():
    inbound = wire.Inbound(HELLO)
    reply = inbound.receive(hello_line(0, (2, 9), ("watch", "environ")))
    assert reply.kind == "hello" and not reply.close
    assert frame(reply.frame) == {"jsonrpc": "2.0", "id": 0, "result": {
        "version": 3, "capabilities": ["steer", "watch"]}}
    assert inbound.agreed == ipc.Agreed(3, {"watch", "environ"})


def test_the_server_answers_its_own_capabilities_whatever_the_client_offered():
    for offered in ((), ("guards",), ("watch", "steer", "other")):
        inbound = wire.Inbound(HELLO)
        reply = inbound.receive(hello_line(0, (1, 3), offered))
        assert frame(reply.frame)["result"] == {"version": 3,
                                                "capabilities": ["steer", "watch"]}
        assert inbound.agreed == ipc.Agreed(3, offered)
    assert isinstance(inbound.receive(req(1, "go")), wire.Message)


def test_any_other_first_request_is_minus_32002_and_closes():
    inbound = wire.Inbound(HELLO)
    reply = inbound.receive(req(5, "go"))
    assert reply.close and reply.kind == "hello"
    answer = frame(reply.frame)
    assert answer["id"] == 5 and answer["error"]["code"] == -32002


def test_a_notification_before_the_hello_is_minus_32002_and_closes():
    reply = wire.Inbound(HELLO).receive(note("tick"))
    assert reply.close and frame(reply.frame)["error"]["code"] == -32002
    reply = wire.Inbound(HELLO).receive(note("rpc.hello", {"versions": [1, 3]}))
    assert reply.close and frame(reply.frame)["error"]["code"] == -32002


def test_framing_errors_before_the_hello_leave_it_due():
    inbound = wire.Inbound(HELLO)
    assert frame(inbound.receive(b"{").frame)["error"]["code"] == -32700
    assert frame(inbound.receive(hello_line()).frame)["result"]["version"] == 3


def test_a_refused_hello_stays_open_and_the_hello_stays_due():
    inbound = wire.Inbound(HELLO)
    reply = inbound.receive(hello_line(1, (7, 9)))
    assert not reply.close
    assert frame(reply.frame)["error"] == {
        "code": -32001, "message": "no common version: the client speaks 7 to 9, the "
                                   "server 1 to 3",
        "data": {"client": [7, 9], "server": [1, 3]}}
    assert inbound.agreed is None
    bad = frame(inbound.receive(req(2, "rpc.hello", [1, 3])).frame)
    assert bad["error"]["code"] == -32602
    assert frame(inbound.receive(hello_line(3)).frame)["result"]["version"] == 3


def test_a_second_hello_is_refused():
    inbound = wire.Inbound(HELLO)
    inbound.receive(hello_line())
    reply = inbound.receive(hello_line(1))
    assert not reply.close and frame(reply.frame)["error"]["code"] == -32600
    assert inbound.agreed == ipc.Agreed(3, {"watch"})


def test_without_a_hello_rpc_hello_is_method_not_found():
    inbound = wire.Inbound(None)
    reply = inbound.receive(hello_line())
    assert frame(reply.frame)["error"]["code"] == -32601
    assert inbound.agreed is None
    assert isinstance(inbound.receive(req(1, "go")), wire.Message)


# -- the client's side ----------------------------------------------------------------------

def test_the_client_reads_results_errors_and_notifications():
    assert wire.read_answer(b"") is None
    assert wire.read_answer(b'{"jsonrpc":"2.0","id":4,"result":[1]}') == wire.Response(
        4, [1], None)
    response = wire.read_answer(
        b'{"jsonrpc":"2.0","id":4,"error":{"code":1001,"message":"no","data":{"x":1}}}')
    assert response.id == 4 and response.result is None
    assert (response.error.code, response.error.message, response.error.data) == (
        1001, "no", {"x": 1})
    assert wire.read_answer(b'{"jsonrpc":"2.0","method":"push","params":{"n":1}}') == (
        wire.Note("push", {"n": 1}))
    assert wire.read_answer(b'{"jsonrpc":"2.0","method":"push"}') == wire.Note("push", None)
    assert wire.read_answer(wire.too_long(8)).error.code == -32600


@pytest.mark.parametrize("line", [
    b"OK registered\n",
    b'{"id": 1, "ok": true, "identity": {}}',          # an old daemon's answer
    b"[1, 2]",
    b'{"jsonrpc":"2.0","id":1}',
    b'{"jsonrpc":"2.0","id":1,"result":1,"error":{"code":1,"message":"x"}}',
    b'{"jsonrpc":"2.0","id":1,"error":"bad"}',
    b'{"jsonrpc":"2.0","id":1,"error":{"code":"1","message":"x"}}',
    b'{"jsonrpc":"2.0","id":1,"method":"call-from-server"}',
    b'{"jsonrpc":"2.0","method":"push","params":5}',
    b"\xff",
])
def test_an_answer_that_is_not_json_rpc_is_not_rpc(line):
    with pytest.raises(ipc.NotRpc) as caught:
        wire.read_answer(line)
    assert caught.value.line == line[:200]


# -- address --------------------------------------------------------------------------------

@POSIX
def test_a_posix_address_is_the_socket_path(monkeypatch):
    monkeypatch.setenv("MOXI_TURNS_RUNTIME_DIR", "/r")
    assert ipc.address("turns", "daemon") == "/r/turns/daemon.sock"
    assert ipc.address("turns", "daemon") == paths.socket_path("turns", "daemon")
    with pytest.raises(ValueError):
        ipc.address("turns", "a/b")


def test_a_windows_address_is_a_pipe_named_for_the_user(monkeypatch):
    from moxi import _win

    monkeypatch.setattr(ipc, "_WINDOWS", True)
    monkeypatch.setattr(_win, "user_sid", lambda: "S-1-5-21-1-2-3-1001")
    u = hashlib.sha256(b"S-1-5-21-1-2-3-1001").hexdigest()[:8]
    assert ipc.address("comms", "daemon") == f"\\\\.\\pipe\\moxi-comms-daemon-{u}"
    assert ipc._pipe_parts(ipc.address("comms", "my-page")) == ("comms", "my-page")
    for module, name in (("Comms", "daemon"), ("comms", ""), ("comms", "a\\b"),
                         ("comms", "a/b"), ("comms", "..")):
        with pytest.raises(ValueError):
            ipc.address(module, name)
    for bad in ("/tmp/x.sock", "\\\\.\\pipe\\other", "\\\\.\\pipe\\moxi-comms-daemon-xyz"):
        with pytest.raises(ValueError):
            ipc._pipe_parts(bad)


@WINDOWS
def test_the_windows_address_hashes_this_users_sid():
    from moxi import _win

    sid = _win.user_sid()
    assert sid.startswith("S-1-")
    u = hashlib.sha256(sid.encode()).hexdigest()[:8]
    assert ipc.address("comms", "daemon").endswith(f"-{u}")


# -- import cost ----------------------------------------------------------------------------

def test_importing_moxi_ipc_loads_neither_the_server_nor_the_client():
    code = textwrap.dedent("""\
        import sys
        import moxi.ipc
        moxi.ipc.Hello((1, 1)); moxi.ipc.RpcError(1, "x")
        heavy = ("asyncio", "socket", "selectors", "dataclasses", "psutil",
                 "moxi.ipc._server", "moxi.ipc._client", "moxi.aio")
        print(" ".join(m for m in heavy if m in sys.modules))
        """)
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                          check=True, timeout=60)
    assert done.stdout.strip() == ""


def test_the_public_surface():
    for name in ("address", "hello", "Hello", "Agreed", "IpcError", "RpcError", "NotRpc",
                 "NotServing", "AlreadyServing", "Timeout"):
        assert name in ipc.__all__ and hasattr(ipc, name)
    for name in ("serve", "sweep", "connect", "Client", "Server", "Connection", "Peer"):
        assert name in ipc.__all__ and name in dir(ipc)
    with pytest.raises(AttributeError):
        ipc.no_such_name  # noqa: B018
