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

Reads (`list_slots`, `show`) take no lock and return typed results; `list_slots` never
raises, and says when it could not read (`Listing.ok` False). Every change runs under
the module lock.

`supported()` says whether slots runs here; a launcher that finds it false treats slots
as absent.
"""

from __future__ import annotations

from .errors import (REASONS, Refused, RegistryError, SlotsError, TmuxError, TryAgain,
                     UsageError)
from .model import Drift, Hold, Last, Listing, Removed, Slot
from .slots import attach_argv, create, hold, list_slots, release, remove, rename, show
from .support import Support, supported

__all__ = ["REASONS", "Refused", "TmuxError", "TryAgain", "RegistryError", "SlotsError",
           "UsageError", "Support", "supported", "Slot", "Hold", "Last", "Listing", "Drift",
           "Removed", "list_slots", "show", "create", "remove", "rename", "hold",
           "release", "attach_argv"]
