"""How sessions tests archive folders, held by a test for the length of a block.

Another module's test reaches these through `moxi_sessions.api.testing`, as it reaches
every name sessions offers, and swaps nothing inside sessions itself.

* `archive_tests(...)`: within the block, sessions tests each archive folder as asked:
  never answering, keeping each answer for `keep_s`, telling `on_test` each folder whose
  test starts, or taking an answer from `stand_in`. Everything is put back at the end.
* `forget_archive_answers()`: forget every answer this process kept, so the next call
  tests each folder again.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

#: The program a folder that never answers runs: it sleeps past any wait.
NEVER_ANSWERS = "import time; time.sleep(60)"


def forget_archive_answers() -> None:
    """Forget every archive folder's answer this process kept."""
    from .. import archives

    archives.forget()


@contextmanager
def archive_tests(*, never_answer: bool = False, keep_s: float | None = None,
                  on_test: Callable[[str], Any] | None = None,
                  stand_in: Callable[..., Any] | None = None) -> Iterator[None]:
    """Hold how sessions tests archive folders until the block ends.

    * `never_answer`: each folder's test sleeps past any wait, so every folder tested is
      offline with "no answer within ...".
    * `keep_s`: how long this process keeps a folder's answer (0 keeps none).
    * `on_test(archive_dir)`: called for each folder as its test starts.
    * `stand_in(archive_dir, fresh=...)`: asked first for each folder a call wants an
      answer for, with the call's `fresh`; an `ArchiveAnswer` it returns is that folder's
      answer, untested and not kept, and None lets the folder be tested. It may change
      the folder before returning None, as an unmount would. It reaches the API's
      `archive_answers` and every listing and find sessions makes; sessions' own setup,
      doctor and CLI keep the answer test as it is.

    Kept answers are forgotten when the block starts and when it ends.
    """
    from .. import api, archives

    saved = {name: getattr(archives, name)
             for name in ("_argv", "_spawn", "KEEP_S", "archive_answers")}
    api_had = vars(api).get("archive_answers")
    try:
        if never_answer:
            archives._argv = lambda archive_dir: [
                sys.executable, "-I", "-S", "-c", NEVER_ANSWERS, archive_dir]
        if keep_s is not None:
            archives.KEEP_S = float(keep_s)
        if on_test is not None:
            spawn = saved["_spawn"]

            def spawn_told(archive_dir: str):
                on_test(archive_dir)
                return spawn(archive_dir)

            archives._spawn = spawn_told
        if stand_in is not None:
            tested = saved["archive_answers"]

            def answers(paths, *, fresh: bool = False, wait_s: float | None = None):
                wanted = list(dict.fromkeys(str(p) for p in paths))
                given = {}
                for archive_dir in wanted:
                    answer = stand_in(archive_dir, fresh=fresh)
                    if answer is not None:
                        given[archive_dir] = answer
                rest = [archive_dir for archive_dir in wanted if archive_dir not in given]
                got = tested(rest, fresh=fresh, wait_s=wait_s) if rest else {}
                return {archive_dir: given.get(archive_dir) or got[archive_dir]
                        for archive_dir in wanted}

            archives.archive_answers = answers
            api.archive_answers = answers
        archives.forget()
        yield
    finally:
        for name, value in saved.items():
            setattr(archives, name, value)
        if api_had is None:
            vars(api).pop("archive_answers", None)
        else:
            api.archive_answers = api_had
        archives.forget()


__all__ = ["NEVER_ANSWERS", "archive_tests", "forget_archive_answers"]
