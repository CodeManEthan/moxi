"""The restart-point measure over the token index: the sample, the populations, the
interactive-only rule, the inherited spec, each model's own `w`, the subagent fallback and
the file it writes. Transcripts come from sessions' builders; `now` is an argument."""

import json
from pathlib import Path

import pytest

from moxi_sessions import api as sessions
from moxi_usage import cli, index, measure, store, thresholds

from conftest import write_config

ClaudeTranscript = sessions.testing.ClaudeTranscript
CodexRollout = sessions.testing.CodexRollout
OpenCodeDb = sessions.testing.OpenCodeDb
usage = sessions.testing.usage

DAY = 86400.0
NOW = 1793400000.0
MODEL = "claude-model-x"
#: Each test session's requests: context 10,000 + 1,000 per request, the cache read the
#: previous request's context (the first's 5,000), every write for an hour.
BASE, STEP, PREFIX = 10_000, 1_000, 5_000
CODEX_MODEL = "codex-model-x"
OPENCODE_MODEL = "opencode-model-x"
#: The test models' price rows: the Claude model priced like Anthropic's (5-minute writes
#: 1.25x input, 1-hour writes 2x), the Codex and OpenCode ones with no write price.
PRICES = f"""
[[model]]
id = "{MODEL}"
input = 4.0
output = 20.0
cache_read = 0.4
cache_write_5m = 5.0
cache_write_1h = 8.0

[[model]]
id = "{CODEX_MODEL}"
input = 1.25
output = 10.0
cache_read = 0.125

[[model]]
id = "{OPENCODE_MODEL}"
input = 2.0
output = 8.0
cache_read = 0.2
cache_write_5m = 3.0
"""


@pytest.fixture(autouse=True)
def priced():
    write_config(PRICES)


def context_of(n):
    return BASE + STEP * n


def session(n, *, calls=6, entrypoint="cli", model=MODEL, start=NOW - DAY, root=None,
            agents=0, agent_calls=6, synthetic=False, sidechain=False):
    """One Claude session with `calls` requests, and `agents` subagent files."""
    t = ClaudeTranscript(Path(root or sessions.root("claude")),
                         session_id=f"0190b000-0000-7000-8000-{n:012d}",
                         project=f"-work-p{n % 3}", entrypoint=entrypoint, start=start)
    for k in range(1, calls + 1):
        read = PREFIX if k == 1 else context_of(k - 1)
        write = context_of(k) - read - 2
        t.user("go")
        t.assistant("ok", model=model, request_id=f"req_{n}_{k}",
                    usage=usage(input=2, cache_read=read, cache_write=write,
                                cache_write_1h=write, output=100))
        if synthetic and k == 2:
            t.synthetic()
    for a in range(agents):
        child = t.subagent(f"a{n:08d}{a:08d}")
        for k in range(1, agent_calls + 1):
            read = PREFIX if k == 1 else 2 * context_of(k - 1)
            child.user("look")
            child.assistant("found", model=model, request_id=f"req_{n}_{a}_{k}",
                            usage=usage(input=2, cache_read=read,
                                        cache_write=2 * context_of(k) - read - 2,
                                        cache_write_1h=0, output=50))
    t.write()
    return t


def build(now=NOW):
    index.pass_(now=now, projects=None, readings=None, workers=1)
    return store.connect()


def measured(now=NOW, **rules):
    conn = build(now)
    try:
        return measure.measure(conn, now=now, rules=measure.Rules(**rules))
    finally:
        conn.close()


def test_the_inherited_spec_over_twenty_interactive_sessions():
    for n in range(20):
        session(n)
    found = measured()
    row = found["models"][MODEL]["main"]
    assert row == {"n": 20, "S": float(context_of(4)), "P": float(PREFIX), "g": float(STEP),
                   "out": 100.0, "w": 2.0, "write_share_1h": 1.0}
    assert "w" not in found and found["no_w"] == {}
    assert found["sample"]["files"] == 20 and found["sample"]["requests"] == 120
    assert found["schema"] == 1 and found["days"] == 21
    assert found["measured_at"] == "2026-10-30T22:40:00+00:00"


def test_a_population_under_twenty_files_gets_no_row_and_is_named_thin():
    for n in range(19):
        session(n)
    found = measured()
    assert found["models"] == {}
    assert found["thin"] == {MODEL: {"main": 19}}


def test_headless_sessions_leave_main_unless_widened():
    for n in range(20):
        session(n, entrypoint="sdk-cli")
    narrow = measured()
    assert narrow["models"] == {} and narrow["sample"]["skipped"]["not_interactive"] == 20
    conn = store.connect()
    try:
        wide = measure.measure(conn, now=NOW, rules=measure.Rules(interactive_only=False))
    finally:
        conn.close()
    assert wide["models"][MODEL]["main"]["n"] == 20


def test_short_files_old_files_synthetic_and_sidechain_requests_are_left_out():
    for n in range(20):
        session(n, synthetic=True)                 # a synthetic request is dropped
    session(30, calls=4)                           # under 5 requests
    session(31, start=NOW - 30 * DAY)              # its last request is past 21 days
    found = measured()
    assert found["models"][MODEL]["main"]["n"] == 20
    assert found["models"][MODEL]["main"]["S"] == context_of(4)
    assert found["sample"]["skipped"]["short"] == 1
    assert found["sample"]["files"] == 20


def test_subagents_get_their_own_row_and_the_write_share_is_over_each_population():
    for n in range(20):
        session(n, agents=1)
    found = measured()
    rows = found["models"][MODEL]
    assert rows["subagent"]["n"] == 20 and "fallback" not in rows["subagent"]
    assert rows["subagent"]["S"] == 2 * context_of(4)
    assert rows["subagent"]["g"] == 2 * STEP
    # The main sessions write for an hour, their agents for five minutes.
    assert (rows["main"]["write_share_1h"], rows["main"]["w"]) == (1.0, 2.0)
    assert (rows["subagent"]["write_share_1h"], rows["subagent"]["w"]) == (0.0, 1.25)


def test_a_subagent_population_without_a_row_carries_the_main_row():
    for n in range(20):
        session(n, agents=1 if n < 3 else 0)
    found = measured()
    rows = found["models"][MODEL]
    assert rows["subagent"] == {**rows["main"], "fallback": "main"}
    assert found["thin"] == {MODEL: {"subagent": 3}}


def test_a_pair_after_a_compaction_or_an_expired_cache_is_no_growth():
    for n in range(20):
        t = ClaudeTranscript(Path(sessions.root("claude")),
                             session_id=f"0190b000-0000-7000-8000-{n:012d}",
                             start=NOW - DAY)
        # contexts 10k, 11k, 12k, 13k, then 5k (a compaction), then 6k with a cold cache
        for k, (context, read) in enumerate(((10_000, 5_000), (11_000, 10_000),
                                             (12_000, 11_000), (13_000, 12_000),
                                             (5_000, 4_000), (6_000, 100))):
            t.user("go")
            t.assistant("ok", request_id=f"req_{n}_{k}", usage=usage(input=2, cache_read=read,
                                          cache_write=context - read - 2,
                                          cache_write_1h=context - read - 2))
        t.write()
    row = measured()["models"][MODEL]["main"]
    assert row["g"] == 1000.0 and row["S"] == 13_000.0


def test_codex_counts_as_interactive_by_its_cli_source_or_a_listed_originator():
    assert measure.interactive("codex", "cli", "codex_exec")
    assert measure.interactive("codex", "vscode", "codex-tui")
    assert measure.interactive("codex", "vscode", "Codex Desktop")
    assert not measure.interactive("codex", "vscode", "codex_exec")
    assert not measure.interactive("codex", "exec", "codex-tui", originators=())
    assert measure.interactive("claude", "cli", None)
    assert not measure.interactive("claude", "sdk-cli", None)
    assert not measure.interactive("opencode", None, None)


def test_the_rules_come_from_the_config():
    write_config("[restart_point]\ndays = 7\ninteractive_only = false\n"
                 'interactive_originators = ["mine"]\n')
    from moxi_usage import settings

    rules = measure.Rules.from_settings(settings.current())
    assert rules == measure.Rules(days=7, interactive_only=False,
                                  interactive_originators=("mine",))


# --- the run, the file and the verbs -----------------------------------------------------

def test_a_run_writes_the_file_the_thresholds_read(monkeypatch):
    monkeypatch.setattr(measure, "MIN_FILES", 2)
    for n in range(2):
        session(n)
    write_config('[[model]]\nid = "claude-model-x"\ninput = 10.0\noutput = 50.0\n'
                 'cache_read = 1.0\n')
    done = measure.run(now=NOW)
    path = thresholds.params_path()
    assert done.path == str(path) and path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == done.doc
    assert "over 2 files and 12 requests" in done.line()
    found = thresholds.threshold(MODEL, "1x")
    assert found.available and found.tokens > context_of(4)
    point = measure.restart_point()
    assert [(m, p) for m, p, _, _ in point.rows] == [(MODEL, "main"), (MODEL, "subagent")]
    assert point.as_dict()["rows"][1]["fallback"] == "main"
    assert point.measured_at == NOW


def test_with_no_index_and_no_pass_nothing_is_written():
    with pytest.raises(measure.MeasureError, match="no token index"):
        measure.run(now=NOW, fresh=False)
    assert not thresholds.params_path().exists()


def test_the_verb_measures_then_prints_rows_and_marks(monkeypatch, capsys):
    monkeypatch.setattr(measure, "MIN_FILES", 2)
    for n in range(2):
        session(n)
    write_config('[[model]]\nid = "claude-model-x"\ninput = 10.0\noutput = 50.0\n'
                 'cache_read = 1.0\n')
    assert cli.main(["restart-point", "--measure"]) == 0
    out = capsys.readouterr().out
    assert "restart point measured over 2 files" in out
    assert f"{MODEL}" in out and "1x " in out and "its main row" in out
    assert cli.main(["restart-point", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["measured"] and found["rows"][0]["marks"]["2x"]["tokens"] > 0


def test_the_verb_without_a_measure_says_so_and_exits_1(capsys):
    assert cli.main(["restart-point"]) == 1
    assert "has not been measured" in capsys.readouterr().out


def test_the_timers_command_runs_one_measure(monkeypatch, capsys):
    monkeypatch.setattr(measure, "MIN_FILES", 1)
    session(1)
    assert measure.main([]) == 0
    assert "restart point measured over 1 files" in capsys.readouterr().out
    assert thresholds.params_path().exists()


def test_the_timer_is_declared_behind_a_switch_the_core_accepts():
    from moxi import modules
    from moxi.units import switch_problem
    from moxi_usage import module, settings

    found = modules.load("usage")
    assert found.ok, found.reason
    [(name, entry)] = module.TIMERS.items()
    assert name == "measure" and entry["command"] == "moxi-usage-measure"
    assert switch_problem(settings.schema(), entry["switch"]) is None
    assert settings.current()[entry["switch"]] is False
    from importlib.metadata import entry_points

    [script] = [ep for ep in entry_points(group="console_scripts")
                if ep.name == "moxi-usage-measure"]
    assert script.value == "moxi_usage.measure:main"


def rollout(n, *, model=CODEX_MODEL):
    """One Codex rollout from the TUI with 5 requests."""
    r = CodexRollout(Path(sessions.root("codex")),
                     thread_id=f"0190c000-0000-7000-8000-{n:012d}",
                     originator="codex-tui", source="vscode", start=NOW - DAY)
    r.turn(model)
    for k in range(1, 6):
        r.user("go")
        r.request(sessions.testing.token_usage(input=context_of(k),
                                               cached=PREFIX if k == 1
                                               else context_of(k - 1)),
                  response_id=f"resp_{n}_{k}")
    r.turn_end()
    r.write()


def test_a_codex_rollout_from_the_tui_joins_main_with_its_own_w():
    """In a sample mixed with Claude sessions, the Codex model takes its own `w` (no
    write price, so 1.0), never the Claude model's 2.0."""
    for n in range(20):
        session(n)
    for n in range(20):
        rollout(n)
    found = measured()
    rows = found["models"][CODEX_MODEL]
    assert rows["main"]["n"] == 20 and rows["main"]["g"] == STEP
    assert rows["main"]["w"] == 1.0 and rows["main"]["write_share_1h"] is None
    assert found["models"][MODEL]["main"]["w"] == 2.0


def test_a_codex_only_sample_gets_its_rows_and_marks():
    for n in range(20):
        rollout(n)
    found = measured()
    row = found["models"][CODEX_MODEL]["main"]
    assert row["n"] == 20 and row["w"] == 1.0
    from moxi_usage import models

    mark = thresholds.threshold(CODEX_MODEL, "1x", doc=found, table=models.table())
    assert mark.available, mark.reason


def test_an_opencode_only_sample_gets_its_rows_when_main_is_widened():
    db = OpenCodeDb(Path(sessions.root("opencode")).parent,
                    name=Path(sessions.root("opencode")).name, start=NOW - DAY)
    for n in range(20):
        each = db.session(title=f"s{n}")
        for k in range(1, 6):
            each.user("go")
            reply = each.assistant(model=("opencode", OPENCODE_MODEL))
            read = PREFIX if k == 1 else context_of(k - 1)
            reply.step(input=context_of(k) - read, cache_read=read, output=10)
            reply.complete()
    db.write()
    found = measured(interactive_only=False)
    row = found["models"][OPENCODE_MODEL]["main"]
    assert row["n"] == 20 and row["w"] == 1.5                # its 5-minute price over input
    assert row["g"] == STEP


def test_a_model_without_an_input_price_or_one_of_0_has_no_row_and_is_named():
    from moxi_usage import models

    from conftest import doc

    for n in range(20):
        rollout(n)
    for n in range(20, 40):
        rollout(n, model="free-model-x")
    conn = build()
    try:
        found = measure.measure(conn, now=NOW, table=models.Table(doc({
            CODEX_MODEL: {"output": 10.0, "cache_write_5m": 1.0},
            "free-model-x": {"input": 0.0, "output": 0.0}})))
    finally:
        conn.close()
    assert found["models"] == {}
    assert "no input price" in found["no_w"][CODEX_MODEL]
    assert "not above 0" in found["no_w"]["free-model-x"]
    run = measure.Run(found, "here")
    assert "2 models without an input price" in run.line()
