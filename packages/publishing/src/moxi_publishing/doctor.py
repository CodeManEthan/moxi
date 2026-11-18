"""publishing's doctor rows: `rows()`, which the announcement's `DOCTOR` names.

Read-only, and no row uses the network. For each repo repo has indexed:

- `fail` when the repo carries the public flag and one of the three pieces is missing or
  disabled there, since part of what the flag promises then doesn't run;
- `fail` when a publishing piece is registered in a repo without the flag: it refuses
  every commit or push there;
- `fail` when a hook command repo wrote for a piece's event names another interpreter
  than `moxi-publishing.interpreter`, the one `enable` registered the pieces with;
- `fail` when a commit-msg hook of the repo's own runs after the pieces while the message
  piece's `drop-trailer` names `Commit-Check` or holds no value (which reads as the seed,
  which names it): that hook would never see the line it checks;
- `warn` when the repo's `user.email` (or `user.name`, when secrets' author has no email)
  isn't secrets' `author.*`, since the push piece then reads none of the repo's commits as
  its own and refuses no leftover drop or pass key;
- `warn` when `moxi-publishing.github` is unset, when `publishing.yml`, the public scan
  GitHub runs on every push, is missing, and when a workflow file `moxi publishing
  workflows write` writes is there but differs from its template;
- for a repo whose push piece sets `standing-branch`: `fail` when a pattern is bad (the
  piece then refuses every push) or `moxi-publishing.github` is unset (no push then goes
  with no word), and `warn` when no push URL of the remote `origin` reduces, by the push
  piece's rule (`push.github_repo`), to `moxi-publishing.github`, naming each URL. The
  URLs are git's own (`git remote get-url --push --all origin`): `pushurl` where set, else
  `url`, each after `insteadOf` and `pushInsteadOf` rewriting, as git hands them to the
  pre-push hook. A repo with no `origin`, or an `origin` with no URL, warns too, since
  `origin` then takes no standing push without the word;
- `ok`, one row, for a flagged repo with none of the above `fail`s, naming the standing
  patterns where there are some.

Then, once: `warn` when secrets' private names list is empty or can't be read, and when
the push log can't be written, since the push piece then refuses every push.
With no flagged repo and no stray piece, one `ok` row says so.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from moxi.doctor import Row

MODULE = "publishing"
#: The trailer a commit-msg hook running after the pieces may check; dropped, it is gone
#: before that hook reads the message.
CHECKED_TRAILER = "Commit-Check"
DROP_KEY = "drop-trailer"
PASS_KEY = "pass-trailer"
#: The events of the three pieces.
EVENTS = ("commit-msg", "pre-commit", "pre-push")


def rows() -> Iterator[Row]:
    from moxi_repo import api

    try:
        repos = api.indexed_repos()
    except api.Failure as exc:
        yield Row(MODULE, "repos", "fail", f"repo's index can't be read: {exc.text}")
        return
    seen = False
    for _key, _entry, repo, _why in repos:
        if repo is None:
            continue
        found = list(_repo_rows(str(repo.root)))
        if found:
            seen = True
            yield from found
    if not seen:
        yield Row(MODULE, "repos", "ok", "no repo is marked public")
        return
    yield from _once_rows()


def _repo_rows(root: str) -> Iterator[Row]:
    from moxi_repo.api import quote_arg

    from moxi_publishing import flag

    name = f"repo {root}"
    try:
        public = flag.is_public(root)
        have = flag.registered(root)
    except flag.Failure as exc:
        yield Row(MODULE, name, "fail", exc.text)
        return
    if not public and not have:
        return
    if not public:
        yield Row(MODULE, name, "fail",
                  f"{', '.join(sorted(have))} registered, but the repo isn't marked "
                  "public, so each refuses",
                  f"moxi publishing enable {quote_arg(root)} (or: moxi publishing "
                  f"disable {quote_arg(root)})")
        return
    missing = [piece for piece in flag.PIECES if piece not in have]
    off = [piece for piece in flag.PIECES if piece in have and not have[piece]["enabled"]]
    if missing or off:
        parts = ([f"missing: {', '.join(missing)}"] if missing else []) + (
            [f"disabled: {', '.join(off)}"] if off else [])
        yield Row(MODULE, name, "fail",
                  f"marked public, but not every guard runs ({'; '.join(parts)})",
                  f"moxi publishing enable {quote_arg(root)}" if missing else
                  f"moxi repo git-hooks enable {quote_arg(root)} {off[0]}")
        return
    problems = list(_interpreter_rows(root, name)) + list(
        _later_hook_rows(root, name, have)) + list(_warn_rows(root, name)) + list(
        _standing_rows(root, name, have))
    yield from problems
    if not any(row.state == "fail" for row in problems):
        patterns = _standing_values(have)
        yield Row(MODULE, name, "ok", "marked public; the three pieces are enabled" + (
            f"; standing branches: {' '.join(patterns)}" if patterns else ""))


def _standing_values(have: dict) -> list[str]:
    from moxi_publishing import flag, push

    values = have[flag.PUSH]["settings"].get(push.STANDING_BRANCH, [])
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def _standing_rows(root: str, name: str, have: dict) -> Iterator[Row]:
    """The rows for a push piece that sets `standing-branch`; none when it sets none."""
    from moxi_repo.api import quote_arg

    from moxi_publishing import flag, push

    if not _standing_values(have):
        return
    standing = push.Standing(have[flag.PUSH]["settings"])
    fix = f"moxi publishing enable {quote_arg(root)}"
    if standing.problems:
        yield Row(MODULE, name, "fail",
                  f"{'; '.join(standing.problems)}, so the push piece refuses every push",
                  f"{fix} --set {quote_arg(push.STANDING_BRANCH + '=ci/*')} (or "
                  f"--set {push.STANDING_BRANCH}= to remove it)")
        return
    github = flag.config_get(root, flag.GITHUB)
    names = " ".join(standing.patterns)
    if not github:
        yield Row(MODULE, name, "fail",
                  f"{push.STANDING_BRANCH} is {names}, but {flag.GITHUB} is unset, so no "
                  "push of a standing branch goes with no word",
                  f"{fix} --github OWNER/REPO")
        return
    done = flag._git(root, "remote", "get-url", "--push", "--all", "origin")
    urls = [u for u in done.stdout.splitlines() if u] if done.returncode == 0 else []
    if not urls:
        yield Row(MODULE, name, "warn",
                  f"the repo has no remote origin with a URL, so a push of {names} goes "
                  "with no word nowhere",
                  f"git -C {quote_arg(root)} remote add origin git@github.com:{github}.git")
        return
    if not any(push.github_repo(url) == github for url in urls):
        shown = ", ".join(push._shown_url(url) for url in urls)
        yield Row(MODULE, name, "warn",
                  f"no push URL of origin ({shown}) reduces to {github} ({flag.GITHUB}) "
                  "in one of the three forms git sends to GitHub, so a push of "
                  f"{names} there asks for the word",
                  f"git -C {quote_arg(root)} remote set-url --push origin "
                  f"git@github.com:{github}.git")


def _status(root: str) -> dict:
    from moxi_repo import api

    try:
        found = api.status(root)
    except api.Failure:
        return {}
    return found[0] if found else {}


def hook_commands(status: dict) -> dict[str, str | None]:
    """{event: the text git runs for it, as repo's status gives it} for the pieces'
    events; None when it can't be read."""
    events = status.get("events", {})
    return {event: (events.get(event) or {}).get("command") for event in EVENTS}


def _interpreter_rows(root: str, name: str) -> Iterator[Row]:
    from moxi_repo.api import quote_arg

    from moxi_publishing import flag

    recorded = flag.config_get(root, flag.INTERPRETER)
    fix = f"moxi publishing enable {quote_arg(root)}"
    if not recorded:
        yield Row(MODULE, name, "fail", f"{flag.INTERPRETER} is unset, so the interpreter "
                                        "the pieces run with can't be checked", fix)
        return
    # repo writes the interpreter into every hook command as one single-quoted sh word
    # with forward slashes.
    word = "'" + recorded.replace("\\", "/").replace("'", "'\\''") + "'"
    wrong = [event for event, text in hook_commands(_status(root)).items()
             if text is None or word not in text]
    if wrong:
        yield Row(MODULE, name, "fail",
                  f"the {', '.join(wrong)} hook command{'s' if len(wrong) > 1 else ''} "
                  f"{'do' if len(wrong) > 1 else 'does'} not run {recorded}, the "
                  f"interpreter {flag.INTERPRETER} records",
                  f"moxi repo git-hooks refresh {quote_arg(root)}, from that interpreter's "
                  f"environment; or, to take this one: {fix}")


def drops_checked_trailer(values) -> bool:
    """Whether a `drop-trailer` setting names the checked trailer, reading a key that
    holds no value, or only empty ones, as the seed, which names it."""
    named = [v.strip() for v in values or [] if v.strip()]
    return not named or any(v.casefold() == CHECKED_TRAILER.casefold() for v in named)


def _later_hook_rows(root: str, name: str, have: dict) -> Iterator[Row]:
    from moxi_repo.api import quote_arg

    from moxi_publishing import flag

    later = (_status(root).get("events", {}).get("commit-msg") or {}).get("existing")
    settings = have[flag.MESSAGE]["settings"]
    values = settings.get(DROP_KEY, [])
    if later and drops_checked_trailer(values):
        yield Row(MODULE, name, "fail",
                  f"a commit-msg hook runs after the pieces ({later}), and {DROP_KEY} "
                  + (f"names {CHECKED_TRAILER}" if values and any(v.strip() for v in values)
                     else f"holds no value, which reads as the seed naming {CHECKED_TRAILER}")
                  + ", so that hook never sees the line if it checks it",
                  _later_hook_fix(root, values, settings.get(PASS_KEY, [])))


def _others(values) -> list[str]:
    """The keys of `values` but the checked trailer, empty ones left out."""
    return [v.strip() for v in values if v.strip()
            and v.strip().casefold() != CHECKED_TRAILER.casefold()]


def _later_hook_fix(root: str, drop, passed) -> str:
    """An `enable` that leaves a drop list the row passes: the repo's other drop keys, or
    the seed's when it has none, since a list left empty reads as the seed again; and the
    repo's pass keys with the checked trailer added, since `--set` replaces a whole list."""
    from moxi.config import ConfigError
    from moxi_repo.api import quote_arg

    from moxi_publishing import settings as config

    keep = _others(drop)
    if not keep:
        try:
            keep = _others(config.load()[config.DROP_TRAILERS])
        except ConfigError:  # an unreadable config only loses the suggestion
            keep = []
    passing = [v.strip() for v in passed if v.strip()]
    if not any(v.casefold() == CHECKED_TRAILER.casefold() for v in passing):
        passing.append(CHECKED_TRAILER)
    sets = " ".join([f"--set {quote_arg(f'{DROP_KEY}={v}')}" for v in keep]
                    + [f"--set {quote_arg(f'{PASS_KEY}={v}')}" for v in passing])
    fix = f"moxi publishing enable {quote_arg(root)} {sets}"
    if keep:
        return fix
    return (f"name a key other than {CHECKED_TRAILER} on {DROP_KEY}, since a list with "
            f"none reads as the seed: {fix} --set {DROP_KEY}=KEY")


def _identity_rows(root: str, name: str) -> Iterator[Row]:
    """`warn` when the repo commits as someone other than secrets' `author.*`: the push
    piece's leftover-key refusal reads only the repo's own commits, so it reads none."""
    from moxi_repo.api import quote_arg
    from moxi_secrets import SetUnavailable

    from moxi_publishing import flag, push

    try:
        own = push._own_identity()
    except SetUnavailable:
        return  # the once row on the private names list says so
    if own is None:
        return
    mine = (flag.config_get(root, "user.name") or "", flag.config_get(root, "user.email")
            or "")
    if push._same(mine, own):
        return
    key, want, have = (("email", own[1], mine[1]) if own[1] else ("name", own[0], mine[0]))
    yield Row(MODULE, name, "warn",
              f"the repo's user.{key} ({have or 'unset'}) isn't secrets' author.{key} "
              f"({want}), so the push piece reads no commit as the repo's own and never "
              "refuses a leftover drop-trailer or pass-trailer key",
              f"git -C {quote_arg(root)} config user.{key} {quote_arg(want)} (or set "
              f"secrets' author.{key} to the repo's)")


def _warn_rows(root: str, name: str) -> Iterator[Row]:
    from moxi_repo.api import quote_arg

    from moxi_publishing import flag, release

    yield from _identity_rows(root, name)
    if not flag.config_get(root, flag.GITHUB):
        yield Row(MODULE, name, "warn",
                  f"{flag.GITHUB} is unset, so the tag gate and github show have no repo",
                  f"moxi publishing enable {quote_arg(root)} --github OWNER/REPO")
    for file in release.WORKFLOWS:
        path = os.path.join(root, *release.WORKFLOW_DIR.split("/"), file)
        try:
            with open(path, "rb") as fh:
                same = fh.read() == release.template(file)
        except FileNotFoundError:
            # The scan's file is the server-side check of every push; the release file
            # matters only to a repo that releases.
            if file == release.SCAN_WORKFLOW:
                yield Row(MODULE, name, "warn",
                          f"{release.WORKFLOW_DIR}/{file} is missing, so GitHub scans no "
                          "push for what the pieces refuse",
                          f"moxi publishing workflows write {quote_arg(root)}, then "
                          "commit it")
            continue
        except OSError as exc:
            yield Row(MODULE, name, "warn", f"{release.WORKFLOW_DIR}/{file} can't be read: "
                                            f"{exc}")
            continue
        if not same:
            yield Row(MODULE, name, "warn",
                      f"{release.WORKFLOW_DIR}/{file} differs from publishing's template",
                      f"moxi publishing workflows write {quote_arg(root)}, then commit it")


def _once_rows() -> Iterator[Row]:
    from moxi_publishing import flag, push

    warning = flag._names_empty()
    if warning:
        yield Row(MODULE, "private names", "warn", warning.removeprefix("warning: "))
    log = push.log_path()
    where = log if log.exists() else next((p for p in log.parents if p.exists()), None)
    if where is None or not os.access(where, os.W_OK):
        yield Row(MODULE, "push log", "warn",
                  f"{log} can't be written, so the push piece refuses every push",
                  f"make {where or log.parent} writable")
