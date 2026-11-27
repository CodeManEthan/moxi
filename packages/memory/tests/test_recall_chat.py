"""The chat recall: its names in the log and records, `begin`'s keyword for a chat
recall's hurry and its bounded lock wait, and the log's read of the agents' sessions."""

from __future__ import annotations

import threading

import pytest

from moxi_memory import recall_start, recalls, records

KEY = "chat-kiosk-approver-1530"


@pytest.fixture(autouse=True)
def nothing_held():
    """Pieces held by an open call live in the process: each test starts with none."""
    from moxi_memory import recall_chat

    recall_chat._held.clear()
    yield
    recall_chat._held.clear()


def begin(**kw):
    fields = dict(start=recalls.CHAT, text="the kiosk approver", senders=(), settings={},
                  cwd=None, notes_dir=None, route_name=recall_start.HEADLESS,
                  spawn=lambda session_id, recall_id: 4242)
    return recall_start.begin(KEY, "claude", **{**fields, **kw})


def states():
    return [r["state"] for r in records.session_records(KEY)]


def test_the_chat_names_sit_beside_the_terminals():
    assert recalls.CHAT == "chat" and recalls.HEADLESS == "headless"
    assert recalls.FENCE == "fence"
    assert recall_start.HEADLESS == "headless"
    assert recall_start.HEADLESS not in recall_start.ROUTES       # no launch stamp names it
    assert recall_start.HEADLESS not in recall_start.BY_COMMS
    assert {"headless_session", "call"} <= set(recalls.LINE_FIELDS)
    assert {"context", "call", "headless_session"} <= set(records.RECORD_FIELDS)


def test_keep_running_hurries_only_the_oldest_past_it():
    begin(keep_running=1)
    begin(keep_running=1)
    assert states() == [records.STARTING, records.STARTING]
    begin(keep_running=1)
    assert states() == [records.HURRIED, records.STARTING, records.STARTING]


def test_the_default_hurries_every_running_recall_as_before():
    begin()
    begin()
    assert states() == [records.HURRIED, records.STARTING]


def test_a_chat_recalls_record_holds_its_context_and_call_and_nothing_else_extra():
    record = begin(chat={"context": "the approver", "call": 3, "fork": "no"})
    assert record["start"] == "chat" and record["route"] == "headless"
    assert record["context"] == "the approver" and record["call"] == 3
    assert "fork" not in record or record["fork"] is None


def test_a_terminal_record_has_no_chat_fields():
    record = begin(start=recalls.PROMPT, route_name=recall_start.SUBAGENT)
    assert "context" not in record and "call" not in record


def test_lock_s_bounds_the_wait_and_writes_nothing():
    from moxi import files

    held, release = threading.Event(), threading.Event()

    def hold():
        with recalls.session_lock(KEY):
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    try:
        with pytest.raises(files.LockTimeout):
            begin(lock_s=0.2)
    finally:
        release.set()
        holder.join()
    assert records.session_records(KEY) == []


def test_headless_sessions_names_every_chat_recalls_agent():
    with recalls.session_lock(KEY):
        recalls.put_line(KEY, {"recall": "c1", "state": recalls.OPEN, "harness": "claude",
                               "headless_session": "s-1"})
        recalls.put_line(KEY, {"recall": "c2", "state": recalls.OPEN, "harness": "claude"})
    assert recalls.headless_sessions() == frozenset({("claude", "s-1")})


# -- the conversation: collecting, stale and dedupe -----------------------------------------

import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402

from moxi_memory import recall_chat, recall_fence, settings as memory_settings  # noqa: E402

CONV = "kiosk-approver-1530"
DROP_S = 900.0


def a_recall(recall_id, call, *texts, at=None, head="the kiosk approver"):
    at = time.time() if at is None else at
    with recalls.session_lock(KEY):
        recalls.put_line(KEY, {
            "recall": recall_id, "state": recalls.BACK, "harness": "claude",
            "start": recalls.CHAT, "route": recalls.HEADLESS, "call": call, "head": head,
            "rounds": [{"round": i + 1, "text": t, "at": at} for i, t in enumerate(texts)]})


def collect(*, has_words=False, **kw):
    return recall_chat.collect(CONV, has_words=has_words, drop_after_s=kw.pop("drop", DROP_S), **kw)


def ids(got):
    return [p.id for p in got.pieces]


def test_the_key_is_the_label_and_a_bad_label_is_refused():
    assert recall_chat.key(CONV) == KEY
    assert recall_chat.conversation_of(KEY) == CONV
    assert recall_chat.conversation_of("0190b000-0000-7000-8000-000000000001") is None
    for bad in ("short", "Kiosk-approver", "-kiosk-approver", "k" * 41, "kiosk approver"):
        with pytest.raises(ValueError):
            recall_chat.key(bad)


def test_a_piece_is_returned_once_when_written_and_again_when_not():
    a_recall("a", 1, "Recalled: the approver is the kiosk lead")
    first = collect()
    assert ids(first) == ["a:1"]
    recall_chat.settle(CONV, first.pieces, written=False)
    again = collect()
    assert ids(again) == ["a:1"]
    recall_chat.settle(CONV, again.pieces, written=True)
    assert ids(collect()) == []


def test_two_calls_at_once_never_return_the_same_piece():
    a_recall("a", 1, "point one", "point two")
    first, second = collect(), collect()
    assert ids(first) == ["a:1", "a:2"] and ids(second) == []
    recall_chat.settle(CONV, first.pieces, written=False)
    assert ids(collect()) == ["a:1", "a:2"]


def stale_of(got):
    return {p.id: p.stale for p in got.pieces}


def test_stale_by_calls_with_a_call_that_starts_no_recall():
    call1 = collect(has_words=True)                       # call 1 starts recall A
    assert call1.call == 1
    a_recall("a", 1, "A's point")
    call2 = collect()                                 # no words
    assert stale_of(call2) == {"a:1": False}
    recall_chat.settle(CONV, call2.pieces, written=False)
    call3 = collect(has_words=True)                       # no-keyword or busy: no recall
    assert call3.call == 2 and stale_of(call3) == {"a:1": False}   # its own words don't
    recall_chat.settle(CONV, call3.pieces, written=False)
    assert stale_of(collect()) == {"a:1": True}       # call 4


def test_stale_by_calls_with_a_later_recall():
    collect(has_words=True)
    a_recall("a", 1, "A's point")
    call2 = collect(has_words=True)                      # call 2 starts recall B
    recall_chat.release(CONV, ids(call2))
    a_recall("b", 2, "B's point")
    assert stale_of(collect()) == {"a:1": True, "b:1": False}


def test_past_drop_after_s_a_piece_is_dropped_and_never_returned():
    a_recall("a", 1, "an old point", at=time.time() - 120)
    assert ids(collect(drop=60)) == []
    assert "a:1" in recall_chat.read_state(CONV)["dropped"]
    assert ids(collect(drop=DROP_S)) == []


def test_nothing_and_the_end_line_are_never_pieces():
    a_recall("a", 1, "NOTHING", "a point\nEnd of recall.")
    got = collect()
    assert ids(got) == ["a:2"] and got.pieces[0].text == "a point"


def test_a_handed_line_is_cut_from_a_later_piece_and_told_to_the_next_brief(monkeypatch):
    a_recall("a", 1, "the approver is Dana\nthe kiosk runs on 4.2")
    got = collect()
    recall_chat.settle(CONV, got.pieces, written=True)
    a_recall("b", 1, "the approver is Dana\nthe rollout is Friday")
    later = collect()
    assert [p.text for p in later.pieces] == ["the rollout is Friday"]
    a_recall("c", 1, "the kiosk runs on 4.2")          # nothing new: counted as handed
    recall_chat.release(CONV, ids(later))
    assert "c:1" not in ids(collect())
    assert "c:1" in recall_chat.read_state(CONV)["pieces"]
    recall = types.SimpleNamespace(
        session_id=KEY, recall_id="d", settings=recall_start.default_settings(),
        record={"text": "the approver", "context": "kiosk talk"}, put=lambda **kw: None)
    brief, _dirs = recall_chat.chat_brief(recall, search=lambda *a, **k: [],
                                          places=lambda cwd, nd: (None, None))
    assert "the approver is Dana" in brief.split("Already recalled")[1]


def test_at_most_twenty_pieces_go_in_one_answer():
    a_recall("a", 1, *[f"point {i}" for i in range(25)])
    got = collect()
    assert len(got.pieces) == 20 and got.waiting == 5 and "5 more" in got.notes[0]


def hold_in_another_process(path):
    code = ("import sys, time; from moxi import files; "
            "ctx = files.lock(sys.argv[1]); ctx.__enter__(); print('held', flush=True); "
            "time.sleep(30)")
    holder = subprocess.Popen([sys.executable, "-c", code, str(path)],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    return holder


def test_with_the_conversations_lock_held_elsewhere_a_call_answers_within_its_budget():
    a_recall("a", 1, "a point")
    holder = hold_in_another_process(recalls.subconscious_dir() / recalls.LOCKS
                                     / f"{KEY}.lock")
    try:
        began = time.monotonic()
        got = collect(has_words=True, deadline=time.monotonic() + 0.5)
        assert time.monotonic() - began < 2
        assert got.pieces == [] and not got.held and got.call is None and got.notes
    finally:
        holder.kill()
        holder.wait()


# -- the start ----------------------------------------------------------------------------

def chat_settings(**over):
    read = memory_settings.read()
    out = {name: read[name] for name in memory_settings.schema().keys}
    out.update(over)
    return out


def start(conv=CONV, words="the kiosk approver", **kw):
    kw.setdefault("spawn", lambda session_id, recall_id: 4242)
    return recall_chat.start(conv, words, "kiosk talk", call=1,
                             settings=kw.pop("settings", chat_settings()), **kw)


def test_running_max_two_a_third_start_hurries_the_oldest_only():
    for _ in range(3):
        record, skip = start()
        assert skip is None and record["start"] == "chat" and record["route"] == "headless"
    assert states() == [records.HURRIED, records.STARTING, records.STARTING]
    assert records.session_records(KEY)[-1]["context"] == "kiosk talk"


def test_at_the_cap_over_every_conversation_a_start_is_busy():
    for i in range(recall_chat.CHATS_RUNNING_MAX):
        assert start(conv=f"other-label-{i}")[1] is None
    record, skip = start()
    assert record is None and skip.reason == recall_chat.BUSY


def test_two_calls_on_two_labels_released_together_start_exactly_one():
    for i in range(recall_chat.CHATS_RUNNING_MAX - 1):
        start(conv=f"other-label-{i}")
    gate = threading.Barrier(2)
    out = []

    def go(conv):
        gate.wait()
        out.append(start(conv=conv))

    threads = [threading.Thread(target=go, args=(c,)) for c in ("first-label", "second-label")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(skip is None for _, skip in out) == [False, True]
    assert [skip.reason for _, skip in out if skip] == [recall_chat.BUSY]


def test_with_the_start_lock_held_elsewhere_a_start_is_busy_within_its_budget():
    recall_chat.chat_dir().mkdir(parents=True, exist_ok=True)
    holder = hold_in_another_process(recall_chat.chat_dir() / recall_chat.START_LOCK)
    try:
        began = time.monotonic()
        record, skip = start(deadline=time.monotonic() + 0.5)
        assert time.monotonic() - began < 2
        assert record is None and skip.reason == recall_chat.BUSY
    finally:
        holder.kill()
        holder.wait()


# -- the checks ---------------------------------------------------------------------------

class CheckLauncher:
    Refusal = RuntimeError

    def __init__(self, refusals=()):
        self.refusals = list(refusals)

    def settable_rows(self, harness):
        return ()

    def LaunchRequest(self, **fields):  # noqa: N802 -- stands in for the class
        return types.SimpleNamespace(**fields)

    def check(self, req, env=None):
        return self.refusals


def with_launcher(monkeypatch, launcher):
    from moxi_memory import links

    monkeypatch.setattr(links, "launcher_headless",
                        lambda: links.Link(links.READY, launcher))


def test_each_check_gives_its_reason_in_order(monkeypatch):
    from moxi_memory import links

    on = chat_settings(**{"subconscious.chat.enabled": True})
    assert recall_chat.why_not(chat_settings()).reason == recall_chat.OFF
    monkeypatch.setattr(links, "launcher_headless",
                        lambda: links.Link(links.NONE, None, "launcher not installed"))
    assert recall_chat.why_not(on).reason == recall_chat.NO_LAUNCHER
    with_launcher(monkeypatch, CheckLauncher(["no claude on PATH"]))
    skip = recall_chat.why_not(on)
    assert skip.reason == recall_chat.NO_HARNESS and "no claude on PATH" in skip.detail
    with_launcher(monkeypatch, CheckLauncher())
    monkeypatch.setattr(recall_fence, "verdict", lambda launcher, spec, env=None:
                        recall_fence.Verdict(recall_fence.FENCE, "tools: extra Bash"))
    skip = recall_chat.why_not(on)
    assert skip.reason == recall_chat.FENCE and "tools: extra Bash" in skip.detail
    monkeypatch.setattr(recall_fence, "verdict", lambda launcher, spec, env=None:
                        recall_fence.Verdict(None))
    assert recall_chat.why_not(on) is None


class CwdLauncher(CheckLauncher):
    """Refuses a working folder that isn't there, as the launcher's check does."""

    def check(self, req, env=None):
        import os

        return [] if os.path.isdir(req.cwd) else [f"the directory {req.cwd} does not exist"]


def test_the_checks_make_the_chat_dir_before_the_first_call(monkeypatch):
    import shutil

    shutil.rmtree(recall_chat.chat_dir(), ignore_errors=True)
    with_launcher(monkeypatch, CwdLauncher())
    monkeypatch.setattr(recall_fence, "verdict", lambda launcher, spec, env=None:
                        recall_fence.Verdict(None))
    assert recall_chat.why_not(chat_settings(**{"subconscious.chat.enabled": True})) is None
    assert recall_chat.chat_dir().is_dir()


def test_a_new_check_joins_the_list_as_one_entry():
    def closed(ctx):
        return recall_chat.Skip("closed")

    checks = (*recall_chat.START_CHECKS[:1], closed)
    on = chat_settings(**{"subconscious.chat.enabled": True})
    assert recall_chat.why_not(on, checks=checks).reason == "closed"


# -- the child's chat branch --------------------------------------------------------------

def a_chat_recall():
    from moxi_memory import recall_child

    record, _ = start()
    return recall_child.Recall(records.read(KEY, record["recall"]))


def test_the_brief_is_the_preamble_then_recall_briefs_output():
    from moxi_memory import recall_brief

    recall = a_chat_recall()
    brief, _dirs = recall_chat.chat_brief(recall, search=lambda *a, **k: [],
                                          places=lambda cwd, nd: (None, None))
    head = recall_chat.preamble("kiosk talk")
    assert brief.startswith(head) and "no shell" in head and "kiosk talk" in head
    found = recall_brief.prefetch("the kiosk approver", session_id=KEY, cwd=None,
                                  notes_dir=None, scope=recall_brief.EVERYWHERE,
                                  hits=8, search=lambda *a, **k: [],
                                  places=lambda cwd, nd: (None, None))
    assert brief[len(head):] == recall_brief.brief(
        start=recalls.CHAT, text="the kiosk approver", session_id=KEY, found=found,
        settings=recall.settings)


@pytest.mark.parametrize("verdict, reason", [
    (recall_fence.Verdict(recall_fence.FENCE, "tools: extra Bash"), "fence"),
    (recall_fence.Verdict(recall_fence.NO_OUTCOME, "no system/init: it exited"), "gone"),
])
def test_a_fence_that_fails_or_gives_no_outcome_ends_the_recall_lost(verdict, reason):
    recall = a_chat_recall()
    seen = []

    def ensure(launcher, spec):
        seen.append((records.read(KEY, recall.recall_id).get("headless_session"),
                     spec.session_id, spec.recall_id))
        return verdict

    assert recall_chat.prepare(recall, launcher=CheckLauncher(), ensure=ensure) is None
    [(written, minted, stamped)] = seen
    assert written == minted and stamped == recall.recall_id   # minted before the fence
    line = recalls.find_line(KEY, recall.recall_id)
    assert line["state"] == recalls.LOST and line["reason"] == reason
    assert line["headless_session"] == minted and line["call"] == 1
    assert records.read(KEY, recall.recall_id)["state"] == records.LOST


def test_a_passed_fence_gives_the_headless_route_with_the_notes_dirs(monkeypatch):
    from moxi_memory import recall_route

    recall = a_chat_recall()
    monkeypatch.setattr(recall_chat, "chat_brief",
                        lambda recall, **kw: ("the brief", ["/notes"]))
    brief, route = recall_chat.prepare(
        recall, launcher=CheckLauncher(),
        ensure=lambda launcher, spec: recall_fence.Verdict(None))
    assert brief == "the brief" and isinstance(route, recall_route.Headless)
    assert route.spec.add_dirs == ("/notes",) and route.spec.name == f"recall-{recall.recall_id}"
    assert route.session == recalls.find_line(KEY, recall.recall_id)["headless_session"]
