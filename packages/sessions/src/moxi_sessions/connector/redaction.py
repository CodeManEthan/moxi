"""The chat redaction as the sessions provider uses it: secrets' `chat_redact`, reached
through `moxi.modules.load` because secrets is an optional link of sessions, checked
before every call so a tool fails closed.

`Gate.open()` loads secrets and runs the chat redaction on an empty text. When either
fails (secrets missing or broken, a chat set that can't run, the fallback worker broken)
it raises `Off` with the reason, and the tool refuses `redaction-off`. Otherwise it gives
a `Redactor`, which redacts through `chat_redact` alone and counts the placeholders it
added, for the call log, and carries secrets' `secret_path`, which names the secret path
a tool call's input holds. A caller's own change to a text (the path cut) goes in as
`between`, which `chat_redact` runs between its two passes, so the last redaction sees
the text sent. A secrets module whose `chat_redact` takes no `between`, or that offers no
`secret_path`, is off too.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: The secrets interface the provider asks for.
SECRETS_INTERFACE = 1
#: What a tool answers, refused `redaction-off`, when secrets can't redact.
OFF_TEXT = ("Secrets can't be checked right now, so nothing was read. "
            "moxi doctor connector says why.")
#: The start of every placeholder secrets writes.
PLACEHOLDER = "[REDACTED:"


class Off(Exception):
    """Secrets can't redact for a chat now; `reason` says why."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Redactor:
    """One call's redaction: `chat_redact`, and how many placeholders it added.
    `secret_path(value)` is secrets' own: the first secret path a tool's input names, or
    None."""

    def __init__(self, chat_redact: Callable[[str], str],
                 secret_path: Callable[[Any], str | None]):
        self._chat_redact = chat_redact
        self.secret_path = secret_path
        self.redactions = 0

    def __call__(self, text: str, *, between: Callable[[str], str] | None = None) -> str:
        out = (self._chat_redact(text) if between is None
               else self._chat_redact(text, between=between))
        self.redactions += max(0, out.count(PLACEHOLDER) - text.count(PLACEHOLDER))
        return out


def _load_secrets() -> Any:
    from moxi import modules

    found = modules.load("secrets", interface=SECRETS_INTERFACE)
    if not found.ok or found.api is None:
        raise Off(f"the secrets module is {found.state}"
                  + (f" ({found.reason})" if found.reason else ""))
    return found.api


class Gate:
    """Opens a redaction for a call, or says why there is none. `load` gives secrets' API
    (a test hands in its own)."""

    def __init__(self, load: Callable[[], Any] | None = None):
        self._load = load or _load_secrets

    def open(self) -> Redactor:
        """A `Redactor` for one call. Raises `Off` when secrets or a chat set can't run."""
        secrets = self._load()
        chat_redact = getattr(secrets, "chat_redact", None)
        if not callable(chat_redact):
            raise Off("the secrets module offers no chat_redact")
        try:
            empty = chat_redact("")
        except Exception as exc:  # noqa: BLE001 - any failure means no redaction
            raise Off(f"the chat sets can't run: {exc}") from exc
        if empty != "":
            raise Off("the chat sets came back unscanned on an empty text")
        try:
            chat_redact("", between=str)
        except TypeError:
            raise Off("the secrets module's chat_redact takes no between") from None
        except Exception as exc:  # noqa: BLE001 - any failure means no redaction
            raise Off(f"the chat sets can't run: {exc}") from exc
        secret_path = getattr(secrets, "secret_path", None)
        if not callable(secret_path):
            raise Off("the secrets module offers no secret_path")
        return Redactor(chat_redact, secret_path)

    def row(self) -> tuple[str, str, str]:
        """The `redaction` status row: whether the chat sets load, and the engine."""
        try:
            self.open()
        except Off as exc:
            return ("redaction", "fail", f"{exc.reason}; every tool refuses")
        engine = _engine_name(self._load())
        if engine == "re2":
            return ("redaction", "ok", "the chat sets load; engine re2")
        if engine is None:
            return ("redaction", "ok", "the chat sets load; secrets names no engine")
        return ("redaction", "warn", f"the chat sets load on the {engine} engine, the "
                                     "fallback: a long text may come back unscanned and "
                                     "is then withheld")


def _engine_name(secrets: Any) -> str | None:
    engine = getattr(secrets, "engine", None)
    active = getattr(engine, "active", None)
    if not callable(active):
        return None
    try:
        return str(active().name)
    except Exception:  # noqa: BLE001 - the row only reports it
        return None


__all__ = ["OFF_TEXT", "Gate", "Off", "Redactor"]
