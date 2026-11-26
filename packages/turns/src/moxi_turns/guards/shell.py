"""Shell command text read as a guard reads it: the commands, words, redirects and
substitutions of `sh`, PowerShell and cmd text, without running anything.

`read_shell(text, dialect="sh")` gives a `ShellText`: its `commands`, each a
`ShellCommand` of `words`, `redirects` and the operator that ended it (`ended_by`), and its
`marks`, everything a strict guard may refuse, in text order. `command_word_index` finds
the word that names what a command runs, past assignments, reserved words and the
wrappers below. `shell_scripts` gives the text a command hands a shell to run. A guard
reads a command with these and keeps its own policy: one that fails open lets through
what the reader doesn't read, and one that fails closed refuses it by a mark.

**What is read.** One closed grammar per dialect (`DIALECTS`):

- `sh` (text for `sh`, `bash`, `zsh`, `dash`, `ksh`, `mksh` and `ash`). Words split at unquoted spaces
  and tabs, and a backslash before a newline joins the lines. `'...'` is verbatim;
  `"..."` expands substitutions, and a backslash in it escapes only `$`, a backtick,
  `"`, `\\` and a newline; in `$'...'` the escapes `\\\\`, `\\'` and `\\"` are taken off
  and every other escape is kept as written; outside quotes a backslash escapes the next
  character. Substitutions are `$(...)`, backticks, `<(...)` and `>(...)`, bare, in
  `"..."`, in an unquoted heredoc's body, in arithmetic and inside `${...}`. Arithmetic is
  `$((...))` and `$[...]`, and `((...))` in command position, one word that runs
  nothing; `<<` and `>>` inside it are shifts. The operators are `|`, `|&` (read as `|`),
  `&&`, `||`, `;`, `;;`, `;&` and `;;&` (read as `;`), `&`, `(`, `)` and a newline. The
  redirects are `<`, `>`, `>>`, `>|`, `<>`, `<&`, `>&`, `&>` and `&>>`, each with an fd
  or `{name}` written against it or none, the here-string `<<<`, and the heredocs `<<`
  and `<<-`. A `#` that starts a word comments out the rest of its line.
- `powershell` (text for `pwsh` and `powershell`). Words split at unquoted spaces and
  tabs, and after an assignment's operator written against what follows it (`$x=cmd`).
  `'...'` is verbatim with `''` an apostrophe; `"..."` expands substitutions with
  `""` a quote; a backtick escapes the next character outside `'...'`, and the named
  escapes (`` `n ``, `` `t ``, `` `0 `` and the rest) are kept as written. Substitutions
  are `$(...)`, `@(...)` and a `(...)` group, bare, in `"..."` and in `@"..."@`. The
  operators are `|`, `&&`, `||`, `;`, `&` after a pipeline, a newline, `{` and `}`; `&`
  and `.` in command position are the call operators. The redirects are `>`, `>>`,
  `2>`, `*>` and `n>&1`. `@'...'@` is a verbatim here-string and `@"..."@` an expanding
  one. A `#` that starts a word comments out the rest of its line, and `<# ... #>` is a
  comment.
- `cmd` (the text `cmd /c` or `/k` runs). Words split at unquoted spaces and tabs;
  `"..."` has no escape inside, and `^` escapes the next character outside it. The
  operators are `&`, `&&`, `||`, `|`, `(`, `)` and a newline, so a `( )` block is read
  wherever it stands. The redirects are `<`, `>`, `>>`, `2>` and `2>&1`. A leading `@`
  is a word of its own.

A heredoc's body is the lines after its line, up to a line that is the delimiter; it goes
on the redirect's `body` as written and is never read as commands. With a quoted
delimiter it is data; with an unquoted one the reader reads it as the shell expands it,
where only `$`, backticks and a backslash act, and each substitution in it goes on the
redirect's `subs`.

**Words.** A word's `text` has its quotes taken off; each substitution in it stands as
one NUL, and its text is kept in `subs`, in order. `is_sub` is true when the word is
exactly one substitution, bare or alone in double quotes. A value (`$name`, `${...}`,
`$env:NAME`, `%NAME%`, a glob, a brace, a tilde) and arithmetic are kept as written. In
`sh`, a word holding a `${...}` outside double quotes whose operator is `-`, `:-`, `+`,
`:+`, `=` or `:=` also has `fields`, the words bash makes of it when that default or
alternate is used, since bash splits it at its blanks; an assignment bash makes without
splitting has none. A
redirect is kept with its operator, the fd written against it and the word it redirects
to; a process substitution's operator is `<(` or `>(` and its target the substitution's
word.

**What isn't read.** Anything that needs evaluating to know what runs: a value's
expansion; escapes a program processes (`printf`, `echo -e`, an escape kept as written);
a string run as code (`eval`, `iex`, `-EncodedCommand`); the command after cmd's `if`,
`for`, `call`, `start` and `else`; an option the wrapper and shell tables don't name;
text handed to a program those tables don't name; and text from outside the command (a
script file, a file or another program's output fed to a shell). `$((` is read as
arithmetic always, where bash would sometimes read `$(` and a subshell.

**Never raises.** An unclosed quote, substitution, here-string or heredoc runs to the end
of the text and is marked `unclosed`; text nested too deep to read reads as no commands
with an `unclosed` mark. Text that isn't a string reads as no commands. An unknown
dialect is a `ValueError`.

Standard library only, like the rest of `moxi_turns.guards`.
"""

from __future__ import annotations

import re
from dataclasses import KW_ONLY, dataclass, field

#: The dialects the reader reads.
DIALECTS = ("sh", "powershell", "cmd")
#: Every kind of `ShellMark`.
MARK_KINDS = ("operator", "redirect", "heredoc", "dollar", "backtick", "backslash", "glob",
              "brace", "tilde", "comment", "ansi-c", "arithmetic", "control", "unclosed")
#: Where a `ShellScript`'s text comes from.
SCRIPT_SOURCES = ("argument", "heredoc", "here-string", "pipe")
#: How a `${...}` starts whose default or alternate bash splits into words where it is
#: used: a name and one of `-`, `:-`, `+`, `:+`, `=` and `:=`.
_SPLIT_VALUE = re.compile(r"!?(?:[A-Za-z_][A-Za-z0-9_]*(?:\[[^]\n]*\])?|[0-9]+|[@*#?$!])"
                          r":?[-+=]")
#: The blanks bash splits a value's words at.
_BLANKS = " \t\n"
_BLANK_RUN = re.compile(r"[ \t\n]+")


@dataclass(frozen=True)
class ShellWord:
    """One word: its text with quotes taken off and each substitution a NUL, its position
    in the text read, and each substitution's text in order. `fields` are the words bash
    makes of it when each `${...}` outside double quotes whose operator is `-`, `:-`, `+`,
    `:+`, `=` or `:=` gives its default or alternate: split at that text's unquoted blanks
    (any blank with `=` and `:=`) and joined to the word's text around it, quotes taken
    off and each substitution a NUL. None when the word holds no such `${...}`, or is an
    assignment bash makes without splitting (before the command word, or given to
    `export`, `local`, `declare`, `readonly` or `typeset`)."""

    text: str
    position: int
    subs: tuple[str, ...]
    _: KW_ONLY
    is_sub: bool
    fields: tuple[str, ...] = ()
    #: Whether the word holds an escape the reader keeps as written.
    _kept_escape: bool = field(default=False, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class ShellRedirect:
    """One redirect: its operator, the fd written against it (`2` in `2>`, `{fd}`), the
    word it redirects to, a heredoc's body as written, and the substitutions an unquoted
    heredoc's body runs."""

    operator: str
    fd: str | None
    target: ShellWord | None
    body: str | None = None
    subs: tuple[str, ...] = ()
    #: A heredoc's body as a shell reading it gets it: an unquoted one unescaped.
    _input: str | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class ShellCommand:
    """One command: its words, its redirects, and the operator that ended it (`|`, `&&`,
    `||`, `;`, `&`, `(`, `)`, a newline, in PowerShell `{` and `}` too, or "" at the end
    of the text)."""

    words: tuple[ShellWord, ...]
    redirects: tuple[ShellRedirect, ...]
    ended_by: str


@dataclass(frozen=True)
class ShellMark:
    """Something a strict guard may refuse, of one of `MARK_KINDS`, at its position."""

    kind: str
    position: int


@dataclass(frozen=True)
class ShellText:
    """Text as the reader reads it: its commands in order, and its marks in text order."""

    commands: tuple[ShellCommand, ...]
    marks: tuple[ShellMark, ...]


@dataclass(frozen=True)
class ShellScript:
    """Text a command hands a shell to run: the text, the dialect to read it in, the index
    of the command that runs it in the `ShellText` given to `shell_scripts`, where the
    text comes from (one of `SCRIPT_SOURCES`), and the indexes into that command's words
    of the words the text was made from (none for a script that is the shell's input)."""

    text: str
    dialect: str
    command_index: int
    source: str
    word_indexes: tuple[int, ...] = ()


# --- the reader ------------------------------------------------------------------------

def read_shell(text: object, *, dialect: str = "sh") -> ShellText:
    """`text` read in `dialect` (one of `DIALECTS`): its commands and marks. Never raises
    on any text; text that isn't a string reads as no commands."""
    if dialect not in DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}; the dialects are {DIALECTS}")
    if not isinstance(text, str):
        return ShellText((), ())
    marks: list[ShellMark] = []
    reader = {"sh": _Sh, "powershell": _Ps, "cmd": _Cmd}[dialect](text, marks, len(text))
    try:
        reader.read(0)
        commands = reader.finish()
    except RecursionError:
        return ShellText((), (ShellMark("unclosed", 0),))
    ordered = sorted(marks, key=lambda m: m.position)
    return ShellText(commands, tuple(ordered))


class _Word:
    """A word being read."""

    __slots__ = ("chars", "subs", "position", "plain", "kept", "splits")

    def __init__(self, position: int):
        self.chars: list[str] = []
        self.subs: list[str] = []
        self.position = position
        #: No quote, escape or substitution in it: it can be a reserved word or an fd.
        self.plain = True
        self.kept = False
        #: For each piece of `chars` that is a `${...}` bash splits, its default's words.
        self.splits: dict[int, list[str]] = {}

    def fields(self) -> tuple[str, ...]:
        """The words bash makes of this word with each split `${...}` used."""
        if not self.splits:
            return ()
        out = [""]
        for k, piece in enumerate(self.chars):
            words = self.splits.get(k)
            if words is None:
                out[-1] += piece
            else:
                out[-1] += words[0]
                out.extend(words[1:])
        return tuple(f for f in out if f)

    def sub(self, text: str) -> None:
        self.chars.append("\0")
        self.subs.append(text)
        self.plain = False


class _Redir:
    __slots__ = ("operator", "fd", "target", "body", "subs", "input", "position", "strip")

    def __init__(self, operator: str, fd: str | None, position: int):
        self.operator, self.fd, self.position = operator, fd, position
        self.target: ShellWord | None = None
        self.body: str | None = None
        self.subs: list[str] = []
        self.input: str | None = None
        self.strip = operator == "<<-"

    def done(self) -> ShellRedirect:
        r = ShellRedirect(self.operator, self.fd, self.target, self.body, tuple(self.subs))
        object.__setattr__(r, "_input", self.input)
        return r


class _Command:
    __slots__ = ("words", "plain", "redirects")

    def __init__(self):
        self.words: list[ShellWord] = []
        self.plain: list[bool] = []
        self.redirects: list[_Redir] = []


def _shell_word(text: str, position: int, subs, *, is_sub: bool,
                kept: bool = False, fields: tuple[str, ...] = ()) -> ShellWord:
    w = ShellWord(text, position, tuple(subs), is_sub=is_sub, fields=fields)
    if kept:
        object.__setattr__(w, "_kept_escape", True)
    return w


class _Reader:
    """What the three dialects' readers share: the commands so far, the word being read,
    and the redirect waiting for its target."""

    def __init__(self, text: str, marks: list[ShellMark], end: int):
        self.t, self.n, self.marks = text, end, marks
        self.done: list[tuple[_Command, str]] = []
        self.cur = _Command()
        self.word: _Word | None = None
        self.target_for: _Redir | None = None

    def mark(self, kind: str, position: int) -> None:
        self.marks.append(ShellMark(kind, position))

    def start(self, position: int) -> _Word:
        if self.word is None:
            self.word = _Word(position)
        return self.word

    def is_sub_word(self, w: _Word, end: int) -> bool:
        raw = self.t[w.position:end]
        return len(w.subs) == 1 and "".join(w.chars) == "\0" and self.whole_sub(raw)

    def whole_sub(self, raw: str) -> bool:
        return False

    def end_word(self, end: int) -> ShellWord | None:
        """End the word being read, if any; a redirect waiting for its target takes it."""
        w, self.word = self.word, None
        if w is None:
            return None
        sw = _shell_word("".join(w.chars), w.position, w.subs,
                         is_sub=self.is_sub_word(w, end), kept=w.kept, fields=w.fields())
        if self.target_for is not None:
            r, self.target_for = self.target_for, None
            r.target = sw
            self.took_target(r, w)
            return None
        self.add_word(sw, plain=w.plain, end=end)
        return sw

    def took_target(self, r: _Redir, w: _Word) -> None:
        pass

    def add_word(self, sw: ShellWord, *, plain: bool, end: int | None = None) -> None:
        self.cur.words.append(sw)
        self.cur.plain.append(plain)

    def operator(self, op: str, position: int) -> None:
        self.end_word(position)
        self.target_for = None
        self.mark("operator", position)
        self.done.append((self.cur, op))
        self.cur = _Command()

    def fd_word(self, digits: str) -> str | None:
        """The word being read, when it is an fd written against a redirect operator: it
        is taken off the words."""
        w = self.word
        if w is not None and w.plain and not w.subs:
            text = "".join(w.chars)
            if self.is_fd(text, digits):
                self.word = None
                return text
        return None

    def is_fd(self, text: str, digits: str) -> bool:
        return len(text) > 0 and all(ch in digits for ch in text)

    def add_redirect(self, op: str, fd: str | None, position: int) -> _Redir:
        if fd is None:
            self.end_word(position)
        self.target_for = None
        r = _Redir(op, fd, position)
        self.cur.redirects.append(r)
        return r

    def finish(self) -> tuple[ShellCommand, ...]:
        self.end_word(self.n)
        self.target_for = None
        self.close_pending()
        rows = list(self.done)
        if self.cur.words or self.cur.redirects or rows:
            rows.append((self.cur, ""))
        return tuple(ShellCommand(tuple(c.words), tuple(r.done() for r in c.redirects), op)
                     for c, op in rows)

    def close_pending(self) -> None:
        pass

    def control(self, c: str, position: int) -> None:
        if ord(c) < 32 or c == "\x7f":
            self.mark("control", position)


# --- sh ---------------------------------------------------------------------------------

#: Reserved words after which the next word is in command position.
_STARTERS = frozenset({"!", "{", "if", "then", "elif", "else", "while", "until", "do",
                       "time"})
#: The builtins whose `NAME=value` arguments bash assigns without splitting the value.
_DECLARES = frozenset({"export", "local", "declare", "readonly", "typeset"})
#: Words that start a command which runs nothing itself.
_RUNS_NOTHING = frozenset({"}", "fi", "done", "esac", "for", "select", "case", "[["})


class _Sh(_Reader):
    def __init__(self, text: str, marks: list[ShellMark], end: int):
        super().__init__(text, marks, end)
        self.heredocs: list[tuple[_Redir, str, bool]] = []
        #: The open `case`s: "subject", "in", "pattern" or "body" each.
        self.cases: list[str] = []
        self.pattern_words = 0
        self.testing = False
        self.depth = 0

    # -- grammar bookkeeping --

    def whole_sub(self, raw: str) -> bool:
        if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
            raw = raw[1:-1]
        return ((raw.startswith("$(") and raw.endswith(")") and not raw.startswith("$(("))
                or (len(raw) >= 2 and raw.startswith("`") and raw.endswith("`")))

    def command_position(self) -> bool:
        """Whether the next word of the command being read names what it runs."""
        if self.cases and self.cases[-1] != "body":
            return False
        return _past_reserved(self.cur.words, self.cur.plain) == len(self.cur.words)

    def for_position(self) -> bool:
        ws = self.cur.words
        return (bool(ws) and self.cur.plain[-1] and ws[-1].text in ("for", "select")
                and (not self.cases or self.cases[-1] == "body")
                and _past_reserved(ws[:-1], self.cur.plain[:-1]) == len(ws) - 1)

    def keeps_value(self, text: str) -> bool:
        """Whether bash assigns the next word's value without splitting it: an assignment
        before the command word, or one a declaration builtin is given."""
        if not _is_assignment(text):
            return False
        k = _past_reserved(self.cur.words, self.cur.plain)
        named = next((w.text for w in self.cur.words[k:] if not _is_assignment(w.text)),
                     None)
        return named is None or named in _DECLARES

    def add_word(self, sw: ShellWord, *, plain: bool, end: int | None = None) -> None:
        text = sw.text
        if sw.fields and self.keeps_value(text):
            object.__setattr__(sw, "fields", ())
        state = self.cases[-1] if self.cases else None
        if state == "subject":
            self.cases[-1] = "in"
        elif state == "in":
            if plain and text == "in":
                self.cases[-1] = "pattern"
                self.pattern_words = 0
        elif state == "pattern":
            if plain and text == "esac" and self.pattern_words == 0:
                self.cases.pop()
            else:
                self.pattern_words += 1
        elif plain and self.command_position():
            if text == "esac" and state == "body":
                self.cases.pop()
            elif text == "case":
                self.cases.append("subject")
            elif text == "[[":
                self.testing = True
        if self.testing and plain and text == "]]":
            self.testing = False
        super().add_word(sw, plain=plain, end=end)

    def operator(self, op: str, position: int) -> None:
        super().operator(op, position)
        self.testing = False

    def took_target(self, r: _Redir, w: _Word) -> None:
        if r.operator in ("<<", "<<-"):
            self.heredocs.append((r, "".join(w.chars), not w.plain))

    # -- the main loop --

    def read(self, i: int, *, close: bool = False) -> int:
        """Read from `i`. With `close`, stop at the `)` that closes a substitution and
        return its index; otherwise, or when none closes it, return the end."""
        t, n = self.t, self.n
        while i < n:
            c = t[i]
            if self.word is None:
                if c in " \t":
                    i += 1
                    continue
                if c == "#":
                    self.mark("comment", i)
                    j = t.find("\n", i, n)
                    i = n if j < 0 else j
                    continue
                if c == "(" and t.startswith("((", i) and (self.command_position()
                                                          or self.for_position()):
                    i = self.arith_word(i)
                    continue
                if (c == "(" and self.cases and self.cases[-1] == "pattern"
                        and self.pattern_words == 0):
                    i += 1
                    continue
            if self.testing and (t.startswith(("&&", "||"), i) or c in "()"):
                size = 2 if c in "&|" else 1
                self.mark("operator", i)
                self.end_word(i)
                self.add_word(_shell_word(t[i:i + size], i, (), is_sub=False), plain=True)
                i += size
                continue
            if c == "\n":
                self.operator("\n", i)
                i = self.bodies(i + 1)
                continue
            if c in " \t":
                self.end_word(i)
                i += 1
                continue
            if c == ";":
                size = 3 if t.startswith(";;&", i) else 2 if t.startswith((";;", ";&"), i) else 1
                self.operator(";", i)
                if size > 1 and self.cases and self.cases[-1] == "body":
                    self.cases[-1] = "pattern"
                    self.pattern_words = 0
                i += size
                continue
            if c == "&":
                if t.startswith("&>", i):
                    i = self.redirect(i)
                elif t.startswith("&&", i):
                    self.operator("&&", i)
                    i += 2
                else:
                    self.operator("&", i)
                    i += 1
                continue
            if c == "|":
                if self.cases and self.cases[-1] == "pattern":
                    self.start(i).chars.append(c)
                    i += 1
                elif t.startswith("||", i):
                    self.operator("||", i)
                    i += 2
                else:
                    self.operator("|", i)
                    i += 2 if t.startswith("|&", i) else 1
                continue
            if c in "<>":
                i = self.redirect(i)
                continue
            if c == "(":
                self.operator("(", i)
                self.depth += 1
                i += 1
                continue
            if c == ")":
                self.end_word(i)
                if self.cases and self.cases[-1] == "pattern":
                    self.operator(")", i)
                    self.cases[-1] = "body"
                elif self.depth > 0:
                    self.operator(")", i)
                    self.depth -= 1
                elif close:
                    self.end_word(i)
                    return i
                else:
                    self.operator(")", i)
                i += 1
                continue
            if c == "'":
                i = self.single(i)
                continue
            if c == '"':
                i = self.double(i)
                continue
            if c == "\\":
                self.mark("backslash", i)
                if i + 1 < n and t[i + 1] != "\n":
                    w = self.start(i)
                    w.chars.append(t[i + 1])
                    w.plain = False
                i += 2
                continue
            if c == "$":
                i = self.dollar(i, in_double=False)
                continue
            if c == "`":
                i = self.backtick(i, in_double=False)
                continue
            leading = self.word is None
            w = self.start(i)
            if c in "*?[":
                self.mark("glob", i)
            elif c in "{}":
                self.mark("brace", i)
            elif c == "~" and not (leading and t[i + 1:i + 2] == "/"):
                self.mark("tilde", i)
            else:
                self.control(c, i)
            w.chars.append(c)
            i += 1
        return n

    # -- quotes and expansions --

    def single(self, i: int) -> int:
        t, n = self.t, self.n
        w = self.start(i)
        w.plain = False
        j = t.find("'", i + 1, n)
        if j < 0:
            self.mark("unclosed", i)
            w.chars.append(t[i + 1:n])
            return n
        w.chars.append(t[i + 1:j])
        return j + 1

    def double(self, i: int) -> int:
        t, n = self.t, self.n
        w = self.start(i)
        w.plain = False
        open_position, i = i, i + 1
        while i < n:
            c = t[i]
            if c == '"':
                return i + 1
            if c == "\\":
                d = t[i + 1:i + 2]
                if d and d in '$`"\\\n':
                    if d == "$":
                        self.mark("dollar", i + 1)
                    elif d == "`":
                        self.mark("backtick", i + 1)
                    if d != "\n":
                        w.chars.append(d)
                    i += 2
                    continue
                w.chars.append(c)
                i += 1
                continue
            if c == "$":
                i = self.dollar(i, in_double=True)
                continue
            if c == "`":
                i = self.backtick(i, in_double=True)
                continue
            w.chars.append(c)
            i += 1
        self.mark("unclosed", open_position)
        return n

    def dollar(self, i: int, *, in_double: bool) -> int:
        t = self.t
        self.mark("dollar", i)
        w = self.start(i)
        nxt = t[i + 1:i + 2]
        if t.startswith("$((", i):
            self.mark("arithmetic", i)
            end, written = self.arith(i, i + 3, w.subs, "(", ")", 2)
            w.chars.append("$((" + written)
            w.plain = False
            return end
        if nxt == "(":
            return self.substitution(i, i + 2, w)
        if nxt == "[":
            self.mark("arithmetic", i)
            end, written = self.arith(i, i + 2, w.subs, "[", "]", 1)
            w.chars.append("$[" + written)
            w.plain = False
            return end
        if nxt == "{":
            split: list[tuple[list[str], str]] | None = None if in_double else []
            end, written = self.value(i, i + 2, w.subs, split)
            if split:
                w.splits[len(w.chars)] = split[0][0]
            w.chars.append("${" + written)
            w.plain = False
            return end
        if nxt == "'" and not in_double:
            self.mark("ansi-c", i)
            return self.ansi_c(i, w)
        w.chars.append("$")
        return i + 1

    def substitution(self, open_position: int, inner: int, w: _Word) -> int:
        """A `$(...)` whose text starts at `inner`: its text joins the word's subs."""
        end = self.closing(inner)
        if end >= self.n:
            self.mark("unclosed", open_position)
            w.sub(self.t[inner:self.n])
            return self.n
        w.sub(self.t[inner:end])
        return end + 1

    def closing(self, i: int) -> int:
        """Where the `)` closing a substitution whose text starts at `i` is, read as the
        shell reads it; the end when none closes it."""
        return _Sh(self.t, self.marks, self.n).read(i, close=True)

    def backtick(self, i: int, *, in_double: bool) -> int:
        self.mark("backtick", i)
        w = self.start(i)
        end, text, closed = self.backtick_text(i, in_double=in_double, end=self.n)
        if not closed:
            self.mark("unclosed", i)
        w.sub(text)
        return end

    def backtick_text(self, i: int, *, in_double: bool, end: int) -> tuple[int, str, bool]:
        t = self.t
        escapes = '$`\\"' if in_double else "$`\\"
        out: list[str] = []
        j = i + 1
        while j < end:
            c = t[j]
            if c == "\\" and j + 1 < end and t[j + 1] in escapes:
                out.append(t[j + 1])
                j += 2
                continue
            if c == "`":
                return j + 1, "".join(out), True
            out.append(c)
            j += 1
        return end, "".join(out), False

    def ansi_c(self, i: int, w: _Word) -> int:
        t, n = self.t, self.n
        w.plain = False
        j = i + 2
        while j < n:
            c = t[j]
            if c == "\\" and j + 1 < n:
                d = t[j + 1]
                if d in "\\'\"":
                    w.chars.append(d)
                else:
                    w.chars.append(c + d)
                    w.kept = True
                j += 2
                continue
            if c == "'":
                return j + 1
            w.chars.append(c)
            j += 1
        self.mark("unclosed", i)
        return n

    def arith(self, open_position: int, i: int, subs: list[str], opener: str, closer: str,
              depth: int) -> tuple[int, str]:
        """Arithmetic opened at `open_position`, from `i` to the `closer` that brings
        `depth` to nought: where it ends and its text from `i` as written, each
        substitution in it a NUL (its text joins `subs`)."""
        t, n = self.t, self.n
        out: list[str] = []
        while i < n:
            c = t[i]
            j = self.inner_expansion(i, subs, out)
            if j is not None:
                i = j
                continue
            if c == "\\" and i + 1 < n:
                out.append(t[i:i + 2])
                i += 2
                continue
            if c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    out.append(c)
                    return i + 1, "".join(out)
            out.append(c)
            i += 1
        self.mark("unclosed", open_position)
        return n, "".join(out)

    def value(self, open_position: int, i: int, subs: list[str],
              split: list[tuple[list[str], str]] | None = None) -> tuple[int, str]:
        """A `${...}` opened at `open_position`, from just inside its brace: where it ends
        and its text from `i` as written, each substitution in it a NUL (its text joins
        `subs`).

        Given a `split` list, a closed `${...}` whose operator bash splits its default or
        alternate for (`_SPLIT_VALUE`) adds to it that text's words and the text itself,
        quotes taken off and each substitution a NUL. The words are split at its unquoted
        blanks; one that starts or ends with a blank comes with an empty word there, so
        the word around it is joined only where bash joins it. With `=` or `:=` bash
        splits the value it assigned, so a quoted blank splits too. A `${...}` of this kind
        inside it gives its own words where it stands outside quotes, and its text as one
        piece inside them; any other `${...}` inside it is kept as written."""
        t, n = self.t, self.n
        out: list[str] = []
        found = _SPLIT_VALUE.match(t, i, n) if split is not None else None
        words_from = found.end() if found else n
        words = [""]
        apart = False
        assigned: list[str] = []

        def put(text: str, *more: str) -> None:
            nonlocal apart
            if apart:
                words.append("")
                apart = False
            words[-1] += text
            words.extend(more)

        while i < n:
            c = t[i]
            reading = i >= words_from
            start = len(out)
            if reading and t.startswith("${", i):
                inner: list[tuple[list[str], str]] = []
                i, written = self.value(i, i + 2, subs, inner)
                out.append("${" + written)
                if inner:
                    put(*inner[0][0])
                    assigned.append(inner[0][1])
                else:
                    put(out[-1])
                    assigned.append(out[-1])
                continue
            j = self.inner_expansion(i, subs, out)
            if j is not None:
                if reading:
                    put("".join(out[start:]))
                    assigned.append("".join(out[start:]))
                i = j
                continue
            if c == "\\" and i + 1 < n:
                out.append(t[i:i + 2])
                if reading and t[i + 1] != "\n":
                    put(t[i + 1])
                    assigned.append(t[i + 1])
                i += 2
                continue
            if c == "'":
                j = t.find("'", i + 1, n)
                j = n - 1 if j < 0 else j
                out.append(t[i:j + 1])
                if reading:
                    put(t[i + 1:j])
                    assigned.append(t[i + 1:j])
                i = j + 1
                continue
            if c == '"':
                piece: list[str] = []
                i = self.value_double(i, subs, out, piece if reading else None)
                if reading:
                    put("".join(piece))
                    assigned.append("".join(piece))
                continue
            out.append(c)
            i += 1
            if c == "}":
                if found and split is not None:
                    text = "".join(assigned)
                    if found.group().endswith("="):
                        split.append((_BLANK_RUN.split(text), text))
                    else:
                        split.append((words + [""] if apart else words, text))
                return i, "".join(out)
            if reading:
                assigned.append(c)
                if c in _BLANKS:
                    apart = True
                else:
                    put(c)
        self.mark("unclosed", open_position)
        return n, "".join(out)

    def value_double(self, i: int, subs: list[str], out: list[str],
                     piece: list[str] | None = None) -> int:
        """A `"..."` inside `${...}` at `i`, read as `double` reads one, so a `'` or `}`
        in it is an ordinary character: where it ends, with its text as written on `out`
        and each substitution's text on `subs`. Given a `piece` list, its text as bash
        gives it goes there too: escapes taken off, each substitution a NUL, and a
        `${...}` whose default bash splits given as that default's text, unsplit."""
        t, n = self.t, self.n
        out.append('"')
        i += 1
        while i < n:
            c = t[i]
            start = len(out)
            if c == '"':
                out.append(c)
                return i + 1
            if c == "\\" and i + 1 < n:
                out.append(t[i:i + 2])
                if piece is not None:
                    d = t[i + 1]
                    piece.append(("" if d == "\n" else d) if d in '$`"\\\n' else c + d)
                i += 2
                continue
            if c == "`":
                j, text, closed = self.backtick_text(i, in_double=True, end=n)
                if not closed:
                    self.mark("unclosed", i)
                subs.append(text)
                out.append("\0")
                if piece is not None:
                    piece.append("\0")
                i = j
                continue
            if piece is not None and t.startswith("${", i):
                inner: list[tuple[list[str], str]] = []
                i, written = self.value(i, i + 2, subs, inner)
                out.append("${" + written)
                piece.append(inner[0][1] if inner else out[-1])
                continue
            j = self.inner_expansion(i, subs, out) if c == "$" else None
            if j is not None:
                if piece is not None:
                    piece.append("".join(out[start:]))
                i = j
                continue
            out.append(c)
            if piece is not None:
                piece.append(c)
            i += 1
        return n

    def inner_expansion(self, i: int, subs: list[str], out: list[str]) -> int | None:
        """An expansion inside arithmetic or `${...}` at `i`: where it ends, with its text
        as written on `out` and each substitution's text on `subs`; None when none starts
        at `i`."""
        t = self.t
        if t.startswith("$((", i):
            j, written = self.arith(i, i + 3, subs, "(", ")", 2)
            out.append("$((" + written)
            return j
        if t.startswith("$(", i):
            j = self.closing(i + 2)
            subs.append(t[i + 2:j])
            out.append("\0")
            if j >= self.n:
                self.mark("unclosed", i)
            return min(j + 1, self.n)
        if t.startswith("${", i):
            j, written = self.value(i, i + 2, subs)
            out.append("${" + written)
            return j
        if t.startswith("`", i):
            j, text, closed = self.backtick_text(i, in_double=False, end=self.n)
            if not closed:
                self.mark("unclosed", i)
            subs.append(text)
            out.append("\0")
            return j
        return None

    def arith_word(self, i: int) -> int:
        """`((...))` in command position: one word that runs nothing."""
        self.mark("arithmetic", i)
        w = self.start(i)
        end, written = self.arith(i, i + 2, w.subs, "(", ")", 2)
        w.chars.append("((" + written)
        w.plain = False
        self.end_word(end)
        return end

    # -- redirects and heredocs --

    def redirect(self, i: int) -> int:
        t = self.t
        op = next(o for o in ("<<<", "<<-", "<<", "<>", "<&", "<(", "<", ">>", ">|", ">&",
                              ">(", ">", "&>>", "&>") if t.startswith(o, i))
        fd = None if op.startswith("&") else self.fd_word("0123456789")
        if op in ("<(", ">("):
            r = self.add_redirect(op, fd, i)
            self.mark("redirect", i)
            end = self.closing(i + 2)
            if end >= self.n:
                self.mark("unclosed", i)
            r.target = _shell_word("\0", i, (t[i + 2:min(end, self.n)],), is_sub=True)
            return min(end + 1, self.n)
        r = self.add_redirect(op, fd, i)
        self.mark("heredoc" if op in ("<<", "<<-") else "redirect", i)
        self.target_for = r
        return i + len(op)

    def is_fd(self, text: str, digits: str) -> bool:
        if text.startswith("{") and text.endswith("}") and len(text) > 2:
            name = text[1:-1]
            return (name[0].isalpha() or name[0] == "_") and all(
                ch.isalnum() or ch == "_" for ch in name)
        return super().is_fd(text, digits)

    def bodies(self, i: int) -> int:
        """Read the bodies of the heredocs the line just ended opened, from `i`."""
        t, n = self.t, self.n
        pending, self.heredocs = self.heredocs, []
        for r, delim, quoted in pending:
            start = j = i
            found = False
            while j < n:
                k = t.find("\n", j, n)
                stop = n if k < 0 else k
                line = t[j:stop]
                if (line.lstrip("\t") if r.strip else line) == delim:
                    body_end, i, found = j, (n if k < 0 else k + 1), True
                    break
                j = n if k < 0 else k + 1
            if not found:
                body_end = i = n
                self.mark("unclosed", r.position)
            self.take_body(r, start, body_end, quoted=quoted)
        return i

    def close_pending(self) -> None:
        for r, _, quoted in self.heredocs:
            self.mark("unclosed", r.position)
            self.take_body(r, self.n, self.n, quoted=quoted)
        self.heredocs = []

    def take_body(self, r: _Redir, start: int, end: int, *, quoted: bool) -> None:
        t = self.t
        r.body = t[start:end]
        # For `<<-` the shell drops the leading tabs of each body line before it reads it.
        if quoted:
            r.input = _untab(r.body) if r.strip else r.body
            return
        # An unquoted body as the shell expands it: only `$`, backticks and `\\` act. Its
        # substitutions are found by a reader of the body alone, which marks nothing.
        body = _Sh(t, [], end)
        out: list[str] = []
        i = start
        while i < end:
            if r.strip and (i == start or t[i - 1] == "\n"):
                while i < end and t[i] == "\t":
                    i += 1
                if i == end:
                    break
            c = t[i]
            if c == "\\" and i + 1 < end and t[i + 1] in "$`\\\n":
                if t[i + 1] != "\n":
                    out.append(t[i + 1])
                i += 2
                continue
            j = body.inner_expansion(i, r.subs, []) if c in "$`" else None
            if j is None:
                out.append(c)
                i += 1
                continue
            out.append(_untab(t[i:j]) if r.strip else t[i:j])
            i = j
        r.input = "".join(out)


def _untab(text: str) -> str:
    """`text` with the leading tabs of each line dropped, as `<<-` reads a body."""
    return "\n".join(line.lstrip("\t") for line in text.split("\n"))


def _past_reserved(words, plain) -> int:
    """The index past the reserved words that lead `words` and keep command position:
    those that start a command, `time -p`, and `function` with its name."""
    k = 0
    while k < len(words):
        if not plain[k]:
            return k
        text = words[k].text
        if text in _STARTERS:
            k += 1
            if text == "time" and k < len(words) and words[k].text == "-p":
                k += 1
            continue
        if text == "function":
            k += 2
            continue
        return k
    return min(k, len(words))


# --- PowerShell -------------------------------------------------------------------------

#: The words that start a PowerShell statement, which runs nothing itself.
_PS_STATEMENTS = frozenset({"if", "elseif", "else", "switch", "while", "for", "foreach",
                            "do", "until", "try", "catch", "finally", "trap", "function",
                            "filter", "param", "begin", "process", "end"})
#: PowerShell's named escapes, kept as written.
_PS_NAMED = frozenset("0abefnrtuv")


class _Ps(_Reader):
    def whole_sub(self, raw: str) -> bool:
        if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
            raw = raw[1:-1]
            return raw.startswith("$(") and raw.endswith(")")
        return raw.startswith(("$(", "@(", "(")) and raw.endswith(")")

    def add_word(self, sw: ShellWord, *, plain: bool, end: int | None = None) -> None:
        """Add `sw`, split where an assignment's operator is written against what follows
        it (`$x=Stop-Process`, `$x =Stop-Process`): PowerShell ends a variable at `=`, so
        the pipeline after it starts a word of its own."""
        while end is not None and _ps_targets_only([w.text for w in self.cur.words]):
            cut = _ps_assignment_cut(sw.text, first=not self.cur.words)
            if cut is None or self.t[sw.position:sw.position + cut] != sw.text[:cut]:
                break
            position = sw.position + cut
            rest = sw.text[cut:]
            is_sub = (rest == "\0" and len(sw.subs) == 1
                      and self.whole_sub(self.t[position:end]))
            super().add_word(_shell_word(sw.text[:cut], sw.position, (), is_sub=False),
                             plain=True)
            sw = _shell_word(rest, position, sw.subs, is_sub=is_sub, kept=sw._kept_escape)
        super().add_word(sw, plain=plain, end=end)

    def read(self, i: int, *, close: bool = False) -> int:
        t, n = self.t, self.n
        while i < n:
            c = t[i]
            if self.word is None:
                if c in " \t\r":
                    i += 1
                    continue
                if c == "#":
                    self.mark("comment", i)
                    j = t.find("\n", i, n)
                    i = n if j < 0 else j
                    continue
                if t.startswith("<#", i):
                    self.mark("comment", i)
                    j = t.find("#>", i + 2, n)
                    if j < 0:
                        self.mark("unclosed", i)
                    i = n if j < 0 else j + 2
                    continue
                if c in "&." and self.call_position(i):
                    self.add_word(_shell_word(c, i, (), is_sub=False), plain=True)
                    i += 1
                    continue
                if t.startswith(("@'", '@"'), i) and self.opens_here_string(i):
                    i = self.here_string(i)
                    continue
            if c in " \t\r":
                self.end_word(i)
                i += 1
                continue
            if c == "\n":
                self.operator("\n", i)
                i += 1
                continue
            if t.startswith(("&&", "||"), i):
                self.operator(t[i:i + 2], i)
                i += 2
                continue
            if c in ";|&{}":
                self.operator(c, i)
                i += 1
                continue
            if c == ">":
                i = self.redirect(i)
                continue
            if c == ")" and close:
                self.end_word(i)
                return i
            if c == "(" or (c in "$@" and t[i + 1:i + 2] == "("):
                if c == "$":
                    self.mark("dollar", i)
                inner = i + (1 if c == "(" else 2)
                i = self.substitution(i, inner, self.start(i))
                continue
            if c == "$":
                self.mark("dollar", i)
                w = self.start(i)
                if t[i + 1:i + 2] == "{":
                    j = t.find("}", i + 2, n)
                    if j < 0:
                        self.mark("unclosed", i)
                    j = n if j < 0 else j + 1
                    w.chars.append(t[i:j])
                    w.plain = False
                    i = j
                    continue
                w.chars.append(c)
                i += 1
                continue
            if c == "'":
                i = self.single(i)
                continue
            if c == '"':
                i = self.double(i)
                continue
            if c == "`":
                i = self.escape(i, self.start(i))
                continue
            w = self.start(i)
            self.control(c, i)
            w.chars.append(c)
            i += 1
        return n

    def call_position(self, i: int) -> bool:
        """Whether the `&` or `.` at `i` is a call operator: it stands alone at a
        command's start, and an `&` doesn't follow a block's `}`."""
        t, n = self.t, self.n
        if self.cur.words or t.startswith("&&", i):
            return False
        if i + 1 < n and t[i + 1] not in " \t\r\n'\"$({":
            return False
        return t[i] == "." or not (self.done and self.done[-1][1] == "}")

    def substitution(self, open_position: int, inner: int, w: _Word) -> int:
        end = _Ps(self.t, self.marks, self.n).read(inner, close=True)
        if end >= self.n:
            self.mark("unclosed", open_position)
            w.sub(self.t[inner:self.n])
            return self.n
        w.sub(self.t[inner:end])
        return end + 1

    def escape(self, i: int, w: _Word) -> int:
        """A backtick at `i` and the character it escapes."""
        t, n = self.t, self.n
        self.mark("backtick", i)
        w.plain = False
        if i + 1 >= n:
            return n
        d = t[i + 1]
        if d == "\n":
            return i + 2
        if d == "\r" and t[i + 2:i + 3] == "\n":
            return i + 3
        if d in _PS_NAMED:
            w.chars.append(t[i:i + 2])
            w.kept = True
        else:
            w.chars.append(d)
        return i + 2

    def single(self, i: int) -> int:
        t, n = self.t, self.n
        w = self.start(i)
        w.plain = False
        j = i + 1
        while j < n:
            if t[j] == "'":
                if t[j + 1:j + 2] == "'":
                    w.chars.append("'")
                    j += 2
                    continue
                return j + 1
            w.chars.append(t[j])
            j += 1
        self.mark("unclosed", i)
        return n

    def double(self, i: int) -> int:
        t, n = self.t, self.n
        w = self.start(i)
        w.plain = False
        j = i + 1
        while j < n:
            c = t[j]
            if c == '"':
                if t[j + 1:j + 2] == '"':
                    w.chars.append('"')
                    j += 2
                    continue
                return j + 1
            if c == "`":
                j = self.escape(j, w)
                continue
            if c == "$" and t[j + 1:j + 2] == "(":
                self.mark("dollar", j)
                j = self.substitution(j, j + 2, w)
                continue
            if c == "$":
                self.mark("dollar", j)
            w.chars.append(c)
            j += 1
        self.mark("unclosed", i)
        return n

    def opens_here_string(self, i: int) -> bool:
        j = i + 2
        while j < self.n and self.t[j] in " \t\r":
            j += 1
        return j < self.n and self.t[j] == "\n"

    def here_string(self, i: int) -> int:
        """A here-string opened at `i`: one word, its lines up to a line that starts with
        its closing quote and `@`."""
        t, n = self.t, self.n
        quote = t[i + 1]
        w = self.start(i)
        w.plain = False
        body = t.find("\n", i, n) + 1
        j = body
        close_at = -1
        while j <= n:
            if t.startswith(quote + "@", j):
                close_at = j
                break
            k = t.find("\n", j, n)
            if k < 0:
                break
            j = k + 1
        stop = n if close_at < 0 else close_at
        text_end = stop - 1 if stop > body and t[stop - 1] == "\n" else stop
        if text_end > body and t[text_end - 1] == "\r":
            text_end -= 1
        if quote == "'":
            w.chars.append(t[body:text_end])
        else:
            k = body
            while k < text_end:
                c = t[k]
                if c == "`":
                    k = self.escape(k, w)
                    continue
                if c == "$" and t[k + 1:k + 2] == "(":
                    self.mark("dollar", k)
                    end = _Ps(t, self.marks, text_end).read(k + 2, close=True)
                    w.sub(t[k + 2:end])
                    k = min(end + 1, text_end)
                    continue
                if c == "$":
                    self.mark("dollar", k)
                w.chars.append(c)
                k += 1
        if close_at < 0:
            self.mark("unclosed", i)
            return n
        return close_at + 2

    def redirect(self, i: int) -> int:
        t = self.t
        fd = self.fd_word("123456*")
        if t.startswith(">&", i) and t[i + 2:i + 3] in tuple("123456"):
            op = t[i:i + 3]
        elif t.startswith(">>", i):
            op = ">>"
        else:
            op = ">"
        r = self.add_redirect(op, fd, i)
        self.mark("redirect", i)
        if "&" not in op:
            self.target_for = r
        return i + len(op)


# --- cmd --------------------------------------------------------------------------------

#: cmd's words that take the rest of their command as something to evaluate.
_CMD_CONTROL = frozenset({"if", "for", "call", "start", "else"})


class _Cmd(_Reader):
    def read(self, i: int, *, close: bool = False) -> int:
        t, n = self.t, self.n
        while i < n:
            c = t[i]
            if self.word is None:
                if c in " \t\r":
                    i += 1
                    continue
                if c == "@" and not self.cur.words:
                    self.add_word(_shell_word("@", i, (), is_sub=False), plain=True)
                    i += 1
                    continue
            if c in " \t\r":
                self.end_word(i)
                i += 1
                continue
            if t.startswith(("&&", "||"), i):
                self.operator(t[i:i + 2], i)
                i += 2
                continue
            if c in "&|()\n":
                self.operator(c, i)
                i += 1
                continue
            if c in "<>":
                fd = self.fd_word("0123456789")
                if t.startswith(">&", i) and t[i + 2:i + 3].isdigit():
                    op = t[i:i + 3]
                elif t.startswith(">>", i):
                    op = ">>"
                else:
                    op = c
                r = self.add_redirect(op, fd, i)
                self.mark("redirect", i)
                if "&" not in op:
                    self.target_for = r
                i += len(op)
                continue
            w = self.start(i)
            if c == '"':
                w.plain = False
                j = t.find('"', i + 1, n)
                if j < 0:
                    self.mark("unclosed", i)
                    w.chars.append(t[i + 1:n])
                    return n
                w.chars.append(t[i + 1:j])
                i = j + 1
                continue
            if c == "^":
                w.plain = False
                if i + 1 < n and t[i + 1] != "\n":
                    w.chars.append(t[i + 1])
                i += 2
                continue
            self.control(c, i)
            w.chars.append(c)
            i += 1
        return n


# --- the command word --------------------------------------------------------------------

#: Each `sh` wrapper: its options without a value, its options with one, and what comes
#: after its options ("command", "assignments" for `env`, "duration" for `timeout`). Each
#: option of the tool that still runs the command is named, short and long, as the tool's
#: own manual gives it. `nice` also takes `-N`, a number. `env -S`'s value is itself a
#: command line env splits; it is read as a value, so what it runs isn't read.
_WRAPPERS: dict[str, tuple[frozenset[str], frozenset[str], str]] = {
    "sudo": (frozenset({"-A", "-b", "-B", "-E", "-H", "-k", "-n", "-N", "-P", "-S",
                        "--askpass", "--background", "--bell", "--preserve-env",
                        "--set-home", "--reset-timestamp", "--non-interactive",
                        "--no-update", "--preserve-groups", "--stdin"}),
             frozenset({"-C", "-D", "-g", "-h", "-p", "-r", "-R", "-t", "-T", "-u", "-U",
                        "--close-from", "--chdir", "--group", "--host", "--prompt",
                        "--role", "--chroot", "--type", "--command-timeout", "--user",
                        "--other-user"}),
             "command"),
    "doas": (frozenset({"-n"}), frozenset({"-a", "-u"}), "command"),
    "env": (frozenset({"-i", "-0", "-v", "-", "--ignore-environment", "--null", "--debug",
                       "--block-signal", "--default-signal", "--ignore-signal",
                       "--list-signal-handling"}),
            frozenset({"-a", "-u", "-C", "-S", "--argv0", "--unset", "--chdir",
                       "--split-string"}), "assignments"),
    "nohup": (frozenset(), frozenset(), "command"),
    "builtin": (frozenset(), frozenset(), "command"),
    "exec": (frozenset({"-c", "-l"}), frozenset({"-a"}), "command"),
    "command": (frozenset({"-p"}), frozenset(), "command"),
    "nice": (frozenset(), frozenset({"-n", "--adjustment"}), "command"),
    "timeout": (frozenset({"-f", "-p", "-v", "--foreground", "--preserve-status",
                           "--verbose"}),
                frozenset({"-s", "-k", "--signal", "--kill-after"}), "duration"),
    "setsid": (frozenset({"-c", "-f", "-w", "--ctty", "--fork", "--wait"}), frozenset(),
               "command"),
    "stdbuf": (frozenset(), frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
               "command"),
}

#: PowerShell's assignment operators: the pipeline on their right is a command.
_PS_ASSIGNMENTS = ("??=", "+=", "-=", "*=", "/=", "%=", "=")


def command_word_index(command: ShellCommand, *, dialect: str = "sh") -> int | None:
    """The index in `command.words` of the word that names what it runs: past
    `NAME=value` words, the reserved words that start a command and the wrappers with
    the options their table names, short or long (`sh`), an assignment's targets and its
    operator (`$x =`, `[int]$x +=`) and the call operators `&` and `.` (`powershell`),
    or a leading `@` (`cmd`). None when nothing is left, when the command starts with a
    word that runs nothing (`}`, `fi`, `done`, `esac`, `for`, `select`, `case`, `[[`, a
    `((...))` word, a PowerShell statement word, cmd's `if`, `for`, `call`, `start` and
    `else`), and where an option a wrapper's row doesn't name ends the read."""
    if dialect not in DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}; the dialects are {DIALECTS}")
    words = [w.text for w in command.words]
    if dialect == "powershell":
        k = 0
        while (past := _ps_past_assignment(words, k)) is not None:
            k = past
        while k < len(words) and words[k] in ("&", "."):
            k += 1
        if k >= len(words) or words[k].lower() in _PS_STATEMENTS:
            return None
        return k
    if dialect == "cmd":
        k = 0
        while k < len(words) and words[k] == "@":
            k += 1
        if k >= len(words) or words[k].lower() in _CMD_CONTROL:
            return None
        return k
    return _sh_command_word(words)


def _ps_targets_only(words: list[str]) -> bool:
    """Whether `words`, the start of a PowerShell command, are assignments and then
    assignment targets only, so a word after them may still be a target or an operator."""
    k = 0
    while (past := _ps_past_assignment(words, k)) is not None:
        k = past
    return all(_ps_target_piece(word, first=j == k) for j, word in enumerate(words[k:], k))


def _ps_target_piece(word: str, *, first: bool) -> bool:
    """Whether `word` can be part of an assignment's targets: a variable or a cast, after
    a comma written against it, or a lone comma after the first target."""
    target = word.lstrip(",")
    return target.startswith(("$", "[")) or (not target and not first)


def _ps_assignment_cut(word: str, *, first: bool) -> int | None:
    """Where the pipeline starts in `word` when an assignment's operator is written
    against it: past `$x=` in `$x=Stop-Process`, past `=` in `=Stop-Process` when a
    target comes before; None otherwise."""
    if not first:
        for op in _PS_ASSIGNMENTS:
            if word.startswith(op) and len(word) > len(op):
                return len(op)
    if not word.startswith(("$", "[")):
        return None
    for position, ch in enumerate(word):
        if ch in "\0\"' \t":
            return None
        for op in _PS_ASSIGNMENTS:
            if word.startswith(op, position) and position + len(op) < len(word):
                return position + len(op) if position else None
        if ch == "=":
            return None
    return None


def _ps_past_assignment(words: list[str], k: int) -> int | None:
    """The index past an assignment that starts at `k` (its targets, each a variable or a
    cast such as `$x`, `$x.y`, `[int]$x` or `$a, $b`, and its operator, standing alone or
    written against the last target), or None when none starts there."""
    for j in range(k, len(words)):
        word = words[j]
        if j > k and word in _PS_ASSIGNMENTS:
            return j + 1
        if not _ps_target_piece(word, first=j == k):
            return None
        if word.lstrip(",").endswith(_PS_ASSIGNMENTS):
            return j + 1
    return None


def _is_assignment(word: str) -> bool:
    name, eq, _ = word.partition("=")
    return bool(eq and name and (name[0].isalpha() or name[0] == "_")
                and all(ch.isalnum() or ch == "_" for ch in name))


def _sh_command_word(words: list[str]) -> int | None:
    k, n = 0, len(words)
    while k < n:
        text = words[k]
        if text in _STARTERS:
            k += 1
            if text == "time" and k < n and words[k] == "-p":
                k += 1
            continue
        if text == "function":
            k += 2
            continue
        if text == "coproc":
            k += 3 if k + 2 < n and words[k + 2] == "{" else 1
            continue
        if text in _RUNS_NOTHING or (text.startswith("((") and text.endswith("))")):
            return None
        if _is_assignment(text):
            k += 1
            continue
        break
    while k < n:
        name = _base(words[k])
        if name not in _WRAPPERS:
            return k
        got = _past_options(words, k + 1, name)
        if got is None:
            return None
        k = got
    return None


def _past_options(words: list[str], k: int, name: str) -> int | None:
    """The index past wrapper `name`'s options and what its row says follows them, or
    None at an option the row doesn't name."""
    plain, valued, then = _WRAPPERS[name]
    n = len(words)
    while k < n:
        word = words[k]
        if word == "--":
            k += 1
            break
        if word in plain:
            k += 1
            continue
        if word in valued:
            k += 2
            continue
        if word.startswith("--") and "=" in word:
            if word.split("=", 1)[0] in plain | valued:
                k += 1
                continue
            return None
        if name == "nice" and word.startswith("-") and word[1:].isdigit():
            k += 1
            continue
        if word.startswith("-") and len(word) > 1 and not word.startswith("--"):
            got = _cluster(word, plain, valued)
            if got is None:
                return None
            k += got
            continue
        if word.startswith("-"):
            return None
        break
    if then == "assignments":
        while k < n and _is_assignment(words[k]):
            k += 1
    elif then == "duration":
        k += 1
    return k


def _cluster(word: str, plain: frozenset[str], valued: frozenset[str]) -> int | None:
    """How many words a cluster of short options takes (1, or 2 when its last option's
    value is the next word), or None when a letter is no option the row names."""
    for position in range(1, len(word)):
        option = "-" + word[position]
        if option in plain:
            continue
        if option in valued:
            return 1 if position + 1 < len(word) else 2
        return None
    return 1


def _base(word: str) -> str:
    return word.replace("\\", "/").rsplit("/", 1)[-1]


# --- scripts handed to a shell ------------------------------------------------------------

_SH_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash"})
_SH_LONG = frozenset({"--login", "--norc", "--noprofile", "--posix", "--noediting",
                      "--restricted", "--verbose"})
_SH_LONG_VALUED = frozenset({"--rcfile", "--init-file"})
_PS_PLAIN = frozenset({"-noprofile", "-nop", "-nologo", "-nol", "-noninteractive",
                       "-noni", "-noexit", "-noe", "-mta", "-sta"})
_PS_VALUED = frozenset({"-executionpolicy", "-ep", "-ex", "-windowstyle", "-w",
                        "-workingdirectory", "-wd", "-inputformat", "-if", "-inp",
                        "-outputformat", "-of", "-o"})
_PS_ENCODED = frozenset({"-encodedcommand", "-e", "-ec"})
_CMD_PLAIN = frozenset({"/q", "/d", "/a", "/u", "/s"})
_CMD_RUN = frozenset({"/c", "/k", "//c", "//k"})

#: What a shell's arguments give: its argument's text, its input, or nothing.
_INPUT = object()


def shell_scripts(shell_text: ShellText, *, dialect: str = "sh") -> tuple[ShellScript, ...]:
    """Each piece of text a command of `shell_text` (read in `dialect`) hands a shell to
    run: a shell's script argument (`-c`, `-Command`, `/c`), or, for a shell that runs its
    input, a heredoc or here-string on its own command or the literal text piped into it.
    A command runs a shell when its command word's last part across `/` and `\\`, with
    `.exe` dropped, is one in any case: `sh`, `bash`, `zsh`, `dash`, `ksh`, `mksh`, `ash`,
    `pwsh`, `powershell` or `cmd`. One level: a caller
    reads each script with `read_shell` and asks again for deeper ones."""
    if dialect not in DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}; the dialects are {DIALECTS}")
    out: list[ShellScript] = []
    commands = shell_text.commands
    for index, command in enumerate(commands):
        k = command_word_index(command, dialect=dialect)
        if k is None or command.words[k].is_sub:
            continue
        name = _shell_name(command.words[k].text)
        if name is None:
            continue
        rest = command.words[k + 1:]
        if name in _SH_SHELLS:
            got, shell_dialect = _sh_argument(rest), "sh"
        elif name in ("pwsh", "powershell"):
            got, shell_dialect = _ps_argument(rest, bare_runs=name == "powershell"), \
                "powershell"
        else:
            got, shell_dialect = _cmd_argument(rest), "cmd"
        if got is None:
            continue
        if got is _INPUT:
            found = _input(commands, index, dialect)
            if found is not None:
                out.append(ShellScript(found[0], shell_dialect, index, found[1]))
            continue
        text = _joined([rest[i] for i in got])
        if text is not None:
            out.append(ShellScript(text, shell_dialect, index, "argument",
                                   tuple(k + 1 + i for i in got)))
    return tuple(out)


def _shell_name(word: str) -> str | None:
    """The shell a command word names, in lower case, or None: its last part across `/`
    and `\\`, with `.exe` dropped, compared in any case on every system, since the text
    doesn't say which system runs it."""
    name = _base(word).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name if name in _SH_SHELLS or name in ("pwsh", "powershell", "cmd") else None


def _joined(words) -> str | None:
    if any(w._kept_escape for w in words):
        return None
    return " ".join(w.text for w in words)


def _sh_argument(words) -> object:
    """What a POSIX shell's arguments `words` give: the index of its `-c` script's word,
    as a 1-tuple, `_INPUT`, or None. A cluster's letters are all flags, but `o` and `O`
    each take the next word, as bash reads `-co pipefail 'cmd'`; `c` and `s` count after
    `-` or `+`, as bash counts them."""
    k, n = 0, len(words)
    runs_argument = reads_input = False
    while k < n:
        word = words[k].text
        if word in ("--", "-"):
            k += 1
            break
        if word in _SH_LONG:
            k += 1
            continue
        if word in _SH_LONG_VALUED:
            k += 2
            continue
        letters = word[1:]
        if word[:1] in ("-", "+") and letters and all(
                ch.isascii() and ch.isalpha() for ch in letters):
            runs_argument = runs_argument or "c" in letters
            reads_input = reads_input or "s" in letters
            k += 1 + letters.count("o") + letters.count("O")
            continue
        if word[:1] in ("-", "+"):
            return None
        break
    if runs_argument:
        return (k,) if k < n else None
    if reads_input or k >= n:
        return _INPUT
    return None


def _ps_argument(words, *, bare_runs: bool) -> object:
    """What PowerShell's arguments `words` give: the indexes of the words its script is
    joined from, `_INPUT`, or None."""
    k, n = 0, len(words)
    while k < n:
        word = words[k].text.lower()
        if word in _PS_PLAIN:
            k += 1
            continue
        if word in _PS_VALUED:
            k += 2
            continue
        if word == "-c" or (len(word) >= 4 and "-command".startswith(word)):
            if k + 1 < n and words[k + 1].text == "-":
                return _INPUT
            return tuple(range(k + 1, n)) or None
        if word == "-file":
            return _INPUT if k + 1 < n and words[k + 1].text == "-" else None
        if word in _PS_ENCODED or word.startswith("-"):
            return None
        return tuple(range(k, n)) if bare_runs else None
    return _INPUT


def _cmd_argument(words) -> object:
    """What cmd's arguments `words` give: the indexes of the words its script is joined
    from, `_INPUT`, or None."""
    for k, w in enumerate(words):
        word = w.text.lower()
        if word in _CMD_RUN:
            return tuple(range(k + 1, len(words))) or None
        if word in _CMD_PLAIN or word[:3] in ("/e:", "/f:", "/v:", "/t:"):
            continue
        return None
    return _INPUT


def _stdin(command: ShellCommand) -> tuple[str, str] | None | bool:
    """What a command's last input redirect gives: `(text, source)` for a heredoc or a
    here-string, False for any other input redirect, None for none."""
    for r in reversed(command.redirects):
        if r.fd not in (None, "0") or not r.operator.startswith("<"):
            continue
        if r.operator in ("<<", "<<-"):
            return (r._input if r._input is not None else r.body or ""), "heredoc"
        if r.operator == "<<<" and r.target is not None:
            return r.target.text, "here-string"
        return False
    return None


def _writes_elsewhere(command: ShellCommand) -> bool:
    return any((r.operator.startswith(">") and r.fd in (None, "1"))
               or r.operator.startswith("&>") for r in command.redirects)


def _input(commands, index: int, dialect: str) -> tuple[str, str] | None:
    """The text a shell's command at `index` reads as its input, and its source."""
    own = _stdin(commands[index]) if dialect == "sh" else None
    if own is False:
        return None
    if own is not None:
        return own
    if index == 0 or commands[index - 1].ended_by != "|":
        return None
    text = _piped(commands[index - 1], dialect)
    return None if text is None else (text, "pipe")


def _piped(source: ShellCommand, dialect: str) -> str | None:
    """The text `source` writes to a pipe, from the sources the reader reads: `echo` with
    no option but `-n` and no backslash, `cat` of a heredoc or here-string (`sh`), and
    `echo` or `Write-Output` with no parameter (`powershell`)."""
    k = command_word_index(source, dialect=dialect)
    if k is None or _writes_elsewhere(source):
        return None
    name = _base(source.words[k].text)
    rest = source.words[k + 1:]
    if dialect == "powershell":
        if name.lower() not in ("echo", "write-output") or any(
                w.text.startswith("-") for w in rest):
            return None
        return None if any(w._kept_escape for w in rest) else "\n".join(w.text for w in rest)
    if dialect != "sh":
        return None
    if name == "echo":
        j = 0
        while j < len(rest) and rest[j].text.startswith("-") and len(rest[j].text) > 1 \
                and all(ch in "neE" for ch in rest[j].text[1:]):
            if rest[j].text != "-n":
                return None
            j += 1
        words = rest[j:]
        if any("\\" in w.text for w in words):
            return None
        return " ".join(w.text for w in words)
    if name == "cat" and all(w.text == "-" for w in rest):
        got = _stdin(source)
        return got[0] if got else None
    return None


__all__ = ["DIALECTS", "MARK_KINDS", "SCRIPT_SOURCES", "ShellCommand", "ShellMark",
           "ShellRedirect", "ShellScript", "ShellText", "ShellWord", "command_word_index",
           "read_shell", "shell_scripts"]
