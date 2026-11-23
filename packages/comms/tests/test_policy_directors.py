"""The box's policy directors: `[policy] directors`, merged at launch, and the guard.

A policy director is a name the box's own comms config gives every session that doesn't
opt out. It is merged once, at launch, through `directors.effective`, and it gives
directive standing only to a verified message whose author is at the standing tier, which
only provisioning writes. A launch-declared director keeps its own rule.
"""

from __future__ import annotations

import time

import pytest

from moxi_comms import directors, owed
from moxi_comms.adapters.claude_shim import frame_line
from moxi_comms.config import CommsConfig, PolicyConfig


def cfg(*names) -> CommsConfig:
    return CommsConfig(policy=PolicyConfig(directors=tuple(names)))


def payload(name, tier="standing", attribution="verified", *, message_id="101",
            posture="once"):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {"message_id": message_id, "channel_id": "c1", "room": "#names",
            "author": {"name": name, "tier": tier, "attribution": attribution},
            "recipients": ["worker@proj-a"], "speaker": False, "backlog": False,
            "text": "body", "reply_to": None, "timestamp": stamp, "posture": posture}


# ------------------------------------------------------------------------ effective

def test_the_setting_follows_the_launch_declared_names_in_order():
    full, policy = directors.effective(["lead@proj-a"], cfg("fleet-lead", "ops"),
                                       own="worker@proj-a", use_policy=True)
    assert full == ["lead@proj-a", "fleet-lead", "ops"]
    assert policy == ["fleet-lead", "ops"]


def test_a_name_both_declared_and_set_is_launch_declared_only():
    full, policy = directors.effective(["fleet-lead@global", "lead"], cfg("fleet-lead"),
                                       own="worker", use_policy=True)
    assert full == ["fleet-lead", "lead"]
    assert policy == []


def test_the_opt_out_takes_no_policy_name_and_keeps_the_launchs():
    full, policy = directors.effective(["fleet-lead"], cfg("fleet-lead", "ops"),
                                       own="worker", use_policy=False)
    assert (full, policy) == (["fleet-lead"], [])


def test_an_empty_setting_adds_nothing():
    assert directors.effective(["lead"], cfg(), own="w", use_policy=True) == (["lead"], [])
    assert directors.effective([], cfg(), own="w", use_policy=True) == ([], [])


def test_the_sessions_own_identity_is_not_its_own_policy_director():
    full, policy = directors.effective([], cfg("fleet-lead", "ops"), own="fleet-lead",
                                       use_policy=True)
    assert (full, policy) == (["ops"], ["ops"])


def test_a_launch_declared_own_name_stays_as_today():
    full, policy = directors.effective(["fleet-lead"], cfg("fleet-lead"),
                                       own="fleet-lead", use_policy=True)
    assert (full, policy) == (["fleet-lead"], [])


def test_the_declared_part_is_qualified_and_de_duplicated():
    full, _ = directors.effective(["boss@global", "boss", "x@p"], cfg(), own="w",
                                  use_policy=True)
    assert full == ["boss", "x@p"]


def test_the_stamps_carry_both_lists_and_read_back():
    env = directors.stamp(["lead", "fleet-lead"], ["fleet-lead"])
    assert env == {"MOXI_COMMS_DIRECTORS": "lead,fleet-lead",
                   "MOXI_COMMS_POLICY_DIRECTORS": "fleet-lead"}
    assert directors.from_env(env) == ("lead", "fleet-lead")
    assert directors.policy_from_env(env) == ("fleet-lead",)
    assert directors.launch_declared(directors.from_env(env),
                                     directors.policy_from_env(env)) == ("lead",)
    assert directors.stamp(["lead"]) == {"MOXI_COMMS_DIRECTORS": "lead"}


# ----------------------------------------------------------------------- the guard

def test_a_verified_standing_policy_director_is_a_directive():
    standing = directors.standing_of(payload("fleet-lead")["author"], ["fleet-lead"],
                                     policy=["fleet-lead"])
    assert standing.directive and standing.note == "your director"


def test_a_chosen_tier_policy_director_is_information_and_says_why():
    author = payload("fleet-lead", tier="chosen")["author"]
    standing = directors.standing_of(author, ["fleet-lead"], policy=["fleet-lead"])
    assert not standing.directive
    assert standing.note == "policy director, not provisioned: information standing"


def test_an_unverified_policy_director_degrades():
    author = payload("fleet-lead", attribution="labeled")["author"]
    standing = directors.standing_of(author, ["fleet-lead"], policy=["fleet-lead"])
    assert not standing.directive and "unverified" in standing.note


def test_a_launch_declared_director_keeps_the_launchs_rule_at_any_tier():
    author = payload("lead", tier="chosen")["author"]
    assert directors.standing_of(author, ["lead", "fleet-lead"],
                                 policy=["fleet-lead"]).directive


def test_the_operator_is_a_directive_whatever_the_policy():
    author = {"name": "operator", "tier": "operator", "attribution": "verified"}
    assert directors.standing_of(author, (), policy=["fleet-lead"]).directive


def test_the_frame_line_carries_the_guard():
    line = frame_line(payload("fleet-lead", tier="chosen"), ["fleet-lead"],
                      policy=["fleet-lead"])
    assert line.startswith("[comms] from fleet-lead")
    assert "policy director, not provisioned" in line
    line = frame_line(payload("fleet-lead"), ["fleet-lead"], policy=["fleet-lead"])
    assert line.startswith("[comms] directive from fleet-lead")


@pytest.mark.parametrize("tier, branch", [("standing", owed.BRANCH_DIRECTOR),
                                          ("chosen", owed.BRANCH_PEER)])
def test_the_owed_ledger_opens_a_director_debt_only_for_a_directive(tier, branch):
    p = payload("fleet-lead", tier=tier)
    assert owed.owing_branch(p, ["fleet-lead"], policy=["fleet-lead"]) == branch
    debt = owed.Ledger().open(p, ["fleet-lead"], "s1", time.time(), policy=["fleet-lead"])
    assert debt is not None and debt.branch == branch
