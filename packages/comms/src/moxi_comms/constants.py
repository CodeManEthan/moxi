"""Tunable numbers the protocol leaves to the implementation.

They are collected in one module so each number lives in exactly one place.
"""

from __future__ import annotations

# --- send retry: retried, then loud ----------------------------------------------------
RETRY_ATTEMPTS = 5     # total attempts, then a loud failure at the verb
RETRY_MAX_SLEEP = 30.0  # cap on an honored Retry-After, per attempt

# --- agent cards ---------------------------------------------------------------------
# The LINE cap is the budget: 10 lines, because an unbudgeted document rots. The other
# three are tunable calibrations -- judgements, not facts about anything on the wire --
# and they exist because a line cap alone is evaded by one 40,000-character line, and
# because the point of the budget is that a peer can read a whole card at the moment it
# is about to message someone.
CARD_MAX_LINES = 10     # Every line counts; nothing is exempt.
CARD_MAX_CHARS = 800    # ten 80-column lines, the same order as glossary.WIDTH = 78
CARD_READ_LIMIT = 8192  # bytes: a card is never slurped, so no file can cost a peer its
                        # context. Past it the reader says "more than", never a count it
                        # cannot have made.
CARD_NATIVE_MAX = 64    # characters of a `native-session:` display claim

# --- the fast local stall signal -----------------------------------------------------
# The adapter's own detector, armed at the injection site and disarmed by the first sign
# that the injected turn started. What it cannot see (an adapter that died) is
# `moxi comms explain`'s to show, on demand. See `stall.py`.
#
# MEASURED, not copied. What it covers:
#
#   claude-shim   injection -> UserPromptSubmit, a real interactive Claude Code session
#                 woken by a real channel notification (0.04 s to 0.06 s measured).
#
# The margin is the point: a false alarm on a slow but healthy turn is the failure this
# number exists to avoid, and the cost of being generous is only that the near detector
# is a little less near. `StallTimer.last_latency` is what makes it re-measurable from
# an ordinary run when the harness changes.
INJECT_STALL_WINDOW = 15.0   # seconds from injection to any sign of life

# --- adapter reconnect backoff -------------------------------------------------------
BACKOFF_START = 0.5    # seconds
BACKOFF_FACTOR = 2.0
BACKOFF_MAX = 30.0
BACKOFF_JITTER = 0.2   # +/- fraction

# --- record rescan ------------------------------------------------------------------
RESCAN_LIMIT = 500     # max #names messages examined by a boot / non-RESUME rescan

# --- the idle boundary (claude-shim) -------------------------------------------------
# An idle session emits no Stop hook, so "turn boundaries only" has to include the
# boundary a session is ALREADY standing at. The shim polls its own queue while the
# session is known idle, and waits for the queue to stop growing before it flushes so
# that a burst still coalesces into one turn.
IDLE_POLL = 0.1        # seconds between idle checks
IDLE_SETTLE = 0.3      # queue must be unchanged this long before an idle flush

# --- cold sends ----------------------------------------------------------------------
# A cache's lifetime is cache's to say (its read gives `ttl_seconds`); comms keeps none.
# A binding younger than this with no session id reported yet has simply not had its
# first look or turn event. The `--next-turn` NOTE reads one older than this, still with
# no id, as a session launched before comms carried session ids.
SESSION_ID_GRACE = 60.0

# --- reply posture -------------------------------------------------------------------
# How long a single-reply ask binds its receivers: a day. The verb's predicate reads it
# against the verb's clock and the daemon's ask query against the daemon's; nothing runs
# on a timer.
ASK_TTL = 24 * 3600
# How many nominated asks the daemon's `query kind="asks"` answers with, newest first.
ASK_QUERY_LIMIT = 10

# --- open threads ------------------------------------------------------------------
# A thread is derived from the record each time the send verb checks a reply: walk
# `re:` up from the referent to the root. The walk stops after this many hops, and a
# longer chain leaves the root unknown, so the check does not run.
THREAD_WALK_LIMIT = 500
# A thread whose root states `[open]` closes once its newest message has been readable
# by the replier for this long with nothing after it. Read at the next reply, against
# the verb's clock; nothing runs on a timer.
THREAD_IDLE = 3600
# The reply cap of an open thread when its opener set none and config.toml has no
# `[threads] reply_cap` (the key's default, settings.toml, is this number).
DEFAULT_REPLY_CAP = 8

# --- reply enforcement ---------------------------------------------------------------
# How long a delivered message can owe a reply: a day, the bound a single-reply ask
# has. The shim's ledger expires a debt at it and the daemon's sent index keeps this
# window of the record; nothing runs on a timer.
OWED_TTL = ASK_TTL
# The Stop's `owed` query runs on a short connection of its own. A query that does not
# answer in time lets the Stop pass.
OWED_QUERY_TIMEOUT = 0.5
