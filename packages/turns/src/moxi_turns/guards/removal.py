"""turns' removal guard: refuse, before it runs, a shell command that Claude Code's own
dangerous-removal check would stop for the user's approval even in bypass mode, where the
agent would sit blocked until someone clicks. Claude Code 2.1.316 refuses such a stop by
itself after about two minutes (1:45 on its countdown) when nobody answers, so an
unattended agent loses that long and then the command.

Coarse text rules, no shell parsing. A command is refused when any rule matches, and the
reasons are joined in the order T, W, S, C. Every rule reads the command with the body of
each file-writing here-document cut out (below):

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

The heredoc cut: before any rule reads the command, the body of a here-document is cut out
when its delimiter is quoted (`<<'X'`, `<<"X"` or `<<\\X`, with or without `-` and a space
before the delimiter), the command before its `<<` is a file writer, no `|` or `|&` follows
later on the operator's line, and a closing line ends the body (the delimiter alone on a
line, or after leading tabs with `<<-`). A file writer is `cat` or `tee`, or `python3` or
`node` with a `-` argument, by base name, after any leading `{`, `!`, `do`, `then`, `else`
and `time` words and `NAME=value` assignments. The scan follows single and double quotes,
backslashes, `$'...'` and comments, so an operator inside a string or a comment does not
count; `<<<` is a here-string. Every here-document body is stepped over, cut or not, so its
text can't open a quote. Everything else is kept: an unquoted delimiter, a body fed to
anything else (`bash`, `sh -s`, `sudo cat`, `python3` with no `-`), a body with no closing
line, and a closing line indented without `<<-`.

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
CHECKED_CLAUDE = "2.1.325"

REASON_T = (
    "Refused before it ran (turns removal guard). This command's text mentions a directory "
    "change (cd, pushd, popd or chdir), a removal (rm or rmdir) and a `*`. After a cd, "
    "Claude Code can't tell which directory a relative `*` means, so it stops for the user's "
    "approval even in bypass mode: you would sit blocked until they click, or, with nobody "
    "there, about two minutes before Claude Code refuses it anyway. This guard checks text "
    "only, so it also refuses when those words are just in a string or in file content. "
    "Two fixes:\n"
    "(1) Drop the cd and use full paths, such as `rm -rf ~/tmp/x/*` for a scratch directory "
    "under your home, and `git -C DIR`, `tar -C DIR` or `make -C DIR` for the other steps. "
    "Don't point rm at your working directory or a directory above it: Claude Code stops on "
    "that too.\n"
    "(2) If the text is file content, write the file with the Write tool instead of Bash, or "
    "feed it to cat through a heredoc whose delimiter is quoted: the guard skips only the "
    "body of a quoted heredoc (<<'EOF') fed to cat, tee, python3 - or node -."
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
    "user's approval even in bypass mode: you would sit blocked until they click, or, with "
    "nobody there, about two minutes before Claude Code refuses it anyway. This guard checks "
    "text only, so it also refuses when the rm is outside the script or just in a string. "
    "Fix: write the script to a file with the Write tool (for example "
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
QUOTES_DELETED = re.compile(r"\$(?=['\"])|['\"\\]")
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

# A here-document operator after its <<: an optional -, then the delimiter, quoted ('X',
# "X", \X) or bare.
HEREDOC = re.compile(r"(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\([A-Za-z0-9_]+)|([A-Za-z0-9_]+))")
# The words that may lead a command before its name.
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
LEADERS = ("{", "!", "do", "then", "else", "time")

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


def feeds_a_file(cmd: str) -> bool:
    """The command text before a here-document operator is a file writer: `cat` or `tee`,
    or `python3` or `node` with a `-` argument, after its leading words and assignments."""
    words = cmd.split()
    while words and (words[0] in LEADERS or ASSIGNMENT.match(words[0])):
        words.pop(0)
    if not words:
        return False
    name = words[0].rsplit("/", 1)[-1]
    return name in ("cat", "tee") or (name in ("python3", "node") and "-" in words[1:])


def _closing_line(raw: str, start: int, delim: str, tabs: bool) -> tuple[int, int] | None:
    """(where the body that starts at `start` ends, where the text after its closing line
    starts), or None when no line closes it."""
    j = start
    while True:
        k = raw.find("\n", j)
        line = raw[j:] if k < 0 else raw[j:k]
        if (line.lstrip("\t") if tabs else line) == delim:
            return j, (len(raw) if k < 0 else k + 1)
        if k < 0:
            return None
        j = k + 1


def cut_heredocs(raw: str) -> str:
    """`raw` with the body of each quoted here-document fed to a file writer cut out."""
    keep, last = [], 0
    i, n = 0, len(raw)
    quote = None
    cmd_start = 0
    pending: list[list] = []  # here-documents opened on this line: [delimiter, tabs, cut]
    while i < n:
        c = raw[i]
        if quote:
            if c == "\\" and quote != "'":
                i += 2
                continue
            if (c == "'" and quote in ("'", "$'")) or (c == '"' and quote == '"'):
                quote = None
            i += 1
        elif c == "\\":
            i += 2
        elif c in "'\"":
            quote = c
            i += 1
        elif raw.startswith("$'", i):
            quote = "$'"
            i += 2
        elif c == "#" and (i == 0 or raw[i - 1] in " \t\n;&|()"):
            j = raw.find("\n", i)
            i = n if j < 0 else j
        elif raw.startswith("<<<", i):
            i += 3
        elif raw.startswith("<<", i):
            m = HEREDOC.match(raw, i + 2)
            if m:
                quoted = m.group(5) is None
                delim = next(g for g in m.group(2, 3, 4, 5) if g is not None)
                pending.append([delim, m.group(1) == "-",
                                quoted and feeds_a_file(raw[cmd_start:i])])
                i = m.end()
            else:
                i += 2
        elif raw.startswith("||", i):
            cmd_start = i = i + 2
        elif c == "|":
            for h in pending:
                h[2] = False
            cmd_start = i = i + 1
        elif c in ";&()":
            cmd_start = i = i + 1
        elif c == "\n":
            cmd_start = i = i + 1
            for delim, tabs, cut in pending:
                found = _closing_line(raw, i, delim, tabs)
                if found is None:
                    break
                body_end, after = found
                if cut:
                    keep.append(raw[last:i])
                    last = body_end
                cmd_start = i = after
            pending = []
        else:
            i += 1
    keep.append(raw[last:])
    return "".join(keep)


def openers(raw: str) -> int:
    """Rule S's count of substitution openers."""
    return (raw.count("$(") + raw.count("<(") + raw.count(">(") + raw.count("`")
            + len(BRACE_OPENER.findall(raw)))


def shell_c(raw: str) -> bool:
    """Rule C's gate: a shell name in the raw text or the text with `$`-before-quote,
    quotes and backslashes deleted, and a -c word in either."""
    texts = (raw, QUOTES_DELETED.sub("", raw))
    return (any(SHELL_NAME.search(t) for t in texts)
            and any("c" in m.group(0) for t in texts for m in C_WORD.finditer(t)))


def c_removal(raw: str) -> bool:
    """Rule C's removal word, in either of the gate's texts or the second with braces
    deleted."""
    stripped = QUOTES_DELETED.sub("", raw)
    unbraced = stripped.replace("{", "").replace("}", "")
    return any(C_REMOVAL.search(t) for t in (raw, stripped, unbraced))


def rules(raw: str, cwd: str | None, home: str | None) -> str:
    """The rules `raw` breaks, as their letters in the order T, W, S, C; empty when
    none. Every rule reads `raw` with its file-writing here-document bodies cut out. `cwd`
    and `home` are absolute and normalized, or None when unknown."""
    raw = cut_heredocs(raw)
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
           "JOIN", "feeds_a_file", "cut_heredocs", "openers", "shell_c", "c_removal", "rules",
           "refusal", "judge"]
