"""The settle steps, driven through a simulated notes repo: each case below scripts the
order of passes, requests and lock tries, and a seed sweep over random runs checks the
session-end bound as the steps state it."""

from __future__ import annotations

import random

import pytest

from moxi_notes import settle as steps

import settle_world as sw
from settle_world import MIN, Ops, World, run_pass, simulate_world

I = 15 * MIN
K = 5 * MIN


def world(*, remote_up=True, **kw) -> World:
    """A world whose passes take no time, whose steps run in start order, and whose
    remote takes every push unless `remote_up` is False."""
    w = World(random.Random(0), kw.pop("interval", I), kw.pop("backoff", K),
              durations=(0.0,), pick=lambda ready: ready[0], **kw)
    w.remote_up = remote_up
    return w


def at(w, t, fn):
    """Run `fn(w)` at time `t`, as a process of its own."""
    def proc():
        yield t
        fn(w)
    w.spawn(proc())


def edit(w, name):
    w.edits[name] = (w.now, w.tick(), name)


def committed_once(w):
    """A first commit at time 0 by a pass that is not a settler's."""
    def proc():
        edit(w, "first")
        yield from run_pass(w, 0, False)
    w.spawn(proc())


def settle_passes(w, ops):
    return [(status, start) for pid, settle, status, start, end in w.passes
            if pid == ops.pid]


class Hooked(Ops):
    """Ops whose steps call a hook at one point: before or after `release`, after a
    pass, or at `give_up`."""

    def __init__(self, world, name, **hooks):
        super().__init__(world, name)
        self.hooks = hooks

    def _call(self, point):
        hook = self.hooks.pop(point, None)
        if hook:
            hook(self.w)

    def release(self):
        self._call("before_release")
        super().release()
        self._call("after_release")

    def run_pass(self):
        p = yield from super().run_pass()
        self._call("after_pass")
        return p

    def give_up(self, text):
        super().give_up(text)
        self._call("give_up")


def hooked_settler(w, at_t, **hooks) -> Hooked:
    ops = Hooked(w, "hooked", **hooks)
    ops.pid = w.spawn(steps.settle(ops), at_t)
    return ops


def newcomer(w, expect_lock=False):
    """A process with a commit duty: its edit, its raise, its lock try, all at once."""
    ops = Ops(w, "newcomer")
    ops.pid = w.spawn(iter(()))
    edit(w, ("newcomer", w.now))
    ops.raise_request()
    assert ops.try_lock() is expect_lock
    return ops


# --- the counter -------------------------------------------------------------------------

def test_a_request_is_open_until_a_pass_covers_its_count_and_generation():
    assert steps.is_open(("g", 1), None)
    assert not steps.is_open((None, 0), None)
    assert not steps.is_open(("g", 3), ("g", 3))
    assert steps.is_open(("g", 4), ("g", 3))


def test_a_counter_below_one_read_or_a_new_generation_is_an_open_request():
    assert steps.is_open(("g", 2), ("g", 3))
    assert steps.is_open(("h", 1), ("g", 1))
    assert steps.is_open((None, 0), ("g", 1))


def test_a_holder_goes_round_again_when_the_runtime_folder_is_cleared_under_it():
    w = world()
    committed_once(w)
    at(w, 30, lambda w: edit(w, "a"))

    def cleared(w):  # the folder goes, and one new request comes with its edit
        w.gen, w.asked = "g2", 1
        edit(w, "after-clear")

    holder = hooked_settler(w, 30)
    at(w, 60, lambda w: holder.hooks.update(after_pass=cleared))
    simulate_world(w)
    assert w.committed_at["a"] == I
    assert w.committed_at["after-clear"] == 2 * I


# --- the commit duty ---------------------------------------------------------------------

def test_an_edit_before_gone_inside_the_interval_is_committed_when_it_ends():
    w = world()
    committed_once(w)
    at(w, 60, lambda w: edit(w, "a"))
    ops = w.settler("gone", at=120)
    simulate_world(w)
    assert settle_passes(w, ops) == [("throttled", 120), ("committed", I)]
    assert w.committed_at["a"] == I
    assert w.settle_lock is None and w.records_cleared == 1


def test_a_throttled_push_retry_never_clears_a_request():
    # A commit's push fails; a gone pass is throttled inside push_backoff; the retry pass
    # is throttled too; the holder stays and commits the edit at the interval's end.
    w = world(remote_up=False)
    edit(w, "first")
    pusher = w.settler("committing")
    at(w, 60, lambda w: edit(w, "a"))
    holder = w.settler("gone", at=120)
    simulate_world(w)
    assert settle_passes(w, pusher) == [("committed", 0), ("throttled", K)]
    # The holder's own commit fails to push too, and it keeps that push duty.
    assert settle_passes(w, holder) == [("throttled", 120), ("committed", I),
                                        ("throttled", I + K)]
    assert w.committed_at["a"] == I


def test_one_settler_per_root_and_the_seconds_edits_land_in_the_firsts_pass():
    w = world()
    committed_once(w)
    at(w, 30, lambda w: edit(w, "a"))
    first = w.settler("one", at=30)
    at(w, 60, lambda w: edit(w, "b"))
    second = w.settler("two", at=60)
    simulate_world(w)
    assert settle_passes(w, second) == [("throttled", 60)]
    assert settle_passes(w, first) == [("throttled", 30), ("committed", I)]
    assert w.committed_at["a"] == w.committed_at["b"] == I


def test_a_request_raised_during_the_holders_last_pass_makes_it_go_round_again():
    w = world()
    committed_once(w)
    at(w, 30, lambda w: edit(w, "a"))
    holder = hooked_settler(w, 30)
    at(w, 60, lambda w: holder.hooks.update(after_pass=lambda w: newcomer(w)))
    simulate_world(w)
    assert [s for s, _ in settle_passes(w, holder)] == ["throttled", "committed", "committed"]
    assert w.committed_at["a"] == I
    assert w.committed_at[("newcomer", I)] == 2 * I


def test_a_request_between_the_holders_last_read_and_its_release_is_read_again():
    w = world()
    committed_once(w)
    at(w, 30, lambda w: edit(w, "a"))
    holder = hooked_settler(w, 30, before_release=lambda w: newcomer(w))
    simulate_world(w)
    assert [s for s, _ in settle_passes(w, holder)] == ["throttled", "committed", "committed"]
    assert w.committed_at[("newcomer", I)] == 2 * I


def test_a_third_process_that_takes_the_lock_first_covers_that_request():
    w = world()
    committed_once(w)
    at(w, 30, lambda w: edit(w, "a"))
    taken = []

    def third(w):
        ops = newcomer(w, expect_lock=True)
        first = steps.Pass("throttled", w.now, w.now, True, interval=w.I, backoff=w.K)
        ops.pid = w.spawn(steps._hold(ops, first, ops.raised, []))
        taken.append(ops)

    holder = hooked_settler(w, 30, after_release=third)
    simulate_world(w)
    assert [s for s, _ in settle_passes(w, holder)] == ["throttled", "committed"]
    assert settle_passes(w, taken[0]) == [("committed", 2 * I)]
    assert w.committed_at[("newcomer", I)] == 2 * I


def test_five_held_passes_in_a_row_give_up_with_the_settle_record():
    w = world()
    committed_once(w)

    def hold(w):
        w.index_ext = True

    at(w, 1, hold)
    at(w, 2 * I, lambda w: setattr(w, "index_ext", False))
    at(w, I, lambda w: edit(w, "a"))
    ops = w.settler("gone", at=I)
    simulate_world(w)
    assert settle_passes(w, ops) == [("index-busy", I + 60 * k) for k in range(5)]
    assert w.giveups == [(I + 240, steps.RECORD_TEXT)]
    assert "a" not in w.committed_at


def test_a_request_after_the_last_read_starts_a_new_streak_at_a_give_up():
    w = world()
    committed_once(w)
    at(w, 1, lambda w: setattr(w, "index_ext", True))
    at(w, I + 270, lambda w: setattr(w, "index_ext", False))
    at(w, I, lambda w: edit(w, "a"))
    ops = hooked_settler(w, I, give_up=lambda w: newcomer(w))
    simulate_world(w)
    statuses = [s for s, _ in settle_passes(w, ops)]
    assert statuses == ["index-busy"] * 5 + ["committed"]
    assert w.committed_at["a"] == I + 300
    assert w.committed_at[("newcomer", I + 240)] == I + 300


# --- the push duty -----------------------------------------------------------------------

def test_a_failed_push_is_tried_once_more_after_push_backoff():
    w = world(remote_up=False)
    edit(w, "a")
    ops = w.settler("gone")
    simulate_world(w)
    assert settle_passes(w, ops) == [("committed", 0), ("throttled", K)]
    assert [t for t, _ in w.attempts] == [0, K]


def test_a_busy_push_retry_runs_again_a_minute_after_it_started():
    w = world(remote_up=False)
    edit(w, "a")
    ops = w.settler("gone")

    def hold(w):
        w.repo_lock = "outside"

    at(w, K - 10, hold)
    at(w, K + 100, lambda w: setattr(w, "repo_lock", None))
    simulate_world(w)
    assert settle_passes(w, ops) == [("committed", 0), ("busy", K), ("busy", K + 60),
                                     ("throttled", K + 120)]
    assert [t for t, _ in w.attempts] == [0, K + 120]


def test_two_unpushed_commits_keep_two_due_times():
    dues: list[float] = []
    steps._discharge(dues, steps.Pass("committed", 0, 0, True, unpushed=True, backoff=K))
    steps._discharge(dues, steps.Pass("committed", 100, 100, True, unpushed=True, backoff=K))
    assert dues == [K, K + 100]
    steps._discharge(dues, steps.Pass("throttled", 200, 250, True))
    assert dues == [K, K + 100]  # a pass that ended before both clears neither
    steps._discharge(dues, steps.Pass("busy", K, K + 10, False))
    assert dues == [K, K + 100]  # a pass that held no lock clears nothing
    steps._discharge(dues, steps.Pass("throttled", K, K + 10, True))
    assert dues == [K + 100]  # the earlier is cleared, never the later


def test_a_push_the_backoff_held_gets_its_attempt_by_push_backoff_after_the_commit():
    w = world()
    w.last_fail = -100.0  # an earlier pass's push failed 100 s before
    w.ahead = True
    edit(w, "a")
    ops = w.settler("gone")
    simulate_world(w)
    assert settle_passes(w, ops) == [("committed", 0), ("throttled", K)]
    assert [t for t, _ in w.attempts] == [K]


# --- no duty -----------------------------------------------------------------------------

def test_the_timer_and_a_snapshot_by_hand_take_no_duty():
    w = world()
    committed_once(w)
    at(w, 30, lambda w: edit(w, "a"))
    throttled = w.one(at=30)
    edit(w, "b")
    w2 = world(remote_up=False)
    edit(w2, "b")
    pushing = w2.one()
    simulate_world(w)
    simulate_world(w2)
    assert settle_passes(w, throttled) == [("throttled", 30)]
    assert w.asked == 0 and "a" not in w.committed_at
    assert settle_passes(w2, pushing) == [("committed", 0)]
    assert [t for t, _ in w2.attempts] == [0]


def test_a_pass_that_is_not_held_or_throttled_takes_no_commit_duty():
    w = world()
    edit(w, "a")
    ops = w.settler("gone")
    simulate_world(w)
    assert settle_passes(w, ops) == [("committed", 0)]
    assert w.asked == 0


# --- the bound over random runs ----------------------------------------------------------

def _no_misses(runs):
    for scenario, gones, late, misses, worst, bound in sw.sweep(runs):
        assert gones > 0
        assert (scenario, late, misses) == (scenario, 0, 0)
        assert worst <= bound


def test_the_bound_holds_over_a_fixed_set_of_random_runs():
    _no_misses(100)


@pytest.mark.slow
@pytest.mark.touches("packages/notes/src/moxi_notes/settle.py")
@pytest.mark.touches("packages/notes/tests/settle_world.py")
def test_the_bound_holds_over_many_random_runs():
    _no_misses(20000)
