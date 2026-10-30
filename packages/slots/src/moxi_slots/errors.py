"""What a slots call raises when it does not do what was asked.

- `Refused(reason, words, holder=None)`: slots declined to act on what it found. `reason`
  is one machine word from `REASONS`; `words` says it for a person, with the command that
  fixes it; `holder` names who has the slot, for a taken one (set by the calls that claim
  slots).
- `TryAgain`: the module lock was not had in its wait, or the server is not answering
  yet. Trying again later may work; a command exits 75 on it.
- `TmuxError`: a tmux call failed, or tmux itself cannot be run. A command exits 1 on it.

Reads return typed results and never raise for "empty".
"""

from __future__ import annotations

#: Every reason a `Refused` may carry.
REASONS = ("occupied", "held", "missing", "in-use", "no-slot", "no-free-slot", "live-pane",
           "name-taken", "job-running", "unsupported")


class SlotsError(Exception):
    """The base of everything slots raises on purpose."""


class Refused(SlotsError):
    """slots declined to act: `reason` is the word, `words` the sentence with its fix."""

    def __init__(self, reason: str, words: str, holder: object = None):
        if reason not in REASONS:
            raise ValueError(f"a refusal's reason is one of {', '.join(REASONS)}, "
                             f"not {reason!r}")
        super().__init__(words)
        self.reason = reason
        self.words = words
        self.holder = holder


class TryAgain(SlotsError):
    """The lock was not had in its wait, or the server is not answering yet."""


class TmuxError(SlotsError):
    """A tmux call failed, or tmux cannot be run; the message says which and why."""


__all__ = ["REASONS", "SlotsError", "Refused", "TryAgain", "TmuxError"]
