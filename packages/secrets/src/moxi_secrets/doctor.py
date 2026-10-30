"""The secrets module's doctor rows: `rows()`, which the announcement's DOCTOR names.

* `rules`: sets.json loads and its pin matches the vendored upstream file; `warn` when a
  rule is dropped, `fail` when the file will not load or the pin differs.
* `engine`: RE2, or the fallback. On the fallback the row starts a worker and scans a
  short text, and fails when the worker does not answer.
* `private-names`: the counts of the list; `off` when it is empty, `warn` for a short or
  empty entry, `fail` when the list will not load.
* `private-names-location`: the config directory, which holds the list and may hold a
  private allowlist, is outside every git work tree.
* `personal-data`: python-stdnum imports.

No row carries an entry of the private names list, and no row prints a path, since a
config path can hold a login that is itself on the list. An entry is named by its key
and 1-based position. Nothing here writes a file.
"""

from __future__ import annotations

import os

from moxi.doctor import Row

MODULE = "secrets"
#: Entries shorter than this, once normalised, get a warning.
SHORT = 3

_REINSTALL = "reinstall moxi-secrets"
_ADD_NAME = "moxi config set secrets private_names.words --add NAME"


# -- rules ----------------------------------------------------------------------------------

def _scan():
    """The scan module. The package's own `scan` attribute is the function."""
    import importlib

    return importlib.import_module("moxi_secrets.scan")


def _upstream_path() -> str:
    scan = _scan()
    return os.path.join(scan._RULES_DIR, "upstream", "gitleaks.toml")


def rules_row() -> Row:
    """The rule sets: counts, the pin, dropped rules."""
    import hashlib

    scan = _scan()
    try:
        sets = scan.load_sets()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return Row(MODULE, "rules", "fail",
                   f"sets.json will not load ({type(exc).__name__})", _REINSTALL)
    try:
        with open(_upstream_path(), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
    except OSError as exc:
        return Row(MODULE, "rules", "fail",
                   f"the pinned gitleaks rules cannot be read ({type(exc).__name__})",
                   _REINSTALL)
    pin = sets.upstream
    if digest != pin.get("sha256"):
        return Row(MODULE, "rules", "fail",
                   f"sets.json's pin (gitleaks {pin.get('tag')}) does not match the bytes "
                   "of the vendored rules/upstream/gitleaks.toml", _REINSTALL)
    raw = sets.data["sets"]
    credentials = raw["credentials"]["rules"]
    upstream = sum(1 for r in credentials if r["source"] == "gitleaks")
    dropped = [f"{name}/{rule_id}" for name in scan.GITLEAKS_SETS
               for rule_id in sets.dropped[name]]
    detail = (f"credentials {len(credentials)} rules ({upstream} from gitleaks "
              f"{pin['tag']}, {len(credentials) - upstream} local), "
              f"broad {len(raw['broad']['rules'])}, {len(dropped)} dropped")
    if dropped:
        return Row(MODULE, "rules", "warn",
                   f"{detail}: {', '.join(dropped)} left out of this release's sets, so "
                   "neither engine runs them")
    return Row(MODULE, "rules", "ok", detail)


# -- engine ---------------------------------------------------------------------------------

#: What the engine row's worker scans: no rule hits it.
PROBE_TEXT = "moxi doctor: a short text for the fallback worker"


def _worker_failure() -> str | None:
    """Start the fallback worker if none runs, scan a short text; how it failed, or None."""
    from . import engine, fallback

    sets = _scan().GITLEAKS_SETS
    found = fallback.scan(engine.Text(PROBE_TEXT), sets, None)
    if all(found.get(name) is not None for name in sets):
        return None
    return fallback.last_failure or "the worker did not scan the text"


def engine_row() -> Row:
    """RE2 where google-re2 imports; else the fallback, with its worker checked."""
    from . import engine

    if engine.re2_module() is not None:
        return Row(MODULE, "engine", "ok", f"RE2 (google-re2 {engine._re2_version()})")
    fits = engine.re2_wheel_fits()
    install = "moxi install secrets --with re2" if fits else None
    failure = _worker_failure()
    if failure is not None:
        return Row(MODULE, "engine", "fail",
                   f"fallback: its worker failed a short scan: {failure}", install)
    if fits:
        return Row(MODULE, "engine", "warn",
                   "fallback: large or unusual texts may come back unscanned", install)
    return Row(MODULE, "engine", "ok",
               "fallback: Python re with a deadline (no google-re2 wheel for this platform)")


# -- private names --------------------------------------------------------------------------

def private_names_row() -> Row:
    """The list's counts; off when empty; warnings by key and position."""
    from . import names
    from .scan import SetUnavailable

    try:
        listed = names.private_names()
    except SetUnavailable as exc:
        # The message names the key and the item's position, never a value.
        return Row(MODULE, "private-names", "fail", str(exc), "moxi config secrets check")
    counts = (f"words {len(listed.words)}, anywhere {len(listed.anywhere)}, "
              f"emails {len(listed.emails)}, allowed {len(listed.allowed)}")
    problems = []
    for key in names.KEYS:
        for i, entry in enumerate(getattr(listed, key.rpartition(".")[2]), 1):
            norm = names.normalise(entry)
            if not norm:
                problems.append(f"{key} entry {i} is empty once separators are trimmed, "
                                "so it is left out")
            elif len(norm) < SHORT:
                problems.append(f"{key} entry {i} is shorter than {SHORT} characters")
    if problems:
        return Row(MODULE, "private-names", "warn", f"{counts}; {'; '.join(problems)}",
                   "moxi config secrets")
    if not (listed.words or listed.anywhere or listed.emails):
        return Row(MODULE, "private-names", "off",
                   "the list is empty, so the private-names set finds nothing", _ADD_NAME)
    return Row(MODULE, "private-names", "ok", counts)


def _work_tree_above(directory) -> bool:
    """True when `directory` or a parent of it holds `.git`, and it is not itself inside
    a `.git` directory (git tracks nothing there)."""
    from pathlib import Path

    directory = Path(directory)
    if ".git" in directory.parts:
        return False
    return any(os.path.lexists(p / ".git") for p in (directory, *directory.parents))


def private_names_location_row() -> Row:
    """The config directory, as named and with links resolved, outside every work tree."""
    from moxi import paths

    layout = paths.Layout(MODULE)
    directory = layout.config_file().parent
    try:
        resolved = directory.resolve()
    except OSError:
        resolved = directory
    if _work_tree_above(directory.absolute()) or _work_tree_above(resolved):
        return Row(MODULE, "private-names-location", "warn",
                   "the secrets config directory lies inside a git work tree (it or a "
                   "parent directory holds .git, directly or through a link): a repo there, "
                   "such as a dotfiles repo, could publish the private names list in the "
                   "config file and a private allowlist kept beside it",
                   f"move the config root outside every work tree with "
                   f"{layout.prefix}CONFIG_HOME or {paths.HOME}, and move the file "
                   "`moxi config secrets path` names there")
    return Row(MODULE, "private-names-location", "ok",
               "the config file, and a private allowlist kept beside it, are outside every "
               "git work tree")


# -- personal data --------------------------------------------------------------------------

_INSTALL_PERSONAL = "moxi install secrets --with personal-data"


def _stdnum_version() -> str | None:
    """python-stdnum's installed version, or None when it is not installed."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("python-stdnum")
    except PackageNotFoundError:
        return None


def personal_data_row() -> Row:
    """python-stdnum imports; off without the extra."""
    try:
        from stdnum import iban, luhn  # noqa: F401
        from stdnum.us import ssn  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - a broken install is this row's fail
        installed = _stdnum_version()
        if installed is None:
            return Row(MODULE, "personal-data", "off",
                       "python-stdnum is not installed, so the personal-data set is off",
                       _INSTALL_PERSONAL)
        return Row(MODULE, "personal-data", "fail",
                   f"python-stdnum {installed} is installed but will not import "
                   f"({type(exc).__name__})", _INSTALL_PERSONAL)
    installed = _stdnum_version()
    return Row(MODULE, "personal-data", "ok",
               f"python-stdnum {installed} imports" if installed else "python-stdnum imports")


def rows() -> list:
    """The module's own checks, as moxi.doctor.Row values, in a fixed order."""
    return [rules_row(), engine_row(), private_names_row(), private_names_location_row(),
            personal_data_row()]
