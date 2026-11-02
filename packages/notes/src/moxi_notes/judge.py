"""What every judge shares: the sets, the allowlists, the cap and one finding's words.

A judge is the snapshot's prepare step, the pre-commit gate, the push gate or the clean
filter. Each scans whole blobs (`moxi_notes.blobs`) with secrets' `credentials` and
`private-names` sets, never `broad`, at the blob's repo-relative path, against two
allowlist files read where secrets accepts each: the public `.secrets-allow` at the work
tree's top (credentials lines only) and the private `secrets-allow-private` in the repo's
common git dir, which git never tracks.

A file can't be judged when it is too big to read (`max_mb`): that is a finding of its
own, `notes/too-large`, which no allowance keeps. An allowlist that can't be read is read
as empty, which only takes allowances away; the judge goes on with none and says why.

A finding's words carry the rule and the line, never the text that matched.

Standard library only at import; secrets and the config are imported where they're used.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: The sets every judge scans with.
SETS = ("credentials", "private-names")
#: The public allowlist, at the work tree's top.
PUBLIC = ".secrets-allow"
#: The private allowlist, in the common git dir.
PRIVATE = "secrets-allow-private"
#: The finding for a blob over `max_mb`.
TOO_LARGE = "notes/too-large"
MIB = 1024 * 1024


@dataclass(frozen=True)
class Finding:
    """One reason a file holds a secret: `rule` is `<set>/<rule id>`, `line` is 1-based
    (None for a whole-file finding)."""

    rule: str
    line: int | None = None
    note: str = ""

    def words(self) -> str:
        """`credentials/github-pat line 12`, or the rule and its note."""
        if self.line is not None:
            return f"{self.rule} line {self.line}"
        return f"{self.rule} ({self.note})" if self.note else self.rule


def too_large(size: int, max_bytes: int) -> Finding:
    mb = max(1, round(size / MIB))
    cap = max_bytes // MIB if max_bytes % MIB == 0 else f"{max_bytes / MIB:.1f}"
    return Finding(TOO_LARGE, None, f"{mb} MiB, over max_mb {cap}; raise max_mb to judge it")


def of_hit(hit) -> Finding:
    return Finding(f"{hit.set}/{hit.rule}", hit.line)


def words(findings) -> str:
    """One file's findings in a few words: the first, and how many more."""
    findings = list(findings)
    head = findings[0].words()
    more = len(findings) - 1
    return head + (f" and {more} more" if more else "")


def config():
    """notes' settings as they stand. Raises `moxi.config.ConfigError`."""
    from moxi import config as _config

    from . import settings

    return _config.load(settings.MODULE, settings.schema())


def max_bytes(settings) -> int:
    return int(settings["max_mb"]) * MIB


def allowlist(root, common_dir) -> tuple[object, str | None]:
    """(the allowlist, why it reads as empty or None). The public file is
    `<root>/.secrets-allow`, the private one `<common_dir>/secrets-allow-private`; a file
    that isn't there adds no line."""
    from moxi_secrets import Allowlist, AllowlistError

    public = os.path.join(os.fspath(root), PUBLIC) if root else None
    private = os.path.join(os.fspath(common_dir), PRIVATE) if common_dir else None
    try:
        return Allowlist.read(public=public, private=private), None
    except AllowlistError as exc:
        return Allowlist(), str(exc)
    except OSError as exc:
        return Allowlist(), f"{exc.filename or 'an allowlist'}: can't be read ({exc.strerror})"


def private_place_ok(common_dir) -> bool:
    """True when secrets will read a private allowlist in `common_dir`: its resolved path
    has a folder named `.git` in it, as secrets requires of a private file outside its own
    config directory. A notes repo made with `--separate-git-dir`, or one whose common git
    dir is a bare `notes.git`, has none, and every judge then reads its allowlist as
    empty."""
    from pathlib import Path

    return ".git" in Path(os.fspath(common_dir)).resolve().parts


def common_dir(git) -> str:
    """The repo's common git dir, absolute, from one `git rev-parse`."""
    from . import blobs

    out = blobs.run(git, "rev-parse", "--path-format=absolute", "--git-common-dir")
    found = os.fsdecode(out).strip()
    if not found:
        raise blobs.GitError("git rev-parse gave no common git dir")
    return found


def scan(text: str, path: str, allow) -> list[Finding]:
    """The findings in one text at `path` that `allow` doesn't keep. Raises
    `moxi_secrets.SetUnavailable`."""
    import moxi_secrets

    return [of_hit(h) for h in moxi_secrets.scan(text, SETS, path=path, allow=allow)]


def judge(entries, git, *, cap: int, allow) -> dict[str, list[Finding]]:
    """The findings of each `(path, blob id)` pair, by path; a path with none is left
    out. Raises `blobs.GitError` and `moxi_secrets.SetUnavailable`."""
    from . import blobs

    entries = [(p, b) for p, b in entries if b]
    found, big = blobs.read(git, [b for _, b in entries], max_bytes=cap)
    out: dict[str, list[Finding]] = {}
    for path, blob in entries:
        if blob in big:
            out.setdefault(path, []).append(too_large(big[blob], cap))
            continue
        if blob not in found:
            continue
        hits = scan(blobs.text(found[blob]), path, allow)
        if hits:
            out.setdefault(path, []).extend(hits)
    return out


def scan_names(text: str, allow) -> list[Finding]:
    """Private-names findings in an author or committer line, at the path `@author`."""
    import moxi_secrets

    return [of_hit(h) for h in moxi_secrets.scan(text, ("private-names",), path="@author",
                                                 allow=allow)]


__all__ = ["SETS", "PUBLIC", "PRIVATE", "TOO_LARGE", "Finding", "too_large", "words",
           "config", "max_bytes", "allowlist", "private_place_ok", "common_dir", "scan",
           "judge", "scan_names"]
