"""The machine-text check: shipped code, docs and tests name no one's machine.

Moxi is public and runs on Linux, macOS and Windows for people who have never seen the
machine it was written on. The check refuses three kinds of text that hold on one
machine only:

- a home folder: a path rooted at one user's home folder (`/home/<user>`,
  `/var/home/<user>`, `/Users/<user>`, `<drive>:\\Users\\<user>` with either slash),
  unless the user is a placeholder (`<user>`, `$USER`, `${USER}`, `{user}`,
  `%USERNAME%`, `*`), an example user (`you`, `me`, `user`, `username` or `name`, in any
  case, with or without trailing digits) or a folder every machine of that platform has
  (`Public`, `Default`, `Shared`, `All Users`). A word, a dot, a tilde or a hyphen just
  before the root means it isn't one, so a relative path passes. A path from the reader's
  own home (`~/...`) is not this rule's. Tests pass this rule: a test's made-up home is
  data no machine resolves.
- a private name: a name from the running contributor's own private-names list, the one
  `moxi_secrets` keeps in its config, outside every repository. The line never prints the
  name. A list secrets can't read raises `moxi_secrets.SetUnavailable`; an empty one finds
  nothing.
- a package-manager command: one of `MANAGERS` followed by one of `VERBS` (with `sudo`
  and flags allowed between), pacman with its operation flag, or the same as an argv
  list of quoted words. Case-sensitive, since commands are lower case. A sentence that
  only names a manager passes; service managers are not managers here.

The check reads every tracked and untracked-but-not-ignored file that reads as UTF-8,
every line of it, comments included, but those `read` leaves out: `.github/`, the core's
vendored libraries, each package's built page folder (`src/<module>/web/`, written from
its `web/`), the lock files, the rename table, the allow and path lists, and LICENSE
files. A file under a package is a test when a directory below the package is named
`tests` or `test`, or its name is `conftest.py`, starts with `test_` or holds `.test.`;
a test is held to the private-name and package-manager rules only.

A genuine use, a line about one machine or one platform on purpose, is let stand by a
mark on the same line in the house form of an exception mark: `noqa:`, the code `CODE`
names, two hyphens and the reason, in the file's own comment syntax or, on a docstring's
inner line, in the text itself. It lets the line's home-folder and package-manager hits
stand, never a private name: what is public on purpose goes in secrets'
`private_names.allowed`. A mark with no reason is refused, and so is a mark on a line
with no home-folder or package-manager hit. A mark quoted as inline code, in backticks,
is an example of the form and not a mark. A JSON file has no comments, so its lines
take no mark.

`scan(root)` reads the files and returns what it found.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from moxi_secrets import SetUnavailable
from moxi_secrets import scan as secrets_scan

from .contract import CORE
from .contract import VENDORED as _CORE_VENDORED

#: The code an exception mark names to let a home folder or a package manager stand.
CODE = "MX6"
#: secrets' set of the contributor's own private names.
NAMES_SET = "private-names"

# -- the home-folder rule ------------------------------------------------------------------

#: The roots of one user's home folder: Linux (and Fedora's image-based variants), macOS
#: and Windows (one or two backslashes, or a slash). The path pieces are spelled apart so
#: this file passes its own check.
_SLASH = r"(?:\\\\|\\|/)"
HOME_ROOT = re.compile(
    r"(?<![\w.~-])/(?:var/)?" + "ho" + r"me/"
    r"|(?<![\w.~-])/" + "Us" + r"ers/"
    r"|(?<![A-Za-z])[A-Za-z]:" + _SLASH + r"(?i:users)" + _SLASH)
#: The user segment after a root. A placeholder starts with none of these characters.
USER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
#: An example user, the whole segment, in any case.
EXAMPLE_USER = re.compile(r"(?:you|me|user|username|name)\d*", re.I)
#: The folders every machine of a platform has under its home root.
SHARED_HOMES = ("Public", "Default", "Shared", "All Users")

# -- the package-manager rule ----------------------------------------------------------------

#: One platform's package managers.
MANAGERS = ("apt-get", "aptitude", "apt", "dnf", "yum", "zypper", "pacman", "apk", "brew",
            "port", "snap", "flatpak", "choco", "winget", "scoop")
#: The verbs that make a manager's name a command.
VERBS = ("install", "reinstall", "remove", "uninstall", "erase", "purge", "autoremove",
         "upgrade", "update", "search", "info", "add", "del")
_MANAGER = "(?P<manager>" + "|".join(map(re.escape, MANAGERS)) + ")"
_VERB = "(?P<verb>" + "|".join(VERBS) + ")"
#: A manager followed by a verb, `sudo` and flags allowed; or pacman's operation flag.
COMMAND = re.compile(
    r"(?<![\w./-])(?:sudo\s+)?(?:" + _MANAGER + r"\s+(?:--?[\w-]+\s+)*" + _VERB
    + r"(?![\w-])|(?P<pacman>pacman)\s+(?P<flag>-[SRUQ]\w*))")
#: The same as an argv list: a quoted manager, any quoted flags, then a quoted verb.
ARGV = re.compile(r"[\"']" + _MANAGER + r"[\"']\s*,\s*(?:[\"']-[\w-]+[\"']\s*,\s*)*"
                  r"[\"']" + _VERB + r"[\"']")

# -- the mark ------------------------------------------------------------------------------

#: A mark that names `CODE` among its codes, then ` -- ` and a reason.
MARK = re.compile(r"\bnoqa:\s*[A-Z0-9, ]*?\b" + CODE + r"\b[A-Z0-9, ]*?\s+--\s+\S")
#: Any mark that names `CODE`, with or without a reason.
ANY_MARK = re.compile(r"\bnoqa:\s*[A-Z0-9, ]*?\b" + CODE + r"\b")
#: A mark quoted as inline code, an example of the form, which is not a mark itself.
QUOTED_MARK = re.compile(r"`[^`\n]*\bnoqa:[^`\n]*`")

#: The fix each rule's line names.
HOME_FIX = ("write it from the home folder (~/...) or with an example user (/home/you), "
            f"or mark a genuine use # noqa: {CODE} -- <why>")
MANAGER_FIX = ("name what to install and leave the manager to the reader's platform, or "
               f"mark a line written for one platform # noqa: {CODE} -- <the platform>")
NAME_FIX = ("reword it, or add the text to secrets' private_names.allowed if it is "
            "public on purpose")

# -- the files -----------------------------------------------------------------------------

#: The core's directory of vendored copies of released libraries, whose text is not ours.
VENDORED = f"packages/{CORE}/{_CORE_VENDORED}"
#: The rename table, whose old column spells the text it rewrites.
RENAME_TABLE = "packages/moxi-dev/renames.tsv"
#: Files left out wherever they stand: resolver output, and lists that quote the text
#: they let stand.
SKIPPED_NAMES = frozenset({"uv.lock", "package-lock.json", ".residue-allow", ".refs-allow",
                           ".secrets-allow", ".public-paths"})
#: The directory names that make a file below a package a test.
TEST_DIRS = frozenset({"tests", "test"})


@dataclass(frozen=True, order=True)
class Hit:
    path: str
    line: int
    #: "home", "manager" or "name" for a refused text; "reason" for a mark with no
    #: reason; "unused" for a mark on a line with nothing to let stand.
    rule: str
    #: The text the line names: the home folder's root and user, or the manager and its
    #: verb. Empty for a private name, which is never printed, and for a mark's fault.
    text: str = ""

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}: machine text: "
        if self.rule == "home":
            return f"{where}home folder {self.text} — fix: {HOME_FIX}"
        if self.rule == "manager":
            return f"{where}package manager {self.text} — fix: {MANAGER_FIX}"
        if self.rule == "name":
            return f"{where}private name — fix: {NAME_FIX}"
        if self.rule == "reason":
            return f"{where}the {CODE} mark gives no reason; add -- <why> after {CODE}"
        return f"{where}the {CODE} mark lets nothing stand; remove it"


def unreadable(exc: Exception) -> str:
    """The line for a private-names list secrets can't read."""
    return (f"the machine-text step could not read your private-names list ({exc}); "
            "run moxi config secrets check")


def _files(root: Path) -> list[str]:
    done = subprocess.run(["git", "ls-files", "-z", "--cached", "--others",
                           "--exclude-standard"], cwd=root, capture_output=True, check=True)
    return sorted({p for p in done.stdout.decode("utf-8", "surrogateescape").split("\0") if p})


def read(path: str) -> bool:
    """Whether the check reads the file at `path`, relative to the root with `/`."""
    parts = path.split("/")
    name = parts[-1]
    if parts[0] == ".github" or path.startswith(VENDORED) or path == RENAME_TABLE:
        return False
    if name in SKIPPED_NAMES or name.startswith("LICENSE"):
        return False
    # A package's built page folder, packages/<p>/src/<module>/web/.
    return not (len(parts) > 5 and parts[0] == "packages" and parts[2] == "src"
                and parts[4] == "web")


def is_test(path: str) -> bool:
    """Whether the file at `path` is a test: below a package, in a `tests` or `test`
    directory, or named `conftest.py`, `test_*` or `*.test.*`."""
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "packages":
        return False
    name = parts[-1]
    return (any(part in TEST_DIRS for part in parts[2:-1]) or name == "conftest.py"
            or name.startswith("test_") or ".test." in name)


def home_paths(line: str) -> list[str]:
    """Each home folder `line` names that isn't a placeholder, an example user or a shared
    folder, as its root and user."""
    found = []
    for root in HOME_ROOT.finditer(line):
        rest = line[root.end():]
        user = USER.match(rest)
        if user is None or any(rest.startswith(shared) and not USER.match(
                rest[len(shared):]) for shared in SHARED_HOMES):
            continue
        if EXAMPLE_USER.fullmatch(user.group(0)):
            continue
        found.append(root.group(0) + user.group(0))
    return found


def manager_commands(line: str) -> list[str]:
    """Each package-manager command `line` holds, as the manager and its verb."""
    found = []
    for match in COMMAND.finditer(line):
        if match.group("pacman"):
            found.append(f"{match.group('pacman')} {match.group('flag')}")
        else:
            found.append(f"{match.group('manager')} {match.group('verb')}")
    found += [f"{match.group('manager')} {match.group('verb')}"
              for match in ARGV.finditer(line)]
    return found


def private_lines(text: str, path: str) -> set[int]:
    """The lines of `text` that hold a name from the contributor's private-names list.
    Tests replace it with a made-up list."""
    return {hit.line for hit in secrets_scan(text, (NAMES_SET,), path=path)}


def check_text(path: str, text: str) -> list[Hit]:
    """What the check refuses in `text`, the file at `path`: each unmarked home folder (in
    shipped text) and package-manager command, each private name, each mark with no reason
    and each unused mark."""
    shipped = not is_test(path)
    takes_marks = not path.endswith(".json")
    hits, used = [], set()
    marked, bare = set(), set()
    for number, line in enumerate(text.split("\n"), 1):
        if takes_marks:
            unquoted = QUOTED_MARK.sub("", line)
            if MARK.search(unquoted):
                marked.add(number)
            elif ANY_MARK.search(unquoted):
                bare.add(number)
        found = [Hit(path, number, "home", home)
                 for home in (home_paths(line) if shipped else [])][:1]
        found += [Hit(path, number, "manager", command)
                  for command in manager_commands(line)][:1]
        if found and number in marked:
            used.add(number)
        else:
            hits += found
    hits += [Hit(path, number, "name") for number in private_lines(text, path)]
    hits += [Hit(path, number, "reason") for number in bare]
    hits += [Hit(path, number, "unused") for number in marked - used]
    return sorted(hits)


def scan(root: Path | str) -> list[Hit]:
    """Every refused text, mark with no reason and unused mark in the repository at
    `root`. A private-names list secrets can't read raises `SetUnavailable`."""
    root = Path(root)
    hits = []
    for path in _files(root):
        if not read(path) or not (root / path).is_file():
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits += check_text(path, text)
    return sorted(hits)


__all__ = ["ARGV", "CODE", "COMMAND", "HOME_FIX", "HOME_ROOT", "Hit", "MANAGERS",
           "MANAGER_FIX", "MARK", "NAME_FIX", "SetUnavailable", "VERBS", "check_text",
           "home_paths", "is_test", "manager_commands", "private_lines", "read", "scan",
           "unreadable"]
