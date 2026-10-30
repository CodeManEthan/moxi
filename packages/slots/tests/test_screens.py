"""The screens as data and the matcher, over every captured fixture and a few made ones.

The fixtures in `fixtures/screens/` are real Claude Code captures, `capture-pane -p`
(`.plain.txt`) or `-p -e` (`.ansi.txt`), named `claude-<version>-<what>-<width>[x<height>]`.
Each runs against the matcher on every run; a new version's capture is added here.

A capture is cropped to a few rows above the last full-width rule before it is added: the
matcher reads only below that rule, and the history above it carries the capturing
machine's paths, prompts and status line. Below the rule, a row that names the capturing
user's own things (paths, skills, a custom status line) is rewritten or taken out.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from moxi_slots import api, screens
from moxi_slots.screens import (CLAUDE_DEV_CHANNELS, CLAUDE_EXIT_DIALOG, CLAUDE_TRUST_FOLDER,
                                SCREENS, Items, Screen, bare, find, match, plain, region)

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/screens*")

FIXTURES = Path(__file__).parent / "fixtures" / "screens"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def width_of(name: str) -> int:
    return int(re.search(r"-(\d+)(?:x\d+)?\.(?:plain|ansi)\.txt$", name).group(1))


#: name -> (screen expected present or None, full, the marked row's text, its why's start)
EXPECTED = {
    "claude-2.1.314-trust-120x40.plain.txt": (CLAUDE_TRUST_FOLDER, True, "No, exit", ""),
    "claude-2.1.314-trust-120x40.ansi.txt": (CLAUDE_TRUST_FOLDER, True, "No, exit", ""),
    "claude-2.1.314-trust-on-option-120x40.plain.txt":
        (CLAUDE_TRUST_FOLDER, True, "Yes, I trust this folder", ""),
    "claude-2.1.314-trust-on-option-120x40.ansi.txt":
        (CLAUDE_TRUST_FOLDER, True, "Yes, I trust this folder", ""),
    "claude-2.1.314-dev-channels-120x40.plain.txt":
        (CLAUDE_DEV_CHANNELS, True, "I am using this for local development", ""),
    "claude-2.1.314-dev-channels-120x40.ansi.txt":
        (CLAUDE_DEV_CHANNELS, True, "I am using this for local development", ""),
    "claude-2.1.314-prompt-120x40.ansi.txt": (None, False, None, ""),
    "claude-2.1.314-exit-shell-120x40.plain.txt":
        (CLAUDE_EXIT_DIALOG, True, "Exit and stop tasks", ""),
    "claude-2.1.314-exit-shell-120x40.ansi.txt":
        (CLAUDE_EXIT_DIALOG, True, "Exit and stop tasks", ""),
    "claude-2.1.296-exit-shell-160.ansi.txt":
        (CLAUDE_EXIT_DIALOG, True, "Exit and stop tasks", ""),
    "claude-2.1.296-exit-monitor-160.ansi.txt":
        (CLAUDE_EXIT_DIALOG, True, "Exit and stop tasks", ""),
    "claude-2.1.296-exit-shell-and-monitor-160.plain.txt":
        (CLAUDE_EXIT_DIALOG, True, "Exit and stop tasks", ""),
    "claude-2.1.296-exit-subagent-160.plain.txt":
        (CLAUDE_EXIT_DIALOG, False, "Exit and stop tasks", "the item row 'subagent"),
    "claude-2.1.289-prompt-200.ansi.txt": (None, False, None, ""),
    "claude-2.1.289-permission-200.ansi.txt": (None, False, None, ""),
    "claude-2.1.289-five-shells-200.ansi.txt": (None, False, None, ""),
    "claude-2.1.289-slash-menu-200.ansi.txt": (None, False, None, ""),
}


def test_every_fixture_has_an_expectation():
    assert sorted(p.name for p in FIXTURES.iterdir()) == sorted(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_matcher_over_each_fixture(name):
    want, full, marked_text, why = EXPECTED[name]
    held = find(SCREENS, fixture(name), width_of(name))
    if want is None:
        assert held is None
        return
    assert held.screen is want
    assert held.full is full, held.why
    assert held.why.startswith(why)
    assert bare(held.rows[held.marked]) == marked_text
    assert bare(held.rows[held.option]) == want.option


def test_the_trust_capture_needs_one_down():
    held = match(CLAUDE_TRUST_FOLDER, fixture("claude-2.1.314-trust-120x40.plain.txt"), 120)
    assert held.option - held.marked == 1


def test_the_plain_and_colour_captures_give_one_region():
    for stem in ("trust-120x40", "dev-channels-120x40", "exit-shell-120x40"):
        a = region(fixture(f"claude-2.1.314-{stem}.plain.txt"), 120)
        b = region(fixture(f"claude-2.1.314-{stem}.ansi.txt"), 120)
        assert a == b


def test_colour_between_the_words_of_a_phrase_still_matches():
    text = fixture("claude-2.1.314-trust-120x40.ansi.txt")
    assert "Accessing workspace:" not in text
    assert "Accessing workspace:" in plain(text)


def test_a_rule_narrower_than_the_pane_is_not_full_width():
    text = fixture("claude-2.1.314-dev-channels-120x40.plain.txt")
    assert region(text, 121) is None
    assert find(SCREENS, text, 121) is None
    assert find(SCREENS, text, 120).screen is CLAUDE_DEV_CHANNELS


def test_a_dialog_above_a_later_rule_is_history_and_does_not_match():
    text = fixture("claude-2.1.296-exit-shell-160.ansi.txt").rstrip("\n")
    text += "\n" + "─" * 160 + "\n❯ \n" + "─" * 160 + "\n  ? for shortcuts\n"
    assert find(SCREENS, text, 160) is None


def dialog(*rows: str, width: int = 40) -> str:
    return "\n".join(["some history", "─" * width, *rows, ""])


TRUST_ROWS = (" Accessing workspace:", "", " ❯ No, exit", "   Yes, I trust this folder", "",
              " Enter to confirm · Esc to cancel")


def test_the_marker_position_is_not_part_of_the_match():
    held = match(CLAUDE_TRUST_FOLDER, dialog(*TRUST_ROWS), 40)
    assert held.full and (held.marked, held.option) == (2, 3)


def test_copy_mode_is_never_a_full_match():
    held = match(CLAUDE_TRUST_FOLDER, dialog(*TRUST_ROWS), 40, in_mode=True)
    assert held.present and not held.full and "copy mode" in held.why


def test_two_marked_rows_or_none_is_not_a_full_match():
    two = [r.replace("   Yes", " ❯ Yes") for r in TRUST_ROWS]
    assert "2 rows are marked" in match(CLAUDE_TRUST_FOLDER, dialog(*two), 40).why
    none = [r.replace("❯", " ") for r in TRUST_ROWS]
    assert "0 rows are marked" in match(CLAUDE_TRUST_FOLDER, dialog(*none), 40).why


def test_a_missing_option_row_is_not_a_full_match():
    rows = [r.replace("Yes, I trust this folder", "Yes, trust it") for r in TRUST_ROWS]
    held = match(CLAUDE_TRUST_FOLDER, dialog(*rows), 40)
    assert held.present and not held.full and "no row is the option" in held.why


def test_an_option_row_with_extra_words_is_not_the_option():
    rows = [r.replace("Yes, I trust this folder", "Yes, I trust this folder and its parent")
            for r in TRUST_ROWS]
    assert not match(CLAUDE_TRUST_FOLDER, dialog(*rows), 40).full


def test_phrases_out_of_order_are_not_present():
    rows = (" Enter to confirm · Esc to cancel", " Accessing workspace:", " ❯ No, exit",
            "   Yes, I trust this folder")
    held = match(CLAUDE_TRUST_FOLDER, dialog(*rows), 40)
    assert not held.present and "'Enter to confirm'" in held.why


def test_no_rule_no_region():
    held = match(CLAUDE_TRUST_FOLDER, "\n".join(TRUST_ROWS), 40)
    assert not held.present and held.why == "no full-width rule on screen"


def test_a_no_break_space_reads_as_a_space():
    rows = [r.replace("❯ No", "❯ No") for r in TRUST_ROWS]
    held = match(CLAUDE_TRUST_FOLDER, dialog(*rows), 40)
    assert held.full and bare(held.rows[held.marked]) == "No, exit"


def test_an_osc_link_is_removed():
    assert plain("\x1b]8;;https://example.com\x07Security guide\x1b]8;;\x07") == \
        "Security guide"
    assert plain("\x1b]8;;https://example.com\x1b\\guide\x1b]8;;\x1b\\") == "guide"


EXIT_ROWS = ("  Background work is running", "", "  The following will stop when you exit:",
             "", "  shell · sleep 600", "  monitor · tail -f log", "",
             "  ❯ 1. Exit and stop tasks", "    2. Move to background and exit",
             "    3. Stay", "", "  Enter to confirm · Esc to cancel")


def test_exit_dialog_items_of_allowed_kinds():
    assert match(CLAUDE_EXIT_DIALOG, dialog(*EXIT_ROWS, width=50), 50).full


@pytest.mark.parametrize("row", ["  subagent · Run it", "  sleep 600", "  shells · x"])
def test_exit_dialog_item_of_another_kind_is_not_a_full_match(row):
    rows = list(EXIT_ROWS)
    rows[5] = row
    held = match(CLAUDE_EXIT_DIALOG, dialog(*rows, width=50), 50)
    assert held.present and not held.full and "the item row" in held.why


def test_the_index_is_dropped_from_an_option_row():
    assert bare("  ❯ 1. Exit and stop tasks") == "Exit and stop tasks"
    assert bare("    12. Stay") == "Stay"
    assert bare(" ❯ No, exit") == "No, exit"


def test_the_three_screens():
    assert CLAUDE_EXIT_DIALOG.move is False and CLAUDE_EXIT_DIALOG.then == "ended"
    assert CLAUDE_EXIT_DIALOG.items == Items("will stop when you exit", ("shell", "monitor"))
    assert CLAUDE_TRUST_FOLDER.move and CLAUDE_TRUST_FOLDER.then == "gone"
    assert CLAUDE_DEV_CHANNELS.move and CLAUDE_DEV_CHANNELS.then == "gone"
    assert {s.marker for s in SCREENS} == {"❯"}


def test_the_api_re_exports_the_screens():
    assert api.CLAUDE_TRUST_FOLDER is CLAUDE_TRUST_FOLDER
    assert api.CLAUDE_DEV_CHANNELS is CLAUDE_DEV_CHANNELS
    assert api.CLAUDE_EXIT_DIALOG is CLAUDE_EXIT_DIALOG
    assert api.Screen is Screen


def test_a_screen_checks_its_then():
    with pytest.raises(ValueError):
        Screen("X", ("a",), "b", move=False, then="maybe")
    with pytest.raises(ValueError):
        Screen("X", (), "b", move=False, then="gone")


def test_the_first_present_screen_is_returned():
    text = fixture("claude-2.1.314-trust-120x40.plain.txt")
    assert find([CLAUDE_DEV_CHANNELS, CLAUDE_TRUST_FOLDER], text, 120).screen \
        is CLAUDE_TRUST_FOLDER
    assert find([CLAUDE_DEV_CHANNELS], text, 120) is None
    assert screens.find([], text, 120) is None
