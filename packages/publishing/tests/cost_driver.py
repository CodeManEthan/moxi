"""Runs one piece, or the whole-tree scan, once in a fresh interpreter, as a git hook
would, and prints its time as JSON; `test_cost.py` reads it.

    python cost_driver.py import MODULE
    python cost_driver.py message MESSAGE_FILE
    python cost_driver.py content
    python cost_driver.py push REMOTE URL STDIN_LINE
    python cost_driver.py scan [--ci]

It runs in the repo it judges (its working directory). A piece's module imports nothing
from repo, so its import time is its own. A piece reaches repo's api on first use, so the
run's timing starts before that api is loaded and takes in every first-use load (repo's
api, secrets' sets, patterns). It also takes in the hook runner's own two modules, which a
real hook has loaded already: a few milliseconds over, on the safe side.
"""

from __future__ import annotations

import importlib
import json
import sys
import time


def _timed(call):
    start = time.perf_counter()
    result = call()
    return time.perf_counter() - start, result


def _piece(piece, event: str, **context):
    """`piece` called with a hook context, repo's api loaded inside the call."""
    def call():
        from moxi_repo.api import HookContext

        return piece(HookContext(event, **context))
    return call


def main(argv: list[str]) -> dict:
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

        took, outcome = _timed(_piece(
            message.commit_msg, "commit-msg", args=(argv[1],),
            settings={"drop-trailer": ["Claude-Session", "Commit-Check"]}))
    elif what == "content":
        from moxi_publishing import content

        took, outcome = _timed(_piece(content.pre_commit, "pre-commit"))
    else:
        from moxi_publishing import push

        took, outcome = _timed(_piece(
            push.pre_push, "pre-push", args=(argv[1], argv[2]),
            stdin=(argv[3] + "\n").encode("ascii"), settings={"main": ["main"]}))
    return {"s": took, "kind": outcome.kind, "lines": list(outcome.lines)}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:])))
