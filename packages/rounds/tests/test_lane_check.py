"""`check` with lanes: where a run of the repo's check goes (here, a lane that is free
now, or here waiting), `--urgent` and `--lane`, which checks may go, the record of a run
on a lane, and the gate and its readers. The lane is the stand-in transport of
`test_lane_send`: a `serve` on this machine with folders of its own, so the far side has
its own check lock, apart from this machine's."""

from __future__ import annotations

import json
import shlex
import threading
import types
from pathlib import Path

import pytest
from test_check import PY, ROUND, build, folded, ran, repo  # noqa: F401 -- fixtures
from test_lane_send import Lane

from moxi_rounds import board, builds, check_line, checking, serving, showing
from moxi_rounds import settings as own
from moxi_rounds.cli import main
from moxi_rounds.errors import BadArgument, Refused

#: The check: prints, as JSON, its words, its folder, and whether this machine's check
#: lock is free and holds no ticket while it runs.
SCRIPT = """
import json, os, sys
from moxi import files
from moxi_rounds import check_line
lock = sys.argv[1]
try:
    with files.lock(lock, timeout_s=0):
        free = True
except Exception:
    free = False
print(json.dumps({"words": sys.argv[2:], "cwd": os.getcwd(), "here_free": free,
                  "tickets": len(check_line.tickets(__import__("pathlib").Path(lock)))}))
sys.exit(int(os.environ.get("CHECK_EXIT", "0")))
"""

UNREACHABLE = [PY, "-c", "import sys; sys.stderr.write('no route to host\\n'); "
               "sys.exit(255)"]


class Lanes:
    """The project `proj-a`'s check, the script above, and the lane `far`."""

    def __init__(self, write_config, tmp_path: Path):
        self.write_config = write_config
        self.lane = Lane(tmp_path, grace_s=checking.GRACE_S)
        self.script = tmp_path / "lane_check.py"
        self.script.write_text(SCRIPT, encoding="utf-8")
        self.words = [PY, str(self.script), str(checking.lock_file())]

    def configure(self, *, lane: str = "", project: str = "", machine: str = "",
            ssh: list[str] | None = None, check: str = "", named: bool = True,
            scoped: bool = False) -> None:
        command = shlex.join([*self.words, "here", "{root}"])
        more = "" if not scoped else (
            f', scoped = {json.dumps(shlex.join([*self.words, "scoped", "{base}"]))}')
        lane_table = "" if not named else f"\n[projects.proj-a.lanes.far]\n{lane}\n"
        self.write_config(
            f"[lanes.far]\nhost = \"far-test\"\nssh = "
            f"{json.dumps(ssh or self.lane.table['ssh'])}\n{machine}\n"
            f"[projects.proj-a]\ncheck = {{ command = {json.dumps(command)}{more}"
            f"{check} }}\n{project}\n{lane_table}")

    def out(self, event: dict) -> dict:
        return json.loads(Path(event["output"]).read_text().strip().splitlines()[-1])


@pytest.fixture
def lanes(write_config, tmp_path, fenced):
    return Lanes(write_config, tmp_path)


def free_later(held, after_s: float = 2.0) -> None:
    threading.Timer(after_s, held.stdin.close).start()


# -- where a run goes --------------------------------------------------------------------

def test_with_a_place_free_here_the_check_runs_here_and_asks_no_lane(build, lanes):
    path, _tree, _made = build
    lanes.configure()

    done = checking.check(ROUND, "B1", by="coder")

    [event] = ran(path)
    assert done.passed and done.lane is None
    assert event["check_lock"] == 1 and "lane" not in event
    assert lanes.lane.relayed() == []


def test_with_this_machine_busy_the_check_goes_to_a_free_lane(build, lanes, holder, repo):
    path, tree, _made = build
    lanes.configure(lane="command = " + json.dumps(
        shlex.join([*lanes.words, "lane", "{root}", "{tip}"])))
    held = holder(checking.lock_file())
    said: list[str] = []

    done = checking.check(ROUND, "B1", by="coder", say=said.append)
    held.stdin.close()

    [event] = ran(path)
    assert done.passed and (done.lane, done.lane_host) == ("far", serving.host_name())
    assert (event["lane"], event["lane_host"], event["lane_check_lock"]) == (
        "far", serving.host_name(), 1)
    assert "check_lock" not in event
    # The record holds the lane's words filled with this machine's root; the far side
    # ran them with its own tree's.
    assert event["argv"] == [*lanes.words, "lane", str(repo.root), tree.rev()]
    found = lanes.out(event)
    assert found["words"][:2] == ["lane", found["cwd"]]
    assert Path(found["cwd"]).parent == lanes.lane.trees
    assert Path(event["output"]).name == "check-1.log"
    assert len(lanes.lane.relayed()) == 1


def test_a_run_sent_to_a_lane_holds_no_place_here(build, lanes):
    path, _tree, _made = build
    lanes.configure()

    done = checking.check(ROUND, "B1", by="coder", lane="far")

    found = lanes.out(ran(path)[0])
    assert done.lane == "far"
    assert found["here_free"] and found["tickets"] == 0
    assert check_line.holder(checking.lock_file()) is None


def test_a_busy_lane_is_passed_over_and_the_check_waits_here(build, lanes, holder):
    path, _tree, _made = build
    lanes.configure()
    held = holder(checking.lock_file())
    holder(lanes.lane.lock)
    said: list[str] = []
    free_later(held)

    done = checking.check(ROUND, "B1", by="coder", say=said.append)

    [event] = ran(path)
    assert done.passed and done.lane is None and event["check_lock"] == 1
    assert any(line.startswith("lane far (far-test) is busy") for line in said)
    assert any(line.startswith("waiting for the machine's check lock") for line in said)


def test_a_lane_that_can_t_be_reached_is_passed_over_with_a_line(build, lanes, holder):
    path, _tree, _made = build
    lanes.configure(ssh=UNREACHABLE)
    held = holder(checking.lock_file())
    said: list[str] = []
    free_later(held)

    done = checking.check(ROUND, "B1", by="coder", say=said.append)

    assert done.passed and done.lane is None
    assert any("ssh could not reach far-test" in line and "no route to host" in line
               for line in said)


def test_with_no_wait_and_nothing_free_the_check_is_refused_with_nothing_sent(
        build, lanes, holder):
    path, _tree, made = build
    lanes.configure()
    held = holder(checking.lock_file())
    holder(lanes.lane.lock)

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder", wait=False)
    held.stdin.close()

    [unmet] = caught.value.unmet
    assert unmet.reason.startswith("another check holds or waits for the machine's check")
    assert "lane far (far-test) is busy" in unmet.reason
    assert "leave --no-wait out" in unmet.fix
    assert ran(path) == []
    [relayed] = lanes.lane.relayed()
    assert relayed["bytes"] < 4096


def test_a_held_shared_lock_makes_the_lane_busy_for_a_plain_check(
        build, lanes, holder, tmp_path):
    path, _tree, _made = build
    shared = tmp_path / "scheduled.lock"
    lanes.configure(machine=f"shared_lock = {json.dumps(str(shared))}")
    held = holder(checking.lock_file())
    holder(shared)

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder", wait=False)
    held.stdin.close()

    assert "lane far (far-test) is busy" in caught.value.unmet[0].reason
    assert ran(path) == []


def test_urgent_keeps_the_check_here_and_asks_no_lane(build, lanes, holder):
    path, _tree, _made = build
    lanes.configure()
    held = holder(checking.lock_file())
    free_later(held)

    done = checking.check(ROUND, "B1", by="coder", urgent=True)

    assert done.passed and done.lane is None and ran(path)[0]["check_lock"] == 1
    assert lanes.lane.relayed() == []


def test_lane_sends_the_check_there_waiting_in_its_line(build, lanes, holder):
    path, _tree, _made = build
    lanes.configure()
    far = holder(lanes.lane.lock)
    said: list[str] = []
    free_later(far)

    done = checking.check(ROUND, "B1", by="coder", lane="far", say=said.append)

    assert done.passed and ran(path)[0]["lane"] == "far"
    assert any(line.startswith("lane far: waiting for the machine's check lock")
               for line in said)


def test_lane_with_no_wait_is_refused_while_that_lane_is_busy(build, lanes, holder):
    path, _tree, _made = build
    lanes.configure()
    holder(lanes.lane.lock)

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder", lane="far", wait=False)

    assert caught.value.unmet[0].reason.startswith("lane far (far-test) is busy")
    assert ran(path) == []


def test_lane_is_refused_when_the_lane_can_t_be_reached(build, lanes):
    path, _tree, _made = build
    lanes.configure(ssh=UNREACHABLE)

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder", lane="far")

    [unmet] = caught.value.unmet
    assert "ssh could not reach far-test" in unmet.reason and "ssh far-test" in unmet.fix
    assert ran(path) == []


def test_lane_is_refused_when_that_lane_is_not_ready(build, lanes):
    path, _tree, _made = build
    lanes.configure(lane=f"preflight = [{json.dumps(shlex.join([PY, '-c', 'raise SystemExit(3)']))}]")

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder", lane="far")

    assert "is not ready" in caught.value.unmet[0].reason
    assert ran(path) == [] and lanes.lane.tree_dirs() == []


GONE = """
import json, sys
out = sys.stdout.buffer
line = sys.stdin.buffer.readline()
sys.stdin.buffer.read(int(line.split()[1]))
body = json.dumps({"check_lock": 1}).encode()
out.write(b"placed %d\\n" % len(body) + body)
out.flush()
"""


def test_a_lane_lost_after_its_place_records_nothing_and_never_falls_back(
        build, lanes, holder):
    path, _tree, _made = build
    lanes.configure(ssh=[PY, "-c", GONE])
    held = holder(checking.lock_file())

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder")
    held.stdin.close()

    [unmet] = caught.value.unmet
    assert "was lost mid-run" in unmet.reason and "it may have run there" in unmet.fix
    assert ran(path) == []


def test_lane_naming_a_lane_the_project_does_not_send_to_is_refused(build, lanes):
    path, _tree, _made = build
    lanes.configure(named=False)

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder", lane="far")

    assert caught.value.unmet[0].reason == (
        "proj-a sends no check to a lane far: its lanes are none")
    assert lanes.lane.relayed() == []


def test_urgent_with_lane_and_a_step_with_lane_are_refused(build, lanes):
    lanes.configure()
    with pytest.raises(BadArgument, match="--urgent keeps the check on this machine"):
        checking.check(ROUND, "B1", by="coder", urgent=True, lane="far")
    with pytest.raises(BadArgument, match="a command step of your own runs here"):
        checking.check(ROUND, "B1", by="coder", step="staging", lane="far")
    assert main(["check", ROUND, "B1", "--urgent", "--lane", "far"]) != 0


# -- which checks may go -----------------------------------------------------------------

def test_a_check_by_ci_runs_here_and_lane_refuses_it(build, lanes, holder):
    path, _tree, _made = build
    lanes.configure(project='ci = { workflows = ["a.yml"], local = "none" }')

    with pytest.raises(Refused) as caught:
        checking.check(ROUND, "B1", by="coder", lane="far")

    [unmet] = caught.value.unmet
    assert unmet.reason == ("a check by CI runs here: its local part reads this "
                            "machine's CI state; --full can go to a lane")
    assert unmet.fix.endswith("--full --lane far")
    assert lanes.lane.relayed() == []


def test_full_in_a_project_with_ci_may_go_to_a_lane(build, lanes):
    path, _tree, _made = build
    lanes.configure(project='ci = { workflows = ["a.yml"], local = "none" }')

    done = checking.check(ROUND, "B1", by="coder", full=True, lane="far")

    assert done.passed and ran(path)[0]["lane"] == "far"


def test_a_queue_project_s_scoped_check_goes_with_its_base_filled(build, lanes, repo):
    path, _tree, _made = build
    lanes.configure(project='land = "queue"', scoped=True)

    done = checking.check(ROUND, "B1", by="coder", lane="far")

    event = ran(path)[0]
    assert done.passed and event["lane"] == "far"
    assert event["argv"] == [*lanes.words, "scoped", repo.rev("main")]
    assert lanes.out(event)["words"] == ["scoped", repo.rev("main")]


# -- the lane's words --------------------------------------------------------------------

def test_the_lane_s_memory_is_its_table_s_else_the_check_s_else_kept(write_config):
    def words(lane: str, check: str = "") -> list[str]:
        write_config('[lanes.far]\nhost = "h"\n[projects.proj-a]\ncheck = { command = '
                     f'"run {{memory_mib}}"{check} }}\n[projects.proj-a.lanes.far]\n{lane}\n')
        return checking.lane_words(own.load(), "proj-a", "far", "command")

    assert words("memory_mib = 512", ", memory_mib = 4096") == ["run", "512"]
    assert words("", ", memory_mib = 4096") == ["run", "4096"]
    assert words("") == ["run", "{memory_mib}"]
    assert words('command = "lane {memory_mib}"\nmemory_mib = 256') == ["lane", "256"]


def test_the_lane_s_time_limit_ends_its_run(build, lanes):
    path, _tree, _made = build
    lanes.words = [PY, "-c", "import time; time.sleep(60)", str(checking.lock_file())]
    lanes.configure(lane="timeout_s = 1", check=", timeout_s = 600")

    done = checking.check(ROUND, "B1", by="coder", lane="far")

    assert done.exit_code is None and ran(path)[0]["exit_code"] is None


# -- the gate and its readers ------------------------------------------------------------

def seen(path: Path, repo) -> object:
    return checking.seen_of(folded(path), ROUND, settings=own.load())["B1"]


def test_a_lane_pass_meets_the_gate_until_its_words_change(build, lanes, repo):
    path, tree, _made = build
    lanes.configure(lane=f"command = {json.dumps(shlex.join([*lanes.words, 'lane']))}")
    checking.check(ROUND, "B1", by="coder", lane="far")
    state = folded(path)

    found = seen(path, repo)
    assert checking.passes(state, "B1", "check", tip=tree.rev(), argv=found.check)

    lanes.configure(lane=f"command = {json.dumps(shlex.join([*lanes.words, 'lane', '-v']))}")
    found = seen(path, repo)
    why = checking.why_not(state, "B1", "check", tip=tree.rev(), argv=found.check)
    assert why is not None and "on lane far" in why and "the command on lane far is now" in why


def test_a_lane_pass_stops_counting_once_the_project_no_longer_names_the_lane(
        build, lanes, repo):
    path, tree, _made = build
    lanes.configure()
    checking.check(ROUND, "B1", by="coder", lane="far")
    lanes.configure(named=False)

    found = seen(path, repo)

    assert found.check == ()
    assert checking.why_not(folded(path), "B1", "check", tip=tree.rev(),
                            argv=found.check) == (
        f"the newest run of check for B1 passed at {tree.rev()[:12]} on lane far, which "
        "the project no longer sends checks to")


def test_a_run_here_then_on_a_lane_are_each_compared_with_their_own_words(
        build, lanes, repo):
    path, tree, _made = build
    lanes.configure(lane=f"command = {json.dumps(shlex.join([*lanes.words, 'lane']))}")
    checking.check(ROUND, "B1", by="coder")
    assert seen(path, repo).check == (*lanes.words, "here", str(repo.root))
    assert checking.passes(folded(path), "B1", "check", tip=tree.rev(),
                           argv=seen(path, repo).check)

    checking.check(ROUND, "B1", by="coder", lane="far")
    assert seen(path, repo).check == (*lanes.words, "lane")
    assert checking.passes(folded(path), "B1", "check", tip=tree.rev(),
                           argv=seen(path, repo).check)


def test_show_s_and_the_board_s_check_lines_name_the_lane(build, lanes):
    path, _tree, _made = build
    lanes.configure()
    checking.check(ROUND, "B1", by="coder", lane="far")
    state = folded(path)
    build_now = builds.current(state, "B1")

    run = showing.check_run(state, build_now)
    assert run["lane"] == "far"
    assert showing.check_line(run).endswith(" on lane far")
    part = types.SimpleNamespace(state=state)
    assert [one["lane"] for one in board._runs(part, "check", "B1")] == ["far"]


def test_the_verb_says_the_lane_the_check_ran_on(build, lanes, capsys):
    lanes.configure()

    assert main(["check", ROUND, "B1", "--lane", "far"]) == 0

    said = capsys.readouterr().out
    assert f" on lane far ({serving.host_name()}): passed" in said.splitlines()[0]
