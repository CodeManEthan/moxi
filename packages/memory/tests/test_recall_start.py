"""Starting a recall: the mode, which prompts count, the route checked at every try, the
record and its child, and the child's first steps (its line, its config read, the first
search and the brief)."""

import types

import pytest

from moxi_memory import (listener, recall_brief, recall_child, recall_start, recalls,
                         records)

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
        return types.SimpleNamespace(hits=tuple(self.by_project.get(project, ())))


def no_notes(cwd, override):
    return None, None


def test_the_prefetch_leaves_out_this_session_and_stays_in_the_project(tmp_path,
                                                                        monkeypatch):
    monkeypatch.setattr("moxi_memory.labels.project_label", lambda cwd: "shop")
    search = FakeSearch({"shop": [hit(SESSION), hit("other-1")], None: [hit("wide-1")]})
    found = recall_brief.prefetch("ingest lock stall", session_id=SESSION,
                                  cwd=str(tmp_path), notes_dir=None, scope="project-first",
                                  hits=8, search=search, places=no_notes)
    assert [h.session_id for h in found.hits] == ["other-1"]
    assert found.widened is False and found.project == "shop"
    assert search.asked == [("ingest lock stall", "shop")]


def test_project_first_goes_wide_only_when_the_project_finds_nothing(tmp_path,
                                                                     monkeypatch):
    monkeypatch.setattr("moxi_memory.labels.project_label", lambda cwd: "shop")
    search = FakeSearch({"shop": [hit(SESSION)], None: [hit("wide-1")]})
    found = recall_brief.prefetch("ingest lock", session_id=SESSION, cwd=str(tmp_path),
                                  notes_dir=None, scope="project-first", hits=8,
                                  search=search, places=no_notes)
    assert [h.session_id for h in found.hits] == ["wide-1"]
    assert found.widened is True and found.project is None
    found = recall_brief.prefetch("ingest lock", session_id=SESSION, cwd=str(tmp_path),
                                  notes_dir=None, scope="project", hits=8,
                                  search=FakeSearch({}), places=no_notes)
    assert found.hits == [] and found.widened is False


def test_a_notes_hit_in_the_project_keeps_the_search_there(tmp_path, monkeypatch):
    monkeypatch.setattr("moxi_memory.labels.project_label", lambda cwd: "shop")
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "a.md").write_text("one\nthe ingest lock stalled at noon\nthree\n")
    (notes_dir / "b.md").write_text("nothing here\n")
    found = recall_brief.prefetch("ingest lock", session_id=SESSION, cwd=str(tmp_path),
                                  notes_dir=None, scope="project-first", hits=8,
                                  search=FakeSearch({}),
                                  places=lambda cwd, o: (str(notes_dir), str(tmp_path)))
    assert found.widened is False
    [(path, lines)] = found.notes
    assert path.endswith("a.md")
    assert lines == ["1: one", "2: the ingest lock stalled at noon", "3: three"]


def test_a_prompt_with_no_keyword_runs_no_search():
    search = FakeSearch({})
    found = recall_brief.prefetch("ok, go on", session_id=SESSION, cwd=None,
                                  notes_dir=None, scope="project-first", hits=8,
                                  search=search, places=no_notes)
    assert search.asked == []
    assert recall_brief.prefetch_text(found, 8) == recall_brief.NO_KEYWORD


def brief(start="prompt", **changes):
    settings = dict(recall_start.default_settings(), **changes)
    found = recall_brief.Prefetch(["ingest"], hits=[hit("other-1")], project="shop",
                                  notes_dirs=["/notes/shop"], notes_root="/notes")
    return recall_brief.brief(start=start, text="why did the ingest stall", session_id=SESSION,
                              found=found, settings=settings)


def test_the_brief_holds_the_prompt_the_hits_and_the_limits():
    text = brief()
    assert "The user has just sent the prompt below" in text
    assert "The prompt:\n<<<\nwhy did the ingest stall\n>>>" in text
    assert "- session other-1 (claude)" in text
    assert "moxi sessions read other-1" in text
    assert 'moxi memory search "<a few keywords>" --project shop' in text
    assert f"Skip any hit from session {SESSION}" in text
    assert "- Notes: the files under /notes/shop." in text
    assert "grep /notes." in text
    assert "at most 8 tool calls and about 60 seconds" in text
    assert "moxi memory search, moxi sessions read, grep" in text
    assert "End of recall." in text
    assert 'Recalled from past sessions and notes for "why did the ingest stall":' in text
    assert "{" not in text


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
    assert recalls.find_line(SESSION, record["recall"])["prefetch_hits"] == 1
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
