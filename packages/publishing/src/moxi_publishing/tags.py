"""Release tags, the not-ready list and the tag gate.

A release tag is `<distribution>-v<version>`, such as `moxi-v0.1.7` or
`moxi-comms-v0.3.1`. It is read against the workspace members at the commit it names: the
distribution is the member whose name, plus `-v`, is the tag's longest matching prefix,
and the rest must be a version in its normalised spelling (`0.1.7`, never `0.1.07`). A tag
that names no member or no version is not a release tag.

A version is *published* when the package index has a file of it that isn't yanked, and
*released* when it is published and the repo has its release tag.

The repo's release settings sit in its root `pyproject.toml`, so the workflow reads the
same values the local gate does:

    [tool.moxi-publishing]
    not-ready = ["moxi", "moxi-cache"]   # never released from a commit that lists them
    ready = ["moxi-comms"]               # confirmed; optional, see below
    check-workflow = "ci.yml"            # the workflow file of the check job
    check-job = "linux"                  # the job that must pass on main at the commit

When the table holds `ready`, every member must be on exactly one of the two lists, so a
member added later is never ready by default: one on neither or on both counts as not
ready, and `moxi-dev check` refuses it. Without `ready`, only `not-ready` counts.

`gate` decides whether a tag may release. `moxi publishing tag`, the push piece and
`moxi publishing ci gate` all call it; only where the main ref comes from differs. Its
seven checks, for a tag on commit C naming distribution D and version V:

1. shape: the tag is annotated and peels, through tag objects only, to C; its name is
   `D-vV` with V normalised; D is a member at C, and V is D's `project.version` at C;
2. CHANGELOG: D's CHANGELOG at C has `## V (YYYY-MM-DD)` as its first version heading;
3. main and green: C is on the first-parent line of the main ref, and GitHub has a
   successful run of `check-job` in `check-workflow`, from a push to the main branch, at C;
4. ready: D is not on the not-ready list at C, and, where the table keeps a ready list,
   D is on that list;
5. siblings: each required dependency of D on another member admits at least one
   published version of it (an extra's are reported, not checked);
6. new: the package index has no file of D at V; or, given the build's files, every file
   it holds at V is one of them, byte for byte (a release that stopped halfway goes on,
   and publishing skips what the index has);
7. catalogue, for the core only: every entry of the core's catalogue at C has a released
   version or says `unreleased = true`.

Checks 3, 5, 6 and 7 read GitHub's API and the package index, each read bounded at ten
seconds; a read that fails refuses, naming it. GitHub is read with the token `gh auth
token` gives, where there is one, and only at api.github.com.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

#: The core's distribution name; its package holds the catalogue.
CORE = "moxi"
#: The catalogue's place in the core's directory.
CATALOGUE = "src/moxi/catalogue.toml"
CHANGELOG = "CHANGELOG.md"
#: What joins a distribution and a version in a release tag.
SEPARATOR = "-v"
#: The root `pyproject.toml`'s table of release settings, and its keys.
TABLE = "moxi-publishing"
NOT_READY = "not-ready"
READY = "ready"
CHECK_WORKFLOW = "check-workflow"
CHECK_JOB = "check-job"
#: Where GitHub's API and the package index are read; tests point these at a fake.
GITHUB_API_ENV = "MOXI_PUBLISHING_GITHUB_API_URL"
PYPI_ENV = "MOXI_PUBLISHING_PYPI_URL"
GITHUB_API = "https://api.github.com"
PYPI = "https://pypi.org"
#: Each read's bound, and the most one answer may hold.
READ_TIMEOUT_S = 10.0
READ_CAP_BYTES = 16 * 1024 * 1024
#: The gate's checks, by number.
CHECKS = {1: "shape", 2: "changelog", 3: "main and green", 4: "ready", 5: "siblings",
          6: "new", 7: "catalogue"}


class ReleaseError(Exception):
    """A release file or setting that can't be read, or a read of GitHub or the index that
    failed. Its text says which."""


# -- the scheme -----------------------------------------------------------------------------

def normalised(text: str) -> str | None:
    """`text` when it is a version in its normalised spelling, else None."""
    from packaging.version import InvalidVersion, Version

    try:
        return text if str(Version(text)) == text else None
    except InvalidVersion:
        return None


def tag_name(dist: str, version: str) -> str:
    """The release tag of `dist` at `version`."""
    return f"{dist}{SEPARATOR}{version}"


def parse_tag(name: str, dists) -> tuple[str, str] | None:
    """(distribution, version) that tag `name` (without `refs/tags/`) releases, read
    against the distribution names `dists`; None when it is no release tag."""
    best = None
    for dist in dists:
        if name.startswith(dist + SEPARATOR) and (best is None or len(dist) > len(best)):
            best = dist
    if best is None:
        return None
    version = normalised(name[len(best) + len(SEPARATOR):])
    return None if version is None else (best, version)


# -- CHANGELOG headings ---------------------------------------------------------------------

#: A heading whose text starts with a version, such as `## 0.1.4 (2026-11-08)` or
#: `## [0.1.4]`.
_VERSION_HEADING = re.compile(r"^(#{1,6})[ \t]+\[?v?(\d+(?:\.\d+)*[^\s\]()]*)")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_DATED = re.compile(r"^## (\S+) \((\d{4}-\d{2}-\d{2})\)[ \t]*$")
_HEADING = re.compile(r"^(#{1,6})[ \t]")


@dataclass(frozen=True)
class Heading:
    """A CHANGELOG heading that starts with a version."""

    version: str    # as the heading spells it
    line: str       # the whole line
    number: int     # its line number, from 1
    level: int      # how many `#` it has

    @property
    def date(self) -> str | None:
        """The date when the heading is `## V (YYYY-MM-DD)`, else None."""
        match = _DATED.match(self.line)
        return match.group(2) if match and match.group(1) == self.version else None


def version_headings(text: str):
    """Each heading of Markdown `text` that starts with a version, outside fenced code."""
    fence = False
    for number, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            fence = not fence
            continue
        match = None if fence else _VERSION_HEADING.match(line)
        if match:
            yield Heading(match.group(2), line, number, len(match.group(1)))


def first_version_heading(text: str) -> Heading | None:
    """The first heading of `text` that starts with a version, or None."""
    return next(version_headings(text), None)


def same_version(a: str, b: str) -> bool:
    from packaging.version import InvalidVersion, Version

    try:
        return Version(a) == Version(b)
    except InvalidVersion:
        return False


def changelog_section(text: str, version: str) -> str | None:
    """The body of the CHANGELOG section headed with `version`, up to the next heading of
    its level or above, without blank lines at either end; None when no heading names
    `version`."""
    lines = text.splitlines()
    for heading in version_headings(text):
        if not same_version(heading.version, version):
            continue
        body = []
        fence = False
        for line in lines[heading.number:]:
            if _FENCE.match(line):
                fence = not fence
            match = None if fence else _HEADING.match(line)
            if match and len(match.group(1)) <= heading.level:
                break
            body.append(line)
        return "\n".join(body).strip("\n")
    return None


# -- the workspace at a commit --------------------------------------------------------------

@dataclass(frozen=True)
class Member:
    path: str               # from the root, `.` for a repo whose root is its one package
    dist: str               # its project name, canonical
    version: str | None     # its `project.version`, as written
    project: dict           # its [project] table

    def file(self, name: str) -> str:
        import posixpath

        return posixpath.normpath(posixpath.join(self.path, name))


@dataclass
class Workspace:
    """The members and release settings of the repo at one commit."""

    commit: str
    members: dict[str, Member]          # by distribution
    table: dict = field(default_factory=dict)

    def _listed(self, key: str) -> set[str] | None:
        from packaging.utils import canonicalize_name

        if key not in self.table:
            return None
        listed = self.table[key]
        if not isinstance(listed, list) or not all(isinstance(x, str) for x in listed):
            raise ReleaseError(f"[tool.{TABLE}] {key} is not a list of names")
        return {canonicalize_name(x) for x in listed}

    def not_ready(self) -> set[str]:
        """The distributions on the not-ready list. Raises `ReleaseError`."""
        return self._listed(NOT_READY) or set()

    def unlisted(self) -> dict[str, str]:
        """{distribution: what is wrong} for each member on neither or both of the
        not-ready and ready lists. A repo that keeps no ready list names none, so there
        only the not-ready list counts. Raises `ReleaseError`."""
        ready = self._listed(READY)
        if ready is None:
            return {}
        not_ready = self.not_ready()
        found = {}
        for dist in sorted(self.members):
            if dist in ready and dist in not_ready:
                found[dist] = f"on both [tool.{TABLE}] {NOT_READY} and {READY}"
            elif dist not in ready and dist not in not_ready:
                found[dist] = f"on neither [tool.{TABLE}] {NOT_READY} nor {READY}"
        return found

    def setting(self, key: str) -> str | None:
        value = self.table.get(key)
        return value if isinstance(value, str) and value.strip() else None


def repo_git(root: str):
    """A git runner for the repo at `root`, as `content.read_objects` takes one. It reads
    with replace refs off, as the pieces do."""
    from moxi_repo.api import git

    from moxi_publishing.content import NO_REPLACE

    def run(*args, stdin_data=None, timeout_s=None):
        extra = {} if timeout_s is None else {"timeout_s": timeout_s}
        return git(root, NO_REPLACE, *args, stdin_data=stdin_data, binary=True, **extra)

    return run


def _toml(data: bytes | None, where: str) -> dict:
    import tomllib

    if data is None:
        raise ReleaseError(f"{where} is missing")
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseError(f"{where} can't be read: {exc}") from None


def _table(data: dict, *keys: str) -> dict:
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data if isinstance(data, dict) else {}


def read_file(git, commit: str, path: str) -> bytes | None:
    """The file at `path` in `commit`, or None. Raises `ReleaseError`."""
    from moxi_publishing.content import ScanError, read_blobs

    spec = f"{commit}:{path}"
    try:
        return read_blobs(git, [spec])[spec]
    except ScanError as exc:
        raise ReleaseError(str(exc)) from None


def workspace(git, commit: str) -> Workspace:
    """The workspace members and the release settings at `commit`. A root with no
    `[tool.uv.workspace]` is its own one member. Raises `ReleaseError`."""
    from moxi_publishing.content import ScanError, read_blobs

    def read(paths):
        specs = {p: f"{commit}:{p}" for p in paths}
        try:
            blobs = read_blobs(git, specs.values())
        except ScanError as exc:
            raise ReleaseError(str(exc)) from None
        return {p: blobs[spec] for p, spec in specs.items()}

    def dirs():
        done = git("ls-tree", "-r", "-d", "-z", "--name-only", commit)
        return [d.decode("utf-8", "surrogateescape") for d in done.stdout.split(b"\0") if d]

    return _workspace(read, dirs, commit, f"{commit[:12]}:")


def work_tree_workspace(root: str) -> Workspace:
    """The workspace members and release settings as the files in the work tree at
    `root` have them. Raises `ReleaseError`."""
    def read(paths):
        out = {}
        for path in paths:
            try:
                with open(os.path.join(root, path), "rb") as fh:
                    out[path] = fh.read()
            except FileNotFoundError:
                out[path] = None
            except OSError as exc:
                raise ReleaseError(f"{path} can't be read: {exc}") from None
        return out

    def dirs():
        found = []
        for top, subdirs, _files in os.walk(root):
            subdirs[:] = [d for d in subdirs if not d.startswith(".")]
            rel = os.path.relpath(top, root)
            if rel != ".":
                found.append(rel.replace(os.sep, "/"))
        return found

    return _workspace(read, dirs, "", "")


def _workspace(read, dirs, commit: str, label: str) -> Workspace:
    from fnmatch import fnmatchcase

    from packaging.utils import canonicalize_name

    root = _toml(read(["pyproject.toml"])["pyproject.toml"], f"{label}pyproject.toml")
    table = _table(root, "tool", TABLE)
    names = _table(root, "tool", "uv", "workspace").get("members")
    globbed = False
    if names is None:
        paths = ["."]
    elif not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ReleaseError("tool.uv.workspace.members is not a list of paths")
    else:
        paths = [n.strip("/") for n in names]
        globbed = any(ch in n for n in paths for ch in "*?[")
    if globbed:
        found = dirs()
        paths = [d for p in paths for d in (sorted(d for d in found if fnmatchcase(d, p))
                                            if any(ch in p for ch in "*?[") else [p])]
    files = {p: "pyproject.toml" if p == "." else p + "/pyproject.toml" for p in paths}
    blobs = read(files.values())
    members: dict[str, Member] = {}
    for path, file in files.items():
        if blobs[file] is None and globbed:
            continue  # a glob may match a directory that is no package
        project = _table(_toml(blobs[file], label + file), "project")
        dist = project.get("name")
        if not isinstance(dist, str):
            raise ReleaseError(f"{label}{file} names no project")
        version = project.get("version")
        members[canonicalize_name(dist)] = Member(
            path, canonicalize_name(dist), version if isinstance(version, str) else None,
            project)
    return Workspace(commit, members, table)


# -- GitHub and the package index -----------------------------------------------------------

def index_from_environment() -> str | None:
    """The package index the environment names in place of pypi.org, or None."""
    return os.environ.get(PYPI_ENV) or None


class Web:
    """Reads JSON from GitHub's API and the package index's JSON API."""

    def __init__(self, *, github_api: str | None = None, pypi: str | None = None,
                 token: str | None = None, timeout_s: float = READ_TIMEOUT_S):
        self.github_api = (github_api or os.environ.get(GITHUB_API_ENV) or GITHUB_API
                           ).rstrip("/")
        self.pypi_url = (pypi or os.environ.get(PYPI_ENV) or PYPI).rstrip("/")
        self.token = token
        self.timeout_s = timeout_s

    def fetch(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        """(status, body) of a GET. Raises `OSError` when nothing came back."""
        import urllib.error
        import urllib.request

        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as answer:
                return answer.status, answer.read(READ_CAP_BYTES + 1)
        except urllib.error.HTTPError as exc:
            return exc.code, b""

    def _json(self, url: str, headers: dict[str, str], what: str):
        import json

        try:
            status, body = self.fetch(url, headers)
        except (OSError, ValueError) as exc:
            raise ReleaseError(f"{what} can't be read: {exc}") from None
        if status == 404:
            return None
        if status != 200:
            raise ReleaseError(f"{what} can't be read: HTTP {status}")
        if len(body) > READ_CAP_BYTES:
            raise ReleaseError(f"{what} is larger than {READ_CAP_BYTES} bytes")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise ReleaseError(f"{what} is not JSON: {exc}") from None

    def github(self, path: str, what: str):
        """GitHub's answer at `path` (from `/repos/...`), or None for a 404."""
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "moxi-publishing"}
        if self.token and self.github_api == GITHUB_API:
            headers["Authorization"] = f"Bearer {self.token}"
        return self._json(self.github_api + path, headers, what)

    def pypi(self, path: str, what: str):
        """The index's JSON answer at `path` (from `/pypi/...`), or None for a 404."""
        return self._json(self.pypi_url + path, {"Accept": "application/json",
                                                 "User-Agent": "moxi-publishing"}, what)

    def published(self, dist: str) -> list[str]:
        """The versions of `dist` the index has a file of that isn't yanked."""
        data = self.pypi(f"/pypi/{dist}/json", f"the index's page of {dist}")
        if data is None:
            return []
        releases = data.get("releases") if isinstance(data, dict) else None
        if not isinstance(releases, dict):
            raise ReleaseError(f"the index's page of {dist} lists no releases")
        return [v for v, files in releases.items() if isinstance(files, list) and any(
            isinstance(f, dict) and not f.get("yanked") for f in files)]

    def tags(self, github: str, prefix: str) -> list[str]:
        """The repo's tag names that start with `prefix`."""
        data = self.github(f"/repos/{github}/git/matching-refs/tags/{prefix}",
                           f"{github}'s tags starting {prefix}")
        if data is None:
            return []
        if not isinstance(data, list):
            raise ReleaseError(f"{github}'s tags starting {prefix} are not a list")
        return [r["ref"].removeprefix("refs/tags/") for r in data
                if isinstance(r, dict) and isinstance(r.get("ref"), str)]


def gh_token() -> str | None:
    """The token `gh auth token` prints, or None when there is no `gh` or no sign-in."""
    import shutil
    import subprocess

    from moxi import proc

    if shutil.which("gh") is None:
        return None
    try:
        done = proc.run(["gh", "auth", "token"], timeout_s=READ_TIMEOUT_S)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    token = (done.stdout or b"").decode("utf-8", "replace").strip()
    return token if done.returncode == 0 and token else None


def default_web() -> Web:
    """GitHub and the index where the environment says, with `gh`'s token for GitHub."""
    github_api = os.environ.get(GITHUB_API_ENV) or GITHUB_API
    return Web(github_api=github_api,
               token=gh_token() if github_api.rstrip("/") == GITHUB_API else None)


# -- the gate -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    number: int
    passed: bool
    said: str

    def __str__(self) -> str:
        return (f"check {self.number} ({CHECKS[self.number]}): "
                f"{'ok' if self.passed else 'refused'}: {self.said}")


@dataclass
class Verdict:
    """What the gate found for one tag."""

    tag: str
    commit: str | None = None
    dist: str | None = None
    version: str | None = None
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.checks) == len(CHECKS) and all(c.passed for c in self.checks)

    def refusals(self) -> list[str]:
        return [str(c) for c in self.checks if not c.passed]

    def lines(self) -> list[str]:
        return [str(c) for c in self.checks] + [f"note: {n}" for n in self.notes]


@dataclass
class Where:
    """Where the gate reads the main line and GitHub, which differs by caller."""

    main_ref: str               # the local ref whose first-parent line C must be on
    main_branch: str            # the branch GitHub's check run must come from
    github: str | None          # OWNER/REPO


def peel(git, obj: str) -> tuple[str | None, str]:
    """(the commit `obj` peels to through tag objects only, or None; why not)."""
    from moxi_publishing.content import ScanError, read_objects

    seen = set()
    first = True
    while obj not in seen:
        seen.add(obj)
        try:
            found = read_objects(git, [obj])[obj]
        except ScanError as exc:
            return None, str(exc)
        if found is None:
            return None, f"{obj[:12]} is not here"
        kind, data = found
        if kind == "commit":
            return (None, "it is a lightweight tag; a release tag is annotated") if first \
                else (obj, "")
        if kind != "tag":
            return None, f"it peels to a {kind}, not a commit"
        first = False
        head = data.split(b"\n\n", 1)[0].decode("utf-8", "surrogateescape")
        obj = next((line[7:].strip() for line in head.split("\n")
                    if line.startswith("object ")), "")
    return None, "its tag chain loops"


def resolve(git, rev: str) -> str | None:
    """The commit `rev` names, or None."""
    done = git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}")
    return done.stdout.decode("ascii").strip() if done.returncode == 0 else None


def gate(git, tag: str, *, where: Where, tag_object: str | None = None,
         commit: str | None = None, web: Web | None = None,
         artifact: str | None = None, files_checked_later: bool = False) -> Verdict:
    """The gate for release tag `tag`. With `tag_object` the tag exists and check 1 reads
    it; without, the tag is about to be made at `commit`. `artifact` is a directory of
    the build's files, which check 6 compares with what the index holds;
    `files_checked_later` lets check 6 pass a version the index already holds files of,
    for a caller that runs the gate again with the files before publishing. A read that
    fails refuses its check."""
    verdict = Verdict(tag)
    if tag_object is not None:
        commit, why = peel(git, tag_object)
        if commit is None:
            verdict.checks.append(Check(1, False, f"{tag}: {why}"))
            return verdict
    verdict.commit = commit
    try:
        ws = workspace(git, commit)
    except ReleaseError as exc:
        verdict.checks.append(Check(1, False, f"the workspace at {commit[:12]} can't be "
                                              f"read: {exc}"))
        return verdict
    parsed = parse_tag(tag, ws.members)
    if parsed is None:
        verdict.checks.append(Check(1, False, f"{tag} names no member and version at "
                                              f"{commit[:12]}"))
        return verdict
    dist, version = verdict.dist, verdict.version = parsed
    member = ws.members[dist]
    if member.version is None or not same_version(member.version, version):
        verdict.checks.append(Check(1, False, f"{dist}'s project.version at {commit[:12]} "
                                              f"is {member.version}, not {version}"))
    else:
        verdict.checks.append(Check(1, True, f"{tag} releases {dist} {version} from "
                                             f"{commit[:12]}"))
    web = web if web is not None else default_web()
    for number, run in ((2, _changelog), (3, _main), (4, _ready), (5, _siblings),
                        (6, _new), (7, _catalogue)):
        try:
            passed, said = run(git=git, ws=ws, member=member, version=version, where=where,
                               web=web, verdict=verdict, artifact=artifact,
                               later=files_checked_later)
        except ReleaseError as exc:
            passed, said = False, str(exc)
        verdict.checks.append(Check(number, passed, said))
    return verdict


def _changelog(*, git, ws, member, version, **_):
    path = member.file(CHANGELOG)
    data = read_file(git, ws.commit, path)
    if data is None:
        return False, f"{path} is missing"
    heading = first_version_heading(data.decode("utf-8", "replace"))
    want = f"## {version} (YYYY-MM-DD)"
    if heading is None:
        return False, f"{path} has no version heading; head the release {want}"
    if not same_version(heading.version, version) or heading.date is None:
        return False, (f"{path}'s first version heading is {heading.line.strip()!r}; it "
                       f"must be {want}, dated (moxi publishing bump writes it)")
    return True, f"{path} is headed {heading.line.strip()}"


def _main(*, git, ws, where, web, **_):
    done = git("rev-list", "--first-parent", where.main_ref, "--")
    if done.returncode != 0:
        return False, f"{where.main_ref} can't be read; fetch first"
    if ws.commit not in done.stdout.decode("ascii").split():
        return False, (f"{ws.commit[:12]} is not on {where.main_ref}'s first-parent line; "
                       "a release tag goes on a commit of main itself")
    workflow, job = ws.setting(CHECK_WORKFLOW), ws.setting(CHECK_JOB)
    for key, value in ((CHECK_WORKFLOW, workflow), (CHECK_JOB, job)):
        if value is None:
            return False, f"[tool.{TABLE}] has no {key}, so nothing says which check to wait for"
    if not where.github:
        return False, "no GitHub repo is set (moxi-publishing.github); enable with --github"
    from urllib.parse import quote

    runs = web.github(f"/repos/{where.github}/actions/workflows/{quote(workflow)}/runs"
                      f"?head_sha={ws.commit}&event=push&branch={quote(where.main_branch)}"
                      f"&per_page=100", f"{workflow}'s runs at {ws.commit[:12]}")
    for run in (runs or {}).get("workflow_runs", []) if isinstance(runs, dict) else []:
        if not isinstance(run, dict) or run.get("head_sha") != ws.commit or run.get(
                "event") != "push" or run.get("head_branch") != where.main_branch:
            continue
        jobs = web.github(f"/repos/{where.github}/actions/runs/{run.get('id')}/jobs"
                          f"?per_page=100", f"the jobs of run {run.get('id')}")
        for one in (jobs or {}).get("jobs", []) if isinstance(jobs, dict) else []:
            name = one.get("name", "") if isinstance(one, dict) else ""
            if (name == job or name.startswith(job + " (")) and one.get(
                    "conclusion") == "success":
                return True, (f"{ws.commit[:12]} is on {where.main_ref}, and {workflow}'s "
                              f"{job} passed there on a push to {where.main_branch}")
    return False, (f"{workflow}'s {job} has no successful run from a push to "
                   f"{where.main_branch} at {ws.commit[:12]}; wait for it, or push main")


def _ready(*, ws, member, **_):
    wrong = ws.unlisted().get(member.dist)
    if wrong is not None:
        return False, (f"{member.dist} is {wrong} at {ws.commit[:12]}, so it counts as "
                       f"not ready; put it on {NOT_READY} until it is confirmed")
    if member.dist in ws.not_ready():
        return False, (f"{member.dist} is on [tool.{TABLE}] {NOT_READY} at "
                       f"{ws.commit[:12]}; it leaves the list once confirmed")
    return True, f"{member.dist} is not on the not-ready list"


def _member_requirements(ws, requirements):
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    out = []
    for text in requirements if isinstance(requirements, list) else []:
        try:
            req = Requirement(text)
        except (InvalidRequirement, TypeError):
            raise ReleaseError(f"requirement {text!r} can't be read") from None
        if canonicalize_name(req.name) in ws.members:
            out.append(req)
    return out


def admitted(req, published) -> list[str]:
    """The published versions `req`'s specifier admits, pre-releases included."""
    return list(req.specifier.filter(published, prereleases=True))


def _siblings(*, ws, member, web, verdict, **_):
    from packaging.utils import canonicalize_name

    missing = []
    seen = []
    for req in _member_requirements(ws, member.project.get("dependencies", [])):
        name = canonicalize_name(req.name)
        if not admitted(req, web.published(name)):
            missing.append(f"{name}{req.specifier}")
        seen.append(name)
    extras = member.project.get("optional-dependencies", {})
    for extra, reqs in sorted(extras.items()) if isinstance(extras, dict) else []:
        for req in _member_requirements(ws, reqs):
            name = canonicalize_name(req.name)
            if not admitted(req, web.published(name)):
                verdict.notes.append(f"extra {extra}: no published {name}{req.specifier}; "
                                     "it is left out of the release tests")
    if missing:
        return False, ("no published version admits " + ", ".join(missing)
                       + "; release those first")
    return True, (f"every sibling {member.dist} requires has a published version it admits"
                  + (f" ({', '.join(seen)})" if seen else " (it requires none)"))


def file_hashes(directory: str) -> dict[str, str]:
    """{sha256: file name} of every file in `directory`."""
    import hashlib

    out = {}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            digest = hashlib.sha256()
            with open(path, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(block)
            out[digest.hexdigest()] = name
    return out


def _new(*, member, version, web, artifact, later, **_):
    data = web.pypi(f"/pypi/{member.dist}/{version}/json",
                    f"the index's page of {member.dist} {version}")
    if data is None:
        return True, f"the index has no file of {member.dist} {version}"
    files = data.get("urls") if isinstance(data, dict) else None
    if not isinstance(files, list):
        raise ReleaseError(f"the index's page of {member.dist} {version} lists no files")
    held = {(f.get("filename"), (f.get("digests") or {}).get("sha256"))
            for f in files if isinstance(f, dict)}
    if artifact is not None:
        ours = file_hashes(artifact)
        foreign = sorted(str(name) for name, digest in held if digest not in ours)
        if foreign:
            return False, (f"the index holds {', '.join(foreign)} of {member.dist} "
                           f"{version}, which this build didn't make; the version is used "
                           "up, so release the next one")
        return True, (f"the index holds {len(held)} of this build's {len(ours)} file(s) of "
                      f"{member.dist} {version}; publishing skips those")
    if later:
        return True, (f"the index holds {len(held)} file(s) of {member.dist} {version}; "
                      "they are compared with the build's files before publishing")
    return False, (f"the index already has {member.dist} {version}; the version is used "
                   "up, so release the next one")


def released(web: Web, github: str, dist: str, dists) -> list[str]:
    """The released versions of `dist`: published, with a release tag on GitHub."""
    tagged = set()
    for name in web.tags(github, tag_name(dist, "")):
        parsed = parse_tag(name, dists)
        if parsed is not None and parsed[0] == dist:
            tagged.add(parsed[1])
    return [v for v in web.published(dist) if normalised(v) in tagged or v in tagged]


def _catalogue(*, git, ws, member, where, web, **_):
    import tomllib

    if member.dist != CORE:
        return True, f"{member.dist} is not the core, which alone holds the catalogue"
    path = member.file(CATALOGUE)
    data = read_file(git, ws.commit, path)
    if data is None:
        return False, f"{path} is missing"
    try:
        rows = tomllib.loads(data.decode("utf-8")).get("module", [])
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseError(f"{path} can't be read: {exc}") from None
    if not where.github:
        return False, "no GitHub repo is set (moxi-publishing.github); enable with --github"
    unreleased = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("unreleased") is True:
            continue
        dist = str(row.get("distribution"))
        if not released(web, where.github, dist, ws.members):
            unreleased.append(dist)
    if unreleased:
        return False, (f"{path} lists {', '.join(unreleased)} with no released version; "
                       "mark each unreleased = true, or release it first")
    return True, f"every entry of {path} has a released version or is marked unreleased"


__all__ = ["CORE", "CATALOGUE", "CHANGELOG", "SEPARATOR", "TABLE", "NOT_READY",
           "CHECK_WORKFLOW", "CHECK_JOB", "GITHUB_API_ENV", "PYPI_ENV", "READY",
           "index_from_environment", "READ_TIMEOUT_S",
           "CHECKS", "ReleaseError", "normalised", "tag_name", "parse_tag", "Heading",
           "version_headings", "first_version_heading", "same_version",
           "changelog_section", "Member", "Workspace", "repo_git", "read_file",
           "workspace", "work_tree_workspace", "Web", "gh_token", "default_web", "Check", "Verdict", "Where",
           "peel", "resolve", "gate", "admitted", "file_hashes", "released"]
