"""usage's interface 1, the module other Moxi modules import (`moxi_usage.api`).

Model facts (`moxi_usage.models`):

* `model_facts(model, *, provider=None, at=None)`: a model's price, cache prices, window,
  output limit and tiers (`ModelFacts`), by the lookup ladder over the shipped table, the
  snapshot layer and the config's `[[model]]` rows, with its `Provenance`. `at` is the
  request's own time in epoch seconds, which picks a dated override row's price period.
* `models()`: every row of the table in effect, each with its provenance.
* `context_window(model, reported=None)`: the window the harness reported when it gave
  one, else the table's, else `UNKNOWN`.
* `table()`: the table in effect (`Table`), for a caller that looks up many models.

`UNKNOWN` is sessions' sentinel: a fact the table does not know. It is neither None nor
0, and refuses to be read as true or false, so an unknown price never turns into $0.
Every result has `as_dict()` for JSON, where an unknown fact is left out and named in the
object's `"unknown"` list.

Every name is imported on first use, so importing this module costs next to nothing.
"""

from __future__ import annotations

import importlib
from typing import Any

#: Each name's home module, under this package; `UNKNOWN` is sessions'.
_HOMES: dict[str, str] = {
    **dict.fromkeys(("model_facts", "models", "context_window", "table", "ModelFacts",
                     "Provenance", "Table", "STEPS"), "models"),
}

__all__ = [*_HOMES, "UNKNOWN"]


def __getattr__(name: str) -> Any:
    if name == "UNKNOWN":
        from moxi_sessions.api import UNKNOWN as value
    else:
        home = _HOMES.get(name)
        if home is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        value = getattr(importlib.import_module(f"{__package__}.{home}"), name)
    globals()[name] = value              # later lookups find it without coming here
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
