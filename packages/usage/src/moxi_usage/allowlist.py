"""What a subagent past its worker ceiling may still do: hand off, commit, return.

`allowed(tool, tool_input, cwd, *, handoff_roots, work_roots, commands, home)` answers
`(True, "")` for a call that wraps the work up and `(False, reason)` for anything else,
the reason short and naming the rule that refused it. It is pure: standard library only,
no subprocess, no file read or write; its only contact with the filesystem is
`os.path.realpath` on the paths it judges. The ceiling (`moxi_usage.ceiling`) asks it for
every call of an agent whose hard marker has latched, after its own return-tool check,
so the return tools are not handled here.

Allowed:

* `Write` and `Edit` of a `.md` file under one of `handoff_roots`;
* `Read` of the same paths, with `limit` a whole number from 1 to 200 and `offset`
  absent or a whole number of at least 0;
* `Bash` running one of git's wrap-up forms (status, diff, add, commit and a few reads,
  `GIT_FORMS`), as `git [-C <dir>] <form>` or `cd <dir> && git <form>`;
* `Bash` running a command whose words start with one of `commands`, the argument-word
  prefixes the user adds (`ceiling.allow_commands`), bare or after `cd <dir> &&`.

The directory a command runs in is `<dir>` when named, else `cwd`, and must be `cwd` or
lie under it or under one of `work_roots`.

**The path rule.** Every path accepted (a hand-off file, a `<dir>`, an `add --` path) lies
inside its root both as the shell or git reads it (as typed, joined to its base, with
`..` taken off lexically) and fully resolved (the real path of the path as typed and of
that lexical form). If any reading falls outside, the call is refused. `cd` and git's
pathspecs read `..` lexically, while `git -C` and the file tools follow links, so a path
must pass both readings.

**The lexer.** The shell text goes through a quote-aware lexer that refuses every control
operator but the one `&&` after `cd <dir>`, every redirection, `$` and backticks outside
single quotes, newlines outside quotes, and the expansion characters (glob, brace,
subshell, and a tilde other than a leading `~/`). This limits work; it is not a security
boundary: the hooks `git commit` runs still run.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence

#: The suffix a hand-off file must have.
HANDOFF_SUFFIX = ".md"
#: The most lines one hand-off `Read` may ask for.
READ_LIMIT_MAX = 200

#: git's wrap-up forms, as the words after `git` (and after `-C <dir>`). `add -- <path>...`
#: and `commit -m <text> [-m <text>]...` take arguments and are matched apart.
GIT_FORMS = frozenset({
    ("status", "--short"),
    ("status", "--porcelain"),
    ("diff", "--stat"),
    ("diff", "--cached", "--stat"),
    ("diff", "--check"),
    ("diff", "--cached", "--check"),
    ("diff",),
    ("diff", "--cached"),
    ("add", "-A"),
    ("add", "--all"),
    ("add", "-u"),
    ("add", "--update"),
    ("rev-parse", "HEAD"),
    ("log", "-1", "--oneline"),
    ("branch", "--show-current"),
})

#: Outside quotes, each of these starts an operator, a redirection, an expansion or a
#: subshell.
_UNQUOTED_REFUSED = {
    ";": "control operator ';'",
    "|": "control operator '|'",
    "<": "redirection",
    ">": "redirection",
    "(": "subshell or pattern '('",
    ")": "subshell or pattern ')'",
    "$": "'$' outside single quotes",
    "`": "backtick outside single quotes",
    "*": "glob '*'",
    "?": "glob '?'",
    "[": "glob '['",
    "{": "brace '{'",
    "}": "brace '}'",
    "\\": "backslash outside quotes",
}

#: The `&&` token among a command's words.
AND = object()


class Refused(Exception):
    """A call the allowlist does not accept; the message says which rule refused it."""


def allowed(tool: object, tool_input: object, cwd: object, *,
            handoff_roots: Iterable[str] | str = (), work_roots: Iterable[str] | str = (),
            commands: Iterable[Sequence[str]] = (), home: str | None = None
            ) -> tuple[bool, str]:
    """Is this call a wrap-up operation? `(True, "")`, or `(False, reason)`.

    `handoff_roots` and `work_roots` are absolute directories (a relative one is
    ignored); `commands` are argument-word prefixes; `home` is what a leading `~/`
    expands to, the process's home when None. A malformed argument never allows a call.
    """
    try:
        if not isinstance(tool_input, Mapping):
            raise Refused("the tool's input is not an object")
        if tool in ("Write", "Edit"):
            _handoff_path(tool_input, cwd, handoff_roots)
        elif tool == "Read":
            _handoff_path(tool_input, cwd, handoff_roots)
            _read_bounds(tool_input)
        elif tool == "Bash":
            _bash(tool_input.get("command"), cwd, work_roots, commands, home)
        else:
            raise Refused(f"{tool or 'this tool'} is not a wrap-up operation")
    except Refused as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 - a malformed argument never allows a call
        return False, f"the call could not be checked ({type(exc).__name__})"
    return True, ""


# --- paths ------------------------------------------------------------------------------

def _real(path: str) -> str:
    return os.path.realpath(path)


def _under(path: str, root: str) -> bool:
    """True when `path` is `root` or lies under it."""
    return path == root or path.startswith(root.rstrip("/") + "/")


def _abs_roots(roots: object) -> list[str]:
    """The usable roots as given: absolute strings only."""
    if isinstance(roots, str):
        roots = [roots]
    if not isinstance(roots, (list, tuple)):
        return []
    return [r for r in roots if isinstance(r, str) and os.path.isabs(r)]


def _inside(path: str, base: str, roots: list[str]) -> tuple[str, str] | None:
    """The one path rule. `path` is as typed (a relative one joins `base`); `roots` are
    absolute.

    The lexical reading, `normpath(base/path)`, must lie under a root's lexical form or
    its real path (both name the root); the real path of the path as typed and of the
    lexical reading must each lie under a root's real path. Returns (lexical, real), or
    None when any reading is outside every root.
    """
    joined = path if os.path.isabs(path) else os.path.join(base, path)
    lexical = os.path.normpath(joined)
    lex_roots = [os.path.normpath(r) for r in roots]
    real_roots = [_real(r) for r in roots]
    if not any(_under(lexical, r) for r in lex_roots + real_roots):
        return None
    for p in (joined, lexical):
        if not any(_under(_real(p), r) for r in real_roots):
            return None
    return lexical, _real(joined)


def _cwd(cwd: object) -> str:
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise Refused("the working directory is not an absolute path")
    return cwd


# --- Write, Edit, Read ------------------------------------------------------------------

def _handoff_path(tool_input: Mapping, cwd: object, roots: object) -> None:
    p = tool_input.get("file_path")
    if not isinstance(p, str) or not p:
        raise Refused("no file_path")
    if not p.endswith(HANDOFF_SUFFIX):
        raise Refused("a hand-off file must end in .md")
    base = cwd if os.path.isabs(p) else _cwd(cwd)
    got = _inside(p, base, _abs_roots(roots))
    if got is None:
        raise Refused("the hand-off file is outside every hand-off root")
    lexical, real = got
    if not (lexical.endswith(HANDOFF_SUFFIX) and real.endswith(HANDOFF_SUFFIX)
            and _real(lexical).endswith(HANDOFF_SUFFIX)):
        raise Refused("a hand-off file must end in .md")


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _read_bounds(tool_input: Mapping) -> None:
    limit = tool_input.get("limit")
    if not _is_int(limit) or not 1 <= limit <= READ_LIMIT_MAX:
        raise Refused(f"a hand-off Read needs a limit from 1 to {READ_LIMIT_MAX}")
    offset = tool_input.get("offset")
    if offset is not None and (not _is_int(offset) or offset < 0):
        raise Refused("a hand-off Read's offset must be a whole number of at least 0")


# --- Bash -------------------------------------------------------------------------------

def lex(text: str, home: str) -> list:
    """Shell text as words and `AND` tokens, refusing everything else the allowlist does.

    Single quotes are literal (newlines, `$` and backticks allowed inside). Double quotes
    allow newlines and the usual backslash escapes, but no `$` or backtick. Outside quotes
    only spaces and tabs part words and `&&` is the only operator. A word that starts with
    an unquoted `~/` is expanded with `home`, as the shell does. Raises `Refused`.
    """
    tokens: list = []
    word: list[str] | None = None        # None between words, else its characters so far
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise Refused("unterminated single quote")
            word = (word or []) + list(text[i + 1:j])
            i = j + 1
            continue
        if c == '"':
            buf: list[str] = []
            i += 1
            while True:
                if i >= n:
                    raise Refused("unterminated double quote")
                d = text[i]
                if d == '"':
                    i += 1
                    break
                if d == "$":
                    raise Refused("'$' outside single quotes")
                if d == "`":
                    raise Refused("backtick outside single quotes")
                if d == "\\" and i + 1 < n and text[i + 1] in '"\\\n':
                    if text[i + 1] != "\n":      # a backslash-newline joins lines: dropped
                        buf.append(text[i + 1])
                    i += 2
                    continue
                buf.append(d)
                i += 1
            word = (word or []) + buf
            continue
        if c in " \t":
            if word is not None:
                tokens.append("".join(word))
                word = None
            i += 1
            continue
        if c == "\n":
            raise Refused("newline outside quotes")
        if c == "&":
            if text[i:i + 2] == "&>":
                raise Refused("redirection")
            if text[i:i + 2] != "&&":
                raise Refused("control operator '&'")
            if word is not None:
                tokens.append("".join(word))
                word = None
            tokens.append(AND)
            i += 2
            continue
        if c in _UNQUOTED_REFUSED:
            raise Refused(_UNQUOTED_REFUSED[c])
        if ord(c) < 32 or c == "\x7f":
            raise Refused("control character outside quotes")
        if c == "#" and word is None:
            raise Refused("comment '#'")
        if c == "~":
            if word is not None or text[i + 1:i + 2] != "/":
                raise Refused("tilde other than a leading '~/'")
            word = list(home)
            i += 1
            continue
        word = (word or []) + [c]
        i += 1
    if word is not None:
        tokens.append("".join(word))
    return tokens


def _resolve_dir(d: str, cwd: str, work_roots: object, logical: bool) -> str:
    """The directory `<dir>` names: absolute, `~/` (the lexer expanded it) or relative to
    `cwd`; by the path rule it must be `cwd` or lie under it or a work root. Returns the
    directory the command runs in: for `cd` (logical, as `cd -L` moves) the real path of
    the lexical reading, for `git -C` (a physical change of directory) the real path of
    the path as typed."""
    if not d:
        raise Refused("empty directory")
    if d.startswith("-"):
        raise Refused("a directory may not start with '-'")
    got = _inside(d, cwd, [cwd] + _abs_roots(work_roots))
    if got is None:
        raise Refused("the repository directory is outside the working directory and "
                      "every work root")
    lexical, real = got
    return _real(lexical) if logical else real


def _git_form(args: list[str], repo: str) -> None:
    """`args`: the words after `git` and any `-C <dir>`. `repo`: the directory git runs
    in (a real path); git reads each `add --` path lexically from there."""
    t = tuple(args)
    if t in GIT_FORMS:
        return
    if t[:1] == ("add",) and len(t) >= 3 and t[1] == "--":
        for p in t[2:]:
            if not p:
                raise Refused("git add: empty path")
            if p.startswith(":"):
                raise Refused("git add: pathspec magic is not allowed")
            if _inside(p, repo, [repo]) is None:
                raise Refused("git add: a path outside the repository directory")
        return
    if t[:1] == ("commit",) and len(t) >= 3 and len(t) % 2 == 1:
        if all(t[k] == "-m" for k in range(1, len(t), 2)):
            return
    if not t:
        raise Refused("git with no subcommand")
    raise Refused(f"git {t[0]} is not a wrap-up git form")


def _prefixes(commands: object) -> list[tuple[str, ...]]:
    """The usable command prefixes: non-empty lists of non-empty strings."""
    if not isinstance(commands, (list, tuple)):
        return []
    out = []
    for c in commands:
        if (isinstance(c, (list, tuple)) and c
                and all(isinstance(w, str) and w for w in c)):
            out.append(tuple(c))
    return out


def _bash(command: object, cwd: object, work_roots: object, commands: object,
          home: str | None) -> None:
    if not isinstance(command, str) or not command.strip():
        raise Refused("no command")
    cwd = _cwd(cwd)
    if home is None:
        home = os.path.expanduser("~")
    if not isinstance(home, str) or not os.path.isabs(home):
        home = "/nonexistent-home"
    tokens = lex(command, home)
    ands = [k for k, t in enumerate(tokens) if t is AND]
    if ands:
        if len(ands) > 1:
            raise Refused("a second '&&'")
        if ands[0] != 2 or tokens[0] != "cd":
            raise Refused("'&&' is allowed only directly after cd <dir>")
        repo: str | None = _resolve_dir(tokens[1], cwd, work_roots, True)
        rest = tokens[3:]
        in_cd = True
    else:
        repo = None
        rest = tokens
        in_cd = False
    if not rest:
        raise Refused("nothing after '&&'")
    for prefix in _prefixes(commands):
        if tuple(rest[:len(prefix)]) == prefix:
            return
    if rest[0] != "git":
        raise Refused(f"{rest[0]} is not a wrap-up command")
    args = rest[1:]
    if args[:1] == ["-C"]:
        if in_cd:
            raise Refused("git -C after cd")
        if len(args) < 2:
            raise Refused("git -C with no directory")
        repo = _resolve_dir(args[1], cwd, work_roots, False)
        args = args[2:]
    elif repo is None:
        repo = _real(cwd)
    if args[:1] and args[0].startswith("-"):
        raise Refused(f"git option {args[0]} is not allowed")
    _git_form(args, repo)


__all__ = ["allowed", "lex", "Refused", "AND", "GIT_FORMS", "HANDOFF_SUFFIX",
           "READ_LIMIT_MAX"]
