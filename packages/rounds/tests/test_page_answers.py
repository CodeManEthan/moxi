"""The operator's answers sent from rounds' page, run in process as its page server runs
them, with a stand-in for comms' API: who records each kind, the order (record, then
tell), one message per recipient, what a draft with no recipient becomes, a message
comms refuses and its `Send again`, and an act posted twice, before and after the
server's store of receipts is emptied as a restart empties it."""

from __future__ import annotations

import asyncio
import secrets
import threading
from types import SimpleNamespace

import pytest
from test_page_acts import (  # noqa: F401 -- the kit is a fixture
    C1_AT, PROJECT, ROUND, SAID, everything_logged, folded, kit)

from moxi_rounds import (actors, asking, corrections, folders, listing, log, marking,
                         opening, pageacts, pointers, questions, ruling, write)
from moxi_rounds.pageacts import (ACCEPT, CORRECT, DONE, EACH, EXPLAIN, KEPT, NOT_TOLD,
                                  PARK, PICK, READ_BACK, RECORDED, REFUSED, REJECT, SENT,
                                  UNPARK, WORDS, Draft, Receipts)

#: A second round of atlas, with a lead of its own.
OTHER = "atlas/tax-report"
#: A round of atlas with no lead, in a project with none.
LEADLESS = "atlas/payroll"


class Comms:
    """comms' API as the page uses it: each `send_as` is kept and given the next
    message id, unless its recipient is in `refuse`; `roster()` gives `bound`."""

    def __init__(self, bound=("atlas-lead", "tax-lead", "kestrel")):
        self.sent: list[tuple[str, str, tuple[str, ...], str]] = []
        self.refuse: dict[str, str] = {}
        self.bound = bound

    def send_as(self, module_name, name, to, text):
        who = to[0]
        if who in self.refuse:
            return SimpleNamespace(outcome="offline", message_id=None, recipients=(),
                                   refusal=SimpleNamespace(code="offline",
                                                           text=self.refuse[who]))
        self.sent.append((module_name, name, tuple(to), text))
        return SimpleNamespace(outcome="sent", message_id=str(1000 + len(self.sent)),
                               refusal=None,
                               recipients=(SimpleNamespace(qualified=who, delivery="live"),))

    def roster(self):
        return SimpleNamespace(bound=[SimpleNamespace(qualified=one) for one in self.bound])

    def to(self, name):
        return [text for _module, _name, to, text in self.sent if to == (name,)]


@pytest.fixture
def comms():
    return Comms()


@pytest.fixture
def rounds(kit):
    """The kit, with `tax-report` (lead `tax-lead`, Q1) and `payroll` (no lead, Q1),
    and C1, a ruling of the kit's round recorded in the past. Returns the kit's
    folder."""
    opening.open_round(OTHER, description="report the tax", by="builder-1", flow="fix",
                       lead="tax-lead")
    asking.ask(OTHER, "Which year is reported?", by="tax-lead", origin=SAID,
               options=["a=this year", "b=last year"])
    opening.open_round(LEADLESS, description="run the payroll", by="builder-1", flow="fix")
    asking.ask(LEADLESS, "Is the payroll weekly?", by="builder-1", origin=SAID,
               options=["a=weekly", "b=monthly"])
    ruling.rule(ROUND, text="The export is a CSV file.", record=SAID, by="atlas-lead",
                at=C1_AT)
    return kit


def act() -> str:
    return secrets.token_urlsafe(32)


def draft(kind, holder=ROUND, item="Q1", **more) -> Draft:
    return Draft(act=more.pop("act", None) or act(), holder=holder, item=item, kind=kind,
                 **more)


def send(drafts, *, comms, receipts=None, to=EACH):
    return pageacts.send(drafts, to, receipts=Receipts() if receipts is None else receipts,
                         comms=comms)


def events_of(holder: str) -> list[dict]:
    """`holder`'s log: a round's own, or a project's."""
    project, _, slug = holder.partition("/")
    rounds_dir = folders.home(project).rounds_dir
    return log.read(log.round_log(rounds_dir / slug) if slug else log.project_log(rounds_dir))


def by_act(holder: str, act_id: str) -> list[dict]:
    """The events of `holder`'s log whose pointer holds `act_id`."""
    return [one for one in events_of(holder) if pointers.page_act(one.get("record")) == act_id]


# -- each kind, with its recorder ------------------------------------------------------------

PAGE_DRAFTS = [
    (PICK, ROUND, "Q1", {"chose": "a"}, "ruling.recorded"),
    (ACCEPT, ROUND, "B1.M1", {}, "ruling.recorded"),
    (REJECT, ROUND, "B1.M1", {}, "ruling.recorded"),
    (READ_BACK, ROUND, "C1", {}, "ruling.shown"),
    (DONE, ROUND, "B1.W1", {"text": "checked on two phones"}, "wait.marked"),
    (PARK, ROUND, "Q2", {}, "ruling.recorded"),
    (PICK, PROJECT, "P1", {"chose": "b"}, "ruling.recorded"),
    (READ_BACK, PROJECT, "S1", {}, "ruling.shown"),
]


@pytest.mark.parametrize(("kind", "holder", "item", "more", "event_type"), PAGE_DRAFTS)
def test_the_page_records_its_kinds_with_a_page_pointer_and_tells_the_lead(
        rounds, comms, kind, holder, item, more, event_type):
    one = draft(kind, holder, item, **more)
    to = "atlas-lead" if holder == ROUND else "kestrel"

    answered = send([one], comms=comms, to=EACH if holder == ROUND else to)

    [receipt] = answered["receipts"]
    [event] = by_act(holder, one.act)
    assert event["type"] == event_type
    assert event["actor"] == actors.default()
    assert receipt["state"] == RECORDED and receipt["record"] == event["record"]
    [message] = comms.to(to)
    assert f"{holder} {item}" in message and event["record"] in message
    assert message.endswith(pageacts.NOTHING_TO_RECORD)
    assert receipt["message"]["id"] == answered["messages"][0]["id"]
    assert comms.sent[0][:2] == ("rounds", "rounds")


def test_a_pick_says_the_ruling_it_recorded(rounds, comms):
    [receipt] = send([draft(PICK, chose="a")], comms=comms)["receipts"]

    ruled = receipt["ruling"]
    assert ruled["id"] == folded(rounds).rulings[-1].id
    assert f"recorded as {ruled['id']}: " in comms.to("atlas-lead")[0]
    assert receipt["left"] == 2


def test_a_done_note_is_in_the_message_and_nowhere_in_rounds(rounds, comms):
    one = draft(DONE, item="B1.W1", text="checked on two phones")

    send([one], comms=comms)

    [event] = by_act(ROUND, one.act)
    assert "checked on two phones" not in str(event)
    assert '"checked on two phones"' in comms.to("atlas-lead")[0]


@pytest.mark.parametrize(("kind", "item", "says"), [
    (WORDS, "Q1", "moxi rounds rule atlas/billing-export Q1 --text \"nightly, at two\" "
                  "--record comms:<this message>"),
    (CORRECT, "C1", "--supersedes C1 --record comms:<this message>"),
    (EXPLAIN, "Q1", '"When does the export run?"'),
])
def test_the_agents_kinds_write_nothing_and_go_in_the_message(rounds, comms, kind, item,
                                                              says):
    before = everything_logged(rounds)
    one = draft(kind, item=item, text="nightly, at two")

    [receipt] = send([one], comms=comms)["receipts"]

    assert everything_logged(rounds) == before
    assert receipt["state"] == SENT
    [message] = comms.to("atlas-lead")
    assert says in message
    assert pageacts.NOTHING_TO_RECORD not in message or kind == EXPLAIN
    if kind != EXPLAIN:
        assert receipt["command"].endswith(f"--record comms:{receipt['message']['id']}")


# -- the order ----------------------------------------------------------------------------

def test_a_refused_record_keeps_its_draft_out_of_the_message_and_the_rest_go_on(
        rounds, comms):
    send([draft(PICK, chose="a")], comms=comms)
    comms.sent.clear()
    again = draft(PICK, chose="b")
    then = draft(PARK, item="Q2")

    first, second = send([again, then], comms=comms)["receipts"]

    assert first["state"] == REFUSED and "already ruled" in first["reason"]
    assert "--supersedes" in first["fix"]
    assert second["state"] == RECORDED
    [message] = comms.to("atlas-lead")
    assert "Q2" in message and "Q1" not in message


def test_the_page_records_before_it_sends(rounds):
    seen = []

    class Watching(Comms):
        def send_as(self, module_name, name, to, text):
            seen.append(len(events_of(ROUND)))
            return super().send_as(module_name, name, to, text)

    before = len(events_of(ROUND))
    send([draft(PICK, chose="a"), draft(PARK, item="Q2")], comms=Watching())

    assert seen == [before + 2]


def test_a_held_lock_is_refused_as_the_round_being_busy(rounds, comms, holder,
                                                        monkeypatch):
    monkeypatch.setattr(pageacts, "WAIT_S", 0.2)
    holder(write.round_lock(PROJECT, "billing-export"))
    before = everything_logged(rounds)

    [receipt] = send([draft(PICK, chose="a")], comms=comms)["receipts"]

    assert receipt["state"] == REFUSED and receipt["busy"]
    assert "the round is busy" in receipt["reason"]
    assert everything_logged(rounds) == before
    assert comms.sent == []


# -- recipients ---------------------------------------------------------------------------

def test_a_batch_over_two_rounds_is_one_message_per_lead(rounds, comms):
    send([draft(PICK, chose="a"), draft(PICK, OTHER, chose="b"), draft(PARK, item="Q2"),
          draft(WORDS, OTHER, text="the year before too")], comms=comms)

    assert [to for _m, _n, to, _t in comms.sent] == [("atlas-lead",), ("tax-lead",)]
    assert "Q2" in comms.to("atlas-lead")[0]
    assert "the year before too" in comms.to("tax-lead")[0]


def test_a_picked_agent_gets_the_batch_in_one_message(rounds, comms):
    answered = send([draft(PICK, chose="a"), draft(PICK, OTHER, chose="b")], comms=comms,
                    to="kestrel")

    [message] = comms.to("kestrel")
    assert ROUND in message and OTHER in message
    assert len(comms.sent) == 1
    assert {one["to"] for one in answered["receipts"]} == {"kestrel"}


LEADLESS_DRAFTS = [
    (PICK, "Q1", {"chose": "a"}), (PARK, "Q1", {}), (WORDS, "Q1", {"text": "weekly"}),
    (EXPLAIN, "Q1", {}),
]


@pytest.mark.parametrize(("kind", "item", "more"), LEADLESS_DRAFTS)
def test_a_draft_of_a_round_with_no_lead_is_kept_and_writes_nothing(rounds, comms, kind,
                                                                     item, more):
    before = everything_logged(rounds)

    [receipt] = send([draft(kind, LEADLESS, item, **more)], comms=comms)["receipts"]

    assert receipt["state"] == KEPT
    assert receipt["reason"] == f"No lead for {LEADLESS}: pick an agent to send it to"
    assert everything_logged(rounds) == before
    assert comms.sent == []


@pytest.mark.parametrize(("kind", "item", "more"), LEADLESS_DRAFTS)
def test_the_same_draft_to_a_picked_agent_goes_as_any(rounds, comms, kind, item, more):
    one = draft(kind, LEADLESS, item, **more)

    [receipt] = send([one], comms=comms, to="kestrel")["receipts"]

    assert receipt["state"] == (SENT if kind in pageacts.AGENT_KINDS else RECORDED)
    assert len(by_act(LEADLESS, one.act)) == (kind in pageacts.PAGE_KINDS)
    assert len(comms.to("kestrel")) == 1


def test_a_projects_question_goes_to_its_projects_lead(rounds, comms, write_config):
    from moxi import cli
    assert cli.main(["projects", "set", PROJECT, "--lead", "kestrel"]) == 0

    send([draft(PICK, PROJECT, "P1", chose="a"), draft(PICK, LEADLESS, chose="a")],
         comms=comms)

    [message] = comms.to("kestrel")
    assert "atlas P1" in message and f"{LEADLESS} Q1" in message


# -- with no comms ------------------------------------------------------------------------

def test_with_no_comms_the_page_records_its_kinds_with_or_without_a_lead(rounds):
    drafts = [draft(PICK, chose="a"), draft(PICK, LEADLESS, chose="b"),
              draft(PARK, LEADLESS, item="Q1"), draft(DONE, item="B1.W1")]

    answered = send(drafts, comms=None)

    assert [one["state"] for one in answered["receipts"]] == [RECORDED, RECORDED,
                                                              REFUSED, RECORDED]
    assert "already ruled" in answered["receipts"][2]["reason"]
    assert answered["messages"] == []
    assert all(len(by_act(one.holder, one.act)) == 1 for one in drafts[:2])


def test_with_no_comms_own_words_are_kept_with_the_command_a_lead_runs(rounds):
    before = everything_logged(rounds)

    [receipt] = send([draft(WORDS, text="nightly")], comms=None)["receipts"]

    assert receipt["state"] == KEPT
    assert receipt["fix"] == ('moxi rounds rule atlas/billing-export Q1 --text "nightly" '
                              "--record PTR")
    assert everything_logged(rounds) == before


# -- a message comms refuses --------------------------------------------------------------

def test_a_refused_message_leaves_records_not_told_and_words_as_drafts(rounds, comms):
    comms.refuse["atlas-lead"] = "the daemon is down"
    picked, words = draft(PICK, chose="a"), draft(WORDS, item="Q3", text="yes")

    first, second = send([picked, words], comms=comms)["receipts"]

    assert first["state"] == NOT_TOLD and "the daemon is down" in first["not_told"]
    assert len(by_act(ROUND, picked.act)) == 1
    assert second["state"] == REFUSED and "the daemon is down" in second["reason"]


def test_send_again_writes_nothing_and_sends_one_message_naming_each_by_id(rounds, comms):
    comms.refuse["atlas-lead"] = "the daemon is down"
    picked, parked = draft(PICK, chose="a"), draft(PARK, item="Q2")
    receipts = Receipts()
    send([picked, parked], comms=comms, receipts=receipts)
    del comms.refuse["atlas-lead"]
    before = everything_logged(rounds)

    again = [Draft(**{**one.__dict__, "tell": act()}) for one in (picked, parked)]
    answered = send(again, comms=comms, receipts=receipts)

    assert everything_logged(rounds) == before
    assert [one["state"] for one in answered["receipts"]] == [RECORDED, RECORDED]
    [message] = comms.to("atlas-lead")
    for one in answered["receipts"]:
        assert one["ruling"]["id"] in message and one["record"] in message


def test_send_again_of_a_draft_with_no_record_is_refused_as_not_on_record(rounds, comms):
    [receipt] = send([draft(PICK, chose="a", tell=act())], comms=comms)["receipts"]

    assert receipt["state"] == REFUSED and "not on record" in receipt["reason"]
    assert comms.sent == []


def test_send_again_refused_again_runs_again_on_its_next_try(rounds, comms):
    comms.refuse["atlas-lead"] = "the daemon is down"
    picked = draft(PICK, chose="a")
    receipts = Receipts()
    send([picked], comms=comms, receipts=receipts)
    again = Draft(**{**picked.__dict__, "tell": act()})
    assert send([again], comms=comms, receipts=receipts)["receipts"][0]["state"] == NOT_TOLD
    del comms.refuse["atlas-lead"]

    [receipt] = send([again], comms=comms, receipts=receipts)["receipts"]

    assert receipt["state"] == RECORDED and len(comms.sent) == 1


# -- an act posted twice runs once --------------------------------------------------------

def every_kind() -> list[Draft]:
    return [draft(PICK, chose="a"), draft(ACCEPT, item="B1.M1"), draft(READ_BACK, item="C1"),
            draft(DONE, item="B1.W1"), draft(PARK, item="Q2"),
            draft(WORDS, item="Q3", text="yes"), draft(CORRECT, item="C1", text="a TSV"),
            draft(EXPLAIN, item="Q3"), draft(PICK, OTHER, chose="a")]


def test_a_post_repeated_with_the_same_act_ids_is_answered_from_the_store(rounds, comms):
    receipts = Receipts()
    drafts = every_kind()
    first = send(drafts, comms=comms, receipts=receipts)
    before, sent = everything_logged(rounds), len(comms.sent)

    second = send(drafts, comms=comms, receipts=receipts)

    assert everything_logged(rounds) == before and len(comms.sent) == sent == 2
    assert second["messages"] == []
    assert [{**one, "repeat": True} for one in first["receipts"]] == second["receipts"]


def test_a_repeat_of_a_refused_draft_runs_again(rounds, comms):
    receipts = Receipts()
    comms.refuse["atlas-lead"] = "the daemon is down"
    words = draft(WORDS, item="Q3", text="yes")
    assert send([words], comms=comms, receipts=receipts)["receipts"][0]["state"] == REFUSED
    del comms.refuse["atlas-lead"]

    [receipt] = send([words], comms=comms, receipts=receipts)["receipts"]

    assert receipt["state"] == SENT and len(comms.sent) == 1


def test_a_repeat_that_comes_while_the_first_runs_waits_for_it(rounds, comms):
    started, release = threading.Event(), threading.Event()

    class Slow(Comms):
        def send_as(self, module_name, name, to, text):
            started.set()
            release.wait(5)
            return super().send_as(module_name, name, to, text)

    slow = Slow()
    receipts = Receipts()
    one = draft(PICK, chose="a")
    answers = []
    first = threading.Thread(target=lambda: answers.append(
        send([one], comms=slow, receipts=receipts)))
    first.start()
    assert started.wait(5)
    second = threading.Thread(target=lambda: answers.append(
        send([one], comms=slow, receipts=receipts)))
    second.start()
    release.set()
    first.join(10)
    second.join(10)

    assert len(slow.sent) == 1 and len(by_act(ROUND, one.act)) == 1
    assert sorted(bool(one["receipts"][0].get("repeat")) for one in answers) == [False, True]


# -- after a restart ----------------------------------------------------------------------

@pytest.mark.parametrize(("kind", "item", "more"), [
    (PICK, "Q1", {"chose": "a"}), (READ_BACK, "C1", {}), (DONE, "B1.W1", {}),
    (PARK, "Q2", {}), (ACCEPT, "B1.M1", {})])
def test_after_a_restart_a_recorded_act_reads_recorded_with_no_second_event(
        rounds, comms, kind, item, more):
    receipts = Receipts()
    one = draft(kind, item=item, **more)
    first = send([one], comms=comms, receipts=receipts)["receipts"][0]
    receipts.clear()
    before = everything_logged(rounds)

    [receipt] = send([one], comms=comms, receipts=receipts)["receipts"]

    assert everything_logged(rounds) == before
    assert receipt["state"] == RECORDED and receipt["again"]
    assert receipt["record"] == first["record"]
    assert len(comms.to("atlas-lead")) == 2


def test_after_a_restart_a_park_whose_question_was_reopened_is_not_parked_again(rounds,
                                                                                comms):
    receipts = Receipts()
    one = draft(PARK, item="Q2")
    send([one], comms=comms, receipts=receipts)
    marking.reopen(ROUND, "Q2", by="atlas-lead")
    receipts.clear()
    before = everything_logged(rounds)

    [receipt] = send([one], comms=comms, receipts=receipts)["receipts"]

    assert everything_logged(rounds) == before and receipt["state"] == RECORDED
    assert questions.find(folded(rounds), "Q2").state == questions.OPEN


def test_after_a_restart_a_pick_whose_ruling_was_taken_back_is_not_ruled_again(rounds,
                                                                               comms):
    receipts = Receipts()
    one = draft(PICK, chose="a")
    [first] = send([one], comms=comms, receipts=receipts)["receipts"]
    ruling.rule(ROUND, item=f"revoke:{first['ruling']['id']}", record=SAID, by="atlas-lead")
    receipts.clear()
    before = everything_logged(rounds)

    [receipt] = send([one], comms=comms, receipts=receipts)["receipts"]

    assert everything_logged(rounds) == before
    assert receipt["state"] == RECORDED and receipt["ruling"]["id"] == first["ruling"]["id"]


def test_after_a_restart_a_message_only_act_is_sent_again(rounds, comms):
    receipts = Receipts()
    one = draft(WORDS, item="Q3", text="yes")
    send([one], comms=comms, receipts=receipts)
    receipts.clear()

    [receipt] = send([one], comms=comms, receipts=receipts)["receipts"]

    assert receipt["state"] == SENT and len(comms.to("atlas-lead")) == 2


# -- unpark -------------------------------------------------------------------------------

def open_questions(project=PROJECT) -> list[tuple[str, str]]:
    found = dict(listing.waiting(project, read_git=False).found)
    return [(one.name, one.question.id) if hasattr(one, "question") else one
            for one in found.get("open", ())]


def test_unpark_takes_back_the_page_parking_and_the_question_waits_again(rounds, comms):
    receipts = Receipts()
    parked = draft(PARK, item="Q2")
    [park] = send([parked], comms=comms, receipts=receipts)["receipts"]
    assert questions.find(folded(rounds), "Q2").state == questions.PARKED
    waiting_before = str(open_questions())
    unpark = draft(UNPARK, item="Q2")

    [receipt] = send([unpark], comms=comms, receipts=receipts)["receipts"]

    [event] = by_act(ROUND, unpark.act)
    assert event["rules"] == f"revoke:{park['ruling']['id']}"
    assert pointers.page_act(event["record"]) == unpark.act
    assert receipt["state"] == RECORDED
    assert questions.find(folded(rounds), "Q2").state == questions.OPEN
    assert "Q2" in str(open_questions()) and waiting_before != str(open_questions())
    assert "unparked, open again" in comms.to("atlas-lead")[-1]


def test_unpark_finds_the_parking_that_stands_whoever_recorded_it(rounds, comms):
    ruling.rule(ROUND, item="park:Q3", record=SAID, by="atlas-lead")
    parking = folded(rounds).rulings[-1].id
    question = questions.find(folded(rounds), "Q3")
    assert corrections.put_by(question) == parking

    send([draft(UNPARK, item="Q3")], comms=comms)

    assert events_of(ROUND)[-1]["rules"] == f"revoke:{parking}"


def test_unpark_on_a_projects_question_takes_back_its_s_ruling(rounds, comms):
    send([draft(PARK, PROJECT, "P1")], comms=comms, to="kestrel")
    unpark = draft(UNPARK, PROJECT, "P1")

    send([unpark], comms=comms, to="kestrel")

    [event] = by_act(PROJECT, unpark.act)
    assert event["rules"].startswith("revoke:S")


def test_unpark_of_a_question_not_parked_is_refused(rounds, comms):
    [receipt] = send([draft(UNPARK, item="Q2")], comms=comms)["receipts"]

    assert receipt["state"] == REFUSED and "not parked by a ruling" in receipt["reason"]


def test_after_a_restart_an_unpark_is_not_recorded_twice(rounds, comms):
    receipts = Receipts()
    send([draft(PARK, item="Q2")], comms=comms, receipts=receipts)
    unpark = draft(UNPARK, item="Q2")
    send([unpark], comms=comms, receipts=receipts)
    receipts.clear()
    before = everything_logged(rounds)

    [receipt] = send([unpark], comms=comms, receipts=receipts)["receipts"]

    assert everything_logged(rounds) == before and receipt["state"] == RECORDED


# -- the post's body ----------------------------------------------------------------------

def test_a_posts_body_is_read_as_parallel_lists():
    one, two = act(), act()

    made = pageacts.drafts_of({"act": [one, two], "holder": [ROUND, PROJECT],
                               "item": ["Q1", "P1"], "kind": [PICK, WORDS],
                               "chose": ["a", ""], "text": ["", "the reader's"]})

    assert made == [Draft(one, ROUND, "Q1", PICK, chose="a"),
                    Draft(two, PROJECT, "P1", WORDS, text="the reader's")]


@pytest.mark.parametrize(("body", "wrong"), [
    ({"act": ["short!"], "holder": [ROUND], "item": ["Q1"], "kind": [PICK]}, "act"),
    ({"act": [act()], "holder": [ROUND], "item": ["Q1"], "kind": ["shout"]}, "kind"),
    ({"act": [act()], "holder": [ROUND, ROUND], "item": ["Q1"], "kind": [PICK]}, "holder"),
])
def test_a_body_that_cant_be_read_names_the_key(body, wrong):
    with pytest.raises(ValueError, match=wrong):
        pageacts.drafts_of(body)


def test_the_answers_route_sends_through_the_servers_store(rounds, comms, monkeypatch):
    from moxi_rounds import pagehandlers

    monkeypatch.setattr(pagehandlers, "RECEIPTS", Receipts())
    monkeypatch.setattr(pagehandlers, "comms_api", lambda: comms)
    one = act()
    body = {"act": [one], "holder": [ROUND], "item": ["Q1"], "kind": [PICK],
            "chose": ["a"], "to": EACH}

    first = asyncio.run(pagehandlers.answers(SimpleNamespace(body=body)))
    second = asyncio.run(pagehandlers.answers(SimpleNamespace(body=body)))

    assert first["receipts"][0]["state"] == RECORDED
    assert second["receipts"][0]["repeat"] and len(comms.sent) == 1
    assert len(by_act(ROUND, one)) == 1
