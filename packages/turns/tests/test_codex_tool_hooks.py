"""The Codex host's tool hooks: the matcher, the trust entries, PreToolUse guards and
PostToolUse after-tool rows with the floor, and the subagent prompt rule. Everything runs
against the fake app-server client and turns' own ping; no Codex is started."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import threading
import time
import tomllib
from pathlib import Path

import pytest

import moxi_turns
from conftest import end_endpoint, write_config
from moxi_turns import codex_hooks, listeners
from moxi_turns.codex import CodexHarness
from moxi_turns.endpoint import Endpoint
from moxi_turns.guards import Allow, Deny, Found, Guard, Loaded, On, Prompt
from moxi_turns.guards.run import GuardRunner, break_deny, break_line, glob_regex
from test_codex_host import FakeAppServer, FakeClient

CASES = json.loads((Path(moxi_turns.__file__).parent / "claude" / "moxi-turns" / "tests"
                    / "guard-cases.json").read_text())
ME = __name__


# -- judges and listeners the tests name -------------------------------------------------

CALLS: list = []


def judge_allow(call):
    CALLS.append(call)
    return Allow(f"judged {call.tool}")


def judge_quiet(call):
    CALLS.append(call)
    return None


def judge_deny(call):
    return Deny("no pushes from here")


def judge_raise(call):
    raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _calls():
    CALLS.clear()
    yield
    CALLS.clear()


def found(gid: str, calls: dict, *, judge: str | None = None, deny: str = "",
          who: str = "any", match=None, wants=(), on_error: str = "allow") -> Found:
    g = Guard(name=gid.split(".", 1)[1], calls={h: tuple(p) for h, p in calls.items()},
              who=who, match=match, judge=f"{ME}:{judge}" if judge else None, deny=deny,
              wants=tuple(wants), on_error=on_error)
    return Found(gid, g, "module", module="t")


def runner(*founds: Found, max_wait: int = 3000) -> GuardRunner:
    held = list(founds)
    r = GuardRunner("codex", settings=lambda: ([], max_wait),
                    load=lambda on: Loaded(on=tuple(On(f, ("list",)) for f in held)),
                    record=lambda *a: None, clear=lambda k: None)
    r.held = held                 # a test may swap what the next reload loads
    return r


# -- the host on fakes -------------------------------------------------------------------

class ArgvAppServer(FakeAppServer):
    def __init__(self, socket_path, **kwargs):
        super().__init__(socket_path, **kwargs)
        self.argv = list(kwargs.get("argv") or [])


class PathClient(FakeClient):
    """The fake client, whose load answer names the thread's rollout."""

    def call(self, method, params=None, timeout=None):
        result = super().call(method, params, timeout)
        if method in ("thread/start", "thread/resume"):
            result["thread"]["path"] = "/codex/sessions/rollout-main.jsonl"
        return result


def host(turns_env, *, guards=None, registry=None, client=PathClient):
    harness = None
    made = []

    def clients(**kwargs):
        c = client(harness, **kwargs)
        made.append(c)
        return c

    harness = CodexHarness(turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
                           client_factory=clients, app_server_factory=ArgvAppServer,
                           guards=guards)
    endpoint = Endpoint(harness, session_id=None,
                        registry=registry or listeners.Registry()).open()
    harness.start()
    return endpoint, harness, made[0]


def ping(monkeypatch, harness, event, data, *extra):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert codex_hooks.main(["--socket", str(harness.hook_server.socket_path), event,
                             *extra]) == 0
    text = out.getvalue()
    return json.loads(text) if text.strip() else None


def timed_count(harness, event: str, expected: int, timeout: float = 5.0) -> int:
    """How many `event` waits the hook server has recorded, once it has `expected` of them
    or `timeout` passes. The server records a hook's wait after it sends the answer, so
    the last one can land a moment after the ping returns."""
    def count() -> int:
        return [s["event"] for s in harness.hook_server.times._samples].count(event)

    deadline = time.monotonic() + timeout
    while count() < expected and time.monotonic() < deadline:
        time.sleep(0.01)
    return count()


def pre(tool, tool_input=None, **extra):
    return {"session_id": "thread-1", "turn_id": "turn-1", "hook_event_name": "PreToolUse",
            "tool_name": tool, "tool_input": tool_input or {}, "tool_use_id": "exec-1",
            "cwd": "/work", "transcript_path": "/codex/sessions/rollout-main.jsonl",
            **extra}


def post(tool, **extra):
    return {"session_id": "thread-1", "turn_id": "turn-1", "hook_event_name": "PostToolUse",
            "tool_name": tool, "tool_input": {}, "tool_use_id": "exec-1",
            "tool_response": "ok", "cwd": "/work",
            "transcript_path": "/codex/sessions/rollout-main.jsonl", **extra}


def context_of(output) -> str | None:
    if output is None:
        return None
    return output["hookSpecificOutput"].get("additionalContext")


def denied(output) -> str | None:
    if output is None:
        return None
    spec = output["hookSpecificOutput"]
    return spec.get("permissionDecisionReason") if spec.get("permissionDecision") == "deny" \
        else None


def hook_groups(argv) -> dict:
    """The `-c hooks.<Event>=...` groups and the one `hooks.state` table in an argv."""
    groups, states = {}, []
    for flag, value in zip(argv, argv[1:]):
        if flag != "-c" or not value.startswith("hooks."):
            continue
        key, _, rest = value.partition("=")
        parsed = tomllib.loads(f"v = {rest}")["v"]
        if key == "hooks.state":
            states.append(parsed)
        else:
            groups[key.split(".", 1)[1]] = parsed
    assert len(states) == 1, "one hooks.state holds every key"
    return {"groups": groups, "state": states[0]}


# -- the matcher -------------------------------------------------------------------------

def codex_selects(matcher: str, name: str) -> bool:
    """Codex 0.153's reading of a matcher (`matches_matcher`): `*` or empty selects every
    tool; only letters, digits, `_` and `|` is an exact alternation; else a regex found
    anywhere in the name."""
    if matcher in ("", "*"):
        return True
    if re.fullmatch(r"[A-Za-z0-9_|]+", matcher):
        return name in matcher.split("|")
    return re.search(matcher, name) is not None


def test_a_star_selects_every_tool():
    assert codex_hooks.matcher(["Bash", "*"]) == "*"
    assert codex_hooks.matcher(["*"]) == "*"


def test_plain_names_make_an_exact_alternation():
    assert codex_hooks.matcher(["Bash", "apply_patch", "Bash", "spawn_agent"]) == \
        "Bash|apply_patch|spawn_agent"


def test_a_glob_makes_one_anchored_regex():
    assert codex_hooks.matcher(["mcp__*Google_Calendar__*"]) == \
        "^(?:mcp__.*Google_Calendar__.*)$"
    # The matcher the live probe trusted and saw narrow.
    assert codex_hooks.matcher(["Bash", "apply_patch", "mcp__toya__*", "spawn_agent"]) == \
        "^(?:Bash|apply_patch|mcp__toya__.*|spawn_agent)$"


def test_no_pattern_makes_no_matcher():
    assert codex_hooks.matcher([]) is None
    assert codex_hooks.matcher(["", None]) is None


PATTERN_SETS = [
    ["Bash"], ["Bash", "apply_patch"], ["mcp__*Google_Calendar__*"], ["mcp__a.b__*"],
    ["mcp__toy-b__*", "Bash"], ["a+b", "c(d)*"], ["spawn_agent", "mcp__search__grep"],
    ["mcp__*"], ["*x"],
]
NAMES = ["Bash", "xBash", "Bashx", "bash", "apply_patch", "spawn_agent", "Agent",
         "multi_agent_v1wait_agent", "mcp__a.b__x", "mcp__aXb__x", "mcp__toy-b__echo",
         "mcp__toyb__echo", "mcp__Google_Calendar__list", "mcp__x_Google_Calendar__y",
         "mcp__search__grep", "mcp__search__grepx", "a+b", "aab", "c(d)", "c(d)zz", "cd",
         "x", "yx", "", "mcp__"]


@pytest.mark.parametrize("patterns", PATTERN_SETS, ids=lambda p: "|".join(p))
def test_the_matcher_selects_what_the_patterns_match_and_nothing_more(patterns):
    m = codex_hooks.matcher(patterns)
    re.compile(m)                                  # a regex both engines read
    for name in NAMES:
        want = any(glob_regex(p).fullmatch(name) for p in patterns)
        assert codex_selects(m, name) == want, (m, name)


# -- the trust entries -------------------------------------------------------------------

def verified_hash(label, one, matcher=None):
    """The hash form the live probe found Codex 0.153 trusting (copied, not imported)."""
    ident = {"event_name": label, "hooks": [one]}
    if matcher is not None:
        ident["matcher"] = matcher
    blob = json.dumps(ident, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def test_the_trust_hash_is_the_verified_form_with_and_without_a_matcher():
    one = codex_hooks.handler("/env/bin/moxi-turns-ping --socket /r/h.sock PreToolUse", 30)
    assert codex_hooks.trusted_hash("pre_tool_use", one) == \
        verified_hash("pre_tool_use", one)
    assert codex_hooks.trusted_hash("pre_tool_use", one, "^(?:Bash|mcp__a__.*)$") == \
        verified_hash("pre_tool_use", one, "^(?:Bash|mcp__a__.*)$")
    assert codex_hooks.trusted_hash("pre_tool_use", one, "*") != \
        codex_hooks.trusted_hash("pre_tool_use", one)
    assert codex_hooks.hook_key("pre_tool_use") == \
        "/<session-flags>/config.toml:pre_tool_use:0:0"


def test_tool_hooks_join_the_flags_with_their_matcher_and_one_state_holds_every_key():
    argv, keys = codex_hooks.session_flags(
        ["/env/bin/moxi-turns-ping"], "/run/hooks.sock", pre_matcher="Bash|spawn_agent",
        guard_wait_s=3, after_tool=True, after_wait_s=0.3)
    parsed = hook_groups(argv)
    assert set(keys) == {codex_hooks.hook_key(e.label) for e in codex_hooks.EVENTS} | {
        "/<session-flags>/config.toml:pre_tool_use:0:0",
        "/<session-flags>/config.toml:post_tool_use:0:0"}
    assert parsed["state"] == {key: {"trusted_hash": h} for key, h in keys.items()}
    for event, label, matcher, wait in (("PreToolUse", "pre_tool_use", "Bash|spawn_agent",
                                         "4"),
                                        ("PostToolUse", "post_tool_use", "*", "1.3")):
        [group] = parsed["groups"][event]
        assert group["matcher"] == matcher
        [one] = group["hooks"]
        assert one["timeout"] == codex_hooks.TOOL_TIMEOUT and one["async"] is False
        assert one["command"].endswith(f"--wait-seconds {wait} {event}")
        assert keys[codex_hooks.hook_key(label)] == verified_hash(label, one, matcher)
    # The five session hooks keep their matcher-less form.
    [group] = parsed["groups"]["Stop"]
    assert "matcher" not in group
    assert keys[codex_hooks.hook_key("stop")] == verified_hash("stop", group["hooks"][0])


def test_without_guards_or_rows_no_tool_hook_is_in_the_flags():
    argv, keys = codex_hooks.session_flags(["ping"], "/run/hooks.sock")
    assert len(keys) == len(codex_hooks.EVENTS)
    assert not {"PreToolUse", "PostToolUse"} & set(hook_groups(argv)["groups"])


# -- registration per launch -------------------------------------------------------------

def _registered(harness):
    return hook_groups(harness.app_server.argv)


def after_registry(fn=None, floor=None):
    row = listeners.Listener(
        "usage", frozenset({"after-tool"}), "usage.rows:after",
        floor="usage.rows:floor" if floor else None,
        _fn=fn or (lambda event: f"after {event.tool}"), _floor_fn=floor)
    return listeners.Registry((row,), order=("usage",))


def test_a_launch_with_no_codex_guard_and_no_after_tool_row_registers_neither(turns_env):
    claude_only = found("t.ask", {"claude": ["AskUserQuestion"]}, judge="judge_allow")
    endpoint, harness, _ = host(turns_env, guards=runner(claude_only))
    try:
        groups = _registered(harness)["groups"]
        assert not {"PreToolUse", "PostToolUse"} & set(groups)
        assert len(harness.hook_keys) == len(codex_hooks.EVENTS)
    finally:
        end_endpoint(endpoint)


def test_a_codex_guard_registers_pre_tool_use_with_its_matcher(turns_env):
    guards = runner(found("t.rm", {"codex": ["Bash"]}, judge="judge_allow"),
                    found("t.cal", {"codex": ["mcp__*Calendar__*"]}, deny="off"))
    endpoint, harness, _ = host(turns_env, guards=guards)
    try:
        parsed = _registered(harness)
        [group] = parsed["groups"]["PreToolUse"]
        assert group["matcher"] == "^(?:Bash|mcp__.*Calendar__.*)$"
        assert "PostToolUse" not in parsed["groups"]
        assert codex_hooks.hook_key("pre_tool_use") in harness.hook_keys
        # The load checked the new key's trust with the others.
        assert harness.loading() is False
    finally:
        end_endpoint(endpoint)


def test_an_after_tool_row_registers_post_tool_use_for_every_tool(turns_env):
    endpoint, harness, _ = host(turns_env, registry=after_registry())
    try:
        parsed = _registered(harness)
        [group] = parsed["groups"]["PostToolUse"]
        assert group["matcher"] == "*"
        assert "PreToolUse" not in parsed["groups"]
        wait = f"--wait-seconds {endpoint.after_tool_wait_s + 1:g} PostToolUse"
        assert group["hooks"][0]["command"].endswith(wait)
    finally:
        end_endpoint(endpoint)


def test_the_guard_list_in_turns_config_sets_the_matcher_and_the_pings_wait(turns_env):
    write_config(turns_env, '[guards]\non = ["turns.no-nested-agents"]\nmax_wait_ms = 1200\n')
    endpoint, harness, _ = host(turns_env)
    try:
        [group] = _registered(harness)["groups"]["PreToolUse"]
        assert group["matcher"] == "spawn_agent"
        assert group["hooks"][0]["command"].endswith("--wait-seconds 2.2 PreToolUse")
        # A later ready reads a larger wait, but no guard's bound passes the ping's.
        write_config(turns_env, '[guards]\non = ["turns.no-nested-agents"]\n'
                                'max_wait_ms = 9000\n')
        harness.guards_ready()
        assert harness._guard_settings()[1] == 1200
    finally:
        end_endpoint(endpoint)


# -- PreToolUse --------------------------------------------------------------------------

def case_guards():
    out = []
    for d in CASES["guards"]:
        if "codex" not in d["calls"]:
            continue
        out.append(found(d["id"], {"codex": d["calls"]["codex"]},
                         judge="judge_allow" if d["judge"] else None, deny=d["deny"],
                         who=d["who"], match=d["match"], wants=d["wants"],
                         on_error=d["on_error"]))
    return out


CODEX_CASES = [c for c in CASES["cases"] if c["harness"] == "codex"]


def test_the_shared_codex_cases_through_the_host(monkeypatch, turns_env):
    every = case_guards()
    guards = runner(*every)
    endpoint, harness, _ = host(turns_env, guards=guards)
    try:
        [group] = _registered(harness)["groups"]["PreToolUse"]
        m = group["matcher"]
        for case in CODEX_CASES:
            guards.held[:] = [f for f in every if f.id in case["on"]]
            guards.reload()
            CALLS.clear()
            agent = {"agent_id": case["agent"]} if case["agent"] else {}
            out = ping(monkeypatch, harness, "PreToolUse",
                       pre(case["tool"], case["input"], **agent))
            if case["covering"]:
                # The matcher narrows only: Codex asks about every covered call.
                assert codex_selects(m, case["tool"]), case["name"]
            if case["decision"] == "next":
                assert out is None, case["name"]
                assert CALLS == []
            elif case["decision"] == "deny":
                assert denied(out) == case["deny"], case["name"]
                assert CALLS == []
            else:
                assert denied(out) is None, case["name"]
                assert context_of(out) == "\n".join(
                    f"judged {case['tool']}" for _ in case["ask"]), case["name"]
                assert len(CALLS) == len(case["ask"])
    finally:
        end_endpoint(endpoint)


def test_the_judge_gets_the_call_as_codex_gave_it(monkeypatch, turns_env):
    guards = runner(found("t.all", {"codex": ["*"]}, judge="judge_quiet"))
    endpoint, harness, _ = host(turns_env, guards=guards)
    try:
        assert ping(monkeypatch, harness, "PreToolUse",
                    pre("Bash", {"command": "ls"}, cwd="/work/moved")) is None
        main = CALLS[-1]
        assert (main.harness, main.session_id, main.agent_id, main.tool,
                main.tool_use_id, dict(main.input), main.cwd, main.record_path) == (
            "codex", "thread-1", None, "Bash", "exec-1", {"command": "ls"}, "/work/moved",
            "/codex/sessions/rollout-main.jsonl")
        assert main.env == endpoint.env and main.prompts == ()

        # A subagent's call: its own id, its own directory, its own rollout.
        ping(monkeypatch, harness, "PreToolUse", pre(
            "Bash", {"command": "pwd"}, agent_id="sub-7", agent_type="default",
            cwd="/work/tree", transcript_path="/codex/sessions/rollout-sub-7.jsonl"))
        sub = CALLS[-1]
        assert (sub.session_id, sub.agent_id, sub.cwd, sub.record_path) == (
            "thread-1", "sub-7", "/work/tree", "/codex/sessions/rollout-sub-7.jsonl")
    finally:
        end_endpoint(endpoint)


@pytest.mark.parametrize("given", [None, "", "moved", "./moved", 7])
def test_a_missing_or_relative_cwd_falls_back_to_the_launch_directory(
        monkeypatch, turns_env, given):
    guards = runner(found("t.all", {"codex": ["*"]}, judge="judge_quiet"))
    endpoint, harness, _ = host(turns_env, guards=guards)
    try:
        frame = pre("Bash", {"command": "ls"})
        if given is None:
            del frame["cwd"]
        else:
            frame["cwd"] = given
        ping(monkeypatch, harness, "PreToolUse", frame)
        assert CALLS[-1].cwd == harness.cwd == str(Path(turns_env).resolve())
    finally:
        end_endpoint(endpoint)


def test_a_deny_a_break_and_lines_in_codex_output(monkeypatch, turns_env):
    guards = runner(
        found("t.push", {"codex": ["Bash"]}, judge="judge_deny",
              match={"command": "^git push"}),
        found("t.strict", {"codex": ["apply_patch"]}, judge="judge_raise", on_error="deny"),
        found("t.loose", {"codex": ["Bash"]}, judge="judge_raise"),
        found("t.note", {"codex": ["Bash"]}, judge="judge_allow"))
    endpoint, harness, _ = host(turns_env, guards=guards)
    try:
        out = ping(monkeypatch, harness, "PreToolUse", pre("Bash", {"command": "git push"}))
        assert out == {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "no pushes from here"}}
        out = ping(monkeypatch, harness, "PreToolUse", pre("apply_patch", {"input": "x"}))
        assert denied(out) == break_deny("t.strict", "it raised RuntimeError: boom")
        out = ping(monkeypatch, harness, "PreToolUse", pre("Bash", {"command": "ls"}))
        assert out == {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "judged Bash\n" + break_line(
                "t.loose", "it raised RuntimeError: boom")}}
        assert timed_count(harness, "guard", 3) == 3
    finally:
        end_endpoint(endpoint)


def test_another_threads_tool_hook_is_passed_untouched(monkeypatch, turns_env):
    guards = runner(found("t.all", {"codex": ["*"]}, deny="never"))
    endpoint, harness, _ = host(turns_env, guards=guards)
    try:
        assert ping(monkeypatch, harness, "PreToolUse",
                    pre("Bash", session_id="other-thread")) is None
    finally:
        end_endpoint(endpoint)


# -- a host that can't answer ------------------------------------------------------------

def registered_ping(harness) -> list[str]:
    """The launch's `PreToolUse` command as words, less the ping's own path."""
    import shlex

    [group] = _registered(harness)["groups"]["PreToolUse"]
    return shlex.split(group["hooks"][0]["command"])[1:]


def run_ping(monkeypatch, words, data):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert codex_hooks.main(words) == 0
    text = out.getvalue()
    return json.loads(text) if text.strip() else None


def strict_and_loose():
    return runner(
        found("t.strict", {"codex": ["apply_patch", "Bash"]}, judge="judge_allow",
              match={"command": "^git push"}, on_error="deny"),
        found("t.agents", {"codex": ["spawn_agent"]}, judge="judge_allow", who="agents",
              on_error="deny"),
        found("t.loose", {"codex": ["Bash", "read_file"]}, judge="judge_allow"))


def test_the_refuse_table_holds_only_the_guards_that_declared_deny(turns_env):
    endpoint, harness, _ = host(turns_env, guards=strict_and_loose())
    try:
        words = registered_ping(harness)
        table = json.loads(words[words.index("--refuse-unanswered") + 1])
        assert table == [
            {"calls": ["apply_patch", "Bash"], "who": "any",
             "match": {"command": "^git push"}, "match_flags": ""},
            {"calls": ["spawn_agent"], "who": "agents", "match": None, "match_flags": ""}]
        # The table is part of the trusted command, so the trust hash covers it.
        [group] = _registered(harness)["groups"]["PreToolUse"]
        assert harness.hook_keys[codex_hooks.hook_key("pre_tool_use")] == \
            verified_hash("pre_tool_use", group["hooks"][0], group["matcher"])
    finally:
        end_endpoint(endpoint)
    # Allow guards alone carry no table.
    endpoint, harness, _ = host(turns_env, guards=runner(
        found("t.loose", {"codex": ["Bash"]}, judge="judge_allow")))
    try:
        assert "--refuse-unanswered" not in registered_ping(harness)
    finally:
        end_endpoint(endpoint)


def test_a_dead_host_refuses_a_call_a_deny_guard_covers_and_passes_the_rest(
        monkeypatch, turns_env):
    endpoint, harness, _ = host(turns_env, guards=strict_and_loose())
    words = registered_ping(harness)
    end_endpoint(endpoint)
    assert not harness.hook_server.socket_path.exists()      # nothing listens now
    refused = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": codex_hooks.CANNOT_CHECK}}
    # A deny guard covers these: by pattern and match, and by who.
    assert run_ping(monkeypatch, words, pre("Bash", {"command": "git push"})) == refused
    assert run_ping(monkeypatch, words, pre("spawn_agent", agent_id="sub-1")) == refused
    # Only an allow guard covers these, or no guard's who or match takes them: they run.
    assert run_ping(monkeypatch, words, pre("Bash", {"command": "ls"})) is None
    assert run_ping(monkeypatch, words, pre("read_file", {"path": "a"})) is None
    assert run_ping(monkeypatch, words, pre("spawn_agent")) is None
    # A call the ping can't read refuses while a deny guard is in the table.
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert codex_hooks.main(words) == 0
    assert json.loads(out.getvalue()) == refused


def test_a_dead_host_with_only_allow_guards_passes_every_call(monkeypatch, turns_env):
    endpoint, harness, _ = host(turns_env, guards=runner(
        found("t.loose", {"codex": ["Bash"]}, judge="judge_allow")))
    words = registered_ping(harness)
    end_endpoint(endpoint)
    assert run_ping(monkeypatch, words, pre("Bash", {"command": "git push"})) is None


def test_a_host_fault_refuses_a_covered_call_and_passes_the_rest(monkeypatch, turns_env):
    endpoint, harness, _ = host(turns_env, guards=strict_and_loose())
    try:
        words = registered_ping(harness)

        def broken(frame):
            raise RuntimeError("boom")

        monkeypatch.setattr(harness, "pre_tool", broken)
        assert denied(run_ping(monkeypatch, words, pre("Bash", {"command": "git push"}))) \
            == codex_hooks.CANNOT_CHECK
        assert run_ping(monkeypatch, words, pre("Bash", {"command": "ls"})) is None
    finally:
        end_endpoint(endpoint)


def test_an_answering_host_decides_and_the_table_adds_nothing(monkeypatch, turns_env):
    endpoint, harness, _ = host(turns_env, guards=strict_and_loose())
    try:
        words = registered_ping(harness)
        out = run_ping(monkeypatch, words, pre("Bash", {"command": "git push"}))
        assert context_of(out) == "judged Bash\njudged Bash"
    finally:
        end_endpoint(endpoint)


@pytest.mark.parametrize("table", ["not json", '{"calls": ["Bash"]}',
                                   '[{"calls": ["Bash"], "match": {"command": "("}}]'])
def test_a_table_that_does_not_read_refuses(table):
    assert codex_hooks.refuses(table, pre("read_file")) is True


def test_a_guard_turned_off_at_a_ready_stops_covering(monkeypatch, turns_env):
    guards = runner(found("t.all", {"codex": ["Bash"]}, deny="refused"))
    endpoint, harness, _ = host(turns_env, guards=guards)
    try:
        assert denied(ping(monkeypatch, harness, "PreToolUse", pre("Bash"))) == "refused"
        guards.held.clear()
        ping(monkeypatch, harness, "SessionStart",
             {"session_id": "thread-1", "source": "compact"})
        assert ping(monkeypatch, harness, "PreToolUse", pre("Bash")) is None
    finally:
        end_endpoint(endpoint)


def test_a_guard_that_did_not_load_is_named_in_the_next_ready(monkeypatch, turns_env):
    guards = runner(found("t.gone", {"codex": ["Bash"]}, judge="no_such_judge"))
    endpoint, harness, _ = host(turns_env, guards=guards)
    try:
        deadline = time.monotonic() + 2
        out = None
        while time.monotonic() < deadline:
            out = ping(monkeypatch, harness, "SessionStart",
                       {"session_id": "thread-1", "source": "startup"})
            if out is not None:
                break
            time.sleep(0.05)
        assert "[turns] guard t.gone could not load" in context_of(out)
    finally:
        end_endpoint(endpoint)


def test_parallel_subagent_calls_are_judged_together(monkeypatch, turns_env):
    gate = threading.Event()
    seen = []

    def slow(call):
        seen.append(call.agent_id)
        gate.wait(2)
        return Allow(f"ok {call.agent_id}")

    sys.modules[ME].slow_judge = slow
    guards = runner(found("t.ceiling", {"codex": ["*"]}, judge="slow_judge", who="agents"))
    endpoint, harness, _ = host(turns_env, guards=guards)
    try:
        answers = {}

        def one(agent):
            answers[agent] = harness.hook_server._tool(
                {"event": "PreToolUse", **pre("Bash", agent_id=agent)})

        threads = [threading.Thread(target=one, args=(f"a{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 2
        while len(seen) < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sorted(seen) == ["a0", "a1", "a2", "a3"]    # all four judged at once
        gate.set()
        for t in threads:
            t.join(3)
        assert answers == {f"a{i}": {"ok": True, "context": f"ok a{i}"} for i in range(4)}
    finally:
        gate.set()
        end_endpoint(endpoint)


# -- the prompt rule ---------------------------------------------------------------------

def prompt_registry(events):
    def answer(event):
        events.append(event)
        return "remember the plan"

    row = listeners.Listener("sample", frozenset({"prompt"}), "sample.module:answer",
                             _fn=answer)
    return listeners.Registry((row,), order=("sample",))


def test_a_subagents_prompt_gets_no_listener_text_and_raises_no_prompt_event(
        monkeypatch, turns_env):
    events = []
    guards = runner(found("t.ask", {"codex": ["Bash"]}, judge="judge_quiet",
                          wants=("prompts",)))
    endpoint, harness, _ = host(turns_env, guards=guards, registry=prompt_registry(events))
    try:
        out = ping(monkeypatch, harness, "UserPromptSubmit", {
            "session_id": "thread-1", "turn_id": "turn-9", "agent_id": "sub-1",
            "agent_type": "default", "prompt": "Reply with the single word ok."})
        assert out is None and events == []

        out = ping(monkeypatch, harness, "UserPromptSubmit", {
            "session_id": "thread-1", "turn_id": "turn-2", "prompt": "fix the build"})
        assert context_of(out) == "remember the plan"
        assert [(e.event, e.text, e.origin) for e in events] == [
            ("prompt", "fix the build", None)]

        # A guard that wants the prompts gets the main turn's, never the subagent's task.
        ping(monkeypatch, harness, "PreToolUse", pre("Bash", turn_id="turn-2"))
        assert CALLS[-1].prompts == (Prompt(None, "fix the build"),)
        ping(monkeypatch, harness, "UserPromptSubmit", {
            "session_id": "thread-1", "turn_id": "turn-3", "prompt": "now ship it"})
        ping(monkeypatch, harness, "PreToolUse", pre("Bash", turn_id="turn-3"))
        assert CALLS[-1].prompts == (Prompt(None, "now ship it"),)
    finally:
        end_endpoint(endpoint)


def test_the_ping_forwards_agent_id_cwd_and_the_tool_fields():
    data = {"session_id": "s", "turn_id": "t", "agent_id": "a", "agent_type": "default",
            "cwd": "/w", "transcript_path": "/r.jsonl", "tool_name": "Bash",
            "tool_input": {"command": "ls"}, "tool_use_id": "exec-1",
            "tool_response": "big", "prompt": "hi", "model": "m"}
    assert codex_hooks.hook_fields(data, event="PreToolUse") == {
        "session_id": "s", "turn_id": "t", "agent_id": "a", "cwd": "/w",
        "transcript_path": "/r.jsonl", "tool_name": "Bash", "tool_use_id": "exec-1",
        "tool_input": {"command": "ls"}}
    post_fields = codex_hooks.hook_fields(data, event="PostToolUse")
    assert "tool_input" not in post_fields and "tool_response" not in post_fields
    assert codex_hooks.hook_fields(data, event="UserPromptSubmit")["prompt"] == "hi"
    main = {k: v for k, v in data.items() if k != "agent_id"}
    assert "agent_id" not in codex_hooks.hook_fields(main, event="PreToolUse")


# -- PostToolUse and the floor -----------------------------------------------------------

def usage(app, thread, tokens):
    app.on_notification("thread/tokenUsage/updated", {
        "threadId": thread, "turnId": "turn-1", "tokenUsage": {
            "total": {"totalTokens": tokens * 3}, "last": {"totalTokens": tokens},
            "modelContextWindow": 258400}}, None)


def test_after_tool_text_reaches_codex_with_the_calls_facts(monkeypatch, turns_env):
    seen = []

    def after(event):
        seen.append(event)
        return f"after {event.tool}"

    endpoint, harness, _ = host(turns_env, registry=after_registry(after))
    try:
        out = ping(monkeypatch, harness, "PostToolUse", post("Bash"))
        assert out == {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                              "additionalContext": "after Bash"}}
        ping(monkeypatch, harness, "PostToolUse", post(
            "apply_patch", agent_id="sub-2",
            transcript_path="/codex/sessions/rollout-sub-2.jsonl"))
        assert [(e.event, e.tool, e.tool_use_id, e.agent_id, e.point, e.record_path,
                 e.cwd) for e in seen] == [
            ("after-tool", "Bash", "exec-1", None, "after",
             "/codex/sessions/rollout-main.jsonl", harness.cwd),
            ("after-tool", "apply_patch", "exec-1", "sub-2", "after",
             "/codex/sessions/rollout-sub-2.jsonl", harness.cwd)]
        assert timed_count(harness, "after-tool", 2) == 2
    finally:
        end_endpoint(endpoint)


def test_the_floor_gates_only_the_main_loop_by_its_token_reading(monkeypatch, turns_env):
    called = []

    def after(event):
        called.append(event.agent_id)
        return "mark"

    endpoint, harness, app = host(
        turns_env, registry=after_registry(after, floor=lambda event: 1000))
    try:
        # The ready reads the floor.
        ping(monkeypatch, harness, "SessionStart",
             {"session_id": "thread-1", "source": "startup"})
        assert list(endpoint.floors.values().values()) == [1000]

        # No reading yet: an unknown context never skips.
        assert context_of(ping(monkeypatch, harness, "PostToolUse", post("Bash"))) == "mark"
        usage(app, "thread-1", 500)
        assert harness.context_tokens() == 500
        assert ping(monkeypatch, harness, "PostToolUse", post("Bash")) is None
        # A subagent's call always reaches the row.
        assert context_of(ping(monkeypatch, harness, "PostToolUse",
                               post("Bash", agent_id="sub-1"))) == "mark"
        # Another thread's reading is not the main loop's context.
        usage(app, "sub-thread", 5000)
        assert ping(monkeypatch, harness, "PostToolUse", post("Bash")) is None
        usage(app, "thread-1", 1000)
        assert context_of(ping(monkeypatch, harness, "PostToolUse", post("Bash"))) == "mark"
        assert called == [None, "sub-1", None]
    finally:
        end_endpoint(endpoint)


def test_with_no_after_tool_row_a_post_tool_ping_adds_nothing(monkeypatch, turns_env):
    endpoint, harness, _ = host(turns_env)
    try:
        assert ping(monkeypatch, harness, "PostToolUse", post("Bash")) is None
        assert ping(monkeypatch, harness, "PreToolUse", pre("Bash")) is None
    finally:
        end_endpoint(endpoint)
