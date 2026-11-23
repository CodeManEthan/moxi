"""The failures of a red CI run, and which of a candidate's the target's own run has.

A failure is `{"job": <name>, "test": <id or None>}`: one for each test a failed job's
log names in pytest's `FAILED <id>` lines (`FAILED_LINE`), or the job alone when its log
names none (`of_run`). A job failed when it completed with a conclusion of
`FAILED_CONCLUSIONS`; a job cancelled beside a failing one is not counted.

`tests_of_log` reads those lines from the text `gh run view --log-failed` prints, each
line `<job>\\t<step>\\t<line>`, colour codes left out.

**The target's own** (`new` gives the rest). A candidate's failed test is the target's
own when the target's run has that test failing in any job, or has the job it failed in
failing with no test named; tests are compared by id across jobs, so a candidate's
scoped job is compared test by test with the target's full one. A job whose log names no
test is the target's own when the target's run has a job of that name failing.

`halves` splits a batch's members in two by join order, the first half the larger.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

#: A pytest line naming a failed test: `FAILED <path>::<test>`.
FAILED_LINE = re.compile(r"(?:^|\s)FAILED (\S+::\S+)")
#: The conclusions of a job that failed.
FAILED_CONCLUSIONS = ("failure", "timed_out", "startup_failure")
_COLOUR = re.compile(r"\x1b\[[0-9;]*m")


def tests_of_log(text: str) -> dict[str, list[str]]:
    """The tests each job's log names as failed, by job, in the order first named."""
    found: dict[str, list[str]] = {}
    for line in text.splitlines():
        parts = _COLOUR.sub("", line).split("\t", 2)
        if len(parts) < 3:
            continue
        for match in FAILED_LINE.finditer(parts[2]):
            tests = found.setdefault(parts[0], [])
            if match.group(1) not in tests:
                tests.append(match.group(1))
    return found


def failed_jobs(jobs: Iterable[Mapping], names: Sequence[str] | None = None) -> list[str]:
    """The names of the jobs that failed, of `names` only when given."""
    return [str(one.get("name")) for one in jobs
            if one.get("status") == "completed"
            and one.get("conclusion") in FAILED_CONCLUSIONS
            and (names is None or one.get("name") in names)]


def of_run(jobs: Sequence[str], tests: Mapping[str, Sequence[str]]) -> list[dict]:
    """A run's failures from its failed `jobs` and the `tests` their logs name."""
    found = []
    for job in jobs:
        named = tests.get(job) or ()
        found.extend({"job": job, "test": test} for test in named)
        if not named:
            found.append({"job": job, "test": None})
    return found


def is_own(failure: Mapping, target: Sequence[Mapping]) -> bool:
    """Whether the target's failures `target` have `failure`."""
    test = failure.get("test")
    if test is None:
        return any(one.get("job") == failure.get("job") for one in target)
    return any(one.get("test") == test
               or (one.get("test") is None and one.get("job") == failure.get("job"))
               for one in target)


def new(candidate: Sequence[Mapping], target: Sequence[Mapping]) -> list[dict]:
    """The candidate's failures that are not the target's own."""
    return [dict(one) for one in candidate if not is_own(one, target)]


def halves(members: Sequence) -> tuple[list, list]:
    """`members` split in two in their order, the first half the larger."""
    middle = (len(members) + 1) // 2
    return list(members[:middle]), list(members[middle:])


def said(failures: Sequence[Mapping]) -> str:
    """The failures in words: `job` or `job: test`, comma-separated."""
    return ", ".join(str(one.get("job")) if one.get("test") is None
                     else f"{one.get('job')}: {one.get('test')}" for one in failures)


__all__ = ["FAILED_LINE", "FAILED_CONCLUSIONS", "tests_of_log", "failed_jobs", "of_run",
           "is_own", "new", "halves", "said"]
