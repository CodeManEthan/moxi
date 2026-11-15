"""The content piece, `publishing/content`: what a commit adds to a public repo.

It runs on pre-commit, fail closed, order 50. This version declares the piece and refuses
every commit in a repo where it is registered and enabled: the scan it runs is not in
this version, and a public repo's guard fails closed rather than pass unread text.

Standard library only at import.
"""

from __future__ import annotations

#: The piece's declaration, read through the entry point `publishing.content`.
CONTENT = {
    "event": "pre-commit",
    "target": "moxi_publishing.content:pre_commit",
    "fail": "closed",
    "order": 50,
    "doc": "In a public repo, refuse a commit that adds a credential, a private name or "
           "a provenance pointer.",
}

PIECE_ID = "publishing/content"


def pre_commit(ctx):
    """The piece."""
    from moxi_repo import Outcome

    from moxi_publishing.message import is_public, no_flag

    if not is_public(ctx):
        return no_flag(ctx, PIECE_ID)
    return Outcome.refuse(f"{PIECE_ID} can't scan in this version of moxi-publishing, so "
                          "it refuses; upgrade it with: moxi install publishing")


__all__ = ["CONTENT", "PIECE_ID", "pre_commit"]
