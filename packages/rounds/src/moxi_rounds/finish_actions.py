"""The finish actions of a build as its log has them: the records at a tip, the
operator's skips, and what keeps a command from being run.

Each action's records are kept under its `id`, for the build's tip `T`. `records` gives
the newest of each. A `command` is run by each `finish` that reaches it without a
result of `ok`, by its newest record:

- `ok` (or `found`, for a merge): never run again;
- `failed`, or no record: run;
- `started` with no result: a finish died while it ran, and nobody can tell whether it
  did its work. It is run again only when its `repeat` is set, or when the lead asks
  for it once with `finish --again ID`; otherwise `finish` refuses and names the two
  ways on: that run once more, or the operator's skip of the action.

**A skip of an action** is the operator's ruling `skip-action:B<n>=ID`, a skip like
any other (`skips.action_live`). While it stands, `finish` does not run the action for
that stage and treats it as done; the ruling is the record, and no `ok` is written.

**What a finish left** (`kept_of`): each thing a finish left behind is on the log, a
`finish.kept` event apiece for the build's worktree, scratch folder and branch, and the
checkout of the target a merge added and could not remove in that merge's own record,
under `kept`. `show` lists them under the build and the board counts them.

`blocked` is the one answer to "may this finish run its commands", read by `finish`
before it takes any lock and by the finish step's row, so the row never reads ready
where the verb refuses. `again_refused` is what `--again ID` is refused for.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from . import builds, skips
from . import settings as own
from . import state as states
from .errors import Unmet

#: The event of one finish action's result.
ACTION = "finish.action"
#: The event of one thing a finish left behind.
KEPT = "finish.kept"
#: What a finish leaves behind: the build's worktree, its scratch folder or its branch,
#: each a `finish.kept` of its own; or the checkout of the target a merge added, which
#: that merge's record holds as its `kept`.
WORKTREE, SCRATCH, BRANCH, CHECKOUT = "worktree", "scratch", "branch", "checkout"
#: The states of a finish action's record: run and passed, run and failed, or a merge
#: found already on the target.
OK, FAILED, FOUND = "ok", "failed", "found"
#: The state of a command's record appended before it runs.
STARTED = "started"
#: The states of a record that mean the action is not run again.
SETTLED = (OK, FOUND)
#: Why a command that started and has no result is run again: its `repeat` setting, or
#: the lead's `--again`.
REPEAT, AGAIN = "repeat", "again"


def records(state: states.State, build: builds.Build, tip: str) -> dict[str, dict]:
    """The newest `finish.action` record of each action id for the build at `tip`."""
    newest: dict[str, dict] = {}
    for event in state.history:
        if (event["type"] == ACTION and event["n"] > build.n
                and event.get("stage") == build.stage and event.get("tip") == tip
                and isinstance(event.get("id"), str)):
            newest[event["id"]] = event
    return newest


def kept_of(state: states.State, build: builds.Build) -> list[dict]:
    """What the finishes of `build` left behind, as its log has it, oldest first, each
    as `{"what", "where", "why", "n", "at"}`: each `finish.kept`, and each checkout of
    the target a merge action's record holds as its `kept`. `where` is as the record
    holds it: a folder with `~` for the home folder, or a branch's name. A record is the
    build's from its commission up to the stage's next commission."""
    found = []
    for event in state.history:
        if event["n"] <= build.n or event.get("stage") != build.stage:
            continue
        if event["type"] == "build.commissioned":
            break
        if event["type"] == KEPT:
            found.append({"what": event.get("what"), "where": event.get("where"),
                          "why": event.get("why"), "n": event["n"],
                          "at": event.get("at")})
        elif event["type"] == ACTION and isinstance(event.get("kept"), Mapping):
            kept = event["kept"]
            found.append({"what": CHECKOUT, "where": kept.get("where"),
                          "why": kept.get("why"), "n": event["n"],
                          "at": event.get("at")})
    return found


def skipped(state: states.State, stage: str) -> dict[str, str]:
    """The id of each action the operator skipped for `stage`, with the ruling that
    skips it."""
    return {one.action: one.id for one in skips.action_live(state) if one.stage == stage}


def run_again(action: Mapping, last: Mapping | None, *, again: str | None = None
              ) -> str | None:
    """Why a command whose newest record is `last` is run again though it started and
    has no result: `REPEAT` or `AGAIN`; None when it did not start without a result, or
    when nothing says to run it again."""
    if last is None or last.get("state") != STARTED:
        return None
    if action.get("repeat") is True:
        return REPEAT
    return AGAIN if again == action["id"] else None


def blocked(state: states.State, build: builds.Build, tip: str,
            actions: Sequence[Mapping], *, name: str, worktree_gone: bool,
            again: str | None = None) -> list[Unmet]:
    """Why a command action due at `tip` can't be run: it started, has no result and
    nothing says to run it again; or the build's worktree it runs in is gone. An action
    the operator skipped for the stage is not run, so nothing keeps it."""
    newest = records(state, build, tip)
    passed = skipped(state, build.stage)
    found = []
    for action in actions:
        last = newest.get(action["id"])
        if (action["do"] != own.COMMAND or action["id"] in passed
                or (last is not None and last.get("state") in SETTLED)):
            continue
        if (last is not None and last.get("state") == STARTED
                and run_again(action, last, again=again) is None):
            found.append(Unmet(
                f"the finish action {action['id']} of {build.stage} started at "
                f"{last.get('at')} and has no result: it may have done its work, so it "
                "is not run again on its own",
                f"look at what it did; if it did not do its work, or running it twice "
                f"is harmless: moxi rounds finish {name} {build.stage} --again "
                f"{action['id']}; if it did its work, the operator's skip: moxi rounds "
                f"rule {name} {skips.action_item(build.stage, action['id'])} "
                "--record PTR"))
        elif worktree_gone:
            found.append(Unmet(
                f"the finish action {action['id']} runs in {build.stage}'s worktree, "
                "which is gone", f"moxi rounds show {name}"))
    return found


def again_refused(state: states.State, build: builds.Build, tip: str,
                  actions: Sequence[Mapping], again: str, *, name: str,
                  repo: str) -> list[Unmet]:
    """Why `--again again` is refused: it is taken only for a `command` whose newest
    record at `tip` is `started` with no result, and that the operator did not skip."""
    plain = f"moxi rounds finish {name} {build.stage}"
    action = next((one for one in actions if one["id"] == again), None)
    if action is None:
        listed = ", ".join(one["id"] for one in actions) or "none"
        return [Unmet(f"{repo} has no finish action {again}; its actions are {listed}",
                      f"name one of them, or leave --again out: {plain}")]
    if action["do"] != own.COMMAND:
        return [Unmet(f"{again} is a {action['do']} action, and --again runs a command "
                      f"once more: a {action['do']} is run by a plain finish until it "
                      "is recorded ok", plain)]
    ruling = skipped(state, build.stage).get(again)
    if ruling is not None:
        return [Unmet(f"the operator skipped {again} for {build.stage}, by {ruling}, so "
                      "finish does not run it", plain)]
    last = records(state, build, tip).get(again)
    if last is not None and last.get("state") in SETTLED:
        return [Unmet(f"{again} of {build.stage} is recorded ok at {tip[:12]}, and an "
                      "action recorded ok is never run again", plain)]
    if last is None or last.get("state") != STARTED:
        how = "has no record" if last is None else "is recorded failed"
        return [Unmet(f"{again} of {build.stage} {how} at {tip[:12]}, so a plain finish "
                      "runs it; --again is for a command that started and has no result",
                      plain)]
    return []


__all__ = ["ACTION", "KEPT", "WORKTREE", "SCRATCH", "BRANCH", "CHECKOUT", "OK", "FAILED",
           "FOUND", "STARTED", "SETTLED", "REPEAT", "AGAIN", "records", "kept_of", "skipped",
           "run_again", "blocked", "again_refused"]
