"""The push piece, `publishing/push`: what a push makes public.

It runs on pre-push, fail closed, order 50. Its settings, in each repo's git config:

- `main`, one value: the repo's main branch;
- `protected`, any number: branches beside `main` a push may never delete;
- `word-required`, retired: the word is the approval piece's now. While it holds `true`
  every push is refused, naming the commands that move it (`approval enable`) and remove
  it (`enable --set word-required=`), so no repo loses its approval silently;
- `free-ref`, any number: ref patterns outside branches and release tags that a push may
  create, move or delete. A pattern never applies to a branch (`refs/heads/*`) or to a
  release tag, and `moxi publishing enable` refuses one that could match either;
- `standing-branch`, any number: patterns such as `ci/*` naming standing branches (see
  STANDING BRANCHES below).

Nothing leaves unless every ref the push moves follows these rules:

- a branch is created, or moved forward: the remote's tip is an ancestor of the new one;
- a branch is deleted only when it is not `main` or protected, and the remote's tip, read
  locally, is an ancestor of the tracking ref of `main`; a tip not here, or no tracking
  ref, refuses with "fetch first" (for a push to a bare URL, which no fetch gives tracking
  refs, with "push through a named remote");
- a tag shaped like a release tag is never moved or deleted; one created must be a release
  tag of a member at its commit and pass the tag gate (`moxi_publishing.tags.gate`), read
  against `<remote>/<main>`;
- any other ref, another tag among them, moves only when a `free-ref` pattern names it;
- every ref pushed points at a commit, or at a tag whose chain ends at one;
- a push creates at most one tag, of any kind, so each tag gets its own push event and
  its own release run, and no release waits on another tag of the same push. (GitHub
  starts nothing at all for the tags of a push that creates more than three.)

And everything the push makes public for the first time passes what a commit would have
had to pass. Already public means reachable from the remote's tracking refs or a ref
line's remote tip; with no tracking refs, the remote is asked, and only an empty one is
scanned whole. For each commit sent: its added lines and new path names, read as the
content piece reads them (a merge by its own lines only), with the allowlists that commit
holds; its message, through the message piece's sets and rules and the repo's
`repo/commit-rules` rules; its headers whole (author, committer, a merged signed tag),
through secrets' sets at `@author`. A trailer still there whose key is on `drop-trailer`
or `pass-trailer` means a commit-msg hook didn't run; it refuses a commit whose committer
is the repo's own identity (secrets' `author.*`), and a foreign commit is scanned like
any other. For each tag pushed, each tag object of its chain: its headers and message the
same way. Each ref name goes through secrets' sets at `@ref`.

In a repo that sets `standing-branch`, a push to the publishing remote (the URL reduces
by `github_repo` to the OWNER/REPO in `moxi-publishing.github`) that creates or moves
`main` to commit C passes only when GitHub has, at C, a run of the root `pyproject.toml`'s
`check-workflow` (read at C) from a push to a standing branch, in whose jobs `check-job`
(read at C) concluded `success` (`main_ci`). GitHub is read as the tag gate reads it
(`tags.default_web`), each read bounded at ten seconds, and the jobs are GitHub's latest
attempt of the run (the jobs list's default), so a re-run that passed counts and one that
failed after a pass does not. A read that fails or a setting missing at C refuses, naming
which. This adds to the rule that `main` only moves forward. A push of
`main` from a repo with no `standing-branch`, or to any other URL, reads no CI here.

STANDING BRANCHES. A `standing-branch` value is a fixed prefix of one or more path parts,
each ending in `/`, then `*` (`ci/*`), and must not match `main` or a `protected` branch;
while any value breaks that, every push is refused (`Standing`). A branch under a pattern
(`refs/heads/ci/x`) is a standing branch:

- it is created, moved or forced only to a commit (not a tag object) that is the tip of
  the local branch named by the rest of its name (`ci/x` at the tip of local `x`), so
  nothing but a local branch's tip goes out under the prefix;
- it is deleted whatever its tip, with no merge check and no fetch first;
- what it sends is judged as any push's, with no setting that narrows it;
- its entry in the log's `refs` names the pattern that allowed it (`standing`).

Another pre-push guard that reads this setting must reduce URLs by `github_repo` and judge
patterns by `Standing` exactly, or the two disagree on one push.

A push that passes is logged first: one JSON line in the module's state directory,
`pushes.jsonl`. A log that can't be written refuses the push. Its `word` is
`MOXI_PUBLISHING_PUSH_WORD` as the push carried it, or null when none, or a blank one, was
given; the approval piece judges it.

Standard library only at import.
"""

from __future__ import annotations

import os
import re

#: The piece's declaration, read through the entry point `publishing.push`.
PUSH = {
    "event": "pre-push",
    "target": "moxi_publishing.push:pre_push",
    "fail": "closed",
    "order": 50,
    "doc": "In a public repo, refuse a push that rewrites or deletes what it may not, or "
           "makes public what a commit would have refused.",
    "settings": {"main": "one", "protected": "multi", "word-required": "one",
                 "free-ref": "multi", "standing-branch": "multi"},
}

PIECE_ID = "publishing/push"
MAIN = "main"
PROTECTED = "protected"
WORD_REQUIRED = "word-required"
FREE_REF = "free-ref"
STANDING_BRANCH = "standing-branch"
#: The repo's OWNER/REPO on GitHub, in its git config: where a push of `main` waits for
#: `main_ci`, which reads CI there.
GITHUB_KEY = "moxi-publishing.github"
#: Where the push names the place it was approved; the approval piece reads it, and the
#: log keeps it.
WORD_ENV = "MOXI_PUBLISHING_PUSH_WORD"
#: The most tags one push may create: one, of any kind.
MAX_TAGS = 1
#: The push log's file name in the module's state directory.
LOG_FILE = "pushes.jsonl"
#: The paths secrets scans a commit's headers and a ref's name at.
AUTHOR_PATH = "@author"
REF_PATH = "@ref"
#: How long asking the remote for its refs may take.
LS_REMOTE_TIMEOUT_S = 10.0

_HEADS = "refs/heads/"
_TAGS = "refs/tags/"


#: A URL's scheme and its userinfo, up to the last `@` before the path: git reads the
#: host after the last one, so a password may hold an unencoded `@`.
_USERINFO = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/]*@")

def _shown_url(url: str) -> str:
    """`url` without its userinfo (the `user:token@` after the scheme), as it is printed
    and logged: a remote URL can carry a credential."""
    return _USERINFO.sub(r"\1", url)


#: A good `standing-branch` value: a fixed prefix of one or more path parts, each ending
#: in `/`, then `*`.
_STANDING = re.compile(r"^((?:[^/*?\[\\\s]+/)+)\*$")
#: The OWNER/REPO a GitHub URL names, in the three forms git sends to GitHub only:
#: `git@github.com:O/R`, `ssh://git@github.com[:port]/O/R` and
#: `https://[user@]github.com/O/R`, each with one `.git` optional. USER is one or more
#: ASCII letters, digits, `.`, `_` and `-`, and any other byte refuses the URL: some,
#: such as `#`, `?`, `\` or a control character, end the URL's authority in git and move
#: the real host. Matched against the whole string (`github_repo`).
#: The bytes an https USER may hold.
_USER = r"[A-Za-z0-9._-]+"
_GITHUB_REPO = re.compile(r"(?:git@github\.com:|ssh://git@github\.com(?::[0-9]+)?/"
                          r"|https://(?:" + _USER + r"@)?github\.com/)"
                          r"([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?")


def github_repo(url: str) -> str | None:
    """The OWNER/REPO `url` reduces to, or None. Only the whole of `url` in one of the
    three forms git sends to GitHub reduces (`_GITHUB_REPO`, by `fullmatch`, so no
    trailing newline or `/`); anything else, such as `git@github.com/O/R` (no colon: git
    takes it for a local directory), `http://`, `ssh://git@github.com:O/R`, another host,
    or an https USER with any byte but ASCII letters, digits, `.`, `_` and `-` (git sends
    `https://evil.example#@github.com/O/R` to evil.example), reduces to nothing."""
    match = _GITHUB_REPO.fullmatch(url)
    return match.group(1) if match else None


def _under(branch: str, prefix: str) -> bool:
    return branch.startswith(prefix) and len(branch) > len(prefix)


def standing_problem(value: str, main: str, protected) -> str | None:
    """Why `value` can't be a `standing-branch` pattern, or None: it must be a fixed
    prefix ending in `/` then `*`, and match neither `main` nor a `protected` branch."""
    match = _STANDING.match(value)
    if match is None:
        return (f"{STANDING_BRANCH} {value!r} is not a fixed prefix ending in / followed "
                "by *, such as ci/*")
    hit = [b for b in [main, *sorted(protected)] if _under(b, match.group(1))]
    if hit:
        return f"{STANDING_BRANCH} {value!r} matches {hit[0]}, which is main or protected"
    return None


class Standing:
    """A repo's standing branches, from the push piece's settings: `patterns`, the good
    `standing-branch` values, and `problems`, one line per value that is not a good
    pattern. Blank values are skipped and repeats read once."""

    def __init__(self, settings: dict):
        main = _one(settings, MAIN) or "main"
        protected = {v.strip() for v in settings.get(PROTECTED, ()) if v.strip()}
        values = [v.strip() for v in settings.get(STANDING_BRANCH, ()) if v.strip()]
        self.patterns: list[str] = []
        self.problems: list[str] = []
        for value in dict.fromkeys(values):
            why = standing_problem(value, main, protected)
            if why is None:
                self.patterns.append(value)
            else:
                self.problems.append(why)

    def match(self, remote_ref: str) -> str | None:
        """The pattern that makes `remote_ref` a standing branch, or None. Only a branch
        under a pattern's prefix with something after it matches; a tag never does."""
        if not remote_ref.startswith(_HEADS):
            return None
        branch = remote_ref[len(_HEADS):]
        return next((p for p in self.patterns if _under(branch, p[:-1])), None)


def _zero(sha: str) -> bool:
    return not sha.strip("0")


def _one(settings: dict, key: str) -> str | None:
    values = [v for v in settings.get(key, ()) if v.strip()]
    return values[-1].strip() if values else None


def _true(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "yes", "on", "1")


def _free(ref: str, patterns) -> bool:
    """Whether a `free-ref` pattern names `ref`. Never a branch or a release tag."""
    from fnmatch import fnmatchcase

    from moxi_publishing.flag import looks_like_release_tag

    if ref.startswith(_HEADS):
        return False
    if ref.startswith(_TAGS) and looks_like_release_tag(ref[len(_TAGS):]):
        return False
    return any(fnmatchcase(ref, pattern) for pattern in patterns)


# -- reading objects ------------------------------------------------------------------------

def _headers(raw: bytes) -> tuple[list[tuple[str, str]], str]:
    """A commit's or tag's headers as (name, value, continuation lines joined by
    newlines), and its message."""
    text = raw.decode("utf-8", "surrogateescape")
    head, _, message = text.partition("\n\n")
    headers: list[tuple[str, str]] = []
    for line in head.split("\n"):
        if line.startswith(" ") and headers:
            name, value = headers[-1]
            headers[-1] = (name, value + "\n" + line[1:])
        elif line:
            name, _, value = line.partition(" ")
            headers.append((name, value))
    return headers, message


def _identity(value: str) -> tuple[str, str]:
    """(name, email) of an author, committer or tagger header's value."""
    name, _, rest = value.partition(" <")
    return name.strip(), rest.partition(">")[0].strip()


class _Reader:
    """Judges commit and tag objects' headers and messages, adding to `reasons`."""

    def __init__(self, sets=None):
        from moxi_publishing.content import SETS

        self.sets = SETS if sets is None else tuple(sets)
        self.reasons: list[str] = []

    def judge_headers(self, where: str, headers, skip) -> None:
        """Every header but those in `skip`, through secrets' sets at `@author`; a merged
        signed tag's text also as a tag."""
        from moxi_secrets import scan_lines

        lines, n = [], 0
        for name, value in headers:
            if name in skip:
                continue
            if name == "mergetag":
                self.judge_tag(f"{where} mergetag", value.encode("utf-8", "surrogateescape"))
                continue
            for part in value.split("\n"):
                n += 1
                lines.append((AUTHOR_PATH, n, f"{name} {part}"))
        for hit in scan_lines(lines, self.sets):
            name = lines[hit.line - 1][2].partition(" ")[0]
            self.reasons.append(f"{where} {name} header: {hit.hit.set}/{hit.hit.rule}")

    def judge_tag(self, where: str, data: bytes) -> None:
        from moxi_publishing import message

        headers, text = _headers(data)
        self.judge_headers(where, headers, skip=("object", "type"))
        judged = message.judge(text, sets=self.sets, whole=True)
        self.reasons += [f"{where} message {line}" for _, line in judged.refusals]


def judge_history(git, rev: str, from_rev: str | None, *, sets=None) -> list[str]:
    """The refusals for what `rev` brings beyond `from_rev`: each commit of `from_rev..rev`, its
    headers whole and its message, and, when `rev` names an annotated tag, each tag object
    of its chain. `git(*args, stdin_data=)` runs git with bytes on stdout. Raises
    `content.ScanError`."""
    from moxi_publishing import message
    from moxi_publishing.content import ScanError, _first_line, read_objects

    reader = _Reader(sets)
    obj = rev
    seen = set()
    while True:
        found = read_objects(git, [obj])[obj]
        if found is None:
            raise ScanError(f"{obj} names no object here")
        if found[0] != "tag" or obj in seen:
            break
        seen.add(obj)
        reader.judge_tag(f"tag {rev}", found[1])
        obj = dict(_headers(found[1])[0]).get("object", "").strip()
    if from_rev is None:
        return reader.reasons
    done = git("rev-list", f"{from_rev}..{rev}", "--")
    if done.returncode != 0:
        raise ScanError(f"git rev-list failed: {_first_line(done.stderr)}")
    commits = done.stdout.decode("ascii").split()
    raw = read_objects(git, commits)
    for sha in commits:
        kind, data = raw[sha] or ("missing", b"")
        if kind != "commit":
            reader.reasons.append(f"{sha[:12]}: can't be read as a commit")
            continue
        headers, text = _headers(data)
        reader.judge_headers(sha[:12], headers, skip=("tree", "parent"))
        judged = message.judge(text, sets=reader.sets, whole=True)
        reader.reasons += [f"{sha[:12]} message {line}" for _, line in judged.refusals]
    return reader.reasons


def given_word() -> str | None:
    """The push word in this process's environment, `WORD_ENV`; a blank one is none."""
    word = os.environ.get(WORD_ENV) or None
    return None if word is None or not word.strip() else word


class _Push(_Reader):
    """One push being judged: the context, its settings, and what it found."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        settings = ctx.settings
        self.main = _one(settings, MAIN) or "main"
        self.protected = {v.strip() for v in settings.get(PROTECTED, ()) if v.strip()}
        self.word_required = _true(_one(settings, WORD_REQUIRED))  # retired; refused
        self.free = [v.strip() for v in settings.get(FREE_REF, ()) if v.strip()]
        self.remote = ctx.args[0] if ctx.args else ""
        self.url = ctx.args[1] if len(ctx.args) > 1 else self.remote
        # What is printed and logged; a push to a bare URL gives git the URL as the remote.
        self.shown_remote, self.shown_url = _shown_url(self.remote), _shown_url(self.url)
        self.updates = ctx.updates
        self.word = given_word()
        self.standing = Standing(settings)
        self.commits: list[str] = []

    def fetch_fix(self) -> str:
        """How to bring the remote's refs here. git passes a push to a bare URL the URL as
        the remote too, and no fetch makes tracking refs under a URL, so the fix there is a
        named remote."""
        if self.remote == self.url:
            return (f"push through a named remote instead (git remote add NAME "
                    f"{self.shown_url}, git fetch NAME, then git push NAME ...), since no "
                    "fetch makes tracking refs for a bare URL")
        return f"fetch first (git fetch {self.shown_remote})"

    def git(self, *args, stdin_data=None, timeout_s=None):
        from moxi_publishing.content import NO_REPLACE

        return self.ctx.git(NO_REPLACE, *args, stdin_data=stdin_data, timeout_s=timeout_s,
                            binary=True)

    def checked(self, *args, stdin_data=None, timeout_s=None) -> bytes:
        from moxi_publishing.content import ScanError, _first_line

        done = self.git(*args, stdin_data=stdin_data, timeout_s=timeout_s)
        if done.returncode != 0:
            raise ScanError(f"git {args[0]} failed: {_first_line(done.stderr)}")
        return done.stdout

    # -- the publishing remote ---------------------------------------------------------

    def github(self) -> str | None:
        """`GITHUB_KEY`'s value, or None when it is unset or blank; read once."""
        if not hasattr(self, "_github"):
            done = self.git("config", "--get", GITHUB_KEY)
            value = done.stdout.decode("utf-8", "surrogateescape").strip()
            self._github = value if done.returncode == 0 and value else None
        return self._github

    def to_publishing(self) -> bool:
        """Whether the repo sets a standing pattern and the push goes to the publishing
        remote: the URL reduces to `GITHUB_KEY`'s value."""
        github = self.github() if self.standing.patterns else None
        return bool(github and github_repo(self.url) == github)

    def judge_leftover(self) -> None:
        """Refuse every push while the retired `word-required` holds true."""
        if not self.word_required:
            return
        from moxi_repo.api import quote_arg

        root = quote_arg(self.ctx.root or ".")
        self.reasons += [
            f"this repo's push piece still holds {WORD_REQUIRED} = true, which is retired: "
            "the word is push approval's now, so no push goes out until it moves there",
            f"turn approval on with moxi publishing approval enable {root} --branch PATTERN "
            f"(a pattern per branch to guard; '*' is every branch), then remove the "
            f"setting with moxi publishing enable {root} --set {WORD_REQUIRED}="]

    # -- each ref line --------------------------------------------------------------------

    def chains(self) -> dict[str, list[tuple[str, bytes]]]:
        """For each local sha pushed: the objects from it down to the first one that is
        not a tag, each as (type, bytes); the last is what the ref finally names, or
        ("missing", b"") when the chain can't be followed."""
        from moxi_publishing.content import read_objects

        starts = sorted({u.local_sha for u in self.updates if not _zero(u.local_sha)})
        found = read_objects(self.git, starts)
        out: dict[str, list[tuple[str, bytes]]] = {}
        for start in starts:
            chain: list[tuple[str, bytes]] = []
            obj, seen = found.get(start), {start}
            while obj is not None and obj[0] == "tag":
                chain.append(obj)
                target = dict(_headers(obj[1])[0]).get("object", "").strip()
                if not target or target in seen:
                    obj = None
                    break
                seen.add(target)
                if target not in found:
                    found.update(read_objects(self.git, [target]))
                obj = found.get(target)
            chain.append(obj if obj is not None else ("missing", b""))
            out[start] = chain
        return out

    def present(self, shas) -> set[str]:
        from moxi_publishing.content import read_objects

        shas = [s for s in set(shas) if not _zero(s)]
        found = read_objects(self.git, shas)
        return {s for s, obj in found.items() if obj is not None}

    def is_ancestor(self, older: str, newer: str) -> bool:
        done = self.git("merge-base", "--is-ancestor", older, newer)
        if done.returncode not in (0, 1):
            from moxi_publishing.content import ScanError, _first_line

            raise ScanError(f"git merge-base failed: {_first_line(done.stderr)}")
        return done.returncode == 0

    def tracking(self, branch: str) -> str | None:
        done = self.git("rev-parse", "--verify", "--quiet", "--end-of-options",
                        f"refs/remotes/{self.remote}/{branch}^{{commit}}")
        return done.stdout.decode("ascii").strip() if done.returncode == 0 else None

    def judge_refs(self, chains) -> None:
        from moxi_publishing.flag import looks_like_release_tag

        here = self.present([u.remote_sha for u in self.updates])
        created_tags = 0
        for u in self.updates:
            ref, deleting, creating = u.remote_ref, _zero(u.local_sha), _zero(u.remote_sha)
            if not deleting:
                end = chains[u.local_sha][-1][0]
                if end != "commit":
                    self.reasons.append(f"{ref}: it would point at a {end}, not a commit "
                                        "or a tag of one; a public repo pushes no other "
                                        "object")
                    continue
            if ref.startswith(_HEADS):
                pattern = self.standing.match(ref)
                if pattern:
                    self.judge_standing(u, pattern, chains, deleting=deleting)
                else:
                    self.judge_branch(u, ref[len(_HEADS):], deleting=deleting,
                                      creating=creating, here=here)
                continue
            if ref.startswith(_TAGS):
                if creating and not deleting:
                    created_tags += 1
                if looks_like_release_tag(ref[len(_TAGS):]):
                    if creating and not deleting:
                        self.judge_release_tag(u.local_sha, ref[len(_TAGS):])
                    else:
                        self.reasons.append(f"{ref}: a release tag is never moved or "
                                            "deleted")
                    continue
            if not _free(ref, self.free):
                what = "deleted" if deleting else "created" if creating else "moved"
                self.reasons.append(
                    f"{ref}: a ref outside branches may be {what} only when the push "
                    f"piece's {FREE_REF} setting names it")
        if created_tags > MAX_TAGS:
            self.reasons.append(f"the push creates {created_tags} tags; a push creates at "
                                "most one tag, so each gets its own push event and release "
                                "run: push them one at a time")

    def judge_release_tag(self, local_sha: str, name: str) -> None:
        """A tag shaped like a release tag, created: the tag gate when the scheme reads it
        as one at its commit; refused when it doesn't, since the shape keeps it from
        `free-ref`."""
        from moxi_publishing import flag, tags

        commit, why = tags.peel(self.git, local_sha)
        parsed = None
        if commit is not None:
            try:
                parsed = tags.parse_tag(name, tags.workspace(self.git, commit).members)
            except tags.ReleaseError as exc:
                why = f"the workspace at {commit[:12]} can't be read: {exc}"
        if parsed is None:
            why = why or "it names no member and version at its commit"
            self.reasons.append(f"{_TAGS}{name}: shaped like a release tag, but {why}; a "
                                "release tag names a member and its version")
            return
        where = tags.Where(f"refs/remotes/{self.remote}/{self.main}", self.main,
                           flag.config_get(self.ctx.root, "moxi-publishing.github"))
        verdict = tags.gate(self.git, name, where=where, tag_object=local_sha)
        self.reasons += [f"{_TAGS}{name}: {line}" for line in verdict.refusals()]

    def judge_standing(self, u, pattern: str, chains, *, deleting: bool) -> None:
        """A standing branch: deleted freely; created, moved or forced only to a commit
        that is the tip of the local branch named by the rest of its name."""
        if deleting:
            return
        ref = u.remote_ref
        if chains[u.local_sha][0][0] != "commit":
            self.reasons.append(f"{ref}: {u.local_sha[:12]} is a tag object; a standing "
                                "branch takes a commit, the tip of a local branch")
            return
        local = _HEADS + ref[len(_HEADS) + len(pattern) - 1:]
        tip = self.branch_tip(local)
        if tip != u.local_sha:
            where = f"is at {tip[:12]}" if tip else "does not exist here"
            self.reasons.append(f"{ref}: a branch under {pattern} goes out only at the tip "
                                f"of {local}, which {where}, not {u.local_sha[:12]}")

    def branch_tip(self, ref: str) -> str | None:
        """The commit the local branch `ref` (`refs/heads/x`) points at, or None when there
        is no such branch. Read exactly: `rev-parse` alone would take
        `refs/tags/refs/heads/x` or `refs/remotes/refs/heads/x` when the branch is
        missing."""
        if self.git("show-ref", "--verify", "--quiet", ref).returncode != 0:
            return None
        done = self.git("rev-parse", "--verify", "--quiet", "--end-of-options",
                        f"{ref}^{{commit}}")
        return done.stdout.decode("ascii").strip() if done.returncode == 0 else None

    def judge_branch(self, u, branch: str, *, deleting: bool, creating: bool, here) -> None:
        ref = u.remote_ref
        if deleting:
            if branch == self.main or branch in self.protected:
                self.reasons.append(f"{ref}: {branch} is never deleted")
                return
            main = self.tracking(self.main)
            if u.remote_sha not in here or main is None:
                self.reasons.append(
                    f"{ref}: can't tell whether {branch} is merged into {self.main}; "
                    f"{self.fetch_fix()}")
            elif not self.is_ancestor(u.remote_sha, main):
                self.reasons.append(
                    f"{ref}: {branch} is not merged into {self.main} as last fetched; a "
                    f"branch is deleted only once merged (if it was merged on the remote "
                    f"since, {self.fetch_fix()})")
            return
        if creating:
            return
        if u.remote_sha not in here:
            self.reasons.append(f"{ref}: the remote's tip isn't here; {self.fetch_fix()}")
        elif not self.is_ancestor(u.remote_sha, u.local_sha):
            self.reasons.append(f"{ref}: the push would rewrite {branch}; a public branch "
                                "is never rewritten")

    def judge_names(self) -> None:
        from moxi_secrets import scan_lines

        from moxi_publishing.content import SETS, _masked

        names = [u.remote_ref for u in self.updates]
        hits: dict[int, list] = {}
        for hit in scan_lines([(REF_PATH, n, name) for n, name in enumerate(names, 1)],
                              SETS):
            hits.setdefault(hit.line, []).append(hit.hit)
        for n, found in sorted(hits.items()):
            shown = _masked(names[n - 1], found)
            for hit in found:
                self.reasons.append(f"the ref name {shown}: {hit.set}/{hit.rule}")

    # -- what is made public --------------------------------------------------------------

    def public(self) -> list[str] | None:
        """What the remote already has, or None after a reason was added."""
        out = self.checked("for-each-ref", "--format=%(objectname)",
                           f"refs/remotes/{self.remote}/")
        tracking = out.decode("ascii").split()
        if not tracking:
            done = self.git("ls-remote", self.url, timeout_s=LS_REMOTE_TIMEOUT_S)
            if done.returncode != 0:
                from moxi_publishing.content import _first_line

                self.reasons.append(f"can't ask {self.shown_url} for its refs: "
                                    f"{_first_line(done.stderr)}")
                return None
            if done.stdout.strip():
                self.reasons.append(
                    f"there are no tracking refs for {self.shown_remote} here, and the "
                    "remote isn't empty, so what is new to it can't be told; "
                    + self.fetch_fix())
                return None
        tips = [u.remote_sha for u in self.updates if not _zero(u.remote_sha)]
        return sorted(set(tracking) | self.present(tips))

    def sent(self, public: list[str], chains) -> list[str]:
        heads = [u.local_sha for u in self.updates
                 if not _zero(u.local_sha) and chains[u.local_sha][-1][0] == "commit"]
        if not heads:
            return []
        lines = heads + ["^" + sha for sha in public]
        out = self.checked("rev-list", "--stdin", stdin_data="\n".join(lines) + "\n")
        return out.decode("ascii").split()

    def judge_commits(self, commits: list[str]) -> None:
        if not commits:
            return
        from moxi_publishing import content, message
        from moxi_publishing.content import read_objects

        raw = read_objects(self.git, commits)
        drop = self.message_keys(message.DROP_TRAILER)
        passed = self.message_keys(message.PASS_TRAILER)
        own = _own_identity()
        from moxi_repo.api import commit_rules_problems

        for sha in commits:
            short = sha[:12]
            kind, data = raw[sha] or ("missing", b"")
            if kind != "commit":
                self.reasons.append(f"{short}: can't be read as a commit")
                continue
            headers, text = _headers(data)
            committer = _identity(dict(headers).get("committer", ""))
            self.judge_headers(short, headers, skip=("tree", "parent"))
            judged = message.judge(text, passed=passed, whole=True)
            self.reasons += [f"{short} message {line}" for _, line in judged.refusals]
            if own is None or _same(committer, own):
                for n, line in enumerate(text.split("\n"), 1):
                    key = message.trailer_key(line)
                    if key is not None and key.lower() in drop | passed:
                        self.reasons.append(
                            f"{short} message line {n}: {key} is still there, so a "
                            "commit-msg hook didn't run on this commit; reword it")
            self.reasons += [f"{short} message: {problem}"
                             for problem in commit_rules_problems(self.ctx.root, text)]
        self.judge_content(commits, content)

    def message_keys(self, setting: str) -> set[str]:
        """The trailer keys of one of the message piece's settings in this repo, read from
        the repo's git config whether or not that piece is enabled."""
        from moxi_publishing.message import PIECE_ID as MESSAGE

        done = self.git("config", "--get-all", f"moxi-repo.piece.{MESSAGE}.{setting}")
        if done.returncode not in (0, 1):
            from moxi_publishing.content import ScanError, _first_line

            raise ScanError(f"git config failed: {_first_line(done.stderr)}")
        values = done.stdout.decode("utf-8", "surrogateescape").split("\n")
        return {v.strip().lower() for v in values if v.strip()}

    def judge_content(self, commits: list[str], content) -> None:
        import re

        flags = [f for f in content.DIFF_FLAGS if f != "-z"]
        out = self.checked("log", "--no-walk=unsorted", "--stdin", "-p", "--root",
                           "--no-show-signature", "--format=format:%x00%H %P",
                           "--diff-merges=first-parent", *flags,
                           stdin_data="\n".join(commits) + "\n")
        per_commit: dict[str, tuple[list[str], list]] = {}
        for chunk in re.split(rb"(?m)^\x00", out):
            if not chunk.strip():
                continue
            first, _, patch = chunk.partition(b"\n")
            sha, *parents = first.decode("ascii").split()
            per_commit[sha] = (parents, content.parse_patch(patch))
        public, residue = content.PUBLIC_ALLOW, content.RESIDUE_ALLOW
        specs = [f"{sha}:{name}" for sha in commits for name in (public, residue)]
        blobs = content.read_blobs(self.git, specs)
        private = os.path.join(self.ctx.common_dir, content.PRIVATE_ALLOW)
        for sha in commits:
            parents, files = per_commit.get(sha, ([], []))
            lines = [(f.path, n, text) for f in files for n, text in f.lines]
            names = [f.path for f in files if f.new]
            for parent in parents[1:]:
                patch = self.checked("diff", *flags, parent, sha, "--")
                against = content.parse_patch(patch)
                added = {(f.path, n) for f in against for n, _ in f.lines}
                new = {f.path for f in against if f.new}
                lines = [t for t in lines if (t[0], t[1]) in added]
                names = [name for name in names if name in new]
            allow, pairs = content.allowlists(blobs, f"{sha}:{public}", f"{sha}:{residue}",
                                              private)
            self.reasons += [f"{sha[:12]} {line}" for line in
                             content.judge(lines, names, allow=allow, residue=pairs)]

    def judge_tags(self, chains) -> None:
        for u in self.updates:
            if _zero(u.local_sha):
                continue
            for kind, data in chains[u.local_sha]:
                if kind == "tag":
                    self.judge_tag(f"tag {u.remote_ref}", data)

    # -- main moves only to a commit CI passed on a standing branch ---------------------

    def judge_main(self) -> None:
        """`main_ci` for each ref line that creates or moves `main`, only where
        `to_publishing` holds."""
        if not self.to_publishing():
            return
        for u in self.updates:
            if u.remote_ref == _HEADS + self.main and not _zero(u.local_sha):
                self.reasons += main_ci(self.git, u.local_sha, main=self.main,
                                        patterns=self.standing.patterns,
                                        github=self.github(), root=self.ctx.root,
                                        remote=self.shown_remote)

    def run(self) -> None:
        if self.standing.problems:
            from moxi_repo.api import quote_arg

            root = quote_arg(self.ctx.root or ".")
            self.reasons += [
                *self.standing.problems,
                f"no push goes out while {STANDING_BRANCH} holds a value that is not a "
                f"good pattern (a fixed prefix ending in / then *, matching neither "
                f"{self.main} nor a protected branch); fix it with moxi publishing enable "
                f"{root} --set '{STANDING_BRANCH}=ci/*', or remove it with --set "
                f"{STANDING_BRANCH}="]
            return
        self.judge_leftover()
        if self.reasons:
            return
        self.judge_names()
        chains = self.chains()
        self.judge_refs(chains)
        self.judge_main()
        public = self.public()
        if public is None:
            return
        self.commits = self.sent(public, chains)
        self.judge_commits(self.commits)
        self.judge_tags(chains)

    def record(self) -> dict:
        import datetime

        return {"time": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
                "repo": self.ctx.root, "remote": self.shown_remote, "url": self.shown_url,
                "word": self.word, "refs": [self.ref_entry(u) for u in self.updates],
                "commits": self.commits}

    def ref_entry(self, u) -> dict:
        entry = {"local_ref": u.local_ref, "local_sha": u.local_sha,
                 "remote_ref": u.remote_ref, "remote_sha": u.remote_sha}
        pattern = self.standing.match(u.remote_ref)
        if pattern:
            entry["standing"] = pattern
        return entry


def main_ci(git, commit: str, *, main: str, patterns, github: str | None,
            root: str | None = None, remote: str = "origin", web=None) -> list[str]:
    """Why `main` can't move to `commit`, or nothing: it moves only when GitHub has, at
    `commit`, a run of the root `pyproject.toml`'s `check-workflow` (read at `commit`) from
    a push to a branch a standing pattern in `patterns` matches, whose `check-job` (read
    at `commit`) concluded `success` in the run's latest attempt. `git(*args)` runs git
    with bytes on stdout; `web` is a `tags.Web`, by default the tag gate's. Each read that
    fails, a missing setting, `github` None and no pattern refuse, naming which."""
    from urllib.parse import quote

    from moxi_publishing import tags

    label, short = f"{_HEADS}{main}", commit[:12]
    if not patterns:
        fix = f"moxi publishing enable {root} --set '{STANDING_BRANCH}=ci/*'" if root \
            else f"set {STANDING_BRANCH}"
        return [f"{label}: moves only to a commit whose check passed on a standing branch, "
                f"and the push piece's {STANDING_BRANCH} setting names none; {fix}"]
    try:
        table = tags.release_table(git, commit)
    except tags.ReleaseError as exc:
        return [f"{label}: the check {main} waits for can't be read at {short}: {exc}"]
    workflow, job = table.get(tags.CHECK_WORKFLOW), table.get(tags.CHECK_JOB)
    for key, value in ((tags.CHECK_WORKFLOW, workflow), (tags.CHECK_JOB, job)):
        if not isinstance(value, str) or not value.strip():
            return [f"{label}: [tool.{tags.TABLE}] at {short} has no {key}, so nothing "
                    f"says which check {main} waits for"]
    if not github:
        return [f"{label}: {GITHUB_KEY} is unset, so GitHub can't be asked whether "
                f"{job} passed at {short}"]
    standing = Standing({STANDING_BRANCH: list(patterns), MAIN: [main]})
    web = web if web is not None else tags.default_web()
    try:
        runs = web.github(f"/repos/{github}/actions/workflows/{quote(workflow)}/runs"
                          f"?head_sha={commit}&event=push&per_page=100",
                          f"{workflow}'s runs at {short}")
        for run in (runs or {}).get("workflow_runs", []) if isinstance(runs, dict) else []:
            if not isinstance(run, dict) or run.get("head_sha") != commit or run.get(
                    "event") != "push" or not isinstance(run.get("head_branch"), str) \
                    or not standing.match(_HEADS + run["head_branch"]):
                continue
            # The jobs list's default filter is `latest`: the jobs of the run's latest
            # attempt, so a re-run reads as its last attempt went.
            jobs = web.github(f"/repos/{github}/actions/runs/{run.get('id')}/jobs"
                              f"?per_page=100", f"the jobs of run {run.get('id')}")
            for one in (jobs or {}).get("jobs", []) if isinstance(jobs, dict) else []:
                name = one.get("name", "") if isinstance(one, dict) else ""
                if (name == job or name.startswith(job + " (")) and one.get(
                        "conclusion") == "success":
                    return []
    except tags.ReleaseError as exc:
        return [f"{label}: {exc}, so whether {job} passed at {short} can't be told"]
    first = standing.patterns[0][:-1] + main
    return [f"{label}: no {workflow} run from a push to {' or '.join(standing.patterns)} "
            f"at {short} has {job} concluded success, and {main} moves only to a commit "
            f"whose {job} passed there. Push the commit to a standing branch first (git "
            f"push {remote} +{main}:refs/heads/{first}) and push {main} once {job} passed "
            f"there, or move {main} to an earlier commit that passed (git push {remote} "
            f"<commit>:refs/heads/{main})"]


def _own_identity() -> tuple[str, str] | None:
    """secrets' `author.name` and `author.email`, or None when both are empty: then every
    commit counts as the repo's own."""
    from moxi_secrets import private_names

    names = private_names()
    if not names.author_name.strip() and not names.author_email.strip():
        return None
    return names.author_name.strip(), names.author_email.strip()


def _same(identity: tuple[str, str], own: tuple[str, str]) -> bool:
    """Whether a header's (name, email) is the repo's own identity: by email when secrets
    has one, else by name, ignoring case."""
    if own[1]:
        return identity[1].casefold() == own[1].casefold()
    return identity[0].casefold() == own[0].casefold()


def log_path():
    """The push log."""
    from moxi.paths import Layout

    return Layout("publishing").state_dir() / LOG_FILE


def _log(record: dict) -> None:
    from moxi.files import append_record

    # No size limit: a first push may send many commits, and a line too long for a limit
    # would refuse a push that passed.
    append_record(log_path(), record, max_bytes=None, durable=True)


def pre_push(ctx):
    """The piece."""
    from moxi_repo.api import Outcome

    from moxi_publishing.message import is_public, no_flag

    if not is_public(ctx):
        return no_flag(ctx, PIECE_ID)
    push = _Push(ctx)
    try:
        push.run()
    except Exception as exc:  # noqa: BLE001 - every failure to judge refuses, named
        return Outcome.refuse(f"this push can't be checked, so it is refused: {exc}")
    if push.reasons:
        return Outcome.refuse("this repo is public, and this push can't go:",
                              *push.reasons, "Nothing was pushed.")
    try:
        _log(push.record())
    except (OSError, ValueError) as exc:
        return Outcome.refuse(f"the push log can't be written, so the push is refused: "
                              f"{exc}")
    return Outcome.ok()


__all__ = ["PUSH", "PIECE_ID", "MAIN", "PROTECTED", "WORD_REQUIRED", "FREE_REF",
           "STANDING_BRANCH", "GITHUB_KEY", "WORD_ENV", "MAX_TAGS", "LOG_FILE", "Standing",
           "given_word",
           "github_repo", "standing_problem", "main_ci", "log_path", "judge_history",
           "pre_push"]
