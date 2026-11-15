"""The public flag of a repo, and the verbs that turn publishing on and off there.

The flag is `moxi-publishing.public = true` in the repo's own git config, beside the two
keys `enable` also writes: `moxi-publishing.github` (OWNER/REPO, for the GitHub verbs and
the tag gate) and `moxi-publishing.interpreter` (the Python the hook entries were written
with, which the doctor compares). Each piece reads the flag first; a piece registered in a
repo without it refuses.

`enable` writes the flag, then registers the three pieces through repo's API, each
piece's settings seeded from publishing's `defaults.*` keys and overridden by `--set`.
A seed is written only where the piece is first registered: enabling a repo again leaves
the settings it has, as repo does with its own seeds. Every `--set` for one key together
give that key's whole list, replacing the seed or what the repo had. `disable` removes the
pieces, then the flag.
"""

from __future__ import annotations

import os
import re

FLAG = "moxi-publishing.public"
GITHUB = "moxi-publishing.github"
INTERPRETER = "moxi-publishing.interpreter"
MESSAGE = "publishing/message"
CONTENT = "publishing/content"
PUSH = "publishing/push"
#: The three pieces, in the order `enable` registers them.
PIECES = (MESSAGE, CONTENT, PUSH)
#: The keys of a registration in git config that are not settings.
_REGISTRATION = frozenset({"event", "target", "fail", "order", "enabled", "recorded"})
_WILDCARDS = re.compile(r"[*?\[]")
#: A tag name the release scheme could read as `<distribution>-v<version>`, whatever the
#: workspace's members are, so no future member's tag slips past the check.
_RELEASE_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*-v[0-9]")
_HEADS = "refs/heads/"
_TAGS = "refs/tags/"


class Failure(Exception):
    """A verb that could not act. `code` is its exit code: 1 failed, 2 usage, 3 refused."""

    def __init__(self, text: str, code: int = 1):
        super().__init__(text)
        self.text = text
        self.code = code


# -- the repo ----------------------------------------------------------------------------

def resolve(path: str | os.PathLike) -> str:
    """The main work tree of the repo at `path`, which may be a project key where repo
    resolves one. Raises `Failure`."""
    from moxi_repo import gitconf, projects

    try:
        where = projects.resolve(os.fspath(path))
    except projects.Unavailable as exc:
        raise Failure(exc.text, exc.code) from None
    try:
        return str(gitconf.resolve(where).root)
    except gitconf.NotARepo as exc:
        raise Failure(f"no git work tree at {exc}", 2) from None


def _git(root: str, *args: str):
    from moxi_repo.api import git

    return git(root, *args)


def config_get(root: str, key: str) -> str | None:
    """The last value of `key` in the repo's git config, or None."""
    done = _git(root, "config", "--get", key)
    return done.stdout.strip() if done.returncode == 0 else None


def is_public(root: str) -> bool:
    done = _git(root, "config", "--type=bool", "--get", FLAG)
    return done.returncode == 0 and done.stdout.strip() == "true"


def registered(root: str) -> dict[str, dict]:
    """publishing's pieces registered in the repo: id -> {"enabled": bool,
    "settings": {name: [values]}}."""
    done = _git(root, "config", "--get-regexp", r"^moxi-repo\.piece\.publishing/")
    if done.returncode not in (0, 1):
        raise Failure(f"git config can't be read in {root}: {done.stderr.strip()}")
    out: dict[str, dict] = {}
    for line in done.stdout.splitlines():
        name, _, value = line.partition(" ")
        section, _, key = name[len("moxi-repo.piece."):].rpartition(".")
        piece = out.setdefault(section, {"enabled": True, "settings": {}})
        if key == "enabled":
            piece["enabled"] = value.strip().lower() not in ("false", "no", "off", "0")
        elif key not in _REGISTRATION:
            piece["settings"].setdefault(key, []).append(value)
    return out


# -- free-ref patterns -------------------------------------------------------------------

def free_ref_problem(pattern: str) -> str | None:
    """Why `pattern` can't be a `free-ref` value, or None. A pattern is a full ref name in
    which `*`, `?` and `[...]` are wildcards; one that could match a branch or a release
    tag is refused, since branches and release tags keep their own rules."""
    if not pattern.startswith("refs/"):
        return f"{pattern!r} is not a full ref name; it starts with refs/"
    wild = _WILDCARDS.search(pattern)
    prefix = pattern if wild is None else pattern[:wild.start()]
    if (prefix.startswith(_HEADS) if wild is None
            else prefix.startswith(_HEADS) or _HEADS.startswith(prefix)):
        return (f"{pattern!r} could match a branch (refs/heads/), and branches keep their "
                "own rules: no rewrite, deletion only once merged")
    if wild is None:
        tag = pattern[len(_TAGS):] if pattern.startswith(_TAGS) else None
        release = tag is not None and _RELEASE_SHAPE.match(tag) is not None
    elif prefix.startswith(_TAGS):
        release = "/" not in prefix[len(_TAGS):]
    else:
        release = _TAGS.startswith(prefix)
    if release:
        return (f"{pattern!r} could match a release tag (<package>-v<version>), and a "
                "release tag is never moved or deleted; name a ref outside them, such as "
                "refs/notes/* or refs/tags/docs/*")
    return None


# -- enable, disable ---------------------------------------------------------------------

def _github_from_origin(root: str) -> str | None:
    url = config_get(root, "remote.origin.url")
    if not url:
        return None
    match = re.match(r"(?:https?://(?:[^@/]+@)?github\.com/|(?:ssh://)?git@github\.com[:/])"
                     r"([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$", url)
    return match.group(1) if match else None


def _planned(sets) -> dict[str, list[str]]:
    """The call's `--set`s by lower-cased key, each with its whole list."""
    out: dict[str, list[str]] = {}
    for item in sets:
        key, eq, value = item.partition("=")
        if not eq or not key:
            raise Failure(f"--set {item!r}: write it KEY=VALUE (KEY= alone removes the key)",
                          2)
        out.setdefault(key.lower(), []).append(value)
    return out


def _check_sets(planned: dict[str, list[str]]) -> None:
    from moxi_publishing import push

    for value in planned.get(push.FREE_REF, []):
        if value == "":
            continue
        why = free_ref_problem(value)
        if why is not None:
            raise Failure(f"--set {push.FREE_REF}: {why}; nothing was written", 2)
    for value in planned.get(push.WORD_REQUIRED, []):
        if value not in ("", "true", "false"):
            raise Failure(f"--set {push.WORD_REQUIRED}: {value!r} is not true or false; "
                          "nothing was written", 2)


def _seeds() -> dict[str, dict[str, list[str]]]:
    """The settings each piece starts with where it is first registered."""
    from moxi import config

    from moxi_publishing import message, push, settings

    try:
        values = settings.load()
    except config.ConfigError as exc:
        raise Failure(f"publishing's config can't be read: {exc}") from None
    return {MESSAGE: {message.DROP_TRAILER: [str(k) for k in values[settings.DROP_TRAILERS]]},
            PUSH: {push.MAIN: [str(values[settings.MAIN])],
                   push.WORD_REQUIRED: ["true" if values[settings.WORD_REQUIRED]
                                        else "false"]}}


def _names_empty() -> str | None:
    """A warning when secrets' private names list is empty or can't be read, else None."""
    from moxi_secrets import SetUnavailable, private_names

    try:
        names = private_names()
    except SetUnavailable as exc:
        return f"warning: {exc}; the pieces refuse every commit until it loads"
    if not (names.words or names.anywhere or names.emails):
        return ("warning: secrets' private names list is empty, so no private name is "
                "refused; add yours with: moxi config secrets")
    return None


def enable(path: str | os.PathLike, *, github: str | None = None,
           sets: list[str] | tuple = ()) -> list[str]:
    """Mark the repo at `path` public and register the three pieces. Returns the lines to
    print. Raises `Failure`; a refused `--set` writes nothing."""
    from moxi_repo import api, gitconf

    root = resolve(path)
    planned = _planned(sets)
    _check_sets(planned)
    if github is not None and not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", github):
        raise Failure(f"--github {github!r}: write it OWNER/REPO; nothing was written", 2)
    seeds = _seeds()
    have = registered(root)
    given = list(sets)
    for piece in PIECES:
        if piece in have:
            continue
        for key, values in seeds.get(piece, {}).items():
            if key not in planned:
                given += [f"{key}={value}" for value in values]
    github = github or _github_from_origin(root)
    lines = []
    for key, value in ((FLAG, "true"), (INTERPRETER, gitconf.interpreter()),
                       *(((GITHUB, github),) if github else ())):
        done = _git(root, "config", key, value)
        if done.returncode != 0:
            raise Failure(f"git config {key} failed in {root}: {done.stderr.strip()}")
    lines.append(f"marked {root} public ({FLAG})")
    try:
        report = api.add(root, *PIECES, sets=given)
    except api.Failure as exc:
        raise Failure(exc.text, exc.code) from None
    lines += report.lines + report.problems
    if not github:
        lines.append(f"note: no GitHub repo is known; name it with: moxi publishing enable "
                     f"{root} --github OWNER/REPO")
    warning = _names_empty()
    if warning:
        lines.append(warning)
    return lines


def disable(path: str | os.PathLike) -> list[str]:
    """Remove the pieces from the repo at `path`, then the flag. Returns the lines to
    print. Raises `Failure`."""
    from moxi_repo import api

    root = resolve(path)
    lines = []
    present = [piece for piece in PIECES if piece in registered(root)]
    if present:
        try:
            report = api.remove(root, *present)
        except api.Failure as exc:
            raise Failure(exc.text, exc.code) from None
        lines += report.lines + report.problems
    done = _git(root, "config", "--unset-all", FLAG)
    if done.returncode not in (0, 5):
        raise Failure(f"git config --unset-all {FLAG} failed in {root}: "
                      f"{done.stderr.strip()}")
    lines.append(f"{root} is no longer marked public")
    return lines


# -- the listing -------------------------------------------------------------------------

def listing() -> list[dict]:
    """Every repo repo has indexed that carries the flag: {"root", "github",
    "interpreter", "pieces": {id: {"enabled", "settings"} or None when missing}}, sorted
    by root. A repo that can't be opened is left out."""
    from moxi_repo import api

    try:
        repos = api.indexed_repos()
    except api.Failure as exc:
        raise Failure(exc.text, exc.code) from None
    out = []
    for _key, _entry, repo, _why in repos:
        if repo is None:
            continue
        root = str(repo.root)
        if not is_public(root):
            continue
        have = registered(root)
        out.append({"root": root, "github": config_get(root, GITHUB),
                    "interpreter": config_get(root, INTERPRETER),
                    "pieces": {piece: have.get(piece) for piece in PIECES}})
    return sorted(out, key=lambda item: item["root"])


__all__ = ["FLAG", "GITHUB", "INTERPRETER", "PIECES", "Failure", "config_get", "disable",
           "enable", "free_ref_problem", "is_public", "listing", "registered", "resolve"]
