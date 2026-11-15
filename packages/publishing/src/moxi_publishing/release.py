"""Releasing a package: `moxi publishing bump`, `tag` and the `ci` steps.

`tag` runs the tag gate (`moxi_publishing.tags.gate`) at a commit of main and makes the
package's annotated release tag only when every check passes; it pushes nothing. The
`ci` steps are the release workflow's: `ci gate` runs the gate on a pushed tag, `ci
notes` prints the release's CHANGELOG section, and `ci siblings` tests the built package
against the sibling versions the package index has.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from moxi_publishing.flag import Failure

#: The remote whose tracking refs hold main, unless `--remote` names another.
REMOTE = "origin"
#: The push piece's setting that names the main branch.
MAIN_KEY = "moxi-repo.piece.publishing/push.main"
GITHUB_KEY = "moxi-publishing.github"


@dataclass
class Result:
    """What a release command did: lines for stdout, and its exit code."""

    lines: list[str] = field(default_factory=list)
    code: int = 0


def main_branch(root: str) -> str:
    """The repo's main branch: the push piece's `main` setting, else publishing's
    `defaults.main`."""
    from moxi_publishing import flag, settings

    values = flag._git(root, "config", "--get-all", MAIN_KEY).stdout.split("\n")
    values = [v.strip() for v in values if v.strip()]
    return values[-1] if values else settings.load()[settings.MAIN]


def _github(root: str, given: str | None) -> str | None:
    from moxi_publishing import flag

    return given or flag.config_get(root, GITHUB_KEY)


# -- tag ------------------------------------------------------------------------------------

def tag(path: str, package: str, *, at: str | None = None, remote: str = REMOTE,
        web=None) -> Result:
    """`moxi publishing tag PACKAGE [--at REV] [--remote NAME]` in the repo at `path`.
    Raises `Failure` for a usage or read problem."""
    from packaging.utils import canonicalize_name
    from packaging.version import InvalidVersion, Version

    from moxi_publishing import flag, tags

    root = flag.resolve(path)
    git = tags.repo_git(root)
    branch = main_branch(root)
    main_ref = f"refs/remotes/{remote}/{branch}"
    rev = at or main_ref
    commit = tags.resolve(git, rev)
    if commit is None:
        raise Failure(f"{rev} names no commit here" + ("" if at else "; fetch first"), 2)
    try:
        ws = tags.workspace(git, commit)
    except tags.ReleaseError as exc:
        raise Failure(f"the workspace at {commit[:12]} can't be read: {exc}", 1) from None
    dist = canonicalize_name(package)
    member = ws.members.get(dist)
    if member is None:
        raise Failure(f"{package} is no workspace member at {commit[:12]}", 2)
    try:
        version = str(Version(member.version or ""))
    except InvalidVersion:
        raise Failure(f"{dist}'s project.version at {commit[:12]} is "
                      f"{member.version!r}, which is no version", 1) from None
    name = tags.tag_name(dist, version)
    if tags.resolve(git, f"refs/tags/{name}") is not None:
        raise Failure(f"the tag {name} exists already; a release tag never moves", 3)
    where = tags.Where(main_ref, branch, _github(root, None))
    verdict = tags.gate(git, name, where=where, commit=commit, web=web)
    result = Result(verdict.lines())
    if not verdict.passed:
        result.lines.append(f"{name} was not made")
        result.code = 3
        return result
    message = tag_message(git, ws, dist, version)
    done = git("tag", "-a", "--cleanup=verbatim", "-F", "-", name, commit,
               stdin_data=message.encode("utf-8"))
    if done.returncode != 0:
        raise Failure(f"git tag failed: {done.stderr.strip()}", 1)
    result.lines.append(f"made {name} at {commit[:12]}; push it with: git push {remote} "
                        f"{name}")
    return result


def tag_message(git, ws, dist: str, version: str) -> str:
    """The release tag's message: a title line, then the CHANGELOG section."""
    from moxi_publishing import tags

    member = ws.members[dist]
    data = tags.read_file(git, ws.commit, member.file(tags.CHANGELOG)) or b""
    section = tags.changelog_section(data.decode("utf-8", "replace"), version) or ""
    return f"{dist} {version}\n\n{section}\n" if section else f"{dist} {version}\n"


# -- the ci steps ---------------------------------------------------------------------------

def _pushed_tag(root: str, name: str):
    from moxi_publishing import tags

    git = tags.repo_git(root)
    done = git("rev-parse", "--verify", "--quiet", "--end-of-options", f"refs/tags/{name}")
    if done.returncode != 0:
        raise Failure(f"no tag {name} here; the release workflow runs on a tag", 2)
    return git, done.stdout.decode("ascii").strip()


def ci_gate(path: str, name: str, *, github: str | None = None,
            artifact: str | None = None, remote: str = REMOTE, web=None) -> Result:
    """`moxi publishing ci gate TAG`: the gate on a pushed tag, with main read from
    `<remote>/<the repository's default branch>`. Without `artifact`, check 6 lets a
    version the index holds files of pass, since the publish job runs the gate again with
    the build's files."""
    from moxi_publishing import flag, tags

    root = flag.work_tree(path)
    git, obj = _pushed_tag(root, name)
    github = _github(root, github)
    if not github:
        raise Failure("no GitHub repo: pass --github OWNER/REPO", 2)
    web = web if web is not None else tags.default_web()
    try:
        repo = web.github(f"/repos/{github}", f"{github}'s settings")
    except tags.ReleaseError as exc:
        return Result([f"check 3 (main and green): refused: {exc}"], 3)
    branch = repo.get("default_branch") if isinstance(repo, dict) else None
    if not isinstance(branch, str):
        return Result([f"check 3 (main and green): refused: {github} has no default branch "
                       "GitHub reports"], 3)
    where = tags.Where(f"refs/remotes/{remote}/{branch}", branch, github)
    verdict = tags.gate(git, name, where=where, tag_object=obj, web=web, artifact=artifact,
                        files_checked_later=artifact is None)
    return Result(verdict.lines(), 0 if verdict.passed else 3)


def ci_notes(path: str, name: str) -> Result:
    """`moxi publishing ci notes TAG`: the CHANGELOG section of the tag's version."""
    from moxi_publishing import flag, tags

    root = flag.work_tree(path)
    git, obj = _pushed_tag(root, name)
    commit, why = tags.peel(git, obj)
    if commit is None:
        raise Failure(f"{name}: {why}", 3)
    try:
        ws = tags.workspace(git, commit)
    except tags.ReleaseError as exc:
        raise Failure(f"the workspace at {commit[:12]} can't be read: {exc}", 1) from None
    parsed = tags.parse_tag(name, ws.members)
    if parsed is None:
        raise Failure(f"{name} names no member and version at {commit[:12]}", 3)
    dist, version = parsed
    data = tags.read_file(git, commit, ws.members[dist].file(tags.CHANGELOG)) or b""
    section = tags.changelog_section(data.decode("utf-8", "replace"), version)
    if section is None:
        raise Failure(f"{dist}'s CHANGELOG at {commit[:12]} has no section for {version}", 3)
    return Result(section.split("\n"))


__all__ = ["REMOTE", "Result", "main_branch", "tag", "tag_message", "ci_gate", "ci_notes"]
