"""`moxi uninstall`: take a module out of the environment `moxi` runs in.

    moxi uninstall <module> [--skip-teardown]

0. `moxi uninstall moxi` is refused: uninstalling a module never removes the `moxi`
   command (`uv tool uninstall moxi` does). The environment checks are install's: the
   system Python and an editable `moxi` are refused. The core lock is taken once, here,
   and held to the end, so an install and an uninstall never interleave.
1. The module must be installed (present or broken). An installed module that requires
   it refuses the uninstall, naming each one.
2. Its `TEARDOWN`, in a fresh process (`python -m moxi._settle --teardown`), before
   anything is removed, so it still finds the module's units in place. A `TEARDOWN`
   that raises `moxi.modules.Refused` stops the uninstall with its reason (exit 3); any
   other failure stops it too (exit 1), naming `--skip-teardown` as the way past one
   that can't run. An interrupt abandons the `TEARDOWN` as a failed one: nothing is
   removed. A broken module has no teardown to run.
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

From step 3 on, a step that fails prints why and the later steps still run; the exit is
then 1. The module's config file and stores are kept.
"""

from __future__ import annotations

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
                               "units and its package. Its config file and stores are "
                               "kept.")
    parser.add_argument("module", help="the module, by its name (comms, cache, ...)")
    parser.add_argument("--skip-teardown", action="store_true",
                        help="uninstall without running the module's TEARDOWN, when it "
                             "can't run")
    return parser


def main(argv: list[str] | None = None, prog: str = "moxi uninstall") -> int:
    args = _parser(prog).parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return Uninstaller(args).run()
    except InstallError as exc:
        print(f"moxi uninstall: {exc}", file=sys.stderr)
        return exc.exit_code


class Uninstaller:
    def __init__(self, args):
        self.module = args.module
        self.skip_teardown = bool(getattr(args, "skip_teardown", False))
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
        from . import modules
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
        if outcome.code != 0:
            say(f"teardown {module}: failed (exit {outcome.code}); nothing was removed. "
                f"To uninstall without it: moxi uninstall {module} --skip-teardown",
                err=True)
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
        """The module's placements: its skills and agent files, which the placer
        (`moxi.place.remove`) removes. This step doesn't call the placer yet, so it
        removes nothing."""

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
