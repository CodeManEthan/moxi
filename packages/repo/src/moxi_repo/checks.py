"""repo's `DOCTOR`: the rows `moxi doctor` shows for repo's git hooks, specs and records.

`rows()` reads and writes nothing; the sweep (`_records.sweep`) does the clearing. The
records repo leaves (`moxi.doctor.record`) are shown by the core itself, one `fail` row
each. The rows here, in order:

- git: the git on `PATH` against the floor, and any other git found (on macOS
  `/usr/bin/git`) that runs no config entries while a repo uses config mode;
- per indexed repo: each event's config entry, shim or launcher, its interpreter and
  runner, a shim a hook manager moved, the chain line, a registered piece no installed
  module declares, and one `ok` row with the mode when nothing is wrong;
- per spec in a repo: the last commit against its interval while changed paths wait, the
  commits not pushed, the paths left out longer than an hour, and the two push records no
  pass will clear by itself (a spec that isn't saved, a branch no pass pushes);
- uncovered files: in a repo with a saved spec, changed or untracked files that are not
  ignored and that no saved spec's paths cover;
- the ledger: a `warn` row for each open key the sweep would clear, and each open piece
  key whose piece has no `recorded` flag.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import time
from pathlib import Path

from moxi.doctor import Row

from moxi_repo import _records, api, committer, gitconf, pieces
from moxi_repo.gitrun import git

MODULE = "repo"
#: A path left out longer than this many seconds is shown.
LEFT_OUT_AFTER = 3600.0
#: Changed paths still waiting this many seconds past a spec's interval are shown.
STALLED_AFTER = 3600.0
#: How many paths a row names before "and N more".
NAMED = 10
#: Other gits to check, by platform, beside the one on PATH.
OTHER_GITS: dict[str, tuple[str, ...]] = {"darwin": ("/usr/bin/git",)}

_now = time.time


def rows() -> list[Row]:
    """Every row repo adds to `moxi doctor`."""
    try:
        index, unreadable = api.indexed(), []
    except api.IndexUnreadable as exc:
        index = {}
        unreadable = [Row(MODULE, "index", "fail", exc.detail,
                          f"fix or remove {exc.path}, then index each repo again: "
                          "moxi repo git-hooks add PATH")]
    version = gitconf.git_version()
    good, _ = pieces.declared()
    out: list[Row] = []
    repos: list[tuple[str, gitconf.Repo | None, str | None]] = []
    for key, entry, repo, why in api.indexed_repos(index):
        repos.append((str(repo.root) if repo else entry.get("root") or key, repo, why))
    statuses = []
    for root, repo, why in repos:
        name = f"hooks {root}"
        if repo is None:
            out.append(Row(MODULE, name, "warn", why,
                           f"moxi repo git-hooks remove {api._arg(root)}"))
            continue
        try:
            status = api._status_of(repo, True, version, good)
        except (api.Failure, OSError) as exc:
            out.append(Row(MODULE, name, "fail", f"its git config can't be read: "
                           f"{getattr(exc, 'text', exc)}"))
            continue
        statuses.append(status)
        out += _hook_rows(status)
    out = _git_rows(version, index, statuses) + unreadable + out
    for root, repo, _ in repos:
        if repo is not None:
            out += _spec_rows(repo)
    out += _ledger_rows(index)
    return out


# -- git ----------------------------------------------------------------------------------

def _git_rows(version, index: dict, statuses: list[dict]) -> list[Row]:
    floor = gitconf.version_text(gitconf.FLOOR)
    state = "fail" if index else "warn"
    path = shutil.which("git")
    if version is None:
        return [Row(MODULE, "git", state, "git is not on PATH, or `git version` gave no "
                    "version", f"install git {floor} or later")]
    if version < gitconf.FLOOR:
        return [Row(MODULE, "git", state, f"git {gitconf.version_text(version)} ({path}) is "
                    f"older than {floor}, the oldest repo's git hooks support",
                    f"install git {floor} or later")]
    mode = "config" if version >= gitconf.CONFIG_FROM else "shim"
    out = [Row(MODULE, "git", "ok", f"git {gitconf.version_text(version)} ({path}); a repo "
               f"not pinned gets {mode} mode")]
    config_roots = [s["root"] for s in statuses if s.get("mode") == "config"]
    for other in OTHER_GITS.get(sys.platform, ()):
        if not os.path.exists(other) or (path and _same(path, other)):
            continue
        found = _version_of(other)
        if found is None or found >= gitconf.CONFIG_FROM:
            if found is not None:
                out.append(Row(MODULE, f"git {other}", "ok",
                               f"git {gitconf.version_text(found)} runs config entries"))
            continue
        text = gitconf.version_text(found)
        if not config_roots:
            out.append(Row(MODULE, f"git {other}", "ok", f"git {text} runs no config "
                           "entries, and no indexed repo uses config mode"))
            continue
        fix = (f"moxi repo git-hooks add {config_roots[0]} --mode shim"
               if len(config_roots) == 1 else
               "moxi repo git-hooks add PATH --mode shim, for each of them")
        out.append(Row(MODULE, f"git {other}", "warn",
                       f"git {text} runs no config entries, so a commit it makes runs no "
                       f"piece in the config-mode repos {', '.join(config_roots)}", fix))
    return out


def _same(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _version_of(path: str):
    from moxi import proc

    try:
        done = proc.run([path, "version"], timeout=10)
    except Exception:  # noqa: BLE001 - a git that can't say is reported as having none
        return None
    if done.returncode != 0:
        return None
    return gitconf.parse_version((done.stdout or b"").decode("utf-8", "replace"))


# -- git hooks ----------------------------------------------------------------------------

def _hook_rows(status: dict) -> list[Row]:
    root = status["root"]
    name = f"hooks {root}"
    out = []
    for problem in status["problems"]:
        out.append(Row(MODULE, name, getattr(problem, "state", "fail"),
                       getattr(problem, "detail", str(problem)), getattr(problem, "fix", None)))
    for event, info in sorted(status["events"].items()):
        if info.get("chain_line") == "needed":
            hook = Path(status["hooks_dir"]) / event
            out.append(Row(MODULE, name, "warn",
                           f"{event}: chain line needed in {hook}, so the pieces don't run",
                           f"add this line to {hook}, before any line that reads stdin: "
                           f"{info.get('line')}"))
    if any(row.state in ("warn", "fail") for row in out):
        return out
    pieces_of = {}
    for piece in status["pieces"]:
        pieces_of[piece["event"]] = pieces_of.get(piece["event"], 0) + 1
    if not pieces_of:
        return out + [Row(MODULE, name, "ok", "indexed, with no piece registered")]
    via = "; ".join(f"{event}: {n} {'piece' if n == 1 else 'pieces'} through "
                    f"{(status['events'].get(event) or {}).get('via') or 'nothing'}"
                    + (", chain line present"
                       if (status['events'].get(event) or {}).get("chain_line") == "present"
                       else "")
                    for event, n in sorted(pieces_of.items()))
    pin = status.get("mode_set")
    return out + [Row(MODULE, name, "ok",
                      f"{status.get('mode')} mode"
                      + (f" (pinned)" if pin in gitconf.MODES else "")
                      + f", git {status.get('git_version')}; {via}")]


# -- specs and uncovered files --------------------------------------------------------------

def _spec_rows(repo: gitconf.Repo) -> list[Row]:
    root = str(repo.root)
    try:
        listed = committer.listing(repo.root)
    except committer.SpecError as exc:
        return [Row(MODULE, f"specs {root}", "warn", f"its specs can't be read: {exc}")]
    if not listed:
        return []
    saved = [item for item in listed if item["saved"]]
    changed = _changed(repo.root) if saved else []
    now = _now()
    head = _head_branch(repo.root)
    ledger = _records.ledger()
    out: list[Row] = []
    for item in listed:
        name = item["name"]
        label = f"spec {root} {name}"
        spec = item["spec"]
        if item["saved"] and spec is None:
            out.append(Row(MODULE, label, "warn", f"the saved spec can't be read: "
                           f"{item['problem']}", f"moxi repo specs remove {root} {name}"))
        if spec is not None:
            out += _cadence_rows(repo, label, name, spec, item, changed, now)
            out += _push_rows(repo, label, name, spec, item, head)
        out += _left_rows(label, item, now)
        out += _stray_push_rows(repo, label, item, head, ledger)
    if saved:
        out += _uncovered_rows(repo, saved, changed)
    return out


def _covers(spec_path: str, path: str) -> bool:
    spec_path = spec_path.rstrip("/")
    return spec_path in (".", "") or path == spec_path or path.startswith(spec_path + "/")


def _changed(top: Path) -> list[str]:
    """Every changed or untracked path in the work tree that isn't ignored."""
    # Without optional locks, status doesn't refresh the index: the doctor writes nothing.
    done = git(top, "--no-optional-locks", "status", "--porcelain", "-z",
               "--untracked-files=all", "--no-renames", binary=True)
    if done.returncode != 0:
        return []
    return [os.fsdecode(item[3:]) for item in done.stdout.split(b"\0") if len(item) > 3]


def _head_branch(top: Path) -> str | None:
    done = git(top, "symbolic-ref", "-q", "HEAD")
    ref = done.stdout.strip()
    return ref[len("refs/heads/"):] if done.returncode == 0 and ref.startswith(
        "refs/heads/") else None


def _stamp(t) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else "never"


def _named(paths: list[str]) -> str:
    text = ", ".join(paths[:NAMED])
    return text + (f" and {len(paths) - NAMED} more" if len(paths) > NAMED else "")


def _cadence_rows(repo, label: str, name: str, spec: dict, item: dict, changed: list[str],
                  now: float) -> list[Row]:
    root = str(repo.root)
    interval = float(spec["min_interval"] or 0)
    left = set(item["left"])
    waiting = [p for p in changed if p not in left
               and any(_covers(sp, p) for sp in spec["paths"])]
    since = committer.last_commit_time(repo.root, tuple(spec["author"]))
    last = item.get("last") or {}
    passed = (f"; last pass {last.get('status')} at {_stamp(last.get('time'))}"
              if last else "; no pass has run")
    if waiting and (since is None or now - since > interval + STALLED_AFTER):
        return [Row(MODULE, label, "warn",
                    f"{len(waiting)} changed {'path' if len(waiting) == 1 else 'paths'} "
                    f"waiting ({_named(waiting)}); the last commit by {spec['author'][0]} "
                    f"was at {_stamp(since)}, interval {committer._plain(interval)} s"
                    + passed, f"moxi repo commit {root} --spec {name}")]
    return [Row(MODULE, label, "ok", f"last commit at {_stamp(since)}, interval "
                f"{committer._plain(interval)} s" + passed)]


def _push_rows(repo, label: str, name: str, spec: dict, item: dict,
               head: str | None) -> list[Row]:
    remote = spec.get("push")
    if not remote:
        return []
    branches = ([head] if head else []) + sorted(b for b in item["push"] if b != head)
    out = []
    for branch in branches:
        if git(repo.root, "rev-parse", "-q", "--verify",
               f"refs/heads/{branch}").returncode != 0:
            continue
        count = git(repo.root, "rev-list", "--count",
                    f"refs/remotes/{remote}/{branch}..refs/heads/{branch}")
        if count.returncode == 0:
            ahead = count.stdout.strip()
            if ahead == "0":
                continue
            what = f"{ahead} {'commit' if ahead == '1' else 'commits'} on {branch} not " \
                   f"pushed to {remote}"
        else:
            what = f"{branch} has never been pushed to {remote}"
        error = (item["push"].get(branch) or {}).get("error")
        out.append(Row(MODULE, f"{label} push", "warn",
                       what + (f"; the last push failed: {error}" if error else ""),
                       f"moxi repo commit {repo.root} --spec {name}"))
    return out


def _left_rows(label: str, item: dict, now: float) -> list[Row]:
    old = sorted((path, entry) for path, entry in item["left"].items()
                 if now - float((entry or {}).get("since") or now) > LEFT_OUT_AFTER)
    if not old:
        return []
    named = "; ".join(f"{path} ({entry.get('why')}, since {_stamp(entry.get('since'))})"
                      for path, entry in old[:NAMED])
    more = f"; and {len(old) - NAMED} more" if len(old) > NAMED else ""
    return [Row(MODULE, f"{label} left out", "warn",
                f"{len(old)} {'path' if len(old) == 1 else 'paths'} left out over an hour: "
                f"{named}{more}",
                "commit or unstage a path someone else staged; ignore a file that is too "
                "big, or raise the spec's max_bytes")]


def _stray_push_rows(repo, label: str, item: dict, head: str | None,
                     ledger: dict) -> list[Row]:
    """Push records no pass clears by itself. A spec that isn't saved is retried only by its
    caller, so when the caller stops running it, its push record stays. A saved spec's
    branch that is not checked out and has no failed push in the state file is in no
    pass's push set."""
    out = []
    root = str(repo.root)
    for key in item["records"]:
        entry = ledger.get(key) or {}
        if entry.get("kind") != "push":
            continue
        branch = (entry.get("subject") or {}).get("branch") or ""
        if not item["saved"]:
            out.append(Row(MODULE, f"{label} push", "warn",
                           f"the spec isn't saved, so only its caller's passes retry the "
                           f"push of {branch}; if nothing runs it any more, forget it",
                           f"moxi repo specs remove {root} {item['name']}"))
        elif branch != head and branch not in item["push"]:
            if git(repo.root, "rev-parse", "-q", "--verify",
                   f"refs/heads/{branch}").returncode != 0:
                continue  # the sweep clears it; the ledger rows say so
            out.append(Row(MODULE, f"{label} push", "warn",
                           f"no pass retries the push of {branch}: it isn't checked out, and "
                           "the repo's state file holds no failed push of it",
                           f"git -C {shlex.quote(root)} switch {branch}, then: moxi repo "
                           f"commit {root} --spec {item['name']}; or: moxi doctor --clear "
                           f"repo {shlex.quote(key)}"))
    return out


def _uncovered_rows(repo, saved: list[dict], changed: list[str]) -> list[Row]:
    covering = [sp for item in saved if item["spec"] for sp in item["spec"]["paths"]]
    loose = [p for p in changed if not any(_covers(sp, p) for sp in covering)]
    if not loose:
        return []
    return [Row(MODULE, f"uncovered {repo.root}", "warn",
                f"{len(loose)} changed or untracked "
                f"{'file' if len(loose) == 1 else 'files'} no saved spec covers: "
                f"{_named(loose)}",
                "add them to a saved spec's paths, commit them, or ignore them")]


# -- the ledger ---------------------------------------------------------------------------

def _ledger_rows(index: dict) -> list[Row]:
    out = []
    for found in _records.judge():
        root = (index.get(found.cgd) or {}).get("root")
        if found.reason == _records.GONE:
            fix = (f"moxi repo git-hooks remove {root}" if root
                   else "moxi repo git-hooks refresh")
            detail = (f"its repo {root or found.cgd} is gone; the next sweep clears its "
                      "record")
        elif found.action == "flag":
            fix = f"moxi repo git-hooks refresh {root}" if root else \
                "moxi repo git-hooks refresh"
            detail = "its piece has no recorded flag; the next sweep sets it"
        else:
            fix = f"moxi repo git-hooks refresh {root}" if root else \
                "moxi repo git-hooks refresh"
            detail = f"{found.reason}; the next sweep clears its record"
        out.append(Row(MODULE, f"ledger {found.key}", "warn", detail, fix))
    return out


__all__ = ["rows", "MODULE", "LEFT_OUT_AFTER", "STALLED_AFTER", "NAMED", "OTHER_GITS"]
