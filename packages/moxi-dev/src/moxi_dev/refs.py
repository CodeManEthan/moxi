"""Check references in comments and docstrings against the repository tree."""

from __future__ import annotations

import ast
import builtins
import io
import keyword
import re
import subprocess
import tokenize
from dataclasses import dataclass
from pathlib import Path

ALLOW_FILE = ".refs-allow"
VENDORED = "packages/moxi/src/moxi/_vendor/"

# Frozen union of sys.stdlib_module_names from CPython 3.12.13, 3.13.15 and 3.14.8.
STDLIB_MODULE_NAMES = frozenset("""
__future__ _abc _aix_support _android_support _apple_support _ast _ast_unparse _asyncio
_bisect _blake2 _bz2 _codecs _codecs_cn _codecs_hk _codecs_iso2022 _codecs_jp
_codecs_kr _codecs_tw _collections _collections_abc _colorize _compat_pickle
_compression _contextvars _crypt _csv _ctypes _curses _curses_panel _datetime _dbm
_decimal _elementtree _frozen_importlib _frozen_importlib_external _functools _gdbm
_hashlib _heapq _hmac _imp _interpchannels _interpqueues _interpreters _io _ios_support
_json _locale _lsprof _lzma _markupbase _md5 _msi _multibytecodec _multiprocessing
_opcode _opcode_metadata _operator _osx_support _overlapped _pickle _posixshmem
_posixsubprocess _py_abc _py_warnings _pydatetime _pydecimal _pyio _pylong _pyrepl
_queue _random _remote_debugging _scproxy _sha1 _sha2 _sha3 _signal _sitebuiltins
_socket _sqlite3 _sre _ssl _stat _statistics _string _strptime _struct _suggestions
_symtable _sysconfig _thread _threading_local _tkinter _tokenize _tracemalloc _types
_typing _uuid _warnings _weakref _weakrefset _winapi _wmi _zoneinfo _zstd abc aifc
annotationlib antigravity argparse array ast asyncio atexit audioop base64 bdb binascii
bisect builtins bz2 cProfile calendar cgi cgitb chunk cmath cmd code codecs codeop
collections colorsys compileall compression concurrent configparser contextlib
contextvars copy copyreg crypt csv ctypes curses dataclasses datetime dbm decimal
difflib dis doctest email encodings ensurepip enum errno faulthandler fcntl filecmp
fileinput fnmatch fractions ftplib functools gc genericpath getopt getpass gettext glob
graphlib grp gzip hashlib heapq hmac html http idlelib imaplib imghdr importlib inspect
io ipaddress itertools json keyword lib2to3 linecache locale logging lzma mailbox
mailcap marshal math mimetypes mmap modulefinder msilib msvcrt multiprocessing netrc
nis nntplib nt ntpath nturl2path numbers opcode operator optparse os ossaudiodev
pathlib pdb pickle pickletools pipes pkgutil platform plistlib poplib posix posixpath
pprint profile pstats pty pwd py_compile pyclbr pydoc pydoc_data pyexpat queue quopri
random re readline reprlib resource rlcompleter runpy sched secrets select selectors
shelve shlex shutil signal site smtplib sndhdr socket socketserver spwd sqlite3
sre_compile sre_constants sre_parse ssl stat statistics string stringprep struct
subprocess sunau symtable sys sysconfig syslog tabnanny tarfile telnetlib tempfile
termios textwrap this threading time timeit tkinter token tokenize tomllib trace
traceback tracemalloc tty turtle turtledemo types typing unicodedata unittest urllib uu
uuid venv warnings wave weakref webbrowser winreg winsound wsgiref xdrlib xml xmlrpc
zipapp zipfile zipimport zlib zoneinfo
""".split())

_NAME = re.compile(r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?:\(\))?`")
_FILE = re.compile(r"`([A-Za-z0-9_~/*{}<>-][A-Za-z0-9_.~/*{}<>-]*\.(?:py|ts|mjs))`")
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9]*(?:-[a-z0-9]+)*)")
_WORD = re.compile(r"[A-Za-z_]\w*")
_SOURCE_FILE = re.compile(r"[A-Za-z0-9_.-]+\.(?:py|ts|mjs)")
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_FILE_EXTENSIONS = frozenset({"py", "ts", "mjs", "md", "toml", "json", "txt",
                              "sql", "html", "css", "js", "sh", "lock", "yml",
                              "cfg"})
_DATA_SUFFIXES = (".toml", ".json", ".sql", ".html", ".css", ".txt", ".cfg",
                  ".ini", ".yml", ".yaml", ".tmpl")


@dataclass(frozen=True, order=True)
class Cite:
    path: str
    line: int
    kind: str
    text: str


@dataclass(frozen=True, order=True)
class Hit:
    path: str
    line: int
    text: str
    stale: bool = False

    def __str__(self) -> str:
        if self.stale:
            return (f"{self.path}:{self.line}: refs: exception for {self.text!r} is unused; "
                    "remove the .refs-allow line")
        return (f"{self.path}:{self.line}: refs: cites {self.text}, which nothing in the "
                "repository defines; fix the comment, or add the outside name to "
                ".refs-allow")


def _files(root: Path) -> list[str]:
    done = subprocess.run(["git", "ls-files", "-z", "--cached", "--others",
                           "--exclude-standard"], cwd=root,
                          capture_output=True, check=False)
    if done.returncode == 0:
        return sorted(p for p in done.stdout.decode("utf-8", "surrogateescape").split("\0")
                      if p)
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


def _doc_nodes(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                found.add(id(first.value))
    return found


def _cites(text: str, path: str, line: int) -> list[Cite]:
    found = []
    for offset, one in enumerate(text.splitlines() or [text]):
        found.extend(Cite(path, line + offset, "name", match.group(1))
                     for match in _NAME.finditer(one))
        found.extend(Cite(path, line + offset, "file", match.group(1))
                     for match in _FILE.finditer(one))
        found.extend(Cite(path, line + offset, "flag", match.group(1))
                     for match in _FLAG.finditer(one))
    return found


def _allow(root: Path) -> tuple[dict[tuple[str, str], int], list[Hit]]:
    path = root / ALLOW_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}, []
    entries: dict[tuple[str, str], int] = {}
    malformed = []
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3 or not all(parts):
            malformed.append(Hit(ALLOW_FILE, number, line, stale=True))
            continue
        entries[(parts[0], parts[1])] = number
    return entries, malformed


def scan(root: Path | str) -> list[Hit]:
    """Return unresolved comment citations and unused exception lines."""
    root = Path(root)
    paths = _files(root)
    package_paths = [p for p in paths if p.startswith("packages/")
                     and not p.startswith(VENDORED) and (root / p).is_file()]
    identifiers = set(keyword.kwlist) | {"True", "False", "None"}
    flags: set[str] = set()
    source_strings: set[str] = set()
    cites: list[Cite] = []

    for path in package_paths:
        full = root / path
        try:
            text = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if path.endswith(".py"):
            try:
                tree = ast.parse(text, filename=path)
            except SyntaxError:
                continue
            docs = _doc_nodes(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    identifiers.add(node.id)
                elif isinstance(node, ast.Attribute):
                    identifiers.add(node.attr)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    identifiers.add(node.name)
                elif isinstance(node, ast.arg):
                    identifiers.add(node.arg)
                elif isinstance(node, ast.keyword) and node.arg:
                    identifiers.add(node.arg)
                elif isinstance(node, ast.alias):
                    identifiers.update(node.name.split("."))
                    if node.asname:
                        identifiers.add(node.asname)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    identifiers.update(node.module.split("."))
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in docs:
                        cites.extend(_cites(node.value, path, node.lineno))
                    else:
                        identifiers.update(_WORD.findall(node.value))
                        flags.update(_FLAG.findall(node.value))
                        source_strings.update(_SOURCE_FILE.findall(node.value))
            try:
                tokens = tokenize.generate_tokens(io.StringIO(text).readline)
                for token in tokens:
                    if token.type == tokenize.COMMENT:
                        cites.extend(_cites(token.string, path, token.start[0]))
            except (tokenize.TokenError, IndentationError):
                pass
            identifiers.update(Path(path).with_suffix("").parts)
        elif path.endswith((".ts", ".mjs")):
            for match in _COMMENT.finditer(text):
                if match.group(0).startswith("//") and match.start() and text[match.start()-1] == ":":
                    continue
                line = text.count("\n", 0, match.start()) + 1
                cites.extend(_cites(match.group(0), path, line))
            code = _COMMENT.sub(" ", text)
            identifiers.update(re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", code))
            flags.update(_FLAG.findall(code))
            identifiers.update(Path(path).with_suffix("").parts)
        elif path.endswith(_DATA_SUFFIXES):
            identifiers.update(_WORD.findall(text))
            flags.update(_FLAG.findall(text))

    allowed, malformed = _allow(root)
    used: set[tuple[str, str]] = set()
    hits = list(malformed)
    for cite in cites:
        key = (cite.path, cite.text)
        if key in allowed:
            used.add(key)
            continue
        if cite.kind == "name":
            parts = cite.text.split(".")
            if parts[-1] in _FILE_EXTENSIONS:
                continue
            exists = (all(part in identifiers for part in parts)
                      or hasattr(builtins, parts[0]) or parts[0] in STDLIB_MODULE_NAMES)
        elif cite.kind == "file":
            unchecked = (cite.text.startswith(("/", "~"))
                         or any(char in cite.text for char in "*{}<"))
            clean = cite.text.removeprefix("./")
            exists = (unchecked or any(p == clean or p.endswith("/" + clean) for p in paths)
                      or clean.rsplit("/", 1)[-1] in source_strings)
        else:
            exists = cite.text in flags
        if not exists:
            hits.append(Hit(cite.path, cite.line, cite.text))
    for key, number in allowed.items():
        if key not in used:
            hits.append(Hit(ALLOW_FILE, number, key[1], stale=True))
    return sorted(hits)


__all__ = ["ALLOW_FILE", "Cite", "Hit", "STDLIB_MODULE_NAMES", "scan"]
