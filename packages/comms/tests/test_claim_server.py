"""`ClaimServer`: a binding held by a module's own process. Its holder is a `self` root,
each child it spawns for the binding a `tree` root written before the child runs and
dropped at its exit. Real processes, real `/proc`, real `SO_PEERCRED`."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from claim_kit import wait_for
from moxi import exit as moxi_exit
from moxi_comms import claimfile
from moxi_comms.claimhold import (CLAIM_SOCKET_ENV, NOT_INSIDE, ClaimServer, ClaimUnreachable,
                                   call)

ASK = ("import json,os,sys\nfrom moxi_comms import claimhold\n"
       "out={}\n"
       "for path in sys.argv[1:]:\n"
       "    a=claimhold.call(path,'claim')\n"
       "    out[path]=a.get('claim',{}).get('qualified') if a.get('ok') else a.get('error')\n"
       "out['env']=os.environ.get('MOXI_COMMS_CLAIM_SOCKET')\n"
       "print(json.dumps(out))\n")


@pytest.fixture
def servers(rig):
    operator = ClaimServer("operator", "global", kind="codex").start()
    archivist = ClaimServer("archivist", "global", kind="opencode").start()
    operator.set_bound(tier="standing", token="operator-token")
    archivist.set_bound(tier="chosen", token="archivist-token")
    yield operator, archivist
    operator.stop()
    archivist.stop()


def test_the_holder_sends_as_each_binding_it_holds(servers):
    operator, archivist = servers
    answer = call(operator.socket_path, "claim")
    assert answer["claim"]["qualified"] == "operator"
    assert "protocol" not in answer and answer["token"] == "operator-token"
    assert call(archivist.socket_path, "claim")["token"] == "archivist-token"


def test_set_bound_requires_and_clears_the_token(rig):
    srv = ClaimServer("relay", "proj-a", kind="codex").start()
    try:
        with pytest.raises(TypeError):
            srv.set_bound(tier="chosen")
        srv.set_bound(tier="chosen", token="first-token")
        assert call(srv.socket_path, "claim")["token"] == "first-token"
        srv.set_offline()
        assert "token" not in call(srv.socket_path, "claim")
        srv.set_bound(tier="chosen", token="second-token")
        srv.set_waiting()
        assert "token" not in call(srv.socket_path, "claim")
        srv.set_bound(tier="chosen", token="third-token")
        srv.holder.name_refused("relay@proj-a")
        assert srv.holder.token is None
        assert "token" not in call(srv.socket_path, "claim")
    finally:
        srv.stop()
    assert srv.holder.token is None


def test_a_child_root_is_inside_its_binding_and_refused_on_the_self_only_one(servers):
    operator, archivist = servers
    child = archivist.spawn([sys.executable, "-c", ASK, str(archivist.socket_path),
                           str(operator.socket_path)], stdout=subprocess.PIPE, text=True)
    out = json.loads(child.communicate(timeout=30)[0])
    assert out[str(archivist.socket_path)] == "archivist"
    assert out[str(operator.socket_path)] == NOT_INSIDE
    assert out["env"] == str(archivist.socket_path)
    assert wait_for(lambda: all(r["pid"] != child.pid
                                for r in claimfile.read(archivist.holder.key)["roots"]))


def test_any_other_child_of_the_holder_is_inside_no_binding(servers):
    operator, archivist = servers
    out = subprocess.run([sys.executable, "-c", ASK, str(archivist.socket_path),
                          str(operator.socket_path)], capture_output=True, text=True,
                         check=True).stdout
    answers = json.loads(out)
    assert answers[str(archivist.socket_path)] == NOT_INSIDE
    assert answers[str(operator.socket_path)] == NOT_INSIDE


def test_state_answers_and_every_old_act_is_an_unknown_op(servers):
    operator, _ = servers
    assert call(operator.socket_path, "state")["claim"] == "operator"
    for op in ("lock", "release", "take", "confirm", "reclaim", "abandon", "contingent",
               "uncontingent"):
        with pytest.raises(ClaimUnreachable, match=f"refused {op}: method not found"):
            call(operator.socket_path, op)


def test_the_file_records_the_holder_as_a_self_root(servers):
    operator, _ = servers
    rec = claimfile.read(operator.holder.key)
    assert [r["reach"] for r in rec["roots"]] == ["self"]
    assert rec["state"] == "bound" and rec["claim"]["qualified"] == "operator"
    assert operator.env() == {CLAIM_SOCKET_ENV: str(operator.socket_path)}


def test_with_its_own_daemon_arm_it_binds_and_goes_offline_with_the_daemon(rig):
    srv = ClaimServer("relay", "proj-a", kind="codex", arm=True).start()
    try:
        assert wait_for(lambda: "relay@proj-a" in rig.daemon.bindings)
        assert srv.holder.state == "bound"
        assert "slot" not in call(srv.socket_path, "claim")["claim"]
    finally:
        srv.stop()
    assert wait_for(lambda: "relay@proj-a" not in rig.daemon.bindings)


def test_a_refused_register_leaves_it_holding_nothing(rig):
    first = ClaimServer("planner", "proj-a", kind="codex", arm=True).start()
    second = ClaimServer("planner", "proj-a", kind="codex", arm=True).start()
    try:
        assert wait_for(lambda: first.holder.state == "bound")
        assert second.holder.state == "waiting" and second.refusal
        assert call(second.socket_path, "claim")["line"] == (
            "planner@proj-a is held by another session")
    finally:
        first.stop()
        second.stop()



def test_a_report_after_stop_writes_no_claim_file(rig):
    """Once `stop()` has removed the claim file and the socket, a late report
    (a supervisor finishing a register, a binding going offline or waiting, a
    reap landing) writes nothing. Otherwise each would write back a dead `srv-` file
    that nothing sweeps."""
    srv = ClaimServer("helper", "proj-a", kind="codex").start()
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                             stdin=subprocess.PIPE)
    try:
        srv.set_bound(tier="chosen", token="first-token")
        assert srv.add_root(child.pid) is not None
        assert claimfile.read(srv.holder.key)["state"] == "bound"
        srv.stop()
        assert claimfile.read(srv.holder.key) is None
        for what, report in (("set_bound", lambda: srv.set_bound(
                                 tier="chosen", token="late-token")),
                             ("set_offline", srv.set_offline),
                             ("set_waiting", srv.set_waiting),
                             ("drop_root", lambda: srv.drop_root(child.pid))):
            report()
            assert claimfile.read(srv.holder.key) is None, what
        # A spawn racing the stop (a module's process sending as it stops) records no
        # root; otherwise `add_root` would write the file back as `bound` with the
        # child as a `tree` root that the reaper, a no-op by then, never drops.
        roots = list(srv.holder.roots)
        root = srv.add_root(child.pid)
        assert claimfile.read(srv.holder.key) is None, \
            ("add_root", claimfile.read(srv.holder.key))
        assert root is None
        late = srv.spawn([sys.executable, "-c", "pass"])
        assert late.wait(timeout=30) == 0
        assert claimfile.read(srv.holder.key) is None, "spawn"
        assert srv.holder.roots == roots
    finally:
        child.communicate()


def test_a_child_whose_root_cant_be_written_still_runs(rig, monkeypatch, tmp_path):
    srv = ClaimServer("relay", "proj-a", kind="codex").start()
    ran = tmp_path / "ran"

    def broken(pid, reach="tree"):
        raise RuntimeError("the claim file can't be written")

    monkeypatch.setattr(srv, "add_root", broken)
    try:
        with pytest.raises(RuntimeError, match="can't be written"):
            srv.spawn([sys.executable, "-c",
                       f"open({str(ran)!r}, 'w', encoding='utf-8').close()"])
        assert wait_for(ran.exists)
    finally:
        srv.stop()


@pytest.mark.skipif(sys.platform != "win32",
                    reason="only Windows starts the child suspended; POSIX gates it")
def test_on_windows_a_child_that_cant_be_resumed_is_ended_and_the_spawn_fails(
        rig, monkeypatch, tmp_path):
    import psutil

    srv = ClaimServer("relay", "proj-a", kind="codex").start()
    ran = tmp_path / "ran"
    started = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        started.append(real_popen(*args, **kwargs))
        return started[-1]

    def refused(self):
        raise psutil.AccessDenied(self.pid)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(psutil.Process, "resume", refused)
    try:
        with pytest.raises(psutil.AccessDenied):
            srv.spawn([sys.executable, "-c",
                       f"open({str(ran)!r}, 'w', encoding='utf-8').close()"])
    finally:
        srv.stop()
    [child] = started
    assert child.returncode is not None and not ran.exists()


# ----------------------------------------------------------------- a claim's line

def line_of(srv) -> str:
    return call(srv.socket_path, "claim")["line"]


def test_a_binding_never_bound_says_so(rig, tmp_path):
    """A claim the daemon has not bound yet says that, not that another session
    holds it: before any report, and through a daemon that is down at start."""
    srv = ClaimServer("newbie", "proj-a", kind="codex").start()
    down = ClaimServer("nobody", "proj-a", kind="codex", arm=True,
                       daemon_socket=tmp_path / "no-daemon.sock").start()
    try:
        assert line_of(srv) == "newbie@proj-a is not bound to comms yet"
        code, out = rig.cli(["send", "--posture", "none", "--new-name",
                             "nobody", "hello"],
                            env={CLAIM_SOCKET_ENV: str(srv.socket_path)})
        assert code == 3
        assert "newbie@proj-a is not bound to comms yet — nothing was sent." in out, out
        assert down.holder.state == "waiting"
        assert line_of(down) == "nobody@proj-a is not bound to comms yet"
    finally:
        srv.stop()
        down.stop()


def test_a_refusal_that_names_no_holder_says_the_daemons_words(rig):
    """A register refused for good (`unknown adapter kind`) names the refusal,
    from the arm's own register and from a holder that reports it."""
    arm = ClaimServer("stranger", "proj-a", kind="no-such-kind", arm=True).start()
    held = ClaimServer("reviewer", "proj-a", kind="codex").start()
    try:
        assert arm.holder.state == "waiting" and arm.refusal
        assert line_of(arm) == f"comms refused stranger@proj-a: {arm.refusal}"
        assert arm.refusal.startswith("unknown adapter kind 'no-such-kind'")
        held.set_bound(tier="chosen", token="held-token")
        held.set_waiting(refusal="unknown adapter kind 'x'")
        assert line_of(held) == (
            "comms refused reviewer@proj-a: unknown adapter kind 'x'")
    finally:
        arm.stop()
        held.stop()


def test_a_held_name_still_says_another_session_holds_it(rig):
    """Only the never-bound and the refused lines are their own: a holder that drops its
    claim to `waiting` without the daemon's words (a link that retries after a
    refusal) still says another session holds it."""
    srv = ClaimServer("planner", "proj-a", kind="codex").start()
    try:
        srv.set_bound(tier="chosen", token="held-token")
        srv.set_waiting()
        assert line_of(srv) == "planner@proj-a is held by another session"
        code, out = rig.cli(["send", "--posture", "none", "--new-name",
                             "nobody", "hello"],
                            env={CLAIM_SOCKET_ENV: str(srv.socket_path)})
        assert code == 3
        assert "planner@proj-a is held by another session — nothing was sent." in out, out
    finally:
        srv.stop()


def test_a_claim_socket_outside_comms_runtime_dir_is_never_asked(
        rig, servers, tmp_path, monkeypatch):
    """A claim socket from the environment must lie inside RUNTIME/comms: one stamped by
    another copy of comms, or by hand, is refused naming the variable and the directory,
    and it is not asked who this process is, though a live server answers there."""
    from moxi_comms import claimhold, paths

    operator, _ = servers
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if sys.platform == "win32":
        # A claim socket is a pipe there: the stray one is named for another runtime folder.
        with monkeypatch.context() as other_home:
            other_home.setenv("MOXI_COMMS_RUNTIME_DIR", str(elsewhere))
            stray_at = paths.claim_socket("stray")
        assert stray_at != paths.claim_socket("stray")
    else:
        stray_at = elsewhere / "c.sock"
    stray = ClaimServer("scout", "proj-a", kind="codex", socket_path=stray_at).start()
    stray.set_bound(tier="chosen", token="scout-token")
    try:
        env = {CLAIM_SOCKET_ENV: str(stray.socket_path)}
        with pytest.raises(paths.OutsideRuntime) as caught:
            claimhold.socket_from_env(env)
        assert CLAIM_SOCKET_ENV in str(caught.value)
        assert str(paths.runtime_dir()) in str(caught.value)

        said: list[str] = []
        assert claimhold.whoami(env, say=said.append) == moxi_exit.REFUSED
        assert said and "outside comms' runtime directory" in said[0]
        assert "scout" not in " ".join(said)

        overlay = claimhold.claim_overlay(dict(env, MOXI_COMMS_NAME="scout",
                                               MOXI_COMMS_SCOPE="proj-a"))
        assert "MOXI_COMMS_NAME" not in overlay and "MOXI_COMMS_SCOPE" not in overlay

        # A send is refused the same way, naming the variable, and nothing is sent.
        code, out = rig.cli(["send", "main", "hello"], env=env)
        assert code == 3, out
        assert "outside comms' runtime directory" in out and CLAIM_SOCKET_ENV in out, out
        assert "Nothing was sent." in out and "scout" not in out, out

        # A socket inside the directory is the one comms asks.
        inside = {CLAIM_SOCKET_ENV: str(operator.socket_path)}
        assert claimhold.socket_from_env(inside) == operator.socket_path
        assert claimhold.socket_from_env({}) is None
    finally:
        stray.stop()
