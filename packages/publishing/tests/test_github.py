"""`moxi publishing github show|apply` against recorded `gh api` answers: each difference
printed field by field, the writes `apply` makes for them, and what it leaves alone."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import git

from moxi_publishing import cli, flag, github

DATA = Path(__file__).parent / "data" / "github"
REPO = "repos/alice/proj-a"


class FakeGh:
    """`gh api` answering GETs from a recorded file; writes are kept, never sent."""

    def __init__(self, recorded: str | dict):
        self.answers = (json.loads((DATA / recorded).read_text(encoding="utf-8"))
                        if isinstance(recorded, str) else recorded)
        self.writes: list[tuple[str, str, dict | None]] = []
        self.fail: dict[str, str] = {}

    def __call__(self, argv, *, stdin_data=None):
        assert argv[:3] == ["api", "--method", argv[2]] and "-H" in argv
        method, path = argv[2], argv[5]
        if path in self.fail:
            return subprocess.CompletedProcess(argv, 1, b"", self.fail[path].encode())
        if method != "GET":
            body = json.loads(stdin_data) if stdin_data is not None else None
            assert ("--input" in argv) == (body is not None)
            self.writes.append((method, path, body))
            return subprocess.CompletedProcess(argv, 0, b"{}", b"")
        key = f"GET {path}"
        if key not in self.answers:
            return subprocess.CompletedProcess(
                argv, 1, b'{"message":"Not Found","status":"404"}',
                b"gh: Not Found (HTTP 404)\n")
        return subprocess.CompletedProcess(argv, 0, json.dumps(self.answers[key]).encode(), b"")


@pytest.fixture
def gh_repo(repo):
    git(repo, "config", flag.GITHUB, "alice/proj-a")
    return repo


def show(repo, gh):
    result = github.show(str(repo), run=gh)
    assert result.code == 0
    return result.lines


def test_show_prints_each_difference_field_by_field(gh_repo):
    lines = show(gh_repo, FakeGh("today.json"))
    assert lines[0] == "alice/proj-a: default branch main, reviewer alice (gh's account)"
    body = lines[1:-2]
    assert body == [
        "ruleset tags-create: bypass actors: has 1; want none",
        "ruleset tags-create: exclude: has refs/tags/v*; want refs/tags/*-v*",
        "ruleset moxi-only-main: not expected (branch ruleset); a public repo keeps only "
        "moxi-main, moxi-tags and tags-create",
        "secret scanning: has disabled; want enabled",
        "push protection: has disabled; want enabled",
        "environment pypi: required reviewers: has bob; want alice",
        "environment pypi: prevent self-review: has true; want false (the reviewer pushes "
        "the tags)",
        "environment pypi: deployment refs: tags *-v* missing",
        "environment pypi: deployment refs: has branch main; want tags *-v* only",
    ]
    assert lines[-2].startswith("PyPI trusted publisher: can't be read from GitHub; ")
    assert "owner alice, repository proj-a, workflow release.yml, environment pypi" in lines[-2]
    assert lines[-1] == "9 difference(s)"


def test_show_finds_nothing_on_a_settled_repo_and_leaves_a_push_ruleset(gh_repo):
    lines = show(gh_repo, FakeGh("settled.json"))
    assert lines[1].startswith("PyPI trusted publisher")
    assert lines[-1] == "no difference"


def test_show_names_a_main_setting_unlike_the_default_branch(gh_repo):
    git(gh_repo, "config", "moxi-repo.piece.publishing/push.main", "trunk")
    lines = show(gh_repo, FakeGh("settled.json"))
    assert "ruleset moxi-main: include: has refs/heads/main; want refs/heads/trunk" in lines
    assert ("push piece main: has trunk; the repository's default branch is main"
            in lines)


def test_show_reads_every_field_of_a_ruleset(gh_repo):
    gh = FakeGh("settled.json")
    main = gh.answers[f"GET {REPO}/rulesets/101"]
    main.update(target="tag", enforcement="evaluate",
                rules=[{"type": "deletion"}, {"type": "required_linear_history"}])
    lines = show(gh_repo, gh)
    assert lines[1:4] == [
        "ruleset moxi-main: target: has tag; want branch",
        "ruleset moxi-main: enforcement: has evaluate; want active",
        "ruleset moxi-main: rules: has deletion, required_linear_history; want deletion, "
        "non_fast_forward"]
    assert lines[4].startswith("PyPI")


def test_show_says_when_the_deployment_or_a_ruleset_is_missing(gh_repo):
    gh = FakeGh("settled.json")
    del gh.answers[f"GET {REPO}/environments/pypi"]
    listing = gh.answers[f"GET {REPO}/rulesets?includes_parents=false&per_page=100"]
    listing[:] = [r for r in listing if r["name"] != "moxi-tags"]
    lines = show(gh_repo, gh)
    assert "ruleset moxi-tags: missing" in lines
    assert "environment pypi: missing" in lines


def test_show_says_secret_scanning_cant_be_read_without_admin(gh_repo):
    gh = FakeGh("settled.json")
    del gh.answers[f"GET {REPO}"]["security_and_analysis"]
    lines = show(gh_repo, gh)
    assert ("secret scanning: can't be read: it needs admin access to the repo" in lines)


def test_apply_writes_what_differs_and_leaves_the_rest(gh_repo):
    gh = FakeGh("today.json")
    result = github.apply(str(gh_repo), run=gh)
    assert result.code == 0
    want = {w["name"]: w for w in github.expected_rulesets("main")}
    env = f"{REPO}/environments/pypi"
    assert gh.writes == [
        ("PUT", f"{REPO}/rulesets/104", want["tags-create"]),
        ("PATCH", REPO, {"security_and_analysis": {
            "secret_scanning": {"status": "enabled"},
            "secret_scanning_push_protection": {"status": "enabled"}}}),
        ("PUT", env, {"reviewers": [{"type": "User", "id": 1001}],
                      "prevent_self_review": False,
                      "deployment_branch_policy": {"protected_branches": False,
                                                   "custom_branch_policies": True}}),
        ("POST", f"{env}/deployment-branch-policies", {"name": "*-v*", "type": "tag"}),
        ("DELETE", f"{env}/deployment-branch-policies/31", None),
    ]
    # The old branch ruleset is printed with its removal, never deleted.
    assert not any(path.endswith("/rulesets/102") for _, path, _ in gh.writes)
    left = [line for line in result.lines if line.startswith("left: ")]
    assert left == ["left: ruleset moxi-only-main: not expected (branch ruleset); a public "
                    "repo keeps only moxi-main, moxi-tags and tags-create; delete it on "
                    "GitHub, or: gh api --method DELETE repos/alice/proj-a/rulesets/102"]
    assert result.lines[-1] == "5 write(s)"


def test_apply_makes_everything_on_a_bare_repo(gh_repo):
    gh = FakeGh("settled.json")
    gh.answers[f"GET {REPO}/rulesets?includes_parents=false&per_page=100"] = []
    del gh.answers[f"GET {REPO}/environments/pypi"]
    github.apply(str(gh_repo), run=gh)
    made = [(method, path, (body or {}).get("name")) for method, path, body in gh.writes]
    assert made == [
        ("POST", f"{REPO}/rulesets", "moxi-main"),
        ("POST", f"{REPO}/rulesets", "moxi-tags"),
        ("POST", f"{REPO}/rulesets", "tags-create"),
        ("PUT", f"{REPO}/environments/pypi", None),
        ("POST", f"{REPO}/environments/pypi/deployment-branch-policies", "*-v*"),
    ]
    for _, _, body in gh.writes[:3]:
        assert body["enforcement"] == "active" and body["bypass_actors"] == []


def test_apply_writes_nothing_on_a_settled_repo(gh_repo):
    gh = FakeGh("settled.json")
    result = github.apply(str(gh_repo), run=gh)
    assert gh.writes == [] and result.lines[-1] == "nothing to write"


def test_the_expected_rulesets(gh_repo):
    found = {w["name"]: w for w in github.expected_rulesets("main")}
    assert found["moxi-main"]["conditions"]["ref_name"] == {
        "include": ["refs/heads/main"], "exclude": []}
    assert found["tags-create"]["conditions"]["ref_name"] == {
        "include": ["refs/tags/**/*"], "exclude": ["refs/tags/*-v*"]}
    assert [r["type"] for r in found["moxi-tags"]["rules"]] == [
        "deletion", "non_fast_forward", "update"]


def test_a_failed_read_fails(gh_repo):
    gh = FakeGh("settled.json")
    gh.fail[REPO] = "gh: Bad credentials (HTTP 401)\n"
    with pytest.raises(flag.Failure) as caught:
        github.show(str(gh_repo), run=gh)
    assert caught.value.code == 1 and "HTTP 401" in caught.value.text


def test_a_failed_write_stops_apply(gh_repo):
    gh = FakeGh("today.json")
    gh.fail[f"{REPO}/rulesets/104"] = "gh: Validation Failed (HTTP 422)\n"
    with pytest.raises(flag.Failure) as caught:
        github.apply(str(gh_repo), run=gh)
    assert "HTTP 422" in caught.value.text and gh.writes == []


def test_no_github_repo_is_a_usage_error(repo, capsys):
    assert cli.main(["github", "show", str(repo)]) == 2
    assert "--github OWNER/REPO" in capsys.readouterr().err


def test_gh_missing_is_named(gh_repo, monkeypatch, capsys):
    import shutil

    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name == "gh"
                        else which(name, *a, **k))
    assert cli.main(["github", "apply", str(gh_repo)]) == 1
    assert "gh is not on PATH" in capsys.readouterr().err
