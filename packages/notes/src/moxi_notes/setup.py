"""notes' setup and teardown: what `moxi install notes` and `moxi notes setup` run.

`setup()` is the announcement's `SETUP`. It asks four things in turn, then writes:

1. **Root.** Where the notes are. When that folder is in no git work tree, whether to make
   it a git repo, so notes can snapshot it. This `git init` on the root is the only one
   notes ever runs: no notes operation makes a git repository inside the notes tree.
2. **Action.** What a commit does with a file that holds a secret: leave it out (the
   default), or commit a redacted copy through a git clean filter.
3. **Push.** When the repo has remotes and no push remote is set, which one snapshots go
   to.
4. **Preview.** It judges the working tree's tracked and untracked files as a snapshot
   would, and prints how many hold a secret, the first ten with their rules, how many
   commit authors the push gate would refuse, and the allowance lines that would keep the
   private names it found, with the path of the private allowlist they go in.
5. **Writes.** The settings, through `moxi.config.set(..., by="moxi install")`; in the
   notes repo, the two git hook pieces (re-enabled if disabled), the clean filter's driver
   and attribute for `redact` or their removal for `leave-out`, and the snapshot spec.

With a terminal it asks; with nobody at the keyboard it takes the current or default
value and prints the line that would change it. It can run again at any time: an answer
equal to the current value writes nothing, and every write is idempotent.

`teardown()` is the announcement's `TEARDOWN`: it removes the clean filter's driver and
attribute from the notes repo. Repo's own uninstall listener removes the pieces and the
saved spec, and the notes folder is never touched.
"""

from __future__ import annotations

import datetime
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TextIO

from . import settings as _settings

MODULE = "notes"
BY = "moxi install"
#: The two git hook pieces setup registers in the notes repo.
PIECES = ("notes/secret-gate", "notes/push-gate")
#: How many paths the preview names.
SHOWN = 10

ACTION_QUESTION = """\
When a note holds a secret (a key or token, or a name from your private names list):
  1. leave the file out of commits until you fix it (default)
  2. redact it: the committed copy gets [REDACTED:<rule>] and the file on disk keeps the
     text. Anything git writes back into the folder (checkout, reset --hard, stash, merge,
     rebase, pull) replaces your file with the redacted copy, and the original is lost.
     A file that can't be redacted is left out instead.
Either way, a push that would carry a secret is refused.
Choose [{default}]: """

ACTIONS = ("leave-out", "redact")


# -- what git says about the notes root -------------------------------------------------

@dataclass(frozen=True)
class Where:
    """The notes root as git sees it. `common_dir` is the common git dir when the root is
    in a repo; `top` its work tree's top, None in a bare repo or a git dir."""

    root: str
    exists: bool
    common_dir: str | None = None
    top: str | None = None

    @property
    def in_repo(self) -> bool:
        return self.common_dir is not None

    @property
    def is_top(self) -> bool:
        from .resolve import same

        return self.top is not None and same(self.top, self.root)


def _git(root) -> Callable:
    from moxi_repo.api import git

    return lambda *args, **kw: git(root, *args, **kw)


def where(root) -> Where:
    """What git says about `root`: two `git rev-parse` calls at most, in a clean
    environment, so a caller inside a git hook gets the answer for `root` itself."""
    from . import blobs

    root = os.fspath(root)
    if not os.path.isdir(root):
        return Where(root, False)
    run = _git(root)
    try:
        common = os.fsdecode(blobs.run(run, "rev-parse", "--path-format=absolute",
                                       "--git-common-dir")).strip()
    except blobs.GitError:
        return Where(root, True)
    try:
        top = os.fsdecode(blobs.run(run, "rev-parse", "--path-format=absolute",
                                    "--show-toplevel")).strip() or None
    except blobs.GitError:
        top = None
    return Where(root, True, common or None, top)


def private_allowlist(common_dir) -> str:
    """The private allowlist's full path for a repo whose common git dir is `common_dir`."""
    from .judge import PRIVATE

    return os.path.join(os.fspath(common_dir), PRIVATE)


# -- the preview --------------------------------------------------------------------------

@dataclass
class Preview:
    """What the preview found. `found` maps each path with a finding to its findings;
    `names` and `author_names` hold `(rule, word)` pairs of private names found in files
    and in commit authors; `problem` says why the allowlist reads as empty; `error` why
    nothing could be judged."""

    judged: int = 0
    found: dict = field(default_factory=dict)
    authors: int = 0
    names: list = field(default_factory=list)
    author_names: list = field(default_factory=list)
    problem: str | None = None
    error: str | None = None


def _files(run) -> list[str]:
    """The tracked and untracked files a snapshot takes, ignored files and nested
    repositories left out."""
    from . import blobs

    out = blobs.run(run, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    paths = []
    for raw in out.split(b"\0"):
        if raw and not raw.endswith(b"/"):
            paths.append(os.fsdecode(raw))
    return list(dict.fromkeys(paths))


def _content(full: str, cap: int):
    """(bytes, None), (None, size) when over `cap`, or (None, None) when there is no file
    to read (gone, a folder, a submodule)."""
    try:
        st = os.lstat(full)
    except OSError:
        return None, None
    import stat

    if stat.S_ISLNK(st.st_mode):
        # git stores a link as its target, so the snapshot judges that text.
        try:
            return os.fsencode(os.readlink(full)), None
        except OSError:
            return None, None
    if not stat.S_ISREG(st.st_mode):
        return None, None
    if st.st_size > cap:
        return None, st.st_size
    try:
        with open(full, "rb") as fh:
            return fh.read(), None
    except OSError:
        return None, None


def _word(text: str) -> str:
    """A matched name as a `text:` value: case folded and whitespace runs as one space,
    as secrets compares it, so the line is the same whichever spelling came first."""
    return " ".join(text.casefold().split())


def _add_name(names: list, rule: str, word: str) -> None:
    if (rule, word) not in names:
        names.append((rule, word))


def _identities(run, push: str | None) -> list[str]:
    """Each distinct author and committer, `name <email>`, of the commits a push of every
    local branch would carry: those not on `push`'s remote-tracking refs, or every one
    when no push remote is set."""
    from . import blobs

    args = ["log", "--branches", "-z", "--format=%an <%ae>%x00%cn <%ce>"]
    if push:
        args += ["--not", f"--remotes={push}"]
    out = blobs.run(run, *args)
    seen = []
    for raw in out.split(b"\0"):
        text = raw.decode("utf-8", "surrogateescape").strip("\n")
        if text and text not in seen:
            seen.append(text)
    return seen


def preview(root, *, push: str | None = None, common_dir=None) -> Preview:
    """Judge `root`'s working tree as a snapshot would, and the commit authors as the push
    gate would."""
    import moxi_secrets
    from moxi.config import ConfigError

    from . import blobs, judge

    found = Preview()
    root = os.fspath(root)
    run = _git(root)
    try:
        settings = judge.config()
        cap = judge.max_bytes(settings)
        common = common_dir or judge.common_dir(run)
        allow, found.problem = judge.allowlist(root, common)
        paths = _files(run)
        for path in paths:
            data, size = _content(os.path.join(root, path), cap)
            if size is not None:
                found.judged += 1
                found.found[path] = [judge.too_large(size, cap)]
                continue
            if data is None:
                continue
            found.judged += 1
            text = blobs.text(data)
            hits = moxi_secrets.scan(text, judge.SETS, path=path, allow=allow)
            if hits:
                found.found[path] = [judge.of_hit(h) for h in hits]
                for h in hits:
                    if h.set == "private-names":
                        _add_name(found.names, f"{h.set}/{h.rule}",
                                  _word(text[h.start:h.end]))
        for who in _identities(run, push):
            hits = moxi_secrets.scan(who, ("private-names",), path="@author", allow=allow)
            if hits:
                found.authors += 1
                for h in hits:
                    _add_name(found.author_names, f"{h.set}/{h.rule}",
                              _word(who[h.start:h.end]))
    except moxi_secrets.SetUnavailable as exc:
        found.error = f"a set notes judges with can't run: {exc}"
    except ConfigError as exc:
        found.error = f"notes' config can't be read: {exc}"
    except blobs.GitError as exc:
        found.error = f"the notes could not be read: {exc}"
    return found


def allowance_lines(found: Preview, today: str) -> list[str]:
    """The private allowlist lines that keep the private names `found` holds."""
    lines = [f"*\t{rule}\ttext:{word}\t{today}\tpaths in my own notes"
             for rule, word in found.names]
    lines += [f"@author\t{rule}\ttext:{word}\t{today}\tmy own commits"
              for rule, word in found.author_names]
    return lines


def print_preview(found: Preview, *, action: str, common_dir, out: TextIO,
                  today: str | None = None) -> None:
    from . import judge

    if found.error:
        print(f"notes: no preview: {found.error}", file=out)
        return
    count = len(found.found)
    print(f"notes: judged {found.judged} files as a snapshot would; {count} hold a "
          "secret by notes' sets (credentials and private names)", file=out)
    if found.problem:
        print(f"notes: judged with no allowances, since {found.problem}", file=out)
    if count:
        if action == "redact":
            print("  with redact, each one's committed and pushed copy has its hits "
                  "blanked, and a cloud session reading the pushed copy sees them "
                  "blanked; one that can't be redacted is left out", file=out)
        else:
            print("  with leave-out, each one leaves the snapshots at its next change, "
                  "until its hit is removed or allowed", file=out)
        for path in sorted(found.found)[:SHOWN]:
            print(f"  {path}  {judge.words(found.found[path])}", file=out)
        if count > SHOWN:
            print(f"  and {count - SHOWN} more; see them all with a snapshot's record",
                  file=out)
    if found.authors:
        noun = "author holds" if found.authors == 1 else "authors hold"
        print(f"notes: {found.authors} commit {noun} a private name; the push gate "
              "refuses a push of their commits, hand commits included, until an @author "
              "allowance keeps it", file=out)
    lines = allowance_lines(found, today or datetime.date.today().isoformat())
    if not judge.private_place_ok(common_dir):
        print(f"notes: the notes repo's git dir {common_dir} is not a folder named .git, "
              "so secrets reads no private allowlist for it: no private allowance can "
              "apply, every file with a hit stays left out and every push with one is "
              "refused", file=out)
        return
    print(f"notes: the private allowlist is {private_allowlist(common_dir)}, in the git "
          "dir, where git never tracks it", file=out)
    if lines:
        print("notes: a private name on your list is often in your own paths and "
              "commits. These allowance lines keep the ones found; add those you mean to "
              "keep to the private allowlist:", file=out)
        for line in lines:
            # Unindented, so a copied line is a line the allowlist reads as it is.
            print(line, file=out)


# -- the questions ------------------------------------------------------------------------

def _ask(question: str) -> str:
    try:
        return input(question)
    except EOFError:
        print()
        return ""


def _shown(path: str) -> str:
    """`path` with the home folder spelled `~`."""
    from pathlib import Path

    from .resolve import below

    home = str(Path.home())
    parts = below(path, home, real=False)
    if parts is None:
        return path
    return "~" if not parts else "~/" + "/".join(parts)


def _yes(answer: str, default: bool) -> bool | None:
    text = answer.strip().lower()
    if not text:
        return default
    if text in ("y", "yes"):
        return True
    if text in ("n", "no"):
        return False
    return None


def _ask_root(current: str, ask, out) -> str:
    shown = _shown(current)
    if ask is None:
        print(f"notes: the notes root is {shown}; change it with: moxi config set notes "
              "root PATH, then moxi notes setup", file=out)
        return current
    answer = ask(f"Where are your notes? [{shown}] ").strip()
    if not answer:
        return current
    expanded = os.path.expanduser(answer)
    if not os.path.isabs(expanded):
        answer = os.path.abspath(expanded)
    return answer


def _ask_init(root: str, ask, out) -> bool:
    question = f"{_shown(root)} is in no git repo. Make it a git repo so notes can " \
               "snapshot it? [Y/n] "
    if ask is None:
        print(f"notes: {_shown(root)} is in no git repo; making it one (the default) so "
              "notes can snapshot it", file=out)
        return True
    for _ in range(3):
        answer = _yes(ask(question), True)
        if answer is not None:
            return answer
    return True


def _ask_action(current: str, ask, out) -> str:
    if ask is None:
        other = "redact" if current == "leave-out" else "leave-out"
        print(f"notes: a note that holds a secret is handled by {current}; change it "
              f"with: moxi config set notes action {other}, then moxi notes setup",
              file=out)
        return current
    default = ACTIONS.index(current) + 1
    for _ in range(3):
        answer = ask(ACTION_QUESTION.format(default=default)).strip().lower()
        if not answer:
            return current
        if answer in ("1", "leave-out"):
            return "leave-out"
        if answer in ("2", "redact"):
            return "redact"
    return current


def _remotes(run) -> list[str]:
    from . import blobs

    try:
        return os.fsdecode(blobs.run(run, "remote")).split()
    except blobs.GitError:
        return []


def _ask_push(remotes: list[str], ask, out) -> str:
    if ask is None:
        print(f"notes: snapshots are not pushed; push them with: moxi config set notes "
              f"snapshot.push {remotes[0]}", file=out)
        return ""
    choices = "/".join([*remotes, "none"])
    for _ in range(3):
        answer = ask(f"Push snapshots to which remote? [{choices}] ").strip()
        if not answer or answer.lower() == "none":
            return ""
        if answer in remotes:
            return answer
    return ""


# -- the writes ---------------------------------------------------------------------------

def _print_report(report, out) -> bool:
    """Print repo's report for the pieces; True when it holds no problem."""
    for line in report.lines:
        print(f"notes: {line}", file=out)
    for root, event, hook, line, present in report.chain:
        if not present:
            print(f"notes: {root}: {event}: git runs notes' gates only once this line is in "
                  f"{hook}, before any line that reads stdin:\n  {line}", file=out)
    for problem in report.problems:
        print(f"notes: {problem}", file=out)
    return report.ok


def _pieces(root: str, out) -> bool:
    """Register both pieces in the repo at `root` and enable a disabled one."""
    from moxi_repo import api

    try:
        ok = _print_report(api.add(root, *PIECES), out)
        listed = api.status(root)
        state = {p["id"]: p["enabled"] for p in (listed[0]["pieces"] if listed else [])}
        for pid in PIECES:
            if state.get(pid) is False:
                ok = _print_report(api.enable(root, pid), out) and ok
    except api.Failure as exc:
        print(f"notes: the git hook pieces were not registered: {exc.text}", file=out)
        for line in exc.lines:
            print(f"  {line}", file=out)
        return False
    return ok


def _filter(root: str, action: str, out) -> bool:
    from . import blobs
    from . import filter as clean_filter

    try:
        if action == "redact":
            clean_filter.install(root)
            print(f"notes: the clean filter redacts what {_shown(root)} commits; its "
                  "driver is not required, so a file it fails on is left out instead",
                  file=out)
        else:
            clean_filter.remove(root)
    except blobs.GitError as exc:
        print(f"notes: the clean filter was not {'written' if action == 'redact' else 'removed'}: "
              f"{exc}", file=out)
        return False
    return True


def _spec(out) -> bool:
    from moxi import files
    from moxi_repo.api import SpecError

    from . import spec

    try:
        saved = spec.ensure_saved(spec.build())
    except files.LockTimeout:
        print("notes: a snapshot pass holds the notes repo's lock, so the snapshot spec "
              "is saved by the next pass", file=out)
        return True
    except SpecError as exc:
        print(f"notes: the snapshot spec was not saved: {exc}", file=out)
        return False
    if saved:
        print(f"notes: saved the snapshot spec {spec.NAME}", file=out)
    return True


def _set(key: str, value, out) -> bool:
    from moxi import config

    try:
        config.set(MODULE, key, value, by=BY)
    except config.ConfigError as exc:
        print(f"notes: {key} was not set: {exc}", file=out)
        return False
    return True


# -- SETUP and TEARDOWN -------------------------------------------------------------------

def setup(*, ask: Callable[[str], str] | None = None, out: TextIO | None = None,
          today: str | None = None) -> int:
    """`SETUP`: ask, preview, write. `ask` stands in for the question at the keyboard and
    returns the answer typed. Returns 0, or 1 when a write failed or the config can't be
    read."""
    from moxi.config import ConfigError

    from . import blobs

    out = sys.stdout if out is None else out
    if ask is None and sys.stdin is not None and sys.stdin.isatty():
        ask = _ask
    try:
        places = _settings.read()
    except ConfigError as exc:
        print(f"notes: setup can't read notes' config: {exc}", file=out)
        return 1
    current = places.settings
    changes: dict[str, object] = {}

    # 1. the root, and a git repo on it
    root_text = _ask_root(str(places.root), ask, out)
    root = os.path.normpath(os.path.expanduser(root_text))
    if not _same_path(root, str(places.root)):
        changes["root"] = root_text
    found = where(root)
    if not found.in_repo:
        if _ask_init(root, ask, out):
            try:
                os.makedirs(root, exist_ok=True)
                blobs.run(_git(root), "init", "-q")
            except (OSError, blobs.GitError) as exc:
                print(f"notes: {_shown(root)} was not made a git repo: {exc}", file=out)
                return 1
            print(f"notes: made {_shown(root)} a git repo", file=out)
            found = where(root)
        else:
            print(f"notes: {_shown(root)} is no git repo, so snapshots and the gates stay "
                  "off until it is one; run moxi notes setup again then", file=out)

    # 2. the action
    action = _ask_action(current["action"], ask, out)
    if action != current["action"]:
        changes["action"] = action

    # 3. the push remote
    push = current["snapshot.push"]
    if found.is_top and not push:
        remotes = _remotes(_git(root))
        if remotes:
            push = _ask_push(remotes, ask, out)
            if push:
                changes["snapshot.push"] = push

    # 5a. the settings, so the preview and the spec read them
    ok = all([_set(key, value, out) for key, value in changes.items()])
    if not ok:
        return 1
    if not found.is_top:
        if found.in_repo:
            print(f"notes: {_shown(root)} is inside the git repo "
                  f"{_shown(found.top) if found.top else found.common_dir}, not its top; "
                  "snapshots and the gates need the notes root to be a repo's top, so "
                  "nothing is written there. Set root to that top, or move the notes",
                  file=out)
        return 0

    # 4. the preview
    found_now = preview(root, push=push or None, common_dir=found.common_dir)
    print_preview(found_now, action=action, common_dir=found.common_dir, out=out,
                  today=today)

    # 5b. the notes repo
    ok = _pieces(root, out)
    ok = _filter(root, action, out) and ok
    ok = _spec(out) and ok
    return 0 if ok else 1


def _same_path(a: str, b: str) -> bool:
    from .resolve import same

    return same(a, b)


def teardown(*, out: TextIO | None = None) -> int:
    """`TEARDOWN`: remove the clean filter's driver and attribute from the notes repo.
    0 when done or when there is nothing to remove; 1 when notes' config can't be read or
    git fails, which stops the uninstall."""
    from moxi.config import ConfigError

    from . import blobs
    from . import filter as clean_filter

    out = sys.stdout if out is None else out
    try:
        places = _settings.read()
    except ConfigError as exc:
        print(f"notes: teardown can't read notes' config, so it can't find the notes "
              f"repo: {exc}", file=out)
        return 1
    root = str(places.root)
    found = where(root)
    if not found.in_repo:
        print(f"notes: {_shown(root)} is no git repo; nothing of notes' is there", file=out)
        return 0
    try:
        clean_filter.remove(root)
    except blobs.GitError as exc:
        print(f"notes: the clean filter could not be removed from {_shown(root)}: {exc}",
              file=out)
        return 1
    print(f"notes: no clean filter is left in {_shown(root)}; the notes are untouched",
          file=out)
    return 0


__all__ = ["ACTION_QUESTION", "PIECES", "Where", "where", "private_allowlist", "Preview",
           "preview", "allowance_lines", "print_preview", "setup", "teardown"]
