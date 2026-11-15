"""`moxi publishing github show|apply`: the GitHub rulesets and settings a public repo
needs, read back or written with `gh api`.

What a public repo expects of GitHub:

- ruleset `moxi-main` on the push piece's main branch: no deletion, no force push;
- no other branch ruleset: other branches are created, updated and deleted freely, and
  the push piece holds "deleted only once merged" locally;
- ruleset `moxi-tags` on every tag: no update, deletion or force push;
- ruleset `tags-create` on every tag but release tags (`*-v*`): no creation;
- secret scanning with push protection;
- environment `pypi`, deployable from release tags only, whose required reviewer is the
  account `gh` is signed in as, allowed to approve its own runs since that account pushes
  the tags. The release workflow's publish job runs in it, so that review is what stands
  between a tagged commit and the package index;
- a trusted publisher on the package index for each distribution, which GitHub can't
  show; `show` says so.

Each ruleset is active with no bypass actors. `show` prints each difference field by
field, and the push piece's `main` setting when it isn't the repository's default branch.
`apply` writes the expected rulesets and settings; it deletes no ruleset and changes no
branch, and prints what it left for you.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from moxi_publishing.flag import Failure
from moxi_publishing.release import Result

#: How long one `gh api` call may take.
GH_TIMEOUT_S = 30.0
#: The environment the release workflow's publish job runs in.
DEPLOYMENT = "pypi"
#: The release tags the environment may deploy from: `<distribution>-v<version>`.
RELEASE_TAGS = "*-v*"
#: The release workflow, as a trusted publisher on the package index names it.
WORKFLOW = "release.yml"
#: The two kinds of ref a ruleset may target that `show` judges.
_REF_TARGETS = ("branch", "tag")


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

    def line(self) -> str:
        return f"{self.subject}: {self.text}"


@dataclass
class State:
    """What `show` read from GitHub."""

    repo: dict
    rulesets: list[dict]
    deployment: dict | None
    tag_policies: list[dict] | None
    account: dict
    notes: list[str] = field(default_factory=list)


def expected_rulesets(main: str) -> list[dict]:
    """The rulesets a public repo expects, as `gh api` takes them."""

    def ruleset(name: str, target: str, include: list[str], exclude: list[str],
                rules: list[str]) -> dict:
        return {"name": name, "target": target, "enforcement": "active",
                "bypass_actors": [],
                "conditions": {"ref_name": {"include": include, "exclude": exclude}},
                "rules": [{"type": rule} for rule in rules]}

    return [
        ruleset("moxi-main", "branch", [f"refs/heads/{main}"], [],
                ["deletion", "non_fast_forward"]),
        ruleset("moxi-tags", "tag", ["refs/tags/**/*"], [],
                ["deletion", "non_fast_forward", "update"]),
        ruleset("tags-create", "tag", ["refs/tags/**/*"], [f"refs/tags/{RELEASE_TAGS}"],
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
    deployment = gh.get(f"repos/{github}/environments/{DEPLOYMENT}")
    policies = None
    if deployment is not None and (deployment.get("deployment_branch_policy") or {}).get(
            "custom_branch_policies"):
        found = gh.get(f"repos/{github}/environments/{DEPLOYMENT}"
                       "/deployment-branch-policies?per_page=100")
        policies = (found or {}).get("branch_policies", [])
    account = gh.get("user")
    if not account:
        raise Failure("gh is signed in to no account; run gh auth login", 1)
    return State(repo=repo, rulesets=rulesets, deployment=deployment,
                 tag_policies=policies, account=account)


def _names(items) -> str:
    return ", ".join(items) if items else "none"


def _ruleset_differences(github: str, have: dict | None, want: dict) -> list[Difference]:
    subject = f"ruleset {want['name']}"
    if have is None:
        return [Difference(subject, "missing",
                           (Request("POST", f"repos/{github}/rulesets", want),))]
    write = (Request("PUT", f"repos/{github}/rulesets/{have['id']}", want),)
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
    has = sorted({rule.get("type", "?") for rule in have.get("rules") or []})
    wants = sorted(rule["type"] for rule in want["rules"])
    if has != wants:
        found.append(Difference(subject, f"rules: has {_names(has)}; want {_names(wants)}",
                                write))
    return found


def _deployment_differences(github: str, state: State) -> list[Difference]:
    subject = f"environment {DEPLOYMENT}"
    account = state.account
    put = Request("PUT", f"repos/{github}/environments/{DEPLOYMENT}", {
        "reviewers": [{"type": "User", "id": account["id"]}],
        "prevent_self_review": False,
        "deployment_branch_policy": {"protected_branches": False,
                                     "custom_branch_policies": True},
    })
    policies = f"repos/{github}/environments/{DEPLOYMENT}/deployment-branch-policies"
    add_tag = Request("POST", policies, {"name": RELEASE_TAGS, "type": "tag"})
    env = state.deployment
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
            found.append(Difference(label, f"has {status}; want enabled", (Request(
                "PATCH", f"repos/{github}",
                {"security_and_analysis": {
                    "secret_scanning": {"status": "enabled"},
                    "secret_scanning_push_protection": {"status": "enabled"}}}),)))
    return found


def differences(github: str, state: State, *, main: str) -> list[Difference]:
    """Every field of `state` unlike what a public repo expects, in `show`'s order."""
    found: list[Difference] = []
    by_name = {ruleset.get("name"): ruleset for ruleset in state.rulesets}
    wanted = expected_rulesets(main)
    for want in wanted:
        found += _ruleset_differences(github, by_name.get(want["name"]), want)
    names = {want["name"] for want in wanted}
    for ruleset in state.rulesets:
        if ruleset.get("name") not in names and ruleset.get("target") in _REF_TARGETS:
            found.append(Difference(
                f"ruleset {ruleset.get('name')}",
                f"not expected ({ruleset.get('target')} ruleset); a public repo keeps "
                "only moxi-main, moxi-tags and tags-create",
                left=f"delete it on GitHub, or: gh api --method DELETE "
                     f"repos/{github}/rulesets/{ruleset.get('id')}"))
    found += _security_differences(github, state.repo)
    found += _deployment_differences(github, state)
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


def _publisher_line(root: str, github: str) -> str:
    from moxi_publishing import tags

    owner, _, name = github.partition("/")
    try:
        dists = sorted(tags.work_tree_workspace(root).members)
    except tags.ReleaseError:
        # The line is advice; a workspace that can't be read only loses its list.
        dists = []
    which = f" ({', '.join(dists)})" if dists else ""
    return (f"PyPI trusted publisher: can't be read from GitHub; on pypi.org each "
            f"distribution{which} needs one for owner {owner}, repository {name}, "
            f"workflow {WORKFLOW}, environment {DEPLOYMENT}")


def show(path: str, *, run=None) -> Result:
    """`moxi publishing github show PATH`: each difference between GitHub and what a
    public repo expects, field by field. Raises `Failure`."""
    root, github, main, gh = _context(path, run)
    state = read(gh, github)
    found = differences(github, state, main=main)
    lines = [f"{github}: default branch {state.repo.get('default_branch')}, reviewer "
             f"{state.account['login']} (gh's account)"]
    lines += [each.line() for each in found]
    lines.append(_publisher_line(root, github))
    lines.append(f"{len(found)} difference(s)" if found else "no difference")
    return Result(lines, 0)


def apply(path: str, *, run=None) -> Result:
    """`moxi publishing github apply PATH`: write each expected ruleset and setting that
    differs, then print what is left for the user. Raises `Failure`."""
    root, github, main, gh = _context(path, run)
    state = read(gh, github)
    found = differences(github, state, main=main)
    lines = [f"{github}: reviewer {state.account['login']} (gh's account)"]
    done: list[Request] = []
    for each in found:
        for request in each.writes:
            if request not in done:
                gh.send(request)
                done.append(request)
                lines.append(f"wrote {request.method} {request.path}")
    for each in found:
        if not each.writes:
            lines.append(f"left: {each.line()}" + (f"; {each.left}" if each.left else ""))
    lines.append(_publisher_line(root, github))
    lines.append(f"{len(done)} write(s)" if done else "nothing to write")
    return Result(lines, 0)


__all__ = ["DEPLOYMENT", "RELEASE_TAGS", "WORKFLOW", "Request", "Difference", "State",
           "Gh", "expected_rulesets", "read", "differences", "show", "apply"]
