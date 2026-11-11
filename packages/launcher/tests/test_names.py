"""A launch's session name: the template's pieces, its date formats and its cleanup, the
check a template passes before it is saved, which template names a launch, the name every
fresh launch records and Claude Code shows, `--name` on the command line, and the name
read on an install without the UI shell."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import pytest
from moxi import exit as codes
from moxi import proc
from moxi.paths import Layout

import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import api, catalog, cli, names, presets, store, targets
from moxi_launcher.api import LaunchRequest, ReplayRequest
from moxi_launcher.names import Pieces

SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
SID2 = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"
#: The template for every launch and one project's own, as the worked cases use them.
EVERY = "<identity>-<date:mmm-dd-yyyy hh:mm>"
OWN = "<project>-<model>-<date:mmm-dd hh:mm>"


def at(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 11, day, hour, minute)


def in_project(model: str) -> Pieces:
    return Pieces(project="proj-a", model=model)


def seated(identity: str) -> Pieces:
    return Pieces(project="proj-b", identity=identity)


#: The worked cases: a template, what the launch fills it with, when, and its name.
WORKED = [
    (OWN, in_project("sonnet"), at(8, 8, 20), "proj-a-sonnet-Nov-08 08:20"),
    (OWN, in_project("sonnet"), at(8, 18, 45), "proj-a-sonnet-Nov-08 18:45"),
    (OWN, in_project("sonnet"), at(5, 21, 24), "proj-a-sonnet-Nov-05 21:24"),
    (OWN, in_project("fable"), at(3, 8, 52), "proj-a-fable-Nov-03 08:52"),
    (EVERY, seated("planner"), at(8, 12, 45), "planner-Nov-08-2026 12:45"),
    (EVERY, seated("planner"), at(5, 19, 53), "planner-Nov-05-2026 19:53"),
    (EVERY, seated("coder"), at(8, 11, 0), "coder-Nov-08-2026 11:00"),
    (EVERY, seated("coder"), at(6, 9, 45), "coder-Nov-06-2026 09:45"),
    (EVERY, seated("reviewer"), at(8, 15, 50), "reviewer-Nov-08-2026 15:50"),
    (EVERY, seated("reviewer"), at(6, 15, 10), "reviewer-Nov-06-2026 15:10"),
    (EVERY, seated("scout"), at(8, 13, 45), "scout-Nov-08-2026 13:45"),
    (EVERY, seated("scout"), at(7, 20, 11), "scout-Nov-07-2026 20:11"),
    (EVERY, seated("scout"), at(7, 22, 25), "scout-Nov-07-2026 22:25"),
    # No identity: the dash the empty piece leaves at the front is cut.
    (EVERY, Pieces(project="proj-b"), at(1, 21, 57), "Nov-01-2026 21:57"),
    # The name the form shows as its fields change.
    (OWN, in_project("sonnet"), at(8, 19, 10), "proj-a-sonnet-Nov-08 19:10"),
    (OWN, in_project("opus"), at(8, 19, 10), "proj-a-opus-Nov-08 19:10"),
    (EVERY, seated("planner"), at(8, 19, 10), "planner-Nov-08-2026 19:10"),
    (EVERY, Pieces(project="proj-b"), at(8, 19, 10), "Nov-08-2026 19:10"),
]


@pytest.mark.parametrize("template, pieces, now, name", WORKED)
def test_the_worked_names(template, pieces, now, name):
    assert names.render(template, pieces, now=now) == name


# -- the pieces -----------------------------------------------------------------------------


def test_every_piece_is_filled_in_the_order_written():
    pieces = Pieces(project="proj-a", agent="planner", model="opus", identity="scout",
                    scope="box-1")
    template = "<agent> <model> in <project> as <identity>/<scope> <instance> <date> <time>"
    assert names.render(template, pieces, now=at(8, 9, 5)) == \
        "planner opus in proj-a as scout/box-1 1 20261108 0905"


def test_a_scope_is_rendered_only_beside_an_identity():
    assert names.render("<project>-<scope>", Pieces(project="proj-a", scope="box-1"),
                        now=at(8, 9, 5)) == "proj-a"


def test_the_shipped_template_leaves_out_a_model_on_the_default():
    assert names.render(names.DEFAULT, Pieces(project="proj-a"), now=at(8, 9, 5)) == \
        "proj-a-Nov-08 09:05"


@pytest.mark.parametrize("text, spelled", [
    ("yyyy-mm-dd", "%Y-%m-%d"), ("yy", "%y"), ("mmm-dd", "%b-%d"),
    ("hh:mm:ss", "%H:%M:%S"), ("mm hh mm", "%m %H %M"), ("MMM-DD HH:MM", "%b-%d %H:%M"),
    ("week %W", "week %W"), ("at noon", "at noon"),
])
def test_a_date_format_is_read_by_its_tokens(text, spelled):
    assert names.date_format(text) == spelled


@pytest.mark.parametrize("raw, name", [
    ("--a---b--", "a-b"), ("café notes", "caf- notes"), ("-:x", "x"), ("", "launch"),
    ("- - -", "launch"), ("a" * 70, "a" * 63), ("a" * 62 + "-b", "a" * 62),
    ("keeps: its spaces", "keeps: its spaces"),
])
def test_a_name_is_cleaned(raw, name):
    assert names.clean(raw) == name


# -- the check before a save ----------------------------------------------------------------


@pytest.mark.parametrize("template", [
    "", "plain", names.DEFAULT, EVERY, "<date:yyyy.mm.dd>", "<time:hh:mm>",
    "<project><agent><model><identity><scope><instance><date><time>",
])
def test_a_template_of_known_pieces_is_taken(template):
    assert names.refusal(template) is None


@pytest.mark.parametrize("template, word", [
    ("<n>", "<n>"), ("<project>-<branch>", "<branch>"), ("<Agent>", "<Agent>"),
    ("<>", "<>"), ("<dates>", "<dates>"),
    ("<agent", "not part of a piece"), ("agent>", "not part of a piece"),
    ("<project>-<date:a<b>", "<b>"), ("a > b", "not part of a piece"),
])
def test_a_template_with_an_unknown_piece_or_a_stray_bracket_is_refused(template, word):
    refused = names.refusal(template)
    assert isinstance(refused, catalog.Refused) and refused.code == "bad-template"
    assert word in refused.problem and "<instance>" in refused.hint
    with pytest.raises(names.BadTemplate):
        names.render(template, Pieces(), now=at(8, 9, 5))


# -- which template -------------------------------------------------------------------------


def test_a_project_with_a_template_of_its_own_takes_it_and_another_the_one_for_all():
    settings = {names.TEMPLATE: EVERY, names.TEMPLATES: {"proj-a": OWN, "proj-b": ""}}
    assert names.chosen(settings, "proj-a") == names.Chosen(OWN, names.PROJECT)
    assert names.chosen(settings, "proj-b") == names.Chosen(EVERY, names.GLOBAL)
    assert names.chosen(settings, None) == names.Chosen(EVERY, names.GLOBAL)


@pytest.mark.parametrize("settings, project, key, source", [
    ({names.TEMPLATE: "<n>", names.TEMPLATES: {}}, None, "name.template", names.GLOBAL),
    ({names.TEMPLATE: EVERY, names.TEMPLATES: {"proj-a": "<agent"}}, "proj-a",
     "name.templates.proj-a", names.PROJECT),
])
def test_a_refused_template_in_the_settings_gives_the_shipped_one_and_a_warning(
        settings, project, key, source):
    got = names.chosen(settings, project)
    assert (got.template, got.source) == (names.DEFAULT, source)
    assert f"launcher {key} is not used" in got.warning


# -- the number -----------------------------------------------------------------------------


def test_instance_is_the_lowest_number_no_live_launch_is_named_with():
    pieces, now = Pieces(project="proj-a"), at(8, 9, 5)
    template = "<project>-<instance>"
    assert names.number(template, pieces, now=now) == 1
    assert names.number(template, pieces, now=now, taken={"proj-a-1", "proj-a-3"}) == 2
    assert names.render(template, pieces, now=now,
                        taken={"proj-a-1", "proj-a-2", "proj-b-3"}) == "proj-a-3"
    # A template without the piece is not numbered, whatever is taken.
    assert names.number("<project>", pieces, now=now, taken={"proj-a"}) == 1
    assert names.render("<project>", pieces, now=now, taken={"proj-a"}) == "proj-a"


def seeded(conn, request_id, *, name=None, identity=None, running=True,
           at_="2026-11-08T16:27:00.000000Z"):
    values = {"model": "opus"} if name is None else {"model": "opus", "name": name}
    store.insert(conn, request_id=request_id, actor="alice", harness="claude",
                 target="terminal", cwd="/work/proj-b", values=values, argv=["claude"],
                 env={}, posture="operator", session_id=f"session-{request_id}", slot=None,
                 identity=identity, at=at_)
    me = proc.identity(os.getpid())
    facts = {"pid": me.pid, "pid_started": me.started} if running else {}
    store.set_state(conn, request_id, "started", **facts)
    return store.get(conn, request_id)


def test_the_taken_names_are_those_of_the_live_launches():
    conn = store.connect()
    try:
        seeded(conn, "live", name="proj-a-1")
        seeded(conn, "ended", name="proj-a-2", running=False)
        seeded(conn, "unnamed")
        assert names.taken(conn) == {"proj-a-1"}
    finally:
        conn.close()


def test_a_launch_is_shown_by_its_name_else_its_identity_else_its_folder_and_start():
    conn = store.connect()
    try:
        named = seeded(conn, "a", name="proj-a-1", identity="planner")
        bound = seeded(conn, "b", identity="planner")
        bare = seeded(conn, "c")
    finally:
        conn.close()
    started = store.parse_time(bare.created_at).astimezone()
    assert names.shown(named) == "proj-a-1" and names.shown(bound) == "planner"
    assert names.shown(bare) == f"proj-b {started:%H:%M}"


# -- the launch -----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_turns(monkeypatch):
    fake_turns.hide(monkeypatch)


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


@pytest.fixture
def work(tmp_path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def no_exec(monkeypatch):
    """exec stood in for, so a launch in this terminal returns and its row records this
    process, which runs."""
    monkeypatch.setattr(targets, "_execve", lambda command, argv, env: None)
    here = os.getcwd()
    yield
    os.chdir(here)


def request(work: Path, *, rid: str = "r1", sid: str | None = SID, seat=None,
            **typed) -> LaunchRequest:
    return LaunchRequest(seat=seat, typed=typed, cwd=str(work), request_id=rid,
                         session_id=sid, actor="alice")


def row(rid: str = "r1") -> store.LaunchRow:
    conn = store.connect(create=False)
    try:
        return store.get(conn, rid)
    finally:
        conn.close()


def settings_file(text: str) -> None:
    path = Layout("launcher").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_presets(text: str) -> None:
    file = presets.path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)


def shown_as(argv) -> str | None:
    """The display name a Claude argv passes, or None."""
    argv = list(argv)
    return argv[argv.index("-n") + 1] if "-n" in argv else None


def test_a_launch_that_sets_no_name_takes_its_templates(work, harness, no_exec):
    result = api.launch(request(work, model="opus"))
    got = row().values
    assert got["name"] == "work-opus-Nov-08 12:45"
    # Claude Code shows it, and the row it was not typed into stays unset.
    assert shown_as(result.argv) == "work-opus-Nov-08 12:45" and got["native_name"] == ""
    assert result.warnings == ()


def test_a_preview_shows_the_name_the_launch_takes_and_writes_nothing(work, harness):
    shown = api.preview(request(work, model="opus"))
    assert shown.values["name"] == "work-opus-Nov-08 12:45"
    assert shown_as(shown.argv) == "work-opus-Nov-08 12:45"
    assert not store.path().exists()


def test_a_typed_name_is_the_launchs_name(work, harness, no_exec):
    result = api.launch(request(work, name="The night shift"))
    assert row().values["name"] == "The night shift"
    assert shown_as(result.argv) == "The night shift"


def test_a_seat_fills_the_agent_piece_with_its_name(work, harness, no_exec):
    settings_file('[name]\ntemplate = "<agent>-<model>"\n')
    write_presets('[seat.planner]\nharness = "claude"\nmodel = "opus"\nagent = "helper"\n')
    api.launch(request(work, seat="planner"))
    api.launch(request(work, rid="r2", sid=SID2, agent="helper"))
    assert row("r1").values["name"] == "planner-opus"
    assert row("r2").values["name"] == "helper"


@pytest.mark.parametrize("layer", ["defaults", "preset", "typed"])
def test_a_native_name_a_layer_sets_is_the_display_name(work, harness, no_exec, layer):
    typed, seat = {}, None
    if layer == "defaults":
        write_presets('[defaults]\nnative_name = "lead"\n')
    elif layer == "preset":
        write_presets('[seat.planner]\nharness = "claude"\nnative_name = "lead"\n')
        seat = "planner"
    else:
        typed = {"native_name": "lead"}
    result = api.launch(request(work, seat=seat, model="opus", **typed))
    got = row().values
    assert shown_as(result.argv) == "lead" and got["native_name"] == "lead"
    assert got["name"] == "work-opus-Nov-08 12:45"


def test_a_native_name_wins_over_a_typed_name(work, harness, no_exec):
    result = api.launch(request(work, name="mine", native_name="lead"))
    assert shown_as(result.argv) == "lead" and row().values["name"] == "mine"


def test_a_codex_launch_records_its_name_and_passes_none(work, harness, no_exec):
    result = api.launch(request(work, sid=None, harness="codex", model="gpt"))
    assert row().values["name"] == "work-gpt-Nov-08 12:45"
    assert "native_name" not in row().values
    assert not any("work-gpt" in word for word in result.argv) and "-n" not in result.argv


def test_a_replay_keeps_its_rows_name(work, harness, no_exec, monkeypatch):
    api.launch(request(work, model="opus"))
    monkeypatch.setattr(names, "now", lambda: at(9, 7, 30))
    replayed = api.launch(ReplayRequest(row("r1"), relaunch_of="r1", request_id="r2",
                                        actor="alice"))
    assert row("r2").values["name"] == "work-opus-Nov-08 12:45"
    assert shown_as(replayed.argv) == "work-opus-Nov-08 12:45"
    fresh = api.launch(request(work, rid="r3", sid=SID2, model="opus"))
    assert shown_as(fresh.argv) == row("r3").values["name"] == "work-opus-Nov-09 07:30"


def test_a_refused_template_in_the_settings_names_the_launch_by_the_shipped_one(
        work, harness, no_exec):
    settings_file('[name]\ntemplate = "<project>-<n>"\n')
    seen = []
    result = api.launch(request(work, model="opus"), warn=seen.append)
    assert row().values["name"] == "work-opus-Nov-08 12:45"
    [line] = result.warnings
    assert "launcher name.template is not used" in line and seen == [line]


def test_instance_counts_past_the_live_launches_of_the_name(work, harness, no_exec):
    settings_file('[name]\ntemplate = "<project>-<instance>"\n')
    assert api.preview(request(work)).values["name"] == "work-1"
    api.launch(request(work))
    api.launch(request(work, rid="r2", sid=SID2))
    assert [row(rid).values["name"] for rid in ("r1", "r2")] == ["work-1", "work-2"]
    assert api.preview(request(work, rid="r3")).values["name"] == "work-3"


# -- the command line -----------------------------------------------------------------------


@pytest.mark.parametrize("harness_name", catalog.HARNESSES)
def test_name_is_a_flag_of_every_launch_and_is_not_passed_on(harness_name):
    parsed = catalog.parse(["--name", "The night shift", "--model", "m"], harness_name)
    assert parsed.values["name"] == "The night shift"
    assert parsed.unknown == () and parsed.extra == () and parsed.warning is None


def test_the_command_names_the_launch_it_previews(work, monkeypatch, capsys):
    monkeypatch.chdir(work)
    assert cli.main(["preview", "--name", "mine", "--json"]) == codes.OK
    seen = capsys.readouterr()
    shown = json.loads(seen.out)
    assert shown["values"]["name"] == "mine" and shown_as(shown["argv"]) == "mine"
    assert "passing on" not in seen.err


def test_a_preset_may_name_its_launches(work, harness, no_exec):
    write_presets('[seat.planner]\nharness = "claude"\nname = "Planner desk"\n')
    result = api.launch(request(work, seat="planner"))
    assert row().values["name"] == shown_as(result.argv) == "Planner desk"


# -- without the UI shell -------------------------------------------------------------------

WITHOUT_UI = textwrap.dedent("""\
    import json
    import os
    import sys
    from datetime import datetime

    # As if the UI shell weren't installed.
    sys.modules["moxi_ui"] = None

    from moxi import proc

    import moxi_launcher.api
    from moxi_launcher import names, store

    me = proc.identity(os.getpid())
    conn = store.connect()
    store.insert(conn, request_id="running", actor="alice", harness="claude",
                 target="terminal", cwd="/work", values={"name": "proj-a-1"},
                 argv=["claude"], env={}, posture="operator", session_id="session-1",
                 slot=None, at="2026-01-02T00:00:00.000000Z")
    store.set_state(conn, "running", "started", pid=me.pid, pid_started=me.started)
    pieces = names.Pieces(project="proj-a")
    taken = names.taken(conn)
    now = datetime(2026, 11, 8, 12, 45)
    print(json.dumps({
        "taken": sorted(taken),
        "number": names.number("<project>-<instance>", pieces, now=now, taken=taken),
        "name": names.render("<project>-<instance>", pieces, now=now, taken=taken),
        "loaded": sorted(name for name in sys.modules
                         if name in ("moxi_launcher.page", "moxi_launcher.pagehandlers")
                         or name.startswith("moxi_ui."))}))
""")


def test_a_launch_is_named_and_numbered_without_the_ui(tmp_path):
    done = subprocess.run([sys.executable, "-P", "-c", WITHOUT_UI], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out == {"taken": ["proj-a-1"], "number": 2, "name": "proj-a-2", "loaded": []}
