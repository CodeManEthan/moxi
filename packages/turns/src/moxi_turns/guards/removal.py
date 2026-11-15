"""turns' removal guard: refuse, before it runs, a shell command that Claude Code's own
dangerous-removal check would stop for the user's approval even in bypass mode, where the
agent would sit blocked until someone clicks. Claude Code 2.1.316 refuses such a stop by
itself after about two minutes (1:45 on its countdown) when nobody answers, so an
unattended agent loses that long and then the command.

One coarse text rule, rule T, no shell parsing. It reads the command with the body of each
file-writing here-document cut out (below), and refuses when the text holds a `cd`,
`pushd`, `popd` or `chdir` word, an `rm` or `rmdir` word and a `*`, all in the text as
written, or all in the text with every `'`, `"` and `\\` deleted. A word here is one not
preceded or followed by a letter, a digit or `_`.

The guard covers only that one stop. Claude Code also stops other removals for approval
(a removal of the working directory or a directory above it, a path it can't read, a
removal inside a shell's `-c` script); a guard at the permission ask step can refuse those
when they are asked.

The heredoc cut: before the rule reads the command, the body of a here-document is cut out
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

A command that is not text is let through, and so is every call the guard can't read: a
break lets the call through too (`on_error = "allow"`).

The guard's `match` passes every command the rule could refuse, a removal word with any
run of `\\`, `'` and `"` between its letters, so a Bash call the filter passes over never
leaves the runner.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from . import Deny, ToolCall

#: The Claude Code version the rule was checked against. The doctor warns when the
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
#: Each rule's letter and its reason.
REASONS = {"T": REASON_T}

# Rule T's searches.
CD = re.compile(r"(?<![A-Za-z0-9_])(cd|pushd|popd|chdir)(?![A-Za-z0-9_])")
RM = re.compile(r"(?<![A-Za-z0-9_])(rm|rmdir)(?![A-Za-z0-9_])")

# A here-document operator after its <<: an optional -, then the delimiter, quoted ('X',
# "X", \X) or bare.
HEREDOC = re.compile(r"(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\([A-Za-z0-9_]+)|([A-Za-z0-9_]+))")
# The words that may lead a command before its name.
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
LEADERS = ("{", "!", "do", "then", "else", "time")


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


def _closing_line(raw: str, start: int, delim: str, *, tabs: bool) -> tuple[int, int] | None:
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
                found = _closing_line(raw, i, delim, tabs=tabs)
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


def rules(raw: str) -> str:
    """The rules `raw` breaks, as their letters: "T" or empty. The rule reads `raw` with
    its file-writing here-document bodies cut out."""
    raw = cut_heredocs(raw)
    texts = (raw, re.sub(r"['\"\\]", "", raw))
    return "T" if any(CD.search(t) and RM.search(t) and "*" in t for t in texts) else ""


def refusal(command: str) -> str | None:
    """The refusal for `command`, or None to let it run."""
    found = rules(command)
    return "\n\n".join(REASONS[r] for r in found) if found else None


def judge(call: ToolCall) -> Deny | None:
    """The guard's judge: a Bash call's `command`, refused when a rule matches."""
    command = call.input.get("command") if isinstance(call.input, Mapping) else None
    if not isinstance(command, str):
        return None
    reason = refusal(command)
    return Deny(reason) if reason else None


__all__ = ["CHECKED_CLAUDE", "REASON_T", "REASONS", "feeds_a_file", "cut_heredocs", "rules",
           "refusal", "judge"]
