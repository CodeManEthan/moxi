"""Two whole rounds run through the `moxi` command, each act the one `next` named.

A `full` round goes from `open` to its close with two stages, the second after the first,
a question that holds only the second, a design sent back and then re-checked, a finding
fixed and re-checked, a decision ratified and a retrospective. A `fix` round declares its
one stage as it commissions it and closes on its finish. Before each act the script reads
`next` and finds the act's command there, with only its placeholders left to fill: the
round's folder, the worktree and the report's place come from what the verbs print, not
from the test's own knowledge. So the transcript of `next` is what an agent running the
round from `next` alone would have read. It is printed when the tests run with `-s`.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

import pytest
from moxi import cli
from moxi_rounds import log, numbers

CHECK = shlex.join([sys.executable, "-c", "print('the check passed')"])
LEAD = "planner"
BUILDER = "coder"
CHECKER = "critic"
WORKER = "helper"
FULL = "proj-a/scripted"
FIX = "proj-a/a-fix"
PLACEHOLDER = re.compile(r"[A-Z]+")
#: The verbs that write nothing, and so take no `--as`.
READS = ("next", "show", "list")


@pytest.fixture
def repo(git_repo, tmp_path, write_config):
    """proj-a, its root a git repo with one commit on `main`, its notes folder under the
    test's tmp, and a check that passes."""
    assert cli.main(["projects", "add", "proj-a", "--root", str(git_repo.root)]) == 0
    assert cli.main(["projects", "set", "proj-a", "--notes-root",
                     str(tmp_path / "notes")]) == 0
    # The merge commit finish makes is the user's own, so the repo names who they are.
    git_repo.git("config", "user.name", BUILDER)
    git_repo.git("config", "user.email", f"{BUILDER}@example.test")
    write_config(f"[projects.proj-a]\ncheck = {{ command = {json.dumps(CHECK)} }}\n")
    return git_repo


class Script:
    """Runs `moxi rounds` in this process and keeps what `next` printed.

    `read(where)` runs `next` and keeps its output as what was last said; `do(template,
    ...)` finds `template` in it, fills its placeholders and runs it. A command the
    script runs that `next` did not name goes through `run` alone."""

    def __init__(self, capsys, monkeypatch, home: Path, repo):
        self.repo = type(repo)
        self.capsys = capsys
        self.monkeypatch = monkeypatch
        self.home = home
        self.said = ""
        self.transcript: list[str] = []

    def run(self, argv: list[str], *, code: int = 0, cwd: Path | None = None) -> str:
        self.monkeypatch.chdir(self.home if cwd is None else cwd)
        self.capsys.readouterr()
        got = cli.main(["rounds", *argv])
        out, err = self.capsys.readouterr()
        assert got == code, f"moxi rounds {shlex.join(argv)} exited {got}:\n{out}{err}"
        assert not re.search(r"\bNone\b", self._plain(out)), out
        self.transcript.append(self._plain(f"$ moxi rounds {shlex.join(argv)}\n{out}"))
        return out

    def read(self, *argv: str, cwd: Path | None = None) -> str:
        self.said = self.run(["next", *argv], cwd=cwd)
        return self.said

    def names(self, *lines: str) -> None:
        """Each of `lines` is a whole line of what `next` last said."""
        said = self.said.splitlines()
        for line in lines:
            assert line in said, f"{line!r} not in:\n{self.said}"

    def do(self, template: str, *fill: str, by: str = LEAD, cwd: Path | None = None,
           code: int = 0) -> str:
        """Run the command `template` as `next` printed it, with `--as by`. Each
        placeholder of it (a word in capitals, `<your report>`, or a choice `a|b`) takes
        the next of `fill`, and an optional part from `[` on is left out. A verb that
        writes nothing takes no `--as`."""
        assert template in self.said, f"{template!r} not named in:\n{self.said}"
        words = shlex.split(template.replace("<your report>", "REPORT"))
        assert words[:2] == ["moxi", "rounds"], template
        if "[" in " ".join(words):
            words = words[:next(i for i, word in enumerate(words) if word.startswith("["))]
        values = iter(fill)
        argv = [next(values) if PLACEHOLDER.fullmatch(word) or "|" in word else word
                for word in words[2:]]
        assert next(values, None) is None, f"more values than placeholders: {template}"
        if argv[0] not in READS:
            argv += ["--as", by]
        return self.run(argv, cwd=cwd, code=code)

    def _plain(self, out: str) -> str:
        return out.replace(str(self.home), "<tmp>")

    def print(self) -> None:
        print("\n".join(self.transcript))


@pytest.fixture
def script(capsys, monkeypatch, tmp_path, repo) -> Script:
    return Script(capsys, monkeypatch, tmp_path, repo)


def after(prefix: str, text: str) -> Path:
    """The path printed after `prefix` at a line's start."""
    found = re.search(rf"^{re.escape(prefix)}(\S+)", text, re.MULTILINE)
    assert found, f"{prefix!r} not in:\n{text}"
    return Path(found.group(1))


def block(*lines: str) -> str:
    return "```toml rounds\n" + "\n".join(lines) + "\n```\n"


def report(round_dir: Path, name: str, text: str, *lines: str) -> str:
    path = round_dir / name
    path.write_text(f"# {name}\n\n{text}\n\n" + block(*lines), encoding="utf-8")
    return str(path)


def build_report(round_dir, name, stage, head, verdict, pass_, *tables) -> str:
    return report(round_dir, name, "A check of the build.",
                  'kind = "build"', f'verdict = "{verdict}"', f'pass = "{pass_}"',
                  f'by = "{CHECKER}"', 'model = "a-model"', f'stage = "{stage}"',
                  f'head = "{head}"', *tables)


def design_report(version: int, verdict: str, pass_: str, state: str) -> list[str]:
    return ['kind = "design"', f'verdict = "{verdict}"', f'pass = "{pass_}"',
            f'by = "{CHECKER}"', 'model = "a-model"', f"design = {version}", "",
            "[[finding]]", 'id = "L1"', 'severity = "major"', f'state = "{state}"',
            'text = "B2 does not say where the backup goes"']


def finding(state: str) -> list[str]:
    return ["", "[[finding]]", 'id = "L1"', 'severity = "major"', f'state = "{state}"',
            'text = "the reader drops the last line"']


DECISION = ['', '[[decision]]', 'id = "M1"', 'weight = "major"', 'forecloses = false',
            'text = "the reader reads the whole file at once; the design did not say"']

def design(more: str = "") -> str:
    return "# A reader and a writer\n\nB1 builds the reader and B2 the writer on top of " \
        f"it.{more}\n\n" + PLAN


PLAN = block('[[question]]', 'id = "Q1"', 'text = "Should the writer keep a backup?"',
            'blocks = ["B2"]', 'options = { a = "Yes", b = "No" }', '',
            '[[stage]]', 'id = "B1"', 'repo = "proj-a"', 'branch = "reader"',
            'after = []', '', '  [[stage.slice]]', '  id = "1a"',
            '  what = "the reader"', '  minutes = 30', '',
            '[[stage]]', 'id = "B2"', 'repo = "proj-a"', 'branch = "writer"',
            'after = ["B1"]', '', '  [[stage.slice]]', '  id = "2a"',
            '  what = "the writer"', '  minutes = 30')


def test_a_full_round_runs_from_open_to_its_close_on_what_next_names(script):
    s = script
    s.run(["open", "proj-a/scripted", "--description", "A reader and a writer",
           "--as", LEAD])

    # The design, sent back once and re-checked.
    s.read("proj-a/scripted")
    s.names(f"round {FULL}   flow full   step design   by lead, tier second")
    round_dir = after("write: design.md in ", s.said)
    (round_dir / "design.md").write_text(design(), encoding="utf-8")
    s.do("moxi rounds fold proj-a/scripted")

    s.read("proj-a/scripted")
    s.names("pass:  full of v1: no full pass over the design is recorded")
    reports = after("write: a report in ", s.said)
    assert "moxi rounds next --schema design" in s.said
    s.do("moxi rounds verdict proj-a/scripted <your report>",
         report(reports, "design-check-1.md", "The backup has no place.",
                *design_report(1, "fold-first", "full", "open")), by=CHECKER)

    s.read("proj-a/scripted")
    s.names(f"round {FULL}   flow full   step verify-design   by checker, "
            "tier third")
    (round_dir / "design.md").write_text(
        design(" The backup goes beside the file it keeps."),
        encoding="utf-8")
    s.do("moxi rounds fold proj-a/scripted")

    s.read("proj-a/scripted")
    s.names("pass:  re-check of v2: the design changed since v1, the last version a "
            "pass read")
    s.do("moxi rounds verdict proj-a/scripted <your report>",
         report(reports, "design-check-2.md", "The backup has its place.",
                *design_report(2, "publishable", "re-check", "fixed")), by=CHECKER)

    # Q1 is open and holds B2 alone, so B1 is commissioned while it waits.
    s.read("proj-a/scripted")
    s.names(f"round {FULL}   flow full   step docket   by operator",
            f"round {FULL}   flow full   stage B1   step commission",
            "waits: Q1 holds B2: Should the writer keep a backup?")
    made = s.do("moxi rounds commission proj-a/scripted B1 --builder NAME", BUILDER)
    reader = after("worktree ", made)

    # The builder works in the worktree with `next`, `check` and `finish` alone.
    tree = s.repo(reader)
    s.read(cwd=reader)
    s.names(f"round {FULL}   flow full   stage B1   step build   by builder, "
            "tier second")
    tree.commit("reader.txt", "the reader")
    s.read(cwd=reader)
    s.names(f"round {FULL}   flow full   stage B1   step check")
    s.do("moxi rounds check", by=BUILDER, cwd=reader)
    s.read(cwd=reader)
    s.names(f"round {FULL}   flow full   stage B1   step verify-build   by "
            "checker, tier second")
    sent_back = s.do("moxi rounds verdict proj-a/scripted <your report>",
                     build_report(reports, "b1-check-1.md", "B1", tree.rev(),
                                  "fold-first", "full", *finding("open"), *DECISION),
                     by=CHECKER)
    assert "finding B1.L1: major, open: the reader drops the last line" in sent_back

    # The fix, checked again and re-checked.
    s.read(cwd=reader)
    s.names(f"owes:  the newest report of B1, at {tree.rev()[:7]}, is fold-first: a "
            "re-check is owed")
    tree.commit("reader.txt", "the reader\nand its last line")
    s.read(cwd=reader)
    s.names(f"round {FULL}   flow full   stage B1   step check")
    s.do("moxi rounds check", by=BUILDER, cwd=reader)
    s.read(cwd=reader)
    s.names(f"round {FULL}   flow full   stage B1   step verify-build   by "
            "checker, tier third")
    s.do("moxi rounds verdict proj-a/scripted <your report>",
         build_report(reports, "b1-check-2.md", "B1", tree.rev(), "publishable",
                      "re-check", *finding("fixed"), *DECISION), by=CHECKER)
    s.read(cwd=reader)
    s.names(f"round {FULL}   flow full   stage B1   step finish   by builder")
    finished = s.do("moxi rounds finish", by=BUILDER, cwd=reader)
    assert "finished B1" in finished
    if sys.platform == "win32":
        # Windows removes no folder a process works in, and the finish runs from inside
        # the worktree: it stays for the sweep, and the finish says so.
        assert f"kept {reader}: removal: git did not remove it" in finished
    else:
        assert not reader.exists()

    # The operator rules Q1 from the waiting list, which also holds B1's decision.
    s.read("proj-a/scripted")
    s.names(f"round {FULL}   flow full   step docket   by operator")
    waiting = s.run(["list", "--waiting"])
    assert "proj-a/scripted Q1: Should the writer keep a backup?" in waiting
    assert "proj-a/scripted B1.M1: the reader reads the whole file at once" in waiting
    s.do("moxi rounds list proj-a/scripted --questions --view docket")
    s.do("moxi rounds rule proj-a/scripted Q1 --chose K --record PTR", "a",
         "comms:1234567")
    s.read("proj-a/scripted")
    s.names(f"round {FULL}   flow full   stage B2   step commission")
    s.do("moxi rounds mark proj-a/scripted C1 --shown --record PTR", "comms:1234568")

    s.read("proj-a/scripted")
    made = s.do("moxi rounds commission proj-a/scripted B2 --builder NAME", BUILDER)
    writer = after("worktree ", made)
    tree = s.repo(writer)
    tree.commit("writer.txt", "the writer")
    s.read(cwd=writer)
    s.names(f"round {FULL}   flow full   stage B2   step check")
    brief = reports / "brief-2a.md"
    brief.write_text("# B2, slice 2a\n\n" + block('stage = "B2"', 'slice = "2a"'),
                     encoding="utf-8")
    s.do("moxi rounds mark proj-a/scripted B2 --brief FILE", str(brief))
    s.do("moxi rounds check", by=BUILDER, cwd=writer)
    s.read(cwd=writer)
    s.names(f"round {FULL}   flow full   stage B2   step verify-build   by "
            "checker, tier second")
    s.do("moxi rounds verdict proj-a/scripted <your report>",
         build_report(reports, "b2-check-1.md", "B2", tree.rev(), "publishable",
                      "full"), by=CHECKER)
    s.read(cwd=writer)
    s.names(f"round {FULL}   flow full   stage B2   step finish   by builder")
    s.do("moxi rounds finish", by=BUILDER, cwd=writer)

    # Ratification, then the retrospective, which closes the round.
    s.read("proj-a/scripted")
    s.names(f"round {FULL}   flow full   step ratify   by operator")
    s.do("moxi rounds rule proj-a/scripted B1.M1=accept|reject --record PTR",
         "B1.M1=accept", "comms:1234569")
    s.read("proj-a/scripted")
    s.names(f"round {FULL}   flow full   step retro   by worker, tier third",
            "numbers:")
    s.do("moxi rounds mark proj-a/scripted C2 --shown --record PTR", "comms:1234570")
    closed = s.do("moxi rounds verdict proj-a/scripted <your report>",
                  report(reports, "retro-report.md", "The round went as planned.",
                         'kind = "retro"', f'by = "{WORKER}"', 'model = "a-model"'),
                  by=WORKER)
    assert closed.startswith(
        f"recorded for proj-a/scripted: the retrospective, by {WORKER}, model a-model")
    assert "closed proj-a/scripted: nothing is left for its close to wait for" in closed

    s.read("proj-a/scripted")
    s.names(f"round {FULL}   flow full   step none: the round is closed")
    assert "proj-a/scripted   closed at " in s.run(["list", "--closed"])

    # The round's own log, each event ten seconds after the one before: a build's
    # verification and finish get their time, and the docket none once Q1 is ruled,
    # though B2 was still to be built.
    events = [{**one, "at": log.stamp(1_000_000 + 10 * n)}
              for n, one in enumerate(log.read(log.round_log(round_dir)))]
    spent = numbers.step_time_s(events)
    assert spent["verify-build B1"] > 0 and spent["finish B1"] > 0
    assert spent["verify-build B2"] > 0 and spent["finish B2"] > 0
    ruled = next(n for n, one in enumerate(events)
                 if one["type"] == "ruling.recorded" and one.get("question") == "Q1")
    assert 0 < spent["docket"] == numbers.step_time_s(events[:ruled + 1])["docket"]
    assert spent["build B2"] > 0
    s.print()


def test_a_fix_round_runs_from_open_to_its_close_on_what_next_names(script):
    s = script
    s.run(["open", "proj-a/a-fix", "--description", "A typo", "--flow", "fix",
           "--as", LEAD])
    s.read("proj-a/a-fix")
    s.names(f"round {FIX}   flow fix   step commission")
    made = s.do("moxi rounds commission proj-a/a-fix --repo KEY --branch NAME "
                "--builder NAME", "proj-a", "fix-typo", BUILDER)
    fix = after("worktree ", made)

    tree = s.repo(fix)
    s.read(cwd=fix)
    s.names(f"round {FIX}   flow fix   stage B1   step build   by builder, "
            "tier second")
    tree.commit("README", "a placeholder repo, its typo fixed")
    s.read(cwd=fix)
    s.names(f"round {FIX}   flow fix   stage B1   step check")
    s.do("moxi rounds check", by=BUILDER, cwd=fix)
    s.read(cwd=fix)
    s.names(f"round {FIX}   flow fix   stage B1   step verify-build   by checker, "
            "tier third")
    reports = after("write: a report in ", s.said)
    s.do("moxi rounds verdict proj-a/a-fix <your report>",
         build_report(reports, "check.md", "B1", tree.rev(), "publishable", "full"),
         by=CHECKER)
    s.read(cwd=fix)
    s.names(f"round {FIX}   flow fix   stage B1   step finish   by builder")
    finished = s.do("moxi rounds finish", by=BUILDER, cwd=fix)
    assert "closed proj-a/a-fix: nothing is left for its close to wait for" in finished

    s.read("proj-a/a-fix")
    s.names(f"round {FIX}   flow fix   step none: the round is closed")
    s.print()
