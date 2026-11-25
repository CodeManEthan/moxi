"""What stands in a harness folder loadout places into that no placement holds, and the
two ways to clear one.

**The folders.** Each harness's skills and agents folders: the core's harness table for
the user section, and for each project section that resolved, its root joined with that
harness's `project_folders` entry. A folder that isn't there has nothing in it.

**A stray** is an entry of such a folder that no placement holds: no want of the plan
names it as a target, loadout's ledger has no record of it, and no owner's placement in
the core placer's ledger holds it (`moxi.place.placed_targets`), the module installed
now or not. An entry whose name starts with a dot is the harness's or the tools' own
(loadout's ignore file, the placer's temporary names, a harness's hidden folders) and is
never one, and nor is an entry git tracks: that is its repo's (a project's own skills),
and moving it would leave a deletion there. A folder in a work tree whose git doesn't
answer gives none. `find(plan, git)` gives them, each a `Stray` with its `path`, `harness`, `kind`
(`skill` in a skills folder, `agent` in an agents folder) and `section`, the section whose
folder it is in. Reads only.

**Taking one** (`take`): the stray is copied into the loadout folder as a source of its
kind under its name (a skill's folder as `skills/<name>/`, an agent's file as
`agents/<name>.md`), listed in the section's list (the stray's own section unless one is
named), and installed with that path named for `--replace`, so loadout's placement
stands where the stray stood and the stray's bytes are kept under the core's
`displaced/` folder first, as every loadout write over bytes it didn't write is. It
refuses a path that is no stray, a name the folder or the section already holds, a skill
that isn't a folder holding `SKILL.md`, and an agent file that isn't a `.md` source.

**Moving one aside** (`move_aside`): the stray moves under `displaced/` by the core's one
move (`moxi.place.move_aside`), and the outcome names the command that puts it back, as
`moxi_loadout.displaced.back` gives it for the platform.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from moxi_loadout.manifest import USER, ManifestError

#: The folder each kind's sources live in, under the loadout folder, and the folder name a
#: harness row gives it.
KIND_DIRS = {"skill": "skills", "agent": "agents"}
#: What an agent source's file name ends in.
AGENT_SUFFIX = ".md"


@dataclass(frozen=True)
class Stray:
    """One entry of a harness folder that no placement holds."""

    path: str
    harness: str
    kind: str
    section: str

    def as_json(self) -> dict:
        return {"path": self.path, "harness": self.harness, "kind": self.kind}


class Refused(Exception):
    """A `take` or `move_aside` that can't go ahead; its text says why."""


def folders(plan) -> list[tuple[str, str, str, Path]]:
    """Each folder loadout places into, as `(section, harness, kind, folder)`: the user
    section's from the core's harness table, then each resolved project section's."""
    from moxi import place

    table, _ = place.harnesses()
    out: list[tuple[str, str, str, Path]] = []
    for name, row in table.items():
        for kind in KIND_DIRS:
            folder = row.harness_dir(kind)
            if folder is not None:
                out.append((USER, name, kind, Path(folder)))
    for section, root in plan.roots.items():
        for name in table:
            for kind, dir_key in KIND_DIRS.items():
                rel = plan.project_dirs.get(name, {}).get(dir_key)
                if rel:
                    out.append((section, name, kind, Path(root) / rel))
    return out


def find(plan, git=None) -> list[Stray]:
    """Every stray, in the order of `folders` and then by name; `git` an
    `ignores.Git` (a fresh one when None). See the module's docs."""
    from moxi import place

    from moxi_loadout import ignores

    git = ignores.Git() if git is None else git

    held = set(place.placed_targets())
    held |= {place.target_key(k) for k in plan.ledger.get("placed", {})}
    held |= {place.target_key(w.step.target) for w in plan.wants
             if w.step is not None and w.step.target is not None}
    held |= {place.target_key(w.item.dest) for w in plan.wants
             if w.item is not None and getattr(w.item, "dest", None) is not None}
    seen: set[str] = set()
    out: list[Stray] = []
    for section, harness, kind, folder in folders(plan):
        key = place.target_key(folder)
        if key in seen:
            continue
        seen.add(key)
        try:
            names = sorted(n for n in os.listdir(folder) if not n.startswith("."))
        except OSError:
            continue
        tracked = _tracked(key, git) if names else set()
        if tracked is None:
            continue
        for name in names:
            if name in tracked:
                continue
            path = os.path.join(key, name)
            if place.target_key(path) in held:
                continue
            out.append(Stray(path, harness, kind, section))
    return out


def _tracked(folder: str, git) -> set[str] | None:
    """The names of `folder`'s entries git tracks (an entry holding a tracked file
    counts), empty outside a work tree, None when git doesn't answer."""
    from moxi_loadout import ignores, repo_link

    if not repo_link.in_work_tree(folder):
        return set()
    try:
        done = git.run(["git", "-C", folder, "ls-files", "-z", "--", "."], None)
    except ignores.GitError:
        return None
    if done.returncode != 0:
        return None
    files = done.stdout.decode("utf-8", "surrogateescape").split("\0")
    return {f.split("/", 1)[0] for f in files if f}


def stray_at(plan, path: str | os.PathLike) -> Stray:
    """The stray at `path`, or `Refused` when what is there is no stray."""
    from moxi import place

    key = place.target_key(os.path.abspath(os.path.expanduser(os.fspath(path))))
    for stray in find(plan):
        if place.target_key(stray.path) == key:
            return stray
    if not os.path.lexists(key):
        raise Refused(f"{key} is not there")
    raise Refused(f"{key} is no stray: it is not in a harness folder loadout places "
                  "into, or a placement holds it (moxi loadout status lists the strays)")


def source_name(stray: Stray) -> str:
    """The name the stray is taken under: a skill folder's name, an agent file's name
    less `.md`. `Refused` for what can't be a source of its kind."""
    from moxi_loadout.manifest import name_problem

    base = os.path.basename(stray.path)
    if stray.kind == "skill":
        if os.path.islink(stray.path) or not os.path.isfile(os.path.join(stray.path,
                                                                         "SKILL.md")):
            raise Refused(f"{stray.path} is not a skill folder holding SKILL.md")
        name = base
    else:
        if os.path.islink(stray.path) or not os.path.isfile(stray.path) \
                or not base.endswith(AGENT_SUFFIX):
            raise Refused(f"{stray.path} is not an agent file ending in {AGENT_SUFFIX}")
        name = base[:-len(AGENT_SUFFIX)]
    if problem := name_problem(stray.kind, name):
        raise Refused(f"{stray.path}: {problem}")
    return name


def copy_in(stray: Stray, name: str, loadout_dir: str | os.PathLike) -> Path:
    """Copy the stray into the loadout folder as a source of its kind under `name`;
    `Refused` when the folder already holds that name. Returns the source's path."""
    import shutil

    kind_dir = Path(loadout_dir) / KIND_DIRS[stray.kind]
    dest = kind_dir / (name if stray.kind == "skill" else name + AGENT_SUFFIX)
    if os.path.lexists(dest):
        raise Refused(f"the loadout folder holds {stray.kind} {name} already ({dest}); "
                      "move the stray aside, or remove that one first")
    kind_dir.mkdir(parents=True, exist_ok=True)
    if stray.kind == "skill":
        shutil.copytree(stray.path, dest, symlinks=True)
    else:
        shutil.copy2(stray.path, dest)
    return dest


def take(path: str | os.PathLike, *, project: str | None = None, user: bool = False,
         loadout_dir=None, resolver=None, ask=None, say=None):
    """Take the stray at `path` into the loadout folder, list it, and install it; returns
    the install's `Outcome`, the lines naming what was copied and listed leading it.
    `project` (a section's name, root or key) or `user` names the section to list it in;
    neither means the stray's own. Raises `Refused` (see the module's docs) or
    `ManifestError`."""
    from moxi_loadout import install, planner, writes

    status = install.status_plan(loadout_dir=loadout_dir,
                                 resolver=resolver or planner.resolve_key)
    plan = status.plan
    stray = stray_at(plan, path)
    name = source_name(stray)
    found = plan.manifest
    if user:
        section = found.user
    elif project is not None:
        section = found.section(project)
        if section is None or section.is_user:
            raise ManifestError(found.path, [f"no section [project.{project}]"])
    else:
        section = found.user if stray.section == USER else found.section(stray.section)
    if section is None:
        raise Refused(f"{stray.path} is in section {stray.section}'s folders, which the "
                      "manifest no longer has")
    if name in section.names(stray.kind):
        raise Refused(f"{section.name} lists {stray.kind} {name} already; a stray of that "
                      "name is in the way of its placement: moxi loadout install --replace "
                      f"{stray.path}")
    source = copy_in(stray, name, plan.loadout_dir)
    lines = [f"copied {stray.path} to {source}"]
    try:
        writes.add(stray.kind, name, project=None if section.is_user else section.name,
                   loadout_dir=plan.loadout_dir)
    except BaseException:
        _remove(source)
        raise
    lines.append(f"listed {stray.kind} {name} in "
                 + ("user" if section.is_user else f"project {section.name}"))
    scope = planner.Scope(item=(stray.kind, name))
    kw = {} if resolver is None else {"resolver": resolver}
    outcome = install.install(scope, replace=[stray.path], accept_diff=True, ask=ask,
                              loadout_dir=loadout_dir, say=say, **kw)
    outcome.lines[:0] = lines
    return outcome


def move_aside(path: str | os.PathLike, *, loadout_dir=None, resolver=None) -> dict:
    """Move the stray at `path` under the core's `displaced/` folder; returns `{target,
    copy, how, id, back}` as a run's `kept` names a copy (`how` `moved`). Raises `Refused`
    for a path that is no stray, `OSError` when the move fails (the stray where it was)."""
    from moxi import place

    from moxi_loadout import displaced, install, planner

    plan = install.status_plan(loadout_dir=loadout_dir,
                               resolver=resolver or planner.resolve_key).plan
    stray = stray_at(plan, path)
    kept = place.move_aside(stray.path, harness=stray.harness)
    return {"target": stray.path, "copy": os.fspath(kept), "how": "moved", "id": None,
            "back": displaced.back("moved", kept, stray.path)}


def _remove(path: Path) -> None:
    import shutil

    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except OSError:
            pass


__all__ = ["KIND_DIRS", "AGENT_SUFFIX", "Stray", "Refused", "folders", "find", "stray_at",
           "source_name", "copy_in", "take", "move_aside"]
