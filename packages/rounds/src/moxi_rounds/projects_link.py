"""rounds' link with projects: the remove refusal.

projects reads this module through its `moxi.projects` entry-point group (interface 1)
and asks `refuse_remove(project)` before it removes a project. rounds answers with one
sentence while a build of any round, open, finished or dropped, has that project as
its repo: a build's record is the round's history, kept for good, and its repo is
read by its project key. So a project is never removed from under the rounds that
built in it.

The answer reads every round's log in every project's rounds folder and nothing else:
no write and no subprocess. A log that can't be read can't say whether a build of it
names the project, so it refuses the remove too, and the sentence names it.
"""

from __future__ import annotations

from . import builds, folders, log
from . import state as states
from .errors import NoLog, RoundsError

#: The version of projects' contribution contract this module implements.
INTERFACE = 1


def refuse_remove(project) -> str | None:
    """One sentence naming the builds whose repo is `project` (projects' `Project`)
    and the rounds that hold them, and the round logs that can't be read, or None when
    there is neither."""
    key = project.key.casefold()
    count, held, unreadable = 0, [], []
    homes, _unplaced = folders.homes()
    for home in homes:
        for slug in folders.slugs(home.rounds_dir):
            name = f"{home.project}/{slug}"
            try:
                events = log.read_round(log.round_log(home.round_dir(slug)))
                found = builds.builds(states.fold(events))
            except NoLog:  # removed since the folder was listed
                continue
            except RoundsError:
                unreadable.append(name)
                continue
            mine = [one for one in found if one.repo.casefold() == key]
            if mine:
                count += len(mine)
                held.append(name)
    said = []
    if count:
        said.append(f"project {project.key} is the repo of {count} "
                    f"build{'s' if count != 1 else ''} of {', '.join(held)}, open or "
                    "ended, and a build stays on its round's record for good, so the "
                    "project stays while they do")
    if unreadable:
        said.append(f"the round log{'s' if len(unreadable) != 1 else ''} of "
                    f"{', '.join(unreadable)} can't be read, so whether a build there "
                    "names it is not known; moxi doctor rounds names the way back")
    return "; ".join(said) or None


__all__ = ["INTERFACE", "refuse_remove"]
