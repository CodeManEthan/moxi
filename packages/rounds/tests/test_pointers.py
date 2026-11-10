"""Pointers: the four shapes one may have, and what has none of them."""

from __future__ import annotations

import pytest

from moxi_rounds import pointers

RIGHT = [
    ("comms:12", pointers.COMMS),
    ("comms:4611686018427393782", pointers.COMMS),
    ("0a1b2c3d@2026-01-02T03:04Z", pointers.SESSION),
    ("0A1B2C3D4E5F@2026-01-02T03:04:05Z", pointers.SESSION),
    ("https://example.com/pages/7?tab=2#answers", pointers.URL),
    ("file:design.md#rulings", pointers.FILE),
    ("file:versions/design-v2.md#q3", pointers.FILE),
]
WRONG = [
    None, "", 12, "he said so in chat", "comms:", "comms:12a", "comms:-3", "msg:12",
    "0a1b2c3@2026-01-02T03:04Z",         # seven hex digits
    "0a1b2c3g@2026-01-02T03:04Z",        # not hex
    "0a1b2c3d@2026-01-02T03:04",         # no Z: not said to be UTC
    "0a1b2c3d@2026-13-02T03:04Z",        # no such month
    "0a1b2c3d@2026-01-02T25:04Z",        # no such hour
    "0a1b2c3d@2026-01-02",
    "http://example.com/pages/7", "https://", "https:///pages/7",
    "file:design.md",                    # no anchor
    "file:design.md#", "file:#rulings",
    "file:/abs/design.md#rulings", "file:~/design.md#rulings",
    "file:../other-round/design.md#rulings", "file:versions//design.md#rulings",
    "comms:12 comms:13", " comms:12", "comms:12\n",
]


@pytest.mark.parametrize(("pointer", "shape"), RIGHT)
def test_a_pointer_of_each_of_the_four_shapes_is_one(pointer, shape):
    assert pointers.shape(pointer) == shape
    assert pointers.unmet(pointer, "a command") is None


@pytest.mark.parametrize("pointer", WRONG)
def test_what_has_none_of_the_four_shapes_is_no_pointer(pointer):
    assert pointers.shape(pointer) is None

    found = pointers.unmet(pointer, "moxi rounds rule proj-a --text TEXT --record PTR")

    assert repr(pointer) in found.reason
    for shape in ("comms:<digits>", "@<UTC time>", "https://", "file:<path"):
        assert shape in found.reason
    assert found.fix == "moxi rounds rule proj-a --text TEXT --record PTR"


def test_only_the_shape_is_checked():
    # Nothing is looked up: no such message, page or file need be there.
    assert pointers.shape("comms:999999999999") == pointers.COMMS
    assert pointers.shape("https://nowhere.invalid/") == pointers.URL
    assert pointers.shape("file:not-written-yet.md#x") == pointers.FILE
