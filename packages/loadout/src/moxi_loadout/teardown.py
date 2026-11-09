"""loadout's `TEARDOWN`, which `moxi uninstall loadout` runs before anything is removed.

Rule: uninstall leaves nothing of loadout's in a harness file or a project tree, and
nothing that points into a store `--purge` removes. It refuses before it removes anything,
or it takes everything out. `teardown()`, in order:

1. **Plan, and refuse first.** Every removal is planned and nothing is written yet: each
   harness entry in the ledger (`moxi_loadout.entries` over an empty manifest), the
   managed global part as `global remove` takes it out, loadout's ignore files, its
   placements. When an entry, an ignore file or a generated agent file was changed since
   loadout wrote it, or a harness file that holds loadout's entries doesn't read, or the
   CLI that removes an MCP server is missing while the server is there, it raises
   `moxi.modules.Refused` naming each with its fix: nothing is removed and the uninstall
   exits 3.
2. **The harness entries.** MCP servers through their CLIs first, then each harness file
   once (backup first), the Codex rules file, the managed global part. An entry the user
   had before loadout stays as theirs; one of those that runs from the loadout folder is
   named.
3. **Placements, ignore files, the opencode row.** Each module skill loadout took over
   from the module's own yes is handed back to its module (`moxi.place.adopt`), so the
   yes outlives loadout. One placed because the manifest listed it (seeded) is not: the
   core refuses that hand-back, and the removal that follows takes it, so a choice made
   through the manifest never becomes the module's own. A lost ledger still ends right:
   every placement that belongs to an installed module's package is offered back, and
   the core refuses the seeded ones with a line. Then `moxi.place.remove("loadout")`,
   then loadout's ignore files (after the placements, so a placement is never left
   un-ignored), and last the opencode row in the core's harness table: removed unless
   it was there before loadout, it was edited since, or `skills.harnesses` names
   opencode, each of which leaves it as the user's with a line. When `SETUP` set the
   `harnesses` key and what is left equals what it read before, the key is unset.

It runs under the uninstall's claim on the core lock and never takes `install.lock`. A
failure in steps 2 and 3 (a CLI that fails, a file that can't be written) returns the
lines with the one to run: the package is kept, the ledger says what is left, and a
second run finishes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

OWNER = "loadout"
#: How long teardown waits for the core lock when no uninstall holds it, in seconds.
CORE_WAIT = 60.0
AGAIN = "moxi uninstall loadout"


def teardown(*, say: Callable[[str], object] = print) -> int | str:
    """The announcement's `TEARDOWN`. See the module's docs. Returns 0, or the lines of
    what was left; raises `moxi.modules.Refused` when it would remove nothing."""
    from moxi import files, paths

    try:
        with files.lock(paths.core_lock(), nested=True, timeout_s=CORE_WAIT):
            return _teardown(say)
    except files.LockTimeout as exc:
        return f"loadout: {exc}; run {AGAIN} again when the other install is done"


def _teardown(say) -> int | str:
    from moxi import modules, place

    from moxi_loadout import entries, ignores, ledger
    from moxi_loadout.planner import Scope

    try:
        data = ledger.read()
    except ledger.LedgerError as exc:
        raise modules.Refused(f"uninstall refused; nothing was removed: {exc}") from None
    machine = _machine()
    folders = _folders(data, machine)
    run = _plan(data, machine)

    problems = _changed(run) + _unreadable(run) + _ignore_files(data) + _generated(data)
    if problems:
        raise modules.Refused(
            "uninstall refused; nothing was removed. Changed since loadout wrote it, or "
            "can't be read:\n" + "\n".join(f"  {line}" for line in problems))

    failed: list[str] = []
    entries.apply(run, Scope(), data, keep=_keep(machine), say=_prefix(say))
    failed += [f"loadout: {line}" for line in run.lines]
    for a in run.actions:
        if a.op in ("add", "update", "remove") and not a.done:
            failed.append(f"loadout: {_label(a.record)} was not removed from "
                          f"{a.record.get('file')}")
        elif a.op == "end" and a.record.get("prior") and _runs_from(a.record, folders):
            how = "reads" if a.record.get("kind") in _GLOBAL_KINDS else "runs from"
            say(f"loadout: {_label(a.record)} stays in {a.record.get('file')}, as it was "
                f"there before loadout; it {how} {_runs_from(a.record, folders)}")

    try:
        _hand_back(data, say)
        gone = place.remove(OWNER, say=_prefix(say))
    except Exception as exc:  # noqa: BLE001 - the ledger is still written, then a rerun
        failed.append(f"loadout: placements not removed: {exc}")
        gone = []
    for p in gone:
        say(f"loadout: removed {p.kind} {p.name} for {p.harness} ({p.path})")
    left = place.status(OWNER)
    for p in left:
        failed.append(f"loadout: {p.kind} {p.name} for {p.harness} was not removed "
                      f"({p.path})")
    data["placed"] = {k: v for k, v in data["placed"].items()
                      if k in {str(p.path) for p in left}}
    failed += [f"loadout: {line}" for line in
               ignores.settle(data, left, None, say=_prefix(say))]
    failed += _opencode_row(data, say)
    ledger.write(data)
    if failed:
        return "\n".join(failed + [f"loadout: run {AGAIN} again to finish"])
    return 0


# -- step 1: the plan, and what refuses it -------------------------------------------------

def _machine():
    from moxi.config import ConfigError

    from moxi_loadout import settings

    try:
        return settings.read()
    except ConfigError:
        return None


def _keep(machine) -> int:
    return 10 if machine is None else machine.backups_keep


def _folders(data: dict, machine) -> list[str]:
    """The loadout folders a harness entry may run from: the one the last install read,
    and the configured one."""
    out = []
    for f in (data.get("source"), None if machine is None else machine.source):
        if f and os.path.abspath(f) not in out:
            out.append(os.path.abspath(f))
    return out


def _plan(data: dict, machine):
    """Every recorded entry planned against an empty manifest: each is removed, ended
    (the user's first, or gone already), or left as changed or unreadable."""
    from moxi_loadout import entries, manifest, stores

    folder = stores.default_source() if machine is None else machine.source
    run = entries.plan(manifest.empty(stores.manifest_path(folder)), folder, data)
    for a in run.actions:
        if a.op == "stand" and a.record is not None and a.record.get("kind") == "mcp" \
                and a.reason.startswith("no ") and entries.mcp_there(a.record) is False:
            a.op, a.reason = "end", ""
    return run


def _changed(run) -> list[str]:
    out = []
    for a in run.actions:
        if a.op != "modified":
            continue
        r = a.record
        fix = "restore it or delete it"
        ident = _ident(r)
        if ident is not None:
            fix += f", or run moxi loadout install --replace {ident}"
        out.append(f"{_label(r)} in {r.get('file')} was edited since loadout wrote it: "
                   f"{fix}")
    return out


def _unreadable(run) -> list[str]:
    out = []
    for a in run.actions:
        if a.op != "stand" or not a.reason or a.record is None:
            continue
        r = a.record
        if r.get("kind") == "mcp" and a.reason.startswith("no "):
            out.append(f"{_label(r)}: {a.reason}; put {r.get('harness')} on PATH, or "
                       "remove the server by hand")
        elif "is not plain JSON" in a.reason:
            out.append(f"{_label(r)}: {a.reason}; make the file plain JSON again, or "
                       "moxi uninstall loadout --skip-teardown leaves loadout's entries "
                       "in it")
        else:
            out.append(f"{_label(r)}: {a.reason}; the file's shape is not what loadout "
                       "expects, so give it back the shape loadout wrote, or "
                       "moxi uninstall loadout --skip-teardown leaves loadout's entries "
                       "in it")
    return list(dict.fromkeys(out))


def _ignore_files(data: dict) -> list[str]:
    from moxi_loadout import ignores

    return [f"{path} was edited since loadout wrote it: restore it or delete it"
            for path, _, _ in ignores.problems(data)]


def _generated(data: dict) -> list[str]:
    """Each generated agent file edited by hand, which a purge would lose. One no
    placement uses any more has no `--replace` to name: deleting it is the fix."""
    from moxi_loadout import convert, install, stores

    root = stores.generated_dir()
    used = install.generated_in_use(data)
    out = []
    for top, _, names in os.walk(root):
        for name in sorted(names):
            path = Path(top) / name
            if convert.state(path) != "modified":
                continue
            if os.path.realpath(path) not in used:
                out.append(f"{path} was edited by hand, and no agent uses it any more: "
                           "copy the edit into its source if you want it, then delete it")
                continue
            harness = path.relative_to(root).parts[0]
            stem = name.rsplit(".", 1)[0]
            out.append(f"{path} was edited by hand: copy the edit into its source and "
                       f"delete it, or run moxi loadout install --replace "
                       f"agent:{stem}@{harness}")
    return out


def _label(record: dict) -> str:
    kind, _, name = str(record.get("item")).partition(":")
    return f"{kind} {name} for {record.get('harness')}"


def _ident(record: dict) -> str | None:
    from moxi_loadout.manifest import parse_id

    kind, _, name = str(record.get("item")).partition(":")
    text = f"{kind}:{name}@{record.get('harness')}"
    if kind == "global":
        return text
    try:
        parse_id(text)
    except ValueError:
        return None
    return text


#: The kinds of entry that bring the global file into a harness: Claude's `@` line,
#: OpenCode's `instructions` path, the Codex block.
_GLOBAL_KINDS = ("global-line", "instructions", "global-block")


def _runs_from(record: dict, folders: list[str]) -> str | None:
    """The loadout folder a kept entry names (a hook's script, an MCP server's, the
    global file a global entry brings in), or None."""
    if record.get("kind") not in ("hook", "mcp", "opencode-mcp") + _GLOBAL_KINDS:
        return None
    texts = list(_strings(record.get("written")))
    return next((f for f in folders if any(f in t for t in texts)), None)


def _strings(value):
    """Every string inside a recorded value, as written (not JSON-escaped, so a Windows
    path's backslashes match)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _prefix(say):
    return lambda line: say(f"loadout: {line}")


# -- step 3: the hand-back and the opencode row --------------------------------------------

def _hand_back(data: dict, say) -> None:
    """Hand each placement loadout took over from a module's own yes back to it. A
    placement the ledger records as seeded is left for the removal; with no record, the
    core refuses a seeded one with a line, and the removal takes it too."""
    from moxi import modules, place

    from moxi_loadout import listener

    records = data["placed"]
    mine = {str(p.path): p for p in place.status(OWNER)}
    for entry in modules.installed():
        module = entry.name
        if module == OWNER or not modules.load(module).ok:
            continue
        back = []
        for step in listener.held(module):
            target = str(step.target)
            if records.get(target, {}).get("seeded") or target not in mine:
                continue
            back.append(mine[target])
        if not back:
            continue
        for p in place.adopt(OWNER, module, back, say=_prefix(say)):
            say(f"loadout: handed {p.kind} {p.name} for {p.harness} back to {module}")


def _opencode_row(data: dict, say) -> list[str]:
    """Take the opencode row out of the core's harness table when loadout added it and it
    is as loadout wrote it, and drop its record. Returns the lines of a failure."""
    from moxi import config, place

    from moxi_loadout import harness_row

    record = harness_row.record_of(data)
    if record is None:
        return []
    keep = [r for r in data["entries"] if r is not record]
    if record.get("prior"):
        data["entries"] = keep
        return []
    try:
        row = _row_now()
    except ValueError as exc:
        return [f"loadout: the opencode row wasn't removed: {exc}"]
    written = record.get("written")
    if row is None:
        pass
    elif row != written:
        say("loadout: the opencode row in the core's harness table was edited since "
            "loadout added it, so it stays, as yours")
    elif harness_row.NAME in place.chosen()[0]:
        say("loadout: the opencode row stays in the core's harness table: "
            "skills.harnesses names opencode, so moxi install places skills there")
    else:
        try:
            config.remove_item("moxi", "harnesses", written, by=harness_row.BY)
        except config.ConfigError as exc:
            return [f"loadout: the opencode row wasn't removed: {exc}"]
        say("loadout: removed the opencode row from the core's harness table")
        if "made_key" in record:
            try:
                if harness_row.file_rows() == record["made_key"]:
                    # loadout set the key; what is left is what it read before, so the
                    # key goes back to its default (a later core's rows show again).
                    config.unset("moxi", "harnesses", by=harness_row.BY)
            except (ValueError, config.ConfigError) as exc:
                return [f"loadout: the harnesses key loadout set wasn't unset: {exc}"]
    data["entries"] = keep
    return []


def _row_now() -> dict | None:
    """The opencode row as the core's config file holds it, or None. Raises `ValueError`
    when the file is there and doesn't read."""
    import tomllib

    from moxi import paths

    path = Path(paths.core().config_file())
    try:
        rows = tomllib.loads(path.read_text(encoding="utf-8")).get("harnesses", [])
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{path} can't be read ({exc})") from None
    if not isinstance(rows, list):
        return None
    return next((r for r in rows if isinstance(r, dict)
                 and r.get("name") == "opencode"), None)


__all__ = ["OWNER", "CORE_WAIT", "teardown"]
