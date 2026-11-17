"""The folder-trust seed: Claude Code's trust of a slot launch's folder, written before the
session starts, so its folder-trust screen does not show.

Claude Code keeps the folders a person has trusted in its own config file, as
`projects[<folder>].hasTrustDialogAccepted`. With `startup.trust_dialog` on, a Claude slot
launch sets that key for its own folder just before slots starts the session, and the
startup watch answers the screen if it shows anyway (`startup`). A refused launch never
gets this far, so it writes nothing.

- The key is the launch folder's git root (`git rev-parse --show-toplevel`), else the
  folder itself, resolved. Never a folder above that.
- The file is `$CLAUDE_CONFIG_DIR/.claude.json` when the launch's environment sets
  `CLAUDE_CONFIG_DIR`, else `.claude.json` in the launch's home folder: the two places
  Claude Code reads.
- The write sets that one key and nothing else. The file is read and parsed, its
  modification time and size noted, and the new text made. Just before it is written, the
  time and size are read again. Unchanged, the text goes to a temporary file in the same
  folder with the original's file mode, renamed over the original (`moxi.files`).
  Changed (a running session wrote the file meanwhile), the temporary file is dropped and
  the read, change and check are done once more; changed again, nothing is written. This
  narrows the window in which a running session's write could be lost, but can't close
  it: Claude Code takes no lock the launcher could share, and the screen answer covers
  what the seed misses.
- A missing file, one that doesn't parse, or a top level, `projects` table or folder entry
  that isn't an object is left untouched, and the outcome says why.
- The home folder is never written: Claude Code keeps trust of the home folder for one
  session only, so the screen answer covers it.

`seed` returns a `Seeded`: the folder, the file, and one of `already`, `seeded`,
`seed-refused(<why>)` or `home-dir`. The caller records it as a `trust-seed` event.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from moxi import files

#: The `events` kind of a seed's outcome.
EVENT = "trust-seed"
#: The key Claude Code reads a folder's trust from.
KEY = "hasTrustDialogAccepted"
#: The outcomes.
ALREADY = "already"
SEEDED = "seeded"
HOME_DIR = "home-dir"
REFUSED = "seed-refused({why})"
#: The file Claude Code keeps folder trust in, in its config folder or the home folder.
CONFIG_FILE = ".claude.json"
#: How long the git root is waited for, in seconds.
GIT_WAIT_S = 5.0


@dataclass(frozen=True)
class Seeded:
    """What one seed did: the folder it keyed (`folder`), the file (`file`) and the
    outcome."""

    folder: str
    file: str
    outcome: str

    @property
    def written(self) -> bool:
        return self.outcome == SEEDED

    def detail(self) -> dict:
        return {"folder": self.folder, "file": self.file, "outcome": self.outcome}


class _Changed(Exception):
    """The file changed between the read and the rename."""


class _Refused(Exception):
    """The file is left untouched, for the reason given."""


def home_of(env: Mapping[str, str]) -> Path:
    """The launch's home folder: its `HOME`, else this account's."""
    home = env.get("HOME")
    return Path(home) if home else Path(os.path.expanduser("~"))


def _config_dir(env: Mapping[str, str]) -> Path | None:
    """`CLAUDE_CONFIG_DIR` in `env`, with a leading `~` read as the launch's home folder."""
    config_dir = env.get("CLAUDE_CONFIG_DIR")
    if not config_dir:
        return None
    if config_dir == "~" or config_dir.startswith("~/"):
        return home_of(env) / config_dir[2:]
    return Path(config_dir)


def config_file(env: Mapping[str, str]) -> Path:
    """The file Claude Code keeps folder trust in, for a session with environment `env`."""
    config_dir = _config_dir(env)
    return (config_dir if config_dir is not None else home_of(env)) / CONFIG_FILE


def settings_file(env: Mapping[str, str]) -> Path:
    """Claude Code's user settings file, for a session with environment `env`: where it
    writes its consent after the bypass warning is answered."""
    config_dir = _config_dir(env)
    base = config_dir if config_dir is not None else home_of(env) / ".claude"
    return base / "settings.json"


def trust_root(folder: str | os.PathLike) -> Path:
    """The folder Claude Code keys `folder`'s trust by: its git root, else itself,
    resolved."""
    here = Path(folder).resolve()
    try:
        found = subprocess.run(["git", "-C", str(here), "rev-parse", "--show-toplevel"],
                               capture_output=True, text=True, timeout=GIT_WAIT_S,
                               check=False)
    except (OSError, subprocess.SubprocessError):
        return here
    top = found.stdout.strip()
    if found.returncode != 0 or not top:
        return here
    return Path(top).resolve()


def seed(folder: str | os.PathLike, env: Mapping[str, str], *,
         between: Callable[[Path], None] | None = None) -> Seeded:
    """Mark `folder`'s trust root trusted in Claude Code's config file for a session with
    environment `env`; see the module's text for the rule. `between` is called with the
    file's path just before the second look at it: a test's stand-in for a session writing the file meanwhile."""
    root = trust_root(folder)
    path = config_file(env)
    done = _done(root, path)
    try:
        home = home_of(env).resolve()
    except OSError:
        home = None
    if root == home:
        return done(HOME_DIR)
    for attempt in range(2):
        try:
            return done(_write(path, str(root), between))
        except _Refused as exc:
            return done(REFUSED.format(why=str(exc)))
        except _Changed:
            if attempt:
                return done(REFUSED.format(why="changed"))
    raise AssertionError("unreachable")  # pragma: no cover


def _done(root: Path, path: Path) -> Callable[[str], Seeded]:
    return lambda outcome: Seeded(str(root), str(path), outcome)


def _stamp(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_mtime_ns, info.st_size


def _write(path: Path, key: str, between: Callable[[Path], None] | None) -> str:
    """One read, change and check: the outcome word, or `_Refused` or `_Changed`."""
    try:
        before = _stamp(path)
        info = path.stat()
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise _Refused("no such file") from None
    except OSError as exc:
        raise _Refused(f"unreadable: {type(exc).__name__}") from None
    try:
        doc = json.loads(raw)
    except ValueError:
        raise _Refused("not JSON") from None
    if not isinstance(doc, dict):
        raise _Refused("top level is not an object")
    projects = doc.get("projects", {})
    if not isinstance(projects, dict):
        raise _Refused("projects is not an object")
    entry = projects.get(key, {})
    if not isinstance(entry, dict):
        raise _Refused("the folder's entry is not an object")
    if entry.get(KEY) is True:
        return ALREADY
    doc["projects"] = {**projects, key: {**entry, KEY: True}}
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    if raw.endswith("\n"):
        text += "\n"
    if between is not None:
        between(path)
    try:
        now = _stamp(path)
    except OSError:
        now = None
    if now != before:
        raise _Changed()
    try:
        files.atomic_write(path, text.encode("utf-8"), mode=info.st_mode & 0o7777)
    except OSError as exc:
        raise _Refused(f"unwritable: {type(exc).__name__}") from None
    return SEEDED


__all__ = ["ALREADY", "CONFIG_FILE", "EVENT", "HOME_DIR", "KEY",
           "REFUSED", "SEEDED", "Seeded", "config_file", "home_of", "seed",
           "settings_file", "trust_root"]
