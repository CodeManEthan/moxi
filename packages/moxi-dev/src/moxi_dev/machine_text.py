"""The machine-text check: shipped code, docs and tests name no one's machine.

Moxi is public and runs on Linux, macOS and Windows for people who have never seen the
machine it was written on. The check refuses three kinds of text that hold on one
machine only:

- a home folder: a path rooted at one user's home folder (`/home/<user>`,
  `/var/home/<user>`, `/Users/<user>`, `<drive>:\\Users\\<user>` with either slash, and
  WSL's `/mnt/<drive>/Users/<user>`), unless the user is a placeholder (`<user>`,
  `$USER`, `${USER}`, `{user}`, `%USERNAME%`, `*`), an example user (`you`, `me`, `user`,
  `username` or `name`, in any case, with or without trailing digits) or a folder that
  is no one person's: a shared folder every machine of a platform ships (Windows'
  `Public`, `Default` and `All Users`, macOS' `Shared` and `Guest`, and `linuxbrew`, the
  account Homebrew on Linux installs into), as the whole segment, in any case under a
  Windows root; `Public.x` or `Guest-ann` is a user's name. A user
  starts with a letter or a digit in any script. A word, a dot, a tilde or a hyphen just
  before the root means it isn't one, so a relative path passes. A path from the
  reader's own home (`~/...`) is not this rule's. Tests pass this rule: a test's made-up
  home is data no machine resolves.
- a private name: a name from the running contributor's own private-names list, the one
  `moxi_secrets` keeps in its config, outside every repository. No line the check prints
  names it: the private-name line names no text, on a line that holds one the
  home-folder line names the root alone, and a segment of a file's path that holds one
  prints as `<private name>`. A list secrets can't read raises
  `moxi_secrets.SetUnavailable`; an empty one finds nothing.
- a package-manager command: one of `MANAGERS`, called by its name or from a folder
  (`/usr/bin/apt-get`, `C:\\...\\choco.exe`), followed by one of `VERBS` (options allowed
  between, with their values: `-o K=V`, `-t <release>`, `--setopt=x`), or pacman with
  its operation flag, anywhere on a line: in a sentence, a string or a comment. The same
  as an argv list of quoted words counts too.
  Case-sensitive, since commands are lower case. A sentence that only names a manager
  passes; service managers are not managers here. The managers whose name is also an
  ordinary word (`WORD_MANAGERS`: port, snap, apk) count only where a command starts: at
  the line's start (past a comment's opening, a shell prompt such as `$ `, `% ` or
  `PS C:\\> `, a Dockerfile's `RUN ` and any `NAME=value` assignments), or after `sudo`
  and its options (`sudo -u root `), `&&`, `||`, `;`, `&`, `|` or a backtick that opens
  inline code, never one that closes it; as an argv list, when the quoted manager comes
  first. A backtick opens inline code where the next run of as many closes it on the
  line, and when it is the line's last run that nothing pairs, as a span wrapped onto
  the next line leaves. So "each port update is logged", "the `host` port update is
  logged" and `("host", "port", "update")` pass.

The check reads every tracked and untracked-but-not-ignored file that reads as UTF-8,
every line of it, comments included, lines numbered as git numbers them (a newline alone
ends one), but those `is_scanned` leaves out: `.github/`, the core's
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
is an example of the form and not a mark; `marks.py` reads the mark, as it does for the
pronoun step. A JSON file has no comments, so its lines take no mark.

`scan(root)` reads the files and returns what it found.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

from moxi_secrets import SetUnavailable
from moxi_secrets import scan as secrets_scan

from .contract import CORE
from .contract import VENDORED as _CORE_VENDORED
from .marks import CODE_SPAN, marked_lines

#: The code an exception mark names to let a home folder or a package manager stand.
CODE = "MX6"
#: secrets' set of the contributor's own private names.
NAMES_SET = "private-names"
#: The path a file's path is scanned at for private names, as publishing scans a path.
NAME_PATH = "@path"
#: What a segment of a file's path that holds a private name prints as.
HIDDEN = "<private name>"

# -- the home-folder rule ------------------------------------------------------------------

#: The roots of one user's home folder: Linux (and Fedora's image-based variants), macOS,
#: Windows (one or two backslashes, or a slash) and Windows seen from WSL. The path pieces
#: are spelled apart so this file passes its own check.
_SLASH = r"(?:\\\\|\\|/)"
_LINUX_ROOT = r"(?<![\w.~-])/(?:var/)?" + "ho" + r"me/"
_MACOS_ROOT = r"(?<![\w.~-])/" + "Us" + r"ers/"
_WINDOWS_ROOT = r"(?<![A-Za-z])[A-Za-z]:" + _SLASH + r"(?i:users)" + _SLASH
_WSL_ROOT = r"(?<![\w.~-])/mnt/[A-Za-z]/(?i:users)/"
HOME_ROOT = re.compile(
    _LINUX_ROOT + "|" + _MACOS_ROOT + "|(?P<windows>" + _WINDOWS_ROOT + "|" + _WSL_ROOT + ")")
#: The user segment after a root: a letter or a digit, in any script, then letters, digits,
#: `.`, `_` and `-`. A placeholder starts with none of these characters.
USER = re.compile(r"[^\W_][\w.-]*")
#: An example user, the whole segment, in any case.
EXAMPLE_USER = re.compile(r"(?:you|me|user|username|name)\d*", re.I)
#: The folders under a home root that are no one person's: Windows' shared and template
#: profiles, macOS' shared folder and guest account, and the account Linuxbrew installs
#: into. Each is the whole segment; under a Windows root they pass in any case, as Windows
#: reads them.
SHARED_USERS = ("Public", "Default", "All Users", "Shared", "Guest", "linuxbrew")
#: What, after a shared folder's name, makes the segment a longer name (`Public.x`,
#: `Guest-ann`, `Shared_1`): more of a name's characters ending in a letter, digit or `_`.
#: A `.` or `-` alone after it ends a sentence or a phrase.
_LONGER_NAME = re.compile(r"[\w.-]*\w")

# -- the package-manager rule ----------------------------------------------------------------

#: One platform's package managers.
MANAGERS = ("apt-get", "aptitude", "apt", "dnf", "yum", "zypper", "pacman", "apk", "brew",
            "port", "snap", "flatpak", "choco", "winget", "scoop")
#: The managers whose name is also an ordinary word: they and a verb make a command only
#: where a command starts, since "each port update is logged" is prose.
WORD_MANAGERS = ("apk", "port", "snap")
#: The verbs that make a manager's name a command.
VERBS = ("install", "reinstall", "remove", "uninstall", "erase", "purge", "autoremove",
         "upgrade", "update", "search", "info", "add", "del", "dist-upgrade", "full-upgrade",
         "groupinstall", "groupremove")
#: No word, path (either slash) or flag runs on into a manager's name or its folder.
_ALONE = r"(?<![\w./\\-])"
#: Where a command starts: the line's start, past a comment's opening (`#`, `//`, `/*`,
#: `*`, `<!--`), a shell prompt (`$ `, `% `, `# `, `> `, `PS C:\> `), a Dockerfile's
#: `RUN ` and its flags, and any variable assignments (`NAME=value `); after `&&`, `||`,
#: `;`, `&` or `|`; after a backtick, which `manager_commands` keeps only where it opens
#: inline code (the `code` group, read by `_openings`); or after `sudo` and its options,
#: with their values.
_COMMENT = r"(?:(?://|/\*+|\*|<!--|#)\s*)?"
_PROMPT = r"(?:(?:PS\s+)?[^\s$%#>]*[$%#>]\s+)?"
_RUN = r"(?:RUN(?:\s+--[\w-]+(?:=\S*)?)*\s+)?"
#: sudo's options that take a value: `-u root`, `-uroot`, `--user=root`, `--user root`. A
#: value never starts with `-`, and a flag is never one of these with its value joined on,
#: so no two readings of sudo's options take the same words.
_SUDO_VALUED = (r"-[CDghpRrTtUu]\s*[^\s-]\S*|--(?:chdir|chroot|close-from|command-timeout|"
                r"group|host|other-user|prompt|role|type|user)(?:=\S+|\s+[^\s-]\S*)")
_SUDO_FLAG = r"(?!-[CDghpRrTtUu]\S)-[\w-]+"
_SUDO = _ALONE + r"sudo(?:\s+(?:" + _SUDO_VALUED + "|" + _SUDO_FLAG + r"))*\s+"
#: A run of backticks.
_TICKS = re.compile(r"`+")
_START = (r"(?:^\s*" + _COMMENT + _PROMPT + _RUN + r"(?:\w+=\S*\s+)*|(?<=[;&|])\s*|"
          r"(?<=`)(?P<code>)\s*|" + _SUDO + ")")
#: The folder a manager is called from: an absolute path (`/usr/bin/`), one from the home
#: folder (`~/`) or from a drive (`C:\...\`).
_CALLED_FROM = r"(?:(?:~|[A-Za-z]:)?(?:" + _SLASH + r"[\w.-]+)*" + _SLASH + ")?"
#: The options between a manager and its verb, with their values: `--yes`, `--setopt=x`,
#: `-o K=V`, `--option K=V`, and apt-get's `-t <release>`, `-c <file>`, `-a <arch>`. An
#: option is one or two dashes and a letter or digit, a value never starts with `-`, and
#: apt-get's four are read by the first alternative alone, so no two readings take the
#: same words and a long run of options with no verb fails in time linear in the line.
_OPTION = (r"(?:-[acot](?:\s+[^\s-]\S*)?|(?!-[acot](?!\S))-{1,2}[A-Za-z0-9][\w-]*(?:=\S*)?"
           r"(?:\s+[^\s=-][^\s=]*=\S*)?)")
_FLAGS = r"\s+(?:" + _OPTION + r"\s+)*"
_VERB = "(?P<verb>" + "|".join(map(re.escape, VERBS)) + ")"
_QUOTE = r"[\"']"
_ARGV_VALUE = r"(?:" + _QUOTE + r"[^\s\"'=-][^\s\"'=]*=[^\"']*" + _QUOTE + r"\s*,\s*)?"
_ARGV_FLAGS = (r"\s*,\s*(?:" + _QUOTE + r"-[\w-]+(?:=[^\"']*)?" + _QUOTE + r"\s*,\s*"
               + _ARGV_VALUE + ")*")


def _manager(names: tuple[str, ...]) -> str:
    """A pattern for one of `names`, called by its name or with `.exe`."""
    return "(?P<manager>" + "|".join(map(re.escape, names)) + r")(?:\.exe)?"


_OTHER = _manager(tuple(name for name in MANAGERS if name not in WORD_MANAGERS))
_WORD = _manager(WORD_MANAGERS)
#: A manager whose name is no ordinary word, followed by a verb, flags allowed between,
#: anywhere on the line; or pacman with its operation flag.
COMMAND = re.compile(
    _ALONE + "(?:" + _CALLED_FROM + _OTHER + _FLAGS + _VERB
    + r"(?![\w-])|(?P<pacman>pacman)\s+(?P<flag>-[SRUQ]\w*))")
#: A `WORD_MANAGERS` manager followed by a verb, flags allowed between, where a command
#: starts.
PLACED_COMMAND = re.compile(_START + _CALLED_FROM + _WORD + _FLAGS + _VERB + r"(?![\w-])")
#: The same as an argv list: a quoted manager, any quoted flags, then a quoted verb,
#: anywhere on the line.
ARGV = re.compile(_QUOTE + _CALLED_FROM + _OTHER + _QUOTE + _ARGV_FLAGS
                  + _QUOTE + _VERB + _QUOTE)
#: A `WORD_MANAGERS` argv list counts when the quoted manager comes first in the list
#: (or after a quoted `sudo`).
PLACED_ARGV = re.compile(
    r"[\[(]\s*(?:" + _QUOTE + "sudo" + _QUOTE + r"\s*,\s*)?" + _QUOTE + _CALLED_FROM
    + _WORD + _QUOTE + _ARGV_FLAGS + _QUOTE + _VERB + _QUOTE)

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
    #: The text the line names: the home folder's root and user (the root alone on a line
    #: holding a private name, since the user may be the name), or the manager and its
    #: verb. Empty for a private name, which is never printed, and for a mark's fault.
    text: str = ""
    #: The path as the line prints it, a segment holding a private name hidden; the path
    #: itself when empty.
    shown_path: str = field(default="", compare=False)

    def __str__(self) -> str:
        where = f"{self.shown_path or self.path}:{self.line}: machine text: "
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


def is_scanned(path: str) -> bool:
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


def _is_shared(rest: str, *, windows: bool) -> bool:
    """Whether `rest`, the text after a home root, starts with a `SHARED_USERS` folder as
    the whole segment, in any case under a Windows root."""
    for shared in SHARED_USERS:
        head = rest[:len(shared)]
        same = head.casefold() == shared.casefold() if windows else head == shared
        if same and not _LONGER_NAME.match(rest[len(shared):]):
            return True
    return False


def user_dirs(line: str) -> list[str]:
    """Each home folder `line` names that isn't a placeholder, an example user or a shared
    folder, as its root and user."""
    found = []
    for root in HOME_ROOT.finditer(line):
        rest = line[root.end():]
        user = USER.match(rest)
        if user is None or _is_shared(rest, windows=root.group("windows") is not None):
            continue
        if EXAMPLE_USER.fullmatch(user.group(0)):
            continue
        found.append(root.group(0) + user.group(0))
    return found


def _openings(line: str) -> set[int]:
    """Where inline code opens on `line`, as the offset just past its backticks: each run
    `CODE_SPAN` pairs as opening, and the line's last unpaired run, which a span wrapped
    onto the next line or left unclosed opens. A run that closes a span opens none."""
    spans = list(CODE_SPAN.finditer(line))
    paired = {run for span in spans for run in (span.start(1), span.end() - len(span.group(1)))}
    unpaired = [run.end() for run in _TICKS.finditer(line) if run.start() not in paired]
    return {span.end(1) for span in spans} | {*unpaired[-1:]}


def manager_commands(line: str) -> list[str]:
    """Each package-manager command `line` holds, as the manager and its verb, in the
    order they stand."""
    opened = _openings(line)
    matches = [match for pattern in (COMMAND, PLACED_COMMAND, ARGV, PLACED_ARGV)
               for match in pattern.finditer(line)
               if match.groupdict().get("code") is None or match.start("code") in opened]
    found = []
    for match in sorted(matches, key=lambda match: match.start()):
        if match.groupdict().get("pacman"):
            found.append(f"{match.group('pacman')} {match.group('flag')}")
        else:
            found.append(f"{match.group('manager')} {match.group('verb')}")
    return found


def private_spans(text: str, path: str) -> list[tuple[int, int]]:
    """Where `text`, at `path`, holds a name from the contributor's private-names list, as
    start and end offsets. Tests replace it with a made-up list."""
    return [(hit.start, hit.end) for hit in secrets_scan(text, (NAMES_SET,), path=path)]


def private_lines(text: str, path: str) -> set[int]:
    """The lines of `text` that hold a private name, numbered by newline alone."""
    return {text.count("\n", 0, start) + 1 for start, _ in private_spans(text, path)}


def shown_path(path: str) -> str:
    """`path` as the check prints it: each `/`-separated segment that holds a private name,
    or part of one, as `HIDDEN`."""
    spans = private_spans(path, NAME_PATH)
    if not spans:
        return path
    shown, start = [], 0
    for segment in path.split("/"):
        end = start + len(segment)
        hidden = any(first < end and last > start for first, last in spans)
        shown.append(HIDDEN if hidden else segment)
        start = end + 1
    return "/".join(shown)


def check_text(path: str, text: str) -> list[Hit]:
    """What the check refuses in `text`, the file at `path`: each unmarked home folder (in
    shipped text) and package-manager command, each private name, each mark with no reason
    and each unused mark."""
    shipped = not is_test(path)
    takes_marks = not path.endswith(".json")
    named = private_lines(text, path)
    hits, used = [], set()
    marked, bare = marked_lines(text, CODE) if takes_marks else (set(), set())
    for number, line in enumerate(text.split("\n"), 1):
        found_dirs = user_dirs(line) if shipped else []
        if number in named:
            found_dirs = [HOME_ROOT.match(found_dir).group(0) for found_dir in found_dirs]
        found = [Hit(path, number, "home", found_dir) for found_dir in found_dirs][:1]
        found += [Hit(path, number, "manager", command)
                  for command in manager_commands(line)][:1]
        if found and number in marked:
            used.add(number)
        else:
            hits += found
    hits += [Hit(path, number, "name") for number in named]
    hits += [Hit(path, number, "reason") for number in bare]
    hits += [Hit(path, number, "unused") for number in marked - used]
    if hits and (shown := shown_path(path)) != path:
        hits = [replace(hit, shown_path=shown) for hit in hits]
    return sorted(hits)


def scan(root: Path | str) -> list[Hit]:
    """Every refused text, mark with no reason and unused mark in the repository at
    `root`. A private-names list secrets can't read raises `SetUnavailable`."""
    root = Path(root)
    hits = []
    for path in _files(root):
        if not is_scanned(path) or not (root / path).is_file():
            continue
        try:
            # Bytes, so no newline is translated and lines number as git numbers them.
            text = (root / path).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits += check_text(path, text)
    return sorted(hits)


__all__ = ["ARGV", "CODE", "COMMAND", "HIDDEN", "HOME_FIX", "HOME_ROOT", "Hit", "MANAGERS",
           "MANAGER_FIX", "NAME_FIX", "PLACED_ARGV", "PLACED_COMMAND", "SetUnavailable",
           "VERBS", "WORD_MANAGERS", "check_text",
           "is_scanned", "is_test", "manager_commands", "private_lines", "private_spans",
           "scan", "shown_path", "unreadable", "user_dirs"]
