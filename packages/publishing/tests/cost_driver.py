"""Runs one piece, or the whole-tree scan, once in a fresh interpreter, as a git hook
would, and prints its time as JSON; `test_cost.py` reads it.

    python cost_driver.py import MODULE
    python cost_driver.py message MESSAGE_FILE
    python cost_driver.py content
    python cost_driver.py push REMOTE URL STDIN_LINE
    python cost_driver.py scan [--ci]

It runs in the repo it judges (its working directory). Before the piece's module it loads
only what repo's hook runner holds before it calls a piece, so the import time is the
piece's own, and the run's time takes in every first-use load (secrets' sets, patterns).
"""

from __future__ import annotations

import importlib
import json
import sys
import time

import moxi_repo.pieces  # noqa: F401 -- loaded by the hook runner before any piece
import moxi_repo.run  # noqa: F401 -- loaded by the hook runner before any piece


def _timed(call):
    start = time.perf_counter()
    result = call()
    return time.perf_counter() - start, result


def main(argv: list[str]) -> dict:
    from moxi_repo.pieces import HookContext

    what = argv[0]
    if what == "import":
        took, _ = _timed(lambda: importlib.import_module(argv[1]))
        return {"s": took, "secrets_loaded": any(name.startswith("moxi_secrets")
                                                 for name in sys.modules)}
    if what == "scan":
        from moxi_publishing import content

        took, found = _timed(lambda: content.scan_tree(".", ci="--ci" in argv))
        return {"s": took, "hits": len(found)}
    if what == "message":
        from moxi_publishing import message

        ctx = HookContext("commit-msg", args=(argv[1],),
                          settings={"drop-trailer": ["Claude-Session", "Commit-Check"]})
        took, outcome = _timed(lambda: message.commit_msg(ctx))
    elif what == "content":
        from moxi_publishing import content

        ctx = HookContext("pre-commit")
        took, outcome = _timed(lambda: content.pre_commit(ctx))
    else:
        from moxi_publishing import push

        ctx = HookContext("pre-push", args=(argv[1], argv[2]),
                          stdin=(argv[3] + "\n").encode("ascii"), settings={"main": ["main"]})
        took, outcome = _timed(lambda: push.pre_push(ctx))
    return {"s": took, "kind": outcome.kind, "lines": list(outcome.lines)}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:])))
