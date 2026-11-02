"""`moxi-notes-snapshot [--settle]`: one committer pass over the notes repo.

Each pass builds notes' spec from config, saves it when the saved one differs, and runs
the saved spec through repo's committer, which takes its lock once and never waits. The
timer runs this without `--settle`, as `moxi notes snapshot` does: one pass, its line
printed, no duty taken. notes' turns listener runs it detached with `--settle`, which
takes on the commit and push duties the settle steps describe (`moxi_notes.settle`).

Repo's committer writes its own records. A failure before the pass (notes' config
unreadable, the root missing or no work tree's top) is the doctor record `snapshot`, and
the next pass that reaches the committer clears it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from contextlib import ExitStack

from . import settle as _settle
from . import settings as _settings
from . import spec as _spec

#: The doctor record for a failure before the pass.
RECORD = "snapshot"


def one_pass():
    """Run one pass: `(CommitResult, spec, ran)`. `spec` is None when it could not be
    built; `ran` is True when the committer ran, so the result is its own. Never
    raises."""
    from moxi import doctor, files
    from moxi.config import ConfigError
    from moxi_repo.api import CommitResult, SpecError, commit_saved

    try:
        spec = _spec.build()
    except ConfigError as exc:
        doctor.record(_settings.MODULE, f"a snapshot could not start: {exc}", key=RECORD,
                      fix="moxi config notes")
        return CommitResult("error", str(exc), spec=_spec.NAME), None, False
    try:
        _spec.ensure_saved(spec)
    except files.LockTimeout:
        return CommitResult("busy", "another pass holds the committer's lock",
                            spec=_spec.NAME), spec, False
    except (SpecError, OSError) as exc:
        doctor.record(_settings.MODULE, f"a snapshot of {spec.root} could not start: "
                                        f"{exc}", key=RECORD, fix="moxi notes setup")
        return CommitResult("error", str(exc), spec=_spec.NAME), spec, False
    result = commit_saved(spec.root, _spec.NAME)
    doctor.clear(_settings.MODULE, RECORD)
    return result, spec, True


class Real:
    """The settle steps' ops for a real process: the wall clock, real passes, the runtime
    files and the doctor."""

    def __init__(self, *, now=time.time, pass_fn=one_pass):
        from moxi import paths

        self.now = now
        self._pass = pass_fn
        self.spec = None
        self.dir = paths.Layout(_settings.MODULE).runtime_dir(create=True)
        self._lock = ExitStack()

    def run_pass(self):
        start = self.now()
        result, spec, ran = self._pass()
        end = self.now()
        if spec is not None:
            self.spec = spec
        unpushed = (result.status == "committed" and spec is not None
                    and spec.push is not None and not result.pushed)
        return _settle.Pass(status=result.status, start=start, end=end,
                            held_lock=ran and result.status != "busy",
                            unpushed=unpushed,
                            interval=float(spec.min_interval) if spec else 0.0,
                            backoff=float(spec.push_backoff) if spec else 300.0,
                            line=result.line())
        yield  # a generator, so the steps can `yield from` it

    def _requests(self):
        return self.dir / _settle.REQUESTS

    def read(self):
        try:
            return _parse(self._requests().read_bytes())
        except OSError:
            return (None, 0)

    def raise_request(self):
        from moxi import files

        seen = []

        def bump(old):
            gen, asked = _parse(old) if old is not None else (None, 0)
            if gen is None:
                gen, asked = uuid.uuid4().hex, 0
            seen.append((gen, asked + 1))
            return json.dumps({"gen": gen, "asked": asked + 1}).encode()

        files.update(self._requests(), bump)
        return seen[-1]

    def try_lock(self) -> bool:
        from moxi import files

        try:
            self._lock.enter_context(files.lock(self.dir / _settle.LOCK, timeout=0))
        except (files.LockTimeout, RuntimeError):
            return False
        return True

    def release(self) -> None:
        self._lock.close()

    def last_commit(self):
        if self.spec is None:
            return None
        from moxi_repo.api import last_commit_time

        try:
            return last_commit_time(self.spec.root, self.spec.author)
        except Exception:  # noqa: BLE001 - no answer means no wait
            return None

    def give_up(self, text: str) -> None:
        from moxi import doctor

        doctor.record(_settings.MODULE, text, key=_settle.RECORD, fix="moxi notes snapshot")

    def clear_record(self) -> None:
        from moxi import doctor

        doctor.clear(_settings.MODULE, _settle.RECORD)


def _parse(raw: bytes):
    try:
        data = json.loads(raw)
        gen, asked = data["gen"], data["asked"]
        if isinstance(gen, str) and gen and isinstance(asked, int) and asked >= 0:
            return gen, asked
    except (ValueError, KeyError, TypeError):
        pass
    return (None, 0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="moxi-notes-snapshot",
        description="Commit a snapshot of the notes repo: one pass of notes' spec through "
                    "repo's committer.")
    parser.add_argument("--settle", action="store_true",
                        help="also see an ended session's edits committed and pushed "
                             "(what notes' turns listener passes)")
    args = parser.parse_args(argv)
    if args.settle:
        _settle.run(_settle.settle(Real()))
        return 0
    result, _, _ = one_pass()
    print(result.line())
    return result.exit_code


__all__ = ["RECORD", "one_pass", "Real", "main"]


if __name__ == "__main__":
    sys.exit(main())
