"""The global instruction text: the shared file, each harness's part, and the text a
harness gets from them.

The shared file is `[global] file` (`global.md` by default). A harness's part is the file
named like it with `.<harness>` before its last suffix, in the same folder:
`global.claude.md`, `global.codex.md`, `global.opencode.md`; a shared file with no suffix
gets `.<harness>` appended. The manifest names no part. A part is in use when a file is at
its path, whatever its text, so creating the file switches the part on and deleting it
switches it off. Only the harnesses loadout writes a global file for have parts.

A file named like a part whose middle word is a row of the core's harness table loadout
writes no global file for, or is one edit from `claude`, `codex` or `opencode` without
being one, is noted as not read (`mistyped`). `global.old.md` is not.

The assembled text (`assemble`) is what the Codex and OpenCode blocks and an `if-absent`
generated file hold: the shared text, then the part's; each loses its trailing newlines,
any left empty or only whitespace is dropped, and the rest are joined with one blank line.

A generated global file (`STATE/loadout/generated/global/<harness>.md`, `generated_path`)
is one marker line naming the files assembled and the sha256 of every byte after that
line's newline, then the assembled text and one final newline (only the marker line and
its newline when the text is empty). It is loadout's and unedited when its first line is
such a marker and the hash matches (`generated_state`).

The real-path rule (`crossed`): before a harness global file is edited, its real path is
resolved. A file that is a link to the shared file, to its own harness's part, or to its
own generated file is left. Two harness files that are one real file other than the
shared file, and a harness file that is another harness's part or generated file, are
blocked, since one harness would read the other's part.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from moxi_loadout import harness as hz

MARKER = "<!-- moxi loadout: from {}; edit there -->"
GENERATED_MARKER = "<!-- moxi loadout: from {}; sha256 {}; edit there -->"
_GENERATED_RE = re.compile(r"<!-- moxi loadout: from (.+); sha256 ([0-9a-f]{64}); "
                           r"edit there -->")
END = "<!-- moxi loadout end -->"


def part_path(shared: str | os.PathLike, harness: str) -> Path:
    """`harness`'s part beside `shared`: `.<harness>` before the last suffix."""
    path = Path(os.path.abspath(shared))
    suffix = path.suffix
    stem = path.name[:-len(suffix)] if suffix else path.name
    return path.with_name(f"{stem}.{harness}{suffix}")


def parts(shared: str | os.PathLike) -> dict[str, Path]:
    """Each harness's part that is in use: a file (or anything) at its path."""
    return {h: p for h in hz.HARNESSES if os.path.lexists(p := part_path(shared, h))}


def read(path: str | os.PathLike) -> str:
    """A part's or the shared file's text. Raises `OSError` or `UnicodeDecodeError`."""
    return Path(path).read_text(encoding="utf-8")


def assemble(texts: Iterable[str]) -> str:
    """The shared text then the part's: trailing newlines trimmed, empty ones dropped,
    joined with one blank line; no trailing newline."""
    kept = [t.rstrip("\n") for t in texts]
    return "\n\n".join(t for t in kept if t.strip())


def named(paths: Iterable[str | os.PathLike]) -> str:
    return " and ".join(os.path.abspath(p) for p in paths)


def block(paths: Iterable[str | os.PathLike], text: str) -> str:
    """The Codex and OpenCode block: the start marker naming every file in use (the
    shared file first), the assembled text and a newline when it isn't empty, the end
    marker. No trailing newline."""
    return MARKER.format(named(paths)) + "\n" + (text + "\n" if text else "") + END


def generated_path(harness: str) -> Path:
    from moxi_loadout import stores

    return stores.generated_dir() / "global" / f"{harness}.md"


def generated_bytes(paths: Iterable[str | os.PathLike], text: str) -> bytes:
    body = (text + "\n" if text else "").encode("utf-8")
    first = GENERATED_MARKER.format(named(paths), hashlib.sha256(body).hexdigest())
    return first.encode("utf-8") + b"\n" + body


def generated_state(path: str | os.PathLike) -> str | None:
    """A generated global file's state: None when missing or unreadable, `ok` when its
    first line is loadout's marker and the hash matches the rest, else `modified`."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    first, _, rest = data.partition(b"\n")
    match = _GENERATED_RE.fullmatch(first.decode("utf-8", "replace"))
    if match is None or hashlib.sha256(rest).hexdigest() != match.group(2):
        return "modified"
    return "ok"


def is_generated(path: str | os.PathLike) -> bool:
    from moxi_loadout import stores

    root = os.path.realpath(stores.generated_dir() / "global")
    return os.path.dirname(os.path.realpath(path)) == root


# -- a file that looks like a mistyped part ------------------------------------------------

def mistyped(shared: str | os.PathLike, table: Iterable[str]) -> list[Path]:
    """The files beside `shared` named like a part of a harness loadout writes no global
    file for (a row of the harness table `table` names), or one edit from a harness
    loadout does: none of them is read."""
    path = Path(os.path.abspath(shared))
    suffix = path.suffix
    stem = path.name[:-len(suffix)] if suffix else path.name
    others = set(table) - set(hz.HARNESSES)
    try:
        names = sorted(os.listdir(path.parent))
    except OSError:
        return []
    out = []
    for name in names:
        if not (name.startswith(stem + ".") and name.endswith(suffix)) or name == path.name:
            continue
        word = name[len(stem) + 1:len(name) - len(suffix)]
        if not word or "." in word or word in hz.HARNESSES:
            continue
        if word in others or any(_one_edit(word, h) for h in hz.HARNESSES):
            out.append(path.parent / name)
    return out


def _one_edit(a: str, b: str) -> bool:
    """Whether `a` and `b` differ by exactly one insertion, deletion or substitution."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i + (len(a) == len(b)):] == b[i + 1:]


# -- the real-path rule --------------------------------------------------------------------

@dataclass(frozen=True)
class Crossing:
    """Why a harness global file is not edited: `left` (a link to the shared file, its
    own part or its own generated file) or `blocked` (one real file with another
    harness's file, or another harness's part or generated file), with its `line`."""

    verdict: str
    line: str


def crossed(shared: str | os.PathLike, harnesses: Iterable[str]) -> dict[str, Crossing]:
    """The harness global files among `harnesses` that loadout must not edit, by
    harness. A harness absent from the result may be edited."""
    names = [h for h in harnesses if h in hz.HARNESSES]
    shared_real = os.path.realpath(shared)
    targets = {h: hz.global_file(h) for h in names}
    reals = {h: os.path.realpath(t) for h, t in targets.items() if os.path.lexists(t)}
    own = {h: {os.path.realpath(part_path(shared, h)): "part",
               os.path.realpath(generated_path(h)): "generated file"}
           for h in hz.HARNESSES}
    out: dict[str, Crossing] = {}
    for h, real in reals.items():
        if real == shared_real or real in own[h]:
            out[h] = Crossing("left", f"{targets[h]} links to {real}; loadout leaves it, "
                              f"and {h} reads what that file holds")
            continue
        theirs = next(((g, own[g][real]) for g in hz.HARNESSES if g != h and real in own[g]),
                      None)
        if theirs is not None:
            g, what = theirs
            out[h] = Crossing("blocked", f"{targets[h]} is {g}'s {what} ({real}), so {h} "
                              f"would read {g}'s part; loadout leaves it: make it a file "
                              "of its own")
            continue
        same = [g for g in reals if g != h and reals[g] == real]
        if same:
            files = " and ".join(str(targets[x]) for x in [h, *same])
            out[h] = Crossing("blocked", f"{files} are one file ({real}), so each harness "
                              "would read the other's part; loadout leaves them: make "
                              "them separate files")
    return out


__all__ = ["MARKER", "GENERATED_MARKER", "END", "part_path", "parts", "read", "assemble",
           "named", "block", "generated_path", "generated_bytes", "generated_state",
           "is_generated", "mistyped", "Crossing", "crossed"]
