"""The endpoint's guard runner: the shared matching cases, each harness's table, the judges'
lanes, deadlines and breaks, the doctor's records, and `/ask/guard` and the `guards` line
on the Claude leg."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import moxi_turns
from claude_runner import FakeRunner, pipe_pair, post
from conftest import write_config
from moxi_turns import claude_leg as leg
from moxi_turns import protocol
from moxi_turns.claude_endpoint import ClaudeHarness
from moxi_turns.endpoint import Endpoint
from moxi_turns.guards import (Allow, Deny, Found, Guard, Loaded, On, Problem, Prompt,
                               ToolCall, regex_problem)
from moxi_turns.guards.run import (GUARD_WORKERS, STILL_JUDGING, Answer, GuardRunner,
                                   break_deny, break_line, decide, entry_for, load_line)
from moxi_turns.listeners import Registry

CASES_PATH = (Path(moxi_turns.__file__).parent / "claude" / "moxi-turns" / "tests"
              / "guard-cases.json")
CASES = json.loads(CASES_PATH.read_text())
DECLARED = {d["id"]: d for d in CASES["guards"]}
ORDER = [d["id"] for d in CASES["guards"]]


def _never(call):                        # the case table's judge target; never called
    raise AssertionError("the case table's judges are never called")


def case_found(d: dict) -> Found:
    g = Guard(name=d["id"].split(".", 1)[1],
              calls={h: tuple(p) for h, p in d["calls"].items()}, who=d["who"],
              match=d["match"], match_flags=d["match_flags"],
              judge="test_guard_run:_never" if d["judge"] else None, deny=d["deny"],
              wants=tuple(d["wants"]), on_error=d["on_error"], deadline_ms=d["deadline_ms"])
    return Found(d["id"], g, "module", module="t")


def leg_entry(d: dict, harness: str, max_wait: int) -> dict | None:
    """The case table's own rule for a harness's table entry."""
    if harness not in d["calls"]:
        return None
    out = {k: d[k] for k in ("id", "who", "match", "match_flags", "judge", "deny", "wants",
                             "on_error")}
    return {**out, "calls": d["calls"][harness],
            "deadline_ms": min(d["deadline_ms"], max_wait)}


# -- the shared cases --------------------------------------------------------------------

def test_the_case_table_is_plain_json_with_what_both_matchers_need():
    assert isinstance(CASES["max_wait_ms"], int)
    assert len(set(ORDER)) == len(ORDER)
    for c in CASES["cases"]:
        assert set(c) >= {"name", "harness", "tool", "agent", "input", "on", "covering",
                          "decision"}
        assert c["decision"] in ("next", "deny", "ask")
        assert set(c["on"]) <= set(ORDER) and set(c["covering"]) <= set(c["on"])
        assert ("deny" in c) == (c["decision"] == "deny")
        assert ("ask" in c) == (c["decision"] == "ask")
    names = [c["name"] for c in CASES["cases"]]
    assert len(set(names)) == len(names)


def test_the_typescript_copy_of_the_case_table_holds_the_same_value():
    # The runner's tests can't read files, so they import a .ts copy of the table.
    text = CASES_PATH.with_suffix(".ts").read_text()
    head = "\nexport default "
    assert head in text, "guard-cases.ts must hold a line `export default ` then the JSON"
    assert json.loads(text[text.index(head) + len(head):]) == CASES


def test_every_guard_of_the_case_table_is_sound_and_its_regexes_are_in_the_subset():
    for d in CASES["guards"]:
        f = case_found(d)
        assert f.guard.problems("module") == [], d["id"]
        for rx in (d["match"] or {}).values():
            assert regex_problem(rx) is None, rx
        # Codex names are canonical only: no alias the loader would refuse.
        assert not set(d["calls"].get("codex", ())) & {"Agent", "Write", "Edit"}


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_the_runner_builds_each_harness_table_as_the_case_table_says(harness):
    founds = [case_found(d) for d in CASES["guards"]]
    runner = GuardRunner(harness, settings=lambda: ([], CASES["max_wait_ms"]),
                         load=lambda on: Loaded(on=tuple(On(f, ("list",)) for f in founds)),
                         record=lambda *a: None, clear=lambda k: None)
    try:
        want = [e for e in (leg_entry(d, harness, CASES["max_wait_ms"])
                            for d in CASES["guards"]) if e is not None]
        assert runner.reload() == want
        assert runner.table() == want
        assert json.loads(json.dumps(want)) == want
    finally:
        runner.close()


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["name"])
def test_a_shared_matching_case(case):
    on = [i for i in ORDER if i in case["on"]]
    entries = [e for e in (entry_for(On(case_found(DECLARED[i]), ("list",)), case["harness"],
                                     CASES["max_wait_ms"]) for i in on) if e is not None]
    got = decide(entries, case["tool"], case["agent"], case["input"])
    assert list(got.covering) == case["covering"]
    assert got.verdict == case["decision"]
    if case["decision"] == "deny":
        assert got.deny == case["deny"]
    if case["decision"] == "ask":
        assert list(got.ask) == case["ask"]


# -- a rig of judges -----------------------------------------------------------------------

def judged(name: str, fn, *, calls=("Bash",), **kw) -> Found:
    """A guard whose judge is `fn`, resolved in its file's namespace."""
    g = Guard(name=name, calls={"claude": tuple(calls)}, judge=":j", **kw)
    return Found(f"t.{name}", g, "py", path="/g/t.py", space=SimpleNamespace(j=fn))


def data_only(name: str, deny: str, *, calls=("Bash",)) -> Found:
    return Found(f"t.{name}", Guard(name=name, calls={"claude": tuple(calls)}, deny=deny),
                 "py", path="/g/t.py")


class Rig:
    def __init__(self, *founds: Found, max_wait: int = 3000, problems=()):
        self.founds = list(founds)
        self.max_wait = max_wait
        self.problems = tuple(problems)
        self.records: list[tuple] = []
        self.runner = GuardRunner(
            "claude", settings=lambda: ([], self.max_wait),
            load=lambda on: Loaded(on=tuple(On(f, ("list",)) for f in self.founds),
                                   problems=self.problems),
            record=lambda text, key, fix: self.records.append(("record", text, key, fix)),
            clear=lambda key: self.records.append(("clear", key)))
        self.runner.reload()

    def call(self, *, tool="Bash", agent=None, input=None, prompts=()) -> ToolCall:
        return ToolCall("claude", "s-1", agent, tool, "tu-1",
                        {"command": "x"} if input is None else input, "/w", None,
                        {"HOME": "/h"}, tuple(prompts))

    def ask(self, *ids: str, **kw) -> Answer:
        return self.runner.ask(self.call(**kw), list(ids) or [f.id for f in self.founds])

    def written(self) -> list[tuple]:
        assert self.runner.flush(5)
        return list(self.records)


@pytest.fixture
def rig():
    made: list[Rig] = []
    gates: list[threading.Event] = []

    def make(*founds, **kw) -> Rig:
        r = Rig(*founds, **kw)
        made.append(r)
        return r

    make.gate = lambda: gates.append(threading.Event()) or gates[-1]
    yield make
    for g in gates:
        g.set()
    for r in made:
        r.runner.close()


def asks_at_once(r: Rig, n: int, **kw) -> tuple[list[Answer], list[float]]:
    answers: list = [None] * n
    took: list = [None] * n
    start = threading.Barrier(n)

    def one(i: int) -> None:
        start.wait()
        t0 = time.monotonic()
        answers[i] = r.ask(**kw)
        took[i] = time.monotonic() - t0

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return answers, took


def guard_threads(guard_id: str) -> int:
    return sum(1 for t in threading.enumerate() if t.name == f"turns-guard-{guard_id}")


# -- answers -----------------------------------------------------------------------------

def test_allow_with_a_line_none_and_deny(rig):
    r = rig(judged("a", lambda c: Allow("mind the size")), judged("n", lambda c: None),
            judged("e", lambda c: Allow()))
    assert r.ask() == Answer(None, ("mind the size",), (), ())
    r = rig(judged("d", lambda c: Deny("not here")), judged("a", lambda c: Allow("x")))
    got = r.ask()
    assert got.deny == "not here"
    assert got.to_leg() == {"deny": "not here", "lines": ["x"], "broken": []}


def test_a_data_only_guard_asked_by_id_refuses_with_its_text(rig):
    r = rig(data_only("no", "no shell here"))
    assert r.ask().deny == "no shell here"


def test_an_id_not_on_the_table_is_skipped(rig):
    r = rig(judged("a", lambda c: Deny("x")))
    assert r.ask("t.gone") == Answer()


def test_only_a_guard_that_wants_prompts_gets_them(rig):
    seen = {}
    r = rig(judged("p", lambda c: seen.__setitem__("p", c.prompts), wants=("prompts",)),
            judged("q", lambda c: seen.__setitem__("q", c.prompts)))
    prompts = (Prompt("composer", "may I ask?"),)
    r.ask(prompts=prompts)
    assert seen == {"p": prompts, "q": ()}


# -- breaks ------------------------------------------------------------------------------

class Boom(Exception):
    pass


def _raise(call):
    raise Boom("judge fell over")


@pytest.mark.parametrize("fn, why", [
    (_raise, "it raised Boom: judge fell over"),
    (lambda c: 5, "it answered int, not None, Allow or Deny"),
    (lambda c: "deny", "it answered str, not None, Allow or Deny"),
    (lambda c: Allow(3), "it answered Allow with a line that is int, not text"),
    (lambda c: Deny(""), "it answered Deny with no reason"),
], ids=["raise", "int", "str", "allow-int", "deny-empty"])
def test_a_break_lets_the_call_through_with_the_agents_line_and_a_record(rig, fn, why):
    r = rig(judged("b", fn))
    got = r.ask()
    assert got.deny is None and got.lines == ()
    assert got.broken == (break_line("t.b", why),)
    assert got.broken[0] == f"[turns] guard t.b could not check this call ({why}); it ran unchecked."
    assert [(b.id, b.why, b.denied) for b in got.breaks] == [("t.b", why, False)]
    assert r.written() == [("record", f"guard t.b could not check a Bash call on claude "
                            f"({why}); the call ran unchecked", "guard:t.b",
                            "moxi config set turns guards.run --remove /g/t.py")]


def test_on_error_deny_refuses_with_the_guard_and_the_reason(rig):
    r = rig(judged("b", _raise, on_error="deny"))
    got = r.ask()
    why = "it raised Boom: judge fell over"
    assert got.deny == break_deny("t.b", why)
    assert got.deny == f"turns could not check this call with guard t.b ({why})"
    assert got.broken == ()
    assert r.written()[0][1].endswith("the call was refused")


def test_an_overrun_breaks_at_the_deadline(rig):
    gate = rig.gate()
    r = rig(judged("slow", lambda c: gate.wait(5), deadline_ms=80))
    t0 = time.monotonic()
    got = r.ask()
    took = time.monotonic() - t0
    assert got.broken == (break_line("t.slow", "it took longer than 80 ms"),)
    assert 0.07 <= took < 0.5


def test_the_deadline_is_clamped_to_max_wait(rig):
    gate = rig.gate()
    r = rig(judged("slow", lambda c: gate.wait(5), deadline_ms=2000), max_wait=60)
    assert r.runner.table()[0]["deadline_ms"] == 60
    t0 = time.monotonic()
    assert r.ask().broken == (break_line("t.slow", "it took longer than 60 ms"),)
    assert time.monotonic() - t0 < 0.5


def test_an_unloadable_module_breaks_every_call_and_is_named_once_at_the_next_ready(rig):
    g = Guard(name="gone", calls={"claude": ("Bash",)}, judge="no_such_guard_module_x:fn")
    r = rig(Found("t.gone", g, "module", module="t"))
    got = r.ask()
    assert len(got.breaks) == 1
    why = got.breaks[0].why
    assert why.startswith("its module did not load: ModuleNotFoundError")
    assert r.ask().breaks[0].why == why          # dead for this table
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not r.runner._notes:
        time.sleep(0.01)
    assert r.runner.take_notes() == [load_line("t.gone", why)]
    r.runner.reload()
    time.sleep(0.1)
    assert r.runner.take_notes() == []              # once per reason


def test_other_guards_covering_the_call_are_unaffected(rig):
    r = rig(judged("b", _raise), judged("a", lambda c: Allow("line")))
    got = r.ask()
    assert got.deny is None and got.lines == ("line",)
    assert got.broken == (break_line("t.b", "it raised Boom: judge fell over"),)
    r = rig(judged("b", _raise), judged("d", lambda c: Deny("no")))
    assert r.ask().deny == "no"


def test_the_record_stands_until_the_next_clean_judge_clears_it(rig):
    state = {"fail": True}

    def fn(call):
        if state["fail"]:
            raise Boom("x")
        return None

    r = rig(judged("f", fn))
    r.ask()
    state["fail"] = False
    r.ask()
    r.ask()                                        # clean again: no second clear
    state["fail"] = True
    r.ask()
    r.ask()                                        # a newer record replaces the older
    state["fail"] = False
    r.ask()
    kinds = [(w[0], w[2] if w[0] == "record" else w[1]) for w in r.written()]
    assert kinds == [("record", "guard:t.f"), ("clear", "guard:t.f"),
                     ("record", "guard:t.f"), ("record", "guard:t.f"),
                     ("clear", "guard:t.f")]


def test_the_first_clean_judge_clears_a_record_an_earlier_endpoint_left(rig):
    r = rig(judged("c", lambda c: None))
    r.ask()
    r.ask()
    assert r.written() == [("clear", "guard:t.c")]


def test_records_reach_the_doctor(turns_env):
    from moxi import doctor, paths

    state = {"fail": True}

    def fn(call):
        if state["fail"]:
            raise Boom("broken")

    f = judged("real", fn)
    runner = GuardRunner("claude", settings=lambda: ([], 3000),
                         load=lambda on: Loaded(on=(On(f, ("list",)),)))
    try:
        runner.reload()
        runner.ask(ToolCall("claude", "s", None, "Bash", input={"command": "x"}), ["t.real"])
        assert runner.flush(5)
        path = paths._layout("turns").state_dir() / doctor.RECORDS_FILE
        assert str(path).startswith(os.environ["MOXI_TURNS_STATE_HOME"])
        records = json.loads(path.read_text())
        assert "it raised Boom: broken" in records["guard:t.real"]["text"]
        assert records["guard:t.real"]["fix"] == "moxi config set turns guards.run --remove /g/t.py"
        state["fail"] = False
        runner.ask(ToolCall("claude", "s", None, "Bash", input={"command": "x"}), ["t.real"])
        assert runner.flush(5)
        assert "guard:t.real" not in json.loads(path.read_text())
    finally:
        runner.close()


# -- lanes and bounds --------------------------------------------------------------------

def test_four_hung_judges_then_a_new_call_breaks_at_once(rig):
    gate = rig.gate()
    r = rig(judged("h", lambda c: gate.wait(10) and None, deadline_ms=300))
    answers, took = asks_at_once(r, GUARD_WORKERS)
    assert all(a.broken == (break_line("t.h", "it took longer than 300 ms"),)
               for a in answers)
    assert guard_threads("t.h") == GUARD_WORKERS
    t0 = time.monotonic()
    got = r.ask()
    assert time.monotonic() - t0 < 0.15             # not its 300 ms
    assert got.broken == (break_line("t.h", STILL_JUDGING),)
    # A new table keeps the guard's lane: no new threads for a hung guard.
    r.runner.reload()
    t0 = time.monotonic()
    assert r.ask().broken == (break_line("t.h", STILL_JUDGING),)
    assert time.monotonic() - t0 < 0.15
    assert guard_threads("t.h") == GUARD_WORKERS
    gate.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and r.ask().broken:
        time.sleep(0.02)
    assert r.ask().broken == ()                    # the threads freed; judged again


def test_a_call_whose_deadline_passes_while_it_waits_for_a_thread_breaks(rig):
    gate = rig.gate()
    r = rig(judged("w", lambda c: gate.wait(10) and None, deadline_ms=2000))
    held: list = []
    threads = [threading.Thread(target=lambda: held.append(r.ask())) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.1)
    # A shorter deadline from a new table: the four running judges are not hung, so the
    # fifth call waits, and breaks when its own deadline passes.
    r.max_wait = 150
    r.runner.reload()
    t0 = time.monotonic()
    got = r.ask()
    took = time.monotonic() - t0
    assert got.broken == (break_line("t.w", STILL_JUDGING),)
    assert 0.13 <= took < 0.6
    gate.set()
    for t in threads:
        t.join(5)
    assert [a.broken for a in held] == [()] * 4    # each judged, none broken


def test_a_waiting_call_is_judged_when_a_thread_frees_in_time(rig):
    r = rig(judged("q", lambda c: time.sleep(0.1), deadline_ms=1000))
    answers, took = asks_at_once(r, GUARD_WORKERS + 1)
    assert [a.breaks for a in answers] == [()] * 5
    assert max(took) >= 0.18                       # the fifth waited for a thread


# -- the ceiling's surface ---------------------------------------------------------------

def ceiling(fn, **kw) -> Found:
    g = Guard(name="ceiling", calls={"claude": ("*",), "codex": ("*",)}, who="agents",
              judge=":j", **kw)
    return Found("t.ceiling", g, "py", path="/g/c.py", space=SimpleNamespace(j=fn))


def test_the_ceilings_declaration_warns_then_refuses(rig):
    count = {"n": 0}

    def judge(call):
        count["n"] += 1
        assert call.agent_id == "a-1" and call.harness == "claude"
        return Allow("you are near your ceiling") if count["n"] == 1 else Deny("over the ceiling")

    r = rig(ceiling(judge))
    entries = r.runner.entries
    assert decide(entries, "Read", None, {}).verdict == "next"
    assert decide(entries, "Read", "a-1", {}).ask == ("t.ceiling",)
    assert r.ask(tool="Read", agent="a-1").lines == ("you are near your ceiling",)
    assert r.ask(tool="Read", agent="a-1").deny == "over the ceiling"


@pytest.mark.parametrize("n", [4, 8])
def test_parallel_subagent_calls_are_each_judged_inside_the_deadline(rig, n):
    live = {"now": 0, "most": 0}
    lock = threading.Lock()

    def judge(call):
        with lock:
            live["now"] += 1
            live["most"] = max(live["most"], live["now"])
        time.sleep(0.05)
        with lock:
            live["now"] -= 1
        return Allow(f"seen {call.agent_id}")

    r = rig(ceiling(judge))
    answers, took = asks_at_once(r, n, tool="Bash", agent="a-1")
    assert [a.breaks for a in answers] == [()] * n
    assert all(a.lines == ("seen a-1",) for a in answers)
    assert max(took) < 0.5                          # each inside its 500 ms
    assert live["most"] <= GUARD_WORKERS


# -- the table from the loader -----------------------------------------------------------

def test_a_guard_on_the_list_that_does_not_load_is_named_once(rig):
    problems = (Problem("t.bad", "shape", "who 'x' must be one of any, main, agents", on=True),
                Problem("t.off", "shape", "deny must be text", on=False),
                Problem("t.sw", "switch", "switch t a.b reads off: no such key", on=True),
                Problem("nope.x", "missing", "module nope offers no guards", on=True))
    r = rig(problems=problems)
    assert r.runner.take_notes() == [
        load_line("t.bad", "who 'x' must be one of any, main, agents"),
        load_line("nope.x", "module nope offers no guards")]
    r.runner.reload()
    assert r.runner.take_notes() == []
    assert load_line("t.bad", "x") == \
        "[turns] guard t.bad could not load (x); the calls it covers run unchecked."


def test_the_table_comes_from_turns_config_and_custom_files(turns_env, tmp_path):
    toml = tmp_path / "mine.toml"
    toml.write_text('[[guard]]\nname = "no-push"\ncalls = { claude = ["Bash"] }\n'
                    'match = { command = "git push" }\ndeny = "no pushes"\n'
                    'deadline_ms = 900\n')
    py = tmp_path / "judged.py"
    py.write_text("from moxi_turns.guards import Guard, Deny\n"
                  "def j(call):\n    return Deny('judged ' + call.input['command'])\n"
                  "GUARDS = (Guard(name='j', calls={'claude': ('Bash',), 'codex': ('Bash',)},"
                  " judge=':j', deadline_ms=900),)\n")
    write_config(turns_env, f'[guards]\nrun = ["{toml}", "{py}"]\nmax_wait_ms = 250\n')
    runner = GuardRunner("claude", env=dict(os.environ))
    try:
        table = runner.reload()
        assert [(e["id"], e["calls"], e["judge"], e["deadline_ms"]) for e in table] == [
            ("file:mine.no-push", ["Bash"], False, 250), ("file:judged.j", ["Bash"], True, 250)]
        got = runner.ask(ToolCall("claude", "s", None, "Bash", input={"command": "ls"}),
                         ["file:judged.j"])
        assert got.deny == "judged ls"
    finally:
        runner.close()


# -- on the Claude leg -------------------------------------------------------------------

@pytest.fixture
def leg_rig(turns_env, rig):
    made = []

    def make(*founds, problems=(), env=None):
        r = rig(*founds, problems=problems)
        rd, w = pipe_pair()
        harness = ClaudeHarness(w, pid=os.getpid(), cwd="/work", guards=r.runner)
        ep = Endpoint(harness, registry=Registry(), env=env)
        ep.prepare()
        harness.serve(str(ep.dir / protocol.MOD_SOCK))
        runner = FakeRunner(rd)
        harness.first_line()
        runner.wait_first()
        runner.session("s-1")
        ep.open()
        made.append((ep, w))
        return ep, runner, r

    yield make
    for ep, w in made:
        ep.stop("the test ended")
        w.close()


def test_the_hello_carries_claudes_table_and_the_leg_is_3(leg_rig):
    ep, runner, r = leg_rig(judged("a", lambda c: None, match={"command": "rm"}),
                            data_only("b", "no", calls=("Agent",)))
    assert leg.LEG == 3 and runner.first["leg"] == 3
    assert leg.PATH_GUARD == "/ask/guard" and leg.PATH_GUARD in leg.PATHS
    assert runner.first["guards"] == [
        {"id": "t.a", "calls": ["Bash"], "who": "any", "match": {"command": "rm"},
         "match_flags": "", "judge": True, "deny": "", "wants": [], "on_error": "allow",
         "deadline_ms": 500},
        {"id": "t.b", "calls": ["Agent"], "who": "any", "match": None, "match_flags": "",
         "judge": False, "deny": "no", "wants": [], "on_error": "allow", "deadline_ms": 500}]


def test_each_ready_sends_the_table_again_and_names_a_load_failure_once(leg_rig):
    problem = Problem("t.bad", "load", "the file does not load: OSError: gone", on=True)
    ep, runner, r = leg_rig(judged("a", lambda c: None), problems=(problem,))
    # Read at the first line, and said at the first ready.
    answer = runner.post(leg.PATH_CONTEXT, {"event": "ready", "source": "startup",
                                            "session_id": "s-1"})
    assert runner.tables.get(timeout=2) == r.runner.table()
    assert answer["context"] == load_line("t.bad", problem.reason)
    r.founds.append(data_only("new", "no"))
    answer = runner.post(leg.PATH_CONTEXT, {"event": "ready", "source": "clear",
                                            "session_id": "s-2"})
    assert [e["id"] for e in runner.tables.get(timeout=2)] == ["t.a", "t.new"]
    assert answer["context"] is None


def test_ask_guard_builds_the_call_from_the_leg_and_the_endpoint(leg_rig):
    seen = {}

    def judge(call):
        seen["call"] = call
        return Allow("checked")

    ep, runner, r = leg_rig(judged("a", judge, wants=("prompts",)),
                            env={**os.environ, "MOXI_GUARD_TEST": "1"})
    answer = runner.post(leg.PATH_GUARD, {
        "call": {"tool": "Bash", "tool_use_id": "tu-9", "agentId": "a-4",
                 "input": {"command": "rm -rf x"},
                 "prompts": [{"origin": "composer", "text": "go"}, {"text": "bare"}, 5]},
        "guards": ["t.a"]})
    assert answer == {"ok": True, "lines": ["checked"], "broken": []}
    c = seen["call"]
    assert (c.harness, c.session_id, c.agent_id, c.tool, c.tool_use_id) == \
        ("claude", "s-1", "a-4", "Bash", "tu-9")
    assert c.input == {"command": "rm -rf x"} and c.cwd == "/work"
    assert c.env["MOXI_GUARD_TEST"] == "1"
    assert c.prompts == (Prompt("composer", "go"), Prompt(None, "bare"))
    answer = runner.post(leg.PATH_GUARD, {"call": {"tool": "Bash", "input": {}},
                                          "guards": ["t.a"]})
    assert seen["call"].agent_id is None and seen["call"].tool_use_id is None


@pytest.mark.parametrize(("given", "want"), [
    ("/work/moved", "/work/moved"),
    ("/", "/"),
    (None, "/work"),
    ("", "/work"),
    ("moved", "/work"),
    ("./moved", "/work"),
    (7, "/work"),
    (["/x"], "/work"),
], ids=["absolute", "root", "none", "empty", "relative", "dot-relative", "number", "list"])
def test_ask_guard_takes_an_absolute_cwd_from_the_ask_else_the_launch_directory(
        leg_rig, given, want):
    seen = {}

    def judge(call):
        seen["cwd"] = call.cwd

    ep, runner, r = leg_rig(judged("a", judge))
    call = {"tool": "Bash", "input": {"command": "x"}}
    if given is not None:
        call["cwd"] = given
    answer = runner.post(leg.PATH_GUARD, {"call": call, "guards": ["t.a"]})
    assert answer == {"ok": True, "lines": [], "broken": []}
    assert seen["cwd"] == want


@pytest.mark.parametrize("body", [
    {"guards": ["t.a"]},
    {"call": {"tool": ""}, "guards": ["t.a"]},
    {"call": {"tool": "Bash", "input": "rm"}, "guards": ["t.a"]},
    {"call": {"tool": "Bash"}},
    {"call": {"tool": "Bash"}, "guards": ["t.a", 3]},
], ids=["no-call", "no-tool", "input-not-object", "no-guards", "bad-id"])
def test_a_malformed_ask_is_400(leg_rig, body):
    ep, runner, r = leg_rig(judged("a", lambda c: None))
    status, answer = post(runner.mod_sock, leg.PATH_GUARD, body)
    assert status == 400 and answer["ok"] is False


def test_an_ask_holds_its_request_thread_no_longer_than_its_deadline(leg_rig, rig):
    gate = rig.gate()
    ep, runner, r = leg_rig(judged("h", lambda c: gate.wait(10), deadline_ms=300))
    body = {"call": {"tool": "Bash", "input": {"command": "x"}}, "guards": ["t.h"]}
    took = []
    answers = []

    def one():
        t0 = time.monotonic()
        answers.append(runner.post(leg.PATH_GUARD, body))
        took.append(time.monotonic() - t0)

    threads = [threading.Thread(target=one) for _ in range(GUARD_WORKERS + 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    # Every ask, the four judged and the two that waited, answers inside the deadline
    # plus the trip; each lets the call through with its line.
    assert len(answers) == GUARD_WORKERS + 2
    assert max(took) < 0.3 + 0.4
    reasons = sorted(a["broken"][0] for a in answers)
    assert reasons.count(break_line("t.h", "it took longer than 300 ms")) == GUARD_WORKERS
    assert reasons.count(break_line("t.h", STILL_JUDGING)) == 2
    t0 = time.monotonic()
    again = runner.post(leg.PATH_GUARD, body)       # every thread hung: at once
    assert time.monotonic() - t0 < 0.2
    assert again["broken"] == [break_line("t.h", STILL_JUDGING)]
