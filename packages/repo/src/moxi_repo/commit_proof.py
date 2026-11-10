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
work tree to run it in.

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

    return secrets.token_hex(8)


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
    shape = _Shape(separators or ":", set())
    read: list[tuple[int, str]] = []
    unread: list[int] = []
    for at, paragraph in enumerate(_paragraphs(_kept_lines(text, comment))):
        found = []
        trailers = at > 0
        continues = False  # a trailer above this line, for a continuation to belong to
        for n, line in paragraph:
            if line[:1] in (" ", "\t"):
                trailers = trailers and continues
                continue
            trailer = shape.trailer(line)
            if trailer is None:
                trailers = continues = False
                continue
            continues = True
            if trailer[0].lower() == KEY.lower():
                found.append((n, trailer[1]))
        if trailers:
            read += found
        else:
            unread += [n for n, _ in found]
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
    missing, doubled, malformed or not this tree's."""
    if ctx.committer:
        return Outcome.ok()
    if ctx.git_dir and replaying(ctx.git_dir):
        return Outcome.ok()
    top = ctx.root or os.getcwd()
    tree, why = index_tree(top, hook_env=True)
    if tree is None:
        return Outcome.refuse(f"commit proof: in {top}, {why}, so there is no tree to "
                              f"check the {KEY} line against")
    if ctx.git_dir and clean_merge(ctx, tree):
        return Outcome.ok()
    salt = salt_of(ctx.settings)
    if not salt:
        return Outcome.refuse(f"commit proof: {_no_salt(top)}")
    with open(ctx.message_path, "rb") as fh:
        text = fh.read().decode("utf-8", "replace")
    comment, _, separators = git_settings(ctx)
    problems = check_message(text, proof_value(salt, tree), top, comment=comment,
                             separators=separators)
    if problems:
        return Outcome.refuse(*problems)
    return Outcome.ok()


__all__ = ["COMMIT_PROOF", "PIECE_ID", "KEY", "check_message", "clean_merge", "commit_msg",
           "index_tree", "merge_heads", "new_salt", "proof_line", "proof_value",
           "read_lines", "replaying", "rule_lines", "salt_of"]
