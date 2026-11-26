"""The host against the read tools' providers, as the workspace installs them: memory's
`search_memory`, sessions' `list_sessions` and `read_session`, and notes' `list_notes`,
`read_note` and `search_notes`, each loaded from `moxi.connector.providers` as the daemon
loads it, and called through the door with the real sign-in.

These are the checks no one package can make alone: a tool's answer as the app gets it,
after the host's scrub; the call log's rows as the host writes them; and a secret planted
in a transcript and in a note, read through every tool, never coming back. The data is
real: a Claude Code transcript under the test's home, memory's index of it built by
memory's own ingest, and a notes root set in notes' config. Beside the canary, which is a
secret's shape in any frame, search_memory and read_session are planted shapes that only
a change or a join after the redaction would make (a key's value apart from its name by a
no-break space, a subagent id holding `token` before its title), and read_session opens a
nested subagent by the pair a search_memory hit gives. Outside a workspace with memory, sessions, notes and secrets'
chat redaction installed beside the host, the module skips.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from importlib import metadata

import moxi.config
import pytest
from conftest import ORIGIN
from kit import call, request, rpc
from signin_kit import CALLBACKS, Clock, Door

from moxi import modules
from moxi_connector import app as app_mod
from moxi_connector import grants
from moxi_connector.app import Front, Host, build_app
from moxi_connector.calllog import MemoryLog
from moxi_connector.oauth import OAuth
from moxi_connector.provider import refused_field
from moxi_connector.providers import Registry

#: The read tools' modules, and each one's tools in the order the door lists them.
TOOLS = {
    "memory": ["search_memory"],
    "sessions": ["list_sessions", "read_session"],
    "notes": ["list_notes", "read_note", "search_notes"],
}
NAMES = [name for names in TOOLS.values() for name in names]
#: The tools those modules list beside their read tools: memory's `recall` starts work.
NOT_READ = frozenset({"recall"})

_secrets = modules.load("secrets")
if (not all(modules.load(m).ok for m in TOOLS) or not _secrets.ok
        or not callable(getattr(_secrets.api, "chat_redact", None))):
    pytest.skip("memory, sessions, notes and secrets' chat redaction aren't installed "
                "beside the host; the joins run in the workspace", allow_module_level=True)

# Memory and sessions are optional beside the host and connector doesn't depend on them,
# so the test reaches them by name, once the skip above has found them installed:
# sessions through its API, memory's walk through its own modules.
sessions_api = modules.load("sessions").api
ingest = importlib.import_module("moxi_memory.ingest")
links = importlib.import_module("moxi_memory.links")

#: A made-up token of a credentials rule's shape: the canary.
CANARY = "ghp_" + "Zq7Lm2Xc9Vb4Nn1Pk8Rt3Ws6Yd5Hf0Jg2UaX"
#: What must never come back: the canary, and each half of it, which a split would leave.
CANARY_PARTS = (CANARY, CANARY[:20], CANARY[20:])
assert len(CANARY) == 40
SESSION = "0190b000-0000-7000-8000-00000000d00a"
ZWSP = "\u200b"
RLO = "\u202e"

#: Each tool's call-log fields: the names the call log's design gives each read tool
#: (memory's count of dropped hits is `hits_dropped`, since `dropped` is the host's own).
LOG_FIELDS = {
    "search_memory": {"words", "hits", "hits_dropped", "unscanned", "redactions", "cut",
                      "refusal"},
    "list_sessions": {"rows", "refusal"},
    "read_session": {"harness", "turns", "rows", "cut", "redactions", "refusal"},
    "list_notes": {"entries", "hidden", "refusal"},
    "read_note": {"note", "from_line", "lines", "redactions", "refusal"},
    "search_notes": {"words", "hits", "files_read", "confirmed", "unscanned", "cut",
                     "refusal"},
}


def _memory_config() -> dict:
    return {"harnesses": ["claude"], "index.include_subagents": False,
            "index.tail_chars": 1000, "redact.sets": ["credentials", "broad"]}


@pytest.fixture
def world(tmp_path):
    """A Claude Code session under the test's home holding the canary, memory's index of
    it, and a notes root with a note holding the canary whole and split by an invisible
    character. The model's turn also names a file by its full path under the home folder
    and a loopback address. Gives the home folder."""
    home = os.environ["HOME"]
    project = f"{home}/work/proj-a"
    t = sessions_api.testing.ClaudeTranscript(sessions_api.root("claude"),
                                              session_id=SESSION, cwd=project)
    t.user(f"deploy{ZWSP} the widget with {CANARY} {RLO}today", origin="human")
    req = t.request()
    req.tool_use("Read", {"file_path": f"{project}/src/widget.py"})
    t.assistant(f"The widget deploy is done; the key {CANARY} worked. Logged to "
                f"{project}/docs/deploy-log.md, live at http://localhost:8080/status.")
    t.write()
    report = ingest.run("walk", config=_memory_config(), launcher=links.Link(links.NONE),
                        then=lambda args: None)
    assert report is not None

    root = (tmp_path / "notes").resolve()
    (root / "plans").mkdir(parents=True)
    (root / "plans" / "rollout.md").write_text(
        f"# Friday plan\n\nThe rollout key is {CANARY}.\n"
        f"Split: {CANARY[:20]}{ZWSP}{CANARY[20:]}\n"
        f"The {RLO}window opens Friday.\n", encoding="utf-8")
    moxi.config.set_value("notes", "root", str(root), by="test")
    return home


def _providers() -> dict:
    """The read tools' providers, from the entry point group the daemon loads."""
    found = {e.name: e for e in metadata.entry_points(group="moxi.connector.providers")
             if e.name in TOOLS}
    assert sorted(found) == sorted(TOOLS)
    return {name: entry.load() for name, entry in found.items()}


def _registry(withheld=()) -> Registry:
    registry = Registry.of(_providers(), withheld=withheld)
    asyncio.run(registry.start(dict(os.environ)))
    return registry


@pytest.fixture
def scrubbed(monkeypatch):
    """Every string the host's scrub changed on its way out: `(before, after)` pairs."""
    changed: list = []
    text, value = app_mod.scrub_text, app_mod.scrub_value

    def scrub_text(s):
        out = text(s)
        if out != s:
            changed.append((s, out))
        return out

    def scrub_value(v):
        out = value(v)
        if out != v:
            changed.append((v, out))
        return out

    monkeypatch.setattr(app_mod, "scrub_text", scrub_text)
    monkeypatch.setattr(app_mod, "scrub_value", scrub_value)
    return changed


async def _door(registry) -> Door:
    host = Host(registry, calllog=MemoryLog())
    clock = Clock()
    oauth = OAuth(grants.Store(), host.calllog, redirect_uris=CALLBACKS, clock=clock)
    host.signin, host.redirect_uris = oauth, CALLBACKS
    app = build_app(ORIGIN, host)
    await app.start()
    return Door(Front(app, host), oauth, clock)


def _calls(registry, asks):
    """Sign in through the page, list the tools, then call each `(name, args)` of `asks`
    through `POST /mcp`. Gives `(tools listed, {name: result}, the call log's rows)`."""

    async def main():
        d = await _door(registry)
        try:
            _, tokens = await d.page_sign_in()
            access = [("authorization", f"Bearer {tokens['access_token']}")]
            listed = (await request(d.front, "POST", "/mcp", rpc("tools/list", rid=2),
                                    headers=access)).json()["result"]["tools"]
            got = {}
            for n, (name, args) in enumerate(asks, start=3):
                answer = await request(d.front, "POST", "/mcp", call(name, args, rid=n),
                                       headers=access)
                assert answer.status == 200, answer.body
                got[name] = answer.json()["result"]
            return listed, got, list(d.host.calllog.rows)
        finally:
            await d.close()

    try:
        return asyncio.run(main())
    finally:
        asyncio.run(registry.stop())


ASKS = [
    ("search_memory", {"words": "widget deploy"}),
    ("list_sessions", {}),
    ("read_session", {"session": SESSION, "turns": 10}),
    ("list_notes", {"folder": "plans"}),
    ("read_note", {"path": "plans/rollout.md"}),
    ("search_notes", {"words": "rollout key"}),
]


def _shown(result) -> str:
    """Everything an answer carries to the app: its text parts and structured value."""
    texts = [part.get("text", "") for part in result.get("content", [])]
    return "\n".join(texts) + "\n" + json.dumps(result.get("structuredContent"),
                                                ensure_ascii=False)


def test_each_read_tool_answers_through_the_door_redacted_and_unchanged_by_the_scrub(
        world, scrubbed):
    registry = _registry()
    rows = [r for r in registry.status_rows() if r.name.split()[0] in TOOLS]
    assert not [r for r in rows if r.level == "fail"], rows
    listed, got, _ = _calls(registry, ASKS)
    listed = [t for t in listed if t["name"] not in NOT_READ]

    assert sorted(t["name"] for t in listed) == sorted(NAMES)
    for tool in listed:
        hints = tool["annotations"]
        assert hints["readOnlyHint"] is True and hints["destructiveHint"] is False, tool
        assert tool["title"] and tool["inputSchema"]["type"] == "object"

    for name, result in got.items():
        assert not result["isError"], (name, result)
        if name not in ("list_sessions", "list_notes"):
            assert "[REDACTED:github-pat]" in _shown(result), name
    # The host's scrub found nothing to remove: each provider's last redaction saw the
    # answer as the app gets it, invisible characters planted in both sources included.
    assert scrubbed == []

    hits = got["search_memory"]["structuredContent"]["hits"]
    assert hits and {h["session"] for h in hits} == {SESSION}
    assert {h["writer"] for h in hits} <= {"person", "model"}
    # the planted path and loopback address come back cut, as read_session cuts them
    found = _shown(got["search_memory"])
    assert "Logged to deploy-log.md, live at local" in found, found
    assert "Read(widget.py)" in found
    assert world not in found and "/docs/" not in found and "localhost" not in found

    sessions = got["list_sessions"]["structuredContent"]["sessions"]
    [mine] = [s for s in sessions if s["session"] == SESSION]
    assert mine["folder"] == "proj-a"

    read = got["read_session"]
    assert "Read(widget.py)" in _shown(read)
    assert world not in _shown(read) and world not in _shown(got["list_sessions"])

    assert [e["name"] for e in got["list_notes"]["structuredContent"]["entries"]] == [
        "rollout.md"]
    note = got["read_note"]["structuredContent"]
    assert note["path"] == "plans/rollout.md" and note["redactions"] >= 2
    [hit] = got["search_notes"]["structuredContent"]["hits"]
    assert hit["path"] == "plans/rollout.md" and "[REDACTED:" in hit["line"]


def test_the_planted_canary_never_comes_back_through_any_tool_or_the_call_log(world):
    _, got, rows = _calls(_registry(), ASKS)
    for name, result in got.items():
        shown = _shown(result)
        for part in CANARY_PARTS:
            assert part not in shown, (name, part)
    logged = json.dumps(rows, ensure_ascii=False)
    for part in CANARY_PARTS:
        assert part not in logged


def test_each_call_log_row_carries_its_tools_fields_and_nothing_the_log_refuses(world):
    _, got, rows = _calls(_registry(), ASKS)
    calls = {r["tool"]: r for r in rows if r.get("kind") == "request" and r.get("tool")
             and r.get("rpc") == "tools/call"}
    assert sorted(calls) == sorted(NAMES)
    for name, row in calls.items():
        assert LOG_FIELDS[name] <= set(row), (name, sorted(row))
        assert row["refusal"] is None, (name, row)
        assert row["provider"] in TOOLS and name in TOOLS[row["provider"]]
    assert calls["read_note"]["note"] == "plans/rollout.md"
    assert calls["search_memory"]["words"] == 2 and calls["search_memory"]["hits"] >= 1
    assert calls["read_session"]["harness"] == "claude"
    for row in rows:
        assert refused_field(row) is None, row
    # No row holds a session id, the words searched for or text read.
    logged = json.dumps(rows, ensure_ascii=False)
    for held in (SESSION, "widget deploy", "rollout key", "Friday plan", "window opens"):
        assert held not in logged, held


def test_a_withheld_read_tool_is_absent_and_answers_as_no_tool(world):
    registry = _registry(withheld=["search_notes"])
    listed, got, rows = _calls(registry, [("search_notes", {"words": "rollout key"}),
                                          ("read_note", {"path": "plans/rollout.md"})])
    listed = [t for t in listed if t["name"] not in NOT_READ]
    assert sorted(t["name"] for t in listed) == sorted(n for n in NAMES
                                                        if n != "search_notes")
    withheld = got["search_notes"]
    assert withheld["isError"]
    assert withheld["structuredContent"] == {"refusal": "unknown-tool"}
    assert withheld["content"][0]["text"] == "There is no tool named search_notes."
    assert not got["read_note"]["isError"]
    [row] = [r for r in rows if r.get("rpc") == "tools/call"
             and r.get("tool") == "search_notes"]
    assert "files_read" not in row


#: A made-up value of a key-named secret's shape, and a title of no rule's shape alone.
KEYED = "Xk9Lm2Qw7Rt5Zp3Vb8Nc4Hd6Jf1Gs0Ty"
TITLE = "Qm7vXk2pLr9sTb4wZ8"
SESSION_KEYED = "0190b000-0000-7000-8000-00000000d00b"
#: A subagent id that holds a key's name.
AGENT_KEYED = "a9fTokenQ2x"


def test_shapes_a_squeeze_or_a_join_would_make_come_back_redacted(world, scrubbed):
    """A key's name and its value apart by a no-break space in a tool call, which one
    line made after the redaction would join, through search_memory's snippet and
    read_session's call line; and a subagent id holding `token` before its title, which
    the header joins. Neither piece is a secret's shape alone."""
    t = sessions_api.testing.ClaudeTranscript(sessions_api.root("claude"),
                                              session_id=SESSION_KEYED,
                                              cwd=f"{world}/work/proj-b")
    t.user("set the service up", origin="human")
    t.request().tool_use("Bash", {"command": f"cd turbine && export API_KEY=\u00a0{KEYED}"})
    child = t.subagent(AGENT_KEYED)
    child.title(TITLE)
    child.user("look into it", origin="human")
    t.write()
    assert ingest.run("walk", config=_memory_config(), launcher=links.Link(links.NONE),
                      then=lambda args: None) is not None

    _, got, _ = _calls(_registry(), [("search_memory", {"words": "turbine"})])
    found = got["search_memory"]
    assert not found["isError"], found
    [hit] = found["structuredContent"]["hits"]
    assert hit["session"] == SESSION_KEYED and "turbine" in hit["snippet"]
    assert KEYED not in _shown(found) and "[REDACTED:" in hit["snippet"]

    _, got, _ = _calls(_registry(), [
        ("read_session", {"session": SESSION_KEYED, "turns": 10})])
    main = got["read_session"]
    assert not main["isError"], main
    [call_row] = [r for r in main["structuredContent"]["rows"] if r["kind"] == "tool call"]
    assert KEYED not in _shown(main) and "[REDACTED:" in call_row["call"]

    _, got, _ = _calls(_registry(), [
        ("read_session", {"session": SESSION_KEYED, "agent": AGENT_KEYED})])
    sub = got["read_session"]
    assert not sub["isError"], sub
    header = sub["content"][0]["text"].splitlines()[1]
    assert f"{AGENT_KEYED}: {TITLE}" not in header and "[REDACTED:" in header
    assert scrubbed == []


def test_a_nested_subagent_s_hit_opens_through_read_session(world):
    """An OpenCode subagent's subagent: search_memory names its hit by the main session
    and the grandchild's id, and read_session opens that pair."""
    store = os.path.split(sessions_api.root("opencode"))    # the database file itself
    db = sessions_api.testing.OpenCodeDb(store[0], name=store[1])
    main = db.session(work_dir=f"{world}/work/proj-c")
    main.user("start the survey")
    child = main.child(title="the child")
    child.user("split the survey")
    grandchild = child.child(title="the grandchild")
    grandchild.user("check the lighthouse")
    grandchild.assistant().text("The lighthouse beacon is lit.")
    db.write()
    config = {**_memory_config(), "harnesses": ["opencode"],
              "index.include_subagents": True}
    assert ingest.run("walk", config=config, launcher=links.Link(links.NONE),
                      then=lambda args: None) is not None

    _, got, _ = _calls(_registry(), [
        ("search_memory", {"words": "lighthouse beacon", "subagents": True})])
    hits = got["search_memory"]["structuredContent"]["hits"]
    assert hits, got["search_memory"]
    pairs = {(h["session"], h["agent"]) for h in hits}
    assert pairs == {(main.id, grandchild.id)}

    _, got, _ = _calls(_registry(), [
        ("read_session", {"session": main.id, "agent": grandchild.id,
                          "harness": "opencode"})])
    read = got["read_session"]
    assert not read["isError"], read
    assert read["structuredContent"]["agent"] == grandchild.id
    assert "The lighthouse beacon is lit." in _shown(read)

