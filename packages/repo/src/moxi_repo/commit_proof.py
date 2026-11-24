"""The commit-proof piece, `repo/commit-proof`: a commit message carries the line
`moxi repo rules` printed for the tree being committed.

It runs on commit-msg, fail closed, order 20, after the commit-rules piece, and is in no
repo until someone adds it:

    moxi repo git-hooks add PATH repo/commit-proof

**The line** is `Commit-Check: <h>`, where `<h>` is the first 12 hex characters of SHA-256
over `commit-check`, NUL, the repo's salt, NUL, and the tree id `git write-tree` gives for
the index. `moxi repo rules` prints it last, so a message that holds it shows the command
ran on the tree that was staged then. It is not a security boundary: anyone who can read
the repo's git config can make the line.

**The salt** is the piece's one setting, `salt`: 16 random hex characters written into
the repo's git config when the piece is first registered there, so a line copied from
another repo doesn't match. Every work tree of the repo shares it.

**Where the line is read**: every trailer paragraph after the subject's, a paragraph
whose every line is a trailer (`Key: value`) or continues one. A paragraph with a prose
line is prose and is left alone, so a body may quote a line.

**What passes without the line**: a commit repo's committer makes; a replay (a rebase,
cherry-pick or revert in progress), which rewrites a message that already exists; and a
clean merge, one `MERGE_HEAD` with the index's tree being what `git merge-tree` makes of
the two heads, which adds nothing of its own.

**What is refused**: no line, more than one, one that isn't 12 hex characters, and one
that doesn't match the tree being committed. Each refusal names `moxi repo rules` and the
work tree to run it in. A refused message is left as it is.

**The line once read**: the line means nothing once the piece has read it, so the piece
drops it from the message file, with the lines that continue its value, and the commit
is made without it. A message that matched loses every line read; a replay loses every
line read too; a clean merge loses the lines that hold the value for its tree. Lines in
the subject's paragraph or in a prose paragraph are not read, so they stay. A commit
the repo's committer makes is left as it is.

The tree is read with the environment git gave the hook. Under `git commit <path>` and
`git commit -a` git commits a temporary index and names it in `GIT_INDEX_FILE`; the tree
of that index is the one that lands. So an agent stages first, runs `moxi repo rules`,
then commits: `git commit -a` over unstaged changes is refused as stale, and so is a
commit whose pre-commit hook staged more.
"""

from __future__ import annotations

import hashlib
import os
import re

from moxi_repo.commit_rules import _kept_lines, _paragraphs, _Shape, git_settings
from moxi_repo.pieces import Outcome
from moxi_repo.run import quote_arg

#: The piece's declaration, read through the entry point `repo.commit-proof`.
COMMIT_PROOF = {
    "event": "commit-msg",
    "target": "moxi_repo.commit_proof:commit_msg",
    "fail": "closed",
    "order": 20,
    "doc": "Refuse a commit whose message lacks the Commit-Check line moxi repo rules "
           "prints for the staged tree.",
    "settings": {"salt": "one"},
}

PIECE_ID = "repo/commit-proof"
SALT = "salt"
#: The length of a salt, in hex characters.
SALT_CHARS = 16
_SALT = re.compile(r"[0-9a-fA-F]{%d}" % SALT_CHARS)
#: The trailer key of the line, and what the hash starts with.
KEY = "Commit-Check"
_PREFIX = b"commit-check"
#: The length of the line's value, in hex characters.
PROOF_CHARS = 12
_VALUE = re.compile(r"[0-9a-f]{%d}" % PROOF_CHARS)
#: The entries of a work tree's git dir that mean a rebase, cherry-pick or revert is in
#: progress there.
_REPLAY_STATE = ("rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD")


def new_salt() -> str:
    """A salt for a repo: 16 random hex characters."""
    import secrets

    return secrets.token_hex(SALT_CHARS // 2)


def salt_problem(value: str) -> str | None:
    """Why `value` can't be a repo's salt, or None when it is 16 hex characters."""
    if _SALT.fullmatch(value):
        return None
    return f"{value!r} is not a salt: a salt is {SALT_CHARS} hex characters (0-9, a-f)"


def proof_value(salt: str, tree: str) -> str:
    """The 12 hex characters of the line for a repo's `salt` and the tree id `tree`."""
    digest = hashlib.sha256(_PREFIX + b"\0" + salt.encode("utf-8") + b"\0"
                            + tree.encode("ascii"))
    return digest.hexdigest()[:PROOF_CHARS]


def proof_line(salt: str, tree: str) -> str:
    return f"{KEY}: {proof_value(salt, tree)}"


def salt_of(settings: dict[str, list[str]]) -> str:
    """The repo's salt from the piece's settings there, or "" when it has none."""
    values = settings.get(SALT) or [""]
    return values[-1]


def index_tree(top: str | os.PathLike, *, hook_env: bool = False) -> tuple[str | None, str]:
    """(the tree id `git write-tree` gives for the index of the work tree at `top`, "")
    or (None, why there is none).

    With `hook_env`, git's local variables are kept: inside a git hook `GIT_INDEX_FILE`
    names the index being committed, which under `git commit <path>` and `git commit -a`
    is not the work tree's own. Without it they are dropped, so the index read is the one
    of the work tree named.
    """
    from moxi_repo.gitrun import git

    done = git(top, "write-tree", clean_env=not hook_env)
    tree = done.stdout.strip()
    if done.returncode == 0 and tree:
        return tree, ""
    unmerged = git(top, "ls-files", "--unmerged", clean_env=not hook_env)
    if unmerged.returncode == 0 and unmerged.stdout.strip():
        return None, "the index has unresolved conflicts"
    why = (done.stderr.strip().splitlines() or ["it printed no tree"])[-1]
    return None, f"git write-tree failed ({why})"


def rule_lines(settings: dict[str, list[str]], top: str | os.PathLike) -> list[str]:
    """What `moxi repo rules` prints for this piece: the line for the index of the work
    tree at `top`, or one line saying why there is none."""
    salt = salt_of(settings)
    if not salt:
        return [f"no {KEY} line: {_no_salt(top)}"]
    tree, why = index_tree(top)
    if tree is None:
        fix = ("resolve them, git add the files, and run this again"
               if "conflicts" in why else "fix that and run this again")
        return [f"no {KEY} line: in {top}, {why}; {fix}"]
    return [proof_line(salt, tree)]


def _no_salt(top) -> str:
    where = quote_arg(top)
    return (f"{PIECE_ID} has no salt in this repo; remove the piece and add it again: "
            f"moxi repo git-hooks remove {where} {PIECE_ID}, then "
            f"moxi repo git-hooks add {where} {PIECE_ID}")


def read_lines(text: str, *, comment: str = "#", separators: str = ":"
               ) -> tuple[list[tuple[int, str]], list[int]]:
    """The message's `Commit-Check` lines: ([(line number, value)] for each one in a
    trailer paragraph after the subject's, [line numbers] of those that are not read: in
    the subject's paragraph or in one with a prose line). The key is matched ignoring
    case, as git matches a trailer's. Line numbers count the message as written."""
    read, unread = _read(text, comment, separators)
    return [(n, value) for n, value, _ in read], unread


def without_lines(text: str, *, value: str | None = None, comment: str = "#",
                  separators: str = ":") -> str:
    """`text` without the `Commit-Check` lines `read_lines` reads and the lines that
    continue their values: every one, or with `value`, those holding it (case ignored).
    Every other line stays as written."""
    read, _ = _read(text, comment, separators)
    gone = {n for n, held, lines in read
            if value is None or held.lower() == value.lower() for n in lines}
    if not gone:
        return text
    return "\n".join(line for n, line in enumerate(text.split("\n"), 1) if n not in gone)


def _read(text: str, comment: str, separators: str
          ) -> tuple[list[tuple[int, str, list[int]]], list[int]]:
    """`read_lines`, each line read with the line numbers it spans: its own and those of
    the lines that continue its value."""
    shape = _Shape(separators or ":", set())
    read: list[tuple[int, str, list[int]]] = []
    unread: list[int] = []
    for at, paragraph in enumerate(_paragraphs(_kept_lines(text, comment))):
        found: list[tuple[int, str, list[int]]] = []
        trailers = at > 0
        continues = False  # a trailer above this line, for a continuation to belong to
        spans: list[int] | None = None  # the lines of the Commit-Check line above, if any
        for n, line in paragraph:
            if line[:1] in (" ", "\t"):
                trailers = trailers and continues
                if spans is not None:
                    spans.append(n)
                continue
            trailer = shape.trailer(line)
            spans = None
            if trailer is None:
                trailers = continues = False
                continue
            continues = True
            if trailer[0].lower() == KEY.lower():
                spans = [n]
                found.append((n, trailer[1], spans))
        if trailers:
            read += found
        else:
            unread += [n for n, _, _ in found]
    return read, unread


def check_message(text: str, want: str, top: str, *, comment: str = "#",
                  separators: str = ":") -> list[str]:
    """Why the message is refused, as the lines to print, or [] when exactly one line
    holds `want`, the value for the tree being committed in the work tree at `top`."""
    rules = f"moxi repo rules {quote_arg(top)}"
    read, unread = read_lines(text, comment=comment, separators=separators)
    if not read:
        lines = [f"commit proof: the message has no {KEY} line; stage what you are "
                 f"committing, run: {rules}, and put the {KEY} line it prints in a "
                 "trailer paragraph (a paragraph of 'Key: value' lines after the subject)"]
        lines += [f"commit proof: the {KEY} line on line {n} is not read: it is in the "
                  "subject's paragraph or in one with a prose line; put a blank line "
                  "before it" for n in unread]
        return lines
    if len(read) > 1:
        where = ", ".join(str(n) for n, _ in read)
        return [f"commit proof: the message has {len(read)} {KEY} lines (lines {where}); "
                f"keep only the one this prints: {rules}"]
    n, value = read[0]
    if not _VALUE.fullmatch(value):
        return [f"commit proof: line {n} is not a {KEY} line as it is printed, "
                f"{PROOF_CHARS} hex characters; copy the line this prints: {rules}"]
    if value != want:
        return [f"commit proof: line {n} doesn't match the tree being committed in {top}: "
                "the index changed after the line was printed, or the line is another "
                f"repo's; stage first, run: {rules}, and commit with the line it prints"]
    return []


def replaying(git_dir: str | os.PathLike) -> bool:
    """True when a rebase, cherry-pick or revert is in progress in the work tree whose
    git dir is `git_dir`."""
    return any(os.path.lexists(os.path.join(git_dir, name)) for name in _REPLAY_STATE)


def merge_heads(git_dir: str | os.PathLike) -> list[str]:
    """The commits being merged in the work tree whose git dir is `git_dir`; empty when
    no merge is in progress."""
    try:
        with open(os.path.join(git_dir, "MERGE_HEAD"), encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.strip()]
    except OSError:
        return []


def clean_merge(ctx, tree: str) -> bool:
    """True when the commit is a merge of one other head and `tree` is what git makes of
    the two heads by itself, with no conflict: `git merge-tree --write-tree`, which git
    has from 2.38."""
    heads = merge_heads(ctx.git_dir)
    if len(heads) != 1:
        return False
    done = ctx.git("merge-tree", "--write-tree", "HEAD", heads[0])
    if done.returncode != 0:
        return False
    return (done.stdout.splitlines() or [""])[0].strip() == tree


def commit_msg(ctx) -> Outcome:
    """The piece: pass the commits that need no line, then refuse a message whose line is
    missing, doubled, malformed or not this tree's. A message it passes loses the lines
    it read, as the module's docstring says."""
    if ctx.committer:
        return Outcome.ok()
    comment, _, separators = git_settings(ctx)
    text = _message(ctx)
    if ctx.git_dir and replaying(ctx.git_dir):
        return _passed(ctx, text, comment, separators)
    top = ctx.root or os.getcwd()
    tree, why = index_tree(top, hook_env=True)
    if tree is None:
        return Outcome.refuse(f"commit proof: in {top}, {why}, so there is no tree to "
                              f"check the {KEY} line against")
    salt = salt_of(ctx.settings)
    if ctx.git_dir and clean_merge(ctx, tree):
        if not salt:
            # No line can hold this tree's value, so none is dropped.
            return Outcome.ok()
        return _passed(ctx, text, comment, separators, value=proof_value(salt, tree))
    if not salt:
        return Outcome.refuse(f"commit proof: {_no_salt(top)}")
    problems = check_message(text, proof_value(salt, tree), top, comment=comment,
                             separators=separators)
    if problems:
        return Outcome.refuse(*problems)
    return _passed(ctx, text, comment, separators)


def _message(ctx) -> str:
    with open(ctx.message_path, "rb") as fh:
        return fh.read().decode("utf-8", "surrogateescape")


def _passed(ctx, text: str, comment: str, separators: str, *,
            value: str | None = None) -> Outcome:
    """Pass the commit whose message is `text`, rewriting its message file without the
    lines `without_lines` drops for `value` when there are any."""
    from moxi.files import atomic_write

    kept = without_lines(text, value=value, comment=comment, separators=separators)
    if kept != text:
        atomic_write(ctx.message_path, kept.encode("utf-8", "surrogateescape"),
                     mode=os.stat(ctx.message_path).st_mode & 0o777)
    return Outcome.ok()


__all__ = ["COMMIT_PROOF", "PIECE_ID", "KEY", "check_message", "clean_merge", "commit_msg",
           "index_tree", "merge_heads", "new_salt", "proof_line", "proof_value",
           "read_lines", "replaying", "rule_lines", "salt_of", "salt_problem",
           "without_lines"]
