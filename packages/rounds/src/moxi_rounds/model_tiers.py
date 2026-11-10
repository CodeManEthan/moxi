"""Whether the model a report names is weaker than the tier its pass is held to.

A step of a flow names a tier as `model`, and a step that verifies may name a second one
as `recheck_model`. The `models` setting maps each tier to a model per harness:

    [models]
    first  = { claude = "model-one", codex = "model-a" }
    second = { claude = "model-two", codex = "model-b" }

The order the tiers are written in is their order, strongest first. Nothing else orders
them: a tier's name is a free word.

A full pass is held to the step's `model`. Any other pass is held to its
`recheck_model`, and to `model` when the step names none. `flags` gives `BELOW_TIER`
for a report whose model is in a tier written after the one its pass is held to. A
report does not say which harness wrote it, so its model is looked up under every
harness and placed in the strongest tier that lists it under any. It is a note on the
record and refuses nothing; rounds can't prove which model ran.

There is no note when the question can't be answered: the table is empty, the report
names no model, the model is in no tier, the step names no tier, or the step's tier is
not in the table.
"""

from __future__ import annotations

from . import report_block

#: The note on a report whose model is in a weaker tier than its pass is held to.
BELOW_TIER = "model-below-tier"


def step_tier(steps, step: str, key: str) -> str | None:
    """The tier the entry of `step` among a flow's entries `steps` names as `key`
    (`model` or `recheck_model`), or None when there is no such entry or it names
    none."""
    for entry in steps:
        if isinstance(entry, dict) and entry.get("step") == step:
            tier = entry.get(key)
            return tier if isinstance(tier, str) and tier else None
    return None


def held_to(steps, step: str, pass_: str | None) -> str | None:
    """The tier a pass `pass_` of `step` is held to: the step's `model` for a full
    pass; for any other its `recheck_model`, or its `model` when it names none."""
    if pass_ == report_block.FULL:
        return step_tier(steps, step, "model")
    return step_tier(steps, step, "recheck_model") or step_tier(steps, step, "model")


def tier_of(models, model: str | None) -> str | None:
    """The strongest tier of the table `models` that lists `model` under any harness,
    or None when no tier does."""
    if not model or not isinstance(models, dict):
        return None
    for tier, named in models.items():
        if isinstance(named, dict) and model in named.values():
            return tier
    return None


def below(models, *, model: str | None, tier: str | None) -> bool:
    """Whether `model` is in a tier written after `tier` in the table `models`. False
    whenever either tier can't be placed in the table."""
    if not isinstance(models, dict) or tier not in models:
        return False
    found = tier_of(models, model)
    if found is None:
        return False
    order = list(models)
    return order.index(found) > order.index(tier)


def flags(models, steps, step: str, *, pass_: str | None,
          model: str | None) -> list[str]:
    """What is noted of a report of `step` whose pass was `pass_`, written by `model`,
    in a round whose recorded flow has the entries `steps`, with the table `models` as
    the settings hold it."""
    tier = held_to(steps, step, pass_)
    return [BELOW_TIER] if below(models, model=model, tier=tier) else []


__all__ = ["BELOW_TIER", "below", "flags", "held_to", "step_tier", "tier_of"]
