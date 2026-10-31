"""comms.db: comms' one store, `STATE/comms/comms.db`.

It holds the record (rooms, messages), the delivery books (queue, digests, cursors,
dropped), the binding history (bindings), the directory (identities, operators), the
reserved send-only names, and `meta`. `schema.py` has the tables.

Who writes what:

  * the daemon is the only message writer: `messages`, `queue`, `digests`, `cursors`,
    `bindings`, `dropped` and `meta.next_id`;
  * provisioning writes `identities`, `operators` and `rooms`;
  * the reserved-names listener writes `reserved`, with the `bindings` row of kind
    `reserved` that goes with each reservation;
  * the move of comms 0.2's files writes every table, once.

Every writer opens the store through `open_write` and writes through
`moxi.store.transaction`; nothing else in comms opens it for writing. A reader that must
not write uses `open_read`.
"""

from __future__ import annotations

from .open import (
    ABSENT,
    MARKED,
    MIGRATE_LOCK,
    UNMARKED,
    StoreRefused,
    Unmoved,
    open_read,
    open_write,
    path,
    state_of,
    zero_two_files,
)
from .schema import ID_BASE, META_MIGRATED_FROM, META_NEXT_ID, STEPS, TABLES

__all__ = ["ABSENT", "MARKED", "MIGRATE_LOCK", "UNMARKED", "StoreRefused", "Unmoved",
           "open_read", "open_write", "path", "state_of", "zero_two_files", "ID_BASE",
           "META_MIGRATED_FROM", "META_NEXT_ID", "STEPS", "TABLES"]
