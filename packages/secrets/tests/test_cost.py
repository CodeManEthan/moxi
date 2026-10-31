"""Cost, the slow tier: the budgets of the design's cost table, each held at three times its
value so a slower runner does not flake, on adversarial shapes generated here at test time
and on every rule of the pinned sets.

On RE2 each set's scan of each shape must take under three times its budget per MB of the
text's UTF-8 plus its budget per candidate match. A candidate is one match of a rule's
pattern before entropy and allowlists, one occurrence of a private name before its
neighbour check, or one personal-data validator check (a card window, an IBAN reading, an
SSN shape); an untimed run counts them. When a gitleaks-format set misses its budget, the failure names the slowest rules,
so the bump can drop them with their cost as the reason.

On the fallback the test holds the deadline's promise instead: each shape comes back,
scanned or unscanned, within its deadline plus the kill time (also at three times), and
never clean for a set where RE2 finds a hit.

Run with `pytest -m slow tests/test_cost.py`. Each run prints its numbers (`-s` shows
them). The timed tests are marked `timed`, so `moxi-dev check` runs them alone, after its
parallel pass.
"""

from __future__ import annotations

import base64
import importlib
import json
import string
import subprocess
import sys
import time

import corpus
import pytest

from moxi_secrets import engine, fallback, redact, scan

pytestmark = pytest.mark.slow

scan_mod = importlib.import_module("moxi_secrets.scan")
names_mod = importlib.import_module("moxi_secrets.names")
personal_mod = importlib.import_module("moxi_secrets.personal")

MB = 1_000_000
SIZE = MB
SLACK = 3.0
#: Budgets: (seconds per MB, seconds per candidate).
GITLEAKS_BUDGET = (0.4, 50e-6)
PERSONAL_BUDGET = (0.1, 10e-6)
#: Private names on RE2: per MB, per MB for each entry the text holds, per candidate.
NAMES_BUDGET = (0.05, 0.001, 5e-6)
#: Private names on the fallback: per MB for each entry on the list, per candidate.
NAMES_FALLBACK_BUDGET = (0.005, 5e-6)
#: The fallback returns by its deadline plus this.
KILL_TIME = 0.1


# -- counting candidates --------------------------------------------------------------------

class _Counted:
    def __init__(self, rx, counter):
        self.rx, self.counter = rx, counter
        self.groups = rx.groups

    def finditer(self, target, *args):
        for m in self.rx.finditer(target, *args):
            self.counter.count += 1
            yield m

    def search(self, target, *args):
        m = self.rx.search(target, *args)
        # A position marks the private-names occurrence loop; a search without one is an
        # allowlist's check of a finding already counted.
        if m is not None and args:
            self.counter.count += 1
        return m


class Counting:
    """An engine that counts candidate matches, for an untimed run."""

    def __init__(self, inner):
        self.inner, self.count = inner, 0
        self.name, self.key = inner.name, inner.key

    def compile(self, pattern):
        return _Counted(self.inner.compile(pattern), self)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def forget_counting():
    """Drop the caches a counting run filled (compiled names, stopword indexes), so the
    timed run compiles with the real engine."""
    scan_mod._sets = None
    names_mod._compiled = None


def re2_engine():
    return engine.Re2Engine(engine.re2_module())


def best_of(n, fn):
    times = []
    for _ in range(n):
        started = time.perf_counter()
        fn()
        times.append(time.perf_counter() - started)
    return min(times)


def size_mb(text):
    return len(text.encode("utf-8", "surrogateescape")) / MB


# -- the shapes -----------------------------------------------------------------------------

LOWER = string.ascii_lowercase
B64 = string.ascii_letters + string.digits + "+/"


def keywords():
    return scan_mod.load_sets().keywords


def prose(r, n):
    out, size = [], 0
    while size < n:
        word = corpus.chars(r, r.randrange(2, 10), LOWER)
        out.append(word)
        size += len(word) + 1
    return " ".join(out)[:n]


def fill(r, n, make, sep="\n"):
    out, size = [], 0
    while size < n:
        piece = make(r)
        out.append(piece)
        size += len(piece) + len(sep)
    return sep.join(out)[:n]


def markdown_image(r):
    kw = sorted(keywords())
    a, b = r.choice(kw), r.choice(kw)
    head = f"# Notes\n\nThe {a} and the {b} are set in the dashboard.\n\n"
    data = base64.b64encode(r.randbytes(SIZE * 3 // 4)).decode()
    return (head + "![chart](data:image/png;base64," + data + ")\n")[:SIZE]


def hex_line(r):
    return "key " + corpus.chars(r, SIZE - 4, "0123456789abcdef")


def jsonl_curl(r):
    def line(r):
        cmds = []
        while sum(map(len, cmds)) < 20_000:
            cmds.append(corpus.curl_user(r) + " -H " + json.dumps(corpus.bearer(r)))
        return json.dumps({"cmd": " && ".join(cmds), "id": r.randrange(10**9)})
    return fill(r, SIZE, line)


def keyword_repeats(r):
    kw = sorted(keywords())
    return fill(r, SIZE, lambda r: r.choice(kw), sep=" ")


def word_runs(r):
    kw = sorted(keywords())
    return fill(r, SIZE, lambda r: corpus.chars(r, r.randrange(1_000, 50_000)) + " " +
                r.choice(kw) + " = ", sep=" ")


def base64_wrapped(r):
    data = base64.b64encode(r.randbytes(SIZE * 3 // 4)).decode()
    return "\n".join(data[i:i + 76] for i in range(0, len(data), 76))[:SIZE]


def base64_unwrapped(r):
    return ("token: " + base64.b64encode(r.randbytes(SIZE * 3 // 4)).decode())[:SIZE]


def every_keyword(r):
    kw = sorted(keywords())
    return fill(r, SIZE, lambda r: " ".join(
        f"{k}={corpus.chars(r, r.randrange(8, 48), corpus.URLSAFE)}" for k in kw))


def html_page(r):
    def block(r):
        attrs = " ".join(f'{corpus.chars(r, 5, LOWER)}="{corpus.chars(r, 12)}"'
                         for _ in range(r.randrange(1, 5)))
        kind = r.randrange(5)
        if kind == 0:
            return f"<script>var {r.choice(('apiKey', 'token', 'auth'))} = " \
                   f"'{corpus.chars(r, 20)}'; fetch('/x?id={r.randrange(10**6)}');</script>"
        if kind == 1:
            return f'<a href="https://example.test/{corpus.chars(r, 30, LOWER)}" {attrs}>' \
                   f"{prose(r, 60)}</a>"
        if kind == 2:
            return f"<style>.{corpus.chars(r, 6, LOWER)}{{color:#{corpus.chars(r, 6, '0123456789abcdef')}}}</style>"
        return f"<div {attrs}><p>{prose(r, r.randrange(50, 400))}</p></div>"
    return "<!doctype html><html><body>" + fill(r, SIZE - 40, block) + "</body></html>"


def hit_dense(r):
    return fill(r, SIZE, lambda r: r.choice(corpus.CREDENTIALS)[0](r))


def stopword_dense(r):
    stops = sorted(next(a["stopwords"] for rule in
                        scan_mod.load_sets().data["sets"]["broad"]["rules"]
                        for a in rule["allowlists"] if a["stopwords"]))
    return fill(r, SIZE, lambda r: f"api_key = {r.choice(stops)}{corpus.chars(r, 12)}")


SHAPES = {
    "markdown with an image": markdown_image,
    "hex on one line": hex_line,
    "jsonl of curl commands": jsonl_curl,
    "keyword repeats": keyword_repeats,
    "word runs": word_runs,
    "base64 wrapped": base64_wrapped,
    "base64 unwrapped": base64_unwrapped,
    "every keyword at once": every_keyword,
    "an html page": html_page,
    "hit dense": hit_dense,
    "stopword dense": stopword_dense,
}

_texts: dict = {}


def shape(name):
    if name not in _texts:
        _texts[name] = SHAPES[name](corpus.rng(len(name)))
    return _texts[name]


# -- the gitleaks-format sets ---------------------------------------------------------------

def slowest_rules(text, set_name, eng, n=5):
    """The `n` slowest rules of a set on `text`, timed one at a time."""
    data = scan_mod.load_sets().data
    t = engine.Text(text)
    times = []
    for rule in data["sets"][set_name]["rules"]:
        one = dict(data, sets={k: dict(v, rules=[rule] if k == set_name else [])
                               for k, v in data["sets"].items()})
        sets = scan_mod.Sets(one)
        real = scan_mod._sets
        scan_mod._sets = sets
        try:
            started = time.perf_counter()
            scan_mod.run_gitleaks(t, (set_name,), None, eng)
            times.append((time.perf_counter() - started, rule["id"]))
        finally:
            scan_mod._sets = real
    return sorted(times, reverse=True)[:n]


@pytest.fixture(scope="module")
def re2_hits():
    """RE2's hits per shape and set, for the fallback's comparison."""
    return {}


@pytest.mark.parametrize("set_name", ["credentials", "broad"])
@pytest.mark.parametrize("name", list(SHAPES))
@pytest.mark.timed
def test_re2_holds_the_budget(name, set_name, re2_hits, monkeypatch):
    monkeypatch.setattr(engine, "_active", None)
    eng = re2_engine()
    text = shape(name)
    counter = Counting(eng)
    forget_counting()
    scan_mod.run_gitleaks(engine.Text(text), (set_name,), None, counter)
    forget_counting()
    mb = size_mb(text)
    budget = SLACK * (GITLEAKS_BUDGET[0] * mb + GITLEAKS_BUDGET[1] * counter.count)
    hits = []
    took = best_of(2, lambda: hits.append(scan(text, (set_name,))))
    re2_hits[(name, set_name)] = {(h.rule, h.start, h.end) for h in hits[-1]}
    print(f"\nre2 {set_name} {name}: {took:.3f} s for {mb:.2f} MB, {counter.count} "
          f"candidates, {len(hits[-1])} hits, budget {budget:.3f} s")
    if took > budget:
        pytest.fail(f"{set_name} on {name}: {took:.3f} s, budget {budget:.3f} s; slowest "
                    f"rules: {slowest_rules(text, set_name, eng)}")


@pytest.mark.parametrize("name", list(SHAPES))
@pytest.mark.timed
def test_the_fallback_returns_by_its_deadline_and_never_clean(name, re2_hits,
                                                              fallback_engine):
    text = shape(name)
    sets = ("credentials", "broad")
    wait = fallback.deadline(len(text.encode("utf-8")))
    started = time.monotonic()
    hits = scan(text, sets)
    took = time.monotonic() - started
    unscanned = {h.set for h in hits if h.rule == "unscanned"}
    print(f"\nfallback {name}: {took:.3f} s, deadline {wait:.2f} s, "
          f"unscanned {sorted(unscanned)}, {len(hits)} hits")
    assert took <= wait + SLACK * KILL_TIME
    for set_name in sets:
        if set_name in unscanned:
            continue
        found = {(h.rule, h.start, h.end) for h in hits if h.set == set_name}
        expected = re2_hits.get((name, set_name))
        if expected is None:
            pytest.skip("RE2's run of this shape did not run first")
        assert expected <= found, (set_name, len(expected - found))


# -- private names --------------------------------------------------------------------------

def made_up_entries(r, n=203):
    out = []
    for i in range(n):
        kind = i % 4
        if kind == 0:
            out.append(corpus.chars(r, r.randrange(4, 10), LOWER))
        elif kind == 1:
            out.append(corpus.chars(r, 5, LOWER).title() + " " + corpus.chars(r, 6, LOWER))
        elif kind == 2:
            out.append(corpus.chars(r, 6, LOWER) + "-" + corpus.chars(r, 3, string.digits))
        else:
            out.append("/home/" + corpus.chars(r, 7, LOWER))
    return out


def names_shapes(entries):
    r = corpus.rng(50)
    some = entries[::5]
    yield "prose with entries", fill(r, SIZE, lambda r: prose(r, 200) + " " +
                                     r.choice(some), sep=" ")
    near = entries[0]
    yield "near misses at every position", (near * (SIZE // len(near) + 1))[:SIZE]
    yield "every entry present", fill(r, SIZE, lambda r: " ".join(entries), sep=" ")
    yield "hits glued in a run", fill(r, SIZE, lambda r: r.choice(entries), sep=" ")


@pytest.mark.parametrize("eng_name", ["re2", "re"])
@pytest.mark.timed
def test_private_names_hold_the_budget(eng_name, names_config):
    entries = made_up_entries(corpus.rng(49))
    names_config(words=entries[:150], anywhere=entries[150:180], emails=[
        f"{e}@example.test" for e in entries[180:]])
    listed = names_mod.private_names()
    eng = re2_engine() if eng_name == "re2" else engine.python()
    for label, text in names_shapes(entries):
        counter = Counting(eng)
        forget_counting()
        names_mod.spans(engine.Text(text), listed, counter)
        forget_counting()
        lower = text.lower()
        held = sum(1 for e in entries if e.lower() in lower)
        mb = size_mb(text)
        if eng_name == "re2":
            base, per_entry, per_candidate = NAMES_BUDGET
            budget = SLACK * (base * mb + per_entry * mb * held + per_candidate * counter.count)
        else:
            per_entry, per_candidate = NAMES_FALLBACK_BUDGET
            budget = SLACK * (per_entry * mb * len(entries) + per_candidate * counter.count)
        took = best_of(2, lambda: names_mod.spans(engine.Text(text), listed, eng))
        print(f"\n{eng_name} private names {label}: {took:.3f} s, {counter.count} "
              f"candidates, {held} entries held, budget {budget:.3f} s")
        assert took <= budget, label


# -- personal data --------------------------------------------------------------------------

def personal_shapes():
    r = corpus.rng(60)
    yield "prose with numbers", fill(r, SIZE, lambda r: prose(r, 80) + " " + str(
        r.randrange(10**r.randrange(1, 20))), sep=" ")
    yield "cards everywhere", fill(r, SIZE, lambda r: corpus.grouped(corpus.card(r)),
                                   sep=" ")
    yield "digit groups in a chain", fill(r, SIZE, lambda r: corpus.chars(
        r, r.choice((4, 4, 4, 6, 5, 15, 16)), string.digits), sep=r.choice(" -"))
    yield "iban-like groups", fill(r, SIZE, lambda r: corpus.grouped(
        corpus.iban(r, "DE", 22)), sep=" ")
    yield "dashed numbers", fill(r, SIZE, lambda r: f"{r.randrange(1000):03d}-"
                                 f"{r.randrange(100):02d}-{r.randrange(10000):04d}", sep=" ")


def personal_candidates(text, eng, monkeypatch) -> int:
    """The validator checks a personal-data scan makes: each card window, IBAN reading
    and SSN shape, the unit the cost of the personal-data set is stated in."""
    luhn_ok, iban_ok, ssn_ok = personal_mod._validators()
    calls = [0]

    def counted(fn):
        def wrapped(*args):
            calls[0] += 1
            return fn(*args)
        return wrapped
    with monkeypatch.context() as m:
        m.setattr(personal_mod, "_is_card", counted(personal_mod._is_card))
        m.setattr(personal_mod, "_stdnum", (luhn_ok, counted(iban_ok), counted(ssn_ok)))
        m.setattr(engine, "_active", eng)
        personal_mod.spans(engine.Text(text))
    return calls[0]


@pytest.mark.parametrize("eng_name", ["re2", "re"])
@pytest.mark.timed
def test_personal_data_holds_the_budget(eng_name, monkeypatch):
    eng = re2_engine() if eng_name == "re2" else engine.python()
    for label, text in personal_shapes():
        candidates = personal_candidates(text, eng, monkeypatch)
        monkeypatch.setattr(engine, "_active", eng)
        mb = size_mb(text)
        budget = SLACK * (PERSONAL_BUDGET[0] * mb + PERSONAL_BUDGET[1] * candidates)
        took = best_of(2, lambda: personal_mod.spans(engine.Text(text)))
        print(f"\n{eng_name} personal data {label}: {took:.3f} s, {candidates} "
              f"candidates, budget {budget:.3f} s")
        assert took <= budget, label


# -- redact ---------------------------------------------------------------------------------

def test_redact_scans_once_without_hits_and_twice_with_them(monkeypatch):
    real = scan_mod.scan
    seen = [0]

    def counting(*args, **kwargs):
        seen[0] += 1
        return real(*args, **kwargs)
    monkeypatch.setattr(scan_mod, "scan", counting)
    for text, scans in ((prose(corpus.rng(70), SIZE), 1), (shape("hit dense"), 2)):
        seen[0] = 0
        redact(text, ("credentials",))
        assert seen[0] == scans


# -- the import, the first scan, a warm short text ------------------------------------------

_CHILD = r"""
import json, sys, time
started = time.perf_counter()
import moxi_secrets
imported = time.perf_counter() - started
text = sys.argv[1]
started = time.perf_counter()
moxi_secrets.scan(text, ("credentials",))
first = time.perf_counter() - started
print(json.dumps({"import": imported, "first": first}))
"""


def child_times(text, hide_re2=False):
    code = _CHILD
    if hide_re2:
        code = "import sys; sys.modules['re2'] = None\n" + code
    out = subprocess.run([sys.executable, "-c", code, text], capture_output=True,
                         text=True, check=True, timeout=60)
    return json.loads(out.stdout)


@pytest.mark.timed
def test_the_import_and_the_first_scan_hold_their_budgets():
    text = prose(corpus.rng(80), 4_000)
    runs = [child_times(text) for _ in range(5)]
    imported = min(r["import"] for r in runs)
    first = min(r["first"] for r in runs)
    print(f"\nimport {imported * 1000:.1f} ms, first 4 KB credentials scan "
          f"{first * 1000:.1f} ms")
    assert imported <= SLACK * 0.010
    assert first <= SLACK * 0.010
    runs = [child_times(text, hide_re2=True) for _ in range(5)]
    first = min(r["first"] for r in runs)
    print(f"first 4 KB fallback scan, the child's start included: {first * 1000:.1f} ms")
    assert first <= SLACK * 0.050


@pytest.mark.parametrize("set_name", ["credentials", "broad", "private-names",
                                      "personal-data"])
@pytest.mark.timed
def test_a_warm_short_text_holds_its_budget(set_name, names_config, monkeypatch):
    monkeypatch.setattr(engine, "_active", None)
    names_config(words=made_up_entries(corpus.rng(90), 40))
    r = corpus.rng(91)
    text = prose(r, 150) + " " + corpus.github(r)
    assert len(text) <= 200
    for _ in range(50):
        scan(text, (set_name,))
    n = 500
    took = best_of(3, lambda: [scan(text, (set_name,)) for _ in range(n)]) / n
    print(f"\nwarm short text, {set_name}: {took * 1e6:.0f} us")
    assert took <= SLACK * 100e-6
