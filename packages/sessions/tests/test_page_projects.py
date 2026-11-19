"""`/api/projects`: the sessions folded into projects by projects' read API, which the
page server loads at interface 2.

projects is stood in for at the core's loader, as `pagehandlers` reaches it, so no test
here imports projects or its kit. One test loads the projects this workspace installs,
with no store, to show the real interface 2 answers.
"""

from __future__ import annotations

import logging
import os
import time
from types import SimpleNamespace

import pytest

from moxi_sessions import lists, pagehandlers
from moxi_sessions.testing import CodexRollout, OpenCodeDb

from conftest import write_config
from test_page import LB, ROUTES, read, refused
from test_page_list import DAY, HOUR, claude, fresh_catalog, roots, sid, tid  # noqa: F401

ROUTE = "/api/projects"


class Projects:
    """projects' read API as the loader hands it over: `for_paths` answers from `keys`,
    a folder to the key of the project holding it, and records each call."""

    def __init__(self, keys: dict[str, str] | None = None, *, offers=(1, 2),
                 raises: Exception | None = None):
        self.keys = keys or {}
        self.offers = set(offers)
        self.raises = raises
        self.calls: list[list[str]] = []
        self.asked: list[int | None] = []

    def for_paths(self, paths):
        paths = list(paths)
        self.calls.append(paths)
        if self.raises is not None:
            raise self.raises
        return {path: self.keys.get(path) for path in paths}

    def load(self, name, *, interface=None):
        self.asked.append(interface)
        if interface is not None and interface not in self.offers:
            return SimpleNamespace(ok=False, api=None, state="broken",
                                   reason=f"projects offers {sorted(self.offers)}")
        return SimpleNamespace(ok=True, api=self, state="present", reason="")


@pytest.fixture
def stand_in(monkeypatch):
    def put(projects: Projects | None) -> Projects | None:
        real = pagehandlers.modules.load

        def load(name, **kwargs):
            if name != "projects":
                return real(name, **kwargs)
            if projects is None:
                return SimpleNamespace(ok=False, api=None, state="absent",
                                       reason="not installed")
            return projects.load(name, **kwargs)

        monkeypatch.setattr(pagehandlers.modules, "load", load)
        return projects
    return put


def _leaves(shape: dict, at: str, marked: str | None = None):
    mark = shape.get("mark") or marked
    if shape["type"] == "string":
        yield at, mark
    elif shape["type"] == "list":
        yield from _leaves(shape["items"], at + "[]", mark)
    elif shape["type"] == "table":
        for key, sub in shape["items"].items():
            yield from _leaves(sub, f"{at}.{key}", mark)


# -- the table ------------------------------------------------------------------------------

def test_the_route_is_a_read_with_no_query_and_none_of_it_the_gateways():
    route = {r["path"]: r for r in LB.export(ROUTES)["routes"]}[ROUTE]
    assert route["kind"] == "read" and not route["query"]
    assert not (route["gateway"] or route["stream"] or route["confirmed"])
    leaves = {}
    for key, shape in route["returns"].items():
        leaves.update(dict(_leaves(shape, key)))
    assert {k for k, v in leaves.items() if v == "path"} == {"home", "folders[].folder"}
    assert {k for k, v in leaves.items() if v is None} == {
        "today", "state", "detail", "projects[].key", "projects[].week[]",
        "projects[].recent[]"}


# -- the fold -------------------------------------------------------------------------------

def test_each_project_gets_its_newest_change_its_count_and_its_claims(roots, stand_in):
    now = time.time()
    claude(roots, 1, now - 600, cwd="/work/moxi")                    # recent
    claude(roots, 2, now - 2 * HOUR, cwd="/work/moxi/packages/ui")   # this week
    claude(roots, 3, now - 300, cwd="/work/moxi", program=True)       # a program's
    claude(roots, 4, now - 10 * DAY, cwd="/work/moxi")               # older
    claude(roots, 5, now - 3 * DAY, cwd="/work/notes")
    projects = stand_in(Projects({"/work/moxi": "moxi", "/work/moxi/packages/ui": "moxi",
                                  "/work/notes": "notes"}))

    answer = read(ROUTE)

    assert answer["state"] == "up" and answer["detail"] is None
    assert answer["recently_s"] == 3600 and answer["shape"] == 1
    assert answer["folders"] == [] and answer["more"] == 0
    by_key = {p["key"]: p for p in answer["projects"]}
    assert list(by_key) == ["moxi", "notes"]
    moxi = by_key["moxi"]
    assert moxi["all"] == 4
    assert moxi["at"] == pytest.approx(now - 300, abs=1)        # the program's counts here
    assert moxi["week"] == [f"claude/{sid(1)}", f"claude/{sid(2)}"]
    assert moxi["recent"] == [f"claude/{sid(1)}"]
    assert by_key["notes"] == {"key": "notes", "at": pytest.approx(now - 3 * DAY, abs=1),
                               "all": 1, "week": [f"claude/{sid(5)}"], "recent": []}
    assert projects.asked == [2]
    assert projects.calls == [["/work/moxi", "/work/moxi/packages/ui", "/work/notes"]]


def test_a_project_of_old_or_program_sessions_only_has_empty_claims(roots, stand_in):
    now = time.time()
    claude(roots, 1, now - 60, cwd="/work/cron", program=True)
    claude(roots, 2, now - 30 * DAY, cwd="/work/old")
    stand_in(Projects({"/work/cron": "cron", "/work/old": "old"}))

    found = {p["key"]: p for p in read(ROUTE)["projects"]}

    assert found["cron"]["all"] == 1 and found["cron"]["week"] == found["cron"]["recent"] == []
    assert found["old"]["all"] == 1 and found["old"]["week"] == []


def test_a_claim_names_each_harness_and_the_id_it_writes(roots, stand_in):
    now = time.time()
    claude(roots, 1, now - 60, cwd="/work/moxi")
    rollout = CodexRollout(roots["codex"], thread_id=tid(1), cwd="/work/moxi")
    rollout.turn()
    rollout.user("Do the thing.")
    rollout.assistant("Done.")
    stamp = int((now - 120) * 1e9)
    path = rollout.write()
    os.utime(path, ns=(stamp, stamp))
    db = OpenCodeDb(roots["opencode"])
    opencode = db.session(title="t", work_dir="/work/moxi", at=now - 180)
    opencode.user("Do the thing.", at=now - 180)
    db.write()
    stand_in(Projects({"/work/moxi": "moxi"}))

    (moxi,) = read(ROUTE)["projects"]

    # Claude Code's session id is its transcript's name; a Codex thread id is the one its
    # rollout file is named by.
    assert f"{sid(1)}.jsonl" in [p.name for p in roots["claude"].rglob("*.jsonl")]
    assert path.name.endswith(f"-{tid(1)}.jsonl")
    assert moxi["recent"] == [f"claude/{sid(1)}", f"codex/{tid(1)}",
                              f"opencode/{opencode.id}"]
    assert all(claim.split("/", 1)[0] in ("claude", "codex", "opencode")
               for claim in moxi["week"])


def test_recent_follows_the_setting(roots, stand_in, own_config):
    now = time.time()
    claude(roots, 1, now - 600, cwd="/work/moxi")
    claude(roots, 2, now - 1800, cwd="/work/moxi")
    stand_in(Projects({"/work/moxi": "moxi"}))
    write_config(own_config, "[list]\nrecent_s = 900\n")

    answer = read(ROUTE)

    assert answer["recently_s"] == 900
    assert answer["projects"][0]["recent"] == [f"claude/{sid(1)}"]


# -- work dirs in no project-----------------------------------------------------------------

def test_a_work_dir_no_project_holds_is_listed_with_this_weeks_count(roots, stand_in):
    now = time.time()
    claude(roots, 1, now - 600, cwd="/work/scratch")
    claude(roots, 2, now - 2 * DAY, cwd="/work/scratch")
    claude(roots, 3, now - 20 * DAY, cwd="/work/scratch")
    claude(roots, 4, now - HOUR, cwd="/work/probe")
    claude(roots, 5, now - 60, cwd="/work/cron", program=True)    # a program's only
    claude(roots, 6, now - 9 * DAY, cwd="/work/stale")            # not this week
    claude(roots, 7, now - 60, cwd="/work/moxi")
    stand_in(Projects({"/work/moxi": "moxi"}))

    answer = read(ROUTE)

    assert [(f["folder"], f["week"]) for f in answer["folders"]] == [
        ("/work/scratch", 2), ("/work/probe", 1)]
    assert answer["folders"][0]["at"] == pytest.approx(now - 600, abs=1)
    assert answer["more"] == 0
    assert [p["key"] for p in answer["projects"]] == ["moxi"]


def test_loose_work_dirs_stop_at_twenty_and_more_counts_the_rest(roots, stand_in):
    now = time.time()
    for n in range(1, 24):
        claude(roots, n, now - n * 60, cwd=f"/work/f{n:02d}")
    stand_in(Projects())

    answer = read(ROUTE)

    assert [f["folder"] for f in answer["folders"]] == [f"/work/f{n:02d}" for n in range(1, 21)]
    assert answer["more"] == 3
    assert lists.LOOSE_DIRS_MAX == 20


# -- when projects can't answer -------------------------------------------------------------

def test_without_projects_the_state_is_off_and_nothing_is_read(roots, stand_in):
    claude(roots, 1, time.time() - 60, cwd="/work/moxi")
    stand_in(None)

    answer = read(ROUTE)

    assert answer["state"] == "off" and answer["detail"] == pagehandlers.PROJECTS_OFF
    assert (answer["projects"], answer["folders"], answer["more"]) == ([], [], 0)


def test_projects_at_interface_one_only_reads_as_absent(roots, stand_in):
    claude(roots, 1, time.time() - 60, cwd="/work/moxi")
    projects = stand_in(Projects({"/work/moxi": "moxi"}, offers=(1,)))

    answer = read(ROUTE)

    assert answer["state"] == "off" and answer["projects"] == []
    assert projects.asked == [2] and projects.calls == []


def test_a_store_projects_cant_read_is_an_error_with_a_fixed_sentence(roots, stand_in,
                                                                       caplog):
    claude(roots, 1, time.time() - 60, cwd="/work/moxi")
    secret = "/home/someone/.local/share/moxi/projects/projects.db is not a database"
    stand_in(Projects(raises=RuntimeError(secret)))
    pagehandlers._logged.clear()

    with caplog.at_level(logging.ERROR, logger="moxi.sessions.page"):
        first = read(ROUTE)
        second = read(ROUTE)

    for answer in (first, second):
        assert answer["state"] == "error"
        assert answer["detail"] == pagehandlers.PROJECTS_UNREADABLE
        assert secret not in str(answer)
        assert (answer["projects"], answer["folders"], answer["more"]) == ([], [], 0)
    assert len([r for r in caplog.records if r.name == "moxi.sessions.page"]) == 1


def test_a_root_with_no_sessions_answers_empty_lists_without_asking_projects(roots,
                                                                              stand_in):
    projects = stand_in(Projects())

    answer = read(ROUTE)

    assert answer["state"] == "up" and answer["projects"] == answer["folders"] == []
    assert projects.calls == []


def test_a_config_that_cannot_be_read_is_refused(roots, stand_in, own_config):
    stand_in(Projects())
    write_config(own_config, "[list\n")
    assert "config cannot be read" in refused(ROUTE, "")


def test_the_installed_projects_with_no_store_holds_no_work_dir(roots):
    claude(roots, 1, time.time() - 60, cwd="/work/moxi")

    answer = read(ROUTE)

    assert answer["state"] == "up"
    assert answer["projects"] == []
    assert [(f["folder"], f["week"]) for f in answer["folders"]] == [("/work/moxi", 1)]
