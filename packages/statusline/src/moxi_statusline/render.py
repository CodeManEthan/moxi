"""`moxi-statusline-render`: the command Claude Code runs for each render of its status
line.

    moxi-statusline-render [--show a,b,c] [--only NAME] [--config PATH] [--no-color]

It reads Claude's input, JSON on stdin, once, and prints the line: one or two rows of
items joined by `separator`. An item is a base field (`moxi_statusline.fields`) or the name
of a module whose announcement has a `SEGMENT`. An item with no value is left out with its
separator.

**A render shows status and triggers nothing.** Before it reads its config or loads a
segment it installs the core's render audit hook (`moxi.render`), so from then on it
writes no file, starts no process, sends no signal and opens no socket; a segment that
tries is refused and shows nothing. It exits 0 on every path, so Claude always gets a
line: an input that is not JSON prints the line with `?` for the directory, and a config
that can't be read prints `config?`, each with one line on stderr, which Claude does not
show.

A module item is found with `moxi.modules.announced`, which reads the announcement and
imports no API module. Its `SEGMENT` module is imported, then `from_input(payload, now)`
is called when the module defines it, else `segment(session_id, now)`. A result with a
string `text` shows its `coloured()`, or its `text` with colour off. A segment that raises
or returns anything else shows nothing and names itself on stderr; nothing one item does
stops the others.

Flags:

- `--show a,b,c`: these items in place of the config's `show`.
- `--only NAME`: that one item, and nothing else.
- `--config PATH`: read this file in place of the module's config file, the same way. A
  missing module config file reads as the defaults; a `--config` file that is missing or
  can't be read prints `config?`, so a vanished draft never shows as the default line.
- `--no-color`: no colour, whatever `color` says.

The render imports only what the items it shows need: no module is imported for a base
field, `moxi.modules` only when a module item is shown, and a segment only when its item
is. `RENDER_IMPORTS` lists every module of the core and of this package that a render
may load; install compiles their bytecode, and the doctor checks it.
"""

import os
import sys

#: Every module of the core and of this package a render may import. The install listener
#: compiles them, the doctor checks their bytecode, and a test holds the list true.
RENDER_IMPORTS = (
    "moxi",
    "moxi._mark",
    "moxi.config",
    "moxi.modules",
    "moxi.names",
    "moxi.paths",
    "moxi.render",
    "moxi_statusline",
    "moxi_statusline.fields",
    "moxi_statusline.render",
    "moxi_statusline.settings",
)

PROG = "moxi-statusline-render"
USAGE = f"usage: {PROG} [--show a,b,c] [--only NAME] [--config PATH] [--no-color]"
#: The whole line when the config can't be read.
CONFIG_UNREADABLE = "config?"


class _Usage(Exception):
    """Arguments the render does not take."""


class _Args:
    __slots__ = ("show", "only", "config", "color", "help")

    def __init__(self):
        self.show = None
        self.only = None
        self.config = None
        self.color = True
        self.help = False


def main(argv=None) -> int:
    """Render the line from stdin to stdout. Returns 0 on every path."""
    try:
        out = _main(sys.argv[1:] if argv is None else list(argv))
    except BaseException as exc:  # noqa: BLE001 - nothing stops the line
        _warn(f"the line failed: {_one_line(exc)}")
        out = "?"
    _write(out)
    return 0


def _main(argv: list) -> str:
    from moxi import render as hook

    hook.install()
    try:
        args = _parse(argv)
    except _Usage as exc:
        _warn(f"{exc}; {USAGE}")
        return ""
    if args.help:
        return USAGE
    payload = _payload()
    try:
        from . import settings as config

        if args.config is not None:
            _exists(args.config)
        values = config.load(args.config)
    except Exception as exc:  # noqa: BLE001 - a config that can't be read is one line
        _warn(_one_line(exc))
        return CONFIG_UNREADABLE
    items = ([args.only] if args.only is not None
             else args.show if args.show is not None else list(values["show"]))
    from .fields import Options

    options = Options(parts=values["dir.parts"], warn_at=values["context.warn_at"],
                      alert_at=values["context.alert_at"],
                      color=values["color"] and args.color, home=os.environ.get("HOME"))
    separator = "" if args.only is not None else values["separator"]
    return "\n".join(lines(items, payload, options, separator))


def _parse(argv: list) -> _Args:
    args = _Args()
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        name, eq, value = arg.partition("=")
        if name in ("--show", "--only", "--config"):
            if not eq:
                if not rest:
                    raise _Usage(f"{name} needs a value")
                value = rest.pop(0)
            if name == "--show":
                args.show = [item.strip() for item in value.split(",") if item.strip()]
            elif name == "--only":
                args.only = value.strip()
            else:
                args.config = value
        elif arg == "--no-color":
            args.color = False
        elif arg in ("-h", "--help"):
            args.help = True
        else:
            raise _Usage(f"unknown argument {arg!r}")
    return args


def _payload() -> dict | None:
    """Claude's input, or None when it is not a JSON object."""
    import json

    try:
        payload = json.loads(sys.stdin.buffer.read())
    except Exception as exc:  # noqa: BLE001 - any unreadable input is one '?'
        _warn(f"the input is not JSON ({_one_line(exc)})")
        return None
    if not isinstance(payload, dict):
        _warn("the input is not a JSON object")
        return None
    return payload


def _exists(path: str) -> None:
    """Refuse a `--config` file that is not there: the module's own file may be missing,
    but a file named to the render must be read."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{path} is not there to read (--config)")


def lines(items, payload: dict | None, options, separator: str, *,
          segment_of=None, now: float | None = None) -> list[str]:
    """The rows of the line for `items`: each item's text joined by `separator`, a new row
    at each `row`, with empty items and empty rows left out.

    `segment_of(name)` returns module `name`'s segment module, or None when it has none
    (default: `segment_module`, through the core's discovery).
    """
    from .fields import RESERVED, ROW, field

    if now is None and any(item not in RESERVED for item in items):
        import time

        now = time.time()
    rows, row = [], []
    for item in items:
        if item == ROW:
            rows.append(row)
            row = []
            continue
        if item in RESERVED:
            text = field(item, payload, options)
        else:
            text = _segment(item, payload, options.color, segment_of, now)
        if text:
            row.append(text)
    rows.append(row)
    return [separator.join(row) for row in rows if row]


def _segment(name: str, payload, color: bool, segment_of, now) -> str | None:
    """Module item `name`'s text, or None. Never raises: a failure is one stderr line."""
    if payload is None:
        return None
    try:
        module = (segment_of or segment_module)(name)
        if module is None:
            return None
        from_input = getattr(module, "from_input", None)
        if callable(from_input):
            result = from_input(payload, now)
        else:
            session = payload.get("session_id")
            if not isinstance(session, str) or not session:
                return None
            result = module.segment(session, now)
        if result is None:
            return None
        text = getattr(result, "text", None)
        if not isinstance(text, str):
            raise TypeError(f"returned {type(result).__name__}, which has no text")
        if not color:
            return text
        coloured = result.coloured()
        if not isinstance(coloured, str):
            raise TypeError(f"coloured() returned {type(coloured).__name__}, not text")
        return coloured
    except (Exception, SystemExit) as exc:
        _warn(f"{name}: {_one_line(exc)}")
        return None


def segment_module(name: str):
    """Module `name`'s `SEGMENT` module, imported under the render audit hook, or None
    when the module is not present or has no segment."""
    from moxi import modules
    from moxi import render as hook

    found = modules.announced(name)
    if found.state != modules.PRESENT or not found.segment:
        return None
    return hook.load_segment(found.segment)


def _one_line(exc: BaseException) -> str:
    said = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {said[0]}" if said else type(exc).__name__


def _warn(text: str) -> None:
    try:
        sys.stderr.write(f"{PROG}: {text}\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 - stderr gone; the line still goes out
        pass


def _write(out: str) -> None:
    try:
        sys.stdout.buffer.write(out.encode("utf-8", "replace") + b"\n")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001 - Claude stopped reading; nothing to do
        pass


if __name__ == "__main__":
    sys.exit(main())
