"""Hook times: what the agent waits per hook event, from turns' own timing.

turns owns the hooks, so it reports their cost. Every hook wait turns causes passes
through the process serving the session, which keeps the samples:

- **Claude.** The runner's and the gate's hook handlers run inside Claude Code's own
  process. Each times its own work, from entry to return less the time spent in `next`
  (the hooks behind it, and for a tool call the tool's own run), and the plugins post
  the samples to the endpoint's leg at most once a minute (`claude_leg`).
- **Codex.** Each hook is a `moxi-turns-ping` process. It stamps the wall clock before
  its imports and sends the stamp in its ask; the host records the time from the stamp
  to its answer (`codex_hooks`). The interpreter's start before the stamp is not seen,
  so the doctor adds the floor to each Codex sample, and says so.
- **OpenCode.** turns runs no hook the agent waits on, so there is nothing to time.

A `Recorder` buffers the samples in the serving process and appends them, one record
each, `{harness, event, ms, at}`, to `STATE/turns/hook-times.jsonl` through
`moxi.files.append_record`, once a minute and when it closes, so no hook pays for the
write. The store is declared in the announcement's `STORES`.

The doctor's rows (`rows`) give p50 and p90 per harness and event over the last 24
hours, against the announcement's `BUDGETS["hook_ms"]`, a multiple of the floor (the
time of `python -I -c pass`) measured in the same doctor run.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("moxi.turns.hooktimes")

#: The store's name in `STORES`, and its file under turns' state directory.
STORE = "hook-times"
FILE = "hook-times.jsonl"
#: How often a recorder writes its samples.
FLUSH_S = 60.0
#: The window the doctor reads.
WINDOW_S = 24 * 3600
#: Fewer samples than this for an event are too few to judge.
MIN_SAMPLES = 20
#: The harnesses whose hooks the agent waits on.
HARNESSES = ("claude", "codex")
#: The harnesses whose samples miss the interpreter's start, which the doctor adds.
ADDS_FLOOR = frozenset({"codex"})
#: The most samples a recorder holds between writes; past it the oldest go.
BUFFER_MAX = 10_000


def store_path(env=None) -> Path:
    """`STATE/turns/hook-times.jsonl`."""
    from moxi.paths import Layout

    from .config import MODULE

    return Layout(MODULE).state_dir(env) / FILE


class Recorder:
    """The serving process's buffer of one harness's samples, and its writer.

    `add` never blocks on the disk. `start` runs a thread that writes once every
    `interval`; `close` writes what is left and stops it. The file is fixed when the
    recorder is made.
    """

    def __init__(self, harness: str, *, path: Path | None = None,
                 interval: float = FLUSH_S):
        self.harness = harness
        self.path = store_path() if path is None else Path(path)
        self.interval = interval
        self._samples: list[dict] = []
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def add(self, event: str, ms: float, at: float | None = None) -> None:
        """One hook's wait, `ms` milliseconds, for `event`. A sample that is not a
        finite number of zero or more is dropped."""
        if not isinstance(event, str) or not event:
            return
        if isinstance(ms, bool) or not isinstance(ms, (int, float)) \
                or not math.isfinite(ms) or ms < 0:
            return
        record = {"harness": self.harness, "event": event, "ms": round(float(ms), 3),
                  "at": round(time.time() if at is None else float(at), 3)}
        with self._lock:
            self._samples.append(record)
            if len(self._samples) > BUFFER_MAX:
                del self._samples[: len(self._samples) - BUFFER_MAX]

    def flush(self) -> int:
        """Append the buffered samples; returns how many were written. Never raises: a
        write that fails costs those samples and is logged."""
        with self._write_lock:
            with self._lock:
                samples, self._samples = self._samples, []
            if not samples:
                return 0
            from moxi import files

            written = 0
            try:
                for record in samples:
                    files.append_record(self.path, record)
                    written += 1
            except (OSError, ValueError) as exc:
                log.warning("%d hook-time samples were not written to %s: %s",
                            len(samples) - written, self.path, exc)
            return written

    def start(self) -> Recorder:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name=f"turns-hook-times-{self.harness}")
            self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self.flush()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.flush()


@dataclass(frozen=True)
class Sample:
    harness: str
    event: str
    ms: float
    at: float


def read(path: Path | None = None, *, now: float | None = None,
         window_s: float = WINDOW_S) -> list[Sample]:
    """The samples of the last `window_s` seconds, both generations of the log."""
    from moxi import files

    path = store_path() if path is None else Path(path)
    since = (time.time() if now is None else now) - window_s
    out = []
    for r in files.read_records(path):
        harness, event, ms, at = r.get("harness"), r.get("event"), r.get("ms"), r.get("at")
        if not (isinstance(harness, str) and isinstance(event, str)):
            continue
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in (ms, at)):
            continue
        if at >= since:
            out.append(Sample(harness, event, float(ms), float(at)))
    return out


def percentile(values: list[float], p: float) -> float:
    """The nearest-rank `p`th percentile of `values` (not empty)."""
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def floor_ms() -> float:
    """The floor: the core's `moxi.doctor.floor_ms()` where this core has it, else the
    same measure taken here, the median of five `python -I -c pass` starts."""
    from moxi import doctor

    core = getattr(doctor, "floor_ms", None)
    if callable(core):
        return float(core())
    import statistics
    import subprocess
    import sys

    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        subprocess.run([sys.executable, "-I", "-c", "pass"], check=False,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def rows(samples: Iterable[Sample], *, budget: float,
         floor: Callable[[], float] | float = floor_ms,
         harnesses: Iterable[str] = HARNESSES, module: str = "turns") -> list:
    """The doctor's `hook time` rows: per harness and event, p50 and p90 against
    `budget` times the floor. `floor` is called at most once, and only when an event
    has enough samples to judge."""
    from moxi.doctor import Row

    by: dict[str, dict[str, list[float]]] = {}
    for s in samples:
        by.setdefault(s.harness, {}).setdefault(s.event, []).append(s.ms)
    measured: list[float] = []

    def the_floor() -> float:
        if not measured:
            measured.append(float(floor() if callable(floor) else floor))
        return measured[0]

    out = []
    for harness in harnesses:
        events = by.get(harness)
        if not events:
            out.append(Row(module, f"hook time: {harness}", "ok",
                           "no hook times recorded in the last 24 hours"))
            continue
        for event in sorted(events):
            values = events[event]
            name = f"hook time: {harness} {event}"
            if len(values) < MIN_SAMPLES:
                out.append(Row(module, name, "ok",
                               f"too few samples ({len(values)}) to judge"))
                continue
            f = the_floor()
            note = ""
            if harness in ADDS_FLOOR:
                values = [v + f for v in values]
                note = ("; each sample adds the floor for the interpreter's start, "
                        "which the hook's own stamp does not see")
            p50, p90 = percentile(values, 50), percentile(values, 90)
            limit = budget * f
            detail = (f"p50 {p50:.1f} ms, p90 {p90:.1f} ms over {len(values)} samples; "
                      f"floor {f:.1f} ms, budget {budget:g} x floor = {limit:.1f} ms{note}")
            out.append(Row(module, name, "warn" if p90 > limit else "ok", detail))
    return out


__all__ = ["STORE", "FILE", "FLUSH_S", "WINDOW_S", "MIN_SAMPLES", "HARNESSES",
           "Recorder", "Sample", "store_path", "read", "percentile", "floor_ms", "rows"]
