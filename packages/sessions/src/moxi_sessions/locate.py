"""Which harness holds a session, and which source of it to read: what
`moxi sessions read` and the chat's `read_session` both open.

`locate(harness, session)` asks as the read does. With `harness` named, that harness's
live root, then its archive folders. Without it, every harness's live root in turn with
`find`, which looks at names and, for OpenCode, one row, never a whole transcript; only
when none holds the session are the archive folders of every harness tested together,
once, and asked. It decides nothing: the caller words what it found.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import claude
from .archives import archive_answers, archive_dirs
from .find import Found, find
from .shape import HARNESSES
from .source import Source


@dataclass(frozen=True)
class Located:
    """What `locate` found.

    * `harness` and `source`: the one harness that holds the session and the source to
      read, or None for both when no harness does or more than one does.
    * `held`: every harness that holds it, in `HARNESSES`' order.
    * `offline`: `(folder, why)` for each archive folder asked that didn't answer. With
      `source` set, a harness not asked through it may hold the session too.
    * `probed`: each root asked and its outcome, in words.
    """

    harness: str | None
    source: Source | None
    held: tuple[str, ...]
    offline: tuple[tuple[str, str], ...]
    probed: str


def _probed(found: Found) -> str:
    return "; ".join(f"{probe.root}: {probe.outcome}" for probe in found.probes)


def pick(found: Found) -> Source:
    """The source to read of what `find` found: the first that isn't compressed, else the
    first, whose read then says why it can't be read."""
    hits = found.hits
    return next((h for h in hits if not h.compressed), hits[0]).source


def _offline(asked, asked_dirs) -> tuple[tuple[str, str], ...]:
    return tuple((probe.root, probe.outcome.partition(": ")[2] or probe.outcome)
                 for found in asked for probe in found.probes
                 if probe.root in asked_dirs and probe.outcome.startswith(claude.UNREADABLE))


def locate(harness: str | None, session: str) -> Located:
    """Where `session` is, as `moxi sessions read` finds it."""
    if harness is not None:
        found = find(harness, session, include_archives=True)
        offline = _offline([found], set(archive_dirs(harness)))
        if found.found:
            return Located(harness, pick(found), (harness,), offline, _probed(found))
        return Located(None, None, (), offline, _probed(found))
    asked = [find(each, session) for each in HARNESSES]
    held = [found for found in asked if found.found]
    asked_dirs: list[str] = []
    if not held:
        asked_dirs = [archive_dir for each in HARNESSES for archive_dir in archive_dirs(each)]
        if asked_dirs:
            archive_answers(asked_dirs)
            asked = [find(each, session, include_archives=True) for each in HARNESSES]
            held = [found for found in asked if found.found]
    names = tuple(found.harness for found in held)
    probed = "; ".join(f"{found.harness}: {_probed(found)}" for found in asked)
    if len(held) == 1:
        offline = _offline([found for found in asked if not found.found], set(asked_dirs))
        return Located(held[0].harness, pick(held[0]), names, offline, probed)
    return Located(None, None, names, _offline(asked, set(asked_dirs)), probed)


__all__ = ["Located", "locate", "pick"]
