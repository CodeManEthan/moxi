"""statusline's setup and teardown, and the verb `use` that shares their questions.

`setup()` is the announcement's `SETUP`, run after each install:

1. Read Claude Code's user `settings.json`. A file that isn't a plain JSON object: say
   why, change nothing, exit 1.
2. Its `statusLine` is Moxi's line: when it runs another environment's render, rewrite
   the path to this one's. Done either way.
3. An answer is recorded in `asked.json`: say that the line is left as it is and that
   `moxi statusline use` switches. Done. Setup asks once; after that only `use` asks.
4. No `statusLine`: ask "Use Moxi's status line in Claude Code? [Y/n]". Yes switches,
   with the default items; no says how to switch later.
5. Another command: `offer(settings, current)`.

Steps 4 and 5 record the answer (`switch`, `keep` or `no`) with the time. With nobody at
the keyboard nothing is asked and nothing recorded, so the next install with a keyboard
asks; step 4 says how to switch, and `offer` says what it would offer. An install never
changes the user's own command: it replaces the `statusLine` value whole, or leaves it.

`use()` is `moxi statusline use`: over Moxi's line it rewrites the path; with no line it
asks step 4's question again (whatever `asked.json` holds) and records the answer, and
with nobody at the keyboard the command itself is the yes; over another command it calls
`offer`.

`teardown()` is the announcement's `TEARDOWN`: over Moxi's line with a saved one, it
puts the saved line back; over Moxi's line with none saved, it removes the setting; over
the user's own line it changes nothing and says that any pieces of statusline that line
calls stop working with the uninstall.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping

from moxi import exit as codes
from moxi import files, harnessfile, paths

from . import switch

MODULE = "statusline"
#: Who the config's history names for a write made by setup.
BY = "moxi install"
#: Who it names for a write made by the verb.
BY_USE = "moxi statusline use"
QUESTION = "Use Moxi's status line in Claude Code? [Y/n] "
#: Setup's line once an answer is recorded.
KEPT = ("Claude Code's status line is left as it is; moxi statusline use switches to "
        "Moxi's.")
#: Step 4's line when the answer is no, or nobody is at the keyboard to give one.
NOT_NOW = "Claude Code has no status line; moxi statusline use sets Moxi's."
#: How many answers that are neither yes nor no are asked again.
TRIES = 3
ANSWERS = ("switch", "keep", "no")

Say = Callable[[str], object]
Ask = Callable[[str], str]


# -- the record of answers ---------------------------------------------------------------

def asked_path(env: Mapping[str, str] | None = None) -> str:
    """The `asked` store: setup's recorded answer."""
    return str(paths.Layout(MODULE).state_dir(env) / "asked.json")


def recorded(env: Mapping[str, str] | None = None) -> dict | None:
    """The recorded answer, `{"answer": ..., "at": ...}`, or None when none is recorded
    (or the record can't be read, which asks again)."""
    try:
        with open(asked_path(env), "rb") as handle:
            data = json.loads(handle.read())
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("answer") in ANSWERS:
        return data
    return None


def record(answer: str, env: Mapping[str, str] | None = None) -> None:
    """Record `answer` (`switch`, `keep` or `no`) with the time."""
    from .clock import stamp

    if answer not in ANSWERS:
        raise ValueError(f"an answer is one of {', '.join(ANSWERS)}, not {answer!r}")
    path = asked_path(env)
    files.private_dir(paths.Layout(MODULE).state_dir(env))
    files.atomic_write(path, json.dumps({"answer": answer, "at": stamp()}) + "\n",
                       mode=0o600)


# -- asking -----------------------------------------------------------------------------

def keyboard(ask: Ask | None = None) -> bool:
    """Whether someone can answer: an `ask` was given, or stdin is a terminal."""
    if ask is not None:
        return True
    stdin = sys.stdin
    return stdin is not None and stdin.isatty()


def yes_no(question: str, ask: Ask | None = None, say: Say = print, *,
           default: bool = True) -> bool | None:
    """Ask `question` until the answer is yes or no (an empty one is `default`), at most
    `TRIES` times. None when no answer came: the input ended, or no answer was yes or
    no."""
    ask = input if ask is None else ask
    prompt = question
    for _ in range(TRIES):
        try:
            text = ask(prompt).strip().lower()
        except EOFError:
            say("")
            return None
        if not text:
            return default
        if text in ("y", "yes"):
            return True
        if text in ("n", "no"):
            return False
        prompt = f"{text!r} is neither yes nor no; {question}"
    return None


def default_show() -> list[str]:
    """The config's default items."""
    from .settings import schema

    return list(schema().keys["show"].default)


# -- the steps --------------------------------------------------------------------------

def _tell(act: switch.Act, say: Say) -> None:
    for line in act.lines:
        say(line)


def _switch(show, *, by: str, say: Say, env) -> int:
    act = switch.switch(show, by=by, env=env)
    _tell(act, say)
    if act.ok or act.kind == "config":
        # The line switched; only its items failed. The answer stands.
        record("switch", env)
    return act.code


def _no_line(*, ask: Ask | None, say: Say, by: str, env, verb: bool) -> int:
    """Step 4, for setup (`verb` false) and for `use`."""
    if not keyboard(ask):
        if verb:
            return _switch(default_show(), by=by, say=say, env=env)
        say(NOT_NOW)
        return codes.OK
    answer = yes_no(QUESTION, ask, say)
    if answer is None:
        say(NOT_NOW)
        return codes.OK
    if not answer:
        record("no", env)
        say(f"{switch.LEFT}." if verb else NOT_NOW)
        return codes.OK
    return _switch(default_show(), by=by, say=say, env=env)


def offer(settings: harnessfile.Doc, current, *, ask: Ask | None = None,
          say: Say = print, by: str = BY,
          env: Mapping[str, str] | None = None) -> int:
    """Step 5: Claude Code's `statusLine` is `current`, another command, in `settings`
    (the file as setup read it). Returns the exit code.

    For now it keeps the user's line, writes nothing and records nothing, and says that
    `moxi statusline use` switches. Running the old line to see what it shows, and the
    choice between switching and keeping it, come here."""
    command = switch.command_of(current)
    runs = f" ({command})" if command else ""
    say(f"Claude Code's status line is your own{runs}; it is left as it is. moxi "
        f"statusline use switches to Moxi's.")
    return codes.OK


def setup(*, say: Say = print, ask: Ask | None = None,
          env: Mapping[str, str] | None = None) -> int:
    """The announcement's `SETUP`. See the module's docs. `ask` reads one answer (the
    terminal's `input` when None, and only with a terminal on stdin)."""
    try:
        doc = switch.read(env)
    except harnessfile.Refused as exc:
        say(str(exc))
        say(f"{switch.LEFT}.")
        return codes.ERROR
    current = doc.data.get(switch.KEY)
    if switch.is_moxis(current):
        act = switch.switch(None, by=BY, env=env)
        _tell(act, say)
        return codes.OK if act.ok else codes.ERROR
    if recorded(env) is not None:
        say(KEPT)
        return codes.OK
    if current is None:
        code = _no_line(ask=ask, say=say, by=BY, env=env, verb=False)
    else:
        code = offer(doc, current, ask=ask, say=say, by=BY, env=env)
    return codes.OK if code == codes.OK else codes.ERROR


def use(*, say: Say = print, ask: Ask | None = None,
        env: Mapping[str, str] | None = None) -> int:
    """`moxi statusline use`. See the module's docs. Returns the exit code."""
    try:
        doc = switch.read(env)
    except harnessfile.Refused as exc:
        say(str(exc))
        say(f"{switch.LEFT}.")
        return codes.REFUSED
    current = doc.data.get(switch.KEY)
    if switch.is_moxis(current):
        act = switch.switch(None, by=BY_USE, env=env)
        _tell(act, say)
        return act.code
    if current is None:
        return _no_line(ask=ask, say=say, by=BY_USE, env=env, verb=True)
    return offer(doc, current, ask=ask, say=say, by=BY_USE, env=env)


def teardown(*, say: Say = print, env: Mapping[str, str] | None = None) -> int:
    """The announcement's `TEARDOWN`. See the module's docs. A file it can't read stops
    the uninstall, so Claude Code's line is never left running a removed command."""
    try:
        doc = switch.read(env)
    except harnessfile.Refused as exc:
        say(str(exc))
        say("So statusline can't tell whether Claude Code's status line is Moxi's, and "
            "the uninstall stops here; fix the file, or uninstall with --skip-teardown")
        return codes.ERROR
    current = doc.data.get(switch.KEY)
    if not switch.is_moxis(current):
        if current is None:
            say("Claude Code has no status line, so there is nothing to put back")
        else:
            command = switch.command_of(current)
            runs = f" ({command})" if command else ""
            say(f"Claude Code's status line is your own{runs}, left as it is; any "
                f"{switch.SCRIPT} --only pieces it calls stop working once statusline "
                f"is uninstalled")
        return codes.OK
    try:
        kept = switch.saved(env)
    except switch.SavedError as exc:
        say(f"{exc}; the old bytes of {switch.settings_path(env)} are kept in "
            f"{switch.backups_dir(env)}")
        kept = None
    act = switch.restore(env=env) if kept is not None else switch.remove(env=env)
    _tell(act, say)
    return codes.OK if act.ok else codes.ERROR


__all__ = ["setup", "use", "teardown", "offer", "keyboard", "yes_no", "recorded",
           "record", "asked_path", "default_show", "QUESTION", "KEPT", "NOT_NOW"]
