"""The doctor's lane row, `lane <name>`, with no ssh: the lane's `ssh` words are a
stand-in that drops ssh's options and host and relays the session to a real `moxi
rounds serve --probe` with a state and cache folder of its own, as another machine
would have. The stand-in can spoil the session the way a bad ssh would: change a byte
of what it passes, or never pass on stdin's end. And the check lock row's holder and
waiter name the machine a run came from."""

from __future__ import annotations

import json
import os
import shlex
import sys
import textwrap
from pathlib import Path

import pytest
from moxi.doctor import Row

from moxi_rounds import check_line, checking, doctor, frames, paths, serving

PY = sys.executable

#: The far side: `serve --probe` when the session's far word asks for it, with the
#: probe's wait for stdin's end cut short when the mode is "noeof".
FAR = textwrap.dedent("""
    import sys
    from moxi_rounds import cli, serving
    mode, far_word = sys.argv[1:3]
    if mode == "noeof":
        serving.PROBE_EOF_WAIT_S = 1.0
    probe = ["--probe"] if far_word.endswith(" --probe") else []
    sys.exit(cli.main(["serve", *probe]))
    """)

#: The stand-in for ssh: its own arguments are the far home, the mode and the record
#: file; the rest are the session's words. It reads the whole of stdin, changes its last
#: byte when the mode is "flip", passes it to the far side, and passes on stdin's end
#: unless the mode is "noeof"; with "nogit" the far side has no git on its PATH. Then it
#: notes the words it was given.
RELAY = textwrap.dedent("""
    import json, os, subprocess, sys
    far_home, mode, record = sys.argv[1:4]
    if sys.platform == "win32":
        import msvcrt
        msvcrt.setmode(0, os.O_BINARY)
    env = {key: value for key, value in os.environ.items() if key not in LOCATORS}
    env[HOME] = far_home
    if mode == "nogit":
        empty = os.path.join(far_home, "empty-bin")
        os.makedirs(empty, exist_ok=True)
        env["PATH"] = empty
    child = subprocess.Popen([sys.executable, "-c", FAR, mode, sys.argv[-1]],
                             stdin=subprocess.PIPE, env=env)
    data = bytearray()
    while True:
        chunk = os.read(0, 65536)
        if not chunk:
            break
        data += chunk
    if mode == "flip" and data:
        data[-1] ^= 0xFF
    child.stdin.write(bytes(data))
    child.stdin.flush()
    if mode != "noeof":
        child.stdin.close()
    code = child.wait()
    with open(record, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"words": sys.argv[4:]}) + "\\n")
    sys.exit(code)
    """).replace("LOCATORS", repr(tuple(paths.LOCATOR_ENVS))).replace(
    "HOME]", f"{paths.HOME_ENV!r}]").replace("FAR,", f"{FAR!r},")


def stand_in(code: int, said: str) -> list[str]:
    """An ssh that writes `said` to stderr and exits `code`."""
    return [PY, "-c", f"import sys; sys.stderr.write({said!r} + '\\n'); sys.exit({code})"]


class Far:
    """The lane `far`, through the stand-in, its far side's folders under `home`."""

    def __init__(self, write_config, tmp_path: Path):
        self.write_config = write_config
        self.home = tmp_path / "far"
        self.relay_log = tmp_path / "relay.jsonl"
        env = {key: value for key, value in os.environ.items()
               if key not in paths.LOCATOR_ENVS}
        env[paths.HOME_ENV] = str(self.home)
        self.lock = checking.lock_file(env)

    def ssh(self, mode: str = "plain") -> list[str]:
        return [PY, "-c", RELAY, str(self.home), mode, str(self.relay_log)]

    def configure(self, *, ssh: list[str] | None = None, mode: str = "plain",
                  preflight: list[str] | None = None, moxi: str = "") -> None:
        lane_table = "" if preflight is None else (
            f"\n[projects.proj-a.lanes.far]\npreflight = {json.dumps(preflight)}\n")
        moxi_line = f"moxi = {json.dumps(moxi)}\n" if moxi else ""
        self.write_config(
            f"[lanes.far]\nhost = \"far-test\"\n{moxi_line}"
            f"ssh = {json.dumps(ssh or self.ssh(mode))}\n"
            f"[projects.proj-a]\ncheck = {{ command = \"run the check\" }}\n{lane_table}")

    def relayed(self) -> list[dict]:
        if not self.relay_log.exists():
            return []
        return [json.loads(line) for line in self.relay_log.read_text().splitlines()]


@pytest.fixture
def far(write_config, tmp_path, fenced):
    return Far(write_config, tmp_path)


def lane_row() -> Row:
    [row] = list(doctor._lanes())
    assert isinstance(row, Row) and (row.module, row.name) == ("rounds", "lane far")
    return row


def line(*words: str) -> str:
    return shlex.join([PY, "-c", *words])


# -- ok ----------------------------------------------------------------------------------

def test_a_lane_that_answers_the_probe_is_ok_and_says_what_the_far_side_has(far):
    far.configure(preflight=[line("print('ready')")])

    row = lane_row()

    assert row.state == "ok", row
    assert row.detail.startswith(f"{serving.host_name()}: version {frames.VERSION}, "
                                 f"{os.cpu_count()} CPUs, ")
    assert "git version" in row.detail
    assert "its check lock: free, 0 waiting" in row.detail
    assert row.detail.endswith("the probe's 256 bytes and their end came through; the "
                               "preflight of proj-a passes there")
    [relayed] = far.relayed()
    assert relayed["words"][-2:] == ["far-test", "moxi rounds serve --probe"]
    assert "ControlMaster=no" in relayed["words"] and "BatchMode=yes" in relayed["words"]


def test_no_lane_is_no_row(write_config, fenced):
    write_config("[projects.proj-a]\ncheck = { command = \"run the check\" }\n")

    assert list(doctor._lanes()) == []


def test_the_lane_row_is_among_the_doctors_rows(far, add_project):
    add_project("proj-a")
    far.configure()

    names = [row.name for row in doctor.rows()]

    assert names.index("lane far") == names.index("check lock") + 1


def test_the_far_check_locks_holder_is_named_with_the_machine_it_came_from(far):
    far.configure()
    check_line.write_holder(far.lock, {**check_line.me(), "round": "proj-a/x",
                                       "stage": "B1", "step": "check", "repo": "proj-a",
                                       "from_host": "near-box", "output": "x.log"})

    row = lane_row()

    assert row.state == "ok", row
    assert ("its check lock: held (proj-a/x B1, repo proj-a, from near-box) for 0 min, "
            "0 waiting") in row.detail


# -- each warn -----------------------------------------------------------------------------

def test_no_ssh_client_here_is_a_warn_naming_it(far):
    far.configure(ssh=["no-such-ssh-client-anywhere"])

    row = lane_row()

    assert row.state == "warn"
    assert row.detail.startswith("no ssh client on PATH: 'no-such-ssh-client-anywhere'")
    assert row.fix == "install OpenSSH's client, or set ssh in [lanes.far]"


def test_a_host_not_reached_is_a_warn_with_ssh_s_words(far):
    far.configure(ssh=stand_in(255, "ssh: connect to host far-test port 22: timed out"))

    row = lane_row()

    assert row.state == "warn"
    assert row.detail == ("lane far: ssh could not reach far-test: ssh: connect to host "
                          "far-test port 22: timed out")
    assert "works here with no prompt" in row.fix


def test_serve_missing_there_is_a_warn_with_the_fix(far):
    far.configure(ssh=stand_in(127, "bash: moxi: command not found"))

    row = lane_row()

    assert row.state == "warn"
    assert row.detail.startswith("lane far: moxi rounds serve is missing on far-test")
    assert row.fix == serving.fix_line("far-test")


def test_serve_failing_to_start_there_gives_its_last_stderr_lines(far):
    far.configure(ssh=stand_in(1, "ImportError: cannot import name 'frames'"))

    row = lane_row()

    assert row.state == "warn"
    assert row.detail.startswith("lane far: moxi rounds serve failed to start on far-test")
    assert "ImportError: cannot import name 'frames'" in row.detail
    assert row.fix == serving.fix_line("far-test")


def test_serve_of_another_version_there_is_a_warn_naming_both(far, monkeypatch):
    far.configure()
    monkeypatch.setattr(frames, "VERSION", frames.VERSION + 1)

    row = lane_row()

    assert row.state == "warn"
    assert row.detail.startswith("lane far: far-test refused the probe: ")
    assert str(frames.VERSION) in row.detail and str(frames.VERSION - 1) in row.detail
    assert row.fix == serving.fix_line(serving.host_name())


def test_a_session_that_changes_a_byte_is_a_warn(far):
    far.configure(mode="flip")

    row = lane_row()

    assert row.state == "warn"
    assert row.detail.startswith("the ssh session here does not carry binary stdin or its "
                                 "end: the 256 bytes the far side read differ from those "
                                 "sent; ")


def test_a_session_that_does_not_pass_on_stdin_s_end_is_a_warn(far):
    far.configure(mode="noeof")

    row = lane_row()

    assert row.state == "warn"
    assert row.detail.startswith("the ssh session here does not carry binary stdin or its "
                                 "end: the far side did not see stdin's end after the "
                                 "payload; ")


def test_no_git_there_is_a_warn(far):
    far.configure(mode="nogit")

    row = lane_row()

    assert row.state == "warn"
    assert row.detail.startswith(f"no git on {serving.host_name()}, which a lane's run "
                                 "needs there; ")


def test_a_failing_preflight_line_is_a_warn_naming_the_project_and_line(far):
    failing = line("print('node is missing'); raise SystemExit(3)")
    far.configure(preflight=[line("print('fine')"), failing])

    row = lane_row()

    assert row.state == "warn"
    assert row.detail.startswith(f"project proj-a's preflight fails on "
                                 f"{serving.host_name()}: the preflight line {failing!r} "
                                 "exited 3; its output ended: node is missing")
    assert row.fix.endswith("[projects.proj-a.lanes.far]'s preflight")


# -- the check lock row here ---------------------------------------------------------------

def test_the_check_lock_row_names_the_machine_a_holder_and_waiter_came_from(fenced,
                                                                            tmp_path):
    output = tmp_path / "check-1.log"
    output.write_text("running\n", encoding="utf-8")
    check_line.write_holder(checking.lock_file(), {
        **check_line.me(), "round": "proj-a/x", "stage": "B1", "step": "check",
        "repo": "proj-a", "from_host": "near-box", "output": str(output)})
    check_line.take(checking.lock_file(), {
        **check_line.me(), "round": "proj-a/y", "stage": "B2", "step": "check",
        "repo": "proj-a", "from_host": "other-box"})

    row = doctor._check_lock()

    pid = os.getpid()
    assert row.detail.startswith(f"held by pid {pid} (proj-a/x B1, repo proj-a, from "
                                 "near-box) for 0 min; ")
    assert row.detail.endswith(f"1 waiting: 1. pid {pid} proj-a/y B2 from other-box for "
                               "0 min")
