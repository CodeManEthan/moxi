"""The stop mark: the latch a session's stop fires, and turns' guard that holds a stopped
session to wrap-up.

A launch with a stop mark stamps `MOXI_USAGE_STOP_AT`, the mark (a multiple `<k>x`,
`moxi_usage.thresholds.cost_multiple`), and `MOXI_USAGE_STOP_HANDOFFS`, the launch's
handoff folder. usage's warn-at listener (`moxi_usage.mark`) measures the main session
against the mark's threshold, and the first reading at or above it fires the stop: it
writes the latch, `STATE/usage/stop/<harness>-<session_id>.json`, and adds one line for
the model, the stop text. A fired stop never re-arms: a compaction that shrinks the context
leaves it latched, the endpoint's `gone` leaves the file in place, so a resume of the same
session stays stopped, and only the doctor's `marks` sweep removes it, after
`marks.max_age_days`.

**The guard** `usage.stop` covers every tool call of the main loop on Claude, and runs
only in a session whose launch stamped a stop mark (`on_for`), so it costs nothing on any
other. Its judge reads the latch: one `stat` per call until the file is there, then held
in memory. With no latch it allows. Latched, it allows only:

* what the allowlist (`moxi_usage.allowlist`) allows with the stamped handoff folder as
  its one hand-off root and no work root: git's wrap-up forms in the session's working
  directory, `Write`, `Edit` and bounded `Read` of `.md` files under the folder, and the
  commands `COMMANDS`, where `<handoff>` is a path under the folder by the hand-off rule;
* comms' MCP tools (server `comms`), and `moxi comms <verb> ...` on the shell for the
  message verbs `COMMS_VERBS`, written bare and passing the allowlist's lexer;
* the tools `WRAP_UP_TOOLS`: `Skill`, so a handoff skill can be loaded, and `ToolSearch`,
  so comms' MCP tools can be loaded where Claude Code holds them back. Neither acts on its
  own: a tool `ToolSearch` loads is judged by this guard when it is called. A skill acts
  on its own when it runs forked in a subagent (`context: fork` in its frontmatter), and
  a subagent's calls are not this guard's, so `Skill` is allowed only for a skill that
  runs inline (`skill_refusal`): one whose file is found where Claude Code finds skills
  and none of whose files forks. A skill with no file found (Claude Code's own bundled
  skills, a managed or `--add-dir` one) can't be told, and is refused.

Everything else is refused with `stop.deny_text`, its `{mark}` and `{folder}` filled in,
and the allowlist's reason in brackets. A subagent's calls are not the guard's: the stop
measures the main context, which only the main loop's calls grow.

**Fail open.** A latch that can't be read is no latch, and the guard's breaks let the call
run; each is logged once per kind.

Importing this module imports nothing beyond `_thread`, `os` and usage's `mark`; the core
and the allowlist load at the first call that needs them.
"""

from __future__ import annotations

import _thread
import os

from .mark import STOP_AT_ENV, STOP_HANDOFFS_ENV, safe_id

#: The guard's id and its name in usage's `GUARDS`.
GUARD_ID = "usage.stop"
NAME = "stop"
#: The setting that holds the refusal's words.
DENY_TEXT_KEY = "stop.deny_text"
#: The latches' directory under usage's state directory, and a latch's schema.
STATE_DIR = "stop"
SCHEMA = 1
#: The prefix of comms' MCP tools in a Claude session (`mcp__<server>__<tool>`).
COMMS_TOOL_PREFIX = "mcp__comms__"
#: The tools a stopped session may call: they load a skill or a tool's schema and act on
#: nothing themselves. `Skill` only for a skill that runs inline (`skill_refusal`).
SKILL_TOOL = "Skill"
WRAP_UP_TOOLS = (SKILL_TOOL, "ToolSearch")
#: A skill's frontmatter key, and its value that runs the skill forked in a subagent.
CONTEXT_KEY, FORK = "context", "fork"
#: The comms verbs a stopped session may run on the shell: its message verbs.
COMMS_VERBS = ("send", "reply", "agents", "whoami", "history", "remove")
#: The commands a stopped session may run, as the allowlist's word lists.
COMMANDS = (
    ("moxi", "launcher", "handoff", "dir"),
    ("moxi", "launcher", "relaunch", "--self", "--handoff", "<handoff>"),
    ("moxi", "repo", "rules"),
)
#: What the stop text names as the handoff folder when the launch stamped none.
NO_HANDOFF_DIR = "the folder `moxi launcher handoff dir` names"
#: The logger a problem goes to.
LOGGER = "moxi.usage"


# --- the latch --------------------------------------------------------------------------

def state_dir(env=None):
    """`STATE/usage/stop`."""
    from moxi.paths import Layout

    return Layout("usage").state_dir(env) / STATE_DIR


def latch_file(harness: str, session_id: str, env=None):
    """The session's latch, or None when the id can't name a file."""
    if harness not in ("claude", "codex", "opencode") or not safe_id(session_id):
        return None
    return state_dir(env) / f"{harness}-{session_id}.json"


def read_latch(path) -> dict | None:
    """A latch as a dict, or None when it is missing or not one."""
    import json

    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return None
    return doc


_latched: dict[tuple[str, str], dict] = {}
_lock = _thread.allocate_lock()


def latched(harness: str, session_id: str, env=None) -> dict | None:
    """The session's latch, or None when it has none: held in memory once found, else one
    `stat` of its file. A file that is there and can't be read is no latch (logged
    once)."""
    key = (harness, session_id)
    with _lock:
        found = _latched.get(key)
    if found is not None:
        return found
    path = latch_file(harness, session_id, env)
    if path is None:
        return None
    try:
        os.stat(path)
    except OSError:
        return None
    found = read_latch(path)
    if found is None:
        _warn("latch", f"the stop latch {path} can't be read; it counts as no latch")
        return None
    with _lock:
        _latched[key] = found
    return found


def latch(harness: str, session_id: str, *, mark: str, tokens: int, threshold: int,
          model: str | None, at: float, env=None) -> bool:
    """Write the session's latch unless a readable one is there; True only for the call
    that wrote it. The latch is held in memory either way."""
    path = latch_file(harness, session_id, env)
    if path is None:
        return False
    import json

    from moxi.files import update

    body = {"schema": SCHEMA, "harness": harness, "session_id": session_id, "mark": mark,
            "tokens": tokens, "threshold": threshold, "model": model, "fired_at": at}
    made: list[bool] = []

    def create(old: bytes | None) -> bytes:
        if old is not None:
            try:
                doc = json.loads(old)
            except ValueError:
                doc = None
            if isinstance(doc, dict) and doc.get("schema") == SCHEMA:
                body.clear()
                body.update(doc)
                return old
        made.append(True)
        return (json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8")

    update(path, create)
    with _lock:
        _latched[(harness, session_id)] = dict(body)
    return bool(made)


def sweep(*, now: float, max_age_days: float, env=None) -> tuple[int, list[str]]:
    """Delete the latches (and their lock files) last changed more than `max_age_days`
    days before `now`. Never raises: returns how many latches went and what could not be
    deleted."""
    root = state_dir(env)
    try:
        names = os.listdir(root)
    except FileNotFoundError:
        return 0, []
    except OSError as exc:
        return 0, [f"{root}: {exc}"]
    oldest = now - max_age_days * 86400.0
    swept, problems = 0, []
    for name in names:
        path = os.path.join(root, name)
        try:
            if os.lstat(path).st_mtime < oldest:
                os.remove(path)
                swept += name.endswith(".json")
        except FileNotFoundError:
            continue
        except OSError as exc:
            problems.append(f"{path}: {exc}")
    return swept, problems


def reset() -> None:
    """Forget the latches held in memory and the cached text (tests)."""
    with _lock:
        _latched.clear()
    _text_cache[:] = [None, None]
    _said.clear()


# --- the stop text ----------------------------------------------------------------------

_text_cache: list = [None, None]


def deny_text(env=None) -> str:
    """`stop.deny_text` now, read again only when usage's config file changes; the
    shipped default when the config can't be read (logged once)."""
    from moxi.paths import Layout

    path = Layout("usage").config_file(env)
    try:
        st = os.stat(path)
        key = (str(path), st.st_size, st.st_mtime_ns, st.st_ino)
    except OSError:
        key = (str(path), None)
    if _text_cache[0] != key:
        from .settings import current, schema

        try:
            text = current(env)[DENY_TEXT_KEY]
        except Exception:  # noqa: BLE001 - an unreadable config keeps the shipped words
            _warn("config", "usage's config could not be read; the stop uses its shipped "
                            "words", exc_info=True)
            text = schema().keys[DENY_TEXT_KEY].default
        _text_cache[:] = [key, text]
    return _text_cache[1]


def stop_text(session_env, env=None) -> str:
    """The stop text for a session with the stamps `session_env`: `stop.deny_text` with
    the mark and the handoff folder filled in."""
    return fill(deny_text(env), mark=session_env.get(STOP_AT_ENV) or "unknown",
                handoff_dir=session_env.get(STOP_HANDOFFS_ENV) or NO_HANDOFF_DIR)


def fill(text: str, *, mark: str, handoff_dir: str) -> str:
    """`text` with `{mark}` and `{folder}` (`handoff_dir`) filled in; text whose braces
    don't read as those is given as written."""
    try:
        return text.format_map({"mark": mark, "folder": handoff_dir})
    except (KeyError, ValueError, IndexError, AttributeError):
        return text


# --- the guard --------------------------------------------------------------------------

def on_for(env) -> bool:
    """turns' `sessions` function: whether the guard runs in a session started with
    `env`. True when the launch stamped a stop mark."""
    return bool(env.get(STOP_AT_ENV))


def judge(call):
    """turns' judge for `usage.stop`: None, or turns' `Deny(reason)`."""
    try:
        reason = decide(call)
    except Exception:  # noqa: BLE001 - fail open
        _warn("judge", "the stop could not judge a call; it was allowed", exc_info=True)
        return None
    if reason is None:
        return None
    from moxi import modules

    return modules.load("turns").api.Deny(reason)


def decide(call, *, env=None) -> str | None:
    """The refusal for `call`, or None to let it run, as `judge` gives it but letting
    errors out. `env` locates usage's state and config, the process's when None."""
    if getattr(call, "agent_id", None):
        return None
    harness, session_id = getattr(call, "harness", None), getattr(call, "session_id", None)
    if not isinstance(harness, str) or not isinstance(session_id, str):
        return None
    if latched(harness, session_id, env) is None:
        return None
    tool = getattr(call, "tool", None)
    from collections.abc import Mapping

    session_env = getattr(call, "env", None)
    session_env = session_env if isinstance(session_env, Mapping) else {}
    if tool == SKILL_TOOL:
        why = skill_refusal(getattr(call, "input", None), getattr(call, "cwd", None),
                            session_env)
        return None if why is None else f"{stop_text(session_env, env)} ({why})"
    if isinstance(tool, str) and (tool.startswith(COMMS_TOOL_PREFIX)
                                  or tool in WRAP_UP_TOOLS):
        return None
    handoff_dir = session_env.get(STOP_HANDOFFS_ENV) or ""
    home = session_env.get("HOME")
    tool_input = getattr(call, "input", None)
    from .allowlist import allowed

    ok, why = allowed(tool, tool_input, getattr(call, "cwd", None),
                      handoff_roots=(handoff_dir,) if os.path.isabs(handoff_dir) else (),
                      work_roots=(), commands=COMMANDS, home=home)
    if ok:
        return None
    if tool == "Bash" and isinstance(tool_input, Mapping) and comms_verb(
            tool_input.get("command"), home):
        return None
    text = stop_text(session_env, env)
    return f"{text} ({why})" if why else text


def comms_verb(command: object, home: str | None = None) -> bool:
    """Whether `command` is `moxi comms <verb> ...` for one of `COMMS_VERBS`, written bare
    (no `cd <dir> &&`) and passing the allowlist's lexer."""
    from .allowlist import AND, Refused, lex

    if not isinstance(command, str):
        return False
    if not isinstance(home, str) or not os.path.isabs(home):
        home = os.path.expanduser("~")
    try:
        words = lex(command, home)
    except Refused:
        return False
    if any(word is AND for word in words):
        return False
    return words[:2] == ["moxi", "comms"] and len(words) >= 3 and words[2] in COMMS_VERBS


# --- skills -----------------------------------------------------------------------------

#: One part of a skill's name (`handoff`, either part of `plugin:skill`).
_NAME_PART = r"[A-Za-z0-9][A-Za-z0-9._-]*"
#: How many folders, the working directory first, project skills are looked for in.
PROJECT_DEPTH = 64


def skill_refusal(tool_input, cwd, session_env) -> str | None:
    """Why a `Skill` call past the stop is refused, or None when its skill runs inline:
    a file of it is found (`skill_files`) and none sets `context: fork`."""
    import re
    from collections.abc import Mapping

    name = tool_input.get("skill") if isinstance(tool_input, Mapping) else None
    name = name.strip().removeprefix("/") if isinstance(name, str) else ""
    if not re.fullmatch(rf"{_NAME_PART}(:{_NAME_PART})*", name):
        return "Skill names no skill a file can be found for"
    files = skill_files(name, cwd, session_env)
    if not files:
        return (f"no file of skill {name} is found, so whether it runs in a subagent "
                "can't be told")
    for path in files:
        forks = skill_forks(path)
        if forks is None:
            return f"skill {name}'s file {path} can't be read"
        if forks:
            return f"skill {name} runs forked in a subagent"
    return None


def skill_files(name: str, cwd, session_env) -> list[str]:
    """The files Claude Code may load for the skill `name`, each one that is there.

    A bare name is looked for under Claude's folder (`CLAUDE_CONFIG_DIR`, else
    `~/.claude`, as `session_env` places it) and under `.claude` in `cwd` and each folder
    above it up to its repository's root: `skills/<name>/SKILL.md`, and the older
    `commands/<name>.md`. `<space>:<name>` is looked for in each installed copy of the
    plugin `<space>` (Claude's `plugins/installed_plugins.json`), and as
    `commands/<space>/<name>.md` in the same folders. Either form is looked for among the
    skills Claude syncs from the account (`skills/synced/<bucket>/<name>/SKILL.md`)."""
    from moxi.paths import claude_home

    *spaces, bare = name.split(":")
    try:
        claude_dir = str(claude_home(session_env))
    except LookupError:
        claude_dir = None
    roots = [claude_dir] if claude_dir else []
    roots += [os.path.join(d, ".claude") for d in _project_dirs(cwd)]
    if len(spaces) == 1 and claude_dir:
        roots += _plugin_dirs(claude_dir, spaces[0])
    found = []
    for root in roots:
        found.append(os.path.join(root, "skills", bare, "SKILL.md"))
        found.append(os.path.join(root, "commands", *spaces, f"{bare}.md"))
        if spaces:
            found.append(os.path.join(root, "commands", f"{bare}.md"))
    if claude_dir:
        synced = os.path.join(claude_dir, "skills", "synced")
        try:
            buckets = sorted(os.listdir(synced))
        except OSError:
            buckets = []
        found += [os.path.join(synced, b, bare, "SKILL.md") for b in buckets]
    return [path for path in dict.fromkeys(found) if os.path.isfile(path)]


def _project_dirs(cwd) -> list[str]:
    """`cwd` and each folder above it, up to the first that holds `.git` (its
    repository's root) or the top of the file system."""
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return []
    out, at = [], os.path.normpath(cwd)
    while len(out) < PROJECT_DEPTH:
        out.append(at)
        up = os.path.dirname(at)
        if os.path.exists(os.path.join(at, ".git")) or up == at:
            break
        at = up
    return out


def _plugin_dirs(claude_dir: str, plugin: str) -> list[str]:
    """The install folders of `plugin`, from any marketplace, that Claude's plugin list
    names; none when the list can't be read."""
    import json

    try:
        with open(os.path.join(claude_dir, "plugins", "installed_plugins.json"),
                  encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return []
    plugins = doc.get("plugins") if isinstance(doc, dict) else None
    out = []
    for key, copies in plugins.items() if isinstance(plugins, dict) else ():
        if str(key).split("@", 1)[0] != plugin or not isinstance(copies, list):
            continue
        out += [c["installPath"] for c in copies
                if isinstance(c, dict) and isinstance(c.get("installPath"), str)]
    return out


def skill_forks(path) -> bool | None:
    """Whether the skill file `path` runs forked in a subagent: its frontmatter (between
    a first line `---` and the next) sets `context: fork`. None when it can't be read."""
    try:
        with open(path, encoding="utf-8") as fh:
            head = fh.read(65536)
    except (OSError, UnicodeDecodeError):
        return None
    lines = head.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, colon, value = line.partition(":")
        if colon and key.strip() == CONTEXT_KEY:
            return value.split("#", 1)[0].strip().strip("'\"").lower() == FORK
    return False


# --- logging ----------------------------------------------------------------------------

_said: set[str] = set()


def _warn(kind: str, message: str, *, exc_info: bool = False) -> None:
    """Log `message` once per kind per process."""
    if kind in _said:
        return
    _said.add(kind)
    import logging

    logging.getLogger(LOGGER).warning(message, exc_info=exc_info)


__all__ = ["GUARD_ID", "NAME", "DENY_TEXT_KEY", "STATE_DIR", "SCHEMA", "COMMS_TOOL_PREFIX",
           "WRAP_UP_TOOLS", "SKILL_TOOL", "CONTEXT_KEY", "FORK", "PROJECT_DEPTH",
           "COMMS_VERBS", "COMMANDS", "NO_HANDOFF_DIR", "state_dir", "latch_file",
           "read_latch", "latched", "latch", "sweep", "reset", "deny_text", "stop_text",
           "fill", "on_for", "judge", "decide", "comms_verb", "skill_refusal",
           "skill_files", "skill_forks"]
