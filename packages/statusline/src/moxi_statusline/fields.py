"""The base fields: the values the line shows from Claude Code's status line input.

Each field shows one value as Claude gives it, formatted: cut, coloured, joined. None
computes a figure from other figures. A field with no value gives None, and the line
leaves it out with its separator.

    dir         `workspace.current_dir`, else `cwd`: `~` for the home folder, and `…/a/b`
                past `dir.parts` parts
    branch      the branch in the repository's `HEAD` file, read from the files (`branch`)
    model       `model.display_name`, with a leading `Claude ` cut
    effort      `effort.level`, as given
    context     `context_window.used_percentage` as `ctx:42%`, yellow from
                `context.warn_at`, red from `context.alert_at`; `ctx:—` while it is null
    session     `#` and the first 6 characters of `session_id`
    transcript  the file name of `transcript_path`
    lines       `cost.total_lines_added` and `total_lines_removed` as `+12/-3`; nothing
                while both are 0
    cost        `cost.total_cost_usd` as `$1.24`, Claude's own figure

`row` is no field: in `show` it starts the second row.

This module imports only `os` and `math`, so a render pays nothing more for it.
"""

import math
import os

#: The item that starts the next row.
ROW = "row"
#: The base fields, by the name `show` gives them. They are read before module names.
BASE_FIELDS = ("dir", "branch", "model", "effort", "context", "session", "transcript",
               "lines", "cost")
#: Every name `show` reads as something other than a module.
RESERVED = (*BASE_FIELDS, ROW)

YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"
#: What the directory shows when the input could not be read.
UNREADABLE = "?"


class Options:
    """What the fields need from the config: `parts`, `warn_at`, `alert_at`, `color`, and
    `home`, the home folder `dir` shows as `~` (None: none)."""

    __slots__ = ("parts", "warn_at", "alert_at", "color", "home")

    def __init__(self, *, parts: int = 2, warn_at: int = 50, alert_at: int = 80,
                 color: bool = True, home: str | None = None):
        self.parts = parts
        self.warn_at = warn_at
        self.alert_at = alert_at
        self.color = color
        self.home = home


def field(name: str, payload: dict | None, options: Options) -> str | None:
    """Base field `name`'s text from `payload` (Claude's parsed input; None when it could
    not be read), or None when it has no value."""
    if payload is None:
        return UNREADABLE if name == "dir" else None
    return _FIELDS[name](payload, options)


def _get(payload, *keys):
    for key in keys:
        if not isinstance(payload, dict):
            return None
        payload = payload.get(key)
    return payload


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _whole(value: float) -> int:
    """A number shown whole, halves up."""
    return math.floor(value + 0.5)


def _current_dir(payload) -> str | None:
    return _text(_get(payload, "workspace", "current_dir")) or _text(_get(payload, "cwd"))


def _dir(payload, options: Options) -> str | None:
    path = _current_dir(payload)
    if path is None:
        return None
    home = (options.home or "").rstrip("/")
    tilde = bool(home) and (path == home or path.startswith(home + "/"))
    rest = path[len(home):] if tilde else path
    parts = [p for p in rest.split("/") if p]
    if len(parts) > max(options.parts, 1):
        return "…/" + "/".join(parts[-max(options.parts, 1):])
    if tilde:
        return "/".join(["~", *parts])
    return path


def _branch(payload, options: Options) -> str | None:
    path = _current_dir(payload)
    return branch(path) if path is not None else None


def branch(start: str) -> str | None:
    """The branch of the repository holding folder `start`, read from its `HEAD` file:
    no process. None outside a repository, on a reftable repository, or when a file
    can't be read."""
    folder = os.path.abspath(start)
    while True:
        dot_git = os.path.join(folder, ".git")
        if os.path.isdir(dot_git):
            return _head(os.path.join(dot_git, "HEAD"))
        if os.path.isfile(dot_git):
            git_dir = _gitdir(dot_git)
            if git_dir is None:
                return None
            if not os.path.isabs(git_dir):
                git_dir = os.path.join(folder, git_dir)
            return _head(os.path.join(git_dir, "HEAD"))
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read(4096)
    except (OSError, UnicodeDecodeError):
        return None


def _gitdir(dot_git: str) -> str | None:
    """The `gitdir:` path of a worktree's or submodule's `.git` file."""
    text = _read(dot_git)
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith("gitdir:"):
            return line[len("gitdir:"):].strip() or None
    return None


def _head(path: str) -> str | None:
    text = _read(path)
    if text is None:
        return None
    head = text.strip()
    if head.startswith("ref:"):
        ref = head[len("ref:"):].strip()
        name = ref.removeprefix("refs/heads/")
        # A reftable repository's HEAD names this placeholder; its branch is in the tables.
        return None if not name or name == ".invalid" else name
    if len(head) >= 7 and all(c in "0123456789abcdef" for c in head):
        return head[:7]
    return None


def _model(payload, options: Options) -> str | None:
    name = _text(_get(payload, "model", "display_name"))
    if name is None:
        return None
    return name.removeprefix("Claude ") or None


def _effort(payload, options: Options) -> str | None:
    return _text(_get(payload, "effort", "level"))


def _context(payload, options: Options) -> str | None:
    window = _get(payload, "context_window")
    if not isinstance(window, dict):
        return None
    raw = window.get("used_percentage")
    if raw is None:
        return "ctx:—"
    used = _number(raw)
    if used is None:
        return None
    shown = _whole(used)
    text = f"ctx:{shown}%"
    if not options.color:
        return text
    colour = (RED if shown >= options.alert_at else YELLOW if shown >= options.warn_at
              else None)
    return f"{colour}{text}{RESET}" if colour else text


def _session(payload, options: Options) -> str | None:
    session = _text(_get(payload, "session_id"))
    return f"#{session[:6]}" if session else None


def _transcript(payload, options: Options) -> str | None:
    path = _text(_get(payload, "transcript_path"))
    return (os.path.basename(path.rstrip("/")) or None) if path else None


def _count(value) -> int:
    number = _number(value)
    return 0 if number is None else _whole(number)


def _lines(payload, options: Options) -> str | None:
    added = _count(_get(payload, "cost", "total_lines_added"))
    removed = _count(_get(payload, "cost", "total_lines_removed"))
    if added == 0 and removed == 0:
        return None
    return f"+{added}/-{removed}"


def _cost(payload, options: Options) -> str | None:
    cost = _number(_get(payload, "cost", "total_cost_usd"))
    return None if cost is None else f"${cost:.2f}"


_FIELDS = {
    "dir": _dir,
    "branch": _branch,
    "model": _model,
    "effort": _effort,
    "context": _context,
    "session": _session,
    "transcript": _transcript,
    "lines": _lines,
    "cost": _cost,
}
