"""loadout's doctor checks: `rows()`, which the announcement's `DOCTOR` names.

The core's own rows already show every loadout placement that isn't ok. These add:

- config, manifest: loadout's settings and its manifest read, every unknown key named
  (`fail`); a folder with no manifest yet is `ok`.
- harness <name>: each harness's items and entries not ok, `modified`, `blocked`,
  `would-commit` and the rest, a harness file loadout can't read among them (`warn`).
- sections: project sections refused this run, and loadout's ignore files edited since
  it wrote them (`warn`).
- generated: generated agent files no agent uses any more (`warn`): an install deletes
  one as it was generated, and leaves one edited by hand for the user to delete.
- global: the global mode, and its state per harness.
- hooks: each hook's program resolves (`warn` when one doesn't).
- projects: a section named by key while projects is absent (`warn`); else the link,
  `off` without projects.
- git hook: the link to repo. `off` when the loadout folder is in no git work tree,
  when the folder is in git and repo is absent ("with repo, a commit reinstalls") or when
  `reinstall_on_commit` is off; `warn` when a piece is unregistered or disabled while the
  setting is on; else `ok`. A failed reinstall is a record of its own, which the core's
  rows show.
- codex: Codex's agents switch, its desktop import, and hooks waiting for trust (`ok`
  with the detail).
- module <name>: an adopted module skill whose module is gone (`off`).

The doctor gives the whole check ten seconds, so the would-commit check's git calls get
two seconds each and four in all here, not install's thirty; a target the check had no
time for is named in a `warn` row. The core placer's tracked check gets two seconds of git
in all; an occupant it had no time for reads `blocked`, its reason saying git did not
answer.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from collections.abc import Iterator

from moxi.doctor import Row

MODULE = "loadout"
#: Seconds one git call of the would-commit check may take on the doctor's path.
GIT_SECONDS = 2.0
#: Seconds all of them together may take there.
GIT_BUDGET = 4.0
#: Seconds the core placer's tracked check may give git in all there.
PLACE_GIT_SECONDS = 2.0
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
#: What makes a hook's command more than one program with its arguments: a check of its
#: first word could then be wrong, so the doctor leaves such a command unchecked.
_SHELL_OPERATORS = re.compile(r"&&|\|\||[;|&<>`\n]|\$\(")
#: Shell builtins a command may start with, which no PATH lookup finds.
_BUILTINS = frozenset({":", ".", "alias", "break", "builtin", "cd", "command", "continue",
                       "eval", "exec", "exit", "export", "readonly", "return", "set",
                       "shift", "source", "trap", "type", "ulimit", "umask", "unset",
                       "wait", "true", "false", "echo", "printf", "test", "["})
_NOT_OK = ("missing", "stale", "modified", "blocked", "would-commit", "bad-source")


def rows() -> Iterator[Row]:
    """Every check's row."""
    from moxi.config import ConfigError

    from moxi_loadout import install, ledger, settings
    from moxi_loadout import manifest as manifest_mod

    try:
        machine = settings.read()
    except ConfigError as exc:
        yield Row(MODULE, "config", "fail", f"loadout's config can't be read: {exc}",
                  "moxi config loadout")
        return
    try:
        found = manifest_mod.load(machine.source)
    except manifest_mod.ManifestError as exc:
        yield Row(MODULE, "manifest", "fail", "; ".join(exc.lines),
                  f"edit {_manifest_file(machine.source)}")
        return
    yield _manifest(found, machine.source)
    try:
        ledger.read()
    except ledger.LedgerError as exc:
        yield Row(MODULE, "ledger", "fail", str(exc))
        return
    from moxi_loadout import ignores

    try:
        outcome = install.status(git=ignores.Git(timeout=GIT_SECONDS, budget=GIT_BUDGET),
                                 git_seconds=PLACE_GIT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - the other rows still say what they can
        yield Row(MODULE, "status", "fail", f"status failed: {exc}", "moxi loadout status")
        outcome = None
    if outcome is not None:
        yield from _harnesses(outcome)
        yield from _sections(outcome)
        yield _global(found, outcome)
        yield from _skipped(outcome)
        yield from _orphans(outcome)
    yield _hooks(found, machine.source)
    yield _projects(found, outcome)
    yield _git_hook(machine)
    yield from _codex(found)
    yield from _modules(found)


def _manifest_file(source) -> str:
    from moxi_loadout import stores

    return str(stores.manifest_path(source))


def _manifest(found, source) -> Row:
    if not found.exists:
        return Row(MODULE, "manifest", "ok", f"no loadout.toml in {source} yet; moxi "
                   "loadout add starts one")
    count = sum(len(s.names(kind)) for s in found.sections()
                for kind in ("skill", "agent", "hook", "permissions", "mcp"))
    return Row(MODULE, "manifest", "ok", f"{found.path} reads: {count} item"
               f"{'s' * (count != 1)} listed")


def _harnesses(outcome) -> Iterator[Row]:
    by: dict[str, list] = {}
    for r in outcome.rows:
        if r.harness != "*" and r.kind != "global":
            by.setdefault(r.harness, [])
            if r.state in _NOT_OK:
                by[r.harness].append(r)
    for harness in sorted(by):
        bad = by[harness]
        if not bad:
            yield Row(MODULE, f"harness {harness}", "ok", "every item is ok or off")
            continue
        yield Row(MODULE, f"harness {harness}", "warn", _listing(bad),
                  _fix(bad))


def _sections(outcome) -> Iterator[Row]:
    bad = [r for r in outcome.rows if r.harness == "*" and r.kind != "global"
           and r.state in _NOT_OK]
    if bad:
        yield Row(MODULE, "sections", "warn", _listing(bad), "moxi loadout status")


def _listing(bad) -> str:
    counts: dict[str, int] = {}
    for r in bad:
        counts[r.state] = counts.get(r.state, 0) + 1
    head = ", ".join(f"{n} {s}" for s, n in counts.items())
    first = bad[0]
    more = f" ({len(bad) - 1} more: moxi loadout status)" if len(bad) > 1 else ""
    return f"{head}: {first.kind} {first.name}" + (f", {first.reason}" if first.reason
                                                    else "") + more


def _fix(bad) -> str:
    if all(r.state in ("missing", "stale") for r in bad):
        return "moxi loadout install"
    return "moxi loadout status"


def _global(found, outcome) -> Row:
    mode = found.global_mode
    if found.mode is None:
        return Row(MODULE, "global", "ok", "off: the manifest sets no [global] mode",
                   "moxi loadout global mode MODE")
    states: dict[str, str] = {}
    for r in outcome.rows:
        if r.kind == "global":
            if r.state != "ok" or r.harness not in states:
                states[r.harness] = r.state
    detail = f"mode {mode}" + (": " + ", ".join(f"{h} {s}" for h, s in sorted(
        states.items())) if states else "")
    if any(s not in ("ok", "off") for s in states.values()):
        return Row(MODULE, "global", "warn", detail, "moxi loadout global show --diff")
    return Row(MODULE, "global", "ok", detail)


def _skipped(outcome) -> Iterator[Row]:
    from moxi_loadout.install import SKIPPED

    for note in outcome.notes:
        if note.startswith(SKIPPED):
            yield Row(MODULE, "would-commit", "warn", note + "; the doctor gives git "
                      f"{GIT_BUDGET:.0f} s", "moxi loadout status")


def _orphans(outcome) -> Iterator[Row]:
    if not outcome.orphans:
        return
    edited = [p for p, state in outcome.orphans if state == "modified"]
    n = len(outcome.orphans)
    detail = (f"{n} generated agent file{'s' * (n != 1)} no agent uses any more: "
              + ", ".join(p + (" (edited by hand)" if p in edited else "")
                          for p, _ in outcome.orphans))
    fix = ("copy each edit into its source if you want it, then delete it" if edited
           else "moxi loadout install")
    yield Row(MODULE, "generated", "warn", detail, fix)


def _hooks(found, source) -> Row:
    from moxi_loadout.manifest import expand_command

    names = found.user.names("hook") if found.exists else []
    if not names:
        return Row(MODULE, "hooks", "off", "no hooks listed")
    bad = []
    for name in names:
        command = found.item("hook", name).get("command")
        if not isinstance(command, str):
            continue
        why = _unresolved(expand_command(command, source))
        if why:
            bad.append(f"hook {name}: {why}")
    if bad:
        return Row(MODULE, "hooks", "warn", "; ".join(bad),
                   "install the program, or fix the hook's command in loadout.toml")
    return Row(MODULE, "hooks", "ok", f"{len(names)} hook program"
               f"{'s' * (len(names) != 1)} found")


def _unresolved(command: str) -> str | None:
    """Why `command`'s program, or a script it names by path, can't be found, or None.
    Only what can be told for certain warns: a command with shell operators, a builtin,
    and a word holding a variable are left unchecked, since the shell resolves them."""
    if _SHELL_OPERATORS.search(command):
        return None
    try:
        words = shlex.split(command, posix=sys.platform != "win32")
    except ValueError as exc:
        return f"its command can't be read ({exc})"
    while words and _ASSIGNMENT.match(words[0]):
        words = words[1:]
    if not words:
        return "its command is empty"
    program = words[0]
    if "$" in program or program in _BUILTINS:
        return None
    if os.sep in program or "/" in program:
        if not os.path.exists(os.path.expanduser(program)):
            return f"{program} is not there"
    elif shutil.which(program) is None:
        return f"{program} is not on PATH"
    for word in words[1:]:
        if "$" in word:
            continue
        if (os.path.isabs(word) or word.startswith("~")) and not os.path.exists(
                os.path.expanduser(word)):
            return f"{word} is not there"
    return None


def _projects(found, outcome) -> Row:
    from moxi_loadout import projects_link

    keys = [s.name for s in found.sections() if s.where == "key"] if found.exists else []
    link, why = projects_link.api()
    if link is None and why == projects_link.ABSENT:
        if keys:
            return Row(MODULE, "projects", "warn", "projects is not installed, so the "
                       f"sections named by key are refused: {', '.join(keys)}",
                       "moxi install projects, or name each section by path")
        return Row(MODULE, "projects", "off", "projects is not installed: project "
                   "sections are named by path", "moxi install projects")
    if link is None:
        return Row(MODULE, "projects", "warn", why, "moxi doctor")
    refused = [r for r in (outcome.rows if outcome is not None else [])
               if r.kind == "section"]
    if refused:
        return Row(MODULE, "projects", "warn", "; ".join(r.reason for r in refused),
                   "moxi projects list")
    return Row(MODULE, "projects", "ok", f"{len(keys)} section{'s' * (len(keys) != 1)} "
               "named by key resolved" if keys else "no section is named by key")


def _git_hook(machine) -> Row:
    from moxi_loadout import repo_link

    try:
        return repo_link.state(machine.source, machine.reinstall_on_commit)
    except Exception as exc:  # noqa: BLE001 - one row's failure is that row's
        return Row(MODULE, "git hook", "warn", f"the link to repo can't be read: {exc}",
                   "moxi doctor")


def _codex(found) -> Iterator[Row]:
    import tomllib

    from moxi import place

    from moxi_loadout import harness as hz

    table, _ = place.harnesses()
    if "codex" not in table:
        return
    path = hz.codex_home() / "config.toml"
    said = []
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {}
    except (OSError, ValueError) as exc:
        yield Row(MODULE, "codex", "warn", f"{path} can't be read ({exc})")
        return
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    if agents.get("enabled") is False:
        said.append(f"codex agents are off in {path} (agents.enabled = false), so codex "
                    "doesn't read the agent files loadout places")
    key = "external-agent-import-sync-enabled"
    desktop = config.get("desktop") if isinstance(config.get("desktop"), dict) else {}
    if True in (config.get(key), desktop.get(key)):
        said.append("codex's desktop import is on, so codex may also import agents from "
                    "elsewhere")
    hooks = [n for n in (found.user.names("hook") if found.exists else [])
             if "codex" in (found.harnesses("hook", n) or table)
             and not found.user.is_disabled("hook", n, "codex")]
    if hooks:
        said.append(f"codex runs a new hook only once you trust it in codex, and "
                    f"loadout can't tell which you have trusted: {', '.join(hooks)}")
    yield Row(MODULE, "codex", "ok", "; ".join(said) if said else "nothing to note")


def _modules(found) -> Iterator[Row]:
    from moxi import modules

    from moxi_loadout import ledger
    from moxi_loadout.manifest import is_module_skill

    skills: dict[str, set[str]] = {}
    names = found.user.names("skill") if found.exists else []
    for name in names:
        if is_module_skill(name):
            skills.setdefault(name.partition("/")[0], set()).add(name)
    try:
        records = ledger.read()["placed"].values()
    except ledger.LedgerError:
        records = []
    for r in records:
        if r.get("module"):
            skills.setdefault(r["module"], set()).add(str(r.get("item")))
    for module in sorted(skills):
        if modules.load(module).ok:
            continue
        yield Row(MODULE, f"module {module}", "off", f"{module} is not installed, so its "
                  f"skill{'s' * (len(skills[module]) != 1)} "
                  f"{', '.join(sorted(skills[module]))} read off",
                  f"moxi install {module}")


__all__ = ["MODULE", "GIT_SECONDS", "GIT_BUDGET", "rows"]
