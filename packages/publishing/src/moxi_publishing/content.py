"""The content piece, `publishing/content`, and `moxi publishing scan`: the text a public
repo holds.

The piece runs on pre-commit, fail closed, order 50. It scans what the commit adds: one
`git diff --cached` pass with every attribute, diff driver, text conversion and colour
turned off, and renames off, so a moved file is read whole and no attribute hides a file.
Each added line is a `(path, line, text)` triple, and each new path's name one more
triple at `@path`. The triples go through secrets' `credentials` and `private-names` sets
and then the provenance rules that apply to files. Any hit refuses the commit, named
`path:line: set/rule`; a provenance hit is shown with its line, a secrets hit never is.

A merge is judged by its own lines only: a line counts as added when it is added against
every parent, as git's combined diff marks it, and a path as new when no parent has it.
What a merge brings in from its other parents was judged when those commits were made,
and again when they are pushed; scanning it again would refuse text already public that
nobody can change.

The allowlists are read from what is judged, the index: `.secrets-allow` (credentials
lines, through secrets' `Allowlist.from_text`) and `.residue-allow` (provenance texts),
with the private file `secrets-allow-private` in the repo's common git directory. A line
of `.residue-allow` is `path TAB exact text TAB why`: the provenance hit of that exact
text in that file stands (`@path` for a path name). `.residue-allow` itself is not read
by the provenance rules, since it spells the texts it lets stand.

`scan_tree` is `moxi publishing scan`: every file of a commit's tree, read as git stores
it, and every path name, judged the same way with the allowlists of that tree.

Standard library only at import; secrets and the rules load when a scan runs.
"""

from __future__ import annotations

import re

#: The piece's declaration, read through the entry point `publishing.content`.
CONTENT = {
    "event": "pre-commit",
    "target": "moxi_publishing.content:pre_commit",
    "fail": "closed",
    "order": 50,
    "doc": "In a public repo, refuse a commit that adds a credential, a private name or "
           "a provenance pointer.",
}

PIECE_ID = "publishing/content"
#: The secrets sets a file is scanned with, and those of a scan without the private list.
SETS = ("credentials", "private-names")
CI_SETS = ("credentials",)
#: The allowlist files at a repo's root, and the private one in its common git directory.
PUBLIC_ALLOW = ".secrets-allow"
RESIDUE_ALLOW = ".residue-allow"
PRIVATE_ALLOW = "secrets-allow-private"
#: The path a new path's name is scanned at, and an allowlist line names.
NAME_PATH = "@path"
#: How much of a line a provenance refusal shows.
SHOWN_CHARS = 100

#: What makes a diff read every byte as git stores it, whatever the repo's config or
#: attributes say, with prefixes and paths the parser below expects.
DIFF_FLAGS = ("--unified=0", "--no-renames", "--text", "--no-textconv", "--no-ext-diff",
              "--no-color", "--no-relative", "--src-prefix=a/", "--dst-prefix=b/", "-z")

_HUNK = re.compile(rb"@@ -[0-9]+(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@")
_ESCAPES = {ord("a"): 7, ord("b"): 8, ord("t"): 9, ord("n"): 10, ord("v"): 11,
            ord("f"): 12, ord("r"): 13, ord('"'): 34, ord("\\"): 92}


class ScanError(Exception):
    """A read the scan needs failed: a git call, or text git wrote that isn't a patch."""


# -- reading a patch ------------------------------------------------------------------------

class FileChange:
    """One file of a patch: `path` (as git stores it, decoded as UTF-8 with other bytes
    kept as surrogates), `new` (the file is created), `lines` ((new-side line number,
    text) for each added line)."""

    __slots__ = ("path", "new", "lines")

    def __init__(self, path: str, new: bool = False):
        self.path, self.new, self.lines = path, new, []


def _unquote(data: bytes) -> tuple[bytes, int]:
    """Git's C-quoted name at the start of `data`: the name's bytes, and where the
    closing quote ends."""
    out = bytearray()
    i = 1
    while i < len(data):
        c = data[i]
        if c == 34:
            return bytes(out), i + 1
        if c == 92:
            nxt = data[i + 1]
            if 48 <= nxt <= 55:
                out.append(int(data[i + 1:i + 4], 8))
                i += 4
                continue
            out.append(_ESCAPES[nxt])
            i += 2
            continue
        out.append(c)
        i += 1
    raise ScanError("a quoted path in git's diff has no closing quote")


def _diff_path(rest: bytes) -> str:
    """The path of a `diff --git a/P b/P` line, from what follows `diff --git `. Renames
    and copies are off, so both sides name the same path; that is how an unquoted name
    with spaces splits."""
    if rest.startswith(b'"'):
        name, _ = _unquote(rest)
        if not name.startswith(b"a/"):
            raise ScanError("git's diff names a path without its a/ prefix")
        path = name[2:]
    else:
        size, odd = divmod(len(rest) - 5, 2)
        path = rest[2:2 + size]
        if odd or rest != b"a/" + path + b" b/" + path:
            raise ScanError("git's diff names two different paths for one file")
    return path.decode("utf-8", "surrogateescape")


def parse_patch(data: bytes) -> list[FileChange]:
    """The files of a `git diff` or `git log -p` patch made with `DIFF_FLAGS`' unified=0,
    each with the lines it adds. Lines outside a file's patch are passed over."""
    files: list[FileChange] = []
    current: FileChange | None = None
    lines = data.split(b"\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith(b"diff --git "):
            current = FileChange(_diff_path(line[len(b"diff --git "):]))
            files.append(current)
            continue
        if current is None:
            continue
        if line.startswith(b"new file mode "):
            current.new = True
            continue
        hunk = _HUNK.match(line)
        if hunk is None:
            continue
        old = 1 if hunk.group(1) is None else int(hunk.group(1))
        number = int(hunk.group(2))
        new = 1 if hunk.group(3) is None else int(hunk.group(3))
        # The counts say how many lines follow, so a line whose text looks like a header
        # is still read as text.
        while (old or new) and i < len(lines):
            body = lines[i]
            i += 1
            if body.startswith(b"\\"):
                continue
            if body.startswith(b"-"):
                old -= 1
            elif body.startswith(b"+"):
                current.lines.append((number, body[1:].decode("utf-8", "surrogateescape")))
                number += 1
                new -= 1
            else:
                old, new, number = old - 1, new - 1, number + 1
    return files


# -- the allowlists -------------------------------------------------------------------------

def residue_allowed(text: str | bytes | None, where: str = RESIDUE_ALLOW
                    ) -> frozenset[tuple[str, str]]:
    """The (path, exact text) pairs of a `.residue-allow` text. A line that is not blank,
    not a `#` comment and has no path or no text raises `moxi_secrets.AllowlistError`
    naming `where` and the line number."""
    from moxi_secrets import AllowlistError

    if text is None:
        return frozenset()
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            raise AllowlistError(f"{where}: is not UTF-8") from None
    pairs = set()
    for n, line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 2 or not fields[0] or not fields[1]:
            raise AllowlistError(f"{where}: line {n}: is not path, tab, exact text, tab, "
                                 "why")
        pairs.add((fields[0], fields[1]))
    return frozenset(pairs)


def read_blobs(git, specs) -> dict[str, bytes | None]:
    """Each object `spec` (`:path` for the index, `<tree>:path`) read with one
    `cat-file --batch`, as git stores it; None for one that doesn't exist. `git(*args,
    stdin_data=)` runs git and returns its finished process with bytes on stdout."""
    specs = list(specs)
    done = git("cat-file", "--batch", stdin_data="".join(f"{s}\n" for s in specs))
    if done.returncode != 0:
        raise ScanError(f"git cat-file failed: {_first_line(done.stderr)}")
    out: dict[str, bytes | None] = {}
    data, at = done.stdout, 0
    for spec in specs:
        end = data.index(b"\n", at)
        header = data[at:end].split(b" ")
        at = end + 1
        if header[-1] == b"missing":
            out[spec] = None
            continue
        if len(header) != 3 or header[1] != b"blob":
            raise ScanError(f"{spec} is not a file")
        size = int(header[2])
        out[spec] = data[at:at + size]
        at += size + 1
    return out


def allowlists(blobs: dict[str, bytes | None], public_spec: str, residue_spec: str,
               private: str | None):
    """(secrets' allowlist, the residue pairs) from the blobs read at those specs and the
    private file at `private` (None: no private file). Raises `AllowlistError`."""
    from moxi_secrets import Allowlist

    allow = Allowlist.from_text(blobs.get(public_spec), private, public_name=public_spec)
    return allow, residue_allowed(blobs.get(residue_spec), residue_spec)


# -- judging --------------------------------------------------------------------------------

def _shown(line: str) -> str:
    return line if len(line) <= SHOWN_CHARS else line[:SHOWN_CHARS - 3] + "..."


def _masked(text: str, hits) -> str:
    """`text` with each secrets hit's span replaced by its rule's name in brackets."""
    for hit in sorted(hits, key=lambda h: h.start, reverse=True):
        text = text[:hit.start] + f"[{hit.rule}]" + text[hit.end:]
    return text


def judge(lines, names=(), *, sets=SETS, allow=None, residue=frozenset(), rules=None
          ) -> list[str]:
    """What can't be published among `lines`, `(path, line number, text)` triples, and
    `names`, path names: one printable line per hit, in path and line order. `allow` is
    secrets' allowlist, `residue` the `.residue-allow` pairs; `rules`, the provenance rules
    that run (None: publishing's config). Raises `moxi_secrets.SetUnavailable`."""
    from moxi_publishing import rules as _rules

    names = list(names)
    enabled = _rules.enabled_rules() if rules is None else tuple(rules)
    found: list[tuple[tuple, str]] = []
    triples = list(lines) + [(NAME_PATH, n, name) for n, name in enumerate(names, 1)]
    if sets:
        from moxi_secrets import scan_lines

        by_name: dict[int, list] = {}
        for hit in scan_lines(triples, sets, allow=allow):
            if hit.path == NAME_PATH:
                by_name.setdefault(hit.line, []).append(hit.hit)
            else:
                found.append(((hit.path, hit.line, 0),
                              f"{hit.path}:{hit.line}: {hit.hit.set}/{hit.hit.rule}"))
        for n, hits in by_name.items():
            shown = _masked(names[n - 1], hits)
            for hit in hits:
                found.append(((names[n - 1], 0, 0),
                              f"{shown} (path name): {hit.set}/{hit.rule}"))
    by_path: dict[str, list[tuple[int, str]]] = {}
    for path, number, text in triples:
        if path != RESIDUE_ALLOW:
            by_path.setdefault(path, []).append((number, text))
    candidates: dict[str, tuple[str, ...]] = {}
    for path, batch in by_path.items():
        # One search of the file's text per rule finds every file worth reading line by
        # line: no rule's shape needs a line's start or end, so a line's match is also a
        # match in the joined text.
        joined = "\n".join(text for _, text in batch)
        candidates[path] = tuple(rule for rule, _ in _rules.hits(joined, _rules.FILES,
                                                                  enabled))
    for path, number, text in triples:
        if not candidates.get(path):
            continue
        for rule, matched in _rules.hits(text, _rules.FILES, candidates[path], every=True):
            if (path, matched) in residue:
                continue
            if path == NAME_PATH:
                found.append(((text, 0, 1), f"{text} (path name): {rule}"))
            else:
                found.append(((path, number, 1), f"{path}:{number}: {rule}: {_shown(text)}"))
    found.sort(key=lambda item: item[0])
    seen, out = set(), []
    for _, line in found:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out


# -- the piece ------------------------------------------------------------------------------

def _first_line(text) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return (text.strip().splitlines() or ["no reason given"])[0]


def staged(git, git_dir: str | None) -> tuple[list[tuple[str, int, str]], list[str]]:
    """What the commit being made adds: its added lines as triples and its new path
    names. In a merge, only what is added against every parent."""
    import os

    def changes(*against: str) -> list[FileChange]:
        done = git("diff", "--cached", *DIFF_FLAGS, *against, "--")
        if done.returncode != 0:
            raise ScanError(f"git diff --cached failed: {_first_line(done.stderr)}")
        return parse_patch(done.stdout)

    first = changes()
    others: list[str] = []
    merge_head = os.path.join(git_dir or "", "MERGE_HEAD")
    if git_dir and os.path.exists(merge_head):
        with open(merge_head, encoding="ascii") as fh:
            others = [line.strip() for line in fh if line.strip()]
    lines = [(f.path, n, text) for f in first for n, text in f.lines]
    names = [f.path for f in first if f.new]
    for parent in others:
        against = changes(parent)
        added = {(f.path, n) for f in against for n, _ in f.lines}
        new = {f.path for f in against if f.new}
        lines = [t for t in lines if (t[0], t[1]) in added]
        names = [name for name in names if name in new]
    return lines, names


def _next_step() -> str:
    return ("Nothing was committed. Take those out and commit again; or, where one is "
            f"meant to stand, allow it: a credential in {PUBLIC_ALLOW}, a provenance text "
            f"in {RESIDUE_ALLOW} (stage the file you change), a private name in "
            f"{PRIVATE_ALLOW} in the repo's git directory.")


def pre_commit(ctx):
    """The piece."""
    import os

    from moxi_repo import Outcome

    from moxi_publishing.message import is_public, no_flag

    if not is_public(ctx):
        return no_flag(ctx, PIECE_ID)

    def git(*args, stdin_data=None):
        return ctx.git(*args, stdin_data=stdin_data, binary=True)

    try:
        lines, names = staged(git, ctx.git_dir)
        public, residue = f":{PUBLIC_ALLOW}", f":{RESIDUE_ALLOW}"
        blobs = read_blobs(git, (public, residue))
        allow, pairs = allowlists(blobs, public, residue,
                                  os.path.join(ctx.common_dir, PRIVATE_ALLOW))
        refused = judge(lines, names, allow=allow, residue=pairs)
    except Exception as exc:  # noqa: BLE001 - every failure to scan refuses, named
        return Outcome.refuse(f"what this commit adds can't be scanned, so it is refused: "
                              f"{exc}")
    if refused:
        return Outcome.refuse("this repo is public, and what this commit adds can't be "
                              "published:", *refused, _next_step())
    return Outcome.ok()


# -- the whole tree -------------------------------------------------------------------------

def scan_tree(root: str, rev: str = "HEAD", *, ci: bool = False) -> list[str]:
    """`moxi publishing scan`: every file of the tree at `rev` in the repo at `root`, read
    as git stores it, and every path name, judged as the content piece judges added lines,
    with the allowlists of that tree. `ci` leaves out the private names and the private
    allowlist. Returns the refusal lines. Raises `ScanError`,
    `moxi_secrets.SetUnavailable` or `moxi_secrets.AllowlistError`."""
    from moxi_repo.api import git as _git

    def git(*args, stdin_data=None):
        return _git(root, *args, stdin_data=stdin_data, binary=True)

    done = git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{tree}}")
    if done.returncode != 0:
        raise ScanError(f"{rev} names no commit or tree here")
    tree = done.stdout.decode("ascii").strip()
    done = git("ls-tree", "-r", "-z", "--full-tree", tree)
    if done.returncode != 0:
        raise ScanError(f"git ls-tree failed: {_first_line(done.stderr)}")
    entries = []
    for record in done.stdout.split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        _mode, kind, oid = meta.decode("ascii").split(" ")
        entries.append((path.decode("utf-8", "surrogateescape"), kind, oid))
    blobs = read_blobs(git, sorted({oid for _, kind, oid in entries if kind == "blob"}))
    lines = []
    for path, kind, oid in entries:
        if kind != "blob":
            continue
        text = blobs[oid].decode("utf-8", "surrogateescape")
        lines += [(path, n, line) for n, line in enumerate(text.split("\n"), 1)]
    private = None
    if not ci:
        done = git("rev-parse", "--path-format=absolute", "--git-common-dir")
        if done.returncode != 0:
            raise ScanError(f"git rev-parse failed: {_first_line(done.stderr)}")
        import os

        private = os.path.join(done.stdout.decode("utf-8", "surrogateescape").strip(),
                               PRIVATE_ALLOW)
    public, residue = f"{tree}:{PUBLIC_ALLOW}", f"{tree}:{RESIDUE_ALLOW}"
    allow, pairs = allowlists(read_blobs(git, (public, residue)), public, residue, private)
    return judge(lines, [path for path, _, _ in entries], sets=CI_SETS if ci else SETS,
                 allow=allow, residue=pairs)


__all__ = ["CONTENT", "PIECE_ID", "SETS", "CI_SETS", "NAME_PATH", "DIFF_FLAGS",
           "FileChange", "ScanError", "parse_patch", "residue_allowed", "read_blobs",
           "allowlists", "judge", "staged", "pre_commit", "scan_tree"]
