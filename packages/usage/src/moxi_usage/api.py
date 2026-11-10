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
* `summary(table)`: what a table is made of: its effective date, its row count, each
  layer with its date and rows, the config's `[[model]]` rows, and what was skipped.

Limits (`moxi_usage.limits`):

* `limits(*, harness=None, now=None, pull=True)`: one `Row` per plan window of Claude
  and Codex, or of one `harness`, at `now` (epoch seconds; None is the clock): its
  state (`fresh`, `reset` or `unavailable`, with a reason), percent used, reset time,
  the age of its reading, when the window is filling, `full_at`, the time it
  reaches 100% at the recent rate, and `opened`, when the window started (None when the
  readings cannot say). With `pull`, a Codex reading older than
  `limits.codex_pull_after_s` is refreshed through turns when turns is installed. Returns
  `Limits`, whose `pull` says what the pull did. What a reading did not say, and every
  fact of a window with no reading, is `UNKNOWN`; the state's reason says which.

Context (`moxi_usage.context`):

* `context(session_id, *, harness=None, agent_id=None)`: how full a session's context
  is, or one of its agents', from its newest request, as a `Context`: `tokens` (the
  whole prompt), `window` (the one the harness reported, else the model table's) and
  `percent`, each `UNKNOWN` with a `reason` when it cannot be said; `text()` prints
  the line with its denominator. `harness` None asks Claude, Codex and OpenCode in that
  order. It reads through sessions alone, so it works without turns.

The restart point (`moxi_usage.measure`, `moxi_usage.thresholds`):

* `restart_point()`: the measured parameters per model and population (`main`,
  `subagent`) and each warn-at mark's threshold in tokens, as a `RestartPoint`. A mark
  that cannot be worked out is unavailable with its reason, never guessed.
* `threshold(model, mark, population="main")`: one mark's `Threshold`; `stamp()`, which
  changes whenever the measure or the model table does.

The warn-at mark (`moxi_usage.marks`; the listener is `moxi_usage.mark`):

* `mark(session_id, *, harness=None)`: the marks in force for the session's notice (a
  line to the model) and its crossing (a call of each `moxi.usage.crossings` handler
  with a `Crossing`), each mark's threshold for the session's model, its current
  context and each purpose's state, `armed`, `fired` with when, or `off`, as a
  `MarkState`; its agents' fired notices in `agents`.

Reports (`moxi_usage.reports`):

* `report(kind, *, since=None, until=None, harness=None, project=None, model=None,
  tz=None, fresh=True, session=None, by=None, agent=None, folder=None)`: tokens and
  cost from the token index, `kind` one of `KINDS` (`daily`, `monthly`, `sessions`,
  `models`, `projects`, `session`, `terms`, `requests`), as a `Report`. With `fresh` the
  index's pass runs first. `tz` is the zone days are cut in, local time when None.
  `folder` is a folder's whole path and keeps the files whose `cwd` it is, whatever
  project key they carry; the `projects` kind with `by="folder"` groups by `cwd` alone.
  Cost is worked out when the report runs; tokens the table cannot price are counted in `unpriced_tokens`, never
  as $0. The `session` kind is one session, the one `session` names (an id a `sessions`
  row gives, with `harness` to tell two harnesses apart), by agent and model; it needs
  `session`. The `terms` kind prints every term of each cost, by `model` (the default),
  `day`, `month` or, with `session`, `agent` (`by`): each row's price groups with each
  token class's tokens, price and dollars, and its cost split by class and price tier;
  `session` alone narrows it to one session. The `requests` kind is one row per request
  of one session's agent (`session`, which it needs, and `agent`, `main` by default):
  its tokens by class, cost and its split, its context against the model's window, its
  turn, and the agent's compactions placed between the rows. A keyword a kind does not
  read is refused, so an unfiltered answer is never read as a filtered one.
  The last kind, `status`, is the index itself: one row per lane with its state, files,
  excluded, held and duplicate counts, and the last pass and schema version in `total`;
  it takes only `harness`.

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
    **dict.fromkeys(("model_facts", "models", "context_window", "table", "summary",
                     "ModelFacts", "Provenance", "Table", "STEPS"), "models"),
    **dict.fromkeys(("limits", "Limits", "Row", "Pull"), "limits"),
    **dict.fromkeys(("report", "Report", "ReportError", "KINDS"), "reports"),
    **dict.fromkeys(("context", "Context"), "context"),
    **dict.fromkeys(("restart_point", "RestartPoint"), "measure"),
    **dict.fromkeys(("threshold", "Threshold", "stamp", "MARKS"), "thresholds"),
    **dict.fromkeys(("mark", "MarkState", "PurposeState"), "marks"),
    "Crossing": "mark",
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
