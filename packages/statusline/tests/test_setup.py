"""Setup, the verb `use`, and teardown, at a fake terminal and with nobody at it.

Claude's folder is the test's own (the fence points `CLAUDE_CONFIG_DIR` into its tmp).
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys

import pytest

from moxi import config, paths
from moxi import exit as codes
from moxi_statusline import settings, setup, switch

OWN = {"type": "command", "command": "/home/u/.claude/line.sh"}
OLD_RENDER = "/old/env/bin/moxi-statusline-render"


def settings_file():
    return paths.claude_home() / "settings.json"


def write_settings(data) -> bytes:
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data if isinstance(data, bytes) else (json.dumps(data, indent=2) + "\n").encode()
    path.write_bytes(raw)
    return raw


def line():
    return json.loads(settings_file().read_bytes()).get("statusLine")


class Terminal:
    """A fake terminal: answers in turn, the questions it was asked, the lines said."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.asked: list[str] = []
        self.said: list[str] = []

    def ask(self, question: str) -> str:
        self.asked.append(question)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def say(self, text: str) -> None:
        self.said.append(text)


def never(question: str) -> str:
    raise AssertionError(f"asked {question!r}")


@pytest.fixture
def nobody(monkeypatch):
    """Nobody at the keyboard: stdin is no terminal."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))


# -- setup ------------------------------------------------------------------------------

def test_no_line_answered_yes_switches_with_the_default_items_and_records_it():
    write_settings({})
    term = Terminal("")
    assert setup.setup(ask=term.ask, say=term.say) == 0
    assert term.asked == [setup.QUESTION]
    assert term.said == [switch.SWITCHED]
    assert switch.is_moxis(line())
    assert setup.recorded()["answer"] == "switch"
    assert settings.load()["show"] == setup.default_show()


def test_no_line_answered_no_then_a_second_install_asks_nothing():
    raw = write_settings({})
    term = Terminal("n")
    assert setup.setup(ask=term.ask, say=term.say) == 0
    assert term.said == [setup.NOT_NOW]
    assert setup.recorded()["answer"] == "no"
    assert settings_file().read_bytes() == raw
    again = Terminal()
    assert setup.setup(ask=never, say=again.say) == 0
    assert again.said == [setup.KEPT]
    assert settings_file().read_bytes() == raw


def test_an_answer_neither_yes_nor_no_is_asked_again():
    write_settings({})
    term = Terminal("maybe", "yes")
    assert setup.setup(ask=term.ask, say=term.say) == 0
    assert len(term.asked) == 2 and term.asked[1].startswith("'maybe' is neither yes nor no")
    assert switch.is_moxis(line())


def test_input_that_ends_records_nothing():
    write_settings({})
    term = Terminal()
    assert setup.setup(ask=term.ask, say=term.say) == 0
    assert term.said[-1] == setup.NOT_NOW
    assert setup.recorded() is None
    assert line() is None


def test_keep_recorded_then_a_second_install_neither_asks_nor_offers(monkeypatch):
    raw = write_settings({"statusLine": OWN})
    setup.record("keep")
    monkeypatch.setattr(setup, "offer", lambda *a, **k: pytest.fail("offered"))
    term = Terminal()
    assert setup.setup(ask=never, say=term.say) == 0
    assert term.said == [setup.KEPT]
    assert settings_file().read_bytes() == raw


def test_another_command_goes_to_the_offer(monkeypatch):
    raw = write_settings({"statusLine": OWN})
    calls = []
    real = setup.offer

    def spy(settings, current, **kw):
        calls.append((settings.path, current))
        return real(settings, current, **kw)

    monkeypatch.setattr(setup, "offer", spy)
    term = Terminal()
    assert setup.setup(ask=term.ask, say=term.say) == 0
    assert calls == [(settings_file(), OWN)]
    assert term.asked == [setup.DETECT_QUESTION]
    assert term.said == ["Claude Code's status line is your own (/home/u/.claude/line.sh).",
                         "", f"{switch.LEFT}."]
    assert settings_file().read_bytes() == raw
    assert setup.recorded() is None


def test_nobody_at_the_keyboard_records_nothing_and_says_how(nobody):
    raw = write_settings({})
    said = []
    assert setup.setup(say=said.append) == 0
    assert said == [setup.NOT_NOW]
    assert setup.recorded() is None
    assert settings_file().read_bytes() == raw
    write_settings({"statusLine": OWN})
    said.clear()
    assert setup.setup(say=said.append) == 0
    assert "is left as it is" in said[0] and "moxi statusline use" in said[1]
    assert setup.recorded() is None


def test_moxis_line_at_another_path_is_pointed_here_without_a_question():
    write_settings({"statusLine": {"type": "command", "command": OLD_RENDER,
                                   "refreshInterval": 2}})
    setup.record("no")
    term = Terminal()
    assert setup.setup(ask=never, say=term.say) == 0
    assert line() == {"type": "command", "command": switch.render_command(),
                      "refreshInterval": 2}
    assert term.said == [f"Claude Code's status line now runs {switch.render_command()}"]
    term = Terminal()
    assert setup.setup(ask=never, say=term.say) == 0
    assert term.said[0].startswith("Claude Code already uses Moxi's status line")


def test_unreadable_settings_exit_1_and_change_nothing():
    raw = write_settings(b"{not json")
    term = Terminal()
    assert setup.setup(ask=never, say=term.say) == 1
    assert "is not plain JSON" in term.said[0]
    assert term.said[1] == f"{switch.LEFT}."
    assert settings_file().read_bytes() == raw
    assert setup.recorded() is None


def test_the_record_is_private_and_names_the_answer_and_time():
    setup.record("keep")
    path = setup.asked_path()
    if os.name == "posix":  # Windows has no mode bits
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    data = json.loads(open(path).read())
    assert data["answer"] == "keep" and data["at"].endswith("Z")
    with pytest.raises(ValueError):
        setup.record("maybe")


# -- use --------------------------------------------------------------------------------

def test_use_asks_again_after_no_was_recorded():
    write_settings({})
    setup.record("no")
    term = Terminal("y")
    assert setup.use(ask=term.ask, say=term.say) == 0
    assert term.asked == [setup.QUESTION]
    assert switch.is_moxis(line())
    assert setup.recorded()["answer"] == "switch"


def test_use_answered_no_leaves_the_line_and_records_it():
    raw = write_settings({})
    term = Terminal("no")
    assert setup.use(ask=term.ask, say=term.say) == 0
    assert term.said == [f"{switch.LEFT}."]
    assert setup.recorded()["answer"] == "no"
    assert settings_file().read_bytes() == raw


def test_use_with_nobody_at_the_keyboard_switches(nobody):
    write_settings({})
    said = []
    assert setup.use(say=said.append) == 0
    assert said == [switch.SWITCHED]
    assert switch.is_moxis(line())


SAVED = ["model", "row", "session"]


@pytest.mark.parametrize("who", ["use at the keyboard", "use with nobody", "setup"])
def test_a_switch_over_no_line_keeps_the_saved_items(who, monkeypatch):
    # Items saved the way the page saves them, then the line put back to none.
    write_settings({})
    config.set_many("statusline", [("show", SAVED)], by="ui")
    if who == "use with nobody":
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        assert setup.use(say=print) == 0
    else:
        run = setup.use if who.startswith("use") else setup.setup
        assert run(ask=Terminal("y").ask, say=print) == 0
    assert switch.is_moxis(line())
    assert settings.load()["show"] == SAVED


def test_use_over_moxis_line_points_it_here():
    write_settings({"statusLine": {"type": "command", "command": OLD_RENDER}})
    term = Terminal()
    assert setup.use(ask=never, say=term.say) == 0
    assert line()["command"] == switch.render_command()


def test_use_over_another_command_calls_the_offer():
    raw = write_settings({"statusLine": OWN})
    term = Terminal()
    assert setup.use(ask=term.ask, say=term.say) == 0
    assert term.asked == [setup.DETECT_QUESTION]
    assert "is your own" in term.said[0]
    assert settings_file().read_bytes() == raw


def test_use_over_unreadable_settings_is_refused():
    write_settings(b"[]")
    term = Terminal()
    assert setup.use(ask=never, say=term.say) == codes.REFUSED
    assert term.said[0].endswith("is not a JSON object")


# -- teardown ---------------------------------------------------------------------------

def test_teardown_restores_when_moxis_with_a_saved_line():
    write_settings({"statusLine": OWN, "theme": "dark"})
    assert switch.switch(None, by="test").ok
    said = []
    assert setup.teardown(say=said.append) == 0
    assert line() == OWN
    assert said == ["Claude Code's status line is your old one again: "
                    "/home/u/.claude/line.sh"]


def test_teardown_removes_moxis_line_with_none_saved():
    write_settings({"statusLine": {"type": "command", "command": OLD_RENDER},
                    "theme": "dark"})
    said = []
    assert setup.teardown(say=said.append) == 0
    assert json.loads(settings_file().read_bytes()) == {"theme": "dark"}
    assert "the setting is removed" in said[0]


def test_teardown_restores_none_by_removing_the_setting():
    write_settings({"theme": "dark"})
    assert switch.switch(None, by="test").ok
    assert setup.teardown(say=lambda text: None) == 0
    assert json.loads(settings_file().read_bytes()) == {"theme": "dark"}


def test_teardown_leaves_another_line_alone_and_says_its_pieces_stop():
    raw = write_settings({"statusLine": OWN})
    said = []
    assert setup.teardown(say=said.append) == 0
    assert settings_file().read_bytes() == raw
    assert said == ["Claude Code's status line is your own (/home/u/.claude/line.sh), "
                    "left as it is; any moxi-statusline-render --only pieces it calls "
                    "stop working once statusline is uninstalled"]


def test_teardown_with_no_line_says_so():
    said = []
    assert setup.teardown(say=said.append) == 0
    assert said == ["Claude Code has no status line, so there is nothing to put back"]
    assert not settings_file().exists()


def test_teardown_over_unreadable_settings_stops_the_uninstall():
    raw = write_settings(b"{bad")
    said = []
    assert setup.teardown(say=said.append) == 1
    assert "--skip-teardown" in said[1]
    assert settings_file().read_bytes() == raw


def test_the_announcement_names_setup_and_teardown():
    from moxi import modules

    found = modules.load("statusline")
    assert found.setup == "moxi_statusline.setup:setup"
    assert found.teardown == "moxi_statusline.setup:teardown"
