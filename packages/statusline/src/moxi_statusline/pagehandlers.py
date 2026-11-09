"""The handlers of statusline's page.

`state` reads the config file once, through `moxi.config.draft_text` with no changes, so
the values it sends and their `base` stamp come from the same bytes. `preview` builds the
draft's text with `draft_text` over `base`, writes it whole to a file of its own in the
page server's runtime directory, runs the real render on it over the sample input, and
removes the file once the render has exited or been stopped. `save` writes the same draft
with `moxi.config.set_many` over the same `base`. `use` and `restore` run the code the
verbs run.

How a refusal is answered: a file changed since `base`, or held by another writer past
the lock's wait (both `ConfigError`s with exit code 75), is a 409; any other
`ConfigError` is a 422 naming the key when it has one. Nothing is written either way.

The page server removes any `preview-*.toml` left in its runtime directory by one that
stopped mid-render. It does so on the first request it answers, not when this module is
imported: the runner imports the handlers before it takes the page lock, and a request
is only answered once that lock is held, so the sweep never removes a file another live
page server is rendering.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import os
import tempfile
import threading
from typing import Any

from moxi import config, files, modules
from moxi import paths as moxi_paths

from . import compile as segments
from . import preview as render
from . import sample, settings, switch
from .fields import BASE_FIELDS, ROW

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"statusline's page needs the ui module: {_ui.reason}")
lb = _ui.api.loopback

MODULE = "statusline"
#: Who a Save names in the config's history.
BY = "ui"
#: Seconds a preview's render may take before it is stopped.
TIMEOUT = render.TIMEOUT
#: The prefix and suffix of a preview's file in the runtime directory.
PREFIX = "preview-"
SUFFIX = ".toml"
#: The made-up folder the sample input is in, under the home folder. Nothing is read
#: there but the branch, which it has none of.
SAMPLE_FOLDER = ("projects", "sample")
#: The note under the preview for a shown item with nothing for the sample.
LIVE = "shows in a live session"
NO_BRANCH = "shows inside a git repository"

_swept = False
_sweep_lock = threading.Lock()


# -- the runtime directory ---------------------------------------------------------------

def runtime_dir():
    """The page server's runtime directory, made private when missing."""
    return moxi_paths.Layout(MODULE).runtime_dir(create=True)


def sweep() -> list[str]:
    """Remove every `preview-*.toml` in the runtime directory; return their names."""
    gone = []
    for path in sorted(runtime_dir().glob(f"{PREFIX}*{SUFFIX}")):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
            gone.append(path.name)
    return gone


def _sweep_once() -> None:
    global _swept
    if _swept:
        return
    with _sweep_lock:
        if not _swept:
            sweep()
            _swept = True


# -- the state ---------------------------------------------------------------------------

def _values(read: config.Settings) -> dict[str, Any]:
    return {
        "show": list(read["show"]),
        "separator": read["separator"],
        "color": read["color"],
        "dir": {"parts": read["dir.parts"]},
        "context": {"warn_at": read["context.warn_at"],
                    "alert_at": read["context.alert_at"]},
    }


def _stamp_now() -> str:
    """The stamp of the config file's bytes as they are, as `draft_text` makes it."""
    path = moxi_paths.Layout(MODULE).config_file()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        data = b""
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()


def _problem(exc: config.ConfigError) -> str:
    where = f"{exc.key} " if exc.key else ""
    return f"The saved config can't be read: {where}{exc.problem}. Fix: {exc.fix}."


def _saved() -> tuple[str, dict[str, Any], str | None]:
    """`(base, values, problem)` from one read of the config file. A file that can't be
    read gives the defaults and says why."""
    try:
        text, base = config.draft_text(MODULE, [])
        read = config.check_text(MODULE, settings.schema(), text)
    except config.ConfigError as exc:
        defaults = config.check_text(MODULE, settings.schema(), "")
        return _stamp_now(), _values(defaults), _problem(exc)
    except OSError as exc:
        defaults = config.check_text(MODULE, settings.schema(), "")
        return (_stamp_now(), _values(defaults),
                f"The saved config can't be read: {exc.strerror or exc}.")
    return base, _values(read), None


def _modules() -> list[dict[str, str]]:
    """Each installed module with a status line segment whose name the line reads as a
    module (a module named like a base field is read as the field)."""
    out = []
    for name in segments.present_segments():
        if name in BASE_FIELDS or name == ROW:
            continue
        out.append({"name": name, "summary": modules.announced(name).summary or ""})
    return out


def _claude() -> dict[str, Any]:
    found = switch.state()
    return {
        "set": bool(found["set"]),
        "command": found["command"],
        "moxis": bool(found["moxis"]),
        "here": bool(found["here"]),
        "saved": found["saved"] is not None and found["saved_error"] is None,
        "problem": found["error"] or found["saved_error"],
    }


def current() -> dict[str, Any]:
    """The page's state now."""
    base, values, problem = _saved()
    return {"base": base, "values": values, "problem": problem,
            "fields": list(BASE_FIELDS), "modules": _modules(), "claude": _claude()}


def state(call: Any) -> dict[str, Any]:
    """`GET /api/state`."""
    _sweep_once()
    return current()


# -- drafts ------------------------------------------------------------------------------

def changes(body: dict) -> list[tuple[str, Any]]:
    """The body's `changes` as `(dotted key, value)` pairs, in the order given."""
    out: list[tuple[str, Any]] = []
    for key, value in (body.get("changes") or {}).items():
        if isinstance(value, dict):
            out += [(f"{key}.{sub}", item) for sub, item in value.items()]
        else:
            out.append((key, value))
    return out


def refusal(exc: config.ConfigError) -> Exception:
    """The answer to a refused draft: `Conflict` (409) for a file changed since `base` or
    held by another writer, else `Refused` (422) naming the key."""
    if exc.exit_code == 75:
        if isinstance(exc, config.ConfigChanged):
            return lb.Conflict("The saved config changed since the page read it.")
        return lb.Conflict("The saved config is being written by something else.")
    if exc.key:
        return lb.Refused(f"{exc.key} {exc.problem}. Fix: {exc.fix}.")
    return lb.Refused(f"The draft as a whole was refused: {exc.problem}. Fix: {exc.fix}.")


def _payload() -> dict:
    home = str(moxi_paths.home())
    return sample.payload(cwd=os.path.join(home, *SAMPLE_FOLDER))


def _render_file(path: str, payload: dict, only: str | None = None) -> render.Rendered:
    return render.render(payload, config=path, only=only, timeout=TIMEOUT)


def _shown(text: str) -> list[str]:
    """The items the draft's text shows."""
    return list(config.check_text(MODULE, settings.schema(), text)["show"])


def _notes(path: str, payload: dict, items: list[str], pool: concurrent.futures.Executor
           ) -> list[tuple[str, concurrent.futures.Future]]:
    """One render of each item that may have nothing for the sample, by itself."""
    present = {item["name"] for item in _modules()}
    asked = [item for item in items if item in present or item == "branch"]
    return [(item, pool.submit(_render_file, path, payload, item)) for item in asked]


def preview(call: Any) -> dict[str, Any]:
    """`POST /api/preview`: the draft's line, rendered from a file of its own."""
    _sweep_once()
    try:
        text, _ = config.draft_text(MODULE, changes(call.body), base=call.body["base"])
    except config.ConfigError as exc:
        raise refusal(exc) from None
    # mkstemp makes the name this request's own (0600); the text then goes over it whole,
    # in one rename, before the render starts.
    fd, path = tempfile.mkstemp(dir=runtime_dir(), prefix=PREFIX, suffix=SUFFIX)
    os.close(fd)
    try:
        files.atomic_write(path, text)
        payload = _payload()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            singles = _notes(path, payload, _shown(text), pool)
            try:
                done = _render_file(path, payload)
            except render.PreviewError as exc:
                return {"lines": [], "ms": None, "notes": [], "problem": str(exc)}
            notes = []
            for item, future in singles:
                try:
                    alone = future.result()
                except render.PreviewError:
                    continue
                if not any(line.strip() for line in alone.lines):
                    notes.append(f"{item}: {NO_BRANCH if item == 'branch' else LIVE}")
        return {"lines": done.lines, "ms": round(done.ms, 1), "notes": notes,
                "problem": None}
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


def save(call: Any) -> dict[str, Any]:
    """`POST /api/save`: the draft written in one write; the new state."""
    _sweep_once()
    try:
        config.set_many(MODULE, changes(call.body), by=BY, base=call.body["base"])
    except config.ConfigError as exc:
        raise refusal(exc) from None
    return current()


# -- Claude Code's line --------------------------------------------------------------------

def _acted(act: switch.Act) -> dict[str, Any]:
    if act.ok:
        return {"said": act.lines, **current()}
    text = " ".join(act.lines)
    if act.kind in ("changed", "not-moxis", "nothing-saved"):
        raise lb.Conflict(text)
    raise lb.Refused(text)


def use(call: Any) -> dict[str, Any]:
    """`POST /api/use`: Claude Code's line set to Moxi's, the config's `show` as it is."""
    _sweep_once()
    return _acted(switch.switch(None, by=BY))


def restore(call: Any) -> dict[str, Any]:
    """`POST /api/restore`: the old line put back; refused (409) over a line that isn't
    Moxi's."""
    _sweep_once()
    return _acted(switch.restore())


__all__ = ["BY", "LIVE", "NO_BRANCH", "PREFIX", "SUFFIX", "TIMEOUT", "changes", "current",
           "preview", "refusal", "restore", "runtime_dir", "save", "state", "sweep", "use"]
