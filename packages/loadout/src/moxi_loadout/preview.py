"""What a Replace of one row would do, for the page's confirm. Reads, writes nothing.

`replace_preview(text, section)` works out `moxi loadout install --replace <text>` for the
row `text` names in `section`, under the sections that row's act runs with (its
`applies`, `moxi_loadout.view`): on the plan `status` builds, and an entries run with
`text` claimed, as install claims it.

- `case`, by what the write does with what is there now: `moves` when the core placer
  moves it aside under `displaced/` and places loadout's (an occupant, one git tracks,
  loadout's own copy edited, a file saved over loadout's link); `keeps` when loadout keeps
  a copy of the file under `displaced/` and then writes it (a generated agent or global
  file that isn't loadout's, a harness file whose entry the Replace writes over); None
  when no row `text` names there is replaceable, or no run applies it.
- `diff`: a unified diff of what is there now (`-`) against what loadout writes (`+`),
  from the code the write uses. A generated file: its bytes against the bytes loadout
  made for it. A move: each file at or under the target against the file loadout places
  there (the folder's, or the generated file). A harness file: its bytes against what
  `entries.preview` makes of it with every action the run carries out there. An MCP server
  its harness's CLI writes: its entry as found against the entry as it will be sent
  (`label` `entry`); its file's bytes can't be known before the CLI writes. `label` is
  `file` everywhere else.
- Bounds: no link at or under a path is followed (`lstat`); at most `FILE_MAX_BYTES` of a
  file, `TREE_MAX_FILES` files of a folder and `TOTAL_MAX_BYTES` in all are read. A file
  past a bound, a link, or one that isn't UTF-8 text gives no lines and is named in
  `skipped`, `{path, why}`, `why` one of `WHYS`. The act keeps all of it whatever the diff
  leaves out.
- `tracked`: the occupant is one git tracks. `others`: how many other rows the run
  changes (rows in its scope reading `missing` or `stale`). `kept_in`: the folder the copy
  goes to; its own path is fixed when the write runs, and the act's outcome names it.
"""

from __future__ import annotations

import difflib
import json
import os
import stat
from pathlib import Path

from moxi_loadout import displaced, entries, install, planner
from moxi_loadout import view as view_mod
from moxi_loadout.manifest import USER

#: The most of one file the preview reads.
FILE_MAX_BYTES = 256 * 1024
#: The most it reads in all.
TOTAL_MAX_BYTES = 2 * 1024 * 1024
#: The most files of one folder it reads.
TREE_MAX_FILES = 200
#: Why a path gives no lines: a link, past a size bound, not UTF-8 text, past the files
#: of a folder.
WHYS = ("link", "large", "binary", "many")
#: The values of `case`, besides None.
CASES = ("moves", "keeps")


def replace_preview(text: str, section: str | None = None, *, loadout_dir=None,
                    resolver=planner.resolve_key, git=None,
                    git_seconds: float | None = None) -> dict:
    """`{case, target, kept_in, tracked, others, label, diff, lines, skipped}` for a
    Replace of what `text` (an id or a target path) names in `section` (any section when
    None). `git` and `git_seconds` as for `install.status`."""
    done = install.status_plan(loadout_dir=loadout_dir, resolver=resolver, git=git,
                               git_seconds=git_seconds)
    answer = {"case": None, "target": None, "kept_in": os.fspath(displaced.root()),
              "tracked": False, "others": 0, "label": "file", "diff": "", "lines": 0,
              "skipped": []}
    rows, sources = done.outcome.rows, done.sources
    named = [i for i, (row, src) in enumerate(zip(rows, sources))
             if (section is None or row.scope == section) and _names(text, src)]
    chosen = next((i for i in named if rows[i].replaceable), None)
    if chosen is None:
        return answer
    applies = view_mod.row_facts(done)[chosen]["applies"]
    if applies is None:
        return answer
    row, (source, src) = rows[chosen], sources[chosen]
    scope = planner.Scope() if applies == "whole" else (
        planner.Scope(user=True) if row.scope == USER else planner.Scope(projects=(row.scope,)))
    answer["others"] = sum(1 for i, r in enumerate(rows) if i not in named
                           and r.state in ("missing", "stale") and _in(scope, r))
    reader = _Reader()
    if source == "want":
        shown = _want(done.plan, src, reader)
    else:
        shown = _entries(done.plan, text, scope, reader)
    if shown is None:
        return answer
    case, target, diff, label = shown
    answer.update(case=case, target=os.fspath(target), label=label, diff=diff,
                  lines=len(diff.splitlines()), skipped=reader.skipped,
                  tracked=source == "want" and src.step is not None
                  and src.step.cause == "tracked")
    return answer


def _in(scope, row) -> bool:
    """Whether a run in `scope` applies `row`, as `status` filters its rows."""
    return scope.covers(row.scope, row.kind, row.name) if row.scope is not None \
        else scope.whole


def _names(text: str, src: tuple) -> bool:
    source, what = src
    if source == "want":
        return install.named_by(text, what)
    if source == "entry" and what is not None:
        return entries.named_by(text, what)
    return False


def _want(plan, want, reader: _Reader):
    """A placed or generated item's Replace: `(case, target, diff, label)`."""
    generated = want.generated
    if generated is not None and generated[2] == "modified":
        path, data = generated[0], generated[1]
        if data is None:
            data = _global_bytes(plan, want.harness)
        now = reader.file(path)
        then = reader.data(path, data)
        return "keeps", path, _diff({"": now}, {"": then}, path, path), "file"
    step = want.step
    if step is None or step.action != "refuse" or step.cause not in ("occupant", "tracked"):
        return None
    occupant = step.occupant or step.target
    if generated is not None and generated[1] is not None:
        source, theirs = generated[0], {"": reader.data(generated[0], generated[1])}
    else:
        source = want.item.source
        theirs = reader.tree(source)
    return "moves", step.target, _diff(reader.tree(occupant), theirs, occupant, source), \
        "file"


def _global_bytes(plan, harness: str) -> bytes | None:
    """What a Replace writes for `harness`'s global file once its edited generated file
    is gone: the shared file with the harness's part, or the shared file alone."""
    from moxi_loadout import globalfile

    shared = plan.manifest.global_file
    if shared is None:
        return None
    part = globalfile.parts(shared).get(harness)
    try:
        if part is None:
            return Path(shared).read_bytes()
        text = globalfile.assemble([globalfile.read(shared), globalfile.read(part)])
    except (OSError, UnicodeDecodeError):
        return None
    return globalfile.generated_bytes([shared, part], text)


def _entries(plan, text: str, scope, reader: _Reader):
    """A harness entry's Replace: `(case, target, diff, label)`, from an entries run
    with `text` claimed, as install plans it."""
    run = entries.plan(plan.manifest, os.fspath(plan.loadout_dir), plan.ledger,
                       claims={text: False})
    over = [a for a in run.actions if a.over and entries.covers(scope, a)
            and entries.named_by(text, a)]
    if not over:
        return None
    first = over[0].where
    get = (lambda k: getattr(first, k)) if isinstance(first, entries.Entry) else first.get
    target = get("file")
    if get("kind") == "mcp":
        diffs = []
        for a in over:
            found, sent = entries.server_entry(run, a)
            diffs.append(_diff({"": _json(found)}, {"": _json(sent)}, target, target))
        return "keeps", target, "".join(diffs), "entry"
    files = list(dict.fromkeys(a.entry.file if a.entry else a.record.get("file")
                               for a in over))
    shown = entries.preview(run, scope, files=set(files))
    diffs = []
    for file in files:
        if file not in shown:
            continue
        now, then = shown[file]
        diffs.append(_diff({"": reader.data(file, now)}, {"": reader.data(file, then)},
                           file, file))
    return "keeps", target, "".join(diffs), "file"


def _json(value) -> str:
    return "" if value is None else json.dumps(value, indent=2, sort_keys=True) + "\n"


def _diff(ours: dict, theirs: dict, ours_at, theirs_at) -> str:
    """The unified diff of two trees (`{relative path: text, or None when skipped}`),
    file by file; a file skipped on either side gives no lines."""
    out = []
    for rel in sorted(set(ours) | set(theirs)):
        a, b = ours.get(rel, ""), theirs.get(rel, "")
        if a is None or b is None or a == b:
            continue
        a_name = os.path.join(os.fspath(ours_at), rel) if rel else os.fspath(ours_at)
        b_name = os.path.join(os.fspath(theirs_at), rel) if rel else os.fspath(theirs_at)
        lines = difflib.unified_diff(a.splitlines(), b.splitlines(), a_name, b_name,
                                     lineterm="")
        out.extend(line + "\n" for line in lines)
    return "".join(out)


class _Reader:
    """Reads files for the diff within the bounds, naming what it leaves out in
    `skipped`."""

    def __init__(self) -> None:
        self.total = 0
        self.skipped: list[dict] = []

    def _skip(self, path, why: str) -> None:
        found = {"path": os.fspath(path), "why": why}
        if found not in self.skipped:
            self.skipped.append(found)

    def data(self, path, raw: bytes | None) -> str | None:
        """`raw`, bytes already in hand for `path`, as text; "" for None."""
        if raw is None:
            return ""
        if len(raw) > FILE_MAX_BYTES or self.total + len(raw) > TOTAL_MAX_BYTES:
            self._skip(path, "large")
            return None
        self.total += len(raw)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            self._skip(path, "binary")
            return None

    def file(self, path) -> str | None:
        """The file at `path` as text: "" when it is missing."""
        tree = self.tree(path)
        return tree.get("", "")

    def tree(self, path) -> dict[str, str | None]:
        """Each file at or under `path` by its path relative to it (`""` for `path`
        itself), as text, or None when it is left out."""
        out: dict[str, str | None] = {}
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return out
        except OSError:
            self._skip(path, "binary")
            out[""] = None
            return out
        if stat.S_ISLNK(st.st_mode) or _junction(path):
            self._skip(path, "link")
            out[""] = None
        elif stat.S_ISDIR(st.st_mode):
            self._walk(Path(path), Path(path), out, [0])
        else:
            out[""] = self._read(path, st)
        return out

    def _walk(self, top: Path, at: Path, out: dict, count: list) -> bool:
        """Read the files under `at` into `out`; False once past `TREE_MAX_FILES`."""
        try:
            found = sorted(os.scandir(at), key=lambda e: e.name)
        except OSError:
            self._skip(at, "binary")
            return True
        for entry in found:
            rel = os.path.relpath(entry.path, top)
            if entry.is_symlink() or _junction(entry.path):
                self._skip(entry.path, "link")
                out[rel] = None
            elif entry.is_dir(follow_symlinks=False):
                if not self._walk(top, Path(entry.path), out, count):
                    return False
            else:
                count[0] += 1
                if count[0] > TREE_MAX_FILES:
                    self._skip(top, "many")
                    return False
                out[rel] = self._read(entry.path, entry.stat(follow_symlinks=False))
        return True

    def _read(self, path, st) -> str | None:
        if not stat.S_ISREG(st.st_mode):
            self._skip(path, "binary")
            return None
        if st.st_size > FILE_MAX_BYTES or self.total + st.st_size > TOTAL_MAX_BYTES:
            self._skip(path, "large")
            return None
        try:
            with open(path, "rb") as fh:
                opened = os.fstat(fh.fileno())
                if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
                    # Swapped for something else since the lstat: a link, not followed.
                    self._skip(path, "link")
                    return None
                raw = fh.read(FILE_MAX_BYTES + 1)
        except OSError:
            self._skip(path, "binary")
            return None
        return self.data(path, raw)


def _junction(path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


__all__ = ["FILE_MAX_BYTES", "TOTAL_MAX_BYTES", "TREE_MAX_FILES", "WHYS", "CASES",
           "replace_preview"]
