"""The git hook runner: `python -I -m moxi_repo.run <event> [git's arguments]`.

Every config entry, shim and launcher repo writes ends in this one command. It reads the
repo's piece registrations with one `git config` call, picks this event's enabled pieces
in order (`order`, then id), reads stdin once for an event that has it, and calls each
piece in this process, importing a piece's module only when that piece runs. Every
enabled piece runs, even after one refuses, and every refusal prints.

What it prints goes to stderr, and it says "git hook", never bare "hook". A piece's own
prints go to stderr too, so whoever ran git (a person, or the committer) sees them.

Exit codes: 0 when every piece passed; 3 when a piece refused; 1 when a closed piece broke,
or when the registrations could not be read on an event that can refuse. Events that
can't refuse (post-*) always exit 0, since git ignores their status.

This module imports the standard library only. A run that writes or clears a doctor
record imports more (`moxi.doctor` and repo's ledger); a run that calls a piece imports
`moxi_repo.pieces`, which is standard library only too.
"""

from __future__ import annotations

import os
import subprocess
import sys

#: The client-side git events the runner accepts.
EVENTS = frozenset({
    "applypatch-msg", "pre-applypatch", "post-applypatch",
    "pre-commit", "pre-merge-commit", "prepare-commit-msg", "commit-msg", "post-commit",
    "pre-rebase", "post-checkout", "post-merge", "pre-push", "pre-auto-gc",
    "post-rewrite", "reference-transaction", "post-index-change", "sendemail-validate",
})
#: Events git feeds on stdin; the runner reads it once and gives every piece the bytes.
STDIN_EVENTS = frozenset({"pre-push", "post-rewrite", "reference-transaction"})
#: Events whose exit status git honours, besides those named pre-*.
_REFUSING = frozenset({"prepare-commit-msg", "commit-msg", "applypatch-msg",
                       "reference-transaction", "sendemail-validate"})

#: The section and subsection prefix of a piece's registration in git config.
SECTION = "moxi-repo"
PIECE_PREFIX = SECTION + ".piece."
#: The keys of a registration that are not the piece's own settings.
REGISTRATION_KEYS = ("event", "target", "fail", "order", "enabled", "recorded")
DEFAULT_ORDER = 50

#: Set by the committer on its own commits: the spec's name, read into `ctx.committer`.
COMMITTER_ENV = "MOXI_REPO_COMMITTER"

#: Exit codes (the core's `moxi.exit`, written out so the runner imports nothing of it).
OK, ERROR, REFUSED = 0, 1, 3

_FALSE = ("false", "no", "off", "0", "")


def can_refuse(event: str) -> bool:
    """True when git honours the event's exit status (pre-* and the message events)."""
    return event.startswith("pre-") or event in _REFUSING


def parse_config(data: bytes) -> list[tuple[str, str | None]]:
    """`git config -z` output as (key, value) pairs; a key with no value reads None."""
    out = []
    for item in data.split(b"\0"):
        if not item:
            continue
        key, nl, value = item.partition(b"\n")
        out.append((key.decode("utf-8", "surrogateescape"),
                    value.decode("utf-8", "surrogateescape") if nl else None))
    return out


def pieces_of(pairs: list[tuple[str, str | None]]) -> dict[str, dict[str, list]]:
    """Piece id -> {variable: [values]} from `moxi-repo.piece.*` config pairs."""
    out: dict[str, dict[str, list]] = {}
    for key, value in pairs:
        if not key.startswith(PIECE_PREFIX):
            continue
        piece_id, dot, var = key[len(PIECE_PREFIX):].rpartition(".")
        if not dot or not piece_id:
            continue
        out.setdefault(piece_id, {}).setdefault(var, []).append(value)
    return out


def last(section: dict[str, list], key: str, default=None):
    values = section.get(key)
    return values[-1] if values else default


def is_true(value: str | None) -> bool:
    """git's reading of a boolean: a key with no value is true; empty is false."""
    return value is None or value.strip().lower() not in _FALSE


def enabled(section: dict[str, list]) -> bool:
    return "enabled" not in section or is_true(last(section, "enabled"))


def fail_policy(section: dict[str, list]) -> str:
    """'open' only when the registration says so; anything else is closed."""
    return "open" if (last(section, "fail") or "").strip().lower() == "open" else "closed"


def order_of(section: dict[str, list]) -> int:
    try:
        return int(last(section, "order", DEFAULT_ORDER))
    except (TypeError, ValueError):
        return DEFAULT_ORDER


def settings_of(section: dict[str, list]) -> dict[str, list[str]]:
    """The piece's own settings: every key of its section but the registration's."""
    return {k: [v if v is not None else "" for v in vals]
            for k, vals in section.items() if k not in REGISTRATION_KEYS}


def selected(pieces: dict[str, dict[str, list]], event: str) -> list[tuple[str, dict]]:
    """This event's enabled pieces, by order and then id."""
    chosen = [(pid, sec) for pid, sec in pieces.items()
              if last(sec, "event") == event and enabled(sec)]
    return sorted(chosen, key=lambda item: (order_of(item[1]), item[0]))


def read_registrations() -> dict[str, dict[str, list]] | None:
    """Every piece registered in this repo's git config, or None when git can't read it."""
    try:
        proc = subprocess.run(
            ["git", "config", "-z", "--get-regexp", r"^moxi-repo\.piece\."],
            stdin=subprocess.DEVNULL, capture_output=True)
    except OSError:
        return None
    if proc.returncode == 1 and not proc.stdout:
        return {}
    if proc.returncode != 0:
        return None
    return pieces_of(parse_config(proc.stdout))


def quote_arg(text) -> str:
    """`text` (a path or a string) as one word of a command a person pastes, quoted only
    when it needs it."""
    text = os.fspath(text)
    if sys.platform == "win32":
        bare = text and not any(c in text for c in ' \t"&|<>^()%!;,=')
        return text if bare else '"' + text.replace('"', '\\"') + '"'
    import shlex

    return shlex.quote(text)


def say(line: str) -> None:
    sys.stderr.write(f"repo: {line}\n")


def describe(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    first = text[0] if text else ""
    first = first if len(first) <= 200 else first[:197] + "..."
    return f"{type(exc).__name__}: {first}" if first else type(exc).__name__


class Where:
    """The repo the git hook runs in, asked of git once, on first use."""

    __slots__ = ("_done", "root", "git_dir", "common_dir")

    def __init__(self) -> None:
        self._done = False
        self.root = self.git_dir = self.common_dir = None

    def load(self) -> "Where":
        if self._done:
            return self
        self._done = True
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-dir",
                 "--git-common-dir", "--show-toplevel"],
                stdin=subprocess.DEVNULL, capture_output=True)
        except OSError:
            return self
        lines = proc.stdout.decode("utf-8", "surrogateescape").splitlines()
        if len(lines) >= 2:
            self.git_dir, self.common_dir = lines[0], lines[1]
        if len(lines) >= 3 and proc.returncode == 0:
            self.root = lines[2]
        return self


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        say("the git hook runner needs an event: python -m moxi_repo.run <event> [args]")
        return ERROR
    event, args = argv[0], tuple(argv[1:])
    refusing = can_refuse(event)
    if event not in EVENTS:
        say(f"{event} is not a git event the git hook runner knows")
        return ERROR if refusing else OK
    registered = read_registrations()
    if registered is None:
        if refusing:
            say(f"{event} refused: the git hook registrations could not be read "
                "(run: git config --get-regexp moxi-repo)")
            return ERROR
        say(f"{event}: the git hook registrations could not be read")
        return OK
    chosen = selected(registered, event)
    stdin = sys.stdin.buffer.read() if event in STDIN_EVENTS and sys.stdin else b""
    if not chosen:
        return OK
    return _run(event, args, stdin, chosen, refusing)


def _run(event: str, args: tuple, stdin: bytes, chosen: list, refusing: bool) -> int:
    from moxi_repo import pieces

    where = Where()
    committer = os.environ.get(COMMITTER_ENV) or None
    refused = broke = False
    for piece_id, section in chosen:
        ctx = pieces.HookContext(event, args, stdin, settings_of(section), committer, where)
        outcome, error = _call(last(section, "target") or "", ctx, pieces.Outcome)
        if error is None and not _fits(outcome, refusing):
            error = (f"returned {outcome.kind}, which "
                     + ("only post-* git hooks take" if refusing
                        else "only pre-* and commit-msg git hooks take"))
        flagged = "recorded" in section
        if error is not None:
            closed = fail_policy(section) == "closed"
            root = quote_arg(where.load().root or ".")
            fix = (f"moxi repo git-hooks disable {root} {piece_id} "
                   f"(or: moxi repo git-hooks remove {root} {piece_id})")
            if refusing and closed:
                broke = True
                say(f"{event} refused: {piece_id} broke ({error}); fix it or run: "
                    f"moxi repo git-hooks disable {root} {piece_id}")
            elif refusing:
                say(f"{event} went on: {piece_id} broke ({error}); the doctor keeps a record")
            else:
                say(f"{event}: {piece_id} broke ({error}); the doctor keeps a record")
            _record(where, piece_id, f"{event} git hook: {piece_id} broke ({error})", fix,
                    flagged)
            continue
        if outcome.kind == "refuse":
            refused = True
            say(f"{event} refused by {piece_id}: {outcome.lines[0]}")
            for more in outcome.lines[1:]:
                sys.stderr.write(f"  {more}\n")
        elif outcome.kind == "notice":
            say(f"{event}, {piece_id}: {outcome.lines[0]}")
        elif outcome.kind == "record":
            root = quote_arg(where.load().root or ".")
            _record(where, piece_id, f"{event} git hook: {piece_id}: {outcome.lines[0]}",
                    f"moxi repo git-hooks disable {root} {piece_id} "
                    f"(or: moxi repo git-hooks remove {root} {piece_id})", flagged)
            continue
        if flagged:
            _clear(where, piece_id)
    sys.stderr.flush()
    if not refusing:
        return OK
    if broke:
        return ERROR
    return REFUSED if refused else OK


def _fits(outcome, refusing: bool) -> bool:
    if outcome.kind == "ok":
        return True
    return outcome.kind == "refuse" if refusing else outcome.kind in ("notice", "record")


def _call(target: str, ctx, outcome_type):
    """Import and call one piece; (outcome, None) or (None, why it broke)."""
    module, _, name = target.partition(":")
    out = sys.stdout
    sys.stdout = sys.stderr
    try:
        if not module or not name:
            raise ImportError(f"{target!r} is not a target of the form package.module:function")
        func = getattr(__import__(module, fromlist=["_"]), name)
        result = func(ctx)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - a piece's failure is reported
        return None, describe(exc)
    finally:
        try:
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        sys.stdout = out
    if not isinstance(result, outcome_type):
        return None, f"returned {type(result).__name__}, not a moxi_repo.Outcome"
    return result, None


def _record(where: Where, piece_id: str, text: str, fix: str, flagged: bool) -> None:
    """Ledger key, then the `recorded` flag, then the doctor record."""
    common = where.load().common_dir
    if not common:
        say(f"the doctor record for {piece_id} could not be written: no git dir found")
        return
    try:
        from moxi_repo import _records

        _records.open_record(
            _records.piece_key(common, piece_id), cgd=common, kind="piece",
            subject={"piece": piece_id, "root": where.root}, text=text, fix=fix,
            before=None if flagged else (lambda: _flag(piece_id, True)))
    except Exception as exc:  # noqa: BLE001 - a record that can't be written is said
        say(f"the doctor record for {piece_id} could not be written ({describe(exc)})")


def _clear(where: Where, piece_id: str) -> None:
    """The doctor record, then the ledger key, then the flag."""
    common = where.load().common_dir
    if not common:
        return
    try:
        from moxi_repo import _records

        _records.close_record(_records.piece_key(common, piece_id),
                              after=lambda: _flag(piece_id, False))
    except Exception as exc:  # noqa: BLE001
        say(f"the doctor record for {piece_id} could not be cleared ({describe(exc)})")


#: Waits before the second and third try of a flag write another git's config lock refused.
FLAG_RETRY = (0.02, 0.05)


def _flag(piece_id: str, value: bool) -> bool:
    """Set or unset `recorded` in the piece's section; True when git took it.

    Another git holding the config lock makes the write fail; it is tried twice more. A
    flag that still can't be set is set by the next sweep; one that can't be unset is
    unset by the next good run.
    """
    import time

    key = f"{PIECE_PREFIX}{piece_id}.recorded"
    argv = (["git", "config", key, "true"] if value
            else ["git", "config", "--unset-all", key])
    for wait in (0.0, *FLAG_RETRY):
        if wait:
            time.sleep(wait)
        try:
            proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True)
        except OSError:
            return False
        # --unset-all exits 5 when the key is already gone.
        if proc.returncode == 0 or (not value and proc.returncode == 5):
            return True
    return False


if __name__ == "__main__":
    # Run as `-m`, this file is `__main__`; `moxi_repo.pieces` imports it by its own
    # name, which then finds this module rather than loading the file a second time.
    me = sys.modules.get(__name__)
    if me is not None and getattr(me, "__dict__", None) is globals():
        sys.modules.setdefault("moxi_repo.run", me)
    sys.exit(main())
