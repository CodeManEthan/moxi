"""touched_files: which files a write call touches, read from the call's input."""

from __future__ import annotations

from collections.abc import Mapping

import moxi_turns as turns
from moxi_turns.guards import TOUCH_KINDS, ToolCall, TouchedFile, touched_files


def _call(harness: str, tool: str, tool_input: object) -> ToolCall:
    return ToolCall(harness, "s", None, tool, input=tool_input)


def test_the_names_reach_the_package():
    for name in ("TouchedFile", "touched_files"):
        assert getattr(turns, name) is globals()[name]
        assert name in turns.__all__
    assert TOUCH_KINDS == ("write", "add", "update", "delete", "moved-from", "moved-to")


def test_claude_write_and_edit_give_one_write_entry():
    assert touched_files(_call("claude", "Write", {"file_path": "a/b.txt", "content": "hi"})) \
        == (TouchedFile("a/b.txt", "write", "hi"),)
    edit = {"file_path": "/w/x.py", "old_string": "a", "new_string": "b"}
    assert touched_files(_call("claude", "Edit", edit)) == (TouchedFile("/w/x.py", "write", "b"),)


def test_opencode_write_and_edit_read_their_own_fields():
    assert touched_files(_call("opencode", "write", {"filePath": "p", "content": "c"})) \
        == (TouchedFile("p", "write", "c"),)
    assert touched_files(_call("opencode", "edit", {"filePath": "p", "newString": "n"})) \
        == (TouchedFile("p", "write", "n"),)


PATCH = """*** Begin Patch
*** Add File: new.txt
+one
+two
*** Update File: old.py
@@
-x
+y
 context
*** Delete File: gone.md
+not placed
*** Update File: from.py
*** Move to: to.py
@@
+moved
*** End Patch"""


def test_a_codex_patch_gives_one_entry_per_section_and_two_for_a_move():
    assert touched_files(_call("codex", "apply_patch", {"command": PATCH})) == (
        TouchedFile("new.txt", "add", "one\ntwo"),
        TouchedFile("old.py", "update", "y"),
        TouchedFile("gone.md", "delete", ""),
        TouchedFile("from.py", "moved-from", "moved"),
        TouchedFile("to.py", "moved-to", "moved"),
    )


def test_a_crlf_patch_reads_as_the_same():
    crlf = PATCH.replace("\n", "\r\n")
    assert touched_files(_call("codex", "apply_patch", {"command": crlf})) \
        == touched_files(_call("codex", "apply_patch", {"command": PATCH}))


def test_a_move_to_with_no_update_before_it_is_skipped():
    patch = "*** Move to: x\n*** Add File: a\n+1\n*** Move to: y\n"
    assert touched_files(_call("codex", "apply_patch", {"command": patch})) \
        == (TouchedFile("a", "add", "1"),)


def test_an_unknown_tool_or_wrong_input_gives_nothing():
    assert touched_files(_call("claude", "Bash", {"command": "rm x"})) == ()
    assert touched_files(_call("claude", "apply_patch", {"command": PATCH})) == ()
    assert touched_files(_call("claude", "Write", {"file_path": 3, "content": "c"})) == ()
    assert touched_files(_call("claude", "Write", {"file_path": "p"})) == ()
    assert touched_files(_call("claude", "Write", ["not", "a", "mapping"])) == ()
    assert touched_files(_call("codex", "apply_patch", {"command": None})) == ()
    assert touched_files(object()) == ()
    assert touched_files(None) == ()


class _Raising(Mapping):
    """Input that is a mapping but raises when a field is read."""

    def __getitem__(self, key):
        raise RuntimeError("unreadable")

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0


def test_input_that_raises_when_read_gives_nothing_and_never_raises():
    assert touched_files(_call("claude", "Write", _Raising())) == ()
    assert touched_files(_call("codex", "apply_patch", _Raising())) == ()
