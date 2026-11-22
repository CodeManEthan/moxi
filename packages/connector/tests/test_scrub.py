"""The answer scrub: control and format characters out of every answer, text and
structured strings at any depth, for every provider."""

from __future__ import annotations

import asyncio
import unicodedata

import pytest
from kit import bearer, call, provider, request

from moxi_connector.provider import Result, Tool
from moxi_connector.scrub import scrub_text, scrub_value

#: One sample of each class the scrub removes, by name.
REMOVED = {
    "NUL": "\x00", "CR": "\r", "ESC": "\x1b", "vertical tab": "\x0b", "form feed": "\x0c",
    "DEL": "\x7f", "C1 PAD": "\x80", "C1 NEL": "\x85", "C1 CSI": "\x9b",
    "zero-width space": "​", "zero-width joiner": "‍", "soft hyphen": "­",
    "word joiner": "⁠", "byte order mark": "﻿", "right-to-left override": "‮",
    "left-to-right isolate": "⁦", "tag begin": "\U000e0001", "tag letter A": "\U000e0041",
    "cancel tag": "\U000e007f",
}


def _removed(ch: str) -> bool:
    cp = ord(ch)
    return ((cp < 0x20 and ch not in "\n\t") or 0x7F <= cp < 0xA0
            or unicodedata.category(ch) == "Cf")


@pytest.mark.parametrize("name", sorted(REMOVED))
def test_each_class_is_removed_from_text(name):
    ch = REMOVED[name]
    assert scrub_text(f"sk{ch}live{ch}") == "sklive"


def test_newline_tab_and_ordinary_text_stay():
    text = "a\tb\nc café 東京  nbsp 🙂 «quote»"
    assert scrub_text(text) == text


def test_every_code_point_is_removed_exactly_when_its_class_says():
    wrong = [hex(cp) for cp in range(0x110000)
             if not 0xD800 <= cp < 0xE000
             and (scrub_text(chr(cp)) == "") != _removed(chr(cp))]
    assert wrong == []


def test_nested_structured_strings_and_keys_are_scrubbed_and_other_values_kept():
    value = {"a​": ["x\x1by", {"deep": ("t\U000e0041ag", 3, None, True)}],
             "n": 1.5, "s": "plain\ttab\nline"}
    assert scrub_value(value) == {"a": ["xy", {"deep": ["tag", 3, None, True]}],
                                  "n": 1.5, "s": "plain\ttab\nline"}


# -- through the door ----------------------------------------------------------------------

HIDDEN = "".join(REMOVED.values())


def _dirty_tool() -> Tool:
    async def dirty(args, ctx):
        if args.get("refuse"):
            return Result.refused("no‮", f"Refused{HIDDEN} here.", why=f"w{HIDDEN}hy")
        return Result(f"From{HIDDEN} a note.\tcol\nnext",
                      {"hits": [{"text": f"se{HIDDEN}cret", "line": 3}],
                       f"ke{HIDDEN}y": [f"x{HIDDEN}"]})

    return Tool("dirty", "Answers hidden characters.",
                {"type": "object", "properties": {"refuse": {"type": "boolean"}}}, dirty)


def test_a_providers_answer_reaches_the_caller_scrubbed(front, fake):
    async def main():
        f = await front(providers={"demo": provider(fake, _dirty_tool())})
        got = (await request(f, "POST", "/mcp", call("dirty", {}), headers=bearer())).json()
        result = got["result"]
        assert result["content"][0]["text"] == "From a note.\tcol\nnext"
        assert result["structuredContent"] == {"hits": [{"text": "secret", "line": 3}],
                                               "key": ["x"]}
        got = (await request(f, "POST", "/mcp", call("dirty", {"refuse": True}),
                             headers=bearer())).json()
        result = got["result"]
        assert result["isError"] and result["content"][0]["text"] == "Refused here."
        assert result["structuredContent"] == {"refusal": "no", "why": "why"}
        await f.close()

    asyncio.run(main())


# -- the scrub never changes what the chat redaction returns -------------------------------

def _chat_redact():
    secrets = pytest.importorskip("moxi_secrets")
    found = getattr(secrets, "chat_redact", None)
    if found is None:
        pytest.skip("this moxi_secrets has no chat_redact")
    return found


@pytest.mark.parametrize("name", sorted(REMOVED))
def test_the_scrub_leaves_chat_redacts_output_unchanged(name):
    chat_redact = _chat_redact()
    ch = REMOVED[name]
    for text in (f"api_key = \"sk{ch}Zx9Qw3Lp0Vb7Nm2Kc5Rt8Yh1Ug4Jd6Fs\"\n",
                 f"plain{ch}words{ch}\tand a tab\n", f"{ch}{ch}"):
        out = chat_redact(text)
        assert scrub_text(out) == out, (name, out)
