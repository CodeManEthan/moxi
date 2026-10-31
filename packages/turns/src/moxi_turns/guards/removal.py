"""turns' removal guard: refuse, before it runs, a shell command that Claude Code's own
dangerous-removal check would stop for the user's approval even in bypass mode, where the
agent would sit blocked until someone clicks.

Coarse text rules, no shell parsing. A command is refused when any rule matches, and the
reasons are joined in the order T, W, S, C:

- **T**: a `cd`, `pushd`, `popd` or `chdir` word, an `rm` or `rmdir` word and a `*`, all in
  the raw text, or all in the text with every `'`, `"` and `\\` deleted.
- **W**: with an `rm` or `rmdir` word in either text, every word of the raw text must be a
  plain path, compared the way Claude Code compares paths: case-folded, with
  `/private/tmp` and `/private/var` mapped. A word holding any character outside
  printable ASCII refuses.
- **S**: an `rm` or `rmdir` word and more than 64 substitution openers.
- **C**: a shell name (`bash`, `sh`, `zsh`, ...), a `-c` word and an `rm` or `rmdir` word,
  any case, in the raw text, in the text with every `$` before a quote and then every
  `'`, `"` and `\\` deleted, or in that text with `{` and `}` deleted too.

A word is a run the split class leaves: space, tab, newline, CR, VT, FF, `;`, `&`, `|`,
`(`, `)`, `<`, `>` and the backtick. A whole word is a run not preceded or followed by a
letter, a digit or `_`.

The rules read the call's working directory and the launch's `HOME`. A command that is not
text is let through, and so is every call the guard can't read: a break lets the call
through too (`on_error = "allow"`).

The guard's `match` passes every command any rule needs, a removal word with any run of
`\\`, `'`, `"`, `{`, `}` and `$` between its letters, so a Bash call the filter passes over
never leaves the runner.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping

from . import Deny, ToolCall

#: The Claude Code version the rules were checked against. The doctor warns when the
#: installed Claude Code is newer: its removal check may have moved.
CHECKED_CLAUDE = "2.1.289"

REASON_T = (
    "Refused before it ran (turns removal guard). This command's text mentions a directory "
    "change (cd, pushd, popd or chdir), a removal (rm or rmdir) and a `*`. After a cd, "
    "Claude Code can't tell which directory a relative `*` means, so it stops for the user's "
    "approval even in bypass mode, and you would sit blocked until they click. This guard "
    "checks text only, so it also refuses when those words are just in a string or in file "
    "content. Two fixes:\n"
    "(1) Drop the cd and use full paths, such as `rm -rf ~/tmp/x/*` for a scratch directory "
    "under your home, and `git -C DIR`, `tar -C DIR` or `make -C DIR` for the other steps. "
    "Don't point rm at your working directory or a directory above it: Claude Code stops on "
    "that too.\n"
    "(2) If the text is file content (a heredoc or an echo into a file), write the file with "
    "the Write tool instead of Bash."
)
REASON_W = (
    "Refused before it ran (turns removal guard): you are removing your working directory or "
    "a directory above it; name a narrower path, or ask the user if you mean it. In a command "
    "with rm or rmdir, this guard lets through only plain paths: a plain relative `name/*` "
    "such as `sub/*`, a word with no `*`, `$`, backtick or backslash, or an absolute (or "
    "`~/`) path below a top-level directory. Anything else (a variable, a quoted or relative "
    "glob, `..`, a path naming /, a top-level directory or your home) is refused, because "
    "Claude Code stops many of these for the user's approval even in bypass mode and this "
    "guard can't tell which. Write a literal path in plain ASCII, absolute or from `~/`, "
    "such as `rm -rf ~/tmp/x/*` for a scratch directory under your home. This guard checks "
    "text only, so it also refuses when such a word is just in a string, in file content or "
    "another command's argument: then write the file with the Write tool, or put the rm in a "
    "command of its own."
)
REASON_S = (
    "Refused before it ran (turns removal guard): this command has rm or rmdir and more than "
    "64 command substitutions. Claude Code stops that for the user's approval even in bypass "
    "mode. Split it into smaller commands, or write a script file with the Write tool and "
    "run it with `bash /full/path/script.sh`."
)
REASON_C = (
    "Refused before it ran (turns removal guard, shell -c rule). This command runs a shell "
    "with -c (bash, sh, zsh and the like), and its text also has rm or rmdir. Claude Code "
    "often can't check such a script for dangerous removals, and then it stops for the "
    "user's approval even in bypass mode, and you would sit blocked until they click. This "
    "guard checks text only, so it also refuses when the rm is outside the script or just in "
    "a string. Fix: write the script to a file with the Write tool (for example "
    "`~/tmp/<your-task>/run.sh`, a scratch directory under your home) and run it with "
    "`bash ~/tmp/<your-task>/run.sh ARGS`. Keep any wrapper in front, as in `timeout 60 bash "
    "/full/path/run.sh` or `env -i PATH=/usr/bin:/bin bash /full/path/run.sh`, and pass "
    "values as arguments. Don't wrap it in bash -c again. If the -c belongs to another "
    "program (head -c, tail -c, wc -c, git -c), use its long option (--bytes) or run that "
    "part as its own Bash call. For python3 -c or node -e, write a .py or .mjs file and run "
    "it with python3 or node. If the match is only in a string or file content, write that "
    "file with the Write tool instead of Bash."
)
#: Each rule's letter and its reason, in the order reasons are joined.
REASONS = {"T": REASON_T, "W": REASON_W, "S": REASON_S, "C": REASON_C}
#: How reasons are joined into one refusal.
JOIN = "\n\n"

# Rule T's searches.
CD = re.compile(r"(?<![A-Za-z0-9_])(cd|pushd|popd|chdir)(?![A-Za-z0-9_])")
RM = re.compile(r"(?<![A-Za-z0-9_])(rm|rmdir)(?![A-Za-z0-9_])")
RMDIR = re.compile(r"(?<![A-Za-z0-9_])rmdir(?![A-Za-z0-9_])")
# Rule W's words: the split class is exactly these characters; no other splits a word.
SPLIT = re.compile("[ \t\n\r\x0b\x0c;&|()<>`]+")
# A word passes only if every character is printable ASCII (0x21 to 0x7E).
PRINTABLE = re.compile(r"[\x21-\x7e]+")
PART_I = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
PART_III = re.compile(r"[A-Za-z0-9_.-][A-Za-z0-9._-]*")
STARS = re.compile(r"\*+")
STAR_DOLLAR_TICK_BSLASH = re.compile(r"[*$`\\]")
DOLLAR_TICK_BSLASH = re.compile(r"[$`\\]")
TRAILING_STARS = re.compile(r"(/\*+)+/*$")
HOME_PREFIXES = ("${HOME}", "$HOME", "~")
# Claude Code's path fold: the /private alias, then lower case.
PRIVATE_VAR = re.compile(r"^/private/var/", re.I)
PRIVATE_TMP = re.compile(r"^/private/tmp(/|$)", re.I)
# Rule S's openers besides `$(`, `<(`, `>(` and the backtick.
BRACE_OPENER = re.compile(r"\$\{[ \t\n|]")
# Rule C's second text: a `$` right before a quote, then every quote and backslash, deleted.
T3_DELETE = re.compile(r"\$(?=['\"])|['\"\\]")
# A shell name, any case, not preceded by a letter, digit, `_`, `.` or `-`, not followed
# by a letter, digit or `_`.
SHELL_NAME = re.compile(r"(?<![A-Za-z0-9_.-])(bash|sh|zsh|dash|ksh|mksh|ash|hush|rbash)"
                        r"(?![A-Za-z0-9_])", re.I)
# A -c word: a `-` that starts a word (split on whitespace, ; & | ( ) { } < > ! and the
# backtick) or follows `=`, not followed by another `-`, with a `c` before the word ends.
C_WORD = re.compile(r"(?:^|(?<=[\s;&|(){}<>!`=]))-(?!-)[^\s;&|(){}<>!`]*")
# Rule C's removal word: any case, not preceded by a letter, digit or `_`, not followed by
# a letter or `_`. Its own letters are ASCII only, so the guard's `match` passes every one;
# a letter beside it is ASCII or one of the four that fold into ASCII (dotted and dotless
# i, long s, the Kelvin sign), as a case-blind Python search reads them.
_FOLDS = "İıſK"
C_REMOVAL = re.compile(rf"(?<![A-Za-z0-9_{_FOLDS}])([rR][mM]|[rR][mM][dD][iI][rR])"
                       rf"(?![A-Za-z_{_FOLDS}])")

_NEEDS_HOME = object()
_NO_CWD = object()


def _unquote(w: str) -> str:
    return w.replace("'", "").replace('"', "")


def _normpath(p: str) -> str:
    return "/" + posixpath.normpath(p).lstrip("/")


def _resolve(u: str, cwd: str | None, home: str | None):
    """A word, its quotes already deleted, as the path it names."""
    for h in HOME_PREFIXES:
        if u == h or u.startswith(h + "/"):
            if home is None:
                return _NEEDS_HOME
            u = home + u[len(h):]
            break
    if not u.startswith("/"):
        if cwd is None:
            return _NO_CWD
        u = cwd + "/" + u
    u = "/" + u.lstrip("/")
    prev = None
    while u != prev:
        prev = u
        u = TRAILING_STARS.sub("", u) or "/"
        u = _normpath(u)
    return u


def _fold(p: str) -> str:
    p = PRIVATE_TMP.sub(r"/tmp\1", PRIVATE_VAR.sub("/var/", p))
    return p.lower().replace("ı", "i").replace("ſ", "s")


def _with_parents(p: str) -> list[str]:
    out = []
    while True:
        out.append(p)
        if p == "/":
            return out
        p = posixpath.dirname(p)


def _too_wide(u: str, cwd: str | None, home: str | None) -> bool:
    """The path names /, a top-level directory, the home, or the working directory or a
    directory above it."""
    r = _resolve(u, cwd, home)
    if r is _NEEDS_HOME:
        return True
    if r is _NO_CWD:
        return all(p in (".", "..", "") or STARS.fullmatch(p) for p in u.split("/"))
    f = _fold(r)
    if "/" in (r, f, posixpath.dirname(r), posixpath.dirname(f)):
        return True
    if home is not None and f == _fold(home):
        return True
    if cwd is not None:
        workspace = {_fold(p) for p in _with_parents(cwd)} | set(_with_parents(_fold(cwd)))
        if f in workspace:
            return True
    return False


def _relative_glob(w: str, cwd: str | None, home: str | None) -> bool:
    """A plain relative `name/*`."""
    parts = w.split("/")
    if len(parts) < 2 or parts[-1] != "*":
        return False
    if not all(PART_I.fullmatch(p) for p in parts[:-1]):
        return False
    return not _too_wide(w, cwd, home)


def _plain_word(w: str, cwd: str | None, home: str | None) -> bool:
    """A word with no `*`, `$`, backtick or backslash."""
    if STAR_DOLLAR_TICK_BSLASH.search(w):
        return False
    return not _too_wide(_unquote(w), cwd, home)


def _plain_part(p: str) -> bool:
    return bool(PART_III.fullmatch(p)) and p not in (".", "..")


def _rooted_path(w: str, cwd: str | None, home: str | None) -> bool:
    """An absolute or home path of plain parts, its last part plain or only stars."""
    u = _unquote(w)
    for pre in ("${HOME}/", "$HOME/", "~/", "/"):
        if u.startswith(pre):
            rest = u[len(pre):]
            if DOLLAR_TICK_BSLASH.search(rest):
                return False
            parts = rest.split("/")
            if not all(_plain_part(p) for p in parts[:-1]):
                return False
            if not (_plain_part(parts[-1]) or STARS.fullmatch(parts[-1])):
                return False
            return not _too_wide(u, cwd, home)
    return False


def _word_passes(w: str, cwd: str | None, home: str | None, has_rmdir: bool) -> bool:
    if not PRINTABLE.fullmatch(w):
        return False
    if has_rmdir and "*" in w:
        return False
    return (_relative_glob(w, cwd, home) or _plain_word(w, cwd, home)
            or _rooted_path(w, cwd, home))


def openers(raw: str) -> int:
    """Rule S's count of substitution openers."""
    return (raw.count("$(") + raw.count("<(") + raw.count(">(") + raw.count("`")
            + len(BRACE_OPENER.findall(raw)))


def shell_c(raw: str) -> bool:
    """Rule C's gate: a shell name in the raw text or the text with `$`-before-quote,
    quotes and backslashes deleted, and a -c word in either."""
    texts = (raw, T3_DELETE.sub("", raw))
    return (any(SHELL_NAME.search(t) for t in texts)
            and any("c" in m.group(0) for t in texts for m in C_WORD.finditer(t)))


def c_removal(raw: str) -> bool:
    """Rule C's removal word, in either of the gate's texts or the second with braces
    deleted."""
    t3 = T3_DELETE.sub("", raw)
    return any(C_REMOVAL.search(t) for t in (raw, t3, t3.replace("{", "").replace("}", "")))


def rules(raw: str, cwd: str | None, home: str | None) -> str:
    """The rules `raw` breaks, as their letters in the order T, W, S, C; empty when
    none. `cwd` and `home` are absolute and normalized, or None when unknown."""
    texts = (raw, re.sub(r"['\"\\]", "", raw))
    out = ""
    if any(CD.search(t) and RM.search(t) and "*" in t for t in texts):
        out += "T"
    if any(RM.search(t) for t in texts):
        has_rmdir = any(RMDIR.search(t) for t in texts)
        if not all(_word_passes(w, cwd, home, has_rmdir) for w in SPLIT.split(raw) if w):
            out += "W"
        if openers(raw) > 64:
            out += "S"
    if shell_c(raw) and c_removal(raw):
        out += "C"
    return out


def _absolute(p: object) -> str | None:
    if isinstance(p, str) and p.startswith("/"):
        return _normpath(p)
    return None


def refusal(command: str, cwd: object = None, env: Mapping[str, str] | None = None
            ) -> str | None:
    """The refusal for `command` run in `cwd` under `env`'s `HOME`, or None to let it run.
    A `cwd` or `HOME` that is not an absolute path counts as unknown."""
    found = rules(command, _absolute(cwd), _absolute((env or {}).get("HOME") or None))
    return JOIN.join(REASONS[r] for r in found) if found else None


def judge(call: ToolCall) -> Deny | None:
    """The guard's judge: a Bash call's `command`, refused when a rule matches."""
    command = call.input.get("command") if isinstance(call.input, Mapping) else None
    if not isinstance(command, str):
        return None
    reason = refusal(command, call.cwd, call.env)
    return Deny(reason) if reason else None


__all__ = ["CHECKED_CLAUDE", "REASON_T", "REASON_W", "REASON_S", "REASON_C", "REASONS",
           "JOIN", "openers", "shell_c", "c_removal", "rules", "refusal", "judge"]
