"""A round's checklist marks: which items of its projects' checklists apply to its work.

A project's `checklist` (a setting of its own table) lists items, each an `id`, a line
of `text` and the `skills` to load. A round marks the items that apply: a design in its
data block's `checklist` key, a round whose flow has no design step at its opening
(`open --checklist`).

A mark is an item id. A bare id is an item of the round's home project; an item of
another project is written `<key>:<id>`. The home project's items are written bare only,
so each item has one spelling.

**Which checklists a round is weighed against.** A design's marks are weighed against
the home project's checklist and those of the projects its stages name as `repo`
(`weighed`): the same projects whose `verifier_brief` lines reach its verifiers. A round
with no design step marks at its opening, before its build's repo is recorded, so its
marks may name an item of any project the config has.

**A round's marks** are the newest frozen version's `checklist`, a version frozen with
no such key reading as none; before any version is frozen, the opening's (`marks_of`).
Marks are a set: their order and how a block says none never differ.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from . import settings as own

#: The key a design's block and an event's `declared` hold the marks under.
KEY = "checklist"
#: What `open --checklist` takes to mark no item.
NONE = "none"
#: A mark: a bare item id or `<key>:<id>`.
_MARK = re.compile(r"(?:([^\s:]+):)?([^\s:]+)")


@dataclass(frozen=True)
class Item:
    """One checklist item as a round names it: its `mark` (bare for the home project's,
    else `<key>:<id>`), its `project`, its `text` and its `skills`; `text` is None and
    `skills` empty for a mark whose item the project's checklist no longer has."""

    mark: str
    project: str
    text: str | None
    skills: tuple[str, ...]

    def data(self) -> dict:
        return {"id": self.mark, "text": self.text, "skills": list(self.skills)}


@dataclass(frozen=True)
class Marks:
    """A round's marks and where they come from: `v<N>` for a frozen version, `opening`
    for the opening, None when nothing marked."""

    marked: tuple[str, ...]
    source: str | None


def split(mark: str, home: str) -> tuple[str, str]:
    """The project and the item id `mark` names, read against the home project
    `home`."""
    found = _MARK.fullmatch(mark) if isinstance(mark, str) else None
    if found is None:
        return home, str(mark)
    return found.group(1) or home, found.group(2)


def spelled(project: str, item_id: str, home: str) -> str:
    """The mark of the item `item_id` of `project`, for a round whose home is `home`."""
    return item_id if project.casefold() == home.casefold() else f"{project}:{item_id}"


def items(settings, project: str) -> list[dict]:
    """The checklist of `project` as the config has it."""
    return own.project_setting(settings, project, "checklist")


def weighed(home: str, repos: Iterable[str]) -> list[str]:
    """The projects a design's marks are weighed against: the home project, then each
    of `repos` once, in order."""
    out = [home]
    for repo in repos:
        if isinstance(repo, str) and repo.casefold() not in (one.casefold() for one in out):
            out.append(repo)
    return out


def stage_repos(declared: dict) -> list[str]:
    """The repos the stages of a block (as an event's `declared` holds it) name."""
    return [stage["repo"] for stage in declared.get("stage") or ()
            if isinstance(stage, dict) and isinstance(stage.get("repo"), str)]


def problems(marks: Sequence[str], home: str, settings, *,
             projects: Sequence[str] | None = None) -> list[tuple[str, str]]:
    """What is wrong with `marks` for a round whose home is `home`, each `(problem,
    fix)`: a mark of no item, a `<home>:<id>`, a mark given twice. `projects` are the
    projects a mark may name (`weighed`); None lets it name any project the config
    has."""
    out = []
    seen = set()
    configured = {name.casefold(): name for name in settings["projects"]}
    for mark in marks:
        if mark in seen:
            out.append((f"marks {mark} twice", "mark each item once"))
            continue
        seen.add(mark)
        found = _MARK.fullmatch(mark) if isinstance(mark, str) else None
        if found is None:
            out.append((f"holds {mark!r}, which is not a mark",
                        "mark an item by its id, or another project's as <key>:<id>"))
            continue
        project, item_id = found.group(1), found.group(2)
        if project is not None and project.casefold() == home.casefold():
            out.append((f"marks {mark}, an item of the home project {home}",
                        f"write it bare: {item_id}"))
            continue
        where = project or home
        if project is not None:
            allowed = (projects is None and project.casefold() in configured) or (
                projects is not None
                and project.casefold() in (one.casefold() for one in projects))
            if not allowed:
                out.append((f"marks {mark}, and {project} is "
                            + ("no project of rounds' config" if projects is None
                               else "neither the home project nor a repo a stage names"),
                            "mark only items of the home project and of the repos the "
                            "design's stages name" if projects is not None else
                            "mark an item of a project rounds' config has, as <key>:<id>"))
                continue
        ids = [item["id"] for item in items(settings, where)]
        if item_id not in ids:
            listed = ", ".join(ids) if ids else "none"
            out.append((f"marks {mark}, and {where}'s checklist has no item {item_id}",
                        f"mark one of {where}'s items ({listed}); moxi config rounds "
                        "shows their text"))
    return out


def parse(text: str) -> tuple[str, ...] | None:
    """The marks of `open --checklist`'s value: () for `none`, each mark once, in
    order; None for a value that names nothing."""
    if text.strip() == NONE:
        return ()
    marks = tuple(dict.fromkeys(one.strip() for one in text.split(",") if one.strip()))
    return marks or None


def marks_of(state) -> Marks:
    """The round `state`'s marks: the newest frozen version's, else the opening's."""
    if state.versions:
        version = state.versions[-1]
        marked = version.declared.get(KEY) or []
        return Marks(tuple(str(one) for one in marked), f"v{version.n}")
    if getattr(state, "marked", None) is not None:
        return Marks(tuple(state.marked), "opening")
    return Marks((), None)


def resolved(marks: Iterable[str], home: str, settings) -> list[Item]:
    """Each of `marks` as the item it names, as the config has it now."""
    out = []
    for mark in marks:
        project, item_id = split(mark, home)
        item = next((one for one in items(settings, project) if one["id"] == item_id), None)
        out.append(Item(mark, project, None if item is None else item["text"],
                        () if item is None else tuple(item["skills"])))
    return out


def every_item(projects: Sequence[str], home: str, settings) -> list[Item]:
    """Every item of each of `projects`' checklists, each spelled as its mark."""
    return [Item(spelled(project, item["id"], home), project, item["text"],
                 tuple(item["skills"]))
            for project in projects for item in items(settings, project)]


def skills(found: Iterable[Item]) -> list[str]:
    """Each skill the items name, once, in the order they first name it."""
    return list(dict.fromkeys(skill for item in found for skill in item.skills))


# -- what a step is told -----------------------------------------------------------------

#: The kinds of group a checklist is said in: the designer drafting, the design's
#: verification, and a build's steps (its build, its verification, a report step of the
#: user's own and the commission block).
DRAFTING = "design"
CHECKING = "verify-design"
BUILDING = "build"
_KINDS = (DRAFTING, CHECKING, BUILDING)

#: How a mark whose item its project's checklist no longer has is said.
GONE = "no longer in the checklist"


def _declared_repos(state) -> list[str]:
    return stage_repos(state.versions[-1].declared) if state.versions else []


def _without_design(state) -> bool:
    from . import flows

    return not flows.has(state.steps, "design")


def is_said(state, home: str, settings, *, repos: Iterable[str] = ()) -> bool:
    """Whether the checklist of the round `state`, whose home project is `home`, is
    said at all: the round has marks, or a project it is weighed against (the home
    project, the repos its newest version's stages name, and `repos`) has an item. A
    round whose projects have no checklist is told nothing and shown nothing."""
    if marks_of(state).marked:
        return True
    projects = weighed(home, [*_declared_repos(state), *repos])
    return any(items(settings, one) for one in projects)


def told(state, home: str, settings, *, kind: str, repo: str | None = None) -> dict | None:
    """What a group of `kind` (`DRAFTING`, `CHECKING` or `BUILDING`) is told of the
    checklist of the round `state`, whose home project is `home`; `repo` is the repo of
    the build a `BUILDING` group is of. None when the round has no marks and no weighed
    project's checklist has an item, so a round whose projects have none is told
    nothing.

    The dict holds `marked`, the round's marks; `from`, where they come from (`v<N>`,
    `opening` or None); `skills`, each skill the marked items name, once; `gone`, the
    marks whose item the checklist no longer has. `DRAFTING` and `CHECKING` add `items`,
    every item of each weighed project. A `BUILDING` group of a round with no design
    step whose `repo` is not its home adds `unweighed`, a table of the `project` (that
    repo) and its `items`, when the round marked none of them: its marks were made
    before the repo was known."""
    if kind not in _KINDS:
        raise ValueError(f"{kind!r} is no kind of group a checklist is said in")
    repos = [repo] if kind == BUILDING and repo is not None else []
    if not is_said(state, home, settings, repos=repos):
        return None
    projects = weighed(home, [*_declared_repos(state), *repos])
    marks = marks_of(state)
    found = resolved(marks.marked, home, settings)
    said = {"marked": list(marks.marked), "from": marks.source,
            "skills": skills(found),
            "gone": [one.mark for one in found if one.text is None]}
    if kind in (DRAFTING, CHECKING):
        said["items"] = [one.data() for one in every_item(projects, home, settings)]
    elif (repo is not None and repo.casefold() != home.casefold()
          and _without_design(state)
          and not any(one.project.casefold() == repo.casefold() for one in found)):
        left = every_item([repo], home, settings)
        if left:
            said["unweighed"] = {"project": repo, "items": [one.data() for one in left]}
    return said


def verifier_lines(state, home: str, settings, *, kind: str,
                   repo: str | None = None) -> list[str]:
    """The `verifier_brief` lines a verifying group of `kind` prints: for `CHECKING`,
    the home project's, then those of each project the newest version's stages name,
    each project once; for `BUILDING`, those of the build's `repo`."""
    if kind == CHECKING:
        projects = weighed(home, _declared_repos(state))
    else:
        projects = [] if repo is None else [repo]
    return [str(line) for one in projects
            for line in own.project_setting(settings, one, "verifier_brief")]


def _item_line(item: dict) -> str:
    text = GONE if item["text"] is None else item["text"]
    named = f" ({', '.join(item['skills'])})" if item["skills"] else ""
    return f"{item['id']}: {text}{named}"


def marks_said(said: dict) -> str:
    """The marks of `told`, each a mark no longer in the checklist said as such, and
    the skills to load: `a, b: load x, y`, or `none`."""
    if not said["marked"]:
        return "none"
    gone = set(said["gone"])
    marks = ", ".join(f"{one} ({GONE})" if one in gone else one for one in said["marked"])
    return f"{marks}: load {', '.join(said['skills'])}" if said["skills"] else marks


def lines(said: dict, kind: str) -> list[str]:
    """What `told` gave for a group of `kind`, as lines."""
    source = said["from"]
    out = []
    if kind == DRAFTING:
        out.append("checklist: mark the items that apply in the block's checklist = [...]")
        if source is not None:
            by = "the opening" if source == "opening" else source
            out.append(f"  {by} marks {marks_said(said)}")
        out += [f"  {_item_line(one)}" for one in said["items"]]
        if source is None or source == "opening":
            out.append("  a stage in another project: mark its items as <key>:<id>")
        return out
    if kind == CHECKING:
        by = "" if source is None else f"{source} "
        out.append(f"checklist: {by}marks {marks_said(said)}")
        out.append("  the items, to check the marks against:"
                   + ("" if said["items"] else " none"))
        out += [f"    {_item_line(one)}" for one in said["items"]]
        return out
    out.append("checklist: none marked" if not said["marked"]
               else f"checklist: marks {marks_said(said)}")
    unweighed = said.get("unweighed")
    if unweighed is not None:
        out.append(f"checklist: {unweighed['project']}'s items were not weighed at this "
                   "round's opening:")
        out += [f"  {_item_line(one)}" for one in unweighed["items"]]
    return out


__all__ = ["KEY", "NONE", "Item", "Marks", "split", "spelled", "items", "weighed",
           "stage_repos", "problems", "parse", "marks_of", "resolved",
           "every_item", "skills", "DRAFTING", "CHECKING", "BUILDING", "GONE", "is_said", "told",
           "verifier_lines", "marks_said", "lines"]
