"""Claude Code's `statusLine` setting: which line is Moxi's, and the acts that write it.

The setting lives in Claude's user `settings.json`, in the folder `moxi.paths.claude_home`
finds. statusline writes its `statusLine` key and nothing else, and only from these acts:
`switch` (the verb `use`, setup, the page), `restore` (the verb, the page, the
teardown) and `remove` (the teardown, when nothing was saved). The render never imports
this module: a render writes nothing.

**Moxi's line** is a `statusLine` whose `command` is one absolute path, any
environment's, to a file named `moxi-statusline-render`. A path with a space is written
shell-quoted, as one word, and still counts. A user's own script that calls the render,
or a bare `moxi-statusline-render` found on PATH, is not Moxi's line: it is theirs, and
statusline leaves it alone.

**The write.** Each act reads the file once with `moxi.harnessfile.Doc` and writes it
once with `moxi.harnessfile.write`: a file that is not a plain JSON object is refused and
nothing changes; the old bytes are kept under statusline's `backups` store (the newest
5); the write is dropped when the file changed since it was read (Claude Code or another
module wrote it meanwhile); the file keeps its mode, key order and indent, a link stays a
link, and no lock file is left in Claude's folder.

**The switch** keeps the line it replaces in the `previous` store (`previous.json`),
unless that line was Moxi's already, so `restore` can put it back. Moxi's line keeps the
old line's `padding` and `refreshInterval`; with no `refreshInterval` it refreshes every 5
seconds, so the cache timer counts while a session is idle. Over Moxi's own line at
another path, the switch rewrites the path alone.

**Restore** writes the saved line back, or removes the key when there was none, only
over Moxi's line: a line the user has set since is theirs, and `force` is the one way
over it. The saved line is kept after a restore.

Each act returns an `Act`: its exit code, what kind of outcome it was, and the lines to
say. It prints nothing itself, so the verbs, setup and the page each show it their way.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path

from moxi import exit as codes
from moxi import files, harnessfile, paths

MODULE = "statusline"
#: The console script Moxi's line runs.
SCRIPT = "moxi-statusline-render"
#: The key of Claude Code's settings this module writes.
KEY = "statusLine"
#: How many backups of `settings.json` are kept.
KEEP = 5
#: Moxi's line's refresh, in seconds, when the line it replaces set none.
REFRESH = 5
#: What every act that changes nothing says after its reason.
LEFT = "Claude Code's status line is left as it is"
SWITCHED = ("Claude Code now uses Moxi's status line. moxi statusline restore puts your old "
            "one back.")

_WINDOWS = os.name == "nt"


class Act:
    """What one act did.

    `code` is its exit code (`moxi.exit`); `kind` names the outcome: `switched`,
    `refreshed` (Moxi's line, its path rewritten), `current` (Moxi's line at this path
    already), `restored`, `removed`, `refused` (the settings file, or the store that
    keeps the old line, can't be read or written),
    `changed` (the file changed under the act, nothing written), `not-moxis` (restore over
    a line that isn't Moxi's), `nothing-saved`, `no-command` (the render isn't installed
    here), `config` (the line switched but `show` wasn't written). `lines` are what it
    says, and `command` the `statusLine` command it found before acting.
    """

    __slots__ = ("code", "kind", "lines", "command")

    def __init__(self, code: int, kind: str, lines: Sequence[str],
                 command: str | None = None):
        self.code = code
        self.kind = kind
        self.lines = list(lines)
        self.command = command

    @property
    def ok(self) -> bool:
        return self.code == codes.OK

    def __repr__(self) -> str:
        return f"Act({self.code}, {self.kind!r}, {self.lines!r})"


class Saved:
    """The line kept at a switch: `had` is whether there was one; `value` the
    `statusLine` value it had (None when it had none); `at` when it was kept."""

    __slots__ = ("had", "value", "at")

    def __init__(self, *, had: bool, value=None, at: str | None = None):
        self.had = had
        self.value = value if had else None
        self.at = at

    def describe(self) -> str:
        if not self.had:
            return "none (restore removes the setting)"
        return command_of(self.value) or json.dumps(self.value)

    def as_json(self) -> dict:
        out: dict = {"had": self.had}
        if self.had:
            out[KEY] = self.value
        if self.at:
            out["saved_at"] = self.at
        return out


class SavedError(Exception):
    """The `previous` store holds something that can't be read as a saved line."""


# -- where things are ------------------------------------------------------------------

def settings_path(env: Mapping[str, str] | None = None) -> Path:
    """Claude Code's user `settings.json`."""
    return paths.claude_home(env) / "settings.json"


def _state_dir(env: Mapping[str, str] | None = None) -> Path:
    return paths.Layout(MODULE).state_dir(env)


def backups_dir(env: Mapping[str, str] | None = None) -> Path:
    """The `backups` store: the old bytes of `settings.json` before each write."""
    return _state_dir(env) / "backups"


def previous_path(env: Mapping[str, str] | None = None) -> Path:
    """The `previous` store: the line a switch replaced."""
    return _state_dir(env) / "previous.json"


# -- what a line is ----------------------------------------------------------------------

def command_of(value) -> str | None:
    """The `command` of a `statusLine` value, or None when it has none."""
    if isinstance(value, dict):
        command = value.get("command")
        if isinstance(command, str) and command.strip():
            return command
    return None


def moxis_path(value) -> str | None:
    """The render's path when `value` is Moxi's line, else None."""
    command = command_of(value)
    if command is None:
        return None
    if _WINDOWS:
        word = command.strip()
        if len(word) > 1 and word[0] == word[-1] == '"':
            word = word[1:-1]
        names = (SCRIPT, SCRIPT + ".exe")
        name = os.path.basename(word).lower()
    else:
        try:
            words = shlex.split(command)
        except ValueError:
            return None
        if len(words) != 1:
            return None
        word = words[0]
        names = (SCRIPT,)
        name = os.path.basename(word)
    if os.path.isabs(word) and name in names:
        return word
    return None


def is_moxis(value) -> bool:
    """Whether `value`, a `statusLine` setting, is Moxi's line."""
    return moxis_path(value) is not None


def render_path() -> str:
    """This environment's render. Raises `FileNotFoundError` when it isn't installed."""
    return str(paths.script(SCRIPT))


def render_command(path: str | None = None) -> str:
    """The `command` of Moxi's line for `path` (default: this environment's render), one
    word for the shell Claude Code runs it with."""
    path = render_path() if path is None else path
    if _WINDOWS:
        return f'"{path}"' if " " in path else path
    return shlex.quote(path)


# -- reading ----------------------------------------------------------------------------

def read(env: Mapping[str, str] | None = None) -> harnessfile.Doc:
    """Claude Code's `settings.json`, read once. Raises `moxi.harnessfile.Refused`."""
    return harnessfile.Doc(settings_path(env), "json")


def saved(env: Mapping[str, str] | None = None) -> Saved | None:
    """The line kept at the last switch, or None when nothing was kept. Raises
    `SavedError` when the store can't be read."""
    path = previous_path(env)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SavedError(f"{path} can't be read ({exc.strerror or exc})") from None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SavedError(f"{path} is not plain JSON ({exc})") from None
    if not isinstance(data, dict) or not isinstance(data.get("had"), bool):
        raise SavedError(f"{path} doesn't hold a saved status line")
    at = data.get("saved_at") if isinstance(data.get("saved_at"), str) else None
    return Saved(had=data["had"], value=data.get(KEY), at=at)


def _keep(doc: harnessfile.Doc, env: Mapping[str, str] | None) -> None:
    from .clock import stamp

    had = KEY in doc.data and doc.data[KEY] is not None
    entry = Saved(had=had, value=doc.data.get(KEY), at=stamp()).as_json()
    path = previous_path(env)
    files.private_dir(path.parent)
    files.atomic_write(path, (json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
                       .encode("utf-8"), mode=0o600)


def _write(doc: harnessfile.Doc, env: Mapping[str, str] | None,
           found: str | None) -> Act | None:
    """Write `doc` when it changed. None when written (or nothing to write), else the
    act that says why not."""
    if not doc.changed:
        return None
    try:
        line = harnessfile.write(doc, backups_dir(env), KEEP)
    except OSError as exc:
        return Act(codes.ERROR, "refused",
                   [f"{doc.path} can't be written ({exc.strerror or exc})", LEFT], found)
    if line is not None:
        return Act(codes.TEMPFAIL, "changed", [line, f"{LEFT}; run it again"], found)
    return None


def _refused(exc: harnessfile.Refused) -> Act:
    return Act(codes.REFUSED, "refused", [str(exc), LEFT])


# -- the acts ---------------------------------------------------------------------------

def switch(show: Sequence[str] | None = None, *, by: str,
           env: Mapping[str, str] | None = None) -> Act:
    """Set Claude Code's line to Moxi's, and when `show` is given write it as the
    config's `show` (`moxi.config.set_value`, with `by`). None leaves `show` as it is.

    Over another line (or none) it keeps that line in `previous.json` first. Over Moxi's
    line it only rewrites the path, and says so; at this path already, it writes nothing
    to Claude's settings."""
    try:
        doc = read(env)
    except harnessfile.Refused as exc:
        return _refused(exc)
    current = doc.data.get(KEY)
    found = command_of(current)
    try:
        command = render_command()
    except FileNotFoundError as exc:
        return Act(codes.ERROR, "no-command", [str(exc), LEFT], found)
    if is_moxis(current):
        if found == command:
            kind, lines = "current", [f"Claude Code already uses Moxi's status line "
                                      f"({command})"]
        else:
            kind, lines = "refreshed", [f"Claude Code's status line now runs {command}"]
        new = dict(current)
        new["command"] = command
    else:
        kind, lines = "switched", [SWITCHED]
        new = {"type": "command", "command": command}
        old = current if isinstance(current, dict) else {}
        if "padding" in old:
            new["padding"] = old["padding"]
        new["refreshInterval"] = old.get("refreshInterval", REFRESH)
        try:
            _keep(doc, env)
        except OSError as exc:
            return Act(codes.ERROR, "refused",
                       [f"{previous_path(env)} can't be written ({exc.strerror or exc}), "
                        "so the line it would keep could not be put back", LEFT], found)
    doc.data[KEY] = new
    failed = _write(doc, env, found)
    if failed is not None:
        return failed
    if show is not None:
        from moxi import config

        try:
            config.set_value(MODULE, "show", list(show), by=by)
        except config.ConfigError as exc:
            return Act(codes.ERROR, "config",
                       [*lines, f"its items weren't written: {exc}"], found)
    return Act(codes.OK, kind, lines, found)


def restore(*, force: bool = False, env: Mapping[str, str] | None = None) -> Act:
    """Put back the line the last switch kept: write its value, or remove the key when
    there was none. Only over Moxi's line, unless `force`. The saved line is kept."""
    try:
        doc = read(env)
    except harnessfile.Refused as exc:
        return _refused(exc)
    current = doc.data.get(KEY)
    found = command_of(current)
    try:
        kept = saved(env)
    except SavedError as exc:
        return Act(codes.ERROR, "nothing-saved", [str(exc), LEFT], found)
    if kept is None:
        return Act(codes.REFUSED, "nothing-saved",
                   [f"No old status line was saved, so there is nothing to put back. "
                    f"{LEFT}."], found)
    if not force and not is_moxis(current):
        now = (f"it runs {found}" if found is not None
               else "it has none" if current is None else "it sets no command")
        return Act(codes.REFUSED, "not-moxis",
                   [f"Claude Code's status line is no longer Moxi's ({now}), so restore "
                    f"leaves it; moxi statusline restore --force puts the saved one back "
                    f"over it"], found)
    if kept.had:
        doc.data[KEY] = kept.value
        line = f"Claude Code's status line is your old one again: {kept.describe()}"
    else:
        doc.data.pop(KEY, None)
        line = "Claude Code's status line is removed, as it was before Moxi's"
    failed = _write(doc, env, found)
    if failed is not None:
        return failed
    return Act(codes.OK, "restored", [line], found)


def remove(*, env: Mapping[str, str] | None = None) -> Act:
    """Remove Moxi's line from Claude Code's settings. Over any other line it changes
    nothing (`not-moxis`)."""
    try:
        doc = read(env)
    except harnessfile.Refused as exc:
        return _refused(exc)
    current = doc.data.get(KEY)
    found = command_of(current)
    if not is_moxis(current):
        return Act(codes.REFUSED, "not-moxis",
                   ["Claude Code's status line is not Moxi's, so it is left as it is"],
                   found)
    del doc.data[KEY]
    failed = _write(doc, env, found)
    if failed is not None:
        return failed
    return Act(codes.OK, "removed",
               ["Claude Code's status line was Moxi's and no old one was saved, so the "
                "setting is removed"], found)


# -- what is there -----------------------------------------------------------------------

def state(env: Mapping[str, str] | None = None) -> dict:
    """Claude Code's line as statusline sees it, as plain data: `settings` (the file),
    `error` (why it can't be read, else None), `set` (whether a `statusLine` is set),
    `command`, `moxis`, `here` (Moxi's, at this environment's render), `saved` (the kept
    line as `Saved.as_json`, None when nothing is kept) and `saved_error`."""
    out: dict = {"settings": str(settings_path(env)), "error": None, "set": False,
                 "command": None, "moxis": False, "here": False, "saved": None,
                 "saved_error": None}
    try:
        doc = read(env)
    except harnessfile.Refused as exc:
        out["error"] = str(exc)
    else:
        current = doc.data.get(KEY)
        out["set"] = current is not None
        out["command"] = command_of(current)
        out["moxis"] = is_moxis(current)
        if out["moxis"]:
            try:
                out["here"] = out["command"] == render_command()
            except FileNotFoundError:
                out["here"] = False
    try:
        kept = saved(env)
    except SavedError as exc:
        out["saved_error"] = str(exc)
    else:
        out["saved"] = None if kept is None else kept.as_json()
    return out


def project_lines(project_dir: str | os.PathLike,
                  env: Mapping[str, str] | None = None) -> list[tuple[str, str | None]]:
    """The project settings in `project_dir` that set their own `statusLine`, which Claude
    Code uses there over the user's: `(path, command)` for each, the command None when
    the line has no command. A file that can't be read as JSON is passed over. The
    user's own `settings.json` is never one of them."""
    mine = os.path.realpath(settings_path(env))
    out = []
    for name in ("settings.json", "settings.local.json"):
        path = Path(project_dir) / ".claude" / name
        if not path.is_file() or os.path.realpath(path) == mine:
            continue
        try:
            data = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get(KEY) is not None:
            out.append((str(path), command_of(data[KEY])))
    return out


__all__ = ["Act", "Saved", "SavedError", "KEY", "SCRIPT", "settings_path", "backups_dir",
           "previous_path", "command_of", "moxis_path", "is_moxis", "render_path",
           "render_command", "read", "saved", "switch", "restore", "remove", "state",
           "project_lines"]
