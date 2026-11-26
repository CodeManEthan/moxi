"""The questions an add puts (contribution interface 3): the contract's shape, the CLI's
ask on a terminal, the lines printed without one, presets, and a failing answer."""

from __future__ import annotations

import io
import json
import shutil
import sys

import pytest

from moxi_projects import acts, api, cli, contributions

ASKS = '''
INTERFACE = 3
SEEN = []
ANSWERS = []
PRESET = {preset!r}
FAIL = {fail!r}

def questions(fields):
    SEEN.append(dict(fields))
    return [
        {{"key": "clean", "text": "Keep it clean?", "choices": ["yes", "no"],
          "suggested": "yes", "preset": PRESET.get("clean"),
          "later": "fake clean enable " + fields["root"]}},
        {{"key": "push", "text": "Guard pushes?", "choices": ["yes", "no"],
          "suggested": "no", "preset": PRESET.get("push"), "later": "fake push enable"}},
        {{"key": "branches", "text": "Which branches?", "after": ["push", "yes"],
          "preset": PRESET.get("branches"), "later": "fake push enable --branch NAME"}},
    ]

def answer(project, key, value):
    if key == FAIL:
        raise RuntimeError("it broke")
    ANSWERS.append((project.key, key, value))
    return f"{{key}} set to {{value}}"
'''


def asks(site, *, preset: dict | None = None, fail: str | None = None) -> str:
    """Install the fake `fake` module asking three questions; returns its link module's
    name."""
    return site.contribution("fake", ASKS.format(preset=preset or {}, fail=fail))


def link(name: str):
    return sys.modules[name]


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def run_tty(monkeypatch, capsys, typed: str, *argv: str) -> tuple[int, str, str]:
    """`moxi projects` with a terminal on stdin and stdout, `typed` its input."""
    stdout = Tty()
    monkeypatch.setattr(sys, "stdin", Tty(typed))
    monkeypatch.setattr(sys, "stdout", stdout)
    code = cli.main(list(argv))
    return code, stdout.getvalue(), capsys.readouterr().err


def run_pipe(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


# --- the CLI's ask ---------------------------------------------------------------------

def test_a_terminal_add_asks_each_question_its_suggestion_the_default(
        site, monkeypatch, capsys, tmp_path):
    name = asks(site)
    code, out, err = run_tty(monkeypatch, capsys, "\nyes\nmain dev\n",
                             "add", "web", "--root", str(tmp_path))
    assert code == 0, err
    assert "fake: Keep it clean? [yes/no] (yes)" in out
    assert "fake: Guard pushes? [yes/no] (no)" in out
    assert "fake: Which branches?" in out
    assert link(name).ANSWERS == [("web", "clean", "yes"), ("web", "push", "yes"),
                                  ("web", "branches", "main dev")]
    assert "note: fake: clean set to yes" in err
    assert "not asked" not in err
    assert api.get("web").root == str(tmp_path)


def test_a_follow_up_is_passed_over_when_its_question_is_answered_otherwise(
        site, monkeypatch, capsys, tmp_path):
    name = asks(site)
    code, out, _err = run_tty(monkeypatch, capsys, "no\n\n", "add", "web", "--root",
                              str(tmp_path))
    assert code == 0
    assert "Which branches?" not in out
    assert link(name).ANSWERS == [("web", "clean", "no"), ("web", "push", "no")]


def test_an_answer_not_among_the_choices_asks_again(site, monkeypatch, capsys, tmp_path):
    name = asks(site)
    code, out, _err = run_tty(monkeypatch, capsys, "maybe\nNO\n\n", "add", "web", "--root",
                              str(tmp_path))
    assert code == 0
    assert "answer yes or no" in out
    assert link(name).ANSWERS[0] == ("web", "clean", "no")


def test_an_end_of_input_stops_the_asking_and_names_the_rest(
        site, monkeypatch, capsys, tmp_path):
    name = asks(site)
    code, _out, err = run_tty(monkeypatch, capsys, "yes\n", "add", "web", "--root",
                              str(tmp_path))
    assert code == 0
    assert link(name).ANSWERS == [("web", "clean", "yes")]
    assert "not asked: fake asks: Guard pushes? (suggested: no); to set it later: " \
           "fake push enable" in err
    assert api.get("web")


def test_without_a_terminal_nothing_is_asked_or_turned_on_and_each_command_is_named(
        site, capsys, tmp_path):
    name = asks(site)
    code, out, err = run_pipe(capsys, "add", "web", "--root", str(tmp_path))
    assert code == 0
    assert out.splitlines()[0] == "web"
    assert link(name).ANSWERS == []
    lines = [line for line in err.splitlines() if line.startswith("not asked: ")]
    assert lines == [
        "not asked: fake asks: Keep it clean? (suggested: yes); to set it later: "
        f"fake clean enable {tmp_path}",
        "not asked: fake asks: Guard pushes? (suggested: no); to set it later: "
        "fake push enable",
    ]


def test_a_terminal_on_stdin_alone_is_no_terminal(site, monkeypatch, capsys, tmp_path):
    name = asks(site)
    monkeypatch.setattr(sys, "stdin", Tty("yes\n"))
    code, _out, err = run_pipe(capsys, "add", "web", "--root", str(tmp_path))
    assert code == 0
    assert link(name).ANSWERS == []
    assert err.count("not asked: ") == 2


def test_json_asks_nothing_even_on_a_terminal_and_returns_the_lines_as_asked(
        site, monkeypatch, capsys, tmp_path):
    name = asks(site)
    code, out, _err = run_tty(monkeypatch, capsys, "yes\n", "add", "web", "--root",
                              str(tmp_path), "--json")
    assert code == 0
    got = json.loads(out)
    assert got["key"] == "web"
    assert got["asked"] == [
        f"fake asks: Keep it clean? (suggested: yes); to set it later: fake clean enable "
        f"{tmp_path}",
        "fake asks: Guard pushes? (suggested: no); to set it later: fake push enable"]
    assert "answered" not in got and "warnings" not in got
    assert link(name).ANSWERS == []


def test_json_with_no_questions_is_the_project_alone(capsys, site, tmp_path):
    code, out, _err = run_pipe(capsys, "add", "web", "--root", str(tmp_path), "--json")
    assert code == 0
    assert set(json.loads(out)) == set(api.get("web").as_dict())


# --- presets ---------------------------------------------------------------------------

def test_a_preset_is_acted_on_without_a_question_with_or_without_a_terminal(
        site, monkeypatch, capsys, tmp_path):
    name = asks(site, preset={"clean": "yes", "push": "no"})
    code, out, err = run_pipe(capsys, "add", "web", "--root", str(tmp_path), "--json")
    assert code == 0, err
    got = json.loads(out)
    assert "asked" not in got
    assert got["answered"] == [{"module": "fake", "key": "clean", "value": "yes"},
                               {"module": "fake", "key": "push", "value": "no"}]
    code, shown, _err = run_tty(monkeypatch, capsys, "", "add", "app", "--root",
                                str(tmp_path / "app"))
    assert code == 0
    assert "?" not in shown.split("app")[0]
    assert link(name).ANSWERS[-2:] == [("app", "clean", "yes"), ("app", "push", "no")]


def test_a_preset_yes_with_its_follow_up_open_asks_it_on_a_terminal_and_names_it_without(
        site, monkeypatch, capsys, tmp_path):
    name = asks(site, preset={"clean": "no", "push": "yes"})
    code, out, _err = run_tty(monkeypatch, capsys, "main\n", "add", "web", "--root",
                              str(tmp_path))
    assert code == 0
    assert "Keep it clean?" not in out and "Which branches?" in out
    assert link(name).ANSWERS[-1] == ("web", "branches", "main")
    code, _out, err = run_pipe(capsys, "add", "app", "--root", str(tmp_path / "app"))
    assert code == 0
    assert [line for line in err.splitlines() if line.startswith("not asked: ")] == [
        "not asked: fake asks: Which branches?; to set it later: "
        "fake push enable --branch NAME"]
    assert ("app", "branches", "main") not in link(name).ANSWERS


# --- a failing answer ------------------------------------------------------------------

def test_a_failing_answer_leaves_the_project_added_and_names_the_fix(
        site, monkeypatch, capsys, tmp_path):
    name = asks(site, fail="clean")
    code, _out, err = run_tty(monkeypatch, capsys, "\n\n", "add", "web", "--root",
                              str(tmp_path))
    assert code == 0
    assert api.get("web").root == str(tmp_path)
    assert (f"warning: fake's answer to 'clean' failed (RuntimeError: it broke), so the "
            f"project is added without it; to set it: fake clean enable {tmp_path}") in err
    assert link(name).ANSWERS == [("web", "push", "no")]


def test_given_answers_are_acted_on_and_one_off_the_choices_is_a_warning(site, tmp_path):
    name = asks(site)
    added = acts.add("web", root=str(tmp_path),
                     answers={("fake", "clean"): " Yes ", ("fake", "push"): "perhaps"})
    assert link(name).ANSWERS == [("web", "clean", "yes")]
    assert added.answered == [{"module": "fake", "key": "clean", "value": "yes"}]
    assert added.warnings == ["fake can't take 'perhaps' for 'push': the answers are yes, "
                              "no; to set it: fake push enable"]
    assert added.asked == []


def test_questions_see_the_row_as_written_and_the_mint(site, tmp_path):
    name = asks(site)
    acts.add("web", root=str(tmp_path / "web"), lead="boss")
    assert link(name).SEEN == [{"key": "web", "root": str(tmp_path / "web"),
                                "kind": "project", "lead": "boss", "notes_root": None,
                                "mint": None}]
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    acts.add("app", root=str(tmp_path / "app"), create=True)
    assert link(name).SEEN[-1]["mint"] == "git"
    assert (tmp_path / "app" / ".git").exists()


# --- the contract's shape --------------------------------------------------------------

@pytest.mark.parametrize("source, reason", [
    ("INTERFACE = 3\ndef questions(fields):\n    return []\n",
     "it offers questions without answer"),
    ("INTERFACE = 3\ndef answer(project, key, value):\n    return None\n",
     "it offers answer without questions"),
    ("INTERFACE = 3\nquestions = 1\ndef answer(project, key, value):\n    return None\n",
     "its questions is not callable"),
])
def test_an_entry_offering_one_half_or_no_callable_wont_load(site, source, reason):
    site.contribution("bad", source)
    found = contributions.load()
    assert [m for m, _r in found.unloadable] == ["bad"]
    assert reason in found.unloadable[0][1]


def test_questions_under_interface_2_are_not_read(site, tmp_path):
    name = site.contribution("old", "INTERFACE = 2\nCALLED = []\n"
                                    "def questions(fields):\n    CALLED.append(1)\n"
                                    "    return [{'key': 'k', 'text': 't', 'later': 'l'}]\n")
    added = acts.add("web", root=str(tmp_path))
    assert added.asked == [] and link(name).CALLED == []


@pytest.mark.parametrize("said, why", [
    ("'no'", "answered str, not a list or None"),
    ("[{'key': 'k', 'text': 't'}]", "gave a question no later"),
    ("[{'key': 'k', 'text': 't', 'later': 'l', 'colour': 1}]",
     "gave a question the field 'colour'"),
    ("[{'key': 'k', 'text': 't', 'later': 'l', 'choices': ['a'], 'suggested': 'b'}]",
     "gave the question 'k' the suggested 'b', not one of its choices"),
    ("[{'key': 'k', 'text': 't', 'later': 'l', 'after': ['j', 'yes']}]",
     "put the question 'k' after 'j', which it hasn't put before it"),
    ("[{'key': 'k', 'text': 't', 'later': 'l'}, {'key': 'k', 'text': 't', 'later': 'l'}]",
     "put the question 'k' twice"),
    ("[{'key': 'Push.x', 'text': 't', 'later': 'l'}]",
     "gave a question the key 'Push.x'; a key is a lower-case letter, then lower-case "
     "letters, digits and _"),
])
def test_questions_out_of_shape_put_none_and_the_add_goes_ahead_with_a_warning(
        site, tmp_path, said, why):
    site.contribution("odd", "INTERFACE = 3\n"
                             f"def questions(fields):\n    return {said}\n"
                             "def answer(project, key, value):\n    return None\n")
    added = acts.add("web", root=str(tmp_path))
    assert api.get("web")
    assert added.asked == []
    assert added.warnings == [f"skipped odd's questions, since its questions {why}, so "
                              "none of them was put; fix: repair odd or uninstall it "
                              "(`moxi uninstall odd`)"]


def test_a_raising_questions_puts_none_and_the_add_goes_ahead(site, tmp_path):
    site.contribution("odd", "INTERFACE = 3\n"
                             "def questions(fields):\n    raise KeyError('root')\n"
                             "def answer(project, key, value):\n    return None\n")
    added = acts.add("web", root=str(tmp_path))
    assert api.get("web")
    assert added.warnings[0].startswith("skipped odd's questions, since its questions "
                                        "raised KeyError")


def test_a_question_reads_as_a_record(site, tmp_path):
    asks(site)
    found = contributions.load()
    questions, warnings = found.questions({"key": "web", "root": str(tmp_path),
                                           "kind": "project", "lead": None,
                                           "notes_root": None, "mint": None})
    assert warnings == []
    assert questions[2] == api.Question(
        module="fake", key="branches", text="Which branches?", choices=(), suggested=None,
        preset=None, after=("push", "yes"), later="fake push enable --branch NAME")
    assert questions[0].as_dict()["choices"] == ["yes", "no"]
