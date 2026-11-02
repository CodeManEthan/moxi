"""The performance targets, timed over one builder-made corpus sized like a heavy user's
machine: 8,300 files and about 280,000 requests. A `slow` test: a plain pytest run leaves
them out; `pytest -m slow -s` runs them and shows what each measured.

The corpus is built once per module, under the run's tmp, with sessions' testing
builders: Claude main transcripts with their subagent files, a few sessions that repeat
another's opening requests (so claims have work to do), and Codex rollouts, some of them
imported, every request carrying its limits. Each file's mtime is its last line's time.
The first pass over it is made once too; each test starts from a copy of that index.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import time
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from moxi_sessions import api as sessions
from moxi_usage import index, limits, reports, store

pytestmark = pytest.mark.slow

ClaudeTranscript = sessions.testing.ClaudeTranscript
CodexRollout = sessions.testing.CodexRollout
usage = sessions.testing.usage
token_usage = sessions.testing.token_usage
rate_limits = sessions.testing.rate_limits
write_imports = sessions.testing.write_imports

HOUR = 3600.0
DAY = 86_400.0

#: The corpus: Claude main transcripts, their subagent files, Codex rollouts (of which
#: some are imported), and the Claude requests spread over the Claude files.
MAINS = 3_350
SUBAGENTS = 4_600
ROLLOUTS = 350
IMPORTED = 140
FILES = MAINS + SUBAGENTS + ROLLOUTS
CLAUDE_REQUESTS = 276_000
#: Main sessions that open by repeating the first requests of an earlier session.
REPEATS = 100
#: How far back the corpus reaches.
SPAN_DAYS = 100
#: The files a test grows, as a busy hour grows the sessions it touches.
GROWN = 13

CLAUDE_MODELS = ("claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5")
CODEX_MODELS = ("gpt-5-codex", "gpt-5")

#: A tool result as a coding session's tools return one: source text, with the quotes and
#: newlines JSON escapes.
CODE = ('def read(path):\n    """Read \\"one\\" file."""\n'
        '    with open(path) as f:\n        return {"text": f.read(), "path": path}\n') * 8


def median_ms(run: Callable[[], object], times: int) -> float:
    taken = []
    for _ in range(times):
        start = time.perf_counter()
        run()
        taken.append(time.perf_counter() - start)
    return statistics.median(taken) * 1000


def _split(total: int, parts: int, rnd: random.Random) -> list[int]:
    """`total` split into `parts` counts of at least 1, skewed as sessions are: most
    short, a few long."""
    weights = [rnd.expovariate(1.0) + 0.05 for _ in range(parts)]
    scale = (total - parts) / sum(weights)
    counts = [1 + int(w * scale) for w in weights]
    counts[-1] += total - sum(counts)
    return counts


def _claude_requests(t: ClaudeTranscript, n: int, rnd: random.Random, ids: Iterator[int],
                     model: str, repeat: list[tuple[str, str]] = ()) -> list[tuple[str, str]]:
    """Add `n` requests to `t`, each a prompt, a tool call and its result; the first ones
    reuse the ids in `repeat`. Returns the ids it used."""
    used = []
    for m in range(n):
        if m < len(repeat):
            request_id, message_id = repeat[m]
        else:
            serial = next(ids)
            request_id, message_id = f"req_{serial:08d}", f"msg_{serial:08d}"
        used.append((request_id, message_id))
        if m % 4 == 0:
            t.user("Look at the module and say what is left.")
        req = t.request(request_id=request_id, message_id=message_id, model=model,
                        usage_block=usage(input=rnd.randint(1, 20),
                                          cache_read=rnd.randint(5_000, 150_000),
                                          cache_write=rnd.randint(0, 4_000),
                                          output=rnd.randint(20, 2_000)))
        call = req.tool_use("Read", {"file_path": f"/work/project/src/m{m % 50}.py"})
        t.tool_result(call, CODE)
    return used


def _settle(path: Path, last: float) -> None:
    os.utime(path, (last, last))


@dataclass
class Corpus:
    """The built corpus and the index of its first pass."""

    root: Path
    now: float
    claude: Path
    codex: Path
    state: Path
    grow: list[ClaudeTranscript] = field(default_factory=list)
    files: int = 0
    requests: int = 0
    bytes: int = 0
    built_s: float = 0.0
    indexed_s: float = 0.0
    index_bytes: int = 0
    readings_bytes: int = 0

    def env(self, home: Path) -> dict[str, str]:
        """The fenced environment for a process over this corpus, its homes in `home`."""
        return {"PATH": os.environ.get("PATH", ""), "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local/share"),
                "XDG_STATE_HOME": str(home / ".local/state"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "CLAUDE_CONFIG_DIR": str(self.claude.parent),
                "CODEX_HOME": str(self.codex.parent),
                "MOXI_USAGE_HOME": str(self.state), "TMPDIR": str(home)}

    def copy_index(self, env: dict[str, str] | None = None) -> None:
        """Put this corpus's index and readings where `env` (the test's) keeps them."""
        to = store.index_path(env).parent
        to.mkdir(parents=True, exist_ok=True)
        for name in (store.INDEX_FILE, "readings.db"):
            shutil.copyfile(self._master / name, to / name)

    def adopt(self) -> None:
        """Take the test's index as the corpus's, after a test changed the corpus."""
        source = store.index_path().parent
        for name in (store.INDEX_FILE, "readings.db"):
            shutil.copyfile(source / name, self._master / name)

    @property
    def _master(self) -> Path:
        return store.index_path(self.env(self.root / "home")).parent


def build(root: Path, now: float) -> Corpus:
    rnd = random.Random(20261031)
    home = root / "home"
    claude = root / "harness" / ".claude" / "projects"
    codex = root / "harness" / ".codex" / "sessions"
    corpus = Corpus(root=root, now=now, claude=claude, codex=codex, state=root / "usage")
    ids = iter(range(1, 10**9))
    start = time.perf_counter()

    per_file = _split(CLAUDE_REQUESTS, MAINS + SUBAGENTS, rnd)
    owners = sorted(rnd.randrange(MAINS) for _ in range(SUBAGENTS))
    children: dict[int, int] = {}
    for owner in owners:
        children[owner] = children.get(owner, 0) + 1
    starts = sorted(now - rnd.uniform(2 * HOUR, SPAN_DAYS * DAY) for _ in range(MAINS))
    opened: list[list[tuple[str, str]]] = []
    counts = iter(per_file)
    for n in range(MAINS):
        model = rnd.choice(CLAUDE_MODELS)
        t = ClaudeTranscript(claude, session_id=f"0190b000-0000-7000-8000-{n:012d}",
                             project=f"-work-p{n % 60}", cwd=f"/work/p{n % 60}",
                             start=starts[n], step=float(rnd.randint(5, 40)))
        repeat = (opened[rnd.randrange(len(opened))][:10]
                  if n >= MAINS - REPEATS * 30 and n % 30 == 0 and opened else [])
        main_count = max(next(counts), len(repeat) + 1)
        opened.append(_claude_requests(t, main_count, rnd, ids, model, repeat))
        kids = []
        for k in range(children.get(n, 0)):
            child = t.subagent(f"a{n:08x}{k:08x}")
            _claude_requests(child, next(counts), rnd, ids, model)
            kids.append(child)
        t.write()
        _settle(t.path, t.clock - t.step)
        for child in kids:
            _settle(child.path, child.clock - child.step)
        if n >= MAINS - GROWN * 7 and n % 7 == 0 and len(corpus.grow) < GROWN:
            corpus.grow.append(t)
        corpus.files += 1 + len(kids)

    imported = []
    picked = set(rnd.sample(range(ROLLOUTS), IMPORTED))
    rollout_starts = sorted(now - rnd.uniform(2 * HOUR, SPAN_DAYS * DAY)
                            for _ in range(ROLLOUTS))
    for n, began in enumerate(rollout_starts):
        thread = f"0190c000-0000-7000-8000-{n:012d}"
        is_imported = n in picked
        r = CodexRollout(codex, thread_id=thread, start=began,
                         step=float(rnd.randint(5, 40)), imported=is_imported,
                         cwd=f"/work/p{n % 60}")
        model = rnd.choice(CODEX_MODELS)
        for _turn in range(rnd.randint(1, 5)):
            r.turn(model)
            r.user("Fix the failing test.")
            for _ in range(rnd.randint(1, 8)):
                five = rnd.uniform(0, 100)
                spent = token_usage(input=rnd.randint(2_000, 120_000),
                                    cached=rnd.randint(0, 1_900),
                                    output=rnd.randint(20, 3_000))
                windows = rate_limits(primary=(five, 300, r.clock + 2 * HOUR),
                                      secondary=(five / 4, 10_080, r.clock + 3 * DAY))
                r.request(spent, response_id=f"resp_{n:05d}_{next(ids):08d}", limits=windows)
            r.assistant("Done.")
            r.turn_end()
        r.write()
        _settle(r.path, r.clock - r.step)
        if is_imported:
            imported.append(thread)
        corpus.files += 1
    write_imports(codex, imported)
    corpus.built_s = time.perf_counter() - start
    corpus.bytes = sum(p.stat().st_size for p in root.joinpath("harness").rglob("*.jsonl"))
    return corpus


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Iterator[Corpus]:
    """The corpus and its first pass, made once for the module. This fixture runs outside
    the per-test fence, so it sets the environment itself, every home inside its tmp,
    before the pass runs."""
    root = tmp_path_factory.mktemp("bench")
    now = float(int(time.time() // HOUR) * HOUR)
    made = build(root, now)
    env = made.env(root / "home")
    with pytest.MonkeyPatch.context() as patch:
        for key in list(os.environ):
            if key not in env:
                patch.delenv(key)
        for key, value in env.items():
            patch.setenv(key, value)
        started = time.perf_counter()
        result = index.pass_(now=now, projects=None, workers=0, env=env)
        made.indexed_s = time.perf_counter() - started
        conn = store.connect(env=env, create=False)
        try:
            made.requests = conn.execute("SELECT count(*) FROM requests").fetchone()[0]
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        made.index_bytes = store.index_path(env).stat().st_size
        made.readings_bytes = (store.index_path(env).parent / "readings.db").stat().st_size
    assert (result.read, result.excluded, made.files) == (FILES - IMPORTED, IMPORTED, FILES)
    print(f"\ncorpus: {made.files} files, {made.requests} requests, "
          f"{made.bytes / 1e6:.0f} MB, built in {made.built_s:.1f} s; first pass "
          f"({result.workers} workers) {made.indexed_s:.1f} s; index.db "
          f"{made.index_bytes / 1e6:.1f} MB, readings.db {made.readings_bytes / 1e6:.1f} MB")
    yield made


@pytest.fixture
def bench(corpus, monkeypatch, fence) -> Corpus:
    """The test reads the corpus's harness roots and starts from a copy of its index."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(corpus.claude.parent))
    monkeypatch.setenv("CODEX_HOME", str(corpus.codex.parent))
    corpus.copy_index()
    return corpus


class Opens:
    """Counts the files this process opens under each of the corpus's roots while
    armed."""

    def __init__(self):
        self.roots: dict[str, int] = {}
        self.armed = False
        sys.addaudithook(self._hook)

    def _hook(self, event, args):
        if not self.armed or event != "open" or not args:
            return
        path = args[0]
        if isinstance(path, (str, bytes, os.PathLike)):
            text = os.fsdecode(path)
            for root in self.roots:
                if text.startswith(root):
                    self.roots[root] += 1


OPENS = Opens()


def _pass(corpus: Corpus, **kw) -> index.PassResult:
    kw.setdefault("now", corpus.now)
    kw.setdefault("projects", None)
    return index.pass_(**kw)


def test_the_index_of_the_corpus_and_its_size(bench):
    conn = store.connect(create=False)
    try:
        assert store.check(conn) == []
        files = conn.execute("SELECT count(*) FROM files").fetchone()[0]
        won = conn.execute("SELECT count(*) FROM requests WHERE won = 1").fetchone()[0]
    finally:
        conn.close()
    print(f"\nindex: {files} files, {bench.requests} requests ({won} claimed), "
          f"index.db {bench.index_bytes / 1e6:.1f} MB "
          f"({bench.index_bytes / bench.requests:.0f} bytes a request)")
    assert files == FILES


@pytest.mark.timed
def test_a_pass_with_nothing_changed_stats_each_source_and_takes_about_600_ms(bench):
    OPENS.roots = {str(bench.claude): 0, str(bench.codex): 0}
    OPENS.armed = True
    try:
        result = _pass(bench)
    finally:
        OPENS.armed = False
    assert (result.read, result.reused) == (0, FILES)
    opened = dict(OPENS.roots)
    taken = median_ms(lambda: _pass(bench), 5)
    print(f"\npass with nothing changed over {FILES} sources: {taken:.0f} ms; files "
          f"opened: claude {opened[str(bench.claude)]}, codex {opened[str(bench.codex)]}")
    assert opened[str(bench.claude)] == 0
    assert taken < 600


@pytest.mark.timed
def test_a_30_day_daily_over_the_stored_claims_takes_under_300_ms(bench):
    since = bench.now - 30 * DAY

    def daily():
        return reports.report("daily", since=since, fresh=False)

    first = daily()
    assert first.rows
    taken = median_ms(daily, 7)
    conn = store.connect(create=False)
    try:
        bare = median_ms(lambda: reports.daily(conn, since=since), 7)
        utc = median_ms(lambda: reports.daily(conn, since=since, tz="UTC"), 7)
        requests = conn.execute("SELECT count(*) FROM requests WHERE won = 1 AND ts >= ?",
                                (since,)).fetchone()[0]
    finally:
        conn.close()
    print(f"\n30-day daily over {requests} claimed requests: {taken:.0f} ms through "
          f"report(); {bare:.0f} ms on an open connection, {utc:.0f} ms in UTC")
    assert taken < 300


@pytest.mark.timed
def test_the_claim_step_after_a_pass_that_read_13_files_takes_under_50_ms(
        bench, monkeypatch):
    rnd = random.Random(13)
    ids = iter(range(900_000_000, 10**10))
    for t in bench.grow:
        _claude_requests(t, 3, rnd, ids, CLAUDE_MODELS[0])
        t.flush()
    spent: list[tuple[float, int]] = []
    claim = store.claim

    def timed(conn, keys):
        keys = list(keys)
        start = time.perf_counter()
        changed = claim(conn, keys)
        spent.append((time.perf_counter() - start, len(keys)))
        return changed

    with monkeypatch.context() as patch:
        patch.setattr(store, "claim", timed)
        started = time.perf_counter()
        result = _pass(bench)
        whole = time.perf_counter() - started
    bench.adopt()
    assert (result.read, result.reused) == (GROWN, FILES - GROWN)
    conn = store.connect(create=False)
    try:
        assert store.check(conn) == []
    finally:
        conn.close()
    taken = sum(s for s, _ in spent) * 1000
    keys = sum(k for _, k in spent)
    print(f"\nclaim step after a pass that read {result.read} files: {taken:.1f} ms over "
          f"{keys} keys in {len(spent)} transactions (slowest {max(spent)[0] * 1000:.1f} "
          f"ms); the whole pass {whole * 1000:.0f} ms")
    assert taken < 50


@pytest.mark.timed
@pytest.mark.skipif((os.cpu_count() or 1) < 4, reason="a rebuild's target needs 4 cores")
@pytest.mark.xfail(strict=True, reason="a known miss: the workers only read, and this "
                   "process writes every row and runs the claim over every key alone, so "
                   "those writes bound how fast more workers can make a rebuild")
def test_a_rebuild_on_4_workers_takes_under_a_quarter_of_one_workers(bench, monkeypatch):
    parent: dict[str, float] = {}

    def timing(name):
        real = getattr(store, name)

        def timed(*args, **kw):
            start = time.perf_counter()
            try:
                return real(*args, **kw)
            finally:
                parent[name] = parent.get(name, 0.0) + time.perf_counter() - start
        monkeypatch.setattr(store, name, timed)

    for name in ("upsert_file", "update_file", "upsert_requests", "delete_requests",
                 "claim_all"):
        timing(name)
    taken: dict[int, float] = {}
    spent: dict[int, dict[str, float]] = {}
    for workers in (1, 4):
        parent.clear()
        start = time.perf_counter()
        result = _pass(bench, rebuild=True, workers=workers)
        taken[workers] = time.perf_counter() - start
        spent[workers] = dict(parent)
        assert (result.read, result.excluded) == (FILES - IMPORTED, IMPORTED)
    conn = store.connect(create=False)
    try:
        assert store.check(conn) == []
    finally:
        conn.close()
    for workers in (1, 4):
        parts = ", ".join(f"{k} {v:.1f} s" for k, v in sorted(spent[workers].items()))
        print(f"\nrebuild on {workers} worker(s): {taken[workers]:.1f} s; in this "
              f"process: {parts}", end="")
    print(f"\nratio 4/1: {taken[4] / taken[1]:.2f}")
    assert taken[4] < taken[1] / 4


@pytest.mark.timed
def test_limits_with_no_pull_over_the_corpus_takes_under_100_ms(bench):
    first = time.perf_counter()
    got = limits.limits(now=bench.now, pull=False)
    first = (time.perf_counter() - first) * 1000
    assert any(row.harness == "codex" and row.state == "fresh" for row in got.rows)
    taken = median_ms(lambda: limits.limits(now=bench.now, pull=False), 7)
    print(f"\nlimits() with no pull: {taken:.1f} ms (the first call {first:.1f} ms)")
    assert taken < 100


#: turns' guard loader, which is the one importer of usage's guard declarations.
TURNS_GUARDS = "moxi_turns.guards"
GUARDS_FRESH = pytest.mark.xfail(
    strict=True, reason="a known miss: the declarations are turns' own Guard values, so "
    "importing them imports turns' guard module, which loads logging, dataclasses, re "
    "and tomllib (about 20-35 ms here); in turns' endpoint, the one importer, that is "
    "loaded already, and the next test holds the declarations alone to the budget")


def import_ms(module: str, cwd: Path, *, before: str = "", times: int = 15) -> float:
    """The median cost of importing `module` in a fresh interpreter, less the median of
    the same interpreter without it; `before` is imported first in both."""
    def run(code: str) -> float:
        start = time.perf_counter()
        subprocess.run([sys.executable, "-c", code], cwd=cwd, check=True)
        return time.perf_counter() - start

    first = f"import {before}; " if before else ""
    bare, loaded = [], []
    for _ in range(times):
        bare.append(run(first + "pass"))
        loaded.append(run(f"{first}import {module}"))
    return (statistics.median(loaded) - statistics.median(bare)) * 1000


@pytest.mark.timed
@pytest.mark.parametrize(("module", "budget"), [
    ("moxi_usage.listeners", 10.0), ("moxi_usage.mark", 10.0),
    ("moxi_usage.ceiling", 10.0),
    pytest.param("moxi_usage.guards", 10.0, marks=GUARDS_FRESH),
    ("moxi_usage.api", 15.0)])
def test_importing_a_hook_path_module_takes_under_its_budget(module, budget, tmp_path):
    taken = import_ms(module, tmp_path)
    print(f"\nimport {module}: {taken:.1f} ms over a bare interpreter")
    assert taken < budget


@pytest.mark.timed
def test_importing_the_guard_declarations_beside_turns_loader_takes_under_10_ms(tmp_path):
    turns = import_ms(TURNS_GUARDS, tmp_path)
    taken = import_ms("moxi_usage.guards", tmp_path, before=f"{TURNS_GUARDS}, moxi.modules")
    print(f"\nimport moxi_usage.guards: {taken:.1f} ms over an interpreter that holds "
          f"turns' guard loader and the core's modules (the loader alone {turns:.1f} ms)")
    assert taken < 10.0


# --- the hook path: the warn-at listener and the worker ceiling's judge -----------------
#
# Each is timed as turns calls it, one event or call at a time, over a long session as
# the hook path meets it: a main transcript of about 35 MB with an agent's file beside it,
# the measure and the model rows in the test's tmp. The time is the function's own work;
# turns' round trip to the endpoint is not in it.

HOOK_SESSION = "0190b000-0000-7000-8000-0000000005c1"
HOOK_AGENT = "a0190b00000005c1"
HOOK_MODEL = "claude-opus-5-5"
#: The measure's row for the model, and the price rows that give its `r` (0.05).
HOOK_PARAMS = {"schema": 1, "measured_at": "2026-10-26T00:00:00+00:00", "w": 2.0,
               "models": {HOOK_MODEL: {
                   "main": {"n": 50, "S": 47058, "P": 23171, "g": 1476},
                   "subagent": {"n": 50, "S": 47058, "P": 23171, "g": 1476,
                                "fallback": "main"}}}}
HOOK_PRICES = f"""
[[model]]
id = "{HOOK_MODEL}"
input = 10.0
output = 50.0
cache_read = 0.5
"""
#: The 1x and 1.5x thresholds those rows give.
ONE_X, ONE_HALF_X = 100_808, 187_776
#: The long session: its requests, each with a tool result of about 11 KB.
HOOK_REQUESTS = 1_200
HOOK_RESULT = CODE * 13
#: The agent's requests, with the same results (about 6 MB).
AGENT_REQUESTS = 200
#: The ceiling's thresholds, in tokens of an agent's growth.
CEILING_WARN, CEILING_HARD = 100_000, 150_000
#: Calls per timed batch, and batches; a measure is the median batch's mean.
CALLS, BATCHES = 200, 7
OFF_CALLS = 20_000


def per_call_ms(run: Callable[[], object], calls: int = CALLS,
                batches: int = BATCHES) -> float:
    means = []
    for _ in range(batches):
        start = time.perf_counter()
        for _ in range(calls):
            run()
        means.append((time.perf_counter() - start) / calls)
    return statistics.median(means) * 1000


class LongSession:
    """A Claude main transcript of `HOOK_REQUESTS` requests whose context climbs to
    `last`, with one agent whose context climbs from `agent_base` to `agent_last`."""

    def __init__(self, *, last: int, agent_base: int = 20_000, agent_last: int = 60_000,
                 tail: str = HOOK_RESULT):
        self.t = ClaudeTranscript(Path(sessions.root("claude")), session_id=HOOK_SESSION,
                                  start=1793400000.0, step=5.0)
        self._requests(self.t, HOOK_REQUESTS, 20_000, last, tail)
        self.child = self.t.subagent(HOOK_AGENT)
        self._requests(self.child, AGENT_REQUESTS, agent_base, agent_last)
        self.t.write()
        self.bytes = self.t.path.stat().st_size
        self.agent_bytes = self.child.path.stat().st_size

    @staticmethod
    def _requests(t: ClaudeTranscript, n: int, first: int, last: int,
                  tail: str = HOOK_RESULT) -> None:
        for m in range(n):
            if m % 4 == 0:
                t.user("Look at the module and say what is left.")
            context = first + (last - first) * m // max(1, n - 1)
            req = t.request(model=HOOK_MODEL,
                            usage_block=usage(input=3, cache_read=context - 1_003,
                                              cache_write=1_000, output=400))
            call = req.tool_use("Read", {"file_path": f"/work/project/src/m{m % 50}.py"})
            t.tool_result(call, tail if m == n - 1 else HOOK_RESULT)

    def event(self, *, env: dict[str, str], agent_id: str | None = None):
        return types.SimpleNamespace(
            event="after-tool", harness="claude", session_id=HOOK_SESSION, env=env,
            agent_id=agent_id, tool="Read", tool_use_id="toolu_1", is_error=False,
            point="after", record_path=str(self.t.path), cwd="/work/project")

    def call(self, tool: str = "Grep", tool_input: dict | None = None):
        return types.SimpleNamespace(
            harness="claude", session_id=HOOK_SESSION, agent_id=HOOK_AGENT, tool=tool,
            tool_use_id="toolu_1", input=tool_input or {}, cwd="/work/project",
            record_path=str(self.t.path), env={}, prompts=())


@pytest.fixture
def hook(monkeypatch):
    """The measure, the price rows and usage's config in the test's tmp; the listener's
    and the judge's state empty; no crossing handlers."""
    from moxi_usage import ceiling, mark, thresholds

    from conftest import write_config

    def config(extra: str = "") -> None:
        write_config(HOOK_PRICES + extra)

    mark.reset()
    ceiling.reset()
    config()
    path = thresholds.params_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(HOOK_PARAMS), encoding="utf-8")
    monkeypatch.setattr(mark, "handlers", lambda: [])
    yield config
    mark.reset()
    ceiling.reset()


@pytest.mark.timed
def test_the_warn_at_listener_takes_under_1_ms_a_call_when_marked(hook):
    from moxi_usage import mark, thresholds

    below = LongSession(last=90_000)
    marked = {mark.WARN_AT: "1x", mark.CROSSING_AT: "1.5x"}
    main, agent = below.event(env=marked), below.event(env=marked, agent_id=HOOK_AGENT)
    assert mark.on_tool(main) is None and mark.on_tool(agent) is None
    armed = per_call_ms(lambda: mark.on_tool(main))
    agents = per_call_ms(lambda: mark.on_tool(agent))
    floor = per_call_ms(lambda: mark.floor(main))
    assert mark.floor(main) == ONE_X
    # Where a call's time goes: the newest request, the thresholds' stamp, the selection.
    source = sessions.Source("claude", str(below.t.path), HOOK_SESSION)
    read = per_call_ms(lambda: sessions.last_request(source))
    stamp = per_call_ms(thresholds.stamp)
    select = per_call_ms(lambda: mark.selected(marked))

    mark.reset()
    past = LongSession(last=160_000)
    main = past.event(env=marked)
    assert mark.on_tool(main) == mark.notice_line(160_000, "1x", ONE_X, HOOK_MODEL)
    assert mark.on_tool(main) is None                    # fired: said once per rise
    fired = per_call_ms(lambda: mark.on_tool(main))
    fired_floor = per_call_ms(lambda: mark.floor(main))
    assert mark.floor(main) == 0
    print(f"\nwarn-at listener over a {below.bytes / 1e6:.0f} MB transcript, both marks "
          f"stamped: {armed:.3f} ms a call below the mark, {fired:.3f} ms past it "
          f"(fired); an agent's call over its {below.agent_bytes / 1e6:.0f} MB file "
          f"{agents:.3f} ms; floor {floor:.3f} ms below, {fired_floor:.4f} ms fired. "
          f"Of a call: last_request {read:.3f} ms, the thresholds' stamp {stamp:.3f} ms, "
          f"the selection {select:.4f} ms")
    assert max(armed, fired, agents, floor, fired_floor) < 1.0


#: A large tool result: on a heavy user's machine about one in 160 is over 100 KB.
LARGE_RESULT_KB = 150


@pytest.mark.timed
@pytest.mark.xfail(strict=True, reason="a known miss: sessions' last_request decodes "
                   "every line after the newest request, the tool's result among them, "
                   "at about 11-16 microseconds a KB, so a result of about 70 KB or more "
                   "takes the call past 1 ms")
def test_the_warn_at_listener_takes_under_1_ms_a_call_after_a_large_tool_result(hook):
    from moxi_usage import mark

    large = CODE * (LARGE_RESULT_KB * 1024 // len(CODE))
    s = LongSession(last=90_000, tail=large)
    main = s.event(env={mark.WARN_AT: "1x"})
    assert mark.on_tool(main) is None
    taken = per_call_ms(lambda: mark.on_tool(main))
    source = sessions.Source("claude", str(s.t.path), HOOK_SESSION)
    read = per_call_ms(lambda: sessions.last_request(source))
    print(f"\nwarn-at listener after a {len(large) // 1024} KB tool result: {taken:.3f} ms "
          f"a call, of which last_request {read:.3f} ms")
    assert taken < 1.0


@pytest.mark.timed
def test_the_warn_at_listener_with_both_purposes_off_takes_about_10_microseconds(hook):
    from moxi_usage import mark

    s = LongSession(last=160_000, agent_base=20_000, agent_last=20_000)
    unstamped, stamped_off = s.event(env={}), s.event(env={mark.WARN_AT: "off"})
    agent = s.event(env={}, agent_id=HOOK_AGENT)
    for e in (unstamped, stamped_off, agent):
        assert mark.on_tool(e) is None
    assert mark.floor(unstamped) == mark.floor(stamped_off) == mark.NEVER
    times = {name: per_call_ms(run, OFF_CALLS) for name, run in (
        ("unstamped", lambda: mark.on_tool(unstamped)),
        ("stamped off", lambda: mark.on_tool(stamped_off)),
        ("an agent's", lambda: mark.on_tool(agent)),
        ("floor", lambda: mark.floor(unstamped)))}
    assert mark._sessions == {}
    print("\nwarn-at listener with both purposes off, per call: " + ", ".join(
        f"{name} {ms * 1000:.1f} us" for name, ms in times.items()))
    assert max(times.values()) < 0.01


@pytest.mark.timed
def test_the_ceiling_judge_takes_under_2_ms_a_call_below_the_band_and_past_the_hard_mark(
        hook, tmp_path):
    from moxi_usage import ceiling

    handoff = tmp_path / "handoff"
    handoff.mkdir()
    hook(f"\n[ceiling]\nwarn_tokens = {CEILING_WARN}\nhard_tokens = {CEILING_HARD}\n"
         f"handoff_roots = [{json.dumps(str(handoff))}]\n")
    below = LongSession(last=90_000, agent_base=20_000, agent_last=50_000)
    call = below.call()
    assert ceiling.judge(call) is None
    under = per_call_ms(lambda: ceiling.judge(call))
    returning = below.call("StructuredOutput")
    returns = per_call_ms(lambda: ceiling.judge(returning))

    ceiling.reset()
    past = LongSession(last=90_000, agent_base=20_000, agent_last=20_000 + CEILING_HARD)
    refused, report = past.call(), past.call(
        "Write", {"file_path": str(handoff / "report.md"), "content": "done"})
    first = ceiling.judge(refused)
    assert type(first).__name__ == "Deny", first
    assert ceiling.judge(report) is None
    denied = per_call_ms(lambda: ceiling.judge(refused))
    allowed = per_call_ms(lambda: ceiling.judge(report))
    print(f"\nceiling judge over a {below.agent_bytes / 1e6:.0f} MB agent file: "
          f"{under:.3f} ms a call below the band, {returns:.4f} ms for a return tool; "
          f"past the hard mark {denied:.3f} ms a refused call, {allowed:.3f} ms a "
          f"hand-off write the allowlist lets through")
    assert under < 2.0
    assert max(denied, allowed) < 2.0          # BUDGETS' hook_ms
