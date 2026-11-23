"""The box's policy directors: `[policy] directors`, merged at launch, and the guard.

A policy director is a name the box's own comms config gives every session that doesn't
opt out. It is merged once, at launch, through `directors.effective`, and it gives
directive standing only to a verified message whose author is at the standing tier, which
only provisioning writes. A launch-declared director keeps its own rule.
"""

from __future__ import annotations

import textwrap
import time

import pytest
from conftest import write_config

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


def test_the_hand_run_seam_reads_the_policy_stamp_and_keeps_the_guard():
    from moxi_comms.adapters.claude_shim import seam_directors

    env = {"MOXI_COMMS_DIRECTORS": "lead@proj-a,fleet-lead",
           "MOXI_COMMS_POLICY_DIRECTORS": "fleet-lead,stray"}
    full, policy = seam_directors([], env)
    assert full == ("lead@proj-a", "fleet-lead")
    assert policy == ("fleet-lead",)
    line = frame_line(payload("fleet-lead", tier="chosen"), full, policy=policy)
    assert line.startswith("[comms] from fleet-lead")


def test_the_hand_run_seams_flag_takes_no_policy_part():
    from moxi_comms.adapters.claude_shim import seam_directors

    env = {"MOXI_COMMS_DIRECTORS": "fleet-lead", "MOXI_COMMS_POLICY_DIRECTORS": "fleet-lead"}
    assert seam_directors(["fleet-lead"], env) == (("fleet-lead",), ())
    with pytest.raises(directors.BadDirector):
        seam_directors(["not a name"], env)


@pytest.mark.parametrize("tier, branch", [("standing", owed.BRANCH_DIRECTOR),
                                          ("chosen", owed.BRANCH_PEER)])
def test_the_owed_ledger_opens_a_director_debt_only_for_a_directive(tier, branch):
    p = payload("fleet-lead", tier=tier)
    assert owed.owing_branch(p, ["fleet-lead"], policy=["fleet-lead"]) == branch
    debt = owed.Ledger().open(p, ["fleet-lead"], "s1", time.time(), policy=["fleet-lead"])
    assert debt is not None and debt.branch == branch


# --------------------------------------------------------------- the wrappers, at launch

SID = "5e55a1d0-0000-4000-8000-00000000f1a1"


def claude_dry_run(capsys, *argv) -> str:
    from moxi_comms.adapters import session

    assert session.main(["--name", "worker", "--scope", "proj-a", "--session-id", SID,
                         *argv, "--dry-run"]) == 0
    return capsys.readouterr().out


def provision_standing(name, scope="global"):
    from moxi_comms import directory
    from moxi_comms.mock_record import MockRecord
    from moxi_comms.paths import comms_db
    from moxi_comms.provision import provision

    provision(MockRecord(), comms_db(), standing=[(name, scope)])
    assert directory.provisioned([name]) == {name}


def test_a_claude_launch_with_no_director_takes_the_policy_names(capsys):
    out = claude_dry_run(capsys)
    assert "  directors  fleet-lead (policy, not provisioned); verified" in out
    provision_standing("fleet-lead")
    out = claude_dry_run(capsys)
    assert "  directors  fleet-lead (policy); verified" in out


def test_the_claude_session_stamps_both_lists():
    from moxi_comms.adapters import session

    s = session.Session("d1", name="worker", scope="proj-a", project="proj-a",
                        directors=["lead", "fleet-lead"], policy_directors=["fleet-lead"])
    env = s.env(base={"MOXI_COMMS_POLICY_DIRECTORS": "someone-else"})
    assert env["MOXI_COMMS_DIRECTORS"] == "lead,fleet-lead"
    assert env["MOXI_COMMS_POLICY_DIRECTORS"] == "fleet-lead"
    none = session.Session("d2", name="worker", directors=["lead"])
    assert "MOXI_COMMS_POLICY_DIRECTORS" not in none.env(
        base={"MOXI_COMMS_POLICY_DIRECTORS": "someone-else"})


def test_a_claude_launch_opted_out_takes_none_and_keeps_its_own(capsys):
    out = claude_dry_run(capsys, "--no-policy-directors")
    assert "  directors  none; policy directors: opted out" in out
    out = claude_dry_run(capsys, "--director", "fleet-lead", "--no-policy-directors")
    assert "  directors  fleet-lead; verified messages from these render as directives; " \
           "policy directors: opted out" in out


def test_an_empty_setting_says_nothing(capsys):
    write_config(None, "[policy]\ndirectors = []\n")
    assert "  directors  " not in claude_dry_run(capsys)


def test_the_claude_launch_stamps_the_merge(monkeypatch, tmp_path):
    from moxi_comms.adapters import session

    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    seen = {}
    monkeypatch.setattr(session, "launch",
                        lambda s, found, cmd, env, **_: seen.update(s=s, env=env) or 0)
    assert session.main(["--name", "worker", "--director", "lead", "--session-id", SID,
                         "--claude", str(binary)]) == 0
    assert seen["env"]["MOXI_COMMS_DIRECTORS"] == "lead,fleet-lead"
    assert seen["env"]["MOXI_COMMS_POLICY_DIRECTORS"] == "fleet-lead"
    assert seen["s"].policy_directors == ["fleet-lead"]


def test_the_fleet_leads_own_launch_takes_no_policy_self(capsys):
    from moxi_comms.adapters import session

    assert session.main(["--name", "fleet-lead", "--session-id", SID, "--dry-run"]) == 0
    assert "  directors  " not in capsys.readouterr().out


def test_the_layer_one_director_sentence_keys_on_launch_declared_only():
    from moxi_comms.adapters.shim_roster import render_snapshot
    from moxi_comms.context import DIRECTOR_LINE

    policy_only = directors.stamp(["fleet-lead"], ["fleet-lead"])
    text, _ = render_snapshot(None, env=policy_only)
    assert DIRECTOR_LINE not in text
    declared = directors.stamp(["lead", "fleet-lead"], ["fleet-lead"])
    text, _ = render_snapshot(None, env=declared)
    assert DIRECTOR_LINE in text


# ------------------------------------------------------------------- codex, opencode

def codex_args(*argv):
    from moxi_comms.adapters.codex_wrapper import build_parser

    return build_parser().parse_args(list(argv))


def test_a_codex_named_launch_takes_the_policy_names(tmp_path):
    from moxi_comms.adapters.codex_wrapper import plan

    launch = plan(codex_args("--name", "coder"), cwd=str(tmp_path))
    assert (launch.agent.directors, launch.agent.policy_directors) == (["fleet-lead"],
                                                                     ["fleet-lead"])
    env = launch.agent.env({})
    assert env["MOXI_COMMS_DIRECTORS"] == env["MOXI_COMMS_POLICY_DIRECTORS"] == "fleet-lead"
    opted = plan(codex_args("--name", "coder", "--director", "fleet-lead",
                            "--no-policy-directors"), cwd=str(tmp_path))
    assert (opted.agent.directors, opted.agent.policy_directors) == (["fleet-lead"], [])
    assert "MOXI_COMMS_POLICY_DIRECTORS" not in opted.agent.env({})


def test_the_codex_plan_marks_the_policy_names(tmp_path):
    import io

    from moxi_comms.adapters import codex_wrapper

    def said(*argv):
        launch = codex_wrapper.plan(codex_args("--name", "coder", *argv), cwd=str(tmp_path))
        out = io.StringIO()
        codex_wrapper.print_plan(launch, launch.host_argv("host"), out=out)
        return [ln for ln in out.getvalue().splitlines() if ln.startswith("  directors  ")]

    assert said("--director", "lead") == [
        "  directors  lead, fleet-lead (policy, not provisioned); verified messages "
        "from these render as directives"]
    assert said("--no-policy-directors") == [
        "  directors  none; policy directors: opted out"]


def test_codex_layer_one_has_no_director_sentence_for_a_policy_director_only():
    from moxi_comms.adapters.codex import CodexAdapter
    from moxi_comms.adapters.codex_roster import CodexAgent
    from moxi_comms.context import DIRECTOR_LINE, layer_one_fixed

    agent = CodexAgent(qualified="c", name="c", scope="global", cwd="/w", thread_file=None,
                       directors=["fleet-lead"], policy_directors=["fleet-lead"])
    adapter = CodexAdapter.__new__(CodexAdapter)
    adapter.agent = agent
    assert adapter._directors() == ()
    assert DIRECTOR_LINE not in layer_one_fixed(directors=adapter._directors())


def roster_rows_for(table, body):
    write_config(None, textwrap.dedent(body).replace("[agents.", f"[{table}.agents."))


@pytest.mark.parametrize("table", ["codex", "opencode"])
def test_a_roster_row_takes_the_policy_names_after_its_own(table):
    from moxi_comms.adapters import codex_roster, opencode_roster

    roster = {"codex": codex_roster, "opencode": opencode_roster}[table]
    roster_rows_for(table, """
        [policy]
        directors = ["fleet-lead", "ops"]

        [agents."a@p"]
        cwd = "/w"
        directors = ["lead"]

        [agents."b@p"]
        cwd = "/w"
        directors = ["fleet-lead"]
        takes_policy_directors = false
    """)
    loaded = roster.load()
    a, b = loaded.require("a@p"), loaded.require("b@p")
    assert (a.directors, a.policy_directors) == (["lead", "fleet-lead", "ops"],
                                                 ["fleet-lead", "ops"])
    assert (b.directors, b.policy_directors) == (["fleet-lead"], [])
    assert b.takes_policy_directors is False
    assert a.env({})["MOXI_COMMS_POLICY_DIRECTORS"] == "fleet-lead,ops"


@pytest.mark.parametrize("table", ["codex", "opencode"])
def test_the_text_check_does_not_merge(table):
    from moxi_comms.adapters import codex_roster, opencode_roster

    roster = {"codex": codex_roster, "opencode": opencode_roster}[table]
    checked = roster.from_table({"agents": {"a@p": {"cwd": "/w"}}}, "t", check=True)
    assert checked.require("a@p").directors == []


@pytest.mark.parametrize("table", ["codex", "opencode"])
def test_a_config_path_roster_takes_the_boxs_setting(tmp_path, table):
    from moxi_comms.adapters import codex_roster, opencode_roster

    roster = {"codex": codex_roster, "opencode": opencode_roster}[table]
    write_config(None, '[policy]\ndirectors = ["box-lead"]\n')
    other = tmp_path / "other.toml"
    other.write_text(f'[policy]\ndirectors = ["file-lead"]\n\n'
                     f'[{table}.agents."a@p"]\ncwd = "/w"\n')
    agent = roster.load(path=str(other)).require("a@p")
    assert (agent.directors, agent.policy_directors) == (["box-lead"], ["box-lead"])


@pytest.mark.parametrize("table", ["codex", "opencode"])
def test_the_roster_defaults_refuse_the_opt_out(table):
    from moxi_comms import config

    with pytest.raises(config.ConfigError) as caught:
        config.check_text(f"[{table}.defaults]\ntakes_policy_directors = false\n")
    assert "takes_policy_directors" in str(caught.value)


@pytest.mark.parametrize("table", ["codex", "opencode"])
def test_a_roster_row_refuses_a_non_bool_opt_out(table):
    from moxi_comms import config

    with pytest.raises(config.ConfigError) as caught:
        config.check_text(f'[{table}.agents."a@p"]\ncwd = "/w"\n'
                          'takes_policy_directors = "no"\n')
    assert "takes_policy_directors must be true or false" in str(caught.value)


def test_a_codex_roster_launch_refuses_the_opt_out_flag():
    from moxi_comms.adapters.codex_wrapper import Refused, plan

    roster_rows_for("codex", '[agents."a@p"]\ncwd = "/w"\n')
    with pytest.raises(Refused) as caught:
        plan(codex_args("a@p", "--no-policy-directors"))
    assert "--no-policy-directors" in str(caught.value)


# ------------------------------------------------------------- the claim and holders

def test_the_claim_keeps_the_policy_list():
    from moxi_comms.claimhold import CLAIM_KEYS, ClaimServer

    assert "policy_directors" in CLAIM_KEYS
    server = ClaimServer("helper", "global", kind="test", directors=["lead", "fleet-lead"],
                         policy_directors=["fleet-lead"])
    assert server.claim["policy_directors"] == ["fleet-lead"]


def test_a_harness_holder_writes_both_lists():
    from types import SimpleNamespace

    from moxi_comms.adapters.harness import HarnessAdapter

    class Port(HarnessAdapter):
        KIND = "test"

    agent = SimpleNamespace(name="coder", scope="proj-a", qualified="coder@proj-a",
                            directors=["lead", "fleet-lead"], policy_directors=["fleet-lead"])
    port = Port(agent, autoreconnect=False)
    assert port.claims.claim["directors"] == ["lead", "fleet-lead"]
    assert port.claims.claim["policy_directors"] == ["fleet-lead"]
    assert port._policy_directors() == ("fleet-lead",)


def test_the_claude_shim_claims_both_lists_and_a_respawn_keeps_them():
    import os

    from moxi_comms.adapters import session

    s = session.Session("pd1", name="worker", scope="proj-a", project="proj-a",
                        directors=["lead", "fleet-lead"], policy_directors=["fleet-lead"])
    shim = session.adapter_shim(s, os.getpid())
    try:
        assert shim.policy_directors == ("fleet-lead",)
        assert shim.claims.claim["policy_directors"] == ["fleet-lead"]
        shim.claims.save()
        # A respawn rebuilds the Session from the stamps and never merges again; the
        # claim file gives the new shim both lists.
        again = session.Session("pd1", name="worker", scope="proj-a", project="proj-a")
        respawned = session.adapter_shim(again, os.getpid())
        try:
            assert respawned.directors == ("lead", "fleet-lead")
            assert respawned.policy_directors == ("fleet-lead",)
        finally:
            respawned.stop()
    finally:
        shim.stop(remove_claim_file=True)


def test_a_policy_director_is_guarded_at_delivery(rig):
    from conftest import wait_for_queue

    worker = rig.shim(name="worker", scope="proj-a", directors=["main@proj-a", "lead@proj-a"],
                      policy_directors=["main@proj-a", "lead@proj-a"])
    code, _ = rig.cli(["send", "--posture", "none", "worker@proj-a", "from main"],
                      name="main", scope="proj-a")
    assert code == 0
    wait_for_queue(worker, 1)
    digest = worker.on_turn_boundary()
    assert "directive from main@proj-a (standing)" in digest
    code, _ = rig.cli(["send", "--posture", "none", "worker@proj-a", "from lead"],
                      name="lead", scope="proj-a")
    assert code == 0
    wait_for_queue(worker, 1)
    digest = worker.on_turn_boundary()
    assert "from lead@proj-a" in digest and "directive from" not in digest
    assert "policy director, not provisioned: information standing" in digest


# ------------------------------------------------------------------------- the API

def test_the_api_names_the_provisioned_policy_directors_only():
    from moxi_comms import api

    assert api.policy_directors() == ()
    provision_standing("fleet-lead")
    assert api.policy_directors() == ("fleet-lead",)
    write_config(None, '[policy]\ndirectors = ["ops", "fleet-lead"]\n')
    assert api.policy_directors() == ("fleet-lead",)
    write_config(None, "[policy]\ndirectors = 3\n")
    assert api.policy_directors() == ()
    assert api.effective(["lead"], cfg("fleet-lead"), own="w", use_policy=True) == (
        ["lead", "fleet-lead"], ["fleet-lead"])


def test_the_api_merge_loads_the_boxs_config_when_given_none():
    from moxi import config as moxi_config

    from moxi_comms import api

    assert api.effective(["lead"], own="w", use_policy=True) == (
        ["lead", "fleet-lead"], ["fleet-lead"])
    write_config(None, '[policy]\ndirectors = ["ops"]\n')
    assert api.effective(["lead"], own="w", use_policy=True) == (["lead", "ops"], ["ops"])
    write_config(None, "[policy]\ndirectors = 3\n")
    with pytest.raises(moxi_config.ConfigError):
        api.effective(["lead"], own="w", use_policy=True)
    assert api.effective(["lead"], own="w", use_policy=False) == (["lead"], [])


def test_the_page_row_carries_the_policy_list():
    from moxi_comms import api, pagehandlers

    row = api.RosterRow(qualified="w", name="w", scope=None, live=True, kind=None,
                        module=None, tier="chosen", adapter="claude-shim",
                        directors=("lead", "fleet-lead"), policy_directors=("fleet-lead",))
    out = pagehandlers._row(row)
    assert out["directors"] == ["lead", "fleet-lead"]
    assert out["policy_directors"] == ["fleet-lead"]
