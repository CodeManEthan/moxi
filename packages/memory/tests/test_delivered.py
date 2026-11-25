"""Delivered messages in tier 1: a plugin's (comms mail among them), a channel's, a
peer's, a coordinator's and a background task's message is indexed with its writer, with
or without its meta flag; a message the harness composed, a compaction summary and a
thought are not. `moxi memory search` finds them and says who delivered them, and a
generation made before memory kept them is named by status."""

import json

import pytest
from moxi_sessions import api as sessions

from moxi_memory import cli, index, ingest, links

T = sessions.testing
START = 1_700_000_000.0
ORIGINS = [("plugin", "hook"), ("channel", "channel"), ("peer", "peer"),
           ("coordinator", "coordinator"), ("task-notification", "task")]
TASK_TEXT = ("<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n"
             "the quokka probe finished</task-notification>")


def config():
    return {"harnesses": ["claude"], "index.include_subagents": False,
            "index.tail_chars": 1000, "redact.sets": ["credentials", "broad"]}


def run(mode="walk"):
    return ingest.run(mode, None, None, config=config(), launcher=links.Link(links.NONE),
                      then=lambda args: None)


def transcript(n=1):
    t = T.ClaudeTranscript(sessions.root("claude"),
                           session_id=f"0190b000-0000-7000-8000-{n:012d}", start=START)
    t._count = 1000 * n
    return t


def writers():
    with index.open_read() as reader:
        return {text: (kind, writer) for kind, text, writer in reader.conn.execute(
            "SELECT kind, text, writer FROM rows")}


def memory(capsys, *argv):
    code = cli.main(list(argv), prog="moxi memory")
    out, err = capsys.readouterr()
    return code, out, err


@pytest.mark.parametrize("meta", [False, True], ids=["plain", "meta"])
def test_each_delivered_origin_gives_a_tier_one_row_with_its_writer(meta):
    t = transcript()
    fields = {"isMeta": True} if meta else {}
    for origin, _ in ORIGINS:
        t.user(f"delivered by the {origin}", origin=origin, **fields)
    t.write()
    run()
    got = writers()
    for origin, writer in ORIGINS:
        assert got[f"delivered by the {origin}"] == ("user", writer)


def test_composed_lines_summaries_and_thoughts_give_no_row():
    t = transcript()
    t.user("typed by the person", origin="human")
    t.user("a line the harness composed", isMeta=True)
    t.user("an origin the harness doesn't name", origin="something-new", isMeta=True)
    t.compaction(summary="the summary of the earlier session")
    t.request().thinking("a thought about the widgets")
    t.write()
    run()
    assert set(writers()) == {"typed by the person"}


def test_a_task_delivery_that_opens_with_its_wrapper_is_kept():
    t = transcript()
    t.user(TASK_TEXT, origin="task-notification", isMeta=True)
    t.write()
    run()
    assert writers()[TASK_TEXT] == ("user", "task")


def test_search_finds_a_comms_delivery_and_names_its_writer(capsys):
    t = transcript()
    t.user("mail from the reviewer about the wombat", origin="plugin", isMeta=True)
    t.user("the wombat question I typed", origin="human")
    t.write()
    run()
    code, out, _ = memory(capsys, "search", "wombat")
    assert code == 0
    heads = [line for line in out.splitlines() if line[:1].isdigit()]
    assert len(heads) == 2
    assert {head.rsplit("  ", 1)[1] for head in heads} == {"hook", "user"}
    code, out, _ = memory(capsys, "search", "wombat", "--json")
    hits = {h["snippet"]: h for h in json.loads(out)["hits"]}
    mail = next(h for s, h in hits.items() if "reviewer" in s)
    typed = next(h for s, h in hits.items() if "typed" in s)
    assert (mail["kind"], mail["writer"]) == ("user", "hook")
    assert (typed["kind"], typed["writer"]) == ("user", "person")


def test_search_finds_a_task_delivery(capsys):
    t = transcript()
    t.user(TASK_TEXT, origin="task-notification", isMeta=True)
    t.write()
    run()
    code, out, _ = memory(capsys, "search", "quokka", "--json")
    hits = json.loads(out)["hits"]
    assert code == 0 and [h["writer"] for h in hits] == ["task"]


def _drop_the_rule_key():
    with index.writer() as w:
        conn = w.connect()
        try:
            with conn:
                conn.execute("DELETE FROM meta WHERE key = ?", (index.DELIVERED_KEY,))
        finally:
            conn.close()


def test_status_names_the_reindex_for_a_generation_made_before_the_rule(capsys):
    t = transcript()
    t.user("typed by the person", origin="human")
    t.write()
    run()
    code, out, _ = memory(capsys, "status")
    assert code == 0 and "delivered messages" not in out
    assert json.loads(memory(capsys, "status", "--json")[1])["keeps_delivered"] is True
    _drop_the_rule_key()
    code, out, _ = memory(capsys, "status")
    line = next(line for line in out.splitlines() if line.startswith("delivered messages:"))
    assert "moxi memory reindex" in line
    assert json.loads(memory(capsys, "status", "--json")[1])["keeps_delivered"] is False
    run("rebuild")
    code, out, _ = memory(capsys, "status")
    assert "delivered messages" not in out


def test_a_rebuild_of_an_old_generation_adds_the_deliveries_it_had_left_out():
    t = transcript()
    t.user("mail the old rule dropped", origin="plugin")
    t.write()
    run()
    with index.writer() as w:
        conn = w.connect()
        try:
            with conn:
                conn.execute("DELETE FROM rows WHERE writer = 'hook'")
                conn.execute("DELETE FROM meta WHERE key = ?", (index.DELIVERED_KEY,))
        finally:
            conn.close()
    assert "mail the old rule dropped" not in writers()
    run("rebuild")
    assert writers()["mail the old rule dropped"] == ("user", "hook")
    with index.open_read() as reader:
        assert index.keeps_delivered(reader.conn)
