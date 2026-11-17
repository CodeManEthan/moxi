"""What the page's view says of each row of `status`, beyond the row itself.

Each fact is read from what the row came from (`install.Status.sources`), on the plan
`status` built, so the view never plans twice and never disagrees with `status`:

- `form`: how loadout places the item: `link`, `junction` or `copy` (the placer's form),
  `generated` (an agent file loadout makes for Codex or OpenCode), `entry` (a hook,
  permission, MCP server, rules file or managed global part in a harness file), or None
  (loadout's ignore file);
- `edited`: a `blocked` row whose placement of loadout's was edited where it stands, as
  the placer reads it (`modified`): its own copy edited, or a file or folder saved over
  its link. A link that points elsewhere, or anything else in the way, is not;
- `covers`: the permission items the Codex rules file holds, on its row;
- `prior`: an entry the user had before loadout, which loadout keeps as the user's;
- `applies`: the sections under which `install` applies the row, by the verb's own
  predicates: `section` when a run naming the row's section does, `whole` when only a
  whole run does, None when no run does (a refused section's row, a removal with no
  record while a section is refused, an ignore file);
- `ignores`: a `would-commit` row that install covers by writing loadout's ignore file.

And of the plan beside the rows:

- each section's refusal (`sections`): its `cause` (`planner.CAUSES`), the refusal's
  `line`, and `last_root`, the root loadout's ledger recorded for the section when every
  record of it that carries one names the same root (a held item's), else None;
- each item's `module` (a module skill's module) and `what` it writes (`item_what`).

Reads only; writes nothing.
"""

from __future__ import annotations

import os

from moxi_loadout import entries, planner
from moxi_loadout.manifest import USER

#: The placer's forms.
FORMS = ("link", "junction", "copy")
#: The values `applies` takes, besides None.
APPLIES = ("section", "whole")


def row_facts(done) -> list[dict]:
    """The facts above for each of `done`'s rows (an `install.Status`), in its order."""
    plan = done.plan
    edited_at = _Edited()
    out = []
    for row, (source, src) in zip(done.outcome.rows, done.sources):
        if source == "want":
            facts = _want(plan, src)
        elif source == "removal":
            facts = _removal(plan, src)
        elif source == "entry":
            facts = _entry(row, src)
        else:
            facts = {"form": None, "covers": None, "prior": False, "applies": None,
                     "ignores": False, "placement": None}
        placement = facts.pop("placement")
        facts["edited"] = row.state == "blocked" and placement in FORMS \
            and edited_at(row.target, placement)
        out.append(facts)
    return out


class _Edited:
    """Whether a target's placement of loadout's was edited where it stands, reading the
    placer's status once, when first asked."""

    def __init__(self) -> None:
        self.states: dict[str, str] | None = None

    def __call__(self, target: str | None, placement: str) -> bool:
        if target is None:
            return False
        if self.states is None:
            from moxi import place

            self.states = {str(p.path): p.state for p in place.status(planner.OWNER)}
        if self.states.get(target) != "modified":
            return False
        if placement == "copy":
            return True
        return not (os.path.islink(target) or os.path.isjunction(target))


def _want(plan, want) -> dict:
    step = want.step
    placement = step.form if step is not None and step.form in FORMS else None
    generated = want.kind == "agent" and want.generated is not None
    return {"form": "generated" if generated else placement, "covers": None,
            "prior": False, "applies": _want_applies(plan, want),
            "ignores": want.ignores, "placement": placement}


def _want_applies(plan, want) -> str | None:
    if not want.section.is_user and want.section.name in plan.refused:
        return None
    return _applies(lambda scope: scope.covers(want.scope, want.kind, want.name),
                    want.scope)


def _removal(plan, step) -> dict:
    placement = step.form if step.form in FORMS else None
    return {"form": placement, "covers": None, "prior": False,
            "applies": _removal_applies(plan, step), "ignores": False,
            "placement": placement}


def _removal_applies(plan, step) -> str | None:
    """As `install` applies a removal: a record's under its section while the manifest
    still has that section, else only in a whole run; one with no record only in a whole
    run in which every section resolved."""
    record = plan.record(step)
    if record is None:
        return "whole" if plan.resolved_all else None
    section = record.get("section")
    kind, name = record.get("kind"), record.get("item")
    named = section == USER or any(s.name == section for s in plan.manifest.projects)
    if not named:
        return "whole" if planner.Scope().covers(section, kind, name) else None
    return _applies(lambda scope: scope.covers(section, kind, name), section)


def _entry(row, action) -> dict:
    if action is None:
        # A row the run states with no action: an item off, or one that can't be built.
        applies = "whole" if row.kind == "global" else "section"
        return {"form": "entry", "covers": None, "prior": False, "applies": applies,
                "ignores": False, "placement": None}
    entry = action.entry
    covers = list(entry.by) if entry is not None and entry.kind == "rules" else None
    prior = bool(action.prior) or (action.op != "add" and bool((action.record or {})
                                                               .get("prior")))
    return {"form": "entry", "covers": covers, "prior": prior,
            "applies": _applies(lambda scope: entries.covers(scope, action), USER),
            "ignores": False, "placement": None}


def _applies(covered, section: str | None) -> str | None:
    """`section` when a run naming `section` applies the row (`covered(scope)`), else
    `whole` when a whole run does, else None."""
    if section is not None:
        named = planner.Scope(user=True) if section == USER \
            else planner.Scope(projects=(section,))
        if covered(named):
            return "section"
    return "whole" if covered(planner.Scope()) else None


def sections(plan) -> list[dict]:
    """Each section of the manifest, `{name, where, root, refused}`, `refused` None for a
    section that resolved."""
    out = []
    for section in plan.manifest.sections():
        res = None if section.is_user else plan.refused.get(section.name)
        refused = None if res is None else {
            "cause": res.cause, "line": res.line, "last_root": last_root(plan, section.name)}
        out.append({"name": section.name, "where": section.where, "root": section.root,
                    "refused": refused})
    return out


def last_root(plan, section: str) -> str | None:
    """The root loadout last placed `section` under, by its ledger: the one root every
    record of the section that carries one names (a held item's), else None. Records
    with no root count neither way."""
    roots = {(record.get("place") or {}).get("root")
             for record in plan.ledger.get("placed", {}).values()
             if isinstance(record, dict) and record.get("section") == section}
    roots.discard(None)
    return roots.pop() if len(roots) == 1 else None


def item_facts(plan, kind: str, name: str) -> dict:
    """`{module, what}` for an item: the module a module skill comes from, else None;
    what it writes, as `item_what` gives it."""
    from moxi_loadout.manifest import is_module_skill

    module = name.partition("/")[0] if kind == "skill" and is_module_skill(name) else None
    return {"module": module, "what": item_what(plan.manifest, kind, name,
                                                 loadout_dir=plan.loadout_dir)}


#: The keys of `item_what`'s answer, each None where the kind has none.
WHAT_KEYS = ("event", "matcher", "command", "url", "allow", "ask", "deny")


def item_what(found, kind: str, name: str, *, loadout_dir=None) -> dict | None:
    """What an item writes, for the opened item: a hook's `event`, `matcher` and
    `command` (expanded as the harness file gets it); a permissions item's `allow`, `ask`
    and `deny`, each its shell prefixes as text; an MCP server's `command` or `url`.
    None for a skill or an agent. Every key of `WHAT_KEYS` is there, None where unset.
    `{source}` expands to `loadout_dir`, the manifest's folder when None."""
    from moxi_loadout.manifest import expand_command

    table = found.item(kind, name)
    out = dict.fromkeys(WHAT_KEYS)
    if kind == "hook":
        command = table.get("command")
        out.update(event=table.get("event"), matcher=table.get("matcher") or None,
                   command=None if command is None
                   else expand_command(command, loadout_dir or found.loadout_dir))
    elif kind == "permissions":
        for decision in ("allow", "ask", "deny"):
            out[decision] = [" ".join(rule["shell"]) for rule in table.get(decision, ())
                             if isinstance(rule, dict) and "shell" in rule]
    elif kind == "mcp":
        out.update(command=table.get("command"), url=table.get("url"))
    else:
        return None
    return out


__all__ = ["FORMS", "APPLIES", "WHAT_KEYS", "row_facts", "sections", "last_root",
           "item_facts", "item_what"]
