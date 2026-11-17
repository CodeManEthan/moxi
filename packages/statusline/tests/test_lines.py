"""The line's rows from its items, with segments handed in: in this process, no hook."""

import types

import pytest
from statusline_kit import INPUT

from moxi_statusline.fields import Options
from moxi_statusline.render import lines

SEP = " | "


class Seg:
    def __init__(self, text, coloured=None):
        self.text = text
        self._coloured = coloured

    def coloured(self):
        return self._coloured if self._coloured is not None else f"<{self.text}>"


def segments(**by_name):
    """A `segment_of` that hands back a made-up module for each name given."""
    def segment_of(name):
        return by_name.get(name)
    return segment_of


def mod(**attrs):
    return types.SimpleNamespace(**attrs)


def opts(**kw):
    return Options(**{"home": None, "color": False, **kw})


def test_items_in_order_joined_by_the_separator():
    assert lines(["model", "effort", "session"], INPUT, opts(), SEP) == \
        ["Madeup 9 | high | #abcdef"]


def test_an_empty_item_is_left_out_with_its_separator():
    payload = {**INPUT, "effort": None, "session_id": ""}
    assert lines(["model", "effort", "session", "cost"], payload, opts(), SEP) == \
        ["Madeup 9 | $1.24"]


def test_row_starts_a_second_row():
    assert lines(["model", "row", "effort", "cost"], INPUT, opts(), SEP) == \
        ["Madeup 9", "high | $1.24"]


def test_an_empty_row_is_left_out():
    payload = {**INPUT, "effort": None}
    assert lines(["model", "row", "effort"], payload, opts(), SEP) == ["Madeup 9"]
    assert lines(["effort"], payload, opts(), SEP) == []


def test_from_input_gets_the_parsed_input_and_now():
    seen = []

    def from_input(payload, now):
        seen.append((payload, now))
        return Seg("a")

    out = lines(["alpha"], INPUT, opts(), SEP, segment_of=segments(alpha=mod(
        from_input=from_input, segment=lambda *a: Seg("never"))), now=99.0)
    assert out == ["a"]
    assert seen == [(INPUT, 99.0)]


def test_segment_gets_the_session_id_and_now():
    seen = []

    def segment(session_id, now=None):
        seen.append((session_id, now))
        return Seg("b")

    out = lines(["beta"], INPUT, opts(), SEP, segment_of=segments(beta=mod(segment=segment)),
                now=5.0)
    assert out == ["b"]
    assert seen == [("abcdef123456", 5.0)]


def test_segment_is_not_called_without_a_session_id():
    called = []
    out = lines(["beta"], {"model": {}}, opts(), SEP,
                segment_of=segments(beta=mod(segment=lambda *a: called.append(a))))
    assert out == []
    assert called == []


def test_colour_on_shows_coloured_and_off_shows_text():
    of = segments(alpha=mod(from_input=lambda p, n: Seg("a", "\033[31ma\033[0m")))
    assert lines(["alpha"], INPUT, opts(color=True), SEP, segment_of=of) == \
        ["\033[31ma\033[0m"]
    assert lines(["alpha"], INPUT, opts(color=False), SEP, segment_of=of) == ["a"]


def test_none_from_a_segment_shows_nothing():
    of = segments(alpha=mod(from_input=lambda p, n: None))
    assert lines(["model", "alpha", "cost"], INPUT, opts(), SEP, segment_of=of) == \
        ["Madeup 9 | $1.24"]


def test_an_absent_module_shows_nothing():
    assert lines(["model", "gamma"], INPUT, opts(), SEP, segment_of=segments()) == \
        ["Madeup 9"]


def raises(payload, now):
    raise RuntimeError("boom\nsecond line")


def exits(payload, now):
    raise SystemExit(3)


@pytest.mark.parametrize("segment", [
    mod(from_input=raises),
    mod(from_input=exits),
    mod(from_input=lambda p, n: "just text"),
    mod(from_input=lambda p, n: mod(text=7, coloured=lambda: "x")),
    mod(from_input=lambda p, n: mod(text="t", coloured=lambda: None)),
    mod(),
], ids=["raises", "exits", "a string", "text not a string", "coloured not a string",
        "no function"])
def test_a_failing_or_junk_segment_shows_nothing_and_leaves_the_rest(segment, capsys):
    of = segments(alpha=segment, beta=mod(from_input=lambda p, n: Seg("b")))
    out = lines(["model", "alpha", "beta"], INPUT, opts(color=True), SEP, segment_of=of)
    assert out == ["Madeup 9 | <b>"]
    err = capsys.readouterr().err
    assert err.startswith("moxi-statusline-render: alpha: ")
    assert err.count("\n") == 1


def test_a_segment_module_that_fails_to_load_shows_nothing(capsys):
    def segment_of(name):
        raise PermissionError("refused at import")

    assert lines(["model", "alpha"], INPUT, opts(), SEP, segment_of=segment_of) == \
        ["Madeup 9"]
    assert "alpha: PermissionError: refused at import" in capsys.readouterr().err


def test_an_unreadable_input_shows_the_base_line_with_a_question_mark_and_no_segments():
    called = []
    of = segments(alpha=mod(from_input=lambda p, n: called.append(p) or Seg("a")))
    assert lines(["dir", "model", "alpha"], None, opts(), SEP, segment_of=of) == ["?"]
    assert called == []
