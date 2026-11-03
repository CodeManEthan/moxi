"""The planner: the manifest turned into `moxi.place.Item`s, planned through the core placer.

Rule: loadout always plans the whole manifest; what it applies may be less.

- The wanted set passed to `moxi.place.plan("loadout", ...)` is every skill and agent file
  the manifest lists and doesn't disable, in every section, whatever the verb. A scope
  (`--user`, `--project P`, one item's verb) chooses which planned steps are applied, never
  what is planned.
- An item that can't be resolved this run is **carried as it stands**: its project section
  is refused, its source is bad, its module doesn't load, or its harness has no project
  folder. The planner passes the `Item` loadout's ledger recorded at its last apply (or,
  with no record, one naming the target it would have), so the placer names the placement
  and keeps it; none of its steps is applied. An item never placed has nothing to carry.
- Only an item the manifest drops (from its section, or a harness from its list) or
  disables plans a `remove`, and a module skill whose module is not installed: it reads
  `off`, and a placement of it loadout still holds (the module's uninstall failed to
  remove it) is removed.

Sources: an item's own `source` key wins; else `<folder>/skills/<n>/` or
`<folder>/agents/<n>.md`; else, in a project section, the project's own copy in Claude's
project folder for the kind (held: placed from where it is, and nothing is placed onto it).
A copy loadout placed there itself (under a copy form) is not the project's own. An item
found both in the folder and held is a clash, not an order. A missing or symlinked
source, a frontmatter `name` that differs, or a clash is `bad-source`.

Targets: user scope takes the harness row's folder for the kind; project scope takes
`<root>/<project_folders[harness][kind]>/<name>`. An agent file goes to Claude as it is and
to Codex and OpenCode as a generated file (`moxi_loadout.convert`) under
`STATE/loadout/generated/<harness>/` (a project's under a folder named for its root).

A module skill, `<module>/<skill>` in `[user] skills`, is planned with the rest. When
nobody has placed it, install places it as the module and takes it over
(`moxi_loadout.adoption`), so the module's own skills step finds it handed over.

States: `ok`, `missing`, `stale`, `modified`, `blocked`, `would-commit`, `bad-source`, `off`.
A step the placer refused for a path in the way reads `blocked`, one whose path git tracks
`would-commit`, both by the step's cause, never its reason. A target git would commit
because nothing ignores it reads `would-commit` too (`moxi_loadout.ignores`).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from moxi.exit import ERROR, REFUSED, TEMPFAIL

from moxi_loadout import convert, ledger
from moxi_loadout.manifest import USER, Manifest, Section, is_module_skill

OWNER = "loadout"
#: The kinds the core placer places.
PLACED_KINDS = ("skill", "agent")
STATES = ("ok", "missing", "stale", "modified", "blocked", "would-commit", "bad-source",
          "off")
#: The states a run leaves without fault.
FINE = ("ok", "off")
#: The exit code each state gives an install when it is left after the write.
STATE_CODES = {"bad-source": ERROR, "missing": ERROR, "stale": ERROR, "blocked": REFUSED,
               "modified": REFUSED, "would-commit": REFUSED}
_FOLDERS = {"skill": "skills", "agent": "agents"}
#: The kind and name a want of the global file carries, in `if-absent` mode.
GLOBAL_KIND = "global"


@dataclass(frozen=True)
class Resolution:
    """A project section's root, or why it can't be used this run: `line` and the exit
    code (`code`) the refusal gives. `every` marks a refusal that holds for every key
    section (projects unreadable or busy), so no other key is asked this run."""

    root: str | None
    line: str = ""
    code: int = REFUSED
    every: bool = False


def resolve_key(key: str) -> Resolution:
    """A key section's root, through projects (`moxi_loadout.projects_link`). Without
    projects every key section is refused."""
    from moxi_loadout import projects_link

    return projects_link.resolve(key)


@dataclass
class Want:
    """One manifest item for one harness in one section, and what the plan says of it.

    `item` is the `Item` passed for it, or None when it is carried or not wanted.
    `carried` holds the items passed in its place. `state` is set when the planner
    decides it without the placer (`bad-source`, `off`, a refused section), with `reason`
    and `code`. `generated` is the converted file to write first, when it needs writing.
    """

    section: Section
    kind: str
    name: str
    harness: str
    item: object = None
    carried: tuple = ()
    state: str | None = None
    reason: str = ""
    code: int = 0
    module: str | None = None
    generated: tuple | None = None   # (path, bytes, file state now)
    notes: tuple[str, ...] = ()
    step: object = None
    adoptable: bool = False
    commit: str | None = None   # why git would commit its target, when it would

    @property
    def scope(self) -> str:
        return USER if self.section.is_user else self.section.name

    def label(self) -> str:
        if self.kind == GLOBAL_KIND:
            return f"the global file for {self.harness}"
        where = "" if self.section.is_user else f" in {self.section.label()}"
        return f"{self.kind} {self.name} for {self.harness}{where}"


@dataclass(frozen=True)
class Row:
    """One line of the run's report: an item for one harness, or a placement no longer
    wanted (`kind` and `name` from its record, when it has one). `replaceable` says that
    `install --replace` places over what stands in its way: an occupant (one git tracks
    among them) or a file edited by hand."""

    scope: str | None
    kind: str
    name: str
    harness: str
    state: str
    reason: str = ""
    target: str | None = None
    code: int = 0
    replaceable: bool = False

    def line(self) -> str:
        where = "" if self.scope in (None, USER) else f" in project {self.scope}"
        text = f"{self.state:<12} {self.kind} {self.name} for {self.harness}{where}"
        return text + (f": {self.reason}" if self.reason else "")

    def as_json(self) -> dict:
        return {"scope": self.scope, "kind": self.kind, "name": self.name,
                "harness": self.harness, "state": self.state, "reason": self.reason,
                "target": self.target, "replaceable": self.replaceable}


@dataclass
class Plan:
    """The whole manifest's plan: `wants`, the `removals` the placer planned, the core
    harness table's lines, the items the manifest lists where they can't be written and
    the section refusals (`lines`), and the conversion notes."""

    manifest: Manifest
    folder: Path
    ledger: dict
    wants: list[Want] = field(default_factory=list)
    removals: list = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    refused: dict[str, Resolution] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: The targets of loadout's own copied files, read once.
    copies: set[str] | None = None

    @property
    def resolved_all(self) -> bool:
        """Whether every section resolved, so a remove with no record may be applied."""
        return not self.refused

    def record(self, step) -> dict | None:
        return self.ledger["placed"].get(str(step.target))


@dataclass(frozen=True)
class Scope:
    """Which planned steps a run applies: every one (no field set), the user section's,
    named project sections', or one item's (`item`, `(kind, name)`, in `sections` when
    given)."""

    user: bool = False
    projects: tuple[str, ...] = ()
    item: tuple[str, str] | None = None

    @property
    def whole(self) -> bool:
        return not (self.user or self.projects or self.item)

    def sections(self) -> set[str] | None:
        if not (self.user or self.projects):
            return None
        return ({USER} if self.user else set()) | set(self.projects)

    def covers(self, scope: str | None, kind: str | None, name: str | None) -> bool:
        if kind == GLOBAL_KIND:
            # The global file is touched only by a whole run and by the global verbs.
            return self.whole or self.item == (GLOBAL_KIND, GLOBAL_KIND)
        named = self.sections()
        if named is not None and scope not in named:
            return False
        return self.item is None or self.item == (kind, name)


# -- building the plan ---------------------------------------------------------------------

def build(manifest: Manifest, machine, data: dict, *,
          resolver: Callable[[str], Resolution] = resolve_key) -> Plan:
    """Every want of the manifest, resolved, with its generated file and its carried items.
    Plans nothing yet (see `run`): a caller may write the generated files first."""
    from moxi import place

    rows, skipped = place.harnesses()
    plan = Plan(manifest, Path(machine.source), data, lines=list(skipped))
    plan.lines += [r.line for r in manifest.refused]
    every = list(rows)
    user_skills = set(manifest.user.names("skill"))
    sticky = None
    for section in manifest.sections():
        root = None
        if section.where == "key":
            res = sticky if sticky is not None else resolver(section.name)
            if res.every:
                sticky = res
            if res.root is None:
                plan.refused[section.name] = res
                if res.line not in plan.lines:
                    plan.lines.append(res.line)
            root = res.root
        elif section.where == "path":
            root = section.root
        for kind in PLACED_KINDS:
            for name in section.names(kind):
                harnesses = manifest.harnesses(kind, name)
                for harness in (every if harnesses is None else harnesses):
                    plan.wants.append(_want(plan, machine, section, root, kind, name,
                                            harness, user_skills))
    if manifest.global_mode == "if-absent":
        plan.wants += _global_wants(plan, manifest, every)
    return plan


def _global_wants(plan: Plan, manifest: Manifest, every) -> list[Want]:
    """In `if-absent` mode, each harness's global file placed from the folder's global
    text where the harness has none; an existing file is left alone."""
    from moxi.place import Item

    from moxi_loadout import harness as hz
    from moxi_loadout import stores

    out = []
    source = Path(manifest.global_file)
    for h in every:
        if h not in hz.HARNESSES:
            continue
        dest = hz.global_file(h)
        want = Want(manifest.user, GLOBAL_KIND, GLOBAL_KIND, h)
        if not source.is_file() or source.is_symlink():
            want.state, want.reason, want.code = ("bad-source", f"no global text at "
                                                  f"{source}", ERROR)
            items = [ledger.item_of(r) for r in plan.ledger["placed"].values()
                     if (r.get("kind"), r.get("harness")) == (GLOBAL_KIND, h)]
            items = [i for i in items if i is not None] or [
                Item(stores.state_dir() / ".carried" / dest.name, h, dest.name, "agent",
                     dest=dest)]
            want.carried = tuple(items)
        else:
            want.item = Item(source, h, dest.name, "agent", dest=dest)
        out.append(want)
    return out


def _want(plan: Plan, machine, section: Section, root, kind, name, harness,
          user_skills) -> Want:
    want = Want(section, kind, name, harness)
    if section.is_disabled(kind, name, harness):
        want.state, want.reason = "off", "disabled"
        return want
    refusal = plan.refused.get(section.name) if section.where == "key" else None
    if refusal is not None:
        return _carry(plan, want, "blocked", refusal.line, refusal.code)
    if kind == "skill" and is_module_skill(name):
        return _module_want(plan, want, user_skills)
    try:
        source, held, fields, body = _source(plan, machine, section, root, kind, name)
    except _BadSource as exc:
        return _carry(plan, want, "bad-source", str(exc), ERROR,
                      filename=_filename(kind, name, harness), root=root, machine=machine)
    filename = name + (convert.SOURCE_SUFFIX if kind == "agent" else "")
    if kind == "agent" and harness in convert.SUFFIX:
        source, filename = _generate(plan, want, section, root, name, source, fields, body)
        held = False
    dest = None
    if root is not None:
        folder = machine.project_folders.get(harness, {}).get(_FOLDERS[kind])
        if folder is None:
            return _carry(plan, want, "blocked",
                          f"harness {harness} has no project folder for {_FOLDERS[kind]} "
                          "(the setting project_folders)", REFUSED, filename=filename)
        dest = Path(root) / folder / filename
    from moxi.place import Item

    want.item = Item(Path(source), harness, filename, kind, dest=dest, held=held,
                     root=Path(root) if held else None)
    return want


def _module_want(plan: Plan, want: Want, user_skills) -> Want:
    import importlib.resources

    from moxi import modules

    module, _, skill = want.name.partition("/")
    want.module = module
    if not want.section.is_user:
        return _carry(plan, want, "blocked", "a module's skill is placed at user scope "
                      "only; list it in [user]", REFUSED, filename=skill)
    if skill in user_skills:
        want.state = "blocked"
        want.reason = f"your own skill {skill} has that name, and it wins"
        want.code = REFUSED
        return want
    found = modules.load(module)
    if found.state == "absent":
        # Not carried: a placement of it loadout still holds (one the module's uninstall
        # couldn't remove) plans its `remove`, so nothing is left dangling.
        want.state, want.reason = "off", f"{module} is not installed"
        return want
    if not found.ok:
        return _carry(plan, want, "off", f"{module} is broken", 0, filename=skill)
    try:
        folder = importlib.resources.files(found.package).joinpath(found.skills or "")
        ok = found.skills is not None and isinstance(folder, Path) \
            and (folder / skill).is_dir()
    except (ImportError, TypeError, ValueError, OSError):
        ok = False
    if not ok:
        return _carry(plan, want, "bad-source", f"{module} ships no skill {skill}", ERROR,
                      filename=skill)
    from moxi.place import Item

    want.item = Item(Path(folder) / skill, want.harness, skill)
    return want


class _BadSource(Exception):
    pass


def _source(plan: Plan, machine, section: Section, root, kind, name):
    """The item's source, whether it is held, and an agent's frontmatter and body."""
    filename = name + (convert.SOURCE_SUFFIX if kind == "agent" else "")
    own = plan.manifest.source_of(kind, name)
    held = False
    if own is not None:
        source = own
    else:
        mine = plan.folder / _FOLDERS[kind] / filename
        theirs = None
        if root is not None:
            claude = machine.project_folders.get("claude", {}).get(_FOLDERS[kind])
            if claude is not None:
                theirs = Path(root) / claude / filename
        theirs_real = theirs is not None and theirs.exists() and not theirs.is_symlink() \
            and not _copied_by_loadout(plan, theirs)
        if os.path.lexists(mine) and theirs_real:
            raise _BadSource(f"clash: {mine} and the project's own {theirs}")
        if os.path.lexists(mine):
            source = mine
        elif theirs_real:
            source, held = theirs, True
        else:
            where = f" or at {theirs}" if theirs is not None else ""
            raise _BadSource(f"no source at {mine}{where}")
    if os.path.islink(source):
        raise _BadSource(f"{source} is a symlink")
    if not os.path.exists(source):
        raise _BadSource(f"no source at {source}")
    md = Path(source) / "SKILL.md" if kind == "skill" else Path(source)
    try:
        fields, body = convert.read(md)
    except (OSError, convert.FrontmatterError) as exc:
        raise _BadSource(f"{md} {_why(exc)}") from None
    if fields.get("name") != name:
        raise _BadSource(f"{md}: its name {fields.get('name')!r} is not {name!r}")
    return Path(source), held, fields, body


def _copied_by_loadout(plan: Plan, path: Path) -> bool:
    """Whether `path` is a copy loadout placed there (its own placement under a copy
    form), which is never the project's own copy: a copied folder whose mark names
    loadout, or a file the core's ledger records as loadout's copy."""
    import json

    from moxi import place

    if path.is_dir():
        try:
            with open(path / place.MARK, encoding="utf-8") as f:
                mark = json.load(f)
        except (OSError, ValueError):
            return False
        return isinstance(mark, dict) and mark.get("owner") == OWNER
    if plan.copies is None:
        plan.copies = {place.target_key(p.path) for p in place.status(OWNER)
                       if p.form == "copy"}
    return place.target_key(path) in plan.copies


def _why(exc) -> str:
    if isinstance(exc, OSError):
        return f"can't be read ({exc.strerror or exc})"
    return str(exc)


def _generate(plan: Plan, want: Want, section: Section, root, name, source, fields, body):
    """The generated file of agent `name` for `want.harness`, recorded on `want`: its
    path, bytes and present state. Returns the path and the placed file name."""
    from moxi_loadout import stores

    harness = want.harness
    suffix = convert.SUFFIX[harness]
    folder = stores.generated_dir() / harness
    if root is not None:
        digest = hashlib.sha256(os.fspath(root).encode("utf-8", "surrogatepass"))
        folder = folder / f"project-{digest.hexdigest()[:16]}"
    path = folder / (name + suffix)
    extra = plan.manifest.item("agent", name).get(harness)
    text, lost = convert.CONVERTERS[harness](name, fields, body, extra)
    data = convert.with_header(os.fspath(source), text)
    try:
        now = path.read_bytes()
    except OSError:
        now = None
    current = None if now is None else ("same" if now == data else convert.state(path))
    want.generated = (path, data, current)
    if lost:
        want.notes = (f"{harness} agent {name}: " + "; ".join(lost),)
    return path, name + suffix


def _filename(kind: str, name: str, harness: str) -> str:
    if kind == "skill":
        return name
    return name + convert.SUFFIX.get(harness, convert.SOURCE_SUFFIX)


def _carry(plan: Plan, want: Want, state: str, reason: str, code: int, *,
           filename: str | None = None, root=None, machine=None) -> Want:
    """`want` carried as it stands: the items its records name, else none."""
    want.state, want.reason, want.code = state, reason, code
    items = []
    for record in plan.ledger["placed"].values():
        if (record.get("section"), record.get("kind"), record.get("item"),
                record.get("harness")) == (want.scope, want.kind, want.name, want.harness):
            item = ledger.item_of(record)
            if item is not None:
                items.append(item)
    dest = None
    if root is not None and machine is not None:
        folder = machine.project_folders.get(want.harness, {}).get(_FOLDERS[want.kind])
        dest = None if folder is None else Path(root) / folder / (filename or "")
    if not items and filename is not None and (want.section.is_user or dest is not None):
        # No record (a lost ledger): name the target it would have, so nothing removes it.
        from moxi.place import Item

        from moxi_loadout import stores

        nowhere = stores.state_dir() / ".carried" / filename
        items.append(Item(nowhere, want.harness, filename, want.kind, dest=dest))
    want.carried = tuple(items)
    return want


# -- planning ------------------------------------------------------------------------------

def run(plan: Plan, *, git_seconds: float | None = None) -> Plan:
    """Plan every want with the core placer, as owner loadout, and set each want's step.
    `git_seconds` goes to `moxi.place.plan`."""
    from moxi import place

    items, owners = [], []
    for want in plan.wants:
        if want.item is not None:
            items.append(want.item)
            owners.append(want)
    for want in plan.wants:
        for item in want.carried:
            items.append(item)
            owners.append(None)
    steps = place.plan(OWNER, items, git_seconds=git_seconds)
    for want, step in zip(owners, steps[:len(items)]):
        if want is not None:
            want.step = step
    plan.removals = [s for s in steps[len(items):] if s.action == "remove"]
    adoptable = _adoptable(plan)
    for want in plan.wants:
        if want.step is not None and want.step.action == "refuse" \
                and want.step.cause == "owner" and str(want.step.target) in adoptable:
            want.adoptable = True
        plan.notes.extend(want.notes)
    return plan


def _adoptable(plan: Plan) -> set[str]:
    """The targets a module placed itself, which install takes over before it plans."""
    from moxi import place

    out = set()
    for module in {w.module for w in plan.wants if w.module}:
        out |= {str(p.path) for p in place.status(module)}
    return out


# -- states --------------------------------------------------------------------------------

def state_of(want: Want) -> tuple[str, str, int]:
    """The state a want reads before anything is applied, its reason, and the exit code
    the state gives if it is left so."""
    if want.state is not None:
        return want.state, want.reason, want.code
    step = want.step
    if step is None:
        return "off", "", 0
    if want.adoptable:
        return "ok", f"{want.module} placed it; install takes it over", 0
    if want.commit is not None:
        return "would-commit", want.commit, REFUSED
    if want.kind == GLOBAL_KIND and step.action == "refuse" \
            and step.cause in ("occupant", "tracked"):
        return "ok", f"{step.target} is there already; if-absent leaves it", 0
    if want.generated is not None and want.generated[2] == "modified":
        return ("modified", f"{want.generated[0]} was edited by hand; edit the source, "
                f"or install --replace {want.kind}:{want.name}@{want.harness}", REFUSED)
    pending = want.generated is not None and want.generated[2] != "same"
    if pending and step.action == "refuse" and step.cause == "source":
        return "missing", "", ERROR
    if step.action in ("keep", "skip"):
        return ("stale", "its generated file is out of date", ERROR) if pending \
            else ("ok", "", 0)
    if step.action == "add":
        return "missing", "", ERROR
    if step.action == "update":
        return "stale", step.reason, ERROR
    if step.action == "refuse":
        if step.cause == "tracked":
            return "would-commit", step.reason, REFUSED
        if step.cause in ("source", "io"):
            return "bad-source", step.reason, ERROR
        return "blocked", step.reason, REFUSED
    return "ok", "", 0


def places(want: Want, *, forced: bool = False) -> bool:
    """Whether applying `want`'s step places (or keeps) a placement of loadout's at its
    target: an `add`, an `update`, a `keep` (but never a target held in place), and with
    `forced` a refusal that `--replace` places over. The would-commit check covers
    these."""
    from moxi.place import HELD_IN_PLACE

    step = want.step
    if step is None or want.state is not None or want.adoptable or step.target is None:
        return False
    if step.action in ("add", "update"):
        return True
    if step.action == "keep":
        return step.reason != HELD_IN_PLACE
    return forced and step.action == "refuse" and step.cause in ("occupant", "tracked")


def replaceable(want: Want) -> bool:
    """Whether `install --replace` places over what stands at `want`'s target as the plan
    reads it: an occupant in the way, one git tracks among them, or a generated file edited
    by hand. A target git would commit for want of an ignore rule is no such case."""
    step = want.step
    if step is None or want.state is not None or want.adoptable or want.commit is not None \
            or want.kind == GLOBAL_KIND:
        return False
    if want.generated is not None and want.generated[2] == "modified":
        return True
    return step.action == "refuse" and step.cause in ("occupant", "tracked")


def target_of(want: Want) -> str | None:
    return None if want.step is None or want.step.target is None else str(want.step.target)


def row(want: Want, state: str | None = None, reason: str | None = None,
        code: int | None = None) -> Row:
    s, r, c = state_of(want)
    return Row(want.scope, want.kind, want.name, want.harness, state or s,
               r if reason is None else reason, target_of(want), c if code is None else code,
               state is None and replaceable(want))


def removal_row(plan: Plan, step) -> Row:
    record = plan.record(step) or {}
    return Row(record.get("section"), record.get("kind", step.item.kind),
               record.get("item", step.item.name), step.item.harness, "stale",
               _removal_reason(record), str(step.target), ERROR)


def _removal_reason(record: dict) -> str:
    """Why a placement is going: its module is gone, else it is no longer wanted. A module
    that is installed but broken keeps its skills, so it is never the reason."""
    module, slash, _ = str(record.get("item") or "").partition("/")
    if slash and record.get("kind") == "skill":
        from moxi import modules

        if modules.load(module).state == "absent":
            return f"{module} is not installed; install removes the placement"
    return "no longer wanted; install removes it"


def rows(plan: Plan) -> list[Row]:
    """Every want's row, then one per placement no longer wanted."""
    return [row(w) for w in plan.wants] + [removal_row(plan, s) for s in plan.removals]


def exit_code(codes: Iterable[int]) -> int:
    """An error wins over a refusal, a refusal over a busy lock."""
    codes = set(codes)
    for code in (ERROR, REFUSED, TEMPFAIL):
        if code in codes:
            return code
    return 0


__all__ = ["OWNER", "STATES", "FINE", "Resolution", "resolve_key", "Want", "Row", "Plan",
           "Scope", "build", "run", "state_of", "row", "removal_row", "rows", "exit_code",
           "places", "replaceable", "target_of"]
