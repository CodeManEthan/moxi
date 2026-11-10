"""The catalogue: the shipped list, its checks, the refreshed copy, and the install gate."""

from __future__ import annotations

import json
import socket

import pytest

import moxi.catalogue as catalogue
import moxi.cli as cli
from moxi.catalogue import CatalogueError

GOOD = """\
version = {version}

[[module]]
name = "comms"
distribution = "moxi-comms"
summary = "Messages"
{extra}"""


def text(version=2, extra=""):
    return GOOD.format(version=version, extra=extra)


# -- the shipped copy ----------------------------------------------------------------

def test_the_shipped_catalogue_lists_every_first_party_module():
    shipped = catalogue.shipped()
    assert shipped.version == 3 and shipped.source == "shipped"
    assert shipped.names() == ["cache", "comms", "connector", "launcher", "loadout", "notes",
                               "projects", "repo", "rounds", "secrets", "services",
                               "sessions", "slots", "tunnel", "turns", "ui", "usage"]
    for entry in shipped.entries:
        assert entry.distribution == f"moxi-{entry.name}"
        assert entry.summary and "\n" not in entry.summary


def test_the_refresh_url_is_the_shipped_file_on_the_public_main_branch():
    assert catalogue.URL == ("https://raw.githubusercontent.com/CodeManEthan/moxi/main/"
                             "packages/moxi/src/moxi/catalogue.toml")


def test_an_entry_is_frozen():
    entry = catalogue.shipped().get("comms")
    with pytest.raises(AttributeError):
        entry.name = "other"
    assert entry == catalogue.Entry("comms", "moxi-comms", entry.summary)


# -- the checks ----------------------------------------------------------------------

@pytest.mark.parametrize("body, why", [
    ("version = true\n", "integer version"),
    ("version = 0\n", "integer version"),
    ('version = "2"\n', "integer version"),
    ("[[module", "not TOML"),
    (text(extra='\n[[module]]\nname = "comms"\ndistribution = "moxi-comms"\n'
               'summary = "again"\n'), "twice"),
    (text().replace('"comms"', '"Comms"'), "no module name"),
    (text().replace('name = "comms"', 'name = "install"'), "no module name"),
    (text().replace("moxi-comms", "comms-moxi"), "must be moxi-comms"),
    (text().replace('"Messages"', '""'), "one non-empty line"),
    (text().replace('"Messages"', '"two\\nlines"'), "one non-empty line"),
    ("version = 2\nmodule = 3\n", "not a list"),
])
def test_a_catalogue_that_does_not_check_is_refused(body, why):
    with pytest.raises(CatalogueError, match=why):
        catalogue.parse(body, "here")


def test_bytes_that_are_not_utf8_are_refused():
    with pytest.raises(CatalogueError, match="not UTF-8"):
        catalogue.parse(b"\xff\xfe", "here")


def test_suggestions_are_the_nearest_names():
    assert catalogue.shipped().suggest("comm") == ["comms"]
    assert catalogue.shipped().suggest("sesions") == ["sessions"]
    assert catalogue.shipped().suggest("zzzz") == []


# -- the refreshed copy ---------------------------------------------------------------

def test_readers_take_the_higher_version(tmp_path):
    env = {"MOXI_HOME": str(tmp_path)}
    assert catalogue.load(env).source == "shipped"
    path = catalogue.cached_path(env)
    assert path == tmp_path / "cache" / "moxi" / "catalogue.toml"
    path.parent.mkdir(parents=True)
    path.write_text(text(version=catalogue.shipped().version))
    assert catalogue.load(env).source == "shipped"  # a tie keeps the shipped copy
    path.write_text(text(version=5))
    assert catalogue.load(env).names() == ["comms"]
    path.write_text("version = 9\n[[module]]\nname = 3\n")
    assert catalogue.load(env).source == "shipped"  # one that doesn't check is ignored


def test_refresh_keeps_a_newer_copy(tmp_path):
    env = {"MOXI_HOME": str(tmp_path)}
    asked = []

    def fetch(url):
        asked.append(url)
        return text(version=9).encode()

    kept, said = catalogue.refresh(env, fetch=fetch)
    assert asked == [catalogue.URL]
    assert kept.version == 9 and kept.names() == ["comms"]
    assert said.startswith("catalogue version 9 kept in ")
    assert catalogue.cached_path(env).read_text() == text(version=9)
    assert catalogue.load(env).version == 9


def test_refresh_leaves_the_shipped_copy_when_the_published_one_is_not_newer(tmp_path):
    env = {"MOXI_HOME": str(tmp_path)}
    have = catalogue.shipped().version
    kept, said = catalogue.refresh(env, fetch=lambda url: text(version=have).encode())
    assert kept.source == "shipped" and f"the shipped one (version {have}) stays" in said
    assert not catalogue.cached_path(env).exists()


def test_refresh_writes_nothing_when_the_fetched_copy_does_not_check(tmp_path):
    env = {"MOXI_HOME": str(tmp_path)}
    with pytest.raises(CatalogueError):
        catalogue.refresh(env, fetch=lambda url: b"version = 7\n[[module]]\nname = 1\n")
    assert not catalogue.cached_path(env).exists()


class _Response:
    def __init__(self, body):
        self.body = body

    def read(self, n):
        return self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_fetch_has_a_timeout_and_a_cap(monkeypatch):
    import urllib.error
    import urllib.request

    seen = {}

    def urlopen(request, timeout):
        seen["timeout"] = timeout
        seen["url"] = request.full_url
        return _Response(b"x" * (catalogue.CAP + 1))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(CatalogueError, match="larger than 256 KiB"):
        catalogue._fetch(catalogue.URL)
    assert seen == {"timeout": 10.0, "url": catalogue.URL}

    def not_found(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", not_found)
    with pytest.raises(CatalogueError, match=r"not published yet \(404\)"):
        catalogue._fetch(catalogue.URL)


def test_no_other_command_uses_the_network(monkeypatch, capsys, site):
    """`moxi modules`, `--available` and the install gate read the copies on disk."""
    def refuse(*args, **kwargs):
        raise AssertionError("a network connection")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert cli.main(["modules"]) == 0
    assert cli.main(["modules", "--available"]) == 0
    assert cli.main(["install", "comm"]) == 3


# -- moxi modules --available -----------------------------------------------------------

def test_available_lists_each_module_installed_or_not(site, capsys):
    site.module("comms")
    assert cli.main(["modules", "--available"]) == 0
    out = capsys.readouterr().out
    assert "comms  moxi-comms  installed 0.1.0\n" in out
    assert "cache  moxi-cache  not installed  (moxi install cache)\n" in out
    assert "    Messages between coding-agent sessions, delivered into their turns\n" in out


def test_available_as_json(site, capsys):
    site.module("comms")
    assert cli.main(["modules", "--available", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == 3 and data["source"] == "shipped"
    rows = {row["name"]: row for row in data["modules"]}
    assert rows["comms"]["state"] == "present" and rows["comms"]["version"] == "0.1.0"
    assert rows["turns"] == {"name": "turns", "distribution": "moxi-turns",
                             "summary": rows["turns"]["summary"], "state": "absent",
                             "version": "", "install": "moxi install turns"}


def test_refresh_goes_with_available(capsys):
    assert cli.main(["modules", "--refresh"]) == 2
    assert "--refresh goes with --available" in capsys.readouterr().err


def test_a_failed_refresh_says_so_and_lists_the_catalogue_in_effect(monkeypatch, capsys):
    def fail(url):
        raise CatalogueError(f"{url} is not published yet (404)")

    monkeypatch.setattr(catalogue, "_fetch", fail)
    assert cli.main(["modules", "--available", "--refresh"]) == 0
    out, err = capsys.readouterr()
    assert "refresh failed:" in err and "(404)" in err
    assert "comms  moxi-comms" in out


def test_a_refresh_that_fetches_a_newer_copy_lists_it(monkeypatch, capsys):
    monkeypatch.setattr(catalogue, "_fetch", lambda url: text(version=4).encode())
    assert cli.main(["modules", "--available", "--refresh"]) == 0
    out, err = capsys.readouterr()
    assert "catalogue version 4 kept in" in err
    assert out.startswith("comms  moxi-comms")
    assert "cache" not in out


@pytest.mark.parametrize("junk", ["status", "body"])
def test_an_answer_that_is_not_http_fails_the_fetch(monkeypatch, junk):
    """A broken proxy's answer is a failed refresh, not a traceback."""
    import http.client
    import urllib.request

    class Torn(_Response):
        def read(self, amount=None):
            raise http.client.IncompleteRead(b"part", 10)

    def urlopen(request, timeout):
        if junk == "status":
            raise http.client.BadStatusLine("NOT-HTTP garbage")
        return Torn(b"")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(CatalogueError, match="cannot fetch"):
        catalogue._fetch(catalogue.URL)
