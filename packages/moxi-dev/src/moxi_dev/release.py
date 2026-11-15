"""The release step of `moxi-dev check`: what every package must hold before it is published.

It reads the root `pyproject.toml`'s workspace members and each member's own
`pyproject.toml`, whatever the change touched, and checks four things:

1. The catalogue, `catalogue.toml` in the core's package (the member named `moxi`),
   lists exactly the members that declare a `moxi.modules` entry point: one line for
   each, whose `name` is the entry point's name and whose `distribution` is the member's
   project name. A workspace without the core has no catalogue, so no member of it may
   declare a module.
2. Every requirement a member places on another member, in `dependencies` or in any
   extra, admits that member's `version`, so the packages as they stand in the tree can
   be installed together.
3. Each member declares `readme`, `license`, `requires-python`, `keywords`,
   `classifiers` and `[project.urls]`; has a `CHANGELOG.md` whose first version heading
   is its `version`, and which its sdist's `only-include`, when it sets one, names; and
   its README links to nothing by a relative path, `](LICENSE)` included. PyPI shows
   the README as the project's page, where a relative link leads nowhere. A link to a
   heading of the same page (`#...`) is let through. A link to a file in the repository
   (`<Repository URL>/blob/<ref>/<path>`) names the member's own release tag at its
   current version as the ref, `<distribution>-v<version>`, so the page PyPI shows for a
   version links to the files of that version. moxi-publishing owns the tag scheme and
   the CHANGELOG heading reader, and this step takes both from it.
4. When the root's `[tool.moxi-publishing]` keeps a `ready` list, every member is on
   exactly one of it and `not-ready`, so a member added to the workspace is never ready
   until a commit says so. The reading is moxi-publishing's, the one its tag gate uses.

Each problem names the member, the field and the fix. `scan` returns them all.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from moxi_publishing import tags

#: The core's distribution name; its package holds the catalogue.
CORE = "moxi"
#: The catalogue's place in the core's directory.
CATALOGUE = Path("src") / "moxi" / "catalogue.toml"
#: The entry point group a module announces itself in.
GROUP = "moxi.modules"
CHANGELOG = "CHANGELOG.md"


@dataclass(frozen=True)
class Problem:
    member: str     # the member's path from the root, as the root's `members` names it
    field: str      # the field or file at fault
    said: str       # what is wrong
    fix: str        # what to do about it

    def __str__(self) -> str:
        return f"{self.member}: {self.field}: {self.said}; {self.fix}"


@dataclass(frozen=True)
class _Member:
    name: str                 # as the root's `members` names it
    path: Path
    project: dict             # its [project] table, or {} when it has none
    dist: str | None          # its project name, canonical
    version: Version | None
    sdist_only: object        # its sdist's `only-include`, or None when it sets none


def scan(root: Path) -> list[Problem]:
    """Every problem the release step finds in the workspace at `root`."""
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        names = data["tool"]["uv"]["workspace"]["members"]
    except (OSError, UnicodeDecodeError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        return [Problem("pyproject.toml", "tool.uv.workspace.members",
                        f"the workspace's members can't be read ({exc})",
                        "name each package under [tool.uv.workspace] members")]
    problems: list[Problem] = []
    members = []
    for name in names:
        member = _read_member(root, name, problems)
        if member is not None:
            members.append(member)
    problems += catalogue_problems(root, members)
    problems += requirement_problems(members)
    for member in members:
        problems += metadata_problems(member)
    problems += readiness_problems(root)
    return problems


def readiness_problems(root: Path) -> list[Problem]:
    """Each member on neither or both of the not-ready and ready lists, where the root
    keeps a ready list."""
    where = f"[tool.{tags.TABLE}]"
    try:
        wrong = tags.work_tree_workspace(str(root)).unlisted()
    except tags.ReleaseError as exc:
        return [Problem("pyproject.toml", where, f"can't be read ({exc})",
                        f"make {tags.NOT_READY} and {tags.READY} lists of distribution names")]
    return [Problem("pyproject.toml", where, f"{dist} is {said}",
                    f"put {dist} on {tags.NOT_READY}, or on {tags.READY} once it is confirmed")
            for dist, said in wrong.items()]


def _read_member(root: Path, name: str, problems: list[Problem]) -> _Member | None:
    path = root / name
    try:
        data = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        problems.append(Problem(name, "pyproject.toml", f"can't be read ({exc})",
                                "give the member a readable pyproject.toml"))
        return None
    project = data.get("project")
    project = project if isinstance(project, dict) else {}
    sdist = _table(data, "tool", "hatch", "build", "targets", "sdist")
    sdist_only = sdist.get("only-include")
    dist = project.get("name")
    if not isinstance(dist, str):
        problems.append(Problem(name, "project.name", "missing",
                                "name the project in its [project] table"))
    version = None
    raw = project.get("version")
    if not isinstance(raw, str):
        problems.append(Problem(name, "project.version", "missing",
                                "give the project a static version, as every package "
                                "here has"))
    else:
        try:
            version = Version(raw)
        except InvalidVersion:
            problems.append(Problem(name, "project.version", f"{raw!r} is not a version",
                                    "write a version such as 0.1.0"))
    return _Member(name, path, project,
                   canonicalize_name(dist) if isinstance(dist, str) else None, version,
                   sdist_only)


def _table(data: dict, *keys: str) -> dict:
    """The table at `keys` in `data`, or {} when any step of the way isn't one."""
    for key in keys:
        data = data.get(key)
        if not isinstance(data, dict):
            return {}
    return data


# --- 1. the catalogue ---------------------------------------------------------------------

def _modules(member: _Member) -> list[str]:
    """The `moxi.modules` entry points the member declares, by name."""
    points = member.project.get("entry-points", {})
    group = points.get(GROUP, {}) if isinstance(points, dict) else {}
    return list(group) if isinstance(group, dict) else []


def catalogue_problems(root: Path, members: list[_Member]) -> list[Problem]:
    """The catalogue against the members that declare a `moxi.modules` entry point."""
    declared = {name: member for member in members for name in _modules(member)}
    core = next((m for m in members if m.dist == CORE), None)
    if core is None:
        return [Problem(member.name, f'project.entry-points."{GROUP}"',
                        f"declares the module {name}, but the workspace has no core "
                        f"({CORE}) whose catalogue could list it",
                        "add the core to the workspace's members")
                for name, member in declared.items()]
    where = f"{core.name}/{CATALOGUE.as_posix()}"
    try:
        data = tomllib.loads((core.path / CATALOGUE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [Problem(core.name, CATALOGUE.as_posix(), f"can't be read ({exc})",
                        "restore the catalogue: an integer version and one [[module]] "
                        "line for each module package")]
    lines = data.get("module", [])
    lines = [line for line in lines if isinstance(line, dict)] if isinstance(lines, list) else []
    listed: dict[str, str | None] = {}
    for line in lines:
        name, dist = line.get("name"), line.get("distribution")
        if isinstance(name, str):
            listed[name] = canonicalize_name(dist) if isinstance(dist, str) else None
    problems = []
    for name, member in declared.items():
        if name not in listed:
            problems.append(Problem(
                member.name, f'project.entry-points."{GROUP}"',
                f"declares the module {name}, which the catalogue doesn't list",
                f'add a [[module]] line to {where} with name = "{name}", distribution = '
                f'"{member.project.get("name")}" and a one-line summary, and raise the '
                f"catalogue's version"))
        elif listed[name] != member.dist:
            problems.append(Problem(
                core.name, CATALOGUE.as_posix(),
                f"lists {name} with distribution {listed[name]}, but {member.name} "
                f"declares it and is named {member.project.get('name')}",
                f'write distribution = "{member.project.get("name")}" on that line, and '
                f"raise the catalogue's version"))
    for name in listed:
        if name not in declared:
            problems.append(Problem(
                core.name, CATALOGUE.as_posix(),
                f"lists {name}, which no workspace member declares as a {GROUP} entry "
                f"point",
                f"remove the line and raise the catalogue's version, or add the module's "
                f"package to the workspace"))
    return problems


# --- 2. requirements between members --------------------------------------------------------

def _requirements(project: dict) -> list[tuple[str, str]]:
    """(field, requirement) for every requirement in `dependencies` and the extras."""
    out = [("project.dependencies", text) for text in project.get("dependencies", [])
           if isinstance(text, str)]
    extras = project.get("optional-dependencies", {})
    if isinstance(extras, dict):
        for extra, texts in extras.items():
            if isinstance(texts, list):
                out += [(f"project.optional-dependencies.{extra}", text) for text in texts
                        if isinstance(text, str)]
    return out


def requirement_problems(members: list[_Member]) -> list[Problem]:
    """Each requirement on a member that the member's own version doesn't satisfy."""
    versions = {m.dist: m for m in members if m.dist is not None}
    problems = []
    for member in members:
        for field, text in _requirements(member.project):
            try:
                requirement = Requirement(text)
            except InvalidRequirement as exc:
                problems.append(Problem(member.name, field, f"{text!r} can't be read ({exc})",
                                        "write it as a PEP 508 requirement"))
                continue
            other = versions.get(canonicalize_name(requirement.name))
            if other is None or other.version is None:
                continue
            if not requirement.specifier.contains(other.version, prereleases=True):
                problems.append(Problem(
                    member.name, field,
                    f"requires {text}, which {other.project.get('name')} {other.version} "
                    f"in this tree doesn't satisfy",
                    f"widen the range to admit {other.version}"))
    return problems


# --- 3. the metadata PyPI shows ---------------------------------------------------------------

_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_CODE_SPAN = re.compile(r"(`+).*?\1")
_LINKS = (
    re.compile(r"\]\(\s*<?([^)\s>]*)"),                          # [text](target), images too
    re.compile(r"^[ \t]{0,3}\[[^\]]+\]:[ \t]*<?([^\s>]+)"),      # [label]: target
    re.compile(r"""\b(?:href|src)[ \t]*=[ \t]*["']([^"']*)"""),  # <a href>, <img src>
)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def links(text: str) -> list[tuple[int, str]]:
    """(line number, target) for each link in Markdown `text`, outside code."""
    found = []
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        opened = _FENCE.match(line)
        if fence is not None:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence):
                fence = None
            continue
        if opened:
            fence = opened.group(1)
            continue
        line = _CODE_SPAN.sub("", line)
        for pattern in _LINKS:
            for match in pattern.finditer(line):
                if match.group(1):
                    found.append((number, match.group(1)))
    return found


def relative_links(text: str) -> list[tuple[int, str]]:
    """(line number, target) for each link in Markdown `text` that names its target by a
    relative path. Absolute URLs, `mailto:` and the like, and links to a heading of the
    same page (`#...`) are left out, as is anything in code."""
    return [(number, target) for number, target in links(text)
            if not _SCHEME.match(target) and not target.startswith("#")]


def _first_version_heading(text: str) -> str | None:
    heading = tags.first_version_heading(text)
    return None if heading is None else heading.version


def _same_version(heading: str, version: Version) -> bool:
    try:
        return Version(heading) == version
    except InvalidVersion:
        return False


def _repository(member: _Member) -> str | None:
    urls = member.project.get("urls", {})
    repository = urls.get("Repository") if isinstance(urls, dict) else None
    return repository.rstrip("/") if isinstance(repository, str) else None


def _release_tag(member: _Member) -> str:
    """The member's release tag at its current version, or a description of one."""
    if member.dist is None or member.version is None:
        return "<distribution>-v<version>"
    return tags.tag_name(member.dist, str(member.version))


def _absolute(member: _Member, target: str) -> str:
    """The absolute URL a relative link to `target` should be, read from the member's
    Repository URL, or a description of one when it has none."""
    repository = _repository(member)
    if repository is None:
        return "an absolute URL"
    path = (Path(member.name) / target.split("#")[0]).as_posix()
    return f"{repository}/blob/{_release_tag(member)}/{path}"


def _blob_refs(member: _Member, readme_file: str, text: str) -> list[Problem]:
    """A link to a file in the repository whose ref is not the member's release tag."""
    repository = _repository(member)
    if repository is None:
        return []
    prefix, want = f"{repository}/blob/", _release_tag(member)
    problems = []
    for number, target in links(text):
        if not target.startswith(prefix):
            continue
        ref, _, rest = target[len(prefix):].partition("/")
        if ref != want:
            problems.append(Problem(
                member.name, f"{readme_file}:{number}",
                f"links to {target}, at {ref} rather than this version's release tag",
                f"link to {prefix}{want}/{rest} instead"))
    return problems


#: The site of the sdist's file list in a member's pyproject.toml.
SDIST_ONLY = "tool.hatch.build.targets.sdist.only-include"


def metadata_problems(member: _Member) -> list[Problem]:
    """The member's readme, licence, Python floor, keywords, classifiers, URLs,
    CHANGELOG and README links."""
    project, name = member.project, member.name
    problems = []
    readme = project.get("readme")
    if readme is None:
        problems.append(Problem(name, "project.readme", "missing",
                                'add readme = "README.md", which PyPI shows as the '
                                "project's page"))
    if not project.get("license"):
        problems.append(Problem(name, "project.license", "missing",
                                'add license = "MIT", the licence every package here '
                                "carries"))
    if not project.get("requires-python"):
        problems.append(Problem(name, "project.requires-python", "missing",
                                'add requires-python = ">=3.12", the oldest Python CI '
                                "runs"))
    if not project.get("keywords"):
        problems.append(Problem(name, "project.keywords", "missing",
                                "add keywords: the words someone would search PyPI for "
                                "to find the package"))
    if not project.get("classifiers"):
        problems.append(Problem(name, "project.classifiers", "missing",
                                "add the base set the core declares (Development Status, "
                                "Intended Audience, the Python versions CI runs, the "
                                "operating systems the README claims) and one Topic"))
    if not project.get("urls"):
        problems.append(Problem(name, "project.urls", "missing",
                                "add [project.urls] with Homepage, Repository and Issues"))
    problems += _changelog(member)
    problems += _sdist_changelog(member)
    readme_file = readme if isinstance(readme, str) else (
        readme.get("file") if isinstance(readme, dict) else None)
    if isinstance(readme_file, str):
        try:
            text = (member.path / readme_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(Problem(name, "project.readme",
                                    f"names {readme_file}, which can't be read ({exc})",
                                    "write the README, or name the file that is"))
        else:
            for number, target in relative_links(text):
                problems.append(Problem(
                    name, f"{readme_file}:{number}",
                    f"links to {target} by a relative path, which leads nowhere on PyPI",
                    f"link to {_absolute(member, target)} instead"))
            problems += _blob_refs(member, readme_file, text)
    return problems


def _sdist_changelog(member: _Member) -> list[Problem]:
    """An sdist that lists its paths must list the CHANGELOG, or it ships without one."""
    listed = member.sdist_only
    if not isinstance(listed, list):
        return []
    paths = {item.strip().lstrip("/").removeprefix("./")
             for item in listed if isinstance(item, str)}
    if CHANGELOG in paths:
        return []
    return [Problem(member.name, SDIST_ONLY, f"leaves out {CHANGELOG}",
                    f"add {CHANGELOG}, so the source distribution carries it")]


def _changelog(member: _Member) -> list[Problem]:
    version = member.version
    if version is None:
        return []
    want = f"## {version} (<date>)"
    try:
        text = (member.path / CHANGELOG).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [Problem(member.name, CHANGELOG, "missing",
                        f"add one whose first entry is headed {want}")]
    heading = _first_version_heading(text)
    if heading is None:
        return [Problem(member.name, CHANGELOG, "has no version heading",
                        f"head its first entry {want}")]
    if not _same_version(heading, version):
        return [Problem(member.name, CHANGELOG,
                        f"its first version heading is {heading}, but project.version is "
                        f"{version}",
                        f"add an entry headed {want} above the rest, or set the version "
                        f"the changelog names")]
    return []
