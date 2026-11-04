"""The contract's two import rules, checked by import-linter and deptry.

`plan(package, workdir, members=...)` reads a package's `pyproject.toml`, its files and
its workspace siblings' declarations, writes import-linter's configuration into
`workdir`, and returns the commands that check the package. `moxi-dev check` runs them
in its own environment, where every package is installed. Nothing is configured by
hand: each rule below comes from what the packages declare.

Another module's internals (import-linter, over `src/` and `tests/`):

- a module reaches a module it requires only through the `API` module that module's
  announcement names, and a module whose announcement names none not at all;
- a module never imports an optional module (one it installs only through an extra);
  it reaches one through `moxi.modules.load` at the point of use;
- a module imports only the core's public modules;
- the core imports no module's package.

An undeclared dependency (deptry): code outside `tests/` imports only what the package
declares in `dependencies` or an extra; `tests/` may import a dependency group's too.
An import maps to its distribution by what the package declares, never by what is
installed: `moxi_<name>` to `moxi-<name>`, any other to the declared distribution of the
same normalized name, or else through the package's `[tool.moxi.imports]` table. A
declared dependency nothing imports is not a finding. deptry does not read the core's
vendored copies of released libraries, which are kept as released.

What the two tools cannot see, the names an import takes, stays in `moxi_dev.contract`.
A distribution that is neither the core nor `moxi-<name>` gets no commands. moxi-dev, the
contributors' package, gets deptry's alone: it is not a module.
"""

from __future__ import annotations

import importlib.util
import json
import pkgutil
import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .contract import (CORE, CORE_MODULES, DEV, DEV_IMPORT, VENDORED, _MODULE_DIST,
                       _REQUIREMENT, _api_from_checkout, _normalize, _Repo, _WorkTree)

#: The file import-linter reads, written into the work directory.
IMPORTLINTER_CONFIG = "importlinter.toml"

#: Directories deptry never reads, as paths under the directory it scans.
_SKIPPED = r"(^|/)(\.[^/]+|venv|build|dist|node_modules|__pycache__|[^/]+\.egg-info)(/|$)"


class LintError(Exception):
    """The package's commands cannot be written; the message says why."""


@dataclass(frozen=True)
class Command:
    """One tool run over the package."""

    what: str                      # what it checks, for the report
    argv: list[str]                # argv[0] is the tool's script in the environment
    pythonpath: list[str] = field(default_factory=list)   # put first on PYTHONPATH


def plan(package: Path, workdir: Path, members: Iterable[Path] = ()) -> list[Command]:
    """The commands that check `package`'s imports; `members` are the workspace's package
    directories, read for the core's list of modules and a required module's API."""
    package = Path(package)
    repo = _Repo(package, _WorkTree(package))
    if not repo._read_pyproject():
        raise LintError(f"{package}/pyproject.toml: "
                        + "; ".join(f.message for f in repo.findings))
    if not (repo.core or repo.name):
        return []
    repo._read_sources()
    data = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
    has_tests = any(p.startswith("tests/") and p.endswith(".py") for p in repo.tree.files())
    commands = []
    if not repo.dev:
        config = _importlinter(repo, has_tests, _members(members, package))
        if config is not None:
            workdir.mkdir(parents=True, exist_ok=True)
            path = workdir / IMPORTLINTER_CONFIG
            path.write_text(config, encoding="utf-8")
            commands.append(Command("another module's internals",
                                    ["lint-imports", "--config", str(path), "--no-cache"],
                                    [str(package)]))
    commands += _deptry(repo, data, has_tests)
    return commands


# --- import-linter ---------------------------------------------------------------------------

def _members(paths: Iterable[Path], package: Path) -> dict[str, Path]:
    """Each workspace member's normalized distribution name and directory, but `package`'s."""
    found = {}
    for path in paths:
        path = Path(path)
        if path.resolve() == package.resolve():
            continue
        try:
            name = tomllib.loads((path / "pyproject.toml").read_text())["project"]["name"]
        except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
            continue
        found[_normalize(str(name))] = path
    return found


def _module_package(dist: str) -> str | None:
    """`moxi_<name>` for a module's distribution `moxi-<name>`; None for any other,
    moxi-dev's included."""
    match = _MODULE_DIST.fullmatch(dist)
    if not match or dist == DEV:
        return None
    return f"moxi_{match.group(1)}"


def _core_private_modules() -> list[str]:
    """The core's modules outside its public table, as this environment installs it."""
    spec = importlib.util.find_spec(CORE)
    if spec is None or not spec.submodule_search_locations:
        raise LintError(f"the core ({CORE}) is not installed, so its modules are unknown")
    found = {info.name for info in pkgutil.iter_modules(spec.submodule_search_locations)}
    return sorted(f"{CORE}.{name}" for name in found - CORE_MODULES)


def _importlinter(repo: _Repo, has_tests: bool, members: Mapping[str, Path]) -> str | None:
    sources = sorted(repo.src_tops) + (["tests"] if has_tests else [])
    if not sources:
        return None
    roots = set(sources)
    contracts = []

    def forbid(name: str, forbidden: list[str], ignore: list[str] = ()) -> None:
        contracts.append({"name": name, "type": "forbidden", "source_modules": sources,
                          "forbidden_modules": forbidden, "ignore_imports": list(ignore),
                          "allow_indirect_imports": True,
                          "unmatched_ignore_imports_alerting": "none"})

    if repo.core:
        modules = sorted({top for dist in members
                          if (top := _module_package(dist)) is not None})
        if modules:
            forbid("the core imports no module's package", modules)
    else:
        for dist in sorted(repo.deps):
            top = _module_package(dist)
            if top is None or top == repo.own_import:
                continue
            module = top.removeprefix("moxi_")
            found = (_api_from_checkout(members[dist], dist) if dist in members
                     else repo._api_of(module, dist))
            if found is None or not found[0]:
                raise LintError(f"{dist}'s announcement cannot be read to learn its API "
                                "module; install the package's dependencies (uv sync) or "
                                f"check {dist} out beside it")
            api = found[1]
            roots.add(top)
            if api is not None and (api == top or api.startswith(f"{top}.")):
                forbid(f"{dist} is reached only through its API module, {api}", [top],
                       [f"{source}{tail} -> {api}" for source in sources
                        for tail in ("", ".**")])
            else:
                forbid(f"{dist} names no API module, so nothing of it is imported", [top])
        optional = sorted({top for dist in repo.extra_only
                           if (top := _module_package(dist)) is not None})
        if optional:
            forbid("an optional module is reached only through moxi.modules.load", optional)
        private = _core_private_modules()
        if private:
            roots.add(CORE)
            forbid("only the core's public modules are imported", private)
    if not contracts:
        return None
    lines = ["[tool.importlinter]", f"root_packages = {json.dumps(sorted(roots))}",
             "include_external_packages = true"]
    for contract in contracts:
        lines += ["", "[[tool.importlinter.contracts]]"]
        lines += [f"{key} = {json.dumps(value)}" for key, value in contract.items()]
    return "\n".join(lines) + "\n"


# --- deptry ------------------------------------------------------------------------------------

def _written(data: dict) -> list[str]:
    """Every distribution the pyproject requires, as it spells it, groups included."""
    project = data.get("project") or {}
    strings = list(project.get("dependencies") or [])
    for reqs in (project.get("optional-dependencies") or {}).values():
        strings += reqs or []
    for reqs in (data.get("dependency-groups") or {}).values():
        strings += [r for r in reqs or [] if isinstance(r, str)]
    names = []
    for text in strings:
        match = _REQUIREMENT.match(text) if isinstance(text, str) else None
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return names


def _name_map(repo: _Repo, data: dict) -> str:
    """deptry's `--package-module-name-map`: each declared distribution's import names,
    by its spelling and the `[tool.moxi.imports]` table, never by what is installed."""
    modules: dict[str, list[str]] = {}
    for written in _written(data):
        dist = _normalize(written)
        top = CORE if dist == CORE else _module_package(dist)
        if dist == DEV:
            top = DEV_IMPORT
        names = modules.setdefault(written, [])
        names.append(top or dist.replace("-", "_"))
        names += [imp for imp, mapped in repo.import_table.items() if mapped == dist]
    return ",".join(f"{written}={'|'.join(dict.fromkeys(names))}"
                    for written, names in modules.items())


def _deptry(repo: _Repo, data: dict, has_tests: bool) -> list[Command]:
    # Run from the package's directory: deptry matches --exclude against the paths it
    # scans as it names them, so they are named relative to it.
    base = ["deptry", "--config", "pyproject.toml", "--no-ansi", "--ignore", "DEP002"]
    mapping = _name_map(repo, data)
    if mapping:
        base += ["--package-module-name-map", mapping]
    own = sorted(repo.own)
    outside = _SKIPPED + r"|^tests(/|$)"
    if repo.core:
        outside += "|^" + re.escape(VENDORED.rstrip("/")) + "(/|$)"
    commands = [Command("undeclared dependencies outside tests/",
                        [*base, "--exclude", outside, *_first(own), "."])]
    if has_tests:
        groups = sorted((data.get("dependency-groups") or {}))
        local = sorted(set(own) | repo.tests_local)
        commands.append(Command(
            "undeclared dependencies in tests/",
            [*base, "--exclude", _SKIPPED,
             *(["--non-dev-dependency-groups", ",".join(groups)] if groups else []),
             *_first(local), "tests"]))
    return commands


def _first(names: Iterable[str]) -> list[str]:
    """deptry's `--known-first-party`, which takes one name each time it is given."""
    return [arg for name in names for arg in ("--known-first-party", name)]


__all__ = ["Command", "LintError", "IMPORTLINTER_CONFIG", "plan"]
