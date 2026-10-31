"""Live acts on launched sessions, against the real Claude Code: resume, relaunch,
exit-session, recycle and keep-warm. Marked `live`, deselected by a plain run, and run
only with `MOXI_TEST_LIVE=1` (`pytest -m live`).

They use `test_live`'s fixtures and keep its rules: the launcher's state stays in the
test's tmp; slots run on a tmux server of the test's own, its socket under a temp
`MOXI_HOME`, so the default tmux server is never reached; comms' daemon socket follows
that home, so the machine's comms daemon is never reached and no name is bound; every
Claude launch passes `--setting-sources project` on, so the user's own settings and hooks
stay out. Claude itself runs with the real home, where its sign-in is: a copy of the
sign-in in a temp home could be refreshed there and leave the real one stale.

Every act but resume needs turns, and so a Claude launch with turns. turns writes
`endpoint.json` into its own installed package at every Claude launch, which the test
fence refuses: each such test asks turns for a launch's additions first and is skipped,
with that reason, while the fence refuses it.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from moxi import config, proc
from moxi_dev.testing import wait_for
from test_live import (ANSWER_S, CLAUDE_OWN, Terminal, flags, launch_row,  # noqa: F401
                       live_env, moxi_command, needs, needs_turns, own_slots, pane_text,
                       real_home, words, work)

from moxi_launcher import api, compose, exits, first, relaunch, resume, store
from moxi_launcher.compose import common

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(sys.platform == "win32",
                                 reason="these live acts drive a POSIX terminal")]

#: Why a Claude launch with turns is skipped inside the test fence.
CLAUDE_WITH_TURNS = ("turns writes endpoint.json into its own installed package at every "
                     "Claude launch, which the test fence refuses (a turns defect, "
                     "reported to turns)")
#: The tools a session that runs a command of the test's needs, passed on to Claude.
BASH_ALLOWED = ("--allowedTools", "Bash")


def needs_claude_with_turns(work: Path, env: dict[str, str]) -> None:
    """Skip unless a Claude launch with turns can run here: Claude on PATH, a turns that
    offers the interface, and turns' additions given without a write the fence refuses."""
    needs("claude")
    needs_turns()
    try:
        added = compose.launch_additions(cwd=str(work), env=env,
                                         session_id=str(uuid.uuid4()))
    except Exception as error:  # noqa: BLE001  (the fence's refusal, however turns words it)
        if "endpoint.json" in str(error) or isinstance(error, PermissionError):
            pytest.skip(f"{CLAUDE_WITH_TURNS}: {error}")
        raise
    refused = [note for note in added.notes if "endpoint.json" in note]
    if refused:
        pytest.skip(f"{CLAUDE_WITH_TURNS}: {refused[0]}")


def slot_settings() -> None:
    """A slot launch answers Claude's folder-trust screen, as part 1's live tests do."""
    config.set("launcher", "startup.trust_dialog", True, by="launcher live test")
    config.set("launcher", "startup.wait_s", 15, by="launcher live test")


def history(socket: str, pane_id: str, env: dict[str, str]) -> str:
    """The pane's screen and its scrollback, on slots' own server."""
    done = subprocess.run(["tmux", "-S", socket, "capture-pane", "-p", "-S", "-2000",
                           "-t", pane_id], capture_output=True, text=True, env=env,
                          timeout=10)
    return done.stdout


def pane_dead(socket: str, pane_id: str, env: dict[str, str]) -> bool:
    done = subprocess.run(["tmux", "-S", socket, "display-message", "-p", "-t", pane_id,
                           "#{pane_dead}"], capture_output=True, text=True, env=env,
                          timeout=10)
    return done.returncode != 0 or done.stdout.strip() == "1"


def press(socket: str, pane_id: str, env: dict[str, str], *keys: str) -> None:
    subprocess.run(["tmux", "-S", socket, "send-keys", "-t", pane_id, *keys], env=env,
                   capture_output=True, timeout=10, check=True)


def end_as_a_person(socket: str, pane_id: str, env: dict[str, str]) -> None:
    """End the Claude session in the pane as a person does: Ctrl-C, and Ctrl-C again
    while Claude says a second one exits."""
    def gone() -> bool:
        if pane_dead(socket, pane_id, env):
            return True
        press(socket, pane_id, env, "C-c")
        time.sleep(0.5)
        press(socket, pane_id, env, "C-c")
        time.sleep(1.0)
        return pane_dead(socket, pane_id, env)
    wait_for(gone, timeout=30, interval=1.0,
             what=lambda: f"the session in pane {pane_id} to end; saw "
                          f"{pane_text(socket, pane_id, env)[-1500:]}")


def kill_panes(socket: str, env: dict[str, str], *pane_ids: str | None) -> None:
    for pane_id in {p for p in pane_ids if p}:
        subprocess.run(["tmux", "-S", socket, "kill-pane", "-t", pane_id], env=env,
                       capture_output=True)


def launches() -> int:
    conn = store.connect(create=False)
    try:
        return conn.execute("SELECT count(*) FROM launches").fetchone()[0]
    finally:
        conn.close()


def runs_of(request_id: str) -> list[store.Run]:
    conn = store.connect(create=False)
    try:
        return store.runs(conn, request_id=request_id)
    finally:
        conn.close()


def run_events(run_id: str) -> list[store.RunEvent]:
    conn = store.connect(create=False)
    try:
        return store.run_events(conn, run_id)
    finally:
        conn.close()


def processes_running(marker: str) -> list[int]:
    """The pids of the processes whose command line holds `marker`."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            line = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                errors="replace")
        except OSError:
            continue
        if marker in line:
            found.append(int(entry.name))
    return found


def claude_in_slot(work: Path, env: dict[str, str], brief: str, *, extra=(), **typed):
    """A Claude launch in a slot, as a person makes it; returns the result and its row."""
    slot_settings()
    request = api.LaunchRequest(
        typed={**flags("claude"), "target": "slot", "brief": brief, "cwd": str(work),
               **typed},
        extra=(*CLAUDE_OWN, *extra), cwd=str(work))
    result = api.launch(request, environ=env)
    return result, launch_row(result.request_id)


# -- resume: Claude without turns -------------------------------------------------------


def test_an_ended_claude_slot_session_resumes_in_its_slot_with_what_it_said(
        live_env, work, own_slots, monkeypatch):
    """A Claude session launched in a slot answers its brief and is ended by a person.
    The past-sessions list shows it ended and resumable; resume launches it again in the
    same slot with `--resume` and the same session id, and the resumed session shows the
    answer it gave before. While it runs, a second resume of it is refused `live` and
    writes nothing."""
    needs("claude")
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: None)
    brief, answer = words()
    result, row = claude_in_slot(work, live_env, brief)
    new = None
    try:
        assert result.state == "started", result.line
        assert row.slot and row.pane_id and row.session_id, row
        wait_for(lambda: answer in pane_text(own_slots, row.pane_id, live_env),
                 timeout=ANSWER_S, interval=0.5,
                 what=lambda: f"{answer} in pane {row.pane_id}; saw "
                              f"{pane_text(own_slots, row.pane_id, live_env)[-2000:]}")
        end_as_a_person(own_slots, row.pane_id, live_env)

        [listed] = [e for e in resume.sessions(turns=None).data["sessions"]
                    if e["request_id"] == row.request_id]
        assert (listed["live"], listed["resumable"]) == (False, True), listed

        got = resume.resume(row.session_id, turns=None, environ=live_env)
        assert got.exit_code == 0, got.lines
        new = launch_row(got.data["request_id"])
        assert (new.resume_of, new.session_id, new.slot) == (
            row.request_id, row.session_id, row.slot), new
        assert new.argv[1:3] == ["--resume", row.session_id], new.argv
        assert "--session-id" not in new.argv
        assert all(word in new.argv for word in CLAUDE_OWN), new.argv
        wait_for(lambda: answer in history(own_slots, new.pane_id, live_env),
                 timeout=ANSWER_S, interval=0.5,
                 what=lambda: f"{answer} in the resumed pane {new.pane_id}; saw "
                              f"{history(own_slots, new.pane_id, live_env)[-2000:]}")
        conn = store.connect(create=False)
        try:
            assert store.resolve(conn, row.session_id).request_id == new.request_id
        finally:
            conn.close()

        before = launches()
        with pytest.raises(resume.Refused) as refused:
            resume.resume(row.session_id, turns=None, environ=live_env)
        assert refused.value.code == "live", refused.value.problem
        assert launches() == before
    finally:
        kill_panes(own_slots, live_env, row.pane_id if row else None,
                   new.pane_id if new else None)


# -- relaunch: Claude with turns --------------------------------------------------------


def arming_brief(handoff: Path, exported: str, passed: str) -> tuple[str, str]:
    """A brief that writes a handoff and arms a self relaunch with it from the session's
    own shell, exporting two variables there first; and the word the handoff asks for."""
    ask, answer = words()
    command = (f"printf '%s\\n' '{ask}' > {handoff} && export {exported}=1 {passed}=1 && "
               f"{moxi_command()} launcher relaunch --self --handoff {handoff}")
    return (f"Run this one shell command with your Bash tool, exactly as written, then "
            f"end your turn without doing anything else: {command}"), answer


def test_a_self_relaunch_of_claude_in_a_slot_comes_back_with_its_handoff_alone(
        live_env, work, own_slots, tmp_path):
    """The session arms its own relaunch. The run ends `relaunched`; the new launch is in
    the same pane, its handoff arrives as its first turn, and its environment holds the
    new launch's stamp, not the old one's, and no variable the arming shell exported but
    the one `relaunch.pass_env` names."""
    needs_claude_with_turns(work, live_env)
    tag = secrets.token_hex(3).upper()
    exported, passed = f"LIVE_ARMED_{tag}", f"LIVE_PASSED_{tag}"
    config.set("launcher", "relaunch.pass_env", [passed], by="launcher live test")
    config.set("launcher", "relaunch.person_s", 5, by="launcher live test")
    handoff = tmp_path / "handoff.md"
    brief, answer = arming_brief(handoff, exported, passed)
    result, row = claude_in_slot(work, live_env, brief, extra=BASH_ALLOWED)
    new = None
    try:
        assert result.state in ("started", "first-sent"), result.line

        def ended() -> store.Run | None:
            done = [r for r in runs_of(row.request_id) if r.kind == "relaunch"
                    and not r.open]
            return done[0] if done else None
        run = wait_for(ended, timeout=relaunch_bound(), interval=1.0,
                       what=lambda: f"the relaunch run; saw "
                                    f"{pane_text(own_slots, row.pane_id, live_env)[-2000:]}")
        assert run.outcome == "relaunched", (run, run_events(run.run_id))
        new = launch_row(run.new_request_id)
        assert (new.relaunch_of, new.slot, new.pane_id) == (row.request_id, row.slot,
                                                            row.pane_id)
        wait_for(lambda: answer in pane_text(own_slots, new.pane_id, live_env),
                 timeout=ANSWER_S, interval=0.5,
                 what=lambda: f"{answer} from the handoff; saw "
                              f"{pane_text(own_slots, new.pane_id, live_env)[-2000:]}")
        env = proc.environ(new.pid) or {}
        assert env.get(common.REQUEST) == new.request_id
        assert env.get(common.RELAUNCH) == run.run_id
        assert exported not in env and env.get(passed) == "1", sorted(env)
    finally:
        kill_panes(own_slots, live_env, row.pane_id if row else None,
                   new.pane_id if new else None)


def relaunch_bound() -> float:
    return ANSWER_S + 120


def test_a_relaunch_armed_while_a_client_types_waits_and_says_person(live_env, work,
                                                                     own_slots,
                                                                     tmp_path):
    """A tmux client attached to the slot types while the run waits: the run's status
    shows it waiting on `person`, and nothing is exited meanwhile."""
    needs_claude_with_turns(work, live_env)
    tag = secrets.token_hex(3).upper()
    config.set("launcher", "relaunch.person_s", 30, by="launcher live test")
    handoff = tmp_path / "handoff.md"
    brief, _ = arming_brief(handoff, f"LIVE_ARMED_{tag}", f"LIVE_PASSED_{tag}")
    result, row = claude_in_slot(work, live_env, brief, extra=BASH_ALLOWED)
    client = Terminal(["/usr/bin/env", "tmux", "-S", own_slots, "attach", "-t",
                       row.pane_id], live_env, work)
    try:
        def armed() -> store.Run | None:
            found = [r for r in runs_of(row.request_id) if r.kind == "relaunch"]
            return found[0] if found else None
        run = wait_for(armed, timeout=ANSWER_S, interval=0.5,
                       what=lambda: f"the armed run; saw "
                                    f"{pane_text(own_slots, row.pane_id, live_env)[-2000:]}")

        def says_person() -> bool:
            client.press(b" ")
            return any(e.kind == "wait" and e.detail.get("word") == "person"
                       for e in run_events(run.run_id))
        wait_for(says_person, timeout=60, interval=2.0, what="a wait on person")
        shown = relaunch.status(run.run_id)
        assert "person" in " ".join(shown.lines), shown.lines
        current = [r for r in runs_of(row.request_id) if r.run_id == run.run_id][0]
        assert current.open and not pane_dead(own_slots, row.pane_id, live_env)
    finally:
        client.close()
        kill_panes(own_slots, live_env, row.pane_id if row else None)


def test_a_director_relaunch_of_claude_in_a_slot(live_env, work, own_slots):
    """A director session relaunches a session that names it, through comms."""
    needs_claude_with_turns(work, live_env)
    pytest.skip("a director is a comms identity proven by comms' daemon, and no fixture "
                "here runs a comms daemon of the test's own")


# -- exit-session: Claude with turns ----------------------------------------------------


def test_exit_session_ends_claude_through_the_exit_dialog_and_its_background_shell(
        live_env, work, own_slots):
    """The session starts a background shell and answers. exit-session exits it through
    turns and answers Claude's exit dialog through slots: the verdict is `exited`, the
    exit log holds it, the pane is dead, and the background shell is gone."""
    needs_claude_with_turns(work, live_env)
    seconds = 900 + secrets.randbelow(90000)
    ask, answer = words()
    brief = (f"Start the shell command `sleep {seconds}` in the background with your Bash "
             f"tool's background option, then: {ask}")
    result, row = claude_in_slot(work, live_env, brief, extra=BASH_ALLOWED)
    marker = f"sleep {seconds}"
    try:
        wait_for(lambda: answer in pane_text(own_slots, row.pane_id, live_env)
                 and processes_running(marker), timeout=ANSWER_S, interval=0.5,
                 what=lambda: f"{answer} and {marker}; saw "
                              f"{pane_text(own_slots, row.pane_id, live_env)[-2000:]}")
        done = exits.exit_session(row.session_id, environ=live_env)
        assert (done.exit_code, done.data["outcome"]) == (0, "exited"), done.lines
        assert pane_dead(own_slots, row.pane_id, live_env)
        wait_for(lambda: not processes_running(marker), timeout=30, interval=0.5,
                 what=f"{marker} to end")
        conn = store.connect(create=False)
        try:
            [logged] = [e for e in store.exits_log(conn) if e.request_id == row.request_id]
        finally:
            conn.close()
        assert logged.verdict == "exited"
    finally:
        for pid in processes_running(marker):
            os.kill(pid, 15)
        kill_panes(own_slots, live_env, row.pane_id if row else None)


# -- recycle and keep-warm ---------------------------------------------------------------


def test_a_clear_and_a_compact_on_expiry(live_env, work, own_slots):
    """A launch with `recycle_on_expiry` cleared and one compacted once its cache reads
    cold."""
    needs_claude_with_turns(work, live_env)
    pytest.skip("a Claude session's cache lifetime comes from its own request read (most "
                "often an hour), and cache offers no lifetime a test can shorten for it")


def test_a_keep_warm_nudge_while_a_subagent_runs(live_env, work, own_slots):
    """A keep-warm launch nudged inside its window while a subagent of it runs."""
    needs_claude_with_turns(work, live_env)
    pytest.skip("keep-warm asks from 15 to 6 minutes before the cache expires, which for "
                "a Claude session is most of an hour after its last request")


def test_a_compact_at_the_limit(live_env, work, own_slots):
    """A launch with `recycle_at_limit = compact` compacted when usage says its context
    crossed `limit_at`."""
    pytest.skip("usage is not built: recycle_at_limit is not offered, and no crossing "
                "comes")


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS asks run on macOS")
def test_on_macos_a_keep_warm_ask_and_an_expiry_ask_fire(live_env, work):
    needs_claude_with_turns(work, live_env)
    pytest.skip("as on Linux, a Claude session's cache lifetime can't be shortened")
