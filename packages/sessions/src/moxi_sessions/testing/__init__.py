"""Builders that write harness records in the shapes the harnesses write, for tests.

Other Moxi modules' tests use them too, so they ship in the wheel. They use the standard
library only and import no test framework. Every builder takes the root it writes under
as an argument and reads no environment variable, so a test decides where its files go.
No line a builder writes is cut from a real record.

* `ClaudeTranscript`: a Claude Code session's transcript, its subagent files with their
  sidecars, and workflow agent files.
"""

from .claude import ClaudeRequest, ClaudeTranscript, usage

__all__ = ["ClaudeTranscript", "ClaudeRequest", "usage"]
