"""Builders that write harness records in the shapes the harnesses write, for tests.

Other Moxi modules' tests use them too, so they ship in the wheel. They use the standard
library and the core's file writer, and import no test framework. Every builder takes the root it writes under
as an argument and reads no environment variable, so a test decides where its files go.
No line a builder writes is cut from a real record.

* `ClaudeTranscript`: a Claude Code session's transcript, its subagent files with their
  sidecars, and workflow agent files.
* `CodexRollout`: a Codex rollout, legacy versions, imported threads and spawned threads
  included, with `write_imports` for the imports file beside the sessions root and
  `spawn_meta` for a spawned thread's line 1.
* `OpenCodeDb`: an OpenCode database with the columns OpenCode writes, written in one
  transaction.
* `today_at`: a time for a record a test means to be today that stays on today's local
  date in the first minutes after midnight.
* `archive_tests` and `forget_archive_answers`: how sessions tests archive folders, held
  by a test for a block (never answering, a keep time, a callback as each test starts, a
  stand-in answer), and forgetting the answers this process kept.
"""

from .claude import ClaudeRequest, ClaudeTranscript, usage
from .codex import CodexRollout, rate_limits, spawn_meta, token_usage, write_imports
from .archives import NEVER_ANSWERS, archive_tests, forget_archive_answers
from .opencode import OpenCodeDb
from .today import today_at

__all__ = ["ClaudeTranscript", "ClaudeRequest", "usage"]
__all__ += ["CodexRollout", "token_usage", "rate_limits", "write_imports", "spawn_meta"]
__all__ += ["OpenCodeDb", "today_at"]
__all__ += ["archive_tests", "forget_archive_answers", "NEVER_ANSWERS"]
