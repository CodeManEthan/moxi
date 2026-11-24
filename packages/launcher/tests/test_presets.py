"""Presets: the file read in place, the resolution order, the one-preset rule, where a
session starts, the posture rule, the modes, projects through a fake module, and the
refusals of `presets check`."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import exit as codes
from moxi import modules
from moxi.config import ConfigError

from moxi_launcher import catalog, presets
from moxi_launcher.catalog import NeedsModule
from moxi_launcher.presets import Request


def write(text: str, file: Path | None = None) -> Path:
    file = file or presets.path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")
    return file


def only(*names):
    """An `available` answering yes for `names` only."""
    return lambda name: name in names


NONE = only()


def resolve(text: str = "", available=NONE, **request):
    found = presets.read(write(text))
    request.setdefault("cwd", "/work")
    return presets.resolve(Request(**request), presets=found, available=available)


def problems(text: str, available=NONE, **kw):
    return presets.check(write(text), available=available, **kw)


def errors(text: str, available=NONE, **kw):
    return [p for p in problems(text, available, **kw) if p.level == presets.ERROR]


# -- a fake projects module ---------------------------------------------------------------


class ProjectsError(Exception):
    def __init__(self, reason, fix=None):
        self.reason, self.fix = reason, fix
        super().__init__(reason)


class NotFound(ProjectsError):
    def __init__(self, key):
        self.key = key
        super().__init__(f"no project is named {key!r}", f"moxi projects add {key}")


class Unreadable(ProjectsError):
    pass


class TooNew(Unreadable):
    def __init__(self):
        super().__init__("the projects store is at schema version 9, newer than the 1 "
                         "this version of moxi-projects knows",
                         "upgrade it with `moxi install projects`")


class Busy(Unreadable):
    def __init__(self):
        super().__init__("the projects store stayed locked", "try again in a moment")


class Project(SimpleNamespace):
    @property
    def agents(self):
        return ((self.lead,) if self.lead else ()) + tuple(self.associated)


@pytest.fixture
def projects(monkeypatch, tmp_path):
    """projects installed, holding `moxi` (root tmp/moxi, lead planner, builder) and
    `app`. `fake.fail` makes the next calls raise; `fake.calls` records them."""
    roots = {"moxi": tmp_path / "moxi", "app": tmp_path / "app"}
    for root in roots.values():
        root.mkdir()
    table = {
        "moxi": Project(key="moxi", root=str(roots["moxi"]), lead="planner",
                        associated=("builder",)),
        "app": Project(key="app", root=str(roots["app"]), lead=None, associated=()),
    }
    fake = SimpleNamespace(fail=None, calls=[], roots=roots)

    def get(key):
        fake.calls.append(("get", key))
        if fake.fail:
            raise fake.fail
        for k, project in table.items():
            if k.lower() == key.lower():
                return project
        raise NotFound(key)

    def for_path(path):
        fake.calls.append(("for_path", path))
        if fake.fail:
            raise fake.fail
        for project in table.values():
            if path == project.root or path.startswith(project.root + os.sep):
                return project
        return None

    api = SimpleNamespace(get=get, for_path=for_path, ProjectsError=ProjectsError,
                          NotFound=NotFound, Unreadable=Unreadable, TooNew=TooNew, Busy=Busy)
    found = modules.Found(name="projects", state=modules.PRESENT, interfaces=frozenset({1}),
                          api=api)
    real_available, real_load = modules.available, modules.load
    monkeypatch.setattr(modules, "available",
                        lambda name: name == "projects" or real_available(name))
    monkeypatch.setattr(modules, "load", lambda name, interface=None: found
                        if name == "projects" else real_load(name, interface=interface))
    return fake


WITH_PROJECTS = only("projects")


# -- the file -----------------------------------------------------------------------------


def test_the_file_lives_in_the_launchers_config_dir():
    from moxi.paths import Layout

    assert presets.path() == Layout("launcher").config_dir() / "presets.toml"


def test_a_missing_file_reads_empty():
    found = presets.read()
    assert (dict(found.defaults), dict(found.seats), dict(found.projects)) == ({}, {}, {})
    assert presets.check() == []


def test_the_file_is_read_in_place():
    write('[defaults]\nmodel = "opus"\n[seat.planner]\nharness = "claude"\n'
          '[project.moxi]\ncwd = "/srv/moxi"\n')
    found = presets.read()
    assert found.defaults["model"] == "opus"
    assert found.seats["planner"].values == {"harness": "claude"}
    assert found.projects["moxi"].where == "project.moxi"
    assert [p.where for p in found.all()] == ["seat.planner", "project.moxi"]


@pytest.mark.parametrize("text, key", [
    ("[other]\nx = 1\n", "other"),
    ("model = 'opus'\n", "model"),
    ("[seat]\nplanner = 'x'\n", "seat.planner"),
    ("[defaults\n", None),
])
def test_a_file_that_does_not_hold_to_the_format_is_a_config_error(key, text):
    write(text)
    with pytest.raises(ConfigError) as caught:
        presets.read()
    assert caught.value.key == key
    assert str(presets.path()) in str(caught.value)


# -- the resolution order -----------------------------------------------------------------


def test_a_plain_launch_takes_the_shipped_defaults():
    got = resolve()
    assert got.harness == "claude" and got.mode == "interactive"
    assert got.values["permission_mode"] == "default"
    assert got.values["model"] == ""
    assert got.sources["model"] == presets.SHIPPED
    assert got.preset_kind is None and got.chosen == frozenset()
    assert got.cwd == "/work" and got.values["cwd"] == "/work"
    assert got.target == "terminal"


def test_a_launch_with_usage_warns_at_2x_unless_it_sets_another_mark(monkeypatch):
    """Every launch with usage installed is stamped with a mark: its own, else the shipped
    default of 2x. An install without usage neither offers the row nor stamps it."""
    from moxi_launcher import compose
    from moxi_launcher.compose import Context

    def stamped(got):
        ctx = Context(request_id="r1", session_id="5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f",
                      actor="ann", runs_dir="/runs", windows=False)
        return compose.compose(dict(got.values), ctx).env.get("MOXI_USAGE_WARN_AT")

    with_usage = only("usage")
    got = resolve(available=with_usage)
    assert got.values["warn_at"] == "2x" and got.sources["warn_at"] == presets.SHIPPED
    assert "usage" in got.links and stamped(got) == "2x"
    assert stamped(resolve(available=with_usage, typed={"warn_at": "1.5x"})) == "1.5x"
    assert stamped(resolve(available=with_usage, typed={"warn_at": "off"})) == "off"

    real = modules.load
    monkeypatch.setattr(modules, "load", lambda name, **kw: (
        SimpleNamespace(ok=False, api=None) if name == "usage" else real(name, **kw)))
    got = resolve()
    assert "warn_at" not in catalog.offered("claude", NONE)
    assert "warn_at" not in got.values and "usage" not in got.links
    assert stamped(got) is None


def test_a_seat_or_codex_launch_warns_only_at_a_mark_it_sets():
    """The 2x default reaches one-off, project and scratch launches only. A seat outside
    a scratch folder, or a Codex launch, holds no mark unless `[defaults]`, its preset or
    a flag sets one; then it holds that mark."""
    with_usage = only("usage")
    seat = '[seat.s]\nmodel = "opus"\n'

    def mark(text="", **request):
        got = resolve(text, available=with_usage, **request)
        return got.values["warn_at"], "usage" in got.links

    assert mark(seat, seat="s") == ("", False)
    assert mark(seat, seat="s", left_out=["warn_at"]) == ("", False)
    assert mark(typed={"harness": "codex"}) == ("", False)
    assert mark('[seat.s]\nwarn_at = "1x"\n', seat="s") == ("1x", True)
    assert mark(seat, seat="s", typed={"warn_at": "1.5x"}) == ("1.5x", True)
    assert mark('[defaults]\nwarn_at = "off"\n' + seat, seat="s") == ("off", True)
    assert mark(typed={"harness": "codex", "warn_at": "2x"}) == ("2x", True)
    assert mark() == ("2x", True)
    assert mark('[project.p]\nmodel = "opus"\n', project_preset="p") == ("2x", True)
    assert mark('[seat.s]\nscratch = true\n', seat="s") == ("2x", True)


def test_each_later_layer_wins():
    text = ('[defaults]\nmodel = "sonnet"\neffort = "low"\nnative_name = "d"\n'
            '[seat.planner]\nmodel = "opus"\neffort = "high"\n')
    got = resolve(text, seat="planner", typed={"model": "fable"})
    assert got.values["model"] == "fable" and got.sources["model"] == presets.TYPED
    assert got.values["effort"] == "high" and got.sources["effort"] == presets.PRESET
    assert got.values["native_name"] == "d" and got.sources["native_name"] == "defaults"
    assert got.values["tools"] == [] and got.sources["tools"] == presets.SHIPPED
    assert got.chosen == {"model", "effort"}


def test_typed_flags_win_over_the_preset_through_the_parser():
    found = presets.read(write('[seat.planner]\nmodel = "opus"\neffort = "high"\n'))
    parsed = catalog.parse(["--model", "sonnet", "--weird", "--", "x"], "claude",
                           available=NONE)
    got = presets.resolve(Request(seat="planner", typed=parsed.values, extra=parsed.extra,
                                  cwd="/work"), presets=found, available=NONE)
    assert got.values["model"] == "sonnet" and got.values["effort"] == "high"
    assert got.values["extra_flags"] == ["--weird", "x"]
    assert "extra_flags" in got.chosen


def test_defaults_for_another_harness_are_left_out():
    got = resolve('[defaults]\nsandbox = "read-only"\nmodel = "opus"\n')
    assert "sandbox" not in got.values and got.values["model"] == "opus"


def test_the_harness_comes_from_typed_then_preset_then_defaults():
    text = '[defaults]\nharness = "codex"\n[seat.c]\nharness = "claude"\n'
    assert resolve(text).harness == "codex"
    assert resolve(text, seat="c").harness == "claude"
    assert resolve(text, seat="c", typed={"harness": "codex"}).harness == "codex"


def test_harness_of_reads_the_preset_before_the_flags_are_parsed():
    found = presets.read(write('[defaults]\nharness = "codex"\n'
                               '[project.p]\nharness = "claude"\n'))
    assert presets.harness_of(Request(project_preset="p"), presets=found) == "claude"
    assert presets.harness_of(Request(), presets=found) == "codex"
    assert presets.harness_of(Request(), presets=presets.read(write(""))) == "claude"


def test_lists_are_never_shared_between_launches():
    first, second = resolve(), resolve()
    first.values["tools"].append("x")
    assert second.values["tools"] == []
    assert catalog.load("claude")["tools"].default == []


def test_values_json_carries_the_mode_and_off_posture():
    got = resolve()
    record = got.values_json()
    assert record["mode"] == "interactive" and record["off_posture"] is False
    assert record["model"] == got.values["model"]


# -- the one-preset rule ------------------------------------------------------------------


def test_a_launch_names_at_most_one_preset():
    text = '[seat.s]\nmodel = "opus"\n[project.p]\ncwd = "/srv"\n'
    with pytest.raises(catalog.BadValue) as caught:
        resolve(text, seat="s", project_preset="p")
    assert caught.value.exit_code == codes.USAGE


def test_values_are_never_drawn_from_a_second_preset():
    text = ('[seat.s]\nmodel = "opus"\n'
            '[project.p]\ncwd = "/srv"\neffort = "high"\nmodel = "sonnet"\n')
    got = resolve(text, seat="s")
    assert got.values["model"] == "opus"
    assert got.values["effort"] == "" and got.cwd == "/work"
    assert (got.preset_kind, got.preset_name) == ("seat", "s")
    got = resolve(text, project_preset="p")
    assert got.values["model"] == "sonnet" and got.cwd == "/srv"
    assert (got.preset_kind, got.preset_name) == ("project", "p")


def test_a_seat_and_a_project_preset_of_one_name_stay_apart():
    text = '[seat.moxi]\nmodel = "opus"\n[project.moxi]\nmodel = "sonnet"\n'
    assert resolve(text, seat="moxi").values["model"] == "opus"
    assert resolve(text, project_preset="moxi").values["model"] == "sonnet"


@pytest.mark.parametrize("request_", [{"seat": "nobody"}, {"project_preset": "nowhere"}])
def test_an_unknown_preset_is_refused(request_):
    with pytest.raises(presets.UnknownPreset) as caught:
        resolve('[seat.s]\nmodel = "opus"\n', **request_)
    assert caught.value.exit_code == codes.REFUSED
    assert "presets list" in caught.value.hint


def test_a_bad_preset_refuses_the_launch_naming_its_problem():
    with pytest.raises(presets.BadPreset) as caught:
        resolve('[seat.s]\nmodel = 3\n', seat="s")
    assert caught.value.exit_code == codes.USAGE
    assert "seat.s" in caught.value.problem and caught.value.row == "model"


def test_another_presets_problem_does_not_refuse_a_launch():
    got = resolve('[seat.good]\nmodel = "opus"\n[seat.bad]\nmodel = 3\n', seat="good")
    assert got.values["model"] == "opus"


def test_a_preset_key_of_another_harness_refuses_the_launch():
    text = '[seat.s]\nharness = "claude"\npermission_mode = "plan"\n'
    with pytest.raises(presets.BadPreset) as caught:
        resolve(text, seat="s", typed={"harness": "codex"}, off_posture=True)
    assert "not a codex row" in caught.value.problem


# -- where the session starts -------------------------------------------------------------


def test_the_latest_layer_naming_a_place_wins_whole(tmp_path):
    text = f'[defaults]\nscratch = true\n[seat.s]\ncwd = "{tmp_path}"\n'
    got = resolve(text)
    assert got.scratch and got.cwd is None and got.values["cwd"] == ""
    got = resolve(text, seat="s")
    assert not got.scratch and got.cwd == str(tmp_path)
    assert got.sources["cwd"] == presets.PRESET
    got = resolve(text, seat="s", typed={"cwd": "/typed"})
    assert got.cwd == "/typed" and got.sources["cwd"] == presets.TYPED


def test_a_typed_place_drops_the_presets_project(projects):
    got = resolve('[project.p]\nproject = "moxi"\n', available=WITH_PROJECTS,
                  project_preset="p", typed={"cwd": "/elsewhere"})
    assert got.cwd == "/elsewhere" and got.project_key is None
    assert got.values["project"] == ""


def test_two_places_in_one_preset_are_refused():
    text = '[seat.s]\ncwd = "/a"\nscratch = true\n'
    with pytest.raises(presets.BadPreset):
        resolve(text, seat="s")
    found = errors(text)
    assert [(p.where, p.code) for p in found] == [("seat.s", "two-places")]


def test_a_seat_home_path():
    got = resolve('[seat.s]\nhome = "~/work/x"\n', seat="s")
    assert got.cwd == str(Path("~/work/x").expanduser())


def test_a_seat_home_key_needs_projects():
    with pytest.raises(presets.NeedsProjects) as caught:
        resolve('[seat.s]\nhome = "moxi"\n', seat="s")
    assert caught.value.exit_code == codes.REFUSED


def test_a_seat_home_key_is_the_projects_root(projects):
    got = resolve('[seat.s]\nhome = "Moxi"\n', available=WITH_PROJECTS, seat="s")
    assert got.cwd == str(projects.roots["moxi"])
    assert got.project_key == "moxi" and got.values["project"] == "moxi"
    assert got.agents == ("planner", "builder")
    assert "projects" in got.links


def test_home_is_only_a_seats():
    found = errors('[project.p]\nhome = "/x"\n')
    assert [(p.where, p.key, p.code) for p in found] == [("project.p", "home",
                                                          "unknown-key")]


def test_a_project_preset_starts_in_its_projects_root(projects):
    got = resolve('[project.p]\nproject = "moxi"\n', available=WITH_PROJECTS,
                  project_preset="p")
    assert got.cwd == str(projects.roots["moxi"]) and got.project_key == "moxi"
    assert ("get", "moxi") in projects.calls


def test_a_typed_dir_gets_its_project_key(projects):
    inside = projects.roots["app"] / "src"
    got = resolve(available=WITH_PROJECTS, typed={"cwd": str(inside)})
    assert got.cwd == str(inside) and got.project_key == "app"
    assert projects.calls == [("for_path", str(inside))]


def test_the_callers_dir_gets_its_project_key(projects):
    got = resolve(available=WITH_PROJECTS, cwd=str(projects.roots["moxi"]))
    assert got.project_key == "moxi"
    assert resolve(available=WITH_PROJECTS, cwd="/nowhere").project_key is None


def test_without_projects_nothing_reads_it(projects):
    got = resolve(available=NONE, typed={"cwd": str(projects.roots["moxi"])})
    assert got.project_key is None and projects.calls == []


def test_a_project_key_without_projects_is_refused():
    with pytest.raises(presets.NeedsProjects):
        resolve('[project.p]\nproject = "moxi"\n', project_preset="p")


def test_an_unknown_project_key_is_refused_with_a_hint(projects):
    with pytest.raises(presets.UnknownProject) as caught:
        resolve('[project.p]\nproject = "gone"\n', available=WITH_PROJECTS,
                project_preset="p")
    assert caught.value.exit_code == codes.REFUSED
    assert caught.value.key == "gone" and "moxi projects add gone" in caught.value.hint


def test_unreadable_projects_refuse_with_reason_and_fix(projects):
    projects.fail = Unreadable("the store is corrupt", "moxi projects doctor")
    with pytest.raises(presets.ProjectsUnreadable) as caught:
        resolve('[project.p]\nproject = "moxi"\n', available=WITH_PROJECTS,
                project_preset="p")
    assert caught.value.exit_code == codes.REFUSED
    assert "corrupt" in caught.value.problem and caught.value.hint == "moxi projects doctor"


def test_a_projects_store_too_new_to_read_refuses_like_an_unreadable_one(projects):
    projects.fail = TooNew()
    with pytest.raises(presets.ProjectsUnreadable) as caught:
        resolve('[project.p]\nproject = "moxi"\n', available=WITH_PROJECTS,
                project_preset="p")
    assert caught.value.exit_code == codes.REFUSED
    assert "newer" in caught.value.problem and "moxi install projects" in caught.value.hint
    found = errors('[project.p]\nproject = "moxi"\n', available=WITH_PROJECTS)
    assert [(p.code, p.exit_code) for p in found] == [("projects-unreadable",
                                                       codes.REFUSED)]


def test_busy_projects_exit_75(projects):
    projects.fail = Busy()
    with pytest.raises(presets.ProjectsBusy) as caught:
        resolve(available=WITH_PROJECTS)
    assert caught.value.exit_code == codes.TEMPFAIL


# -- the posture rule ---------------------------------------------------------------------


def test_a_preset_off_the_shipped_posture_needs_off_posture():
    text = '[seat.s]\npermission_mode = "bypassPermissions"\n'
    with pytest.raises(presets.OffPosture) as caught:
        resolve(text, seat="s")
    assert caught.value.exit_code == codes.REFUSED
    assert "'bypassPermissions'" in caught.value.problem
    assert "'default'" in caught.value.problem
    got = resolve(text, seat="s", off_posture=True)
    assert got.values["permission_mode"] == "bypassPermissions"
    assert got.off_posture and got.values_json()["off_posture"] is True


def test_the_users_default_is_the_effective_default():
    text = ('[defaults]\npermission_mode = "bypassPermissions"\n'
            '[seat.same]\npermission_mode = "bypassPermissions"\n'
            '[seat.other]\npermission_mode = "plan"\n')
    assert resolve(text).values["permission_mode"] == "bypassPermissions"
    assert resolve(text, seat="same").values["permission_mode"] == "bypassPermissions"
    with pytest.raises(presets.OffPosture) as caught:
        resolve(text, seat="other")
    assert "'bypassPermissions'" in caught.value.problem


def test_a_typed_posture_value_off_the_default_needs_off_posture():
    with pytest.raises(presets.OffPosture):
        resolve(typed={"permission_mode": "plan"})
    with pytest.raises(presets.OffPosture):
        resolve(typed={"allowed_tools": ["Bash"]})
    assert resolve(typed={"permission_mode": "default"}).values["permission_mode"] \
        == "default"


def test_codex_posture_rows():
    with pytest.raises(presets.OffPosture):
        resolve('[seat.c]\nharness = "codex"\nsandbox = "danger-full-access"\n', seat="c")
    got = resolve('[seat.c]\nharness = "codex"\nsandbox = "workspace-write"\n', seat="c")
    assert got.values["sandbox"] == "workspace-write"


def test_check_warns_of_a_preset_that_needs_off_posture():
    found = problems('[seat.s]\npermission_mode = "plan"\n')
    assert found == []  # a posture row's value is checked at launch, not here


# -- the start posture, identity and modes ------------------------------------------------


def test_identity_without_comms_is_ignored_with_a_warning():
    got = resolve('[seat.s]\nidentity = "planner"\nscope = "moxi"\n', seat="s")
    assert got.identity is None and got.scope is None
    assert got.values["identity"] == ""
    assert any("comms is not installed" in w for w in got.warnings)
    assert "comms" not in got.links


def test_identity_with_comms():
    got = resolve('[seat.s]\nidentity = "planner"\nscope = "moxi"\n',
                  available=only("comms"), seat="s")
    assert (got.identity, got.scope) == ("planner", "moxi") and "comms" in got.links


@pytest.mark.parametrize("given, warned", [
    ({"typed": {"director": ["boss"]}}, ["director"]),
    ({"typed": {"scope": "moxi"}}, ["scope"]),
    ({"text": '[seat.s]\ndirector = ["boss"]\nscope = "moxi"\n', "seat": "s"},
     ["director", "scope"]),
])
def test_a_director_or_scope_without_an_identity_is_ignored_with_a_warning(given,
                                                                          warned):
    got = resolve(available=only("comms"), **given)
    assert got.identity is None and "comms" not in got.links
    assert [w.split()[0] for w in got.warnings] == warned
    assert all(w.endswith("the launch has no identity, and only comms' wrapper takes it")
               for w in got.warnings)


def test_a_default_scope_without_an_identity_says_nothing():
    # Set for every launch, it is there for the seats with an identity.
    got = resolve('[defaults]\nscope = "moxi"\n', available=only("comms"))
    assert got.warnings == ()


def test_the_with_posture_needs_comms():
    with pytest.raises(presets.BadPreset):
        resolve(typed={"posture": "with:lead"})
    got = resolve(typed={"posture": "with:lead"}, available=only("comms"))
    assert got.posture == "with:lead"
    with pytest.raises(presets.BadPreset):
        resolve(typed={"posture": "later"})


def test_a_go_launch_with_nothing_to_start_on_is_refused():
    # Go with no brief is refused, since nobody directs the session.
    with pytest.raises(presets.PostureBrief) as caught:
        resolve(typed={"posture": "go"})
    assert (caught.value.code, caught.value.row) == ("posture-brief", "brief")
    assert "posture go needs a brief" in caught.value.problem
    with pytest.raises(presets.PostureBrief):
        resolve(typed={"posture": "go", "brief": "  \n"})


@pytest.mark.parametrize("first", [{"brief": "Build it."}, {"brief_file": "/w/brief.md"},
                                   {"handoff": "/w/handoff.md"}])
def test_a_go_launch_with_a_first_turn_resolves(first):
    assert resolve(typed={"posture": "go", **first}).posture == "go"


def test_a_go_launch_with_no_identity_starts_on_the_words_typed_after_the_dashes():
    # On a launch that binds no comms identity, any words typed after `--` carry the
    # task, flags alone included: they go last on the harness's argv.
    for tail in (["Carry on."], ["--verbose", "Carry on."], ["--verbose"]):
        got = resolve(typed={"posture": "go"}, extra=tail, tail=tail)
        assert got.posture == "go"
    # comms not installed: the identity is ignored, so the launch binds none.
    assert resolve(typed={"posture": "go", "identity": "builder"},
                   extra=["Carry on."], tail=["Carry on."]).posture == "go"


def test_a_go_launch_does_not_start_on_flags_passed_on_but_not_typed_after_the_dashes():
    # Unknown flags typed before `--`, the page's field (both reach `extra` with no
    # tail), a preset's `extra_flags` and `[defaults]`' carry no first turn.
    with pytest.raises(presets.PostureBrief) as caught:
        resolve(typed={"posture": "go"}, extra=["--add-dir", "Carry on."])
    assert "or its task after --" in caught.value.hint
    with pytest.raises(presets.PostureBrief):
        resolve('[seat.s]\nposture = "go"\nextra_flags = ["--add-dir", "/x"]\n', seat="s")
    with pytest.raises(presets.PostureBrief):
        resolve('[defaults]\nextra_flags = ["--append-system-prompt", "be terse"]\n',
                typed={"posture": "go"})


def test_a_go_launch_that_binds_an_identity_does_not_start_on_words_passed_on():
    with pytest.raises(presets.PostureBrief) as caught:
        resolve(typed={"posture": "go", "identity": "builder"}, extra=["Carry on."],
                tail=["Carry on."], available=only("comms"))
    assert "after --" not in caught.value.hint


def test_a_brief_under_with_is_refused():
    # Under with:<agent> that agent sends the first message.
    for first in ({"brief": "Build it."}, {"brief_file": "/w/brief.md"}):
        with pytest.raises(presets.PostureBrief) as caught:
            resolve(typed={"posture": "with:lead", **first}, available=only("comms"))
        assert caught.value.code == "posture-brief"
        assert caught.value.problem == ("posture with:lead takes no brief: lead sends the "
                                        "first message")
    got = resolve(typed={"posture": "with:lead"}, available=only("comms"))
    assert got.posture == "with:lead"


def test_a_handoff_under_with_is_sent_first_and_the_director_follows():
    # A typed handoff is the session's first turn under any posture; under
    # with:<agent> the director's message comes after it.
    for posture in ("with:lead", "operator", ""):
        got = resolve(typed={"posture": posture, "handoff": "/w/handoff.md"},
                      available=only("comms"))
        assert (got.posture, got.values["handoff"]) == (posture, "/w/handoff.md")


def test_a_resume_of_a_go_launch_needs_a_brief():
    # A resumed session does not wake itself either.
    with pytest.raises(presets.PostureBrief) as caught:
        presets.check_resume_brief("go", brief="  \n")
    assert (caught.value.code, caught.value.row) == ("posture-brief", "brief")
    assert caught.value.problem == ("posture go needs a brief on a resume: a resumed "
                                    "session does not wake itself either, so with no "
                                    "first turn it sits idle")
    assert caught.value.hint == "give the resume --brief or --brief-file"
    presets.check_resume_brief("go", brief="Carry on.")
    presets.check_resume_brief("go", brief_file="/w/brief.md")


def test_a_resume_takes_the_brief_as_a_launch_does_under_each_posture():
    for first in ({"brief": "Go."}, {"brief_file": "/w/brief.md"}):
        with pytest.raises(presets.PostureBrief) as caught:
            presets.check_resume_brief("with:lead", **first)
        assert caught.value.problem == ("posture with:lead takes no brief: lead sends the "
                                        "first message")
    presets.check_resume_brief("with:lead")
    for posture in ("operator", ""):
        presets.check_resume_brief(posture)
        presets.check_resume_brief(posture, brief="Read the design.")
    with pytest.raises(presets.BadValue) as caught:
        presets.check_resume_brief("go", brief="Go.", brief_file="/w/brief.md")
    assert (caught.value.row, caught.value.problem, caught.value.hint) == (
        "brief", "both brief and brief_file are given", "give one of them")


def test_a_brief_under_operator_or_no_posture_is_sent():
    # Under operator the session reads its brief and waits, as the operator ruled; with no
    # posture the brief goes as it always did. Neither needs one.
    for posture in ("operator", ""):
        assert resolve(typed={"posture": posture, "brief": "Read the design."}).posture \
            == posture
        assert resolve(typed={"posture": posture}).posture == posture


def test_a_go_posture_from_a_seat_needs_the_brief_too():
    with pytest.raises(presets.PostureBrief):
        resolve('[seat.s]\nposture = "go"\n', seat="s")
    assert resolve('[seat.s]\nposture = "go"\n', seat="s",
                   typed={"brief": "Go."}).posture == "go"


@pytest.mark.parametrize("harness, target, links, mode, turns", [
    ("claude", "terminal", (), "interactive", False),
    ("claude", "slot", ("turns",), "interactive", True),
    ("claude", "headless", (), "print", False),
    ("claude", "headless", ("turns",), "print", True),
    ("codex", "terminal", (), "interactive", False),
    ("codex", "headless", ("turns",), "exec", False),
    ("codex", "terminal", ("turns",), "host", True),
])
def test_the_mode(monkeypatch, harness, target, links, mode, turns):
    monkeypatch.setattr(presets, "_host_installed", lambda: True)
    assert presets.mode_of(harness, target, available=only(*links)) == (mode, turns, [])


@pytest.mark.parametrize("script, can_start, found", [
    (True, True, True), (False, True, False), (True, False, False), (True, None, False)])
def test_the_codex_host_counts_only_when_it_can_start(monkeypatch, tmp_path, script,
                                                      can_start, found):
    """The script installed and turns saying its host can start; `can_start` None is a
    turns that can't be reached at the interface the launcher calls."""
    from types import SimpleNamespace

    from moxi import paths

    from moxi_launcher import first

    host = tmp_path / presets.CODEX_HOST

    def script_of(name):
        if name == presets.CODEX_HOST and script:
            return host
        raise FileNotFoundError(name)

    monkeypatch.setattr(paths, "script", script_of)
    api = None if can_start is None else SimpleNamespace(
        codex_host_available=lambda: can_start)
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: api)
    assert presets.codex_host() == (str(host) if found else None)
    assert presets._host_installed() is found
    from moxi_launcher import compose

    assert compose.host_script() == presets.codex_host()
    assert presets.mode_of("codex", "terminal", available=only("turns"))[0] == (
        "host" if found else "interactive")


def test_codex_with_turns_but_no_host_runs_without_turns(monkeypatch):
    monkeypatch.setattr(presets, "_host_installed", lambda: False)
    got = resolve(typed={"harness": "codex"}, available=only("turns"))
    assert got.mode == "interactive" and "turns" not in got.links
    assert any(presets.CODEX_HOST in w for w in got.warnings)


def headless_comms(*harnesses, cold_gap_ok=True):
    """A comms whose `HEADLESS` names `harnesses` (None: a comms older than the name), and
    whose `parse_exit` takes the grammar of comms' own and records each call."""
    calls = []

    def parse_exit(values, *, harness):
        calls.append((list(values), harness))
        for value in values:
            kind, _, rest = value.partition("=")
            if value not in ("done", "cold") and not (
                    kind in ("idle", "after") and rest[:-1].isdigit() and rest[-1:] in "smh"
                    and rest[-1:]):
                raise ValueError(f"--exit {value}: give done, cold, idle=<D> or after=<D>, "
                                 "D like 90m or 2h")
        gap = any(v.startswith(("idle=", "after=")) for v in values) and "cold" not in values
        return SimpleNamespace(cold_gap=gap)

    api = SimpleNamespace(parse_exit=parse_exit, calls=calls)
    if harnesses != (None,):
        api.HEADLESS = frozenset(harnesses)
    return api


def headless_launch(monkeypatch, comms_api, **typed):
    monkeypatch.setattr(presets, "comms_api", lambda: comms_api)
    monkeypatch.setattr(presets, "_host_installed", lambda: True)
    typed = {"harness": "codex", "target": "headless", "identity": "x", **typed}
    return resolve(typed=typed, available=only("comms", "turns", "cache"))


@pytest.mark.parametrize("harness", ["claude", "codex"])
@pytest.mark.parametrize("comms_api", [None, headless_comms(None), headless_comms()],
                         ids=["no-comms-api", "older-comms", "names-none"])
def test_a_headless_launch_with_an_identity_waits_on_comms(monkeypatch, harness, comms_api):
    monkeypatch.setattr(presets, "comms_api", lambda: comms_api)
    with pytest.raises(presets.NotYet, match="headless") as caught:
        resolve(typed={"harness": harness, "target": "headless", "identity": "x"},
                available=only("comms"))
    assert caught.value.exit_code == codes.REFUSED


def test_the_headless_refusal_lifts_only_for_a_harness_comms_names(monkeypatch):
    comms_api = headless_comms("codex")
    got = headless_launch(monkeypatch, comms_api)
    # Codex headless with an identity runs comms' wrapper, which execs turns' host.
    assert got.mode == "host" and {"turns", "comms"} <= got.links and got.identity == "x"
    with pytest.raises(presets.NotYet, match="headless"):
        headless_launch(monkeypatch, comms_api, harness="claude")
    # Claude stays print where comms names it; the host's absence refuses Codex as today.
    assert headless_launch(monkeypatch, headless_comms("claude", "codex"),
                           harness="claude").mode == "print"
    monkeypatch.setattr(presets, "_host_installed", lambda: False)
    with pytest.raises(presets.NotYet, match="Codex host"):
        resolve(typed={"harness": "codex", "target": "headless", "identity": "x"},
                available=only("comms", "turns"))


def test_a_headless_launch_without_an_identity_keeps_its_mode(monkeypatch):
    monkeypatch.setattr(presets, "comms_api", lambda: headless_comms("codex"))
    assert resolve(typed={"harness": "codex", "target": "headless"},
                   available=only("comms", "turns")).mode == "exec"


def test_the_defaults_refuse_the_policy_opt_out_by_name_with_comms_installed():
    # One line there would opt every seat out; a seat opts out on its own.
    found = errors('[defaults]\ntakes_policy_directors = true\n', available=only("comms"))
    assert [(p.where, p.key, p.code) for p in found] == [
        ("defaults", "takes_policy_directors", "per-seat")]
    assert "every seat" in found[0].problem
    assert errors('[seat.s]\nidentity = "s"\ntakes_policy_directors = false\n',
                  available=only("comms")) == []
    with pytest.raises(presets.BadPreset) as raised:
        resolve('[defaults]\ntakes_policy_directors = false\n', available=only("comms"))
    assert raised.value.found.code == "per-seat"


def test_a_seat_opted_out_resolves_and_records_false():
    got = resolve('[seat.s]\nidentity = "s"\ntakes_policy_directors = false\n',
                  available=only("comms"), seat="s")
    assert got.values["takes_policy_directors"] is False
    assert resolve("", available=only("comms")).values["takes_policy_directors"] is True


def test_comms_has_policy_probes_for_the_call():
    assert presets.comms_has_policy(SimpleNamespace(policy_directors=lambda: ())) is True
    assert presets.comms_has_policy(SimpleNamespace()) is False
    assert presets.comms_has_policy(None) is False


@pytest.mark.parametrize("value, want", [
    (None, frozenset()), ("codex", frozenset()), (3, frozenset()),
    (["codex"], frozenset({"codex"})), (frozenset({"claude", "codex"}),
                                        frozenset({"claude", "codex"}))])
def test_comms_headless_reads_comms_list_with_a_type_guard(value, want):
    api = SimpleNamespace() if value is None else SimpleNamespace(HEADLESS=value)
    assert presets.comms_headless(api) == want
    assert presets.comms_headless(None) == frozenset()


def test_the_exit_rule_is_checked_with_comms_parser(monkeypatch):
    comms_api = headless_comms("codex")
    got = headless_launch(monkeypatch, comms_api, headless_exit=["idle=90m", "cold"])
    assert got.values["headless_exit"] == ["idle=90m", "cold"]
    assert comms_api.calls == [(["idle=90m", "cold"], "codex")]
    assert not any("cold" in w for w in got.warnings)


@pytest.mark.parametrize("rule", [["idle=90"], ["soon"], ["after=0x"]])
def test_an_exit_rule_comms_refuses_is_refused_at_resolve(monkeypatch, rule):
    with pytest.raises(catalog.BadValue) as caught:
        headless_launch(monkeypatch, headless_comms("codex"), headless_exit=rule)
    assert caught.value.exit_code == codes.USAGE and caught.value.row == "headless_exit"
    # comms' own words, naming the fix.
    assert f"--exit {rule[0]}: give done, cold" in caught.value.problem


def test_check_refuses_an_exit_rule_comms_refuses(monkeypatch, tmp_path):
    from moxi_launcher import api, slots

    monkeypatch.setattr(presets, "comms_api", lambda: headless_comms("codex"))
    monkeypatch.setattr(presets, "_host_installed", lambda: True)
    monkeypatch.setattr(slots, "links", lambda name: name in ("comms", "turns"))
    (refused,) = api.check(api.LaunchRequest(
        typed={"harness": "codex", "target": "headless", "identity": "x", "brief": "b",
               "headless_exit": ["idle=90"]}, cwd=str(tmp_path)))
    assert isinstance(refused, catalog.BadValue) and refused.row == "headless_exit"


def test_no_exit_rule_asks_comms_nothing_and_leaves_its_default(monkeypatch):
    comms_api = headless_comms("codex")
    got = headless_launch(monkeypatch, comms_api)
    assert got.values["headless_exit"] == [] and comms_api.calls == []


@pytest.mark.parametrize("rule, gap", [
    (["idle=90m"], True), (["after=2h"], True), (["done", "after=2h"], True),
    (["idle=90m", "cold"], False), (["done"], False), (["cold"], False)])
def test_idle_or_after_without_cold_warns_of_the_cold_gap(monkeypatch, rule, gap):
    got = headless_launch(monkeypatch, headless_comms("codex"), headless_exit=rule)
    assert (presets.COLD_GAP in got.warnings) is gap


@pytest.mark.parametrize("typed", [
    {"target": "terminal"}, {"identity": ""}, {"target": "slot"}],
    ids=["terminal", "no-identity", "slot"])
def test_an_exit_rule_on_any_other_launch_is_left_out_with_a_warning(monkeypatch, typed):
    comms_api = headless_comms("codex")
    got = headless_launch(monkeypatch, comms_api, headless_exit=["idle=90m"], **typed)
    assert got.values["headless_exit"] == [] and comms_api.calls == []
    assert any(w.startswith("headless_exit is ignored") for w in got.warnings)
    assert presets.COLD_GAP not in got.warnings


def test_an_exit_rule_from_defaults_is_left_out_without_a_warning(monkeypatch):
    monkeypatch.setattr(presets, "comms_api", lambda: headless_comms("codex"))
    got = resolve('[defaults]\nheadless_exit = ["cold"]\n',
                  typed={"harness": "codex"}, available=only("comms"))
    assert got.values["headless_exit"] == []
    assert not any("headless_exit" in w for w in got.warnings)


def test_the_exit_rule_needs_comms(monkeypatch):
    monkeypatch.setattr(presets, "comms_api", lambda: None)
    with pytest.raises(NeedsModule):
        resolve(typed={"harness": "codex", "target": "headless",
                       "headless_exit": ["cold"]}, available=only("turns"))


@pytest.mark.parametrize("row, value", [("keep_warm", "on"), ("recycle_on_expiry", "clear"),
                                        ("recycle_on_expiry", "compact")])
def test_the_cache_timer_rows_are_left_out_of_a_headless_launch_with_an_identity(
        monkeypatch, row, value):
    monkeypatch.setattr(catalog, "session_env_readable", lambda: True)
    got = headless_launch(monkeypatch, headless_comms("claude", "codex"), harness="claude",
                          brief="b", **{row: value})
    assert got.values[row] == "off"
    assert f"{row} {value} is left out: a headless session with an identity ends by its " \
           "exit rule" in " ".join(got.warnings)
    # Without the identity the row stays.
    plain = headless_launch(monkeypatch, headless_comms("claude", "codex"),
                            harness="claude", identity="", brief="b", **{row: value})
    assert plain.values[row] == value


# -- keep_warm: a launch setting, off unless set ------------------------------------------

WARM = only("turns", "cache", "slots")


@pytest.fixture
def warm_rows(monkeypatch):
    monkeypatch.setattr(catalog, "session_env_readable", lambda: True)
    monkeypatch.setattr(catalog, "slots_here", lambda: True)


@pytest.mark.parametrize("target", ["terminal", "slot", "headless"])
def test_a_launch_that_sets_no_keep_warm_holds_it_off(warm_rows, target):
    got = resolve(typed={"target": target, "brief": "b"}, available=WARM)
    assert got.values["keep_warm"] == "off"
    assert not any("keep_warm" in w for w in got.warnings)


@pytest.mark.parametrize("setting", ["off", "on"])
@pytest.mark.parametrize("where", ["typed", "preset", "preset-slot", "defaults",
                                   "defaults-headless"])
def test_keep_warm_from_any_layer_launches_as_given(warm_rows, setting, where):
    """`off` as well as `on`, typed, in a preset or from `[defaults]`, on any target: no
    refusal, no warning, and the value kept."""
    line = f'keep_warm = "{setting}"\n'
    text, request_ = {
        "typed": ("", {"typed": {"keep_warm": setting}}),
        "preset": ("[seat.s]\n" + line, {"seat": "s"}),
        "preset-slot": ('[seat.s]\ntarget = "slot"\n' + line, {"seat": "s"}),
        "defaults": ("[defaults]\n" + line, {}),
        "defaults-headless": ("[defaults]\n" + line,
                              {"typed": {"target": "headless", "brief": "b"}}),
    }[where]
    got = resolve(text, available=WARM, **request_)
    assert got.values["keep_warm"] == setting
    assert not any("keep_warm" in w for w in got.warnings)


def test_presets_check_takes_keep_warm_off_and_on_on_any_seat(warm_rows):
    text = ('[seat.s]\nkeep_warm = "off"\n'
            '[seat.h]\ntarget = "headless"\nkeep_warm = "off"\n'
            '[seat.o]\nkeep_warm = "on"\n')
    assert not [p for p in errors(text, WARM) if p.key == "keep_warm"]


def test_keep_warm_is_left_alone_on_a_codex_launch_and_where_it_is_not_offered(warm_rows):
    assert "keep_warm" not in resolve(typed={"harness": "codex"}, available=WARM).values
    assert "keep_warm" not in resolve(available=only("turns")).values


@pytest.mark.parametrize("target", ["terminal", "slot"])
def test_a_codex_launch_with_an_identity_runs_under_the_host(monkeypatch, target):
    monkeypatch.setattr(presets, "_host_installed", lambda: True)
    monkeypatch.setattr(presets, "comms_codex", lambda: True)
    got = resolve(typed={"harness": "codex", "target": target, "identity": "x"},
                  available=only("comms", "turns", "slots"))
    assert got.mode == "host" and {"turns", "comms"} <= got.links


def test_a_codex_launch_with_an_identity_needs_the_host(monkeypatch):
    monkeypatch.setattr(presets, "comms_codex", lambda: True)
    with pytest.raises(presets.NotYet, match="Codex host") as caught:
        resolve(typed={"harness": "codex", "identity": "x"}, available=only("comms"))
    assert caught.value.exit_code == codes.REFUSED
    monkeypatch.setattr(presets, "_host_installed", lambda: False)
    with pytest.raises(presets.NotYet, match="Codex host"):
        resolve(typed={"harness": "codex", "identity": "x"},
                available=only("comms", "turns"))


def test_a_codex_launch_with_an_identity_needs_comms_codex_wrapper(monkeypatch):
    monkeypatch.setattr(presets, "_host_installed", lambda: True)
    monkeypatch.setattr(presets, "comms_codex", lambda: False)
    with pytest.raises(presets.NotYet, match="moxi comms codex") as caught:
        resolve(typed={"harness": "codex", "identity": "x"},
                available=only("comms", "turns"))
    assert f"install {presets.needed('comms')}," in caught.value.hint


def test_needed_reads_the_extra_from_the_launchers_own_requirement():
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    extras = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
        "optional-dependencies"]
    for extra in ("comms", "usage", "turns"):
        (declared,) = extras[extra]
        name, _, specs = declared.partition(">")
        assert presets.needed(extra) == f"{name}>{specs}"
    assert presets.needed("comms").startswith("moxi-comms>=0.3,")
    assert presets.needed("no-such-extra") == "moxi-no-such-extra"


def test_a_codex_launch_with_an_identity_and_extra_flags_is_refused(monkeypatch):
    monkeypatch.setattr(presets, "_host_installed", lambda: True)
    monkeypatch.setattr(presets, "comms_codex", lambda: True)
    with pytest.raises(catalog.WrapperFlags) as caught:
        resolve(typed={"harness": "codex", "identity": "x"}, extra=["--no-window"],
                available=only("comms", "turns"))
    assert caught.value.exit_code == codes.REFUSED
    assert "takes no other flags" in caught.value.hint
    # Without an identity, or for Claude, the flags go on.
    assert resolve(typed={"harness": "codex"}, extra=["--option", "x=1"],
                   available=only("turns")).values["extra_flags"] == ["--option", "x=1"]
    assert resolve(typed={"harness": "claude", "identity": "x"}, extra=["--verbose"],
                   available=only("comms")).identity == "x"


def test_comms_codex_follows_comms_interface(monkeypatch):
    monkeypatch.setattr(presets.modules, "available", lambda name: name == "comms")
    monkeypatch.setattr(presets.modules, "load", lambda name, interface=None:
                        SimpleNamespace(ok=interface == presets.COMMS_INTERFACE,
                                        api="comms-api"))
    assert presets.comms_api() == "comms-api" and presets.comms_codex()
    monkeypatch.setattr(presets.modules, "load", lambda name, interface=None:
                        SimpleNamespace(ok=False, api=None))
    assert presets.comms_api() is None and not presets.comms_codex()
    monkeypatch.setattr(presets.modules, "available", lambda name: False)
    assert not presets.comms_codex()


def test_comms_codex_resume_follows_comms_constant(monkeypatch):
    for api_, can in ((SimpleNamespace(CODEX_RESUME=True), True),
                      (SimpleNamespace(CODEX_RESUME=False), False),
                      (SimpleNamespace(), False), (None, False)):
        monkeypatch.setattr(presets, "comms_api", lambda api_=api_: api_)
        assert presets.comms_codex_resume() is can


def test_links_follow_the_values(monkeypatch):
    got = resolve(typed={"target": "slot"}, available=only("turns", "slots"))
    assert got.links == {"turns", "slots"}


# -- pass-through and typed keys ----------------------------------------------------------


def test_a_presets_extra_flags_are_refused_when_they_spell_an_owned_row():
    with pytest.raises(presets.BadPreset) as caught:
        resolve('[seat.s]\nextra_flags = ["--session-id", "x"]\n', seat="s")
    assert caught.value.exit_code == codes.REFUSED


def test_a_presets_extra_flags_pass_through():
    got = resolve('[seat.s]\nextra_flags = ["--verbose"]\n', seat="s")
    assert got.values["extra_flags"] == ["--verbose"]


def test_a_typed_row_this_install_does_not_offer_is_refused():
    with pytest.raises(catalog.NeedsModule):
        resolve(typed={"slot": "3"})
    with pytest.raises(catalog.BadValue):
        resolve(typed={"nonsense": 1})


# -- presets check ------------------------------------------------------------------------


def test_a_good_file_checks_clean(tmp_path):
    instructions = tmp_path / "planner.md"
    instructions.write_text("Plan the work.\n", encoding="utf-8")
    text = (f'[defaults]\npermission_mode = "bypassPermissions"\nsandbox = "read-only"\n'
            f'[seat.planner]\nharness = "claude"\nmodel = "opus"\n'
            f'instructions_file = "{instructions}"\nposture = "operator"\ntarget = "slot"\n'
            f'[project.moxi]\ncwd = "{tmp_path}"\nharness = "codex"\n'
            f'sandbox = "workspace-write"\n')
    assert problems(text, available=only("slots")) == []


@pytest.mark.parametrize("text, where, key, code", [
    ('[defaults]\nnope = 1\n', "defaults", "nope", "unknown-key"),
    ('[defaults]\nsession_id = "x"\n', "defaults", "session_id", "owned"),
    ('[defaults]\nmodel = 1\n', "defaults", "model", "bad-value"),
    ('[seat.s]\nnope = 1\n', "seat.s", "nope", "unknown-key"),
    ('[seat.s]\nmodel = ["x"]\n', "seat.s", "model", "bad-value"),
    ('[seat.s]\neffort = "huge"\n', "seat.s", "effort", "bad-value"),
    ('[seat.s]\ntarget = "moon"\n', "seat.s", "target", "bad-value"),
    ('[seat.s]\nharness = "vim"\n', "seat.s", "harness", "bad-value"),
    ('[seat.s]\nsession_id = "x"\n', "seat.s", "session_id", "owned"),
    ('[seat.s]\nslot = "1"\n', "seat.s", "slot", "needs-module"),
    ('[seat.s]\ndirector = ["a"]\n', "seat.s", "director", "needs-module"),
    ('[seat.s]\ntakes_policy_directors = false\n', "seat.s", "takes_policy_directors",
     "needs-module"),
    ('[defaults]\ntakes_policy_directors = false\n', "defaults",
     "takes_policy_directors", "per-seat"),
    ('[seat.s]\ninstructions_file = "rel/x.md"\n', "seat.s", "instructions_file",
     "bad-value"),
    ('[seat.s]\ninstructions = "a"\ninstructions_file = "/x.md"\n', "seat.s",
     "instructions", "two-instructions"),
    ('[seat.s]\nbrief = "a"\nbrief_file = "/x.md"\n', "seat.s", "brief", "two-brief"),
    ('[seat.s]\nposture = "with:lead"\n', "seat.s", "posture", "needs-module"),
    ('[seat.s]\nposture = "eventually"\n', "seat.s", "posture", "bad-value"),
    ('[seat.s]\nextra_flags = ["--permission-mode", "plan"]\n', "seat.s",
     "extra_flags", "owned"),
    ('[seat.c]\nharness = "codex"\npermission_mode = "plan"\n', "seat.c",
     "permission_mode", "unknown-key"),
    # What every launch would refuse at compose: a posture set by a passed-on flag or a
    # config override, in a preset or in the defaults every launch takes.
    ('[seat.c]\nharness = "codex"\nconfig = ["sandbox_mode=danger-full-access"]\n',
     "seat.c", "config", "owned"),
    ('[seat.c]\nharness = "codex"\nconfig = ["x=1", "approval_policy=never"]\n',
     "seat.c", "config", "owned"),
    ('[seat.c]\nharness = "codex"\nextra_flags = ["--yolo"]\n', "seat.c",
     "extra_flags", "owned"),
    ('[seat.s]\nextra_flags = ["--dangerously-skip-permissions"]\n', "seat.s",
     "extra_flags", "owned"),
    ('[defaults]\nconfig = ["sandbox_mode=read-only"]\n', "defaults", "config", "owned"),
    ('[defaults]\nextra_flags = ["--dangerously-skip-permissions"]\n', "defaults",
     "extra_flags", "owned"),
    ('[defaults]\nextra_flags = ["--approve-for-me"]\n', "defaults", "extra_flags",
     "owned"),
    ('[seat."a b"]\nmodel = "opus"\n', "seat.a b", None, "bad-name"),
    ('[seat.s]\nhome = ""\n', "seat.s", "home", "bad-value"),
    ('[seat.s]\nhome = "./rel"\n', "seat.s", "home", "bad-value"),
    ('[seat.s]\nproject = "moxi"\n', "seat.s", "project", "needs-module"),
    ('[seat.s]\ninstructions_file = "/no/such/file.md"\n', "seat.s",
     "instructions_file", "unreadable"),
])
def test_check_refusals(text, where, key, code):
    found = errors(text)
    assert [(p.where, p.key, p.code) for p in found] == [(where, key, code)]
    assert all(str(p).startswith("error: ") for p in found)


def test_an_empty_default_harness_reads_as_the_shipped_one_in_check_and_resolve():
    text = '[defaults]\nharness = ""\n[seat.s]\nmodel = "opus"\n'
    assert errors(text) == []
    assert resolve(text, seat="s").harness == "claude"


def test_check_returns_every_problem_in_file_order():
    found = errors('[defaults]\nnope = 1\n[seat.a]\nmodel = 1\n[seat.b]\neffort = "x"\n'
                   '[project.p]\ntarget = "x"\n')
    assert [p.where for p in found] == ["defaults", "seat.a", "seat.b", "project.p"]


def test_check_a_project_key(projects):
    assert errors('[project.p]\nproject = "moxi"\n', available=WITH_PROJECTS) == []
    found = errors('[project.p]\nproject = "gone"\n[seat.s]\nhome = "gone"\n',
                   available=WITH_PROJECTS)
    assert [(p.where, p.key, p.code) for p in found] == [("seat.s", "home", "no-project"),
                                                         ("project.p", "project",
                                                          "no-project")]
    projects.fail = Busy()
    found = errors('[project.p]\nproject = "moxi"\n', available=WITH_PROJECTS)
    assert [(p.code, p.exit_code) for p in found] == [("busy", codes.TEMPFAIL)]


def test_check_the_identity_warns_without_comms():
    found = problems('[seat.s]\nidentity = "planner"\n')
    assert [(p.key, p.level) for p in found] == [("identity", presets.WARNING)]
    assert problems('[seat.s]\nidentity = "planner"\n', available=only("comms")) == []


@pytest.fixture
def claude_home(tmp_path):
    home = tmp_path / "claude-home"
    (home / "agents").mkdir(parents=True)
    (home / "agents" / "reviewer.md").write_text("---\nname: code-reviewer\n---\nReview.\n",
                                                 encoding="utf-8")
    return {**os.environ, "CLAUDE_CONFIG_DIR": str(home)}


def test_check_an_agent_role_among_the_harnesss_role_files(claude_home, tmp_path):
    project = tmp_path / "proj"
    (project / ".claude" / "agents").mkdir(parents=True)
    (project / ".claude" / "agents" / "local.md").write_text("x", encoding="utf-8")
    text = ('[seat.a]\nagent = "reviewer"\n[seat.b]\nagent = "code-reviewer"\n'
            f'[seat.c]\nagent = "local"\ncwd = "{project}"\n'
            '[seat.d]\nagent = "local"\n[seat.e]\nagent = "nobody"\n')
    found = errors(text, env=claude_home)
    assert [(p.where, p.code) for p in found] == [("seat.d", "no-role"),
                                                  ("seat.e", "no-role")]


def test_roles_reads_the_core_harness_tables_dir(tmp_path):
    from moxi import config

    agents_dir = tmp_path / "my-agents"
    agents_dir.mkdir()
    (agents_dir / "lead.md").write_text("x", encoding="utf-8")
    config.set_value("moxi", "harnesses",
               [{"name": "claude", "skills": "", "agents": str(agents_dir)},
                {"name": "codex", "skills": "", "agents": ""}], by="test")
    assert presets.roles("claude") == {"lead"}


def test_the_codex_start_ceiling_is_the_launchers_half_of_turns_share():
    # turns splits 9000 characters of Codex start context evenly between the modules
    # that answer there, and comms answers there too.
    assert presets.CODEX_START_CEILING == 4500


def test_check_warns_when_a_codex_seat_passes_the_start_ceiling(tmp_path):
    long = tmp_path / "long.md"
    long.write_text("x" * presets.CODEX_START_CEILING, encoding="utf-8")
    text = (f'[seat.c]\nharness = "codex"\ninstructions_file = "{long}"\n'
            f'[seat.claude]\ninstructions_file = "{long}"\n'
            '[seat.short]\nharness = "codex"\ninstructions = "Be brief."\n')
    found = problems(text)
    assert [(p.where, p.code, p.level) for p in found] == [("seat.c", "too-long",
                                                            presets.WARNING)]


def test_check_reads_the_file_in_place():
    write('[seat.s]\nmodel = 1\n')
    assert [p.key for p in presets.check()] == ["model"]


def test_problems_render_as_json():
    found = errors('[seat.s]\nmodel = 1\n')
    assert found[0].as_json() == {"where": "seat.s", "key": "model",
                                  "problem": found[0].problem, "hint": found[0].hint,
                                  "code": "bad-value", "level": "error"}


# -- the project listing ------------------------------------------------------------------


def test_a_projects_agents_with_their_seats(projects):
    found = presets.read(write('[seat.plan]\nidentity = "planner"\n'
                               '[seat.plan2]\nidentity = "planner@moxi"\n'
                               '[seat.other]\nidentity = "someone"\n'
                               '[seat.homed]\nhome = "moxi"\n'))
    key, agents = presets.project_agents("MOXI", presets=found, available=WITH_PROJECTS)
    assert key == "moxi"
    assert [a.as_json() for a in agents] == [
        {"agent": "planner", "seats": ["plan", "plan2"]},
        {"agent": "builder", "seats": []},
    ]


def test_the_project_listing_needs_projects():
    with pytest.raises(presets.NeedsProjects):
        presets.project_agents("moxi", presets=presets.read(write("")), available=NONE)


def test_the_project_listing_refuses_an_unknown_key(projects):
    with pytest.raises(presets.UnknownProject):
        presets.project_agents("gone", presets=presets.read(write("")),
                               available=WITH_PROJECTS)


# -- a text given inline or from a file ------------------------------------------------------


def test_a_typed_inline_text_replaces_a_presets_file_and_the_other_way_round():
    got = resolve('[seat.s]\ninstructions_file = "/seat.md"\n', seat="s",
                  typed={"instructions": "typed"})
    assert got.values["instructions"] == "typed" and got.values["instructions_file"] == ""
    assert got.sources["instructions_file"] == presets.SHIPPED
    got = resolve('[seat.s]\ninstructions = "seat"\n', seat="s",
                  typed={"instructions_file": "/typed.md"})
    assert got.values["instructions_file"] == "/typed.md" and got.values["instructions"] == ""
    got = resolve('[defaults]\nbrief = "default"\n[seat.s]\nbrief_file = "/b.md"\n',
                  seat="s")
    assert got.values["brief_file"] == "/b.md" and got.values["brief"] == ""
    # A later layer that gives neither leaves an earlier one's alone.
    got = resolve('[seat.s]\ninstructions_file = "/seat.md"\n', seat="s",
                  typed={"model": "opus"})
    assert got.values["instructions_file"] == "/seat.md"


@pytest.mark.parametrize("typed", [{"brief": "b", "brief_file": "/b.md"},
                                   {"instructions": "i", "instructions_file": "/i.md"}])
def test_a_text_given_both_ways_at_once_is_refused(typed):
    with pytest.raises(catalog.BadValue) as raised:
        resolve(typed=typed)
    inline = next(iter(typed))
    assert raised.value.row == inline
    assert raised.value.problem == f"both {inline} and {inline}_file are given"


# -- the layered values -------------------------------------------------------------------

#: A presets file of many shapes: a place and a text in `[defaults]`, a seat with each
#: kind of place, both spellings of a text, a Codex seat, and a project key.
LAYERED = """\
[defaults]
model = "sonnet"
cwd = "/srv/default"
brief = "the default brief"

[seat.plain]
effort = "high"

[seat.home]
home = "~/work/x"
instructions_file = "/seat.md"

[seat.scratchy]
scratch = true
brief_file = "/b.md"

[seat.coder]
harness = "codex"
approval = "never"
sandbox = "workspace-write"

[project.moxi]
project = "moxi"
extra_flags = ["--verbose"]

[project.there]
cwd = "/srv/there"
instructions = "inline"
"""

PLACE_ROWS = ("cwd", "project", "scratch")


def laid_or_refused(call):
    try:
        return call(), None
    except catalog.Refused as exc:
        return None, exc


@pytest.mark.parametrize("harness", [None, "claude", "codex"])
@pytest.mark.parametrize("preset", [{}, {"seat": "plain"}, {"seat": "home"},
                                    {"seat": "scratchy"}, {"seat": "coder"},
                                    {"project_preset": "moxi"},
                                    {"project_preset": "there"}])
def test_layered_gives_resolves_values_and_sources_with_nothing_typed(projects, preset,
                                                                      harness):
    found = presets.read(write(LAYERED))
    laid, refused = laid_or_refused(lambda: presets.layered(
        Request(**preset), harness=harness, presets=found, available=WITH_PROJECTS))
    typed = {"harness": harness} if harness else {}
    got, also = laid_or_refused(lambda: presets.resolve(
        Request(**preset, typed=typed, off_posture=True, cwd="/work"), presets=found,
        available=WITH_PROJECTS))
    if refused is not None or also is not None:
        assert type(refused) is type(also) and refused.problem == also.problem
        return
    assert laid.harness == got.harness and set(laid.values) == set(got.values)
    for name in laid.values:
        if name in PLACE_ROWS or (harness and name == "harness"):
            continue
        assert (laid.values[name], laid.sources[name]) == (got.values[name],
                                                           got.sources[name]), name
    asked = {"harness"} if harness else set()
    assert laid.chosen - asked == got.chosen - asked
    # The place rows show the latest layer's place whole; the others are at their default.
    for name in PLACE_ROWS:
        if name in laid.place:
            assert laid.values[name] == laid.place[name]
            assert laid.sources[name] == got.sources[name] == laid.place_source
        else:
            assert laid.values[name] == catalog.launcher()[name].default
            assert laid.sources[name] == presets.SHIPPED


def test_layered_shows_a_place_whole_and_one_spelling_per_text():
    found = presets.read(write(LAYERED))
    laid = presets.layered(Request(seat="scratchy"), presets=found, available=NONE)
    assert laid.place == {"scratch": True} and laid.place_source == presets.PRESET
    assert (laid.values["cwd"], laid.sources["cwd"]) == ("", presets.SHIPPED)
    assert (laid.values["brief_file"], laid.sources["brief_file"]) == ("/b.md",
                                                                       presets.PRESET)
    assert (laid.values["brief"], laid.sources["brief"]) == ("", presets.SHIPPED)
    laid = presets.layered(Request(seat="home"), presets=found, available=NONE)
    assert laid.place == {"home": "~/work/x"}
    assert all(laid.sources[name] == presets.SHIPPED for name in PLACE_ROWS)
    laid = presets.layered(Request(seat="plain"), presets=found, available=NONE)
    assert laid.place == {"cwd": "/srv/default"} and laid.place_source == "defaults"
    assert laid.values["brief"] == "the default brief"


def test_layered_for_another_harness_keeps_the_layers_harness_row():
    found = presets.read(write('[seat.c]\nharness = "codex"\nmodel = "o3"\n'))
    laid = presets.layered(Request(seat="c"), harness="claude", presets=found,
                           available=NONE)
    assert laid.harness == "claude" and "sandbox" not in laid.values
    assert (laid.values["harness"], laid.sources["harness"]) == ("codex", presets.PRESET)
    assert laid.values["model"] == "o3"


# -- rows left out --------------------------------------------------------------------------


def test_a_left_out_row_holds_its_shipped_default_and_is_not_chosen():
    text = '[defaults]\nmodel = "sonnet"\n[seat.s]\neffort = "high"\nnative_name = "n"\n'
    got = resolve(text, seat="s", left_out=("effort", "model"))
    assert (got.values["effort"], got.sources["effort"]) == ("", presets.LEFT_OUT)
    assert (got.values["model"], got.sources["model"]) == ("", presets.LEFT_OUT)
    assert got.chosen == {"native_name"}
    laid = presets.layered(Request(seat="s", left_out=("effort",)),
                           presets=presets.read(write(text)), available=NONE)
    assert laid.sources["effort"] == presets.LEFT_OUT and "effort" not in laid.chosen
    assert laid.preset.values == {"native_name": "n"}


def test_leaving_out_extra_flags_removes_the_presets():
    text = '[seat.s]\nextra_flags = ["--verbose"]\n'
    assert resolve(text, seat="s").values["extra_flags"] == ["--verbose"]
    got = resolve(text, seat="s", left_out=("extra_flags",))
    assert got.values["extra_flags"] == [] and "extra_flags" not in got.chosen


def test_a_left_out_posture_row_that_defaults_set_needs_off_posture():
    text = ('[defaults]\npermission_mode = "bypassPermissions"\n'
            '[seat.s]\npermission_mode = "bypassPermissions"\n')
    with pytest.raises(presets.OffPosture) as caught:
        resolve(text, seat="s", left_out=("permission_mode",))
    assert caught.value.source == presets.LEFT_OUT
    assert caught.value.problem == ("permission_mode 'default' (left out) is off your "
                                    "default 'bypassPermissions'")
    got = resolve(text, seat="s", left_out=("permission_mode",), off_posture=True)
    assert got.values["permission_mode"] == "default"
    # A posture row only the preset sets goes back to the shipped default: no box needed.
    got = resolve('[seat.s]\npermission_mode = "plan"\n', seat="s",
                  left_out=("permission_mode",))
    assert got.values["permission_mode"] == "default"


def test_the_place_rule_sees_a_left_out_place_gone(projects):
    text = '[defaults]\ncwd = "/srv/default"\n[seat.s]\nproject = "moxi"\n'
    got = resolve(text, available=WITH_PROJECTS, seat="s", left_out=("project",))
    assert got.cwd == "/srv/default" and got.project_key is None
    assert got.sources["cwd"] == "defaults"
    # Leaving out the only place falls to the caller's directory on the command line.
    got = resolve(text, available=WITH_PROJECTS, seat="s", left_out=("project", "cwd"))
    assert got.cwd == "/work" and got.sources["cwd"] == presets.LEFT_OUT


def test_a_seat_with_scratch_left_out_launches_in_the_typed_cwd():
    text = '[seat.s]\nscratch = true\n'
    got = resolve(text, seat="s", typed={"cwd": "/typed"}, left_out=("scratch",))
    assert got.cwd == "/typed" and not got.scratch
    laid = presets.layered(Request(seat="s", left_out=("scratch",)),
                           presets=presets.read(write(text)), available=NONE)
    assert laid.place == {} and laid.sources["scratch"] == presets.LEFT_OUT


def test_the_one_spelling_rule_sees_a_left_out_spelling_gone():
    text = '[defaults]\nbrief = "the default"\n[seat.s]\nbrief_file = "/b.md"\n'
    got = resolve(text, seat="s", left_out=("brief_file",))
    assert got.values["brief"] == "the default" and got.sources["brief"] == "defaults"
    assert got.sources["brief_file"] == presets.LEFT_OUT
    got = resolve(text, seat="s", typed={"brief": "typed"}, left_out=("brief_file",))
    assert got.sources["brief_file"] == presets.LEFT_OUT


@pytest.mark.parametrize("left_out, typed, row", [
    (("nonsense",), {}, "nonsense"),
    (("home",), {}, "home"),
    (("harness",), {}, "harness"),
    (("model",), {"model": "opus"}, "model"),
    (("extra_flags",), {"extra_flags": ["--verbose"]}, "extra_flags"),
])
def test_a_left_out_name_no_harness_has_harness_or_one_also_given_is_refused(left_out, typed,
                                                                              row):
    with pytest.raises(catalog.BadValue) as caught:
        resolve(typed=typed, left_out=left_out)
    assert caught.value.code == "bad-value" and caught.value.row == row
    assert caught.value.exit_code == codes.USAGE
    assert row in caught.value.problem


def test_a_row_of_another_harness_may_be_left_out():
    got = resolve(left_out=("sandbox", "approval"))
    assert got.harness == "claude" and "sandbox" not in got.values


def test_a_codex_seats_approval_left_out_launches_under_claude():
    text = '[seat.c]\nharness = "codex"\napproval = "never"\n'
    with pytest.raises(presets.BadPreset, match="approval is not a claude row"):
        resolve(text, seat="c", typed={"harness": "claude"})
    got = resolve(text, seat="c", typed={"harness": "claude"}, left_out=("approval",))
    assert got.harness == "claude" and "approval" not in got.values


def test_leaving_out_the_harness_is_refused_since_a_launch_needs_one():
    found = presets.read(write('[defaults]\nharness = "codex"\n[seat.c]\nharness = "codex"\n'))
    request = Request(seat="c", left_out=("harness",), cwd="/work")
    assert presets.harness_of(request, presets=found) == "codex"
    for lay in (presets.resolve, presets.layered):
        with pytest.raises(catalog.BadValue) as caught:
            lay(request, presets=found, available=NONE)
        assert caught.value.row == "harness" and "a launch needs one" in caught.value.problem


def test_a_word_enum_left_out_ends_a_seats_recycling():
    available = only("turns", "usage")
    text = '[seat.s]\nrecycle_at_limit = "compact"\nlimit_at = "2x"\n'
    assert resolve(text, available, seat="s").values["limit_at"] == "2x"
    with pytest.raises(catalog.NeedsModule, match="limit_at needs recycle_at_limit"):
        resolve(text, available, seat="s", typed={"recycle_at_limit": "off"})
    got = resolve(text, available, seat="s", typed={"recycle_at_limit": "off"},
                  left_out=("limit_at",))
    assert got.values["recycle_at_limit"] == "off" and got.values["limit_at"] == ""
    assert got.sources["limit_at"] == presets.LEFT_OUT and "limit_at" not in got.chosen


# -- a scratch launch can't recycle itself --------------------------------------------------

RECYCLES = only("turns", "usage")


def test_a_preset_that_sets_scratch_and_relaunch_is_flagged():
    found = errors('[seat.scratchy]\nscratch = true\nrecycle_at_limit = "relaunch"\n',
                   RECYCLES)
    assert [(p.where, p.key, p.code) for p in found] == [
        ("seat.scratchy", "recycle_at_limit", "scratch-recycle")]
    assert found[0].problem == "a scratch launch can't recycle itself"
    assert found[0].hint == "recycle with compact, or launch in a directory"


def test_a_defaults_relaunch_is_not_flagged_on_a_scratch():
    # `[defaults]` reaches every launch, so its relaunch holds off on a scratch one.
    for text in ('[defaults]\nscratch = true\nrecycle_at_limit = "relaunch"\n',
                 '[defaults]\nrecycle_at_limit = "relaunch"\n[seat.s]\nscratch = true\n',
                 '[defaults]\nscratch = true\nrecycle_at_limit = "relaunch"\n'
                 '[seat.s]\nposture = "go"\n'):
        assert "scratch-recycle" not in {p.code for p in problems(text, RECYCLES)}


def test_a_preset_place_replaces_the_defaults_scratch_in_the_check():
    text = ('[defaults]\nscratch = true\n'
            '[seat.s]\ncwd = "/work"\nrecycle_at_limit = "relaunch"\n')
    assert "scratch-recycle" not in {p.code for p in problems(text, RECYCLES)}


def test_a_preset_meeting_the_defaults_in_the_pair_is_flagged():
    text = '[defaults]\nscratch = true\n[seat.s]\nrecycle_at_limit = "relaunch"\n'
    assert [(p.where, p.code) for p in errors(text, RECYCLES)] == [
        ("seat.s", "scratch-recycle")]


@pytest.mark.parametrize("text", [
    '[seat.s]\nscratch = true\nrecycle_at_limit = "compact"\n',
    '[seat.s]\nrecycle_at_limit = "relaunch"\n',
    '[defaults]\nrecycle_at_limit = "relaunch"\n[seat.s]\nscratch = false\n'])
def test_compact_on_a_scratch_and_relaunch_elsewhere_are_not_flagged(text):
    assert "scratch-recycle" not in {p.code for p in problems(text, RECYCLES)}


def test_resolve_refuses_a_scratch_set_to_relaunch():
    with pytest.raises(catalog.ScratchRecycle) as caught:
        resolve('[seat.s]\nscratch = true\n', RECYCLES, seat="s",
                typed={"recycle_at_limit": "relaunch"})
    assert caught.value.code == "scratch-recycle" and caught.value.exit_code == 3


def test_resolve_refuses_a_scratch_whose_preset_sets_relaunch():
    for text in ('[seat.s]\nscratch = true\nrecycle_at_limit = "relaunch"\n',
                 '[defaults]\nscratch = true\n[seat.s]\nrecycle_at_limit = "relaunch"\n'):
        with pytest.raises(catalog.ScratchRecycle):
            resolve(text, RECYCLES, seat="s")


@pytest.mark.parametrize("text, asked", [
    ('[defaults]\nrecycle_at_limit = "relaunch"\n[seat.s]\nscratch = true\n',
     {"seat": "s"}),
    ('[defaults]\nrecycle_at_limit = "relaunch"\n', {"typed": {"scratch": True}}),
    ('[defaults]\nscratch = true\nrecycle_at_limit = "relaunch"\n', {})])
def test_a_defaults_relaunch_holds_off_on_a_scratch_with_a_warning(text, asked):
    got = resolve(text, RECYCLES, **asked)
    assert got.scratch and got.values["recycle_at_limit"] == "off"
    assert got.sources["recycle_at_limit"] == presets.LEFT_OUT
    assert presets.SCRATCH_RECYCLE_OFF in got.warnings
    assert "recycle_at_limit" not in got.chosen


def test_a_defaults_relaunch_stays_outside_a_scratch(monkeypatch):
    monkeypatch.setattr(catalog, "slots_here", lambda: True)
    got = resolve('[defaults]\nrecycle_at_limit = "relaunch"\n',
                  only("turns", "usage", "slots"), typed={"target": "slot"})
    assert got.values["recycle_at_limit"] == "relaunch"
    assert presets.SCRATCH_RECYCLE_OFF not in got.warnings
