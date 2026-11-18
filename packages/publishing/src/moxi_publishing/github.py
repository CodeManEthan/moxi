"""`moxi publishing github show|apply`: the GitHub rulesets and settings a public repo
needs, read back or written with `gh api`.

What a public repo expects of GitHub:

- ruleset `moxi-main` on the push piece's main branch: no deletion, no force push, and
  the check the root `pyproject.toml` names as `check-job` in `[tool.moxi-publishing]`
  required, from the GitHub Actions app only, not strict. GitHub then moves the branch
  only to a commit that check has already passed on, which for a direct push means a
  run on another ref first. With no `check-job` there is no check to require: the
  difference is printed and left, and a required check GitHub already holds is kept;
- no other branch ruleset: other branches are created, updated and deleted freely, and
  the push piece holds "deleted only once merged" locally;
- ruleset `moxi-tags` on every tag: no update, deletion or force push;
- ruleset `moxi-tags-create` on every tag but release tags (`*-v*`): no creation;
- secret scanning with push protection;
- environment `pypi`, deployable from release tags only, whose required reviewer is the
  account `gh` is signed in as, allowed to approve its own runs since that account pushes
  the tags. The release workflow's publish job runs in it, so that review is what stands
  between a tagged commit and the package index;
- a trusted publisher on the package index for each distribution, which GitHub can't
  show; `show` says so.

Each ruleset is active with no bypass actors, and its rules are compared with their
parameters, not by type alone: a parameter the expected rule doesn't set is GitHub's to
keep, and a rewrite carries it. `show` prints each difference field by field, and the
push piece's `main` setting when it isn't the repository's default branch. `apply`
writes the expected rulesets and settings; it deletes no ruleset and changes no branch,
and prints what it left for you. Where main would require after `apply` a check it
doesn't require today, both say what that asks of the CI workflow: a check that runs
only on pushes to main can never pass first elsewhere, so main could no longer move.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from moxi_publishing.flag import Failure
from moxi_publishing.release import Result
from moxi_publishing.tags import CHECK_JOB as CHECK_JOB_KEY
from moxi_publishing.tags import CHECK_WORKFLOW as CHECK_WORKFLOW_KEY
from moxi_publishing.tags import TABLE as TOOL_TABLE

#: How long one `gh api` call may take.
GH_TIMEOUT_S = 30.0
#: The GitHub environment the release workflow's publish job runs in.
GITHUB_ENVIRONMENT = "pypi"  # noqa: MX3 -- GitHub's own name for it
#: The release tags the environment may deploy from: `<distribution>-v<version>`.
RELEASE_TAGS = "*-v*"
#: The release workflow, as a trusted publisher on the package index names it.
WORKFLOW = "release.yml"
#: The two kinds of ref a ruleset may target that `show` judges.
_REF_TARGETS = ("branch", "tag")
#: The GitHub Actions app: the one source `moxi-main`'s required check is taken from, so a
#: commit status another token posts under the check's name doesn't meet it.
ACTIONS_APP_ID = 15368
#: The ruleset rule that requires checks, and the expected rulesets' names.
REQUIRED_CHECKS = "required_status_checks"
MAIN_RULESET = "moxi-main"
TAGS_RULESET = "moxi-tags"
TAGS_CREATE_RULESET = "moxi-tags-create"


@dataclass(frozen=True)
class Request:
    """One `gh api` write."""

    method: str
    path: str
    body: dict | None = None


@dataclass
class Difference:
    """One field of GitHub's state unlike what publishing expects, and the writes that
    would set it; no writes means `apply` leaves it to the user."""

    subject: str
    text: str
    writes: tuple[Request, ...] = ()
    left: str = ""
    #: The checks main requires once `apply` has made the writes and doesn't require
    #: today, by name; `differences` sets them on one difference at most.
    new_required_checks: tuple[str, ...] = ()

    def line(self) -> str:
        return f"{self.subject}: {self.text}"


@dataclass(frozen=True)
class CheckJob:
    """The check `moxi-main` requires, as the root `pyproject.toml` names it: `job` is
    `check-job`, `workflow` is `check-workflow`, and `missing` says why there is no job."""

    job: str | None
    workflow: str | None = None
    missing: str = ""


@dataclass
class State:
    """What `show` read from GitHub."""

    repo: dict
    rulesets: list[dict]
    github_environment: dict | None  # noqa: MX3 -- GitHub's own name for it
    tag_policies: list[dict] | None
    account: dict
    notes: list[str] = field(default_factory=list)


def required_check_rule(check: str) -> dict:
    """The rule requiring `check` from the GitHub Actions app, not strict (the branch
    need not be up to date with main first)."""
    return {"type": REQUIRED_CHECKS, "parameters": {
        "strict_required_status_checks_policy": False,
        "required_status_checks": [{"context": check, "integration_id": ACTIONS_APP_ID}]}}


def expected_rulesets(main: str, check: str | None = None) -> list[dict]:
    """The rulesets a public repo expects, as `gh api` takes them. `check` is the root
    `pyproject.toml`'s `check-job`; without one `moxi-main` requires no check."""

    def ruleset(name: str, target: str, include: list[str], exclude: list[str],
                rules: list[str]) -> dict:
        return {"name": name, "target": target, "enforcement": "active",
                "bypass_actors": [],
                "conditions": {"ref_name": {"include": include, "exclude": exclude}},
                "rules": [{"type": rule} for rule in rules]}

    main_ruleset = ruleset(MAIN_RULESET, "branch", [f"refs/heads/{main}"], [],
                           ["deletion", "non_fast_forward"])
    if check:
        main_ruleset["rules"].append(required_check_rule(check))
    return [
        main_ruleset,
        ruleset(TAGS_RULESET, "tag", ["refs/tags/**/*"], [],
                ["deletion", "non_fast_forward", "update"]),
        ruleset(TAGS_CREATE_RULESET, "tag", ["refs/tags/**/*"], [f"refs/tags/{RELEASE_TAGS}"],
                ["creation"]),
    ]


# -- gh -------------------------------------------------------------------------------------

class Gh:
    """`gh api` through `moxi.proc.run`, or through `run` in tests."""

    def __init__(self, run=None):
        import shutil

        if run is None:
            gh = shutil.which("gh")
            if gh is None:
                raise Failure("gh is not on PATH; github show and apply read and write "
                              "GitHub with it (https://cli.github.com, then gh auth "
                              "login)", 1)
            from moxi import proc

            def run(argv, *, stdin_data=None):
                return proc.run([gh, *argv], stdin_data=stdin_data, timeout_s=GH_TIMEOUT_S)
        self._run = run

    def _call(self, method: str, path: str, body: dict | None):
        import subprocess

        argv = ["api", "--method", method, "-H", "Accept: application/vnd.github+json", path]
        data = None
        if body is not None:
            argv += ["--input", "-"]
            data = json.dumps(body).encode("utf-8")
        try:
            done = self._run(argv, stdin_data=data)
        except subprocess.TimeoutExpired:
            raise Failure(f"gh api {method} {path} took over {GH_TIMEOUT_S:.0f} s", 1) from None
        except OSError as exc:
            raise Failure(f"gh can't be run: {exc}", 1) from None
        return done

    @staticmethod
    def _missing(done) -> bool:
        # gh prints the response body on stdout and "(HTTP 404)" on stderr for a 404.
        err = (done.stderr or b"").decode("utf-8", "replace")
        if "HTTP 404" in err:
            return True
        try:
            return str(json.loads(done.stdout or b"{}").get("status")) == "404"
        except (ValueError, AttributeError):
            return False

    @staticmethod
    def _problem(done) -> str:
        err = (done.stderr or b"").decode("utf-8", "replace").strip()
        return err.split("\n")[-1] if err else f"exit {done.returncode}"

    def get(self, path: str):
        """The JSON at `path`, or None when GitHub answers 404."""
        done = self._call("GET", path, None)
        if done.returncode != 0:
            if self._missing(done):
                return None
            raise Failure(f"gh api {path} failed: {self._problem(done)}", 1)
        try:
            return json.loads(done.stdout or b"null")
        except ValueError:
            raise Failure(f"gh api {path} gave no JSON", 1) from None

    def send(self, request: Request) -> None:
        done = self._call(request.method, request.path, request.body)
        if done.returncode != 0:
            raise Failure(f"gh api --method {request.method} {request.path} failed: "
                          f"{self._problem(done)}", 1)


# -- reading --------------------------------------------------------------------------------

def read(gh: Gh, github: str) -> State:
    """Everything `show` compares, read from GitHub."""
    repo = gh.get(f"repos/{github}")
    if repo is None:
        raise Failure(f"GitHub has no repo {github}, or gh's account can't see it", 1)
    summaries = gh.get(f"repos/{github}/rulesets?includes_parents=false&per_page=100") or []
    rulesets = []
    for summary in summaries:
        full = gh.get(f"repos/{github}/rulesets/{summary['id']}")
        if full is not None:
            rulesets.append(full)
    github_environment = gh.get(  # noqa: MX3 -- GitHub's own name for it
        f"repos/{github}/environments/{GITHUB_ENVIRONMENT}")
    policies = None
    if github_environment is not None and (
            github_environment.get("deployment_branch_policy") or {}).get(
            "custom_branch_policies"):
        found = gh.get(f"repos/{github}/environments/{GITHUB_ENVIRONMENT}"
                       "/deployment-branch-policies?per_page=100")
        policies = (found or {}).get("branch_policies", [])
    account = gh.get("user")
    if not account:
        raise Failure("gh is signed in to no account; run gh auth login", 1)
    return State(repo=repo, rulesets=rulesets, github_environment=github_environment,
                 tag_policies=policies, account=account)


def _names(items) -> str:
    return ", ".join(items) if items else "none"


def _checks(rule: dict | None) -> list[tuple[str, int | None]]:
    """The (check name, app id) pairs a `required_status_checks` rule requires."""
    items = ((rule or {}).get("parameters") or {}).get(REQUIRED_CHECKS) or []
    found = [(str(item.get("context")), item.get("integration_id"))
             for item in items if isinstance(item, dict)]
    return sorted(found, key=lambda pair: (pair[0], str(pair[1])))


def _checks_text(checks: list[tuple[str, int | None]]) -> str:
    def one(name: str, app: int | None) -> str:
        if app is None:
            return f"{name} from any app"
        if app == ACTIONS_APP_ID:
            return f"{name} from GitHub Actions (app {app})"
        return f"{name} from app {app}"

    return ", ".join(one(*pair) for pair in checks) if checks else "none"


def _ruleset_differences(github: str, have: dict | None, want: dict) -> list[Difference]:
    subject = f"ruleset {want['name']}"
    want_rules = {rule["type"]: rule for rule in want["rules"]}
    if have is None:
        return [Difference(subject, "missing",
                           (Request("POST", f"repos/{github}/rulesets", want),))]
    have_rules: dict[str, dict] = {}
    for rule in have.get("rules") or []:
        have_rules.setdefault(rule.get("type", "?"), rule)
    write = (Request("PUT", f"repos/{github}/rulesets/{have['id']}",
                     _carrying(want, have_rules)),)
    found = []

    def differ(name: str, has, wants) -> None:
        if has != wants:
            found.append(Difference(subject, f"{name}: has {has}; want {wants}", write))

    differ("target", have.get("target"), want["target"])
    differ("enforcement", have.get("enforcement"), want["enforcement"])
    actors = have.get("bypass_actors") or []
    if actors:
        found.append(Difference(subject, f"bypass actors: has {len(actors)}; want none",
                                write))
    ref_name = (have.get("conditions") or {}).get("ref_name") or {}
    for key in ("include", "exclude"):
        has = sorted(ref_name.get(key) or [])
        wants = sorted(want["conditions"]["ref_name"][key])
        if has != wants:
            found.append(Difference(subject, f"{key}: has {_names(has)}; want "
                                             f"{_names(wants)}", write))
    has = sorted(kind for kind in have_rules if kind != REQUIRED_CHECKS)
    wants = sorted(kind for kind in want_rules if kind != REQUIRED_CHECKS)
    if has != wants:
        found.append(Difference(subject, f"rules: has {_names(has)}; want {_names(wants)}",
                                write))
    has_checks = _checks(have_rules.get(REQUIRED_CHECKS))
    want_checks = _checks(want_rules.get(REQUIRED_CHECKS))
    if has_checks != want_checks:
        found.append(Difference(subject, f"required checks: has {_checks_text(has_checks)}; "
                                         f"want {_checks_text(want_checks)}", write))
    # A rule's parameters, where GitHub holds the rule: every one the expected rule sets.
    for kind, rule in want_rules.items():
        if kind not in have_rules:
            continue
        has_params = have_rules[kind].get("parameters") or {}
        for key, value in (rule.get("parameters") or {}).items():
            if key != REQUIRED_CHECKS and has_params.get(key) != value:
                found.append(Difference(
                    subject, f"{kind} {key}: has {json.dumps(has_params.get(key))}; want "
                             f"{json.dumps(value)}", write))
    return found


def _carrying(want: dict, have_rules: dict[str, dict]) -> dict:
    """`want` as a rewrite writes it: each rule GitHub holds keeps the parameters the
    expected rule doesn't set (`do_not_enforce_on_create`, say), rather than having them
    written back to GitHub's defaults."""
    rules = []
    for rule in want["rules"]:
        held = (have_rules.get(rule["type"]) or {}).get("parameters") or {}
        params = {**held, **(rule.get("parameters") or {})}
        rules.append({**rule, "parameters": params} if params else rule)
    return {**want, "rules": rules}


def _names_main(patterns, main: str, default: str | None) -> bool:
    """Whether a ruleset's `include` list names `main` for sure: by its full ref, `~ALL`,
    or `~DEFAULT_BRANCH` when main is the default branch. A pattern is not matched."""
    return any(p == f"refs/heads/{main}" or p == "~ALL"
               or (p == "~DEFAULT_BRANCH" and default == main) for p in patterns)


def _may_name_main(patterns, main: str, default: str | None) -> bool:
    """Whether an `exclude` list may leave `main` out: it names main as `include` would,
    or holds a pattern (`*`, `?`, `[`)."""
    return (_names_main(patterns, main, default)
            or any(re.search(r"[*?\[]", p) for p in patterns))


def _required_on_main(ruleset: dict, main: str, default: str | None) -> set[str]:
    """The checks `ruleset` makes main require today, by name: only where it surely
    applies (an active branch ruleset with no bypass actors that names main and can't
    leave it out). Where that isn't sure it counts as requiring none, so the note on a
    new required check errs toward being printed."""
    if (ruleset.get("target") != "branch" or ruleset.get("enforcement") != "active"
            or ruleset.get("bypass_actors")):
        return set()
    ref_name = (ruleset.get("conditions") or {}).get("ref_name") or {}
    if (not _names_main(ref_name.get("include") or [], main, default)
            or _may_name_main(ref_name.get("exclude") or [], main, default)):
        return set()
    return {name for rule in ruleset.get("rules") or [] if rule.get("type") == REQUIRED_CHECKS
            for name, _ in _checks(rule)}


def _github_environment_differences(  # noqa: MX3 -- GitHub's own name for it
        github: str, state: State) -> list[Difference]:
    subject = f"environment {GITHUB_ENVIRONMENT}"
    account = state.account
    put = Request("PUT", f"repos/{github}/environments/{GITHUB_ENVIRONMENT}", {
        "reviewers": [{"type": "User", "id": account["id"]}],
        "prevent_self_review": False,
        "deployment_branch_policy": {"protected_branches": False,
                                     "custom_branch_policies": True},
    })
    policies = f"repos/{github}/environments/{GITHUB_ENVIRONMENT}/deployment-branch-policies"
    add_tag = Request("POST", policies, {"name": RELEASE_TAGS, "type": "tag"})
    env = state.github_environment
    if env is None:
        return [Difference(subject, "missing", (put, add_tag))]
    found = []
    rules = env.get("protection_rules") or []
    review = next((r for r in rules if r.get("type") == "required_reviewers"), None)
    reviewers = sorted((r.get("reviewer") or {}).get("login", "?")
                       for r in (review or {}).get("reviewers") or [])
    if reviewers != [account["login"]]:
        found.append(Difference(subject, f"required reviewers: has {_names(reviewers)}; "
                                         f"want {account['login']}", (put,)))
    if review is not None and review.get("prevent_self_review"):
        found.append(Difference(subject, "prevent self-review: has true; want false "
                                         "(the reviewer pushes the tags)", (put,)))
    policy = env.get("deployment_branch_policy") or {}
    if not policy.get("custom_branch_policies") or policy.get("protected_branches"):
        has = "protected branches" if policy.get("protected_branches") else "any ref"
        found.append(Difference(subject, f"deployment refs: has {has}; want tags "
                                         f"{RELEASE_TAGS} only", (put, add_tag)))
        return found
    have = state.tag_policies or []
    if not any(p.get("name") == RELEASE_TAGS and p.get("type", "branch") == "tag"
               for p in have):
        found.append(Difference(subject, f"deployment refs: tags {RELEASE_TAGS} missing",
                                (add_tag,)))
    for p in have:
        if not (p.get("name") == RELEASE_TAGS and p.get("type", "branch") == "tag"):
            found.append(Difference(
                subject, f"deployment refs: has {p.get('type', 'branch')} {p.get('name')}; "
                         f"want tags {RELEASE_TAGS} only",
                (Request("DELETE", f"{policies}/{p['id']}"),)))
    return found


def _security_differences(github: str, repo: dict) -> list[Difference]:
    security = repo.get("security_and_analysis")
    if security is None:
        return [Difference("secret scanning", "can't be read: it needs admin access to "
                                              "the repo", left="needs admin access")]
    found = []
    for key, label in (("secret_scanning", "secret scanning"),
                       ("secret_scanning_push_protection", "push protection")):
        status = (security.get(key) or {}).get("status", "disabled")
        if status != "enabled":
            why = (" (GitHub then refuses a push it finds a secret in, and links a way "
                   "past it)" if key == "secret_scanning_push_protection" else "")
            found.append(Difference(label, f"has {status}; want enabled{why}", (Request(
                "PATCH", f"repos/{github}",
                {"security_and_analysis": {
                    "secret_scanning": {"status": "enabled"},
                    "secret_scanning_push_protection": {"status": "enabled"}}}),)))
    return found


def differences(github: str, state: State, *, main: str,
                check: CheckJob | None = None) -> list[Difference]:
    """Every field of `state` unlike what a public repo expects, in `show`'s order.
    `check` is the root `pyproject.toml`'s check job; none means it names none."""
    check = check or CheckJob(None, missing=f"no {CHECK_JOB_KEY} in the root "
                                            f"pyproject.toml's [tool.{TOOL_TABLE}]")
    found: list[Difference] = []
    by_name = {ruleset.get("name"): ruleset for ruleset in state.rulesets}
    wanted = expected_rulesets(main, check.job)
    held = next((rule for rule in (by_name.get(MAIN_RULESET) or {}).get("rules") or []
                 if rule.get("type") == REQUIRED_CHECKS), None)
    if not check.job and held is not None:
        # No check to require: one GitHub already holds is kept, never written away.
        wanted[0]["rules"].append({"type": REQUIRED_CHECKS,
                                   "parameters": held.get("parameters") or {}})
    for want in wanted:
        found += _ruleset_differences(github, by_name.get(want["name"]), want)
    # After apply, main requires what the other rulesets require today and what the
    # expected moxi-main requires (when moxi-main isn't written, GitHub's equals it).
    held_main = by_name.get(MAIN_RULESET)
    default = state.repo.get("default_branch")
    today: set[str] = set()
    after = {name for rule in wanted[0]["rules"] if rule["type"] == REQUIRED_CHECKS
             for name, _ in _checks(rule)}
    for ruleset in state.rulesets:
        required = _required_on_main(ruleset, main, default)
        today |= required
        if ruleset is not held_main:
            after |= required
    new = tuple(sorted(after - today))
    writing = next((each for each in found
                    if each.subject == f"ruleset {MAIN_RULESET}" and each.writes), None)
    if new and writing is not None:
        writing.new_required_checks = new
    if not check.job:
        kept = (f"GitHub holds {_checks_text(_checks(held))}, kept" if _checks(held)
                else "GitHub holds none")
        found.append(Difference(
            f"ruleset {MAIN_RULESET}",
            f"required checks: {check.missing}, so none is judged; {kept}",
            left=f"name the CI job that must pass before {main} moves as {CHECK_JOB_KEY}, "
                 "then run github apply again"))
    names = {want["name"] for want in wanted}
    for ruleset in state.rulesets:
        if ruleset.get("name") not in names and ruleset.get("target") in _REF_TARGETS:
            found.append(Difference(
                f"ruleset {ruleset.get('name')}",
                f"not expected ({ruleset.get('target')} ruleset); a public repo keeps "
                f"only {MAIN_RULESET}, {TAGS_RULESET} and {TAGS_CREATE_RULESET}",
                left=f"delete it on GitHub, or: gh api --method DELETE "
                     f"repos/{github}/rulesets/{ruleset.get('id')}"))
    found += _security_differences(github, state.repo)
    found += _github_environment_differences(github, state)
    default = state.repo.get("default_branch")
    if default and default != main:
        found.append(Difference(
            "push piece main", f"has {main}; the repository's default branch is {default}",
            left="set one to the other: moxi publishing enable PATH --set main=<branch>, "
                 "or the default branch on GitHub"))
    return found


# -- the commands ---------------------------------------------------------------------------

def _context(path: str, run) -> tuple[str, str, str, Gh]:
    from moxi_publishing import flag, release

    root = flag.work_tree(path)
    github = flag.config_get(root, flag.GITHUB)
    if not github:
        raise Failure("no GitHub repo for this repo: moxi publishing enable PATH --github "
                      "OWNER/REPO sets one", 2)
    return root, github, release.main_branch(root), Gh(run)


def _work_tree(root: str):
    """The workspace as the work tree has it, through the tag gate's reader, or the
    reason it can't be read."""
    from moxi_publishing import tags

    try:
        return tags.work_tree_workspace(root), ""
    except tags.ReleaseError as exc:
        return None, str(exc)


def check_job(root: str) -> CheckJob:
    """The check `moxi-main` requires: the root `pyproject.toml`'s `check-job` in
    `[tool.moxi-publishing]`, read from the work tree as the tag gate reads it."""
    ws, problem = _work_tree(root)
    if ws is None:
        return CheckJob(None, missing=f"{CHECK_JOB_KEY} can't be read: {problem}")
    job = ws.setting(CHECK_JOB_KEY)
    workflow = ws.setting(CHECK_WORKFLOW_KEY)
    if job is None:
        return CheckJob(None, workflow, f"no {CHECK_JOB_KEY} in the root pyproject.toml's "
                                        f"[tool.{TOOL_TABLE}]")
    return CheckJob(job.strip(), workflow)


def _check_note(found: list[Difference], check: CheckJob, main: str) -> list[str]:
    """The note on the checks main requires after `apply` and not today, or none."""
    new = [name for each in found for name in each.new_required_checks]
    if not new:
        return []
    names = " and ".join(new)
    whose = f"{names} check has" if len(new) == 1 else f"{names} checks have"
    where = (f".github/workflows/{check.workflow}" if check.workflow
             else "the CI workflow")
    return [f"note: with {names} required, GitHub moves {main} only to a commit whose "
            f"{whose} already passed on another ref, so {where} must run "
            f"{names} on pushes to branches other than {main}; a check that runs only "
            f"on {main} can never pass first, and {main} could no longer move"]


def _publisher_line(root: str, github: str) -> str:
    owner, _, name = github.partition("/")
    ws, _problem = _work_tree(root)
    # The line is advice; a workspace that can't be read only loses its list.
    dists = sorted(ws.members) if ws is not None else []
    which = f" ({', '.join(dists)})" if dists else ""
    return (f"PyPI trusted publisher: can't be read from GitHub; on pypi.org each "
            f"distribution{which} needs one for owner {owner}, repository {name}, "
            f"workflow {WORKFLOW}, environment {GITHUB_ENVIRONMENT}")


def show(path: str, *, run=None) -> Result:
    """`moxi publishing github show PATH`: each difference between GitHub and what a
    public repo expects, field by field. Raises `Failure`."""
    root, github, main, gh = _context(path, run)
    state = read(gh, github)
    check = check_job(root)
    found = differences(github, state, main=main, check=check)
    lines = [f"{github}: default branch {state.repo.get('default_branch')}, reviewer "
             f"{state.account['login']} (gh's account)"]
    lines += [each.line() for each in found]
    lines += _check_note(found, check, main)
    lines.append(_publisher_line(root, github))
    lines.append(f"{len(found)} difference(s)" if found else "no difference")
    return Result(lines, 0)


def apply(path: str, *, run=None) -> Result:
    """`moxi publishing github apply PATH`: write each expected ruleset and setting that
    differs, then print what is left for the user. Raises `Failure`."""
    root, github, main, gh = _context(path, run)
    state = read(gh, github)
    check = check_job(root)
    found = differences(github, state, main=main, check=check)
    lines = [f"{github}: reviewer {state.account['login']} (gh's account)"]
    done: list[Request] = []
    for each in found:
        for request in each.writes:
            if request not in done:
                gh.send(request)
                done.append(request)
                lines.append(f"wrote {request.method} {request.path}")
    lines += _check_note(found, check, main)
    for each in found:
        if not each.writes:
            lines.append(f"left: {each.line()}" + (f"; {each.left}" if each.left else ""))
    lines.append(_publisher_line(root, github))
    lines.append(f"{len(done)} write(s)" if done else "nothing to write")
    return Result(lines, 0)


__all__ = ["GITHUB_ENVIRONMENT", "RELEASE_TAGS", "WORKFLOW", "ACTIONS_APP_ID",
           "REQUIRED_CHECKS", "MAIN_RULESET", "TAGS_RULESET", "TAGS_CREATE_RULESET",
           "Request", "Difference", "CheckJob", "State", "Gh", "required_check_rule",
           "expected_rulesets", "read", "differences", "check_job", "show", "apply"]
