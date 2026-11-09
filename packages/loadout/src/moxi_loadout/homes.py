"""Whether a page act would write where the user's terminal does, through ui's check.

A page job runs in the browser shell's environment, not the user's terminal's, and the
variables that decide where loadout reads and writes may differ between the two. The core
keeps a record of the environment the user's last run of `moxi` in a terminal had, and ui
offers the check that compares a page with it (`moxi.modules.load("ui", interface=2)`,
its API's `check`). loadout records nothing itself.

What loadout compares (`places`), each resolved by the resolver the code that reads or
writes it uses, from the environment it is given:

- loadout's data folder, state folder and config file (`moxi.paths.Layout("loadout")`):
  the ledger, the manifest's default folder, the generated files and the backups;
- Moxi's state folder and settings file (`moxi.paths.core()`): the core lock, the
  placer's ledger, the harness table and the opencode row loadout adds to it;
- each harness row's skills and agents folders, as the placer resolves them
  (`moxi.place.folders`);
- Claude Code's, Codex's and OpenCode's homes (`moxi_loadout.harness`), which place the
  harness files loadout writes;
- projects' data, state and config folders when the act reads a project section named by
  key, and repo's when the act registers or removes the git hook pieces with repo;
- the home folder (`moxi.paths.home`).

The program compared is git: the record's `PATH` finding it and the job's not would let
the job place what a terminal's run refuses, since nothing could check what git would
commit. The `claude` and `codex` programs are not compared: a page whose `PATH` finds no
CLI does what a terminal without it does, which writes less.

Where ui is needed:

- A page job calls `check` before anything is written and refuses (exit 3) with its line.
  When ui can't be loaded at interface 2 the job refuses with the reason, and never writes
  unchecked. An error the check raises refuses the job as an error.
- The page server's reads (`status` and the drift block, inside `as_page`) show the line
  as a note, or the reason ui can't be loaded.
- `moxi loadout status` in a terminal shows the line, worded for a terminal, only when ui
  loads at interface 2; without it there is no page to differ from, so no note. An older
  or broken ui reads as absent here.
- `path_fix`, the lasting fix for a program `PATH` doesn't find, names where to set it for
  the browser shell only when ui loads at interface 2.

`MARKER` set to `PAGE_JOB` in a page job's environment, or `as_page` in the page server's
own threads, words the lines for the browser page.

Standard library only at import.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

#: The variable a page job sets so its lines are worded for the browser page.
MARKER = "MOXI_LOADOUT_CALLER"
#: The marker's value in a page job.
PAGE_JOB = "page-job"
#: The ui interface whose API holds the check.
UI_INTERFACE = 2
#: The programs compared: whose absence on the page would let an act do what the
#: terminal's run refuses.
PROGRAMS = {"git": "nothing can check what git would commit"}
#: The modules loadout reads in process, whose folders an act that reads them compares.
READS = ("projects", "repo")
#: Each harness by the name a person knows it by.
NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}

_page = contextvars.ContextVar("moxi_loadout_page", default=False)

Places = Callable[[Mapping[str, str]], dict[str, Path]]


def on_page() -> bool:
    """Whether a line said now is read on the browser page: in a page job, or inside
    `as_page` (the page server's reads)."""
    return os.environ.get(MARKER) == PAGE_JOB or _page.get()


@contextlib.contextmanager
def as_page() -> Iterator[None]:
    """Word the lines said inside for the browser page, in this thread."""
    token = _page.set(True)
    try:
        yield
    finally:
        _page.reset(token)


# -- ui -------------------------------------------------------------------------------------

def ui():
    """ui's API at interface 2, or why there is none: `(api, None)`, or `(None, (reason,
    step))` with the step that fixes it."""
    from moxi import modules

    found = modules.load("ui", interface=UI_INTERFACE)
    if found.ok and found.api is not None:
        return found.api, None
    if found.state == modules.ABSENT:
        return None, ("the browser UI isn't installed", "moxi install ui")
    if found.interfaces and UI_INTERFACE not in found.interfaces:
        return None, (found.reason, "update moxi-ui")
    return None, (found.reason, "moxi doctor says more")


def unloadable(why: tuple[str, str]) -> str:
    """The line a page part refuses with when ui's check can't be loaded (`ui`'s reason
    and step)."""
    reason, step = why
    return ("this page can't tell whether it would write where your terminal does, "
            f"because the browser UI's check can't be loaded ({reason}), so it refuses to "
            f"write; {step}")


# -- the places -----------------------------------------------------------------------------

def places(*, reads: Sequence[str] = ()) -> Places:
    """The places a loadout act compares, as `places(env) -> {label: path}`, each read
    from `env` alone by name. `reads` names the modules of `READS` the act reads in
    process; their data, state and config folders are compared too."""
    from moxi import place
    from moxi.paths import Layout, core, home

    from moxi_loadout import harness

    modules = [m for m in READS if m in reads]

    def resolve(env: Mapping[str, str]) -> dict[str, Path]:
        mine = Layout("loadout")
        found = {"loadout's data folder": mine.data_dir(env),
                 "loadout's state folder": mine.state_dir(env),
                 "loadout's config file": mine.config_file(env),
                 "Moxi's state folder": core().state_dir(env),
                 "Moxi's settings file": core().config_file(env)}
        found.update(place.harness_dirs(env))
        found.update({f"{NAMES[h]}'s home": harness.home(h, env)
                      for h in harness.HARNESSES})
        for m in modules:
            theirs, whose = Layout(m), m + ("'" if m.endswith("s") else "'s")
            found[f"{whose} data folder"] = theirs.data_dir(env)
            found[f"{whose} state folder"] = theirs.state_dir(env)
            found[f"{whose} config folder"] = theirs.config_dir(env)
        found["the home folder"] = home(env)
        return found

    return resolve


def reads(argv: Sequence[str] = ()) -> tuple[str, ...]:
    """The modules of `READS` an act reads in process, as this process finds them:
    projects when it is installed and the manifest, or a `--project` in `argv`, names a
    project by key; repo when it is installed and the loadout folder is in a git work
    tree, where every install registers or removes the git hook pieces with it. When
    loadout's settings or the manifest can't be read, each installed one counts: the
    check then compares more, never less.

    It is read once, from this process's view: when the page's other places agree with
    the terminal's, both resolve the same settings and manifest, and when they don't the
    check refuses on those."""
    from moxi import modules

    from moxi_loadout import manifest, repo_link, settings

    found = []
    try:
        source = settings.read().source
    except Exception:  # noqa: BLE001 - unreadable settings: compare every module installed
        return tuple(m for m in READS if modules.available(m))
    if modules.available("projects") and _names_key(source, argv, manifest):
        found.append("projects")
    if modules.available("repo") and repo_link.in_work_tree(source):
        found.append("repo")
    return tuple(found)


def _names_key(source, argv: Sequence[str], manifest) -> bool:
    args = list(argv)
    given = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--project"]
    given += [a.split("=", 1)[1] for a in args if a.startswith("--project=")]
    if any(not manifest.is_path_name(p) for p in given):
        return True
    try:
        found = manifest.load(source)
    except Exception:  # noqa: BLE001 - a manifest that doesn't read: compare projects too
        return True
    return any(s.key is not None for s in found.projects)


# -- the check ------------------------------------------------------------------------------

def check(argv: Sequence[str] = ()) -> str | None:
    """The one line a page job refuses with before it writes, or None when it would
    write where the user's last run in a terminal did. `argv` is the job's verb and its
    arguments. When ui's check can't be loaded it is the reason, so the job never writes
    unchecked. Raises `ValueError` (a place read outside the core's set) and
    `moxi.config.ConfigError` (Moxi's settings don't read) for the job to fail on."""
    api, why = ui()
    if api is None:
        return unloadable(why)
    return api.check(places(reads=reads(argv)), programs=PROGRAMS, page=True)


def note() -> str | None:
    """The line `status` shows as a note: on the page (`on_page`), the check's line or
    why ui's check can't be loaded; in a terminal, the check's line worded for a terminal
    when ui loads at interface 2, else nothing. An error the check raises is the note."""
    page = on_page()
    api, why = ui()
    if api is None:
        return unloadable(why) if page else None
    try:
        return api.check(places(reads=reads()), programs=PROGRAMS, page=page)
    except Exception as exc:  # noqa: BLE001 - a read says what failed, it never raises
        return f"loadout couldn't compare its folders with your terminal's: {exc}"


def path_fix(program: str) -> str:
    """The lasting fix for a `program` that `PATH` doesn't find. With ui loaded at
    interface 2 it says where to set `PATH` for the browser shell, worded for the page or
    for a terminal; without it, only to put the program's folder on `PATH`."""
    api, _ = ui()
    if api is None:
        return f"put {program}'s folder on PATH"
    where = api.where()
    if on_page():
        return (f"set PATH, with {program}'s folder on it, {where.place}, so the browser "
                f"shell finds {program} at every login, then {where.step}; or run the "
                "command in a terminal")
    return (f"put {program}'s folder on PATH; the browser page does the same until its "
            f"own PATH finds {program}, so to give the browser shell that PATH at every "
            f"login, set it {where.place}, then {where.step}")


__all__ = ["MARKER", "PAGE_JOB", "UI_INTERFACE", "PROGRAMS", "READS", "NAMES", "on_page",
           "as_page", "ui", "unloadable", "places", "reads", "check", "note", "path_fix"]
