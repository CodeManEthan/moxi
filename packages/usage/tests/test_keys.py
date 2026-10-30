"""The index's request keys."""

import hashlib

from moxi_usage import keys


def test_a_key_is_blake2b_8_of_the_harness_and_id_read_signed_big_endian():
    raw = hashlib.blake2b(b"claude:req_011", digest_size=8).digest()
    assert raw.hex() == "ee4cb9bb19654b17"
    key = keys.request_key("claude", "req_011", lane="claude", path="/a")
    assert key == int.from_bytes(raw, "big", signed=True) == -1275440381216601321
    assert key < 0                      # the top bit is set, so the signed read matters


def test_keys_fit_sqlite_integers():
    for index in range(500):
        key = keys.request_key("codex", f"resp_{index}", lane="codex", path="/r")
        assert -(2 ** 63) <= key < 2 ** 63


def test_the_harness_namespaces_the_key():
    claude = keys.request_key("claude", "req_011", lane="claude", path="/a")
    codex = keys.request_key("codex", "req_011", lane="codex", path="/a")
    assert claude != codex
    assert codex == -6503948835821925517


def test_a_request_id_that_is_not_file_local_is_the_same_key_in_every_file():
    one = keys.request_key("claude", "req_011", lane="claude", path="/live/s.jsonl")
    two = keys.request_key("claude", "req_011", lane="archive:old", path="/old/s.jsonl")
    assert one == two


def test_a_file_local_id_is_qualified_by_lane_and_path():
    at = keys.request_key("claude", "line:7", lane="claude", path="/a/s.jsonl")
    assert at == 3279919316312035418
    assert at != keys.request_key("claude", "line:7", lane="claude", path="/b/s.jsonl")
    assert at != keys.request_key("claude", "line:7", lane="archive:x", path="/a/s.jsonl")
    assert at == keys.request_key("claude", "line:7", lane="claude", path="/a/s.jsonl")
    codex_a = keys.request_key("codex", "tc:main:12", lane="codex", path="/r1.jsonl")
    codex_b = keys.request_key("codex", "tc:main:12", lane="codex", path="/r2.jsonl")
    assert codex_a != codex_b


def test_lane_and_path_cannot_run_together():
    one = keys.namespaced("claude", "line:1", lane="archive:a", path="b:c")
    two = keys.namespaced("claude", "line:1", lane="archive:a:b", path="c")
    assert one != two


def test_which_ids_are_file_local():
    assert keys.is_file_local("line:3")
    assert keys.is_file_local("tc:thread:4")
    assert not keys.is_file_local("req_011")
    assert not keys.is_file_local("msg:msg_01")
    assert not keys.is_file_local("uuid:0190")
    assert not keys.is_file_local("prt_01")
