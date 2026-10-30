"""The wire both ends of `moxi.ipc` share, without any I/O.

One JSON-RPC 2.0 message per line, UTF-8, ending in a newline. This module holds the
error types and codes, the hello rule, the encoding and decoding of one line, the line
splitter a blocking reader feeds, and `Inbound`, the server's side of one connection: it
turns each line into the message a handler gets or the answer the server sends itself.
The server and the client add the sockets, pipes and threads around it.
"""

from __future__ import annotations

import json

from .. import exit as _exit
from ..modules import _Frozen

TYPE_CHECKING = False
if TYPE_CHECKING:
    from collections.abc import Iterable

#: The longest line either end reads, without its newline.
MAX_LINE = 4 << 20

#: The hello's method. JSON-RPC keeps the `rpc.` prefix for methods like this one.
HELLO = "rpc.hello"

#: JSON-RPC's standard codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
#: `moxi.ipc`'s own codes, in the implementation block. A module takes its codes from
#: outside the reserved -32768..-32000.
HELLO_REFUSED = -32001
HELLO_REQUIRED = -32002

#: How much of a line that isn't JSON-RPC `NotRpc` keeps.
SHOWN = 200


# -- errors -----------------------------------------------------------------------------

class IpcError(Exception):
    """Every failure `moxi.ipc` raises derives from this."""


class RpcError(IpcError):
    """A JSON-RPC error object: raised by a handler to answer with it, and raised by the
    client when the server answers with one."""

    def __init__(self, code: int, message: str, data: object = None):
        if isinstance(code, bool) or not isinstance(code, int):
            raise TypeError(f"an RpcError's code is an int, not {code!r}")
        super().__init__(code, message, data)
        self.code = code
        self.message = str(message)
        self.data = data

    def __str__(self) -> str:
        return f"{self.message} (code {self.code})"

    def error(self) -> dict:
        """The error object as it goes on the wire."""
        obj: dict = {"code": self.code, "message": self.message}
        if self.data is not None:
            obj["data"] = self.data
        return obj


class NotRpc(IpcError):
    """The other end sent a line that is not a JSON-RPC message (an old daemon, say).
    `line` holds its first 200 bytes."""

    def __init__(self, line: bytes, reason: str | None = None):
        self.line = bytes(line[:SHOWN])
        self.reason = reason
        text = f"not a JSON-RPC message: {self.line!r}"
        super().__init__(f"{text} ({reason})" if reason else text)


class NotServing(IpcError):
    """Nothing listens at the address: the daemon is cold. A command that stops on this
    exits `exit_code` (try again later)."""

    exit_code = _exit.TEMPFAIL

    def __init__(self, address: str, reason: str | None = None):
        self.address = address
        self.reason = reason
        text = f"nothing is serving at {address}"
        super().__init__(f"{text}: {reason}" if reason else text)


class AlreadyServing(IpcError):
    """Another server holds the address's lock. `holder` is its pid when the lock file
    names one."""

    def __init__(self, address: str, holder: int | None = None):
        self.address = address
        self.holder = holder
        who = f"pid {holder}" if holder else "another process"
        super().__init__(f"{who} is already serving at {address}")


class Timeout(IpcError, TimeoutError):
    """No answer, or no connection, in the time given."""


class LineTooLong(IpcError):
    """A line ran past the cap. `lines` holds the whole lines read before it."""

    def __init__(self, max_line: int, lines: list[bytes] | None = None):
        self.max_line = max_line
        self.lines = lines or []
        super().__init__(f"line longer than {max_line} bytes")


# -- the hello --------------------------------------------------------------------------

class Hello(_Frozen):
    """What one end speaks: a range of versions, both ends included, and its
    capabilities."""

    __slots__ = _fields = ("versions", "capabilities")

    def __init__(self, versions: tuple[int, int],
                 capabilities: Iterable[str] = frozenset()):
        super().__init__(_range(versions, ValueError), _names(capabilities, ValueError))


class Agreed(_Frozen):
    """What a hello settled for one connection: the version, and the capabilities the
    other end declared (on the server the client's offered set, on the client the
    server's answered set)."""

    __slots__ = _fields = ("version", "capabilities")

    def __init__(self, version: int, capabilities: Iterable[str] = frozenset()):
        if not _whole(version):
            raise ValueError(f"a version is a whole number of 0 or more, not {version!r}")
        super().__init__(version, _names(capabilities, ValueError))


def hello(offer: object, ours: Hello) -> Agreed:
    """The hello rule: what a server that speaks `ours` agrees to with the client's
    `offer`, `{"versions": [lo, hi], "capabilities": [...]}`.

    The version is the highest one in both ranges. The two ends' capabilities are
    separate names, each end's own to declare, and nothing intersects them: the server
    answers its own set whatever the client offered (`hello_result`), and each side's
    `Agreed` holds the other end's set. So the `Agreed` this returns, the server's, holds
    the set the client offered; the client's, from `agreed_from`, holds the set the
    server answered. A module decides what a pair of sets means. No common version
    raises `RpcError` -32001, with data
    `{"client": [lo, hi], "server": [lo, hi]}`; an offer of the wrong shape raises
    `RpcError` -32602.
    """
    if not isinstance(offer, dict):
        raise RpcError(INVALID_PARAMS, f"{HELLO} takes an object "
                       '{"versions": [lo, hi], "capabilities": [...]}')
    try:
        theirs = _range(offer.get("versions"), TypeError)
        names = _names(offer.get("capabilities", []), TypeError, wire=True)
    except TypeError as exc:
        raise RpcError(INVALID_PARAMS, f"{HELLO}: {exc}") from None
    low, high = max(theirs[0], ours.versions[0]), min(theirs[1], ours.versions[1])
    if low > high:
        raise RpcError(HELLO_REFUSED,
                       f"no common version: the client speaks {theirs[0]} to {theirs[1]}, "
                       f"the server {ours.versions[0]} to {ours.versions[1]}",
                       {"client": list(theirs), "server": list(ours.versions)})
    return Agreed(high, names)


def hello_params(ours: Hello) -> dict:
    """The client's offer for `ours`, as `rpc.hello`'s params."""
    return {"versions": list(ours.versions), "capabilities": sorted(ours.capabilities)}


def hello_result(agreed: Agreed, ours: Hello) -> dict:
    """The server's answer to a hello it agreed to: the agreed version and the server's
    own capabilities, `ours`."""
    return {"version": agreed.version, "capabilities": sorted(ours.capabilities)}


def agreed_from(answer: object, ours: Hello) -> Agreed:
    """The client's reading of the server's hello answer: the agreed version and the
    server's capabilities as it answered them. An answer of the wrong shape, or a version
    outside the range the client offered, raises `IpcError`."""
    if not isinstance(answer, dict) or not _whole(answer.get("version")):
        raise IpcError(f"the server's hello answer is not "
                       f'{{"version": v, "capabilities": [...]}}: {answer!r}')
    version = answer["version"]
    if not ours.versions[0] <= version <= ours.versions[1]:
        raise IpcError(f"the server agreed version {version}, outside the "
                       f"{ours.versions[0]} to {ours.versions[1]} offered")
    try:
        names = _names(answer.get("capabilities", []), TypeError, wire=True)
    except TypeError as exc:
        raise IpcError(f"the server's hello answer: {exc}") from None
    return Agreed(version, names)


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _range(value: object, error: type[Exception]) -> tuple[int, int]:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(_whole(v) for v in value) or value[0] > value[1]):
        raise error(f"versions is [lo, hi], two whole numbers with lo <= hi, not {value!r}")
    return (value[0], value[1])


def _names(value: object, error: type[Exception], *, wire: bool = False) -> frozenset:
    kinds = (list,) if wire else (list, tuple, set, frozenset)
    if not isinstance(value, kinds) or not all(isinstance(v, str) for v in value):
        raise error(f"capabilities is a list of strings, not {value!r}")
    return frozenset(value)


# -- one line ---------------------------------------------------------------------------

def encode(message: dict) -> bytes:
    """One message as its line, newline included. Raises `TypeError` or `ValueError` for
    a value JSON can't carry (NaN included)."""
    return json.dumps(message, separators=(",", ":"), allow_nan=False).encode() + b"\n"


def request(id: int | str, method: str, params: object = None) -> dict:
    return {"jsonrpc": "2.0", "id": id, "method": method, **_params(params)}


def notification(method: str, params: object = None) -> dict:
    return {"jsonrpc": "2.0", "method": method, **_params(params)}


def result(id: int | str | None, value: object) -> dict:
    return {"jsonrpc": "2.0", "id": id, "result": value}


def error(id: int | str | None, exc: RpcError) -> dict:
    return {"jsonrpc": "2.0", "id": id, "error": exc.error()}


def too_long(max_line: int) -> bytes:
    """The answer to a line over the cap, sent just before the connection closes."""
    return encode(error(None, RpcError(INVALID_REQUEST,
                                       f"line longer than {max_line} bytes")))


def _params(params: object) -> dict:
    if params is None:
        return {}
    if isinstance(params, tuple):
        params = list(params)
    if not isinstance(params, (dict, list)):
        raise TypeError(f"params is an object or an array, not {params!r}")
    return {"params": params}


def _loads(line: bytes) -> object:
    """The JSON value of a UTF-8 line. Raises `ValueError` (a `UnicodeDecodeError` too)
    for anything else, NaN and Infinity included, which JSON doesn't have."""
    return json.loads(line.decode("utf-8"), parse_constant=_no_constant)


def _no_constant(name: str) -> object:
    raise ValueError(f"{name} is not JSON")


def _id_ok(value: object) -> bool:
    return value is None or isinstance(value, str) or (
        isinstance(value, int) and not isinstance(value, bool))


class Lines:
    """Splits the bytes a blocking reader gets into lines, holding at most `max_line`
    bytes of an unfinished one."""

    def __init__(self, max_line: int = MAX_LINE):
        self.max_line = max_line
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        """The lines `data` completes, newlines stripped. A line over `max_line` raises
        `LineTooLong` as soon as the bytes in hand show it, carrying in `lines` the lines
        that came before it; the splitter is spent after that."""
        lines: list[bytes] = []
        start = 0
        while (end := data.find(b"\n", start)) >= 0:
            self._take(data, start, end, lines)
            lines.append(bytes(self._buffer))
            self._buffer.clear()
            start = end + 1
        self._take(data, start, len(data), lines)
        return lines

    def _take(self, data: bytes, start: int, end: int, lines: list[bytes]) -> None:
        if len(self._buffer) + end - start > self.max_line:
            self._buffer.clear()
            raise LineTooLong(self.max_line, lines)
        self._buffer += data[start:end]


# -- the client's side of a line ----------------------------------------------------------

class Response(_Frozen):
    """An answer to a call: `result`, or `error` when the server refused it."""

    __slots__ = _fields = ("id", "result", "error")


class Note(_Frozen):
    """A notification: a message with a method and no id."""

    __slots__ = _fields = ("method", "params")


def read_answer(line: bytes) -> Response | Note | None:
    """What the client makes of one line from the server: a `Response`, a `Note`, or
    None for a blank line. Anything else raises `NotRpc`."""
    if not line.strip():
        return None
    try:
        obj = _loads(line)
    except ValueError:
        raise NotRpc(line, "not JSON") from None
    if not isinstance(obj, dict) or obj.get("jsonrpc") != "2.0":
        raise NotRpc(line, "not a JSON-RPC 2.0 object")
    if "method" in obj:
        if "id" in obj or not isinstance(obj["method"], str) or not isinstance(
                obj.get("params", {}), (dict, list)):
            raise NotRpc(line, "not a notification")
        return Note(obj["method"], obj.get("params"))
    if "id" not in obj or not _id_ok(obj["id"]) or ("result" in obj) == ("error" in obj):
        raise NotRpc(line, "neither a response nor a notification")
    if "result" in obj:
        return Response(obj["id"], obj["result"], None)
    err = obj["error"]
    if (not isinstance(err, dict) or isinstance(err.get("code"), bool)
            or not isinstance(err.get("code"), int) or not isinstance(err.get("message"), str)):
        raise NotRpc(line, "an error that is not a JSON-RPC error object")
    return Response(obj["id"], None, RpcError(err["code"], err["message"], err.get("data")))


# -- the server's side of a connection ----------------------------------------------------

class Message(_Frozen):
    """A request or notification for the handler. `id` is None for a notification
    (`notification` True); a request may carry an id of null, which is answered."""

    __slots__ = _fields = ("id", "method", "params", "notification")


class Reply(_Frozen):
    """An answer the server sends itself, without the handler.

    `frame` is the line to write, or None to write nothing; `close` says the connection
    closes after it. `first` is True when this was the connection's first line, and
    `kind` says what the line was: "json" (not JSON), "value" (JSON, not an object),
    "batch" (an array), "request" (an object, not a valid JSON-RPC request), "hello" (a
    message the hello rules answer: the hello itself, or anything sent before it), or
    "reserved" (an `rpc.` method once no hello is due; a notification of one is dropped,
    `frame` None). The server's legacy hook reads `first` and `kind`.
    """

    __slots__ = _fields = ("frame", "close", "first", "kind")


class Inbound:
    """The server's side of one connection's protocol, without I/O.

    `receive(line)` takes each line read (its newline stripped) in order and gives the
    `Message` to hand the handler, the `Reply` to send instead, or None for a blank line.
    With `hello` given, the connection's first request must be `rpc.hello`: anything else
    is answered -32002 and closes. Without it, `rpc.hello` is -32601 like every other
    `rpc.` method. `answer` and `fail` make the line for a handler's result or exception.
    """

    def __init__(self, hello: Hello | None = None):
        self.hello = hello
        self.agreed: Agreed | None = None
        self._seen = False

    def receive(self, line: bytes) -> Message | Reply | None:
        if not line.strip():
            return None
        first, self._seen = not self._seen, True
        try:
            obj = _loads(line)
        except ValueError:
            return self._refuse(None, PARSE_ERROR, "parse error: not JSON", first, "json")
        if isinstance(obj, list):
            return self._refuse(None, INVALID_REQUEST, "batches are not supported", first,
                                "batch")
        if not isinstance(obj, dict):
            return self._refuse(None, INVALID_REQUEST, "a message is a JSON object", first,
                                "value")
        id = obj.get("id")
        if not _id_ok(id):
            return self._refuse(None, INVALID_REQUEST, "an id is a string, an integer or "
                                "null", first, "request")
        why = _invalid(obj)
        if why:
            return self._refuse(id, INVALID_REQUEST, why, first, "request")
        note = "id" not in obj
        method = obj["method"]
        if self.hello is not None and self.agreed is None:
            if method != HELLO or note:
                return self._refuse(id, HELLO_REQUIRED, f"send {HELLO} first", first,
                                    "hello", close=True)
            try:
                self.agreed = hello(obj.get("params"), self.hello)
            except RpcError as exc:
                return Reply(encode(error(id, exc)), False, first, "hello")
            return Reply(encode(result(id, hello_result(self.agreed, self.hello))), False,
                         first, "hello")
        if method.startswith("rpc."):
            if note:
                return Reply(None, False, first, "reserved")
            if method == HELLO and self.hello is not None:
                return self._refuse(id, INVALID_REQUEST, "the hello is already agreed",
                                    first, "reserved")
            return self._refuse(id, METHOD_NOT_FOUND, f"method not found: {method}", first,
                                "reserved")
        return Message(None if note else id, method, obj.get("params"), note)

    def answer(self, message: Message, value: object) -> bytes | None:
        """The line answering `message` with `value`; None for a notification. A value
        JSON can't carry is answered -32603."""
        if message.notification:
            return None
        try:
            return encode(result(message.id, value))
        except (TypeError, ValueError):
            return self._internal(message)

    def fail(self, message: Message, exc: BaseException) -> bytes | None:
        """The line answering `message` when the handler raised `exc`: an `RpcError`
        answers with itself, anything else with -32603 and no detail (the server logs
        it). None for a notification."""
        if message.notification:
            return None
        if isinstance(exc, RpcError):
            try:
                return encode(error(message.id, exc))
            except (TypeError, ValueError):
                pass
        return self._internal(message)

    def _internal(self, message: Message) -> bytes:
        return encode(error(message.id, RpcError(INTERNAL_ERROR, "internal error")))

    def _refuse(self, id, code: int, text: str, first: bool, kind: str, *,
                close: bool = False) -> Reply:
        return Reply(encode(error(id, RpcError(code, text))), close, first, kind)


def _invalid(obj: dict) -> str | None:
    """Why `obj` isn't a JSON-RPC 2.0 request or notification, or None."""
    if obj.get("jsonrpc") != "2.0":
        return 'a request carries "jsonrpc": "2.0"'
    if not isinstance(obj.get("method"), str):
        return "a request names its method as a string"
    if "params" in obj and not isinstance(obj["params"], (dict, list)):
        return "params is an object or an array"
    return None
