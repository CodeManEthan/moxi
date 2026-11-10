"""The list routes of sessions' page server, in process, through loopback's strict app:
`/api/list`, `/api/more`, `/api/session` and `/api/folders`.

The app is the one `test_page` serves: strict projection, so an answer with a key its
route does not declare, a value of another type, or a null where none is declared fails
here as a 500. The roots are the ones the test's environment names, written with
`moxi_sessions.testing`; the page server's one catalog is made anew for each test.
"""

from __future__ import annotations

import os
import threading
import time
from urllib.parse import quote

import pytest

from moxi_sessions import catalog as catalog_module, page
from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb

from conftest import write_config
from test_page import LB, ROUTES, get, read, refused

HOUR = 3600.0
DAY = 24 * HOUR
LIST_ROUTES = ("/api/list", "/api/more", "/api/session", "/api/folders")


def sid(n: int) -> str:
    return f"0190b000-0000-7000-8000-{n:012x}"


def tid(n: int) -> str:
    return f"0190c000-0000-7000-8000-{n:012x}"


@pytest.fixture(autouse=True)
def fresh_catalog():
    """Each test meets a page server that has read nothing, in UTC."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    catalog_module.reset()
    yield
    catalog_module.reset()
    if before is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = before
    time.tzset()


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A root per harness, each empty, named by the environment the page server reads."""
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    data = tmp_path / "data"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    (claude_home / "projects").mkdir(parents=True)
    (codex_home / "sessions").mkdir(parents=True)
    OpenCodeDb(data / "opencode").write()
    return {"claude": claude_home / "projects", "codex": codex_home / "sessions",
            "opencode": data / "opencode"}


def changed_at(path, at: float) -> None:
    stamp = int(at * 1_000_000_000)
    os.utime(path, ns=(stamp, stamp))


def claude(roots, n: int, at: float, *, program: bool = False, cwd: str = "/work/proj-a",
           project: str = "-work-proj-a") -> ClaudeTranscript:
    t = ClaudeTranscript(roots["claude"], session_id=sid(n), cwd=cwd, project=project,
                         entrypoint="sdk-cli" if program else "cli")
    t.user("Do the thing.", origin=None if program else "human")
    t.assistant("Done.")
    changed_at(t.write(), at)
    return t


LONG = 70_000        # more bytes than one message is sent with


def seed(roots) -> dict:
    """A root of every kind of session the list meets, timed from now:

    * a person's Claude session ten minutes old, named, with a prompt and a reply each
      longer than an answer carries and two subagents;
    * a program's Claude session twenty minutes old;
    * a Claude session whose record says nothing, two days old;
    * a Codex session a day old, and a compressed rollout beside it;
    * an OpenCode session;
    * a Claude session on each of the days three to nine days back.
    """
    now = time.time()
    named = ClaudeTranscript(roots["claude"], session_id=sid(1), cwd="/work/proj-a",
                             project="-work-proj-a")
    named.title("fix the tests")
    named.user("please fix them " + "é" * (LONG // 2), origin="human")
    for agent, words in (("a0190b0000000001", "x" * 200 + "é" * 200),
                         ("a0190b0000000002", "Nothing found.")):
        sub = named.subagent(agent, agent_type="scout", description=f"look at {agent[-1]}")
        sub.user("look")
        sub.assistant(words)
    named.assistant("All fixed. " + "é" * (LONG // 2), model="claude-model-y")
    changed_at(named.write(), now - 600)

    claude(roots, 2, now - 1200, program=True)

    silent = ClaudeTranscript(roots["claude"], session_id=sid(3), project="-work-proj-b")
    silent.other("mode", mode="default")
    changed_at(silent.write(), now - 2 * DAY)

    rollout = CodexRollout(roots["codex"], thread_id=tid(1), cwd="/work/proj-b")
    rollout.turn()
    rollout.user("Do the thing.")
    rollout.assistant("Done.")
    changed_at(rollout.write(), now - DAY)
    rollout.path.with_name(f"rollout-2026-10-30T10-00-00-{tid(2)}.jsonl.zst").write_bytes(
        b"not read")

    db = OpenCodeDb(roots["opencode"])
    opencode = db.session(title="a title the harness made", directory="/work/proj-c",
                          at=now - 5 * HOUR)
    opencode.user("Do the thing.", at=now - 5 * HOUR)
    db.write()

    for back in range(3, 10):
        claude(roots, 10 + back, now - back * DAY)
    return {"now": now, "opencode": opencode.id}


def group(answer: dict, key: str) -> dict:
    return next(g for g in answer["groups"] if g["key"] == key)


def rows(answer: dict) -> dict[str, dict]:
    return {row["session"]: row for g in answer["groups"] for row in g.get("rows", ())}


# -- the table ------------------------------------------------------------------------------

def routes() -> dict[str, dict]:
    return {r["path"]: r for r in LB.export(ROUTES)["routes"]}


def test_the_list_routes_are_reads_and_none_is_the_gateways():
    found = routes()
    for path in LIST_ROUTES:
        route = found[path]
        assert route["kind"] == "read", path
        assert not (route["gateway"] or route["stream"] or route["confirmed"]), path
    assert set(found["/api/list"]["query"]) == {"harness", "since", "until", "folder",
                                                "find"}
    assert set(found["/api/more"]["query"]) == {"group", "kind", "before", "now",
                                                "harness", "since", "until", "folder",
                                                "find"}
    assert set(found["/api/session"]["query"]) == {"harness", "session"}
    assert set(found["/api/folders"]["query"]) == {"harness", "since", "until", "find"}
    assert found["/api/more"]["query"]["kind"]["values"] == ["person", "program"]


def _leaves(shape: dict, at: str, marked: str | None = None):
    """Each string leaf of an exported shape, by its dotted place (`[]` a list's item),
    with the nearest mark on its way down."""
    mark = shape.get("mark") or marked
    if shape["type"] == "string":
        yield at, mark
    elif shape["type"] == "list":
        yield from _leaves(shape["items"], at + "[]", mark)
    elif shape["type"] == "table":
        for key, sub in shape["items"].items():
            yield from _leaves(sub, f"{at}.{key}", mark)


def every_leaf() -> dict[str, str | None]:
    out = {}
    for path in LIST_ROUTES:
        for key, shape in routes()[path]["returns"].items():
            out.update({f"{path} {k}": v for k, v in _leaves(shape, key)})
    return out


#: Within one row: the string leaves that are an id, a word of a fixed set or a model's
#: name, and so carry no mark; the ones a person or a model wrote; and the paths.
ROW_PLAIN = {"harness", "session", "model", "activity.who"}
ROW_REDACT = {"name", "activity.text", "prompt.text"}
ROW_PATH = {"path", "folder"}
#: Where a row sits in the four answers.
ROW_AT = ("/api/list groups[].rows[]", "/api/more rows[]", "/api/more groups[].rows[]",
          "/api/session ")


def _in_rows(leaves: set[str]) -> set[str]:
    return {f"{at}{'' if at.endswith(' ') else '.'}{leaf}" for at in ROW_AT
            for leaf in leaves}


def test_every_string_leaf_is_plain_only_for_an_id_a_key_or_a_word():
    plain = _in_rows(ROW_PLAIN) | {
        "/api/list today", "/api/list roots[].harness", "/api/list harnesses[].harness",
        "/api/list groups[].key", "/api/list groups[].before",
        "/api/more group", "/api/more kind", "/api/more before",
        "/api/more groups[].key", "/api/more groups[].before",
        "/api/session subagents[].agent_type", "/api/session resume.command"}
    assert {k for k, v in every_leaf().items() if v is None} == plain


def test_what_a_person_or_a_model_wrote_is_redact_and_every_folder_and_path_is_path():
    found = every_leaf()
    assert {k for k, v in found.items() if v == "redact"} == _in_rows(ROW_REDACT) | {
        "/api/list roots[].reason", "/api/session reply.text",
        "/api/session subagents[].description", "/api/session subagents[].last"}
    assert {k for k, v in found.items() if v == "path"} == _in_rows(ROW_PATH) | {
        "/api/list roots[].root", "/api/session resume.folder",
        "/api/folders folders[].folder"}


# -- the list -------------------------------------------------------------------------------

def test_the_list_answers_its_counts_and_groups_on_a_seeded_root(roots):
    seeded = seed(roots)

    answer = read("/api/list")

    assert answer["shape"] == 1
    assert seeded["now"] <= answer["now"] <= time.time()
    assert answer["today"] == time.strftime("%Y-%m-%d", time.gmtime(answer["now"]))
    assert answer["all"] == answer["total"] == 12
    assert answer["unreadable"] == 1 and answer["roots"] == []
    assert answer["harnesses"] == [{"harness": "claude", "count": 10},
                                   {"harness": "codex", "count": 1},
                                   {"harness": "opencode", "count": 1}]
    keys = [g["key"] for g in answer["groups"]]
    assert keys[0] == "recent" and keys[-1] == "earlier" and len(keys) == 9
    assert keys[1:-1] == sorted(keys[1:-1], reverse=True)
    recent = group(answer, "recent")
    assert [row["session"] for row in recent["rows"]] == [sid(1)]
    assert (recent["count"], recent["before"]) == (1, None) and "program" not in recent
    days = answer["groups"][1:-1]
    assert all(set(day) == {"key", "count", "rows", "before", "program"} for day in days)
    assert all(set(day["program"]) == {"count"} for day in days)
    assert sum(day["program"]["count"] for day in days) == 1
    assert sid(2) not in rows(answer)
    earlier = group(answer, "earlier")
    assert set(earlier) == {"key", "count", "oldest", "before"}
    assert earlier["count"] >= 1 and earlier["before"] is not None
    assert answer["total"] == (sum(g["count"] for g in answer["groups"])
                               + sum(day["program"]["count"] for day in days))


def test_a_row_carries_what_was_read_and_null_for_what_was_not(roots):
    seeded = seed(roots)

    found = rows(read("/api/list"))

    named = found[sid(1)]
    assert named["name"] == "fix the tests" and named["folder"] == "/work/proj-a"
    assert named["subagents"] == 2 and named["recent"] is True
    assert named["by_program"] is None and named["model"] == "claude-model-y"
    assert named["path"].endswith(f"{sid(1)}.jsonl") and named["size"] > LONG
    assert named["activity"]["who"] == "agent"
    assert named["activity"]["text"].startswith("All fixed. é")
    assert len(named["activity"]["text"].encode()) <= 300
    assert named["prompt"]["truncated"] is True
    assert len(named["prompt"]["text"].encode()) == page.TEXT_MAX
    assert named["prompt"]["text"].startswith("please fix them é")

    silent = found[sid(3)]
    for key in ("folder", "started", "by_program", "name", "model", "activity",
                "prompt"):
        assert silent[key] is None, key
    assert silent["subagents"] == 0 and silent["recent"] is False

    codex = found[tid(1)]
    assert codex["folder"] == "/work/proj-b" and codex["name"] is None
    assert codex["prompt"] is None and codex["activity"]["who"] == "agent"

    opencode = found[seeded["opencode"]]
    assert opencode["size"] is None and opencode["name"] is None
    assert opencode["folder"] == "/work/proj-c"
    assert opencode["prompt"] is None


def test_a_configured_root_that_does_not_exist_is_named_and_the_others_answer(
        roots, tmp_path, own_config):
    seed(roots)
    write_config(own_config, f'[codex]\nsessions = "{tmp_path / "nowhere"}"\n')

    answer = read("/api/list")

    assert answer["all"] == 11 and answer["unreadable"] is None
    assert [(r["harness"], r["root"]) for r in answer["roots"]] == [
        ("codex", str(tmp_path / "nowhere"))]
    assert "does not exist" in answer["roots"][0]["reason"]
    assert [h["harness"] for h in answer["harnesses"]] == ["claude", "opencode"]


def test_a_harness_that_is_not_on_the_machine_is_no_failure_and_has_no_row(
        roots, tmp_path, monkeypatch):
    seed(roots)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nowhere"))

    answer = read("/api/list")

    assert answer["all"] == 11 and answer["roots"] == []
    assert answer["unreadable"] is None
    assert [h["harness"] for h in answer["harnesses"]] == ["claude", "opencode"]


def test_a_machine_with_no_harness_answers_the_empty_list(tmp_path, monkeypatch):
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(name, str(tmp_path / "nowhere"))

    answer = read("/api/list")

    assert (answer["all"], answer["total"]) == (0, 0)
    assert answer["roots"] == [] and answer["harnesses"] == []
    assert [(g["key"], g["count"]) for g in answer["groups"]] == [("recent", 0)]
    assert read("/api/folders") == {"shape": answer["shape"], "folders": [], "more": 0}


def test_a_default_root_that_exists_and_cannot_be_read_is_named(roots):
    seed(roots)
    database = roots["opencode"] / "opencode.db"
    database.write_text("not a database")

    answer = read("/api/list")

    assert [r["harness"] for r in answer["roots"]] == ["opencode"]
    assert answer["roots"][0]["reason"]
    assert [h["harness"] for h in answer["harnesses"]] == ["claude", "codex"]


def test_a_config_that_cannot_be_read_is_refused_with_the_reason(roots, own_config):
    write_config(own_config, "[list\n")

    assert "config cannot be read" in refused("/api/list", "")


def test_the_filters_narrow_the_list_and_keep_the_counts_exact(roots):
    seeded = seed(roots)

    by_harness = read("/api/list", "harness=codex")
    assert (by_harness["all"], by_harness["total"]) == (12, 1)
    assert [h["count"] for h in by_harness["harnesses"]] == [10, 1, 1]
    assert list(rows(by_harness)) == [tid(1)]

    by_folder = read("/api/list", "folder=" + quote("/work/proj-b", safe=""))
    assert by_folder["total"] == 1 and list(rows(by_folder)) == [tid(1)]

    since = read("/api/list", f"since={seeded['now'] - 2 * HOUR:.3f}")
    assert since["total"] == 2
    until = read("/api/list", f"until={int(seeded['now'] - 2 * HOUR)}")
    assert until["total"] == 10

    found = read("/api/list", "find=PROJ-C")
    assert [g["key"] for g in found["groups"]] == ["found"]
    assert group(found, "found")["count"] == 1
    assert list(rows(found)) == [seeded["opencode"]]
    by_id = read("/api/list", f"find={sid(2)[:30]}")
    assert set(rows(by_id)) >= {sid(2)} and rows(by_id)[sid(2)]["by_program"] is True


def test_the_setting_decides_what_counts_as_recent(roots, own_config):
    seed(roots)
    assert [r["session"] for r in group(read("/api/list"), "recent")["rows"]] == [sid(1)]

    write_config(own_config, "[list]\nrecent_s = 60\n")
    narrow = read("/api/list")
    assert group(narrow, "recent")["count"] == 0
    assert rows(narrow)[sid(1)]["recent"] is False

    write_config(own_config, f"[list]\nrecent_s = {int(6 * HOUR)}\n")
    wide = group(read("/api/list"), "recent")
    assert wide["count"] == 2 and all(row["recent"] for row in wide["rows"])
    assert sid(2) not in [row["session"] for row in wide["rows"]]


def test_a_filter_that_is_not_one_is_refused(roots):
    seed(roots)

    assert "2 characters" in refused("/api/list", "find=a")
    assert "absolute path" in refused("/api/list", "folder=proj-a")
    for query in ("since=yesterday", "until=-5", "since=1e9", "since="):
        assert "epoch seconds" in refused("/api/list", query), query
    assert get("/api/list", "harness=other")[0] == 400
    assert get("/api/list", "limit=5")[0] == 400


# -- more -----------------------------------------------------------------------------------

def test_more_pages_a_day_by_kind_and_earlier_by_whole_days(roots):
    seeded = seed(roots)
    listed = read("/api/list")
    now = f"now={listed['now']!r}"
    days = listed["groups"][1:-1]
    with_program = next(day for day in days if day["program"]["count"])

    program = read("/api/more", f"group={with_program['key']}&kind=program&{now}")
    assert set(program) == {"shape", "group", "kind", "rows", "before"}
    assert (program["group"], program["kind"]) == (with_program["key"], "program")
    assert [row["session"] for row in program["rows"]] == [sid(2)]
    assert program["rows"][0]["by_program"] is True and program["before"] is None
    assert program["rows"][0]["prompt"] is None

    person = read("/api/more", f"group={with_program['key']}&{now}")
    assert person["kind"] == "person"
    assert [r["session"] for r in person["rows"]] == [r["session"]
                                                      for r in with_program["rows"]]

    earlier = group(listed, "earlier")
    older = read("/api/more",
                 f"group=earlier&before={quote(earlier['before'], safe='')}&{now}")
    assert set(older) == {"shape", "groups", "before", "left"}
    assert sum(g["count"] + g["program"]["count"] for g in older["groups"]) == (
        earlier["count"])
    assert older["left"] == 0 and older["before"] is None
    assert all(set(g) == {"key", "count", "rows", "before", "program"}
               for g in older["groups"])

    found = read("/api/more", "group=found&find=proj-c")
    assert found["kind"] is None
    assert [row["session"] for row in found["rows"]] == [seeded["opencode"]]


def test_more_cuts_recent_at_the_now_it_is_given(roots):
    seed(roots)
    listed = read("/api/list")

    then = read("/api/more", f"group=recent&now={listed['now']!r}")
    assert [row["session"] for row in then["rows"]] == [sid(1)]
    later = read("/api/more", f"group=recent&now={listed['now'] + 2 * HOUR!r}")
    assert later["rows"] == [] and later["before"] is None


def test_more_refuses_what_it_cannot_answer(roots):
    seed(roots)

    assert "place in the list" in refused("/api/more", "group=recent&before=nonsense")
    assert "place in the list" in refused("/api/more",
                                          "group=recent&before=12:other:abc")
    assert "only a day" in refused("/api/more", "group=recent&kind=program")
    assert "group is" in refused("/api/more", "group=someday")
    assert "group is" in refused("/api/more", "group=2026-13-45")
    assert "2 characters" in refused("/api/more", "group=found&find=a")
    assert "found" in refused("/api/more", "group=found")
    assert "epoch seconds" in refused("/api/more", "group=recent&now=soon")
    assert get("/api/more", "group=recent&kind=other")[0] == 400
    assert get("/api/more", "")[0] == 400


# -- one session ----------------------------------------------------------------------------

def test_an_opened_session_carries_its_reply_subagents_and_resume_command(roots):
    seed(roots)

    opened = read("/api/session", f"harness=claude&session={sid(1)}")

    assert opened["shape"] == 1 and opened["name"] == "fix the tests"
    assert opened["prompt"]["truncated"] is True
    reply = opened["reply"]
    assert reply["truncated"] is True and reply["text"].startswith("All fixed. é")
    assert len(reply["text"].encode()) in (page.TEXT_MAX, page.TEXT_MAX - 1)
    assert opened["more"] == 0
    first, second = opened["subagents"]
    assert first["description"] == "look at 1" and first["agent_type"] == "scout"
    assert first["last"] == "x" * 200 + "é" * 50          # 300 bytes, on a character
    assert second["last"] == "Nothing found."
    assert first["started"] is not None and first["last_at"] is not None
    assert opened["resume"] == {"command": f"claude --resume {sid(1)}",
                                "folder": "/work/proj-a"}


def test_an_opened_session_with_nothing_read_is_nulls(roots):
    seeded = seed(roots)

    silent = read("/api/session", f"harness=claude&session={sid(3)}")
    for key in ("folder", "started", "name", "model", "activity", "prompt", "reply"):
        assert silent[key] is None, key
    assert silent["subagents"] == [] and silent["more"] == 0
    assert silent["resume"] == {"command": f"claude --resume {sid(3)}", "folder": None}

    codex = read("/api/session", f"harness=codex&session={tid(1)}")
    assert codex["reply"] == {"at": codex["reply"]["at"], "text": "Done.",
                              "truncated": False}
    assert codex["resume"] == {"command": f"codex resume {tid(1)}", "folder": None}

    opencode = read("/api/session", f"harness=opencode&session={seeded['opencode']}")
    assert opencode["reply"] is None and opencode["size"] is None


def test_an_id_the_list_does_not_hold_is_refused(roots):
    seed(roots)

    assert "holds no claude session" in refused("/api/session",
                                                "harness=claude&session=a0190b0000000001")
    assert "holds no codex session" in refused("/api/session",
                                               f"harness=codex&session={sid(1)}")


# -- folders --------------------------------------------------------------------------------

def test_folders_are_counted_under_the_filters_newest_first(roots):
    seed(roots)

    answer = read("/api/folders")
    assert answer["shape"] == 1 and answer["more"] == 0
    assert [(f["folder"], f["count"]) for f in answer["folders"]] == [
        ("/work/proj-a", 9), ("/work/proj-c", 1), ("/work/proj-b", 1)]
    assert all(isinstance(f["changed"], float) for f in answer["folders"])

    codex = read("/api/folders", "harness=codex")
    assert [(f["folder"], f["count"]) for f in codex["folders"]] == [("/work/proj-b", 1)]
    assert "2 characters" in refused("/api/folders", "find=a")
    assert get("/api/folders", "folder=/work/proj-a")[0] == 400


# -- the catalog between requests -----------------------------------------------------------

def test_the_server_keeps_what_one_request_read_for_the_next(roots):
    seed(roots)

    read("/api/list")
    cat = catalog_module.shared()
    heads, tails = cat.head_reads, cat.tail_reads
    read("/api/list")
    read("/api/session", f"harness=codex&session={tid(1)}")

    assert catalog_module.shared() is cat
    assert (cat.head_reads, cat.tail_reads) == (heads, tails)


def test_two_folder_requests_at_once_read_each_head_once(roots, monkeypatch):
    now = time.time()
    for n in range(1, 21):
        claude(roots, n, now - n * HOUR, cwd=f"/work/proj-{n % 2}")
    seen: list[str] = []
    real = catalog_module.read_head

    def slow(listed, imports=None):
        seen.append(listed.source.path)
        time.sleep(0.002)
        return real(listed, imports)

    monkeypatch.setattr(catalog_module, "read_head", slow)
    start = threading.Barrier(2)
    answers: list[tuple[int, dict | None]] = []

    def ask(folder: str) -> None:
        start.wait()
        answers.append(get("/api/list", "folder=" + quote(folder, safe="")))

    threads = [threading.Thread(target=ask, args=(f"/work/proj-{n}",)) for n in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [status for status, _ in answers] == [200, 200]
    assert sorted(body["total"] for _, body in answers) == [10, 10]
    assert len(seen) == 20 and len(set(seen)) == 20
    assert catalog_module.shared().head_reads == 20
