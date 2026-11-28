"""The frames a lane's session carries, both ways, and the version of its request.

A lane's session (`moxi rounds serve` on the far machine, reached over ssh) carries
messages and raw bytes on one pipe each way: the request, the bundle and the brought
private files from the near side; the far lock's answer, the refs it holds, the
command's output and the result from the far side. So every part is a frame: one
header line, `<kind> <length>\\n` (the kind an ASCII word, the length the payload's
size in bytes, in decimal), then exactly that many bytes. Neither side reads past a
part's end, and a command's output can never be read as a message. A frame whose
payload is a message holds it as JSON (`send_json`, `as_json`).

`VERSION` is the request's version. A far side refuses a request of a version it does
not speak, naming both.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable

#: The version of the request a near side sends and a far side speaks.
VERSION = 1
#: The longest header line a frame may have, in bytes, its newline included.
HEADER_MAX_BYTES = 64
#: How much of a payload is read or copied at a time, in bytes.
CHUNK_BYTES = 64 * 1024

# Near side to far side.
#: The request: what to run, for which build, and how.
REQUEST = "request"
#: The bundle of the commits the far side lacks; empty when it lacks none.
BUNDLE = "bundle"
#: One brought private file: a JSON line (`path`, `sha256`), a newline, then its bytes.
FILE = "file"
#: No more brought private files.
FILES_END = "files-end"
#: The probe's payload, which the far side hashes as it read it.
PROBE = "probe"

# Far side to near side.
#: A line the far side says while it waits for its lock.
WAIT = "wait"
#: The far lock gave the run a place (`check_lock`).
PLACED = "placed"
#: No place, or the shared lock, could be taken now, and the request asked not to wait.
BUSY = "busy"
#: The request was refused before anything ran (`reason`, `fix`).
REFUSED = "refused"
#: The commits the far side holds for the sender and project (`refs`).
REFS = "refs"
#: The run can't start: a brought file or a preflight line failed (`reason`).
NOT_READY = "not-ready"
#: The command has started.
STARTED = "started"
#: A piece of the command's output, raw bytes.
OUT = "out"
#: The run's result, the last message of a run.
RESULT = "result"
#: The probe's answer.
PROBED = "probed"

#: Every kind, each a word of ASCII letters and hyphens.
KINDS = (REQUEST, BUNDLE, FILE, FILES_END, PROBE, WAIT, PLACED, BUSY, REFUSED, REFS,
         NOT_READY, STARTED, OUT, RESULT, PROBED)


class FrameError(Exception):
    """A frame that can't be read: a header that is not one, or the pipe's end inside a
    frame."""


class Reader:
    """Frames read from `read(n)`, a callable that gives up to `n` bytes and `b""` at
    the pipe's end."""

    def __init__(self, read: Callable[[int], bytes]):
        self._read = read

    def header(self) -> tuple[str, int] | None:
        """The next frame's kind and length; None at the pipe's end before a header.
        Raises `FrameError` for a header that is not one."""
        line = b""
        while not line.endswith(b"\n"):
            one = self._read(1)
            if not one:
                if line:
                    raise FrameError(f"the pipe ended inside a header: {line!r}")
                return None
            line += one
            if len(line) > HEADER_MAX_BYTES:
                raise FrameError(f"a header longer than {HEADER_MAX_BYTES} bytes: "
                                 f"{line[:HEADER_MAX_BYTES]!r}")
        parts = line[:-1].split(b" ")
        if (len(parts) != 2 or not parts[1].isdigit()
                or parts[0].decode("ascii", "replace") not in KINDS):
            raise FrameError(f"not a frame's header: {line!r}")
        return parts[0].decode("ascii"), int(parts[1])

    def payload(self, length: int) -> bytes:
        """The next `length` bytes, the payload of the header just read."""
        found = bytearray()
        while len(found) < length:
            one = self._read(min(CHUNK_BYTES, length - len(found)))
            if not one:
                raise FrameError(f"the pipe ended {length - len(found)} bytes short of a "
                                 f"payload of {length}")
            found += one
        return bytes(found)

    def frame(self) -> tuple[str, bytes] | None:
        """The next frame whole: its kind and payload; None at the pipe's end before a
        header."""
        found = self.header()
        if found is None:
            return None
        kind, length = found
        return kind, self.payload(length)


class Writer:
    """Frames written by `write(data)`, a callable that writes all of `data` or raises
    `OSError`. Writes from several threads are kept whole by a lock. With `drop`, a
    write that fails is dropped, and every later one too (`broken` says so); without
    it, the `OSError` is raised."""

    def __init__(self, write: Callable[[bytes], None], *, drop: bool = False):
        self._write = write
        self._drop = drop
        self._lock = threading.Lock()
        self.broken = False

    def send(self, kind: str, payload: bytes = b"") -> None:
        """Write one frame of `kind` holding `payload`."""
        if kind not in KINDS:
            raise ValueError(f"{kind!r} is not a frame's kind")
        data = f"{kind} {len(payload)}\n".encode("ascii") + payload
        with self._lock:
            if self.broken:
                return
            try:
                self._write(data)
            except OSError:
                if not self._drop:
                    raise
                self.broken = True

    def send_json(self, kind: str, value) -> None:
        """Write one frame of `kind` holding `value` as JSON."""
        self.send(kind, json.dumps(value).encode("utf-8"))


def as_json(payload: bytes):
    """A message's payload, read as JSON. Raises `FrameError` when it is not JSON."""
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise FrameError(f"a message that is not JSON: {exc}") from None


def file_payload(meta: dict, data: bytes) -> bytes:
    """A brought file's frame payload: `meta` as one JSON line, then `data`."""
    return json.dumps(meta).encode("utf-8") + b"\n" + data


def read_file_payload(payload: bytes) -> tuple[dict, bytes]:
    """A brought file's frame payload as its JSON line and its bytes. Raises
    `FrameError` when it holds no such line."""
    line, newline, data = payload.partition(b"\n")
    meta = as_json(line) if newline else None
    if not isinstance(meta, dict):
        raise FrameError("a file frame with no JSON line before its bytes")
    return meta, data


__all__ = ["VERSION", "HEADER_MAX_BYTES", "CHUNK_BYTES", "REQUEST", "BUNDLE", "FILE",
           "FILES_END", "PROBE", "WAIT", "PLACED", "BUSY", "REFUSED", "REFS", "NOT_READY",
           "STARTED", "OUT", "RESULT", "PROBED", "KINDS", "FrameError", "Reader", "Writer",
           "as_json", "file_payload", "read_file_payload"]
