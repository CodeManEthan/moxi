"""`moxi notes backfill`: add the required frontmatter keys a filed note lacks and that
can be inferred. A dry run unless `--apply`.

For each filed note (frozen, exempt and unfiled notes are never proposed for) it proposes
only keys that are required and missing, and only these three:

- `type`, from the type whose home wins the file;
- `project`, with the projects module installed, the key of the project whose notes folder
  holds the file (its `notes_root`, or the folder notes gives its root; the longest folder
  wins, and two projects on that folder skip it);
- `updated`, the file's modified date.

Any other required key, `description` and `tags` among them, is never inferred: a person
writes those words. A key that is there is never replaced, whatever it says. A note whose
header can't be read, or whose home is a tie, gets no proposal, with the reason.

`--apply` writes only regular files under the notes root and never through a symlink:
each file is checked with `lstat` first, read through a descriptor opened with
`O_NOFOLLOW` where the OS has it, and replaced in one step (a rename over the path, which
follows no symlink), keeping its mode. A read-only file is left alone. Its writes are
ordinary edits, so the next snapshot commits them. A second run adds
nothing.
"""

from __future__ import annotations

import datetime
import os
import stat
from dataclasses import dataclass, field

from . import doctypes as _doctypes
from . import frontmatter as _fm
from . import validate as _validate

#: The keys a backfill may write. Every other required key is skipped.
INFERABLE = ("type", "project", "updated")
NEVER = "never inferred; a person writes it"
#: Open flags where the OS has them, else no flag.
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_BINARY = getattr(os, "O_BINARY", 0)


@dataclass
class Proposal:
    path: str
    relative: str
    type: str | None
    additions: dict[str, str] = field(default_factory=dict)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    refused: str | None = None
    applied: bool = False
    error: str | None = None

    @property
    def actionable(self) -> bool:
        return self.refused is None and bool(self.additions)

    def as_dict(self) -> dict:
        return {"path": self.path, "type": self.type, "additions": dict(self.additions),
                "skipped": [{"key": k, "reason": r} for k, r in self.skipped],
                "refused": self.refused, "applied": self.applied, "error": self.error}


@dataclass
class Plan:
    ok: bool
    root: str
    apply: bool = False
    reason: str | None = None
    projects: str = ""
    required: tuple[str, ...] = _fm.DEFAULT_REQUIRED
    proposals: list[Proposal] = field(default_factory=list)

    def counts(self) -> dict:
        p = self.proposals
        return {"considered": len(p),
                "with_additions": sum(1 for x in p if x.actionable),
                "complete": sum(1 for x in p if not x.additions and not x.refused),
                "refused": sum(1 for x in p if x.refused),
                "written": sum(1 for x in p if x.applied),
                "errors": sum(1 for x in p if x.error),
                "keys": sum(len(x.additions) for x in p if not x.refused)}

    def as_dict(self) -> dict:
        return {"ok": self.ok, "root": self.root, "apply": self.apply,
                "reason": self.reason, "projects": self.projects,
                "counts": self.counts() if self.ok else None,
                "proposals": [p.as_dict() for p in self.proposals]}


def project_folders() -> tuple[list[tuple[str, str]], str]:
    """`([(folder, key)], line)`: each project's notes folder, and one line saying where
    they came from or why there are none."""
    import moxi.modules

    if not moxi.modules.available("projects"):
        return [], "projects is not installed (moxi install projects), so no project is inferred"
    found = moxi.modules.load("projects", interface=1)
    if not found.ok:
        return [], f"projects can't be loaded ({found.reason}), so no project is inferred"
    try:
        rows = found.api.list_projects()
    except found.api.ProjectsError as exc:
        return [], f"projects can't be read ({exc.reason}), so no project is inferred"
    from .resolve import notes_root_for

    folders = []
    for row in rows:
        folder = row.notes_root or notes_root_for(row.root, row.kind, row.key)
        if folder is not None:
            folders.append((os.path.normpath(os.fspath(folder)), row.key))
    if not folders:
        return [], "no project has a notes folder, so no project is inferred"
    return folders, f"{len(folders)} project notes folder(s)"


def infer_project(path: str, folders: list[tuple[str, str]]) -> tuple[str | None, str]:
    """The key of the project whose notes folder, the longest, holds `path`; or None and
    the reason."""
    from .resolve import under

    hits = [(folder, key) for folder, key in folders if under(path, folder)]
    if not hits:
        return None, "in no project's notes folder"
    longest = max(len(folder) for folder, _ in hits)
    keys = sorted({key for folder, key in hits if len(folder) == longest})
    if len(keys) > 1:
        return None, f"in the notes folder of {len(keys)} projects ({', '.join(keys)})"
    return keys[0], ""


def plan(*, root=None, types: _doctypes.Types | None = None) -> Plan:
    """What `--apply` would write, computed without writing anything."""
    report = _validate.validate(root=root, types=types)
    if not report.ok:
        return Plan(ok=False, root=report.root, reason=report.reason)
    out = Plan(ok=True, root=report.root, required=report.types.required)
    filed = report.filed
    folders: list[tuple[str, str]] = []
    if any("project" in n.header.missing(out.required)
           for n in filed if n.header is not None and n.header.ok):
        folders, out.projects = project_folders()
    for note in filed:
        out.proposals.append(_propose(note, out, folders))
    return out


def _propose(note: _validate.Note, plan: Plan, folders) -> Proposal:
    proposal = Proposal(path=note.path, relative=note.relative, type=note.type)
    header = note.header
    if header is None:
        proposal.refused = "the file can't be read"
        return proposal
    if not header.ok:
        proposal.refused = f"its frontmatter can't be read ({header.reason}), so nothing is " \
                           f"written into it"
        return proposal
    if len(note.matched) > 1:
        proposal.refused = (f"{len(note.matched)} types claim it at the same priority "
                            f"({', '.join(note.matched)}), so its type is unknown")
        return proposal
    for key in header.missing(plan.required):
        if key not in INFERABLE:
            proposal.skipped.append((key, NEVER))
        elif key == "type":
            proposal.additions[key] = note.type
        elif key == "project":
            found, why = infer_project(note.path, folders)
            if found:
                proposal.additions[key] = found
            else:
                proposal.skipped.append((key, plan.projects if not folders else why))
        else:
            stamp = _modified(note.path)
            if stamp:
                proposal.additions[key] = stamp
            else:
                proposal.skipped.append((key, "its modified date can't be read"))
    return proposal


def _modified(path: str) -> str | None:
    try:
        mtime = os.lstat(path).st_mtime
    except OSError:
        return None
    return datetime.date.fromtimestamp(mtime).isoformat()


def apply(plan: Plan) -> Plan:
    """Write each actionable proposal. Each key is added only if it is still missing
    when the file is read again here."""
    plan.apply = True
    real_root = os.path.realpath(plan.root)
    for proposal in plan.proposals:
        if proposal.actionable:
            _write(proposal, plan, real_root)
    return plan


def _write(proposal: Proposal, plan: Plan, real_root: str) -> None:
    """Read the file again through a descriptor that follows no symlink, add the keys,
    and replace the file in one step with `moxi.files.atomic_write`, keeping its mode. A
    replace renames over the path, so it never writes through a symlink either."""
    import moxi.files

    path = proposal.path
    try:
        before = os.lstat(path)
    except OSError as exc:
        proposal.error = f"can't be read now: {exc.strerror or exc}"
        return
    if not stat.S_ISREG(before.st_mode):
        proposal.error = "is no longer a regular file (a symlink is never written through)"
        return
    if not before.st_mode & stat.S_IWUSR:
        proposal.error = "is read-only, so it was left alone"
        return
    real = os.path.realpath(path)
    try:
        inside = os.path.commonpath([real, real_root]) == real_root
    except ValueError:
        inside = False
    if not inside:
        proposal.error = "is not under the notes root once its folders are resolved"
        return
    text = _read_nofollow(path, before, proposal)
    if text is None:
        return
    new = _added(text, proposal, plan.required)
    if new is None or new == text:
        return
    try:
        now = os.lstat(path)
        if (now.st_dev, now.st_ino, now.st_mtime_ns) != (before.st_dev, before.st_ino,
                                                          before.st_mtime_ns):
            proposal.error = "changed while it was read, so it was left alone"
            return
        moxi.files.atomic_write(path, new, mode=stat.S_IMODE(before.st_mode))
        proposal.applied = True
    except OSError as exc:
        proposal.error = f"the write failed: {exc.strerror or exc}"


def _read_nofollow(path: str, before, proposal: Proposal) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | O_NOFOLLOW | O_BINARY)
    except OSError as exc:
        proposal.error = f"can't be opened: {exc.strerror or exc}"
        return None
    try:
        now = os.fstat(fd)
        if not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino) != (before.st_dev,
                                                                          before.st_ino):
            proposal.error = "changed while it was opened, so it was left alone"
            return None
        chunks = []
        while chunk := os.read(fd, 1 << 16):
            chunks.append(chunk)
    except OSError as exc:
        proposal.error = f"can't be read: {exc.strerror or exc}"
        return None
    finally:
        os.close(fd)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        proposal.error = "is not UTF-8 text, so it was left alone"
        return None


def _added(text: str, proposal: Proposal, required) -> str | None:
    for key in [k for k in required if k in proposal.additions]:
        header = _fm.parse(text)
        if not header.ok:
            proposal.error = f"its frontmatter can't be read now ({header.reason})"
            return None
        if key in header.fields:
            proposal.skipped.append((key, "there when the file was read again; not replaced"))
            continue
        text, _ = _fm.upsert(text, key, proposal.additions[key], order=required)
    return text


# --- the text report ------------------------------------------------------------------

def render(plan: Plan, *, limit: int = 80) -> str:
    mode = "applied" if plan.apply else "a dry run; nothing was written"
    out = [f"notes backfill: {mode}", f"root: {plan.root}"]
    if not plan.ok:
        out += [f"can't backfill: {plan.reason}", "Nothing was planned or written."]
        return "\n".join(out) + "\n"
    c = plan.counts()
    if plan.projects:
        out.append(f"project: {plan.projects}")
    out += ["", f"{c['considered']} filed file(s): {c['with_additions']} would gain "
                f"{c['keys']} key(s), {c['complete']} have every key it can add, "
                f"{c['refused']} refused"]
    if plan.apply:
        out.append(f"written: {c['written']} file(s), errors: {c['errors']}")
    never = [k for k in plan.required if k not in INFERABLE]
    out += ["It only adds a missing key and never replaces one."
            + (f" It never infers {', '.join(never)}." if never else ""), ""]
    shown = [p for p in plan.proposals if p.additions or p.refused or p.error]
    for proposal in shown[:max(limit, 0)]:
        mark = "written" if proposal.applied else ("refused" if proposal.refused else
                                                   "error" if proposal.error else "add")
        out.append(f"{mark}: {proposal.relative} ({proposal.type})")
        if proposal.refused:
            out.append(f"    refused: {proposal.refused}")
        if proposal.error:
            out.append(f"    error: {proposal.error}")
        if not proposal.refused:
            out += [f"    + {k}: {v}" for k, v in proposal.additions.items()]
        out += [f"    - {k}: {r}" for k, r in proposal.skipped]
    if len(shown) > max(limit, 0):
        out.append(f"...and {len(shown) - max(limit, 0)} more (raise --limit, or use "
                   f"--json, which lists every one)")
    out.append("")
    out.append("A second run adds nothing." if plan.apply else
               "Write these with: moxi notes backfill --apply")
    return "\n".join(out) + "\n"


__all__ = ["INFERABLE", "Proposal", "Plan", "project_folders", "infer_project", "plan",
           "apply", "render"]
