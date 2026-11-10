"""The frozen versions of a round's design, as its log holds them.

A design is frozen into `versions/design-v<N>.md` in the round's folder, `N` from 1, and
the log holds each version: its number, the SHA-256 of the frozen file, what the design's
block declared, and what the block took away. The functions here read those records
from a round's folded state:

- `current(state)`, `find(state, n)`: the newest version, and the one numbered `n`;
- `digest(state, n=None)`: a version's digest, the current one's when no number is given;
- `declarations(state, n=None)`: what a version's block declared;
- `pending_removals(state, n)`: what version `n` took away, and `said(pending)` one of
  those in a line;
- `unchanged(state, folder, name)`: the condition that `design.md` is as it was when the
  current version was frozen, for a gate that needs the design verified.

What a block takes away (a question, a stage, a stage another waited for, a stage a
question held up) changes nothing when it is frozen. It is kept with the version that
carries it, and is applied by a verdict that finds that version publishable or by the
operator's ruling.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import questions
from . import state as states
from .errors import Unmet

#: The file a round's design is written in, in the round's folder.
DESIGN_FILE = "design.md"


def file(folder: Path | str, n: int) -> Path:
    """Where version `n` of the design of the round in `folder` is frozen."""
    return Path(folder) / states.FROZEN_FILE.format(n=n)


def current(state: states.State) -> states.Version | None:
    """The newest frozen version, or None for a design never frozen."""
    return state.versions[-1] if state.versions else None


def find(state: states.State, n: int) -> states.Version | None:
    """The version numbered `n`, or None when no such version was frozen."""
    for version in state.versions:
        if version.n == n:
            return version
    return None


def _one(state: states.State, n: int | None) -> states.Version | None:
    return current(state) if n is None else find(state, n)


def digest(state: states.State, n: int | None = None) -> str | None:
    """The SHA-256 of the frozen file of version `n`, the current version when `n` is
    left out; None when there is no such version."""
    version = _one(state, n)
    return None if version is None else version.digest


def declarations(state: states.State, n: int | None = None) -> dict:
    """What the block of version `n` declared, the current version's when `n` is left
    out: `question`, `stage`, `trace` and `commitment`, each a list of entries as the
    block gave them. Empty when there is no such version."""
    version = _one(state, n)
    return {} if version is None else version.declared


def pending_removals(state: states.State, n: int) -> tuple[states.Pending, ...]:
    """What version `n` took away, as it was found when the version was frozen: each
    question and stage its block left out, each stage one of its stages waits for no
    longer, and each question whose block entry holds up fewer stages than the record.
    None of it is applied by the freezing. Whether each still stands when it comes to
    be applied (a question since ruled, a stage since built) is for whoever applies
    it to read from the record as it is then."""
    version = find(state, n)
    return () if version is None else version.pending


def said(pending: states.Pending) -> str:
    """One thing a version took away, in a line."""
    if pending.what == states.QUESTION:
        return f"the question {pending.id} is gone from the block"
    if pending.what == states.STAGE:
        return f"the stage {pending.id} is gone from the block"
    if pending.what == states.AFTER:
        return f"{pending.id} waits for {', '.join(pending.gone)} no longer"
    blocks = pending.blocks
    holds = blocks if isinstance(blocks, str) else ", ".join(blocks or ())
    return f"{pending.id} holds up fewer stages: {holds}"


# -- what the index keeps of a round's plan ----------------------------------------------

def stages(state: states.State):
    """Each stage of the round's plan, as `(id, body)`, for the index: what it builds
    on or the round it stands for, and what it waits for. A stage that left the plan
    is not listed."""
    out = questions.left_stages(state)
    for stage in state.stages:
        if stage.id not in out:
            yield stage.id, {"repo": stage.repo, "branch": stage.branch,
                             "round": stage.round, "after": list(stage.after)}


def newest(state: states.State):
    """The current version, as one `(id, body)` for the index, or nothing for a design
    never frozen."""
    version = current(state)
    if version is not None:
        yield f"v{version.n}", {"version": version.n, "digest": version.digest,
                                "at": version.at, "by": version.actor,
                                "records_only": version.records_only}


def waiting(state: states.State):
    """What the current version took away and so waits for a verdict or a ruling, each
    as `(id, body)` for the index."""
    version = current(state)
    for one in () if version is None else version.pending:
        yield f"v{version.n}:{one.what}:{one.id}", {
            "version": version.n, "what": one.what, "id": one.id,
            "gone": list(one.gone),
            "blocks": list(one.blocks) if isinstance(one.blocks, tuple) else one.blocks,
            "said": said(one)}


def unchanged(state: states.State, folder: Path | str, name: str) -> Unmet | None:
    """The unmet condition of a design that is not the one frozen last, or None when
    `design.md` in `folder`, the folder of the round `name`, has the bytes the current
    version was frozen from."""
    again = f"moxi rounds fold {name}"
    version = current(state)
    if version is None:
        return Unmet(f"the design of {name} was never frozen", again)
    try:
        now = hashlib.sha256((Path(folder) / DESIGN_FILE).read_bytes()).hexdigest()
    except OSError:
        now = None
    if now == version.digest:
        return None
    return Unmet(f"{DESIGN_FILE} changed since v{version.n} was frozen", again)


__all__ = ["DESIGN_FILE", "file", "current", "find", "digest", "declarations",
           "pending_removals", "said", "stages", "newest", "waiting", "unchanged"]
