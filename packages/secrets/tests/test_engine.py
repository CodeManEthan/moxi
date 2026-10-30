"""The engine: compiling every pattern, the engine text, the fallback and its worker, the
wheel-fit check and the announcement's OPTIONAL."""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
import time
import warnings

import corpus
import pytest

from moxi_secrets import engine, fallback, scan

scan_mod = importlib.import_module("moxi_secrets.scan")


def every_pattern():
    sets = scan_mod.load_sets()
    out = []
    for a in sets.data["allowlists"]:
        out += a["regexes"] + a["paths"]
    for name in scan_mod.GITLEAKS_SETS:
        for r in sets.data["sets"][name]["rules"]:
            out += [p for p in (r["regex"], r["path"]) if p]
            for a in r["allowlists"]:
                out += a["regexes"] + a["paths"]
    return out


def test_every_pattern_compiles_verbatim_on_re2_and_translated_on_python_re():
    import re2

    patterns = every_pattern()
    assert len(patterns) == 319
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for p in patterns:
            re2.compile(p["pattern"])
            re.compile(p["fallback"])


def test_every_local_rule_is_in_the_shared_dialect():
    import re2

    sets = scan_mod.load_sets()
    local = [r for name in scan_mod.GITLEAKS_SETS for r in sets.data["sets"][name]["rules"]
             if r["source"] == "local"]
    assert len(local) == 12
    for r in local:
        re2.compile(r["regex"]["pattern"])
        re.compile(r["regex"]["fallback"])


@pytest.mark.parametrize("text", [
    "plain ascii text\nline two",
    "café 中文 \U0001f600 end\né",
    b"a\xffb\xc3\xa9\x80\nz".decode("utf-8", "surrogateescape"),
])
def test_byte_offsets_map_to_the_callers_str_offsets(text):
    t = engine.Text(text)
    assert len(t.etext) == len(text)
    assert "\udcff" not in t.etext
    data = t.data
    for s in range(len(text) + 1):
        b = len(t.etext[:s].encode("utf-8"))
        assert t.byte_pos(s) == b
        assert t.str_pos(b) == s
    assert data.decode("utf-8") == t.etext
    for pos in range(len(text) + 1):
        assert t.line(pos) == text.count("\n", 0, pos) + 1


def test_escaped_bytes_read_as_the_replacement_character(engine_name):
    text = b"\xff".decode("utf-8", "surrogateescape")
    assert engine.Text(text).etext == "�"
    assert scan(text, ("credentials", "broad")) == []


def test_a_path_with_escaped_bytes_scans_as_the_text_does(engine_name):
    # A file name that is not UTF-8, decoded with surrogateescape: each path pattern reads
    # the escaped byte as U+FFFD, and both engines give the same hits.
    def path(name):
        return name.encode("utf-8").replace(b"%", b"\xe9").decode("utf-8", "surrogateescape")

    sets = ("credentials", "broad")
    assert [h.rule for h in scan("hello", sets, path=path("keys/caf%.p12"))] == [
        "pkcs12-file"]
    token = corpus.github(corpus.rng(12))
    assert [h.rule for h in scan(token, ("credentials",), path=path("src/caf%.py"))] == [
        "github-pat"]
    # A global path allowance (images) still skips the text.
    assert scan(token, sets, path=path("img/caf%.png")) == []


def test_a_first_scan_on_re2_reads_no_distribution_metadata():
    # Reading google-re2's version through importlib.metadata costs about 20 ms, twice the
    # first scan's whole budget; only the doctor asks for the version.
    code = ("import sys, moxi_secrets\n"
            "from moxi_secrets import engine\n"
            "moxi_secrets.scan('hello there', ('credentials', 'broad'))\n"
            "print(engine.active().name, 'importlib.metadata' in sys.modules)\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True, timeout=60)
    assert out.stdout.split() == ["re2", "False"]


def test_the_fallback_gives_the_hits_re2_gives(monkeypatch):
    r = corpus.rng(11)
    texts = [corpus.mixed_text(r) for _ in range(12)]
    texts += [corpus.curl_user(r) + " " + corpus.curl_user(r), corpus.bearer(r),
              corpus.key_header(), corpus.password_assignment(r)]
    sets = ("credentials", "broad")
    engine.reset()
    on_re2 = [scan(t, sets) for t in texts]
    assert engine.active().name == "re2"
    monkeypatch.setitem(sys.modules, "re2", None)
    engine.reset()
    try:
        on_re = [scan(t, sets) for t in texts]
        assert engine.active().name == "re"
    finally:
        engine.reset()
    assert on_re == on_re2
    assert sum(len(h) for h in on_re2) > 20


# -- where a wheel fits ---------------------------------------------------------------------

LINUX = {"implementation": "CPython", "version": (3, 12), "gil_disabled": False,
         "system": "Linux", "machine": "x86_64", "bits": 64, "libc": "glibc 2.39",
         "mac": ""}


@pytest.mark.parametrize("change, fits", [
    ({}, True),
    ({"libc": None}, False),                                  # musl answers nothing
    ({"libc": "glibc 2.27"}, False),
    ({"libc": "glibc 2.28"}, True),
    ({"machine": "aarch64"}, True),
    ({"machine": "i686", "bits": 32}, False),                 # 32-bit Linux
    ({"machine": "x86_64", "bits": 32}, False),
    ({"gil_disabled": True}, False),                          # free-threaded
    ({"version": (3, 14)}, True),
    ({"version": engine.RE2_BOUND}, False),                   # at the bound
    ({"implementation": "PyPy"}, False),
    ({"system": "Darwin", "libc": None, "mac": "12.7", "machine": "arm64"}, False),
    ({"system": "Darwin", "libc": None, "mac": "13.0", "machine": "arm64"}, True),
    ({"system": "Darwin", "libc": None, "mac": "14.2", "machine": "x86_64"}, True),
    ({"system": "Windows", "libc": None, "machine": "AMD64"}, True),
    ({"system": "Windows", "libc": None, "machine": "x86", "bits": 32}, True),
    ({"system": "Windows", "libc": None, "machine": "ARM64", "version": (3, 10)}, False),
    ({"system": "Windows", "libc": None, "machine": "ARM64", "version": (3, 11)}, True),
    ({"system": "FreeBSD", "libc": None}, False),
])
def test_re2_wheel_fits(change, fits):
    assert engine.re2_wheel_fits({**LINUX, **change}) is fits


def test_platform_facts_survive_a_libc_that_will_not_answer(monkeypatch):
    def refuse(name):
        raise ValueError("unrecognized configuration name")

    monkeypatch.setattr(engine.os, "confstr", refuse, raising=False)
    facts = engine.platform_facts()
    assert facts["libc"] is None
    if facts["system"] == "Linux":
        assert engine.re2_wheel_fits(facts) is False


@pytest.mark.parametrize("fits", [True, False])
def test_optional_names_re2_exactly_where_a_wheel_fits(monkeypatch, fits):
    import runpy

    import moxi_secrets.module as module

    monkeypatch.setattr(engine, "re2_wheel_fits", lambda facts=None: fits)
    names = runpy.run_path(module.__file__)
    assert ("re2" in names["OPTIONAL"]) is fits
    assert "personal-data" in names["OPTIONAL"]


# -- the fallback worker --------------------------------------------------------------------

@pytest.fixture
def worker_command(monkeypatch, fallback_engine):
    """Replace the worker's command line; restores the real one after."""
    fallback.shutdown()

    def use(argv):
        monkeypatch.setattr(fallback, "command", lambda: argv)
    yield use
    fallback.shutdown()


SETS2 = ("credentials", "broad")


def assert_unscanned(text, hits):
    assert [(h.set, h.rule, h.start, h.end) for h in hits] == [
        (s, "unscanned", 0, len(text)) for s in SETS2]


def test_a_worker_that_will_not_start_gives_unscanned(worker_command, tmp_path):
    worker_command([str(tmp_path / "no-such-python"), "-c", "pass"])
    assert_unscanned("hello", scan("hello", SETS2))
    assert fallback.last_failure.startswith("the worker will not start")
    assert redact_once("hello") == "[REDACTED:unscanned]"


def redact_once(text):
    from moxi_secrets import redact
    return redact(text, SETS2)


def test_a_worker_that_exits_or_sends_garbage_gives_unscanned(worker_command):
    worker_command([sys.executable, "-c", "import sys; sys.stdin.readline()"])
    assert_unscanned("abc", scan("abc", SETS2))
    assert fallback.last_failure == "the worker exited"
    worker_command([sys.executable, "-c",
                    "import sys; sys.stdin.readline(); print('not json', flush=True)"])
    assert_unscanned("abc", scan("abc", SETS2))
    assert fallback.last_failure == "the worker sent a reply that does not parse"
    worker_command([sys.executable, "-I", "-m", "moxi_secrets.fallback"])
    assert scan("abc", SETS2) == []                         # the next scan starts afresh


def test_a_worker_killed_mid_scan_gives_unscanned_and_the_next_scan_starts_afresh(
        worker_command):
    worker_command([sys.executable, "-c",
                    "import sys, time; sys.stdin.readline(); time.sleep(30)"])
    import threading

    def kill_soon():
        time.sleep(0.3)
        fallback._worker.proc.kill()

    threading.Thread(target=kill_soon, daemon=True).start()
    started = time.monotonic()
    assert_unscanned("abc", scan("abc", SETS2))
    assert time.monotonic() - started < 1.0
    worker_command([sys.executable, "-I", "-m", "moxi_secrets.fallback"])
    assert scan("abc", SETS2) == []


def test_a_scan_past_its_deadline_gives_unscanned_by_the_deadline(worker_command,
                                                                  monkeypatch):
    worker_command([sys.executable, "-c",
                    "import sys, time; sys.stdin.readline(); time.sleep(30)"])
    monkeypatch.setattr(fallback, "BASE_DEADLINE", 0.3)
    started = time.monotonic()
    assert_unscanned("abc", scan("abc", SETS2))
    assert time.monotonic() - started < 0.3 + 0.5
    assert fallback.last_failure == "the scan reached its deadline"


def slow_text():
    # A keyword, then a megabyte of hex on one line: about 3 s for the broad set on
    # Python's re, far past the deadlines these tests give.
    r = corpus.rng(12)
    return "key " + corpus.chars(r, 1_000_000, "0123456789abcdef")


def child():
    return subprocess.Popen([sys.executable, "-I", "-m", "moxi_secrets.fallback"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)


def request(proc, text, wait, sets=("credentials",)):
    data = text.encode()
    proc.stdin.write(json.dumps({"sets": list(sets), "path": None, "size": len(data),
                                 "deadline": wait}).encode() + b"\n" + data)
    proc.stdin.flush()


@pytest.mark.skipif(sys.platform == "win32", reason="the child's own timer is Linux and "
                    "macOS only; on Windows an orphaned child runs until its search ends")
def test_a_child_whose_caller_stops_listening_exits_by_its_own_timer():
    proc = child()
    try:
        request(proc, slow_text(), 0.2, ("broad",))
        started = time.monotonic()
        assert proc.wait(timeout=20) == fallback.TIMER_EXIT
        assert time.monotonic() - started < 0.2 + fallback.CHILD_GRACE + 1.5
    finally:
        proc.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="the child's timer is Linux and macOS "
                    "only")
def test_the_child_cancels_its_timer_when_a_text_is_done():
    proc = child()
    try:
        request(proc, "hello", 0.1)
        assert json.loads(proc.stdout.readline()) == {"hits": {"credentials": []}}
        time.sleep(0.1 + fallback.CHILD_GRACE + 0.4)
        assert proc.poll() is None
        request(proc, "again", 0.1)
        assert json.loads(proc.stdout.readline()) == {"hits": {"credentials": []}}
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
