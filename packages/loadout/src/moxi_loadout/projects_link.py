"""The optional link to projects: a project section named by key resolved to its root.

projects is reached in process, never by a subprocess: `moxi.modules.available
("projects")` first, then `load("projects", interface=1)` and its API's `get(key)`, at
the point of use, so nothing of projects is imported unless a key section is read.

Rule: a projects error refuses the key sections it touches; their items are carried as
they stand, so their placements stay, and the rest of the run goes on. None of these
plans a `remove`.

- `NotFound` refuses that one section (exit 3).
- `Unreadable` and `TooNew` refuse every key section, with the reason and the fix
  (exit 3).
- `Busy` refuses every key section, "projects is busy; run again" (exit 75).
- Without projects every key section is refused with "name it by path, or moxi install
  projects" (exit 3).

projects' own `exit_code` is not used: the codes above are loadout's, by error class.
"""

from __future__ import annotations

import os

from moxi.exit import REFUSED, TEMPFAIL

#: The projects interface loadout reads.
INTERFACE = 1
ABSENT = "a project key needs projects; name it by path, or moxi install projects"


def api():
    """projects' read API, or a line saying why there is none (`(api, None)` or
    `(None, line)`)."""
    from moxi import modules

    if not modules.available("projects"):
        return None, ABSENT
    found = modules.load("projects", interface=INTERFACE)
    if found.state == "absent":
        return None, ABSENT
    if not found.ok or found.api is None:
        return None, f"projects can't be loaded ({found.reason}); moxi doctor says more"
    return found.api, None


def resolve(key: str):
    """The root of project `key`, as a `moxi_loadout.planner.Resolution`."""
    from moxi_loadout.planner import Resolution

    link, why = api()
    if link is None:
        return Resolution(None, f"project {key}: {why}", REFUSED,
                          every=why != ABSENT)
    try:
        project = link.get(key)
    except link.Busy:
        return Resolution(None, "projects is busy; run again", TEMPFAIL, every=True)
    except link.TooNew as exc:
        return Resolution(None, f"projects can't be read: {exc}", REFUSED, every=True)
    except link.Unreadable as exc:
        return Resolution(None, f"projects can't be read: {exc}", REFUSED, every=True)
    except link.NotFound as exc:
        return Resolution(None, f"project {key}: {exc}", REFUSED)
    root = getattr(project, "root", None)
    if not root:
        return Resolution(None, f"project {key} has no root; moxi projects shows it",
                          REFUSED)
    return Resolution(os.path.abspath(os.path.expanduser(str(root))))


__all__ = ["INTERFACE", "ABSENT", "api", "resolve"]
