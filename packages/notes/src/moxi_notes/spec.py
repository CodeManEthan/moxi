"""notes' one committer spec, built from notes' config at every pass notes starts.

`build()` gives the repo `Spec` named `notes/snapshot`: the whole notes root, notes'
prepare step, the interval, the push remote and the size cap from config, and an author
of this device's own. The author is per device because the committer's cadence reads
this author's last commit on any ref, so a snapshot fetched from another machine never
throttles this one. The email's `.invalid` domain is never a real address.

`ensure_saved(spec)` compares the spec with the one saved in the notes repo (one
`git config` read) and saves it when they differ, so a config edit takes effect at the
next pass notes starts. `moxi repo commit` runs the spec as it was last saved.

The committer's names come from `moxi_repo.api`, the one place notes reaches repo.
"""

from __future__ import annotations

import dataclasses
import os

from . import settings as _settings

#: The spec's name in the notes repo's git config.
NAME = "notes/snapshot"
#: notes' prepare step, which judges each staged file before the commit.
PREPARE = "moxi_notes.prepare:prepare"
#: The whole tree.
PATHS = (".",)
#: Seconds in one minute of `snapshot.interval`; tests shorten it.
MINUTE = 60
MIB = 1024 * 1024


def author(settings) -> tuple[str, str]:
    """The snapshot author: `snapshot.author_name`, and `snapshot.author_email` or
    `notes+<first 8 of this device's id>@moxi.invalid`."""
    email = settings["snapshot.author_email"]
    if not email:
        from moxi import paths

        email = f"notes+{paths.device_id()[:8]}@moxi.invalid"
    return settings["snapshot.author_name"], email


def build(places=None):
    """The spec notes' config gives now. Raises `moxi.config.ConfigError`."""
    from moxi_repo.api import Spec

    places = places or _settings.read()
    s = places.settings
    return Spec(name=NAME, root=places.root, paths=PATHS, author=author(s),
                min_interval_s=s["snapshot.interval"] * MINUTE, message=None,
                prepare=PREPARE, push=s["snapshot.push"] or None,
                max_bytes=s["max_mb"] * MIB)


def _plain(spec):
    return dataclasses.replace(spec, root=os.path.realpath(spec.root))


def same(a, b) -> bool:
    """True when two specs run the same, their roots compared by real path."""
    return a is not None and b is not None and _plain(a) == _plain(b)


def ensure_saved(spec) -> bool:
    """Save `spec` in its repo unless the saved one is the same; True when it saved.

    The committer's lock is tried once: `moxi.files.LockTimeout` when a pass holds it.
    Raises `SpecError` when the root is no work tree's top or the spec can't be saved."""
    from moxi_repo.api import SpecError, save_spec, saved_spec

    try:
        saved = saved_spec(spec.root, spec.name)
    except SpecError:
        # A section that can't be read is replaced whole; a root that is no work tree's
        # top makes save_spec raise the same error.
        saved = None
    if same(saved, spec):
        return False
    save_spec(spec, wait_s=0)
    return True


__all__ = ["NAME", "PREPARE", "PATHS", "author", "build", "same", "ensure_saved"]
