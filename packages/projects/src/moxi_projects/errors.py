"""The errors projects' own acts raise, beside the read API's.

Each is a `moxi_projects.api.ProjectsError`, carrying `reason`, `fix` and `exit_code`:

- `Refused` (exit 3): the act was declined on what it found: a bad or taken key, a taken
  root, an unknown kind, a kind's check, a remove refusal.
- `NoGit` (exit 3), a `Refused`: `add --create` for a `git` mint found no `git` on PATH.
- `Broken` (exit 1): a contribution the act depends on won't load, raises, collides or
  answers out of shape. `modules` names the modules at fault.
- `Usage` (exit 2): the act was asked wrongly, such as an add with no root and nothing to
  supply one.
- `MintFailed` (exit 1): `add --create` could not make the root: the directory could not
  be made, or `git init` failed or timed out. No row was written; `left` is the directory
  left behind, or None.
- `MoveFailed` (exit 1): `promote` could not move the inbox one-off's folder to its new
  place. Nothing was moved and no row was written.

The read API's `NotFound` (3), `Unreadable` (1), `TooNew` (3) and `Busy` (75) are raised
by the acts too.
"""

from __future__ import annotations

from .api import Busy, NotFound, ProjectsError, TooNew, Unreadable


class Refused(ProjectsError):
    """The act was declined on what it found."""

    exit_code = 3


class NoGit(Refused):
    """`add --create` for a `git` mint found no `git` on PATH. Nothing was made."""


class Broken(ProjectsError):
    """A contribution the act depends on is at fault. `modules` names them."""

    exit_code = 1

    def __init__(self, modules: tuple[str, ...] | list[str] | str, reason: str,
                 fix: str | None = None):
        self.modules = (modules,) if isinstance(modules, str) else tuple(modules)
        super().__init__(reason, fix)


class Usage(ProjectsError):
    """The act was asked wrongly."""

    exit_code = 2


class MintFailed(ProjectsError):
    """`add --create` could not make the root. No row was written. `left` is the
    directory left behind, or None when nothing was."""

    exit_code = 1

    def __init__(self, reason: str, fix: str | None = None, left: str | None = None):
        self.left = left
        super().__init__(reason, fix)


class MoveFailed(ProjectsError):
    """`promote` could not move the one-off's folder. Nothing was moved and no row was
    written."""

    exit_code = 1


__all__ = ["ProjectsError", "NotFound", "Unreadable", "TooNew", "Busy", "Refused", "NoGit",
           "Broken", "Usage", "MintFailed", "MoveFailed"]
