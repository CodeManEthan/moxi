"""The path rules notes' tools in the Claude apps keep before they read a file: one copy,
which `list_notes`, `read_note` and `search_notes` all judge with.

The root is notes' own setting, `moxi_notes.api.root()`, read and resolved once per call
(`notes_root`). A path the chat gives is judged in this order, and the first rule it fails
names the refusal:

1. **Relative.** Empty (where a file is asked for), absolute, starting with `~`, holding a
   NUL or a backslash, or with a `..` part: `bad-path`. Empty parts are dropped, so
   `a//b/` is `a/b`.
2. **Blocked parts.** A part that starts with `.` (where tools keep their config and
   credentials), a path `moxi_secrets.is_secret_path` names, or a path the chat redaction
   would change: `blocked`. A path goes back to the chat as it is, so one the redaction
   would change is never offered or read.
3. **Links followed, then judged.** The path is resolved with every link followed. It must
   lie under the resolved root (`outside-root`), and rule 2 runs again on its part below
   the root, so both spellings are judged.
4. **Plain files.** A regular file (`not-a-file` for a folder, FIFO, socket or device),
   with one hard link: a second link could be a secret file from elsewhere linked in,
   which no path check sees (`blocked`).
5. **Size and kind.** At most the tool's cap (`too-large`). A NUL in the first 8 KiB, or
   first bytes of `%PDF-` (a PDF), mean `not-text`: only text is read, since
   secrets can't read anything else.
6. **The file read is the file judged.** It is opened with `O_NOFOLLOW` and `O_NONBLOCK`,
   and `fstat` must show the device and inode judged, still a regular file with one link
   (`changed` otherwise). This narrows a swap between the check and the read; a process
   of the same user can read the file directly anyway.

A folder (`list_notes`) is judged by rules 1 to 3, and its entries by rules 2 to 4
(`entry`), so a list never offers a path the read would refuse. The search's walk
(`walk`) never follows a link and keeps rules 2 (dot parts and secret paths), 4, 5 and 6
for every file it opens; the redaction rule of 2 is checked on a hit before it is shown.

A judged path that fails raises `Refused` with its code and a text written to be read
aloud. A chat redaction that can't run raises `RedactionOff`.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass

#: How many leading bytes are checked for a NUL.
SNIFF_BYTES = 8192
#: What a PDF opens with; it is refused like any file that isn't text.
PDF_MAGIC = b"%PDF-"
#: Folder names the search's walk never enters, besides every dot name.
WALK_SKIPS = frozenset({"node_modules", "__pycache__"})
#: What the chat redaction returns for a text secrets couldn't scan.
UNSCANNED = "[REDACTED:unscanned]"

#: Each refusal's text. None names the path it refuses, which may be what was kept out.
TEXTS = {
    "bad-path": ("That path can't be read. Give a path inside the notes folder, relative "
                 "to it, with no .. part."),
    "blocked": ("That path is kept out of the chat: a dot folder, a file that holds "
                "secrets, a name that looks like a secret, or a file with a second link."),
    "outside-root": "That path leads outside the notes folder, so it isn't read.",
    "not-found": "There is nothing at that path in the notes folder.",
    "not-a-file": "That path isn't a plain file, so it isn't read.",
    "not-a-folder": "That path isn't a folder.",
    "not-text": "That file isn't text, so it isn't read. Only text files are.",
    "too-large": "That file is over {cap_mib} MiB, so it isn't read.",
    "changed": "The file changed while it was being read. Ask again.",
    "no-root": ("The notes folder isn't there, so nothing was read. moxi doctor "
                "connector says why."),
}


class Refused(Exception):
    """A path a rule refuses: `code` and `text`, the refusal's code word and its text.
    `rel` is the path as given once rule 2 has let it through (so it names no secret),
    else None."""

    def __init__(self, code: str, text: str | None = None, *, rel: str | None = None):
        super().__init__(code)
        self.code = code
        self.text = text if text is not None else TEXTS[code]
        self.rel = rel


class RedactionOff(Exception):
    """The chat redaction can't run, so nothing may be read."""


def chat_redact(text: str) -> str:
    """`moxi_secrets.chat_redact(text)`, the one redaction notes' tools use. Raises
    `RedactionOff` when secrets won't load or a chat set can't run."""
    try:
        import moxi_secrets

        return moxi_secrets.chat_redact(text)
    except Exception as exc:  # noqa: BLE001 - any failure of secrets keeps the read closed
        raise RedactionOff(f"{type(exc).__name__}: {exc}") from exc


def _is_secret_path(rel: str) -> bool:
    try:
        import moxi_secrets

        return moxi_secrets.is_secret_path(rel)
    except Exception as exc:  # noqa: BLE001 - as above
        raise RedactionOff(f"{type(exc).__name__}: {exc}") from exc


# -- the root ------------------------------------------------------------------------------

def notes_root() -> str:
    """notes' root, every link resolved, read from notes' config now. `Refused`
    (`no-root`) when the config can't be read or the root isn't a folder."""
    from .. import api

    try:
        root_dir = os.path.realpath(api.root())
    except Exception:  # noqa: BLE001 - a config that can't be read: the status row says why
        raise Refused("no-root") from None
    if not os.path.isdir(root_dir):
        raise Refused("no-root")
    return root_dir


def root_problem() -> str | None:
    """Why the notes root can't be read, or None when it can: the status row's text."""
    from .. import api

    try:
        given = api.root()
    except Exception as exc:  # noqa: BLE001 - shown, never raised
        return f"notes' config can't be read: {exc}"
    if not os.path.exists(given):
        return f"{given} is missing"
    if not os.path.isdir(given):
        return f"{given} isn't a folder"
    return None


# -- rules 1 and 2 -------------------------------------------------------------------------

def parts_of(path: str, *, allow_empty: bool) -> tuple[str, ...]:
    """Rule 1: `path`'s parts, or `Refused` (`bad-path`)."""
    if not isinstance(path, str):
        raise Refused("bad-path")
    if path.startswith(("/", "~")) or "\x00" in path or "\\" in path:
        raise Refused("bad-path")
    parts = tuple(p for p in path.split("/") if p)
    if ".." in parts or (not parts and not allow_empty):
        raise Refused("bad-path")
    return parts


def redaction_changes(rel: str) -> bool:
    """Whether the chat redaction would change the path `rel`. Raises `RedactionOff`."""
    return chat_redact(rel) != rel


def blocked(parts: tuple[str, ...]) -> bool:
    """Rule 2: whether `parts` hold a dot part, name a secret path, or are a path the chat
    redaction would change. Raises `RedactionOff`."""
    if not parts:
        return False
    if any(p.startswith(".") for p in parts):
        return True
    rel = "/".join(parts)
    return _is_secret_path(rel) or redaction_changes(rel)


def _check(parts: tuple[str, ...]) -> None:
    if blocked(parts):
        raise Refused("blocked")


# -- rule 3 --------------------------------------------------------------------------------

def resolve(root_dir: str, parts: tuple[str, ...]) -> str:
    """Rule 3: the path `parts` name under `root_dir`, every link followed. `Refused`
    when it leads outside the root or its spelling below the root is blocked."""
    resolved = os.path.realpath(os.path.join(root_dir, *parts))
    if resolved != root_dir and not resolved.startswith(root_dir.rstrip(os.sep) + os.sep):
        raise Refused("outside-root")
    below = tuple(p for p in os.path.relpath(resolved, root_dir).split(os.sep)
                  if p and p != ".")
    if below != parts:
        _check(below)
    return resolved


def _stat(path: str) -> os.stat_result:
    try:
        return os.stat(path)
    except OSError:
        raise Refused("not-found") from None


# -- a file --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Judged:
    """A file rules 1 to 5 let through: `rel`, the path as the chat gave it (empty parts
    dropped); `resolved`, where it leads; `st`, what `stat` saw."""

    rel: str
    resolved: str
    st: os.stat_result


def _plain(st: os.stat_result, cap_bytes: int) -> None:
    """Rules 4 and 5's size: a regular file of one link, at most `cap_bytes`."""
    if not stat.S_ISREG(st.st_mode):
        raise Refused("not-a-file")
    if st.st_nlink != 1:
        raise Refused("blocked")
    if st.st_size > cap_bytes:
        raise Refused("too-large", TEXTS["too-large"].format(cap_mib=cap_bytes >> 20))


def judge_file(root_dir: str, path: str, *, cap_bytes: int) -> Judged:
    """Rules 1 to 5 (but the bytes) for the file `path` under `root_dir`."""
    parts = parts_of(path, allow_empty=False)
    _check(parts)
    rel = "/".join(parts)
    try:
        resolved = resolve(root_dir, parts)
        st = _stat(resolved)
        _plain(st, cap_bytes)
    except Refused as exc:
        exc.rel = rel
        raise
    return Judged(rel, resolved, st)


def not_text(head: bytes) -> bool:
    """Rule 5's kind: whether a file whose first bytes are `head` isn't text."""
    return b"\x00" in head[:SNIFF_BYTES] or head.startswith(PDF_MAGIC)


def read_judged(resolved: str, st: os.stat_result, *, cap_bytes: int) -> bytes:
    """Rules 6 and 5 for a file judged as `st`: its bytes, read through a descriptor
    opened without following a link and checked against `st`."""
    try:
        fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        raise Refused("changed") from None
    try:
        now = os.fstat(fd)
        if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino) \
                or not stat.S_ISREG(now.st_mode) or now.st_nlink != 1:
            raise Refused("changed")
        chunks, size = [], 0
        while size <= cap_bytes:
            chunk = os.read(fd, min(1 << 20, cap_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > cap_bytes:
        raise Refused("too-large", TEXTS["too-large"].format(cap_mib=cap_bytes >> 20))
    if not_text(data):
        raise Refused("not-text")
    return data


def decode(data: bytes) -> str:
    """A file's bytes as text: UTF-8, a bad byte read as a replacement character."""
    return data.decode("utf-8", errors="replace")


# -- a folder ------------------------------------------------------------------------------

def judge_dir(root_dir: str, path: str) -> tuple[str, str]:
    """Rules 1 to 3 for the folder `path` (empty for the root): `(rel, resolved)`."""
    parts = parts_of(path, allow_empty=True)
    _check(parts)
    resolved = resolve(root_dir, parts)
    if not stat.S_ISDIR(_stat(resolved).st_mode):
        raise Refused("not-a-folder")
    return "/".join(parts), resolved


@dataclass(frozen=True)
class Entry:
    """One entry a list may offer: `name`, `kind` (`folder` or `file`), `size_bytes`
    (None for a folder) and `mtime`, seconds since the epoch."""

    name: str
    kind: str
    size_bytes: int | None
    mtime: float


def entry(root_dir: str, rel: str, dir_entry: os.DirEntry) -> Entry | None:
    """The entry `dir_entry` of the folder `rel`, or None when rules 2 to 4 would refuse
    it: a dot name, a secret path, a name the redaction would change, a link leading out
    of the root or to a blocked path, or anything not a folder or a one-link file."""
    parts = tuple(p for p in rel.split("/") if p) + (dir_entry.name,)
    try:
        if blocked(parts):
            return None
        st = os.stat(resolve(root_dir, parts))
    except (Refused, OSError):
        return None
    if stat.S_ISDIR(st.st_mode):
        return Entry(dir_entry.name, "folder", None, st.st_mtime)
    if stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
        return Entry(dir_entry.name, "file", st.st_size, st.st_mtime)
    return None


# -- the search's walk ---------------------------------------------------------------------

@dataclass(frozen=True)
class Walked:
    """A file the walk may open: `rel` below the root, its absolute `path`, and `st`."""

    rel: str
    path: str
    st: os.stat_result


def walk(root_dir: str, *, cap_bytes: int) -> Iterator[Walked]:
    """Every file under `root_dir` the search may open, in name order, each folder's files
    before its subfolders: never through a link, never into a dot name, `node_modules`
    or `__pycache__`, and only regular files of one link, at most `cap_bytes`, whose path
    `moxi_secrets.is_secret_path` doesn't name. The bytes (rules 5 and 6) are judged by
    `read_judged` when the file is read."""
    stack: list[tuple[str, str]] = [("", root_dir)]
    while stack:
        rel_dir, abs_dir = stack.pop()
        try:
            with os.scandir(abs_dir) as found:
                entries = sorted(found, key=lambda e: e.name)
        except OSError:
            continue
        sub_dirs = []
        for item in entries:
            if item.name.startswith(".") or item.is_symlink():
                continue
            rel = f"{rel_dir}/{item.name}" if rel_dir else item.name
            try:
                if item.is_dir(follow_symlinks=False):
                    if item.name not in WALK_SKIPS:
                        sub_dirs.append((rel, item.path))
                    continue
                st = item.stat(follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > cap_bytes:
                continue
            if _is_secret_path(rel):
                continue
            yield Walked(rel, item.path, st)
        stack.extend(reversed(sub_dirs))


__all__ = ["Entry", "Judged", "PDF_MAGIC", "RedactionOff", "Refused", "SNIFF_BYTES",
           "TEXTS", "UNSCANNED", "WALK_SKIPS", "Walked", "blocked", "chat_redact",
           "decode", "entry", "judge_file", "judge_dir", "not_text", "notes_root",
           "parts_of", "read_judged", "redaction_changes", "resolve", "root_problem",
           "walk"]
