"""One render of the line by the real command, in a child process, timed.

The preview verb, the doctor's sample render and the module's page all go through
`render`, so each shows what Claude would get: the command Claude's setting names, the
same flags, and the time it took from start to exit. The child installs the render audit
hook; the caller does not.
"""

import json
import subprocess
import time
from collections.abc import Mapping, Sequence

from moxi import paths

#: The console script Claude's setting names.
SCRIPT = "moxi-statusline-render"
#: How long a render may take here before it is stopped.
TIMEOUT = 2.0


class PreviewError(Exception):
    """A render that could not run, or did not finish in time."""


class Rendered:
    """What one render printed: `lines`, the rows on stdout; `stderr`, its notes; `ms`,
    the wall time from start to exit."""

    __slots__ = ("lines", "stderr", "ms")

    def __init__(self, lines: list[str], stderr: str, ms: float):
        self.lines = lines
        self.stderr = stderr
        self.ms = ms

    def __repr__(self) -> str:
        return f"Rendered({self.lines!r}, ms={self.ms:.1f})"


def render(payload: Mapping, *, show: Sequence[str] | None = None, only: str | None = None,
           config: str | None = None, color: bool = True, timeout: float = TIMEOUT,
           env: Mapping[str, str] | None = None) -> Rendered:
    """Run the render over `payload` with these flags and return what it printed.

    Raises `PreviewError` when the command is not installed in this environment or does
    not exit within `timeout` seconds."""
    try:
        argv = [str(paths.script(SCRIPT))]
    except FileNotFoundError as exc:
        raise PreviewError(str(exc)) from None
    if show is not None:
        argv += ["--show", ",".join(show)]
    if only is not None:
        argv += ["--only", only]
    if config is not None:
        argv += ["--config", str(config)]
    if not color:
        argv.append("--no-color")
    data = json.dumps(payload).encode("utf-8")
    start = time.perf_counter()
    try:
        done = subprocess.run(argv, input=data, capture_output=True, timeout=timeout,
                              check=False, env=None if env is None else dict(env))
    except subprocess.TimeoutExpired:
        raise PreviewError(f"{SCRIPT} did not finish in {timeout:g} s") from None
    except OSError as exc:
        raise PreviewError(f"{SCRIPT} could not start: {exc}") from None
    ms = (time.perf_counter() - start) * 1000
    out = done.stdout.decode("utf-8", "replace").rstrip("\n")
    return Rendered(out.split("\n") if out else [], done.stderr.decode("utf-8", "replace"),
                    ms)
