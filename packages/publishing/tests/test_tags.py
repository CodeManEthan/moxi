"""Release tags and the tag gate: the scheme read against members, CHANGELOG headings, the
workspace at a commit, and each of the gate's seven checks both ways, with GitHub and the
package index faked."""

from __future__ import annotations

import hashlib

import pytest
from conftest import fake_web, git, member_toml, release_files, write

from moxi_publishing import tags


def head(repo, rev="HEAD"):
    return git(repo, "rev-parse", rev).stdout.strip()


def where(main_ref="refs/remotes/origin/main", github="operator/proj-a"):
    return tags.Where(main_ref, "main", github)


def run_gate(repo, fake, name, *, at="HEAD", annotated=True, **kwargs):
    """Make tag `name` at `at` and run the gate on it."""
    git(repo, "tag", *(["-a", "-m", name] if annotated else []), name, at)
    return tags.gate(tags.repo_git(str(repo)), name, where=kwargs.pop("where", where()),
                     tag_object=head(repo, f"refs/tags/{name}"), web=fake_web(fake),
                     **kwargs)


def failed(verdict, number, text):
    bad = {c.number: c.said for c in verdict.checks if not c.passed}
    assert number in bad, verdict.lines()
    assert text in bad[number], bad[number]
    assert not verdict.passed


# -- the scheme -----------------------------------------------------------------------------

@pytest.mark.parametrize("name, want", [
    ("proj-a-v0.1.0", ("proj-a", "0.1.0")),
    ("proj-a-extra-v0.2.0", ("proj-a-extra", "0.2.0")),
    ("proj-a-v1.0rc1", ("proj-a", "1.0rc1")),
    ("proj-a-v01.0", None),
    ("proj-a-v1.0-rc1", None),
    ("proj-a-vnext", None),
    ("proj-b-v1.0", None),
    ("v0.1.1", None),
])
def test_a_tag_is_read_against_the_members_by_longest_prefix(name, want):
    assert tags.parse_tag(name, {"proj-a", "proj-a-extra"}) == want


def test_the_tag_name_is_the_distribution_and_the_version():
    assert tags.tag_name("proj-a", "0.1.0") == "proj-a-v0.1.0"


# -- CHANGELOG headings ---------------------------------------------------------------------

def test_the_first_version_heading_and_its_date():
    text = ("# Changelog\n\n## Unreleased\n\n```\n## 9.9.9 (2026-01-01)\n```\n\n"
            "## 0.1.0 (2026-01-05)\n\n- First.\n")
    heading = tags.first_version_heading(text)
    assert (heading.version, heading.date, heading.number) == ("0.1.0", "2026-01-05", 9)
    assert tags.first_version_heading("## 0.1.0 (unreleased)\n").date is None
    assert tags.first_version_heading("## 0.1.0\n").date is None
    assert tags.first_version_heading("## Unreleased\n- x\n") is None


def test_a_section_runs_to_the_next_heading_of_its_level():
    text = ("## 0.2.0 (2026-01-06)\n\n- New.\n\n### Fixed\n\n- A fix.\n\n"
            "## 0.1.0 (2026-01-05)\n\n- First.\n")
    assert tags.changelog_section(text, "0.2.0") == "- New.\n\n### Fixed\n\n- A fix."
    assert tags.changelog_section(text, "0.1") == "- First."
    assert tags.changelog_section(text, "0.3.0") is None


# -- the workspace at a commit --------------------------------------------------------------

def test_the_workspace_is_read_at_the_commit_not_the_work_tree(repo):
    release_files(repo, not_ready=["proj-a"])
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "The workspace")
    write(repo, "packages/a/pyproject.toml", member_toml("proj-a", "9.9.9"))
    ws = tags.workspace(tags.repo_git(str(repo)), head(repo))
    assert sorted(ws.members) == ["proj-a", "proj-a-extra"]
    assert ws.members["proj-a"].version == "0.1.0"
    assert ws.members["proj-a-extra"].file("CHANGELOG.md") == "packages/a-extra/CHANGELOG.md"
    assert ws.not_ready() == {"proj-a"}
    assert ws.setting("check-job") == "linux"


def test_a_root_package_is_its_own_member_and_globs_are_read(repo):
    write(repo, "pyproject.toml", member_toml("Proj_A", "1.0"))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "One package")
    ws = tags.workspace(tags.repo_git(str(repo)), head(repo))
    assert list(ws.members) == ["proj-a"] and ws.members["proj-a"].path == "."
    assert ws.members["proj-a"].file("CHANGELOG.md") == "CHANGELOG.md"
    write(repo, "pyproject.toml", '[tool.uv.workspace]\nmembers = ["packages/*"]\n')
    write(repo, "packages/one/pyproject.toml", member_toml("one", "1.0"))
    write(repo, "packages/docs/README.md", "not a package\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Globbed")
    assert list(tags.workspace(tags.repo_git(str(repo)), head(repo)).members) == ["one"]


def test_a_bad_not_ready_list_is_an_error(repo):
    write(repo, "pyproject.toml", '[project]\nname = "x"\n[tool.moxi-publishing]\n'
                                  'not-ready = "x"\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Bad")
    ws = tags.workspace(tags.repo_git(str(repo)), head(repo))
    with pytest.raises(tags.ReleaseError, match="not-ready"):
        ws.not_ready()


# -- the gate -------------------------------------------------------------------------------

def test_a_release_tag_on_green_main_passes_every_check(released_repo, fake):
    verdict = run_gate(released_repo, fake, "proj-a-extra-v0.2.0")
    assert verdict.passed, verdict.lines()
    assert [c.number for c in verdict.checks] == [1, 2, 3, 4, 5, 6, 7]
    assert (verdict.dist, verdict.version) == ("proj-a-extra", "0.2.0")
    assert any("extra more" in note for note in verdict.notes)


def test_check_1_wants_an_annotated_tag_of_the_members_version(released_repo, fake):
    failed(run_gate(released_repo, fake, "proj-a-v0.1.0", annotated=False), 1,
           "lightweight")
    failed(run_gate(released_repo, fake, "proj-a-v0.1.1"), 1, "is 0.1.0, not 0.1.1")
    failed(run_gate(released_repo, fake, "proj-b-v0.1.0"), 1, "names no member")
    tree = head(released_repo, "HEAD^{tree}")
    git(released_repo, "tag", "-a", "-m", "tree", "proj-a-v0.1.2", tree)
    verdict = tags.gate(tags.repo_git(str(released_repo)), "proj-a-v0.1.2", where=where(),
                        tag_object=head(released_repo, "refs/tags/proj-a-v0.1.2"),
                        web=fake_web(fake))
    failed(verdict, 1, "peels to a tree")


def test_check_1_without_the_tag_reads_the_name_it_will_make(released_repo, fake):
    verdict = tags.gate(tags.repo_git(str(released_repo)), "proj-a-v0.1.0", where=where(),
                        commit=head(released_repo), web=fake_web(fake))
    assert verdict.passed, verdict.lines()


@pytest.mark.parametrize("heading", ["## 0.1.0 (unreleased)", "## 0.1.0", "## 0.0.9 (2026-01-01)"])
def test_check_2_wants_the_version_dated_as_the_first_heading(repo, remote, fake, heading):
    release_files(repo, a_heading=heading)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Undated")
    git(repo, "push", "-q", "origin", "main")
    fake.green(head(repo))
    failed(run_gate(repo, fake, "proj-a-v0.1.0"), 2, "first version heading")


def test_check_3_wants_main_and_its_check_green(released_repo, fake):
    repo = released_repo
    fake.runs.clear()
    failed(run_gate(repo, fake, "proj-a-v0.1.0"), 3, "no successful run")
    git(repo, "tag", "-d", "proj-a-v0.1.0")
    fake.green(head(repo), conclusion="failure")
    failed(run_gate(repo, fake, "proj-a-v0.1.0"), 3, "no successful run")
    git(repo, "tag", "-d", "proj-a-v0.1.0")
    fake.runs.clear()
    fake.green(head(repo), job="linux (3.12)")
    assert run_gate(repo, fake, "proj-a-v0.1.0").passed
    git(repo, "tag", "-d", "proj-a-v0.1.0")
    failed(run_gate(repo, fake, "proj-a-v0.1.0", where=where(github=None)), 3,
           "no GitHub repo")
    git(repo, "tag", "-d", "proj-a-v0.1.0")
    failed(run_gate(repo, fake, "proj-a-v0.1.0", where=where("refs/remotes/origin/gone")),
           3, "fetch first")


def test_check_3_refuses_a_branch_commit_a_merge_brought_into_main(released_repo, fake):
    repo = released_repo
    git(repo, "switch", "-q", "-c", "topic")
    write(repo, "packages/a/notes.md", "a branch line\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "On the branch")
    side = head(repo)
    git(repo, "switch", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge topic", "topic")
    git(repo, "push", "-q", "origin", "main")
    fake.green(side)
    fake.green(head(repo))
    failed(run_gate(repo, fake, "proj-a-v0.1.0", at=side), 3, "first-parent line")
    git(repo, "tag", "-d", "proj-a-v0.1.0")
    assert run_gate(repo, fake, "proj-a-v0.1.0").passed


def test_check_3_wants_the_named_job_and_says_which_key_is_missing(repo, remote, fake):
    release_files(repo)
    text = (repo / "pyproject.toml").read_text().replace('check-job = "linux"\n', "")
    write(repo, "pyproject.toml", text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "No job")
    git(repo, "push", "-q", "origin", "main")
    fake.green(head(repo))
    failed(run_gate(repo, fake, "proj-a-v0.1.0"), 3, "has no check-job")


def test_check_4_refuses_a_package_on_the_not_ready_list(repo, remote, fake):
    release_files(repo, not_ready=["proj-a"])
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Not ready")
    git(repo, "push", "-q", "origin", "main")
    fake.green(head(repo))
    failed(run_gate(repo, fake, "proj-a-v0.1.0"), 4, "not-ready")


def ready_lists(repo, fake, not_ready, ready):
    """Commit the release workspace with both lists, push it and make its check green."""
    release_files(repo, not_ready=not_ready)
    with (repo / "pyproject.toml").open("a") as fh:
        fh.write(f"ready = {ready!r}\n".replace("'", '"'))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Lists")
    git(repo, "push", "-q", "origin", "main")
    fake.green(head(repo))
    fake.publish("proj-a", "0.0.9")


@pytest.mark.parametrize("not_ready, ready, says", [
    ([], ["proj-a-extra"], "on neither [tool.moxi-publishing] not-ready nor ready"),
    (["proj-a"], ["proj-a", "proj-a-extra"], "on both [tool.moxi-publishing] not-ready "
                                             "and ready"),
])
def test_check_4_counts_a_member_missing_from_the_ready_list_as_not_ready(
        repo, remote, fake, not_ready, ready, says):
    ready_lists(repo, fake, not_ready, ready)
    verdict = run_gate(repo, fake, "proj-a-v0.1.0")
    failed(verdict, 4, says)
    failed(verdict, 4, "put it on not-ready until it is confirmed")


def test_check_4_passes_a_member_on_the_ready_list_only(repo, remote, fake):
    ready_lists(repo, fake, ["proj-a-extra"], ["proj-a"])
    verdict = run_gate(repo, fake, "proj-a-v0.1.0")
    assert verdict.passed, verdict.lines()


def test_unlisted_names_members_on_neither_or_both_and_nothing_without_ready(repo):
    release_files(repo, not_ready=["proj-a"])
    ws = tags.work_tree_workspace(str(repo))
    assert ws.unlisted() == {}
    with (repo / "pyproject.toml").open("a") as fh:
        fh.write('ready = ["proj-a"]\n')
    assert tags.work_tree_workspace(str(repo)).unlisted() == {
        "proj-a": "on both [tool.moxi-publishing] not-ready and ready",
        "proj-a-extra": "on neither [tool.moxi-publishing] not-ready nor ready"}


def test_check_5_wants_a_published_sibling_the_requirement_admits(released_repo, fake):
    fake.index.clear()
    failed(run_gate(released_repo, fake, "proj-a-extra-v0.2.0"), 5, "proj-a>=0.0.9")
    git(released_repo, "tag", "-d", "proj-a-extra-v0.2.0")
    fake.publish("proj-a", "0.0.9", yanked=True)
    failed(run_gate(released_repo, fake, "proj-a-extra-v0.2.0"), 5, "release those first")


def test_check_6_wants_the_version_new_or_held_as_this_builds_files(released_repo, fake,
                                                                     tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "proj_a-0.1.0.tar.gz").write_bytes(b"sdist bytes")
    (dist / "proj_a-0.1.0-py3-none-any.whl").write_bytes(b"wheel bytes")
    fake.publish("proj-a", "0.1.0", sha256=hashlib.sha256(b"sdist bytes").hexdigest())
    failed(run_gate(released_repo, fake, "proj-a-v0.1.0"), 6, "used up")
    git(released_repo, "tag", "-d", "proj-a-v0.1.0")
    verdict = run_gate(released_repo, fake, "proj-a-v0.1.0", artifact=str(dist))
    assert verdict.passed, verdict.lines()
    assert "publishing skips those" in verdict.checks[5].said
    git(released_repo, "tag", "-d", "proj-a-v0.1.0")
    assert run_gate(released_repo, fake, "proj-a-v0.1.0", files_checked_later=True).passed
    git(released_repo, "tag", "-d", "proj-a-v0.1.0")
    fake.publish("proj-a", "0.1.0", sha256="1" * 64, filename="other.whl")
    failed(run_gate(released_repo, fake, "proj-a-v0.1.0", artifact=str(dist)), 6,
           "other.whl")


def test_a_read_that_fails_refuses_its_check_and_names_it(released_repo, fake):
    fake.broken.add("/pypi/proj-a-extra/0.2.0")
    failed(run_gate(released_repo, fake, "proj-a-extra-v0.2.0"), 6,
           "the index's page of proj-a-extra 0.2.0 can't be read: HTTP 500")


@pytest.fixture
def core_repo(repo, remote, fake):
    """A workspace whose one member is the core, holding a catalogue."""
    write(repo, "pyproject.toml", '[tool.uv.workspace]\nmembers = ["packages/moxi", '
          '"packages/proj-b"]\n[tool.moxi-publishing]\ncheck-workflow = "ci.yml"\n'
          'check-job = "linux"\n')
    write(repo, "packages/moxi/pyproject.toml", member_toml("moxi", "0.2.0"))
    write(repo, "packages/moxi/CHANGELOG.md", "## 0.2.0 (2026-01-07)\n\n- Core.\n")
    write(repo, "packages/proj-b/pyproject.toml", member_toml("moxi-proj-b", "0.1.0"))
    write(repo, "packages/moxi/src/moxi/catalogue.toml",
          'version = 1\n[[module]]\nname = "proj-b"\ndistribution = "moxi-proj-b"\n'
          'summary = "B."\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Core")
    git(repo, "push", "-q", "origin", "main")
    fake.green(head(repo))
    return repo


def test_check_7_holds_the_cores_catalogue_to_released_versions(core_repo, fake):
    failed(run_gate(core_repo, fake, "moxi-v0.2.0"), 7, "moxi-proj-b")
    git(core_repo, "tag", "-d", "moxi-v0.2.0")
    fake.publish("moxi-proj-b", "0.1.0")
    failed(run_gate(core_repo, fake, "moxi-v0.2.0"), 7, "no released version")
    git(core_repo, "tag", "-d", "moxi-v0.2.0")
    fake.tags.append("moxi-proj-b-v0.1.0")
    assert run_gate(core_repo, fake, "moxi-v0.2.0").passed


def test_check_7_lets_an_entry_marked_unreleased_pass(core_repo, fake):
    path = core_repo / "packages/moxi/src/moxi/catalogue.toml"
    write(core_repo, "packages/moxi/src/moxi/catalogue.toml",
          path.read_text() + "unreleased = true\n")
    git(core_repo, "commit", "-q", "-am", "Unreleased")
    git(core_repo, "push", "-q", "origin", "main")
    fake.green(head(core_repo))
    assert run_gate(core_repo, fake, "moxi-v0.2.0").passed


def test_the_token_goes_only_to_githubs_own_api():
    seen = {}

    class Web(tags.Web):
        def fetch(self, url, headers):
            seen[url] = headers
            return 200, b"{}"

    Web(token="secret-value").github("/repos/o/r", "it")
    Web(github_api="http://github.example.test", token="secret-value").github("/repos/o/r", "it")
    assert seen["https://api.github.com/repos/o/r"]["Authorization"] == "Bearer secret-value"
    assert "Authorization" not in seen["http://github.example.test/repos/o/r"]

