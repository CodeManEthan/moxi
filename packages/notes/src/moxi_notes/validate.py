"""`moxi notes validate`: a read-only walk of the notes root, and a report.

Everything it finds is information, never an error: missing frontmatter is a fact about
notes older than the convention. The one thing that stops the walk is a types file that
can't be read, because with no vocabulary there is no "unknown type", and the report would
call every note unknown. A notes root that is not a folder stops it too.

The walk takes every `*.md` under the root with `os.walk`, pruning while it walks:
vendored folders (`VENDORED`, matched as whole folder names) and `.git` are dropped before
it descends, and no symlinked folder or file is followed. Each pruned tree is counted under
its marker, and every marker is printed, at zero too, so no exclusion is silent.

Each file gets one class:

- `filed`: one type's homes win it (the highest priority among the types that match; a tie
  is the finding `ambiguous-home`). Its header is read and checked.
- `frozen`: a frozen type matches it. Counted, never opened.
- `exempt`: an exempt type wins it. Counted under its type, never opened.
- `unfiled`: no type matches it. Counted by top folder, never opened, except when there
  is no types file: then every file is unfiled, and each is read so the frontmatter
  findings still report.

The findings: `missing-frontmatter`, `unreadable-frontmatter`, `missing-required`,
`unknown-type`, `type-disagrees-with-home`, `unknown-keys`, `unknown-status` and
`ambiguous-home`. A key is unknown when nothing declared it: not required, not `type`, and
not a key the winning type permits (`status`, when it lists values).
"""

from __future__ import annotations

import os
import stat
from collections import Counter
from dataclasses import dataclass, field

from . import doctypes as _doctypes
from . import frontmatter as _fm

MISSING_FRONTMATTER = "missing-frontmatter"
UNREADABLE_FRONTMATTER = "unreadable-frontmatter"
MISSING_REQUIRED = "missing-required"
UNKNOWN_TYPE = "unknown-type"
TYPE_DISAGREES = "type-disagrees-with-home"
UNKNOWN_KEYS = "unknown-keys"
UNKNOWN_STATUS = "unknown-status"
AMBIGUOUS_HOME = "ambiguous-home"
CODES = (MISSING_FRONTMATTER, UNREADABLE_FRONTMATTER, MISSING_REQUIRED, UNKNOWN_TYPE,
         TYPE_DISAGREES, UNKNOWN_KEYS, UNKNOWN_STATUS, AMBIGUOUS_HOME)

CLASSES = ("filed", "frozen", "exempt", "unfiled")

#: Folder names that hold somebody else's files, matched as a whole folder name.
VENDORED = ("node_modules", ".venv", "venv", "site-packages", "vendor", "bower_components")
GIT = ".git"
#: The marker a symlinked folder or `.md` file is counted under.
SYMLINK = "symlink"
#: The marker a folder the walk could not list is counted under.
UNLISTED = "unlisted"
MARKERS = VENDORED + (GIT, SYMLINK, UNLISTED)


@dataclass
class Note:
    """One `*.md` file. `path` is absolute; `relative` uses `/` under the root."""

    path: str
    relative: str
    klass: str
    type: str | None = None
    matched: tuple[str, ...] = ()
    header: _fm.Frontmatter | None = None
    findings: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"path": self.path, "class": self.klass, "type": self.type,
                "matched": list(self.matched),
                "frontmatter": self.header.as_dict() if self.header else None,
                "findings": [{"code": c, "detail": d} for c, d in self.findings]}


@dataclass
class Report:
    """What the walk found. `ok` False means nothing was walked, and `reason` says why."""

    ok: bool
    root: str
    types: _doctypes.Types | None
    reason: str | None = None
    notes: list[Note] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=lambda: dict.fromkeys(MARKERS, 0))

    def of(self, klass: str) -> list[Note]:
        return [n for n in self.notes if n.klass == klass]

    @property
    def filed(self) -> list[Note]:
        return self.of("filed")

    def counts(self) -> dict:
        filed = self.filed
        with_fm = sum(1 for n in filed if n.header is not None and n.header.present)
        out = {"files": len(self.notes)}
        out.update({klass: len(self.of(klass)) for klass in CLASSES})
        out.update({"filed_with_frontmatter": with_fm,
                    "filed_without_frontmatter": len(filed) - with_fm,
                    "findings": sum(len(n.findings) for n in self.notes)})
        return out

    def as_dict(self) -> dict:
        return {"ok": self.ok, "root": self.root, "reason": self.reason,
                "types": self.types.as_dict() if self.types else None,
                "counts": self.counts() if self.ok else None,
                "excluded": dict(self.excluded),
                "files": [n.as_dict() for n in self.notes]}


def validate(*, root=None, types: _doctypes.Types | None = None) -> Report:
    """Walk the notes root (default: the `root` setting) and report. Writes nothing.
    A types file that can't be read gives `ok=False`; notes' config that can't be read
    raises `moxi.config.ConfigError`."""
    if root is None:
        from . import settings

        root = settings.read().root
    root = os.path.normpath(os.fspath(root))
    if types is None:
        try:
            types = _doctypes.load()
        except _doctypes.TypesError as exc:
            return Report(ok=False, root=root, types=None,
                          reason=f"the types file can't be used: {exc.reason}")
    report = Report(ok=True, root=root, types=types)
    if not os.path.isdir(root):
        report.ok = False
        report.reason = f"the notes root {root} is not a folder"
        return report
    for path, relative in walk(root, report.excluded):
        report.notes.append(_read(path, relative, types))
    return report


def walk(root: str, excluded: dict[str, int]):
    """`(path, relative)` for every regular `*.md` file under `root`, sorted, pruning
    while walking. Each tree left out adds one to its marker in `excluded`."""
    def unlisted(_error):
        excluded[UNLISTED] += 1

    for folder, dirs, files in os.walk(root, onerror=unlisted, followlinks=False):
        keep = []
        for name in sorted(dirs):
            if name in VENDORED or name == GIT:
                excluded[name] += 1
            elif os.path.islink(os.path.join(folder, name)):
                excluded[SYMLINK] += 1
            else:
                keep.append(name)
        dirs[:] = keep
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(folder, name)
            try:
                mode = os.lstat(path).st_mode
            except OSError:
                continue
            if stat.S_ISLNK(mode):
                excluded[SYMLINK] += 1
            elif stat.S_ISREG(mode):
                yield path, os.path.relpath(path, root).replace(os.sep, "/")


def _read(path: str, relative: str, types: _doctypes.Types) -> Note:
    hits = types.match(relative)
    frozen = [h for h in hits if h.frozen]
    if frozen:
        # A frozen corpus stays frozen whatever else claims it.
        return Note(path, relative, "frozen", frozen[0].name, tuple(h.name for h in hits))
    top = _doctypes.winners(hits)
    names = tuple(h.name for h in top)
    exempt = [h for h in top if h.exempt]
    if exempt:
        return Note(path, relative, "exempt", exempt[0].name, names)
    if not hits:
        note = Note(path, relative, "unfiled")
        if not types.present:
            _check(note, types, [])
        return note
    note = Note(path, relative, "filed", names[0], names)
    if len(top) > 1:
        note.findings.append((AMBIGUOUS_HOME,
                              f"{len(top)} types claim it at the same priority "
                              f"({', '.join(names)}); give one a higher priority, or "
                              f"narrow a home"))
    _check(note, types, top)
    return note


def _check(note: Note, types: _doctypes.Types, top: list[_doctypes.DocType]) -> None:
    try:
        with open(note.path, "rb") as handle:
            text = handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        note.findings.append((UNREADABLE_FRONTMATTER,
                              f"the file can't be read: {exc.strerror or exc}"))
        return
    header = note.header = _fm.parse(text)
    if not header.present:
        said = f"; its home says it is a {note.type}" if note.type else ""
        note.findings.append((MISSING_FRONTMATTER, f"no frontmatter{said}"))
        return
    if not header.ok:
        note.findings.append((UNREADABLE_FRONTMATTER, header.reason or "can't be read"))
        return
    missing = header.missing(types.required)
    if missing:
        note.findings.append((MISSING_REQUIRED, ", ".join(missing)))
    declared = header.get(_doctypes.TYPE_KEY)
    if types.present and declared not in (None, ""):
        if not isinstance(declared, str) or declared not in types.names:
            note.findings.append((UNKNOWN_TYPE,
                                  f"{declared!r} is not a type in "
                                  f"{os.path.basename(types.path)}; declare it there, "
                                  f"or fix the key"))
        elif top and declared not in note.matched:
            note.findings.append((TYPE_DISAGREES,
                                  f"it says {declared}; its home says {note.type}"))
    known = set(types.required) | {_doctypes.TYPE_KEY}
    known |= {key for row in top for key in row.permitted()}
    unknown = header.unknown(known)
    if unknown:
        note.findings.append((UNKNOWN_KEYS, ", ".join(unknown)))
    value = header.get(_doctypes.STATUS_KEY)
    allowed = {v for row in top for v in row.status}
    if allowed and value not in (None, "") and str(value) not in allowed:
        note.findings.append((UNKNOWN_STATUS,
                              f"{value!r} is not a status its type allows "
                              f"({', '.join(sorted(allowed))})"))


# --- the text report ------------------------------------------------------------------

def render(report: Report, *, unfiled: bool = False, limit: int = 60) -> str:
    """The report as text: counts first, then the files that have findings."""
    out = [f"notes validate: a read-only walk of {report.root}"]
    if not report.ok:
        out += [f"can't validate: {report.reason}", "Nothing was walked."]
        return "\n".join(out) + "\n"
    types, counts = report.types, report.counts()
    if types.present:
        out.append(f"types: {len(types.types)} from {types.path}")
    else:
        out.append(f"types: none, since {types.path} doesn't exist; every note is "
                   f"unfiled, and its frontmatter is still checked")
    out += ["", f"{counts['files']} markdown file(s): " + ", ".join(
        f"{klass} {counts[klass]}" for klass in CLASSES),
        f"of the {counts['filed']} filed: {counts['filed_with_frontmatter']} have "
        f"frontmatter, {counts['filed_without_frontmatter']} don't",
        "left out of the walk: " + ", ".join(
            f"{marker} {report.excluded[marker]}" for marker in MARKERS), ""]

    with_findings = [n for n in report.notes if n.findings]
    out.append(f"## Findings: {sum(len(n.findings) for n in with_findings)} in "
               f"{len(with_findings)} file(s)")
    for note in with_findings[:max(limit, 0)]:
        out.append(f"- {note.relative}" + (f" ({note.type})" if note.type else ""))
        out += [f"    {code}: {detail}" for code, detail in note.findings]
    hidden = len(with_findings) - max(limit, 0)
    if hidden > 0:
        out.append(f"...and {hidden} more file(s) with findings (raise --limit, or use "
                   f"--json, which lists every one)")
    out.append("")

    for klass, title in (("frozen", "Frozen, never opened"),
                         ("exempt", "Exempt, never opened")):
        by_type = Counter(n.type for n in report.of(klass))
        if by_type:
            out.append(f"## {title}")
            out += [f"- {name}: {total} file(s)" for name, total in sorted(by_type.items())]
            out.append("")
    notes = report.of("unfiled")
    if notes:
        out.append("## Unfiled: no type's home matches them" +
                   ("; never opened" if types.present else ""))
        tops = Counter(n.relative.split("/")[0] + "/" if "/" in n.relative else "(root)"
                       for n in notes)
        out += [f"- {top}: {total} file(s)"
                for top, total in sorted(tops.items(), key=lambda kv: (-kv[1], kv[0]))]
        if unfiled:
            out += [f"    {n.relative}" for n in notes]
        out.append("")
    out.append("This is information, not errors: validate wrote nothing and exits 0.")
    return "\n".join(out) + "\n"


__all__ = ["CODES", "CLASSES", "VENDORED", "MARKERS", "Note", "Report", "validate", "walk",
           "render"]
