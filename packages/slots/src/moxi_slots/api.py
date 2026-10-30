"""slots' API: the module other code calls (the launcher above all).

A slot is a numbered tmux session on the module's own tmux server, which you attach to
once; sessions come and go in it and your attachment survives them. Every call reaches
the server through the socket under this process's `MOXI_HOME`, so a caller gets its own
server by setting `MOXI_HOME`; there is no socket argument.

What a call raises when it does not do what was asked:

- `Refused(reason, words, holder)`: declined, `reason` a word of `REASONS`;
- `TryAgain`: the module lock not had in 10 s, or the server not yet answering;
- `TmuxError`: a tmux call failed;
- `RegistryError`: the registry can't be read or written;
- `UsageError` (a `ValueError`): an argument is wrong.

All of them are `SlotsError`s.

Reads (`list_slots`, `show`, `job`, `jobs`) take no lock and return typed results;
`list_slots` never raises, and says when it could not read (`Listing.ok` False). Every
change runs under the module lock.

`start` and `respawn` place a launch in a slot and `run_job` runs a detached one: the
launch's environment goes through a private file the pane's trampoline reads, never
through tmux, and each returns once the command has started or has not (`exec`
`started`, `failed` or `unconfirmed`). `wait_ended` waits for a run pane to end.

`supported()` says whether slots runs here; a launcher that finds it false treats slots
as absent.
"""

from __future__ import annotations

from .errors import (REASONS, Refused, RegistryError, SlotsError, TmuxError, TryAgain,
                     UsageError)
from .jobs import job, jobs, run_job
from .launch import respawn, start, wait_ended
from .model import Drift, Ended, Hold, Job, Last, Listing, Placed, Removed, Slot
from .slots import attach_argv, create, hold, list_slots, release, remove, rename, show
from .support import Support, supported

__all__ = ["REASONS", "Refused", "TmuxError", "TryAgain", "RegistryError", "SlotsError",
           "UsageError", "Support", "supported", "Slot", "Hold", "Last", "Listing", "Drift",
           "Removed", "Placed", "Job", "Ended", "list_slots", "show", "create", "remove",
           "rename", "hold", "release", "attach_argv", "start", "respawn", "wait_ended",
           "run_job", "job", "jobs"]
