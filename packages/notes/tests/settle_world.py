"""A scheduler that drives the real settle steps through a simulated notes repo.

Every process is a generator. At each instant the scheduler runs one ready step at a time,
picked by `pick` (at random by default), so a holder's last read, its release, a
newcomer's raise and its lock try are separate steps and every order of steps due at one
instant can come up. Times are seconds on a 30-second grid, so ties are common.

A pass here follows repo's committer: `busy` without the lock; `throttled` within the
interval of this author's last commit; `index-busy` while git's index lock is held; else
it commits what is unsaved, or is `clean`. The push part runs on every pass that took the
lock, and a push is skipped inside `push_backoff_s` of the last failed one.

`judge(world)` checks the session-end bound as the settle steps state it: each edit a
session made before its `gone` is committed by `gone` + interval + 6 minutes, unless a
settle give-up, or an `error` pass that started at or after the `gone`, fell in that
window; and each commit a `--settle` pass left unpushed sees a push attempt start after
it and by commit + backoff + 6 minutes, unless a give-up fell in that window.
"""

from __future__ import annotations

import random

from moxi_notes import settle as steps

MIN = 60.0
NOT_COVERING = steps.NOT_COVERING


class Res:
    __slots__ = ("status", "start_seq", "end_seq", "start_t", "end_t", "committed",
                 "pushed", "push_error", "attempted", "held_lock")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class World:
    def __init__(self, rng, interval, backoff, p_err=0.0, durations=(0.0, 30.0, 60.0),
                 hangs=(0.0,), pick=None):
        self.rng, self.I, self.K, self.p_err = rng, interval, backoff, p_err
        self.durations, self.hangs = durations, hangs
        self.pick = pick or rng.choice
        self.now = 0.0
        self.seq = 0
        self.edits = {}          # edit id -> (time, seq, session)
        self.committed_at = {}   # edit id -> commit time
        self.repo_lock = None
        self.index_ext = False
        self.last_commit = None
        self.ahead = False
        self.last_fail = None
        self.remote_up = False
        self.attempts = []       # (start time, seq)
        self.settle_lock = None
        self.gen = "g1"
        self.asked = 0
        self.gone = []           # (session, time, seq)
        self.giveups = []        # (time, text)
        self.errors = []         # (start time, end time) of each error pass
        self.push_obl = []       # (commit time, end seq) of each unpushed --settle commit
        self.passes = []         # (pid, settle, status, start, end)
        self.procs = []
        self.pid = 0
        self.records_cleared = 0

    def tick(self):
        self.seq += 1
        return self.seq

    def spawn(self, gen, at=None):
        self.pid += 1
        self.procs.append([gen, self.now if at is None else at, self.pid])
        return self.pid

    def settler(self, name=None, at=None):
        """Start one `--settle` process running the real steps."""
        ops = Ops(self, name)
        pid = self.spawn(steps.settle(ops), at)
        ops.pid = pid
        return ops

    def one(self, at=None):
        """Start one pass that takes no duty (the timer, `moxi notes snapshot`)."""
        ops = Ops(self, None, settle_pass=False)
        ops.pid = self.spawn(steps.one(ops), at)
        return ops


def run_pass(w, pid, settle_pass):
    """One committer pass: lock, cadence, dirty, commit, push part, release."""
    yield None
    if w.repo_lock is not None:
        s = w.tick()
        w.passes.append((pid, settle_pass, "busy", w.now, w.now))
        return Res(status="busy", start_seq=s, end_seq=s, start_t=w.now, end_t=w.now,
                   committed=False, pushed=not w.ahead, push_error=False, attempted=False,
                   held_lock=False)
    w.repo_lock = pid
    start_seq, start_t = w.tick(), w.now
    snapshot = None
    if w.last_commit is not None and w.now - w.last_commit < w.I:
        status = "throttled"
    elif w.index_ext:
        status = "index-busy"
    elif w.p_err and w.rng.random() < w.p_err:
        status = "error"
    else:
        snapshot = [e for e in w.edits if e not in w.committed_at]
        status = "committing" if snapshot else "clean"
    dur = w.rng.choice(w.durations)
    yield (w.now + dur) if dur else None
    if status == "committing":
        for e in snapshot:
            w.committed_at[e] = w.now
        w.last_commit = w.now
        w.ahead = True
        status = "committed"
    if status == "error":
        w.errors.append((start_t, w.now))
    attempted = failed = False
    if w.ahead and (w.last_fail is None or w.now - w.last_fail >= w.K):
        attempted = True
        a_start = w.now
        if w.remote_up:
            w.ahead, w.last_fail = False, None
        else:
            hang = w.rng.choice(w.hangs)
            if hang:
                yield w.now + hang
            w.last_fail, failed = w.now, True
        w.attempts.append((a_start, w.tick()))
    end_seq = w.tick()
    w.repo_lock = None
    w.passes.append((pid, settle_pass, status, start_t, w.now))
    r = Res(status=status, start_seq=start_seq, end_seq=end_seq, start_t=start_t,
            end_t=w.now, committed=status == "committed", pushed=not w.ahead,
            push_error=failed, attempted=attempted, held_lock=True)
    if settle_pass and r.committed and not r.pushed:
        w.push_obl.append((w.now, end_seq))
    return r


class Ops:
    """The settle steps' ops over the world: its clock, its passes, its counter and lock."""

    def __init__(self, world, name, settle_pass=True):
        self.w, self.name, self.settle_pass = world, name, settle_pass
        self.pid = None
        self.raised = None
        self.held = False

    def now(self):
        return self.w.now

    def run_pass(self):
        r = yield from run_pass(self.w, self.pid, self.settle_pass)
        return steps.Pass(status=r.status, start=r.start_t, end=r.end_t,
                          held_lock=r.held_lock, unpushed=r.committed and not r.pushed,
                          interval=self.w.I, backoff=self.w.K)

    def read(self):
        return (self.w.gen, self.w.asked)

    def raise_request(self):
        self.w.asked += 1
        self.raised = (self.w.gen, self.w.asked)
        return self.raised

    def try_lock(self):
        if self.w.settle_lock is not None:
            return False
        self.w.settle_lock = self.pid
        self.held = True
        return True

    def release(self):
        if self.held:
            self.w.settle_lock = None
            self.held = False

    def last_commit(self):
        return self.w.last_commit

    def give_up(self, text):
        self.w.giveups.append((self.w.now, text))

    def clear_record(self):
        self.w.records_cleared += 1


def simulate_world(w, limit=200000):
    """Run the world's processes to their end."""
    steps_run = 0
    while w.procs:
        steps_run += 1
        if steps_run > limit:
            raise RuntimeError("the world did not end")
        t = min(p[1] for p in w.procs)
        w.now = t
        ready = [p for p in w.procs if p[1] == t]
        p = w.pick(ready)
        try:
            v = next(p[0])
        except StopIteration:
            w.procs.remove(p)
            continue
        if v is not None:
            if v < w.now:
                raise RuntimeError("time went back")
            p[1] = v
    if w.settle_lock is not None:
        raise RuntimeError("a settler ended holding the settle lock")
    return w


# --- the random environment -------------------------------------------------------------

def _q(rng, lo, hi):
    return round(rng.uniform(lo, hi) * 2) / 2 * MIN


def _session(w, sess, rng):
    t = _q(rng, 0, 40)
    yield t
    end = t + _q(rng, 0, 30)
    events = sorted([(_q(rng, t / MIN, end / MIN), "edit") for _ in range(rng.randint(0, 4))]
                    + [(_q(rng, t / MIN, end / MIN), "turn")
                       for _ in range(rng.randint(0, 3))])
    eid = 0
    for at, kind in events:
        yield max(w.now, at)
        if kind == "edit":
            eid += 1
            w.edits[(sess, eid)] = (w.now, w.tick(), sess)
        else:
            w.settler(("turn", sess))
    yield max(w.now, end)
    if rng.random() < 0.5:  # a last edit at the gone instant, ordered before it
        eid += 1
        w.edits[(sess, eid)] = (w.now, w.tick(), sess)
    yield None
    w.gone.append((sess, w.now, w.tick()))
    w.settler(sess)


def _remote(w, rng):
    w.remote_up = rng.random() < 0.3
    for _ in range(rng.randint(0, 4)):
        yield w.now + _q(rng, 1, 20)
        w.remote_up = not w.remote_up


def _hand_pass(w, rng):
    yield _q(rng, 0, 60)
    yield from run_pass(w, 0, False)


def _ext_lock(w, rng, longest):
    yield _q(rng, 0, 60)
    if w.repo_lock is None:
        w.repo_lock = "outside"
        yield w.now + _q(rng, 1, longest)
        w.repo_lock = None


def _index_lock(w, rng, longest):
    yield _q(rng, 0, 60)
    w.index_ext = True
    yield w.now + _q(rng, 1, longest)
    w.index_ext = False


def simulate(seed, interval_min, backoff_min, *, p_err=0.02, longest_hold=6, hang=0):
    """One random run: 1-4 sessions with edits, turns and a `gone`, a remote that goes up
    and down, up to two hand passes and up to two outside holds of each lock."""
    rng = random.Random(seed)
    hangs = (0.0, 30.0, 60.0, 90.0, 120.0) if hang else (0.0,)
    w = World(rng, interval_min * MIN, backoff_min * MIN, p_err, hangs=hangs)
    for s in range(rng.randint(1, 4)):
        w.spawn(_session(w, s, rng))
    w.spawn(_remote(w, rng))
    for _ in range(rng.randint(0, 2)):
        w.spawn(_hand_pass(w, rng))
    for _ in range(rng.randint(0, 2)):
        w.spawn(_ext_lock(w, rng, longest_hold))
    for _ in range(rng.randint(0, 2)):
        w.spawn(_index_lock(w, rng, longest_hold))
    return simulate_world(w)


def judge(w):
    """`(late edits, exempt edits, worst commit lateness, push misses)`."""
    late, exempt, worst = [], 0, 0.0
    for sess, tg, gseq in w.gone:
        dl = tg + w.I + 6 * MIN
        ex = (any(tg <= t <= dl for t, _ in w.giveups)
              or any(tg <= end <= dl and start >= tg for start, end in w.errors))
        for e, (et, esq, es) in w.edits.items():
            if es != sess or esq > gseq:
                continue
            c = w.committed_at.get(e)
            if c is not None and c <= dl:
                worst = max(worst, c - tg)
                continue
            if ex:
                exempt += 1
                continue
            late.append((sess, tg, e))
    misses = 0
    for ct, cseq in w.push_obl:
        dl = ct + w.K + 6 * MIN
        if any(cseq < sq and t <= dl for t, sq in w.attempts):
            continue
        if any(ct <= t <= dl for t, _ in w.giveups):
            continue
        misses += 1
    return late, exempt, worst, misses


#: The scenarios: (longest outside hold in minutes, error rate, push hang, interval
#: minutes, backoff minutes).
SCENARIOS = ((6, 0.02, 0, 15, 5), (6, 0.02, 0, 5, 5), (6, 0.02, 0, 2, 5), (6, 0.02, 0, 30, 5),
             (3, 0.02, 0, 15, 5), (3, 0.02, 0, 2, 5), (3, 0.0, 0, 15, 5), (3, 0.0, 2, 15, 5),
             (3, 0.0, 2, 5, 5))


def sweep(runs, base=1):
    """Run `runs` seeds per scenario; `(scenario, gone events, late, misses, worst, bound)`
    per scenario."""
    out = []
    for hold, p_err, hang, interval, backoff in SCENARIOS:
        gones = late_n = misses = 0
        worst = 0.0
        for i in range(runs):
            seed = (base * 100_000_000 + hold * 10_000_000 + int(p_err * 100) * 1_000_000
                    + hang * 3_000_000 + interval * 100_000 + i)
            w = simulate(seed, interval, backoff, p_err=p_err, longest_hold=hold, hang=hang)
            late, _, wst, pm = judge(w)
            gones += len(w.gone)
            late_n += len(late)
            misses += pm
            worst = max(worst, wst)
        out.append(((hold, p_err, hang, interval, backoff), gones, late_n, misses, worst,
                    (interval + 6) * MIN))
    return out
