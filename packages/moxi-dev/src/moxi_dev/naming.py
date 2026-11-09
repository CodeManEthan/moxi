"""Check the mechanical public-name rules over a Moxi repository."""

from __future__ import annotations

import ast
import importlib
import inspect
import io
import re
import subprocess
import sys
import tokenize
import tomllib
import warnings
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .contract import CORE_MODULES

VENDORED = "packages/moxi/src/moxi/_vendor/"
DURATION_WORDS = (
    "timeout", "wait", "interval", "ttl", "age", "delay", "tick", "expiry",
    "expires", "retain", "keep", "hold", "idle", "grace", "backoff", "period",
    "every", "lifetime", "stale", "warn_before", "retry", "poll", "linger",
    "settle", "cooldown", "pause", "sleep", "duration", "window", "patience",
    "after",
)
_DURATION = re.compile(r"(?:^|[_-])(?:" + "|".join(DURATION_WORDS) + r")(?:[_-]|$)", re.I)
_UNIT = re.compile(r"[_-](s|ms|minutes|hours|days|mib|chars|tokens)$", re.I)
_UNIT_ONLY = re.compile(r"^(?:s|ms|minutes|hours|days|mib|chars|tokens)$", re.I)
_OTHER_UNIT = re.compile(r"[_-](?:seconds|secs?|mb|millis|msecs?|mins?|hrs?)$", re.I)
_POINT = re.compile(r"(?:deadline|^at$|_at$|since|until|before$|^now$|when|started|mtime)",
                    re.I)
_UNIT_DOC = re.compile(
    r"\b(milliseconds|seconds|minutes|hours|days|characters|tokens)\b|\bMiB\b", re.I)
_DOC_UNIT = {"milliseconds": "ms", "seconds": "s", "minutes": "minutes",
             "hours": "hours", "days": "days", "mib": "mib",
             "characters": "chars", "tokens": "tokens"}
_METAVARS = frozenset({"SECONDS", "SECS", "MINUTES", "HOURS", "DAYS", "MS",
                       "MILLISECONDS"})
_BAD_VERBS = frozenset({"delete", "rm", "on", "off", "state", "ls"})
_SWITCH_KEYS = frozenset({"on", "off", "auto", "active", "enable", "disable",
                          "disabled"})
_MX_CODE = re.compile(r"\bMX[1-4]\b")


@dataclass(frozen=True, order=True)
class Hit:
    path: str
    line: int
    code: str
    part: str
    message: str
    fix: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.code}: {self.part}: {self.message}; {self.fix}"


def _files(root: Path, pattern: str | None = None) -> list[str]:
    args = ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    if pattern:
        args.append(pattern)
    done = subprocess.run(args, cwd=root, capture_output=True, check=False)
    if done.returncode == 0:
        return sorted(p for p in done.stdout.decode("utf-8", "surrogateescape").split("\0")
                      if p)
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def _table_lines(text: str) -> dict[str, int]:
    found = {}
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"\s*\[([^]]+)]", line)
        if match:
            found[match.group(1).strip()] = number
    return found


def _walk_keys(data: dict, prefix: tuple[str, ...] = ()):
    if isinstance(data.get("type"), str) or "retired" in data:
        yield prefix, data
        return
    for name, value in data.items():
        if isinstance(value, dict):
            yield from _walk_keys(value, (*prefix, name))


def _settings(root: Path) -> tuple[list[Hit], dict[str, list[tuple[str, str, str, int]]]]:
    hits = []
    leaves: dict[str, list[tuple[str, str, str, int]]] = defaultdict(list)
    for path in _files(root):
        if not path.startswith("packages/") or not path.endswith("/settings.toml"):
            continue
        text = (root / path).read_text(encoding="utf-8")
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            continue
        lines = _table_lines(text)
        module = path.split("/")[1]
        for parts, item in _walk_keys(data):
            if not parts or "retired" in item:
                continue
            key, leaf = ".".join(parts), parts[-1]
            kind = item.get("type", "")
            line = lines.get(key, 1)
            leaves[leaf].append((kind, module, key, line))
            if leaf in _SWITCH_KEYS:
                hits.append(Hit(path, line, "MX4", "switch key", f"{key} uses {leaf!r}",
                                "name what the key turns on, or use enabled for a whole part"))
            if leaf == "enabled" and kind != "bool":
                hits.append(Hit(path, line, "MX4", "switch key",
                                f"{key} is enabled but has type {kind!r}",
                                "make an enabled key a bool"))
            if kind not in ("int", "float"):
                continue
            if _OTHER_UNIT.search(leaf):
                hits.append(Hit(path, line, "MX1", "key unit",
                                f"{key} uses a non-house unit suffix",
                                "use _ms, _s, _minutes, _hours, _days, _mib, _chars or _tokens"))
                continue
            match = _UNIT_DOC.search(str(item.get("doc", "")))
            if not match:
                continue
            wanted = _DOC_UNIT[match.group(0).lower()]
            suffix = _UNIT.search(leaf)
            if leaf in (wanted, match.group(0).lower()):
                continue
            if suffix is None or suffix.group(1).lower() != wanted:
                hits.append(Hit(path, line, "MX1", "key unit",
                                f"{key} documents {match.group(0)} but its name lacks _{wanted}",
                                f"end the key with _{wanted}"))
    for leaf, entries in leaves.items():
        if len({kind for kind, _, _, _ in entries}) > 1:
            kind, module, key, line = sorted(entries)[0]
            details = ", ".join(f"{mod}.{name}:{typ}" for typ, mod, name, _ in sorted(entries))
            path = next(p for p in _files(root) if p.startswith(f"packages/{module}/")
                        and p.endswith("/settings.toml"))
            hits.append(Hit(path, line, "MX4", "key type",
                            f"the last name {leaf!r} has more than one type: {details}",
                            "give one last name one type in every module"))
    return hits, leaves


def _constant(node: ast.AST | None):
    return node.value if isinstance(node, ast.Constant) else None


def _python_sources(root: Path):
    for path in _files(root):
        if (path.startswith("packages/") and "/src/" in path and path.endswith(".py")
                and not path.startswith(VENDORED)):
            try:
                text = (root / path).read_text(encoding="utf-8")
                yield path, text, ast.parse(text, filename=path)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue


def _flags_and_verbs(root: Path) -> list[Hit]:
    hits = []
    verbs: dict[tuple[str, str], tuple[str, int]] = {}
    for path, _text, tree in _python_sources(root):
        module = path.rsplit("/", 1)[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "add_argument":
                    options = [_constant(arg) for arg in node.args
                               if isinstance(_constant(arg), str)
                               and _constant(arg).startswith("--")]
                    keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
                    metavar = _constant(keywords.get("metavar"))
                    typ = ast.unparse(keywords["type"]) if "type" in keywords else None
                    for option in options:
                        bare = option[2:]
                        if _UNIT.search(bare) or _UNIT_ONLY.fullmatch(bare):
                            continue
                        why = None
                        if _OTHER_UNIT.search(bare):
                            why = "uses a non-house unit suffix"
                        elif metavar in _METAVARS:
                            why = f"uses the {metavar} metavar"
                        elif typ in ("int", "float") and _DURATION.search(bare) and not _POINT.search(bare):
                            why = f"is a numeric duration ({typ})"
                        if why:
                            hits.append(Hit(path, node.lineno, "MX1", "flag unit",
                                            f"{option} {why} but has no house unit suffix",
                                            "add the unit suffix to the flag"))
                elif node.func.attr == "add_parser" and node.args:
                    verb = _constant(node.args[0])
                    if verb in _BAD_VERBS:
                        verbs.setdefault((module, verb), (path, node.lineno))
                elif (node.func.attr == "update" and isinstance(node.func.value, ast.Name)
                      and node.func.value.id in ("VERBS", "_VERBS") and node.args
                      and isinstance(node.args[0], ast.Dict)):
                    for key in node.args[0].keys:
                        verb = _constant(key)
                        if verb in _BAD_VERBS:
                            verbs.setdefault((module, verb), (path, key.lineno))
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if (any(isinstance(t, ast.Name) and t.id in ("VERBS", "_VERBS") for t in targets)
                        and isinstance(value, ast.Dict)):
                    for key in value.keys:
                        verb = _constant(key)
                        if verb in _BAD_VERBS:
                            verbs.setdefault((module, verb), (path, key.lineno))
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "Verb" and node.args):
                verb = _constant(node.args[0])
                if verb in _BAD_VERBS:
                    verbs.setdefault((module, verb), (path, node.lineno))
    for (_module, verb), (path, line) in verbs.items():
        hits.append(Hit(path, line, "MX4", "CLI verb", f"{verb!r} is a refused verb",
                        "use remove, revoke, enable, disable, list, show, status or doctor"))
    return hits


def _lazy_names(module) -> set[str]:
    try:
        source = inspect.getsource(module)
    except (OSError, TypeError):
        return set()
    tree = ast.parse(source)
    getter = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "__getattr__"), None)
    if getter is None:
        return set()
    referenced = {n.id for n in ast.walk(getter) if isinstance(n, ast.Name)}
    nodes = [getter]
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id in referenced for target in targets):
                nodes.append(node)
    return {one.value for node in nodes for one in ast.walk(node)
            if isinstance(one, ast.Constant) and isinstance(one.value, str)
            and one.value.isidentifier()}


def _numeric(parameter: inspect.Parameter) -> bool:
    annotation = parameter.annotation
    if annotation is not inspect.Parameter.empty:
        rendered = annotation if isinstance(annotation, str) else getattr(annotation, "__name__",
                                                                           str(annotation))
        if re.search(r"\bbool\b", rendered):
            return False
        return bool(re.search(r"\b(?:int|float)\b", rendered))
    return parameter.default is not inspect.Parameter.empty and type(parameter.default) in (int, float)


def _source_location(root: Path, obj, qualified: str) -> tuple[str, int]:
    try:
        source = Path(inspect.getsourcefile(obj) or "").resolve()
        path = str(source.relative_to(root.resolve()))
        line = inspect.getsourcelines(obj)[1]
        if path.startswith("packages/"):
            return path, line
    except (OSError, TypeError, ValueError):
        pass
    module = sys.modules.get(getattr(obj, "__module__", ""))
    try:
        source = Path(module.__file__).resolve()
        path = str(source.relative_to(root.resolve()))
        tree = ast.parse(source.read_text(encoding="utf-8"))
        wanted = qualified.split(".")[0] if qualified.endswith(".__init__") else qualified.split(".")[-1]
        node = next(one for one in ast.walk(tree)
                    if isinstance(one, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and one.name == wanted)
        return path, node.lineno
    except (AttributeError, OSError, StopIteration, SyntaxError, TypeError, ValueError):
        return "packages", 1


def _parameters(root: Path) -> list[Hit]:
    core_dir = root / "packages" / "moxi" / "src" / "moxi"
    surfaces = [("moxi", f"moxi.{name}") for name in sorted(CORE_MODULES)
                if (core_dir / f"{name}.py").is_file() or (core_dir / name).is_dir()]
    for path, text, _tree in _python_sources(root):
        if path.endswith("/module.py"):
            match = re.search(r'^API\s*=\s*["\']([^"\']+)["\']', text, re.M)
            if match:
                package = path.split("/src/", 1)[1].split("/", 1)[0]
                surfaces.append((package, match.group(1)))
    additions = [str(root / "packages" / package / "src")
                 for package in sorted((root / "packages").iterdir())
                 if (root / "packages" / package / "src").is_dir()]
    old_path = list(sys.path)
    old_modules = set(sys.modules)
    sys.path[:0] = additions
    importlib.invalidate_caches()
    seen = set()
    hits = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for package, module_name in surfaces:
                try:
                    module = importlib.import_module(module_name)
                except Exception:
                    continue
                top = module_name.split(".")[0]
                names = (set(getattr(module, "__all__", ()))
                         | {name for name in dir(module) if not name.startswith("_")}
                         | _lazy_names(module))
                for name in sorted(names):
                    if name.startswith("_"):
                        continue
                    try:
                        obj = getattr(module, name)
                    except Exception:
                        continue
                    if not (inspect.isfunction(obj) or inspect.isclass(obj)):
                        continue
                    if (getattr(obj, "__module__", "") or "").split(".")[0] != top:
                        continue
                    key = (obj.__module__, obj.__qualname__)
                    if key in seen:
                        continue
                    seen.add(key)
                    functions = [(obj, obj.__qualname__)] if inspect.isfunction(obj) else []
                    if inspect.isclass(obj):
                        functions.extend((value.__func__ if isinstance(value, (staticmethod, classmethod)) else value,
                                          f"{obj.__qualname__}.{attr}")
                                         for attr, value in vars(obj).items()
                                         if (not attr.startswith("_") or attr == "__init__")
                                         and inspect.isfunction(value.__func__ if isinstance(
                                             value, (staticmethod, classmethod)) else value))
                    for function, qualified in functions:
                        try:
                            signature = inspect.signature(function)
                        except (TypeError, ValueError):
                            continue
                        path, line = _source_location(root, function, qualified)
                        for parameter in signature.parameters.values():
                            if (parameter.name in ("self", "cls")
                                    or parameter.kind in (parameter.VAR_POSITIONAL,
                                                          parameter.VAR_KEYWORD)):
                                continue
                            name = parameter.name
                            if _UNIT.search(name) or _UNIT_ONLY.fullmatch(name):
                                continue
                            reason = None
                            if _OTHER_UNIT.search(name) and _numeric(parameter):
                                reason = "uses a non-house unit suffix"
                            elif (_POINT.search(name) and _numeric(parameter)
                                  and type(parameter.default) in (int, float)
                                  and parameter.default > 0):
                                reason = f"names a point in time but defaults to the amount {parameter.default!r}"
                            elif (_DURATION.search(name) and not _POINT.search(name)
                                  and _numeric(parameter)):
                                reason = "is a numeric duration without a unit suffix"
                            if reason:
                                hits.append(Hit(path, line, "MX1", "public parameter",
                                                f"{module_name}.{qualified} parameter {name!r} {reason}",
                                                "end the parameter with its unit"))
    finally:
        sys.path[:] = old_path
        for name in set(sys.modules) - old_modules:
            module = sys.modules.get(name)
            try:
                Path(module.__file__).resolve().relative_to(root.resolve())
            except (AttributeError, TypeError, ValueError):
                continue
            sys.modules.pop(name, None)
    return hits


def _marks(root: Path) -> list[Hit]:
    hits = []
    for path in _files(root):
        if not path.startswith("packages/") or path.startswith(VENDORED):
            continue
        if path.endswith(".py"):
            text = (root / path).read_text(encoding="utf-8")
            try:
                lines = [(token.start[0], token.string) for token in
                         tokenize.generate_tokens(io.StringIO(text).readline)
                         if token.type == tokenize.COMMENT and "# noqa" in token.string]
            except (tokenize.TokenError, IndentationError):
                lines = []
        elif path.endswith("/settings.toml"):
            lines = [(number, line) for number, line in enumerate(
                (root / path).read_text(encoding="utf-8").splitlines(), 1)
                     if re.match(r"\s*\[[^]]+]", line) and "# noqa" in line]
        else:
            continue
        for number, line in lines:
            tail = line.split("# noqa", 1)[1]
            if not tail.lstrip().startswith(":"):
                hits.append(Hit(path, number, "marks", "exception mark",
                                "a bare # noqa gives no rule or reason",
                                "write # noqa: <code> -- <why>"))
            elif _MX_CODE.search(tail) and not re.search(r"\s--\s\S", tail):
                hits.append(Hit(path, number, "marks", "exception mark",
                                "an MX mark has no reason",
                                "add -- <why> after the code"))
    return hits


def _allowed_marks(root: Path) -> dict[tuple[str, int], set[str]]:
    allowed: dict[tuple[str, int], set[str]] = {}
    pattern = re.compile(r"# noqa:\s*([^#]*?)\s+--\s+\S")
    for path in _files(root):
        if (not path.startswith("packages/") or path.startswith(VENDORED)
                or not path.endswith((".py", "/settings.toml"))):
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if path.endswith(".py"):
            try:
                lines = [(token.start[0], token.string) for token in
                         tokenize.generate_tokens(io.StringIO(text).readline)
                         if token.type == tokenize.COMMENT]
            except (tokenize.TokenError, IndentationError):
                lines = []
        else:
            lines = [(number, line) for number, line in enumerate(text.splitlines(), 1)
                     if re.match(r"\s*\[[^]]+]", line)]
        for number, line in lines:
            match = pattern.search(line)
            if match:
                allowed[(path, number)] = set(re.findall(r"\bMX[1-4]\b", match.group(1)))
    return allowed


def scan(root: Path | str) -> list[Hit]:
    """Return every unit, verb, switch-key and mark finding in the whole tree."""
    root = Path(root)
    settings, _leaves = _settings(root)
    findings = settings + _flags_and_verbs(root) + _parameters(root)
    allowed = _allowed_marks(root)
    findings = [hit for hit in findings if hit.code not in allowed.get((hit.path, hit.line), set())]
    return sorted(findings + _marks(root))


__all__ = ["DURATION_WORDS", "Hit", "scan"]
