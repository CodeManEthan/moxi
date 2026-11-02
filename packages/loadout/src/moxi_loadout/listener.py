"""loadout's `moxi.installs` listener: what a module's install and uninstall do to the
module skills loadout holds.

`moxi install <module>` calls `on_install(module, found)` after the module's skills step,
and `moxi uninstall <module>` calls `on_uninstall(module)` before the package goes. Both
run in the install's own process, under its claim on the core lock, so the placer's
calls here never wait for that install. Neither takes `install.lock` or writes loadout's
ledger or ignore files: a stale record or ignore line names an absent target, and the
next `moxi loadout install` ends both.

- **`on_install`** hands the module's own placements (made after a yes at its skills
  question) to loadout, unseeded, and lists each in the manifest with the harnesses it
  was placed for (`moxi_loadout.adoption.take`), so a yes is taken over in the same run.
  Then each placement of that module's skills that the manifest disables is removed, by
  target, through the placer: its `remove` step out of `plan("loadout", [])`, applied
  alone. One line for each, `loadout: removed moxi-demo for codex (disabled)`. A skill
  the manifest switches off is not left placed when the module's install ends cleanly.
  Nothing else of loadout's changes: this is not an install run, so no other item's
  steps are planned or applied.
- **`on_uninstall`** removes every placement of that module's skills loadout holds,
  seeded or not, the same way. The manifest keeps the item, which then reads `off`.

Which of loadout's placements are a module's: those its ledger records with that
module, those just taken over, and those whose recorded source lies in the module's
installed package (`moxi.place.from_package`), so one taken over and not yet recorded,
or a lost ledger, still counts. For loadout's own install and uninstall both do nothing:
`SETUP` and `TEARDOWN` do that work.

A failure raises, so the module's install or uninstall names it and exits 1. A disabled
skill left placed is removed by the next `moxi loadout install`'s plan, and so is a skill
whose module is gone: that plan reads the item `off` and removes the placement.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

OWNER = "loadout"


def on_install(module: str, found=None, *, say: Callable[[str], object] = print) -> None:
    """After `module`'s install: take its own placements over, then remove each placement
    of its skills the manifest disables."""
    if module == OWNER:
        return
    from moxi_loadout import adoption, manifest

    taken = adoption.take([module], say=say)
    for t in taken:
        say(f"loadout: took over {t.module}/{t.placed.name} for {t.placed.harness}")
    user = manifest.load().user
    off = [s for s in held(module, taken)
           if user.is_disabled("skill", f"{module}/{s.item.name}", s.item.harness)]
    _remove(off, "disabled", "moxi loadout install removes it", say)


def on_uninstall(module: str, *, say: Callable[[str], object] = print) -> None:
    """Before `module`'s package goes: remove every placement of its skills loadout
    holds, seeded or not."""
    if module == OWNER:
        return
    _remove(held(module), f"{module} is being uninstalled",
            "moxi loadout install removes it once the module is gone", say)


def held(module: str, taken: Iterable = ()) -> list:
    """loadout's `remove` steps for the placements it holds of `module`'s skills, one
    per target, out of `moxi.place.plan("loadout", [])`. Plans only; places nothing."""
    from moxi import modules, place

    from moxi_loadout import ledger

    targets = {str(t.placed.path) for t in taken}
    try:
        records = ledger.read()["placed"]
    except ledger.LedgerError:
        records = {}
    targets |= {key for key, r in records.items() if r.get("module") == module}
    found = modules.load(module)
    out = []
    for step in place.plan(OWNER, []):
        if step.action != "remove" or step.item.kind != "skill":
            continue
        if str(step.target) in targets or (
                found.ok and place.from_package(found, step.item.source)):
            out.append(step)
    return out


def _remove(steps: list, why: str, fix: str, say) -> None:
    """Apply `steps` (loadout's `remove` steps) and say a line for each target gone;
    raise naming each target still there."""
    if not steps:
        return
    from moxi import place

    place.apply(steps, say=say)
    now = {str(p.path) for p in place.status(OWNER)}
    left = []
    for step in steps:
        if str(step.target) in now:
            left.append(str(step.target))
        else:
            say(f"loadout: removed {step.item.name} for {step.item.harness} ({why})")
    if left:
        raise RuntimeError(f"could not remove {', '.join(left)} ({why}); {fix}")


__all__ = ["OWNER", "on_install", "on_uninstall", "held"]
