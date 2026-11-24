"""Where each harness keeps its files, and how loadout reads and rewrites them.

Homes, honouring the harnesses' own variables. Each resolver takes the environment to read
(`os.environ` without one) and reads it only by name, so a page can resolve the homes the
user's terminal would (`moxi_loadout.homes`); `~` is the home folder that environment
places (`moxi.paths.home`):

- Claude Code: `$CLAUDE_CONFIG_DIR`, else `~/.claude`: `settings.json` (hooks and
  permission rules) and `CLAUDE.md` (the global file). Its MCP servers are in
  `$CLAUDE_CONFIG_DIR/.claude.json` when the variable is set, else `~/.claude.json`. A
  leading `~` in `CLAUDE_CONFIG_DIR` is the home folder, for both
  (`moxi.paths.claude_home`).
- Codex: `$CODEX_HOME`, else `~/.codex`: `hooks.json`, `rules/loadout.rules` (a whole file
  loadout owns), `AGENTS.md`, and `config.toml` (its MCP servers, read only: the `codex`
  CLI writes them). That Codex reads `hooks.json` and the rules file is taken from its
  binary, not yet confirmed by a run.
- OpenCode: `$OPENCODE_CONFIG_DIR` when set, else `$XDG_CONFIG_HOME/opencode`, else
  `~/.config/opencode`, as OpenCode resolves it: `opencode.json` and `AGENTS.md`. A run of
  OpenCode 1.19.5 confirmed the `AGENTS.md` facts: it reads the XDG one; with the variable
  set it reads the one under it instead, and not the XDG one even when the variable's has
  none; and it doesn't read `~/.claude/CLAUDE.md` while its own `AGENTS.md` exists, even
  empty (a dangling link counts as absent). The `opencode.json` shapes follow OpenCode's
  published config schema, not yet confirmed by a run. Its skills and agents folders (the
  opencode row in the core's harness table) keep the XDG form, `opencode_xdg_home`:
  OpenCode reads skills and agents from the XDG folder and from `$OPENCODE_CONFIG_DIR`
  both, so the XDG row reaches it either way.

A `Doc` is one harness file read once, and `write(doc, harness, keep)` the one write per
file: both are the core's (`moxi.harnessfile`), which Claude's other writers share, with
loadout's own lines. JSON files are read with `json` and written back with their key
order and the indent of their first indented line (two spaces if none); a file that
doesn't parse as a JSON object (comments, `.jsonc`, a syntax error) is refused with one
line that says loadout leaves it. Text files are read as UTF-8, and any bytes that aren't
are written back as they were. A write keeps a backup of the old bytes in
`DATA/loadout/backups/<harness>/<stamp>-<name>` (0600, pruned past `keep`), re-reads the
file (bytes changed since the plan: the write is dropped), then writes it with
`moxi.files.atomic_write` in the file's own mode. A link is written through, to the file
it points at. A missing parent folder is made with the ordinary mode.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from moxi import harnessfile
from moxi.paths import claude_home

HARNESSES = ("claude", "codex", "opencode")
#: Each harness's global instruction file, by its home.
GLOBAL_NAMES = {"claude": "CLAUDE.md", "codex": "AGENTS.md", "opencode": "AGENTS.md"}


def claude_json(env: Mapping[str, str] | None = None) -> Path:
    """Claude's MCP file: in Claude's folder when `CLAUDE_CONFIG_DIR` is set, else in the
    home folder. Its `~` reads as `settings.json`'s does."""
    if _env(env).get("CLAUDE_CONFIG_DIR"):
        return claude_home(env) / ".claude.json"
    return _home(env) / ".claude.json"


def codex_home(env: Mapping[str, str] | None = None) -> Path:
    given = _env(env).get("CODEX_HOME")
    return Path(given) if given else _home(env) / ".codex"


def opencode_home(env: Mapping[str, str] | None = None) -> Path:
    """Where OpenCode reads its `AGENTS.md` and `opencode.json`."""
    given = _env(env).get("OPENCODE_CONFIG_DIR")
    return Path(given) if given else opencode_xdg_home(env)


def opencode_xdg_home(env: Mapping[str, str] | None = None) -> Path:
    """OpenCode's folder under the XDG config home, which its skills and agents folders
    stay under whatever `OPENCODE_CONFIG_DIR` says."""
    given = _env(env).get("XDG_CONFIG_HOME")
    return (Path(given) if given else _home(env) / ".config") / "opencode"


def home(harness: str, env: Mapping[str, str] | None = None) -> Path:
    return {"claude": claude_home, "codex": codex_home,
            "opencode": opencode_home}[harness](env)


def present(names: Iterable[str], env: Mapping[str, str] | None = None) -> list[str]:
    """Which of `names` (the core harness table's) are on this machine, in their order.

    A harness loadout knows is on it when its home above is a folder, as the sessions
    catalog skips a harness whose home is absent. A row loadout knows no home for is
    counted as present, since nothing here can tell. `harness_row.present` is another
    fact: whether the table has an opencode row at all."""
    out = []
    for name in names:
        if name not in HARNESSES:
            out.append(name)
            continue
        try:
            if home(name, env).is_dir():
                out.append(name)
        except (OSError, ValueError, LookupError):
            continue
    return out


#: The states of a row that say something stands where it places: loadout's placement, or
#: what is in its way.
STANDING = frozenset({"ok", "stale", "modified", "blocked"})


def shown(names: Iterable[str], rows: Iterable[tuple[str | None, str, str | None]],
          env: Mapping[str, str] | None = None) -> list[str]:
    """Which of `names` the page draws, in their order: each `present` one, and one that
    isn't present while one of its `rows` (`(harness, state, target)`) is `STANDING` at a
    target, so nothing on disk is hidden. A harness not present with only `missing` or
    `off` rows, or rows that place nowhere (a section that can't be placed), isn't drawn,
    and its rows aren't counted."""
    names = list(names)
    standing = {h for h, state, target in rows if state in STANDING and target}
    here = set(present(names, env))
    return [name for name in names if name in here or name in standing]


def settings_file(harness: str) -> Path | None:
    """The JSON file a harness's hooks and settings go in: Claude's `settings.json`,
    Codex's `hooks.json`, OpenCode's `opencode.json`."""
    return {"claude": claude_home() / "settings.json", "codex": codex_home() / "hooks.json",
            "opencode": opencode_home() / "opencode.json"}.get(harness)


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _home(env: Mapping[str, str] | None) -> Path:
    from moxi import paths

    return paths.home(env)


def global_file(harness: str) -> Path:
    return home(harness) / GLOBAL_NAMES[harness]


def rules_file() -> Path:
    return codex_home() / "rules" / "loadout.rules"


class Refused(harnessfile.Refused):
    """A harness file loadout can't use as it is: one line naming it and why."""


# What loadout does about a file the core's `Doc` refuses, after the core's line.
_NOT_PLAIN = "; loadout leaves it, so its entries for this harness are not written"
_NOT_OBJECT = "; loadout leaves it"


class Doc(harnessfile.Doc):
    """One harness file as read: `raw` (None when missing), and `data` (a dict for JSON,
    a str for text) that the writers change in place. A refusal is loadout's `Refused`,
    its line saying what loadout does about the file."""

    def __init__(self, path: Path, kind: str):
        try:
            super().__init__(path, kind)
        except harnessfile.Refused as exc:
            line = str(exc)
            where = Path(path)
            if line.startswith(f"{where} is not plain JSON ("):
                line += _NOT_PLAIN
            elif line == f"{where} is not a JSON object":
                line += _NOT_OBJECT
            raise Refused(line) from None


def write(doc: Doc, harness: str, keep: int, *, remove_empty: bool = False,
          keep_copy=None) -> str | None:
    """Write `doc` back once, or remove it when `remove_empty` and nothing is left in it.
    With `keep_copy`, `keep_copy(path, now)` is called first with the bytes just read
    again and checked; when it raises `OSError` nothing is written. Returns None when
    written, else the line saying why not."""
    line = harnessfile.write(doc, _backups(harness), keep, remove_empty=remove_empty,
                             keep_copy=keep_copy)
    if line == f"{doc.path} changed since it was read":
        return f"{doc.path} changed while planning; run install again"
    return line


def backup(path: Path, harness: str, data: bytes, keep: int) -> Path:
    """Keep `data`, file `path`'s bytes before a write, under the backups store, and
    prune that file's backups to the newest `keep`."""
    return harnessfile.backup(path, _backups(harness), data, keep)


def _backups(harness: str) -> Path:
    from moxi_loadout import stores

    return stores.backups_dir() / harness


__all__ = ["HARNESSES", "GLOBAL_NAMES", "present", "claude_home", "claude_json", "codex_home",
           "opencode_home", "opencode_xdg_home", "home", "settings_file", "global_file", "rules_file", "Refused",
           "Doc", "write", "backup"]
