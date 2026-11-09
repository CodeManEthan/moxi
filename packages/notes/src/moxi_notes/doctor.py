"""notes' doctor checks: `rows()`, which the announcement's `DOCTOR` names.

Repo's own rows already report, for the saved spec `notes/snapshot`, the last commit
against its interval, commits not pushed, paths left out for over an hour and files no
spec covers, and its records carry every failed pass and push. These rows don't repeat
them:

- root: the notes folder, and that it is a git work tree's top. `fail` when it is
  missing after notes' setup has run here; `warn` when it is missing and the core records
  that notes' setup has never run in this home (a plain wheel install, say), since setup
  is what makes it; `warn` when it is no git repo, or inside one but not its top:
  snapshots and the gates are off until it is one.
- snapshot: a trigger is on (`snapshot.session_end` or the timer) and the saved spec equals the
  one notes' config gives. `off` when both triggers are off; `warn` when the saved spec
  differs or none is saved.
- gates: both git hook pieces registered and enabled in the notes repo; `fail` otherwise.
- filter: under `redact`, the clean filter's driver command, its attribute line and the
  interpreter it names are there, and `required` is not true. `fail` when one is missing;
  `warn` for `required = true`, for a driver written for another interpreter, and for a
  driver or attribute left behind under `leave-out`.
- allowlist: both allowlist files parse, each read where secrets accepts it (the public
  `.secrets-allow` at the root, the private `secrets-allow-private` in the common git
  dir). `fail` when either can't be read: until it is fixed no allowance applies.
  `warn` for a `.secrets-allow-private` at the root, which secrets never reads.
- topics: with projects installed, every `topic` row's folder exists and holds no `.git`;
  `off` without projects.

Each fix that needs setup again names `moxi notes setup`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator

from moxi.doctor import Row

MODULE = "notes"
SETUP = "moxi notes setup"
_QUOTED = re.compile(r"^'((?:[^']|'\\'')*)'")


def rows() -> Iterator[Row]:
    """Every check's row."""
    from moxi.config import ConfigError

    from . import settings
    from .setup import where

    try:
        places = settings.read()
    except ConfigError as exc:
        yield Row(MODULE, "root", "fail", f"notes' config can't be read: {exc}",
                  "moxi config notes")
        return
    found = where(places.root)
    yield _root(found)
    if found.is_top:
        yield _snapshot(places)
        yield _gates(found)
        yield _filter(found, places.settings["action"])
        yield _allowlist(found)
    else:
        why = "the notes root is no git repo's top"
        for name in ("snapshot", "gates", "filter", "allowlist"):
            yield Row(MODULE, name, "off", why, SETUP)
    yield _topics()


def _root(found) -> Row:
    if not found.exists:
        import moxi.modules

        if not moxi.modules.setup_ran(MODULE):
            return Row(MODULE, "root", "warn", f"notes' setup has not run here, so the "
                       f"notes root {found.root} is not made yet", "moxi install notes")
        return Row(MODULE, "root", "fail", f"the notes root {found.root} is missing",
                   f"make it, or set another with moxi config set notes root PATH; then "
                   f"{SETUP}")
    if not found.in_repo:
        return Row(MODULE, "root", "warn", f"{found.root} is no git repo, so snapshots "
                   "and the gates are off", SETUP)
    if not found.is_top:
        inside = found.top or found.common_dir
        return Row(MODULE, "root", "warn", f"{found.root} is inside the git repo "
                   f"{inside}, not its top, so snapshots and the gates are off",
                   f"set root to that top, or make the notes a repo of their own; then "
                   f"{SETUP}")
    return Row(MODULE, "root", "ok", f"{found.root}, a git work tree")


def _snapshot(places) -> Row:
    from moxi_repo.api import SpecError, saved_spec

    from . import spec

    s = places.settings
    auto, timer = s["snapshot.session_end"], s["snapshot.timer"]
    if not auto and not timer:
        return Row(MODULE, "snapshot", "off", "snapshot.session_end and snapshot.timer are both "
                   "off, so only moxi notes snapshot commits the notes",
                   "moxi config set notes snapshot.session_end true")
    try:
        saved = saved_spec(places.root, spec.NAME)
    except SpecError as exc:
        return Row(MODULE, "snapshot", "warn", f"the saved spec can't be read: {exc}",
                   SETUP)
    if saved is None:
        return Row(MODULE, "snapshot", "warn", f"no spec {spec.NAME} is saved in "
                   f"{places.root} yet, so moxi repo commit can't run it", SETUP)
    if not spec.same(saved, spec.build(places)):
        return Row(MODULE, "snapshot", "warn", f"the saved spec {spec.NAME} differs from "
                   "notes' config; the next snapshot notes starts saves it", SETUP)
    on = " and ".join(x for x, y in (("after agent turns", auto), ("on the timer", timer))
                      if y)
    push = s["snapshot.push"]
    return Row(MODULE, "snapshot", "ok", f"{on}, at most every {s['snapshot.interval_minutes']} "
               f"min, " + (f"pushed to {push}" if push else "not pushed")
               + "; the saved spec equals the config")


def _gates(found) -> Row:
    from moxi_repo import api

    from .setup import PIECES

    try:
        listed = api.status(found.root)
    except api.Failure as exc:
        return Row(MODULE, "gates", "fail", f"the git hook pieces can't be read: "
                   f"{exc.text}", SETUP)
    have = {p["id"]: p["enabled"] for p in (listed[0]["pieces"] if listed else [])}
    wrong = []
    for pid in PIECES:
        if pid not in have:
            wrong.append(f"{pid} is not registered")
        elif not have[pid]:
            wrong.append(f"{pid} is disabled")
    if wrong:
        return Row(MODULE, "gates", "fail", "; ".join(wrong) + f" in {found.root}, so a "
                   "secret can reach a commit or a push", SETUP)
    return Row(MODULE, "gates", "ok", f"{' and '.join(PIECES)} registered and enabled")


def _driver(found) -> dict[str, str]:
    from moxi_repo.api import git

    from . import filter as clean_filter

    done = git(found.root, "config", "-z", "--get-regexp",
               rf"^filter\.{re.escape(clean_filter.DRIVER)}\.")
    out = {}
    for item in (done.stdout or "").split("\0"):
        key, _, value = item.partition("\n")
        if key:
            out[key.rsplit(".", 1)[-1].lower()] = value
    return out


def _has_attribute(found) -> bool:
    from . import filter as clean_filter

    path = os.path.join(found.common_dir, "info", "attributes")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return clean_filter.ATTRIBUTE in fh.read().splitlines()
    except OSError:
        return False


def _filter(found, action: str) -> Row:
    from . import filter as clean_filter

    driver = _driver(found)
    process = driver.get("process")
    attribute = _has_attribute(found)
    if action != "redact":
        left = [what for what, there in (("the driver", process is not None),
                                         ("the attribute line", attribute)) if there]
        if left:
            return Row(MODULE, "filter", "warn", f"{' and '.join(left)} of the clean filter "
                       "are left from redact; under leave-out the filter redacts nothing",
                       SETUP)
        return Row(MODULE, "filter", "ok", "leave-out: no clean filter")
    missing = []
    if process is None:
        missing.append("the driver command")
    if not attribute:
        missing.append(f"the attribute line in {found.common_dir}/info/attributes")
    if missing:
        return Row(MODULE, "filter", "fail", f"action is redact, but {' and '.join(missing)} "
                   "is missing, so nothing is redacted and every file with a hit is left "
                   "out", SETUP)
    quoted = _QUOTED.match(process)
    python = quoted.group(1).replace("'\\''", "'") if quoted else None
    if python is None or not os.path.exists(python):
        return Row(MODULE, "filter", "fail", f"the clean filter's interpreter "
                   f"{python or process} is missing, so nothing is redacted", SETUP)
    if driver.get("required", "").strip().lower() in ("true", "yes", "on", "1"):
        return Row(MODULE, "filter", "warn", "the clean filter is required: one file it "
                   "fails on stops every snapshot and breaks git status", SETUP)
    if process != clean_filter.process_command():
        return Row(MODULE, "filter", "warn", "the clean filter's driver was written for "
                   "another interpreter", SETUP)
    return Row(MODULE, "filter", "ok", "redact: the driver, its attribute and its "
               "interpreter are in place, and it is not required")


def _allowlist(found) -> Row:
    from . import judge
    from .setup import private_allowlist

    allow, problem = judge.allowlist(found.root, found.common_dir)
    stray = os.path.join(found.root, ".secrets-allow-private")
    stray_text = (f"{stray} is never read, since secrets reads the private allowlist only "
                  f"from {private_allowlist(found.common_dir)}, and a snapshot would commit "
                  "it") if os.path.lexists(stray) else None
    if problem:
        detail = (f"{problem}: until it is fixed no allowance applies, so every file with a "
                  "hit is left out and every push with one is refused")
        if not judge.private_place_ok(found.common_dir):
            detail += (f". The notes repo's git dir {found.common_dir} is not a folder "
                       "named .git, so secrets reads no private allowlist for it")
        if stray_text:
            detail += f". Also, {stray_text}"
        return Row(MODULE, "allowlist", "fail", detail)
    if stray_text:
        return Row(MODULE, "allowlist", "warn", stray_text,
                   f"move its lines to {private_allowlist(found.common_dir)}")
    return Row(MODULE, "allowlist", "ok", f"{len(allow)} allowance lines read from "
               f".secrets-allow and {private_allowlist(found.common_dir)}")


def _topics() -> Row:
    import moxi.modules

    if not moxi.modules.available("projects"):
        return Row(MODULE, "topics", "off", "projects is not installed",
                   "moxi install projects")
    loaded = moxi.modules.load("projects", interface=1)
    if not loaded.ok:
        return Row(MODULE, "topics", "warn", "projects can't be loaded, so topics can't "
                   "be checked")
    try:
        topics = loaded.api.list_projects("topic")
    except loaded.api.Unreadable as exc:
        return Row(MODULE, "topics", "warn", f"projects can't be read: {exc}")
    missing = [t.key for t in topics if not os.path.isdir(t.root)]
    repos = [t.key for t in topics
             if os.path.isdir(t.root) and os.path.lexists(os.path.join(t.root, ".git"))]
    if missing:
        return Row(MODULE, "topics", "warn", f"topic folders missing: {', '.join(missing)}",
                   "make each folder, or moxi projects remove KEY then moxi projects add KEY "
                   "--kind topic --create")
    if repos:
        return Row(MODULE, "topics", "warn", f"topic folders holding a .git, whose files no "
                   f"snapshot takes: {', '.join(repos)}",
                   "move the notes out of that repo, or remove its .git")
    if not topics:
        return Row(MODULE, "topics", "ok", "no topic rows")
    noun = "topic" if len(topics) == 1 else "topics"
    return Row(MODULE, "topics", "ok", f"{len(topics)} {noun}, each a plain folder")


__all__ = ["rows"]
