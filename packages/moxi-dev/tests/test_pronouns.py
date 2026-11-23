"""The pronoun check: which words it refuses, where it reads them, and the mark that lets a
genuine use stand."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from moxi_dev import check, pronouns
from moxi_dev.pronouns import (Hit, check_text, doc_words, kind, python_words, scan,
                               web_texts, web_words)


# --- the words -------------------------------------------------------------------------

@pytest.mark.parametrize("word", ["he", "his", "him", "himself", "He", "HIS", "Him",
                                  "HimSelf"])
def test_each_word_is_refused_in_any_case(word):
    assert doc_words(f"then {word} left\n") == [(1, word)]


@pytest.mark.parametrize("text", [
    "the shell", "hehe", "heh", "this", "whim", "hims", "here", "hello", "Thesis",
    "chimself", "his_count", "him2", "him-and-her", "she-he", "shape.his", "`ListShape.his`", "a.b.his",
])
def test_a_word_that_only_holds_one_is_not_refused(text):
    assert doc_words(text) == []


@pytest.mark.parametrize("text, word", [
    ("he's out", "he"), ("'his'", "his"), ("(him)", "him"), ("him.", "him"),
    ("`he`", "he"), ("at him, then", "him"), ("wait...he left", "he"),
    ("done.He left", "He"), ("a..him", "him"),
])
def test_a_word_beside_punctuation_is_refused(text, word):
    assert doc_words(text) == [(1, word)]


# --- what each kind of file is read as -------------------------------------------------

@pytest.mark.parametrize("path, how", [
    ("packages/cache/src/moxi_cache/page.py", "python"),
    ("packages/cache/src/moxi_cache/sub/deep.py", "python"),
    ("packages/cache/web/src/page.ts", "web"),
    ("packages/cache/web/src/deep/page.mjs", "web"),
    ("packages/cache/web/src/page.tsx", "web"),
    ("packages/cache/web/src/page.js", "web"),
    ("packages/cache/README.md", "doc"),
    ("packages/comms/docs/protocol.md", "doc"),
    ("packages/rounds/src/moxi_rounds/skills/moxi-rounds/SKILL.md", "doc"),
    ("packages/moxi/src/moxi/_vendor.py", "python"),
    ("packages/cache/src/moxi_cache/_vendor/lib.py", "python"),
])
def test_the_files_the_check_reads(path, how):
    assert kind(path) == how


@pytest.mark.parametrize("path", [
    "packages/cache/tests/test_page.py",
    "packages/cache/CHANGELOG.md",
    "packages/cache/docs/deep/more.md",
    "packages/cache/docs/notes.txt",
    "packages/cache/src/moxi_cache/notes.md",
    "packages/cache/src/moxi_cache/web/page.js",
    "packages/cache/web/dist/page.js",
    "packages/cache/web/src/page.css",
    "packages/cache/web/tests/page.test.ts",
    "packages/cache/pyproject.toml",
    "packages/moxi/src/moxi/_vendor/tomlkit/parser.py",
    "packages/README.md",
    "README.md",
    "CONTRIBUTING.md",
    "scripts/x.py",
])
def test_the_files_the_check_leaves_alone(path):
    assert kind(path) is None


# --- Python: string literals and docstrings, never names or comments -------------------

def test_python_reads_string_literals_and_docstrings():
    source = ('"""The module: he reads it."""\n'
              "def f():\n"
              "    '''A doc,\n"
              "    then his line.'''\n"
              "    return 'for him'\n")
    assert python_words(source) == [(1, "he"), (4, "his"), (5, "him")]


def test_python_leaves_names_comments_and_code_alone():
    source = ("him = 1  # he wrote this\n"
              "def his(he): return he.himself\n"
              "x = him\n")
    assert python_words(source) == []


def test_python_reads_f_string_text_but_not_its_expressions():
    source = 'name = f"{him} told {his!r}: he left"\n'
    assert python_words(source) == [(1, "he")]


def test_python_reads_bytes_raw_and_implicitly_joined_strings():
    source = 'x = (b"his", r"him"\n     "he")\n'
    assert python_words(source) == [(1, "his"), (1, "him"), (2, "he")]


def test_python_reads_a_word_right_after_an_escape():
    assert python_words('x = "line\\nhe left\\thim"\n') == [(1, "he"), (1, "him")]
    assert python_words('x = "\\N{BULLET}his"\n') == [(1, "his")]


def test_python_that_does_not_tokenize_gives_what_came_before():
    assert python_words('x = "he"\ny = (\n') == [(1, "he")]


# --- web: strings, template text and comments ------------------------------------------

def texts(source: str) -> list[str]:
    return [piece for _, piece in web_texts(source)]


def test_web_reads_comments_strings_and_template_text():
    source = ("// he reads\n"
              "/** his doc\n * and him */\n"
              "const a = 'he', b = \"his\";\n"
              "const c = `him ${b} himself`;\n")
    assert web_words(source) == [(1, "he"), (2, "his"), (3, "him"), (4, "he"), (4, "his"),
                                 (5, "him"), (5, "himself")]


def test_web_leaves_names_and_code_alone():
    source = ("const him = operatorAddresses(mail);\n"
              "if (him.has(x)) his(he => he.himself);\n"
              "class X { #him(): Set<string> { return this.him; } }\n")
    assert web_words(source) == []


def test_web_reads_a_template_expression_as_code_and_its_strings_as_strings():
    source = "const t = html`<p>${him ? 'he' : `his ${him}`}</p> himself`;\n"
    assert web_words(source) == [(1, "he"), (1, "his"), (1, "himself")]


def test_web_reads_braces_inside_a_template_expression():
    source = "const t = `a ${{ him: 1 }.him} he`;\n"
    assert texts(source) == ["a ", " he"]


def test_web_skips_a_regular_expression_literal_with_a_quote_in_it():
    source = "const r = /['\"]/g; // he\nconst q = x / 2; // his\n"
    assert web_words(source) == [(1, "he"), (2, "his")]


def test_web_reads_a_division_after_a_name_or_bracket_as_code():
    source = "const a = b / c / d, e = (f) / g; // him\n"
    assert texts(source) == ["// him"]


def test_web_reads_a_regex_after_return():
    source = "function f() { return /'/.test(x); } // he\n"
    assert texts(source) == ["// he"]


def test_web_reads_a_url_in_a_string_as_a_string():
    assert texts("const u = 'https://example.com/he';\n") == ["'https://example.com/he'"]


def test_web_reads_an_escaped_quote_inside_a_string():
    assert texts("const s = 'it\\'s his';\n") == ["'it\\'s his'"]


def test_web_reads_an_unclosed_comment_or_template_to_the_end():
    assert web_words("/* he\nhis") == [(1, "he"), (2, "his")]
    assert web_words("`he\nhim") == [(1, "he"), (2, "him")]


# --- the mark ------------------------------------------------------------------------

def test_a_marked_line_lets_its_words_stand():
    source = "x = 'he'  # noqa: MX5 -- a quotation\ny = 'his'\n"
    assert check_text("a.py", source, "python") == [Hit("a.py", 2, "his")]


def test_the_mark_takes_the_file_s_own_comment_syntax():
    doc = "a reply to him <!-- noqa: MX5 -- a generic replier -->\n"
    web = "// as he types it  // noqa: MX5 -- quotes a tool's own words\n"
    assert check_text("a.md", doc, "doc") == []
    assert check_text("a.ts", web, "web") == []


def test_the_mark_may_name_other_codes_beside_its_own():
    source = "def f(x='he'):  # noqa: FBT001, MX5 -- a sample\n    pass\n"
    assert check_text("a.py", source, "python") == []


def test_the_mark_stands_on_a_docstring_s_inner_line_in_the_text_itself():
    source = '"""One.\n\nhe said (noqa: MX5 -- a quotation)\n"""\n'
    assert check_text("a.py", source, "python") == []


def test_a_mark_on_another_line_lets_nothing_stand():
    source = "# noqa: MX5 -- a quotation\nx = 'he'\n"
    assert check_text("a.py", source, "python") == [
        Hit("a.py", 1, "", "unused"), Hit("a.py", 2, "he")]


def test_a_mark_with_no_reason_is_refused_and_lets_nothing_stand():
    for mark in ("# noqa: MX5", "# noqa: MX5 --", "# noqa: MX5 --  "):
        source = f"x = 'he'  {mark}\n"
        assert check_text("a.py", source, "python") == [
            Hit("a.py", 1, "", "reason"), Hit("a.py", 1, "he")], mark


def test_a_mark_for_another_code_lets_nothing_stand():
    source = "x = 'he'  # noqa: MX3 -- not this rule\n"
    assert check_text("a.py", source, "python") == [Hit("a.py", 1, "he")]


def test_a_mark_quoted_as_inline_code_is_an_example_not_a_mark():
    doc = "A genuine use carries `noqa: MX5 -- <why>` on its line.\n"
    assert check_text("a.md", doc, "doc") == []
    assert check_text("a.md", "he `# noqa: MX5 -- a sample`\n", "doc") == [Hit("a.md", 1, "he")]


def test_a_mark_whose_reason_cites_code_in_backticks_still_counts():
    source = "x = 'he'  # noqa: MX5 -- quotes `Row.label`\n"
    assert check_text("a.py", source, "python") == []


def test_a_mark_on_a_line_with_nothing_refused_is_unused():
    source = "him = 1  # noqa: MX5 -- the name is no word the check reads\n"
    assert check_text("a.py", source, "python") == [Hit("a.py", 1, "", "unused")]


# --- the lines ------------------------------------------------------------------------

def test_a_hit_reads_as_path_line_word_and_fix():
    assert str(Hit("packages/a/README.md", 3, "His")) == (
        "packages/a/README.md:3: His — fix: name the operator neutrally, or mark it "
        "# noqa: MX5 -- <why>")


def test_a_mark_fault_reads_with_its_fix():
    assert str(Hit("a.py", 2, "", "reason")) == (
        "a.py:2: pronouns: the MX5 mark gives no reason; add -- <why> after MX5")
    assert str(Hit("a.py", 2, "", "unused")) == (
        "a.py:2: pronouns: the MX5 mark lets nothing stand; remove it")


# --- the scan over a tree --------------------------------------------------------------

def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q")
    (root / ".gitignore").write_text("ignored/\n")
    return root


def test_scan_reads_each_kind_and_leaves_the_rest(repo):
    write(repo, "packages/a/src/moxi_a/x.py", "'''He reads.'''\nhim = 1  # he\n")
    write(repo, "packages/a/web/src/p.ts", "const him = 1; // his\n")
    write(repo, "packages/a/README.md", "Give it to him.\n")
    write(repo, "packages/a/docs/d.md", "one\nhimself\n")
    write(repo, "packages/a/src/moxi_a/skills/s/SKILL.md", "as he likes\n")
    write(repo, "packages/a/tests/test_x.py", "'he'\n")
    write(repo, "packages/a/CHANGELOG.md", "he\n")
    write(repo, "packages/a/src/moxi_a/web/p.js", "'he'\n")
    write(repo, "packages/moxi/src/moxi/_vendor/lib.py", "'he'\n")
    write(repo, "README.md", "he\n")
    write(repo, "ignored/packages/a/README.md", "he\n")
    git(repo, "add", "packages/a/src")
    assert scan(repo) == [
        Hit("packages/a/README.md", 1, "him"),
        Hit("packages/a/docs/d.md", 2, "himself"),
        Hit("packages/a/src/moxi_a/skills/s/SKILL.md", 1, "he"),
        Hit("packages/a/src/moxi_a/x.py", 1, "He"),
        Hit("packages/a/web/src/p.ts", 1, "his"),
    ]


def test_scan_skips_a_file_that_is_not_text(repo):
    (repo / "packages/a").mkdir(parents=True)
    (repo / "packages/a/README.md").write_bytes(b"\xff\xfe he \x00")
    assert scan(repo) == []


# --- in the check ---------------------------------------------------------------------

def test_the_static_steps_run_the_pronoun_step_and_print_its_lines(repo, monkeypatch,
                                                                    capsys):
    write(repo, "packages/a/README.md", "Ask him.\n")
    result = check.Result()
    steps = check._Steps(repo, [], (), {}, lambda *a: 0, lambda *a: [], result)
    for name in ("tags", "references", "names", "ruff", "renames", "release"):
        monkeypatch.setattr(check._Steps, name, lambda self: True)
    assert steps.static() is False and result.pronouns == "fail"
    assert "| release not run | pronouns fail |" in result.line()
    assert capsys.readouterr().out == (
        "moxi-dev check: packages/a/README.md:1: him — fix: " + pronouns.FIX + "\n")


def test_the_pronoun_step_passes_a_clean_tree(repo):
    write(repo, "packages/a/README.md", "Ask the operator.\n")
    result = check.Result()
    steps = check._Steps(repo, [], (), {}, lambda *a: 0, lambda *a: [], result)
    assert steps.pronouns() is True and result.pronouns == "pass"


def test_only_pronouns_runs_the_step_alone_and_filters_by_path(repo, capsys):
    write(repo, "packages/a/README.md", "Ask him.\n")
    write(repo, "packages/b/README.md", "Ask him.\n")
    assert check.only(repo, "pronouns", ["packages/b"]) == 1
    out = capsys.readouterr().out
    assert "packages/b/README.md:1: him" in out and "packages/a" not in out
    assert check.only(repo, "pronouns", ["packages/c"]) == 0
