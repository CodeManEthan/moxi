"""The release step: the catalogue, the ranges between members, and the metadata PyPI
shows. Each test builds a small workspace in tmp, one that passes, and breaks one thing."""

from __future__ import annotations

from pathlib import Path

import pytest

from moxi_dev import release

#: The [project] keys the release step asks of every member, for tests that build one.
METADATA = ('readme = "README.md"\n'
            'license = "MIT"\n'
            'requires-python = ">=3.12"\n'
            'keywords = ["moxi"]\n'
            'classifiers = ["Programming Language :: Python :: 3"]\n'
            'urls = { Repository = "https://github.com/example/moxi" }\n')


def release_files(package: Path, version: str = "0.1.0") -> None:
    """The README and CHANGELOG a member needs to pass the release step."""
    package.mkdir(parents=True, exist_ok=True)
    (package / "README.md").write_text(f"# {package.name}\n\nWhat it is.\n")
    (package / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {version} (2026-01-01)\n\n- The first release.\n")


def catalogue(*lines: tuple[str, str]) -> str:
    return "version = 1\n" + "".join(
        f'\n[[module]]\nname = "{name}"\ndistribution = "{dist}"\nsummary = "s"\n'
        for name, dist in lines)


def member(root: Path, directory: str, name: str, version: str = "0.1.0", *,
           extra: str = "", module: str | None = None) -> Path:
    """A member that passes the release step; `extra` goes in its [project] table."""
    package = root / "packages" / directory
    release_files(package, version)
    text = f'[project]\nname = "{name}"\nversion = "{version}"\n{METADATA}{extra}'
    if module:
        text += f'\n[project.entry-points."moxi.modules"]\n{module} = "x.module"\n'
    (package / "pyproject.toml").write_text(text)
    return package


@pytest.fixture
def ws(tmp_path):
    """The core with a catalogue listing alpha; alpha, a module; beta, which needs alpha
    in its dependencies and in an extra."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["packages/moxi", "packages/alpha", '
        '"packages/beta"]\n')
    core = member(root, "moxi", "moxi", "0.1.7")
    (core / "src" / "moxi").mkdir(parents=True)
    (core / "src" / "moxi" / "catalogue.toml").write_text(catalogue(("alpha", "moxi-alpha")))
    member(root, "alpha", "moxi-alpha", "0.2.0", module="alpha")
    member(root, "beta", "Moxi_Beta", extra=(
        'dependencies = ["moxi>=0.1.6,<0.2", "moxi-alpha>=0.2,<0.3", "pytest>=99"]\n'
        'optional-dependencies = { more = ["moxi-alpha[x]>=0.2"] }\n'))
    return root


def problems(root: Path) -> list[str]:
    return [str(problem) for problem in release.scan(root)]


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, (old, text)
    path.write_text(text.replace(old, new))


def test_a_ready_workspace_passes(ws):
    assert problems(ws) == []


# --- 1. the catalogue ---------------------------------------------------------------------

def test_a_module_the_catalogue_does_not_list_fails(ws):
    with (ws / "packages/beta/pyproject.toml").open("a") as fh:
        fh.write('\n[project.entry-points."moxi.modules"]\nbeta = "x"\n')
    assert problems(ws) == [
        'packages/beta: project.entry-points."moxi.modules": declares the module beta, '
        "which the catalogue doesn't list; add a [[module]] line to "
        'packages/moxi/src/moxi/catalogue.toml with name = "beta", distribution = '
        "\"Moxi_Beta\" and a one-line summary, and raise the catalogue's version"]


def test_a_catalogue_line_no_member_declares_fails(ws):
    (ws / "packages/moxi/src/moxi/catalogue.toml").write_text(
        catalogue(("alpha", "moxi-alpha"), ("gone", "moxi-gone")))
    assert problems(ws) == [
        "packages/moxi: src/moxi/catalogue.toml: lists gone, which no workspace member "
        "declares as a moxi.modules entry point; remove the line and raise the "
        "catalogue's version, or add the module's package to the workspace"]


def test_a_catalogue_line_with_another_distribution_fails(ws):
    (ws / "packages/moxi/src/moxi/catalogue.toml").write_text(
        catalogue(("alpha", "moxi-other")))
    [said] = problems(ws)
    assert said.startswith("packages/moxi: src/moxi/catalogue.toml: lists alpha with "
                           "distribution moxi-other, but packages/alpha declares it")
    assert 'write distribution = "moxi-alpha"' in said


def test_distribution_names_compare_in_their_normal_form(ws):
    (ws / "packages/moxi/src/moxi/catalogue.toml").write_text(
        catalogue(("alpha", "Moxi_Alpha")))
    assert problems(ws) == []


def test_an_unreadable_catalogue_fails(ws):
    (ws / "packages/moxi/src/moxi/catalogue.toml").write_text("version = \n")
    [said] = problems(ws)
    assert said.startswith("packages/moxi: src/moxi/catalogue.toml: can't be read")


def test_a_module_with_no_core_in_the_workspace_fails(ws):
    edit(ws / "pyproject.toml", '"packages/moxi", ', "")
    [said] = problems(ws)
    assert said.startswith('packages/alpha: project.entry-points."moxi.modules": declares '
                           "the module alpha, but the workspace has no core (moxi)")


def test_no_core_and_no_module_needs_no_catalogue(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["packages/low"]\n')
    member(tmp_path, "low", "low")
    assert problems(tmp_path) == []


# --- 2. requirements between members --------------------------------------------------------

def test_a_dependency_the_member_s_version_does_not_satisfy_fails(ws):
    edit(ws / "packages/alpha/pyproject.toml", 'version = "0.2.0"', 'version = "0.3.0"')
    edit(ws / "packages/alpha/CHANGELOG.md", "## 0.2.0", "## 0.3.0")
    assert problems(ws) == [
        "packages/beta: project.dependencies: requires moxi-alpha>=0.2,<0.3, which "
        "moxi-alpha 0.3.0 in this tree doesn't satisfy; widen the range to admit 0.3.0"]


def test_an_extra_the_member_s_version_does_not_satisfy_fails(ws):
    edit(ws / "packages/beta/pyproject.toml", '"moxi-alpha[x]>=0.2"', '"moxi_alpha[x]>=0.4"')
    assert problems(ws) == [
        "packages/beta: project.optional-dependencies.more: requires moxi_alpha[x]>=0.4, "
        "which moxi-alpha 0.2.0 in this tree doesn't satisfy; widen the range to admit "
        "0.2.0"]


def test_the_core_floor_is_checked_like_any_member(ws):
    edit(ws / "packages/beta/pyproject.toml", "moxi>=0.1.6,<0.2", "moxi>=0.1.8,<0.2")
    [said] = problems(ws)
    assert said.startswith("packages/beta: project.dependencies: requires moxi>=0.1.8,<0.2, "
                           "which moxi 0.1.7 in this tree")


def test_a_requirement_that_can_not_be_read_fails(ws):
    edit(ws / "packages/beta/pyproject.toml", '"pytest>=99"', '"pytest >>= 1"')
    [said] = problems(ws)
    assert said.startswith("packages/beta: project.dependencies: 'pytest >>= 1' can't be "
                           "read")


def test_a_missing_version_fails_and_its_requirements_are_not_judged(ws):
    edit(ws / "packages/alpha/pyproject.toml", 'version = "0.2.0"\n', "")
    assert problems(ws) == [
        "packages/alpha: project.version: missing; give the project a static version, "
        "as every package here has"]


# --- 3. the metadata PyPI shows ---------------------------------------------------------------

@pytest.mark.parametrize("key, says", [
    ('readme = "README.md"\n', 'project.readme: missing; add readme = "README.md"'),
    ('classifiers = ["Programming Language :: Python :: 3"]\n',
     "project.classifiers: missing; add the base set"),
    ('urls = { Repository = "https://github.com/example/moxi" }\n',
     "project.urls: missing; add [project.urls] with Homepage, Repository and Issues"),
    ('license = "MIT"\n', 'project.license: missing; add license = "MIT"'),
    ('requires-python = ">=3.12"\n',
     'project.requires-python: missing; add requires-python = ">=3.12"'),
    ('keywords = ["moxi"]\n', "project.keywords: missing; add keywords"),
], ids=["readme", "classifiers", "urls", "license", "requires-python", "keywords"])
def test_a_missing_field_fails(ws, key, says):
    edit(ws / "packages/alpha/pyproject.toml", key, "")
    [said] = problems(ws)
    assert said.startswith(f"packages/alpha: {says}")


def test_an_sdist_listing_its_changelog_passes(ws):
    with (ws / "packages/alpha/pyproject.toml").open("a") as fh:
        fh.write('\n[tool.hatch.build.targets.sdist]\n'
                 'only-include = ["CHANGELOG.md", "README.md", "src/x"]\n')
    assert problems(ws) == []


def test_an_sdist_leaving_out_its_changelog_fails(ws):
    with (ws / "packages/alpha/pyproject.toml").open("a") as fh:
        fh.write('\n[tool.hatch.build.targets.sdist]\n'
                 'only-include = ["README.md", "src/x"]\n')
    assert problems(ws) == [
        "packages/alpha: tool.hatch.build.targets.sdist.only-include: leaves out "
        "CHANGELOG.md; add CHANGELOG.md, so the source distribution carries it"]


def test_a_missing_changelog_fails(ws):
    (ws / "packages/alpha/CHANGELOG.md").unlink()
    assert problems(ws) == [
        "packages/alpha: CHANGELOG.md: missing; add one whose first entry is headed "
        "## 0.2.0 (<date>)"]


def test_a_changelog_behind_the_version_fails(ws):
    edit(ws / "packages/alpha/pyproject.toml", 'version = "0.2.0"', 'version = "0.2.1"')
    assert problems(ws) == [
        "packages/alpha: CHANGELOG.md: its first version heading is 0.2.0, but "
        "project.version is 0.2.1; add an entry headed ## 0.2.1 (<date>) above the rest, "
        "or set the version the changelog names"]


def test_a_changelog_with_no_version_heading_fails(ws):
    (ws / "packages/alpha/CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n")
    [said] = problems(ws)
    assert said.startswith("packages/alpha: CHANGELOG.md: has no version heading")


@pytest.mark.parametrize("heading", ["## [0.2.0] - 2026-01-01", "## v0.2.0", "### 0.2.0"])
def test_the_first_version_heading_is_read_past_others(ws, heading):
    (ws / "packages/alpha/CHANGELOG.md").write_text(
        f"# Changelog\n\n## Unreleased\n\n```\n## 9.9.9\n```\n\n{heading}\n\n## 0.1.0\n")
    assert problems(ws) == []


def test_a_relative_readme_link_fails_and_names_the_absolute_one(ws):
    (ws / "packages/alpha/README.md").write_text(
        "# alpha\n\nMIT. See [LICENSE](LICENSE).\n")
    assert problems(ws) == [
        "packages/alpha: README.md:3: links to LICENSE by a relative path, which leads "
        "nowhere on PyPI; link to "
        "https://github.com/example/moxi/blob/main/packages/alpha/LICENSE instead"]


def test_a_missing_readme_file_fails(ws):
    (ws / "packages/alpha/README.md").unlink()
    [said] = problems(ws)
    assert said.startswith("packages/alpha: project.readme: names README.md, which can't "
                           "be read")


README = """\
# Links

An [absolute](https://example.com/a) one, [mail](mailto:a@example.com), a
[heading](#links) of this page, and `[code](not/a/link)` in a code span.

```md
[in a fence](docs/x.md)
```

![image](docs/shot.png) and [doc](../docs/guide.md#part) and <a href="LICENSE">l</a>.

[label]: CHANGELOG.md
"""


def test_relative_links_are_found_in_every_form_but_code():
    assert release.relative_links(README) == [
        (10, "docs/shot.png"), (10, "../docs/guide.md#part"), (10, "LICENSE"),
        (12, "CHANGELOG.md")]


def test_each_problem_names_member_field_and_fix(ws):
    (ws / "packages/alpha/CHANGELOG.md").unlink()
    [problem] = release.scan(ws)
    assert (problem.member, problem.field, problem.said) == (
        "packages/alpha", "CHANGELOG.md", "missing")
    assert problem.fix.startswith("add one")
