"""`find`, `sources` and `subagents` over each harness, on files and databases the test
kit builds."""

from __future__ import annotations

import os
import sqlite3

import pytest
from conftest import write_config

from moxi_sessions import codex
from moxi_sessions.find import (HIT, LISTED, MISS, NONE, UNREADABLE, find, sources,
                                subagents)
from moxi_sessions.shape import UNKNOWN
from moxi_sessions.source import Source, file_header, unpack
from moxi_sessions.testing import (ClaudeTranscript, CodexRollout, OpenCodeDb,
                                   write_imports)

SESSION = "0190b000-0000-7000-8000-00000000c1a0"
OTHER = "0190b000-0000-7000-8000-00000000c1a1"
THREAD = "0190c000-0000-7000-8000-0000000c0de1"
THREAD_B = "0190c000-0000-7000-8000-0000000c0de2"

needs_permissions = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="permission bits do not hold for root")


@pytest.fixture
def projects(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    return root


@pytest.fixture
def rollouts(tmp_path):
    root = tmp_path / "codex" / "sessions"
    root.mkdir(parents=True)
    return root


def transcript(root, session_id=SESSION, **kw) -> ClaudeTranscript:
    t = ClaudeTranscript(root, session_id=session_id, **kw)
    t.user()
    t.assistant()
    return t


def rollout(root, thread=THREAD, **kw) -> CodexRollout:
    r = CodexRollout(root, thread_id=thread, **kw)
    r.turn()
    r.request()
    return r


@pytest.fixture
def unlock():
    """Directories a test made unreadable, given their mode back so tmp can be removed."""
    locked = []
    yield locked.append
    for path in locked:
        os.chmod(path, 0o755)


# --- find: Claude Code ----------------------------------------------------------------------

def test_find_a_claude_transcript(projects):
    path = transcript(projects).write()
    found = find("claude", SESSION, root=projects)
    assert found.found
    (hit,) = found.hits
    assert hit.source == Source("claude", str(path), SESSION, None)
    assert not hit.compressed
    assert [(p.root, p.outcome) for p in found.probes] == [(str(projects), HIT)]


def test_a_claude_miss_is_a_root_read_and_holding_none(projects):
    transcript(projects, OTHER).write()
    found = find("claude", SESSION, root=projects)
    assert not found.found and found.hits == ()
    assert [p.outcome for p in found.probes] == [MISS]


def test_an_absent_root_is_unreadable_never_a_miss(tmp_path):
    found = find("claude", SESSION, root=tmp_path / "unmounted")
    (probe,) = found.probes
    assert probe.outcome.startswith(f"{UNREADABLE}: ")
    assert not found.found


def test_roots_are_asked_in_order_until_one_holds_the_session(tmp_path, projects):
    archive = tmp_path / "archive"
    path = transcript(archive).write()
    found = find("claude", SESSION, root=[tmp_path / "gone", projects, archive,
                                          tmp_path / "never-asked"])
    assert [p.outcome.split(":")[0] for p in found.probes] == [UNREADABLE, MISS, HIT]
    assert [h.source.path for h in found.hits] == [str(path)]


def test_an_id_that_is_not_one_is_refused_at_every_root(projects):
    found = find("claude", "../etc", root=[projects, projects / "x"])
    assert [p.outcome.startswith("refused: ") for p in found.probes] == [True, True]
    assert not found.found


def test_find_reads_the_default_and_configured_roots(tmp_path, monkeypatch, own_config):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    path = transcript(tmp_path / "cfg" / "projects").write()
    assert [h.source.path for h in find("claude", SESSION).hits] == [str(path)]
    stated = tmp_path / "stated"
    other = transcript(stated).write()
    write_config(own_config, f'[claude]\nprojects = "{stated}"\n')
    assert [h.source.path for h in find("claude", SESSION).hits] == [str(other)]


def test_find_refuses_a_harness_it_does_not_read(projects):
    with pytest.raises(ValueError):
        find("aider", SESSION, root=projects)


def test_found_as_dict(projects):
    path = transcript(projects).write()
    data = find("claude", SESSION, root=projects).as_dict()
    assert data == {"harness": "claude", "session_id": SESSION, "found": True,
                    "hits": [{"path": str(path), "session_id": SESSION, "agent_id": None,
                              "compressed": False}],
                    "probes": [{"root": str(projects), "outcome": HIT,
                                "hits": data["hits"]}]}


# --- find: Codex ---------------------------------------------------------------------------

def test_find_every_rollout_of_a_thread_a_compressed_one_marked(rollouts):
    plain = rollout(rollouts).write()
    packed = plain.parent.parent / "01" / (plain.name + ".zst")
    packed.parent.mkdir()
    packed.write_bytes(b"\x28\xb5\x2f\xfd")
    found = find("codex", THREAD, root=rollouts)
    assert [(h.source.path, h.compressed) for h in found.hits] == sorted(
        [(str(plain), False), (str(packed), True)])
    assert {h.source.session_id for h in found.hits} == {THREAD}
    assert {h.source.harness for h in found.hits} == {"codex"}


def test_find_a_codex_thread_by_its_upper_case_id(rollouts):
    rollout(rollouts).write()
    (hit,) = find("codex", THREAD.upper(), root=rollouts).hits
    assert hit.source.session_id == THREAD


def test_a_codex_miss_and_an_unreadable_root(rollouts, tmp_path):
    rollout(rollouts, THREAD_B).write()
    found = find("codex", THREAD, root=[rollouts, tmp_path / "nowhere"])
    assert [p.outcome.split(":")[0] for p in found.probes] == [MISS, UNREADABLE]


def test_find_reads_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    path = rollout(tmp_path / "ch" / "sessions").write()
    assert [h.source.path for h in find("codex", THREAD).hits] == [str(path)]


# --- find: OpenCode ------------------------------------------------------------------------

def test_find_an_opencode_session_and_a_child(tmp_path):
    db = OpenCodeDb(tmp_path / "oc")
    parent = db.session()
    child = parent.child()
    db.write()
    (hit,) = find("opencode", parent.id, root=db.path).hits
    assert hit.source == Source("opencode", str(db.path), parent.id, None)
    (hit,) = find("opencode", child.id, root=db.path).hits
    assert hit.source == Source("opencode", str(db.path), child.id, child.id)


def test_an_opencode_miss_needs_the_database_read(tmp_path):
    db = OpenCodeDb(tmp_path / "oc")
    db.session()
    db.write()
    found = find("opencode", "ses_absent", root=[tmp_path / "none.db", db.path])
    outcomes = [p.outcome for p in found.probes]
    assert outcomes[0].startswith(f"{UNREADABLE}: ") and "missing" in outcomes[0]
    assert outcomes[1] == MISS


def test_an_opencode_database_without_the_columns_is_unreadable(tmp_path):
    path = tmp_path / "opencode.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    (probe,) = find("opencode", "ses_x", root=path).probes
    assert probe.outcome.startswith(f"{UNREADABLE}: ") and "column" in probe.outcome


def test_find_reads_xdg_data_home_for_opencode(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    db = OpenCodeDb(tmp_path / "data" / "opencode")
    s = db.session()
    db.write()
    assert [h.source.path for h in find("opencode", s.id).hits] == [str(db.path)]


# --- sources: Claude Code ------------------------------------------------------------------

def claude_corpus(projects):
    t = transcript(projects, project="-work-a")
    sub = t.subagent("aaa1", tool_use_id="toolu_1")
    sub.user()
    sub.assistant()
    flow = t.subagent("bbb2", workflow="wf_run1", agent_type="worker")
    flow.assistant()
    t.write()
    other = transcript(projects, OTHER, project="-work-b", cwd="/work/b").write()
    (projects / "-work-a" / "notes.jsonl").write_text("{}\n")
    (projects / "loose.jsonl").write_text("{}\n")
    return t, sub, flow, other


def test_sources_lists_mains_subagents_and_workflow_agents(projects):
    t, sub, flow, other = claude_corpus(projects)
    listing = sources("claude", root=projects)
    assert listing.ok and listing.reason is None and listing.unreadable == ()
    assert [(e.kind, e.rel_path, e.workflow_id) for e in listing] == [
        ("main", f"-work-a/{SESSION}.jsonl", None),
        ("subagent", f"-work-a/{SESSION}/subagents/agent-aaa1.jsonl", None),
        ("subagent", f"-work-a/{SESSION}/subagents/workflows/wf_run1/agent-bbb2.jsonl",
         "wf_run1"),
        ("main", f"-work-b/{OTHER}.jsonl", None)]
    main, agent, worker, second = listing.entries
    assert main.source == Source("claude", str(t.path), SESSION, None)
    assert agent.source == Source("claude", str(sub.path), SESSION, "aaa1")
    assert worker.source == Source("claude", str(flow.path), SESSION, "bbb2")
    assert second.source.session_id == OTHER and len(listing) == 4


def test_each_file_carries_its_reuse_keys(projects):
    claude_corpus(projects)
    for entry in sources("claude", root=projects, heads=False):
        st = os.stat(entry.source.path)
        assert (entry.size, entry.mtime_ns, entry.dev, entry.ino) == (
            st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino)
        assert entry.header is None


def test_heads_read_each_files_header_and_a_subagents_labels(projects):
    claude_corpus(projects)
    main, agent, worker, second = sources("claude", root=projects).entries
    assert main.header.cwd == "/work/project" and main.header.labels is None
    assert main.header.entrypoint == "cli" and main.header.session_id == SESSION
    assert second.header.cwd == "/work/b"
    assert agent.header.agent_id == "aaa1" and agent.header.parent == SESSION
    assert agent.header.labels["tool_use_id"] == "toolu_1"
    assert worker.header.labels["workflow_id"] == "wf_run1"
    assert worker.header.labels["agent_type"] == "worker"


def test_modified_since_keeps_only_newer_files(projects):
    t, sub, flow, other = claude_corpus(projects)
    for path in (t.path, sub.path, flow.path):
        os.utime(path, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    os.utime(other, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
    listing = sources("claude", root=projects, modified_since=1_500_000_000)
    assert [e.source.session_id for e in listing] == [OTHER]
    listing = sources("claude", root=projects, modified_since=2_000_000_000)
    assert [e.source.session_id for e in listing] == [OTHER]


def test_a_missing_root_is_not_an_empty_listing(tmp_path):
    listing = sources("claude", root=tmp_path / "unmounted")
    assert not listing.ok and "does not exist" in listing.reason
    assert listing.entries == ()
    file = tmp_path / "file"
    file.write_text("")
    listing = sources("codex", root=file)
    assert not listing.ok and "not a directory" in listing.reason


@needs_permissions
def test_a_directory_that_cannot_be_listed_is_named_and_the_walk_goes_on(projects,
                                                                           unlock):
    claude_corpus(projects)
    locked = projects / "-work-a"
    os.chmod(locked, 0)
    unlock(locked)
    listing = sources("claude", root=projects)
    assert listing.ok
    assert [e.source.session_id for e in listing] == [OTHER]
    assert [path for path, _why in listing.unreadable] == ["-work-a"]


def test_a_listing_entry_bounds_a_read_at_its_size(projects):
    t, *_ = claude_corpus(projects)
    entry = sources("claude", root=projects).entries[0]
    t.user("more")
    t.flush()
    source, bound = unpack(entry)
    assert source == entry.source and bound == entry.size < os.stat(t.path).st_size
    assert unpack(source) == (source, None)


def test_sources_reads_the_configured_root(tmp_path, own_config):
    stated = tmp_path / "stated"
    transcript(stated).write()
    write_config(own_config, f'[claude]\nprojects = "{stated}"\n')
    listing = sources("claude")
    assert listing.root == str(stated) and len(listing) == 1


# --- sources: Codex ------------------------------------------------------------------------

def test_codex_sources_carry_head_facts_and_import_marks(rollouts):
    first = rollout(rollouts).write()
    imported = rollout(rollouts, THREAD_B, imported=True, start=1793440800.0).write()
    packed = first.parent / (first.name.replace(THREAD, "0190c000-0000-7000-8000-0000000c0de3")
                             + ".zst")
    packed.write_bytes(b"\x28\xb5\x2f\xfd")
    write_imports(rollouts, [THREAD_B])
    (rollouts / "2026" / "stray.jsonl").parent.mkdir(exist_ok=True)
    (rollouts / "2026" / "stray.jsonl").write_text("{}\n")
    listing = sources("codex", root=rollouts)
    assert listing.ok and listing.imports.ids == frozenset({THREAD_B})
    by_thread = {e.source.session_id: e for e in listing}
    assert set(by_thread) == {THREAD, THREAD_B, "0190c000-0000-7000-8000-0000000c0de3"}
    plain = by_thread[THREAD]
    assert plain.kind == "rollout" and not plain.compressed
    assert plain.source == Source("codex", str(first), THREAD, None)
    assert plain.header.codex_source == "exec" and plain.header.version == "0.153.0"
    marks = plain.header.import_marks
    assert (marks.originator, marks.turn_id, marks.listed) == (False, False, False)
    marks = by_thread[THREAD_B].header.import_marks
    assert (marks.originator, marks.turn_id, marks.listed) == (True, True, True)
    assert by_thread[THREAD_B].source.path == str(imported)
    zst = by_thread["0190c000-0000-7000-8000-0000000c0de3"]
    assert zst.compressed and zst.header is None and zst.size == 4


def test_codex_sources_without_heads_read_no_file(rollouts):
    rollout(rollouts).write()
    (entry,) = sources("codex", root=rollouts, heads=False)
    assert entry.header is None and entry.size > 0


def test_a_codex_listing_says_why_import_marks_are_unknown(rollouts):
    rollout(rollouts).write()
    (entry,) = sources("codex", root=rollouts)
    assert entry.header.import_marks.listed_reason == codex.IMPORTS_NOT_FOUND
    write_imports(rollouts, text="not json")
    listing = sources("codex", root=rollouts)
    assert listing.imports.reason.startswith("imports file unreadable")
    (entry,) = listing
    assert entry.header.import_marks.listed_reason.startswith("imports file unreadable")


# --- sources: OpenCode ---------------------------------------------------------------------

def test_opencode_sources_are_one_per_session_row(tmp_path):
    db = OpenCodeDb(tmp_path / "oc")
    parent = db.session(title="the parent", at=1793354400.0)
    child = parent.child(title="the child", at=1793354500.0)
    db.write()
    listing = sources("opencode", root=db.path)
    assert listing.ok and listing.root == str(db.path)
    assert [(e.kind, e.source) for e in listing] == [
        ("session", Source("opencode", str(db.path), parent.id, None)),
        ("session", Source("opencode", str(db.path), child.id, child.id))]
    assert [e.header.title for e in listing] == ["the parent", "the child"]
    assert all(e.size is None and e.rel_path is None for e in listing)
    later = sources("opencode", root=db.path, modified_since=1793354500.0)
    assert [e.source.session_id for e in later] == [child.id]


def test_a_missing_opencode_database_is_not_an_empty_listing(tmp_path):
    listing = sources("opencode", root=tmp_path / "opencode.db")
    assert not listing.ok and "missing" in listing.reason


# --- subagents: Claude Code ----------------------------------------------------------------

def test_subagents_of_a_claude_session(projects):
    t, sub, flow, _ = claude_corpus(projects)
    listing = subagents(Source("claude", str(t.path), SESSION))
    assert listing.state == LISTED and listing.reason is None
    first, second = listing.entries
    assert first.agent_id == "aaa1" and first.source == Source(
        "claude", str(sub.path), SESSION, "aaa1")
    assert (first.agent_type, first.description, first.tool_use_id, first.spawn_depth,
            first.parent_agent_id, first.workflow_id) == (
        "general-purpose", "Look into it", "toolu_1", 1, None, None)
    assert second.agent_id == "bbb2" and second.workflow_id == "wf_run1"
    assert second.agent_type == "worker"
    assert first.size == os.stat(sub.path).st_size


def test_a_subagent_entry_is_read_up_to_its_listed_size(projects):
    t, sub, *_ = claude_corpus(projects)
    entry = subagents(Source("claude", str(t.path), SESSION)).entries[0]
    sub.assistant()
    sub.flush()
    assert unpack(entry) == (entry.source, entry.size)
    assert entry.size < os.stat(sub.path).st_size


def test_no_subagents_directory_is_none(projects):
    path = transcript(projects).write()
    listing = subagents(Source("claude", str(path), SESSION))
    assert listing.state == NONE and listing.entries == ()


def test_an_empty_subagents_directory_is_none(projects):
    path = transcript(projects).write()
    (path.parent / SESSION / "subagents").mkdir(parents=True)
    assert subagents(Source("claude", str(path), SESSION)).state == NONE


def test_a_missing_transcript_is_unreadable_not_none(projects):
    listing = subagents(Source("claude", str(projects / "p" / f"{SESSION}.jsonl"),
                               SESSION))
    assert listing.state == UNREADABLE and "cannot be read" in listing.reason


@needs_permissions
def test_a_subagents_directory_that_cannot_be_listed_is_unreadable(projects, unlock):
    t, *_ = claude_corpus(projects)
    locked = t.path.parent / SESSION / "subagents"
    os.chmod(locked, 0)
    unlock(locked)
    listing = subagents(Source("claude", str(t.path), SESSION))
    assert listing.state == UNREADABLE and "cannot be listed" in listing.reason


@needs_permissions
def test_a_workflow_directory_that_cannot_be_listed_is_named(projects, unlock):
    t, *_ = claude_corpus(projects)
    locked = t.path.parent / SESSION / "subagents" / "workflows"
    os.chmod(locked, 0)
    unlock(locked)
    listing = subagents(Source("claude", str(t.path), SESSION))
    assert listing.state == LISTED and [e.agent_id for e in listing] == ["aaa1"]
    assert [path for path, _why in listing.unreadable_parts] == [
        "subagents/workflows"]


def test_a_sidecar_that_cannot_be_read_costs_its_labels_not_the_entry(projects):
    t = transcript(projects)
    t.subagent("ccc3", sidecar="{not json").assistant()
    t.subagent("ddd4", sidecar=False).assistant()
    t.write()
    listing = subagents(Source("claude", str(t.path), SESSION))
    assert [e.agent_id for e in listing] == ["ccc3", "ddd4"]
    for entry in listing:
        assert (entry.agent_type, entry.description, entry.tool_use_id,
                entry.spawn_depth, entry.parent_agent_id) == (None,) * 5


def test_a_subagents_own_listing_holds_what_it_spawned(projects):
    t = transcript(projects)
    t.subagent("eee5").assistant()
    t.subagent("fff6", parent_agent_id="eee5", spawn_depth=2).assistant()
    t.subagent("ggg7", workflow="wf_run2", parent_agent_id="eee5").assistant()
    t.write()
    whole = subagents(Source("claude", str(t.path), SESSION))
    assert [e.agent_id for e in whole] == ["eee5", "fff6", "ggg7"]
    parent = whole.entries[0]
    spawned = subagents(parent)
    assert spawned.state == LISTED
    assert [e.agent_id for e in spawned] == ["fff6", "ggg7"]
    assert subagents(whole.entries[1]).state == NONE


def test_a_listing_entry_from_sources_lists_its_subagents(projects):
    claude_corpus(projects)
    main = sources("claude", root=projects).entries[0]
    assert [e.agent_id for e in subagents(main)] == ["aaa1", "bbb2"]


def test_subagents_as_dict(projects):
    t, sub, *_ = claude_corpus(projects)
    data = subagents(Source("claude", str(t.path), SESSION)).as_dict()
    assert data["state"] == LISTED and data["session_id"] == SESSION
    assert data["entries"][0]["path"] == str(sub.path)
    assert "reason" not in data


# --- subagents: Codex ---------------------------------------------------------------------

CHILD = "0190c000-0000-7000-8000-0000000c0de3"
CHILD_B = "0190c000-0000-7000-8000-0000000c0de4"
GRANDCHILD = "0190c000-0000-7000-8000-0000000c0de5"


def spawned(child) -> object:
    """A child rollout with one turn and request, written."""
    child.turn()
    child.request()
    child.write()
    return child


def test_codex_subagents_one_child(rollouts):
    r = rollout(rollouts)
    child = spawned(r.spawn(CHILD, "explore", call_id="call_spawn_a", role="explorer",
                            nickname="Noether"))
    r.write()
    listing = subagents(Source("codex", str(r.path), THREAD))
    assert listing.state == LISTED and listing.reason is None
    assert listing.unreadable_parts == ()
    (entry,) = listing
    assert entry.agent_id == CHILD
    assert entry.source == Source("codex", str(child.path), CHILD, CHILD)
    assert entry.size == os.stat(child.path).st_size
    assert (entry.agent_type, entry.description, entry.tool_use_id, entry.spawn_depth,
            entry.parent_agent_id, entry.workflow_id) == (
        "explorer", "/root/explore", "call_spawn_a", 1, None, None)


def test_codex_subagents_two_children_in_spawn_order(rollouts):
    r = rollout(rollouts)
    b = spawned(r.spawn(CHILD_B, "second"))
    a = spawned(r.spawn(CHILD, "first", completed=False))
    r.write()
    listing = subagents(Source("codex", str(r.path), THREAD))
    assert [e.agent_id for e in listing] == [CHILD_B, CHILD]
    assert [e.source.path for e in listing] == [str(b.path), str(a.path)]
    assert [e.description for e in listing] == ["/root/second", "/root/first"]
    assert all(e.agent_type is None for e in listing)     # Codex wrote a null role


def test_codex_a_nested_spawn_is_listed_by_the_thread_that_spawned_it(rollouts):
    r = rollout(rollouts)
    child = r.spawn(CHILD, "explore")
    child.turn()
    grand = spawned(child.spawn(GRANDCHILD, "look", call_id="call_spawn_g"))
    child.request()
    child.write()
    r.write()
    top = subagents(Source("codex", str(r.path), THREAD))
    assert [e.agent_id for e in top] == [CHILD]
    assert top.entries[0].parent_agent_id is None
    nested = subagents(top.entries[0])
    assert nested.state == LISTED
    (entry,) = nested
    assert entry.source == Source("codex", str(grand.path), GRANDCHILD, GRANDCHILD)
    assert (entry.description, entry.tool_use_id, entry.spawn_depth,
            entry.parent_agent_id) == ("/root/explore/look", "call_spawn_g", 2, CHILD)
    assert subagents(nested.entries[0]).state == NONE


def test_codex_a_child_named_but_missing_is_named_not_dropped(rollouts):
    r = rollout(rollouts)
    spawned(r.spawn(CHILD, "here"))
    r.spawn(CHILD_B, "gone")                     # its rollout is never written
    r.write()
    listing = subagents(Source("codex", str(r.path), THREAD))
    assert listing.state == LISTED and [e.agent_id for e in listing] == [CHILD]
    ((thread, why),) = listing.unreadable_parts
    assert thread == CHILD_B and MISS in why
    assert listing.as_dict()["unreadable_parts"] == [[CHILD_B, why]]


def test_codex_children_all_missing_are_unreadable_not_none(rollouts):
    r = rollout(rollouts)
    r.spawn(CHILD, "gone")
    r.write()
    listing = subagents(Source("codex", str(r.path), THREAD))
    assert listing.state == UNREADABLE and listing.entries == ()
    assert CHILD in listing.reason and MISS in listing.reason


def test_codex_a_parent_with_no_spawns_is_none(rollouts):
    path = rollout(rollouts).write()
    listing = subagents(Source("codex", str(path), THREAD))
    assert listing.state == NONE and listing.reason is None and listing.entries == ()


def test_codex_a_missing_or_compressed_parent_is_unreadable(rollouts):
    listing = subagents(Source("codex", str(rollouts / "absent.jsonl"), THREAD))
    assert listing.state == UNREADABLE and "cannot be read" in listing.reason
    zst = rollouts / "2026/10/30/rollout-2026-10-30T10-00-00-" f"{THREAD}.jsonl.zst"
    zst.parent.mkdir(parents=True)
    zst.write_bytes(b"\x28\xb5\x2f\xfd")
    listing = subagents(Source("codex", str(zst), THREAD))
    assert listing.state == UNREADABLE and "zstd" in listing.reason


def test_codex_a_parent_still_writing_is_read_to_its_listed_size(rollouts):
    r = rollout(rollouts)
    child = spawned(r.spawn(CHILD, "first", completed=False))
    r.write()
    (listed,) = [e for e in sources("codex", root=rollouts) if e.source.session_id == THREAD]
    # After the listing: a second spawn, the first child's next lines, and a spawn item
    # still being written.
    spawned(r.spawn(CHILD_B, "second"))
    r.flush()
    child.request()
    child.flush()
    r.spawn_item(GRANDCHILD)
    r.flush(terminated=False)
    with open(r.path, "rb+") as handle:
        handle.truncate(os.path.getsize(r.path) - 20)
    assert [e.agent_id for e in subagents(listed)] == [CHILD]
    now = subagents(Source("codex", str(r.path), THREAD))
    assert [e.agent_id for e in now] == [CHILD, CHILD_B]
    entry = now.entries[0]
    assert entry.size == os.stat(child.path).st_size
    assert unpack(entry) == (entry.source, entry.size)


def test_codex_spawn_items_carried_from_another_thread_are_not_its_own(rollouts):
    r = rollout(rollouts)
    r.spawn_item(CHILD, writer=THREAD_B)
    r.write()
    assert subagents(Source("codex", str(r.path), THREAD)).state == NONE


def test_codex_a_child_whose_line_one_is_missing_keeps_its_entry(rollouts):
    r = rollout(rollouts)
    child = spawned(r.spawn(CHILD, "explore", call_id="call_spawn_a", meta=False))
    r.write()
    (entry,) = subagents(Source("codex", str(r.path), THREAD))
    assert entry.source.path == str(child.path)
    assert (entry.description, entry.tool_use_id) == ("/root/explore", "call_spawn_a")
    assert entry.agent_type is UNKNOWN and entry.spawn_depth is UNKNOWN
    data = entry.as_dict()
    assert data["unknown"] == ["agent_type", "spawn_depth"]
    assert "agent_type" not in data and data["tool_use_id"] == "call_spawn_a"


def test_codex_a_childs_header_names_its_parent(rollouts):
    r = rollout(rollouts)
    child = spawned(r.spawn(CHILD, "explore", role="explorer", nickname="Noether"))
    r.write()
    headers = {e.source.session_id: e.header for e in sources("codex", root=rollouts)}
    link = headers[CHILD].spawn
    assert (link.parent_thread_id, link.depth, link.agent_path, link.agent_nickname,
            link.agent_role) == (THREAD, 1, "/root/explore", "Noether", "explorer")
    assert headers[CHILD].parent == THREAD
    assert headers[CHILD].as_dict()["spawn"] == {
        "parent_thread_id": THREAD, "depth": 1, "agent_path": "/root/explore",
        "agent_nickname": "Noether", "agent_role": "explorer"}
    assert headers[THREAD].spawn is None and "spawn" not in headers[THREAD].as_dict()
    assert child.path.exists()


def test_codex_a_header_with_no_line_one_has_an_unknown_spawn(rollouts):
    r = CodexRollout(rollouts, meta=False)
    r.turn()
    r.write()
    (entry,) = sources("codex", root=rollouts)
    assert entry.header.spawn is UNKNOWN and "spawn" in entry.header.as_dict()["unknown"]


# --- subagents: OpenCode -------------------------------------------------------------------

def test_opencode_subagents_are_the_child_sessions(tmp_path):
    db = OpenCodeDb(tmp_path / "oc")
    parent = db.session(title="the parent")
    a = parent.assistant()
    child = parent.child(title="explore the repo", agent="explore")
    a.tool("task", {"description": "explore"}, metadata={"sessionId": child.id},
           call_id="call_task")
    lonely = db.session()
    db.write()
    listing = subagents(Source("opencode", str(db.path), parent.id))
    assert listing.state == LISTED and listing.header.title == "the parent"
    (entry,) = listing
    assert entry.source == Source("opencode", str(db.path), child.id, child.id)
    assert (entry.agent_id, entry.agent_type, entry.description, entry.tool_use_id,
            entry.spawn_depth, entry.parent_agent_id, entry.size) == (
        child.id, "explore", "explore the repo", "call_task", 1, None, None)
    assert subagents(Source("opencode", str(db.path), lonely.id)).state == NONE


def test_opencode_subagents_of_a_missing_session_or_database_are_unreadable(tmp_path):
    db = OpenCodeDb(tmp_path / "oc")
    db.session()
    db.write()
    listing = subagents(Source("opencode", str(db.path), "ses_absent"))
    assert listing.state == UNREADABLE and "ses_absent" in listing.reason
    listing = subagents(Source("opencode", str(tmp_path / "none.db"), "ses_absent"))
    assert listing.state == UNREADABLE and "missing" in listing.reason


def test_opencode_subagents_need_a_session(tmp_path):
    with pytest.raises(ValueError):
        subagents(Source("opencode", str(tmp_path / "oc.db"), None))


def test_subagents_refuses_what_is_not_a_source():
    with pytest.raises(ValueError):
        subagents("/some/path.jsonl")


# --- subagents: a real Codex spawn (live) --------------------------------------------------

#: A real Codex rollout whose thread spawned one child, for the live test to read. The
#: environment is read when the module loads, before a test's own is emptied.
LIVE_PARENT_ENV = "MOXI_SESSIONS_CODEX_SPAWN_PARENT"
LIVE_PARENT = os.environ.get(LIVE_PARENT_ENV)


@pytest.mark.live
@pytest.mark.skipif(not LIVE_PARENT, reason=f"{LIVE_PARENT_ENV} names no rollout")
def test_live_a_real_spawn_lists_its_one_child():
    thread = codex.NAME_RE.match(os.path.basename(LIVE_PARENT)).group("thread")
    listing = subagents(Source("codex", LIVE_PARENT, thread))
    assert listing.state == LISTED and listing.unreadable_parts == ()
    (entry,) = listing
    assert entry.agent_id == entry.source.session_id != thread
    assert entry.size == os.stat(entry.source.path).st_size
    assert entry.spawn_depth == 1 and entry.parent_agent_id is None
    assert isinstance(entry.tool_use_id, str) and entry.description.startswith("/root/")
    with open(entry.source.path, "rb") as handle:
        header = file_header(entry.source, handle, entry.size)
    assert header.spawn.parent_thread_id == thread and header.parent == thread
    assert header.spawn.depth == 1 and header.spawn.agent_path == entry.description
