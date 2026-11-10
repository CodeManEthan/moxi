"""Flows: the steps rounds defines, the three flows it ships, and the check of a flow a
config file writes.

A flow is a named list of step entries. An entry is a table: `step` names one of rounds'
ten steps or a step of the user's own, and the other keys say who does it, at which model
tier and what they read first. A step of the user's own has one of two shapes: a report
someone writes (`does = "report"`) or a command rounds runs (`does = "command"`, with its
`argv`). Both are done before `finish`.

`read(settings)` gives every flow by name as plain lists of tables, the built-in ones
first; a flow the config names like a built-in one stands in its place. `check(settings)`
refuses a flow the rest of rounds could not follow, and a tier that names no flow.
"""

from __future__ import annotations

import copy
import re

from moxi.config import ConfigError

#: A step done once for the round.
ROUND = "round"
#: A step done once for each build.
STAGE = "stage"

#: The ten steps rounds defines, each with what it is done for.
STEPS = {
    "design": ROUND,
    "verify-design": ROUND,
    "docket": ROUND,
    "commission": STAGE,
    "build": STAGE,
    "check": STAGE,
    "verify-build": STAGE,
    "finish": STAGE,
    "ratify": ROUND,
    "retro": ROUND,
}
#: The two steps a checker does, which also take `recheck_model`.
VERIFY_STEPS = ("verify-design", "verify-build")
#: The two steps that are the operator's, which take no `by`.
OPERATOR_STEPS = ("docket", "ratify")
#: The steps a flow has without listing them: the first three whenever it has `build`,
#: `docket` whenever it has `verify-design`.
UNLISTED_STEPS = ("commission", "check", "ratify", "docket")

#: The two things a step of the user's own may be.
REPORT = "report"
COMMAND = "command"
DOES = (REPORT, COMMAND)
#: The one step a user's own step may come before.
BEFORE = "finish"

#: What a user's own step may be called.
_USER_STEP_NAME = re.compile(r"[a-z0-9-]+\Z")

#: The flows rounds ships, lightest first.
BUILT_IN = {
    "fix": [
        {"step": "build", "by": "builder", "model": "second"},
        {"step": "verify-build", "by": "checker", "model": "third"},
        {"step": "finish", "by": "builder"},
    ],
    "small": [
        {"step": "design", "by": "lead", "model": "second"},
        {"step": "build", "by": "builder", "model": "second"},
        {"step": "verify-build", "by": "checker", "model": "second",
         "recheck_model": "third"},
        {"step": "finish", "by": "builder"},
        {"step": "retro", "by": "worker", "model": "third"},
    ],
    "full": [
        {"step": "design", "by": "lead", "model": "second"},
        {"step": "verify-design", "by": "checker", "model": "second",
         "recheck_model": "third"},
        {"step": "build", "by": "builder", "model": "second"},
        {"step": "verify-build", "by": "checker", "model": "second",
         "recheck_model": "third"},
        {"step": "finish", "by": "builder"},
        {"step": "retro", "by": "worker", "model": "third"},
    ],
}


def is_user_step(entry) -> bool:
    """True when step entry `entry` names a step of the user's own, not one of the ten."""
    return entry["step"] not in STEPS


def read(settings) -> dict[str, list[dict]]:
    """Every flow by name, each a list of step entries: the built-in flows, then the
    config's own, one named like a built-in standing in its place. The lists are the
    caller's to keep or change."""
    flows = copy.deepcopy(BUILT_IN)
    for name, flow in settings["flows"].items():
        flows[name] = copy.deepcopy(flow["steps"])
    return flows


def check(settings) -> None:
    """Refuse a flow of `settings` that is not a list of well-formed steps or that leaves
    out a step another of its steps needs, and a tier that names no flow. Raises
    `moxi.config.ConfigError` naming the key and the fix."""
    file = settings.file
    for name, flow in settings["flows"].items():
        _check_flow(file, f"flows.{name}", flow)
    known = read(settings)
    for tier in settings["tiers"]:
        if tier not in known:
            raise ConfigError(
                file, "tiers", f"names {tier!r}, which is no flow",
                f"name only flows ({', '.join(known)}), or add [flows.{tier}]")


def _check_flow(file, key, flow) -> None:
    if not isinstance(flow, dict):
        raise ConfigError(file, key, f"is {kind_of(flow)}, not a table",
                          f"write it as a [{key}] table with a `steps` list")
    for other in flow:
        if other != "steps":
            raise ConfigError(file, f"{key}.{other}", "is not a key of a flow",
                              "remove it; a flow holds `steps` and nothing else")
    steps = flow.get("steps")
    if not isinstance(steps, list):
        raise ConfigError(
            file, f"{key}.steps",
            "is missing" if steps is None else f"is {kind_of(steps)}, not a list",
            "give the flow's steps as a list of tables, each with `step`")
    names = []
    for index, entry in enumerate(steps):
        names.append(_check_entry(file, f"{key}.steps[{index}]", entry))
    key = f"{key}.steps"
    for name in names:
        if names.count(name) > 1:
            raise ConfigError(file, key, f"lists the step {name!r} twice",
                              "list each step once")
    user = [name for name in names if name not in STEPS]
    if "verify-design" in names and "design" not in names:
        raise ConfigError(file, key, "has `verify-design` and no `design`",
                          "add a `design` step, or remove `verify-design`")
    for name in ("verify-build", "finish", *user):
        if name in names and "build" not in names:
            raise ConfigError(file, key, f"has {name!r} and no `build`",
                              f"add a `build` step, or remove {name!r}")
    if "build" in names and "finish" not in names:
        raise ConfigError(file, key, "has `build` and no `finish`",
                          "add a `finish` step, or remove `build`")


def _check_entry(file, key, entry) -> str:
    """Check one step entry and return the step's name."""
    if not isinstance(entry, dict):
        raise ConfigError(file, key, f"is {kind_of(entry)}, not a table",
                          'write each step as a table: { step = "build", by = '
                          '"builder" }')
    name = entry.get("step")
    if not isinstance(name, str) or not name:
        raise ConfigError(file, f"{key}.step",
                          "is missing" if name is None else "is not a step's name",
                          f"name one of {', '.join(STEPS)}, or a step of your own")
    if name in STEPS:
        allowed = ["step", "by", "model", "reads"]
        if name in VERIFY_STEPS:
            allowed.append("recheck_model")
        if name in OPERATOR_STEPS:
            allowed.remove("by")
        for other in entry:
            if other not in allowed:
                raise ConfigError(
                    file, f"{key}.{other}", f"is not a key of the step {name!r}",
                    f"remove it; {name!r} takes {_listed(allowed)}")
    else:
        _check_user_step(file, key, name, entry)
    for text in ("by", "model", "recheck_model"):
        if text in entry and (not isinstance(entry[text], str) or not entry[text]):
            raise ConfigError(file, f"{key}.{text}", "is not a name",
                              "give it as a string, or leave the key out")
    if "reads" in entry and not _strings(entry["reads"]):
        raise ConfigError(file, f"{key}.reads", "is not a list of strings",
                          "give each line to read as a string")
    return name


def _check_user_step(file, key, name, entry) -> None:
    """Check the entry of a step that is none of the ten: its name and its shape."""
    if not _USER_STEP_NAME.match(name):
        raise ConfigError(
            file, f"{key}.step",
            f"is {name!r}, which is none of rounds' steps and not a name a step of your "
            "own may have",
            f"name one of {', '.join(STEPS)}, or call your own step by lower-case "
            "letters, digits and `-`")
    does = entry.get("does")
    if does not in DOES:
        raise ConfigError(
            file, f"{key}.does",
            f"is missing, and {name!r} is none of rounds' steps" if does is None
            else f"is {does!r}, which it cannot be",
            f'say what your step is: does = "{REPORT}" or does = "{COMMAND}"')
    if entry.get("before") != BEFORE:
        raise ConfigError(
            file, f"{key}.before",
            "is missing" if "before" not in entry
            else f"is {entry['before']!r}, which it cannot be",
            f'write before = "{BEFORE}": a step of your own is done before `{BEFORE}`')
    allowed = ["step", "does", "before", "by", "model", "reads"]
    if does == COMMAND:
        allowed += ["argv", "timeout_s"]
    for other in entry:
        if other not in allowed:
            raise ConfigError(
                file, f"{key}.{other}", f"is not a key of a {does} step",
                f"remove it; a {does} step takes {_listed(allowed)}")
    if does == COMMAND:
        check_argv(file, f"{key}.argv", entry.get("argv"))
        check_timeout(file, f"{key}.timeout_s", entry.get("timeout_s"))


def check_argv(file, key, argv) -> None:
    """Refuse an `argv` that is not a list of strings with a command first."""
    if argv is None:
        raise ConfigError(file, key, "is missing",
                          'give the command as a list: argv = ["./run.sh", "{tip}"]')
    if not _strings(argv) or not argv or not argv[0]:
        raise ConfigError(file, key, "is not a command",
                          "give a list of strings, the command first")


def check_timeout(file, key, timeout_s) -> None:
    """Refuse a `timeout_s` that is set and is not a whole number of seconds above 0."""
    if timeout_s is None:
        return
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or timeout_s <= 0:
        raise ConfigError(file, key, "is not a number of seconds",
                          "give a whole number above 0, or leave the key out")


def _strings(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _listed(names) -> str:
    return ", ".join(f"`{name}`" for name in names)


def kind_of(value) -> str:
    """What `value` is, in the words of the file it came from."""
    kinds = ((bool, "a boolean"), (int, "an int"), (float, "a float"), (str, "a string"),
             (list, "a list"), (dict, "a table"))
    return next((text for kind, text in kinds if isinstance(value, kind)), "a value")


__all__ = ["ROUND", "STAGE", "STEPS", "VERIFY_STEPS", "OPERATOR_STEPS", "UNLISTED_STEPS",
           "REPORT", "COMMAND", "DOES", "BEFORE", "BUILT_IN", "is_user_step", "read",
           "check", "check_argv", "check_timeout", "kind_of"]
