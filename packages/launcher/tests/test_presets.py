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


def test_the_file_lives_in_the_launchers_config_folder():
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


def test_a_typed_directory_gets_its_project_key(projects):
    inside = projects.roots["app"] / "src"
    got = resolve(available=WITH_PROJECTS, typed={"cwd": str(inside)})
    assert got.cwd == str(inside) and got.project_key == "app"
    assert projects.calls == [("for_path", str(inside))]


def test_the_callers_directory_gets_its_project_key(projects):
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


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_headless_launch_with_an_identity_waits_on_comms(harness):
    with pytest.raises(presets.NotYet, match="headless") as caught:
        resolve(typed={"harness": harness, "target": "headless", "identity": "x"},
                available=only("comms"))
    assert caught.value.exit_code == codes.REFUSED


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
    with pytest.raises(presets.NotYet, match="moxi comms codex"):
        resolve(typed={"harness": "codex", "identity": "x"},
                available=only("comms", "turns"))


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


def test_roles_reads_the_core_harness_tables_folder(tmp_path):
    from moxi import config

    folder = tmp_path / "my-agents"
    folder.mkdir()
    (folder / "lead.md").write_text("x", encoding="utf-8")
    config.set("moxi", "harnesses",
               [{"name": "claude", "skills": "", "agents": str(folder)},
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
