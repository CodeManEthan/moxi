"""The pronoun check: text a reader sees names the operator neutrally.

Moxi is public, and the person it serves is "the operator", or "you" where the text
speaks to the reader. The check refuses the four third-person masculine pronouns (the
nominative, the possessive, the objective and the reflexive), whole words, in any case, in
the text a package shows or ships:

- its Python sources under `src/`: every string literal and docstring, f-string text
  included, read with `tokenize`, so a name, a comment and code are never read;
- its web sources under `web/src/`: every string literal, template text and comment in
  a `.ts`, `.tsx`, `.js`, `.jsx` or `.mjs` file; a built bundle is not read;
- its shipped docs: `README.md`, each `docs/*.md`, and each bundled skill's `SKILL.md`
  under `src/`, every line.

A lower-case word joined by one dot to the name before it, as a field is to its object, is a
name and passes; after an ellipsis, or capitalised after a dot, it is prose. Tests, CHANGELOGs (release history) and the core's vendored libraries are not read.

A genuine use (a quotation, a generic replier in a protocol's prose) is let stand by a
mark on the same line, in the house form of an exception mark: `noqa:`, the code `CODE`
names, two hyphens and the reason, as CONTRIBUTING.md shows it. It is written in the
file's own comment syntax (`# ...` in Python, `// ...` in TypeScript, `<!-- ... -->` in
Markdown) or, on a docstring's inner line, in the text itself. A mark with no reason is
refused, and so is a mark on a line the check would not refuse. A mark quoted as inline
code, in backticks, is an example of the form and not a mark.

`scan(root)` reads every tracked and untracked-but-not-ignored file it covers and returns
what it found.
"""

from __future__ import annotations

import bisect
import io
import re
import subprocess
import tokenize
from dataclasses import dataclass
from pathlib import Path

from .contract import CORE
from .contract import VENDORED as _CORE_VENDORED

#: The code an exception mark names to let a pronoun stand.
CODE = "MX5"
#: The refused words, whole words in any case. Spelled in parts so no word stands here. A
#: lower-case word joined by one dot to the name before it is a name, not prose; after an
#: ellipsis, or capitalised after a dot ("done.He"), it is prose.
WORD = re.compile(r"(?<![\w-])(?<!\w\.(?=(?-i:[a-z])))h(?:e|is|im|imself)(?![\w-])", re.I)
#: A mark that names `CODE` among its codes, then ` -- ` and a reason.
MARK = re.compile(r"\bnoqa:\s*[A-Z0-9, ]*?\b" + CODE + r"\b[A-Z0-9, ]*?\s+--\s+\S")
#: Any mark that names `CODE`, with or without a reason.
ANY_MARK = re.compile(r"\bnoqa:\s*[A-Z0-9, ]*?\b" + CODE + r"\b")
#: A mark quoted as inline code, an example of the form, which is not a mark itself.
QUOTED_MARK = re.compile(r"`[^`\n]*\bnoqa:[^`\n]*`")
#: The fix a refused word's line names.
FIX = f"name the operator neutrally, or mark it # noqa: {CODE} -- <why>"

#: The core's directory of vendored copies of released libraries, whose text is not ours.
VENDORED = f"packages/{CORE}/{_CORE_VENDORED}"
WEB_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs")
#: Python's tokens that hold a string's text: a whole literal, or an f- or t-string's text.
_STRING_TOKENS = frozenset(
    getattr(tokenize, name) for name in ("STRING", "FSTRING_MIDDLE", "TSTRING_MIDDLE")
    if hasattr(tokenize, name))
#: A backslash escape in a literal's source, so `\nword` still reads `word` as a word.
_ESCAPE = re.compile(r"\\(?:N\{[^}\n]*\}|x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|[^\n])")
#: What may come before a `/` that starts a regular expression literal, not a division.
_REGEX_AFTER = frozenset("(,=:[!&|?{};+-*%<>~^")
_REGEX_WORDS = frozenset({"return", "typeof", "case", "do", "else", "in", "of", "new",
                          "delete", "void", "throw", "instanceof", "yield", "await"})


@dataclass(frozen=True, order=True)
class Hit:
    path: str
    line: int
    word: str
    #: "" for a refused word; "reason" for a mark with no reason; "unused" for a mark on a
    #: line with nothing to let stand.
    fault: str = ""

    def __str__(self) -> str:
        if self.fault == "reason":
            return (f"{self.path}:{self.line}: pronouns: the {CODE} mark gives no reason; "
                    f"add -- <why> after {CODE}")
        if self.fault == "unused":
            return (f"{self.path}:{self.line}: pronouns: the {CODE} mark lets nothing stand; "
                    "remove it")
        return f"{self.path}:{self.line}: {self.word} — fix: {FIX}"


def _files(root: Path) -> list[str]:
    done = subprocess.run(["git", "ls-files", "-z", "--cached", "--others",
                           "--exclude-standard"], cwd=root, capture_output=True, check=True)
    return sorted({p for p in done.stdout.decode("utf-8", "surrogateescape").split("\0") if p})


def kind(path: str) -> str | None:
    """What the check reads `path` as: "python", "web" or "doc"; None when it is not read."""
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "packages" or path.startswith(VENDORED):
        return None
    rest = parts[2:]
    if rest[0] == "src":
        if path.endswith(".py"):
            return "python"
        return "doc" if rest[-1] == "SKILL.md" else None
    if rest[:2] == ["web", "src"] and path.endswith(WEB_SUFFIXES):
        return "web"
    if rest == ["README.md"] or (len(rest) == 2 and rest[0] == "docs"
                                  and rest[1].endswith(".md")):
        return "doc"
    return None


def _words(text: str, line: int) -> list[tuple[int, str]]:
    """Each refused word in `text`, which starts on `line`, with its own line."""
    return [(line + text.count("\n", 0, match.start()), match.group(0))
            for match in WORD.finditer(text)]


def python_words(text: str) -> list[tuple[int, str]]:
    """Each refused word in the string literals and docstrings of the Python `text`."""
    found = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type in _STRING_TOKENS:
                found += _words(_ESCAPE.sub(" ", token.string), token.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return found


def _quoted_end(text: str, start: int) -> int:
    """The index after the quoted literal opened at `start`; a literal stops at a line end."""
    quote, at = text[start], start + 1
    while at < len(text) and text[at] not in (quote, "\n"):
        at += 2 if text[at] == "\\" else 1
    return min(at + 1, len(text))


def _regex_end(text: str, start: int) -> int:
    """The index after the regular expression literal opened at `start`, flags included."""
    at, in_class = start + 1, False
    while at < len(text) and text[at] != "\n":
        char = text[at]
        if char == "\\":
            at += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            break
        at += 1
    at += 1
    while at < len(text) and (text[at].isalnum() or text[at] == "_"):
        at += 1
    return min(at, len(text))


def web_texts(text: str) -> list[tuple[int, str]]:
    """The string literals, template text and comments of the TypeScript or JavaScript
    `text`, each with the index it starts at."""
    found: list[tuple[int, str]] = []
    depths: list[int] = []      # one brace depth per template expression we are inside
    last, last_word = "", ""    # the last significant character, and the word it ended
    at, size = 0, len(text)

    def template(start: int) -> int:
        """Read template text from `start` to its closing backtick or a `${`."""
        here = start
        while here < size:
            char = text[here]
            if char == "\\":
                here += 2
                continue
            if char == "`":
                found.append((start, text[start:here]))
                return here + 1
            if text.startswith("${", here):
                found.append((start, text[start:here]))
                depths.append(0)
                return here + 2
            here += 1
        found.append((start, text[start:]))
        return size

    while at < size:
        char = text[at]
        if text.startswith("//", at):
            end = text.find("\n", at)
            end = size if end < 0 else end
            found.append((at, text[at:end]))
            at = end
            continue
        if text.startswith("/*", at):
            end = text.find("*/", at + 2)
            end = size if end < 0 else end + 2
            found.append((at, text[at:end]))
            at = end
            continue
        if char in "'\"":
            end = _quoted_end(text, at)
            found.append((at, text[at:end]))
            at, last, last_word = end, '"', ""
            continue
        if char == "`":
            at, last, last_word = template(at + 1), '"', ""
            continue
        if char == "/" and (last in _REGEX_AFTER or last == "" or last_word in _REGEX_WORDS):
            at, last, last_word = _regex_end(text, at), '"', ""
            continue
        if depths and char == "{":
            depths[-1] += 1
        elif depths and char == "}":
            if depths[-1] == 0:
                depths.pop()
                at, last, last_word = template(at + 1), '"', ""
                continue
            depths[-1] -= 1
        if char.isalnum() or char in "_$":
            end = at
            while end < size and (text[end].isalnum() or text[end] in "_$"):
                end += 1
            last, last_word, at = "a", text[at:end], end
            continue
        if not char.isspace():
            last, last_word = char, ""
        at += 1
    return found


def web_words(text: str) -> list[tuple[int, str]]:
    """Each refused word in the string literals, template text and comments of `text`."""
    breaks = [index for index, char in enumerate(text) if char == "\n"]
    found = []
    for start, piece in web_texts(text):
        line = bisect.bisect_right(breaks, start - 1) + 1 if breaks else 1
        found += _words(piece, line)
    return found


def doc_words(text: str) -> list[tuple[int, str]]:
    """Each refused word in the Markdown `text`, every line read."""
    return _words(text, 1)


_READERS = {"python": python_words, "web": web_words, "doc": doc_words}


def check_text(path: str, text: str, how: str) -> list[Hit]:
    """What the check refuses in `text`, read as `how` ("python", "web" or "doc"), for the
    file at `path`: each unmarked word, each mark with no reason and each unused mark."""
    lines = text.splitlines()
    marked, bare = set(), set()
    for number, line in enumerate(lines, 1):
        line = QUOTED_MARK.sub("", line)
        if MARK.search(line):
            marked.add(number)
        elif ANY_MARK.search(line):
            bare.add(number)
    hits, used = [], set()
    for number, word in _READERS[how](text):
        if number in marked:
            used.add(number)
        else:
            hits.append(Hit(path, number, word))
    hits += [Hit(path, number, "", "reason") for number in bare]
    hits += [Hit(path, number, "", "unused") for number in marked - used]
    return sorted(hits)


def scan(root: Path | str) -> list[Hit]:
    """Every refused word, mark with no reason and unused mark in the repository at `root`."""
    root = Path(root)
    hits = []
    for path in _files(root):
        how = kind(path)
        if how is None or not (root / path).is_file():
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits += check_text(path, text, how)
    return sorted(hits)


__all__ = ["CODE", "FIX", "Hit", "MARK", "WORD", "check_text", "doc_words", "kind",
           "python_words", "scan", "web_texts", "web_words"]
