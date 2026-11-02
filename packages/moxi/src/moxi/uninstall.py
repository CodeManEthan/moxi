"""`moxi uninstall`: take a module out of the environment `moxi` runs in.

    moxi uninstall <module> [--purge] [--yes] [--skip-teardown]

0. `moxi uninstall moxi` is refused: uninstalling a module never removes the `moxi`
   command (`uv tool uninstall moxi` does). The environment checks are install's: the
   system Python and an editable `moxi` are refused. The core lock is taken once, here,
   and held to the end, so an install and an uninstall never interleave.
1. The module must be installed (present or broken). An installed module that requires
   it refuses the uninstall, naming each one.
2. Its `TEARDOWN`, in a fresh process (`python -m moxi._settle --teardown`), before
   anything is removed, so it still finds the module's units in place. A `TEARDOWN`
   that raises `moxi.modules.Refused` stops the uninstall with its reason (exit 3); any
   other failure stops it too (exit 1). One that never ran (its target can't be loaded,
   or the child can't start) removed nothing, and the line names `--skip-teardown` as the
   way past it. Once it ran, the core can't tell what it removed: what the module
   reported stands on its own, and the line names neither. An interrupt abandons the
   `TEARDOWN` as a failed one: nothing is removed. A broken module has no teardown to
   run.
3. Every `moxi.installs` listener's `on_uninstall(module)`, the module's own included.
4. Its units (`moxi.units.remove`, which also unwraps every foreign timer it wrapped and
   drops its `units.json` entries), and its install questions' record.
5. Its placements (`_placements`).
6. The package: in a uv tool environment, `uv tool install` run again from the receipt
   without `moxi-<module>` and without any extra named `<module>` on another
   requirement; in a plain one, `uv pip uninstall` (or `pip uninstall`). Modules it
   pulled in stay installed (in a uv tool environment they are written into the receipt
   for that), and the report names each that nothing else requires, with its own
   uninstall line.

7. With `--purge`, its stores: each store its announcement declares (`STORES`, read at
   step 1, before the package went), with its path and size. A store marked rebuildable
   is removed; any other is asked about, default no, `--yes` answers yes, and with
   nobody at the keyboard it is kept and its path printed. Then the core's own stores in
   the module's state directory (the gate's `runs.jsonl` with its `.1` and `logs/`, the
   doctor's `doctor.json`), removed without asking, and the module's `setup.json` entry.
   The config file is kept and its path printed. A store that is a link has the link
   removed and what it points at kept. A broken module's declared stores can't be read,
   so only the core's are removed.

From step 3 on, a step that fails prints why and the later steps still run; the exit is
then 1. Without `--purge` the module's config file and stores are kept.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import names
from .cli import normalize
from .exit import REFUSED
from .install import InstallError


def run_teardown(python: str, module: str):
    """Run module `module`'s `TEARDOWN` in a fresh process under this uninstall's hold
    of the core lock, and wait for it however long it takes (`moxi._settle.run_child`).
    Returns its `moxi._settle.Outcome`."""
    from . import _settle

    return _settle.run_child(_settle.child_argv(python, module, teardown=True))


def _parser(prog: str):
    import argparse

    parser = argparse.ArgumentParser(
        prog=prog, description="Uninstall a Moxi module: run its teardown, then remove its "
                               "units, its skills and its package. Its config file is "
                               "kept, and so are its stores unless --purge is given.")
    parser.add_argument("module", help="the module, by its name (comms, cache, ...)")
    parser.add_argument("--purge", action="store_true",
                        help="also remove the module's stores: rebuildable ones at once, "
                             "any other after asking; and the core's run records, logs "
                             "and doctor records for it")
    parser.add_argument("--yes", action="store_true",
                        help="with --purge, remove every store without asking")
    parser.add_argument("--skip-teardown", action="store_true",
                        help="uninstall without running the module's TEARDOWN, when it "
                             "can't run")
    return parser


def main(argv: list[str] | None = None, prog: str = "moxi uninstall") -> int:
    parser = _parser(prog)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.yes and not args.purge:
        parser.error("--yes answers --purge's questions; give it with --purge")
    try:
        return Uninstaller(args).run()
    except InstallError as exc:
        print(f"moxi uninstall: {exc}", file=sys.stderr)
        return exc.exit_code


class Uninstaller:
    def __init__(self, args):
        self.module = args.module
        self.skip_teardown = bool(getattr(args, "skip_teardown", False))
        self.purge = bool(getattr(args, "purge", False))
        self.yes = bool(getattr(args, "yes", False))
        self.failed: list[str] = []

    # step 0 ----------------------------------------------------------------------
    def run(self) -> int:
        module = self.module
        if module == names.HARNESS:
            raise InstallError("uninstalling a module never removes the moxi command; to "
                               "remove Moxi itself: uv tool uninstall moxi",
                               exit_code=REFUSED)
        if not names.is_module_name(module):
            raise InstallError(f"{module!r} is not a module name: lower-case ASCII letters "
                               f"and digits only")
        if module in names.VERBS or module in names.RESERVED:
            raise InstallError(f"{module!r} is one of moxi's own names, not a module")
        from .install import _core_lock

        with _core_lock():
            return self._run(module)

    def _run(self, module: str) -> int:
        from . import install

        env = install.environment()
        self.env = env

        # step 1 ------------------------------------------------------------------
        target = self._target(module)
        dist = target.distribution or names.distribution(module)
        pulled = self._pulled_in(module, dist)

        # step 2 ------------------------------------------------------------------
        code = self._teardown(target)
        if code is not None:
            return code

        # steps 3 to 5 --------------------------------------------------------------
        self._listeners(module)
        self._units(module)
        self._placements(module)

        # step 6 ------------------------------------------------------------------
        self._package(module, dist, pulled)

        # step 7 ------------------------------------------------------------------
        if self.purge:
            self._purge(target)

        # the report ---------------------------------------------------------------
        print()
        version = f" {target.version}" if target.version else ""
        if "package" in self.failed:
            print(f"{module} was not uninstalled: its package is still installed",
                  file=sys.stderr)
        else:
            print(f"uninstalled {module} ({dist}{version})")
        for name in pulled:
            print(f"{name} was installed for {module}, and nothing else requires it now; "
                  f"uninstall it with: moxi uninstall {name}")
        for step in self.failed:
            print(f"the {step} step failed; see above", file=sys.stderr)
        return 1 if self.failed else 0

    def _target(self, module: str):
        """Step 1: the module's `Found`, which must be present or broken, and no
        installed module requiring it."""
        from . import cli, modules

        scanned = {found.name: found for found in modules.scan()}
        target = scanned.get(module)
        if target is None:
            raise InstallError(f"{module} is not installed here", exit_code=REFUSED)
        by_dist = cli._module_of_distribution()
        requirers = [found.name for found in scanned.values()
                     if found.name != module and found.distribution
                     and module in cli.required_modules(found.distribution, by_dist)]
        if requirers:
            raise InstallError(f"{', '.join(requirers)} "
                               f"{'requires' if len(requirers) == 1 else 'require'} "
                               f"{module}; uninstall {'it' if len(requirers) == 1 else 'them'}"
                               f" first", exit_code=REFUSED)
        return target

    def _pulled_in(self, module: str, dist: str) -> list[str]:
        """The modules `module` requires that nothing else installed requires or takes
        through an installed extra: they stay, and the report names them."""
        from . import cli

        out = []
        for name in cli.required_modules(dist):
            others = [user for user in cli.users_of(name)
                      if user.split(" ", 1)[0] != module]
            if not others:
                out.append(name)
        return out

    # step 2 ----------------------------------------------------------------------
    def _teardown(self, target) -> int | None:
        """Run the module's `TEARDOWN` in a child. Returns the exit code when the
        uninstall stops here, None when it goes on."""
        from . import _settle, modules
        from ._settle import guard_streams, say

        module = target.name
        if self.skip_teardown:
            say(f"teardown {module}: skipped (--skip-teardown)")
            return None
        if target.state != modules.PRESENT:
            say(f"{module} is {target.state}, so it has no teardown to run")
            return None
        if not target.teardown:
            return None
        guard_streams()
        outcome = run_teardown(self.env.python, module)
        if outcome.interrupted:
            raise InstallError(f"interrupted in {module}'s teardown; nothing was removed")
        if outcome.code == REFUSED:
            say(f"{module}'s teardown refused the uninstall; nothing was removed", err=True)
            return REFUSED
        if outcome.code in (_settle.NOT_RUN, 127):     # 127: the child didn't start
            say(f"teardown {module}: it could not run, so nothing was removed. To "
                f"uninstall without it: moxi uninstall {module} --skip-teardown", err=True)
            return 1
        if outcome.code != 0:
            say(f"teardown {module}: failed (exit {outcome.code}); the uninstall stopped "
                f"here and {module} stays installed", err=True)
            return 1
        return None

    # step 3 ----------------------------------------------------------------------
    def _listeners(self, module: str) -> None:
        """Every `moxi.installs` listener's `on_uninstall(module)`, the module's own
        included; one that fails is printed and the rest still run."""
        from . import modules
        from ._settle import INSTALLS, _one_line, say

        for ext in modules.extensions(INSTALLS):
            try:
                getattr(ext.load(), "on_uninstall")(module)
            except (Exception, SystemExit) as exc:
                reason = (exc.reason if isinstance(exc, modules.ExtensionError)
                          else _one_line(exc))
                say(f"{module}: the moxi.installs listener {ext.name} failed: {reason}",
                    err=True)
                if "listeners" not in self.failed:
                    self.failed.append("listeners")

    # step 4 ----------------------------------------------------------------------
    def _units(self, module: str) -> None:
        """The module's units, its wraps, its `units.json` entries and its install
        questions' record."""
        from . import _questions, units
        from ._settle import _one_line, say

        try:
            applied = units.remove(module, say=say)
        except NotImplementedError:
            applied = []   # no writer on this system, so no unit of moxi's either
        except Exception as exc:  # noqa: BLE001 - the later steps still run
            say(f"{module}: units not removed: {_one_line(exc)}", err=True)
            applied = None
        if applied is None or not all(a.ok for a in applied):
            for result in applied or ():
                if not result.ok:
                    for line in result.lines or (f"{result.change.name}: not removed",):
                        say(f"{module}: {line}", err=True)
            self.failed.append("units")
        try:
            _questions.forget(module)
        except OSError as exc:
            say(f"{module}: the install questions' record was not dropped: {exc}",
                err=True)
            if "units" not in self.failed:
                self.failed.append("units")

    # step 5 ----------------------------------------------------------------------
    def _placements(self, module: str) -> None:
        """The module's placements, removed by the placer (`moxi.place.remove`) in this
        process, where its lock yields on the uninstall's own hold. What the module
        handed to another owner stays. One line per placement removed; a placement that
        couldn't be removed is printed by the placer and fails the step."""
        from . import place
        from ._settle import _one_line, say

        try:
            gone = place.remove(module, say=lambda line: say(f"{module}: {line}"))
            left = place.status(module)
        except Exception as exc:  # noqa: BLE001 - the later steps still run
            say(f"{module}: placements not removed: {_one_line(exc)}", err=True)
            self.failed.append("placements")
            return
        for p in gone:
            say(f"{module}: removed {p.kind} {p.name} for {p.harness} ({p.path})")
        if left:
            for p in left:
                say(f"{module}: {p.kind} {p.name} for {p.harness} was not removed "
                    f"({p.path})", err=True)
            self.failed.append("placements")

    # step 6 ----------------------------------------------------------------------
    def _package(self, module: str, dist: str, pulled: list[str]) -> None:
        from . import install
        from ._settle import say

        env = self.env
        if env.kind == install.TOOL:
            import shutil
            import tempfile

            uv = shutil.which("uv")
            if uv is None:
                say("moxi uninstall: this is a uv tool environment, and uv is not on PATH",
                    err=True)
                self.failed.append("package")
                return
            keep = []
            for name in pulled:
                req = None
                try:
                    req = install.installed_source(names.distribution(name))
                except InstallError:
                    pass
                keep.append(req or install.Req(names.distribution(name)))
            with tempfile.TemporaryDirectory(prefix="moxi-uninstall-") as scratch:
                try:
                    argv, extra_env, _ = install.tool_command(
                        env.receipt, env.prefix, keep, uv, Path(scratch), without=module)
                except InstallError as exc:
                    say(f"moxi uninstall: {exc}", err=True)
                    self.failed.append("package")
                    return
                code = install.run(argv, extra_env)
        else:
            code = install.run(uninstall_command(dist))
        if code != 0:
            say(f"moxi uninstall: removing {dist} failed (exit {code})", err=True)
            self.failed.append("package")


    # step 7 ----------------------------------------------------------------------
    def _purge(self, target) -> None:
        """`--purge`: the module's declared stores as read at step 1, the core's stores
        in its state directory, and its `setup.json` entry. The config file is kept."""
        from . import modules, paths
        from ._settle import _ask, _one_line, forget_setup, say
        from .cli import _size_text, _store_facts

        module = target.name
        layout = paths.Layout(module)
        roots = {"data": layout.data_dir, "state": layout.state_dir,
                 "cache": layout.cache_dir, "runtime": layout.runtime_dir}
        ok = True
        if target.state != modules.PRESENT:
            say(f"{module} is {target.state}, so its declared stores can't be read; only "
                f"the core's stores in its state directory are removed")
        for name, entry in target.stores.items():
            path = roots[entry["root"]]() / entry["path"]
            if not os.path.lexists(path):
                say(f"{module}: store {name} is not there ({path})")
                continue
            size = _store_facts(path, entry["kind"])["size"]
            what = f"{path} ({_size_text(size)})" if size is not None else str(path)
            if not entry.get("rebuildable", False):
                wanted = self.yes or _ask(f"{module}: remove store {name}, {what}? It "
                                          f"can't be rebuilt. [y/N] ")
                if not wanted:
                    say(f"{module}: kept store {name}: {what}")
                    continue
            ok = _remove_store(module, f"store {name}", path, entry["kind"], what) and ok
        state = layout.state_dir()
        for name in modules.CORE_STATE_FILES + modules.CORE_STATE_DIRS:
            path = state / name
            if os.path.lexists(path):
                kind = "dir" if name in modules.CORE_STATE_DIRS else "file"
                ok = _remove_store(module, name, path, kind, str(path)) and ok
        try:
            forget_setup(module)
        except OSError as exc:
            say(f"{module}: its setup.json entry was not dropped: {_one_line(exc)}",
                err=True)
            ok = False
        config = layout.config_file()
        if config.exists():
            say(f"{module}: kept its config file: {config}")
        if not ok:
            self.failed.append("purge")


#: The files SQLite keeps beside a store, removed with it.
_SQLITE_SIDES = ("-wal", "-shm", "-journal")


def _remove_store(module: str, label: str, path: Path, kind: str, what: str) -> bool:
    """Remove one store at `path`; True when it went. A link (or a junction) is removed
    as a link, and what it points at is kept. A SQLite store's `-wal`, `-shm` and
    `-journal` files go with it."""
    import shutil

    from ._settle import say

    try:
        if os.path.islink(path) or _is_junction(path):
            pointed = os.readlink(path)
            try:
                os.unlink(path)
            except (IsADirectoryError, PermissionError):
                os.rmdir(path)   # a directory link or junction on Windows
            say(f"{module}: removed {label}, a link; kept what it points at ({pointed})")
            return True
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
        if kind == "sqlite":
            for side in _SQLITE_SIDES:
                try:
                    os.unlink(f"{path}{side}")
                except FileNotFoundError:
                    pass
    except OSError as exc:
        say(f"{module}: {label} not removed ({path}): {exc.strerror or exc}", err=True)
        return False
    say(f"{module}: removed {label}: {what}")
    return True


def _is_junction(path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def uninstall_command(dist: str) -> list[str]:
    """A plain environment's command that removes distribution `dist`."""
    import shutil

    uv = shutil.which("uv")
    if uv is not None:
        return [uv, "pip", "uninstall", "--python", sys.executable, dist]
    return [sys.executable, "-m", "pip", "uninstall", "-y", dist]


def without(reqs: list, module: str) -> list:
    """`reqs` without module `module`'s distribution and without any extra named
    `module` on another requirement (`moxi-comms[cache]` becomes `moxi-comms`)."""
    from .install import Req

    own = normalize(names.distribution(module))
    name = normalize(module)
    out = []
    for req in reqs:
        if req.key == own:
            continue
        extras = [e for e in req.extras if normalize(e) != name]
        out.append(req if extras == req.extras
                   else Req(req.name, extras, req.specifier, req.url, req.editable))
    return out


__all__ = ["main", "Uninstaller", "run_teardown", "uninstall_command", "without"]
