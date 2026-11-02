"""`install` and `status`: the manifest planned whole, the steps in scope applied.

`install` runs, under `install.lock` (60 s, else exit 75) and then the core lock:

1. take every module's own skill placements over (`moxi_loadout.adoption.take`), writing
   them into the manifest, and record them in loadout's ledger;
2. read the manifest, the settings and the ledger, and build the whole plan
   (`moxi_loadout.planner`);
3. write the generated agent files of the wants in scope;
4. plan through the core placer and apply the steps in scope: each `add`, `update` and
   `keep`; each `remove` whose record names a section in scope (a `remove` with no record
   only in an unscoped run in which every section resolved); a refusal `--replace` names,
   or a link of loadout's own under the folder the last install read; a module skill
   nobody has placed, as its module, then taken over seeded, and so is one whose
   occupant `--replace` names (moved aside as the module), so loadout never holds a
   module's skill the module didn't hand over;
   before anything is placed, the would-commit check (`moxi_loadout.ignores`): loadout's
   ignore file written first where it covers a target, and a target git would still
   commit left out with its line; after, loadout's ignore files brought into line with
   what it holds;
5. write the harness entries (`moxi_loadout.entries`): hooks, permission rules, MCP
   servers and the managed global file, CLI-backed MCP servers first, then each harness
   file once;
6. write loadout's ledger: a record per placement made or kept and per entry written or
   kept, none for one removed.

Locks are always taken in one order: `install.lock`, the core lock, the manifest's.

`status` builds the same plan and applies nothing; it takes no lock. `install --dry-run`
is `status` over the run's scope.

Exit codes: `status` 0 when every item is `ok` or `off`, else 1. `install` 0 when every
item in scope is `ok` or `off` afterwards; 1 an error (a bad source, a failed write, a
state still missing after the write); 3 refused (`blocked`, `modified` or `would-commit`
left, a `--replace` that names nothing refused, a project section refused); 75 a lock held
past its wait, or projects busy. 1 wins over 3, 3 over 75. A refusal outside the scope is
a line, not a code.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from moxi.exit import ERROR, REFUSED, TEMPFAIL

from moxi_loadout import adoption, entries, ignores, ledger, planner
from moxi_loadout.manifest import USER, ManifestError, parse_id
from moxi_loadout.planner import FINE, Row, Scope

#: How long install waits for another loadout install, in seconds.
INSTALL_WAIT = 60.0
#: How long install waits for a `moxi install` or `moxi uninstall` that holds the core
#: lock (it may be installing packages), in seconds.
CORE_WAIT = 600.0
#: How the note naming targets the would-commit check had no time for starts.
SKIPPED = "the would-commit check skipped"


@dataclass
class Outcome:
    """What a run came to: its `rows` (every want for `status`, those in scope for
    `install`), the `lines` said on the way, `notes`, and the exit `code`."""

    rows: list[Row] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    code: int = 0

    def as_json(self) -> dict:
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r.state] = counts.get(r.state, 0) + 1
        return {"rows": [r.as_json() for r in self.rows], "lines": self.lines,
                "notes": self.notes, "counts": counts, "exit": self.code}

    def text(self) -> list[str]:
        out = list(self.lines)
        out += [r.line() for r in self.rows if r.state != "ok"]
        out += [f"note: {n}" for n in self.notes]
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r.state] = counts.get(r.state, 0) + 1
        out.append("loadout: " + (", ".join(f"{counts[s]} {s}" for s in planner.STATES
                                            if counts.get(s)) or "nothing listed"))
        return out


# -- status ---------------------------------------------------------------------------------

def status(scope: Scope = Scope(), *, folder=None, resolver=planner.resolve_key,
           git: ignores.Git | None = None) -> Outcome:
    """Every item's state; the scope filters the rows. Takes no lock, writes nothing.
    `git` runs the would-commit check's git calls (a fresh `ignores.Git` when None)."""
    plan, run = _plan(folder, resolver)
    notes = _check_placed(plan, ignores.Git() if git is None else git)
    rows = [r for r in planner.rows(plan) + entries.rows(run) + _ignore_rows(plan.ledger)
            if _shown(scope, r)]
    code = 0 if all(r.state in FINE for r in rows) else ERROR
    return Outcome(rows, list(plan.lines) + run.lines,
                   list(dict.fromkeys(plan.notes + run.notes + notes)), code)


def _check_placed(plan: planner.Plan, git: ignores.Git) -> list[str]:
    """The would-commit check over every target the plan places or keeps, for `status`:
    a target git would commit reads `would-commit`; so does a placement already made
    that nothing ignores, which the next install covers with loadout's ignore file."""
    wants = [w for w in plan.wants if planner.places(w)]
    verdicts = ignores.check([str(w.step.target) for w in wants], git)
    skipped = []
    for w in wants:
        v = verdicts.get(str(w.step.target))
        if v is None:
            continue
        if v.state == "would-commit":
            w.commit = v.line
        elif v.state == "needs" and w.step.action == "keep":
            w.commit = (f"git would commit it; install writes "
                        f"{os.path.join(v.folder, ignores.IGNORE)}")
        elif v.state == "unchecked":
            skipped.append(v.line)
    if not skipped:
        return []
    return [f"{SKIPPED} {len(skipped)} target"
            f"{'s' * (len(skipped) != 1)}: {skipped[0]}"]


def _ignore_rows(data: dict) -> list[Row]:
    """A row for each of loadout's ignore files edited since loadout wrote it."""
    return [Row(section, "ignore", path, "*", "modified", why, path, REFUSED)
            for path, section, why in ignores.problems(data)]


def _shown(scope: Scope, r: Row) -> bool:
    return scope.covers(r.scope, r.kind, r.name) if r.scope is not None else scope.whole


def _plan(folder, resolver) -> tuple[planner.Plan, entries.Run]:
    from moxi_loadout import manifest as manifest_mod
    from moxi_loadout import settings

    machine = settings.read()
    if folder is not None:
        machine.source = os.path.abspath(folder)
    found = manifest_mod.load(machine.source)
    data = ledger.read()
    plan = planner.build(found, machine, data, resolver=resolver)
    return planner.run(plan), entries.plan(found, machine.source, data)


# -- install --------------------------------------------------------------------------------

def install(scope: Scope = Scope(), *, replace: Iterable[str] = (), dry_run: bool = False,
            folder=None, resolver=planner.resolve_key,
            say: Callable[[str], object] | None = None) -> Outcome:
    """Adopt, plan the whole manifest, apply the steps in scope. See the module's docs."""
    if dry_run:
        out = status(scope, folder=folder, resolver=resolver)
        out.code = planner.exit_code(r.code for r in out.rows
                                     if r.state not in ("missing", "stale"))
        return out
    from moxi import files, paths

    from moxi_loadout import stores

    lines: list[str] = []
    try:
        with files.lock(stores.install_lock(), timeout=INSTALL_WAIT):
            with _core_lock(files, paths):
                return _install(scope, list(replace), folder, resolver, lines)
    except files.LockTimeout as exc:
        lines.append(f"{exc}; another install is running, so try again later")
        return Outcome(lines=lines, code=TEMPFAIL)


def _core_lock(files, paths):
    from contextlib import ExitStack, contextmanager

    @contextmanager
    def held():
        with ExitStack() as stack:
            try:
                stack.enter_context(files.lock(paths.core_lock(), nested=True, timeout=0))
            except files.LockTimeout as exc:
                who = f"pid {exc.holder}" if exc.holder else "another process"
                print(f"loadout: waiting for an install or uninstall ({who}) to finish",
                      file=sys.stderr, flush=True)
                stack.enter_context(files.lock(paths.core_lock(), nested=True,
                                               timeout=CORE_WAIT))
            yield
    return held()


def _install(scope: Scope, replace: list[str], folder, resolver, lines: list[str]) -> Outcome:
    from moxi import place

    from moxi_loadout import manifest as manifest_mod
    from moxi_loadout import settings

    say = lines.append
    machine = settings.read()
    if folder is not None:
        machine.source = os.path.abspath(folder)
    data = ledger.read()
    old_source = data.get("source")
    for t in adoption.take(folder=machine.source, say=say):
        say(f"adopted skill {t.module}/{t.placed.name} for {t.placed.harness}")
        data["placed"][str(t.placed.path)] = {
            "section": USER, "kind": "skill", "item": f"{t.module}/{t.placed.name}",
            "harness": t.placed.harness, "module": t.module, "seeded": False}
    found = manifest_mod.load(machine.source)
    lines.extend(r.line for r in found.refused)
    claims = _claims(replace)
    written: list[Row] = []
    notes: list[str] = []

    def harness_entries() -> None:
        run = entries.plan(found, machine.source, data, claims=claims)
        written.extend(entries.apply(run, scope, data, keep=machine.backups_keep, say=say))
        lines.extend(run.lines)
        notes.extend(run.notes)

    # A mode change removes the old mode's part first: the managed parts go before the
    # placer places a global file, and a placed one goes before the managed parts are
    # written, so neither is ever written through the other.
    if found.global_mode != "managed":
        harness_entries()
    plan = planner.build(found, machine, data, resolver=resolver)
    in_scope = [w for w in plan.wants if scope.covers(w.scope, w.kind, w.name)]
    for want in in_scope:
        _write_generated(want, claims, say)
    planner.run(plan)

    mine = {str(p.path) for p in place.status(planner.OWNER)}
    rows: list[Row] = []
    plain, forced, seeded = [], [], []
    replaced: set[int] = set()   # the seeded wants `--replace` places over an occupant
    for want in in_scope:
        step = want.step
        state, _, _ = planner.state_of(want)
        if want.state is not None or step is None:
            rows.append(planner.row(want))
            continue
        in_the_way = step.action == "refuse" and step.cause in ("occupant", "tracked")
        if want.module and step.action == "add":
            seeded.append(want)
        elif want.module and in_the_way and _claimed(claims, want):
            # A module skill is placed as its module and handed over, never as loadout.
            seeded.append(want)
            replaced.add(id(want))
        elif step.action in ("add", "update", "keep") and state != "modified":
            plain.append(step)
        elif not want.module and in_the_way and (
                _claimed(claims, want) or _moved(step, mine, old_source)):
            forced.append(step)
        else:
            rows.append(planner.row(want))
    touched = _cover(in_scope, plain, forced, seeded, place.status(planner.OWNER), data,
                     rows, say)
    removes = [s for s in plan.removals if _remove_in_scope(plan, s, scope)]
    done = {str(p.path): p for p in place.apply(plain + removes, say=say)}
    done.update({str(p.path): p for p in place.apply(forced, replace=True, say=say)})
    now = {str(p.path) for p in place.status(planner.OWNER)}

    seeded_done: dict[str, object] = {}
    for want in seeded:
        result = adoption.place_seeded(want.module, want.item,
                                       replace=id(want) in replaced, say=say)
        if result.placed is not None:
            seeded_done[str(result.placed.path)] = result.placed
            say(f"placed skill {want.name} for {want.harness} ({result.placed.path})")
        elif result.step is not None:
            want.step = result.step
            rows.append(planner.row(want))
            continue
        else:
            rows.append(planner.row(want, "missing", result.line, ERROR))
            continue
        rows.append(planner.row(want, "ok", ""))

    for want in in_scope:
        step = want.step
        if step is None or want.state is not None or (want.module and want in seeded):
            continue
        if step in plain or step in forced:
            target = str(step.target)
            if target in done:
                if step.action != "keep":
                    verb = "updated" if step.action == "update" else "placed"
                    say(f"{verb} {want.label()} ({target})")
                rows.append(planner.row(want, "ok", "") if want.commit is None
                            else planner.row(want))
            else:
                rows.append(planner.row(want, "missing", f"not placed at {target}", ERROR))
    for step in removes:
        target = str(step.target)
        r = planner.removal_row(plan, step)
        if target in now:
            rows.append(Row(r.scope, r.kind, r.name, r.harness, "stale",
                            f"could not remove {target}", target, ERROR))
        else:
            say(f"removed {r.kind} {r.name} for {r.harness} ({target})")

    folders = None if scope.whole else touched | {os.path.dirname(str(s.target))
                                                  for s in removes}
    lines.extend(ignores.settle(data, place.status(planner.OWNER), folders, say=say))
    if found.global_mode == "managed":
        harness_entries()
    rows += written
    rows += [r for r in _ignore_rows(data) if _shown(scope, r)]
    unclaimed = [c for c in claims if not claims[c]]
    for text in unclaimed:
        lines.append(f"--replace {text}: nothing of loadout's is refused there")
    _write_ledger(plan, data, machine.source, done, seeded_done, removes, now)
    for name, res in plan.refused.items():
        if scope.sections() is not None and name not in scope.sections():
            continue
        if scope.item is not None and not any(
                w.scope == name and (w.kind, w.name) == scope.item for w in plan.wants):
            continue
        rows.append(Row(name, "section", name, "*", "blocked", res.line, None, res.code))
    codes = [r.code for r in rows if r.state not in FINE] + [REFUSED] * bool(unclaimed)
    return Outcome(rows, lines, list(dict.fromkeys(plan.notes + notes)),
                   planner.exit_code(codes))


def _cover(in_scope, plain, forced, seeded, placed, data, rows, say) -> set[str]:
    """The would-commit check, before anything is placed: each target the run will place
    or keep is checked against git's ignore rules; loadout's ignore file is written first
    where it covers a target, and a target git would still commit is taken out of the run
    with its row. Returns the folders checked."""
    from moxi.place import IN_PLACE

    chosen = {id(s) for s in plain} | {id(s) for s in forced}
    by_target: dict[str, list] = {}
    for want in in_scope:
        if want.step is None:
            continue
        if any(want is w for w in seeded) or (
                id(want.step) in chosen and planner.places(want, forced=True)):
            by_target.setdefault(str(want.step.target), []).append(want)
    verdicts = ignores.check(list(by_target), ignores.Git())
    have: dict[str, set[str]] = {}
    for p in placed:
        if p.form != IN_PLACE:
            have.setdefault(os.path.dirname(str(p.path)), set()).add(p.path.name)
    needs: dict[str, list[str]] = {}
    stopped: set[str] = set()
    for target, v in verdicts.items():
        if v.state == "needs":
            needs.setdefault(v.folder, []).append(target)
        elif v.state in ("would-commit", "unchecked"):
            line = v.line if v.state == "would-commit" else (
                f"loadout can't tell whether git would commit it ({v.line})")
            for want in by_target[target]:
                want.commit = line
            stopped.add(target)
    for folder, targets in needs.items():
        names = have.get(folder, set()) | {os.path.basename(t) for t in targets}
        problem = ignores.write(folder, names, data, by_target[targets[0]][0].scope, say)
        if problem:
            for t in targets:
                for want in by_target[t]:
                    want.commit = f"git would commit it; {problem}"
                stopped.add(t)
    for target in stopped:
        for want in by_target[target]:
            if want.step.action != "keep":
                rows.append(planner.row(want))
    plain[:] = [s for s in plain if s.action == "keep" or str(s.target) not in stopped]
    forced[:] = [s for s in forced if str(s.target) not in stopped]
    seeded[:] = [w for w in seeded if str(w.step.target) not in stopped]
    return {v.folder for v in verdicts.values()}


def _claims(replace: list[str]) -> dict[str, bool]:
    """Each `--replace` value, not yet matched: a target path or an id."""
    out = {}
    for text in replace:
        if not _is_path(text) and not text.startswith("global:"):
            try:
                parse_id(text)
            except ValueError as exc:
                raise ManifestError(None, [f"--replace {exc}"]) from None
        out[text] = False
    return out


def _is_path(text: str) -> bool:
    """Whether a `--replace` value is a target path: an id (`skill:demo/moxi-demo@claude`
    holds a `/` too) never is."""
    from moxi_loadout.manifest import _ID

    if _ID.fullmatch(text):
        return False
    return "/" in text or "\\" in text or text[:1] in ("~", ".")


def _claimed(claims: dict[str, bool], want) -> bool:
    """Whether a `--replace` value names `want`'s target or its id; marks it matched."""
    from moxi.place import target_key

    hit = False
    target = planner.target_of(want)
    for text in claims:
        if text.startswith("global:"):
            ok = want.kind == "global" and text.rsplit("@", 1)[-1] in ("*", want.harness)
        elif _is_path(text):
            ok = target is not None and target_key(text) == target
        else:
            ident = parse_id(text)
            ok = ident.covers(want.kind, want.name, want.harness)
        if ok:
            claims[text] = hit = True
    return hit


def _moved(step, mine: set[str], old_source) -> bool:
    """Whether a refused occupant is a link loadout made from the folder an earlier
    install read, before the folder moved."""
    from moxi.place import target_key

    if not old_source or str(step.target) not in mine or step.occupant is None:
        return False
    path = os.fspath(step.occupant)
    if not os.path.islink(path):
        return False
    pointed = os.path.normpath(os.path.join(os.path.dirname(path), os.readlink(path)))
    old = os.path.normpath(os.path.abspath(old_source))
    return target_key(pointed).startswith(old.rstrip(os.sep) + os.sep)


def _write_generated(want, claims: dict[str, bool], say) -> None:
    """Write `want`'s generated agent file when it needs writing: missing, out of date
    with its header intact, or edited by hand and named by `--replace`."""
    from moxi.files import atomic_write

    if want.generated is None or want.state is not None:
        return
    path, data, current = want.generated
    if current == "same":
        return
    if current == "modified" and not _claimed(claims, want):
        return
    atomic_write(path, data, mode=0o644)
    want.generated = (path, data, "same")
    if current == "modified":
        say(f"rewrote {path}, which was edited by hand")


def _remove_in_scope(plan: planner.Plan, step, scope: Scope) -> bool:
    record = plan.record(step)
    if record is None:
        return scope.whole and plan.resolved_all
    return scope.covers(record.get("section"), record.get("kind"), record.get("item"))


def _write_ledger(plan, data, source, done, seeded_done, removes, now) -> None:
    placed = data["placed"]
    for want in plan.wants:
        step = want.step
        if step is None or want.item is None:
            continue
        target = str(step.target)
        if target not in done and target not in seeded_done:
            continue
        record = {"section": want.scope, "kind": want.kind, "item": want.name,
                  "harness": want.harness, "place": ledger.item_record(want.item)}
        if want.module:
            old = placed.get(target, {})
            record["module"] = want.module
            record["seeded"] = target in seeded_done or bool(old.get("seeded"))
        placed[target] = record
    for step in removes:
        if str(step.target) not in now:
            placed.pop(str(step.target), None)
    for target in [t for t in placed if t not in now and t not in seeded_done]:
        del placed[target]
    data["source"] = os.fspath(source)
    ledger.write(data)


__all__ = ["INSTALL_WAIT", "CORE_WAIT", "SKIPPED", "Outcome", "status", "install"]
