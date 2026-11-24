"""Starting a recall: the mode, which prompts count, the route checked at every try, the
record and its child, and the child's first steps (its line, its config read, the first
search and the brief)."""

import types

import pytest

from moxi_memory import (listener, recall_brief, recall_child, recall_guard, recall_start,
                         recalls, records)

SESSION = "0190b000-0000-7000-8000-000000000001"


def event(*, mode="automatic", own=True, by=None, text="why did the ingest lock stall",
          harness="claude", session_id=SESSION, cwd=None):
    env = {recall_start.SUBCONSCIOUS_ENV: mode} if mode else {}
    return types.SimpleNamespace(event="prompt", harness=harness, session_id=session_id,
                                 env=env, own=own, delivery_by=by, delivery_senders=None,
                                 text=text, cwd=cwd, origin="composer" if own else None)


@pytest.fixture
def started(monkeypatch):
    """The listener's route is the subagent one, and children are recorded, not run."""
    spawned = []
    monkeypatch.setattr(recall_start, "route", lambda harness, session_id: (
        (recall_start.SUBAGENT, None) if harness == "claude" else (None, recall_start.CODEX)))
    monkeypatch.setattr(recall_start, "spawn_child",
                        lambda session_id, recall_id: spawned.append((session_id, recall_id)))
    return spawned


# -- the mode and the trigger ----------------------------------------------------------

def test_only_a_stamped_mode_turns_it_on():
    assert recall_start.mode({recall_start.SUBCONSCIOUS_ENV: "automatic"}) == "automatic"
    assert recall_start.mode({recall_start.SUBCONSCIOUS_ENV: "on-demand"}) == "on-demand"
    assert recall_start.mode({recall_start.SUBCONSCIOUS_ENV: "off"}) is None
    assert recall_start.mode({}) is None
    assert recall_start.mode(None) is None


@pytest.mark.parametrize("own, by, trigger, expected", [
    (True, None, "user", True),
    (False, "comms", "user", False),
    (False, "comms", "user-and-comms", True),
    (False, None, "user-and-comms", False),
    (None, None, "user-and-comms", False),
    (False, "memory", "user-and-comms", False),
])
def test_which_prompts_count(own, by, trigger, expected):
    assert recall_start.counts(event(own=own, by=by), trigger) is expected


# -- the listener ---------------------------------------------------------------------

def test_a_counted_prompt_in_an_automatic_session_writes_a_record_and_starts_a_child(
        started):
    assert listener.subconscious(event()) is None
    [(session_id, recall_id)] = started
    record = records.read(SESSION, recall_id)
    assert session_id == SESSION
    assert record["state"] == "starting" and record["start"] == "prompt"
    assert record["text"] == "why did the ingest lock stall"
    assert record["route"] == "subagent"
    assert record["settings"]["limit_s"] == 60 and record["settings"]["max_tools"] == 8


@pytest.mark.parametrize("kwargs", [{"mode": None}, {"mode": "off"}, {"own": False},
                                    {"mode": "on-demand"}, {"session_id": "../x"}])
def test_nothing_starts_otherwise(started, kwargs):
    assert listener.subconscious(event(**kwargs)) is None
    assert started == []
    assert recalls.logged_sessions() == []


def test_a_new_recall_hurries_the_running_one(started):
    listener.subconscious(event())
    listener.subconscious(event(text="and the writer lock"))
    first, second = [records.read(SESSION, r) for _s, r in started]
    assert first["state"] == "hurried"
    assert second["state"] == "starting"


def test_a_session_with_no_route_logs_off_once_per_reason(started):
    listener.subconscious(event(harness="codex"))
    listener.subconscious(event(harness="codex"))
    assert started == []
    [line] = recalls.read_lines(SESSION)
    assert line["state"] == "off" and line["reason"] == recall_start.CODEX


def test_the_route_is_checked_at_every_try(started, monkeypatch):
    calls = []
    monkeypatch.setattr(recall_start, "route", lambda harness, session_id: (
        calls.append(1) or ((None, recall_start.NO_FORK) if len(calls) == 1
                            else (recall_start.SUBAGENT, None))))
    listener.subconscious(event())
    listener.subconscious(event())
    assert len(calls) == 2 and len(started) == 1
    assert recalls.read_lines(SESSION)[0]["reason"] == recall_start.NO_FORK


def test_a_counted_prompt_on_demand_resets_the_count(started):
    with recalls.session_lock(SESSION):
        recall_start.write_asked(SESSION, 2)
    listener.subconscious(event(mode="on-demand", own=False))
    assert recall_start.asked(SESSION) == 2
    listener.subconscious(event(mode="on-demand"))
    assert recall_start.asked(SESSION) == 0
    assert started == []


def test_a_codex_or_other_session_has_no_route():
    assert recall_start.route("codex", SESSION) == (None, recall_start.CODEX)
    assert recall_start.route("opencode", SESSION) == (None, recall_start.NO_FORK)


def test_a_claude_session_with_no_endpoint_has_no_route():
    assert recall_start.route("claude", SESSION) == (None, recall_start.NO_FORK)


def test_the_listener_keeps_its_last_good_settings_while_the_file_is_in_error(
        started, monkeypatch):
    from moxi.config import ConfigError

    from moxi_memory import settings

    listener._good_subconscious = None
    good = listener.subconscious_settings()
    monkeypatch.setattr(listener, "_good_subconscious", dict(good, limit_s=7))

    def refuse(env=None):
        raise ConfigError(None, "subconscious.limit_s", "is 0, below 1", "fix it")

    monkeypatch.setattr(settings, "read", refuse)
    listener.subconscious(event())
    [(_s, recall_id)] = started
    assert records.read(SESSION, recall_id)["settings"]["limit_s"] == 7


def test_the_listener_reports_a_config_error_once_good_read_or_not(monkeypatch, caplog):
    import logging

    from moxi.config import ConfigError

    from moxi_memory import settings

    monkeypatch.setattr(listener, "_good_subconscious", None)
    monkeypatch.setattr(listener, "_reported_error", None)

    def refuse(env=None):
        raise ConfigError(None, "subconscious.limit_s", "is 0, below 1", "fix it")

    monkeypatch.setattr(settings, "read", refuse)
    with caplog.at_level(logging.WARNING, logger=listener.__name__):
        assert listener.subconscious_settings() == recall_start.default_settings()
        listener.subconscious_settings()
    [said] = [r.getMessage() for r in caplog.records]
    assert "the default settings" in said and "is 0, below 1" in said


def test_a_child_that_cannot_start_loses_the_recall(monkeypatch):
    def boom(session_id, recall_id):
        raise FileNotFoundError("no moxi-memory-subconscious")

    record = recall_start.begin(SESSION, "claude", start="prompt", text="t", senders=(),
                                settings=recall_start.default_settings(), cwd=None,
                                notes_dir=None, route_name="subagent", spawn=boom)
    assert record["state"] == "lost" and record["reason"] == "spawn"
    [line] = recalls.read_lines(SESSION)
    assert line["state"] == "lost" and line["reason"] == "spawn"


# -- the first search and the brief ----------------------------------------------------

def test_keywords_leave_out_common_and_short_words():
    assert recall_brief.keywords("Why did the Ingest lock stall? the ingest, again") == [
        "did", "Ingest", "lock", "stall"]
    assert recall_brief.keywords("ok, go on") == []


def hit(session_id, project="shop"):
    return types.SimpleNamespace(session_id=session_id, harness="claude", time=1.7e9,
                                 project=project, snippet="the «lock» stalled",
                                 agent_id=None)


class FakeSearch:
    def __init__(self, by_project):
        self.by_project = by_project
        self.asked = []

    def __call__(self, query, *, project=None, limit=10):
        self.asked.append((query, project))
        return types.SimpleNamespace(hits=tuple(self.by_project.get(project, ()))[:limit])


def no_notes(cwd, override):
    return None, None


def test_project_first_runs_both_parts_every_time(tmp_path, monkeypatch):
    monkeypatch.setattr("moxi_memory.labels.project_label", lambda cwd: "shop")
    search = FakeSearch({"shop": [hit(SESSION), hit("other-1")],
                         None: [hit("other-1"), hit("wide-1", project="farm")]})
    found = recall_brief.prefetch("ingest lock stall", session_id=SESSION,
                                  cwd=str(tmp_path), notes_dir=None, scope="project-first",
                                  hits=8, search=search, places=no_notes)
    assert [p.name for p in found.parts] == ["project", "beyond"]
    # the project's part first; beyond it, the sessions it showed left out
    assert [h.session_id for h in found.part("project").hits] == ["other-1"]
    assert [h.session_id for h in found.part("beyond").hits] == ["wide-1"]
    assert search.asked == [("ingest lock stall", "shop"), ("ingest lock stall", None)]
    assert found.counts() == {"project": 1, "beyond": 1}


def test_the_part_beyond_keeps_its_full_count_past_the_sessions_it_leaves_out(
        tmp_path, monkeypatch):
    monkeypatch.setattr("moxi_memory.labels.project_label", lambda cwd: "shop")
    shown = [hit("own-0"), hit("own-1")]
    wide = [hit(f"own-{i % 2}") for i in range(60)] + [hit("wide-1"), hit("wide-2")]
    search = FakeSearch({"shop": shown, None: wide})
    found = recall_brief.prefetch("ingest", session_id=SESSION, cwd=str(tmp_path),
                                  notes_dir=None, scope="project-first", hits=2,
                                  search=search, places=no_notes)
    assert [h.session_id for h in found.part("beyond").hits] == ["wide-1", "wide-2"]
    # the first ask (2 + 50 hits) held only shown sessions, so it asked for more
    assert len(search.asked) == 3


def test_project_and_everywhere_run_one_part(tmp_path, monkeypatch):
    monkeypatch.setattr("moxi_memory.labels.project_label", lambda cwd: "shop")
    search = FakeSearch({"shop": [hit("own-1")], None: [hit("wide-1")]})
    found = recall_brief.prefetch("ingest", session_id=SESSION, cwd=str(tmp_path),
                                  notes_dir=None, scope="project", hits=8, search=search,
                                  places=no_notes)
    assert [h.session_id for h in found.hits] == ["own-1"]
    assert found.counts() == {"project": 1, "beyond": None}
    found = recall_brief.prefetch("ingest", session_id=SESSION, cwd=str(tmp_path),
                                  notes_dir=None, scope="everywhere", hits=8, search=search,
                                  places=no_notes)
    assert [h.session_id for h in found.hits] == ["wide-1"]
    assert found.counts() == {"project": None, "beyond": 1}


def test_a_session_with_no_project_label_searches_everywhere(monkeypatch):
    search = FakeSearch({None: [hit("wide-1")]})
    found = recall_brief.prefetch("ingest", session_id=SESSION, cwd=None, notes_dir=None,
                                  scope="project-first", hits=8, search=search,
                                  places=no_notes)
    assert found.scope == "everywhere" and found.project is None
    assert search.asked == [("ingest", None)]


def notes_tree(tmp_path):
    root = tmp_path / "notes"
    own = root / "shop"
    own.mkdir(parents=True)
    (own / "a.md").write_text("one\nthe ingest lock stalled at noon\nthree\n")
    (own / "b.md").write_text("nothing here\n")
    (root / "farm").mkdir()
    (root / "farm" / "c.md").write_text("ingest lock again\n")
    (root / "farm" / "d.json").write_text('{"ingest": "lock"}\n')
    return root, own


def test_notes_come_from_the_project_then_the_root_less_what_it_showed(tmp_path,
                                                                        monkeypatch):
    monkeypatch.setattr("moxi_memory.labels.project_label", lambda cwd: "shop")
    root, own = notes_tree(tmp_path)
    found = recall_brief.prefetch("ingest lock", session_id=SESSION, cwd=str(tmp_path),
                                  notes_dir=None, scope="project-first", hits=8,
                                  search=FakeSearch({}),
                                  places=lambda cwd, o: (str(own), str(root)))
    [(path, lines)] = found.part("project").notes
    assert path.endswith("a.md")
    assert lines == ["1: one", "2: the ingest lock stalled at noon", "3: three"]
    # the root's part leaves out a.md (shown) and reads only Markdown files
    assert [p for p, _ in found.part("beyond").notes] == [str(root / "farm" / "c.md")]
    assert found.notes_dirs == [str(root)] and found.project_dir == str(own)


def test_a_file_already_shown_is_known_by_its_resolved_path(tmp_path, monkeypatch):
    monkeypatch.setattr("moxi_memory.labels.project_label", lambda cwd: "shop")
    root, own = notes_tree(tmp_path)
    link = tmp_path / "shop-link"
    try:
        link.symlink_to(own, target_is_directory=True)
    except OSError:
        pytest.skip("this system makes no symlink here")
    found = recall_brief.prefetch("ingest lock", session_id=SESSION, cwd=str(tmp_path),
                                  notes_dir=None, scope="project-first", hits=8,
                                  search=FakeSearch({}),
                                  places=lambda cwd, o: (str(link), str(root)))
    assert [p for p, _ in found.part("project").notes] == [str(link / "a.md")]
    assert [p for p, _ in found.part("beyond").notes] == [str(root / "farm" / "c.md")]


def test_the_notes_are_read_whole_and_each_line_cut(tmp_path):
    root = tmp_path / "notes"
    for i in range(30):
        (root / f"d{i:02}").mkdir(parents=True)
        for j in range(250):
            (root / f"d{i:02}" / f"n{j:03}.md").write_text("nothing\n")
    (root / "zz").mkdir()
    (root / "zz" / "late.md").write_text("ingest " + "x" * 400 + "\n")
    [(path, lines)] = recall_brief._notes(["ingest"], [str(root)])
    assert path.endswith("late.md")
    assert lines == ["1: " + ("ingest " + "x" * 400)[:200]]


def test_files_that_match_alike_are_ordered_by_path(tmp_path):
    for name in ("b.md", "a.md", "c.md", "d.md"):
        (tmp_path / name).write_text("ingest\n")
    shown = [p for p, _ in recall_brief._notes(["ingest"], [str(tmp_path)])]
    assert shown == [str(tmp_path / n) for n in ("a.md", "b.md", "c.md")]


def test_a_notes_directory_that_does_not_exist_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr("moxi_memory.labels.project_label", lambda cwd: "shop")
    root, _own = notes_tree(tmp_path)
    missing = tmp_path / "notes" / ".local" / "state" / "shop"
    found = recall_brief.prefetch("ingest lock", session_id=SESSION, cwd=str(tmp_path),
                                  notes_dir=None, scope="project-first", hits=8,
                                  search=FakeSearch({}),
                                  places=lambda cwd, o: (str(missing), str(root)))
    assert found.project_dir is None and found.notes_dirs == [str(root)]
    assert found.part("project").notes == []
    assert len(found.part("beyond").notes) == 2


def test_a_project_notes_directory_outside_the_root_is_named_too(tmp_path, monkeypatch):
    monkeypatch.setattr("moxi_memory.labels.project_label", lambda cwd: "shop")
    root, _own = notes_tree(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    found = recall_brief.prefetch("ingest", session_id=SESSION, cwd=str(tmp_path),
                                  notes_dir=None, scope="project-first", hits=8,
                                  search=FakeSearch({}),
                                  places=lambda cwd, o: (str(outside), str(root)))
    assert found.notes_dirs == [str(root), str(outside)]


def test_a_prompt_with_no_keyword_runs_no_search():
    search = FakeSearch({})
    found = recall_brief.prefetch("ok, go on", session_id=SESSION, cwd=None,
                                  notes_dir=None, scope="project-first", hits=8,
                                  search=search, places=no_notes)
    assert search.asked == []
    assert recall_brief.prefetch_text(found, 8) == recall_brief.NO_KEYWORD


def brief(start="prompt", *, scope="project-first", project_dir="/notes/shop",
          parts=None, **changes):
    settings = dict(recall_start.default_settings(), **changes)
    if parts is None:
        parts = [recall_brief.Part("project", hits=[hit("other-1")]),
                 recall_brief.Part("beyond", hits=[hit("wide-1", project="farm")])]
    found = recall_brief.Prefetch(["ingest"], scope=scope, parts=parts, project="shop",
                                  project_dir=project_dir, notes_dirs=["/notes"],
                                  notes_root="/notes")
    return recall_brief.brief(start=start, text="why did the ingest stall", session_id=SESSION,
                              found=found, settings=settings)


def test_the_brief_holds_the_prompt_the_hits_and_the_limits():
    text = brief()
    assert "The user has just sent the prompt below" in text
    assert "The prompt:\n<<<\nwhy did the ingest stall\n>>>" in text
    assert "- session other-1 (claude)" in text
    assert "moxi sessions read other-1" in text
    assert 'moxi memory search "<a few keywords>"\n' in text
    assert f"Skip any hit from session {SESSION}" in text
    assert "- Notes: the files under /notes." in text
    assert "at most 8 tool calls and about 60 seconds" in text
    assert "moxi memory search, moxi sessions read, grep" in text
    assert "End of recall." in text
    assert 'Recalled from past sessions and notes for "why did the ingest stall":' in text
    assert "{" not in text


def test_project_first_shows_the_project_first_and_names_it():
    text = brief()
    project_at = text.index("In this project, shop:\n- session other-1")
    assert text.index("Beyond this project:\n- session wide-1") > project_at
    assert ("These search past this project too. This project is shop: its hits are this "
            "project's own record, and --project shop searches its sessions alone. Its "
            "own notes are under /notes/shop.") in " ".join(text.split())
    assert "--project shop\n" not in text


def test_a_part_that_found_nothing_says_so():
    text = brief(parts=[recall_brief.Part("project"),
                        recall_brief.Part("beyond", hits=[hit("wide-1")])],
                 project_dir=None)
    assert "In this project, shop:\n(found nothing here)\nBeyond this project:" in text
    assert "Its own notes" not in text


def test_the_project_scope_searches_with_project_and_names_no_project_line():
    text = brief(scope="project", parts=[recall_brief.Part("project",
                                                           hits=[hit("other-1")])])
    assert 'moxi memory search "<a few keywords>" --project shop\n' in text
    assert "In this project" not in text and "These search past" not in text


def test_the_brief_asks_for_a_first_answer_at_once_and_a_stream_after_it():
    text = " ".join(brief().split())
    assert ("Give a first answer as soon as you have one useful point: when the first "
            "search above already holds one, answer from it at once, before any tool "
            "call.") in text
    assert "Your first answer never ends the recall" in text
    assert "keep digging while you have tool calls and time left" in text
    assert "with only what is new since your last answer" in text
    assert "End a later answer with the line End of recall." in text


def test_the_brief_says_a_backslash_is_refused():
    text = " ".join(brief().split())
    assert "no backslash, (, ), $ or ! anywhere, even inside quotes" in text
    assert 'grep -E "one|other"' in text
    assert "the Grep tool, which takes any pattern" in text
    for refused in ("\\", "(", ")", "$", "!"):
        assert refused in recall_guard.REFUSED_CHARS
    assert recall_guard.plain_read('grep -E "one|other" notes.md', ["grep"])
    assert not recall_guard.plain_read('grep "one\\|other" notes.md', ["grep"])


def test_an_asked_recall_and_one_round_change_the_brief():
    text = brief(start="asked", max_rounds=1)
    assert "The main agent has asked for a recall on the topic below" in text
    assert "The topic:\n" in text
    assert "End of recall." not in text and "first answer" not in text


# -- the child's first steps -----------------------------------------------------------

def a_record():
    return recall_start.begin(SESSION, "claude", start="prompt", text="ingest lock stall",
                              senders=(), settings=recall_start.default_settings(),
                              cwd=None, notes_dir=None, route_name="subagent",
                              spawn=lambda s, r: None)


def test_the_child_opens_its_line_with_its_identity_then_writes_the_brief(monkeypatch):
    record = a_record()
    recall = recall_child.Recall(records.read(SESSION, record["recall"]))
    recall_child.open_line(recall)
    line = recalls.find_line(SESSION, record["recall"])
    assert line["state"] == "open" and line["child"]["pid"] > 0
    assert line["head"] == "ingest lock stall" and line["route"] == "subagent"
    recall_child.write_brief(recall, search=FakeSearch({None: [hit("other-1")]}),
                             places=no_notes)
    line = recalls.find_line(SESSION, record["recall"])
    # a record with no cwd has no project part: its one part is the one beyond
    assert line["prefetch_hits"] == 1
    assert line["project_hits"] is None and line["beyond_hits"] == 1
    assert "widened" not in line
    assert "other-1" in records.read(SESSION, record["recall"])["brief"]


def test_a_child_whose_config_read_is_refused_loses_the_recall(monkeypatch):
    record = a_record()
    monkeypatch.setattr(recall_child, "read_config", lambda: "limit_s is 0, below 1")
    assert recall_child.run(SESSION, record["recall"]) == 0
    line = recalls.find_line(SESSION, record["recall"])
    assert line["state"] == "lost" and line["reason"] == "config"
    assert line["error"] == "limit_s is 0, below 1"
    assert records.read(SESSION, record["recall"])["state"] == "lost"


def test_the_child_refuses_an_unknown_recall():
    assert recall_child.main(["--session", SESSION, "--recall", "nope"]) == 2


# -- memory's own earlier answers in a prompt ----------------------------------------

def comms_event(senders, *, mode="automatic"):
    found = event(mode=mode, own=False, by="comms",
                  text="  - the lock is held by the writer\n  please also check the reader")
    found.delivery_senders = senders
    return found


@pytest.fixture
def recall_names(monkeypatch):
    """comms reads `me-subconscious` back as this session's recall sender."""
    from moxi_memory import recall_send

    monkeypatch.setattr(recall_send, "session_parent", lambda session_id, env: "me")
    monkeypatch.setattr(recall_send, "recall_senders", lambda senders, parent: [
        s for s in senders if parent == "me" and s == "me-subconscious"])
    monkeypatch.setattr(listener, "subconscious_settings", lambda: dict(
        recall_start.default_settings(), trigger="user-and-comms"))


def test_a_delivery_only_from_this_sessions_recalls_starts_no_recall(started, recall_names):
    listener.subconscious(comms_event(["me-subconscious"]))
    assert started == [] and recalls.logged_sessions() == []


def test_a_delivery_only_from_recalls_resets_no_count(started, recall_names):
    with recalls.session_lock(SESSION):
        recall_start.write_asked(SESSION, 2)
    listener.subconscious(comms_event(["me-subconscious"], mode="on-demand"))
    assert recall_start.asked(SESSION) == 2
    listener.subconscious(comms_event(["me-subconscious", "builder"], mode="on-demand"))
    assert recall_start.asked(SESSION) == 0


def test_a_name_of_the_form_comms_does_not_read_back_follows_the_trigger(started,
                                                                         recall_names):
    listener.subconscious(comms_event(["other-subconscious"]))
    [(_, recall_id)] = started
    assert records.read(SESSION, recall_id)["recall_senders"] == []


def test_a_recall_with_other_mail_leaves_memorys_own_lines_to_recalled(started,
                                                                       recall_names):
    with recalls.session_lock(SESSION):
        recalls.put_line(SESSION, {"recall": "earlier", "state": "back", "rounds": [
            {"round": 1, "text": "Recalled ...:\n- the lock is held by the writer"}]})
    listener.subconscious(comms_event(["me-subconscious", "builder"]))
    [(_, recall_id)] = started
    record = records.read(SESSION, recall_id)
    assert record["recall_senders"] == ["me-subconscious"]
    recall = recall_child.Recall(record)
    search = FakeSearch({})
    text = recall_child.write_brief(recall, search=search, places=no_notes)
    assert all("writer" not in query for query, _ in search.asked)
    assert "Already recalled for the main agent" in text
    assert "<<<\n- the lock is held by the writer\n>>>" in text
    prompt_part = text.split("Already recalled")[0]
    assert "please also check the reader" in prompt_part
    assert "held by the writer" not in prompt_part


def test_split_recalled_matches_stripped_lines_only():
    kept, recalled = recall_brief.split_recalled("  - a\n- b changed\n\n- c",
                                                 frozenset({"- a", "- b", "- c"}))
    assert kept == "- b changed\n" and recalled == ["- a", "- c"]
