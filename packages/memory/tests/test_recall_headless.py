"""A chat recall's headless route (`recall_route.Headless`): each round a headless launch
through a fake launcher, its answer and cost read from the run log, a round past its
bound ended through the launcher, `exit`, and a taker's settle of a dead child's round
from the run log its line's request names. Also the request's fork stamp."""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from moxi_memory import (recall_child, recall_fence, recall_route, recall_start,
                         recall_stream, recall_take, recalls, records)

KEY = "chat-kiosk-approver-1530"
SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"


def result_line(text="the kiosk approver was settled in round 4", cost=0.12, **more):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False,
                       "result": text, "total_cost_usd": cost, **more})


class FakeLauncher:
    """Starts a small Python process per launch that writes `plan`'s output to the
    launch's run log, sleeps `sleep_s`, and exits `exit`."""

    Refusal = RuntimeError

    def __init__(self, root: Path):
        self.root = root
        self.runs = root / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        self.plans: list[dict] = []
        self.launched: list[tuple[str, object]] = []
        self.ended: list[str] = []
        self.procs: dict[str, subprocess.Popen] = {}

    def plan(self, **plan):
        self.plans.append(plan)

    def run_log(self, request_id, env=None):
        return self.runs / f"{request_id}.log"

    def _start(self, request_id):
        plan = self.plans.pop(0) if self.plans else {"out": result_line()}
        code = ("import sys, time; open(sys.argv[1], 'w').write(sys.argv[2]); "
                "time.sleep(float(sys.argv[3])); sys.exit(int(sys.argv[4]))")
        p = subprocess.Popen([sys.executable, "-c", code, str(self.run_log(request_id)),
                              plan.get("out", ""), str(plan.get("sleep_s", 0)),
                              str(plan.get("exit", 0))])
        self.procs[request_id] = p
        return types.SimpleNamespace(pid=p.pid, state="started", reason=None)

    def launch(self, req, env=None):
        self.launched.append(("launch", req))
        return self._start(req.request_id)

    def continue_headless(self, session_id, text, *, request_id=None, env=None):
        self.launched.append(("continue", (session_id, text, request_id)))
        return self._start(request_id)

    def end_headless(self, request_id, *, env=None):
        self.ended.append(request_id)
        p = self.procs.get(request_id)
        if p is None or p.poll() is not None:
            return types.SimpleNamespace(outcome="gone", error="")
        p.kill()
        p.wait()
        return types.SimpleNamespace(outcome="ended", error="")

    def running(self, **kw):
        return tuple(types.SimpleNamespace(request_id=rid)
                     for rid, p in self.procs.items() if p.poll() is None)

    def settable_rows(self, harness):
        return ("identity", "keep_warm")

    def LaunchRequest(self, **fields):  # noqa: N802 -- stands in for the class
        return types.SimpleNamespace(**fields)

    def reap(self):
        for p in self.procs.values():
            if p.poll() is None:
                p.kill()
            p.wait()


@pytest.fixture
def launcher(tmp_path):
    fake = FakeLauncher(tmp_path / "launcher")
    yield fake
    fake.reap()


def spec(**kw):
    fields = dict(brief="", cwd="/work", name="recall-c1", request_id="", session_id=SID,
                  max_tools=8, recall_id="c1")
    return recall_fence.Spec(**{**fields, **kw})


def route(launcher, requests=None, **kw):
    return recall_route.Headless(KEY, "c1", spec=spec(), launcher=launcher,
                                 on_request=(requests.append if requests is not None
                                             else None), **kw)


def test_the_request_is_stamped_as_the_recalls_fork(launcher):
    req = recall_fence.request(launcher, spec(request_id="r1"))
    assert req.stamps == {recall_start.SUBCONSCIOUS_FORK_ENV: "c1"}
    assert not hasattr(recall_fence.request(launcher, spec(recall_id="")), "stamps")


def test_a_run_log_with_a_result_gives_the_answer_and_the_cost(launcher):
    from moxi_turns import Ended

    requests = []
    r = route(launcher, requests)
    handle = r.first("the brief", ref="c1:1", round_timeout_s=10)
    assert requests == [handle.request_id] and r.started(handle) == SID
    kind, req = launcher.launched[0]
    assert kind == "launch" and req.request_id == requests[0] and req.typed["brief"] == "the brief"
    assert req.session_id == SID
    end = r.ended(handle, 70)
    assert isinstance(end, Ended) and end.answer.startswith("the kiosk approver")
    assert r.round_fields() == {"request": requests[0], "cost_usd": 0.12}


def test_a_later_round_is_a_headless_resume_of_the_same_session(launcher):
    r = route(launcher)
    first = r.first("the brief", ref="c1:1", round_timeout_s=10)
    r.ended(first, 70)
    second = r.next(first, "keep digging", ref="c1:2", round_timeout_s=10)
    kind, (session, text, rid) = launcher.launched[1]
    assert (kind, session, text, rid) == ("continue", SID, "keep digging",
                                          second.request_id)
    assert second.request_id != first.request_id


@pytest.mark.parametrize("plan", [
    {"out": "error: something went wrong\n"},
    {"out": "", "exit": 1},
    {"out": result_line(is_error=True, subtype="error_max_turns", result=None)},
])
def test_no_result_or_an_error_result_is_lost_gone(launcher, plan):
    from moxi_turns import Lost

    launcher.plan(**plan)
    r = route(launcher)
    end = r.ended(r.first("b", ref="c1:1", round_timeout_s=10), 70)
    assert isinstance(end, Lost) and end.reason.startswith("gone")


def test_past_the_bound_the_launcher_is_asked_to_end_it(launcher):
    from moxi_turns import Lost

    launcher.plan(out="", sleep_s=60)
    r = route(launcher)
    handle = r.first("b", ref="c1:1", round_timeout_s=0.3)
    end = r.ended(handle, 70)
    assert isinstance(end, Lost) and "bound" in end.reason
    assert launcher.ended == [handle.request_id]


def test_exit_asks_the_launcher_to_end_the_running_round(launcher):
    launcher.plan(out="", sleep_s=60)
    r = route(launcher)
    handle = r.first("b", ref="c1:1", round_timeout_s=30)
    assert r.exit() == "ended"
    assert launcher.ended == [handle.request_id]
    assert r.exit() is None                 # nothing runs now


def test_a_refused_launch_is_lost_denied(launcher):
    from moxi_turns import Refused

    def refuse(req, env=None):
        raise RuntimeError("no claude")

    launcher.launch = refuse
    r = route(launcher)
    end = r.ended(r.first("b", ref="c1:1", round_timeout_s=10), 70)
    assert isinstance(end, Refused) and end.reason.startswith("denied")


def test_the_stream_keeps_each_rounds_request_and_cost(launcher):
    launcher.plan(out=result_line("first point", cost=0.1))
    launcher.plan(out=result_line("NOTHING", cost=0.05))
    record = recall_start.begin(KEY, "claude", start=recalls.CHAT, text="the approver",
                                senders=(), settings=recall_start.default_settings(),
                                cwd=None, notes_dir=None, route_name=recall_start.HEADLESS,
                                spawn=lambda s, r: None)
    recall = recall_child.Recall(records.read(KEY, record["recall"]))
    r = recall_route.Headless(KEY, recall.recall_id, spec=spec(recall_id=recall.recall_id),
                              launcher=launcher,
                              on_request=lambda rid: recall.put(request=rid))
    assert recall_stream.run(recall, r, "the brief") == records.ENDED
    rounds = recall.line["rounds"]
    assert [x["text"] for x in rounds] == ["first point", "NOTHING"]
    assert [x["cost_usd"] for x in rounds] == [0.1, 0.05]
    assert [x["request"] for x in rounds] == [rid for _, rid in
                                              [(k, v.request_id if k == "launch" else v[2])
                                               for k, v in launcher.launched]]
    assert recall.line["fork"] == SID and recall.line["kept"] is False
    assert recall.line["state"] == recalls.BACK


# -- a taker ----------------------------------------------------------------------------

def dead_line(launcher, request_id, **fields):
    record = recall_start.begin(KEY, "claude", start=recalls.CHAT, text="the approver",
                                senders=(), settings=recall_start.default_settings(),
                                cwd=None, notes_dir=None, route_name=recall_start.HEADLESS,
                                spawn=lambda s, r: None)
    recall = recall_child.Recall(records.read(KEY, record["recall"]))
    with recalls.session_lock(KEY):
        recall.write(child={"pid": 1, "started": 1.0}, ref=f"{recall.recall_id}:1",
                     kept=True, request=request_id, headless_session=SID, **fields)
    return recall.recall_id


def test_a_taker_settles_a_dead_childs_round_from_the_run_log(launcher, monkeypatch):
    monkeypatch.setattr(recall_route.Headless, "_api", lambda self: launcher)
    launcher.run_log("req-1").write_text(result_line("found it", cost=0.2) + "\n")
    recall_id = dead_line(launcher, "req-1")
    assert recall_take.take(KEY, recall_id) == recall_take.SETTLED
    line = recalls.find_line(KEY, recall_id)
    assert line["state"] == recalls.BACK and line["kept"] is False
    assert line["rounds"][-1]["text"] == "found it"
    assert line["rounds"][-1]["request"] == "req-1"
    assert line["rounds"][-1]["cost_usd"] == 0.2
    assert line["fork"] == SID
    assert records.read(KEY, recall_id)["state"] == records.ENDED


def test_a_taker_leaves_a_round_whose_agent_still_runs(launcher, monkeypatch):
    monkeypatch.setattr(recall_route.Headless, "_api", lambda self: launcher)
    launcher.plan(out="", sleep_s=60)
    launcher._start("req-2")
    recall_id = dead_line(launcher, "req-2")
    assert recall_take.take(KEY, recall_id) == recall_take.LEFT
    line = recalls.find_line(KEY, recall_id)
    assert line["state"] == recalls.OPEN and line["kept"] is True


def test_a_taker_loses_a_round_never_launched(launcher, monkeypatch):
    monkeypatch.setattr(recall_route.Headless, "_api", lambda self: launcher)
    recall_id = dead_line(launcher, None)
    assert recall_take.take(KEY, recall_id) == recall_take.SETTLED
    line = recalls.find_line(KEY, recall_id)
    assert line["state"] == recalls.LOST and line["reason"] == "gone"


def test_for_recall_names_the_headless_route_from_the_line():
    recall = types.SimpleNamespace(session_id=KEY, recall_id="c1",
                                   record={"route": "headless"},
                                   line={"headless_session": SID, "request": "req-9"})
    r = recall_route.for_recall(recall)
    assert isinstance(r, recall_route.Headless)
    assert r.session == SID and r.request == "req-9"
