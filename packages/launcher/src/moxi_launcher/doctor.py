"""The launcher's own doctor checks, `rows()`, which the announcement's `DOCTOR` names.

The core already checks the launcher's settings and its declared stores. The launcher adds
one row per harness, `catalog.<harness>`: the installed harness's `--help` against the
shipped flag catalog (`moxi_launcher.catalog.diff`).

* `fail`: a flag a catalog row spells is no longer listed, so a launch that sets the row
  would be refused by the harness.
* `warn`: the help lists flags the catalog does not know, or no longer lists one the
  catalog recorded as there; or the help could not be read. Nothing a launch composes
  breaks; the new flags pass on after `--`.
* `off`: the harness is not on PATH.
* `ok`: every flag matches.
"""

from __future__ import annotations

from collections.abc import Iterator

from moxi.doctor import Row

from . import catalog

MODULE = "launcher"
#: How many flags a row's detail names before it says how many more.
SHOWN = 8


def rows(read: catalog.Reader | None = None) -> Iterator[Row]:
    """Every row the launcher adds to `moxi doctor`. `read` stands in for running the
    harness in tests."""
    for harness in catalog.HARNESSES:
        yield catalog_row(catalog.diff(harness, read=read))


def catalog_row(diff: catalog.Diff) -> Row:
    """The doctor row of one harness's `--help` diff."""
    name = f"catalog.{diff.harness}"
    if diff.missing:
        return Row(MODULE, name, "off", f"{diff.harness} is not on PATH")
    errors = [h.error for h in diff.helps if h.error]
    if errors:
        return Row(MODULE, name, "warn", "; ".join(errors))
    removed = [f"{h.command} no longer lists {_flags(h.removed)}"
               for h in diff.helps if h.removed]
    if removed:
        return Row(MODULE, name, "fail", "; ".join(removed) + _version(diff),
                   "update moxi-launcher, or install the harness version its catalog "
                   "was checked against")
    said = []
    for h in diff.helps:
        if h.added:
            said.append(f"{h.command} lists flags the catalog does not know: "
                        f"{_flags(h.added)}")
        if h.gone:
            said.append(f"{h.command} no longer lists {_flags(h.gone)}")
    if said:
        return Row(MODULE, name, "warn", "; ".join(said) + _version(diff),
                   "nothing to do now: an unknown flag passes on after --")
    helps = " and ".join(h.command for h in diff.helps)
    return Row(MODULE, name, "ok", f"the catalog matches {helps}{_version(diff)}")


def _flags(flags) -> str:
    shown = ", ".join(flags[:SHOWN])
    more = len(flags) - SHOWN
    return shown + (f" and {more} more" if more > 0 else "")


def _version(diff: catalog.Diff) -> str:
    version = next((h.version for h in diff.helps if h.version), None)
    return "" if version is None else f" ({diff.harness} {version})"
