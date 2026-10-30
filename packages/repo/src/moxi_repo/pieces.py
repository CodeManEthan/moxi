"""Pieces: what a module declares, what a piece is given, and what it returns.

A module declares a piece in the entry-point group `moxi.repo.git_hooks`, under the entry
name `<module>.<name>`, pointing at a table in a light module:

    [project.entry-points."moxi.repo.git_hooks"]
    "notes.secret-gate" = "moxi_notes.gitpieces:SECRET_GATE"

    SECRET_GATE = {
        "event": "pre-commit",                    # one git event per piece
        "target": "moxi_notes.gates:pre_commit",  # the function the runner calls
        "fail": "closed",                         # "closed" or "open"
        "order": 20,                              # 0-99, default 50
        "doc": "Refuse a commit that adds a secret.",
        "settings": {"refused-name": "multi"},    # optional: per-repo settings, "one" or "multi"
    }

The piece's id is `<module>/<name>`. Its function takes a `HookContext` and returns an
`Outcome`; one that raises, or can't be imported, has broken, and its fail policy says
what that does to the event.

A setting's name is spelled the way git hands a config variable back: lower-case letters,
digits and `-`, starting with a letter. So the name a piece declares, the name `--set`
writes and the key in `ctx.settings` are one string. A setting may not take a name the
registration itself uses (`event`, `target`, `fail`, `order`, `enabled`, `recorded`),
since both live in the same section of the repo's git config.

The context and the outcome are what the git hook runner loads, so this module imports
the standard library only, and only what the runner already has at module level;
reading declarations imports more, inside the functions that do it.
"""

from __future__ import annotations

import os
import subprocess

from moxi_repo.run import DEFAULT_ORDER, EVENTS, REGISTRATION_KEYS, can_refuse

#: The extension group pieces are declared in.
GROUP = "moxi.repo.git_hooks"
FAIL_POLICIES = ("closed", "open")
SETTING_KINDS = ("one", "multi")
#: Keys a declaration may hold.
DECLARATION_KEYS = frozenset({"event", "target", "fail", "order", "doc", "settings"})


class Outcome:
    """What a piece returns. Make one with `ok()`, `refuse()`, `notice()` or `record()`."""

    __slots__ = ("kind", "lines")

    def __init__(self, kind: str, lines: tuple[str, ...] = ()):
        self.kind = kind
        self.lines = lines

    @classmethod
    def ok(cls) -> "Outcome":
        """The piece passed."""
        return cls("ok")

    @classmethod
    def refuse(cls, line: str, *more: str) -> "Outcome":
        """pre-* and commit-msg only: the event fails and the lines print."""
        return cls("refuse", (str(line), *(str(m) for m in more)))

    @classmethod
    def notice(cls, line: str) -> "Outcome":
        """post-* only: a failure on something the committing agent asked for; the line
        prints to stderr."""
        return cls("notice", (str(line),))

    @classmethod
    def record(cls, line: str) -> "Outcome":
        """post-* only: any other failure; one doctor record, nothing printed."""
        return cls("record", (str(line),))

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Outcome) and other.kind == self.kind
                and other.lines == self.lines)

    def __hash__(self) -> int:
        return hash((self.kind, self.lines))

    def __repr__(self) -> str:
        return f"Outcome.{self.kind}({', '.join(map(repr, self.lines))})"


class PushUpdate:
    """One line git gives a pre-push git hook on stdin."""

    __slots__ = ("local_ref", "local_sha", "remote_ref", "remote_sha")

    def __init__(self, local_ref: str, local_sha: str, remote_ref: str, remote_sha: str):
        self.local_ref, self.local_sha = local_ref, local_sha
        self.remote_ref, self.remote_sha = remote_ref, remote_sha

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PushUpdate) and self._tuple() == other._tuple()

    def __hash__(self) -> int:
        return hash(self._tuple())

    def _tuple(self) -> tuple:
        return (self.local_ref, self.local_sha, self.remote_ref, self.remote_sha)

    def __repr__(self) -> str:
        return "PushUpdate({!r}, {!r}, {!r}, {!r})".format(*self._tuple())


class HookContext:
    """What a piece is given.

    `event`; `args`, git's arguments; `stdin`, the bytes git fed the git hook (empty for
    an event without stdin); `settings`, the piece's per-repo settings, each name to its
    list of values; `committer`, the spec's name when repo's committer is making this
    commit, else None. `root` (the work tree's top), `git_dir` and `common_dir` are
    absolute paths, asked of git on first use. `message_path` is commit-msg's message
    file; `updates` are pre-push's ref lines.

    `git(*args, input=None)` runs git with the git hook's own environment, so a partly
    staged commit is judged as it will land. A piece that reads another repository uses
    `moxi_repo.git()`, which drops git's local variables first.
    """

    __slots__ = ("event", "args", "stdin", "settings", "committer", "_where")

    def __init__(self, event: str, args: tuple[str, ...] = (), stdin: bytes = b"",
                 settings: dict[str, list[str]] | None = None, committer: str | None = None,
                 where=None):
        self.event = event
        self.args = tuple(args)
        self.stdin = stdin
        self.settings = dict(settings or {})
        self.committer = committer
        if where is None:
            from moxi_repo.run import Where

            where = Where()
        self._where = where

    @property
    def root(self) -> str | None:
        return self._where.load().root

    @property
    def git_dir(self) -> str | None:
        return self._where.load().git_dir

    @property
    def common_dir(self) -> str | None:
        return self._where.load().common_dir

    @property
    def message_path(self) -> str | None:
        if self.event not in ("commit-msg", "prepare-commit-msg", "applypatch-msg"):
            return None
        if not self.args:
            return None
        return os.path.abspath(self.args[0])

    @property
    def updates(self) -> tuple[PushUpdate, ...]:
        if self.event != "pre-push":
            return ()
        out = []
        for line in self.stdin.decode("utf-8", "surrogateescape").splitlines():
            parts = line.split(" ")
            if len(parts) == 4:
                out.append(PushUpdate(*parts))
        return tuple(out)

    def git(self, *args: str, input: bytes | str | None = None,
            timeout: float | None = None,
            binary: bool = False) -> subprocess.CompletedProcess:
        """Run `git <args>` with this git hook's environment, in its working directory.

        `input` is fed on stdin (else stdin is closed). Returns the finished process with
        `stdout` and `stderr` as text (UTF-8; bytes that aren't are kept as surrogates);
        with `binary`, `stdout` is the bytes git wrote. The exit status is not checked.
        `timeout` raises `subprocess.TimeoutExpired`.
        """
        data = input.encode("utf-8") if isinstance(input, str) else input
        proc = subprocess.run(["git", *args], input=data,
                              stdin=subprocess.DEVNULL if data is None else None,
                              capture_output=True, timeout=timeout)
        return subprocess.CompletedProcess(
            proc.args, proc.returncode,
            proc.stdout if binary else proc.stdout.decode("utf-8", "surrogateescape"),
            proc.stderr.decode("utf-8", "surrogateescape"))

    def __repr__(self) -> str:
        return f"HookContext({self.event!r}, args={self.args!r})"


# -- declarations -------------------------------------------------------------------------

class DeclarationError(Exception):
    """A piece's declaration that repo refuses; `reason` names the key."""

    def __init__(self, piece_id: str, reason: str):
        super().__init__(f"{piece_id}: {reason}")
        self.piece_id = piece_id
        self.reason = reason


class Piece:
    """A checked declaration."""

    __slots__ = ("id", "module", "name", "event", "target", "fail", "order", "doc",
                 "settings", "entry")

    def __init__(self, *, id: str, module: str, name: str, event: str, target: str,
                 fail: str, order: int, doc: str, settings: dict[str, str], entry: str):
        self.id, self.module, self.name = id, module, name
        self.event, self.target, self.fail, self.order = event, target, fail, order
        self.doc, self.settings, self.entry = doc, dict(settings), entry

    def registration(self) -> dict[str, str]:
        """The keys the declaration gives, as they are written into git config."""
        return {"event": self.event, "target": self.target, "fail": self.fail,
                "order": str(self.order)}

    def as_dict(self) -> dict:
        return {"id": self.id, "module": self.module, "event": self.event,
                "target": self.target, "fail": self.fail, "order": self.order,
                "doc": self.doc, "settings": dict(self.settings)}

    def __repr__(self) -> str:
        return f"Piece({self.id!r}, {self.event!r})"


def piece_id(entry_name: str) -> str:
    """`<module>/<name>` from the entry name `<module>.<name>`."""
    module, _, name = entry_name.partition(".")
    return f"{module}/{name}" if name else entry_name


def check(entry_name: str, value: object) -> Piece:
    """Check one declaration; return the `Piece` or raise `DeclarationError`."""
    import re

    pid = piece_id(entry_name)
    module, _, name = entry_name.partition(".")
    if not re.fullmatch(r"[a-z0-9]+", module) or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*",
                                                                   name):
        raise DeclarationError(pid, f"the entry name {entry_name!r} is not "
                                    "<module>.<name> (lower-case letters, digits and -)")
    if not isinstance(value, dict):
        raise DeclarationError(pid, f"the declaration is a {type(value).__name__}, "
                                    "not a table")
    unknown = sorted(set(map(str, value)) - DECLARATION_KEYS)
    if unknown:
        raise DeclarationError(pid, f"unknown key {unknown[0]!r}; a declaration takes "
                                    f"{', '.join(sorted(DECLARATION_KEYS))}")
    event = value.get("event")
    if event not in EVENTS:
        raise DeclarationError(pid, f"event {event!r} is not a client-side git event "
                                    "the runner knows")
    target = value.get("target")
    if not isinstance(target, str) or not re.fullmatch(
            r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", target):
        raise DeclarationError(pid, f"target {target!r} is not package.module:function")
    fail = value.get("fail")
    if fail not in FAIL_POLICIES:
        raise DeclarationError(pid, f"fail {fail!r} is not \"closed\" or \"open\"")
    order = value.get("order", DEFAULT_ORDER)
    if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 99:
        raise DeclarationError(pid, f"order {order!r} is not a whole number from 0 to 99")
    doc = value.get("doc", "")
    if not isinstance(doc, str) or "\n" in doc:
        raise DeclarationError(pid, "doc is not one line of text")
    settings = value.get("settings", {})
    if not isinstance(settings, dict):
        raise DeclarationError(pid, "settings is not a table of name to \"one\" or "
                                    "\"multi\"")
    for key, kind in settings.items():
        reason = setting_name_problem(key)
        if reason:
            raise DeclarationError(pid, f"setting {key!r}: {reason}")
        if kind not in SETTING_KINDS:
            raise DeclarationError(pid, f"setting {key!r}: {kind!r} is not \"one\" or "
                                        "\"multi\"")
    return Piece(id=pid, module=module, name=name, event=event, target=target, fail=fail,
                 order=order, doc=doc, settings=settings, entry=entry_name)


def setting_name_problem(name: object) -> str | None:
    """Why `name` can't be a setting's name, or None when it can."""
    import re

    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        return ("a setting's name is lower-case letters, digits and -, starting with a "
                "letter, the way git hands a config name back")
    if name in REGISTRATION_KEYS:
        return f"{name!r} is a key of the registration itself; pick another name"
    return None


def extensions():
    """The entries of the group; a seam tests replace."""
    from moxi import modules

    return modules.extensions(GROUP)


def declared() -> tuple[dict[str, Piece], dict[str, str]]:
    """Every piece the installed modules declare: (good by id, refused by id with why).

    Loading a declaration imports its light module; one that can't be loaded is refused
    with the reason, as is one that fails `check`.
    """
    good: dict[str, Piece] = {}
    bad: dict[str, str] = {}
    for ext in extensions():
        pid = piece_id(ext.name)
        try:
            value = ext.load()
        except Exception as exc:  # noqa: BLE001 - ExtensionError, reported per piece
            bad[pid] = f"its declaration can't be loaded ({getattr(exc, 'reason', exc)})"
            continue
        try:
            piece = check(ext.name, value)
        except DeclarationError as exc:
            bad[pid] = exc.reason
            continue
        if pid in good:
            bad[pid] = f"two distributions declare {pid}"
            good.pop(pid)
            continue
        good[pid] = piece
    return good, bad


# -- settings -----------------------------------------------------------------------------

class SetError(Exception):
    """A `--set` that can't be applied; the message names the key."""


def plan_sets(pieces: list[Piece], sets: list[str]) -> dict[str, dict[str, list[str] | None]]:
    """What a call's `--set KEY=VALUE` arguments do to each named piece's settings.

    Returns piece id -> {declared name: values, or None to remove the key}. Every `--set`
    for one key together give its whole list; `--set KEY=` alone removes it; an empty
    value beside others for the same key is refused, naming the key. The key is matched
    ignoring case against the names the named pieces declare and stored in the declared
    spelling. A key no named piece declares, or two of them declare, is refused; so is a
    second value for a key declared "one".
    """
    grouped: dict[str, list[str]] = {}
    owner: dict[str, Piece] = {}
    for item in sets:
        key, eq, value = item.partition("=")
        if not eq or not key:
            raise SetError(f"--set {item!r}: write it KEY=VALUE (KEY= alone removes the key)")
        holders = [(p, name) for p in pieces for name in p.settings
                   if name.lower() == key.lower()]
        if not holders:
            named = ", ".join(p.id for p in pieces) or "no piece"
            raise SetError(f"--set {key}: {named} declares no setting named {key.lower()}")
        if len({p.id for p, _ in holders}) > 1:
            both = " and ".join(sorted({p.id for p, _ in holders}))
            raise SetError(f"--set {key}: both {both} declare it; add them in two calls")
        piece, name = holders[0]
        owner[name] = piece
        grouped.setdefault(name, []).append(value)
    out: dict[str, dict[str, list[str] | None]] = {}
    for name, values in grouped.items():
        piece = owner[name]
        if any(v == "" for v in values):
            if len(values) > 1:
                raise SetError(f"--set {name}=: an empty value removes {name} and goes "
                               "alone; it was given with other values in the same call")
            out.setdefault(piece.id, {})[name] = None
            continue
        if piece.settings[name] == "one" and len(values) > 1:
            raise SetError(f"--set {name}: {piece.id} takes one value for {name}, "
                           f"not {len(values)}")
        out.setdefault(piece.id, {})[name] = values
    return out


__all__ = ["GROUP", "Outcome", "HookContext", "PushUpdate", "Piece", "DeclarationError",
           "SetError", "check", "declared", "plan_sets", "setting_name_problem",
           "can_refuse"]
