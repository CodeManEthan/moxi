"""The token index's request keys: one signed 64-bit integer per request.

A key is the first 8 bytes of BLAKE2b (`digest_size=8`) over `<harness>:<request_id>`,
read big-endian as a signed 64-bit integer, so it fits SQLite's INTEGER. Two copies of
one request, in two files or two lanes, get the same key, and the claim keeps one.

Prefixing the harness keeps a Codex key from ever meeting a Claude key.

Some request ids name a place in one file rather than the request itself: sessions
falls back on Claude's `line:<n>` and Codex's `tc:<thread>:<line>` when the harness wrote
no id. The same such id turns up in unrelated files, so it is qualified by the file's
lane and path before digesting, and two files can never claim each other's requests
through it. `path` is the text the index stores in `files.path`.
"""

from __future__ import annotations

import hashlib

#: The prefixes of the request ids that only mean something inside one file.
FILE_LOCAL = ("line:", "tc:")


def is_file_local(request_id: str) -> bool:
    """Whether `request_id` names a place in one file rather than the request itself."""
    return request_id.startswith(FILE_LOCAL)


def namespaced(harness: str, request_id: str, *, lane: str, path: str) -> str:
    """The text a key digests: `<harness>:<request_id>`, a file-local id followed by its
    lane and path.

    The parts are joined with NUL, which no lane, path or id holds, so two different
    (lane, path) pairs can never run together into the same text.
    """
    if is_file_local(request_id):
        return f"{harness}:{request_id}\0{lane}\0{path}"
    return f"{harness}:{request_id}"


def digest(text: str) -> int:
    """BLAKE2b-8 of `text`, read big-endian as a signed 64-bit integer."""
    raw = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(raw, "big", signed=True)


def request_key(harness: str, request_id: str, *, lane: str, path: str) -> int:
    """The index key of one request read from the file at `path` in `lane`."""
    return digest(namespaced(harness, request_id, lane=lane, path=path))


__all__ = ["FILE_LOCAL", "is_file_local", "namespaced", "digest", "request_key"]
