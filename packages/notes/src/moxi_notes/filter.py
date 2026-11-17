"""The clean filter that puts a redacted copy in the index under `action = redact`.

git starts it once per command as `filter.moxi-notes.process`
(`'<python>' -I -m moxi_notes.filter`), speaks git's long-running filter protocol,
version 2, and asks for `clean` only. At the handshake the filter finds the work tree's
top and common git dir with one `git rev-parse`, and reads notes' config and both
allowlists once. Then, for each file git hands it, it answers with the text `redact` gives
for notes' sets at the file's path. The file on disk keeps the original.

It redacts exactly what the gates refuse in a text blob, and `redact` reaches a fixed
point, so a redacted blob passes the prepare step and both gates. What it passes through
unchanged, and so is still left out or refused by them: a blob with a NUL byte in its
first 8,000 bytes (git's own binary test), and a blob over `max_mib`.

It never answers with one placeholder in place of a whole text. git writes the committed
copy back over the file at the next checkout, reset, stash, merge, rebase or pull, so a
whole-text placeholder would destroy the note. When `redact` gives exactly one
`[REDACTED:<rule>]` for a text that wasn't already that (a text the fallback engine
couldn't scan in time, a rule that matches the path alone, or `redact`'s own last
resort), the filter answers `status=error` for that file: git keeps its content, and the
prepare step leaves it out. A file whose whole text, with no line end, is one secret is
left out the same way. A placeholder that replaces one line of a longer text is redaction and stays.

A failure costs only the files it failed on: setup writes the driver with
`required = false`. A file the filter fails on is answered `status=error` and git keeps
it as it is on disk. When the allowlist can't be read, a set can't run or the git call
fails, it answers `status=abort` and git keeps every file as it is on disk for the rest
of the command: redacting against an allowlist it can't read would write over text the
user meant to keep. It writes nothing on stderr while it redacts; on an abort it writes
one line.

`install(root)` and `remove(root)` write and take away the driver and the attribute
(`* filter=moxi-notes` in the common git dir's `info/attributes`, never a tracked
`.gitattributes`).
"""

from __future__ import annotations

import os
import re
import sys

#: The filter driver's name in git config and in the attribute.
DRIVER = "moxi-notes"
ATTRIBUTE = f"* filter={DRIVER}"
#: git's binary test reads this many bytes for a NUL.
BINARY_PROBE = 8000
#: Largest data in one pkt-line.
MAX_DATA = 65516
_WHOLE = re.compile(r"\[REDACTED:[^\[\]]*\]")


class ProtocolError(Exception):
    """git spoke something the filter can't read; the process ends."""


# -- pkt-lines ----------------------------------------------------------------------------

def _read_exact(stream, n: int) -> bytes:
    out = b""
    while len(out) < n:
        part = stream.read(n - len(out))
        if not part:
            raise ProtocolError("git closed the pipe inside a packet")
        out += part
    return out


def read_packet(stream) -> bytes | None:
    """One packet's data, or None for a flush. `EOFError` when git has closed the pipe
    between packets."""
    head = stream.read(4)
    if not head:
        raise EOFError
    if len(head) < 4:
        head += _read_exact(stream, 4 - len(head))
    try:
        size = int(head, 16)
    except ValueError:
        raise ProtocolError(f"a packet length {head!r} is not hex") from None
    if size == 0:
        return None
    if size < 4:
        raise ProtocolError(f"a packet of length {size}")
    return _read_exact(stream, size - 4)


def read_list(stream) -> list[str]:
    """Text packets up to a flush, each without its newline."""
    out = []
    while True:
        data = read_packet(stream)
        if data is None:
            return out
        out.append(data.decode("utf-8", "surrogateescape").rstrip("\n"))


def read_content(stream) -> bytes:
    """Content packets up to a flush."""
    parts = []
    while True:
        data = read_packet(stream)
        if data is None:
            return b"".join(parts)
        parts.append(data)


def write_packet(out, data: bytes) -> None:
    out.write(b"%04x" % (len(data) + 4) + data)


def write_flush(out) -> None:
    out.write(b"0000")


def write_list(out, lines) -> None:
    for line in lines:
        write_packet(out, line.encode("utf-8", "surrogateescape") + b"\n")
    write_flush(out)


def write_content(out, data: bytes) -> None:
    for at in range(0, len(data), MAX_DATA):
        write_packet(out, data[at:at + MAX_DATA])
    write_flush(out)


# -- what the filter knows for one git command --------------------------------------------

class State:
    """Read once at the handshake: the allowlist, the cap, whether to redact, and why
    the filter aborts (None while it can work)."""

    def __init__(self, *, allow=None, cap: int = 0, redact: bool = True,
                 abort: str | None = None):
        self.allow = allow
        self.cap = cap
        self.redact = redact
        self.abort = abort


def load_state() -> State:
    """The state for this git command, from the repo the filter was started in."""
    from moxi.config import ConfigError
    from moxi_repo.api import git

    from . import blobs, judge

    try:
        settings = judge.config()
    except ConfigError as exc:
        return State(abort=f"notes' config can't be read: {exc}")
    try:
        out = blobs.run(lambda *a, **kw: git(".", *a, clean_env=False, **kw),
                        "rev-parse", "--path-format=absolute", "--show-toplevel",
                        "--git-common-dir")
    except blobs.GitError as exc:
        return State(abort=f"the repo could not be found: {exc}")
    lines = os.fsdecode(out).splitlines()
    if len(lines) < 2:
        return State(abort="the repo could not be found: git rev-parse gave no top")
    allow, problem = judge.allowlist(lines[0], lines[1])
    if problem:
        return State(abort=f"the allowlist can't be read: {problem}")
    return State(allow=allow, cap=judge.max_bytes(settings),
                 redact=settings["action"] == "redact")


def clean(data: bytes, path: str, state: State) -> tuple[str, bytes | None]:
    """What the filter answers for one file: ("success", content), ("error", None) or
    ("abort", None)."""
    if state.abort is not None:
        return "abort", None
    if not state.redact or len(data) > state.cap or b"\0" in data[:BINARY_PROBE]:
        return "success", data
    from moxi_secrets import SetUnavailable, redact

    from . import blobs, judge

    text = blobs.text(data)
    try:
        result = redact(text, judge.SETS, path=path, allow=state.allow)
    except SetUnavailable as exc:
        state.abort = f"a set notes judges with can't run: {exc}"
        return "abort", None
    except Exception:  # noqa: BLE001 - one file's failure costs that file
        return "error", None
    if result == text:
        return "success", data
    if _WHOLE.fullmatch(result):
        return "error", None
    return "success", result.encode("utf-8", "surrogateescape")


# -- the process --------------------------------------------------------------------------

def handshake(stdin, stdout) -> None:
    hello = read_list(stdin)
    if "git-filter-client" not in hello or "version=2" not in hello:
        raise ProtocolError("git did not offer the filter protocol, version 2")
    write_list(stdout, ["git-filter-server", "version=2"])
    stdout.flush()
    offered = read_list(stdin)
    write_list(stdout, ["capability=clean"] if "capability=clean" in offered else [])
    stdout.flush()


def serve(stdin, stdout, state_of=load_state) -> int:
    """Answer git until it closes the pipe; 0, or 1 on a protocol error."""
    try:
        handshake(stdin, stdout)
        state = None
        while True:
            try:
                request = read_list(stdin)
            except EOFError:
                return 0
            keys = dict(line.partition("=")[::2] for line in request)
            content = read_content(stdin)
            if keys.get("command") != "clean":
                write_list(stdout, ["status=error"])
                stdout.flush()
                continue
            if state is None:
                state = state_of()
                if state.abort is not None:
                    _say(f"the clean filter redacts nothing in this git command: "
                         f"{state.abort}")
            had = state.abort
            status, out = clean(content, keys.get("pathname", ""), state)
            if status == "abort" and had is None:
                _say(f"the clean filter redacts nothing more in this git command: "
                     f"{state.abort}")
            write_list(stdout, [f"status={status}"])
            if status == "success":
                write_content(stdout, out)
                write_flush(stdout)
            stdout.flush()
    except ProtocolError as exc:
        _say(f"the clean filter stopped: {exc}")
        return 1
    except EOFError:
        return 0


def _say(line: str) -> None:
    sys.stderr.write(f"notes: {line}\n")
    sys.stderr.flush()


def main() -> int:
    return serve(sys.stdin.buffer, sys.stdout.buffer)


# -- the driver and the attribute ---------------------------------------------------------

def process_command() -> str:
    """`'<python>' -I -m moxi_notes.filter`, the path with forward slashes and quoted for
    the shell git runs it with."""
    from moxi import paths

    python = os.fspath(paths.python()).replace("\\", "/")
    return "'" + python.replace("'", "'\\''") + "' -I -m moxi_notes.filter"


def _attributes(root) -> str:
    from moxi_repo.api import git

    from . import blobs

    out = blobs.run(lambda *a, **kw: git(root, *a, **kw), "rev-parse",
                    "--path-format=absolute", "--git-common-dir")
    return os.path.join(os.fsdecode(out).strip(), "info", "attributes")


def install(root) -> None:
    """Write the driver (`process`, and `required = false`) in the repo's config and the
    attribute in its common git dir's `info/attributes`. Raises `blobs.GitError`."""
    from moxi import files
    from moxi_repo.api import git

    from . import blobs

    run = lambda *a, **kw: git(root, *a, **kw)  # noqa: E731
    blobs.run(run, "config", f"filter.{DRIVER}.process", process_command())
    blobs.run(run, "config", f"filter.{DRIVER}.required", "false")
    path = _attributes(root)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    if ATTRIBUTE not in text.splitlines():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lead = "" if not text or text.endswith("\n") else "\n"
        files.atomic_write(path, (text + lead + ATTRIBUTE + "\n").encode("utf-8"))


def remove(root) -> None:
    """Take the attribute line and the driver away. Raises `blobs.GitError`."""
    from moxi import files
    from moxi_repo.api import git

    from . import blobs

    path = _attributes(root)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = None
    if text is not None and ATTRIBUTE in text.splitlines():
        kept = [line for line in text.splitlines() if line != ATTRIBUTE]
        files.atomic_write(path, ("\n".join(kept) + "\n" if kept else "").encode("utf-8"))
    done = git(root, "config", "--remove-section", f"filter.{DRIVER}")
    # 128: no such section, which is the state wanted.
    if done.returncode not in (0, 128):
        raise blobs.GitError(f"git config --remove-section exited {done.returncode}")


if __name__ == "__main__":
    sys.exit(main())
