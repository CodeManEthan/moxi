"""`moxi notes scan`: which files a snapshot would leave out now, with their rules and lines.

It judges the notes root's tracked and untracked files (ignored files and nested repos
aside) the way setup's preview does, which is the way a snapshot does, and changes
nothing. A file is listed when the snapshot that takes its current content would leave it
out. One already committed stays as it was committed until it changes.

Under `leave-out` every file with a finding is left out. Under `redact` the clean filter
gives a file's committed copy, and its own answer decides: a file it redacts commits with
its hits blanked, and is listed as redacted, not left out. A file it can't redact (too big,
binary, wholly one secret, a path-only rule) is left out. While the filter is missing, or
the allowlist can't be read, it redacts nothing, so every file with a finding is left out.

`scan()` gives a `Scan`; its `as_dict()` is `moxi notes scan --json`, whose shape
`JSON_SHAPE` states and the verb's help prints. A later component reads it, so a change to
it is a change to notes' interface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

JSON_SHAPE = """\
--json prints one object, the same shape whatever the exit code:
  {"root": str, "action": "leave-out" | "redact", "judged": int,
   "left_out": [{"path": str, "reason": str,
                 "findings": [{"rule": str, "line": int | null, "note": str}]}],
   "redacted": [{"path": str, "findings": [...]}],
   "authors": int, "allowlist_problem": str | null, "error": str | null}
Lists are sorted by path. rule is <set>/<rule id>; line is 1-based, null for a
whole-file finding. No finding carries the text that matched. authors counts the commit
identities the push gate would refuse for a private name. error says why nothing could
be judged; the lists are then empty."""

#: Why a file the clean filter answered for is left out under `redact`.
CANT = "the clean filter can't redact it"


@dataclass
class Scan:
    """What `scan()` found. `left_out` maps each path to `(findings, why)`, `why` naming
    what keeps it out beyond its findings (None when the findings say it); `redacted` maps
    a path to its findings; `problem` says why the allowlist reads as empty."""

    root: str
    action: str
    judged: int = 0
    left_out: dict = field(default_factory=dict)
    redacted: dict = field(default_factory=dict)
    authors: int = 0
    problem: str | None = None
    error: str | None = None

    def reason(self, path: str) -> str:
        from . import judge

        findings, why = self.left_out[path]
        return judge.words(findings) + (f"; {why}" if why else "")

    def as_dict(self) -> dict:
        def findings(fs):
            return [{"rule": f.rule, "line": f.line, "note": f.note} for f in fs]

        return {
            "root": self.root, "action": self.action, "judged": self.judged,
            "left_out": [{"path": p, "reason": self.reason(p),
                          "findings": findings(self.left_out[p][0])}
                         for p in sorted(self.left_out)],
            "redacted": [{"path": p, "findings": findings(self.redacted[p])}
                         for p in sorted(self.redacted)],
            "authors": self.authors, "allowlist_problem": self.problem,
            "error": self.error,
        }


def _redacts(root: str, where, cap: int, found: dict, out: Scan) -> None:
    """Sort `found` by the clean filter's own answer for each file."""
    from . import doctor, judge
    from . import filter as clean_filter
    from .setup import _content

    if doctor._filter(where, "redact").state == "fail":
        why = "the clean filter is not in place, so it redacts nothing; fix: moxi notes setup"
        if out.problem:
            why += f"; judged with no allowances, since {out.problem}"
    elif out.problem:
        why = f"the clean filter redacts nothing while the allowlist can't be read: " \
              f"{out.problem}"
    else:
        why = None
    allow, _ = judge.allowlist(root, where.common_dir)
    state = clean_filter.State(allow=allow, cap=cap, redact=True)
    for path, findings in found.items():
        one = why
        if why is None and all(f.rule != judge.TOO_LARGE for f in findings):
            data, _ = _content(os.path.join(root, path), cap)
            status, copy = (clean_filter.clean(data, path, state) if data is not None
                            else ("error", None))
            if status == "success" and copy != data:
                out.redacted[path] = findings
                continue
            one = CANT
        out.left_out[path] = (findings, one)


def scan() -> Scan:
    """Judge the notes root now. Raises `moxi.config.ConfigError`."""
    from . import judge
    from . import settings as _settings
    from .setup import preview, where

    places = _settings.read()
    s = places.settings
    root = str(places.root)
    out = Scan(root=root, action=s["action"])
    found_where = where(root)
    if not found_where.is_top:
        out.error = (f"the notes root {root} is no git repo's top, so no snapshot runs; "
                     "fix: moxi notes setup")
        return out
    found = preview(root, push=s["snapshot.push"] or None,
                    common_dir=found_where.common_dir)
    if found.error:
        out.error = found.error
        return out
    out.judged, out.authors, out.problem = found.judged, found.authors, found.problem
    if out.action == "redact":
        _redacts(root, found_where, judge.max_bytes(s), found.found, out)
    else:
        why = f"judged with no allowances, since {found.problem}" if found.problem else None
        for path, findings in found.found.items():
            out.left_out[path] = (findings, why)
    return out


def print_scan(found: Scan, out) -> None:
    """The text `moxi notes scan` prints: one line per finding, as the gates word them."""
    from .gates import _lines

    print(f"notes: judged {found.judged} files in {found.root} as a snapshot would "
          f"(action {found.action}); {len(found.left_out)} would be left out", file=out)
    whys = {why for _, why in found.left_out.values() if why and why != CANT}
    for why in sorted(whys):
        print(f"notes: {why}", file=out)
    if found.problem and not whys:
        print(f"notes: judged with no allowances, since {found.problem}", file=out)
    for path in sorted(found.left_out):
        findings, why = found.left_out[path]
        for line in _lines({path: findings}):
            print(line + (" (can't be redacted)" if why == CANT else ""), file=out)
    if found.redacted:
        print(f"notes: {len(found.redacted)} would be committed with the hits blanked",
              file=out)
        for line in _lines(found.redacted):
            print(line, file=out)
    if found.authors:
        noun = "author holds" if found.authors == 1 else "authors hold"
        print(f"notes: {found.authors} commit {noun} a private name; the push gate "
              "refuses a push of their commits until an @author allowance keeps it",
              file=out)


__all__ = ["JSON_SHAPE", "CANT", "Scan", "scan", "print_scan"]
