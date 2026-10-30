"""The scan: the dated misses, the probe misses, no-hit cases, gitleaks' behaviour,
scan_lines and the import's weight."""

from __future__ import annotations

import importlib
import subprocess
import sys

import corpus
import pytest

from moxi_secrets import SETS, Hit, describe, redact, scan, scan_lines
from moxi_secrets import engine as eng_mod
scan_mod = importlib.import_module("moxi_secrets.scan")

GITLEAKS = ("credentials", "broad")


def rules_of(hits):
    return {(h.set, h.rule) for h in hits}


# -- the dated misses and the probe misses ------------------------------------------------

def test_the_dated_misses_hit_in_their_sets(engine_name):
    r = corpus.rng(1)
    cases = [
        ("bare " + corpus.discord(r) + " end", "credentials", "discord-bot-token"),
        ("GET https://api.telegram.org/bot" + corpus.telegram(r) + "/getMe",
         "credentials", "telegram-bot-token"),
        ("dsn " + corpus.url_credential(r), "credentials", "url-credential"),
        ("dsn " + corpus.url_credential(r, "admin"), "credentials", "url-credential"),
        (corpus.password_assignment(r), "broad", "credential-assignment"),
    ]
    for text, set_name, rule in cases:
        assert (set_name, rule) in rules_of(scan(text, GITLEAKS)), rule


def test_the_probe_misses_hit_and_its_false_positives_stay_quiet(engine_name):
    r = corpus.rng(2)
    for make, rule in [(corpus.stripe, "stripe-access-token"),
                       (corpus.google_api, "gcp-api-key"),
                       (lambda r: corpus.github(r, "o"), "github-oauth"),
                       (lambda r: corpus.github(r, "s"), "github-app-token"),
                       (corpus.jwt, "jwt"), (corpus.bws, "bws-access-token"),
                       (corpus.google_refresh, "google-oauth-refresh-token"),
                       (corpus.google_access, "google-oauth-access-token"),
                       (corpus.google_client_secret, "google-oauth-client-secret")]:
        value = make(r)
        text = f"value: {value}\n"
        hits = [h for h in scan(text, ("credentials",)) if h.rule == rule]
        assert hits, rule
        assert any(text[h.start:h.end] in value for h in hits)
    iban = corpus.iban(r)
    assert rules_of(scan(f"pay to {iban} today", ("personal-data",))) == {
        ("personal-data", "iban")}
    quiet = "id 123456789, ssn 000-12-3456, number " + corpus.card(r, 16, "9")
    assert scan(quiet, ("personal-data",)) == []


# -- no-hit cases ---------------------------------------------------------------------------

def every_placeholder():
    return [scan_mod.placeholder(rule) for rule in sorted(scan_mod.emittable())]


def test_ordinary_text_raises_no_hit(engine_name):
    r = corpus.rng(3)
    hexd = "0123456789abcdef"
    texts = [
        "commit " + corpus.chars(r, 40, hexd),
        "sha256 " + corpus.chars(r, 64, hexd),
        "id " + "-".join(corpus.chars(r, n, hexd) for n in (8, 4, 4, 4, 12)),
        "/srv/" + "/".join(corpus.chars(r, 9, "abcdefghij") for _ in range(12)) + ".txt",
        "Choose a strong password and keep the password manager locked.",
    ]
    for text in texts:
        assert scan(text, ("credentials", "broad", "personal-data")) == [], text


def test_every_placeholder_is_quiet(engine_name, names_config):
    names_config(words=["redacted", "pat", "github"])
    placeholders = every_placeholder()
    for text in placeholders + ["".join(placeholders), "\n".join(placeholders)]:
        assert scan(text, ("credentials", "broad", "personal-data")) == []
    # Placeholder text alone is opaque even to a path-only rule; a newline between two
    # placeholders is not placeholder text, so there the path-only hit is reported.
    for text in placeholders + ["".join(placeholders)]:
        assert scan(text, SETS, path="keys/a.p12") == []
    assert [h.rule for h in scan("\n".join(placeholders[:2]), SETS,
                                 path="keys/a.p12")] == ["pkcs12-file"]


# -- gitleaks' behaviour, on a small sets.json ---------------------------------------------

def pat(text):
    return {"pattern": text, "fallback": text}


def rule(rid, regex, *, keywords=(), entropy=0.0, group=0, path=None, allowlists=()):
    return {"id": rid, "description": rid, "source": "local",
            "regex": pat(regex) if regex else None, "secretGroup": group,
            "entropy": entropy, "keywords": list(keywords),
            "path": pat(path) if path else None, "tags": [],
            "allowlists": list(allowlists)}


def allowlist(*, condition="OR", target="secret", regexes=(), paths=(), stopwords=()):
    return {"description": "", "condition": condition, "regexTarget": target,
            "regexes": [pat(x) for x in regexes], "paths": [pat(x) for x in paths],
            "stopwords": list(stopwords)}


@pytest.fixture(params=["re2", "re"])
def small(request, monkeypatch):
    """Install a small sets.json; yields run(text, path=None) -> [(rule, secret)]."""
    def install(rules, global_allowlists=(), broad=()):
        data = {"format": 1, "upstream": {"tag": "t", "commit": "c", "sha256": "s",
                                         "fetched": "d"},
                "allowlists": list(global_allowlists),
                "sets": {"credentials": {"title": "", "rules": list(rules), "dropped": []},
                         "broad": {"title": "", "rules": list(broad), "dropped": []}}}
        monkeypatch.setattr(scan_mod, "_sets", scan_mod.Sets(data))
        engine = eng_mod.Re2Engine(eng_mod.re2_module()) if request.param == "re2" \
            else eng_mod.ReEngine()

        def run(text, path=None, sets=("credentials",)):
            t = eng_mod.Text(text)
            found = scan_mod.run_gitleaks(t, sets, path, engine)
            return [(rid, text[s:e]) for name in sets for rid, s, e in found[name]]
        return run
    return install


def test_keywords_gate_a_rule(small):
    run = small([rule("r", r"zz[0-9]{4}", keywords=["magic"])])
    assert run("zz1234") == []
    assert run("MAGIC zz1234") == [("r", "zz1234")]


def test_entropy_drops_a_secret_at_the_floor_and_keeps_one_above(small):
    # "aabb" has entropy exactly 1.0.
    run = small([rule("r", r"v=([a-z]+)", entropy=1.0)])
    assert run("v=aabb") == []
    assert run("v=aabc") == [("r", "aabc")]


def test_the_secret_is_the_group_then_the_first_non_empty_group_then_the_match(small):
    run = small([rule("g2", r"k(a*)=(b+)(c*)", group=3), rule("first", r"x(y*)(z+)"),
                 rule("whole", r"w[0-9]+"), rule("over", r"o(p)", group=5)])
    assert run("k=bbccc") == [("g2", "ccc")]
    assert run("xzz") == [("first", "zz")]
    assert run("w42") == [("whole", "w42")]
    assert run("op") == []                # a secret group past the last group


def test_global_and_rule_allowlists(small):
    run = small([rule("r", r"tok_([a-z0-9]+)", allowlists=[
                    allowlist(regexes=[r"^safe"]),
                    allowlist(target="line", regexes=[r"#\s*ok"]),
                    allowlist(condition="AND", target="match", regexes=[r"tok_"],
                              stopwords=["dummy"])])],
                global_allowlists=[allowlist(stopwords=["example"])])
    assert run("tok_abc123") == [("r", "abc123")]
    assert run("tok_safe1") == []                       # rule regex on the secret
    assert run("x = tok_abc123  # ok") == []            # line target
    assert run("tok_dummy9") == []                      # AND: match regex and stopword
    assert run("tok_example7") == []                    # global stopword
    run_and = small([rule("r", r"tok_([a-z0-9]+)", allowlists=[
        allowlist(condition="AND", regexes=[r"^zz"], paths=[r"^tests/"])])])
    assert run_and("tok_zz1") == [("r", "zz1")]          # AND needs the path too
    assert run_and("tok_zz1", "tests/a.txt") == []
    assert run_and("tok_aa1", "tests/a.txt") == [("r", "aa1")]


def test_path_scoped_and_path_only_rules(small):
    run = small([rule("scoped", r"pw=([a-z]+)", path=r"\.ya?ml$"),
                 rule("file", None, path=r"\.p12$")])
    assert run("pw=abc") == []
    assert run("pw=abc", "a/b.txt") == []
    assert run("pw=abc", "a/b.yml") == [("scoped", "abc")]
    assert run("anything", "keys/a.p12") == [("file", "anything")]


def test_a_global_path_allowance_skips_the_text(small):
    run = small([rule("r", r"tok_([a-z]+)"), rule("file", None, path=r"\.p12$")],
                global_allowlists=[allowlist(paths=[r"^vendor/"])])
    assert run("tok_abc", "vendor/x.p12") == []
    assert run("tok_abc", "src/x.p12") == [("r", "abc"), ("file", "tok_abc")]


def test_gitleaks_allow_keeps_its_line_out_for_the_gitleaks_sets_only(engine_name,
                                                                     names_config):
    names_config(words=["quillfeather"])
    r = corpus.rng(4)
    token = corpus.github(r)
    marked = f"t = {token}  # gitleaks:allow quillfeather 123-45-6789"
    hits = scan(marked, SETS)
    assert not any(h.set in GITLEAKS for h in hits)
    assert {"private-names", "personal-data"} <= {h.set for h in hits}
    assert any(h.rule == "github-pat" for h in scan(f"t = {token}\n# gitleaks:allow",
                                                   ("credentials",)))


# -- hits -----------------------------------------------------------------------------------

def test_a_hit_holds_no_secret_and_its_repr_shows_none(engine_name):
    r = corpus.rng(5)
    token = corpus.github(r)
    text = f"x {token}"
    (hit,) = scan(text, ("credentials",))
    assert token not in repr(hit) and token not in str(hit.__slots__)
    assert text[hit.start:hit.end] == token and hit.line == 1
    assert hit.fingerprint == scan_mod.fingerprint(token)
    assert hit.fingerprint.startswith("sha256:") and len(hit.fingerprint) == 23
    with pytest.raises(AttributeError):
        hit.rule = "x"


def test_known_values_come_first_and_an_empty_one_is_ignored(engine_name):
    text = "pass the value s3cr3t-value along, s3cr3t-value again"
    hits = scan(text, ("credentials",), known=["s3cr3t-value", ""])
    assert [h.rule for h in hits] == ["known", "known"]
    assert [text[h.start:h.end] for h in hits] == ["s3cr3t-value"] * 2


def test_sets_are_required_and_checked():
    with pytest.raises(ValueError):
        scan("x", ("nope",))
    with pytest.raises(ValueError):
        scan("x", "credentials")
    with pytest.raises(TypeError):
        scan(b"x", ("credentials",))
    assert scan("x", ()) == []


def test_any_text_scans_including_escaped_bytes(engine_name):
    r = corpus.rng(6)
    token = corpus.github(r)
    raw = b"caf\xc3\xa9 \xff\xfe " + token.encode() + b" \x80"
    text = raw.decode("utf-8", "surrogateescape")
    (hit,) = scan(text, ("credentials",))
    assert text[hit.start:hit.end] == token
    out = redact(text, ("credentials",))
    assert out.encode("utf-8", "surrogateescape") == \
        b"caf\xc3\xa9 \xff\xfe [REDACTED:github-pat] \x80"


def test_hits_are_sorted_and_a_pair_on_one_span_both_come_back(engine_name):
    r = corpus.rng(8)
    text = f"token = {corpus.github(r)}\nb = {corpus.stripe(r)}"
    hits = scan(text, ("credentials", "broad"))
    assert [h.start for h in hits] == sorted(h.start for h in hits)
    assert {"github-pat", "generic-api-key"} <= {h.rule for h in hits}


def test_describe_counts_the_sets():
    info = {i.name: i for i in describe()}
    assert set(info) == set(SETS)
    assert len(info["credentials"].rules) == 231 and len(info["broad"].rules) == 3
    assert info["credentials"].pin["tag"] == "v8.30.1"
    assert info["credentials"].dropped == ()


# -- scan_lines -----------------------------------------------------------------------------

def test_scan_lines_maps_hits_back_to_path_and_line(engine_name):
    r = corpus.rng(9)
    a, b = corpus.github(r), corpus.stripe(r)
    triples = [("src/a.py", 10, "x = 1"), ("docs/b.md", 3, "token " + b + " end"),
               ("src/a.py", 12, f"y = {a}"), ("keys/c.p12", 7, "first"),
               ("keys/c.p12", 9, "second")]
    hits = scan_lines(triples, ("credentials",))
    got = {(h.path, h.line, h.hit.rule) for h in hits}
    assert ("src/a.py", 12, "github-pat") in got
    assert ("docs/b.md", 3, "stripe-access-token") in got
    assert ("keys/c.p12", 7, "pkcs12-file") in got
    for h in hits:
        line = dict(((p, n), t) for p, n, t in triples)[(h.path, h.line)]
        if h.hit.rule == "github-pat":
            assert line[h.hit.start:h.hit.end] == a
    assert isinstance(hits[0].hit, Hit)


# -- the import -----------------------------------------------------------------------------

def test_the_import_pulls_in_no_engine_config_or_stdnum():
    code = ("import sys, moxi_secrets; "
            "print(sorted(m for m in ('re2', 'moxi.config', 'stdnum') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True)
    assert out.stdout.strip() == "[]"
