"""What the lander tells, through comms: a member's end to its builder and its round's
lead, a cleared blocker to the member it ejected, a pause to the project's director;
only recorded names, under the reserved name `rounds`; comms optional; and what is
recorded when comms is offline, refuses or raises, or when there is nobody to tell."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_queue_batch import (  # noqa: F401 -- fixtures
    FULL, OTHER, ROUND, SCOPED, FakeCi, configure, fake, queue, ready, repo)

from moxi_rounds import batching, lander, leads, module, queue_file, queue_view, telling
from moxi_rounds.cli import main


class FakeComms:
    """comms' API as the lander uses it: each `send_as` is kept, and answers by the
    recipient's `outcome` ("sent", "offline", "never-bound" or "raises") and
    `delivery`."""

    def __init__(self):
        self.sent: list[tuple[str, str, tuple[str, ...], str]] = []
        self.outcome: dict[str, str] = {}
        self.delivery: dict[str, str] = {}
        self.states: dict[str, str] = {}

    def identity_state(self, name):
        state = self.states.get(name, "held")
        if state == "raises":
            raise ValueError(f"{name!r} is not a name")
        return SimpleNamespace(state=state)

    def send_as(self, module_name, name, to, text):
        self.sent.append((module_name, name, tuple(to), text))
        who = to[0]
        outcome = self.outcome.get(who, "sent")
        if outcome == "raises":
            raise RuntimeError("the socket broke")
        if outcome == "sent":
            return SimpleNamespace(outcome="sent", message_id="m-1", refusal=None,
                                   recipients=(SimpleNamespace(
                                       qualified=who,
                                       delivery=self.delivery.get(who, "live")),))
        if outcome == "never-bound":
            return SimpleNamespace(outcome="refused", message_id=None, recipients=(),
                                   refusal=SimpleNamespace(
                                       code="never-bound",
                                       text=f"comms has never seen {who} bound"))
        return SimpleNamespace(outcome="offline", message_id=None, recipients=(),
                               refusal=SimpleNamespace(code="offline",
                                                       text="the daemon is down"))

    def to(self, name: str) -> list[str]:
        return [text for _module, _name, to, text in self.sent if to == (name,)]


@pytest.fixture(autouse=True)
def no_lander_process(monkeypatch):
    """`next` and `queue` start no lander process here."""
    monkeypatch.setattr(lander, "start", lambda repo, *, env=None, spawn=None: lander.Started(
        "session", lander.log_file(repo)))


@pytest.fixture
def comms():
    return FakeComms()


@pytest.fixture
def lander_pass(fake, comms):
    """`lander_pass(now, comms=)`: one pass of the lander with `comms` (the fixture's
    stand-in when left out), the clock reading `now`."""

    def run(now: float = 0.0, **given) -> None:
        batching.step("proj-a", candidate_ci=fake, comms=given.get("comms", comms),
                      say=lambda _line: None, clock=lambda: now)

    return run


def told(about: str | None = None) -> list[dict]:
    return [one for one in queue().told if about is None or one.get("about") == about]


def config_text(*, defaults: str = "", table: str = "", ref: bool = False) -> str:
    ref_text = ', ref = "ci/{branch}", remote = "origin"' if ref else ""
    return (f"[defaults]\n{defaults}\n[projects.proj-a]\nland = \"queue\"\n"
            f"check = {{ command = {json.dumps(FULL)}, scoped = {json.dumps(SCOPED)} }}\n"
            f"ci = {{ workflows = [\"ci.yml\"], local = \"none\"{ref_text} }}\n{table}\n")


# -- a member's news ---------------------------------------------------------------------

def test_rounds_reserves_its_send_only_name_and_takes_comms_as_optional():
    assert module.RESERVED_NAMES == [telling.SENDER] == ["rounds"]
    assert "comms" in module.OPTIONAL


def test_a_landed_member_is_told_to_its_builder_and_its_rounds_lead_under_rounds(
        repo, ready, lander_pass, comms):
    ready()
    leads.mark(ROUND, "lead-a", by="planner")
    lander_pass()
    lander_pass()
    assert queue().member(ROUND, "B1").state == queue_file.LANDED
    assert {to for _module, _name, to, _text in comms.sent} == {("coder",), ("lead-a",)}
    assert all(sent[:2] == ("rounds", "rounds") for sent in comms.sent)
    assert "landed in proj-a's queue" in comms.to("coder")[0]
    assert {one["to"]: one["delivery"] for one in told("landed")} == {
        "coder": "live", "lead-a": "live"}
    lander_pass()
    assert len(comms.sent) == 2


def test_the_projects_lead_is_told_when_the_round_names_none(repo, ready, lander_pass,
                                                             comms):
    from moxi import cli

    assert cli.main(["projects", "set", "proj-a", "--lead", "proj-lead"]) == 0
    ready()
    lander_pass()
    lander_pass()
    assert comms.to("proj-lead") and comms.to("coder")


def test_with_no_lead_the_builder_alone_is_told_and_next_says_so(repo, ready, lander_pass,
                                                                 comms, capsys):
    ready()
    assert main(["next", ROUND]) == 0
    assert "the round has no lead: the lander tells the builder alone" in (
        capsys.readouterr().out)
    lander_pass()
    lander_pass()
    assert {to for _module, _name, to, _text in comms.sent} == {("coder",)}
    nobody = [one for one in told("landed") if one["delivery"] == telling.NOBODY]
    assert nobody and nobody[0]["why"] == f"{ROUND} has no lead recorded"
    assert not any("director" in line for line in queue_view.told_lines(queue()))


def test_without_comms_the_member_lands_and_nothing_is_sent(repo, ready, lander_pass):
    ready()
    leads.mark(ROUND, "lead-a", by="planner")
    lander_pass(comms=None)
    lander_pass(comms=None)
    assert queue().member(ROUND, "B1").state == queue_file.LANDED
    assert told() == []


def test_a_member_ejected_for_a_conflict_is_told_when_its_blocker_lands(
        repo, ready, lander_pass, comms):
    ready(file="same.txt", text="the first\n")
    ready(OTHER, branch="other", file="same.txt", text="the second\n")
    lander_pass()
    assert queue().member(OTHER, "B1").blocker == f"{ROUND} B1"
    assert any("ejected from proj-a's queue: conflict with" in text
               for text in comms.to("coder"))
    lander_pass()
    blocked = told("blocker")
    assert [one["to"] for one in blocked if one["delivery"] != telling.NOBODY] == ["coder"]
    assert f"a conflict with {ROUND} B1, which landed" in blocked[0]["text"]
    assert "merge its target into the branch" in blocked[0]["text"]
    lander_pass()
    assert len(told("blocker")) == len(blocked)


# -- the queue's news --------------------------------------------------------------------

def test_a_pause_goes_to_the_director_the_projects_table_names_else_the_defaults(
        repo, ready, lander_pass, comms, fake, write_config):
    write_config(config_text(defaults='director = "dir-all"'))
    ready()
    fake.refusal = "the network is down"
    lander_pass()
    lander_pass()
    assert queue().paused is not None
    assert len(comms.to("dir-all")) == 1 and "is paused" in comms.to("dir-all")[0]
    assert "--resume" in comms.to("dir-all")[0]

    write_config(config_text(defaults='director = "dir-all"', table='director = "dir-a"'))
    assert main(["queue", "--repo", "proj-a", "--resume"]) == 0
    lander_pass()
    lander_pass()
    assert len(comms.to("dir-a")) == 1 and len(comms.to("dir-all")) == 1


def test_no_director_set_is_recorded_and_the_queue_says_so(repo, ready, lander_pass,
                                                           comms, fake):
    ready()
    fake.refusal = "the network is down"
    lander_pass()
    lander_pass()
    paused = told("paused")
    assert [(one["to"], one["delivery"], one["why"]) for one in paused] == [
        (None, telling.NOBODY, "no director set")]
    assert ("director: no director set; 1 notice(s) about the queue went to nobody"
            in queue_view.told_lines(queue()))


# -- what comms answers ------------------------------------------------------------------

def test_a_message_comms_cannot_take_is_tried_each_pass_and_keeps_the_lander(
        repo, ready, lander_pass, comms):
    ready()
    leads.mark(ROUND, "lead-a", by="planner")
    comms.outcome["coder"] = "offline"
    lander_pass()
    lander_pass()
    assert [one["delivery"] for one in told("landed") if one["to"] == "coder"] == [
        telling.OFFLINE]
    assert not lander.settled("proj-a", queue())
    assert lander.ensure("proj-a", spawn=lambda argv, env: None) is not None
    lander_pass(now=60.0)
    assert len(comms.to("coder")) == 2
    assert "tried again each pass" in "\n".join(queue_view.told_lines(queue()))
    comms.outcome["coder"] = "sent"
    lander_pass(now=120.0)
    assert [one["delivery"] for one in told("landed") if one["to"] == "coder"][-1] == "live"
    assert lander.settled("proj-a", queue())


def test_a_message_tried_past_the_bound_is_recorded_unsent_and_frees_the_lander(
        repo, ready, lander_pass, comms):
    ready()
    leads.mark(ROUND, "lead-a", by="planner")
    comms.outcome["coder"] = "offline"
    lander_pass()
    lander_pass()
    lander_pass(now=telling.UNSENT_S + 1)
    newest = [one for one in told("landed") if one["to"] == "coder"][-1]
    assert newest["delivery"] == telling.UNSENT
    assert lander.settled("proj-a", queue())
    assert "unsent" in "\n".join(queue_view.told_lines(queue()))


def test_a_refusal_or_an_error_is_recorded_as_no_delivery_and_not_tried_again(
        repo, ready, lander_pass, comms):
    ready()
    leads.mark(ROUND, "lead-a", by="planner")
    comms.outcome.update({"coder": "never-bound", "lead-a": "raises"})
    lander_pass()
    lander_pass()
    found = {one["to"]: one for one in told("landed")}
    assert found["coder"]["delivery"] == telling.REFUSED
    assert found["coder"]["why"].startswith("never-bound: ")
    assert (found["lead-a"]["delivery"], found["lead-a"]["why"]) == (
        telling.ERROR, "the socket broke")
    assert lander.settled("proj-a", queue())
    lander_pass(now=60.0)
    assert len(comms.sent) == 2
    assert "messages not delivered: 2" in queue_view.told_lines(queue())
