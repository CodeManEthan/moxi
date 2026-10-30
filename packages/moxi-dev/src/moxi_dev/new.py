"""`moxi-dev new <module>`: start a new package under packages/.

    moxi-dev new <module> [--summary TEXT] [--core-range RANGE]

Run it in the repository (the root is the nearest directory up whose `pyproject.toml`
declares `[tool.uv.workspace]`). It writes `packages/<module>/`:

- `pyproject.toml` declaring `moxi-<module>`, its `moxi.modules` entry point and the
  `moxi` console script, a dev group with `pytest` and `moxi-dev`, and pytest options
  that deselect the browser, slow, build and live tiers;
- `conftest.py`, which loads the test kit (`moxi_dev.testing`), so every test runs
  inside its fence;
- `src/moxi_<module>/` with an announcement that offers nothing yet;
- `tests/test_contract.py`, the contract check over the package;
- `LICENSE` (the repository's MIT text), a one-line `README.md` and `.gitignore`.

Then it adds `packages/<module>` to the root's workspace `members` and `moxi-<module>`
to its `[tool.uv.sources]` (editing the text, so comments stay) and runs `uv lock` at
the root. It refuses a name that is not a module name or is one of the core's verbs, a
module the workspace already has, and any file that already exists: it overwrites
nothing.

The templates are `templates/**.tmpl` beside this file: `$module`, `$summary`,
`$summary_py`, `$summary_toml` and `$core_range` are substituted in their text, and the directory
`__import__` becomes the module's import package `moxi_<module>`. A rendered package
imports only names that keep their place: `moxi_dev.contract` and `moxi_dev.testing`.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from string import Template

from moxi import names

TEMPLATES = Path(__file__).resolve().parent / "templates"
SUFFIX = ".tmpl"
DEFAULT_CORE_RANGE = ">=0.1,<0.2"
PACKAGES = "packages"


class Refused(Exception):
    """`moxi-dev new` will not do this; the message says why."""


def plan(module: str, summary: str, core_range: str) -> dict[Path, str]:
    """Each file to write, relative to the package's directory, with its rendered text."""
    values = {
        "module": module,
        "summary": summary,
        "summary_py": repr(summary),
        "summary_toml": json.dumps(summary),
        "core_range": core_range,
    }
    out = {}
    for template in sorted(TEMPLATES.rglob(f"*{SUFFIX}")):
        rel = template.relative_to(TEMPLATES)
        parts = [names.import_name(module) if p == "__import__" else p for p in rel.parts]
        parts[-1] = parts[-1].removesuffix(SUFFIX)
        out[Path(*parts)] = Template(template.read_text(encoding="utf-8")).substitute(values)
    return out


def _workspace(path: Path) -> dict | None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    workspace = data.get("tool", {}).get("uv", {}).get("workspace")
    return data if isinstance(workspace, dict) else None


def find_root(start: Path) -> Path:
    """The workspace root at or above `start`: its `pyproject.toml` declares
    `[tool.uv.workspace]`."""
    start = start.resolve()
    for here in (start, *start.parents):
        if (here / "pyproject.toml").is_file() and _workspace(here / "pyproject.toml"):
            return here
    raise Refused(f"no workspace root at or above {start}: run it in the repository, "
                  "whose pyproject.toml declares [tool.uv.workspace]")


# --- editing the root pyproject.toml ----------------------------------------------------

def _header_span(text: str, table: str) -> tuple[int, int] | None:
    """Where `[table]`'s body starts (after its header line) and ends (the next header)."""
    header = re.compile(r"^[ \t]*\[[ \t]*" + re.escape(table) + r"[ \t]*\][ \t]*(#.*)?$",
                        re.M)
    found = header.search(text)
    if not found:
        return None
    start = text.find("\n", found.end())
    start = len(text) if start < 0 else start + 1
    nxt = re.compile(r"^[ \t]*\[", re.M).search(text, start)
    return start, nxt.start() if nxt else len(text)


def _array_tokens(text: str, at: int) -> tuple[list[tuple[int, int]], list[int], int]:
    """Scan the array whose `[` is at `at`: its string tokens, comma positions and the
    position of its closing `]`."""
    strings: list[tuple[int, int]] = []
    commas: list[int] = []
    i = at + 1
    while i < len(text):
        c = text[i]
        if c == "#":
            i = text.find("\n", i)
            if i < 0:
                break
        elif c in "\"'":
            triple = text.startswith(c * 3, i)
            quote = c * 3 if triple else c
            j = i + len(quote)
            while j < len(text) and not text.startswith(quote, j):
                j += 2 if c == '"' and text[j] == "\\" else 1
            strings.append((i, j + len(quote)))
            i = j + len(quote)
            continue
        elif c == ",":
            commas.append(i)
        elif c == "[":
            raise Refused("the workspace members hold a nested array")
        elif c == "]":
            return strings, commas, i
        i += 1
    raise Refused("the workspace members array has no closing ]")


def _add_to_members(text: str, member: str) -> str:
    span = _header_span(text, "tool.uv.workspace")
    if span is None:
        raise Refused("pyproject.toml has no [tool.uv.workspace] table to add to")
    key = re.compile(r"^([ \t]*)members[ \t]*=[ \t]*\[", re.M).search(text, *span)
    if key is None:
        raise Refused("[tool.uv.workspace] has no members array to add to")
    strings, commas, close = _array_tokens(text, key.end() - 1)
    item = json.dumps(member)
    if not strings:
        inner = text[key.end():close]
        if "\n" in inner:
            at = text.index("\n", key.end()) + 1
            return text[:at] + f"{key.group(1)}    {item},\n" + text[at:]
        return text[:key.end()] + item + text[close:]
    last_end = strings[-1][1]
    comma = next((c for c in commas if c >= last_end), None)
    if comma is None:
        text = text[:last_end] + "," + text[last_end:]
        comma, close = last_end, close + 1
    anchor = comma + 1
    if "\n" in text[anchor:close]:
        line_start = text.rfind("\n", 0, strings[-1][0]) + 1
        indent = re.match(r"[ \t]*", text[line_start:]).group(0)
        at = text.index("\n", anchor) + 1
        return text[:at] + f"{indent}{item},\n" + text[at:]
    return text[:anchor] + f" {item}" + text[anchor:]


def _add_to_sources(text: str, dist: str) -> str:
    line = f"{dist} = {{ workspace = true }}\n"
    span = _header_span(text, "tool.uv.sources")
    if span is None:
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return f"{text}{sep}[tool.uv.sources]\n{line}"
    pairs = list(re.compile(r"^[ \t]*[A-Za-z0-9_.\"'-]+[ \t]*=.*$", re.M)
                 .finditer(text, *span))
    if not pairs:
        return text[:span[0]] + line + text[span[0]:]
    end = pairs[-1].end()
    at = text.find("\n", end)
    at = len(text) if at < 0 else at + 1
    if at == len(text) and not text.endswith("\n"):
        return text + "\n" + line
    return text[:at] + line + text[at:]


def add_member(text: str, module: str) -> str:
    """The root `pyproject.toml` text with `packages/<module>` added to the workspace
    members and `moxi-<module>` to the sources; every other line is kept as it was."""
    before = tomllib.loads(text)
    member, dist = f"{PACKAGES}/{module}", names.distribution(module)
    after_text = _add_to_sources(_add_to_members(text, member), dist)
    try:
        after = tomllib.loads(after_text)
    except tomllib.TOMLDecodeError as exc:
        raise Refused(f"adding {member} would break pyproject.toml ({exc}); "
                      "add it by hand") from None
    expected = copy.deepcopy(before)
    uv = expected.setdefault("tool", {}).setdefault("uv", {})
    uv["workspace"]["members"] = [*uv["workspace"]["members"], member]
    uv.setdefault("sources", {})[dist] = {"workspace": True}
    if after != expected:
        raise Refused(f"adding {member} to pyproject.toml changed more than the members "
                      "and sources; add it by hand")
    return after_text


# --- rendering --------------------------------------------------------------------------

def render(module: str, root: Path, *, summary: str | None = None,
           core_range: str = DEFAULT_CORE_RANGE) -> list[Path]:
    """Write `packages/<module>/` under the workspace `root` and name it in the root's
    `pyproject.toml`; return the paths written. `uv lock` is the caller's next step
    (`main` runs it)."""
    names.check_module_name(module)
    if module in names.VERBS:
        raise Refused(f"{module!r} is one of the core's own verbs; a module needs another "
                      "name")
    summary = summary or f"Moxi's {module} module."
    if "\n" in summary or not summary.strip():
        raise Refused("the summary is one non-empty line")
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    data = _workspace(pyproject)
    if data is None:
        raise Refused(f"{pyproject} declares no [tool.uv.workspace]")
    member, dist = f"{PACKAGES}/{module}", names.distribution(module)
    members = data["tool"]["uv"]["workspace"].get("members", [])
    sources = data["tool"]["uv"].get("sources", {})
    if member in members or dist in sources:
        raise Refused(f"the workspace already has {dist} ({member}); pick another name")
    package = root / PACKAGES / module
    files = plan(module, summary, core_range)
    clash = [str(Path(PACKAGES, module, rel)) for rel in files if (package / rel).exists()]
    if clash:
        raise Refused(f"{root} already holds {', '.join(clash)}; moxi-dev new starts a "
                      "package and overwrites nothing")
    new_text = add_member(text, module)
    written = []
    for rel, body in files.items():
        path = package / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        written.append(path)
    pyproject.write_text(new_text, encoding="utf-8")
    return written


def lock(root: Path) -> int:
    """Run `uv lock` at the workspace root; return its exit code."""
    uv = shutil.which("uv")
    if uv is None:
        print("moxi-dev new: uv is not on PATH; run `uv lock` at the root yourself",
              file=sys.stderr)
        return 1
    return subprocess.run([uv, "lock"], cwd=root, check=False).returncode


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="moxi-dev new",
        description="Start a new package under packages/, name it in the workspace and "
                    "lock it.")
    parser.add_argument("module", help="the module's name, lower-case letters and digits")
    parser.add_argument("--summary", help="the module's SUMMARY and description, one line")
    parser.add_argument("--core-range", default=DEFAULT_CORE_RANGE,
                        help="the version range of moxi the module requires "
                             f"(default {DEFAULT_CORE_RANGE})")
    args = parser.parse_args(argv)
    try:
        root = find_root(Path.cwd())
        written = render(args.module, root, summary=args.summary,
                         core_range=args.core_range)
    except (ValueError, Refused) as exc:
        print(f"moxi-dev new: {exc}", file=sys.stderr)
        return 2
    for path in written:
        print(path.relative_to(root))
    print(f"pyproject.toml: {PACKAGES}/{args.module} added to the workspace members and "
          "sources")
    code = lock(root)
    if code:
        print(f"moxi-dev new: uv lock failed (exit {code}); the package is written and "
              "named in pyproject.toml, so fix the cause and run `uv lock` again",
              file=sys.stderr)
        return 1
    return 0
