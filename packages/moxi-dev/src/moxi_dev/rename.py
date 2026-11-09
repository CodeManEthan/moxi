"""Apply the repository's identifier rename table and report unresolved old uses."""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

KINDS = frozenset({"name", "kwonly", "use", "keep"})
VENDORED = "packages/moxi/src/moxi/_vendor/"


class RenameError(Exception):
    """The table or tree cannot be read."""


@dataclass(frozen=True)
class Row:
    package: str
    path: str
    scope: str
    kind: str
    old: str
    new: str
    line: int = 0

    def key(self) -> tuple[str, str, str]:
        return self.kind, self.old, self.new

    def __str__(self) -> str:
        return "\t".join((self.package, self.path, self.scope, self.kind, self.old, self.new))


@dataclass(frozen=True, order=True)
class Leftover:
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    text: str


@dataclass
class Source:
    path: str
    text: str
    tree: ast.Module
    module: str
    offsets: list[int]
    scopes: dict[str, ast.AST] = field(default_factory=dict)
    parents: dict[int, ast.AST] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    instances: dict[tuple[str, str], str] = field(default_factory=dict)
    # The qualified name of the scope each node sits in, by node id, and every node in
    # walk order: read once here, so each row doesn't walk every tree again.
    enclosing: dict[int, str] = field(default_factory=dict)
    nodes: list[ast.AST] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    def position(self, line: int, column: int) -> int:
        return self.offsets[line - 1] + column

    def span(self, node: ast.AST) -> tuple[int, int]:
        return (self.position(node.lineno, node.col_offset),
                self.position(node.end_lineno, node.end_col_offset))

    def line_text(self, line: int) -> str:
        return self.lines[line - 1] if 0 < line <= len(self.lines) else ""


def read_table(path: Path) -> list[Row]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RenameError(f"cannot read {path}: {exc}") from exc
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 6:
            raise RenameError(f"{path}:{number}: a row has {len(parts)} fields, not 6")
        row = Row(*parts, line=number)
        if row.kind not in KINDS:
            raise RenameError(f"{path}:{number}: unknown kind {row.kind!r}")
        if row.kind in ("name", "kwonly") and not (row.old.isidentifier()
                                                      and row.new.isidentifier()):
            raise RenameError(f"{path}:{number}: old and new must be identifiers")
        rows.append(row)
    return rows


def _module(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    try:
        at = parts.index("src")
    except ValueError:
        return None
    dotted = list(parts[at + 1:])
    if not dotted or not path.endswith(".py"):
        return None
    dotted[-1] = dotted[-1][:-3]
    if dotted[-1] == "__init__":
        dotted.pop()
    return ".".join(dotted) if dotted and all(p.isidentifier() for p in dotted) else None


def _outside_module(path: str) -> str | None:
    """A name for a file outside a package's `src` (its tests, conftest and scripts),
    which no import reaches: the script reads its uses but none of its definitions is a
    row's."""
    parts = PurePosixPath(path).parts
    if len(parts) < 3 or parts[0] != "packages" or not path.endswith(".py"):
        return None
    dotted = [re.sub(r"\W", "_", part) for part in (*parts[1:-1], parts[-1][:-3])]
    return ".".join(["~outside", *dotted])


def _scope_map(tree: ast.Module) -> tuple[dict[str, ast.AST], dict[int, ast.AST],
                                          dict[int, str]]:
    scopes: dict[str, ast.AST] = {"-": tree}
    parents: dict[int, ast.AST] = {}
    enclosing: dict[int, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
            enclosing[id(child)] = prefix or "-"
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                scopes[qual] = child
                walk(child, qual)
            else:
                walk(child, prefix)

    walk(tree, "")
    return scopes, parents, enclosing


def _offsets(text: str) -> list[int]:
    out, total = [], 0
    for line in text.splitlines(keepends=True):
        out.append(total)
        total += len(line)
    if not out or text.endswith(("\n", "\r")):
        out.append(total)
    return out


def _strings(node: ast.Tuple | ast.List) -> list[str]:
    return [one.value for one in node.elts
            if isinstance(one, ast.Constant) and isinstance(one.value, str)]


def _relative(module: str, level: int, named: str | None, package: bool = False) -> str:
    if level == 0:
        return named or ""
    # A package's `__init__` is the package itself: one dot names it.
    base = module.split(".") if package else module.split(".")[:-1]
    if level:
        base = base[:max(0, len(base) - level + 1)]
    if named:
        base += named.split(".")
    return ".".join(base)


class Project:
    def __init__(self, root: Path, texts: dict[str, str] | None = None,
                 rows: Sequence[Row] = ()):
        self.root = root
        self.sources: dict[str, Source] = {}
        # A `name` row whose old name another row of its scope gives a parameter (a
        # local renamed out of a parameter's way): its old name is the local, never
        # that parameter.
        self.chained = {(row.path, row.scope, row.new) for row in rows if row.kind == "name"}
        if texts is None:
            done = subprocess.run(["git", "ls-files", "-z", "--cached", "--others",
                                   "--exclude-standard", "packages"],
                                  cwd=root, capture_output=True, check=False)
            if done.returncode != 0:
                raise RenameError(done.stderr.decode(errors="replace").strip())
            paths = [p for p in done.stdout.decode("utf-8", "surrogateescape").split("\0")
                     if p]
            texts = {}
            for path in paths:
                if path.endswith(".py") and not path.startswith(VENDORED):
                    try:
                        texts[path] = (root / path).read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        pass
        for path, text in texts.items():
            module = _module(path) or _outside_module(path)
            if module is None:
                continue
            try:
                tree = ast.parse(text, filename=path)
            except SyntaxError:
                continue
            scopes, parents, enclosing = _scope_map(tree)
            self.sources[path] = Source(path, text, tree, module, _offsets(text), scopes,
                                        parents, enclosing=enclosing,
                                        nodes=list(ast.walk(tree)),
                                        lines=text.splitlines())
        self.modules = {source.module: source for source in self.sources.values()}
        for source in self.sources.values():
            self._index(source)
        for source in self.sources.values():
            self._lazy(source)
        self._settle_aliases()
        for source in self.sources.values():
            self._instances(source)

    def _index(self, source: Source) -> None:
        for node in source.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                source.aliases[node.name] = f"sym:{source.module}:{node.name}"
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        source.aliases[target.id] = f"sym:{source.module}:{target.id}"
        # Imports at module level bind first; an import inside a block or a function
        # (an optional or late import) binds a name nothing at module level does.
        top = {id(node) for node in source.tree.body}
        imports = [node for node in source.tree.body
                   if isinstance(node, (ast.Import, ast.ImportFrom))]
        imports += [node for node in source.nodes
                    if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in top]
        for node in imports:
            bind = (source.aliases.__setitem__ if id(node) in top
                    else source.aliases.setdefault)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    module = alias.name if alias.asname else alias.name.split(".")[0]
                    bind(local, ("mod:" if module in self.modules else "ext:") + module)
            else:
                module = _relative(source.module, node.level, node.module,
                                   source.path.endswith("/__init__.py"))
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    local = alias.asname or alias.name
                    if f"{module}.{alias.name}" in self.modules:
                        bind(local, f"mod:{module}.{alias.name}")
                    else:
                        prefix = "sym:" if module in self.modules else "ext:"
                        bind(local, f"{prefix}{module}:{alias.name}")

    def _tables(self, source: Source) -> dict[str, ast.expr]:
        return {target.id: node.value for node in source.tree.body
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
                for target in (node.targets if isinstance(node, ast.Assign)
                               else [node.target]) if isinstance(target, ast.Name)}

    def _table(self, source: Source, node: ast.expr, depth: int = 0) -> dict[str, str]:
        """The string-to-string mapping a module-level expression spells: a dict literal,
        `dict.fromkeys(names, value)`, a comprehension giving each name one value, and
        `**` of another such table in this module or one it imports."""
        out: dict[str, str] = {}
        if depth > 5:
            return out
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is None:
                    out.update(self._table(source, value, depth + 1))
                elif (isinstance(key, ast.Constant) and isinstance(key.value, str)
                      and isinstance(value, ast.Constant) and isinstance(value.value, str)):
                    out[key.value] = value.value
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name) and node.func.value.id == "dict"
              and node.func.attr == "fromkeys" and len(node.args) == 2
              and isinstance(node.args[0], (ast.Tuple, ast.List))
              and isinstance(node.args[1], ast.Constant)
              and isinstance(node.args[1].value, str)):
            out.update(dict.fromkeys(_strings(node.args[0]), node.args[1].value))
        elif (isinstance(node, ast.DictComp) and len(node.generators) == 1
              and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
              and isinstance(node.key, ast.Name)
              and isinstance(node.generators[0].target, ast.Name)
              and node.generators[0].target.id == node.key.id
              and isinstance(node.generators[0].iter, (ast.Tuple, ast.List))):
            out.update(dict.fromkeys(_strings(node.generators[0].iter), node.value.value))
        elif isinstance(node, ast.Name):
            tables = self._tables(source)
            if node.id in tables:
                return self._table(source, tables[node.id], depth + 1)
            bound = source.aliases.get(node.id, "")
            if bound.startswith("sym:"):
                _, module, qual = bound.split(":", 2)
                other = self.modules.get(module)
                if other is not None and qual in self._tables(other):
                    return self._table(other, self._tables(other)[qual], depth + 1)
        return out

    def _lazy(self, source: Source) -> None:
        """Bind the names a module's `__getattr__` hands out from a table of where each
        lives (`{"connect": "_client"}`, `{"git": "moxi_repo.gitrun"}`), so a re-export
        that loads on first touch resolves as an import would. Only a module-level table
        the function reads by name counts."""
        getter = next((node for node in source.tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == "__getattr__"), None)
        if getter is None:
            return
        read = {node.id for node in ast.walk(getter) if isinstance(node, ast.Name)}
        package = (source.module if source.path.endswith("/__init__.py")
                   else source.module.rpartition(".")[0])
        for name, value in self._tables(source).items():
            if name not in read:
                continue
            for served, home in self._table(source, value).items():
                if not served.isidentifier():
                    continue
                if home == "":
                    candidates = [f"{package}.{served}"]
                else:
                    candidates = [home, f"{source.module}.{home}", f"{package}.{home}"]
                module = next((one for one in candidates if one in self.modules), None)
                if module is None:
                    continue
                if module.rpartition(".")[2] == served:
                    source.aliases.setdefault(served, f"mod:{module}")
                else:
                    source.aliases.setdefault(served, f"sym:{module}:{served}")

    def _settle_aliases(self) -> None:
        for _ in range(len(self.modules) + 2):
            changed = False
            for source in self.sources.values():
                for name, value in list(source.aliases.items()):
                    if not value.startswith("sym:"):
                        continue
                    _, module, qual = value.split(":", 2)
                    if "." in qual:
                        continue
                    target = self.modules.get(module)
                    replacement = target.aliases.get(qual) if target else None
                    if replacement and replacement != value:
                        source.aliases[name] = replacement
                        changed = True
            if not changed:
                break

    def resolve(self, source: Source, node: ast.AST, scope: str = "-") -> str | None:
        if isinstance(node, ast.Name):
            if (scope, node.id) in source.instances:
                return source.instances[(scope, node.id)]
            if scope != "-":
                first = scope.split(".")[0]
                class_node = source.scopes.get(first)
                if node.id in ("self", "cls") and isinstance(class_node, ast.ClassDef):
                    return f"sym:{source.module}:{first}"
            return source.aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve(source, node.value, scope)
            if base is None:
                return None
            if base.startswith("mod:"):
                module = base[4:]
                if f"{module}.{node.attr}" in self.modules:
                    return f"mod:{module}.{node.attr}"
                target = self.modules.get(module)
                return target.aliases.get(node.attr, f"sym:{module}:{node.attr}") if target \
                    else f"ext:{module}:{node.attr}"
            if base.startswith("ext:"):
                return base + "." + node.attr
            if base.startswith("sym:"):
                return base + "." + node.attr
        return None

    def _instances(self, source: Source) -> None:
        for scope, node in source.scopes.items():
            for one in ast.walk(node):
                if isinstance(one, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                        and one is not node:
                    continue
                if (isinstance(one, (ast.Assign, ast.AnnAssign))
                        and isinstance(one.value, ast.Call)):
                    targets = one.targets if isinstance(one, ast.Assign) else [one.target]
                    resolved = self.resolve(source, one.value.func, scope)
                    if resolved and resolved.startswith("sym:"):
                        for target in targets:
                            if isinstance(target, ast.Name):
                                source.instances[(scope, target.id)] = resolved


def _args(node: ast.AST) -> list[ast.arg]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    return [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]


def _binding_nodes(source: Source, scope: str, name: str) -> list[ast.AST]:
    node = source.scopes.get(scope)
    if node is None:
        return []
    found: list[ast.AST] = [arg for arg in _args(node) if arg.arg == name]
    body = node.body if hasattr(node, "body") else []
    for statement in body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if statement.name == name:
                found.append(statement)
            continue
        for one in ast.walk(statement):
            if isinstance(one, ast.Name) and isinstance(one.ctx, ast.Store) and one.id == name:
                found.append(one)
            elif (isinstance(one, ast.Attribute) and isinstance(one.ctx, ast.Store)
                  and one.attr == name):
                found.append(one)
            elif (scope == "-" and isinstance(one, ast.alias) and one.asname == name
                  and isinstance(statement, ast.ImportFrom)):
                found.append(one)
    return found


def _parameter(source: Source, scope: str, name: str) -> ast.arg | None:
    return next((arg for arg in _args(source.scopes.get(scope)) if arg.arg == name), None)


def _field(source: Source, scope: str, name: str) -> bool:
    """Whether class `scope` declares `name` as an annotated field in its body, which its
    generated constructor takes by that keyword."""
    node = source.scopes.get(scope)
    return isinstance(node, ast.ClassDef) and any(
        isinstance(one, ast.AnnAssign) and isinstance(one.target, ast.Name)
        and one.target.id == name for one in node.body)


def state(project: Project, row: Row) -> str:
    if row.kind in ("use", "keep"):
        source = project.sources.get(row.path)
        return line_state({row.path: source.text} if source else {}, row)
    source = project.sources.get(row.path)
    if source is None:
        return "not here"
    if row.kind == "kwonly":
        old = _parameter(source, row.scope, row.old)
        new = _parameter(source, row.scope, row.new)
        if old and isinstance(source.scopes.get(row.scope), (ast.FunctionDef,
                                                             ast.AsyncFunctionDef)):
            fn = source.scopes[row.scope]
            return "applied" if old in fn.args.kwonlyargs else "to apply"
        if new and new in source.scopes[row.scope].args.kwonlyargs:
            return "applied"
        return "not here"
    # A parameter's state is its signature's: an attribute of the same name the function
    # stores is another thing.
    chained = (row.path, row.scope, row.old) in project.chained
    if not chained and _parameter(source, row.scope, row.old):
        return "to apply"
    if _parameter(source, row.scope, row.new):
        return "applied"
    old = [node for node in _binding_nodes(source, row.scope, row.old)
           if not (chained and isinstance(node, ast.arg))]
    new = _binding_nodes(source, row.scope, row.new)
    return "to apply" if old else "applied" if new else "not here"


def _name_edit(source: Source, node: ast.AST, old: str, new: str) -> Edit | None:
    if isinstance(node, ast.arg):
        start = source.position(node.lineno, node.col_offset)
        return Edit(start, start + len(old), new)
    if isinstance(node, ast.Name):
        start, end = source.span(node)
        return Edit(start, end, new)
    if isinstance(node, ast.Attribute):
        _start, end = source.span(node)
        return Edit(end - len(old), end, new)
    if isinstance(node, ast.alias):
        start, end = source.span(node)
        matches = list(re.finditer(rf"\b{re.escape(old)}\b", source.text[start:end]))
        return (Edit(start + matches[-1].start(), start + matches[-1].end(), new)
                if matches else None)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        line_start = source.position(node.lineno, 0)
        line_end = source.offsets[node.lineno] if node.lineno < len(source.offsets) else len(source.text)
        match = re.search(rf"\b{re.escape(old)}\b", source.text[line_start:line_end])
        return Edit(line_start + match.start(), line_start + match.end(), new) if match else None
    return None


def _scope_of(source: Source, node: ast.AST) -> str:
    return source.enclosing.get(id(node), "-")


@dataclass(frozen=True)
class Target:
    """What a `name` or `kwonly` row reaches. `symbol` is the definition's canonical
    symbol (the function, for a parameter). `kind` is "parameter", "field" (a class
    body's annotated field), "local" (a function's own variable) or None. A parameter's or field's `callee` is the symbol a
    call reaches to pass it by keyword, and a parameter's `position` its index among the
    positional arguments a call passes; `following` names the positional parameters from
    it on, which a star before it makes keyword-only too."""

    symbol: str
    kind: str | None = None
    position: int | None = None
    callee: str | None = None
    following: tuple[str, ...] = ()


def _target(project: Project, row: Row) -> Target:
    source = project.sources.get(row.path)
    if source is None:
        return Target("")
    # After the rename the definition binds the new name; leftovers read that tree.
    chained = (row.path, row.scope, row.old) in project.chained
    parameter = ((None if chained else _parameter(source, row.scope, row.old))
                 or _parameter(source, row.scope, row.new))
    if parameter is not None:
        function = f"sym:{source.module}:{row.scope}"
        fn = source.scopes[row.scope]
        positional = [*fn.args.posonlyargs, *fn.args.args]
        skip = 1 if positional and positional[0].arg in ("self", "cls") else 0
        if parameter in positional:
            index = positional.index(parameter)
            following = tuple(arg.arg for arg in positional[index:])
        else:
            index, following = len(positional), ()
        return Target(function, "parameter", index - skip,
                      function.removesuffix(".__init__"), following)
    if _field(source, row.scope, row.old) or _field(source, row.scope, row.new):
        owner = f"sym:{source.module}:{row.scope}"
        return Target(f"{owner}.{row.old}", "field", callee=owner)
    qual = row.old if row.scope == "-" else f"{row.scope}.{row.old}"
    if isinstance(source.scopes.get(row.scope), (ast.FunctionDef, ast.AsyncFunctionDef)):
        return Target(f"sym:{source.module}:{qual}", "local")
    return Target(_canonical(project, f"sym:{source.module}:{qual}") or "")


def _canonical(project: Project, value: str | None) -> str | None:
    if not value or not value.startswith("sym:"):
        return value
    _, module, qual = value.split(":", 2)
    if "." in qual:
        return value
    source = project.modules.get(module)
    return source.aliases.get(qual, value) if source else value


def _known(project: Project, value: str | None) -> bool:
    """Whether a resolved symbol names something: a module, an outside name, or a
    function, class, module name or class attribute defined in the tree. An attribute
    chain through an instance attribute resolves to a symbol no definition has."""
    if value is None:
        return False
    if not value.startswith("sym:"):
        return True
    _, module, qual = value.split(":", 2)
    source = project.modules.get(module)
    if source is None:
        return False
    if qual in source.scopes or qual in source.aliases:
        return True
    head, _, attr = qual.rpartition(".")
    return bool(head) and head in source.scopes and bool(_binding_nodes(source, head, attr))


def _add(edits: dict[str, list[Edit]], path: str, edit: Edit | None) -> None:
    if edit is not None and edit.start != edit.end or edit is not None and edit.text:
        edits.setdefault(path, []).append(edit)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _names_all(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Name) and node.id == "__all__"


def _in_all(source: Source, node: ast.AST) -> bool:
    """Whether a string sits in a list, tuple or set given to `__all__` (assigned,
    added to, or passed to its `extend`)."""
    parent = source.parents.get(id(node))
    if not isinstance(parent, (ast.List, ast.Tuple, ast.Set)):
        return False
    holder = source.parents.get(id(parent))
    while isinstance(holder, ast.BinOp):
        holder = source.parents.get(id(holder))
    if isinstance(holder, ast.Assign):
        return any(_names_all(target) for target in holder.targets)
    if isinstance(holder, (ast.AnnAssign, ast.AugAssign)):
        return _names_all(holder.target)
    if isinstance(holder, ast.Call) and isinstance(holder.func, ast.Attribute):
        return _names_all(holder.func.value)
    return False


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.ListComp,
           ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _names(node: ast.AST, name: str, *, stored: bool = False) -> list[ast.AST]:
    """The nodes under `node` that bind `name` as a bare name (an argument, an assigned
    or loop name, a def or class, an import, an except or match name, a global or
    nonlocal), and, unless `stored`, those that read it. `node`'s own name is not one:
    the scope around it binds that."""
    found = []
    for one in ast.walk(node):
        if isinstance(one, ast.Name) and one.id == name:
            if not stored or not isinstance(one.ctx, ast.Load):
                found.append(one)
        elif isinstance(one, ast.arg) and one.arg == name:
            found.append(one)
        elif (isinstance(one, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
              and one.name == name and one is not node):
            found.append(one)
        elif isinstance(one, ast.alias) and (one.asname or one.name.split(".")[0]) == name:
            found.append(one)
        elif isinstance(one, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) \
                and one.name == name:
            found.append(one)
        elif isinstance(one, (ast.Global, ast.Nonlocal)) and name in one.names:
            found.append(one)
    return found


def _inner_scopes(node: ast.AST) -> list[ast.AST]:
    return [one for one in ast.walk(node) if isinstance(one, _SCOPES) and one is not node]


def _line(node: ast.AST) -> int:
    return getattr(node, "lineno", 0)


def refusals(project: Project, rows: Sequence[Row], states: Sequence[str]) -> list[str]:
    """Each `name` row to apply whose edit would change what a token means: its new name
    already bound or read in the scope it edits; a nested function, lambda, comprehension
    or method there with its own binding of the old name (a field's old name read bare
    in a method is never the field); a module-level row's old name bound inside a
    function of a module the edit reaches. A binding another row of the same scope
    renames away is no collision. Each names the row's table line and the code line."""
    active = [row for row, status in zip(rows, states)
              if status == "to apply" and row.kind == "name"]
    leaving = {(row.path, row.scope, row.old) for row in active}
    found = []

    def refuse(row: Row, source: Source, node: ast.AST, why: str) -> None:
        found.append(f"row {row.line} ({row.path} {row.scope} {row.old} -> {row.new}): "
                     f"{source.path}:{_line(node)}: {why}")

    for row in active:
        source = project.sources[row.path]
        clear = (row.path, row.scope, row.new) in leaving
        if row.scope != "-":
            node = source.scopes[row.scope]
            hits = [] if clear else _names(node, row.new)
            if isinstance(node, ast.ClassDef) and not clear:
                hits += [one for one in ast.walk(node) if isinstance(one, ast.Attribute)
                         and one.attr == row.new and isinstance(one.value, ast.Name)
                         and one.value.id in ("self", "cls")]
            if hits:
                first = min(hits, key=_line)
                refuse(row, source, first, f"{row.new!r} is already bound or read in "
                                           f"{row.scope}")
                continue
            for inner in _inner_scopes(node):
                own = (_names(inner, row.old) if isinstance(node, ast.ClassDef)
                       and isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                       else _names(inner, row.old, stored=True))
                if own:
                    refuse(row, source, min(own, key=_line),
                           f"a scope inside {row.scope} has its own {row.old!r}")
                    break
            continue
        target = _canonical(project, f"sym:{source.module}:{row.old}")
        reached = [source] + [other for other in project.sources.values()
                              if other is not source and any(
                                  value == target or _canonical(project, value) == target
                                  for name, value in other.aliases.items()
                                  if name == row.old)]
        for other in reached:
            hits = [] if clear else _names(other.tree, row.new)
            if hits:
                refuse(row, other, min(hits, key=_line),
                       f"{row.new!r} is already bound or read in {other.module}")
                break
            # A function's own import of the definition binds the definition itself.
            imported = {id(alias) for node in other.nodes if isinstance(node, ast.ImportFrom)
                        for alias in node.names if _canonical(project, "sym:{}:{}".format(
                            _relative(other.module, node.level, node.module,
                                      other.path.endswith("/__init__.py")),
                            alias.name)) == target}
            local = [one for inner in _inner_scopes(other.tree)
                     if not isinstance(inner, ast.ClassDef)
                     for one in _names(inner, row.old, stored=True)
                     if id(one) not in imported]
            if local:
                refuse(row, other, min(local, key=_line),
                       f"{row.old!r} is also a local of a function there")
                break
    return found


def plan(project: Project, rows: Sequence[Row], states: Sequence[str]) -> dict[str, list[Edit]]:
    edits: dict[str, list[Edit]] = {}
    active = [(row, _target(project, row)) for row, status in zip(rows, states)
              if status == "to apply" and row.kind in ("name", "kwonly")]
    touched: dict[int, set[str]] = {}

    def put(row: Row, path: str, edit: Edit | None) -> None:
        if edit is not None:
            touched.setdefault(id(row), set()).add(path)
        _add(edits, path, edit)

    for row, target in active:
        source = project.sources[row.path]
        if row.kind == "name":
            for node in _binding_nodes(source, row.scope, row.old):
                if target.kind in ("parameter", "local") and isinstance(node, ast.Attribute):
                    continue
                put(row, row.path, _name_edit(source, node, row.old, row.new))
            if row.scope != "-":
                scope_node = source.scopes[row.scope]
                for node in ast.walk(scope_node):
                    if isinstance(node, ast.Name) and node.id == row.old:
                        put(row, row.path, _name_edit(source, node, row.old, row.new))
                    elif (target.kind not in ("parameter", "local")
                          and isinstance(node, ast.Attribute) and node.attr == row.old and isinstance(node.value, ast.Name)
                          and node.value.id in ("self", "cls")):
                        put(row, row.path, _name_edit(source, node, row.old, row.new))
        else:
            parameter = _parameter(source, row.scope, row.old)
            if parameter is not None:
                start = source.position(parameter.lineno, parameter.col_offset)
                # A star before this parameter makes it and every following parameter
                # keyword-only. Rows are expected to name the first such parameter.
                put(row, row.path, Edit(start, start, "*, "))
                fn = source.scopes[row.scope]
                if fn.args.vararg is None and fn.args.kwonlyargs:
                    # The bare star that already stood before the keyword-only
                    # parameters goes: there is one star, now earlier.
                    positional = [*fn.args.posonlyargs, *fn.args.args]
                    ends = [source.span(positional[-1])[1]]
                    if fn.args.defaults:
                        ends.append(source.span(fn.args.defaults[-1])[1])
                    after = max(ends)
                    first = fn.args.kwonlyargs[0]
                    before = source.position(first.lineno, first.col_offset)
                    star = re.search(r"\*\s*,\s*", source.text[after:before])
                    if star:
                        put(row, row.path,
                             Edit(after + star.start(), after + star.end(), ""))

    by_old: dict[str, list[tuple[Row, Target]]] = {}
    for row, target in active:
        by_old.setdefault(row.old, []).append((row, target))
    callers = [(row, target) for row, target in active if target.callee]
    for other in project.sources.values():
        for node in other.nodes:
            if isinstance(node, ast.ImportFrom):
                module = _relative(other.module, node.level, node.module,
                                   other.path.endswith("/__init__.py"))
                for alias in node.names:
                    for row, target in by_old.get(alias.name, ()):
                        if _canonical(project, f"sym:{module}:{alias.name}") == target.symbol:
                            start = other.position(alias.lineno, alias.col_offset)
                            put(row, other.path, Edit(start, start + len(row.old), row.new))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                for row, target in by_old.get(node.id, ()):
                    scope = _scope_of(other, node)
                    if _canonical(project, project.resolve(other, node, scope)) == target.symbol:
                        put(row, other.path, _name_edit(other, node, row.old, row.new))
            elif isinstance(node, ast.Attribute):
                for row, target in by_old.get(node.attr, ()):
                    scope = _scope_of(other, node)
                    if _canonical(project, project.resolve(other, node, scope)) == target.symbol:
                        put(row, other.path, _name_edit(other, node, row.old, row.new))
            elif isinstance(node, ast.Call) and callers:
                passed = {keyword.arg for keyword in node.keywords}
                wanted = [(row, target) for row, target in callers
                          if row.old in passed or row.kind == "kwonly"]
                if not wanted:
                    continue
                callee = _canonical(project, project.resolve(other, node.func,
                                                             _scope_of(other, node)))
                for row, target in wanted:
                    if callee != target.callee:
                        continue
                    for keyword in node.keywords:
                        if keyword.arg == row.old:
                            start = other.position(keyword.lineno, keyword.col_offset)
                            put(row, other.path, Edit(start, start + len(row.old), row.new))
                    if (row.kind == "kwonly" and target.position is not None
                            and len(node.args) > target.position):
                        # Every positional argument from this one on is now keyword-only.
                        for offset, argument in enumerate(node.args[target.position:]):
                            if (isinstance(argument, ast.Starred)
                                    or offset >= len(target.following)):
                                break
                            keyword = row.new if offset == 0 else target.following[offset]
                            start = other.position(argument.lineno, argument.col_offset)
                            put(row, other.path, Edit(start, start, f"{keyword}="))

    for row, target in active:
        if row.kind != "name" or target.kind is not None:
            continue
        source = project.sources[row.path]
        for other in project.sources.values():
            if other.module != source.module and not any(
                    _canonical(project, value) == target.symbol
                    for value in other.aliases.values()):
                continue
            for node in other.nodes:
                if isinstance(node, ast.Constant) and node.value == row.old:
                    parent = other.parents.get(id(node))
                    in_getattr = any(scope.endswith("__getattr__") and node in ast.walk(one)
                                     for scope, one in other.scopes.items())
                    in_all = _in_all(other, node)
                    if in_getattr or in_all:
                        start, end = other.span(node)
                        literal = other.text[start:end]
                        put(row, other.path,
                             Edit(start, end, literal.replace(row.old, row.new)))

    # A backticked cite of the old name is renamed where it means this definition: a
    # parameter's or a local's in its function, a field's in its class, any other name's
    # in the files the row edited. Every other cite is a leftover; a cite on a line a `keep` row names
    # is another thing's and stays.
    kept = {(row.path, row.scope) for row in rows if row.kind == "keep"}
    for row, target in active:
        if row.kind != "name":
            continue
        source = project.sources[row.path]
        if target.kind in ("parameter", "field", "local"):
            node = source.scopes[row.scope]
            regions = [(source, source.position(node.lineno, 0),
                        source.position(node.end_lineno, node.end_col_offset))]
        else:
            regions = [(project.sources[path], 0, len(project.sources[path].text))
                       for path in sorted(touched.get(id(row), ()))]
        pattern = re.compile(rf"`{re.escape(row.old)}(?:\(\))?`")
        for other, start, end in regions:
            for match in pattern.finditer(other.text, start, end):
                line = other.text.count("\n", 0, match.start()) + 1
                if (other.path, other.line_text(line).strip()) in kept:
                    continue
                _add(edits, other.path,
                     Edit(match.start() + 1, match.start() + 1 + len(row.old), row.new))

    return edits


def _used(scope: str, rows: Sequence[Row]) -> str:
    for row in rows:
        scope = _use_pattern(row.old).sub(row.new, scope)
    return scope


def line_state(texts: dict[str, str], row: Row, rows: Sequence[Row] = ()) -> str:
    """A `use` or `keep` row's state, read from the text after the `name` and `kwonly`
    rows' edits: its line is the line as those leave it, so a line that also holds a
    resolved use of another row is named once, the same on every tree."""
    if row.scope.startswith("removed:"):
        return "applied"
    text = texts.get(row.path)
    if text is None:
        return "not here"
    lines = {line.strip() for line in text.splitlines()}
    # Every `use` row naming this line changes it at once.
    siblings = [one for one in rows if one.kind == "use" and one.path == row.path
                and one.scope == row.scope]
    if row.kind == "keep":
        # A keep row's line may also carry a `use` row: it is here as those leave it.
        here = row.scope in lines or (siblings and _used(row.scope, siblings) in lines)
        return "applied" if here else "not here"
    if row.scope in lines:
        return "to apply"
    return "applied" if _used(row.scope, siblings or [row]) in lines else "not here"


def _use_pattern(old: str) -> re.Pattern[str]:
    """A `use` row's old text, matched whole: an end that is a word character must not
    run on into another (`timeout` is not `timeout_s`); old text longer than one name,
    such as `clock.age_seconds` or `timeout=`, picks the token where a line holds the
    name twice with two meanings."""
    start = r"\b" if re.match(r"\w", old) else ""
    end = r"\b" if re.search(r"\w$", old) else ""
    return re.compile(start + re.escape(old) + end)


def apply_uses(texts: dict[str, str], rows: Sequence[Row], states: Sequence[str]) -> set[str]:
    """Rename every match of a `use` row's old text on each line it names; the paths
    changed."""
    changed: set[str] = set()
    named: dict[tuple[str, str], list[Row]] = {}
    for row, status in zip(rows, states):
        if row.kind == "use" and status == "to apply":
            named.setdefault((row.path, row.scope), []).append(row)
    for (path, scope), group in named.items():
        lines = texts[path].splitlines(keepends=True)
        for number, line in enumerate(lines):
            if line.strip() == scope:
                lines[number] = _used(line, group)
        texts[path] = "".join(lines)
        changed.add(path)
    return changed


def _apply(text: str, edits: Iterable[Edit]) -> str:
    unique = {(edit.start, edit.end, edit.text): edit for edit in edits}
    ordered = sorted(unique.values(), key=lambda edit: (edit.start, edit.end), reverse=True)
    last = len(text) + 1
    for edit in ordered:
        if edit.end > last:
            continue
        text = text[:edit.start] + edit.text + text[edit.end:]
        last = edit.start
    return text


def transformed(project: Project, edits: dict[str, list[Edit]], rows: Sequence[Row]) -> dict[str, str]:
    texts = {path: source.text for path, source in project.sources.items()}
    for path, changes in edits.items():
        texts[path] = _apply(texts[path], changes)
    return texts


def leftovers(project: Project, rows: Sequence[Row]) -> list[Leftover]:
    found: set[Leftover] = set()
    covered = {(row.path, row.scope) for row in rows if row.kind in ("use", "keep")}
    # A `use` row covers its line as it leaves it too, so a run after it lists what the
    # run before it did.
    uses: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        if row.kind == "use":
            uses.setdefault((row.path, row.scope), []).append(row)
    covered |= {(path, _used(scope, group)) for (path, scope), group in uses.items()}
    targets = [(row, _target(project, row)) for row in rows if row.kind in ("name", "kwonly")]
    by_old: dict[str, list[tuple[Row, Target]]] = {}
    for row, target in targets:
        by_old.setdefault(row.old, []).append((row, target))
    kwonly = []
    for row, target in targets:
        if row.kind == "kwonly":
            function_name = row.scope.rsplit(".", 1)[-1]
            if row.scope.endswith(".__init__"):
                function_name = row.scope.rsplit(".", 2)[-2]
            kwonly.append((row, target, function_name))
    # A parameter's or a local's name means something only in its function, so a cite of
    # it is looked for in its own file; any other old name is looked for everywhere.
    inner = ("parameter", "local")
    everywhere = {row.old for row, target in targets if target.kind not in inner}
    own: dict[str, set[str]] = {}
    for row, target in targets:
        if target.kind in inner and row.old != row.new:
            own.setdefault(row.path, set()).add(row.old)
    for source in project.sources.values():
        def is_covered(line: int) -> bool:
            return (source.path, source.line_text(line).strip()) in covered

        for node in source.nodes:
            if isinstance(node, ast.Call):
                name = _call_name(node)
                passed = [keyword for keyword in node.keywords if any(
                    target.kind != "local" for _row, target in by_old.get(keyword.arg, ()))]
                # A call never reaches a parameter outside its function.
                named = [(row, target) for row, target in by_old.get(name, ())
                         if row.kind == "name" and target.kind not in inner] if name else []
                # A star at or before the position may reach it however few arguments
                # the call spells.
                positional = [(row, target, function_name)
                              for row, target, function_name in kwonly
                              if name == function_name and target.position is not None
                              and (len(node.args) > target.position or any(
                                  isinstance(argument, ast.Starred)
                                  for argument in node.args[:target.position + 1]))]
                # A row covers a keyword on its own line or on the call's first line.
                passed = [keyword for keyword in passed if not is_covered(node.lineno)
                          and not is_covered(keyword.value.lineno)]
                if not (passed or named or positional) or is_covered(node.lineno):
                    continue
                canonical = _canonical(project, project.resolve(source, node.func,
                                                                _scope_of(source, node)))
                unresolved = not _known(project, canonical)
                if not unresolved:
                    # A star at or before the position may still pass it.
                    for row, target, function_name in positional:
                        if canonical == target.callee and any(
                                isinstance(argument, ast.Starred)
                                for argument in node.args[:target.position + 1]):
                            found.add(Leftover(source.path, node.lineno,
                                               f"{function_name} call passes a starred "
                                               f"argument that may reach keyword-only "
                                               f"{row.new!r}"))
                    continue
                for keyword in passed:
                    found.add(Leftover(source.path, keyword.value.lineno,
                                       f"unresolved call still passes keyword {keyword.arg!r}"))
                for row, _row_target, function_name in positional:
                    found.add(Leftover(source.path, node.lineno,
                                       f"unresolved {function_name} call still passes "
                                       f"parameter {row.old!r} by position"))
                if named:
                    found.add(Leftover(source.path, node.lineno,
                                       f"unresolved call still uses {name!r}"))
            elif isinstance(node, ast.Attribute):
                # A parameter or a local is never an attribute; any other old name can be.
                if not any(target.kind not in inner
                           for _row, target in by_old.get(node.attr, ())):
                    continue
                if is_covered(node.lineno):
                    continue
                resolved = project.resolve(source, node, _scope_of(source, node))
                if not _known(project, _canonical(project, resolved)):
                    found.add(Leftover(source.path, node.lineno,
                                       f"unresolved attribute still uses {node.attr!r}"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if not any(target.kind not in inner
                           for _row, target in by_old.get(node.value, ())):
                    continue
                if not is_covered(node.lineno):
                    found.add(Leftover(source.path, node.lineno,
                                       f"string lookup still names {node.value!r}"))
        cited = everywhere | own.get(source.path, set())
        if not cited or "`" not in source.text:
            continue
        cite = re.compile(r"`(" + "|".join(re.escape(old) for old in
                                           sorted(cited, key=len, reverse=True))
                          + r")(?:\(\))?`")
        for token in tokenize.generate_tokens(io.StringIO(source.text).readline):
            if token.type not in (tokenize.COMMENT, tokenize.STRING):
                continue
            for match in cite.finditer(token.string):
                line = token.start[0] + token.string.count("\n", 0, match.start())
                if not is_covered(line):
                    found.add(Leftover(source.path, line,
                                       f"comment still cites {match.group(1)!r}"))
    return sorted(found)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          check=False)


def _ref_project(root: Path, ref: str, rows: Sequence[Row]) -> Project:
    listed = _git(root, "ls-tree", "-r", "--name-only", ref, "--", "packages")
    if listed.returncode:
        raise RenameError(listed.stderr.strip())
    texts = {}
    for path in listed.stdout.splitlines():
        if not path.endswith(".py") or path.startswith(VENDORED):
            continue
        shown = _git(root, "show", f"{ref}:{path}")
        if shown.returncode == 0:
            texts[path] = shown.stdout
    return Project(root, texts, rows=rows)


def main_leftovers(root: Path, project: Project, rows: Sequence[Row], states: Sequence[str],
                   ref: str | None) -> tuple[list[Leftover], str]:
    if ref is None:
        return [], "comparison with main did not run; pass --main REF"
    ancestor = _git(root, "merge-base", "--is-ancestor", ref, "HEAD")
    if ancestor.returncode != 0:
        return [], f"comparison with main did not run; {ref} is not an ancestor of HEAD"
    main = _ref_project(root, ref, rows)
    found = []
    for row, status in zip(rows, states):
        if status != "not here" or row.kind not in ("name", "kwonly"):
            continue
        if state(main, row) != "applied":
            continue
        clearing = False
        for other in rows:
            if other is row or other.key() != row.key() or other.scope != row.scope:
                continue
            if state(project, other) in ("to apply", "applied"):
                clearing = True
        if clearing:
            continue
        candidates = []
        for source in project.sources.values():
            if source.module == _module(row.path) and _binding_nodes(source, row.scope, row.old):
                candidates = [source]
                break
            if _binding_nodes(source, row.scope, row.old):
                candidates.append(source)
        if len(candidates) == 1:
            found.append(Leftover(candidates[0].path, 0,
                                  f"main's row moved here and still binds {row.old!r}"))
        elif len(candidates) > 1:
            names = ", ".join(source.path for source in candidates)
            found.append(Leftover(row.path, 0,
                                  f"main's row has ambiguous moved scopes binding {row.old!r}: {names}"))
    return found, f"compared not-here rows with {ref}"


def run(root: Path, table: Path, *, dry_run: bool = False, main_ref: str | None = None) -> int:
    rows = read_table(table)
    project = Project(root, rows=rows)
    states = [state(project, row) for row in rows]
    refused = refusals(project, rows, states)
    if refused:
        for one in refused:
            print(f"moxi-dev rename: refused: {table.name} {one}")
        print(f"moxi-dev rename: {len(refused)} rows refused; nothing was written")
        return 2
    edits = plan(project, rows, states)
    texts = transformed(project, edits, rows)
    states = [line_state(texts, row, rows) if row.kind in ("use", "keep") else status
              for row, status in zip(rows, states)]
    written = set(edits) | apply_uses(texts, rows, states)
    for path in sorted(written):
        try:
            ast.parse(texts[path], filename=path)
        except SyntaxError as exc:
            raise RenameError(f"{path}:{exc.lineno}: the renamed file would not parse: "
                              f"{exc.msg}; nothing was written") from None
    after = Project(root, texts, rows=rows)
    remaining = leftovers(after, rows)
    moved, comparison = main_leftovers(root, after, rows, states, main_ref)
    remaining = sorted(set(remaining + moved))
    counts = {word: states.count(word) for word in ("to apply", "applied", "not here")}
    print("moxi-dev rename: " + ", ".join(f"{counts[word]} {word}" for word in counts))
    print(f"moxi-dev rename: {comparison}")
    for leftover in remaining:
        print(f"moxi-dev rename: leftover: {leftover}")
    print(f"moxi-dev rename: {len(remaining)} leftovers")
    if not dry_run:
        for path in sorted(written):
            (root / path).write_text(texts[path], encoding="utf-8")
    return 1 if remaining else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moxi-dev rename",
        description="Apply identifier renames from one table and list unresolved old uses.")
    checkout = Path(__file__).resolve().parents[2] / "renames.tsv"
    default = checkout if checkout.is_file() else Path(__file__).with_name("renames.tsv")
    parser.add_argument("--table", type=Path, default=default, metavar="PATH")
    parser.add_argument("--dry-run", action="store_true",
                        help="show states and leftovers without editing files")
    parser.add_argument("--main", dest="main_ref", metavar="REF",
                        help="compare not-here rows with this merged main ref")
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd(), metavar="ROOT")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = Path(_git(args.root, "rev-parse", "--show-toplevel").stdout.strip())
        if not root.is_dir():
            raise RenameError(f"{args.root} is not in a git checkout")
        return run(root, args.table, dry_run=args.dry_run, main_ref=args.main_ref)
    except RenameError as exc:
        print(f"moxi-dev rename: {exc}", file=sys.stderr)
        return 2


__all__ = ["KINDS", "Leftover", "RenameError", "Row", "main", "read_table", "refusals", "run"]


if __name__ == "__main__":
    sys.exit(main())
