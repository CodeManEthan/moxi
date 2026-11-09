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

    def position(self, line: int, column: int) -> int:
        return self.offsets[line - 1] + column

    def span(self, node: ast.AST) -> tuple[int, int]:
        return (self.position(node.lineno, node.col_offset),
                self.position(node.end_lineno, node.end_col_offset))

    def line_text(self, line: int) -> str:
        return self.text.splitlines()[line - 1] if line > 0 else ""


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


def _scope_map(tree: ast.Module) -> tuple[dict[str, ast.AST], dict[int, ast.AST]]:
    scopes: dict[str, ast.AST] = {"-": tree}
    parents: dict[int, ast.AST] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                scopes[qual] = child
                walk(child, qual)
            else:
                walk(child, prefix)

    walk(tree, "")
    return scopes, parents


def _offsets(text: str) -> list[int]:
    out, total = [], 0
    for line in text.splitlines(keepends=True):
        out.append(total)
        total += len(line)
    if not out or text.endswith(("\n", "\r")):
        out.append(total)
    return out


def _relative(module: str, level: int, named: str | None) -> str:
    if level == 0:
        return named or ""
    base = module.split(".")[:-1]
    if level:
        base = base[:max(0, len(base) - level + 1)]
    if named:
        base += named.split(".")
    return ".".join(base)


class Project:
    def __init__(self, root: Path, texts: dict[str, str] | None = None):
        self.root = root
        self.sources: dict[str, Source] = {}
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
            module = _module(path)
            if module is None:
                continue
            try:
                tree = ast.parse(text, filename=path)
            except SyntaxError:
                continue
            scopes, parents = _scope_map(tree)
            self.sources[path] = Source(path, text, tree, module, _offsets(text), scopes,
                                        parents)
        self.modules = {source.module: source for source in self.sources.values()}
        for source in self.sources.values():
            self._index(source)
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
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    module = alias.name if alias.asname else alias.name.split(".")[0]
                    source.aliases[local] = (("mod:" if module in self.modules else "ext:")
                                             + module)
            elif isinstance(node, ast.ImportFrom):
                module = _relative(source.module, node.level, node.module)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    local = alias.asname or alias.name
                    prefix = "sym:" if module in self.modules else "ext:"
                    source.aliases[local] = f"{prefix}{module}:{alias.name}"

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
    return found


def _parameter(source: Source, scope: str, name: str) -> ast.arg | None:
    return next((arg for arg in _args(source.scopes.get(scope)) if arg.arg == name), None)


def state(project: Project, row: Row) -> str:
    if row.kind in ("use", "keep"):
        if row.scope.startswith("removed:"):
            return "applied"
        source = project.sources.get(row.path)
        if source is None:
            return "not here"
        lines = [line.strip() for line in source.text.splitlines()]
        if row.kind == "keep" and row.scope in lines:
            return "applied"
        if row.kind == "use":
            if row.scope in lines:
                return "to apply"
            changed = re.sub(rf"\b{re.escape(row.old)}\b", row.new, row.scope, count=1)
            if changed in lines:
                return "applied"
        return "not here"
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
    old = _binding_nodes(source, row.scope, row.old)
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
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        line_start = source.position(node.lineno, 0)
        line_end = source.offsets[node.lineno] if node.lineno < len(source.offsets) else len(source.text)
        match = re.search(rf"\b{re.escape(old)}\b", source.text[line_start:line_end])
        return Edit(line_start + match.start(), line_start + match.end(), new) if match else None
    return None


def _scope_of(source: Source, node: ast.AST) -> str:
    current = node
    while id(current) in source.parents:
        current = source.parents[id(current)]
        for qual, scope in source.scopes.items():
            if scope is current:
                return qual
    return "-"


def _target(project: Project, row: Row) -> tuple[str, str | None, int | None]:
    source = project.sources.get(row.path)
    if source is None:
        return "", None, None
    function = f"sym:{source.module}:{row.scope}" if row.scope != "-" else None
    parameter = _parameter(source, row.scope, row.old)
    if parameter is not None:
        fn = source.scopes[row.scope]
        positional = [*fn.args.posonlyargs, *fn.args.args]
        skip = 1 if positional and positional[0].arg in ("self", "cls") else 0
        index = ((positional.index(parameter) if parameter in positional else len(positional))
                 - skip)
        return function or "", "parameter", index
    qual = row.old if row.scope == "-" else f"{row.scope}.{row.old}"
    return f"sym:{source.module}:{qual}", None, None


def _canonical(project: Project, value: str | None) -> str | None:
    if not value or not value.startswith("sym:"):
        return value
    _, module, qual = value.split(":", 2)
    if "." in qual:
        return value
    source = project.modules.get(module)
    return source.aliases.get(qual, value) if source else value


def _add(edits: dict[str, list[Edit]], path: str, edit: Edit | None) -> None:
    if edit is not None and edit.start != edit.end or edit is not None and edit.text:
        edits.setdefault(path, []).append(edit)


def plan(project: Project, rows: Sequence[Row], states: Sequence[str]) -> dict[str, list[Edit]]:
    edits: dict[str, list[Edit]] = {}
    active = [(row, _target(project, row)) for row, status in zip(rows, states)
              if status == "to apply" and row.kind in ("name", "kwonly")]
    for row, (target, parameter_kind, position) in active:
        source = project.sources[row.path]
        if row.kind == "name":
            for node in _binding_nodes(source, row.scope, row.old):
                _add(edits, row.path, _name_edit(source, node, row.old, row.new))
            if row.scope != "-":
                scope_node = source.scopes[row.scope]
                for node in ast.walk(scope_node):
                    if isinstance(node, ast.Name) and node.id == row.old:
                        _add(edits, row.path, _name_edit(source, node, row.old, row.new))
                    elif (isinstance(node, ast.Attribute) and node.attr == row.old
                          and isinstance(node.value, ast.Name)
                          and node.value.id in ("self", "cls")):
                        _add(edits, row.path, _name_edit(source, node, row.old, row.new))
        else:
            parameter = _parameter(source, row.scope, row.old)
            if parameter is not None:
                start = source.position(parameter.lineno, parameter.col_offset)
                # A star before this parameter makes it and every following parameter
                # keyword-only. Rows are expected to name the first such parameter.
                _add(edits, row.path, Edit(start, start, "*, "))

        for other in project.sources.values():
            for node in ast.walk(other.tree):
                scope = _scope_of(other, node)
                if isinstance(node, ast.ImportFrom):
                    module = _relative(other.module, node.level, node.module)
                    for alias in node.names:
                        value = f"sym:{module}:{alias.name}"
                        if _canonical(project, value) == target and alias.name == row.old:
                            start = other.position(alias.lineno, alias.col_offset)
                            _add(edits, other.path, Edit(start, start + len(row.old), row.new))
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    if _canonical(project, project.resolve(other, node, scope)) == target \
                            and node.id == row.old:
                        _add(edits, other.path, _name_edit(other, node, row.old, row.new))
                elif isinstance(node, ast.Attribute) and node.attr == row.old:
                    if _canonical(project, project.resolve(other, node, scope)) == target:
                        _add(edits, other.path, _name_edit(other, node, row.old, row.new))
                elif isinstance(node, ast.Call):
                    callee = _canonical(project, project.resolve(other, node.func, scope))
                    expected = target
                    if parameter_kind and target.endswith(".__init__"):
                        expected = target.removesuffix(".__init__")
                    if parameter_kind and callee == expected:
                        for keyword in node.keywords:
                            if keyword.arg == row.old:
                                start = other.position(keyword.value.lineno,
                                                       keyword.value.col_offset) - len(row.old) - 1
                                _add(edits, other.path,
                                     Edit(start, start + len(row.old), row.new))
                        if row.kind == "kwonly" and position is not None and len(node.args) > position:
                            argument = node.args[position]
                            start = other.position(argument.lineno, argument.col_offset)
                            _add(edits, other.path, Edit(start, start, f"{row.new}="))

            if row.kind == "name" and parameter_kind is None:
                aliases_target = any(_canonical(project, value) == target
                                     for value in other.aliases.values())
                if other.module == source.module or aliases_target:
                    for node in ast.walk(other.tree):
                        if isinstance(node, ast.Constant) and node.value == row.old:
                            parent = other.parents.get(id(node))
                            in_getattr = any(scope.endswith("__getattr__") and node in ast.walk(one)
                                             for scope, one in other.scopes.items())
                            in_all = isinstance(parent, (ast.List, ast.Tuple, ast.Set))
                            if in_getattr or in_all:
                                start, end = other.span(node)
                                literal = other.text[start:end]
                                _add(edits, other.path,
                                     Edit(start, end, literal.replace(row.old, row.new)))

    for row, status in zip(rows, states):
        if row.kind != "use" or status != "to apply":
            continue
        source = project.sources[row.path]
        for number, line in enumerate(source.text.splitlines(), 1):
            if line.strip() != row.scope:
                continue
            base = source.position(number, 0)
            for match in re.finditer(rf"\b{re.escape(row.old)}\b", line):
                _add(edits, row.path, Edit(base + match.start(), base + match.end(), row.new))
                break
    return edits


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


def _with_cites(text: str, pairs: Iterable[tuple[str, str]]) -> str:
    for old, new in pairs:
        text = re.sub(rf"`{re.escape(old)}(?P<call>\(\))?`",
                      lambda match: f"`{new}{match.group('call') or ''}`", text)
    return text


def transformed(project: Project, edits: dict[str, list[Edit]], rows: Sequence[Row]) -> dict[str, str]:
    texts = {path: source.text for path, source in project.sources.items()}
    pairs = [(row.old, row.new) for row in rows if row.kind in ("name", "kwonly")]
    for path, changes in edits.items():
        texts[path] = _with_cites(_apply(texts[path], changes), pairs)
    return texts


def _covered(rows: Sequence[Row], source: Source, line: int) -> bool:
    text = source.line_text(line).strip()
    return any(row.kind in ("use", "keep") and row.path == source.path
               and row.scope == text for row in rows)


def leftovers(project: Project, rows: Sequence[Row]) -> list[Leftover]:
    found: set[Leftover] = set()
    for row in rows:
        if row.kind not in ("name", "kwonly"):
            continue
        target, parameter_kind, position = _target(project, row)
        function_name = row.scope.rsplit(".", 1)[-1]
        if row.scope.endswith(".__init__"):
            function_name = row.scope.rsplit(".", 2)[-2]
        for source in project.sources.values():
            for node in ast.walk(source.tree):
                scope = _scope_of(source, node)
                if _covered(rows, source, getattr(node, "lineno", 0)):
                    continue
                if isinstance(node, ast.Call):
                    callee = project.resolve(source, node.func, scope)
                    canonical = _canonical(project, callee)
                    expected = target.removesuffix(".__init__") if target.endswith(".__init__") else target
                    for keyword in node.keywords:
                        if keyword.arg == row.old and canonical is None:
                            found.add(Leftover(source.path, keyword.value.lineno,
                                               f"unresolved call still passes keyword {row.old!r}"))
                    if (row.kind == "kwonly" and canonical is None and position is not None
                            and len(node.args) > position):
                        name = (node.func.id if isinstance(node.func, ast.Name)
                                else node.func.attr if isinstance(node.func, ast.Attribute) else None)
                        if name == function_name:
                            found.add(Leftover(source.path, node.lineno,
                                               f"unresolved {function_name} call still passes "
                                               f"parameter {row.old!r} by position"))
                    if canonical == expected:
                        continue
                    if (canonical is None and row.kind == "name"
                            and ((isinstance(node.func, ast.Name) and node.func.id == row.old)
                                 or (isinstance(node.func, ast.Attribute)
                                     and node.func.attr == row.old))):
                        found.add(Leftover(source.path, node.lineno,
                                           f"unresolved call still uses {row.old!r}"))
                if isinstance(node, ast.Attribute) and node.attr == row.old:
                    resolved = project.resolve(source, node, scope)
                    if resolved is None:
                        found.add(Leftover(source.path, node.lineno,
                                           f"unresolved attribute still uses {row.old!r}"))
                if (parameter_kind is None and isinstance(node, ast.Constant)
                        and node.value == row.old):
                    found.add(Leftover(source.path, node.lineno,
                                       f"string lookup still names {row.old!r}"))
            for token in tokenize.generate_tokens(io.StringIO(source.text).readline):
                if token.type in (tokenize.COMMENT, tokenize.STRING) \
                        and re.search(rf"`{re.escape(row.old)}(?:\(\))?`", token.string):
                    found.add(Leftover(source.path, token.start[0],
                                       f"comment still cites {row.old!r}"))
    return sorted(found)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          check=False)


def _ref_project(root: Path, ref: str) -> Project:
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
    return Project(root, texts)


def main_leftovers(root: Path, project: Project, rows: Sequence[Row], states: Sequence[str],
                   ref: str | None) -> tuple[list[Leftover], str]:
    if ref is None:
        return [], "comparison with main did not run; pass --main REF"
    ancestor = _git(root, "merge-base", "--is-ancestor", ref, "HEAD")
    if ancestor.returncode != 0:
        return [], f"comparison with main did not run; {ref} is not an ancestor of HEAD"
    main = _ref_project(root, ref)
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
    project = Project(root)
    states = [state(project, row) for row in rows]
    edits = plan(project, rows, states)
    texts = transformed(project, edits, rows)
    after = Project(root, texts)
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
        for path in sorted(edits):
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


__all__ = ["KINDS", "Leftover", "RenameError", "Row", "main", "read_table", "run"]


if __name__ == "__main__":
    sys.exit(main())
