"""A past slot session resumed from the launcher's page, in a browser, through a real shell
whose page server runs the resume on real slots: the resume button, the job it starts,
and the new launch's row arriving by push.

slots runs on a tmux server of its own. The test shell's `MOXI_HOME` is under its own
temporary home, slots' socket follows `MOXI_HOME`, and the server is started here on that
socket and stopped after, so the default tmux server, and any other, is never reached.
Skipped where slots is not installed or tmux 3.2 or later is not on `PATH`.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import signal
import subprocess
import sys

import pytest
from moxi import paths
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import launcher_page_kit as kit
from moxi_launcher import store

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(sys.platform == "win32", reason="slots needs tmux"),
              pytest.mark.skipif(importlib.util.find_spec("moxi_slots") is None,
                                 reason="slots is not installed"),
              pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not on PATH")]

#: The oldest tmux slots runs on.
TMUX_FLOOR = (3, 2)
#: What the shell's environment runs to set slots up and say where its socket is.
SETUP = """
import sys
from moxi import config, paths
config.set("slots", "tmux", sys.argv[1], by="launcher browser test")
config.set("slots", "tmux_conf", sys.argv[2], by="launcher browser test")
print(paths.socket_path("slots", "tmux"))
"""


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    return kit.Harness(tmp_path_factory.mktemp("fake-harness"))


@pytest.fixture(scope="module")
def ui_shell_options(harness):
    """The shell's options, given once the fake harness is first on `PATH`."""
    with kit.first_on_path(harness.bin):
        yield {}


@pytest.fixture(scope="module")
def own_slots(ui_shell):
    """slots' server on the shell's own socket, started as the supervisor would start it
    (tmux's absolute path recorded, `tmux_conf` /dev/null), and stopped after with its
    tmux server. Yields the socket."""
    found = shutil.which("tmux")
    said = subprocess.run([found, "-V"], capture_output=True, text=True, timeout=5).stdout
    version = re.search(r"(\d+)\.(\d+)", said)
    if version is None or tuple(map(int, version.groups())) < TMUX_FLOOR:
        pytest.skip(f"tmux on PATH is {said.strip()!r}, below 3.2")
    env = dict(ui_shell.env)
    tmux = os.path.abspath(found)
    socket = subprocess.run([sys.executable, "-c", SETUP, tmux, os.devnull], env=env,
                            capture_output=True, text=True, check=True,
                            timeout=30).stdout.strip()
    assert socket.startswith(env["MOXI_HOME"]) or "/moxi-" in socket, socket
    log_path = ui_shell.home / "slots-server.log"
    with open(log_path, "wb") as log:
        server = subprocess.Popen([str(paths.script("moxi-slots-server"))], env=env,
                                  stdin=subprocess.DEVNULL, stdout=log,
                                  stderr=subprocess.STDOUT, start_new_session=True)
    try:
        wait_for(lambda: subprocess.run([tmux, "-S", socket, "display-message", "-p",
                                         "#{pid}"], capture_output=True, env=env,
                                        timeout=5).returncode == 0,
                 20, interval=0.2,
                 what=lambda: f"slots' own server; its log: "
                              f"{log_path.read_text(errors='replace')}")
        yield socket
    finally:
        subprocess.run([tmux, "-S", socket, "kill-server"], capture_output=True, env=env,
                       timeout=10)
        if server.poll() is None:
            try:
                os.killpg(server.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait(timeout=10)


def test_a_past_slot_session_resumes_in_its_slot_and_the_new_row_arrives_by_push(
        ui_shell, own_slots, ui_page, harness, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    sid = kit.seed(ui_shell, "past-slot", target="slot", cwd=str(work))
    panel = kit.open_panel(ui_page)
    past = panel.locator('section.past li[data-request="past-slot"]')
    expect(past).to_have_attribute("data-resume", "slot")
    resume = past.locator('button[data-action="resume"]')
    expect(resume).to_have_text("Resume")
    resume.click()
    said = past.locator('[data-field="resumed"]')
    expect(said).to_contain_text("Resuming as launch")
    expect(said.locator('[data-follow="row"]')).to_be_visible(timeout=30_000)
    with kit.launch_log(ui_shell) as conn:
        [new] = [row for row in store.log(conn) if row.resume_of == "past-slot"]
    row = panel.locator(f'section.launches li[data-request="{new.request_id}"]')
    expect(row).to_have_count(1)
    assert (new.target, new.slot, new.session_id) == ("slot", "3", sid)
    [run] = wait_for(lambda: [run for run in harness.runs() if run["cwd"] == str(work)], 20,
                     what="the fake harness's run in the slot")
    assert "--resume" in run["argv"] and sid in run["argv"], run["argv"]
