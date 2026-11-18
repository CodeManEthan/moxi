"""The home-paths guard: turns' guard that tells an agent its note spells the home folder
or the login.

turns calls `judge(call)` before a covered tool call, in the session's endpoint, when the
guard `notes.home-paths` is on: by notes' switch `guard.home_paths`, which setup asks about
with yes as the default, or by naming the guard on turns' `guards.run`. It answers None
(the call runs, nothing is said) or turns' `Allow(LINE)`: the call runs and the agent reads
`LINE` after it. It never refuses.

**What it reads.** Claude Code's `Write` (`content`) and `Edit` (`new_string`), OpenCode's
`write` (`content`) and `edit` (`newString`), each by its file path, and Codex's
`apply_patch`, whose patch text is `command`: the added lines of each file section, a
relative path joined with the call's `cwd`. A file counts when it is under the notes root,
compared as absolute paths, normalized, never resolved.

**When it tells.** When the text spells `H` (the home folder, from the call's `HOME`) where
Rule HP of `moxi notes home-paths` would change it, or spells the login by secrets'
`words` rule: any case, `-` and `_` alike, and no letter or digit right before or after
it. A login shorter than three characters is not looked for; the home folder still is.
It reads neither the private-names list nor any allowlist, so an allowance that keeps the
login in the gates never quiets it.

**Rule PF.** The login is protected while a real folder (not a link) named like it sits
directly under the root, or while notes' `prefix` equals it. A match of a protected login
inside a pointer to that folder is not counted: `notes/<login>` where a token starts
before `notes` or a `/` stands there and no name continues after it, or `<login>/` at a
token start followed by the name of an entry directly under that folder, read now.

**Fail open.** Any error is logged once per kind and answers None, so a fault never
blocks a write; the guard's `on_error` is `allow` as well.

Importing this module imports only the standard library.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable

#: The guard's name in notes' `GUARDS`, its id, and its switch in notes' settings.
NAME = "home-paths"
GUARD_ID = f"notes.{NAME}"
SWITCH = "guard.home_paths"
#: The tools it covers, per harness.
CALLS = {"claude": ("Write", "Edit"), "codex": ("apply_patch",),
         "opencode": ("write", "edit")}
#: The line the agent reads after a covered write that spells the home folder or login.
LINE = ("This note spells your home folder or your login. Write `~/` for a path under your "
        "home, cite a session by its id, not its transcript's path, and write `{user}` "
        "where `~` can't stand.")
#: The shortest login it looks for.
SHORT_LOGIN = 3
#: The characters after which a pointer's token starts: whitespace, a quote, a backtick,
#: `(`, `[`, `<`, `=` or `:`. A line start counts too.
P_START = b" \t\n\r\x0b\x0c\"'`([<=:"
#: Where a pointer's `notes/` may follow: one of `P_START` or a `/`.
POINTER_START = P_START + b"/"

log = logging.getLogger("moxi.notes")
_said: set[str] = set()

#: Each covered tool's path field and text field.
_FIELDS = {("claude", "Write"): ("file_path", "content"),
           ("claude", "Edit"): ("file_path", "new_string"),
           ("opencode", "write"): ("filePath", "content"),
           ("opencode", "edit"): ("filePath", "newString")}


def judge(call):
    """turns' judge: `Allow(LINE)` for a covered write under the notes root that spells the
    home folder or the login, else None."""
    try:
        if _tells(call):
            from moxi_turns import Allow

            return Allow(LINE)
    except Exception as exc:  # noqa: BLE001 - the guard fails open, by design
        kind = type(exc).__name__
        if kind not in _said:
            _said.add(kind)
            log.warning("notes' home-paths guard could not judge a call: %s: %s", kind, exc)
    return None


def _tells(call) -> bool:
    from . import settings as _settings

    places = _settings.read()
    root = os.path.normpath(str(places.root))
    cwd = call.cwd or os.getcwd()
    texts = [text for path, text in written(call) if _under(_absolute(path, cwd), root)]
    if not texts:
        return False
    home = call.env.get("HOME") or os.path.expanduser("~")
    return spells(texts, home=home, root=root, prefix=places.settings["prefix"])


def written(call) -> list[tuple[str, str]]:
    """`(path, text)` for each file the call writes, as the call names it."""
    tool_input = call.input if hasattr(call.input, "get") else {}
    if call.tool == "apply_patch":
        return patch_texts(tool_input.get("command"))
    fields = _FIELDS.get((call.harness, call.tool))
    if fields is None:
        return []
    path, text = tool_input.get(fields[0]), tool_input.get(fields[1])
    if not isinstance(path, str) or not isinstance(text, str):
        return []
    return [(path, text)]


_SECTION = re.compile(r"\*\*\* (Add File|Update File|Delete File|Move to): (.*)")


def patch_texts(text: object) -> list[tuple[str, str]]:
    """`(path, added lines)` for each file section of a Codex patch; a moved section counts
    under both its paths. A line it does not place is skipped."""
    if not isinstance(text, str):
        return []
    out: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for raw in text.split("\n"):
        line = raw[:-1] if raw.endswith("\r") else raw
        m = _SECTION.match(line.strip())
        if m:
            kind, path = m.group(1), m.group(2).strip()
            if kind == "Move to" and out:
                out.append((path, out[-1][1]))
                continue
            current = [] if kind != "Delete File" else None
            if current is not None:
                out.append((path, current))
            continue
        if current is not None and line.startswith("+"):
            current.append(line[1:])
    return [(path, "\n".join(lines)) for path, lines in out]


def _absolute(path: str, cwd: str) -> str:
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))


def _under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def spells(texts: Iterable[str], *, home: str, root: str, prefix: str = "") -> bool:
    """Whether any of `texts` spells `home` in a Rule HP place, or the login outside a
    pointer Rule PF protects."""
    from .home_paths import Rules

    rules = Rules(home)
    protected = _protected(rules.login, root, prefix)
    for text in texts:
        data = text.encode("utf-8", "surrogateescape")
        if rules.home_spans(data):
            return True
        if len(rules.login) >= SHORT_LOGIN and any(
                not _in_pointer(data, s, e, protected) for s, e in login_spans(data, rules.login)):
            return True
    return False


def login_spans(data: bytes, login: str) -> list[tuple[int, int]]:
    """Each match of `login` in `data` by secrets' `words` rule: any case, `-` and `_`
    alike, and no letter or digit right before or after it."""
    pattern = b"".join(b"[-_]" if c in "-_" else re.escape(c.encode("utf-8"))
                       for c in login)
    out = []
    text = data.decode("utf-8", "surrogateescape")
    for m in re.finditer(pattern.decode("utf-8"), text, re.IGNORECASE):
        s, e = m.span()
        if (s > 0 and text[s - 1].isalnum()) or (e < len(text) and text[e].isalnum()):
            continue
        out.append((len(text[:s].encode("utf-8", "surrogateescape")),
                    len(text[:e].encode("utf-8", "surrogateescape"))))
    return out


def _protected(login: str, root: str, prefix: str) -> str | None:
    """The folder Rule PF protects pointers into, or None."""
    login_dir = os.path.join(root, login)
    if prefix.strip("/") == login or (os.path.isdir(login_dir)
                                      and not os.path.islink(login_dir)):
        return login_dir
    return None


_ENTRY_END = re.compile(rb"[/\s`'\"()\[\]<>,:;|#*?]")


def _in_pointer(data: bytes, s: int, e: int, protected: str | None) -> bool:
    """Rule PF: whether the login at `data[s:e]` sits in a pointer into `protected`."""
    from .home_paths import continues, starts_token

    if protected is None:
        return False
    if s >= 6 and data[s - 6:s] == b"notes/" and not continues(data, e):
        at = s - 6
        if at == 0 or data[at - 1] in POINTER_START:
            return True
    if starts_token(data, s, P_START) and data[e:e + 1] == b"/":
        m = _ENTRY_END.search(data, e + 1)
        name = data[e + 1:m.start() if m else len(data)].rstrip(b".")
        if name:
            try:
                return os.fsdecode(name) in os.listdir(protected)
            except OSError:
                return False
    return False


__all__ = ["NAME", "GUARD_ID", "SWITCH", "CALLS", "LINE", "judge", "written", "patch_texts",
           "spells", "login_spans"]
