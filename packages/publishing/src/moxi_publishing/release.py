"""Releasing a package: `moxi publishing bump`, `tag` and the `ci` steps.

`bump` sets the next version of each package named, in edits it leaves unstaged for one
commit: its `[project] version`; its CHANGELOG, whose `## Unreleased` heading becomes the
version's dated heading under a new empty `## Unreleased`; each README link into the
repository from the old release tag to the new one; then `uv lock`. It refuses, writing
nothing, a package whose CHANGELOG has no `## Unreleased`, and a requirement another
member places on a bumped package that the new version would no longer meet.

`tag` runs the tag gate (`moxi_publishing.tags.gate`) at a commit of main and makes the
package's annotated release tag only when every check passes; it pushes nothing. The
`ci` steps are the release workflow's: `ci gate` runs the gate on a pushed tag, `ci
notes` prints the release's CHANGELOG section, and `ci siblings` tests the built package
against the sibling versions the package index has, from fresh venvs.
`workflows write` writes the two workflow files the release and the public scan run on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from moxi_publishing.flag import Failure

#: The remote whose tracking refs hold main, unless `--remote` names another.
REMOTE = "origin"
#: The parts of a version `bump --part` may raise.
PARTS = ("patch", "minor", "major")
#: The CHANGELOG heading a build writes its lines under.
UNRELEASED = "## Unreleased"
#: How long `uv lock` may take.
LOCK_TIMEOUT_S = 300.0
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


# -- bump -----------------------------------------------------------------------------------

def next_version(version: str, part: str) -> str:
    """The version after `version` when `part` is raised; lower parts start again at 0."""
    from packaging.version import Version

    major, minor, micro = (list(Version(version).release) + [0, 0])[:3]
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{micro + 1}"


def _set_version(text: str, old: str, new: str) -> str | None:
    """`text` (a pyproject.toml) with `[project] version` set to `new`, or None when no
    such line holds `old`."""
    import re

    lines = text.split("\n")
    table = None
    for i, line in enumerate(lines):
        header = re.match(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$", line)
        if header:
            table = header.group(1).strip()
            continue
        if table == "project":
            match = re.match(r'^(\s*version\s*=\s*)(["\'])([^"\']*)\2(.*)$', line)
            if match and match.group(3) == old:
                lines[i] = f"{match.group(1)}{match.group(2)}{new}{match.group(2)}{match.group(4)}"
                return "\n".join(lines)
    return None


def _date_changelog(text: str, version: str, date: str) -> str | None:
    """`text` with its `## Unreleased` heading become `## <version> (<date>)` under a new
    empty `## Unreleased`, or None when it has no such heading."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.rstrip() == UNRELEASED:
            lines[i:i + 1] = [UNRELEASED, "", f"## {version} ({date})"]
            return "\n".join(lines)
    return None


def bump(path: str, packages, *, part: str = "patch", today: str | None = None,
         lock: bool = True) -> Result:
    """`moxi publishing bump PACKAGE... [--part patch|minor|major]` in the work tree at
    `path`. Raises `Failure`."""
    import datetime
    import os
    import shutil

    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    from moxi import files
    from moxi_publishing import flag, tags

    if part not in PARTS:
        raise Failure(f"--part is one of {', '.join(PARTS)}", 2)
    root = flag.work_tree(path)
    try:
        ws = tags.work_tree_workspace(root)
    except tags.ReleaseError as exc:
        raise Failure(f"the workspace can't be read: {exc}", 1) from None
    date = today or datetime.date.today().isoformat()
    plan: dict[str, str] = {}
    problems: list[str] = []
    for package in packages:
        dist = canonicalize_name(package)
        member = ws.members.get(dist)
        if member is None:
            raise Failure(f"{package} is no workspace member", 2)
        if member.version is None:
            raise Failure(f"{member.file('pyproject.toml')} has no static version", 1)
        plan[dist] = next_version(member.version, part)
    edits: dict[str, bytes] = {}
    for dist, new in plan.items():
        member = ws.members[dist]
        old = member.version
        changelog = member.file(tags.CHANGELOG)
        text = _read(root, changelog)
        dated = None if text is None else _date_changelog(text, new, date)
        if dated is None:
            problems.append(f"{changelog} has no {UNRELEASED} heading to release")
        else:
            edits[changelog] = dated.encode("utf-8")
        pyproject = member.file("pyproject.toml")
        versioned = _set_version(_read(root, pyproject) or "", old, new)
        if versioned is None:
            problems.append(f"{pyproject}: no [project] version = \"{old}\" line to set")
        else:
            edits[pyproject] = versioned.encode("utf-8")
        readme = member.project.get("readme")
        readme = readme if isinstance(readme, str) else "README.md"
        readme_path = member.file(readme)
        text = _read(root, readme_path)
        if text is not None:
            old_tag, new_tag = tags.tag_name(dist, old), tags.tag_name(dist, new)
            moved = text.replace(f"/blob/{old_tag}/", f"/blob/{new_tag}/").replace(
                f"/tree/{old_tag}/", f"/tree/{new_tag}/")
            if moved != text:
                edits[readme_path] = moved.encode("utf-8")
    for other in ws.members.values():
        reqs = list(other.project.get("dependencies") or [])
        extras = other.project.get("optional-dependencies") or {}
        reqs += [r for group in extras.values() if isinstance(group, list) for r in group]
        for text in reqs:
            try:
                req = Requirement(text)
            except (InvalidRequirement, TypeError):
                continue
            name = canonicalize_name(req.name)
            if name not in plan:
                continue
            was = ws.members[name].version
            if req.specifier.contains(was, prereleases=True) and not req.specifier.contains(
                    plan[name], prereleases=True):
                problems.append(f"{other.file('pyproject.toml')}: {text!r} would not admit "
                                f"{name} {plan[name]}; change it in the same commit")
    if problems:
        return Result(problems + ["nothing was changed"], 3)
    for rel, data in edits.items():
        target = os.path.join(root, rel)
        files.atomic_write(target, data, mode=os.stat(target).st_mode & 0o777)
    result = Result([f"{dist}: {ws.members[dist].version} -> {new}"
                     for dist, new in plan.items()])
    if lock:
        from moxi import proc

        uv = shutil.which("uv")
        if uv is None:
            raise Failure("the versions are set, but uv is not on PATH to run uv lock", 1)
        done = proc.run([uv, "lock"], cwd=root, timeout_s=LOCK_TIMEOUT_S)
        if done.returncode != 0:
            err = (done.stderr or b"").decode("utf-8", "replace").strip()
            raise Failure(f"the versions are set, but uv lock failed: {err}", 1)
        result.lines.append("uv.lock updated")
    result.lines.append("the edits are left unstaged for one commit")
    return result


def _read(root: str, rel: str) -> str | None:
    import os

    try:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


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
    version the index holds files of pass, since the verify job runs the gate again with
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



# -- ci siblings: the release test and the published-siblings check -------------------------

#: The resolutions the release test installs at: the newest published siblings the package
#: admits, then the lowest set that resolves.
RESOLUTIONS = ("highest", "lowest-direct")
#: How long installing into a fresh venv may take, and the suite's run.
INSTALL_TIMEOUT_S = 900.0
SUITE_TIMEOUT_S = 3600.0
#: The name the site-packages check is loaded under in the release test's pytest run.
SITE_CHECK = "moxi_publishing_site_check"
#: The lockfile the test tools' versions are read from, at the repo's root.
LOCK = "uv.lock"
#: The workspace member whose pytest plugin is the test fence every package's conftest
#: loads. A package whose dev group names it gets it from the checkout with `--no-deps`,
#: so the fence loads without its own requirements moving any sibling's resolution.
FENCE = "moxi-dev"
#: The test tools the fence runs beside, installed at uv.lock's versions with it, since
#: `--no-deps` leaves out the ones its own requirements would bring.
FENCE_TOOLS = ("pytest", "pytest-xdist", "pytest-timeout")

_INSTALLED = ("import json, importlib.metadata as m\n"
              "print(json.dumps({d.metadata['Name']: [d.version, d.requires or []]\n"
              "                  for d in m.distributions()}))\n")


@dataclass
class ReleaseTest:
    """What the release test of one tag installs."""

    dist: str
    version: str
    root: str                   # the work tree
    member_path: str            # the package's directory, from the root
    wheel: str                  # the wheel's path
    imports: list[str]          # the wheel's top-level import names
    siblings: list              # the requirements on members it installs, as Requirement
    extras: list[str]           # the extras installed
    tools: list[str]            # the test tools, pinned
    lines: list[str] = field(default_factory=list)
    fence: str | None = None    # the test fence member's directory, from the root


def wheel_imports(wheel: str) -> list[str]:
    """The top-level packages and modules a wheel installs."""
    import zipfile

    found = set()
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            first, _, rest = name.partition("/")
            if first.endswith((".dist-info", ".data")):
                continue
            if rest:
                found.add(first)
            elif first.endswith(".py"):
                found.add(first[:-3])
    return sorted(found)


def _wheel(artifact: str, dist: str, version: str) -> str:
    import os

    from packaging.utils import InvalidWheelFilename, parse_wheel_filename
    from packaging.version import Version

    try:
        names = sorted(os.listdir(artifact))
    except OSError as exc:
        raise Failure(f"{artifact} can't be read: {exc}", 2) from None
    found = []
    for name in names:
        if not name.endswith(".whl"):
            continue
        try:
            wdist, wversion, _build, _tags = parse_wheel_filename(name)
        except InvalidWheelFilename:
            continue
        if wdist == dist and wversion == Version(version):
            found.append(name)
    if len(found) != 1:
        raise Failure(f"{artifact} holds {len(found)} wheel(s) of {dist} {version}; the "
                      "release test wants one", 2)
    return os.path.join(artifact, found[0])


def locked_versions(data: bytes | None) -> dict[str, str]:
    """{canonical name: version} of every package a `uv.lock` pins."""
    import tomllib

    from packaging.utils import canonicalize_name

    if data is None:
        return {}
    try:
        lock = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise Failure(f"{LOCK} can't be read: {exc}", 1) from None
    out: dict[str, str] = {}
    for package in lock.get("package", []):
        if isinstance(package, dict) and isinstance(package.get("name"), str) \
                and isinstance(package.get("version"), str):
            out.setdefault(canonicalize_name(package["name"]), package["version"])
    return out


def _test_tools(git, ws, member) -> tuple[list[str], str | None]:
    """The package's own dev group less workspace members, each pinned to uv.lock, and the
    test fence member's directory when the dev group names it; the fence's tools are then
    among the tools."""
    import tomllib

    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    from moxi_publishing import tags

    data = tags.read_file(git, ws.commit, member.file("pyproject.toml")) or b""
    try:
        groups = tomllib.loads(data.decode("utf-8")).get("dependency-groups", {})
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        groups = {}
    entries = groups.get("dev", []) if isinstance(groups, dict) else []
    locked = locked_versions(tags.read_file(git, ws.commit, LOCK))
    tools, unpinned, named, fence = [], [], set(), None
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, str):
            continue  # an include-group table
        try:
            req = Requirement(entry)
        except InvalidRequirement:
            raise Failure(f"{member.file('pyproject.toml')}: dev requirement {entry!r} "
                          "can't be read", 1) from None
        name = canonicalize_name(req.name)
        if name in ws.members:
            if name == FENCE:
                fence = ws.members[name].path
            continue
        named.add(name)
        version = locked.get(name)
        if version is None:
            unpinned.append(name)
            continue
        extras = f"[{','.join(sorted(req.extras))}]" if req.extras else ""
        marker = f"; {req.marker}" if req.marker else ""
        tools.append(f"{req.name}{extras}=={version}{marker}")
    if unpinned:
        raise Failure(f"{', '.join(unpinned)}, in {member.file('pyproject.toml')}'s dev "
                      f"group, {'is' if len(unpinned) == 1 else 'are'} not in {LOCK}", 1)
    if fence is not None:
        for name in FENCE_TOOLS:
            if name in named:
                continue
            if name not in locked:
                unpinned.append(name)
            else:
                tools.append(f"{name}=={locked[name]}")
        if unpinned:
            raise Failure(f"{', '.join(unpinned)}, which the test fence ({FENCE}) runs "
                          f"beside, {'is' if len(unpinned) == 1 else 'are'} not in {LOCK}",
                          1)
    return tools, fence


def release_test(path: str, name: str, *, artifact: str, web=None) -> ReleaseTest | Result:
    """What the release test of the pushed tag `name` installs, or a refusal: a required
    sibling no published version admits. Raises `Failure`."""
    from packaging.utils import canonicalize_name

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
    member = ws.members[dist]
    wheel = _wheel(artifact, dist, version)
    web = web if web is not None else tags.default_web()
    lines, missing, siblings, extras = [], [], [], []
    try:
        for req in tags._member_requirements(ws, member.project.get("dependencies", [])):
            if tags.admitted(req, web.published(canonicalize_name(req.name))):
                siblings.append(req)
            else:
                missing.append(f"{canonicalize_name(req.name)}{req.specifier}")
        groups = member.project.get("optional-dependencies", {})
        for extra, reqs in sorted(groups.items()) if isinstance(groups, dict) else []:
            wanted = tags._member_requirements(ws, reqs)
            lacking = [f"{canonicalize_name(r.name)}{r.specifier}" for r in wanted
                       if not tags.admitted(r, web.published(canonicalize_name(r.name)))]
            if lacking:
                lines.append(f"extra {extra}: left out, no published {', '.join(lacking)}")
            else:
                extras.append(extra)
                siblings += wanted
                lines.append(f"extra {extra}: installed")
    except tags.ReleaseError as exc:
        return Result([f"the index can't be read: {exc}"], 1)
    if missing:
        return Result(lines + [f"no published version admits {', '.join(missing)}, which "
                               f"{dist} requires; release those first"], 3)
    tools, fence = _test_tools(git, ws, member)
    return ReleaseTest(dist, version, root, member.path, wheel, wheel_imports(wheel),
                       siblings, extras, tools, lines, fence)


def _default_run(argv, *, cwd=None, env=None, capture=True, timeout_s):
    from moxi import proc

    return proc.run(argv, cwd=cwd, env=env, capture=capture, timeout_s=timeout_s)


def floors_raised(siblings, published: dict, installed: dict) -> list[str]:
    """A line for each sibling installed above the lowest published version its
    requirement admits, naming the installed requirements that raised it.
    `published` maps a sibling to its published versions, `installed` a distribution's
    canonical name to (version, its requirements)."""
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version

    from moxi_publishing import tags

    lines = []
    for req in siblings:
        name = canonicalize_name(req.name)
        admitted = tags.admitted(req, published.get(name, []))
        got = installed.get(name)
        if not admitted or got is None:
            continue
        lowest = min(admitted, key=Version)
        if Version(got[0]) <= Version(lowest):
            continue
        raisers = []
        for other, (other_version, requires) in sorted(installed.items()):
            for text in requires:
                try:
                    need = Requirement(text)
                except InvalidRequirement:
                    continue
                if canonicalize_name(need.name) == name and not need.specifier.contains(
                        lowest, prereleases=True):
                    raisers.append(f"{other} {other_version} ({need.name}{need.specifier})")
        lines.append(f"floor raised: {name} {lowest} is the lowest published version "
                     f"{req.specifier or 'any'} admits; {got[0]} was installed, raised by "
                     + (", ".join(raisers) if raisers else "the resolver"))
    return lines


def _run_once(plan: ReleaseTest, resolution: str, *, uv: str, run, web) -> tuple[bool, list[str]]:
    """Install the wheel into a fresh venv at `resolution` and run the suite there."""
    import json
    import os
    import shutil
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from packaging.utils import canonicalize_name

    from moxi_publishing import site_check, tags

    lines = []
    where = tempfile.mkdtemp(prefix="moxi-release-")
    try:
        venv = os.path.join(where, "venv")
        python = os.path.join(venv, "Scripts", "python.exe") if os.name == "nt" \
            else os.path.join(venv, "bin", "python")
        done = run([uv, "venv", "--quiet", "--python", sys.executable, venv],
                   timeout_s=INSTALL_TIMEOUT_S)
        if done.returncode != 0:
            return False, [f"{resolution}: the venv can't be made: {_err(done)}"]
        index = tags.index_from_environment()
        wanted = [f"{plan.dist}[{','.join(plan.extras)}] @ {Path(plan.wheel).resolve().as_uri()}"
                  if plan.extras else f"{plan.dist} @ {Path(plan.wheel).resolve().as_uri()}"]
        # The siblings are named as well, so the lowest resolution reaches them: it lowers
        # direct requirements only.
        wanted += [str(req) for req in plan.siblings] + plan.tools
        argv = [uv, "pip", "install", "--quiet", "--python", python]
        if index:
            argv += ["--default-index", index.rstrip("/") + "/simple"]
        done = run(argv + ["--resolution", resolution] + wanted, cwd=where,
                   timeout_s=INSTALL_TIMEOUT_S)
        if done.returncode != 0:
            return False, [f"{resolution}: the install failed: {_err(done)}"]
        done = run([python, "-I", "-c", _INSTALLED], cwd=where, timeout_s=INSTALL_TIMEOUT_S)
        try:
            installed = {canonicalize_name(k): (v[0], v[1]) for k, v in
                         json.loads(done.stdout).items()}
        except (ValueError, TypeError, AttributeError, IndexError):
            return False, [f"{resolution}: the venv's packages can't be read: {_err(done)}"]
        names = sorted({canonicalize_name(r.name) for r in plan.siblings})
        lines.append(f"{resolution}: installed " + ", ".join(
            f"{n} {installed[n][0]}" for n in names if n in installed)
            if names else f"{resolution}: installed {plan.dist} {plan.version}, which "
                          "requires no sibling")
        if resolution == "lowest-direct":
            published = {n: web.published(n) for n in names}
            lines += floors_raised(plan.siblings, published, installed)
        if plan.fence is not None:
            # After the resolution and its reading, alone and without its requirements:
            # the fence's own requirements never take part in the siblings' resolution.
            source = os.path.join(plan.root, plan.fence)
            done = run(argv + ["--no-deps", source], cwd=where, timeout_s=INSTALL_TIMEOUT_S)
            if done.returncode != 0:
                return False, lines + [f"{resolution}: the test fence ({FENCE}) can't be "
                                       f"installed: {_err(done)}"]
        plugin = os.path.join(where, "plugin")
        os.mkdir(plugin)
        shutil.copyfile(site_check.__file__, os.path.join(plugin, SITE_CHECK + ".py"))
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
        env.update({site_check.NAMES_ENV: ",".join(plan.imports), "PYTHONPATH": plugin,
                    "PYTHONSAFEPATH": "1"})
        try:
            done = run([python, "-m", "pytest", "-p", SITE_CHECK, "-p", "no:cacheprovider"],
                       cwd=os.path.join(plan.root, plan.member_path), env=env,
                       capture=False, timeout_s=SUITE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return False, lines + [f"{resolution}: the suite ran past {SUITE_TIMEOUT_S:.0f} s"]
        if done.returncode != 0:
            return False, lines + [f"{resolution}: the suite failed (exit {done.returncode})"]
        return True, lines + [f"{resolution}: the suite passed"]
    except subprocess.TimeoutExpired:
        return False, lines + [f"{resolution}: the install ran past {INSTALL_TIMEOUT_S:.0f} s"]
    except tags.ReleaseError as exc:
        return False, lines + [f"{resolution}: the index can't be read: {exc}"]
    finally:
        shutil.rmtree(where, ignore_errors=True)


def _err(done) -> str:
    text = done.stderr or b""
    text = text.decode("utf-8", "replace") if isinstance(text, bytes) else text
    return text.strip().split("\n")[-1] if text.strip() else f"exit {done.returncode}"


def ci_siblings(path: str, name: str, *, artifact: str, resolution: str | None = None,
                web=None, run=None) -> Result:
    """`moxi publishing ci siblings TAG --artifact DIR`: the package's whole suite from a
    fresh venv holding only its wheel, the extras whose siblings are published, the
    published siblings and its test tools at uv.lock's versions, and the test fence from
    the checkout without its requirements when its dev group names it; once at the
    newest published siblings it admits and once at the lowest set that resolves, or only
    at `resolution`. Raises `Failure`."""
    import shutil

    from moxi_publishing import tags

    if resolution is not None and resolution not in RESOLUTIONS:
        raise Failure(f"--resolution is one of {', '.join(RESOLUTIONS)}", 2)
    web = web if web is not None else tags.default_web()
    plan = release_test(path, name, artifact=artifact, web=web)
    if isinstance(plan, Result):
        return plan
    uv = shutil.which("uv")
    if uv is None:
        raise Failure("uv is not on PATH; the release test installs with it", 1)
    result = Result(list(plan.lines))
    for each in (resolution,) if resolution else RESOLUTIONS:
        passed, lines = _run_once(plan, each, uv=uv, run=run or _default_run, web=web)
        result.lines += lines
        if not passed:
            result.code = 1
    return result


# -- the workflow files ---------------------------------------------------------------------

#: The workflow files `workflows write` writes, from the package's templates.
#: The public scan's workflow: GitHub's check of every push, a release or not.
SCAN_WORKFLOW = "publishing.yml"
WORKFLOWS = (SCAN_WORKFLOW, "release.yml")
WORKFLOW_DIR = ".github/workflows"


def template(name: str) -> bytes:
    """The template of the workflow file `name`."""
    from importlib.resources import files

    return files("moxi_publishing").joinpath("templates", name).read_bytes()


def workflows_write(path: str) -> Result:
    """`moxi publishing workflows write PATH`: the two workflow files into the repo's
    `.github/workflows/`, each replaced by its template. Raises `Failure`."""
    import os

    from moxi import files
    from moxi_publishing import flag

    root = flag.work_tree(path)
    result = Result()
    for name in WORKFLOWS:
        target = os.path.join(root, *WORKFLOW_DIR.split("/"), name)
        data = template(name)
        try:
            with open(target, "rb") as fh:
                same = fh.read() == data
        except FileNotFoundError:
            same = None
        except OSError as exc:
            raise Failure(f"{target} can't be read: {exc}", 1) from None
        if same:
            result.lines.append(f"{WORKFLOW_DIR}/{name}: unchanged")
            continue
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            files.atomic_write(target, data, mode=0o644)
        except OSError as exc:
            raise Failure(f"{target} can't be written: {exc}", 1) from None
        result.lines.append(f"{WORKFLOW_DIR}/{name}: " + ("written" if same is None
                                                         else "replaced"))
    result.lines.append("commit them; the release workflow needs the pypi environment and "
                        "a trusted publisher per package")
    return result

__all__ = ["REMOTE", "PARTS", "UNRELEASED", "RESOLUTIONS", "WORKFLOWS", "Result",
           "ReleaseTest", "next_version", "bump", "main_branch", "tag", "tag_message",
           "ci_gate", "ci_notes", "ci_siblings", "release_test", "floors_raised",
           "wheel_imports", "locked_versions", "template", "workflows_write"]
