"""The push piece, `publishing/push`: what a push makes public.

It runs on pre-push, fail closed, order 50. Its settings, in each repo's git config:

- `main`, one value: the repo's main branch;
- `protected`, any number: branches beside `main` a push may never delete;
- `word-required`, one value: `true` when a push needs `MOXI_PUBLISHING_PUSH_WORD`;
- `free-ref`, any number: ref patterns outside branches and release tags that a push may
  create, move or delete. A pattern never applies to a branch (`refs/heads/*`) or to a
  release tag, and `moxi publishing enable` refuses one that could match either.

This version declares the piece and refuses every push in a repo where it is registered
and enabled: the checks it runs are not in this version, and a public repo's guard fails
closed rather than let unread text out.

Standard library only at import.
"""

from __future__ import annotations

#: The piece's declaration, read through the entry point `publishing.push`.
PUSH = {
    "event": "pre-push",
    "target": "moxi_publishing.push:pre_push",
    "fail": "closed",
    "order": 50,
    "doc": "In a public repo, refuse a push that rewrites or deletes what it may not, or "
           "makes public what a commit would have refused.",
    "settings": {"main": "one", "protected": "multi", "word-required": "one",
                 "free-ref": "multi"},
}

PIECE_ID = "publishing/push"
MAIN = "main"
PROTECTED = "protected"
WORD_REQUIRED = "word-required"
FREE_REF = "free-ref"


def pre_push(ctx):
    """The piece."""
    from moxi_repo import Outcome

    from moxi_publishing.message import is_public, no_flag

    if not is_public(ctx):
        return no_flag(ctx, PIECE_ID)
    return Outcome.refuse(f"{PIECE_ID} can't check a push in this version of "
                          "moxi-publishing, so it refuses; upgrade it with: "
                          "moxi install publishing")


__all__ = ["PUSH", "PIECE_ID", "MAIN", "PROTECTED", "WORD_REQUIRED", "FREE_REF",
           "pre_push"]
