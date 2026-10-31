"""turns' Claude plugin on disk: `SETUP`, the plugin folders and endpoint for one launch,
and its doctor rows.

The plugin ships inside the package as a folder marketplace, `moxi_turns/claude/`,
holding `.claude-plugin/marketplace.json` and two plugins: the runner (`moxi-turns`)
and the gate (`moxi-turns-gate`).

The runner finds the endpoint command it spawns in this order: `MOXI_TURNS_ENDPOINT` in
its session's environment, else `endpoint.json` beside it. A variable that is set but not
an absolute path is no endpoint; the runner does not fall back to the file then.

`SETUP` has two steps:

1. Write `endpoint.json` beside the runner: the absolute endpoint command, which is
   `claude.endpoint` when the key is set and otherwise this environment's
   `moxi-turns-endpoint` (`moxi.paths.script`, never a PATH lookup). Every run
   rewrites it, so a changed key or a moved environment takes effect at the next
   `moxi install turns`. The file is generated and never tracked.
2. `claude plugin marketplace add <folder>`, then install both plugins at user scope,
   skipping each one already done.

Only a user-scope install needs either step: it has no launch to name its endpoint, so
it reads the file. A launch that loads the plugins for itself gets them from
`moxi_turns.launch_additions("claude", ...)`, which names both folders for `--plugin-dir`
(`claude_plugin_dirs()`) and the endpoint command in `MOXI_TURNS_ENDPOINT`
(`launch_endpoint()`). A launch writes nothing into the package, so launches in parallel
share no write and a read-only install still launches. SETUP is the one writer of
`endpoint.json`, and it writes through a temp file of its own each time, so two setups at
once don't fail each other.
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


class SetupError(Exception):
    """A setup step that could not be done, with why."""


def claude_root() -> Path:
    """The folder marketplace inside the package. A test points this at a copy of its
    own, so no test writes the `endpoint.json` of the environment it runs in."""
    return Path(__file__).resolve().parent / "claude"


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
    """SETUP's first step: write `endpoint.json` beside the runner. Returns its path.

    It writes a temp file of its own in the same folder, then moves it into place, so two
    writes at once each land whole and neither fails the other."""
    command = endpoint_command(env)
    target = plugin_dir(RUNNER, root) / ENDPOINT_FILE
    if not target.parent.is_dir():
        raise SetupError(f"the runner plugin's folder {target.parent} does not exist")
    from .endpoint import package_version

    data = json.dumps({"command": str(command), "environment": sys.prefix,
                       "version": package_version()}, indent=1) + "\n"
    try:
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{ENDPOINT_FILE}.")
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
    try:
        data = json.loads((plugin_dir(RUNNER, root) / ENDPOINT_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def claude_plugin_dirs() -> tuple[Path, Path]:
    """The runner's and the gate's folders, for a per-launch `--plugin-dir` each. Internal:
    a launch reaches it through `launch_additions`. It writes nothing.

    Raises `moxi_turns.TurnsError` when the runner's folder is missing.
    """
    from .client import TurnsError

    root = claude_root()
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


# -- SETUP -------------------------------------------------------------------------------


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


def install_plugins(root: Path | None = None) -> list[str]:
    """SETUP's second step. Returns one line per action; raises `SetupError`."""
    root = claude_root() if root is None else root
    claude = _claude()
    if claude is None:
        raise SetupError("`claude` is not on PATH; install Claude Code, then run "
                         "`moxi install turns` again")
    lines = []
    markets = {m.get("name"): m for m in _json_list(claude, "marketplace", "list")
               if isinstance(m, dict)}
    have = markets.get(MARKETPLACE)
    folder = str(root.resolve())
    if have is None:
        done = _run([claude, "plugin", "marketplace", "add", folder])
        if done.returncode != 0:
            raise SetupError(f"`claude plugin marketplace add {folder}` failed "
                             f"(exit {done.returncode}): {done.stderr.strip()}")
        lines.append(f"added the marketplace {MARKETPLACE} from {folder}")
    else:
        where = have.get("path") or have.get("installLocation")
        if where and Path(where).resolve() != Path(folder):
            raise SetupError(
                f"a marketplace named {MARKETPLACE} is already added from {where}, not "
                f"{folder}; remove it with `claude plugin marketplace remove "
                f"{MARKETPLACE}` and run `moxi install turns` again")
        lines.append(f"the marketplace {MARKETPLACE} is already added")
    installed = {p.get("id") for p in _json_list(claude, "list")
                 if isinstance(p, dict) and p.get("scope") == "user"}
    for plugin in (RUNNER, GATE):
        ident = f"{plugin}@{MARKETPLACE}"
        if ident in installed:
            lines.append(f"{ident} is already installed")
            continue
        done = _run([claude, "plugin", "install", ident])
        if done.returncode != 0:
            raise SetupError(f"`claude plugin install {ident}` failed "
                             f"(exit {done.returncode}): {done.stderr.strip()}")
        lines.append(f"installed {ident}")
    return lines


def setup() -> int:
    """turns' `SETUP`: write `endpoint.json`, then install both plugins.
    Safe to repeat. Prints what it did; returns 0, or 1 when a step failed."""
    try:
        path = write_endpoint_file()
        print(f"turns: wrote {path}")
    except SetupError as exc:
        print(f"turns: cannot write endpoint.json: {exc}", file=sys.stderr)
        return 1
    try:
        for line in install_plugins():
            print(f"turns: {line}")
    except (SetupError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"turns: the Claude plugin is not installed at user scope: {exc}. "
              "A launcher can still load it per launch with --plugin-dir.",
              file=sys.stderr)
        return 1
    return 0


# -- doctor rows -------------------------------------------------------------------------


def rows() -> list:
    """`check()`'s Claude rows: each plugin's presence and version, whether
    `endpoint.json` names the command the config and the environment give now, and the
    Claude Code version against the ones the probes passed.

    A missing or stale `endpoint.json` fails when the runner is installed at user scope,
    since every such session then runs without turns and nothing but SETUP rewrites the
    file; otherwise it warns, since only a user-scope install reads it."""
    from moxi.doctor import Row

    from .doctor import MODULE

    root = claude_root()
    out = []
    for plugin in (RUNNER, GATE):
        folder = plugin_dir(plugin, root)
        try:
            manifest = json.loads((folder / ".claude-plugin" / "plugin.json")
                                  .read_text("utf-8"))
            out.append(Row(MODULE, f"claude plugin {plugin}", "ok",
                           f"version {manifest.get('version', '?')} at {folder}"))
        except (OSError, ValueError) as exc:
            out.append(Row(MODULE, f"claude plugin {plugin}", "fail",
                           f"missing from the package at {folder}: {exc}",
                           "reinstall turns"))
    written = read_endpoint_file(root)
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
           "PASSED_VERSIONS", "SetupError", "claude_root", "plugin_dir", "endpoint_command",
           "write_endpoint_file", "read_endpoint_file", "claude_plugin_dirs",
           "launch_endpoint", "ENDPOINT_ENV",
           "install_plugins", "setup", "rows"]
