"""The Cloudflare writer: the exact file with its digest line, 0600, the catch-all last,
every string quoted, the refusals, the credential never opened, an atomic replace, and
hand edits told from a file left behind."""

from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import stat

import pytest

from moxi_tunnel import cloudflare
from moxi_tunnel.errors import Refused

ID = "6ff42ae2-765d-4adf-8112-31c55c1551ef"


def _render(tmp_path, pairs, creds=None):
    if creds is None:
        creds = tmp_path / "creds.json"
        creds.write_text("{}")
    return cloudflare.render(pairs, tunnel_id=ID, credentials=creds, metrics_port=20241)


def test_the_exact_file(tmp_path):
    creds = tmp_path / "creds.json"
    creds.write_text("{}")
    text = _render(tmp_path, [("a.example.com", 8461), ("b.example.com", 9000)], creds)
    rest = (f'tunnel: "{ID}"\n'
            f'credentials-file: "{creds}"\n'
            'metrics: "127.0.0.1:20241"\n'
            "ingress:\n"
            '  - hostname: "a.example.com"\n'
            '    service: "http://127.0.0.1:8461"\n'
            "    originRequest:\n"
            '      httpHostHeader: "a.example.com"\n'
            '  - hostname: "b.example.com"\n'
            '    service: "http://127.0.0.1:9000"\n'
            "    originRequest:\n"
            '      httpHostHeader: "b.example.com"\n'
            '  - service: "http_status:404"\n')
    assert text == f"# moxi-tunnel sha256:{hashlib.sha256(rest.encode()).hexdigest()}\n{rest}"
    assert text.rstrip().splitlines()[-1] == '  - service: "http_status:404"'


def test_every_string_is_quoted_so_a_path_cant_change_the_parse(tmp_path):
    creds = tmp_path / "a #b: c\\d 'e.json"
    creds.write_text("{}")
    text = _render(tmp_path, [("a.example.com", 8461)], creds)
    values = {}
    for line in text.splitlines()[1:]:
        key, sep, value = line.strip().removeprefix("- ").partition(": ")
        if value:
            values.setdefault(key, []).append(json.loads(value))
    assert values["credentials-file"] == [str(creds)]
    assert values["tunnel"] == [ID]
    assert values["service"] == ["http://127.0.0.1:8461", "http_status:404"]
    assert cloudflare.parse_rules(text) == {"a.example.com": "http://127.0.0.1:8461"}


@pytest.mark.parametrize("tunnel_id", ["", "not-a-uuid", ID + "0", ID.replace("-", "")])
def test_a_tunnel_id_that_is_not_a_uuid_is_refused(tmp_path, tunnel_id):
    creds = tmp_path / "c.json"
    creds.write_text("{}")
    with pytest.raises(Refused, match="not a UUID"):
        cloudflare.render([], tunnel_id=tunnel_id, credentials=creds, metrics_port=1)


@pytest.mark.parametrize("host", [
    "localhost", "-a.example.com", "a-.example.com", "a..example.com", "a_b.example.com",
    "a.example.com\n", "a b.example.com", "x" * 64 + ".example.com",
    ".".join(["abcdefghi"] * 26) + ".com", "", "é.example.com", "a.example.com/x"])
def test_a_hostname_that_fails_the_label_check_is_refused(host):
    with pytest.raises(Refused):
        cloudflare.check_hostname(host)


def test_a_good_hostname_is_kept_in_the_one_host_form():
    assert cloudflare.check_hostname("App.Example.COM.") == "app.example.com"
    assert cloudflare.check_hostname("x" * 63 + ".example.com") == "x" * 63 + ".example.com"
    assert cloudflare.check_hostname("a-1.b2.example.com") == "a-1.b2.example.com"


def test_a_path_with_a_control_character_is_refused(tmp_path):
    with pytest.raises(Refused, match="control character"):
        cloudflare.render([], tunnel_id=ID, credentials=str(tmp_path / "a\x07.json"),
                          metrics_port=1)
    with pytest.raises(Refused, match="not set"):
        cloudflare.render([], tunnel_id=ID, credentials=None, metrics_port=1)


def test_credentials_that_are_missing_or_not_a_file_are_a_problem_the_writer_skips(tmp_path):
    assert "can't be found" in cloudflare.credentials_problem(str(tmp_path / "nope.json"))
    assert "not a regular file" in cloudflare.credentials_problem(str(tmp_path))
    # The writer checks only the path's form: taking a rule out needs no credentials file.
    assert str(tmp_path / "nope.json") in _render(tmp_path, [], tmp_path / "nope.json")


def test_the_credential_is_never_opened(tmp_path, monkeypatch, cf, units):
    opened = []
    real_open, real_io_open = builtins.open, io.open

    def watch(real):
        def opener(file, *args, **kwargs):
            if str(file) == str(cf):
                opened.append(file)
            return real(file, *args, **kwargs)
        return opener

    monkeypatch.setattr(builtins, "open", watch(real_open))
    monkeypatch.setattr(io, "open", watch(real_io_open))
    text = cloudflare.render([("a.example.com", 1)], tunnel_id=ID, credentials=cf,
                             metrics_port=1)
    assert str(cf) in json.loads(text.splitlines()[2].partition(": ")[2])
    assert cloudflare.setup_problems() == []
    assert opened == []
    assert stat.S_IMODE(cf.stat().st_mode) == 0  # unreadable, and nothing needed to read it


def test_the_file_is_written_0600_and_replaced_atomically(cf):
    path = cloudflare.config_path()
    cloudflare.write_file("one\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    first = path.stat().st_ino
    cloudflare.write_file("two\n")
    assert path.read_text() == "two\n"
    assert path.stat().st_ino != first  # a new file renamed over the old, not rewritten
    assert sorted(os.listdir(path.parent)) == ["cloudflared.yml"]


def test_a_file_whose_mode_changed_by_hand_is_not_written(cf):
    path = cloudflare.config_path()
    cloudflare.write_file("one\n")
    path.chmod(0o644)
    with pytest.raises(cloudflare.StepFailed) as caught:
        cloudflare.write_file("two\n")
    assert caught.value.file and "0644" in caught.value.reason
    assert path.read_text() == "one\n"


def test_the_digest_line_tells_a_hand_edit_from_a_file_left_behind(tmp_path, cf):
    assert cloudflare.read_file().text is None
    text = _render(tmp_path, [("a.example.com", 8461)])
    cloudflare.write_file(text)
    got = cloudflare.read_file()
    assert not got.by_hand and got.digest == text.split(":")[1].split("\n")[0]
    cloudflare.write_file(text.replace("8461", "8462"))
    assert cloudflare.read_file().by_hand
    cloudflare.write_file(text.split("\n", 1)[1])
    assert cloudflare.read_file().by_hand  # no digest line at all
