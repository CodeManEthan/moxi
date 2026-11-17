"""How long each harness keeps the transcripts sessions reads: `retention()`.

sessions reads transcripts where each harness keeps them, so nothing outlives a harness's
own cleanup, and every reader of sessions loses what a harness deletes. `retention()` reads
each harness's setting as it is set and changes nothing:

* Claude Code: `cleanupPeriodDays` in `settings.json` under `${CLAUDE_CONFIG_DIR:-~/.claude}`.
  Claude Code deletes transcripts older than that many days; unset, 30.
* Codex: no setting deletes rollouts by age. Two things take a rollout out of sessions'
  reach: the switch `features.local_thread_store_compression` in
  `${CODEX_HOME:-~/.codex}/config.toml`, which compresses rollouts to `.jsonl.zst` (sessions
  lists those but reads none), and archiving a thread, which moves its rollout under
  `archived_sessions/` beside the sessions root, where sessions doesn't read.
* OpenCode: sessions reads no retention setting for it.

For Claude Code and Codex it also names the archive folders sessions' config gives
(`claude.archives`, `codex.archives`) in `archive_dirs` and a line, without touching them:
what a folder holds stays as long as whoever keeps it keeps it, and sessions never changes
it.

Each harness's answer is a `Retention`, with `lines` that a setup prints and `brief`, the
doctor's words. The files are found over the environment (this process's unless one is
handed in), not through sessions' root keys: a configured root moves where sessions reads,
not where the harness keeps its settings.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .shape import CLAUDE, CODEX, OPENCODE

#: Claude Code's setting, and the days it keeps transcripts when the setting is unset.
CLAUDE_KEY = "cleanupPeriodDays"
CLAUDE_DEFAULT_DAYS = 30
#: Codex's compression switch, as a dotted key in its `config.toml`.
CODEX_KEY = "features.local_thread_store_compression"
#: Where Codex moves an archived thread's rollout, under its home.
CODEX_ARCHIVE = "archived_sessions"
#: The line that comes before the harnesses' lines.
HEAD = ("sessions reads transcripts where each harness keeps them, so nothing outlives a "
        "harness's own cleanup.")


@dataclass(frozen=True)
class Retention:
    """One harness's retention as set.

    * `harness`: `claude`, `codex` or `opencode`.
    * `file`: the settings file read (None for OpenCode); `key`: the setting read there.
    * `value`: the setting as written, None when the file doesn't set it or doesn't exist.
    * `reason`: why the file could not be read or parsed, else None.
    * `archive` and `archived`: Codex's archive folder and the rollouts in it now (None
      when it can't be listed); None for the others.
    * `archive_dirs`: the archive folders sessions' config names for the harness, in the
      order read; none for OpenCode. Named, never opened.
    * `warn`: true when something here takes transcripts from sessions that the user may
      not expect: a file that can't be read, Codex's compression switched on, or a
      `cleanupPeriodDays` that is not a whole number of days, 1 or more.
    * `lines`: what a setup prints, each a plain sentence; `brief`: one short clause for the
      doctor.
    """

    harness: str
    file: str | None
    key: str | None
    value: Any
    reason: str | None
    warn: bool
    lines: tuple[str, ...]
    brief: str
    archive: str | None = None
    archived: int | None = None
    archive_dirs: tuple[str, ...] = ()


def _home(env: Mapping[str, str]) -> str:
    return env.get("HOME") or str(Path.home())


def _under(variable: str, default: str, env: Mapping[str, str]) -> Path:
    home = _home(env)
    value = env.get(variable)
    if not value:
        return Path(home) / default
    if value == "~" or value.startswith("~/"):
        value = home + value[1:]
    return Path(value)


def claude_settings(env: Mapping[str, str] | None = None) -> Path:
    """Claude Code's user settings file: `${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json`."""
    env = os.environ if env is None else env
    return _under("CLAUDE_CONFIG_DIR", ".claude", env) / "settings.json"


def codex_home(env: Mapping[str, str] | None = None) -> Path:
    """Codex's home: `${CODEX_HOME:-~/.codex}`."""
    return _under("CODEX_HOME", ".codex", os.environ if env is None else env)


def _why(exc: Exception) -> str:
    return getattr(exc, "strerror", None) or str(exc)


def _read(path: Path, parse) -> tuple[Any, str | None]:
    """The parsed file, None when it doesn't exist, or the reason it can't be read."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"cannot be read: {_why(exc)}"
    try:
        return parse(data), None
    except (ValueError, UnicodeDecodeError) as exc:
        return None, f"cannot be parsed: {exc}"


def _claude(env: Mapping[str, str]) -> Retention:
    path = claude_settings(env)
    data, reason = _read(path, lambda b: json.loads(b.decode("utf-8")))
    if reason is None and data is not None and not isinstance(data, dict):
        reason = "does not hold a JSON object"
    file = str(path)
    default = f"unset, Claude Code keeps transcripts {CLAUDE_DEFAULT_DAYS} days"
    raise_it = (f"To keep transcripts longer, raise {CLAUDE_KEY} in {file}; "
                "sessions changes no harness setting.")
    if reason is not None:
        return Retention(CLAUDE, file, CLAUDE_KEY, None, f"{file} {reason}", warn=True, lines=(
            f"Claude Code: {file} {reason}, so its {CLAUDE_KEY} is not known ({default}).",
            raise_it), brief=f"Claude Code: {file} {reason}")
    value = data.get(CLAUDE_KEY) if isinstance(data, dict) else None
    if value is None:
        unset = (f"{CLAUDE_KEY} is not set in {file}" if data is not None else
                 f"{file} doesn't exist, so {CLAUDE_KEY} is not set")
        return Retention(CLAUDE, file, CLAUDE_KEY, None, None, warn=False, lines=(
            f"Claude Code: {unset}: {CLAUDE_DEFAULT_DAYS} days. "
            f"Claude Code deletes transcripts older than that.", raise_it),
            brief=f"Claude Code: {CLAUDE_KEY} not set, {CLAUDE_DEFAULT_DAYS} days")
    if isinstance(value, bool) or not isinstance(value, int):
        return Retention(CLAUDE, file, CLAUDE_KEY, value, None, warn=True, lines=(
            f"Claude Code: {CLAUDE_KEY} is {json.dumps(value)} in {file}, which is not a "
            "whole number of days.", raise_it),
            brief=f"Claude Code: {CLAUDE_KEY} is {json.dumps(value)}, not a whole number")
    if value < 1:
        return Retention(CLAUDE, file, CLAUDE_KEY, value, None, warn=True, lines=(
            f"Claude Code: {CLAUDE_KEY} is {value} in {file}, which is below one day, so how "
            "long Claude Code keeps transcripts is not known.", raise_it),
            brief=f"Claude Code: {CLAUDE_KEY} is {value}, below one day")
    return Retention(CLAUDE, file, CLAUDE_KEY, value, None, warn=False, lines=(
        f"Claude Code: {CLAUDE_KEY} is {value} in {file}. Claude Code deletes transcripts "
        f"older than {value} days.", raise_it),
        brief=f"Claude Code: {CLAUDE_KEY} {value} days")


def _count_rollouts(rollouts_dir: Path) -> int | None:
    if not rollouts_dir.is_dir():
        return 0 if not rollouts_dir.exists() else None
    count = 0
    try:
        for _, _, names in os.walk(rollouts_dir, onerror=_raise):
            count += sum(1 for name in names if name.startswith("rollout-"))
    except OSError:
        return None
    return count


def _raise(exc: OSError) -> None:
    raise exc


def _codex(env: Mapping[str, str]) -> Retention:
    home = codex_home(env)
    path = home / "config.toml"
    file = str(path)
    archive = home / CODEX_ARCHIVE
    archived = _count_rollouts(archive)
    held = ("it holds no rollout" if archived == 0 else
            f"it holds {archived} rollout{'s' if archived != 1 else ''}"
            if archived is not None else "it cannot be listed")
    archive_line = (f"An archived Codex thread's rollout moves to {archive} ({held}), which "
                    "sessions doesn't read.")
    no_age = "Codex: no setting deletes rollouts by age."
    data, reason = _read(path, lambda b: tomllib.loads(b.decode("utf-8")))
    if reason is not None:
        return Retention(CODEX, file, CODEX_KEY, None, f"{file} {reason}", warn=True, lines=(
            no_age, f"Codex: {file} {reason}, so whether it compresses rollouts is not known.",
            archive_line), brief=f"Codex: {file} {reason}", archive=str(archive),
            archived=archived)
    features = data.get("features") if isinstance(data, dict) else None
    value = features.get(CODEX_KEY.split(".", 1)[1]) if isinstance(features, dict) else None
    if value is True:
        return Retention(CODEX, file, CODEX_KEY, value=True, reason=None, warn=True, lines=(
            no_age,
            f"Codex: {CODEX_KEY} is on in {file}: Codex compresses rollouts to .jsonl.zst, "
            "which sessions finds but can't read.", archive_line),
            brief="Codex: compression on, new rollouts can't be read", archive=str(archive),
            archived=archived)
    if value is not None and not isinstance(value, bool):
        return Retention(CODEX, file, CODEX_KEY, value, None, warn=True, lines=(
            no_age, f"Codex: {CODEX_KEY} is {value!r} in {file}, which is not true or "
            "false.", archive_line),
            brief=f"Codex: {CODEX_KEY} is {value!r}, not true or false", archive=str(archive),
            archived=archived)
    said = "off" if value is False else "not set (off)"
    return Retention(CODEX, file, CODEX_KEY, value, None, warn=False, lines=(
        no_age, f"Codex: {CODEX_KEY} is {said} in {file}.", archive_line),
        brief=f"Codex: no age limit, compression {said}, archive {held.removeprefix('it ')}",
        archive=str(archive), archived=archived)


def _opencode() -> Retention:
    return Retention(OPENCODE, None, None, None, None, warn=False,
                     lines=("OpenCode: sessions reads no retention setting for it.",),
                     brief="OpenCode: no setting read")


#: How the lines name a harness that has archive folders.
_LABELS = {CLAUDE: "Claude Code", CODEX: "Codex"}


def _with_archives(item: Retention) -> Retention:
    """`item` with the archive folders sessions' config names for its harness, named and
    never opened."""
    from moxi.config import ConfigError

    from .archives import archive_dirs

    label = _LABELS[item.harness]
    try:
        asked_dirs = archive_dirs(item.harness)
    except ConfigError as exc:
        return replace(item, warn=True, lines=(*item.lines, (
            f"{label}: sessions' config can't be read, so its archive folders are not "
            f"known: {exc}")), brief=f"{item.brief}; archive folders not known")
    if not asked_dirs:
        return item
    many = len(asked_dirs) != 1
    return replace(item, archive_dirs=asked_dirs, lines=(*item.lines, (
        f"{label}: sessions also reads {len(asked_dirs)} archive folder{'s' if many else ''} "
        f"after the live root, in this order: {', '.join(asked_dirs)}. What an archive "
        "folder holds stays as long as whoever keeps it keeps it; sessions never "
        "writes, moves or deletes anything there.")),
        brief=f"{item.brief}; {len(asked_dirs)} archive folder{'s' if many else ''}")


def retention(env: Mapping[str, str] | None = None) -> tuple[Retention, ...]:
    """Each harness's retention as set, Claude Code, Codex, OpenCode in that order, over
    `env` (this process's when None), with the archive folders sessions' config names.
    Reads two small files and lists one folder; opens no archive folder and writes
    nothing."""
    env = os.environ if env is None else env
    return (_with_archives(_claude(env)), _with_archives(_codex(env)), _opencode())


def lines(env: Mapping[str, str] | None = None) -> list[str]:
    """`HEAD`, then every harness's lines: what a setup prints."""
    out = [HEAD]
    for item in retention(env):
        out.extend(item.lines)
    return out


__all__ = ["CLAUDE_DEFAULT_DAYS", "CLAUDE_KEY", "CODEX_ARCHIVE", "CODEX_KEY", "HEAD",
           "Retention", "claude_settings", "codex_home", "lines", "retention"]
