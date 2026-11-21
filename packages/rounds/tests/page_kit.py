"""The rounds the page's tests read, seeded through rounds' own write path, and what the
browser tests share: seeding under a test shell's home, and a short wait.

In the project `kestrel`:

- `billing-export` on the `staged` flow (a step of the user's own): stages B1 to B6, so
  its other stages fold; a question with options and a huge one, a ruling not yet shown,
  B1 commissioned with its check failed, CI red, a verdict sent back with an open finding,
  a major decision that forecloses and a job for him; B2 commissioned and dropped.
- `ledger`, fresh: no stage in its plan.
- `archive`, closed.
- `broken`, whose log can't be read.
- a question for the project itself.

A test shell keeps its files under a home of its own, and its page servers read rounds
from there; `under(shell)` points this process at that home while a test seeds it, so
nothing is written outside it.
"""

from __future__ import annotations

import contextlib
import os
import re
import textwrap
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from moxi_rounds import asking, freezing, log, opening, paths, ruling, verdicts

BUSY = "kestrel/billing-export"
FRESH = "kestrel/ledger"
SHUT = "kestrel/archive"
BROKEN = "kestrel/broken"
PTR = "comms:1234567"
BASE, TIP = "0" * 40, "1" * 40

FLOWS = """\
[flows.staged]
steps = [ { step = "design", by = "lead" },
          { step = "verify-design", by = "checker" },
          { step = "build", by = "builder" },
          { step = "staging", does = "command", before = "finish", by = "builder", argv = ["true"] },
          { step = "verify-build", by = "checker" },
          { step = "finish", by = "builder" },
          { step = "retro", by = "worker" } ]
"""


def append(round_dir: Path, event_type: str, **fields: Any) -> None:
    log.append(log.round_log(round_dir), event_type, fields, by="lead")


def design(round_dir: Path, name: str, entries: str) -> None:
    (round_dir / "design.md").write_text(
        f"# A design\n\nQ1 is asked here: which name does the export keep?\n\n"
        f"```toml rounds\n{entries}\n```\n", encoding="utf-8")
    freezing.freeze(name, by="lead")
    report = round_dir / "report-1.md"
    report.write_text('# Report\n\n```toml rounds\nverdict = "publishable"\npass = "full"\n'
                      'by = "checker"\nmodel = "m"\nkind = "design"\ndesign = 1\n```\n',
                      encoding="utf-8")
    verdicts.record(name, report, by="checker")


def stage(id_: str) -> str:
    return (f'[[stage]]\nid = "{id_}"\nrepo = "kestrel"\nbranch = "branch-{id_.lower()}"\n'
            "after = []\n")


def add_project(key: str, notes_root: Path) -> None:
    """Add the project `key` to the projects list, its root a new folder under the home,
    its notes under `notes_root`."""
    from moxi import cli

    root = Path.home() / "roots" / key
    root.mkdir(parents=True, exist_ok=True)
    notes_root.mkdir(parents=True, exist_ok=True)
    assert cli.main(["projects", "add", key, "--root", str(root)]) == 0
    assert cli.main(["projects", "set", key, "--notes-root", str(notes_root)]) == 0


def write_config(text: str) -> Path:
    """Write `text`, dedented, as rounds' config file."""
    config_file = paths.LAYOUT.config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(textwrap.dedent(text), encoding="utf-8")
    return config_file


def seed(notes_root: Path) -> Path:
    """Write the rounds above under `notes_root`, the project's config and its rounds'
    config included; returns the rounds folder."""
    add_project("kestrel", notes_root)
    write_config(FLOWS)
    rounds_dir = notes_root / "rounds"
    opening.open_round(BUSY, description="billing export", by="planner", flow="staged")
    for name in (FRESH, SHUT, BROKEN):
        opening.open_round(name, description="a placeholder round", by="planner")
    busy = rounds_dir / "billing-export"
    design(busy, BUSY, "\n".join(stage(f"B{n}") for n in range(1, 7)))
    asking.ask(BUSY, "Which name does the export keep?", by="scout",
               origin="file:design.md#which-name",
               options=("old=the old name", "new=the new name"))
    asking.ask(BUSY, "Why? " * 900, by="scout", origin=PTR)
    asking.ask("kestrel", "Which repo keeps the ledgers?", by="scout", origin=PTR)
    ruling.rule(BUSY, text="Exports keep a header row.", record=PTR, by="lead",
                at="2026-01-02T00:00:00Z")
    for one in ("B1", "B2"):
        append(busy, "build.commissioned", stage=one, repo="kestrel",
               branch=f"branch-{one.lower()}", base=BASE, target="main",
               builder="builder-1", build="0123456789abcdef", worktree="/w/" + one)
    append(busy, "build.dropped", stage="B2")
    append(busy, "check.ran", stage="B1", step="check", commit=TIP, argv=["false"],
           exit_code=1, duration_s=2.5, output="/scratch/check-1.log")
    append(busy, "ci.read", stage="B1", commit=TIP, reading="red", workflows=[
        {"path": ".github/workflows/ci.yml", "reading": "red", "url": "https://ci/1",
         "jobs": [{"name": "linux", "reading": "red", "url": "https://ci/1/1"},
                  {"name": "macos", "reading": "green", "url": "https://ci/1/2"}]}])
    append(busy, "verdict.recorded", kind="build", verdict="fold-first", stage="B1",
           head=TIP, by="checker", model="m", report="report-2.md",
           findings=[{"id": "F1", "severity": "major", "state": "open",
                     "text": "The header is lost."}],
           decisions=[{"id": "M1", "text": "Exports drop the old format.",
                       "weight": "major", "forecloses": True}],
           waits=[{"id": "W1", "text": "Say whether the old format may go."}])
    append(rounds_dir / "archive", "round.closed")
    with log.round_log(rounds_dir / "broken").open("ab") as fh:
        fh.write(b"not an event\n")
    return rounds_dir


#: A Moxi locator variable the test kit sets for this process; the shell sets its own.
_LOCATOR = re.compile(r"MOXI_(?:HOME|[A-Z0-9_]+_(?:HOME|RUNTIME_DIR))")


@contextlib.contextmanager
def under(shell: Any) -> Iterator[None]:
    """Point this process at `shell`'s home while the block runs: its `HOME`, XDG homes
    and Moxi home, and no locator variable of the test kit's own. Restores them after."""
    kept = dict(os.environ)
    try:
        for key in [key for key in os.environ if _LOCATOR.fullmatch(key)
                    and key != "MOXI_ANCHOR_HOME"]:
            del os.environ[key]
        for key, value in shell.env.items():
            if key == "HOME" or key.startswith("XDG_") or _LOCATOR.fullmatch(key):
                os.environ[key] = value
        yield
    finally:
        os.environ.clear()
        os.environ.update(kept)


def shell_home(shell: Any) -> Path:
    return Path(shell.env["HOME"])


def wait_for(check: Callable[[], Any], timeout: float, *, what: str,
             interval: float = 0.05) -> Any:
    """Call `check` until it gives a true value, and return that; fail after `timeout`
    seconds saying `what` did not happen."""
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"{what} did not happen in {timeout:g}s")
        time.sleep(interval)
