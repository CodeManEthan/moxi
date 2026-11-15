"""`moxi publishing tag`: the gate at main's tip before the tag exists, an annotated tag
whose message is the CHANGELOG section only on a pass, and nothing pushed; and the same
verdict from `tag`, the push piece and `ci gate`."""

from __future__ import annotations

import pytest
from conftest import fake_web, git, public_repo, release_files

from moxi_publishing import cli, release


def head(repo, rev="HEAD"):
    return git(repo, "rev-parse", rev).stdout.strip()


def tags_here(repo):
    return git(repo, "tag", "--list").stdout.split()


def test_tag_makes_the_annotated_tag_at_mains_tip(released_repo, fake, remote):
    result = release.tag(str(released_repo), "proj-a", web=fake_web(fake))
    assert result.code == 0, result.lines
    assert "made proj-a-v0.1.0" in result.lines[-1]
    assert git(released_repo, "cat-file", "-t", "proj-a-v0.1.0").stdout.strip() == "tag"
    assert head(released_repo, "proj-a-v0.1.0^{commit}") == head(released_repo)
    message = git(released_repo, "tag", "-l", "--format=%(contents)", "proj-a-v0.1.0").stdout
    assert message.startswith("proj-a 0.1.0\n\n- First.\n\n### Fixed\n\n- A fix.")
    assert "Older" not in message
    assert git(remote, "tag", "--list").stdout == ""


def test_tag_reads_mains_tip_not_the_checkout(released_repo, fake):
    git(released_repo, "commit", "-q", "--allow-empty", "-m", "Local only")
    assert release.tag(str(released_repo), "proj-a", web=fake_web(fake)).code == 0
    assert head(released_repo, "proj-a-v0.1.0^{commit}") == head(released_repo, "HEAD^")


def test_tag_makes_nothing_when_the_gate_refuses(released_repo, fake):
    fake.runs.clear()
    result = release.tag(str(released_repo), "proj-a", web=fake_web(fake))
    assert result.code == 3
    assert any(line.startswith("check 3 (main and green): refused") for line in result.lines)
    assert "proj-a-v0.1.0 was not made" in result.lines
    assert tags_here(released_repo) == []


def test_tag_refuses_an_unknown_package_and_an_existing_tag(released_repo, fake):
    from moxi_publishing.flag import Failure

    with pytest.raises(Failure, match="no workspace member"):
        release.tag(str(released_repo), "proj-c", web=fake_web(fake))
    git(released_repo, "tag", "-a", "-m", "x", "proj-a-v0.1.0")
    with pytest.raises(Failure, match="exists already"):
        release.tag(str(released_repo), "proj-a", web=fake_web(fake))


def test_tag_wants_mains_tracking_ref(repo, fake):
    from moxi_publishing.flag import Failure

    with pytest.raises(Failure, match="fetch first"):
        release.tag(str(repo), "proj-a", web=fake_web(fake))


def test_the_cli_runs_the_gate_against_the_served_fakes(released_repo, served, capsys):
    assert cli.main(["tag", "proj-a-extra", "--path", str(released_repo)]) == 0
    out = capsys.readouterr().out
    assert "check 7 (catalogue): ok" in out and "made proj-a-extra-v0.2.0" in out


# -- the same verdict in all three places ---------------------------------------------------

def push(repo, *args):
    return git(repo, "push", "-q", *args, check=False)


@pytest.mark.parametrize("ready", [True, False])
def test_tag_push_and_ci_gate_agree(repo, names, remote, served, monkeypatch, ready):
    fake = served
    release_files(repo, not_ready=[] if ready else ["proj-a"])
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "The workspace")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "config", "moxi-publishing.github", "operator/proj-a")
    fake.green(head(repo))
    public_repo(repo, "publishing/push", sets=("main=main",))

    made = release.tag(str(repo), "proj-a")
    assert (made.code == 0) is ready, made.lines
    if not ready:
        assert any("check 4 (ready): refused" in line for line in made.lines)
        git(repo, "tag", "-a", "-m", "proj-a 0.1.0", "proj-a-v0.1.0")
    done = push(repo, "origin", "proj-a-v0.1.0")
    assert (done.returncode == 0) is ready, done.stderr
    if not ready:
        assert "check 4 (ready): refused" in done.stderr
    monkeypatch.chdir(repo)
    gated = release.ci_gate(".", "proj-a-v0.1.0")
    assert (gated.code == 0) is ready, gated.lines
    assert [line.split(":")[1] for line in gated.lines if line.startswith("check")] == [
        line.split(":")[1] for line in made.lines if line.startswith("check")]


def test_the_push_piece_refuses_a_release_shaped_tag_that_names_no_member(
        released_repo, names, served):
    public_repo(released_repo, "publishing/push", sets=("main=main",))
    git(released_repo, "tag", "-a", "-m", "x", "proj-c-v1.0")
    done = push(released_repo, "origin", "proj-c-v1.0")
    assert done.returncode != 0 and "names no member and version" in done.stderr


def test_ci_notes_prints_the_section(released_repo, monkeypatch, capsys):
    git(released_repo, "tag", "-a", "-m", "x", "proj-a-v0.1.0")
    monkeypatch.chdir(released_repo)
    assert cli.main(["ci", "notes", "proj-a-v0.1.0"]) == 0
    assert capsys.readouterr().out == "- First.\n\n### Fixed\n\n- A fix.\n"
    assert cli.main(["ci", "notes", "proj-a-v9.9.9"]) == 2
