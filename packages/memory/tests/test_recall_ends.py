"""How a recall ends outside its stream: a person's discard, the day sweep of records,
and the on-demand command with its refusals, its cap and its start line."""

import types

import pytest
from moxi.config import ConfigError

from moxi_memory import (cli, listener, recall_child, recall_discard, recall_start,
                         recall_stream, recalls, records)

SESSION = "0190b000-0000-7000-8000-000000000004"
ON_DEMAND = {recall_start.SUBCONSCIOUS_ENV: "on-demand"}


def memory(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def a_recall(**line):
    record = recall_start.begin(SESSION, "claude", start="prompt", text="ingest lock",
                                senders=(), settings=recall_start.default_settings(),
                                cwd=None, notes_dir=None, route_name="subagent",
                                spawn=lambda s, r: None)
    recall = recall_child.Recall(records.read(SESSION, record["recall"]))
    recall_child.open_line(recall)
    if line:
        recall.put(**line)
    return recall


# -- discard ---------------------------------------------------------------------------

def test_discard_refuses_a_recall_it_does_not_know(capsys):
    code, _, err = memory(capsys, "subconscious", "discard", "nosuchrecall")
    assert code == 3 and "no recall nosuchrecall" in err


@pytest.mark.parametrize("state", ["open", "back", "lost"])
def test_discard_refuses_every_line_the_subagent_route_writes(capsys, state):
    recall = a_recall(state=state)
    code, _, err = memory(capsys, "subconscious", "discard", recall.recall_id)
    assert code == 3 and f"is {state}" in err
    assert recalls.find_line(SESSION, recall.recall_id)["state"] == state


def test_a_discard_sets_the_line_and_the_record_then_acks_and_clears_the_mark(
        monkeypatch):
    monkeypatch.setattr(recalls, "DISCARDABLE", ("back",))
    recall = a_recall(state="back", ref="x:2", kept=True)
    acked = []

    def ack(session_id, ref):
        line = recalls.find_line(session_id, recall.recall_id)
        acked.append((ref, line["state"], line["kept"]))

    line = recall_discard.discard(recall.recall_id, by="you", ack=ack, now=50.0)
    assert acked == [("x:2", "discarded", True)]
    assert line["state"] == "discarded" and line["kept"] is False
    assert line["discarded_by"] == "you" and line["discarded_at"] == 50.0
    assert records.read(SESSION, recall.recall_id)["state"] == "ended"


def test_a_discard_whose_ack_is_refused_leaves_the_mark(monkeypatch):
    monkeypatch.setattr(recalls, "DISCARDABLE", ("back",))
    recall = a_recall(state="back", ref="x:1", kept=True)

    def refuse(session_id, ref):
        raise RuntimeError("the endpoint did not answer")

    line = recall_discard.discard(recall.recall_id, ack=refuse)
    assert line["state"] == "discarded" and line["kept"] is True


def test_the_owner_asks_no_further_round_of_a_discarded_line():
    recall = a_recall()

    class Route:
        name = "subagent"
        asked = []

        def first(self, text, *, ref, round_timeout_s):
            self.asked.append(ref)
            return types.SimpleNamespace(id="d1")

        def next(self, prior, text, *, ref, round_timeout_s):
            self.asked.append(ref)
            return types.SimpleNamespace(id="d2")

        def started(self, handle):
            return "agent-1"

        def ended(self, handle, timeout_s):
            from moxi_turns import Ended

            return Ended(turn_id=None, outcome="completed", answer="- a")

        def ack(self, ref):
            pass

    route = Route()
    assert recall_stream.open_round(recall, 1, f"{recall.recall_id}:1")
    with recalls.session_lock(SESSION):
        line = recalls.find_line(SESSION, recall.recall_id)
        line["state"] = "discarded"
        recalls.put_line(SESSION, line)
    assert recall_stream.run(recall, route, "the brief") == "discarded"
    assert route.asked == []
    assert recalls.find_line(SESSION, recall.recall_id)["state"] == "discarded"


# -- the day sweep ---------------------------------------------------------------------

def test_the_day_sweep_removes_old_records_but_not_one_whose_line_is_kept():
    old = a_recall(state="back")
    kept = a_recall(state="open", ref="k:1", kept=True)
    young = a_recall(state="back")
    for recall in (old, kept):
        with recalls.session_lock(SESSION):
            records.write(dict(records.read(SESSION, recall.recall_id), at=1000.0))
    now = 1000.0 + records.SWEEP_AFTER_S
    with recalls.session_lock(SESSION):
        records.write(dict(records.read(SESSION, young.recall_id), at=now - 10))
    assert records.sweep_old(now=now) == [old.recall_id]
    assert records.read(SESSION, old.recall_id) is None
    assert records.read(SESSION, kept.recall_id) is not None
    assert records.read(SESSION, young.recall_id) is not None
    # the log keeps the line of a swept record
    assert recalls.find_line(SESSION, old.recall_id) is not None


# -- on demand -------------------------------------------------------------------------

@pytest.fixture
def spawned(monkeypatch):
    started = []
    monkeypatch.setattr(recall_start, "route",
                        lambda harness, session_id: (recall_start.SUBAGENT, None))
    monkeypatch.setattr(recall_start, "spawn_child",
                        lambda session_id, recall_id: started.append(recall_id))
    return started


def here(env=ON_DEMAND):
    return (SESSION, "claude", dict(env), "/work/project")


def test_on_demand_starts_an_asked_recall_and_counts_it(spawned):
    record = recall_start.ask("  the ingest\nlock  ", session=here())
    assert record["start"] == "asked" and record["text"] == "the ingest lock"
    assert record["cwd"] == "/work/project" and record["route"] == "subagent"
    assert spawned == [record["recall"]]
    assert recall_start.asked(SESSION) == 1


def test_on_demand_refuses_past_its_cap_until_a_counted_prompt_resets_it(spawned):
    recall_start.ask("one", session=here())
    recall_start.ask("two", session=here())
    with pytest.raises(recall_start.Refused, match="used up"):
        recall_start.ask("three", session=here())
    recall_start.reset_asked(SESSION)
    recall_start.ask("three", session=here())
    assert len(spawned) == 3


def test_a_newer_on_demand_recall_hurries_the_running_one(spawned):
    first = recall_start.ask("one", session=here())
    recall_start.ask("two", session=here())
    assert records.read(SESSION, first["recall"])["state"] == "hurried"


@pytest.mark.parametrize("env, match", [
    ({}, "off in this session"),
    ({recall_start.SUBCONSCIOUS_ENV: "automatic"}, "automatically"),
])
def test_on_demand_refuses_a_session_off_or_automatic(spawned, env, match):
    with pytest.raises(recall_start.Refused, match=match):
        recall_start.ask("topic", session=here(env))
    assert spawned == []


def test_on_demand_refuses_outside_a_session(spawned, monkeypatch):
    monkeypatch.setattr(recall_start, "this_session", lambda: None)
    with pytest.raises(recall_start.Refused, match="no session"):
        recall_start.ask("topic")


def test_on_demand_with_no_route_logs_off_and_says_why(monkeypatch):
    monkeypatch.setattr(recall_start, "route",
                        lambda harness, session_id: (None, recall_start.NO_FORK))
    with pytest.raises(recall_start.Refused, match=recall_start.NO_FORK):
        recall_start.ask("topic", session=here())
    assert recalls.read_lines(SESSION)[-1]["state"] == "off"


def test_on_demand_refuses_a_config_file_in_error(spawned, monkeypatch):
    from moxi_memory import settings

    def bad():
        raise ConfigError(None, "subconscious.limit_s", "0 is below 1", "set 1 to 3600")

    monkeypatch.setattr(settings, "read", bad)
    with pytest.raises(recall_start.Refused, match="limit_s"):
        recall_start.ask("topic", session=here())
    assert spawned == [] and recall_start.asked(SESSION) == 0


def test_the_recall_command_prints_its_start_line_or_its_refusal(capsys, spawned,
                                                                 monkeypatch):
    monkeypatch.setattr(recall_start, "this_session", here)
    code, out, _ = memory(capsys, "subconscious", "recall", "the ingest lock")
    assert code == 0
    assert out.strip() == ('Started a recall on "the ingest lock". Its points come back '
                           "to you as it finds them; keep working.")
    code, out, _ = memory(capsys, "subconscious", "recall", "x", "--json")
    assert code == 0 and '"topic": "x"' in out
    code, _, err = memory(capsys, "subconscious", "recall", "y")
    assert code == 3 and "used up (2)" in err


def test_the_start_line_names_the_command_only_on_demand():
    def start(env):
        return listener.start(types.SimpleNamespace(event="start", env=env, cwd=None,
                                                    harness="claude", session_id=SESSION))

    assert recall_start.START_LINE in start(ON_DEMAND)
    assert recall_start.START_LINE not in start({recall_start.SUBCONSCIOUS_ENV:
                                                 "automatic"})
    assert recall_start.START_LINE not in start({})
