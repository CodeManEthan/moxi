"""Generation: sets.json against its sources, what the generator drops or refuses, and the
fallback translator's rewrites."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest

from moxi_secrets import _generate as gen

RULES = Path(gen.__file__).resolve().parent / "rules"
PACKAGE = RULES.parents[2]

# Small made-up rule files; no pattern here matches a real credential's shape.
UPSTREAM = r"""
title = "fixture upstream"
minVersion = "v8.0.0"

[allowlist]
description = "global"
paths = ['''\.lock$''']
stopwords = ["Example"]

[[rules]]
id = "generic-api-key"
description = "catch-all"
regex = '''(?i)key\s*=\s*([a-z0-9]{12,})'''
keywords = ["key"]
entropy = 3.5

[[rules]]
id = "widget-token"
description = "widget"
regex = '''\bwidget-[a-z]{4}\b'''
keywords = ["Widget-"]
"""

CREDENTIALS = r"""
title = "fixture credentials"

[extend]
path = "upstream/gitleaks.toml"
disabledRules = ["generic-api-key"]

[[allowlists]]
paths = ['''(?:^|/)rules/sets\.json$''']

[[rules]]
id = "gadget-token"
description = "gadget"
regex = '''\bgadget_[0-9]{6}\b'''
keywords = ["gadget_"]
"""

BROAD = r"""
title = "fixture broad"

[[rules]]
id = "long-run"
description = "a long run"
regex = '''[a-z]{30,}'''
entropy = 4
"""

DROP = "# rule id <TAB> reason\n"


def make_rules(root: Path, *, upstream: str = UPSTREAM, credentials: str = CREDENTIALS,
               broad: str = BROAD, drop: str = DROP) -> Path:
    rules = root / "rules"
    (rules / "upstream").mkdir(parents=True)
    (rules / "upstream" / "gitleaks.toml").write_bytes(upstream.encode())
    digest = hashlib.sha256(upstream.encode()).hexdigest()
    (rules / "upstream" / "PIN").write_bytes(
        f'tag = "v0.0.1"\ncommit = "{"0" * 40}"\nsha256 = "{digest}"\n'
        f"fetched = 2026-01-02\n".encode())
    (rules / "credentials.toml").write_bytes(credentials.encode())
    (rules / "broad.toml").write_bytes(broad.encode())
    (rules / "drop.txt").write_bytes(drop.encode())
    return rules


def rule_ids(data: dict, name: str) -> list[str]:
    return [r["id"] for r in data["sets"][name]["rules"]]


def dropped(data: dict, name: str) -> dict[str, str]:
    return {d["id"]: d["reason"] for d in data["sets"][name]["dropped"]}


# --- the package's own files ----------------------------------------------------------------

def test_sets_json_equals_a_fresh_generation():
    assert gen.generate(RULES) == (RULES / "sets.json").read_bytes()


def test_check_mode_passes_on_the_committed_file():
    assert gen.main(["--check", str(RULES)]) == 0


def test_the_pin_matches_the_upstream_bytes():
    pin = tomllib.loads((RULES / "upstream" / "PIN").read_text(encoding="utf-8"))
    digest = hashlib.sha256((RULES / "upstream" / "gitleaks.toml").read_bytes()).hexdigest()
    assert pin["sha256"] == digest
    assert json.loads((RULES / "sets.json").read_bytes())["upstream"]["sha256"] == digest


def test_the_sets_hold_the_expected_rules():
    data = gen.build(RULES)
    credentials, broad = data["sets"]["credentials"], data["sets"]["broad"]
    sources = [r["source"] for r in credentials["rules"]]
    assert (sources.count("gitleaks"), sources.count("local")) == (221, 10)
    assert rule_ids(data, "broad") == ["generic-api-key", "credential-assignment",
                                       "high-entropy-run"]
    assert "generic-api-key" not in rule_ids(data, "credentials")
    assert credentials["dropped"] == broad["dropped"] == []


def test_no_local_id_is_an_upstream_id():
    upstream = tomllib.loads((RULES / "upstream" / "gitleaks.toml").read_text("utf-8"))
    upstream_ids = {r["id"] for r in upstream["rules"]}
    for name in ("credentials.toml", "broad.toml"):
        local = tomllib.loads((RULES / name).read_text("utf-8"))
        assert not {r["id"] for r in local["rules"]} & upstream_ids, name


def test_sets_json_is_ascii_with_lf_endings():
    data = (RULES / "sets.json").read_bytes()
    assert data.isascii() and b"\r" not in data and data.endswith(b"}\n")


def test_the_rule_files_are_marked_binary_for_git():
    lines = (PACKAGE / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "src/moxi_secrets/rules/** -text" in lines


def test_the_own_file_allowance_names_sets_json():
    paths = [p["pattern"] for a in gen.build(RULES)["allowlists"] for p in a["paths"]]
    assert any(re.search(p, "packages/secrets/src/moxi_secrets/rules/sets.json")
               for p in paths)


# --- generation on made-up rule files ------------------------------------------------------

def test_a_fixture_resolves(tmp_path):
    data = gen.build(make_rules(tmp_path))
    assert rule_ids(data, "credentials") == ["widget-token", "gadget-token"]
    assert rule_ids(data, "broad") == ["generic-api-key", "long-run"]
    widget = data["sets"]["credentials"]["rules"][0]
    assert widget["keywords"] == ["widget-"]
    assert widget["regex"]["pattern"] == r"\bwidget-[a-z]{4}\b"
    assert widget["source"] == "gitleaks" and widget["entropy"] == 0.0
    assert data["sets"]["broad"]["rules"][1]["entropy"] == 4.0
    first, second = data["allowlists"]
    assert first["stopwords"] == ["example"] and first["condition"] == "OR"
    assert first["regexTarget"] == "secret"
    assert second["paths"][0]["pattern"] == r"(?:^|/)rules/sets\.json$"
    assert data["upstream"]["fetched"] == "2026-01-02"


@pytest.mark.parametrize("extra, field", [
    ("[[rules.required]]\nid = \"gadget-token\"\n", "required"),
    ("requiredRules = [\"gadget-token\"]\n", "requiredRules"),
    ("skipReport = true\n", "skipReport"),
    ("madeUpField = 1\n", "madeUpField"),
    ("[[rules.allowlists]]\ncommits = [\"abc123\"]\n", "commits"),
    ("[rules.allowlist]\nstopwords = [\"x\"]\n", "allowlist"),
])
def test_an_unimplemented_field_drops_the_rule(tmp_path, extra, field):
    upstream = UPSTREAM.replace('keywords = ["Widget-"]\n', 'keywords = ["Widget-"]\n' + extra)
    data = gen.build(make_rules(tmp_path, upstream=upstream))
    assert "widget-token" not in rule_ids(data, "credentials")
    reason = dropped(data, "credentials")["widget-token"]
    assert field in reason and "does not implement" in reason
    assert rule_ids(data, "credentials") == ["gadget-token"]


@pytest.mark.parametrize("change", [
    lambda u, c: (u.replace('title = "fixture upstream"', 'title = "x"\nmadeUp = 1'), c),
    lambda u, c: (u, c.replace('title = "fixture credentials"', 'title = "x"\ndescription = "y"')),
    lambda u, c: (u.replace('stopwords = ["Example"]', 'stopwords = ["Example"]\ncommits = ["a"]'), c),
    lambda u, c: (u, c.replace("[[allowlists]]\n", "[[allowlists]]\ntargetRules = [\"x\"]\n")),
    lambda u, c: (u, c.replace("[[allowlists]]\n", "[[allowlists]]\nmadeUp = true\n")),
    lambda u, c: (u, c.replace("[extend]\n", "[extend]\nuseDefault = true\n")),
], ids=["upstream-top", "local-top", "global-commits", "global-targetRules", "global-made-up",
        "extend-field"])
def test_an_unknown_global_or_top_level_field_fails(tmp_path, change):
    upstream, credentials = change(UPSTREAM, CREDENTIALS)
    with pytest.raises(gen.GenerationError):
        gen.build(make_rules(tmp_path, upstream=upstream, credentials=credentials))


def test_a_drop_txt_entry_is_dropped_with_its_reason(tmp_path):
    rules = make_rules(tmp_path, drop=DROP + "widget-token\tRE2 and Go disagree on a fold\n"
                                             "\nlong-run\ttoo slow on long lines\n")
    data = gen.build(rules)
    assert dropped(data, "credentials") == {"widget-token": "RE2 and Go disagree on a fold"}
    assert dropped(data, "broad") == {"long-run": "too slow on long lines"}
    assert rule_ids(data, "credentials") == ["gadget-token"]


@pytest.mark.parametrize("line", ["nosuchrule\tgone\n", "widget-token\n", "widget-token\t \n",
                                  "widget-token\ta\nwidget-token\tb\n"])
def test_a_bad_drop_txt_line_fails(tmp_path, line):
    with pytest.raises(gen.GenerationError, match="drop.txt"):
        gen.build(make_rules(tmp_path, drop=DROP + line))


def test_a_pattern_the_translator_refuses_drops_its_rule(tmp_path):
    upstream = UPSTREAM.replace(r"'''\bwidget-[a-z]{4}\b'''", r"'''\bwidget-\pL{4}'''")
    reason = dropped(gen.build(make_rules(tmp_path, upstream=upstream)), "credentials")
    assert "fallback translator refuses its regex" in reason["widget-token"]


def test_a_refused_global_allowlist_pattern_fails(tmp_path):
    upstream = UPSTREAM.replace(r"paths = ['''\.lock$''']", r"paths = ['''\pL\.lock$''']")
    with pytest.raises(gen.GenerationError, match="global allowlist"):
        gen.build(make_rules(tmp_path, upstream=upstream))


@pytest.mark.parametrize("credentials, broad", [
    (CREDENTIALS.replace('id = "gadget-token"', 'id = "widget-token"'), BROAD),
    (CREDENTIALS, BROAD.replace('id = "long-run"', 'id = "widget-token"')),
    (CREDENTIALS, BROAD.replace('id = "long-run"', 'id = "gadget-token"')),
    (CREDENTIALS + '\n[[rules]]\nid = "gadget-token"\nregex = "a"\n', BROAD),
], ids=["local-upstream", "broad-upstream", "broad-local", "twice"])
def test_a_rule_id_clash_fails(tmp_path, credentials, broad):
    with pytest.raises(gen.GenerationError, match="rule id"):
        gen.build(make_rules(tmp_path, credentials=credentials, broad=broad))


def test_a_pin_that_does_not_match_fails(tmp_path):
    rules = make_rules(tmp_path)
    (rules / "upstream" / "gitleaks.toml").write_bytes(UPSTREAM.encode() + b"\n")
    with pytest.raises(gen.GenerationError, match="PIN"):
        gen.build(rules)


@pytest.mark.parametrize("credentials, match", [
    (CREDENTIALS.replace('"generic-api-key"]', '"generic-api-key", "nope"]'), "disabledRules"),
    (CREDENTIALS.replace('path = "upstream/gitleaks.toml"\n', ""), "extend"),
    (CREDENTIALS.replace(r"regex = '''\bgadget_[0-9]{6}\b'''", ""), "neither"),
    (CREDENTIALS.replace("[[allowlists]]\n", "[[allowlists]]\ncondition = \"XOR\"\n"),
     "condition"),
])
def test_a_config_gitleaks_would_refuse_fails(tmp_path, credentials, match):
    with pytest.raises(gen.GenerationError, match=match):
        gen.build(make_rules(tmp_path, credentials=credentials))


def test_broad_toml_takes_no_extend_or_global_allowlist(tmp_path):
    broad = BROAD + "\n[[allowlists]]\nstopwords = [\"x\"]\n"
    with pytest.raises(gen.GenerationError, match="broad.toml"):
        gen.build(make_rules(tmp_path, broad=broad))


def test_main_writes_and_checks(tmp_path, capsys):
    rules = make_rules(tmp_path)
    assert gen.main(["--check", str(rules)]) == 1
    assert gen.main([str(rules)]) == 0
    assert (rules / "sets.json").read_bytes() == gen.generate(rules)
    assert gen.main(["--check", str(rules)]) == 0
    (rules / "drop.txt").write_bytes(b"nosuchrule\tx\n")
    assert gen.main([str(rules)]) == 2
    assert "drop.txt" in capsys.readouterr().err


# --- the fallback translator ---------------------------------------------------------------

def search(pattern: str, text: str) -> str | None:
    m = re.search(gen.translate(pattern), text)
    return None if m is None else m.group(0)


@pytest.mark.parametrize("pattern, text, expected", [
    (r"ab\z", "ab", "ab"),
    (r"ab\z", "ab\n", None),
    (r"ab$", "ab\n", None),                 # Go's $ is the end of the text
    (r"(?m)ab$", "ab\ncd", "ab"),
    (r"(?m:ab$)", "ab\ncd", "ab"),
    (r"a\sb", "a\vb", None),                # Go's \s has no vertical tab
    (r"a\sb", "a b", None),
    (r"a\sb", "a\tb", "a\tb"),
    (r"\w+", "été", "t"),         # Go's \w is ASCII
    (r"\d", "٣", None),
    (r"[\d]", "٣", None),
    (r"\bx", "éx", "x"),               # an ASCII boundary
    (r"[[:alnum:]]+", "a:b", "a"),
    (r"[[:^digit:]]+", "ab1", "ab"),
    (r"[\s\S-]{3}", "a\n-", "a\n-"),
    (r"[^\S]", "a b", " "),
    (r"[^\S\t]", "a\t b", " "),
    (r"[\S]+", "ab cd", "ab"),
    (r"a{,2}", "a{,2}", "a{,2}"),           # a literal brace in Go
    (r"a{2,}", "aaa", "aaa"),
    (r"(?<name>ab)", "ab", "ab"),
    (r"\x41\x{42}\101", "ABA", "ABA"),
    (r"[]a]+", "]a", "]a"),
    (r"[a-]+", "a-", "a-"),
    (r"[[]", "[", "["),
    (r"[&&~~||-]+", "&~|-", "&~|-"),
    (r"\.\$\#", ".$#", ".$#"),
    (r"(?i)k", "K", "K"),         # case folds as Go's (?i) does
    (r"(?i)s", "ſ", "ſ"),
])
def test_the_translation_matches_as_go_does(pattern, text, expected):
    assert search(pattern, text) == expected


def test_a_flag_group_holds_to_the_end_of_its_group_across_alternatives():
    translated = gen.translate(r"\b(p8e-(?i)[a-z]{2}|q(?-i:Z))x")
    assert search(r"\b(p8e-(?i)[a-z]{2}|q(?-i:Z))x", "p8e-AZx") == "p8e-AZx"
    assert re.search(translated, "QZx").group(0) == "QZx"     # (?i) holds after the |
    assert re.search(translated, "Qzx") is None
    assert re.search(translated, "P8E-azx") is None           # but not before it


def test_a_leading_flag_stays_global():
    assert gen.translate(r"(?i)ab") == "(?i)ab"
    assert gen.translate(r"(?-i)ab") == "(?-i:ab)"


@pytest.mark.parametrize("pattern", [
    r"\pL", r"\p{Greek}", r"\PN", r"\Qa.b\E", r"\C", r"(?U)a+", r"(a)\1", r"\y",
    r"(?=a)", r"(?<=a)b", r"[[:nope:]]", r"[\b]", r"(a", r"a)", r"[a",
])
def test_the_translator_refuses_what_it_cannot_express(pattern):
    with pytest.raises(gen.Unsupported):
        gen.translate(pattern)


@pytest.mark.parametrize("pattern, groups", [
    (r"(a)(?:b)(?i)(c)|(d)", 3),
    (r"[(](x)[\s\S]", 1),
    (r"\((?P<n>y)(?<m>z)", 2),
    (r"(?i:(a))[^\S(]", 1),
])
def test_translations_keep_the_capture_groups(pattern, groups):
    assert re.compile(gen.translate(pattern)).groups == groups
