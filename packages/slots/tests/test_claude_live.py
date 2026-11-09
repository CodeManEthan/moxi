"""The screens against a real Claude Code, in a slot on a private server. Live tier:
deselected by default, and run only with MOXI_TEST_LIVE=1 and `claude` on PATH.

- The folder trust warning: Claude Code runs with a config directory of its own in the
  test's tmp (onboarding marked done, no login), in a folder it has never seen. The
  screen must fully match, then be answered with Down and Enter and leave.
- The development channels warning shows only to a logged-in Claude Code, so this test
  runs it with your own config, in safe mode (no hooks, plugins or MCP servers), in the
  folder MOXI_TEST_LIVE_CLAUDE_CWD names, which your Claude Code must already trust. It
  is skipped without that variable. Answering it starts a session that is given no
  prompt and is killed with the server.
- The exit dialog needs background work, which only a turn starts, so this test runs
  Claude Code the same way (your own config, safe mode, that folder, skipped without
  it) with a prompt on its command line: one short turn on the small model that starts
  `sleep 600` as a background shell. Then it types `/exit`, reads the dialog and
  answers it, which stops the shell and ends Claude Code.

Each screen's capture (plain and with colour) is written to the test's tmp, under
`captures/`, and its path printed (`pytest -s` shows it), to be added as a fixture once
it is cropped as `test_screens.py` says.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from moxi_slots import launch, slots, tmux
from moxi_slots.answer import answer_screen, read_screen
from moxi_slots.screens import (CLAUDE_DEV_CHANNELS, CLAUDE_EXIT_DIALOG, CLAUDE_TRUST_FOLDER,
                                SCREENS, plain)

pytestmark = [pytest.mark.live,
              pytest.mark.touches("packages/slots/src/moxi_slots/answer.py",
                                  "packages/slots/src/moxi_slots/screens*")]

# Read when the file is collected, before the fence empties the environment.
REAL = {k: os.environ[k] for k in ("HOME", "USER", "LOGNAME", "CLAUDE_CONFIG_DIR",
                                   "MOXI_TEST_LIVE_CLAUDE_CWD")
        if k in os.environ}
CLAUDE = shutil.which("claude")


@pytest.fixture
def claude_slot(server):
    if CLAUDE is None:
        pytest.skip("claude is not on PATH")
    tmux.call("set-option", "-g", "default-size", "120x40")
    slots.create([1])
    return server


def run(argv, env, cwd) -> str:
    placed = launch.start(1, [CLAUDE, *argv], env={"PATH": os.environ["PATH"],
                                                   "LANG": "C.UTF-8", **env},
                          cwd=str(cwd), label="claude")
    assert placed.exec == "started", placed
    return placed.pane_id


def until_shown(screen, bound: float = 45.0):
    deadline = time.monotonic() + bound
    seen = None
    while time.monotonic() < deadline:
        seen = read_screen(1, SCREENS)
        if seen.screen is not None and seen.full:
            assert seen.screen is screen, f"{seen.screen.name} showed, not {screen.name}"
            return seen
        assert not seen.dead, f"claude ended: {seen.rows}"
        time.sleep(0.25)
    raise AssertionError(f"{screen.name} did not show in {bound:g} s: {seen}")


def keep(pane: str, stem: str, tmp_path: Path) -> None:
    out = tmp_path / "captures"
    out.mkdir(exist_ok=True)
    size = tmux.call("display-message", "-p", "-t", pane,
                     "#{pane_width}x#{pane_height}").out.strip()
    for flags, kind in ((("-p",), "plain"), (("-p", "-e"), "ansi")):
        text = tmux.call("capture-pane", *flags, "-t", pane).out
        path = out / f"{stem}-{size}.{kind}.txt"
        path.write_text(text, encoding="utf-8")
        print(f"captured {path}")


def test_the_trust_warning_on_a_fresh_config(claude_slot, tmp_path):
    config = tmp_path / "claude-config"
    config.mkdir()
    (config / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True,
                                                     "theme": "dark"}))
    work = tmp_path / "work"
    work.mkdir()
    pane = run([], {"HOME": os.environ["HOME"], "CLAUDE_CONFIG_DIR": str(config)}, work)
    seen = until_shown(CLAUDE_TRUST_FOLDER)
    keep(pane, "claude-trust", tmp_path)
    marked = [r for r in seen.rows if r.lstrip().startswith("❯")]
    assert marked and "No, exit" in marked[0]
    done = answer_screen(1, CLAUDE_TRUST_FOLDER, wait_s=10)
    assert (done.outcome, done.why, done.keys) == ("gone", "", ("Down", "Enter"))


def own_config(why: str) -> tuple[str, dict[str, str]]:
    """The trusted folder and the environment that reach your own Claude Code config."""
    cwd = REAL.get("MOXI_TEST_LIVE_CLAUDE_CWD")
    if not cwd or "HOME" not in REAL:
        pytest.skip(f"set MOXI_TEST_LIVE_CLAUDE_CWD to a folder your Claude Code trusts: "
                    f"{why}, so this test uses your own config")
    env = {k: REAL[k] for k in ("HOME", "USER", "LOGNAME", "CLAUDE_CONFIG_DIR") if k in REAL}
    return cwd, env


def test_the_dev_channels_warning(claude_slot, tmp_path):
    cwd, env = own_config("the development channels warning shows only when logged in")
    pane = run(["--safe-mode", "--dangerously-load-development-channels", "server:moxi-live"],
               env, cwd)
    until_shown(CLAUDE_DEV_CHANNELS)
    keep(pane, "claude-dev-channels", tmp_path)
    done = answer_screen(1, CLAUDE_DEV_CHANNELS, wait_s=10)
    assert (done.outcome, done.why, done.keys) == ("gone", "", ("Enter",))


BACKGROUND = ("Run the shell command sleep 600 with the Bash tool in the background "
              "(run_in_background: true), then reply with the single word: started.")


def turn_done(pane: str) -> bool:
    """The turn has replied `started` and nothing is working any more."""
    text = plain(tmux.call("capture-pane", "-p", "-t", pane).out)
    replied = any(row.startswith("●") and "started" in row for row in text.split("\n"))
    return replied and "esc to interrupt" not in text


def test_the_exit_dialog_with_a_background_shell(claude_slot, tmp_path):
    cwd, env = own_config("only a turn starts background work, and a turn needs a login")
    # The prompt goes first: --allowedTools takes every word after it as another tool.
    pane = run([BACKGROUND, "--safe-mode", "--model", "haiku",
                "--allowedTools", "Bash(sleep 600)"], env, cwd)
    deadline = time.monotonic() + 90
    while not turn_done(pane):
        if time.monotonic() > deadline:
            keep(pane, "claude-exit-turn", tmp_path)
            raise AssertionError("the turn did not end with the shell started in 90 s")
        assert not read_screen(1, SCREENS).dead, "claude ended before /exit"
        time.sleep(0.5)
    time.sleep(1.0)
    tmux.call("send-keys", "-t", pane, "-l", "/exit")
    time.sleep(0.5)
    tmux.call("send-keys", "-t", pane, "Enter")
    seen = until_shown(CLAUDE_EXIT_DIALOG, bound=30)
    keep(pane, "claude-exit-shell", tmp_path)
    assert any("sleep 600" in row for row in seen.rows), seen.rows
    done = answer_screen(1, CLAUDE_EXIT_DIALOG, wait_s=10)
    assert (done.outcome, done.why, done.keys) == ("ended", "", ("Enter",))
