"""What the page says of the loadout folder itself: the files in it that git has not
committed, the items in it that no section lists, and whether a commit installs it again.

- `uncommitted(loadout_dir, git)`: when the folder is in a git work tree
  (`repo_link.in_work_tree`), each file `git status` lists under it, untracked files
  inside an untracked folder one by one, made relative to the folder; a renamed or copied
  file by its new path. git runs with `--no-optional-locks`, so the read never writes the
  index, and with the `ignores.Git` bounds the caller gives it. Raises `ignores.GitError`
  when git fails or doesn't answer.
- `unlisted(found)`: each `skills/<name>/` holding `SKILL.md` and each `agents/<name>.md`
  in the folder that no section of the manifest lists (where it can be written or not),
  by kind and name, skills first.
  A name the manifest couldn't take is left out, since `add` would refuse it.
- `reinstalls(loadout_dir, ...)`: whether repo's git hook pieces install the folder again
  on a commit: the doctor's row for the link (`repo_link.state`) reads `ok`.

Reads only; writes nothing.
"""

from __future__ import annotations

import os

from moxi_loadout.manifest import name_problem

#: The most uncommitted files the answer names; `more` counts the rest.
UNCOMMITTED_MAX = 20


def uncommitted(loadout_dir: str | os.PathLike, git) -> list[str] | None:
    """The files under `loadout_dir` that git has not committed, relative to it, in git's
    order, or None when the folder is in no git work tree. See the module's docs."""
    from moxi_loadout import repo_link
    from moxi_loadout.ignores import GitError

    if not repo_link.in_work_tree(loadout_dir):
        return None
    here = os.fspath(loadout_dir)
    done = git.run(["git", "-C", here, "rev-parse", "--show-prefix"], None)
    if done.returncode != 0:
        raise GitError(f"git rev-parse exited {done.returncode}")
    prefix = done.stdout.decode("utf-8", "replace").strip()
    done = git.run(["git", "--no-optional-locks", "-C", here, "status", "--porcelain", "-z",
                    "--untracked-files=all", "--", "."], None)
    if done.returncode != 0:
        raise GitError(f"git status exited {done.returncode}")
    return _paths(done.stdout.decode("utf-8", "replace"), prefix)


def _paths(listed: str, prefix: str) -> list[str]:
    """The paths of `git status --porcelain -z`'s records, made relative to the folder
    `prefix` names within the work tree."""
    out = []
    fields = iter(listed.split("\0"))
    for record in fields:
        if len(record) < 4:
            continue
        code, path = record[:2], record[3:]
        if "R" in code or "C" in code:
            next(fields, None)  # the path it was renamed or copied from
        out.append(path[len(prefix):] if prefix and path.startswith(prefix) else path)
    return out


def unlisted(found) -> list[dict]:
    """Each skill and agent file in the folder of manifest `found` that no section lists:
    `{kind, name, source}`. See the module's docs."""
    listed = {(kind, name) for section in found.sections()
              for kind, names in section.lists.items() for name in names}
    listed |= {(r.kind, r.name) for r in found.refused}
    out = []
    for kind, name, source in (*_skills(found.loadout_dir), *_agents(found.loadout_dir)):
        if (kind, name) not in listed and name_problem(kind, name) is None:
            out.append({"kind": kind, "name": name, "source": source})
    return out


def _entries(path) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as found:
            return sorted(found, key=lambda e: e.name)
    except OSError:
        return []


def _skills(loadout_dir):
    for entry in _entries(os.path.join(loadout_dir, "skills")):
        if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "SKILL.md")):
            yield "skill", entry.name, entry.path


def _agents(loadout_dir):
    for entry in _entries(os.path.join(loadout_dir, "agents")):
        stem, ext = os.path.splitext(entry.name)
        if ext == ".md" and stem and entry.is_file():
            yield "agent", stem, entry.path


def reinstalls(loadout_dir: str | os.PathLike, *, enabled: bool, seconds: float) -> bool:
    """Whether a commit to `loadout_dir` installs it again: repo installed, the setting
    `enabled`, and both pieces registered and on, as repo reads them within `seconds`."""
    from moxi_loadout import repo_link

    try:
        return repo_link.state(loadout_dir, enabled=enabled, seconds=seconds).state == "ok"
    except Exception:  # noqa: BLE001 - the doctor's row says why; the page only says yes
        return False


__all__ = ["UNCOMMITTED_MAX", "uncommitted", "unlisted", "reinstalls"]
