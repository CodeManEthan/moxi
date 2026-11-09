"""The lifetime table: the shipped rows pinned, the loader's refusals, the user's own rows,
and what a table that does not load holds."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import (CodexRollout, assistant_line, token_usage, write_cache_config,
                      write_transcript)

from moxi_cache import api, table

NOW = 1_791_300_000.0
TID = "0190c000-0000-7000-8000-00000000000a"
SID = "ses_0000000000000TestSessionA"
CLAUDE_SID = "aaaaaaaa-1111-4111-8111-111111111111"


def shipped_text() -> str:
    """The table as it ships, not the copy the suite reads."""
    return Path(table.__file__).with_name("lifetimes.toml").read_text()


GOOD_ROW = """[[lifetime]]
harness = "opencode"
provider = "anthropic"
models = ["*"]
seconds = 300
anchor = "request-start"
source = "https://example.invalid/docs"
checked = 2026-10-05
"""


def row_with(**changes) -> str:
    lines = []
    for line in GOOD_ROW.splitlines():
        key = line.split(" = ")[0] if " = " in line else None
        if key in changes:
            if changes[key] is None:
                continue
            line = f"{key} = {changes.pop(key)}"
        lines.append(line)
    lines += [f"{k} = {v}" for k, v in changes.items() if v is not None]
    return "\n".join(lines) + "\n"


MEASURED_ROW = """[[lifetime]]
harness = "codex"
provider = "openai"
models = ["gpt-5.6-sol"]
seconds = 7200
floor = 1800
measured = true
anchor = "last-use"
source = "https://example.invalid/docs"
checked = 2026-10-06
evidence = "a measurement"
basis = "a basis"
"""

FREE_ROW = """[[lifetime]]
harness = "opencode"
provider = "opencode"
models = ["big-pickle"]
charge = "free"
source = "https://example.invalid/zen"
checked = 2026-10-05
basis = "a basis"
"""

MALFORMED = [
    row_with(source='""'),                                       # no source, no basis
    row_with(harness='"claude"'),
    row_with(anchor='"whenever"'),
    row_with(models='["*", "claude-x"]'),
    row_with(charge='"free"'),                                   # seconds and charge
    row_with(seconds=None),                                      # neither
    row_with(seconds='"300"'),
    row_with(seconds="0"),
    row_with(seconds="1.5"),
    row_with(models='"claude-x"'),
    row_with(models="[]"),
    row_with(models='["claude-x", "claude-x"]'),
    row_with(checked='"2026-10-05"'),
    FREE_ROW.replace('"free"', '"cheap"'),
    FREE_ROW + 'anchor = "last-use"\n',
    FREE_ROW + "floor = 10\n",
    FREE_ROW + "measured = true\n",
    FREE_ROW.replace('["big-pickle"]', '["*"]'),
    FREE_ROW.replace('basis = "a basis"\n', ""),
    MEASURED_ROW.replace('basis = "a basis"\n', ""),
    MEASURED_ROW.replace('evidence = "a measurement"\n', ""),
    MEASURED_ROW + 'ruling = "the old name beside the new"\n',  # basis and ruling
    MEASURED_ROW.replace("floor = 1800", "floor = 9000"),
    MEASURED_ROW.replace("floor = 1800", 'floor = "1800"'),
    MEASURED_ROW.replace("floor = 1800\n", ""),
    MEASURED_ROW + MEASURED_ROW.replace("7200", "3600"),         # two rows, one model
    GOOD_ROW + GOOD_ROW.replace('["*"]', '["claude-x"]'),        # "*" overlaps an id
    row_with(colour='"blue"'),                                   # a key the table ignores
    "not = [toml",
    "",
    "[other]\nkey = 1\n",
]


def test_the_shipped_table_loads_and_meets_every_rule():
    loaded = table.parse_table(shipped_text())
    for row in loaded.rows:
        assert row.source.startswith("https://") or row.basis, row
        assert row.checked
        assert (row.seconds or 0) > 0 or row.free
        if row.provider == "openai":
            assert row.evidence or row.basis, row
        if row.measured or row.free:
            assert row.basis, row
        assert row.origin == table.SHIPPED


def test_the_shipped_rows_are_pinned():
    """Adding or changing a row changes this pin in the same commit."""
    loaded = table.parse_table(shipped_text())
    got = [(r.harness, r.provider, r.models, r.seconds, r.charge, r.floor, r.measured,
            r.anchor, r.checked) for r in loaded.rows]
    assert got == [
        ("codex", "openai", ("gpt-5.6-sol",), 5400, None, 1800, True, "last-use",
         "2026-10-06"),
        ("codex", "openai", ("gpt-5.6-terra", "gpt-5.6-luna", "gpt-6-astra"), 1800, None,
         None, False, "last-use", "2026-10-06"),
        ("opencode", "openai", ("gpt-5.6-sol", "gpt-6-astra", "gpt-5.6-terra",
                                "gpt-5.6-luna"), 1800, None, None, False, "last-use",
         "2026-10-05"),
        ("opencode", "anthropic", ("*",), 300, None, None, False, "request-start",
         "2026-10-05"),
        ("opencode", "opencode", ("big-pickle", "space-bunny-free"), None, "free", None,
         False, None, "2026-10-06"),
    ]
    sources = {r.source for r in loaded.rows}
    assert sources == {"https://developers.openai.com/api/docs/guides/prompt-caching",
                       "https://platform.claude.com/docs/en/build-with-claude/prompt-caching",
                       "https://opencode.ai/docs/zen/"}


@pytest.mark.parametrize("text", MALFORMED, ids=[str(n) for n in range(len(MALFORMED))])
def test_the_loader_refuses_each_malformed_table(text):
    with pytest.raises(table.TableError):
        table.parse_table(text)


def test_the_loader_takes_each_good_row_kind():
    loaded = table.parse_table(GOOD_ROW + MEASURED_ROW + FREE_ROW)
    assert loaded.lookup("opencode", "anthropic", "anything").seconds == 300
    assert loaded.lookup("codex", "openai", "gpt-5.6-sol").measured
    assert loaded.is_free("opencode", "opencode", "big-pickle")
    assert loaded.lookup("opencode", "opencode", "gpt-5.5") is None


def test_the_shipped_table_is_kept_while_the_file_is_unchanged(lifetime_table):
    first = table.load_shipped()
    assert table.load_shipped() is first
    lifetime_table.write_text(GOOD_ROW)
    assert table.load_shipped() is not first


# ============================================================ a table that does not load

def codex_rollout(root: Path, model="gpt-5.6-terra", age=60) -> None:
    """A one-turn rollout of TID, its request ending `age` seconds before NOW."""
    r = CodexRollout(root, thread_id=TID, start=NOW - 86400)
    r.clock = NOW - age - 5
    r.turn(model, turn_id="t1")
    r.clock = NOW - age
    r.request(token_usage(input=100, cached=90))
    r.write()


def test_a_table_that_does_not_load_holds_codex_and_opencode_only(tmp_path,
                                                                  lifetime_table):
    root = tmp_path / "codex-sessions"
    codex_rollout(root)
    assert api.read("codex", TID, cache_source=str(root), now=NOW).kind == "warm"
    write_transcript(CLAUDE_SID, 60, root=tmp_path / "projects")
    lifetime_table.write_text(row_with(seconds='"300"'))
    for clock in (api.read("codex", TID, cache_source=str(root), now=NOW),
                  api.read("codex", None, new_session=True, now=NOW),
                  api.read("opencode", SID, cache_source=str(tmp_path / "o.db"),
                           agent_model={"agent": "build", "model": "opencode/big-pickle"},
                           now=NOW)):
        assert (clock.kind, clock.code) == ("unknown", "table-not-loaded")
        assert clock.reason.startswith(table.NOT_LOADED)
    claude = api.read("claude", CLAUDE_SID, projects_root=str(tmp_path / "projects"))
    assert claude.kind == "warm"


# ============================================================ the user's own rows

USER_ROW = """[[lifetime]]
harness = "codex"
provider = "openai"
models = ["gpt-5.5"]
seconds = 600
anchor = "last-use"
basis = "my own reading of my route"
checked = 2026-10-10
"""


def test_a_user_row_adds_a_model(tmp_path, own_roots):
    root = tmp_path / "codex-sessions"
    codex_rollout(root, model="gpt-5.5", age=300)
    before = api.read("codex", TID, cache_source=str(root), now=NOW)
    assert (before.kind, before.code) == ("no-cache", "no-row")
    write_cache_config(own_roots, USER_ROW)
    after = api.read("codex", TID, cache_source=str(root), now=NOW)
    assert (after.kind, after.ttl_s, after.key) == ("warm", 600, "codex openai/gpt-5.5")
    loaded = table.load_table()
    assert [r.models for r in loaded.of(table.USER)] == [("gpt-5.5",)]
    codex_rollout(root, model="gpt-5.5", age=600)
    assert api.read("codex", TID, cache_source=str(root), now=NOW).kind == "cold"


@pytest.mark.parametrize("text,words", [
    (USER_ROW.replace('["gpt-5.5"]', '["gpt-5.6-terra"]'), "overlaps a shipped row"),
    (USER_ROW.replace('provider = "openai"\nmodels = ["gpt-5.5"]',
                      'provider = "openai"\nmodels = ["*"]'), "overlaps a shipped row"),
    (USER_ROW + USER_ROW, "your config's rows overlap"),
    (USER_ROW.replace('basis = "my own reading of my route"\n', ""), "no `source`"),
    (USER_ROW.replace('harness = "codex"', 'harness = "claude"'), "unknown harness"),
    ("lifetime = 3\n", "is an int, not a list"),
], ids=["shipped-model", "shipped-wildcard", "two-user-rows", "no-source", "harness",
        "not-a-list"])
def test_a_user_row_that_cannot_be_vouched_for_refuses_the_table(tmp_path, own_roots,
                                                                 text, words):
    """A user row adds a model and never overrides one; a row the loader refuses refuses
    the table, so a typo never silently changes who can be reached."""
    root = tmp_path / "codex-sessions"
    codex_rollout(root)
    write_cache_config(own_roots, text)
    with pytest.raises(table.TableError) as err:
        table.load_table()
    assert words in str(err.value)
    clock = api.read("codex", TID, cache_source=str(root), now=NOW)
    assert (clock.kind, clock.code) == ("unknown", "table-not-loaded")
    assert words in clock.reason


def test_a_row_that_says_ruling_is_read_as_basis(own_roots):
    """`ruling` is `basis`'s old name: a row written before the rename still loads."""
    old = USER_ROW.replace("basis = ", "ruling = ")
    assert "ruling = " in old and "basis = " not in old
    write_cache_config(own_roots, old)
    (row,) = table.load_table().of(table.USER)
    assert row.basis == "my own reading of my route"
    loaded = table.parse_table(MEASURED_ROW.replace("basis = ", "ruling = "))
    assert loaded.rows[0].basis == "a basis"


def test_a_user_row_is_read_from_the_config_at_each_use(tmp_path, own_roots):
    path = write_cache_config(own_roots, USER_ROW)
    assert len(table.load_table().of(table.USER)) == 1
    path.write_text("")
    assert table.load_table().of(table.USER) == ()


def test_a_user_free_row_needs_its_basis(own_roots):
    free = FREE_ROW.replace('["big-pickle"]', '["my-free-model"]')
    write_cache_config(own_roots, free)
    assert table.load_table().is_free("opencode", "opencode", "my-free-model")
    write_cache_config(own_roots, free.replace('basis = "a basis"\n', ""))
    with pytest.raises(table.TableError):
        table.load_table()


def test_a_claude_line_never_reads_the_table(tmp_path, own_roots):
    write_cache_config(own_roots, "lifetime = 3\n")
    write_transcript(CLAUDE_SID, root=tmp_path, lines=[assistant_line(60, tier="1h")])
    assert api.read("claude", CLAUDE_SID, projects_root=str(tmp_path)).kind == "warm"
