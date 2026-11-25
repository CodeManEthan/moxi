"""`moxi publishing workflows write`, and the two workflow files it writes."""

import os
import re
from pathlib import Path

import pytest

from moxi_publishing import cli, release

REPO_ROOT = Path(__file__).resolve().parents[3]
PIN = re.compile(r"^\s*-?\s*uses: ([\w.-]+/[\w./-]+)@([0-9a-f]{40})  # v\d+\.\d+\.\d+$")


def jobs(text: str) -> dict[str, str]:
    """Each job's block of a workflow file, by job name."""
    body = text.split("\njobs:\n", 1)[1]
    found = {}
    for block in re.split(r"\n(?=  [\w-]+:\n)", "\n" + body):
        match = re.match(r"\n?  ([\w-]+):\n", block)
        if match:
            found[match.group(1)] = block
    return found


def needs(block: str) -> str | None:
    match = re.search(r"^    needs: ([\w-]+)$", block, re.M)
    return match.group(1) if match else None


@pytest.fixture(params=release.WORKFLOWS)
def name(request):
    return request.param


def test_the_committed_files_equal_the_templates(name):
    committed = REPO_ROOT / ".github" / "workflows" / name
    assert committed.read_bytes() == release.template(name)


def test_write_puts_both_files_in_place_then_leaves_them(repo, capsys):
    assert cli.main(["workflows", "write", str(repo)]) == 0
    out = capsys.readouterr().out
    for each in release.WORKFLOWS:
        target = repo / ".github" / "workflows" / each
        assert target.read_bytes() == release.template(each)
        if os.name != "nt":  # mode bits are POSIX's
            assert target.stat().st_mode & 0o777 == 0o644
        assert f".github/workflows/{each}: written" in out
    assert cli.main(["workflows", "write", str(repo)]) == 0
    assert "publishing.yml: unchanged" in capsys.readouterr().out


def test_write_replaces_a_changed_file_and_keeps_others(repo, capsys):
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "release.yml").write_text("name: mine\n")
    (workflows / "ci.yml").write_text("name: check\n")
    assert cli.main(["workflows", "write", str(repo)]) == 0
    assert ".github/workflows/release.yml: replaced" in capsys.readouterr().out
    assert (workflows / "release.yml").read_bytes() == release.template("release.yml")
    assert (workflows / "ci.yml").read_text() == "name: check\n"


def test_a_workflow_file_with_crlf_lines_reads_as_its_template(repo, capsys):
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for each in release.WORKFLOWS:
        (workflows / each).write_bytes(release.template(each).replace(b"\n", b"\r\n"))
    assert release.is_template((workflows / "release.yml").read_bytes(), "release.yml")
    assert not release.is_template(b"name: mine\r\n", "release.yml")
    assert cli.main(["workflows", "write", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "publishing.yml: unchanged" in out and "release.yml: unchanged" in out


def test_every_action_is_pinned_to_a_commit(name):
    uses = [line for line in release.template(name).decode().split("\n")
            if "uses:" in line and not line.lstrip().startswith("#")]
    assert uses
    for line in uses:
        assert PIN.match(line), line


def test_the_release_jobs_run_in_order_publish_before_release():
    text = release.template("release.yml").decode()
    found = jobs(text)
    order = ["gate", "scan", "build", "siblings", "verify", "publish", "release"]
    assert list(found) == order
    assert [needs(found[job]) for job in order] == [None] + order[:-1]
    assert 'tags: ["*-v*"]' in text and "workflow_dispatch:" in text


def test_the_build_is_made_once_and_passed_on():
    found = jobs(release.template("release.yml").decode())
    built = [job for job, block in found.items() if "uv build" in block]
    assert built == ["build"]
    assert "actions/upload-artifact@" in found["build"]
    for job in ("siblings", "verify", "publish", "release"):
        assert "actions/download-artifact@" in found[job], job
        assert "name: dist" in found[job]
    assert "--artifact dist" in found["siblings"]
    assert "ci siblings \"$TAG\" --artifact dist --resolution highest" in found["build"]
    # A publish that stopped halfway goes on: the gate compares the index's files with
    # the build's, and the upload skips the ones there.
    assert "ci gate \"$TAG\" --github \"$GITHUB_REPOSITORY\" --artifact dist" in found["verify"]
    assert "skip-existing: true" in found["publish"]
    assert "attestations: true" in found["publish"]


def test_each_job_holds_only_the_permissions_it_needs():
    text = release.template("release.yml").decode()
    found = jobs(text)
    assert "permissions:\n  contents: read\n" in text
    assert [job for job, b in found.items() if "id-token: write" in b] == ["publish"]
    assert [job for job, b in found.items() if "contents: write" in b] == ["release"]
    assert sorted(job for job, b in found.items() if "actions: read" in b) == [
        "gate", "verify"]
    assert [job for job, b in found.items() if "environment: pypi" in b] == ["publish"]
    # The job holding the write token runs no code of the repo's.
    assert "uv " not in found["release"] and "actions/checkout" not in found["release"]


def test_the_job_that_can_get_an_upload_token_runs_only_pinned_actions():
    found = jobs(release.template("release.yml").decode())
    publish = found["publish"]
    steps = [line.strip() for line in publish.split("\n")
             if re.match(r"^      - ", line)]
    assert steps and all(PIN.match(step) for step in steps), steps
    assert not re.search(r"^\s+(- )?run:", publish, re.M)
    assert "actions/checkout" not in publish and "uv " not in publish
    assert [step.split("@")[0] for step in steps] == [
        "- uses: actions/download-artifact", "- uses: pypa/gh-action-pypi-publish"]
    # The gate the upload needs runs in a job that holds no upload token.
    assert "id-token" not in found["verify"] and "environment:" not in found["verify"]


def test_the_release_file_says_which_rerun_is_for_which_failure():
    text = release.template("release.yml").decode()
    for words in ("Re-run failed jobs", "never \"Re-run all\n#   jobs\"", "gh workflow run",
                  "re-run the verify job", "release job", "used up"):
        assert words in text


def test_the_scan_runs_on_every_branch_and_tag_with_the_pushed_commits():
    text = release.template("publishing.yml").decode()
    assert 'branches: ["**"]' in text and 'tags: ["**"]' in text
    assert 'moxi publishing scan --ci --rev "$rev" --from "$base"' in text
    assert 'base="origin/$DEFAULT_BRANCH"' in text
    assert "permissions:\n  contents: read\n" in text
