"""Live launches of the real Claude Code and Codex: marked `live`, deselected by a plain
run, and run only with `MOXI_TEST_LIVE=1` (`pytest -m live`).

Each one starts a real session, signed in with the harness's own account, and costs a
few tokens. The launcher's state stays in the test's tmp; the harness runs with the real
home, so it finds its sign-in, and keeps its own record of the session there as it does of
any other. A launch without turns passes `--setting-sources project` on to Claude, so the
user's own hooks do not run in it; a launch with turns needs them, since turns' hooks are
how the session reports itself ready.

What each needs, and is skipped without: the harness on PATH; for the launches with
turns, a turns that offers the interface the launcher calls; for a Codex launch with turns
in this terminal or a slot, turns' Codex host; for a slot launch, slots installed and tmux
on PATH. A slot launch runs slots on a tmux server of its own: `MOXI_HOME` points into a
short temp dir for the test, slots' socket follows it, and the server is stopped after.
The default tmux server is never reached. The `--help` diff against the catalog is in
`test_catalog.py`.

Each brief asks the model to join two random words with a hyphen. The joined word is in
no brief, so seeing it in the session's output means the model read the brief.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from moxi import config, modules, paths
from moxi_dev.testing import short_dir, wait_for

from moxi_launcher import (api, catalog, compose, first, handoffs, presets, store,
                           targets)

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(sys.platform == "win32",
                                 reason="these live launches drive a POSIX terminal")]

#: How long a real session gets to answer.
ANSWER_S = 240
#: Claude's model for these launches: the smallest, since the work is one word.
CLAUDE_MODEL = "haiku"
#: The variables that point a harness's home away from the user's: the fence points them
#: into the test's tmp, and these launches need the real ones.
HOMES = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
         "XDG_STATE_HOME", "XDG_CACHE_HOME")
_ESCAPES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


@pytest.fixture(scope="session")
def real_home() -> str:
    """The user's home. A session-wide fixture runs outside the fence, so it reads the
    real environment."""
    return os.path.expanduser("~")


@pytest.fixture
def live_env(real_home) -> dict[str, str]:
    """The fenced environment with the real home: the harness finds its sign-in, and the
    launcher's own homes stay in the test's tmp."""
    env = dict(os.environ)
    for name in HOMES:
        env.pop(name, None)
    env["HOME"] = real_home
    return env


@pytest.fixture
def work(tmp_path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def needs(harness: str) -> None:
    if shutil.which(harness) is None:
        pytest.skip(f"{harness} is not on PATH")


def needs_turns() -> None:
    if first.turns_api(quiet=True) is None:
        pytest.skip("needs a turns that offers the interface the launcher calls")


def needs_host() -> None:
    if compose.host_script() is None:
        pytest.skip("needs turns' Codex host")


def needs_slots() -> None:
    if not modules.available("slots"):
        pytest.skip("needs slots")
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not on PATH")


@pytest.fixture
def own_slots(live_env, monkeypatch):
    """slots on a tmux server of its own, for one test; yields the server's socket.

    `MOXI_HOME` points into a short temp dir, in this process (where the launcher calls
    slots) and in the launch's environment, and every `MOXI_SLOTS_*` locator is dropped, so
    slots' socket follows `MOXI_HOME` there. `TMUX` and `TMUX_PANE` are dropped, and
    `TMUX_TMPDIR` points into the dir too. On an editable install slots' server is not
    started on demand, so the fixture starts `moxi-slots-server` as the supervisor would
    (tmux's absolute path recorded, `tmux_conf` /dev/null), as slots' own server fixture
    does, and after the test kills the server, its process group and its socket."""
    needs_slots()
    with short_dir("mxs-") as home:
        for name in [n for n in os.environ if n.startswith("MOXI_SLOTS_")] + [
                "TMUX", "TMUX_PANE"]:
            monkeypatch.delenv(name, raising=False)
            live_env.pop(name, None)
        for name, value in (("MOXI_HOME", str(home)), ("TMUX_TMPDIR", str(home))):
            monkeypatch.setenv(name, value)
            live_env[name] = value
        socket = paths.socket_path("slots", "tmux")
        assert socket.startswith(str(home)), socket
        tmux = shutil.which("tmux")
        config.set("slots", "tmux", tmux, by="launcher live test")
        config.set("slots", "tmux_conf", os.devnull, by="launcher live test")
        log = open(home / "server.log", "wb")
        server = subprocess.Popen([str(paths.script("moxi-slots-server"))],
                                  env=dict(os.environ), stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=subprocess.STDOUT,
                                  start_new_session=True)

        def answering() -> bool:
            return subprocess.run([tmux, "-S", socket, "display-message", "-p", "#{pid}"],
                                  capture_output=True, env=dict(os.environ),
                                  timeout=5).returncode == 0
        try:
            wait_for(answering, timeout=20, interval=0.2,
                     what=lambda: f"slots' own server; its log: "
                                  f"{(home / 'server.log').read_text(errors='replace')}")
            yield socket
        finally:
            subprocess.run([tmux, "-S", socket, "kill-server"], capture_output=True,
                           env=dict(os.environ), timeout=10)
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
            if os.path.exists(socket):
                os.remove(socket)
            log.close()


def words() -> tuple[str, str]:
    """A brief asking for two words joined, and the joined word it should bring back."""
    a, b = f"kiwi{secrets.token_hex(3)}", f"plum{secrets.token_hex(3)}"
    return (f"Reply with only the two words {a} and {b} joined by one hyphen, and "
            "nothing else."), f"{a}-{b}"


def flags(harness: str) -> dict[str, object]:
    return {"harness": harness, **({"model": CLAUDE_MODEL} if harness == "claude" else {})}


def launch_row(request_id: str | None = None) -> store.LaunchRow | None:
    """The launch `request_id`, or the newest launch."""
    if not store.path().exists():
        return None
    conn = store.connect(create=False)
    try:
        if request_id is not None:
            return store.get(conn, request_id)
        newest = store.log(conn, limit=1)
        return newest[0] if newest else None
    finally:
        conn.close()


def first_turn_events(request_id: str) -> list[store.Event]:
    conn = store.connect(create=False)
    try:
        return [e for e in store.events(conn, request_id) if e.kind == first.EVENT]
    finally:
        conn.close()


def plain(text: str) -> str:
    return _ESCAPES.sub("", text)


def run_log_holds(request_id: str, word: str) -> bool:
    log = targets.run_log(request_id)
    return log.exists() and word in log.read_text(errors="replace")


class Terminal:
    """`moxi launcher run ...` in a terminal of its own, a pseudo-terminal this test
    reads, as a person runs it."""

    def __init__(self, argv: list[str], env: dict[str, str], cwd: Path):
        import fcntl
        import pty
        import struct
        import termios

        self.out = bytearray()
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # the child: become the command
            try:
                os.chdir(cwd)
                os.execve(argv[0], argv, env)
            finally:
                os._exit(127)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            self.out += data

    def text(self) -> str:
        return plain(bytes(self.out).decode(errors="replace"))

    def press(self, keys: bytes) -> None:
        os.write(self.fd, keys)

    def close(self) -> None:
        try:
            os.killpg(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass
        os.close(self.fd)


def moxi_command() -> str:
    found = shutil.which("moxi", path=str(Path(sys.executable).parent))
    if found is None:
        pytest.skip("this environment has no moxi script")
    return found


def answer_trust_once(term: Terminal, answered: list[bool]) -> None:
    """Claude's folder-trust screen, pressed only once it shows."""
    if not answered and "trust" in term.text().lower():
        term.press(b"\r")
        answered.append(True)


def in_terminal(live_env, work, harness: str, brief: str, *more: str):
    """Launch through the CLI in a terminal of its own; returns the terminal and, once the
    row is written, the launch row."""
    argv = [moxi_command(), "launcher", "run", "--harness", harness, "--cwd", str(work),
            "--brief", brief, *more]
    if harness == "claude":
        argv += ["--model", CLAUDE_MODEL]
    else:
        argv += ["--trust-cwd"]
    term = Terminal(argv, live_env, work)
    row = wait_for(launch_row, timeout=60, what=lambda: f"the launch row; saw {term.text()}")
    return term, row


# -- without turns: the brief is the opening prompt -------------------------------------


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_launch_without_turns_has_the_brief_as_its_opening_prompt(
        harness, live_env, work, monkeypatch):
    needs(harness)
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: None)
    brief, answer = words()
    typed = {**flags(harness), "target": "headless", "brief": brief}
    extra: tuple[str, ...] = ()
    if harness == "claude":
        extra = ("--setting-sources", "project")
        typed["cwd"] = str(work)
    else:
        typed["scratch"] = True
    result = api.launch(api.LaunchRequest(typed=typed, extra=extra, cwd=str(work)),
                        environ=live_env)
    assert result.state == "started", result.line
    assert first.on_argv(result.argv) and any(brief in word for word in result.argv)
    wait_for(lambda: run_log_holds(result.request_id, answer), timeout=ANSWER_S,
             what=lambda: f"{answer} in {targets.run_log(result.request_id)}")
    # Nothing was owed after the start, and nothing was delivered.
    assert first_turn_events(result.request_id) == []


# -- with turns: one launch per target, the brief received as the first turn -------------


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_terminal_launch_with_turns_gets_its_brief_as_the_first_turn(
        harness, live_env, work):
    needs(harness)
    needs_turns()
    if harness == "codex":
        needs_host()
    brief, answer = words()
    term, row = in_terminal(live_env, work, harness, brief)
    answered: list[bool] = []
    try:
        assert not first.on_argv(row.argv), "with turns the brief is not on the argv"

        def sent():
            if harness == "claude":
                answer_trust_once(term, answered)
            current = launch_row(row.request_id)
            return current is not None and current.state in ("first-sent", "failed")
        wait_for(sent, timeout=ANSWER_S, interval=0.25,
                 what=lambda: f"the first turn; saw {term.text()[-2000:]}")
        current = launch_row(row.request_id)
        assert (current.state, current.reason) == ("first-sent", None), current.reason
        [event] = first_turn_events(row.request_id)
        assert event.detail["outcome"] == "sent"
        wait_for(lambda: answer in term.text(), timeout=ANSWER_S, interval=0.25,
                 what=lambda: f"{answer} on the screen; saw {term.text()[-2000:]}")
        if harness == "codex":
            assert current.session_id, "the Codex session id was learned"
    finally:
        term.close()


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_headless_launch_with_turns_runs_its_brief(harness, live_env, work):
    needs(harness)
    needs_turns()
    brief, answer = words()
    typed = {**flags(harness), "target": "headless", "brief": brief}
    typed.update({"cwd": str(work)} if harness == "claude" else {"scratch": True})
    result = api.launch(api.LaunchRequest(typed=typed, cwd=str(work)), environ=live_env)
    assert result.state == "started", result.line
    # Headless, the brief is the harness's prompt even with turns: nothing is delivered.
    assert any(brief in word for word in result.argv)
    wait_for(lambda: run_log_holds(result.request_id, answer), timeout=ANSWER_S,
             what=lambda: f"{answer} in {targets.run_log(result.request_id)}")
    assert first_turn_events(result.request_id) == []


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_slot_launch_with_turns_gets_its_brief_as_the_first_turn(harness, live_env,
                                                                     work, own_slots):
    needs(harness)
    needs_turns()
    if harness == "codex":
        needs_host()
    brief, answer = words()
    typed = {**flags(harness), "target": "slot", "brief": brief, "cwd": str(work)}
    if harness == "codex":
        typed["trust_cwd"] = True
    result = api.launch(api.LaunchRequest(typed=typed, cwd=str(work)), environ=live_env)
    row = launch_row(result.request_id)
    try:
        assert result.outcome is not None and result.outcome.outcome == "sent", result.line
        assert (row.state, row.reason) == ("first-sent", None)

        def screen() -> str:
            done = subprocess.run(["tmux", "-S", own_slots, "capture-pane", "-p", "-t",
                                   row.pane_id], capture_output=True, text=True,
                                  env=live_env)
            return done.stdout
        wait_for(lambda: answer in screen(), timeout=ANSWER_S, interval=0.5,
                 what=lambda: f"{answer} in pane {row.pane_id}; saw {screen()[-2000:]}")
    finally:
        if row is not None and row.pane_id:
            subprocess.run(["tmux", "-S", own_slots, "kill-pane", "-t", row.pane_id],
                           env=live_env, capture_output=True)


# -- a Codex seat: its instructions in start context ------------------------------------


def test_a_codex_seat_gets_its_instructions_in_start_context(live_env, work):
    needs("codex")
    needs_turns()
    needs_host()
    a, b = f"fig{secrets.token_hex(3)}", f"lime{secrets.token_hex(3)}"
    file = presets.path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        "[seat.liveseat]\n"
        'harness = "codex"\n'
        f'cwd = "{work}"\n'
        "trust_cwd = true\n"
        f'instructions = "The seat word is the two words {a} and {b} joined by one '
        'hyphen. When asked for the seat word, reply with it and nothing else."\n')
    argv = [moxi_command(), "launcher", "run", "--seat", "liveseat", "--brief",
            "What is the seat word?"]
    term = Terminal(argv, live_env, work)
    try:
        row = wait_for(launch_row, timeout=60, what=lambda: f"the row; saw {term.text()}")
        assert row.values["mode"] == "host"
        # The instructions are in no argv word: only the start listener gives them.
        assert not any(a in word for word in row.argv)
        wait_for(lambda: f"{a}-{b}" in term.text(), timeout=ANSWER_S, interval=0.25,
                 what=lambda: f"the seat word; saw {term.text()[-2000:]}")
    finally:
        term.close()


# -- claude -p with turns: its id recorded, no pointer -----------------------------------


def test_claude_print_with_turns_records_its_id_and_shows_no_pointer(live_env, work):
    needs("claude")
    needs_turns()
    file = presets.path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f'[seat.liveprint]\nharness = "claude"\ncwd = "{work}"\n')
    waiting = handoffs.own_folder() / handoffs.file_name("liveprint", "2026-10-30")
    waiting.write_text("A handoff a headless run must neither show nor use up.\n")
    brief, answer = words()
    result = api.launch(api.LaunchRequest(
        seat="liveprint", typed={"target": "headless", "brief": brief,
                                 "model": CLAUDE_MODEL}, cwd=str(work)),
        environ=live_env)
    assert result.state == "started", result.line
    wait_for(lambda: run_log_holds(result.request_id, answer), timeout=ANSWER_S,
             what=lambda: f"{answer} in {targets.run_log(result.request_id)}")
    row = wait_for(lambda: (r := launch_row(result.request_id)) is not None
                   and r.endpoint and r, timeout=60, what="the claim")
    conn = store.connect(create=False)
    try:
        ids = store.session_ids(conn, result.request_id)
        assert (result.session_id, "minted") in ids
        assert any(source != "minted" for _, source in ids), ids
        assert store.resolve(conn, result.session_id).request_id == result.request_id
        assert store.picked(conn, "liveprint") == []
    finally:
        conn.close()
    assert row.state in ("started", "ready")
    assert "Handoff waiting" not in targets.run_log(result.request_id).read_text()


def test_codex_trusts_the_launch_directory_from_the_trust_row_even_with_a_dot_in_it(
        tmp_path):
    """The trust row's words, as `codex` and `codex exec` launches carry them, trust the
    launch's directory in the real Codex, a dot in its path and all, and leave the
    user's own trusted projects trusted. A project's `.codex/config.toml` is read only in
    a trusted directory, so `codex features list` shows the feature it turns on. Codex
    runs with its home in the test's tmp, and no session starts."""
    needs("codex")
    home = tmp_path / "codex-home"
    home.mkdir()
    env = {**os.environ, "CODEX_HOME": str(home)}

    def features(where: Path, *words: str) -> dict[str, bool]:
        out = subprocess.run(["codex", "features", "list", *words], cwd=where, env=env,
                             capture_output=True, text=True, timeout=60, check=True)
        found = {}
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1] in ("true", "false"):
                found[parts[0]] = parts[-1] == "true"
        return found

    stages = {line.split()[0]: line for line in subprocess.run(
        ["codex", "features", "list"], cwd=tmp_path, env=env, capture_output=True,
        text=True, timeout=60, check=True).stdout.splitlines() if line.strip()}
    name = next(name for name, on in features(tmp_path).items()
                if not on and "removed" not in stages[name])
    projects = {}
    for folder in ("my.repo", "other"):
        projects[folder] = tmp_path / folder
        (projects[folder] / ".codex").mkdir(parents=True)
        (projects[folder] / ".codex" / "config.toml").write_text(
            f"[features]\n{name} = true\n", encoding="utf-8")
    (home / "config.toml").write_text(
        f'[projects.{catalog.toml_literal(str(projects["other"]))}]\n'
        'trust_level = "trusted"\n', encoding="utf-8")
    cwd = projects["my.repo"]
    assert features(cwd)[name] is False
    assert features(projects["other"])[name] is True
    row = catalog.load("codex")["trust_cwd"]
    for mode in ("interactive", "exec"):
        words = row.tokens(mode, True, cwd=str(cwd))
        assert features(cwd, *words)[name] is True, (mode, words)
        assert features(projects["other"], *words)[name] is True, (mode, words)
