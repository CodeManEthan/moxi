"""What one install run keeps under the core's `displaced/` folder, and how to put it back.

**No loadout write destroys bytes loadout cannot show it wrote.** Before a write of
loadout's goes over a file, or an entry of a file, that isn't what loadout last wrote
there, the run keeps a copy of those bytes here first; a copy that can't be made stops
that write. The core placer keeps what isn't its own the same way, by moving it here.

The folder is the core's, `DATA/moxi/displaced/`, laid out as the placer lays it:
`<UTC stamp>/<harness>/<file name>`, the stamp a UTC time like 20261116T093000Z taken once per run, a
taken name followed by `-2`, `-3`. A copy is written 0600 in private folders, since a
harness file can hold a server's secrets. Nothing in loadout deletes under this folder: a
copy stays until the user deletes it.

`Keeper.kept` names each copy for the run's outcome, `{target, copy, how, id, back}`:

- `how`: `copied` (loadout wrote a copy, then wrote the target), `replaced` (the placer
  moved it aside and placed loadout's there) or `moved` (the placer moved it aside on a
  removal, and the target is now empty);
- `id`: the row's `<kind>:<name>@<harness>`, None for a harness file's copy, which can
  hold several entries;
- `back`: the command that puts it back, for the platform the run is on, its paths
  quoted: `cp` (`copy`) for `copied`, `mv` (`move`) for `moved`, and for `replaced`
  `moxi loadout disable <id>` with the row's section first (`moxi loadout global remove`
  for the global file), since loadout's placement stands at the target until the item is
  off there.
"""

from __future__ import annotations

import os
import shlex
import time
from pathlib import Path

#: How a run's stamp folder is named (UTC), as the placer names its own.
STAMP = "%Y%m%dT%H%M%SZ"
#: The values of `how`.
HOWS = ("copied", "replaced", "moved")


def root() -> Path:
    """The core's `displaced/` folder."""
    from moxi import paths

    return paths.core().data_dir() / "displaced"


def _windows() -> bool:
    return os.name == "nt"


class Keeper:
    """One run's copies and the placer's moves it matched, in `kept`."""

    def __init__(self) -> None:
        self.kept: list[dict] = []
        self._stamp: str | None = None

    def copy(self, target: str | os.PathLike, harness: str, data: bytes, *,
             ident: str | None = None, say=None) -> Path:
        """Keep `data`, the bytes at `target` just before a write goes over them, under
        `<stamp>/<harness>/`; say so through `say`. Raises `OSError` when the copy can't
        be made, and then the write must not be made."""
        from moxi.files import atomic_write, private_dir

        stamp = self._stamp or time.strftime(STAMP, time.gmtime())
        harness_dir = private_dir(root() / stamp / (harness or "dest"))
        name = Path(target).name
        dest, n = harness_dir / name, 1
        while os.path.lexists(dest):
            n += 1
            dest = harness_dir / f"{name}-{n}"
        atomic_write(dest, data, mode=0o600)
        self._stamp = stamp
        self._add(target, dest, "copied", ident, None)
        if say is not None:
            say(f"kept a copy of {target} at {dest}")
        return dest

    def moved(self, target: str | os.PathLike, copy: str | os.PathLike, *, how: str,
              ident: str | None, section: str | None = None) -> None:
        """Name a move the placer made: `how` `replaced` or `moved`; `section` the row's
        section (`user` or a project section), for the way back of a `replaced` one."""
        self._add(target, copy, how, ident, section)

    def _add(self, target, copy, how: str, ident, section) -> None:
        self.kept.append({"target": os.fspath(target), "copy": os.fspath(copy),
                          "how": how, "id": ident,
                          "back": back(how, copy, target, ident=ident, section=section)})


def back(how: str, copy, target, *, ident: str | None = None,
         section: str | None = None) -> str:
    """The command that puts the copy at `copy` back at `target`. See the module's docs."""
    from moxi_loadout.manifest import USER

    windows = _windows()
    q = _quote_windows if windows else shlex.quote
    paths = f"{q(os.fspath(copy))} {q(os.fspath(target))}"
    if how == "copied":
        return f"copy /y {paths}" if windows else f"cp {paths}"
    mv = f"move /y {paths}" if windows else f"mv {paths}"
    if how == "moved" or ident is None:
        return mv
    if ident.startswith("global:"):
        return f"moxi loadout global remove && {mv}"
    flag = "" if section is None else (" --user" if section == USER
                                       else f" --project={q(section)}")
    return f"moxi loadout disable {q(ident)}{flag} && {mv}"


def _quote_windows(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


__all__ = ["STAMP", "HOWS", "Keeper", "root", "back"]
