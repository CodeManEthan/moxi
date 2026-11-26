"""The chat recall's read-only fence (`moxi_memory.recall_fence`): the headless request
every chat recall launches, and the probe that checks the fence on the machine, with a fake
`claude` on PATH that prints a set `system/init`."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from moxi.paths import Layout
from moxi_launcher import api

from moxi_memory import recall_fence as fence

POSIX = pytest.mark.skipif(sys.platform == "win32",
                           reason="the fake claude is a script run by its #! line")

SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"

FAKE = """#!{python}
import json, os, subprocess, sys, time
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(here, "fake.json")) as fh:
    plan = json.load(fh)
if sys.argv[1:] == ["--version"]:
    print(plan.get("version", "2.1.332") + " (Claude Code)")
    sys.exit(0)
runs = os.path.join(here, "runs")
os.makedirs(runs, exist_ok=True)
record = {{"argv": sys.argv[1:], "pid": os.getpid()}}
if plan.get("grandchild"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    record["grandchild"] = child.pid
with open(os.path.join(runs, "%d.json" % os.getpid()), "w") as fh:
    json.dump(record, fh)
if plan.get("stderr"):
    sys.stderr.write(plan["stderr"] + "\\n")
    sys.stderr.flush()
if plan.get("exit") is not None:
    sys.exit(plan["exit"])
print(json.dumps({{"type": "system", "subtype": "session_title_changed"}}), flush=True)
if plan.get("init") is not None:
    print(json.dumps(dict(type="system", subtype="init", **plan["init"])), flush=True)
time.sleep(plan.get("sleep_s", 120))
"""

PASSING = {"tools": ["Glob", "Grep", "Read", "EndConversation"], "mcp_servers": []}


class Claude:
    """The fake `claude`, first on PATH, and what each of its runs recorded."""

    def __init__(self, root: Path, monkeypatch):
        self.root = root
        self.bin = root / "bin"
        self.bin.mkdir(parents=True)
        self.path = self.bin / "claude"
        self.path.write_text(FAKE.format(python=sys.executable))
        self.path.chmod(0o755)
        self.plan(init=PASSING)
        monkeypatch.setenv("PATH", f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}")

    def plan(self, **plan) -> None:
        (self.root / "fake.json").write_text(json.dumps(plan))

    def runs(self) -> list[dict]:
        runs = self.root / "runs"
        return [json.loads(p.read_text()) for p in sorted(runs.glob("*.json"))] \
            if runs.is_dir() else []

    def touch(self) -> None:
        """Change the binary's size and time, as an update would."""
        with self.path.open("a") as fh:
            fh.write("# updated\n")
        later = time.time() + 5
        os.utime(self.path, (later, later))


@pytest.fixture
def claude(tmp_path, monkeypatch):
    return Claude(tmp_path / "fake", monkeypatch)


def spec(**kw) -> fence.Spec:
    cwd = fence.chat_dir()
    cwd.mkdir(parents=True, exist_ok=True)
    fields = dict(brief="what did we settle about the kiosk approver", cwd=str(cwd),
                  name="recall-r1", request_id="chat-r1", session_id=SID, max_tools=8)
    return fence.Spec(**{**fields, **kw})


def argv(s: fence.Spec, **kw) -> list[str]:
    return list(api.preview(fence.request(api, s, **kw)).argv)


def flag_values(words: list[str], flag: str) -> list[str]:
    return [words[i + 1] for i, w in enumerate(words[:-1]) if w == flag]


# -- the request (the fence's half of the headless request's test) ------------------------

def write_defaults() -> None:
    presets = Layout("launcher").config_dir() / "presets.toml"
    presets.parent.mkdir(parents=True, exist_ok=True)
    presets.write_text(
        '[defaults]\nidentity = "someone"\npermission_mode = "acceptEdits"\n'
        'mcp_config = ["/x/servers.json"]\nkeep_warm = "on"\n'
        'extra_flags = ["--verbose-ish"]\n', encoding="utf-8")
    config = Layout("launcher").config_dir() / "config.toml"
    config.write_text('[name]\ntemplate = "tmpl-<project>"\n', encoding="utf-8")


def test_the_request_carries_every_fence_flag_and_nothing_of_the_defaults():
    write_defaults()
    words = argv(spec())
    assert words[:2] == ["claude", "-p"]
    assert flag_values(words, "--tools") == ["Read,Grep,Glob"]
    assert flag_values(words, "--disallowedTools") == ["mcp__*"]
    assert flag_values(words, "--permission-mode") == ["dontAsk"]
    assert flag_values(words, "--permission-prompts") == ["none"]
    assert "--restricted" in words and "--safe-mode" in words
    assert flag_values(words, "--max-budget-usd") == ["0.50"]
    assert flag_values(words, "--max-turns") == ["9"]
    assert flag_values(words, "--output-format") == ["json"]
    assert flag_values(words, "-n") == ["recall-r1"]
    assert flag_values(words, "--session-id") == [SID]
    for absent in ("--mcp-config", "--strict-mcp-config", "--verbose-ish"):
        assert absent not in words
    assert not any(w.startswith("tmpl-") for w in words)
    assert "acceptEdits" not in words


def moxi_additions() -> list[str]:
    """The words Moxi's own launcher additions (turns') give a Claude launch without
    comms here; none when turns can't give them."""
    from moxi_launcher import compose

    try:
        return list(compose.launch_additions(cwd=str(fence.chat_dir()), env=os.environ,
                                             session_id=SID).argv)
    except Exception:  # noqa: BLE001 - turns not reachable: it adds nothing
        return []


def without(words: list[str], part: list[str]) -> list[str]:
    """`words` with the run `part` taken out once, where it appears."""
    for i in range(len(words) - len(part) + 1):
        if part and words[i:i + len(part)] == part:
            return words[:i] + words[i + len(part):]
    return list(words)


def test_no_allow_rule_or_plugin_comes_from_the_users_install_or_the_project():
    presets = Layout("launcher").config_dir() / "presets.toml"
    presets.parent.mkdir(parents=True, exist_ok=True)
    presets.write_text('[defaults]\nallowed_tools = ["Bash(rm:*)"]\n', encoding="utf-8")
    project = fence.chat_dir() / ".claude"
    project.mkdir(parents=True, exist_ok=True)
    (project / "settings.json").write_text('{"permissions": {"allow": ["Bash"]}}')
    words = without(argv(spec()), moxi_additions())
    assert not any(w.startswith("--allowedTools") for w in words)
    assert "--plugin-dir" not in words
    assert not any("rm:*" in w for w in argv(spec()))


def test_the_web_adds_its_two_tools_with_an_allow_rule_for_them():
    words = argv(spec(web=True))
    assert flag_values(words, "--tools") == ["Read,Grep,Glob,WebFetch,WebSearch"]
    assert "WebFetch" in words and "WebSearch" in words
    assert fence.fence_tools(web=True) == {"Read", "Grep", "Glob", "EndConversation",
                                           "WebFetch", "WebSearch"}


@pytest.mark.parametrize("tools,web,part", [
    (["Read", "Grep", "Glob"], False, None),
    (["Read", "Grep", "Glob", "EndConversation"], False, None),
    (["Read", "Grep", "Glob", "WebFetch"], True, None),
    (["Read", "Grep", "Glob", "WebFetch"], False, "tools"),
    (["Read", "Grep"], False, "tools"),
    (["Read", "Grep", "Glob", "Bash"], False, "tools"),
])
def test_the_tools_part_checks_a_bound_not_an_exact_set(tools, web, part):
    got, detail = fence.judge({"tools": tools, "mcp_servers": []}, "", exited=False,
                              web=web)
    assert got == part
    if part:
        assert ("missing Glob" in detail) or ("extra " in detail)


def test_every_other_settable_row_is_left_out():
    req = fence.request(api, spec(model="sonnet", add_dirs=["/notes"]))
    rows = set(api.settable_rows("claude"))
    assert set(req.left_out) | set(req.typed) | {"extra_flags"} == rows
    assert {"identity", "scope", "mcp_config", "keep_warm", "subconscious",
            "notes_dir", "project", "scratch"} <= set(req.left_out)
    words = argv(spec(model="sonnet", add_dirs=["/notes"]))
    assert flag_values(words, "--model") == ["sonnet"]
    assert flag_values(words, "--add-dir") == ["/notes"]


# -- the probe ----------------------------------------------------------------------------

@POSIX
def test_the_expected_init_passes_and_is_recorded_for_the_binary(claude):
    v = fence.ensure(api, spec())
    assert v.ok and v.outcome.passed and v.outcome.version == "2.1.332"
    assert fence.recorded(v.binary) == v.outcome
    assert v.binary.path == os.path.realpath(claude.path)
    assert fence.ensure(api, spec()).outcome == v.outcome
    assert len(claude.runs()) == 1          # the second read the record


@POSIX
@pytest.mark.parametrize("plan,part,named", [
    ({"init": {"tools": [*PASSING["tools"], "Bash"], "mcp_servers": []}}, "tools", "Bash"),
    ({"init": {"tools": ["Read", "Grep", "EndConversation"], "mcp_servers": []}},
     "tools", "missing Glob"),
    ({"init": {**PASSING, "mcp_servers": [{"name": "notes", "status": "connected"}]}},
     "mcp", "notes"),
    ({"stderr": "error: unknown option '--safe-mode'", "exit": 1}, "flags", "--safe-mode"),
    ({"version": "2.1.200", "init": PASSING}, "flags", "2.1.200"),
])
def test_each_failed_part_is_recorded_and_stops_the_next_start(claude, plan, part, named):
    claude.plan(**plan)
    v = fence.ensure(api, spec())
    assert v.reason == fence.FENCE and v.outcome.part == part and named in v.detail
    later = fence.verdict(api, spec())
    assert later.reason == fence.FENCE and later.detail.startswith(f"{part}: ")
    assert named in later.detail


@POSIX
def test_an_exit_with_no_init_for_another_reason_records_nothing(claude):
    claude.plan(stderr="not signed in", exit=1)
    v = fence.ensure(api, spec())
    assert v.reason == fence.NO_OUTCOME and "not signed in" in v.detail
    assert fence.recorded(v.binary) is None
    claude.plan(init=PASSING)
    assert fence.ensure(api, spec()).ok
    assert len(claude.runs()) == 2          # the next recall probed again


@POSIX
def test_a_changed_binary_is_probed_again(claude):
    first = fence.ensure(api, spec())
    claude.touch()
    claude.plan(init={**PASSING, "tools": [*PASSING["tools"], "Write"]})
    assert fence.verdict(api, spec()).outcome is None
    second = fence.ensure(api, spec())
    assert second.reason == fence.FENCE and second.binary != first.binary
    assert len(claude.runs()) == 2


@POSIX
def test_the_probe_runs_the_recalls_own_argv_but_its_output_flags_and_ids(claude):
    s = spec()
    fence.ensure(api, s)
    [run] = claude.runs()
    own = argv(s)[1:]

    def without_own(words):
        out = list(words)
        for i in reversed(range(len(out))):
            if out[i] in ("--output-format", "-n", "--session-id",
                          "--append-system-prompt-file"):
                del out[i:i + 2]
        return [w for w in out if w not in ("--verbose", "--no-session-persistence")]

    probe_name = flag_values(run["argv"], "-n")
    assert probe_name == ["recall-r1-probe"]
    assert flag_values(run["argv"], "--session-id") != [SID]
    assert flag_values(run["argv"], "--output-format") == ["stream-json"]
    assert "--verbose" in run["argv"] and "--no-session-persistence" in run["argv"]
    assert run["argv"][0] == own[0] == "-p"
    assert run["argv"][1].endswith(fence.PROBE_PROMPT) and own[1].endswith(s.brief)
    assert without_own(run["argv"][2:]) == without_own(own[2:])   # past the brief


@POSIX
def test_the_probe_is_a_launcher_launch_ended_through_the_launcher(claude, monkeypatch):
    ended = []
    real = api.end_headless
    monkeypatch.setattr(api, "end_headless",
                        lambda rid, **kw: ended.append(rid) or real(rid, **kw))
    assert fence.ensure(api, spec()).ok
    assert len(ended) == 1 and ended[0] != "chat-r1"   # the recall's own id stays free
    assert "system" in api.run_log(ended[0]).read_text()


@POSIX
def test_the_probe_ends_the_process_tree_after_the_init(claude):
    from moxi import proc

    claude.plan(init=PASSING, grandchild=True)
    assert fence.ensure(api, spec()).ok
    [run] = claude.runs()
    for pid in (run["pid"], run["grandchild"]):
        found = proc.identity(pid)
        assert found is None or not proc.alive(found)


@POSIX
def test_two_children_at_once_run_one_probe(claude, tmp_path):
    claude.plan(init=PASSING, sleep_s=120)
    code = ("import sys, json; from moxi_launcher import api; "
            "from moxi_memory import recall_fence as f; "
            f"s = f.Spec(brief='b', cwd={str(fence.chat_dir())!r}, name='recall-r1', "
            f"request_id=sys.argv[1], session_id={SID!r}, max_tools=8); "
            "v = f.ensure(api, s); print(json.dumps(v.ok))")
    fence.chat_dir().mkdir(parents=True, exist_ok=True)
    kids = [subprocess.Popen([sys.executable, "-c", code, f"chat-r{i}"],
                             stdout=subprocess.PIPE, text=True) for i in (1, 2)]
    outs = [k.communicate(timeout=60)[0].strip() for k in kids]
    assert outs == ["true", "true"]
    assert len(claude.runs()) == 1
