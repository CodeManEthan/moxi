"""The four verbs that ACT: send / reply / agents / history. (`help` reads: test_help.py.)"""

import os

import pytest
from conftest import no_cold_at, queued, wait_for_queue, wait_until, write_config


from moxi_comms import constants, machine_line


def test_send_to_an_unbound_ephemeral_is_an_honest_error(rig):
    code, out = rig.cli(["send", "--posture", "none", "agent-9", "hello?"],  name="planner", scope="proj-a")
    assert code == 3
    assert "no such ephemeral identity" in out
    assert "never reused" in out
    assert rig.record.history(rig.names_channel) == [], "nothing was posted"


def test_send_to_a_live_ephemeral_works(rig):
    ghost = rig.shim()
    code, out = rig.cli(["send", "--posture", "none", ghost.identity, "you there?"],  name="planner",
                        scope="proj-a")
    assert code == 0
    wait_for_queue(ghost, 1)
    assert ghost.queue[0]["text"] == "you there?"


def test_send_without_an_identity_is_refused(rig):
    """No claim in the environment, no send -- and no escape hatch: the refusal names
    the only place an identity may come from, which is launch. It does NOT name a seat:
    the agent reading it cannot stamp one, and slots are the operator's inventory."""
    rig.shim(name="reviewer", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a", "hi"])
    assert code == 3
    assert out.strip() == NO_CLAIM
    assert "slot" not in out.lower() and "seat" not in out.lower()
    assert rig.record.history(rig.names_channel) == [], "nothing was posted"


NO_CLAIM = ("comms has no identity for this process: nothing was sent. A session sends "
            "as its identity through its own claim socket, which its comms adapter gives "
            "it at launch, and this process has none. Launching is the operator's; ask "
            "them.")


def test_a_slot_stamp_without_a_name_is_no_claim_at_all(rig):
    """Identity comes from the claim-stamped environment, full stop; a process with no
    claim is refused at the verb. A bare MOXI_COMMS_SLOT used to synthesize `slot-1`
    and wear it on the wire -- a fabricated identity, once
    reachable from `moxi-comms-claude --comm-slot 3`. A slot is a webhook bundle, never a name."""
    rig.shim(name="reviewer", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a", "hi"])
    assert code == 3
    assert out.strip() == NO_CLAIM
    assert "slot" not in out.lower() and "seat" not in out.lower()
    assert rig.record.history(rig.names_channel) == [], "nothing was posted"


def test_send_when_the_daemon_is_down_says_comms_offline(rig):
    rig.daemon.stop()
    code, out = rig.cli(["send", "--posture", "none", "reviewer", "hi"],  name="planner", scope="proj-a")
    assert code == 75 and "comms offline" in out


def test_agents_lists_the_names_you_can_message(rig):
    """The default rendering is the AGENT-facing one: qualified name, tier, adapter,
    one row per messageable binding, and nothing else: no seat (a slot is
    webhook-bundle capacity the operator provisions) and no waiting mail."""
    rig.shim(name="planner", scope="proj-a")
    rig.shim()
    code, out = rig.cli(["send", "--posture", "none", "--new-name", "curator@proj-a",
                         "waiting"],  name="planner", scope="proj-a")
    assert code == 0, out
    # The mail is counted in the answer's `waiting`, never in the default view.
    assert wait_until(lambda: "curator@proj-a" in
                      (rig.daemon._agents_query({}).get("waiting") or {}))
    code, out = rig.cli(["agents"])
    assert code == 0
    assert out == (
        "agent-1                  ephemeral  adapter claude-shim\n"
        "planner@proj-a           chosen     adapter claude-shim\n")
    assert "slot" not in out and "seat" not in out


def test_agents_seats_is_the_operators_view(rig):
    """`--seats` shows the full binding table. The snapshot never passes the flag."""
    rig.shim(name="planner", scope="proj-a")
    rig.shim()
    code, out = rig.cli(["agents", "--seats"])
    assert code == 0
    assert out == (
        "agent-1                  ephemeral  adapter claude-shim\n"
        "planner@proj-a           chosen     adapter claude-shim\n")


def test_agents_when_the_daemon_is_down(rig):
    rig.daemon.stop()
    code, out = rig.cli(["agents"])
    assert code == 75 and "Nothing is addressable" in out


def test_reply_obeys_the_send_boundary_and_threads(rig):
    planner = rig.shim(name="planner", scope="proj-a")
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    rig.cli(["send", "--posture", "open", "reviewer@proj-a", "question?"],  name="planner", scope="proj-a")
    wait_for_queue(reviewer, 1)
    mid = reviewer.queue[0]["message_id"]

    code, out = rig.cli(["reply", mid, "answer."],  name="reviewer", scope="proj-a")
    assert code == 0
    # The verb reads the record, not the daemon's live table -- and with the custody
    # binding history the record is enough: the webhook proves the SEAT, the binding history
    # names the one holder of that seat at the message's timestamp, and the display
    # agrees with them. Two independent facts, so the same `verified` a live frame gets:
    # a chosen agent that has merely stopped being live is still attributed for words it
    # provably said. `test_custody.py` pins the edges.
    assert "replying to planner@proj-a (chosen, verified)" in out
    wait_for_queue(planner, 1)
    assert planner.queue[0]["reply_to"] == mid
    # the correlation is IN the record, not in a Discord field the webhook cannot write:
    # the verb wrote `re:<id>` onto line zero and the daemon parsed the payload from it.
    posted = rig.record.history(rig.names_channel)[-1]
    assert no_cold_at(posted.content).splitlines()[0] == f"::to planner@proj-a re:{mid}"
    assert machine_line.parse(posted.content).reply_to == mid
    assert not hasattr(posted, "reply_to")
    # ...and the machine line is still transport: the token never reaches the model
    assert "re:" not in planner.queue[0]["text"]
    digest = planner.on_turn_boundary()
    assert "::to" not in digest, "line zero is transport; it never reaches the model"
    assert f"re:{mid}" not in digest, "...and neither does its unspaced wire spelling"
    assert f"· re: {mid}" in digest, "the frame line says the correlation instead"


def test_a_reply_id_that_is_not_a_snowflake_is_refused_at_the_verb(rig):
    """The token IS the machine line: a malformed id would make line zero prose, and the
    message would land in the record addressed to nobody. Refuse before posting."""
    rig.shim(name="reviewer", scope="proj-a")
    code, out = rig.cli(["send", "reviewer", "hi", "--reply-to", "not-a-snowflake"],
                         name="planner", scope="proj-a")
    assert code == 2 and "Nothing was sent" in out
    assert rig.record.history(rig.names_channel) == []


def test_history_reads_the_referent_out_of_the_record(rig):
    """`moxi comms history` surfaces reply_to from the machine line -- the record is
    self-describing, and no Discord field is consulted."""
    first = rig.post_as("planner@proj-a", ["reviewer@proj-a"], "question?")
    rig.post_as("curator@proj-a", ["reviewer@proj-a"], "answer.", reply_to=first.id)
    code, out = rig.cli(["history", "reviewer@proj-a"])
    assert code == 0 and f"re:{first.id}" in out


def test_reply_to_a_missing_message(rig):
    code, out = rig.cli(["reply", "1400000009999", "hi"],  name="planner",
                        scope="proj-a")
    assert code == 3 and "nothing to reply to" in out


def _old_conference_room(rig):
    """What an install from before one room still carries: a `#conference` rooms row."""
    from moxi_comms import directory
    from moxi_comms.paths import comms_db

    channel_id = rig.record.create_channel("#conference")
    conn = directory.connect(comms_db())
    try:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute("INSERT INTO rooms(room_id, name, kind, created_at) "
                     "VALUES (?, '#conference', 'conference', ?) "
                     "ON CONFLICT(room_id) DO UPDATE SET kind = excluded.kind",
                     (channel_id, directory.now()))
        conn.commit()
    finally:
        conn.close()
    return channel_id


def test_a_reply_posts_in_names_whatever_room_the_original_sits_in(rig):
    """One room. A reply to a verified message an older install's `#conference`
    holds still posts in `#names`, and says so."""

    rig.shim(name="herald", global_=True)
    rig.shim(name="reviewer", scope="proj-a")
    conference = _old_conference_room(rig)
    original = rig.record.post_as_identity(
        "herald", conference, machine_line.compose(["reviewer@proj-a"], "question?"))
    assert original.channel_id == conference

    code, out = rig.cli(["reply", original.id, "answer."],  name="reviewer",
                        scope="proj-a")
    assert code == 0, out
    assert out.splitlines()[0] == ("replying to herald (standing, verified) "
                                   "in #names")
    assert out.splitlines()[-1].endswith(" in #names")
    posted = rig.record.history(rig.names_channel)[-1]
    assert posted.channel_id == rig.names_channel
    assert machine_line.parse(posted.content).reply_to == original.id
    assert [m.id for m in rig.record.history(conference)] == [original.id], \
        "nothing was posted in #conference"


def test_history_of_agent_n_is_time_scoped_to_the_binding(rig):
    ghost = rig.shim()                                    # agent-1, window opens
    rig.post_as("agent-6", ["agent-1"], "inside the binding")
    wait_for_queue(ghost, 1)
    rig.clock.advance(10)
    ghost.stop()
    wait_until(lambda: "agent-1" not in rig.daemon.bindings)    # window closes
    rig.clock.advance(60)
    rig.post_as("agent-6", ["agent-1"], "long after that agent was gone")

    code, out = rig.cli(["history", "agent-1"])
    assert code == 0
    assert "inside the binding" in out
    assert "long after that agent was gone" not in out
    assert "time-scoped to the agent-1 binding" in out


def test_history_of_an_agent_n_the_daemon_never_saw(rig):
    code, out = rig.cli(["history", "agent-42"])
    assert code == 0 and "never reused" in out


def test_history_needs_the_daemon_only_for_agent_n(rig):
    rig.post_as("agent-6", ["curator@proj-a"], "still readable")
    rig.daemon.stop()
    code, out = rig.cli(["history", "curator@proj-a"])
    assert code == 0 and "still readable" in out
    code, out = rig.cli(["history", "agent-1"])
    assert code == 75 and "rather than guessing" in out


def test_history_is_empty_honestly(rig):
    code, out = rig.cli(["history", "nobody@proj-a"])
    assert code == 0 and "no #names history for nobody@proj-a" in out


def test_send_multicast_lists_every_deferred_name(rig):
    rig.shim(name="reviewer", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "--new-name",
                         "reviewer@proj-a,curator@proj-a,archivist@proj-a", "standup"],
                         name="planner", scope="proj-a")
    assert code == 0
    assert "curator@proj-a is not bound" in out
    assert "archivist@proj-a is not bound" in out
    assert "reviewer@proj-a is not bound" not in out
    assert len(rig.record.history(rig.names_channel)) == 1


def test_standing_declaration_sends_normally_when_the_row_exists(rig):
    main = rig.shim(name="main", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "main@proj-a", "declared", "--standing"],
                         name="planner", scope="proj-a")
    assert code == 0 and "sent" in out
    wait_for_queue(main, 1)
    assert main.queue[0]["text"] == "declared"


def test_standing_declaration_works_unbound_too(rig):
    """The flag checks the DIRECTORY, not liveness: an unbound standing name defers."""
    code, out = rig.cli(["send", "--posture", "none", "main@proj-a", "waiting for you", "--standing"],
                         name="planner", scope="proj-a")
    assert code == 0
    assert "main@proj-a is not bound right now" in out


def test_standing_declaration_refuses_a_name_with_no_row(rig):
    code, out = rig.cli(["send", "--posture", "none", "ops@proj-a", "are you there", "--standing"],
                         name="planner", scope="proj-a")
    assert code == 3
    assert "declared standing" in out
    assert "'ops@proj-a' has no standing row" in out
    assert "treated as a chosen name" in out
    assert rig.record.history(rig.names_channel) == [], "nothing reaches the record"


def test_standing_declaration_refuses_a_live_chosen_binding(rig):
    """The point of the flag: bound and reachable is not the same as declared."""
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a", "hi", "--standing"],
                         name="planner", scope="proj-a")
    assert code == 3
    assert "'reviewer@proj-a' has no standing row" in out
    assert "currently bound" in out
    assert rig.record.history(rig.names_channel) == []
    assert reviewer.queue == []


def test_standing_declaration_refuses_an_ephemeral(rig):
    ghost = rig.shim()
    code, out = rig.cli(["send", "--posture", "none", ghost.identity, "hi", "--standing"],
                         name="planner", scope="proj-a")
    assert code == 3 and "has no standing row" in out
    assert rig.record.history(rig.names_channel) == []


def test_standing_declaration_is_scoped(rig):
    """`main` exists standing in proj-a and proj-b, but not in proj-c."""
    code, _ = rig.cli(["send", "--posture", "none", "main@proj-b", "ok", "--standing"],  name="planner",
                      scope="proj-b")
    assert code == 0
    code, out = rig.cli(["send", "--posture", "none", "main@proj-c", "nope", "--standing"],  name="planner",
                        scope="proj-c")
    assert code == 3 and "'main@proj-c' has no standing row" in out


def test_standing_declaration_refuses_the_whole_multicast(rig):
    code, out = rig.cli(["send", "--posture", "none", "main@proj-a,ops@proj-a", "standup", "--standing"],
                         name="planner", scope="proj-a")
    assert code == 3 and "'ops@proj-a' has no standing row" in out
    assert rig.record.history(rig.names_channel) == [], "one post, so all or nothing"


def test_reply_standing_declaration_applies_to_the_derived_target(rig):
    main = rig.shim(name="main", scope="proj-a")             # standing author
    reviewer = rig.shim(name="reviewer", scope="proj-a")     # chosen author
    rig.cli(["send", "--posture", "once", "reviewer@proj-a", "from a standing identity"],  name="main",
            scope="proj-a")
    wait_for_queue(reviewer, 1)
    standing_msg = reviewer.queue[0]["message_id"]

    code, out = rig.cli(["reply", standing_msg, "acknowledged", "--standing"],
                         name="reviewer", scope="proj-a")
    assert code == 0
    wait_for_queue(main, 1)

    rig.post_as("ghost@proj-a", ["reviewer@proj-a"], "and me")
    wait_until(lambda: len(reviewer.queue) >= 2)
    chosen_msg = reviewer.queue[1]["message_id"]
    posted = len(rig.record.history(rig.names_channel))
    code, out = rig.cli(["reply", chosen_msg, "acknowledged", "--standing"],
                         name="reviewer", scope="proj-a")
    assert code == 3
    assert "'ghost@proj-a' has no standing row" in out
    assert len(rig.record.history(rig.names_channel)) == posted


def test_without_the_flag_behaviour_is_unchanged(rig):
    code, out = rig.cli(["send", "--posture", "none", "--new-name", "ops@proj-a",
                         "are you there"],  name="planner", scope="proj-a")
    assert code == 0
    assert "ops@proj-a is not bound right now" in out
    assert len(rig.record.history(rig.names_channel)) == 1


def test_the_declaration_never_reaches_the_record(rig):
    rig.shim(name="main", scope="proj-a")
    rig.cli(["send", "--posture", "none", "main@proj-a", "body", "--standing"],  name="planner", scope="proj-a")
    content = rig.record.history(rig.names_channel)[-1].content
    assert no_cold_at(content).splitlines()[0] == "::to main@proj-a [no reply]"
    assert "standing" not in content


def test_constants_are_the_recorded_build_rulings():
    """Tunable numbers; the test pins them so a change is deliberate."""
    assert (constants.REPLAY_N, constants.REPLAY_T) == (50, 86400)
    assert constants.RETRY_ATTEMPTS == 5
    assert (constants.BACKOFF_START, constants.BACKOFF_FACTOR, constants.BACKOFF_MAX) == (
        0.5, 2.0, 30.0)


def test_send_with_name_but_no_slot_borrows_the_live_bindings_slot(rig):
    """The wrapper stamps MOXI_COMMS_NAME at spawn; the slot is assigned at REGISTER,
    after the env was handed over. The send verb asks the daemon for its own live
    binding rather than failing over a fact only the daemon has."""
    rig.shim(name="planner", scope="proj-a")   # the session's own binding
    ghost = rig.shim()
    code, out = rig.cli(["send", "--posture", "none", ghost.identity, "no slot in my env"],
                        name="planner", scope="proj-a")
    assert code == 0, out
    wait_for_queue(ghost, 1)
    assert ghost.queue[0]["text"] == "no slot in my env"
    assert ghost.queue[0]["author"]["name"] == "planner@proj-a"


def test_send_with_name_no_slot_binds_for_each_call_when_all_slots_are_full(rig):
    """The rig's stand-in binds the stamped name without consuming a slot."""
    names = ("reviewer", "curator", "coder", "critic", "scout", "pilot")
    shims = [rig.shim(name=name, scope="proj-a")
             for slot, name in enumerate(names, 1)]
    for text in ("first", "second"):
        code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a", text],
                             name="planner", scope="proj-a")
        assert code == 0, out
        assert "planner@proj-a" not in rig.daemon.bindings
    rows = rig.record.history(rig.names_channel)
    assert [(row.author_kind, row.author_id) for row in rows] == [
        ("identity", "planner@proj-a"), ("identity", "planner@proj-a")]
    wait_for_queue(shims[0], 2)


# ------------------------------------------------------- identity is the claim's
# There is no way to spend one identity's credential while wearing another's name:
# the claim in the environment decides BOTH, and nothing on the command line can
# separate them: spending another binding's credential would be an attribution hazard.

def test_the_claim_decides_the_credential_and_the_name_together(rig):
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a", "hi"],  name="planner", scope="proj-a")
    assert code == 0, out
    posted = rig.record.history(rig.names_channel)[-1]
    assert posted.author_display == "planner@proj-a", "the claim is the speaker"
    assert (posted.author_kind, posted.author_id) == ("identity", "planner@proj-a")
    wait_for_queue(reviewer, 1)
    assert reviewer.queue[0]["author"]["name"] == "planner@proj-a"


def test_a_global_claim_keeps_its_scope_and_is_stamped_by_the_daemon(rig):
    """The claim's global scope determines the identity the daemon stamps."""
    rig.shim(name="reviewer", scope="proj-a")
    code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a", "from global"],
                         name="herald", env={"MOXI_COMMS_GLOBAL": "1"})
    assert code == 0, out
    posted = rig.record.history(rig.names_channel)[-1]
    assert posted.author_display == "herald", "global renders bare, not herald@proj-a"
    assert (posted.author_kind, posted.author_id) == ("identity", "herald")

    code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a", "from proj-b"],
                         name="planner", scope="proj-b")
    assert code == 0, out
    assert rig.record.history(rig.names_channel)[-1].author_display == "planner@proj-b"


# --------------------------------------------------------------------------- bare names
# A bare name is the global identity from every scope: no own-scope step, no binding history
# step, no hint naming other scopes at resolution.

def test_a_bare_name_of_a_known_global_identity_defers_globally_not_into_my_scope(rig):
    lead = rig.shim(name="lead", global_=True)
    assert lead.identity == "lead"
    lead.stop()                                           # unbound now, but it EXISTS
    assert wait_until(lambda: "lead" not in rig.daemon.bindings)

    code, out = rig.cli(["send", "--posture", "none", "lead", "for the global lead"],
                         name="planner", scope="proj-a")
    assert code == 0
    assert "lead is not bound right now" in out and "lead@proj-a" not in out
    assert wait_until(lambda: queued(rig.daemon, "lead"))
    assert not queued(rig.daemon, "lead@proj-a")

    again = rig.shim(name="lead", global_=True)   # ...and the rebind gets it
    wait_for_queue(again, 1)
    assert again.queue[0]["text"] == "for the global lead"


def test_a_bare_name_known_only_in_another_scope_is_the_global_name(rig):
    other = rig.shim(name="reviewer", scope="proj-b")
    other.stop()
    assert wait_until(lambda: "reviewer@proj-b" not in rig.daemon.bindings)
    before = len(rig.record.history(rig.names_channel))

    # The global reviewer has never been bound, so the bare name is refused without
    # --new-name: proj-b's binding says nothing about it
    code, out = rig.cli(["send", "--posture", "none", "reviewer", "meant for proj-b's reviewer"],
                         name="planner", scope="proj-a")
    assert code == 3, out
    assert out.startswith("reviewer: comms has never seen reviewer bound")
    assert len(rig.record.history(rig.names_channel)) == before

    code, out = rig.cli(["send", "--posture", "none", "--new-name", "reviewer",
                         "meant for proj-b's reviewer"],  name="planner", scope="proj-a")
    assert code == 0, out
    assert "reviewer is not bound right now" in out
    assert len(rig.record.history(rig.names_channel)) == before + 1
    assert wait_until(lambda: queued(rig.daemon, "reviewer"))
    assert not queued(rig.daemon, "reviewer@proj-a")
    assert not queued(rig.daemon, "reviewer@proj-b")


def test_a_bare_name_nobody_has_ever_held_defers_globally(rig):
    """First contact: a bare name is the global identity, never the sender's scope."""
    code, out = rig.cli(["send", "--posture", "none", "--new-name", "newcomer", "first ever"],
                         name="planner", scope="proj-a")
    assert code == 0, out
    assert "newcomer is not bound right now" in out
    assert wait_until(lambda: queued(rig.daemon, "newcomer"))
    assert not queued(rig.daemon, "newcomer@proj-a")


def test_a_bare_name_known_in_my_own_scope_still_defers_globally(rig):
    mine = rig.shim(name="reviewer", scope="proj-a")
    mine.stop()
    assert wait_until(lambda: "reviewer@proj-a" not in rig.daemon.bindings)
    theirs = rig.shim(name="reviewer", global_=True)
    theirs.stop()
    assert wait_until(lambda: not rig.daemon.bindings)

    code, out = rig.cli(["send", "--posture", "none", "reviewer", "no own scope first"],
                         name="planner", scope="proj-a")
    assert code == 0
    assert "reviewer is not bound right now" in out
    assert "reviewer@proj-a" not in out


# ------------------------------------------- the [policy] table

def test_the_policy_table_is_refused_when_malformed():
    from moxi_comms import config

    for body, match in (('cache_callers = "x"\n', "cache_callers"),
                        ('whoever = ["x"]\n', "whoever"),
                        ('cache_callers = [""]\n', "cache_callers"),
                        ('labels = { "a@b" = 3 }\n', "labels")):
        write_config(None, "[policy]\n" + body)
        with pytest.raises(config.ConfigError, match=match):
            config.load()


@pytest.mark.parametrize("key", ["never_held", "next_turn_refused"])
def test_a_removed_policy_key_is_refused_with_its_fix(key):
    """A config file that still names a removed `[policy]` key fails with what to do,
    not as an unknown key."""
    from moxi_comms import config

    write_config(None, f'[policy]\n{key} = ["a@b"]\ncache_callers = ["pal@x"]\n')
    with pytest.raises(config.ConfigError) as exc:
        config.load()
    assert key in str(exc.value) and "remove it" in str(exc.value)


def test_the_cache_callers_are_the_whole_list():
    from moxi_comms import policy

    assert policy.for_env().cache_callers == ("operator",)
    write_config(None, '[policy]\ncache_callers = ["operator", "pal@x"]\n')
    assert policy.for_env().cache_callers == ("operator", "pal@x")
    write_config(None, '[policy]\ncache_callers = ["pal@x"]\n')
    assert policy.for_env().cache_callers == ("pal@x",), "no default is added"


def test_the_send_only_refusal_reads_the_labels():
    from moxi_comms import cli

    assert cli.send_only_refusal("console@acme").startswith(
        "console@acme is a send-only identity and receives no mail; nothing was sent.")
    write_config(None, '[policy]\nlabels = { "console@acme" = '
                       '"the build console\'s send-only identity" }\n')
    assert cli.send_only_refusal("console@acme").startswith(
        "console@acme is the build console's send-only identity and receives no mail")
    assert cli.send_only_refusal("pager@acme").startswith(
        "pager@acme is a send-only identity")


def test_main_takes_prog_and_its_help_names_moxi_comms(capsys):
    """The umbrella calls `main(argv, prog="moxi comms")`; the usage line names it."""
    import inspect

    from moxi_comms import cli

    params = inspect.signature(cli.main).parameters
    assert "argv" in params and params["prog"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["prog"].default == "moxi comms"
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert capsys.readouterr().out.startswith("usage: moxi comms ")
    with pytest.raises(SystemExit):
        cli.main(["send", "--help"], prog="comms")
    assert capsys.readouterr().out.startswith("usage: comms send ")


def test_a_config_that_does_not_load_refuses_the_verb(rig):
    write_config(None, "[policy]\nwhoever = 1\n")
    code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a", "hi"],
                         name="planner", scope="proj-a", env=dict(os.environ))
    assert code == 3 and "whoever" in out, out


def test_a_message_over_the_one_cap_is_refused_with_the_pointer_text(rig):
    """One cap, 16,000, on both backends; over it nothing is sent, before
    any credential, and `--check` refuses the same way. Comms does not split."""
    from moxi_comms import record

    assert rig.record.max_content_length == record.MAX_CONTENT_LENGTH == 16_000
    rig.shim(name="reviewer", scope="proj-a")
    # A warm reviewer's cold-at is a ten-digit time: the line's length is known.
    line = "::to reviewer@proj-a [cold-at:1234567890] [no reply]"
    fits = "x" * (16_000 - len(line) - 1)
    over = fits + "x"
    want = (f"that message is 16001 characters with its routing line, over the "
            "16000-character cap on one message. Nothing was sent. Put the content "
            "somewhere it can live (a file, a note, a paste) and send a pointer to it "
            "instead.")
    for argv in (["send", "--posture", "none", "reviewer@proj-a", over],
                 ["send", "--check", "--posture", "none", "reviewer@proj-a", over]):
        code, out = rig.cli(argv,  name="planner", scope="proj-a")
        assert (code, out.strip()) == (3, want)
    assert rig.record.history(rig.names_channel) == []
    code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a", fits],
                        name="planner", scope="proj-a")
    assert code == 0, out
    (msg,) = rig.record.history(rig.names_channel)
    assert no_cold_at(msg.content) == f"::to reviewer@proj-a [no reply]\n{fits}"


def test_comms_help_names_no_owner_and_its_braces_name_every_other_verb():
    """`owner` is a query for a wrapper that keeps sessions warm, asking whether a
    session it may nudge has an ask out; it is not a verb an agent reads about. It leaves the braces and the rows; it still parses and runs."""
    from moxi_comms import cli

    parser = cli.build_parser()
    sub = [a for a in parser._actions if a.dest == "verb"][0]
    help_text = parser.format_help()
    assert "owner" not in help_text
    listed = [v for v in sub.choices if v != "owner"]
    assert "{" + ",".join(listed) + "}" in help_text
    assert all(f"\n    {v} " in help_text or f"\n    {v}\n" in help_text for v in listed)
    assert "owner" in sub.choices
    assert sub.choices["owner"].get_default("func") is cli.cmd_owner
    assert parser.parse_args(["owner", "abc", "--json"]).json
