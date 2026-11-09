"""turns' question mode: `AskUserQuestion` runs only in a turn where the person at the
keyboard typed something that mentions a question.

The judge reads the turn's prompts the runner hands it (the guard `wants` them), each with
its origin as the harness stamped it. It allows the call when a prompt whose origin is in
`question_mode.origins` (by default `composer`, the person's Enter at this terminal)
contains "question", case-folded, anywhere, and that prompt is not the one the session was
launched with. A subagent's call is always refused: a subagent can't reach the person.
Every other call is refused, naming why. A prompt the runner can't tie to the turn is not
in the list, so a miss refuses (fails closed), and so does every break
(`on_error = "deny"`).

The launch prompt: a brief a launcher hands over arrives through turns' own delivery,
whose origin is `plugin`, so it never counts. A prompt given on the command line
(`claude "..."`) is stamped `composer`, so, only when a grant is in sight, the judge reads
the Claude Code process's arguments and refuses a prompt equal to a whole argument. The
judge runs in the session's endpoint, whose parent is that process.

The reminder: `on_prompt`, a `prompt` listener, adds one line to a prompt that asks to go
through or answer open questions, and not in prose, when question mode is on and the
prompt's origin is one the judge counts. So a message delivered by another module that
says "go through the open questions" gets no reminder, since the judge would refuse the
call it asks for. A prompt with no origin gets none either.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

from . import Deny, ToolCall

#: The guard's id.
GUARD_ID = "turns.question-mode"
#: The guard's switch in turns' settings.
SWITCH = "guards.question_mode"
#: turns' setting: the prompt origins that count as the person's own.
ORIGINS_KEY = "question_mode.origins"
#: The word a prompt must hold.
WORD = "question"

R_SUBAGENT = (
    "AskUserQuestion is refused: a subagent can't reach the person at the keyboard. Put the "
    "question in your report to your parent."
)
R_NOT_ASKED = (
    "AskUserQuestion is refused: nothing the person typed this turn mentions a question. "
    "Keep the question for later and carry on."
)
R_NOT_HERE = (
    "AskUserQuestion is refused: the prompt that mentions a question came in with origin "
    "{origin}, which question mode doesn't count as typed by the person at this terminal. "
    "Put the questions in prose instead."
)
R_LAUNCH = (
    "AskUserQuestion is refused: the prompt that mentions a question is the one this session "
    "was launched with, not something the person typed. Put the questions in prose instead."
)
#: What `on_prompt` adds.
REMINDER = (
    "The person at the keyboard asked to go through open questions. Put the questions open "
    "now to them with AskUserQuestion: up to four per call, as many calls as you need, each "
    "self-contained, with your recommendation first."
)

#: A word that asks for prose.
PROSE = frozenset(("prose",))
_WORD = re.compile(r"[^\W_]+")


# -- the judge ---------------------------------------------------------------------------

def mentions_question(text: object) -> bool:
    """The text, case-folded, holds "question" anywhere."""
    return isinstance(text, str) and WORD in text.casefold()


def harness_pid() -> int:
    """The Claude Code process: the endpoint's parent, which the endpoint takes as its
    harness and ends with."""
    return os.getppid()


def cmdline(pid: int) -> list[str]:
    """The process's arguments, program first. Raises when they can't be read."""
    import psutil

    return psutil.Process(pid).cmdline()


def launch_args() -> list[str]:
    """The Claude Code process's arguments after the program."""
    return list(cmdline(harness_pid()))[1:]


def is_launch_prompt(text: str, args: Iterable[str]) -> bool:
    """The text is the prompt the session was launched with: equal, with leading and
    trailing whitespace stripped, to a whole argument stripped the same way."""
    t = text.strip()
    return t != "" and t in {a.strip() for a in args if isinstance(a, str)}


def origins(env: Mapping[str, str] | None) -> frozenset[str]:
    """`question_mode.origins` from turns' config. Raises when the config doesn't load."""
    from .. import config

    value = config.load(env)[ORIGINS_KEY]
    return frozenset(o for o in value if isinstance(o, str))


def judge(call: ToolCall) -> Deny | None:
    """Question mode's judge for one `AskUserQuestion` call."""
    if call.agent_id:
        return Deny(R_SUBAGENT)
    counted = origins(call.env)
    asked = [p for p in call.prompts if p.origin in counted and mentions_question(p.text)]
    if asked:
        # Read only now, when a grant is in sight: a failed read never touches a call that
        # would be refused anyway.
        args = launch_args()
        if any(not is_launch_prompt(p.text, args) for p in asked):
            return None
        return Deny(R_LAUNCH)
    for p in call.prompts:
        if mentions_question(p.text):
            return Deny(R_NOT_HERE.format(origin=p.origin or "none"))
    return Deny(R_NOT_ASKED)


# -- the reminder ------------------------------------------------------------------------

def nudges(text: object) -> bool:
    """The prompt asks to go through or answer open questions, and not in prose: no leading
    `<` (a notice), "open question" or "open questions" as consecutive words, one of "go
    through", "going through" or "answer" as words anywhere, and no word "prose"."""
    if not isinstance(text, str) or text.lstrip().startswith("<"):
        return False
    words = _WORD.findall(text.casefold())
    pairs = set(zip(words, words[1:]))
    if ("open", "question") not in pairs and ("open", "questions") not in pairs:
        return False
    if ("go", "through") not in pairs and ("going", "through") not in pairs \
            and "answer" not in words:
        return False
    return PROSE.isdisjoint(words)


def is_on(settings) -> bool:
    """Question mode is on: its id is on `guards.run`, or its switch reads true.
    `settings` is turns' settings, read by key."""
    listed = settings["guards.run"] or ()
    return GUARD_ID in listed or settings[SWITCH] is True


def on_prompt(event) -> str | None:
    """turns' `prompt` listener: the reminder, or None."""
    if not nudges(getattr(event, "text", None)):
        return None
    from .. import config

    settings = config.load(getattr(event, "env", None))
    if not is_on(settings):
        return None
    if event.origin not in frozenset(o for o in settings[ORIGINS_KEY] if isinstance(o, str)):
        return None
    return REMINDER


__all__ = ["GUARD_ID", "SWITCH", "ORIGINS_KEY", "R_SUBAGENT", "R_NOT_ASKED", "R_NOT_HERE",
           "R_LAUNCH", "REMINDER", "mentions_question", "harness_pid", "cmdline",
           "launch_args", "is_launch_prompt", "origins", "judge", "nudges", "is_on",
           "on_prompt"]
