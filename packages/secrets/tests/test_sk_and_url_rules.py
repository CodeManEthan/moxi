"""credentials' `sk-api-key` and `url-credential`: the keys and URL passwords they find,
which `redact` leaves no character of, and the placeholders and type names they let
through. Every value is built at run time from pieces (see `corpus.py`)."""

from __future__ import annotations

import re

import corpus

from moxi_secrets import redact, scan

GITLEAKS = ("credentials", "broad")
RULES = {"sk-api-key", "url-credential"}
PLACEHOLDERS = re.compile(r"(?:\[REDACTED:[a-z0-9-]+\])+")
SK = "".join(("s", "k-"))


def url(password: str, user: str = "app", scheme: str = "postgres") -> str:
    return "".join((scheme, "://", user, ":", password, "@", "db.example.test:5432/main"))


def sk_must_hit(r) -> list[tuple[str, str]]:
    """(text, value) for each `sk-` key the rule must find."""
    cases = []
    for text, value in [
        ("{}", corpus.sk_key(r, 16)),
        ("{}", corpus.sk_key(r, 32)),
        ("{}", corpus.sk_key(r, 32, corpus.HEX)),
        ("the key is {} ok", corpus.sk_key(r, 24)),
        ("DEEPSEEK_API_KEY={}", corpus.sk_key(r, 32, corpus.HEX)),
        ("export OPENROUTER_API_KEY={}", corpus.sk_key(r, 64, corpus.HEX, "or-v1-")),
        ("{}", corpus.sk_key(r, 20, corpus.URLSAFE, "proj-")),
        ('key = "{}"', corpus.sk_key(r, 20)),
    ]:
        cases.append((text.format(value), value))
    return cases


def url_must_hit(r) -> list[tuple[str, str]]:
    """(text, password) for each URL password the rule must find."""
    passwords = [("app", "".join(("my", "password1"))),
                 ("root", "".join(("Pass", "w0rd!"))),
                 ("app", "".join(("change", "me99"))),
                 ("app", "".join(("by", "pass2024"))),
                 ("app", corpus.chars(r, 12))]
    return [("dsn " + url(pw, user, "mysql" if user == "root" else "postgres"), pw)
            for user, pw in passwords]


def assert_redacted_whole(text: str, value: str) -> None:
    """`redact` with credentials and broad keeps what lies around `value` and leaves only
    placeholders where any character of it stood."""
    out = redact(text, GITLEAKS)
    before, after = text.split(value)
    lead = 0
    while lead < min(len(before), len(out)) and out[lead] == before[lead]:
        lead += 1
    tail = 0
    while (tail < min(len(after), len(out) - lead)
           and out[len(out) - 1 - tail] == after[len(after) - 1 - tail]):
        tail += 1
    assert PLACEHOLDERS.fullmatch(out[lead:len(out) - tail]), (text, out)


def rule_hits(text: str) -> set[str]:
    return {h.rule for h in scan(text, ("credentials",))} & RULES


def test_each_sk_key_hits_and_redacts_whole(engine_name):
    for text, value in sk_must_hit(corpus.rng(31)):
        assert "sk-api-key" in rule_hits(text), text
        assert_redacted_whole(text, value)


def test_sk_placeholders_short_values_type_names_and_kebab_words_stay_quiet(engine_name):
    r = corpus.rng(32)
    quiet = [
        corpus.sk_key(r, 15),
        SK + "x" * 24,
        SK + "0" * 32,
        SK + "proj-" + "x" * 16,
        SK + "or-v1-" + "x" * 16,
        "type " + SK + "ecdsa-sha2-nistp256@openssh.com",
        "type " + SK + "ecdsa-sha2-nistp256-cert-v01@openssh.com",
        "type " + SK + "ssh-ed25519-cert-v01@openssh.com",
        "type " + SK + "ssh-ed25519@openssh.com",
        "pip install fla" + SK + "sqlalchemy-utils-extension",
        "ta" + SK + "runner-config-file-name",
    ]
    for text in quiet:
        assert "sk-api-key" not in rule_hits(text), text


def test_an_sk_key_glued_after_a_word_character_is_not_found(engine_name):
    r = corpus.rng(33)
    for glue in ("my_", "a", "9"):
        assert "sk-api-key" not in rule_hits(glue + corpus.sk_key(r, 32)), glue


def test_each_url_password_hits_and_redacts_whole(engine_name):
    for text, password in url_must_hit(corpus.rng(34)):
        assert "url-credential" in rule_hits(text), text
        assert_redacted_whole(text, password)


def test_a_whole_placeholder_password_stays_quiet(engine_name):
    for password in ["password", "pass", "PASSWORD", "<password>", "[password]",
                     "{password}", "{{password}}", "YOUR_PASSWORD", "$DB_PASSWORD",
                     "%PASSWORD%", "changeme", "xxxx", "${DB_PASS}", "db_pass", "bypassme"]:
        assert "url-credential" not in rule_hits(url(password)), password
