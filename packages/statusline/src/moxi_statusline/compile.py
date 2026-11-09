"""statusline's `moxi.installs` listener: the bytecode a render loads, compiled at install.

The render runs under the core's audit hook, which stops bytecode writes, so a module only
ever imported by a render would be compiled again in memory at every render. So at every
module's install (`on_install`), this compiles, with `compileall`:

- the core's package, `moxi`, whole;
- this package, whole;
- for every present module that announces a `SEGMENT`: its announcement, which a render
  reads to find the segment, and the segment module's import closure inside its own
  package (what it imports from that package, found by reading the files, not by
  importing them), with each package's `__init__` on the way.

It runs at every install of any module, so an upgraded core or segment module is compiled
again. A file it can't compile (a read-only folder) is no failure: the render still runs,
slower, and the doctor's bytecode row names what lacks a current `.pyc`.

`.pyc` files written into another distribution's folder are not in that distribution's
record, so uninstalling it may leave its `__pycache__` behind. Python ignores a `.pyc`
whose source is gone. `on_uninstall` has nothing to do.

`current(source)` says whether a source file's `.pyc` is there and matches it, the way the
import system judges it; `render_sources` lists the files a render of given items loads.
"""

import ast
import importlib.util
import os
from collections.abc import Callable, Iterable

OWNER = "statusline"


def on_install(module: str, found=None, *, say: Callable[[str], object] = print) -> None:
    """After any module's install: compile what a render loads."""
    failed = compile_all()
    if failed:
        say(f"statusline: {len(failed)} files a status line render loads could not be "
            "compiled; the line still renders, and `moxi doctor` names them")


def on_uninstall(module: str, *, say: Callable[[str], object] = print) -> None:
    """Before a module's package goes: nothing; its `.pyc` files go with its folder or are
    ignored."""


def compile_all() -> list[str]:
    """Compile the core's package, this package and every present segment's files.
    Returns the files a render loads that are left without a current `.pyc`."""
    import compileall

    from .render import RENDER_IMPORTS

    for top in ("moxi", "moxi_statusline"):
        folder = package_dir(top)
        if folder is not None:
            compileall.compile_dir(folder, quiet=2)
    files = []
    for name in present_segments():
        files += [f for f in segment_sources(name) if f not in files]
    for path in files:
        compileall.compile_file(path, quiet=2)
    loaded = [*render_sources(RENDER_IMPORTS).values(), *files]
    return [f for f in loaded if not current(f)]


def present_segments() -> list[str]:
    """The names of every present module that announces a `SEGMENT`."""
    from moxi import modules

    out = []
    for entry in modules.installed():
        found = modules.announced(entry.name)
        if found.state == modules.PRESENT and found.segment and entry.name not in out:
            out.append(entry.name)
    return out


def segment_sources(name: str) -> list[str]:
    """The source files a render loads for module item `name`: its announcement and its
    `SEGMENT` module's closure inside its package. Empty when it has no segment."""
    from moxi import modules

    found = modules.announced(name)
    if found.state != modules.PRESENT or not found.segment:
        return []
    out = []
    for dotted in (found.target, found.segment):
        for path in closure(dotted):
            if path not in out:
                out.append(path)
    return out


def render_sources(names: Iterable[str]) -> dict[str, str]:
    """`{module: source file}` for each dotted module name in `names` that has a source
    file here."""
    out = {}
    for name in names:
        path = _origin(name)
        if path is not None:
            out[name] = path
    return out


def closure(dotted: str) -> list[str]:
    """The source files of module `dotted` and of every module of its top-level package it
    imports, directly or not, with each package's `__init__` on the way. Read from the
    files with `ast`; nothing is imported."""
    top = dotted.split(".")[0]
    roots = _roots(top)
    if not roots:
        return []
    seen: dict[str, str] = {}
    queue = [dotted]
    while queue:
        name = queue.pop(0)
        if name in seen:
            continue
        found = _file(name, roots)
        if found is None:
            continue
        path, is_package = found
        seen[name] = path
        parts = name.split(".")
        queue += [".".join(parts[:i]) for i in range(1, len(parts))]
        queue += [n for n in _imports(path, name, is_package)
                  if n.split(".")[0] == top and n not in seen]
    return list(seen.values())


def current(source: str) -> bool:
    """Whether `source` has a `.pyc` the import system would use as it is."""
    try:
        pyc = importlib.util.cache_from_source(source)
        with open(pyc, "rb") as fh:
            header = fh.read(16)
        st = os.stat(source)
    except (OSError, ValueError, NotImplementedError):
        return False
    if len(header) < 16 or header[:4] != importlib.util.MAGIC_NUMBER:
        return False
    flags = int.from_bytes(header[4:8], "little")
    if flags & 0b1:
        if not flags & 0b10:
            return True
        try:
            with open(source, "rb") as fh:
                return header[8:16] == importlib.util.source_hash(fh.read())
        except OSError:
            return False
    mtime = int.from_bytes(header[8:12], "little")
    size = int.from_bytes(header[12:16], "little")
    return mtime == int(st.st_mtime) & 0xFFFFFFFF and size == st.st_size & 0xFFFFFFFF


def package_dir(top: str) -> str | None:
    """The folder of top-level package `top`, found without importing it."""
    roots = _roots(top)
    return roots[0] if roots else None


def _roots(top: str) -> list[str]:
    try:
        spec = importlib.util.find_spec(top)
    except (ImportError, ValueError):
        return []
    if spec is None or not spec.submodule_search_locations:
        return []
    return list(spec.submodule_search_locations)


def _file(name: str, roots: list[str]) -> tuple[str, bool] | None:
    """`(source file, is a package)` for dotted `name` under its package's `roots`."""
    rest = name.split(".")[1:]
    for root in roots:
        base = os.path.join(root, *rest)
        init = os.path.join(base, "__init__.py")
        if os.path.isfile(init):
            return init, True
        if rest and os.path.isfile(base + ".py"):
            return base + ".py", False
    return None


def _imports(path: str, name: str, is_package: bool) -> list[str]:
    """Every module name `path` may import: each `import a.b`, each `from a import b` as
    both `a` and `a.b` (`b` may be a submodule), relative ones resolved against `name`."""
    try:
        with open(path, "rb") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError, ValueError):
        return []
    package = name.split(".") if is_package else name.split(".")[:-1]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(package) - (node.level - 1)
                if keep < 1:
                    continue
                base = package[:keep] + ([node.module] if node.module else [])
                module = ".".join(base)
            elif node.module:
                module = node.module
            else:
                continue
            out.append(module)
            out += [f"{module}.{alias.name}" for alias in node.names if alias.name != "*"]
    return out


def _origin(name: str) -> str | None:
    top = name.split(".")[0]
    found = _file(name, _roots(top))
    return found[0] if found else None
