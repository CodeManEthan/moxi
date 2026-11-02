"""The manifest's writers: each change is one `moxi.files.edit_toml` block.

`add` and `remove` change one section's list, `disable` and `enable` its `disabled`,
`set_mode` the global mode, and `adopt` lists module skills the user said yes to with the
harnesses they took. The verbs, the page, `SETUP` and adoption all write through here.

Rules every writer keeps:

- **No lock file in the user's folder.** Every edit holds the one lock
  `STATE/loadout/manifest.lock` (`stores.manifest_lock()`, which makes its folder), never
  `loadout.toml.lock` beside the file, since the folder is often a git work tree.
- **The file never turns invalid.** The manifest is read and checked before the change
  (a manifest that doesn't read refuses every write, as the verbs refuse it), and the new
  text is checked again before it is written; a refusal writes nothing.
- **Comments and order stay**, through `edit_toml`. A change that changes nothing writes
  nothing, so the file's time is kept too.
- **A write calls no placer** and takes no other lock, so it can run under the core lock
  and under `install.lock` alike.

Each writer returns whether it changed the file (`disable` and `enable` the sections
they changed) and raises `ManifestError` when it refuses, `moxi.files.LockTimeout` when
the manifest's lock stays held past its wait.

A section is named as the verbs' flags name it: `project=None` is the user section, else
a project section, found by its table name, by its root for a path, or by its key
ignoring case. A new path section under the home folder is written `~/...`, so the
manifest names the same project on another machine; any other new path is written
absolute.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from moxi_loadout.manifest import (EVERY, GLOBAL, KINDS, LISTS, MODES, PROJECT, USER,
                                   USER_ONLY, Id, Manifest, ManifestError, is_module_skill,
                                   is_path_name, name_problem, parse, parse_id)

#: How long a write waits for the manifest's lock, in seconds.
LOCK_TIMEOUT = 10.0


class _Unchanged(Exception):
    """Raised out of the edit block when nothing changed, so nothing is written."""


def edit(change: Callable, *, folder: str | os.PathLike | None = None,
         timeout: float | None = LOCK_TIMEOUT):
    """Run `change(doc, manifest)` in one `edit_toml` block on the manifest in `folder`
    (the configured folder when None) and return what it returns.

    `doc` is the tomlkit document to change, given plain Python values; `manifest` the
    `Manifest` as it stood. When `change` returns a false value nothing is written. The
    new text is checked before the write. The loadout folder is made (with the ordinary
    mode) when it is missing; the lock's folder is made private.
    """
    from moxi.files import edit_toml

    from moxi_loadout import stores

    path = stores.manifest_path(folder)
    _make_folder(path.parent)
    lock = stores.manifest_lock()
    result = None
    entered = False
    try:
        with edit_toml(path, timeout=timeout, lock=lock) as doc:
            entered = True
            before = parse(doc.as_string(), path=path)
            result = change(doc, before)
            if not result:
                raise _Unchanged
            parse(doc.as_string(), path=path)
    except _Unchanged:
        return result
    except ValueError as exc:
        # edit_toml raises ValueError for a file that isn't TOML, before the block runs.
        if entered:
            raise
        raise ManifestError(path, [f"is not valid TOML: {exc}"]) from None
    return result


def add(kind: str, name: str, *, project: str | None = None,
        folder: str | os.PathLike | None = None) -> bool:
    """List `kind` `name` in the user section, or in project section `project` (made when
    missing). False when it is listed there already."""
    _check_item(kind, name)
    if project is not None and kind in USER_ONLY:
        raise ManifestError(None, [f"{kind} {name}: hooks, permission rules and MCP "
                                   "servers are written at user scope only; add it "
                                   "without --project"])
    if project is not None and is_module_skill(name):
        raise ManifestError(None, [f"skill {name}: a module's skill is listed in the user "
                                   "section only"])

    def change(doc, manifest: Manifest) -> bool:
        table = _section_table(doc, manifest, project, create=True)
        key = LISTS[kind]
        if key not in table:
            table[key] = [name]
            return True
        if name in table[key]:
            return False
        table[key].append(name)
        return True

    return edit(change, folder=folder)


def remove(kind: str, name: str, *, project: str | None = None,
           folder: str | os.PathLike | None = None) -> bool:
    """Take `kind` `name` out of the user section's list, or project section `project`'s.
    False when that section doesn't list it. The item's own table is kept."""
    _check_item(kind, name)

    def change(doc, manifest: Manifest) -> bool:
        table = _section_table(doc, manifest, project, create=False)
        key = LISTS[kind]
        if table is None or key not in table or name not in table[key]:
            return False
        while name in table[key]:
            table[key].remove(name)
        return True

    return edit(change, folder=folder)


def disable(ident: str | Id, *, user: bool = False, project: str | None = None,
            folder: str | os.PathLike | None = None) -> list[str]:
    """Write id `ident` (`<kind>:<name>@<harness>`) into `disabled` in every section that
    lists the item, or only the user section (`user`) or project section `project`.
    Returns the sections changed, each as `Section.label()` names it; a section where the
    id is already in force is left alone."""
    ident = _id(ident)

    def change(doc, manifest: Manifest) -> list[str]:
        changed = []
        for section in _sections(manifest, ident, user, project):
            if section.is_disabled(ident.kind, ident.name, ident.harness):
                continue
            table = _section_table(doc, manifest, None if section.is_user else section.name,
                                   create=False)
            if "disabled" in table:
                table["disabled"].append(str(ident))
            else:
                table["disabled"] = [str(ident)]
            changed.append(section.label())
        return changed

    return edit(change, folder=folder) or []


def enable(ident: str | Id, *, user: bool = False, project: str | None = None,
           folder: str | os.PathLike | None = None) -> list[str]:
    """Take id `ident` out of `disabled` in every section that lists the item, or only the
    one named. With harness `*` every id of that item goes. An item switched off for every
    harness (`...@*`) can't be enabled for one: that is refused, naming the id to enable.
    Returns the sections changed."""
    ident = _id(ident)

    def change(doc, manifest: Manifest) -> list[str]:
        changed = []
        sections = _sections(manifest, ident, user, project)
        for section in sections:
            if ident.harness != EVERY and Id(ident.kind, ident.name, EVERY) in section.disabled:
                raise ManifestError(manifest.path, [
                    f"{section.label()}: {ident.kind}:{ident.name}@* switches it off for "
                    f"every harness; enable {ident.kind}:{ident.name}@*, then disable the "
                    "harnesses to keep off"])
        for section in sections:
            table = _section_table(doc, manifest, None if section.is_user else section.name,
                                   create=False)
            drop = [text for text in table.get("disabled", ())
                    if _matches(text, ident)]
            for text in drop:
                table["disabled"].remove(text)
            if drop:
                changed.append(section.label())
        return changed

    return edit(change, folder=folder) or []


def set_mode(mode: str, *, folder: str | os.PathLike | None = None) -> bool:
    """Write `[global] mode`. False when it is that mode already."""
    if mode not in MODES:
        raise ManifestError(None, [f"global mode {mode!r}: use one of {', '.join(MODES)}"])

    def change(doc, manifest: Manifest) -> bool:
        if manifest.mode == mode:
            return False
        if GLOBAL in doc:
            doc[GLOBAL]["mode"] = mode
        else:
            doc[GLOBAL] = {"mode": mode}
        return True

    return edit(change, folder=folder)


def adopt(skills: Mapping[str, Iterable[str]], *,
          folder: str | os.PathLike | None = None) -> bool:
    """List each module skill `<module>/<skill>` in `skills` in the user section, with the
    harnesses it was taken for, all in one write.

    The item's `[skill."<module>/<skill>"] harnesses` gains those harnesses when it has a
    list. With no list the item already wants every harness when it was listed, so it is
    left; an item not listed before gets a list of just the harnesses taken, so the next
    plan wants what the user said yes to, not every row of the harness table. False when
    nothing changed.
    """
    wanted = {name: list(dict.fromkeys(hs)) for name, hs in skills.items()}
    for name, harnesses in wanted.items():
        if not is_module_skill(name):
            raise ManifestError(None, [f"skill {name}: adoption takes a module's skill, "
                                       "<module>/<skill>"])
        _check_item("skill", name)
        for h in harnesses:
            if not isinstance(h, str) or not h:
                raise ManifestError(None, [f"skill {name}: names the harness {h!r}"])
    if not wanted:
        return False

    def change(doc, manifest: Manifest) -> bool:
        changed = False
        user_table = _section_table(doc, manifest, None, create=True)
        for name, harnesses in wanted.items():
            listed = manifest.user.lists_item("skill", name)
            if not listed:
                if "skills" in user_table:
                    user_table["skills"].append(name)
                else:
                    user_table["skills"] = [name]
                changed = True
            has = manifest.harnesses("skill", name)
            if has is None and listed:
                continue
            item = _item_table(doc, "skill", name)
            if "harnesses" not in item:
                if harnesses:
                    item["harnesses"] = list(harnesses)
                    changed = True
                continue
            for h in harnesses:
                if h not in item["harnesses"]:
                    item["harnesses"].append(h)
                    changed = True
        return changed

    return edit(change, folder=folder)


# -- helpers -------------------------------------------------------------------------------

def _make_folder(folder: Path) -> None:
    """Make the loadout folder with the ordinary mode; `atomic_write` would make a
    missing one private, and the folder is the user's, often a clone."""
    os.makedirs(folder, exist_ok=True)


def _check_item(kind: str, name: str) -> None:
    if kind not in KINDS:
        raise ManifestError(None, [f"{kind!r} is not a kind; use one of {', '.join(KINDS)}"])
    if problem := name_problem(kind, name):
        raise ManifestError(None, [problem])


def _id(ident: str | Id) -> Id:
    if isinstance(ident, Id):
        return ident
    try:
        return parse_id(ident)
    except ValueError as exc:
        raise ManifestError(None, [str(exc)]) from None


def _matches(text: str, ident: Id) -> bool:
    try:
        other = parse_id(text)
    except ValueError:
        return False
    if (other.kind, other.name) != (ident.kind, ident.name):
        return False
    return ident.harness == EVERY or other.harness == ident.harness


def _sections(manifest: Manifest, ident: Id, user: bool, project: str | None):
    """The sections `disable` and `enable` write: the one named, else every section that
    lists the item. A named section that doesn't list it, or no section at all, is
    refused."""
    what = f"{ident.kind} {ident.name}"
    if user or project is not None:
        section = manifest.section(None if user else project)
        if section is None:
            raise ManifestError(manifest.path, [f"no section [{PROJECT}.{project}]"])
        if not section.lists_item(ident.kind, ident.name):
            raise ManifestError(manifest.path, [f"{section.label()} doesn't list {what}"])
        return [section]
    sections = list(manifest.listing(ident.kind, ident.name))
    if not sections:
        raise ManifestError(manifest.path, [f"no section lists {what}"])
    return sections


def _section_table(doc, manifest: Manifest, project: str | None, *, create: bool):
    """The document's table for the user section (`project` None) or a project section,
    found as `Manifest.section` finds it; made when missing and `create`, else None."""
    if project is None:
        if USER not in doc:
            if not create:
                return None
            doc[USER] = {}
        return doc[USER]
    found = manifest.section(project)
    name = found.name if found is not None else _new_section_name(project)
    if PROJECT not in doc:
        if not create:
            return None
        doc[PROJECT] = {}
    projects = doc[PROJECT]
    if name not in projects:
        if not create:
            return None
        projects[name] = {}
    return projects[name]


def _new_section_name(project: str) -> str:
    if not project.strip():
        raise ManifestError(None, ["a project section needs a path or a key"])
    if not is_path_name(project):
        return project
    path = os.path.normpath(os.path.abspath(os.path.expanduser(project)))
    home = os.path.normpath(os.path.expanduser("~"))
    if path == home:
        return "~"
    if path.startswith(home.rstrip(os.sep) + os.sep):
        return "~/" + os.path.relpath(path, home).replace(os.sep, "/")
    return path


def _item_table(doc, kind: str, name: str):
    if kind not in doc:
        doc[kind] = {}
    if name not in doc[kind]:
        doc[kind][name] = {}
    return doc[kind][name]


__all__ = ["LOCK_TIMEOUT", "edit", "add", "remove", "disable", "enable", "set_mode", "adopt"]
