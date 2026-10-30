"""repo's doctor records, and the ledger of the ones it has open.

This module is the only code in repo that calls `moxi.doctor.record` or `clear`. Each key
names one condition in one repo:

- `<cgd>::spec::<name>::commit`: a committer spec's last conclusive pass;
- `<cgd>::spec::<name>::push::<branch>`: the spec's last push of that branch;
- `<cgd>::piece::<id>`: a piece's last run broke, or a post-* piece returned `record`.

`<cgd>` is the repo's common git dir, `normcase`d. A newer record with the same key
replaces the older, so there is at most one per key.

The ledger (`STATE/repo/records.json`, the `records` store) holds every key repo has open,
with its repo's common git dir, its kind and its subject. A key enters the ledger before
its record is written and leaves after its record is cleared, so the ledger names every
open key even when the repo it names is gone. A `clear` that fails because the record is
already gone counts as done. No code holds the ledger's lock across a git call.

`sweep()` clears each key whose subject is gone: its repo's common git dir doesn't exist,
or its piece's section is missing or disabled (and then unsets the disabled piece's
`recorded` flag); and it sets a missing `recorded` flag on an open piece key whose piece is
registered and enabled. Spec and push keys are judged by the
committer's own rules, through `SPEC_GONE`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

MODULE = "repo"
#: The ledger's file name under the module's state directory.
LEDGER = "records.json"
KINDS = ("piece", "commit", "push")


def key_of(cgd: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(cgd)))


def piece_key(cgd: str | os.PathLike, piece_id: str) -> str:
    return f"{key_of(cgd)}::piece::{piece_id}"


def commit_key(cgd: str | os.PathLike, spec: str) -> str:
    return f"{key_of(cgd)}::spec::{spec}::commit"


def push_key(cgd: str | os.PathLike, spec: str, branch: str) -> str:
    return f"{key_of(cgd)}::spec::{spec}::push::{branch}"


def ledger_path() -> Path:
    from moxi import paths

    return paths.Layout(MODULE).state_dir() / LEDGER


def ledger() -> dict[str, dict]:
    """Every open key: key -> {"cgd", "kind", "subject"}."""
    try:
        data = json.loads(ledger_path().read_bytes())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _change(fn: Callable[[dict], None]) -> None:
    from moxi import files

    def edit(old: bytes | None) -> bytes:
        try:
            data = json.loads(old) if old else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        fn(data)
        return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
                + "\n").encode()

    files.update(ledger_path(), edit)


def open_record(key: str, *, cgd: str | os.PathLike, kind: str, subject: dict, text: str,
                fix: str | None = None, before: Callable[[], object] | None = None) -> None:
    """The ledger key, then `before()` (a piece's `recorded` flag), then the record."""
    from moxi import doctor

    if kind not in KINDS:
        raise ValueError(f"a record's kind is one of {', '.join(KINDS)}, not {kind!r}")
    entry = {"cgd": key_of(cgd), "kind": kind, "subject": dict(subject)}

    def put(data: dict) -> None:
        data[key] = entry

    _change(put)
    if before is not None:
        before()
    doctor.record(MODULE, text, key=key, fix=fix)


def close_record(key: str, *, after: Callable[[], object] | None = None) -> None:
    """The record, then the ledger key, then `after()` (unsetting a piece's flag)."""
    from moxi import doctor

    try:
        doctor.clear(MODULE, key)
    except (KeyError, LookupError):
        pass

    def drop(data: dict) -> None:
        data.pop(key, None)

    _change(drop)
    if after is not None:
        after()


def keys_of_repo(cgd: str | os.PathLike) -> list[str]:
    want = key_of(cgd)
    return sorted(k for k, v in ledger().items() if v.get("cgd") == want)


def close_repo(cgd: str | os.PathLike) -> list[str]:
    """Clear every key of one repo (`remove PATH`); return the keys cleared."""
    keys = keys_of_repo(cgd)
    for key in keys:
        close_record(key)
    return keys


def close_all() -> list[str]:
    """Clear every key repo has open (repo's own uninstall); return them."""
    keys = sorted(ledger())
    for key in keys:
        close_record(key)
    return keys


def close_piece(cgd: str | os.PathLike, piece_id: str) -> bool:
    """Clear one piece's record if it is open; True when it was."""
    key = piece_key(cgd, piece_id)
    if key not in ledger():
        return False
    close_record(key)
    return True


#: The committer's judge of a spec or push key: fn(cgd, kind, subject) -> why it is gone,
#: or None while its subject stands. The committer sets it; until then such keys are left
#: to the committer's own pass, and only a repo that is gone clears them here.
SPEC_GONE: Callable[[str, str, dict], str | None] | None = None


#: The reason a piece key is cleared whose section stays, so its flag is unset too.
DISABLED = "its piece is disabled"


class Finding:
    """One thing the sweep found: a key to clear, or a flag to set."""

    __slots__ = ("key", "action", "reason", "cgd", "subject")

    def __init__(self, key: str, action: str, reason: str, cgd: str, subject: dict):
        self.key, self.action, self.reason = key, action, reason
        self.cgd, self.subject = cgd, subject

    def __repr__(self) -> str:
        return f"Finding({self.key!r}, {self.action!r}, {self.reason!r})"


def _pieces(cgd: str) -> dict[str, dict[str, list]] | None:
    from moxi_repo.gitrun import git
    from moxi_repo.run import parse_config, pieces_of

    done = git(cgd, f"--git-dir={cgd}", "config", "--local", "-z", "--get-regexp",
               r"^moxi-repo\.piece\.")
    if done.returncode == 1 and not done.stdout:
        return {}
    if done.returncode != 0:
        return None
    return pieces_of(parse_config(done.stdout.encode("utf-8", "surrogateescape")))


def judge(repo: str | os.PathLike | None = None) -> list[Finding]:
    """What a sweep would do, writing nothing: what the doctor shows as `warn` rows."""
    from moxi_repo.run import enabled

    want = key_of(repo) if repo is not None else None
    by_repo: dict[str, dict[str, dict]] = {}
    for key, entry in ledger().items():
        cgd = entry.get("cgd") or ""
        if want is None or cgd == want:
            by_repo.setdefault(cgd, {})[key] = entry
    out: list[Finding] = []
    for cgd, entries in sorted(by_repo.items()):
        if not cgd or not os.path.isdir(cgd):
            for key, entry in sorted(entries.items()):
                out.append(Finding(key, "clear", "its repo is gone", cgd,
                                   entry.get("subject") or {}))
            continue
        pieces = None
        if any(e.get("kind") == "piece" for e in entries.values()):
            pieces = _pieces(cgd)
        for key, entry in sorted(entries.items()):
            kind, subject = entry.get("kind"), entry.get("subject") or {}
            if kind == "piece":
                if pieces is None:
                    continue
                section = pieces.get(subject.get("piece", ""))
                if section is None:
                    out.append(Finding(key, "clear", "its piece is no longer registered",
                                       cgd, subject))
                elif not enabled(section):
                    out.append(Finding(key, "clear", DISABLED, cgd, subject))
                elif "recorded" not in section:
                    out.append(Finding(key, "flag", "its piece has no recorded flag", cgd,
                                       subject))
            elif SPEC_GONE is not None:
                why = SPEC_GONE(cgd, kind, subject)
                if why:
                    out.append(Finding(key, "clear", why, cgd, subject))
    return out


def sweep(repo: str | os.PathLike | None = None) -> list[Finding]:
    """Clear each open key whose subject is gone and set each missing `recorded` flag, in
    every repo or in `repo` (a common git dir); return what was done."""
    from moxi_repo.gitrun import git

    found = judge(repo)
    for item in found:
        flag = f"moxi-repo.piece.{item.subject.get('piece')}.recorded"
        if item.action == "clear" and item.reason == DISABLED:
            close_record(item.key, after=lambda cgd=item.cgd, flag=flag: git(
                cgd, f"--git-dir={cgd}", "config", "--local", "--unset-all", flag))
        elif item.action == "clear":
            close_record(item.key)
        elif item.action == "flag":
            git(item.cgd, f"--git-dir={item.cgd}", "config", "--local", flag, "true")
    return found


__all__ = ["MODULE", "piece_key", "commit_key", "push_key", "ledger", "open_record",
           "close_record", "close_repo", "close_all", "close_piece", "keys_of_repo", "judge",
           "sweep", "Finding", "SPEC_GONE"]
