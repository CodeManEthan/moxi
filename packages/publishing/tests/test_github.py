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


PYPROJECT = """[project]
name = "proj-a"
version = "0.1.0"

[tool.moxi-publishing]
not-ready = ["proj-a"]
check-workflow = "ci.yml"
check-job = "linux"
"""
#: What `show` and `apply` say when they make `moxi-main` require the check.
NOTE = ("note: with linux required, GitHub moves main only to a commit whose linux check "
        "has already passed on another ref, so .github/workflows/ci.yml must run linux on "
        "pushes to branches other than main; a check that runs only on main can never pass "
        "first, and main could no longer move")
WANT_CHECK = "want linux from GitHub Actions (app 15368)"


@pytest.fixture
def gh_repo(repo):
    git(repo, "config", flag.GITHUB, "alice/proj-a")
    (repo / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    return repo


def without_check_job(repo, text=None):
    (repo / "pyproject.toml").write_text(
        PYPROJECT.replace('check-job = "linux"\n', "") if text is None else text,
        encoding="utf-8")


def show(repo, gh):
    result = github.show(str(repo), run=gh)
    assert result.code == 0
    return result.lines


def main_rules(gh):
    return gh.answers[f"GET {REPO}/rulesets/101"]["rules"]


def held_check(gh):
    return next(rule for rule in main_rules(gh) if rule["type"] == "required_status_checks")


def test_show_prints_each_difference_field_by_field(gh_repo):
    lines = show(gh_repo, FakeGh("today.json"))
    assert lines[0] == "alice/proj-a: default branch main, reviewer alice (gh's account)"
    body = lines[1:-2]
    assert body == [
        f"ruleset moxi-main: required checks: has none; {WANT_CHECK}",
        "ruleset moxi-tags-create: exclude: has refs/tags/v*; want refs/tags/*-v*",
        "ruleset moxi-only-main: not expected (branch ruleset); a public repo keeps only "
        "moxi-main, moxi-tags and moxi-tags-create",
        "secret scanning: has disabled; want enabled",
        "push protection: has disabled; want enabled (GitHub then refuses a push it finds a "
        "secret in, and links a way past it)",
        "environment pypi: required reviewers: has bob; want alice",
        "environment pypi: prevent self-review: has true; want false (the reviewer pushes "
        "the tags)",
        "environment pypi: deployment refs: tags *-v* missing",
        "environment pypi: deployment refs: has branch main; want tags *-v* only",
        NOTE,
    ]
    assert lines[-2].startswith("PyPI trusted publisher: can't be read from GitHub; ")
    assert ("(proj-a) needs one for owner alice, repository proj-a, workflow release.yml, "
            "environment pypi" in lines[-2])
    assert lines[-1] == "9 difference(s)"


def test_show_finds_nothing_on_a_settled_repo_and_leaves_a_push_ruleset(gh_repo):
    lines = show(gh_repo, FakeGh("settled.json"))
    assert len(lines) == 3
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
                rules=[{"type": "deletion"}, {"type": "required_linear_history"},
                       held_check(gh)])
    gh.answers[f"GET {REPO}/rulesets/104"]["bypass_actors"] = [
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
    lines = show(gh_repo, gh)
    assert lines[1:5] == [
        "ruleset moxi-main: target: has tag; want branch",
        "ruleset moxi-main: enforcement: has evaluate; want active",
        "ruleset moxi-main: rules: has deletion, required_linear_history; want deletion, "
        "non_fast_forward",
        "ruleset moxi-tags-create: bypass actors: has 1; want none"]
    # moxi-main isn't in force on main today, so the check it lists is new to main.
    assert lines[5] == NOTE
    assert lines[6].startswith("PyPI")


@pytest.mark.parametrize(("checks", "has"), [
    ([], "none"),
    ([{"context": "build", "integration_id": 15368}],
     "build from GitHub Actions (app 15368)"),
    ([{"context": "linux", "integration_id": 15368}, {"context": "build",
                                                       "integration_id": 15368}],
     "build from GitHub Actions (app 15368), linux from GitHub Actions (app 15368)"),
])
def test_moxi_mains_required_check_is_judged_by_name(gh_repo, checks, has):
    gh = FakeGh("settled.json")
    held_check(gh)["parameters"]["required_status_checks"] = checks
    lines = show(gh_repo, gh)
    assert f"ruleset moxi-main: required checks: has {has}; {WANT_CHECK}" in lines
    # The note comes only when the write makes main need a check it doesn't need today.
    assert (NOTE in lines) is (not any(c["context"] == "linux" for c in checks))
    assert lines[-1] == "1 difference(s)"


@pytest.mark.parametrize(("source", "has"), [
    ({"integration_id": 4242}, "linux from app 4242"),
    ({}, "linux from any app"),
])
def test_moxi_mains_required_check_must_come_from_github_actions(gh_repo, source, has):
    gh = FakeGh("settled.json")
    held_check(gh)["parameters"]["required_status_checks"] = [{"context": "linux", **source}]
    lines = show(gh_repo, gh)
    assert lines[1] == f"ruleset moxi-main: required checks: has {has}; {WANT_CHECK}"
    assert NOTE not in lines


def test_moxi_main_is_rewritten_when_its_check_is_missing_or_strict(gh_repo):
    gh = FakeGh("settled.json")
    held_check(gh)["parameters"]["strict_required_status_checks_policy"] = True
    lines = show(gh_repo, gh)
    assert lines[1] == ("ruleset moxi-main: required_status_checks "
                        "strict_required_status_checks_policy: has true; want false")
    github.apply(str(gh_repo), run=gh)
    want = {w["name"]: w for w in github.expected_rulesets("main", "linux")}
    (method, path, body), = gh.writes
    assert (method, path) == ("PUT", f"{REPO}/rulesets/101")
    # The rule's parameter publishing doesn't set is carried as GitHub holds it.
    assert body["rules"][:2] == want["moxi-main"]["rules"][:2]
    assert body["rules"][2]["parameters"] == {
        **want["moxi-main"]["rules"][2]["parameters"], "do_not_enforce_on_create": False}
    gh = FakeGh("settled.json")
    main_rules(gh).remove(held_check(gh))
    result = github.apply(str(gh_repo), run=gh)
    assert gh.writes == [("PUT", f"{REPO}/rulesets/101", want["moxi-main"])]
    assert NOTE in result.lines


def test_with_no_check_job_the_check_is_left_and_one_github_holds_is_kept(gh_repo):
    without_check_job(gh_repo)
    gh = FakeGh("settled.json")
    lines = show(gh_repo, gh)
    left = ("ruleset moxi-main: required checks: no check-job in the root pyproject.toml's "
            "[tool.moxi-publishing], so none is judged; GitHub holds linux from GitHub "
            "Actions (app 15368), kept")
    assert lines[1:-2] == [left] and lines[-1] == "1 difference(s)"
    result = github.apply(str(gh_repo), run=gh)
    assert gh.writes == []
    assert [line for line in result.lines if line.startswith("left: ")] == [
        f"left: {left}; name the CI job that must pass before main moves as check-job, "
        "then run github apply again"]
    assert NOTE not in result.lines
    # A rewrite of moxi-main for another field keeps the check GitHub holds; with the
    # ruleset not in force today, that check is new to main.
    gh.answers[f"GET {REPO}/rulesets/101"]["enforcement"] = "evaluate"
    result = github.apply(str(gh_repo), run=gh)
    (_, path, body), = gh.writes
    assert path == f"{REPO}/rulesets/101" and held_check(gh) in body["rules"]
    assert NOTE in result.lines


def add_ruleset(gh, ruleset):
    gh.answers[f"GET {REPO}/rulesets?includes_parents=false&per_page=100"].append(
        {key: ruleset[key] for key in ("id", "name", "target", "enforcement")})
    gh.answers[f"GET {REPO}/rulesets/{ruleset['id']}"] = ruleset


def check_ruleset(name="ci-gate", include=("refs/heads/main",), exclude=(), **fields):
    """A branch ruleset requiring linux from GitHub Actions, active unless `fields` say."""
    return {"id": 110, "name": name, "target": "branch", "enforcement": "active",
            "bypass_actors": [],
            "conditions": {"ref_name": {"include": list(include), "exclude": list(exclude)}},
            "rules": [github.required_check_rule("linux")], **fields}


@pytest.mark.parametrize("held", [
    {"enforcement": "evaluate"},
    {"enforcement": "disabled"},
    {"conditions": {"ref_name": {"include": ["refs/heads/trunk"], "exclude": []}}},
    {"conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
     "default_branch": "trunk"},
    {"conditions": {"ref_name": {"include": ["~ALL"], "exclude": ["refs/heads/m*"]}}},
    {"bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole",
                        "bypass_mode": "always"}]},
])
def test_the_note_comes_when_moxi_main_holds_the_check_but_main_doesnt_require_it(
        gh_repo, held):
    gh = FakeGh("settled.json")
    held = dict(held)
    gh.answers[f"GET {REPO}"]["default_branch"] = held.pop("default_branch", "main")
    gh.answers[f"GET {REPO}/rulesets/101"].update(held)
    assert NOTE in show(gh_repo, gh)
    result = github.apply(str(gh_repo), run=gh)
    (method, path, body), = gh.writes
    assert (method, path) == ("PUT", f"{REPO}/rulesets/101")
    assert body["enforcement"] == "active" and body["bypass_actors"] == []
    assert body["conditions"]["ref_name"] == {"include": ["refs/heads/main"], "exclude": []}
    assert NOTE in result.lines


@pytest.mark.parametrize("include", [["~DEFAULT_BRANCH"], ["~ALL"]])
def test_no_note_when_moxi_main_already_requires_the_check_on_main(gh_repo, include):
    gh = FakeGh("settled.json")
    gh.answers[f"GET {REPO}/rulesets/101"]["conditions"]["ref_name"]["include"] = include
    lines = show(gh_repo, gh)
    assert f"ruleset moxi-main: include: has {include[0]}; want refs/heads/main" in lines
    assert NOTE not in lines


@pytest.mark.parametrize(("other", "noted"), [
    (check_ruleset(), False),
    (check_ruleset(include=["~ALL"]), False),
    (check_ruleset(enforcement="evaluate"), True),
    (check_ruleset(include=["refs/heads/ma*"]), True),
    (check_ruleset(include=["~ALL"], exclude=["refs/heads/release/*"]), True),
])
def test_the_note_weighs_what_another_ruleset_requires_on_main(gh_repo, other, noted):
    gh = FakeGh("settled.json")
    main_rules(gh).remove(held_check(gh))
    add_ruleset(gh, other)
    lines = show(gh_repo, gh)
    assert "ruleset moxi-main: required checks: has none; " + WANT_CHECK in lines
    assert (NOTE in lines) is noted
    # moxi-main missing altogether: the same.
    gh.answers[f"GET {REPO}/rulesets?includes_parents=false&per_page=100"].pop(0)
    lines = show(gh_repo, gh)
    assert "ruleset moxi-main: missing" in lines and (NOTE in lines) is noted


def test_the_note_names_each_new_check(gh_repo):
    without_check_job(gh_repo)
    gh = FakeGh("settled.json")
    gh.answers[f"GET {REPO}/rulesets/101"]["enforcement"] = "evaluate"
    held_check(gh)["parameters"]["required_status_checks"].append(
        {"context": "build", "integration_id": 15368})
    lines = show(gh_repo, gh)
    assert NOTE.replace("with linux required", "with build and linux required").replace(
        "whose linux check has", "whose build and linux checks have").replace(
        "must run linux", "must run build and linux") in lines


def test_a_rewrite_carries_each_parameter_github_holds_that_publishing_doesnt_set(gh_repo):
    gh = FakeGh("settled.json")
    held_check(gh)["parameters"]["do_not_enforce_on_create"] = True
    main_rules(gh)[0]["parameters"] = {"held": 1}
    assert show(gh_repo, gh)[-1] == "no difference"
    gh.answers[f"GET {REPO}/rulesets/101"]["enforcement"] = "evaluate"
    github.apply(str(gh_repo), run=gh)
    (_, _, body), = gh.writes
    rules = {rule["type"]: rule for rule in body["rules"]}
    assert rules["required_status_checks"]["parameters"] == {
        "strict_required_status_checks_policy": False, "do_not_enforce_on_create": True,
        "required_status_checks": [{"context": "linux", "integration_id": 15368}]}
    assert rules["non_fast_forward"]["parameters"] == {"held": 1}
    assert "parameters" not in rules["deletion"]


def test_with_no_check_job_apply_writes_moxi_mains_other_rules(gh_repo):
    without_check_job(gh_repo)
    gh = FakeGh("settled.json")
    gh.answers[f"GET {REPO}/rulesets?includes_parents=false&per_page=100"] = []
    result = github.apply(str(gh_repo), run=gh)
    method, path, body = gh.writes[0]
    assert (method, path, body["name"]) == ("POST", f"{REPO}/rulesets", "moxi-main")
    assert [r["type"] for r in body["rules"]] == ["deletion", "non_fast_forward"]
    assert any(line.startswith("left: ruleset moxi-main: required checks: no check-job ")
               and "GitHub holds none" in line for line in result.lines)
    assert NOTE not in result.lines


def test_a_check_job_that_cant_be_read_is_named(gh_repo):
    (gh_repo / "pyproject.toml").unlink()
    lines = show(gh_repo, FakeGh("settled.json"))
    assert any(line.startswith("ruleset moxi-main: required checks: check-job can't be "
                               "read: pyproject.toml is missing, so none is judged")
               for line in lines)
    without_check_job(gh_repo, "[project\n")
    lines = show(gh_repo, FakeGh("settled.json"))
    assert any("check-job can't be read: pyproject.toml can't be read" in line
               for line in lines)


def test_the_note_names_the_check_workflow_or_says_the_ci_workflow(gh_repo):
    without_check_job(gh_repo, PYPROJECT.replace('check-workflow = "ci.yml"\n', ""))
    lines = show(gh_repo, FakeGh("today.json"))
    assert NOTE.replace(".github/workflows/ci.yml", "the CI workflow") in lines


def test_show_says_when_the_github_environment_or_a_ruleset_is_missing(gh_repo):  # noqa: MX3 -- GitHub's own name for it
    gh = FakeGh("settled.json")
    del gh.answers[f"GET {REPO}/environments/pypi"]
    listing = gh.answers[f"GET {REPO}/rulesets?includes_parents=false&per_page=100"]
    listing[:] = [r for r in listing if r["name"] != "moxi-tags"]
    lines = show(gh_repo, gh)
    assert "ruleset moxi-tags: missing" in lines
    assert "environment pypi: missing" in lines
    assert NOTE not in lines


def test_show_says_secret_scanning_cant_be_read_without_admin(gh_repo):
    gh = FakeGh("settled.json")
    del gh.answers[f"GET {REPO}"]["security_and_analysis"]
    lines = show(gh_repo, gh)
    assert ("secret scanning: can't be read: it needs admin access to the repo" in lines)


def test_apply_writes_what_differs_and_leaves_the_rest(gh_repo):
    gh = FakeGh("today.json")
    result = github.apply(str(gh_repo), run=gh)
    assert result.code == 0
    want = {w["name"]: w for w in github.expected_rulesets("main", "linux")}
    env = f"{REPO}/environments/pypi"
    assert gh.writes == [
        ("PUT", f"{REPO}/rulesets/101", want["moxi-main"]),
        # The tag-creation ruleset GitHub holds is rewritten in place, never made anew.
        ("PUT", f"{REPO}/rulesets/104", want["moxi-tags-create"]),
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
                    "repo keeps only moxi-main, moxi-tags and moxi-tags-create; delete it on "
                    "GitHub, or: gh api --method DELETE repos/alice/proj-a/rulesets/102"]
    assert NOTE in result.lines
    assert result.lines[-1] == "6 write(s)"


def test_a_ruleset_still_named_tags_create_is_not_expected(gh_repo):
    gh = FakeGh("settled.json")
    for ruleset in (gh.answers[f"GET {REPO}/rulesets?includes_parents=false&per_page=100"][2],
                    gh.answers[f"GET {REPO}/rulesets/104"]):
        ruleset["name"] = "tags-create"
    lines = show(gh_repo, gh)
    assert "ruleset moxi-tags-create: missing" in lines
    assert ("ruleset tags-create: not expected (tag ruleset); a public repo keeps only "
            "moxi-main, moxi-tags and moxi-tags-create") in lines


def test_apply_makes_everything_on_a_bare_repo(gh_repo):
    gh = FakeGh("settled.json")
    gh.answers[f"GET {REPO}/rulesets?includes_parents=false&per_page=100"] = []
    del gh.answers[f"GET {REPO}/environments/pypi"]
    result = github.apply(str(gh_repo), run=gh)
    made = [(method, path, (body or {}).get("name")) for method, path, body in gh.writes]
    assert made == [
        ("POST", f"{REPO}/rulesets", "moxi-main"),
        ("POST", f"{REPO}/rulesets", "moxi-tags"),
        ("POST", f"{REPO}/rulesets", "moxi-tags-create"),
        ("PUT", f"{REPO}/environments/pypi", None),
        ("POST", f"{REPO}/environments/pypi/deployment-branch-policies", "*-v*"),
    ]
    for _, _, body in gh.writes[:3]:
        assert body["enforcement"] == "active" and body["bypass_actors"] == []
    assert NOTE in result.lines


def test_apply_writes_nothing_on_a_settled_repo(gh_repo):
    gh = FakeGh("settled.json")
    result = github.apply(str(gh_repo), run=gh)
    assert gh.writes == [] and result.lines[-1] == "nothing to write"
    assert NOTE not in result.lines


def test_the_expected_rulesets(gh_repo):
    found = {w["name"]: w for w in github.expected_rulesets("main", "linux")}
    assert found["moxi-main"]["conditions"]["ref_name"] == {
        "include": ["refs/heads/main"], "exclude": []}
    assert found["moxi-main"]["rules"] == [
        {"type": "deletion"}, {"type": "non_fast_forward"},
        {"type": "required_status_checks", "parameters": {
            "strict_required_status_checks_policy": False,
            "required_status_checks": [{"context": "linux", "integration_id": 15368}]}}]
    assert found["moxi-tags-create"]["conditions"]["ref_name"] == {
        "include": ["refs/tags/**/*"], "exclude": ["refs/tags/*-v*"]}
    assert [r["type"] for r in found["moxi-tags"]["rules"]] == [
        "deletion", "non_fast_forward", "update"]
    bare = {w["name"]: w for w in github.expected_rulesets("main")}
    assert [r["type"] for r in bare["moxi-main"]["rules"]] == ["deletion", "non_fast_forward"]


def test_check_job_is_read_from_the_root_pyproject(gh_repo):
    assert github.check_job(str(gh_repo)) == github.CheckJob("linux", "ci.yml")
    without_check_job(gh_repo)
    found = github.check_job(str(gh_repo))
    assert found.job is None and found.workflow == "ci.yml"
    assert found.missing == ("no check-job in the root pyproject.toml's "
                             "[tool.moxi-publishing]")


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
