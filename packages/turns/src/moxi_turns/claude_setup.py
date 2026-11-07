"""turns' Claude plugin on disk: `SETUP` and `TEARDOWN`, the plugin folders and endpoint
for one launch, and its doctor rows.

The plugin ships inside the package as a folder marketplace, `moxi_turns/claude/`
(`claude_root()`), holding `.claude-plugin/marketplace.json` and two plugins: the runner
(`moxi-turns`) and the gate (`moxi-turns-gate`). Nothing writes into it.

The runner finds the endpoint command it spawns in this order: `MOXI_TURNS_ENDPOINT` in
its session's environment, else `endpoint.json` beside it. A variable that is set but not
an absolute path is no endpoint; the runner does not fall back to the file then.

`SETUP` is one step, Claude's, in three parts:

1. Place a copy of the package's folder at `DATA/turns/claude/` (`placed_root()`),
   through the core's placer as owner `turns`, so the core's ledger knows it and the
   settle step leaves it. It is a copy, never a link, so a write into it never reaches
   the package. The paths that change after placing (`endpoint.json`, and what Claude
   Code lays beside a plugin it loads from a folder: `tsconfig.json` and
   `.claude-plugin/types/`) are the placement's volatile paths.
2. Write `endpoint.json` beside the placed runner: the absolute endpoint command, which
   is `claude.endpoint` when the key is set and otherwise this environment's
   `moxi-turns-endpoint` (`moxi.paths.script`, never a PATH lookup), and the turns
   version that placed the copy. Every run rewrites it, so a changed key or a moved
   environment takes effect at the next `moxi install turns`.
3. `claude plugin marketplace add <placed folder>`, then install both plugins at user
   scope, skipping each one already done. A marketplace of the same name added from a
   package's own folder (a path ending in `moxi_turns/claude`, as an older turns added
   it) is removed and added again from the placed folder, and both plugins are installed
   again. One added from any other folder is refused.

When `claude` is not on PATH the step is skipped as a whole, with one line, and `SETUP`
succeeds: run `moxi install turns` again once Claude Code is installed.

Only a user-scope install reads `endpoint.json`: it has no launch to name its endpoint.
A launch that loads the plugins for itself gets them from
`moxi_turns.launch_additions("claude", ...)`, which names both folders for `--plugin-dir`
(`claude_plugin_dirs()`) and the endpoint command in `MOXI_TURNS_ENDPOINT`
(`launch_endpoint()`). Its folders are the placed copy's when that copy is current and
was placed by this version of turns, else the package's own, so a read-only install with
no setup run still launches. A launch writes nothing. SETUP is the one writer of
`endpoint.json`, and it writes through a temp file of its own beside the placed folder
each time, so two setups at once don't fail each other.

`TEARDOWN` removes the marketplace from Claude Code when it was added from the placed
folder, then removes turns' placements.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("moxi.turns.claude")

#: The module these placements and rows belong to.
MODULE = "turns"
#: The marketplace and plugin names (`claude plugin install moxi-turns@moxi-turns`).
MARKETPLACE = "moxi-turns"
RUNNER = "moxi-turns"
GATE = "moxi-turns-gate"
#: The file a user-scope install's runner reads through `${CLAUDE_PLUGIN_ROOT}`.
ENDPOINT_FILE = "endpoint.json"
#: The variable a launch names the endpoint command in; the runner reads it first.
ENDPOINT_ENV = "MOXI_TURNS_ENDPOINT"
#: The endpoint's console script.
ENDPOINT_SCRIPT = "moxi-turns-endpoint"
#: The Claude Code versions the live probes of the plugins passed on.
PASSED_VERSIONS = ("2.1.296",)
#: The bound on each `claude` command SETUP and the rows run.
CLAUDE_TIMEOUT_S = 60.0
#: The placed copy's folder name under turns' data dir, and its placement's name.
PLACED_NAME = "claude"
#: What Claude Code lays beside a plugin it loads from a folder, relative to the plugin.
LOADER_FILES = ("tsconfig.json", ".claude-plugin/types")
#: The line SETUP says when it skips its Claude step.
SKIPPED = ("Claude Code not found; skipped its plugin. Run moxi install turns again "
           "after installing it.")
#: The two last parts of a package's own marketplace folder, which SETUP moves from.
PACKAGE_FOLDER = ("moxi_turns", "claude")
#: The doctor row that compares the placed copy with the package.
PLACED_ROW = "claude placed copy"


class SetupError(Exception):
    """A setup step that could not be done, with why."""


def claude_root() -> Path:
    """The folder marketplace inside the package: the source of the placed copy, and the
    folders a launch uses when there is no current copy. A test points this at a copy of
    its own, so no test reads or writes the package's folder."""
    return Path(__file__).resolve().parent / "claude"


def placed_root() -> Path:
    """Where SETUP places its copy of the marketplace: `claude/` in turns' data dir. The
    path, whether or not anything is placed there."""
    from moxi.paths import Layout

    return Layout(MODULE).data_dir() / PLACED_NAME


def plugin_dir(name: str, root: Path | None = None) -> Path:
    """A plugin's folder, as the marketplace names it (`source`), else `<root>/<name>`."""
    root = claude_root() if root is None else root
    try:
        market = json.loads((root / ".claude-plugin" / "marketplace.json")
                            .read_text(encoding="utf-8"))
        for entry in market.get("plugins") or ():
            if isinstance(entry, dict) and entry.get("name") == name:
                source = entry.get("source")
                if isinstance(source, str) and source:
                    return (root / source).resolve()
    except (OSError, ValueError, AttributeError):
        pass
    return root / name


def volatile_paths(root: Path | None = None) -> tuple[str, ...]:
    """The paths in a copy of `root` (default the package's folder) that change after it
    is placed: `endpoint.json` beside the runner, and each plugin's `LOADER_FILES`. A
    plugin whose folder lies outside the marketplace has none."""
    root = claude_root() if root is None else root
    base = root.resolve()
    out: list[str] = []
    for plugin in (RUNNER, GATE):
        try:
            rel = plugin_dir(plugin, root).resolve().relative_to(base)
        except ValueError:
            continue
        if plugin == RUNNER:
            out.append((rel / ENDPOINT_FILE).as_posix())
        out.extend((rel / path).as_posix() for path in LOADER_FILES)
    return tuple(out)


def endpoint_command(env: dict[str, str] | None = None) -> Path:
    """The endpoint command this environment and turns' config give now. Raises
    `SetupError` when there is none."""
    from moxi import paths
    from moxi.config import ConfigError

    from . import config

    try:
        configured = config.reader(env).start()["claude.endpoint"]
    except ConfigError as exc:
        raise SetupError(f"turns' config cannot be read: {exc}") from None
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            raise SetupError(f"claude.endpoint {configured!r} is not an absolute path")
        return path
    try:
        return paths.script(ENDPOINT_SCRIPT)
    except FileNotFoundError as exc:
        raise SetupError(str(exc)) from None


def write_endpoint_file(root: Path | None = None, env: dict[str, str] | None = None) -> Path:
    """Write `endpoint.json` beside the runner in `root` (default the placed copy).
    Returns its path.

    It writes a temp file of its own beside `root`, outside the copy, then moves it into
    place, so two writes at once each land whole, neither fails the other, and no temp
    file is ever read as part of the copy."""
    root = placed_root() if root is None else root
    command = endpoint_command(env)
    target = plugin_dir(RUNNER, root) / ENDPOINT_FILE
    if not target.parent.is_dir():
        raise SetupError(f"the runner plugin's folder {target.parent} does not exist")
    from .endpoint import package_version

    data = json.dumps({"command": str(command), "environment": sys.prefix,
                       "version": package_version()}, indent=1) + "\n"
    try:
        fd, tmp = tempfile.mkstemp(dir=root.resolve().parent,
                                   prefix=f".{root.name}.{ENDPOINT_FILE}.")
    except OSError as exc:
        raise SetupError(f"cannot write {target}: {exc}") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise SetupError(f"cannot write {target}: {exc}") from None
    return target


def read_endpoint_file(root: Path | None = None) -> dict | None:
    """`endpoint.json` beside the runner in `root` (default the placed copy), or None."""
    root = placed_root() if root is None else root
    try:
        data = json.loads((plugin_dir(RUNNER, root) / ENDPOINT_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# -- the placed copy ---------------------------------------------------------------------


def placed_copy() -> tuple[str, str]:
    """The placed copy, read against the package: `("none", why)` when nothing is placed,
    `("current", detail)` when the placement reads `ok` and its `endpoint.json` names this
    turns' version, else `("behind", why)`. Takes no lock and writes nothing."""
    from moxi import place

    from .endpoint import package_version

    root = placed_root()
    key = Path(place.target_key(root))
    found = next((p for p in place.status(MODULE) if p.path == key), None)
    if found is None:
        return "none", "no placed copy; launches use the package's folders"
    if found.state != "ok":
        return "behind", (f"the placed copy at {root} is {found.state}; launches use the "
                          f"package's folders")
    written = read_endpoint_file(root) or {}
    have, want = written.get("version"), package_version()
    if have != want:
        held = "no version" if have is None else f"version {have}"
        return "behind", (f"the placed copy at {root} holds {held}, the package is {want}; "
                          f"launches use the package's folders")
    return "current", f"version {want} at {root}, the package's; launches use it"


def launch_root() -> Path:
    """The marketplace a launch and the plugin rows read: the placed copy when it is
    current, else the package's own folder. Never raises for a copy it cannot read."""
    try:
        if placed_copy()[0] == "current":
            return placed_root()
    except Exception as exc:  # noqa: BLE001 - the package's folder always serves
        log.debug("the placed copy could not be read: %s", exc)
    return claude_root()


def place_copy() -> tuple[Path, list[str]]:
    """SETUP's first part: place a copy of the package's folder at `placed_root()`, or
    keep the one there. Returns the folder and the lines the placer said; raises
    `SetupError` when nothing current is placed.

    It plans this one item and applies only its step: the plan takes its items as the
    owner's whole set, so applying every step would remove turns' other placements. A
    path in the way (a folder the placer did not mark, or a copy changed outside its
    volatile paths) is moved to the core's displaced folder, never deleted."""
    from moxi import files, paths, place

    root = placed_root()
    item = place.Item(claude_root(), "claude", PLACED_NAME, dest=root,
                      volatile=volatile_paths())
    lines: list[str] = []
    with files.lock(paths.core_lock(), nested=True):
        step = next(s for s in place.plan(MODULE, [item], form="copy") if s.item == item)
        if step.action == "refuse" and step.cause not in ("occupant", "tracked"):
            raise SetupError(step.reason)
        done = place.apply([step], replace=True, say=lines.append)
    key = Path(place.target_key(root))
    if not any(p.path == key and p.state == "ok" for p in done):
        why = "; ".join(lines) or step.reason
        raise SetupError(f"the copy at {root} was not placed: {why}")
    if step.action != "keep":
        lines.append(f"placed the Claude plugin at {root}")
    return root, lines


# -- launches ----------------------------------------------------------------------------


def claude_plugin_dirs() -> tuple[Path, Path]:
    """The runner's and the gate's folders, for a per-launch `--plugin-dir` each: the
    placed copy's when it is current, else the package's (`launch_root()`). Internal: a
    launch reaches it through `launch_additions`. It writes nothing.

    Raises `moxi_turns.TurnsError` when the runner's folder is missing.
    """
    from .client import TurnsError

    root = launch_root()
    runner = plugin_dir(RUNNER, root)
    if not runner.is_dir():
        raise TurnsError(f"the Claude plugin cannot be loaded: the runner plugin's folder "
                         f"{runner} does not exist")
    return runner, plugin_dir(GATE, root)


def launch_endpoint() -> str:
    """The endpoint command one launch names in `MOXI_TURNS_ENDPOINT`: the command
    `endpoint_command()` gives with turns' config read from this process's own
    environment, as SETUP reads it. Internal: a launch reaches it through
    `launch_additions`. It writes nothing.

    Raises `moxi_turns.TurnsError` when the config cannot be read, `claude.endpoint` is
    not absolute, or this environment has no endpoint script.
    """
    from .client import TurnsError

    try:
        return str(endpoint_command())
    except SetupError as exc:
        raise TurnsError(f"the Claude plugin has no endpoint command: {exc}") from None


# -- SETUP and TEARDOWN ------------------------------------------------------------------


def _claude() -> str | None:
    return shutil.which("claude")


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False,
                          timeout=CLAUDE_TIMEOUT_S, stdin=subprocess.DEVNULL)


def _json_list(claude: str, *args: str) -> list:
    done = _run([claude, "plugin", *args, "--json"])
    if done.returncode != 0:
        raise SetupError(f"`claude plugin {' '.join(args)} --json` failed "
                         f"(exit {done.returncode}): {done.stderr.strip()}")
    try:
        data = json.loads(done.stdout)
    except ValueError:
        raise SetupError(f"`claude plugin {' '.join(args)} --json` printed no JSON") \
            from None
    return data if isinstance(data, list) else []


def _must(claude: str, *args: str) -> None:
    done = _run([claude, "plugin", *args])
    if done.returncode != 0:
        raise SetupError(f"`claude plugin {' '.join(args)}` failed "
                         f"(exit {done.returncode}): {done.stderr.strip()}")


def _market_folder(claude: str) -> str | None:
    """The folder Claude Code's `moxi-turns` marketplace was added from, "" for one it
    names no folder for, or None when there is none."""
    markets = {m.get("name"): m for m in _json_list(claude, "marketplace", "list")
               if isinstance(m, dict)}
    have = markets.get(MARKETPLACE)
    if have is None:
        return None
    return have.get("path") or have.get("installLocation") or ""


def _same_folder(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except (OSError, RuntimeError):
        return False


def install_plugins(root: Path | None = None) -> list[str]:
    """SETUP's last part: add the marketplace from `root` (default the placed copy) and
    install both plugins at user scope. Returns one line per action; raises `SetupError`
    for a step that failed. Without `claude` on PATH it does nothing and returns the
    one line that says so."""
    root = placed_root() if root is None else root
    claude = _claude()
    if claude is None:
        return [SKIPPED]
    lines = []
    folder = str(root.resolve())
    where = _market_folder(claude)
    moved = False
    if where is None:
        _must(claude, "marketplace", "add", folder)
        lines.append(f"added the marketplace {MARKETPLACE} from {folder}")
    elif where and not _same_folder(where, root):
        if Path(where).parts[-2:] != PACKAGE_FOLDER:
            raise SetupError(
                f"a marketplace named {MARKETPLACE} is already added from {where}, not "
                f"{folder}; remove it with `claude plugin marketplace remove "
                f"{MARKETPLACE}` and run `moxi install turns` again")
        _must(claude, "marketplace", "remove", MARKETPLACE)
        _must(claude, "marketplace", "add", folder)
        moved = True
        lines.append(f"moved the marketplace {MARKETPLACE} from {where} to {folder}")
    else:
        lines.append(f"the marketplace {MARKETPLACE} is already added")
    installed = set() if moved else {
        p.get("id") for p in _json_list(claude, "list")
        if isinstance(p, dict) and p.get("scope") == "user"}
    for plugin in (RUNNER, GATE):
        ident = f"{plugin}@{MARKETPLACE}"
        if ident in installed:
            lines.append(f"{ident} is already installed")
            continue
        _must(claude, "install", ident)
        lines.append(f"installed {ident}")
    return lines


def setup() -> int:
    """turns' `SETUP`: its Claude step, which places the copy, writes `endpoint.json`
    there and installs both plugins. Skipped as a whole, returning 0, when `claude` is
    not on PATH. Safe to repeat. Prints what it did; returns 0, or 1 when a part that
    ran failed."""
    if _claude() is None:
        print(f"turns: {SKIPPED}")
        return 0
    try:
        root, lines = place_copy()
    except (SetupError, OSError) as exc:
        print(f"turns: cannot place the Claude plugin: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(f"turns: {line}")
    try:
        path = write_endpoint_file(root)
        print(f"turns: wrote {path}")
    except SetupError as exc:
        print(f"turns: cannot write endpoint.json: {exc}", file=sys.stderr)
        return 1
    try:
        for line in install_plugins(root):
            print(f"turns: {line}")
    except (SetupError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"turns: the Claude plugin is not installed at user scope: {exc}. "
              "A launcher can still load it per launch with --plugin-dir.",
              file=sys.stderr)
        return 1
    return 0


def teardown() -> int:
    """turns' `TEARDOWN`: remove the `moxi-turns` marketplace from Claude Code when it
    was added from the placed copy, then remove turns' placements. A marketplace added
    from any other folder is left. Returns 0, or 1 when Claude Code could not be read or
    the removal failed, which stops the uninstall."""
    from moxi import place

    root = placed_root()
    claude = _claude()
    if claude is not None:
        try:
            where = _market_folder(claude)
            if where and _same_folder(where, root):
                _must(claude, "marketplace", "remove", MARKETPLACE)
                print(f"turns: removed the marketplace {MARKETPLACE} from Claude Code")
        except (SetupError, OSError, subprocess.TimeoutExpired) as exc:
            print(f"turns: cannot remove the marketplace {MARKETPLACE} from Claude Code: "
                  f"{exc}. Fix that and uninstall again, or uninstall without it: moxi "
                  "uninstall turns --skip-teardown", file=sys.stderr)
            return 1
    gone = place.remove(MODULE, say=lambda line: print(f"turns: {line}"))
    for placed in gone:
        print(f"turns: removed {placed.path}")
    if claude is None and gone:
        print(f"turns: Claude Code not found; if it still lists the {MARKETPLACE} "
              f"marketplace, remove it with `claude plugin marketplace remove "
              f"{MARKETPLACE}`")
    return 0


# -- doctor rows -------------------------------------------------------------------------


def rows() -> list:
    """`check()`'s Claude rows: each plugin's presence and version in the folders a launch
    would use, the placed copy against the package, whether the placed `endpoint.json`
    names the command the config and the environment give now, and the Claude Code
    version against the ones the probes passed.

    A missing or stale `endpoint.json` fails when the runner is installed at user scope,
    since every such session then runs without turns and nothing but SETUP rewrites the
    file; otherwise it warns, since only a user-scope install reads it. With no setup run
    (or `claude` missing when it ran) no row fails: the plugin rows read the package."""
    from moxi.doctor import Row

    out = []
    try:
        state, detail = placed_copy()
    except Exception as exc:  # noqa: BLE001 - a doctor reports, it does not raise
        state, detail = "behind", f"the placed copy could not be read: {exc}"
    root = placed_root() if state == "current" else claude_root()
    where = "the placed copy" if state == "current" else "the package"
    for plugin in (RUNNER, GATE):
        folder = plugin_dir(plugin, root)
        try:
            manifest = json.loads((folder / ".claude-plugin" / "plugin.json")
                                  .read_text("utf-8"))
            out.append(Row(MODULE, f"claude plugin {plugin}", "ok",
                           f"version {manifest.get('version', '?')} at {folder}"))
        except (OSError, ValueError) as exc:
            out.append(Row(MODULE, f"claude plugin {plugin}", "fail",
                           f"missing from {where} at {folder}: {exc}",
                           "reinstall turns" if state != "current"
                           else "moxi install turns"))
    out.append(Row(MODULE, PLACED_ROW, {"none": "off", "current": "ok"}.get(state, "warn"),
                   detail, "moxi install turns" if state == "behind" else None))
    written = read_endpoint_file()
    try:
        want = str(endpoint_command())
    except SetupError as exc:
        want = None
        out.append(Row(MODULE, "claude endpoint.json", "fail",
                       f"no endpoint command: {exc}"))
    if want is not None:
        if written is not None and written.get("command") == want:
            out.append(Row(MODULE, "claude endpoint.json", "ok", f"names {want}"))
        else:
            problem = ("missing" if written is None else
                       f"names {written.get('command')!r}, but the config and this "
                       f"environment give {want!r}")
            out.append(_endpoint_file_row(Row, MODULE, problem))
    out.append(_version_row(Row, MODULE))
    return out


def _user_scope_runner() -> bool | None:
    """Whether the runner is installed at user scope, as SETUP reads it; None when
    `claude` is not on PATH or the call fails."""
    claude = _claude()
    if claude is None:
        return None
    try:
        listed = _json_list(claude, "list")
    except (SetupError, OSError, subprocess.TimeoutExpired):
        return None
    return any(isinstance(p, dict) and p.get("scope") == "user"
               and p.get("id") == f"{RUNNER}@{MARKETPLACE}" for p in listed)


def _endpoint_file_row(Row, module, problem: str):
    installed = _user_scope_runner()
    if installed:
        return Row(module, "claude endpoint.json", "fail",
                   f"{problem}: {RUNNER}@{MARKETPLACE} is installed at user scope, so "
                   "every session it loads in runs without turns until the file is "
                   "rewritten", "moxi install turns")
    why = ("no user-scope install of the runner reads it" if installed is False else
           "only a user-scope install of the runner reads it, and `claude plugin list` "
           "could not say whether there is one")
    return Row(module, "claude endpoint.json", "warn",
               f"{problem}; {why}. A launch through launch_additions names its endpoint "
               f"in {ENDPOINT_ENV} and needs no file", "moxi install turns")


def _version_row(Row, module):
    claude = _claude()
    if claude is None:
        return Row(module, "claude code", "off", "`claude` is not on PATH")
    try:
        done = _run([claude, "--version"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Row(module, "claude code", "fail", f"`claude --version` failed: {exc}")
    version = (done.stdout.split() or ["?"])[0]
    if version in PASSED_VERSIONS:
        return Row(module, "claude code", "ok", f"{version}, which the probes passed")
    return Row(module, "claude code", "warn",
               f"{version}, which the probes have not seen (passed: "
               f"{', '.join(PASSED_VERSIONS)})")


__all__ = ["MARKETPLACE", "RUNNER", "GATE", "ENDPOINT_FILE", "ENDPOINT_SCRIPT",
           "PASSED_VERSIONS", "SetupError", "claude_root", "placed_root", "plugin_dir",
           "volatile_paths", "endpoint_command", "write_endpoint_file",
           "read_endpoint_file", "placed_copy", "launch_root", "place_copy",
           "claude_plugin_dirs", "launch_endpoint", "ENDPOINT_ENV",
           "install_plugins", "setup", "teardown", "rows"]
