"""The Codex host against synthetic app-server answers and notifications."""

from __future__ import annotations

import itertools
import os
import sys
import threading
import time

import pytest

from moxi_turns import codex_hooks
from moxi_turns import codex_protocol as proto
from moxi_turns import codex as codex_module
from moxi_turns import doctor
from moxi_turns import protocol
from moxi_turns.client import Session
from moxi_turns.codex import CodexHarness
from moxi_turns.endpoint import Endpoint
from moxi_turns.results import Ended, Lost, Refused, Started, StopFacts
from conftest import bare, end_endpoint
from fake_codex import LIMITS


class FakeAppServer:
    def __init__(self, socket_path, **kwargs):
        self.socket_path = socket_path
        self.alive = False
        self.returncode = None
        self.starts = 0

    def start(self):
        self.alive = True
        self.starts += 1
        return self

    def wait_for_socket(self):
        return True

    def stop(self):
        self.alive = False


class FakeClient:
    def __init__(self, harness, *, on_notification, on_close):
        self.harness = harness
        self.on_notification = on_notification
        self.on_close = on_close
        self.connected = False
        self.calls = []
        if not hasattr(harness, "_fake_turns"):
            harness._fake_turns = itertools.count(1)
        self.turns = harness._fake_turns
        if not hasattr(harness, "_fake_ran"):
            harness._fake_ran = []   # the thread's turns, oldest first, across clients
        self.ran = harness._fake_ran
        self.live_turn = None
        self.miss_steer = False
        self.absorbed = []          # turn/start texts taken into a running turn

    def connect(self):
        self.connected = True
        return self

    def initialize(self):
        return {}

    def close(self):
        self.connected = False

    def notify(self, method, params=None):
        pass

    def call(self, method, params=None, timeout=None):
        self.calls.append((method, params))
        if method == "hooks/list":
            return {"data": [{"hooks": [
                {"key": key, "enabled": True, "trustStatus": codex_hooks.TRUSTED}
                for key in self.harness.hook_keys
            ]}]}
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "thread/resume":
            return {"thread": {"id": "thread-1", "status": {"type": "idle"}}}
        if method == "thread/inject_items":
            assert params["items"], "Codex refuses an empty item list"
            return {}
        if method == "turn/start":
            if self.live_turn is not None:
                # Codex takes a turn/start's input into the running turn: it answers
                # with a phantom id that never starts, and sends no turn/started.
                self.absorbed.append(params["input"][0]["text"])
                return {"turn": {"id": f"phantom-{len(self.absorbed)}"}}
            turn_id = f"turn-{next(self.turns)}"
            self.live_turn = turn_id
            self.ran.append(turn_id)
            self.on_notification("turn/started", {"turn": {"id": turn_id}}, None)
            return {"turn": {"id": turn_id}}
        if method == "turn/steer":
            if self.miss_steer:
                self.miss_steer = False
                self.complete()
                raise proto.NoActiveTurn(-32600, "no active turn to steer")
            return {"turnId": self.live_turn}
        if method == "thread/backgroundTerminals/list":
            return {"data": []}
        if method == "account/rateLimits/read":
            error = getattr(self.harness, "fake_limits_error", None)
            if error is not None:
                raise error
            return dict(getattr(self.harness, "fake_limits", LIMITS))
        if method in ("mcpServerStatus/list", "config/mcpServer/reload"):
            # The MCP settle's calls, answered by a test's own app-server stand-in.
            return self.harness.fake_mcp(method, params, timeout)
        if method == "thread/read":
            # The thread's history: the collab items a test recorded on the harness.
            error = getattr(self.harness, "fake_read_error", None)
            if error is not None:
                raise error
            items = list(getattr(self.harness, "fake_history", ()))
            return {"thread": {"id": "thread-1", "turns": [
                {"id": "turn-0", "status": "completed", "items": items}]}}
        if method == "thread/turns/list":
            # Newest first, as Codex lists them by default. A test may set the live
            # turn by hand; it is listed too.
            ran = self.ran + ([self.live_turn] if self.live_turn is not None
                              and self.live_turn not in self.ran else [])
            data = [{"id": turn_id, "status": "inProgress" if turn_id == self.live_turn
                     else "completed"} for turn_id in reversed(ran)]
            return {"data": data}
        raise AssertionError(f"unexpected app-server call {method}")

    def complete(self, status="completed"):
        turn_id, self.live_turn = self.live_turn, None
        self.on_notification(
            "turn/completed", {"turn": {"id": turn_id, "status": status}}, None
        )

    def status(self, kind):
        self.on_notification("thread/status/changed",
                             {"threadId": "thread-1", "status": {"type": kind}}, None)


def _turn_texts(app):
    """The texts of the turns a client started."""
    return [params["input"][0]["text"] for method, params in app.calls
            if method == "turn/start"]


def _host(turns_env):
    harness = None

    def clients(**kwargs):
        client = FakeClient(harness, **kwargs)
        made.append(client)
        return client

    made = []
    harness = CodexHarness(
        turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
        client_factory=clients, app_server_factory=FakeAppServer,
    )
    endpoint = Endpoint(harness, session_id=None).open()
    harness.start()
    return endpoint, harness, made[0]


def test_a_submitted_delivery_is_tied_to_the_app_servers_turn(turns_env):
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        assert session.session_id == "thread-1"
        delivery = session.deliver("hello", only_if_idle=True)
        assert delivery.wait_started(2).turn_id == "turn-1"
        app.complete()
        ended = delivery.wait_ended(2)
        assert (ended.turn_id, ended.outcome) == ("turn-1", "answer")
    finally:
        end_endpoint(endpoint)


def test_a_steer_uses_turn_steer_and_ends_with_that_turn(turns_env):
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        first = session.deliver("first", only_if_idle=True)
        assert first.wait_started(2).turn_id == "turn-1"
        steered = session.steer("now")
        assert steered.wait_started(2).turn_id == "turn-1"
        assert any(method == "turn/steer" for method, _ in app.calls)
        app.complete("interrupted")
        assert steered.wait_ended(2).outcome == "aborted"
    finally:
        end_endpoint(endpoint)


def test_stopping_the_endpoint_reaps_the_owned_children_and_runtime_dir(turns_env):
    endpoint, harness, _ = _host(turns_env)
    directory = endpoint.dir
    end_endpoint(endpoint)
    assert not harness.app_server.alive
    assert not directory.exists()


def test_a_plain_steer_that_races_the_turn_end_refuses_idle(turns_env):
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        first = session.deliver("first", only_if_idle=True)
        assert first.wait_started(2).turn_id == "turn-1"
        app.miss_steer = True
        delivery = session.steer("too late")
        assert delivery.wait_started(2).reason == "idle"
        assert delivery.wait_ended(2).reason == "idle"
        assert _turn_texts(app) == ["first"]
    finally:
        end_endpoint(endpoint)


def test_supervision_reconnects_and_resumes_the_same_thread(turns_env):
    endpoint, harness, first = _host(turns_env)
    try:
        harness.app_server.alive = False
        first.connected = False
        deadline = time.monotonic() + 3
        while harness.ws is first and time.monotonic() < deadline:
            time.sleep(0.02)
        assert harness.ws is not first
        assert ("thread/resume", {"threadId": "thread-1", "excludeTurns": True,
                                  "approvalPolicy": "never"}) \
            in harness.ws.calls
        assert harness.app_server.starts == 2
        assert endpoint.session()["session_id"] == "thread-1"
    finally:
        end_endpoint(endpoint)


@pytest.mark.parametrize("key", sorted(codex_module.OWNED_OPTIONS))
def test_owned_options_are_refused_before_the_endpoint_opens(monkeypatch, turns_env, key):
    opened = []
    monkeypatch.setattr(Endpoint, "open", lambda self: opened.append(self))
    with pytest.raises(ValueError, match=key):
        codex_module.launch(turns_env, window=False, options={key: "caller value"})
    assert opened == []


def test_options_and_verifier_reach_start_and_every_resume(turns_env):
    answers = []
    harness = None
    made = []

    def clients(**kwargs):
        client = FakeClient(harness, **kwargs)
        made.append(client)
        return client

    options = {"model": "sample", "sandbox": "workspace-write"}
    harness = CodexHarness(
        turns_env, instructions="rules", options=options,
        verify=lambda answer: answers.append(answer) or None,
        window=False, ping=["/env/bin/moxi-turns-ping"], client_factory=clients,
        app_server_factory=FakeAppServer,
    )
    endpoint = Endpoint(harness).open()
    try:
        harness.hook_server.start()
        harness._connect()
        made[-1].close()
        harness._connect()
        harness.app_server.alive = False
        made[-1].close()
        harness._connect()
        starts = [params for method, params in made[0].calls if method == "thread/start"]
        resumes = [params for client in made[1:] for method, params in client.calls
                   if method == "thread/resume"]
        assert starts == [{**options, "cwd": str(turns_env), "approvalPolicy": "never",
                           "developerInstructions": "rules"}]
        assert resumes == [{**options, "threadId": "thread-1", "excludeTurns": True,
                            "approvalPolicy": "never",
                            "developerInstructions": "rules"}] * 2
        assert len(answers) == 3
    finally:
        end_endpoint(endpoint)


def test_unaccepted_resume_gates_input_and_publishes_its_reason(turns_env):
    resume_entered = threading.Event()
    accept_resume = threading.Event()
    calls = 0

    def verify(answer):
        nonlocal calls
        calls += 1
        if calls == 2:
            resume_entered.set()
            assert accept_resume.wait(2)
        return None

    endpoint, harness, first = _host(turns_env)
    harness.verify = verify
    # The helper's first load happened before the verifier was installed.
    calls = 1
    first.close()
    thread = threading.Thread(target=harness._connect)
    thread.start()
    assert resume_entered.wait(2)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    assert session.deliver("too early", only_if_idle=True).wait_started(1).reason == "loading"
    assert session.steer("too early").wait_started(1).reason == "loading"
    queued = session.deliver("after acceptance")
    try:
        with pytest.raises(TimeoutError):
            queued.wait_started(0.05)
        accept_resume.set()
        thread.join(2)
        assert queued.wait_started(2).turn_id == "turn-1"
        assert "trouble" not in endpoint.session()
    finally:
        accept_resume.set()
        end_endpoint(endpoint)


def _injects(app):
    """The `thread/inject_items` calls a client made."""
    return [params for method, params in app.calls if method == "thread/inject_items"]


def test_fresh_window_injects_before_releasing_a_gated_delivery(turns_env):
    harness = None
    made = []
    in_inject, release = threading.Event(), threading.Event()

    class HoldInject(FakeClient):
        def call(self, method, params=None, timeout=None):
            if method == "thread/inject_items":
                in_inject.set()
                assert release.wait(5)
            return super().call(method, params, timeout)

    def clients(**kwargs):
        client = HoldInject(harness, **kwargs)
        made.append(client)
        return client

    harness = CodexHarness(
        turns_env, window=True, ping=["/env/bin/moxi-turns-ping"],
        client_factory=clients, app_server_factory=FakeAppServer,
    )
    endpoint = Endpoint(harness).open()
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    queued = session.deliver("real work")
    loader = threading.Thread(target=harness._connect, daemon=True)
    try:
        harness.hook_server.start()
        loader.start()
        assert in_inject.wait(2)
        assert harness.loading() and not harness.generation.ready.is_set()
        with pytest.raises(TimeoutError):
            queued.wait_started(0.05)
        release.set()
        loader.join(2)
        assert harness.generation.ready.is_set()
        assert queued.wait_started(2).turn_id == "turn-1"
        assert _turn_texts(made[0]) == ["real work"]
    finally:
        release.set()
        end_endpoint(endpoint)


def test_a_headless_start_injects_one_item_and_sends_no_turn(turns_env):
    # A fresh thread has no rollout until something enters its history, and
    # `thread/resume` refuses it, so a thread launched without a window gets the item too.
    endpoint, harness, first = _host(turns_env)
    try:
        assert _injects(first) == [proto.inject_params("thread-1", codex_module.READY_ITEM)]
        assert _turn_texts(first) == []
        assert harness.active_turn_id is None and harness.generation.ready.is_set()
        assert not harness.loading()
        first.close()
        harness._connect()
        second = harness.ws
        assert _injects(second) == [] and _turn_texts(second) == []
    finally:
        end_endpoint(endpoint)


def test_the_injected_item_is_one_developer_message_with_text():
    params = proto.inject_params("thread-9", codex_module.READY_ITEM)
    assert params == {"threadId": "thread-9", "items": [{
        "type": "message", "role": "developer",
        "content": [{"type": "input_text", "text": codex_module.READY_ITEM}]}]}
    with pytest.raises(ValueError):
        proto.inject_params("thread-9", "")


def _old_codex(base):
    class OldCodex(base):
        def call(self, method, params=None, timeout=None):
            if method == "thread/inject_items":
                self.calls.append((method, params))
                raise proto.CodexError(proto.METHOD_NOT_FOUND,
                                       "method not found: thread/inject_items")
            return super().call(method, params, timeout)
    return OldCodex


def test_a_codex_without_inject_is_refused_with_no_turn_and_no_retry(turns_env):
    harness = None
    made = []

    def clients(**kwargs):
        client = _old_codex(FakeClient)(harness, **kwargs)
        made.append(client)
        return client

    harness = CodexHarness(
        turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
        client_factory=clients, app_server_factory=FakeAppServer,
        backoff_factory=lambda: itertools.repeat(0.001),
    )
    endpoint = Endpoint(harness, session_id=None).open()
    try:
        with pytest.raises(codex_module.CodexTooOld) as raised:
            harness.start()
        assert str(raised.value) == codex_module.INJECT_MISSING
        assert "Codex 0.153.0" in str(raised.value)
        assert harness.give_up_reason() == codex_module.INJECT_MISSING
        time.sleep(0.1)
        assert len(made) == 1 and not made[0].connected
        assert _turn_texts(made[0]) == []
        assert harness.session_id is None     # the unwritten thread is forgotten
        assert harness._stop.is_set() and not harness.app_server.alive
    finally:
        end_endpoint(endpoint)


def test_a_retry_that_meets_a_codex_without_inject_gives_up(turns_env):
    # The first load fails as any load can; the supervisor's retry then finds the
    # call missing, and the launch ends with the line instead of retrying again.
    harness = None
    made = []

    def clients(**kwargs):
        client = _old_codex(FakeClient)(harness, **kwargs)
        if not made:
            client.connect = lambda: (_ for _ in ()).throw(OSError("offline"))
        made.append(client)
        return client

    harness = CodexHarness(
        turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
        client_factory=clients, app_server_factory=FakeAppServer,
        backoff_factory=lambda: itertools.repeat(0.001),
    )
    endpoint = Endpoint(harness, session_id=None).open()
    try:
        harness.start()
        deadline = time.monotonic() + 2
        while not endpoint.is_gone and time.monotonic() < deadline:
            time.sleep(0.01)
        assert endpoint.is_gone
        time.sleep(0.1)
        assert len(made) == 2
        assert harness.give_up_reason() == codex_module.INJECT_MISSING
        assert all(_turn_texts(client) == [] for client in made)
        assert harness.session_id is None
    finally:
        end_endpoint(endpoint)


def test_start_warning_runs_but_resume_warning_fails_and_stops_its_process(turns_env):
    verdicts = iter(["start differs", "resume differs"])
    harness = None
    made = []

    def clients(**kwargs):
        client = FakeClient(harness, **kwargs)
        made.append(client)
        return client

    harness = CodexHarness(
        turns_env, window=False, verify=lambda answer: next(verdicts),
        ping=["/env/bin/moxi-turns-ping"], client_factory=clients,
        app_server_factory=FakeAppServer,
    )
    endpoint = Endpoint(harness).open()
    try:
        harness.hook_server.start()
        harness._connect()
        assert endpoint.session()["trouble"] == "start differs"
        row = next(row for row in doctor._endpoint_rows()
                   if row.name.startswith("endpoint codex"))
        assert row.state == "warn" and "start differs" in row.detail
        made[-1].close()
        with pytest.raises(RuntimeError, match="resume differs"):
            harness._connect()
        assert not harness.app_server.alive
        harness._load_failed(RuntimeError("resume differs"))
        assert endpoint.session()["trouble"] == "resume differs"
    finally:
        end_endpoint(endpoint)


def test_real_host_builds_stop_facts_through_its_hook_server(monkeypatch, turns_env):
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    hold = session.handle_stops(lambda facts: "wait" if facts.task_types else None)
    delivery = session.deliver("work", only_if_idle=True)
    assert delivery.wait_started(2).turn_id == "turn-1"
    try:
        import io
        import json
        import sys

        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
            "session_id": "thread-1", "turn_id": "turn-1",
            "background_tasks": [{"type": "background-terminal"}],
            "stop_hook_active": False,
        })))
        output = io.StringIO()
        monkeypatch.setattr(sys, "stdout", output)
        assert codex_hooks.main([
            "--socket", str(harness.hook_server.socket_path), "Stop",
        ]) == 0
        assert json.loads(output.getvalue()) == {"decision": "block", "reason": "wait"}
        assert harness.stop_facts({"turn_id": "turn-1", "task_types": ["x"]}) == \
            StopFacts("turn-1", "thread-1", None, ("x",), 0)
    finally:
        hold.close()
        end_endpoint(endpoint)


def test_phantom_turn_start_id_is_never_reported(monkeypatch, turns_env):
    endpoint, harness, app = _host(turns_env)
    original = app.call

    def call(method, params=None, timeout=None):
        if method == "turn/start":
            app.calls.append((method, params))
            return {"turn": {"id": "phantom"}}
        return original(method, params, timeout)

    app.call = call
    monkeypatch.setattr(codex_module, "TURN_STARTED_WAIT", 0.01)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        # The request went out and Codex answered it, so the text may be in: not a
        # refusal. Nothing names a turn to tie it to, so it reads lost at once.
        delivery = session.deliver("hello", only_if_idle=True)
        started = delivery.wait_started(1)
        assert isinstance(started, Lost) and started.untied
        assert delivery.wait_ended(1) == started
        assert not endpoint.facts().turn_open
    finally:
        end_endpoint(endpoint)


def test_exhausted_steer_mismatches_refuse_instead_of_claiming_delivery(turns_env):
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    first = session.deliver("first", only_if_idle=True)
    assert first.wait_started(2).turn_id == "turn-1"
    original = app.call
    count = itertools.count(2)

    def call(method, params=None, timeout=None):
        if method == "turn/steer":
            found = f"turn-{next(count)}"
            raise proto.ActiveTurnMismatch(-32600, "changed", params["expectedTurnId"],
                                           found)
        return original(method, params, timeout)

    app.call = call
    try:
        delivery = session.steer("follow-up")
        assert delivery.wait_started(2).reason == "unsupported"
        assert delivery.wait_ended(2).reason == "unsupported"
        assert _turn_texts(app) == ["first"]
    finally:
        end_endpoint(endpoint)


@pytest.mark.parametrize("mode", ["raise", "timeout"])
def test_verifier_fault_fails_load_without_publishing(mode, turns_env):
    def verify(answer):
        if mode == "raise":
            raise LookupError("bad verifier")
        time.sleep(0.2)

    harness = None
    made = []

    def clients(**kwargs):
        client = FakeClient(harness, **kwargs)
        made.append(client)
        return client

    harness = CodexHarness(
        turns_env, window=False, verify=verify, verify_wait=0.02,
        ping=["/env/bin/moxi-turns-ping"], client_factory=clients,
        app_server_factory=FakeAppServer,
    )
    endpoint = Endpoint(harness).open()
    try:
        harness.hook_server.start()
        with pytest.raises(RuntimeError):
            harness._connect()
        assert harness.ws is None
        assert harness.generation is None
        assert harness.session_id is None
        assert not harness.app_server.alive
        assert not made[0].connected
    finally:
        end_endpoint(endpoint)


def test_dead_process_open_turn_settles_error_before_resume(turns_env):
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    delivery = session.deliver("work", only_if_idle=True)
    assert delivery.wait_started(2).turn_id == "turn-1"
    harness.app_server.alive = False
    app.close()
    harness._connect()
    try:
        assert delivery.wait_ended(2).outcome == "error"
        assert not endpoint.facts().turn_open
    finally:
        end_endpoint(endpoint)


def test_backoff_grows_caps_and_jitters():
    class Lowest:
        @staticmethod
        def uniform(low, high):
            assert (low, high) == (-0.2, 0.2)
            return -0.2

    delays = codex_module._backoff_delays(Lowest())
    assert [next(delays) for _ in range(8)] == [0.4, 0.8, 1.6, 3.2, 6.4, 12.8,
                                                   24.0, 24.0]


def test_headless_first_failure_returns_and_retries_until_accepted(turns_env):
    harness = None
    attempts = 0
    backoff_starts = 0

    def clients(**kwargs):
        nonlocal attempts
        attempts += 1
        client = FakeClient(harness, **kwargs)
        if attempts == 1:
            client.connect = lambda: (_ for _ in ()).throw(OSError("offline"))
        return client

    def backoffs():
        nonlocal backoff_starts
        backoff_starts += 1
        return itertools.repeat(0.001)

    harness = CodexHarness(
        turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
        client_factory=clients, app_server_factory=FakeAppServer,
        backoff_factory=backoffs,
    )
    endpoint = Endpoint(harness).open()
    try:
        harness.start()
        assert harness._accepted.wait(2)
        assert harness.session_id == "thread-1"
        assert not endpoint.is_gone
        assert "trouble" not in endpoint.session()
        assert attempts >= 2
        deadline = time.monotonic() + 1
        while backoff_starts < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert backoff_starts >= 2  # initial retry sequence, then reset on acceptance
    finally:
        end_endpoint(endpoint)


def test_headless_first_accepted_load_after_a_failure_names_the_thread_in_ready(turns_env):
    # A no-window launch returns once its first load was tried. When that load
    # failed, the caller has no thread id; the first accepted load gives it in `Ready`.
    harness = None
    attempts = 0
    retry = threading.Event()

    def clients(**kwargs):
        nonlocal attempts
        attempts += 1
        client = FakeClient(harness, **kwargs)
        if attempts == 1:
            client.connect = lambda: (_ for _ in ()).throw(OSError("offline"))
        else:
            assert retry.wait(5)
        return client

    harness = CodexHarness(
        turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
        client_factory=clients, app_server_factory=FakeAppServer,
        backoff_factory=lambda: itertools.repeat(0.001),
    )
    endpoint = Endpoint(harness, session_id=None).open()
    events = []
    try:
        harness.start()
        session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
        assert session.session_id is None
        watcher = threading.Thread(target=lambda: events.extend(session.watch()),
                                   daemon=True)
        watcher.start()
        time.sleep(0.2)
        retry.set()
        deadline = time.monotonic() + 2
        while (not any(type(e).__name__ == "Ready" for e in events)
               and time.monotonic() < deadline):
            time.sleep(0.02)
        readies = [(e.source, e.session_id) for e in events
                   if type(e).__name__ == "Ready"]
        assert readies == [("startup", "thread-1")]
    finally:
        retry.set()
        end_endpoint(endpoint)


def test_untrusted_hooks_give_up_on_the_first_load(turns_env):
    harness = None

    class UntrustedClient(FakeClient):
        def call(self, method, params=None, timeout=None):
            if method == "hooks/list":
                return {"data": []}
            return super().call(method, params, timeout)

    def clients(**kwargs):
        return UntrustedClient(harness, **kwargs)

    harness = CodexHarness(
        turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
        client_factory=clients, app_server_factory=FakeAppServer,
    )
    endpoint = Endpoint(harness).open()
    with pytest.raises(codex_hooks.HooksUntrusted):
        harness.start()
    try:
        assert harness._stop.is_set()
        assert not harness.app_server.alive
        assert harness.ws is None
    finally:
        end_endpoint(endpoint)


def test_close_cleans_a_candidate_started_during_shutdown(turns_env):
    start_entered = threading.Event()
    release_start = threading.Event()
    made = []
    harness = None

    class BlockingAppServer(FakeAppServer):
        def start(self):
            self.alive = True
            self.starts += 1
            start_entered.set()
            assert release_start.wait(2)
            return self

    def clients(**kwargs):
        client = FakeClient(harness, **kwargs)
        made.append(client)
        return client

    harness = CodexHarness(
        turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
        client_factory=clients, app_server_factory=BlockingAppServer,
    )
    endpoint = Endpoint(harness).open()
    harness.hook_server.start()
    connect_errors = []
    connecting = threading.Thread(
        target=lambda: _capture_error(harness._connect, connect_errors)
    )
    connecting.start()
    assert start_entered.wait(2)
    stopping = threading.Thread(target=harness.close)
    stopping.start()
    assert harness._stop.wait(2)
    release_start.set()
    connecting.join(2)
    stopping.join(2)
    endpoint.stop("shutdown race")
    assert connect_errors
    assert not harness.app_server.alive
    assert made and not made[0].connected
    assert harness.generation is None
    assert not endpoint.dir.exists()


def _capture_error(call, errors):
    try:
        call()
    except BaseException as exc:
        errors.append(exc)


def test_a_refused_inject_closes_its_candidate_and_retries(turns_env):
    harness = None
    made = []
    refusals = [proto.CodexError(-32603, "inject refused")]

    class RefuseInject(FakeClient):
        def call(self, method, params=None, timeout=None):
            if method == "thread/inject_items" and refusals:
                raise refusals.pop()
            return super().call(method, params, timeout)

    class NoWindow:
        def __init__(self, host, **kwargs):
            pass

        def start(self):
            return self

        def stop(self):
            pass

    def clients(**kwargs):
        client = RefuseInject(harness, **kwargs)
        made.append(client)
        return client

    harness = CodexHarness(turns_env, window=True, ping=["/env/bin/moxi-turns-ping"],
                           client_factory=clients, app_server_factory=FakeAppServer,
                           window_factory=NoWindow,
                           backoff_factory=lambda: iter(lambda: 0.01, None))
    endpoint = Endpoint(harness, session_id=None).open()
    try:
        harness.start()
        assert not made[0].connected
        assert len(made) == 2 and harness.ws is made[1]
        # The retry starts a thread again: the refused one has no rollout to resume.
        assert [m for m, _ in made[1].calls if m.startswith("thread/")] == [
            "thread/start", "thread/inject_items"]
        assert harness.generation.ready.is_set()
        assert "trouble" not in endpoint.session()
        assert all(_turn_texts(client) == [] for client in made)
    finally:
        end_endpoint(endpoint)


def test_a_delivery_made_during_the_inject_waits_at_the_gate(turns_env):
    # The inject is part of the load, so the gate stays shut while it runs. A
    # delivery made then is not handed to the candidate; it starts on the next load.
    harness = None
    made = []
    in_inject, release = threading.Event(), threading.Event()

    class RefuseInject(FakeClient):
        def call(self, method, params=None, timeout=None):
            if method == "thread/inject_items":
                self.calls.append((method, params))
                in_inject.set()
                assert release.wait(5)
                raise proto.CodexError(-32603, "inject refused")
            return super().call(method, params, timeout)

    def clients(**kwargs):
        client = (FakeClient if made else RefuseInject)(harness, **kwargs)
        made.append(client)
        return client

    harness = CodexHarness(turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
                           client_factory=clients, app_server_factory=FakeAppServer,
                           backoff_factory=lambda: iter(lambda: 0.01, None))
    endpoint = Endpoint(harness, session_id=None).open()
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    starter = threading.Thread(target=harness.start, daemon=True)
    try:
        starter.start()
        assert in_inject.wait(2)
        assert harness.loading()
        queued = session.deliver("mid-load")
        time.sleep(0.1)
        release.set()
        assert queued.wait_started(2).turn_id == "turn-1"
        assert _turn_texts(made[0]) == []
        assert _turn_texts(made[1]) == ["mid-load"]
    finally:
        release.set()
        end_endpoint(endpoint)
        starter.join(2)


def _watched_host(turns_env, registry=None):
    """A host with a watcher on its events; the start is left to the test."""
    harness = None
    made = []

    def clients(**kwargs):
        client = FakeClient(harness, **kwargs)
        made.append(client)
        return client

    harness = CodexHarness(turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
                           client_factory=clients, app_server_factory=FakeAppServer)
    endpoint = Endpoint(harness, session_id=None, registry=registry).open()
    events = []
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    watcher = threading.Thread(target=lambda: events.extend(session.watch()), daemon=True)
    watcher.start()
    time.sleep(0.2)
    return endpoint, harness, made, events, session, watcher


def _readies(events, count):
    def readies():
        return [(e.source, e.session_id) for e in events if type(e).__name__ == "Ready"]

    deadline = time.monotonic() + 2
    while len(readies()) < count and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.1)
    return readies()


def test_accepted_start_and_resume_each_raise_one_ready(turns_env):
    endpoint, harness, made, events, session, watcher = _watched_host(turns_env)
    try:
        harness.start()
        # A turn runs, so the thread is no longer unturned and the reload is a resume.
        delivery = session.deliver("work", only_if_idle=True)
        assert delivery.wait_started(2).turn_id == "turn-1"
        made[-1].complete()
        delivery.wait_ended(2)
        made[-1].close()
        harness._connect()
        # The resume's own SessionStart hook asks the listeners but raises no second Ready.
        assert endpoint.ready("resume", "thread-1", announce=False) is None
        assert _readies(events, 2) == [("startup", "thread-1"), ("resume", "thread-1")]
        # The start's Ready follows the inject, which belongs to the load; no turn
        # comes before it.
        names = [type(e).__name__ for e in events]
        assert names.index("Ready") < names.index("TurnStarted")
        assert [m for m, _ in made[0].calls].index("thread/inject_items") \
            < [m for m, _ in made[0].calls].index("turn/start")
    finally:
        end_endpoint(endpoint)
        watcher.join(2)


def test_an_unturned_resume_raises_ready_startup(turns_env):
    # A reload before the thread's first turn (a supervisor restart, or a launcher
    # resume of that id): its turn list is empty, so the load is a start.
    endpoint, harness, made, events, session, watcher = _watched_host(turns_env)
    try:
        harness.start()
        made[-1].close()
        harness._connect()
        assert ("thread/turns/list", {"threadId": "thread-1", "limit": 1}) \
            in made[-1].calls
        assert _readies(events, 2) == [("startup", "thread-1"), ("startup", "thread-1")]
        assert harness._unturned
    finally:
        end_endpoint(endpoint)
        watcher.join(2)


def test_a_launch_that_resumes_an_unturned_thread_raises_ready_startup(turns_env):
    harness = None
    made = []

    def clients(**kwargs):
        client = FakeClient(harness, **kwargs)
        made.append(client)
        return client

    harness = CodexHarness(turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
                           client_factory=clients, app_server_factory=FakeAppServer,
                           resume="thread-1")
    endpoint = Endpoint(harness, session_id="thread-1").open()
    events = []
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    watcher = threading.Thread(target=lambda: events.extend(session.watch()), daemon=True)
    watcher.start()
    time.sleep(0.2)
    try:
        harness.start()
        assert _injects(made[0]) == [] and _turn_texts(made[0]) == []
        assert _readies(events, 1) == [("startup", "thread-1")]
    finally:
        end_endpoint(endpoint)
        watcher.join(2)


def test_the_first_turns_end_clears_the_unturned_mark(turns_env):
    endpoint, harness, made, events, session, watcher = _watched_host(turns_env)
    try:
        harness.start()
        assert harness._unturned
        delivery = session.deliver("work", only_if_idle=True)
        assert delivery.wait_started(2).turn_id == "turn-1"
        # turn/started never clears it: the turn's SessionStart may still be to come.
        assert harness._unturned
        made[-1].complete()
        delivery.wait_ended(2)
        assert not harness._unturned
    finally:
        end_endpoint(endpoint)
        watcher.join(2)


def _start_listener():
    from moxi_turns import listeners

    asked = []

    def answer(event):
        asked.append(event.source)
        return f"context at {event.source}"

    listener = listeners.Listener("sample", frozenset({"start"}), "sample.module:answer",
                                  _fn=answer)
    return listeners.Registry((listener,), order=("sample",)), asked


def _session_start(monkeypatch, harness, source):
    import io
    import json

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "thread-1", "source": source})))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    assert codex_hooks.main(["--socket", str(harness.hook_server.socket_path),
                             "SessionStart"]) == 0
    return json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]


def test_an_unturned_threads_session_start_resume_reads_startup_once(
        monkeypatch, turns_env):
    # Codex says `resume` at the first turn of a thread reloaded before it had any, and
    # fires that SessionStart inside the turn, after `turn/started`.
    registry, asked = _start_listener()
    endpoint, harness, made, events, session, watcher = _watched_host(turns_env, registry)
    try:
        harness.start()
        made[-1].close()
        harness._connect()                      # the reload before any turn
        assert harness._unturned
        delivery = session.deliver("first", only_if_idle=True)
        assert delivery.wait_started(2).turn_id == "turn-1"   # turn/started came first
        assert _session_start(monkeypatch, harness, "resume") == "context at startup"
        assert asked == ["startup"]
        assert not harness._unturned            # cleared once the answer was out
        # A later SessionStart of the same thread is what Codex says it is.
        assert _session_start(monkeypatch, harness, "resume") == "context at resume"
        assert asked == ["startup", "resume"]
    finally:
        end_endpoint(endpoint)
        watcher.join(2)


def test_a_turned_threads_session_start_resume_stays_resume(monkeypatch, turns_env):
    registry, asked = _start_listener()
    endpoint, harness, made, events, session, watcher = _watched_host(turns_env, registry)
    try:
        harness.start()
        delivery = session.deliver("work", only_if_idle=True)
        assert delivery.wait_started(2).turn_id == "turn-1"
        assert _session_start(monkeypatch, harness, "startup") == "context at startup"
        made[-1].complete()
        delivery.wait_ended(2)
        made[-1].close()
        harness._connect()                      # the reload after a turn
        assert not harness._unturned
        assert _session_start(monkeypatch, harness, "resume") == "context at resume"
        assert asked == ["startup", "resume"]
    finally:
        end_endpoint(endpoint)
        watcher.join(2)


def test_a_refused_inject_raises_no_ready(turns_env):
    # Only an accepted load raises Ready, and a refused inject fails the start. The
    # refused thread has no rollout, so `thread/resume` would refuse it for good; the
    # retry starts a new thread instead. The first thread's id, already named by
    # `identify`, is abandoned, and the launch's one Ready names the new one.
    harness = None
    made = []

    class RefuseInject(FakeClient):
        def call(self, method, params=None, timeout=None):
            if method == "thread/inject_items":
                raise proto.CodexError(-32603, "inject refused")
            return super().call(method, params, timeout)

    class NewThread(FakeClient):
        def call(self, method, params=None, timeout=None):
            if method == "thread/resume":
                self.calls.append((method, params))
                raise proto.CodexError(
                    -32600, f"no rollout found for thread id {params['threadId']}")
            if method == "thread/start":
                self.calls.append((method, params))
                return {"thread": {"id": "thread-2"}}
            return super().call(method, params, timeout)

    def clients(**kwargs):
        client = (NewThread if made else RefuseInject)(harness, **kwargs)
        made.append(client)
        return client

    harness = CodexHarness(turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
                           client_factory=clients, app_server_factory=FakeAppServer,
                           backoff_factory=lambda: iter(lambda: 0.01, None))
    endpoint = Endpoint(harness, session_id=None).open()
    events = []
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    watcher = threading.Thread(target=lambda: events.extend(session.watch()), daemon=True)
    watcher.start()
    time.sleep(0.2)
    try:
        harness.start()
        assert harness._accepted.wait(2)
        time.sleep(0.2)
        assert len(made) == 2
        assert [m for m, _ in made[1].calls if m.startswith("thread/")] == [
            "thread/start", "thread/inject_items"]
        assert harness.session_id == "thread-2"
        assert [(e.source, e.session_id) for e in events
                if type(e).__name__ == "Ready"] == [("startup", "thread-2")]
        hello = endpoint.session()
        assert hello["session_id"] == "thread-2" and hello["earlier"] == ["thread-1"]
        assert endpoint.facts().session_id == "thread-2"
    finally:
        end_endpoint(endpoint)
        watcher.join(2)


@pytest.mark.parametrize("answer", [True, {"sandbox": "differs"}, ["differs"]])
def test_a_non_string_verify_answer_fails_the_load(turns_env, answer):
    endpoint, harness, first = _host(turns_env)
    try:
        harness.verify = lambda result: answer
        first.close()
        with pytest.raises(RuntimeError, match="not a reason"):
            harness._connect()
        assert not harness.app_server.alive and harness.loading()
    finally:
        end_endpoint(endpoint)


# -- A Codex turn closes only on Codex's own word ------------------------------------


def _hook(monkeypatch, harness, event, turn_id):
    """Send one Codex hook ping, as the hook command would, and return what it prints."""
    import io
    import json
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "session_id": harness.session_id, "turn_id": turn_id, "stop_hook_active": False,
    })))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    assert codex_hooks.main(["--socket", str(harness.hook_server.socket_path), event]) == 0
    return output.getvalue()


def test_a_plain_steer_after_an_unblocked_stop_reads_idle(monkeypatch, turns_env):
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        first = session.deliver("first", only_if_idle=True)
        assert first.wait_started(2).turn_id == "turn-1"
        assert _hook(monkeypatch, harness, "Stop", "turn-1") == ""
        delivery = session.steer("too late")
        assert bare(delivery.wait_started(2)) == bare(delivery.wait_ended(2)) == Refused("idle")
        assert endpoint.facts().turn_open
        assert not [m for m, _ in app.calls if m == "turn/steer"]
        app.complete()
        assert bare(first.wait_ended(2)) == Ended("turn-1", "answer")
    finally:
        end_endpoint(endpoint)


def test_a_blocked_stop_leaves_the_turn_open_to_steers(monkeypatch, turns_env):
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    hold = session.handle_stops(lambda facts: "reply first")
    try:
        first = session.deliver("first", only_if_idle=True)
        assert first.wait_started(2).turn_id == "turn-1"
        assert "reply first" in _hook(monkeypatch, harness, "Stop", "turn-1")
        delivery = session.steer("x")
        assert bare(delivery.wait_started(2)) == Started("turn-1")
        assert [p["input"][0]["text"] for m, p in app.calls if m == "turn/steer"] == ["x"]
        app.complete()
        assert bare(delivery.wait_ended(2)) == Ended("turn-1", "answer")
    finally:
        hold.close()
        end_endpoint(endpoint)


def test_the_stop_answer_waits_for_a_steer_codex_has_not_yet_answered(
        monkeypatch, turns_env):
    # A turn/steer already on its way when the hold answers Stop must reach Codex
    # before the Stop answer does: Codex then takes it inside the hook, before its end
    # check, and answers it. None may land in the gap after.
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    first = session.deliver("first", only_if_idle=True)
    assert first.wait_started(2).turn_id == "turn-1"
    steering, gate = threading.Event(), threading.Event()
    original = app.call
    order = []

    def call(method, params=None, timeout=None):
        if method == "turn/steer":
            steering.set()
            assert gate.wait(5)
            order.append("steer answered")
        return original(method, params, timeout)

    app.call = call
    try:
        delivery = session.steer("x")            # answered at once; turn/steer on the worker
        assert steering.wait(2)
        timer = threading.Timer(0.3, gate.set)    # Codex answers the steer later
        timer.start()
        assert _hook(monkeypatch, harness, "Stop", "turn-1") == ""   # the ping runs here
        order.append("stop answered")
        assert order == ["steer answered", "stop answered"]
        assert bare(delivery.wait_started(2)) == Started("turn-1")
        app.complete()
        assert bare(delivery.wait_ended(2)) == Ended("turn-1", "answer")
    finally:
        gate.set()
        end_endpoint(endpoint)


def test_a_steer_the_endpoint_tied_before_the_stop_and_sent_after_it_waits(
        monkeypatch, turns_env):
    # The other side of that race: the endpoint recorded the steer on turn-1 before the
    # Stop, but its turn/steer had not gone out when the Stop was answered. It must not go
    # out after: it reads `idle`, and the caller delivers it as a new turn if it wants.
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    first = session.deliver("first", only_if_idle=True)
    assert first.wait_started(2).turn_id == "turn-1"
    entered, gate = threading.Event(), threading.Event()
    original = harness._inject

    def inject(text):
        entered.set()
        assert gate.wait(5)
        return original(text)

    harness._inject = inject
    try:
        delivery = session.steer("x")
        assert entered.wait(2)
        assert _hook(monkeypatch, harness, "Stop", "turn-1") == ""
        gate.set()
        assert bare(delivery.wait_started(2)) == bare(delivery.wait_ended(2)) == Refused("idle")
        assert not [m for m, _ in app.calls if m == "turn/steer"]
        assert endpoint.facts().turn_open
        app.complete()
        assert bare(first.wait_ended(2)) == Ended("turn-1", "answer")
        assert _turn_texts(app) == ["first"] and app.absorbed == []
    finally:
        gate.set()
        end_endpoint(endpoint)


def test_a_start_absorbed_into_a_turn_already_reported_reads_lost_at_once(
        monkeypatch, turns_env):
    # The reported-turn guard: the ledger is closed while Codex still runs turn-1, which
    # the host has already reported. A turn/start Codex absorbs into it cannot be tied,
    # and reporting turn-1 again would close it as error: the delivery reads lost.
    monkeypatch.setattr(codex_module, "TURN_STARTED_WAIT", 0.01)
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        first = session.deliver("first", only_if_idle=True)
        assert first.wait_started(2).turn_id == "turn-1"
        harness.settle_turn("turn-1", "error")      # the ledger closes; Codex runs on
        assert bare(first.wait_ended(2)) == Ended("turn-1", "error")
        epoch = endpoint.facts().turn_number
        delivery = session.deliver("y")
        started = delivery.wait_started(2)
        assert isinstance(started, Lost) and started.untied
        assert delivery.wait_ended(1) == started
        assert app.absorbed == ["y"]
        facts = endpoint.facts()
        assert not facts.turn_open and facts.turn_number == epoch
    finally:
        end_endpoint(endpoint)


def test_an_interrupt_ends_the_turn_at_its_completion_not_at_the_ping(
        monkeypatch, turns_env):
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        first = session.deliver("first", only_if_idle=True)
        assert first.wait_started(2).turn_id == "turn-1"
        assert _hook(monkeypatch, harness, "Interrupt", "turn-1") == ""
        assert endpoint.facts().turn_open
        with pytest.raises(TimeoutError):
            first.wait_ended(0.05)
        app.complete("interrupted")
        assert bare(first.wait_ended(2)) == Ended("turn-1", "aborted")
        assert not endpoint.facts().turn_open
    finally:
        end_endpoint(endpoint)


def test_codex_going_idle_without_completing_ends_the_turn_after_the_grace(
        monkeypatch, turns_env):
    monkeypatch.setattr(codex_module, "TURN_COMPLETED_GRACE", 0.05)
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        first = session.deliver("first", only_if_idle=True)
        assert first.wait_started(2).turn_id == "turn-1"
        assert _hook(monkeypatch, harness, "Interrupt", "turn-1") == ""
        app.status("idle")                  # and no turn/completed ever comes
        assert endpoint.facts().turn_open
        assert bare(first.wait_ended(2)) == Ended("turn-1", "error")
        assert not endpoint.facts().turn_open
    finally:
        end_endpoint(endpoint)


def test_a_completion_or_a_return_to_active_inside_the_grace_stops_its_timer(
        monkeypatch, turns_env):
    monkeypatch.setattr(codex_module, "TURN_COMPLETED_GRACE", 0.2)
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        first = session.deliver("first", only_if_idle=True)
        assert first.wait_started(2).turn_id == "turn-1"
        assert _hook(monkeypatch, harness, "Interrupt", "turn-1") == ""
        app.status("idle")
        app.complete("interrupted")
        assert bare(first.wait_ended(2)) == Ended("turn-1", "aborted")
        second = session.deliver("second", only_if_idle=True)
        assert second.wait_started(2).turn_id == "turn-2"
        app.status("idle")
        app.status("active")
        time.sleep(0.4)                     # both timers would have run out by now
        assert endpoint.facts().turn_open
        with pytest.raises(TimeoutError):
            second.wait_ended(0.01)
        app.complete()
        assert bare(second.wait_ended(2)) == Ended("turn-2", "answer")
    finally:
        end_endpoint(endpoint)


_RAISES = [
    # Not connected, or the send itself raised: the request never went out.
    pytest.param(lambda: proto.AppServerOffline("Codex app-server not connected"),
                 "refused", id="not-sent"),
    # Codex answered the request with an error.
    pytest.param(lambda: proto.CodexError(-32000, "synthetic failure"), "refused",
                 id="error-answer"),
    # The connection closed after the send, or the call timed out: Codex may have it.
    pytest.param(lambda: proto.NoReply("Codex app-server closed the connection"), "lost",
                 id="closed-after-send"),
    pytest.param(lambda: proto.NoReply("Codex app-server timed out on turn/start"),
                 "lost", id="timed-out"),
]


def _check(delivery, reading):
    started, ended = delivery.wait_started(2), delivery.wait_ended(2)
    if reading == "refused":
        assert bare(started) == bare(ended) == Refused("unsupported")
    else:
        assert isinstance(started, Lost) and started.untied and ended == started


@pytest.mark.parametrize("make,reading", _RAISES)
def test_a_raising_turn_start_reads_refused_only_when_codex_did_not_take_it(
        turns_env, make, reading):
    endpoint, harness, app = _host(turns_env)
    original = app.call

    def call(method, params=None, timeout=None):
        if method == "turn/start":
            raise make()
        return original(method, params, timeout)

    app.call = call
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        _check(session.deliver("hello", only_if_idle=True), reading)
        assert not endpoint.facts().turn_open
    finally:
        end_endpoint(endpoint)


@pytest.mark.parametrize("make,reading", _RAISES)
def test_a_raising_turn_steer_reads_refused_only_when_codex_did_not_take_it(
        turns_env, make, reading):
    endpoint, harness, app = _host(turns_env)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    first = session.deliver("first", only_if_idle=True)
    assert first.wait_started(2).turn_id == "turn-1"
    original = app.call

    def call(method, params=None, timeout=None):
        if method == "turn/steer":
            raise make()
        return original(method, params, timeout)

    app.call = call
    try:
        _check(session.steer("now"), reading)
        assert endpoint.facts().turn_open
        app.complete()
        assert bare(first.wait_ended(2)) == Ended("turn-1", "answer")
    finally:
        end_endpoint(endpoint)


def test_a_raising_turns_list_after_an_absorbed_start_reads_lost(monkeypatch, turns_env):
    # Once turn/start has answered, Codex has the text: a raise from the later
    # thread/turns/list call is not a refusal either.
    endpoint, harness, app = _host(turns_env)
    original = app.call

    def call(method, params=None, timeout=None):
        if method == "turn/start":
            app.calls.append((method, params))
            return {"turn": {"id": "phantom"}}
        if method == "thread/turns/list":
            raise proto.AppServerOffline("Codex app-server closed the connection")
        return original(method, params, timeout)

    app.call = call
    monkeypatch.setattr(codex_module, "TURN_STARTED_WAIT", 0.01)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        delivery = session.deliver("hello", only_if_idle=True)
        started = delivery.wait_started(2)
        assert isinstance(started, Lost) and started.untied
        assert not endpoint.facts().turn_open
    finally:
        end_endpoint(endpoint)


def test_a_load_that_shows_the_turn_running_stops_the_grace(monkeypatch, turns_env):
    # The status backstop is Codex's word read late; a reconnect whose load shows the
    # turn still in progress is a later word, and the timer set before it stops.
    monkeypatch.setattr(codex_module, "TURN_COMPLETED_GRACE", 0.2)
    harness = None
    made = []

    class ActiveResume(FakeClient):
        def call(self, method, params=None, timeout=None):
            if method == "thread/resume":
                self.calls.append((method, params))
                return {"thread": {"id": "thread-1", "status": {"type": "active"}}}
            return super().call(method, params, timeout)

    def clients(**kwargs):
        client = (ActiveResume if made else FakeClient)(harness, **kwargs)
        if made:
            client.live_turn = made[0].live_turn
        made.append(client)
        return client

    harness = CodexHarness(turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
                           client_factory=clients, app_server_factory=FakeAppServer)
    endpoint = Endpoint(harness, session_id=None).open()
    harness.start()
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        running = session.deliver("running", only_if_idle=True)
        assert running.wait_started(2).turn_id == "turn-1"
        made[0].status("idle")
        made[0].close()
        harness._connect()
        time.sleep(0.4)
        assert endpoint.facts().turn_open
        made[1].complete()
        assert bare(running.wait_ended(2)) == Ended("turn-1", "answer")
    finally:
        end_endpoint(endpoint)


def test_noreply_after_turn_started_claimed_the_delivery_leaves_it_on_that_turn(
        turns_env):
    # turn/started reaches the host and claims the delivery, then the connection
    # closes before turn/start's answer. Codex named the turn that took the text, so the
    # delivery stays Started on it and ends with it; NoReply does not make it Lost.
    endpoint, harness, app = _host(turns_env)
    original = app.call

    def call(method, params=None, timeout=None):
        if method == "turn/start" and params["input"][0]["text"] == "hello":
            app.calls.append((method, params))
            app.live_turn = "turn-1"
            app.on_notification("turn/started", {"turn": {"id": "turn-1"}}, None)
            raise proto.NoReply("Codex app-server closed the connection")
        return original(method, params, timeout)

    app.call = call
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        delivery = session.deliver("hello", only_if_idle=True)
        assert bare(delivery.wait_started(2)) == Started("turn-1")
        with pytest.raises(TimeoutError):
            delivery.wait_ended(0.2)
        assert endpoint.facts().turn_open
        app.complete()
        assert bare(delivery.wait_ended(2)) == Ended("turn-1", "answer")
    finally:
        end_endpoint(endpoint)


class _ActiveResume(FakeClient):
    """A reconnect's client whose load shows the thread running."""

    def call(self, method, params=None, timeout=None):
        if method == "thread/resume":
            self.calls.append((method, params))
            return {"thread": {"id": "thread-1", "status": {"type": "active"}}}
        return super().call(method, params, timeout)


def _reconnecting_host(turns_env, first_client):
    """A host whose first client is `first_client` and whose reconnects resume active,
    carrying the running turn over."""
    harness = None
    made = []

    def clients(**kwargs):
        client = (_ActiveResume if made else first_client)(harness, **kwargs)
        if made:
            client.live_turn = made[0].live_turn
        made.append(client)
        return client

    harness = CodexHarness(turns_env, window=False, ping=["/env/bin/moxi-turns-ping"],
                           client_factory=clients, app_server_factory=FakeAppServer)
    endpoint = Endpoint(harness, session_id=None).open()
    harness.start()
    return endpoint, harness, made


def test_a_reconnect_inside_the_started_wait_ties_the_delivery_to_the_turn_it_shows(
        monkeypatch, turns_env):
    # turn/start is answered, its turn/started is
    # lost with the connection, and the reconnect's load shows the turn that took the
    # text running. That load is a list naming an in-progress turn the host has not
    # reported, so it ties the delivery; nothing waits for PENDING_TTL_S.
    monkeypatch.setattr(codex_module, "TURN_STARTED_WAIT", 3.0)
    sent = threading.Event()

    class DropNotify(FakeClient):
        def call(self, method, params=None, timeout=None):
            if method == "turn/start" and params["input"][0]["text"] == "hello":
                self.calls.append((method, params))
                self.live_turn = "turn-1"     # Codex started it; the notification is lost
                sent.set()
                return {"turn": {"id": "turn-1"}}
            return super().call(method, params, timeout)

    endpoint, harness, made = _reconnecting_host(turns_env, DropNotify)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        delivery = session.deliver("hello", only_if_idle=True)
        assert sent.wait(2)
        time.sleep(0.05)                      # turn/start has answered; the host waits
        made[0].close()
        harness._connect()
        assert bare(delivery.wait_started(1)) == Started("turn-1")
        assert endpoint._inflight is None and endpoint.facts().turn_open
        made[1].complete()
        assert bare(delivery.wait_ended(2)) == Ended("turn-1", "answer")
    finally:
        end_endpoint(endpoint)


def test_a_turn_a_load_reports_before_turn_start_answers_does_not_count_as_its_start(
        monkeypatch, turns_env):
    # The other order: the reconnect's load reports the running turn before
    # turn/start has answered, so it cannot claim the delivery, and its report wakes the
    # wait. _start_turn checks the claim and takes the list path: the turn is already
    # reported, so the delivery reads lost at once instead of waiting in flight.
    monkeypatch.setattr(codex_module, "TURN_STARTED_WAIT", 3.0)
    harness_box = []

    class ReconnectMidStart(FakeClient):
        def call(self, method, params=None, timeout=None):
            if method == "turn/start" and params["input"][0]["text"] == "hello":
                self.calls.append((method, params))
                self.live_turn = "turn-1"     # the person's turn absorbs the text
                harness_box[0]._connect()     # and the link drops and reloads first
                return {"turn": {"id": "phantom-1"}}
            return super().call(method, params, timeout)

    endpoint, harness, made = _reconnecting_host(turns_env, ReconnectMidStart)
    harness_box.append(harness)
    session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
    try:
        delivery = session.deliver("hello", only_if_idle=True)
        started = delivery.wait_started(1)
        assert isinstance(started, Lost) and started.untied
        assert endpoint._inflight is None
        facts = endpoint.facts()
        assert facts.turn_open                # turn-1 is the person's, and runs on
        made[1].complete()
        deadline = time.monotonic() + 2
        while endpoint.facts().turn_open and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not endpoint.facts().turn_open
    finally:
        end_endpoint(endpoint)


# -- the internal sockets' paths ------------------------------------------------------

def test_the_codex_sockets_sit_in_the_endpoint_directory_when_their_paths_are_short(
        turns_env, short_runtime):
    endpoint, harness, app = _host(turns_env)
    try:
        assert harness.app_socket == endpoint.dir / protocol.CODEX_APP_SOCK
        assert harness.hook_server.socket_path == endpoint.dir / protocol.CODEX_HOOKS_SOCK
    finally:
        end_endpoint(endpoint)


@pytest.mark.skipif(sys.platform == "win32", reason="no Unix socket paths on Windows")
def test_a_long_runtime_root_puts_the_codex_sockets_on_the_short_path(
        turns_env, monkeypatch):
    import stat

    from moxi import paths

    long_root = turns_env / ("r" * 60) / ("u" * 60)
    monkeypatch.setenv("MOXI_TURNS_RUNTIME_DIR", str(long_root))
    short_root = turns_env / "s"           # short, and the test's own, never /tmp itself
    short_root.mkdir()
    monkeypatch.setattr(paths, "_SHORT_ROOT", str(short_root))
    endpoint, harness, app = _host(turns_env)
    short = str(short_root / f"moxi-{os.getuid()}") + os.sep
    sockets = (harness.app_socket, harness.hook_server.socket_path)
    try:
        for name in (protocol.CODEX_APP_SOCK, protocol.CODEX_HOOKS_SOCK):
            assert len(os.fsencode(str(endpoint.dir / name))) >= paths.SOCKET_MAX
        assert harness.app_server.socket_path == harness.app_socket
        assert len(set(sockets)) == 2
        for sock in sockets:
            assert str(sock).startswith(short)
            assert len(os.fsencode(str(sock))) < paths.SOCKET_MAX
        # The hooks' server is listening there, and the session runs.
        assert stat.S_ISSOCK(os.lstat(harness.hook_server.socket_path).st_mode)
        session = Session(endpoint.address, endpoint.session(), endpoint_dir=endpoint.dir)
        delivery = session.deliver("hello", only_if_idle=True)
        assert delivery.wait_started(2).turn_id == "turn-1"
        app.complete()
        assert delivery.wait_ended(2).outcome == "answer"
    finally:
        end_endpoint(endpoint)
    assert not os.path.lexists(harness.hook_server.socket_path)
