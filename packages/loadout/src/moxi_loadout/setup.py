"""loadout's `SETUP`, which `moxi install loadout` runs on every install and upgrade.

`setup()` does five things, in order, and places nothing:

1. **The example.** When the loadout folder is missing or empty, it writes the example
   manifest the package ships (every item commented out) as `loadout.toml`, with empty
   `skills/` and `agents/` folders beside it. A folder with anything in it is the user's
   and is left alone.
2. **The opencode row.** It adds an opencode row to the core's harness table when there
   is none, and records it (`moxi_loadout.harness_row`).
3. **Adoption.** It lists every installed module's own skill placements in the manifest
   with the harnesses they were placed for, then hands them to loadout, unseeded
   (`moxi_loadout.adoption.take`), so the yes the user gave at each module's question is
   kept. The next `moxi loadout install` records them in loadout's ledger. A manifest
   that doesn't read, or a write of it that fails, comes before any hand-over, so its
   line says no module skill was taken over, and none was.
4. **The global mode.** Only when the manifest has no `[global] mode`: with someone at
   the keyboard it shows the three modes and writes the answer (default `off`) to the
   manifest; with nobody there it writes nothing, so the mode reads `off`, and prints
   the line that sets one. A manifest that carries a mode, a clone's included, is never
   asked about.
5. **The git hook pieces.** With repo installed and the folder in a git work tree, it
   registers the two pieces there while `reinstall_on_commit` is on, or removes them
   while it is off (`moxi_loadout.repo_link`). A failure is a line, and setup still
   returns 0 for it: the doctor's row names what is missing.

It runs under the install's claim on the core lock and never takes `install.lock`. Every
manifest write holds loadout's manifest lock, so no lock file lands in the folder.

It returns 0, or one line per step that failed (the install then says setup failed);
the steps after a failed one still run when they can.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from moxi_loadout.manifest import MODES

#: The example manifest the package ships, a resource of `moxi_loadout`.
EXAMPLE = "example/loadout.toml"
#: The folders the example's comments name, made empty beside it.
EXAMPLE_FOLDERS = ("skills", "agents")

MODE_QUESTION = """\
loadout: the global instruction file, global.md in your loadout folder, can reach every
session of every harness. Choose how:
  off        loadout touches no global file (the default)
  managed    your own CLAUDE.md and AGENTS.md stay; loadout adds a line or a block that
             brings global.md in, and global remove takes exactly that out
  if-absent  global.md is placed where a harness has no global file; an existing one
             is left alone
Global mode [off]: """
#: What setup says when nobody is at the keyboard to answer.
NO_ANSWER = ("loadout: the global file is off: the manifest sets no [global] mode; "
             "moxi loadout global mode MODE sets one (off, managed or if-absent)")
#: How many answers that aren't a mode are asked again before setup leaves the mode.
TRIES = 3


def setup(*, say: Callable[[str], object] = print,
          ask: Callable[[str], str] | None = None) -> int | str:
    """The announcement's `SETUP`. See the module's docs. `ask` reads one answer (the
    terminal's `input` when None) and is used only with someone at the keyboard."""
    from moxi.config import ConfigError
    from moxi.files import LockTimeout

    from moxi_loadout import adoption, harness_row, ledger, settings, stores
    from moxi_loadout.manifest import ManifestError

    failed: list[str] = []
    try:
        machine = settings.read()
    except ConfigError as exc:
        return f"loadout: its settings don't read: {exc}"
    folder = machine.source
    try:
        if write_example(folder):
            say(f"loadout: wrote an example manifest, every item commented out: "
                f"{stores.manifest_path(folder)}")
    except (OSError, LockTimeout) as exc:
        failed.append(f"loadout: the example manifest wasn't written: {exc}")
    try:
        harness_row.add(say=say)
    except (ConfigError, ledger.LedgerError, LockTimeout) as exc:
        failed.append(f"loadout: the opencode row wasn't added: {exc}")
    manifest_ok = True
    try:
        for t in adoption.take(folder=folder, say=say):
            say(f"loadout: took over {t.module}/{t.placed.name} for {t.placed.harness}")
    except (ManifestError, LockTimeout) as exc:
        manifest_ok = False
        failed.append(f"loadout: no module skill was taken over: {_one_line(exc)}")
    if manifest_ok:
        try:
            ask_mode(folder, say=say, ask=ask)
        except (ManifestError, LockTimeout) as exc:
            failed.append(f"loadout: the global mode wasn't written: {_one_line(exc)}")
    from moxi_loadout import repo_link

    repo_link.sync(folder, machine.reinstall_on_commit, say=lambda line: say(
        f"loadout: {line}"))
    return "\n".join(failed) if failed else 0


def write_example(folder: str | os.PathLike) -> bool:
    """Write the example manifest into `folder` when the folder is missing or empty.
    Returns whether it wrote. Holds the manifest's lock, so it never writes over a
    manifest another writer has just made."""
    import importlib.resources

    from moxi.files import atomic_write, lock

    from moxi_loadout import stores

    folder = Path(folder)
    if not _empty(folder):
        return False
    text = importlib.resources.files("moxi_loadout").joinpath(EXAMPLE).read_text(
        encoding="utf-8")
    with lock(stores.manifest_lock(), timeout_s=10.0):
        if not _empty(folder):
            return False
        os.makedirs(folder, exist_ok=True)
        atomic_write(stores.manifest_path(folder), text, mode=0o644)
        for name in EXAMPLE_FOLDERS:
            os.makedirs(folder / name, exist_ok=True)
    return True


def _empty(folder: Path) -> bool:
    if not os.path.lexists(folder):
        return True
    with os.scandir(folder) as found:
        return next(found, None) is None


def ask_mode(folder, *, say: Callable[[str], object] = print,
             ask: Callable[[str], str] | None = None) -> str | None:
    """The install question for the global mode, asked only when the manifest in
    `folder` has no `[global] mode`. Returns the mode written, or None when nothing was
    written (a mode set already, nobody at the keyboard, or no answer)."""
    from moxi_loadout import manifest, writes

    found = manifest.load(folder)
    if found.mode is not None:
        return None
    if not interactive():
        say(NO_ANSWER)
        return None
    answer = _answer(ask or input, say)
    if answer is None:
        say(NO_ANSWER)
        return None
    writes.set_mode(answer, folder=folder)
    say(f"loadout: global mode {answer}, written to {found.path}; moxi loadout global "
        f"mode MODE changes it")
    if answer != "off":
        if not os.path.exists(found.global_file):
            say(f"loadout: write {found.global_file}, then run moxi loadout install")
        else:
            say("loadout: moxi loadout install applies it")
    return answer


def interactive() -> bool:
    """Whether someone is at the keyboard: stdin is a terminal."""
    stdin = sys.stdin
    return stdin is not None and stdin.isatty()


def _answer(ask: Callable[[str], str], say) -> str | None:
    prompt = MODE_QUESTION
    for _ in range(TRIES):
        try:
            text = ask(prompt).strip().lower()
        except EOFError:
            say("")
            return None
        if not text:
            return MODES[0]
        if text in MODES:
            return text
        prompt = f"{text!r} is not a mode; answer off, managed or if-absent [off]: "
    say(f"loadout: no mode chosen after {TRIES} answers")
    return None


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())


__all__ = ["EXAMPLE", "MODE_QUESTION", "NO_ANSWER", "setup", "write_example",
           "ask_mode", "interactive"]
