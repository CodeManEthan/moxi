"""The performance targets, timed over builder-made files. A `slow` test: a plain pytest
run leaves them out; `pytest -m slow` runs them. Each is also `timed`, so `moxi-dev check`
runs them alone, after its parallel pass.

Each target is a median over several runs, and each test prints what it measured, so
`pytest -m slow -s` shows the numbers.
"""

from __future__ import annotations

import builtins
import io
import json
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from moxi_sessions.find import sources
from moxi_sessions.read import last_request, records_since
from moxi_sessions.source import Source
from moxi_sessions.testing import ClaudeTranscript, OpenCodeDb
from moxi_sessions.testing.claude import usage

pytestmark = [pytest.mark.slow, pytest.mark.timed]

MB = 1_000_000


def median_ms(run: Callable[[], object], times: int) -> float:
    taken = []
    for _ in range(times):
        start = time.perf_counter()
        run()
        taken.append(time.perf_counter() - start)
    return statistics.median(taken) * 1000


#: Tool output as a coding session's tools return it: source text, with the quotes,
#: backslashes and newlines that JSON escapes.
CODE = ('def read(path):\n    """Read \\"one\\" file."""\n'
        '    with open(path) as f:\n        return {"text": f.read(), "path": path}\n')

#: The sizes of one turn's tool results, in bytes, cycled.
RESULT_SIZES = (9_000, 40_000, 120_000, 2_000, 60_000, 15_000, 110_000, 4_000)


def long_transcript(root: Path, size: int = 35 * MB) -> ClaudeTranscript:
    """A transcript of about `size` bytes, made like a long Claude Code session's: nearly
    all of its bytes are tool results in user lines; each request writes a thought and a
    tool call (two lines with the same usage); each turn ends with a text answer and a
    `turn_duration`, with a few small lines of other kinds between. The last tool result
    of a turn is a small one: `last_request` decodes the line its backward read stops at,
    the newest line that names a turn, and a tool result is one."""
    t = ClaudeTranscript(root)
    written = 0
    while written < size:
        t.user("Where do things stand? Look at the module and say what is left.")
        t.other("attachment", attachment={"type": "todo", "items": []})
        for n, result in enumerate(RESULT_SIZES):
            req = t.request(usage_block=usage(input=4, cache_read=90_000 + n,
                                              cache_write=1_500, output=300))
            req.thinking("Which file holds it? " * 20)
            call = req.tool_use("Read", {"file_path": f"/work/project/src/m{n}.py"})
            t.tool_result(call, (CODE * (result // len(CODE) + 1))[:result])
            written += len(t.lines[-1]) + 1
        t.other("queue-operation", operation="enqueue", content="and the tests?")
        t.assistant("It reads every file it is given. " * 30)
        t.turn_duration()
    t.write()
    return t


@pytest.fixture(scope="module")
def big(tmp_path_factory) -> ClaudeTranscript:
    return long_transcript(tmp_path_factory.mktemp("bench") / "projects")


def source_of(t: ClaudeTranscript) -> Source:
    return Source("claude", str(t.path), t.session_id)


def test_last_request_on_a_35_mb_transcript_is_under_1_ms(big):
    src = source_of(big)
    assert big.path.stat().st_size >= 35 * MB
    assert last_request(src).request is not None
    taken = median_ms(lambda: last_request(src), 41)
    print(f"\nlast_request: {taken:.3f} ms median over {big.path.stat().st_size / MB:.1f} MB")
    assert taken < 1.0


def test_records_since_with_nothing_new_is_under_1_ms(big):
    src = source_of(big)
    cursor = records_since(src, kinds={"request"}).cursor
    batch = records_since(src, cursor)
    assert batch.records == () and batch.header is None and batch.cursor == cursor
    taken = median_ms(lambda: records_since(src, cursor), 41)
    print(f"\nrecords_since with nothing new: {taken:.3f} ms median")
    assert taken < 1.0


def bare_pass(path: Path) -> None:
    """The yardstick: a forward pass that decodes only the lines holding `"usage"`."""
    with open(path, "rb") as handle:
        for line in handle:
            if b'"usage"' in line:
                json.loads(line)


def test_a_request_read_from_the_start_is_within_1_5_times_a_bare_pass(big):
    src = source_of(big)
    requests = records_since(src, kinds={"request"}).records
    turns = sum('"turn_duration"' in line for line in big.lines)
    assert len(requests) == turns * (len(RESULT_SIZES) + 1)
    bare, read = [], []
    for _ in range(7):                       # interleaved, so a busy machine hits both
        bare.append(median_ms(lambda: bare_pass(big.path), 1))
        read.append(median_ms(lambda: records_since(src, kinds={"request"}), 1))
    bare_ms, read_ms = statistics.median(bare), statistics.median(read)
    print(f"\nbare pass {bare_ms:.1f} ms, records_since(kinds={{'request'}}) {read_ms:.1f} ms,"
          f" {read_ms / bare_ms:.2f}x")
    assert read_ms <= 1.5 * bare_ms


@pytest.fixture(scope="module")
def opencode_db(tmp_path_factory) -> Path:
    """An OpenCode database of a little over 3 MB: sessions of finished steps, text and
    tool calls."""
    db = OpenCodeDb(tmp_path_factory.mktemp("bench-opencode"))
    for _ in range(24):
        s = db.session()
        for _ in range(20):
            s.user("Read the module and say what is left.")
            a = s.assistant()
            a.reasoning("Which file holds it? " * 10)
            a.tool("read", {"filePath": "/work/project/src/m.py"},
                   output=CODE * 30)
            a.step(input=900, output=120, text="It reads every file it is given. " * 10)
            a.complete()
    return db.write()


def test_opencode_no_change_check_is_under_1_ms_and_a_whole_read_under_50_ms(opencode_db):
    wal = Path(f"{opencode_db}-wal")
    size = opencode_db.stat().st_size + (wal.stat().st_size if wal.exists() else 0)
    assert size >= 3 * MB
    src = Source("opencode", str(opencode_db), None)
    whole = records_since(src)
    assert whole.records and not whole.reset
    taken_whole = median_ms(lambda: records_since(src), 7)
    cursor = records_since(src).cursor
    again = records_since(src, cursor)
    assert again.records == () and again.header is None       # nothing was read
    taken_check = median_ms(lambda: records_since(src, cursor), 41)
    print(f"\nOpenCode {size / MB:.1f} MB: whole read {taken_whole:.1f} ms, "
          f"no-change check {taken_check:.3f} ms")
    assert taken_check < 1.0
    assert taken_whole < 50.0


SOURCES = 8_300


@pytest.fixture(scope="module")
def many(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("bench-many") / "projects"
    for n in range(SOURCES):
        t = ClaudeTranscript(root, session_id=f"0190b000-0000-7000-8000-{n:012d}",
                             project=f"-work-project-{n % 60}")
        t.user("go")
        t.assistant("done")
        t.write()
    return root


def test_a_usage_pass_with_no_changed_source_is_one_stat_per_source(many, monkeypatch):
    seen = {(e.source.path, e.size, e.mtime_ns)
            for e in sources("claude", root=many, heads=False)}
    assert len(seen) == SOURCES

    def no_open(*args, **kwargs):
        raise AssertionError(f"a file was opened: {args[0]!r}")

    def usage_pass() -> int:
        listing = sources("claude", root=many, heads=False)
        assert listing.ok
        return sum((e.source.path, e.size, e.mtime_ns) not in seen for e in listing)

    with monkeypatch.context() as patched:
        patched.setattr(builtins, "open", no_open)
        patched.setattr(io, "open", no_open)
        assert usage_pass() == 0
        taken = median_ms(usage_pass, 5)
    print(f"\nusage pass over {SOURCES} unchanged sources: {taken:.0f} ms")
    assert taken < 600


def test_importing_the_api_takes_under_15_ms(tmp_path):
    def run(code: str) -> float:
        start = time.perf_counter()
        subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True)
        return time.perf_counter() - start

    bare, api = [], []
    for _ in range(15):
        bare.append(run("pass"))
        api.append(run("import moxi_sessions.api"))
    taken = (statistics.median(api) - statistics.median(bare)) * 1000
    print(f"\nimport moxi_sessions.api: {taken:.1f} ms over a bare interpreter")
    assert taken < 15.0
