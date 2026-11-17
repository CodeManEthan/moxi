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
    assert shipped.version == 7 and shipped.source == "shipped"
    assert shipped.names() == ["cache", "comms", "connector", "launcher", "loadout", "memory",
                               "notes", "projects", "publishing", "repo", "rounds",
                               "secrets", "services", "sessions", "slots", "statusline",
                               "tunnel", "turns", "ui", "usage"]
    for entry in shipped.entries:
        assert entry.distribution == f"moxi-{entry.name}"
        assert entry.summary and "\n" not in entry.summary


def test_a_module_on_the_not_ready_list_is_marked_unreleased():
    # A member on the workspace's not-ready list has never passed the release gate, so it
    # has no released version and `moxi install` must not send a user to the index for it.
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "pyproject.toml"
    if not root.is_file():
        pytest.skip("the workspace root isn't here (an installed copy is being tested)")
    table = tomllib.loads(root.read_text(encoding="utf-8")).get("tool", {}).get(
        "moxi-publishing", {})
    not_ready = set(table.get("not-ready", []))
    unmarked = [entry.distribution for entry in catalogue.shipped().entries
                if entry.distribution in not_ready and not entry.unreleased]
    assert unmarked == []


def test_the_refresh_reads_the_core_release_tags_the_index_and_the_tagged_file():
    assert catalogue.TAG_PREFIX == "moxi-v"
    assert catalogue.TAGS_URL == ("https://api.github.com/repos/CodeManEthan/moxi/git/"
                                  "matching-refs/tags/moxi-v")
    assert catalogue.INDEX_URL.format(version="0.1.8") == (
        "https://pypi.org/pypi/moxi/0.1.8/json")
    assert catalogue.FILE_URL.format(tag="moxi-v0.1.8") == (
        "https://raw.githubusercontent.com/CodeManEthan/moxi/moxi-v0.1.8/"
        "packages/moxi/src/moxi/catalogue.toml")


def test_an_entry_may_be_unreleased():
    listed = catalogue.parse(text(extra="unreleased = true\n"))
    assert listed.get("comms").unreleased is True
    assert catalogue.parse(text()).get("comms").unreleased is False
    with pytest.raises(CatalogueError, match="unreleased must be true or false"):
        catalogue.parse(text(extra='unreleased = "yes"\n'))


def test_an_entry_is_frozen():
    entry = catalogue.shipped().get("comms")
    with pytest.raises(AttributeError):
        entry.name = "other"
    assert entry == catalogue.Entry("comms", "moxi-comms", entry.summary,
                                    unreleased=entry.unreleased)


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
    path.write_text(text(version=catalogue.shipped().version + 1))
    assert catalogue.load(env).names() == ["comms"]
    path.write_text("version = 9\n[[module]]\nname = 3\n")
    assert catalogue.load(env).source == "shipped"  # one that doesn't check is ignored


def released(tags=("0.1.7", "0.1.8", "0.1.10"), published=("0.1.7", "0.1.8"),
             body=None, broken=None, files=None):
    """A fetch answering as GitHub and the index would: the core's release `tags`, the
    `published` versions, and the catalogue at each tag; `broken` URLs fail to read.
    `files` gives a version's page another list of files than one unyanked file."""
    asked = []

    def fetch(url):
        asked.append(url)
        if url == broken:
            raise CatalogueError(f"{url} answered HTTP 500")
        if url == catalogue.TAGS_URL:
            return json.dumps([{"ref": f"refs/tags/moxi-v{t}"} for t in tags]
                              + [{"ref": "refs/tags/moxi-v0.2.0rc1"}]).encode()
        for version in published:
            if url == catalogue.INDEX_URL.format(version=version):
                found = (files or {}).get(version, [{"filename": "f", "yanked": False}])
                return json.dumps({"urls": found}).encode()
        if url.startswith("https://pypi.org/"):
            raise catalogue.NotFound(f"{url} is not published yet (404)")
        if body is not None:
            return body
        return text(version=9).encode()

    fetch.asked = asked
    return fetch


def test_refresh_keeps_the_catalogue_of_the_highest_released_core(tmp_path):
    env = {"MOXI_HOME": str(tmp_path)}
    fetch = released()
    kept, said = catalogue.refresh(env, fetch=fetch)
    assert fetch.asked == [catalogue.TAGS_URL,
                           catalogue.INDEX_URL.format(version="0.1.10"),
                           catalogue.INDEX_URL.format(version="0.1.8"),
                           catalogue.FILE_URL.format(tag="moxi-v0.1.8")]
    assert kept.version == 9 and kept.names() == ["comms"]
    assert said.startswith("catalogue version 9 kept in ")
    assert catalogue.cached_path(env).read_text() == text(version=9)
    assert catalogue.load(env).version == 9


def test_a_version_whose_files_are_all_yanked_or_none_is_not_released(tmp_path):
    env = {"MOXI_HOME": str(tmp_path)}
    yanked = [{"filename": "a", "yanked": True}, {"filename": "b", "yanked": True}]
    fetch = released(published=("0.1.7", "0.1.8", "0.1.10"),
                     files={"0.1.10": yanked, "0.1.8": []})
    kept, _ = catalogue.refresh(env, fetch=fetch)
    assert fetch.asked[-1] == catalogue.FILE_URL.format(tag="moxi-v0.1.7")
    assert kept.version == 9


def test_refresh_asks_about_three_tags_at_most(tmp_path):
    fetch = released(tags=("0.1.1", "0.1.2", "0.1.3", "0.1.4"), published=("0.1.1",))
    with pytest.raises(catalogue.NotFound, match="no released version"):
        catalogue.refresh({"MOXI_HOME": str(tmp_path)}, fetch=fetch)
    assert len(fetch.asked) == 4 and not catalogue.cached_path(
        {"MOXI_HOME": str(tmp_path)}).exists()


def test_refresh_with_no_release_tag_keeps_the_shipped_copy(tmp_path):
    env = {"MOXI_HOME": str(tmp_path)}
    with pytest.raises(catalogue.NotFound, match="no released version"):
        catalogue.refresh(env, fetch=released(tags=()))
    assert not catalogue.cached_path(env).exists()


def test_a_read_error_fails_the_refresh(tmp_path):
    env = {"MOXI_HOME": str(tmp_path)}
    broken = catalogue.INDEX_URL.format(version="0.1.10")
    with pytest.raises(CatalogueError, match="HTTP 500"):
        catalogue.refresh(env, fetch=released(broken=broken))
    assert not catalogue.cached_path(env).exists()


def test_refresh_leaves_the_shipped_copy_when_the_released_one_is_not_newer(tmp_path):
    env = {"MOXI_HOME": str(tmp_path)}
    have = catalogue.shipped().version
    kept, said = catalogue.refresh(env, fetch=released(body=text(version=have).encode()))
    assert kept.source == "shipped" and f"the shipped one (version {have}) stays" in said
    assert not catalogue.cached_path(env).exists()


def test_refresh_writes_nothing_when_the_fetched_copy_does_not_check(tmp_path):
    env = {"MOXI_HOME": str(tmp_path)}
    with pytest.raises(CatalogueError):
        catalogue.refresh(env, fetch=released(body=b"version = 7\n[[module]]\nname = 1\n"))
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
        catalogue._fetch(catalogue.TAGS_URL)
    assert seen == {"timeout": 10.0, "url": catalogue.TAGS_URL}

    def not_found(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", not_found)
    with pytest.raises(catalogue.NotFound, match=r"not published yet \(404\)"):
        catalogue._fetch(catalogue.TAGS_URL)


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
    assert data["version"] == catalogue.shipped().version and data["source"] == "shipped"
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
    newer = catalogue.shipped().version + 1
    monkeypatch.setattr(catalogue, "_fetch", released(body=text(version=newer).encode()))
    assert cli.main(["modules", "--available", "--refresh"]) == 0
    out, err = capsys.readouterr()
    assert f"catalogue version {newer} kept in" in err
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
        catalogue._fetch(catalogue.TAGS_URL)
