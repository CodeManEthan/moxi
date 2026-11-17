"""`moxi publishing bump`: the version, the CHANGELOG with and without `## Unreleased`,
README links, `uv lock` called, and a dependent's requirement refused."""

from __future__ import annotations

import os
import stat

import pytest
from conftest import git, member_toml, release_files, write

from moxi_publishing import cli, release

BASE = "https://github.com/operator/proj-a"


@pytest.fixture
def uv(tmp_path, monkeypatch):
    """A `uv` on PATH that records how it was called."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "uv-calls"
    script = bin_dir / "uv"
    script.write_text(f'#!/bin/sh\necho "$PWD $*" >> "{calls}"\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return calls


@pytest.fixture
def ws(repo):
    release_files(repo)
    write(repo, "packages/a/CHANGELOG.md",
          "# Changelog\n\n## Unreleased\n\n- New work.\n\n## 0.1.0 (2026-01-05)\n\n- First.\n")
    write(repo, "packages/a/pyproject.toml", member_toml("proj-a", "0.1.0")
          + f'readme = "README.md"\n[project.urls]\nRepository = "{BASE}"\n')
    write(repo, "packages/a/README.md",
          f"# proj-a\n\n[licence]({BASE}/blob/proj-a-v0.1.0/packages/a/LICENSE) and "
          f"[docs]({BASE}/tree/proj-a-v0.1.0/docs), [main]({BASE}/blob/main/x)\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "The workspace")
    return repo


def test_bump_sets_version_changelog_links_and_locks(ws, uv):
    result = release.bump(str(ws), ["proj-a"], today="2026-02-03")
    assert result.code == 0, result.lines
    assert result.lines[0] == "proj-a: 0.1.0 -> 0.1.1"
    assert 'version = "0.1.1"' in (ws / "packages/a/pyproject.toml").read_text()
    assert (ws / "packages/a/CHANGELOG.md").read_text() == (
        "# Changelog\n\n## Unreleased\n\n## 0.1.1 (2026-02-03)\n\n- New work.\n\n"
        "## 0.1.0 (2026-01-05)\n\n- First.\n")
    readme = (ws / "packages/a/README.md").read_text()
    assert f"{BASE}/blob/proj-a-v0.1.1/packages/a/LICENSE" in readme
    assert f"{BASE}/tree/proj-a-v0.1.1/docs" in readme and f"{BASE}/blob/main/x" in readme
    assert uv.read_text() == f"{ws} lock\n"
    assert git(ws, "diff", "--cached", "--name-only").stdout == ""


@pytest.mark.parametrize("part, want", [("minor", "0.2.0"), ("major", "1.0.0")])
def test_bump_raises_the_part_named(ws, uv, part, want):
    assert release.bump(str(ws), ["proj-a"], part=part, today="2026-02-03").code == 0
    assert f'version = "{want}"' in (ws / "packages/a/pyproject.toml").read_text()
    assert f"## {want} (2026-02-03)" in (ws / "packages/a/CHANGELOG.md").read_text()


def test_bump_refuses_a_changelog_without_unreleased_and_changes_nothing(ws, uv):
    result = release.bump(str(ws), ["proj-a", "proj-a-extra"], today="2026-02-03")
    assert result.code == 3
    assert "packages/a-extra/CHANGELOG.md has no ## Unreleased heading" in result.lines[0]
    assert git(ws, "status", "--porcelain").stdout == ""
    assert not uv.exists()


def test_bump_refuses_a_dependents_requirement_the_new_version_misses(ws, uv):
    write(ws, "packages/a-extra/pyproject.toml",
          member_toml("proj-a-extra", "0.2.0", ["proj-a>=0.0.9,<0.2"]))
    write(ws, "packages/a/CHANGELOG.md", "## Unreleased\n\n- x\n")
    result = release.bump(str(ws), ["proj-a"], part="minor", today="2026-02-03")
    assert result.code == 3
    assert result.lines[0] == ("packages/a-extra/pyproject.toml: 'proj-a>=0.0.9,<0.2' "
                               "would not admit proj-a 0.2.0; change it in the same commit")
    assert "0.2.0" not in (ws / "packages/a/pyproject.toml").read_text()


def test_bump_through_the_cli(ws, uv, capsys):
    assert cli.main(["bump", "proj-a", "--path", str(ws)]) == 0
    assert "proj-a: 0.1.0 -> 0.1.1" in capsys.readouterr().out
    assert cli.main(["bump", "proj-c", "--path", str(ws)]) == 2


def test_next_version_starts_lower_parts_again():
    assert release.next_version("1.2.3", "patch") == "1.2.4"
    assert release.next_version("1.2", "patch") == "1.2.1"
    assert release.next_version("1.2.3", "minor") == "1.3.0"
    assert release.next_version("1.2.3rc1", "major") == "2.0.0"
