"""notes' two git hook pieces, as repo reads them from the `moxi.repo.git_hooks` group.

Both are closed: a gate that breaks refuses the commit or the push. Setup registers them
in the notes repo; the snapshot's own commits and pushes run them too.

Imports nothing: the git hook runner loads this table to learn what to call.
"""

SECRET_GATE = {
    "event": "pre-commit",
    "target": "moxi_notes.gates:pre_commit",
    "fail": "closed",
    "order": 20,
    "doc": "Leave out or refuse a staged file that holds a secret.",
}

PUSH_GATE = {
    "event": "pre-push",
    "target": "moxi_notes.gates:pre_push",
    "fail": "closed",
    "order": 20,
    "doc": "Refuse a push that would carry a secret.",
}
