"""Scoped senders: send-only names comms forms from one parent comms name and a word a
module declares, which only that module's code sends under, to that parent alone.

A module declares a word by an entry point in the group `moxi.comms.scoped_senders`,
named by the word, whose target is a one-line description in plain stdlib data, so a
declaration imports nothing of comms:

    [project.entry-points."moxi.comms.scoped_senders"]
    digest = "some_package.words:DIGEST"

    # some_package/words.py
    DIGEST = "the nightly digest a session gets from its own reader"

The module that owns the word is the one whose distribution carries the entry point
(`moxi-some` declares for `some`). A word is lower-case letters and digits, with no dash,
so a formed name reads back to one parent and one word.

**The name.** For the parent `builder@acme` and the word `digest`, the scoped sender is
`builder-digest@acme`: the parent's name, a dash and the word, in the parent's scope.
comms forms it (`form`); a module never builds the string itself.

**The reconcile.** comms reads the group where it reads a module's send-only names, in
`reserved.reconcile`: when a module installs or is uninstalled, at the daemon's start and
in comms' doctor, a reconcile with a module gone leaving that module's words out. A word
one module declares is `active`, and comms mints that module's token for it under the
secret id `comms/scoped/<word>`, held by the module as a reserved name's token is. A word
two modules declare is in `conflict` for both, and no token sends under it. A word no
module declares any more is freed and its token removed. A declaration that doesn't hold
(a bad word, a description that isn't one line of text, a distribution that is no
installed Moxi module) is left out with its reason; one that doesn't load keeps the word
as it was, as a broken module keeps its reserved names.

**Who holds a name of the form.** Once a word is declared, no session binds and no
program registers a name of its form. A name of the form held before (by a live
session's binding, or as a send-only name with its open interval) stays its holder's, and
that one parent's scoped sender is in conflict: a send under it is refused
(`scoped-conflict`) and comms' doctor reports it. A scoped sender is **active** while its
word is declared by one module and its name is held by nothing, and only an active one
reads back as a scoped sender (`sender_of`).

**The send.** Only the owning module's token sends under it, from a process outside every
session (the daemon walks the sender's ancestry as for any send-only name), to its parent
alone: a send to anyone else, or to the parent and another, is refused before anything is
posted (`scoped-recipient`). Nobody mails it. Its messages carry `[no reply]`, and are
delivered as any message is.

**Read-back.** `sender_of(conn, name)` and `state(conn, module, parent)` read comms.db
alone, never the daemon, so a listener can ask at each prompt.
"""

from __future__ import annotations

import logging
import re
import secrets as _secrets
import sqlite3
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .bindings import stamp
from .scoping import GLOBAL, NAME_RE, qualify, split_tag

log = logging.getLogger("comms.scoped")

#: The entry-point group a module declares a word in.
GROUP = "moxi.comms.scoped_senders"

#: A word: lower-case letters and digits, starting with a letter, with no dash.
WORD_RE = re.compile(r"^[a-z][a-z0-9]*$")
#: The longest description comms keeps, in characters.
DESCRIPTION_MAX_CHARS = 200

ACTIVE, CONFLICT = "active", "conflict"

#: The secret id a word's token rests under, held by the owning module.
SECRET_PREFIX = "comms/scoped/"

#: Why a module's scoped sender for a parent can't send (`state`): no module declares
#: the word for it, the word or the name is in conflict, or comms.db can't be read.
UNDECLARED, IN_CONFLICT, UNREADABLE = "undeclared", "conflict", "unreadable"
REASONS = (UNDECLARED, IN_CONFLICT, UNREADABLE)


def secret_id(word: str) -> str:
    """The credential chain's id for the token of `word`."""
    return SECRET_PREFIX + word


def token_hash(token: str) -> str:
    from .reserved import token_hash as _hash

    return _hash(token)


# --------------------------------------------------------------------------- the name

def form(parent: str, word: str) -> str:
    """The scoped sender of `word` for `parent`: the parent's name, a dash and the word,
    in the parent's scope. Raises `ValueError` for a parent that is not a comms name, or
    a word that is not one."""
    if not isinstance(word, str) or not WORD_RE.match(word):
        raise ValueError(f"{word!r} is not a word for a scoped sender (lower-case letters "
                         "and digits, starting with a letter)")
    name, scope = _parent(parent)
    return qualify(f"{name}-{word}", scope)


def _parent(parent) -> tuple[str, str]:
    """(name, scope) of `parent`, checked."""
    if not isinstance(parent, str) or not parent:
        raise ValueError(f"{parent!r} is not a comms name")
    name, scope = split_tag(parent)
    scope = GLOBAL if scope in (None, "") else scope
    if not NAME_RE.match(name) or (scope != GLOBAL and not NAME_RE.match(scope)):
        raise ValueError(f"{parent!r} is not a comms name")
    return name, scope


def parent_of(parent: str) -> str:
    """`parent` qualified, as comms writes it (`builder@acme`, `fleet-lead`). Raises
    `ValueError` for what is not a comms name."""
    name, scope = _parent(parent)
    return qualify(name, scope)


def split(qualified: str) -> tuple[str, str] | None:
    """(parent, word) for a name of the scoped form, read from its shape alone: the part
    of its name after the last dash is the word. None for a name that has no such part."""
    if not isinstance(qualified, str) or not qualified:
        return None
    name, scope = split_tag(qualified)
    scope = GLOBAL if scope in (None, "") else scope
    head, dash, word = name.rpartition("-")
    if not dash or not head or not WORD_RE.match(word) or not NAME_RE.match(head):
        return None
    if scope != GLOBAL and not NAME_RE.match(scope):
        return None
    return qualify(head, scope), word


# --------------------------------------------------------------------------- the declarations

@dataclass(frozen=True)
class Broken:
    """A declaration left out, and why."""

    word: str
    module: str | None
    reason: str


@dataclass
class Declared:
    """The words the installed modules declare: module -> {word: description}, the
    declarations left out, and `kept`, the words whose declaration didn't load, which keep
    their row as it is."""

    words: dict = field(default_factory=dict)
    broken: list = field(default_factory=list)
    kept: set = field(default_factory=set)

    def by_word(self) -> dict:
        """word -> {module: description}."""
        out: dict = {}
        for module, words in self.words.items():
            for word, description in words.items():
                out.setdefault(word, {})[module] = description
        return out


def check(word, description) -> str | None:
    """Why a declaration of `word` doesn't hold, or None when it does."""
    if not isinstance(word, str) or not WORD_RE.match(word):
        return (f"{word!r} is not a word for a scoped sender (lower-case letters and "
                "digits, starting with a letter, with no dash)")
    if not isinstance(description, str):
        return f"its declaration is a {type(description).__name__}, not a line of text"
    text = description.strip()
    if not text or "\n" in text or "\r" in text:
        return "its declaration is not one line of text"
    if len(text) > DESCRIPTION_MAX_CHARS:
        return f"its description is over {DESCRIPTION_MAX_CHARS} characters"
    return None


def from_mapping(mapping: Mapping, gone: str | None = None) -> Declared:
    """module -> {word: description}, each checked, as `load` reads the group; `gone`'s
    words are left out."""
    out = Declared()
    for module in sorted(mapping):
        if module == gone:
            continue
        for word, description in dict(mapping[module]).items():
            why = check(word, description)
            if why:
                out.broken.append(Broken(str(word), module, why))
                continue
            out.words.setdefault(module, {})[word] = description.strip()
    return out


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dist_name(dist) -> str:
    if dist is None:
        return ""
    try:
        return dist.metadata["Name"] or ""
    except Exception:                                              # noqa: BLE001
        return getattr(dist, "name", "") or ""


def load(gone: str | None = None, *, entries: Iterable | None = None,
         found: Iterable | None = None) -> Declared:
    """The words the group declares (or `entries`, each with `.name`, `.load()` and
    `.dist`, for tests), each checked and given to the module whose distribution carries
    it, from the installed modules (`found`, each with `.name`, `.state` and
    `.distribution`, else `moxi.modules.scan()`). `gone`'s words are left out, and so is
    a word of a distribution that is no installed module. Never raises."""
    from moxi import modules

    if entries is None:
        try:
            from importlib.metadata import entry_points

            entries = list(entry_points(group=GROUP))
        except Exception as exc:  # noqa: BLE001 - a broken environment costs the words
            log.warning("reading the %s group failed: %s", GROUP, exc)
            entries = []
    if found is None:
        try:
            found = modules.scan()
        except Exception as exc:                                   # noqa: BLE001
            log.warning("the installed modules couldn't be read: %s", exc)
            found = []
    owners = {}
    for f in found:
        dist = getattr(f, "distribution", None)
        if dist and getattr(f, "state", None) in (modules.PRESENT, modules.BROKEN):
            owners[_normalize(dist)] = f.name
    out = Declared()
    for ep in entries:
        dist = _dist_name(getattr(ep, "dist", None))
        module = owners.get(_normalize(dist)) if dist else None
        if module is None:
            out.broken.append(Broken(ep.name, None, f"its distribution {dist or '(none)'} "
                                                    "is no installed Moxi module"))
            continue
        if module == gone:
            continue
        try:
            description = ep.load()
        except Exception as exc:                                   # noqa: BLE001
            out.kept.add(ep.name)
            out.broken.append(Broken(ep.name, module, f"its declaration does not load: "
                                                      f"{type(exc).__name__}: {exc}"))
            continue
        why = check(ep.name, description)
        if why:
            out.broken.append(Broken(ep.name, module, why))
            continue
        out.words.setdefault(module, {})[ep.name] = description.strip()
    for b in out.broken:
        log.warning("scoped sender word %s is left out: %s", b.word, b.reason)
    return out


# --------------------------------------------------------------------------- reads

def rows(conn) -> dict:
    """word -> its `scoped` row, freed ones included."""
    conn.row_factory = sqlite3.Row
    return {r["word"]: r for r in conn.execute("SELECT * FROM scoped")}


def word_row(conn, word: str):
    """The `scoped` row of `word` that isn't freed, or None."""
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM scoped WHERE word = ? AND freed_at IS NULL",
                        (word,)).fetchone()


def declarers(row) -> tuple:
    """The modules that declare the word of `row`."""
    return tuple((row["declared_by"] or "").split())


@dataclass(frozen=True)
class Form:
    """A name of a declared word's form: the name, its parent, the word and the word's
    row."""

    qualified: str
    parent: str
    word: str
    row: object


def form_of(conn, qualified: str) -> Form | None:
    """The declared word whose form `qualified` has, or None: a name no session binds
    and no program registers."""
    parts = split(qualified)
    if parts is None:
        return None
    parent, word = parts
    row = word_row(conn, word)
    if row is None:
        return None
    return Form(qualified, parent, word, row)


def held(conn, qualified: str) -> str | None:
    """What holds `qualified`, in a sentence's words, or None: a live session's binding,
    or a send-only name (an open interval, or a reserved row not freed)."""
    open_ = conn.execute("SELECT kind FROM bindings WHERE qualified = ? AND "
                         "released_at IS NULL ORDER BY id DESC LIMIT 1",
                         (qualified,)).fetchone()
    if open_ is not None and open_[0] == "session":
        return "a live session holds it"
    if open_ is not None or conn.execute(
            "SELECT 1 FROM reserved WHERE name = ? AND freed_at IS NULL",
            (qualified,)).fetchone():
        return "it is a send-only name"
    return None


def conflict_why(conn, found: Form, *, bound: bool = False) -> str | None:
    """Why the scoped sender `found` names can't send, in a sentence's words, or None
    when it is active. `bound` says the daemon holds a live binding of the name."""
    row = found.row
    if row["state"] != ACTIVE:
        return f"the word {found.word} is in conflict ({row['note'] or 'in conflict'})"
    why = "a live session holds it" if bound else held(conn, found.qualified)
    if why:
        return (f"{found.qualified} is held by its holder ({why}), so "
                f"{row['module']}'s scoped sender for {found.parent} is in conflict")
    return None


def sender_of(conn, qualified: str):
    """The active scoped sender `qualified` names, as (module, word, parent), or None: a
    name whose word no module declares now, whose word is in conflict, or that something
    holds, reads back as none."""
    found = form_of(conn, qualified)
    if found is None or conflict_why(conn, found) is not None:
        return None
    return found.row["module"], found.word, found.parent


def unmailable(conn, qualified: str) -> bool:
    """Does `qualified` name a name of a declared word's form that nothing holds? No
    session can bind it, so nobody mails it."""
    found = form_of(conn, qualified)
    return found is not None and held(conn, qualified) is None


def bind_refusal(found: Form) -> str:
    """The daemon's words refusing a bind of `found`'s name."""
    row = found.row
    if row["state"] == ACTIVE:
        whose = f"{row['module']}'s scoped sender for {found.parent}"
    else:
        whose = f"a scoped sender of the word {found.word}, which is in conflict"
    return f"{found.qualified} is {whose}: no session binds it"


@dataclass(frozen=True)
class State:
    """Whether `module`'s scoped sender for `parent` can send now. `qualified` is its
    name when the word is known, `reason` one of `REASONS` when it can't send, and `why`
    says it in words."""

    module: str
    parent: str
    word: str | None
    qualified: str | None
    can_send: bool
    reason: str | None = None
    why: str | None = None


def state(conn, module: str, parent: str, word: str | None = None) -> State:
    """Whether `module`'s scoped sender for `parent` can send now, from comms.db alone.
    `word` is the module's word; None takes the one word the module declares, and raises
    `ValueError` when it declares more than one. Raises `ValueError` for a parent that is
    not a comms name."""
    parent = parent_of(parent)
    if word is not None and not WORD_RE.match(str(word)):
        raise ValueError(f"{word!r} is not a word for a scoped sender")
    if conn is None:
        return State(module, parent, word, form(parent, word) if word else None,
                     can_send=False, reason=UNREADABLE, why="comms' store can't be read")
    try:
        found_rows = [r for r in rows(conn).values()
                      if r["freed_at"] is None and module in declarers(r)]
    except sqlite3.Error as exc:
        return State(module, parent, word, form(parent, word) if word else None,
                     can_send=False, reason=UNREADABLE,
                     why=f"comms' store can't be read: {exc}")
    if word is None:
        if len(found_rows) > 1:
            raise ValueError(f"{module} declares more than one word for scoped senders "
                             f"({', '.join(sorted(r['word'] for r in found_rows))}); "
                             "name one")
        if not found_rows:
            return State(module, parent, None, None, can_send=False, reason=UNDECLARED,
                         why=f"{module} declares no word for scoped senders")
        word = found_rows[0]["word"]
    qualified = form(parent, word)
    if not any(r["word"] == word for r in found_rows):
        return State(module, parent, word, qualified, can_send=False, reason=UNDECLARED,
                     why=f"{module} doesn't declare the word {word}")
    try:
        why = conflict_why(conn, form_of(conn, qualified))
    except sqlite3.Error as exc:
        return State(module, parent, word, qualified, can_send=False, reason=UNREADABLE,
                     why=f"comms' store can't be read: {exc}")
    if why:
        return State(module, parent, word, qualified, can_send=False, reason=IN_CONFLICT,
                     why=why)
    return State(module, parent, word, qualified, can_send=True)


def holders(conn) -> list:
    """(qualified, parent, word, module, why) for each name of an active word's form that
    something holds: each parent whose scoped sender is in conflict."""
    out = []
    for word, row in sorted(rows(conn).items()):
        if row["freed_at"] is not None or row["state"] != ACTIVE:
            continue
        names = {r[0] for r in conn.execute(
            "SELECT qualified FROM bindings WHERE released_at IS NULL AND "
            "(name GLOB ? OR qualified GLOB ?)", (f"*-{word}", f"*-{word}"))}
        names |= {r[0] for r in conn.execute(
            "SELECT name FROM reserved WHERE freed_at IS NULL AND name GLOB ?",
            (f"*-{word}",))}
        for qualified in sorted(names):
            found = form_of(conn, qualified)
            if found is None or found.word != word:
                continue
            why = held(conn, qualified)
            if why:
                out.append((qualified, found.parent, word, row["module"], why))
    return out


# --------------------------------------------------------------------------- the reconcile

def reconcile(conn, declared: Declared, chain, clock, result) -> None:
    """Bring the `scoped` table in line with `declared`, in the reconcile `reserved`
    runs under its lock: a word one module declares is active with a token minted for
    that module, a word two declare is in conflict, a word nobody declares is freed and
    its token removed. A word in `declared.kept` stays as it is. Writes `result`'s
    `word_*` fields and its lines."""
    from moxi.store import transaction

    for b in declared.broken:
        result.word_conflicts.append((b.word, b.module, b.reason))
        result.lines.append(f"{b.module or 'a module'}: the scoped sender word {b.word} "
                            f"is left out: {b.reason}")
    table = rows(conn)
    wanted = declared.by_word()
    for word, row in sorted(table.items()):
        if row["freed_at"] is not None or word in wanted or word in declared.kept:
            continue
        with transaction(conn) as c:
            c.execute("UPDATE scoped SET freed_at = ?, token_hash = NULL WHERE word = ?",
                      (stamp(clock()), word))
        result.words_freed.append((word, row["module"]))
        result.lines.append(f"{row['module'] or 'comms'}: the scoped sender word {word} "
                            "freed")
        if row["module"]:
            _remove_token(chain, word, row["module"], result)
    for word in sorted(wanted):
        modules_ = wanted[word]
        by = " ".join(sorted(modules_))
        row = table.get(word)
        live = row is not None and row["freed_at"] is None
        if len(modules_) > 1:
            why = f"{' and '.join(sorted(modules_))} each declare it"
            result.word_conflicts.append((word, None, why))
            result.lines.append(f"{' and '.join(sorted(modules_))}: the scoped sender word "
                                f"{word} isn't declared: {why}")
            if live and row["state"] == CONFLICT and row["declared_by"] == by:
                continue
            with transaction(conn) as c:
                c.execute("INSERT INTO scoped(word, module, declared_by, description, "
                          "token_hash, state, declared_at, freed_at, note) VALUES "
                          "(?, NULL, ?, NULL, NULL, 'conflict', ?, NULL, ?) "
                          "ON CONFLICT(word) DO UPDATE SET module = NULL, "
                          "declared_by = excluded.declared_by, description = NULL, "
                          "token_hash = NULL, state = 'conflict', declared_at = "
                          "COALESCE(scoped.declared_at, excluded.declared_at), "
                          "freed_at = NULL, note = excluded.note",
                          (word, by, stamp(clock()), why))
            if live and row["state"] == ACTIVE and row["module"]:
                _remove_token(chain, word, row["module"], result)
            continue
        ((module, description),) = modules_.items()
        if live and row["state"] == ACTIVE and row["module"] == module \
                and row["token_hash"] is not None:
            if row["description"] != description:
                with transaction(conn) as c:
                    c.execute("UPDATE scoped SET description = ? WHERE word = ?",
                              (description, word))
            continue
        if live and row["state"] == ACTIVE and row["module"] and row["module"] != module:
            _remove_token(chain, word, row["module"], result)
        _mint(conn, chain, word, module, description, clock, result,
              since=row["declared_at"] if live else None)


def _mint(conn, chain, word: str, module: str, description: str, clock, result, *,
          since=None) -> None:
    """A fresh token for `word`, written to the chain for `module`, then the row active
    with its hash. A write that fails commits nothing."""
    from moxi.store import transaction

    from .reserved import owner
    from .secrets import SecretError

    token = _secrets.token_urlsafe(32)
    try:
        step = chain.put(secret_id(word), token, owner=owner(module))
    except SecretError as exc:
        result.word_failed.append((word, module, str(exc)))
        result.lines.append(f"{module}: the scoped sender word {word} isn't declared: its "
                            f"token couldn't be written. {exc}")
        return
    at = since or stamp(clock())
    with transaction(conn) as c:
        c.execute("INSERT INTO scoped(word, module, declared_by, description, token_hash, "
                  "state, declared_at, freed_at, note) VALUES (?, ?, ?, ?, ?, 'active', ?, "
                  "NULL, NULL) ON CONFLICT(word) DO UPDATE SET module = excluded.module, "
                  "declared_by = excluded.declared_by, description = "
                  "excluded.description, token_hash = excluded.token_hash, "
                  "state = 'active', declared_at = excluded.declared_at, freed_at = NULL, "
                  "note = NULL",
                  (word, module, module, description, token_hash(token), at))
    result.words.append((word, module))
    result.lines.append(f"{module}: the scoped sender word {word} declared")
    log.info("scoped sender word %s declared for %s (token in the %s step)", word, module,
             step)


def _remove_token(chain, word: str, module: str, result) -> None:
    from .reserved import owner
    from .secrets import SecretError

    try:
        chain.delete(secret_id(word), owner=owner(module))
    except SecretError as exc:
        result.lines.append(f"{module}: the token of the scoped sender word {word} "
                            f"couldn't be removed: {exc}")
        log.warning("the token of %s for %s couldn't be removed: %s", word, module, exc)


def release_all(conn, chain, clock, result) -> None:
    """comms' own uninstall: every word freed and every word token comms minted
    removed."""
    from moxi.store import transaction

    for word, row in sorted(rows(conn).items()):
        if row["freed_at"] is None:
            with transaction(conn) as c:
                c.execute("UPDATE scoped SET freed_at = ?, token_hash = NULL "
                          "WHERE word = ?", (stamp(clock()), word))
            result.words_freed.append((word, row["module"]))
            result.lines.append(f"{row['module'] or 'comms'}: the scoped sender word "
                                f"{word} freed")
        for module in declarers(row):
            _remove_token(chain, word, module, result)


def token_rows(conn, chain, context: str) -> list:
    """A doctor row for each active word whose token this process's chain can't read,
    or reads with a hash that doesn't match."""
    from moxi.doctor import Row

    from .release import MODULE
    from .reserved import owner
    from .secrets import SecretError

    out = []
    for word, row in sorted(rows(conn).items()):
        if row["freed_at"] is not None or row["state"] != ACTIVE:
            continue
        try:
            got = chain.get(secret_id(word), owner=owner(row["module"]))["value"]
        except SecretError:
            got = None
        if got is None or token_hash(got) != row["token_hash"]:
            out.append(Row(MODULE, "scoped", "warn",
                           f"comms can't read {row['module']}'s token for the scoped "
                           f"sender word {word} from {context} environment",
                           f"moxi comms reserved renew --word {word}"))
    return out


def renew(conn, chain, word: str, clock, result) -> None:
    """A fresh token for the active word `word`, for its module. Raises `KeyError` when
    `word` isn't active."""
    row = word_row(conn, word)
    if row is None or row["state"] != ACTIVE:
        raise KeyError(word)
    _mint(conn, chain, word, row["module"], row["description"], clock, result,
          since=row["declared_at"])


__all__ = ["ACTIVE", "CONFLICT", "DESCRIPTION_MAX_CHARS", "GROUP", "IN_CONFLICT",
           "REASONS", "SECRET_PREFIX", "UNDECLARED", "UNREADABLE", "WORD_RE", "Broken",
           "Declared", "Form", "State", "bind_refusal", "check", "conflict_why",
           "declarers", "form", "form_of", "from_mapping", "held", "holders", "load",
           "parent_of", "reconcile", "release_all", "renew", "rows", "secret_id",
           "sender_of", "split", "state", "token_rows", "unmailable", "word_row"]
