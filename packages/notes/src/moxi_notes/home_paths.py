"""`moxi notes home-paths`: write paths under the home folder as `~`, and session paths as
their session ids, across the notes tree. A dry run unless `--apply`.

`H` is the home folder's absolute path, taken from the OS; the login is its last part. The
verb reads the snapshot set (the tracked and untracked files under the notes root, ignored
files and nested repos aside) and makes one pass over each file: Rule SI first, then Rules
HP and HQ on what SI left, and Rule L on each symbolic link. It works on bytes, so every
byte outside a change stays as it was (Rule E): encoding, line endings and a missing final
newline included.

A *token start* is the start of a line, or a position after whitespace or one of
`" ' ` ( [ < = , : > |` (`START`). A character *continues a name* when it is a letter, a
digit, `_`, `-`, or a `.` followed by one of those.

- **Rule SI.** A session path becomes the part of it that starts at its session id. A
  session path is, at a token start, an optional root, then a Claude Code project folder
  name for a folder under the home folder, then `/`, then a full session id (8-4-4-4-12 hex
  digits) whose next character is not a letter or digit. The root is `/tmp/claude-<digits>/`,
  or a run that begins with `~`, `/` or `H`, holds no `START` character, `)` or `]` after
  that, and ends `/.claude/projects/`, or `.claude/projects/` alone. On Windows `\\` divides
  a session path as `/` does (`H` there starts with a drive). The folder name is `H` with every
  character other than an ASCII letter or digit turned into `-`, alone or followed by `-`
  and a run of letters, digits and `-`. The root and the folder name with its `/` are
  removed, and so is a `.jsonl` right after the id when the character after it does not
  continue a name. SI only removes text, so it writes neither `H` nor the login.
- **Rule HP.** An occurrence of `H` at a token start whose next character does not continue
  a name becomes `~`.
- **Rule HQ.** In a file that is not `.json` or `.jsonl`, an occurrence HP would change
  stays as written when the character before it is `"`, `'`, or a `=` that is not a shell
  assignment's, and the file is listed `quoted`. A `=` is a shell assignment's when a shell
  variable name sits right before it, starts a token, and has no quote before it.
- **Rule L.** A link whose target starts with `H` and lies under the notes root becomes a
  relative link to the same target. One whose target lies outside the root is listed
  `link-outside` and left.
- **Rule B.** Only regular files no larger than `max_mib` are read. A file with a NUL byte
  in its first 8 KiB is binary: listed `binary`, not read. One over `max_mib` is listed
  `too-large`.

`--apply` (Rule U) changes only files git tracks that match the index and `HEAD`: when any
file it would change is untracked or has uncommitted changes, it refuses the whole run,
changes nothing, and lists each such file `untracked` or `modified`. Its writes follow
backfill's: regular files under the root only, `lstat` first, read through a descriptor
opened with `O_NOFOLLOW`, replaced by a rename that keeps the mode; a read-only file is
left alone and listed `read-only`. Rule C: a file whose size, mtime or bytes changed
between the read and the write is skipped and listed `changed`, and the run's last lines
say that change is someone else's. The verb commits nothing; its last line prints the
commit to make. A second run changes nothing.

`plan()` gives a `Pass`; `as_dict()` is `--json`, whose shape `JSON_SHAPE` states.
Standard library only at import.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field

JSON_SHAPE = """\
--json prints one object, the same shape whatever the exit code:
  {"root": str, "home": str,
   "files": [{"path": str, "class": str,
              "changes": [{"line": int, "kind": "home" | "session" | "link",
                           "before": str, "after": str}]}],
   "left": [{"path": str, "reason": str}],
   "counts": {"files": int, "changes": int, "by_class": {str: int},
              "by_kind": {str: int}, "sessions": int, "quoted_left": int},
   "error": str | null}
A change's line is 1-based, 0 for a link. before and after hold about 40 characters
around the change. left holds one entry per file and reason: binary, too-large,
link-outside, untracked, modified, read-only, changed, quoted or login. sessions counts
the distinct session ids Rule SI kept; quoted_left the places Rule HQ left as written.
error names a refused run."""

#: The commit the verb's last line prints.
COMMIT_MESSAGE = "notes: home paths written as ~, session paths as ids"
#: The characters after which a token starts (the start of a line counts too).
START = b" \t\n\r\x0b\x0c\"'`([<=,:>|"
#: The quotes Rule HQ looks for before an occurrence, and before a shell variable name.
QUOTES = b"\"'"
#: The review classes, in the order the dry run shows them.
CLASSES = ("code", "data", "diff", "quote", "prose")
DATA_SUFFIXES = (".json", ".jsonl", ".tsv", ".csv")
DIFF_SUFFIXES = (".diff", ".patch")
JSON_SUFFIXES = (".json", ".jsonl")
#: How much of a file Rule B reads for a NUL byte.
BINARY_PROBE = 8192
#: A login shorter than this is not looked for (reason `login`): it would match almost
#: every file.
SHORT_LOGIN = 3
#: The characters shown on each side of a change.
CONTEXT = 20
#: Why a file is in `left`.
REASONS = ("binary", "too-large", "link-outside", "untracked", "modified", "read-only",
           "changed", "quoted", "login")

O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_BINARY = getattr(os, "O_BINARY", 0)

_NAME_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_VAR_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")
_VAR_FIRST = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_")
_ID = rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


# -- the rules, on bytes ---------------------------------------------------------------------

def _class_of(chars: bytes) -> bytes:
    return b"".join(re.escape(bytes([c])) for c in chars)


_AT_START = rb"(?:(?<=[" + _class_of(START) + rb"])|^)"
_ROOT_RUN = rb"[^" + _class_of(START + b")]") + rb"]"


def _char_is_alnum(data: bytes, i: int) -> bool:
    """Whether the character starting at byte `i` is a letter or digit: ASCII by its byte,
    anything else decoded as UTF-8, else as Latin-1."""
    c = data[i]
    if c < 0x80:
        return chr(c).isalnum()
    for size in (2, 3, 4):
        try:
            return data[i:i + size].decode("utf-8").isalnum()
        except UnicodeDecodeError:
            continue
    return bytes([c]).decode("latin-1").isalnum()


def _name_char(data: bytes, i: int) -> bool:
    return i < len(data) and (data[i] in _NAME_BYTES or (data[i] >= 0x80
                                                        and _char_is_alnum(data, i)))


def continues(data: bytes, i: int) -> bool:
    """Whether the character at byte `i` continues a name: a letter, a digit, `_`, `-`, or
    a `.` followed by one of those."""
    if i >= len(data):
        return False
    if data[i:i + 1] == b".":
        return _name_char(data, i + 1)
    return _name_char(data, i)


def starts_token(data: bytes, i: int, chars: bytes = START) -> bool:
    """Whether a token starts at byte `i`: the start of a line, or after one of `chars`."""
    return i == 0 or data[i - 1] in chars or data[i - 1] == ord("\n")


class Rules:
    """The compiled rules for one home folder `home` (an absolute path). `windows` reads
    session paths by Windows' rules (`\\` divides too); it is this system's by default."""

    def __init__(self, home: str, *, windows: bool = os.name == "nt"):
        self.home = home.rstrip("/") or "/"
        self.h = os.fsencode(self.home)
        self.login = os.path.basename(self.home)
        self.login_bytes = os.fsencode(self.login).lower()
        self.project_name = re.sub(rb"[^A-Za-z0-9]", b"-", self.h)
        self._home_rx = re.compile(_AT_START + re.escape(self.h), re.M)
        sep = rb"[/\\]" if windows else rb"/"
        claude = rb"\.claude" + sep + rb"projects" + sep
        root = (rb"(?:/tmp/claude-[0-9]+/|(?:(?:" + re.escape(self.h) + rb"|[~/])"
                + _ROOT_RUN + rb"*?)?" + sep + claude + rb"|" + claude + rb")")
        self._session_rx = re.compile(
            _AT_START + rb"(?P<root>" + root + rb")?" + re.escape(self.project_name)
            + rb"(?:-[A-Za-z0-9-]*)?" + sep + rb"(?P<id>" + _ID
            + rb")(?![A-Za-z0-9])(?P<jsonl>\.jsonl)?", re.M)

    def home_spans(self, data: bytes) -> list[tuple[int, int]]:
        """Each occurrence of `H` Rule HP would change, as `(start, end)`."""
        if self.h not in data:
            return []
        return [m.span() for m in self._home_rx.finditer(data) if not continues(data, m.end())]

    def session_spans(self, data: bytes) -> list[tuple[int, int, bytes]]:
        """Each session path Rule SI takes, as `(start, end, the id it keeps)`."""
        if self.project_name not in data:
            return []
        out = []
        for m in self._session_rx.finditer(data):
            end = m.end()
            if m.group("jsonl") and continues(data, end):
                end = m.end("id")
            out.append((m.start(), end, m.group("id")))
        return out

    def spells_login(self, data: bytes) -> bool:
        """Whether `data` spells the login in any case (none for a login shorter than
        `SHORT_LOGIN`)."""
        return len(self.login_bytes) >= SHORT_LOGIN and self.login_bytes in data.lower()


def _assignment(data: bytes, eq: int) -> bool:
    """Whether the `=` at byte `eq` is a shell assignment's: a shell variable name sits
    right before it and starts a token, and the character before the name is no quote."""
    j = eq
    while j > 0 and data[j - 1] in _VAR_BYTES:
        j -= 1
    if j == eq or data[j] not in _VAR_FIRST:
        return False
    return starts_token(data, j) and (j == 0 or data[j - 1] not in QUOTES)


def quoted(data: bytes, start: int) -> bool:
    """Rule HQ: whether an occurrence at `start` stays as written."""
    if start == 0:
        return False
    before = data[start - 1]
    if before in QUOTES:
        return True
    return before == ord("=") and not _assignment(data, start - 1)


@dataclass(frozen=True)
class Edit:
    """One change to a file's bytes: `data[start:end]` becomes `new`."""

    start: int
    end: int
    new: bytes
    kind: str


@dataclass
class Rewritten:
    """What one pass over a file's bytes gives."""

    data: bytes
    edits: list = field(default_factory=list)
    sessions: set = field(default_factory=set)
    quoted_left: int = 0


def rewrite(data: bytes, rules: Rules, *, is_json: bool = False) -> Rewritten:
    """One pass of Rules SI, HP and HQ over `data`. `is_json` turns Rule HQ off."""
    found = Rewritten(data=data)
    session = rules.session_spans(data)
    found.sessions = {sid.decode("ascii").lower() for _, _, sid in session}
    found.edits = [Edit(s, e, sid, "session") for s, e, sid in session]
    middle = _apply(data, found.edits)
    # Rule HP reads what SI left; each place maps back to the original bytes by the bytes
    # SI removed before it: `(end of a kept id in the text SI left, removed up to there)`.
    shifts = []
    removed = 0
    for edit in found.edits:
        kept_end = edit.start - removed + len(edit.new)
        removed += (edit.end - edit.start) - len(edit.new)
        shifts.append((kept_end, removed))
    for s, e in rules.home_spans(middle):
        if not is_json and quoted(middle, s):
            found.quoted_left += 1
            continue
        back = _original(s, shifts)
        found.edits.append(Edit(back, back + (e - s), b"~", "home"))
    found.edits.sort(key=lambda edit: edit.start)
    found.data = _apply(data, found.edits)
    return found


def _original(position: int, shifts: list[tuple[int, int]]) -> int:
    """A position in the text SI left, as a position in the original."""
    removed = 0
    for new_end, total in shifts:
        if new_end <= position:
            removed = total
        else:
            break
    return position + removed


def _apply(data: bytes, edits) -> bytes:
    out, at = [], 0
    for edit in sorted(edits, key=lambda x: x.start):
        out.append(data[at:edit.start])
        out.append(edit.new)
        at = edit.end
    out.append(data[at:])
    return b"".join(out)


# -- review ----------------------------------------------------------------------------------

def _show(raw: bytes) -> str:
    return raw.decode("utf-8", "replace").replace("\t", " ")


def _lines(data: bytes) -> list[tuple[int, int]]:
    """Each line's `(start, end)`, its end before the newline."""
    out, at = [], 0
    for m in re.finditer(rb"\n", data):
        out.append((at, m.start()))
        at = m.end()
    out.append((at, len(data)))
    return out


_FENCE = re.compile(rb" {0,3}(```|~~~)")


def classify(path: str, data: bytes, edits) -> tuple[str, list[dict]]:
    """The file's review class and its changes as the JSON gives them."""
    suffix = os.path.splitext(path)[1].lower()
    lines = _lines(data)
    starts = [s for s, _ in lines]
    fenced = []
    inside = None
    for s, e in lines:
        line = data[s:e].rstrip(b"\r")
        m = _FENCE.match(line)
        if m:
            fenced.append(True)
            if inside is None:
                inside = m.group(1)
            elif m.group(1) == inside:
                inside = None
            continue
        fenced.append(inside is not None)
    code = quote = False
    changes = []
    import bisect

    for edit in edits:
        n = bisect.bisect_right(starts, edit.start) - 1
        ls, le = lines[n]
        line = data[ls:le]
        if fenced[n] or data[ls:edit.start].count(b"`") % 2:
            code = True
        if line.lstrip().startswith(b">"):
            quote = True
        lo, hi = max(ls, edit.start - CONTEXT), min(le, edit.end + CONTEXT)
        changes.append({"line": n + 1, "kind": edit.kind,
                        "before": _show(data[lo:hi].rstrip(b"\r")),
                        "after": _show((data[lo:edit.start] + edit.new
                                        + data[edit.end:hi]).rstrip(b"\r"))})
    if suffix in DATA_SUFFIXES:
        cls = "data"
    elif suffix in DIFF_SUFFIXES:
        cls = "diff"
    elif code:
        cls = "code"
    elif quote:
        cls = "quote"
    else:
        cls = "prose"
    return cls, changes


# -- the pass over the tree ------------------------------------------------------------------

@dataclass
class FilePlan:
    """One file the pass changes. A link's `target` and `new_target` are set and `data` is
    empty; a text file's `data` and `new` hold its bytes before and after."""

    path: str
    cls: str
    changes: list
    data: bytes = b""
    new: bytes = b""
    seen: tuple = ()
    target: str | None = None
    new_target: str | None = None
    written: bool = False

    def as_dict(self) -> dict:
        return {"path": self.path, "class": self.cls, "changes": self.changes}


@dataclass
class Pass:
    """What `plan()` found and `apply()` did."""

    root: str
    home: str
    judged: int = 0
    files: list = field(default_factory=list)
    left: list = field(default_factory=list)
    sessions: set = field(default_factory=set)
    quoted_left: int = 0
    error: str | None = None
    applied: bool = False
    refused: bool = False
    would_refuse: list = field(default_factory=list)

    def leave(self, path: str, reason: str) -> None:
        if (path, reason) not in self.left:
            self.left.append((path, reason))

    def counts(self) -> dict:
        by_class = {c: 0 for c in CLASSES}
        by_kind = {"home": 0, "session": 0, "link": 0}
        for f in self.files:
            by_class[f.cls] += 1
            for change in f.changes:
                by_kind[change["kind"]] += 1
        return {"files": len(self.files), "changes": sum(by_kind.values()),
                "by_class": by_class, "by_kind": by_kind, "sessions": len(self.sessions),
                "quoted_left": self.quoted_left}

    def reasons(self) -> dict:
        out = {}
        for _, reason in self.left:
            out[reason] = out.get(reason, 0) + 1
        return {r: out[r] for r in REASONS if r in out}

    def as_dict(self) -> dict:
        return {"root": self.root, "home": self.home,
                "files": [f.as_dict() for f in self.files],
                "left": [{"path": p, "reason": r} for p, r in sorted(self.left)],
                "counts": self.counts(), "error": self.error}


def _home() -> str:
    return os.path.normpath(os.path.expanduser("~"))


def _read(full: str, before) -> bytes | None:
    """The file's bytes through a descriptor that follows no link, or None when it is no
    longer the file `before` described."""
    try:
        fd = os.open(full, os.O_RDONLY | O_NOFOLLOW | O_BINARY)
    except OSError:
        return None
    try:
        now = os.fstat(fd)
        if not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino) != (before.st_dev,
                                                                          before.st_ino):
            return None
        chunks = []
        while chunk := os.read(fd, 1 << 16):
            chunks.append(chunk)
    except OSError:
        return None
    finally:
        os.close(fd)
    return b"".join(chunks)


def _seen(st) -> tuple:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _link(found: Pass, rules: Rules, root: str, path: str, full: str) -> None:
    from moxi.paths import link_target

    try:
        target = link_target(full)
    except OSError:
        return
    if not (target == rules.home or target.startswith(rules.home.rstrip(os.sep) + os.sep)):
        return
    absolute = os.path.normpath(target)
    if not _under(absolute, root):
        found.leave(path, "link-outside")
        return
    relative = os.path.relpath(absolute, os.path.dirname(full))
    cls, _ = classify(path, b"", [])
    found.files.append(FilePlan(
        path=path, cls=cls, target=target, new_target=relative,
        changes=[{"line": 0, "kind": "link", "before": target, "after": relative}]))


def plan(*, root: str | None = None, home: str | None = None,
         max_bytes: int | None = None) -> Pass:
    """The pass over the notes root's snapshot set, computed without writing anything.
    `root` and `max_bytes` default to notes' settings, `home` to the OS's home folder.
    Raises `moxi.config.ConfigError`."""
    from . import blobs, judge
    from .setup import _files, _git, where

    if root is None or max_bytes is None:
        from . import settings as _settings

        places = _settings.read()
        root = str(places.root) if root is None else root
        max_bytes = judge.max_bytes(places.settings) if max_bytes is None else max_bytes
    root = os.path.normpath(os.path.abspath(os.path.expanduser(root)))
    rules = Rules(_home() if home is None else os.path.normpath(home))
    found = Pass(root=root, home=rules.home)
    if not where(root).is_top:
        found.error = (f"the notes root {root} is no git repo's top, so it has no snapshot "
                       "set to read; fix: moxi notes setup")
        return found
    try:
        paths = _files(_git(root))
    except blobs.GitError as exc:
        found.error = f"the notes could not be listed: {exc}"
        return found
    for path in sorted(paths):
        full = os.path.join(root, path)
        try:
            st = os.lstat(full)
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode):
            found.judged += 1
            _link(found, rules, root, path, full)
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        found.judged += 1
        if st.st_size > max_bytes:
            found.leave(path, "too-large")
            continue
        data = _read(full, st)
        if data is None:
            continue
        if b"\0" in data[:BINARY_PROBE]:
            found.leave(path, "binary")
            continue
        _one(found, rules, path, data, _seen(st))
    found.would_refuse = _dirty(root, found)
    return found


def _one(found: Pass, rules: Rules, path: str, data: bytes, seen: tuple) -> None:
    is_json = os.path.splitext(path)[1].lower() in JSON_SUFFIXES
    done = rewrite(data, rules, is_json=is_json)
    found.quoted_left += done.quoted_left
    if done.quoted_left:
        found.leave(path, "quoted")
    if done.edits:
        found.sessions |= done.sessions
        cls, changes = classify(path, data, done.edits)
        found.files.append(FilePlan(path=path, cls=cls, changes=changes, data=data,
                                    new=done.data, seen=seen))
    if rules.spells_login(done.data):
        found.leave(path, "login")


def _dirty(root: str, found: Pass) -> list[tuple[str, str]]:
    """Rule U: each file the pass would change that is untracked (`untracked`) or differs
    from the index or `HEAD` (`modified`)."""
    from . import blobs
    from .setup import _git

    run = _git(root)

    def names(*args: str) -> set[str]:
        out = blobs.run(run, *args)
        return {os.fsdecode(p) for p in out.split(b"\0") if p}

    wanted = [f.path for f in found.files]
    if not wanted:
        return []
    tracked = names("ls-files", "-z", "--cached")
    try:
        changed = names("diff", "-z", "--name-only", "--no-renames")
        changed |= names("diff", "-z", "--name-only", "--no-renames", "--cached", "HEAD")
    except blobs.GitError:
        changed = set(wanted)
    out = []
    for path in wanted:
        if path not in tracked:
            out.append((path, "untracked"))
        elif path in changed:
            out.append((path, "modified"))
    return out


def refuse(found: Pass) -> bool:
    """Rule U, at `--apply`: True, with the files listed and `error` set, when any file it
    would change is untracked or has uncommitted changes."""
    from . import blobs

    if found.error:
        return True
    try:
        dirty = _dirty(found.root, found)
    except blobs.GitError as exc:
        found.error = f"git could not say which files are committed: {exc}"
        found.refused = True
        return True
    if not dirty:
        return False
    for path, reason in dirty:
        found.leave(path, reason)
    found.refused = True
    found.error = (f"--apply changes only committed files, and {len(dirty)} of the files it "
                   "would change are untracked or have uncommitted changes; commit or drop "
                   "those changes, then run again. Nothing was changed")
    return True


def apply(found: Pass) -> Pass:
    """Rule U, then each file's write under Rule C. A refused run writes nothing."""
    found.applied = True
    if refuse(found):
        return found
    real_root = os.path.realpath(found.root)
    for one in found.files:
        if one.target is not None:
            _write_link(found, one)
        else:
            _write(found, one, real_root)
    return found


def _write(found: Pass, one: FilePlan, real_root: str) -> None:
    import moxi.files

    full = os.path.join(found.root, one.path)
    try:
        now = os.lstat(full)
    except OSError:
        found.leave(one.path, "changed")
        return
    if not stat.S_ISREG(now.st_mode) or _seen(now) != one.seen:
        found.leave(one.path, "changed")
        return
    if not now.st_mode & stat.S_IWUSR:
        found.leave(one.path, "read-only")
        return
    if not _under(os.path.realpath(full), real_root):
        found.leave(one.path, "changed")
        return
    if _read(full, now) != one.data:
        found.leave(one.path, "changed")
        return
    try:
        moxi.files.atomic_write(full, one.new, mode=stat.S_IMODE(now.st_mode))
    except OSError:
        found.leave(one.path, "changed")
        return
    one.written = True


def _write_link(found: Pass, one: FilePlan) -> None:
    import moxi.files
    from moxi.paths import link_target

    full = os.path.join(found.root, one.path)
    try:
        if not os.path.islink(full) or link_target(full) != one.target:
            found.leave(one.path, "changed")
            return
        temp = os.path.join(os.path.dirname(full),
                            f".{os.path.basename(full)}.{os.urandom(4).hex()}.tmp")
        os.symlink(one.new_target, temp)
        try:
            moxi.files.replace(temp, full)
        except OSError:
            os.unlink(temp)
            raise
    except OSError:
        found.leave(one.path, "changed")
        return
    one.written = True


def skipped(found: Pass) -> list[str]:
    """The files Rule C skipped."""
    return sorted(p for p, r in found.left if r == "changed")


# -- the text report -------------------------------------------------------------------------

#: The reasons the text report names file by file; the rest it counts.
NAMED = ("too-large", "link-outside", "untracked", "modified", "read-only", "changed")


def render(found: Pass, *, limit: int = 80) -> str:
    from .setup import _shown

    mode = ("refused; nothing was written" if found.refused else
            "applied" if found.applied else "a dry run; nothing was written")
    out = [f"notes home-paths: {mode}", f"root: {_shown(found.root)}   home: {found.home}"]
    if found.error and not found.refused:
        out += [f"can't run: {found.error}"]
        return "\n".join(out) + "\n"
    c = found.counts()
    kinds = c["by_kind"]
    out += ["",
            f"judged {found.judged} files; {c['files']} would change"
            if not found.applied else f"judged {found.judged} files; {c['files']} to change",
            f"  {kinds['home']} home paths written as ~",
            f"  {kinds['session']} session paths written as ids ({c['sessions']} sessions)",
            f"  {kinds['link']} links made relative",
            f"  {c['quoted_left']} home paths left as written after a quote or a = "
            "(quoted)"]
    out.append("by class: " + ", ".join(f"{k} {v}" for k, v in c["by_class"].items()))
    reasons = found.reasons()
    if reasons:
        out.append("left: " + ", ".join(f"{k} {v}" for k, v in reasons.items())
                   + " (--json lists every one)")
    for path, reason in sorted(found.left):
        if reason in NAMED:
            out.append(f"  {reason}: {path}")
    if found.would_refuse and not found.applied:
        out.append(f"--apply would refuse while {len(found.would_refuse)} of these files "
                   "are untracked or have uncommitted changes:")
        out += [f"  {reason}: {path}" for path, reason in found.would_refuse[:limit]]
    if found.refused:
        out += ["", f"refused: {found.error}"]
        return "\n".join(out) + "\n"
    shown = sorted(found.files, key=lambda f: (CLASSES.index(f.cls), f.path))
    if shown and not found.applied:
        out += ["", "Read the code, data, diff and quote changes in full, then sample prose."]
    for one in shown[:max(limit, 0)] if not found.applied else []:
        out.append(f"{one.cls}: {one.path}")
        for change in one.changes:
            where = "link" if change["kind"] == "link" else str(change["line"])
            out.append(f"    {where}: {change['before']} -> {change['after']}")
    if not found.applied and len(shown) > max(limit, 0):
        out.append(f"...and {len(shown) - max(limit, 0)} more files (raise --limit, or use "
                   "--json, which lists every change)")
    out.append("")
    if not found.applied:
        out.append("Write these with: moxi notes home-paths --apply")
        return "\n".join(out) + "\n"
    written = sum(1 for f in found.files if f.written)
    out.append(f"written: {written} files")
    late = skipped(found)
    if late:
        out.append(f"{len(late)} files changed while this run read them, so they were left "
                   "alone. That change is someone else's: commit it or drop it, then run "
                   "again.")
    if written:
        out.append(f'Commit the rewrite: git -C {_shown(found.root)} commit -am '
                   f'"{COMMIT_MESSAGE}"')
    return "\n".join(out) + "\n"


__all__ = ["JSON_SHAPE", "COMMIT_MESSAGE", "START", "CLASSES", "REASONS", "Rules", "Edit",
           "Rewritten", "rewrite", "continues", "starts_token", "quoted", "classify",
           "FilePlan", "Pass", "plan", "refuse", "apply", "skipped", "render"]
