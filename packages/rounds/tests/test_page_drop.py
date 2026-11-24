"""The operator's drop of one build from rounds' page, and the agents its To picker
offers, run in process as the page server runs them, with a stand-in for comms' API:
the confirm's facts read before the drop, the drop recorded as the operator's with the
page's pointer, the round's lead told, an act posted twice and after a restart, and
comms' roster."""

from __future__ import annotations

import asyncio
import re
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_page_acts import ROUND, everything_logged, folded, kit  # noqa: F401 -- fixture
from test_page_answers import Comms

from moxi_rounds import builds, log, opening, pageacts, pointers
from moxi_rounds.pageacts import RECORDED, REFUSED, Receipts

LEADLESS = "atlas/payroll"


@pytest.fixture
def comms():
    return Comms()


def act() -> str:
    return secrets.token_urlsafe(32)


def commissioned(round_dir) -> dict:
    return next(one for one in log.read(log.round_log(round_dir))
                if one["type"] == "build.commissioned")


def drop(*, comms, receipts=None, name=ROUND, stage="B1", act_id=None):
    return pageacts.drop(name, stage, act_id or act(),
                         receipts=Receipts() if receipts is None else receipts, comms=comms)


def dropped_events(round_dir) -> list[dict]:
    return [one for one in log.read(log.round_log(round_dir))
            if one["type"] == "build.dropped"]


# -- the confirm --------------------------------------------------------------------------

def test_the_confirm_names_the_build_its_worktree_and_scratch_and_what_stays(kit, git_repo):
    event = commissioned(kit)
    before = everything_logged(kit)

    facts = pageacts.drop_facts(ROUND, "B1")

    assert everything_logged(kit) == before
    assert facts["open"] and facts["branch"] == "work-b1"
    assert facts["builder"] == "builder-1" and facts["at"] == event["at"]
    assert facts["tip"] == git_repo.rev("work-b1")
    assert [(one["kind"], one["path"], one["goes"]) for one in facts["folders"]] == [
        ("worktree", event["worktree"], True), ("scratch", event["scratch"], True)]


def test_the_confirm_says_a_worktree_with_untracked_work_will_be_kept(kit):
    event = commissioned(kit)
    (Path(event["worktree"]) / "draft.txt").write_text("not committed\n", encoding="utf-8")

    facts = pageacts.drop_facts(ROUND, "B1")

    worktree = facts["folders"][0]
    assert not worktree["goes"] and "draft.txt" in worktree["why"]


@pytest.mark.parametrize(("name", "stage", "says"), [
    (ROUND, "B2", "B2 has no open build"),
    ("atlas/no-such-round", "B1", "no-such-round"),
    ("atlas/../elsewhere/sub", "B1", "is not a round's name"),
    ("..", "B1", "is not a round's name"),
])
def test_the_confirm_of_no_open_build_says_why(kit, name, stage, says):
    facts = pageacts.drop_facts(name, stage)

    assert not facts["open"] and says in facts["reason"]


# -- the drop -----------------------------------------------------------------------------

def test_a_drop_is_the_operators_with_the_pages_pointer_and_tells_the_lead(kit, comms):
    event = commissioned(kit)
    act_id = act()

    receipt = drop(comms=comms, act_id=act_id)

    [dropped] = dropped_events(kit)
    assert dropped["by"] == "operator" and pointers.page_act(dropped["record"]) == act_id
    assert receipt["state"] == RECORDED and receipt["record"] == dropped["record"]
    assert receipt["removed"] == [event["worktree"], event["scratch"]]
    assert builds.current(folded(kit), "B1").ended == builds.DROPPED
    [message] = comms.to("atlas-lead")
    assert "dropped the open build of B1" in message and event["worktree"] in message
    assert "work-b1 stays" in message
    assert receipt["message"]["to"] == "atlas-lead"


def test_a_drop_with_a_worktree_holding_work_keeps_it_and_says_so(kit, comms):
    event = commissioned(kit)
    (Path(event["worktree"]) / "draft.txt").write_text("not committed\n", encoding="utf-8")

    receipt = drop(comms=comms)

    assert receipt["removed"] == [event["scratch"]]
    [kept] = receipt["kept"]
    # The kept folder is named by its path, with the removal rule's words and no check id,
    # alike in the receipt, the lead's message and the confirm.
    assert kept["path"] == event["worktree"] and "draft.txt" in kept["why"]
    assert not re.match(r"[AB]\d+: ", kept["why"])
    assert f"- kept {event['worktree']}: {kept['why']}" in comms.to("atlas-lead")[0]


def test_the_confirm_and_the_receipt_say_a_kept_folder_alike(kit, comms):
    event = commissioned(kit)
    (Path(event["worktree"]) / "draft.txt").write_text("not committed\n", encoding="utf-8")

    [worktree, _scratch] = pageacts.drop_facts(ROUND, "B1")["folders"]
    receipt = drop(comms=comms)

    assert receipt["kept"] == [{"path": worktree["path"], "why": worktree["why"]}]


def test_a_refused_message_leaves_the_drop_recorded(kit, comms):
    comms.refuse["atlas-lead"] = "the daemon is down"

    receipt = drop(comms=comms)

    assert receipt["state"] == RECORDED and "the daemon is down" in receipt["not_told"]
    assert len(dropped_events(kit)) == 1


def test_a_drop_of_a_round_with_no_lead_tells_no_one(kit, comms):
    opening.open_round(LEADLESS, description="run the payroll", by="builder-1", flow="fix")
    log.append(log.round_log(kit.parent / "payroll"), "stage.declared",
               {"id": "B1", "repo": "atlas", "branch": "pay-b1", "after": []}, by="x")
    from moxi_rounds import commissioning
    commissioning.commission(LEADLESS, "B1", builder="builder-1", by="builder-1")

    receipt = drop(comms=comms, name=LEADLESS)

    assert receipt["state"] == RECORDED and "no lead" in receipt["not_told"]
    assert comms.sent == []


def test_with_no_comms_the_drop_is_recorded_and_no_one_told(kit):
    receipt = drop(comms=None)

    assert receipt["state"] == RECORDED and "comms is not installed" in receipt["not_told"]


def test_a_drop_with_no_open_build_is_refused_and_writes_nothing(kit, comms):
    drop(comms=comms)
    before = everything_logged(kit)

    receipt = drop(comms=comms)

    assert receipt["state"] == REFUSED and "no open build" in receipt["reason"]
    assert everything_logged(kit) == before and len(comms.sent) == 1


def test_a_drop_posted_twice_runs_once(kit, comms):
    receipts = Receipts()
    act_id = act()
    first = drop(comms=comms, receipts=receipts, act_id=act_id)

    second = drop(comms=comms, receipts=receipts, act_id=act_id)

    assert second == {**first, "repeat": True}
    assert len(dropped_events(kit)) == 1 and len(comms.sent) == 1


def test_after_a_restart_a_drop_posted_again_reads_recorded_and_tells_the_lead(kit, comms):
    receipts = Receipts()
    act_id = act()
    first = drop(comms=comms, receipts=receipts, act_id=act_id)
    receipts.clear()

    again = drop(comms=comms, receipts=receipts, act_id=act_id)

    assert again["state"] == RECORDED and again["again"]
    assert again["record"] == first["record"]
    assert len(dropped_events(kit)) == 1
    assert "recorded earlier" in comms.to("atlas-lead")[-1]


def test_the_drop_routes_use_the_servers_store(kit, comms, monkeypatch):
    from moxi_rounds import pagehandlers

    monkeypatch.setattr(pagehandlers, "RECEIPTS", Receipts())
    monkeypatch.setattr(pagehandlers, "comms_api", lambda: comms)
    body = {"round": ROUND, "stage": "B1", "act": act()}

    facts = asyncio.run(pagehandlers.drop_confirm(
        SimpleNamespace(query={"round": ROUND, "stage": "B1"})))
    first = asyncio.run(pagehandlers.drop(SimpleNamespace(body=body)))
    second = asyncio.run(pagehandlers.drop(SimpleNamespace(body=body)))

    assert facts["open"] and first["state"] == RECORDED and second["repeat"]
    assert len(dropped_events(kit)) == 1


# -- the agents ---------------------------------------------------------------------------

def test_the_agents_are_the_names_comms_gives_as_bound(comms):
    assert pageacts.agents(comms) == {"agents": ["atlas-lead", "kestrel", "tax-lead"],
                                      "reason": None, "comms": True}


def test_with_no_comms_the_agents_are_none_with_the_reason():
    assert pageacts.agents(None) == {"agents": [], "reason": "comms is not installed",
                                       "comms": False}


def test_a_roster_that_cant_be_read_gives_comms_reason(comms):
    def broken():
        raise ConnectionError("the daemon is down")

    comms.roster = broken

    assert pageacts.agents(comms) == {"agents": [], "reason": "the daemon is down",
                                     "comms": True}
