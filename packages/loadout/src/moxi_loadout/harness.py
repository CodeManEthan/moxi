"""Where each harness keeps its files, and how loadout reads and rewrites them.

Homes, honouring the harnesses' own variables:

- Claude Code: `$CLAUDE_CONFIG_DIR`, else `~/.claude`: `settings.json` (hooks and
  permission rules) and `CLAUDE.md` (the global file). Its MCP servers are in
  `$CLAUDE_CONFIG_DIR/.claude.json` when the variable is set, else `~/.claude.json`.
- Codex: `$CODEX_HOME`, else `~/.codex`: `hooks.json`, `rules/loadout.rules` (a whole file
  loadout owns), `AGENTS.md`, and `config.toml` (its MCP servers, read only: the `codex`
  CLI writes them).
- OpenCode: `$XDG_CONFIG_HOME/opencode`, else `~/.config/opencode`: `opencode.json` and
  `AGENTS.md`.

A `Doc` is one harness file read once. JSON files are read with `json` and written back
with their key order and the indent of their first indented line (two spaces if none);
a file that doesn't parse as a JSON object (comments, `.jsonc`, a syntax error) is
refused with one line. Text files are read as UTF-8, and any bytes that aren't are
written back as they were.

`write(doc, harness, keep)` is the one write per file: a backup of the old bytes to
`DATA/loadout/backups/<harness>/<stamp>-<name>` (0600, pruned past `keep`), a re-read
(bytes changed since the plan: the write is dropped), then `moxi.files.atomic_write`
with the file's own mode. A link is written through, to the file it points at. A missing
parent folder is made with the ordinary mode.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

HARNESSES = ("claude", "codex", "opencode")
#: Each harness's global instruction file, by its home.
GLOBAL_NAMES = {"claude": "CLAUDE.md", "codex": "AGENTS.md", "opencode": "AGENTS.md"}


def claude_home() -> Path:
    given = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(given) if given else Path.home() / ".claude"


def claude_json() -> Path:
    given = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(given) / ".claude.json" if given else Path.home() / ".claude.json"


def codex_home() -> Path:
    given = os.environ.get("CODEX_HOME")
    return Path(given) if given else Path.home() / ".codex"


def opencode_home() -> Path:
    given = os.environ.get("XDG_CONFIG_HOME")
    return (Path(given) if given else Path.home() / ".config") / "opencode"


def home(harness: str) -> Path:
    return {"claude": claude_home, "codex": codex_home, "opencode": opencode_home}[harness]()


def settings_file(harness: str) -> Path | None:
    """The JSON file a harness's hooks and settings go in: Claude's `settings.json`,
    Codex's `hooks.json`, OpenCode's `opencode.json`."""
    return {"claude": claude_home() / "settings.json", "codex": codex_home() / "hooks.json",
            "opencode": opencode_home() / "opencode.json"}.get(harness)


def global_file(harness: str) -> Path:
    return home(harness) / GLOBAL_NAMES[harness]


def rules_file() -> Path:
    return codex_home() / "rules" / "loadout.rules"


class Refused(Exception):
    """A harness file loadout can't use as it is: one line naming it and why."""


class Doc:
    """One harness file as read: `raw` (None when missing), and `data` (a dict for JSON,
    a str for text) that the writers change in place."""

    def __init__(self, path: Path, kind: str):
        self.path = Path(path)
        self.kind = kind
        try:
            self.raw = self.path.read_bytes()
        except FileNotFoundError:
            self.raw = None
        except OSError as exc:
            raise Refused(f"{self.path} can't be read ({exc.strerror or exc})") from None
        if kind == "json":
            self.data = self._json()
            self.indent = _indent(self.raw)
        else:
            # Bytes that aren't UTF-8 pass through as they are, so the user's text comes
            # back byte-equal around loadout's part.
            self.data = "" if self.raw is None else self.raw.decode("utf-8",
                                                                    "surrogateescape")
        self.original = self.dumps() if self.raw is not None else None

    def _json(self) -> dict:
        if self.raw is None or not self.raw.strip():
            return {}
        try:
            data = json.loads(self.raw)
        except ValueError as exc:
            raise Refused(f"{self.path} is not plain JSON ({exc}); loadout leaves it, so "
                          "its entries for this harness are not written") from None
        if not isinstance(data, dict):
            raise Refused(f"{self.path} is not a JSON object; loadout leaves it")
        return data

    def dumps(self) -> bytes:
        if self.kind == "json":
            return (json.dumps(self.data, indent=self.indent, ensure_ascii=False)
                    + "\n").encode("utf-8")
        return self.data.encode("utf-8", "surrogateescape")

    @property
    def changed(self) -> bool:
        if self.raw is None:
            return bool(self.data)
        return self.dumps() != self.original

    @property
    def empty(self) -> bool:
        return (not self.data) if self.kind == "json" else not self.data.strip()


def _indent(raw: bytes | None) -> int:
    for line in (raw or b"").decode("utf-8", "replace").splitlines()[1:]:
        stripped = line.lstrip(" ")
        if stripped and len(stripped) < len(line):
            return len(line) - len(stripped)
    return 2


def write(doc: Doc, harness: str, keep: int, *, remove_empty: bool = False) -> str | None:
    """Write `doc` back once, or remove it when `remove_empty` and nothing is left in it.
    Returns None when written, else the line saying why not."""
    from moxi.files import atomic_write

    target = Path(os.path.realpath(doc.path))
    try:
        now = target.read_bytes()
    except FileNotFoundError:
        now = None
    except OSError as exc:
        return f"{doc.path} can't be read again ({exc.strerror or exc})"
    if now != doc.raw:
        return f"{doc.path} changed while planning; run install again"
    if now is not None:
        backup(target, harness, now, keep)
    if remove_empty and doc.empty:
        if now is not None:
            target.unlink()
        return None
    mode = 0o644 if doc.kind == "text" else 0o600
    if now is not None:
        mode = os.stat(target).st_mode & 0o7777
    os.makedirs(target.parent, exist_ok=True)
    atomic_write(target, doc.dumps(), mode=mode)
    return None


def backup(path: Path, harness: str, data: bytes, keep: int) -> Path:
    """Keep `data`, file `path`'s bytes before a write, under the backups store, and
    prune that file's backups to the newest `keep`."""
    from moxi.files import atomic_write, private_dir

    from moxi_loadout import stores

    folder = private_dir(stores.backups_dir() / harness)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + f"{time.time_ns() % 10**9:09d}"
    out = folder / f"{stamp}-{path.name}"
    atomic_write(out, data, mode=0o600)
    mine = sorted(p for p in folder.iterdir() if p.name.endswith("-" + path.name))
    for old in mine[:-keep] if keep > 0 else []:
        try:
            old.unlink()
        except OSError:
            pass
    return out


__all__ = ["HARNESSES", "GLOBAL_NAMES", "claude_home", "claude_json", "codex_home",
           "opencode_home", "home", "settings_file", "global_file", "rules_file", "Refused",
           "Doc", "write", "backup"]
