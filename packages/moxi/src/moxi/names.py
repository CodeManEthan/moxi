"""Moxi's names: the constants every public spelling is built from, and the spellings.

A Moxi module has one name, lower-case ASCII letters and digits (`cache`, `comms`).
Everything else a module is called is spelled from that name here:

    distribution("cache")   moxi-cache          the package on PyPI
    import_name("cache")    moxi_cache          the Python package
    repo("cache")           CodeManEthan/moxi   the repository that holds it and its issues
    env_prefix("cache")     MOXI_CACHE_         the prefix of every variable it reads

Every module lives in the one repository, so `repo` spells the same for each. The core
itself is `moxi` as a distribution, an import package and a prefix, lives in that same
repository, and is not a module.

Every skill Moxi ships, each subfolder `is_skill_folder` counts in a module's `SKILLS`
folder, is named `moxi-<name>` (`is_skill_name`), and its `SKILL.md` `name:` is that
folder name. The prefix keeps a shipped skill from taking the name of a user's own.
"""

from __future__ import annotations

import re

#: The harness.
HARNESS = "moxi"
#: The GitHub account that owns the public repository, which holds every module and its
#: issues.
OWNER = "CodeManEthan"
#: The same account under its earlier name, kept so code that reads `ORG` still works.
#: The owner is a user account, not an organisation: use `OWNER`.
ORG = OWNER
#: The directory namespace under the data, state, cache, config and runtime roots,
#: shared by every module, each under its own subdirectory.
NAMESPACE = "moxi"

#: The umbrella's own verbs (`moxi install`, `moxi config`, ...), with the ones this
#: version keeps for commands still to come. A module named by one could never be
#: reached as `moxi <module>`, so no module may take one. `check` only says where the
#: contract check went, and stays reserved so no module takes the name a contributor
#: still types.
VERBS = frozenset({"modules", "install", "shortcut", "config", "check", "help", "version",
                   "doctor", "uninstall", "run", "paths"})

#: Why the core keeps each name of `RESERVED`, one reason per name, as `reserved_why`
#: gives it. `test` is kept because `MOXI_TEST_` is the prefix of the variables
#: moxi-dev's test tiers read (moxi-dev's `contract.DEV_ENV_PREFIX`): a module named
#: `test` would have that prefix for its own variables, and the two would collide.
_RESERVED_WHY = {
    "moxi": "it is the core's own state directory",
    "set": "it is the first word of `moxi config set`",
    "unset": "it is the first word of `moxi config unset`",
    "runtime": "it holds every module's runtime directory where the system gives none",
    "installs": "it is the core's entry-point group moxi.installs",
    "test": "its variables would start MOXI_TEST_, which moxi-dev's test tiers use",
}

#: Names the core uses for itself, so no module may take one; `reserved_why` says why,
#: per name.
RESERVED = frozenset(_RESERVED_WHY)

_NAME = re.compile(r"[a-z0-9]+")

#: The prefix of every skill Moxi ships, so none takes the name of a skill the user has.
SKILL_PREFIX = f"{HARNESS}-"
#: The longest skill name, the cap the Agent Skills format sets on a skill's `name`.
SKILL_NAME_MAX = 64
_SKILL = re.compile(re.escape(SKILL_PREFIX) + r"[a-z0-9]+(-[a-z0-9]+)*")


def is_module_name(name: object) -> bool:
    """True when `name` is spelled as a module name: lower-case ASCII letters and digits."""
    return isinstance(name, str) and _NAME.fullmatch(name) is not None


def is_reserved(name: object) -> bool:
    """True when no module may take `name`: a verb, or a name the core keeps for itself."""
    return isinstance(name, str) and (name in VERBS or name in RESERVED)


def reserved_why(name: object) -> str | None:
    """Why no module may take `name`, a name of `RESERVED`, as one clause; None for any
    other name. Every refusal of a reserved name says it after the name:
    `'test' is a name moxi keeps for itself, not a module: its variables would start
    MOXI_TEST_, which moxi-dev's test tiers use`."""
    return _RESERVED_WHY.get(name) if isinstance(name, str) else None


def check_module_name(name: object) -> str:
    """Return `name` when it is spelled as a module name, else raise `ValueError`."""
    if not is_module_name(name):
        raise ValueError(f"{name!r} is not a Moxi module name: "
                         "use lower-case ASCII letters and digits only")
    return name  # type: ignore[return-value]


def is_skill_folder(name: str) -> bool:
    """True when a subfolder named `name` of a module's `SKILLS` folder is a skill: any
    name but one that starts with `.` and `__pycache__`."""
    return not name.startswith(".") and name != "__pycache__"


def is_skill_name(name: object) -> bool:
    """True when `name` is spelled as the name of a skill Moxi ships: `moxi-`, then words
    of lower-case ASCII letters and digits joined by single hyphens, 64 characters at
    most."""
    return (isinstance(name, str) and len(name) <= SKILL_NAME_MAX
            and _SKILL.fullmatch(name) is not None)


def check_skill_name(name: object) -> str:
    """Return `name` when it is spelled as a skill name, else raise `ValueError`."""
    if not is_skill_name(name):
        raise ValueError(f"{name!r} is not a Moxi skill name: a skill Moxi ships is named "
                         f"{SKILL_PREFIX}<name>, lower-case ASCII letters and digits in "
                         f"words joined by single hyphens, {SKILL_NAME_MAX} characters at "
                         "most")
    return name  # type: ignore[return-value]


def distribution(name: str) -> str:
    """The distribution name of module `name`: `moxi-<name>`."""
    return f"{HARNESS}-{check_module_name(name)}"


def import_name(name: str) -> str:
    """The import package of module `name`: `moxi_<name>`."""
    return f"{HARNESS}_{check_module_name(name)}"


def repo(name: str) -> str:
    """The repository that holds module `name`, its code and its issues: the one Moxi
    repository, `CodeManEthan/moxi`, the same for every module."""
    check_module_name(name)
    return f"{OWNER}/{HARNESS}"


def env_prefix(name: str) -> str:
    """The environment prefix of module `name`: `MOXI_<NAME>_`."""
    return f"{HARNESS.upper()}_{check_module_name(name).upper()}_"


__all__ = ["HARNESS", "OWNER", "ORG", "NAMESPACE", "VERBS", "RESERVED", "SKILL_PREFIX",
           "SKILL_NAME_MAX", "is_module_name", "is_reserved", "reserved_why",
           "check_module_name", "is_skill_folder", "is_skill_name", "check_skill_name",
           "distribution", "import_name", "repo", "env_prefix"]
